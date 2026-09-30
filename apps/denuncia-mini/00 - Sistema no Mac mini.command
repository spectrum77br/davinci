#!/bin/bash
# Deixe esta janela aberta: é o Sistema de Fiscalização rodando neste Mac mini
# (http://127.0.0.1:8710), a cópia pro DaVinci a cada 5 min, as provas pro MEGA
# e o status do robô. Abre sozinho quando o Mac liga (~/.fiscalizacao-inicio.sh).
# Fechou sem querer? Dê dois cliques de novo.
cd "$HOME/Desktop/Denuncias/Ecomerce/DaVinci" || exit 1
exec "$HOME/Desktop/Denuncias/Ecomerce/fiscalizacao-sistema/.venv/bin/python" servidor_mini.py
