"""End-to-end orchestration of the birthday report.

``main.py`` only sets up logging and translates the outcome into an exit
code; all business sequencing lives here so it can be tested with mocks.

The operational spreadsheet (Pessoa Física clients, provisional patrimony
from ``clients.currentValue``) is published independently of the financial
audit, which runs as a separate diagnostic and never blocks publication.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.api_client import CarteiraAdvisorClient
from src.cadastral import (
    ActivityStatus,
    CadastralAudit,
    Classification,
    audit_cadastral,
    classify_clients,
    classify_status,
)
from src.config import Settings
from src.currency import (
    CurrencyBreakdown,
    apply_currency_gate,
    build_breakdown,
    currency_gate_reasons,
    parse_wallet_detail,
)
from src.diagnostic import write_diagnostic_report
from src.duplicates import DuplicateEvidence, find_cross_client_duplicates
from src.errors import ConfigurationError, ExitCode, IntegrationError, TransformationError
from src.estimate import (
    FxEstimate,
    ZeroPatrimony,
    classify_zero_patrimony,
    estimate_patrimony,
    ptax_window,
)
from src.estimate import dollar_consolidation_moments as _dollar_moments
from src.exporter import (
    create_temp_path,
    discard,
    publish,
    report_filename,
    write_workbook,
)
from src.ptax import PtaxError, fetch_usd_brl_period
from src.reconciliation import (
    AuditStatus,
    ClientPatrimony,
    PatrimonyStatus,
    ReconciliationResult,
    compute_client_patrimony,
    raw_wallets_total,
    reconcile,
    to_decimal,
)
from src.stability import StabilityResult, compare_snapshots
from src.summary import PERSON_LABELS, name_and_id
from src.transformer import PersonType, build_rows, classify_document, classify_person, id_key, sort_rows
from src.validator import validate_excel, validate_row_quantities

logger = logging.getLogger(__name__)

ClientFactory = Callable[[Settings], CarteiraAdvisorClient]


@dataclass(frozen=True)
class RunResult:
    """Outcome of one execution.

    Attributes:
        exit_code: Process exit code.
        clients_found: Clients returned by the API.
        individuals: Clients classified as Pessoa Física (valid CPF), any status.
        companies_excluded: Clients classified as Pessoa Jurídica (valid CNPJ).
        unclassified_ids: Ids of clients whose document could not be classified.
        clients_exported: Rows written to the Excel file (active PF only).
        valid_birthdays: Rows with a valid birth date.
        pending_patrimony: Rows without a usable ``currentValue``.
        provisional_patrimony_total: Sum of ``currentValue`` of exported rows
            (diagnostic only, not audited).
        reconciliation: Financial audit result (diagnostic, non-blocking).
        output_path: Published spreadsheet.
        patrimony_clients: Clients present in the patrimony endpoint.
        wallet_count: Wallets received from the patrimony endpoint.
        missing_patrimony_ids: Client ids absent from the patrimony endpoint.
        raw_wallets_total: Plain sum of all wallets (diagnostic, not audited).
        audit_error: Why the patrimony endpoint could not be used, if so.
        currency: Wallet values per original currency (``None`` if the wallet
            details were not collected).
        fx: PTAX-based patrimony coherence check (``None`` if not run).
        fx_error: Why the PTAX check could not run, if so.
        diagnostic_path: Local per-client diagnostic workbook, if written.
        stability: Comparison of consolidated-pl read at the start and at the
            end of the financial collection (``None`` if not possible).
        stability_error: Why the stability check could not run, if so.
        duplicates: Possible patrimony duplication between clients.
        never_consolidated_wallets: Wallets without ``lastConsolidation``.
        zero_patrimony: Active PF clients with ``currentValue == 0``.
        client_names: Client id key -> full name, for the console summary
            only (never logged).
        detailed_audit: Whether the detailed financial audit ran.
        api_requests: HTTP requests sent to Carteira Advisor in this run
            (``None`` when the client does not count them).
        classification: Document type x activity status contingency table.
        cadastral: Cadastral audit (ids of active clients vs consolidated-pl).
    """

    exit_code: ExitCode
    clients_found: int
    clients_exported: int
    valid_birthdays: int
    reconciliation: ReconciliationResult
    output_path: Path | None
    individuals: int = 0
    companies_excluded: int = 0
    unclassified_ids: tuple[Any, ...] = ()
    special_ids: tuple[Any, ...] = ()
    pending_patrimony: int = 0
    provisional_patrimony_total: Decimal = Decimal(0)
    patrimony_clients: int = 0
    wallet_count: int = 0
    missing_patrimony_ids: tuple[Any, ...] = ()
    raw_wallets_total: Decimal | None = None
    audit_error: str | None = None
    currency: CurrencyBreakdown | None = None
    classification: Classification | None = None
    cadastral: CadastralAudit | None = None
    fx: FxEstimate | None = None
    fx_error: str | None = None
    diagnostic_path: Path | None = None
    stability: StabilityResult | None = None
    stability_error: str | None = None
    duplicates: list[DuplicateEvidence] = field(default_factory=list)
    never_consolidated_wallets: int | None = None
    zero_patrimony: list[ZeroPatrimony] = field(default_factory=list)
    client_names: dict[str, str] = field(default_factory=dict)
    detailed_audit: bool = True
    api_requests: int | None = None


def split_by_person_type(
    clients: list[dict[str, Any]], special_ids: frozenset[str] = frozenset()
) -> dict[PersonType, list[dict[str, Any]]]:
    """Group clients by :class:`PersonType`, preserving order.

    Raises:
        TransformationError: If the groups do not add up to the input.
    """
    groups: dict[PersonType, list[dict[str, Any]]] = {person_type: [] for person_type in PersonType}
    for client in clients:
        groups[classify_person(client, special_ids)].append(client)
    if sum(len(group) for group in groups.values()) != len(clients):
        raise TransformationError("Classificação PF/PJ não preservou todos os clientes.")
    return groups


def _log_special_records(clients: list[dict[str, Any]], special_ids: frozenset[str]) -> None:
    """Warn about configuration that looks stale (ids only, no personal data)."""
    present = {id_key(client.get("id")) for client in clients}
    unknown = sorted(special_ids - present)
    if unknown:
        logger.warning("IDs especiais configurados que não existem no cadastro: %s", unknown)
    for client in clients:
        if id_key(client.get("id")) in special_ids and classify_document(client.get("cpfCnpj")) in (
            PersonType.INDIVIDUAL,
            PersonType.COMPANY,
        ):
            logger.warning(
                "Cliente id=%s configurado como especial possui documento válido; revise a configuração.",
                client.get("id"),
            )


def compute_patrimonies(
    clients: list[dict[str, Any]],
    patrimony_entries: list[dict[str, Any]],
    settings: Settings,
) -> tuple[dict[str, ClientPatrimony], list[Any]]:
    """Compute the individual patrimony of every client.

    Clients absent from the patrimony endpoint are marked MISSING rather than
    assumed to be zero, since the API does not document that absence means
    "no wallets".

    Returns:
        Patrimony keyed by client id, and patrimony ``clientId`` values that
        have no matching client (orphans).
    """
    by_client = {id_key(entry.get("clientId")): entry for entry in patrimony_entries}
    client_ids = {id_key(client.get("id")) for client in clients}
    patrimonies: dict[str, ClientPatrimony] = {}
    for client in clients:
        key = id_key(client.get("id"))
        entry = by_client.get(key)
        if entry is None:
            patrimonies[key] = ClientPatrimony(
                client.get("id"), None, PatrimonyStatus.MISSING, 0, "ausente no endpoint de patrimônio"
            )
        else:
            patrimonies[key] = compute_client_patrimony(entry, settings.wallet_policy)
    orphans = [entry.get("clientId") for key, entry in by_client.items() if key not in client_ids]
    return patrimonies, orphans


def _log_patrimony_diagnostics(
    clients: list[dict[str, Any]], patrimonies: dict[str, ClientPatrimony]
) -> None:
    """Log safe diagnostics (ids and counts only, never names or values).

    Per-client lines go to INFO (log file only); aggregates stay at WARNING so
    the console shows the problem without burying the final summary.
    """
    counts = Counter(p.status.value for p in patrimonies.values())
    logger.info("Patrimônio individual por situação: %s", dict(counts))
    unresolved = len(patrimonies) - counts.get(PatrimonyStatus.VERIFIED.value, 0)
    if unresolved:
        logger.warning("%d cliente(s) sem patrimônio auditado; detalhes por id no log.", unresolved)
    for patrimony in patrimonies.values():
        if not patrimony.is_verified:
            logger.info(
                "Cliente id=%s: patrimônio %s (%s, %d carteira(s)).",
                patrimony.client_id,
                patrimony.status.value,
                patrimony.reason,
                patrimony.wallet_count,
            )
    # clients.currentValue is only a cross-check; it never replaces the value
    # computed from wallets.
    mismatches = 0
    for client in clients:
        registered = to_decimal(client.get("currentValue"))
        computed = patrimonies[id_key(client.get("id"))]
        if registered is not None and computed.is_verified and registered != computed.value:
            mismatches += 1
            logger.info("Cliente id=%s: currentValue do cadastro difere do apurado.", client.get("id"))
    if mismatches:
        logger.warning("%d cliente(s) com currentValue do cadastro diferente do apurado.", mismatches)


def _log_consultant_diagnostics(clients: list[dict[str, Any]], consultants: list[dict[str, Any]]) -> None:
    known = {id_key(c.get("id")) for c in consultants}
    for client in clients:
        consultant_id = id_key(client.get("consultantId"))
        if not consultant_id or consultant_id not in known:
            has_name = bool(id_key(client.get("consultantName")))
            logger.info(
                "Cliente id=%s: consultor não relacionado por ID (consultantId=%s, "
                "consultantName no cadastro: %s).",
                client.get("id"),
                consultant_id or "ausente",
                "presente" if has_name else "ausente",
            )


def _collect_currency(
    api: CarteiraAdvisorClient,
    pl_entries: list[dict[str, Any]],
    clients: list[dict[str, Any]],
    company_total: Decimal | None,
    settings: Settings,
    now: datetime,
) -> tuple[CurrencyBreakdown | None, str | None, dict[Any, Any]]:
    """Fetch wallet details and group values by currency (isolated, optional).

    Returns:
        The breakdown, why it is unavailable (if so) and the parsed details.
    """
    if not settings.wallet_details_enabled:
        return None, "consulta de detalhes desativada (CARTEIRA_ADVISOR_WALLET_DETAILS=false)", {}
    wallet_ids = [
        wallet.get("walletInfoId")
        for entry in pl_entries
        for wallet in entry.get("wallets") or []
        if isinstance(wallet, dict)
    ]
    logger.info("Coletando detalhes de %d carteira(s) (auditoria multimoeda).", len(wallet_ids))
    try:
        raw = api.fetch_wallet_details(wallet_ids)
    except IntegrationError as exc:
        logger.warning("Detalhes de carteiras indisponíveis: %s", exc)
        return None, str(exc), {}
    details = {wallet_id: parse_wallet_detail(body) for wallet_id, body in raw.items()}
    breakdown = build_breakdown(pl_entries, details, clients, company_total, now)
    logger.info("Carteiras por moeda: %s", breakdown.counts)
    if breakdown.error_wallet_ids:
        logger.warning("Carteiras com erro de consolidação (ids): %s", breakdown.error_wallet_ids)
    if breakdown.stale_wallet_ids:
        logger.info("Carteiras sem consolidação recente (ids): %s", breakdown.stale_wallet_ids)
    return breakdown, None, details


def _check_stability(
    api: CarteiraAdvisorClient, first: Any, started_at: datetime
) -> tuple[StabilityResult | None, str | None]:
    """Read consolidated-pl once more (no retries loop) and compare record by record."""
    finished_at = datetime.now()
    try:
        second = api.fetch_consolidated_pl()
    except IntegrationError as exc:
        logger.warning("Verificação de estabilidade não concluída: %s", exc)
        return None, str(exc)
    result = compare_snapshots(first, second, started_at, finished_at)
    if result.stable:
        logger.info("Consolidado estável durante a coleta.")
    else:
        logger.warning(
            "Consolidado alterado durante a coleta: %d cliente(s) afetado(s) (ids): %s",
            len(result.affected_clients),
            result.affected_clients,
        )
    return result, None


def _run_financial_audit(
    api: CarteiraAdvisorClient, clients: list[dict[str, Any]], settings: Settings, now: datetime
) -> tuple[ReconciliationResult, dict[str, Any]]:
    """Run the wallet-based reconciliation as an isolated diagnostic.

    Any integration failure on the patrimony endpoint is logged and turned
    into a NOT_VERIFIED result: the financial audit never blocks the
    operational birthday spreadsheet.

    Returns:
        The reconciliation result and extra diagnostic fields for RunResult.
    """
    logger.info("Coletando patrimônio consolidado (auditoria financeira, não bloqueante).")
    started_at = datetime.now()
    try:
        consolidated = api.fetch_consolidated_pl()
    except IntegrationError as exc:
        logger.warning("Auditoria financeira sem dados de patrimônio: %s", exc)
        result = ReconciliationResult(
            AuditStatus.NOT_VERIFIED, None, Decimal(0), None, len(clients), [], [str(exc)]
        )
        return result, {"audit_error": str(exc)}

    patrimonies, orphans = compute_patrimonies(clients, consolidated.clients, settings)
    _log_patrimony_diagnostics(clients, patrimonies)
    missing_ids = tuple(p.client_id for p in patrimonies.values() if p.status is PatrimonyStatus.MISSING)
    if missing_ids:
        logger.warning("Clientes ausentes do consolidado patrimonial (ids): %s", list(missing_ids))
    if orphans:
        logger.warning("clientId(s) de patrimônio sem cliente no cadastro: %s", orphans[:20])
    reconciliation = reconcile(consolidated.total_current_value, list(patrimonies.values()), orphans)
    breakdown, unavailable, details = _collect_currency(
        api, consolidated.clients, clients, consolidated.total_current_value, settings, now
    )
    reconciliation = apply_currency_gate(reconciliation, currency_gate_reasons(breakdown, unavailable))
    stability, stability_error = _check_stability(api, consolidated, started_at)
    duplicates = find_cross_client_duplicates(consolidated.clients, details)
    if duplicates:
        logger.warning(
            "Possível duplicidade patrimonial entre clientes (ids): %s",
            sorted({str(e.client_a) for e in duplicates} | {str(e.client_b) for e in duplicates}),
        )
    logger.info("Auditoria financeira: %s", reconciliation.status.value)
    for reason in reconciliation.reasons:
        logger.warning("Auditoria financeira: %s", reason)
    return reconciliation, {
        "patrimony_clients": len(consolidated.clients),
        "wallet_count": sum(
            len(entry["wallets"]) for entry in consolidated.clients if isinstance(entry.get("wallets"), list)
        ),
        "missing_patrimony_ids": missing_ids,
        "raw_wallets_total": raw_wallets_total(consolidated.clients),
        "patrimony_client_ids": [entry.get("clientId") for entry in consolidated.clients],
        "currency": breakdown,
        "stability": stability,
        "stability_error": stability_error,
        "duplicates": duplicates,
        "never_consolidated_wallets": (
            sum(1 for d in details.values() if d.last_consolidation is None) if details else None
        ),
        "_pl_entries": consolidated.clients,
        "_details": details,
        # Internal hand-over to the PTAX check; removed before building RunResult.
        "_fx_inputs": (consolidated.clients, details, consolidated.total_current_value) if details else None,
    }


def _run_fx_check(
    clients: list[dict[str, Any]],
    fx_inputs: tuple[list[dict[str, Any]], dict[Any, Any], Decimal | None] | None,
    settings: Settings,
) -> tuple[FxEstimate | None, str | None]:
    """Estimate patrimony per client with PTAX (independent reference)."""
    if not settings.fx_check_enabled:
        return None, "verificação PTAX desativada (CARTEIRA_ADVISOR_FX_CHECK=false)"
    if fx_inputs is None:
        return None, "detalhes das carteiras indisponíveis (moedas desconhecidas)"
    pl_entries, details, company_total = fx_inputs
    start, end = ptax_window(_dollar_moments(pl_entries, details), settings.ptax_window_days, date.today())
    try:
        series = fetch_usd_brl_period(
            settings.ptax_base_url, start, end, settings.timeout_seconds, settings.max_retries
        )
    except PtaxError as exc:
        logger.warning("Cotação PTAX indisponível: %s", exc)
        return None, str(exc)
    result = estimate_patrimony(
        clients,
        pl_entries,
        details,
        company_total,
        series,
        settings.fx_relevant_pct,
        settings.patrimony_thresholds,
        settings.ptax_window_days,
    )
    # Counts and ids only: no names, documents or individual values in logs.
    logger.info(
        "Verificação PTAX: %d estimado(s), %d sem dados, faixas %s.",
        len(result.estimated),
        len(result.insufficient),
        result.band_counts,
    )
    if result.insufficient:
        logger.info(
            "Clientes sem dados suficientes para estimativa (ids): %s",
            [c.client_id for c in result.insufficient],
        )
    if result.relevant:
        logger.info("Clientes com divergência relevante (ids): %s", [c.client_id for c in result.relevant])
    return result, None


def _diagnostic_annotations(
    pl_entries: list[dict[str, Any]] | None,
    details: dict[Any, Any],
    stability: StabilityResult | None,
    duplicates: list[DuplicateEvidence],
    names: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Per-client consolidation, stability and duplication indicators."""
    changed = {id_key(i) for i in stability.affected_clients} if stability else set()
    annotations: dict[str, dict[str, Any]] = {}
    for entry in pl_entries or []:
        key = id_key(entry.get("clientId"))
        found = [details.get(w.get("walletInfoId")) for w in entry.get("wallets") or []]
        moments = [d.last_consolidation for d in found if d and d.last_consolidation]
        client_id = entry.get("clientId")
        notes = []
        for e in duplicates:
            if e.involves(client_id):
                other_client, other_wallet = e.other(client_id)
                notes.append(
                    f"{e.status}: carteira {e.own_wallet(client_id)} ≡ "
                    f"{name_and_id(names, other_client)} carteira {other_wallet} ({e.kind.value})"
                )
        annotations[key] = {
            "consolidation": max(moments) if moments else None,
            "never": sum(1 for d in found if d and d.last_consolidation is None),
            "changed": key in changed,
            "duplicate": " | ".join(notes),
        }
    return annotations


def _safe_fx_check(
    clients: list[dict[str, Any]],
    fx_inputs: Any,
    classification: Classification,
    settings: Settings,
    now: datetime,
    annotations: dict[str, dict[str, Any]] | None = None,
) -> tuple[FxEstimate | None, str | None, Path | None]:
    """PTAX check and diagnostic report; no failure here may affect the sheet."""
    try:
        result, error = _run_fx_check(clients, fx_inputs, settings)
    except Exception as exc:  # the spreadsheet is already published
        logger.exception("Falha interna na verificação PTAX.")
        return None, f"falha interna na verificação PTAX ({type(exc).__name__})", None
    if result is None or not settings.diagnostic_report_enabled:
        return result, error, None
    labels = {}
    for client in clients:
        cell = classification.classification_of(client.get("id"))
        if cell:
            labels[id_key(client.get("id"))] = (PERSON_LABELS[cell[0]], cell[1].value)
    try:
        path = write_diagnostic_report(result, clients, labels, settings.output_dir, now, annotations)
    except Exception:  # diagnostic only
        logger.exception("Falha ao gravar o relatório diagnóstico.")
        return result, error, None
    logger.info("Relatório diagnóstico local: %s", path.name)
    return result, error, path


def _fast_patrimony_check(
    api: CarteiraAdvisorClient, clients: list[dict[str, Any]]
) -> tuple[ReconciliationResult, dict[str, Any]]:
    """Single consolidated-pl read for the cadastral id match (fast mode).

    No wallet details, second read, PTAX or diagnostic: the detailed financial
    audit is only run with ``CARTEIRA_ADVISOR_DETAILED_AUDIT=true``.
    """
    reason = "Auditoria financeira detalhada não executada (CARTEIRA_ADVISOR_DETAILED_AUDIT=false)."
    try:
        consolidated = api.fetch_consolidated_pl()
    except IntegrationError as exc:
        logger.warning("Consolidado indisponível no modo rápido: %s", exc)
        result = ReconciliationResult(AuditStatus.NOT_VERIFIED, None, Decimal(0), None, 0, [], [reason])
        return result, {"audit_error": str(exc)}
    result = ReconciliationResult(
        AuditStatus.NOT_VERIFIED, consolidated.total_current_value, Decimal(0), None, 0, [], [reason]
    )
    return result, {
        "patrimony_clients": len(consolidated.clients),
        "wallet_count": sum(
            len(entry["wallets"]) for entry in consolidated.clients if isinstance(entry.get("wallets"), list)
        ),
        "patrimony_client_ids": [entry.get("clientId") for entry in consolidated.clients],
        "_pl_entries": consolidated.clients,
    }


def _safe_financial_audit(
    api: CarteiraAdvisorClient, clients: list[dict[str, Any]], settings: Settings, now: datetime
) -> tuple[ReconciliationResult, dict[str, Any]]:
    """Run the audit so that no failure in it can affect the published sheet."""
    try:
        return _run_financial_audit(api, clients, settings, now)
    except Exception as exc:  # the spreadsheet is already published; never fail the run here
        logger.exception("Falha interna na auditoria financeira.")
        reason = f"falha interna na auditoria financeira ({type(exc).__name__})"
        result = ReconciliationResult(
            AuditStatus.NOT_VERIFIED, None, Decimal(0), None, len(clients), [], [reason]
        )
        return result, {"audit_error": reason}


def run(
    settings: Settings,
    now: datetime | None = None,
    client_factory: ClientFactory = CarteiraAdvisorClient,
) -> RunResult:
    """Execute the full report flow.

    Args:
        settings: Runtime settings.
        now: Execution timestamp (injected by tests).
        client_factory: Builds the API client (injected by tests).

    Returns:
        The run result. The spreadsheet is published whatever the outcome of
        the financial audit.

    Raises:
        ConfigurationError: If the token is missing.
        IntegrationError: If client or consultant collection fails or is
            incomplete.
        TransformationError: If rows cannot be built or counted correctly.
        ExportError: If the Excel cannot be written, validated or published.
    """
    now = now or datetime.now()
    if not settings.has_token:
        raise ConfigurationError(
            "CARTEIRA_ADVISOR_TOKEN não configurado no .env. Nenhuma requisição foi realizada."
        )

    api = client_factory(settings)
    logger.info("Coletando clientes.")
    clients = api.fetch_clients()
    logger.info("Coletando consultores.")
    consultants = api.fetch_consultants()
    logger.info(
        "Coleta cadastral concluída: %d cliente(s), %d consultor(es).", len(clients), len(consultants)
    )

    special_config = settings.special_client_ids
    _log_special_records(clients, special_config)
    groups = split_by_person_type(clients, special_config)
    individuals = groups[PersonType.INDIVIDUAL]
    companies = groups[PersonType.COMPANY]
    unclassified = groups[PersonType.UNCLASSIFIED]
    special_ids = tuple(client.get("id") for client in groups[PersonType.SPECIAL])
    unclassified_ids = tuple(client.get("id") for client in unclassified)
    logger.info(
        "Classificação: %d PF, %d PJ excluída(s), %d especial(is), %d documento(s) pendente(s).",
        len(individuals),
        len(companies),
        len(special_ids),
        len(unclassified),
    )
    if unclassified_ids:
        logger.warning("Clientes com documento sem classificação segura (ids): %s", list(unclassified_ids))

    classification = classify_clients(clients, special_config)
    status_pending = classification.ids(status=ActivityStatus.UNKNOWN)
    if status_pending:
        logger.warning("Clientes com status ativo/inativo pendente (ids): %s", status_pending)
    # Only active Pessoa Física clients go to the operational spreadsheet.
    active_individuals = [c for c in individuals if classify_status(c.get("active")) is ActivityStatus.ACTIVE]
    expected_rows = classification.count(PersonType.INDIVIDUAL, ActivityStatus.ACTIVE)
    if len(active_individuals) != expected_rows:
        raise TransformationError("Filtro de PF ativas diverge da tabela de classificação.")

    _log_consultant_diagnostics(active_individuals, consultants)
    rows = sort_rows(build_rows(active_individuals, consultants, now.date()))
    validate_row_quantities(active_individuals, rows)

    final_path = settings.output_dir / report_filename(now)
    temp_path = create_temp_path(settings.output_dir)
    try:
        write_workbook(rows, temp_path, settings.money_unit)
        validate_excel(temp_path, rows, settings.money_unit)
        publish(temp_path, final_path)
    finally:
        discard(temp_path)
    logger.info("Planilha publicada: %s (%d linha(s)).", final_path.name, len(rows))

    # Everything below runs only after publication, so it can neither delay nor
    # block the operational spreadsheet.
    if settings.detailed_audit:
        reconciliation, audit_details = _safe_financial_audit(api, clients, settings, now)
    else:
        reconciliation, audit_details = _fast_patrimony_check(api, clients)
    cadastral = audit_cadastral(clients, classification, audit_details.pop("patrimony_client_ids", None))
    names = {id_key(c.get("id")): str(c.get("name") or "").strip() for c in clients}
    pl_entries = audit_details.pop("_pl_entries", None)
    wallet_details = audit_details.pop("_details", None) or {}
    zero_patrimony = classify_zero_patrimony(
        active_individuals, pl_entries or [], wallet_details, evaluate=settings.detailed_audit
    )
    if zero_patrimony:
        logger.info(
            "PF ativas com PL zero: %d (ids): %s",
            len(zero_patrimony),
            [z.client_id for z in zero_patrimony],
        )
    if settings.detailed_audit:
        annotations = _diagnostic_annotations(
            pl_entries,
            wallet_details,
            audit_details.get("stability"),
            audit_details.get("duplicates") or [],
            names,
        )
        fx, fx_error, diagnostic_path = _safe_fx_check(
            clients, audit_details.pop("_fx_inputs", None), classification, settings, now, annotations
        )
    else:
        fx, fx_error, diagnostic_path = None, "auditoria detalhada desativada", None
    logger.info("Auditoria cadastral: %s", cadastral.status.value)
    for reason in cadastral.reasons:
        logger.warning("Auditoria cadastral: %s", reason)

    return RunResult(
        exit_code=ExitCode.SUCCESS,
        clients_found=len(clients),
        clients_exported=len(rows),
        valid_birthdays=sum(1 for row in rows if row.birthdate is not None),
        reconciliation=reconciliation,
        output_path=final_path,
        individuals=len(individuals),
        companies_excluded=len(companies),
        unclassified_ids=unclassified_ids,
        special_ids=special_ids,
        pending_patrimony=sum(1 for row in rows if row.patrimony is None),
        provisional_patrimony_total=sum(
            (row.patrimony for row in rows if row.patrimony is not None), Decimal(0)
        ),
        classification=classification,
        cadastral=cadastral,
        fx=fx,
        fx_error=fx_error,
        diagnostic_path=diagnostic_path,
        zero_patrimony=zero_patrimony,
        client_names=names,
        detailed_audit=settings.detailed_audit,
        api_requests=getattr(api, "request_count", None),
        **audit_details,
    )
