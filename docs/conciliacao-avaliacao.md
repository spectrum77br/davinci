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
- **Leitura do extrato** (`services/conciliacao_extrato.py`): a cada 10 min o worker olha as lojas ligadas; se há janela pedida e o arquivo ficou pronto, lê e grava os movimentos (sem duplicar: identidade = hash da linha); senão pede a próxima janela, do dia seguinte ao último lido até ontem (a primeira pede desde o início da loja). Cria a configuração do relatório na conta se faltar. Linha de saque já nasce classificada; linha de venda/mediação/estorno/reserva guarda o nº da venda, o pack e, quando o Bling já importou, o pedido do Bling.
- **Leitura da fatura** (`services/conciliacao_fatura.py`): 2× por dia (06:12 e 18:12), cobranças e bonificações do período aberto e do anterior, incremental (`from_id` = último id lido), uma página de 1.000 por chamada e **13 s entre chamadas** (limite de 5/min do ML); resumo do período aberto guardado. Product Ads nasce classificado "publicidade".
- **Recálculo** (`services/conciliacao_calculo.py`): de hora em hora (:40) e no fim de cada leitura. Pedidos da loja desde o início; venda = o que a Margem já leu; recebido = créditos − débitos do extrato sem as reservas; situação e etapa pelas regras da seção 12 (tolerância R$ 0,05; bateu finaliza sozinho em 7 dias; movimento novo depois de finalizado reabre); sobrou = recebido (ou previsto) − custo − reembolso do painel Reembolso. Linha de pedido anterior ao início vira `anterior_ao_inicio`.
- **Endpoints**: `GET /api/conciliacao/status` (o painel "cru": leituras, movimentos por tipo, pedidos por situação/etapa) e `POST /api/conciliacao/lojas/{conta}/sincronizar` (rodada completa no worker, trava por loja).
- Testes: `tests/test_conciliacao_base.py` (normalização do extrato e da fatura com os dados reais da sondagem, ingestão sem duplicar, janelas do extrato, leitura incremental com pausa, recálculo com as situações, etapas) e `tests/test_conciliacao_sonda.py`.
