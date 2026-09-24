/** Client do control plane do DaVinci (superfície /api/marketing/agent).
 *  Todas as chamadas levam o header X-Agent-Token; o DaVinci recusa com 401 se
 *  o token não bater (ou se estiver vazio nas settings dele). */
import { cfg } from "./config";

/** Um comando entregue por /agent/lease, já enriquecido com o perfil AdsPower
 *  que o executor precisa abrir. */
export interface LeasedCommand {
  id: string;
  account_id: string;
  account_name: string;
  adspower_user_id: string | null;
  platform: string;
  action: string; // "pause" | "resume"
  payload: Record<string, unknown>;
  campaign_external_id: string | null;
}

/** Comando do robô da Logística (/api/logistica/agent/lease): hoje só
 *  `melhorenvio_suspender` — suspender a entrega de um envio postado. */
export interface LeasedLogisticaCommand {
  id: string;
  logistica_id: string;
  acao: string;
  payload: Record<string, unknown>;
  attempts: number;
}

export interface HeartbeatPayload {
  agent_name: string;
  version?: string;
  adspower_ok?: boolean | null;
  accounts_online?: number | null;
  info?: Record<string, unknown>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${cfg.davinciApiUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Agent-Token": cfg.agentToken,
      },
      body: JSON.stringify(body),
    });
  } catch (e: any) {
    throw new Error(
      `DaVinci inacessível em ${cfg.davinciApiUrl}${path}: ${e?.message || e}`
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `DaVinci ${path} HTTP ${res.status}${text ? `: ${text.slice(0, 300)}` : ""}`
    );
  }
  return (await res.json()) as T;
}

/** Reivindica até `limit` comandos 'browser' pendentes (o servidor os marca
 *  como 'claimed' atomicamente). Retorna [] quando não há trabalho. */
export async function lease(limit: number): Promise<LeasedCommand[]> {
  const data = await post<{ commands: LeasedCommand[] }>(
    "/api/marketing/agent/lease",
    { limit }
  );
  return data.commands ?? [];
}

/** Reporta o desfecho. Em 'done' de um pause/resume da loja inteira, o DaVinci
 *  espelha o resultado em applied_state (o estado ACTUAL que o reconciler lê). */
export async function reportResult(
  commandId: string,
  status: "done" | "failed",
  result?: string
): Promise<void> {
  await post(`/api/marketing/agent/commands/${commandId}/result`, {
    status,
    result: result ?? null,
  });
}

/** Comandos pendentes do robô da Logística (mesmo token M2M). `acoes` = só
 *  as que esta máquina faz — o servidor não entrega as outras. */
export async function leaseLogistica(
  limit: number,
  acoes: string[]
): Promise<LeasedLogisticaCommand[]> {
  const data = await post<{ comandos: LeasedLogisticaCommand[] }>(
    "/api/logistica/agent/lease",
    { limit, acoes }
  );
  return data.comandos ?? [];
}

export async function reportLogistica(
  commandId: string,
  status: "done" | "failed",
  result?: string
): Promise<void> {
  await post(`/api/logistica/agent/comandos/${commandId}/resultado`, {
    status,
    result: result ?? null,
  });
}

/** Sinal de vida — alimenta o badge ONLINE/OFFLINE + saúde do AdsPower no
 *  dashboard (o DaVinci considera ONLINE quando o último heartbeat < 120s). */
export async function heartbeat(payload: HeartbeatPayload): Promise<void> {
  await post("/api/marketing/agent/heartbeat", payload);
}

/** Sinal de vida do robô do Melhor Envio — quem olha é a Ouvidoria (Vigia
 *  Robô Melhor Envio), separado do badge do Marketing. */
export async function heartbeatLogistica(payload: HeartbeatPayload): Promise<void> {
  await post("/api/logistica/agent/heartbeat", {
    agent_name: payload.agent_name,
    version: payload.version,
    adspower_ok: payload.adspower_ok,
    info: payload.info ?? {},
  });
}
