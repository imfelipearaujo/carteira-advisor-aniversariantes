"""Cadastral audit: PF/PJ x active/inactive table and id-level matching."""

from __future__ import annotations

import pytest

from src.cadastral import (
    ActivityStatus,
    CadastralStatus,
    Classification,
    audit_cadastral,
    classify_clients,
    classify_status,
)
from src.errors import TransformationError
from src.transformer import PersonType
from tests.conftest import make_client, make_cnpj

PF, PJ, PEND = PersonType.INDIVIDUAL, PersonType.COMPANY, PersonType.UNCLASSIFIED
ACT, INACT, UNK = ActivityStatus.ACTIVE, ActivityStatus.INACTIVE, ActivityStatus.UNKNOWN


def mixed_base() -> list[dict]:
    """One client per cell of the 3 x 3 table."""
    return [
        make_client(1, active=True),  # PF active
        make_client(2, active=False),  # PF inactive
        make_client(3, active=None),  # PF status pending
        make_client(4, cpfCnpj=make_cnpj(4), active=True),  # PJ active
        make_client(5, cpfCnpj=make_cnpj(5), active=False),  # PJ inactive
        make_client(6, cpfCnpj=make_cnpj(6), active="true"),  # PJ status pending
        make_client(7, cpfCnpj=None, active=True),  # doc pending, active
        make_client(8, cpfCnpj="123", active=False),  # doc pending, inactive
        make_client(9, cpfCnpj=None, active=1),  # doc pending, status pending
    ]


class TestClassifyStatus:
    def test_booleans(self) -> None:
        assert classify_status(True) is ACT
        assert classify_status(False) is INACT

    @pytest.mark.parametrize("raw", [None, "true", "false", 1, 0, "ativo", [], {}])
    def test_missing_or_non_boolean_is_pending(self, raw: object) -> None:
        assert classify_status(raw) is UNK

    def test_absent_field_is_pending(self) -> None:
        client = make_client(1)
        del client["active"]
        assert classify_clients([client]).ids(status=UNK) == [1]


class TestContingencyTable:
    def test_every_cell(self) -> None:
        table = classify_clients(mixed_base())
        expected = {
            (PF, ACT): [1], (PF, INACT): [2], (PF, UNK): [3],
            (PJ, ACT): [4], (PJ, INACT): [5], (PJ, UNK): [6],
            (PEND, ACT): [7], (PEND, INACT): [8], (PEND, UNK): [9],
        }  # fmt: skip
        assert {cell: ids for cell, ids in table.cells.items() if ids} == expected
        assert table.count(person=PersonType.SPECIAL) == 0

    def test_row_and_column_totals_add_up(self) -> None:
        table = classify_clients(mixed_base())
        assert sum(table.count(person=p) for p in PersonType) == table.total == 9
        assert sum(table.count(status=s) for s in ActivityStatus) == table.total
        assert sorted(table.ids()) == list(range(1, 10))

    def test_duplicate_id_is_rejected(self) -> None:
        with pytest.raises(TransformationError, match="mais de uma classificação"):
            classify_clients([make_client(1), make_client(1, active=False)])

    def test_inconsistent_table_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A table whose cells do not cover every client must never pass.
        original = Classification.count

        def broken_count(self, person=None, status=None):  # type: ignore[no-untyped-def]
            return original(self, person, status) + (1 if person is PF and status is None else 0)

        monkeypatch.setattr(Classification, "count", broken_count)
        with pytest.raises(TransformationError, match="PF \\+ PJ"):
            classify_clients(mixed_base())


class TestAuditCadastral:
    def clean_base(self) -> list[dict]:
        return [
            make_client(1),
            make_client(2),
            make_client(3, active=False),
            make_client(4, cpfCnpj=make_cnpj(4)),
        ]

    def test_approved_when_ids_match_exactly(self) -> None:
        clients = self.clean_base()
        audit = audit_cadastral(clients, classify_clients(clients), [4, 2, 1])
        assert audit.status is CadastralStatus.APPROVED
        assert audit.in_both == [1, 2, 4]
        assert audit.active_missing_from_patrimony == audit.patrimony_not_active == []
        assert audit.outside_patrimony == [(3, PF, INACT)]

    def test_equal_counts_with_different_ids_is_divergent(self) -> None:
        clients = self.clean_base()
        # 3 active and 3 consolidated records, but not the same clients.
        audit = audit_cadastral(clients, classify_clients(clients), [1, 2, 3])
        assert len(audit.active_ids) == len(audit.patrimony_ids) == 3
        assert audit.status is CadastralStatus.DIVERGENT
        assert audit.active_missing_from_patrimony == [4]
        assert audit.patrimony_not_active == [3]

    def test_unregistered_patrimony_id_is_reported(self) -> None:
        clients = self.clean_base()
        audit = audit_cadastral(clients, classify_clients(clients), [1, 2, 4, 999])
        assert audit.patrimony_not_active == [999]
        assert audit.status is CadastralStatus.DIVERGENT

    def test_string_and_int_ids_match_exactly(self) -> None:
        clients = self.clean_base()
        audit = audit_cadastral(clients, classify_clients(clients), ["1", "2", "4"])
        assert audit.status is CadastralStatus.APPROVED

    def test_repeated_patrimony_id_is_divergent(self) -> None:
        clients = self.clean_base()
        audit = audit_cadastral(clients, classify_clients(clients), [1, 2, 4, 4])
        assert audit.status is CadastralStatus.DIVERGENT
        assert any("repetido" in r for r in audit.reasons)

    @pytest.mark.parametrize("override", [{"cpfCnpj": None}, {"active": None}])
    def test_pending_classification_blocks_approval(self, override: dict) -> None:
        clients = [*self.clean_base(), make_client(5, **override)]
        ids = [1, 2, 4] + ([5] if override.get("active", True) else [])
        audit = audit_cadastral(clients, classify_clients(clients), ids)
        assert audit.active_missing_from_patrimony == audit.patrimony_not_active == []
        assert audit.status is CadastralStatus.DIVERGENT

    def test_patrimony_unavailable_is_not_verified(self) -> None:
        clients = self.clean_base()
        audit = audit_cadastral(clients, classify_clients(clients), None)
        assert audit.status is CadastralStatus.NOT_VERIFIED


class TestSpecialRecords:
    SPECIAL = frozenset({"5", "6"})

    def base(self) -> list[dict]:
        return [
            make_client(1),
            make_client(2, active=False),
            make_client(3, cpfCnpj=make_cnpj(3)),
            make_client(4, cpfCnpj=None),  # genuinely pending
            make_client(5, cpfCnpj=None, name="Familia Ficticia"),  # special (family)
            make_client(6, cpfCnpj=None, name="Simulacao Ficticia"),  # special (simulation)
        ]

    def test_special_ids_get_their_own_row(self) -> None:
        table = classify_clients(self.base(), self.SPECIAL)
        assert table.ids(person=PersonType.SPECIAL) == [5, 6]
        assert table.ids(person=PEND) == [4]
        assert sum(table.count(person=p) for p in PersonType) == table.total == 6
        assert sum(table.count(status=s) for s in ActivityStatus) == table.total

    def test_without_configuration_they_remain_pending(self) -> None:
        table = classify_clients(self.base())
        assert table.ids(person=PEND) == [4, 5, 6]
        assert table.count(person=PersonType.SPECIAL) == 0

    def test_special_records_do_not_block_cadastral_audit(self) -> None:
        clients = [c for c in self.base() if c["id"] != 4]
        audit = audit_cadastral(clients, classify_clients(clients, self.SPECIAL), [1, 3, 5, 6])
        assert audit.status is CadastralStatus.APPROVED
        assert audit.in_both == [1, 3, 5, 6]

    def test_remaining_pending_document_still_blocks(self) -> None:
        clients = self.base()
        audit = audit_cadastral(clients, classify_clients(clients, self.SPECIAL), [1, 3, 4, 5, 6])
        assert audit.status is CadastralStatus.DIVERGENT
        assert any("[4]" in r for r in audit.reasons)
