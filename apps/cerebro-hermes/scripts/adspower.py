#!/usr/bin/env python3
"""AdsPower do Mac Santiago para a IA de Chamado: achar o perfil da loja e abrir.

Vinicius, 24/09/2026: "ele tem que identificar qual perfil é e abrir no
AdsPower". Os perfis seguem "Loja - Plataforma" ("Vortan - Shopee", "Aguiar 2 -
Mercado Livre"). Os grupos de operação são Israel/Marrocos/Contas; o grupo
Contabilidade tem cópias ("Mega - ml sh") e só entra se não houver outro.

  achar --conta "Shopee Vortan" --plataforma shopee   → o perfil + confiança
  abrir PERFIL_ID                                      → endereço CDP do navegador
  fechar PERFIL_ID
  perfis                                               → todos (nome, grupo, id)

Local API do AdsPower: no máximo ~1 chamada/s. Só biblioteca padrão.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

BASE = os.environ.get("ADSPOWER_API_BASE", "http://local.adspower.net:50325").rstrip("/")
CACHE = Path("~/DaVinci/cerebro/estado/adspower_perfis.json").expanduser()
CACHE_VALE_S = 3600
GRUPOS_OPERACAO = {"israel", "marrocos", "contas"}

# plataforma do chamado → como aparece no nome do perfil
APELIDOS = {
    "ml": {"mercado livre", "ml", "meli"},
    "shopee": {"shopee", "sh"},
    "tiktok": {"tiktok", "tk"},
    "amazon": {"amazon", "am"},
    "temu": {"temu", "te"},
    "shein": {"shein", "she"},
    "magalu": {"magalu", "ma"},
}
# Lojas com nome diferente no DaVinci e no AdsPower (Vinicius, 24/09).
LOJA_NO_ADSPOWER = {"zorvex": "zortex"}  # "ML Zorvex" = perfil "zortex - Mercado Livre"
# Loja + plataforma que usam um perfil de outro nome — pelo NÚMERO do perfil.
# Vinicius 24/09: "shopee marquezini é o número 160, vai tá com nome de mega escrito".
PERFIL_FIXO = {("marquezini", "shopee"): "160"}
PALAVRAS_PLATAFORMA = {p for s in APELIDOS.values() for p in s} | {
    "mercadolivre", "loja", "tiktok shop",
}


def _sem_acento(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _plataforma(p: str | None) -> str | None:
    v = _sem_acento(p or "")
    for chave, nomes in APELIDOS.items():
        if v == chave or v in nomes or v.replace(" ", "") in {n.replace(" ", "") for n in nomes}:
            return chave
    return None


def _api(caminho: str) -> dict:
    time.sleep(1.1)  # limite da Local API
    with urllib.request.urlopen(f"{BASE}{caminho}", timeout=60) as r:  # noqa: S310
        d = json.loads(r.read())
    if d.get("code") != 0:
        raise RuntimeError(f"AdsPower: {d.get('msg') or d}")
    return d.get("data") or {}


def _perfis(forcar: bool = False) -> list[dict]:
    if not forcar and CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_VALE_S:
        return json.loads(CACHE.read_text())
    todos: list[dict] = []
    for pagina in range(1, 20):
        lista = _api(f"/api/v1/user/list?page_size=100&page={pagina}").get("list") or []
        todos += [
            {
                "id": p["user_id"],
                "numero": p.get("serial_number"),
                "nome": (p.get("name") or "").strip(),
                "grupo": (p.get("group_name") or "").strip(),
            }
            for p in lista
        ]
        if len(lista) < 100:
            break
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(todos, ensure_ascii=False))
    return todos


def _loja_do_perfil(nome: str) -> tuple[str, set[str]]:
    """'Aguiar 2 - Mercado Livre' → ('aguiar 2', {'ml'}); 'Mega - ml sh' → ('mega', {'ml','shopee'})."""
    nome = _sem_acento(nome.split("\n")[0])
    if " - " not in nome:
        return nome, set()
    loja, resto = nome.split(" - ", 1)
    plats = {_plataforma(resto)} - {None}
    if not plats:
        plats = {_plataforma(t) for t in resto.split()} - {None}
    return loja.strip(), plats  # type: ignore[return-value]


def _loja_da_conta(conta: str) -> str:
    """'Shopee Vortan' → 'vortan'; 'ML Aguiar 2' → 'aguiar 2'; 'aguiar2' → 'aguiar 2'."""
    t = _sem_acento(conta)
    for p in sorted(PALAVRAS_PLATAFORMA, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(p)}\b", " ", t)
    t = re.sub(r"([a-z])(\d)$", r"\1 \2", t.strip())
    return re.sub(r"\s+", " ", t).strip()


def achar(conta: str, plataforma: str | None) -> dict:
    loja = _loja_da_conta(conta)
    loja = LOJA_NO_ADSPOWER.get(loja, loja)
    plat = _plataforma(plataforma) or _plataforma(conta.split()[0] if conta else "")
    fixo = PERFIL_FIXO.get((loja, plat or ""))
    if fixo:
        perfil = next((p for p in _perfis() if str(p.get("numero")) == fixo), None)
        if perfil is not None:
            return {"ok": True, "loja": loja, "plataforma": plat, "perfil": perfil,
                    "confianca": "alta", "alternativas": [], "motivo": "perfil fixado pelo Vinicius"}
    candidatos = []
    for p in _perfis():
        loja_p, plats_p = _loja_do_perfil(p["nome"])
        if loja_p != loja:
            continue
        if plat and plat not in plats_p:
            continue
        operacao = _sem_acento(p["grupo"]) in GRUPOS_OPERACAO
        # nome "Loja - Plataforma" exato no grupo de operação é o preferido
        so_essa = len(plats_p) == 1
        candidatos.append((0 if operacao else 1, 0 if so_essa else 1, p))
    candidatos.sort(key=lambda x: (x[0], x[1]))
    if not candidatos:
        return {"ok": False, "loja": loja, "plataforma": plat, "motivo": "nenhum perfil com essa loja e plataforma"}
    melhor = candidatos[0]
    empatados = [c for c in candidatos if c[:2] == melhor[:2]]
    return {
        "ok": len(empatados) == 1,
        "loja": loja,
        "plataforma": plat,
        "perfil": melhor[2],
        "confianca": "alta" if melhor[0] == 0 and melhor[1] == 0 and len(empatados) == 1 else "baixa",
        "alternativas": [c[2] for c in candidatos[1:5]],
        "motivo": None if len(empatados) == 1 else "mais de um perfil igual — confirmar com uma pessoa",
    }


def abrir(perfil_id: str) -> dict:
    d = _api(f"/api/v1/browser/start?user_id={perfil_id}&open_tabs=1")
    ws = (d.get("ws") or {}).get("puppeteer")
    if not ws:
        raise RuntimeError("AdsPower não devolveu o endereço do navegador (o perfil abriu?)")
    return {"perfil": perfil_id, "cdp": ws, "porta": d.get("debug_port")}


def fechar(perfil_id: str) -> dict:
    _api(f"/api/v1/browser/stop?user_id={perfil_id}")
    return {"perfil": perfil_id, "fechado": True}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("achar")
    a.add_argument("--conta", required=True)
    a.add_argument("--plataforma")
    sub.add_parser("perfis").add_argument("--atualizar", action="store_true")
    sub.add_parser("abrir").add_argument("perfil")
    sub.add_parser("fechar").add_argument("perfil")
    args = p.parse_args()
    try:
        if args.cmd == "achar":
            out = achar(args.conta, args.plataforma)
        elif args.cmd == "perfis":
            out = _perfis(forcar=args.atualizar)
        elif args.cmd == "abrir":
            out = abrir(args.perfil)
        else:
            out = fechar(args.perfil)
    except Exception as e:  # noqa: BLE001 — a IA precisa ver o erro, não um traceback
        sys.exit(f"erro: {e}")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
