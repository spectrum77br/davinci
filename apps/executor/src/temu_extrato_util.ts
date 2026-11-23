/**
 * Peças puras da leitura da Temu (sem AdsPower, sem navegador, sem config): o que o
 * teste exercita. O robô em si está em temu_extrato.ts.
 */
export type Corpo = Record<string, any>;
export type Nome = "saldo" | "extrato" | "previsto" | "saques";

/** Chave de uma resposta guardada: nome, página pedida e tamanho da página. */
export function chaveDaResposta(nome: Nome, envio: Corpo): string {
  return `${nome}:${envio.page ?? "-"}:${envio.size ?? "-"}`;
}

const numero = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** As linhas que uma página trouxe. No previsto, a lista positiva e a negativa (pedido
 *  que a Temu estima cobrar em vez de pagar), quando ela existir. */
export function linhasDaPagina(nome: Nome, corpo: Corpo): Corpo[] {
  const r = corpo?.result ?? {};
  if (nome === "extrato") return (r.mall_transaction_flow_volist as Corpo[]) ?? [];
  if (nome === "saques") return (r.mall_withdraw_order_simple_volist as Corpo[]) ?? [];
  if (nome === "previsto") {
    return [...((r.positive_settle_bill_list as Corpo[]) ?? []), ...((r.negative_settle_bill_list as Corpo[]) ?? [])];
  }
  return [];
}

/** Quantas linhas a tela diz que a lista tem (vem na resposta de cada página). */
export function totalInformado(nome: Nome, corpo: Corpo): number | null {
  const r = corpo?.result ?? {};
  if (nome === "previsto") return numero(r.total_count);
  if (nome === "extrato" || nome === "saques") return numero(r.total);
  return null;
}

/** Saldo total da tela em centavos (Reserva + Fundos disponíveis); null se não veio. */
export function totalDoSaldo(saldo: Corpo | null | undefined): number | null {
  const r = saldo?.result ?? {};
  const reserva = numero(r.reserve_amount?.amount);
  const disponivel = numero(r.payout_amount?.amount);
  return reserva === null || disponivel === null ? null : reserva + disponivel;
}

export interface Partes {
  saldo: Corpo;
  extrato: Corpo[];
  previsto: Corpo[];
  saques: Corpo[];
  lojaNaTela?: string | null;
  periodo?: string | null;
}

/** Junta as páginas lidas no JSON que o DaVinci recebe. Linha repetida entre duas
 *  páginas (a lista andou enquanto o robô passava) entra uma vez só; o total que a
 *  tela informou vai junto pro DaVinci conferir se veio tudo. Lista que chegou vazia e
 *  sem total conta como zero; lista que nem chegou vai sem total (o DaVinci recusa). */
export function montarLeitura(loja: string, p: Partes): Corpo {
  const unicos = (itens: Corpo[], chave: string): Corpo[] => {
    const vistos = new Set<string>();
    const out: Corpo[] = [];
    for (const item of itens) {
      const k = String(item?.[chave] ?? "");
      if (k && vistos.has(k)) continue;
      if (k) vistos.add(k);
      out.push(item);
    }
    return out;
  };
  const total = (nome: Nome, paginas: Corpo[], lidas: number): number | null => {
    if (!paginas.length) return null;
    return totalInformado(nome, paginas[0]) ?? (lidas === 0 ? 0 : null);
  };
  const linhas = unicos(
    p.extrato.flatMap((c) => linhasDaPagina("extrato", c)),
    "flow_id"
  );
  const previstos = unicos(
    p.previsto.flatMap((c) => linhasDaPagina("previsto", c)),
    "parent_order_sn"
  );
  const saques = p.saques.flatMap((c) => linhasDaPagina("saques", c));
  return {
    loja,
    loja_na_tela: p.lojaNaTela ?? null,
    periodo: p.periodo ?? null,
    saldo: p.saldo,
    extrato: linhas,
    extrato_total: total("extrato", p.extrato, linhas.length),
    previstos,
    previstos_total: total("previsto", p.previsto, previstos.length),
    saques,
    saques_total: total("saques", p.saques, saques.length) ?? saques.length,
  };
}

/** Quando parar de passar página: já leu tudo que a tela disse ter, ou a página veio
 *  vazia. Sem total informado, só o fim da paginação decide (devolve false). */
export function leuTudo(nome: Nome, paginas: Corpo[]): boolean {
  if (!paginas.length) return false;
  const ultima = linhasDaPagina(nome, paginas[paginas.length - 1]);
  if (!ultima.length) return true;
  const total = totalInformado(nome, paginas[0]);
  if (total === null) return false;
  // no previsto o total da tela conta os positivos; a lista negativa, se vier, vem à parte
  const lidas = paginas.reduce(
    (n, c) =>
      n + (nome === "previsto" ? ((c?.result?.positive_settle_bill_list as Corpo[]) ?? []).length : linhasDaPagina(nome, c).length),
    0
  );
  return lidas >= total;
}

// ---------- o perfil que o robô deixou aberto (23/11) ----------
/** Por que o robô deixou o perfil aberto: "leitura" = caiu no meio de uma leitura;
 *  "login" = a Temu pediu login e o perfil ficou na tela de login pra uma pessoa
 *  entrar (Vinicius, 23/11: "quando é assim precisa deixar na tela a loja"). */
export type MarcaDoPerfil = "leitura" | "login";

/** Conteúdo do arquivo da marca. */
export function textoDaMarca(marca: MarcaDoPerfil, agora: Date): string {
  return `${marca} ${agora.toISOString()}`;
}

/** Lê o arquivo da marca; a marca antiga (só a data, de antes de 23/11) é "leitura". */
export function lerMarca(conteudo: string | null): MarcaDoPerfil | null {
  if (conteudo === null) return null;
  return conteudo.trim().startsWith("login") ? "login" : "leitura";
}

/** O que fazer com o perfil que JÁ está aberto quando a rodada começa:
 *  - sem marca: uma pessoa ou outro robô abriu → não mexe nem fecha;
 *  - "leitura": este robô caiu no meio → fecha e abre de novo;
 *  - "login": este robô deixou na tela de login → tenta ler com ele aberto (se a pessoa
 *    já logou, lê e fecha; se não, fecha só a aba nova e o login continua na frente). */
export function decidirPerfilAberto(marca: MarcaDoPerfil | null): "em_uso" | "fechar_e_reabrir" | "ler_aberto" {
  if (marca === null) return "em_uso";
  return marca === "login" ? "ler_aberto" : "fechar_e_reabrir";
}

export const eTelaDeLogin = (url: string): boolean => /\/login\.html/.test(url);
