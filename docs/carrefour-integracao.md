# Carrefour Brasil — leitura de lojas VR e Poofy

O cliente `apps/api/app/services/marketplaces/carrefour.py` usa a Mirakl Seller
API oficial em `https://marketplace.carrefour.com.br/api`. O endereço é fixo;
URLs vindas de credenciais, respostas ou redirecionamentos não são seguidas.
Esta etapa consulta a conta, importa anúncios e permite consultar pedidos.
Não publica ofertas, estoque, preços, notas fiscais, envio ou reembolsos.
Desde 30/10/2026 lê e responde as mensagens da loja no `/atendimento` (seção
"Mensagens"): a resposta na thread (M12) é a única escrita remota liberada.

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
  `read_only`, `checked_at` e indicação `cached`.
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
- `update_stock(...)`, inclusive com `force=True`, devolve `REQUIRES_REVIEW`
  e `carrefour_write_not_enabled`, sem fazer qualquer chamada HTTP.

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
que esta primeira etapa não implementa. O cliente não instala agendamentos.

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

## Próximas etapas e referências

Validar credenciais reais de VR e Poofy e comparar anúncios/pedidos com o portal
antes de considerar a integração operacional. Esta implementação e seus testes
não chamaram APIs autenticadas nem acessaram o banco de produção.

Uma futura escrita exige contrato próprio: OF24 redefine campos omitidos, logo
não pode ser usado como atualização parcial de estoque. PRI01 também exclui
preços não reenviados. Carrefour Envios atualiza seus próprios estados de
expedição/recebimento; não aplicar o fluxo de transporte próprio nesses pedidos.

Fontes oficiais consultadas em 26/10/2026:

- [Manual Carrefour v2](https://storage.googleapis.com/portal-seller-file-prd/manual_integrac%CC%A7a%CC%83o_carrefour_marketplace_v2.pdf)
- [Mirakl: autenticação, paginação e limites](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3)
- [A01 — identificação da loja](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/stores)
- [OF21 — ofertas](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/of21)
- [OR11 — pedidos](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/orders/or11)
- [OF24 — atualização completa de ofertas](https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3/offers/of24)

Teste isolado (Python >= 3.12, dependências de desenvolvimento instaladas):

```sh
cd apps/api
DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:1/test PYTHONPATH=. \
  .venv/bin/python -m pytest --noconftest tests/unit/test_carrefour_client.py -q
```

`--noconftest` é necessário para não carregar as fixtures de banco do projeto.
`tests/unit/test_carrefour_cache_postgres.py` verifica a trava com PostgreSQL
real descartável e HTTP simulado. Exige `CARREFOUR_TEST_DATABASE_URL` em
localhost, cria um schema aleatório por teste e o remove ao terminar.
