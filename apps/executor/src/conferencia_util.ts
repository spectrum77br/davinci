/**
 * Conferência Shopee — as peças PURAS da coleta (sem AdsPower, sem browser,
 * sem rede): datas em epoch BRT, conversão de dinheiro, montagem das
 * chamadas e do payload (ColetaDados v1), conta de páginas e a leitura das
 * respostas da Central do Vendedor. Ficam aqui pra o teste
 * (test/conferencia_util.test.ts) rodar com respostas sintéticas, sem abrir
 * perfil nenhum. Quem abre o perfil e faz as chamadas é o conferencia.ts.
 *
 * Formatos conferidos ao vivo na Barbosa em 06/10/2026 (contrato §8):
 *   - afiliados (`dis_*`) já vêm em R$ como string;
 *   - Ads e saldo vêm em inteiros ÷ 100.000;
 *   - `product/performance` vem em R$ (float).
 * Cliques de afiliados (07/10/2026): `data.clicks` do seller_daily e
 * `clicks` de cada item do seller_item_detail — chave sempre presente,
 * null quando a Shopee não manda (nunca 0 inventado).
 */

/** Uma semana do relatório (datas BRT, inclusive). */
export interface Semana {
  inicio: string; // YYYY-MM-DD
  fim: string;
}

export interface LoginLoja {
  username: string;
  shopid: number | null;
  shop_name: string | null;
}

export interface AfiliadosTotais {
  vendas: number;
  comissao: number;
  pedidos: number;
  /** cliques nos links de afiliados (`data.clicks`); null = a Shopee não mandou. */
  cliques: number | null;
}
export interface AfiliadoItem {
  item_id: string;
  nome: string;
  categoria_id: number | null;
  vendas: number;
  comissao: number;
  pedidos: number;
  /** `clicks` do item; null = a Shopee não mandou. A soma dos itens NÃO
   *  bate com o `data.clicks` do seller_daily (conferido na Barbosa): o
   *  servidor (calculo.py) divide o total na proporção dos itens. */
  cliques: number | null;
}
export interface AdsTotais {
  impressoes: number;
  cliques: number;
  gasto: number;
  vendas: number;
  pedidos: number;
}
export interface AdsItem {
  item_id: string | null; // null = anúncio sem produto (loja/GMV Max)
  nome: string;
  tipo: string;
  impressoes: number;
  cliques: number;
  gasto: number;
  vendas: number;
  pedidos: number;
}
export interface VendasTotais {
  valor: number;
  pedidos: number;
}
export interface VendaItem {
  item_id: string;
  nome: string;
  valor: number;
  pedidos: number;
}

/** O investimento de UM dia da semana (M-36 item 6, 24/11/2026): o
 *  Faturamento soma o mês pelos dias em vez de ratear a semana que cruza o
 *  mês. `afiliados` = comissão do dia (as parcelas do "Invest. afiliados"),
 *  `ads` = gasto de Ads do dia (as do "Invest. Ads"), em R$. null = a Shopee
 *  não devolveu aquele dia, ou o dia a dia dela não fechou com o total da
 *  semana. Os dias com número somam EXATAMENTE o total da semana que vai no
 *  payload (centavo a centavo), então quem soma os dias vê o mesmo número da
 *  tela da Conferência. */
export interface DiaDados {
  dia: string; // YYYY-MM-DD
  afiliados: number | null;
  ads: number | null;
}

/** Uma semana coletada. Seção null = a chamada falhou (o motivo vai em avisos). */
export interface SemanaDados {
  inicio: string;
  fim: string;
  afiliados: AfiliadosTotais | null;
  afiliados_itens: AfiliadoItem[] | null;
  ads: AdsTotais | null;
  ads_itens: AdsItem[] | null;
  vendas: VendasTotais | null;
  vendas_itens: VendaItem[] | null;
  avisos: string[];
  /** M-36 item 6 (24/11/2026): um item por dia da semana, do início ao fim.
   *  null = não lido. Coleta de antes de 24/11 não tem a chave. NÃO entra no
   *  relatório nem no status da coleta. */
  dias: DiaDados[] | null;
  /** Por que um lado do dia a dia ficou null (formato que a coleta não
   *  reconheceu, soma que não fechou). Fica de fora dos `avisos` de
   *  propósito: o relatório de seg/qua lista os avisos e não pode mudar. */
  dias_motivos: string[];
}

/** O que sobe pro DaVinci (contrato §4, versao 1). Dinheiro em R$ (float),
 *  contagens inteiras, item_id sempre string. */
export interface ColetaDados {
  versao: 1;
  coletado_em: string;
  duracao_s: number;
  chamadas: number;
  login: LoginLoja;
  login_auto_usado: boolean;
  saldo_ads: number | null;
  afiliados_ultimo_dia: string | null;
  semanas: SemanaDados[];
  avisos: string[];
}

/** Resposta num formato que não dá pra ler (campo faltando, lista que não é
 *  lista). Vira "seção null + aviso", nunca para a loja. */
export class ErroFormato extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = "ErroFormato";
  }
}

// ---------------------------------------------------------------------------
// Datas (America/Sao_Paulo = UTC-3 o ano todo desde 2019)
// ---------------------------------------------------------------------------
const DIA_S = 86_400;
const BRT_MS = 3 * 3600 * 1000;
const YMD = /^\d{4}-\d{2}-\d{2}$/;

function partes(ymd: string): [number, number, number] {
  const [y, m, d] = ymd.split("-").map(Number);
  return [y, m, d];
}

/** 00:00 BRT do dia, em segundos (o que a Shopee chama de start_time). */
export function epochInicioDia(ymd: string): number {
  const [y, m, d] = partes(ymd);
  return Date.UTC(y, m - 1, d, 3, 0, 0) / 1000;
}

/** 23:59:59 BRT do dia. Ads EXIGE esse fim: 00:00 do dia seguinte volta
 *  `code 5 end time must be 23:59:59`. */
export function epochFimDia(ymd: string): number {
  return epochInicioDia(ymd) + DIA_S - 1;
}

export function somarDias(ymd: string, n: number): string {
  const [y, m, d] = partes(ymd);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** Dia de hoje no fuso de São Paulo. */
export function hojeBRT(agora: Date = new Date()): string {
  return new Date(agora.getTime() - BRT_MS).toISOString().slice(0, 10);
}

/** 0 = domingo … 6 = sábado. */
export function diaDaSemana(ymd: string): number {
  const [y, m, d] = partes(ymd);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

/** Os dias da semana, do início ao fim (inclusive). */
export function diasDaSemana(s: Semana): string[] {
  const out: string[] = [];
  for (let d = s.inicio; d <= s.fim && out.length < 31; d = somarDias(d, 1)) out.push(d);
  return out;
}

/** "2026-10-04" → "04/10". */
export function ddmm(ymd: string): string {
  return `${ymd.slice(8, 10)}/${ymd.slice(5, 7)}`;
}

/** O domingo que abre a semana de `ymd` (o próprio dia, se for domingo). */
export function domingoDaSemana(ymd: string): string {
  return somarDias(ymd, -diaDaSemana(ymd));
}

/** As 4 semanas de uma execução, do jeito do servidor (contrato §1;
 *  `conferencia_shopee/periodos.py`). Só o `--teste-conferencia` usa — no dia
 *  a dia as semanas vêm prontas no job. A semana vai de domingo a sábado:
 *  `semanal` = a semana fechada anterior (domingo–sábado); `parcial` = domingo
 *  desta semana até ontem. `parcial` num domingo (sem dia nenhum ainda) cai
 *  para `semanal`. */
export function periodoConferencia(
  tipo: "semanal" | "parcial",
  agora: Date = new Date()
): { tipo: "semanal" | "parcial"; semanas: Semana[]; afiliados_ate: string } {
  const hoje = hojeBRT(agora);
  const domingo = domingoDaSemana(hoje);
  let s1: Semana;
  if (tipo === "parcial" && hoje !== domingo) {
    s1 = { inicio: domingo, fim: somarDias(hoje, -1) };
  } else {
    tipo = "semanal";
    // o último sábado antes desta semana e os 6 dias antes dele
    const sabado = somarDias(domingo, -1);
    s1 = { inicio: somarDias(sabado, -6), fim: sabado };
  }
  const semanas = [0, 1, 2, 3].map((k) => ({
    inicio: somarDias(s1.inicio, -7 * k),
    fim: somarDias(s1.fim, -7 * k),
  }));
  return { tipo, semanas, afiliados_ate: s1.fim };
}

/** ISO UTC sem milissegundos ("2026-10-06T16:41:02Z"). */
export function isoUtc(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

// ---------------------------------------------------------------------------
// Dinheiro e números
// ---------------------------------------------------------------------------
function centavos(n: number): number {
  return Math.round(n * 100) / 100;
}

/** R$ que já vem em reais (string `dis_*` ou float). null se não for número. */
export function reais(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(String(v).replace(",", "."));
  return Number.isFinite(n) ? centavos(n) : null;
}

/** Inteiro da Shopee em 1/100.000 de real (Ads, saldo) → R$. */
export function deCentMil(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? centavos(n / 100_000) : null;
}

function exigir<T>(v: T | null, campo: string): T {
  if (v === null) throw new ErroFormato(`campo ${campo} ausente`);
  return v;
}

function inteiro(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? Math.round(n) : null;
}

/** id do item como STRING (os ids da Shopee passam de 2^31; ainda cabem no
 *  double, mas o contrato pede string pra ninguém somar/arredondar). */
function idItem(v: unknown): string | null {
  if (v === null || v === undefined || v === "" || v === 0) return null;
  return String(v);
}

const MAX_NOME = 150;
function nome(v: unknown): string {
  return String(v ?? "").replace(/\s+/g, " ").trim().slice(0, MAX_NOME);
}

/** Lista da resposta: null/ausente só vale como vazia quando o total é 0. */
function lista(v: unknown, total: unknown, campo: string): any[] {
  if (Array.isArray(v)) return v;
  if ((v === null || v === undefined) && (total === undefined || total === null || Number(total) === 0)) return [];
  throw new ErroFormato(`campo ${campo} não é lista`);
}

// ---------------------------------------------------------------------------
// Leitura das respostas → seções do payload
// ---------------------------------------------------------------------------

/** `seller_daily` (body.data) → totais da semana + último dia publicado
 *  (maior `ymd` da lista; o `last_update_time` NÃO serve pra isso).
 *  `clicks` é opcional: sem ele a seção vale assim mesmo, com cliques null. */
export function afiliadosDoDaily(data: any): { totais: AfiliadosTotais; ultimoDia: string | null } {
  if (!data || typeof data !== "object") throw new ErroFormato("afiliados sem data");
  const totais: AfiliadosTotais = {
    vendas: exigir(reais(data.dis_total_actual_amount), "dis_total_actual_amount"),
    comissao: exigir(reais(data.dis_total_seller_commission), "dis_total_seller_commission"),
    pedidos: exigir(inteiro(data.total_order_count), "total_order_count"),
    cliques: inteiro(data.clicks),
  };
  return { totais, ultimoDia: ultimoDiaAfiliados(data.list) };
}

export function ultimoDiaAfiliados(list: unknown): string | null {
  if (!Array.isArray(list)) return null;
  let ultimo: string | null = null;
  for (const x of list) {
    const ymd = typeof x?.ymd === "string" ? x.ymd : "";
    if (YMD.test(ymd) && (ultimo === null || ymd > ultimo)) ultimo = ymd;
  }
  return ultimo;
}

/** Uma página de `seller_item_detail` (data.list). */
export function afiliadosItens(list: unknown): AfiliadoItem[] {
  return lista(list, 0, "list").map((x: any) => ({
    item_id: exigir(idItem(x?.item_id), "item_id"),
    nome: nome(x?.item_name),
    categoria_id: inteiro(x?.category_id),
    vendas: exigir(reais(x?.dis_gmv), "dis_gmv"),
    comissao: exigir(reais(x?.dis_spend), "dis_spend"),
    pedidos: inteiro(x?.orders) ?? 0,
    cliques: inteiro(x?.clicks),
  }));
}

/** `get_time_graph` → data.report_aggregate. */
export function adsDoAgregado(agg: any): AdsTotais {
  if (!agg || typeof agg !== "object") throw new ErroFormato("Ads sem report_aggregate");
  return {
    impressoes: exigir(inteiro(agg.impression), "impression"),
    cliques: exigir(inteiro(agg.click), "click"),
    gasto: exigir(deCentMil(agg.cost), "cost"),
    vendas: exigir(deCentMil(agg.broad_gmv), "broad_gmv"),
    pedidos: exigir(inteiro(agg.broad_order), "broad_order"),
  };
}

/** Uma página de `homepage/query` (data.entry_list). Anúncio sem
 *  `manual_product_ads` (loja, GMV Max…) fica com item_id null. */
export function adsItens(entries: unknown): AdsItem[] {
  return lista(entries, 0, "entry_list").map((e: any) => {
    const r = e?.report;
    if (!r || typeof r !== "object") throw new ErroFormato("anúncio sem report");
    return {
      item_id: idItem(e?.manual_product_ads?.item_id),
      nome: nome(e?.title),
      tipo: String(e?.type ?? ""),
      impressoes: inteiro(r.impression) ?? 0,
      cliques: inteiro(r.click) ?? 0,
      gasto: deCentMil(r.cost) ?? 0,
      vendas: deCentMil(r.broad_gmv) ?? 0,
      pedidos: inteiro(r.broad_order) ?? 0,
    };
  });
}

/** Soma uma página de `product/performance` (result.items) no acumulado da
 *  semana, por item. */
export function acumularVendas(acc: Map<string, VendaItem>, items: unknown): void {
  for (const x of lista(items, 0, "items") as any[]) {
    const id = exigir(idItem(x?.id), "id");
    const valor = reais(x?.paid_sales) ?? 0;
    const pedidos = inteiro(x?.paid_orders) ?? 0;
    const atual = acc.get(id);
    if (atual) {
      atual.valor += valor;
      atual.pedidos += pedidos;
      if (!atual.nome) atual.nome = nome(x?.name);
    } else {
      acc.set(id, { item_id: id, nome: nome(x?.name), valor, pedidos });
    }
  }
}

/** Fecha o acumulado: itens por valor (maior primeiro) + total da semana.
 *  `pedidos` do total é a soma por item (um pedido com 2 produtos conta 2). */
export function vendasDoAcumulado(acc: Map<string, VendaItem>): { totais: VendasTotais; itens: VendaItem[] } {
  const itens = [...acc.values()]
    .map((x) => ({ ...x, valor: centavos(x.valor) }))
    .sort((a, b) => b.valor - a.valor || a.item_id.localeCompare(b.item_id));
  const valor = centavos(itens.reduce((s, x) => s + x.valor, 0));
  const pedidos = itens.reduce((s, x) => s + x.pedidos, 0);
  return { totais: { valor, pedidos }, itens };
}

/** `wallet/get` (data) → saldo de Ads disponível em R$. */
export function saldoDaCarteira(data: any): number {
  return exigir(deCentMil(data?.ads_credit?.available), "ads_credit.available");
}

// ---------------------------------------------------------------------------
// Dia a dia de afiliados e de Ads (M-36 item 6, 24/11/2026)
//
// O Faturamento (Tabela de Preços) soma o investimento do mês; com a semana
// inteira só dava pra ratear a semana que cruza o mês pelos dias. As duas
// chamadas que a coleta JÁ faz trazem o dia a dia — o seller_daily (lista por
// dia) e o get_time_graph (blocos por hora) — e ele era descartado. Nenhuma
// chamada nova à Shopee. Regra de ouro: um dia só ganha número quando a soma
// dos dias fecha com o total da semana que a Conferência mostra; senão o lado
// inteiro da semana fica null e o Faturamento usa a semana (item 5).
// ---------------------------------------------------------------------------

/** Campos da comissão do dia nos itens do `data.list` do seller_daily, em
 *  ordem de preferência. Os totais da semana são `dis_total_*` (conferidos
 *  ao vivo); o nome do campo DO DIA não foi conferido — o do total sem o
 *  "total_", como `dis_actual_amount`/`order_count` dos dias. */
export const CAMPOS_COMISSAO_DIA = ["dis_seller_commission", "dis_total_seller_commission", "dis_estimated_seller_commission"];

/** Um lado (afiliados ou Ads) do dia a dia de uma semana: um valor por dia de
 *  `diasDaSemana`, e o motivo quando ficou tudo null por um problema (null
 *  sem motivo = a seção da semana já falhou e o aviso dela já existe). */
export interface LadoDoDia {
  valores: (number | null)[];
  motivo: string | null;
}

/** Número sem arredondar (string ou número); null se não for número. */
function numeroCru(v: unknown): number | null {
  if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
  const n = typeof v === "number" ? v : Number(String(v).replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

/** Nomes dos campos de um item (só os nomes, nunca os valores) pro motivo. */
function nomesDosCampos(x: unknown): string {
  return x && typeof x === "object" ? Object.keys(x).slice(0, 20).join(", ") : typeof x;
}

function brl2(n: number): string {
  return n.toFixed(2);
}

/** Reparte o total da semana (`alvo`, em R$ com 2 casas) pelos dias pra soma
 *  bater EXATAMENTE: cada dia arredondado ao centavo e a sobra de centavos
 *  vai pros dias com maior resto (dia zerado não ganha nem perde centavo).
 *  null se não dá (sobra maior que os dias com valor, ou um dia mudaria de
 *  sinal). */
export function repartirCentavos(valores: number[], alvo: number): number[] | null {
  const cents = valores.map((v) => Math.round(v * 100));
  let falta = Math.round(alvo * 100) - cents.reduce((a, b) => a + b, 0);
  const comValor = valores.map((_, i) => i).filter((i) => valores[i] !== 0);
  if (Math.abs(falta) > comValor.length) return null;
  const resto = (i: number) => valores[i] * 100 - cents[i];
  const ordem = comValor.sort((a, b) => (falta > 0 ? resto(b) - resto(a) : resto(a) - resto(b)) || a - b);
  for (let k = 0; falta !== 0; k++) {
    const passo = falta > 0 ? 1 : -1;
    cents[ordem[k]] += passo;
    falta -= passo;
  }
  if (cents.some((c, i) => (valores[i] > 0 && c < 0) || (valores[i] < 0 && c > 0))) return null;
  return cents.map((c) => c / 100);
}

/** Encaixa os valores crus no total: `null` nos dias desconhecidos fica null;
 *  o resto é repartido até somar o `alvo`. */
function encaixar(dias: string[], crus: (number | null)[], alvo: number, rotulo: string): LadoDoDia {
  const idx = crus.map((v, i) => (v === null ? -1 : i)).filter((i) => i >= 0);
  const rep = repartirCentavos(idx.map((i) => crus[i] as number), alvo);
  if (rep === null) return { valores: dias.map(() => null), motivo: `${rotulo}: não deu pra repartir os centavos da semana` };
  const valores: (number | null)[] = dias.map(() => null);
  idx.forEach((i, k) => (valores[i] = rep[k]));
  return { valores, motivo: null };
}

/** Comissão de afiliados de cada dia, do `data.list` do seller_daily que a
 *  coleta já pede (até 24/11 só servia pro último dia publicado). Dia sem
 *  linha até `publicadoAte` (o último dia que a Shopee já publicou) = 0 — a
 *  soma das linhas fechou com o total, então ele não tem comissão; depois
 *  dele = null (ainda não saiu, nunca 0 inventado). `alvo` = a comissão da
 *  semana que vai no payload (null = a seção falhou). */
export function afiliadosPorDia(sem: Semana, data: any, publicadoAte: string | null, alvo: number | null): LadoDoDia {
  const dias = diasDaSemana(sem);
  const nulos = (motivo: string | null): LadoDoDia => ({ valores: dias.map(() => null), motivo });
  if (alvo === null) return nulos(null);
  const total = numeroCru(data?.dis_total_seller_commission);
  if (total === null) return nulos("afiliados: sem o total da semana");
  if (!Array.isArray(data?.list)) return nulos("afiliados: a Shopee não mandou a lista por dia");
  const porDia = new Map<string, number>();
  for (const x of data.list) {
    const ymd = typeof x?.ymd === "string" ? x.ymd : "";
    if (!YMD.test(ymd) || ymd < sem.inicio || ymd > sem.fim) continue;
    const campo = CAMPOS_COMISSAO_DIA.find((c) => numeroCru(x?.[c]) !== null);
    if (!campo) return nulos(`afiliados: comissão do dia não reconhecida (campos: ${nomesDosCampos(x)})`);
    porDia.set(ymd, (porDia.get(ymd) ?? 0) + (numeroCru(x[campo]) as number));
  }
  const soma = [...porDia.values()].reduce((a, b) => a + b, 0);
  if (Math.abs(soma - total) > 0.011) {
    return nulos(`afiliados: a soma dos dias (R$ ${brl2(soma)}) não fecha com a semana (R$ ${brl2(total)})`);
  }
  const crus = dias.map((d) => porDia.get(d) ?? (publicadoAte !== null && d <= publicadoAte ? 0 : null));
  return encaixar(dias, crus, alvo, "afiliados");
}

/** Gasto de Ads de cada dia, somando os blocos do `report_by_time` que o
 *  get_time_graph da semana já devolve (a página manda só hora e custo de
 *  cada bloco). Só vale se TODO bloco tem hora (epoch, s ou ms) dentro da
 *  semana, em hora cheia ou quarto de hora, de no máximo 1 h em 1 h, e a
 *  soma fecha com o `report_aggregate.cost`. Dia sem bloco = 0 (Ads é em
 *  tempo real e a semana já fechou). `alvo` = o gasto da semana no payload. */
export function adsPorDia(sem: Semana, data: any, alvo: number | null): LadoDoDia {
  const dias = diasDaSemana(sem);
  const nulos = (motivo: string | null): LadoDoDia => ({ valores: dias.map(() => null), motivo });
  if (alvo === null) return nulos(null);
  const total = numeroCru(data?.report_aggregate?.cost);
  if (total === null) return nulos("Ads: sem o total da semana");
  // Semana sem gasto: todo dia é 0, venha o gráfico como vier.
  if (total === 0 && alvo === 0) return { valores: dias.map(() => 0), motivo: null };
  const blocos = data?.report_by_time;
  const campos = typeof data?.report_by_time__campos === "string" ? data.report_by_time__campos : "";
  if (!Array.isArray(blocos)) {
    const erro = typeof data?.report_by_time__erro === "string" ? ` (${data.report_by_time__erro})` : "";
    return nulos(`Ads: a Shopee não mandou o gráfico por hora${erro}`);
  }
  const n = Number(data?.report_by_time__n);
  if (Number.isFinite(n) && n > blocos.length) return nulos(`Ads: gráfico por hora cortado (${blocos.length} de ${n} blocos)`);
  const ini = epochInicioDia(sem.inicio);
  const fim = epochFimDia(sem.fim);
  const porDia = new Map<string, number>();
  const horas: number[] = [];
  for (const b of blocos) {
    const campoHora = CAMPOS_HORA_ADS.find((c) => numeroCru(b?.[c]) !== null);
    const campoCusto = CAMPOS_CUSTO_ADS.find((c) => numeroCru(b?.[c]) !== null);
    if (!campoHora || !campoCusto) {
      return nulos(`Ads: bloco por hora sem ${campoHora ? "custo" : "horário"} reconhecível (campos: ${campos || nomesDosCampos(b)})`);
    }
    let ts = numeroCru(b[campoHora]) as number;
    if (ts > 1e11) ts = Math.floor(ts / 1000); // milissegundos
    if (ts < ini || ts > fim || (ts - ini) % 900 !== 0) {
      return nulos(`Ads: horário do bloco fora da semana ou fora do quarto de hora (${campoHora}=${String(b[campoHora]).slice(0, 20)})`);
    }
    horas.push(ts);
    const dia = hojeBRT(new Date(ts * 1000));
    porDia.set(dia, (porDia.get(dia) ?? 0) + (numeroCru(b[campoCusto]) as number));
  }
  // Blocos de 1 dia inteiro (ou de 3 h…) podiam atravessar a meia-noite de
  // Brasília: só aceita gráfico de 1 h em 1 h ou mais fino.
  const distintas = [...new Set(horas)].sort((a, b) => a - b);
  let passo = Infinity;
  for (let i = 1; i < distintas.length; i++) passo = Math.min(passo, distintas[i] - distintas[i - 1]);
  if (!(passo <= 3600 && 3600 % passo === 0)) return nulos(`Ads: o gráfico não é por hora (${distintas.length} horários)`);
  const soma = [...porDia.values()].reduce((a, b) => a + b, 0);
  if (Math.abs(soma - total) > 1000) {
    return nulos(`Ads: a soma das horas (R$ ${brl2(soma / 100_000)}) não fecha com a semana (R$ ${brl2(total / 100_000)})`);
  }
  return encaixar(dias, dias.map((d) => (porDia.get(d) ?? 0) / 100_000), alvo, "Ads");
}

/** Os `dias` de uma semana já coletada. Nunca lança: o dia a dia não pode
 *  mexer no que a coleta já tem (o pior caso é tudo null com o motivo). */
export function diasDaColeta(
  sem: Semana,
  p: { daily: any; publicadoAte: string | null; alvoAfiliados: number | null; graficoAds: any; alvoAds: number | null }
): { dias: DiaDados[]; motivos: string[] } {
  const ds = diasDaSemana(sem);
  let af: LadoDoDia;
  let ad: LadoDoDia;
  try {
    af = afiliadosPorDia(sem, p.daily, p.publicadoAte, p.alvoAfiliados);
  } catch (e: any) {
    af = { valores: ds.map(() => null), motivo: `afiliados: ${String(e?.message || e).slice(0, 120)}` };
  }
  try {
    ad = adsPorDia(sem, p.graficoAds, p.alvoAds);
  } catch (e: any) {
    ad = { valores: ds.map(() => null), motivo: `Ads: ${String(e?.message || e).slice(0, 120)}` };
  }
  return {
    dias: ds.map((dia, i) => ({ dia, afiliados: af.valores[i], ads: ad.valores[i] })),
    motivos: [af.motivo, ad.motivo].filter((m): m is string => !!m).map((m) => m.slice(0, 300)),
  };
}

// ---------------------------------------------------------------------------
// Paginação
// ---------------------------------------------------------------------------

/** Pede mais uma página? `cobertos` = linhas já cobertas (coletadas, ou
 *  offset+limit no homepage/query); para quando cobriu o total, quando a
 *  página veio vazia (proteção contra total errado) ou no teto de páginas. */
export function continuarPaginando(
  cobertos: number,
  total: number,
  paginasFeitas: number,
  maxPaginas: number,
  vieramNaUltima: number
): boolean {
  return cobertos < total && vieramNaUltima > 0 && paginasFeitas < maxPaginas;
}

// ---------------------------------------------------------------------------
// Chamadas (o que roda DENTRO da página da Central do Vendedor)
// ---------------------------------------------------------------------------
export interface Req {
  path: string; // relativo a https://seller.shopee.com.br
  method: "GET" | "POST";
  body?: unknown;
  /** caminhos a apagar da resposta ANTES de sair da página (blocos pesados
   *  que não usamos; "*" = cada item da lista). */
  podar?: string[];
  /** /api/v2/login/: só username/shopid/shop_name saem da página (a resposta
   *  tem tokens — nunca vão pro log nem pro DaVinci). */
  login?: boolean;
  /** listas a ENXUGAR antes de sair da página: cada item vira um objeto só
   *  com os `campos` (caminho com ponto vira chave "a.b"), até `max` itens;
   *  ao lado ficam `<lista>__n` (quantos vieram) e `<lista>__campos` (os
   *  NOMES dos campos do 1º item, pra quem for conferir o formato). Roda
   *  antes da poda; se der erro, a lista é apagada como antes. */
  enxugar?: { lista: string; campos: string[]; max: number }[];
}

const QS_AFILIADOS = "is_real_time=0&order_type=2&channel=0";

export const REQ_LOGIN: Req = { path: "/api/v2/login/", method: "GET", login: true };

export const REQ_CARTEIRA: Req = {
  path: "/api/pas/v1/wallet/get/",
  method: "POST",
  body: { info_type_list: ["ads_credit"] },
};

export function reqSellerDaily(s: Semana): Req {
  return {
    path:
      `/api/v3/affiliateplatform/dashboard/seller_daily?start_time=${epochInicioDia(s.inicio)}` +
      `&end_time=${epochFimDia(s.fim)}&${QS_AFILIADOS}`,
    method: "GET",
  };
}

export const PAGINA_AFILIADOS = 20; // a Shopee corta em 20 mesmo pedindo mais
export const MAX_PAGINAS_AFILIADOS = 25;

export function reqItensAfiliados(s: Semana, pagina: number): Req {
  return {
    path:
      `/api/v3/affiliateplatform/dashboard/seller_item_detail?start_time=${epochInicioDia(s.inicio)}` +
      `&end_time=${epochFimDia(s.fim)}&${QS_AFILIADOS}&page_num=${pagina}&page_size=${PAGINA_AFILIADOS}&sort_rule=1`,
    method: "GET",
    podar: ["data.list.*.content_info"],
  };
}

/** Campos de cada bloco do `report_by_time` do get_time_graph, em ordem de
 *  preferência (M-36 item 6, 24/11/2026). Até 24/11 a coleta jogava o bloco
 *  fora na página sem ler e o formato dele nunca foi conferido ao vivo: por
 *  isso vários nomes possíveis, e o dia só vale se as horas caem dentro da
 *  semana e a soma fecha com o `report_aggregate.cost` (adsPorDia). */
export const CAMPOS_HORA_ADS = ["timestamp", "key", "time", "start_time", "ts", "datetime", "date"];
export const CAMPOS_CUSTO_ADS = ["cost", "metrics.cost", "report.cost", "data.cost", "stats.cost", "value.cost"];
/** 96 blocos por dia × 7 = 672 numa semana; folga pra semana com mais blocos. */
export const MAX_BLOCOS_ADS = 3000;

export function reqAdsAgregado(s: Semana): Req {
  return {
    path: "/api/pas/v1/report/get_time_graph/",
    method: "POST",
    body: {
      agg_interval: 1,
      campaign_type: "product_homepage_v2",
      start_time: epochInicioDia(s.inicio),
      end_time: epochFimDia(s.fim),
      need_roi_target_setting: false,
      filter_params: { campaign_type: "new_cpc_homepage" },
    },
    // 96 blocos por dia (~900 KB na semana): o total sai do agregado; dos
    // blocos só sobem a hora e o custo (o gasto de Ads de cada dia, M-36
    // item 6, 24/11/2026) — uns 30 KB na semana.
    enxugar: [
      { lista: "data.report_by_time", campos: [...CAMPOS_HORA_ADS, ...CAMPOS_CUSTO_ADS], max: MAX_BLOCOS_ADS },
    ],
  };
}

export const PAGINA_ADS = 50;
export const MAX_PAGINAS_ADS = 10;

export function reqAdsAnuncios(s: Semana, offset: number): Req {
  return {
    path: "/api/pas/v1/homepage/query/",
    method: "POST",
    body: {
      start_time: epochInicioDia(s.inicio),
      end_time: epochFimDia(s.fim),
      filter_list: [
        { campaign_type: "product_homepage_v3", state: "all", search_term: "", is_valid_rebate_only: false },
      ],
      offset,
      limit: PAGINA_ADS,
      use_paid_gmv: false,
    },
    podar: ["data.entry_list.*.ratio", "data.entry_list.*.trait_data", "data.entry_list.*.campaign"],
  };
}

export const PAGINA_VENDAS = 50;
export const MAX_PAGINAS_VENDAS = 10;

/** Vendas pagas por produto de UM dia (fim = 00:00 do dia seguinte). */
export function reqVendasDia(ymd: string, pagina: number): Req {
  const ini = epochInicioDia(ymd);
  return {
    path:
      `/api/mydata/v4/product/performance/?period=day&start_time=${ini}&end_time=${ini + DIA_S}` +
      `&category_type=shopee&category_id=-1&page_size=${PAGINA_VENDAS}&page_num=${pagina}` +
      `&order_type=paid&order_by=paid_sales.desc`,
    method: "GET",
    podar: ["result.items.*.models"],
  };
}

/** JS (STRING — o tsx põe __name nas funções e isso quebra no browser) que
 *  faz UMA chamada de dentro da página logada: cookies da própria sessão,
 *  SPC_CDS na URL e x-csrftoken no POST. Devolve {status, redirected, url,
 *  body|raw} ou {status: -1, erro}. Do /api/v2/login/ só saem username,
 *  shopid, shop_name e os campos de ERRO curtos (código e mensagem: é o que
 *  diz "captcha"/"login" pro classificarResposta) — token nunca. */
export function scriptChamada(req: Req, timeoutMs: number): string {
  const arg = {
    path: req.path,
    method: req.method,
    body: req.body === undefined ? null : req.body,
    podar: req.podar || [],
    enxugar: req.enxugar || [],
    login: req.login === true,
    timeout: timeoutMs,
  };
  return `(async (a) => {
  const ck = {};
  for (const par of String(document.cookie || '').split('; ')) {
    const i = par.indexOf('=');
    if (i > 0) ck[par.slice(0, i)] = par.slice(i + 1);
  }
  const url = a.path + (a.path.indexOf('?') >= 0 ? '&' : '?') +
    'SPC_CDS=' + encodeURIComponent(ck.SPC_CDS || '') + '&SPC_CDS_VER=2';
  const h = {};
  if (a.method === 'POST') { h['content-type'] = 'application/json'; h['x-csrftoken'] = ck.csrftoken || ''; }
  const podar = (o, p) => {
    if (!o || typeof o !== 'object' || !p.length) return;
    const k = p[0], resto = p.slice(1);
    if (k === '*') { if (Array.isArray(o)) for (const x of o) podar(x, resto); return; }
    if (!resto.length) { delete o[k]; return; }
    podar(o[k], resto);
  };
  const pegar = (o, c) => {
    let x = o;
    for (const k of c.split('.')) { if (!x || typeof x !== 'object') return undefined; x = x[k]; }
    return x;
  };
  const enxugar = (o, e) => {
    const p = e.lista.split('.');
    let pai = o;
    for (const k of p.slice(0, -1)) pai = pai && typeof pai === 'object' ? pai[k] : undefined;
    const ult = p[p.length - 1];
    if (!pai || typeof pai !== 'object' || !Array.isArray(pai[ult])) return;
    try {
      const l = pai[ult];
      const prim = l.find((x) => x && typeof x === 'object');
      const nomes = prim ? Object.keys(prim).slice(0, 25).map((k) => (prim[k] && typeof prim[k] === 'object' && !Array.isArray(prim[k]))
        ? k + '{' + Object.keys(prim[k]).slice(0, 25).join(',') + '}' : k) : [];
      const curta = l.slice(0, e.max).map((x) => {
        const r = {};
        for (const c of e.campos) {
          const v = pegar(x, c);
          if (typeof v === 'number') r[c] = v;
          else if (typeof v === 'string') r[c] = v.slice(0, 40);
        }
        return r;
      });
      pai[ult] = curta;
      pai[ult + '__n'] = l.length;
      pai[ult + '__campos'] = nomes.join(', ').slice(0, 600);
    } catch (err) {
      delete pai[ult];
      pai[ult + '__erro'] = String((err && err.message) || err).slice(0, 120);
    }
  };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), a.timeout);
  try {
    const r = await fetch(url, { method: a.method, headers: h, credentials: 'include',
      body: a.body === null ? undefined : JSON.stringify(a.body), signal: ctl.signal });
    const txt = await r.text();
    let body = null;
    try { body = JSON.parse(txt); } catch (e) { body = null; }
    if (body && typeof body === 'object') {
      if (a.login) {
        const d = body.data && body.data.username ? body.data : body;
        const erro = {};
        for (const k of ['code', 'err_code', 'errcode', 'error', 'msg', 'message', 'err_msg', 'error_msg', 'errmsg']) {
          const v = body[k];
          if (typeof v === 'number' || (typeof v === 'string' && v)) erro[k] = typeof v === 'string' ? v.slice(0, 120) : v;
        }
        body = Object.assign({ username: d.username || null, shopid: d.shopid == null ? null : d.shopid,
                 shop_name: d.shop_name == null ? null : d.shop_name }, erro);
      } else {
        for (const e of a.enxugar) enxugar(body, e);
        for (const p of a.podar) podar(body, p.split('.'));
      }
    }
    return { status: r.status, redirected: r.redirected, url: r.url, body: body,
             raw: body === null && !a.login ? txt.slice(0, 300) : null };
  } catch (e) {
    return { status: -1, erro: String((e && e.message) || e).slice(0, 200) };
  } finally {
    clearTimeout(timer);
  }
})(${JSON.stringify(arg)})`;
}

/** O que o scriptChamada devolve. */
export interface RespostaPagina {
  status: number;
  redirected?: boolean;
  url?: string;
  body?: any;
  raw?: string | null;
  erro?: string;
}

export type Classificacao =
  | { tipo: "ok" }
  | { tipo: "bloqueada" | "deslogada" | "falha"; detalhe: string };

// Mensagem de erro (JSON) que pede verificação/login.
const RX_BLOQUEIO = /captcha|verif/i;
const RX_LOGIN = /signin|sign_in|log ?in|not logged|unauthori|sess[aã]o expirada|session expired/i;
// Corpo que nem é JSON: só conta como bloqueio/login com sinal forte (HTML
// comum tem "verify"/"robots" à toa).
const RX_BLOQUEIO_HTML = /captcha|anti[-_ ]?bot|traffic[-_ ]?verif/i;
const RX_LOGIN_HTML = /account\/signin|accounts\.shopee/i;

/** Decide o que fazer com uma resposta (contrato §8): 403/429 ou captcha =
 *  `bloqueada`; login/redirecionamento pro signin = `deslogada` (as duas
 *  PARAM a loja); qualquer outra falha = só aquela seção fica null. */
export function classificarResposta(r: RespostaPagina): Classificacao {
  if (r.status === -1) return { tipo: "falha", detalhe: `sem resposta (${r.erro || "erro de rede"})` };
  if (r.status === 403 || r.status === 429) return { tipo: "bloqueada", detalhe: `a Shopee recusou (HTTP ${r.status})` };
  if (r.status === 401) return { tipo: "deslogada", detalhe: "a Shopee pediu login (HTTP 401)" };
  if (r.redirected && /\/account\/signin|accounts\.shopee|\/login/i.test(r.url || "")) {
    return { tipo: "deslogada", detalhe: "a Shopee mandou pro login" };
  }
  const body = r.body;
  if (body === null || body === undefined || typeof body !== "object") {
    const raw = String(r.raw || "");
    if (RX_BLOQUEIO_HTML.test(raw)) return { tipo: "bloqueada", detalhe: "a Shopee mostrou verificação/captcha" };
    if (RX_LOGIN_HTML.test(raw)) return { tipo: "deslogada", detalhe: "a Shopee devolveu a tela de login" };
    return { tipo: "falha", detalhe: r.status >= 400 ? `HTTP ${r.status}` : "resposta não é JSON" };
  }
  const codigo = body.code ?? body.err_code ?? body.errcode ?? body.error;
  const msg = [body.msg, body.message, body.err_msg, body.error_msg, body.errmsg]
    .filter((x) => typeof x === "string" && x)
    .join(" ")
    .slice(0, 200);
  const temErro = codigo !== undefined && codigo !== null && codigo !== 0 && codigo !== "0" && codigo !== "";
  if (temErro || r.status >= 400) {
    const texto = `${typeof codigo === "string" ? codigo : ""} ${msg}`;
    if (RX_BLOQUEIO.test(texto)) return { tipo: "bloqueada", detalhe: `a Shopee pediu verificação (${msg || codigo})` };
    if (RX_LOGIN.test(texto)) return { tipo: "deslogada", detalhe: `a Shopee pediu login (${msg || codigo})` };
    const oque = temErro ? `code ${codigo}${msg ? ` ${msg}` : ""}` : `HTTP ${r.status}`;
    return { tipo: "falha", detalhe: oque.slice(0, 200) };
  }
  return { tipo: "ok" };
}

// ---------------------------------------------------------------------------
// URLs da tela
// ---------------------------------------------------------------------------
function hostPath(raw: string): { host: string; path: string } | null {
  try {
    const u = new URL(raw);
    return { host: u.hostname.toLowerCase(), path: u.pathname.toLowerCase() };
  } catch {
    return null;
  }
}

/** A aba está DENTRO da Central do Vendedor (e não no login/verificação)?
 *  Só aí as chamadas relativas /api/... vão pro lugar certo. Olha host+path,
 *  nunca a query (o ?next=<portal> do login daria falso positivo). */
export function naCentral(url: string): boolean {
  const u = hostPath(url);
  if (!u || u.host !== "seller.shopee.com.br") return false;
  return !/\/account\/signin|\/login|\/signin|\/verify|captcha/.test(u.path);
}

/** Host da URL em minúsculas ("" se não for URL). */
export function hostDaUrl(url: string): string {
  return hostPath(url)?.host ?? "";
}

/** Hosts da Shopee onde a tela de login pode estar (o clique em "Entrar"
 *  só acontece num deles). */
export const HOSTS_LOGIN_SHOPEE = new Set(["accounts.shopee.com.br", "seller.shopee.com.br", "shopee.com.br"]);

/** A aba está numa tela da própria Shopee (https) onde dá pra clicar em
 *  "Entrar"? Qualquer outro host (portal do proxy, página de erro do SSO) →
 *  não clica: o formulário levaria o usuário e a senha do perfil. */
export function telaDeLoginDaShopee(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "https:" && HOSTS_LOGIN_SHOPEE.has(u.hostname.toLowerCase());
  } catch {
    return false;
  }
}

/** A tela pede código/OTP/captcha/verificação? (nunca resolvemos isso). */
export function urlPedeVerificacao(url: string): boolean {
  const u = hostPath(url);
  if (!u) return false;
  return /verif|captcha|challenge|2fa|security-check|(^|[/_-])otp([/_-]|$)/.test(`${u.host}${u.path}`);
}

/** Lê a tela de login (STRING pro page.evaluate). Rola o botão Entrar pra
 *  vista e devolve o centro dele + se é ELE que está no topo daquele ponto
 *  (nada cobrindo), sem clicar e sem digitar nada. */
export const SCRIPT_FORM_LOGIN = `(() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const cheio = (el) => { try { if (el.matches(':-webkit-autofill')) return true; } catch (e) {}
    return String(el.value || '').length > 0; };
  const senhas = Array.from(document.querySelectorAll('input[type="password"]')).filter(vis);
  const caixa = senhas[0] ? (senhas[0].closest('form') || document) : document;
  let usuarios = Array.from(caixa.querySelectorAll('input[name="loginKey"]')).filter(vis);
  if (!usuarios.length) usuarios = Array.from(caixa.querySelectorAll(
    'input[type="text"], input[type="email"], input[type="tel"], input:not([type])')).filter(vis);
  const texto = String((document.body && document.body.innerText) || '').slice(0, 6000);
  const pedeCodigo = /c[oó]digo de verifica|verifica[cç][aã]o|captcha|insira o c[oó]digo|n[aã]o sou um rob|verification code|\\bOTP\\b/i.test(texto);
  const entrar = (raiz) => Array.from(raiz.querySelectorAll('button, [role="button"]')).filter(vis)
    .filter((b) => /^(entrar|log ?in)$/i.test(String(b.innerText || b.textContent || '').trim()));
  let botoes = caixa !== document ? entrar(caixa) : [];
  if (!botoes.length) botoes = entrar(document);
  let botao = null;
  if (botoes.length === 1) {
    const b = botoes[0];
    b.scrollIntoView({ block: 'center', inline: 'center' });
    const r = b.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const topo = document.elementFromPoint(x, y);
    botao = { x: x, y: y, livre: !!topo && (topo === b || b.contains(topo)),
      desabilitado: !!b.disabled || b.getAttribute('aria-disabled') === 'true' || /disabled/i.test(String(b.className || '')) };
  }
  return { senha: senhas.length, senhaCheia: senhas.some(cheio), usuario: usuarios.length,
    usuarioCheio: usuarios.some(cheio), pedeCodigo: pedeCodigo, botoes: botoes.length, botao: botao };
})()`;

export interface FormLogin {
  senha: number;
  senhaCheia: boolean;
  usuario: number;
  usuarioCheio: boolean;
  pedeCodigo: boolean;
  botoes: number;
  botao: { x: number; y: number; livre: boolean; desabilitado: boolean } | null;
}

/** A página (depois do Entrar) está pedindo código/verificação? */
export const SCRIPT_PEDE_CODIGO = `(() => /c[oó]digo de verifica|verifica[cç][aã]o|captcha|insira o c[oó]digo|n[aã]o sou um rob|verification code|\\bOTP\\b/i
  .test(String((document.body && document.body.innerText) || '').slice(0, 6000)))()`;

// ---------------------------------------------------------------------------
// Ritmo e relógio
// ---------------------------------------------------------------------------

/** Pausa entre chamadas: 1,2–1,8 s, aleatória. */
export function pausaAleatoria(rnd: () => number = Math.random): number {
  return Math.round(1200 + rnd() * 600);
}

/** Salto de relógio entre dois passos além da pausa planejada — o Mac
 *  dormiu no meio da loja e o que já foi lido não é confiável. */
export const LIMITE_SALTO_MS = 120_000;
export function relogioPulou(planejadoMs: number, decorridoMs: number, limiteMs = LIMITE_SALTO_MS): boolean {
  return decorridoMs - planejadoMs > limiteMs;
}

// ---------------------------------------------------------------------------
// Payload final
// ---------------------------------------------------------------------------
export function semanaVazia(s: Semana): SemanaDados {
  return {
    inicio: s.inicio,
    fim: s.fim,
    afiliados: null,
    afiliados_itens: null,
    ads: null,
    ads_itens: null,
    vendas: null,
    vendas_itens: null,
    avisos: [],
    dias: null,
    dias_motivos: [],
  };
}

export function montarDados(p: {
  inicioMs: number;
  fimMs: number;
  chamadas: number;
  login: LoginLoja;
  loginAutoUsado: boolean;
  saldo: number | null;
  ultimoDia: string | null;
  semanas: SemanaDados[];
  avisos: string[];
}): ColetaDados {
  return {
    versao: 1,
    coletado_em: isoUtc(p.fimMs),
    duracao_s: Math.round((p.fimMs - p.inicioMs) / 100) / 10,
    chamadas: p.chamadas,
    login: p.login,
    login_auto_usado: p.loginAutoUsado,
    saldo_ads: p.saldo,
    afiliados_ultimo_dia: p.ultimoDia,
    semanas: p.semanas,
    avisos: p.avisos,
  };
}

/** ok = todas as seções; parcial = alguma ficou null (ver avisos); erro =
 *  nenhuma veio. */
export function statusDaColeta(d: ColetaDados): { status: "ok" | "parcial" | "erro"; erro: string | null } {
  const secoes: unknown[] = [d.saldo_ads];
  for (const s of d.semanas) {
    secoes.push(s.afiliados, s.afiliados_itens, s.ads, s.ads_itens, s.vendas, s.vendas_itens);
  }
  const nulas = secoes.filter((x) => x === null).length;
  if (nulas === 0) return { status: "ok", erro: null };
  if (nulas === secoes.length) {
    const primeiro = d.semanas.flatMap((s) => s.avisos)[0] || d.avisos[0] || "";
    return { status: "erro", erro: `nenhuma parte coletada${primeiro ? ` (${primeiro})` : ""}`.slice(0, 500) };
  }
  return { status: "parcial", erro: `${nulas} de ${secoes.length} partes sem dados — ver avisos` };
}

/** Confere o job antes de abrir qualquer perfil. null = ok. */
export function validarJob(job: {
  adspower_user_id?: unknown;
  semanas?: unknown;
  afiliados_ate?: unknown;
  esperar_afiliados_ate?: unknown;
  corte?: unknown;
}): string | null {
  if (!job.adspower_user_id || typeof job.adspower_user_id !== "string") return "job sem adspower_user_id";
  const sem = job.semanas as Semana[] | undefined;
  if (!Array.isArray(sem) || sem.length !== 4) return "job sem as 4 semanas";
  for (const s of sem) {
    if (!s || !YMD.test(String(s.inicio)) || !YMD.test(String(s.fim)) || s.inicio > s.fim) {
      return `semana inválida no job: ${JSON.stringify(s)}`;
    }
  }
  if (!YMD.test(String(job.afiliados_ate))) return "job sem afiliados_ate";
  if (!Number.isFinite(Date.parse(String(job.esperar_afiliados_ate)))) return "job sem esperar_afiliados_ate";
  if (!Number.isFinite(Date.parse(String(job.corte)))) return "job sem corte";
  return null;
}

// ---------------------------------------------------------------------------
// Resumo pro --teste-conferencia (texto, sem tokens)
// ---------------------------------------------------------------------------
function brl(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `R$ ${n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString("pt-BR");
}

export function resumoConferencia(status: string, erro: string | null, d: ColetaDados | null): string {
  const out: string[] = [`status: ${status}${erro ? ` — ${erro}` : ""}`];
  if (!d) return out.join("\n");
  out.push(`login: ${d.login.username} (shopid ${d.login.shopid ?? "?"}) · Entrar clicado: ${d.login_auto_usado ? "sim" : "não"}`);
  out.push(`${d.chamadas} chamadas em ${d.duracao_s} s · saldo Ads ${brl(d.saldo_ads)} · afiliados até ${d.afiliados_ultimo_dia ? ddmm(d.afiliados_ultimo_dia) : "—"}`);
  d.semanas.forEach((s, i) => {
    out.push(`S${i + 1} ${ddmm(s.inicio)}–${ddmm(s.fim)}`);
    out.push(
      `  afiliados: ${s.afiliados ? `${brl(s.afiliados.vendas)} · comissão ${brl(s.afiliados.comissao)} · ${num(s.afiliados.pedidos)} ped. · ${num(s.afiliados.cliques)} cliques` : "—"}` +
        ` (${s.afiliados_itens ? s.afiliados_itens.length : "—"} itens)`
    );
    out.push(
      `  Ads: ${s.ads ? `${brl(s.ads.vendas)} · gasto ${brl(s.ads.gasto)} · ${num(s.ads.impressoes)} impr. · ${num(s.ads.cliques)} cliques · ${num(s.ads.pedidos)} ped.` : "—"}` +
        ` (${s.ads_itens ? s.ads_itens.length : "—"} anúncios)`
    );
    out.push(
      `  vendas: ${s.vendas ? `${brl(s.vendas.valor)} · ${num(s.vendas.pedidos)} ped.` : "—"}` +
        ` (${s.vendas_itens ? s.vendas_itens.length : "—"} itens)`
    );
    // M-36 item 6 (24/11/2026): quantos dias vieram com número — é aqui que
    // se confere, no Mac, se o formato do dia a dia foi reconhecido.
    if (s.dias) {
      const com = (lado: "afiliados" | "ads") => s.dias!.filter((x) => x[lado] !== null).length;
      out.push(`  dia a dia: afiliados ${com("afiliados")}/${s.dias.length} dias · Ads ${com("ads")}/${s.dias.length} dias`);
    }
    for (const m of s.dias_motivos || []) out.push(`  dia a dia: ${m}`);
    for (const a of s.avisos) out.push(`  aviso: ${a}`);
  });
  for (const a of d.avisos) out.push(`aviso: ${a}`);
  return out.join("\n");
}
