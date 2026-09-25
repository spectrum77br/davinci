#!/usr/bin/env python3
"""Aplica no AdsPower o IP novo de cada empresa cadastrado no DaVinci.

Eduardo (25/09/2026): "quando eu colocar o ip novo já funcione" — e "não quero
que atualize nada [do que já existe], para não dar pau".

A API do AdsPower só responde nesta máquina, então o servidor não fala com
ela: este serviço busca no DaVinci as empresas com IP novo, troca o proxy dos
perfis delas e devolve o resultado, que aparece na tela de Empresas.

Uma passada por execução; o launchd chama de novo a cada 60 segundos.
Só usa a biblioteca padrão do Python que vem no macOS (3.9).

REGRAS DE SEGURANÇA
- Nunca escreve usuário nem senha de proxy em log, tela ou resposta. Tudo que
  sai daqui passa por `_limpo`, que apaga qualquer credencial vista na passada.
- Nunca TIRA o proxy de um perfil: sem proxy, o marketplace veria o IP deste
  Mac. Apagar o IP no DaVinci só para de sincronizar.
- Antes de gravar, testa o proxy novo e confere que ele sai pelo IP certo. Se
  não sair, não toca em nenhum perfil.
- Só mexe em perfil que o DaVinci entregou como da empresa e de mais ninguém
  (perfil dividido com outra empresa vem à parte e nunca é alterado).

USO
  adspower_ip_sync.py                         uma passada de verdade
  adspower_ip_sync.py --simular               faz tudo menos gravar e reportar
  adspower_ip_sync.py --perfil ID --ip IP     ensaio num perfil só (NUNCA grava)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

DAVINCI = os.environ.get("DAVINCI_URL", "https://app.hadken.com").rstrip("/")
ADSPOWER = os.environ.get("ADSPOWER_URL", "http://127.0.0.1:50325").rstrip("/")
TOKEN_FILE = Path(
    os.environ.get(
        "ADSPOWER_AGENT_TOKEN_FILE", str(Path.home() / ".davinci" / "adspower_agent_token")
    )
)
# A API local do AdsPower recusa rajadas: uma chamada por vez, com folga.
PAUSA_ADSPOWER = 1.1
# Serviço que só devolve o IP de quem pergunta. Usado para conferir por qual IP
# o proxy novo sai de verdade.
ECO_DE_IP = "https://api.ipify.org"

log = logging.getLogger("adspower_ip")

# Credenciais vistas nesta passada — `_limpo` apaga todas de qualquer texto
# antes de ele ir para log ou para o DaVinci.
_SEGREDOS: set[str] = set()


class Falha(Exception):
    """Erro já explicado em português para aparecer na tela de Empresas."""


def _limpo(texto: object) -> str:
    s = str(texto)
    for segredo in _SEGREDOS:
        if segredo and len(segredo) >= 3:
            s = s.replace(segredo, "***")
    return s[:480]


def _guardar_segredos(cfg: dict) -> None:
    for campo in ("proxy_user", "proxy_password"):
        v = cfg.get(campo)
        if v:
            _SEGREDOS.add(str(v))


# --- DaVinci -----------------------------------------------------------------


def _token() -> str:
    try:
        t = TOKEN_FILE.read_text().strip()
    except FileNotFoundError:
        raise Falha(f"arquivo de token não encontrado em {TOKEN_FILE}") from None
    if not t:
        raise Falha("arquivo de token vazio")
    return t


def davinci(metodo: str, caminho: str, corpo: dict | None = None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(
        DAVINCI + caminho,
        method=metodo,
        data=dados,
        headers={"X-Agent-Token": _token(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


# --- AdsPower ----------------------------------------------------------------


def adspower(caminho: str, corpo: dict | None = None):
    time.sleep(PAUSA_ADSPOWER)
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(
        ADSPOWER + caminho,
        method="POST" if corpo is not None else "GET",
        data=dados,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
    except urllib.error.URLError:
        raise Falha("o AdsPower deste Mac não respondeu (está aberto?)") from None
    if d.get("code") != 0:
        raise Falha("o AdsPower recusou: " + _limpo(d.get("msg"))[:160])
    return d.get("data")


def ler_perfil(user_id: str) -> dict | None:
    d = adspower("/api/v1/user/list?" + urllib.parse.urlencode({"user_id": user_id, "page_size": 1}))
    for p in (d or {}).get("list") or []:
        if p.get("user_id") == user_id:
            _guardar_segredos(p.get("user_proxy_config") or {})
            return p
    return None


def todos_os_perfis() -> list[dict]:
    perfis, pagina = [], 1
    while True:
        d = adspower(f"/api/v1/user/list?page={pagina}&page_size=100")
        lote = (d or {}).get("list") or []
        for p in lote:
            _guardar_segredos(p.get("user_proxy_config") or {})
        perfis += lote
        if len(lote) < 100:
            return perfis
        pagina += 1


def tem_proxy(cfg: dict) -> bool:
    return bool(cfg) and cfg.get("proxy_soft") not in (None, "", "no_proxy") and bool(
        cfg.get("proxy_host")
    )


def plano_padrao(perfis: list[dict]) -> dict | None:
    """O proxy mais usado nos perfis (tipo, porta, usuário e senha).

    Só entra em perfil que ainda não tem proxy nenhum. Perfil que já tem proxy
    mantém o dele e troca apenas o endereço."""
    contagem: Counter = Counter()
    exemplo: dict = {}
    for p in perfis:
        c = p.get("user_proxy_config") or {}
        if not tem_proxy(c):
            continue
        chave = (
            c.get("proxy_soft"),
            c.get("proxy_type"),
            str(c.get("proxy_port")),
            c.get("proxy_user") or "",
            c.get("proxy_password") or "",
        )
        contagem[chave] += 1
        exemplo[chave] = c
    if not contagem:
        return None
    return exemplo[contagem.most_common(1)[0][0]]


def proxy_novo(atual: dict, ip: str, padrao: dict | None) -> dict:
    base = atual if tem_proxy(atual) else padrao
    if not base:
        raise Falha("perfil sem proxy e nenhum outro perfil de onde copiar o plano")
    return {
        "proxy_soft": base.get("proxy_soft") or "other",
        "proxy_type": base.get("proxy_type") or "socks5",
        "proxy_host": ip,
        "proxy_port": str(base.get("proxy_port") or ""),
        "proxy_user": base.get("proxy_user") or "",
        "proxy_password": base.get("proxy_password") or "",
    }


def _aspas(v: str) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def testar_proxy(cfg: dict) -> str:
    """Conecta pelo proxy e devolve o IP pelo qual ele sai.

    As credenciais vão para o curl pela entrada padrão (`-K -`), nunca pela
    linha de comando — lá qualquer processo da máquina as veria com `ps`."""
    esquema = {"socks5": "socks5h", "http": "http", "https": "https"}.get(
        cfg["proxy_type"], cfg["proxy_type"]
    )
    endereco = f"{esquema}://{cfg['proxy_host']}:{cfg['proxy_port']}"
    linhas = [f"proxy = {_aspas(endereco)}"]
    if cfg.get("proxy_user"):
        linhas.append(f"proxy-user = {_aspas(cfg['proxy_user'] + ':' + cfg['proxy_password'])}")
    linhas += [f"url = {_aspas(ECO_DE_IP)}", "silent", "max-time = 20"]
    r = subprocess.run(
        ["/usr/bin/curl", "-K", "-"],
        input="\n".join(linhas),
        capture_output=True,
        text=True,
        timeout=40,
    )
    if r.returncode != 0:
        raise Falha(
            f"o proxy {cfg['proxy_host']}:{cfg['proxy_port']} não respondeu "
            f"(curl {r.returncode}); nada foi trocado"
        )
    return r.stdout.strip()


# --- uma empresa -------------------------------------------------------------


def aplicar_empresa(pend: dict, *, simular: bool) -> str:
    """Troca o proxy de todos os perfis da empresa para o IP novo.

    Devolve um resumo. Levanta `Falha` quando algo impede a empresa de ficar
    inteira no IP novo — com o motivo em português para a tela."""
    ip = pend["ip"]
    perfis = pend.get("perfis") or []
    avisos = []
    if pend.get("compartilhados"):
        nomes = ", ".join(f"n{p['profile_no']}" for p in pend["compartilhados"])
        avisos.append(f"perfil que também atende outra empresa não foi trocado ({nomes})")
    if pend.get("sem_perfil"):
        avisos.append("loja sem perfil no AdsPower: " + "; ".join(pend["sem_perfil"]))
    if not perfis:
        raise Falha(
            "nenhum perfil do AdsPower é só desta empresa"
            + (" — " + "; ".join(avisos) if avisos else "")
        )

    # 1) Lê tudo e monta o que seria gravado, sem gravar nada ainda.
    padrao = None
    planos = []
    for p in perfis:
        atual_perfil = ler_perfil(p["user_id"])
        if atual_perfil is None:
            raise Falha(f"perfil n{p['profile_no']} não está no AdsPower deste Mac")
        atual = atual_perfil.get("user_proxy_config") or {}
        if (atual.get("proxy_host") or "").strip() == ip:
            continue  # esse perfil já está no IP novo
        if not tem_proxy(atual) and padrao is None:
            padrao = plano_padrao(todos_os_perfis())
        planos.append((p, proxy_novo(atual, ip, padrao)))

    # 2) Testa cada proxy diferente ANTES de mexer em qualquer perfil.
    testados = set()
    for _p, cfg in planos:
        chave = (cfg["proxy_type"], cfg["proxy_port"], cfg["proxy_user"], cfg["proxy_password"])
        if chave in testados:
            continue
        saida = testar_proxy(cfg)
        if saida != ip:
            raise Falha(
                f"o proxy novo saiu pelo IP {saida or '(nenhum)'}, não pelo {ip}; "
                "nada foi trocado"
            )
        testados.add(chave)

    if simular:
        return (
            f"SIMULAÇÃO {pend['apelido']} {ip}: trocaria {len(planos)} perfil(s) "
            + ", ".join(f"n{p['profile_no']}" for p, _ in planos)
            + (f" | {len(perfis) - len(planos)} já estavam no IP" if len(perfis) > len(planos) else "")
        )

    # 3) Grava e relê cada perfil.
    trocados = []
    for p, cfg in planos:
        try:
            adspower("/api/v1/user/update", {"user_id": p["user_id"], "user_proxy_config": cfg})
            conferido = ((ler_perfil(p["user_id"]) or {}).get("user_proxy_config") or {}).get(
                "proxy_host"
            )
        except Falha as e:
            raise Falha(
                f"trocado em {', '.join(trocados) or 'nenhum'}; parou no n{p['profile_no']}: {e}"
            ) from None
        if conferido != ip:
            raise Falha(
                f"trocado em {', '.join(trocados) or 'nenhum'}; o n{p['profile_no']} "
                "não ficou com o IP novo"
            )
        trocados.append(f"n{p['profile_no']}")

    if avisos:
        raise Falha(f"IP aplicado em {len(perfis)} perfil(s), mas: " + "; ".join(avisos))
    return f"{pend['apelido']} {ip}: {len(trocados)} trocado(s), {len(perfis) - len(trocados)} já estavam"


# --- execução ----------------------------------------------------------------


def passada(simular: bool) -> int:
    pendentes = davinci("GET", "/api/agent/adspower/ip-pendentes") or []
    if not pendentes:
        log.info("nada pendente")
        return 0
    falhas = 0
    for pend in pendentes:
        try:
            resumo = aplicar_empresa(pend, simular=simular)
            ok, erro = True, None
            log.info(_limpo(resumo))
        except Falha as e:
            ok, erro = False, _limpo(e)
            falhas += 1
            log.warning("%s %s: %s", pend.get("apelido"), pend.get("ip"), erro)
        except Exception as e:  # noqa: BLE001 - uma empresa não derruba as outras
            ok, erro = False, f"erro inesperado ({type(e).__name__})"
            falhas += 1
            log.exception("%s: erro inesperado", pend.get("apelido"))
        if not simular:
            davinci(
                "POST",
                "/api/agent/adspower/ip-resultado",
                {"company_id": pend["company_id"], "ip": pend["ip"], "ok": ok, "erro": erro},
            )
    return 1 if falhas else 0


def ensaio_perfil(user_id: str, ip: str) -> int:
    """Monta e testa o proxy de UM perfil com o IP dado. Nunca grava."""
    perfil = ler_perfil(user_id)
    if perfil is None:
        print(f"perfil {user_id} não encontrado neste AdsPower")
        return 1
    atual = perfil.get("user_proxy_config") or {}
    print(f"perfil n{perfil.get('serial_number')} {perfil.get('name')}")
    print(f"  proxy atual: {atual.get('proxy_type')} {atual.get('proxy_host')}:{atual.get('proxy_port')}"
          if tem_proxy(atual) else "  proxy atual: nenhum")
    padrao = None if tem_proxy(atual) else plano_padrao(todos_os_perfis())
    cfg = proxy_novo(atual, ip, padrao)
    print(f"  gravaria:    {cfg['proxy_type']} {cfg['proxy_host']}:{cfg['proxy_port']} "
          f"(usuário e senha {'do próprio perfil' if tem_proxy(atual) else 'do plano padrão'})")
    try:
        saida = testar_proxy(cfg)
    except Falha as e:
        print("  teste do proxy: FALHOU —", _limpo(e))
        return 1
    print(f"  teste do proxy: saiu pelo IP {saida} -> {'CERTO' if saida == ip else 'ERRADO'}")
    print("  (ensaio: nada foi gravado)")
    return 0 if saida == ip else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simular", action="store_true", help="faz tudo menos gravar e reportar")
    ap.add_argument("--perfil", help="ensaio num perfil só (nunca grava)")
    ap.add_argument("--ip", help="IP do ensaio")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if a.perfil:
            if not a.ip:
                ap.error("--perfil precisa de --ip")
            return ensaio_perfil(a.perfil, a.ip)
        return passada(simular=a.simular)
    except Falha as e:
        log.error(_limpo(e))
        return 1
    except urllib.error.HTTPError as e:
        log.error("DaVinci respondeu %s em %s", e.code, e.url.split("?")[0])
        return 1


if __name__ == "__main__":
    sys.exit(main())
