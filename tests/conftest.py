"""Shared fixtures and fictional data builders.

Every name, address and value here is invented for testing purposes.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.api_client import ConsolidatedPL
from src.config import Settings, WalletPolicy


def _check_digit(values: list[int], weights: list[int]) -> int:
    remainder = sum(v * w for v, w in zip(values, weights, strict=True)) % 11
    return 0 if remainder < 2 else 11 - remainder


def make_cpf(seed: int) -> str:
    """Return a fictional, checksum-valid CPF derived from ``seed``."""
    digits = [int(c) for c in f"{100_000_000 + seed:09d}"[-9:]]
    digits.append(_check_digit(digits, list(range(10, 1, -1))))
    digits.append(_check_digit(digits, list(range(11, 1, -1))))
    return "".join(map(str, digits))


def make_cnpj(seed: int) -> str:
    """Return a fictional, checksum-valid numeric CNPJ derived from ``seed``."""
    weights = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    digits = [int(c) for c in f"{10_000_000 + seed:08d}0001"]
    digits.append(_check_digit(digits, weights))
    digits.append(_check_digit(digits, [6, *weights]))
    return "".join(map(str, digits))


def make_client(client_id: int, **overrides: Any) -> dict[str, Any]:
    """Return a complete fictional Pessoa Física client record."""
    record: dict[str, Any] = {
        "id": client_id,
        "name": f"Cliente Ficticio {client_id}",
        "cpfCnpj": make_cpf(client_id),
        "birthdate": "1980-06-15",
        "consultantId": 1,
        "consultantName": "Consultora Exemplo",
        "street": "Rua das Flores",
        "number": "100",
        "complement": None,
        "district": "Centro",
        "city": "Cidade Exemplo",
        "state": "SP",
        "country": "Brasil",
        "postalCode": "01001000",
        "currentValue": 1000,
        "active": True,
        "deceased": False,
        "visualizationOnly": False,
    }
    record.update(overrides)
    return record


def make_wallets(*values: Any) -> list[dict[str, Any]]:
    """Return fictional wallets with sequential ids."""
    return [
        {
            "walletInfoId": 1000 + index,
            "name": f"Carteira {index}",
            "investedValue": value,
            "currentValue": value,
            "profit": 0,
        }
        for index, value in enumerate(values)
    ]


def make_pl_entry(client_id: int, *values: Any) -> dict[str, Any]:
    """Return a ``clients.content`` entry of the patrimony endpoint."""
    return {
        "clientId": client_id,
        "clientName": f"Cliente Ficticio {client_id}",
        "wallets": make_wallets(*values),
    }


CONSULTANTS = [
    {"id": 1, "name": "Consultora Exemplo", "email": "c@example.com", "permission": "X", "code": "C1"}
]


class FakeApi:
    """In-memory stand-in for :class:`CarteiraAdvisorClient`."""

    def __init__(
        self,
        clients: list[dict[str, Any]],
        pl_entries: list[dict[str, Any]],
        total: Decimal | int | None,
        consultants: list[dict[str, Any]] | None = None,
        fail_on: str | None = None,
        error: Exception | None = None,
        wallet_details: dict[Any, dict[str, Any]] | None = None,
    ) -> None:
        self.clients = clients
        self.pl_entries = pl_entries
        self.total = Decimal(total) if total is not None else None
        self.consultants = CONSULTANTS if consultants is None else consultants
        self.fail_on = fail_on
        self.error = error
        self.wallet_details = wallet_details or {}
        self.calls: list[str] = []

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_on == name and self.error is not None:
            raise self.error

    def fetch_clients(self) -> list[dict[str, Any]]:
        self._maybe_fail("clients")
        return self.clients

    def fetch_consultants(self) -> list[dict[str, Any]]:
        self._maybe_fail("consultants")
        return self.consultants

    def fetch_consolidated_pl(self) -> ConsolidatedPL:
        self._maybe_fail("pl")
        return ConsolidatedPL(self.total, self.pl_entries)

    def fetch_wallet_details(self, wallet_ids: list[Any]) -> dict[Any, dict[str, Any]]:
        """Default: BRL wallets consolidated on the test run date, no errors."""
        self._maybe_fail("details")
        return {wid: self.wallet_details.get(wid, make_wallet_detail(wid)) for wid in wallet_ids}


def make_wallet_detail(wallet_id: Any, **overrides: Any) -> dict[str, Any]:
    """Return a fictional ``/company-api/wallet/{id}`` response."""
    detail: dict[str, Any] = {
        "id": wallet_id,
        "userId": 1,
        "description": f"Carteira {wallet_id}",
        "principal": True,
        "lastConsolidation": "2026-10-28T04:00:00.000000",
        "hasConsolidationErrors": False,
        "walletCoin": "REAIS",
    }
    detail.update(overrides)
    return detail


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with a fake token and isolated output/log directories."""
    return Settings(
        base_url="https://api.example.test/v1/user",
        token="fake-token",
        page_size=2,
        max_retries=0,
        wallet_detail_interval_seconds=0,
        # Unit tests never reach the external PTAX service; FX tests opt in with a fake.
        fx_check_enabled=False,
        # Existing scenarios exercise the full audit; fast-mode tests opt out explicitly.
        detailed_audit=True,
        max_requests_per_minute=0,
        output_dir=tmp_path / "output",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
def exclusive_settings(settings: Settings) -> Settings:
    """Settings with the mutually exclusive wallet policy."""
    return replace(settings, wallet_policy=WalletPolicy.MUTUALLY_EXCLUSIVE)
