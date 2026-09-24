import type { Page } from "puppeteer-core";
import { cfg } from "./config";
import type { Caso, Fala } from "./davinci";

/**
 * Lê o "Histórico da Solicitação" de uma devolução no Seller Center da Shopee.
 *
 * Caminho visto na tela real em 24/09/2026 (perfil "Vortan - Shopee", pedido
 * 260910MATESNVN, solicitação 2609200FUTKM4JD):
 *
 *   Retornos e Pedidos cancelados › busca pelo nº do PEDIDO › Aplicar
 *   › /portal/sale/return/<id interno> ("Detalhes da Solicitação": linha do
 *     tempo + caixa do resultado, ex. "Requisição negada")
 *   › "Histórico do chat" › Ver detalhes › janela "Histórico da Solicitação"
 *     (cada mensagem: autor, texto, "DD-MM-AAAA HH:MM").
 *
 * SÓ LÊ. Os únicos cliques são: o campo de busca, "Aplicar" e "Ver detalhes"
 * (abre uma janela de leitura). A página também tem as estrelas de "Como você
 * avalia…" e "Conversa com o comprador" — nunca são tocadas; a devolução é
 * aberta pelo endereço do link, não por clique na linha.
 *
 * Toda lógica de DOM vai como STRING pro page.evaluate (o tsx/esbuild
 * instrumenta funções com __name e quebra dentro do browser) — mesmo padrão do
 * executor da Logística.
 */

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class LoginNecessario extends Error {
  constructor(msg = "login do Seller Center caiu — entrar no perfil e logar de novo") {
    super(msg);
    this.name = "LoginNecessario";
  }
}

export interface Item {
  chat: boolean; // mensagem de alguém (true) × evento do sistema ("Você criou uma disputa…")
  autor: string;
  texto: string;
  quando: string | null; // "DD-MM-AAAA HH:MM" como a tela mostra
}

export interface Leitura {
  url: string;
  situacao: string;
  itens: Item[];
  falas: Fala[];
  historico: string;
}

const SEL_BUSCA = 'input[placeholder*="ID da solicita"]';

// Janela "Histórico da Solicitação" visível (há cópias escondidas no DOM).
const JS_MODAL = `(function(){return [...document.querySelectorAll('.eds-modal__box')].find(function(b){var t=b.querySelector('.eds-modal__title');return b.getBoundingClientRect().width>0&&t&&/Hist.rico da Solicita/.test(t.innerText||'');})||null;})()`;

async function evalJS<T = any>(page: Page, js: string): Promise<T | undefined> {
  return (page.evaluate(js) as Promise<T>).catch(() => undefined);
}

async function clicar(page: Page, pt: { x: number; y: number } | null | undefined): Promise<boolean> {
  if (!pt) return false;
  await page.mouse.move(pt.x, pt.y).catch(() => undefined);
  await sleep(80);
  await page.mouse.click(pt.x, pt.y).catch(() => undefined);
  return true;
}

async function conferirLogin(page: Page): Promise<void> {
  const url = page.url();
  const senha = await evalJS<boolean>(page, `!!document.querySelector('input[type="password"]')`);
  if (/signin|login|\/account\//i.test(url) || senha) throw new LoginNecessario();
}

/** "24-09-2026 15:59" -> "2026-09-24T15:59:00-03:00" (as telas da Shopee BR
 *  mostram a hora de São Paulo). */
export function isoDaTela(s: string | null): string | null {
  const m = /(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2})/.exec(s || "");
  if (!m) return null;
  return `${m[3]}-${m[2]}-${m[1]}T${m[4]}:${m[5]}:00-03:00`;
}

function dataCurta(s: string | null): string {
  const m = /(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2})/.exec(s || "");
  return m ? `${m[1]}/${m[2]}/${m[3]} ${m[4]}:${m[5]}` : "sem hora";
}

/** Fala DELES = mensagem (não evento) de autor Shopee. A nossa ("Você…") e a
 *  do nome da loja ficam só no histórico; o servidor ainda descarta eco. */
export function eFalaDaShopee(i: Item): boolean {
  return i.chat && /shopee/i.test(i.autor) && !/^voc[eê]\b/i.test(i.autor) && !!i.texto && !!i.quando;
}

/** Acha a devolução do pedido na lista e devolve o link dela. */
async function acharDevolucao(page: Page, caso: Caso): Promise<string> {
  const pedido = (caso.pedido_marketplace || "").trim();
  const solicitacao = (caso.chamado || "").trim();
  await page
    .goto(`${cfg.sellerUrl}/portal/sale/returnrefundcancel`, { waitUntil: "networkidle2", timeout: 60000 })
    .catch(() => undefined);
  await conferirLogin(page);
  await page.waitForSelector(SEL_BUSCA, { visible: true, timeout: 30000 });
  await page.focus(SEL_BUSCA);
  await evalJS(page, `(function(){var i=document.querySelector(${JSON.stringify(SEL_BUSCA)});if(i&&i.select)i.select();})()`);
  await page.keyboard.press("Backspace");
  await page.keyboard.type(pedido, { delay: 50 });
  await sleep(500);
  // Enter não filtra (visto em 24/09): tem que ser o botão.
  const aplicar = await evalJS<{ x: number; y: number } | null>(
    page,
    `(function(){var b=[...document.querySelectorAll('button')].find(function(e){return (e.innerText||'').trim()==='Aplicar'&&e.getBoundingClientRect().width>0});if(!b)return null;var r=b.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};})()`
  );
  if (!(await clicar(page, aplicar))) throw new Error("botão Aplicar não encontrado na lista de devoluções");

  for (let i = 0; i < 20; i++) {
    await sleep(1000);
    const links =
      (await evalJS<{ href: string; texto: string }[]>(
        page,
        `[...document.querySelectorAll('a[href*="/portal/sale/return/"]')].map(function(a){var p=a.parentElement&&a.parentElement.parentElement;return {href:a.getAttribute('href'),texto:((a.innerText||'')+' '+(p?(p.innerText||''):'')).slice(0,800)};})`
      )) || [];
    const doPedido = links.filter((l) => l.texto.includes(pedido));
    if (!doPedido.length) continue;
    const exato = solicitacao ? doPedido.find((l) => l.texto.includes(solicitacao)) : undefined;
    if (exato) return exato.href;
    const hrefs = [...new Set(doPedido.map((l) => l.href))];
    if (hrefs.length === 1 && i >= 2) return hrefs[0];
    if (hrefs.length > 1 && i >= 4) {
      throw new Error(
        `o pedido ${pedido} tem ${hrefs.length} devoluções e nenhuma mostrou a solicitação ${solicitacao}`
      );
    }
  }
  throw new Error(`a devolução do pedido ${pedido} não apareceu na busca desta loja`);
}

/** Linha do tempo + caixa do resultado ("Requisição negada …"), em texto. */
async function lerSituacao(page: Page): Promise<string> {
  const t =
    (await evalJS<string>(
      page,
      `(function(){var t=document.body.innerText||'';var i=t.indexOf('Comprador solicitou');if(i<0)i=t.indexOf('Detalhes da Solicita');if(i<0)i=0;var fim=t.length;['Como você avalia','Informações da disputa','Histórico do chat','Solicitado pelo comprador','Detalhes do pedido'].forEach(function(k){var j=t.indexOf(k,i+1);if(j>i&&j<fim)fim=j;});return t.slice(i,fim);})()`
    )) || "";
  return t
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
    .join("\n")
    .slice(0, 2000);
}

/** Abre "Histórico do chat › Ver detalhes", carrega tudo e lê as mensagens. */
async function lerJanela(page: Page): Promise<Item[] | null> {
  const ver = await evalJS<{ x: number; y: number } | null>(
    page,
    `(function(){var t=document.querySelector('.discussion-history-title');if(!t)return null;var box=t.parentElement;for(var k=0;k<5&&box;k++){var v=[...box.querySelectorAll('*')].find(function(e){return e.children.length===0&&(e.innerText||'').trim()==='Ver detalhes'});if(v){v.scrollIntoView({block:'center'});var r=v.getBoundingClientRect();if(r.width>0)return {x:r.left+r.width/2,y:r.top+r.height/2};}box=box.parentElement;}return null;})()`
  );
  if (!ver) return null; // página sem "Histórico do chat": ainda não houve conversa
  await sleep(400);
  await clicar(page, ver);
  let aberta = false;
  for (let i = 0; i < 20 && !aberta; i++) {
    await sleep(500);
    aberta = !!(await evalJS<boolean>(page, `!!${JS_MODAL}`));
  }
  if (!aberta) throw new Error('cliquei em "Ver detalhes" e a janela "Histórico da Solicitação" não abriu');

  // Rolagem infinita: sobe e desce até o nº de mensagens parar de mudar.
  let antes = -1;
  for (let i = 0; i < 10; i++) {
    const n =
      (await evalJS<number>(
        page,
        `(function(){var m=${JS_MODAL};if(!m)return 0;[...m.querySelectorAll('*')].forEach(function(e){if(e.scrollHeight>e.clientHeight+10){e.scrollTop=(${i}%2===0)?0:e.scrollHeight;}});return m.querySelectorAll('.chat_message-item').length;})()`
      )) ?? 0;
    await sleep(800);
    if (n === antes && i >= 2) break;
    antes = n;
  }

  const itens =
    (await evalJS<Item[]>(
      page,
      `(function(){var m=${JS_MODAL};if(!m)return [];return [...m.querySelectorAll('.chat_message-item')].map(function(i){var folhas=[...i.querySelectorAll('*')].filter(function(e){return e.children.length===0&&(e.innerText||'').trim()}).map(function(e){return (e.innerText||'').trim()});var tudo=(i.innerText||'').trim();var hs=tudo.match(/\\d{2}-\\d{2}-\\d{4}\\s+\\d{2}:\\d{2}/g);var quando=hs?hs[hs.length-1]:null;var autor=folhas[0]||'';var tc=i.querySelector('[class*="text-content"]');var texto=tc?(tc.innerText||'').trim():tudo.split('\\n').map(function(s){return s.trim()}).filter(function(s){return s&&s!==autor&&s!==quando}).join('\\n');return {chat:(i.className||'').toString().indexOf('chat-message-item')>=0,autor:autor,texto:texto,quando:quando};});})()`
    )) || [];

  // Fecha a janela (Esc; se não fechar, o X dela).
  await page.keyboard.press("Escape").catch(() => undefined);
  await sleep(500);
  if (await evalJS<boolean>(page, `!!${JS_MODAL}`)) {
    const x = await evalJS<{ x: number; y: number } | null>(
      page,
      `(function(){var m=${JS_MODAL};var c=m&&m.querySelector('.eds-modal__close');if(!c)return null;var r=c.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};})()`
    );
    await clicar(page, x);
  }
  return itens;
}

function montarHistorico(situacao: string, itens: Item[] | null): string {
  const partes = [`Situação na tela da Shopee:\n${situacao || "(não li)"}`];
  if (itens === null) {
    partes.push("Histórico da Solicitação: a página ainda não tem conversa.");
  } else {
    const linhas = itens.map((i) => `[${dataCurta(i.quando)}] ${i.autor}:\n${i.texto}`);
    partes.push(`Histórico da Solicitação:\n\n${linhas.join("\n\n") || "(vazio)"}`);
  }
  return partes.join("\n\n");
}

/** Lê UM caso. Lança erro quando não deu pra ler (quem chama manda ok:false). */
export async function ler(page: Page, caso: Caso): Promise<Leitura> {
  if (!(caso.pedido_marketplace || "").trim()) throw new Error("caso sem nº do pedido da Shopee");
  const href = await acharDevolucao(page, caso);
  const url = href.startsWith("http") ? href : `${cfg.sellerUrl}${href}`;
  await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 }).catch(() => undefined);
  await conferirLogin(page);
  let pronta = false;
  for (let i = 0; i < 20 && !pronta; i++) {
    await sleep(500);
    pronta = !!(await evalJS<boolean>(
      page,
      `/Detalhes da Solicita|N.º? da solicita/i.test(document.body.innerText||'')`
    ));
  }
  if (!pronta) throw new Error(`a página da devolução não carregou (${url})`);
  const solicitacao = (caso.chamado || "").trim();
  if (solicitacao) {
    const confere = await evalJS<boolean>(
      page,
      `(document.body.innerText||'').indexOf(${JSON.stringify(solicitacao)})>=0`
    );
    if (!confere) throw new Error(`a página aberta não mostra a solicitação ${solicitacao} (${url})`);
  }
  const situacao = await lerSituacao(page);
  const itens = await lerJanela(page);
  const falas: Fala[] = (itens || []).filter(eFalaDaShopee).map((i) => ({
    texto: i.texto,
    quando: isoDaTela(i.quando) as string,
    autor: i.autor,
  }));
  return { url, situacao, itens: itens || [], falas, historico: montarHistorico(situacao, itens) };
}
