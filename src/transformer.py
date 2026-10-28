"""Transform raw API records into sorted report rows.

No value is invented: absent or invalid data becomes ``None`` plus a
pendency in the row status. Only clients whose ``cpfCnpj`` is a valid CPF
(Pessoa Física) become rows; see :func:`classify_document`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from src.errors import TransformationError
from src.reconciliation import to_decimal

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOCUMENT_SEPARATORS = re.compile(r"[.\-/\s]")
_CNPJ_WEIGHTS = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


class PersonType(StrEnum):
    """Classification of a client by its ``cpfCnpj`` document.

    SPECIAL is never derived from the document: it is assigned only to ids
    explicitly configured in ``CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS``.
    """

    INDIVIDUAL = "PF"
    COMPANY = "PJ"
    SPECIAL = "ESPECIAL"
    UNCLASSIFIED = "PENDENTE"


class Pendency(StrEnum):
    """Pendency labels, declared in the order they appear in the status."""

    NAME = "NOME"
    BIRTHDATE = "DATA DE NASCIMENTO"
    CONSULTANT = "CONSULTOR"
    PATRIMONY = "PATRIMÔNIO"
    ADDRESS = "ENDEREÇO"


_PENDENCY_ORDER = {pendency: index for index, pendency in enumerate(Pendency)}


@dataclass
class ReportRow:
    """One line of the birthday spreadsheet.

    Attributes:
        client_id: Client identifier (not exported, used for ordering/audit).
        name: Full name, or empty string when absent.
        birthdate: Valid birth date, or ``None``.
        patrimony: Provisional patrimony (``clients.currentValue``) in API
            units, or ``None``. Not audited.
        address: Formatted full address, or ``None``.
        consultant: Responsible consultant name, or ``None``.
        pendencies: Detected pendencies.
        city: ``clients.city`` as registered, or ``None``.
        state: ``clients.state`` as registered, or ``None``.
    """

    client_id: Any
    name: str
    birthdate: date | None
    patrimony: Decimal | None
    address: str | None
    consultant: str | None
    pendencies: list[Pendency] = field(default_factory=list)
    city: str | None = None
    state: str | None = None

    @property
    def status(self) -> str:
        """Status text, listing every pendency in a stable order."""
        if not self.pendencies:
            return "OK"
        ordered = sorted(set(self.pendencies), key=_PENDENCY_ORDER.__getitem__)
        return "PENDÊNCIA: " + "; ".join(p.value for p in ordered)


def _clean(value: Any) -> str:
    """Return a stripped string for scalar values, ``""`` for missing ones."""
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()


def id_key(value: Any) -> str:
    """Normalise an identifier for exact matching across endpoints.

    IDs are compared by exact textual value (``42`` and ``"42"`` match),
    never by similarity.
    """
    return _clean(value)


def parse_birthdate(raw: Any, today: date) -> date | None:
    """Parse a documented ``YYYY-MM-DD`` birth date.

    Args:
        raw: Value of ``birthdate``.
        today: Reference date; future dates are rejected.

    Returns:
        The date, or ``None`` if absent, malformed, non-existent (e.g.
        ``2001-02-29``) or in the future.
    """
    text = _clean(raw)
    if not _ISO_DATE.match(text):
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed <= today else None


def format_postal_code(raw: Any) -> str:
    """Format a Brazilian CEP as ``00000-000`` when it has exactly 8 digits."""
    text = _clean(raw)
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8 and len(text) <= 10:
        return f"{digits[:5]}-{digits[5:]}"
    return text


def build_address(client: dict[str, Any]) -> str | None:
    """Join the available address components in logical order.

    Order: street, number, complement, district, city, state, CEP, country.
    Missing components are skipped without leaving empty separators.

    Returns:
        The address, or ``None`` when every component is absent.
    """
    parts = [
        _clean(client.get(key)) for key in ("street", "number", "complement", "district", "city", "state")
    ]
    postal_code = format_postal_code(client.get("postalCode"))
    parts.append(f"CEP {postal_code}" if postal_code else "")
    parts.append(_clean(client.get("country")))
    present = [part for part in parts if part]
    return ", ".join(present) if present else None


def _check_digit(values: list[int], weights: tuple[int, ...]) -> int:
    remainder = sum(v * w for v, w in zip(values, weights, strict=True)) % 11
    return 0 if remainder < 2 else 11 - remainder


def is_valid_cpf(document: str) -> bool:
    """Validate an 11-digit CPF (separators already removed)."""
    if len(document) != 11 or not document.isdigit() or len(set(document)) == 1:
        return False
    digits = [int(char) for char in document]
    first = _check_digit(digits[:9], tuple(range(10, 1, -1)))
    second = _check_digit(digits[:10], tuple(range(11, 1, -1)))
    return digits[9:] == [first, second]


def is_valid_cnpj(document: str) -> bool:
    """Validate a 14-character CNPJ, numeric or alphanumeric.

    The alphanumeric format (Receita Federal, from July 2026) keeps two
    numeric check digits and maps each character to ``ord(char) - 48``, which
    reduces to the classic algorithm for numeric CNPJs.
    """
    if len(document) != 14 or not document[12:].isdigit() or len(set(document)) == 1:
        return False
    if not all(char.isdigit() or "A" <= char <= "Z" for char in document[:12]):
        return False
    values = [ord(char) - 48 for char in document]
    first = _check_digit(values[:12], _CNPJ_WEIGHTS)
    second = _check_digit(values[:13], (6, *_CNPJ_WEIGHTS))
    return values[12:] == [first, second]


def classify_document(raw: Any) -> PersonType:
    """Classify a client as PF or PJ from ``cpfCnpj``, without guessing.

    Only common separators (``.``, ``-``, ``/``, spaces) are removed. Anything
    that is not a valid CPF or a valid CNPJ (absent, wrong length, bad check
    digits, unexpected characters) is UNCLASSIFIED.
    """
    if isinstance(raw, bool) or not isinstance(raw, str):
        return PersonType.UNCLASSIFIED
    document = _DOCUMENT_SEPARATORS.sub("", raw).upper()
    if is_valid_cpf(document):
        return PersonType.INDIVIDUAL
    if is_valid_cnpj(document):
        return PersonType.COMPANY
    return PersonType.UNCLASSIFIED


def classify_person(client: dict[str, Any], special_ids: frozenset[str] = frozenset()) -> PersonType:
    """Classify a client: configured special id first, then the document.

    Args:
        client: Record from ``/company-api/clients``.
        special_ids: Normalised ids (:func:`id_key`) of special records.
    """
    if id_key(client.get("id")) in special_ids:
        return PersonType.SPECIAL
    return classify_document(client.get("cpfCnpj"))


def build_rows(
    clients: list[dict[str, Any]],
    consultants: list[dict[str, Any]],
    today: date,
) -> list[ReportRow]:
    """Build one report row per client given, preserving every client.

    The caller is expected to pass only Pessoa Física clients.

    Args:
        clients: Records from ``/company-api/clients``.
        consultants: Records from ``/company-api/consultants``.
        today: Reference date for birth date validation.

    Returns:
        Unsorted rows, one per client.

    Raises:
        TransformationError: If a client record is not a JSON object.
    """
    consultants_by_id = {id_key(c.get("id")): c for c in consultants if isinstance(c, dict)}
    rows: list[ReportRow] = []
    for client in clients:
        if not isinstance(client, dict):
            raise TransformationError("Registro de cliente em formato inesperado.")
        pendencies: list[Pendency] = []
        client_id = client.get("id")

        name = _clean(client.get("name"))
        if not name:
            pendencies.append(Pendency.NAME)

        birthdate = parse_birthdate(client.get("birthdate"), today)
        if birthdate is None:
            pendencies.append(Pendency.BIRTHDATE)

        consultant_name: str | None = None
        consultant_id = id_key(client.get("consultantId"))
        consultant = consultants_by_id.get(consultant_id) if consultant_id else None
        if consultant is not None:
            consultant_name = _clean(consultant.get("name")) or None
        if consultant_name is None:
            pendencies.append(Pendency.CONSULTANT)

        # Provisional operational source; never replaced by wallet sums.
        value = to_decimal(client.get("currentValue"))
        if value is None:
            pendencies.append(Pendency.PATRIMONY)

        address = build_address(client)
        if address is None:
            pendencies.append(Pendency.ADDRESS)

        rows.append(
            ReportRow(
                client_id,
                name,
                birthdate,
                value,
                address,
                consultant_name,
                pendencies,
                # Taken as registered (also kept inside the full address); never inferred.
                city=_clean(client.get("city")) or None,
                state=_clean(client.get("state")) or None,
            )
        )
    return rows


def _id_sort_key(value: Any) -> tuple[int, int, str]:
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value, "")
    return (1, 0, _clean(value))


def sort_key(row: ReportRow) -> tuple[Any, ...]:
    """Sort key: valid dates by month/day, then name, then id; pendings last."""
    if row.birthdate is None:
        return (1, 0, 0, row.name.casefold(), _id_sort_key(row.client_id))
    return (0, row.birthdate.month, row.birthdate.day, row.name.casefold(), _id_sort_key(row.client_id))


def sort_rows(rows: list[ReportRow]) -> list[ReportRow]:
    """Return rows ordered for the birthday report."""
    return sorted(rows, key=sort_key)
