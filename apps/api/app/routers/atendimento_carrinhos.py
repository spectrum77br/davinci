"""Pós-venda › Atendimento: o cartão do CARRINHO ABANDONADO dos sites (RF9, 02/10/2026).

  GET  /api/atendimento/conversas/{id}/carrinho
       O carrinho aberto (ou o último) da conversa do lojista + os anteriores
       dele no mesmo site: lojista (nome, empresa, e-mail, WhatsApp, cidade),
       itens com o estoque ATUAL do DaVinci pelo SKU (`painel.saldo_do_item`,
       pela mesma regra do estoque do site), situação, parado desde,
       recuperado em, a taxa de recuperação do site (30 dias) e a saúde da
       leitura do site (a rota ainda não publicada aparece aqui também).
       Lido do banco, nunca do site na hora.
  POST /api/atendimento/carrinhos/{id}/resolvido   {"motivo": "..."?}
       "Marcar como resolvido": situação `resolvido`, `tratado_por`/
       `tratado_em`, mensagem do sistema na conversa, a conversa sai da fila
       e a etiqueta é recalculada. Idempotente; o carrinho já recuperado ou
       não recuperado → 409 `carrinho_encerrado`. Nada é mandado ao lojista.

A MESMA TRAVA do router do atendimento (`_so_admin`), a mesma permissão
(`_view` para ler, `_edit` para resolver) e o escopo por equipe (a conversa
sem integração só aparece para quem vê tudo — `_conversa_ou_404`; o
carrinho, pela conversa dele). Router à parte; o `main.py` já o inclui.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.team_scope import resolve_team_scope
from app.models import AtendimentoCanal, AtendimentoCarrinho, AtendimentoConversa, User
from app.routers.atendimento import (
    _conversa_ou_404,
    _edit,
    _no_escopo,
    _nome,
    _so_admin,
    _uuid_ou_404,
    _view,
)
from app.services.atendimento import carrinhos
from app.services.atendimento.constantes import CANAL_CARRINHO

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


class LojistaOut(BaseModel):
    # O retrato da última leitura do site (o site é a verdade).
    nome: str | None = None
    empresa: str | None = None
    email: str | None = None
    telefone: str | None = None
    cidade: str | None = None
    estado: str | None = None
    # O cadastro no site: aprovado | em_analise | recusado…
    status: str | None = None
    cnpj: str | None = None


class SkuEstoqueOut(BaseModel):
    sku: str
    existe: bool
    saldo: int | None = None
    nome: str | None = None
    lote: str | None = None
    kit: bool = False
    atualizado_em: str | None = None


class EstoqueItemOut(BaseModel):
    # Soma dos SKUs da escolha (negativo conta 0); None = desconhecido.
    disponivel: int | None
    # A quantidade das linhas do carrinho que dividem algum SKU com esta.
    demanda: int
    # ok | acima | zero | desconhecido | sem_mapa
    status: str
    texto: str
    # Peças do SKU exato em OUTROS lotes de venda (para o "há N em outros lotes").
    outros_lotes: int = 0
    falhou: bool = False
    skus: list[SkuEstoqueOut] = []


class ItemCarrinhoOut(BaseModel):
    produto_id: str | None = None
    titulo: str | None = None
    cor: str | None = None
    cor_rotulo: str | None = None
    quantidade: int
    skus: list[str] = []
    url: str | None = None
    imagem: str | None = None
    preco: float | None = None
    estoque: EstoqueItemOut | None = None


class ItemEnviadoOut(BaseModel):
    produto_id: str | None = None
    cor: str | None = None
    quantidade: int = 0
    skus: list[str] = []


class CarrinhoOut(BaseModel):
    id: UUID
    site: str
    site_nome: str
    site_url: str | None
    conversa_id: UUID | None
    # aberto | recuperado | nao_recuperado | resolvido
    situacao: str
    situacao_rotulo: str
    # finalizado_whatsapp | esvaziado | prazo | marcado_resolvido
    motivo_fim: str | None
    motivo_fim_rotulo: str | None
    lojista_id: str
    lojista: LojistaOut
    itens: list[ItemCarrinhoOut]
    # Quantos itens o carrinho tem (a lista pode vir cortada no teto da tela).
    itens_total: int
    quantidade_total: int
    # Soma de preço × quantidade — só quando todo item tem preço.
    valor_total: float | None
    # Itens com o estoque do DaVinci zerado ou abaixo do que o carrinho pede.
    itens_sem_estoque: int
    parado_desde: datetime
    detectado_em: datetime | None
    visto_em: datetime | None
    # Sumiu da lista do site sem evento (o lojista voltou a mexer, ou saiu da
    # janela): continua aberto até o evento ou o prazo.
    fora_da_lista_desde: datetime | None
    # O site disse que o lojista voltou a mexer no carrinho (lista `ativos`):
    # a hora da última mexida. Também continua aberto.
    mexido_em: datetime | None = None
    # Até quando a finalização conta como "recuperado" (detectado + 7 dias).
    prazo_em: datetime | None
    encerrado_em: datetime | None
    recuperado_em: datetime | None
    # O que foi na mensagem do WhatsApp (o evento `finalizado` do site).
    itens_enviados: list[ItemEnviadoOut] = []
    restantes: int | None = None
    tratado_em: datetime | None = None
    tratado_por_nome: str | None = None
    resolvido_motivo: str | None = None
    pode_resolver: bool


class CarrinhoResumoOut(BaseModel):
    id: UUID
    situacao: str
    situacao_rotulo: str
    motivo_fim_rotulo: str | None
    quantidade_total: int
    itens_total: int
    valor_total: float | None
    parado_desde: datetime
    detectado_em: datetime | None
    encerrado_em: datetime | None
    recuperado_em: datetime | None


class TaxaOut(BaseModel):
    dias: int
    detectados: int
    abertos: int
    recuperados: int
    nao_recuperados: int
    resolvidos: int
    encerrados: int
    # recuperados / encerrados; None sem encerrado no período.
    taxa: float | None


class LeituraSiteOut(BaseModel):
    # ok | novo | sem_escopo | sem_endpoint | desligado | erro
    status: str
    ultimo_ok_em: datetime | None
    ultimo_erro_em: datetime | None
    # Texto de operação (HTTP, rota, token não confere) — nunca dado de lojista.
    ultimo_erro: str | None


class CarrinhoDaConversaOut(BaseModel):
    carrinho: CarrinhoOut | None
    anteriores: list[CarrinhoResumoOut]
    taxa: TaxaOut | None
    leitura: LeituraSiteOut | None
    # A hora em que o estoque do DaVinci foi lido (agora: nunca guardado).
    estoque_lido_em: datetime
    aviso: str


class ResolvidoIn(BaseModel):
    # O porquê (opcional, texto da equipe): fica em `dados.resolvido_motivo`
    # — não vai para o lojista nem para o site.
    motivo: str | None = Field(default=None, max_length=300)


class ResolvidoOut(BaseModel):
    carrinho: CarrinhoOut


async def _nomes(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str | None]:
    validos = [i for i in ids if i is not None]
    if not validos:
        return {}
    usuarios = (await session.execute(select(User).where(User.id.in_(validos)))).scalars().all()
    return {u.id: _nome(u) for u in usuarios}


def _resumo(c: AtendimentoCarrinho) -> CarrinhoResumoOut:
    tela = carrinhos.para_tela(c, estoque=None)
    return CarrinhoResumoOut(
        id=tela["id"],
        situacao=tela["situacao"],
        situacao_rotulo=tela["situacao_rotulo"],
        motivo_fim_rotulo=tela["motivo_fim_rotulo"],
        quantidade_total=tela["quantidade_total"],
        itens_total=tela["itens_total"],
        valor_total=tela["valor_total"],
        parado_desde=tela["parado_desde"],
        detectado_em=tela["detectado_em"],
        encerrado_em=tela["encerrado_em"],
        recuperado_em=tela["recuperado_em"],
    )


async def _saida(session: AsyncSession, c: AtendimentoCarrinho) -> CarrinhoOut:
    """O carrinho inteiro, com o estoque do DaVinci lido agora."""
    estoque = await carrinhos.estoque_dos_itens(session, list(c.itens or []))
    nomes = await _nomes(session, {c.tratado_por})
    return CarrinhoOut(**carrinhos.para_tela(c, estoque=estoque, nomes=nomes))


async def _leitura(session: AsyncSession, canal_id: UUID | None) -> LeituraSiteOut | None:
    if canal_id is None:
        return None
    canal = await session.get(AtendimentoCanal, canal_id)
    if canal is None:
        return None
    return LeituraSiteOut(
        status=canal.status,
        ultimo_ok_em=canal.ultimo_ok_em,
        ultimo_erro_em=canal.ultimo_erro_em,
        ultimo_erro=canal.ultimo_erro,
    )


@router.get("/conversas/{conversa_id}/carrinho", response_model=CarrinhoDaConversaOut)
async def carrinho_da_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> CarrinhoDaConversaOut:
    """O cartão do carrinho: o aberto (ou o último), os anteriores e a taxa do site."""
    agora = datetime.now(UTC)
    scope = await resolve_team_scope(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    vazio = CarrinhoDaConversaOut(
        carrinho=None,
        anteriores=[],
        taxa=None,
        leitura=None,
        estoque_lido_em=agora,
        aviso=carrinhos.AVISO_SEM_ENVIO,
    )
    if conversa.canal != CANAL_CARRINHO:
        return vazio
    atual, anteriores = await carrinhos.carrinho_da_conversa(session, conversa)
    leitura = await _leitura(session, conversa.canal_id)
    if atual is None:
        vazio.leitura = leitura
        return vazio
    return CarrinhoDaConversaOut(
        carrinho=await _saida(session, atual),
        anteriores=[_resumo(c) for c in anteriores],
        taxa=TaxaOut(**await carrinhos.taxa_do_site(session, atual.site, agora=agora)),
        leitura=leitura,
        estoque_lido_em=agora,
        aviso=carrinhos.AVISO_SEM_ENVIO,
    )


async def _carrinho_ou_404(
    session: AsyncSession, carrinho_id: str, user: User
) -> AtendimentoCarrinho:
    """O carrinho, se a pessoa vê a conversa dele (sem integração: só quem vê tudo)."""
    uid = _uuid_ou_404(carrinho_id, "carrinho_nao_encontrado")
    c = await session.get(AtendimentoCarrinho, uid)
    scope = await resolve_team_scope(session, user)
    conversa = (
        await session.get(AtendimentoConversa, c.conversa_id)
        if c is not None and c.conversa_id
        else None
    )
    integration_id = conversa.integration_id if conversa is not None else None
    if c is None or not _no_escopo(scope, integration_id):
        raise HTTPException(404, detail={"code": "carrinho_nao_encontrado"})
    return c


@router.post("/carrinhos/{carrinho_id}/resolvido", response_model=ResolvidoOut)
async def marcar_resolvido(
    carrinho_id: str,
    body: ResolvidoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ResolvidoOut:
    """Marcar como resolvido: sai de "aberto", a conversa sai da fila e a etiqueta volta.

    Idempotente (o já resolvido devolve 200 igual). O recuperado/não
    recuperado → 409 `carrinho_encerrado`. Nada vai para o lojista nem para
    o site.
    """
    c = await _carrinho_ou_404(session, carrinho_id, user)
    try:
        mudou = await carrinhos.marcar_resolvido(session, c, user=user, motivo=body.motivo)
    except carrinhos.AcaoRecusada as e:
        raise HTTPException(409, detail={"code": e.code, "detail": e.detail}) from e
    await session.commit()
    await session.refresh(c)
    if mudou:
        logger.info(
            "atendimento_carrinho_resolvido",
            carrinho_id=str(c.id),
            conversa_id=str(c.conversa_id) if c.conversa_id else None,
            user_id=str(user.id),
        )
    return ResolvidoOut(carrinho=await _saida(session, c))
