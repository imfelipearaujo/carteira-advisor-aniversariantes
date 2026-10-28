"""Cliente PTAX (Banco Central): consulta histórica, fins de semana, falhas."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
import requests

from src.ptax import PtaxError, PtaxQuote, PtaxSeries, fetch_usd_brl_period, parse_period_response

BASE = "https://ptax.example.test/olinda/servico/PTAX/versao/v1"
NOW = datetime(2026, 10, 28, 15, 0)

# Boletins fictícios em formato real; 24/10 e 25/10 (fim de semana) não existem.
BODY = {
    "value": [
        {"cotacaoCompra": 5.0, "cotacaoVenda": 5.0377, "dataHoraCotacao": "2026-10-23 13:07:14.754567"},
        {"cotacaoCompra": 5.0, "cotacaoVenda": 5.5007, "dataHoraCotacao": "2026-10-26 13:09:51.106105"},
        {"cotacaoCompra": 5.0, "cotacaoVenda": 5.3346, "dataHoraCotacao": "2026-10-27 13:05:26.521898"},
        {"cotacaoCompra": 5.0, "cotacaoVenda": 5.3420, "dataHoraCotacao": "2026-10-28 13:05:31"},
    ]
}


def series(body: dict = BODY) -> PtaxSeries:
    raw = json.loads(json.dumps(body), parse_float=Decimal)
    return parse_period_response(raw, date(2026, 10, 18), date(2026, 10, 28), NOW)


class FakeResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self._text = text

    def json(self, **kwargs: Any) -> Any:
        return json.loads(self._text, **kwargs)


class FakeSession:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict, timeout: float) -> Any:
        self.calls.append((url, params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestSeries:
    def test_quotes_are_ordered_and_decimal(self) -> None:
        s = series()
        assert [q.published_at.date() for q in s.quotes] == [
            date(2026, 10, 23),
            date(2026, 10, 26),
            date(2026, 10, 27),
            date(2026, 10, 28),
        ]
        assert s.quotes[2].sell_rate == Decimal("5.3346")

    def test_historical_date(self) -> None:
        quote = series().latest_until(datetime(2026, 10, 27, 18, 0))
        assert quote == PtaxQuote(datetime(2026, 10, 27, 13, 5, 26, 521898), Decimal("5.3346"))

    def test_weekend_uses_last_previous_bulletin(self) -> None:
        quote = series().latest_until(datetime(2026, 10, 25, 23, 0))  # domingo
        assert quote.published_at.date() == date(2026, 10, 23)

    def test_holiday_gap_uses_last_previous_bulletin(self) -> None:
        body = {"value": [BODY["value"][0], BODY["value"][3]]}  # 26 e 27 sem boletim (feriado fictício)
        quote = series(body).latest_until(datetime(2026, 10, 27, 20, 0))
        assert quote.published_at.date() == date(2026, 10, 23)

    def test_never_uses_a_future_bulletin(self) -> None:
        # Consolidação às 04:16 de 28/10: a PTAX de 28/10 só sai às 13:05.
        quote = series().latest_until(datetime(2026, 10, 28, 4, 16))
        assert quote.published_at.date() == date(2026, 10, 27)

    def test_date_without_any_previous_quote(self) -> None:
        assert series().latest_until(datetime(2026, 10, 20, 12, 0)) is None

    def test_empty_period(self) -> None:
        assert series({"value": []}).latest_until(NOW) is None


class TestInvalidResponses:
    @pytest.mark.parametrize(
        "body",
        [
            [],
            {"semValue": []},
            {"value": [{"cotacaoVenda": "5,34", "dataHoraCotacao": "2026-10-27 13:05:26"}]},
            {"value": [{"cotacaoVenda": -1, "dataHoraCotacao": "2026-10-27 13:05:26"}]},
            {"value": [{"cotacaoVenda": True, "dataHoraCotacao": "2026-10-27 13:05:26"}]},
            {"value": [{"cotacaoVenda": 5.3, "dataHoraCotacao": "27/10/2026"}]},
            {"value": [{"cotacaoVenda": 5.3}]},
            {"value": ["texto"]},
            {"value": [{"cotacaoVenda": 5.3, "dataHoraCotacao": "2026-11-05 13:00:00"}]},  # fora do período
        ],
    )
    def test_rejected(self, body: Any) -> None:
        with pytest.raises(PtaxError):
            series(body)


class TestFetch:
    def test_request_format_and_parsing(self) -> None:
        session = FakeSession(FakeResponse(200, json.dumps(BODY)))
        result = fetch_usd_brl_period(BASE, date(2026, 10, 18), date(2026, 10, 28), session=session)
        url, params = session.calls[0]
        assert (
            url
            == BASE
            + "/odata/CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
        )
        assert params == {
            "@dataInicial": "'10-18-2026'",
            "@dataFinalCotacao": "'10-28-2026'",
            "$format": "json",
        }
        assert len(result.quotes) == 4
        assert (result.requested_from, result.requested_to) == (date(2026, 10, 18), date(2026, 10, 28))

    def test_http_error_after_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.ptax.time.sleep", lambda _: None)
        session = FakeSession(*(FakeResponse(500, "{}") for _ in range(3)))
        with pytest.raises(PtaxError, match="HTTP 500"):
            fetch_usd_brl_period(BASE, date(2026, 10, 1), date(2026, 10, 2), max_retries=2, session=session)
        assert len(session.calls) == 3

    def test_transient_failure_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.ptax.time.sleep", lambda _: None)
        session = FakeSession(requests.ConnectionError("x"), FakeResponse(200, json.dumps(BODY)))
        assert (
            len(fetch_usd_brl_period(BASE, date(2026, 10, 18), date(2026, 10, 28), session=session).quotes)
            == 4
        )

    def test_network_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.ptax.time.sleep", lambda _: None)
        session = FakeSession(requests.Timeout("x"), requests.Timeout("x"))
        with pytest.raises(PtaxError, match="rede"):
            fetch_usd_brl_period(BASE, date(2026, 10, 1), date(2026, 10, 2), max_retries=1, session=session)

    def test_invalid_json(self) -> None:
        session = FakeSession(FakeResponse(200, "<html>"))
        with pytest.raises(PtaxError, match="JSON"):
            fetch_usd_brl_period(BASE, date(2026, 10, 1), date(2026, 10, 2), session=session)

    def test_inverted_period(self) -> None:
        with pytest.raises(PtaxError, match="período"):
            fetch_usd_brl_period(BASE, date(2026, 10, 2), date(2026, 10, 1), session=FakeSession())
