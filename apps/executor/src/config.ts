/** Configuração vinda do ambiente (.env carregado pelo index.ts antes deste
 *  módulo). Defaults sensatos para tudo, exceto os que o operador precisa
 *  preencher (DAVINCI_API_URL, MARKETING_AGENT_TOKEN). */

function str(name: string, def = ""): string {
  const v = process.env[name];
  return v === undefined || v === "" ? def : v;
}

function int(name: string, def: number): number {
  const v = process.env[name];
  if (!v) return def;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) ? n : def;
}

export interface Config {
  davinciApiUrl: string;
  agentToken: string;
  agentName: string;
  leaseLimit: number;
  pollIntervalMs: number;
  heartbeatIntervalMs: number;
  profileGapMs: number;
  defaultMode: "manual" | "gmvmax";
  defaultScope: "all" | "ids" | "names";
  calibrated: boolean;
  // Melhor Envio (robô "Suspender entrega" da Logística)
  melhorEnvioAdspowerUserId: string;
  melhorEnvioUrl: string;
  melhorEnvioCalibrated: boolean;
  logisticaLeaseLimit: number;
  // Tuta (leitura da caixa atrás dos códigos de devolução)
  tutaAdspowerUserId: string;
  tutaCaixaUrl: string;
}

const modeRaw = str("EXECUTOR_DEFAULT_MODE", "manual");
const defaultMode: "manual" | "gmvmax" = modeRaw === "gmvmax" ? "gmvmax" : "manual";

const scopeRaw = str("EXECUTOR_DEFAULT_SCOPE", "all");
const defaultScope: "all" | "ids" | "names" =
  scopeRaw === "ids" || scopeRaw === "names" ? scopeRaw : "all";

export const cfg: Config = {
  // Barra final removida para montar as rotas com segurança.
  davinciApiUrl: str("DAVINCI_API_URL", "http://localhost:8000").replace(/\/$/, ""),
  agentToken: str("MARKETING_AGENT_TOKEN"),
  agentName: str("AGENT_NAME", "marionete"),
  leaseLimit: int("LEASE_LIMIT", 10),
  pollIntervalMs: int("POLL_INTERVAL_MS", 15000),
  heartbeatIntervalMs: int("HEARTBEAT_INTERVAL_MS", 60000),
  profileGapMs: int("PROFILE_GAP_MS", 2000),
  defaultMode,
  defaultScope,
  calibrated: str("SELECTORS_CALIBRATED") === "true",
  melhorEnvioAdspowerUserId: str("MELHORENVIO_ADSPOWER_USER_ID"),
  melhorEnvioUrl: str("MELHORENVIO_ENVIOS_URL", "https://app.melhorenvio.com.br/envios/postados"),
  melhorEnvioCalibrated: str("MELHORENVIO_CALIBRATED") === "true",
  logisticaLeaseLimit: int("LOGISTICA_LEASE_LIMIT", 5),
  tutaAdspowerUserId: str("TUTA_ADSPOWER_USER_ID"),
  tutaCaixaUrl: str("TUTA_CAIXA_URL", "https://app.tuta.com"),
};
