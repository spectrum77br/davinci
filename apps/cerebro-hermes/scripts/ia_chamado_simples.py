#!/usr/bin/env python3
"""Pré-rodada da passada "simples" da IA de Chamado (o agendador do Hermes não passa
argumento ao script). forte = instrução de pessoa ou envio travado → Opus 5.5
no esforço extra; simples = a plataforma respondeu → Sonnet 5. Vinicius, 24/09."""

import runpy
import sys
from pathlib import Path

sys.argv = [sys.argv[0], "precheck", "--tipo", "simples"]
runpy.run_path(str(Path(__file__).resolve().parent / "davinci_chamados.py"), run_name="__main__")
