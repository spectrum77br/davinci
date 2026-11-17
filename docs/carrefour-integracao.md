# Carrefour Brasil — leitura e envio de estoque e preços

O cliente `apps/api/app/services/marketplaces/carrefour.py` usa a Mirakl Seller
API oficial em `https://marketplace.carrefour.com.br/api`. O endereço é fixo;
URLs vindas de credenciais, respostas ou redirecionamentos não são seguidas.
O cliente consulta a conta, importa anúncios e permite consultar pedidos.
Desde 17/11/2026, a implementação também permite enfileirar atualizações de
estoque e preços de ofertas existentes, com ativação separada por conta e
confirmação assíncrona. Não cria ofertas ou catálogo, nem envia notas fiscais,
expedição ou reembolsos.
Desde 30/10/2026 lê e responde as mensagens da loja no `/atendimento` (seção
"Mensagens") pela resposta na thread (M12).

## Credenciais e identificação da loja

Cada integração guarda `api_key` e `shop_id` nas credenciais cifradas existentes.
`shop_id` é inteiro positivo (string numérica também é aceita). A chave vai no
cabeçalho `Authorization` sem o prefixo Bearer. Todas as consultas incluem o ID
explicitamente: não se usa a loja padrão de um usuário com várias lojas.

Antes da leitura, A01 precisa confirmar o mesmo `shop_id`, nome não vazio e
`shop_state=OPEN`. Uma loja fechada, suspensa, encerrada, desconhecida ou uma
resposta de outra loja produz falha de conexão, sem iniciar a importação.
Um sucesso dessa consulta confirma somente o acesso e a identificação naquele
momento; as permissões de ofertas e pedidos são confirmadas por suas leituras.

O manual Carrefour recomenda um usuário específico para o integrador, com as
permissões das funções utilizadas. A chave é vinculada ao usuário. Gerar uma
chave nova invalida a anterior: não regenerar uma chave usada por outro sistema
sem planejar sua atualização. Produção e homologação têm acessos separados.

## Consultas e contrato dos dados

- `test_connection()` → A01 `GET /account`; devolve nome, ID, estado, modo
  `read_only`, `checked_at` e indicação `cached`. Esse modo descreve a
  verificação de conexão, que não grava nada na Mirakl; não representa o
  estado dos controles de envio nem comprova permissão para escrever.
- `list_listings(page_size=50, max_pages=None)` → OF21 `GET /offers` paginado
  com `max` (1–100) e `offset`. `external_id` é `offer_id` como string;
  `sku` é `shop_sku`, preservando caixa, pontos, hífens e `+`. O SKU de catálogo
  do Carrefour (`product_sku`) não substitui o SKU do vendedor.
  Páginas repetidas sem avanço, página vazia antes do total informado e término
  com menos IDs distintos que `total_count` sinalizam consulta incompleta,
  em vez de sucesso. Um total que diminua durante a consulta é aceito quando
  já há pelo menos essa quantidade de registros distintos.
- O preço vem de `applicable_pricing.price`, quando presente, senão de `price`.
  É convertido com Decimal e arredondamento half-up para centavos inteiros,
  conforme `Listing.price` no DaVinci (123,45 reais → 12345).
  `stock` vem de `quantity`; zero é preservado e um valor inválido vira `None`.
  `active=true` vira `active`; outros valores viram `inactive`. Título,
  descrição, categoria e JSON original são preservados. OF21 não documenta
  imagem do produto, então `thumbnail_url` é `None`.
- `list_orders(start_update_date=None, page_size=50, max_pages=None)` → OR11
  `GET /orders`, devolvendo os pedidos originais. A data opcional deve conter
  fuso. A camada chamadora deve deduplicar por pedido e controlar o agendamento.
- `get_offer(offer_id)` → OF22 `GET /offers/{offer_id}`, sempre com `shop_id`,
  confirma a identidade e o SKU da oferta vinculada antes e depois de um envio.
- `update_stock(...)` e `update_price(...)` em uma integração persistida
  enfileiram a intenção quando o controle correspondente está ativo. Retornam
  `REQUIRES_REVIEW` e `carrefour_write_pending`: ainda não são confirmação de
  aplicação. Sem `integration_id`, ou com envio desativado, retornam
  `carrefour_write_not_enabled`. `force=True` não contorna essas condições.

## Envio de estoque e preços (17/11/2026)

Os campos `carrefour_stock_write_enabled` e `carrefour_price_write_enabled`
são separados por integração e começam em **false**, inclusive nas contas
existentes sem esses campos. A alteração passa pelo PATCH da integração e
pela permissão `integracoes/edit`; o servidor persiste os controles nas
credenciais cifradas, sem aceitar sua injeção no objeto livre de credenciais.
O modo férias impede novos envios de estoque. Desativar um controle impede
novos POSTs daquele tipo; um envio já aceito continua sendo consultado para
registrar seu resultado.

Ativar os controles não cria vínculos. Cada envio exige `ProductLink` da mesma
integração com `offer_id` válido e SKU exato, produto ativo e vínculo com uma
integração Bling ativa. Antes de enviar estoque, o worker consulta novamente
o Bling, confirma ID, SKU, situação e `saldoVirtualTotal` e aplica as regras
existentes de estoque publicável/família. A confirmação da fonte precisa ter
no máximo cinco minutos no momento do POST. Dados ausentes, inválidos ou
ambíguos interrompem o envio; não viram estoque zero nem autorizam o uso de um
saldo antigo.

O cliente envia apenas CSVs parciais para ofertas existentes:

| Operação | Envio | Consulta do processamento | Campos do CSV |
| --- | --- | --- | --- |
| Estoque | STO01 `POST /offers/stock/imports` | STO02 `GET /offers/stock/imports/{import_id}/status` | `offer-sku;quantity` |
| Preço | PRI01 `POST /offers/pricing/imports` | PRI02 `GET /offers/pricing/imports?import_id=...` | `offer-sku;price` |

Todos incluem `shop_id` explícito. O CSV de estoque não inclui depósito e
atualiza o saldo global; o validador aceita somente ofertas sem estoques por
depósito e com centro de atendimento `DEFAULT`. Não reenvia preço, promoção,
frete ou outros campos da oferta.

PRI01 substitui o conjunto de preços da oferta. Por isso, o envio está limitado
a BRL, um preço padrão sem desconto, sem canal/contexto específico e uma única
faixa de quantidade iniciada em 1. Promoções, descontos, preços por volume,
múltiplos canais, preços de varejo, MSRP ou unidade de preço exigem revisão e
não são enviados. O estoque não participa desse CSV. A leitura anterior ao
envio não fornece uma trava remota: alterações simultâneas de preços ou
promoções por outro sistema ainda precisam de coordenação operacional.

**Não há fallback para OF24**, que redefine campos omitidos. Respostas
403/404 nos endpoints parciais não levam a uma atualização completa da oferta
nem a upload de catálogo. PRI03 é relatório de erros, não a consulta usada
para confirmar o processamento.

### Fila e confirmação

`app/services/carrefour_offer_write.py` mantém um lote ativo por integração,
com até dez itens de um único tipo. Quando há estoque e preço pendentes, os
tipos se alternam; o intervalo mínimo entre lotes e entre consultas de status
é de cinco minutos. Intenções posteriores são agrupadas por vínculo, mantendo
independentes os campos ainda pendentes. Antes do POST, o worker relê as
permissões, credenciais, revisão da intenção e identidade do vínculo sob
trava. Uma intenção substituída volta à fila sem enviar o valor antigo.

Um HTTP 201 e um `import_id` significam apenas recebimento. A confirmação exige
status `COMPLETE`, ausência de erros/relatório de erros, quantidade de linhas
bem-sucedidas exatamente igual à enviada e nova leitura OF22 do mesmo ID e
SKU. Os contadores de STO02 são opcionais no contrato oficial: se faltarem em
uma resposta terminal, o lote vai para revisão, pois não há prova suficiente
de processamento. Respostas malformadas e divergências de identidade também
terminam em revisão; falhas transitórias de leitura podem ser consultadas
novamente após o intervalo persistido.

Para estoque, após o processamento comprovado, um saldo observado entre zero
e o solicitado é aceito e registrado como observado: uma venda pode ter
reduzido o saldo. Essa confirmação limpa a intenção processada e não repõe a
diferença. Preço exige correspondência com o valor solicitado. Se a leitura
prévia já mostra o valor desejado, a fila conclui o item por essa leitura,
sem POST.

O estado de envio é persistido antes do HTTP. Timeout, falha de transporte,
resposta ambígua ou interrupção após esse marco deixam o lote incerto e
bloqueiam novos envios da conta até conferência; não se repete um POST
automaticamente. Rejeições conhecidas e inconsistências ficam em revisão,
sem reentrada automática pela sincronização normal.

A precificação mostra `carrefour_write_pending` como pendente, sem marcar a
célula como aplicada/`AUTO` ou como `ERROR`. O cache de anúncio/vínculo só
recebe o campo confirmado, preservando o outro. O endpoint
`GET /api/integrations/{integration_id}/carrefour-writes` expõe quantidades de
vínculos, pendências, revisões, confirmações e bloqueio por resultado incerto.

## Limites e cache da verificação

A documentação atual limita A01 a uma chamada por dia. O cliente guarda
`_carrefour_account_check` por 24 horas, incluindo resultados negativos, e não
repete A01 automaticamente após timeout ou 429. O cache contém o horário,
resultado, identificação da loja e hash vinculando a chave e o ID, nunca a chave
em claro dentro desse registro. Trocar qualquer uma das credenciais invalida o
cache. A interface deve indicar quando está mostrando uma verificação anterior.

Para integrações persistidas, todos os construtores passam `integration_id`.
O cliente abre uma transação própria, trava a linha com `FOR NO KEY UPDATE`,
relê as credenciais/cache após a trava e só então decide se chama A01. A mesma
trava serializa API e workers. O cache é mesclado nas credenciais atuais e
persistido cifrado antes de retornar sucesso. Nesse caminho o callback não é
usado, evitando sobrescrever uma chave rotacionada com uma cópia antiga.

Sem `integration_id`, há somente cache/trava locais e o callback assíncrono
`on_token_refresh` pode persistir o metadado; não há renovação de token. Esse
caminho serve para clientes avulsos, não para as rotas de contas salvas.
O campo é **exclusivo do servidor**: rotas não devem aceitar um cache enviado
pelo usuário. Uma transação que falhe não retorna sucesso; como em qualquer
operação externa seguida de gravação, uma interrupção depois do HTTP e antes
do commit pode deixar a consulta sem registro, exigindo atenção ao limite diário.

OF21 é destinado à visualização/importação sob demanda. OR11 recomenda polling
a cada 5 minutos (máximo uma vez por minuto); para External Partner o intervalo
é de 15 minutos. Grandes históricos devem usar o fluxo de exportação assíncrona,
ainda não implementado. O cliente de leitura não instala
agendamentos; a fila de escrita é processada pelo worker da aplicação.

As consultas têm timeout de 30 segundos. Um 429 considera `Retry-After` em
segundos ou data HTTP; leituras de ofertas/pedidos repetem no máximo uma vez
quando a espera é de até 5 segundos. Esperas maiores retornam erro com o atraso,
sem tentar antes do horário. Corpos de erro e mensagens brutas de transporte
não são incluídos nos erros do cliente, evitando expor segredos.

## Mensagens no atendimento (30/10/2026)

Cada loja Carrefour ganha um canal `chat` no `/atendimento` (nasce em
`observar`, como os outros; com a loja em `humano` a equipe responde pela
caixa). O adaptador é `apps/api/app/services/atendimento/carrefour.py`; uma
conversa por thread da Mirakl.

- **M11** `GET /inbox/threads` (`list_threads`): `updated_since` (o cursor do
  canal, com 15 min de folga; primeira leitura de 30 dias), `with_messages=true`
  (as últimas 100 mensagens de cada thread, sem consulta por conversa) e
  `limit=100`. Paginação por `page_token` (com ele a Mirakl ignora os outros
  filtros); o cursor só anda com a lista inteira. Não chama A01: o `shop_id`
  vai explícito, e o limite diário do A01 (com a falha guardada por 24 h)
  calaria o chat depois de um timeout.
- **A vez da loja** é a da Mirakl: `metadata.shop_reply_needed_since` (nulo
  quando a loja respondeu ou marcou "não precisa de resposta") vai em
  `dados.vez_da_loja_desde`, como o SAC da Magalu. `CUSTOMER_USER` = cliente,
  `SHOP_USER` = loja, `OPERATOR_USER` (atendimento do Carrefour) = mediador.
- **M12** `POST /inbox/threads/{id}/message` (`reply_thread`): multipart com a
  parte `message_input` (JSON `body` + `to`); corpo de 3 a 50.000 caracteres
  (a caixa limita a 2.000). Vai para quem falou por último que não é a loja (o
  comprador, ou o Carrefour cobrando) e só para quem a thread autoriza. Nunca
  repete: timeout e 5xx ficam `revisar`. O `message_id` do 201 é o id externo
  da linha; a leitura seguinte a reconhece por ele (sem ele, pelo texto).
- Papéis da chave (OpenAPI): leitura `ROLE_SHOP_ADMIN` ou
  `ROLE_PARTNER_MESSAGE_READ`/`ROLE_PARTNER_OFFER_MESSAGE_READ`; resposta
  `ROLE_SHOP_ADMIN` ou `..._MESSAGE_WRITE`. 401/403 deixam o canal
  `sem_escopo` ("gere a chave de API no portal… e atualize em Integrações");
  o sync tenta de hora em hora, ou na hora em que a integração é atualizada.
  429 segura o canal até o `Retry-After`.
- Conferido em produção em 30/10/2026, só leitura e sem texto de comprador:
  Poofy responde 200 no M11 (0 threads; a loja não tem pedidos ainda); a chave
  do VR dá 401 em M11, OR11 e OF21 (o A01 de 26/10 tinha passado: a chave foi
  trocada ou revogada no portal depois disso).

Testes: `tests/test_atendimento_carrefour.py` (Mirakl falsa por respx).

Fontes: [M11](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/messages/m11),
[M12](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/messages/m12),
[paginação](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3).

## Validação operacional e referências

Os testes de escrita usam HTTP simulado e banco PostgreSQL descartável; não
comprovam a disponibilidade ou as permissões STO01/PRI01 de uma conta real.
Na revisão desta implementação, nenhum POST de estoque/preço foi feito no
Carrefour. A01 e a leitura de ofertas bem-sucedidos não comprovam esse acesso.
Ele só estará validado após um piloto real cujo recebimento, processamento e
leitura final sejam confirmados. Antes de ativar uma conta, confirmar também
os vínculos e a fonte Bling: controles ativos sem vínculos elegíveis não
produzem envios.

Carrefour Envios atualiza seus próprios estados de expedição/recebimento;
não aplicar o fluxo de transporte próprio nesses pedidos.

Fontes oficiais de leitura consultadas em 26/10/2026 e contrato de escrita
conferido no OpenAPI Mirakl em 17/11/2026:

- [Manual Carrefour v2](https://storage.googleapis.com/portal-seller-file-prd/manual_integrac%CC%A7a%CC%83o_carrefour_marketplace_v2.pdf)
- [Mirakl: autenticação, paginação e limites](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3)
- [A01 — identificação da loja](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/stores)
- [OF21 — ofertas](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/of21)
- [OF22 — consulta de uma oferta](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/of22)
- [OR11 — pedidos](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/orders/or11)
- [OF24 — atualização completa de ofertas](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/of24)
- [STO01 — importação de estoque](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/sto01)
- [STO02 — status da importação de estoque](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/sto02)
- [PRI01 — importação de preços](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/pri01)
- [PRI02 — consulta das importações de preços](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/pri02)
- [OpenAPI Seller](https://developer.mirakl.com/_bundle/content/product/mmp/rest/seller/openapi3.json?download)

Teste isolado (Python >= 3.12, dependências de desenvolvimento instaladas):

```sh
cd apps/api
DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:1/test PYTHONPATH=. \
  .venv/bin/python -m pytest --noconftest \
  tests/unit/test_carrefour_client.py \
  tests/unit/test_carrefour_partial_imports.py \
  tests/unit/test_pricing_carrefour_push.py -q
```

`--noconftest` é necessário para não carregar as fixtures de banco do projeto.
`tests/unit/test_carrefour_cache_postgres.py` verifica a trava com PostgreSQL
real descartável e HTTP simulado. Exige `CARREFOUR_TEST_DATABASE_URL` em
localhost, cria um schema aleatório por teste e o remove ao terminar.
`tests/unit/test_carrefour_outbox_postgres.py` cobre também concorrência,
desativação, confirmação, agrupamento de intenções e resultado incerto da fila.
