# Conciliação — Temu (piloto na Eron): desenho

17/11/2026. Aprovado pelo Vinicius: "Sim, piloto na Eron" → "Desde 24/10". **Construído e
testado no mesmo dia** (leitor, migration 0495, robô, aba na tela); o que ficou valendo está
na seção 47 de `docs/conciliacao-avaliacao.md`. Este documento guarda o estudo da tela e os
achados que levaram ao desenho. Onde o texto abaixo fala em guardar o previsto em
`conciliacao_movimentos`, o que foi feito é a tabela própria `conciliacao_previstos`.

## O que a tela da Temu entrega (conferido na Eron, perfil AdsPower 126 `k1dkfq5y`, Mac mini)

Finanças › Pagamentos (`https://br.seller.temu.com/payment.html`), quatro abas na mesma URL.
A própria página chama (POST, JSON):

| O quê | Chamada da página | Campos |
|---|---|---|
| Saldo | `/mms/statement/amount/detail` | `opening_amount`, `sales_amount`, `refund_amount`, `reserve_amount`, `payout_amount`… (centavos em `.amount`) |
| Extrato (aba Extratos) | `/mms/statement/order/list` `{begin_create_time,end_create_time,page,size}` | `mall_transaction_flow_volist[]`: `flow_id`, `trans_sn`, `batch_id`, `order_id`/`parent_order_sn` (PO-029-…), `biz_category`, `sub_biz_category`, `prod_amount`, `fee_amount`, `other_amount`, `total_amount`, `created_at` (ms), `note` |
| Tipos de linha | `/mms/statement/transaction/type/list` | Order Payment, Refund, Shipping label purchase (+ adjustment), Payment Refund, Seller repayment, Reserve amount, Released amount, Advertising service fee, Others |
| Previsto (aba Pedidos não processados) | `/mms/settle/query/amount/selfproduce` `{size,page}` (página começa em 0) | `positive_settle_bill_list[]`: `parent_order_sn`, `estimated_revenue` (+`_positive`/`_negative`, texto "R$26,00"), `estimated_settled_time` (ms ou "Entregue em + 7 dias"), `order_status_desc`, `parent_order_time` |
| Saques (aba Pagamentos) | `/mms/payment/disbursement/list` | `mall_withdraw_order_simple_volist[]` — **formato desconhecido** (a Eron nunca sacou) |
| Prazo | `/mms/settle/query/period` | `settle_period: "7"` |

- Página de 10 linhas, sem seletor de tamanho. Paginação: `ul[data-testid="beast-core-pagination"]`,
  próxima = `li[data-testid="beast-core-pagination-next"]` (classe `…disabled…` no fim).
- Abas: `[data-testid="beast-core-tab-itemLabel"]` pelo texto.
- O extrato abre nos últimos 30 dias (campo de datas é só leitura, muda por calendário).
- Sessão vencida cai em `login.html` → `needs_manual_login`.
- Ensaio 17/11 12:05: 16 linhas (2 páginas), 11 previstos (2 páginas), 0 saques. Soma das 16 linhas
  = R$ 538,00; Saldo total da tela = R$ 552,00. Os R$ 14,00 que faltam devem ser uma venda paga
  entre 15 e 17/10, fora da janela de 30 dias (não conferido).
- Barbosa 115, JLAS 143 e Atv 138 estão deslogadas (17/11): só a Eron dá pra ler.

## Achados dos três agentes (leitura do código, 17/11)

1. **Não gravar o previsto na Margem (`marketplace_order_financials`).** Com valor real gravado,
   `_ATTENTION_SALDO_SQL` (routers/margens.py) dispara "saldo divergente" pra qualquer diferença
   acima de R$ 0,01 e o robô `margem_auto_hold` passa a segurar os pedidos da Temu (hoje aprovados
   pela âncora do Bling). Bling 45,80 × Temu 26,00 → todo pedido seguraria. Decisão separada, dele.
2. **A Conciliação exige `integration_id` (FK NOT NULL nas 6 tabelas) e a Temu não tem integração.**
   A migration 0347 (Atendimento) recusou criar integração pra loja de robô, porque todo worker que
   percorre `integrations` passaria a mirá-la. Caminho escolhido: **âncora arquivada desde o
   nascimento** (`archived_at` preenchido, `status='ancora'`, credenciais vazias, sem `store_id`,
   sem `bling_loja_id`) com **nome que não casa com nenhuma loja** (ex.: "eron · conciliação temu"),
   pra não ser pareada por nome em Lojas/Preços/e-mail. Arquivada ela não aparece em Integrações
   (o botão Excluir apagaria a Conciliação em cascata) nem nos crons que respeitam `archived_at`.
   A loja do Bling vem de uma coluna nova `conciliacao_lojas.bling_loja` (206288707), não do nome.
3. **Lugares que quebram com uma loja `temu`** (todos precisam de ramo próprio):
   - `models/conciliacao.py`: nova origem `temu_extrato` em `ORIGENS_EXTRATO`.
   - `conciliacao_jobs.sincronizar_loja`: o `else` cai no leitor do Mercado Pago; `_cliente()` roda
     fora do `try`. Temu = extrato pulado (entra pelo robô), sem cliente.
   - `conciliacao_amazon_csv.enfileirar_busca_csv_todas` (cron 06:00) e
     `POST /lojas/{conta}/buscar-transacoes`: filtram só `modo_extrato=='upload'` e mandariam o
     robô da Amazon. Filtrar por plataforma e criar a ação da Temu.
   - `conciliacao_calculo`: sem venda na Margem o pedido pago vira sempre `sem_venda`; a Temu
     precisa de `liquido_previsto` (como a KFA) vindo do previsto guardado; texto "arquivo da
     Amazon" na linha ~662; `PRAZO_LIBERACAO` sem Temu (usar a data estimada da Temu).
   - Tela: `MARKETPLACES` em `pages/conciliacao.vue` (sem aba Temu) e textos que caem em
     "Mercado Pago" (`ConciliacaoExtrato.vue`, `conciliacao.vue`, `ConciliacaoResumo.vue`).
   - Fechamento do dia exige `saldo_apos` nas linhas (`_totais_do_dia`): sem ele o dia nunca fecha.

## Desenho

- **Robô** (executor do Mac mini, fila `conciliacao`, ação nova `temu_extrato`): abre o perfil,
  navega como pessoa e guarda o que a página carrega (não chama a Temu por fora). Envia um JSON
  pra `POST /api/conciliacao/temu/leitura` (X-Agent-Token): saldo, linhas, previstos, saques.
  Perfil por loja em `CONCILIACAO_PERFIS` (`eron (temu)=k1dkfq5y`). Cron diário + botão ⟳.
- **Extrato** → `conciliacao_movimentos`, origem `temu_extrato`, `id_externo = flow_id`:
  Order Payment → `venda_liberada` (crédito); Refund → `estorno` (débito); etiqueta → `frete`;
  publicidade → `publicidade`; reserva/liberado → reserva; resto → `outro` sem dono.
- **Saldo**: a linha mais nova recebe o Saldo total da tela e as anteriores são calculadas pra
  trás; linha já gravada não é reescrita. Se faltar linha entre duas leituras, o dia não fecha.
- **Previsto** → `conciliacao_movimentos`, origem `temu_previsto` (fora de `ORIGENS_EXTRATO`, não
  é dinheiro), uma linha por PO; atualiza enquanto o pedido não foi pago, congela quando paga
  ("comparar, não copiar"). O cálculo usa como `liquido_previsto`.
- **Margem: não muda.**
- **Volta atrás**: desligar = `conciliacao_lojas.ativa=false` na loja; nada das outras lojas muda.

## Em aberto

- **Data de início da loja:** decidido, 24/10 como as outras. Na Eron isso dá 10 pedidos já
  pagos "sem previsto" pra finalizar à mão, 1 estornado (308104) e 4 linhas de pedidos
  anteriores ao início.
- Formato das linhas de etiqueta e de publicidade, e do saque que falha e volta ("Payment
  Refund"): nenhuma loja teve até 18/11. Saque e reserva: resolvidos abaixo.
- A Eron não tem conta bancária cadastrada na Temu (não dá pra sacar).

## Saque e reserva (18/11, lojas Barbosa e JLAS)

Vinicius logou Barbosa (perfil 115 `k1dkegpc`), JLAS (143 `k1dof4hk`) e Atv (138 `k1docw56`)
no AdsPower do Mac mini. Lidas pelo mesmo caminho do robô, sem enviar.

- **O saque não é linha do Extrato da Temu.** Só aparece na aba Pagamentos
  (`/mms/payment/disbursement/list`, o histórico inteiro, mais novo primeiro): `withdraw_order_id`
  ("LWO…"), `amount`, `created_at`, `status` 2 = pago, `status_time_map` (CREATED, PROCESSING,
  PAID), `statement_begin_time`/`statement_end_time` (o período de 14 dias que ele paga),
  `bank_account_suffix`, `is_refund`, `mall_id`. Na Barbosa a Temu saca sozinha a cada 14 dias.
- **O saque deixa um par de reserva no extrato:** "Reserve amount" −X às 23:59:59,999 do último
  dia do período (`order_id` = nº do saque + `_in`) e "Released amount" +X às 00:00 (`_off`).
  X é o que a Temu segura pro período seguinte. O par soma zero; quem tira o dinheiro é o saque.
- **A conta que fecha** (Barbosa, 03/11 → 17/11): 57,40 + vendas 218,00 = 275,40; reserva −78,50;
  +78,50; saque −196,90 → 78,50; depois +26,00 e +183,00 → 287,50 = Saldo total da tela.
  Conferida nos três saques da janela (20/10, 03/11, 17/11).
- **No painel:** cada saque vira uma linha "Saque" (`tipo`/`classificacao` = `saque`,
  `id_externo` = `saque:<nº>`, data = a criação do saque). As linhas de reserva viram
  `reserva_temu` com o nº do saque em `pagamento_id`; `_reservas_vai_e_volta` classifica o par
  como "Reserva (vai e volta)" só quando a saída e a volta existem — só a saída fica sem dono.
- **Período dos saques:** os feitos depois da linha mais antiga do extrato lido ou depois da
  última linha gravada (o que vier antes); mais velho que isso é passado, como o saldo de
  abertura.
- **Quais saques entram:** só saque que a Temu já marcou como PAGO (o estado em que a conta
  foi conferida). Ela cria o saque ~05:40 e paga ~2 h depois; lido "criado", a leitura espera.
- **Leitura recusada inteira** quando: um saque do período vem sem nº, valor ou data; a lista
  de saques veio cortada sem nenhum saque anterior ao período; um saque já gravado sumiu da
  lista; um saque novo não está pago ou está numa situação desconhecida; o extrato cita um
  saque que a aba Pagamentos não trouxe e a reserva tem menos de 48 h (gravar sem ele
  deixaria o saldo das linhas errado pra sempre). Saque antigo ilegível não trava a loja.
- **Login:** a sessão da Temu Seller vence 24 h depois do login (medido em 18/11); não há
  "manter conectado". O robô só lê a loja em que alguém logou nas últimas 24 h.
- **JLAS:** nunca sacou; 4 pagamentos de 348,80 na janela + 348,80 de antes de 18/10 = 1.744,00.
- **Atv fica de fora:** saldo 0,00, extrato vazio, nenhum pedido a receber, sem conta bancária.
  A tela dela nem chama o saldo (`/mms/statement/amount/detail`), então o robô não a leria.
- **Lojas no painel:** migration 0501 (`barbosa (temu)` = loja 205925390 do Bling, nº
  635517739923741 na Temu; `jlas (temu)` = 206288651, nº 635517739909677), desde 24/10.

## Anexo — roteiro do ensaio (base do módulo `apps/executor/src/temu_extrato.ts`)

```js
// Ensaio da leitura do robô (17/11/2026) — só leitura, não envia nada. Apagar no fim.
import fs from 'node:fs'
import puppeteer from '../executor-logistica/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js'
const ADS = 'http://local.adspower.net:50325'
const PERFIL = process.env.PERFIL || 'k1dkfq5y'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const at = await (await fetch(`${ADS}/api/v1/browser/active?user_id=${PERFIL}`)).json()
const ws = at.data?.ws?.puppeteer
if (!ws) { console.log('perfil fechado'); process.exit(2) }
const browser = await puppeteer.connect({ browserWSEndpoint: ws, defaultViewport: null })
const page = await browser.newPage()

const CAMINHOS = {
  '/mms/statement/amount/detail': 'saldo',
  '/mms/statement/order/list': 'extrato',
  '/mms/statement/transaction/type/list': 'tipos',
  '/mms/settle/query/amount/selfproduce': 'previsto',
  '/mms/settle/query/period': 'prazo',
  '/mms/payment/disbursement/list': 'saques',
}
const vistas = new Map() // "nome:pagina" → corpo
page.on('response', async (resp) => {
  try {
    const u = new URL(resp.url())
    if (u.hostname !== 'br.seller.temu.com') return
    const nome = CAMINHOS[u.pathname]
    if (!nome) return
    const envio = JSON.parse(resp.request().postData() || '{}')
    const corpo = await resp.json()
    const pagina = envio.page ?? '-'
    vistas.set(`${nome}:${pagina}:${envio.size ?? '-'}`, { envio, corpo })
  } catch { /* resposta sem corpo */ }
})
async function esperar(prefixo, ms = 30000) {
  const fim = Date.now() + ms
  while (Date.now() < fim) {
    for (const [k, v] of vistas) if (k.startsWith(prefixo)) return v
    await sleep(500)
  }
  return null
}
async function clicarAba(texto) {
  return page.evaluate((t) => {
    const el = [...document.querySelectorAll('[data-testid="beast-core-tab-itemLabel"]')].find((e) => (e.innerText || '').trim() === t)
    if (!el) return false
    el.click()
    return true
  }, texto)
}
async function proxima() {
  return page.evaluate(() => {
    const pags = [...document.querySelectorAll('[data-testid="beast-core-pagination"]')].filter((e) => e.offsetParent !== null)
    const pg = pags[pags.length - 1]
    if (!pg) return 'sem_paginacao'
    const nx = pg.querySelector('[data-testid="beast-core-pagination-next"]')
    if (!nx || /disabled/i.test(nx.className)) return 'fim'
    nx.click()
    return 'ok'
  })
}
async function paginar(nome, primeira, tamanho, teto = 60) {
  const corpos = []
  let pagina = primeira
  for (let i = 0; i < teto; i++) {
    const r = await esperar(`${nome}:${pagina}:${tamanho}`)
    if (!r) return { corpos, erro: `página ${pagina} de ${nome} não chegou` }
    corpos.push(r.corpo)
    const p = await proxima()
    if (p !== 'ok') return { corpos, fim: p }
    pagina += 1
    await sleep(1200 + Math.random() * 800)
  }
  return { corpos, erro: 'teto de páginas' }
}

const saida = {}
await page.goto('https://br.seller.temu.com/payment.html', { waitUntil: 'domcontentloaded', timeout: 60000 })
const saldo = await esperar('saldo:', 45000)
if (/login\.html/.test(page.url()) || !saldo) { console.log(JSON.stringify({ ok: false, reason: /login/.test(page.url()) ? 'needs_manual_login' : 'tela não carregou', url: page.url() })); await page.close(); browser.disconnect(); process.exit(1) }
saida.saldo = saldo.corpo
saida.prazo = (await esperar('prazo:', 5000))?.corpo
saida.loja_tela = await page.evaluate(() => (document.body.innerText.match(/\nPT\n(.+)\n/) || [])[1] || null)
await sleep(1500)
saida.aba_extratos = await clicarAba('Extratos')
saida.extrato = await paginar('extrato', 1, 10)
saida.tipos = (await esperar('tipos:', 5000))?.corpo
saida.periodo_extrato = await page.evaluate(() => (document.querySelector('[data-testid="beast-core-rangePicker-htmlInput"]') || {}).value || null)
await sleep(1500)
saida.aba_previsto = await clicarAba('Pedidos não processados')
saida.previsto = await paginar('previsto', 0, 10)
await sleep(1500)
saida.aba_saques = await clicarAba('Pagamentos')
saida.saques = await paginar('saques', 1, 10, 5)
fs.writeFileSync('temu_saida/coleta.json', JSON.stringify(saida))
const linhas = saida.extrato.corpos.flatMap((c) => c.result?.mall_transaction_flow_volist || [])
const prev = saida.previsto.corpos.flatMap((c) => [...(c.result?.positive_settle_bill_list || []), ...(c.result?.negative_settle_bill_list || [])])
console.log(JSON.stringify({
  loja_tela: saida.loja_tela, periodo: saida.periodo_extrato,
  extrato: { paginas: saida.extrato.corpos.length, total_tela: saida.extrato.corpos[0]?.result?.total, linhas: linhas.length, fim: saida.extrato.fim, erro: saida.extrato.erro },
  previsto: { paginas: saida.previsto.corpos.length, linhas: prev.length, fim: saida.previsto.fim, erro: saida.previsto.erro, chaves: Object.keys(saida.previsto.corpos[0]?.result || {}) },
  saques: { paginas: saida.saques.corpos.length, total: saida.saques.corpos[0]?.result?.total, fim: saida.saques.fim, erro: saida.saques.erro },
}))
await page.close(); browser.disconnect()
```
