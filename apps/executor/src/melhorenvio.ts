import puppeteer, { Browser, Page } from "puppeteer-core";
import fs from "node:fs";
import path from "node:path";
import { cfg } from "./config";
import { log } from "./log";

/**
 * Melhor Envio — "Suspender entrega" de um envio JÁ POSTADO (Correios).
 *
 * O Melhor Envio não tem API pra isso; só o painel: Meus envios › Envios
 * postados › (envio) › Ações do envio › Suspender entrega › SOLICITAR. Quem
 * pede é o DaVinci (botão na Logística); aqui só se clica.
 *
 * Regras (do próprio Melhor Envio): quanto antes, maior a chance; o frete não
 * volta; não dá pra desfazer. Por isso a TRAVA: sem MELHORENVIO_CALIBRATED=true
 * o fluxo roda em modo SECO — encontra o envio, abre as ações, LOCALIZA o botão
 * "Suspender entrega" e PARA ANTES DE CLICAR NELE, devolvendo print e a lista
 * de botões da tela (pra calibrar os seletores).
 *
 * A trava fica antes desse clique de propósito: ninguém verificou ainda que o
 * Melhor Envio mostra uma confirmação depois dele. Se não mostrar, clicar JÁ É
 * suspender. Calibrar a etapa final exige um humano fazendo uma vez, de olho,
 * num envio que ele realmente queira suspender.
 *
 * Toda lógica de DOM vai como STRING pro page.evaluate (o tsx/esbuild
 * instrumenta funções com __name e quebra dentro do browser) — mesmo padrão
 * do shopee.ts/flashsale.ts. NUNCA browser.close(): o perfil é do AdsPower.
 */

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const DEBUG_DIR = process.env.DEBUG_DIR || "./debug";

export class NeedsManualLogin extends Error {
  constructor(msg = "login necessário no Melhor Envio") {
    super(msg);
    this.name = "NeedsManualLogin";
  }
}

export interface Session {
  browser: Browser;
  page: Page;
}

export interface SuspensaoResult {
  ok: boolean; // fluxo chegou ao fim sem erro
  found: boolean; // achou o envio na lista
  requested: boolean; // clicou em SOLICITAR de verdade
  dry: boolean; // parou antes do SOLICITAR (trava ou sem commit)
  reason?: string;
  url?: string;
  buttons?: string[]; // botões visíveis na tela (calibração)
  confirmation?: string; // texto lido depois do Solicitar
  screenshot?: string;
}

async function evalJS<T = any>(page: Page, js: string): Promise<T> {
  return page.evaluate(js).catch(() => undefined) as Promise<T>;
}

async function clickCenter(page: Page, selector: string): Promise<boolean> {
  const pt = (await evalJS<{ x: number; y: number } | null>(
    page,
    `(function(){var el=document.querySelector(${JSON.stringify(selector)});if(!el)return null;el.scrollIntoView({block:'center'});var r=el.getBoundingClientRect();if(r.width<1||r.height<1)return null;return {x:r.left+r.width/2,y:r.top+r.height/2};})()`
  )) as { x: number; y: number } | null;
  if (!pt) return false;
  await page.mouse.move(pt.x, pt.y).catch(() => undefined);
  await sleep(80);
  await page.mouse.click(pt.x, pt.y).catch(() => undefined);
  return true;
}

async function screenshot(page: Page, name: string): Promise<string | undefined> {
  try {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    const file = path.join(
      DEBUG_DIR,
      `me-${name}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`
    );
    await page.screenshot({ path: file, fullPage: false });
    return file;
  } catch {
    return undefined;
  }
}

// Helpers de DOM (strings): vis() = visível; txt() = texto compacto.
const H = `var vis=function(el){return !!el && el.offsetParent!==null;};var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};`;

const DUMP_JS = `(function(){${H}
  var all=[].slice.call(document.querySelectorAll('button,a,[role="button"],.btn,li')).filter(vis);
  var seen={},out=[];all.forEach(function(b){var t=txt(b).slice(0,50);if(t&&!seen[t]){seen[t]=1;out.push(t);}});
  return {url:location.href,title:document.title,buttons:out.slice(0,80),text:(document.body.innerText||'').replace(/\\s+/g,' ').slice(0,500)};
})()`;

// Campo de busca da lista de envios (placeholder tipo "Buscar", "Pesquisar",
// "código de rastreio"…). Marca com data-me-search.
const FIND_SEARCH_JS = `(function(){${H}
  [].slice.call(document.querySelectorAll('[data-me-search]')).forEach(function(e){e.removeAttribute('data-me-search');});
  var ins=[].slice.call(document.querySelectorAll('input[type="search"],input[type="text"],input:not([type])')).filter(vis);
  var pick=ins.find(function(i){return /busc|pesquis|rastre|c[oó]digo|procur|filtr/i.test((i.placeholder||'')+' '+(i.getAttribute('aria-label')||'')+' '+(i.name||''));})||ins[0];
  if(!pick)return {ok:false};
  pick.setAttribute('data-me-search','1');
  return {ok:true,placeholder:pick.placeholder||'',name:pick.name||''};
})()`;

// Bloco (card/linha) do envio que contém o rastreio; marca o bloco
// (data-me-card) e, se houver, a seta de expandir (data-me-expand).
function findCardJS(rastreio: string): string {
  return `(function(){${H}
  var alvo=${JSON.stringify(rastreio.toUpperCase())};
  ['data-me-card','data-me-expand'].forEach(function(a){[].slice.call(document.querySelectorAll('['+a+']')).forEach(function(e){e.removeAttribute(a);});});
  var leaf=[].slice.call(document.querySelectorAll('span,div,td,p,a,strong,b,small')).filter(function(e){return vis(e)&&e.children.length<=2&&txt(e).toUpperCase().indexOf(alvo)>=0;});
  if(!leaf.length)return {found:false};
  var el=leaf[0];
  var card=el.closest('tr,li,article,[class*="card"],[class*="Card"],[class*="shipment"],[class*="envio"],[class*="item"]');
  var n=0;while(!card&&el&&n<6){el=el.parentElement;n++;if(el&&el.querySelectorAll('button,a,[role="button"]').length>=1)card=el;}
  if(!card)card=leaf[0].parentElement;
  card.setAttribute('data-me-card','1');
  var exp=[].slice.call(card.querySelectorAll('button,a,[role="button"],i,svg,span')).filter(function(e){var s=((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('title')||'')+' '+(e.className&&e.className.baseVal!==undefined?e.className.baseVal:e.className||'')+' '+txt(e)).toLowerCase();return vis(e)&&/expand|chevron|arrow|seta|caret|toggle|detalhe|abrir|ver mais/.test(s);});
  if(exp.length){exp[exp.length-1].setAttribute('data-me-expand','1');}
  return {found:true,cardText:txt(card).slice(0,160),hasExpand:exp.length>0};
})()`;
}

// Botão/menu por texto (regex), preferindo dentro de modal/dropdown aberto e
// depois dentro do card; marca com data-me-btn.
function findBtnJS(pattern: string, scope: "card" | "modal" | "any"): string {
  return `(function(){${H}
  [].slice.call(document.querySelectorAll('[data-me-btn]')).forEach(function(e){e.removeAttribute('data-me-btn');});
  var re=new RegExp(${JSON.stringify(pattern)},'i');
  var roots=[];
  if(${JSON.stringify(scope)}==='modal'){roots=[].slice.call(document.querySelectorAll('[role="dialog"],.modal,.swal2-container,[class*="modal"],[class*="Modal"],[class*="dialog"]')).filter(vis);}
  if(${JSON.stringify(scope)}==='card'){var c=document.querySelector('[data-me-card]');if(c)roots=[c];}
  if(!roots.length)roots=[document.body];
  for(var r=0;r<roots.length;r++){
    var cand=[].slice.call(roots[r].querySelectorAll('button,a,[role="button"],[role="menuitem"],li,span,div')).filter(function(e){return vis(e)&&re.test(txt(e))&&txt(e).length<60;});
    if(cand.length){var el=cand[cand.length-1];el.setAttribute('data-me-btn','1');return {ok:true,text:txt(el).slice(0,60),tag:el.tagName};}
  }
  return {ok:false};
})()`;
}

function classifyUrl(raw: string): "login" | "app" | "other" {
  try {
    const u = new URL(raw);
    if (/\/(login|entrar|signin|sign-in|auth|cadastro)/i.test(u.pathname)) return "login";
    if (/melhorenvio\.com\.br$/i.test(u.hostname)) return "app";
  } catch {
    /* ignore */
  }
  return "other";
}

async function guardLogin(page: Page): Promise<void> {
  await sleep(1500);
  if (classifyUrl(page.url()) === "login") throw new NeedsManualLogin(`URL de login: ${page.url()}`);
  const pwd = await evalJS<boolean>(
    page,
    `(function(){var i=document.querySelector('input[type="password"]');return !!(i&&i.offsetParent!==null);})()`
  );
  if (pwd) throw new NeedsManualLogin("campo de senha visível (tela de login)");
}

export async function connect(wsEndpoint: string): Promise<Session> {
  const browser = await puppeteer.connect({ browserWSEndpoint: wsEndpoint, defaultViewport: null });
  const pages = await browser.pages();
  let page =
    pages.find((p) => /melhorenvio\.com\.br/.test(p.url())) ||
    pages.find((p) => !/^about:blank$/i.test(p.url())) ||
    pages[0];
  if (!page) page = await browser.newPage();
  await page.bringToFront().catch(() => undefined);
  await page
    .goto(cfg.melhorEnvioUrl, { waitUntil: "networkidle2", timeout: 60_000 })
    .catch(() => undefined);
  await guardLogin(page);
  return { browser, page };
}

export async function disconnect(session: Session): Promise<void> {
  try {
    await session.browser.disconnect();
  } catch {
    /* ignore */
  }
}

/** Fluxo completo. Só clica em SOLICITAR com commit=true E MELHORENVIO_CALIBRATED=true. */
export async function suspenderEntrega(
  page: Page,
  opts: { rastreio: string; commit: boolean }
): Promise<SuspensaoResult> {
  const rastreio = opts.rastreio.trim().toUpperCase();
  const dump0 = await evalJS<any>(page, DUMP_JS);
  log.info(`ME ${rastreio}: tela inicial ${dump0?.url} botões=${(dump0?.buttons || []).length}`);

  // 1) busca pelo rastreio
  const search = await evalJS<any>(page, FIND_SEARCH_JS);
  if (search?.ok) {
    await clickCenter(page, '[data-me-search="1"]');
    await page.keyboard.down("Control").catch(() => undefined);
    await page.keyboard.press("KeyA").catch(() => undefined);
    await page.keyboard.up("Control").catch(() => undefined);
    await page.keyboard.down("Meta").catch(() => undefined);
    await page.keyboard.press("KeyA").catch(() => undefined);
    await page.keyboard.up("Meta").catch(() => undefined);
    await page.keyboard.press("Backspace").catch(() => undefined);
    await page.keyboard.type(rastreio, { delay: 30 }).catch(() => undefined);
    await page.keyboard.press("Enter").catch(() => undefined);
    await sleep(3000);
  } else {
    log.warn(`ME ${rastreio}: campo de busca não encontrado — seguindo pela lista`);
  }

  // 2) card do envio
  let card = await evalJS<any>(page, findCardJS(rastreio));
  if (!card?.found) {
    await sleep(2500);
    card = await evalJS<any>(page, findCardJS(rastreio));
  }
  if (!card?.found) {
    const shot = await screenshot(page, `nao-encontrado-${rastreio}`);
    const d = await evalJS<any>(page, DUMP_JS);
    return {
      ok: false,
      found: false,
      requested: false,
      dry: true,
      reason: "envio não encontrado na lista de postados (rastreio não apareceu na tela)",
      url: d?.url,
      buttons: d?.buttons,
      screenshot: shot,
    };
  }
  log.info(`ME ${rastreio}: envio encontrado — "${card.cardText}"`);
  if (card.hasExpand) await clickCenter(page, '[data-me-expand="1"]');
  else await clickCenter(page, '[data-me-card="1"]');
  await sleep(1200);

  // 3) "Ações do envio" → "Suspender entrega"
  const acoes = await evalJS<any>(page, findBtnJS("a[çc][õo]es do envio|a[çc][õo]es", "card"));
  if (acoes?.ok) {
    await clickCenter(page, '[data-me-btn="1"]');
    await sleep(900);
  }
  const susp = await evalJS<any>(page, findBtnJS("suspender entrega|suspender a entrega", "any"));
  if (!susp?.ok) {
    const shot = await screenshot(page, `sem-suspender-${rastreio}`);
    const d = await evalJS<any>(page, DUMP_JS);
    return {
      ok: false,
      found: true,
      requested: false,
      dry: true,
      reason: acoes?.ok
        ? 'menu "Ações do envio" abriu, mas "Suspender entrega" não apareceu (envio não elegível ou tela mudou)'
        : 'não achei "Ações do envio" nem "Suspender entrega" no envio',
      url: d?.url,
      buttons: d?.buttons,
      screenshot: shot,
    };
  }
  // TRAVA — antes do clique, não depois.
  //
  // O modo seco clicava em "Suspender entrega" e só então checava a trava,
  // parando na confirmação. Isso só é seguro se o Melhor Envio REALMENTE
  // mostrar uma confirmação depois desse botão — e isso nunca foi verificado
  // por ninguém: a fila `logistica_robo_comando` está vazia, o robô nunca
  // rodou, e a afirmação vem de relato de segunda mão. Se não houver
  // confirmação, o "teste seco" suspende a entrega de verdade, sem volta e sem
  // devolução do frete. Enquanto um humano não confirmar a tela, o seco para
  // AQUI: com o envio achado, o menu aberto e o botão localizado e fotografado
  // — que é tudo que ele precisa entregar para calibrar os seletores.
  if (!(opts.commit && cfg.melhorEnvioCalibrated)) {
    const shotSeco = await screenshot(page, `seco-${rastreio}`);
    const dSeco = await evalJS<any>(page, DUMP_JS);
    await page.keyboard.press("Escape").catch(() => undefined);
    return {
      ok: true,
      found: true,
      requested: false,
      dry: true,
      reason: cfg.melhorEnvioCalibrated
        ? `comando sem commit — parei antes de clicar em "${susp.text}"`
        : `MODO SECO: achei o envio e o botão "${susp.text}", e parei ANTES de clicar nele `
          + "(MELHORENVIO_CALIBRATED != true no executor)",
      url: dSeco?.url,
      buttons: dSeco?.buttons,
      screenshot: shotSeco,
    };
  }

  await clickCenter(page, '[data-me-btn="1"]');
  await sleep(1200);

  // 4) confirmação: SOLICITAR
  const sol = await evalJS<any>(page, findBtnJS("^solicitar$|^confirmar$|^sim, suspender", "modal"));
  const dModal = await evalJS<any>(page, DUMP_JS);
  if (!sol?.ok) {
    const shot = await screenshot(page, `sem-solicitar-${rastreio}`);
    return {
      ok: false,
      found: true,
      requested: false,
      dry: true,
      reason: 'cliquei em "Suspender entrega" mas a confirmação "Solicitar" não apareceu',
      url: dModal?.url,
      buttons: dModal?.buttons,
      screenshot: shot,
    };
  }
  // Chegou aqui = commit && calibrado (a trava ficou lá atrás). Ponto de não
  // retorno: o Melhor Envio não desfaz e o frete não volta.
  await clickCenter(page, '[data-me-btn="1"]');
  await sleep(2500);
  const d2 = await evalJS<any>(page, DUMP_JS);
  const shot = await screenshot(page, `solicitado-${rastreio}`);
  const conf = String(d2?.text || "");
  const okText = /suspens[ãa]o[^.]{0,80}(solicitad|recebid|registrad|enviad)|sucesso/i.test(conf);
  return {
    ok: true,
    found: true,
    requested: true,
    dry: false,
    reason: okText ? "suspensão solicitada" : 'cliquei em "Solicitar"; confirmação não lida na tela',
    url: d2?.url,
    confirmation: conf.slice(0, 300),
    screenshot: shot,
  };
}
