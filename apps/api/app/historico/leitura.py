"""Transforma as linhas cruas do gatilho no que o Eduardo lê.

"pricing_overrides U {price_override: 1299 → 1249}" vira "alterou Preço da
célula · dg053 · ML kia · Preço manual: R$ 1.299,00 → R$ 1.249,00".

O NOME do item (dg053 · ML kia) é resolvido e CONGELADO quando o evento é
gravado (`congelar`): se o produto for apagado ou renomeado depois, o
histórico continua dizendo o que era na hora — e a busca acha pelo SKU.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.historico import nomes
from app.models.base import Base

BRT = ZoneInfo("America/Sao_Paulo")

# Coluna que dá nome à linha de outra tabela.
ROTULOS = (
    "sku", "apelido", "nome", "name", "account_name", "titulo", "numero", "codigo",
    "razao_social", "slug", "label", "email",
)

# Quais chaves compõem o NOME do item, por tabela, em ordem. Tabela fora
# daqui usa só o próprio rótulo (sku, apelido, nome…) — pegar toda FK dava
# "kia · heisenberg" (responsável) ou "conta · segmento · loja · segmento".
ITEM_FKS: dict[str, tuple[str, ...]] = {
    "pricing_overrides": ("pricing_product_id", "pricing_account_id"),
    "historico_acesso": ("user_id",),
    "product_links": ("product_id",),
    "segment_special_dates": ("segment_id",),
}

# Guardados como fração (0,125) e digitados como porcentagem (12,5%).
PERCENTUAIS = {"commission", "min_margin", *(f"margin{i}" for i in range(1, 6))}
DINHEIRO = {
    "price_override", "bling_cost_price", "valor_base",
    *(f"cost_kit{i}" for i in range(1, 9)), *(f"shipping{i}" for i in range(1, 6)),
}


def _tabela_meta(tabela: str):
    return Base.metadata.tables.get(f"{Base.metadata.schema}.{tabela}")


def fks(tabela: str) -> dict[str, Any]:
    """coluna -> Table de destino, para toda FK cujo destino tem nome."""
    t = _tabela_meta(tabela)
    if t is None:
        return {}
    saida = {}
    for fk in t.foreign_keys:
        destino = fk.column.table
        if any(c in destino.c for c in ROTULOS):
            saida[fk.parent.name] = destino
    return saida


def _chave(pk, v):
    try:
        return UUID(str(v)) if str(pk.type).upper() == "UUID" else int(v)
    except (ValueError, TypeError):
        return None


async def nomes_de_fk(session, alteracoes) -> dict:
    """{(tabela_destino, id): nome}. Linha apagada ou fora do alcance cai no
    último rótulo que o próprio Histórico viu dela (ex. o produto excluído no
    mesmo pedido, cuja cascata levou os preços)."""
    pedidos: dict[Any, set] = {}
    for a in alteracoes:
        for col, destino in fks(a.tabela).items():
            for fonte in (a.ident or {}, a.antes or {}, a.depois or {}):
                v = fonte.get(col)
                if isinstance(v, (str, int)) and not isinstance(v, bool) and v != "":
                    pedidos.setdefault(destino, set()).add(v)
    achados: dict = {}
    for destino, ids in pedidos.items():
        pk = destino.c.get("id")
        if pk is None:
            continue
        coluna = next(destino.c[c] for c in ROTULOS if c in destino.c)
        chaves = [c for c in (_chave(pk, v) for v in ids) if c is not None]
        if chaves:
            for r in await session.execute(select(pk, coluna).where(pk.in_(chaves))):
                if r[1] not in (None, ""):
                    achados[(destino.name, str(r[0]))] = str(r[1])
        faltam = [str(v) for v in ids if (destino.name, str(v)) not in achados]
        if faltam:
            from app.models import HistoricoAlteracao as HA  # noqa: N817

            q = (
                select(HA.registro_id, HA.rotulo)
                .where(HA.tabela == destino.name, HA.registro_id.in_(faltam), HA.rotulo.is_not(None))
                .order_by(HA.id.desc())
            )
            for rid, rot in await session.execute(q):
                achados.setdefault((destino.name, rid), rot)
    return achados


def item(a, fk_nomes: dict) -> str:
    partes = []
    alvo = fks(a.tabela)
    for col in ITEM_FKS.get(a.tabela, ()):
        v = (a.ident or {}).get(col)
        destino = alvo.get(col)
        if v is not None and destino is not None:
            nome = fk_nomes.get((destino.name, str(v)))
            if nome:
                partes.append(nome)
    if a.rotulo:
        partes.insert(0, a.rotulo)
    if partes:
        return " · ".join(partes)
    return f"#{str(a.registro_id)[:8]}" if a.registro_id else ""


# --- formatação ----------------------------------------------------------------

_ISO_DATA_HORA = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_ISO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _numero(v: float, casas_max: int = 4) -> str:
    txt = f"{v:,.{casas_max}f}".rstrip("0").rstrip(".") if not float(v).is_integer() else f"{int(v):,}"
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def e_codigo(k: str) -> bool:
    return k == "id" or k.endswith("_id") or k in ("numero", "codigo", "bling_id", "pedido")


def fmt(v: Any, campo: str | None = None) -> str:
    if v is None:
        return "—"
    if isinstance(v, dict):
        if v.get("_oculto"):
            return "(oculta)"
        if v.get("_arquivo"):
            kb = max(1, round((v.get("bytes") or 0) / 1024))
            return f"arquivo ({kb} KB)"
        if v.get("_longo"):
            return "(texto longo)"
    if isinstance(v, bool):
        return "sim" if v else "não"
    if isinstance(v, (int, float)):
        if campo and e_codigo(campo) and isinstance(v, int):
            return str(v)
        if campo in PERCENTUAIS:
            return _numero(v * 100, 2) + "%"
        if campo in DINHEIRO:
            return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return _numero(v)
    if isinstance(v, str):
        if v == "":
            return "(vazio)"
        if _ISO_DATA_HORA.match(v):
            try:
                d = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if d.tzinfo is not None:
                    d = d.astimezone(BRT)
                return d.strftime("%d/%m/%Y %H:%M")
            except ValueError:
                return v
        if _ISO_DATA.match(v):
            try:
                return date.fromisoformat(v).strftime("%d/%m/%Y")
            except ValueError:
                return v
        return v
    return json.dumps(v, ensure_ascii=False)[:500]


def _e_marcador(v) -> bool:
    return isinstance(v, dict) and any(k in v for k in ("_oculto", "_arquivo", "_longo"))


def _folhas(v: Any, prefixo: tuple = ()) -> dict[tuple, Any]:
    if isinstance(v, dict) and not _e_marcador(v):
        saida = {}
        for k, x in v.items():
            saida.update(_folhas(x, (*prefixo, str(k))))
        return saida
    return {prefixo: v}


def campos(a, fk_nomes: dict) -> list[dict]:
    alvo = fks(a.tabela)
    chaves = list(dict.fromkeys([*(a.antes or {}).keys(), *(a.depois or {}).keys()]))
    saida = []
    for k in chaves:
        antes = (a.antes or {}).get(k)
        depois = (a.depois or {}).get(k)

        # JSON (ex. permissões): só as sub-chaves que mudaram, uma por linha.
        if (
            a.operacao == "U" and isinstance(antes, dict) and isinstance(depois, dict)
            and not _e_marcador(antes) and not _e_marcador(depois)
        ):
            fa, fd = _folhas(antes), _folhas(depois)
            for caminho in sorted(set(fa) | set(fd)):
                if fa.get(caminho) != fd.get(caminho):
                    rotulo = " › ".join(p.replace("_", " ") for p in caminho)
                    saida.append({
                        "campo": f"{k}.{'.'.join(caminho)}",
                        "nome": f"{nomes.nome_campo(k)} › {rotulo}",
                        "antes": fmt(fa.get(caminho)),
                        "depois": fmt(fd.get(caminho)),
                    })
            continue

        def rotular(v, k=k):
            if k in alvo and v is not None and not isinstance(v, dict):
                nome = fk_nomes.get((alvo[k].name, str(v)))
                if nome:
                    return nome
            return fmt(v, k)

        saida.append({
            "campo": k,
            "nome": nomes.nome_campo(k),
            "antes": rotular(antes) if a.operacao != "I" else None,
            "depois": rotular(depois) if a.operacao != "D" else None,
        })
    return saida


def alteracao_out(a, fk_nomes: dict) -> dict:
    if a.operacao == "X":
        nome_item = a.rotulo or ""
    else:
        nome_item = getattr(a, "item", None) or item(a, fk_nomes)
    return {
        "id": a.id,
        "tabela": a.tabela,
        "entidade": nomes.nome_tabela(a.tabela),
        "operacao": a.operacao,
        "verbo": nomes.VERBOS.get(a.operacao, "alterou"),
        "item": nome_item,
        "campos": campos(a, fk_nomes) if a.operacao != "X" else [],
        "vezes": 1,
    }


def agrupar(saidas: list[dict]) -> list[dict]:
    """Iguais em sequência viram uma só com "vezes" (pedido do Bling com 3
    itens mudando igual aparecia 3 vezes)."""
    grupos: list[dict] = []
    for s in saidas:
        chave = (s["tabela"], s["operacao"], s["item"], json.dumps(s["campos"], sort_keys=True))
        if grupos and grupos[-1]["_chave"] == chave:
            grupos[-1]["vezes"] += 1
            continue
        grupos.append({**s, "_chave": chave})
    for g in grupos:
        g.pop("_chave")
    return grupos


async def congelar(session, req_id) -> str:
    """Resolve e grava o nome de cada item do pedido (coluna `item`) e devolve
    o texto de busca do evento."""
    from app.models import HistoricoAlteracao as HA  # noqa: N817

    linhas = (
        (await session.execute(select(HA).where(HA.req_id == req_id).order_by(HA.id)))
        .scalars()
        .all()
    )
    fk_nomes = await nomes_de_fk(session, linhas)
    vistos: list[str] = []
    for a in linhas:
        if a.operacao == "X":
            continue
        nome_item = item(a, fk_nomes)
        a.item = nome_item or None
        if nome_item and nome_item not in vistos:
            vistos.append(nome_item)
    return " | ".join(vistos)[:4000]
