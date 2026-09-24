/**
 * DaVinci — Executor de leitura de chamado (Mac Santiago).
 *
 * Vinicius, 24/09/2026 (chamado 2609200FUTKM4JD, pedido 296012): a recusa
 * escrita da Shopee só existe no Seller Center ("Histórico da Solicitação"); a
 * API diz só "aguardando análise". Este robô abre a devolução pelo AdsPower,
 * lê e devolve o texto pro chamado. SÓ LÊ: não responde, não contesta, não
 * avalia — nenhuma rota que ele alcança posta na conversa com o cliente.
 *
 * Mesmo molde do executor da Logística (pasta ao lado), mas processo e senha
 * próprios. O ciclo, sem estado nenhum aqui (a cadência é do DaVinci):
 *
 *   1. pergunta ao DaVinci quais devoluções reler (/agent/leitor/fila), só das
 *      lojas que este Mac tem perfil no AdsPower;
 *   2. por loja: abre o perfil (se estiver FECHADO — aberto = alguém usando,
 *      pula e tenta na próxima), lê cada caso, FECHA o perfil;
 *   3. real: manda o que leu (/agent/leitor/resultado); seco: grava em
 *      logs/seco/ e não manda nada.
 *
 * Uso:
 *   npm start                         loop (LEITURA_MODO do .env, default seco)
 *   npm start -- --uma-vez            uma passada e sai
 *   npm start -- --uma-vez --so 296012
 *                                     só esse caso (pedido Bling, pedido Shopee,
 *                                     nº da solicitação ou id do chamado)
 *   npm start -- --teste-pedido 260910MATESNVN --conta "Shopee Vortan"
 *                                     lê direto na tela, sem falar com o DaVinci
 */
import "dotenv/config";

import fs from "node:fs";
import path from "node:path";
import puppeteer, { Browser, Page } from "puppeteer-core";
import { cfg } from "./config";
import { log } from "./log";
import * as adspower from "./adspower";
import * as davinci from "./davinci";
import * as perfis from "./perfis";
import * as historico from "./shopee_historico";
import type { Caso } from "./davinci";

const VERSION = "1.0.0";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function arg(nome: string): string | undefined {
  const i = process.argv.indexOf(nome);
  return i >= 0 ? process.argv[i + 1] : undefined;
}
const UMA_VEZ = process.argv.includes("--uma-vez");
const SO = arg("--so");
const TESTE_PEDIDO = arg("--teste-pedido");

interface Sessao {
  browser: Browser;
  page: Page;
  userId: string;
}

/** Abre o perfil SE estiver fechado. Aberto (ou "em uso" noutra máquina) =
 *  alguém trabalhando nele: devolve null e o caso volta na próxima passada. */
async function abrirPerfil(userId: string, nome: string): Promise<Sessao | null> {
  if (await adspower.active(userId).catch(() => false)) {
    log.warn(`${nome}: perfil já está aberto neste Mac — alguém usando; pulo`);
    return null;
  }
  let ws: string;
  try {
    ws = await adspower.start(userId);
  } catch (e: any) {
    log.warn(`${nome}: AdsPower não abriu o perfil (${String(e?.message || e)}) — pulo`);
    return null;
  }
  const browser = await puppeteer.connect({ browserWSEndpoint: ws, defaultViewport: null });
  const page = await browser.newPage();
  return { browser, page, userId };
}

async function fecharPerfil(s: Sessao): Promise<void> {
  await s.page.close().catch(() => undefined);
  try {
    s.browser.disconnect();
  } catch {
    /* já desconectado */
  }
  // NUNCA browser.close(): quem fecha o perfil é o AdsPower.
  await adspower.stop(s.userId).catch((e) => log.warn(`stop ${s.userId}: ${e?.message || e}`));
}

async function print(page: Page, nome: string): Promise<string | undefined> {
  try {
    fs.mkdirSync(cfg.debugDir, { recursive: true });
    const file = path.join(cfg.debugDir, `${nome}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`);
    await page.screenshot({ path: file });
    return file;
  } catch {
    return undefined;
  }
}

async function lerUm(page: Page, caso: Caso, real: boolean): Promise<void> {
  const rot = `pedido ${caso.pedido_bling || "?"} (${caso.pedido_marketplace} / ${caso.chamado})`;
  try {
    const l = await historico.ler(page, caso);
    log.info(`${rot}: ${l.itens.length} mensagem(ns) na janela, ${l.falas.length} da Shopee`);
    if (!real) {
      fs.mkdirSync(cfg.secoDir, { recursive: true });
      const file = path.join(cfg.secoDir, `${caso.pedido_bling || caso.pedido_marketplace}.json`);
      fs.writeFileSync(file, JSON.stringify({ caso, leitura: l }, null, 2));
      log.info(`${rot}: SECO — nada enviado; leitura em ${file}`);
      return;
    }
    const r = await davinci.resultado({
      chamado_id: caso.chamado_id,
      ok: true,
      falas: l.falas,
      historico: l.historico,
    });
    log.info(`${rot}: enviado — ${JSON.stringify(r)}`);
  } catch (e: any) {
    const msg = String(e?.message || e).slice(0, 280);
    const img = await print(page, `erro-${caso.pedido_bling || caso.pedido_marketplace}`);
    log.error(`${rot}: não li — ${msg}${img ? ` (print: ${img})` : ""}`);
    if (real) {
      await davinci
        .resultado({ chamado_id: caso.chamado_id, ok: false, erro: msg })
        .catch((e2) => log.error(`${rot}: nem o erro chegou ao DaVinci — ${e2?.message || e2}`));
    }
    if (e instanceof historico.LoginNecessario) throw e; // os outros casos da loja também vão falhar
  }
}

let rodando = false;

async function passada(): Promise<void> {
  if (rodando) return;
  rodando = true;
  try {
    if (!(await adspower.garantirAberto())) {
      log.error("AdsPower não responde — passada cancelada");
      return;
    }
    const mapa = await perfis.mapa();
    const contas = [...mapa.keys()];
    // `--so` sempre espia: não marca a entrega dos outros casos da fila.
    const espiar = cfg.modo === "seco" || !!SO;
    let casos = await davinci.fila(SO ? 200 : cfg.limite, contas, espiar);
    if (SO) {
      casos = casos.filter((c) =>
        [c.chamado_id, c.pedido_bling, c.pedido_marketplace, c.chamado].includes(SO)
      );
    }
    if (!casos.length) {
      log.info(`nada pra ler agora (${contas.length} lojas com perfil)`);
      return;
    }
    const porConta = new Map<string, Caso[]>();
    for (const c of casos) {
      const k = (c.conta || "").trim().toLowerCase();
      porConta.set(k, [...(porConta.get(k) || []), c]);
    }
    for (const [conta, lista] of porConta) {
      const p = mapa.get(conta);
      if (!p) {
        log.warn(`${conta}: sem perfil no AdsPower — ${lista.length} caso(s) ficam pra depois`);
        continue;
      }
      const s = await abrirPerfil(p.userId, p.nome);
      if (!s) continue;
      try {
        for (const caso of lista) await lerUm(s.page, caso, cfg.modo === "real");
      } catch (e: any) {
        log.error(`${p.nome}: parei a loja — ${String(e?.message || e)}`);
      } finally {
        await fecharPerfil(s);
      }
      await sleep(cfg.profileGapMs);
    }
  } catch (e: any) {
    log.error(`passada falhou: ${String(e?.message || e)}`);
  } finally {
    rodando = false;
  }
}

/** `--teste-pedido`: lê direto na tela, sem DaVinci — pra conferir o caminho. */
async function teste(): Promise<void> {
  const conta = (arg("--conta") || "").trim().toLowerCase();
  const p = (await perfis.mapa()).get(conta);
  if (!p) throw new Error(`sem perfil no AdsPower pra "${conta}"`);
  const s = await abrirPerfil(p.userId, p.nome);
  if (!s) throw new Error(`perfil ${p.nome} em uso`);
  try {
    const l = await historico.ler(s.page, {
      chamado_id: "teste",
      chamado: arg("--chamado") || "",
      pedido_bling: null,
      pedido_marketplace: TESTE_PEDIDO || "",
      conta,
      plataforma: "shopee",
      leitura_robo_at: null,
    });
    console.log(JSON.stringify(l, null, 2));
  } finally {
    await fecharPerfil(s);
  }
}

async function main(): Promise<void> {
  log.info(
    `executor-leitura-chamado v${VERSION} — api=${cfg.davinciApiUrl} modo=${cfg.modo.toUpperCase()}` +
      ` intervalo=${Math.round(cfg.intervaloMs / 60000)}min`
  );
  if (TESTE_PEDIDO) {
    await teste();
    return;
  }
  if (!cfg.token) {
    log.error("LEITOR_TOKEN vazio — o DaVinci vai recusar com 401. Preencha o .env.");
  }
  await passada();
  if (UMA_VEZ) return;
  setInterval(() => void passada(), cfg.intervaloMs);
  log.info(`rodando — pergunta a fila a cada ${Math.round(cfg.intervaloMs / 60000)} min`);
}

main().catch((e) => {
  log.error(`fatal: ${String(e?.message || e)}`);
  process.exit(1);
});
