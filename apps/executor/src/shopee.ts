import puppeteer, { Browser, Page } from "puppeteer-core";
import fs from "node:fs";
import path from "node:path";

/**
 * FASE 5 - Camada HIBRIDA da Shopee (calibrada na tela real, FASE 4.5).
 * Dois modos de ligar/desligar anuncios por loja:
 *
 *   MODE 'gmvmax' : liga/desliga o "GMV Max da Loja" inteiro (1 controle por loja)
 *     - status  .gms-campaign-status  (classe ongoing/paused/ended)
 *     - menu    .gms-overview-more-dropdown ("Mais") -> .eds-dropdown-item
 *       "Pausar" (desliga) / "Ativar|Resumir|Retomar|Iniciar agora" (liga)
 *
 *   MODE 'manual' : pausa/retoma anuncio por anuncio (aba "Anuncios de Produtos")
 *     - linhas  .eds-table__row com a[href*="pas/product/manual/{id}"]
 *     - status  .campaign-state-new (ongoing/paused/ended)
 *     - orcam.  .budget-text
 *     - menu da linha -> .eds-dropdown-item "Pausar"/"Resumir"
 *
 * NUNCA usa "Encerrar"/"Excluir" (destrutivos). Toda a logica de DOM vai como
 * STRING pro page.evaluate (o tsx/esbuild instrumenta funcoes com __name e quebra
 * dentro do browser). NUNCA browser.close() - quem fecha o profile e adspower.stop().
 */

const ADS_BASE =
  process.env.SHOPEE_SELLER_ADS_URL ||
  "https://seller.shopee.com.br/portal/marketing/pas/index";
const ADS_HOME = /[?&]type=/.test(ADS_BASE)
  ? ADS_BASE
  : ADS_BASE + (ADS_BASE.includes("?") ? "&" : "?") + "type=new_cpc_homepage&group=today";

const DEBUG_DIR = process.env.DEBUG_DIR || "./debug";
const CALIBRATED = process.env.SELECTORS_CALIBRATED === "true";

// Sinal de tela de login (fallback humano, nunca resolvemos captcha)
const SEL_LOGIN_PASSWORD = process.env.SEL_LOGIN_PASSWORD || 'input[type="password"]';

export type ControlMode = "gmvmax" | "manual";
export interface ManualFilter {
  scope: "all" | "ids" | "names";
  list?: string[];
}

export class NeedsManualLogin extends Error {
  constructor(msg = "login/verificacao necessaria") {
    super(msg);
    this.name = "NeedsManualLogin";
  }
}

export interface Session {
  browser: Browser;
  page: Page;
}
export interface ActionResult {
  matched: number;
  changed: number;
  detail?: string;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const rand = (min: number, max: number) => Math.floor(min + Math.random() * (max - min));
/** Pausa humana pequena e ALEATORIA entre acoes. */
const humanPause = () => sleep(rand(350, 1100));

// ---------------------------------------------------------------------------
// Conexao / sessao
// ---------------------------------------------------------------------------
export async function connect(wsEndpoint: string): Promise<Session> {
  const browser = await puppeteer.connect({
    browserWSEndpoint: wsEndpoint,
    defaultViewport: null,
  });
  const pages = await browser.pages();
  let page =
    pages.find((p) => /seller\.shopee/.test(p.url())) ||
    pages.find((p) => !/^about:blank$/i.test(p.url())) ||
    pages[0];
  if (!page) page = await browser.newPage();
  await page.bringToFront().catch(() => undefined);
  await page
    .goto(ADS_HOME, { waitUntil: "networkidle2", timeout: 60_000 })
    .catch(() => undefined);
  await guardLoginCaptcha(page);
  await closePromoModals(page);
  return { browser, page };
}

export async function disconnect(session: Session): Promise<void> {
  try {
    await session.browser.disconnect();
  } catch {
    /* ignore */
  }
}

/** Classifica a URL atual por HOST+PATH (IGNORA a query). Sem ignorar a query, o
 *  ?next=<url-do-portal> da propria tela de login daria falso "portal". */
function classifyUrl(raw: string): "portal" | "login" | "other" {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return "other";
  }
  const host = u.hostname.toLowerCase();
  const path = u.pathname.toLowerCase();
  if (/accounts\.shopee/.test(host)) return "login"; // dominio de contas = tela de login
  if (/(\/login|\/signin|\/sign-in|\/verify|\/captcha|account\/auth)/.test(path)) return "login";
  if (/seller\.shopee/.test(host) && /\/portal/.test(path)) return "portal";
  return "other";
}

/** Se a tela indicar login/verificacao -> lanca NeedsManualLogin (sem tentar resolver).
 *  IMPORTANTE: quando a conta ESTA logada, o portal faz um "bounce" de SSO
 *  (portal -> accounts/login?next=portal -> portal). O networkidle2 as vezes cai no
 *  meio desse bounce; se decidissemos no 1o olhar, daria falso NeedsManualLogin numa
 *  conta boa (e a agenda pularia a ativacao). Por isso: se a URL parece login,
 *  esperamos ate ~12s ela ASSENTAR no portal. So e login DE VERDADE se ficar presa. */
export async function guardLoginCaptcha(page: Page): Promise<void> {
  let cls = classifyUrl(page.url());
  for (let i = 0; i < 12 && cls === "login"; i++) {
    await sleep(1000);
    cls = classifyUrl(page.url());
  }
  if (cls === "login") {
    throw new NeedsManualLogin(`URL de login/verificacao (apos espera SSO): ${page.url()}`);
  }
  const pwd = await page.$(SEL_LOGIN_PASSWORD).catch(() => null);
  if (pwd) throw new NeedsManualLogin("campo de senha detectado (tela de login)");
}

export async function dumpDebug(
  page: Page,
  account: string,
  label: string
): Promise<string | null> {
  try {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const base = path.join(DEBUG_DIR, `${stamp}_${sanitize(account)}_${sanitize(label)}`);
    await page.screenshot({ path: `${base}.png`, fullPage: true }).catch(() => undefined);
    const html = await page.content().catch(() => "");
    fs.writeFileSync(`${base}.html`, html);
    return `${base}.png`;
  } catch {
    return null;
  }
}

function sanitize(s: string): string {
  return s.replace(/[^a-z0-9_-]+/gi, "-").slice(0, 60);
}

function requireCalibrated(): void {
  if (!CALIBRATED) {
    throw new Error(
      "Seletores nao calibrados (FASE 5). Valide 1 pausa/retomada ao vivo, ajuste se preciso e defina SELECTORS_CALIBRATED=true no .env."
    );
  }
}

// ---------------------------------------------------------------------------
// Helpers de pagina (todo DOM vai como STRING)
// ---------------------------------------------------------------------------
async function evalJS<T = any>(page: Page, js: string): Promise<T> {
  return page.evaluate(js) as Promise<T>;
}

/** Repete um JS ate a condicao bater ou estourar o tempo. Substitui os sleep()
 *  fixos que perdiam a corrida de render do eds (causa das falhas de 1a tentativa).
 *  Devolve o ultimo resultado se a condicao bateu; senao undefined. Use SO com JS
 *  read-only (contar/ler) ou re-tag idempotente (que limpa a tag anterior) — NUNCA
 *  com JS que clica/toggla, pois seria disparado varias vezes. */
async function pollUntil<T = any>(
  page: Page,
  js: string,
  ok: (r: T | undefined) => boolean,
  timeoutMs: number,
  intervalMs = 250
): Promise<T | undefined> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const r = (await evalJS<T>(page, js).catch(() => undefined)) as T | undefined;
    if (ok(r)) return r;
    if (Date.now() >= deadline) return undefined;
    await sleep(intervalMs);
  }
}

/** Clica no CENTRO do elemento via page.mouse (coordenadas de viewport).
 *  IMPORTANTE: NAO usar page.click(selector) na barra de acao em massa — em listas
 *  longas a barra "Alterar Status" e sticky (.list-fixed-top/.mass-action) e o
 *  scrollIntoViewIfNeeded interno do page.click() erra o ponto -> clique vira no-op
 *  silencioso (era a causa real das contas grandes nao abrirem o menu, ex: Mega).
 *  O page.mouse.click(x,y) NAO rola a tela e acerta o alvo. Diagnostico (diagbulk)
 *  provou: estrategia B (mouse.click por coordenada) abre; A/C (page.click) nao. */
async function clickCenter(page: Page, selector: string): Promise<boolean> {
  const pt = (await evalJS<{ x: number; y: number; cleared?: string[] } | null>(
    page,
    `(function(){
      var el=document.querySelector(${JSON.stringify(selector)});
      if(!el)return null;
      function ponto(){
        var r=el.getBoundingClientRect();
        if(r.width<1||r.height<1)return null;
        var cx=r.left+r.width/2, cy=r.top+r.height/2;
        // se o centro cair fora da viewport, traz pra dentro e recalcula
        if(cy<0||cy>window.innerHeight||cx<0||cx>window.innerWidth){
          el.scrollIntoView({block:'center'});
          r=el.getBoundingClientRect();
          cx=r.left+r.width/2; cy=r.top+r.height/2;
        }
        return {x:cx,y:cy};
      }
      var p=ponto();
      if(!p)return null;
      // Se um overlay (coach-mark/mascara/popover) estiver NA FRENTE do ponto de
      // clique, o page.mouse.click acerta o overlay e o alvo nunca recebe o clique
      // -> era a causa do menu "Alterar Status" nao abrir nas contas pesadas
      // (Minas/Poofy/KFA/Mini, ~82 falhas "item Resumir nao achado" com menu vazio).
      // Deixa o clique ATRAVESSAR camada por camada (pointer-events:none) ate o topo
      // do ponto ser o proprio alvo. Nada de destrutivo: so torna o overlay
      // click-through; a sessao e descartada ao fim do comando.
      var cleared=[];
      for(var i=0;i<8;i++){
        var top=document.elementFromPoint(p.x,p.y);
        if(!top||top===el||el.contains(top)||top.contains(el))break;
        try{
          top.style.setProperty('pointer-events','none','important');
          var cls=(typeof top.className==='string'?top.className:'')||'';
          cleared.push((top.tagName||'').toLowerCase()+'.'+cls.slice(0,32));
        }catch(e){break;}
        var np=ponto();
        if(np)p=np;
      }
      return {x:p.x,y:p.y,cleared:cleared};
    })()`
  ).catch(() => null)) as { x: number; y: number; cleared?: string[] } | null;
  if (!pt) return false;
  if (pt.cleared && pt.cleared.length) {
    console.log(`[clickCenter] overlay neutralizado antes de "${selector}": ${pt.cleared.join(" > ")}`);
  }
  await page.mouse.move(pt.x, pt.y).catch(() => undefined);
  await sleep(60);
  await page.mouse.click(pt.x, pt.y).catch(() => undefined);
  return true;
}

/** Confirma a acao em massa DE VERDADE. O clique no item ("Pausar"/"Resumir")
 *  pode abrir DOIS tipos de modal (varia por conta):
 *    (1) "Pausar/Resumir em Massa" -> botao primario "Confirmar"  (Mega, Inova);
 *    (2) AVISO "X Ads Are In Tasks Now" -> "Proceed to Change" / "Prosseguir com a
 *        alteracao" (perde credito gratis). Aqui o botao de PROSSEGUIR e o NORMAL
 *        (cinza), e o PRIMARIO e "Keep Settings"/"Manter" = CANCELAR. Por isso
 *        casamos por TEXTO, nunca por .eds-button--primary. (Poofy etc.)
 *  O usuario autorizou prosseguir mesmo perdendo o credito. Espera (poll) o modal,
 *  clica o botao de prosseguir por COORDENADA, e trata ENCADEAMENTO (aviso -> depois
 *  confirmar) clicando de novo ate nao sobrar nenhum modal de prosseguir/confirmar.
 *  O sumico e a PROVA de que aplicou — se nada some/clica, retorna false e ABORTA
 *  (nunca conta falso positivo). */
async function confirmBulkModal(page: Page): Promise<boolean> {
  const appeared = await pollUntil<{ ok: boolean }>(
    page,
    CONFIRM_MODAL_PRESENT_JS,
    (r) => !!r && r.ok === true,
    6000,
    200
  );
  if (!appeared || !appeared.ok) return false;
  let clicks = 0;
  for (let i = 0; i < 10; i++) {
    const tagged = (await evalJS(page, TAG_CONFIRM_BTN_JS)) as { ok: boolean; text?: string };
    if (tagged && tagged.ok) {
      await clickCenter(page, '[data-marionete-confirm="1"]');
      clicks++;
      await sleep(700);
      continue; // re-avalia: pode haver um 2o modal encadeado (aviso -> confirmar)
    }
    // nenhum botao prosseguir/confirmar visivel agora: confirma que estabilizou
    // (e nao e so a transicao pra um 2o modal ainda carregando).
    const back = await pollUntil<{ ok: boolean }>(
      page,
      CONFIRM_MODAL_PRESENT_JS,
      (r) => !!r && r.ok === true,
      1500,
      250
    );
    if (back && back.ok) continue; // (re)apareceu -> volta e clica
    return clicks > 0; // estabilizou sem modal -> sucesso SE clicamos ao menos 1x
  }
  const fin = (await evalJS(page, CONFIRM_MODAL_PRESENT_JS)) as { ok: boolean };
  return !!(fin && fin.ok === false) && clicks > 0;
}

const CLOSE_MODALS_JS = `(function(){var n=0;Array.prototype.slice.call(document.querySelectorAll('.eds-modal__close, .eds-modal__close-icon')).forEach(function(c){try{c.click();n++;}catch(e){}});return n;})()`;

/** Fecha modais promocionais (Escape NAO fecha os eds-modal). */
export async function closePromoModals(page: Page): Promise<void> {
  for (let i = 0; i < 3; i++) {
    const n = (await evalJS<number>(page, CLOSE_MODALS_JS).catch(() => 0)) || 0;
    if (!n) break;
    await sleep(600);
  }
}

async function ensureHome(page: Page): Promise<void> {
  if (!/pas\/index/.test(page.url())) {
    await page.goto(ADS_HOME, { waitUntil: "networkidle2", timeout: 60_000 }).catch(() => undefined);
    await sleep(1500);
  }
  await guardLoginCaptcha(page);
  await closePromoModals(page);
}

/** Clica a aba certa. 'gmvmax' = aba interna "GMV Max da Loja";
 *  'manual' = aba EXTERNA cujo texto COMECA com "Anuncios de Produtos". */
function clickTabJS(mode: ControlMode): string {
  if (mode === "gmvmax") {
    return `(function(){var tabs=Array.prototype.slice.call(document.querySelectorAll('.eds-tabs__nav-tab'));var t=tabs.find(function(x){return ((x.innerText||x.textContent||'').replace(/\\s+/g,' ').trim().indexOf('GMV Max da Loja')>=0);});if(t){t.scrollIntoView({block:'center'});t.click();return true;}return false;})()`;
  }
  return `(function(){var tabs=Array.prototype.slice.call(document.querySelectorAll('.eds-tabs__nav-tab'));var t=tabs.find(function(x){var s=(x.innerText||x.textContent||'').replace(/\\s+/g,' ').trim();return s.indexOf('An\\u00fancios de Produtos')===0;});if(t){t.scrollIntoView({block:'center'});t.click();return true;}return false;})()`;
}

/** Sub-aba interna dos anuncios individuais (onde cada anuncio tem pausa propria):
 *  "Anuncios em grupo e anuncios individuais". Casa por "individuais" (sem acento). */
const CLICK_INDIVIDUAL_TAB_JS = `(function(){var tabs=Array.prototype.slice.call(document.querySelectorAll('.eds-tabs__nav-tab'));var t=tabs.find(function(x){var s=(x.innerText||x.textContent||'').replace(/\\s+/g,' ').trim().toLowerCase();return s.indexOf('individuais')>=0;});if(t){t.scrollIntoView({block:'center'});t.click();return (t.innerText||t.textContent||'').replace(/\\s+/g,' ').trim();}return null;})()`;

/** Navega ate a LISTA de anuncios individuais: aba externa "Anuncios de Produtos"
 *  e depois a sub-aba interna "Anuncios em grupo e anuncios individuais". */
async function gotoManualList(page: Page): Promise<void> {
  await ensureHome(page);
  await evalJS(page, clickTabJS("manual")).catch(() => undefined); // aba externa
  await sleep(1500);
  await evalJS(page, CLICK_INDIVIDUAL_TAB_JS).catch(() => undefined); // sub-aba interna
  await sleep(2500);
  await closePromoModals(page);
}

/** Verifica se a UI da lista "Por anuncio" REALMENTE renderizou: precisa ter os
 *  radios de filtro de status (Tudo/Em andamento/Pausado) E o container da tabela.
 *  Esses elementos existem mesmo quando o filtro retorna 0 linhas, entao servem
 *  pra distinguir "lista vazia de verdade" de "pagina nao carregou (em branco)". */
const MANUAL_UI_READY_JS = `(function(){
  var radios=[].slice.call(document.querySelectorAll('.eds-radio-button')).filter(function(e){return e.offsetParent!==null;});
  var hasFilter=radios.some(function(e){var t=(e.innerText||e.textContent||'').trim().toLowerCase();return t==='em andamento'||t==='pausado'||t==='tudo';});
  var hasTable=!!document.querySelector('.eds-table');
  var rows=document.querySelectorAll('.eds-table__row a[href*="pas/product/manual/"]').length;
  return {ready:!!(hasFilter&&hasTable),hasFilter:hasFilter,hasTable:hasTable,rows:rows};
})()`;

/** Navega ate a lista "Por anuncio" E confirma que a UI carregou (filtros+tabela).
 *  Recarrega/re-navega ate 3x pra curar telas em branco transitorias (ja vimos o
 *  AdsPower abrir a aba sem renderizar -> HTML de 0 bytes). Se nunca carregar, LANCA
 *  -> assim uma falha de carregamento NUNCA vira "nada a fazer/sucesso" silencioso. */
async function gotoManualListReady(page: Page, account: string): Promise<void> {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) {
      await page.reload({ waitUntil: "domcontentloaded" }).catch(() => undefined);
      await sleep(2500);
    }
    await gotoManualList(page);
    for (let i = 0; i < 6; i++) {
      const st = (await evalJS(page, MANUAL_UI_READY_JS).catch(() => ({ ready: false }))) as {
        ready: boolean;
      };
      if (st && st.ready) return;
      await sleep(1200);
    }
  }
  const shot = await dumpDebug(page, account, "manual-lista-nao-carregou");
  throw new Error(
    `Lista "Por anuncio" nao carregou (filtros/tabela ausentes apos 3 tentativas). ` +
      `Provavel tela em branco do AdsPower/sessao. Debug: ${shot}`
  );
}

// ---------------------------------------------------------------------------
// MODE 'gmvmax' - mestre da loja
// ---------------------------------------------------------------------------
const GMV_STATUS_JS = `(function(){var el=document.querySelector('.gms-campaign-status');if(!el)return 'unknown';var c=el.className.toString();return /ongoing/.test(c)?'on':(/paused/.test(c)?'off':(/ended/.test(c)?'ended':'unknown'));})()`;

const OPEN_GMV_MENU_JS = `(function(){var t=document.querySelector('.gms-overview-more-dropdown');if(!t)return false;try{t.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));t.click();}catch(e){return false;}return true;})()`;

/** Clica o item de menu visivel certo. kind: 'pause' -> "Pausar"; 'resume' -> ligar. */
function clickDropdownItemJS(kind: "pause" | "resume"): string {
  const test =
    kind === "pause"
      ? `function(t){return t==='Pausar';}`
      : `function(t){return /^(Ativar|Resumir|Retomar|Iniciar agora|Reativar|Iniciar)$/.test(t);}`;
  return `(function(){var ok=${test};var items=Array.prototype.slice.call(document.querySelectorAll('.eds-dropdown-item'));var vis=items.filter(function(it){return it.offsetParent!==null;});var seen=vis.map(function(it){return (it.innerText||it.textContent||'').trim();});var hit=vis.find(function(it){return ok((it.innerText||it.textContent||'').trim());});if(hit){hit.click();return {clicked:(hit.innerText||hit.textContent||'').trim(),seen:seen};}return {clicked:null,seen:seen};})()`;
}

const CONFIRM_MODAL_JS = `(function(){var modals=Array.prototype.slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(function(m){return m.offsetParent!==null;});if(!modals.length)return {ok:false};var modal=modals[modals.length-1];var btn=modal.querySelector('.eds-modal__footer .eds-button--primary, .eds-modal__box .eds-button--primary, .eds-button--primary');if(btn){var txt=(btn.innerText||btn.textContent||'').trim();btn.click();return {ok:true,text:txt};}return {ok:false};})()`;

/** TRUE se existe um modal VISIVEL com botao de PROSSEGUIR/CONFIRMAR. Casa por
 *  TEXTO (nunca pelo botao primario, pois no aviso de credito o primario e
 *  "Manter"/"Keep Settings" = CANCELAR):
 *    - "Confirmar"                      (Pausar/Resumir em Massa);
 *    - "Prosseguir..." / "Proceed..."   (aviso "X Ads Are In Tasks Now").
 *  Usado pra esperar abrir E pra esperar fechar. */
const PROCEED_BTN_TEST_JS = `function(t){t=(t||'').trim();return /^confirmar$/i.test(t)||/^prosseguir/i.test(t)||/^proceed\\b/i.test(t);}`;
const CONFIRM_MODAL_PRESENT_JS = `(function(){
  var isProceed=${PROCEED_BTN_TEST_JS};
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var modals=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(vis);
  for(var i=0;i<modals.length;i++){
    var btns=[].slice.call(modals[i].querySelectorAll('button,.eds-button')).filter(vis);
    var hit=btns.find(function(b){return isProceed(txt(b));});
    if(hit)return {ok:true,text:txt(hit)};
  }
  return {ok:false};
})()`;

/** Marca o botao de PROSSEGUIR/CONFIRMAR do modal visivel com data-attr pro clique
 *  por coordenada. Mesma regra de texto do present acima (jamais o primario). */
const TAG_CONFIRM_BTN_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-marionete-confirm]')).forEach(function(e){e.removeAttribute('data-marionete-confirm');});
  var isProceed=${PROCEED_BTN_TEST_JS};
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var modals=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(vis);
  for(var i=modals.length-1;i>=0;i--){
    var btns=[].slice.call(modals[i].querySelectorAll('button,.eds-button')).filter(vis);
    var btn=btns.find(function(b){return isProceed(txt(b));});
    if(btn){btn.setAttribute('data-marionete-confirm','1');return {ok:true,text:txt(btn)};}
  }
  return {ok:false};
})()`;

export async function gmvMaxStatus(page: Page): Promise<"on" | "off" | "ended" | "unknown"> {
  await ensureHome(page);
  await evalJS(page, clickTabJS("gmvmax")).catch(() => undefined);
  await sleep(1800);
  await closePromoModals(page);
  return (await evalJS(page, GMV_STATUS_JS).catch(() => "unknown")) as any;
}

export async function setGmvMax(
  page: Page,
  account: string,
  on: boolean
): Promise<ActionResult> {
  await ensureHome(page);
  await evalJS(page, clickTabJS("gmvmax")).catch(() => undefined);
  await sleep(2000);
  await closePromoModals(page);

  const status = (await evalJS(page, GMV_STATUS_JS).catch(() => "unknown")) as string;
  if (status === "unknown") {
    const shot = await dumpDebug(page, account, "gmvmax-sem-status");
    throw new Error(`GMV Max: status nao encontrado (.gms-campaign-status). Debug: ${shot}`);
  }
  if (status === "ended") {
    return { matched: 1, changed: 0, detail: "GMV Max encerrado (nao ha o que ligar/desligar)" };
  }
  const already = (on && status === "on") || (!on && status === "off");
  if (already) {
    return { matched: 1, changed: 0, detail: `GMV Max ja ${on ? "ligado" : "pausado"}` };
  }

  const opened = await evalJS<boolean>(page, OPEN_GMV_MENU_JS).catch(() => false);
  if (!opened) {
    const shot = await dumpDebug(page, account, "gmvmax-sem-menu");
    throw new Error(`GMV Max: botao "Mais" (.gms-overview-more-dropdown) nao achado. Debug: ${shot}`);
  }
  await humanPause();
  const clicked = (await evalJS(page, clickDropdownItemJS(on ? "resume" : "pause"))) as {
    clicked: string | null;
    seen: string[];
  };
  if (!clicked || !clicked.clicked) {
    const shot = await dumpDebug(page, account, "gmvmax-sem-item");
    throw new Error(
      `GMV Max: item "${on ? "Ativar/Resumir" : "Pausar"}" nao achado (vistos: ${
        clicked ? clicked.seen.join(", ") : "?"
      }). Debug: ${shot}`
    );
  }
  await humanPause();
  await evalJS(page, CONFIRM_MODAL_JS).catch(() => undefined);
  await sleep(2000);
  await closePromoModals(page);

  const after = (await evalJS(page, GMV_STATUS_JS).catch(() => "unknown")) as string;
  const success = (on && after === "on") || (!on && after === "off");
  return {
    matched: 1,
    changed: 1,
    detail: `GMV Max ${status} -> ${after} (item "${clicked.clicked}")${
      success ? "" : " [verificar: status pos-acao nao confirmou]"
    }`,
  };
}

// ---------------------------------------------------------------------------
// MODE 'manual' - anuncio por anuncio (aba "Anuncios de Produtos")
// ---------------------------------------------------------------------------
interface ManualAd {
  id: string;
  name: string;
  status: "on" | "off" | "ended" | "unknown";
}

const LIST_MANUAL_ADS_JS = `(function(){
  var rows=Array.prototype.slice.call(document.querySelectorAll('.eds-table__row'));
  var out=[];
  rows.forEach(function(r){
    var link=r.querySelector('a[href*="pas/product/manual/"]');
    if(!link)return;
    var m=(link.getAttribute('href')||'').match(/pas\\/product\\/manual\\/(\\d+)/);
    var id=m?m[1]:'';
    if(!id)return;
    var st=r.querySelector('.campaign-state-new');
    var cls=st?st.className.toString():'';
    var status=/ongoing/.test(cls)?'on':(/paused/.test(cls)?'off':(/ended/.test(cls)?'ended':'unknown'));
    var name=(link.innerText||link.textContent||'').trim().replace(/\\s+/g,' ');
    if(!name){name=(r.innerText||'').trim().replace(/\\s+/g,' ').slice(0,80);}
    out.push({id:id,name:name.slice(0,90),status:status});
  });
  // dedup por id (tabela fixa+scroll pode repetir a linha)
  var seen={};return out.filter(function(a){if(seen[a.id])return false;seen[a.id]=1;return true;});
})()`;

// Anuncios individuais NAO tem menu por linha. O controle de status e o dropdown
// "Alterar Status" da barra de ferramentas, que so aparece ao MARCAR o checkbox da
// linha. O dropdown (eds) so abre com clique REAL (CDP), nao com evento sintetico.
// Itens confirmados: "Pausar", "Resumir", "Encerrar", "Iniciar agora", "Excluir".

/** Desmarca TODAS as linhas marcadas. */
const UNSELECT_ALL_ROWS_JS = `(function(){[].slice.call(document.querySelectorAll('.eds-table__row input[type=checkbox]:checked')).forEach(function(inp){var w=inp.closest('.eds-checkbox');var clk=(w&&w.querySelector('.eds-checkbox__input'))||inp;try{clk.click();}catch(e){}});return true;})()`;

/** Marca o checkbox de SELECT-ALL do cabecalho da tabela (seleciona a pagina inteira
 *  de uma vez). Exclui o 'roas-protection-checkbox' (toggle de Protecao ROAS, que NAO
 *  seleciona linhas) e clica no '.eds-checkbox__input' (span) — clicar no <input>
 *  escondido nao dispara o handler do eds. */
const SELECT_ALL_HEADER_JS = `(function(){
  var roas=function(w){return w && /roas-protection/.test(w.className||'');};
  var vis=function(el){return el && el.offsetParent!==null;};
  var headers=[].slice.call(document.querySelectorAll('.eds-table__header'));
  var wrap=null;
  for(var h=0;h<headers.length && !wrap;h++){
    wrap=[].slice.call(headers[h].querySelectorAll('.eds-checkbox')).find(function(w){return !roas(w)&&vis(w);});
  }
  if(!wrap){
    var rows=[].slice.call(document.querySelectorAll('.eds-table__row a[href*="pas/product/manual/"]')).map(function(a){return a.closest('.eds-table__row');});
    if(rows.length){
      var rTop=rows[0].getBoundingClientRect().top;
      wrap=[].slice.call(document.querySelectorAll('.eds-checkbox')).find(function(w){if(roas(w)||!vis(w)||w.closest('.eds-table__row'))return false;var r=w.getBoundingClientRect();return r.top<rTop&&r.top>0;});
    }
  }
  if(!wrap)return {ok:false,reason:'sem checkbox de cabecalho'};
  var clk=wrap.querySelector('.eds-checkbox__input')||wrap;
  clk.scrollIntoView({block:'center'});clk.click();
  return {ok:true,headerCls:(wrap.className||'').slice(0,60)};
})()`;

/** Conta quantas LINHAS estao marcadas (tolera linha duplicada fixo+scroll). */
const COUNT_CHECKED_ROWS_JS = `(function(){
  var n=[].slice.call(document.querySelectorAll('.eds-table__row .eds-checkbox')).filter(function(w){return /checked|is-checked/.test(w.className||'')||(w.querySelector('input')&&w.querySelector('input').checked);}).length;
  return {checkedRows:n};
})()`;

/** Marca o item EXATO do menu "Alterar Status" (Pausar/Resumir) pro clique real.
 *  Casamento por texto EXATO — nunca pega Encerrar/Excluir/Iniciar agora por engano. */
function tagItemExactJS(label: string): string {
  return `(function(){
    [].slice.call(document.querySelectorAll('[data-marionete-item]')).forEach(function(e){e.removeAttribute('data-marionete-item');});
    var L=${JSON.stringify(label)};
    var txt=function(el){return (el.innerText||el.textContent||'').trim();};
    var items=[].slice.call(document.querySelectorAll('.eds-dropdown-item,.eds-dropdown-menu__item,[role="menuitem"]')).filter(function(it){return it.offsetParent!==null;});
    var seen=items.map(txt);
    var hit=items.find(function(it){return txt(it)===L;});
    if(hit){hit.setAttribute('data-marionete-item','1');return {found:true,label:txt(hit),seen:seen};}
    return {found:false,seen:seen};
  })()`;
}

/** Clica o radio do filtro "Status do Anuncio" (Tudo/Em andamento/Pausado/...).
 *  Casa por texto exato (case-insensitive). Retorna true se achou. */
function statusFilterJS(label: string): string {
  return `(function(){
    var L=${JSON.stringify(label)}.toLowerCase();
    var rb=[].slice.call(document.querySelectorAll('.eds-radio-button')).find(function(e){return (e.innerText||e.textContent||'').trim().toLowerCase()===L;});
    if(rb){rb.scrollIntoView({block:'center'});rb.click();return true;}return false;
  })()`;
}

/** Garante que SO a linha {id} esteja marcada (desmarca o resto, marca esta). */
function selectOnlyRowJS(id: string): string {
  return `(function(){
    var ID=${JSON.stringify(id)};
    [].slice.call(document.querySelectorAll('.eds-table__row input[type=checkbox]:checked')).forEach(function(inp){var w=inp.closest('.eds-checkbox');var clk=(w&&w.querySelector('.eds-checkbox__input'))||inp;try{clk.click();}catch(e){}});
    var sel='a[href*="pas/product/manual/'+ID+'"]';
    var rows=[].slice.call(document.querySelectorAll('.eds-table__row'));
    var row=rows.find(function(r){return !!r.querySelector(sel);});
    if(!row)return {ok:false,reason:'linha nao achada'};
    row.scrollIntoView({block:'center'});
    var input=row.querySelector('input[type=checkbox]');
    if(input&&input.checked)return {ok:true,already:true};
    var clk=row.querySelector('.eds-checkbox__input')||input;
    if(clk){clk.click();return {ok:true,already:false};}
    return {ok:false,reason:'sem checkbox'};
  })()`;
}

/** Acha o gatilho "Alterar Status" (so existe apos selecionar) e marca com
 *  data-attr para o Node clicar de VERDADE (page.click). Limpa tags antigas. */
const TAG_STATUS_TRIGGER_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-marionete-status]')).forEach(function(e){e.removeAttribute('data-marionete-status');});
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var vis=function(el){return el && el.offsetParent!==null;};
  var cands=[].slice.call(document.querySelectorAll('.eds-dropdown,button,.eds-button')).filter(vis);
  var host=cands.find(function(e){return /^alterar status$/i.test(txt(e));}) || cands.find(function(e){return /alterar status/i.test(txt(e));});
  if(!host)return {ok:false};
  host.setAttribute('data-marionete-status','1');
  return {ok:true};
})()`;

/** Apos abrir o menu, marca o item certo (Pausar/Resumir) com data-attr e devolve
 *  o que viu (pra diagnostico). Limpa tags antigas de item. */
function tagStatusItemJS(on: boolean): string {
  const test = on
    ? `function(t){return /^(Resumir|Ativar|Retomar|Reativar|Iniciar agora|Iniciar)$/.test(t);}`
    : `function(t){return t==='Pausar';}`;
  return `(function(){
    [].slice.call(document.querySelectorAll('[data-marionete-item]')).forEach(function(e){e.removeAttribute('data-marionete-item');});
    var ok=${test};
    var txt=function(el){return (el.innerText||el.textContent||'').trim();};
    var items=[].slice.call(document.querySelectorAll('.eds-dropdown-item,.eds-dropdown-menu__item,[role="menuitem"]')).filter(function(it){return it.offsetParent!==null;});
    var seen=items.map(txt);
    var hit=items.find(function(it){return ok(txt(it));});
    if(hit){hit.setAttribute('data-marionete-item','1');return {found:true,label:txt(hit),seen:seen};}
    return {found:false,seen:seen};
  })()`;
}

/** Le o status atual de UM anuncio (pra confirmar a mudanca). */
function statusOfJS(id: string): string {
  return `(function(){
    var ID=${JSON.stringify(id)};
    var sel='a[href*="pas/product/manual/'+ID+'"]';
    var rows=[].slice.call(document.querySelectorAll('.eds-table__row'));
    var row=rows.find(function(r){return !!r.querySelector(sel);});
    if(!row)return 'gone';
    var st=row.querySelector('.campaign-state-new');var c=st?st.className.toString():'';
    return /ongoing/.test(c)?'on':(/paused/.test(c)?'off':(/ended/.test(c)?'ended':'unknown'));
  })()`;
}

/** Aplica Pausar/Resumir em UM anuncio via fluxo "Alterar Status" (clique real). */
async function applyOneManual(
  page: Page,
  account: string,
  ad: ManualAd,
  on: boolean,
  target: "on" | "off"
): Promise<{ changed: boolean; detail: string }> {
  const sel = (await evalJS(page, selectOnlyRowJS(ad.id))) as { ok: boolean; reason?: string };
  if (!sel || !sel.ok) return { changed: false, detail: `${ad.id}: selecao falhou (${sel?.reason || "?"})` };
  await sleep(900);

  const tagged = (await evalJS(page, TAG_STATUS_TRIGGER_JS)) as { ok: boolean };
  if (!tagged || !tagged.ok) {
    await dumpDebug(page, account, "manual-sem-alterar-status");
    await evalJS(page, UNSELECT_ALL_ROWS_JS).catch(() => undefined);
    return { changed: false, detail: `${ad.id}: "Alterar Status" nao apareceu` };
  }
  // Abre o dropdown eds por COORDENADA (page.click falha em barra sticky -> no-op).
  await clickCenter(page, '[data-marionete-status="1"]');
  await sleep(1000);

  const item = (await evalJS(page, tagStatusItemJS(on))) as {
    found: boolean;
    label?: string;
    seen: string[];
  };
  if (!item || !item.found) {
    await dumpDebug(page, account, "manual-sem-item");
    await page.keyboard.press("Escape").catch(() => undefined);
    await evalJS(page, UNSELECT_ALL_ROWS_JS).catch(() => undefined);
    return {
      changed: false,
      detail: `${ad.id}: item ${on ? "Resumir" : "Pausar"} nao achado (vistos: ${(item?.seen || []).join(",")})`,
    };
  }
  // Clica o item do menu por COORDENADA.
  await clickCenter(page, '[data-marionete-item="1"]');
  await humanPause();
  // Pode (ou nao) abrir um modal de confirmacao -> confirma se houver.
  await evalJS(page, CONFIRM_MODAL_JS).catch(() => undefined);
  await sleep(1800);
  await closePromoModals(page);

  const after = (await evalJS(page, statusOfJS(ad.id)).catch(() => "unknown")) as string;
  await evalJS(page, UNSELECT_ALL_ROWS_JS).catch(() => undefined);
  await sleep(500);

  // Sucesso = ficou no estado alvo OU saiu da lista filtrada (acionaveis):
  // sob o filtro "Em andamento"/"Pausado", o anuncio mudado SOME da lista.
  if (after === target) {
    return { changed: true, detail: `${ad.id}: ${ad.status} -> ${target} (item "${item.label}")` };
  }
  if (after === "gone") {
    return { changed: true, detail: `${ad.id}: ${ad.status} -> ${target} (saiu da lista; item "${item.label}")` };
  }
  return { changed: false, detail: `${ad.id}: aplicado "${item.label}", status=${after} (verificar)` };
}

// Le os anuncios que casam com o filtro na pagina/abas atuais, varrendo paginas.
async function collectMatching(
  page: Page,
  matches: (a: ManualAd) => boolean,
  maxPages = 30
): Promise<ManualAd[]> {
  const seen = new Map<string, ManualAd>();
  for (let pg = 0; pg < maxPages; pg++) {
    const ads = (await evalJS<ManualAd[]>(page, LIST_MANUAL_ADS_JS).catch(() => [])) || [];
    for (const a of ads) if (matches(a) && !seen.has(a.id)) seen.set(a.id, a);
    const moved = await goNextPage(page);
    if (!moved) break;
    await sleep(1800);
    await closePromoModals(page);
  }
  return [...seen.values()];
}

/**
 * Pausa/retoma anuncios individuais via o fluxo "Alterar Status".
 * Estrategia: filtra a lista pelo status ACIONAVEL (Em andamento p/ pausar,
 * Pausado p/ retomar) - assim so aparecem os anuncios que ainda precisam mudar,
 * e cada acao bem-sucedida FAZ o anuncio sumir da lista (verificacao natural,
 * imune a paginacao/reordenacao da loja com >20 anuncios).
 */
async function setManualAdsByList(
  page: Page,
  account: string,
  on: boolean,
  filter: ManualFilter
): Promise<ActionResult> {
  await gotoManualListReady(page, account);

  const target = on ? "on" : "off";
  const actionableLabel = on ? "Pausado" : "Em andamento";
  const wantList = (filter.list || []).map((s) => s.toLowerCase().trim()).filter(Boolean);
  const matches = (a: ManualAd): boolean => {
    if (filter.scope === "all") return true;
    if (filter.scope === "ids") return wantList.includes(a.id.toLowerCase());
    if (filter.scope === "names") return wantList.some((n) => a.name.toLowerCase().includes(n));
    return false;
  };

  // Filtra para mostrar SO os anuncios acionaveis (que ainda precisam mudar).
  const filtered = await evalJS<boolean>(page, statusFilterJS(actionableLabel)).catch(() => false);
  await sleep(2500);
  await closePromoModals(page);

  let changed = 0;
  const details: string[] = [];
  const failed = new Set<string>();
  const attempted = new Set<string>();
  const MAX_ACTIONS = filter.scope === "all" ? 500 : wantList.length + 5;

  for (let i = 0; i < MAX_ACTIONS; i++) {
    const ads = (await evalJS<ManualAd[]>(page, LIST_MANUAL_ADS_JS).catch(() => [])) || [];
    const next = ads.find(
      (a) => matches(a) && (a.status === "on" || a.status === "off") && !failed.has(a.id)
    );
    if (!next) {
      const moved = await goNextPage(page); // fallback: outra pagina de acionaveis
      if (!moved) break;
      await sleep(1800);
      await closePromoModals(page);
      continue;
    }
    attempted.add(next.id);
    const res = await applyOneManual(page, account, next, on, target);
    if (res.changed) changed++;
    else failed.add(next.id);
    details.push(res.detail);
    await sleep(700);
    if (filter.scope !== "all" && changed + failed.size >= wantList.length) break;
  }

  // Nada acionavel: ou ja esta tudo no estado alvo (no-op), ou o alvo nao existe.
  if (attempted.size === 0) {
    if (filter.scope !== "all") {
      await evalJS(page, statusFilterJS("Tudo")).catch(() => undefined);
      await sleep(2000);
      await closePromoModals(page);
      const all = await collectMatching(page, matches);
      if (all.length === 0) {
        const shot = await dumpDebug(page, account, "manual-sem-anuncios");
        throw new Error(
          `Manual: nenhum anuncio para filtro ${filter.scope}=${(filter.list || []).join(",")}. ` +
            `(filtro de status aplicado=${filtered}) Debug: ${shot}`
        );
      }
      const already = all.filter((a) => a.status === target).length;
      return {
        matched: all.length,
        changed: 0,
        detail: `nada a fazer: ${already}/${all.length} ja em '${target}'`,
      };
    }
    return { matched: 0, changed: 0, detail: `nenhum anuncio acionavel para ${on ? "retomar" : "pausar"}` };
  }

  return { matched: attempted.size, changed, detail: details.slice(0, 12).join("; ") };
}

/**
 * scope='all': pausa/retoma TODOS os anuncios individuais via SELECT-ALL do
 * cabecalho (1 acao em massa por pagina), drenando pelo filtro acionavel. Muito
 * mais rapido e estavel que o um-a-um. So casa o item EXATO Pausar/Resumir
 * (nunca Encerrar/Excluir). Validado ao vivo (Inova, 36 anuncios, varios ciclos).
 */
async function setAllManualBulk(
  page: Page,
  account: string,
  on: boolean
): Promise<ActionResult> {
  await gotoManualListReady(page, account);
  const target = on ? "on" : "off";
  const actionableLabel = on ? "Pausado" : "Em andamento";
  const itemLabel = on ? "Resumir" : "Pausar";

  await evalJS(page, statusFilterJS(actionableLabel)).catch(() => false);
  await sleep(2500);
  await closePromoModals(page);

  // Anuncios ja TENTADOS (por id) + nº de tentativas reais. Se um anuncio segue
  // "acionavel" depois de MAX_AD_TRIES tentativas (menu aberto + item clicado),
  // tratamos como PRESO: ao RETOMAR, quase sempre e produto ESGOTADO e a Shopee
  // recusa reativar. Nao insistimos (evita loop/tempestade) nem derrubamos o
  // comando — so relatamos quantos ficaram de fora.
  const attempts = new Map<string, number>();
  const MAX_AD_TRIES = 2;
  let lotes = 0;
  const MAX_LOOPS = 15; // cada lote limpa uma pagina (~20); cobre lojas grandes
  for (let i = 0; i < MAX_LOOPS; i++) {
    const ads = (await evalJS<ManualAd[]>(page, LIST_MANUAL_ADS_JS).catch(() => [])) || [];
    const actionable = ads.filter((a) => a.status === "on" || a.status === "off");
    if (actionable.length === 0) break; // filtro acionavel vazio -> tudo no alvo
    // So insiste em quem ainda tem tentativas; se todo o restante ja estourou o
    // limite, sao PRESOS (esgotado/erro) -> para de girar em vez de repetir.
    const fresh = actionable.filter((a) => (attempts.get(a.id) || 0) < MAX_AD_TRIES);
    if (fresh.length === 0) break;

    // (1) SELECT-ALL com retry: clica o checkbox de cabecalho e ESPERA (poll) o eds
    //     registrar a selecao. O clique do eds as vezes nao "pega" de primeira
    //     (corrida de render) -> re-tenta ate 3x em vez de um sleep fixo.
    let checked = 0;
    let headerFound = false;
    for (let selTry = 0; selTry < 3 && !checked; selTry++) {
      if (selTry > 0) {
        await evalJS(page, UNSELECT_ALL_ROWS_JS).catch(() => undefined); // limpa meia-selecao
        await sleep(600);
      }
      const sel = (await evalJS(page, SELECT_ALL_HEADER_JS)) as { ok: boolean; reason?: string };
      if (!sel || !sel.ok) {
        await sleep(700); // cabecalho pode nao ter renderizado ainda -> espera e re-tenta
        continue;
      }
      headerFound = true;
      const chk = await pollUntil<{ checkedRows: number }>(
        page,
        COUNT_CHECKED_ROWS_JS,
        (r) => !!r && r.checkedRows > 0,
        4000,
        250
      );
      if (chk && chk.checkedRows > 0) checked = chk.checkedRows;
    }
    if (!checked) {
      const label = headerFound ? "bulk-selecao-vazia" : "bulk-sem-selectall";
      const why = headerFound ? "select-all nao marcou nenhuma linha" : "checkbox de cabecalho nao encontrado";
      const shot = await dumpDebug(page, account, label);
      throw new Error(`Bulk ${on ? "resume" : "pause"}: ${why}. Debug: ${shot}`);
    }

    // Conta a tentativa REAL de cada anuncio selecionado (mesmo que a acao nao
    // "pegue" adiante) -> alimenta a deteccao de PRESO/esgotado la em cima.
    for (const a of actionable) attempts.set(a.id, (attempts.get(a.id) || 0) + 1);

    // (2) "Alterar Status" so existe com algo selecionado e pode demorar a renderizar
    //     -> ESPERA (poll) o gatilho aparecer em vez de checar uma vez so.
    const trig = await pollUntil<{ ok: boolean }>(
      page,
      TAG_STATUS_TRIGGER_JS,
      (r) => !!r && r.ok,
      6000,
      250
    );
    if (!trig || !trig.ok) {
      const shot = await dumpDebug(page, account, "bulk-sem-alterar-status");
      throw new Error(`Bulk ${on ? "resume" : "pause"}: "Alterar Status" nao apareceu apos selecionar. Debug: ${shot}`);
    }

    // (3) Abre o dropdown e ESPERA o item aparecer. O 1o clique as vezes nao abre o
    //     menu eds (corrida) -> hover+click REAL e re-tenta ate 4x, fechando com
    //     Escape entre as tentativas. Para assim que o item aparece.
    let item: { found: boolean; label?: string; seen?: string[] } | undefined;
    for (let openTry = 0; openTry < 4; openTry++) {
      const t = (await evalJS(page, TAG_STATUS_TRIGGER_JS)) as { ok: boolean };
      if (t && t.ok) {
        await clickCenter(page, '[data-marionete-status="1"]'); // clique por coordenada (abre o menu sticky)
      }
      item = await pollUntil<{ found: boolean; label?: string; seen?: string[] }>(
        page,
        tagItemExactJS(itemLabel),
        (r) => !!r && r.found,
        1800,
        200
      );
      if (item && item.found) break;
      await page.keyboard.press("Escape").catch(() => undefined); // fecha meia-abertura
      await sleep(500);
    }
    if (!item || !item.found) {
      const shot = await dumpDebug(page, account, "bulk-sem-item");
      await page.keyboard.press("Escape").catch(() => undefined);
      throw new Error(`Bulk: item "${itemLabel}" nao achado (vistos: ${(item?.seen || []).join(",")}). Debug: ${shot}`);
    }
    await clickCenter(page, '[data-marionete-item="1"]'); // abre o modal de confirmacao
    // Confirma DE VERDADE (poll + clique por coordenada + espera fechar).
    const confirmed = await confirmBulkModal(page);
    if (!confirmed) {
      // Menu abriu e clicamos o item de status, mas a Shopee NAO confirmou. Ao
      // RETOMAR, e o sintoma tipico de produto ESGOTADO (a loja recusa reativar).
      // NAO derruba o comando: fecha o aviso e segue. O anuncio ja foi contado
      // como tentado e, apos MAX_AD_TRIES, sai do loop como preso (relatado).
      await page.keyboard.press("Escape").catch(() => undefined);
      await closePromoModals(page);
      await evalJS(page, UNSELECT_ALL_ROWS_JS).catch(() => undefined);
      if (!on) {
        // Pausar nunca deveria ser recusado -> mantem rigido (deixa o executor
        // re-tentar; assim uma falha transitoria de UI se cura na proxima).
        const shot = await dumpDebug(page, account, "bulk-sem-confirmar");
        throw new Error(`Bulk pause: confirmacao nao aplicada (modal nao fechou). Debug: ${shot}`);
      }
      await sleep(800);
      await evalJS(page, statusFilterJS(actionableLabel)).catch(() => undefined);
      await sleep(1500);
      continue;
    }
    await sleep(1500);
    await closePromoModals(page);
    lotes++;
    // Re-aplica o filtro (a tela pode ter recarregado) pra proxima leva.
    await evalJS(page, statusFilterJS(actionableLabel)).catch(() => undefined);
    await sleep(1800);
  }

  // Contagem HONESTA: relista TODAS as paginas do filtro acionavel (o restante
  // pode ficar em mais de uma pagina) e cruza com os que tentamos:
  //   mudou = tentados que SAIRAM do filtro; preso = tentados que continuam la
  //   (ao ligar: provavel produto ESGOTADO/erro).
  const stillAds = await collectMatching(page, (a) => a.status === "on" || a.status === "off");
  const stillActionable = new Set(stillAds.map((a) => a.id));
  let changed = 0;
  let stuck = 0;
  for (const id of attempts.keys()) {
    if (stillActionable.has(id)) stuck++;
    else changed++;
  }
  if (changed === 0 && stuck === 0) {
    return { matched: 0, changed: 0, detail: `nada a fazer: ja estava tudo em '${target}'` };
  }
  const stuckNote = stuck
    ? on
      ? `; ${stuck} nao ativado(s) — provavel produto ESGOTADO/erro`
      : `; ATENCAO restam ${stuck} nao pausado(s)`
    : "";
  return {
    matched: changed + stuck,
    changed,
    detail: `bulk ${on ? "resume" : "pause"}: ${changed} anuncio(s) em ${lotes} lote(s)${stuckNote}`,
  };
}

/** Dispatcher do modo manual: scope='all' usa o bulk select-all (rapido);
 *  ids/names usam o fluxo um-a-um (preciso para alvos especificos). */
async function setManualAds(
  page: Page,
  account: string,
  on: boolean,
  filter: ManualFilter
): Promise<ActionResult> {
  if (filter.scope === "all") return setAllManualBulk(page, account, on);
  return setManualAdsByList(page, account, on, filter);
}

const NEXT_PAGE_JS = `(function(){var n=document.querySelector('.eds-pagination__btn-next:not(.is-disabled), .eds-pager__next:not(.is-disabled), li.eds-pagination__item--next:not(.is-disabled)');if(n){n.scrollIntoView({block:'center'});n.click();return true;}return false;})()`;
async function goNextPage(page: Page): Promise<boolean> {
  return (await evalJS<boolean>(page, NEXT_PAGE_JS).catch(() => false)) || false;
}

// ---------------------------------------------------------------------------
// Dispatcher publico (usado pelo executor)
// ---------------------------------------------------------------------------
/** Aplica o estado desejado (on/off) no modo indicado. on=true liga; on=false pausa. */
export async function applyState(
  page: Page,
  account: string,
  mode: ControlMode,
  on: boolean,
  filter?: ManualFilter
): Promise<ActionResult> {
  requireCalibrated();
  if (mode === "gmvmax") return setGmvMax(page, account, on);
  return setManualAds(page, account, on, filter || { scope: "all" });
}

// ---------------------------------------------------------------------------
// Dry-run READ-ONLY (relata os dois modos, sem mudar nada)
// ---------------------------------------------------------------------------
export async function dryRun(page: Page, account = "dry-run"): Promise<{
  url: string;
  gmvMax: string;
  manualCount: number;
  manualSample: ManualAd[];
  debug: string | null;
}> {
  await ensureHome(page);
  let gmv = "unknown";
  try {
    gmv = await gmvMaxStatus(page);
  } catch {
    /* ignore */
  }
  let manualCount = 0;
  let manualSample: ManualAd[] = [];
  try {
    await gotoManualList(page);
    const ads = (await evalJS<ManualAd[]>(page, LIST_MANUAL_ADS_JS).catch(() => [])) || [];
    manualCount = ads.length;
    manualSample = ads.slice(0, 5);
  } catch {
    /* ignore */
  }
  const debug = await dumpDebug(page, account, "dry-run");
  return { url: page.url(), gmvMax: gmv, manualCount, manualSample, debug };
}

// ---------------------------------------------------------------------------
// DIAGNOSTICO READ-ONLY do dropdown "Alterar Status" (NAO altera status)
// Reproduz select-all -> marca o gatilho, relata a GEOMETRIA (rect, ponto de
// clique, quem esta no topo daquele ponto, pilha de overlays, viewport/scroll)
// e testa varias estrategias de clique pra ver QUAL realmente abre o menu.
// Nunca clica num item nem confirma -> efeito = somente leitura.
// ---------------------------------------------------------------------------
const DIAG_COUNT_ITEMS_JS = `(function(){
  var items=[].slice.call(document.querySelectorAll('.eds-dropdown-item,.eds-dropdown-menu__item,[role="menuitem"]')).filter(function(it){return it.offsetParent!==null;});
  return {count:items.length, texts:items.map(function(it){return (it.innerText||it.textContent||'').trim();}).slice(0,12)};
})()`;

const DIAG_GEOMETRY_JS = `(function(){
  function clsOf(el){return (el&&typeof el.className==='string')?el.className:((el&&el.getAttribute&&el.getAttribute('class'))||'');}
  function info(el){
    if(!el)return null;
    var r=el.getBoundingClientRect();var cs=getComputedStyle(el);
    return {tag:(el.tagName||'').toLowerCase(),cls:clsOf(el).slice(0,90),
      rect:{x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height),bottom:Math.round(r.bottom),right:Math.round(r.right)},
      visible:el.offsetParent!==null,display:cs.display,visibility:cs.visibility,pointerEvents:cs.pointerEvents,zIndex:cs.zIndex,position:cs.position};
  }
  var vw=window.innerWidth,vh=window.innerHeight;
  function pointInfo(el){
    if(!el)return null;
    var r=el.getBoundingClientRect();
    var cx=Math.min(Math.max(r.left+r.width/2,1),vw-1);
    var cy=Math.min(Math.max(r.top+r.height/2,1),vh-1);
    var top=document.elementFromPoint(cx,cy);
    var stack=(document.elementsFromPoint(cx,cy)||[]).slice(0,6).map(function(e){return (e.tagName||'').toLowerCase()+'.'+clsOf(e).slice(0,40);});
    return {cx:Math.round(cx),cy:Math.round(cy),
      fullyInViewport:(r.top>=0&&r.bottom<=vh&&r.left>=0&&r.right<=vw),
      topTag:top?(top.tagName||'').toLowerCase():null,topCls:top?clsOf(top).slice(0,90):null,
      hitsTrigger:top?(el.contains(top)||top.contains(el)||top===el):false,
      stack:stack};
  }
  var host=document.querySelector('[data-marionete-status="1"]');
  if(!host)return {ok:false,reason:'host nao marcado'};
  var btn=(host.matches&&host.matches('button,.eds-button'))?host:(host.querySelector('button,.eds-button')||null);
  return {ok:true,viewport:{w:vw,h:vh,scrollX:Math.round(window.scrollX),scrollY:Math.round(window.scrollY)},
    host:info(host),button:info(btn),hostPoint:pointInfo(host),buttonPoint:pointInfo(btn)};
})()`;

const DIAG_TAG_BTN_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-marionete-btn]')).forEach(function(e){e.removeAttribute('data-marionete-btn');});
  var host=document.querySelector('[data-marionete-status="1"]');if(!host)return {ok:false};
  var btn=(host.matches&&host.matches('button,.eds-button'))?host:(host.querySelector('button,.eds-button')||null);
  if(!btn)return {ok:false,reason:'sem button interno'};
  btn.setAttribute('data-marionete-btn','1');return {ok:true,tag:(btn.tagName||'').toLowerCase()};
})()`;

const DIAG_SYNTH_CLICK_JS = `(function(){var b=document.querySelector('[data-marionete-btn]')||document.querySelector('[data-marionete-status="1"]');if(!b)return false;try{b.click();}catch(e){return false;}return true;})()`;

const DIAG_DISPATCH_SEQ_JS = `(function(){
  var b=document.querySelector('[data-marionete-btn]')||document.querySelector('[data-marionete-status="1"]');
  if(!b)return {ok:false};
  var r=b.getBoundingClientRect();var cx=r.left+r.width/2,cy=r.top+r.height/2;
  var opt={bubbles:true,cancelable:true,view:window,clientX:cx,clientY:cy,button:0};
  ['pointerover','pointerenter','mouseover','mouseenter','pointermove','mousemove','pointerdown','mousedown','pointerup','mouseup','click'].forEach(function(t){
    var P=(t.indexOf('pointer')===0&&window.PointerEvent)?PointerEvent:MouseEvent;
    try{b.dispatchEvent(new P(t,opt));}catch(e){try{b.dispatchEvent(new MouseEvent(t.replace('pointer','mouse'),opt));}catch(_){}}
  });
  return {ok:true};
})()`;

const DIAG_OUTSIDE_CLICK_JS = `(function(){var o={bubbles:true,cancelable:true,view:window,clientX:2,clientY:2,button:0};['pointerdown','mousedown','pointerup','mouseup','click'].forEach(function(t){var P=(t.indexOf('pointer')===0&&window.PointerEvent)?PointerEvent:MouseEvent;try{document.body.dispatchEvent(new P(t,o));}catch(e){}});return true;})()`;

const DIAG_SCROLL_HOST_JS = `(function(){var h=document.querySelector('[data-marionete-status="1"]');if(h)h.scrollIntoView({block:'center',inline:'center'});return true;})()`;

export async function diagBulkOpen(page: Page, account = "diag"): Promise<any> {
  const report: any = { account, strategies: [] as any[] };
  await gotoManualListReady(page, account);
  report.url = page.url();

  await evalJS(page, statusFilterJS("Em andamento")).catch(() => false);
  await sleep(2500);
  await closePromoModals(page);

  const ads = (await evalJS<ManualAd[]>(page, LIST_MANUAL_ADS_JS).catch(() => [])) || [];
  report.totalRows = ads.length;
  report.actionable = ads.filter((a) => a.status === "on" || a.status === "off").length;

  const sel = (await evalJS(page, SELECT_ALL_HEADER_JS)) as any;
  report.selectAll = sel;
  await sleep(1100);
  const chk = (await evalJS(page, COUNT_CHECKED_ROWS_JS).catch(() => ({ checkedRows: 0 }))) as any;
  report.checkedRows = chk.checkedRows;

  const trig = (await evalJS(page, TAG_STATUS_TRIGGER_JS)) as any;
  report.trigger = trig;
  if (!trig || !trig.ok) {
    report.fatal = "Alterar Status nao apareceu apos selecionar";
    report.debug = await dumpDebug(page, account, "diag-sem-trigger");
    return report;
  }

  report.geometry = await evalJS(page, DIAG_GEOMETRY_JS).catch(() => null);

  const countItems = async () =>
    ((await evalJS(page, DIAG_COUNT_ITEMS_JS).catch(() => ({ count: 0, texts: [] }))) as any);
  const closeMenu = async () => {
    await page.keyboard.press("Escape").catch(() => undefined);
    await sleep(300);
    let c = await countItems();
    if (c.count > 0) {
      await evalJS(page, DIAG_OUTSIDE_CLICK_JS).catch(() => undefined);
      await sleep(300);
      c = await countItems();
    }
    return c.count;
  };

  const runStrategy = async (name: string, action: () => Promise<void>) => {
    const pre = (await countItems()).count;
    let err: string | undefined;
    try {
      await action();
    } catch (e: any) {
      err = String((e && e.message) || e);
    }
    await sleep(1200);
    const c = await countItems();
    report.strategies.push({
      name,
      preCount: pre,
      postCount: c.count,
      openedByThis: pre === 0 && c.count > 0,
      texts: c.texts,
      ...(err ? { err } : {}),
    });
    const leftover = await closeMenu();
    if (leftover > 0) report.strategies[report.strategies.length - 1].closeFailedLeftover = leftover;
  };

  await closeMenu(); // garante inicio limpo

  // A: producao atual — page.click no WRAPPER .eds-dropdown marcado
  await runStrategy("A page.click(host wrapper)", async () => {
    await page.click('[data-marionete-status="1"]');
  });

  // marca o button interno pras proximas estrategias
  report.buttonTag = (await evalJS(page, DIAG_TAG_BTN_JS).catch(() => null)) as any;

  // C: page.click no BOTAO interno
  if (report.buttonTag && report.buttonTag.ok) {
    await runStrategy("C page.click(inner button)", async () => {
      await page.click('[data-marionete-btn="1"]');
    });
  }

  // B: page.mouse (down/up reais do CDP) no centro calculado, apos scrollIntoView
  await evalJS(page, DIAG_SCROLL_HOST_JS).catch(() => undefined);
  await sleep(400);
  const g2 = (await evalJS(page, DIAG_GEOMETRY_JS).catch(() => null)) as any;
  const pt = g2 && (g2.buttonPoint || g2.hostPoint);
  if (pt) {
    await runStrategy(`B page.mouse.click(${pt.cx},${pt.cy})`, async () => {
      await page.mouse.move(pt.cx, pt.cy);
      await sleep(120);
      await page.mouse.down();
      await sleep(60);
      await page.mouse.up();
    });
  }

  // D: .click() sintetico (confirma se o eds exige clique real)
  await runStrategy("D synthetic .click()", async () => {
    await evalJS(page, DIAG_SYNTH_CLICK_JS);
  });

  // E: sequencia completa pointer+mouse dispatchada no elemento
  await runStrategy("E dispatch pointer+mouse seq", async () => {
    await evalJS(page, DIAG_DISPATCH_SEQ_JS);
  });

  await evalJS(page, UNSELECT_ALL_ROWS_JS).catch(() => undefined);
  report.debug = await dumpDebug(page, account, "diag-bulk");
  return report;
}

/** Lista TODOS os overlays visiveis (modais, tours, popovers) com seus botoes.
 *  Usado pra descobrir o que cobre o modal de confirmacao em contas como a Poofy.
 *  Tambem reporta o elemento no centro do viewport (quem receberia o clique). */
const OVERLAY_DUMP_JS = `(function(){
  var vis=function(el){if(!el)return false;var r=el.getBoundingClientRect();if(r.width<1||r.height<1)return false;var s=getComputedStyle(el);if(s.visibility==='hidden'||s.display==='none'||parseFloat(s.opacity||'1')<0.05)return false;return el.offsetParent!==null||s.position==='fixed';};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ').slice(0,200)):'';};
  var sels=['.eds-modal','[role="dialog"]','.eds-tour','.eds-popover','[class*="tour" i]','[class*="guide" i]','[class*="popover" i]','[class*="onboarding" i]','[class*="driver" i]','[class*="walkthrough" i]','[class*="mask" i]','[class*="overlay" i]'];
  var set=[];var seen=[];
  sels.forEach(function(s){try{[].slice.call(document.querySelectorAll(s)).forEach(function(e){if(seen.indexOf(e)<0){seen.push(e);set.push(e);}});}catch(e){}});
  var out=[];
  set.forEach(function(el){
    if(!vis(el))return;
    var r=el.getBoundingClientRect();
    if(r.width<40||r.height<24)return;
    var cs=getComputedStyle(el);
    var btns=[].slice.call(el.querySelectorAll('button,.eds-button,[role="button"],a,.eds-modal__close,.eds-modal__close-icon')).filter(vis).map(function(b){return {t:txt(b),cls:(b.className||'').toString().slice(0,80)};}).filter(function(b){return b.t||/close/i.test(b.cls);});
    out.push({cls:(el.className||'').toString().slice(0,140),z:cs.zIndex,pos:cs.position,rect:{x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height)},text:txt(el),buttons:btns.slice(0,14)});
  });
  var cx=Math.round(window.innerWidth/2),cy=Math.round(window.innerHeight/2);
  var topCenter=document.elementFromPoint(cx,cy);
  var chain=[];var e=topCenter;
  for(var i=0;i<6&&e;i++){chain.push(((e.tagName||'')+'.'+((e.className||'').toString().replace(/\\s+/g,'.'))).slice(0,90));e=e.parentElement;}
  return {count:out.length,overlays:out,viewport:{w:window.innerWidth,h:window.innerHeight},centerEl:chain};
})()`;

export async function diagOverlays(page: Page, account = "diagov"): Promise<any> {
  const report: any = { account };
  await gotoManualListReady(page, account);
  report.url = page.url();
  // Ponto A: logo apos navegar (tour/promo costuma aparecer aqui).
  report.afterNav = await evalJS(page, OVERLAY_DUMP_JS).catch(() => null);

  // Reproduz o caminho ate ABRIR (sem confirmar) o modal de confirmacao.
  await evalJS(page, statusFilterJS("Em andamento")).catch(() => undefined);
  await sleep(2200);
  await closePromoModals(page);

  let checked = 0;
  for (let t = 0; t < 3 && !checked; t++) {
    if (t > 0) {
      await evalJS(page, UNSELECT_ALL_ROWS_JS).catch(() => undefined);
      await sleep(500);
    }
    const sel = (await evalJS(page, SELECT_ALL_HEADER_JS)) as { ok: boolean };
    if (!sel || !sel.ok) {
      await sleep(700);
      continue;
    }
    const chk = await pollUntil<{ checkedRows: number }>(
      page,
      COUNT_CHECKED_ROWS_JS,
      (r) => !!r && r.checkedRows > 0,
      4000,
      250
    );
    if (chk && chk.checkedRows > 0) checked = chk.checkedRows;
  }
  report.checkedRows = checked;

  // abre o dropdown e clica "Pausar" -> ABRE o modal de confirmacao (NAO confirma)
  let opened = false;
  for (let openTry = 0; openTry < 4 && !opened; openTry++) {
    const tr = (await evalJS(page, TAG_STATUS_TRIGGER_JS)) as { ok: boolean };
    if (tr && tr.ok) await clickCenter(page, '[data-marionete-status="1"]');
    const item = await pollUntil<{ found: boolean }>(
      page,
      tagItemExactJS("Pausar"),
      (r) => !!r && r.found,
      1800,
      200
    );
    if (item && item.found) {
      await clickCenter(page, '[data-marionete-item="1"]');
      opened = true;
    } else {
      await page.keyboard.press("Escape").catch(() => undefined);
      await sleep(500);
    }
  }
  report.openedItem = opened;
  await sleep(1200);
  // Ponto B: o que esta na tela no momento em que o confirm deveria estar aberto.
  report.atConfirm = await evalJS(page, OVERLAY_DUMP_JS).catch(() => null);
  report.confirmPresent = await evalJS(page, CONFIRM_MODAL_PRESENT_JS).catch(() => null);
  report.debug = await dumpDebug(page, account, "diag-overlays");

  // limpa SEM confirmar: Escape + desmarca tudo (nada e aplicado)
  await page.keyboard.press("Escape").catch(() => undefined);
  await sleep(300);
  await evalJS(page, UNSELECT_ALL_ROWS_JS).catch(() => undefined);
  return report;
}
