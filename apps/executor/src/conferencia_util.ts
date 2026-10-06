/**
 * Conferência Shopee — as peças PURAS da coleta (sem AdsPower, sem browser,
 * sem rede): datas em epoch BRT, conversão de dinheiro, montagem das
 * chamadas e do payload (ColetaDados v1), conta de páginas e a leitura das
 * respostas da Central do Vendedor. Ficam aqui pra o teste
 * (test/conferencia_util.test.ts) rodar com respostas sintéticas, sem abrir
 * perfil nenhum. Quem abre o perfil e faz as chamadas é o conferencia.ts.
 *
 * Formatos conferidos ao vivo na Barbosa em 06/10/2026 (contrato §8):
 *   - afiliados (`dis_*`) já vêm em R$ como string;
 *   - Ads e saldo vêm em inteiros ÷ 100.000;
 *   - `product/performance` vem em R$ (float).
 */

/** Uma semana do relatório (datas BRT, inclusive). */
export interface Semana {
  inicio: string; // YYYY-MM-DD
  fim: string;
}

export interface LoginLoja {
  username: string;
  shopid: number | null;
  shop_name: string | null;
}

export interface AfiliadosTotais {
  vendas: number;
  comissao: number;
  pedidos: number;
}
export interface AfiliadoItem {
  item_id: string;
  nome: string;
  categoria_id: number | null;
  vendas: number;
  comissao: number;
  pedidos: number;
}
export interface AdsTotais {
  impressoes: number;
  cliques: number;
  gasto: number;
  vendas: number;
  pedidos: number;
}
export interface AdsItem {
  item_id: string | null; // null = anúncio sem produto (loja/GMV Max)
  nome: string;
  tipo: string;
  impressoes: number;
  cliques: number;
  gasto: number;
  vendas: number;
  pedidos: number;
}
export interface VendasTotais {
  valor: number;
  pedidos: number;
}
export interface VendaItem {
  item_id: string;
  nome: string;
  valor: number;
  pedidos: number;
}

/** Uma semana coletada. Seção null = a chamada falhou (o motivo vai em avisos). */
export interface SemanaDados {
  inicio: string;
  fim: string;
  afiliados: AfiliadosTotais | null;
  afiliados_itens: AfiliadoItem[] | null;
  ads: AdsTotais | null;
  ads_itens: AdsItem[] | null;
  vendas: VendasTotais | null;
  vendas_itens: VendaItem[] | null;
  avisos: string[];
}

/** O que sobe pro DaVinci (contrato §4, versao 1). Dinheiro em R$ (float),
 *  contagens inteiras, item_id sempre string. */
export interface ColetaDados {
  versao: 1;
  coletado_em: string;
  duracao_s: number;
  chamadas: number;
  login: LoginLoja;
  login_auto_usado: boolean;
  saldo_ads: number | null;
  afiliados_ultimo_dia: string | null;
  semanas: SemanaDados[];
  avisos: string[];
}

/** Resposta num formato que não dá pra ler (campo faltando, lista que não é
 *  lista). Vira "seção null + aviso", nunca para a loja. */
export class ErroFormato extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = "ErroFormato";
  }
}

// ---------------------------------------------------------------------------
// Datas (America/Sao_Paulo = UTC-3 o ano todo desde 2019)
// ---------------------------------------------------------------------------
const DIA_S = 86_400;
const BRT_MS = 3 * 3600 * 1000;
const YMD = /^\d{4}-\d{2}-\d{2}$/;

function partes(ymd: string): [number, number, number] {
  const [y, m, d] = ymd.split("-").map(Number);
  return [y, m, d];
}

/** 00:00 BRT do dia, em segundos (o que a Shopee chama de start_time). */
export function epochInicioDia(ymd: string): number {
  const [y, m, d] = partes(ymd);
  return Date.UTC(y, m - 1, d, 3, 0, 0) / 1000;
}

/** 23:59:59 BRT do dia. Ads EXIGE esse fim: 00:00 do dia seguinte volta
 *  `code 5 end time must be 23:59:59`. */
export function epochFimDia(ymd: string): number {
  return epochInicioDia(ymd) + DIA_S - 1;
}

export function somarDias(ymd: string, n: number): string {
  const [y, m, d] = partes(ymd);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** Dia de hoje no fuso de São Paulo. */
export function hojeBRT(agora: Date = new Date()): string {
  return new Date(agora.getTime() - BRT_MS).toISOString().slice(0, 10);
}

/** 0 = domingo … 6 = sábado. */
export function diaDaSemana(ymd: string): number {
  const [y, m, d] = partes(ymd);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

/** Os dias da semana, do início ao fim (inclusive). */
export function diasDaSemana(s: Semana): string[] {
  const out: string[] = [];
  for (let d = s.inicio; d <= s.fim && out.length < 31; d = somarDias(d, 1)) out.push(d);
  return out;
}

/** "2026-10-04" → "04/10". */
export function ddmm(ymd: string): string {
  return `${ymd.slice(8, 10)}/${ymd.slice(5, 7)}`;
}

/** As 4 semanas de uma execução, do jeito do servidor (contrato §1). Só o
 *  `--teste-conferencia` usa — no dia a dia as semanas vêm prontas no job.
 *  `parcial` numa segunda (sem dia nenhum ainda) cai para `semanal`. */
export function periodoConferencia(
  tipo: "semanal" | "parcial",
  agora: Date = new Date()
): { tipo: "semanal" | "parcial"; semanas: Semana[]; afiliados_ate: string } {
  const hoje = hojeBRT(agora);
  const segunda = somarDias(hoje, -((diaDaSemana(hoje) + 6) % 7));
  let s1: Semana;
  if (tipo === "parcial" && hoje !== segunda) {
    s1 = { inicio: segunda, fim: somarDias(hoje, -1) };
  } else {
    tipo = "semanal";
    s1 = { inicio: somarDias(segunda, -7), fim: somarDias(segunda, -1) };
  }
  const semanas = [0, 1, 2, 3].map((k) => ({
    inicio: somarDias(s1.inicio, -7 * k),
    fim: somarDias(s1.fim, -7 * k),
  }));
  return { tipo, semanas, afiliados_ate: s1.fim };
}

/** ISO UTC sem milissegundos ("2026-10-06T16:41:02Z"). */
export function isoUtc(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

// ---------------------------------------------------------------------------
// Dinheiro e números
// ---------------------------------------------------------------------------
function centavos(n: number): number {
  return Math.round(n * 100) / 100;
}

/** R$ que já vem em reais (string `dis_*` ou float). null se não for número. */
export function reais(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(String(v).replace(",", "."));
  return Number.isFinite(n) ? centavos(n) : null;
}

/** Inteiro da Shopee em 1/100.000 de real (Ads, saldo) → R$. */
export function deCentMil(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? centavos(n / 100_000) : null;
}

function exigir<T>(v: T | null, campo: string): T {
  if (v === null) throw new ErroFormato(`campo ${campo} ausente`);
  return v;
}

function inteiro(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? Math.round(n) : null;
}

/** id do item como STRING (os ids da Shopee passam de 2^31; ainda cabem no
 *  double, mas o contrato pede string pra ninguém somar/arredondar). */
function idItem(v: unknown): string | null {
  if (v === null || v === undefined || v === "" || v === 0) return null;
  return String(v);
}

const MAX_NOME = 150;
function nome(v: unknown): string {
  return String(v ?? "").replace(/\s+/g, " ").trim().slice(0, MAX_NOME);
}

/** Lista da resposta: null/ausente só vale como vazia quando o total é 0. */
function lista(v: unknown, total: unknown, campo: string): any[] {
  if (Array.isArray(v)) return v;
  if ((v === null || v === undefined) && (total === undefined || total === null || Number(total) === 0)) return [];
  throw new ErroFormato(`campo ${campo} não é lista`);
}

// ---------------------------------------------------------------------------
// Leitura das respostas → seções do payload
// ---------------------------------------------------------------------------

/** `seller_daily` (body.data) → totais da semana + último dia publicado
 *  (maior `ymd` da lista; o `last_update_time` NÃO serve pra isso). */
export function afiliadosDoDaily(data: any): { totais: AfiliadosTotais; ultimoDia: string | null } {
  if (!data || typeof data !== "object") throw new ErroFormato("afiliados sem data");
  const totais: AfiliadosTotais = {
    vendas: exigir(reais(data.dis_total_actual_amount), "dis_total_actual_amount"),
    comissao: exigir(reais(data.dis_total_seller_commission), "dis_total_seller_commission"),
    pedidos: exigir(inteiro(data.total_order_count), "total_order_count"),
  };
  return { totais, ultimoDia: ultimoDiaAfiliados(data.list) };
}

export function ultimoDiaAfiliados(list: unknown): string | null {
  if (!Array.isArray(list)) return null;
  let ultimo: string | null = null;
  for (const x of list) {
    const ymd = typeof x?.ymd === "string" ? x.ymd : "";
    if (YMD.test(ymd) && (ultimo === null || ymd > ultimo)) ultimo = ymd;
  }
  return ultimo;
}

/** Uma página de `seller_item_detail` (data.list). */
export function afiliadosItens(list: unknown): AfiliadoItem[] {
  return lista(list, 0, "list").map((x: any) => ({
    item_id: exigir(idItem(x?.item_id), "item_id"),
    nome: nome(x?.item_name),
    categoria_id: inteiro(x?.category_id),
    vendas: exigir(reais(x?.dis_gmv), "dis_gmv"),
    comissao: exigir(reais(x?.dis_spend), "dis_spend"),
    pedidos: inteiro(x?.orders) ?? 0,
  }));
}

/** `get_time_graph` → data.report_aggregate. */
export function adsDoAgregado(agg: any): AdsTotais {
  if (!agg || typeof agg !== "object") throw new ErroFormato("Ads sem report_aggregate");
  return {
    impressoes: exigir(inteiro(agg.impression), "impression"),
    cliques: exigir(inteiro(agg.click), "click"),
    gasto: exigir(deCentMil(agg.cost), "cost"),
    vendas: exigir(deCentMil(agg.broad_gmv), "broad_gmv"),
    pedidos: exigir(inteiro(agg.broad_order), "broad_order"),
  };
}

/** Uma página de `homepage/query` (data.entry_list). Anúncio sem
 *  `manual_product_ads` (loja, GMV Max…) fica com item_id null. */
export function adsItens(entries: unknown): AdsItem[] {
  return lista(entries, 0, "entry_list").map((e: any) => {
    const r = e?.report;
    if (!r || typeof r !== "object") throw new ErroFormato("anúncio sem report");
    return {
      item_id: idItem(e?.manual_product_ads?.item_id),
      nome: nome(e?.title),
      tipo: String(e?.type ?? ""),
      impressoes: inteiro(r.impression) ?? 0,
      cliques: inteiro(r.click) ?? 0,
      gasto: deCentMil(r.cost) ?? 0,
      vendas: deCentMil(r.broad_gmv) ?? 0,
      pedidos: inteiro(r.broad_order) ?? 0,
    };
  });
}

/** Soma uma página de `product/performance` (result.items) no acumulado da
 *  semana, por item. */
export function acumularVendas(acc: Map<string, VendaItem>, items: unknown): void {
  for (const x of lista(items, 0, "items") as any[]) {
    const id = exigir(idItem(x?.id), "id");
    const valor = reais(x?.paid_sales) ?? 0;
    const pedidos = inteiro(x?.paid_orders) ?? 0;
    const atual = acc.get(id);
    if (atual) {
      atual.valor += valor;
      atual.pedidos += pedidos;
      if (!atual.nome) atual.nome = nome(x?.name);
    } else {
      acc.set(id, { item_id: id, nome: nome(x?.name), valor, pedidos });
    }
  }
}

/** Fecha o acumulado: itens por valor (maior primeiro) + total da semana.
 *  `pedidos` do total é a soma por item (um pedido com 2 produtos conta 2). */
export function vendasDoAcumulado(acc: Map<string, VendaItem>): { totais: VendasTotais; itens: VendaItem[] } {
  const itens = [...acc.values()]
    .map((x) => ({ ...x, valor: centavos(x.valor) }))
    .sort((a, b) => b.valor - a.valor || a.item_id.localeCompare(b.item_id));
  const valor = centavos(itens.reduce((s, x) => s + x.valor, 0));
  const pedidos = itens.reduce((s, x) => s + x.pedidos, 0);
  return { totais: { valor, pedidos }, itens };
}

/** `wallet/get` (data) → saldo de Ads disponível em R$. */
export function saldoDaCarteira(data: any): number {
  return exigir(deCentMil(data?.ads_credit?.available), "ads_credit.available");
}

// ---------------------------------------------------------------------------
// Paginação
// ---------------------------------------------------------------------------

/** Pede mais uma página? `cobertos` = linhas já cobertas (coletadas, ou
 *  offset+limit no homepage/query); para quando cobriu o total, quando a
 *  página veio vazia (proteção contra total errado) ou no teto de páginas. */
export function continuarPaginando(
  cobertos: number,
  total: number,
  paginasFeitas: number,
  maxPaginas: number,
  vieramNaUltima: number
): boolean {
  return cobertos < total && vieramNaUltima > 0 && paginasFeitas < maxPaginas;
}

// ---------------------------------------------------------------------------
// Chamadas (o que roda DENTRO da página da Central do Vendedor)
// ---------------------------------------------------------------------------
export interface Req {
  path: string; // relativo a https://seller.shopee.com.br
  method: "GET" | "POST";
  body?: unknown;
  /** caminhos a apagar da resposta ANTES de sair da página (blocos pesados
   *  que não usamos; "*" = cada item da lista). */
  podar?: string[];
  /** /api/v2/login/: só username/shopid/shop_name saem da página (a resposta
   *  tem tokens — nunca vão pro log nem pro DaVinci). */
  login?: boolean;
}

const QS_AFILIADOS = "is_real_time=0&order_type=2&channel=0";

export const REQ_LOGIN: Req = { path: "/api/v2/login/", method: "GET", login: true };

export const REQ_CARTEIRA: Req = {
  path: "/api/pas/v1/wallet/get/",
  method: "POST",
  body: { info_type_list: ["ads_credit"] },
};

export function reqSellerDaily(s: Semana): Req {
  return {
    path:
      `/api/v3/affiliateplatform/dashboard/seller_daily?start_time=${epochInicioDia(s.inicio)}` +
      `&end_time=${epochFimDia(s.fim)}&${QS_AFILIADOS}`,
    method: "GET",
  };
}

export const PAGINA_AFILIADOS = 20; // a Shopee corta em 20 mesmo pedindo mais
export const MAX_PAGINAS_AFILIADOS = 25;

export function reqItensAfiliados(s: Semana, pagina: number): Req {
  return {
    path:
      `/api/v3/affiliateplatform/dashboard/seller_item_detail?start_time=${epochInicioDia(s.inicio)}` +
      `&end_time=${epochFimDia(s.fim)}&${QS_AFILIADOS}&page_num=${pagina}&page_size=${PAGINA_AFILIADOS}&sort_rule=1`,
    method: "GET",
    podar: ["data.list.*.content_info"],
  };
}

export function reqAdsAgregado(s: Semana): Req {
  return {
    path: "/api/pas/v1/report/get_time_graph/",
    method: "POST",
    body: {
      agg_interval: 1,
      campaign_type: "product_homepage_v2",
      start_time: epochInicioDia(s.inicio),
      end_time: epochFimDia(s.fim),
      need_roi_target_setting: false,
      filter_params: { campaign_type: "new_cpc_homepage" },
    },
    // 96 blocos por dia (~900 KB na semana) — só o agregado interessa
    podar: ["data.report_by_time"],
  };
}

export const PAGINA_ADS = 50;
export const MAX_PAGINAS_ADS = 10;

export function reqAdsAnuncios(s: Semana, offset: number): Req {
  return {
    path: "/api/pas/v1/homepage/query/",
    method: "POST",
    body: {
      start_time: epochInicioDia(s.inicio),
      end_time: epochFimDia(s.fim),
      filter_list: [
        { campaign_type: "product_homepage_v3", state: "all", search_term: "", is_valid_rebate_only: false },
      ],
      offset,
      limit: PAGINA_ADS,
      use_paid_gmv: false,
    },
    podar: ["data.entry_list.*.ratio", "data.entry_list.*.trait_data", "data.entry_list.*.campaign"],
  };
}

export const PAGINA_VENDAS = 50;
export const MAX_PAGINAS_VENDAS = 10;

/** Vendas pagas por produto de UM dia (fim = 00:00 do dia seguinte). */
export function reqVendasDia(ymd: string, pagina: number): Req {
  const ini = epochInicioDia(ymd);
  return {
    path:
      `/api/mydata/v4/product/performance/?period=day&start_time=${ini}&end_time=${ini + DIA_S}` +
      `&category_type=shopee&category_id=-1&page_size=${PAGINA_VENDAS}&page_num=${pagina}` +
      `&order_type=paid&order_by=paid_sales.desc`,
    method: "GET",
    podar: ["result.items.*.models"],
  };
}

/** JS (STRING — o tsx põe __name nas funções e isso quebra no browser) que
 *  faz UMA chamada de dentro da página logada: cookies da própria sessão,
 *  SPC_CDS na URL e x-csrftoken no POST. Devolve {status, redirected, url,
 *  body|raw} ou {status: -1, erro}. Do /api/v2/login/ só saem username,
 *  shopid, shop_name e os campos de ERRO curtos (código e mensagem: é o que
 *  diz "captcha"/"login" pro classificarResposta) — token nunca. */
export function scriptChamada(req: Req, timeoutMs: number): string {
  const arg = {
    path: req.path,
    method: req.method,
    body: req.body === undefined ? null : req.body,
    podar: req.podar || [],
    login: req.login === true,
    timeout: timeoutMs,
  };
  return `(async (a) => {
  const ck = {};
  for (const par of String(document.cookie || '').split('; ')) {
    const i = par.indexOf('=');
    if (i > 0) ck[par.slice(0, i)] = par.slice(i + 1);
  }
  const url = a.path + (a.path.indexOf('?') >= 0 ? '&' : '?') +
    'SPC_CDS=' + encodeURIComponent(ck.SPC_CDS || '') + '&SPC_CDS_VER=2';
  const h = {};
  if (a.method === 'POST') { h['content-type'] = 'application/json'; h['x-csrftoken'] = ck.csrftoken || ''; }
  const podar = (o, p) => {
    if (!o || typeof o !== 'object' || !p.length) return;
    const k = p[0], resto = p.slice(1);
    if (k === '*') { if (Array.isArray(o)) for (const x of o) podar(x, resto); return; }
    if (!resto.length) { delete o[k]; return; }
    podar(o[k], resto);
  };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), a.timeout);
  try {
    const r = await fetch(url, { method: a.method, headers: h, credentials: 'include',
      body: a.body === null ? undefined : JSON.stringify(a.body), signal: ctl.signal });
    const txt = await r.text();
    let body = null;
    try { body = JSON.parse(txt); } catch (e) { body = null; }
    if (body && typeof body === 'object') {
      if (a.login) {
        const d = body.data && body.data.username ? body.data : body;
        const erro = {};
        for (const k of ['code', 'err_code', 'errcode', 'error', 'msg', 'message', 'err_msg', 'error_msg', 'errmsg']) {
          const v = body[k];
          if (typeof v === 'number' || (typeof v === 'string' && v)) erro[k] = typeof v === 'string' ? v.slice(0, 120) : v;
        }
        body = Object.assign({ username: d.username || null, shopid: d.shopid == null ? null : d.shopid,
                 shop_name: d.shop_name == null ? null : d.shop_name }, erro);
      } else {
        for (const p of a.podar) podar(body, p.split('.'));
      }
    }
    return { status: r.status, redirected: r.redirected, url: r.url, body: body,
             raw: body === null && !a.login ? txt.slice(0, 300) : null };
  } catch (e) {
    return { status: -1, erro: String((e && e.message) || e).slice(0, 200) };
  } finally {
    clearTimeout(timer);
  }
})(${JSON.stringify(arg)})`;
}

/** O que o scriptChamada devolve. */
export interface RespostaPagina {
  status: number;
  redirected?: boolean;
  url?: string;
  body?: any;
  raw?: string | null;
  erro?: string;
}

export type Classificacao =
  | { tipo: "ok" }
  | { tipo: "bloqueada" | "deslogada" | "falha"; detalhe: string };

// Mensagem de erro (JSON) que pede verificação/login.
const RX_BLOQUEIO = /captcha|verif/i;
const RX_LOGIN = /signin|sign_in|log ?in|not logged|unauthori|sess[aã]o expirada|session expired/i;
// Corpo que nem é JSON: só conta como bloqueio/login com sinal forte (HTML
// comum tem "verify"/"robots" à toa).
const RX_BLOQUEIO_HTML = /captcha|anti[-_ ]?bot|traffic[-_ ]?verif/i;
const RX_LOGIN_HTML = /account\/signin|accounts\.shopee/i;

/** Decide o que fazer com uma resposta (contrato §8): 403/429 ou captcha =
 *  `bloqueada`; login/redirecionamento pro signin = `deslogada` (as duas
 *  PARAM a loja); qualquer outra falha = só aquela seção fica null. */
export function classificarResposta(r: RespostaPagina): Classificacao {
  if (r.status === -1) return { tipo: "falha", detalhe: `sem resposta (${r.erro || "erro de rede"})` };
  if (r.status === 403 || r.status === 429) return { tipo: "bloqueada", detalhe: `a Shopee recusou (HTTP ${r.status})` };
  if (r.status === 401) return { tipo: "deslogada", detalhe: "a Shopee pediu login (HTTP 401)" };
  if (r.redirected && /\/account\/signin|accounts\.shopee|\/login/i.test(r.url || "")) {
    return { tipo: "deslogada", detalhe: "a Shopee mandou pro login" };
  }
  const body = r.body;
  if (body === null || body === undefined || typeof body !== "object") {
    const raw = String(r.raw || "");
    if (RX_BLOQUEIO_HTML.test(raw)) return { tipo: "bloqueada", detalhe: "a Shopee mostrou verificação/captcha" };
    if (RX_LOGIN_HTML.test(raw)) return { tipo: "deslogada", detalhe: "a Shopee devolveu a tela de login" };
    return { tipo: "falha", detalhe: r.status >= 400 ? `HTTP ${r.status}` : "resposta não é JSON" };
  }
  const codigo = body.code ?? body.err_code ?? body.errcode ?? body.error;
  const msg = [body.msg, body.message, body.err_msg, body.error_msg, body.errmsg]
    .filter((x) => typeof x === "string" && x)
    .join(" ")
    .slice(0, 200);
  const temErro = codigo !== undefined && codigo !== null && codigo !== 0 && codigo !== "0" && codigo !== "";
  if (temErro || r.status >= 400) {
    const texto = `${typeof codigo === "string" ? codigo : ""} ${msg}`;
    if (RX_BLOQUEIO.test(texto)) return { tipo: "bloqueada", detalhe: `a Shopee pediu verificação (${msg || codigo})` };
    if (RX_LOGIN.test(texto)) return { tipo: "deslogada", detalhe: `a Shopee pediu login (${msg || codigo})` };
    const oque = temErro ? `code ${codigo}${msg ? ` ${msg}` : ""}` : `HTTP ${r.status}`;
    return { tipo: "falha", detalhe: oque.slice(0, 200) };
  }
  return { tipo: "ok" };
}

// ---------------------------------------------------------------------------
// URLs da tela
// ---------------------------------------------------------------------------
function hostPath(raw: string): { host: string; path: string } | null {
  try {
    const u = new URL(raw);
    return { host: u.hostname.toLowerCase(), path: u.pathname.toLowerCase() };
  } catch {
    return null;
  }
}

/** A aba está DENTRO da Central do Vendedor (e não no login/verificação)?
 *  Só aí as chamadas relativas /api/... vão pro lugar certo. Olha host+path,
 *  nunca a query (o ?next=<portal> do login daria falso positivo). */
export function naCentral(url: string): boolean {
  const u = hostPath(url);
  if (!u || u.host !== "seller.shopee.com.br") return false;
  return !/\/account\/signin|\/login|\/signin|\/verify|captcha/.test(u.path);
}

/** Host da URL em minúsculas ("" se não for URL). */
export function hostDaUrl(url: string): string {
  return hostPath(url)?.host ?? "";
}

/** Hosts da Shopee onde a tela de login pode estar (o clique em "Entrar"
 *  só acontece num deles). */
export const HOSTS_LOGIN_SHOPEE = new Set(["accounts.shopee.com.br", "seller.shopee.com.br", "shopee.com.br"]);

/** A aba está numa tela da própria Shopee (https) onde dá pra clicar em
 *  "Entrar"? Qualquer outro host (portal do proxy, página de erro do SSO) →
 *  não clica: o formulário levaria o usuário e a senha do perfil. */
export function telaDeLoginDaShopee(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "https:" && HOSTS_LOGIN_SHOPEE.has(u.hostname.toLowerCase());
  } catch {
    return false;
  }
}

/** A tela pede código/OTP/captcha/verificação? (nunca resolvemos isso). */
export function urlPedeVerificacao(url: string): boolean {
  const u = hostPath(url);
  if (!u) return false;
  return /verif|captcha|challenge|2fa|security-check|(^|[/_-])otp([/_-]|$)/.test(`${u.host}${u.path}`);
}

/** Lê a tela de login (STRING pro page.evaluate). Rola o botão Entrar pra
 *  vista e devolve o centro dele + se é ELE que está no topo daquele ponto
 *  (nada cobrindo), sem clicar e sem digitar nada. */
export const SCRIPT_FORM_LOGIN = `(() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const cheio = (el) => { try { if (el.matches(':-webkit-autofill')) return true; } catch (e) {}
    return String(el.value || '').length > 0; };
  const senhas = Array.from(document.querySelectorAll('input[type="password"]')).filter(vis);
  const caixa = senhas[0] ? (senhas[0].closest('form') || document) : document;
  let usuarios = Array.from(caixa.querySelectorAll('input[name="loginKey"]')).filter(vis);
  if (!usuarios.length) usuarios = Array.from(caixa.querySelectorAll(
    'input[type="text"], input[type="email"], input[type="tel"], input:not([type])')).filter(vis);
  const texto = String((document.body && document.body.innerText) || '').slice(0, 6000);
  const pedeCodigo = /c[oó]digo de verifica|verifica[cç][aã]o|captcha|insira o c[oó]digo|n[aã]o sou um rob|verification code|\\bOTP\\b/i.test(texto);
  const entrar = (raiz) => Array.from(raiz.querySelectorAll('button, [role="button"]')).filter(vis)
    .filter((b) => /^(entrar|log ?in)$/i.test(String(b.innerText || b.textContent || '').trim()));
  let botoes = caixa !== document ? entrar(caixa) : [];
  if (!botoes.length) botoes = entrar(document);
  let botao = null;
  if (botoes.length === 1) {
    const b = botoes[0];
    b.scrollIntoView({ block: 'center', inline: 'center' });
    const r = b.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const topo = document.elementFromPoint(x, y);
    botao = { x: x, y: y, livre: !!topo && (topo === b || b.contains(topo)),
      desabilitado: !!b.disabled || b.getAttribute('aria-disabled') === 'true' || /disabled/i.test(String(b.className || '')) };
  }
  return { senha: senhas.length, senhaCheia: senhas.some(cheio), usuario: usuarios.length,
    usuarioCheio: usuarios.some(cheio), pedeCodigo: pedeCodigo, botoes: botoes.length, botao: botao };
})()`;

export interface FormLogin {
  senha: number;
  senhaCheia: boolean;
  usuario: number;
  usuarioCheio: boolean;
  pedeCodigo: boolean;
  botoes: number;
  botao: { x: number; y: number; livre: boolean; desabilitado: boolean } | null;
}

/** A página (depois do Entrar) está pedindo código/verificação? */
export const SCRIPT_PEDE_CODIGO = `(() => /c[oó]digo de verifica|verifica[cç][aã]o|captcha|insira o c[oó]digo|n[aã]o sou um rob|verification code|\\bOTP\\b/i
  .test(String((document.body && document.body.innerText) || '').slice(0, 6000)))()`;

// ---------------------------------------------------------------------------
// Ritmo e relógio
// ---------------------------------------------------------------------------

/** Pausa entre chamadas: 1,2–1,8 s, aleatória. */
export function pausaAleatoria(rnd: () => number = Math.random): number {
  return Math.round(1200 + rnd() * 600);
}

/** Salto de relógio entre dois passos além da pausa planejada — o Mac
 *  dormiu no meio da loja e o que já foi lido não é confiável. */
export const LIMITE_SALTO_MS = 120_000;
export function relogioPulou(planejadoMs: number, decorridoMs: number, limiteMs = LIMITE_SALTO_MS): boolean {
  return decorridoMs - planejadoMs > limiteMs;
}

// ---------------------------------------------------------------------------
// Payload final
// ---------------------------------------------------------------------------
export function semanaVazia(s: Semana): SemanaDados {
  return {
    inicio: s.inicio,
    fim: s.fim,
    afiliados: null,
    afiliados_itens: null,
    ads: null,
    ads_itens: null,
    vendas: null,
    vendas_itens: null,
    avisos: [],
  };
}

export function montarDados(p: {
  inicioMs: number;
  fimMs: number;
  chamadas: number;
  login: LoginLoja;
  loginAutoUsado: boolean;
  saldo: number | null;
  ultimoDia: string | null;
  semanas: SemanaDados[];
  avisos: string[];
}): ColetaDados {
  return {
    versao: 1,
    coletado_em: isoUtc(p.fimMs),
    duracao_s: Math.round((p.fimMs - p.inicioMs) / 100) / 10,
    chamadas: p.chamadas,
    login: p.login,
    login_auto_usado: p.loginAutoUsado,
    saldo_ads: p.saldo,
    afiliados_ultimo_dia: p.ultimoDia,
    semanas: p.semanas,
    avisos: p.avisos,
  };
}

/** ok = todas as seções; parcial = alguma ficou null (ver avisos); erro =
 *  nenhuma veio. */
export function statusDaColeta(d: ColetaDados): { status: "ok" | "parcial" | "erro"; erro: string | null } {
  const secoes: unknown[] = [d.saldo_ads];
  for (const s of d.semanas) {
    secoes.push(s.afiliados, s.afiliados_itens, s.ads, s.ads_itens, s.vendas, s.vendas_itens);
  }
  const nulas = secoes.filter((x) => x === null).length;
  if (nulas === 0) return { status: "ok", erro: null };
  if (nulas === secoes.length) {
    const primeiro = d.semanas.flatMap((s) => s.avisos)[0] || d.avisos[0] || "";
    return { status: "erro", erro: `nenhuma parte coletada${primeiro ? ` (${primeiro})` : ""}`.slice(0, 500) };
  }
  return { status: "parcial", erro: `${nulas} de ${secoes.length} partes sem dados — ver avisos` };
}

/** Confere o job antes de abrir qualquer perfil. null = ok. */
export function validarJob(job: {
  adspower_user_id?: unknown;
  semanas?: unknown;
  afiliados_ate?: unknown;
  esperar_afiliados_ate?: unknown;
  corte?: unknown;
}): string | null {
  if (!job.adspower_user_id || typeof job.adspower_user_id !== "string") return "job sem adspower_user_id";
  const sem = job.semanas as Semana[] | undefined;
  if (!Array.isArray(sem) || sem.length !== 4) return "job sem as 4 semanas";
  for (const s of sem) {
    if (!s || !YMD.test(String(s.inicio)) || !YMD.test(String(s.fim)) || s.inicio > s.fim) {
      return `semana inválida no job: ${JSON.stringify(s)}`;
    }
  }
  if (!YMD.test(String(job.afiliados_ate))) return "job sem afiliados_ate";
  if (!Number.isFinite(Date.parse(String(job.esperar_afiliados_ate)))) return "job sem esperar_afiliados_ate";
  if (!Number.isFinite(Date.parse(String(job.corte)))) return "job sem corte";
  return null;
}

// ---------------------------------------------------------------------------
// Resumo pro --teste-conferencia (texto, sem tokens)
// ---------------------------------------------------------------------------
function brl(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `R$ ${n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString("pt-BR");
}

export function resumoConferencia(status: string, erro: string | null, d: ColetaDados | null): string {
  const out: string[] = [`status: ${status}${erro ? ` — ${erro}` : ""}`];
  if (!d) return out.join("\n");
  out.push(`login: ${d.login.username} (shopid ${d.login.shopid ?? "?"}) · Entrar clicado: ${d.login_auto_usado ? "sim" : "não"}`);
  out.push(`${d.chamadas} chamadas em ${d.duracao_s} s · saldo Ads ${brl(d.saldo_ads)} · afiliados até ${d.afiliados_ultimo_dia ? ddmm(d.afiliados_ultimo_dia) : "—"}`);
  d.semanas.forEach((s, i) => {
    out.push(`S${i + 1} ${ddmm(s.inicio)}–${ddmm(s.fim)}`);
    out.push(
      `  afiliados: ${s.afiliados ? `${brl(s.afiliados.vendas)} · comissão ${brl(s.afiliados.comissao)} · ${num(s.afiliados.pedidos)} ped.` : "—"}` +
        ` (${s.afiliados_itens ? s.afiliados_itens.length : "—"} itens)`
    );
    out.push(
      `  Ads: ${s.ads ? `${brl(s.ads.vendas)} · gasto ${brl(s.ads.gasto)} · ${num(s.ads.impressoes)} impr. · ${num(s.ads.pedidos)} ped.` : "—"}` +
        ` (${s.ads_itens ? s.ads_itens.length : "—"} anúncios)`
    );
    out.push(
      `  vendas: ${s.vendas ? `${brl(s.vendas.valor)} · ${num(s.vendas.pedidos)} ped.` : "—"}` +
        ` (${s.vendas_itens ? s.vendas_itens.length : "—"} itens)`
    );
    for (const a of s.avisos) out.push(`  aviso: ${a}`);
  });
  for (const a of d.avisos) out.push(`aviso: ${a}`);
  return out.join("\n");
}
