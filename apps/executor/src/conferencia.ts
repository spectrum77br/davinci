/**
 * Conferência Shopee — a coleta de UMA loja (contrato §8).
 *
 * Relatório de terça/quinta (docs/conferencia-shopee.md): o DaVinci cria uma
 * coleta por loja e este executor pede uma por vez. Pra cada loja:
 *
 *   1. passou do corte → "erro" sem abrir nada;
 *   2. perfil aberto (alguém usando) → espera 7 s ×3; continua aberto →
 *      "perfil_em_uso" e NÃO mexe nele (nunca fecha perfil que não abriu);
 *   3. caffeinate enquanto coleta (o Mac dormindo no meio estraga a loja);
 *   4. abre o perfil, aba NOVA (não sequestra as abas da pessoa), Central do
 *      Vendedor, confere o login (403/429/captcha já aqui → "bloqueada", sem
 *      recarregar nem chamar de novo). Deslogada com usuário e senha JÁ
 *      preenchidos pelo perfil, numa tela de login da PRÓPRIA Shopee → UM
 *      clique em "Entrar" (nunca digita nada); pediu código ou captcha →
 *      "deslogada";
 *   5. afiliados do último dia ainda não publicados e antes do limite →
 *      "aguardando_afiliados" (o servidor devolve pra fila em 10 min). Lista
 *      de S1 sem NENHUM dia = loja sem venda de afiliado na semana: não há o
 *      que esperar (`afiliados_ultimo_dia` null, contrato §4);
 *   6. 4 semanas × (afiliados, itens de afiliados, Ads, anúncios, vendas por
 *      dia) + saldo de Ads, uma chamada por vez com 1,2–1,8 s de pausa, de
 *      dentro da página logada (fetch com os cookies da própria sessão);
 *   7. fecha a nossa aba, desconecta e fecha o perfil (fomos nós que abrimos).
 *
 * Só LÊ: nenhuma chamada muda nada na Shopee. Tokens da sessão nunca saem da
 * página (do /api/v2/login/ só voltam username, shopid e shop_name).
 * Todo JS de página vai como STRING (o tsx põe __name nas funções).
 */
import { spawn, type ChildProcess } from "node:child_process";
import puppeteer, { type Browser, type Page } from "puppeteer-core";
import { cfg } from "./config";
import * as adspower from "./adspower";
import { log } from "./log";
import * as u from "./conferencia_util";

export type {
  ColetaDados,
  SemanaDados,
  Semana,
  AfiliadosTotais,
  AfiliadoItem,
  AdsTotais,
  AdsItem,
  VendasTotais,
  VendaItem,
  LoginLoja,
} from "./conferencia_util";

/** Uma coleta entregue por POST /api/marketing/conferencia-shopee/agent/lease
 *  (contrato §3). Datas-limite em ISO UTC. */
export interface JobConferencia {
  coleta_id: string;
  execucao_id: string;
  conta: string;
  adspower_user_id: string;
  grupo: string; // "mala" | "celular"
  semanas: u.Semana[]; // 4, índice 0 = S1 (a mais recente)
  afiliados_ate: string; // YYYY-MM-DD: último dia de afiliados que precisa estar publicado
  esperar_afiliados_ate: string;
  corte: string;
  login_auto: boolean;
  tentativa: number;
}

/** Finais: ok, parcial, deslogada, sem_automacao, bloqueada, interrompida,
 *  erro. Voltam pra fila (o servidor reagenda): perfil_em_uso,
 *  aguardando_afiliados. */
export type StatusConferencia =
  | "ok"
  | "parcial"
  | "deslogada"
  | "perfil_em_uso"
  | "aguardando_afiliados"
  | "sem_automacao"
  | "bloqueada"
  | "interrompida"
  | "erro";

/** Corpo de POST /agent/coletas/{id}/resultado. */
export interface ResultadoConferencia {
  status: StatusConferencia;
  erro: string | null;
  dados: u.ColetaDados | null;
}

const SELLER = "https://seller.shopee.com.br";
const TIMEOUT_FETCH_MS = 45_000; // dentro da página
const TIMEOUT_CHAMADA_MS = 60_000; // do lado do Node (page.evaluate travado)
const FALHAS_SEGUIDAS_MAX = 5;

/** As esperas passam por aqui só pra o teste (test/conferencia_coleta.test.ts)
 *  rodar sem as pausas de verdade. */
export const _ritmo = { sleep: (ms: number) => new Promise<void>((r) => setTimeout(r, ms)) };
const sleep = (ms: number) => _ritmo.sleep(ms);

/** Para a loja inteira (bloqueio, login, Mac dormiu, login que não deu
 *  pra conferir). */
class Parada extends Error {
  constructor(
    readonly status: "bloqueada" | "deslogada" | "interrompida" | "erro",
    msg: string
  ) {
    super(msg);
    this.name = "Parada";
  }
}

/** Uma chamada que falhou sem ser bloqueio: só a seção fica null. */
class FalhaSecao extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = "FalhaSecao";
  }
}

/** As chamadas de UMA loja: ritmo, contagem, prazo e relógio. */
class Sessao {
  chamadas = 0;
  private fimUltima = Date.now();
  private falhasSeguidas = 0;
  private desistiu = false;

  constructor(private readonly page: Page) {}

  /** Reinicia a régua do relógio depois de um passo que não é chamada
   *  (navegação, login) — esses têm prazo próprio. */
  marcar(): void {
    this.fimUltima = Date.now();
  }

  /** Faz a chamada e devolve a resposta crua (sem classificar). */
  async bruto(req: u.Req, exigirCentral = true): Promise<u.RespostaPagina> {
    const planejado = this.chamadas > 0 ? u.pausaAleatoria() : 0;
    if (planejado) await sleep(planejado);
    const gap = Date.now() - this.fimUltima;
    if (u.relogioPulou(planejado, gap)) {
      throw new Parada("interrompida", `o relógio pulou ${Math.round(gap / 60_000)} min entre dois passos (o Mac dormiu?)`);
    }
    if (exigirCentral && !u.naCentral(this.page.url())) {
      const url = this.page.url();
      if (u.urlPedeVerificacao(url)) throw new Parada("bloqueada", "a Shopee abriu uma tela de verificação no meio da coleta");
      throw new Parada("deslogada", "a página saiu da Central do Vendedor (caiu no login?)");
    }
    const t0 = Date.now();
    let timer: NodeJS.Timeout | undefined;
    let r: u.RespostaPagina;
    try {
      r = (await Promise.race([
        this.page.evaluate(u.scriptChamada(req, TIMEOUT_FETCH_MS)) as Promise<u.RespostaPagina>,
        new Promise<u.RespostaPagina>((res) => {
          timer = setTimeout(() => res({ status: -1, erro: `sem resposta em ${TIMEOUT_CHAMADA_MS / 1000} s` }), TIMEOUT_CHAMADA_MS);
        }),
      ])) || { status: -1, erro: "página não devolveu nada" };
    } catch (e: any) {
      // ex.: "Execution context was destroyed" (a página navegou no meio)
      r = { status: -1, erro: String(e?.message || e).slice(0, 200) };
    } finally {
      if (timer) clearTimeout(timer);
    }
    this.chamadas++;
    const dur = Date.now() - t0;
    this.fimUltima = Date.now();
    // A chamada tem prazo de 60 s; passar de 2 min só com o Mac dormindo.
    if (u.relogioPulou(0, dur)) {
      throw new Parada("interrompida", `uma chamada levou ${Math.round(dur / 60_000)} min (o Mac dormiu?)`);
    }
    return r;
  }

  /** Chamada classificada: devolve o corpo; bloqueio/login PARAM a loja;
   *  outra falha vira FalhaSecao (a seção fica null). Depois de 5 falhas
   *  seguidas não chama mais nada (o resto da loja fica null). */
  async json(req: u.Req): Promise<any> {
    if (this.desistiu) throw new FalhaSecao(`não coletado (${FALHAS_SEGUIDAS_MAX} falhas seguidas)`);
    const r = await this.bruto(req);
    const c = u.classificarResposta(r);
    if (c.tipo === "ok") {
      this.falhasSeguidas = 0;
      return r.body;
    }
    if (c.tipo === "bloqueada" || c.tipo === "deslogada") throw new Parada(c.tipo, c.detalhe);
    if (++this.falhasSeguidas >= FALHAS_SEGUIDAS_MAX) this.desistiu = true;
    throw new FalhaSecao(c.detalhe);
  }
}

/** Roda uma seção; falha comum vira aviso + null, Parada sobe. */
async function secao<T>(avisos: string[], rotulo: string, fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch (e: any) {
    if (e instanceof Parada) throw e;
    avisos.push(`${rotulo}: ${String(e?.message || e).slice(0, 200)}`);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Tela: Central do Vendedor e login
// ---------------------------------------------------------------------------

/** Abre a Central e espera a URL assentar: logada, o portal faz um "bounce"
 *  de SSO (portal → contas → portal); decidir no meio daria falso deslogada. */
async function abrirCentral(page: Page): Promise<void> {
  await page.goto(`${SELLER}/`, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => undefined);
  await sleep(4000);
  await assentar(page);
}

/** Espera a URL ficar parada dentro da Central (até ~15 s). */
async function assentar(page: Page): Promise<void> {
  let anterior = page.url();
  for (let i = 0; i < 15; i++) {
    await sleep(1000);
    const agora = page.url();
    if (agora === anterior && u.naCentral(agora)) break;
    anterior = agora;
  }
}

/** O que o /api/v2/login/ disse: logada (`login`), deslogada (`login`
 *  null) ou não deu pra saber (`falha`: rede, HTTP 5xx, resposta torta). */
type LeituraLogin = { login: u.LoginLoja; falha?: undefined } | { login: null; falha?: string };

/** Lê o login pela primeira chamada da loja. 403/429/captcha → Parada
 *  ("bloqueada") NA HORA: recarregar a Central e chamar de novo logo depois
 *  de a Shopee limitar só piora. 401/pedido de login/sem username →
 *  deslogada. */
async function lerLogin(page: Page, s: Sessao): Promise<LeituraLogin> {
  // fora da Central (tela de login) a chamada relativa iria pra outro host
  if (!u.naCentral(page.url())) return { login: null };
  const r = await s.bruto(u.REQ_LOGIN, false);
  const c = u.classificarResposta(r);
  if (c.tipo === "bloqueada") throw new Parada("bloqueada", c.detalhe);
  const b = r.body;
  if (r.status === 200 && b && typeof b.username === "string" && b.username) {
    const shopid = Number(b.shopid);
    return {
      login: {
        username: b.username,
        shopid: Number.isFinite(shopid) && shopid > 0 ? shopid : null,
        shop_name: typeof b.shop_name === "string" ? b.shop_name : null,
      },
    };
  }
  if (c.tipo === "falha") return { login: null, falha: c.detalhe };
  return { login: null }; // 401, pedido de login, ou 200 sem username
}

const MSG_VERIFICACAO = "a Shopee pediu código/verificação no login — entre manualmente pelo perfil do AdsPower";

/** O clique em "Entrar" manda o usuário e a senha que o perfil preencheu:
 *  só numa tela de login da própria Shopee (proxy com portal cativo, erro de
 *  SSO ou qualquer site com formulário levaria a senha embora). */
function exigirLoginDaShopee(url: string): void {
  if (u.telaDeLoginDaShopee(url)) return;
  const host = u.hostDaUrl(url) || url.slice(0, 80);
  throw new Parada("deslogada", `a tela de login não é da Shopee (${host}) — entre manualmente pelo perfil`);
}

/** Loja deslogada: se a tela de login já tem usuário E senha preenchidos
 *  pelo perfil, dá UM clique de mouse no "Entrar" (só se for ele que está no
 *  topo daquele ponto). Nunca digita nada. Qualquer coisa fora disso →
 *  Parada("deslogada") com o motivo. */
async function entrarComPerfil(page: Page, conta: string): Promise<void> {
  if (u.urlPedeVerificacao(page.url())) throw new Parada("deslogada", MSG_VERIFICACAO);
  exigirLoginDaShopee(page.url());
  const f = (await page.evaluate(u.SCRIPT_FORM_LOGIN).catch(() => null)) as u.FormLogin | null;
  if (!f || !f.senha || !f.usuario) {
    throw new Parada("deslogada", "loja deslogada e a tela de login não foi reconhecida — entre manualmente pelo perfil");
  }
  if (f.pedeCodigo) throw new Parada("deslogada", MSG_VERIFICACAO);
  if (!f.usuarioCheio || !f.senhaCheia) {
    throw new Parada("deslogada", "loja deslogada e o perfil não tem usuário e senha preenchidos — entre manualmente");
  }
  if (!f.botao) {
    throw new Parada("deslogada", `loja deslogada; não achei UM botão "Entrar" (achei ${f.botoes}) — entre manualmente`);
  }
  if (!f.botao.livre) throw new Parada("deslogada", 'loja deslogada; o botão "Entrar" está coberto por outra coisa na tela');
  if (f.botao.desabilitado) throw new Parada("deslogada", 'loja deslogada; o botão "Entrar" está desabilitado');

  // A página pode ter navegado enquanto lia o formulário: confere de novo.
  exigirLoginDaShopee(page.url());
  log.info(`conferência ${conta}: deslogada com usuário e senha preenchidos — um clique em Entrar`);
  await page.mouse.click(f.botao.x, f.botao.y);

  // até 30 s pra sair do login (navegação ou SPA)
  const limite = Date.now() + 30_000;
  while (Date.now() < limite) {
    await sleep(1000);
    const url = page.url();
    if (u.urlPedeVerificacao(url)) throw new Parada("deslogada", MSG_VERIFICACAO);
    if (u.naCentral(url)) break;
  }
  if (u.naCentral(page.url())) {
    await assentar(page);
  } else {
    const pede = (await page.evaluate(u.SCRIPT_PEDE_CODIGO).catch(() => false)) as boolean;
    if (pede) throw new Parada("deslogada", MSG_VERIFICACAO);
    await abrirCentral(page);
  }
  if (u.urlPedeVerificacao(page.url())) throw new Parada("deslogada", MSG_VERIFICACAO);
}

async function garantirLogin(
  job: JobConferencia,
  page: Page,
  s: Sessao
): Promise<{ login: u.LoginLoja; autoUsado: boolean }> {
  let l = await lerLogin(page, s);
  if (!l.login) {
    // uma recarga antes de concluir (o bounce do SSO às vezes demora; ou a
    // rede piscou na primeira chamada)
    await abrirCentral(page);
    s.marcar();
    l = await lerLogin(page, s);
  }
  if (l.login) return { login: l.login, autoUsado: false };
  // Duas vezes sem resposta que diga quem está logado: não é "deslogada"
  // (nem motivo pra clicar em Entrar numa Central aberta).
  if (l.falha) throw new Parada("erro", `não deu pra conferir o login da loja: ${l.falha}`);
  if (!job.login_auto || !cfg.conferenciaLoginAuto) {
    const porque = job.login_auto ? " (login automático desligado neste Mac: CONFERENCIA_LOGIN_AUTO)" : "";
    throw new Parada("deslogada", `loja deslogada — entre na Shopee pelo perfil do AdsPower${porque}`);
  }
  await entrarComPerfil(page, job.conta);
  s.marcar();
  l = await lerLogin(page, s);
  if (!l.login) {
    const porque = l.falha ? ` (${l.falha})` : "";
    throw new Parada("deslogada", `cliquei em Entrar uma vez e a loja continuou deslogada${porque} — entre manualmente`);
  }
  return { login: l.login, autoUsado: true };
}

// ---------------------------------------------------------------------------
// Seções de uma semana
// ---------------------------------------------------------------------------

async function itensAfiliados(s: Sessao, sem: u.Semana, avisos: string[]): Promise<u.AfiliadoItem[]> {
  const itens: u.AfiliadoItem[] = [];
  let total = 0;
  for (let pagina = 1; ; pagina++) {
    const body = await s.json(u.reqItensAfiliados(sem, pagina));
    const lista = body?.data?.list;
    const novos = u.afiliadosItens(lista);
    itens.push(...novos);
    total = Number(body?.data?.total_count) || 0;
    if (!u.continuarPaginando(itens.length, total, pagina, u.MAX_PAGINAS_AFILIADOS, novos.length)) break;
  }
  if (itens.length < total) avisos.push(`itens de afiliados: só ${itens.length} de ${total}`);
  return itens;
}

async function anunciosAds(s: Sessao, sem: u.Semana, avisos: string[]): Promise<u.AdsItem[]> {
  const itens: u.AdsItem[] = [];
  let total = 0;
  for (let pagina = 1, offset = 0; ; pagina++, offset += u.PAGINA_ADS) {
    const body = await s.json(u.reqAdsAnuncios(sem, offset));
    const novos = u.adsItens(body?.data?.entry_list);
    itens.push(...novos);
    total = Number(body?.data?.total) || 0;
    if (!u.continuarPaginando(offset + u.PAGINA_ADS, total, pagina, u.MAX_PAGINAS_ADS, novos.length)) break;
  }
  if (itens.length < total) avisos.push(`anúncios de Ads: só ${itens.length} de ${total}`);
  return itens;
}

/** Vendas pagas por produto, um dia de cada vez, somadas na semana. Um dia
 *  que falha derruba a semana inteira (soma pela metade engana). */
async function vendasDaSemana(
  s: Sessao,
  sem: u.Semana,
  avisos: string[]
): Promise<{ totais: u.VendasTotais; itens: u.VendaItem[] }> {
  const acc = new Map<string, u.VendaItem>();
  for (const dia of u.diasDaSemana(sem)) {
    let vistos = 0;
    let total = 0;
    for (let pagina = 1; ; pagina++) {
      const body = await s.json(u.reqVendasDia(dia, pagina));
      const result = body?.result;
      if (!result || typeof result !== "object") throw new u.ErroFormato(`vendas de ${u.ddmm(dia)} sem result`);
      const items = Array.isArray(result.items) ? result.items : [];
      u.acumularVendas(acc, items);
      vistos += items.length;
      total = Number(result.total) || 0;
      if (!u.continuarPaginando(vistos, total, pagina, u.MAX_PAGINAS_VENDAS, items.length)) break;
    }
    if (vistos < total) avisos.push(`vendas de ${u.ddmm(dia)}: só ${vistos} de ${total} produtos`);
  }
  return u.vendasDoAcumulado(acc);
}

// ---------------------------------------------------------------------------
// A loja inteira (perfil já aberto, aba nova)
// ---------------------------------------------------------------------------

/** A coleta com o perfil já aberto numa aba nossa; bloqueio/login/Mac
 *  dormindo viram o status da loja (sem dados). Exportada pro teste (página
 *  falsa); quem usa de verdade é o coletarConferencia. */
export async function coletarNaPagina(job: JobConferencia, page: Page): Promise<ResultadoConferencia> {
  try {
    return await coletarLoja(job, page);
  } catch (e) {
    if (e instanceof Parada) return { status: e.status, erro: e.message, dados: null };
    throw e;
  }
}

async function coletarLoja(job: JobConferencia, page: Page): Promise<ResultadoConferencia> {
  const inicioMs = Date.now();
  const s = new Sessao(page);
  await abrirCentral(page);
  s.marcar();
  const { login, autoUsado } = await garantirLogin(job, page, s);
  log.info(`conferência ${job.conta}: logada como ${login.username}${autoUsado ? " (Entrar clicado)" : ""}`);

  const avisos: string[] = [];
  const semanas = job.semanas.map(u.semanaVazia);

  // S1 primeiro: diz se os afiliados do último dia já saíram. Se a chamada
  // falhar não dá pra saber — segue sem esperar (a seção fica null). Lista
  // sem NENHUM dia (ultimoDia null) = sem venda de afiliado na semana: não
  // há o que esperar, e o servidor também não lista como incompleta
  // (contrato §4). Esperar até as 15:00 toda semana a loja sem afiliados
  // não traria dia nenhum.
  const dailyS1 = await secao(semanas[0].avisos, "afiliados", async () =>
    u.afiliadosDoDaily((await s.json(u.reqSellerDaily(job.semanas[0])))?.data)
  );
  semanas[0].afiliados = dailyS1 ? dailyS1.totais : null;
  const ultimoDia = dailyS1 ? dailyS1.ultimoDia : null;
  if (ultimoDia !== null && ultimoDia < job.afiliados_ate) {
    const ate = u.ddmm(ultimoDia);
    if (Date.now() < Date.parse(job.esperar_afiliados_ate)) {
      return {
        status: "aguardando_afiliados",
        erro: `afiliados publicados só até ${ate}; falta ${u.ddmm(job.afiliados_ate)}`,
        dados: null,
      };
    }
    avisos.push(`afiliados só até ${ate}`);
  }

  for (let w = 0; w < job.semanas.length; w++) {
    const sem = job.semanas[w];
    const sd = semanas[w];
    if (w > 0) {
      sd.afiliados = await secao(sd.avisos, "afiliados", async () =>
        u.afiliadosDoDaily((await s.json(u.reqSellerDaily(sem)))?.data).totais
      );
    }
    sd.afiliados_itens = await secao(sd.avisos, "itens de afiliados", () => itensAfiliados(s, sem, sd.avisos));
    sd.ads = await secao(sd.avisos, "Ads", async () =>
      u.adsDoAgregado((await s.json(u.reqAdsAgregado(sem)))?.data?.report_aggregate)
    );
    sd.ads_itens = await secao(sd.avisos, "anúncios de Ads", () => anunciosAds(s, sem, sd.avisos));
    const vendas = await secao(sd.avisos, "vendas", () => vendasDaSemana(s, sem, sd.avisos));
    sd.vendas = vendas ? vendas.totais : null;
    sd.vendas_itens = vendas ? vendas.itens : null;
  }

  const saldo = await secao(avisos, "saldo de Ads", async () =>
    u.saldoDaCarteira((await s.json(u.REQ_CARTEIRA))?.data)
  );

  const dados = u.montarDados({
    inicioMs,
    fimMs: Date.now(),
    chamadas: s.chamadas,
    login,
    loginAutoUsado: autoUsado,
    saldo,
    ultimoDia,
    semanas,
    avisos,
  });
  const { status, erro } = u.statusDaColeta(dados);
  return { status, erro, dados };
}

/** caffeinate -i -w <pid>: o Mac não dorme por inatividade enquanto a loja
 *  é coletada (e o caffeinate morre sozinho se o executor morrer). */
function manterAcordado(): ChildProcess | null {
  if (process.platform !== "darwin") return null;
  try {
    const p = spawn("caffeinate", ["-i", "-w", String(process.pid)], { stdio: "ignore" });
    p.on("error", (e) => log.warn(`caffeinate não rodou: ${e.message}`));
    return p;
  } catch (e: any) {
    log.warn(`caffeinate não rodou: ${String(e?.message || e)}`);
    return null;
  }
}

/** Erro do AdsPower de um instante (limite de ~1 req/s dividido com outros
 *  programas do Mac): tenta de novo antes de desistir. */
async function comRetentativa<T>(fn: () => Promise<T>, vezes = 3): Promise<T> {
  for (let i = 1; ; i++) {
    try {
      return await fn();
    } catch (e) {
      if (i >= vezes) throw e;
      await sleep(2000 * i);
    }
  }
}

/** puppeteer.connect com uma segunda chance (o Chrome do perfil às vezes
 *  ainda está subindo). */
async function conectar(ws: string): Promise<Browser> {
  try {
    return await puppeteer.connect({ browserWSEndpoint: ws, defaultViewport: null });
  } catch {
    await sleep(3000);
    return await puppeteer.connect({ browserWSEndpoint: ws, defaultViewport: null });
  }
}

/** Coleta UMA loja e devolve o corpo do resultado. Não lança: qualquer
 *  imprevisto vira status "erro". */
export async function coletarConferencia(job: JobConferencia): Promise<ResultadoConferencia> {
  const invalido = u.validarJob(job);
  if (invalido) return { status: "erro", erro: invalido, dados: null };
  if (Date.now() > Date.parse(job.corte)) return { status: "erro", erro: "fora do horário (corte)", dados: null };

  const uid = job.adspower_user_id;
  const cafe = manterAcordado();
  let abrimos = false;
  let browser: Browser | null = null;
  let page: Page | null = null;
  try {
    // Aberto = alguém usando. O AdsPower segue dizendo "Active" alguns
    // segundos depois de um stop, por isso a espera antes de concluir.
    let emUso: boolean;
    try {
      emUso = await comRetentativa(() => adspower.active(uid));
      for (let i = 0; emUso && i < 3; i++) {
        await sleep(7000);
        emUso = await comRetentativa(() => adspower.active(uid));
      }
    } catch (e: any) {
      return { status: "erro", erro: `AdsPower não respondeu: ${String(e?.message || e).slice(0, 200)}`, dados: null };
    }
    if (emUso) {
      return { status: "perfil_em_uso", erro: "perfil aberto por outra pessoa — tento de novo depois", dados: null };
    }

    let ws: string;
    try {
      ws = await adspower.start(uid);
      abrimos = true;
    } catch (e: any) {
      const msg = String(e?.message || e).slice(0, 200);
      // O AdsPower abriu, mas sem endpoint do puppeteer: núcleo que não é
      // Chromium (Firefox). O perfil abriu por nossa causa → fecha no finally.
      if (/ws\.puppeteer/.test(msg)) {
        abrimos = true;
        return { status: "sem_automacao", erro: `o perfil não aceita automação (núcleo Firefox?): ${msg}`, dados: null };
      }
      if (/another (device|computer|user)|other device|being used|in use|em uso|outro (dispositivo|computador|usu)/i.test(msg)) {
        return { status: "perfil_em_uso", erro: `perfil em uso noutra máquina: ${msg}`, dados: null };
      }
      return { status: "erro", erro: `AdsPower não abriu o perfil: ${msg}`, dados: null };
    }

    try {
      browser = await conectar(ws);
    } catch (e: any) {
      return {
        status: "sem_automacao",
        erro: `o perfil não aceita automação (núcleo Firefox?): ${String(e?.message || e).slice(0, 200)}`,
        dados: null,
      };
    }
    const versao = await browser.version().catch(() => "");
    if (/firefox/i.test(versao)) {
      return { status: "sem_automacao", erro: `o perfil usa ${versao} (só Chromium é automatizado)`, dados: null };
    }
    page = await browser.newPage();
    return await coletarNaPagina(job, page);
  } catch (e: any) {
    if (e instanceof Parada) return { status: e.status, erro: e.message, dados: null };
    return { status: "erro", erro: String(e?.message || e).slice(0, 500), dados: null };
  } finally {
    if (page) await page.close().catch(() => undefined);
    if (browser) await browser.disconnect().catch(() => undefined);
    // NUNCA browser.close(): quem fecha o perfil é o AdsPower — e só o que
    // NÓS abrimos.
    if (abrimos) {
      try {
        await adspower.stop(uid);
        for (let i = 0; i < 10 && (await adspower.active(uid).catch(() => false)); i++) await sleep(2000);
      } catch (e) {
        log.error(`falha ao fechar profile ${uid}: ${String(e)}`);
      }
    }
    if (cafe) cafe.kill();
  }
}
