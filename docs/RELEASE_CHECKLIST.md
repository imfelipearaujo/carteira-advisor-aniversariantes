# Checklist de publicação de versão

Use antes de publicar uma versão ou um repositório de distribuição. Todas as
verificações devem passar; nenhuma delas usa token ou dados reais.

## 1. Origem limpa

- [ ] Partir **somente dos arquivos versionados** (`git archive <branch>`), sem
      histórico, `.env`, `output/`, `logs/` ou caches.
- [ ] Um repositório de distribuição novo começa com um único commit inicial;
      nenhum commit de repositórios privados anteriores é importado.

## 2. Verificações automáticas

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m pre_commit run --all-files
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline
```

- [ ] Todos os comandos acima sem falhas.
- [ ] CI (GitHub Actions) verde em todas as versões de Python da matriz.

## 3. Revisão manual

- [ ] `git ls-files` contém apenas código, testes, documentação e configuração
      (nenhum `.env`, `.xlsx`, `.csv`, `.json`, `.log`, nada em `output/` ou `logs/`
      além de `.gitkeep`).
- [ ] `.env.example` sem token e sem IDs reais.
- [ ] README, `docs/`, `SECURITY.md`, `CONTRIBUTING.md` e `CHANGELOG.md` sem nomes,
      documentos, IDs, valores ou estatísticas de clientes reais.
- [ ] Opcional, localmente e sem enviar dados a terceiros: comparar os arquivos
      com uma consulta à API (nomes, CPF/CNPJ, e-mails, valores) para confirmar
      que nenhum dado real foi incluído.

## 4. Versão

- [ ] `version` em `pyproject.toml` e entrada no `CHANGELOG.md` atualizadas.
- [ ] Tag anotada `vX.Y.Z` criada sobre o commit verificado.
- [ ] Arquivo `LICENSE` presente e coerente com o README.
