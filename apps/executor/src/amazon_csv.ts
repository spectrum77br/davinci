/**
 * Conciliação › Amazon por arquivo (16/10/2026). Comando `amazon_transacoes_csv` da
 * fila da Logística: abre o perfil da loja no AdsPower, vai em Pagamentos ›
 * Visualizar transações com o período na URL (epoch ms, todos os grupos de
 * pagamento), clica "Fazer download", e envia o CSV pro DaVinci em
 * POST /api/conciliacao/amazon/upload/script (X-Agent-Token). Só leitura na Amazon.
 * Se a Amazon pedir login/código, falha com `needs_manual_login` (ninguém está
 * olhando a tela do mini): alguém entra no perfil uma vez e a próxima rodada passa.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import puppeteer, { type Page } from "puppeteer-core";

import { cfg } from "./config";
import { log } from "./log";
import * as adspower from "./adspower";

export interface PedidoCsv {
  loja: string;
  dias: number;
}

export interface ResultadoCsv {
  ok: boolean;
  reason?: string;
  transacoes?: string;
  linhas?: number;
  davinci?: Record<string, unknown>;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function urlTransacoes(dias: number): string {
  const agora = Date.now();
  const ini = agora - dias * 24 * 3600 * 1000;
  const fim = agora + 24 * 3600 * 1000; // amanhã: a tela exclui o fim do período
  return `https://sellercentral.amazon.com.br/payments/event/view?accountType=ALL&startDate=${ini}&endDate=${fim}&resultsPerPage=50&pageNumber=1`;
}

async function temBotaoDownload(page: Page): Promise<boolean> {
  return page
    .evaluate(
      () =>
        !!Array.from(document.querySelectorAll("kat-button, button")).find(
          (b) => (b.getAttribute("label") || b.textContent || "").trim() === "Fazer download"
        )
    )
    .catch(() => false);
}

export async function buscarTransacoes(perfilUserId: string, p: PedidoCsv): Promise<ResultadoCsv> {
  await adspower.garantirAberto();
  const ws = await adspower.start(perfilUserId);
  const browser = await puppeteer.connect({ browserWSEndpoint: ws, defaultViewport: null });
  const pasta = path.join(os.tmpdir(), "davinci-conciliacao", new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-"));
  fs.mkdirSync(pasta, { recursive: true });
  try {
    const page = await browser.newPage();
    const url = urlTransacoes(p.dias);
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 90000 });
    // até 60 s pra tela aparecer; login/código = falha explicada
    let pronto = false;
    for (let i = 0; i < 20 && !pronto; i++) {
      await sleep(3000);
      pronto = await temBotaoDownload(page);
      if (!pronto && /\/ap\/(signin|mfa)/.test(page.url())) {
        return { ok: false, reason: "needs_manual_login: a Amazon pediu login/código no perfil do AdsPower" };
      }
    }
    if (!pronto) return { ok: false, reason: "tela de transações não carregou em 60 s" };
    const total = await page
      .evaluate(() => (document.body.innerText.match(/Item \d+ a \d+ de (\d+)/) || [])[1] || "?")
      .catch(() => "?");
    if (Number(total) > 600) log.warn(`Amazon ${p.loja}: ${total} transações no período; a tela só baixa 600`);
    const cdp = await page.createCDPSession();
    await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: pasta, eventsEnabled: true });
    const clicou = await page.evaluate(() => {
      const b = Array.from(document.querySelectorAll("kat-button, button")).find(
        (e) => (e.getAttribute("label") || e.textContent || "").trim() === "Fazer download"
      ) as HTMLElement | undefined;
      if (!b) return false;
      b.click();
      return true;
    });
    if (!clicou) return { ok: false, reason: "botão Fazer download não encontrado" };
    let arquivo: string | null = null;
    for (let i = 0; i < 40 && !arquivo; i++) {
      await sleep(1500);
      const f = fs.readdirSync(pasta).find((n) => n.toLowerCase().endsWith(".csv"));
      if (f) arquivo = path.join(pasta, f);
    }
    if (!arquivo) return { ok: false, reason: "download não apareceu em 60 s" };
    const csv = fs.readFileSync(arquivo);
    const linhas = csv.toString("utf8").split("\n").filter((l) => l.trim()).length - 1;
    const fd = new FormData();
    fd.append("loja", p.loja);
    fd.append("arquivo", new Blob([csv], { type: "text/csv" }), path.basename(arquivo));
    const res = await fetch(`${cfg.davinciApiUrl}/api/conciliacao/amazon/upload/script`, {
      method: "POST",
      body: fd,
      headers: { "X-Agent-Token": cfg.agentToken },
    });
    const corpo = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) return { ok: false, reason: `DaVinci ${res.status}: ${JSON.stringify(corpo).slice(0, 300)}`, linhas };
    log.info(
      `Amazon ${p.loja}: CSV ${linhas} linha(s) → DaVinci novas=${String(corpo.inseridos)} atualizadas=${String(corpo.atualizados)} já=${String(corpo.ja_existiam)}`
    );
    return { ok: true, transacoes: String(total), linhas, davinci: corpo };
  } finally {
    try {
      await browser.disconnect();
    } catch {
      /* ignora */
    }
    try {
      fs.rmSync(pasta, { recursive: true, force: true });
    } catch {
      /* ignora */
    }
    try {
      await adspower.stop(perfilUserId);
    } catch (e) {
      log.error(`falha ao fechar profile ${perfilUserId}: ${String(e)}`);
    }
  }
}
