/** Configuração vinda do ambiente (.env carregado pelo index.ts antes deste
 *  módulo). Só o LEITOR_TOKEN não tem default — sem ele o DaVinci recusa. */

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

/** `seco` = lê a tela e grava o resultado em logs/seco/, SEM mandar nada pro
 *  DaVinci (e sem marcar a entrega na fila). `real` = manda o que leu pro
 *  chamado. Default seco: quem liga o real é uma pessoa, de propósito. */
export type Modo = "seco" | "real";

export interface Config {
  davinciApiUrl: string;
  token: string;
  modo: Modo;
  intervaloMs: number;
  limite: number;
  profileGapMs: number;
  sellerUrl: string;
  debugDir: string;
  secoDir: string;
  /** 25/09: também lê as consultas do Portal de Atendimento (LEITURA_PORTAL=0 desliga). */
  portal: boolean;
  /** conta do chamado (minúscula) -> user_id do AdsPower, por cima do casamento
   *  automático pelo nome do perfil ("Vortan - Shopee" -> "Shopee Vortan"). */
  perfisExtra: Record<string, string>;
}

function perfisExtra(): Record<string, string> {
  const raw = str("PERFIS_EXTRA");
  if (!raw) return {};
  try {
    const obj = JSON.parse(raw) as Record<string, string>;
    const out: Record<string, string> = {};
    for (const [conta, uid] of Object.entries(obj)) {
      if (conta.trim() && String(uid).trim()) out[conta.trim().toLowerCase()] = String(uid).trim();
    }
    return out;
  } catch {
    console.error("PERFIS_EXTRA não é um JSON válido — ignorado");
    return {};
  }
}

export const cfg: Config = {
  davinciApiUrl: str("DAVINCI_API_URL", "https://app.hadken.com").replace(/\/$/, ""),
  token: str("LEITOR_TOKEN"),
  modo: str("LEITURA_MODO", "seco") === "real" ? "real" : "seco",
  // A cadência de cada caso (3 h / 24 h frio) é do servidor; aqui é só de
  // quanto em quanto tempo o robô pergunta se tem alguém pra ler.
  intervaloMs: int("LEITURA_INTERVALO_MS", 10 * 60 * 1000),
  limite: int("LEITURA_LIMITE", 10),
  profileGapMs: int("PROFILE_GAP_MS", 3000),
  sellerUrl: str("SHOPEE_SELLER_URL", "https://seller.shopee.com.br").replace(/\/$/, ""),
  debugDir: str("DEBUG_DIR", "./debug"),
  secoDir: str("SECO_DIR", "./logs/seco"),
  portal: str("LEITURA_PORTAL", "1") !== "0",
  perfisExtra: perfisExtra(),
};
