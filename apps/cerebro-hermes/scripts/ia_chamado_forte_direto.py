#!/usr/bin/env python3
"""Passada "forte" da IA de Chamado pelo modo DIRETO do Hermes (`hermes -z`).

25/09: com a conta nova da Anthropic (criada depois de 31/08), o Opus 5.5 recusa
raciocínio "preso a outra conversa" — e o agendador do Hermes (agente de cron)
muda o começo da conversa entre a 1ª e a 2ª chamada, então TODA passada forte
caía com "Invalid signature in thinking block". O modo direto não tem isso
(testado: skill carregada + várias ferramentas, Opus 5.5 esforço extra, ok).

Rodado pelo agendador como job sem agente (`--no-agent`): faz a pré-rodada
(instrução de pessoa ou envio travado); sem caso → não imprime nada (passada
silenciosa, custo zero); com caso → chama a IA direto com o manual e os casos.
"""

import os
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PROMPT = (
    "Passada da IA de Chamado. Abaixo, a saída do script: o MANUAL do Vinicius, as "
    "correções/confirmações dele e os CASOS que esperam por você. Decida CADA caso seguindo "
    "a skill ia-de-chamado (instrução de pessoa > manual > bom senso prudente) e registre "
    "cada decisão com: python3 ~/.hermes/scripts/davinci_chamados.py decidir. Tarefa na tela "
    "sempre com --fundo. Na dúvida, humano. No fim, só a lista curta: pedido → ação → por quê."
)

pre = subprocess.run([sys.executable, str(AQUI / "davinci_chamados.py"), "precheck", "--tipo", "forte"],
                     capture_output=True, text=True, timeout=300)
saida = (pre.stdout or "").strip()
if pre.returncode != 0:
    sys.exit(f"pré-rodada falhou: {(pre.stderr or saida)[:500]}")
if not saida or '"wakeAgent": false' in saida:
    sys.exit(0)  # nada a fazer: passada silenciosa

env = dict(os.environ)
env["PATH"] = os.pathsep.join([os.path.expanduser("~/.local/bin"),
                               os.path.expanduser("~/.hermes/node/bin"), env.get("PATH", "/usr/bin:/bin")])
r = subprocess.run([os.path.expanduser("~/.local/bin/hermes"), "-z", PROMPT + "\n\n" + saida,
                    "-s", "ia-de-chamado", "-t", "terminal,file", "-m", "claude-opus-5-5",
                    "--provider", "anthropic", "--reasoning", "xhigh"],
                   capture_output=True, text=True, env=env, timeout=3000)
print((r.stdout or r.stderr or "").strip()[-3000:])
