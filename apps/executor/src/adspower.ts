const BASE = (process.env.ADSPOWER_API_BASE || "http://local.adspower.net:50325").replace(/\/$/, "");
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

/** Lista os profiles do AdsPower (mapear name -> user_id). */
export async function list(): Promise<AdsPowerProfile[]> {
  const data = await apiGet(`/api/v1/user/list?page=1&page_size=100`);
  const items = (data?.list || []) as any[];
  return items.map((x) => ({ user_id: x.user_id, name: x.name }));
}
