"""Cadastral audit: document type x activity status, and ID-level match with
the consolidated patrimony.

Every client is placed in exactly one cell of a contingency table
(PF / PJ / pending document x active / inactive / pending status). The audit
is only approved when nothing is pending, the counts add up and the set of
active client ids is *exactly* the set of ids in the consolidated patrimony.
Equal counts are never taken as evidence: ids are compared one by one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.errors import TransformationError
from src.transformer import PersonType, classify_person, id_key


class ActivityStatus(StrEnum):
    """Classification of ``clients.active``."""

    ACTIVE = "ATIVO"
    INACTIVE = "INATIVO"
    UNKNOWN = "STATUS PENDENTE"


class CadastralStatus(StrEnum):
    """Cadastral audit verdict."""

    APPROVED = "APROVADA"
    DIVERGENT = "DIVERGENTE"
    NOT_VERIFIED = "NÃO VERIFICADA"


def classify_status(raw: Any) -> ActivityStatus:
    """Classify ``active`` strictly: only JSON booleans are accepted.

    Strings such as ``"true"``, numbers or ``null`` are UNKNOWN, so no client
    is presumed active.
    """
    if raw is True:
        return ActivityStatus.ACTIVE
    if raw is False:
        return ActivityStatus.INACTIVE
    return ActivityStatus.UNKNOWN


@dataclass(frozen=True)
class Classification:
    """Contingency table of client ids.

    Attributes:
        cells: Client ids per ``(PersonType, ActivityStatus)``, in API order.
        total: Number of clients classified.
    """

    cells: dict[tuple[PersonType, ActivityStatus], list[Any]]
    total: int

    def ids(self, person: PersonType | None = None, status: ActivityStatus | None = None) -> list[Any]:
        """Ids matching the given row and/or column (``None`` = any)."""
        return [
            client_id
            for (cell_person, cell_status), ids in self.cells.items()
            if person in (None, cell_person) and status in (None, cell_status)
            for client_id in ids
        ]

    def count(self, person: PersonType | None = None, status: ActivityStatus | None = None) -> int:
        """Number of clients in a cell, row, column or the whole table."""
        return len(self.ids(person, status))

    def classification_of(self, client_id: Any) -> tuple[PersonType, ActivityStatus] | None:
        """Cell of a client id, or ``None`` if it is not a registered client."""
        key = id_key(client_id)
        for cell, ids in self.cells.items():
            if any(id_key(i) == key for i in ids):
                return cell
        return None


def classify_clients(
    clients: list[dict[str, Any]], special_ids: frozenset[str] = frozenset()
) -> Classification:
    """Place every client in exactly one cell and verify the arithmetic.

    Configured special records get their own row (they are classified, not
    pending), so they never block the cadastral audit.

    Raises:
        TransformationError: If row totals or column totals do not equal the
            number of clients, or if an id appears more than once.
    """
    cells: dict[tuple[PersonType, ActivityStatus], list[Any]] = {
        (person, status): [] for person in PersonType for status in ActivityStatus
    }
    for client in clients:
        cell = (classify_person(client, special_ids), classify_status(client.get("active")))
        cells[cell].append(client.get("id"))
    table = Classification(cells=cells, total=len(clients))

    if sum(table.count(person=p) for p in PersonType) != table.total:
        raise TransformationError("PF + PJ + especiais + documentos pendentes ≠ total de clientes.")
    if sum(table.count(status=s) for s in ActivityStatus) != table.total:
        raise TransformationError("Ativos + inativos + status pendente ≠ total de clientes.")
    repeated = [key for key, n in Counter(id_key(i) for i in table.ids()).items() if n > 1]
    if repeated:
        raise TransformationError(f"Cliente(s) em mais de uma classificação: {repeated[:10]}.")
    return table


@dataclass(frozen=True)
class CadastralAudit:
    """Result of the cadastral audit.

    Attributes:
        status: Verdict.
        classification: Contingency table.
        active_ids: A — registered clients with ``active == true``.
        patrimony_ids: B — ``clientId`` values of consolidated-pl (``None``
            if the endpoint could not be read).
        in_both: A ∩ B.
        active_missing_from_patrimony: A − B.
        patrimony_not_active: B − A (includes ids not registered at all).
        outside_patrimony: Registered clients absent from B with their cell.
        reasons: Why the audit is not approved.
    """

    status: CadastralStatus
    classification: Classification
    active_ids: list[Any]
    patrimony_ids: list[Any] | None
    in_both: list[Any] = field(default_factory=list)
    active_missing_from_patrimony: list[Any] = field(default_factory=list)
    patrimony_not_active: list[Any] = field(default_factory=list)
    outside_patrimony: list[tuple[Any, PersonType, ActivityStatus]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _sorted_ids(ids: set[str], originals: dict[str, Any]) -> list[Any]:
    return sorted((originals[key] for key in ids), key=lambda value: (not isinstance(value, int), str(value)))


def audit_cadastral(
    clients: list[dict[str, Any]], classification: Classification, patrimony_ids: list[Any] | None
) -> CadastralAudit:
    """Compare active clients with the consolidated patrimony, id by id.

    Args:
        clients: Records from ``/company-api/clients``.
        classification: Result of :func:`classify_clients` for ``clients``.
        patrimony_ids: ``clientId`` values of consolidated-pl, or ``None``.

    Returns:
        APPROVED only if no document or status is pending and A == B exactly.
    """
    active_ids = classification.ids(status=ActivityStatus.ACTIVE)
    reasons: list[str] = []
    pending_docs = classification.ids(person=PersonType.UNCLASSIFIED)
    pending_status = classification.ids(status=ActivityStatus.UNKNOWN)
    if pending_docs:
        reasons.append(f"{len(pending_docs)} cliente(s) com documento pendente: {pending_docs}.")
    if pending_status:
        reasons.append(
            f"{len(pending_status)} cliente(s) com status ativo/inativo pendente: {pending_status}."
        )

    if patrimony_ids is None:
        reasons.append("Consolidado patrimonial indisponível: conjuntos de ids não comparados.")
        return CadastralAudit(CadastralStatus.NOT_VERIFIED, classification, active_ids, None, reasons=reasons)

    originals = {id_key(i): i for i in [*active_ids, *patrimony_ids, *(c.get("id") for c in clients)]}
    a = {id_key(i) for i in active_ids}
    b = {id_key(i) for i in patrimony_ids}
    if len(b) != len(patrimony_ids):
        reasons.append("Consolidado patrimonial com clientId repetido.")
    missing = _sorted_ids(a - b, originals)
    not_active = _sorted_ids(b - a, originals)
    if missing:
        reasons.append(f"{len(missing)} cliente(s) ativo(s) ausente(s) do consolidado: {missing}.")
    if not_active:
        reasons.append(
            f"{len(not_active)} registro(s) do consolidado que não são clientes ativos: {not_active}."
        )

    outside = []
    for client in clients:
        if id_key(client.get("id")) not in b:
            person, status = classification.classification_of(client.get("id")) or (None, None)
            outside.append((client.get("id"), person, status))

    return CadastralAudit(
        status=CadastralStatus.DIVERGENT if reasons else CadastralStatus.APPROVED,
        classification=classification,
        active_ids=active_ids,
        patrimony_ids=list(patrimony_ids),
        in_both=_sorted_ids(a & b, originals),
        active_missing_from_patrimony=missing,
        patrimony_not_active=not_active,
        outside_patrimony=outside,
        reasons=reasons,
    )
