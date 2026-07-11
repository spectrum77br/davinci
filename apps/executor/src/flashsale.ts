import type { Page } from "puppeteer-core";
import * as shopee from "./shopee";
import { log as logger } from "./log";

/**
 * CORE do fluxo "Duplicar" da Oferta Relampago (Shop Flash Sale).
 * Port fiel do marionete/src/flashsale.ts (provado no Barbosa), adaptado pro
 * executor do DaVinci:
 *   - recebe a `page` JA conectada (quem abre/fecha o perfil AdsPower e o
 *     processCommand do index.ts — igual applyState). NAO e dona da sessao.
 *   - sem withProfileLock: o executor ja serializa (flag `ticking` + laco serial)
 *     e e o UNICO processo que abre AdsPower.
 *   - devolve um FlashResult estruturado.
 *
 * Duplica a oferta 'Em andamento' pro 1o dia com "Aberturas" (ou targetDay),
 * mantendo produtos/%OFF/preco/estoque, habilita as variacoes validas e, se
 * commit=true, aperta o Confirmar final -> CRIA a oferta.
 *
 * SEGURANCA:
 *  - commit=false (default) faz TUDO menos o Confirmar final (nada e criado).
 *  - commit=true so aperta o Confirmar final se SELECTORS_CALIBRATED=true
 *    (mesma trava do applyState). Senao lanca erro ANTES de criar.
 */

const SELLER = "https://seller.shopee.com.br";
const LIST_URL = `${SELLER}/portal/marketing/shop-flash-sale/list`;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface FlashResult {
  account: string;
  ok: boolean; // fluxo chegou ao fim sem erro
  created: boolean; // criou de verdade (so em commit + sucesso confirmado)
  dry: boolean; // true = nao tentou criar (commit=false)
  chosenDay?: string; // dia escolhido no calendario
  enabledOn?: number; // qtd de variacoes ligadas (toggles verdes)
  priceErrors?: number; // qtd de "menor que o preco original"
  reason?: string; // motivo de skip/aviso (quando ok=false ou created=false)
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
async function scrollVars(page: Page): Promise<void> {
  for (let i = 0; i < 6; i++) {
    await evalJS(
      page,
      `(function(){var b=document.querySelector('.eds-table__body-wrapper,.eds-table__body,[class*="table__body"]');if(b){b.scrollTop=b.scrollTop+600;}window.scrollBy(0,600);})()`
    );
    await sleep(400);
  }
  await evalJS(page, `window.scrollTo(0,0);`);
  await sleep(300);
}

// ---- trechos do mapeador (provados no _flashone.ts) ----
const FIND_DUP_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-fs-dup]')).forEach(function(e){e.removeAttribute('data-fs-dup');});
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var rows=[].slice.call(document.querySelectorAll('.eds-table__row')).filter(vis);
  for(var i=0;i<rows.length;i++){
    if(/em andamento/i.test(txt(rows[i]))){
      var cand=[].slice.call(rows[i].querySelectorAll('a,button,span,div')).filter(function(e){return vis(e)&&/^duplicar$/i.test(txt(e));});
      if(cand.length){cand[0].setAttribute('data-fs-dup','1');return {found:true, rowText:txt(rows[i]).slice(0,90), rowIndex:i};}
    }
  }
  var any=[].slice.call(document.querySelectorAll('a,button,span,div')).filter(function(e){return vis(e)&&/^duplicar$/i.test(txt(e));});
  if(any.length){any[0].setAttribute('data-fs-dup','1');return {found:true, rowText:'(fallback: 1o Duplicar visivel)', rowIndex:-1};}
  return {found:false};
})()`;

const FORM_DUMP_JS = `(function(){
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var all=[].slice.call(document.querySelectorAll('button,.eds-button,[role="button"],a')).filter(vis);
  var btns=all.map(function(b){return txt(b).slice(0,40);}).filter(Boolean);
  var seen={},ub=[];btns.forEach(function(t){if(!seen[t]){seen[t]=1;ub.push(t);}});
  var has=function(re){return all.filter(function(e){return re.test(txt(e));}).length;};
  return {
    url:location.href,
    buttons:ub.slice(0,50),
    hasPeriodBtn:has(/selecionar per[ií]odo/i),
    hasHabilitar:has(/^habilitar$/i),
    hasConfirmar:has(/^confirmar$/i),
    habilitouTexto:(function(){var m=(document.body.innerText||'').match(/habilitou\\s+\\d+\\s+de\\s+\\d+\\s+produto/i);return m?m[0]:'';})()
  };
})()`;

const TAG_PERIOD_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-fs-period]')).forEach(function(e){e.removeAttribute('data-fs-period');});
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var b=[].slice.call(document.querySelectorAll('button,.eds-button,[role="button"],a')).filter(function(e){return vis(e)&&/selecionar per[ií]odo/i.test(txt(e));});
  if(b.length){b[0].setAttribute('data-fs-period','1');return {ok:true};}
  return {ok:false};
})()`;

const MODAL_DUMP_JS = `(function(){
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var modal=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(vis).pop();
  if(!modal)return {modal:false};
  var cells=[].slice.call(modal.querySelectorAll('td,[class*="cell"],[class*="day"],[class*="date"]')).filter(vis);
  return {modal:true, cellCount:cells.length, cellsSample:cells.map(function(c){return txt(c).slice(0,14);}).filter(Boolean).slice(0,50), modalTextHead:txt(modal).slice(0,140)};
})()`;

function tagDayJS(dayNum: number): string {
  return `(function(){
    [].slice.call(document.querySelectorAll('[data-fs-day]')).forEach(function(e){e.removeAttribute('data-fs-day');});
    var vis=function(el){return el && el.offsetParent!==null;};
    var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
    var modal=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(vis).pop();
    if(!modal)return {ok:false,reason:'sem modal'};
    var cells=[].slice.call(modal.querySelectorAll('td,[class*="cell"],[class*="day"],[class*="date"]')).filter(vis);
    var hit=cells.find(function(c){return new RegExp('^'+${dayNum}+'(\\\\s|$)').test(txt(c));});
    if(hit){hit.setAttribute('data-fs-day','1');return {ok:true, cellText:txt(hit).slice(0,25)};}
    return {ok:false, reason:'dia nao achado', sample:cells.map(txt).slice(0,40)};
  })()`;
}

// escolhe a 1a celula de dia com "Aberturas" disponiveis (nao passada/cheia/desabilitada)
const TAG_OPENDAY_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-fs-day]')).forEach(function(e){e.removeAttribute('data-fs-day');});
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var modal=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(vis).pop();
  if(!modal)return {ok:false,reason:'sem modal'};
  var cells=[].slice.call(modal.querySelectorAll('[class*="date-table__cell-inner"],[class*="date-table__cell"]')).filter(function(c){
    return vis(c) && /abertura/i.test(txt(c)) && !/disabled|is-empty|out-of-month/.test(c.className||'');
  });
  cells.sort(function(a,b){var ra=a.getBoundingClientRect(),rb=b.getBoundingClientRect();return (ra.top-rb.top)||(ra.left-rb.left);});
  if(cells.length){cells[0].setAttribute('data-fs-day','1');return {ok:true, dayText:txt(cells[0]).slice(0,25), count:cells.length};}
  return {ok:false, reason:'nenhum dia com Aberturas no mes visivel'};
})()`;

const SLOTS_DUMP_JS = `(function(){
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var modal=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(vis).pop();
  if(!modal)return {ok:false};
  var slots=[].slice.call(modal.querySelectorAll('[class*="slot"],[class*="time"],[class*="interval"],li,tr')).filter(vis).map(txt).filter(function(t){return t && /\\d|abertura|dispon/i.test(t);});
  var seen={},us=[];slots.forEach(function(t){if(!seen[t]){seen[t]=1;us.push(t);}});
  return {ok:true, slotItems:us.slice(0,25)};
})()`;

const TAG_SLOT_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-fs-slot]')).forEach(function(e){e.removeAttribute('data-fs-slot');});
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var modal=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(vis).pop();
  if(!modal)return {ok:false,reason:'sem modal'};
  var radios=[].slice.call(modal.querySelectorAll('.eds-radio,input[type=radio],[class*="radio"]')).filter(vis);
  if(radios.length){radios[0].setAttribute('data-fs-slot','1');return {ok:true, via:'radio', radioCount:radios.length};}
  var rows=[].slice.call(modal.querySelectorAll('tr,li,div')).filter(function(e){return vis(e)&&/\\d{2}:\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}:\\d{2}/.test(txt(e))&&txt(e).length<70;});
  if(rows.length){rows[0].setAttribute('data-fs-slot','1');return {ok:true, via:'row', rowText:txt(rows[0]).slice(0,50)};}
  return {ok:false, reason:'sem radio/slot'};
})()`;

const TAG_MODAL_CONFIRM_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-fs-mconfirm]')).forEach(function(e){e.removeAttribute('data-fs-mconfirm');});
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var modal=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"]')).filter(vis).pop();
  if(!modal)return {ok:false};
  var b=[].slice.call(modal.querySelectorAll('button,.eds-button')).filter(function(e){return vis(e)&&/^confirmar$/i.test(txt(e));});
  if(b.length){var d=/is-disabled|--disabled|disabled/.test(b[0].className)||b[0].disabled===true;b[0].setAttribute('data-fs-mconfirm','1');return {ok:true, disabled:d};}
  return {ok:false};
})()`;

// ---- Selecionar tudo (checkbox do cabecalho "Variação(ões)") + Habilitar em lote ----
const TAG_MASTERCB_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-fs-mastercb]')).forEach(function(e){e.removeAttribute('data-fs-mastercb');});
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var labels=[].slice.call(document.querySelectorAll('div,span,th,td,label')).filter(function(e){return vis(e)&&/^varia[çc][aã]o/i.test(txt(e))&&txt(e).length<22;});
  for(var i=0;i<labels.length;i++){
    var p=labels[i];
    for(var up=0; up<6 && p; up++){
      var cb=p.querySelector('.eds-checkbox');
      if(cb && vis(cb)){cb.setAttribute('data-fs-mastercb','1');return {ok:true, labelText:txt(labels[i]).slice(0,25), checked:/--checked|is-checked/.test(cb.className)};}
      p=p.parentElement;
    }
  }
  return {ok:false, reason:'sem checkbox de cabecalho perto de Variação'};
})()`;

// aviso que pode aparecer apos "Habilitar" -> botao de prosseguir
const TAG_DIALOG_CONFIRM_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-fs-dlg]')).forEach(function(e){e.removeAttribute('data-fs-dlg');});
  var vis=function(el){return el && el.offsetParent!==null;};
  var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
  var modal=[].slice.call(document.querySelectorAll('.eds-modal,[role="dialog"],.eds-message-box,[class*="message-box"]')).filter(vis).pop();
  if(!modal)return {ok:false};
  var b=[].slice.call(modal.querySelectorAll('button,.eds-button')).filter(function(e){return vis(e)&&/^(confirmar|ok|continuar|sim|habilitar)$/i.test(txt(e));});
  if(b.length){b[0].setAttribute('data-fs-dlg','1');return {ok:true, modalText:txt(modal).slice(0,120), btn:txt(b[0])};}
  return {ok:false, modalText:txt(modal).slice(0,120)};
})()`;

// FALLBACK quando o "Habilitar em lote" e tudo-ou-nada (habilitou 0 por precos
// invalidos): liga SO as variacoes validas, clicando cada toggle habilitavel.
const ENABLE_VALID_SWITCHES_JS = `(function(){
  [].slice.call(document.querySelectorAll('[data-fs-sw]')).forEach(function(e){e.removeAttribute('data-fs-sw');});
  var vis=function(el){return el && el.offsetParent!==null;};
  var sws=[].slice.call(document.querySelectorAll('.eds-switch')).filter(vis);
  var byRow={};
  sws.forEach(function(s){var t=Math.round(s.getBoundingClientRect().top/4);if(!byRow[t])byRow[t]=[];byRow[t].push(s);});
  var reps=Object.keys(byRow).map(function(k){return byRow[k].sort(function(a,b){return a.getBoundingClientRect().left-b.getBoundingClientRect().left;})[0];});
  reps.sort(function(a,b){return a.getBoundingClientRect().top-b.getBoundingClientRect().top;});
  var out={rows:reps.length, valid:0, disabled:0, alreadyOn:0, tagCount:0};
  var idx=0;
  reps.forEach(function(s){
    var cls=s.className||'';
    if(/--disabled/.test(cls)){out.disabled++;return;}
    if(/--open|--checked|is-checked/.test(cls)){out.alreadyOn++;return;}
    out.valid++;
    s.setAttribute('data-fs-sw', String(idx++));
    out.tagCount++;
  });
  return out;
})()`;

// verificacao pos-Habilitar: "habilitou N de M produto" + toggles verdes (ligados)
const ENABLE_COUNT_JS = `(function(){
  var vis=function(el){return el && el.offsetParent!==null;};
  var m=(document.body.innerText||'').match(/habilitou\\s+(\\d+)\\s+de\\s+(\\d+)\\s+produto/i);
  var sw=[].slice.call(document.querySelectorAll('.eds-switch')).filter(vis);
  var on=sw.filter(function(s){return /--open|--checked|is-checked/.test(s.className)||s.getAttribute('aria-checked')==='true';}).length;
  var esc=(document.querySelector('.eds-message,[class*="toast"],[class*="notice"]')||{}).innerText||'';
  var errCount=((document.body.innerText||'').match(/menor que o pre[cç]o original/gi)||[]).length;
  return {habTxt:m?m[0]:'', habN:m?+m[1]:null, habM:m?+m[2]:null, switchesVis:sw.length, switchesOn:on, priceErrors:errCount, msg:(esc||'').slice(0,80)};
})()`;

function tagBtnJS(reSource: string, tag: string, scope: "footer" | "any"): string {
  return `(function(){
    [].slice.call(document.querySelectorAll('[data-fs-${tag}]')).forEach(function(e){e.removeAttribute('data-fs-${tag}');});
    var vis=function(el){return el && el.offsetParent!==null;};
    var txt=function(el){return el?((el.innerText||el.textContent||'').trim().replace(/\\s+/g,' ')):'';};
    var re=${reSource};
    var inModal=function(el){return !!el.closest('.eds-modal,[role="dialog"]');};
    var all=[].slice.call(document.querySelectorAll('button,.eds-button,[role="button"]')).filter(function(e){return vis(e)&&re.test(txt(e))&&!inModal(e);});
    if(${scope === "footer" ? "true" : "false"}){
      var foot=all.filter(function(e){return !!e.closest('[class*="footer"],[class*="bottom-bar"],[class*="fixed"]');});
      var pool=foot.length?foot:all;
      pool.sort(function(a,b){return b.getBoundingClientRect().top-a.getBoundingClientRect().top;});
      if(pool.length){var d0=/is-disabled|--disabled|disabled/.test(pool[0].className)||pool[0].disabled===true;pool[0].setAttribute('data-fs-${tag}','1');return {ok:true,count:pool.length,disabled:d0,text:txt(pool[0]).slice(0,30)};}
      return {ok:false,count:0};
    } else {
      if(all.length){var d1=/is-disabled|--disabled|disabled/.test(all[0].className)||all[0].disabled===true;all[0].setAttribute('data-fs-${tag}','1');return {ok:true,count:all.length,disabled:d1,text:txt(all[0]).slice(0,30)};}
      return {ok:false,count:0};
    }
  })()`;
}

const SUCCESS_JS = `(function(){
  var body=(document.body.innerText||'').replace(/\\s+/g,' ');
  var toast=(body.match(/sucesso|criad[ao]|salv[ao]|enviad[ao] com sucesso/i)||[])[0]||'';
  return {url:location.href, backToList:/shop-flash-sale\\/list/.test(location.href), toast:toast};
})()`;

/** Duplica a Oferta Relampago 'Em andamento' de UMA loja usando a `page` JA
 *  conectada (o processCommand do executor e dono da sessao AdsPower e ja
 *  serializa 1 perfil por vez). Lanca Error/NeedsManualLogin em caso de falha —
 *  o chamador reporta failed sem mexer no estado. */
export async function duplicateFlashSale(
  page: Page,
  accountName: string,
  opts: { commit?: boolean; targetDay?: number | null } = {}
): Promise<FlashResult> {
  const commit = !!opts.commit;
  const forcedDay = opts.targetDay ?? null;
  const log = (...a: unknown[]) =>
    logger.info(
      `[flash ${accountName}] ` +
        a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ")
    );

  log(`modo=${commit ? "COMMIT (CRIA)" : "DRY (nao cria)"} | dia=${forcedDay ?? "auto (1o com Aberturas)"}`);
  const browser = page.browser();

  await page.goto(LIST_URL, { waitUntil: "networkidle2", timeout: 60_000 }).catch(() => undefined);
  await sleep(2500);
  await shopee.guardLoginCaptcha(page);

  // 1) Duplicar da 'Em andamento'
  const dup = await evalJS(page, FIND_DUP_JS);
  log("[1] Duplicar:", JSON.stringify(dup));
  await shopee.dumpDebug(page, accountName, "flash-1-list");
  if (!dup || !dup.found) throw new Error("nao achei Duplicar na oferta 'Em andamento'");
  await clickCenter(page, '[data-fs-dup="1"]');
  await sleep(1500);
  const dupDlg = await evalJS(page, TAG_DIALOG_CONFIRM_JS);
  if (dupDlg && dupDlg.ok) {
    log("[1] dialogo de confirmacao do Duplicar:", JSON.stringify(dupDlg));
    await clickCenter(page, '[data-fs-dlg="1"]');
    await sleep(1200);
  }

  // form abre em nova aba: /shop-flash-sale/create?from=<id>
  let form: Page | undefined;
  for (let i = 0; i < 30; i++) {
    const ps = await browser.pages();
    form =
      ps.find((p) => /shop-flash-sale\/create/i.test(p.url())) ||
      ps.find((p) => /[?&]from=/i.test(p.url()));
    if (form) break;
    await sleep(800);
  }
  if (!form) {
    const urls = (await browser.pages()).map((p) => p.url());
    log("[1] abas abertas:", JSON.stringify(urls));
    throw new Error("o clique em 'Duplicar' nao abriu o form /create (caiu em detalhes/lista?)");
  }
  await form.bringToFront().catch(() => undefined);
  page = form;
  await sleep(1800);

  const f0 = await evalJS(page, FORM_DUMP_JS);
  log("[2] FORM:", JSON.stringify(f0));
  await shopee.dumpDebug(page, accountName, "flash-2-form");

  // 3) Selecionar Periodo -> dia alvo -> slot -> Confirmar do MODAL
  const tagP = await evalJS(page, TAG_PERIOD_JS);
  if (!tagP || !tagP.ok) throw new Error("nao achei 'Selecionar Periodo'");
  await clickCenter(page, '[data-fs-period="1"]');
  await sleep(2500);
  const modal = await evalJS(page, MODAL_DUMP_JS);
  log("[3] modal calendario:", JSON.stringify(modal).slice(0, 200));

  const tagD = forcedDay ? await evalJS(page, tagDayJS(forcedDay)) : await evalJS(page, TAG_OPENDAY_JS);
  log(`[3] tag dia (${forcedDay ?? "1o com Aberturas"}):`, JSON.stringify(tagD));
  if (!tagD || !tagD.ok) throw new Error("nao achei dia com 'Aberturas' disponiveis no calendario");
  const chosenDay: string = tagD.dayText || (forcedDay ? `dia ${forcedDay}` : "?");
  await clickCenter(page, '[data-fs-day="1"]');
  await sleep(2000);
  const slots = await evalJS(page, SLOTS_DUMP_JS);
  log("[3] horarios:", JSON.stringify(slots).slice(0, 300));
  await shopee.dumpDebug(page, accountName, "flash-3-modal");

  const tagS = await evalJS(page, TAG_SLOT_JS);
  log("[3] slot:", JSON.stringify(tagS));
  if (tagS && tagS.ok) {
    await clickCenter(page, '[data-fs-slot="1"]');
    await sleep(1200);
  }
  const mc = await evalJS(page, TAG_MODAL_CONFIRM_JS);
  log("[3] modal Confirmar:", JSON.stringify(mc));
  if (!mc || !mc.ok) throw new Error("nao achei Confirmar do modal de periodo");
  if (mc.disabled) throw new Error("Confirmar do modal esta desabilitado (sem slot valido?)");
  await clickCenter(page, '[data-fs-mconfirm="1"]');
  await sleep(3500);

  // 4) variacoes populadas -> estado inicial
  await scrollVars(page);
  const pre = await evalJS(page, ENABLE_COUNT_JS);
  log("[4] pre-Habilitar:", JSON.stringify(pre));
  await shopee.dumpDebug(page, accountName, "flash-4-variations");

  // 5) Habilitar EM LOTE (checkbox mestre -> Habilitar -> confirma dialogo)
  const mcb = await evalJS(page, TAG_MASTERCB_JS);
  log("[5] checkbox mestre:", JSON.stringify(mcb));
  if (!mcb || !mcb.ok) throw new Error("nao achei o checkbox 'Selecionar tudo' (cabecalho Variação)");
  if (!mcb.checked) {
    await clickCenter(page, '[data-fs-mastercb="1"]');
    await sleep(1200);
  }
  const tagH = await evalJS(page, tagBtnJS("/^habilitar$/i", "hab", "any"));
  log("[5] botao Habilitar:", JSON.stringify(tagH));
  if (!tagH || !tagH.ok) throw new Error("nao achei o botao 'Habilitar' em lote");
  await clickCenter(page, '[data-fs-hab="1"]');
  await sleep(1800);
  const dlg = await evalJS(page, TAG_DIALOG_CONFIRM_JS);
  if (dlg && dlg.ok) {
    log("[5] dialogo apos Habilitar:", JSON.stringify(dlg));
    await clickCenter(page, '[data-fs-dlg="1"]');
    await sleep(1800);
  }
  await sleep(1200);
  let enab = await evalJS(page, ENABLE_COUNT_JS);
  log("[5] pos-Habilitar (lote):", JSON.stringify(enab));

  // Fallback: lote e tudo-ou-nada. Se ligou 0 por precos invalidos, liga SO as validas.
  if (enab && (enab.switchesOn || 0) === 0) {
    const scan = await evalJS(page, ENABLE_VALID_SWITCHES_JS);
    log("[5] lote nao ligou nada -> ligar so as validas:", JSON.stringify(scan));
    const nSw = (scan && scan.tagCount) || 0;
    for (let i = 0; i < nSw; i++) {
      await clickCenter(page, `[data-fs-sw="${i}"]`);
      await sleep(200);
    }
    await sleep(1200);
    enab = await evalJS(page, ENABLE_COUNT_JS);
    log("[5] pos-fallback (validas ligadas):", JSON.stringify(enab));
  }
  await shopee.dumpDebug(page, accountName, "flash-5-habilitado");
  const enabledOn: number = (enab && enab.switchesOn) || 0;
  const priceErrors: number = (enab && enab.priceErrors) || 0;
  if (enabledOn === 0)
    throw new Error("nada ligou (0 toggles verdes) — nem lote nem individual; abortando antes do Confirmar");

  // 6) estado do Confirmar FINAL (rodape)
  const tagC = await evalJS(page, tagBtnJS("/^confirmar$/i", "fconfirm", "footer"));
  log("[6] Confirmar final:", JSON.stringify(tagC));
  await shopee.dumpDebug(page, accountName, "flash-6-preconfirm");

  if (!commit) {
    log(">>> DRY: parei ANTES do Confirmar final. NADA foi criado.");
    return { account: accountName, ok: true, created: false, dry: true, chosenDay, enabledOn, priceErrors };
  }

  // TRAVA de seguranca: em COMMIT so cria se os seletores estiverem calibrados
  // (mesma trava do applyState). Sem isso, recusa o Confirmar final — nada e criado.
  if (process.env.SELECTORS_CALIBRATED !== "true") {
    throw new Error(
      "SELECTORS_CALIBRATED != true — recusando o Confirmar final da Oferta Relampago (nada criado). " +
        "Valide 1 duplicacao ao vivo e defina SELECTORS_CALIBRATED=true no .env do executor."
    );
  }

  // 7) COMMIT: aperta Confirmar final -> pode abrir modal de confirmacao secundario
  if (!tagC || !tagC.ok) throw new Error("nao achei Confirmar final (rodape)");
  if (tagC.disabled) throw new Error("Confirmar final desabilitado — ha bloqueio (erro de preco/estoque?)");
  await clickCenter(page, '[data-fs-fconfirm="1"]');
  await sleep(2500);
  const mc2 = await evalJS(page, TAG_MODAL_CONFIRM_JS);
  if (mc2 && mc2.ok && !mc2.disabled) {
    log("[7] confirmacao secundaria -> Confirmar");
    await clickCenter(page, '[data-fs-mconfirm="1"]');
    await sleep(3000);
  }
  await sleep(3000);
  const ok = await evalJS(page, SUCCESS_JS);
  log("[7] resultado:", JSON.stringify(ok));
  await shopee.dumpDebug(page, accountName, "flash-7-resultado");
  const created = !!(ok && (ok.backToList || ok.toast));
  return {
    account: accountName,
    ok: true,
    created,
    dry: false,
    chosenDay,
    enabledOn,
    priceErrors,
    reason: created ? undefined : "COMMIT feito, mas nao confirmei sucesso (conferir screenshot flash-7-resultado)",
  };
}
