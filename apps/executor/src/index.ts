/**
 * DaVinci Executor — o "braço" local.
 *
 * Roda no Mac (do lado do AdsPower) e é a única peça do sistema que realmente
 * clica na Shopee. NÃO decide nada: o cérebro é o DaVinci (agenda BRT + outbox).
 * O ciclo é simples e sem estado próprio — se cair, reconverge no próximo tick:
 *
 *   1. heartbeat  → diz ao DaVinci que está vivo (badge ONLINE + saúde AdsPower)
 *   2. lease      → puxa comandos 'browser' pendentes (/agent/lease)
 *   3. para cada  → abre o perfil AdsPower, aplica pause/resume via shopee.ts,
 *                   reporta done/failed (/agent/commands/{id}/result)
 *
 * Sem SQLite, sem cron, sem UI: tudo isso agora vive no DaVinci. O retry é
 * natural — um comando 'failed' não mexe no applied_state, então o reconciler
 * do DaVinci reenfileira no próximo minuto se o desired ainda divergir.
 *
 * Substitui o antigo projeto standalone ~/marionete.
 *
 * Cada máquina liga só as filas dela (EXECUTOR_FILAS): desde 24/09/2026 o
 * "Suspender entrega" do Melhor Envio roda no Mac Santiago, e a Shopee e o
 * Tuta ficam no executor do Eduardo.
 */
import "dotenv/config";

import { cfg } from "./config";
import { log } from "./log";
import * as adspower from "./adspower";
import * as shopee from "./shopee";
import * as flashsale from "./flashsale";
import * as melhorenvio from "./melhorenvio";
import * as tuta from "./tuta";
import * as davinci from "./davinci";
import type { LeasedCommand, LeasedLogisticaCommand } from "./davinci";

const VERSION = "1.1.0";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

let ticking = false;

/** Executa UM comando: abre o perfil, aplica, reporta e SEMPRE fecha o perfil. */
async function processCommand(cmd: LeasedCommand): Promise<void> {
  const userId = cmd.adspower_user_id;
  if (!userId) {
    log.warn(`${cmd.account_name}: sem adspower_user_id — comando marcado failed`);
    await davinci.reportResult(cmd.id, "failed", "sem adspower_user_id");
    return;
  }
  // Ações suportadas pelo executor: pause/resume (liga/desliga anúncios) e
  // flash_duplicate (duplica a Oferta Relâmpago 'Em andamento' pro próximo dia).
  if (
    cmd.action !== "pause" &&
    cmd.action !== "resume" &&
    cmd.action !== "flash_duplicate"
  ) {
    await davinci.reportResult(
      cmd.id,
      "failed",
      `ação não suportada pelo executor: ${cmd.action}`
    );
    return;
  }

  let session: shopee.Session | null = null;
  try {
    const ws = await adspower.start(userId);
    session = await shopee.connect(ws);

    if (cmd.action === "flash_duplicate") {
      // Oferta Relâmpago: payload.commit=true cria de verdade (gated por
      // SELECTORS_CALIBRATED no flashsale.ts); sem commit é DRY (não cria).
      const payload = (cmd.payload || {}) as {
        commit?: boolean;
        target_day?: number | null;
      };
      const result = await flashsale.duplicateFlashSale(session.page, cmd.account_name, {
        commit: payload.commit === true,
        targetDay: payload.target_day ?? null,
      });
      await davinci.reportResult(
        cmd.id,
        result.ok ? "done" : "failed",
        JSON.stringify(result)
      );
      log.info(
        `${cmd.account_name} flash_duplicate → ok=${result.ok} created=${result.created} ` +
          `dry=${result.dry}${result.chosenDay ? ` dia=${result.chosenDay}` : ""}` +
          `${result.enabledOn != null ? ` on=${result.enabledOn}` : ""}`
      );
    } else {
      const on = cmd.action === "resume";
      // O comando pode carregar mode/filter no payload; senão usa o default do .env.
      const payload = (cmd.payload || {}) as {
        mode?: shopee.ControlMode;
        filter?: shopee.ManualFilter;
      };
      const mode: shopee.ControlMode =
        payload.mode === "gmvmax" || payload.mode === "manual"
          ? payload.mode
          : cfg.defaultMode;
      const filter: shopee.ManualFilter =
        payload.filter && payload.filter.scope
          ? payload.filter
          : { scope: cfg.defaultScope };
      const result = await shopee.applyState(session.page, cmd.account_name, mode, on, filter);
      await davinci.reportResult(cmd.id, "done", JSON.stringify(result));
      log.info(
        `${cmd.account_name} ${cmd.action} [${mode}] → matched=${result.matched} changed=${result.changed}`
      );
    }
  } catch (err: any) {
    if (err instanceof shopee.NeedsManualLogin || err?.name === "NeedsManualLogin") {
      log.warn(`${cmd.account_name}: precisa de login/verificação manual (needs_manual_login)`);
      await davinci.reportResult(cmd.id, "failed", "needs_manual_login");
    } else {
      const msg = String(err?.message || err);
      log.error(`${cmd.account_name} ${cmd.action} falhou: ${msg}`);
      await davinci.reportResult(cmd.id, "failed", msg.slice(0, 2000));
    }
  } finally {
    if (session) {
      try {
        await shopee.disconnect(session);
      } catch {
        /* ignora erro de desconexão */
      }
    }
    // adspower.stop() é o ÚNICO jeito correto de fechar o perfil (nunca browser.close()).
    try {
      await adspower.stop(userId);
    } catch (e) {
      log.error(`falha ao fechar profile ${userId}: ${String(e)}`);
    }
  }
}

/** Robô da Logística: "Suspender entrega" no painel do Melhor Envio (perfil
 *  AdsPower logado lá, MELHORENVIO_ADSPOWER_USER_ID). Modo seco enquanto
 *  MELHORENVIO_CALIBRATED != true: devolve o que achou, sem clicar em Solicitar. */
/** Leitura diária da caixa do Tuta (códigos de devolução). Só LÊ: não abre
 *  e-mail, não marca como lido, não responde e não apaga. Quem filtra o que é
 *  devolução é o servidor — ver services/tuta_devolucoes.py. */
async function processTutaCommand(cmd: LeasedLogisticaCommand): Promise<void> {
  const userId = cfg.tutaAdspowerUserId;
  if (!userId) {
    await davinci.reportLogistica(
      cmd.id,
      "failed",
      JSON.stringify({
        ok: false,
        reason:
          "TUTA_ADSPOWER_USER_ID vazio no executor (perfil do AdsPower logado no Tuta não configurado)",
      })
    );
    return;
  }
  let browser: Awaited<ReturnType<typeof tuta.connect>> | null = null;
  try {
    const ws = await adspower.start(userId);
    browser = await tuta.connect(ws);
    const r = await tuta.lerCaixa(browser.page);
    await davinci.reportLogistica(cmd.id, r.ok ? "done" : "failed", JSON.stringify(r));
    log.info(`Tuta: ok=${r.ok} linhas=${r.linhas ?? 0}${r.reason ? ` (${r.reason})` : ""}`);
  } catch (err: any) {
    const login = err instanceof tuta.TutaPrecisaLogin || err?.name === "TutaPrecisaLogin";
    const msg = String(err?.message || err);
    if (login) log.warn(`Tuta: precisa de login manual no perfil do AdsPower`);
    else log.error(`Tuta falhou: ${msg}`);
    await davinci.reportLogistica(
      cmd.id,
      "failed",
      JSON.stringify({
        ok: false,
        reason: login
          ? `needs_manual_login: entre no Tuta pelo perfil do AdsPower (${msg})`
          : msg.slice(0, 1500),
      })
    );
  } finally {
    if (browser) await tuta.disconnect(browser.browser);
    try {
      await adspower.stop(userId);
    } catch (e) {
      log.error(`falha ao fechar profile ${userId}: ${String(e)}`);
    }
  }
}

async function processLogisticaCommand(cmd: LeasedLogisticaCommand): Promise<void> {
  if (cmd.acao === "tuta_devolucoes") {
    await processTutaCommand(cmd);
    return;
  }
  if (cmd.acao !== "melhorenvio_suspender") {
    await davinci.reportLogistica(cmd.id, "failed", `ação não suportada pelo executor: ${cmd.acao}`);
    return;
  }
  const userId = cfg.melhorEnvioAdspowerUserId;
  const rastreio = String((cmd.payload || {}).rastreio || "").trim();
  if (!userId) {
    await davinci.reportLogistica(
      cmd.id,
      "failed",
      "MELHORENVIO_ADSPOWER_USER_ID vazio no executor (perfil do Melhor Envio não configurado)"
    );
    return;
  }
  if (!rastreio) {
    await davinci.reportLogistica(cmd.id, "failed", "comando sem rastreio");
    return;
  }
  let session: melhorenvio.Session | null = null;
  try {
    const ws = await adspower.start(userId);
    session = await melhorenvio.connect(ws);
    const r = await melhorenvio.suspenderEntrega(session.page, {
      rastreio,
      commit: (cmd.payload || {}).commit === true,
    });
    await davinci.reportLogistica(cmd.id, r.ok && r.requested ? "done" : "failed", JSON.stringify(r));
    log.info(
      `ME suspender ${rastreio} → ok=${r.ok} found=${r.found} requested=${r.requested} dry=${r.dry}` +
        (r.reason ? ` (${r.reason})` : "")
    );
  } catch (err: any) {
    if (err instanceof melhorenvio.NeedsManualLogin || err?.name === "NeedsManualLogin") {
      log.warn(`ME: precisa de login manual no perfil do Melhor Envio (needs_manual_login)`);
      await davinci.reportLogistica(cmd.id, "failed", "needs_manual_login: entre no Melhor Envio no perfil do AdsPower");
    } else {
      const msg = String(err?.message || err);
      log.error(`ME suspender ${rastreio} falhou: ${msg}`);
      await davinci.reportLogistica(cmd.id, "failed", msg.slice(0, 2000));
    }
  } finally {
    if (session) {
      try {
        await melhorenvio.disconnect(session);
      } catch {
        /* ignora */
      }
    }
    try {
      await adspower.stop(userId);
    } catch (e) {
      log.error(`falha ao fechar profile ${userId}: ${String(e)}`);
    }
  }
}

/** Ações da fila da Logística que esta máquina faz (o servidor só entrega
 *  essas). */
function acoesLogistica(): string[] {
  const acoes: string[] = [];
  if (cfg.filas.has("melhorenvio")) acoes.push("melhorenvio_suspender");
  if (cfg.filas.has("tuta")) acoes.push("tuta_devolucoes");
  return acoes;
}

/** Um ciclo de trabalho: puxa as filas e drena SERIALMENTE (um perfil por vez). */
async function tick(): Promise<void> {
  if (ticking) return; // sem reentrância — um AdsPower de cada vez
  ticking = true;
  try {
    const commands = cfg.filas.has("shopee") ? await davinci.lease(cfg.leaseLimit) : [];
    if (commands.length) log.info(`lease: ${commands.length} comando(s) para executar`);
    for (let i = 0; i < commands.length; i++) {
      await processCommand(commands[i]);
      // Espaça os perfis (rate-limit ~1 req/s da Local API do AdsPower).
      if (i < commands.length - 1) await sleep(cfg.profileGapMs);
    }
    // Fila do robô da Logística (Melhor Envio / Tuta) — mesmo laço serial.
    const acoes = acoesLogistica();
    let logistica: LeasedLogisticaCommand[] = [];
    if (acoes.length) {
      try {
        logistica = await davinci.leaseLogistica(cfg.logisticaLeaseLimit, acoes);
      } catch (err: any) {
        log.warn(`lease logística falhou: ${String(err?.message || err)}`);
      }
    }
    if (logistica.length) log.info(`lease logística: ${logistica.length} comando(s)`);
    for (let i = 0; i < logistica.length; i++) {
      if (commands.length || i > 0) await sleep(cfg.profileGapMs);
      await processLogisticaCommand(logistica[i]);
    }
  } catch (err: any) {
    log.error(`tick falhou: ${String(err?.message || err)}`);
  } finally {
    ticking = false;
  }
}

/** Sinal de vida + saúde do AdsPower (nº de perfis é uma prova de conexão). */
async function sendHeartbeat(): Promise<void> {
  let adspowerOk: boolean | null = null;
  let accountsOnline: number | null = null;
  try {
    const profiles = await adspower.list();
    adspowerOk = true;
    accountsOnline = profiles.length;
  } catch {
    adspowerOk = false;
  }
  try {
    await davinci.heartbeat({
      agent_name: cfg.agentName,
      version: VERSION,
      adspower_ok: adspowerOk,
      accounts_online: accountsOnline,
      info: { calibrated: cfg.calibrated, default_mode: cfg.defaultMode },
    });
  } catch (err: any) {
    log.error(`heartbeat falhou: ${String(err?.message || err)}`);
  }
}

async function main(): Promise<void> {
  log.info(
    `davinci-executor v${VERSION} — api=${cfg.davinciApiUrl} agent=${cfg.agentName} ` +
      `filas=${[...cfg.filas].join(",") || "(nenhuma)"} calibrated=${cfg.calibrated} mode=${cfg.defaultMode}`
  );
  if (!cfg.filas.size) {
    log.error("EXECUTOR_FILAS sem nenhuma fila válida (shopee, melhorenvio, tuta) — nada a fazer.");
  }
  if (!cfg.agentToken) {
    log.error("MARKETING_AGENT_TOKEN vazio — o DaVinci vai recusar com 401. Preencha o .env.");
  }
  if (cfg.filas.has("melhorenvio")) {
    if (!cfg.melhorEnvioAdspowerUserId) {
      log.error("MELHORENVIO_ADSPOWER_USER_ID vazio — as suspensões vão voltar como falhou.");
    }
    log.info(
      cfg.melhorEnvioCalibrated
        ? "Melhor Envio: MELHORENVIO_CALIBRATED=true — o robô clica de verdade."
        : "Melhor Envio: MODO SECO — acha o envio e para antes de clicar em Suspender entrega."
    );
  }
  if (cfg.filas.has("shopee") && !cfg.calibrated) {
    log.warn(
      "SELECTORS_CALIBRATED != true — TRAVA ativa: os comandos vão FALHAR de " +
        "propósito (nada é alterado na Shopee). Vire para true quando quiser agir."
    );
  }

  // Heartbeat imediato + periódico (o dashboard mostra ONLINE em < 120s). É o
  // badge do executor da SHOPEE: máquina sem `shopee` (o Mac Santiago, só
  // Melhor Envio) não manda, senão o badge mentiria com a Shopee parada.
  if (cfg.filas.has("shopee")) {
    await sendHeartbeat();
    setInterval(() => {
      void sendHeartbeat();
    }, cfg.heartbeatIntervalMs);
  }

  // Primeiro ciclo já, depois no ritmo do poll.
  await tick();
  setInterval(() => {
    void tick();
  }, cfg.pollIntervalMs);

  log.info(`rodando — puxando comandos a cada ${Math.round(cfg.pollIntervalMs / 1000)}s`);
}

main().catch((e) => {
  log.error(`fatal: ${String(e?.message || e)}`);
  process.exit(1);
});
