"""Contrato de migração de leitura, sem depender do cursor dos adaptadores.

A fonte e a completude são validadas pelo importador. Este módulo somente
propaga um corte já aplicado a conversas criadas depois da importação. Não
consulta nem marca conversas nas plataformas.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, cast, false, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import literal

from app.models import AtendimentoCanal, AtendimentoConversa
from app.services.atendimento.constantes import e_chat_de_afiliado

VERSAO = 1
ORIGEM = "duoke_snapshot"
ESCOPO_COMPRADOR = "comprador"
ESCOPO_AFILIADO = "afiliado"
_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_HASH = r"^[0-9a-f]{64}$"


def chave_trava(canal_id: Any) -> str:
    return f"atendimento_notificacoes:{canal_id}"


def escopo(plataforma: str, dados: dict | None) -> str:
    return ESCOPO_AFILIADO if e_chat_de_afiliado(plataforma, dados) else ESCOPO_COMPRADOR


def data_utc(valor: Any) -> datetime | None:
    """Data de configuração válida, sempre com timezone explícito."""
    if not isinstance(valor, str):
        return None
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if data.tzinfo is None or data.utcoffset() is None:
        return None
    return data.astimezone(UTC)


def _ids_validos(ids: Any) -> bool:
    return (
        isinstance(ids, list)
        and all(isinstance(i, str) and 1 <= len(i) <= 191 for i in ids)
        and len(ids) == len(set(ids))
    )


def metadados_validos(dados: Any) -> bool:
    """O gate estrutural não substitui a prova da fonte feita no importador."""
    if not isinstance(dados, dict):
        return False
    if type(dados.get("versao")) is not int or dados["versao"] != VERSAO:
        return False
    snapshot = dados.get("snapshot_id")
    if not isinstance(snapshot, str):
        return False
    try:
        if str(UUID(snapshot)) != snapshot:
            return False
    except ValueError:
        return False
    hash_ = dados.get("hash")
    return (
        dados.get("origem") == ORIGEM
        and isinstance(hash_, str)
        and re.fullmatch(_HASH, hash_) is not None
        and _ids_validos(dados.get("nao_lidas_ids"))
        and _ids_validos(dados.get("observadas_limite_ids"))
    )


def perfil_valido(perfil: Any) -> bool:
    return (
        isinstance(perfil, dict)
        and data_utc(perfil.get("corte_em")) is not None
        and metadados_validos({**perfil, "nao_lidas_ids": [], "observadas_limite_ids": []})
    )


def estado_valido(conversa: Any) -> bool:
    return isinstance(getattr(conversa, "notificacoes_corte_em", None), datetime) and (
        metadados_validos(getattr(conversa, "notificacoes_iniciais", None))
    )


def estado_valido_sql(c: Any = AtendimentoConversa):
    """Gate gêmeo, sem CAST de conteúdo JSON que possa derrubar o resumo.

    CASE evita examinar arrays em todas as conversas legadas. Mesmo nos
    metadados malformados, a função que expande arrays recebe só um array.
    """
    dados = c.notificacoes_iniciais
    basico = and_(
        c.notificacoes_corte_em.is_not(None),
        func.jsonb_typeof(dados) == "object",
        func.jsonb_typeof(dados["versao"]) == "number",
        dados["versao"].astext == str(VERSAO),
        func.jsonb_typeof(dados["origem"]) == "string",
        dados["origem"].astext == ORIGEM,
        func.jsonb_typeof(dados["snapshot_id"]) == "string",
        dados["snapshot_id"].astext.op("~")(_UUID),
        func.jsonb_typeof(dados["hash"]) == "string",
        dados["hash"].astext.op("~")(_HASH),
    )
    listas = []
    for chave in ("nao_lidas_ids", "observadas_limite_ids"):
        ids = dados[chave]
        array = case(
            (func.jsonb_typeof(ids) == "array", ids),
            else_=literal([], type_=JSONB),
        )
        elementos = func.jsonb_array_elements_text(array).table_valued("value")
        unicos = (
            select(func.count(func.distinct(elementos.c.value)))
            .select_from(elementos)
            .correlate(c)
            .scalar_subquery()
        )
        invalidos = (
            f'$.{chave}[*] ? (@.type() != "string" || !(@ like_regex "^.{{1,191}}$" flag "s"))'
        )
        listas.append(
            and_(
                func.jsonb_typeof(ids) == "array",
                ~func.jsonb_path_exists(dados, cast(literal(invalidos), JSONPATH)),
                func.jsonb_array_length(array) == unicos,
            )
        )
    return case((basico, and_(*listas)), else_=false())


async def estado_para_nova_conversa(
    session: AsyncSession,
    canal_id: Any,
    plataforma: str,
    dados: dict | None,
) -> dict[str, Any]:
    """Herda um perfil já migrado, sob a trava compartilhada do importador.

    Toda criação com canal participa, mesmo antes de haver perfil. Assim o
    importador exclusivo aguarda criações em voo e cobre todas no backfill;
    criações posteriores aguardam o commit e leem a configuração nova.
    """
    if canal_id is None:
        return {}
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:chave, 0))"),
        {"chave": chave_trava(canal_id)},
    )
    bruto = await session.scalar(
        select(AtendimentoCanal.notificacoes_migracao).where(AtendimentoCanal.id == canal_id)
    )
    perfil = bruto.get(escopo(plataforma, dados)) if isinstance(bruto, dict) else None
    if not perfil_valido(perfil):
        return {}
    corte = data_utc(perfil.get("corte_em"))
    if corte is None:
        return {}
    return {
        "notificacoes_corte_em": corte,
        "notificacoes_iniciais": {
            **{k: perfil[k] for k in ("versao", "snapshot_id", "hash", "origem") if k in perfil},
            "nao_lidas_ids": [],
            "observadas_limite_ids": [],
        },
        "notificacoes_visto_em": None,
    }
