"""Independent patrimony estimate per client, converted with PTAX.

``PL_ESTIMADO_BRL = SUM(REAIS wallets) + SUM(DOLLAR wallets x PTAX)``

Each DOLLAR wallet is converted with the latest PTAX sell rate published at
or before that wallet's ``lastConsolidation`` (never a later quote). The
estimate is a coherence check for commercial analysis, not a reproduction of
Carteira Advisor's methodology: it never changes the operational spreadsheet
nor approves the exact financial audit.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from src.currency import BASE_COIN, WalletDetail
from src.ptax import PTAX_SOURCE, PtaxQuote, PtaxSeries
from src.reconciliation import has_subset_overlap, to_decimal
from src.transformer import id_key

FOREIGN_COIN = "DOLLAR"
SUPPORTED_COINS = frozenset({BASE_COIN, FOREIGN_COIN})
# Upper bounds (percent) of the diagnostic divergence bands. Purely
# descriptive: no band means "approved".
DIVERGENCE_BANDS = (Decimal(1), Decimal(5), Decimal(10), Decimal(25))
BAND_UNDEFINED = "indefinida (operacional zero)"
BAND_NO_OPERATIONAL = "sem valor operacional"
BAND_INSUFFICIENT = "sem dados suficientes"


class EstimateStatus(StrEnum):
    """Whether an estimate could be computed for a client."""

    ESTIMATED = "ESTIMADO"
    INSUFFICIENT = "DADOS INSUFICIENTES"


@dataclass(frozen=True)
class ClientEstimate:
    """Estimate and comparison for one client of consolidated-pl.

    Attributes:
        client_id: ``clientId``.
        status: ESTIMATED or INSUFFICIENT.
        reason: Why the estimate is insufficient (empty otherwise).
        brl_total: Sum of REAIS wallets (BRL).
        usd_total: Sum of DOLLAR wallets (USD, original currency).
        estimated_brl: Estimated patrimony in BRL.
        operational_brl: ``clients.currentValue``.
        difference: ``estimated_brl - operational_brl``.
        difference_pct: ``difference / operational_brl x 100`` when defined.
        quotes: PTAX quotes used for this client's DOLLAR wallets.
        crossed_thresholds: Configured thresholds with operational and
            estimated values on different sides.
    """

    client_id: Any
    status: EstimateStatus
    reason: str = ""
    brl_total: Decimal | None = None
    usd_total: Decimal | None = None
    estimated_brl: Decimal | None = None
    operational_brl: Decimal | None = None
    difference: Decimal | None = None
    difference_pct: Decimal | None = None
    quotes: tuple[PtaxQuote, ...] = ()
    crossed_thresholds: tuple[Decimal, ...] = ()

    @property
    def band(self) -> str:
        """Descriptive divergence band."""
        if self.status is EstimateStatus.INSUFFICIENT:
            return BAND_INSUFFICIENT
        if self.operational_brl is None:
            return BAND_NO_OPERATIONAL
        if self.difference_pct is None:
            return BAND_UNDEFINED
        return divergence_band(self.difference_pct)


@dataclass(frozen=True)
class FxEstimate:
    """Result of the PTAX-based coherence check.

    Attributes:
        source: Rate source description.
        series: PTAX bulletins fetched (window, fetch time).
        clients: One estimate per consolidated-pl client.
        company_total: ``totalCurrentValue``.
        relevant_pct: Divergence (percent) above which a client is listed as
            relevant. Diagnostic only.
        thresholds: Configured patrimony thresholds (BRL), possibly empty.
    """

    source: str
    series: PtaxSeries
    clients: list[ClientEstimate]
    company_total: Decimal | None
    relevant_pct: Decimal
    thresholds: tuple[Decimal, ...] = ()
    requested_moments: tuple[datetime, ...] = field(default_factory=tuple)

    @property
    def estimated(self) -> list[ClientEstimate]:
        return [c for c in self.clients if c.status is EstimateStatus.ESTIMATED]

    @property
    def insufficient(self) -> list[ClientEstimate]:
        return [c for c in self.clients if c.status is EstimateStatus.INSUFFICIENT]

    @property
    def estimated_total(self) -> Decimal:
        """Sum of estimates of the clients with sufficient data."""
        return sum((c.estimated_brl for c in self.estimated if c.estimated_brl is not None), Decimal(0))

    @property
    def global_difference(self) -> Decimal | None:
        """``estimated_total - totalCurrentValue``; meaningful only if nothing is missing."""
        return None if self.company_total is None else self.estimated_total - self.company_total

    @property
    def global_difference_pct(self) -> Decimal | None:
        diff = self.global_difference
        if diff is None or not self.company_total:
            return None
        return diff / self.company_total * 100

    @property
    def relevant(self) -> list[ClientEstimate]:
        """Clients whose |divergence| reaches ``relevant_pct`` (or is undefined with a non-zero gap)."""
        return [c for c in self.estimated if _is_relevant(c, self.relevant_pct)]

    @property
    def band_counts(self) -> dict[str, int]:
        return dict(Counter(c.band for c in self.clients))

    @property
    def quotes_used(self) -> dict[PtaxQuote, int]:
        """Each PTAX quote used and how many client estimates relied on it."""
        counter: Counter[PtaxQuote] = Counter()
        for c in self.estimated:
            counter.update(set(c.quotes))
        return dict(counter)


def _is_relevant(estimate: ClientEstimate, relevant_pct: Decimal) -> bool:
    if estimate.difference_pct is not None:
        return abs(estimate.difference_pct) >= relevant_pct
    # Operational value zero: any non-zero estimate is a relevant divergence.
    return bool(estimate.difference)


def divergence_band(pct: Decimal) -> str:
    """Label of the band containing ``|pct|``."""
    value = abs(pct)
    lower = Decimal(0)
    for upper in DIVERGENCE_BANDS:
        if value < upper:
            return "< 1%" if lower == 0 else f"{lower}% a {upper}%"
        lower = upper
    return f"≥ {DIVERGENCE_BANDS[-1]}%"


def band_order() -> list[str]:
    """All band labels in display order."""
    labels = []
    lower = Decimal(0)
    for upper in DIVERGENCE_BANDS:
        labels.append("< 1%" if lower == 0 else f"{lower}% a {upper}%")
        lower = upper
    return [*labels, f"≥ {DIVERGENCE_BANDS[-1]}%", BAND_UNDEFINED, BAND_NO_OPERATIONAL, BAND_INSUFFICIENT]


def _insufficient(client_id: Any, reason: str, operational: Decimal | None) -> ClientEstimate:
    return ClientEstimate(client_id, EstimateStatus.INSUFFICIENT, reason, operational_brl=operational)


def estimate_client(
    entry: dict[str, Any],
    details: dict[Any, WalletDetail],
    series: PtaxSeries,
    operational: Decimal | None,
    thresholds: tuple[Decimal, ...] = (),
    window_days: int = 10,
) -> ClientEstimate:
    """Estimate one client's patrimony in BRL from its wallets.

    Requires complete data: every wallet with a valid value, a detail, a
    supported currency, no consolidation error, and, for non-zero DOLLAR
    wallets, a consolidation date and a PTAX quote published up to it.
    Any arithmetic sign of overlap between wallets blocks the estimate.
    """
    client_id = entry.get("clientId")
    wallets = entry.get("wallets")
    if not isinstance(wallets, list):
        return _insufficient(client_id, "lista de carteiras ausente", operational)

    values: list[Decimal] = []
    ids: list[Any] = []
    for wallet in wallets:
        value = to_decimal(wallet.get("currentValue")) if isinstance(wallet, dict) else None
        if value is None:
            return _insufficient(client_id, "valor de carteira inválido", operational)
        values.append(value)
        ids.append(wallet.get("walletInfoId"))
    if len(ids) != len(set(ids)):
        return _insufficient(client_id, "carteira repetida", operational)
    if has_subset_overlap(values):
        return _insufficient(client_id, "evidência de sobreposição entre carteiras", operational)

    brl = Decimal(0)
    usd = Decimal(0)
    converted = Decimal(0)
    quotes: list[PtaxQuote] = []
    for wallet_id, value in zip(ids, values, strict=True):
        detail = details.get(wallet_id)
        if detail is None:
            return _insufficient(client_id, "detalhe de carteira ausente", operational)
        if detail.coin not in SUPPORTED_COINS:
            return _insufficient(client_id, f"moeda não suportada ({detail.coin or 'ausente'})", operational)
        if detail.has_consolidation_errors is True:
            return _insufficient(client_id, "carteira com erro de consolidação", operational)
        if value == 0:
            continue  # zero contributes nothing in any currency
        if detail.has_consolidation_errors is None:
            return _insufficient(client_id, "status de consolidação não informado", operational)
        if detail.coin == BASE_COIN:
            brl += value
            continue
        if detail.last_consolidation is None:
            return _insufficient(client_id, "carteira em dólar sem data de consolidação", operational)
        quote = series.latest_until(detail.last_consolidation)
        if quote is None or detail.last_consolidation - quote.published_at > timedelta(days=window_days):
            return _insufficient(client_id, "sem PTAX disponível na janela até a consolidação", operational)
        usd += value
        converted += value * quote.sell_rate
        quotes.append(quote)

    estimated = brl + converted
    difference = pct = None
    crossed: tuple[Decimal, ...] = ()
    if operational is not None:
        difference = estimated - operational
        pct = difference / operational * 100 if operational != 0 else None
        crossed = tuple(t for t in thresholds if (operational >= t) != (estimated >= t))
    return ClientEstimate(
        client_id,
        EstimateStatus.ESTIMATED,
        brl_total=brl,
        usd_total=usd,
        estimated_brl=estimated,
        operational_brl=operational,
        difference=difference,
        difference_pct=pct,
        quotes=tuple(dict.fromkeys(quotes)),
        crossed_thresholds=crossed,
    )


def dollar_consolidation_moments(
    pl_entries: list[dict[str, Any]], details: dict[Any, WalletDetail]
) -> list[datetime]:
    """Consolidation timestamps of non-zero DOLLAR wallets (drives the PTAX window)."""
    moments = []
    for entry in pl_entries:
        for wallet in entry.get("wallets") or []:
            detail = details.get(wallet.get("walletInfoId"))
            value = to_decimal(wallet.get("currentValue"))
            if detail and detail.coin == FOREIGN_COIN and value and detail.last_consolidation:
                moments.append(detail.last_consolidation)
    return moments


def estimate_patrimony(
    clients: list[dict[str, Any]],
    pl_entries: list[dict[str, Any]],
    details: dict[Any, WalletDetail],
    company_total: Decimal | None,
    series: PtaxSeries,
    relevant_pct: Decimal,
    thresholds: tuple[Decimal, ...] = (),
    window_days: int = 10,
) -> FxEstimate:
    """Estimate every consolidated-pl client and build the coherence result."""
    operational = {id_key(c.get("id")): to_decimal(c.get("currentValue")) for c in clients}
    estimates = [
        estimate_client(
            entry, details, series, operational.get(id_key(entry.get("clientId"))), thresholds, window_days
        )
        for entry in pl_entries
    ]
    return FxEstimate(
        source=PTAX_SOURCE,
        series=series,
        clients=estimates,
        company_total=company_total,
        relevant_pct=relevant_pct,
        thresholds=thresholds,
        requested_moments=tuple(sorted(dollar_consolidation_moments(pl_entries, details))),
    )


def ptax_window(moments: list[datetime], window_days: int, today: date) -> tuple[date, date]:
    """Date window to request: ``window_days`` before the earliest moment up to the latest one."""
    if not moments:
        return today - timedelta(days=window_days), today
    return min(moments).date() - timedelta(days=window_days), min(max(moments).date(), today)


class ZeroCategory(StrEnum):
    """Why an active PF client shows zero operational patrimony."""

    CONSOLIDATED = "PL zero com carteira consolidada"
    NOT_CONSOLIDATED = "PL zero sem consolidação"
    INSUFFICIENT = "PL zero com dados insuficientes"
    WALLETS_WITH_VALUE = "PL zero no cadastro, carteiras com valor"
    NOT_EVALUATED = "PL zero (consolidação não avaliada: auditoria detalhada desativada)"


@dataclass(frozen=True)
class ZeroPatrimony:
    """Evidence about one client with ``clients.currentValue == 0``.

    Attributes:
        client_id: Client id.
        category: See :class:`ZeroCategory`. Never a verdict of integration
            failure: it only describes what the API shows.
        wallet_count: Wallets in consolidated-pl.
        coins: ``walletCoin`` of each wallet (``None`` if unknown).
        last_consolidation: Most recent ``lastConsolidation`` (``None`` if none).
        never_consolidated: Wallets without ``lastConsolidation``.
        has_errors: True if any wallet reports consolidation errors; ``None``
            if any status is not informed; False otherwise.
        wallets_total: Sum of wallet values per coin (original currency).
    """

    client_id: Any
    category: ZeroCategory
    wallet_count: int = 0
    coins: tuple[str | None, ...] = ()
    last_consolidation: datetime | None = None
    never_consolidated: int = 0
    has_errors: bool | None = None
    wallets_total: dict[str, Decimal] = field(default_factory=dict)


def classify_zero_patrimony(
    clients: list[dict[str, Any]],
    pl_entries: list[dict[str, Any]],
    details: dict[Any, WalletDetail],
    evaluate: bool = True,
) -> list[ZeroPatrimony]:
    """Describe every client of ``clients`` whose ``currentValue`` is exactly zero.

    Clients with an absent or invalid ``currentValue`` are not "zero" and are
    left out (they already carry a patrimony pendency). With ``evaluate`` False
    (fast mode, no wallet details) clients are only listed as NOT_EVALUATED.
    """
    entries = {id_key(e.get("clientId")): e for e in pl_entries}
    result = []
    for client in clients:
        if to_decimal(client.get("currentValue")) != 0:
            continue
        client_id = client.get("id")
        wallets = (entries.get(id_key(client_id)) or {}).get("wallets") or []
        found = [details.get(w.get("walletInfoId")) for w in wallets]
        totals: dict[str, Decimal] = {}
        for wallet, detail in zip(wallets, found, strict=True):
            coin = detail.coin if detail and detail.coin else "SEM_MOEDA"
            totals[coin] = totals.get(coin, Decimal(0)) + (
                to_decimal(wallet.get("currentValue")) or Decimal(0)
            )
        consolidations = [d.last_consolidation for d in found if d and d.last_consolidation]
        statuses = [d.has_consolidation_errors for d in found if d]
        never = sum(1 for d in found if d and d.last_consolidation is None)
        if any(statuses):
            has_errors: bool | None = True
        elif None in statuses or len(statuses) < len(found):
            has_errors = None
        else:
            has_errors = False

        if not evaluate:
            category = ZeroCategory.NOT_EVALUATED
        elif not wallets or None in found or any(to_decimal(w.get("currentValue")) is None for w in wallets):
            category = ZeroCategory.INSUFFICIENT
        elif any(value != 0 for value in totals.values()):
            category = ZeroCategory.WALLETS_WITH_VALUE
        elif has_errors is True:
            category = ZeroCategory.INSUFFICIENT
        elif never:
            category = ZeroCategory.NOT_CONSOLIDATED
        else:
            category = ZeroCategory.CONSOLIDATED
        result.append(
            ZeroPatrimony(
                client_id=client_id,
                category=category,
                wallet_count=len(wallets),
                coins=tuple(d.coin if d else None for d in found),
                last_consolidation=max(consolidations) if consolidations else None,
                never_consolidated=never,
                has_errors=has_errors,
                wallets_total=totals,
            )
        )
    return result
