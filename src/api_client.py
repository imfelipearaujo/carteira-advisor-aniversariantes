"""Read-only HTTP client for the Carteira Advisor company API.

Only ``GET`` requests are issued. Every paginated collection is validated
page by page (see :func:`collect_pages`); any doubt about completeness raises
:class:`CollectionIntegrityError` so that no definitive report is published
from partial data.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests

from src.config import Settings
from src.errors import AuthenticationError, CollectionIntegrityError, IntegrationError

logger = logging.getLogger(__name__)

CLIENTS_PATH = "/company-api/clients"
CONSULTANTS_PATH = "/company-api/consultants"

WALLET_DETAIL_PATH = "/company-api/wallet/{wallet_id}"

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
# Upper bound for a server-requested Retry-After, so a misbehaving header
# cannot stall the run indefinitely.
_MAX_RETRY_AFTER_SECONDS = 60.0


def _retry_wait(response: Any, attempt: int) -> float:
    """Seconds to wait before retrying: ``Retry-After`` if numeric, else backoff."""
    raw = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
    try:
        return min(max(float(raw), 0.0), _MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError):
        return float(min(2 ** (attempt - 1), 10))


PageFetcher = Callable[[int], Any]


@dataclass(frozen=True)
class ConsolidatedPL:
    """Raw content of ``/company-api/wallet/consolidated-pl`` after pagination.

    Attributes:
        total_current_value: ``totalCurrentValue`` as returned (``None`` if absent).
        clients: Concatenated ``clients.content`` entries of all pages.
    """

    total_current_value: Decimal | None
    clients: list[dict[str, Any]]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_int(page: dict[str, Any], key: str, resource: str, page_index: int) -> int:
    value = page.get(key)
    if not _is_int(value) or value < 0:
        raise CollectionIntegrityError(
            f"{resource}: campo de paginação '{key}' ausente ou inválido na página {page_index}."
        )
    return value


def _check_unique_ids(records: list[dict[str, Any]], id_key: str, resource: str) -> None:
    seen: set[Any] = set()
    duplicates: set[Any] = set()
    for record in records:
        if not isinstance(record, dict):
            raise CollectionIntegrityError(f"{resource}: registro em formato inesperado.")
        record_id = record.get(id_key)
        if record_id is None or isinstance(record_id, bool):
            raise CollectionIntegrityError(f"{resource}: registro sem '{id_key}'.")
        if record_id in seen:
            duplicates.add(record_id)
        seen.add(record_id)
    if duplicates:
        sample = sorted(map(str, duplicates))[:10]
        raise CollectionIntegrityError(
            f"{resource}: {len(duplicates)} '{id_key}' duplicado(s) na coleta "
            f"(exemplos: {', '.join(sample)})."
        )


def collect_pages(
    fetch_page: PageFetcher,
    resource: str,
    id_key: str,
    max_pages: int,
    page_getter: Callable[[Any], Any] = lambda body: body,
    invariant_getter: Callable[[Any], Any] | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Walk every page of a Spring-style page object and validate it.

    A response that is a plain JSON list (instead of a page object) is
    accepted as an unpaginated collection.

    Args:
        fetch_page: Returns the decoded body for a zero-based page index.
        resource: Human-readable name used in error messages.
        id_key: Field that uniquely identifies each record.
        max_pages: Safety cap on the number of requests.
        page_getter: Extracts the page object from the response body.
        invariant_getter: Extracts a value that must be identical on every
            page (e.g. ``totalCurrentValue``).

    Returns:
        The concatenated records and the invariant value of the first page.

    Raises:
        CollectionIntegrityError: On any inconsistency that prevents
            guaranteeing a complete collection.
    """
    records: list[dict[str, Any]] = []
    first_invariant: Any = None
    expected_total_pages: int | None = None
    expected_total_elements: int | None = None
    page_index = 0

    while True:
        if page_index >= max_pages:
            raise CollectionIntegrityError(
                f"{resource}: limite de {max_pages} páginas atingido sem encontrar a última."
            )
        body = fetch_page(page_index)
        invariant = invariant_getter(body) if invariant_getter else None
        if page_index == 0:
            first_invariant = invariant
        elif invariant != first_invariant:
            raise CollectionIntegrityError(
                f"{resource}: valor de referência mudou durante a coleta (página {page_index})."
            )

        page = page_getter(body)
        if isinstance(page, list) and page_index == 0:
            records = list(page)
            logger.info("%s: resposta sem paginação com %d registro(s).", resource, len(records))
            break
        if not isinstance(page, dict) or not isinstance(page.get("content"), list):
            raise CollectionIntegrityError(
                f"{resource}: resposta da página {page_index} não contém 'content'."
            )

        content: list[dict[str, Any]] = page["content"]
        total_pages = _require_int(page, "totalPages", resource, page_index)
        total_elements = _require_int(page, "totalElements", resource, page_index)
        if expected_total_pages is None:
            expected_total_pages, expected_total_elements = total_pages, total_elements
        elif (total_pages, total_elements) != (expected_total_pages, expected_total_elements):
            raise CollectionIntegrityError(
                f"{resource}: totais alterados durante a coleta (página {page_index})."
            )

        number = page.get("number")
        if number is not None and number != page_index:
            raise CollectionIntegrityError(f"{resource}: página solicitada {page_index}, recebida {number}.")
        number_of_elements = page.get("numberOfElements")
        if number_of_elements is not None and number_of_elements != len(content):
            raise CollectionIntegrityError(
                f"{resource}: 'numberOfElements' diverge do conteúdo na página {page_index}."
            )

        records.extend(content)
        is_final = page_index >= total_pages - 1
        last = page.get("last")
        if last is not None and bool(last) != is_final:
            raise CollectionIntegrityError(
                f"{resource}: indicador 'last' inconsistente na página {page_index}."
            )
        if not is_final and not content:
            raise CollectionIntegrityError(f"{resource}: página intermediária {page_index} veio vazia.")
        logger.info(
            "%s: página %d/%d com %d registro(s).",
            resource,
            page_index + 1,
            max(total_pages, 1),
            len(content),
        )
        if is_final:
            break
        page_index += 1

    if expected_total_elements is not None and len(records) != expected_total_elements:
        raise CollectionIntegrityError(
            f"{resource}: {len(records)} registro(s) coletado(s), "
            f"{expected_total_elements} informado(s) em 'totalElements'."
        )
    _check_unique_ids(records, id_key, resource)
    return records, first_invariant


class CarteiraAdvisorClient:
    """Thin read-only wrapper around the Carteira Advisor company API."""

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        """Create the client.

        Args:
            settings: Runtime settings; must contain a token.
            session: Optional pre-built session (tests inject mocks here).

        Raises:
            AuthenticationError: If no token is configured.
        """
        if not settings.has_token:
            raise AuthenticationError("Token ausente: nenhuma requisição autenticada será feita.")
        self._settings = settings
        self._session = session or requests.Session()
        credential = f"{settings.auth_scheme} {settings.token}".strip()
        self._session.headers.update({settings.auth_header: credential, "Accept": "application/json"})
        self._request_count = 0
        self._last_request_at: float | None = None

    @property
    def request_count(self) -> int:
        """HTTP requests sent by this client (retries included)."""
        return self._request_count

    def _throttle(self) -> None:
        """Keep at most ``max_requests_per_minute`` requests per minute.

        Requests are evenly spaced (60 / limit seconds apart) instead of sent
        in bursts, so the provider limit holds over any one-minute window.
        """
        limit = self._settings.max_requests_per_minute
        if limit <= 0:
            return
        if self._last_request_at is not None:
            wait = 60 / limit - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _get(self, path: str, params: dict[str, Any] | None = None, base_url: str | None = None) -> Any:
        """Issue a GET with retries for transient failures and decode JSON.

        Floats are decoded as :class:`~decimal.Decimal` so monetary values
        keep the exact precision sent by the API.
        """
        url = f"{base_url or self._settings.base_url}{path}"
        attempts = self._settings.max_retries + 1
        for attempt in range(1, attempts + 1):
            self._throttle()
            self._request_count += 1
            try:
                response = self._session.get(url, params=params, timeout=self._settings.timeout_seconds)
            except (requests.ConnectionError, requests.Timeout) as exc:
                reason = type(exc).__name__
                if attempt < attempts:
                    logger.warning("GET %s falhou (%s), tentativa %d/%d.", path, reason, attempt, attempts)
                    time.sleep(min(2 ** (attempt - 1), 10))
                    continue
                raise IntegrationError(f"GET {path}: falha de rede ({reason}).") from exc

            status = response.status_code
            if status in (401, 403):
                raise AuthenticationError(f"GET {path}: acesso negado (HTTP {status}). Verifique o token.")
            if status in _TRANSIENT_STATUS and attempt < attempts:
                logger.warning("GET %s retornou HTTP %d, tentativa %d/%d.", path, status, attempt, attempts)
                time.sleep(_retry_wait(response, attempt))
                continue
            if status != 200:
                raise IntegrationError(f"GET {path}: HTTP {status} inesperado.")
            try:
                return response.json(parse_float=Decimal)
            except ValueError as exc:
                raise IntegrationError(f"GET {path}: resposta não é JSON válido.") from exc
        raise IntegrationError(f"GET {path}: tentativas esgotadas.")  # pragma: no cover

    def _page_fetcher(self, path: str, base_url: str | None = None) -> PageFetcher:
        def fetch(page_index: int) -> Any:
            params = {
                self._settings.page_param: page_index,
                self._settings.size_param: self._settings.page_size,
            }
            return self._get(path, params, base_url)

        return fetch

    def fetch_clients(self) -> list[dict[str, Any]]:
        """Return every client of the company, across all pages."""
        records, _ = collect_pages(
            self._page_fetcher(CLIENTS_PATH), "clientes", "id", self._settings.max_pages
        )
        return records

    def fetch_consultants(self) -> list[dict[str, Any]]:
        """Return every consultant of the company, across all pages."""
        records, _ = collect_pages(
            self._page_fetcher(CONSULTANTS_PATH), "consultores", "id", self._settings.max_pages
        )
        return records

    def fetch_wallet_detail(self, wallet_id: Any) -> dict[str, Any]:
        """Return ``GET {patrimony_base_url}/company-api/wallet/{id}``.

        ``walletInfoId`` from consolidated-pl is the ``{id}`` (verified in
        production on 2026-10-28).

        Raises:
            CollectionIntegrityError: If the body is not an object for that id.
        """
        path = WALLET_DETAIL_PATH.format(wallet_id=wallet_id)
        body = self._get(path, base_url=self._settings.patrimony_base_url)
        if not isinstance(body, dict) or str(body.get("id")) != str(wallet_id):
            raise CollectionIntegrityError(f"carteira {wallet_id}: detalhe em formato inesperado.")
        return body

    def fetch_wallet_details(self, wallet_ids: list[Any]) -> dict[Any, dict[str, Any]]:
        """Fetch wallet details one by one, pausing between requests.

        The pause (``wallet_detail_interval_seconds``) is a client-side rate
        limit; HTTP 429 is additionally handled by :meth:`_get`.
        """
        details: dict[Any, dict[str, Any]] = {}
        for index, wallet_id in enumerate(dict.fromkeys(wallet_ids)):
            if index and self._settings.wallet_detail_interval_seconds:
                time.sleep(self._settings.wallet_detail_interval_seconds)
            details[wallet_id] = self.fetch_wallet_detail(wallet_id)
        logger.info("Detalhes de carteiras coletados: %d.", len(details))
        return details

    def fetch_consolidated_pl(self) -> ConsolidatedPL:
        """Return the consolidated patrimony with all ``clients`` pages."""

        def page_getter(body: Any) -> Any:
            if not isinstance(body, dict):
                raise CollectionIntegrityError("patrimônio: resposta em formato inesperado.")
            return body.get("clients")

        def invariant_getter(body: Any) -> Any:
            return body.get("totalCurrentValue") if isinstance(body, dict) else None

        records, total = collect_pages(
            self._page_fetcher(self._settings.consolidated_pl_path, self._settings.patrimony_base_url),
            "patrimônio",
            "clientId",
            self._settings.max_pages,
            page_getter=page_getter,
            invariant_getter=invariant_getter,
        )
        total_value = None
        if total is not None and not isinstance(total, bool) and isinstance(total, (int, Decimal)):
            total_value = Decimal(total)
        return ConsolidatedPL(total_current_value=total_value, clients=records)
