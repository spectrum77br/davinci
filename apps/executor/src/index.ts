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
 */
import "dotenv/config";

import { cfg } from "./config";
import { log } from "./log";
import * as adspower from "./adspower";
import * as shopee from "./shopee";
import * as flashsale from "./flashsale";
import * as davinci from "./davinci";
import type { LeasedCommand } from "./davinci";

const VERSION = "1.0.0";
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

/** Um ciclo de trabalho: puxa a fila e drena SERIALMENTE (um perfil por vez). */
async function tick(): Promise<void> {
  if (ticking) return; // sem reentrância — um AdsPower de cada vez
  ticking = true;
  try {
    const commands = await davinci.lease(cfg.leaseLimit);
    if (commands.length === 0) return;
    log.info(`lease: ${commands.length} comando(s) para executar`);
    for (let i = 0; i < commands.length; i++) {
      await processCommand(commands[i]);
      // Espaça os perfis (rate-limit ~1 req/s da Local API do AdsPower).
      if (i < commands.length - 1) await sleep(cfg.profileGapMs);
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
      `calibrated=${cfg.calibrated} mode=${cfg.defaultMode}`
  );
  if (!cfg.agentToken) {
    log.error("MARKETING_AGENT_TOKEN vazio — o DaVinci vai recusar com 401. Preencha o .env.");
  }
  if (!cfg.calibrated) {
    log.warn(
      "SELECTORS_CALIBRATED != true — TRAVA ativa: os comandos vão FALHAR de " +
        "propósito (nada é alterado na Shopee). Vire para true quando quiser agir."
    );
  }

  // Heartbeat imediato + periódico (o dashboard mostra ONLINE em < 120s).
  await sendHeartbeat();
  setInterval(() => {
    void sendHeartbeat();
  }, cfg.heartbeatIntervalMs);

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
