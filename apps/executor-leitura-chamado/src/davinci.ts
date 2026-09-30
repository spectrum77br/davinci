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
  /** 30/09: `ml_consulta` = consulta do formulário de ajuda do ML
   *  (`chamado_url` = mercadolivre.com.br/cases/detail/<N>). */
  tipo?: "devolucao" | "portal" | "ambos" | "ml_consulta";
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
  /** 30/09 (ML): a tela só mostra o dia — `quando` vale pela data. */
  so_dia?: boolean;
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

async function post<T>(path: string, body: unknown, token = cfg.token): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${cfg.davinciApiUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agent-Token": token },
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
 *  e o `--so`. `contas` = as lojas da Shopee que este Mac tem perfil pra abrir;
 *  `mlLojas` (30/09) = as do ML ("forpaper", "aguiar2"), vazio = nenhuma. */
export async function fila(
  limite: number,
  contas: string[],
  espiar: boolean,
  portal: boolean,
  mlLojas: string[] = []
): Promise<Caso[]> {
  const data = await post<{ casos: Caso[] }>("/api/chamados/agent/leitor/fila", {
    limite,
    contas,
    espiar,
    portal,
    // v1.2: sabe ler a devolução E a consulta ligada no mesmo caso (tipo "ambos")
    consultas: portal,
    ml_lojas: mlLojas,
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

// ------------------------------------------------ mãos do ML (30/09, 298394)
// Senha PRÓPRIA (RESPONDER_TOKEN, linha de `chamados_leitores` com
// `responde_ml`): a LEITOR_TOKEN só lê e não abre estas rotas.

/** Uma réplica nossa pra postar na consulta do ML (mesmo formato do lease). */
export interface Tarefa {
  tipo: "abrir" | "responder";
  mensagem_id: string;
  chamado_id: string;
  pedido_bling: string | null;
  pedido_marketplace: string | null;
  conta: string | null;
  plataforma: string | null;
  chamado: string | null;
  chamado_url: string | null;
  texto: string;
  anexos: string[];
  /** Quando a réplica nasceu (ISO) — a trava de "já enviada" só olha dali pra frente. */
  criada_em?: string | null;
}

/** Réplicas pra postar. `espiar` = seco: não marca `enviando`. */
export async function maosFila(limite: number, espiar: boolean): Promise<Tarefa[]> {
  const data = await post<{ tarefas: Tarefa[] }>(
    "/api/chamados/agent/leitor/responder/fila",
    { limite, espiar },
    cfg.responderToken
  );
  return data.tarefas ?? [];
}

/** Enviada (ok) ou falhou (+ erro — volta pra fila até 3 tentativas). */
export async function maosResultado(mensagemId: string, ok: boolean, erro?: string): Promise<Record<string, unknown>> {
  return post(
    "/api/chamados/agent/leitor/responder/resultado",
    { mensagem_id: mensagemId, ok, erro: erro ?? null },
    cfg.responderToken
  );
}

/** A foto da réplica (pra anexar na consulta). */
export async function baixarAnexo(id: string): Promise<{ conteudo: Buffer; tipo: string }> {
  const res = await fetch(`${cfg.davinciApiUrl}/api/chamados/agent/anexos/${id}`, {
    headers: { "X-Agent-Token": cfg.responderToken },
    signal: AbortSignal.timeout(60000),
  });
  if (!res.ok) throw new Error(`DaVinci anexo ${id} HTTP ${res.status}`);
  return { conteudo: Buffer.from(await res.arrayBuffer()), tipo: res.headers.get("content-type") || "" };
}
