"""Os fatos aprovados da loja — `atendimento_fatos` por escopo (spec 5.2, 20/10/2026).

Vale só fato `ativo` (aprovado: o CHECK do banco exige `aprovado_por`) e
dentro da validade (`valido_ate IS NULL OR valido_ate > agora`). Escopos
(`constantes.FATO_ESCOPOS`):

- `global`: toda loja (o assunto recorta);
- `plataforma`: a plataforma da conversa;
- `loja`: a integração da conversa;
- `anuncio`: plataforma + `item_id` (e a loja, quando o fato tem uma);
- `produto`: `product_id` com vínculo VIVO (`product_links.morto_desde IS
  NULL`) na loja da conversa — e do anúncio da conversa, quando há anúncio;
- `sku`: o SKU dos itens do pedido;
- `marca`: "uranyx"/"charlots" (quem chama descobre: `sites_marca.
  marca_do_anuncio`, `garantia.e_uranyx`).

Em qualquer escopo, `plataforma` e `integration_id` preenchidos no fato
também recortam (o fato da marca só da Shopee não vale no ML).

Assunto: `assunto IS NULL` vale para qualquer assunto. Com `assunto`, entra o
fato do assunto, o fato sem assunto de escopo ESPECÍFICO (anúncio, produto,
SKU — a origem do envio do anúncio do Watch) e o fato cujo texto casa com a
`pergunta` (busca de texto: `busca_texto.consulta_de` + `ts_rank_cd`) — o
assunto que o modelo escolheu nem sempre é o do fato ("tem NFC?" em "outro"
acha o fato de `espec_celular`).

O agente recebe `{"id": "fato:<id>", "texto", "escopo", "assunto", "fonte"}`
e cita `fato:<id>` (`contexto.id_fato`); quem aprovou nunca sai daqui. Com
`ctx.sem_fatos` (ablação do eval) quem chama nem consulta.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import and_, case, false, func, literal, or_, select

from app.models import ProductLink
from app.models.atendimento import AtendimentoFato
from app.services.atendimento import busca_texto, constantes
from app.services.atendimento import ficha as ficha_svc

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

MAX_FATOS = 8
# Do mais específico ao mais geral: a ordem sem pergunta (e o desempate com ela).
ORDEM_ESCOPO: dict[str, int] = {
    constantes.FATO_ESCOPO_ANUNCIO: 0,
    constantes.FATO_ESCOPO_PRODUTO: 1,
    constantes.FATO_ESCOPO_SKU: 2,
    constantes.FATO_ESCOPO_LOJA: 3,
    constantes.FATO_ESCOPO_MARCA: 4,
    constantes.FATO_ESCOPO_PLATAFORMA: 5,
    constantes.FATO_ESCOPO_GLOBAL: 6,
}
# Os escopos em que o fato SEM assunto vale para qualquer pergunta: o fato
# já é daquele anúncio/produto/SKU.
ESCOPOS_ESPECIFICOS = (
    constantes.FATO_ESCOPO_ANUNCIO,
    constantes.FATO_ESCOPO_PRODUTO,
    constantes.FATO_ESCOPO_SKU,
)
# Os escopos da LOJA (o que vale para a conversa inteira, sem anúncio):
# os do `chaves_ativas`.
ESCOPOS_DA_LOJA = (
    constantes.FATO_ESCOPO_GLOBAL,
    constantes.FATO_ESCOPO_PLATAFORMA,
    constantes.FATO_ESCOPO_LOJA,
)
MAX_SKUS = 20


def _campo(fato: Any, nome: str) -> Any:
    if isinstance(fato, Mapping):
        return fato.get(nome)
    return getattr(fato, nome, None)


def _instante(valor: Any) -> datetime | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    try:
        quando = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        # Validade ilegível: o lado seguro é "não vale".
        return datetime.min.replace(tzinfo=UTC)
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def valido(fato: Any, agora: datetime) -> bool:
    """O fato (`AtendimentoFato` ou dicionário) vale agora? PURA.

    `ativo`, aprovado (`aprovado_por` com texto) e dentro da validade
    (`valido_ate` vazio ou depois de `agora`).
    """
    if _campo(fato, "ativo") is not True:
        return False
    if not str(_campo(fato, "aprovado_por") or "").strip():
        return False
    ate = _instante(_campo(fato, "valido_ate"))
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=UTC)
    return ate is None or ate > agora


def _vale_agora(agora: datetime) -> Any:
    """A condição SQL de `valido`."""
    f = AtendimentoFato
    return and_(
        f.ativo.is_(True),
        f.aprovado_por.is_not(None),
        func.length(func.btrim(f.aprovado_por)) > 0,
        or_(f.valido_ate.is_(None), f.valido_ate > agora),
    )


def _recorte(plataforma: str, integration_id: UUID | None) -> list[Any]:
    """`plataforma`/`integration_id` preenchidos no fato recortam em qualquer escopo."""
    f = AtendimentoFato
    return [
        or_(f.plataforma.is_(None), f.plataforma == plataforma),
        or_(f.integration_id.is_(None), f.integration_id == integration_id)
        if integration_id is not None
        else f.integration_id.is_(None),
    ]


def _do_escopo(
    *,
    plataforma: str,
    integration_id: UUID | None,
    item_id: str | None,
    product_ids: Sequence[UUID],
    skus: Sequence[str],
    marca: str | None,
) -> Any:
    """A condição SQL "o fato é desta conversa", escopo a escopo."""
    f = AtendimentoFato
    partes: list[Any] = [
        f.escopo == constantes.FATO_ESCOPO_GLOBAL,
        and_(f.escopo == constantes.FATO_ESCOPO_PLATAFORMA, f.plataforma == plataforma),
    ]
    if integration_id is not None:
        partes.append(
            and_(f.escopo == constantes.FATO_ESCOPO_LOJA, f.integration_id == integration_id)
        )
    if item_id:
        partes.append(
            and_(
                f.escopo == constantes.FATO_ESCOPO_ANUNCIO,
                f.plataforma == plataforma,
                f.item_id == item_id,
            )
        )
    plat = ficha_svc.plataforma_do_catalogo(plataforma)
    produtos = [p for p in product_ids if p is not None]
    if produtos and integration_id is not None and plat is not None:
        vivo = [
            ProductLink.product_id == f.product_id,
            ProductLink.integration_id == integration_id,
            ProductLink.platform == plat,
            ProductLink.morto_desde.is_(None),
        ]
        if item_id:
            vivo.append(ProductLink.external_id == item_id)
        partes.append(
            and_(
                f.escopo == constantes.FATO_ESCOPO_PRODUTO,
                f.product_id.in_(produtos),
                select(ProductLink.id).where(*vivo).exists(),
            )
        )
    minusculos = sorted({s.strip().lower() for s in skus if s and s.strip()})[:MAX_SKUS]
    if minusculos:
        partes.append(
            and_(f.escopo == constantes.FATO_ESCOPO_SKU, func.lower(f.sku).in_(minusculos))
        )
    if marca:
        partes.append(and_(f.escopo == constantes.FATO_ESCOPO_MARCA, f.marca == marca))
    return or_(*partes)


def _ordem_do_escopo() -> Any:
    f = AtendimentoFato
    return case(
        *((f.escopo == escopo, ordem) for escopo, ordem in ORDEM_ESCOPO.items()),
        else_=len(ORDEM_ESCOPO),
    )


def para_o_agente(fato: Any) -> dict:
    """O fato como o agente vê: id citável, texto, escopo, assunto e fonte. PURA."""
    return {
        "id": f"fato:{_campo(fato, 'id')}",
        "texto": " ".join(str(_campo(fato, "texto") or "").split()),
        "escopo": _campo(fato, "escopo"),
        "assunto": _campo(fato, "assunto"),
        "fonte": _campo(fato, "fonte"),
    }


async def fatos_aplicaveis(
    session: AsyncSession,
    *,
    plataforma: str,
    integration_id: UUID | None,
    item_id: str | None,
    product_ids: Sequence[UUID],
    skus: Sequence[str],
    marca: str | None,
    assunto: str | None,
    pergunta: str | None,
    agora: datetime,
    limite: int = MAX_FATOS,
) -> list[dict]:
    """Os fatos que valem nesta conversa, do mais relevante ao mais geral.

    Cada um: {"id": "fato:<id>", "texto", "escopo", "assunto", "fonte"} —
    nunca `aprovado_por`. Com `pergunta`, os que casam com o texto vêm
    primeiro (pelo `ts_rank_cd`); depois (e sem `pergunta`) pelo escopo
    (anúncio > produto > sku > loja > marca > plataforma > global) e pelo id.
    """
    if limite <= 0:
        return []
    f = AtendimentoFato
    consulta = busca_texto.consulta_de(pergunta)
    casa = f.tsv.op("@@")(consulta) if consulta is not None else false()
    rank = func.ts_rank_cd(f.tsv, consulta) if consulta is not None else literal(0.0)
    conds = [
        _vale_agora(agora),
        *_recorte(plataforma, integration_id),
        _do_escopo(
            plataforma=plataforma,
            integration_id=integration_id,
            item_id=item_id,
            product_ids=product_ids,
            skus=skus,
            marca=marca,
        ),
    ]
    do_assunto = f.assunto == assunto if assunto else false()
    if assunto:
        conds.append(
            or_(
                casa,
                do_assunto,
                and_(f.assunto.is_(None), f.escopo.in_(ESCOPOS_ESPECIFICOS)),
            )
        )
    linhas = (
        await session.execute(
            select(f.id, f.texto, f.escopo, f.assunto, f.fonte)
            .where(*conds)
            .order_by(case((casa, 0), else_=1), rank.desc(), _ordem_do_escopo(), f.id)
            .limit(limite)
        )
    ).all()
    return [para_o_agente(linha._mapping) for linha in linhas]


async def chaves_ativas(
    session: AsyncSession,
    chaves: Collection[str],
    *,
    plataforma: str,
    integration_id: UUID | None,
    agora: datetime,
) -> frozenset[str]:
    """Quais destas `atendimento_fatos.chave` têm fato válido para esta loja.

    Só os escopos da LOJA (global, plataforma, loja): é o que vale para a
    conversa inteira. Usado pelo `acao_pessoa` (recusas padrão e remetente:
    com o fato, o gatilho vira orientação).
    """
    pedidas = sorted({c for c in chaves if c})
    if not pedidas:
        return frozenset()
    f = AtendimentoFato
    escopo = [
        f.escopo == constantes.FATO_ESCOPO_GLOBAL,
        and_(f.escopo == constantes.FATO_ESCOPO_PLATAFORMA, f.plataforma == plataforma),
    ]
    if integration_id is not None:
        escopo.append(
            and_(f.escopo == constantes.FATO_ESCOPO_LOJA, f.integration_id == integration_id)
        )
    achadas = await session.scalars(
        select(f.chave).where(
            f.chave.in_(pedidas),
            _vale_agora(agora),
            *_recorte(plataforma, integration_id),
            or_(*escopo),
        )
    )
    return frozenset(c for c in achadas.all() if c)


async def fato_da_chave(session: AsyncSession, chave: str, *, agora: datetime) -> dict | None:
    """O fato válido desta `chave` (semeado pela migration), no formato do agente — ou None.

    Para quem já sabe o recorte (a garantia da Charlots no `ver_garantia`:
    o fato é da marca, e a marca vem dos itens do pedido).
    """
    f = AtendimentoFato
    linha = (
        await session.execute(
            select(f.id, f.texto, f.escopo, f.assunto, f.fonte)
            .where(f.chave == chave, _vale_agora(agora))
            .limit(1)
        )
    ).first()
    return para_o_agente(linha._mapping) if linha is not None else None
