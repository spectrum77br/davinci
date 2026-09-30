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

export interface Perfil {
  userId: string;
  nome: string;
}

export async function mapa(
  perfis?: adspower.AdsPowerProfile[]
): Promise<Map<string, Perfil>> {
  const out = new Map<string, Perfil>();
  for (const p of perfis ?? (await adspower.list())) {
    const m = /^(.+?)\s*-\s*shopee\s*$/i.exec(p.name.trim());
    if (!m) continue;
    out.set(`shopee ${m[1].trim().toLowerCase()}`, { userId: p.user_id, nome: p.name });
  }
  for (const [conta, userId] of Object.entries(cfg.perfisExtra)) {
    out.set(conta, { userId, nome: `(PERFIS_EXTRA) ${userId}` });
  }
  return out;
}

// ------------------------------------------------------------------- ML (30/09)

/** "ML Forpaper", "forpaper", "Mercado Livre Aguiar 2", "Aguiar 2 - ML" ->
 *  "forpaper", "aguiar2". Mesma regra do DaVinci (`chamados_leitura.loja_ml`),
 *  que filtra a fila por essa chave. */
export function lojaMl(conta: string | null | undefined): string {
  let t = (conta || "").trim().toLowerCase();
  t = t.normalize("NFKD").replace(/[̀-ͯ]/g, "");
  t = t.replace(/^(mercado ?livre|meli|ml)[^a-z0-9]+/, "");
  t = t.replace(/[^a-z0-9]+(mercado ?livre|meli|ml)$/, "");
  return t.replace(/[^a-z0-9]+/g, "");
}

/** Loja com nome diferente no DaVinci e no AdsPower — chave do chamado -> chave
 *  do perfil (Vinicius 24/09: "ML Zorvex" = perfil "zortex - Mercado Livre"; o
 *  robô antigo tratava "victor mei" como "victor"). */
const APELIDO_ML: Record<string, string> = { zorvex: "zortex", victormei: "victor" };

const GRUPOS_OPERACAO = new Set(["lojas", "israel", "marrocos", "contas"]);

/** Perfis dedicados do ML ("Forpaper - Mercado Livre"), pela chave da loja. Com
 *  dois perfis da mesma loja, fica o do grupo de operação (Lojas) — o da
 *  Contabilidade é cópia e só vale se não houver outro. */
export async function mapaMl(
  perfis?: adspower.AdsPowerProfile[]
): Promise<Map<string, Perfil>> {
  const out = new Map<string, Perfil & { operacao: boolean }>();
  for (const p of perfis ?? (await adspower.list())) {
    const m = /^(.+?)\s*-\s*mercado\s*livre\s*$/i.exec(p.name.trim());
    if (!m) continue;
    const chave = lojaMl(m[1]);
    if (!chave) continue;
    const operacao = GRUPOS_OPERACAO.has((p.group_name || "").trim().toLowerCase());
    const ja = out.get(chave);
    if (ja && (ja.operacao || !operacao)) continue;
    out.set(chave, { userId: p.user_id, nome: p.name, operacao });
  }
  const final = new Map<string, Perfil>();
  for (const [k, v] of out) final.set(k, { userId: v.userId, nome: v.nome });
  return final;
}

/** O perfil do ML de um chamado (pelo nome da loja no chamado). */
export function perfilMl(m: Map<string, Perfil>, conta: string | null | undefined): Perfil | undefined {
  const k = lojaMl(conta);
  return m.get(APELIDO_ML[k] || k);
}

/** As chaves que a fila do DaVinci deve aceitar pra estes perfis: a do perfil e
 *  os apelidos que o chamado pode trazer. */
export function lojasMlDaFila(m: Map<string, Perfil>): string[] {
  const out = new Set(m.keys());
  for (const [apelido, perfil] of Object.entries(APELIDO_ML)) if (m.has(perfil)) out.add(apelido);
  return [...out].sort();
}
