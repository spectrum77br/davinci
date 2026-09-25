#!/usr/bin/env python3
"""A IA de Chamado responde o "Upload Evidence" da Shopee (2ª disputa).

Vinicius, 25/09/2026 (294571 ATV, 296012 Vortan — prazo 26/09): "quero que o
agente de IA escolha e suba". A janela "Enviar Prova" só aceita ARQUIVO (até 3
fotos de 10 MB ou vídeo de 1 min/30 MB), sem texto nem link — e o navegador do
Hermes não escolhe arquivo do disco. Então:

  1. baixa as fotos do chamado (e, se faltar, as da 1ª disputa na página da
     devolução) pra uma pasta temporária no Santiago;
  2. a IA OLHA as fotos (ferramenta de visão) e escolhe até 3, com o motivo;
  3. `shopee_evidencia.mjs` abre a janela da linha do pedido, anexa, confere
     "N / 3" e — com --de-verdade — clica Enviar e confere que a linha saiu do
     "Upload Evidence";
  4. grava no chamado (análise da IA + print do envio);
  5. APAGA a pasta das fotos, dê certo ou errado ("depois que baixar e fizer
     tudo excluir do mac do santiago", 25/09).

Trava por chamado junto com o tela.py (nunca duas tarefas de tela no mesmo).

  evidencia.py --chamado-id UUID [--de-verdade] [--fundo] [--nao-registrar]
  evidencia.py --pedido 294571 …
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PY = sys.executable
HERMES = os.path.expanduser("~/.local/bin/hermes")
NODE = os.path.expanduser("~/.local/bin/node")
BASE = Path("~/DaVinci/cerebro").expanduser()
LOG = BASE / "evidencia.jsonl"
TRAVAS = BASE / "estado"
LOGS_FUNDO = BASE / "tela_logs"
TEMP = BASE / "evidencias"
MAX_FOTO = 9_500_000  # a Shopee aceita até 10 MB por imagem


def _rodar(cmd: list[str], timeout: int = 300, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def _json(cmd: list[str], timeout: int = 300) -> dict | list:
    r = _rodar(cmd, timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-600:] or f"falhou: {cmd[1]}")
    linhas = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    try:
        return json.loads(r.stdout)
    except ValueError:
        return json.loads(linhas[-1])  # o .mjs imprime uma linha JSON no fim


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _mjs(cmd: str, ws: str, pedido: str, *extra: str, timeout: int = 240) -> dict:
    return _json([NODE, str(AQUI / "shopee_evidencia.mjs"), cmd, "--ws", ws, "--pedido", pedido,
                  *extra], timeout)


def _caber(arq: Path) -> None:
    """Foto acima de 10 MB: reduz no próprio lugar (sips, do macOS)."""
    if arq.stat().st_size > MAX_FOTO and arq.suffix.lower() in (".jpg", ".jpeg", ".png"):
        _rodar(["sips", "-Z", "2400", "-s", "formatQuality", "85", str(arq), "--out", str(arq)])


def _escolher(caso: dict, candidatos: list[dict], janela: str) -> tuple[list[str], str, str]:
    """A IA olha cada foto e escolhe até 3. Devolve (arquivos, motivo, saída)."""
    fatos = [
        f"- {m.get('created_at', '')[:16]} {m.get('autor_nome')}: "
        f"{(m.get('texto') or '').strip()[:600]}"
        for m in (caso.get("mensagens") or [])
        if m.get("tipo") in ("abertura", "resposta", "instrucao", "replica", "historico")
    ][-12:]
    lista = "\n".join(
        f"- {Path(c['arquivo']).name}: {c.get('origem')}"
        + (f", enviado em {c['quando'][:10]}" if c.get("quando") else "")
        for c in candidatos
    )
    prompt = "\n".join([
        "Você é a IA de Chamado do DaVinci. A Shopee reabriu a disputa deste pedido e pede "
        "evidência na janela 'Enviar Prova', que só aceita ARQUIVO: no máximo 3 fotos (sem "
        "texto, sem link). Sua tarefa é SÓ ESCOLHER as fotos; quem envia é outro programa.",
        f"Pedido Bling {caso.get('pedido_bling')}, pedido Shopee {caso.get('pedido_marketplace')}, "
        f"conta {caso.get('conta')}. Observação do chamado: {(caso.get('observacao') or '')[:500]}",
        "O que a janela da Shopee pede (texto da tela):",
        janela,
        "O caso até aqui (mais recente por último):",
        "\n".join(fatos) or "(sem mensagens)",
        "FOTOS DISPONÍVEIS (arquivos locais):",
        lista,
        f"Pasta: {Path(candidatos[0]['arquivo']).parent}",
        "Olhe CADA foto com a ferramenta de visão (vision_analyze com o caminho completo do "
        "arquivo). Escolha até 3 que, juntas, melhor provam o NOSSO lado: o que voltou na "
        "devolução (pacote/etiqueta da devolução, o que veio dentro no lugar do produto, a data "
        "à vista) ou, se o caso for de produto errado/não recebido, o que foi enviado. Prefira "
        "fotos diferentes entre si (não 3 iguais), nítidas, e que mostrem a etiqueta. Não "
        "escolha foto que ajude o comprador nem foto sem relação com o caso. Se nenhuma serve, "
        "diga nenhuma.",
        "Responda em português e termine com DUAS linhas exatamente assim:",
        "ESCOLHA: <nomes dos arquivos separados por vírgula, na ordem de importância> "
        "(ou ESCOLHA: nenhuma)",
        "MOTIVO: <até 300 caracteres: o que cada foto mostra e por que prova o nosso lado>",
    ])
    caminho = os.pathsep.join([os.path.expanduser("~/.local/bin"),
                               os.path.expanduser("~/.hermes/node/bin"),
                               os.environ.get("PATH", "/usr/bin:/bin")])
    r = _rodar([HERMES, "-z", prompt, "-t", "vision", "-m", "claude-sonnet-5",
                "--provider", "anthropic", "--reasoning", "xhigh"],
               timeout=900, env=dict(os.environ, PATH=caminho))
    saida = (r.stdout or r.stderr or "").strip()
    m = re.search(r"ESCOLHA:\s*(.+)", saida)
    motivo = (re.search(r"MOTIVO:\s*(.+)", saida, re.S) or [None, ""])[1].strip().replace("\n", " ")
    if not m or re.match(r"\s*nenhuma", m.group(1), re.I):
        return [], motivo[:300], saida
    nomes = {Path(c["arquivo"]).name: c["arquivo"] for c in candidatos}
    escolhidas = []
    for nome in re.split(r"[,;\s]+", m.group(1).strip()):
        nome = nome.strip(" .`'\"")
        if nome in nomes and nomes[nome] not in escolhidas:
            escolhidas.append(nomes[nome])
    return escolhidas[:3], motivo[:300], saida


def _registrar(caso: dict, acao: str, resumo: str, prints: list[Path]) -> str:
    _rodar([PY, str(AQUI / "davinci_chamados.py"), "caso", "--id", caso["chamado_id"]], 120)
    decisao = {"chamado_id": caso["chamado_id"], "classe": "shopee_evidencia_na_tela",
               "resumo": resumo[:590], "acao": acao}
    r = _rodar([PY, str(AQUI / "davinci_chamados.py"), "decidir"], 120,
               input=json.dumps(decisao, ensure_ascii=False))
    saida = (r.stdout or r.stderr).strip()
    try:
        analise_id = json.loads(saida)["resposta"]["analise_id"]
    except (ValueError, KeyError, TypeError):
        return saida
    for p in prints:
        if p.exists():
            _rodar([PY, str(AQUI / "davinci_chamados.py"), "guardar-print", "--id",
                    caso["chamado_id"], "--mensagem", analise_id, "--arquivo", str(p)], 120)
    return saida


def _tarefa(caso: dict, a: argparse.Namespace, pasta: Path, det: dict) -> tuple[str, str, list[Path]]:
    """Faz tudo; devolve (acao, resumo, prints) e anota em `det`. Não fecha o perfil
    (quem chama fecha, mesmo se der erro no meio)."""
    if (caso.get("plataforma") or "").lower() != "shopee":
        return "humano", "Upload Evidence automático só existe pra Shopee.", []
    achado = _json([PY, str(AQUI / "adspower.py"), "achar", "--conta", caso.get("conta") or "",
                    "--plataforma", "shopee"])
    if not achado.get("ok"):
        return "humano", f"não achei o perfil do AdsPower da loja {caso.get('conta')}: " \
                         f"{achado.get('motivo')}", []
    perfil = achado["perfil"]
    det["perfil"] = perfil
    det["aberto"] = True  # antes de abrir: se cair no meio, quem chama fecha
    aberto = _json([PY, str(AQUI / "adspower.py"), "abrir", perfil["id"]])
    ws, pedido = aberto["cdp"], caso["pedido_marketplace"]
    time.sleep(5)

    antes = _mjs("conferir", ws, pedido)
    det["antes"] = antes
    if not antes.get("ok"):
        return "humano", f"Na tela ({perfil['nome']}): não consegui ver a devolução — " \
                         f"{antes.get('motivo')}", []
    if not antes.get("pendente"):
        return "esperar", f"Na tela ({perfil['nome']}): a linha do pedido {pedido} não pede mais " \
                          "evidência (sem 'Upload Evidence') — nada a enviar.", []

    # fotos: as do chamado; se forem menos de 3, também as da 1ª disputa na página
    baixadas = _json([PY, str(AQUI / "davinci_chamados.py"), "anexos", "--id",
                      caso["chamado_id"], "--pasta", str(pasta / "chamado")])
    candidatos = [
        {**b, "origem": "foto da abertura do chamado (devolução)" if b.get("da_abertura")
         else f"arquivo do chamado ({b.get('nome_original')})"}
        for b in baixadas if str(b.get("tipo", "")).startswith("image/")
    ]
    if len(candidatos) < 3:
        extra = _mjs("fotos", ws, pedido, "--pasta", str(pasta / "shopee"), timeout=300)
        candidatos += [{**f, "quando": None} for f in extra.get("fotos") or []]
    for c in candidatos:
        _caber(Path(c["arquivo"]))
    det["candidatos"] = [Path(c["arquivo"]).name for c in candidatos]
    if not candidatos:
        return "humano", f"Na tela ({perfil['nome']}): a Shopee pede evidência até " \
                         f"{antes.get('prazo')}, mas o chamado não tem nenhuma foto pra mandar.", []

    janela = ("Você pode anexar fotos/vídeos gerados no momento da recepção da devolução OU "
              "fotos/vídeos enviadas pelo próprio comprador no momento da solicitação da "
              "devolução (essas evidências só serão aceitas se evidenciarem também a sua "
              "reclamação). * Envie imagem pacote vazio retornado no momento da abertura ou "
              "imagem dos itens retornados pelo cliente.")
    escolhidas, motivo, saida_ia = _escolher(caso, candidatos, janela)
    det["escolhidas"] = [Path(e).name for e in escolhidas]
    det["ia"] = saida_ia[-1500:]
    if not escolhidas:
        return "humano", f"Na tela ({perfil['nome']}): a Shopee pede evidência até " \
                         f"{antes.get('prazo')}; a IA olhou {len(candidatos)} foto(s) e não achou " \
                         f"nenhuma que prove o caso. {motivo}", []

    imp = pasta / "envio.png"
    extra = ["--arquivos", ",".join(escolhidas), "--print", str(imp)]
    if a.de_verdade:
        extra.append("--de-verdade")
    res = _mjs("enviar", ws, pedido, *extra, timeout=420)
    det["envio"] = res
    # a janela com as fotos anexadas + a linha do pedido depois do envio
    prints = [p for p in (pasta / "envio-antes.png", pasta / "envio-lista.png") if p.exists()]
    nomes = ", ".join(Path(e).name for e in escolhidas)
    if not a.de_verdade:
        return ("esperar" if res.get("ok") else "humano"), \
            f"TESTE (sem enviar): {len(escolhidas)} foto(s) anexadas e retiradas ({nomes}). {motivo}", prints
    if res.get("ok"):
        return "esperar", (
            f"Evidência enviada na tela ({perfil['nome']}, Upload Evidence da "
            f"{antes.get('disputa') or '2ª disputa'}, prazo {antes.get('prazo')}): "
            f"{len(escolhidas)} foto(s) — {motivo} A linha saiu do 'Upload Evidence'; agora a "
            "Shopee analisa."), prints
    return "humano", (
        f"Na tela ({perfil['nome']}): tentei enviar {len(escolhidas)} foto(s) no Upload Evidence "
        f"(prazo {antes.get('prazo')}) e não confirmou: {res.get('motivo')}. Conferir na tela."), prints


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--pedido")
    p.add_argument("--chamado-id")
    p.add_argument("--de-verdade", action="store_true", help="clica Enviar (sem isso, só testa)")
    p.add_argument("--fundo", action="store_true", help="roda desacoplado; o resultado vai pro chamado")
    p.add_argument("--nao-registrar", action="store_true")
    a = p.parse_args()
    if not (a.pedido or a.chamado_id):
        sys.exit("informe --pedido ou --chamado-id")
    filtro = ["--id", a.chamado_id] if a.chamado_id else ["--pedido", a.pedido]
    casos = _json([PY, str(AQUI / "davinci_chamados.py"), "caso", *filtro])
    if not casos:
        sys.exit("chamado não encontrado no DaVinci")
    caso = casos[0]

    TRAVAS.mkdir(parents=True, exist_ok=True)
    trava = TRAVAS / f"tela_{caso['chamado_id']}.lock"
    if trava.exists():
        try:
            pid = int(trava.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and _vivo(pid) and pid != os.getpid():
            print(json.dumps({"ok": False, "motivo": "já tem uma tarefa na tela rodando neste chamado"}))
            return

    if a.fundo:
        LOGS_FUNDO.mkdir(parents=True, exist_ok=True)
        log = LOGS_FUNDO / f"{time.strftime('%Y%m%d-%H%M%S')}-{caso.get('pedido_bling')}-evidencia.log"
        args = [PY, str(Path(__file__).resolve()), "--chamado-id", caso["chamado_id"]]
        args += [f for f, on in (("--de-verdade", a.de_verdade), ("--nao-registrar", a.nao_registrar)) if on]
        with open(log, "w") as f:
            proc = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        trava.write_text(str(proc.pid))
        print(json.dumps({"ok": True, "em_andamento": True, "pedido": caso.get("pedido_bling"),
                          "aviso": "a IA escolhe as fotos e envia sozinha (alguns minutos); o "
                                   "resultado vai pro chamado"}, ensure_ascii=False))
        return

    trava.write_text(str(os.getpid()))
    pasta = TEMP / f"{caso.get('pedido_bling')}-{time.strftime('%Y%m%d-%H%M%S')}"
    pasta.mkdir(parents=True, exist_ok=True)
    det: dict = {}
    prints: list[Path] = []
    try:
        try:
            acao, resumo, prints = _tarefa(caso, a, pasta, det)
        except Exception as e:  # noqa: BLE001 — vira "humano" no chamado, não traceback
            acao, resumo = "humano", f"Upload Evidence automático falhou: {str(e)[:400]}"
        finally:
            perfil = det.get("perfil")
            if perfil and det.get("aberto"):
                _rodar([PY, str(AQUI / "adspower.py"), "fechar", perfil["id"]], 120)
        registro = "" if a.nao_registrar else _registrar(caso, acao, resumo, prints)
        with LOG.open("a") as f:
            f.write(json.dumps({"quando": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "pedido": caso.get("pedido_bling"), "de_verdade": a.de_verdade,
                                "acao": acao, "resumo": resumo, "detalhes": det,
                                "registro": registro}, ensure_ascii=False, default=str) + "\n")
        print(json.dumps({"acao": acao, "resumo": resumo, "escolhidas": det.get("escolhidas"),
                          "candidatos": det.get("candidatos"), "envio": det.get("envio")},
                         ensure_ascii=False, indent=1, default=str))
    finally:
        shutil.rmtree(pasta, ignore_errors=True)  # fotos de cliente não ficam no Santiago
        try:
            trava.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
