#!/usr/bin/env python3
"""A IA de Chamado na TELA da loja: acha o perfil do AdsPower do chamado, abre,
roda o Hermes com o navegador daquele perfil, fecha no fim e registra no chamado
o que foi feito.

Vinicius, 24/09/2026 ("ele tem que identificar qual perfil é e abrir no
AdsPower"). Por padrão é SÓ LEITURA; `--pode-agir` libera escrever/enviar (só com
instrução de pessoa).

25/09: o Hermes corta qualquer comando em 7 min e a tela da Shopee leva mais que
isso — `--fundo` dispara a tarefa desacoplada e volta na hora; no fim a própria
tarefa grava a análise no chamado (ACAO/RESUMO que a IA da tela devolve). Trava
por chamado: nunca duas tarefas de tela no mesmo chamado.

  tela.py --chamado-id UUID --tarefa "…" [--pode-agir] [--fundo]
  tela.py --pedido 296012 --tarefa "…"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PY = sys.executable
HERMES = os.path.expanduser("~/.local/bin/hermes")
BASE = Path("~/DaVinci/cerebro").expanduser()
LOG = BASE / "tela.jsonl"
TRAVAS = BASE / "estado"
LOGS_FUNDO = BASE / "tela_logs"

LEITURA = (
    "MODO SÓ LEITURA: NÃO envie mensagem, NÃO clique em botões que enviam, confirmam, "
    "aceitam, recusam ou pagam nada, NÃO preencha formulário. Só navegue e leia."
)
AGIR = (
    "Você PODE agir nesta tela (escrever e enviar) só no que a tarefa pede, neste chamado. "
    "Antes de enviar, confira que está na loja, no pedido e na conversa certos. "
    # 25/09 (296012): o Kaue reabriu a disputa no chat e a tela parou ("anexar não
    # fazia parte desta tarefa") — o Vinicius quer que ela emende sozinha.
    "Se a própria conversa abrir o próximo passo DESTE chamado (ex.: o atendente reabriu "
    "a disputa e pede evidência — a tela mostra '2ª', 'Upload Evidence' ou 'Enviar "
    "evidência'), NÃO pare: siga o manual e faça esse passo na mesma tarefa."
)
SEMPRE = (
    "Vá DIRETO ao que a tarefa pede. NÃO abra páginas só para reler histórico, pedido ou "
    "devolução: os fatos do chamado já estão na tarefa (Vinicius, 25/09). "
    "Se aparecer quebra-cabeça/captcha, tela de login ou verificação por código, PARE e "
    "diga isso — não tente resolver. Não abra outros pedidos nem outras lojas. No fim, "
    "responda em português: (1) o que viu, (2) o que fez, (3) em que página parou (URL). "
    "E termine com DUAS linhas exatamente assim:\n"
    "ACAO: esperar   (se deixou tudo feito e a bola ficou com a plataforma)\n"
    "ou ACAO: humano (se travou, faltou algo ou precisa de gente)\n"
    "RESUMO: <até 450 caracteres: o que viu, o que fez e o que falta>"
)


def _json(cmd: list[str]) -> dict | list:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        sys.exit((r.stderr or r.stdout).strip() or f"falhou: {' '.join(cmd)}")
    return json.loads(r.stdout)


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _registrar(caso: dict, perfil: dict, saida: str) -> str:
    """Grava no chamado o que a tela fez (vira análise da IA de Chamado)."""
    acao = "humano"
    m = re.search(r"ACAO:\s*(esperar|humano)", saida, re.I)
    if m:
        acao = m.group(1).lower()
    m = re.search(r"RESUMO:\s*(.+)", saida, re.S)
    resumo = (m.group(1).strip() if m else saida.strip()[-450:]).replace("\n", " ")
    resumo = f"Na tela ({perfil['nome']}): {resumo}"[:590]
    subprocess.run([PY, str(AQUI / "davinci_chamados.py"), "caso", "--id", caso["chamado_id"]],
                   capture_output=True, text=True, timeout=120)
    plat = re.sub(r"[^a-z]", "", (caso.get("plataforma") or "loja").lower())[:20] or "loja"
    decisao = {"chamado_id": caso["chamado_id"], "classe": f"{plat}_tarefa_na_tela",
               "resumo": resumo, "acao": acao}
    r = subprocess.run([PY, str(AQUI / "davinci_chamados.py"), "decidir"],
                       input=json.dumps(decisao, ensure_ascii=False),
                       capture_output=True, text=True, timeout=120)
    return (r.stdout or r.stderr).strip()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--pedido")
    p.add_argument("--chamado-id")
    p.add_argument("--tarefa", required=True)
    p.add_argument("--pode-agir", action="store_true")
    p.add_argument("--fundo", action="store_true",
                   help="dispara desacoplado e volta na hora; o resultado vai pro chamado")
    p.add_argument("--nao-registrar", action="store_true",
                   help="não grava a análise no chamado (teste manual)")
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
        log = LOGS_FUNDO / f"{time.strftime('%Y%m%d-%H%M%S')}-{caso.get('pedido_bling')}.log"
        args = [PY, str(Path(__file__).resolve()), "--chamado-id", caso["chamado_id"],
                "--tarefa", a.tarefa] + (["--pode-agir"] if a.pode_agir else [])
        with open(log, "w") as f:
            proc = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        trava.write_text(str(proc.pid))
        print(json.dumps({"ok": True, "em_andamento": True, "pedido": caso.get("pedido_bling"),
                          "aviso": "a tarefa roda sozinha (leva alguns minutos) e grava o "
                                   "resultado no chamado quando terminar"}, ensure_ascii=False))
        return

    trava.write_text(str(os.getpid()))
    try:
        achado = _json(
            [PY, str(AQUI / "adspower.py"), "achar", "--conta", caso.get("conta") or "",
             "--plataforma", caso.get("plataforma") or ""]
        )
        if not achado.get("ok"):
            msg = (f"não achei o perfil do AdsPower da loja '{caso.get('conta')}' "
                   f"({caso.get('plataforma')}): {achado.get('motivo')} — precisa de uma pessoa")
            if not a.nao_registrar:
                _registrar(caso, {"nome": "sem perfil"}, f"ACAO: humano\nRESUMO: {msg}")
            sys.exit(msg)
        perfil = achado["perfil"]
        # 25/09 (Vinicius): o que ele ensina no manual vale também na tela — ex.: no
        # Assistente do Vendedor da Shopee, responder o robô curto e só mandar tudo
        # quando o chat abrir o caminho.
        r = subprocess.run([PY, str(AQUI / "davinci_chamados.py"), "manual"],
                           capture_output=True, text=True, timeout=120)
        manual = r.stdout.strip() if r.returncode == 0 else "(não consegui ler o manual)"
        aberto = _json([PY, str(AQUI / "adspower.py"), "abrir", perfil["id"]])

        prompt = "\n".join(
            [
                f"Você é a IA de Chamado do DaVinci, agora NA TELA da loja '{perfil['nome']}' "
                f"(perfil AdsPower {perfil['id']}, já logado). Use as ferramentas de navegador.",
                f"Chamado: pedido Bling {caso.get('pedido_bling')}, pedido na plataforma "
                f"{caso.get('pedido_marketplace')}, plataforma {caso.get('plataforma')}, conta "
                f"{caso.get('conta')}, protocolo {caso.get('chamado')}, url {caso.get('chamado_url')}.",
                "MANUAL DO VINICIUS (vale acima do seu julgamento; siga o que se aplicar a esta tela):",
                manual,
                f"TAREFA: {a.tarefa}",
                AGIR if a.pode_agir else LEITURA,
                SEMPRE,
            ]
        )
        # o navegador do Hermes roda pelo Node dele (agent-browser via npx) — sem o PATH
        # do ~/.local/bin a sessão sobe só com o cofre de senhas, sem navegar
        caminho = os.pathsep.join(
            [os.path.expanduser("~/.local/bin"), os.path.expanduser("~/.hermes/node/bin"),
             os.environ.get("PATH", "/usr/bin:/bin")]
        )
        env = dict(os.environ, BROWSER_CDP_URL=aberto["cdp"], PATH=caminho)
        try:
            r = subprocess.run(
                [HERMES, "-z", prompt, "-t", "browser", "-m", "claude-sonnet-5",
                 "--provider", "anthropic", "--reasoning", "xhigh"],
                capture_output=True, text=True, env=env, timeout=1800,
            )
            saida = (r.stdout or "").strip() or (r.stderr or "").strip()
        except subprocess.TimeoutExpired:
            saida = "ACAO: humano\nRESUMO: a tarefa na tela passou de 30 minutos e foi interrompida."
        finally:
            subprocess.run([PY, str(AQUI / "adspower.py"), "fechar", perfil["id"]],
                           capture_output=True, timeout=120)
        registro = "" if a.nao_registrar else _registrar(caso, perfil, saida)
        with LOG.open("a") as f:
            f.write(json.dumps({"quando": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "pedido": caso.get("pedido_bling"), "perfil": perfil,
                                "tarefa": a.tarefa, "pode_agir": a.pode_agir,
                                "saida": saida, "registro": registro}, ensure_ascii=False) + "\n")
        print(f"[perfil {perfil['nome']} ({perfil['id']})]\n{saida}")
    finally:
        try:
            trava.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
