# Marketing — Ads do Mercado Livre (conserto de 07/10/2026)

A coleta de Ads do Mercado Livre (gasto, impressões, cliques e vendas pelos anúncios) estava **zerada desde
~09/07/2026** sem nenhum aviso. Esta página explica o que mudou e como refazer os últimos 90 dias.

## 1. O que estava errado

- O ML **desligou em 27/05/2026** o endereço que o DaVinci usava para as métricas
  (`GET /advertising/advertisers/{ADV}/product_ads/campaigns`), que passou a responder **404**.
- O DaVinci engolia o 404, gravava **gasto 0** e marcava a coleta como "deu certo" (sem erro na integração,
  sem contador de falhas, sem Telegram).
- A revisão dos últimos 7 dias regravava os dias já guardados com esse 0 — por isso os números bons de 09 a
  14/07 também sumiram.
- Antes de 10/07 o gasto estava **inflado** (o total de 7 dias era gravado numa linha de um dia só).
- A Nexus (loja arquivada em 07/07) continuava sendo chamada a cada 30 minutos.

## 2. O que mudou

**De onde vêm os números** (doc oficial:
[Product Ads para Catálogo e User Products](https://developers.mercadolivre.com.br/pt_br/product-ads-para-catalogo-e-user-products-leitura)):

| O quê | Endereço | Cabeçalho |
|---|---|---|
| Anunciante da conta | `GET /advertising/advertisers?product_id=PADS` | `Api-Version: 1` |
| Gasto **por dia** da conta | `GET /advertising/MLB/advertisers/{ADV}/product_ads/campaigns/search?date_from&date_to&metrics=…&aggregation_type=DAILY` (em pedaços de até 30 dias) | `Api-Version: 2` |
| Campanhas: nome, status, orçamento (`budget`) e números (7 dias) | o mesmo `campaigns/search`, sem `aggregation_type` | `Api-Version: 2` |
| Só complemento, se faltar nome/status/orçamento | `GET /marketplace/advertising/MLB/advertisers/{ADV}/product_ads/campaigns/search` (caminho antigo, **não documentado**; falha nele não derruba a coleta) | `Api-Version: 1` |

Campos lidos: `cost` = gasto (R$), `prints` = impressões, `clicks` = cliques, `total_amount` = vendas
atribuídas aos anúncios (diretas + indiretas, janela de 14 dias do ML), `units_quantity` = unidades.
Limites do ML: só **90 dias para trás**; os números do dia fecham às **10:00 BRT do dia seguinte** (o dia de
hoje aparece com gasto 0 e se corrige nas próximas rodadas).

**Erro nunca mais vira zero.** A cada rodada (cron `marketing_full_sync`, minutos :05 e :35) cada conta do ML
fica com um destes estados, gravado em `marketing_accounts.sync_status` (+ `sync_erro`, `sync_em`,
`sync_ok_em`):

| Estado | Quando | O que acontece |
|---|---|---|
| `ok` | o ML respondeu com as métricas | grava os 7 dias, as campanhas e carimba `integrations.last_ads_sync_at` |
| `erro` | 404/4xx/5xx (inclusive 404 do anunciante **sem** a mensagem "No permissions found" — ex.: o ML mudou o endereço), falha de rede, resposta sem `cost`/`prints`/`clicks` ou com `null` num dia já fechado, **paginação incompleta** (página repetida, página vazia antes do `paging.total`, total que muda no meio), dia fora da janela pedida, token recusado | **não mexe em nenhum número**; conta uma falha seguida (Telegram na 3ª, como antes) |
| `sem_permissao` | conta sem Publicidade liberada (404 com a mensagem "No permissions found", ou lista de anunciantes vazia) ou token sem o escopo de anúncios (403) | não mexe em números; **não** conta como falha (é ação do dono, sem alarme a cada 30 min) |

Na tela **Marketing › Mercado Livre**, a linha **Status** mostra "Erro na coleta" (vermelho) ou "Sem
permissão de Ads" (amarelo) no lugar de "Ativo"; passando o mouse aparece o erro e a última coleta boa.
No log do worker, `marketing_full_sync` agora traz a contagem por estado (`ml_ok`, `ml_erro`,
`ml_sem_permissao`…) e sai como *warning* quando alguma conta falhou; cada falha também gera
`ml_ads_sync_erro` / `ml_ads_sem_permissao` com o código do ML.

**Revisão dos 7 dias:** um dia só é reescrito quando a chamada deu certo **e** o ML trouxe aquele dia
(resposta completa: todas as páginas até o `paging.total`). Dia que o ML não trouxe fica como estava e sai
no aviso `ml_ads_avisos` do log (e em `avisos.dias_sem_dado_ml` no retorno/simulação) — o dia de hoje não
conta, o ML só fecha no dia seguinte. Agora a rodada também **cria** a linha dos dias da janela que ainda
não existiam (uma linha por dia, 12:00 UTC, `intensity=0`, como antes).

**Uma linha por dia, sempre:** a 0381 cria a chave única `uq_marketing_metrics_conta_dia` (conta + dia, só
nas linhas diárias `intensity=0`). Cron e backfill gravando a mesma conta ao mesmo tempo ficam em fila
(trava por conta) e a linha nova entra com `INSERT … ON CONFLICT` — antes os dois inseriam o mesmo dia e as
telas somavam o gasto em dobro. Se aparecer dia repetido (banco sem a 0381), ele sai em
`avisos.linhas_repetidas` e no log de erro `ml_ads_linha_diaria_duplicada`.

**Token renovado nunca se perde:** o `refresh_token` do ML é de uso único. Quando a coleta renova o token,
o novo é gravado e confirmado na hora, numa transação à parte — nenhum erro depois disso (sem permissão,
falha, exceção) consegue voltar o banco para o token já gasto.

**Conferência por campanha:** a soma do gasto por dia é comparada com a soma do gasto por campanha da
mesma janela (as duas vêm do mesmo `campaigns/search`). Se não batem (diferença > R$ 1 e > 1%), sai em
`avisos.divergencia_campanhas` — só aviso, não muda o estado da conta.

**Outras mudanças:**
- Loja arquivada (Lojas › Arquivar) não é chamada — nem pelo ML nem pela Amazon.
- Conta de Marketing arquivada (`marketing_accounts.arquivada_em`) some de todas as telas/somas do Marketing
  e não é chamada.
- Conta órfã com o mesmo nome (jlas2, aguiar2 e forpaper ficaram sem integração em set/2026) é **adotada**
  quando o Ads da loja for ligado — antes a coleta quebraria na regra de nome único.
- As campanhas passam a mostrar gasto, impressões, **vendas pelos anúncios** e ACOS do próprio ML dos
  últimos 7 dias (igual à Shopee). Antes a venda ficava 0. Campanha que o ML não devolveu com métricas
  não é tocada (fica com o número que tinha — nada de gasto 0 inventado).
- O "dia de hoje" segue o fuso de Brasília (antes era UTC — das 21h à meia-noite gravava no dia seguinte).

## 3. Amazon: dados de demonstração desligados

As 2 contas "Kfa" da Amazon do Marketing **não eram reais**: foram criadas pelo `POST /api/marketing/seed`
de demonstração em 20/05/2026, e o robô de demonstração (`services/marketing/agent.py`, números aleatórios)
gravava ~190 linhas por dia nelas.

- O robô, o botão "rodar ciclo agora" e o `/seed` só funcionam com `MARKETING_AGENTE_SIMULADO=true` no
  `.env`. **Sem a variável = desligado** (é o que vale em produção).
- A migration **0381** arquiva as contas da Amazon sem integração (as 2 "Kfa") e desliga o robô nelas.
- **Nada foi apagado**: as linhas antigas continuam no banco, só não aparecem nem entram em soma nenhuma.
- A coleta real da Amazon continua como estava (sem credenciais da API de Ads em produção, ela não roda).

## 4. Deploy

1. Rodar a migration **0381_marketing_ads_estado** (só `ADD COLUMN` nulável + `UPDATE` de 2 linhas + o
   índice único das linhas diárias, com `lock_timeout` de 10 s) e conferir
   `davinci.alembic_version = '0381_marketing_ads_estado'` **antes** do `up -d` — o código novo lê as
   colunas novas de `marketing_accounts` em toda consulta do Marketing e grava as linhas diárias com
   `ON CONFLICT` nesse índice. Antes, conferir que não há dia repetido (deve voltar 0 linhas; em 07/10/2026
   eram 0 em 2.563 linhas diárias):
   `SELECT account_id, "timestamp", count(*) FROM davinci.marketing_metrics WHERE intensity = 0 GROUP BY 1, 2 HAVING count(*) > 1;`
2. Não criar `MARKETING_AGENTE_SIMULADO` no `.env` de produção.
3. Na primeira rodada (:05 ou :35) os últimos 7 dias se corrigem sozinhos. O resto, pelo backfill abaixo.

## 5. Backfill (até 90 dias)

Refaz gasto/impressões/cliques por dia + o faturamento do Bling dos últimos N dias (no máximo 90, o limite
do ML). **Por padrão só simula**: chama o ML (só leitura), lê o Bling e mostra o que cada dia passaria a ter,
sem gravar nada e **sem renovar token** (conta com token vencido aparece como `token_expirado` — rode de
novo depois que o worker renovar).

```bash
cd /opt/davinci
# 1) simulação (todas as contas do ML com Ads ligado)
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api \
  python -m scripts.ml_ads_backfill --dias 90
# plano completo por dia, em JSON
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api \
  python -m scripts.ml_ads_backfill --dias 90 --json > /tmp/ml_ads_backfill_simulacao.json
# 2) gravar
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api \
  python -m scripts.ml_ads_backfill --dias 90 --apply
# só algumas contas (id da integração; repita a opção) — vale também para loja com Ads desligado
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api \
  python -m scripts.ml_ads_backfill --dias 90 --apply --integracao <id-da-integracao>
```

- Idempotente: rodar de novo dá `iguais` e não cria linha repetida. Nunca apaga linha.
- Pede ao ML em pedaços de até 30 dias (90 dias = 3 chamadas) e só usa resposta completa.
- Linha `aviso … dia(s) fechado(s) sem dado do ML`: o ML não trouxe aqueles dias (sem anúncio rodando, ou
  formato diferente do esperado) — neles fica o que já estava gravado. Conferir no painel do ML antes de
  confiar.
- Conta com erro (ou sem permissão) não tem nenhum número alterado e não impede as outras.
- Sai com código 1 se alguma conta deu `erro`.
- Cada dia que passar sem rodar perde 1 dia de histórico recuperável (o ML só guarda 90 dias).

## 6. Pendências e o que NÃO mudou

- **Checagem ao vivo não feita**: a chamada de leitura à API do ML em produção (07/10/2026) foi bloqueada
  pela permissão do ambiente. O formato foi tirado da doc oficial. Antes do `--apply`, olhar a simulação de
  uma conta grande (ex.: Marquezini) contra o painel do ML. Se o ML devolver uma linha por campanha e dia
  (em vez de uma por dia), o código já soma por dia. A checagem de paginação é estrita (resposta parcial =
  `erro`): se o `paging.total` do ML contar mais do que as linhas que ele entrega, a simulação vai mostrar
  `paginacao_incompleta` em vez de números — ajustar antes do `--apply`.
- Ação do dono (o código não resolve): ligar o Ads de Jlas 2, Aguiar 2, Forpaper, Mega, Dream 2 e Counhago;
  liberar Publicidade na Eron (vai aparecer "Sem permissão de Ads"); reconectar a Lucas MEI (o ML recusa o
  app desde set/2026 — vai aparecer "Erro na coleta").
- **Não mudou**: pausar/retomar/orçamento de campanha (comandos) continuam pelo caminho de antes; a Shopee
  não foi tocada; `credit_balance` do ML continua sendo "orçamento diário restante" (a tela só mostra
  crédito para a Shopee); o faturamento da linha diária continua vindo do Bling.
