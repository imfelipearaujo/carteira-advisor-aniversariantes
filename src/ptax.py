"""Read-only client for the Banco Central do Brasil PTAX USD/BRL series.

PTAX is used as an *independent* exchange-rate reference for the patrimony
coherence check. It is not the rate source of Carteira Advisor, whose
methodology is unknown (see docs/DECISIONS.md, D16 and D21).

Documentation: https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/documentacao
"""

from __future__ import annotations

import logging
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import requests

logger = logging.getLogger(__name__)

PTAX_SOURCE = "PTAX — Banco Central do Brasil (cotação de venda USD/BRL)"
_PERIOD_PATH = "/odata/CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
_TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")


class PtaxError(Exception):
    """PTAX could not be fetched or returned an unusable response."""


@dataclass(frozen=True)
class PtaxQuote:
    """One PTAX bulletin.

    Attributes:
        published_at: ``dataHoraCotacao`` (Brasília time, naive).
        sell_rate: ``cotacaoVenda`` (BRL per USD).
    """

    published_at: datetime
    sell_rate: Decimal


@dataclass(frozen=True)
class PtaxSeries:
    """Quotes of a date window, ordered by publication time.

    Attributes:
        quotes: Ordered quotes.
        requested_from: First date requested.
        requested_to: Last date requested.
        fetched_at: When the request was made.
    """

    quotes: tuple[PtaxQuote, ...]
    requested_from: date
    requested_to: date
    fetched_at: datetime

    def latest_until(self, moment: datetime) -> PtaxQuote | None:
        """Latest quote published at or before ``moment`` (never a future one)."""
        times = [quote.published_at for quote in self.quotes]
        index = bisect_right(times, moment)
        return self.quotes[index - 1] if index else None


def _parse_quote(item: Any) -> PtaxQuote:
    if not isinstance(item, dict):
        raise PtaxError("PTAX: item de cotação em formato inesperado.")
    rate = item.get("cotacaoVenda")
    if isinstance(rate, bool) or not isinstance(rate, (int, Decimal)) or rate <= 0:
        raise PtaxError("PTAX: cotacaoVenda ausente ou inválida.")
    raw_time = item.get("dataHoraCotacao")
    if isinstance(raw_time, str):
        for fmt in _TIMESTAMP_FORMATS:
            try:
                return PtaxQuote(datetime.strptime(raw_time, fmt), Decimal(rate))
            except ValueError:
                continue
    raise PtaxError("PTAX: dataHoraCotacao ausente ou inválida.")


def parse_period_response(
    body: Any, requested_from: date, requested_to: date, fetched_at: datetime
) -> PtaxSeries:
    """Validate a ``CotacaoDolarPeriodo`` body and build the series.

    Raises:
        PtaxError: If the body is not the documented shape, or a quote lies
            outside the requested window.
    """
    if not isinstance(body, dict) or not isinstance(body.get("value"), list):
        raise PtaxError("PTAX: resposta sem a lista 'value'.")
    quotes = sorted((_parse_quote(item) for item in body["value"]), key=lambda q: q.published_at)
    for quote in quotes:
        if not requested_from <= quote.published_at.date() <= requested_to:
            raise PtaxError("PTAX: cotação fora do período solicitado.")
    return PtaxSeries(tuple(quotes), requested_from, requested_to, fetched_at)


def fetch_usd_brl_period(
    base_url: str,
    start: date,
    end: date,
    timeout: float = 30.0,
    max_retries: int = 2,
    session: Any = None,
) -> PtaxSeries:
    """GET the PTAX USD/BRL bulletins between ``start`` and ``end`` (inclusive).

    Days without bulletins (weekends, holidays) simply do not appear.

    Raises:
        PtaxError: On network/HTTP failure or invalid response.
    """
    if start > end:
        raise PtaxError("PTAX: período inválido.")
    http = session or requests
    params = {
        "@dataInicial": f"'{start:%m-%d-%Y}'",
        "@dataFinalCotacao": f"'{end:%m-%d-%Y}'",
        "$format": "json",
    }
    url = base_url.rstrip("/") + _PERIOD_PATH
    for attempt in range(1, max_retries + 2):
        fetched_at = datetime.now()
        try:
            response = http.get(url, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt <= max_retries:
                time.sleep(min(2 ** (attempt - 1), 10))
                continue
            raise PtaxError(f"PTAX: falha de rede ({type(exc).__name__}).") from exc
        if response.status_code in (429, 500, 502, 503, 504) and attempt <= max_retries:
            time.sleep(min(2 ** (attempt - 1), 10))
            continue
        if response.status_code != 200:
            raise PtaxError(f"PTAX: HTTP {response.status_code}.")
        try:
            body = response.json(parse_float=Decimal)
        except ValueError as exc:
            raise PtaxError("PTAX: resposta não é JSON válido.") from exc
        series = parse_period_response(body, start, end, fetched_at)
        logger.info("PTAX: %d boletim(ns) entre %s e %s.", len(series.quotes), start, end)
        return series
    raise PtaxError("PTAX: tentativas esgotadas.")  # pragma: no cover
