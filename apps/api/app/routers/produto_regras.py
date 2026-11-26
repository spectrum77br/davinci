"""Produtos 2 › cadastro do produto: Margem mín, Condição especial e DUIMP padrão (M-74).

Pedido M-74 do Harry (26/11/2026). Os três valores passam a poder ser POR PRODUTO; em branco, o
produto usa o do segmento (a categoria de venda), como sempre. Guardados no DaVinci (migration
0516, models/produto_regras.py), pela numeração sem a tag de envio (`sku_base`; a tag é a da
lista "Tag de envio" de Cadastros › Produtos): o mesmo valor vale para dg019.ra, dg019.sp…; o kit
tem o seu; b001.12 e b001.24 (tamanhos) são produtos diferentes.

Quem chama é a tela do ERP (Produtos 2), com o login da pessoa (`credentials: 'same-origin'`).
São rotas do DaVinci: não passam pela trava do modo Consulta do ERP, então funcionam nele.

- GET  /api/produto-regras/{sku}: o que está gravado, o que o produto herda quando em branco e
  `pode_editar` (só administrador). Qualquer pessoa logada lê (o ERP já é só de administrador).
- PUT  /api/produto-regras/{sku}: só administrador. Grava só os campos enviados; vazio (null ou
  "") volta a herdar. Margem em FRAÇÃO (0,12 = 12%), de 0 a 1; DUIMP com as regras do segmento
  (até 75 letras, sem "|").
- POST   /api/produto-regras/{sku}/condicoes: só administrador. Período (data do pedido) e piso.
- DELETE /api/produto-regras/{sku}/condicoes/{id}: só administrador.

M-73 (26/11/2026): o Centro de custo do produto (`centro_custo_id`, migration 0518) entra no
mesmo GET/PUT. Vazio = o departamento do produto, calculado e não copiado: o da linha da Tabela de
Preços; sem linha, o da categoria. Por isso todas as rotas aceitam, no endereço, `linha` (o
`pricing_id` que o ERP acha para o SKU) e `categoria` (a categoria do produto no ERP): a resposta
traz o centro herdado e a linha (categoria de venda atual e as opções do menu, que o ERP troca
pela rota da coluna "Tabela" da Tabela de Preços). O PUT só grava o centro escolhido; a categoria
de venda e a tag NÃO passam por aqui.

O Histórico registra quem mudou, o antes e o depois pelo gatilho do banco (as duas tabelas são
de negócio). Os nomes que aparecem lá ficam em historico/nomes.py.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_admin
from app.models import CentroCusto, ProdutoCondicaoEspecial, ProdutoRegra, User, UserRole
from app.schemas.segments import _limpar_duimp_padrao
from app.services import nf_emissao_gerar as nf
from app.services import produto_regras as svc
from app.services.sku_tag_envio import tags_de_envio

router = APIRouter(prefix="/api/produto-regras", tags=["produto_regras"])

_SP = ZoneInfo("America/Sao_Paulo")

_MSG_DUIMP = {
    "duimp_padrao_sem_barra": 'A DUIMP padrão não pode ter "|" (ele separa as linhas da etiqueta).',
    "duimp_padrao_muito_longo": "A DUIMP padrão cabe em até 75 letras (é o que cabe na etiqueta).",
    "duimp_padrao_caractere_invalido": (
        "A DUIMP padrão tem um caractere que a etiqueta não imprime (emoji ou símbolo)."
    ),
}


def _hoje() -> date:
    return datetime.now(_SP).date()


def _erro(code: str, message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


async def _base(session: AsyncSession, sku: str) -> str:
    """A chave do produto (sem a tag de envio). Recusa código vazio, longo demais, com peça vazia
    ("a++b") ou com peça que é só a tag (".ra")."""
    texto = (sku or "").strip()
    tags = await tags_de_envio(session)
    base = svc.sku_base(texto, tags)
    pecas = base.split("+")
    if not texto or len(texto) > svc.SKU_MAX or not base or any(not p or p in tags for p in pecas):
        raise _erro("sku_invalido", "Código do produto inválido.")
    return base


def _margem(v: Any) -> Decimal | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise _erro("margem_invalida", "A margem mínima precisa ser um número.")
    try:
        d = Decimal(str(v).strip().replace(",", "."))
    except InvalidOperation as e:
        raise _erro("margem_invalida", "A margem mínima precisa ser um número.") from e
    if not d.is_finite() or d < 0 or d > 1:
        raise _erro("margem_invalida", "A margem mínima vai de 0 a 100%.")
    return d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _piso(v: Any) -> Decimal | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise _erro("piso_invalido", "A margem da condição precisa ser um número.")
    try:
        d = Decimal(str(v).strip().replace(",", "."))
    except InvalidOperation as e:
        raise _erro("piso_invalido", "A margem da condição precisa ser um número.") from e
    if not d.is_finite() or d < -1 or d > 1:
        raise _erro("piso_invalido", "A margem da condição vai de -100% a 100%.")
    return d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _duimp(v: Any) -> str | None:
    try:
        return _limpar_duimp_padrao(v)
    except ValueError as e:
        code = str(e)
        raise _erro(code, _MSG_DUIMP.get(code, "DUIMP padrão inválida.")) from e


def _data(v: Any, campo: str) -> date:
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError) as e:
        raise _erro("periodo_invalido", f"Informe a data {campo} do período.") from e


async def _centro(session: AsyncSession, v: Any) -> UUID | None:
    """O centro de custo escolhido (id de Cadastros › Centro de custo); vazio = volta ao
    departamento do produto."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    centro_id = svc.uuid_ou_none(v) if isinstance(v, str) else None
    if centro_id is None or await session.get(CentroCusto, centro_id) is None:
        raise _erro(
            "centro_custo_invalido",
            "Centro de custo não encontrado: ele pode ter sido excluído em Cadastros › Centro "
            "de custo. Feche e abra o produto de novo.",
        )
    return centro_id


def _f(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


# M-73: o que a tela do ERP sabe do produto e o DaVinci não (o endereço de todas as rotas aceita).
# A linha vem como texto: um id que não é uuid (cópia antiga do ERP) vale como "sem linha", e o
# resto da resposta (Margem mín, DUIMP…) não deixa de vir por causa dele.
Linha = Annotated[
    str | None, Query(max_length=64, description="pricing_id da linha da Tabela de Preços")
]
Categoria = Annotated[str | None, Query(max_length=120, description="categoria do produto")]


async def _saida(
    session: AsyncSession,
    sku: str,
    base: str,
    user: User,
    linha: str | None = None,
    categoria: str | None = None,
) -> dict:
    regra = await session.get(ProdutoRegra, base, populate_existing=True)
    herdada = await svc.margem_herdada(session, sku)
    chave = sku.strip().lower()
    duimp_seg = (await nf._texto_segmento_por_sku(session, {chave})).get(chave)
    pecas = nf._pecas_do_sku(chave)
    importacao = await nf._duimp_da_importacao(session, set(pecas))
    hoje = _hoje()
    conds = await svc.condicoes(session, base)
    a_linha = await svc.linha_da_tabela(session, svc.uuid_ou_none(linha) if linha else None)
    herdado = await svc.centro_herdado(session, a_linha, categoria)
    escolhido = (
        await session.get(CentroCusto, regra.centro_custo_id)
        if regra is not None and regra.centro_custo_id is not None
        else None
    )
    return {
        "sku": sku.strip(),
        "sku_base": base,
        "margem_min": _f(regra.margem_min) if regra else None,
        "margem_min_herdada": float(herdada.valor),
        "margem_min_origem": herdada.origem,
        "duimp_padrao": regra.duimp_padrao if regra else None,
        "duimp_herdada": duimp_seg,
        "duimp_importacao": nf._juntar_duimps(importacao.get(p) for p in pecas),
        "condicoes": [svc.condicao_out(c, hoje) for c in conds],
        "condicao_resumo": svc.resumo_condicao(conds, hoje),
        # M-73: o centro escolhido (vazio = o herdado, que vem sempre junto, como a margem).
        "centro_custo_id": str(escolhido.id) if escolhido is not None else None,
        "centro_custo": escolhido.nome if escolhido is not None else None,
        "centro_custo_herdado": (
            {"id": str(herdado.centro.id), "nome": herdado.centro.nome}
            if herdado.centro is not None
            else None
        ),
        "centro_custo_origem": herdado.origem,
        "linha": svc.linha_out(a_linha),
        "pode_editar": user.role == UserRole.ADMIN,
    }


@router.get("/{sku}")
async def ler(
    sku: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    linha: Linha = None,
    categoria: Categoria = None,
) -> dict:
    return await _saida(session, sku, await _base(session, sku), user, linha, categoria)


@router.put("/{sku}")
async def gravar(
    sku: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
    corpo: Annotated[dict[str, Any], Body()],
    linha: Linha = None,
    categoria: Categoria = None,
) -> dict:
    base = await _base(session, sku)
    sobra = set(corpo) - {"margem_min", "duimp_padrao", "centro_custo_id"}
    if sobra:
        raise _erro("campo_desconhecido", f"Campo que não existe aqui: {', '.join(sorted(sobra))}.")
    novos: dict[str, Any] = {}
    if "margem_min" in corpo:
        novos["margem_min"] = _margem(corpo["margem_min"])
    if "duimp_padrao" in corpo:
        novos["duimp_padrao"] = _duimp(corpo["duimp_padrao"])
    if "centro_custo_id" in corpo:
        novos["centro_custo_id"] = await _centro(session, corpo["centro_custo_id"])
    regra = await session.get(ProdutoRegra, base)
    if regra is None:
        # Linha nova só com valor de verdade: apagar o que não existe não cria nada.
        if any(v is not None for v in novos.values()):
            session.add(ProdutoRegra(sku_base=base, atualizado_por=user.id, **novos))
            await session.commit()
    else:
        mudou = {k: v for k, v in novos.items() if getattr(regra, k) != v}
        if mudou:
            for k, v in mudou.items():
                setattr(regra, k, v)
            regra.atualizado_por = user.id
            regra.atualizado_em = func.now()
            await session.commit()
    return await _saida(session, sku, base, user, linha, categoria)


@router.post("/{sku}/condicoes", status_code=status.HTTP_201_CREATED)
async def criar_condicao(
    sku: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
    corpo: Annotated[dict[str, Any], Body()],
    linha: Linha = None,
    categoria: Categoria = None,
) -> dict:
    base = await _base(session, sku)
    de = _data(corpo.get("pedido_de"), "inicial")
    ate = _data(corpo.get("pedido_ate"), "final")
    if ate < de:
        raise _erro("periodo_invalido", "A data final não pode ser antes da inicial.")
    session.add(
        ProdutoCondicaoEspecial(
            sku_base=base,
            pedido_de=de,
            pedido_ate=ate,
            piso=_piso(corpo.get("piso")),
            criado_por=user.id,
        )
    )
    await session.commit()
    return await _saida(session, sku, base, user, linha, categoria)


@router.delete("/{sku}/condicoes/{condicao_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apagar_condicao(
    sku: str,
    condicao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_admin)],
) -> Response:
    base = await _base(session, sku)
    cond = (
        await session.execute(
            select(ProdutoCondicaoEspecial).where(
                ProdutoCondicaoEspecial.id == condicao_id,
                ProdutoCondicaoEspecial.sku_base == base,
            )
        )
    ).scalar_one_or_none()
    if cond is None:
        raise _erro("condicao_nao_encontrada", "Esta condição já foi removida.", 404)
    await session.delete(cond)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
