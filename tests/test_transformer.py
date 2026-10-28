"""Birth dates, ordering, address, consultant and status rules."""

from datetime import date
from decimal import Decimal

import pytest

from src.transformer import (
    Pendency,
    PersonType,
    ReportRow,
    build_address,
    build_rows,
    classify_document,
    classify_person,
    is_valid_cnpj,
    is_valid_cpf,
    parse_birthdate,
    sort_rows,
)
from tests.conftest import CONSULTANTS, make_client, make_cnpj, make_cpf

TODAY = date(2026, 10, 28)


class TestBirthdate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1982-01-05", date(1982, 1, 5)),
            ("1988-02-29", date(1988, 2, 29)),
            (" 1965-12-31 ", date(1965, 12, 31)),
        ],
    )
    def test_valid(self, raw: str, expected: date) -> None:
        assert parse_birthdate(raw, TODAY) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "2001-02-29",
            "1990-13-01",
            "1990-00-10",
            "05/01/1982",
            "1982-1-5",
            "2030-01-01",
            19820105,
        ],
    )
    def test_invalid_or_missing(self, raw: object) -> None:
        assert parse_birthdate(raw, TODAY) is None


class TestOrdering:
    def test_documented_example_order(self) -> None:
        clients = [
            make_client(1, birthdate="1965-12-31"),
            make_client(2, birthdate=None),
            make_client(3, birthdate="1971-02-03"),
            make_client(4, birthdate="1990-01-20"),
            make_client(5, birthdate="data-invalida"),
            make_client(6, birthdate="1988-02-29"),
            make_client(7, birthdate="1982-01-05"),
        ]
        rows = sort_rows(build_rows(clients, CONSULTANTS, TODAY))
        dates = [r.birthdate.strftime("%d/%m/%Y") if r.birthdate else "PENDENTE" for r in rows]
        assert dates == [
            "05/01/1982",
            "20/01/1990",
            "03/02/1971",
            "29/02/1988",
            "31/12/1965",
            "PENDENTE",
            "PENDENTE",
        ]

    def test_birth_year_does_not_affect_order(self) -> None:
        clients = [make_client(1, birthdate="2000-03-10"), make_client(2, birthdate="1950-03-11")]
        rows = sort_rows(build_rows(clients, CONSULTANTS, TODAY))
        assert [r.client_id for r in rows] == [1, 2]
        assert rows[0].birthdate.year == 2000

    def test_ties_broken_by_name_then_id(self) -> None:
        clients = [
            make_client(9, name="Bruno", birthdate="1980-05-05"),
            make_client(8, name="ana", birthdate="1990-05-05"),
            make_client(3, name="Bruno", birthdate="1970-05-05"),
        ]
        rows = sort_rows(build_rows(clients, CONSULTANTS, TODAY))
        assert [r.client_id for r in rows] == [8, 3, 9]

    def test_pending_rows_sorted_by_name(self) -> None:
        clients = [make_client(1, name="Zeca", birthdate=None), make_client(2, name="Ana", birthdate="x")]
        rows = sort_rows(build_rows(clients, CONSULTANTS, TODAY))
        assert [r.name for r in rows] == ["Ana", "Zeca"]


class TestAddress:
    def test_full_address_order(self) -> None:
        client = make_client(1, complement="Apto 12")
        assert build_address(client) == (
            "Rua das Flores, 100, Apto 12, Centro, Cidade Exemplo, SP, CEP 01001-000, Brasil"
        )

    def test_missing_parts_have_no_empty_separators(self) -> None:
        client = make_client(1, number=None, complement="  ", district="", postalCode=None)
        assert build_address(client) == "Rua das Flores, Cidade Exemplo, SP, Brasil"

    def test_totally_missing_address(self) -> None:
        keys = ("street", "number", "complement", "district", "city", "state", "country", "postalCode")
        assert build_address(make_client(1, **dict.fromkeys(keys))) is None

    def test_non_brazilian_postal_code_kept_as_is(self) -> None:
        assert "CEP 10115" in build_address(make_client(1, postalCode="10115"))


class TestRowsAndStatus:
    def test_complete_client_is_ok(self) -> None:
        (row,) = build_rows([make_client(1)], CONSULTANTS, TODAY)
        assert row.status == "OK"
        assert row.consultant == "Consultora Exemplo"
        assert row.patrimony == Decimal(1000)

    def test_consultant_matched_by_id_only(self) -> None:
        client = make_client(1, consultantId=999, consultantName="Consultora Exemplo")
        (row,) = build_rows([client], CONSULTANTS, TODAY)
        assert row.consultant is None
        assert row.status == "PENDÊNCIA: CONSULTOR"

    def test_consultant_id_string_and_int_match_exactly(self) -> None:
        (row,) = build_rows([make_client(1, consultantId="1")], CONSULTANTS, TODAY)
        assert row.consultant == "Consultora Exemplo"

    def test_missing_consultant_id(self) -> None:
        (row,) = build_rows([make_client(1, consultantId=None)], CONSULTANTS, TODAY)
        assert Pendency.CONSULTANT in row.pendencies

    def test_patrimony_comes_from_client_current_value(self) -> None:
        (row,) = build_rows([make_client(1, currentValue=Decimal("1234.5678901234"))], CONSULTANTS, TODAY)
        assert row.patrimony == Decimal("1234.5678901234")
        assert row.status == "OK"

    def test_zero_patrimony_is_valid(self) -> None:
        (row,) = build_rows([make_client(1, currentValue=0)], CONSULTANTS, TODAY)
        assert row.patrimony == Decimal(0)
        assert row.status == "OK"

    @pytest.mark.parametrize("raw", [None, "1000", True, 1.5])
    def test_absent_or_invalid_patrimony_is_pending_and_blank(self, raw: object) -> None:
        (row,) = build_rows([make_client(1, currentValue=raw)], CONSULTANTS, TODAY)
        assert row.patrimony is None
        assert row.status == "PENDÊNCIA: PATRIMÔNIO"

    def test_multiple_pendencies_listed_in_order(self) -> None:
        keys = ("street", "number", "complement", "district", "city", "state", "country", "postalCode")
        client = make_client(1, birthdate=None, consultantId=None, currentValue=None, **dict.fromkeys(keys))
        (row,) = build_rows([client], CONSULTANTS, TODAY)
        assert row.status == "PENDÊNCIA: DATA DE NASCIMENTO; CONSULTOR; PATRIMÔNIO; ENDEREÇO"

    def test_inactive_and_deceased_clients_are_kept(self) -> None:
        clients = [make_client(1, active=False), make_client(2, deceased=True, visualizationOnly=True)]
        rows = build_rows(clients, CONSULTANTS, TODAY)
        assert len(rows) == 2

    def test_status_deduplicates(self) -> None:
        row = ReportRow(
            1, "x", None, None, None, None, [Pendency.ADDRESS, Pendency.BIRTHDATE, Pendency.ADDRESS]
        )
        assert row.status == "PENDÊNCIA: DATA DE NASCIMENTO; ENDEREÇO"


class TestDocumentClassification:
    @pytest.mark.parametrize("raw", ["52998224725", "529.982.247-25", " 529 982 247 25 "])
    def test_valid_cpf_is_individual(self, raw: str) -> None:
        assert classify_document(raw) is PersonType.INDIVIDUAL

    @pytest.mark.parametrize("raw", ["11222333000181", "11.222.333/0001-81", "12.ABC.345/01DE-35"])
    def test_valid_cnpj_is_company(self, raw: str) -> None:
        # Includes the alphanumeric CNPJ example published by Receita Federal.
        assert classify_document(raw) is PersonType.COMPANY

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "52998224724",  # wrong CPF check digit
            "11111111111",  # repeated digits
            "11222333000182",  # wrong CNPJ check digit
            "5299822472",  # 10 digits: could be a CPF that lost a leading zero
            "529982247250",  # 12 digits
            "CPF52998224725",  # unexpected characters
            52998224725,  # not a string
            True,
        ],
    )
    def test_unsafe_documents_are_unclassified(self, raw: object) -> None:
        assert classify_document(raw) is PersonType.UNCLASSIFIED

    def test_fixture_documents_are_valid(self) -> None:
        assert all(is_valid_cpf(make_cpf(i)) for i in range(1, 200))
        assert all(is_valid_cnpj(make_cnpj(i)) for i in range(1, 200))


class TestClassifyPerson:
    def test_configured_id_is_special_even_without_document(self) -> None:
        assert classify_person(make_client(1001, cpfCnpj=None), frozenset({"1001"})) is PersonType.SPECIAL

    def test_id_matching_is_textual(self) -> None:
        assert classify_person(make_client(1002, cpfCnpj=None), frozenset({"1002"})) is PersonType.SPECIAL
        assert classify_person({"id": "1002", "cpfCnpj": None}, frozenset({"1002"})) is PersonType.SPECIAL

    def test_other_ids_use_the_document(self) -> None:
        special = frozenset({"1001"})
        assert classify_person(make_client(1), special) is PersonType.INDIVIDUAL
        assert classify_person(make_client(2, cpfCnpj=make_cnpj(2)), special) is PersonType.COMPANY
        assert classify_person(make_client(3, cpfCnpj=None), special) is PersonType.UNCLASSIFIED


class TestCityAndState:
    def test_taken_as_registered_and_kept_in_address(self) -> None:
        (row,) = build_rows([make_client(1, city=" Porto Alegre ", state="RS")], CONSULTANTS, TODAY)
        assert (row.city, row.state) == ("Porto Alegre", "RS")
        assert "Porto Alegre, RS" in row.address

    def test_missing_values_are_not_invented(self) -> None:
        (row,) = build_rows([make_client(1, city="", state=None)], CONSULTANTS, TODAY)
        assert (row.city, row.state) == (None, None)
        assert row.status == "OK"  # o endereço ainda tem outros componentes
