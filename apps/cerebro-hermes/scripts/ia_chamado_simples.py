#!/usr/bin/env python3
"""Pré-rodada da passada "simples" da IA de Chamado (o agendador do Hermes não passa
argumento ao script). forte = instrução de pessoa ou envio travado; simples = a
plataforma respondeu. Vinicius, 24/09. Modelos desde 25/09 (assinatura do ChatGPT,
openai-codex): forte gpt-6-sol, simples gpt-6-luna — fixados no job do agendador."""

import runpy
import sys
from pathlib import Path

sys.argv = [sys.argv[0], "precheck", "--tipo", "simples"]
runpy.run_path(str(Path(__file__).resolve().parent / "davinci_chamados.py"), run_name="__main__")
