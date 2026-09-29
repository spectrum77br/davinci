#!/usr/bin/env node
// Mãos da IA de Chamado no "Upload Evidence" da Shopee (2ª disputa).
//
// Vinicius, 25/09/2026 (294571/296012): "quero que o agente de IA escolha e
// suba". O navegador do Hermes não escolhe arquivo do disco; este script sim
// (puppeteer `uploadFile`, testado na janela "Enviar Prova" do 294571: a
// miniatura aparece e o contador vai a "1 / 3"). A IA escolhe as fotos; o
// clique fica aqui porque o clique por coordenada NÃO abre a janela — só o
// `button.click()` no botão da linha do pedido.
//
//   node shopee_evidencia.mjs conferir --ws WS --pedido X
//   node shopee_evidencia.mjs fotos    --ws WS --pedido X --pasta DIR
//   node shopee_evidencia.mjs enviar   --ws WS --pedido X --arquivos a.jpg,b.jpg
//                                      [--print P.png] [--de-verdade]
//
// Sem --de-verdade o `enviar` anexa, confere e sai por "Voltar" (nada vai pra
// Shopee). Saída: uma linha JSON. Não abre nem fecha o perfil do AdsPower.

import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(
  path.join(
    process.env.PUPPETEER_DIR ||
      path.join(process.env.HOME, "DaVinci/executor-leitura-chamado/node_modules"),
    "/"
  )
);
const puppeteer = require("puppeteer-core");

const SELLER = process.env.SHOPEE_SELLER_URL || "https://seller.shopee.com.br";
const SEL_BUSCA = 'input[placeholder*="ID da solicita"]';
const MAX_ARQUIVOS = 3;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function args() {
  const a = { cmd: process.argv[2] };
  for (let i = 3; i < process.argv.length; i++) {
    const k = process.argv[i];
    if (!k.startsWith("--")) continue;
    const v = process.argv[i + 1];
    if (v === undefined || v.startsWith("--")) a[k.slice(2)] = true;
    else (a[k.slice(2)] = v), i++;
  }
  return a;
}

// A janela "Enviar Prova" visível (há cópias escondidas no DOM).
const JS_MODAL = `[...document.querySelectorAll('.eds-modal__box, .eds-modal__content')].find(function(b){return b.getBoundingClientRect().width>0&&/Enviar Prova/.test(b.innerText||'')})`;

// A linha do pedido = o `a.return-row-item` (visto 25/09: cabeçalho com "ID do
// Pedido" + conteúdo com R$, status e o botão). Subir até "ID do Pedido" parava
// na célula do ID; subir até "ID do Pedido + R$" pegava a página inteira quando
// a busca deixa 1 resultado só.
const JS_LINHA = (pedido, de) =>
  `(function(e){var a=e;while(a&&!(a.classList&&a.classList.contains('return-row-item')))a=a.parentElement;return a&&(a.innerText||'').indexOf(${JSON.stringify(pedido)})>=0?a:null;})(${de})`;

// A lista às vezes demora e a busca volta vazia (297335, 25/09 16:11: a mesma
// busca achou em 1 s antes e depois) — tenta a busca inteira 3 vezes.
async function buscar(page, pedido) {
  let erro;
  for (let tentativa = 1; tentativa <= 3; tentativa++) {
    try {
      return await buscarUmaVez(page, pedido);
    } catch (e) {
      erro = e;
      if (/login/.test(String(e && e.message))) break;
      await sleep(3000 * tentativa);
    }
  }
  throw erro;
}

async function buscarUmaVez(page, pedido) {
  await page.goto(`${SELLER}/portal/sale/returnrefundcancel`, { waitUntil: "networkidle2", timeout: 60000 }).catch(() => {});
  const url = page.url();
  const senha = await page.evaluate(`!!document.querySelector('input[type="password"]')`).catch(() => false);
  if (/signin|login|\/account\//i.test(url) || senha) throw new Error("login do Seller Center caiu — precisa de uma pessoa");
  await page.waitForSelector(SEL_BUSCA, { visible: true, timeout: 30000 });
  // foco pelo elemento, não por clique: com a janela noutro tamanho o clique
  // caiu na aba "Todos" e a busca ficou vazia (25/09)
  for (let i = 0; i < 3; i++) {
    await page.focus(SEL_BUSCA);
    await page.evaluate(`(function(){var i=document.querySelector(${JSON.stringify(SEL_BUSCA)});if(i&&i.select)i.select();})()`);
    await page.keyboard.press("Backspace");
    await page.keyboard.type(pedido, { delay: 40 });
    await sleep(500);
    const v = await page.evaluate(`(document.querySelector(${JSON.stringify(SEL_BUSCA)})||{}).value`);
    if ((v || "").trim() === pedido) break;
    if (i === 2) throw new Error("não consegui escrever o pedido na busca da lista");
  }
  // Enter não filtra (visto em 24/09): tem que ser o botão.
  const ok = await page.evaluate(`(function(){var b=[...document.querySelectorAll('button')].find(function(e){return (e.innerText||'').trim()==='Aplicar'&&e.getBoundingClientRect().width>0});if(b)b.click();return !!b;})()`);
  if (!ok) throw new Error("botão Aplicar não encontrado na lista de devoluções");
  for (let i = 0; i < 20; i++) {
    await sleep(1000);
    // só dentro das linhas da lista: a sugestão da busca ("Procurar por …") também
    // mostra o nº do pedido e pegava ela no lugar da linha (297335, 25/09)
    const linha = await page.evaluate(
      `(function(){var l=[...document.querySelectorAll('a.return-row-item')].find(function(r){return (r.innerText||'').indexOf(${JSON.stringify(pedido)})>=0});return l?l.innerText:null;})()`
    );
    if (linha) return linha;
  }
  throw new Error(`o pedido ${pedido} não apareceu na lista de devoluções desta loja`);
}

function situacao(linha) {
  const t = linha || "";
  const pendente = /Upload Evidence|Enviar evid[eê]ncia/i.test(t);
  const m = /evidence by\s*(\d{2})-(\d{2})-(\d{4})/i.exec(t);
  return {
    pendente,
    prazo: m ? `${m[1]}/${m[2]}/${m[3]}` : null,
    disputa: (/(\d+)\s*ª\s*Disputa[^\n]*\n?[^\n]*/i.exec(t) || [""])[0].replace(/\n/g, " — "),
  };
}

async function conferir(page, a) {
  const linha = await buscar(page, a.pedido);
  return { ok: true, ...situacao(linha), linha: linha.slice(0, 900) };
}

// Fotos da 1ª disputa ("Informações da disputa") na página da devolução —
// reserva pra quando o chamado não tem foto. Numa aba nova, com a sessão da loja.
async function fotos(browser, page, a) {
  await buscar(page, a.pedido);
  const href = await page.evaluate(
    `(function(){var l=[...document.querySelectorAll('a[href*="/portal/sale/return/"]')].find(function(x){return !!${JS_LINHA(a.pedido, "x")}});return l?l.href:null;})()`
  );
  if (!href) throw new Error("link da devolução não achado");
  const pasta = path.resolve(a.pasta);
  fs.mkdirSync(pasta, { recursive: true });
  const aba = await browser.newPage();
  const saida = [];
  try {
    await aba.goto(href, { waitUntil: "networkidle2", timeout: 60000 }).catch(() => {});
    await sleep(2500);
    await aba.evaluate("window.scrollTo(0, document.body.scrollHeight)");
    await sleep(2000);
    const srcs = await aba.evaluate(
      `[...new Set([...document.querySelectorAll('img')].filter(function(i){if(i.naturalWidth<400)return false;var s=i.parentElement;for(var k=0;k<8&&s;k++,s=s.parentElement){if(/Informações da disputa/.test(s.innerText||''))return true;}return false;}).map(function(i){return i.src}))]`
    );
    let n = 0;
    for (const src of srcs.slice(0, 10)) {
      const r = await aba.goto(src, { timeout: 30000 }).catch(() => null);
      if (!r || r.status() !== 200) continue;
      const buf = await r.buffer();
      const arq = path.join(pasta, `shopee-disputa-${String(++n).padStart(2, "0")}.jpg`);
      fs.writeFileSync(arq, buf);
      saida.push({ arquivo: arq, bytes: buf.length, origem: "Shopee — fotos da 1ª disputa" });
    }
  } finally {
    await aba.close().catch(() => {});
    await page.bringToFront().catch(() => {});
  }
  return { ok: true, devolucao: href, fotos: saida };
}

// Foto pronta = a Shopee confirmou o upload (POST .../uploadapi/.../notify) e o
// quadrinho liberou os botões ver/apagar (`.actions-layer` sem display:none).
// A classe "uploading" do quadrinho NÃO some nunca — não serve (visto 25/09).
async function estadoJanela(page) {
  return page.evaluate(
    `(function(){var m=${JS_MODAL};if(!m)return null;var t=m.innerText||'';var c=/(\\d)\\s*\\/\\s*3/.exec(t);var env=[...m.querySelectorAll('button')].find(function(b){return (b.innerText||'').trim()==='Enviar'});var itens=[...m.querySelectorAll('.evidence-item')];return {contador:c?Number(c[1]):null,miniaturas:[...m.querySelectorAll('img')].filter(function(i){return i.getBoundingClientRect().width>0&&i.complete&&i.naturalWidth>0}).length,prontas:itens.filter(function(it){var l=it.querySelector('.actions-layer');return l&&l.style.display!=='none'}).length,enviar_ativo:!!env&&!env.disabled&&!/disabled/.test(env.className),erro:(t.match(/[^\\n]*(falh|erro|inválid|excede|grande demais)[^\\n]*/i)||[null])[0]};})()`
  );
}

async function clicarNaJanela(page, texto) {
  return page.evaluate(
    `(function(){var m=${JS_MODAL};if(!m)return false;var b=[...m.querySelectorAll('button')].find(function(e){return (e.innerText||'').trim()===${JSON.stringify(texto)}});if(!b)return false;b.click();return true;})()`
  );
}

async function enviar(page, a) {
  const arquivos = String(a.arquivos || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!arquivos.length || arquivos.length > MAX_ARQUIVOS) throw new Error(`de 1 a ${MAX_ARQUIVOS} arquivos`);
  for (const f of arquivos) {
    if (!fs.existsSync(f)) throw new Error(`arquivo não existe: ${f}`);
    const mb = fs.statSync(f).size / 1048576;
    const video = /\.(mp4|mov)$/i.test(f);
    if (mb > (video ? 30 : 10)) throw new Error(`${path.basename(f)} tem ${mb.toFixed(1)} MB (limite ${video ? 30 : 10} MB)`);
  }
  const antes = situacao(await buscar(page, a.pedido));
  if (!antes.pendente) return { ok: false, motivo: "a linha do pedido não pede evidência (sem Upload Evidence)", ...antes };

  const clicou = await page.evaluate(
    `(function(){var b=[...document.querySelectorAll('button')].find(function(e){return (e.innerText||'').indexOf('Upload Evidence')>=0&&!!${JS_LINHA(a.pedido, "e")}});if(!b)return false;b.scrollIntoView({block:'center'});b.click();return true;})()`
  );
  if (!clicou) throw new Error("botão Upload Evidence da linha do pedido não achado");
  let janela = null;
  for (let i = 0; i < 15 && !janela; i++) {
    await sleep(1000);
    janela = await page.evaluateHandle(JS_MODAL).then((h) => h.asElement());
  }
  if (!janela) throw new Error("a janela Enviar Prova não abriu");
  const input = await janela.$("input[type=file]");
  if (!input) throw new Error("a janela não tem campo de arquivo");
  let subidas = 0;
  const contar = (r) => {
    if (r.request().method() === "POST" && r.status() === 200 && /\/uploadapi\/.*notify/.test(r.url())) subidas++;
  };
  page.on("response", contar);
  await input.uploadFile(...arquivos);

  // espera cada foto subir pra Shopee (ela manda na hora em que o arquivo entra)
  const n = arquivos.length;
  let est = null;
  for (let i = 0; i < 90; i++) {
    await sleep(1000);
    est = await estadoJanela(page);
    if (est && est.miniaturas >= n && est.prontas >= n && subidas >= n && est.enviar_ativo) break;
  }
  page.off("response", contar);
  await sleep(1500);
  if (est) est.subidas = subidas;
  if (a.print) await page.screenshot({ path: a.print.replace(/\.png$/, "-antes.png") }).catch(() => {});
  if (!est || est.miniaturas < n || est.prontas < n || subidas < n) {
    await clicarNaJanela(page, "Voltar");
    return { ok: false, motivo: "as fotos não subiram todas pra Shopee", janela: est, ...antes };
  }
  if (!a["de-verdade"]) {
    await clicarNaJanela(page, "Voltar");
    await sleep(1500);
    return { ok: true, seco: true, anexadas: arquivos.length, janela: est, ...antes };
  }
  if (!est.enviar_ativo) {
    await clicarNaJanela(page, "Voltar");
    return { ok: false, motivo: "o botão Enviar não ficou ativo", janela: est, ...antes };
  }

  await clicarNaJanela(page, "Enviar");
  // Depois do Enviar a Shopee pergunta (visto no 294571, 25/09): "Contestar
  // Shopee — Tem certeza que deseja solicitar uma contestação? A Shopee
  // reserva-se o direito exclusivo de tomar a decisão final." Voltar/Confirmar.
  // É a confirmação do próprio envio; outra janela qualquer → para e avisa.
  const JS_OUTRA = `[...document.querySelectorAll('.eds-modal__box, .eds-modal__content')].find(function(b){return b.getBoundingClientRect().width>0&&!/Enviar Prova|Feedback da Plataforma/.test(b.innerText||'')})`;
  let outra = null;
  let confirmou = false;
  let fechou = false;
  for (let i = 0; i < 30; i++) {
    await sleep(1000);
    const aberta = await page.evaluate(`!!(${JS_MODAL})`);
    outra = await page.evaluate(`(function(){var m=${JS_OUTRA};return m?(m.innerText||'').slice(0,500):null;})()`);
    if (outra && !confirmou) {
      if (!/contesta|prova|evid[eê]ncia/i.test(outra)) break; // janela desconhecida: não clica
      if (a.print) await page.screenshot({ path: a.print.replace(/\.png$/, "-confirmar.png") }).catch(() => {});
      confirmou = await page.evaluate(
        `(function(){var m=${JS_OUTRA};var b=m&&[...m.querySelectorAll('button')].find(function(e){return (e.innerText||'').trim()==='Confirmar'});if(b)b.click();return !!b;})()`
      );
      continue;
    }
    if (!aberta && !outra) {
      fechou = true;
      break;
    }
  }
  await sleep(2000);
  if (a.print) await page.screenshot({ path: a.print }).catch(() => {});
  let depoisErro = null;
  const depois = situacao(await buscar(page, a.pedido).catch((e) => ((depoisErro = String(e.message || e)), "Upload Evidence")));
  if (a.print) await page.screenshot({ path: a.print.replace(/\.png$/, "-lista.png") }).catch(() => {});
  return {
    ok: fechou && !depois.pendente,
    enviado: fechou,
    confirmou,
    anexadas: arquivos.length,
    antes,
    depois,
    outra_janela: outra,
    motivo: !fechou
      ? (outra && !confirmou ? "apareceu uma janela que eu não conheço depois do Enviar — não cliquei" : "a janela não fechou depois do Enviar/Confirmar")
      : depoisErro ? `enviado, mas não consegui reler a linha: ${depoisErro}`
      : depois.pendente ? "a linha ainda pede evidência depois do envio" : null,
  };
}

const a = args();
if (!a.ws || !a.pedido || !["conferir", "fotos", "enviar"].includes(a.cmd)) {
  console.log(JSON.stringify({ ok: false, motivo: "uso: conferir|fotos|enviar --ws WS --pedido X …" }));
  process.exit(2);
}
let browser;
let saida;
try {
  // o perfil pode estar acabando de abrir: tenta a conexão algumas vezes
  for (let i = 0; i < 5 && !browser; i++) {
    browser = await puppeteer.connect({ browserWSEndpoint: a.ws, defaultViewport: null }).catch((e) => {
      if (i === 4) throw e;
      return sleep(3000).then(() => undefined);
    });
  }
  const abas = await browser.pages();
  const page = abas.find((p) => p.url().startsWith(SELLER)) || abas[0] || (await browser.newPage());
  await page.bringToFront().catch(() => {});
  if (a.cmd === "conferir") saida = await conferir(page, a);
  else if (a.cmd === "fotos") saida = await fotos(browser, page, a);
  else saida = await enviar(page, a);
} catch (e) {
  saida = { ok: false, motivo: String((e && e.message) || e) };
} finally {
  browser?.disconnect();
}
console.log(JSON.stringify(saida));
