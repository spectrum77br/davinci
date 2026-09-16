"""Marketing › Legendas — biblioteca de variações por marca/produto (16/09/2026).

Eduardo, 16/09/2026: "com base no produto do criativo e se não, cai num
padrão da marca… também tem que sair quando o post sai automático, pois a
ideia futuramente é deixar todos postando automático". Este arquivo é a BORDA
dessa biblioteca: CRUD das variações, prévia renderizada com dados reais e a
consulta que o modal de publicar usa no lugar do antigo pré-preenchimento
pelo `roteiro` — que é prompt de geração de vídeo em inglês, nunca legenda
(dos 2 Reels já publicados, um saiu sem legenda nenhuma).

A CASCATA — legenda da postagem → legenda do criativo → modelo do PRODUTO →
modelo da MARCA → recusa — e o rodízio entre variações moram em
`services/marketing/legenda.py`, nunca aqui. O `/resolvida` chama a MESMA
função que o `agendar()` chama: se a borda tivesse regra própria, o texto que
o operador aprova no modal deixaria de ser byte a byte o que vai pro
Instagram, e post não se edita.

Permissão: `marketing_criativos` (view pra ler, edit pra escrever) — é a
mesma tela dos criativos. Diferente do criativo, a variação NÃO tem equipe:
é cadastro da marca, como os padrões de e-mail. O escopo de equipe aparece só
no `/resolvida`, que parte de um criativo e herda a trava dele.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    Marca,
    MarketingCreative,
    MarketingLegendaModelo,
    Product,
    RedeSocial,
    User,
)
from app.routers.marketing_creatives import _ensure_equipe
from app.schemas.marketing_legendas import (
    LegendaModeloCreate,
    LegendaModeloOut,
    LegendaModeloPatch,
    LegendaPreviewIn,
    LegendaPreviewOut,
    LegendaResolvidaOut,
)
from app.services.marketing import legenda as svc

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing/legendas", tags=["marketing_legendas"])

_view = require_permission("marketing_criativos", "view")
_edit = require_permission("marketing_criativos", "edit")


def _template_422(e: svc.TemplateInvalidoError) -> HTTPException:
    """Código estável + o erro do Jinja (molde do routers/email_padroes.py):
    o front tem uma tradução só pra "placeholder_desconhecido" e afins."""
    msg = str(e)
    code = msg.split(":", 1)[0].strip() or "template_invalido"
    return HTTPException(422, detail={"code": code, "erro": msg[:300]})


def _out(
    m: MarketingLegendaModelo,
    *,
    marca_nome: str | None = None,
    product_nome: str | None = None,
    product_sku: str | None = None,
) -> LegendaModeloOut:
    out = LegendaModeloOut.model_validate(m)
    out.marca_nome = marca_nome or ""
    out.product_nome = product_nome
    out.product_sku = product_sku
    return out


async def _marca_or_404(session: AsyncSession, marca_id: UUID) -> Marca:
    m = (await session.execute(select(Marca).where(Marca.id == marca_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    return m


async def _produto_or_404(session: AsyncSession, product_id: UUID | None) -> Product | None:
    """Produto do vínculo, quando a variação é de um produto.

    404 em vez de gravar calado: `product_id` não vem de digitação, vem do
    select da tela (ou do `product_id` já resolvido no criativo). Um id que
    não existe é bug de integração, e a variação ficaria invisível na
    cascata — presa a um produto que ninguém acha.
    """
    if product_id is None:
        return None
    p = (
        await session.execute(select(Product).where(Product.id == product_id))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, detail={"code": "product_not_found"})
    return p


async def _conta_exemplo(session: AsyncSession, marca: Marca) -> RedeSocial | None:
    """Uma conta da marca só pra prévia preencher `{{ instagram }}`.

    Na prévia ainda não existe post, então não existe conta escolhida — e
    deixar o placeholder vazio faria a prévia mentir justamente no que ela
    promete (texto e tamanho reais). Instagram primeiro porque é a conta que
    o `{{ instagram }}` nomeia; sem nenhuma conta, o placeholder sai vazio
    mesmo, e a tela mostra o buraco.
    """
    return (
        await session.execute(
            select(RedeSocial)
            .where(
                RedeSocial.marca_id == marca.id,
                RedeSocial.conta.is_not(None),
                RedeSocial.ativo.is_(True),
            )
            .order_by(RedeSocial.plataforma != "instagram", RedeSocial.conta)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _modelo_or_404(session: AsyncSession, modelo_id: UUID) -> MarketingLegendaModelo:
    m = (
        await session.execute(
            select(MarketingLegendaModelo).where(MarketingLegendaModelo.id == modelo_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "legenda_modelo_not_found"})
    return m


async def _out_uma(session: AsyncSession, m: MarketingLegendaModelo) -> LegendaModeloOut:
    """Uma variação com os campos que vêm de fora da tabela (marca e produto)."""
    marca_nome = (
        await session.execute(select(Marca.nome).where(Marca.id == m.marca_id))
    ).scalar_one_or_none()
    nome = sku = None
    if m.product_id is not None:
        linha = (
            await session.execute(
                select(Product.name, Product.sku).where(Product.id == m.product_id)
            )
        ).first()
        if linha is not None:
            nome, sku = linha
    return _out(m, marca_nome=marca_nome, product_nome=nome, product_sku=sku)


@router.get("", response_model=list[LegendaModeloOut])
async def list_modelos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
    marca_id: UUID | None = Query(None),
    product_id: UUID | None = Query(None),
) -> list[LegendaModeloOut]:
    """Variações cadastradas, pra aba de legendas da marca.

    Ordem: padrão da marca PRIMEIRO (`product_id IS NULL`), depois as de
    produto — é a ordem da cascata lida de trás pra frente, e é assim que o
    operador confere "o que sai quando o criativo não tem produto".
    """
    stmt = (
        select(MarketingLegendaModelo, Marca.nome, Product.name, Product.sku)
        .join(Marca, Marca.id == MarketingLegendaModelo.marca_id)
        # OUTER: `product_id` NULL é o padrão da marca, a linha mais comum.
        .outerjoin(Product, Product.id == MarketingLegendaModelo.product_id)
        .order_by(
            Marca.nome,
            MarketingLegendaModelo.product_id.is_(None).desc(),
            MarketingLegendaModelo.created_at,
        )
    )
    if marca_id is not None:
        stmt = stmt.where(MarketingLegendaModelo.marca_id == marca_id)
    if product_id is not None:
        stmt = stmt.where(MarketingLegendaModelo.product_id == product_id)
    rows = (await session.execute(stmt)).all()
    return [
        _out(m, marca_nome=marca_nome, product_nome=nome, product_sku=sku)
        for m, marca_nome, nome, sku in rows
    ]


@router.get("/resolvida", response_model=LegendaResolvidaOut)
async def legenda_resolvida(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    creative_id: Annotated[UUID, Query()],
    rede_social_id: UUID | None = Query(None),
    file_id: UUID | None = Query(None),
) -> LegendaResolvidaOut:
    """O que a cascata escolheria AGORA — é o que o modal mostra no lugar do
    `roteiro`, já RENDERIZADO (com marca, produto, WhatsApp e @ da conta).

    A conta entra na conversa por dois motivos: `{{ instagram }}` é o @ dela e
    o rodízio é POR CONTA (a variação escolhida é a menos usada recentemente
    ali). Por isso o texto pode mudar de conta pra conta no mesmo clique — e
    é de propósito: mesmo texto + mesmo formato + API + intervalo cravado é o
    retrato do que o detector de automação procura.

    `rede_social_id` é OPCIONAL porque o modal abre antes de o operador marcar
    a conta, e é aí que ele lê a legenda pela primeira vez. Sem conta o rodízio
    não gira e `{{ instagram }}` sai vazio — mas o texto já aparece, que é
    muito melhor que o campo em branco com aviso de erro. Quem vale de verdade
    é a resolução do `agendar()`, uma por conta.

    Aqui NÃO se grava nada: o snapshot é do `agendar()`, uma vez só, no
    momento em que a postagem nasce. Chamar isto duas vezes pode devolver
    variações diferentes, e é justamente por isso que o que vale é o snapshot.
    """
    creative = (
        await session.execute(select(MarketingCreative).where(MarketingCreative.id == creative_id))
    ).scalar_one_or_none()
    if creative is None:
        raise HTTPException(404, detail={"code": "creative_not_found"})
    _ensure_equipe(user, creative)
    # O arquivo não muda a legenda (hoje a cascata é do criativo), mas o modal
    # sempre manda o dele e o serviço recebe: conferir aqui é o que denuncia a
    # tela aberta há meia hora, com um vídeo que já foi trocado ou apagado.
    arquivo = None
    if file_id is not None:
        arquivo = next((f for f in creative.files if f.id == file_id), None)
        if arquivo is None:
            raise HTTPException(404, detail={"code": "file_not_found"})
    rede = None
    if rede_social_id is not None:
        rede = (
            await session.execute(select(RedeSocial).where(RedeSocial.id == rede_social_id))
        ).scalar_one_or_none()
        if rede is None:
            raise HTTPException(404, detail={"code": "rede_social_not_found"})
        # A conta tem que ser DA MARCA do criativo — a mesma regra que o
        # `agendar` aplica (RoboError "conta_de_outra_marca"). Sem isto a tela
        # mostraria uma legenda com o @ de outra marca, renderizada e
        # convincente, que o backend recusaria na hora de gravar: o operador
        # confia no que leu e leva um erro que não explica nada.
        if creative.marca_id is None or rede.marca_id != creative.marca_id:
            raise HTTPException(409, detail={"code": "conta_de_outra_marca"})

    try:
        r = await svc.resolver(session, creative=creative, file=arquivo, rede=rede)
    except svc.TemplateInvalidoError as e:
        # Variação salva com template válido pode ficar inválida depois (um
        # placeholder que sai da allowlist). Vira 422 com código, nunca 500:
        # o modal precisa dizer QUAL variação consertar.
        raise _template_422(e) from e
    return LegendaResolvidaOut(
        texto=r.texto,
        origem=r.origem,
        total_variacoes=r.total_variacoes,
        indice=r.indice,
    )


@router.post("/preview", response_model=LegendaPreviewOut)
async def preview_legenda(
    body: LegendaPreviewIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> LegendaPreviewOut:
    """Renderiza um rascunho com dados REAIS da marca (e do produto, quando
    há), sem gravar — é o que o operador lê antes de salvar a variação.

    Dados reais, e não valores de exemplo, porque a armadilha desta tela é de
    concordância: "uso do {{ produto }}" vira "uso do Cafeteira". Com o nome
    de verdade na frente, quem escreve vê o erro na hora.

    Sob `view` (como a prévia dos padrões de e-mail): ler o próprio rascunho
    não é escrever — quem não tem `edit` até consegue a prévia, mas o POST
    seguinte recusa.
    """
    marca = await _marca_or_404(session, body.marca_id)
    produto = await _produto_or_404(session, body.product_id)
    contexto = svc.placeholders_de(
        marca, produto.name if produto is not None else "", await _conta_exemplo(session, marca)
    )
    try:
        texto = svc.renderizar(body.texto, contexto)
    except svc.TemplateInvalidoError as e:
        raise _template_422(e) from e
    return LegendaPreviewOut(texto=texto, tamanho=len(texto))


@router.post("", response_model=LegendaModeloOut, status_code=201)
async def criar_modelo(
    body: LegendaModeloCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> LegendaModeloOut:
    """Cadastra UMA variação. Repetir a mesma chave (marca, produto) é o uso
    normal — é o rodízio que precisa de várias; não há unicidade nenhuma."""
    marca = await _marca_or_404(session, body.marca_id)
    await _produto_or_404(session, body.product_id)
    m = MarketingLegendaModelo(
        id=uuid4(),
        marca_id=marca.id,
        product_id=body.product_id,
        texto=body.texto,
        ativo=body.ativo,
        created_by=user.id,
    )
    session.add(m)
    await session.commit()
    logger.info(
        "marketing_legenda_modelo_criado",
        modelo_id=str(m.id),
        marca_id=str(marca.id),
        product_id=str(body.product_id) if body.product_id else None,
        user_id=str(user.id),
    )
    return await _out_uma(session, m)


@router.patch("/{modelo_id}", response_model=LegendaModeloOut)
async def patch_modelo(
    modelo_id: UUID,
    body: LegendaModeloPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> LegendaModeloOut:
    """Editar aqui NÃO reescreve post nenhum: a legenda que foi pro ar está
    em snapshot na `marketing_postagens.legenda`. Vale da próxima vez."""
    m = await _modelo_or_404(session, modelo_id)
    data = body.model_dump(exclude_unset=True)
    if "product_id" in data:
        # null explícito é comando: "esta variação passa a ser o padrão da
        # marca". Por isso `exclude_unset` e não `exclude_none`.
        await _produto_or_404(session, data["product_id"])
        m.product_id = data["product_id"]
    if "texto" in data:
        m.texto = data["texto"]
    if "ativo" in data:
        m.ativo = data["ativo"]
    await session.commit()
    await session.refresh(m)  # updated_at é onupdate no SQL
    logger.info(
        "marketing_legenda_modelo_editado",
        modelo_id=str(m.id),
        campos=sorted(data),
        user_id=str(user.id),
    )
    return await _out_uma(session, m)


@router.delete("/{modelo_id}")
async def delete_modelo(
    modelo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> dict[str, str]:
    """DELETE aqui apaga de verdade — e pode.

    O que já foi publicado não depende desta linha: a postagem guarda o texto
    em snapshot e o `legenda_modelo_id` é FK SET NULL. Some só o histórico de
    rodízio DESTA variação, que sem a variação não serve a nada. Quem quer
    parar de usar sem perder o texto usa `ativo=false`.
    """
    m = await _modelo_or_404(session, modelo_id)
    await session.delete(m)
    await session.commit()
    logger.info(
        "marketing_legenda_modelo_apagado", modelo_id=str(modelo_id), user_id=str(user.id)
    )
    return {"status": "deleted"}
