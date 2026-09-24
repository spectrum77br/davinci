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

/** Trabalhos que um executor pode fazer. Cada máquina liga só os seus
 *  (EXECUTOR_FILAS): desde 24/09/2026 o Melhor Envio roda no Mac Santiago e a
 *  Shopee/Tuta continuam no executor do Eduardo. */
export type Fila = "shopee" | "melhorenvio" | "tuta";
const FILAS: readonly Fila[] = ["shopee", "melhorenvio", "tuta"];

export interface Config {
  filas: Set<Fila>;
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
  melhorEnvioMotivo: string;
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

// Default = o que o executor antigo fazia, MENOS o Melhor Envio: quem só dá
// `git pull` não volta a disputar a suspensão com o Mac Santiago.
const filas = new Set<Fila>(
  str("EXECUTOR_FILAS", "shopee,tuta")
    .split(",")
    .map((f) => f.trim().toLowerCase())
    .filter((f): f is Fila => (FILAS as readonly string[]).includes(f))
);

export const cfg: Config = {
  filas,
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
  // "Envios › Liberados e postados". Em 24/09/2026 o painel já morava em
  // melhorenvio.com.br/painel; o antigo app.melhorenvio.com.br/envios/postados
  // dá 404. Sem "#…" aqui: o robô abre a sub-aba Postados clicando.
  melhorEnvioUrl: str("MELHORENVIO_ENVIOS_URL", "https://melhorenvio.com.br/painel/meus-envios"),
  melhorEnvioCalibrated: str("MELHORENVIO_CALIBRATED") === "true",
  // Opção marcada na janela "Qual é o motivo para suspender a entrega?"
  // (texto igual ao do Melhor Envio). O comando pode trazer outro no payload.
  melhorEnvioMotivo: str("MELHORENVIO_MOTIVO", "Meu cliente desistiu da compra"),
  logisticaLeaseLimit: int("LOGISTICA_LEASE_LIMIT", 5),
  tutaAdspowerUserId: str("TUTA_ADSPOWER_USER_ID"),
  tutaCaixaUrl: str("TUTA_CAIXA_URL", "https://app.tuta.com"),
};
