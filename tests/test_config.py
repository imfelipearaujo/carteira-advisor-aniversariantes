"""Settings loading from environment mappings."""

import pytest

from src.config import (
    DEFAULT_BASE_URL,
    DEFAULT_PATRIMONY_BASE_URL,
    MoneyUnit,
    WalletPolicy,
    load_settings,
)
from src.errors import ConfigurationError


def test_defaults_without_token() -> None:
    settings = load_settings(env={})
    assert settings.base_url == DEFAULT_BASE_URL
    assert not settings.has_token
    assert settings.wallet_policy is WalletPolicy.UNCONFIRMED
    assert settings.money_unit is MoneyUnit.REAIS
    assert (settings.auth_header, settings.auth_scheme) == ("token", "")


def test_token_is_not_in_repr() -> None:
    settings = load_settings(env={"CARTEIRA_ADVISOR_TOKEN": "segredo-ficticio"})
    assert settings.has_token
    assert "segredo-ficticio" not in repr(settings)


def test_overrides() -> None:
    settings = load_settings(
        env={
            "CARTEIRA_ADVISOR_BASE_URL": "https://api.example.test/v1/",
            "CARTEIRA_ADVISOR_AUTH_SCHEME": "",
            "CARTEIRA_ADVISOR_PAGE_SIZE": "50",
            "CARTEIRA_ADVISOR_WALLET_POLICY": "MUTUALLY_EXCLUSIVE",
            "CARTEIRA_ADVISOR_MONEY_UNIT": "centavos",
        }
    )
    assert settings.base_url == "https://api.example.test/v1"
    assert settings.auth_scheme == ""
    assert settings.page_size == 50
    assert settings.wallet_policy is WalletPolicy.MUTUALLY_EXCLUSIVE
    assert settings.money_unit is MoneyUnit.CENTAVOS


@pytest.mark.parametrize(
    "env",
    [
        {"CARTEIRA_ADVISOR_BASE_URL": "http://inseguro.test"},
        {"CARTEIRA_ADVISOR_PAGE_SIZE": "abc"},
        {"CARTEIRA_ADVISOR_PAGE_SIZE": "0"},
        {"CARTEIRA_ADVISOR_WALLET_POLICY": "sum_everything"},
        {"CARTEIRA_ADVISOR_MONEY_UNIT": "dolares"},
        {"CARTEIRA_ADVISOR_CONSOLIDATED_PL_PATH": "https://outro.host/pl"},
    ],
)
def test_invalid_values(env: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(env=env)


def test_patrimony_base_url_default_and_override() -> None:
    assert load_settings(env={}).patrimony_base_url == DEFAULT_PATRIMONY_BASE_URL
    assert DEFAULT_PATRIMONY_BASE_URL.endswith("/v1/trade-rules")
    custom = load_settings(
        env={"CARTEIRA_ADVISOR_PATRIMONY_BASE_URL": "https://api.example.test/v1/trade-rules/"}
    )
    assert custom.patrimony_base_url == "https://api.example.test/v1/trade-rules"
    assert custom.base_url == DEFAULT_BASE_URL


def test_patrimony_base_url_requires_https() -> None:
    with pytest.raises(ConfigurationError, match="PATRIMONY_BASE_URL"):
        load_settings(env={"CARTEIRA_ADVISOR_PATRIMONY_BASE_URL": "http://inseguro.test"})


def test_wallet_detail_settings() -> None:
    default = load_settings(env={})
    assert default.wallet_details_enabled is True
    assert default.wallet_detail_interval_seconds == 1.0
    custom = load_settings(
        env={"CARTEIRA_ADVISOR_WALLET_DETAILS": "false", "CARTEIRA_ADVISOR_WALLET_DETAIL_INTERVAL_MS": "1200"}
    )
    assert custom.wallet_details_enabled is False
    assert custom.wallet_detail_interval_seconds == 1.2
    with pytest.raises(ConfigurationError):
        load_settings(env={"CARTEIRA_ADVISOR_WALLET_DETAILS": "talvez"})


def test_special_client_ids() -> None:
    assert load_settings(env={}).special_client_ids == frozenset()
    settings = load_settings(env={"CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS": " 1001, 1002 ,"})
    assert settings.special_client_ids == frozenset({"1001", "1002"})
    with pytest.raises(ConfigurationError, match="SPECIAL_CLIENT_IDS"):
        load_settings(env={"CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS": "1001,abc"})


def test_fx_settings() -> None:
    from decimal import Decimal

    default = load_settings(env={})
    assert default.fx_check_enabled is True
    assert default.ptax_base_url == "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1"
    assert (default.ptax_window_days, default.fx_relevant_pct, default.patrimony_thresholds) == (
        10,
        Decimal(5),
        (),
    )
    custom = load_settings(
        env={
            "CARTEIRA_ADVISOR_FX_CHECK": "false",
            "CARTEIRA_ADVISOR_PTAX_WINDOW_DAYS": "5",
            "CARTEIRA_ADVISOR_FX_RELEVANT_PCT": "10",
            "CARTEIRA_ADVISOR_PATRIMONY_THRESHOLDS": "5000000, 1000000",
            "CARTEIRA_ADVISOR_DIAGNOSTIC_REPORT": "false",
        }
    )
    assert custom.fx_check_enabled is False and custom.diagnostic_report_enabled is False
    assert custom.patrimony_thresholds == (Decimal(1_000_000), Decimal(5_000_000))
    for env in (
        {"CARTEIRA_ADVISOR_PATRIMONY_THRESHOLDS": "abc"},
        {"CARTEIRA_ADVISOR_PATRIMONY_THRESHOLDS": "-5"},
        {"CARTEIRA_ADVISOR_PTAX_BASE_URL": "http://inseguro.test"},
    ):
        with pytest.raises(ConfigurationError):
            load_settings(env=env)
