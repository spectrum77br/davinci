import type { Page } from "puppeteer-core";
import type { Caso, Fala } from "./davinci";
import { LoginNecessario, type Item, type Leitura } from "./shopee_historico";

/**
 * Lê uma consulta do formulário de ajuda do Mercado Livre
 * (mercadolivre.com.br/cases/detail/<nº da consulta>).
 *
 * Nasceu no 298394 (30/09/2026): a parte de chamados do ML sai do computador do
 * Eduardo pro Mac Santiago. Lá a resposta do ML vinha pelo e-mail do Tuta; aqui
 * vem da página, que mostra a conversa inteira — inclusive o que o ML manda por
 * e-mail.
 *
 * O que a sonda de 30/09 mostrou na tela real (perfil "Forpaper - Mercado Livre"):
 *   - o link direto abre já logado; a página vem pronta do servidor (sem JSON
 *     à parte) e o estado da consulta está no HTML:
 *     "interaction":{"createdDate","closedDate","channel":"FORM","status":"closed"};
 *   - cada mensagem é um `[data-testid="message-card"]`: autor ("Você" ou
 *     "Mercado Livre") e data ("24 de setembro", SEM hora) no
 *     `.message-card__header`, texto em `[data-testid="message-card-text"]`,
 *     anexos como nome de arquivo;
 *   - o ML marca "Finalizou" ~15 min depois da 1ª resposta, mas a conversa
 *     continua (479763421: falas de 02/09 a 23/09) — finalizada NÃO é encerrado.
 *
 * SÓ LÊ: nenhum clique. "Retomar consulta" não é tocado.
 *
 * Como a tela só dá o dia, a fala vai com `so_dia: true` e o DaVinci reconhece a
 * repetida por (texto, dia) e grava a hora no fim do dia (ou a da leitura, hoje).
 *
 * Lógica de DOM como STRING pro page.evaluate (o tsx instrumenta funções com
 * __name e quebra dentro do browser) — mesmo padrão do shopee_historico.
 */

export const CONSULTA_URL = "https://www.mercadolivre.com.br/cases/detail/";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function evalJS<T = any>(page: Page, js: string): Promise<T | undefined> {
  return (page.evaluate(js) as Promise<T>).catch(() => undefined);
}

const MESES: Record<string, string> = {
  janeiro: "01", fevereiro: "02", "março": "03", marco: "03", abril: "04", maio: "05",
  junho: "06", julho: "07", agosto: "08", setembro: "09", outubro: "10", novembro: "11",
  dezembro: "12",
};

/** Hoje em São Paulo, "AAAA-MM-DD". */
function hojeSP(agora = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Sao_Paulo" }).format(agora);
}

/** "24 de setembro" / "2 de setembro de 2025" / "Hoje" / "Ontem" -> "AAAA-MM-DD".
 *  Sem ano: o ano de hoje, ou o anterior se a data cairia no futuro (dezembro
 *  lido em janeiro). */
export function diaDaTela(s: string | null, agora = new Date()): string | null {
  const t = (s || "").trim().toLowerCase();
  const hoje = hojeSP(agora);
  if (/^hoje\b/.test(t)) return hoje;
  if (/^ontem\b/.test(t)) return hojeSP(new Date(agora.getTime() - 24 * 3600 * 1000));
  const m = /(\d{1,2})\s+de\s+([a-zç]+)(?:\s+de\s+(\d{4}))?/.exec(t);
  if (!m || !MESES[m[2]]) return null;
  const dd = m[1].padStart(2, "0");
  const mm = MESES[m[2]];
  if (m[3]) return `${m[3]}-${mm}-${dd}`;
  const ano = Number(hoje.slice(0, 4));
  const iso = `${ano}-${mm}-${dd}`;
  return iso > hoje ? `${ano - 1}-${mm}-${dd}` : iso;
}

/** Fala DELES = tudo que não é "Você" (a loja). */
export function eFalaDoMl(i: Item): boolean {
  return !/^voc[êe]$/i.test(i.autor.trim()) && !!i.texto;
}

const JS_MENSAGENS = `(function(){
  function limpo(e){
    return String((e&&e.innerText)||'').replace(/\\u00a0/g,' ').split('\\n')
      .map(function(l){return l.trim();}).join('\\n').replace(/\\n{3,}/g,'\\n\\n').trim();
  }
  return [...document.querySelectorAll('[data-testid="message-card"]')].map(function(b){
    var cab=[...b.querySelectorAll('.message-card__header span')].map(function(s){return (s.textContent||'').trim();});
    var anexos=[...b.querySelectorAll('[title]')].map(function(e){return e.getAttribute('title')||'';})
      .filter(function(t){return /\\.[a-z0-9]{2,5}$/i.test(t);});
    return {autor:cab[0]||'',data:cab[1]||'',texto:limpo(b.querySelector('[data-testid="message-card-text"]')),anexos:anexos};
  });
})()`;

const JS_ESTADO = `(function(){
  var h=document.documentElement.innerHTML;
  var i=h.indexOf('"interaction":{');
  var obj=i>=0?h.slice(i,i+3000):'';
  function campo(n){var m=new RegExp('"'+n+'":"([^"]*)"').exec(obj);return m?m[1]:'';}
  return {
    status:campo('status'), criada:campo('createdDate'), fechada:campo('closedDate'),
    canal:campo('channel'), naoAchou:/"caseNotFound":true/.test(h),
    titulo:((document.body&&document.body.innerText)||'').indexOf('Consulta número')>=0
  };
})()`;

interface Bruto {
  autor: string;
  data: string;
  texto: string;
  anexos: string[];
}

interface Estado {
  status: string;
  criada: string;
  fechada: string;
  canal: string;
  naoAchou: boolean;
  titulo: boolean;
}

function situacao(e: Estado): string {
  if (e.status === "closed") return `Finalizada pelo ML${e.fechada ? ` em ${e.fechada}` : ""}`;
  return e.status ? `Aberta (${e.status})` : "(não li)";
}

function montarHistorico(consulta: string, e: Estado, brutos: Bruto[]): string {
  const linhas = brutos.map((b) => {
    const anexos = b.anexos.length ? `\n[anexos: ${b.anexos.join(", ")}]` : "";
    return `[${b.data || "sem data"}] ${b.autor || "?"}:\n${b.texto}${anexos}`;
  });
  return [
    `Consulta ${consulta} no Mercado Livre (formulário de ajuda)`,
    `Situação: ${situacao(e)}${e.criada ? ` — criada em ${e.criada}` : ""}`,
    `Conversa:\n\n${linhas.join("\n\n") || "(vazia)"}`,
  ].join("\n\n");
}

/** Lê UMA consulta. Lança erro quando não deu pra ler (quem chama manda ok:false). */
export async function ler(page: Page, caso: Caso, agora = new Date()): Promise<Leitura> {
  const consulta = (caso.chamado || "").trim();
  if (!/^\d{6,12}$/.test(consulta)) throw new Error(`"${consulta}" não é nº de consulta do ML`);
  const url = (caso.chamado_url || "").trim() || `${CONSULTA_URL}${consulta}`;
  await page.goto(url, { waitUntil: "networkidle2", timeout: 90000 }).catch(() => undefined);
  const senha = await evalJS<boolean>(page, `!!document.querySelector('input[type="password"]')`);
  if (/\/jms\/|\/login|registration|lgz/i.test(page.url()) || senha) {
    throw new LoginNecessario("login do Mercado Livre caiu — entrar no perfil e logar de novo");
  }
  let e: Estado | undefined;
  for (let i = 0; i < 30; i++) {
    e = await evalJS<Estado>(page, JS_ESTADO);
    if (e && (e.naoAchou || e.titulo)) break;
    await sleep(500);
  }
  if (!e || e.naoAchou) {
    throw new Error(
      `a consulta ${consulta} não aparece neste login do ML — foi aberta por outra conta da loja?`
    );
  }
  if (!e.titulo) throw new Error(`a consulta não carregou (${page.url()})`);
  const confere = await evalJS<boolean>(
    page,
    `(document.body.innerText||'').indexOf(${JSON.stringify(`Consulta número: ${consulta}`)})>=0`
  );
  if (!confere) throw new Error(`a página aberta não mostra a consulta ${consulta} (${page.url()})`);
  const brutos = (await evalJS<Bruto[]>(page, JS_MENSAGENS)) || [];
  if (!brutos.length) throw new Error(`a consulta ${consulta} abriu sem nenhuma mensagem`);
  const itens: Item[] = brutos.map((b) => ({
    chat: true,
    autor: b.autor || "?",
    texto: b.texto || "",
    quando: b.data || null,
  }));
  const falas: Fala[] = [];
  for (const i of itens.filter(eFalaDoMl)) {
    // Data que não entendi: vai com HOJE — perder a resposta é pior que datar
    // tarde (o dedupe por dia segura as releituras do mesmo dia).
    const dia = diaDaTela(i.quando, agora) || hojeSP(agora);
    falas.push({ texto: i.texto, quando: `${dia}T00:00:00-03:00`, autor: i.autor, so_dia: true });
  }
  return {
    url,
    situacao: situacao(e),
    itens,
    falas,
    historico: montarHistorico(consulta, e, brutos),
  };
}
