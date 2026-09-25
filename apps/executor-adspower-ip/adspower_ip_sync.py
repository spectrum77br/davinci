#!/usr/bin/env python3
"""Aplica no AdsPower o IP novo de cada empresa cadastrado no DaVinci.

Eduardo (25/09/2026): "quando eu colocar o ip novo já funcione" — e "não quero
que atualize nada [do que já existe], para não dar pau".

A API do AdsPower só responde nesta máquina, então o servidor não fala com
ela: este serviço busca no DaVinci as empresas com IP novo, troca o proxy dos
perfis delas e devolve o resultado, que aparece na tela de Empresas.

Uma passada por execução; o launchd chama de novo a cada 60 segundos.
Só usa a biblioteca padrão do Python que vem no macOS (3.9).

A SENHA DO PROXY DEPENDE DO IP
Os proxies deste AdsPower usam um usuário só, mas a porta e a senha variam por
IP (em 25/09/2026 eram 3 combinações). Por isso o serviço não reaproveita a
conta antiga do perfil às cegas: ele descobre qual conta funciona com o IP
novo, nesta ordem, e grava a primeira que sair pelo IP certo:
  1. a de algum perfil que já usa esse IP (é exatamente a certa);
  2. a que os perfis da empresa já usam;
  3. cada conta conhecida neste AdsPower, da mais usada para a menos.
Se nenhuma funcionar, é proxy de compra nova com conta nova: basta configurar
esse IP à mão em um perfil qualquer do AdsPower uma vez, e daí em diante o
serviço passa a reconhecer a conta.

REGRAS DE SEGURANÇA
- Nunca escreve usuário nem senha de proxy em log, tela ou resposta. Tudo que
  sai daqui passa por `_limpo`, que apaga qualquer credencial vista.
- Nunca TIRA o proxy de um perfil: sem proxy, o marketplace veria o IP deste
  Mac. Apagar o IP no DaVinci só para de sincronizar.
- Nada é gravado sem antes testar o proxy e confirmar que ele sai pelo IP
  certo. Se não sair, não toca em nenhum perfil.
- Só mexe em perfil que o DaVinci entregou como da empresa e de mais ninguém.
- Não passa por proxy do sistema nem segue redirecionamento: o token do
  DaVinci e as senhas lidas do AdsPower não podem ir parar em outro lugar.

USO
  adspower_ip_sync.py                         uma passada de verdade
  adspower_ip_sync.py --simular               faz tudo menos gravar e reportar
  adspower_ip_sync.py --perfil ID --ip IP     ensaio num perfil só (NUNCA grava)
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import subprocess
import sys
import time
import traceback
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
# A API local do AdsPower recusa rajadas, e o executor antigo deste Mac usa a
# mesma API: uma chamada por vez, com folga, e novas tentativas se recusar.
PAUSA_ADSPOWER = 1.1
TENTATIVAS_ADSPOWER = 4
# Serviços que só devolvem o IP de quem pergunta. Dois, para a queda de um não
# parecer defeito do proxy.
ECOS_DE_IP = ("https://api.ipify.org", "https://icanhazip.com")

log = logging.getLogger("adspower_ip")

# Credenciais vistas nesta execução — `_limpo` apaga todas de qualquer texto.
_SEGREDOS: set[str] = set()


class Falha(Exception):
    """Erro já explicado em português para aparecer na tela de Empresas."""


class FalhaPassageira(Falha):
    """AdsPower ocupado ou fora do ar. Se nada foi gravado, a empresa não é
    reportada: tenta de novo no minuto seguinte, sem cair no castigo de 1 hora."""


def _limpo(texto: object) -> str:
    s = str(texto)
    # Do maior para o menor: se o usuário for pedaço da senha (ou o contrário),
    # apagar o menor primeiro deixaria o resto do maior aparecendo.
    for segredo in sorted(_SEGREDOS, key=len, reverse=True):
        if segredo:
            s = s.replace(segredo, "***")
    return s[:480]


def _guardar_segredos(cfg: dict) -> None:
    for campo in ("proxy_user", "proxy_password"):
        v = cfg.get(campo)
        if v:
            _SEGREDOS.add(str(v))


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirecionamento recusado", headers, fp)


# Sem proxy do sistema (ProxyHandler vazio) e sem seguir redirecionamento.
_ABRIR = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SemRedirecionar())


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
    with _ABRIR.open(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


# --- AdsPower ----------------------------------------------------------------


def adspower(caminho: str, corpo: dict | None = None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    espera = 2.0
    for tentativa in range(1, TENTATIVAS_ADSPOWER + 1):
        time.sleep(PAUSA_ADSPOWER)
        req = urllib.request.Request(
            ADSPOWER + caminho,
            method="POST" if corpo is not None else "GET",
            data=dados,
            headers={"Content-Type": "application/json"},
        )
        try:
            with _ABRIR.open(req, timeout=30) as r:
                d = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if tentativa == TENTATIVAS_ADSPOWER:
                raise FalhaPassageira("o AdsPower deste Mac não respondeu (está aberto?)") from None
        else:
            if d.get("code") == 0:
                return d.get("data")
            msg = _limpo(d.get("msg"))
            if "too many" not in msg.lower():
                raise Falha("o AdsPower recusou: " + msg[:160])
            if tentativa == TENTATIVAS_ADSPOWER:
                raise FalhaPassageira("o AdsPower está recusando por excesso de chamadas")
        time.sleep(espera)
        espera *= 2
    raise FalhaPassageira("o AdsPower não respondeu")  # pragma: no cover


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


def _conta(cfg: dict) -> tuple:
    """A conta do proxy, sem o endereço: o que precisa bater com o IP."""
    return (
        cfg.get("proxy_soft") or "other",
        cfg.get("proxy_type") or "socks5",
        str(cfg.get("proxy_port") or ""),
        cfg.get("proxy_user") or "",
        cfg.get("proxy_password") or "",
    )


def _com_endereco(conta: tuple, ip: str) -> dict:
    soft, tipo, porta, usuario, senha = conta
    return {
        "proxy_soft": soft,
        "proxy_type": tipo,
        "proxy_host": ip,
        "proxy_port": porta,
        "proxy_user": usuario,
        "proxy_password": senha,
    }


def contas_candidatas(ip: str, da_empresa: list[dict], todos: list[dict]) -> list[tuple]:
    """Contas a tentar com o IP novo, da mais provável para a menos."""
    ordem: list[tuple] = []

    def junta(conta: tuple) -> None:
        if conta not in ordem:
            ordem.append(conta)

    # 1) quem já usa esse IP tem exatamente a conta certa
    for p in todos:
        c = p.get("user_proxy_config") or {}
        if tem_proxy(c) and (c.get("proxy_host") or "").strip() == ip:
            junta(_conta(c))
    # 2) a que a empresa já usa
    for c in da_empresa:
        if tem_proxy(c):
            junta(_conta(c))
    # 3) todas as contas conhecidas, da mais usada para a menos
    uso: Counter = Counter(
        _conta(p.get("user_proxy_config") or {})
        for p in todos
        if tem_proxy(p.get("user_proxy_config") or {})
    )
    for conta, _n in uso.most_common():
        junta(conta)
    return ordem


def _aspas(v: str) -> str:
    """Texto entre aspas no formato de configuração do curl."""
    s = str(v)
    for de, para in (("\\", "\\\\"), ('"', '\\"'), ("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        s = s.replace(de, para)
    return '"' + s + '"'


_MOTIVO_CURL = {
    5: "não achou o endereço do proxy",
    7: "não conseguiu conectar no proxy",
    28: "o proxy não respondeu a tempo",
    97: "o proxy recusou o usuário/senha",
}


def testar_proxy(cfg: dict) -> str:
    """Conecta pelo proxy e devolve o IP pelo qual ele sai.

    As credenciais vão para o curl pela entrada padrão (`-K -`), nunca pela
    linha de comando — lá qualquer processo da máquina as veria com `ps`. O
    `-q` vem primeiro para o curl ignorar um ~/.curlrc (um `trace` lá dentro
    gravaria a senha num arquivo)."""
    esquema = {"socks5": "socks5h", "http": "http", "https": "https"}.get(
        cfg["proxy_type"], cfg["proxy_type"]
    )
    endereco = f"{esquema}://{cfg['proxy_host']}:{cfg['proxy_port']}"
    ultimo = 0
    for eco in ECOS_DE_IP:
        linhas = [f"proxy = {_aspas(endereco)}"]
        if cfg.get("proxy_user"):
            linhas.append(f"proxy-user = {_aspas(cfg['proxy_user'] + ':' + cfg['proxy_password'])}")
        linhas += [f"url = {_aspas(eco)}", "silent", "fail", "max-time = 20", "max-filesize = 200"]
        r = subprocess.run(
            ["/usr/bin/curl", "-q", "-K", "-"],
            input="\n".join(linhas),
            capture_output=True,
            text=True,
            timeout=40,
        )
        if r.returncode == 0:
            saida = r.stdout.strip()
            try:
                return str(ipaddress.ip_address(saida))
            except ValueError:
                ultimo = -1  # o eco respondeu lixo: tenta o outro
                continue
        ultimo = r.returncode
        # Recusa de senha ou de conexão é do proxy, não do eco: não adianta
        # perguntar ao segundo eco.
        if r.returncode in (5, 7, 97):
            break
    motivo = _MOTIVO_CURL.get(ultimo, f"falhou (curl {ultimo})" if ultimo > 0 else "resposta estranha")
    raise Falha(f"proxy {cfg['proxy_host']}:{cfg['proxy_port']}: {motivo}")


def conta_que_funciona(ip: str, candidatas: list[tuple]) -> tuple:
    """A primeira conta que sai pelo IP certo. Nada é gravado aqui."""
    tentativas = []
    for conta in candidatas:
        try:
            saida = testar_proxy(_com_endereco(conta, ip))
        except Falha as e:
            tentativas.append(str(e))
            continue
        if saida == ip:
            return conta
        tentativas.append(f"saiu pelo IP {saida}")
    if not candidatas:
        raise Falha("nenhuma conta de proxy conhecida neste AdsPower para testar")
    raise Falha(
        f"nenhuma das {len(candidatas)} contas de proxy deste AdsPower funcionou com o IP {ip} "
        f"({tentativas[-1]}). Se é proxy de compra nova, configure esse IP à mão uma vez em "
        "qualquer perfil do AdsPower; depois disso o serviço reconhece a conta. Nada foi trocado."
    )


# --- uma empresa -------------------------------------------------------------


def aplicar_empresa(pend: dict, *, simular: bool, todos: list[dict] | None = None) -> str:
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

    # 1) Lê os perfis. Perfil que não está neste AdsPower (o da Contabilidade
    #    fica em outro) não impede os demais de receberem o IP.
    atuais, fora = [], []
    for p in perfis:
        lido = ler_perfil(p["user_id"])
        if lido is None:
            fora.append(f"n{p['profile_no']}")
        else:
            atuais.append((p, lido.get("user_proxy_config") or {}))
    if fora:
        avisos.append(
            f"{', '.join(fora)} não está no AdsPower deste Mac (é do outro, da Contabilidade?) "
            "e precisa ser trocado lá à mão"
        )
    a_trocar = [(p, c) for p, c in atuais if (c.get("proxy_host") or "").strip() != ip]

    # 2) Descobre a conta certa para o IP e testa, ANTES de mexer em qualquer
    #    perfil. Um perfil só é trocado com a conta que acabou de funcionar.
    conta = None
    if a_trocar:
        if todos is None:
            todos = todos_os_perfis()
        conta = conta_que_funciona(ip, contas_candidatas(ip, [c for _p, c in atuais], todos))

    if simular:
        return (
            f"SIMULAÇÃO {pend['apelido']} {ip}: trocaria {len(a_trocar)} perfil(s) "
            + ", ".join(f"n{p['profile_no']}" for p, _ in a_trocar)
            + f" | {len(atuais) - len(a_trocar)} já no IP"
            + (" | " + "; ".join(avisos) if avisos else "")
        )

    # 3) Grava e relê cada perfil.
    trocados: list[str] = []
    for p, _c in a_trocar:
        novo = _com_endereco(conta, ip)
        try:
            adspower("/api/v1/user/update", {"user_id": p["user_id"], "user_proxy_config": novo})
        except Falha as e:
            e.args = (
                f"trocado em {', '.join(trocados) or 'nenhum'}; parou no n{p['profile_no']}: {e}",
            )
            e.gravou_algo = bool(trocados)
            raise
        trocados.append(f"n{p['profile_no']}")  # gravou: conta como trocado já
        conferido = ((ler_perfil(p["user_id"]) or {}).get("user_proxy_config") or {}).get(
            "proxy_host"
        )
        if conferido != ip:
            raise Falha(f"trocado em {', '.join(trocados)}; o n{p['profile_no']} não ficou com o IP novo")

    if avisos:
        raise Falha(
            f"IP aplicado em {len(atuais)} perfil(s) deste Mac, mas: " + "; ".join(avisos)
        )
    return (
        f"{pend['apelido']} {ip}: {len(trocados)} trocado(s), "
        f"{len(atuais) - len(trocados)} já estavam"
    )


# --- execução ----------------------------------------------------------------


def passada(simular: bool) -> int:
    try:
        pendentes = davinci("GET", "/api/agent/adspower/ip-pendentes") or []
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
        # DaVinci fora do ar: nada a fazer, tenta no minuto seguinte.
        log.warning("DaVinci indisponível (%s)", type(e).__name__)
        return 1
    if not pendentes:
        log.info("nada pendente")
        return 0
    todos = None  # lido uma vez só por passada, e só se precisar
    falhas = 0
    for pend in pendentes:
        relatar = True
        try:
            if todos is None:
                todos = todos_os_perfis()
            resumo = aplicar_empresa(pend, simular=simular, todos=todos)
            ok, erro = True, None
            log.info(_limpo(resumo))
        except FalhaPassageira as e:
            ok, erro = False, _limpo(e)
            falhas += 1
            # Passageira e sem nada gravado: não castiga a empresa por 1 hora.
            relatar = getattr(e, "gravou_algo", False)
            log.warning("%s %s: %s%s", pend.get("apelido"), pend.get("ip"), erro,
                        "" if relatar else " (tenta de novo no próximo minuto)")
        except Falha as e:
            ok, erro = False, _limpo(e)
            falhas += 1
            log.warning("%s %s: %s", pend.get("apelido"), pend.get("ip"), erro)
        except Exception as e:  # noqa: BLE001 - uma empresa não derruba as outras
            ok, erro = False, f"erro inesperado ({type(e).__name__})"
            falhas += 1
            log.error("%s: erro inesperado\n%s", pend.get("apelido"), _limpo(traceback.format_exc()))
        if relatar and not simular:
            try:
                davinci(
                    "POST",
                    "/api/agent/adspower/ip-resultado",
                    {"company_id": pend["company_id"], "ip": pend["ip"], "ok": ok, "erro": erro},
                )
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                log.warning("não consegui avisar o DaVinci (%s); ele pergunta de novo", type(e).__name__)
    return 1 if falhas else 0


def ensaio_perfil(user_id: str, ip: str) -> int:
    """Descobre e testa a conta de proxy para UM perfil com o IP dado. Nunca grava."""
    perfil = ler_perfil(user_id)
    if perfil is None:
        print(f"perfil {user_id} não encontrado neste AdsPower")
        return 1
    atual = perfil.get("user_proxy_config") or {}
    print(f"perfil n{perfil.get('serial_number')} {perfil.get('name')}")
    if tem_proxy(atual):
        print(f"  proxy atual: {atual.get('proxy_type')} {atual.get('proxy_host')}:{atual.get('proxy_port')}")
    else:
        print("  proxy atual: nenhum")
    candidatas = contas_candidatas(ip, [atual], todos_os_perfis())
    print(f"  contas de proxy para tentar: {len(candidatas)}")
    try:
        conta = conta_que_funciona(ip, candidatas)
    except Falha as e:
        print("  resultado: NÃO FUNCIONOU —", _limpo(e))
        return 1
    print(f"  resultado: a conta {candidatas.index(conta) + 1} sai pelo IP {ip} (porta {conta[2]})")
    print("  (ensaio: nada foi gravado)")
    return 0


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
        log.error("DaVinci respondeu %s", e.code)
        return 1
    except Exception:  # noqa: BLE001 - nada sai daqui sem passar pelo _limpo
        log.error("erro inesperado\n%s", _limpo(traceback.format_exc()))
        return 1


if __name__ == "__main__":
    sys.exit(main())
