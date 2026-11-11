#!/usr/bin/env python3
# ruff: noqa — roda no Mac mini da Makisa (python 3.12 do sistema, só biblioteca padrão), não na API
"""Vigia do Jurídico no Mac mini (LaunchAgent com.davinci.juridico-vigia, a cada 60 s).

Pergunta ao DaVinci se tem pedido de leitura (06:00 ou botão Atualizar do painel Jurídico). Se
tem, guarda o pedido e abre juridico_ler.command numa janela do Terminal — o Terminal é quem
enxerga a Mesa (janelas50 do perfil 50); o LaunchAgent não. Já tem uma leitura rodando: não pega
outro pedido. Só biblioteca padrão.
"""

import json
import os
import subprocess
import urllib.request
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.expanduser("~/.davinci_denuncia.json")
PEDIDO = os.path.join(AQUI, "pedido.json")
TRAVA = os.path.join(AQUI, "rodando.pid")
COMANDO = os.path.join(AQUI, "juridico_ler.command")


def log(*partes):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *partes, flush=True)


def rodando():
    try:
        with open(TRAVA) as f:
            pid = int(f.read().strip() or 0)
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def main():
    if rodando():
        return
    with open(CONFIG) as f:
        c = json.load(f)
    req = urllib.request.Request(
        c["url"].rstrip("/") + "/api/juridico/sync/pedido",
        headers={"Authorization": "Bearer " + c["token"], "User-Agent": "davinci-juridico-mini/1"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        ped = (json.loads(r.read().decode() or "{}") or {}).get("pedido")
    if not ped:
        return
    with open(PEDIDO, "w") as f:
        json.dump(ped, f)
    log("pedido %s (%s): abrindo a leitura no Terminal" % (ped.get("id"), ped.get("origem")))
    subprocess.run(["/usr/bin/open", "-a", "Terminal", COMANDO], check=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("ERRO", e)
