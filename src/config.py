"""Application configuration loaded from environment variables / ``.env``.

Only ``CARTEIRA_ADVISOR_BASE_URL`` and ``CARTEIRA_ADVISOR_TOKEN`` are required.
The remaining variables exist because the corresponding API behaviour has not
been confirmed yet (see docs/DECISIONS.md); they let the operator adjust the
integration once the Swagger is available, without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum, StrEnum
from pathlib import Path

from dotenv import load_dotenv

from src.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://api-prod.carteiradeinvestimentos.com/v1/user"
# The patrimony endpoint lives under a different service prefix (confirmed in
# production on 2026-10-28; the same path under /v1/user answers 404).
DEFAULT_PATRIMONY_BASE_URL = "https://api-prod.carteiradeinvestimentos.com/v1/trade-rules"
# Confirmed against production on 2026-10-28: the API reads the raw token from
# a header named "token" ("Authorization: Bearer" is answered with 401).
DEFAULT_PTAX_BASE_URL = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1"
DEFAULT_AUTH_HEADER = "token"
DEFAULT_AUTH_SCHEME = ""
DEFAULT_CONSOLIDATED_PL_PATH = "/company-api/wallet/consolidated-pl"


class WalletPolicy(StrEnum):
    """How a client's wallets are combined into an individual patrimony.

    UNCONFIRMED: the API has not confirmed that wallets are mutually exclusive.
        Only clients with zero or one wallet get an audited value; clients with
        two or more wallets are flagged as ambiguous.
    MUTUALLY_EXCLUSIVE: the API owner has confirmed that ``wallets`` never
        contains aggregating records. Wallets are summed, but a wallet whose
        value equals the sum of other wallets of the same client still blocks
        the value as a suspected overlap.
    """

    UNCONFIRMED = "unconfirmed"
    MUTUALLY_EXCLUSIVE = "mutually_exclusive"


class MoneyUnit(StrEnum):
    """Unit in which the API expresses monetary values."""

    REAIS = "reais"
    CENTAVOS = "centavos"


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings.

    Attributes:
        base_url: Base URL for clients and consultants, without trailing slash.
        patrimony_base_url: Base URL for the consolidated patrimony endpoint.
        token: API token. Never logged.
        auth_header: HTTP header that carries the token.
        auth_scheme: Prefix placed before the token (empty for raw token).
        consolidated_pl_path: Path of the patrimony endpoint, under
            ``patrimony_base_url``.
        page_param: Query parameter for the zero-based page index.
        size_param: Query parameter for the page size.
        page_size: Requested page size.
        timeout_seconds: Per-request timeout.
        max_retries: Retries for transient failures (429/5xx/network).
        max_pages: Safety cap against endless pagination.
        detailed_audit: Run the detailed financial audit (wallet details,
            second consolidated-pl read, PTAX, duplicates, diagnostic). When
            False only clients, consultants and one consolidated-pl read (for
            the cadastral id match) are requested.
        max_requests_per_minute: Client-side limit for Carteira Advisor
            requests (0 disables it; only meant for tests).
        wallet_details_enabled: Fetch ``/company-api/wallet/{id}`` for the
            currency audit (one request per wallet).
        wallet_detail_interval_seconds: Minimum pause between wallet detail
            requests (client-side rate limit).
        special_client_ids: Client ids explicitly classified as special
            records (e.g. family or simulation registrations without an
            individual CPF). Excluded from the spreadsheet, counted in audits.
        fx_check_enabled: Run the PTAX-based patrimony coherence check.
        ptax_base_url: PTAX OData service root (Banco Central do Brasil).
        ptax_window_days: Maximum age (days) of a PTAX bulletin relative to a
            wallet's consolidation; also how far back the window is fetched.
        fx_relevant_pct: Divergence (percent) from which a client is listed
            as relevant. Diagnostic only, never an approval criterion.
        patrimony_thresholds: Optional BRL thresholds; clients whose
            operational and estimated values fall on different sides are
            flagged.
        diagnostic_report_enabled: Write the local per-client diagnostic
            workbook under ``output/diagnostico``.
        wallet_policy: See :class:`WalletPolicy`.
        money_unit: See :class:`MoneyUnit`.
        output_dir: Directory for the definitive Excel file.
        log_dir: Directory for execution logs.
    """

    base_url: str
    token: str = field(repr=False)
    patrimony_base_url: str = DEFAULT_PATRIMONY_BASE_URL
    auth_header: str = DEFAULT_AUTH_HEADER
    auth_scheme: str = DEFAULT_AUTH_SCHEME
    consolidated_pl_path: str = DEFAULT_CONSOLIDATED_PL_PATH
    page_param: str = "page"
    size_param: str = "size"
    page_size: int = 100
    timeout_seconds: float = 30.0
    max_retries: int = 3
    max_pages: int = 10_000
    detailed_audit: bool = False
    max_requests_per_minute: int = 50
    wallet_details_enabled: bool = True
    special_client_ids: frozenset[str] = frozenset()
    fx_check_enabled: bool = True
    ptax_base_url: str = DEFAULT_PTAX_BASE_URL
    ptax_window_days: int = 10
    fx_relevant_pct: Decimal = Decimal(5)
    patrimony_thresholds: tuple[Decimal, ...] = ()
    diagnostic_report_enabled: bool = True
    wallet_detail_interval_seconds: float = 1.0
    wallet_policy: WalletPolicy = WalletPolicy.UNCONFIRMED
    money_unit: MoneyUnit = MoneyUnit.REAIS
    output_dir: Path = PROJECT_ROOT / "output"
    log_dir: Path = PROJECT_ROOT / "logs"

    @property
    def has_token(self) -> bool:
        """Whether a non-blank token was configured."""
        return bool(self.token.strip())


def _int_env(env: dict[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} deve ser um número inteiro.") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} deve ser maior ou igual a {minimum}.")
    return value


def _bool_env(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "sim", "yes"}:
        return True
    if raw in {"0", "false", "nao", "não", "no"}:
        return False
    raise ConfigurationError(f"{name} deve ser true ou false.")


def _ids_env(env: dict[str, str], name: str) -> frozenset[str]:
    """Parse a comma-separated list of numeric client ids."""
    raw = env.get(name, "").strip()
    if not raw:
        return frozenset()
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not all(part.isdigit() for part in ids):
        raise ConfigurationError(f"{name} deve ser uma lista de IDs numéricos separados por vírgula.")
    return frozenset(str(int(part)) for part in ids)


def _thresholds_env(env: dict[str, str], name: str) -> tuple[Decimal, ...]:
    """Parse comma-separated BRL thresholds (e.g. ``1000000,5000000``)."""
    raw = env.get(name, "").strip()
    if not raw:
        return ()
    try:
        values = sorted({Decimal(part.strip()) for part in raw.split(",") if part.strip()})
    except InvalidOperation as exc:
        raise ConfigurationError(f"{name} deve conter valores numéricos separados por vírgula.") from exc
    if any(not value.is_finite() or value <= 0 for value in values):
        raise ConfigurationError(f"{name} deve conter apenas valores positivos.")
    return tuple(values)


def _enum_env(env: dict[str, str], name: str, enum_cls: type[Enum], default: Enum) -> Enum:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ConfigurationError(f"{name} inválido. Valores aceitos: {allowed}.") from exc


def _url_env(env: dict[str, str], name: str, default: str) -> str:
    url = env.get(name, "").strip() or default
    if not url.startswith("https://"):
        raise ConfigurationError(f"{name} deve usar https://.")
    return url.rstrip("/")


def _path_env(env: dict[str, str], name: str) -> str:
    raw = env.get(name, "").strip()
    if not raw:
        return DEFAULT_CONSOLIDATED_PL_PATH
    if not raw.startswith("/") or "://" in raw:
        raise ConfigurationError(f"{name} deve ser um caminho relativo iniciado por '/'.")
    return raw.rstrip("/")


def load_settings(env: dict[str, str] | None = None, dotenv_path: Path | None = None) -> Settings:
    """Build :class:`Settings` from the environment.

    Args:
        env: Explicit environment mapping (used by tests). When ``None``, the
            ``.env`` file is loaded and ``os.environ`` is used.
        dotenv_path: Alternative ``.env`` location.

    Returns:
        The validated settings. A missing token is *not* an error here; the
        caller decides how to react via :attr:`Settings.has_token`.

    Raises:
        ConfigurationError: If any configured value is invalid.
    """
    if env is None:
        load_dotenv(dotenv_path or PROJECT_ROOT / ".env", override=False)
        env = dict(os.environ)

    return Settings(
        base_url=_url_env(env, "CARTEIRA_ADVISOR_BASE_URL", DEFAULT_BASE_URL),
        patrimony_base_url=_url_env(env, "CARTEIRA_ADVISOR_PATRIMONY_BASE_URL", DEFAULT_PATRIMONY_BASE_URL),
        token=env.get("CARTEIRA_ADVISOR_TOKEN", "").strip(),
        auth_header=env.get("CARTEIRA_ADVISOR_AUTH_HEADER", "").strip() or DEFAULT_AUTH_HEADER,
        auth_scheme=env.get("CARTEIRA_ADVISOR_AUTH_SCHEME", DEFAULT_AUTH_SCHEME).strip(),
        consolidated_pl_path=_path_env(env, "CARTEIRA_ADVISOR_CONSOLIDATED_PL_PATH"),
        page_param=env.get("CARTEIRA_ADVISOR_PAGE_PARAM", "").strip() or "page",
        size_param=env.get("CARTEIRA_ADVISOR_SIZE_PARAM", "").strip() or "size",
        page_size=_int_env(env, "CARTEIRA_ADVISOR_PAGE_SIZE", 100),
        timeout_seconds=float(_int_env(env, "CARTEIRA_ADVISOR_TIMEOUT_SECONDS", 30)),
        max_retries=_int_env(env, "CARTEIRA_ADVISOR_MAX_RETRIES", 3, minimum=0),
        detailed_audit=_bool_env(env, "CARTEIRA_ADVISOR_DETAILED_AUDIT", False),
        max_requests_per_minute=_int_env(env, "CARTEIRA_ADVISOR_MAX_REQUESTS_PER_MINUTE", 50, minimum=0),
        wallet_details_enabled=_bool_env(env, "CARTEIRA_ADVISOR_WALLET_DETAILS", True),
        special_client_ids=_ids_env(env, "CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS"),
        fx_check_enabled=_bool_env(env, "CARTEIRA_ADVISOR_FX_CHECK", True),
        ptax_base_url=_url_env(env, "CARTEIRA_ADVISOR_PTAX_BASE_URL", DEFAULT_PTAX_BASE_URL),
        ptax_window_days=_int_env(env, "CARTEIRA_ADVISOR_PTAX_WINDOW_DAYS", 10),
        fx_relevant_pct=Decimal(_int_env(env, "CARTEIRA_ADVISOR_FX_RELEVANT_PCT", 5)),
        patrimony_thresholds=_thresholds_env(env, "CARTEIRA_ADVISOR_PATRIMONY_THRESHOLDS"),
        diagnostic_report_enabled=_bool_env(env, "CARTEIRA_ADVISOR_DIAGNOSTIC_REPORT", True),
        wallet_detail_interval_seconds=_int_env(
            env, "CARTEIRA_ADVISOR_WALLET_DETAIL_INTERVAL_MS", 1000, minimum=0
        )
        / 1000,
        wallet_policy=_enum_env(
            env, "CARTEIRA_ADVISOR_WALLET_POLICY", WalletPolicy, WalletPolicy.UNCONFIRMED
        ),
        money_unit=_enum_env(env, "CARTEIRA_ADVISOR_MONEY_UNIT", MoneyUnit, MoneyUnit.REAIS),
    )
