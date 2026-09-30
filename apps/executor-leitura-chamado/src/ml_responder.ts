import fs from "node:fs";
import path from "node:path";
import type { Page } from "puppeteer-core";
import { cfg } from "./config";
import * as davinci from "./davinci";
import { log } from "./log";
import { CONSULTA_URL, diaDaTela } from "./ml_consulta";
import { LoginNecessario } from "./shopee_historico";

/**
 * As MÃOS do ML: posta a nossa réplica numa consulta do formulário de ajuda do
 * Mercado Livre (mercadolivre.com.br/cases/detail/<N>).
 *
 * Nasceu no 298394 (30/09/2026): a réplica do Cairo ficou "pendente" desde 29/09
 * porque ninguém pedia a fila "responder" do ML — o robô do Eduardo respondia
 * pelo e-mail do Tuta e parou ~24/09.
 *
 * O que a tela mostrou em 30/09 (perfil "Forpaper - Mercado Livre", só olhando):
 *   - toda consulta tem o botão "Retomar consulta" (mesmo "Finalizou" — clicar
 *     não muda a situação); ele abre, na própria página, a caixa
 *     `textarea[placeholder="Digite uma mensagem"]`, o botão
 *     `button.message-input__send-btn` ("Enviar", desativado com a caixa vazia) e
 *     o clipe `button.message-input__attach-icon-btn`;
 *   - a mensagem enviada entra na conversa como um card "Você" do dia.
 *
 * Travas contra mandar duas vezes / mandar errado:
 *   - o texto entra pelo `value` da caixa (evento `input`), nunca por teclado —
 *     um Enter no meio do texto poderia disparar o envio;
 *   - antes de escrever, se a conversa já tem um "Você" com este texto DATADO a
 *     partir do dia em que a réplica nasceu (tentativa anterior que enviou mas não
 *     conseguiu avisar o DaVinci), só avisa "enviada". Sem a data, a réplica do
 *     Cairo (cópia da abertura de 24/09) passava por "já enviada" (30/09);
 *   - depois de clicar Enviar, só conta como enviada se o card "Você" com o texto
 *     aparecer na conversa;
 *   - `seco`: escreve, fotografa, APAGA e não clica Enviar (nem anexa: o anexo
 *     sobe pro ML no momento em que entra na caixa).
 *
 * Lógica de DOM como STRING pro page.evaluate (o tsx instrumenta funções com
 * __name e quebra dentro do browser) — mesmo padrão do shopee_historico.
 */

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function evalJS<T = any>(page: Page, js: string): Promise<T | undefined> {
  return (page.evaluate(js) as Promise<T>).catch(() => undefined);
}

/** Compara textos como a tela os mostra: sem NENHUM espaço/quebra (a página
 *  junta linhas — "Solicito:\n1." vira "Solicito:1."), minúsculo. */
export function normal(t: string): string {
  return (t || "").replace(/\s+/g, "").toLowerCase();
}

/** O começo do nosso texto (o card pode reformatar o fim). */
export function assinatura(t: string): string {
  return normal(t).slice(0, 120);
}

/** Dia (AAAA-MM-DD, São Paulo) de um ISO. */
export function diaSP(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Sao_Paulo" }).format(d);
}

export interface Nossa {
  texto: string;
  dia: string | null;
}

/** A nossa réplica já está na conversa? Só vale fala "Você" do dia em que ela
 *  nasceu em diante, com o mesmo começo de texto. */
export function jaNaConversa(nossas: Nossa[], texto: string, criadaEm?: string | null): boolean {
  const alvo = assinatura(texto);
  const desde = diaSP(criadaEm);
  return nossas.some(
    (v) => (!desde || (v.dia !== null && v.dia >= desde)) && normal(v.texto).includes(alvo)
  );
}

const JS_NOSSAS = `(function(){
  return [...document.querySelectorAll('[data-testid="message-card"]')].map(function(b){
    var s=[...b.querySelectorAll('.message-card__header span')].map(function(e){return (e.textContent||'').trim();});
    var t=b.querySelector('[data-testid="message-card-text"]');
    return {autor:s[0]||'', data:s[1]||'', texto:t?(t.innerText||''):''};
  }).filter(function(m){return /^voc[êe]$/i.test(m.autor);});
})()`;

const JS_TEM_CAIXA = `!!document.querySelector('textarea[placeholder="Digite uma mensagem"], .message-input textarea')`;

const JS_RETOMAR = `(function(){
  var b=[...document.querySelectorAll('button')].find(function(e){return /^\\s*Retomar consulta\\s*$/i.test(e.innerText||'');});
  if(!b) return false; b.scrollIntoView({block:'center'}); b.click(); return true;
})()`;

function jsPreencher(texto: string): string {
  return `(function(){
    var ta=document.querySelector('textarea[placeholder="Digite uma mensagem"], .message-input textarea');
    if(!ta) return 'sem caixa';
    var set=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
    set.call(ta, ${JSON.stringify(texto)});
    ta.dispatchEvent(new Event('input',{bubbles:true}));
    ta.dispatchEvent(new Event('change',{bubbles:true}));
    ta.scrollIntoView({block:'center'});
    return ta.value.length;
  })()`;
}

const JS_ENVIAR_ATIVO = `(function(){
  var b=document.querySelector('button.message-input__send-btn');
  return !!b && !b.disabled && b.getAttribute('aria-disabled')!=='true';
})()`;

const JS_ENVIAR = `(function(){
  var b=document.querySelector('button.message-input__send-btn');
  if(!b||b.disabled) return false; b.click(); return true;
})()`;

export interface Resultado {
  ok: boolean;
  erro?: string;
  jaEstava?: boolean;
  print?: string;
}

async function print(page: Page, nome: string): Promise<string | undefined> {
  try {
    fs.mkdirSync(cfg.debugDir, { recursive: true });
    const file = path.join(cfg.debugDir, `${nome}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`);
    await page.screenshot({ path: file });
    return file;
  } catch {
    return undefined;
  }
}

async function nossasNaConversa(page: Page): Promise<Nossa[]> {
  const brutas = (await evalJS<{ texto: string; data: string }[]>(page, JS_NOSSAS)) || [];
  return brutas.map((b) => ({ texto: b.texto, dia: diaDaTela(b.data) }));
}

/** Baixa as fotos da réplica do DaVinci pra anexar. */
async function baixarAnexos(t: davinci.Tarefa): Promise<string[]> {
  const dir = path.join(cfg.debugDir, "anexos", t.mensagem_id);
  fs.mkdirSync(dir, { recursive: true });
  const out: string[] = [];
  for (const [i, id] of t.anexos.entries()) {
    const a = await davinci.baixarAnexo(id);
    const ext = /png/.test(a.tipo) ? "png" : /pdf/.test(a.tipo) ? "pdf" : "jpg";
    const f = path.join(dir, `anexo-${i + 1}.${ext}`);
    fs.writeFileSync(f, a.conteudo);
    out.push(f);
  }
  return out;
}

/** Posta UMA réplica. Não lança: devolve ok/erro pro DaVinci (LoginNecessario sobe). */
export async function responder(page: Page, t: davinci.Tarefa, real: boolean): Promise<Resultado> {
  const consulta = (t.chamado || "").trim();
  const rot = `pedido ${t.pedido_bling || "?"} (consulta ${consulta})`;
  if (!/^\d{6,12}$/.test(consulta)) return { ok: false, erro: `"${consulta}" não é nº de consulta do ML` };
  const texto = (t.texto || "").trim();
  if (!texto) return { ok: false, erro: "réplica sem texto" };
  const url = (t.chamado_url || "").includes("/cases/") ? (t.chamado_url as string) : `${CONSULTA_URL}${consulta}`;
  await page.goto(url, { waitUntil: "networkidle2", timeout: 90000 }).catch(() => undefined);
  const senha = await evalJS<boolean>(page, `!!document.querySelector('input[type="password"]')`);
  if (/\/jms\/|\/login|registration|lgz/i.test(page.url()) || senha) {
    throw new LoginNecessario("login do Mercado Livre caiu — entrar no perfil e logar de novo");
  }
  let achou = false;
  for (let i = 0; i < 30 && !achou; i++) {
    achou = !!(await evalJS<boolean>(
      page,
      `(document.body.innerText||'').indexOf(${JSON.stringify(`Consulta número: ${consulta}`)})>=0`
    ));
    if (!achou) await sleep(500);
  }
  if (!achou) return { ok: false, erro: `a consulta ${consulta} não abriu neste login do ML (${page.url()})` };

  const antes = await nossasNaConversa(page);
  if (jaNaConversa(antes, texto, t.criada_em)) {
    // tentativa anterior mandou e não conseguiu avisar: não manda de novo
    log.warn(`${rot}: a conversa JÁ tem esta réplica — não mando de novo`);
    return { ok: true, jaEstava: true };
  }

  if (!(await evalJS<boolean>(page, JS_TEM_CAIXA))) {
    if (!(await evalJS<boolean>(page, JS_RETOMAR))) {
      return { ok: false, erro: `a consulta ${consulta} não tem o botão "Retomar consulta"` };
    }
    for (let i = 0; i < 20; i++) {
      await sleep(500);
      if (await evalJS<boolean>(page, JS_TEM_CAIXA)) break;
    }
  }
  if (!(await evalJS<boolean>(page, JS_TEM_CAIXA))) {
    const img = await print(page, `responder-sem-caixa-${t.pedido_bling || consulta}`);
    return { ok: false, erro: `"Retomar consulta" não abriu a caixa de mensagem`, print: img };
  }

  let arquivos: string[] = [];
  if (t.anexos.length && real) {
    arquivos = await baixarAnexos(t);
    const [chooser] = await Promise.all([
      page.waitForFileChooser({ timeout: 15000 }),
      page.click("button.message-input__attach-icon-btn"),
    ]);
    await chooser.accept(arquivos);
    await sleep(3000 + 2000 * arquivos.length); // o ML sobe cada arquivo na hora
  } else if (t.anexos.length) {
    log.info(`${rot}: SECO — anexaria ${t.anexos.length} foto(s)`);
  }

  const escrito = await evalJS<number | string>(page, jsPreencher(texto));
  if (typeof escrito !== "number") return { ok: false, erro: `não consegui escrever na caixa (${escrito})` };
  let ativo = false;
  for (let i = 0; i < 10 && !ativo; i++) {
    await sleep(300);
    ativo = !!(await evalJS<boolean>(page, JS_ENVIAR_ATIVO));
  }
  if (!ativo) {
    // React às vezes só acorda com tecla de verdade: um espaço no FIM e apaga
    await page.focus('textarea[placeholder="Digite uma mensagem"]').catch(() => undefined);
    await page.keyboard.press("End").catch(() => undefined);
    await page.keyboard.type(" ").catch(() => undefined);
    await page.keyboard.press("Backspace").catch(() => undefined);
    await sleep(500);
    ativo = !!(await evalJS<boolean>(page, JS_ENVIAR_ATIVO));
  }
  const img = await print(page, `responder-${real ? "antes" : "seco"}-${t.pedido_bling || consulta}`);
  if (!ativo) return { ok: false, erro: "escrevi, mas o botão Enviar não acendeu", print: img };

  if (!real) {
    await evalJS(page, jsPreencher(""));
    return { ok: true, print: img };
  }

  if (!(await evalJS<boolean>(page, JS_ENVIAR))) {
    return { ok: false, erro: "o botão Enviar sumiu na hora de clicar", print: img };
  }
  for (let i = 0; i < 40; i++) {
    await sleep(750);
    const agora = await nossasNaConversa(page);
    if (agora.length > antes.length && jaNaConversa(agora, texto, t.criada_em)) {
      const depois = await print(page, `responder-enviada-${t.pedido_bling || consulta}`);
      for (const f of arquivos) fs.rmSync(f, { force: true });
      return { ok: true, print: depois };
    }
  }
  // Pode ter saído sem a tela atualizar: recarrega e confere antes de dizer que falhou
  await page.reload({ waitUntil: "networkidle2", timeout: 90000 }).catch(() => undefined);
  await sleep(2000);
  if (jaNaConversa(await nossasNaConversa(page), texto, t.criada_em)) {
    return { ok: true, print: await print(page, `responder-enviada-${t.pedido_bling || consulta}`) };
  }
  const falha = await print(page, `responder-falhou-${t.pedido_bling || consulta}`);
  return { ok: false, erro: "cliquei Enviar e a mensagem não apareceu na conversa", print: falha };
}
