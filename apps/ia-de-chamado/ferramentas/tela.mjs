// Mãos da IA de Chamado (Claude Code) no navegador de um perfil do AdsPower.
// 29/09/2026 (294654): a IA do Hermes ficou sem cota e o Claude Code conduziu a
// tela da Shopee assim — abriu o atendimento, conversou, mandou a evidência.
//
//   ferramentas/tela <perfil> estado | ws | aba N | ir URL | texto [cauda]
//     | foto arq.png | clicar "texto exato" | ponto X Y
//     | escrever "placeholder" "texto" [enter] | tecla Enter | vivo | js "expr"
//
// Abre o perfil se estiver fechado (quem chama FECHA no fim: adspower.py fechar).
// A aba escolhida fica em tmp/.aba-<perfil>. Coordenadas em px CSS (print ÷ 2).
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(
  path.join(
    process.env.PUPPETEER_DIR ||
      path.join(process.env.HOME, "DaVinci/executor-leitura-chamado/node_modules"),
    "/",
  ),
);
const puppeteer = require("puppeteer-core");
const BASE = process.env.ADSPOWER_API_BASE || "http://local.adspower.net:50325";

const [perfil, cmd, ...args] = process.argv.slice(2);
const ABA = `tmp/.aba-${perfil}`;
const espera = (ms) => new Promise((ok) => setTimeout(ok, ms));

async function ws() {
  let r = await (await fetch(`${BASE}/api/v1/browser/active?user_id=${perfil}`)).json();
  if (r?.data?.status === "Active") return r.data.ws.puppeteer;
  await espera(1200); // limite da API local do AdsPower: ~1 chamada/s
  r = await (await fetch(`${BASE}/api/v1/browser/start?user_id=${perfil}&open_tabs=1`)).json();
  if (r.code !== 0) throw new Error(`AdsPower: ${r.msg}`);
  return r.data.ws.puppeteer;
}

if (!perfil || !cmd) {
  console.log("uso: ferramentas/tela <perfil> <comando> [args]");
  process.exit(1);
}

const browser = await puppeteer.connect({ browserWSEndpoint: await ws(), defaultViewport: null });
try {
  const paginas = (await browser.pages()).filter((p) => !p.url().startsWith("chrome-extension"));
  let n = fs.existsSync(ABA) ? Number(fs.readFileSync(ABA, "utf8")) : -1;
  if (!(n >= 0 && n < paginas.length)) n = paginas.length - 1;
  const page = paginas[n];
  const saida = (o) => console.log(typeof o === "string" ? o : JSON.stringify(o, null, 1));

  if (cmd === "ws") {
    saida(browser.wsEndpoint());
  } else if (cmd === "estado") {
    saida(paginas.map((p, i) => `${i === n ? "*" : " "}${i} ${p.url()}`).join("\n"));
  } else if (cmd === "aba") {
    fs.mkdirSync("tmp", { recursive: true });
    fs.writeFileSync(ABA, String(Number(args[0])));
    saida(`aba ${args[0]}: ${paginas[Number(args[0])]?.url()}`);
  } else if (cmd === "ir") {
    await page.goto(args[0], { waitUntil: "networkidle2", timeout: 60000 }).catch(() => {});
    saida(page.url());
  } else if (cmd === "texto") {
    const cauda = Number(args[0] || 3000);
    const t = await page.evaluate(() => document.body.innerText);
    saida(`[${page.url()}]\n` + t.slice(-cauda));
  } else if (cmd === "clicar") {
    // o elemento visível mais interno cujo texto é exatamente o pedido
    const r = await page.evaluate((alvo) => {
      const vis = (e) => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0; };
      const todos = [...document.querySelectorAll("*")].filter(
        (e) => vis(e) && (e.innerText || "").trim() === alvo,
      );
      const folhas = todos.filter((e) => !todos.some((o) => o !== e && e.contains(o)));
      if (!folhas.length) return { ok: false, achados: 0 };
      const e = folhas[folhas.length - 1];
      e.scrollIntoView({ block: "center" });
      const b = e.getBoundingClientRect();
      return { ok: true, achados: folhas.length, x: b.x + b.width / 2, y: b.y + b.height / 2, tag: e.tagName };
    }, args[0]);
    if (r.ok) await page.mouse.click(r.x, r.y);
    await espera(2500);
    saida(r);
  } else if (cmd === "ponto") {
    await page.mouse.click(Number(args[0]), Number(args[1]));
    await espera(2500);
    saida("ok");
  } else if (cmd === "escrever") {
    const [ph, texto, enter] = args;
    const sel = `[placeholder="${ph}"]`;
    await page.waitForSelector(sel, { timeout: 15000 });
    await page.click(sel);
    await page.type(sel, texto, { delay: 15 });
    const valor = await page.$eval(sel, (e) => e.value);
    if (enter === "enter") await page.keyboard.press("Enter");
    await espera(2500);
    saida({ escrito: valor, enviado: enter === "enter" });
  } else if (cmd === "tecla") {
    await page.keyboard.press(args[0]);
    saida("ok");
  } else if (cmd === "vivo") {
    // chat da Shopee: "Você gostaria que continuássemos? … encerrado após 60s"
    const r = await page.evaluate(() => {
      const t = document.body.innerText;
      const i = t.lastIndexOf("O chat será encerrado");
      if (i < 0) return "sem aviso";
      const resto = t.slice(i, t.indexOf("Click to view chat history", i));
      const linhas = resto.split("\n").map((x) => x.trim()).filter(Boolean).slice(1);
      if (linhas.some((x) => !["Não", "Sim", "Obrigado por aguardar", "1."].includes(x))) return "já respondido";
      const sims = [...document.querySelectorAll("*")].filter(
        (e) => e.children.length === 0 && (e.innerText || "").trim() === "Sim",
      );
      if (!sims.length) return "sem botão";
      sims[sims.length - 1].scrollIntoView({ block: "center" });
      sims[sims.length - 1].click();
      return "cliquei Sim";
    });
    saida(r);
  } else if (cmd === "foto") {
    fs.mkdirSync(path.dirname(args[0]) || ".", { recursive: true });
    await page.screenshot({ path: args[0] });
    saida(args[0]);
  } else if (cmd === "js") {
    saida(await page.evaluate(args[0]));
  } else {
    saida(`comando desconhecido: ${cmd}`);
    process.exitCode = 1;
  }
} finally {
  await browser.disconnect();
}
