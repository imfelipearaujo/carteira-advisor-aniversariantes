"""HTTP client: authentication, GET-only access, retries and pagination."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
import requests

from src.api_client import (
    CLIENTS_PATH,
    CarteiraAdvisorClient,
    collect_pages,
)
from src.config import DEFAULT_CONSOLIDATED_PL_PATH, Settings
from src.errors import AuthenticationError, CollectionIntegrityError, IntegrationError
from tests.conftest import make_client, make_pl_entry


def _json_number(value: Any) -> Any:
    """Serialise Decimal as a JSON number, as the real API does."""
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value).__name__)


class FakeResponse:
    def __init__(self, status: int, body: Any = None, raw: str | None = None) -> None:
        self.status_code = status
        self._text = raw if raw is not None else json.dumps(body, default=_json_number)

    def json(self, **kwargs: Any) -> Any:
        return json.loads(self._text, **kwargs)


class FakeSession:
    """Records GETs; refuses any other HTTP verb."""

    def __init__(self, responder: Any) -> None:
        self.headers: dict[str, str] = {}
        self.responder = responder
        self.requests: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        self.requests.append((url, params))
        result = self.responder(url, params)
        if isinstance(result, Exception):
            raise result
        return result

    def __getattr__(self, name: str) -> Any:
        if name in {"post", "put", "patch", "delete", "request"}:
            raise AssertionError(f"Método HTTP proibido: {name}")
        raise AttributeError(name)


def page(content: list[dict[str, Any]], number: int, total_pages: int, total_elements: int) -> dict[str, Any]:
    return {
        "content": content,
        "number": number,
        "totalPages": total_pages,
        "totalElements": total_elements,
        "numberOfElements": len(content),
        "last": number >= total_pages - 1,
    }


def paged_responder(pages: list[dict[str, Any]], wrap: Any = None) -> Any:
    def respond(url: str, params: dict[str, Any] | None) -> FakeResponse:
        index = params["page"]
        body = pages[index] if wrap is None else wrap(pages[index])
        return FakeResponse(200, body)

    return respond


def client_for(settings: Settings, responder: Any) -> tuple[CarteiraAdvisorClient, FakeSession]:
    session = FakeSession(responder)
    return CarteiraAdvisorClient(settings, session=session), session


class TestAuthentication:
    def test_missing_token_refuses_to_build_client(self, settings: Settings) -> None:
        session = FakeSession(lambda *_: pytest.fail("nenhuma requisição deveria ocorrer"))
        with pytest.raises(AuthenticationError):
            CarteiraAdvisorClient(replace(settings, token="  "), session=session)
        assert session.requests == []

    def test_token_header_is_configurable(self, settings: Settings) -> None:
        custom = replace(settings, auth_header="X-Api-Key", auth_scheme="")
        _, session = client_for(custom, lambda *_: FakeResponse(200, []))
        assert session.headers["X-Api-Key"] == "fake-token"

    def test_default_header_is_raw_token(self, settings: Settings) -> None:
        _, session = client_for(settings, lambda *_: FakeResponse(200, []))
        assert session.headers["token"] == "fake-token"
        assert "Authorization" not in session.headers

    def test_bearer_scheme_when_configured(self, settings: Settings) -> None:
        bearer = replace(settings, auth_header="Authorization", auth_scheme="Bearer")
        _, session = client_for(bearer, lambda *_: FakeResponse(200, []))
        assert session.headers["Authorization"] == "Bearer fake-token"

    def test_consolidated_pl_path_is_configurable(self, settings: Settings) -> None:
        body = {"totalCurrentValue": 0, "clients": []}
        api, session = client_for(
            replace(settings, consolidated_pl_path="/x/pl"), lambda *_: FakeResponse(200, body)
        )
        assert api.fetch_consolidated_pl().clients == []
        assert session.requests[0][0].endswith("/x/pl")

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_token(self, settings: Settings, status: int) -> None:
        api, _ = client_for(settings, lambda *_: FakeResponse(status, raw="Token não informado!"))
        with pytest.raises(AuthenticationError):
            api.fetch_clients()


class TestHttpFailures:
    def test_http_500_after_retries(self, settings: Settings) -> None:
        api, session = client_for(replace(settings, max_retries=1), lambda *_: FakeResponse(500, {}))
        with pytest.raises(IntegrationError, match="HTTP 500"):
            api.fetch_clients()
        assert len(session.requests) == 2

    def test_transient_failure_then_success(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.api_client.time.sleep", lambda _: None)
        responses = iter([requests.ConnectionError("boom"), FakeResponse(200, [make_client(1)])])
        api, _ = client_for(replace(settings, max_retries=2), lambda *_: next(responses))
        assert [c["id"] for c in api.fetch_clients()] == [1]

    def test_network_failure(self, settings: Settings) -> None:
        api, _ = client_for(settings, lambda *_: requests.Timeout("slow"))
        with pytest.raises(IntegrationError, match="falha de rede"):
            api.fetch_clients()

    def test_invalid_json(self, settings: Settings) -> None:
        api, _ = client_for(settings, lambda *_: FakeResponse(200, raw="<html>"))
        with pytest.raises(IntegrationError, match="JSON"):
            api.fetch_clients()

    def test_documented_patrimony_endpoint_404(self, settings: Settings) -> None:
        # Production behaviour observed on 2026-10-28.
        api, _ = client_for(settings, lambda *_: FakeResponse(404, {"status": 404, "error": "Not Found"}))
        with pytest.raises(IntegrationError, match="HTTP 404"):
            api.fetch_consolidated_pl()

    def test_404(self, settings: Settings) -> None:
        api, _ = client_for(settings, lambda *_: FakeResponse(404, {}))
        with pytest.raises(IntegrationError, match="HTTP 404"):
            api.fetch_consultants()


class TestPagination:
    def test_walks_all_client_pages(self, settings: Settings) -> None:
        pages = [
            page([make_client(1), make_client(2)], 0, 3, 5),
            page([make_client(3), make_client(4)], 1, 3, 5),
            page([make_client(5)], 2, 3, 5),
        ]
        api, session = client_for(settings, paged_responder(pages))
        assert [c["id"] for c in api.fetch_clients()] == [1, 2, 3, 4, 5]
        assert [p["page"] for _, p in session.requests] == [0, 1, 2]
        assert all(url.endswith(CLIENTS_PATH) for url, _ in session.requests)

    def test_unpaginated_list_response(self, settings: Settings) -> None:
        api, _ = client_for(settings, lambda *_: FakeResponse(200, [make_client(1), make_client(2)]))
        assert len(api.fetch_clients()) == 2

    def test_empty_collection(self, settings: Settings) -> None:
        api, _ = client_for(settings, paged_responder([page([], 0, 0, 0)]))
        assert api.fetch_clients() == []

    def test_consolidated_pl_pages_and_decimal(self, settings: Settings) -> None:
        pages = [
            page([make_pl_entry(1, 100)], 0, 2, 2),
            page([make_pl_entry(2, 200)], 1, 2, 2),
        ]

        def wrap(p: dict[str, Any]) -> dict[str, Any]:
            return {"totalCurrentValue": 300.1, "clients": p}

        api, session = client_for(settings, paged_responder(pages, wrap))
        result = api.fetch_consolidated_pl()
        assert result.total_current_value == Decimal("300.1")
        assert [c["clientId"] for c in result.clients] == [1, 2]
        assert session.requests[0][0].endswith(DEFAULT_CONSOLIDATED_PL_PATH)

    def test_total_changes_during_collection(self, settings: Settings) -> None:
        pages = [page([make_pl_entry(1, 100)], 0, 2, 2), page([make_pl_entry(2, 200)], 1, 2, 2)]
        totals = iter([300, 999])

        def wrap(p: dict[str, Any]) -> dict[str, Any]:
            return {"totalCurrentValue": next(totals), "clients": p}

        api, _ = client_for(settings, paged_responder(pages, wrap))
        with pytest.raises(CollectionIntegrityError, match="mudou"):
            api.fetch_consolidated_pl()

    def test_consolidated_pl_without_clients(self, settings: Settings) -> None:
        api, _ = client_for(settings, lambda *_: FakeResponse(200, {"totalCurrentValue": 1}))
        with pytest.raises(CollectionIntegrityError):
            api.fetch_consolidated_pl()


def run_pages(pages: list[Any], id_key: str = "id", max_pages: int = 100) -> list[dict[str, Any]]:
    records, _ = collect_pages(lambda i: pages[i], "teste", id_key, max_pages)
    return records


class TestPaginationIntegrity:
    def test_total_elements_mismatch(self) -> None:
        with pytest.raises(CollectionIntegrityError, match="totalElements"):
            run_pages([page([make_client(1)], 0, 1, 2)])

    def test_total_pages_changes(self) -> None:
        pages = [page([make_client(1)], 0, 2, 2), page([make_client(2)], 1, 3, 2)]
        with pytest.raises(CollectionIntegrityError, match="totais alterados"):
            run_pages(pages)

    def test_wrong_page_number(self) -> None:
        pages = [page([make_client(1)], 0, 2, 2), page([make_client(2)], 0, 2, 2)]
        with pytest.raises(CollectionIntegrityError, match="recebida 0"):
            run_pages(pages)

    def test_repeated_page_detected_as_duplicates(self) -> None:
        # Server ignores the page parameter and repeats page 0 without "number".
        repeated = page([make_client(1)], 0, 2, 2)
        repeated.pop("number")
        repeated.pop("last")
        with pytest.raises(CollectionIntegrityError, match="duplicado"):
            run_pages([repeated, repeated])

    def test_number_of_elements_mismatch(self) -> None:
        bad = page([make_client(1)], 0, 1, 1)
        bad["numberOfElements"] = 2
        with pytest.raises(CollectionIntegrityError, match="numberOfElements"):
            run_pages([bad])

    def test_last_flag_inconsistent(self) -> None:
        bad = page([make_client(1)], 0, 2, 2)
        bad["last"] = True
        with pytest.raises(CollectionIntegrityError, match="last"):
            run_pages([bad, page([make_client(2)], 1, 2, 2)])

    def test_empty_intermediate_page(self) -> None:
        with pytest.raises(CollectionIntegrityError, match="vazia"):
            run_pages([page([], 0, 2, 1), page([make_client(1)], 1, 2, 1)])

    def test_missing_pagination_fields(self) -> None:
        with pytest.raises(CollectionIntegrityError, match="totalPages"):
            run_pages([{"content": [make_client(1)]}])

    def test_missing_content(self) -> None:
        with pytest.raises(CollectionIntegrityError, match="content"):
            run_pages([{"totalPages": 1, "totalElements": 0}])

    def test_duplicate_ids_across_pages(self) -> None:
        pages = [page([make_client(1)], 0, 2, 2), page([make_client(1, name="Outro")], 1, 2, 2)]
        with pytest.raises(CollectionIntegrityError, match="duplicado"):
            run_pages(pages)

    def test_record_without_id(self) -> None:
        with pytest.raises(CollectionIntegrityError, match="sem 'id'"):
            run_pages([[{"name": "sem id"}]])

    def test_max_pages_guard(self) -> None:
        pages = [page([make_client(i)], i, 50, 50) for i in range(50)]
        with pytest.raises(CollectionIntegrityError, match="limite"):
            run_pages(pages, max_pages=3)


USER_BASE = "https://api.example.test/v1/user"
TRADE_BASE = "https://api.example.test/v1/trade-rules"


def spring_page(content: list[dict[str, Any]], number: int, size: int, total_elements: int) -> dict[str, Any]:
    """Page object with every key observed in production on 2026-10-28."""
    total_pages = -(-total_elements // size)
    return {
        "content": content,
        "pageable": {"pageNumber": number, "pageSize": size, "offset": number * size},
        "totalPages": total_pages,
        "last": number >= total_pages - 1,
        "totalElements": total_elements,
        "first": number == 0,
        "numberOfElements": len(content),
        "sort": {"sorted": False, "unsorted": True, "empty": True},
        "size": size,
        "number": number,
        "empty": not content,
    }


class TestSeparateBaseUrls:
    def test_patrimony_uses_trade_rules_base_and_others_use_user_base(self, settings: Settings) -> None:
        custom = replace(settings, base_url=USER_BASE, patrimony_base_url=TRADE_BASE)

        def respond(url: str, params: dict[str, Any] | None) -> FakeResponse:
            if url.startswith(TRADE_BASE):
                return FakeResponse(200, {"totalCurrentValue": 0, "clients": spring_page([], 0, 20, 0)})
            return FakeResponse(200, [])

        api, session = client_for(custom, respond)
        api.fetch_clients()
        api.fetch_consultants()
        api.fetch_consolidated_pl()
        assert [url for url, _ in session.requests] == [
            f"{USER_BASE}/company-api/clients",
            f"{USER_BASE}/company-api/consultants",
            f"{TRADE_BASE}{DEFAULT_CONSOLIDATED_PL_PATH}",
        ]

    def test_patrimony_under_user_base_404_is_integration_error(self, settings: Settings) -> None:
        # Production answers 404 for the patrimony path under /v1/user.
        wrong = replace(settings, patrimony_base_url=USER_BASE)

        def respond(url: str, params: dict[str, Any] | None) -> FakeResponse:
            return FakeResponse(404 if url.startswith(USER_BASE) else 200, {})

        api, _ = client_for(wrong, respond)
        with pytest.raises(IntegrationError, match="HTTP 404"):
            api.fetch_consolidated_pl()


class TestObservedPatrimonyPagination:
    """Shape observed in production: 77 clients, size 20, 4 pages."""

    TOTAL = Decimal("123456789.01")

    def build_pages(self, size: int = 20, total: int = 77) -> list[dict[str, Any]]:
        entries = [make_pl_entry(5000 + i, Decimal("100.10")) for i in range(total)]
        return [
            {
                "totalCurrentValue": self.TOTAL,
                "clients": spring_page(entries[start : start + size], number, size, total),
            }
            for number, start in enumerate(range(0, total, size))
        ]

    def test_walks_four_pages_and_keeps_total_once(self, settings: Settings) -> None:
        pages = self.build_pages()
        assert [len(p["clients"]["content"]) for p in pages] == [20, 20, 20, 17]
        api, session = client_for(replace(settings, page_size=20), paged_responder(pages))

        result = api.fetch_consolidated_pl()

        assert len(result.clients) == 77
        assert [params["page"] for _, params in session.requests] == [0, 1, 2, 3]
        assert all(params["size"] == 20 for _, params in session.requests)
        # totalCurrentValue is repeated on every page and must never be summed.
        assert result.total_current_value == self.TOTAL

    def test_missing_record_on_last_page_is_detected(self, settings: Settings) -> None:
        pages = self.build_pages()
        pages[3]["clients"]["content"] = pages[3]["clients"]["content"][:-1]
        pages[3]["clients"]["numberOfElements"] = 16
        api, _ = client_for(replace(settings, page_size=20), paged_responder(pages))
        with pytest.raises(CollectionIntegrityError, match="totalElements"):
            api.fetch_consolidated_pl()

    def test_total_changing_between_pages_is_detected(self, settings: Settings) -> None:
        pages = self.build_pages()
        pages[2]["totalCurrentValue"] = self.TOTAL + 1
        api, _ = client_for(replace(settings, page_size=20), paged_responder(pages))
        with pytest.raises(CollectionIntegrityError, match="mudou"):
            api.fetch_consolidated_pl()


class FakeHeadersResponse(FakeResponse):
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(status, body)
        self.headers = headers or {}


class TestWalletDetails:
    def test_detail_uses_trade_rules_base_and_wallet_info_id(self, settings: Settings) -> None:
        custom = replace(settings, base_url=USER_BASE, patrimony_base_url=TRADE_BASE)
        api, session = client_for(custom, lambda *_: FakeResponse(200, {"id": 501, "walletCoin": "DOLLAR"}))
        assert api.fetch_wallet_detail(501)["walletCoin"] == "DOLLAR"
        assert session.requests[0][0] == f"{TRADE_BASE}/company-api/wallet/501"

    def test_detail_for_other_id_is_rejected(self, settings: Settings) -> None:
        api, _ = client_for(settings, lambda *_: FakeResponse(200, {"id": 1}))
        with pytest.raises(CollectionIntegrityError, match="detalhe"):
            api.fetch_wallet_detail(2)

    def test_client_side_rate_limit_between_requests(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("src.api_client.time.sleep", sleeps.append)

        def respond(url: str, params: Any) -> FakeResponse:
            return FakeResponse(200, {"id": int(url.rsplit("/", 1)[1])})

        api, session = client_for(replace(settings, wallet_detail_interval_seconds=0.5), respond)
        details = api.fetch_wallet_details([1, 2, 3, 2])
        assert sorted(details) == [1, 2, 3]
        assert len(session.requests) == 3  # duplicates fetched once
        assert sleeps == [0.5, 0.5]

    def test_http_429_honours_retry_after(self, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("src.api_client.time.sleep", sleeps.append)
        responses = iter(
            [FakeHeadersResponse(429, {}, {"Retry-After": "7"}), FakeHeadersResponse(200, {"id": 5})]
        )
        api, _ = client_for(replace(settings, max_retries=2), lambda *_: next(responses))
        assert api.fetch_wallet_detail(5) == {"id": 5}
        assert sleeps == [7.0]

    def test_http_429_retry_after_is_capped_and_retries_are_limited(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("src.api_client.time.sleep", sleeps.append)
        api, session = client_for(
            replace(settings, max_retries=2),
            lambda *_: FakeHeadersResponse(429, {}, {"Retry-After": "99999"}),
        )
        with pytest.raises(IntegrationError, match="HTTP 429"):
            api.fetch_wallet_detail(5)
        assert len(session.requests) == 3
        assert sleeps == [60.0, 60.0]

    def test_http_429_without_retry_after_uses_backoff(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("src.api_client.time.sleep", sleeps.append)
        responses = iter(
            [FakeHeadersResponse(429), FakeHeadersResponse(429), FakeHeadersResponse(200, {"id": 5})]
        )
        api, _ = client_for(replace(settings, max_retries=3), lambda *_: next(responses))
        api.fetch_wallet_detail(5)
        assert sleeps == [1.0, 2.0]
