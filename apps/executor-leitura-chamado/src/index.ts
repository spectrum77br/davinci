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
 * 25/09 (292592): a fila também traz consulta do Portal de Atendimento ao
 * Vendedor (`tipo: portal`) — chamado que o robô abriu NA TELA, pelo Portal.
 * Essa é lida pelo link direto (shopee_portal.ts), não pela busca do pedido.
 * `tipo: ambos` (294571): devolução acompanhada pela API COM uma consulta do
 * Portal aberta à mão — lê as duas e junta no mesmo chamado. E toda leitura da
 * devolução procura o que a tela PEDE com prazo ("Upload Evidence … até
 * 26-09-2026") e manda como pendência (vira aviso no chamado).
 *
 * 30/09 (298394): consulta do formulário de ajuda do Mercado Livre (`tipo:
 * ml_consulta`) — a parte de chamados do ML saiu do computador do Eduardo. Lida
 * pelo link direto (ml_consulta.ts) no perfil "<Loja> - Mercado Livre". Fila e
 * modo PRÓPRIOS: `LEITURA_ML=seco|real` (sem nada = desligado), pra ligar o ML
 * sem mexer na Shopee que já roda de verdade.
 *
 * 30/09 (298394), MÃOS do ML: `RESPONDER_ML=seco|real` posta a nossa réplica na
 * consulta (ml_responder.ts), com a senha PRÓPRIA `RESPONDER_TOKEN` — a
 * LEITOR_TOKEN segue só lendo. Roda no começo da passada: a resposta que saiu
 * volta pra fila de leitura e a conversa com ela entra no chamado na mesma passada.
 *
 * Uso:
 *   npm start                         loop (LEITURA_MODO do .env, default seco)
 *   npm start -- --uma-vez            uma passada e sai
 *   npm start -- --uma-vez --so 296012
 *                                     só esse caso (pedido Bling, pedido Shopee,
 *                                     nº da solicitação ou id do chamado)
 *   npm start -- --teste-pedido 260910MATESNVN --conta "Shopee Vortan"
 *                                     lê direto na tela, sem falar com o DaVinci
 *   npm start -- --teste-ml 484465159 --conta forpaper
 *                                     idem, consulta do ML
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
import * as portal from "./shopee_portal";
import * as ml from "./ml_consulta";
import * as maos from "./ml_responder";
import type { Caso } from "./davinci";

const VERSION = "1.4.0";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function arg(nome: string): string | undefined {
  const i = process.argv.indexOf(nome);
  return i >= 0 ? process.argv[i + 1] : undefined;
}
const UMA_VEZ = process.argv.includes("--uma-vez");
const SO = arg("--so");
const TESTE_PEDIDO = arg("--teste-pedido");
const TESTE_ML = arg("--teste-ml");

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
  await maximizar(page, nome);
  return { browser, page, userId };
}

/** A janela do perfil abre do tamanho que o AdsPower lembra — no Santiago era
 *  721×659: a página da devolução não cabia e o "Ver detalhes" ficava debaixo
 *  da barra lateral fixa da Shopee (28/09, 294571). Maximiza antes de ler; se
 *  não der, segue (o clique tem plano B pelo elemento). */
async function maximizar(page: Page, nome: string): Promise<void> {
  try {
    const cdp = await page.target().createCDPSession();
    const { windowId } = (await cdp.send("Browser.getWindowForTarget")) as { windowId: number };
    await cdp.send("Browser.setWindowBounds", { windowId, bounds: { windowState: "maximized" } });
    await cdp.detach().catch(() => undefined);
    await sleep(600);
    const larg = (await page.evaluate("window.innerWidth").catch(() => 0)) as number;
    if (larg && larg < 1100) log.warn(`${nome}: janela ainda estreita (${larg}px) depois de maximizar`);
  } catch (e: any) {
    log.warn(`${nome}: não maximizei a janela (${String(e?.message || e)}) — sigo assim`);
  }
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
  const tipo = caso.tipo || "devolucao";
  const consulta = caso.consulta_portal || (tipo === "portal" ? caso.chamado : "");
  const rot =
    `pedido ${caso.pedido_bling || "?"} (${caso.pedido_marketplace} / ${caso.chamado}` +
    `${consulta && consulta !== caso.chamado ? ` + consulta ${consulta}` : ""})`;
  try {
    let l: historico.Leitura;
    if (tipo === "ml_consulta") {
      l = await ml.ler(page, caso);
    } else if (tipo === "portal") {
      l = await portal.ler(page, caso);
    } else if (tipo === "ambos") {
      const dev = await historico.ler(page, caso);
      let por: historico.Leitura | null = null;
      let falhou = "";
      try {
        por = await portal.ler(page, caso);
      } catch (e: any) {
        if (e instanceof historico.LoginNecessario) throw e;
        falhou = String(e?.message || e).slice(0, 280);
        log.error(`${rot}: a devolução eu li, a consulta do Portal não — ${falhou}`);
      }
      l = {
        url: dev.url,
        situacao: dev.situacao,
        itens: [...dev.itens, ...(por?.itens ?? [])],
        falas: [...dev.falas, ...(por?.falas ?? [])],
        historico:
          `${dev.historico}\n\n———\n\n` +
          (por ? por.historico : `Consulta ${consulta} no Portal: não consegui ler — ${falhou}`),
        pendencias: dev.pendencias,
      };
    } else {
      l = await historico.ler(page, caso);
    }
    if (l.pendencias?.length) log.warn(`${rot}: a tela pede algo nosso — ${l.pendencias.join(" | ")}`);
    const deles = tipo === "ml_consulta" ? "do ML" : "da Shopee";
    log.info(`${rot}: ${l.itens.length} mensagem(ns) na janela, ${l.falas.length} ${deles}`);
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
      pendencias: l.pendencias ?? [],
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
    const todos = await adspower.list();
    const mapa = await perfis.mapa(todos);
    const precisaMl = cfg.ml !== "desligado" || cfg.responder !== "desligado";
    const perfisMl = precisaMl ? await perfis.mapaMl(todos) : new Map<string, perfis.Perfil>();
    if (cfg.responder !== "desligado") await responderMl(perfisMl);
    const mapaMl = cfg.ml === "desligado" ? new Map<string, perfis.Perfil>() : perfisMl;
    const contas = [...mapa.keys()];
    // `--so` sempre espia: não marca a entrega dos outros casos da fila.
    const espiar = cfg.modo === "seco" || !!SO;
    let casos = await davinci.fila(SO ? 200 : cfg.limite, contas, espiar, cfg.portal);
    if (mapaMl.size) {
      // Fila própria do ML (30/09): modo dele, sem mexer na Shopee.
      const espiarMl = cfg.ml === "seco" || !!SO;
      casos = casos.concat(
        await davinci.fila(SO ? 200 : cfg.limite, [], espiarMl, false, perfis.lojasMlDaFila(mapaMl))
      );
    }
    if (SO) {
      casos = casos.filter((c) =>
        [c.chamado_id, c.pedido_bling, c.pedido_marketplace, c.chamado].includes(SO)
      );
    }
    if (!casos.length) {
      log.info(`nada pra ler agora (${contas.length} lojas Shopee, ${mapaMl.size} ML com perfil)`);
      return;
    }
    // Agrupa pelo PERFIL: a mesma loja tem um perfil na Shopee e outro no ML.
    const porPerfil = new Map<string, { p: perfis.Perfil; lista: Caso[] }>();
    for (const c of casos) {
      const p =
        c.tipo === "ml_consulta"
          ? perfis.perfilMl(mapaMl, c.conta)
          : mapa.get((c.conta || "").trim().toLowerCase());
      if (!p) {
        log.warn(`${c.conta}: sem perfil no AdsPower — o caso ${c.chamado} fica pra depois`);
        continue;
      }
      const g = porPerfil.get(p.userId) || { p, lista: [] };
      g.lista.push(c);
      porPerfil.set(p.userId, g);
    }
    for (const { p, lista } of porPerfil.values()) {
      const s = await abrirPerfil(p.userId, p.nome);
      if (!s) continue;
      try {
        for (const caso of lista) {
          const real = caso.tipo === "ml_consulta" ? cfg.ml === "real" : cfg.modo === "real";
          await lerUm(s.page, caso, real);
        }
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

/** As mãos do ML: posta as réplicas pendentes nas consultas (30/09, 298394). */
async function responderMl(mapaMl: Map<string, perfis.Perfil>): Promise<void> {
  if (!cfg.responderToken) {
    log.error("RESPONDER_ML ligado mas RESPONDER_TOKEN vazio — não respondo nada");
    return;
  }
  const real = cfg.responder === "real" && !SO;
  let tarefas: davinci.Tarefa[];
  try {
    tarefas = await davinci.maosFila(SO ? 50 : 5, !real);
  } catch (e: any) {
    log.error(`mãos do ML: fila falhou — ${String(e?.message || e)}`);
    return;
  }
  if (SO) {
    tarefas = tarefas.filter((t) =>
      [t.mensagem_id, t.chamado_id, t.pedido_bling, t.pedido_marketplace, t.chamado].includes(SO)
    );
  }
  if (!tarefas.length) return;
  const porPerfil = new Map<string, { p: perfis.Perfil; lista: davinci.Tarefa[] }>();
  for (const t of tarefas) {
    const p = perfis.perfilMl(mapaMl, t.conta);
    if (!p) {
      const erro = `sem perfil "<loja> - Mercado Livre" no AdsPower pra "${t.conta}"`;
      log.warn(`pedido ${t.pedido_bling}: ${erro}`);
      if (real) await davinci.maosResultado(t.mensagem_id, false, erro).catch(() => undefined);
      continue;
    }
    const g = porPerfil.get(p.userId) || { p, lista: [] };
    g.lista.push(t);
    porPerfil.set(p.userId, g);
  }
  for (const { p, lista } of porPerfil.values()) {
    // perfil em uso: as tarefas (já `enviando`) voltam sozinhas pra fila em 30 min
    const s = await abrirPerfil(p.userId, p.nome);
    if (!s) continue;
    try {
      for (const t of lista) {
        const rot = `pedido ${t.pedido_bling || "?"} (consulta ${t.chamado})`;
        let r: maos.Resultado;
        try {
          r = await maos.responder(s.page, t, real);
        } catch (e: any) {
          r = { ok: false, erro: String(e?.message || e).slice(0, 280) };
          if (real) await davinci.maosResultado(t.mensagem_id, false, r.erro).catch(() => undefined);
          log.error(`${rot}: não respondi — ${r.erro}`);
          if (e instanceof historico.LoginNecessario) throw e;
          continue;
        }
        if (!real) {
          fs.mkdirSync(cfg.secoDir, { recursive: true });
          const file = path.join(cfg.secoDir, `responder-${t.pedido_bling || t.chamado}.json`);
          fs.writeFileSync(file, JSON.stringify({ tarefa: t, resultado: r }, null, 2));
          const oque = r.jaEstava
            ? "a conversa JÁ tem esta réplica — não escreveria"
            : r.ok
              ? "escrevi, fotografei e apaguei"
              : `não consegui: ${r.erro}`;
          log.info(`${rot}: SECO — ${oque}; ${file}`);
          continue;
        }
        const out = await davinci
          .maosResultado(t.mensagem_id, r.ok, r.erro)
          .catch((e) => ({ erro_ao_avisar: String(e?.message || e) }));
        if (r.ok) log.info(`${rot}: RÉPLICA ENVIADA${r.jaEstava ? " (já estava na conversa)" : ""} — ${JSON.stringify(out)}`);
        else log.error(`${rot}: não enviei — ${r.erro}${r.print ? ` (print: ${r.print})` : ""} — ${JSON.stringify(out)}`);
      }
    } catch (e: any) {
      log.error(`${p.nome}: parei as respostas da loja — ${String(e?.message || e)}`);
    } finally {
      await fecharPerfil(s);
    }
    await sleep(cfg.profileGapMs);
  }
}

/** `--teste-ml`: lê a consulta do ML direto na tela, sem DaVinci. */
async function testeMl(): Promise<void> {
  const conta = arg("--conta") || "";
  const p = perfis.perfilMl(await perfis.mapaMl(), conta);
  if (!p) throw new Error(`sem perfil "<loja> - Mercado Livre" no AdsPower pra "${conta}"`);
  const s = await abrirPerfil(p.userId, p.nome);
  if (!s) throw new Error(`perfil ${p.nome} em uso`);
  try {
    const l = await ml.ler(s.page, {
      chamado_id: "teste",
      chamado: TESTE_ML || "",
      pedido_bling: null,
      pedido_marketplace: null,
      conta,
      plataforma: "ml",
      leitura_robo_at: null,
      tipo: "ml_consulta",
    });
    console.log(JSON.stringify({ perfil: p.nome, ...l }, null, 2));
  } finally {
    await fecharPerfil(s);
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
      ` intervalo=${Math.round(cfg.intervaloMs / 60000)}min ml=${cfg.ml.toUpperCase()}` +
      ` responder=${cfg.responder.toUpperCase()}`
  );
  if (TESTE_ML) {
    await testeMl();
    return;
  }
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
