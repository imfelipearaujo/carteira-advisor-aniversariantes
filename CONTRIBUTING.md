# Como contribuir

## Ambiente

```bash
python -m venv .venv
pip install -r requirements-dev.txt
python -m pre_commit install
```

## Antes de cada commit

```bash
python -m ruff check .
python -m ruff format .
python -m pytest
```

O `pre-commit` executa automaticamente detect-secrets, bloqueio de arquivos
sensíveis, Ruff e o teste de higiene do repositório.

## Regras

- **Dados:** use somente dados fictícios em testes, exemplos e documentação.
  Nunca versione `.env`, tokens, planilhas, logs, respostas da API ou qualquer
  dado real de clientes, inclusive IDs ou valores.
- **API:** somente requisições `GET`. Testes não acessam a rede: use os fakes de
  `tests/conftest.py` e sessões simuladas.
- **Código:** PEP 8 via Ruff, type hints e docstrings no padrão Google. Nomes de
  código em inglês; documentação e mensagens ao usuário em português (pt-BR).
- **Regras de negócio:** mudanças em classificação, ordenação, colunas da
  planilha ou auditorias devem ser registradas em `docs/DECISIONS.md`, com
  testes.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`), pequenos e com os
  testes passando.
- **Branches:** desenvolvimento na `develop`; a `main` recebe apenas versões
  aprovadas. Sem force push.

## Licença

Ao contribuir, você concorda que sua contribuição será distribuída sob a
[licença MIT](LICENSE) do projeto.

## Segurança

Vulnerabilidades devem ser relatadas de forma privada, conforme
[SECURITY.md](SECURITY.md).
