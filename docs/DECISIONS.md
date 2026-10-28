# Decisões técnicas, premissas e limitações

> **Política de dados sensíveis:** este documento é versionado. Ele registra
> métodos, regras e conclusões técnicas, mas **nunca** nomes, documentos, IDs
> de clientes, valores patrimoniais, totais da empresa ou estatísticas da base
> real. Evidências concretas de uma execução ficam apenas no resumo do terminal
> e no diagnóstico local (`output/`, fora do Git). Ver D27.

## Investigação inicial da API (28/10/2026)

Não foi encontrada documentação pública (Swagger/OpenAPI) da API. Os caminhos
`/v1/user/swagger-ui/...` e `/v1/user/v3/api-docs` retornam 404, e
`/v1/user/company-api/v3/api-docs` retorna 403. Foram feitas apenas requisições
`GET` de leitura, inspecionadas de forma agregada.

| Item | Observação |
|---|---|
| Header de autenticação | `token: <valor>` → 200. `Authorization: Bearer`, `Authorization: <valor>`, `x-api-key`, `x-access-token`, `api-key`, `x-token` → 401 "Token não informado!". |
| `/company-api/clients` | Lista JSON simples, IDs únicos. `page`/`size` são ignorados. |
| `/company-api/consultants` | Lista JSON simples, IDs únicos. |
| `/v1/user/company-api/wallet/consolidated-pl` | HTTP 404. O endpoint correto fica sob **`/v1/trade-rules`**. |
| `/v1/trade-rules/company-api/wallet/consolidated-pl` | Objeto de página Spring. `page` (base 0) e `size` são respeitados. |
| Valores monetários | Números com até 10 casas decimais (não são centavos inteiros). |
| Datas de nascimento | Formato `YYYY-MM-DD` ou nulo. |

## Decisões

### D1. Header de autenticação configurável, com padrão `token`
O header de autenticação não está documentado publicamente. Ele foi confirmado
empiricamente (único formato aceito) e virou o padrão. Ele continua configurável (`CARTEIRA_ADVISOR_AUTH_HEADER`/`_SCHEME`).

### D2. Duas URLs base: `/v1/user` e `/v1/trade-rules`
O patrimônio fica em outro serviço (`/v1/trade-rules`), separado de clientes e
consultores (`/v1/user`). As duas bases são configuráveis
(`CARTEIRA_ADVISOR_BASE_URL` e `CARTEIRA_ADVISOR_PATRIMONY_BASE_URL`), e o mesmo
token vale para as duas. Uma falha no endpoint de patrimônio **não** bloqueia a
planilha (D14): só as auditorias ficam NÃO VERIFICADAS.

### D3. Política de carteiras conservadora por padrão (`unconfirmed`)
A API não informa se `wallets[]` é mutuamente exclusivo ou se inclui carteiras
agregadoras. Por isso:
- 0 ou 1 carteira: valor auditado (não há sobreposição possível);
- 2 ou mais carteiras: `AMBIGUOUS`. O patrimônio fica pendente e a auditoria
  exata fica NÃO VERIFICADA.

A política `mutually_exclusive` existe para quando houver **confirmação formal**
de que não há registros agregadores. Mesmo nela, uma carteira cujo valor seja
igual à soma de outras do mesmo cliente bloqueia o valor (`OVERLAP_SUSPECTED`).
**Não altere a política só para "fazer a auditoria passar".**

### D4. Comparação exata em `Decimal`, sem tolerância
O JSON é decodificado com `parse_float=Decimal`, e a diferença da auditoria exata
precisa ser zero na precisão recebida. Nenhuma tolerância arbitrária foi adotada.

### D5. ~~Arquivo "NÃO VALIDADO"~~ (substituída por D14)
A pasta `output/nao_validados/` não é mais usada.

### D6. Cliente ausente no endpoint de patrimônio não vale zero
A ausência não é interpretada como "sem carteiras". O cliente recebe pendência na
auditoria e `clientId` de patrimônio sem cliente correspondente impede a
aprovação.

### D7. Paginação defensiva e compatível com listas simples
O cliente aceita tanto lista simples quanto objeto de página e valida todos os
campos de paginação quando eles existem.

### D8. `pandas` não foi adotado
A geração e a validação do Excel exigem controle por célula, que o `openpyxl` já
oferece. Pode ser revisto se surgir necessidade de análise tabular.

### D9. Datas
Somente `YYYY-MM-DD`, data real e não futura. Nenhum outro formato é convertido.

### D10. Estrutura de diretórios
A raiz do repositório cumpre o papel de `carteira_advisor_aniversarios/`.
`src/errors.py` e `src/pipeline.py` separam exceções e orquestração do `main.py`.

### D12. Composição das carteiras (investigação de 28/10/2026)
Conclusões, obtidas só com GET e análise agregada:
1. Não havia assinatura aritmética de sobreposição entre carteiras do mesmo
   cliente, nem `walletInfoId` repetido. Isso **não prova** exclusividade, por isso
   a política continua `unconfirmed`.
2. A soma bruta das carteiras não fechava com `totalCurrentValue`. A causa foi
   explicada em D16 (moedas).
3. `totalCurrentValue` varia ao longo do dia (precificação intradiária).
4. `clients.currentValue` não coincide com a soma das carteiras em muitos
   clientes. Ele é usado como fonte operacional provisória (D14), nunca como
   conciliação.

### D13. Diagnóstico de soma bruta no resumo
Quando a auditoria exata não é aprovada e todas as carteiras estão em REAIS, o
resumo mostra a soma bruta das carteiras, rotulada "NÃO auditada".

### D14. Planilha operacional independente da auditoria
- Somente PF (CPF com dígitos verificadores válidos); documentos sem
  classificação segura ficam fora e são listados no resumo.
- Patrimônio exibido = `clients.currentValue`, marcado como **provisório**.
- A planilha é publicada **sem depender** da auditoria financeira. O código de
  saída é 0 quando a planilha é publicada.
- Falhas de autenticação, coleta cadastral, integridade de quantidades e
  exportação continuam bloqueando.

### D15. Comparação do Excel por precisão relativa
O validador compara as células monetárias com `math.isclose(rel_tol=1e-15)`, a
precisão do próprio Excel. Isso não é uma tolerância financeira.

### D16. Moedas: origem da divergência patrimonial
Comprovado com consultas a `GET /company-api/wallet/{id}` (sob `/v1/trade-rules`;
`{id}` = `walletInfoId`):
1. `wallets[].currentValue` vem **na moeda original** da carteira (`walletCoin`:
   `REAIS` ou `DOLLAR`).
2. `totalCurrentValue` está em BRL e converte o dólar com uma taxa que **não é
   fornecida** pela API. A taxa implícita, deduzida do próprio total, explicava a
   divergência quase inteira: era a parcela em dólar somada como se fosse real.
3. `clients.currentValue` também está em BRL, mas com razões de conversão
   diferentes por cliente (data-base ou consolidação diferentes).
4. Não há endpoint de cotação conhecido; o Swagger responde 403 a este token.

Decisão: a conciliação multimoeda **não** é aprovável sem a cotação oficial do
fornecedor. A auditoria exata permanece **NÃO VERIFICADA**, e a taxa implícita
aparece só como pista.

### D17. Bloqueio de mistura de moedas
`apply_currency_gate` mantém NÃO VERIFICADA e descarta a diferença sempre que há
moeda diferente de REAIS, moeda desconhecida, detalhe ausente, erro ou status de
consolidação desconhecido, ou consolidação defasada.

### D18. Planilha somente com PF ativas
Só entram clientes com CPF válido **e** `active == true` (booleano). `active`
ausente ou não booleano não é presumido ativo.

### D19. Auditoria cadastral por ID
A tabela documento × situação é conferida matematicamente (linhas, colunas e IDs
únicos) e os IDs ativos são comparados um a um com os `clientId` do consolidado.
Contagens iguais não bastam. Documento ou status pendente impede a aprovação.

### D20. Registros especiais por configuração
Cadastros sem CPF individual por natureza (por exemplo, cadastro familiar ou
carteira de simulação) são declarados **explicitamente por ID** em
`CARTEIRA_ADVISOR_SPECIAL_CLIENT_IDS`, **somente no `.env` local**. Não há
heurística por nome. Esses registros ficam fora da planilha, ocupam linha
própria na tabela cadastral e não bloqueiam a auditoria. Sem configuração,
voltam a ser documento pendente, o que é seguro.

### D21. Verificação independente com PTAX
A PTAX de venda do Banco Central é a **referência independente** para estimar o
patrimônio por cliente (`REAIS + DOLLAR × PTAX`). Ela **não** é tratada como a
cotação oficial do Carteira Advisor. A cotação de cada carteira é a última PTAX
publicada até a `lastConsolidation` dela, sem presumir D-1. Na investigação, a
estimativa ficou próxima do total oficial (diferença bem abaixo de 1%), e a
diferença global se explicou pela taxa cambial. As divergências individuais
vinham de diferenças entre o cadastro e as carteiras, não do câmbio.

### D22. Planilha com oito colunas (Cidade e Estado)
Cidade e Estado vêm de `clients.city` e `clients.state` sem inferência e
permanecem também no endereço completo. O validador confere as duas colunas e
exige autofiltro nas 8 colunas.

### D23. Patrimônio dinâmico e verificação temporal
Nada é fixado no código, nenhum relatório anterior é lido e tudo é recalculado a
cada execução. No modo completo, o consolidado é lido no início e no fim da
coleta financeira e comparado registro a registro. Não há repetição automática.

### D24. Possível duplicidade entre cadastro familiar e cadastros individuais
Na investigação, um cadastro familiar (registro especial) tinha carteiras com
valor atual **e** valor investido idênticos, ao centavo, a carteiras de dois
cadastros individuais, e as descrições das carteiras citavam os membros. A API
não comprova que se trata do mesmo registro (`walletInfoId` e `userId`
diferentes).
- Status: **POSSÍVEL DUPLICIDADE — CONFERÊNCIA MANUAL**. **Nada é subtraído** de
  nenhum total.
- A detecção é genérica (todos os cadastros) e não usa nomes.
- **Pendente:** decisão do usuário com o consultor sobre manter ou remover o
  cadastro familiar.

### D25. Clientes apresentados por nome no terminal
O resumo do terminal mostra clientes como `NOME COMPLETO (ID n)`. Logs em arquivo
e documentação versionada não contêm nomes.

### D26. Dois modos de execução e controle de requisições
Limites informados pelo fornecedor: 1.000.000 de requisições por mês e **50 por
minuto**.

| Modo | `CARTEIRA_ADVISOR_DETAILED_AUDIT` | Requisições ao Carteira Advisor |
|---|---|---|
| **Rápido** (padrão) | `false` | 3: clientes, consultores e uma leitura do consolidado (`size=100`, para a auditoria cadastral por ID) |
| **Completo** | `true` | 3 + uma por carteira (detalhes) + uma segunda leitura do consolidado (verificação temporal); mais 1 consulta à PTAX (Banco Central) |

- O modo rápido gera a planilha e a auditoria cadastral em poucos segundos. Ele
  não consulta carteiras individualmente nem a PTAX, e informa que a auditoria
  detalhada não foi executada.
- Todo GET passa por um limitador que espaça as chamadas em `60 / limite`
  segundos (padrão: 1,2 s com 50/min), inclusive as novas tentativas. HTTP 429
  respeita `Retry-After` (limitado a 60 s), com backoff e tentativas limitadas.
- Nenhum cache de patrimônio entre execuções (D23). Na mesma execução, os dados
  do consolidado e dos detalhes são reaproveitados por todas as verificações.
- O resumo informa quantas requisições foram feitas ao Carteira Advisor.

### D27. Política de dados sensíveis e versionamento
- O GitHub contém **somente** código-fonte, configuração sem credenciais,
  documentação técnica e **dados fictícios** de teste.
- `.env`, `output/` (planilhas e diagnósticos), `logs/`, `*.xlsx`, `*.xls`,
  `*.csv` e respostas brutas da API (`*.json`, exceto arquivos de configuração
  listados explicitamente) ficam fora do Git (`.gitignore`).
- IDs reais de registros especiais ficam apenas no `.env` local.
- Antes de cada commit, o `pre-commit` executa o **detect-secrets** (busca de
  credenciais, com baseline em `.secrets.baseline`), bloqueia arquivos grandes,
  chaves privadas e arquivos `.env`/planilhas/logs.
- O teste `tests/test_repository_hygiene.py` falha se aparecer no repositório um
  CPF/CNPJ válido fora da lista de exemplos fictícios, um e-mail fora dos
  domínios de exemplo, um token preenchido ou um arquivo de extensão proibida.
- Uma auditoria do histórico (28/10/2026) não encontrou credenciais. Encontrou
  dados identificáveis em commits antigos, já removidos dos arquivos atuais. A
  limpeza do histórico depende de autorização (plano no relatório da etapa).

### D11. Fluxo Git
`main` estável, `develop` para desenvolvimento, Conventional Commits. Merge para a
`main` somente com aprovação.

## Premissas ainda não confirmadas pela API

1. Se `clients.currentValue` representa o patrimônio que o negócio espera ver na
   lista de aniversariantes (fonte provisória).
2. Se `wallets[]` pode conter carteiras agregadoras.
3. A cotação oficial usada em `totalCurrentValue`: fonte, data/horário e
   arredondamento, e se algum endpoint a expõe.
4. Por que `clients.currentValue` diverge das carteiras em parte dos clientes.
5. Se a ausência de um cliente no consolidado significa patrimônio zero.
6. Precisão monetária contratada e regra de arredondamento do servidor.

## Limitações conhecidas

- O patrimônio da planilha é **provisório** (`clients.currentValue`).
- Carteiras zeradas ou nunca consolidadas podem ser **intencionais** (mantidas sem
  vínculo pelo usuário). O resumo as lista como informação, não como erro.
- A duplicidade só é detectada quando os valores coincidem exatamente.
- A verificação temporal compara só o início e o fim da coleta financeira.
- Registros especiais dependem da lista de IDs no `.env`.
- Clientes inativos, com `active` não booleano ou com documento inválido ficam
  fora da planilha.
- A verificação PTAX depende de um serviço público externo, sem cache nem
  cotação de reserva.
- A auditoria exata não pode ser aprovada sem a cotação oficial do fornecedor.
- O modo completo faz uma requisição por carteira e leva alguns minutos com o
  limite de 50 requisições por minuto.
- Na política `unconfirmed`, clientes com 2 ou mais carteiras impedem a aprovação
  da auditoria exata.
- No Excel, valores com mais de 15 dígitos significativos são arredondados pelo
  próprio Excel.
