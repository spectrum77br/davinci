# Conferência Shopee

Relatório de marketing da Shopee, **terça e quinta às 13:30 (BRT)**, com as lojas separadas em
**Mala · Celular · Eletro** e comparado com as 3 semanas anteriores. Aparece em **Marketing → Conferência
Shopee** e gera HTML (a própria tela), CSV, MD, JSON e Excel. O Resumo segue o desenho da planilha antiga
do dono (seção 5).

Substitui o `conferencia.js` descrito em `COMO-MONTA-O-RELATORIO.md` (06/10/2026). As diferenças em relação
a esse documento estão no fim desta página.

## 1. Período

| Execução | Semana do relatório (S1) | Comparação (S2, S3, S4) |
|---|---|---|
| **Terça** (`semanal`) | a semana fechada anterior, **segunda a domingo** | as 3 semanas segunda–domingo antes dela |
| **Quinta** (`parcial`) | **segunda a quarta** da semana atual | segunda–quarta das 3 semanas anteriores |
| Manual | escolhe `semanal` ou `parcial` | idem |

Exemplos: terça 06/10/2026 → S1 28/09–04/10, S2 21/09–27/09, S3 14/09–20/09, S4 07/09–13/09.
Quinta 08/10/2026 → S1 05/10–07/10, S2 28/09–30/09, S3 21/09–23/09, S4 14/09–16/09.

Os períodos são fixados **quando a execução é criada** (no fuso America/Sao_Paulo). Uma coleta atrasada
continua cobrindo o mesmo período.

## 2. Contas

Tabela `conferencia_shopee_conta`: uma linha por loja, com o perfil do AdsPower, o nome exibido e o grupo
(`mala` ou `celular`). Começa com as 17 lojas do documento. VR, Eron e Lucas MEI entram desativadas, e
podem ser ligadas na tela.

- Nomes exibidos: KIA → **Fiore**, JLAS → **Atlas**.
- **Eletro não é uma conta, é uma parte da conta.** Uma conta de celular aparece também em Eletro se teve
  venda, afiliado ou Ads de eletro em alguma das 4 semanas.
- Saldo de Ads é da conta inteira: aparece na linha de Celular e fica "—" em Eletro.

## 3. O que é Eletro

Por produto (item da Shopee), nesta ordem:

1. **Vínculo do DaVinci**: item → `product_links` → SKU. Se o SKU está na lista de eletro do DaVinci (air
   fryer, cafeteira, smart cooking, cookware, slushie, sorveteira), é Eletro; se não está, não é.
2. Sem vínculo: categoria Shopee de nível 1 **100010** (Eletrodomésticos) ou **100636** (Casa e Decoração).
3. Sem vínculo e sem categoria: título com `air ?fryer|airfryer|fritadeira|cafeteira|slush|cookware|panela|smart ?cook|sorvet`
   (sem diferenciar maiúsculas nem acentos).

Os itens em que a regra 1 discorda da categoria da Shopee são listados nas Notas, para conferência.

## 4. Colunas (nesta ordem)

| Coluna | Fonte (Central do Vendedor, dentro do perfil logado) |
|---|---|
| Vendas afiliados | `affiliateplatform/dashboard/seller_daily` → `dis_total_actual_amount` (eletro: `seller_item_detail` → `dis_gmv`) |
| Vendas Ads | `pas/v1/report/get_time_graph` → `report_aggregate.broad_gmv ÷ 100.000` (eletro: `pas/v1/homepage/query` por anúncio) |
| Saldo Ads | `pas/v1/wallet/get` → `ads_credit.available ÷ 100.000`, no momento da coleta |
| Impressões | `get_time_graph` → `report_aggregate.impression` (eletro: por anúncio) |
| Invest. afiliados | `seller_daily` → `dis_total_seller_commission` (comissão estimada; eletro: `dis_spend`) |
| Invest. Ads | `get_time_graph` → `report_aggregate.cost ÷ 100.000` (eletro: por anúncio) |
| % s/ vendas | (Invest. afiliados + Invest. Ads) ÷ Vendas × 100 |
| Vendas | `mydata/v4/product/performance` por dia, `category_id=-1` → soma de `paid_sales` (eletro: itens eletro) |
| Cliques afiliados | `seller_daily` → `clicks` (eletro: `seller_item_detail` → `clicks`) — desde 07/10/2026 |
| Pedidos afiliados | `seller_daily` → `total_order_count` (eletro: `seller_item_detail` → `orders`) |
| Conversão afiliados | Pedidos afiliados ÷ Cliques afiliados × 100 |
| Cliques Ads | `get_time_graph` → `report_aggregate.click` (eletro: por anúncio) |
| Pedidos Ads | `get_time_graph` → `report_aggregate.broad_order` (eletro: por anúncio) |
| Conversão Ads | Pedidos Ads ÷ Cliques Ads × 100 |

As 6 últimas (cliques, pedidos e conversão) entraram em 07/10/2026 e vêm **depois** das 8 primeiras no
relatório e no CSV (nada mudou de lugar). Coleta de antes disso não tem os cliques de afiliados: a métrica e
a conversão de afiliados ficam "—" (nunca 0), também no "Recalcular".

- Celular = total da conta − eletro. Vendas, comissão e pedidos de afiliados e tudo de Ads e de Vendas fecham:
  a soma dos itens bate com o total (conferido na Barbosa em 06/10/2026: 71 = 71 pedidos de afiliados; cliques,
  pedidos e impressões de Ads iguais ao agregado). Gasto de Ads sem item (ex.: GMV Max da loja) fica em Celular.
- **Cliques de afiliados são a exceção**: o total (`seller_daily` → `clicks`) e os cliques por produto
  (`seller_item_detail` → `clicks`) são contas diferentes da Shopee e **não fecham** (Barbosa, mesma semana:
  total 18.299, soma dos produtos 20.884). Subtrair a soma dos produtos eletro do total deixava Celular errado
  (3.139 cliques e 1,24% em vez de ~0,7%) e, com eletro mais pesado, negativo. Por isso o total é **dividido na
  proporção dos produtos**: eletro = total × Σcliques dos produtos eletro ÷ Σcliques de todos os produtos
  (inteiro, meio para cima) e Celular = o resto. Celular + Eletro = total, sempre; nada fica negativo. Algum
  produto sem o número de cliques → não dá para dividir (vai tudo para Celular, com aviso).
- Celular que daria **negativo** em qualquer métrica (a parte eletro passou do total da conta — fontes que não
  batem) fica "—", Eletro fica com o total da conta (o Geral continua certo) e a linha da conta ganha o aviso
  "… de eletro passou do total da conta". Nunca aparece número nem conversão negativa.
- Se o total de uma seção falhou e só os itens vieram (são chamadas separadas), a métrica fica "—" em
  Celular **e** em Eletro: um número só da parte eletro deixaria o Geral pela metade sem avisar.
- Totais de grupo usam **somas**; o % do grupo é Σinvestimento ÷ Σvendas e a conversão é Σpedidos ÷
  Σcliques (nunca a média dos percentuais), **só das lojas que têm os dois números**: uma loja com pedidos e
  sem cliques (coleta antiga, Celular que ficou "—") não entra na conta da conversão — os pedidos dela sem os
  cliques inflariam a conversão do grupo. Os totais de pedidos e de cliques continuam somando tudo o que veio.
  Conversão sem cliques (0 ou negativo) fica "—".
- Se um item **eletro** veio sem os cliques (ou pedidos) por produto, mas o total veio, o **par** cliques +
  pedidos dessa seção fica "—" em Eletro e inteiro em Celular, com um aviso na linha da conta. Separar só os
  pedidos deixaria Celular com os cliques de eletro sem os pedidos deles (conversão baixa demais) e Eletro com
  pedidos sem cliques.
- Arredondamento (tela, Excel, CSV, MD, HTML e Threema iguais): o número como se escreve, **meio para
  longe do zero** — 8,25% → 8,3%; 6,35% → 6,4%; R$ 0,125 → R$ 0,13 (é como o Excel mostra a célula).
  O relatório guarda dinheiro e % com 2 casas por essa regra; o % sai dos valores já arredondados.

## 5. O Resumo (tela, Excel e HTML) — formato da planilha

Pedido de 07/10/2026 ("mais ou menos desse jeito"): o Resumo tem o desenho da planilha antiga do dono.

```
                  |        07/09 a 13/09          | … |        28/09 a 04/10          | Variação (28/09–04/10 × 21/09–27/09)
 Métrica          | Mala | Celular | Eletro | Geral | … | Mala | Celular | Eletro | Geral | Mala | Celular | Eletro | Geral
 Vendas       afiliados
              Ads
 Impressões   afiliados   ← sempre "—": a Shopee não dá impressões de afiliados
              Ads
 Conversão    afiliados   ← pedidos ÷ cliques (%)
              Ads
 Investimento afiliados
              Ads
 Resumo       % investimento / vendas   ← o "% s/ vendas"
              Vendas no período         ← "Vendas"
```

- Duas colunas de rótulo: a categoria (mesclada nas suas 2 linhas) e o detalhe.
- Semanas da **mais antiga para a mais recente**, da esquerda para a direita; cada semana com Mala, Celular,
  Eletro e Geral. No fim, **Variação** = semana do relatório × a anterior, por grupo, com as regras de
  sempre ("▲ 12,3%", "novo", "="; % e conversão em p.p.; verde/vermelho pelo que é bom — conversão boa é
  subir, % de investimento bom é descer; investimento sempre cinza). Os p.p. têm **2 casas**, como o % da
  planilha ("▼ 0,01 p.p."): com 1 casa, uma conversão de 0,44% → 0,43% saía "▼ 0,0 p.p." em vermelho. O
  aviso do Threema (% com 1 casa) continua com 1 casa.
- Valores: R$ com 2 casas, inteiros com ponto de milhar, % com **2 casas** ("7,50%"). Sem dado = "—" (Eletro
  sem conta com eletro sai "—").
- Visual: cabeçalho azul-escuro (#1F3864) com letra branca; as 2 colunas de rótulo e toda coluna **Geral**
  em bege (#DDD9C4, como na planilha); grade cinza fina (#BFBFBF).
- Embaixo da tabela, só os avisos curtos: contas sem dados e afiliados incompletos.
- **Saldo Ads não aparece no Resumo** — continua no relatório, no CSV e no JSON.
- **Excel**: só a aba Resumo, com as 2 colunas de rótulo e o cabeçalho travados ao rolar; % gravado como
  fração com formato 0,00% (o Numbers e a pré-visualização do Mac multiplicavam um "%" escrito). Os 2
  gráficos de linha (Vendas e % investimento / vendas por grupo) ficam **abaixo** da tabela, e os números
  deles embaixo dos gráficos.
- **HTML**: a mesma tabela; rola de lado com as 2 colunas de rótulo paradas.
- Tela e HTML no celular: a data da semana (e o "Variação (…)") gruda logo depois das 2 colunas paradas,
  então a semana dos números que estão à vista aparece sempre em cima deles (a célula mesclada de 4 colunas
  é mais larga que a tela e a data centrada caía fora dela). No desktop a data continua centrada.
- **MD**: o mesmo Resumo em uma tabela por grupo, as vendas por conta, avisos e notas.
- **CSV**: uma linha por grupo × conta × semana com todas as métricas (inclusive as 6 novas e o Saldo Ads).

## 6. Detalhes das APIs (conferidos ao vivo na Barbosa em 06/10/2026)

- Datas em segundos (epoch) com início 00:00 BRT. `get_time_graph` e `homepage/query` **exigem** fim às
  23:59:59; com 00:00 do dia seguinte respondem `code 5 invalid request`.
- Dinheiro de Ads e saldo: inteiros ÷ 100.000. Afiliados: usar os campos `dis_*`, que já vêm em R$.
  `product/performance`: R$.
- `seller_item_detail` devolve **no máximo 20 itens por página** (mesmo pedindo 50): paginar até
  `total_count`.
- `product/performance` aceita dias com mais de 30 dias e com `category_id=-1` bate exatamente com o painel.
- `seller_daily`: o último dia publicado é o maior `ymd` da lista (o `last_update_time` não serve para isso).
  Às 10:20 de terça só havia até domingo; os afiliados de ontem saem por volta de 12:10–12:50.
- POST levam `x-csrftoken` (cookie `csrftoken`); as rotas levam `SPC_CDS` e `SPC_CDS_VER=2`.

## 7. Como roda

1. **Servidor (worker)**: terça e quinta 16:30 UTC (= 13:30 BRT), se `CONFERENCIA_SHOPEE_CRON=true`, cria a
   execução e uma coleta por conta ativa. O botão **Gerar agora** faz o mesmo.
2. **Executor do Mac** (`apps/executor/src/conferencia.ts`): pede uma coleta por vez e, para cada loja:
   - se o perfil já está aberto por alguém, devolve para o fim da fila (até 3 vezes) e **nunca fecha um perfil
     que não abriu**. As 3 voltas são só do perfil em uso: esperar os afiliados (abaixo) não gasta essa cota;
   - mantém o Mac acordado (`caffeinate`) enquanto coleta;
   - confere o login; 403/429/captcha já nessa primeira chamada → "bloqueada" na hora (sem recarregar nem
     chamar de novo). Se a loja caiu no login e o usuário e a senha já estão preenchidos no perfil, clica
     **Entrar** uma vez — só numa tela de login da própria Shopee (`accounts.shopee.com.br`,
     `seller.shopee.com.br` ou `shopee.com.br`); fora delas, ou se pedir código ou captcha, para e marca
     "deslogada";
   - espera os afiliados do último dia (até 15:00). Se a semana não tem **nenhum** dia de afiliado na lista
     (loja sem venda de afiliado), não há o que esperar: coleta na hora e a loja não entra em "Afiliados
     incompletos";
   - faz as chamadas uma de cada vez, com pausa, e para a loja no primeiro bloqueio (403/429/captcha);
   - não começa loja nova depois das 17:30 (às 18:00 o robô de ads usa os mesmos perfis);
   - se o relógio pular mais de 2 min entre passos (Mac dormiu), descarta a loja e marca "interrompida".
3. **Servidor**: confere a forma dos números de cada loja ao receber (formato errado → recusa com 422, e o
   executor fecha a loja como "erro"). Quando a última coleta chega (ou no prazo), calcula o relatório,
   guarda e, se configurado, avisa no Threema com o link do Excel (um destinatário por vez: falha de rede
   num não faz o outro receber de novo). Se os números de uma loja já guardados derrubarem o cálculo, só
   ela fica sem dados ("erro") e a rodada fecha com o resto.

## 8. Diferenças em relação ao documento original

| Documento | Aqui | Por quê |
|---|---|---|
| Segunda e quinta | Terça e quinta | Pedido do usuário |
| "Últimos 7 dias até ontem" | Terça = semana fechada; quinta = parcial seg–qua | Semanas comparáveis; sem 5 dias repetidos entre os relatórios |
| Eletro pela categoria Shopee | Pela lista de SKUs do DaVinci; categoria e título só de reserva | Decisão do usuário; "Casa e Decoração" é ampla demais |
| `seller_item_detail` em páginas de 50 | Páginas de 20, até `total_count` | A Shopee limita a 20 |
| Vendas pelo painel geral (30 dias) | Soma diária por produto | Mesma fonte para total e eletro, sem limite de 30 dias |
| "A agenda do DaVinci liga e desliga o Ads" | Quem liga e desliga é o robô `marionete` (18–22h) | O texto original está errado |
| Saldo anterior: "±2 dias do início" e "dia seguinte ao fim ±1" | Leitura mais próxima do dia seguinte ao fim, ±1 dia | Uma regra só |

## 9. Mercado Livre e Amazon (07/10/2026)

Decisão do dono: o **mesmo relatório** (a planilha da seção 5, as mesmas semanas da seção 1, as
mesmas regras de grupo, de total e de variação) para o Mercado Livre e a Amazon, **um por
marketplace**. Na tela, Marketing → Conferência ganha a troca **Shopee | Mercado Livre | Amazon**
(`?conf=ml`). As tabelas são as mesmas (`conferencia_shopee_*`, migration 0382): a conta e a
rodada ganharam `plataforma`, e há **uma rodada coletando por plataforma** (a da Shopee, que vai até
17:30–18:00, não segura a do ML).

**Quem coleta.** ML e Amazon são coletados pelo **servidor** (job `conferencia_coletar_servidor` no
worker), sem AdsPower e sem o Mac: o executor do Mac (`/agent/lease`) só recebe coleta da Shopee.
"Gerar agora" cria a rodada e põe o job na fila na hora; a agenda (terça e quinta 13:30 BRT, como a
Shopee) só com `CONFERENCIA_ML_CRON=true` / `CONFERENCIA_AMAZON_CRON=true` (desligadas de fábrica).
O job pega as lojas uma a uma (3 tentativas, 10 min por loja); se a rodada parar (Redis fora,
worker reiniciado), o varredor de 10 em 10 min põe o job na fila de novo. A coleta é `parcial` quando
faltou uma semana ou uma seção que a plataforma tem de trazer (ML: Vendas e Ads; Amazon: Vendas).

**Contas.** Não têm perfil do AdsPower: cada uma é ligada à **integração do DaVinci** (pelo nome,
na migration; depois, em Contas) e à **loja do Bling** de onde saem as Vendas. Conta sem uma das
duas ligações fica desativada, com a observação do que falta.

| Marketplace | Mala | Celular (eletro separado) | Desativadas de início |
|---|---|---|---|
| Mercado Livre | Marquezini, Forpaper, KFA, Poofy | Jlas 2, KFA 2, Inova, Barbosa, Injox, Aguiar, Aguiar 2, Kia, Velasco, Victor MEI, Mega, Dream 2, Zorvex, Mini, Vita | Atlas, Fiore, VR (contas novas, sem integração ainda), Jlas, Eron, Lucas MEI, Counhago |
| Amazon | KFA (loja Bling 204438129), Poofy (206099015) | Kia (204713113) | Nexus (206064394) |

**Vendas** (ML e Amazon): itens dos pedidos do Bling da semana, pela data do pedido (a mesma da aba
Faturamento), valor de tabela dos produtos — **sem frete** e **sem** tirar os descontos e promoções do
pedido (pode ficar acima do que o cliente pagou; na Amazon KFA, 28/09–04/10, R$ 9.156 nos itens
contra R$ 8.566,30 nos totais dos pedidos) —, sem os pedidos que estão cancelados na hora da coleta.
**Eletro** pelo SKU de cada item (a lista de eletro do DaVinci: SKU, categoria do Bling "Eletro…" ou
segmento Eletro), refeito a cada cálculo — "Recalcular" pega categoria nova do produto.

**Ads do ML**: API de Anúncios (Product Ads), dia a dia: Vendas Ads = receita total, Impressões,
Cliques, Invest. Ads = custo, Pedidos Ads = unidades; Conversão Ads = pedidos ÷ cliques. O eletro do
Ads vai anúncio a anúncio pelo vínculo do DaVinci; conta sem os anúncios por item fica com o Ads
inteiro em Celular (com aviso).

**Células sem número por um motivo conhecido** (não é o "—", que continua querendo dizer "não veio"):

| Estado | Onde |
|---|---|
| **não coletado** | afiliados do ML (Venda com Afiliados não tem API; a leitura do painel pelo AdsPower vem depois) |
| **não se aplica** | afiliados da Amazon; Saldo Ads no ML e na Amazon (os dois cobram depois) |
| **aguardando acesso** | Ads da Amazon e o % investimento / vendas dela, até a API de Anúncios da Amazon ser conectada |

O relatório guarda `plataforma` e `estados` (`{métrica: estado}`; a linha "Impressões afiliados"
usa a chave `impressoes_afiliados`). A célula com estado fica sem número em toda linha, total e Geral
— no valor e na Variação — e a tela, o Excel, o HTML, o CSV e o MD escrevem o texto do estado, em
cinza. O % investimento / vendas do ML é só o Ads (os afiliados não entram enquanto não são
coletados). Título e arquivo dizem o marketplace: "Conferência Mercado Livre — …",
`conferencia-ml-<S1>.xlsx`. O aviso no Threema usa o mesmo cadastro "Quem recebe" da Shopee, mas
cada marketplace tem a sua chave — `CONFERENCIA_ML_THREEMA` / `CONFERENCIA_AMAZON_THREEMA`
(desligadas de fábrica): ligar a da Shopee depois do piloto não manda o do ML/Amazon (nem as rodadas
de teste) antes de ele ser aprovado. No ML a linha do aviso diz que o investimento é só do Ads
("invest. Ads R$ 182 (afiliados não coletado) · 17,7% Ads s/ vendas").

**Loja do Bling**: uma por conta em cada marketplace (a mesma loja em duas contas contaria as
vendas duas vezes): a tela recusa (`loja_bling_em_uso`) e a migration, se a busca achar a mesma
loja para duas contas, deixa só na primeira da lista (a outra entra desativada, com a nota).

Código: `services/conferencia_shopee/plataformas.py` (o perfil de cada marketplace),
`servidor.py` (o job), `coletores/` (a interface; um módulo por marketplace).

### 9.1 Como cada número é lido (coletores do servidor)

Tudo no servidor, sem AdsPower; nenhum número de `marketing_metrics` entra (o ML lá ficou zerado de
~15/07 a 07/10 e a Amazon lá era do robô de demonstração): as 4 semanas são lidas de novo a cada
rodada.

**Vendas** (`coletores/vendas_bling.py`, ML e Amazon) — só leitura do banco:

- linhas de item de `bling_orders` com `loja` = a loja do Bling da conta;
- semana pelo dia do pedido (`data`) em Brasília — a mesma data da aba Faturamento;
- valor = `itemvalor × item_quantidade` (os produtos, **sem frete** e sem os descontos/promoções do
  pedido — o `bling_orders` não guarda o desconto; a soma das linhas é o `totalprodutos` do
  pedido); linha sem quantidade conta 1; linha sem valor conta R$ 0,00 e a semana ganha aviso;
- fora: situação **12 (Cancelado)** e `excluido` na hora da coleta. Todo o resto entra — "Em
  digitação" (na Amazon é pedido normal com etiqueta), situação vazia, devolução em andamento;
- item = o SKU da linha (`item_codigo`, que é o SKU do DaVinci); linha sem SKU vira
  "(sem SKU) <descrição>" e o eletro dela sai pelo nome;
- zero só quando dá para confiar nele (falha do Bling nunca vira R$ 0,00):
  - semana que termina depois do pedido mais novo do espelho **inteiro** (de qualquer loja) fica
    **sem** Vendas ("—", coleta `parcial`), com aviso: o espelho do Bling parou;
  - loja que o espelho não conhece (nenhum pedido dela, nunca — loja errada em Contas): nenhuma
    semana tem Vendas ("—"; na Amazon a coleta vira `erro`) e a loja ganha aviso;
  - loja conhecida sem pedido nas 4 semanas: R$ 0,00 com aviso (a data do último pedido dela);
  - S1 zerada com pedido nas anteriores: R$ 0,00 com aviso para conferir o espelho;
  - fora isso, semana sem pedido = R$ 0,00 (não é falha).

**Ads do Mercado Livre** (`coletores/ml.py`), pelo cliente consertado em 07/10/2026
(`services/ml_ads.py`, docs/marketing-ml-ads.md):

- totais: `campaigns/search` com `aggregation_type=DAILY` de S4.inicio a S1.fim (uma chamada; o ML
  guarda 90 dias) e a soma dos dias de cada semana. Semana com um dia **já fechado** que o ML não
  trouxe fica **sem** Ads ("—", com aviso): o ML manda todo dia, até os zerados, então dia faltando
  é resposta incompleta;
- eletro (só contas de Celular): `ad_groups/search` com as métricas de cada semana (uma busca por
  semana). Grupo **ITEM** = o próprio anúncio (MLB…); **FAMILY** (User Products) e **CATALOG** juntam
  variações — os itens deles vêm de `ad_groups/{id}/ads` (uma chamada por grupo com movimento nas
  4 semanas). Item → SKU pelo vínculo **vivo** do DaVinci (`product_links` do ML; o da própria
  integração primeiro, depois o de outra integração; vínculo morto não conta, como na Shopee).
  Grupo com **qualquer** item eletro é eletro (a regra da Shopee). Sem a lista de itens ou sem
  vínculo vivo: eletro pelo título do anúncio (com aviso);
- a busca por anúncio de uma semana falhou (ou acabou o tempo, 6 min por loja para o detalhe): a
  semana fica sem os anúncios e o Ads inteiro dela vai para Celular, com aviso. A soma dos anúncios
  que não fecha com o total da conta (> R$ 1 e > 1%) também vira aviso;
- conta sem Publicidade, token sem o escopo, token recusado na renovação ou API fora: a seção Ads
  fica de fora (a coleta vira `parcial`, o relatório mostra "—") e o motivo vai nos avisos. Nunca
  zero no lugar de erro. Conta de Mala não busca anúncio por anúncio (entra inteira em Mala).

Chamadas por loja de Celular: anunciante (1) + DAILY (1) + 4 buscas de grupos (1 página a cada 50
grupos) + 1 por grupo FAMILY/CATALOG com movimento. O token renovado no meio é gravado na hora
(o refresh_token do ML é de uso único).

**Amazon** (`coletores/amazon.py`): só Vendas por enquanto; sem loja do Bling ligada a coleta vira
`erro`. Quando a API de Anúncios da Amazon for conectada, o Ads entra no mesmo coletor (relatório
`spAdvertisedProduct` diário por SKU anunciado — o SKU da Amazon é o do DaVinci).
