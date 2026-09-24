import { execFile } from "node:child_process";
import { log } from "./log";

const BASE = (process.env.ADSPOWER_API_BASE || "http://local.adspower.net:50325").replace(/\/$/, "");
// Nome do app pra reabrir o AdsPower quando a Local API não responde (ex.:
// "AdsPower Global"). Vazio = não abre sozinho (o executor do Eduardo).
const APP = process.env.ADSPOWER_APP || "";
const API_KEY = process.env.ADSPOWER_API_KEY || "";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Serializa TODAS as chamadas e garante ~1 req/s (limite da Local API do AdsPower).
let chain: Promise<unknown> = Promise.resolve();
let lastAt = 0;

function rateLimited<T>(fn: () => Promise<T>): Promise<T> {
  const run = async (): Promise<T> => {
    const wait = Math.max(0, 1100 - (Date.now() - lastAt));
    if (wait) await sleep(wait);
    try {
      return await fn();
    } finally {
      lastAt = Date.now();
    }
  };
  const p = chain.then(run, run);
  chain = p.catch(() => undefined);
  return p;
}

interface AdsPowerEnvelope {
  code: number;
  msg?: string;
  data?: any;
}

async function apiGet(pathQ: string): Promise<any> {
  return rateLimited(async () => {
    const headers: Record<string, string> = {};
    // A Local API normalmente nao exige chave. Se a sua versao exigir, ela vai aqui.
    if (API_KEY) headers["Authorization"] = `Bearer ${API_KEY}`;
    let res: Response;
    try {
      res = await fetch(`${BASE}${pathQ}`, { headers });
    } catch (e: any) {
      throw new Error(`AdsPower inacessivel em ${BASE} (a Local API esta ligada?): ${e?.message || e}`);
    }
    if (!res.ok) throw new Error(`AdsPower HTTP ${res.status}`);
    const json = (await res.json()) as AdsPowerEnvelope;
    if (json.code !== 0) {
      throw new Error(`AdsPower: ${json.msg || "erro desconhecido"} (code ${json.code})`);
    }
    return json.data;
  });
}

export interface AdsPowerProfile {
  user_id: string;
  name: string;
}

/** Abre o profile e retorna o wsEndpoint (CDP) pra anexar o puppeteer. */
export async function start(userId: string): Promise<string> {
  const data = await apiGet(
    `/api/v1/browser/start?user_id=${encodeURIComponent(userId)}&open_tabs=1`
  );
  const ws = data?.ws?.puppeteer as string | undefined;
  if (!ws) throw new Error("AdsPower nao retornou ws.puppeteer (profile abriu?)");
  return ws;
}

/** Fecha o profile no AdsPower (e quem realmente fecha o browser). */
export async function stop(userId: string): Promise<void> {
  await apiGet(`/api/v1/browser/stop?user_id=${encodeURIComponent(userId)}`);
}

export async function active(userId: string): Promise<boolean> {
  const data = await apiGet(`/api/v1/browser/active?user_id=${encodeURIComponent(userId)}`);
  return data?.status === "Active";
}

/** Lista TODOS os profiles do AdsPower (mapear name -> user_id). Paginado: o
 *  Mac Santiago tinha 139 perfis em 24/09/2026 e a página vai até 100. */
export async function list(): Promise<AdsPowerProfile[]> {
  const out: AdsPowerProfile[] = [];
  for (let page = 1; page <= 20; page++) {
    const data = await apiGet(`/api/v1/user/list?page=${page}&page_size=100`);
    const items = (data?.list || []) as any[];
    out.push(...items.map((x) => ({ user_id: String(x.user_id), name: String(x.name || "") })));
    if (items.length < 100) break;
  }
  return out;
}

/** A Local API está de pé? (sem o rate-limit: é só um "alô"). */
async function responde(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/status`, { signal: AbortSignal.timeout(3000) });
    return res.ok && ((await res.json()) as AdsPowerEnvelope).code === 0;
  } catch {
    return false;
  }
}

/** Garante o AdsPower aberto antes de usar. Mac Santiago, 24/09/2026: o
 *  AdsPower fechou sozinho às 13:26 e a primeira suspensão de verdade voltou
 *  "AdsPower inacessível" — ninguém estava olhando a tela. Com ADSPOWER_APP,
 *  o executor abre o app (a sessão gráfica do LaunchAgent permite) e espera a
 *  API subir (~15 s). Devolve se a API respondeu. */
export async function garantirAberto(): Promise<boolean> {
  if (await responde()) return true;
  if (!APP || process.platform !== "darwin") return false;
  log.warn(`AdsPower não responde — abrindo "${APP}"`);
  await new Promise<void>((res) => execFile("open", ["-a", APP], () => res()));
  for (let i = 0; i < 20; i++) {
    await sleep(3000);
    if (await responde()) {
      log.info(`AdsPower de volta depois de ${(i + 1) * 3}s`);
      return true;
    }
  }
  log.error(`abri "${APP}" mas a Local API não subiu em 60s`);
  return false;
}
