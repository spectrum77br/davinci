/**
 * Conciliação › Temu lida da TELA (17/11/2026, piloto na loja Eron). Comando
 * `temu_extrato` da fila da Logística. A Temu (Brasil) não tem API de extrato,
 * repasse nem saldo: o robô abre o perfil da loja no AdsPower, vai em Finanças ›
 * Pagamentos (https://br.seller.temu.com/payment.html) e passa pelas abas como uma
 * pessoa — Visão geral, Extratos, Pedidos não processados, Pagamentos —, página por
 * página, guardando o que a PRÓPRIA PÁGINA carrega. Não chama a Temu por fora e não
 * clica em nada além das abas e da seta de próxima página. Só leitura.
 *
 * O que a página carrega (POST, JSON):
 *   /mms/statement/amount/detail           saldo (reserva + fundos disponíveis)
 *   /mms/statement/order/list              extrato, 10 linhas por página (page a partir de 1)
 *   /mms/settle/query/amount/selfproduce   previsto por pedido, 10 por página (page a partir de 0)
 *   /mms/payment/disbursement/list         saques
 *
 * Fecha o perfil e SÓ ENTÃO envia tudo em POST /api/conciliacao/temu/leitura
 * (X-Agent-Token), com até 3 tentativas se o DaVinci estiver ocupado. O DaVinci recusa
 * extrato incompleto (a tela diz quantas linhas tem), então página que não chegou =
 * falha explicada, nunca leitura pela metade. Se o saldo da tela mudar entre o começo e
 * o fim da leitura, ela é descartada (as linhas e o saldo não seriam do mesmo instante).
 *
 * Sessão vencida cai em login.html → `needs_manual_login` (ninguém está olhando a
 * tela do mini): alguém entra no perfil uma vez e a próxima rodada passa. Perfil que
 * já está aberto no AdsPower por uma pessoa ou outro robô não é tocado nem fechado; o
 * que ESTE robô deixou aberto (caiu no meio, o AdsPower não respondeu ao fechar) tem
 * uma marca em disco e é fechado na rodada seguinte — sem ela, uma queda travava a
 * loja pra sempre em "perfil em uso".
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import puppeteer, { type Browser, type HTTPResponse, type Page } from "puppeteer-core";

import { cfg } from "./config";
import { log } from "./log";
import * as adspower from "./adspower";
import {
  chaveDaResposta,
  leuTudo,
  linhasDaPagina,
  montarLeitura,
  totalDoSaldo,
  totalInformado,
  type Corpo,
  type Nome,
} from "./temu_extrato_util";

export interface PedidoTemu {
  loja: string;
}

export interface ResultadoTemu {
  ok: boolean;
  reason?: string;
  linhas?: number;
  previstos?: number;
  saques?: number;
  davinci?: Record<string, unknown>;
}

export const URL_PAGAMENTOS = "https://br.seller.temu.com/payment.html";
const HOST = "br.seller.temu.com";
const CAMINHOS: Record<string, Nome> = {
  "/mms/statement/amount/detail": "saldo",
  "/mms/statement/order/list": "extrato",
  "/mms/settle/query/amount/selfproduce": "previsto",
  "/mms/payment/disbursement/list": "saques",
};
const POR_PAGINA = 10;
/** 60 páginas = 600 linhas em 30 dias; acima disso a leitura falha explicada. */
const TETO_PAGINAS = 60;
const TETO_PAGINAS_SAQUES = 5;
const MARCAS = path.join(os.tmpdir(), "davinci-temu-perfis");

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
/** Pausa de pessoa entre um clique e outro. */
const pausa = () => sleep(1200 + Math.floor(Math.random() * 1300));

// ---------- o perfil que este robô abriu ----------
const arquivoDaMarca = (perfil: string) => path.join(MARCAS, perfil.replace(/[^\w.-]/g, "_"));

function marcar(perfil: string): void {
  try {
    fs.mkdirSync(MARCAS, { recursive: true });
    fs.writeFileSync(arquivoDaMarca(perfil), new Date().toISOString());
  } catch (e) {
    log.warn(`não consegui gravar a marca do perfil ${perfil}: ${String(e)}`);
  }
}

function temMarca(perfil: string): boolean {
  return fs.existsSync(arquivoDaMarca(perfil));
}

/** Fecha o perfil (3 tentativas) e tira a marca. Devolve se ele ficou fechado. */
async function fecharPerfil(perfil: string): Promise<boolean> {
  for (let i = 0; i < 3; i++) {
    try {
      await adspower.stop(perfil);
    } catch (e) {
      log.warn(`fechar o perfil ${perfil} (tentativa ${i + 1}): ${String(e)}`);
    }
    await sleep(2000);
    try {
      if (!(await adspower.active(perfil))) {
        fs.rmSync(arquivoDaMarca(perfil), { force: true });
        return true;
      }
    } catch {
      /* AdsPower sem resposta: tenta de novo */
    }
  }
  log.error(`o perfil ${perfil} ficou aberto no AdsPower; a próxima rodada fecha (marca em ${MARCAS})`);
  return false;
}

// ---------- o que a página carrega ----------
/** Guarda as respostas das chamadas que interessam, do jeito que a página as fez. */
class Coletor {
  readonly vistas = new Map<string, Corpo>();
  /** O primeiro e o último saldo que a tela carregou (cada aba recarrega). */
  saldoPrimeiro: Corpo | null = null;
  saldoUltimo: Corpo | null = null;
  /** A última recusa da Temu numa dessas chamadas (sessão vencida sem ir pro login). */
  ultimoErro: string | null = null;

  ouvir(page: Page): void {
    page.on("response", (resp: HTTPResponse) => {
      void this.guardar(resp);
    });
  }

  private async guardar(resp: HTTPResponse): Promise<void> {
    try {
      const u = new URL(resp.url());
      if (u.hostname !== HOST) return;
      const nome = CAMINHOS[u.pathname];
      if (!nome) return;
      if (resp.status() !== 200) {
        this.ultimoErro = `${nome}: HTTP ${resp.status()}`;
        return;
      }
      const envio = JSON.parse(resp.request().postData() || "{}") as Corpo;
      const corpo = (await resp.json()) as Corpo;
      if (corpo?.success === false) {
        this.ultimoErro = `${nome}: ${String(corpo.error_code ?? corpo.errorCode ?? "")} ${String(corpo.error_msg ?? corpo.errorMsg ?? "")}`.trim();
        return;
      }
      if (nome === "saldo") {
        if (!this.saldoPrimeiro) this.saldoPrimeiro = corpo;
        this.saldoUltimo = corpo;
        return;
      }
      this.vistas.set(chaveDaResposta(nome, envio), corpo);
    } catch {
      /* resposta sem corpo (redirecionamento, aba fechada): ignora */
    }
  }

  async esperar(chave: string, ms = 30000): Promise<Corpo | null> {
    const fim = Date.now() + ms;
    while (Date.now() < fim) {
      const v = this.vistas.get(chave);
      if (v) return v;
      await sleep(400);
    }
    return null;
  }
}

async function clicarAba(page: Page, texto: string): Promise<boolean> {
  return page
    .evaluate((t) => {
      const el = Array.from(document.querySelectorAll('[data-testid="beast-core-tab-itemLabel"]')).find(
        (e) => ((e as HTMLElement).innerText || "").trim() === t
      ) as HTMLElement | undefined;
      if (!el) return false;
      el.click();
      return true;
    }, texto)
    .catch(() => false);
}

/** Clica na seta de próxima página da paginação visível. */
async function proximaPagina(page: Page): Promise<"ok" | "fim" | "sem_paginacao"> {
  return page
    .evaluate(() => {
      const pags = Array.from(document.querySelectorAll('[data-testid="beast-core-pagination"]')).filter(
        (e) => (e as HTMLElement).offsetParent !== null
      );
      const pg = pags[pags.length - 1];
      if (!pg) return "sem_paginacao" as const;
      const nx = pg.querySelector('[data-testid="beast-core-pagination-next"]') as HTMLElement | null;
      if (!nx || /disabled/i.test(String(nx.className))) return "fim" as const;
      nx.click();
      return "ok" as const;
    })
    .catch(() => "sem_paginacao" as const);
}

/** Lê uma lista inteira. Quem manda parar é o total que a tela informou (ou a página
 *  vazia) — o desenho da paginação pode atrasar e não serve de sinal sozinho. Faltando
 *  linha e sem próxima página pra clicar, é falha explicada, não lista pela metade. */
async function lerLista(
  page: Page,
  coletor: Coletor,
  nome: Nome,
  primeira: number,
  teto: number
): Promise<{ corpos: Corpo[]; erro?: string }> {
  const corpos: Corpo[] = [];
  let pagina = primeira;
  for (let i = 0; i < teto; i++) {
    const corpo = await coletor.esperar(`${nome}:${pagina}:${POR_PAGINA}`);
    if (!corpo) {
      const motivo = coletor.ultimoErro ? ` (${coletor.ultimoErro})` : "";
      return { corpos, erro: `a página ${pagina} de ${nome} não carregou em 30 s${motivo}` };
    }
    corpos.push(corpo);
    if (leuTudo(nome, corpos)) return { corpos };
    // ainda falta: dá tempo de a tela desenhar a lista e a paginação antes de clicar
    await pausa();
    let passo: "ok" | "fim" | "sem_paginacao" = "sem_paginacao";
    for (let t = 0; t < 8 && passo !== "ok"; t++) {
      passo = await proximaPagina(page);
      if (passo !== "ok") await sleep(1000);
    }
    if (passo !== "ok") {
      const total = totalInformado(nome, corpos[0]);
      // previsto: a Temu pode contar no total o que não está na lista positiva; chegar
      // ao fim da paginação vale como lista inteira (o DaVinci avisa se vier a menos)
      if (total === null || (nome === "previsto" && passo === "fim")) return { corpos };
      const lidas = corpos.reduce((n, c) => n + linhasDaPagina(nome, c).length, 0);
      return { corpos, erro: `${nome}: a tela diz ${total} linha(s), li ${lidas} e não há próxima página` };
    }
    pagina += 1;
  }
  return { corpos, erro: `${nome} passou de ${teto} páginas` };
}

/** Abre o perfil, lê a tela e fecha. Devolve a leitura montada ou o motivo da falha. */
async function coletar(perfil: string, p: PedidoTemu): Promise<{ leitura: Corpo } | { reason: string }> {
  let browser: Browser | null = null;
  let page: Page | null = null;
  try {
    const ws = await adspower.start(perfil);
    browser = await puppeteer.connect({ browserWSEndpoint: ws, defaultViewport: null });
    page = await browser.newPage();
    const coletor = new Coletor();
    coletor.ouvir(page);
    await page.goto(URL_PAGAMENTOS, { waitUntil: "domcontentloaded", timeout: 90000 });
    // até 60 s pela Visão geral; login aparece na hora, não depois do prazo
    const prazo = Date.now() + 60000;
    while (!coletor.saldoPrimeiro && Date.now() < prazo) {
      if (/\/login\.html/.test(page.url())) {
        return { reason: "needs_manual_login: a Temu pediu login no perfil do AdsPower" };
      }
      await sleep(500);
    }
    if (/\/login\.html/.test(page.url())) {
      return { reason: "needs_manual_login: a Temu pediu login no perfil do AdsPower" };
    }
    if (!coletor.saldoPrimeiro) {
      const motivo = coletor.ultimoErro ? ` (a Temu respondeu: ${coletor.ultimoErro})` : "";
      return { reason: `a tela de Pagamentos da Temu não carregou em 60 s${motivo}` };
    }
    const lojaNaTela = await page
      .evaluate(() => (document.body.innerText.match(/\nPT\n(.+)\n/) || [])[1] || null)
      .catch(() => null);
    await pausa();

    if (!(await clicarAba(page, "Extratos"))) return { reason: "aba Extratos não encontrada" };
    const extrato = await lerLista(page, coletor, "extrato", 1, TETO_PAGINAS);
    if (extrato.erro) return { reason: extrato.erro };
    const periodo = await page
      .evaluate(
        () =>
          (document.querySelector('[data-testid="beast-core-rangePicker-htmlInput"]') as HTMLInputElement | null)
            ?.value || null
      )
      .catch(() => null);
    await pausa();

    if (!(await clicarAba(page, "Pedidos não processados"))) {
      return { reason: "aba Pedidos não processados não encontrada" };
    }
    const previsto = await lerLista(page, coletor, "previsto", 0, TETO_PAGINAS);
    if (previsto.erro) return { reason: previsto.erro };
    if (previsto.corpos[0]?.result?.total_count_over_limit) {
      return { reason: "a Temu cortou a lista de Pedidos não processados (acima do limite da tela)" };
    }
    await pausa();

    if (!(await clicarAba(page, "Pagamentos"))) return { reason: "aba Pagamentos não encontrada" };
    const saques = await lerLista(page, coletor, "saques", 1, TETO_PAGINAS_SAQUES);
    if (saques.erro) return { reason: saques.erro };

    // cada aba recarrega o saldo: se ele andou no meio da leitura, as linhas e o saldo
    // não são do mesmo instante e o encadeamento sairia torto pra sempre
    const antes = totalDoSaldo(coletor.saldoPrimeiro);
    const depois = totalDoSaldo(coletor.saldoUltimo);
    if (antes !== null && depois !== null && antes !== depois) {
      return {
        reason: `o saldo da Temu mudou durante a leitura (${(antes / 100).toFixed(2)} → ${(depois / 100).toFixed(2)}): descartei, a próxima rodada lê de novo`,
      };
    }
    return {
      leitura: montarLeitura(p.loja, {
        saldo: coletor.saldoUltimo || coletor.saldoPrimeiro,
        extrato: extrato.corpos,
        previsto: previsto.corpos,
        saques: saques.corpos,
        lojaNaTela,
        periodo,
      }),
    };
  } finally {
    try {
      if (page) await page.close();
    } catch {
      /* ignora */
    }
    try {
      if (browser) await browser.disconnect();
    } catch {
      /* ignora */
    }
    await fecharPerfil(perfil);
  }
}

/** Entrega a leitura. DaVinci ocupado (409: a rodada da loja está correndo), fora do
 *  ar ou rede: espera e tenta de novo — a leitura levou minutos pra ser feita. */
async function enviar(leitura: Corpo): Promise<{ ok: boolean; corpo: Record<string, unknown>; erro?: string }> {
  let ultimo = "";
  for (let tentativa = 1; tentativa <= 3; tentativa++) {
    try {
      const res = await fetch(`${cfg.davinciApiUrl}/api/conciliacao/temu/leitura`, {
        method: "POST",
        body: JSON.stringify(leitura),
        headers: { "X-Agent-Token": cfg.agentToken, "Content-Type": "application/json" },
        signal: AbortSignal.timeout(120000),
      });
      const corpo = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (res.ok) return { ok: true, corpo };
      ultimo = `DaVinci ${res.status}: ${JSON.stringify(corpo).slice(0, 300)}`;
      // recusa de verdade (leitura inválida, loja errada, token): tentar de novo não muda
      if (res.status !== 409 && res.status < 500) return { ok: false, corpo, erro: ultimo };
    } catch (e) {
      ultimo = `DaVinci não respondeu: ${String((e as Error)?.message || e).slice(0, 200)}`;
    }
    if (tentativa < 3) {
      log.warn(`Temu ${String(leitura.loja)}: ${ultimo} — tento de novo em 30 s (${tentativa}/3)`);
      await sleep(30000);
    }
  }
  return { ok: false, corpo: {}, erro: ultimo };
}

/** Abre o perfil, lê a tela e fecha — sem enviar nada. É a metade "Temu" do comando;
 *  separada pra dar pra ensaiar a leitura sozinha. */
export async function lerTela(perfilUserId: string, p: PedidoTemu): Promise<{ leitura: Corpo } | { reason: string }> {
  await adspower.garantirAberto();
  if (await adspower.active(perfilUserId)) {
    if (!temMarca(perfilUserId)) {
      // aberto por uma pessoa ou por outro robô: não mexe nem fecha
      return { reason: "perfil da Temu já está aberto no AdsPower (em uso): não mexi" };
    }
    log.warn(`Temu ${p.loja}: o perfil ${perfilUserId} ficou aberto de uma leitura anterior — fechando`);
    if (!(await fecharPerfil(perfilUserId))) {
      return { reason: "não consegui fechar o perfil que eu mesmo deixei aberto na leitura anterior" };
    }
    await sleep(3000);
  }
  marcar(perfilUserId);
  return coletar(perfilUserId, p);
}

export async function lerPagamentos(perfilUserId: string, p: PedidoTemu): Promise<ResultadoTemu> {
  const lido = await lerTela(perfilUserId, p);
  if ("reason" in lido) return { ok: false, reason: lido.reason };
  const leitura = lido.leitura;
  const contagem = {
    linhas: (leitura.extrato as Corpo[]).length,
    previstos: (leitura.previstos as Corpo[]).length,
    saques: (leitura.saques as Corpo[]).length,
  };
  const envio = await enviar(leitura);
  if (!envio.ok) return { ok: false, reason: envio.erro, ...contagem };
  const corpo = envio.corpo;
  log.info(
    `Temu ${p.loja}: ${contagem.linhas} linha(s), ${contagem.previstos} previsto(s), ${contagem.saques} saque(s) → DaVinci novas=${String(corpo.inseridos)} já=${String(corpo.ja_existiam)}`
  );
  // o resultado vai pro comando (2 000 caracteres): só os números, não o recálculo inteiro
  const resumo: Record<string, unknown> = {};
  for (const k of ["inseridos", "ja_existiam", "antes_do_inicio", "previstos", "aviso", "saldo_temu"]) {
    if (corpo[k] !== undefined) resumo[k] = corpo[k];
  }
  return { ok: true, ...contagem, davinci: resumo };
}
