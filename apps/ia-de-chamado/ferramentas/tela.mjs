// Mãos da IA de Chamado (Claude Code) no navegador de um perfil do AdsPower.
// 29/09/2026 (294654): a IA do Hermes ficou sem cota e o Claude Code conduziu a
// tela da Shopee assim — abriu o atendimento, conversou, mandou a evidência.
//
//   ferramentas/tela <perfil> estado | ws | aba N | ir URL | texto [cauda]
//     | foto arq.png | clicar "texto exato" | ponto X Y
//     | campos | escrever "campo" "texto" [enter] | anexar "campo" tmp/a.jpg[,tmp/b.jpg]
//     | captcha | tecla Enter | vivo | js "expr"
//
// Abre o perfil se estiver fechado (quem chama FECHA no fim: adspower.py fechar).
// A aba escolhida fica em tmp/.aba-<perfil>. Coordenadas em px CSS (print ÷ 2).
//
// 30/09 (297130): o formulário "Fale conosco › E-mail" do ML tem caixas sem
// placeholder de verdade e o `escrever` antigo só achava por placeholder — a IA
// chegou no formulário e não conseguiu escrever. Agora `campos` numera as caixas
// visíveis com o rótulo que a pessoa vê, e `escrever`/`anexar` aceitam "#N", o
// placeholder ou um pedaço do rótulo. Todo comando que mexe na tela avisa quando
// aparece CAPTCHA/"não sou robô" (a IA para e chama gente — CLAUDE.md).
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

// Roda NA PÁGINA: CAPTCHA à vista? "desafio" (quebra-cabeça/imagens abertas),
// "caixa" ("não sou um robô" pra marcar), "texto: …" (aviso escrito) ou null.
// O selo do reCAPTCHA invisível (canto da tela) não conta: ele só vira desafio
// se o Google desconfiar — aí aparece o `bframe`.
function captchaNaPagina() {
  const vis = (e) => {
    const b = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    if (b.width < 30 || b.height < 30 || b.bottom <= 0 || b.right <= 0) return false;
    if (s.visibility === "hidden" || s.display === "none" || Number(s.opacity) === 0) return false;
    return e.checkVisibility ? e.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) : true;
  };
  const frames = [...document.querySelectorAll("iframe")].filter(vis);
  const src = (f) => f.src || "";
  const desafio = /recaptcha\/(api2|enterprise)\/bframe|hcaptcha\.com\/.*(challenge|frame=challenge)|challenges\.cloudflare\.com|geetest|arkoselabs|funcaptcha/;
  if (frames.some((f) => desafio.test(src(f)) && f.getBoundingClientRect().height > 100)) return "desafio";
  if (
    frames.some(
      (f) =>
        (/recaptcha\/(api2|enterprise)\/anchor/.test(src(f)) && !/size=invisible/.test(src(f)) && !f.closest(".grecaptcha-badge")) ||
        /hcaptcha\.com\/.*frame=checkbox/.test(src(f)),
    )
  )
    return "caixa";
  const t = document.body.innerText || "";
  const frase = [
    /n[ãa]o sou (um )?rob[ôo]/i,
    /i'?m not a robot/i,
    /arraste o controle deslizante/i,
    /deslize para completar/i,
    /slide to complete the puzzle/i,
    /complete o quebra-cabe[çc]a/i,
  ].find((re) => re.test(t));
  return frase ? `texto: ${t.match(frase)[0]}` : null;
}

// Roda NA PÁGINA: caixas visíveis (texto, área de texto, editável, arquivo),
// numeradas na ordem da tela e marcadas com data-ia-campo pra achar de novo.
// `rotulo` = o que a pessoa lê pra saber o que vai ali (label, aria, placeholder,
// data-placeholder, texto em volta); `textos` = tudo isso, pra casar o pedido.
function camposDaPagina() {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const sel = [
    "input:not([type])",
    ...["text", "search", "email", "tel", "number", "url", "file"].map((t) => `input[type=${t}]`),
    "textarea",
    '[contenteditable=""]',
    '[contenteditable="true"]',
  ].join(",");
  const vis = (e) => {
    const b = e.getBoundingClientRect();
    if (b.width > 0 && b.height > 0) return true;
    return false;
  };
  const todos = [...document.querySelectorAll(sel)].filter(
    (e) => !e.disabled && !e.readOnly && e.name !== "g-recaptcha-response",
  );
  // arquivo costuma ser um input invisível dentro da área "Selecionar arquivos"
  const campos = todos.filter((e) => vis(e) || (e.type === "file" && e.parentElement && vis(e.parentElement)));
  document.querySelectorAll("[data-ia-campo]").forEach((e) => e.removeAttribute("data-ia-campo"));
  const eCampo = new Set(campos);
  const textoAntes = (el) => {
    // último texto visível antes da caixa (rótulo solto, fora de <label>)
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let ultimo = "";
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (el.contains(n)) break;
      if (!(n.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) break;
      const p = n.parentElement;
      if (!p || ["SCRIPT", "STYLE", "NOSCRIPT", "OPTION"].includes(p.tagName)) continue;
      const t = norm(n.textContent);
      if (t && vis(p)) ultimo = t;
    }
    return ultimo;
  };
  const textoEmVolta = (el) => {
    // sobe até achar um bloco com texto que não tenha outra caixa dentro
    let a = el.parentElement;
    for (let i = 0; a && i < 5; i++, a = a.parentElement) {
      if ([...eCampo].some((o) => o !== el && a.contains(o))) return "";
      let t = norm(a.innerText);
      if (el.isContentEditable) t = norm(t.replace(norm(el.innerText), ""));
      if (t) return t.slice(0, 200);
    }
    return "";
  };
  return campos.map((e, i) => {
    const n = i + 1;
    e.setAttribute("data-ia-campo", String(n));
    const porId = (ids) =>
      norm((ids || "").split(/\s+/).map((id) => document.getElementById(id)?.innerText || "").join(" "));
    const textos = [
      norm([...(e.labels || [])].map((l) => l.innerText).join(" ")),
      norm(e.getAttribute("aria-label")),
      porId(e.getAttribute("aria-labelledby")),
      norm(e.getAttribute("placeholder")),
      norm(e.getAttribute("data-placeholder")),
      norm(e.getAttribute("title")),
      textoEmVolta(e),
    ].filter(Boolean);
    const proprios = [...new Set(textos)];
    if (!textos.length) textos.push(textoAntes(e));
    const tipo = e.isContentEditable ? "editavel" : e.tagName === "TEXTAREA" ? "area" : e.type === "file" ? "arquivo" : "linha";
    const valor = e.isContentEditable ? norm(e.innerText) : e.type === "file" ? [...e.files].map((f) => f.name).join(", ") : e.value;
    return {
      n,
      tipo,
      rotulo: (textos[0] || "(sem rótulo)").slice(0, 200),
      textos: [...new Set(textos.filter(Boolean))].map((t) => t.slice(0, 200)),
      proprios: proprios.map((t) => t.slice(0, 200)),
      valor: (valor || "").slice(0, 120),
      ...(e.maxLength > 0 ? { max: e.maxLength } : {}),
      ...(e.required || e.getAttribute("aria-required") === "true" ? { obrigatorio: true } : {}),
    };
  });
}

// Qual caixa o pedido quer: "#N", texto igual a um rótulo/placeholder, ou
// pedaço de um rótulo (sem acento, sem maiúscula). Mais de uma → erro com a lista.
function escolherCampo(lista, alvo, soArquivo) {
  const tira = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
  const cand = lista.filter((c) => (soArquivo ? c.tipo === "arquivo" : c.tipo !== "arquivo"));
  const m = /^#(\d+)$/.exec(alvo.trim());
  if (m) {
    const c = lista.find((x) => x.n === Number(m[1]));
    if (!c) return { erro: `não existe a caixa #${m[1]}` };
    if (soArquivo !== (c.tipo === "arquivo")) return { erro: `a caixa #${m[1]} é do tipo ${c.tipo}` };
    return { campo: c };
  }
  const a = tira(alvo);
  let achados = cand.filter((c) => c.textos.some((t) => tira(t) === a));
  // pedaço: primeiro no rótulo da própria caixa; o texto solto antes dela
  // (caixa sem rótulo nenhum) só conta se nada mais casar
  if (!achados.length) achados = cand.filter((c) => c.proprios.some((t) => tira(t).includes(a)));
  if (!achados.length) achados = cand.filter((c) => c.textos.some((t) => tira(t).includes(a)));
  if (achados.length === 1) return { campo: achados[0] };
  return { erro: achados.length ? "mais de uma caixa casa — use #N" : "nenhuma caixa casa — use #N", achados: achados.map((c) => c.n) };
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
  const captcha = () => page.evaluate(captchaNaPagina).catch(() => null);
  const avisoCaptcha = (c) => `\n⚠ CAPTCHA NA TELA (${c}) — pare aqui, não tente passar (CLAUDE.md).`;
  // espera a caixa aparecer (chat que ainda está montando) até 15 s
  const acharCampo = async (alvo, soArquivo) => {
    let r;
    for (let i = 0; i < 8; i++) {
      const lista = await page.evaluate(camposDaPagina);
      r = { ...escolherCampo(lista, alvo, soArquivo), lista };
      if (r.campo || r.achados?.length) return r;
      await espera(2000);
    }
    return r;
  };
  const resumoLista = (lista) => lista.map((c) => `#${c.n} ${c.tipo}: ${c.rotulo}`);

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
    const c = await captcha();
    saida(page.url() + (c ? avisoCaptcha(c) : ""));
  } else if (cmd === "texto") {
    const cauda = Number(args[0] || 3000);
    const t = await page.evaluate(() => document.body.innerText);
    const c = await captcha();
    saida(`[${page.url()}]\n` + t.slice(-cauda) + (c ? avisoCaptcha(c) : ""));
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
    saida({ ...r, captcha: await captcha() });
  } else if (cmd === "ponto") {
    await page.mouse.click(Number(args[0]), Number(args[1]));
    await espera(2500);
    const c = await captcha();
    saida("ok" + (c ? avisoCaptcha(c) : ""));
  } else if (cmd === "campos") {
    const lista = await page.evaluate(camposDaPagina);
    saida({ url: page.url(), captcha: await captcha(), campos: lista.map(({ textos, proprios, ...c }) => c) });
  } else if (cmd === "escrever") {
    const [alvo, texto, enter] = args;
    const r = await acharCampo(alvo, false);
    if (!r.campo) {
      saida({ ok: false, erro: r.erro, achados: r.achados, campos: resumoLista(r.lista) });
      process.exitCode = 1;
    } else {
      const h = await page.$(`[data-ia-campo="${r.campo.n}"]`);
      await h.evaluate((e) => e.scrollIntoView({ block: "center" }));
      await h.click();
      // apaga o que já estava escrito (repetir o comando não duplica o texto)
      await h.evaluate((e) => {
        if (e.isContentEditable) {
          const s = window.getSelection();
          s.selectAllChildren(e);
        } else e.select?.();
      });
      await page.keyboard.press("Backspace");
      // quebra de linha: Enter pode ENVIAR (chat, formulário de uma linha só) —
      // caixa de uma linha junta com espaço; nas outras vai Shift+Enter.
      const linhas = r.campo.tipo === "linha" ? [texto.replace(/\s*\n+\s*/g, " ")] : texto.split("\n");
      for (let i = 0; i < linhas.length; i++) {
        if (i > 0) {
          await page.keyboard.down("Shift");
          await page.keyboard.press("Enter");
          await page.keyboard.up("Shift");
        }
        if (linhas[i]) await page.keyboard.type(linhas[i], { delay: 15 });
      }
      const valor = await h.evaluate((e) => (e.isContentEditable ? e.innerText : e.value));
      const tira = (s) => (s || "").replace(/\s+/g, " ").trim();
      const confere = tira(valor) === tira(texto);
      if (enter === "enter") await page.keyboard.press("Enter");
      await espera(2500);
      saida({
        ok: true,
        campo: `#${r.campo.n} ${r.campo.rotulo}`,
        escrito: valor,
        confere,
        ...(confere ? {} : { aviso: r.campo.max ? `a caixa aceita no máximo ${r.campo.max} caracteres` : "o que ficou na caixa não é igual ao texto" }),
        enviado: enter === "enter",
        captcha: await captcha(),
      });
    }
  } else if (cmd === "anexar") {
    const [alvo, lista] = args;
    const arquivos = (lista || "").split(",").map((a) => a.trim()).filter(Boolean);
    const fora = arquivos.filter((a) => !path.resolve(a).startsWith(path.resolve("tmp") + path.sep) || !fs.existsSync(a));
    if (!arquivos.length || fora.length) {
      saida({ ok: false, erro: "arquivos precisam existir dentro de tmp/", fora });
      process.exitCode = 1;
    } else {
      const r = await acharCampo(alvo, true);
      if (!r.campo) {
        saida({ ok: false, erro: r.erro, achados: r.achados, campos: resumoLista(r.lista) });
        process.exitCode = 1;
      } else {
        const h = await page.$(`[data-ia-campo="${r.campo.n}"]`);
        await h.uploadFile(...arquivos.map((a) => path.resolve(a)));
        await espera(3000);
        const nomes = await h.evaluate((e) => [...e.files].map((f) => f.name));
        saida({ ok: true, campo: `#${r.campo.n} ${r.campo.rotulo}`, anexados: nomes, captcha: await captcha() });
      }
    }
  } else if (cmd === "captcha") {
    saida({ captcha: await captcha() });
  } else if (cmd === "tecla") {
    await page.keyboard.press(args[0]);
    await espera(1500);
    const c = await captcha();
    saida("ok" + (c ? avisoCaptcha(c) : ""));
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
