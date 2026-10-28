"""Individual patrimony calculation and company-level reconciliation.

The API documents a list of ``wallets`` per client but does not state whether
those wallets are mutually exclusive or whether some of them aggregate others
(e.g. a "consolidated" wallet). Until that is confirmed, a value is only
considered audited when it cannot contain double counting. See
docs/ARCHITECTURE.md ("Regras de consolidação") and docs/DECISIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from src.config import WalletPolicy

# Above this many wallets the subset-sum overlap check becomes too expensive
# to run exhaustively, so the value is left unverified instead of guessed.
MAX_WALLETS_FOR_OVERLAP_CHECK = 20


class PatrimonyStatus(StrEnum):
    """Outcome of the individual patrimony calculation."""

    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    OVERLAP_SUSPECTED = "overlap_suspected"
    INVALID_DATA = "invalid_data"
    MISSING = "missing"


class AuditStatus(StrEnum):
    """Final patrimony audit verdict."""

    APPROVED = "APROVADA"
    DIVERGENCE = "DIVERGÊNCIA"
    NOT_VERIFIED = "NÃO VERIFICADA"


@dataclass(frozen=True)
class ClientPatrimony:
    """Individual patrimony of one client.

    Attributes:
        client_id: Client identifier as returned by the API.
        value: Audited patrimony in API units; ``None`` unless VERIFIED.
        status: Calculation outcome.
        wallet_count: Number of wallets received for the client.
        reason: Short diagnostic text (no personal data).
    """

    client_id: Any
    value: Decimal | None
    status: PatrimonyStatus
    wallet_count: int = 0
    reason: str = ""

    @property
    def is_verified(self) -> bool:
        """Whether :attr:`value` is an audited figure."""
        return self.status is PatrimonyStatus.VERIFIED


@dataclass(frozen=True)
class ReconciliationResult:
    """Company-level reconciliation outcome.

    Attributes:
        status: Audit verdict.
        company_total: ``totalCurrentValue`` from the API.
        clients_total: Sum of verified individual values (complete only when
            every client is verified).
        difference: ``company_total - clients_total``; ``None`` when it cannot
            be computed reliably.
        unresolved_clients: Clients whose patrimony is not verified.
        orphan_patrimony_ids: ``clientId`` values present in the patrimony
            endpoint but absent from the client list.
        reasons: Human-readable explanations for a non-approved verdict.
    """

    status: AuditStatus
    company_total: Decimal | None
    clients_total: Decimal
    difference: Decimal | None
    unresolved_clients: int
    orphan_patrimony_ids: list[Any] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def to_decimal(value: Any) -> Decimal | None:
    """Convert an API monetary value to :class:`Decimal` without guessing.

    Only JSON numbers (``int`` or ``Decimal`` from ``parse_float=Decimal``)
    are accepted; strings, booleans, floats and ``None`` return ``None``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    return None


def has_subset_overlap(values: list[Decimal]) -> bool:
    """Detect a wallet whose value equals the sum of other wallets.

    A non-zero wallet equal to the sum of any non-empty subset of the other
    non-zero wallets is the arithmetic signature of an aggregating wallet
    (e.g. Bank A 1000 + Bank B 2000 = Consolidated 3000, or a consolidated
    wallet mirroring a single bank). It is evidence of possible double
    counting, not proof, so callers must treat it as blocking.

    Args:
        values: Wallet values of a single client.

    Returns:
        True when such a combination exists.
    """
    for index, target in enumerate(values):
        if target == 0:
            continue
        others = [v for i, v in enumerate(values) if i != index and v != 0]
        reachable: set[Decimal] = set()
        for value in others:
            reachable |= {value} | {value + partial for partial in reachable}
            if target in reachable:
                return True
    return False


def compute_client_patrimony(entry: dict[str, Any], policy: WalletPolicy) -> ClientPatrimony:
    """Compute the audited patrimony of a client from its wallets.

    Args:
        entry: One element of ``clients.content`` from the patrimony endpoint.
        policy: Wallet combination policy (see :class:`WalletPolicy`).

    Returns:
        The calculation outcome. Only VERIFIED results carry a value.
    """
    client_id = entry.get("clientId")
    wallets = entry.get("wallets")
    if not isinstance(wallets, list):
        return ClientPatrimony(client_id, None, PatrimonyStatus.INVALID_DATA, 0, "wallets ausente")

    count = len(wallets)
    values: list[Decimal] = []
    wallet_ids: list[Any] = []
    for wallet in wallets:
        value = to_decimal(wallet.get("currentValue")) if isinstance(wallet, dict) else None
        if value is None:
            return ClientPatrimony(
                client_id, None, PatrimonyStatus.INVALID_DATA, count, "currentValue inválido"
            )
        values.append(value)
        wallet_ids.append(wallet.get("walletInfoId"))

    known_ids = [wid for wid in wallet_ids if wid is not None]
    if len(known_ids) != len(set(known_ids)):
        return ClientPatrimony(
            client_id, None, PatrimonyStatus.OVERLAP_SUSPECTED, count, "walletInfoId repetido"
        )

    if count == 0:
        return ClientPatrimony(client_id, Decimal(0), PatrimonyStatus.VERIFIED, 0, "sem carteiras")
    if count == 1:
        return ClientPatrimony(client_id, values[0], PatrimonyStatus.VERIFIED, 1, "carteira única")

    if count > MAX_WALLETS_FOR_OVERLAP_CHECK:
        return ClientPatrimony(
            client_id, None, PatrimonyStatus.AMBIGUOUS, count, "carteiras demais para verificar"
        )
    if has_subset_overlap(values):
        return ClientPatrimony(
            client_id,
            None,
            PatrimonyStatus.OVERLAP_SUSPECTED,
            count,
            "carteira com valor igual à soma de outras",
        )
    if policy is WalletPolicy.UNCONFIRMED:
        return ClientPatrimony(
            client_id,
            None,
            PatrimonyStatus.AMBIGUOUS,
            count,
            "múltiplas carteiras sem exclusividade confirmada",
        )
    return ClientPatrimony(
        client_id, sum(values, Decimal(0)), PatrimonyStatus.VERIFIED, count, "soma de carteiras exclusivas"
    )


def raw_wallets_total(entries: list[dict[str, Any]]) -> Decimal | None:
    """Sum every wallet of every client, without any overlap treatment.

    Diagnostic only: it is the figure that naive summing would produce and is
    never used as an audited value.

    Returns:
        The sum, or ``None`` if any wallet value is missing or invalid.
    """
    total = Decimal(0)
    for entry in entries:
        wallets = entry.get("wallets")
        if not isinstance(wallets, list):
            return None
        for wallet in wallets:
            value = to_decimal(wallet.get("currentValue")) if isinstance(wallet, dict) else None
            if value is None:
                return None
            total += value
    return total


def reconcile(
    company_total: Decimal | None,
    patrimonies: list[ClientPatrimony],
    orphan_patrimony_ids: list[Any] | None = None,
) -> ReconciliationResult:
    """Compare the company total with the sum of individual patrimonies.

    Approval requires every client to be verified, no orphan patrimony
    records and an exact (zero) difference. No tolerance is applied.

    Args:
        company_total: ``totalCurrentValue`` from the API.
        patrimonies: One entry per client in the report.
        orphan_patrimony_ids: Patrimony ``clientId`` values with no client.

    Returns:
        The reconciliation result.
    """
    orphans = list(orphan_patrimony_ids or [])
    unresolved = [p for p in patrimonies if not p.is_verified]
    clients_total = sum((p.value for p in patrimonies if p.value is not None), Decimal(0))
    reasons: list[str] = []

    if company_total is None:
        reasons.append("totalCurrentValue ausente ou inválido na resposta da API.")
    if unresolved:
        reasons.append(f"{len(unresolved)} cliente(s) com patrimônio não apurado de forma confiável.")
    if orphans:
        reasons.append(f"{len(orphans)} registro(s) de patrimônio sem cliente correspondente no cadastro.")

    if company_total is None or unresolved:
        return ReconciliationResult(
            AuditStatus.NOT_VERIFIED, company_total, clients_total, None, len(unresolved), orphans, reasons
        )

    difference = company_total - clients_total
    if difference != 0:
        reasons.append("Soma dos patrimônios individuais difere do total da empresa.")
        status = AuditStatus.DIVERGENCE
    elif orphans:
        status = AuditStatus.NOT_VERIFIED
    else:
        status = AuditStatus.APPROVED
    return ReconciliationResult(status, company_total, clients_total, difference, 0, orphans, reasons)
