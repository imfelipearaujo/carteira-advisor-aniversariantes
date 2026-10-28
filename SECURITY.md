# Política de segurança

## Escopo

Esta política cobre o código deste repositório. Vulnerabilidades da plataforma
Carteira Advisor ou de sua API devem ser comunicadas diretamente ao fornecedor,
pelos canais de atendimento da plataforma.

## Como relatar uma vulnerabilidade

Relate de forma **privada**:

- pelo recurso *Report a vulnerability* (aba **Security** do repositório no
  GitHub), quando estiver habilitado; ou
- diretamente ao mantenedor do repositório, por canal privado.

**Não** abra issues públicas para vulnerabilidades e **nunca** inclua no relato:
tokens, credenciais, planilhas, logs, respostas da API ou qualquer dado real de
clientes (nomes, CPF/CNPJ, endereços, patrimônios, IDs). Descreva o problema com
dados fictícios.

Inclua, se possível: versão ou commit afetado, passos para reproduzir com dados
fictícios, impacto esperado e sugestão de correção.

## Tratamento de credenciais

- O token da API fica apenas no `.env` local, que é ignorado pelo Git.
- Se um token for exposto (commit, log, captura de tela, mensagem), solicite a
  revogação e um novo token ao atendimento do Carteira Advisor **antes** de
  qualquer outra ação. Remover o arquivo do repositório não basta.
- A autenticação por token não torna a API invulnerável. Proteja também o
  computador e a pasta onde a ferramenta é executada.

## Dados gerados

`output/` (planilhas e diagnósticos) e `logs/` contêm ou podem conter dados
pessoais. Eles não devem ser versionados, anexados a issues nem compartilhados
fora do escritório. O tratamento desses dados é responsabilidade do escritório
que executa a ferramenta, conforme a LGPD.

## Verificações automatizadas

- `pre-commit`: detect-secrets, detecção de chave privada, bloqueio de `.env`,
  planilhas, logs e arquivos em `output/`/`logs/`, Ruff e teste de higiene.
- CI (GitHub Actions): Ruff, pytest e detect-secrets, sem token e sem acesso à
  API de produção.

## Versões suportadas

Correções de segurança são feitas apenas na versão mais recente da branch
principal.
