"""Pós-venda › Atendimento: as ABAS da conversa aberta (RF2, 02/10/2026).

  GET /api/atendimento/conversas/{id}/abas[?aba=<chave>&antes_de=<cursor>&limite=N]

Embaixo da conversa aberta, como o "Com o comprador / Com Meli" do Duoke:
Pré-venda · Pós-venda · Reclamação · Mediador · E-mail · Zap · Avaliação,
com TUDO do mesmo comprador e do mesmo pedido, na mesma loja. A regra de quem
entra e de qual aba é cada mensagem mora em `services/atendimento/abas.py`.

O que volta, por aba (só as com conteúdo, e sempre a da conversa aberta):
  - `total` (mensagens, notas internas incluídas) e as `conversas` de origem;
  - `mensagens`: as mais novas (`limite`, padrão 50), cada uma com a
    `conversa_id` e o `canal` de origem; `tem_mais` + `proximo` (cursor) para
    pedir as mais antigas com `?aba=<chave>&antes_de=<proximo>` (aí volta só
    aquela aba). Na aba DA CONVERSA ABERTA, as mensagens dela não vêm (o
    detalhe já as traz): vêm só as das outras conversas daquela aba;
  - `responde`: a conversa por onde a caixa responde nessa aba e se dá para
    enviar agora — as MESMAS travas do detalhe (`_envio`: envio desligado,
    conversa bloqueada, loja em observar…). Mediador: só leitura, sem conversa.
E, no topo: `aba_da_conversa` (a aba padrão da tela) e `fora_da_aba` (as
mensagens da conversa aberta que moram em outra aba: o pré-venda do chat da
Shopee, o mediador da reclamação), para a tela mostrar cada uma no seu lugar;
`compra_em` (a hora do pedido âncora) e `primeira_compra_em` (a primeira
compra do comprador na loja, Shopee: o chat se divide pela mais antiga).

SÓ LEITURA: nada é enviado, marcado como lido ou gravado por esta rota. A
MESMA TRAVA de acesso do router do atendimento (`_so_admin`: na observação,
toda a equipe lê e só ATENDIMENTO_USUARIOS mexe), a permissão de
leitura (`_view`) e o escopo por equipe (`_conversa_ou_404`; a família é
toda da mesma loja). Router à parte; o `main.py` o inclui.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.team_scope import resolve_team_scope
from app.models import AtendimentoConversa, AtendimentoMensagem, User
from app.routers.atendimento import (
    _conversa_ou_404,
    _envio,
    _mensagem_out,
    _nomes,
    _so_admin,
    _view,
)
from app.schemas.atendimento import MensagemOut
from app.services.atendimento import abas as abas_svc
from app.services.atendimento import instagram
from app.services.atendimento.constantes import CANAL_AVALIACAO

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


class AbaMensagemOut(MensagemOut):
    # A conversa de onde a mensagem veio (a tela separa e liga para ela).
    conversa_id: str
    canal: str


class AbaConversaOut(BaseModel):
    """Uma conversa de origem da aba (sem dado pessoal)."""

    id: str
    canal: str
    canal_rotulo: str
    # "Mala ABS" (pergunta/avaliação), "nº 5582543195" (reclamação), "pedido …".
    titulo: str | None
    pedido_marketplace: str | None
    situacao: str
    aguardando_resposta: bool
    ultima_mensagem_em: datetime | None
    # É a conversa aberta na tela.
    aberta: bool
    # Quantas mensagens dela estão NESTA aba.
    mensagens: int


class AbaRespondeOut(BaseModel):
    """Por onde a caixa responde nesta aba — e se dá para enviar agora."""

    # None = ninguém responde por aqui (Mediador: só leitura).
    conversa_id: str | None
    canal: str | None
    canal_rotulo: str | None
    pode_enviar: bool
    motivo: str | None
    codigo: str | None
    limite_caracteres: int | None
    modo_observacao: bool
    # A resposta é PÚBLICA (a avaliação): a tela pede a confirmação de sempre.
    publica: bool
    # A última mensagem daquela conversa (vai no envio: o `conversa_mudou`).
    ultima_vista_id: str | None


class AbaOut(BaseModel):
    chave: str
    rotulo: str
    total: int
    conversas: list[AbaConversaOut]
    mensagens: list[AbaMensagemOut]
    tem_mais: bool
    proximo: str | None
    responde: AbaRespondeOut


class AbasOut(BaseModel):
    conversa_id: str
    # A aba da conversa aberta (a padrão da tela); None = sem abas.
    aba_da_conversa: str | None
    abas: list[AbaOut]
    # Mensagens da conversa aberta que moram em outra aba: {id: aba}.
    fora_da_aba: dict[str, str]
    # Os números do pedido âncora e a hora da compra dele.
    pedido: list[str]
    compra_em: datetime | None
    # A primeira compra do comprador na loja (Shopee, pelo índice de pedidos):
    # o chat se divide pela compra mais antiga entre esta e `compra_em`.
    primeira_compra_em: datetime | None


def _vazio(conversa_id: str) -> AbasOut:
    return AbasOut(
        conversa_id=conversa_id,
        aba_da_conversa=None,
        abas=[],
        fora_da_aba={},
        pedido=[],
        compra_em=None,
        primeira_compra_em=None,
    )


def _conversa_aba(
    c: AtendimentoConversa, *, aberta: AtendimentoConversa, mensagens: int
) -> AbaConversaOut:
    return AbaConversaOut(
        id=str(c.id),
        canal=c.canal,
        canal_rotulo=abas_svc.rotulo_do_canal(c),
        titulo=abas_svc.titulo_da_conversa(c),
        pedido_marketplace=c.pedido_marketplace,
        situacao=c.situacao,
        aguardando_resposta=bool(c.aguardando_resposta),
        ultima_mensagem_em=c.ultima_mensagem_em,
        aberta=c.id == aberta.id,
        mensagens=mensagens,
    )


@router.get("/conversas/{conversa_id}/abas", response_model=AbasOut)
async def abas_da_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    aba: Annotated[str | None, Query(max_length=24)] = None,
    antes_de: Annotated[str | None, Query(max_length=120)] = None,
    limite: Annotated[int, Query(ge=1, le=abas_svc.MAX_POR_PAGINA)] = abas_svc.POR_PAGINA,
) -> AbasOut:
    """As abas da conversa aberta (só leitura): contagem, mensagens e quem responde."""
    if aba is not None and aba not in abas_svc.ORDEM_ABAS:
        raise HTTPException(422, detail={"code": "aba_invalida"})
    try:
        antes = abas_svc.ler_cursor(antes_de)
    except ValueError as e:
        raise HTTPException(422, detail={"code": "cursor_invalido"}) from e
    if antes is not None and aba is None:
        # O cursor é de UMA aba: sem ela, não se sabe de qual.
        raise HTTPException(422, detail={"code": "cursor_invalido"})
    if instagram.e_instagram(conversa_id):
        # Direct do Instagram: sem pedido, sem abas.
        return _vazio(conversa_id)
    scope = await resolve_team_scope(session, user)
    aberta = await _conversa_ou_404(session, conversa_id, scope)
    resultado = await abas_svc.abas_da_conversa(session, aberta)
    fam = resultado.familia
    por_id = {c.id: c for c in fam.conversas}

    escolhidas = [a for a in resultado.abas if aba is None or a.chave == aba]
    paginas: dict[str, tuple[list[abas_svc.Leve], bool]] = {}
    for a in escolhidas:
        paginas[a.chave] = abas_svc.pagina(
            a,
            sem_conversa=aberta.id if a.chave == resultado.aba_da_conversa else None,
            antes=antes,
            limite=limite,
        )
    # As mensagens COMPLETAS só da página de cada aba: uma consulta.
    ids = [m.id for pag, _ in paginas.values() for m in pag]
    completas: dict[UUID, AtendimentoMensagem] = {}
    if ids:
        completas = {
            m.id: m
            for m in (
                await session.execute(
                    select(AtendimentoMensagem).where(AtendimentoMensagem.id.in_(ids))
                )
            ).scalars()
        }
    nomes = await _nomes(session, {m.autor_user_id for m in completas.values()})

    # A última mensagem de cada conversa (o `ultima_vista_id` do envio): a
    # mais NOVA pela hora — as abas vêm na ordem da tela, e a da reclamação
    # se divide em Reclamação e Mediador (a fala da loja depois do mediador
    # é a última, não a do mediador).
    ultima: dict[UUID, abas_svc.Leve] = {}
    for a in resultado.abas:
        for m in a.mensagens:
            atual = ultima.get(m.conversa_id)
            if atual is None or m.ordem > atual.ordem:
                ultima[m.conversa_id] = m
    # O envio de cada conversa que responde (no máximo uma por aba).
    respondem = {
        a.chave: abas_svc.quem_responde(
            a.chave, [por_id[i] for i in a.conversas if i in por_id], aberta
        )
        for a in escolhidas
    }
    envios = {}
    for c in {r.id: r for r in respondem.values() if r is not None}.values():
        envios[c.id] = await _envio(session, c)

    saida: list[AbaOut] = []
    for a in escolhidas:
        pag, tem_mais = paginas[a.chave]
        contagem: dict[UUID, int] = {}
        for m in a.mensagens:
            contagem[m.conversa_id] = contagem.get(m.conversa_id, 0) + 1
        mensagens = []
        for leve in pag:
            m = completas.get(leve.id)
            origem = por_id.get(leve.conversa_id)
            if m is None or origem is None:
                continue
            base = _mensagem_out(m, conversa=origem, nomes=nomes)
            mensagens.append(
                AbaMensagemOut(**base.model_dump(), conversa_id=str(origem.id), canal=origem.canal)
            )
        quem = respondem[a.chave]
        if quem is None:
            responde = AbaRespondeOut(
                conversa_id=None,
                canal=None,
                canal_rotulo=None,
                pode_enviar=False,
                motivo=abas_svc.MOTIVO_MEDIADOR,
                codigo="somente_leitura",
                limite_caracteres=None,
                modo_observacao=False,
                publica=False,
                ultima_vista_id=None,
            )
        else:
            envio = envios[quem.id]
            vista = ultima[quem.id].id if quem.id in ultima else None
            responde = AbaRespondeOut(
                conversa_id=str(quem.id),
                canal=quem.canal,
                canal_rotulo=abas_svc.rotulo_do_canal(quem),
                pode_enviar=envio.pode_enviar,
                motivo=envio.motivo,
                codigo=envio.codigo,
                limite_caracteres=envio.limite_caracteres,
                modo_observacao=envio.modo_observacao,
                publica=quem.canal == CANAL_AVALIACAO,
                ultima_vista_id=str(vista) if vista is not None else None,
            )
        saida.append(
            AbaOut(
                chave=a.chave,
                rotulo=abas_svc.ROTULO_ABA[a.chave],
                total=len(a.mensagens),
                conversas=[
                    _conversa_aba(por_id[i], aberta=aberta, mensagens=contagem.get(i, 0))
                    for i in a.conversas
                    if i in por_id
                ],
                mensagens=mensagens,
                tem_mais=tem_mais,
                proximo=abas_svc.cursor_de(pag[0]) if tem_mais and pag else None,
                responde=responde,
            )
        )
    return AbasOut(
        conversa_id=str(aberta.id),
        aba_da_conversa=resultado.aba_da_conversa,
        abas=saida,
        fora_da_aba={str(k): v for k, v in resultado.fora_da_aba.items()},
        pedido=sorted(fam.pedido),
        compra_em=fam.compra_em,
        primeira_compra_em=fam.primeira_compra_em,
    )
