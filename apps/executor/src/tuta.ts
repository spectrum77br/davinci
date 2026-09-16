import puppeteer, { Browser, Page } from "puppeteer-core";
import fs from "node:fs";
import path from "node:path";
import { cfg } from "./config";
import { log } from "./log";

/**
 * Tuta (tuta.com) — leitura da caixa atrás dos códigos de devolução.
 *
 * O Tuta não tem IMAP nem API pública: é decisão de produto deles, por causa da
 * criptografia ponta a ponta. E as regras de caixa de entrada só movem para
 * pasta, marcam spam ou descartam — não encaminham para fora. Sobra a tela.
 *
 * Este robô é DE LEITURA. Ele não abre e-mail, não marca como lido, não
 * responde e não apaga nada: só entra na caixa, espera a lista renderizar e
 * devolve o TEXTO VISÍVEL da tela. Quem decide o que é "e-mail de devolução" é
 * o servidor (services/tuta_devolucoes.py) — assim, mudar o critério não exige
 * mexer aqui nem reiniciar o executor.
 *
 * Toda lógica de DOM vai como STRING pro page.evaluate (o tsx/esbuild
 * instrumenta funções com __name e quebra dentro do browser) — mesmo padrão do
 * melhorenvio.ts. NUNCA browser.close(): o perfil é do AdsPower.
 */

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const DEBUG_DIR = process.env.DEBUG_DIR || "./debug";

export class TutaPrecisaLogin extends Error {
  constructor(msg = "login necessário no Tuta") {
    super(msg);
    this.name = "TutaPrecisaLogin";
  }
}

export interface LeituraTuta {
  ok: boolean;
  conta?: string;
  url?: string;
  texto?: string;
  linhas?: number;
  reason?: string;
  screenshot?: string;
}

const MAX_TEXTO = 20000;

async function screenshot(page: Page, nome: string): Promise<string | undefined> {
  try {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    const arq = path.join(
      DEBUG_DIR,
      `tuta-${nome}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`
    );
    await page.screenshot({ path: arq });
    return arq;
  } catch {
    return undefined;
  }
}

async function evalJS<T>(page: Page, js: string): Promise<T | undefined> {
  try {
    return (await page.evaluate(js)) as T;
  } catch {
    return undefined;
  }
}

// Texto visível da caixa + a conta logada. Prefere o painel da lista quando dá
// para reconhecê-lo; senão cai no corpo inteiro — é melhor devolver demais do
// que devolver nada, porque quem filtra é o servidor.
const LER_CAIXA_JS = `(function(){
  function vis(e){ if(!e) return false; var r=e.getClientRects(); return !!(e.offsetParent||r.length); }
  function txt(e){ return ((e&&e.innerText)||'').replace(/\\u00a0/g,' '); }
  var alvo=null, melhor=0;
  var cands=[].slice.call(document.querySelectorAll('[role="list"],[class*="list"],[class*="List"],main,[role="main"]'));
  for(var i=0;i<cands.length;i++){
    var e=cands[i];
    if(!vis(e)) continue;
    var t=txt(e);
    var linhas=t.split('\\n').filter(function(l){return l.trim().length>3;}).length;
    if(linhas>melhor && linhas<400){ melhor=linhas; alvo=e; }
  }
  var corpo = alvo?txt(alvo):txt(document.body);
  var conta='';
  var m=(txt(document.body)||'').match(/[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}/i);
  if(m) conta=m[0];
  return { url: location.href, texto: corpo.slice(0, ${MAX_TEXTO}), conta: conta,
           temSenha: !!document.querySelector('input[type="password"]') };
})()`;

export async function connect(wsEndpoint: string): Promise<{ browser: Browser; page: Page }> {
  const browser = await puppeteer.connect({ browserWSEndpoint: wsEndpoint, defaultViewport: null });
  const pages = await browser.pages();
  let page =
    pages.find((p) => /tuta\.com/i.test(p.url())) ||
    pages.find((p) => !/^about:blank$/i.test(p.url())) ||
    pages[0];
  if (!page) page = await browser.newPage();
  await page.bringToFront().catch(() => undefined);
  await page
    .goto(cfg.tutaCaixaUrl, { waitUntil: "networkidle2", timeout: 60_000 })
    .catch(() => undefined);
  return { browser, page };
}

export async function lerCaixa(page: Page): Promise<LeituraTuta> {
  // A caixa do Tuta é decifrada no navegador: renderiza depois do "carregou".
  await sleep(6000);
  let info = await evalJS<any>(page, LER_CAIXA_JS);
  if (!info || (info.texto || "").trim().length < 40) {
    await sleep(6000);
    info = await evalJS<any>(page, LER_CAIXA_JS);
  }
  if (!info) {
    const shot = await screenshot(page, "sem-resposta");
    return { ok: false, reason: "não consegui ler a tela do Tuta", screenshot: shot };
  }
  if (info.temSenha || /\/login/i.test(String(info.url || ""))) {
    const shot = await screenshot(page, "login");
    throw new TutaPrecisaLogin(`tela de login (${info.url}) — print em ${shot}`);
  }
  const texto = String(info.texto || "");
  const linhas = texto.split("\n").filter((l: string) => l.trim().length > 3).length;
  const shot = await screenshot(page, "caixa");
  if (linhas < 2) {
    return {
      ok: false,
      url: info.url,
      conta: info.conta,
      texto,
      linhas,
      reason: "a caixa abriu mas veio vazia — a lista pode não ter renderizado",
      screenshot: shot,
    };
  }
  log.info(`Tuta: li a caixa de ${info.conta || "(conta não identificada)"} — ${linhas} linhas`);
  return { ok: true, url: info.url, conta: info.conta, texto, linhas, screenshot: shot };
}

export async function disconnect(browser: Browser): Promise<void> {
  try {
    await browser.disconnect();
  } catch {
    /* ignore */
  }
}
