/**
 * Teste das peças puras da Conferência Shopee (sem AdsPower, sem Shopee).
 * As respostas imitam o FORMATO das reais (conferidas na Barbosa em
 * 06/10/2026), com números e nomes inventados.
 *
 *   npm test
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import * as u from "../src/conferencia_util";

// ---------------------------------------------------------------------------
// Datas
// ---------------------------------------------------------------------------
test("epoch BRT: 00:00 e 23:59:59 (os números que a Shopee aceitou ao vivo)", () => {
  assert.equal(u.epochInicioDia("2026-09-29"), 1790650800);
  assert.equal(u.epochFimDia("2026-10-05"), 1791255599);
  assert.equal(u.epochInicioDia("2026-10-04"), 1791082800);
  assert.equal(u.epochInicioDia("2026-09-01"), 1788231600);
});

test("somarDias atravessa mês e ano", () => {
  assert.equal(u.somarDias("2026-09-30", 1), "2026-10-01");
  assert.equal(u.somarDias("2026-01-01", -1), "2025-12-31");
  assert.equal(u.somarDias("2026-10-06", -21), "2026-09-15");
});

test("hojeBRT usa o fuso de São Paulo", () => {
  // 02:30 UTC de segunda = 23:30 de domingo em Brasília
  assert.equal(u.hojeBRT(new Date("2026-10-05T02:30:00Z")), "2026-10-04");
  assert.equal(u.hojeBRT(new Date("2026-10-05T03:00:00Z")), "2026-10-05");
});

test("semanal criada na terça 06/10/2026 (exemplo do contrato)", () => {
  const p = u.periodoConferencia("semanal", new Date("2026-10-06T16:30:00Z"));
  assert.equal(p.tipo, "semanal");
  assert.deepEqual(p.semanas, [
    { inicio: "2026-09-28", fim: "2026-10-04" },
    { inicio: "2026-09-21", fim: "2026-09-27" },
    { inicio: "2026-09-14", fim: "2026-09-20" },
    { inicio: "2026-09-07", fim: "2026-09-13" },
  ]);
  assert.equal(p.afiliados_ate, "2026-10-04");
});

test("parcial criada na quinta 08/10/2026 (exemplo do contrato)", () => {
  const p = u.periodoConferencia("parcial", new Date("2026-10-08T16:30:00Z"));
  assert.equal(p.tipo, "parcial");
  assert.deepEqual(p.semanas, [
    { inicio: "2026-10-05", fim: "2026-10-07" },
    { inicio: "2026-09-28", fim: "2026-09-30" },
    { inicio: "2026-09-21", fim: "2026-09-23" },
    { inicio: "2026-09-14", fim: "2026-09-16" },
  ]);
  assert.equal(p.afiliados_ate, "2026-10-07");
});

test("parcial numa segunda cai para semanal", () => {
  const p = u.periodoConferencia("parcial", new Date("2026-10-05T15:00:00Z"));
  assert.equal(p.tipo, "semanal");
  assert.deepEqual(p.semanas[0], { inicio: "2026-09-28", fim: "2026-10-04" });
});

test("domingo 23:30 BRT ainda é a semana do domingo", () => {
  const p = u.periodoConferencia("semanal", new Date("2026-10-05T02:30:00Z"));
  assert.deepEqual(p.semanas[0], { inicio: "2026-09-21", fim: "2026-09-27" });
});

test("diasDaSemana e ddmm", () => {
  assert.equal(u.diasDaSemana({ inicio: "2026-09-28", fim: "2026-10-04" }).length, 7);
  assert.deepEqual(u.diasDaSemana({ inicio: "2026-10-05", fim: "2026-10-07" }), [
    "2026-10-05",
    "2026-10-06",
    "2026-10-07",
  ]);
  assert.equal(u.ddmm("2026-10-04"), "04/10");
  assert.equal(u.isoUtc(Date.UTC(2026, 9, 6, 16, 41, 2, 789)), "2026-10-06T16:41:02Z");
});

// ---------------------------------------------------------------------------
// Dinheiro
// ---------------------------------------------------------------------------
test("dinheiro: dis_* em R$ e inteiros ÷ 100.000", () => {
  assert.equal(u.reais("1234.56789"), 1234.57);
  assert.equal(u.reais(98.5), 98.5);
  assert.equal(u.reais(null), null);
  assert.equal(u.reais("abc"), null);
  assert.equal(u.deCentMil(12345678), 123.46);
  assert.equal(u.deCentMil(50000000), 500);
  assert.equal(u.deCentMil(0), 0);
  assert.equal(u.deCentMil(undefined), null);
});

// ---------------------------------------------------------------------------
// Leitura das respostas (formas reais, números inventados)
// ---------------------------------------------------------------------------
const DAILY = {
  list: [
    { ymd: "2026-10-03", dis_actual_amount: "100.00", order_count: 2 },
    { ymd: "2026-10-04", dis_actual_amount: "50.00", order_count: 1 },
    { ymd: "2026-10-01", dis_actual_amount: "70.00", order_count: 1 },
  ],
  last_update_time: 1791285601,
  total_order_count: 4,
  dis_total_actual_amount: "220.00",
  dis_total_seller_commission: "11.12345",
  clicks: 1234,
};

test("seller_daily: totais e último dia = maior ymd (fora de ordem)", () => {
  const r = u.afiliadosDoDaily(DAILY);
  assert.deepEqual(r.totais, { vendas: 220, comissao: 11.12, pedidos: 4, cliques: 1234 });
  assert.deepEqual(Object.keys(r.totais), ["vendas", "comissao", "pedidos", "cliques"]);
  assert.equal(r.ultimoDia, "2026-10-04");
  assert.equal(u.afiliadosDoDaily({ ...DAILY, list: [] }).ultimoDia, null);
  assert.throws(() => u.afiliadosDoDaily({ list: [] }), u.ErroFormato);
});

test("seller_daily: clicks é opcional — sem ele a seção vale, com cliques null (nunca 0)", () => {
  const { clicks: _, ...semCliques } = DAILY;
  assert.deepEqual(u.afiliadosDoDaily(semCliques).totais, { vendas: 220, comissao: 11.12, pedidos: 4, cliques: null });
  assert.equal(u.afiliadosDoDaily({ ...DAILY, clicks: null }).totais.cliques, null);
  assert.equal(u.afiliadosDoDaily({ ...DAILY, clicks: "" }).totais.cliques, null);
  assert.equal(u.afiliadosDoDaily({ ...DAILY, clicks: "abc" }).totais.cliques, null);
  assert.equal(u.afiliadosDoDaily({ ...DAILY, clicks: "57" }).totais.cliques, 57);
  assert.equal(u.afiliadosDoDaily({ ...DAILY, clicks: 0 }).totais.cliques, 0, "0 de verdade continua 0");
});

test("seller_item_detail: item_id vira string, categoria nível 1", () => {
  const itens = u.afiliadosItens([
    { item_id: 51234567890, item_name: "Fritadeira Teste", category_id: 100010, dis_gmv: "300.5", dis_spend: "9.87654", orders: 3, clicks: 812 },
    { item_id: 51234567891, item_name: "  Capinha   Teste ", category_id: 100013, dis_gmv: "20", dis_spend: "1", orders: 1 },
  ]);
  assert.deepEqual(itens[0], {
    item_id: "51234567890",
    nome: "Fritadeira Teste",
    categoria_id: 100010,
    vendas: 300.5,
    comissao: 9.88,
    pedidos: 3,
    cliques: 812,
  });
  assert.equal(itens[1].nome, "Capinha Teste");
  assert.equal(itens[1].cliques, null, "item sem clicks → null, nunca 0");
  assert.ok("cliques" in itens[1], "a chave vai sempre, mesmo null");
  assert.deepEqual(u.afiliadosItens(null), []);
});

test("get_time_graph: report_aggregate ÷ 100.000", () => {
  const agg = { impression: 1000, click: 50, cost: 2500000, broad_gmv: 30000000, broad_order: 2, ctr: 0.05 };
  assert.deepEqual(u.adsDoAgregado(agg), { impressoes: 1000, cliques: 50, gasto: 25, vendas: 300, pedidos: 2 });
  assert.throws(() => u.adsDoAgregado(null), u.ErroFormato);
  assert.throws(() => u.adsDoAgregado({ impression: 1 }), u.ErroFormato);
});

test("homepage/query: anúncio sem produto fica com item_id null", () => {
  const itens = u.adsItens([
    {
      title: "Anúncio A",
      type: "product_manual",
      manual_product_ads: { item_id: 59999999999 },
      report: { impression: 10, click: 2, cost: 150000, broad_gmv: 1000000, broad_order: 1 },
    },
    {
      title: "Loja",
      type: "shop_manual",
      manual_product_ads: null,
      report: { impression: 5, click: 1, cost: 50000, broad_gmv: 0, broad_order: 0 },
    },
  ]);
  assert.equal(itens[0].item_id, "59999999999");
  assert.equal(itens[0].gasto, 1.5);
  assert.equal(itens[0].vendas, 10);
  assert.equal(itens[1].item_id, null);
  assert.equal(itens[1].tipo, "shop_manual");
  assert.throws(() => u.adsItens([{ title: "sem report" }]), u.ErroFormato);
});

test("product/performance: soma por item nos dias da semana", () => {
  const acc = new Map<string, u.VendaItem>();
  u.acumularVendas(acc, [
    { id: 111, name: "Produto X", paid_sales: 100.1, paid_orders: 1 },
    { id: 222, name: "Produto Y", paid_sales: 50, paid_orders: 2 },
  ]);
  u.acumularVendas(acc, [{ id: 111, name: "Produto X", paid_sales: 200.2, paid_orders: 2 }]);
  u.acumularVendas(acc, []);
  const r = u.vendasDoAcumulado(acc);
  assert.deepEqual(r.totais, { valor: 350.3, pedidos: 5 });
  assert.deepEqual(r.itens[0], { item_id: "111", nome: "Produto X", valor: 300.3, pedidos: 3 });
  assert.equal(r.itens[1].item_id, "222");
});

test("wallet: ads_credit.available ÷ 100.000", () => {
  assert.equal(u.saldoDaCarteira({ ads_credit: { available: 12345000, total: 99 } }), 123.45);
  assert.throws(() => u.saldoDaCarteira({}), u.ErroFormato);
});

// ---------------------------------------------------------------------------
// Paginação e chamadas
// ---------------------------------------------------------------------------
test("paginação: 45 itens em páginas de 20 = 3 páginas; teto e página vazia param", () => {
  let paginas = 0;
  let vistos = 0;
  for (let p = 1; ; p++) {
    paginas++;
    const veio = Math.min(20, 45 - vistos);
    vistos += veio;
    if (!u.continuarPaginando(vistos, 45, p, 25, veio)) break;
  }
  assert.equal(paginas, 3);
  assert.equal(u.continuarPaginando(500, 900, 25, 25, 20), false); // teto
  assert.equal(u.continuarPaginando(40, 900, 2, 25, 0), false); // página vazia
  assert.equal(u.continuarPaginando(50, 120, 1, 10, 50), true); // homepage: offset+limit
  assert.equal(u.continuarPaginando(150, 120, 3, 10, 20), false);
});

test("Ads pede fim 23:59:59; vendas pede o dia até 00:00 do seguinte", () => {
  const s = { inicio: "2026-09-28", fim: "2026-10-04" };
  const ag = u.reqAdsAgregado(s).body as any;
  assert.equal(ag.end_time, u.epochInicioDia("2026-10-05") - 1);
  assert.equal(ag.start_time, u.epochInicioDia("2026-09-28"));
  assert.equal((u.reqAdsAnuncios(s, 50).body as any).offset, 50);
  const v = u.reqVendasDia("2026-10-04", 2).path;
  assert.match(v, /start_time=1791082800&end_time=1791169200/);
  assert.match(v, /category_id=-1/);
  assert.match(v, /page_num=2/);
  assert.match(u.reqItensAfiliados(s, 3).path, /page_num=3&page_size=20/);
  assert.match(u.reqSellerDaily(s).path, /end_time=1791169199/);
});

/** Roda o JS de página num contexto isolado com document/fetch falsos. */
async function rodarNaPagina(script: string, resposta: { status: number; texto: string; redirected?: boolean; url?: string }) {
  const pedidos: { url: string; init: any }[] = [];
  const ctx = {
    document: { cookie: "SPC_CDS=abc-123; csrftoken=tok%20x; outro=1" },
    fetch: async (url: string, init: any) => {
      pedidos.push({ url, init });
      return {
        status: resposta.status,
        redirected: !!resposta.redirected,
        url: resposta.url || "https://seller.shopee.com.br" + url,
        text: async () => resposta.texto,
      };
    },
    AbortController,
    setTimeout,
    clearTimeout,
  };
  const r = await vm.runInNewContext(script, ctx);
  return { r: JSON.parse(JSON.stringify(r)), pedidos };
}

test("JS de página: SPC_CDS na URL, csrftoken no POST, poda dos blocos pesados", async () => {
  const s = { inicio: "2026-09-28", fim: "2026-10-04" };
  const corpo = { code: 0, data: { report_aggregate: { impression: 1 }, report_by_time: [{ x: 1 }] } };
  const { r, pedidos } = await rodarNaPagina(u.scriptChamada(u.reqAdsAgregado(s), 1000), {
    status: 200,
    texto: JSON.stringify(corpo),
  });
  assert.equal(pedidos[0].url, "/api/pas/v1/report/get_time_graph/?SPC_CDS=abc-123&SPC_CDS_VER=2");
  assert.equal(pedidos[0].init.method, "POST");
  assert.equal(pedidos[0].init.credentials, "include");
  assert.equal(pedidos[0].init.headers["x-csrftoken"], "tok%20x");
  assert.equal(JSON.parse(pedidos[0].init.body).end_time, 1791169199);
  assert.equal(r.status, 200);
  assert.deepEqual(r.body, { code: 0, data: { report_aggregate: { impression: 1 } } });

  const lista = { code: 0, result: { total: 1, items: [{ id: 1, paid_sales: 2, models: [{ a: 1 }] }] } };
  const g = await rodarNaPagina(u.scriptChamada(u.reqVendasDia("2026-10-04", 1), 1000), {
    status: 200,
    texto: JSON.stringify(lista),
  });
  assert.match(g.pedidos[0].url, /&SPC_CDS=abc-123&SPC_CDS_VER=2$/);
  assert.equal(g.pedidos[0].init.headers["x-csrftoken"], undefined);
  assert.equal(g.pedidos[0].init.body, undefined);
  assert.deepEqual(g.r.body.result.items, [{ id: 1, paid_sales: 2 }]);
});

test("JS de página: do login só saem username/shopid/shop_name (nada de token)", async () => {
  const login = { username: "loja_teste", shopid: 123, shop_name: "Loja Teste", token: "SEGREDO", sub_account_token: "X" };
  const { r } = await rodarNaPagina(u.scriptChamada(u.REQ_LOGIN, 1000), { status: 200, texto: JSON.stringify(login) });
  assert.deepEqual(r.body, { username: "loja_teste", shopid: 123, shop_name: "Loja Teste" });
  assert.ok(!JSON.stringify(r).includes("SEGREDO"));
  // Pedido de captcha/login: o código e a mensagem saem (classificarResposta precisa), o token não.
  const captcha = { error: "captcha_required", msg: "please verify captcha", token: "SEGREDO", data: { sess: "SEGREDO" } };
  const c = await rodarNaPagina(u.scriptChamada(u.REQ_LOGIN, 1000), { status: 200, texto: JSON.stringify(captcha) });
  assert.deepEqual(c.r.body, { username: null, shopid: null, shop_name: null, error: "captcha_required", msg: "please verify captcha" });
  assert.ok(!JSON.stringify(c.r).includes("SEGREDO"));
  assert.equal(u.classificarResposta(c.r).tipo, "bloqueada");
  const sair = await rodarNaPagina(u.scriptChamada(u.REQ_LOGIN, 1000), { status: 200, texto: JSON.stringify({ error: "error_require_login" }) });
  assert.equal(u.classificarResposta(sair.r).tipo, "deslogada");
  const fora = await rodarNaPagina(u.scriptChamada(u.REQ_LOGIN, 1000), { status: 200, texto: "<html>token=SEGREDO</html>" });
  assert.equal(fora.r.body, null);
  assert.ok(!JSON.stringify(fora.r).includes("SEGREDO"));
});

test("JS de página: erro de rede vira status -1", async () => {
  const ctx = {
    document: { cookie: "" },
    fetch: async () => {
      throw new Error("Failed to fetch");
    },
    AbortController,
    setTimeout,
    clearTimeout,
  };
  const r = await vm.runInNewContext(u.scriptChamada(u.REQ_CARTEIRA, 1000), ctx);
  assert.equal(r.status, -1);
  assert.match(r.erro, /Failed to fetch/);
});

test("JS da tela de login compila (não roda: precisa de DOM)", () => {
  assert.doesNotThrow(() => new vm.Script(u.SCRIPT_FORM_LOGIN));
  assert.doesNotThrow(() => new vm.Script(u.SCRIPT_PEDE_CODIGO));
});

// ---------------------------------------------------------------------------
// Classificação das respostas
// ---------------------------------------------------------------------------
test("403/429 e captcha bloqueiam; login/signin desloga; o resto é falha da seção", () => {
  assert.equal(u.classificarResposta({ status: 200, body: { code: 0, data: {} } }).tipo, "ok");
  assert.equal(u.classificarResposta({ status: 403, body: null, raw: "" }).tipo, "bloqueada");
  assert.equal(u.classificarResposta({ status: 429, body: { code: 0 } }).tipo, "bloqueada");
  assert.equal(u.classificarResposta({ status: 200, body: { code: 90309999, msg: "need captcha" } }).tipo, "bloqueada");
  assert.equal(u.classificarResposta({ status: 200, body: { code: 3, msg: "please verify" } }).tipo, "bloqueada");
  assert.equal(u.classificarResposta({ status: 200, body: { code: 2, msg: "not logged in" } }).tipo, "deslogada");
  assert.equal(u.classificarResposta({ status: 401, body: {} }).tipo, "deslogada");
  assert.equal(
    u.classificarResposta({
      status: 200,
      redirected: true,
      url: "https://seller.shopee.com.br/account/signin?next=x",
      body: null,
      raw: "<html>",
    }).tipo,
    "deslogada"
  );
  const invalida = u.classificarResposta({ status: 200, body: { code: 5, msg: "invalid request" } });
  assert.deepEqual(invalida, { tipo: "falha", detalhe: "code 5 invalid request" });
  assert.equal(u.classificarResposta({ status: 500, body: null, raw: "oops" }).tipo, "falha");
  assert.equal(u.classificarResposta({ status: 200, body: null, raw: '<meta name="robots"> verify.js' }).tipo, "falha");
  assert.equal(u.classificarResposta({ status: -1, erro: "Failed to fetch" }).tipo, "falha");
});

test("URLs: Central × login × verificação", () => {
  assert.equal(u.naCentral("https://seller.shopee.com.br/"), true);
  assert.equal(u.naCentral("https://seller.shopee.com.br/portal/marketing/pas/index?x=1"), true);
  assert.equal(u.naCentral("https://seller.shopee.com.br/account/signin?next=%2Fportal"), false);
  assert.equal(u.naCentral("https://accounts.shopee.com.br/seller/login?next=https%3A%2F%2Fseller.shopee.com.br"), false);
  assert.equal(u.urlPedeVerificacao("https://accounts.shopee.com.br/seller/login"), false);
  assert.equal(u.urlPedeVerificacao("https://accounts.shopee.com.br/verify/traffic"), true);
  assert.equal(u.urlPedeVerificacao("https://accounts.shopee.com.br/seller/otp"), true);
  assert.equal(u.urlPedeVerificacao("https://seller.shopee.com.br/portal/hotpage"), false);
  // Onde o robô pode clicar em "Entrar" (o formulário leva a senha do perfil).
  assert.equal(u.telaDeLoginDaShopee("https://accounts.shopee.com.br/seller/login?next=x"), true);
  assert.equal(u.telaDeLoginDaShopee("https://seller.shopee.com.br/account/signin"), true);
  assert.equal(u.telaDeLoginDaShopee("https://shopee.com.br/buyer/login"), true);
  assert.equal(u.telaDeLoginDaShopee("https://portal-do-proxy.example.net/login"), false);
  assert.equal(u.telaDeLoginDaShopee("https://accounts.shopee.com.br.golpe.net/seller/login"), false);
  assert.equal(u.telaDeLoginDaShopee("https://golpe.net/?next=https://accounts.shopee.com.br"), false);
  assert.equal(u.telaDeLoginDaShopee("http://accounts.shopee.com.br/seller/login"), false, "sem https não");
  assert.equal(u.telaDeLoginDaShopee("about:blank"), false);
  assert.equal(u.hostDaUrl("https://Portal.Example.net/x"), "portal.example.net");
  assert.equal(u.hostDaUrl("não é url"), "");
});

// ---------------------------------------------------------------------------
// Ritmo, relógio, payload
// ---------------------------------------------------------------------------
test("pausa 1,2–1,8 s e salto de relógio > 2 min além da pausa", () => {
  assert.equal(u.pausaAleatoria(() => 0), 1200);
  assert.equal(u.pausaAleatoria(() => 0.999999), 1800);
  for (let i = 0; i < 100; i++) {
    const p = u.pausaAleatoria();
    assert.ok(p >= 1200 && p <= 1800);
  }
  assert.equal(u.relogioPulou(1500, 1600), false);
  assert.equal(u.relogioPulou(1500, 1500 + 120_000), false);
  assert.equal(u.relogioPulou(1500, 1500 + 120_001), true);
});

function semanaCheia(s: u.Semana): u.SemanaDados {
  return {
    ...u.semanaVazia(s),
    afiliados: { vendas: 1, comissao: 0.1, pedidos: 1, cliques: 10 },
    afiliados_itens: [],
    ads: { impressoes: 1, cliques: 0, gasto: 0, vendas: 0, pedidos: 0 },
    ads_itens: [],
    vendas: { valor: 1, pedidos: 1 },
    vendas_itens: [],
  };
}

test("payload v1: chaves na ordem do contrato e status ok/parcial/erro", () => {
  const sem = u.periodoConferencia("semanal", new Date("2026-10-06T16:30:00Z")).semanas;
  const d = u.montarDados({
    inicioMs: Date.UTC(2026, 9, 6, 16, 39, 27, 0),
    fimMs: Date.UTC(2026, 9, 6, 16, 41, 2, 250),
    chamadas: 63,
    login: { username: "loja_teste", shopid: 1, shop_name: "Loja" },
    loginAutoUsado: false,
    saldo: 10.5,
    ultimoDia: "2026-10-04",
    semanas: sem.map(semanaCheia),
    avisos: [],
  });
  assert.deepEqual(Object.keys(d), [
    "versao",
    "coletado_em",
    "duracao_s",
    "chamadas",
    "login",
    "login_auto_usado",
    "saldo_ads",
    "afiliados_ultimo_dia",
    "semanas",
    "avisos",
  ]);
  assert.deepEqual(Object.keys(d.semanas[0]), [
    "inicio",
    "fim",
    "afiliados",
    "afiliados_itens",
    "ads",
    "ads_itens",
    "vendas",
    "vendas_itens",
    "avisos",
  ]);
  assert.equal(d.versao, 1);
  assert.equal(d.coletado_em, "2026-10-06T16:41:02Z");
  assert.equal(d.duracao_s, 95.3);
  assert.deepEqual(u.statusDaColeta(d), { status: "ok", erro: null });

  const parcial = { ...d, saldo_ads: null };
  assert.equal(u.statusDaColeta(parcial).status, "parcial");

  const vazia = { ...d, saldo_ads: null, semanas: sem.map((s) => ({ ...u.semanaVazia(s), avisos: ["Ads: HTTP 500"] })) };
  const e = u.statusDaColeta(vazia);
  assert.equal(e.status, "erro");
  assert.match(e.erro || "", /Ads: HTTP 500/);

  const texto = u.resumoConferencia("ok", null, d);
  assert.match(texto, /S1 28\/09–04\/10/);
  assert.match(texto, /loja_teste/);
  assert.match(texto, /1 ped\. · 10 cliques/);
  const semCliques = { ...d, semanas: d.semanas.map((s) => ({ ...s, afiliados: { ...s.afiliados!, cliques: null } })) };
  assert.match(u.resumoConferencia("ok", null, semCliques), /1 ped\. · — cliques/);
});

test("validarJob recusa job torto antes de abrir perfil", () => {
  const p = u.periodoConferencia("semanal", new Date("2026-10-06T16:30:00Z"));
  const ok = {
    adspower_user_id: "k1abc",
    semanas: p.semanas,
    afiliados_ate: p.afiliados_ate,
    esperar_afiliados_ate: "2026-10-06T18:00:00Z",
    corte: "2026-10-06T20:30:00Z",
  };
  assert.equal(u.validarJob(ok), null);
  assert.match(u.validarJob({ ...ok, semanas: p.semanas.slice(0, 3) }) || "", /4 semanas/);
  assert.match(u.validarJob({ ...ok, adspower_user_id: "" }) || "", /adspower_user_id/);
  assert.match(u.validarJob({ ...ok, corte: "amanhã" }) || "", /corte/);
  assert.match(
    u.validarJob({ ...ok, semanas: [{ inicio: "2026-10-04", fim: "2026-09-28" }, ...p.semanas.slice(1)] }) || "",
    /semana inválida/
  );
});
