"""Wallet currency diagnostics for the financial audit.

``/company-api/wallet/consolidated-pl`` returns ``wallets[].currentValue`` in
each wallet's **original currency** (verified in production on 2026-10-28:
a DOLLAR wallet's value times ~5.34 matched the client's
``clients.currentValue``). ``totalCurrentValue`` is expressed in BRL. The API exposes no
exchange rate, so values in different currencies are only ever totalled per
currency here and are never added together or converted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from src.reconciliation import AuditStatus, ReconciliationResult, to_decimal

BASE_COIN = "REAIS"
# walletCoin values observed in production. Any other non-blank code is kept
# under its own name but reported as unrecognised.
KNOWN_COINS = frozenset({"REAIS", "DOLLAR"})
UNIDENTIFIED = "SEM_MOEDA"
# A wallet consolidated longer ago than this, relative to the run, is
# reported as potentially stale.
STALE_AFTER = timedelta(days=1)


@dataclass(frozen=True)
class WalletDetail:
    """Relevant fields of ``GET /company-api/wallet/{id}``.

    Attributes:
        wallet_id: ``id`` (equal to ``walletInfoId`` of consolidated-pl).
        coin: ``walletCoin`` upper-cased, or ``None`` if absent/blank.
        last_consolidation: Parsed ``lastConsolidation``, or ``None``.
        has_consolidation_errors: ``hasConsolidationErrors`` (``None`` if not a bool).
        principal: ``principal`` flag; informative only, never used to
            identify aggregating wallets.
    """

    wallet_id: Any
    coin: str | None
    last_consolidation: datetime | None
    has_consolidation_errors: bool | None
    principal: bool | None


@dataclass(frozen=True)
class CurrencyBreakdown:
    """Wallet values grouped by original currency, never mixed.

    Attributes:
        totals: Sum of ``currentValue`` per coin code (``SEM_MOEDA`` for
            wallets whose currency is unknown).
        counts: Wallets per coin code.
        unrecognised_coins: Coin codes outside :data:`KNOWN_COINS`.
        missing_detail_ids: Wallets without a detail response.
        error_wallet_ids: Wallets with ``hasConsolidationErrors = true``.
        unknown_status_ids: Wallets where ``hasConsolidationErrors`` is not a bool.
        stale_wallet_ids: Wallets never consolidated or consolidated before
            ``now - STALE_AFTER``.
        implied_rates: Foreign coin -> rate that would make
            ``totalCurrentValue`` match (derived, NOT official).
        client_rates: ``(clientId, coin, clients.currentValue ratio)`` for
            clients holding a single foreign coin; evidence of conversion.
    """

    totals: dict[str, Decimal]
    counts: dict[str, int]
    unrecognised_coins: list[str] = field(default_factory=list)
    missing_detail_ids: list[Any] = field(default_factory=list)
    error_wallet_ids: list[Any] = field(default_factory=list)
    unknown_status_ids: list[Any] = field(default_factory=list)
    stale_wallet_ids: list[Any] = field(default_factory=list)
    implied_rates: dict[str, Decimal] = field(default_factory=dict)
    client_rates: list[tuple[Any, str, Decimal]] = field(default_factory=list)

    @property
    def foreign_coins(self) -> list[str]:
        """Coin codes other than BRL, including unknown currency."""
        return sorted(coin for coin in self.counts if coin != BASE_COIN)


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # Production sends naive local timestamps; drop any offset so they can be
    # compared with the naive run timestamp.
    return parsed.replace(tzinfo=None)


def parse_wallet_detail(raw: dict[str, Any]) -> WalletDetail:
    """Extract the audit-relevant fields from a wallet detail response."""
    coin = raw.get("walletCoin")
    coin = coin.strip().upper() if isinstance(coin, str) and coin.strip() else None
    errors = raw.get("hasConsolidationErrors")
    principal = raw.get("principal")
    return WalletDetail(
        wallet_id=raw.get("id"),
        coin=coin,
        last_consolidation=_parse_timestamp(raw.get("lastConsolidation")),
        has_consolidation_errors=errors if isinstance(errors, bool) else None,
        principal=principal if isinstance(principal, bool) else None,
    )


def build_breakdown(
    pl_entries: list[dict[str, Any]],
    details: dict[Any, WalletDetail],
    clients: list[dict[str, Any]],
    company_total: Decimal | None,
    now: datetime,
) -> CurrencyBreakdown:
    """Group wallet values by currency and collect consolidation diagnostics.

    Args:
        pl_entries: ``clients.content`` of consolidated-pl.
        details: Wallet details keyed by ``walletInfoId``.
        clients: Records from ``/company-api/clients`` (for conversion evidence).
        company_total: ``totalCurrentValue`` (BRL).
        now: Run timestamp used for staleness.

    Returns:
        The per-currency breakdown. Invalid wallet values are skipped here;
        they are already reported by the wallet-based audit.
    """
    totals: dict[str, Decimal] = defaultdict(Decimal)
    counts: dict[str, int] = defaultdict(int)
    missing, errors, unknown_status, stale = [], [], [], []
    client_parts: dict[Any, dict[str, Decimal]] = {}

    for entry in pl_entries:
        parts: dict[str, Decimal] = defaultdict(Decimal)
        for wallet in entry.get("wallets") or []:
            wallet_id = wallet.get("walletInfoId")
            detail = details.get(wallet_id)
            if detail is None:
                missing.append(wallet_id)
            coin = detail.coin if detail is not None and detail.coin else UNIDENTIFIED
            counts[coin] += 1
            value = to_decimal(wallet.get("currentValue"))
            if value is not None:
                totals[coin] += value
                parts[coin] += value
            if detail is None:
                continue
            if detail.has_consolidation_errors is True:
                errors.append(wallet_id)
            elif detail.has_consolidation_errors is None:
                unknown_status.append(wallet_id)
            if detail.last_consolidation is None or detail.last_consolidation < now - STALE_AFTER:
                stale.append(wallet_id)
        client_parts[entry.get("clientId")] = parts

    breakdown = CurrencyBreakdown(
        totals=dict(totals),
        counts=dict(counts),
        unrecognised_coins=sorted(c for c in counts if c not in KNOWN_COINS and c != UNIDENTIFIED),
        missing_detail_ids=missing,
        error_wallet_ids=errors,
        unknown_status_ids=unknown_status,
        stale_wallet_ids=stale,
    )
    return replace(
        breakdown,
        implied_rates=implied_rates(company_total, breakdown),
        client_rates=client_conversion_rates(client_parts, clients),
    )


def implied_rates(company_total: Decimal | None, breakdown: CurrencyBreakdown) -> dict[str, Decimal]:
    """Rate that would reconcile the total, when exactly one foreign coin exists.

    This is arithmetic derived from the total itself (circular), so it is a
    diagnostic hint only and must never be used to approve the audit.
    """
    foreign = [c for c in breakdown.foreign_coins if breakdown.totals.get(c, Decimal(0)) != 0]
    if company_total is None or len(foreign) != 1 or foreign[0] == UNIDENTIFIED:
        return {}
    coin = foreign[0]
    brl = breakdown.totals.get(BASE_COIN, Decimal(0))
    return {coin: (company_total - brl) / breakdown.totals[coin]}


def client_conversion_rates(
    client_parts: dict[Any, dict[str, Decimal]], clients: list[dict[str, Any]]
) -> list[tuple[Any, str, Decimal]]:
    """Ratio ``(clients.currentValue - BRL part) / foreign part`` per client.

    Only clients with exactly one non-zero foreign coin are considered.
    Ratios far from 1 show that ``clients.currentValue`` is already
    converted to BRL while consolidated-pl keeps the original currency.
    """
    registered = {str(c.get("id")): to_decimal(c.get("currentValue")) for c in clients}
    rates: list[tuple[Any, str, Decimal]] = []
    for client_id, parts in client_parts.items():
        foreign = {coin: value for coin, value in parts.items() if coin != BASE_COIN and value != 0}
        current = registered.get(str(client_id))
        if len(foreign) != 1 or current is None:
            continue
        ((coin, value),) = foreign.items()
        if coin == UNIDENTIFIED:
            continue
        rates.append((client_id, coin, (current - parts.get(BASE_COIN, Decimal(0))) / value))
    return rates


def currency_gate_reasons(
    breakdown: CurrencyBreakdown | None, unavailable_reason: str | None = None
) -> list[str]:
    """Reasons that prevent a currency-consistent reconciliation.

    Args:
        breakdown: Per-currency breakdown, or ``None`` if not collected.
        unavailable_reason: Why the breakdown is missing, if so.

    Returns:
        Empty list only when every wallet is in BRL, fully identified,
        consolidated recently and without consolidation errors.
    """
    if breakdown is None:
        return [f"Moeda das carteiras não verificada: {unavailable_reason or 'detalhes não coletados'}."]
    reasons: list[str] = []
    for coin in breakdown.foreign_coins:
        if coin == UNIDENTIFIED:
            reasons.append(f"{breakdown.counts[coin]} carteira(s) sem moeda identificada.")
        else:
            reasons.append(
                f"{breakdown.counts[coin]} carteira(s) em {coin}: a API não fornece cotação nem "
                "metodologia oficial de conversão para REAIS; moedas não são somadas."
            )
    if breakdown.missing_detail_ids:
        reasons.append(f"{len(breakdown.missing_detail_ids)} carteira(s) sem detalhe consultado.")
    if breakdown.error_wallet_ids:
        reasons.append(f"{len(breakdown.error_wallet_ids)} carteira(s) com erro de consolidação.")
    if breakdown.unknown_status_ids:
        reasons.append(
            f"{len(breakdown.unknown_status_ids)} carteira(s) sem status de consolidação informado."
        )
    if breakdown.stale_wallet_ids:
        reasons.append(
            f"{len(breakdown.stale_wallet_ids)} carteira(s) sem consolidação ou consolidada(s) há mais de "
            f"{STALE_AFTER.days} dia(s)."
        )
    return reasons


def apply_currency_gate(result: ReconciliationResult, reasons: list[str]) -> ReconciliationResult:
    """Downgrade the audit to NOT_VERIFIED when currencies cannot be reconciled.

    A difference computed over wallets in different currencies has no
    financial meaning, so it is discarded instead of being reported.
    """
    if not reasons:
        return result
    return replace(
        result,
        status=AuditStatus.NOT_VERIFIED,
        difference=None,
        reasons=[*result.reasons, *reasons],
    )
