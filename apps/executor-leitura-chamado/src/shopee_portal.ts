import type { Page } from "puppeteer-core";
import type { Caso, Fala } from "./davinci";
import { LoginNecessario, type Item, type Leitura } from "./shopee_historico";

/**
 * Lê uma consulta do Portal de Atendimento ao Vendedor da Shopee
 * (seller-service.cs.shopee.com.br/detail/<ID da consulta>).
 *
 * Nasceu no 292592 (25/09/2026): o robô abriu o chamado NA TELA, pelo Portal, e
 * o Agente Shopee respondeu lá em 19/09 e em 23/09 ("solicitação de compensação
 * em análise… até 15 dias úteis"). Nem a API nem a leitura do Seller Center
 * chegavam nessa página, e o chamado ficou "Aguard. Plataforma".
 *
 * O que a sonda de 25/09 mostrou na tela real (perfil Marquezini Shopee):
 *   - o link direto abre já logado (mesmo login do Seller Center);
 *   - "Histórico de conversas" mostra só a primeira e a última mensagem; as do
 *     meio ficam atrás de "Ver N mais conversas" (expande a lista, não envia);
 *   - cada mensagem é um `[class*="message-content___"]` com autor
 *     (`user-name___`), hora "DD/MM/AAAA HH:MM" (`span.time___`), canal
 *     ("Através do rastreador de caso") e o texto do agente DENTRO de
 *     `<shadow-html data-html="…">` — o innerText da página não enxerga;
 *   - "Status da consulta" + "Progresso do Processamento" à direita.
 *
 * SÓ LÊ. O único clique é "Ver N mais conversas". "Caso concluído" NÃO vira
 * encerrado: é a consulta de suporte que fechou, não a decisão do caso (no
 * 292592 a compensação seguia em análise).
 *
 * Lógica de DOM como STRING pro page.evaluate (o tsx instrumenta funções com
 * __name e quebra dentro do browser) — mesmo padrão do shopee_historico.
 */

export const PORTAL_URL = "https://seller-service.cs.shopee.com.br/detail/";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function evalJS<T = any>(page: Page, js: string): Promise<T | undefined> {
  return (page.evaluate(js) as Promise<T>).catch(() => undefined);
}

/** "23/09/2026 10:27" -> "2026-09-23T10:27:00-03:00" (hora de São Paulo). */
export function isoDoPortal(s: string | null): string | null {
  const m = /(\d{2})\/(\d{2})\/(\d{4})\s+(\d{2}):(\d{2})/.exec(s || "");
  if (!m) return null;
  return `${m[3]}-${m[2]}-${m[1]}T${m[4]}:${m[5]}:00-03:00`;
}

/** Fala DELES = mensagem do Agente Shopee (a nossa sai com o login da loja). */
export function eFalaDoAgente(i: Item): boolean {
  return /shopee/i.test(i.autor) && !!i.texto && !!i.quando;
}

const JS_EXPANDIR = `(function(){
  var el=[...document.querySelectorAll('[class*="split-text___"]')].find(function(e){return /mais conversas/i.test(e.textContent||'');});
  if(!el) return false; el.click(); return true;
})()`;

const JS_MENSAGENS = `(function(){
  function texto(html){
    var h=String(html||'').replace(/<br\\s*\\/?>/gi,'\\n').replace(/<\\/(p|div|li)>/gi,'\\n');
    var d=document.createElement('div'); d.innerHTML=h;
    return (d.textContent||'').replace(/\\u00a0/g,' ').split('\\n').map(function(l){return l.trim();})
      .join('\\n').replace(/\\n{3,}/g,'\\n\\n').trim();
  }
  return [...document.querySelectorAll('[class*="message-content___"]')].map(function(b){
    function q(sel){var e=b.querySelector(sel);return e?(e.textContent||'').trim():'';}
    var sh=b.querySelector('shadow-html');
    var corpo=sh?texto(sh.getAttribute('data-html')):(q('[class*="summary___"]')||q('[class*="reply-content___"]').replace(/(Ver tudo|Recolher)$/,'').trim());
    return {autor:q('[class*="user-name___"]'),quando:q('span[class^="time___"]'),canal:q('[class*="channel___"]'),texto:corpo};
  });
})()`;

const JS_STATUS = `(function(){
  var t=document.body?document.body.innerText:'';
  var m=/Status da consulta\\s*\\n?\\s*([^\\n]+)/.exec(t);
  var i=t.indexOf('Progresso do Processamento');
  return {status:m?m[1].trim():'', progresso:i>=0?t.slice(i+'Progresso do Processamento'.length).trim():''};
})()`;

function montarHistorico(consulta: string, status: string, progresso: string, itens: Item[]): string {
  const linhas = itens.map((i) => `[${i.quando || "sem hora"}] ${i.autor}:\n${i.texto}`);
  return [
    `Consulta ${consulta} no Portal de Atendimento ao Vendedor da Shopee`,
    `Status da consulta: ${status || "(não li)"}`,
    `Progresso do Processamento:\n${progresso.replace(/\n{2,}/g, "\n") || "(não li)"}`,
    `Histórico de conversas:\n\n${linhas.join("\n\n") || "(vazio)"}`,
  ].join("\n\n");
}

/** Lê UMA consulta. Lança erro quando não deu pra ler (quem chama manda ok:false). */
export async function ler(page: Page, caso: Caso): Promise<Leitura> {
  // consulta ligada ao chamado (294571) ou o próprio protocolo (aberto na tela, 292592)
  const consulta = (caso.consulta_portal || caso.chamado || "").trim();
  if (!/^\d{15,}$/.test(consulta)) throw new Error(`"${consulta}" não é ID de consulta do Portal`);
  const url =
    (caso.consulta_url || "").trim() ||
    (caso.consulta_portal ? "" : (caso.chamado_url || "").trim()) ||
    `${PORTAL_URL}${consulta}`;
  await page.goto(url, { waitUntil: "networkidle2", timeout: 90000 }).catch(() => undefined);
  const senha = await evalJS<boolean>(page, `!!document.querySelector('input[type="password"]')`);
  if (/signin|login|\/account\//i.test(page.url()) || senha) throw new LoginNecessario();
  let pronta = false;
  for (let i = 0; i < 30 && !pronta; i++) {
    await sleep(500);
    pronta = !!(await evalJS<boolean>(
      page,
      `/Detalhes da consulta|ID da Consulta/i.test(document.body.innerText||'')&&document.querySelectorAll('[class*="message-content___"]').length>0`
    ));
  }
  if (!pronta) {
    // 25/09 (294571): a página abre logada mas VAZIA quando a consulta foi aberta
    // com outro login da loja — o Portal só mostra a consulta pra quem abriu.
    const login = await evalJS<string>(
      page,
      `(function(){var e=document.querySelector('[class*="user__info"]');return e?(e.innerText||'').trim():'';})()`
    );
    const abriu = await evalJS<boolean>(page, `/Detalhes da consulta/i.test(document.body.innerText||'')`);
    if (abriu) {
      throw new Error(
        `a consulta ${consulta} não aparece no login "${login || "?"}" deste perfil — ` +
          `ela foi aberta com outro login da loja? (o Portal só mostra pra quem abriu)`
      );
    }
    throw new Error(`a consulta não carregou no Portal (${page.url()})`);
  }
  const confere = await evalJS<boolean>(
    page,
    `(document.body.innerText||'').indexOf(${JSON.stringify(consulta)})>=0`
  );
  if (!confere) throw new Error(`a página aberta não mostra a consulta ${consulta} (${page.url()})`);
  for (let i = 0; i < 10; i++) {
    if (!(await evalJS<boolean>(page, JS_EXPANDIR))) break;
    await sleep(2500);
  }
  const brutos = (await evalJS<{ autor: string; quando: string; canal: string; texto: string }[]>(
    page,
    JS_MENSAGENS
  )) || [];
  const itens: Item[] = brutos.map((b) => ({
    chat: true,
    autor: b.autor || "?",
    texto: b.texto || "",
    quando: b.quando || null,
  }));
  const st = (await evalJS<{ status: string; progresso: string }>(page, JS_STATUS)) || {
    status: "",
    progresso: "",
  };
  const falas: Fala[] = itens.filter(eFalaDoAgente).map((i) => ({
    texto: i.texto,
    quando: isoDoPortal(i.quando) as string,
    autor: i.autor,
  }));
  return {
    url,
    situacao: st.status,
    itens,
    falas: falas.filter((f) => !!f.quando),
    historico: montarHistorico(consulta, st.status, st.progresso, itens),
  };
}
