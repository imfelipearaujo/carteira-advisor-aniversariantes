# Arquitetura

## Módulos e responsabilidades

| Módulo | Responsabilidade |
|---|---|
| `main.py` | Ponto de entrada. Configura logging, chama `pipeline.run`, imprime o resumo e converte exceções em códigos de saída. |
| `src/config.py` | Lê `.env`/ambiente e produz `Settings` imutável e validado. O token tem `repr=False`. |
| `src/errors.py` | Hierarquia de exceções (`ConfigurationError`, `IntegrationError` → `AuthenticationError`/`CollectionIntegrityError`, `TransformationError`, `ExportError`) e `ExitCode`. |
| `src/api_client.py` | Cliente HTTP somente `GET`, com retentativas e decodificação de números como `Decimal`. `collect_pages` percorre e valida a paginação. |
| `src/reconciliation.py` | Auditoria financeira (diagnóstico): patrimônio individual a partir das carteiras e conciliação com o total da empresa. |
| `src/transformer.py` | Classifica PF/PJ pelo `cpfCnpj` e converte clientes PF em `ReportRow`: patrimônio provisório (`currentValue`), datas, endereço, consultor por ID, pendências e ordenação. |
| `src/exporter.py` | Gera a planilha (openpyxl), cria o temporário e publica de forma atômica. |
| `src/validator.py` | Valida quantidades (clientes = linhas, mesmos IDs) e reabre o Excel para conferir estrutura, tipos e conteúdo. |
| `src/cadastral.py` | Classificação documento × situação (`active`), tabela de contingência com conferência matemática e auditoria cadastral por ID (ativos × consolidado). |
| `src/stability.py` | Compara duas leituras do consolidado (início e fim da coleta financeira): clientes, carteiras, `currentValue`/`investedValue` e total. |
| `src/duplicates.py` | Detecta possível duplicidade patrimonial entre cadastros diferentes (mesmo `walletInfoId`, ou valor atual **e** investido idênticos na mesma moeda). |
| `src/ptax.py` | Cliente somente GET da PTAX (Banco Central): consulta por período, validação da resposta e escolha da última cotação publicada até um instante. |
| `src/estimate.py` | Estimativa patrimonial por cliente em BRL (REAIS + DOLLAR × PTAX), comparação com `clients.currentValue`, faixas de divergência e classificação de PL zero. Lógica pura, sem dependência de Excel. |
| `src/diagnostic.py` | Planilha de diagnóstico local (`output/diagnostico/`), gravada de forma atômica e revalidada. |
| `src/summary.py` | Resumo exibido no terminal (apresentação); clientes como `NOME COMPLETO (ID n)`. |
| `src/currency.py` | Auditoria multimoeda (diagnóstico): detalhes das carteiras (`walletCoin`, `lastConsolidation`, `hasConsolidationErrors`), totais por moeda original, defasagem, evidência de conversão e bloqueio de somas entre moedas. |
| `src/pipeline.py` | Orquestra a sequência (coleta, classificação, planilha, auditorias) e registra os logs. |

As dependências apontam para dentro, em três camadas:

| Camada | Módulos | Depende de |
|---|---|---|
| Domínio (regras, sem I/O) | `transformer`, `cadastral`, `reconciliation`, `currency`, `estimate`, `duplicates`, `stability` | entre si, `errors`/`config` e apenas os **tipos de dados** `PtaxSeries`/`PtaxQuote` (`ptax`) e `ConsolidatedPL` (`api_client`); nenhuma chamada de rede |
| Integração (rede) | `api_client` (Carteira Advisor), `ptax` (Banco Central) | `config`, `errors` |
| Saída e orquestração | `exporter`, `validator`, `diagnostic` (Excel), `summary` (terminal), `pipeline`, `main` | as camadas acima |

As regras de negócio podem ser reaproveitadas sem Excel nem terminal (por
exemplo, incorporadas a outro sistema), e as regras críticas são testáveis sem
rede. O `pipeline` recebe o cliente da API por injeção (`client_factory`), o que
permite os testes com sessões simuladas.

## Fluxo de dados

```text
.env ─► Settings ─► CarteiraAdvisorClient (GET)
                        ├─ {BASE_URL}/company-api/clients ───────────────────────┐
                        ├─ {BASE_URL}/company-api/consultants ───────────────────┤
                        └─ {PATRIMONY_BASE_URL}/company-api/wallet/consolidated-pl┤
                                                              ▼
   clients ─► classify_clients (documento × active; totais e IDs únicos conferidos)
   clients ─► split_by_person_type ─► PF ativas ─┬─► build_rows ─► sort_rows ─► validate_row_quantities
                    │                     │                                        ▼
                    ├─► PJ (excluídas)    │   create_temp_path ─► write_workbook ─► validate_excel ─► publish
                    └─► documento pendente (IDs no resumo/log)
   (depois da publicação) todos os clientes + consolidated-pl ─► compute_patrimonies ─► reconcile
                          └─► wallet/{id} × N ─► build_breakdown ─► apply_currency_gate   (diagnóstico, não bloqueia)
   (depois da publicação) clientId do consolidado ─► audit_cadastral (A = ativos × B = consolidado)
```

## Classificação PF/PJ

`classify_document(cpfCnpj)` remove só os separadores comuns (`.`, `-`, `/`,
espaços) e decide:

| Documento | Classe | Destino |
|---|---|---|
| CPF com 11 dígitos e dígitos verificadores válidos | PF | planilha |
| CNPJ com 14 caracteres e dígitos verificadores válidos (numérico ou alfanumérico, formato da Receita Federal de jul/2026) | PJ | excluído |
| Ausente, comprimento diferente, dígitos verificadores inválidos, todos os dígitos iguais, caracteres inesperados ou valor não textual | pendente | fora da planilha; IDs no resumo e no log |

Nenhum documento pendente é classificado por aproximação. Por exemplo, 10 dígitos
podem ser um CPF que perdeu o zero à esquerda, mas o programa não presume isso.
A soma PF + PJ + pendentes é conferida contra o total de clientes.

## Situação cadastral e auditoria cadastral (`src/cadastral.py`)

`classify_status(active)` aceita **somente booleanos JSON**: `true` = ATIVO,
`false` = INATIVO. Qualquer outro valor (ausente, `null`, `"true"`, `1`) vira
STATUS PENDENTE, ou seja, nunca se presume ativo.

`classify_person` decide o tipo do cliente. Primeiro vem o **registro
especial**: o ID está em `CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS`, com comparação
textual por `id_key`. Só depois vem o documento (`classify_document`: PF, PJ ou
pendente). O tipo especial nunca é deduzido do documento ou do nome. O pipeline
registra em log (só IDs) quando um ID configurado não existe no cadastro ou
quando um registro especial tem CPF/CNPJ válido, porque isso indica configuração
desatualizada.

`classify_clients` coloca cada cliente em exatamente uma célula da tabela 4 × 3
(PF / PJ / registro especial / documento pendente × ativo / inativo / status
pendente). Antes da exportação, ela confere:

- `PF + PJ + ESPECIAIS + DOCUMENTOS_PENDENTES = TOTAL_CLIENTES`;
- `ATIVOS + INATIVOS + STATUS_PENDENTE = TOTAL_CLIENTES`;
- nenhum `id` em mais de uma célula.

Uma violação é erro de transformação (código 5) e nenhuma planilha é gerada.

**Somente PF ativas** viram linhas. O número de linhas é conferido contra a
célula PF × ATIVO.

Depois da publicação, `audit_cadastral` compara **por ID** (com a normalização
textual de `id_key`; `42` = `"42"`):

- A = clientes com `active == true`;
- B = `clientId` do consolidado (todas as páginas);
- reporta A∩B, A − B, B − A (inclusive IDs que nem estão cadastrados), `clientId`
  repetido em B e a célula de cada cadastrado fora de B.

| Condição | Resultado |
|---|---|
| Consolidado indisponível | NÃO VERIFICADA |
| Documento pendente, status pendente, A ≠ B ou B com repetição | DIVERGENTE (com IDs) |
| Registro especial configurado | classificado; **não** bloqueia (continua em A/B normalmente) |
| Nada pendente e A = B exatamente | APROVADA |

Contagens iguais não bastam: 3 ativos e 3 registros com IDs diferentes resultam
em DIVERGENTE. A auditoria cadastral não bloqueia a planilha.

## Patrimônio provisório (planilha)

A coluna Patrimônio usa `clients.currentValue` diretamente (`to_decimal`, sem
arredondamento):

- número válido → valor (zero aparece como R$ 0,00);
- ausente ou inválido (texto, booleano, float, `null`) → célula vazia + `PENDÊNCIA: PATRIMÔNIO`.

Esse valor **não** é auditado nem conciliado. Ele nunca é substituído pela soma
das carteiras, e a auditoria nunca usa esse valor. O cabeçalho da coluna leva um
comentário (`PROVISIONAL_NOTE`) e as propriedades do arquivo repetem o aviso. O
validador do Excel exige esse aviso.

## Integração com os endpoints

- **Duas URLs base** (confirmadas em produção em 28/10/2026):

  | Recurso | URL base | Caminho |
  |---|---|---|
  | Clientes | `CARTEIRA_ADVISOR_BASE_URL` (`…/v1/user`) | `/company-api/clients` |
  | Consultores | `CARTEIRA_ADVISOR_BASE_URL` (`…/v1/user`) | `/company-api/consultants` |
  | Patrimônio | `CARTEIRA_ADVISOR_PATRIMONY_BASE_URL` (`…/v1/trade-rules`) | `/company-api/wallet/consolidated-pl` |

  O mesmo caminho de patrimônio sob `/v1/user` responde 404.
- **Autenticação:** header `token: <valor>`. Confirmado em produção em 28/10/2026:
  `Authorization: Bearer` responde 401 "Token não informado!". Header e prefixo
  são configuráveis.
- **Somente GET:** o cliente não expõe nenhum outro verbo HTTP. Os testes usam uma
  sessão falsa que falha se qualquer outro verbo for chamado.
- **Retentativas:** HTTP 429/500/502/503/504 e falhas de rede/timeout são
  repetidos até `MAX_RETRIES` vezes, com backoff exponencial limitado a 10 s. As
  respostas 401/403 viram `AuthenticationError` imediatamente, e os demais
  códigos viram `IntegrationError`.
- **Precisão:** o JSON é decodificado com `parse_float=Decimal`, então os valores
  monetários preservam todos os dígitos enviados pela API.

## Estratégia de paginação

A função `collect_pages` aceita dois formatos:

1. **Lista JSON simples** na página 0: coleção sem paginação. É o comportamento
   observado em produção para clientes e consultores, que ignoram `page`/`size`.
2. **Objeto de página (estilo Spring)** com `content`, `totalPages`,
   `totalElements` e, opcionalmente, `number`, `numberOfElements` e `last`. É o
   formato do patrimônio em produção: `?page=0&size=N`, base 0, `size`
   respeitado (77 registros = 4 páginas de 20 ou 1 página de 100). Os campos
   `pageable`, `first`, `sort`, `size` e `empty` também vêm na resposta, mas não
   são usados.

`totalCurrentValue` se repete em todas as páginas do patrimônio. Ele é lido uma
única vez (da primeira página), **nunca somado**, e precisa ser idêntico em todas
as páginas.

No formato paginado, a coleta é **interrompida** (`CollectionIntegrityError`) quando:

- `content` está ausente ou `totalPages`/`totalElements` estão ausentes ou inválidos;
- `totalPages` ou `totalElements` mudam entre páginas;
- um valor de referência (`totalCurrentValue`) muda entre páginas;
- `number` difere da página solicitada;
- `numberOfElements` difere da quantidade efetiva em `content`;
- `last` contradiz a posição da página;
- uma página intermediária vem vazia;
- a quantidade coletada difere de `totalElements`;
- um ID está ausente ou repetido (isso também detecta servidores que ignoram o
  parâmetro de página e repetem a página 0);
- o limite de segurança de páginas (`max_pages`) é atingido.

## Regras de consolidação patrimonial

O cálculo fica em `compute_client_patrimony`. Um valor só é considerado
**auditado** (`VERIFIED`) quando não pode conter dupla contagem:

| Situação do cliente | Resultado |
|---|---|
| Ausente no endpoint de patrimônio | `MISSING` (não se presume zero; IDs listados no resumo e no log) |
| `wallets` ausente ou com `currentValue` não numérico | `INVALID_DATA` |
| `walletInfoId` repetido | `OVERLAP_SUSPECTED` |
| 0 carteiras | `VERIFIED`, valor 0 |
| 1 carteira | `VERIFIED`, valor da carteira (não há como sobrepor) |
| ≥ 2 carteiras e alguma carteira não nula igual à soma de um subconjunto das demais | `OVERLAP_SUSPECTED` (ex.: A 1.000 + B 2.000 = Consolidada 3.000) |
| ≥ 2 carteiras com política `unconfirmed` (padrão) | `AMBIGUOUS` |
| ≥ 2 carteiras com política `mutually_exclusive` e sem assinatura de sobreposição | `VERIFIED`, soma das carteiras |
| > 20 carteiras | `AMBIGUOUS` (verificação exaustiva inviável) |

O cálculo **não**:

- exclui carteiras pelo nome (por exemplo, "consolidada");
- presume que uma carteira consolidada contém todas as demais;
- usa a maior carteira como total;
- substitui o valor por `clients.currentValue`, que é usado só como diagnóstico em log.

Clientes sem valor auditado ficam com a célula de patrimônio **vazia** e com
`PENDÊNCIA: PATRIMÔNIO`.

## Auditoria patrimonial (diagnóstico financeiro, não bloqueante)

Roda sobre **todos** os clientes cadastrados (PF, PJ e pendentes), porque
`totalCurrentValue` representa a empresa inteira. Uma falha no endpoint de
patrimônio (`IntegrationError`) é registrada, vira NÃO VERIFICADA e a planilha é
publicada mesmo assim. Nenhum resultado da auditoria altera a planilha nem o
código de saída. A planilha também nunca é apresentada como auditada.

`reconcile(company_total, patrimonies, orphans)`:

- `PATRIMONIO_CLIENTES` = soma dos valores `VERIFIED`;
- `PATRIMONIO_EMPRESA` = `totalCurrentValue`;
- `DIFERENCA` = `PATRIMONIO_EMPRESA − PATRIMONIO_CLIENTES`, em `Decimal`, comparada com zero **sem tolerância**.

| Condição | Resultado |
|---|---|
| `totalCurrentValue` ausente/inválido | NÃO VERIFICADA |
| Algum cliente sem valor auditado | NÃO VERIFICADA (diferença não calculável) |
| Diferença ≠ 0 | DIVERGÊNCIA (diferença exibida, positiva ou negativa) |
| Diferença = 0, mas há `clientId` de patrimônio sem cliente | NÃO VERIFICADA |
| Diferença = 0, sem pendências acima | **APROVADA** |

### Bloqueio multimoeda (`src/currency.py`)

`consolidated-pl` devolve `wallets[].currentValue` **na moeda original da
carteira**, enquanto `totalCurrentValue` está em REAIS (ver DECISIONS, D16). Por
isso, depois de `reconcile`, a auditoria consulta
`GET {PATRIMONY_BASE_URL}/company-api/wallet/{walletInfoId}` para cada carteira:

- **Controle de taxa:** pausa de `CARTEIRA_ADVISOR_WALLET_DETAIL_INTERVAL_MS`
  (padrão 1000 ms) entre consultas, uma consulta por ID distinto.
- **HTTP 429:** respeita `Retry-After` numérico (limitado a 60 s); sem o header,
  usa backoff exponencial de 1, 2, 4… s (máximo 10 s), com no máximo
  `MAX_RETRIES` novas tentativas.
- O `id` da resposta precisa ser igual ao `walletInfoId` solicitado.

`build_breakdown` agrupa os valores **por moeda**, nunca entre moedas. Ele também
registra: carteiras sem moeda (`SEM_MOEDA`), códigos não reconhecidos (fora de
`REAIS`/`DOLLAR`), detalhes ausentes, `hasConsolidationErrors` verdadeiro ou não
informado e consolidação ausente ou com mais de 1 dia (`STALE_AFTER`).

`apply_currency_gate` rebaixa a auditoria para **NÃO VERIFICADA** e descarta a
diferença sempre que houver qualquer um destes itens: moeda diferente de REAIS
(não existe cotação oficial disponível), moeda desconhecida, detalhe ausente, erro
ou status de consolidação desconhecido, ou consolidação defasada. Com a consulta
desativada ou falhando, o resultado também é NÃO VERIFICADA. Assim, uma soma que
"fecha" misturando moedas nunca é aprovada.

Diagnósticos exibidos, nunca usados para aprovar:
- **taxa implícita** `(totalCurrentValue − REAIS) / moeda estrangeira`, quando há
  uma única moeda estrangeira. Ela é derivada do próprio total, portanto circular;
- **razão `clients.currentValue / carteira estrangeira`** por cliente, que é
  evidência de que o cadastro já está convertido para REAIS.

No resumo, "Total auditado" aparece como `n/d (moedas)` quando há moedas
misturadas ou não verificadas. A soma bruta das carteiras só aparece quando
todas estão em REAIS.

A auditoria inteira roda **após** a publicação da planilha, dentro de
`_safe_financial_audit`. Qualquer exceção ali vira NÃO VERIFICADA, sem afetar o
arquivo nem o código de saída.

## Modos de execução e consumo da API

`CARTEIRA_ADVISOR_DETAILED_AUDIT` escolhe o que roda **depois** da publicação da
planilha (antes dela, o fluxo é idêntico nos dois modos: clientes, consultores,
classificação, Excel):

| Etapa pós-publicação | Rápido (`false`, padrão) | Completo (`true`) |
|---|---|---|
| Leitura do consolidado (`size=100`) | 1 (`_fast_patrimony_check`) | 1 (início da auditoria) |
| Auditoria cadastral (A × B por ID) | sim | sim |
| Detalhes `wallet/{id}` | não | 1 por carteira |
| Auditoria exata + bloqueio de moedas | não (NÃO VERIFICADA, motivo informado) | sim |
| Segunda leitura do consolidado (temporal) | não | 1 |
| Duplicidades, PTAX, diagnóstico local | não | sim |
| PL zero | listado como "não avaliado" | classificado pela consolidação |

**Limitador de requisições:** `CarteiraAdvisorClient._throttle` espaça todos os
GET (inclusive novas tentativas) em `60 / CARTEIRA_ADVISOR_MAX_REQUESTS_PER_MINUTE`
segundos (1,2 s com o limite de 50/min do fornecedor), garantindo no máximo 50
chamadas em qualquer janela de 60 s. `request_count` contabiliza as chamadas e o
resumo mostra o total. A PTAX é um serviço público separado (1 chamada) e não
entra nessa conta.

**Reaproveitamento sem cache persistente:** na mesma execução, o consolidado e
os detalhes coletados servem à auditoria exata, à cadastral, ao PL zero, às
duplicidades, à PTAX e ao diagnóstico. Nada é guardado entre execuções.

## Verificação temporal (`src/stability.py`)

O patrimônio muda enquanto o programa roda. A auditoria financeira lê o
consolidado completo (todas as páginas), coleta os detalhes das carteiras (cerca
de 2 a 3 minutos) e **lê o consolidado completo mais uma vez**.
`compare_snapshots` compara:

- IDs de clientes incluídos ou removidos;
- pares (cliente, carteira) incluídos ou removidos;
- `currentValue` **ou** `investedValue` alterados em cada carteira;
- `totalCurrentValue`.

O resultado só é **ESTÁVEL** se nada mudou. Um total igual não basta, porque
mudanças podem se compensar. Se algo mudou, o resumo informa "DADOS ATUALIZADOS
DURANTE A EXECUÇÃO" e lista os clientes afetados. O diagnóstico marca "Alterado
durante a coleta". Não há repetição automática da coleta. Se a segunda leitura
falhar, o resultado é NÃO VERIFICADA. Em nenhum caso o Excel operacional é
afetado, porque ele já foi publicado antes.

## Duplicidade patrimonial entre cadastros (`src/duplicates.py`)

`find_cross_client_duplicates` compara as carteiras de **clientes diferentes**:

| Evidência | Critério |
|---|---|
| `SAME_WALLET` | o mesmo `walletInfoId` aparece em dois clientes |
| `IDENTICAL_VALUES` | mesma moeda e `currentValue` **e** `investedValue` idênticos e ≠ 0 |

Nomes parecidos ou um único valor coincidente **não** são evidência. O status é
sempre **POSSÍVEL DUPLICIDADE — CONFERÊNCIA MANUAL**: a API não diz se o cadastro
familiar deve existir, então a decisão é do negócio. O resumo mostra, para cada
registro especial, os cadastros envolvidos (nome + ID), o valor potencialmente
contado duas vezes e o peso desse valor no total oficial. **Nada é subtraído de
nenhum total.**

## Patrimônio zero (`classify_zero_patrimony`)

Carteiras zeradas ou nunca consolidadas podem ser **intencionais** (mantidas sem
vínculo pelo usuário). A lista do resumo é informativa, não aponta erro.

Para cada PF ativa com `clients.currentValue` exatamente 0:

| Categoria | Condição |
|---|---|
| PL zero com carteira consolidada | carteiras com valor 0 e todas com `lastConsolidation` |
| PL zero sem consolidação | carteiras com valor 0 e alguma sem `lastConsolidation` |
| PL zero com dados insuficientes | sem registro ou sem carteiras no consolidado, detalhe ausente, valor inválido ou erro de consolidação |
| PL zero no cadastro, carteiras com valor | o cadastro diz 0, mas as carteiras somam ≠ 0 |

A classificação descreve o que a API mostra. **Não** declara falha de integração.

## Apresentação de clientes

O resumo do terminal identifica clientes sempre como `NOME COMPLETO (ID n)` (ou
`ID n (não cadastrado)` para IDs que só existem no consolidado). Os logs em
arquivo continuam sem nomes.

## Verificação independente com PTAX (`src/ptax.py`, `src/estimate.py`)

Objetivo: estimar a **dimensão** do patrimônio de cada cliente com uma cotação
pública, para apoio comercial. Não é reprodução da metodologia do Carteira
Advisor e não aprova a auditoria exata, que continua NÃO VERIFICADA.

1. **Data-base, sem presumir D-1:** para cada carteira em DOLLAR com valor, usa
   o instante `lastConsolidation` dela. A PTAX aplicada é a **última cotação de
   venda publicada até esse instante** (`PtaxSeries.latest_until`), ou seja,
   nunca uma cotação futura. Fins de semana e feriados não têm boletim, então
   vale o anterior. Se a PTAX mais recente for mais antiga que
   `CARTEIRA_ADVISOR_PTAX_WINDOW_DAYS`, o cliente fica "sem dados suficientes".
2. **Consulta:** um único `GET CotacaoDolarPeriodo` cobre a janela
   [consolidação mais antiga − janela, consolidação mais recente]. Datas no
   formato `MM-DD-AAAA`. HTTP 429/5xx e falhas de rede têm novas tentativas
   limitadas. Uma resposta inválida (sem `value`, cotação não numérica ou ≤ 0,
   data ilegível ou fora do período) gera `PtaxError`. **Nenhuma cotação é
   inventada.**
3. **Estimativa por cliente:** `PL_ESTIMADO_BRL = Σ REAIS + Σ (DOLLAR × PTAX)`,
   em `Decimal`. Só é calculada com dados completos: todas as carteiras com
   valor válido, detalhe, moeda REAIS/DOLLAR, sem erro de consolidação, status
   informado (para valores ≠ 0), consolidação e PTAX para dólar, sem carteira
   repetida e **sem assinatura de sobreposição** (`has_subset_overlap`).
   Carteiras com valor zero não precisam de cotação.
4. **Comparação:** diferença = estimado − `clients.currentValue`; percentual em
   relação ao operacional, quando ele é ≠ 0. Faixas descritivas: `< 1%`,
   `1% a 5%`, `5% a 10%`, `10% a 25%`, `≥ 25%`, "indefinida (operacional zero)",
   "sem dados suficientes". "Relevante" significa |diferença| ≥
   `CARTEIRA_ADVISOR_FX_RELEVANT_PCT` (ou operacional zero com estimado ≠ 0).
   **Nenhuma faixa significa aprovação.**
5. **Global:** Σ estimados × `totalCurrentValue`, com diferença absoluta e
   percentual. O total é marcado como parcial se houver cliente sem dados.
6. **Isolamento:** roda depois da publicação, dentro de `_safe_fx_check`. Falha
   da PTAX ou erro interno viram "Não executada: …", sem afetar o Excel nem o
   código de saída. Os logs registram só contagens e IDs.

## Critérios que impedem a publicação da planilha

| Situação | Arquivo | Código |
|---|---|---|
| Token ausente / configuração inválida | nenhum | 2 |
| Falha HTTP, autenticação ou coleta incompleta de **clientes ou consultores** | nenhum | 3 |
| Tabela de classificação não fecha (linhas, colunas ou ID repetido), ou linhas ≠ PF ativas | nenhum | 5 |
| Falha ao gravar/validar/publicar o Excel | nenhum (temporário removido) | 6 |
| Falha no endpoint de patrimônio ou nos detalhes das carteiras (inclusive 429 persistente) | planilha publicada; auditoria NÃO VERIFICADA | 0 |
| Auditoria financeira APROVADA, DIVERGÊNCIA ou NÃO VERIFICADA | planilha publicada | 0 |
| Auditoria cadastral APROVADA, DIVERGENTE ou NÃO VERIFICADA | planilha publicada | 0 |
| PTAX indisponível/inválida ou falha na verificação independente | planilha publicada; verificação "Não executada" | 0 |

A publicação é atômica. O Excel é gravado em `output/.tmp_aniversariantes_*.xlsx`,
reaberto e validado (aba única, cabeçalho, filtro, painel congelado, quantidade de
linhas, nome/status/endereço/consultor por linha, datas como data real, patrimônio
numérico igual à origem na precisão do Excel, aviso de patrimônio provisório) e só
então movido com `os.replace`. Em caso de falha, o temporário é
removido. O programa nunca sobrescreve um arquivo existente.

## Logging

- Arquivo: `logs/carteira_advisor_<timestamp>.log` (INFO+). Console: WARNING+.
- As linhas por cliente (patrimônio não auditado, consultor não relacionado,
  `currentValue` divergente) ficam em INFO, só no arquivo. As contagens agregadas
  ficam em WARNING e aparecem no console.
- Os logs registram apenas IDs de clientes, contagens e motivos. Não registram
  nomes, endereços, valores individuais ou o token.
- O logger `urllib3` fica em WARNING para evitar URLs completas em nível DEBUG.
