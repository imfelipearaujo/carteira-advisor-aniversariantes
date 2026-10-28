"""Estabilidade do consolidado durante a coleta."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.api_client import ConsolidatedPL
from src.stability import compare_snapshots

T0 = datetime(2026, 10, 28, 16, 53, 14)
T1 = datetime(2026, 10, 28, 16, 55, 47)


def snapshot(total: str, *clients: tuple[int, list[tuple[int, str, str]]]) -> ConsolidatedPL:
    return ConsolidatedPL(
        Decimal(total),
        [
            {
                "clientId": client_id,
                "wallets": [
                    {"walletInfoId": w, "currentValue": Decimal(cur), "investedValue": Decimal(inv)}
                    for w, cur, inv in wallets
                ],
            }
            for client_id, wallets in clients
        ],
    )


BASE = snapshot("1500", (1, [(10, "1000", "900")]), (2, [(20, "500", "400")]))


def test_identical_snapshots_are_stable() -> None:
    copy = snapshot("1500", (1, [(10, "1000", "900")]), (2, [(20, "500", "400")]))
    result = compare_snapshots(BASE, copy, T0, T1)
    assert result.stable and result.affected_clients == []
    assert (result.started_at, result.finished_at) == (T0, T1)


def test_value_change_with_same_total_is_not_stable() -> None:
    # Total igual não basta: 1000 → 1100 em um cliente e 500 → 400 no outro.
    after = snapshot("1500", (1, [(10, "1100", "900")]), (2, [(20, "400", "400")]))
    result = compare_snapshots(BASE, after, T0, T1)
    assert not result.total_changed
    assert not result.stable
    assert sorted(result.value_changes) == [(1, 10), (2, 20)]
    assert sorted(result.affected_clients) == [1, 2]


def test_invested_value_change_is_detected() -> None:
    after = snapshot("1500", (1, [(10, "1000", "950")]), (2, [(20, "500", "400")]))
    assert compare_snapshots(BASE, after, T0, T1).value_changes == [(1, 10)]


def test_wallets_added_and_removed() -> None:
    after = snapshot("1500", (1, [(11, "1000", "900")]), (2, [(20, "500", "400")]))
    result = compare_snapshots(BASE, after, T0, T1)
    assert result.wallets_added == [(1, 11)] and result.wallets_removed == [(1, 10)]
    assert result.affected_clients == [1]


def test_clients_added_and_removed() -> None:
    after = snapshot("1700", (1, [(10, "1000", "900")]), (3, [(30, "700", "700")]))
    result = compare_snapshots(BASE, after, T0, T1)
    assert result.clients_added == [3] and result.clients_removed == [2]
    assert result.total_changed and not result.stable
    assert sorted(result.affected_clients) == [2, 3]


def test_total_change_alone_is_not_stable() -> None:
    after = snapshot("1600", (1, [(10, "1000", "900")]), (2, [(20, "500", "400")]))
    result = compare_snapshots(BASE, after, T0, T1)
    assert not result.stable and result.affected_clients == []
