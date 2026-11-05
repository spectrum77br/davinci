import type { Page } from "puppeteer-core";
import type { Caso, Fala } from "./davinci";
import { LoginNecessario, type Item, type Leitura } from "./shopee_historico";

/**
 * Lê um caso do suporte ao vendedor da Amazon
 * (sellercentral.amazon.com.br/cu/case-dashboard/view-case?caseID=<nº>).
 *
 * Nasceu no 288967 (05/11/2026, caso 23057891781 da KFA): a resposta do suporte
 * só existia no Seller Central e no e-mail principal do caso, e o chamado não
 * ficava sabendo.
 *
 * O que a sonda de 05/11 mostrou no perfil "KFA - Amazon" (lista + 4 casos, sem
 * captcha e sem login):
 *   - a própria página busca o caso em `/hill/hillservice/mons-api/ViewCase`, e
 *     é ESSA resposta que lemos: hora exata, autor e o texto limpo, sem depender
 *     do desenho da tela;
 *   - `contactList` vem do mais novo pro mais velho, 10 por vez: a fala nova
 *     sempre cabe, só o começo de caso muito longo fica de fora;
 *   - a fala da Amazon é `outbound: true`; `hmdMetaData.beforeHMDContent` é o
 *     texto antes da pesquisa "Você ficou satisfeito";
 *   - chat é um contato CHAT com `outbound: false` e o nosso e-mail como
 *     remetente, com as falas em `messageList` (`sender.role` AGENT ou CUSTOMER).
 *     Só aparece depois de encerrado: durante o chat o caso vem vazio;
 *   - `Resolved` é "Respondidas" na tela e não fecha nada (o caso aceita resposta
 *     por 5 dias): nunca encerra o chamado.
 *
 * SÓ LÊ: nenhum clique, nenhum pedido feito por nós — a resposta é a que a página
 * já busca sozinha. "Responder" e "Encerrar este caso" não são tocados.
 */

export const CASO_URL = "https://sellercentral.amazon.com.br/cu/case-dashboard/view-case?caseID=";

const SP = "America/Sao_Paulo";

interface MensagemChat {
  type?: string | null;
  timestamp?: number | null;
  sender?: { displayName?: string | null; role?: string | null } | null;
  content?: { contentType?: string | null; data?: string | null } | null;
}

interface Contato {
  channelType?: string | null;
  outbound?: boolean | null;
  sender?: string | null;
  timestamp?: number | null;
  message?: string | null;
  messageTruncated?: boolean | null;
  messageList?: MensagemChat[] | null;
  attachments?: { fileName?: string | null }[] | null;
  hmdMetaData?: { beforeHMDContent?: string | null; afterHMDContent?: string | null } | null;
}

export interface CasoAmazon {
  contactList?: Contato[] | null;
  totalNumberOfContacts?: number | null;
  viewCaseMetaData?: {
    caseStatus?: string | null;
    caseTitle?: string | null;
    primaryEmail?: string | null;
    creationDate?: number | null;
  } | null;
}

const STATUS: Record<string, string> = {
  PendingAmazonAction: "Trabalho em andamento",
  Resolved: "Respondidas",
};

/** O que vem depois da fala, em toda resposta: a pesquisa e o rodapé. */
const RODAPE = [
  /Para nos ajudar a melhorar continuamente/i,
  /Você ficou satisfeito com o suporte/i,
  /Muito obrigado por vender com a Amazon/i,
  /={5,}/,
  /OUTRAS MANEIRAS PARA OBTER AJUDA/i,
];

function limpo(texto: string): string {
  return texto
    .replace(/ /g, " ")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function semRodape(texto: string): string {
  let fim = texto.length;
  for (const r of RODAPE) {
    const i = texto.search(r);
    if (i >= 0 && i < fim) fim = i;
  }
  return limpo(texto.slice(0, fim)) || limpo(texto);
}

function textoDoContato(c: Contato): string {
  const texto = semRodape(c.hmdMetaData?.beforeHMDContent || c.message || "");
  const anexos = (c.attachments ?? []).map((a) => a.fileName).filter(Boolean);
  return [
    texto,
    c.messageTruncated ? "[a Amazon cortou esta mensagem: o resto só no Seller Central]" : "",
    anexos.length ? `[anexos: ${anexos.join(", ")}]` : "",
  ]
    .filter(Boolean)
    .join("\n\n");
}

/** "Rafael C." e "[[Mohd Adil A.]]": a linha antes de "Suporte ao Vendedor". */
function atendente(c: Contato): string | null {
  const t = c.hmdMetaData?.afterHMDContent || c.message || "";
  const m = /([^\n]+)\n\s*Amazon\.com\.br Suporte ao Vendedor/.exec(t);
  const nome = (m?.[1] || "").replace(/[[\]]/g, "").trim();
  return nome && nome.length <= 60 ? nome : null;
}

function iso(segundos: number): string {
  return new Date(segundos * 1000).toISOString();
}

function dataHora(segundos: number | null | undefined): string {
  if (!segundos) return "sem data";
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: SP,
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(segundos * 1000));
}

function horaDoChat(segundos: number | null | undefined): string {
  if (!segundos) return "--:--";
  return new Intl.DateTimeFormat("pt-BR", { timeZone: SP, timeStyle: "short" }).format(
    new Date(segundos * 1000)
  );
}

function falasDoChat(c: Contato): MensagemChat[] {
  return (c.messageList ?? []).filter((m) => m.type === "MESSAGE" && m.content?.data?.trim());
}

function eDaAmazon(m: MensagemChat): boolean {
  return m.sender?.role === "AGENT";
}

function linhaDoChat(m: MensagemChat): string {
  const nome = eDaAmazon(m) ? m.sender?.displayName || "Amazon" : "Nós";
  return `${horaDoChat(m.timestamp)} ${nome}: ${limpo(m.content?.data || "")}`;
}

function eChat(c: Contato): boolean {
  return c.channelType === "CHAT";
}

/** A fala da Amazon neste contato, se houver. Do chat vão só as linhas dela (a
 *  conversa inteira fica no histórico), todas numa fala: 15 linhas soltas
 *  encheriam o chamado e acordariam a IA uma vez só de qualquer jeito. */
function falaDoContato(c: Contato): Fala | null {
  if (!c.timestamp) return null;
  if (eChat(c)) {
    const delas = falasDoChat(c).filter(eDaAmazon);
    if (!delas.length) return null;
    return {
      texto: `Chat com o suporte da Amazon:\n\n${delas.map(linhaDoChat).join("\n\n")}`,
      quando: iso(c.timestamp),
      autor: "Amazon (chat)",
    };
  }
  if (!c.outbound) return null;
  const texto = textoDoContato(c);
  if (!texto) return null;
  const nome = atendente(c);
  return { texto, quando: iso(c.timestamp), autor: nome ? `Amazon (${nome})` : "Amazon" };
}

function autorDoContato(c: Contato): string {
  if (eChat(c)) return "Chat";
  if (!c.outbound) return "Nós";
  const nome = atendente(c);
  return nome ? `Amazon (${nome})` : "Amazon";
}

function textoNoHistorico(c: Contato): string {
  if (eChat(c)) return falasDoChat(c).map(linhaDoChat).join("\n") || limpo(c.message || "");
  return textoDoContato(c);
}

/** Monta a leitura a partir da resposta da Amazon. Sem navegador: dá pra conferir
 *  com uma resposta guardada. */
export function montar(numero: string, url: string, dados: CasoAmazon): Leitura {
  const meta = dados.viewCaseMetaData;
  if (!meta) throw new Error(`o caso ${numero} não aparece neste login da Amazon`);
  const contatos = [...(dados.contactList ?? [])].sort(
    (a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0)
  );
  const situacao = STATUS[meta.caseStatus || ""] || meta.caseStatus || "(não li)";
  const itens: Item[] = contatos.map((c) => ({
    chat: true,
    autor: autorDoContato(c),
    texto: textoNoHistorico(c),
    quando: dataHora(c.timestamp),
  }));
  const falas = contatos.map(falaDoContato).filter((f): f is Fala => f !== null);
  const total = dados.totalNumberOfContacts ?? contatos.length;
  const conversa = itens.map((i) => `[${i.quando}] ${i.autor}:\n${i.texto}`).join("\n\n");
  const historico = [
    `Caso ${numero} na Amazon: ${meta.caseTitle || "(sem título)"}`,
    `Situação: ${situacao}`,
    `E-mail principal: ${meta.primaryEmail || "(não li)"}. Criado em ${dataHora(meta.creationDate)}`,
    total > contatos.length ? `Mostrando as ${contatos.length} mais recentes de ${total}.` : "",
    `Conversa:\n\n${conversa || "(nenhuma atualização no caso)"}`,
  ]
    .filter(Boolean)
    .join("\n\n");
  return { url, situacao, itens, falas, historico };
}

async function telaDeLogin(page: Page): Promise<boolean> {
  if (/\/ap\/(signin|mfa|cvf)|\/signin/i.test(page.url())) return true;
  return page
    .evaluate(`!!document.querySelector('input[type="password"]')`)
    .then(Boolean)
    .catch(() => false);
}

/** Lê UM caso. Lança erro quando não deu pra ler (quem chama manda ok:false). */
export async function ler(page: Page, caso: Caso): Promise<Leitura> {
  const numero = (caso.chamado || "").trim();
  if (!/^\d{9,14}$/.test(numero)) throw new Error(`"${numero}" não é nº de caso da Amazon`);
  const url = (caso.chamado_url || "").trim() || `${CASO_URL}${numero}`;
  const chegou = page
    .waitForResponse(
      (r) => r.url().includes("/mons-api/ViewCase") && r.url().includes(`caseId=${numero}`),
      { timeout: 60000 }
    )
    .catch(() => null);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 90000 }).catch(() => undefined);
  const caiu = new LoginNecessario("login do Seller Central caiu — entrar no perfil e logar de novo");
  if (await telaDeLogin(page)) throw caiu;
  const r = await chegou;
  if (!r) {
    if (await telaDeLogin(page)) throw caiu;
    throw new Error(`o caso ${numero} não carregou (${page.url()})`);
  }
  if (!r.ok()) throw new Error(`a Amazon não mostrou o caso ${numero} (HTTP ${r.status()})`);
  return montar(numero, url, (await r.json()) as CasoAmazon);
}
