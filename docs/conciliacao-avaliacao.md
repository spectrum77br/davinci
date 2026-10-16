# Painel de Conciliação — avaliação e desenho

*14/10/2026. Nada foi implementado nem alterado no sistema. O que vem da documentação oficial do Mercado Livre / Mercado Pago está marcado como **documentado**; o que depende de testar com as contas reais está marcado como **a confirmar**.*

---

## 1. Resumo em 6 linhas

1. **Dá pra fazer, e o DaVinci já tem metade.** A Margem já lê, pedido a pedido, o valor da venda, a tarifa, o frete, o cupom e o estorno do Mercado Livre (e de Shopee, TikTok e Amazon). O que falta é o outro lado: **o que de fato entrou na conta** (extrato) e **a fatura do ML por período** (tudo que foi cobrado no mês, inclusive o que não é de pedido nenhum).
2. **No ML o dinheiro de uma venda tem quatro "versões":** o pedido no Bling, a venda no ML, a fatura do ML e o extrato do Mercado Pago. Conciliar é colocar as quatro lado a lado por pedido e marcar onde não bate.
3. **O extrato existe por API** (documentado): é o "Relatório de liberações" do Mercado Pago. Cada linha diz o tipo do movimento (pagamento liberado, estorno, chargeback, mediação, frete, saque, reserva…), o valor bruto, a tarifa, o frete, o líquido e **o número do pedido/pack**. **A confirmar:** se o token que o DaVinci já tem é aceito nessa API. Plano B pronto: importar o CSV que o próprio Mercado Pago gera.
4. **A fatura do ML também tem API por período** (documentado), com todas as cobranças e bonificações do mês. Cada cobrança diz de qual pedido/envio é e se foi "descontada da venda" ou cobrada à parte — é exatamente a resposta pra "esse desconto veio de onde".
5. **Painel novo "Conciliação"**, com abas por marketplace como na Logística, começando pelo Mercado Livre, com três visões: **Pedidos** (uma linha por pedido do Bling), **Extrato** (cada movimento da conta, com ou sem pedido) e **Fechamento do mês** (totais e a conferência "fatura × detalhes" que o próprio ML recomenda).
6. **Entrega em passos curtos:** sondagem só-leitura (1 dia) → painel v1 com o que já temos + fatura (≈1 semana) → extrato (≈1 semana) → vigia com aviso no Threema → Shopee, TikTok, Amazon, um por vez.

---

## 2. O que é "conciliar" aqui: as quatro versões do dinheiro de uma venda

| Versão | O que ela diz | De onde vem | Já temos? |
|---|---|---|---|
| **Bling** | total do pedido, valor dos produtos e o bloco "taxas" que o Bling anota da plataforma (valor base, custo do frete, comissão); comissão por item | pedidos importados do Bling (`bling_orders`: total, totalprodutos, valorbase, custofrete, taxacomissao, comissão por item). **Não gravamos hoje** o desconto do pedido nem o frete pago pelo cliente (`transporte.frete`) — se a coluna for necessária, a importação do Bling passa a guardar | sim (com essa lacuna) |
| **Venda (ML)** | valor da venda, tarifa de venda por item, custo do frete pro vendedor, cupom que a loja banca, estorno/cancelamento → **líquido previsto** | API de pedidos, envios e descontos do ML | sim (é o que a Margem mostra em "Saldo › Plataforma") |
| **Fatura do ML** | o que o ML cobrou de fato no período: "Cobrança por vender", "Cobrança por Mercado Envios", taxa de parcelamento, Product Ads, e as bonificações (devolução de tarifa quando a venda cai) | API de faturamento por período | em parte: hoje só por pedido, e só pra calcular margem |
| **Extrato (Mercado Pago)** | o que entrou e saiu da conta e **quando**: pagamento liberado (bruto, tarifa, frete, líquido), estorno, chargeback, mediação, reserva por disputa, frete cobrado à parte, saque pro banco | "Relatório de liberações" do Mercado Pago | não |

Com as quatro versões na mesa, conciliar vira responder perguntas simples:

- **Por pedido:** (1) o Bling bate com a venda? (2) o líquido previsto bate com o que foi liberado? (3) já liberou? quando? (4) depois de liberar, saiu dinheiro (estorno, chargeback, mediação)?
- **Pela conta:** toda linha do extrato tem dono (um pedido) ou é classificada como "sem pedido" (saque, fatura do ML, publicidade, reserva, ajuste).
- **Pelo mês:** a fatura do ML (resumo) bate com a soma dos detalhes, e os totais do mês fecham (vendido, tarifas, frete, estornos, liberado, saques, saldo).

---

## 3. O que o DaVinci já tem e dá pra aproveitar

- **Financeiro por pedido** (`marketplace_order_financials` + eventos + conciliação de frete): uma linha por pedido do Bling × plataforma. Pro ML guarda bruto, comissão, frete real (custo do envio), cupom do vendedor, estorno e líquido previsto, com status `posted` (fatura já existe) ou `estimated`. O JSON bruto guarda o pedido do ML, o detalhe da fatura **daquele pedido** (que já inclui a data e a situação da liberação do pagamento) e os pedidos irmãos do pack. É alimentado pelo webhook do Bling, pelo botão "atualizar este pedido" e por varreduras (tentativas, esteira lenta, cancelados às :27).
- **Casamento Bling ↔ ML já resolvido:** `numeroloja` pode ser a venda ou o pack; pack juntado num pedido só ou dividido em vários — a Margem já trata (casa os irmãos por SKU).
- **Raio-X da Margem** (`saldo-detalhe`): já é uma mini-conciliação por pedido, Bling × plataforma (bruto, taxas, frete, desconto, reembolso, líquido e a diferença). A Conciliação estende esse Raio-X com a fatura e o extrato, em vez de inventar outro.
- **Saldo diário das contas** (`valuation_marketplace_saldo_diario`: disponível e a receber por loja × marketplace, preenchido por uma rotina externa que lê o painel): serve de prova dos nove no Fechamento do mês.
- **Campos já reservados e nunca usados** em `marketplace_financial_events`: `posted_at` e `settlement_id` — exatamente "quando entrou" e "em qual repasse".
- **Leitura do pagamento já usada** nos chamados (`/collections/{id}`): data de aprovação, data de liberação, estornos e o caso `bpp_covered` (o ML pagou do próprio bolso, não saiu da conta).
- **Logística** é o modelo de painel por marketplace (abas, filtros, Excel, atualização a cada 30 s).
- **Ouvidoria › Robôs** é o modelo de robô (modos ligado/silencioso/desligado, lista do Threema, ocorrências).
- **Lojas** (`store_info`, `integrations`, `stores`) já ligam pedido do Bling → conta do ML; a Margem usa esse caminho.

O que **não** existe: nenhuma chamada ao Mercado Pago (extrato), nenhuma leitura da fatura por período (só por pedido), nenhum registro do que entrou de fato na conta e nenhum "fechamento do mês".

Duas pegadinhas do que existe, que o desenho precisa respeitar:

- **`posted` não quer dizer "liberado".** Quer dizer "a plataforma publicou o detalhe do pedido". Uma linha `posted` nunca é relida pelo robô; só o webhook do Bling, a varredura de cancelados e alguns gatilhos pontuais a tocam. Bonificação que chega semanas depois (tarifa devolvida numa devolução) não entra. A leitura **diária da fatura por período** corrige isso sem reler pedido a pedido.
- **O botão "atualizar este pedido" da Margem não relê a plataforma**, só reconstrói o snapshot. Na Conciliação o botão equivalente precisa reler de verdade (fatura por pedido + extrato daquele pedido).

---

## 4. De onde vem cada número no Mercado Livre

| Informação | Caminho | Situação |
|---|---|---|
| Venda: valor, tarifa por item, pagamentos, pack | `GET /orders/{id}`, `GET /packs/{id}` | já usamos (Margem) |
| Custo do frete pro vendedor | `GET /shipments/{id}/costs` | já usamos |
| Cupom: quem bancou (loja × ML) | `GET /orders/{id}/discounts` | já usamos |
| Fatura **por pedido/pack**: cobranças + situação da liberação | `GET /billing/integration/group/ML/order/details?order_ids=…` (até 60 por chamada) | já usamos; o ML pede pra **não reler pedido já lido** (é o endpoint que mais toma 429) |
| Fatura **por período**: todas as cobranças e bonificações do mês | `GET /billing/integration/periods/key/AAAA-MM-01/group/ML/details?document_type=BILL` (1.000 por página, paginação por `from_id`) | **documentado**, não usamos |
| Resumo da fatura do mês (total por tipo: venda, envios, publicidade, bonificações, o que foi pago) | `…/periods/key/AAAA-MM-01/summary/details` | documentado, não usamos |
| Períodos e faturas (PDF/XML) | `…/monthly/periods`, `…/periods/key/…/documents` | documentado, não usamos |
| Pagamentos **da fatura** (débito automático na conta) e quais cobranças cada pagamento quitou | `…/group/ML/payment/details`, `/billing/integration/payment/{id}/charges` | documentado, não usamos |
| Movimentações da conta Mercado Pago, por período de fatura | `…/periods/key/…/group/MP/details` | documentado, não usamos |
| Pagamento: aprovado em, liberado em, estornos | `GET /collections/{id}` | já usamos (chamados) |
| **Extrato da conta: Relatório de liberações** | `api.mercadopago.com/v1/account/release_report` (criar por período → listar → baixar CSV); `…/config` (colunas, fuso, frequência); `…/schedule` (geração automática diária) | documentado. **A confirmar:** o token do app do ML serve nesse host? (o `/collections` já traz dado do Mercado Pago com ele, mas é outro domínio e pode exigir permissão de "relatórios") |

Cada linha do Relatório de liberações traz (documentado): data da liberação, id do pagamento, tipo do movimento (`payment`, `refund`, `chargeback`, `dispute`, `mediation`, `shipping`, `shipping_cancel`, `payout` = saque, `reserve_for_dispute`, `reserve_for_refund`…), valor bruto, tarifa (`MP_FEE_AMOUNT` = comissão do Mercado Pago e/ou Mercado Livre), parcelamento, frete, cupom (e a parte do cupom que o vendedor banca), crédito líquido, débito líquido, saldo após o movimento, **ORDER_ID, PACK_ID, SHIPPING_ID**, item e SKU. Aviso da própria documentação: o total do relatório pode não bater com o saldo nem com a fatura (prazos de liberação, banco, intermediação) — por isso o painel precisa mostrar a diferença, não escondê-la.

Regras de uso que o ML impõe (documentado): guardar em cache local; período aberto consultar no máximo 2× por dia, fechado 1×; uma chamada por conta e paginar por `from_id`; nada de lote paralelo; a chave do período é sempre o primeiro dia do mês, mas **o período de faturamento não é o mês calendário** (ex.: 19/09 → 18/10).

---

## 5. O painel

### 5.1 Onde fica e como se organiza

- Menu **Operação**, ao lado de Margem. Permissão nova `conciliacao` (ver / editar), como as outras páginas.
- Abas no topo, como na Logística: **Mercado Livre · Shopee · Amazon · TikTok**. Só a do ML nasce ligada; as outras ficam "em breve" até cada integração entrar.
- Dentro da aba, três visões: **Pedidos** (padrão), **Extrato** e **Fechamento do mês**.
- Filtro de **loja** e de **período** valem pras três visões. O período tem um seletor de "qual data": data do pedido (Bling) ou data da liberação (extrato) — sem isso os números não batem com o que a pessoa vê no Mercado Pago.

### 5.2 Visão "Pedidos" — uma linha por pedido do Bling

Grupos de colunas (no estilo da Margem, com a barra separando cada grupo):

| Grupo | Colunas |
|---|---|
| Identificação | Data · Pedido Bling · Venda ML (ou pack, com os irmãos) · Loja |
| Bling | Total · Produtos · Valor base · Custo frete (Bling) · Comissão (Bling) |
| Venda ML | Valor da venda · Tarifa · Frete · Parcelamento · Cupom (loja) · Estorno · **Líquido previsto** |
| Extrato | Liberado em · Liberado R$ · Saídas depois (estorno/chargeback/mediação) · **Recebido** |
| Conciliação | Diferença Bling × ML · Diferença previsto × recebido · **Situação** · Conferido ✓ · Observação |

Situações (uma por pedido, em linguagem de operação):

- **Bateu** — liberado e a diferença fica dentro da tolerância (centavos).
- **Aguardando liberação** — o ML ainda não liberou; mostra a previsão ("prev. 23/10").
- **Não liberado (vencido)** — a previsão passou (ou enviado há mais de N dias) e nada entrou.
- **Recebido a menos / a mais** — entrou, mas diferente do previsto; quando dá, o painel diz o motivo (frete cobrado maior que o previsto, tarifa maior, parcelamento, cupom).
- **Bling ≠ ML** — o valor do pedido no Bling não bate com a venda (pedido editado, pack, cancelamento parcial).
- **Cancelado** — venda desfeita: previsto 0 e nada a receber (ou devolvido).
- **Estornado depois** — liberou e depois saiu dinheiro (estorno, chargeback, mediação, reserva).
- **Conferido à mão** — alguém marcou como ok e deixou observação.

Em cima, cartões de resumo (como em Devoluções): pedidos, vendido, tarifas + frete, líquido previsto, liberado, diferença, sem liberação, divergentes. Botão Excel. Clicando na linha abre o **Raio-X**: as quatro versões lado a lado e, embaixo, as linhas do extrato e da fatura daquele pedido.

### 5.3 Visão "Extrato" — cada movimento da conta

Colunas: Data · Loja · Tipo (traduzido: Pagamento liberado, Estorno, Chargeback, Mediação, Frete, Frete devolvido, Saque, Reserva por disputa, Fatura do ML, Publicidade, Ajuste…) · Pedido (link pro Bling) ou **"Sem pedido"** · Descrição original · Bruto · Tarifa · Frete · Parcelamento · Cupom · Crédito · Débito · Saldo após · Origem (extrato MP / fatura ML).

Filtros: tipo, com/sem pedido, loja, período. Ações: **Vincular a pedido** (quando o robô não achou) e **Classificar** (sem pedido: saque, fatura do ML, publicidade, ajuste, outro). Excel.

É nessa visão que aparece o "tem um valor descontado e não sei de onde veio": o movimento fica em "Sem pedido" até alguém (ou o robô, cruzando com a fatura do ML) dizer o que é.

### 5.4 Visão "Fechamento do mês" — por loja × mês

Vendas (bruto) · Tarifa de venda · Frete (Mercado Envios) · Parcelamento · Publicidade · Bonificações · Estornos e chargebacks · **Líquido liberado** · Saques · Saldo inicial / final · **Fatura do ML × soma dos detalhes** (✔ / ✖, a conferência que o próprio ML recomenda) · período de faturamento do ML (ex.: 19/09 → 18/10) ao lado do mês calendário · aberto / fechado · Excel.

---

## 6. Como o robô alimenta

- **Fatura do ML por período** (1× por dia por loja, mais uma passada à tarde no período aberto): baixa todas as cobranças e bonificações do período, pagina por `from_id`, grava cada uma com o pedido/envio/pack a que pertence e se foi "descontada da venda". Relê o período anterior até ele fechar e até as faturas (PDF) existirem.
- **Extrato do Mercado Pago** (1× por dia por loja): pede o relatório do dia anterior (ou liga a geração automática diária uma vez e só baixa os arquivos novos), grava linha a linha, sem duplicar (loja + id do pagamento + tipo + data).
- **Recalcular a conciliação** (a cada hora e logo depois de cada sync): só pros pedidos tocados. Reaproveita o financeiro da Margem; quando o pedido ainda não tem financeiro, dispara o sync existente.
- **Vigia de Conciliação** (Ouvidoria › Robôs, nasce silencioso): venda enviada há mais de N dias sem liberação; recebido a menos acima de R$ X; movimento sem pedido acima de R$ X; estorno / chargeback / mediação novo; fatura ≠ detalhes; extrato não baixado há mais de 2 dias. Aviso no Threema como os outros vigias.

Cuidados: o ML pede cache e proíbe lote paralelo (nunca reler pedido já lido; uma chamada por loja por dia); o token do ML troca a cada renovação e é de uso único — o job usa o caminho já blindado do Atendimento, não renova "na mão".

---

## 7. Casos difíceis que o desenho precisa tratar desde o começo

- **Pack / carrinho:** o pagamento é do pack inteiro; o Bling pode ter juntado tudo num pedido ou dividido em vários. Conciliar no nível do pack e ratear nos pedidos do Bling (a Margem já casa os irmãos por SKU).
- **Pagamento com dois cartões:** duas liberações pro mesmo pedido.
- **Liberação em parcelas:** o relatório separa parcelas liquidadas de pendentes; somar.
- **Taxa de parcelamento:** o comprador paga a mais e o ML cobra o acréscimo; efeito ≈ 0 pro vendedor. Não pode virar "recebido a menos".
- **Cancelamento / estorno:** venda desfeita → líquido 0, bonificação de tarifa e frete na fatura, `refund` no extrato. `bpp_covered` (o ML pagou do próprio bolso) não sai da conta.
- **Mediação, chargeback, reserva por disputa:** saída depois da liberação ou saldo bloqueado → "Estornado depois" / "Reserva".
- **Frete:** pode vir descontado na própria liberação ou como linha "shipping" separada; a diferença frete do anúncio × frete real a Margem já mede.
- **Cupom:** do ML (reembolsado, não é custo) × da loja (`EFFECTIVE_COUPON_AMOUNT`); a Margem já separa.
- **Flex:** bonificação de envio entra como crédito sem venda nova.
- **Publicidade, assinaturas, fatura do ML paga por débito automático:** "sem pedido". Dá pra casar com os pagamentos da fatura (`payment/details`) e dizer "isso é a fatura do ML de setembro".
- **Período de faturamento ≠ mês calendário.**
- **Três datas diferentes:** liberação (quando ficou disponível), aprovação (quando o cliente pagou) e data do pedido no Bling.
- **Retenções fiscais** (`TAXES_AMOUNT`) no Brasil normalmente 0; manter a coluna.

---

## 8. Depois do Mercado Livre (mesmo modelo, fontes diferentes)

- **Shopee:** o "escrow" por pedido já é lido pela Margem; o extrato vem do `get_wallet_transaction_list` (tipo, pedido, valor) e os repasses ao banco do `get_payout_detail`.
- **TikTok:** extratos por período (`statements`) e por pedido (já lido), repasses (`payments`) e saques (`withdrawals`).
- **Amazon:** eventos financeiros por pedido (já lidos; a KFA segue com 403 pendente) e os grupos de repasse quinzenal (settlement).
- **Magalu / Shein / Temu / AliExpress:** sem API de extrato conhecida → importar o relatório do portal (o mesmo importador do plano B).

---

## 9. Passos (cada um termina esperando o seu OK)

**Passo 1 — Sondagem só-leitura (1 dia)**
- *Você:* de **uma** loja do ML, baixa e me manda: Mercado Pago › Relatórios › **Liberações** (CSV, setembro) e Mercado Livre › Faturamento › **Detalhes de faturamento** (Excel, mesmo período). E me diz quem vai usar o painel.
- *Eu:* um endpoint de admin só-leitura (`/api/conciliacao/sonda`) que, com o token de uma loja, lista os relatórios e a configuração do Mercado Pago e lê períodos, resumo e cinco detalhes da fatura do ML. Deploy, rodo numa loja, mostro o retorno aqui. Nada é gravado. No fim sabemos se o token serve pro extrato e como são os dados reais.

**Passo 2 — Painel v1, Mercado Livre (≈1 semana)**
Página, permissão, aba ML, visão Pedidos com Bling × Venda × Fatura, "liberado em" vindo da fatura por pedido, sync diário da fatura por período, Raio-X (estendendo o da Margem), Excel. Já aparecem aqui as situações Bling ≠ ML, Aguardando liberação, Cancelado e Conferido à mão; as que dependem do extrato ficam pro passo 3.

**Passo 3 — Extrato (≈1 semana)**
Job do Relatório de liberações (ou importação do CSV, se o token não servir), visão Extrato, situações "recebido a menos / a mais" e "sem pedido", Fechamento do mês.

**Passo 4 — Vigia de Conciliação + ajustes do uso real (alguns dias)**

**Passo 5 — Shopee, depois TikTok, depois Amazon (≈1 semana cada)**

---

## 10. Perguntas que só você responde

1. **Quem vai usar** e o que essa pessoa faz hoje pra conferir (baixa o Relatório de liberações? confere em planilha? com que frequência?).
2. **Uma linha por pedido do Bling** (recomendo: é como a Logística e a Margem pensam) ou por venda do ML?
3. **Extrato pela API** (robô diário) ou começar pela **importação do CSV**? Recomendo API se o token servir, CSV como plano B — e o importador é útil de qualquer jeito pra meses antigos.
4. Posso **mexer na configuração do Relatório de liberações** das contas pela API (colunas e geração automática)? Isso muda também o relatório que vocês baixam à mão no painel do Mercado Pago.
5. **Desde quando** conciliar (últimos 3 meses? desde setembro?).

---

## 11. Apêndice técnico (pra quem for implementar)

### Tabelas novas (proposta)

- `conciliacao_movimentos` — cada movimento da conta: plataforma, loja (`integration_id`), origem (`mp_release_report` | `ml_billing` | `shopee_wallet` | `tiktok_statement` | `amazon_finances` | `importado`), id externo (único por loja + origem), data, tipo normalizado, descrição original, `pedido_marketplace`, `pack_id`, `shipping_id`, `pedido_bling` (resolvido), bruto, tarifa, frete, parcelamento, cupom, crédito, débito, saldo após, `raw` JSONB, classificação manual, vínculo manual.
- `conciliacao_pedidos` — uma linha por `pedido_bling` × plataforma: os quatro blocos de valores, diferenças, situação, liberado em, conferido por / em, observação, `recalculado_em`. Reconstruída por job (como o snapshot da Margem).
- `conciliacao_periodos` — loja × período de faturamento: resumo da fatura por tipo, soma dos detalhes, totais do extrato, fechado/aberto, última leitura.
- `conciliacao_sync` — controle por loja / origem / período / arquivo (último `from_id`, último arquivo baixado, erro).
- Reaproveitar, não duplicar: `marketplace_order_financials` continua sendo a "Venda" (líquido previsto); a Conciliação lê dela pelo mesmo caminho da view (`bling_id` + `numeroloja`). `marketplace_financial_events.posted_at`/`settlement_id` podem receber a data da liberação e o id do pagamento quando o extrato casar.

### Endpoints DaVinci

`GET /api/conciliacao?plataforma=ml&visao=pedidos|extrato|fechamento&loja=&de=&ate=&data=pedido|liberacao&situacao=` (paginado no servidor, como a Margem) · `GET /api/conciliacao/pedidos/{pedido}/raio-x` · `PATCH /api/conciliacao/pedidos/{pedido}` (conferido, observação) · `PATCH /api/conciliacao/movimentos/{id}` (vínculo, classificação) · `POST /api/conciliacao/export.xlsx` · `POST /api/conciliacao/importar` (CSV do Mercado Pago / Excel da fatura) · `GET /api/conciliacao/sonda` (admin, passo 1).

### Jobs (worker, arq)

`conciliacao_ml_fatura_sync` (06:10 e 18:10 UTC-3) · `conciliacao_ml_extrato_sync` (07:10) · `conciliacao_rebuild` (:40) · `vigia_conciliacao_tick` (Ouvidoria, modos ligado/silencioso/desligado).

### Regras de casamento (ML)

1. `bling_orders.numeroloja` → venda ou pack (404 em `/orders` ⇒ pack). Pack: usar `raw.pack_matched_order_ids` da Margem.
2. Extrato → pedido: `ORDER_ID`, senão `PACK_ID`, senão `EXTERNAL_REFERENCE`; linha de `refund`/`chargeback`/`mediation` herda o pedido pelo `SOURCE_ID` (mesmo pagamento) ou pelo `METADATA`.
3. Fatura → pedido: `sales_info[].order_id`; frete: `shipping_info.shipping_id`/`pack_id`; sem pedido (Product Ads etc.): classificar pelo `detail_sub_type`.
4. Recebido = Σ créditos de `payment` do(s) pagamento(s) do pedido − Σ débitos posteriores ligados ao mesmo pagamento.
5. Tolerância padrão R$ 0,05 (parametrizável no robô).

### O que a documentação confirma (links)

- Faturamento ML: relatórios de faturamento, boas práticas, provisões (detalhes por período), relatórios de pagamentos — developers.mercadolivre.com.br (seção "Relatórios de Faturamento").
- Relatório de liberações: `mercadopago.com.br/developers/pt/docs/reports/released-money/introduction` e `…/api`; colunas em `…/reports/released-money/report-fields`.
- Referência dos endpoints de relatório: `mercadopago.com/developers/en/reference/releases-report/…` (create-report, consult-report, consult-configurations, enable-automatic-generation).

### Sub-tipos da fatura do ML vistos na documentação

`CV` cobrança por venda · `CXD` cobrança por Mercado Envios · `BXD` bonificação de Mercado Envios · `PADS` Product Ads · `CFONPN` taxa de parcelamento (acréscimo pago pelo comprador). A lista completa não está na documentação pública; o passo 1 lê os sub-tipos reais das contas.

---

## 12. Conversa de 14/10 à tarde — o que mudou no desenho

Maquetes: `docs/img/conciliacao-maquete.png` (Pedidos × valores soltos) e `docs/img/conciliacao-extrato-do-dia.png` (Extrato do dia + histórico do pedido).

**Decisões do Vinicius**
- A rotina principal é **conciliar o dia anterior, linha a linha**: todo movimento do extrato precisa achar um pedido ou receber uma classificação. A visão "Extrato do dia" passa a ser a tela de trabalho; "Pedidos" é a segunda lente.
- O dia tem **"Fechar o dia"**: só fecha com 100% das linhas resolvidas (vinculada, classificada ou divergência justificada). Ao fechar, as linhas travam e o fechamento do mês soma os dias. Prova dos nove: saldo inicial + entradas − saídas = saldo final = saldo informado pelo Mercado Pago.
- Ao clicar num pedido abre o **histórico completo em ordem**: venda aprovada (ML), importado no Bling, etiqueta/NF, entregue, cobranças da fatura do ML, liberação na conta, estornos/reservas/mediações, divergências e chamados. Cada linha com data, valor e de onde veio (cor por origem).
- Colunas novas na visão Pedidos: **Custo do produto · Reembolso · Sobrou · Margem final**. Sobrou = Recebido − custo − reembolso; margem = sobrou ÷ custo (mesma conta da Margem). Reembolso vem do painel Reembolso (`refunds`), que a Margem já desconta — a "planilha de reembolso" precisa ser esse painel, ou ser importada nele, pra não lançar duas vezes.
- Visual mais analítico, no espírito da conciliação bancária do Protheus: grade densa, débito/crédito/saldo acumulado, legenda de cores, totais no rodapé, botões Conciliar/Classificar/Desfazer/Fechar o dia.
- Uso duplo: conferir pedido a pedido **e** saber o resultado da empresa. O Fechamento do mês vira um "Resultado" por loja → marketplace → empresa, com previsto × realizado.

**Ideias novas levantadas (não decididas)**
1. **Resultado da empresa**: Vendas − tarifas − frete − cupons − estornos − publicidade − custo dos produtos − reembolsos = resultado das vendas (tudo com dinheiro recebido de fato). Pra chegar no resultado da empresa, um bloco mensal de despesas fora dos marketplaces (fixas, embalagem, imposto por CNPJ), lançado à mão ou puxado do contas a pagar do Bling.
2. **Previsão de caixa**: o extrato diz quando cada venda libera; somando, "quanto vai liberar nos próximos 7/15/30 dias" por loja. Junto com o saldo diário das contas (já lido) e os saques, "quanto dinheiro temos e onde está".
3. **Diferença de frete vira chamado**: a divergência "frete cobrado maior que o do anúncio" abre o pedido de revisão de peso/custo no ML direto da linha (via Chamados / IA de Chamado).
4. **Tarifa fora da tabela**: comparar a tarifa cobrada com a esperada pelo tipo de anúncio/categoria (a Tabela de preços já tem comissões) e marcar cobrança errada.
5. **Resultado real por produto / anúncio / conta**: a mesma base, agrupada por SKU — quais produtos dão prejuízo depois de frete, devolução e reembolso.
6. **Saque conciliado com o banco**: cada payout casado com o extrato bancário (OFX) — fecha o circuito até a conta da empresa.
7. **Trava e auditoria**: dia/mês fechado não se mexe; tudo que foi vinculado ou classificado à mão fica no histórico com quem e quando.

**Etapas do pedido (pergunta "como sei o que está finalizado, e se o marketplace desconta um mês depois?")** — maquete `docs/img/conciliacao-etapas.png`
- Coluna **Etapa** por pedido: 1 Aguardando liberação → 2 Conciliado (bateu) ou 2b Com pendência → 3 Finalizado → 4 Reaberto → 3 de novo.
- **Finalizar:** o que bateu finaliza sozinho 7 dias depois, sem novidade (prazo parametrizável). O que teve pendência finaliza à mão, com motivo (chamado aberto, ajuste no Bling, prejuízo aceito). Botão "Finalizar selecionados" pra lote. Fica registrado quem/quando/motivo.
- **Reabrir:** "finalizado" nunca tranca o pedido pro robô. Qualquer linha nova do extrato com o número do pedido (chargeback, estorno, mediação, reserva, bonificação) reabre na hora, com motivo e valor; o pedido entra na fila "Reabertos"; o vigia avisa no Threema; o extrato do dia marca a linha como "reabre o pedido X (finalizado em Y por Z)".
- **Mês fechado não muda:** o movimento tardio entra no fechamento do mês em que aconteceu, numa linha "Ajustes de meses anteriores" com a lista dos pedidos. Na aba Pedidos o pedido mostra o resultado da vida inteira; no Fechamento cada mês mostra o dinheiro que se mexeu nele; as duas contas batem no acumulado.
- Visual aprovado: o padrão "grade à esquerda + detalhe à direita" (Extrato do dia) vale pro painel inteiro; a aba Pedidos foi refeita nesse padrão (`docs/img/conciliacao-maquete.png`).

**Decidido 14/10 (fim do dia):** o que bateu finaliza sozinho 7 dias depois, sem novidade.

**Tela de entrada "Resumo"** — maquete `docs/img/conciliacao-resumo.png`
- Uma linha por loja, agrupada por marketplace: dia de ontem (fechado sozinho / fechado por alguém / não fechou / extrato não lido), movimentos, pendentes, pedidos com pendência, reabertos, não liberados vencidos, saldo bate?, último extrato lido, fatura do ML, situação em uma frase ("agir: 5 linhas sem dono, saldo não bate").
- Cartões no topo: dias fechados, movimentos pendentes, reabertos, não liberados, extrato atrasado, saldo bate.
- Lado direito "Onde agir hoje": lista ordenada do que mais dói, cada item com botão pra tela certa (extrato da loja, reabertos, pedidos, integração). É a mesma lista que o vigia manda no Threema às 07:00.
- Rodapé: mês atual por marketplace e Empresa (vendido, tarifas+frete, recebido, ajustes de meses anteriores, custo, reembolsos, sobrou, margem, % finalizados).
- "Fechado sozinho" = 100% das linhas casaram, saldo bateu, ninguém mexeu; loja sem pendência some da lista de ação.

**Respostas do Vinicius (14/10, fim do dia):** reembolso vem do painel Reembolso do DaVinci; começar hoje mesmo com uma loja do Mercado Livre, depois uma da Shopee, depois uma da Amazon, e replicar pras outras lojas do mesmo marketplace; despesas fora dos marketplaces ficam pra fase seguinte, primeiro o resultado pedido a pedido.

---

## 13. Sondagem de 14/10/2026 (jlas2) — o que a API respondeu de verdade

Rodada às 18:19 (BRT) com a chave que o DaVinci já tem da loja jlas2 (seller 2363933120, JLAS COMERCIO, reputação verde/prata, 328 vendas em 60 dias). Só leitura.

**A porta do extrato abre com a chave do ML.** `api.mercadopago.com/v1/account/release_report/list` → 200 (lista vazia: nunca gerado por API) e `…/config` → 404 "config_not_found_for_user" (a conta ainda não tem configuração de relatório). `settlement_report/list` → 200 (vazio). Ou seja: não precisa conectar o Mercado Pago à parte; falta só **criar a configuração** (colunas) e **pedir o relatório** do período — as duas são escritas na conta (só de configuração), a decidir com o Vinicius.

**A fatura do ML por período funciona e tem histórico.** `monthly/periods` → 13 períodos disponíveis. O período de faturamento da jlas2 vai do **dia 23 ao dia 22** (ex.: 23/09→22/10 = chave 2026-10-01, aberto, R$ 27.866,81, R$ 1.283,06 em aberto; 23/08→22/09 fechado, R$ 39.278,50; 23/07→22/08 fechado, R$ 16.771,75). Detalhes: **1.049 cobranças no período aberto** e **1.141 no anterior**, paginadas por `from_id`.

**Como a tarifa aparece de verdade (MLB):** a "tarifa de venda" de um pedido vem quebrada em três cobranças, todas "descontadas da venda" (`debited_from_operation = YES`):
- `CVVML` "Custo por vender no Mercado Livre" (ex.: 82,77)
- `CVVPRC` "Custo por cobrar no Mercado Pago" (ex.: 27,59)
- `CVVFN` "Taxa de parcelamento" (ex.: 72,69; sem NF-e)
Soma = `sales_info.sale_fee.net` (183,05; `gross` 186,91 e `rebate` 3,86 = desconto de campanha). O **frete** vem como `CDSB` "Tarifa do Mercado Envios" (marketplace SHIPPING) e, nesse pedido, **não descontado da venda** (`NO`, 27,90 com "Desconto geral" sobre 46,50) — cobrado na fatura. **Product Ads** vem como `PADS` (marketplace MCLICS), sem pedido (`sales_info = null`), não descontado da venda. Também apareceu uma cobrança com `status = BONUS_ON_BILL` ("Cancelado neste mês"), `debited_from_operation = INAPPLICABLE`: cobrança anulada no mesmo mês.

**Limite de uso:** a API de faturamento devolveu 429 "Rate limit exceeded: **5 requests per minute**" depois da 5ª chamada seguida. O job diário precisa espaçar as chamadas (uma página de 1.000 detalhes por chamada é suficiente: um período inteiro cabe em 2 chamadas).

**Dois parâmetros que a documentação não deixa claro:** `summary/details` exige `document_type=BILL` (sem ele, 422); o filtro de bonificações é `detail_type=BONUS` em maiúsculas (`bonus` dá 422). Corrigidos na sondagem, que ganhou `parte=1|2|3` pra caber no limite de 5/min.

**Parte 2 (18:24, as quatro chamadas 200):**
- **Bonificações** do mês fechado: **189 linhas**, cada uma com o pedido e com `charge_bonified_id` apontando pra cobrança original. Sub-tipos: `BVVML` "Cancelamento do Custo por vender no Mercado Livre" (158,41), `BVVPRC` "Cancelamento do Custo por cobrar no Mercado Pago" (0,89), `BDSB` "Cancelamento da tarifa por envios" (24,45, SHIPPING), `BVVFN` "Cancelamento da taxa de parcelamento" (52,08). É a devolução da tarifa quando a venda cai — a parte que a Margem hoje não vê.
- **Grupo MP** (`group/MP/details`): 200 com **zero** linhas no período — esse grupo é dos serviços do próprio Mercado Pago (maquininha, QR); não entra no desenho.
- **Pagamentos da fatura** do mês fechado: 6 pagamentos. A fatura do ML é paga por **débito automático na conta Mercado Pago** (`payment_type = collections_forced` "Débito automático por fatura vencida", `payment_method = account_money`, ex.: R$ 2.268,55 em 29/09 07:56, id `000g6i7t62`) e tem **nota de crédito** (`CREDIT_NOTE` "Estorno", R$ 702,63, "em processo de faturamento"). Esse é o débito "sem pedido" que vai aparecer no extrato e que o painel casa pela fatura.

**Conclusão da sondagem:** tudo que o desenho precisa do lado do ML já responde com a chave atual. Do lado do Mercado Pago, a porta abre; pra ver as linhas do extrato faltam duas escritas de configuração na conta da loja: criar a configuração do Relatório de liberações (colunas, fuso, separador) e pedir um relatório de um período (ex.: 01/10→14/10). A loja nunca gerou relatório por API nem pelo painel (lista vazia), então não há rotina manual pra atrapalhar.

### Extrato real da jlas2 (Relatório de liberações, 01/10 → 14/10, lido 14/10 18:38)

Com o OK do Vinicius, a configuração do relatório foi criada na conta (36 colunas, fuso GMT-03, sem agendamento) e um relatório do período foi pedido: ficou pronto em menos de 2 minutos. **422 linhas**: 1 saldo inicial, 420 movimentos (`release`), 1 total.

| Tipo (`DESCRIPTION`) | Linhas | Crédito | Débito | Com nº de pedido |
|---|---|---|---|---|
| `payment` — venda liberada | 95 | 68.330,63 | — | 95 de 95 |
| `mediation` — reclamação perdida, valor devolvido ao comprador | 77 | — | 46.623,76 | 77 de 77 |
| `refund` — **tarifas devolvidas** quando a venda cai (crédito) | 47 | 9.609,84 | — | 47 de 47 |
| `reserve_for_dispute` — reserva por reclamação (débito ao abrir, crédito ao fechar) | 162 | 50.734,75 | 49.538,56 | 162 de 162 |
| `reserve_for_bpp_shipping_return` — reserva do frete de devolução | 7 | 808,43 | 146,70 | 7 de 7 |
| `reserve_for_refund` / `mediation_cancel` | 4 / 2 | 97,80 / 97,80 | 97,80 / — | todos |
| `shipping` — crédito de frete | 2 | 65,98 | — | 2 de 2 |
| `reserve_for_payout` — reserva antes do saque (débito e crédito) | 16 | 39.895,00 | 39.895,00 | nenhum (não é de pedido) |
| `payout` — **saque pra conta bancária** | 8 | — | 39.895,00 | nenhum |

**Prova dos nove fecha:** 9.147,81 (saldo inicial) + 68.330,63 − 46.623,76 + 9.609,84 + 1.196,19 (reservas liberadas) + 661,73 + 97,80 + 65,98 − 39.895,00 = **2.591,22** = o líquido que a linha `total` do relatório traz. É exatamente a conta do rodapé da tela "Extrato do dia".

**O que uma linha de venda traz:** `payment` de 880,98 (cartão 10×): `MP_FEE_AMOUNT` −92,36 (custo por vender + custo por cobrar), `FINANCING_FEE_AMOUNT` −115,64 (taxa de parcelamento), `SHIPPING_FEE_AMOUNT` −62,33 (frete) → `NET_CREDIT_AMOUNT` **610,65**. Bate com os três pedaços da fatura (CVVML + CVVPRC = MP_FEE; CVVFN = FINANCING_FEE; CDSB = SHIPPING_FEE). `SOURCE_ID` = id do pagamento, `EXTERNAL_REFERENCE` = nº da venda, `ORDER_ID` e `PACK_ID` preenchidos (pack presente nas vendas de carrinho), `SHIPPING_ID`, parcelas e meio de pagamento.

**Como uma reclamação perdida aparece:** no mesmo pagamento, `mediation` (débito do valor cheio, ex.: −732,35) e, 5 segundos depois, `refund` (crédito das tarifas: +92,36 de tarifa +24,45 de frete = 116,81). Ou seja, perder a mediação devolve a loja a zero naquela venda: sai o bruto, voltam as tarifas. A reserva (`reserve_for_dispute`) entra como débito quando a reclamação abre e volta como crédito quando fecha — não é dinheiro perdido, é dinheiro travado.

**Atenção pro desenho:** a coluna `METADATA` traz um JSON com vírgulas (`payments_group_uuid`, `fee_discount`); sem aspas na célula, as colunas depois dela (`BALANCE_AMOUNT`, `ITEM_ID`, `PRODUCT_SKU`, `SALE_DETAIL`…) deslocam. Os valores, o nº do pedido e o pack vêm ANTES da `METADATA`, então os totais acima não são afetados. A confirmar na linha crua se é falta de aspas (solução: separador `;` na configuração) ou outro formato.

**Leitura do negócio:** em duas semanas a jlas2 liberou R$ 68,3 mil de vendas e perdeu R$ 46,6 mil em mediações — o painel vai expor isso pedido a pedido no primeiro dia.

**Linha crua conferida (14/10 18:52):** o arquivo é CSV com vírgula; as colunas de JSON (`METADATA` = `"{"fee_discount": "2.85", "device_id": …, "payments_group_uuid": …}"` e `OPERATION_TAGS` = `"[{"logistic_type":"drop_off"}]"`) vêm entre aspas **sem escapar as aspas de dentro** (CSV inválido); `SALE_DETAIL` vem no padrão (`"""título"""`). Solução adotada na sondagem: um divisor próprio que lê `"{…}"`/`"[…]"` até fechar o colchete — não depende de trocar o separador na conta. Outras colunas úteis vistas na linha de venda: `TRANSACTION_APPROVAL_DATE` (pago 11/09, liberado 01/10 = 20 dias), `SHIPMENT_MODE` me2, `ITEM_ID`, **`PRODUCT_SKU` = o SKU do anúncio (ex.: `dg057.ci+a001.ci`)**, `SALE_DETAIL` = título, `PAYMENT_METHOD_TYPE` credit_card, `BALANCE_AMOUNT` = saldo após a linha.

**Passo 1 concluído (14/10, 19:00).** Com o leitor próprio, o arquivo inteiro lê certo: 422 linhas, saldo após cada linha correto (ex.: 611,46 depois da primeira venda liberada), `METADATA` e `PRODUCT_SKU` íntegros. Ficou na conta Mercado Pago da jlas2 a configuração `davinci-liberacoes` (sem agendamento) e um relatório de 01/10→14/10 na lista (aparece no painel do Mercado Pago em Relatórios › Liberações). Nada foi gravado no DaVinci.

**O que o passo 2 (painel v1) pode assumir como certo:**
1. Extrato por API, sem conectar o Mercado Pago à parte; pedir um relatório por dia (ou por janela) e ler o CSV com o leitor próprio.
2. Toda linha de venda, mediação, estorno de tarifa e reserva traz `ORDER_ID` (e `PACK_ID` no carrinho); saques não têm. Casamento com o Bling pelo `numeroloja`.
3. Líquido da venda no extrato = `GROSS − MP_FEE − FINANCING_FEE − SHIPPING_FEE` (coupon à parte), e bate com a fatura (CVVML + CVVPRC, CVVFN, CDSB).
4. Reclamação perdida = `mediation` (−bruto) + `refund` (+tarifas) no mesmo `SOURCE_ID`; reserva por disputa = par débito/crédito, dinheiro travado.
5. Fatura do ML por período: 1 página de 1.000 por chamada, no máximo 5 chamadas por minuto; bonificações com `detail_type=BONUS`; período da loja vai do dia 23 ao 22.
6. Prova dos nove: saldo inicial + créditos − débitos = líquido da linha `total` (2.591,22 no período lido).

---

## 14. Passo 2a (14/10, noite) — a base: tabelas e leituras diárias

Com o OK do Vinicius ("Ok" ao passo 2), entrou a base do painel, sem tela ainda:

- **Migration 0397**: `conciliacao_lojas` (jlas2 ligada, início 01/10/2026), `conciliacao_movimentos` (livro-razão), `conciliacao_pedidos` (uma linha por pedido do Bling), `conciliacao_sync` (controle das leituras) e `conciliacao_dias` (fechamento do dia, ainda sem uso).
- **Uma rodada por dia** (Vinicius, 14/10: "é tudo do dia anterior, uma vez só de madrugada"): às 06:00 o robô pede ao Mercado Pago o extrato de ontem e, a cada 10 min até 08:50, lê o arquivo assim que fica pronto; às 06:12 lê as cobranças novas da fatura. Cada leitura recalcula os pedidos ao terminar. Depois disso nada muda até o dia seguinte, a não ser pelo botão "sincronizar agora".
- **Leitura do extrato** (`services/conciliacao_extrato.py`): se há janela pedida e o arquivo ficou pronto, lê e grava os movimentos (sem duplicar: identidade = hash da linha); senão pede a próxima janela, do dia seguinte ao último lido até ontem (a primeira pede desde o início da loja). Cria a configuração do relatório na conta se faltar. Linha de saque já nasce classificada; linha de venda/mediação/estorno/reserva guarda o nº da venda, o pack e, quando o Bling já importou, o pedido do Bling.
- **Leitura da fatura** (`services/conciliacao_fatura.py`): cobranças e bonificações do período aberto e do anterior, incremental (`from_id` = último id lido), uma página de 1.000 por chamada e **13 s entre chamadas** (limite de 5/min do ML); resumo do período aberto guardado. Product Ads nasce classificado "publicidade".
- **Recálculo** (`services/conciliacao_calculo.py`): no fim de cada leitura (e no botão). Pedidos da loja desde o início; venda = o que a Margem já leu; recebido = créditos − débitos do extrato sem as reservas; situação e etapa pelas regras da seção 12 (tolerância R$ 0,05; bateu finaliza sozinho em 7 dias; movimento novo depois de finalizado reabre); sobrou = recebido (ou previsto) − custo − reembolso do painel Reembolso. Linha de pedido anterior ao início vira `anterior_ao_inicio`.
- **Endpoints**: `GET /api/conciliacao/status` (o painel "cru": leituras, movimentos por tipo, pedidos por situação/etapa) e `POST /api/conciliacao/lojas/{conta}/sincronizar` (rodada completa no worker, trava por loja).
- Testes: `tests/test_conciliacao_base.py` (normalização do extrato e da fatura com os dados reais da sondagem, ingestão sem duplicar, janelas do extrato, leitura incremental com pausa, recálculo com as situações, etapas) e `tests/test_conciliacao_sonda.py`.

**Primeiras rodadas em produção (14/10, 19:55 e 20:03 BRT), jlas2:**
- Fatura: período 23/09→22/10 com 824 cobranças + 228 bonificações; período 23/08→22/09 com 952 + 189. **18 sub-tipos** vistos: CVVML (custo por vender, 45.036,15), CVVPRC (custo por cobrar no MP, 5.643,67), CDSB (Mercado Envios, 14.690,91), PADS (Product Ads, 348 linhas, 3.552,20), CVVFN/CVVFNU (taxa de parcelamento da loja, 4.582,28), **CFONPN (110 linhas, 15.874,19) = acréscimo de parcelamento pago pelo comprador** (entra e sai: não é custo da loja), CDSDB/CXDED/CXDID (envio de devolução), e as bonificações BVVML/BVVPRC/BDSB/BVVFN/BFONPN/… (25.402,91 devolvidos à loja no total).
- Extrato 01→13/10: 410 movimentos — 90 vendas liberadas (65.126,09), 76 mediações (45.795,77), 46 estornos de tarifa, 160 linhas de reserva por disputa, 7 saques (36.991,00). Toda linha de venda achou o pedido do Bling.
- **Limite do ML é dividido com a Margem**: a primeira rodada tomou 429 na 3ª chamada (a Margem lê a fatura pedido a pedido com a mesma chave). O robô agora espera um minuto e tenta de novo; a segunda rodada levou 3m45 e leu tudo.
- **Efeito da data de início 01/10**: o ML libera ~20 dias depois da venda, então as liberações de 01→13/10 são de vendas de setembro — ficam classificadas "anterior ao início". Os 156 pedidos de outubro estão "aguardando" (110) ou "cancelado" (46); os primeiros "bateu" aparecem quando as vendas de outubro liberarem, por volta de 20/10.

---

## 15. Passo 2b (14/10, noite) — a tela: aba Pedidos + Raio-X

- **Página `/conciliacao`** (menu Operação, ao lado da Margem), permissão `conciliacao` (ver/editar). Migration 0398 dá ver+editar a quem já edita a Margem (admin vê tudo).
- **Abas** por marketplace (só Mercado Livre ligada; Shopee, Amazon e TikTok aparecem desligadas "entra depois") e visões Pedidos · Extrato do dia · Resumo (as duas últimas vêm no 2c/2d).
- **Filtros**: loja, período (de/até) por data do pedido ou da liberação, situação, etapa, busca por pedido/venda. Linha de status com a barra "fechados de N" e os contadores por situação e etapa (clicáveis = filtram). No canto, quando foi a última leitura do extrato, da fatura e o último recálculo, e o erro se houver.
- **Grade** no padrão da maquete: Identificação · Bling · Venda ML · Extrato (entrou de fato) · Dif. · Resultado de verdade (custo, reembolso, sobrou, margem; em cinza "prev." enquanto não libera) · Situação / motivo / etapa (✓ quando conferido). Rodapé com os totais do filtro e paginação de 100.
- **Raio-X** à direita, ao clicar: as quatro versões lado a lado (Bling · Venda · Fatura · Extrato: valor, tarifa, frete, parcelamento, cupom, estorno/bonificação, líquido; a linha do frete fica vermelha quando o cobrado difere do previsto), os movimentos do pedido em ordem (extrato e fatura, com "cobrada à parte" quando a fatura não descontou da venda) e o resultado. Quem edita: observação, marcar/desmarcar conferido, Finalizar (motivo obrigatório quando há pendência) e Reabrir.
- **Botões**: Recarregar, Excel (mesmo filtro, até 20 mil linhas) e, pra quem edita, "Sincronizar agora" (enfileira fatura + extrato + recálculo da loja).
- **API**: `GET /api/conciliacao/lojas`, `GET /api/conciliacao/pedidos` (paginado, totais e contagens do filtro), `GET /api/conciliacao/pedidos/{pedido}` (Raio-X), `PATCH /api/conciliacao/pedidos/{pedido}` (observação, conferido, etapa finalizado/reaberto com motivo), `POST /api/conciliacao/export.xlsx`.

---

## 16. Passo 2c (14/10, noite) — Extrato do dia: vincular, classificar, fechar o dia

- **Visão "Extrato do dia"** na página Conciliação: navega por dia (◀ ▶, "ontem"), mostra cada movimento do extrato do Mercado Pago daquele dia na ordem certa (em empate de horário, a ordem é a que encadeia o saldo), com hora, nº do pagamento, tipo, pedido do Bling (clicável: leva à aba Pedidos), venda ML, bruto, tarifa, frete, parcelamento, débito, crédito, saldo após, o status da conciliação (casou sozinho · vinculado à mão · sem pedido classificado · pedido anterior ao início · pendente · divergente) e quem/quando.
- **Rodapé = prova dos nove**: saldo inicial (o saldo da primeira linha menos o movimento dela) + créditos − débitos = saldo final calculado, comparado com o saldo informado pelo Mercado Pago (última linha). Diferença 0,00 ✔.
- **Painel da direita**: detalhe do movimento; pra linha sem dono, campo "nº do pedido Bling" + **sugestões do robô** (mesmo pagamento, mesmo nº de venda/pack, mesmo valor até 45 dias antes) e os botões de classificação (Saque, Fatura do ML, Publicidade, Pedido anterior ao início, Ajuste, Outro); pra linha vinculada, o pedido com a situação e "Desvincular". Vincular/classificar recalcula a loja na hora.
- **Fechar o dia** só com 100% das linhas resolvidas (409 com a contagem de pendentes); grava `conciliacao_dias` com os totais e quem fechou; "Reabrir" desfaz. Filtro "Mostrar": todos · só pendentes · só divergentes · só sem pedido.
- **API**: `GET /api/conciliacao/extrato?loja=&dia=&mostrar=&q=`, `GET /api/conciliacao/dias?loja=&de=&ate=` (linhas, pendentes, fechado por dia — base do Resumo), `GET /api/conciliacao/movimentos/{id}/sugestoes`, `PATCH /api/conciliacao/movimentos/{id}` (pedido_bling | classificacao | desvincular), `POST /api/conciliacao/dias/{dia}/fechar` e `/reabrir`.
- Raio-X: a situação do Bling agora aparece pelo nome (era o id).

---

## 17. Passo 2d (14/10, noite) — Resumo (tela de entrada) e Fechamento do mês

- **Resumo** é a tela de entrada da Conciliação (visão padrão ao abrir). Uma linha por loja ligada, agrupada por marketplace, com o **dia de ontem** (ou o dia escolhido no seletor): `fechado HH:MM · quem` / `pronto pra fechar` (100% das linhas com destino e saldo batendo, falta só clicar) / `não fechou` (linha sem dono ou saldo diferente) / `extrato não lido` (nenhuma linha e o extrato está há mais de 36 h sem leitura) / `sem movimento`. Colunas: movimentos, sem dono, pedidos com pendência, reabertos, não liberados (vencidos), saldo (✔ bate / ✖ diferença), extrato lido (vermelho quando atrasado), fatura ML, situação numa frase (verde/amarelo/vermelho). Clique na loja abre o extrato daquele dia; clique nos contadores abre os pedidos já filtrados.
- **Cartões** no topo: dia fechado (x de n lojas), movimentos sem dono (em quantas lojas), pedidos reabertos, não liberados vencidos, extrato atrasado, saldo bate (x de n com movimento).
- **"Onde agir hoje"**: lista em ordem de gravidade — linhas sem dono / saldo não bate (abre o extrato), extrato não lido (Sincronizar, pra quem edita), reabertos, vencidos, recebidos a menos, estornados depois (abrem os pedidos filtrados), e "dia pronto pra fechar". É a mesma lista que o Vigia de Conciliação vai mandar no Threema (fase seguinte).
- **Rodapé do mês** (do dia 1º até o dia escolhido), por marketplace e da empresa: pedidos, vendido, tarifas + frete, recebido, ajustes de meses anteriores (movimento do mês de pedido de antes do dia 1º ou anterior ao início), custo, reembolsos, sobrou, margem, finalizados (%).
- **Fechamento do mês** (visão própria, loja × mês): três contas lado a lado — **Vendas** pela data do pedido (vendido, tarifa, frete, parcelamento, cupom, líquido previsto, recebido, saiu depois, custo, reembolso, sobrou, margem, finalizados, contagem por situação); **Caixa** pela data do movimento no extrato (vendas liberadas, mediações, estornos, chargebacks, frete, outros, ajustes de meses anteriores = resultado de caixa; reservas e saques à parte = movimento líquido) com a **prova dos nove do mês**: saldo inicial do primeiro dia + movimento líquido = saldo final informado no último dia; **Fatura do ML** do período cuja chave é o mês (o resumo que o ML dá: cobranças e bonificações por tipo, total, já cobrado/em aberto) × a soma dos detalhes lidos linha a linha por sub-tipo, com ✔/✖ "resumo = detalhes" (tolerância R$ 1). Embaixo, os dias do mês com movimento: linhas, sem dono, créditos, débitos, fechado por quem (clique abre o extrato do dia).
- **API**: `GET /api/conciliacao/resumo?dia=` e `GET /api/conciliacao/fechamento?loja=&mes=AAAA-MM`. Testes em `tests/test_conciliacao_base.py` (resumo com dia aberto/pronto/fechado/sem movimento/extrato atrasado; fechamento com vendas, caixa com ajuste de mês anterior, fatura resumo × detalhes, mês vazio, mês inválido).
- Cabeçalho da página: Recarregar e Excel só na aba Pedidos (Resumo, Extrato e Fechamento têm o próprio Recarregar); "Sincronizar agora" some no Resumo (lá cada loja tem o botão na lista de agir).
- **Fica pra fase seguinte**: Vigia de Conciliação (Threema 07:00 com a lista "onde agir"), Shopee → TikTok → Amazon, despesas fora dos marketplaces, e a leitura do extrato automática a partir de 15/10 (06:00) — o primeiro dia a cair na rotina de madrugada é 14/10, no dia 15.

**No ar em 15/10 00:50 (commits b65b64ab, fe6f0172 e o de polimento), conferido com a jlas2:**
- Resumo de 13/10: 62 movimentos, 0 sem dono, saldo bate (2.904,39 = 2.904,39) → "pronto pra fechar"; "Onde agir" com um item só (fechar o dia). Mês até 13/10: 148 pedidos, vendido 136.803,29, recebido 0,00 (as vendas de outubro ainda não liberaram), ajustes de meses anteriores +28.889,66 (66 linhas = liberações e mediações de vendas de setembro).
- Fechamento de outubro: **caixa** com prova dos nove batendo (saldo 01/10 9.147,81 + movimento −6.243,42 = 2.904,39 em 13/10); "vendas liberadas 31.796,56 / mediações −40.053,16 / estornos +8.228,61 / frete 27,99" somam ZERO — são as 39 vendas de outubro canceladas por reclamação, em que o ML libera e tira no mesmo dia; por isso "Recebido (39 liberados) 0,00". Tudo que entrou de verdade no mês é de setembro (28.889,66). **Fatura** 2026-10 (23/09→22/10): resumo de cobranças 39.428,79 = soma dos 824 detalhes ✔; bonificações do resumo 11.131,25 × detalhes 13.760,93 (diferença não investigada — o resumo pode abater bonificação anulada no mesmo mês); já descontado das vendas 27.014,48, em aberto 1.283,06 (Product Ads, cobrado à parte).
- Dois acertos ao ver a tela: (1) os componentes das visões carregavam com `await` no setup e trocar do Resumo pro Fechamento quebrava o DOM (NotFoundError insertBefore) — agora carregam no onMounted; (2) o dia/mês passam a ser definidos no navegador (no servidor o relógio é UTC: depois das 21h "ontem" vinha um dia à frente). "Sobrou" no Fechamento e no rodapé do Resumo avisa que pedido sem liberação entra pelo previsto.

**Item 6 (15/10, madrugada, "Faz o 6 que é simples ajuste"), commit bc9dc111:**
- Excel do Fechamento (botão na visão, `POST /api/conciliacao/fechamento.xlsx?loja=&mes=`): abas Vendas, Caixa, Fatura ML e Dias com os mesmos números da tela.
- "Liberado" passou a significar **liberou e ficou**: o cancelado por reclamação (o ML libera e tira no mesmo dia) não conta mais como liberado, nem na aba Pedidos nem no Fechamento; o Fechamento mostra quantos foram "liberados e devolvidos". No caixa do mês esses cancelados ganharam linha própria (entrou / saiu / líquido) em vez de inflar "vendas liberadas".
- Cada linha da fatura passa a guardar de que período veio (`raw._periodo`), pra separar período da fatura de data da cobrança daqui pra frente.
- Sondagem das bonificações (`GET /api/conciliacao/sonda/bonus?conta=jlas2&periodo=2026-10-01`, só leitura, 2 chamadas ao ML): resultado abaixo, na seção 18.

---

## 18. Sondagem Shopee (15/10, madrugada) — o que a API dá de dinheiro

Autorização do Vinicius: "pega qualquer conta Shopee que tiver aberta e pode fazer a leitura". Loja escolhida: **Jlas** (a Shopee da mesma família da jlas2). Só leitura: `GET /api/conciliacao/sonda/shopee?conta=Jlas&dias=7`. Tudo com a chave que o DaVinci já tem (nenhuma configuração nova, ao contrário do Mercado Pago).

**O que abriu, 7 dias (07→14/10):**

| Fonte | Rota | Resultado |
|---|---|---|
| Carteira (= o extrato) | `POST /api/v2/payment/get_wallet_transaction_list` (page_size 100, create_time_from/to) | **200**. 62 lançamentos, com `current_balance` (saldo após cada linha → prova dos nove igual ao Mercado Pago), `order_sn` na renda de pedido, `refund_sn`, `transaction_type`, `money_flow`, `reason`/`description`, `status`, `withdrawal_id`. |
| Liberações (escrow) | `GET /api/v2/payment/get_escrow_list` (release_time_from/to) | **200**. 51 liberações, 97.627,47 — bate com a soma das rendas da carteira. Só `order_sn`, `payout_amount`, `escrow_release_time`. |
| Detalhe da liberação | `GET /api/v2/payment/get_escrow_detail` (order_sn) | **200**. A conta inteira do pedido: `buyer_total_amount`, `escrow_amount`, `commission_fee`, `service_fee`, `net_commission_fee`, `net_service_fee` (lista por regra), `ams_commission_fee` (afiliados), frete (`buyer_paid_shipping_fee`, `actual_shipping_fee`, `final_shipping_fee`), vouchers (Shopee × vendedor), `pix_discount`, `seller_product_rebate`, itens com `model_sku`, `return_order_sn_list`. É o que a Margem já lê. |
| Repasses pro banco | `POST /api/v2/payment/get_payout_info` | `error_param`: pede `cursor` ("" na primeira página). Corrigido na sonda, a conferir na próxima rodada. |
| Repasses (cross-border) | `GET /api/v2/payment/get_payout_detail` | `error_param`: "only applicable for cross border shop" — não serve pra loja nacional. |

**Tipos de lançamento vistos na carteira (7 dias):** `ESCROW_VERIFIED_ADD` 50× (+97.627,47, renda do pedido, todos com `order_sn`), `WITHDRAWAL_CREATED` 4× (−88.875,73, saque pedido) e `WITHDRAWAL_COMPLETED` 4× (0,00, só a confirmação), `ADJUSTMENT_CENTER_DEDUCT` 3× (−259,78, ex. "Cobrança de taxa referente ao atingimento de Faixa 2 de Penalização"), `SPM_DEDUCT` 1× (−3.000,00, dedução de anúncios/Shopee Ads — confirmar). `transaction_tab_type` separa renda de pedido (`wallet_order_income`) de ajustes (`wallet_adjustment_filter`).

**O que isso significa pro painel Shopee:**
- As quatro versões existem como no ML: Bling · venda (escrow detail, que a Margem já lê) · **carteira** (a renda do pedido é a liberação; ajustes, penalidades, Ads e saques vêm na mesma lista, com saldo após) · não há "fatura" separada — as tarifas já vêm descontadas na renda do pedido, e cobranças avulsas (Ads, penalidade) são linhas da carteira.
- Dá pra fazer **Extrato do dia** igual ao do Mercado Pago, lendo a carteira por `create_time` (dia anterior, sobreposição de um dia) com `transaction_id` como chave; prova dos nove com `current_balance`. Reembolsos/devoluções devem aparecer como lançamentos com `refund_sn` (não houve nenhum nos 7 dias — conferir com uma janela maior).
- **Limites vistos nas rodadas de 14 e 31 dias:** a carteira aceita no máximo ~15 dias por chamada (31 dias → `wallet.time_invalid: time period too large`) e devolve 100 por página com `more` (14 dias → 100 linhas e `more=true`: 63 rendas de pedido = 121.980,26, 24 ajustes = −370,16, 1 `SPM_DEDUCT` de −3.000,00, 6 saques = −114.850,13); a lista de liberações aceita 31 dias (100 por página, `more`). `get_payout_info` e `get_payout_detail`, mesmo com `cursor` e janela de 15 dias, respondem "only applicable for cross border shop": **pra loja nacional o repasse é o `WITHDRAWAL_CREATED`/`WITHDRAWAL_COMPLETED` da própria carteira** (o saque pedido e a confirmação, como o `payout` do Mercado Pago). Em 14 dias não apareceu nenhuma linha com `refund_sn`: devolução na Shopee tende a sair antes da liberação (a renda do pedido já vem líquida) — conferir num mês inteiro quando o painel ler dia a dia.
- **Leitura diária proposta (igual ao ML):** de madrugada, carteira do dia anterior (janela de 1 dia + sobreposição, paginando por `page_no` até `more=false`), chave `transaction_id`; a renda do pedido liga ao pedido do Bling pelo `order_sn` (= `numeroloja`); o `escrow_detail` do pedido vira a versão "venda" (a Margem já lê). Prova dos nove com `current_balance`.

**Bonificações do ML, resumo × detalhes (sondagem `sonda/bonus`, jlas2, período 2026-10 = 23/09→22/10):**
- O que gravamos é **exatamente** o que o ML devolve agora: 228 bonificações, 13.760,93, nenhuma a mais nem a menos (por `detail_id`). A leitura está certa.
- A diferença é **dentro do ML**: o resumo da fatura (`summary/details`) lista 11.131,25 de bonificações, os detalhes do mesmo período somam 13.760,93 (2.629,68 a mais nos detalhes, em todos os sub-tipos: BVVML +1.373,42, BFONPN +637,43, BDSB +407,80, BVVPRC +145,83, BVVFN +65,20). Todas as 228 têm `charge_bonified_id` (a cobrança que estão devolvendo) e `debited_from_operation = INAPPLICABLE`; 59 foram criadas em setembro (23→30/09) e 169 em outubro.
- **Confirmado** (`sonda/bonus?so_banco=true`, cruzando cada bonificação com a cobrança que ela devolve): as 193 bonificações cuja cobrança original é **desta fatura** somam **11.131,25 = o resumo do ML**, sub-tipo por sub-tipo, ao centavo (BDSB 1.959,52, BVVML 5.914,88, BFONPN 1.983,69, BVVPRC 753,92, BVVFN 413,42, BVVFNU 8,02, BDSDB 48,90, BXDED 48,90). As outras 35 (2.629,68) devolvem cobranças de **faturas anteriores**: 32 de cobranças de 13→22/09 (a fatura 2026-09) e 3 de cobranças mais antigas que nem temos gravadas. **Regra do ML:** o resumo da fatura só abate a bonificação que anula cobrança da própria fatura; a que devolve cobrança de fatura já fechada é crédito real pra loja, mas aparece fora do resumo. O Fechamento passou a conferir "resumo × bonificações desta fatura" (✔) e a mostrar à parte "bonificações devolvendo cobrança de fatura anterior".


---

## 19. Ajustes de 15/10 de manhã (pedidos do Vinicius ao ver a tela)

- **Bling fora da tela.** "Nunca bate, o Bling sempre errado": a coluna Bling saiu da grade da aba Pedidos e a versão Bling saiu do Raio-X (ficam venda · fatura · extrato). A diferença Bling × venda continua gravada (`dif_bling_venda`) mas não vira mais situação ("Bling ≠ venda" deixou de existir no filtro e no recálculo). A situação do pedido no Bling (Em aberto, Atendido…) continua no cabeçalho do Raio-X.
- **Cancelado = custo zero.** Venda desfeita não leva custo de produto (o produto não saiu ou voltou): custo 0, sobrou = o que entrou − reembolso, margem em branco. Vale no próximo recálculo (madrugada, ou "Sincronizar agora").
- **Rodapé de somas da aba Pedidos saiu** (Pedidos / Vendido / Tarifas + frete / Líquido / Recebido / Custo / Sobrou / Margem): total é coisa do Resumo. A aba Pedidos é pra trabalhar linha a linha.
- **Fechamento do mês escondido por enquanto** (o botão saiu da barra de visões; o componente e a API ficam prontos pra voltar).

- **Caso 301418 (venda 2000018802788964, cancelada por reclamação), lido no relatório original:** o comprador pagou em DOIS pagamentos (700,00 cartão + 32,35 Pix); o MP liberou os dois (581,75 + 32,35) e, na reclamação ganha pelo comprador, devolveu os 732,35 em FATIAS, na medida em que o dinheiro entrava na conta (0,35 · 118,25 · 463,15 · 32,35 · 86,25 · 32,00), cada fatia em três linhas (`reserve_for_dispute` −X, `reserve_for_dispute` +X, `mediation` −X), mais o `refund` +118,25 das tarifas devolvidas à loja. 21 linhas no extrato, zero no fim; na fatura do ML, 4 cobranças e 4 cancelamentos iguais. Dois acertos: (1) cancelado não tem "saiu depois" (a última fatia caía 2 s depois da liberação do Pix e virava "−32,00"); (2) o Raio-X esconde as reservas por padrão (pares que somam zero), mostra de qual pagamento é cada linha quando há mais de um, e tem o botão **original** com a linha completa do relatório do MP (ou o detalhe da fatura do ML).

- **Caso 300415 ("frete 24,45 na venda, 55,44 na fatura"), 15/10:** os dois estão certos. O ML põe o frete que o COMPRADOR pagou (30,99) dentro do bruto do pagamento (880,99 = 850,00 + 30,99) e desconta o frete INTEIRO do Mercado Envios (55,44); a venda guarda só a parte da loja (24,45 = 55,44 − 30,99). O Raio-X passou a mostrar "Produtos", "+ Frete pago pelo comprador", "= Total pago", "Frete cobrado (Mercado Envios)" nas três versões e "Frete líquido pra loja"; o motivo de diferença compara líquido com líquido. De quebra: o "cupom" do extrato era o COUPON_AMOUNT (174,46), que soma o cupom bancado pelo ML; o certo é o EFFECTIVE_COUPON_AMOUNT (16,00), o que sai do bolso da loja — corrigido na leitura e nas linhas já gravadas (manutenção no recálculo). E a fatura do Mercado Envios vinha com `debited_from_operation = NO` mesmo com o frete descontado na liberação: o rótulo "cobrada à parte" só aparece quando o extrato não descontou o frete.

- **Pedido dele de 15/10 de manhã, pronto mas AINDA NÃO em produção ("não precisa subir ainda, tem mais alterações"):** (1) aba Pedidos ganha a coluna **Extras** = valor − tarifa − frete − líquido previsto (cupom da loja, parcelamento, outros), com a composição ao passar o mouse; Tarifa e Frete continuam separados porque ele tira Excel; o Excel ganha a mesma coluna. (2) O Raio-X troca "Venda ML · Fatura ML · Extrato" por **Previsto × Real × Diferença** (maquete em `docs/img/conciliacao-raiox-v2.png`): Produtos, + frete pago pelo comprador, = total pago, − tarifa, − frete inteiro, − parcelamento, − cupom da loja, ± depois da liberação, − cobranças à parte na fatura, = líquido; a diferença fica em vermelho na linha certa; em cancelado não compara. A fatura só aparece onde cobra à parte; tudo continua em "Movimentos deste pedido".

- **Extrato do dia, dois consertos (15/10, pronto, sem subir):** (1) **Desvincular não fazia nada**: a linha ficava sem pedido por um segundo e o recálculo religava pelo nº da venda, só trocando o rótulo pra "vinculado à mão". Agora o robô respeita a decisão da pessoa: linha desvinculada (ou classificada sem pedido) não é religada nem contada no pedido; ela fica "pendente · desvinculada à mão por X" até alguém vincular ou classificar. (2) **Dia fechado continuava editável**: a API passa a recusar vincular/classificar/desvincular em linha de dia fechado (409 `dia_fechado`, com quem fechou) e a tela esconde os botões e explica que é preciso Reabrir.
- **Extrato × fatura, em uma frase (pergunta dele):** o extrato é o dinheiro (toda entrada e saída da conta do Mercado Pago, com saldo); a fatura é a conta de tarifas que o Mercado Livre apresenta por período (23→22), e quase toda cobrança dela o ML já desconta dentro da liberação da venda, por isso os números se repetem. A visão "Extrato do dia" mostra SÓ o extrato; a fatura aparece no Raio-X do pedido ("Movimentos deste pedido", origem fatura) e no Fechamento. O que ele vê na tela é, linha a linha, o **Relatório de liberações** do Mercado Pago (Seu negócio › Relatórios › Liberações); a tela "Atividade" do MP agrupa diferente e não mostra as reservas.


---

## 20. Passo 3 (15/10, manhã) — Shopee entra: a carteira é o extrato

OK do Vinicius: "tem meu ok vamos fazer ... pegamos do dia 01". Loja **Jlas** (Shopee), início 01/10/2026 (migration 0400).

- **Leitor** `conciliacao_shopee.run_carteira_sync`: lê `get_wallet_transaction_list` um dia por vez (fuso de Brasília, do último dia lido até ontem, com sobreposição de um dia; `transaction_id` é a chave única), paginando por `page_no` até `more=false`. Cada lançamento vira um movimento de origem `shopee_carteira`: renda do pedido (`ESCROW_VERIFIED_ADD`) = venda liberada, com o `order_sn` casando com o `numeroloja` do Bling; saques (`WITHDRAWAL_CREATED`/`COMPLETED`) já classificados como Saque; penalidades e ajustes (`ADJUSTMENT_CENTER_*`) como Ajuste; Ads (`SPM_*`) como Publicidade. `current_balance` vira o saldo após a linha → prova dos nove igual ao Mercado Pago.
- **Rodada**: o mesmo `sincronizar_loja` escolhe pela plataforma: ML = fatura + relatório do MP; Shopee = carteira (não tem fatura). Mesmos crons de madrugada e o mesmo "Sincronizar agora".
- **Recálculo e telas**: iguais. "Venda" da Shopee = o `escrow_detail` que a Margem já lê (valor, tarifa, frete, cupom, líquido); a carteira só traz o líquido, então no Raio-X a coluna "Real (carteira da Shopee)" só preenche o líquido e o comparativo compara líquido previsto × líquido real. A aba Shopee mostra só as lojas Shopee; Extrato do dia diz "carteira da Shopee" e "saldo informado pela Shopee".
- **Testes**: `tests/test_conciliacao_shopee.py` (mapeamento dos tipos, leitura dia a dia sem duplicar, Extrato do dia fechando a conta).
- **A conferir com dados reais**: devoluções (nos 14 dias da sondagem não apareceu `refund_sn`), o rótulo exato dos tipos que ainda não vimos (`ESCROW_VERIFIED_DEDUCT`?), e se o `order_sn` da carteira bate com o `numeroloja` do Bling em 100% dos casos.

**Primeira leitura real da Jlas (15/10 09:27 BRT):** carteira de 01/10 a 14/10 lida em 13 s (12 dias com movimento; 11 e 12/10 vazios), 0 linhas sem dono; 14/10 com 13 lançamentos e prova dos nove batendo (45.621,96 − 45.871,96 + 18.077,64 = 17.827,64 = saldo da Shopee). Pedidos desde 01/10: 139 (30 bateu, 92 aguardando, 5 recebido a menos, 12 sem venda = a Margem ainda não leu o escrow). Os 5 "a menos" são diferenças de 16,51 a 20,52 entre o líquido que a Margem calculou e o que a carteira pagou (ex. 301039: 1.604,51 × 1.583,99) — a investigar no detalhe do escrow (provável tarifa que o escrow abre e a carteira já desconta); o motivo deixou de dizer "frete cobrado 0,00", porque a carteira não abre tarifa/frete.

**Os 5 "recebido a menos" da Jlas, resolvidos (15/10 ~10:00 BRT):** a Shopee só põe a comissão de **Afiliados** (`order_ams_commission_fee`) e a taxa de serviço final no escrow na hora de liberar; a Margem tinha lido dias antes. Diferenças fechavam ao centavo: 301267 e 301248 16,51 (14,48 + 2,03), 301039 20,52 (18,00 + 2,52), 300823 20,58 (18,05 + 2,53), 300858 73,04 (64,07 + 8,97). A carteira estava certa. Conserto: a cada rodada da Shopee, toda renda de pedido cuja leitura da Margem é mais velha que a liberação faz a Margem reler o escrow (`reler_escrow_liberados`, uma chamada por pedido, ~12 s cada pelo caminho da Margem). Depois da releitura: os 5 viraram "bateu" com a tarifa nova (717,53 / 294,52 / 271,00); Jlas = 35 bateu, 77 aguardando, 27 venda não lida. Sondagem que mostrou isso: `GET /sonda/shopee-escrow?conta=Jlas` (escrow gravado × agora × carteira). Efeito colateral bom: a Margem passa a mostrar o lucro certo nesses pedidos.
- Pedidos dele na sequência (prontos, sem subir): rodapé do mês do Resumo escondido; Raio-X da Shopee com o "Real" preenchido pelo escrow da liberação e rótulos da Shopee; proposta do bloco "Dinheiro nas plataformas" (maquete `docs/img/conciliacao-dinheiro.png`) aguardando OK.


---

## 21. 15/10, fim da manhã — vigia no Threema, dia fecha sozinho, cancelado da Shopee

OK do Vinicius: "1 pode fazer · 2 pode criar na lista do robô e deixa somente para enviar ao cairo · 3 ok pode fechar · Amazon por último, TikTok antes".

- **Vigia de Conciliação** (`vigia_conciliacao`, Ouvidoria › Robôs, área Margem, 07:00 BRT = 10:00 UTC, depois da rodada de madrugada): pega a lista "Onde agir hoje" do Resumo (a mesma função `montar_resumo`) e registra cada item como ocorrência (linha sem dono / saldo não bate / extrato não lido / reaberto / vencido / recebido a menos / estornado depois); a Ouvidoria manda UMA mensagem no Threema com o pendente e fecha sozinha o que sumiu. Item "dia pronto pra fechar" não vira ocorrência. Destinatário: migration 0401 cadastra o robô já com o Threema do usuário "cairo…" (Admin › Usuários); se não achar, nasce `silencioso` (registra, não avisa) — nunca cai na lista geral. O link da ocorrência abre o painel no lugar certo (`/conciliacao?loja=&visao=extrato&dia=` ou `visao=pedidos&situacao=|etapa=`).
- **Dia fecha sozinho** (`fechar_dias_prontos`, no fim de cada rodada): dia com todas as linhas com destino e saldo batendo é fechado "pelo robô" (mesma regra do botão). Reabrir continua à mão. Dia com linha sem dono ou saldo diferente fica aberto e aparece no Resumo e no vigia.
- **Cancelado pelo Bling**: pedido com situação "Cancelado" no Bling, sem venda com valor na plataforma e sem nenhum dinheiro, vira "Cancelado" (custo 0, finaliza de cara). Resolve os cancelados da Shopee, que nunca ganham escrow (15 dos 27 "venda não lida" da Jlas).

- **Raio-X da Shopee = tela "Detalhes de Renda" da Shopee (15/10, pedido 301438):** a Margem guarda as taxas BRUTAS (722,36) e o desconto de tabela (5.078), e a tela da Shopee mostra Preço de venda 4.925 → − subsídio da Shopee no produto 417 = Subtotal dos produtos 4.508 → ± frete da loja 0 → − cupons da loja 3 → − taxas e encargos 354,85 (comissão 220,32 + serviço 85,04 + afiliados 49 + proteção 0,49, já líquidas do subsídio) = Renda 4.150,15. Agora `conta_shopee` (a partir do escrow guardado) alimenta a aba Pedidos (Valor = preço de venda, Tarifa = taxas líquidas, Frete = parte da loja, Extras = subsídio + cupons) e o Raio-X traz exatamente essas linhas.


---

## 22. Sondagem TikTok (15/10, 12:20 BRT) — a loja que mais vende: " Mini"

`GET /api/conciliacao/sonda/tiktok?dias=30` (só leitura; sem `conta` escolhe a loja TikTok com mais vendas lidas pela Margem em 30 dias). Tudo com a chave que o DaVinci já tem (a Margem já lê o financeiro por pedido na mesma API).

| Fonte | Rota (Finance API v202309) | Resultado em 30 dias |
|---|---|---|
| Extratos (statements) | `GET /finance/202309/statements` (statement_time_ge/lt, page 50) | **200**. 30 extratos, um por dia, todos `PAID`, 456.429,82 liquidados. Campos: id, statement_time, revenue_amount, fee_amount, shipping_cost_amount, adjustment_amount, net_sales_amount, settlement_amount, payment_id, payment_status, payment_time. |
| Transações de um extrato | `GET /finance/202309/statements/{id}/statement_transactions` (page 100) | **200**. 14 linhas (tipo `ORDER`, todas com `order_id`), soma = o `settlement_amount` do extrato (9.835,09). ~60 campos por linha: gross_sales, seller_discount, platform_discount, customer_payment, platform_commission, affiliate_commission, transaction_fee, shipping (cobrado, pago pelo cliente, subsídio), refunds, adjustment, settlement_amount. |
| Transações de um pedido | `GET /finance/202309/orders/{order_id}/statement_transactions` | **200** (é o que a Margem já lê). |
| Repasses pro banco (payments) | `GET /finance/202309/payments` (create_time_ge/lt) | **200**. 22 repasses, todos `PAID`, 447.875,99; cada um é a liquidação de UM extrato (`payment_id` do extrato = `id` do repasse; ex. 9.835,09), com `bank_account` e `paid_time`. |

**Como a TikTok funciona (diferente do MP e da Shopee):** não há carteira com saldo. Todo dia a TikTok fecha um **extrato** com as vendas liquidadas naquele dia (uma transação por pedido, com a conta aberta campo a campo) e manda o total pro banco como **um repasse**. Então a prova dos nove do dia é: soma das transações = `settlement_amount` do extrato = valor do repasse pago.

**Proposta pro painel TikTok (passo seguinte, com OK):** ler de madrugada os extratos de ontem e as transações deles (uma linha por pedido, casando `order_id` com o `numeroloja` do Bling) + os repasses; Extrato do dia com a prova "transações = extrato = repasse" no lugar do saldo; a venda (previsto) é o financeiro que a Margem já lê (o mesmo endpoint por pedido) — e pra pedido ainda não liquidado existe `GET /finance/202507/orders/unsettled` (a Margem já usa) pra prever. Telas iguais, aba TikTok.

## 23. TikTok no painel (15/10, tarde) — loja Mini desde 01/10 (commit 25cfbeed)

OK do Vinicius: "faz sentido, pode fazer o tiktok desde 01/10". Entrou no mesmo molde do ML e da Shopee, com a diferença que a sondagem (seção 22) mostrou: **a TikTok não tem carteira com saldo**.

**Como o robô lê (todo dia às 06:00, junto com as outras lojas):**

| O quê | De onde | Vira no painel |
|---|---|---|
| Extrato do dia (`statement`, só `PAID`) | `GET /finance/202309/statements` | a chave de leitura (`conciliacao_sync`, um por extrato; lido não relê) |
| Transações do extrato (uma por pedido) | `GET /finance/202309/statements/{id}/statement_transactions` | uma linha por pedido: crédito = liquidação (`settlement_amount`); negativa = **estorno**; tipo ≠ ORDER = **ajuste da TikTok**. `order_id` casa com o `numeroloja` do Bling |
| Repasse pro banco | `GET /finance/202309/payments` (mesmo `payment_id` do extrato) | uma linha **Saque** "Repasse pro banco ***conta · pago dd/mm hh:mm", classificada sozinha |

- **Saldo sintético**: o extrato começa em 0, cada pedido sobe, o repasse zera. O rodapé do Extrato do dia vira *Início do extrato · Pedidos liquidados · Repasse pro banco · Pedidos − repasse · Tem que dar · Diferença* — a prova dos nove é **pedidos = extrato = repasse**. Dia com diferença não fecha sozinho e aparece no "Onde agir".
- **Dia do extrato = dia do banco**: a TikTok fecha o extrato à meia-noite UTC (= 21:00 BRT da véspera) e paga ~00:20 BRT. O painel ancora as linhas em **00:00 BRT da data UTC do extrato**: o extrato `statement_time` 14/10 00:00 UTC (9.835,09) é o dia **14/10**, o mesmo dia em que o repasse cai no banco ("pago 14/10 00:23"). Assim o "extrato de ontem" já está lá às 06:00 (primeira versão usava 21:00 da véspera e o dia de ontem ficava vazio até a TikTok publicar o próximo — mig 0403 deslocou as linhas e reabriu os dias pro robô fechar de novo).
- **Liquidação zerada sem pedido no Bling** (08/10, pedido 586482278473107237: 412,30 de venda, 0,00 liquidado — desfeito antes de faturar): vira "Outro" sozinho ("zerada: nada entrou, nada saiu"); não é dinheiro e não trava o dia.
- **"Outras taxas"** no Raio-X: a API da TikTok não detalha uma parte da taxa (no pedido da sondagem: 67,85 = 24,74 comissão + 37,11 afiliados + **6,00 sem campo próprio** — provavelmente a taxa de transação, ≈1,6% do que o cliente pagou). O total fecha; só o nome da fatia fica genérico.
- **Status Bling** na grade (pedido dele ao ver os "Aguardando liberação" sem motivo): coluna ao lado de Motivo, e no Excel.
- **Venda (previsto)**: a Margem já guarda as transações do pedido (`raw.settlements`). Enquanto não liquida, a Margem tem a **estimativa do unsettled** (o mesmo número da Central do Vendedor) — é o que aparece em "A liberar". Na hora que o extrato liquida um pedido que na Margem ainda era estimativa, o robô **relê o financeiro** daquele pedido (`reler_financeiro_liquidados`, até 60 por rodada) — senão a estimativa (centavos diferente) viraria "recebido a menos" falso.
- **Grade (aba Pedidos)**: Valor = produtos **após o desconto da loja** (`after_seller_discounts_subtotal_amount`), Tarifa = todas as taxas (`fee_amount`: comissão + taxa de transação + afiliados), Frete = parte da loja (`shipping_cost_amount`), Extras = o que a TikTok acrescentou na receita (subsídio no produto) — entra **negativo**. Valor − tarifa − frete − extras = liquidação.
- **Raio-X**: Previsto (transações que a Margem guardou) × Real (linhas do extrato diário) com as linhas da TikTok: *Vendas brutas (preço de tabela) · − Desconto da loja · = Produtos após desconto · ± Subsídio da TikTok e outros na receita · = Receita · − Comissão · − Taxa de transação · − Afiliados · (− Outras taxas) · ± Frete (parte da loja) · (± Ajustes) · (± Outros) · ± Depois da liquidação · = Liquidação do pedido*. A identidade que a Margem conferiu em pedidos reais: **liquidação = receita + taxas + frete (+ ajustes)**; o que sobrar cai em "Outros" pra tabela sempre fechar. Com os números da sondagem (pedido 586488303325120242): 1.300,65 de tabela − 888,35 de desconto da loja = 412,30; taxas 67,85 (24,74 comissão + 6,00 transação + 37,11 afiliados); frete 0 (a loja cobrou 23,90 e pagou 23,90) → **344,45**.
- **Resumo / Dinheiro nas plataformas**: Na conta = — (a TikTok não guarda saldo: "repassa pro banco todo dia"); A liberar = soma das estimativas dos pedidos aguardando.
- Vigia de Conciliação e fechamento automático do dia valem igual (a loja entra na rodada das 07:00).

**Conferido em produção (15/10 13:36 BRT, commits 25cfbeed → b4562c74):** Mini desde 01/10 = 117 pedidos (52 bateu, 63 aguardando com 48.928,25 a liberar, 2 cancelados no Bling), 0 recebido a menos; 15 extratos lidos (01→14/10), todos com pedidos = extrato = repasse (ex. 14/10: 15 linhas, 9.835,09; 01/10: 21 linhas, 11.238,10), robô fechou 01→14/10; pedido 302381: 412,30 − 67,85 = 344,45 liquidado em 14/10, Status Bling "Entregue". Lições do dia: a subida reinicia o worker e mata a rodada em andamento (o arq reexecuta depois); a releitura do financeiro na TikTok leva ~40 s por pedido quando a API limita — por isso 12 por rodada com teto de 5 min.

**Mais dois achados (15/10, 13:50 BRT):**
- A API de extratos filtra por **dia** e o `statement_time_lt` exclui o dia inteiro: com `lt = agora`, o extrato de hoje (pago às 04:39) só aparecia amanhã — o repasse já estava em `payments` e o extrato não vinha. Leitor e sonda pedem até amanhã (commit 212d86aa). O extrato de 15/10 (3.819,28 = 7 liquidações 4.492,21 − 1 estorno 672,93 de pedido de setembro) entrou no mesmo dia.
- **"Em espera" da Central × "A liberar" do painel** (`GET /sonda/tiktok/espera?conta=Mini`, só leitura): para os mesmos pedidos os dois batem no centavo (56 pedidos, 44.436,04 dos dois lados). A diferença é só o que a TikTok conta e o painel não: 11 pedidos de antes de 01/10 (3.363,19) e 5 linhas de valor 0 de pedidos já liquidados. Na hora do print dele (47.634,93 × 48.928,25) o painel ainda tinha os 7 pedidos pagos hoje de madrugada (4.492,21), que a TikTok já tinha tirado do "Em espera".

**Regra das colunas Tarifa × Extras (15/10, 14:00 BRT — decisão dele depois de ver as opções):** "tarifa é tarifa, aquela porcentagem sempre igual; afiliado, cupom, tudo que for assim entra em Extras". Valor fica como está em cada plataforma (Shopee = preço de venda com o subsídio da loja em Extras; TikTok = preço após o desconto da loja). **Shopee:** Tarifa = comissão + taxa de serviço + taxa de transação + encargos; **Afiliados** (`order_ams_commission_fee`) vai pra Extras (301438: tarifa 354,85 → 305,85; Extras 420 → 469). **TikTok:** Tarifa = comissão + taxa de transação; Afiliados pra Extras (302381: tarifa 67,85 → 30,74; Extras 0 → 37,11). **ML fica como está** (custo por vender + custo por cobrar na tarifa; cupom/parcelamento em Extras). Coluna nova `venda_afiliados` (mig 0404), linha "− Afiliados" no Raio-X da Shopee, coluna "Afiliados" no Excel, balão de Extras com "afiliados X". Líquido não muda. Rejeitado por ele: igualar o Valor ao preço efetivo nas duas plataformas.

Testes: `tests/test_conciliacao_tiktok.py` (mapeamento, leitura por extrato + segunda rodada sem duplicar, Extrato do dia fechando em zero, Raio-X previsto × real linha a linha, releitura do financeiro só pra estimado/velho).

## 24. Sondagem Amazon (15/10, 14:00–14:35 BRT) — kfa, kia, poofy, nexus (só leitura)

`GET /api/conciliacao/sonda/amazon?conta=&dias=60[&baixar=false]` (3 rodadas; commits aa42666f, a2e367fa, e435ab45). OK do Vinicius: "pode rodar"; regra dele: **um processo só pra todas as contas**.

| Conta | Finances (transações, grupos de repasse) | Relatório de repasse (Reports API) | Margem (financeiro por pedido) | Pedidos desde 01/10 |
|---|---|---|---|---|
| **kfa** | 403 | **403** ("Access to the resource is forbidden") | 1.736 em `error` (nunca leu) | 21 (20 DBA) |
| **kia** | 200 | 200 — 9 relatórios em 60 dias, todos baixados | 72 posted / 11 pending; líquido posted 73.507,20 | 83 (36 DBA, 35 próprio/SEDEX) |
| **poofy** | 200 | 200 — 10 relatórios | 50 posted / 6 pending | 46 (45 DBA) |
| **nexus** | 200 | nenhum relatório | nada | 0 (conta parada) |

**Como a Amazon paga (dados reais da Kia, 60 dias):** 256 transações: Shipment +140.043,81 · Refund −4.536,64 · ServiceFee −1.958,50 · ProductAdsPayment −2.701,40 · Transfer (repasse pro banco) 7.723,29 · DebtRecovery 449,89. **Situação: DEFERRED 89 (87 vendas + 2 estornos) = 95.269,33 presos pela "reserva por data de entrega" (a Amazon segura até ~7 dias depois da entrega); RELEASED 126 = 23.858,70; DEFERRED_RELEASED 41.** Repasses pro banco: 08/09 743,50 · 10/09 445,22 + 815,25 · 24/09 2.230,02 + 834,41 · 08/10 1.316,29 + 1.338,60 (vários grupos de repasse correm em paralelo: pedidos, Easy Ship, um grupo "antigo" desde 12/2024). Grupos abertos em 07/10: 2.317,39 + 2.744,28 + 3.324,50. Poofy: Shipment 15.851,85; DEFERRED 41 = 12.135,02; Transfers 2.185,50; DebtRecovery 853,88 em 13/10 = o acerto negativo de Easy Ship (DBA) cobrado à parte.

**Relatório de repasse (settlement V2, TSV):** um por repasse (~14 dias), cabeçalho com settlement-id, período, **deposit-date** e total; linhas com transaction-type (Order, Refund, other-transaction, ServiceFee…), order-id, amount-type/description (ItemPrice Principal, ItemFees Commission, Promotion, Amazon Easy Ship Charges, Tax on fee COFINS/PIS…), **fulfillment-id = MFN tanto no DBA quanto no próprio** (não separa), posted-date. Prova dos nove dentro do arquivo: soma das linhas = total (conferido: Kia 1.316,29; Poofy −853,88). A Amazon gera sozinha; só listar e baixar. **É a única fonte que pode valer pra todas as contas** (KFA por upload do mesmo arquivo do Seller Central até a Amazon aprovar o perfil — caso 21731531021 sem resposta desde 24/08).

**A conta de um pedido (quebras da Finances, que a Margem já guarda em raw):**
- Envio próprio (Kia 301635): ProductCharges 2.598,00 + Shipping (frete cobrado do cliente) 94,81 − Commission 285,78 (base + imposto) − AmazonForAllFee 40,39 (1,5%, base + imposto) − ShippingHB 10,43 (retenção de 11% sobre o frete) = 2.356,21. O frete pago de verdade está fora (Melhor Envio).
- DBA (Kia 301943): ProductCharges 2.176,00 − OurPriceDiscount (promoção) 108,80 − MFNPostageFee (etiqueta Easy Ship) 41,90 − AmazonForAllFee 31,01 = 1.994,29 (**sem comissão** nesta conta neste pedido); Poofy DBA 303300 tem Commission 9% + 1,5% + etiqueta 65,85.
- Regra Tarifa × Extras (seção 23): Tarifa = Commission + AmazonForAll (porcentagens fixas); Frete = DBA: etiqueta + cobranças Easy Ship; próprio: +frete cobrado − ShippingHB; Extras = promoções da loja, Ads, impostos sobre taxas quando vierem separados.

**Melhor Envio:** o token do DaVinci responde **401 Unauthenticated** (`/api/v2/me` e `/api/v2/me/orders`) — vencido. Sem token novo não dá pra trazer o frete pago do envio próprio.

**Proposta (aba Amazon, mesmo padrão):** 1) Extrato = relatório de repasse: linha por transação na `posted-date`, "Repasse pro banco" na `deposit-date` com o total, saldo sintético que zera no repasse (modelo TikTok, cadência de 14 dias; vários repasses no mesmo dia ficam separados por settlement-id); API na Kia/Poofy, upload na KFA. 2) Venda = Margem (Kia/Poofy); KFA "venda não lida" até aprovar. 3) Resumo › Dinheiro: **Preso (reserva por entrega)** = DEFERRED, **Liberado aguardando repasse** = grupos abertos, repasses do mês. 4) Frete por canal como acima; frete pago no Melhor Envio como "frete pago fora" no resultado quando o token for renovado.

## 25. Amazon no painel (15/10, 15:10 BRT) — kia e poofy desde 01/10 (commit 7107118a)

OK do Vinicius ("pode começar pela kia e poofy desde 01/10"), depois de entender pela tela Pagamentos › Transações que **é essa lista dia a dia que vem pra nós** e que o extrato de 14 dias é só a soma do que estava liberado + o pagamento.

- **Leitor** `conciliacao_amazon.run_amazon_sync`: Finances API `GET /finances/2024-06-19/transactions?postedAfter=` desde o início da loja (ou 60 dias), paginando por `nextToken`, com **sobreposição de 21 dias** a cada rodada — porque a mesma transação muda de situação (DEFERRED → RELEASED) e precisa ser atualizada (sub_tipo = situação, descrição sem "preso na reserva"). `transactionId` é a chave única. Uma rodada por dia às 06:00 com as outras lojas; "Sincronizar agora" relê.
- **Linhas:** Shipment → "Lançamento do pedido X" (crédito = total líquido; "(preso na reserva)" enquanto DEFERRED), Refund → "Estorno do pedido X" (débito), Transfer → Saque "Repasse pro banco" (débito; a API manda positivo), DebtRecovery → "Cobrança do saldo negativo (cartão/conta)" (crédito, ajuste), ProductAdsPayment → Publicidade, ServiceFee → Ajuste da Amazon (com pedido quando houver). Pedido casa por `relatedIdentifiers ORDER_ID` = numeroloja do Bling.
- **Saldo:** sintético e contínuo, recalculado do zero a cada rodada em ordem de data = "Saldo total" da Amazon (reserva + disponível). O rodapé do Extrato do dia vira "Saldo na Amazon (início/fim do dia) · Lançamentos e ajustes · Estornos, tarifas e repasses"; a prova dos nove independente é o repasse de 14 dias (passo seguinte: relatório de liquidação).
- **Conta do pedido** (`conta_amazon`, quebras `breakdowns` da Finances; a Margem guarda em `raw.transactions`): produto + frete cobrado do cliente + promoção (−) + tarifas (−: Commission, AmazonForAllFee 1,5%, ShippingHB = retenção sobre o frete, MFNPostageFee = etiqueta Easy Ship no DBA) = total. Grade: **Valor** = produto; **Tarifa** = comissão + 1,5% + retenção (porcentagens); **Frete** = etiqueta (DBA) − frete cobrado do cliente (próprio: fica negativo = a loja recebeu o frete; o pago no Melhor Envio entra quando houver token); **Extras** = promoção da loja + outras tarifas. Kia 301635 (próprio): 2.598,00 · 336,60 · −94,81 · 0 · 2.356,21. Kia 301943 (DBA): 2.176,00 · 31,01 · 41,90 · 108,80 · 1.994,29.
- **Raio-X Amazon:** Produto · + Frete cobrado do cliente · − Desconto promocional · − Comissão · − Amazon for All · − Retenção sobre o frete · − Etiqueta Easy Ship · (− Outras tarifas) · (± Outros) · ± Depois · = Total do pedido. Previsto = Margem; Real = linhas lidas.
- **Resumo › Dinheiro (Amazon):** **preso na reserva por entrega** = soma dos lançamentos DEFERRED; Na conta = saldo − preso (liberado aguardando repasse); A liberar = preso + previsto dos ainda não lançados. Situação do pedido: "bateu" quando a Amazon lança (3–4 dias após o envio), mesmo preso; "Liberado em" = data do lançamento.
- **KFA:** fora da migração (API 403); entra por upload do arquivo "Fazer download" da tela Transações (passo seguinte, mesmo leitor com outro formato).

**Primeira carga (15/10, 15:00 BRT) e dois acertos:** Kia 83 pedidos desde 01/10 — 36 bateu, 11 aguardando, preso na reserva 73.716,29 (72 lançamentos), na conta (liberado) 8.719,29; Poofy preso 12.135,02, na conta −230,95 (saldo negativo do Easy Ship). O robô fechou os dias com movimento. (1) **Etiqueta Easy Ship em transação separada** (`ServiceFee` com pedido, quebra `MFNPostageFee`), dias depois do lançamento: 32 pedidos DBA da Kia caíram em "estornado depois" → vira linha "frete" do pedido, "saiu depois" ignora frete, Raio-X real inclui a etiqueta (bb504680). (2) **Saída da reserva = registro novo**: o lançamento original vira `DEFERRED_RELEASED` e a Amazon manda outro `RELEASED` do mesmo pedido e valor na data da liberação — 4 pedidos "recebido a mais" e saldo dobrado → fica o original (data do lançamento, "· liberado em dd/mm", raw._liberado_em) e a repetição é descartada/removida (commit seguinte). Status na API: DEFERRED (preso) · DEFERRED_RELEASED (original já liberado) · RELEASED (liberado ou nunca preso).

## 26. Frete do envio próprio (Melhor Envio), "falta frete" e cancelado da Amazon (15/10, 15:40–16:30 BRT)

Decisões dele depois da maquete `docs/img/conciliacao-amazon-frete.png`: **Valor = produto + frete cobrado do cliente**; **Frete = só o que a Amazon cobra** (etiqueta Easy Ship no DBA); o frete pago no Melhor Envio aparece na coluna Frete do envio próprio (marcado "ME") mas **não entra no Líquido** — entra no **Sobrou**; **envio próprio sem o frete do Melhor Envio não pode ser "bateu"** → "Falta frete (Melhor Envio)" (pendência, item no Onde agir). Sem coluna de canal (dispensada).

- **Token do Melhor Envio pela tela** (commit 71146f19): Conciliação › Amazon › botão "Melhor Envio" (quem edita a conciliação). Tabela `configuracoes_seguras` (cifrada), endpoints `/api/conciliacao/melhor-envio/token` (GET status · PUT salva depois de conferir em `GET /api/v2/me` · POST testar · DELETE). O token do `.env` estava vencido (401) e ninguém da operação edita o servidor. Cairo colou o token às 15:43: conta CASA E DECORAÇÃO, 1.612 envios.
- **Como o Melhor Envio devolve um envio** (`GET /sonda/melhor-envio`): `price` (pago), `discount`, `conciliation` (conferência de peso; `type` debit = cobrado a mais), `billed_weight`, `paid_at`/`posted_at`/`delivered_at`/`canceled_at`, `status` (released/posted/delivered/canceled), `service.name`, `tracking` e as **tags** do Bling: `mi:reference_code` = nº do pedido no Bling, `mi:marketplace_code` = id da Amazon, `mi:reference_link`. A busca `q=` NÃO acha por essas tags (204) — por isso a leitura é paginada (10 por página, do mais novo pro mais velho) até passar do início da loja.
- **Leitor** `conciliacao_melhor_envio.run_me_sync` (roda na rodada da loja Amazon): guarda em `conciliacao_fretes_pagos` (mig 0407; frete_pago = preço − desconto + conferência; cancelado = 0; sem dados do cliente no raw) e o recálculo soma por pedido em `venda_frete_pago`. Sobrou = recebido − custo − reembolso − frete pago. Raio-X: bloco "Frete pago fora da Amazon · Melhor Envio" (etiqueta, desconto, conferência, total) ou o aviso de que o envio ainda não veio.
- **Cancelado da Amazon** (302142, pergunta dele "é cancelado, não é?"): lançou 749,53, a Amazon devolveu 759,53 ao cliente e ficou com 10,00 de tarifa; Bling Cancelado. Regra nova: Bling cancelado + devolveu tudo (recebido ≤ 0) = **cancelado**, custo 0, sobrou = o que a plataforma ficou (−10,00), motivo "cancelado no Bling: devolveu tudo, a plataforma ficou com 10,00". Antes caía em "estornado depois" com custo 495.

**Mais acertos de 15/10 à tarde (302142, Kia):** (1) estorno na grade = só o líquido na célula Recebido com ↩ e balão "entrou · saiu depois · ficou" (opção escolhida por ele); Dif e Recebido mantêm o sentido de sempre. (2) Raio-X: a linha final compara o previsto com o que a plataforma **lançou** (recebido + estornos), "± Depois do lançamento" só com estornos/ajustes (frete que chega depois, como a etiqueta Easy Ship, é parte da liquidação) e linha nova **"= Ficou com a loja"**; previsto da Amazon = só Shipment + ServiceFee (estorno é "depois") e é esse o líquido previsto do pedido. (3) Melhor Envio do 302142: etiqueta SEDEX 89,88 paga 08/10 10:56, postada 11:40, **entregue 14/10** (AE006781821BR) — o estorno da Amazon foi às 10:44 do mesmo dia; a equipe dizia que não tinha postado. (4) Ele mudou o status no Bling (Cancelado → Aguardando Devolução): a troca chega ao espelho em até 10 min (webhook/safety net) e a situação só muda na rodada da loja ("Sincronizar agora" ou 06:00) — "Recarregar" não recalcula; e o robô agora **reabre** o pedido que ele mesmo tinha finalizado como "venda desfeita" quando a situação deixa de ser cancelado.

## 27. Todas as lojas (15/10, 17:30 BRT) — mig 0408, um job por loja, carga inicial em fila

OK do Vinicius ("pode seguir, faz os ajustes e liga as lojas"), depois da lista das contas e dos riscos (limite da fatura do ML dividido com a Margem, fila única da madrugada, Onde agir cheio no 1º dia).

- **Nomes repetidos entre plataformas** (poofy, kia, jlas, mini, kfa, barbosa, inova, vita, aguiar, mega, victor mei, eron, injox, atv): a tela e os links identificam a loja pelo nome, então quem existe em mais de uma plataforma passou a se chamar "nome (plataforma)" — inclusive as já ligadas: Jlas (shopee), Mini (tiktok), kia (amazon), poofy (amazon). jlas2 segue jlas2.
- **Ligadas (início 01/10):** 22 ML, 13 Shopee, 7 TikTok (todas as integrações ativas). Amazon como estava; nexus (parada) fora; kfa por upload (pendente).
- **Rodada diária = um job por loja** (`enfileirar_rodadas`): fatura do ML às 06:12 em fila de 4 min (22 contas → até ~07:40; competem com a Margem só nos :15/:45); extrato às 06:00–08:50 a cada 10 min, repetindo só o ML (relatório do MP pode demorar) e uma vez por dia nas outras. Job com 1 h de limite; trava por loja continua.
- **Carga inicial em fila** `POST /api/conciliacao/carga-inicial` {plataformas, contas, intervalo_min=12, so_sem_carga}: ML a cada 12 min (≈4,5 h pra 22 contas, feita à noite com a Margem parada — a Margem só atrasa leituras, não perde), Shopee/TikTok 30 s entre uma e outra.
- Cada loja precisa do id de loja do Bling na tela Lojas; sem isso o recálculo grava "sem loja do Bling ligada" em `ultimo_erro` (conferir no Resumo no dia seguinte).


## 28. Revisão da manhã de 16/10 — erros do leitor (commit desta seção)

Primeira madrugada com todas as lojas: 47 contas carregadas; Shopee 14/14, TikTok 8/8, Amazon 2/2 lidas; 6 contas do ML falharam a ingestão (coluna estreita, mig 0409) e foram relidas às 07:00. O que sobrou no Onde agir e o que foi corrigido:

- **injox "saldo não bate −362,15"**: pagamento e reserva no mesmo segundo invertiam a ordem; a prova dos nove do dia agora parte do fechamento do dia anterior (`_saldo_anterior`) — diferença 0.
- **aguiar: 14 pagamentos "sem dono"**: a conta tinha uma config antiga do relatório do Mercado Pago com 11 colunas (sem ORDER_ID). `garantir_config` agora compara as colunas a cada pedido de relatório (≈ 1×/dia) e completa com PUT; quando completa, esquece as janelas já lidas e pede tudo desde o início. A ingestão virou upsert: linha repetida COMPLETA a gravada (nº da venda, SKU, tarifas, raw) sem mexer no que uma pessoa fez; linha gravada pelo relatório pobre (identidade sem ORDER_ID) é casada pela chave natural (hora, pagamento, tipo, valores) e troca de identidade em vez de duplicar.
- **Linha sem nº de venda mas do mesmo pagamento de uma linha ligada**: herda o pedido da irmã (`irma_do_pagamento`).
- **Gêmeas (achado da releitura da aguiar, 10:31–10:51 UTC):** a config antiga também não tinha RECORD_TYPE; a chave natural exigia igualdade no `sub_tipo` e a gêmea completa não casou → 130 "sem dono" caíram pra 28, mas a linha pobre ficou (ligada ao pedido pela irmã do pagamento = recebido em dobro; saques em dobro). Correção: chave natural = hora, pagamento, descrição, valores, pra qualquer linha antiga que o relatório novo não traz com a mesma identidade; `apagar_duplicatas_pobres` (ingestão + começo do recálculo) some a gêmea sem nº da venda ou sem RECORD_TYPE, nunca a que uma pessoa vinculou. A conferência da config é por dia de calendário (`extrato_config_em` < hoje), não "≥ 20 h".
- **injox: 4 reservas de vendas que não existem no Bling**: vão e voltam (soma zero); o robô classifica como "reserva" (classificação nova "Reserva (vai e volta)") pra não ficar sem dono; se o pedido aparecer depois, a ligação desfaz a classificação. Estorno de verdade sem pedido continua sem dono.
- **Barbosa (shopee) −0,98**: a carteira da Shopee trouxe uma linha cujo valor não bate com a variação do saldo (13:05:28, R$ 292,39). Não é erro nosso; fica pra pessoa classificar como ajuste (regra automática avaliada e deixada de lado por enquanto: esconderia erro real).
- **lucas mei (ML)**: `invalid client` ao renovar o token — precisa reconectar em Integrações › Mercado Livre.

Segunda rodada (Vinicius: "você não consegue fixar essas coisas que faltou?"), regras novas do robô:

- **Fatura do ML cobrada do saldo do MP**: linha `payment` com EXTERNAL_REFERENCE `MELIPAYMENTS-COLLECTIONATTEMPT-…` (mega 15/10: débito 377,27 = `pago.total_payment` da fatura 17/09→09/10, que vence 15/10). Vira tipo `fatura_cobrada`, sem nº de venda, classificação "Fatura do ML". `reserve_for_debt_payment` (o MP segurando o que entra enquanto o saldo está negativo depois da cobrança — aguiar 12/10) vira `cobranca_divida`, mesma classificação. Linhas antigas são reclassificadas no recálculo (`classificar_cobrancas_da_fatura`).
- **Venda desfeita sem pedido no Bling**: venda do ML que não existe no Bling e cujo dinheiro entrou e saiu inteiro no mesmo pagamento (aguiar 02/10: +320,69 −396,17 +75,48 = 0) ganha a classificação "Venda desfeita (sem pedido no Bling)"; se o pedido aparecer, a ligação desfaz. Venda que só entrou continua sem dono (precisa importar no Bling ou classificar).
- **Pulo pequeno de saldo da Shopee**: a carteira muda o saldo sem listar o lançamento (Barbosa 15/10, −0,98; nada na API nem relendo). `ajustar_saldo_do_dia` grava uma linha "Ajuste da Shopee" classificada como ajuste no ponto do pulo, até R$ 10 por dia (e no máximo 3 pulos); acima disso fica "saldo não bate" pra uma pessoa. Refeito a cada leitura do dia; ajuste que uma pessoa mexeu fica.
- **Só pessoa resolve**: lucas mei (reconectar o ML); vendas do ML que não existem no Bling (importar ou classificar); os itens de negócio (reaberto, estornado depois, recebido a menos) são o painel funcionando, não erro.
