"""Estimativa patrimonial por cliente com PTAX e comparação com o operacional."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.currency import parse_wallet_detail
from src.diagnostic import DIAGNOSTIC_SHEET, write_diagnostic_report
from src.estimate import (
    BAND_INSUFFICIENT,
    BAND_UNDEFINED,
    EstimateStatus,
    divergence_band,
    estimate_client,
    estimate_patrimony,
    ptax_window,
)
from src.ptax import PtaxQuote, PtaxSeries
from tests.conftest import make_client, make_wallet_detail

CONSOLIDATION = "2026-10-28T04:00:00.000000"
Q27 = PtaxQuote(datetime(2026, 10, 27, 13, 5), Decimal("5.3346"))
Q28 = PtaxQuote(datetime(2026, 10, 28, 13, 5), Decimal("5.3420"))
SERIES = PtaxSeries((Q27, Q28), date(2026, 10, 18), date(2026, 10, 28), datetime(2026, 10, 28, 15, 0))


def entry(client_id: int, *wallets: tuple[int, object]) -> dict:
    return {"clientId": client_id, "wallets": [{"walletInfoId": w, "currentValue": v} for w, v in wallets]}


def details(**coins: str) -> dict:
    """``details(w10="REAIS", w11="DOLLAR")`` → detalhes consolidados em 28/10 04:16."""
    return {
        int(key[1:]): parse_wallet_detail(
            make_wallet_detail(int(key[1:]), walletCoin=coin, lastConsolidation=CONSOLIDATION)
        )
        for key, coin in coins.items()
    }


def estimate(e: dict, d: dict, operational: object = None, **kwargs) -> object:
    op = Decimal(str(operational)) if operational is not None else None
    return estimate_client(e, d, SERIES, op, **kwargs)


class TestEstimateClient:
    def test_brl_wallets(self) -> None:
        result = estimate(
            entry(1, (10, 1000), (11, Decimal("500.50"))), details(w10="REAIS", w11="REAIS"), 1500.50
        )
        assert result.status is EstimateStatus.ESTIMATED
        assert result.estimated_brl == Decimal("1500.50")
        assert result.difference == 0 and result.difference_pct == 0
        assert result.quotes == ()

    def test_usd_wallet_uses_ptax_until_consolidation(self) -> None:
        # Consolidada às 04:16 de 28/10 → PTAX de 27/10 (a de 28/10 só sai às 13h).
        result = estimate(entry(1, (10, 100)), details(w10="DOLLAR"), 533.46)
        assert result.estimated_brl == Decimal("533.4600")
        assert result.usd_total == Decimal(100) and result.brl_total == 0
        assert result.quotes == (Q27,)
        assert result.difference == 0

    def test_mixed_currencies_are_never_summed_raw(self) -> None:
        result = estimate(entry(1, (10, 1000), (11, 100)), details(w10="REAIS", w11="DOLLAR"))
        assert result.estimated_brl == Decimal("1533.4600")  # e não 1100
        assert result.operational_brl is None and result.difference is None

    def test_zero_patrimony(self) -> None:
        result = estimate(entry(1), {}, 0)
        assert result.estimated_brl == 0
        assert result.difference == 0 and result.difference_pct is None
        assert result.band == BAND_UNDEFINED

    def test_zero_valued_wallets_need_no_rate_or_consolidation(self) -> None:
        d = {
            10: parse_wallet_detail(
                make_wallet_detail(
                    10, walletCoin="DOLLAR", lastConsolidation=None, hasConsolidationErrors=None
                )
            )
        }
        assert estimate(entry(1, (10, 0)), d, 0).status is EstimateStatus.ESTIMATED

    def test_small_and_large_divergences(self) -> None:
        small = estimate(entry(1, (10, 1000)), details(w10="REAIS"), 1005)
        large = estimate(entry(2, (10, 1000)), details(w10="REAIS"), 2000)
        assert small.band == "< 1%"
        assert large.difference == Decimal(-1000) and large.difference_pct == Decimal(-50)
        assert large.band == "≥ 25%"

    def test_threshold_crossing(self) -> None:
        result = estimate(
            entry(1, (10, 900_000)),
            details(w10="REAIS"),
            1_100_000,
            thresholds=(Decimal(1_000_000), Decimal(5_000_000)),
        )
        assert result.crossed_thresholds == (Decimal(1_000_000),)

    @pytest.mark.parametrize(
        ("wallets", "detail_overrides", "reason"),
        [
            (((10, 100),), None, "detalhe de carteira ausente"),
            (((10, 100),), {"walletCoin": "EURO"}, "moeda não suportada (EURO)"),
            (((10, 100),), {"walletCoin": None}, "moeda não suportada (ausente)"),
            (((10, 100),), {"hasConsolidationErrors": True}, "erro de consolidação"),
            (((10, 100),), {"hasConsolidationErrors": None}, "status de consolidação"),
            (((10, 100),), {"walletCoin": "DOLLAR", "lastConsolidation": None}, "sem data de consolidação"),
            (((10, 100),), {"walletCoin": "DOLLAR", "lastConsolidation": "2026-10-20T10:00:00"}, "sem PTAX"),
            (((10, 100),), {"walletCoin": "DOLLAR", "lastConsolidation": "2026-11-20T10:00:00"}, "sem PTAX"),
            (((10, "100"),), {}, "valor de carteira inválido"),
            (((10, 1000), (11, 2000), (12, 3000)), {}, "sobreposição"),
            (((10, 100), (10, 50)), {}, "carteira repetida"),
        ],
    )
    def test_incomplete_data_is_never_estimated(self, wallets, detail_overrides, reason) -> None:
        d = {}
        if detail_overrides is not None:
            for wid, _ in wallets:
                fields = {"walletCoin": "REAIS", "lastConsolidation": CONSOLIDATION, **detail_overrides}
                d[wid] = parse_wallet_detail(make_wallet_detail(wid, **fields))
        result = estimate(entry(1, *wallets), d, 1000)
        assert result.status is EstimateStatus.INSUFFICIENT
        assert reason in result.reason
        assert result.estimated_brl is None
        assert result.band == BAND_INSUFFICIENT


class TestGlobal:
    def build(self, total: object = 2533.46) -> object:
        clients = [
            make_client(1, currentValue=1000),
            make_client(2, currentValue=533.46),
            make_client(3, currentValue=50),
        ]
        entries = [entry(1, (10, 1000)), entry(2, (20, 100)), entry(3, (30, 50))]
        d = {
            **details(w10="REAIS", w20="DOLLAR"),
            30: parse_wallet_detail(make_wallet_detail(30, walletCoin="EURO")),
        }
        return estimate_patrimony(clients, entries, d, Decimal(str(total)), SERIES, Decimal(5))

    def test_totals_and_counts(self) -> None:
        result = self.build()
        assert len(result.estimated) == 2 and len(result.insufficient) == 1
        assert result.estimated_total == Decimal("1533.4600")
        assert result.global_difference == Decimal("-1000.0000")
        assert result.quotes_used == {Q27: 1}
        assert result.requested_moments == (datetime(2026, 10, 28, 4, 0),)

    def test_relevant_divergences(self) -> None:
        clients = [
            make_client(1, currentValue=1000),
            make_client(2, currentValue=1300),
            make_client(3, currentValue=0),
        ]
        entries = [entry(1, (10, 1040)), entry(2, (20, 1000)), entry(3, (30, 10))]
        d = details(w10="REAIS", w20="REAIS", w30="REAIS")
        result = estimate_patrimony(clients, entries, d, Decimal(2050), SERIES, Decimal(5))
        # 4% não é relevante; -23% é; operacional zero com estimativa > 0 é.
        assert [c.client_id for c in result.relevant] == [2, 3]
        assert result.band_counts == {"1% a 5%": 1, "10% a 25%": 1, BAND_UNDEFINED: 1}

    def test_ptax_window(self) -> None:
        moments = [datetime(2026, 10, 28, 4), datetime(2026, 10, 27, 23)]
        assert ptax_window(moments, 10, date(2026, 10, 28)) == (date(2026, 10, 17), date(2026, 10, 28))
        assert ptax_window([], 10, date(2026, 10, 28)) == (date(2026, 10, 18), date(2026, 10, 28))

    def test_divergence_bands(self) -> None:
        assert [divergence_band(Decimal(v)) for v in ("0.5", "-3", "7", "-24.99", "25", "300")] == [
            "< 1%",
            "1% a 5%",
            "5% a 10%",
            "10% a 25%",
            "≥ 25%",
            "≥ 25%",
        ]


def test_diagnostic_report(tmp_path: Path) -> None:
    clients = [
        make_client(1, name="Cliente Ficticio Um", currentValue=1000),
        make_client(2, currentValue=533.46),
    ]
    entries = [entry(1, (10, 1000)), entry(2, (20, 100))]
    result = estimate_patrimony(
        clients, entries, details(w10="REAIS", w20="DOLLAR"), Decimal(1533.46), SERIES, Decimal(5)
    )
    path = write_diagnostic_report(
        result,
        clients,
        {"1": ("PF", "ATIVO"), "2": ("PJ", "ATIVO")},
        tmp_path / "output",
        datetime(2026, 10, 28, 16),
    )
    assert path == tmp_path / "output" / "diagnostico" / "Diagnostico_Patrimonial_2026-10-28_160000.xlsx"
    sheet = load_workbook(path)[DIAGNOSTIC_SHEET]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 2 and sheet.freeze_panes == "A2" and sheet.auto_filter.ref
    by_id = {row[0]: row for row in rows}
    assert by_id[1][1] == "Cliente Ficticio Um"
    assert (
        by_id[2][6] == 100 and by_id[2][7] == pytest.approx(5.3346) and by_id[2][9] == pytest.approx(533.46)
    )
    assert not list((tmp_path / "output").glob(".tmp_*"))


class TestZeroPatrimony:
    def detail(self, wid: int, **kw: object):
        fields = {"walletCoin": "REAIS", "lastConsolidation": CONSOLIDATION, **kw}
        return parse_wallet_detail(make_wallet_detail(wid, **fields))

    def run(self, client: dict, entries: list, details: dict):
        from src.estimate import classify_zero_patrimony

        return classify_zero_patrimony([client], entries, details)

    def test_zero_with_consolidated_wallet(self) -> None:
        (z,) = self.run(make_client(1, currentValue=0), [entry(1, (10, 0))], {10: self.detail(10)})
        assert z.category.value == "PL zero com carteira consolidada"
        assert (z.wallet_count, z.coins, z.has_errors, z.never_consolidated) == (1, ("REAIS",), False, 0)
        assert z.last_consolidation == datetime(2026, 10, 28, 4, 0)

    def test_zero_without_consolidation(self) -> None:
        (z,) = self.run(
            make_client(1, currentValue=0), [entry(1, (10, 0))], {10: self.detail(10, lastConsolidation=None)}
        )
        assert z.category.value == "PL zero sem consolidação"
        assert z.last_consolidation is None and z.never_consolidated == 1

    def test_zero_with_insufficient_data(self) -> None:
        assert (
            self.run(make_client(1, currentValue=0), [], {})[0].category.value
            == "PL zero com dados insuficientes"
        )
        assert self.run(make_client(1, currentValue=0), [entry(1, (10, 0))], {})[0].category.value == (
            "PL zero com dados insuficientes"
        )
        errors = {10: self.detail(10, hasConsolidationErrors=True)}
        (z,) = self.run(make_client(1, currentValue=0), [entry(1, (10, 0))], errors)
        assert z.category.value == "PL zero com dados insuficientes" and z.has_errors is True

    def test_zero_in_registry_but_wallets_with_value(self) -> None:
        (z,) = self.run(make_client(1, currentValue=0), [entry(1, (10, 250))], {10: self.detail(10)})
        assert z.category.value == "PL zero no cadastro, carteiras com valor"
        assert z.wallets_total == {"REAIS": Decimal(250)}

    @pytest.mark.parametrize("value", [None, "0", 1, Decimal("0.01")])
    def test_only_exact_numeric_zero_counts(self, value: object) -> None:
        assert self.run(make_client(1, currentValue=value), [entry(1, (10, 0))], {10: self.detail(10)}) == []


def test_diagnostic_report_with_annotations(tmp_path: Path) -> None:
    from src.diagnostic import DIAGNOSTIC_HEADERS

    clients = [make_client(1, currentValue=1000), make_client(2, currentValue=533.46)]
    entries = [entry(1, (10, 1000)), entry(2, (20, 100))]
    result = estimate_patrimony(
        clients, entries, details(w10="REAIS", w20="DOLLAR"), Decimal(1533.46), SERIES, Decimal(5)
    )
    notes = {
        "1": {
            "consolidation": datetime(2026, 10, 28, 16, 51),
            "never": 0,
            "changed": True,
            "duplicate": "POSSÍVEL X",
        },
        "2": {"consolidation": None, "never": 1, "changed": False, "duplicate": ""},
    }
    path = write_diagnostic_report(result, clients, {}, tmp_path, datetime(2026, 10, 28, 17), notes)
    sheet = load_workbook(path)[DIAGNOSTIC_SHEET]
    assert tuple(c.value for c in sheet[1]) == DIAGNOSTIC_HEADERS
    assert DIAGNOSTIC_HEADERS[-4:] == (
        "Última consolidação",
        "Carteiras nunca consolidadas",
        "Alterado durante a coleta",
        "Possível duplicidade",
    )
    rows = {r[0]: r for r in sheet.iter_rows(min_row=2, values_only=True)}
    assert rows[1][-4:] == (datetime(2026, 10, 28, 16, 51), 0, "Sim", "POSSÍVEL X")
    assert rows[2][-4:] == (None, 1, "Não", None)
    assert sheet.auto_filter.ref == f"A1:R{len(rows) + 1}"
