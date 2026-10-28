"""Multi-currency diagnostics: totals per currency, never mixed."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.currency import (
    UNIDENTIFIED,
    apply_currency_gate,
    build_breakdown,
    currency_gate_reasons,
    parse_wallet_detail,
)
from src.reconciliation import AuditStatus, ClientPatrimony, PatrimonyStatus, reconcile
from tests.conftest import make_client, make_pl_entry, make_wallet_detail

NOW = datetime(2026, 10, 28, 14, 0, 0)


def details_for(entries: list[dict], **per_wallet: dict) -> dict:
    """Parsed details for every wallet; ``per_wallet`` overrides by walletInfoId."""
    result = {}
    for entry in entries:
        for wallet in entry["wallets"]:
            wid = wallet["walletInfoId"]
            result[wid] = parse_wallet_detail(make_wallet_detail(wid, **per_wallet.get(str(wid), {})))
    return result


def entry(client_id: int, *wallets: tuple[int, object, str]) -> dict:
    """consolidated-pl entry with explicit ``(walletInfoId, currentValue, coin)``."""
    return {
        "clientId": client_id,
        "wallets": [
            {"walletInfoId": wid, "currentValue": value, "coin": coin} for wid, value, coin in wallets
        ],
    }


def coin_details(entries: list[dict]) -> dict:
    return {
        w["walletInfoId"]: parse_wallet_detail(make_wallet_detail(w["walletInfoId"], walletCoin=w["coin"]))
        for e in entries
        for w in e["wallets"]
    }


def breakdown(entries, details, clients=(), total=None):
    return build_breakdown(entries, details, list(clients), total, NOW)


class TestParseWalletDetail:
    def test_documented_fields(self) -> None:
        raw = make_wallet_detail(101, walletCoin="dollar", lastConsolidation="2026-10-28T17:47:16.195Z")
        detail = parse_wallet_detail(raw)
        assert detail.coin == "DOLLAR"
        assert detail.last_consolidation == datetime(2026, 10, 28, 17, 47, 16, 195000)
        assert detail.has_consolidation_errors is False

    @pytest.mark.parametrize("coin", [None, "", "   ", 3])
    def test_missing_wallet_coin(self, coin: object) -> None:
        assert parse_wallet_detail(make_wallet_detail(1, walletCoin=coin)).coin is None

    @pytest.mark.parametrize("raw", [None, "", "ontem", 1730000000])
    def test_missing_or_invalid_consolidation_date(self, raw: object) -> None:
        assert parse_wallet_detail(make_wallet_detail(1, lastConsolidation=raw)).last_consolidation is None

    def test_non_boolean_error_flag_is_unknown(self) -> None:
        assert (
            parse_wallet_detail(make_wallet_detail(1, hasConsolidationErrors=None)).has_consolidation_errors
            is None
        )


class TestBreakdown:
    def test_brl_only_has_no_gate_reasons(self) -> None:
        entries = [make_pl_entry(1, 1000), make_pl_entry(2, Decimal("0.50"))]
        result = breakdown(entries, details_for(entries))
        assert result.totals == {"REAIS": Decimal("1000.50")}
        assert result.counts == {"REAIS": 2}
        assert currency_gate_reasons(result) == []

    def test_usd_and_other_currencies_are_totalled_separately(self) -> None:
        entries = [entry(1, (10, 1000, "REAIS"), (11, 200, "DOLLAR")), entry(2, (20, 50, "EURO"))]
        result = breakdown(entries, coin_details(entries))
        # Never a single mixed total: 1000 BRL, 200 USD and 50 EUR stay apart.
        assert result.totals == {"REAIS": Decimal(1000), "DOLLAR": Decimal(200), "EURO": Decimal(50)}
        assert result.unrecognised_coins == ["EURO"]
        reasons = currency_gate_reasons(result)
        assert any("DOLLAR" in r for r in reasons) and any("EURO" in r for r in reasons)

    def test_unknown_currency_and_missing_detail(self) -> None:
        entries = [make_pl_entry(1, 1000, 300)]
        details = details_for(entries, **{"1001": {"walletCoin": None}})
        del details[1000]
        result = breakdown(entries, details)
        assert result.counts == {UNIDENTIFIED: 2}
        assert result.missing_detail_ids == [1000]
        reasons = currency_gate_reasons(result)
        assert any("sem moeda identificada" in r for r in reasons)
        assert any("sem detalhe" in r for r in reasons)

    def test_consolidation_errors_and_unknown_status(self) -> None:
        entries = [make_pl_entry(1, 10, 20, 30)]
        details = details_for(
            entries, **{"1000": {"hasConsolidationErrors": True}, "1001": {"hasConsolidationErrors": None}}
        )
        result = breakdown(entries, details)
        assert result.error_wallet_ids == [1000]
        assert result.unknown_status_ids == [1001]
        assert any("erro de consolidação" in r for r in currency_gate_reasons(result))

    def test_missing_and_old_consolidation_dates_are_stale(self) -> None:
        entries = [make_pl_entry(1, 10, 20, 30)]
        details = details_for(
            entries,
            **{"1000": {"lastConsolidation": None}, "1001": {"lastConsolidation": "2026-10-20T10:00:00"}},
        )
        result = breakdown(entries, details)
        assert result.stale_wallet_ids == [1000, 1001]
        assert any("sem consolidação" in r for r in currency_gate_reasons(result))

    def test_not_collected(self) -> None:
        assert currency_gate_reasons(None, "desativada") == [
            "Moeda das carteiras não verificada: desativada."
        ]


class TestConversionEvidence:
    def test_implied_rate_is_diagnostic_only(self) -> None:
        entries = [entry(1, (10, 1000, "REAIS")), entry(2, (20, 100, "DOLLAR"))]
        result = breakdown(entries, coin_details(entries), total=Decimal("1534.62"))
        assert result.implied_rates == {"DOLLAR": Decimal("5.3462")}
        # Even with a rate that "closes", the gate keeps the audit unverified.
        assert any("DOLLAR" in r for r in currency_gate_reasons(result))

    def test_client_current_value_already_converted(self) -> None:
        entry = make_pl_entry(7, Decimal("1000.00"), 0)
        details = details_for([entry], **{"1000": {"walletCoin": "DOLLAR"}})
        client = make_client(7, currentValue=Decimal("5344.90"))
        result = breakdown([entry], details, clients=[client])
        ((client_id, coin, rate),) = result.client_rates
        assert (client_id, coin) == (7, "DOLLAR")
        assert rate.quantize(Decimal("0.0001")) == Decimal("5.3449")

    def test_brl_only_clients_have_no_conversion_ratio(self) -> None:
        entry = make_pl_entry(7, 850)
        result = breakdown([entry], details_for([entry]), clients=[make_client(7, currentValue=850)])
        assert result.client_rates == []


class TestCurrencyGate:
    def verified(self, value: int) -> ClientPatrimony:
        return ClientPatrimony(1, Decimal(value), PatrimonyStatus.VERIFIED, 1)

    def test_mixed_currencies_cannot_be_approved_even_if_numbers_match(self) -> None:
        # 1000 "units" of USD summed as if BRL would match a 1000 total.
        approved = reconcile(Decimal(1000), [self.verified(1000)])
        assert approved.status is AuditStatus.APPROVED
        gated = apply_currency_gate(approved, ["1 carteira(s) em DOLLAR: sem cotação."])
        assert gated.status is AuditStatus.NOT_VERIFIED
        assert gated.difference is None
        assert gated.reasons[-1].startswith("1 carteira(s) em DOLLAR")

    def test_divergence_over_mixed_currencies_is_discarded(self) -> None:
        divergent = reconcile(Decimal(5000), [self.verified(1000)])
        gated = apply_currency_gate(divergent, ["moedas"])
        assert gated.status is AuditStatus.NOT_VERIFIED and gated.difference is None

    def test_no_reasons_keeps_result(self) -> None:
        approved = reconcile(Decimal(1000), [self.verified(1000)])
        assert apply_currency_gate(approved, []) is approved
