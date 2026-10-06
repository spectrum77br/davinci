# Conferência Shopee

Relatório de marketing da Shopee, **terça e quinta às 13:30 (BRT)**, com as lojas separadas em
**Mala · Celular · Eletro** e comparado com as 3 semanas anteriores. Aparece em **Marketing → Conferência
Shopee** e gera HTML (a própria tela), CSV, MD, JSON e Excel.

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

- Celular = total da conta − eletro. Como total e eletro saem da **mesma chamada** (a soma dos itens bate
  com o total), não há diferença de fonte. Gasto de Ads sem item (ex.: GMV Max da loja) fica em Celular.
- Se o total de uma seção falhou e só os itens vieram (são chamadas separadas), a métrica fica "—" em
  Celular **e** em Eletro: um número só da parte eletro deixaria o Geral pela metade sem avisar.
- Totais de grupo usam **somas**; o % do grupo é Σinvestimento ÷ Σvendas.
- Arredondamento (tela, Excel, CSV, MD, HTML e Threema iguais): o número como se escreve, **meio para
  longe do zero** — 8,25% → 8,3%; 6,35% → 6,4%; R$ 0,125 → R$ 0,13 (é como o Excel mostra a célula).
  O relatório guarda dinheiro e % com 2 casas por essa regra; o % sai dos valores já arredondados.

## 5. Detalhes das APIs (conferidos ao vivo na Barbosa em 06/10/2026)

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

## 6. Como roda

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

## 7. Diferenças em relação ao documento original

| Documento | Aqui | Por quê |
|---|---|---|
| Segunda e quinta | Terça e quinta | Pedido do usuário |
| "Últimos 7 dias até ontem" | Terça = semana fechada; quinta = parcial seg–qua | Semanas comparáveis; sem 5 dias repetidos entre os relatórios |
| Eletro pela categoria Shopee | Pela lista de SKUs do DaVinci; categoria e título só de reserva | Decisão do usuário; "Casa e Decoração" é ampla demais |
| `seller_item_detail` em páginas de 50 | Páginas de 20, até `total_count` | A Shopee limita a 20 |
| Vendas pelo painel geral (30 dias) | Soma diária por produto | Mesma fonte para total e eletro, sem limite de 30 dias |
| "A agenda do DaVinci liga e desliga o Ads" | Quem liga e desliga é o robô `marionete` (18–22h) | O texto original está errado |
| Saldo anterior: "±2 dias do início" e "dia seguinte ao fim ±1" | Leitura mais próxima do dia seguinte ao fim, ±1 dia | Uma regra só |
