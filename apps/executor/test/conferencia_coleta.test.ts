/**
 * A coleta de uma loja de ponta a ponta, com uma "Shopee" falsa: a página
 * falsa roda o MESMO JS que vai pro browser (vm) com um fetch que responde no
 * formato real (números e nomes inventados). Sem AdsPower, sem rede, sem as
 * pausas de verdade.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import * as u from "../src/conferencia_util";
import { coletarNaPagina, _ritmo, type JobConferencia } from "../src/conferencia";

_ritmo.sleep = async () => undefined;

const CENTRAL = "https://seller.shopee.com.br/";
const LOGIN_URL = "https://accounts.shopee.com.br/seller/login?next=https%3A%2F%2Fseller.shopee.com.br%2F";

interface Opcoes {
  ultimoPublicado: string; // maior ymd da lista do seller_daily de S1
  logada: boolean;
  // "rede" = o fetch da página rejeita (sem resposta nenhuma)
  falhar?: (path: string, body: any) => { status: number; texto: string } | "rede" | null;
  semDiasAfiliadosS1?: boolean; // seller_daily de S1 com a lista vazia
  loginUrl?: string; // pra onde a Central manda quando deslogada
}

/** Responde como a Central do Vendedor (formas reais, números inventados). */
function shopeeFalsa(o: Opcoes) {
  const chamadas: string[] = [];
  const fetch = async (url: string, init: any) => {
    const path = url.split("?")[0];
    const qs = new URLSearchParams(url.split("?")[1] || "");
    const body = init.body ? JSON.parse(init.body) : null;
    chamadas.push(path);
    const r = (status: number, json: unknown) => ({
      status,
      redirected: false,
      url: "https://seller.shopee.com.br" + url,
      text: async () => JSON.stringify(json),
    });
    const f = o.falhar?.(path, body ?? Object.fromEntries(qs));
    if (f === "rede") throw new TypeError("Failed to fetch");
    if (f) return { status: f.status, redirected: false, url, text: async () => f.texto };
    if (!qs.get("SPC_CDS")) return r(400, { code: 9, msg: "sem SPC_CDS" });
    if (path === "/api/v2/login/") {
      return o.logada
        ? r(200, { username: "loja_teste", shopid: 42, shop_name: "Loja Teste", token: "SEGREDO" })
        : r(200, { error: "error_require_login" });
    }
    if (path === "/api/v3/affiliateplatform/dashboard/seller_daily") {
      const fim = u.somarDias(new Date((Number(qs.get("end_time")) - 3 * 3600) * 1000).toISOString().slice(0, 10), 0);
      const ultimo = fim > o.ultimoPublicado ? o.ultimoPublicado : fim;
      const vazia = o.semDiasAfiliadosS1 && fim === P.semanas[0].fim;
      return r(200, {
        code: 0,
        msg: "success",
        data: {
          list: vazia ? [] : [{ ymd: u.somarDias(ultimo, -1) }, { ymd: ultimo }],
          last_update_time: 1,
          total_order_count: 7,
          dis_total_actual_amount: "700.00",
          dis_total_seller_commission: "35.5",
        },
      });
    }
    if (path === "/api/v3/affiliateplatform/dashboard/seller_item_detail") {
      const pagina = Number(qs.get("page_num"));
      const n = pagina === 1 ? 20 : pagina === 2 ? 5 : 0;
      const list = Array.from({ length: n }, (_, i) => ({
        item_id: 50000000000 + pagina * 100 + i,
        item_name: `Item ${pagina}-${i}`,
        category_id: i === 0 ? 100010 : 100013,
        dis_gmv: "28.00",
        dis_spend: "1.42",
        orders: 1,
        content_info: [{ pesado: true }],
      }));
      return r(200, { code: 0, data: { list, page_num: pagina, page_size: 20, total_count: 25 } });
    }
    if (path === "/api/pas/v1/report/get_time_graph/") {
      assert.equal(body.end_time % 86400, (86400 - 1 + 3 * 3600) % 86400, "Ads tem que terminar 23:59:59 BRT");
      return r(200, {
        code: 0,
        data: {
          report_by_time: new Array(10).fill({ cost: 1 }),
          report_aggregate: { impression: 900, click: 30, cost: 4500000, broad_gmv: 60000000, broad_order: 3 },
        },
      });
    }
    if (path === "/api/pas/v1/homepage/query/") {
      const entry = (id: number | null, custo: number) => ({
        title: id ? `Anúncio ${id}` : "Anúncio da loja",
        type: id ? "product_manual" : "shop_manual",
        manual_product_ads: id ? { item_id: id } : null,
        report: { impression: 300, click: 10, cost: custo, broad_gmv: 20000000, broad_order: 1 },
        ratio: { x: 1 },
      });
      return r(200, {
        code: 0,
        data: { entry_list: [entry(61, 1500000), entry(62, 1500000), entry(null, 1500000)], total: 3 },
      });
    }
    if (path === "/api/mydata/v4/product/performance/") {
      return r(200, {
        code: 0,
        result: {
          total: 2,
          items: [
            { id: 71, name: "Produto 71", paid_sales: 10.1, paid_orders: 1, models: [{}] },
            { id: 72, name: "Produto 72", paid_sales: 5, paid_orders: 1, models: [{}] },
          ],
        },
      });
    }
    if (path === "/api/pas/v1/wallet/get/") {
      return r(200, { code: 0, data: { ads_credit: { available: 12345000 } } });
    }
    return r(404, { code: 404, msg: "rota desconhecida" });
  };
  return { fetch, chamadas };
}

function paginaFalsa(o: Opcoes, form?: u.FormLogin) {
  const shopee = shopeeFalsa(o);
  let url = "about:blank";
  let logada = o.logada;
  const cliques: [number, number][] = [];
  const page = {
    url: () => url,
    goto: async () => {
      url = logada ? CENTRAL : o.loginUrl ?? LOGIN_URL;
    },
    evaluate: async (script: string) => {
      if (script === u.SCRIPT_FORM_LOGIN) return form ?? null;
      if (script === u.SCRIPT_PEDE_CODIGO) return false;
      return vm.runInNewContext(script, {
        document: { cookie: "SPC_CDS=cds-teste; csrftoken=csrf-teste" },
        fetch: shopee.fetch,
        AbortController,
        setTimeout,
        clearTimeout,
      });
    },
    mouse: {
      click: async (x: number, y: number) => {
        cliques.push([x, y]);
        logada = true;
        o.logada = true;
        url = CENTRAL;
      },
    },
  };
  return { page: page as any, chamadas: shopee.chamadas, cliques };
}

const P = u.periodoConferencia("semanal", new Date("2026-10-06T16:30:00Z"));
let job: JobConferencia;
beforeEach(() => {
  job = {
    coleta_id: "c1",
    execucao_id: "e1",
    conta: "Teste",
    adspower_user_id: "k1teste",
    grupo: "celular",
    semanas: P.semanas,
    afiliados_ate: P.afiliados_ate,
    esperar_afiliados_ate: new Date(Date.now() + 3600_000).toISOString(),
    corte: new Date(Date.now() + 7200_000).toISOString(),
    login_auto: true,
    tentativa: 1,
  };
});

test("loja completa: 4 semanas, paginação, somas e saldo → ok", async () => {
  const f = paginaFalsa({ ultimoPublicado: "2026-10-04", logada: true });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "ok", r.erro || "");
  const d = r.dados!;
  // login + S1 daily + S1 (2 itens afil. + Ads + anúncios + 7 dias) + 3×(daily + 11) + carteira
  assert.equal(d.chamadas, 50);
  assert.equal(f.chamadas.length, 50);
  assert.deepEqual(d.login, { username: "loja_teste", shopid: 42, shop_name: "Loja Teste" });
  assert.equal(d.login_auto_usado, false);
  assert.equal(d.saldo_ads, 123.45);
  assert.equal(d.afiliados_ultimo_dia, "2026-10-04");
  assert.deepEqual(d.avisos, []);
  const s1 = d.semanas[0];
  assert.equal(s1.inicio, "2026-09-28");
  assert.deepEqual(s1.afiliados, { vendas: 700, comissao: 35.5, pedidos: 7 });
  assert.equal(s1.afiliados_itens!.length, 25);
  assert.equal(typeof s1.afiliados_itens![0].item_id, "string");
  assert.deepEqual(s1.ads, { impressoes: 900, cliques: 30, gasto: 45, vendas: 600, pedidos: 3 });
  assert.equal(s1.ads_itens!.length, 3);
  assert.equal(s1.ads_itens![2].item_id, null);
  assert.deepEqual(s1.vendas, { valor: 105.7, pedidos: 14 });
  assert.deepEqual(s1.vendas_itens![0], { item_id: "71", nome: "Produto 71", valor: 70.7, pedidos: 7 });
  assert.equal(d.semanas[3].inicio, "2026-09-07");
  assert.ok(!JSON.stringify(r).includes("SEGREDO"), "token do login não pode vazar");
  assert.ok(!JSON.stringify(r).includes("pesado"), "blocos podados não sobem");
});

test("afiliados do último dia ainda não saíram e dentro do prazo → aguardando_afiliados", async () => {
  const f = paginaFalsa({ ultimoPublicado: "2026-10-03", logada: true });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "aguardando_afiliados");
  assert.equal(r.dados, null);
  assert.match(r.erro || "", /só até 03\/10; falta 04\/10/);
  assert.equal(f.chamadas.length, 2); // login + seller_daily de S1, nada mais
});

test("passou do prazo dos afiliados → coleta com aviso", async () => {
  job.esperar_afiliados_ate = new Date(Date.now() - 1000).toISOString();
  const f = paginaFalsa({ ultimoPublicado: "2026-10-03", logada: true });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "ok");
  assert.deepEqual(r.dados!.avisos, ["afiliados só até 03/10"]);
  assert.equal(r.dados!.afiliados_ultimo_dia, "2026-10-03");
});

test("uma chamada com erro comum → só a seção fica null, status parcial", async () => {
  const s2 = P.semanas[1];
  const f = paginaFalsa({
    ultimoPublicado: "2026-10-04",
    logada: true,
    falhar: (path, body) =>
      path === "/api/pas/v1/report/get_time_graph/" && body.start_time === u.epochInicioDia(s2.inicio)
        ? { status: 500, texto: "erro interno" }
        : null,
  });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "parcial");
  assert.equal(r.dados!.semanas[1].ads, null);
  assert.deepEqual(r.dados!.semanas[1].avisos, ["Ads: HTTP 500"]);
  assert.notEqual(r.dados!.semanas[1].ads_itens, null);
  assert.notEqual(r.dados!.semanas[0].ads, null);
  assert.match(r.erro || "", /1 de 25 partes sem dados/);
});

test("um dia de vendas que falha derruba as vendas da semana (não soma pela metade)", async () => {
  const dia = u.epochInicioDia("2026-10-01");
  const f = paginaFalsa({
    ultimoPublicado: "2026-10-04",
    logada: true,
    falhar: (path, q) =>
      path === "/api/mydata/v4/product/performance/" && Number(q.start_time) === dia
        ? { status: 200, texto: JSON.stringify({ code: 7, msg: "timeout" }) }
        : null,
  });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "parcial");
  assert.equal(r.dados!.semanas[0].vendas, null);
  assert.equal(r.dados!.semanas[0].vendas_itens, null);
  assert.deepEqual(r.dados!.semanas[0].avisos, ["vendas: code 7 timeout"]);
});

test("429 no meio → bloqueada, sem dados, para na hora", async () => {
  let n = 0;
  const f = paginaFalsa({
    ultimoPublicado: "2026-10-04",
    logada: true,
    falhar: (path) => (path === "/api/pas/v1/homepage/query/" && ++n === 2 ? { status: 429, texto: "" } : null),
  });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "bloqueada");
  assert.equal(r.dados, null);
  assert.equal(f.chamadas.at(-1), "/api/pas/v1/homepage/query/");
});

test("deslogada sem login automático → deslogada, nenhum clique", async () => {
  job.login_auto = false;
  const f = paginaFalsa({ ultimoPublicado: "2026-10-04", logada: false });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "deslogada");
  assert.equal(f.cliques.length, 0);
  assert.equal(f.chamadas.length, 0); // na tela de login nem chama a API
});

const FORM_CHEIO: u.FormLogin = {
  senha: 1,
  senhaCheia: true,
  usuario: 1,
  usuarioCheio: true,
  pedeCodigo: false,
  botoes: 1,
  botao: { x: 640, y: 420, livre: true, desabilitado: false },
};

test("deslogada com usuário e senha preenchidos → UM clique em Entrar e coleta", async () => {
  const f = paginaFalsa({ ultimoPublicado: "2026-10-04", logada: false }, FORM_CHEIO);
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "ok", r.erro || "");
  assert.deepEqual(f.cliques, [[640, 420]]);
  assert.equal(r.dados!.login_auto_usado, true);
});

test("tela de login sem senha preenchida → deslogada, nenhum clique", async () => {
  const f = paginaFalsa({ ultimoPublicado: "2026-10-04", logada: false }, { ...FORM_CHEIO, senhaCheia: false });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "deslogada");
  assert.match(r.erro || "", /não tem usuário e senha preenchidos/);
  assert.equal(f.cliques.length, 0);
});

test("tela de login pedindo código → deslogada, nenhum clique", async () => {
  const f = paginaFalsa({ ultimoPublicado: "2026-10-04", logada: false }, { ...FORM_CHEIO, pedeCodigo: true });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "deslogada");
  assert.match(r.erro || "", /código\/verificação/);
  assert.equal(f.cliques.length, 0);
});

test("botão Entrar coberto → não clica", async () => {
  const f = paginaFalsa(
    { ultimoPublicado: "2026-10-04", logada: false },
    { ...FORM_CHEIO, botao: { ...FORM_CHEIO.botao!, livre: false } }
  );
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "deslogada");
  assert.equal(f.cliques.length, 0);
});

test("S1 sem nenhum dia de afiliados (loja sem venda de afiliado) → não espera, coleta sem aviso", async () => {
  const f = paginaFalsa({ ultimoPublicado: "2026-10-04", logada: true, semDiasAfiliadosS1: true });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "ok", r.erro || "");
  assert.equal(r.dados!.afiliados_ultimo_dia, null, "null = nada a esperar (o servidor não lista como incompleta)");
  assert.deepEqual(r.dados!.avisos, []);
  assert.equal(f.chamadas.length, 50, "coletou a loja inteira, sem voltar pra fila");
});

// A 1ª chamada da loja é o /api/v2/login/: bloqueio ali para NA HORA — sem
// recarregar a Central e sem chamar de novo logo depois do limite.
for (const [nome, falha] of [
  ["429", { status: 429, texto: "" }],
  ["403", { status: 403, texto: "" }],
  ["captcha em JSON", { status: 200, texto: JSON.stringify({ error: "captcha_required", msg: "please verify captcha" }) }],
] as const) {
  test(`${nome} no login → bloqueada com UMA chamada`, async () => {
    for (const loginAuto of [true, false]) {
      job.login_auto = loginAuto;
      const f = paginaFalsa({
        ultimoPublicado: "2026-10-04",
        logada: true,
        falhar: (path) => (path === "/api/v2/login/" ? falha : null),
      });
      const r = await coletarNaPagina(job, f.page);
      assert.equal(r.status, "bloqueada", `${r.erro} (login_auto=${loginAuto})`);
      assert.equal(r.dados, null);
      assert.deepEqual(f.chamadas, ["/api/v2/login/"]);
      assert.equal(f.cliques.length, 0);
    }
  });
}

test("rede falha no login uma vez → recarrega, tenta de novo e coleta", async () => {
  let n = 0;
  const f = paginaFalsa({
    ultimoPublicado: "2026-10-04",
    logada: true,
    falhar: (path) => (path === "/api/v2/login/" && ++n === 1 ? "rede" : null),
  });
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "ok", r.erro || "");
  assert.deepEqual(f.chamadas.slice(0, 2), ["/api/v2/login/", "/api/v2/login/"]);
});

test("rede falha no login duas vezes → erro (não é deslogada) e nenhum clique", async () => {
  const f = paginaFalsa(
    { ultimoPublicado: "2026-10-04", logada: true, falhar: (path) => (path === "/api/v2/login/" ? "rede" : null) },
    FORM_CHEIO
  );
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "erro");
  assert.match(r.erro || "", /não deu pra conferir o login/);
  assert.equal(f.chamadas.length, 2);
  assert.equal(f.cliques.length, 0);
});

test("tela de login fora da Shopee (portal do proxy) → deslogada, nenhum clique", async () => {
  const f = paginaFalsa(
    { ultimoPublicado: "2026-10-04", logada: false, loginUrl: "https://portal-do-proxy.example.net/login" },
    FORM_CHEIO
  );
  const r = await coletarNaPagina(job, f.page);
  assert.equal(r.status, "deslogada");
  assert.match(r.erro || "", /não é da Shopee \(portal-do-proxy\.example\.net\)/);
  assert.equal(f.cliques.length, 0);
});
