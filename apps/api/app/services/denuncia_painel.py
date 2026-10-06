"""Ouvidoria › Denúncia › aba "Anúncios e denúncias": o que aconteceu com cada anúncio.

Vinicius, 01/10/2026: as abas Anúncios e Denúncias mostravam a mesma loja duas vezes
("eles são quase as mesmas informações") e ele precisava ver "já no painel, bem fácil" a
denúncia no marketplace e a denúncia na Anatel, com o resultado. Aqui cada anúncio ganha
dois status, calculados das denúncias que o mini mandou:

- **na loja** (denúncia no marketplace): não denunciado · aguardando · recusou · removido;
- **na Anatel**: processo aberto (SEI) ou, sem processo, onde ele está no caminho — na fila,
  falta o print da página — ou nada a fazer (fora do ar, removido, sem nº declarado).

A fila de verdade é do sistema do mini (/api/v1/denunciar/pendentes); aqui é o retrato
pela cópia, com as mesmas regras: o Diversos vai sem denúncia na loja (01/10), o Nosso
também (06/10 — antes só ia com a recusa da loja; "esperando a loja recusar" e "falta
denunciar na loja" não aparecem mais), e todo anúncio precisa do print da página
("Captura no ato").

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
    # 01/10 (Vinicius): "aguardando" separado — a loja já respondeu e o robô está conferindo o anúncio
    # (o e-mail chegou; vira "recusou" ou "removido" depois do print) × sem resposta nenhuma
    "conferindo": ("respondeu · conferindo", "muted"),
    "aguardando": ("aguardando", "muted"),
    "nao": ("não denunciado", "muted"),
    # 01/10 (Vinicius: "o que tava pendente e não vai mais abrir pode excluir, deixa sem informação
    # nenhuma"): Diversos com denúncia velha sem desfecho — desde 01/10 ele não é mais denunciado nas
    # lojas; fica em branco e fora das contas
    "vazio": ("—", "muted"),
}
_RESPONDEU = ("respondeu", "não identific", "provável", "medidas cab")
_RECUSOU = ("não identific", "continua ativo", "improcedente")
ANATEL = {
    "processo": ("processo aberto", "info"),
    "fila": ("na fila", "warning"),
    "falta_print": ("falta o print", "warning"),
    "esperando_recusa": ("esperando a loja recusar", "muted"),
    "falta_loja": ("falta denunciar na loja", "muted"),
    "nada": ("—", "muted"),
}
# 05/10: fase do processo no SEI (status_anatel que o sistema do mini grava com a leitura do
# passo 1)
FASE_SEI = {
    "Recebida": ("processo recebido", "info"),
    "Em tratamento": ("na fiscalização", "info"),
    "Respondida — analisar": ("Anatel respondeu", "success"),
    "Exigência": ("Anatel pede complemento", "danger"),
}


def _quando(d: dict) -> tuple:
    return (d.get("data") or "", d.get("hora") or "", d.get("id") or 0)


def status_loja(dens: list[dict], grupo: str | None = None) -> dict:
    """Denúncias do anúncio nos marketplaces → o que a loja fez. Removido vale mesmo
    que uma tentativa anterior tenha sido recusada; senão manda a mais recente.
    Sem desfecho: "conferindo" se a resposta já chegou (nota do resultado), senão
    "aguardando". No Diversos (GRUPO 2): a resposta que já chegou e é recusa conta como
    "recusou" ("o que tinha denúncia e perdeu coloca recusada"); o resto fica "vazio"."""
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
        nota = (ult.get("resultado_nota") or "").lower()
        chave = "conferindo" if any(x in nota for x in _RESPONDEU) else "aguardando"
        if grupo == "GRUPO 2":
            chave = "recusou" if chave == "conferindo" and any(x in nota for x in _RECUSOU) else "vazio"
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
        # 05/10: o passo 1 lê o andamento do processo no SEI (status_anatel) — a etiqueta diz a
        # fase; a chave continua "processo" (contas e filtros de "com processo" não mudam)
        fase = FASE_SEI.get(ult.get("status_anatel") or "")
        return {"chave": "processo", "protocolo": proc, "data": ult.get("data"),
                "area": ult.get("status_anatel_area"), "desde": ult.get("status_anatel_em"),
                **({"rotulo_fase": fase[0], "tom_fase": fase[1]} if fase else {}), **extra}
    if anuncio.get("situacao") != "ativo" or anuncio.get("propria"):
        return {"chave": "nada", **extra}
    grupo, hom = anuncio.get("grupo"), (anuncio.get("hom") or "").strip()
    if grupo == "GRUPO 1":
        # 01/10: o Nosso só ia à Anatel depois que a loja recusava. 06/10 (Vinicius): "pode abrir na
        # Anatel mesmo sem recusa da loja, pois hoje não vamos mais denunciar na loja os nosso" — vai
        # direto; só o que a loja já removeu fica fora
        if loja["chave"] == "removido":
            return {"chave": "nada", **extra}
    elif grupo == "GRUPO 2":
        # 01/10: o Diversos vai sem denúncia na loja — com nº declarado (ou TikTok sem nº)
        if not hom and anuncio.get("marketplace") != "TikTok Shop":
            return {"chave": "nada", **extra}
    else:
        return {"chave": "nada", **extra}
    return {"chave": "fila" if tem_print else "falta_print", **extra}


def rotular(st: dict, tabela: dict) -> dict:
    rotulo, tom = tabela[st["chave"]]
    return {**st, "rotulo": st.get("rotulo_fase") or rotulo, "tom": st.get("tom_fase") or tom}


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
                "processos": set(), "casos": {}, "caso_pendente": False,
                "ultimo_achado": "", "ultima_denuncia": "",
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
        for k in a.get("casos") or []:
            lj["casos"][k["id"]] = k
        lj["caso_pendente"] = lj["caso_pendente"] or bool(a.get("caso_pendente"))
        lj["ultimo_achado"] = max(lj["ultimo_achado"], a.get("visto_primeiro") or "")
        lj["ultima_denuncia"] = max(lj["ultima_denuncia"], a.get("ultima_denuncia") or "")
    out = []
    for lj in lojas.values():
        lj["processos"] = sorted(lj["processos"])
        lj["casos"] = sorted(lj["casos"].values(), key=lambda k: k["id"])
        lj["caso_pendente"] = lj["caso_pendente"] and not lj["casos"]
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
