/**
 * Pedido de senha ao comprador no chat da TikTok (devolução "Bloqueado").
 *
 * Vinicius, 29/09/2026: "conseguimos fazer isso também no TikTok? solicitar a
 * senha ao cliente" → "podemos tentar via script". A API da TikTok não deixa a
 * loja falar com o comprador (falta o escopo seller.customer_service), mas a
 * tela deixa. O caminho foi medido no 296301 (TikTok Barbosa, 29/09):
 *
 *   1. página do pedido (/order/detail?order_no=…): a própria página pede
 *      `mGetContactBuyerLinkByOrder`, que devolve o link do chat DAQUELE
 *      comprador (urlPc, com token). O ícone de chat da página não abre nada no
 *      clique automático — vamos direto no link;
 *   2. chat "Bate-papo da loja": textarea (até 2000) + botão "Enviar"; o
 *      input[type=file] aceita jpg/png e abre a janela "Enviar fotos"
 *      (Cancelar/Enviar);
 *   3. o aviso de cookies fica num shadow DOM e COBRE a caixa de texto.
 *
 * Duas regras que vieram de um susto no teste: NUNCA teclado e NUNCA clique por
 * coordenada (o clique caiu no aviso de cookies, as teclas foram pra página e a
 * tela trocou de conversa). O texto entra pelo value da textarea, e a conversa
 * é conferida (cabeçalho = comprador do pedido + painel com o nº do pedido)
 * antes de escrever E de novo antes de clicar em Enviar.
 *
 * Só manda com commit=true E TIKTOK_CALIBRATED=true; sem isso é modo seco:
 * abre, confere a conversa e devolve o que faria.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import puppeteer, { type Browser, type Page } from "puppeteer-core";
import { cfg } from "./config";
import * as adspower from "./adspower";
import * as davinci from "./davinci";
import { log } from "./log";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface PedidoSenha {
  linha_id?: string;
  pedido_bling?: string;
  pedido_tiktok?: string;
  conta?: string;
  texto?: string;
  texto_loja?: string; // com "{LOJA}" pro nome que o comprador vê no chat
  foto_anexo_id?: string | null;
  commit?: boolean;
}

/** O que volta pro DaVinci (vira `result` da tarefa; o servidor lê `motivo`
 *  pra saber se gasta tentativa e `detalhe` vira o recado na tela). */
export interface Resultado {
  ok: boolean;
  motivo?: string;
  detalhe?: string;
  texto?: string;
  loja?: string;
  comprador?: string;
  perfil?: string;
  com_foto?: boolean;
  ja_enviada?: boolean;
  seco?: boolean;
}

class Falha extends Error {
  constructor(public motivo: string, detalhe: string) {
    super(detalhe);
    this.name = "Falha";
  }
}

// ---------------------------------------------------------------- perfil

function semAcento(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
}

/** "TikTok Barbosa" (conta do Bling) → perfil "Barbosa - Tiktok" do grupo das
 *  lojas. O perfil de contabilidade ("Barbosa - ml sh tk te") não casa de
 *  propósito: é cópia de reserva. O que não casar vai no TIKTOK_PERFIS_EXTRA
 *  ({"Loja 206081932": "k1dkfg0l"} — a JLAS aparece assim no Bling). */
export async function acharPerfil(conta: string): Promise<{ userId: string; nome: string } | null> {
  const chave = semAcento(conta);
  const extra = cfg.tiktokPerfisExtra[chave];
  if (extra) return { userId: extra, nome: `(TIKTOK_PERFIS_EXTRA) ${extra}` };
  const loja = semAcento(chave.replace(/tik\s*tok/g, " "));
  if (!loja) return null;
  for (const p of await adspower.listAll()) {
    const m = /^(.+?)\s*-\s*tik\s*tok\s*$/i.exec(p.name.trim());
    if (m && semAcento(m[1]) === loja) return { userId: p.user_id, nome: p.name };
  }
  return null;
}

// ---------------------------------------------------------------- tela

/** Clica num botão pelo texto, procurando também dentro de shadow DOM (o aviso
 *  de cookies da TikTok mora num). Clique pelo DOM, nunca por coordenada.
 *  Sem função com nome dentro do evaluate: o tsx (keepNames) injeta um
 *  `__name(...)` que não existe na página ("__name is not defined"). */
async function clicarPeloTexto(page: Page, rotulo: string): Promise<boolean> {
  return page.evaluate((rot) => {
    const pilha: (Document | ShadowRoot)[] = [document];
    while (pilha.length) {
      const raiz = pilha.pop() as Document | ShadowRoot;
      for (const e of Array.from(raiz.querySelectorAll<HTMLElement>("*"))) {
        const papel = e.tagName === "BUTTON" || e.getAttribute("role") === "button";
        if (papel && (e.innerText || e.textContent || "").trim() === rot) {
          e.click();
          return true;
        }
        if (e.shadowRoot) pilha.push(e.shadowRoot);
      }
    }
    return false;
  }, rotulo);
}

/** A conversa aberta é a do comprador do pedido? O cabeçalho da coluna da
 *  conversa (a mesma coluna da caixa de texto) tem o nome de usuário E o painel
 *  da direita tem "#<pedido>". Pela coluna, e não pelo "Adicionar tag": com
 *  etiqueta na conversa ("Pós-vendas", Mini 29/09) esse botão nem aparece. */
interface Conferencia {
  ok: boolean;
  cabecalho: string;
  pedidoNoPainel: boolean;
}

async function conversaCerta(page: Page, usuario: string, pedido: string): Promise<Conferencia> {
  return page.evaluate(
    (u, o) => {
      const ta = Array.from(document.querySelectorAll("textarea")).find((e) => e.getBoundingClientRect().width > 0);
      const pedidoNoPainel = document.body.innerText.includes(`#${o}`);
      if (!ta) return { ok: false, cabecalho: "", pedidoNoPainel };
      const c = ta.getBoundingClientRect();
      const topo = Array.from(document.querySelectorAll<HTMLElement>("body *"))
        .filter((e) => e.childElementCount === 0)
        .map((e) => ({ t: (e.innerText || "").trim(), r: e.getBoundingClientRect() }))
        .filter(({ t, r }) => t && r.width > 0 && r.top >= 0 && r.top < 150 && r.left >= c.left - 40 && r.right <= c.right + 40)
        .sort((a, b) => a.r.top - b.r.top || a.r.left - b.r.left);
      const achou = topo.some((x) => x.t === u);
      return { ok: achou && pedidoNoPainel, cabecalho: achou ? u : topo[0]?.t || "", pedidoNoPainel };
    },
    usuario,
    pedido
  );
}

/** Texto das mensagens da conversa aberta (só a coluna da conversa, acima da
 *  caixa de texto — a lista da esquerda tem a prévia de OUTROS compradores). */
async function textoDaConversa(page: Page): Promise<string> {
  return page.evaluate(() => {
    const ta = Array.from(document.querySelectorAll("textarea")).find((e) => e.getBoundingClientRect().width > 0);
    if (!ta) return "";
    const c = ta.getBoundingClientRect();
    const partes: string[] = [];
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const el = n.parentElement;
      const t = (n.textContent || "").trim();
      if (!el || !t) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.left >= c.left - 40 && r.right <= c.right + 40 && r.bottom <= c.top) partes.push(t);
    }
    return partes.join("\n");
  });
}

function porQue(c: Conferencia): string {
  return `cabeçalho "${c.cabecalho || "?"}", pedido no painel: ${c.pedidoNoPainel ? "sim" : "não"}`;
}

/** Nome da loja como o comprador vê (o do canto superior direito do Seller
 *  Center, ex.: "Barbosa 34"). Vazio = não achou; o texto sai sem o nome. */
async function nomeDaLoja(page: Page): Promise<string> {
  return page.evaluate(() => {
    const largura = window.innerWidth;
    const cands = Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .filter((e) => e.childElementCount === 0)
      .map((e) => ({ t: (e.innerText || "").trim(), r: e.getBoundingClientRect() }))
      .filter(
        ({ t, r }) =>
          r.width > 0 && r.top >= 0 && r.top < 70 && r.left > largura * 0.6 &&
          t.length >= 2 && t.length <= 40 && /[A-Za-zÀ-ú]/.test(t) &&
          !/^(R\$|Assistente|Central do vendedor|Pesquis)/i.test(t)
      )
      .sort((a, b) => b.r.left - a.r.left);
    return cands.length ? cands[0].t : "";
  });
}

async function esperar<T>(fn: () => Promise<T>, ok: (v: T) => boolean, ms: number, passo = 1000): Promise<T> {
  const fim = Date.now() + ms;
  let v = await fn();
  while (!ok(v) && Date.now() < fim) {
    await sleep(passo);
    v = await fn();
  }
  return v;
}

function jaEnviada(conversa: string, pedido: string): boolean {
  return conversa.includes(`do pedido ${pedido}`) && /Recebemos de volta/.test(conversa);
}

/** A conversa já fala de senha (alguém pediu na mão pelo Duoke, ou o comprador
 *  mandou sozinho — Mini 295333, 29/09: "a senha é essa / 2706"). Aí o robô
 *  não repete o pedido: devolve pra uma pessoa olhar o chat. */
function jaFalamDeSenha(conversa: string): boolean {
  return /senha|segredo|desbloque/i.test(conversa);
}

async function conversar(
  page: Page,
  cmdId: string,
  p: PedidoSenha,
  perfilNome: string,
  fotoTmp: { caminho: string | null }
): Promise<Resultado> {
  const pedido = String(p.pedido_tiktok || "").trim();
  const base = cfg.tiktokSellerUrl;

  // 1) página do pedido → link do chat daquele comprador
  const resposta = page
    .waitForResponse((r) => r.url().includes("mGetContactBuyerLinkByOrder"), { timeout: 45_000 })
    .catch(() => null);
  await page
    .goto(`${base}/order/detail?order_no=${encodeURIComponent(pedido)}&shop_region=BR`, {
      waitUntil: "domcontentloaded",
      timeout: 60_000,
    })
    .catch(() => undefined);
  const r = await resposta;
  if (/login|passport\/web|account\/register/i.test(page.url())) {
    throw new Falha("precisa_login", `precisa entrar na loja pelo perfil "${perfilNome}" do AdsPower`);
  }
  if (!r) throw new Falha("tela_mudou", "a página do pedido não trouxe o link do chat (tela mudou ou verificação)");
  let info: any = null;
  try {
    info = ((await r.json()) as any)?.data?.orderIdToContactLinkInfo?.[pedido];
  } catch {
    info = null;
  }
  if (!info?.urlPc) throw new Falha("sem_chat", "a TikTok não liberou chat com o comprador desse pedido");
  const usuario = await esperar(
    () => page.evaluate(() => (/Nome de usuário\n([^\n]+)/.exec(document.body.innerText) || [])[1] || ""),
    (v) => Boolean(v.trim()),
    20_000
  );
  if (!usuario.trim()) throw new Falha("tela_mudou", "não achei o nome de usuário do comprador no pedido");
  const loja = await nomeDaLoja(page).catch(() => "");

  // 2) chat do comprador
  await page.goto(new URL(info.urlPc, base).toString(), { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForSelector("textarea", { timeout: 40_000 }).catch(() => undefined);
  for (let i = 0; i < 3; i++) {
    if (await clicarPeloTexto(page, "Recusar cookies opcionais")) break;
    await sleep(2000);
  }
  const certa = await esperar(() => conversaCerta(page, usuario.trim(), pedido), (c) => c.ok, 20_000);
  if (!certa.ok) {
    throw new Falha(
      "conversa_errada",
      `a conversa aberta não é a de ${usuario} (pedido ${pedido}; ${porQue(certa)}) — nada enviado`
    );
  }

  const base_res: Resultado = { ok: true, comprador: usuario.trim(), loja, perfil: perfilNome };
  const conversa = await textoDaConversa(page);
  if (jaEnviada(conversa, pedido)) {
    return { ...base_res, ja_enviada: true, detalhe: "o pedido de senha já estava no chat" };
  }
  if (jaFalamDeSenha(conversa)) {
    throw new Falha(
      "ja_conversando",
      "o chat com esse comprador já fala de senha — não mandei; alguém precisa olhar a conversa"
    );
  }

  const modelo = String(p.texto_loja || "");
  const texto = loja && modelo.includes("{LOJA}") ? modelo.replace("{LOJA}", loja) : String(p.texto || "");
  if (!texto.trim()) throw new Falha("sem_texto", "tarefa sem texto");
  if (texto.length > 2000) throw new Falha("texto_grande", "texto acima de 2000 caracteres");

  if (!(p.commit === true && cfg.tiktokCalibrated)) {
    return {
      ok: false,
      seco: true,
      motivo: "seco",
      detalhe: `modo seco: conversa com ${usuario} conferida${loja ? ` (loja ${loja})` : ""}, nada enviado`,
      texto,
      loja,
      comprador: usuario.trim(),
      perfil: perfilNome,
    };
  }

  // 3) texto: pelo value da caixa (sem teclado), conferido, e conversa de novo
  const escrito = await page.evaluate((t) => {
    const ta = Array.from(document.querySelectorAll("textarea")).find((e) => e.getBoundingClientRect().width > 0);
    if (!ta) return null;
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    set.call(ta, t);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    return ta.value;
  }, texto);
  if (escrito !== texto) throw new Falha("tela_mudou", "a caixa de texto do chat não aceitou o texto — nada enviado");
  await sleep(800);
  const antes = await conversaCerta(page, usuario.trim(), pedido);
  if (!antes.ok) {
    throw new Falha("conversa_errada", `a conversa mudou antes de enviar (${porQue(antes)}) — nada enviado`);
  }
  const clicou = await page.evaluate(() => {
    const bt = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(
      (e) => e.innerText.trim() === "Enviar" && !e.disabled
    );
    bt?.click();
    return Boolean(bt);
  });
  if (!clicou) throw new Falha("tela_mudou", "não achei o botão Enviar do chat");
  const saiu = await esperar(
    () =>
      page.evaluate(
        (o) =>
          (document.querySelector("textarea")?.value || "") === "" &&
          document.body.innerText.includes(`do pedido ${o}`),
        pedido
      ),
    Boolean,
    15_000
  );
  if (!saiu) throw new Falha("nao_confirmou", "cliquei em Enviar, mas o texto não apareceu no chat");
  const res: Resultado = { ...base_res, texto, com_foto: false };

  // 4) foto (nunca derruba o que já saiu)
  if (p.foto_anexo_id) {
    try {
      const foto = await davinci.baixarFotoLogistica(cmdId);
      if (foto) {
        const ext = /png/i.test(foto.tipo) ? "png" : "jpg";
        fotoTmp.caminho = path.join(os.tmpdir(), `davinci-tiktok-${cmdId}.${ext}`);
        fs.writeFileSync(fotoTmp.caminho, foto.dados);
        const input = await page.$('input[type=file][accept*="image"]');
        if (!input) throw new Error("sem campo de foto no chat");
        await (input as any).uploadFile(fotoTmp.caminho);
        const janela = await esperar(
          () => page.evaluate(() => document.body.innerText.includes("Enviar fotos")),
          Boolean,
          15_000
        );
        if (!janela) throw new Error("a janela Enviar fotos não abriu");
        const confirmou = await page.evaluate(() => {
          const modal = Array.from(document.querySelectorAll<HTMLElement>("[class*=modal],[class*=Modal],[role=dialog]")).find(
            (m) => (m.innerText || "").includes("Enviar fotos")
          );
          const bt = modal
            ? Array.from(modal.querySelectorAll<HTMLButtonElement>("button")).find((b) => b.innerText.trim() === "Enviar")
            : undefined;
          bt?.click();
          return Boolean(bt);
        });
        if (!confirmou) throw new Error("não achei o Enviar da janela de fotos");
        const fechou = await esperar(
          () => page.evaluate(() => !document.body.innerText.includes("Enviar fotos")),
          Boolean,
          20_000
        );
        res.com_foto = fechou;
        if (!fechou) res.detalhe = "texto enviado; a foto não confirmou";
      }
    } catch (e: any) {
      res.detalhe = `texto enviado; foto não foi: ${String(e?.message || e).slice(0, 150)}`;
      log.warn(`TikTok senha ${pedido}: ${res.detalhe}`);
    }
  }
  return res;
}

/** Tarefa inteira: acha o perfil da loja, abre (só se estiver FECHADO — aberto
 *  = alguém usando), conversa e SEMPRE fecha o perfil e apaga a foto. */
export async function pedirSenha(cmdId: string, p: PedidoSenha): Promise<Resultado> {
  const conta = String(p.conta || "").trim();
  if (!String(p.pedido_tiktok || "").trim()) {
    return { ok: false, motivo: "sem_pedido", detalhe: "tarefa sem o nº do pedido da TikTok" };
  }
  if (!(await adspower.garantirAberto())) {
    return { ok: false, motivo: "adspower", detalhe: "AdsPower fechado no Mac Santiago — tento de novo depois" };
  }
  // Erro do AdsPower aqui (limite de ~1 req/s, API fora) não diz nada do
  // pedido: volta como "adspower" (não gasta tentativa). E sem saber se o
  // perfil está aberto, NÃO abre — pode ter alguém usando.
  let perfil: { userId: string; nome: string } | null;
  let emUso: boolean;
  try {
    perfil = await acharPerfil(conta);
    if (!perfil) {
      return { ok: false, motivo: "sem_perfil", detalhe: `a loja "${conta}" não tem perfil TikTok no AdsPower do Mac Santiago` };
    }
    emUso = await adspower.active(perfil.userId);
  } catch (e: any) {
    return { ok: false, motivo: "adspower", detalhe: `AdsPower: ${String(e?.message || e).slice(0, 150)} — tento de novo depois` };
  }
  if (emUso) {
    return {
      ok: false,
      motivo: "perfil_em_uso",
      detalhe: `perfil "${perfil.nome}" aberto por outra pessoa — tento de novo depois`,
    };
  }
  let browser: Browser | null = null;
  const fotoTmp: { caminho: string | null } = { caminho: null };
  try {
    const ws = await adspower.start(perfil.userId);
    browser = await puppeteer.connect({ browserWSEndpoint: ws, defaultViewport: null });
    const page = await browser.newPage();
    return await conversar(page, cmdId, p, perfil.nome, fotoTmp);
  } catch (e: any) {
    if (e instanceof Falha) return { ok: false, motivo: e.motivo, detalhe: e.message, perfil: perfil.nome };
    return { ok: false, motivo: "erro", detalhe: String(e?.message || e).slice(0, 300), perfil: perfil.nome };
  } finally {
    if (fotoTmp.caminho) fs.rmSync(fotoTmp.caminho, { force: true });
    if (browser) {
      try {
        browser.disconnect();
      } catch {
        /* ignora */
      }
    }
    try {
      await adspower.stop(perfil.userId);
    } catch (e) {
      log.error(`falha ao fechar profile ${perfil.userId}: ${String(e)}`);
    }
  }
}
