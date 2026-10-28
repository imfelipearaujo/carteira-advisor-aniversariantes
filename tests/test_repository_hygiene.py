"""Higiene do repositório: nenhum dado real, credencial ou arquivo sensível versionado.

Complementa o detect-secrets (pre-commit). Verifica apenas padrões objetivos:
CPF/CNPJ com dígitos verificadores válidos fora da lista de exemplos fictícios,
e-mails fora dos domínios de exemplo, token preenchido em arquivos versionados e
arquivos de extensões proibidas. Ver docs/DECISIONS.md, D27.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.transformer import is_valid_cnpj, is_valid_cpf

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".yaml", ".yml", ".cfg", ".ini", ".example", ".baseline"}
SKIP_DIRS = {".git", "output", "logs", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv"}
FORBIDDEN = re.compile(r"(^|/)\.env$|\.(xlsx|xlsm|xls|csv|tsv|log|json|pem|key|p12|pfx)$")
# Exemplos públicos/fictícios usados nos testes de validação de documentos.
# Documentos de exemplo públicos e fictícios, não credenciais.
FICTITIOUS_DOCUMENTS = {"52998224725", "11222333000181", "12ABC34501DE35"}  # pragma: allowlist secret
EXAMPLE_EMAIL_DOMAINS = {"example.com", "example.test"}
DOCUMENT_PATTERN = re.compile(
    r"(?<![\w.])("
    r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"  # CPF
    r"|[\dA-Z]{2}\.?[\dA-Z]{3}\.?[\dA-Z]{3}/?[\dA-Z]{4}-?\d{2}"  # CNPJ (numérico ou alfanumérico)
    r")(?![\w])"
)
# Value up to an inline comment, as python-dotenv reads it.
TOKEN_ASSIGNMENT = re.compile(r"\s*CARTEIRA_ADVISOR_TOKEN\s*=\s*([^\s#]+)")
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def repository_files() -> list[Path]:
    """Files under version control (or, outside a Git checkout, the project tree)."""
    if shutil.which("git") and (ROOT / ".git").exists():
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
        )
        return [ROOT / name for name in listed.stdout.splitlines() if (ROOT / name).is_file()]
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(ROOT).parts) and path.name != ".env"
    ]


def text_files() -> list[Path]:
    return [
        p for p in repository_files() if p.suffix in TEXT_SUFFIXES or p.name in {".gitignore", ".env.example"}
    ]


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_no_forbidden_files_are_versioned() -> None:
    offenders = [
        relative(p)
        for p in repository_files()
        if (FORBIDDEN.search(relative(p)) or relative(p).startswith(("output/", "logs/")))
        and p.name != ".gitkeep"
    ]
    assert offenders == []


def test_gitignore_protects_sensitive_paths() -> None:
    rules = set((ROOT / ".gitignore").read_text(encoding="utf-8").split())
    for rule in (".env", "output/*", "logs/*", "*.log", "*.xlsx", "*.xls", "*.csv", "*.json"):
        assert rule in rules, rule


def test_env_example_has_no_credentials_or_real_ids() -> None:
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in content.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["CARTEIRA_ADVISOR_TOKEN"].strip() == ""
    assert values["CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS"].strip() == ""


def test_no_token_assigned_in_versioned_files() -> None:
    offenders = []
    for path in text_files():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = TOKEN_ASSIGNMENT.match(line)
            if match and match.group(1) not in {'""', "''"}:
                offenders.append(relative(path))
    assert offenders == []


def test_token_detector_ignores_comments_but_catches_values() -> None:
    assert TOKEN_ASSIGNMENT.match("CARTEIRA_ADVISOR_TOKEN=   # cole aqui") is None
    assert TOKEN_ASSIGNMENT.match("CARTEIRA_ADVISOR_TOKEN=") is None
    assert TOKEN_ASSIGNMENT.match("CARTEIRA_ADVISOR_TOKEN=abc123").group(1) == "abc123"


def test_no_valid_cpf_or_cnpj_outside_fictitious_examples() -> None:
    offenders = []
    for path in text_files():
        for match in DOCUMENT_PATTERN.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            document = re.sub(r"[.\-/]", "", match.group(0)).upper()
            if document in FICTITIOUS_DOCUMENTS:
                continue
            if (len(document) == 11 and is_valid_cpf(document)) or (
                len(document) == 14 and is_valid_cnpj(document)
            ):
                offenders.append(f"{relative(path)}: {document[:3]}…")
    assert offenders == []


def test_no_real_email_addresses() -> None:
    offenders = []
    for path in text_files():
        for match in EMAIL_PATTERN.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            if match.group(1).lower() not in EXAMPLE_EMAIL_DOMAINS:
                offenders.append(f"{relative(path)}: …@{match.group(1)}")
    assert offenders == []


def test_fixture_clients_are_fictitious() -> None:
    from tests.conftest import make_client

    assert "Ficticio" in make_client(1)["name"]
    assert make_client(1)["street"] == "Rua das Flores"


@pytest.mark.parametrize("formatted", [False, True])
def test_detector_finds_valid_documents_in_text(formatted: bool) -> None:
    from tests.conftest import make_cnpj, make_cpf

    cpf, cnpj = make_cpf(7), make_cnpj(7)
    if formatted:
        cpf = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
        cnpj = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    found = {
        re.sub(r"[.\-/]", "", m.group(0)) for m in DOCUMENT_PATTERN.finditer(f"cpf {cpf} e cnpj {cnpj}.")
    }
    assert {make_cpf(7), make_cnpj(7)} <= found
