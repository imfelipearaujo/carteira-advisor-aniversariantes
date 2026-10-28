"""Console summary of a run (presentation only).

Kept apart from :mod:`src.pipeline` so the business logic can be reused
without this terminal report. Clients are always shown as
``NOME COMPLETO (ID n)``; nothing here is written to the log files.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.cadastral import ActivityStatus
from src.config import MoneyUnit
from src.currency import BASE_COIN, CurrencyBreakdown
from src.estimate import FxEstimate, ZeroCategory, band_order
from src.exporter import to_reais
from src.reconciliation import AuditStatus
from src.transformer import PersonType, id_key

if TYPE_CHECKING:
    from src.pipeline import RunResult

_STATUS_COLUMNS = (ActivityStatus.ACTIVE, ActivityStatus.INACTIVE, ActivityStatus.UNKNOWN)
PERSON_LABELS = {
    PersonType.INDIVIDUAL: "PF",
    PersonType.COMPANY: "PJ",
    PersonType.SPECIAL: "Registro especial",
    PersonType.UNCLASSIFIED: "Documento pendente",
}


def name_and_id(names: dict[str, str], client_id: Any) -> str:
    """``NOME COMPLETO (ID n)``; ids never appear alone in user-facing text."""
    name = names.get(id_key(client_id))
    return f"{name} (ID {client_id})" if name else f"ID {client_id} (não cadastrado)"


def format_brl(value: Decimal | None, unit: MoneyUnit) -> str:
    """Format an API monetary value as ``R$ 1.234,56`` (``n/d`` if unknown)."""
    if value is None:
        return "n/d"
    reais = to_reais(value, unit)
    sign = "-" if reais < 0 else ""
    # Never round away precision silently: show extra digits if the API sent them.
    pattern = ",.2f" if reais == round(reais, 2) else ",f"
    text = format(abs(reais), pattern).replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{sign}R$ {text}"


def _format_int(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _format_amount(value: Decimal) -> str:
    pattern = ",.2f" if value == round(value, 2) else ",f"
    return format(value, pattern).replace(",", "_").replace(".", ",").replace("_", ".")


def _currency_lines(breakdown: CurrencyBreakdown | None, unit: MoneyUnit) -> list[str]:
    """Per-currency diagnostic lines; values are never added across currencies."""
    if breakdown is None:
        return []
    lines = ["  Carteiras por moeda (valores na moeda original, sem conversão):"]
    for coin in sorted(breakdown.counts):
        total = breakdown.totals.get(coin, Decimal(0))
        shown = format_brl(total, unit) if coin == BASE_COIN else f"{coin} {_format_amount(total)}"
        lines.append(f"    {coin:<10}{breakdown.counts[coin]:>5} carteira(s){shown:>30}")
    lines.append(
        f"  Erros de consolidação: {len(breakdown.error_wallet_ids)} | status não informado: "
        f"{len(breakdown.unknown_status_ids)} | sem consolidação recente: {len(breakdown.stale_wallet_ids)}"
    )
    for coin, rate in breakdown.implied_rates.items():
        shown_rate = _format_amount(rate.quantize(Decimal("1e-6")))
        lines.append(
            f"  Taxa implícita {coin}→{BASE_COIN} que fecharia o total: {shown_rate}"
            " (derivada do próprio total; NÃO é cotação oficial)"
        )
    if breakdown.client_rates:
        ratios = [rate for _, _, rate in breakdown.client_rates]
        lines.append(
            f"  clients.currentValue / carteira estrangeira ({len(ratios)} cliente(s)): "
            f"{_format_amount(min(ratios).quantize(Decimal('1e-4')))} a "
            f"{_format_amount(max(ratios).quantize(Decimal('1e-4')))} → cadastro já convertido para REAIS"
        )
    return lines


def _classification_lines(result: RunResult) -> list[str]:
    """Contingency table PF/PJ/special/pending x active/inactive/pending status."""
    table = result.classification
    if table is None:
        return []
    header = f"  {'Tipo':<20}{'Ativos':>8}{'Inativos':>10}{'Status pend.':>14}{'Total':>8}"
    lines = ["", "CLASSIFICAÇÃO CADASTRAL", header]
    for person, label in PERSON_LABELS.items():
        cells = [table.count(person, status) for status in _STATUS_COLUMNS]
        lines.append(f"  {label:<20}{cells[0]:>8}{cells[1]:>10}{cells[2]:>14}{table.count(person=person):>8}")
    totals = [table.count(status=status) for status in _STATUS_COLUMNS]
    lines.append(f"  {'Total':<20}{totals[0]:>8}{totals[1]:>10}{totals[2]:>14}{table.total:>8}")
    # classify_clients already raised if these did not hold; shown as evidence.
    lines.append(
        f"  Conferência: PF+PJ+especiais+pendentes = {sum(table.count(person=p) for p in PERSON_LABELS)}"
        f" | ativos+inativos+pendentes = {sum(totals)} | total = {table.total} | ids únicos: sim"
    )
    pending_status = table.ids(status=ActivityStatus.UNKNOWN)
    if pending_status:
        lines.append(f"  Status ativo/inativo pendente, fora da planilha: {_names(result, pending_status)}")
    return lines


def _names(result: RunResult, ids: Any) -> str:
    """Comma-separated ``NOME (ID n)`` list, or ``nenhum``."""
    ids = list(ids)
    return ", ".join(name_and_id(result.client_names, i) for i in ids) if ids else "nenhum"


def _cadastral_lines(result: RunResult) -> list[str]:
    """Id-level comparison of active clients (A) with consolidated-pl (B), by name."""
    audit = result.cadastral
    if audit is None:
        return []
    lines = ["", f"AUDITORIA CADASTRAL: {audit.status.value}"]
    if audit.patrimony_ids is not None:
        lines += [
            f"  Ativos (A): {len(audit.active_ids)} | Consolidado (B): {len(audit.patrimony_ids)}"
            f" | Em ambos (A∩B): {len(audit.in_both)}",
            f"  A − B (ativos fora do consolidado): {len(audit.active_missing_from_patrimony)}"
            f" → {_names(result, audit.active_missing_from_patrimony)}",
            f"  B − A (consolidado não ativo): {len(audit.patrimony_not_active)}"
            f" → {_names(result, audit.patrimony_not_active)}",
        ]
        outside = [
            f"{name_and_id(result.client_names, client_id)} "
            f"[{PERSON_LABELS.get(person, '?')}, {status.value if status else '?'}]"
            for client_id, person, status in audit.outside_patrimony
        ]
        lines.append(f"  Cadastrados fora do consolidado: {', '.join(outside) if outside else 'nenhum'}")
    table = audit.classification
    pending_docs = table.ids(person=PersonType.UNCLASSIFIED)
    if pending_docs:
        lines.append(f"  - Documento pendente: {_names(result, pending_docs)}")
    pending_status = table.ids(status=ActivityStatus.UNKNOWN)
    if pending_status:
        lines.append(f"  - Status ativo/inativo pendente: {_names(result, pending_status)}")
    if audit.patrimony_ids is None:
        lines.append("  - Consolidado patrimonial indisponível: conjuntos de ids não comparados.")
    elif len(set(map(str, audit.patrimony_ids))) != len(audit.patrimony_ids):
        lines.append("  - Consolidado patrimonial com clientId repetido.")
    return lines


def _format_pct(value: Decimal | None) -> str:
    if value is None:
        return "n/d"
    return f"{value.quantize(Decimal('0.01')):+}%".replace(".", ",")


def _fx_lines(result: RunResult, unit: MoneyUnit, project_root: Path | None) -> list[str]:
    """PTAX coherence check: aggregates only, no individual values."""
    lines = [
        "",
        "VERIFICAÇÃO INDEPENDENTE (PTAX/BCB — referência externa, NÃO é a cotação do Carteira Advisor)",
    ]
    fx = result.fx
    if fx is None:
        lines.append(f"  Não executada: {result.fx_error or 'motivo não informado'}")
        return lines
    quotes = sorted(fx.quotes_used.items(), key=lambda item: item[0].published_at)
    if fx.requested_moments:
        first, last = fx.requested_moments[0], fx.requested_moments[-1]
        lines.append(
            "  Data-base solicitada (consolidação das carteiras em dólar): "
            f"{first:%d/%m/%Y %H:%M} a {last:%d/%m/%Y %H:%M}"
        )
    for quote, count in quotes:
        lines.append(
            f"  PTAX venda {quote.published_at:%d/%m/%Y %H:%M}: {_format_amount(quote.sell_rate)}"
            f" (usada por {count} cliente(s))"
        )
    lines.append(f"  Fonte: {fx.source} | consulta: {fx.series.fetched_at:%d/%m/%Y %H:%M:%S}")
    lines += [
        f"  Clientes analisados: {len(fx.clients)} | estimados: {len(fx.estimated)}"
        f" | sem dados suficientes: {len(fx.insufficient)}",
        f"{'  Total estimado:':<28}{format_brl(fx.estimated_total.quantize(Decimal('0.01')), unit):>26}",
        f"{'  Total oficial (empresa):':<28}{format_brl(fx.company_total, unit):>26}",
    ]
    diff = fx.global_difference
    lines.append(
        f"{'  Diferença (estimado − oficial):':<28}"
        f"{format_brl(diff.quantize(Decimal('0.01')) if diff is not None else None, unit):>26}"
        f" ({_format_pct(fx.global_difference_pct)})"
    )
    if fx.insufficient:
        lines.append("  Atenção: total estimado parcial (há clientes sem dados suficientes).")
    lines.append(
        f"  Divergência relevante (|dif.| ≥ {fx.relevant_pct}% do operacional): {len(fx.relevant)} cliente(s)"
    )
    counts = fx.band_counts
    lines.append("  Distribuição (estimado × clients.currentValue):")
    for label in band_order():
        if counts.get(label):
            lines.append(f"    {label:<32}{counts[label]:>4} cliente(s)")
    if fx.thresholds:
        crossing = sum(1 for c in fx.estimated if c.crossed_thresholds)
        lines.append(f"  Clientes que mudam de lado em algum limite configurado: {crossing}")
    else:
        lines.append("  Limites patrimoniais: não configurados (CARTEIRA_ADVISOR_PATRIMONY_THRESHOLDS).")
    if result.diagnostic_path is not None:
        shown: Path = result.diagnostic_path
        if project_root is not None:
            with contextlib.suppress(ValueError):
                shown = result.diagnostic_path.relative_to(project_root)
        lines.append(f"  Relatório diagnóstico local (dados pessoais, fora do Git): {shown.as_posix()}")
    return lines


def _stability_lines(result: RunResult) -> list[str]:
    """Outcome of the start/end comparison of consolidated-pl."""
    lines = ["", "VERIFICAÇÃO TEMPORAL (consolidado no início × no fim da coleta financeira)"]
    stability = result.stability
    if stability is None:
        reason = result.stability_error or result.audit_error or "consolidado não coletado"
        lines.append(f"  NÃO VERIFICADA: {reason}")
        return lines
    lines.append(
        f"  Leituras: {stability.started_at:%d/%m/%Y %H:%M:%S} e {stability.finished_at:%d/%m/%Y %H:%M:%S}"
    )
    if stability.stable:
        lines.append("  ESTÁVEL: mesmos clientes, carteiras e valores nas duas leituras.")
        return lines
    lines.append(
        f"  DADOS ATUALIZADOS DURANTE A EXECUÇÃO: {len(stability.affected_clients)} cliente(s) afetado(s)"
        f" | carteiras alteradas: {len(stability.value_changes)}"
        f" | incluídas: {len(stability.wallets_added)} | removidas: {len(stability.wallets_removed)}"
        f" | total da empresa {'alterado' if stability.total_changed else 'inalterado'}"
    )
    lines.append(f"  Afetados: {_names(result, stability.affected_clients)}")
    lines.append(
        "  Os valores destes clientes podem não refletir um único instante; reexecute após as consolidações."
    )
    return lines


def _duplicate_lines(result: RunResult, unit: MoneyUnit) -> list[str]:
    """Possible duplication, with focus on configured special records."""
    lines = ["", "POSSÍVEL DUPLICIDADE PATRIMONIAL ENTRE CADASTROS"]
    if result.stability is None and result.audit_error:
        lines.append("  NÃO VERIFICADA: consolidado indisponível.")
        return lines
    focus = list(result.special_ids)
    shown: set[int] = set()
    company_total = result.reconciliation.company_total
    for special in focus:
        related = [e for e in result.duplicates if e.involves(special)]
        who = name_and_id(result.client_names, special)
        if not related:
            lines.append(f"  {who}: sem evidência pelos critérios (mesma carteira ou valores idênticos).")
            continue
        others = list(dict.fromkeys(e.other(special)[0] for e in related))
        brl = sum((e.current_value for e in related if e.coin in (BASE_COIN, None)), Decimal(0))
        share = (
            f" ({_format_pct(brl / company_total * 100).lstrip('+')} do total oficial)"
            if company_total
            else ""
        )
        lines.append(f"  {who}: {related[0].status}")
        lines.append(f"    {len(related)} carteira(s) com valores idênticos aos de: {_names(result, others)}")
        lines.append(
            f"    Valor potencialmente contado duas vezes: {format_brl(brl, unit)}{share}"
            " — NÃO subtraído do total; confirmar com o consultor."
        )
        shown.update(id(e) for e in related)
    rest = [e for e in result.duplicates if id(e) not in shown]
    for e in rest:
        lines.append(
            f"  {name_and_id(result.client_names, e.client_a)} × "
            f"{name_and_id(result.client_names, e.client_b)}: {e.status} ({e.kind.value})"
        )
    if not result.duplicates and not focus:
        lines.append("  Nenhuma evidência encontrada.")
    return lines


def _optional_int(value: int | None) -> str:
    return "n/d" if value is None else _format_int(value)


def _divergence_counts(fx: FxEstimate | None) -> tuple[int, int, int]:
    """Clients with |divergence| > 1%, > 5%, and operational zero with wallets ≠ 0."""
    if fx is None:
        return 0, 0, 0
    over1 = sum(1 for c in fx.estimated if c.difference_pct is not None and abs(c.difference_pct) > 1)
    over5 = sum(1 for c in fx.estimated if c.difference_pct is not None and abs(c.difference_pct) > 5)
    zero_gap = sum(1 for c in fx.estimated if c.difference_pct is None and c.difference)
    return over1, over5, zero_gap


def _indicator_lines(result: RunResult, unit: MoneyUnit) -> list[str]:
    """Headline indicators of the current run (all recomputed now)."""
    table = result.classification
    fx = result.fx
    over1, over5, zero_gap = _divergence_counts(fx)
    stability = result.stability
    if stability is None:
        temporal = "NÃO VERIFICADA"
    else:
        temporal = (
            "ESTÁVEL" if stability.stable else f"ALTERADA ({len(stability.affected_clients)} cliente(s))"
        )
    special_status = []
    for special in result.special_ids:
        related = [e for e in result.duplicates if e.involves(special)]
        status = related[0].status if related else "sem evidência"
        special_status.append(f"{name_and_id(result.client_names, special)}: {status}")

    def count(person: PersonType | None, status: ActivityStatus | None = None) -> str:
        return _format_int(table.count(person, status)) if table else "n/d"

    lines = [
        "",
        "INDICADORES DESTA EXECUÇÃO",
        f"  Clientes cadastrados: {_format_int(result.clients_found)}",
        f"  PF ativas: {count(PersonType.INDIVIDUAL, ActivityStatus.ACTIVE)}"
        f" | PF inativas: {count(PersonType.INDIVIDUAL, ActivityStatus.INACTIVE)}"
        f" | PJ: {count(PersonType.COMPANY)} | registros especiais: {count(PersonType.SPECIAL)}",
        f"  Carteiras: {_format_int(result.wallet_count)}"
        f" | nunca consolidadas: {_optional_int(result.never_consolidated_wallets)}",
        f"  Total oficial: {format_brl(result.reconciliation.company_total, unit)}",
    ]
    if fx is not None:
        diff = fx.global_difference
        lines += [
            f"  Total estimado (PTAX): {format_brl(fx.estimated_total.quantize(Decimal('0.01')), unit)}",
            f"  Diferença: {format_brl(diff.quantize(Decimal('0.01')) if diff is not None else None, unit)}"
            f" ({_format_pct(fx.global_difference_pct)})",
        ]
    else:
        lines.append(f"  Total estimado (PTAX): não calculado ({result.fx_error or 'motivo não informado'})")
    lines += [
        f"  PF ativas com PL zero: {len(result.zero_patrimony)}",
        f"  Clientes com divergência > 1%: {over1} | > 5%: {over5}"
        + (f" | operacional zero com carteiras ≠ 0: {zero_gap}" if zero_gap else ""),
        f"  Verificação temporal: {temporal}",
        "  Duplicidade em registros especiais: "
        + ("; ".join(special_status) if special_status else "nenhum configurado"),
    ]
    return lines


def _attention_lines(result: RunResult) -> list[str]:
    """Clients that need human review, always by full name and id."""
    changed = {id_key(i) for i in result.stability.affected_clients} if result.stability else set()

    def tag(client_id: Any) -> str:
        return " [alterado durante a coleta]" if id_key(client_id) in changed else ""

    def item(client_id: Any, text: str) -> str:
        return f"    - {name_and_id(result.client_names, client_id)}: {text}{tag(client_id)}"

    sections: list[tuple[str, list[str]]] = []
    fx = result.fx
    if fx is not None:
        big = [c for c in fx.estimated if (c.difference_pct is not None and abs(c.difference_pct) > 5)]
        zero_gap = [c for c in fx.estimated if c.difference_pct is None and c.difference]
        mid = [c for c in fx.estimated if c.difference_pct is not None and 1 < abs(c.difference_pct) <= 5]
        order = sorted(big, key=lambda c: c.difference_pct)
        sections.append(
            (
                "Divergência > 5% (estimado PTAX × clients.currentValue)",
                [item(c.client_id, _format_pct(c.difference_pct)) for c in order]
                + [item(c.client_id, "operacional zero, carteiras com valor") for c in zero_gap],
            )
        )
        sections.append(
            (
                "Divergência entre 1% e 5%",
                [
                    item(c.client_id, _format_pct(c.difference_pct))
                    for c in sorted(mid, key=lambda c: c.difference_pct)
                ],
            )
        )
        sections.append(
            ("Sem dados suficientes para estimativa", [item(c.client_id, c.reason) for c in fx.insufficient])
        )
    zero_items = []
    for z in result.zero_patrimony:
        if z.category is ZeroCategory.NOT_EVALUATED:
            # Fast mode: wallet currency and consolidation were not requested.
            zero_items.append(
                item(z.client_id, f"{z.category.value} | {z.wallet_count} carteira(s); cadastro R$ 0,00")
            )
            continue
        coins = ", ".join(sorted({c or "SEM_MOEDA" for c in z.coins})) or "nenhuma"
        last = f"{z.last_consolidation:%d/%m/%Y %H:%M}" if z.last_consolidation else "nunca"
        errors = {True: "com erro", False: "sem erro", None: "status não informado"}[z.has_errors]
        values = (
            ", ".join(f"{coin} {_format_amount(v)}" for coin, v in sorted(z.wallets_total.items())) or "—"
        )
        zero_items.append(
            item(
                z.client_id,
                f"{z.category.value} | {z.wallet_count} carteira(s), moeda {coins},"
                f" última consolidação {last}, {errors}; cadastro R$ 0,00; carteiras {values}",
            )
        )
    sections.append(("PF ativas com PL zero (informativo; pode ser intencional)", zero_items))
    sections.append(
        (
            "Possível duplicidade patrimonial",
            [
                item(
                    e.client_a,
                    f"{e.status} — carteira {e.wallet_a} × "
                    f"{name_and_id(result.client_names, e.client_b)} carteira {e.wallet_b}",
                )
                for e in result.duplicates
            ],
        )
    )
    pending = [
        *result.unclassified_ids,
        *(result.classification.ids(status=ActivityStatus.UNKNOWN) if result.classification else []),
    ]
    sections.append(
        ("Documento ou status cadastral pendente", [item(i, "cadastro a corrigir") for i in pending])
    )

    lines = ["", "CLIENTES QUE PRECISAM DE CONFERÊNCIA"]
    for title, items in sections:
        if items:
            lines.append(f"  {title}: {len(items)}")
            lines.extend(items)
    if len(lines) == 2:
        lines.append("  Nenhum.")
    return lines


def render_summary(result: RunResult, unit: MoneyUnit, project_root: Path | None = None) -> str:
    """Build the console summary shown at the end of the run."""
    rec = result.reconciliation
    pending_births = result.clients_exported - result.valid_birthdays
    lines = [
        "CARTEIRA ADVISOR — ANIVERSARIANTES (PESSOA FÍSICA)",
        "",
        f"{'Clientes cadastrados:':<30}{_format_int(result.clients_found):>8}",
        *_classification_lines(result),
        f"{'PF ativas exportadas:':<30}{_format_int(result.clients_exported):>8}",
        "",
        f"{'Aniversários válidos:':<30}{_format_int(result.valid_birthdays):>8}",
        f"{'Nascimentos pendentes:':<30}{_format_int(pending_births):>8}",
        f"{'Patrimônio pendente:':<30}{_format_int(result.pending_patrimony):>8}",
    ]
    if result.special_ids:
        lines.append(
            f"Registros especiais configurados, fora da planilha: {_names(result, result.special_ids)}"
        )
    if result.unclassified_ids:
        pending_docs = _names(result, result.unclassified_ids)
        lines.append(f"Documentos sem classificação segura, fora da planilha: {pending_docs}")
    lines += _cadastral_lines(result)
    lines += [
        "",
        "PATRIMÔNIO PROVISÓRIO (clients.currentValue — NÃO auditado, NÃO conciliado)",
        f"{'Soma das PF exportadas:':<26}{format_brl(result.provisional_patrimony_total, unit):>30}",
    ]
    if not result.detailed_audit:
        lines += _fast_mode_lines(result)
        return _finish_summary(lines, result, project_root)
    lines += [
        "",
        "AUDITORIA FINANCEIRA (diagnóstico separado; não bloqueia a planilha)",
        f"AUDITORIA PATRIMONIAL: {rec.status.value}",
    ]
    # A per-client sum that may contain wallets in different currencies has no
    # financial meaning, so it is only shown when every wallet is in BRL.
    mixed_currencies = result.currency is None or bool(result.currency.foreign_coins)
    audited_total = "n/d (moedas)" if mixed_currencies else format_brl(rec.clients_total, unit)
    if result.audit_error:
        lines.append(f"- Patrimônio consolidado indisponível: {result.audit_error}")
    else:
        lines += [
            f"{'Clientes no consolidado:':<26}{_format_int(result.patrimony_clients):>10}",
            f"{'Carteiras encontradas:':<26}{_format_int(result.wallet_count):>10}",
            f"{'Total da empresa:':<20}{format_brl(rec.company_total, unit):>24}",
            f"{'Total auditado:':<20}{audited_total:>24}",
            f"{'Diferença:':<20}{format_brl(rec.difference, unit):>24}",
        ]
        if rec.status is not AuditStatus.APPROVED:
            if rec.difference is None:
                lines.append("Diferença não calculável com segurança; total auditado é parcial.")
            lines.extend(f"- {reason}" for reason in rec.reasons)
            if result.missing_patrimony_ids:
                lines.append(
                    f"- Clientes sem registro no consolidado: {_names(result, result.missing_patrimony_ids)}"
                )
            lines += _currency_lines(result.currency, unit)
            if (
                result.raw_wallets_total is not None
                and rec.company_total is not None
                and not mixed_currencies
            ):
                raw_diff = rec.company_total - result.raw_wallets_total
                lines += [
                    "  Diagnóstico (soma bruta de todas as carteiras, NÃO auditada):",
                    f"{'  Soma bruta:':<20}{format_brl(result.raw_wallets_total, unit):>24}",
                    f"{'  Empresa − bruta:':<20}{format_brl(raw_diff, unit):>24}",
                ]
    lines += _stability_lines(result)
    lines += _fx_lines(result, unit, project_root)
    lines += _duplicate_lines(result, unit)
    lines += _indicator_lines(result, unit)
    lines += _attention_lines(result)
    return _finish_summary(lines, result, project_root)


def _fast_mode_lines(result: RunResult) -> list[str]:
    """Summary tail when the detailed financial audit is disabled."""
    lines = [
        "",
        "AUDITORIA FINANCEIRA DETALHADA: NÃO EXECUTADA (CARTEIRA_ADVISOR_DETAILED_AUDIT=false)",
        "  Para executá-la, defina CARTEIRA_ADVISOR_DETAILED_AUDIT=true no .env e rode python main.py.",
    ]
    if result.audit_error:
        lines.append(f"  Consolidado indisponível: {result.audit_error}")
    else:
        clients_read = _format_int(result.patrimony_clients)
        wallets_read = _format_int(result.wallet_count)
        lines.append(
            f"  Consolidado lido para a auditoria cadastral: {clients_read} cliente(s),"
            f" {wallets_read} carteira(s)."
        )
    lines += _attention_lines(result)
    return lines


def _finish_summary(lines: list[str], result: RunResult, project_root: Path | None) -> str:
    """Request count and output path (common to both modes)."""
    if result.api_requests is not None:
        lines += ["", f"Requisições ao Carteira Advisor nesta execução: {result.api_requests}"]
    lines.append("")
    if result.output_path is not None:
        shown: Path = result.output_path
        if project_root is not None:
            with contextlib.suppress(ValueError):
                shown = result.output_path.relative_to(project_root)
        lines.append("ARQUIVO GERADO (patrimônio provisório)")
        lines.append(shown.as_posix())
    return "\n".join(lines)
