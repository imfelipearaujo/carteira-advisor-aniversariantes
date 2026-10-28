# Dicionário de dados

Os tipos abaixo foram observados em produção (28/10/2026). Por política de
dados sensíveis, este documento descreve formatos e regras, nunca dados ou
estatísticas da base real de clientes.

## `GET /company-api/clients`

Resposta observada: **lista JSON simples, sem paginação**. Os parâmetros
`page`/`size` são ignorados.

| Campo | Tipo observado | Uso | Tratamento |
|---|---|---|---|
| `id` | int | Chave do cliente, ordenação final e auditoria | Obrigatório e único; ausência ou duplicidade interrompe a coleta. |
| `cpfCnpj` | str \| null | **Filtro PF/PJ** | Observado: somente dígitos (11 ou 14), sem pontuação, ou nulo. Se o `id` está em `CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS` → registro especial (o documento não é avaliado). Senão: CPF válido → PF; CNPJ válido → excluído; demais → pendente (fora da planilha, IDs no resumo). Nunca é exportado nem registrado em log. |
| `name` | str | **Nome completo** | Espaços removidos. Vazio → `PENDÊNCIA: NOME`. |
| `birthdate` | str \| null | **Data de nascimento** | Exige `YYYY-MM-DD`, data real e não futura. Caso contrário → `PENDENTE` + `PENDÊNCIA: DATA DE NASCIMENTO`. |
| `consultantId` | int | Relacionamento com consultor | Comparado de forma exata com `consultants[].id` (texto normalizado: `42` = `"42"`). |
| `consultantName` | str | Apenas diagnóstico em log | Nunca substitui o relacionamento por ID. |
| `street`, `number`, `complement`, `district`, `city`, `state`, `country` | str | **Endereço completo** | Componentes vazios são ignorados. |
| `postalCode` | str | **Endereço completo** | 8 dígitos → `CEP 00000-000`. Outros formatos são mantidos como vieram. |
| `currentValue` | number (até 10 casas) | **Patrimônio (provisório)** | Valor numérico usado como veio. Zero → R$ 0,00. Ausente/inválido → célula vazia + `PENDÊNCIA: PATRIMÔNIO`. Não auditado. Na auditoria financeira, serve só de comparação em log. |
| `active` | bool | **Filtro da planilha** e auditoria cadastral | Observado: apenas `true`/`false`. Só `true` (booleano) exporta; `false` exclui; ausente/não booleano → status pendente (excluído, ID no resumo). |
| `deceased`, `visualizationOnly` | bool \| null | — | Não filtram clientes. |

A API também retorna outros campos (por exemplo `phone`, `email`, `income`) que
**não** são lidos nem exportados.

## `GET /company-api/consultants`

Resposta observada: lista JSON simples, sem paginação.

| Campo | Tipo | Uso |
|---|---|---|
| `id` | int | Chave do relacionamento. Obrigatório e único. |
| `name` | str | **Consultor responsável**. Vazio → `PENDÊNCIA: CONSULTOR`. |
| `email`, `permission`, `code` | str/null | Não utilizados. |

## `GET {PATRIMONY_BASE_URL}/company-api/wallet/consolidated-pl`

URL base: `…/v1/trade-rules`. Resposta paginada (`page` base 0, `size`
respeitado).

| Campo | Tipo observado | Uso |
|---|---|---|
| `totalCurrentValue` | number (2 casas) | `PATRIMONIO_EMPRESA`. Repete-se em cada página; é lido uma vez, nunca somado, e precisa ser idêntico em todas as páginas. |
| `clients.totalPages`, `totalElements`, `number`, `numberOfElements`, `last` | int/bool | Validação de paginação. |
| `clients.content[].clientId` | int | Chave; precisa ser única. |
| `clients.content[].clientName` | str | Não utilizado. |
| `clients.content[].wallets[].walletInfoId` | int | Detecção de carteira repetida. |
| `clients.content[].wallets[].currentValue` | number | Base do patrimônio individual, **na moeda original da carteira** (não convertido; ver `walletCoin`). |
| `wallets[].investedValue` | number | Não entra no cálculo do patrimônio. Usado na verificação temporal e como segunda evidência de duplicidade (junto com `currentValue`). |
| `wallets[].name`, `profit` | str / number | Não utilizados. O nome **não** é critério de exclusão nem de duplicidade. |
| `clients.pageable`, `first`, `sort`, `size`, `empty` | object/bool/int | Presentes na resposta; não utilizados. |

## `GET {PATRIMONY_BASE_URL}/company-api/wallet/{id}`

URL base: `…/v1/trade-rules`. Sob `/v1/user`, retorna 404. `{id}` é o
`walletInfoId` de consolidated-pl, e a resposta traz o mesmo `id`. Usado apenas
na auditoria multimoeda.

| Campo | Tipo observado | Valores observados | Uso |
|---|---|---|---|
| `id` | int | = `walletInfoId` | Conferência da resposta. |
| `walletCoin` | str | `REAIS`, `DOLLAR` | Moeda original de `currentValue`. Ausente/vazio → `SEM_MOEDA`. |
| `lastConsolidation` | str (ISO 8601, sem fuso) \| null | data/hora ou nulo (nulo observado só em carteiras com valor zero) | Defasagem: nulo ou mais de 1 dia antes da execução → "sem consolidação recente". |
| `hasConsolidationErrors` | bool \| null | `false` ou `null` (nenhum `true` observado) | `true` → erro; `null` → status não informado. |
| `principal` | bool | `true` ou `false` | Apenas informativo. **Não** identifica carteira consolidada/agregadora. |
| `description` | str | nome da carteira (ex.: instituição) | Não utilizado em log nem em cálculo. |
| `userId`, `considerDividends`, `cutDate`, taxas e perfis | diversos | maioria nula | Não utilizados. |

Nenhum campo de cotação, taxa de câmbio ou valor convertido foi encontrado nessa
resposta.

## PTAX — `GET {PTAX_BASE_URL}/odata/CotacaoDolarPeriodo(...)` (Banco Central)

Referência externa e independente, **não** a fonte de câmbio do Carteira Advisor.
Parâmetros: `@dataInicial='MM-DD-AAAA'`, `@dataFinalCotacao='MM-DD-AAAA'`,
`$format=json`.

| Campo | Tipo | Uso |
|---|---|---|
| `value[].cotacaoVenda` | number (5 casas) | Taxa USD/BRL aplicada (venda). Deve ser > 0. |
| `value[].dataHoraCotacao` | str `AAAA-MM-DD HH:MM:SS.ffffff` (horário de Brasília) | Instante de publicação (cerca de 13h). Só vale se for ≤ `lastConsolidation` da carteira. |
| `value[].cotacaoCompra` | number | Não utilizado. |

Fins de semana e feriados não têm boletim (lista vazia). Datas inválidas
retornam HTTP 500 do serviço.

## Relatório diagnóstico local (`output/diagnostico/`)

Uma aba, `Diagnostico`, com uma linha por cliente do consolidado, ordenada pela
maior divergência: ID, nome, tipo, situação, patrimônio operacional (R$),
carteiras em REAIS (R$), carteiras em DOLLAR (US$), PTAX e data, estimado (R$),
diferença (R$ e %), faixa, observação (motivo de dados insuficientes ou limites
cruzados), **última consolidação** (a mais recente entre as carteiras do
cliente), **carteiras nunca consolidadas**, **alterado durante a coleta**
(Sim/Não) e **possível duplicidade** (cadastro e carteira correspondentes, com
nome + ID). São 18 colunas, com autofiltro e cabeçalho congelado. A gravação é
atômica e o arquivo é revalidado (cabeçalho, quantidade de linhas e conjunto de
IDs). Contém dados pessoais e não é versionado.

## Exemplos nesta documentação e nos testes

Todos os nomes, documentos, endereços, IDs e valores usados em exemplos e testes
são **fictícios**. CPF/CNPJ de exemplo: `529.982.247-25`, `11.222.333/0001-81` e o
CNPJ alfanumérico oficial de exemplo da Receita `12.ABC.345/01DE-35`. Testes geram
documentos válidos fictícios em tempo de execução (`make_cpf`, `make_cnpj`).

## Mapeamento API → Excel

Somente clientes **Pessoa Física** (CPF válido) **ativos** (`active == true`) geram linhas.

| Coluna Excel | Origem | Tipo na célula | Formato | Ausente / inválido |
|---|---|---|---|---|
| Nome completo | `clients.name` | texto | — | célula vazia + pendência NOME |
| Data de nascimento | `clients.birthdate` | **data real** | `DD/MM/YYYY` | texto `PENDENTE` |
| Patrimônio | `clients.currentValue` (**provisório**, não auditado; comentário no cabeçalho) | **número** | `"R$" #,##0.00` | célula vazia + pendência PATRIMÔNIO |
| Endereço completo | campos de endereço | texto | — | célula vazia + pendência ENDEREÇO |
| Cidade | `clients.city` (espaços removidos) | texto | — | célula vazia (sem pendência própria) |
| Estado | `clients.state` (espaços removidos) | texto | — | célula vazia (sem pendência própria) |
| Consultor responsável | `consultants[id = consultantId].name` | texto | — | célula vazia + pendência CONSULTOR |
| Status | pendências | texto | destaque amarelo-claro se houver pendência | `OK` |

## Valores monetários

**Moedas.** `wallets[].currentValue` (consolidated-pl) está na moeda da carteira
(`walletCoin`). `totalCurrentValue` e `clients.currentValue` estão em REAIS
(convertidos pela plataforma). Valores em moedas diferentes nunca são somados.

- O JSON é decodificado em `Decimal` com todos os dígitos enviados. Em produção há
  valores com até 10 casas decimais, por exemplo `…876.2536083531`.
- A unidade padrão é **reais** (`CARTEIRA_ADVISOR_MONEY_UNIT=reais`). Com
  `centavos`, os valores são divididos por 100 na exibição e no Excel.
- A auditoria compara os `Decimal` exatos, sem arredondamento.
- No Excel, o valor é gravado como número de ponto flutuante (o Excel guarda no
  máximo 15 dígitos significativos). A validação do arquivo compara nessa
  precisão. A auditoria nunca usa os valores do Excel.

## Status

Formato: `OK` ou `PENDÊNCIA: <itens separados por "; ">`, sempre na ordem
NOME → DATA DE NASCIMENTO → CONSULTOR → PATRIMÔNIO → ENDEREÇO, sem itens repetidos.

## Ordenação

1. Datas válidas primeiro.
2. Mês crescente; 3. dia crescente (o ano não influencia, mas é mantido na célula).
4. Nome completo (sem diferenciar maiúsculas/minúsculas).
5. ID (numérico antes de textual).

Datas ausentes ou inválidas ficam no final, ordenadas por nome e ID.
