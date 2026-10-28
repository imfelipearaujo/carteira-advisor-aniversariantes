# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versões seguem [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.0.0]

Primeira versão estável, distribuída sob a licença MIT.

### Planilha de aniversariantes

- Comando único `python main.py`.
- Planilha `Aniversariantes` com oito colunas (nome, nascimento, patrimônio,
  endereço, cidade, estado, consultor, status), autofiltro e cabeçalho congelado.
- Somente pessoas físicas ativas: exclui pessoas jurídicas, inativos, registros
  especiais configurados e cadastros com documento ou situação pendentes.
- Ordenação por mês e dia, com nascimentos ausentes ou inválidos no final.
- Patrimônio de referência (`clients.currentValue`), consultado a cada execução.
- Gravação atômica com validação do arquivo antes da publicação.

### Auditorias

- Auditoria cadastral: tabela documento × situação e conferência por ID com o
  patrimônio consolidado.
- Auditoria financeira detalhada opcional (`CARTEIRA_ADVISOR_DETAILED_AUDIT=true`):
  moedas das carteiras, estimativa independente com a PTAX, possíveis
  duplicidades, estabilidade durante a coleta e diagnóstico local.

### Integração e segurança

- Somente `GET`, limite de 50 requisições por minuto, tratamento de HTTP 429 e
  `Retry-After`, timeout e tentativas limitadas.
- Modo rápido por padrão (3 requisições).
- `pre-commit` com detect-secrets e bloqueio de arquivos sensíveis; teste de
  higiene do repositório; CI com Ruff, pytest (Python 3.11 a 3.13) e
  detect-secrets, sem token e sem acesso à API.
