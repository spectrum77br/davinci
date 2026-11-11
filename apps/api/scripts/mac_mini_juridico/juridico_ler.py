#!/usr/bin/env python3
# ruff: noqa — roda no Mac mini da Makisa (python 3.12 do sistema, só biblioteca padrão), não na API
"""Mac mini da Makisa → DaVinci: a leitura do Jurídico — Diário oficial (CNJ) + Jusbrasil.

11/11/2026, Vinicius: "6 da manhã … chegar e ver tudo pronto" e "no botão Atualizar, atualizar
Jusbrasil e CNJ junto". O DaVinci cria um pedido (06:00 ou botão Atualizar); o vigia
(juridico_vigia.py, a cada minuto) pega o pedido e abre este script numa janela do Terminal —
precisa ser pelo Terminal porque o perfil 50 é controlado pelo janelas50.py, que mora na Mesa
(o LaunchAgent não lê a Mesa).

1. Diário de Justiça Eletrônico Nacional (DJEN, do CNJ): as publicações de cada processo com nº.
   (O DJEN dá 403 ao servidor do DaVinci, que fica fora do Brasil — daqui funciona.)
2. Jusbrasil, no perfil 50 (logado pela MAKISA), numa janela própria (janelas50, canal
   "jusbrasil"): os "Andamento" do Histórico — o que o Diário não traz. Um processo por vez e
   devagar (06:00: 2–4 min entre eles; botão: 45–90 s). Primeira vez num processo: acha pelo nº,
   guarda o link e carrega o histórico inteiro ("Mostrar mais"). Os termos do Jusbrasil proíbem
   robô; o Vinicius assumiu o risco.
   Verificação de robô (captcha) ou login caído: PARA, deixa a janela aberta na tela (regra da
   Denúncia) e avisa no topo do painel Jurídico.

Config: ~/.davinci_denuncia.json (mesmo endereço e token da cópia da Denúncia).
Log: ~/DaVinci-juridico/juridico_ler.log
"""

import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get("JURIDICO_CONFIG") or os.path.expanduser("~/.davinci_denuncia.json")
FISCALIZACAO = os.path.expanduser("~/Desktop/Denuncias/Ecomerce/Fiscalizacao")
PEDIDO = os.path.join(AQUI, "pedido.json")
TRAVA = os.path.join(AQUI, "rodando.pid")
URL_DJEN = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
PAUSA_DJEN = 3.2
UA = "davinci-juridico-mini/1"
SINAIS_ROBO = (
    "captcha", "não sou um robô", "nao sou um robo", "verifique se você é humano",
    "confirme que você é humano", "verify you are human", "acesso bloqueado", "access denied",
    "too many requests", "atividade incomum", "atividade suspeita",
)
DATA = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def log(*partes):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *partes, flush=True)


# ── DaVinci ────────────────────────────────────────────────────────────────────────────────
def _config():
    with open(CONFIG) as f:
        c = json.load(f)
    return c["url"].rstrip("/"), c["token"]


def _http(metodo, url, corpo=None, cabecalhos=None, timeout=60):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(cabecalhos or {})
    if dados is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=dados, headers=h, method=metodo)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "null")


def davinci(metodo, rota, corpo=None):
    base, token = _config()
    return _http(metodo, base + rota, corpo, {"Authorization": "Bearer " + token})


def andamento(pid, texto):
    log("·", texto)
    if pid:
        try:
            davinci("POST", "/api/juridico/sync/pedido/%s" % pid, {"progresso": texto})
        except Exception as e:  # noqa: BLE001
            log("não consegui avisar o andamento:", e)


# ── 1. Diário (DJEN) ───────────────────────────────────────────────────────────────────────
def djen(numero):
    itens = []
    pagina = 1
    while True:
        q = urllib.parse.urlencode({"numeroProcesso": numero, "pagina": pagina, "itensPorPagina": 100})
        try:
            d = _http("GET", URL_DJEN + "?" + q)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(61)
            d = _http("GET", URL_DJEN + "?" + q)
        lote = d.get("items") or []
        itens.extend(lote)
        if not lote or len(itens) >= int(d.get("count") or 0) or len(lote) < 100:
            return itens
        pagina += 1
        time.sleep(PAUSA_DJEN)


def ler_diario(pid, procs):
    conta = {"origem": "mac_mini", "processos": 0, "publicacoes": 0, "novas": 0, "falhas": 0}
    erros = []
    for i, p in enumerate(procs):
        if i:
            time.sleep(PAUSA_DJEN)
        andamento(pid, "Diário %d/%d" % (i + 1, len(procs)))
        try:
            itens = djen(p["numero"])
            r = davinci("POST", "/api/juridico/sync/processos/%s/diario" % p["id"], {"itens": itens})
            conta["processos"] += 1
            conta["publicacoes"] += int(r.get("publicacoes") or 0)
            conta["novas"] += int(r.get("novas") or 0)
        except Exception as e:  # noqa: BLE001
            conta["falhas"] += 1
            erros.append("%s: %s" % (p["id"], str(e)[:120]))
            log("Diário, processo %s: FALHOU %s" % (p["id"], e))
    if erros and not conta["processos"]:
        conta["erro"] = "; ".join(erros)[:450]
    davinci("POST", "/api/juridico/sync/leitura", conta)
    return conta


# ── 2. Jusbrasil (perfil 50) ───────────────────────────────────────────────────────────────
JS_TEXTO = "return {url: location.href.split('?')[0], titulo: document.title, texto: (document.body && document.body.innerText) || ''};"
JS_LINKS = "return [...new Set([...document.querySelectorAll('a[href]')].map(a => a.href.split('?')[0]).filter(h => /\\/processos\\/\\d+\\//.test(h)))];"
JS_MOSTRAR_MAIS = r"""
const todos = [...document.querySelectorAll('h1,h2,h3,h4,h5,span,div,p')];
const h = todos.find(e => e.childElementCount === 0 && e.textContent.trim() === 'Histórico');
const fim = todos.find(e => e.childElementCount === 0 && e.textContent.trim() === 'Decisões do processo');
const bts = [...document.querySelectorAll('button,a,[role=button]')].filter(b => (b.innerText || '').trim() === 'Mostrar mais');
const alvo = bts.find(b => (!h || (h.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)) &&
                          (!fim || (fim.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_PRECEDING)));
if (!alvo) return false;
alvo.scrollIntoView({block: 'center'}); alvo.click(); return true;
"""


def mascara(n):
    return "%s-%s.%s.%s.%s.%s" % (n[0:7], n[7:9], n[9:13], n[13], n[14:16], n[16:20])


def bloqueio(pagina):
    t = (pagina.get("texto", "") + " " + pagina.get("titulo", "")).lower()
    if any(s in t for s in SINAIS_ROBO):
        return "o Jusbrasil pediu verificação de robô — resolver na tela do perfil 50 (janela deixada aberta)"
    if "minha conta" not in t and ("entrar" in t or "fazer login" in t or "/login" in pagina.get("url", "")):
        return "o perfil 50 saiu do Jusbrasil — fazer o login de novo no perfil 50"
    return ""


def andamentos_do_historico(texto):
    """Os "Andamento" do Histórico (as "Publicação" já vêm do Diário)."""
    linhas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    ini = linhas.index("Histórico") + 1 if "Histórico" in linhas else 0
    fim = len(linhas)
    for k in range(ini, len(linhas)):
        if linhas[k].startswith("Decisões do processo") or linhas[k] == "Outros documentos":
            fim = k
            break
    data, atual, itens = None, None, []
    for ln in linhas[ini:fim]:
        if DATA.match(ln):
            data, atual = ln, None
            continue
        if re.match(r"^(há|Há) ", ln) or ln.startswith("Todas as informações publicadas"):
            continue
        if ln in ("Andamento", "Publicação"):
            atual = {"data": data, "tipo": ln, "linhas": []}
            itens.append(atual)
            continue
        if ln == "Mostrar explicação":
            atual = None
            continue
        if ln in ("Mostrar mais", "Exibir mais"):
            continue
        if atual is not None:
            atual["linhas"].append(ln)
    out = []
    for it in itens:
        texto_it = " ".join(it["linhas"]).strip()
        if it["tipo"] == "Andamento" and it["data"] and texto_it:
            out.append({"data": it["data"], "tipo": "Andamento", "texto": texto_it[:2000]})
    return out


def ler_jusbrasil(pid, procs, origem):
    sys.path.insert(0, FISCALIZACAO)
    os.chdir(FISCALIZACAO)
    import janelas50  # noqa: E402 — o controle de janelas do perfil 50 da Denúncia

    pausa = (120, 240) if origem == "cron" else (45, 90)
    conta = {"origem": "jusbrasil", "processos": 0, "publicacoes": 0, "novas": 0, "falhas": 0}
    parou = ""
    jan = janelas50.abrir_janela("jusbrasil")
    try:
        for i, p in enumerate(procs):
            if i:
                time.sleep(random.uniform(*pausa))
            andamento(pid, "Jusbrasil %d/%d" % (i + 1, len(procs)))
            try:
                link = (p.get("jusbrasil_link") or "").split("?")[0]
                if not link:
                    jan.get("https://www.jusbrasil.com.br/busca?q=" + mascara(p["numero"]))
                    jan.esperar_pronta(40)
                    time.sleep(random.uniform(6, 10))
                    pag = jan.execute_script(JS_TEXTO)
                    parou = bloqueio(pag)
                    if parou:
                        break
                    links = jan.execute_script(JS_LINKS) or []
                    link = next((u for u in links if p["numero"] in re.sub(r"\D", "", u)), "")
                    if not link:
                        log("processo %s: não achei no Jusbrasil" % p["id"])
                        conta["falhas"] += 1
                        continue
                    time.sleep(random.uniform(8, 15))
                jan.get(link)
                jan.esperar_pronta(40)
                time.sleep(random.uniform(7, 12))
                pag = jan.execute_script(JS_TEXTO)
                parou = bloqueio(pag)
                if parou:
                    break
                if not p.get("tem_jusbrasil"):  # primeira vez: o histórico inteiro
                    for _ in range(15):
                        if not jan.execute_script(JS_MOSTRAR_MAIS):
                            break
                        time.sleep(random.uniform(3, 6))
                    pag = jan.execute_script(JS_TEXTO)
                andamentos = andamentos_do_historico(pag.get("texto", ""))
                r = davinci("POST", "/api/juridico/sync/processos/%s/jusbrasil" % p["id"],
                            {"link": pag.get("url") or link, "andamentos": andamentos})
                conta["processos"] += 1
                conta["publicacoes"] += len(andamentos)
                conta["novas"] += int(r.get("novas") or 0)
                log("Jusbrasil, processo %s: %d andamentos, %s novos" % (p["id"], len(andamentos), r.get("novas")))
            except Exception as e:  # noqa: BLE001
                conta["falhas"] += 1
                log("Jusbrasil, processo %s: FALHOU %s" % (p["id"], e))
    finally:
        if parou:
            log("PAROU:", parou)  # a janela fica aberta na tela
        else:
            try:
                jan.fechar()
            except Exception:  # noqa: BLE001
                pass
    if parou:
        conta["erro"] = parou
    davinci("POST", "/api/juridico/sync/leitura", conta)
    return conta, parou


def main():
    with open(TRAVA, "w") as f:
        f.write(str(os.getpid()))
    pid = None
    try:
        ped = {}
        if os.path.exists(PEDIDO):
            with open(PEDIDO) as f:
                ped = json.load(f) or {}
        pid = ped.get("id")
        log("início do pedido", pid, ped.get("origem"))
        procs = davinci("GET", "/api/juridico/sync/processos")["processos"]
        d = ler_diario(pid, procs)
        resultado = "Diário: %d nova(s)" % d["novas"]
        ok = True
        if ped.get("jusbrasil", True) and procs:
            j, parou = ler_jusbrasil(pid, procs, ped.get("origem") or "botao")
            resultado += " · Jusbrasil: %d novo(s)" % j["novas"]
            if parou:
                resultado, ok = parou, False
        if pid:
            davinci("POST", "/api/juridico/sync/pedido/%s" % pid,
                    {"progresso": "terminado", "terminado": True, "ok": ok, "resultado": resultado})
        log("fim:", resultado)
    except Exception as e:  # noqa: BLE001
        log("ERRO", e)
        if pid:
            try:
                davinci("POST", "/api/juridico/sync/pedido/%s" % pid,
                        {"terminado": True, "ok": False, "resultado": "erro no Mac mini: %s" % str(e)[:300]})
            except Exception:  # noqa: BLE001
                pass
        sys.exit(1)
    finally:
        try:
            os.remove(TRAVA)
        except OSError:
            pass


if __name__ == "__main__":
    main()
