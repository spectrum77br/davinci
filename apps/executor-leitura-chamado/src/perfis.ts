/** Qual perfil do AdsPower abre a loja de cada chamado.
 *
 * Os nomes não batem entre os sistemas (24/09/2026): no chamado a loja é
 * "Shopee Vortan", no AdsPower o perfil é "Vortan - Shopee" e no Marketing do
 * DaVinci a mesma loja se chama "Luminin". Casamos pelo nome do PERFIL:
 * "<Loja> - Shopee" -> "shopee <loja>". O que não casar vai no PERFIS_EXTRA do
 * .env ({"Shopee Jlas": "k1dkfg0k"}). Loja sem perfil não é pedida na fila —
 * senão o caso dela voltaria sempre primeiro e tomaria o lugar dos outros. */
import { cfg } from "./config";
import * as adspower from "./adspower";

export async function mapa(): Promise<Map<string, { userId: string; nome: string }>> {
  const out = new Map<string, { userId: string; nome: string }>();
  for (const p of await adspower.list()) {
    const m = /^(.+?)\s*-\s*shopee\s*$/i.exec(p.name.trim());
    if (!m) continue;
    out.set(`shopee ${m[1].trim().toLowerCase()}`, { userId: p.user_id, nome: p.name });
  }
  for (const [conta, userId] of Object.entries(cfg.perfisExtra)) {
    out.set(conta, { userId, nome: `(PERFIS_EXTRA) ${userId}` });
  }
  return out;
}
