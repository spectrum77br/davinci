#!/usr/bin/env python3
"""Ponte entre a IA de Chamado (Hermes, Mac Santiago) e o DaVinci.

Vinicius, 24/09/2026: o cérebro dos chamados sai do computador do Eduardo e
passa a ser o Hermes — na aba Chamados ele se chama "IA de Chamado", é lá que
liga/desliga e escreve o manual. O Hermes nunca vê o token: tudo que fala com o
DaVinci passa por este script, que lê o token de ~/DaVinci/cerebro/.env.

Comandos (a IA usa pelo terminal):

  precheck                 pré-rodada do cron (é o que roda sem argumento):
                           IA desligada na aba ou nenhum caso novo →
                           {"wakeAgent": false} (a IA nem acorda); senão, o
                           MANUAL + os casos
  pendentes                os mesmos casos, JSON completo
  manual                   o manual do Vinicius (regras ativas), como na passada
  decidir                  lê UMA decisão em JSON da entrada padrão e manda pro
                           DaVinci
  caso --pedido X | --chamado PROTOCOLO | --id UUID
                           um chamado qualquer (pendente ou não), com a conversa:
                           "no chamado do pedido X, vê como está"
  pagamento PEDIDO...      fatos do pagamento no ML (liberação, estorno, envio)
  exemplos [--plataforma ml] [--limite 10] [--offset 0] [--desde 2026-09-01]
                           casos que o cérebro já decidiu, com a conversa
  guarda [estado|assumir|liberar]
                           troca de guarda com o cérebro antigo (só gente usa)

Toda decisão também fica em ~/DaVinci/cerebro/decisoes.jsonl. Sem modo teste
(decisão dele): ligada na aba = decide de verdade.

Só biblioteca padrão: roda no Python do próprio Hermes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PASTA = Path(os.environ.get("DAVINCI_CEREBRO_DIR", "~/DaVinci/cerebro")).expanduser()
ENV = PASTA / ".env"
DECISOES = PASTA / "decisoes.jsonl"
# Casos entregues à IA nesta rodada e casos já decididos (impressão digital da
# conversa) — se a IA pular um caso ou a decisão falhar, ele não acorda a IA de
# novo a cada passada até algo mudar nele.
SERVIDOS = PASTA / "estado" / "servidos.json"
DECIDIDOS = PASTA / "estado" / "decididos.json"

ACOES = ("esperar", "responder", "resolver", "humano")
# Caso entregue à IA (pela passada ou pelo `caso`) pode ser decidido por 2 h. A
# passada automática e uma análise pedida à parte SOMAM na lista — uma não apaga
# a outra.
SERVIDO_VALE_S = 2 * 3600
TEXTO_MAX = 4000  # por mensagem, no que vai pro prompt


def _cfg() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if ENV.exists():
        for linha in ENV.read_text().splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in list(cfg):
        cfg[k] = os.environ.get(k, cfg[k])
    if not cfg.get("DAVINCI_CEREBRO_TOKEN"):
        sys.exit(f"sem DAVINCI_CEREBRO_TOKEN em {ENV}")
    cfg.setdefault("DAVINCI_URL", "https://app.hadken.com")
    cfg.setdefault("DAVINCI_CEREBRO_PLATAFORMA", "todas")
    cfg.setdefault("DAVINCI_CEREBRO_CANAIS", "robo,api,manual")
    cfg.setdefault("DAVINCI_CEREBRO_LIMITE", "10")
    return cfg


def _post(cfg: dict[str, str], rota: str, corpo: dict) -> dict:
    req = urllib.request.Request(
        f"{cfg['DAVINCI_URL'].rstrip('/')}/api/chamados/agent/{rota}",
        data=json.dumps(corpo, default=str).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Agent-Token": cfg["DAVINCI_CEREBRO_TOKEN"],
            "User-Agent": "davinci-cerebro-hermes/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:  # noqa: S310 — URL do .env
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        corpo_erro = e.read().decode(errors="replace")[:600]
        sys.exit(f"DaVinci {rota}: HTTP {e.code} {corpo_erro}")


def _ler(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _gravar(p: Path, dados: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1))
    tmp.replace(p)


def _servir(casos: list[dict]) -> None:
    agora = time.time()
    servidos = {
        k: v
        for k, v in _ler(SERVIDOS).items()
        if isinstance(v, dict) and agora - v.get("t", 0) < SERVIDO_VALE_S
    }
    servidos.update({c["chamado_id"]: {"d": _digital(c), "t": agora} for c in casos})
    _gravar(SERVIDOS, servidos)


def _digital(caso: dict) -> str:
    """Muda quando o caso muda: fala nova, instrução nova, bloqueio novo."""
    msgs = caso.get("mensagens") or []
    partes = [
        msgs[-1]["id"] if msgs else "",
        str(len(msgs)),
        (caso.get("instrucao") or {}).get("quando") or "",
        (caso.get("bloqueio") or {}).get("desde") or "",
    ]
    return hashlib.sha256("|".join(partes).encode()).hexdigest()[:16]


def _enxuto(caso: dict) -> dict:
    c = dict(caso)
    c["mensagens"] = [
        {**m, "texto": (m.get("texto") or "")[:TEXTO_MAX]} for m in caso.get("mensagens") or []
    ]
    return c


def _casos(cfg: dict[str, str]) -> list[dict]:
    plat = cfg["DAVINCI_CEREBRO_PLATAFORMA"].strip()
    corpo = {
        "limite": int(cfg["DAVINCI_CEREBRO_LIMITE"]),
        "plataforma": None if plat in ("", "todas") else plat,
        "canais": [c.strip() for c in cfg["DAVINCI_CEREBRO_CANAIS"].split(",") if c.strip()],
    }
    casos = _post(cfg, "analisar", corpo).get("chamados") or []
    decididos = _ler(DECIDIDOS)
    novos = [c for c in casos if decididos.get(c["chamado_id"]) != _digital(c)]
    _servir(novos)
    return [_enxuto(c) for c in novos]


def _manual(regras: list[dict]) -> str:
    if not regras:
        return "(o manual ainda está vazio)"
    linhas = []
    for i, r in enumerate(regras, 1):
        plat = r.get("plataforma") or "todas as plataformas"
        linhas.append(f"{i}. [{plat}] QUANDO: {r['quando']}\n   FAÇA: {r['faca']}")
    return "\n".join(linhas)


def _aprendizado(itens: list[dict]) -> str:
    erros = [a for a in itens if not a.get("certo")]
    certos = [a for a in itens if a.get("certo")]
    partes = []
    if erros:
        partes.append("CORREÇÕES DO VINICIUS (decisões suas que ele marcou como ERRADAS — não repita):")
        for a in erros:
            partes.append(
                f"- pedido {a.get('pedido_bling')} ({a.get('plataforma')}). Você decidiu: "
                f"{(a.get('decisao') or '')[:500]}\n  O CERTO ERA: {a.get('correcao')}"
            )
    if certos:
        partes.append("DECISÕES QUE ELE CONFIRMOU (referência do que é certo):")
        for a in certos:
            partes.append(
                f"- pedido {a.get('pedido_bling')} ({a.get('plataforma')}): "
                f"{(a.get('decisao') or '')[:400]}"
            )
    return "\n".join(partes)


def cmd_precheck(cfg: dict[str, str], _a: argparse.Namespace) -> None:
    ia = _post(cfg, "cerebro", {})
    if not ia.get("ligada"):
        print(json.dumps({"wakeAgent": False}))
        return
    casos = _casos(cfg)
    if not casos:
        print(json.dumps({"wakeAgent": False}))
        return
    print(
        f"{len(casos)} caso(s) esperando a IA de Chamado "
        f"({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC). Decida CADA UM com "
        "`python3 ~/.hermes/scripts/davinci_chamados.py decidir`.\n"
    )
    print("MANUAL (regras do Vinicius — valem acima do seu julgamento):")
    print(_manual(ia.get("regras") or []))
    aprendizado = _aprendizado(ia.get("aprendizado") or [])
    if aprendizado:
        print("\n" + aprendizado)
    print("\nCASOS:")
    print(json.dumps(casos, ensure_ascii=False, indent=1))


def cmd_manual(cfg: dict[str, str], _a: argparse.Namespace) -> None:
    ia = _post(cfg, "cerebro", {})
    print(f"IA {'LIGADA' if ia.get('ligada') else 'DESLIGADA'}")
    print(_manual(ia.get("regras") or []))
    aprendizado = _aprendizado(ia.get("aprendizado") or [])
    if aprendizado:
        print("\n" + aprendizado)


def cmd_pendentes(cfg: dict[str, str], _a: argparse.Namespace) -> None:
    print(json.dumps(_casos(cfg), ensure_ascii=False, indent=1))


def _validar(d: dict) -> str | None:
    if not d.get("chamado_id"):
        return "falta chamado_id"
    if d.get("acao") not in ACOES:
        return f"acao tem que ser uma de {ACOES}"
    if not (1 <= len(d.get("classe") or "") <= 60):
        return "classe: 1 a 60 caracteres"
    if not (1 <= len(d.get("resumo") or "") <= 600):
        return "resumo: 1 a 600 caracteres"
    if d["acao"] == "responder" and not (d.get("texto_replica") or "").strip():
        return "responder exige texto_replica"
    if d["acao"] != "responder" and d.get("texto_replica"):
        return "texto_replica só com acao=responder"
    return None


def cmd_decidir(cfg: dict[str, str], _a: argparse.Namespace) -> None:
    try:
        d = json.loads(sys.stdin.read())
    except ValueError as e:
        sys.exit(f"JSON inválido: {e}")
    campos = {
        "chamado_id",
        "classe",
        "resumo",
        "acao",
        "texto_replica",
        "reanexar_abertura",
        "valor_recuperado",
        "observacao",
        "reabrir",
    }
    extras = set(d) - campos
    if extras:
        sys.exit(f"campos desconhecidos: {sorted(extras)}")
    erro = _validar(d)
    if erro:
        sys.exit(f"decisão recusada: {erro}")
    servido = _ler(SERVIDOS).get(d["chamado_id"])
    digital = servido.get("d") if isinstance(servido, dict) else None
    if digital is None:
        sys.exit("esse chamado não está entre os casos desta rodada (use `caso` pra buscar)")
    resposta = _post(cfg, "analise", d)
    with DECISOES.open("a") as f:
        f.write(
            json.dumps(
                {"quando": datetime.now(timezone.utc).isoformat(), **d, "resposta": resposta},
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )
    decididos = _ler(DECIDIDOS)
    decididos[d["chamado_id"]] = digital
    _gravar(DECIDIDOS, decididos)
    print(json.dumps({"ok": True, "resposta": resposta}, ensure_ascii=False))


def cmd_caso(cfg: dict[str, str], a: argparse.Namespace) -> None:
    corpo = {"pedido_bling": a.pedido, "chamado": a.chamado, "chamado_id": a.id}
    out = _post(cfg, "caso", {k: v for k, v in corpo.items() if v})
    casos = out.get("chamados") or []
    _servir(casos)  # achado a pedido: pode ser decidido
    print(json.dumps([_enxuto(c) for c in casos], ensure_ascii=False, indent=1))


def cmd_pagamento(cfg: dict[str, str], a: argparse.Namespace) -> None:
    print(
        json.dumps(
            _post(cfg, "pagamento-ml", {"pedidos_bling": a.pedidos}), ensure_ascii=False, indent=1
        )
    )


def cmd_exemplos(cfg: dict[str, str], a: argparse.Namespace) -> None:
    corpo = {"limite": a.limite, "offset": a.offset, "plataforma": a.plataforma, "desde": a.desde}
    out = _post(cfg, "exemplos", {k: v for k, v in corpo.items() if v is not None})
    out["chamados"] = [_enxuto(c) for c in out.get("chamados") or []]
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_guarda(cfg: dict[str, str], a: argparse.Namespace) -> None:
    corpo = {"estado": {}, "assumir": {"exclusivo": True}, "liberar": {"exclusivo": False}}[a.o_que]
    print(json.dumps(_post(cfg, "cerebro", corpo), ensure_ascii=False, indent=1))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("precheck")
    sub.add_parser("pendentes")
    sub.add_parser("manual")
    sub.add_parser("decidir")
    cs = sub.add_parser("caso")
    cs.add_argument("--pedido")
    cs.add_argument("--chamado")
    cs.add_argument("--id", help="chamado_id (uuid)")
    pg = sub.add_parser("pagamento")
    pg.add_argument("pedidos", nargs="+")
    ex = sub.add_parser("exemplos")
    ex.add_argument("--plataforma")
    ex.add_argument("--limite", type=int, default=10)
    ex.add_argument("--offset", type=int, default=0)
    ex.add_argument("--desde")
    gd = sub.add_parser("guarda")
    gd.add_argument("o_que", nargs="?", default="estado", choices=("estado", "assumir", "liberar"))
    a = p.parse_args()
    if a.cmd is None:  # o agendador do Hermes chama sem argumento
        a.cmd = "precheck"
    cfg = _cfg()
    {
        "precheck": cmd_precheck,
        "pendentes": cmd_pendentes,
        "manual": cmd_manual,
        "decidir": cmd_decidir,
        "caso": cmd_caso,
        "pagamento": cmd_pagamento,
        "exemplos": cmd_exemplos,
        "guarda": cmd_guarda,
    }[a.cmd](cfg, a)


if __name__ == "__main__":
    main()
