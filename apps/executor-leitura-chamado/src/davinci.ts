/** Client das rotas do executor de leitura no DaVinci
 *  (/api/chamados/agent/leitor/*). Header X-Agent-Token = LEITOR_TOKEN, a senha
 *  PRÓPRIA deste robô (tabela chamados_leitores) — ela não abre nenhuma rota
 *  que poste na conversa com o cliente. */
import { cfg } from "./config";

/** Um caso pra reler. `tipo: devolucao` — `chamado` = nº da solicitação na
 *  Shopee (ex. 2609200FUTKM4JD) e a busca na tela é pelo `pedido_marketplace`.
 *  `tipo: portal` (25/09) — `chamado` = ID da consulta no Portal de Atendimento
 *  e `chamado_url` = a página dela. */
export interface Caso {
  chamado_id: string;
  chamado: string;
  chamado_url?: string | null;
  tipo?: "devolucao" | "portal" | "ambos";
  /** 25/09 (294571): consulta do Portal ligada ao chamado (tipo portal/ambos). */
  consulta_portal?: string | null;
  consulta_url?: string | null;
  pedido_bling: string | null;
  pedido_marketplace: string | null;
  conta: string | null;
  plataforma: string | null;
  leitura_robo_at: string | null;
}

/** Uma fala DELES, com a hora que a TELA mostra (ISO com -03:00). */
export interface Fala {
  texto: string;
  quando: string;
  autor: string;
}

export interface Resultado {
  chamado_id: string;
  ok: boolean;
  erro?: string;
  falas?: Fala[];
  historico?: string;
  /** O que a tela PEDE de nós com prazo (ex. evidência da 2ª disputa). */
  pendencias?: string[];
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${cfg.davinciApiUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agent-Token": cfg.token },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30000),
    });
  } catch (e: any) {
    throw new Error(`DaVinci inacessível em ${cfg.davinciApiUrl}${path}: ${e?.message || e}`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`DaVinci ${path} HTTP ${res.status}${text ? `: ${text.slice(0, 300)}` : ""}`);
  }
  return (await res.json()) as T;
}

/** Casos pra reler agora. `espiar` = só olha (não marca a entrega) — modo seco
 *  e o `--so`. `contas` = as lojas que este Mac tem perfil pra abrir. */
export async function fila(
  limite: number,
  contas: string[],
  espiar: boolean,
  portal: boolean
): Promise<Caso[]> {
  const data = await post<{ casos: Caso[] }>("/api/chamados/agent/leitor/fila", {
    limite,
    contas,
    espiar,
    portal,
    // v1.2: sabe ler a devolução E a consulta ligada no mesmo caso (tipo "ambos")
    consultas: portal,
  });
  return data.casos ?? [];
}

/** Devolve o que leu (ou `ok: false` + erro — vira ocorrência na Ouvidoria). */
export async function resultado(r: Resultado): Promise<Record<string, unknown>> {
  return post("/api/chamados/agent/leitor/resultado", {
    chamado_id: r.chamado_id,
    ok: r.ok,
    erro: r.erro ?? null,
    falas: r.falas ?? [],
    historico: r.historico ?? null,
    pendencias: r.pendencias ?? [],
    encerrado: false,
  });
}
