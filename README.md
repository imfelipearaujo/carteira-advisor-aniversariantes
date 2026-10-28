# Carteira Advisor — Aniversariantes

Gera, com um único comando, uma planilha Excel com os aniversariantes entre os
clientes pessoa física de um escritório, usando os dados já centralizados no
Carteira Advisor por meio da API da plataforma.

> Projeto independente. Não é um produto oficial do Carteira Advisor e não tem
> parceria, autorização institucional ou vínculo com a empresa. Veja
> [Aviso de projeto independente](#14-aviso-de-projeto-independente).

## Sumário

1. [Apresentação e finalidade](#1-apresentação-e-finalidade)
2. [Funcionalidades](#2-funcionalidades)
3. [Pré-requisitos](#3-pré-requisitos)
4. [Solicitação do token](#4-solicitação-do-token)
5. [Instalação](#5-instalação)
6. [Configuração](#6-configuração)
7. [Execução](#7-execução)
8. [Estrutura da planilha](#8-estrutura-da-planilha)
9. [Modo rápido e auditoria detalhada](#9-modo-rápido-e-auditoria-detalhada)
10. [Segurança e privacidade](#10-segurança-e-privacidade)
11. [Testes e qualidade](#11-testes-e-qualidade)
12. [Limitações conhecidas](#12-limitações-conhecidas)
13. [Possíveis melhorias futuras](#13-possíveis-melhorias-futuras)
14. [Aviso de projeto independente](#14-aviso-de-projeto-independente)

Referência: [estrutura do projeto](#estrutura-do-projeto) ·
[solução de problemas](#solução-de-problemas) ·
[arquitetura](docs/ARCHITECTURE.md) · [dicionário de dados](docs/DATA_DICTIONARY.md) ·
[decisões técnicas](docs/DECISIONS.md) · [segurança](SECURITY.md) ·
[contribuição](CONTRIBUTING.md) · [changelog](CHANGELOG.md) · [licença MIT](LICENSE)

## 1. Apresentação e finalidade

O projeto nasceu da necessidade de automatizar a lista de aniversariantes de
clientes a partir de dados que o escritório já mantém no Carteira Advisor, sem
planilhas paralelas nem digitação manual.

A lista apoia atividades administrativas, comerciais e de relacionamento. O
patrimônio de cada cliente aparece como **informação de referência**: a
ferramenta não define valores de presentes, critérios de elegibilidade nem
qualquer regra comercial. Cada escritório aplica a própria política de
relacionamento.

## 2. Funcionalidades

- Planilha de aniversariantes ordenada por **mês e dia** (o ano não interfere),
  com datas ausentes ou inválidas no final.
- Inclui **somente pessoas físicas ativas**:
  - **pessoas jurídicas** (CNPJ válido) são excluídas;
  - **clientes inativos** são excluídos;
  - **registros especiais** configurados (por exemplo, cadastro familiar ou
    carteira de simulação) são excluídos;
  - documentos ausentes ou inválidos e situação cadastral não informada ficam
    fora e são listados no resumo para correção no cadastro.
- Patrimônio de referência (`clients.currentValue`), endereço completo, cidade,
  estado e consultor responsável.
- Coluna de status com as pendências de cada cliente (nascimento, patrimônio,
  endereço, consultor).
- Auditoria cadastral: confere por ID se os clientes ativos são exatamente os
  presentes no patrimônio consolidado.
- Auditoria financeira detalhada **opcional**: moedas das carteiras, verificação
  independente com a cotação PTAX do Banco Central, possíveis duplicidades entre
  cadastros, estabilidade dos dados durante a coleta e relatório de diagnóstico.
- Somente leitura: todas as chamadas à API são `GET`, com limite de requisições
  por minuto.

## 3. Pré-requisitos

- Python **3.11** ou superior.
- Acesso ao Carteira Advisor com permissão para usar a API da empresa.
- Um **token de API** (próxima seção).

## 4. Solicitação do token

O token é solicitado pelo próprio usuário, **abrindo um chamado ao atendimento do
Carteira Advisor dentro da plataforma**.

Trate o token como uma senha:

- ele permite consultar pela API os dados de clientes do escritório; quem o
  possuir terá esse acesso;
- guarde-o apenas no arquivo `.env` local, que nunca é versionado;
- não o envie por e-mail, chat ou issue, nem o registre em capturas de tela;
- se houver suspeita de exposição, solicite a revogação e um novo token ao
  atendimento.

A autenticação por token **não torna a API ou os dados invulneráveis**. A
proteção depende também da guarda do token e do computador onde a ferramenta é
executada.

## 5. Instalação

```bash
git clone https://github.com/imfelipearaujo/carteira-advisor-aniversariantes.git
cd carteira-advisor-aniversariantes
python -m venv .venv
# Windows: .venv\Scripts\activate    |    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt        # para desenvolver: requirements-dev.txt
```

## 6. Configuração

Copie o modelo e preencha o token:

```bash
cp .env.example .env
```

Configuração mínima:

```env
CARTEIRA_ADVISOR_TOKEN=                  # cole aqui o token recebido do atendimento
CARTEIRA_ADVISOR_DETAILED_AUDIT=false
CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS=      # opcional, ex.: 1001,1002
```

<details>
<summary>Todas as variáveis</summary>

| Variável | Padrão | Descrição |
|---|---|---|
| `CARTEIRA_ADVISOR_TOKEN` | — | Token da API (obrigatório). Nunca é registrado em logs. |
| `CARTEIRA_ADVISOR_BASE_URL` | `https://api-prod.carteiradeinvestimentos.com/v1/user` | Base de clientes e consultores (`https://` obrigatório). |
| `CARTEIRA_ADVISOR_PATRIMONY_BASE_URL` | `https://api-prod.carteiradeinvestimentos.com/v1/trade-rules` | Base do patrimônio consolidado e das carteiras. |
| `CARTEIRA_ADVISOR_DETAILED_AUDIT` | `false` | `false`: modo rápido. `true`: auditoria financeira detalhada (seção 9). |
| `CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS` | *(vazio)* | IDs de registros especiais, separados por vírgula. Ficam fora da planilha e são contados na auditoria cadastral. |
| `CARTEIRA_ADVISOR_MAX_REQUESTS_PER_MINUTE` | `50` | Limite de requisições por minuto ao Carteira Advisor (chamadas espaçadas em 60/limite segundos). |
| `CARTEIRA_ADVISOR_TIMEOUT_SECONDS` | `30` | Timeout por requisição. |
| `CARTEIRA_ADVISOR_MAX_RETRIES` | `3` | Novas tentativas para HTTP 429/5xx e falhas de rede. |
| `CARTEIRA_ADVISOR_AUTH_HEADER` | `token` | Header que transporta o token. |
| `CARTEIRA_ADVISOR_AUTH_SCHEME` | *(vazio)* | Prefixo do token (ex.: `Bearer`); vazio envia o token puro. |
| `CARTEIRA_ADVISOR_CONSOLIDATED_PL_PATH` | `/company-api/wallet/consolidated-pl` | Caminho do patrimônio consolidado. |
| `CARTEIRA_ADVISOR_PAGE_PARAM` | `page` | Parâmetro do número da página (começa em 0). |
| `CARTEIRA_ADVISOR_SIZE_PARAM` | `size` | Parâmetro do tamanho da página. |
| `CARTEIRA_ADVISOR_PAGE_SIZE` | `100` | Registros por página. |
| `CARTEIRA_ADVISOR_MONEY_UNIT` | `reais` | `reais` ou `centavos`. |
| `CARTEIRA_ADVISOR_WALLET_POLICY` | `unconfirmed` | Política de carteiras da auditoria exata. Altere somente com confirmação formal (ver [D3](docs/DECISIONS.md)). |
| `CARTEIRA_ADVISOR_WALLET_DETAILS` | `true` | Auditoria detalhada: consulta moeda e consolidação de cada carteira. |
| `CARTEIRA_ADVISOR_WALLET_DETAIL_INTERVAL_MS` | `1000` | Pausa adicional entre consultas de carteiras. |
| `CARTEIRA_ADVISOR_FX_CHECK` | `true` | Auditoria detalhada: verificação independente com a PTAX. |
| `CARTEIRA_ADVISOR_PTAX_BASE_URL` | `https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1` | Serviço PTAX do Banco Central. |
| `CARTEIRA_ADVISOR_PTAX_WINDOW_DAYS` | `10` | Idade máxima da cotação em relação à consolidação de cada carteira. |
| `CARTEIRA_ADVISOR_FX_RELEVANT_PCT` | `5` | Divergência (%) a partir da qual um cliente é destacado. Só diagnóstico. |
| `CARTEIRA_ADVISOR_PATRIMONY_THRESHOLDS` | *(vazio)* | Valores de referência em R$ (ex.: `1000000,5000000`) para sinalizar clientes cuja posição muda conforme a fonte do patrimônio. Não impõem regra comercial. |
| `CARTEIRA_ADVISOR_DIAGNOSTIC_REPORT` | `true` | Auditoria detalhada: grava o relatório de diagnóstico local. |

</details>

## 7. Execução

```bash
python main.py
```

É o único comando operacional. Ao final, o terminal mostra um resumo e o caminho
do arquivo gerado em `output/Aniversariantes_AAAA-MM-DD_HHMMSS.xlsx`.

O resumo traz a classificação cadastral (PF, PJ, registros especiais e pendências
× ativos e inativos), a auditoria cadastral, os indicadores da execução, a
lista de clientes que precisam de conferência e o número de requisições feitas.
Clientes aparecem sempre como `NOME COMPLETO (ID n)`.

| Código de saída | Significado |
|---|---|
| 0 | Planilha publicada. O resultado das auditorias aparece no resumo e não altera o código. |
| 1 | Erro inesperado (detalhes no log). |
| 2 | Configuração inválida ou token ausente (nenhuma requisição é feita). |
| 3 | Falha de autenticação ou coleta cadastral incompleta/inconsistente (nenhum arquivo é gerado). |
| 5 | Erro de transformação (por exemplo, quantidade de linhas diferente da de PF ativas). |
| 6 | Falha ao gravar, validar ou publicar o Excel (nenhum arquivo é gerado). |

## 8. Estrutura da planilha

Uma aba, `Aniversariantes`, com autofiltro nas oito colunas e cabeçalho congelado:

| # | Coluna | Conteúdo |
|---|---|---|
| 1 | Nome completo | Nome do cadastro. |
| 2 | Data de nascimento | Data real do Excel (`DD/MM/AAAA`), ou `PENDENTE`. |
| 3 | Patrimônio | Valor numérico em R$ (`clients.currentValue`), consultado a cada execução. Marcado como provisório no cabeçalho, pois não é conciliado financeiramente. |
| 4 | Endereço completo | Logradouro, número, complemento, bairro, cidade, estado, CEP e país disponíveis. |
| 5 | Cidade | `clients.city`, como cadastrada. |
| 6 | Estado | `clients.state`, como cadastrado. |
| 7 | Consultor responsável | Consultor relacionado pelo ID. |
| 8 | Status | `OK` ou `PENDÊNCIA: …`, com destaque discreto. |

Exemplo **fictício** de linha:

| Nome completo | Data de nascimento | Patrimônio | Endereço completo | Cidade | Estado | Consultor responsável | Status |
|---|---|---|---|---|---|---|---|
| Maria Exemplo da Silva | 05/01/1980 | R$ 250.000,00 | Rua das Flores, 100, Centro, Cidade Exemplo, SP, CEP 01001-000, Brasil | Cidade Exemplo | SP | Consultora Exemplo | OK |

A planilha é gravada em arquivo temporário, reaberta e validada (colunas,
quantidade de linhas, tipos, filtro e conteúdo) antes de ser publicada. Nenhum
dado é inventado: campos ausentes ficam vazios e geram pendência.

## 9. Modo rápido e auditoria detalhada

| | Modo rápido (padrão) | Auditoria detalhada |
|---|---|---|
| Configuração | `CARTEIRA_ADVISOR_DETAILED_AUDIT=false` | `CARTEIRA_ADVISOR_DETAILED_AUDIT=true` |
| Planilha e auditoria cadastral | sim | sim |
| Requisições ao Carteira Advisor | **3** (clientes, consultores, consolidado) | 3 + **1 por carteira** + 1 (segunda leitura do consolidado) |
| Outras consultas | — | 1 à PTAX (Banco Central) |
| Duração típica | segundos | minutos (limite de 50 requisições/min) |
| Moedas das carteiras e auditoria exata | — | sim |
| Estimativa independente com PTAX | — | sim |
| Possível duplicidade entre cadastros | — | sim |
| Estabilidade dos dados durante a coleta | — | sim |
| Relatório de diagnóstico (`output/diagnostico/`) | — | sim |

A planilha é publicada **antes** da auditoria detalhada. Uma falha ou um limite
da API nessa etapa nunca impede o Excel. Não há cache de patrimônio entre
execuções: os valores mudam ao longo do dia e são sempre consultados de novo.

A auditoria **exata** (soma das carteiras = total da empresa) fica como
`NÃO VERIFICADA` enquanto a API não informar a cotação usada pela plataforma
para carteiras em outras moedas. A estimativa com a PTAX é uma referência
independente, não a metodologia oficial do Carteira Advisor.

## 10. Segurança e privacidade

- Somente leitura: o cliente HTTP só envia `GET`.
- Limite de **50 requisições por minuto** (configurável), respeito a HTTP 429 e
  `Retry-After`, timeout e número limitado de tentativas.
- O token fica só no `.env` local e nunca aparece em logs.
- `output/` (planilhas e diagnósticos) e `logs/` contêm dados pessoais e são
  ignorados pelo Git. Os logs registram apenas IDs e contagens.
- O repositório contém apenas código, configuração sem credenciais, documentação
  e **dados fictícios** de teste. Verificações automáticas antes de cada commit
  (detect-secrets, bloqueio de arquivos sensíveis e teste de higiene) ajudam a
  manter essa regra.
- Vulnerabilidades: veja [SECURITY.md](SECURITY.md).

## 11. Testes e qualidade

```bash
pip install -r requirements-dev.txt
python -m pytest                  # não usa token nem rede; só dados fictícios e mocks
python -m ruff check .
python -m ruff format --check .
```

Verificações antes de cada commit (uma vez por clone):

```bash
python -m pre_commit install
python -m pre_commit run --all-files   # execução manual
```

O CI (GitHub Actions, `.github/workflows/ci.yml`) roda Ruff, pytest e a
verificação de segredos a cada push e pull request, sem token e sem acessar a
API.

## 12. Limitações conhecidas

- O patrimônio da planilha é o valor do cadastro (`clients.currentValue`), não
  conciliado financeiramente.
- A auditoria exata não pode ser aprovada sem a cotação oficial usada pela
  plataforma para carteiras em dólar.
- Registros especiais dependem da lista de IDs configurada no `.env`.
- Carteiras zeradas ou nunca consolidadas podem ser intencionais. O resumo as
  lista como informação, não como erro.
- A detecção de duplicidade só identifica valores idênticos.
- A auditoria detalhada faz uma requisição por carteira e leva alguns minutos.
- A estimativa independente depende do serviço público da PTAX.

Detalhes em [docs/DECISIONS.md](docs/DECISIONS.md).

## 13. Possíveis melhorias futuras

Ideias **não implementadas**:

- Contemplar aniversários de **sócios e representantes de pessoas jurídicas**,
  desde que essas informações possam ser mantidas na própria plataforma de origem,
  sem exigir uma base cadastral paralela.
- Usar a cotação oficial da plataforma, se ela for disponibilizada pela API, para
  permitir a aprovação da auditoria exata.
- Separar a lógica de negócio em um pacote reutilizável, independente da geração
  de Excel.
- Filtros de período (por exemplo, aniversariantes do mês) no próprio comando.

## 14. Aviso de projeto independente

Este é um projeto independente que utiliza a API disponibilizada pelo Carteira
Advisor aos seus clientes. Não é desenvolvido, mantido, endossado nem auditado
pelo Carteira Advisor, e não há parceria ou vínculo institucional. "Carteira
Advisor" é marca de seu respectivo titular.

O uso da API está sujeito aos termos da plataforma. Cada escritório é
responsável pelo uso dos dados de seus clientes e pela conformidade com a LGPD.

## Licença

Distribuído sob a [licença MIT](LICENSE). O software é fornecido "como está",
sem garantias.

---

## Estrutura do projeto

```text
.
├── main.py                  # ponto de entrada (python main.py)
├── src/
│   ├── config.py            # leitura e validação do .env
│   ├── errors.py            # exceções e códigos de saída
│   ├── api_client.py        # cliente HTTP somente GET, paginação e limite de requisições
│   ├── transformer.py       # classificação PF/PJ, datas, endereço, consultor, status, ordenação
│   ├── cadastral.py         # tabela documento × situação e auditoria cadastral por ID
│   ├── reconciliation.py    # patrimônio por carteira e auditoria exata
│   ├── currency.py          # moedas das carteiras e bloqueio de mistura de moedas
│   ├── ptax.py              # cotação PTAX (Banco Central)
│   ├── estimate.py          # estimativa por cliente e patrimônio zero (sem dependência de Excel)
│   ├── stability.py         # estabilidade do consolidado durante a coleta
│   ├── duplicates.py        # possível duplicidade entre cadastros
│   ├── exporter.py          # planilha de aniversariantes (gravação atômica)
│   ├── validator.py         # validação de quantidades e do Excel gerado
│   ├── diagnostic.py        # planilha de diagnóstico local
│   ├── summary.py           # resumo exibido no terminal
│   └── pipeline.py          # orquestração da execução
├── tests/                   # testes automatizados (dados fictícios e mocks)
├── docs/                    # arquitetura, dicionário de dados, decisões e checklist de publicação
├── .github/workflows/ci.yml # CI: Ruff, pytest e verificação de segredos
├── .env.example             # modelo de configuração, sem credenciais
├── .pre-commit-config.yaml  # verificações antes de cada commit
├── .secrets.baseline        # baseline do detect-secrets
├── pyproject.toml           # metadados e configuração de Ruff e pytest
├── requirements.txt         # dependências de execução
├── requirements-dev.txt     # dependências de desenvolvimento
├── LICENSE                  # licença MIT
├── SECURITY.md · CONTRIBUTING.md · CHANGELOG.md
├── output/                  # planilhas e diagnósticos gerados (fora do Git)
└── logs/                    # logs de execução (fora do Git)
```

## Solução de problemas

| Sintoma | Causa provável / ação |
|---|---|
| `CARTEIRA_ADVISOR_TOKEN não configurado` (código 2) | Preencha o token no `.env`. |
| `acesso negado (HTTP 401)` | Token inválido ou expirado. Solicite um novo ao atendimento. |
| "Documentos sem classificação segura, fora da planilha" | `cpfCnpj` ausente ou inválido. Corrija no Carteira Advisor ou, se for um cadastro sem CPF por natureza, inclua o ID em `CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS`. |
| "Status ativo/inativo pendente" | O campo `active` veio ausente ou não booleano. O cliente fica fora da planilha. |
| `AUDITORIA CADASTRAL: DIVERGENTE` | Há documento ou status pendente, ou os IDs ativos não coincidem com o consolidado. O resumo lista os clientes. Não impede a planilha. |
| "Consolidado indisponível" | O endpoint de patrimônio falhou. A planilha é gerada; só as auditorias ficam sem verificação. |
| `HTTP 429` | Limite de requisições. O programa espera e tenta de novo. Se persistir, reduza `CARTEIRA_ADVISOR_MAX_REQUESTS_PER_MINUTE`. |
| `Patrimônio divergente na linha N do Excel` (código 6) | O valor gravado não confere com a origem; nada é publicado. Verifique o log. |
| "Não executada: PTAX: …" | O serviço da PTAX falhou. Nenhuma cotação é inventada; o Excel é gerado normalmente. |
| `Arquivo de destino já existe` | Duas execuções no mesmo segundo. Execute novamente. |
