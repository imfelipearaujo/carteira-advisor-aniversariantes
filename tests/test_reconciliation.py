"""Individual patrimony and company reconciliation rules."""

from decimal import Decimal

import pytest

from src.config import WalletPolicy
from src.reconciliation import (
    AuditStatus,
    ClientPatrimony,
    PatrimonyStatus,
    compute_client_patrimony,
    has_subset_overlap,
    raw_wallets_total,
    reconcile,
    to_decimal,
)
from tests.conftest import make_pl_entry

UNCONFIRMED = WalletPolicy.UNCONFIRMED
EXCLUSIVE = WalletPolicy.MUTUALLY_EXCLUSIVE


def verified(client_id: int, value: int | str) -> ClientPatrimony:
    return ClientPatrimony(client_id, Decimal(value), PatrimonyStatus.VERIFIED, 1)


class TestToDecimal:
    def test_accepts_json_numbers(self) -> None:
        assert to_decimal(10) == Decimal(10)
        assert to_decimal(Decimal("0.10")) == Decimal("0.10")

    @pytest.mark.parametrize("raw", [None, "1000", True, 1.5, Decimal("NaN")])
    def test_rejects_non_numbers(self, raw: object) -> None:
        assert to_decimal(raw) is None


class TestClientPatrimony:
    def test_single_wallet_is_verified(self) -> None:
        result = compute_client_patrimony(make_pl_entry(1, 112500), UNCONFIRMED)
        assert result.status is PatrimonyStatus.VERIFIED
        assert result.value == Decimal(112500)

    def test_zero_wallets_is_zero(self) -> None:
        result = compute_client_patrimony(make_pl_entry(1), UNCONFIRMED)
        assert result.is_verified and result.value == 0

    def test_single_wallet_with_zero_value(self) -> None:
        result = compute_client_patrimony(make_pl_entry(1, 0), UNCONFIRMED)
        assert result.is_verified and result.value == 0

    def test_multiple_wallets_unconfirmed_policy_is_ambiguous(self) -> None:
        result = compute_client_patrimony(make_pl_entry(1, 1000, 2500), UNCONFIRMED)
        assert result.status is PatrimonyStatus.AMBIGUOUS
        assert result.value is None

    def test_multiple_exclusive_wallets_are_summed(self) -> None:
        result = compute_client_patrimony(make_pl_entry(1, 1000, 2500, Decimal("0.35")), EXCLUSIVE)
        assert result.is_verified
        assert result.value == Decimal("3500.35")

    @pytest.mark.parametrize("policy", [UNCONFIRMED, EXCLUSIVE])
    def test_scenario_c_consolidated_overlap_is_never_6000(self, policy: WalletPolicy) -> None:
        # Bank A 1000 + Bank B 2000 + consolidated 3000: never accept 6000.
        result = compute_client_patrimony(make_pl_entry(1, 1000, 2000, 3000), policy)
        assert not result.is_verified
        assert result.value is None
        assert result.value != Decimal(6000)

    def test_scenario_c_flags_overlap_even_when_exclusivity_was_declared(self) -> None:
        result = compute_client_patrimony(make_pl_entry(1, 1000, 2000, 3000), EXCLUSIVE)
        assert result.status is PatrimonyStatus.OVERLAP_SUSPECTED

    def test_consolidated_name_alone_does_not_exclude_wallet(self) -> None:
        entry = make_pl_entry(1, 1000, 500)
        entry["wallets"][1]["name"] = "Carteira consolidada"
        result = compute_client_patrimony(entry, EXCLUSIVE)
        assert result.is_verified and result.value == Decimal(1500)

    def test_mirror_wallet_is_suspected_overlap(self) -> None:
        result = compute_client_patrimony(make_pl_entry(1, 1000, 1000), EXCLUSIVE)
        assert result.status is PatrimonyStatus.OVERLAP_SUSPECTED

    def test_repeated_wallet_id_is_suspected_overlap(self) -> None:
        entry = make_pl_entry(1, 1000, 2500)
        entry["wallets"][1]["walletInfoId"] = entry["wallets"][0]["walletInfoId"]
        result = compute_client_patrimony(entry, EXCLUSIVE)
        assert result.status is PatrimonyStatus.OVERLAP_SUSPECTED

    @pytest.mark.parametrize("bad_value", [None, "1000", True])
    def test_invalid_wallet_value_is_not_guessed(self, bad_value: object) -> None:
        result = compute_client_patrimony(make_pl_entry(1, bad_value), UNCONFIRMED)
        assert result.status is PatrimonyStatus.INVALID_DATA
        assert result.value is None

    def test_missing_wallet_list_is_invalid(self) -> None:
        result = compute_client_patrimony({"clientId": 1}, UNCONFIRMED)
        assert result.status is PatrimonyStatus.INVALID_DATA

    def test_too_many_wallets_is_ambiguous(self) -> None:
        values = [10**i for i in range(21)]
        result = compute_client_patrimony(make_pl_entry(1, *values), EXCLUSIVE)
        assert result.status is PatrimonyStatus.AMBIGUOUS


class TestSubsetOverlap:
    def test_detects_sum_of_two(self) -> None:
        assert has_subset_overlap([Decimal(1), Decimal(2), Decimal(3)])

    def test_ignores_zero_wallets(self) -> None:
        assert not has_subset_overlap([Decimal(0), Decimal(5), Decimal(7)])

    def test_independent_values(self) -> None:
        assert not has_subset_overlap([Decimal(1), Decimal(2), Decimal(4)])


class TestReconcile:
    def test_scenario_a_approved(self) -> None:
        result = reconcile(Decimal(2000), [verified(1, 1000), verified(2, 1000)])
        assert result.status is AuditStatus.APPROVED
        assert result.difference == 0

    def test_scenario_b_positive_divergence(self) -> None:
        result = reconcile(Decimal(5000), [verified(1, 1000), verified(2, 1000)])
        assert result.status is AuditStatus.DIVERGENCE
        assert result.difference == Decimal(3000)

    def test_negative_divergence(self) -> None:
        result = reconcile(Decimal(1500), [verified(1, 1000), verified(2, 1000)])
        assert result.status is AuditStatus.DIVERGENCE
        assert result.difference == Decimal(-500)

    def test_one_cent_is_a_divergence(self) -> None:
        result = reconcile(Decimal("2000.01"), [verified(1, 1000), verified(2, 1000)])
        assert result.status is AuditStatus.DIVERGENCE
        assert result.difference == Decimal("0.01")

    def test_decimal_precision_without_float_errors(self) -> None:
        result = reconcile(Decimal("0.3"), [verified(1, "0.1"), verified(2, "0.2")])
        assert result.status is AuditStatus.APPROVED

    def test_unresolved_client_blocks_approval(self) -> None:
        ambiguous = ClientPatrimony(2, None, PatrimonyStatus.AMBIGUOUS, 3)
        result = reconcile(Decimal(1000), [verified(1, 1000), ambiguous])
        assert result.status is AuditStatus.NOT_VERIFIED
        assert result.difference is None

    def test_missing_company_total_is_not_verified(self) -> None:
        result = reconcile(None, [verified(1, 1000)])
        assert result.status is AuditStatus.NOT_VERIFIED

    def test_orphan_patrimony_blocks_approval(self) -> None:
        result = reconcile(Decimal(1000), [verified(1, 1000)], orphan_patrimony_ids=[99])
        assert result.status is AuditStatus.NOT_VERIFIED

    def test_orphan_with_value_is_divergence(self) -> None:
        result = reconcile(Decimal(1500), [verified(1, 1000)], orphan_patrimony_ids=[99])
        assert result.status is AuditStatus.DIVERGENCE
        assert result.difference == Decimal(500)

    def test_zero_patrimony_company(self) -> None:
        result = reconcile(Decimal(0), [verified(1, 0)])
        assert result.status is AuditStatus.APPROVED


class TestRawWalletsTotal:
    def test_sums_everything_for_diagnosis_only(self) -> None:
        entries = [make_pl_entry(1, 1000, 2000, 3000), make_pl_entry(2, Decimal("0.5"))]
        assert raw_wallets_total(entries) == Decimal("6000.5")

    def test_none_when_any_value_is_invalid(self) -> None:
        assert raw_wallets_total([make_pl_entry(1, 1000, None)]) is None
        assert raw_wallets_total([{"clientId": 1}]) is None
