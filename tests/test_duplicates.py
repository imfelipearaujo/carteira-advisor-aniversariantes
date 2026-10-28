"""Possível duplicidade entre cadastro familiar e cadastros individuais."""

from __future__ import annotations

from decimal import Decimal

from src.currency import parse_wallet_detail
from src.duplicates import MANUAL_REVIEW, EvidenceKind, find_cross_client_duplicates
from tests.conftest import make_wallet_detail


def entry(client_id: int, *wallets: tuple[int, str, str]) -> dict:
    return {
        "clientId": client_id,
        "wallets": [
            {"walletInfoId": w, "currentValue": Decimal(cur), "investedValue": Decimal(inv)}
            for w, cur, inv in wallets
        ],
    }


def details(coins: dict[int, str]) -> dict:
    return {wid: parse_wallet_detail(make_wallet_detail(wid, walletCoin=coin)) for wid, coin in coins.items()}


# Cadastro familiar fictício (ID 100) com cópias das carteiras dos membros 200 e 300.
FAMILY = entry(100, (1, "1234.56", "1100.00"), (2, "2345.67", "2000.00"), (3, "9876.54", "9000.00"))
MEMBER_A = entry(200, (21, "1234.56", "1100.00"), (22, "0", "0"))
MEMBER_B = entry(300, (31, "2345.67", "2000.00"))
ALL_BRL = details({w: "REAIS" for w in (1, 2, 3, 21, 22, 31)})


def test_family_wallets_identical_to_members_are_flagged() -> None:
    found = find_cross_client_duplicates([FAMILY, MEMBER_A, MEMBER_B], ALL_BRL)
    pairs = sorted((e.wallet_a, e.client_b, e.wallet_b) for e in found)
    assert pairs == [(1, 200, 21), (2, 300, 31)]
    assert all(e.kind is EvidenceKind.IDENTICAL_VALUES for e in found)
    assert all(e.status == MANUAL_REVIEW for e in found)
    assert sum(e.current_value for e in found) == Decimal("3580.23")
    first = next(e for e in found if e.wallet_a == 1)
    assert first.involves(100) and first.other(100) == (200, 21) and first.own_wallet(100) == 1


def test_only_current_value_equal_is_not_evidence() -> None:
    member = entry(200, (21, "1234.56", "999.99"))
    assert find_cross_client_duplicates([FAMILY, member], ALL_BRL) == []


def test_different_currency_is_not_evidence() -> None:
    coins = details({1: "REAIS", 2: "REAIS", 3: "REAIS", 21: "DOLLAR", 22: "REAIS"})
    assert find_cross_client_duplicates([FAMILY, MEMBER_A], coins) == []


def test_zero_valued_wallets_are_not_evidence() -> None:
    other = entry(400, (41, "0", "0"))
    assert (
        find_cross_client_duplicates([MEMBER_A, other], details({21: "REAIS", 22: "REAIS", 41: "REAIS"}))
        == []
    )


def test_same_wallet_id_under_two_clients() -> None:
    other = entry(500, (21, "1", "1"))
    found = find_cross_client_duplicates([MEMBER_A, other], details({21: "REAIS", 22: "REAIS"}))
    assert [(e.kind, e.client_a, e.client_b) for e in found] == [(EvidenceKind.SAME_WALLET, 200, 500)]


def test_identical_wallets_of_the_same_client_are_ignored() -> None:
    same = entry(600, (61, "10", "9"), (62, "10", "9"))
    assert find_cross_client_duplicates([same], details({61: "REAIS", 62: "REAIS"})) == []


def test_names_are_not_used_as_evidence() -> None:
    # Sem coincidência numérica, nada é apontado, independentemente dos nomes.
    member = entry(200, (21, "123.45", "100.00"))
    assert find_cross_client_duplicates([FAMILY, member], ALL_BRL) == []
