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

// Linha da tabela do envio que contém o rastreio; marca a linha
// (data-me-card) e o botão de menu de ações dela (data-me-menu).
//
// Não existe mais um helper de busca: a busca desta tela é por destinatário ou
// ORD e não aceita código de rastreio, então usá-la só escondia o envio.
// Sub-aba "Postados" dentro de "Liberados e postados". É um elemento sem href
// (troca de aba por JS), então mira-se no texto exato.
const CLICAR_ABA_POSTADOS_JS = `(function(){${H}
  var cand=[].slice.call(document.querySelectorAll('div,button,a,li,span,[role="tab"]')).filter(function(e){
    return vis(e)&&txt(e).toLowerCase()==='postados';
  });
  if(!cand.length)return {ok:false};
  var el=cand[cand.length-1];
  el.click();
  return {ok:true,texto:txt(el)};
})()`;

// A linha do envio é o PRIMEIRO ancestral do rastreio que tem um botão de menu
// — e tem que ter exatamente um, e nenhum outro rastreio. Antes se usava
// closest() com classes genéricas, que casava num contêiner da página inteira:
// o "menu da linha" virava o primeiro menu da tabela, e pedir a suspensão do 5º
// envio abria o menu do 1º (visto no modo seco em 24/09/2026 — no modo real
// teria suspendido a entrega de outra pessoa).
function findCardJS(rastreio: string): string {
  return `(function(){${H}
  var alvo=${JSON.stringify(rastreio.toUpperCase())};
  ['data-me-card','data-me-menu'].forEach(function(a){[].slice.call(document.querySelectorAll('['+a+']')).forEach(function(e){e.removeAttribute(a);});});
  var ehMenu=function(e){var s=((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('title')||'')).toLowerCase();return /menu de a[çc][õo]es|a[çc][õo]es do envio/.test(s);};
  var rotulos=function(el){return [].slice.call(el.querySelectorAll('button,[role="button"]')).filter(vis).map(function(e){return (e.getAttribute('aria-label')||e.getAttribute('title')||txt(e)||'(sem rótulo)').slice(0,50);});};
  // O elemento MAIS justo que mostra o rastreio (o link da célula), não um
  // contêiner grande que por acaso também contém o texto.
  var leaf=[].slice.call(document.querySelectorAll('a,span,div,td,p,strong,b,small')).filter(function(e){return vis(e)&&txt(e).toUpperCase().indexOf(alvo)>=0;});
  if(!leaf.length)return {found:false};
  leaf.sort(function(x,y){return txt(x).length-txt(y).length;});
  var el=leaf[0],card=null,men=[];
  for(var n=0;el&&el!==document.body&&n<15;n++){
    men=[].slice.call(el.querySelectorAll('button,a,[role="button"]')).filter(function(e){return vis(e)&&ehMenu(e);});
    if(men.length){card=el;break;}
    el=el.parentElement;
  }
  if(!card)return {found:true,hasMenu:false,cardText:txt(leaf[0].parentElement).slice(0,160),botoesDaLinha:[]};
  var outros=(txt(card).toUpperCase().match(/[A-Z]{2}[0-9]{9}BR/g)||[]).filter(function(c){return c!==alvo;});
  if(men.length!==1||outros.length){
    return {found:true,hasMenu:false,ambiguo:true,cardText:txt(card).slice(0,160),botoesDaLinha:rotulos(card)};
  }
  card.setAttribute('data-me-card','1');
  men[0].setAttribute('data-me-menu','1');
  return {found:true,hasMenu:true,cardText:txt(card).slice(0,160),
          menuLabel:men[0].getAttribute('aria-label')||'',botoesDaLinha:rotulos(card)};
})()`;
}

// O menu de ações é flutuante (fica fora da linha). Confere que o "Suspender
// entrega" achado está colado no botão que abrimos — se estiver longe, é o
// menu de outra linha e não se clica.
const DISTANCIA_MENU_JS = `(function(){
  var m=document.querySelector('[data-me-menu="1"]'),b=document.querySelector('[data-me-btn="1"]');
  if(!m||!b)return null;
  var r1=m.getBoundingClientRect(),r2=b.getBoundingClientRect();
  return Math.round(Math.min(Math.abs(r2.top-r1.bottom),Math.abs(r1.top-r2.bottom)));
})()`;
const DISTANCIA_MAX_PX = 250;

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

  // 1) abrir a aba POSTADOS
  //
  // "Envios › Liberados e postados" tem duas sub-abas e a que abre por padrão é
  // LIBERADOS, que está vazia. O envio postado só aparece em POSTADOS. Antes se
  // tentava resolver isso pela âncora #postados na URL, mas num arquivo .env o
  // "#" começa um comentário: o endereço chegava aqui truncado e o robô caía em
  // Liberados. Clicar na aba, como uma pessoa faria, não depende de URL.
  //
  // Também NÃO se digita mais o rastreio na busca: a busca desta tela é por
  // "dados do destinatário ou ORD" e não aceita código de rastreio — jogar o
  // rastreio ali zerava a lista e escondia justamente o envio procurado.
  const aba = await evalJS<any>(page, CLICAR_ABA_POSTADOS_JS);
  if (aba?.ok) {
    await sleep(4000);
    log.info(`ME ${rastreio}: abri a aba "${aba.texto}"`);
  } else {
    log.warn(`ME ${rastreio}: não achei a aba "Postados" — seguindo com a tela como veio`);
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
  // 3) três pontinhos da linha → "Suspender entrega"
  //
  // A tela é uma TABELA, não cartões: cada linha tem ícones sem texto à
  // direita. O menu de ações abre por um botão rotulado
  // "Abrir menu de ações do envio" — não existe nenhum botão escrito "Ações
  // do envio", que era o que este robô procurava e nunca achava.
  if (!card.hasMenu) {
    const shot = await screenshot(page, `sem-menu-${rastreio}`);
    const d = await evalJS<any>(page, DUMP_JS);
    return {
      ok: false,
      found: true,
      requested: false,
      dry: true,
      reason: card.ambiguo
        ? "achei o rastreio mas não consegui separar a linha dele das outras "
          + "(mais de um menu ou mais de um rastreio no mesmo bloco) — não cliquei em nada"
        : "achei o envio mas não achei o botão de ações na linha dele; botões vistos: "
          + (card.botoesDaLinha || []).join(" | "),
      url: d?.url,
      buttons: d?.buttons,
      screenshot: shot,
    };
  }
  log.info(`ME ${rastreio}: abrindo menu de ações ("${card.menuLabel}")`);
  await clickCenter(page, '[data-me-menu="1"]');
  await sleep(1200);

  // O menu é flutuante e fica FORA da linha, por isso a busca é na página.
  const susp = await evalJS<any>(page, findBtnJS("suspender entrega|suspender a entrega", "any"));
  if (!susp?.ok) {
    const shot = await screenshot(page, `sem-suspender-${rastreio}`);
    const d = await evalJS<any>(page, DUMP_JS);
    return {
      ok: false,
      found: true,
      requested: false,
      dry: true,
      reason:
        'o menu de ações abriu, mas "Suspender entrega" não apareceu '
        + "(envio não elegível, ou a tela mudou)",
      url: d?.url,
      buttons: d?.buttons,
      screenshot: shot,
    };
  }
  const distancia = await evalJS<number | null>(page, DISTANCIA_MENU_JS);
  if (distancia == null || distancia > DISTANCIA_MAX_PX) {
    const shot = await screenshot(page, `menu-longe-${rastreio}`);
    await page.keyboard.press("Escape").catch(() => undefined);
    return {
      ok: false,
      found: true,
      requested: false,
      dry: true,
      reason:
        `o "Suspender entrega" que apareceu não está junto do menu da linha deste envio `
        + `(distância ${distancia ?? "?"} px) — pode ser de outra linha; não cliquei em nada`,
      screenshot: shot,
    };
  }
  log.info(`ME ${rastreio}: linha "${card.cardText}" — menu a ${distancia}px do botão`);

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
  //
  // O rótulo exato do botão de confirmação nunca foi visto por ninguém, então a
  // regex aceita as variações plausíveis. Se mesmo assim nada aparecer, o
  // recado NÃO pode ser um "falhou" seco: o clique em "Suspender entrega" já
  // foi dado, e se essa tela não tiver confirmação o pedido já está suspenso.
  // Dizer "falhou" aí levaria alguém a tentar de novo ou a tratar na mão um
  // envio que já foi suspenso.
  const sol = await evalJS<any>(
    page,
    findBtnJS("^solicitar|^confirmar|^sim[, ]|^suspender$|suspens[ãa]o$", "modal")
  );
  const dModal = await evalJS<any>(page, DUMP_JS);
  if (!sol?.ok) {
    const shot = await screenshot(page, `sem-solicitar-${rastreio}`);
    return {
      ok: false,
      found: true,
      requested: false,
      dry: false,
      reason:
        'ATENÇÃO: cliquei em "Suspender entrega" e NENHUMA confirmação apareceu. '
        + "Se esta tela não pede confirmação, a suspensão JÁ FOI FEITA. "
        + "Confira no painel do Melhor Envio antes de tentar de novo ou tratar na mão.",
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
