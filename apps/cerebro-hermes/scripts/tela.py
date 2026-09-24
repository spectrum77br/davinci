#!/usr/bin/env python3
"""A IA de Chamado na TELA da loja: acha o perfil do AdsPower do chamado, abre,
roda o Hermes com o navegador daquele perfil e fecha no fim.

Vinicius, 24/09/2026 ("ele tem que identificar qual perfil é e abrir no
AdsPower"). Piloto: por padrão é SÓ LEITURA — a IA entra, lê e conta o que viu;
não envia nem confirma nada. `--pode-agir` libera clicar/enviar (depois do
piloto).

  tela.py --pedido 296012 --tarefa "veja a situação da devolução e onde fica o chat"
  tela.py --chamado-id UUID --tarefa "…"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PY = sys.executable
HERMES = os.path.expanduser("~/.local/bin/hermes")
LOG = Path("~/DaVinci/cerebro/tela.jsonl").expanduser()

LEITURA = (
    "MODO SÓ LEITURA: NÃO envie mensagem, NÃO clique em botões que enviam, confirmam, "
    "aceitam, recusam ou pagam nada, NÃO preencha formulário. Só navegue e leia."
)
AGIR = (
    "Você PODE agir nesta tela (escrever e enviar) só no que a tarefa pede, neste chamado. "
    "Antes de enviar, confira que está na loja, no pedido e na conversa certos."
)
SEMPRE = (
    "Se aparecer quebra-cabeça/captcha, tela de login ou verificação por código, PARE e "
    "diga isso — não tente resolver. Não abra outros pedidos nem outras lojas. No fim, "
    "responda em português: (1) o que viu, (2) o que fez, (3) em que página parou (URL)."
)


def _json(cmd: list[str]) -> dict | list:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        sys.exit((r.stderr or r.stdout).strip() or f"falhou: {' '.join(cmd)}")
    return json.loads(r.stdout)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--pedido")
    p.add_argument("--chamado-id")
    p.add_argument("--tarefa", required=True)
    p.add_argument("--pode-agir", action="store_true")
    a = p.parse_args()
    if not (a.pedido or a.chamado_id):
        sys.exit("informe --pedido ou --chamado-id")

    filtro = ["--id", a.chamado_id] if a.chamado_id else ["--pedido", a.pedido]
    casos = _json([PY, str(AQUI / "davinci_chamados.py"), "caso", *filtro])
    if not casos:
        sys.exit("chamado não encontrado no DaVinci")
    caso = casos[0]
    achado = _json(
        [PY, str(AQUI / "adspower.py"), "achar", "--conta", caso.get("conta") or "",
         "--plataforma", caso.get("plataforma") or ""]
    )
    if not achado.get("ok"):
        sys.exit(
            f"não achei o perfil do AdsPower da loja '{caso.get('conta')}' "
            f"({caso.get('plataforma')}): {achado.get('motivo')} — precisa de uma pessoa"
        )
    perfil = achado["perfil"]
    aberto = _json([PY, str(AQUI / "adspower.py"), "abrir", perfil["id"]])

    prompt = "\n".join(
        [
            f"Você é a IA de Chamado do DaVinci, agora NA TELA da loja '{perfil['nome']}' "
            f"(perfil AdsPower {perfil['id']}, já logado). Use as ferramentas de navegador.",
            f"Chamado: pedido Bling {caso.get('pedido_bling')}, pedido na plataforma "
            f"{caso.get('pedido_marketplace')}, plataforma {caso.get('plataforma')}, conta "
            f"{caso.get('conta')}, protocolo {caso.get('chamado')}, url {caso.get('chamado_url')}.",
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
            [HERMES, "-z", prompt, "-t", "browser", "-m", "claude-opus-5-5",
             "--provider", "anthropic", "--reasoning", "xhigh"],
            capture_output=True, text=True, env=env, timeout=1800,
        )
        saida = (r.stdout or "").strip() or (r.stderr or "").strip()
    finally:
        subprocess.run([PY, str(AQUI / "adspower.py"), "fechar", perfil["id"]],
                       capture_output=True, timeout=120)
    with LOG.open("a") as f:
        f.write(json.dumps({"pedido": caso.get("pedido_bling"), "perfil": perfil,
                            "tarefa": a.tarefa, "pode_agir": a.pode_agir,
                            "saida": saida}, ensure_ascii=False) + "\n")
    print(f"[perfil {perfil['nome']} ({perfil['id']})]\n{saida}")


if __name__ == "__main__":
    main()
