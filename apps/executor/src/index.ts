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
 * Tuta ficam no executor do Eduardo. Desde 06/10/2026 há a `conferencia`
 * (Conferência Shopee, só leitura): uma loja por ciclo, depois das outras.
 */
import "dotenv/config";

import fs from "node:fs";

import { cfg, FILAS } from "./config";
import { log } from "./log";
import * as adspower from "./adspower";
import * as shopee from "./shopee";
import * as flashsale from "./flashsale";
import * as melhorenvio from "./melhorenvio";
import * as tuta from "./tuta";
import * as tiktok from "./tiktok";
import * as davinci from "./davinci";
import { coletarConferencia, type JobConferencia, type ResultadoConferencia } from "./conferencia";
import { periodoConferencia, resumoConferencia } from "./conferencia_util";
import type { LeasedCommand, LeasedLogisticaCommand } from "./davinci";

const VERSION = "1.3.0";
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

/** Pedido de senha ao comprador no chat da TikTok (devolução "Bloqueado"):
 *  abre o perfil da loja SÓ se estiver fechado, escreve no chat do pedido e
 *  fecha. Modo seco enquanto TIKTOK_CALIBRATED != true. */
async function processTiktokCommand(cmd: LeasedLogisticaCommand): Promise<void> {
  const p = (cmd.payload || {}) as tiktok.PedidoSenha;
  let r: tiktok.Resultado;
  try {
    r = await tiktok.pedirSenha(cmd.id, p);
  } catch (err: any) {
    r = { ok: false, motivo: "erro", detalhe: String(err?.message || err).slice(0, 300) };
  }
  await davinci.reportLogistica(cmd.id, r.ok ? "done" : "failed", JSON.stringify(r));
  const oque = r.ok ? (r.ja_enviada ? "já estava no chat" : `enviada${r.com_foto ? " + foto" : ""}`) : r.motivo;
  const msg = `TikTok senha ${p.pedido_bling || "?"} (${p.conta || "?"}) → ${oque}${r.detalhe ? ` — ${r.detalhe}` : ""}`;
  if (r.ok || r.seco) log.info(msg);
  else log.warn(msg);
}

async function processLogisticaCommand(cmd: LeasedLogisticaCommand): Promise<void> {
  if (cmd.acao === "tuta_devolucoes") {
    await processTutaCommand(cmd);
    return;
  }
  if (cmd.acao === "tiktok_senha") {
    await processTiktokCommand(cmd);
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
    await adspower.garantirAberto();
    const ws = await adspower.start(userId);
    session = await melhorenvio.connect(ws);
    const p = (cmd.payload || {}) as Record<string, unknown>;
    const pedidos = [
      p.pedido_amazon ? `Pedido Amazon ${p.pedido_amazon}` : "",
      p.pedido_bling ? `Bling ${p.pedido_bling}` : "",
    ].filter(Boolean);
    const r = await melhorenvio.suspenderEntrega(session.page, {
      rastreio,
      commit: p.commit === true,
      motivo: typeof p.motivo === "string" ? p.motivo : undefined,
      texto: `${pedidos.join(" / ") || `Rastreio ${rastreio}`} — suspensão pedida pelo DaVinci`,
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
  if (cfg.filas.has("tiktok")) acoes.push("tiktok_senha");
  return acoes;
}

/** O servidor recusa resultado acima de 3 MB (413); folga pro envelope. */
const MAX_RESULTADO_CONFERENCIA = 2_900_000;
let adspowerForaAvisado = false;

/** Entrega o resultado da loja. Rede/5xx: tenta de novo (a coleta levou
 *  minutos; perder por um soluço do servidor faria abrir o perfil de novo).
 *  Grande demais: manda só o status, sem os dados. */
async function entregarConferencia(job: JobConferencia, r: ResultadoConferencia): Promise<boolean> {
  let corpo = r;
  const tamanho = Buffer.byteLength(JSON.stringify(corpo));
  if (tamanho > MAX_RESULTADO_CONFERENCIA) {
    corpo = { status: "erro", erro: `dados grandes demais pro DaVinci (${Math.round(tamanho / 1024)} KB)`, dados: null };
  }
  for (let tentativa = 1; tentativa <= 3; tentativa++) {
    try {
      await davinci.resultadoConferencia(job.coleta_id, corpo);
      return true;
    } catch (err: any) {
      const msg = String(err?.message || err);
      if (err instanceof davinci.DavinciHttpError && err.status === 413 && corpo.dados) {
        corpo = { status: "erro", erro: "dados grandes demais pro DaVinci (413)", dados: null };
        continue;
      }
      // 422 com dados: o DaVinci não reconheceu a forma dos números (executor
      // desatualizado?). Fecha a loja como erro em vez de deixar a coleta
      // presa até o lease vencer e abrir o perfil de novo.
      if (err instanceof davinci.DavinciHttpError && err.status === 422 && corpo.dados) {
        log.error(`conferência ${job.conta}: o DaVinci recusou os números: ${msg}`);
        corpo = { status: "erro", erro: `o DaVinci recusou os números desta loja (422): ${msg.slice(0, 300)}`, dados: null };
        continue;
      }
      if (err instanceof davinci.DavinciHttpError && err.status < 500) {
        log.error(`conferência ${job.conta}: o DaVinci recusou o resultado: ${msg}`);
        return false;
      }
      if (tentativa === 3) {
        log.error(
          `conferência ${job.conta}: não consegui entregar o resultado (${msg}) — ` +
            "o DaVinci devolve a loja pra fila sozinho em 20 min"
        );
        return false;
      }
      await sleep(tentativa * 10_000);
    }
  }
  return false;
}

/** Conferência Shopee: UMA loja por ciclo (cada uma leva alguns minutos;
 *  uma por vez deixa as outras filas respirarem entre as lojas). */
async function processConferencia(): Promise<void> {
  // AdsPower fora: nem pede trabalho — senão cada loja voltaria "erro" em
  // segundos e a execução inteira se perdia.
  if (!(await adspower.garantirAberto())) {
    if (!adspowerForaAvisado) log.warn("conferência: AdsPower não responde — não puxo loja até ele voltar");
    adspowerForaAvisado = true;
    return;
  }
  adspowerForaAvisado = false;
  let job: JobConferencia | null;
  try {
    job = await davinci.leaseConferencia(cfg.agentName);
  } catch (err: any) {
    log.warn(`lease conferência falhou: ${String(err?.message || err)}`);
    return;
  }
  if (!job) return;
  log.info(`conferência ${job.conta} (${job.adspower_user_id}): começando, tentativa ${job.tentativa}`);
  let r: ResultadoConferencia;
  try {
    r = await coletarConferencia(job);
  } catch (err: any) {
    r = { status: "erro", erro: String(err?.message || err).slice(0, 500), dados: null };
  }
  const entregue = await entregarConferencia(job, r);
  const d = r.dados;
  const msg =
    `conferência ${job.conta} → ${r.status}` +
    (d ? ` (${d.chamadas} chamadas, ${d.duracao_s}s${d.avisos.length ? `, ${d.avisos.length} aviso(s)` : ""})` : "") +
    (r.erro ? ` — ${r.erro}` : "") +
    (entregue ? "" : " [NÃO entregue]");
  if (r.status === "ok" || r.status === "aguardando_afiliados" || r.status === "perfil_em_uso") log.info(msg);
  else log.warn(msg);
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
    // Conferência Shopee por último: uma loja por ciclo.
    if (cfg.filas.has("conferencia")) {
      if (commands.length || logistica.length) await sleep(cfg.profileGapMs);
      await processConferencia();
    }
  } catch (err: any) {
    log.error(`tick falhou: ${String(err?.message || err)}`);
  } finally {
    ticking = false;
  }
}

/** Sinal de vida + saúde do AdsPower (nº de perfis é uma prova de conexão).
 *  Cada fila tem o seu: o da Shopee acende o badge do Marketing; o do Melhor
 *  Envio é o que a Ouvidoria ("Vigia Robô Melhor Envio") olha. Máquina que não
 *  faz Shopee não manda o da Shopee, senão o badge mentiria com a Shopee
 *  parada. */
async function sendHeartbeat(): Promise<void> {
  let adspowerOk: boolean | null = null;
  let accountsOnline: number | null = null;
  try {
    // Reabre o AdsPower se ele fechou (só com ADSPOWER_APP no .env): a
    // suspensão é corrida contra o tempo, não dá pra esperar alguém ver.
    await adspower.garantirAberto();
    const profiles = await adspower.list();
    adspowerOk = true;
    accountsOnline = profiles.length;
  } catch {
    adspowerOk = false;
  }
  if (cfg.filas.has("shopee")) {
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
  if (cfg.filas.has("melhorenvio")) {
    try {
      await davinci.heartbeatLogistica({
        agent_name: cfg.agentName,
        version: VERSION,
        adspower_ok: adspowerOk,
        info: {
          filas: [...cfg.filas],
          melhorenvio_calibrated: cfg.melhorEnvioCalibrated,
          perfil_melhorenvio: Boolean(cfg.melhorEnvioAdspowerUserId),
          tiktok_calibrated: cfg.filas.has("tiktok") ? cfg.tiktokCalibrated : undefined,
        },
      });
    } catch (err: any) {
      log.error(`heartbeat logística falhou: ${String(err?.message || err)}`);
    }
  }
}

/** `npm start -- --teste-tiktok "TikTok Mini" 585945262750598710`: roda o
 *  pedido de senha de UM pedido em modo seco (nunca envia), sem o DaVinci —
 *  pra conferir perfil, login e tela de uma loja. */
async function testeTiktok(conta: string, pedido: string): Promise<void> {
  const r = await tiktok.pedirSenha("teste", {
    conta,
    pedido_tiktok: pedido,
    texto: "(teste — nada é enviado)",
    texto_loja: "(teste da loja {LOJA} — nada é enviado)",
    commit: false,
  });
  console.log(JSON.stringify(r, null, 1));
}

/** `npm start -- --teste-conferencia k1dkeaxv [--parcial] [--login-auto]
 *  [--json saida.json]`: coleta UMA loja pelo perfil do AdsPower com as
 *  semanas de agora (semanal, ou parcial com --parcial) e imprime o resumo —
 *  sem o DaVinci, nada é enviado. Não espera os afiliados do último dia (só
 *  avisa). Sem --login-auto, loja deslogada não leva clique no Entrar. */
async function testeConferencia(uid: string): Promise<void> {
  if (!uid || uid.startsWith("--")) {
    console.error("uso: npm start -- --teste-conferencia <adspower_user_id> [--parcial] [--login-auto] [--json arquivo]");
    process.exitCode = 2;
    return;
  }
  const agora = new Date();
  const p = periodoConferencia(process.argv.includes("--parcial") ? "parcial" : "semanal", agora);
  const job: JobConferencia = {
    coleta_id: "teste",
    execucao_id: "teste",
    conta: uid,
    adspower_user_id: uid,
    grupo: "celular",
    semanas: p.semanas,
    afiliados_ate: p.afiliados_ate,
    esperar_afiliados_ate: agora.toISOString(), // não espera: só avisa
    corte: new Date(agora.getTime() + 3 * 3600_000).toISOString(),
    login_auto: process.argv.includes("--login-auto"),
    tentativa: 1,
  };
  console.log(`teste ${p.tipo}: ${p.semanas.map((s) => `${s.inicio}..${s.fim}`).join(" | ")}`);
  if (!(await adspower.garantirAberto())) {
    console.error("AdsPower não responde (a Local API está ligada?)");
    process.exitCode = 1;
    return;
  }
  const r = await coletarConferencia(job);
  console.log(resumoConferencia(r.status, r.erro, r.dados));
  const j = process.argv.indexOf("--json");
  const arquivo = j >= 0 ? process.argv[j + 1] : "";
  if (arquivo && r.dados) {
    fs.writeFileSync(arquivo, JSON.stringify(r, null, 1));
    console.log(`resultado completo em ${arquivo}`);
  }
}

async function main(): Promise<void> {
  const i = process.argv.indexOf("--teste-tiktok");
  if (i >= 0) {
    await testeTiktok(process.argv[i + 1] || "", process.argv[i + 2] || "");
    return;
  }
  const k = process.argv.indexOf("--teste-conferencia");
  if (k >= 0) {
    await testeConferencia(process.argv[k + 1] || "");
    return;
  }
  log.info(
    `davinci-executor v${VERSION} — api=${cfg.davinciApiUrl} agent=${cfg.agentName} ` +
      `filas=${[...cfg.filas].join(",") || "(nenhuma)"}` +
      (cfg.filas.has("shopee") ? ` calibrated=${cfg.calibrated} mode=${cfg.defaultMode}` : "")
  );
  if (!cfg.filas.size) {
    log.error(`EXECUTOR_FILAS sem nenhuma fila válida (${FILAS.join(", ")}) — nada a fazer.`);
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
  if (cfg.filas.has("tiktok")) {
    log.info(
      cfg.tiktokCalibrated
        ? "TikTok: TIKTOK_CALIBRATED=true — o robô pede a senha no chat de verdade."
        : "TikTok: MODO SECO — abre o chat do pedido, confere a conversa e não envia."
    );
  }
  if (cfg.filas.has("conferencia")) {
    log.info(
      "Conferência Shopee: ligada — uma loja por ciclo, só leitura" +
        (cfg.conferenciaLoginAuto ? "" : " (CONFERENCIA_LOGIN_AUTO=false: nunca clica em Entrar)") +
        (cfg.filas.has("shopee") || cfg.filas.has("melhorenvio") ? "" : " (esta máquina não manda sinal de vida)")
    );
  }
  if (cfg.filas.has("shopee") && !cfg.calibrated) {
    log.warn(
      "SELECTORS_CALIBRATED != true — TRAVA ativa: os comandos vão FALHAR de " +
        "propósito (nada é alterado na Shopee). Vire para true quando quiser agir."
    );
  }

  // Heartbeat imediato + periódico (o DaVinci considera ONLINE em < 120s).
  // Máquina só com `conferencia` não manda nenhum (o da Shopee acenderia o
  // badge do Marketing com a Shopee parada); quem acompanha a Conferência é a
  // própria tela, pelo andamento das coletas.
  if (cfg.filas.has("shopee") || cfg.filas.has("melhorenvio")) {
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
