"""Ouvidoria › Denúncia › aba "Anúncios e denúncias": o que aconteceu com cada anúncio.

Vinicius, 01/10/2026: as abas Anúncios e Denúncias mostravam a mesma loja duas vezes
("eles são quase as mesmas informações") e ele precisava ver "já no painel, bem fácil" a
denúncia no marketplace e a denúncia na Anatel, com o resultado. Aqui cada anúncio ganha
dois status, calculados das denúncias que o mini mandou:

- **na loja** (denúncia no marketplace): não denunciado · aguardando · recusou · removido;
- **na Anatel**: processo aberto (SEI) ou, sem processo, onde ele está no caminho — na fila,
  falta o print da página, esperando a loja recusar (o Nosso só vai à Anatel depois da
  recusa, regra de 01/10), falta denunciar na loja — ou nada a fazer (fora do ar).

A fila de verdade é do sistema do mini (/api/v1/denunciar/pendentes); aqui é o retrato
pela cópia, com as mesmas regras de 01/10: o Diversos vai sem denúncia na loja, o Nosso
só com a recusa, e todo anúncio precisa do print da página ("Captura no ato").

Funções puras (sem banco) para serem testadas direto.
"""

from __future__ import annotations

from typing import Any

CANAIS_LOJA = ("Mercado Livre", "Shopee", "TikTok Shop", "Amazon")
CANAL_SEI = "Anatel SEI"
# Anatel Consumidor: desde 25/09 só leitura; o anúncio volta à fila do SEI
CANAL_CONSUMIDOR = "Anatel"
RESOLVEU = ("Anúncio removido", "Anúncio ajustado", "Loja suspensa")

# chave → (rótulo na tela, tom da etiqueta)
LOJA = {
    "removido": ("removido", "success"),
    "recusou": ("recusou", "danger"),
    "aguardando": ("aguardando", "muted"),
    "nao": ("não denunciado", "muted"),
}
ANATEL = {
    "processo": ("processo aberto", "info"),
    "fila": ("na fila", "warning"),
    "falta_print": ("falta o print", "warning"),
    "esperando_recusa": ("esperando a loja recusar", "muted"),
    "falta_loja": ("falta denunciar na loja", "muted"),
    "nada": ("—", "muted"),
}


def _quando(d: dict) -> tuple:
    return (d.get("data") or "", d.get("hora") or "", d.get("id") or 0)


def status_loja(dens: list[dict]) -> dict:
    """Denúncias do anúncio nos marketplaces → o que a loja fez. Removido vale mesmo
    que uma tentativa anterior tenha sido recusada; senão manda a mais recente."""
    dens = [d for d in dens if d.get("canal") in CANAIS_LOJA]
    if not dens:
        return {"chave": "nao", "data": None, "tentativas": 0, "canal": None}
    ult = max(dens, key=_quando)
    if any(
        (d.get("resultado") or "") in RESOLVEU or d.get("situacao") == "Procedente" for d in dens
    ):
        chave = "removido"
    elif "improcedente" in f"{ult.get('situacao') or ''} {ult.get('resultado') or ''}".lower():
        chave = "recusou"
    else:
        chave = "aguardando"
    return {"chave": chave, "data": ult.get("data"), "tentativas": len(dens),
            "canal": ult.get("canal")}


def status_anatel(anuncio: dict, dens: list[dict], loja: dict, tem_print: bool) -> dict:
    """Onde o anúncio está no caminho da Anatel (SEI)."""
    sei = [d for d in dens if d.get("canal") == CANAL_SEI]
    consumidor = [d for d in dens if d.get("canal") == CANAL_CONSUMIDOR and d.get("protocolo")]
    extra = {"consumidor": max(consumidor, key=_quando).get("protocolo") if consumidor else None}
    if sei:
        ult = max(sei, key=_quando)
        proc = ult.get("protocolo") or ult.get("sei_processo")
        return {"chave": "processo", "protocolo": proc, "data": ult.get("data"), **extra}
    if anuncio.get("situacao") != "ativo" or anuncio.get("propria"):
        return {"chave": "nada", **extra}
    grupo, hom = anuncio.get("grupo"), (anuncio.get("hom") or "").strip()
    if grupo == "GRUPO 1":
        # 01/10 (Vinicius): o Nosso só vai à Anatel depois que a loja recusou
        if loja["chave"] == "removido":
            return {"chave": "nada", **extra}
        if loja["chave"] == "nao":
            return {"chave": "falta_loja", **extra}
        if loja["chave"] == "aguardando":
            return {"chave": "esperando_recusa", **extra}
    elif grupo == "GRUPO 2":
        # 01/10: o Diversos vai sem denúncia na loja — com nº declarado (ou TikTok sem nº)
        if not hom and anuncio.get("marketplace") != "TikTok Shop":
            return {"chave": "nada", **extra}
    else:
        return {"chave": "nada", **extra}
    return {"chave": "fila" if tem_print else "falta_print", **extra}


def rotular(st: dict, tabela: dict) -> dict:
    rotulo, tom = tabela[st["chave"]]
    return {**st, "rotulo": rotulo, "tom": tom}


def somar_lojas(itens: list[dict]) -> list[dict]:
    """Anúncios (já com "loja_st" e "anatel_st") → uma linha por marketplace + loja."""
    lojas: dict[tuple, dict] = {}
    for a in itens:
        k = (a.get("marketplace"), a.get("shop_id"))
        lj = lojas.get(k)
        if lj is None:
            lj = lojas[k] = {
                "marketplace": a.get("marketplace"), "shop_id": a.get("shop_id"),
                "chave": a.get("shop_id") or "_sem", "loja": None,
                "anuncios": 0, "no_ar": 0, "fora_do_ar": 0, "vendas": 0,
                "nosso": 0, "diversos": 0, "outros": 0,
                "na_loja": dict.fromkeys(LOJA, 0), "na_anatel": dict.fromkeys(ANATEL, 0),
                "processos": set(), "ultimo_achado": "", "ultima_denuncia": "",
            }
        lj["loja"] = lj["loja"] or a.get("loja")
        lj["anuncios"] += 1
        lj["no_ar"] += a.get("situacao") == "ativo"
        lj["fora_do_ar"] += a.get("situacao") == "fora do ar"
        lj["vendas"] += int(a.get("vendas") or 0)
        g = a.get("grupo")
        lj["nosso" if g == "GRUPO 1" else "diversos" if g == "GRUPO 2" else "outros"] += 1
        lj["na_loja"][a["loja_st"]["chave"]] += 1
        lj["na_anatel"][a["anatel_st"]["chave"]] += 1
        if a["anatel_st"].get("protocolo"):
            lj["processos"].add(a["anatel_st"]["protocolo"])
        lj["ultimo_achado"] = max(lj["ultimo_achado"], a.get("visto_primeiro") or "")
        lj["ultima_denuncia"] = max(lj["ultima_denuncia"], a.get("ultima_denuncia") or "")
    out = []
    for lj in lojas.values():
        lj["processos"] = sorted(lj["processos"])
        lj["ultimo_achado"] = lj["ultimo_achado"] or None
        lj["ultima_denuncia"] = lj["ultima_denuncia"] or None
        out.append(lj)
    return out


def numeros(itens: list[dict], lojas: list[dict]) -> dict:
    """Os cartões do topo, sobre o recorte."""
    n = {
        "lojas": len(lojas), "anuncios": len(itens),
        "no_ar": sum(a.get("situacao") == "ativo" for a in itens),
        "fora_do_ar": sum(a.get("situacao") == "fora do ar" for a in itens),
        "na_loja": dict.fromkeys(LOJA, 0), "na_anatel": dict.fromkeys(ANATEL, 0),
    }
    for a in itens:
        n["na_loja"][a["loja_st"]["chave"]] += 1
        n["na_anatel"][a["anatel_st"]["chave"]] += 1
    n["processos"] = len({p for lj in lojas for p in lj["processos"]})
    return n


def ordenar_lojas(lojas: list[dict], ordem: str) -> list[dict]:
    chave: dict[str, Any] = {
        "vendas": lambda x: (-x["vendas"], -x["anuncios"]),
        "anuncios": lambda x: (-x["anuncios"], -x["vendas"]),
        "recusadas": lambda x: (-x["na_loja"]["recusou"], -x["vendas"]),
        "fila": lambda x: (-(x["na_anatel"]["fila"] + x["na_anatel"]["falta_print"]), -x["vendas"]),
        "recentes": lambda x: (x["ultimo_achado"] or "",),
        "nome": lambda x: ((x["loja"] or "").lower(),),
    }
    f = chave.get(ordem, chave["vendas"])
    return sorted(lojas, key=f, reverse=(ordem == "recentes"))
