"""Pós-venda › Atendimento: o cartão da reclamação (RF2, 01/10/2026).

  GET /api/atendimento/conversas/{id}/reclamacoes
  GET /api/atendimento/conversas/{id}/reclamacoes/{rid}/detalhe        (18/11/2026)
  GET /api/atendimento/conversas/{id}/reclamacoes/{rid}/motivos-recusa (18/11/2026)
  POST /api/atendimento/conversas/{id}/reclamacoes/{rid}/responder     (18/11/2026)

As reclamações, mediações e devoluções da PLATAFORMA ligadas à conversa —
pela `conversa_id` que o leitor gravou OU pelo pedido da conversa (a conversa
do pack e a conversa `reclamacao` do mesmo pedido mostram o mesmo cartão).
Abertas primeiro, prazo mais curto antes; depois as encerradas. Quem lê e
grava é `services/atendimento/reclamacoes.py` (ML) e
`reclamacoes_devolucoes.py` (Shopee/TikTok, pela Logística).

SÓ LEITURA: nenhuma ação na plataforma sai daqui (aceitar devolução, oferecer
solução e pedir mediação ficam para depois, com confirmação e auditoria). O
"Abrir na plataforma" é um link.

A DEVOLUÇÃO DO TIKTOK INTEIRA (18/11/2026, `services/atendimento/
devolucao_detalhe.py`): o `/detalhe` lê na hora no TikTok o caso e a linha do
tempo (valores, a volta, o produto, o texto, as fotos e o vídeo do comprador)
para o bloco da aba Pedido — só GET, cache curto, nada do comprador no banco.
O `/responder` é a primeira AÇÃO na plataforma daqui (o dono: "se der p eles
responder a devolução por ali tbm" → tudo, inclusive o pacote): aprovar ou
recusar o pedido, confirmar ou recusar o pacote. É de quem responde
(`ROTAS_DE_QUEM_RESPONDE`), nas lojas da pessoa, atrás de
`ATENDIMENTO_DEVOLUCAO_ACOES_ATIVA`, com o caso relido antes e uma nota interna
com quem fez.

A MESMA TRAVA de acesso do router do atendimento (`_so_admin`: na
observação, toda a equipe lê e só ATENDIMENTO_USUARIOS mexe) e a mesma
permissão de leitura (`_view`) e escopo por equipe (`_conversa_ou_404`).
Router à parte para não mexer no do atendimento: o `main.py` o inclui.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import AtendimentoConversa, AtendimentoReclamacao, User
from app.routers.atendimento import _conversa_ou_404, _nome, _responde, _so_admin, _view
from app.services.atendimento import devolucao_detalhe, foco_devolucao, instagram, reclamacoes
from app.services.atendimento.constantes import CANAL_CHAT, CANAL_RECLAMACAO
from app.services.atendimento.escopo import escopo_do_atendimento

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


class ReclamacaoOut(BaseModel):
    id: UUID
    plataforma: str
    plataforma_nome: str
    # Nº na plataforma (claim_id do ML, return_sn da Shopee, return_id da
    # TikTok); None quando a Logística ainda não tem o id do caso.
    numero: str | None
    # reclamacao | mediacao | devolucao
    tipo: str
    tipo_rotulo: str
    # O status cru da plataforma e o mesmo em português.
    status: str | None
    status_rotulo: str | None
    aberta: bool
    # O motivo da PLATAFORMA (nunca texto do comprador).
    motivo: str | None
    # O que o comprador pede (Shopee/TikTok: "Devolução + reembolso", "Só reembolso").
    solucao: str | None = None
    pedido_marketplace: str | None
    # Até quando a loja tem de agir (só das abertas).
    prazo_em: datetime | None
    aberta_em: datetime | None
    encerrada_em: datetime | None
    # O que a plataforma espera da loja, em português.
    acao_pendente: str | None
    # O ML mediando (o "Com Meli").
    mediacao: bool
    # A reclamação conta na reputação (ML)? None = não se sabe.
    reputacao_afetada: bool | None
    # Status da devolução do ML ligada à reclamação (cru).
    devolucao_status: str | None
    conversa_id: UUID | None
    url_plataforma: str | None
    # O bloco "Devolução" no painel do pedido (04/11/2026): caso ABERTO da Shopee
    # do pedido da conversa pedida. True = a tela põe o bloco e encolhe o cartão.
    do_pedido: bool = False
    # `refund_amount` e `currency` do detalhe; o rastreio e a situação da volta,
    # de `devolucao_rastreio`. Sem o dado, None: a tela não desenha a linha.
    valor_reembolso: float | None = None
    moeda: str | None = None
    rastreio_volta: str | None = None
    situacao_volta: str | None = None
    # O bloco tem o histórico completo lido na hora (TikTok, 18/11/2026): a tela
    # busca o `/detalhe` dele.
    detalhe_plataforma: bool = False


class ReclamacoesOut(BaseModel):
    itens: list[ReclamacaoOut]
    abertas: int
    # O prazo mais curto entre as abertas (o cartão conta o tempo até ele).
    prazo_mais_curto: datetime | None


@router.get("/conversas/{conversa_id}/reclamacoes", response_model=ReclamacoesOut)
async def reclamacoes_da_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> ReclamacoesOut:
    """As reclamações e devoluções da plataforma ligadas à conversa (cartão, só leitura)."""
    if instagram.e_instagram(conversa_id):
        # DM do Instagram não tem pedido nem reclamação.
        return ReclamacoesOut(itens=[], abertas=0, prazo_mais_curto=None)
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    linhas = await reclamacoes.reclamacoes_da_conversa(session, conversa)
    # O "Abrir na Shopee" com o id interno (devolução/pedido): uma consulta.
    ids_shopee = await reclamacoes.ids_shopee_das(session, linhas)
    # O bloco no painel só no chat e na conversa da devolução: e-mail, avaliação e
    # chat de afiliado ficam com o cartão de sempre. O chat do TikTok (18/11/2026)
    # é de um pedido só: o bloco vale para ele também.
    pedido_do_bloco = (
        conversa.pedido_marketplace
        if foco_devolucao.vale_para(conversa)
        or conversa.canal == CANAL_RECLAMACAO
        or (conversa.plataforma == "tiktok" and conversa.canal == CANAL_CHAT)
        else None
    )
    itens = [
        ReclamacaoOut(**reclamacoes.para_tela(r, ids_shopee, pedido_da_conversa=pedido_do_bloco))
        for r in linhas
    ]
    prazos = [i.prazo_em for i in itens if i.aberta and i.prazo_em is not None]
    return ReclamacoesOut(
        itens=itens,
        abertas=sum(1 for i in itens if i.aberta),
        prazo_mais_curto=min(prazos) if prazos else None,
    )


# ── A devolução do TikTok inteira (18/11/2026) ────────────────────────────


class DevolucaoAcaoOut(BaseModel):
    # aprovar_devolucao | recusar_devolucao | aprovar_reembolso | recusar_reembolso |
    # confirmar_pacote | recusar_pacote
    codigo: str
    rotulo: str
    recusa: bool
    exige_foto: bool
    # O que acontece no TikTok (a confirmação mostra antes do clique final).
    efeito: str
    # Por que não dá para fazer pela API (reembolso rápido): o botão vem travado.
    bloqueio: str | None = None
    prazo: datetime | None = None


class DevolucaoItemOut(BaseModel):
    nome: str | None = None
    variacao: str | None = None
    sku: str | None = None
    foto: str | None = None
    preco: float | None = None
    # O preço cheio (riscado), só com uma unidade no caso.
    preco_original: float | None = None
    moeda: str | None = None
    quantidade: int = 1


class DevolucaoReembolsoOut(BaseModel):
    total: float
    subtotal: float | None = None
    frete: float | None = None
    moeda: str | None = None


class DevolucaoVoltaOut(BaseModel):
    rastreio: str | None = None
    transportadora: str | None = None
    # O frete da volta que a LOJA paga (`seller_paid_return_shipping_fee`).
    frete_loja: float | None = None
    moeda: str | None = None


class DevolucaoFotoOut(BaseModel):
    url: str


class DevolucaoVideoOut(BaseModel):
    url: str
    capa: str | None = None
    duracao_s: int | None = None


class DevolucaoEventoOut(BaseModel):
    quando: datetime | None = None
    papel: str | None = None
    quem: str | None = None
    evento: str | None = None
    # O título do TikTok, em português ("Reembolso concluído").
    titulo: str
    motivo: str | None = None
    # O texto de quem fez (o do comprador = as "outras informações" do Duoke).
    texto: str | None = None
    fotos: list[DevolucaoFotoOut] = []
    videos: list[DevolucaoVideoOut] = []


class DevolucaoDetalheOut(BaseModel):
    reclamacao_id: UUID
    # Lido agora no TikTok (ou há menos de 5 min). False = a leitura falhou
    # (`erro`/`erro_texto`): a tela fica com o que o cartão já tem.
    ao_vivo: bool
    erro: str | None = None
    erro_texto: str | None = None
    lido_em: datetime | None = None
    status: str | None = None
    status_rotulo: str | None = None
    tipo_caso: str | None = None
    solucao: str | None = None
    motivo: str | None = None
    reembolso: DevolucaoReembolsoOut | None = None
    # O TikTok pagou o comprador assim que ele postou o pacote.
    reembolso_rapido: bool = False
    reembolso_pago_em: datetime | None = None
    volta: DevolucaoVoltaOut | None = None
    itens: list[DevolucaoItemOut] = []
    acao_pendente: str | None = None
    prazo: datetime | None = None
    arbitragem: str | None = None
    aberto_em: datetime | None = None
    acoes: list[DevolucaoAcaoOut] = []
    # `ATENDIMENTO_DEVOLUCAO_ACOES_ATIVA`: desligada, os botões vêm travados.
    acoes_ligadas: bool = False
    historico: list[DevolucaoEventoOut] = []


class MotivoRecusaOut(BaseModel):
    codigo: str
    texto: str


class MotivosRecusaOut(BaseModel):
    itens: list[MotivoRecusaOut]


async def _caso_da_conversa(
    session: AsyncSession, user: User, conversa_id: str, reclamacao_id: UUID
) -> tuple[AtendimentoConversa, AtendimentoReclamacao]:
    """A conversa (no escopo da pessoa) e o caso ligado a ela; 404 fora disso."""
    if instagram.e_instagram(conversa_id):
        raise HTTPException(404, detail={"code": "reclamacao_nao_encontrada"})
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    for r in await reclamacoes.reclamacoes_da_conversa(session, conversa):
        if r.id == reclamacao_id:
            return conversa, r
    raise HTTPException(404, detail={"code": "reclamacao_nao_encontrada"})


def _detalhe_out(reclamacao_id: UUID, dados: dict) -> DevolucaoDetalheOut:
    # O id vem de antes do rollback/commit: depois deles o objeto expira.
    return DevolucaoDetalheOut(
        reclamacao_id=reclamacao_id,
        acoes_ligadas=get_settings().atendimento_devolucao_acoes_ativa,
        **dados,
    )


def _recusa_http(e: devolucao_detalhe.AcaoRecusada) -> HTTPException:
    return HTTPException(e.http, detail={"code": e.code, "detail": e.detail})


@router.get(
    "/conversas/{conversa_id}/reclamacoes/{reclamacao_id}/detalhe",
    response_model=DevolucaoDetalheOut,
)
async def detalhe_da_devolucao(
    conversa_id: str,
    reclamacao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    atualizar: bool = False,
) -> DevolucaoDetalheOut:
    """O bloco inteiro da devolução do TikTok, lido na hora (só GET; `atualizar`
    pula o cache de 5 min). Falha do TikTok não é erro HTTP: volta em `erro`."""
    _, r = await _caso_da_conversa(session, user, conversa_id, reclamacao_id)
    dados = await devolucao_detalhe.ler_detalhe(session, r, forcar=atualizar)
    # Só leitura: nada a gravar (o token renovado, se houver, grava na sessão dele).
    await session.rollback()
    return _detalhe_out(reclamacao_id, dados)


@router.get(
    "/conversas/{conversa_id}/reclamacoes/{reclamacao_id}/motivos-recusa",
    response_model=MotivosRecusaOut,
)
async def motivos_de_recusa(
    conversa_id: str,
    reclamacao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> MotivosRecusaOut:
    """Os motivos que o TikTok aceita para recusar ESTE caso agora (só GET)."""
    _, r = await _caso_da_conversa(session, user, conversa_id, reclamacao_id)
    try:
        itens = await devolucao_detalhe.motivos_de_recusa(session, r)
    except devolucao_detalhe.AcaoRecusada as e:
        raise _recusa_http(e) from e
    finally:
        await session.rollback()
    return MotivosRecusaOut(itens=[MotivoRecusaOut(**m) for m in itens])


async def _fotos_da_recusa(fotos: list[UploadFile] | None) -> list[devolucao_detalhe.FotoDaRecusa]:
    saida = []
    for i, f in enumerate(fotos or [], start=1):
        dados = await f.read(devolucao_detalhe.FOTO_MAX_BYTES + 1)
        if len(dados) > devolucao_detalhe.FOTO_MAX_BYTES:
            raise HTTPException(
                422, detail={"code": "foto_invalida", "detail": f"A foto {i} passa de 5 MB."}
            )
        saida.append(
            devolucao_detalhe.FotoDaRecusa(
                nome=(f.filename or f"foto-{i}.jpg")[:120],
                dados=dados,
                tipo=(f.content_type or "").lower(),
            )
        )
    return saida


@router.post(
    "/conversas/{conversa_id}/reclamacoes/{reclamacao_id}/responder",
    response_model=DevolucaoDetalheOut,
)
async def responder_devolucao(
    conversa_id: str,
    reclamacao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_responde)],
    acao: Annotated[str, Form(max_length=40)],
    motivo: Annotated[str | None, Form(max_length=200)] = None,
    comentario: Annotated[str | None, Form(max_length=2000)] = None,
    fotos: Annotated[list[UploadFile] | None, File()] = None,
) -> DevolucaoDetalheOut:
    """A resposta da loja à devolução do TikTok (aprovar/recusar o pedido,
    confirmar/recusar o pacote). Recusa → 409/422 com o `code` e a frase; o
    TikTok sem resposta → 502 `resposta_incerta` (tentar de novo é seguro: a
    mesma chave de idempotência). Deu certo → a nota interna e o bloco relido."""
    conversa, r = await _caso_da_conversa(session, user, conversa_id, reclamacao_id)
    lidas = await _fotos_da_recusa(fotos)
    try:
        dados = await devolucao_detalhe.responder(
            session,
            r,
            conversa,
            acao,
            user_id=user.id,
            quem=_nome(user) or "equipe",
            motivo=motivo,
            comentario=comentario,
            fotos=lidas,
        )
    except devolucao_detalhe.AcaoRecusada as e:
        await session.rollback()
        raise _recusa_http(e) from e
    await session.commit()
    return _detalhe_out(reclamacao_id, dados)
