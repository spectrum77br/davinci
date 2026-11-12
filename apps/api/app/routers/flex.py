"""Flex por anúncio — a tela (Pós-venda → Logística, aba Flex › Anúncios).

Projeto Flex, etapa 3 (services/flex_motor). Mesmo recurso da Logística:
`logistica:view` para ver, `logistica:edit` para agir (sincronizar, aprovar,
emergência); admin passa sempre (`require_permission`).

  GET  /api/flex/config        modo, contas (com o Flex DA CONTA: assinatura
                               do ML / canal da loja Shopee, e a descoberta),
                               números (nada de segredo)
  GET  /api/flex/anuncios      estado por anúncio (filtros, busca e o resumo
                               do topo da tela)
  POST /api/flex/sincronizar   roda o motor agora (job no worker), no modo atual
  POST /api/flex/anuncios/{conta}/{anuncio}/aprovar   aprova LIGAR (rodada
                               ocupada: a ligação vai para a fila do worker)
  POST /api/flex/anuncios/{conta}/{anuncio}/desligar  uma pessoa desliga o
                               Flex do anúncio agora (só piloto/ativo e conta
                               pronta; tira a aprovação — "Desligar selecionados")
  POST /api/flex/emergencia    tira as aprovações e põe na fila o job que
                               desliga tudo das contas permitidas
                               (observar/desligado: só simula). Desde 12/11/2026
                               a tela não tem mais o botão ("Desligar tudo"
                               desligava também o ML de Piracicaba)
  GET  /api/flex/emergencia/ultima   a última emergência (andamento)
  GET  /api/flex/emergencia/{id}     o andamento de uma emergência
  GET  /api/flex/log           últimas linhas da trilha
  GET  /api/flex/pedidos       pedidos Flex detectados (com o aviso do .sp;
                               `abertos=true` tira os que já saíram — menos
                               os que saíram sem passar pelo .sp e esperam o
                               acerto do estoque)
  POST /api/flex/pedidos/{bling_id}/acertado   a pessoa acertou o estoque
                               no Bling (o saldo Flex para de descontar)

Quem vê: admin + cairo SA e churchill (`flex_config.pode_ver`, 05/11 e 11/11/2026;
de 05/10 a 05/11 era a lista `flex_usuarios`) — para os outros TODA rota daqui responde o 404 de
rota que não existe (como Sistema › Histórico) e o router fica fora da
documentação (/api/docs).
`GET /api/flex/acesso` é como a tela descobre se mostra a aba.

Escopo por equipe (deps/team_scope): usuário com equipe só vê e só mexe nas
contas da equipe — anúncios, resumo, trilha, pedidos, aprovar e emergência
(admin e quem não tem equipe: tudo), como a Logística (`?envio=flex`).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, exists, false, func, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.config import get_settings
from app.db import get_session
from app.deps.auth import get_current_user, require_permission
from app.deps.team_scope import resolve_team_scope
from app.models import (
    BlingOrder,
    FlexAnuncioEstado,
    FlexConta,
    FlexEmergencia,
    FlexLocal,
    FlexLog,
    FlexPedido,
    Integration,
    Listing,
    ProductLink,
    User,
    UserRole,
)
from app.models.flex import FLEX_ACOES
from app.schemas.flex import (
    FlexAcertoOut,
    FlexAjustesIn,
    FlexAjustesOut,
    FlexAnuncioOut,
    FlexAnunciosOut,
    FlexAprovarOut,
    FlexConfigOut,
    FlexContaOut,
    FlexDesligarOut,
    FlexEmergenciaOut,
    FlexLocalIn,
    FlexLocalOut,
    FlexLogOut,
    FlexPedidoOut,
    FlexResumoOut,
    FlexSincronizarOut,
)
from app.security.cipher import decrypt_json
from app.services import flex_config, flex_envio, flex_local, flex_motor, flex_textos

logger = structlog.get_logger()


async def _so_quem_ve(user: Annotated[User | None, Depends(get_current_user)]) -> None:
    # Igual ao 404 do FastAPI para rota desconhecida: quem não vê o Flex não
    # descobre nem que o Flex existe.
    if not flex_config.pode_ver(user):
        raise HTTPException(404, detail="Not Found")
    # O local de saída (cidade + lote) vale para os textos e as contas desta
    # resposta (cache de 30 s; quem salva atualiza na hora).
    await flex_local.carregar()


router = APIRouter(
    prefix="/api/flex",
    tags=["flex"],
    include_in_schema=False,
    dependencies=[Depends(_so_quem_ve)],
)

Ver = Annotated[User, Depends(require_permission("logistica", "view"))]
Agir = Annotated[User, Depends(require_permission("logistica", "edit"))]
Sessao = Annotated[AsyncSession, Depends(get_session)]


def _plataforma_txt(integ: Integration | None) -> str | None:
    if integ is None:
        return None
    return getattr(integ.platform, "value", str(integ.platform))


def _loja_shopee(integ: Integration | None) -> str | None:
    """O shop_id da loja da Shopee (das credenciais) para a tela montar o link
    do anúncio — só ele sai daqui, nunca o resto das credenciais."""
    if integ is None or _plataforma_txt(integ) != flex_envio.PLATAFORMA_SHOPEE:
        return None
    try:
        sid = int((decrypt_json(integ.credentials) or {}).get("shop_id") or 0)
    except Exception:  # noqa: BLE001 — sem o número, a tela fica sem o link
        return None
    return str(sid) if sid > 0 else None


async def _escopo(session: AsyncSession, user: User) -> frozenset[UUID] | None:
    """Contas (integration_id) que o usuário enxerga; None = todas (admin ou
    sem equipe). A mesma régua das outras telas (deps/team_scope)."""
    scope = await resolve_team_scope(session, user)
    return None if scope.unrestricted else frozenset(scope.integration_ids)


def _no_escopo(coluna, escopo: frozenset[UUID] | None):
    """Filtro da coluna de conta pelo escopo (None = sem filtro)."""
    if escopo is None:
        return None
    return coluna.in_(list(escopo)) if escopo else false()


async def _permitidas(session: AsyncSession) -> frozenset[UUID]:
    """As contas em que o motor mexe AGORA (flex_contas, ativas, ML/Shopee).
    Conta que saiu da lista fica com o estado antigo no banco — o "esperando
    aprovação" dela não vale mais (o motor não regrava conta fora da lista)."""
    ids = flex_config.contas()
    return frozenset((await flex_motor.integracoes_permitidas(session, ids)).keys())


@router.get("/acesso")
async def acesso() -> dict[str, bool | str]:
    """A tela mostra a aba Flex só quando isto responde 200. Traz também o
    local de saída (os textos da Logística dizem de onde o Flex sai)."""
    local = flex_local.atual()
    return {"ok": True, "cidade": local.cidade, "lote": local.lote}


async def _local_out(session: AsyncSession, local: flex_local.LocalFlex) -> FlexLocalOut:
    nome = None
    if local.atualizado_por is not None:
        quem = await session.get(User, local.atualizado_por)
        nome = (quem.name or quem.email) if quem is not None else None
    return FlexLocalOut(
        cidade=local.cidade,
        lote=local.lote,
        atualizado_em=local.atualizado_em,
        atualizado_por=nome,
    )


def _pode_alterar_local(user: User) -> bool:
    """Trocar o local muda em que contas o robô mexe e de que estoque o Flex
    vive: só admin (e só quem vê o Flex — o router já garante)."""
    return user.role == UserRole.ADMIN


@router.put("/local", response_model=FlexLocalOut)
async def trocar_local(body: FlexLocalIn, session: Sessao, user: Agir) -> FlexLocalOut:
    """Troca o local de saída do Flex (cidade + lote). Vale na próxima rodada
    do robô (até 15 min) — o "Sincronizar agora" aplica na hora."""
    if not _pode_alterar_local(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "so_admin", "detalhe": "só um admin troca o local de saída do Flex"},
        )
    # Trava a linha do local antes de comparar: dois PUTs ao mesmo tempo com o
    # mesmo "antes" — o segundo espera o primeiro e recebe o 409.
    linha = await session.get(FlexLocal, 1, with_for_update=True)
    atual = flex_local.local_da_linha(linha)
    if body.cidade_antes is not None and (
        body.cidade_antes != atual.cidade or (body.lote_antes or "") != atual.lote
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "local_mudou",
                "detalhe": (
                    f"o local foi trocado para {atual.cidade} (.{atual.lote}) agora há pouco"
                ),
            },
        )
    try:
        local = await flex_local.salvar(session, cidade=body.cidade, lote=body.lote, por=user.id)
    except flex_local.LocalFlexInvalidoError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"code": e.codigo, "detalhe": e.detalhe}
        ) from e
    await session.commit()
    flex_local.usar(local)
    out = await _local_out(session, local)
    out.ligados_fora = await _ligados_fora_do_local(session, local)
    return out


async def _ligados_fora_do_local(session: AsyncSession, local: flex_local.LocalFlex) -> int:
    """Anúncios com Flex ligado (última leitura) nas contas liberadas do ML
    cuja saída do Flex NÃO é na cidade do local — o robô deixa de mexer neles."""
    ids = flex_config.contas()
    if not ids:
        return 0
    contas = (
        await session.execute(select(FlexConta).where(FlexConta.integration_id.in_(list(ids))))
    ).scalars()
    fora = [
        c.integration_id
        for c in contas
        if c.plataforma == flex_envio.PLATAFORMA_ML
        and c.flex_ativo
        and not flex_local.origem_ok(c.origem_cidade, local)
    ]
    if not fora:
        return 0
    n = await session.scalar(
        select(func.count()).where(
            FlexAnuncioEstado.integration_id.in_(fora), FlexAnuncioEstado.observado == "ligado"
        )
    )
    return int(n or 0)


@router.get("/config", response_model=FlexConfigOut)
async def config(session: Sessao, user: Ver) -> FlexConfigOut:
    s = get_settings()
    cfg = flex_motor.ConfigFlex.das_configuracoes()
    escopo = await _escopo(session, user)
    ids = sorted(
        (i for i in flex_config.contas() if escopo is None or i in escopo), key=str
    )
    permitidas = await flex_motor.integracoes_permitidas(session, ids)
    # O Flex de cada conta como o motor deixou (a tela não chama a plataforma).
    linhas = (
        {
            c.integration_id: c
            for c in (
                await session.execute(select(FlexConta).where(FlexConta.integration_id.in_(ids)))
            )
            .scalars()
            .all()
        }
        if ids
        else {}
    )
    contas = []
    for i in ids:
        integ = permitidas.get(i)
        c = linhas.get(i)
        plataforma = _plataforma_txt(integ)
        motivo = None
        conta_flex = flex_motor.conta_flex_da_linha(c) if c is not None else None
        if conta_flex is not None and conta_flex.ativo is not None:
            # Sem Flex na plataforma, ou (ML) com a saída do Flex fora da
            # cidade do local / não lida: a frase que os anúncios dela mostram.
            motivo = flex_motor.bloqueio_da_conta(c.plataforma, conta_flex)
        origem_ok = (
            flex_motor.bloqueio_da_origem(conta_flex) is None
            if conta_flex is not None and c.plataforma == "ml" and c.flex_ativo
            else None
        )
        contas.append(
            FlexContaOut(
                id=i,
                nome=integ.name if integ is not None else None,
                plataforma=plataforma,
                existe=integ is not None,
                loja_id=_loja_shopee(integ),
                flex_ativo=c.flex_ativo if c else None,
                flex_status=c.status if c else None,
                flex_detalhe=c.detalhe if c else None,
                flex_motivo=motivo,
                flex_origem_cep=c.origem_cep if c else None,
                flex_origem_cidade=c.origem_cidade if c else None,
                flex_origem_ok=origem_ok,
                flex_lido_em=c.lido_em if c else None,
                flex_erro=c.erro if c else None,
                descoberta_em=c.descoberta_em if c else None,
                descoberta_ok=c.descoberta_ok if c else None,
                descoberta_total=c.descoberta_total if c else None,
                descoberta_novos=c.descoberta_novos if c else None,
                descoberta_erro=c.descoberta_erro if c else None,
            )
        )
    m = flex_config.modo()
    vistas: list[str] = []
    for c in linhas.values():
        for cid in flex_local.cidades_da_origem(c.origem_cidade):
            if not any(flex_local.mesma_cidade(cid, v) for v in vistas):
                vistas.append(cid)
    return FlexConfigOut(
        modo=m,
        pode_escrever=flex_config.pode_escrever(m),
        contas=contas,
        n_liga=cfg.n_liga,
        n_desliga=cfg.n_desliga,
        kits=cfg.kits,
        max_anuncios_por_familia=cfg.max_anuncios_por_familia,
        teto_escritas_por_rodada=max(0, int(s.flex_teto_escritas_por_rodada or 0)),
        shopee_canais=sorted(flex_envio.canais_shopee_flex()),
        shopee_escrita=flex_config.shopee_escrita(),
        intervalo_min=max(1, int(s.flex_intervalo_min or 15)),
        pedido_no_sp=flex_config.pedido_no_sp(),
        local=await _local_out(session, flex_local.atual()),
        pode_alterar_local=_pode_alterar_local(user),
        cidades_vistas=sorted(vistas, key=flex_local.normalizar_cidade),
        lotes=list(flex_local.LOTES),
        ajustes=await _ajustes_out(session),
        pode_ajustar=flex_config.pode_ajustar(user),
    )


async def _ajustes_out(session: AsyncSession) -> FlexAjustesOut:
    a = flex_config.ajustes()
    nome = None
    if a.atualizado_por is not None:
        quem = await session.get(User, a.atualizado_por)
        nome = (quem.name or quem.email) if quem is not None else None
    return FlexAjustesOut(
        modo_origem=flex_config.origem("modo"),
        shopee_escrita_origem=flex_config.origem("shopee_escrita"),
        pedido_no_sp_origem=flex_config.origem("pedido_no_sp"),
        kits_origem=flex_config.origem("kits"),
        atualizado_em=a.atualizado_em,
        atualizado_por=nome,
    )


@router.put("/ajustes", response_model=FlexAjustesOut)
async def trocar_ajustes(body: FlexAjustesIn, session: Sessao, user: Agir) -> FlexAjustesOut:
    """Troca o modo do Flex, a escrita da Shopee, o pedido Flex no .sp
    (11/11/2026: antes só no .env do servidor) e, desde 12/11/2026, "Kits
    entram". Só admin e o gerente. Vale na próxima rodada do robô (até 15 min)
    — o "Sincronizar Flex" aplica na hora."""
    if not flex_config.pode_ajustar(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "so_admin",
                "detalhe": "só um admin (ou o gerente) troca o modo do Flex",
            },
        )
    # Trava a linha (dentro de `salvar`) e relê: quem abriu o formulário com
    # outro valor na tela não sobrescreve a troca de outra pessoa.
    await flex_config.carregar(forcar=True)
    antes = (
        flex_config.modo(),
        flex_config.shopee_escrita(),
        flex_config.pedido_no_sp(),
        flex_config.kits(),
    )
    mostrado = (
        body.modo_antes,
        body.shopee_escrita_antes,
        body.pedido_no_sp_antes,
        body.kits_antes,
    )
    if any(m is not None and m != a for m, a in zip(mostrado, antes, strict=True)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "ajuste_mudou",
                "detalhe": "alguém trocou essas chaves agora há pouco — reabra e confira",
            },
        )
    try:
        novo = await flex_config.salvar(
            session,
            modo_novo=body.modo,
            shopee_escrita_nova=body.shopee_escrita,
            pedido_no_sp_novo=body.pedido_no_sp,
            kits_novo=body.kits,
            por=user.id,
        )
    except flex_config.AjusteInvalidoError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"code": e.codigo, "detalhe": e.detalhe}
        ) from e
    await session.commit()
    flex_config.usar(novo)
    return await _ajustes_out(session)


def _importado_do_estado():
    """O anúncio importado (`listings`) da linha do estado. Na Shopee a
    importação às vezes grava `item_model` (um por modelo) — o anúncio é a
    primeira parte (o mesmo `_id_anuncio` do motor)."""
    est = FlexAnuncioEstado
    return and_(
        Listing.integration_id == est.integration_id,
        or_(
            Listing.external_id == est.external_id,
            and_(
                est.plataforma == flex_envio.PLATAFORMA_SHOPEE,
                func.split_part(Listing.external_id, "_", 1) == est.external_id,
            ),
        ),
    )


def _titulo_subq():
    """Título do anúncio — o do primeiro vínculo que tiver; sem vínculo (o
    anúncio só importado, que o motor desliga), o da importação. Antes o só
    importado vinha sem título e a busca pelo nome não o achava."""
    do_vinculo = (
        select(func.min(ProductLink.listing_title))
        .where(
            ProductLink.integration_id == FlexAnuncioEstado.integration_id,
            ProductLink.external_id == FlexAnuncioEstado.external_id,
        )
        .correlate(FlexAnuncioEstado)
        .scalar_subquery()
    )
    da_importacao = (
        select(func.min(Listing.title))
        .where(_importado_do_estado())
        .correlate(FlexAnuncioEstado)
        .scalar_subquery()
    )
    return func.coalesce(do_vinculo, da_importacao)


def _anuncio_out(
    est: FlexAnuncioEstado,
    conta: str | None,
    titulo: str | None,
    permitidas: frozenset[UUID] | None = None,
) -> FlexAnuncioOut:
    out = FlexAnuncioOut.model_validate(est)
    out.conta = conta
    out.titulo = titulo
    out.motivo_claro = flex_textos.motivo_claro(est.motivo)
    if permitidas is not None and est.integration_id not in permitidas:
        # Conta fora de flex_contas: ninguém aprova (nem o motor regrava) —
        # "esperando aprovação" ali seria um aviso que nada limpa.
        out.aguardando_aprovacao = False
    return out


async def _liberadas(session: AsyncSession, permitidas: frozenset[UUID]) -> frozenset[UUID]:
    """Contas em que o robô mexe de verdade: em `flex_contas` e sem bloqueio
    da conta (sem Flex, não conferida, ou a saída do Flex fora da cidade do
    local de saída)."""
    if not permitidas:
        return frozenset()
    linhas = (
        await session.execute(
            select(FlexConta).where(FlexConta.integration_id.in_(list(permitidas)))
        )
    ).scalars()
    return frozenset(
        c.integration_id
        for c in linhas
        if flex_motor.bloqueio_da_conta(c.plataforma, flex_motor.conta_flex_da_linha(c)) is None
    )


def _desligar(liberadas: frozenset[UUID]):
    """"Ligados que deveriam desligar": ligado na plataforma, a regra não quer
    e o robô mexe na conta (a de Piracicaba fica de fora: o robô não toca)."""
    est = FlexAnuncioEstado
    if not liberadas:
        return false()
    return and_(
        est.observado == "ligado",
        est.desejado != "ligado",
        est.integration_id.in_(list(liberadas)),
    )


def _sem_controle(permitidas: frozenset[UUID], liberadas: frozenset[UUID]):
    """Flex ligado em conta liberada em que o robô não mexe (ver `_liberadas`)."""
    est = FlexAnuncioEstado
    fora = permitidas - liberadas
    if not fora:
        return false()
    return and_(est.observado == "ligado", est.integration_id.in_(list(fora)))


def _aguardando(permitidas: frozenset[UUID]):
    """Esperando aprovação DE VERDADE: só nas contas em que o motor mexe."""
    est = FlexAnuncioEstado
    if not permitidas:
        return false()
    return and_(est.aguardando_aprovacao.is_(True), est.integration_id.in_(list(permitidas)))


def _das_contas_do_flex(permitidas: frozenset[UUID]):
    """Só os anúncios das contas que existem e estão em flex_contas: a conta
    arquivada (ou tirada da lista) deixa o estado antigo no banco — em
    12/11/2026 eram 13 contas do ML com 1.531 "Flex ligado" lidos em 08/10."""
    if not permitidas:
        return false()
    return FlexAnuncioEstado.integration_id.in_(list(permitidas))


def _com_peca():
    """Anúncio com peça livre no estoque do local de saída (.sp). Desde
    11/11/2026 o motor anota as peças também nas contas que não estão prontas."""
    return FlexAnuncioEstado.saldo_sp >= 1


def _conta_nao_pronta(liberadas: frozenset[UUID]):
    """Com peça, mas numa conta em que o robô não mexe (fora de flex_contas, sem
    Flex, ou a saída do Flex em outra cidade): falta arrumar a CONTA."""
    if not liberadas:
        return _com_peca()
    return and_(_com_peca(), FlexAnuncioEstado.integration_id.not_in(list(liberadas)))


# ---- "Sistema quer" e "Ação" como a tela mostra (12/11/2026) -------------------
# Vinicius: filtrando "Não pode ter Flex" apareciam linhas com o selo "Pode ter
# Flex" (o filtro olhava só o `desejado`; a tela mostra "Pode ter Flex" para
# quem tem peça e só falta a conta ficar pronta, e — desde hoje — para quem a
# Shopee recusou por PROMOÇÃO: "ele pode ter flex, eu só preciso tirar a
# promoção"). Os filtros aqui seguem a mesma régua de `querInfo`/`acaoDe` em
# components/LogisticaFlexAnuncios.vue.
_CONFIRMANDO_LIKE = "%esperando ela mostrar%"


def _recusa_promocao():
    """A Shopee recusou ligar porque o anúncio está em promoção."""
    r = func.coalesce(FlexAnuncioEstado.recusa, "")
    return or_(r.ilike("%em promoção%"), r.ilike("%has promotion%"))


def _quer_pode(liberadas: frozenset[UUID], n_liga: int):
    """Selo "Pode ter Flex": a regra quer ligado — ou tem peça (n_liga+) e só
    falta a conta ficar pronta, a Shopee só ler, ou tirar a promoção."""
    est = FlexAnuncioEstado
    fora = est.integration_id.not_in(list(liberadas)) if liberadas else true()
    return or_(
        est.desejado == "ligado",
        and_(
            func.coalesce(est.saldo_sp, -1) >= n_liga,
            or_(
                fora,
                func.coalesce(est.motivo, "").startswith("Shopee só leitura"),
                _recusa_promocao(),
            ),
        ),
    )


def _quer_desligado(liberadas: frozenset[UUID], n_liga: int):
    """Selo "Flex desligado": a regra quer desligado — ou a conta não está
    pronta e há peça de menos (o cinza igual ao das contas prontas)."""
    est = FlexAnuncioEstado
    fora = est.integration_id.not_in(list(liberadas)) if liberadas else true()
    return and_(
        not_(_quer_pode(liberadas, n_liga)),
        or_(
            est.desejado == "desligado",
            and_(fora, est.saldo_sp.is_not(None), est.saldo_sp < n_liga),
        ),
    )


def _quer_nao_pode(liberadas: frozenset[UUID], n_liga: int):
    """Selo "Não pode ter Flex": o resto (sem vínculo, sem .sp, kit misturado…)."""
    return and_(
        not_(_quer_pode(liberadas, n_liga)),
        not_(_quer_desligado(liberadas, n_liga)),
    )


def _acao(acao: str, liberadas: frozenset[UUID]):
    """O que a coluna "Ação" mostra. Só conta pronta tem botão/selo; a outra
    mostra o que falta na conta ("conta_nao_pronta")."""
    est = FlexAnuncioEstado
    pronta = est.integration_id.in_(list(liberadas)) if liberadas else false()
    erro = func.coalesce(est.ultimo_erro, "")
    if acao == "conta_nao_pronta":
        return not_(pronta)
    if acao == "aprovar":
        return and_(pronta, est.aguardando_aprovacao.is_(True), est.recusa.is_(None))
    aprovado_sem_ligar = and_(
        est.aprovado_em.is_not(None),
        est.desejado == "ligado",
        func.coalesce(est.observado, "") != "ligado",
        est.recusa.is_(None),
        not_(erro.ilike(_CONFIRMANDO_LIKE)),
    )
    if acao == "tentar_novamente":
        # 12/11/2026: "Tentar de novo" (a plataforma recusou) e "Tentar agora"
        # (aprovado e ainda não ligou) viraram um botão só, em amarelo.
        return and_(pronta, or_(est.recusa.is_not(None), aprovado_sem_ligar))
    if acao == "tentar_de_novo":
        return and_(pronta, est.recusa.is_not(None))
    if acao == "tentar_agora":
        return and_(pronta, aprovado_sem_ligar)
    if acao == "nao_conferido":
        # Conta pronta, plataforma ainda não lida (a Ação diz "ainda não conferido").
        return and_(pronta, est.observado.is_(None))
    if acao == "confirmando":
        return and_(pronta, erro.ilike(_CONFIRMANDO_LIKE))
    if acao == "ativado":
        return and_(pronta, est.observado == "ligado", est.desejado == "ligado")
    if acao == "desativado":
        return and_(
            pronta,
            est.observado == "desligado",
            est.desejado != "ligado",
            est.recusa.is_(None),
            not_(erro.ilike(_CONFIRMANDO_LIKE)),
        )
    if acao == "desliga":
        return _desligar(liberadas)
    return true()


async def _resumo(
    session: AsyncSession,
    escopo: frozenset[UUID] | None,
    permitidas: frozenset[UUID],
    *,
    plataforma: str | None = None,
    integration_id: UUID | None = None,
) -> FlexResumoOut:
    """O quadro do topo da tela: os anúncios avaliados das contas do Flex,
    sem os filtros de situação/peças da lista (o dono vê de cara quantos
    esperam por ele) — dentro do escopo da equipe.

    12/11/2026 (revisão do painel): segue a Plataforma e a Conta escolhidas —
    com "Mercado Livre" a lista dava 0 e os cartões seguiam com os números da
    Shopee. E só conta que existe e está em flex_contas: as 13 contas do ML
    arquivadas deixavam 1.531 "Flex ligado" lidos em 08/10 nos totais."""
    liberadas = await _liberadas(session, permitidas)
    n_liga = flex_motor.ConfigFlex.das_configuracoes().n_liga
    est = FlexAnuncioEstado
    q = select(
        func.count(),
        func.count().filter(est.observado == "ligado"),
        func.count().filter(_aguardando(permitidas)),
        func.count().filter(_desligar(liberadas)),
        func.count().filter(est.observado.is_(None)),
        func.count().filter(_sem_controle(permitidas, liberadas)),
        func.count().filter(_com_peca()),
        func.count().filter(est.saldo_sp >= n_liga),
        func.count().filter(_conta_nao_pronta(liberadas)),
    ).select_from(est)
    q = q.where(_das_contas_do_flex(permitidas))
    filtro = _no_escopo(est.integration_id, escopo)
    if filtro is not None:
        q = q.where(filtro)
    if plataforma is not None:
        q = q.where(est.plataforma == plataforma)
    if integration_id is not None:
        q = q.where(est.integration_id == integration_id)
    row = (await session.execute(q)).one()
    return FlexResumoOut(
        avaliados=int(row[0] or 0),
        ligados=int(row[1] or 0),
        aguardando=int(row[2] or 0),
        desligar=int(row[3] or 0),
        nao_lidos=int(row[4] or 0),
        sem_controle=int(row[5] or 0),
        com_peca=int(row[6] or 0),
        da_para_ter=int(row[7] or 0),
        conta_nao_pronta=int(row[8] or 0),
    )


@router.get("/anuncios", response_model=FlexAnunciosOut)
async def anuncios(
    session: Sessao,
    user: Ver,
    integration_id: UUID | None = None,
    plataforma: Literal["ml", "shopee"] | None = None,
    desejado: Literal["ligado", "desligado", "inelegivel"] | None = None,
    # O que a plataforma disse da última leitura; `nao_lido` = ainda não leu.
    observado: Literal["ligado", "desligado", "nao_lido"] | None = None,
    aguardando: bool | None = None,
    # `desligar=true`: ligado na plataforma, mas a regra não quer — o mesmo
    # número do resumo ("ligados que deveriam desligar").
    desligar: bool = False,
    # Peças no local de saída (11/11/2026): `com` = pelo menos 1 peça livre;
    # `flex` = o bastante para ter Flex (n_liga). Os mesmos números do resumo.
    pecas: Literal["com", "flex"] | None = None,
    # Com peça, mas a conta ainda não está pronta (o robô não mexe nela).
    conta_nao_pronta: bool = False,
    # O selo "Sistema quer" como a tela mostra (12/11/2026 — ver `_quer_pode`).
    quer: Literal["pode", "desligado", "nao_pode"] | None = None,
    # A coluna "Ação" (12/11/2026: "filtrar somente os tentar de novo etc.").
    acao: Literal[
        "aprovar",
        "tentar_novamente",
        "tentar_de_novo",
        "tentar_agora",
        "nao_conferido",
        "confirmando",
        "ativado",
        "desativado",
        "desliga",
        "conta_nao_pronta",
    ]
    | None = None,
    # Busca da tela: id do anúncio, família ou título (sem curinga: `%`/`_`
    # digitados são letras).
    busca: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FlexAnunciosOut:
    escopo = await _escopo(session, user)
    permitidas = await _permitidas(session)
    filtros = [_das_contas_do_flex(permitidas)]
    if pecas == "com":
        filtros.append(_com_peca())
    elif pecas == "flex":
        n_liga = flex_motor.ConfigFlex.das_configuracoes().n_liga
        filtros.append(FlexAnuncioEstado.saldo_sp >= n_liga)
    if conta_nao_pronta:
        filtros.append(_conta_nao_pronta(await _liberadas(session, permitidas)))
    if quer is not None or acao is not None:
        liberadas = await _liberadas(session, permitidas)
        n_liga = flex_motor.ConfigFlex.das_configuracoes().n_liga
        if quer == "pode":
            filtros.append(_quer_pode(liberadas, n_liga))
        elif quer == "desligado":
            filtros.append(_quer_desligado(liberadas, n_liga))
        elif quer == "nao_pode":
            filtros.append(_quer_nao_pode(liberadas, n_liga))
        if acao is not None:
            filtros.append(_acao(acao, liberadas))
    if (f := _no_escopo(FlexAnuncioEstado.integration_id, escopo)) is not None:
        filtros.append(f)
    if integration_id is not None:
        filtros.append(FlexAnuncioEstado.integration_id == integration_id)
    if plataforma is not None:
        filtros.append(FlexAnuncioEstado.plataforma == plataforma)
    if desejado is not None:
        filtros.append(FlexAnuncioEstado.desejado == desejado)
    if observado == "nao_lido":
        filtros.append(FlexAnuncioEstado.observado.is_(None))
    elif observado is not None:
        filtros.append(FlexAnuncioEstado.observado == observado)
    if aguardando is True:
        filtros.append(_aguardando(permitidas))
    elif aguardando is False:
        filtros.append(not_(_aguardando(permitidas)))
    if desligar:
        filtros.append(_desligar(await _liberadas(session, permitidas)))
    termo = (busca or "").strip()
    if termo:
        filtros.append(
            or_(
                FlexAnuncioEstado.external_id.icontains(termo, autoescape=True),
                FlexAnuncioEstado.familias.icontains(termo, autoescape=True),
                exists().where(
                    ProductLink.integration_id == FlexAnuncioEstado.integration_id,
                    ProductLink.external_id == FlexAnuncioEstado.external_id,
                    ProductLink.listing_title.icontains(termo, autoescape=True),
                ),
                exists().where(
                    _importado_do_estado(),
                    Listing.title.icontains(termo, autoescape=True),
                ),
            )
        )
    total = int(
        await session.scalar(select(func.count()).select_from(FlexAnuncioEstado).where(*filtros))
        or 0
    )
    rows = await session.execute(
        select(FlexAnuncioEstado, Integration.name, _titulo_subq())
        .outerjoin(Integration, Integration.id == FlexAnuncioEstado.integration_id)
        .where(*filtros)
        # Quem espera uma pessoa primeiro; depois o que está/quer ligado.
        .order_by(
            case((_aguardando(permitidas), 0), else_=1),
            case((FlexAnuncioEstado.observado == "ligado", 0), else_=1),
            case((FlexAnuncioEstado.desejado == "ligado", 0), else_=1),
            # Depois, quem tem mais peça no local de saída (sem peça por último).
            FlexAnuncioEstado.saldo_sp.desc().nulls_last(),
            FlexAnuncioEstado.atualizado_em.desc(),
            FlexAnuncioEstado.external_id,
        )
        .limit(limit)
        .offset(offset)
    )
    return FlexAnunciosOut(
        total=total,
        itens=[_anuncio_out(est, nome, titulo, permitidas) for est, nome, titulo in rows.all()],
        resumo=await _resumo(
            session, escopo, permitidas, plataforma=plataforma, integration_id=integration_id
        ),
    )


@router.post("/sincronizar", response_model=FlexSincronizarOut)
async def sincronizar(user: Agir) -> FlexSincronizarOut:
    """Roda o motor agora, no modo em vigor (o worker pega o job em
    segundos — a rodada pode levar minutos com muitos anúncios, por isso não
    roda dentro do pedido HTTP). Modo desligado: não faz nada."""
    m = flex_config.modo()
    if m == flex_config.MODO_DESLIGADO:
        return FlexSincronizarOut(enfileirado=False, modo=m, motivo="flex_modo=desligado")
    if not flex_config.contas():
        return FlexSincronizarOut(enfileirado=False, modo=m, motivo="nenhuma conta em flex_contas")
    pool = await worker_pool.get_arq_pool()
    # Um clique por minuto vira UMA rodada (id do job pelo minuto).
    job = await pool.enqueue_job(
        "flex_motor_run", str(user.id), _job_id=f"flex_motor_manual:{int(time.time() // 60)}"
    )
    return FlexSincronizarOut(
        enfileirado=job is not None,
        modo=m,
        motivo=None if job is not None else "já há uma rodada pedida neste minuto",
    )


async def _estado_out(session: AsyncSession, integration_id: UUID, external_id: str):
    row = (
        await session.execute(
            select(FlexAnuncioEstado, Integration.name, _titulo_subq())
            .outerjoin(Integration, Integration.id == FlexAnuncioEstado.integration_id)
            .where(
                FlexAnuncioEstado.integration_id == integration_id,
                FlexAnuncioEstado.external_id == external_id,
            )
        )
    ).first()
    if row is None:
        return None
    est, nome, titulo = row
    return _anuncio_out(est, nome, titulo, await _permitidas(session))


_ERRO_HTTP = {
    "conta_nao_permitida": status.HTTP_403_FORBIDDEN,
    "nao_avaliado": status.HTTP_404_NOT_FOUND,
}


@router.post(
    "/anuncios/{integration_id}/{external_id}/aprovar", response_model=FlexAprovarOut
)
async def aprovar(
    integration_id: UUID, external_id: str, session: Sessao, user: Agir
) -> FlexAprovarOut:
    """Aprova LIGAR o Flex neste anúncio (a orientação do ML é que a ativação
    seja decisão deliberada do vendedor). Em piloto/ativo o motor aplica na
    hora; em observar fica registrada. Depois de uma recusa da plataforma,
    é também o "tente de novo". Conta fora da equipe do usuário: 403."""
    escopo = await _escopo(session, user)
    if escopo is not None and integration_id not in escopo:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "fora_do_escopo", "detalhe": "a conta não é da sua equipe"},
        )
    try:
        res = await flex_motor.aprovar(integration_id, external_id.strip(), por=user.id)
    except flex_motor.FlexRegraError as exc:
        raise HTTPException(
            _ERRO_HTTP.get(exc.codigo, status.HTTP_409_CONFLICT),
            detail={"code": exc.codigo, "detalhe": exc.detalhe},
        ) from exc
    na_fila = False
    if res.get("ocupado"):
        # A rodada do motor estava no meio (segura a trava de 4 a 8 min a cada
        # 15): em vez de "tenta na próxima rodada" — que esperava a fila
        # inteira de desligar —, um job tenta de novo daqui a pouco, só para
        # este anúncio. Um pedido por anúncio (o id do job).
        ext = external_id.strip()
        try:
            pool = await worker_pool.get_arq_pool()
            job = await pool.enqueue_job(
                "flex_aprovado_run",
                str(integration_id),
                ext,
                str(user.id),
                _job_id=f"flex_aprovado:{integration_id}:{ext}",
                _defer_by=30,
            )
            na_fila = True
            if job is None:
                logger.info("flex_aprovado_ja_na_fila", external_id=ext)
        except Exception as exc:  # noqa: BLE001 — sem fila: a varredura liga
            logger.warning("flex_aprovado_fila_falhou", erro=str(exc)[:200])
    session.expire_all()
    return FlexAprovarOut(
        aprovado=bool(res.get("aprovado")),
        modo=str(res.get("modo")),
        aplicado=bool(res.get("aplicado")),
        motor=res.get("motor"),
        na_fila=na_fila,
        estado=await _estado_out(session, integration_id, external_id.strip()),
    )


@router.post(
    "/anuncios/{integration_id}/{external_id}/desligar", response_model=FlexDesligarOut
)
async def desligar(
    integration_id: UUID, external_id: str, session: Sessao, user: Agir
) -> FlexDesligarOut:
    """Uma pessoa desliga o Flex deste anúncio agora ("Desligar selecionados",
    12/11/2026). Só em piloto/ativo e em conta pronta (o Flex que o sistema
    controla); tira a aprovação — volta a ligar só com outra. Conta fora da
    equipe do usuário: 403."""
    escopo = await _escopo(session, user)
    if escopo is not None and integration_id not in escopo:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "fora_do_escopo", "detalhe": "a conta não é da sua equipe"},
        )
    ext = external_id.strip()
    try:
        res = await flex_motor.desligar(integration_id, ext, por=user.id)
    except flex_motor.FlexRegraError as exc:
        raise HTTPException(
            _ERRO_HTTP.get(exc.codigo, status.HTTP_409_CONFLICT),
            detail={"code": exc.codigo, "detalhe": exc.detalhe},
        ) from exc
    session.expire_all()
    resultado = str(res.get("resultado"))
    return FlexDesligarOut(
        modo=str(res.get("modo")),
        resultado=resultado,
        desligado=resultado in ("ok", "ja_desligado"),
        estado=await _estado_out(session, integration_id, ext),
    )


def _emergencia_out(linha: FlexEmergencia, *, ja_em_andamento: bool = False) -> FlexEmergenciaOut:
    resumo = dict(linha.resumo or {})
    return FlexEmergenciaOut(
        id=linha.id,
        status=linha.status,
        modo=linha.modo,
        escreve=bool(resumo.get("escreve")),
        pedido_em=linha.pedido_em,
        iniciado_em=linha.iniciado_em,
        terminado_em=linha.terminado_em,
        erro=linha.erro,
        ja_em_andamento=ja_em_andamento,
        resumo=resumo,
    )


def _emergencias_visiveis(user: User, escopo: frozenset[UUID] | None):
    """Admin e quem não tem equipe vê todas; usuário com equipe, as que pediu."""
    return None if escopo is None else FlexEmergencia.por == user.id


# Uma emergência "na fila" há mais que isto, ou "rodando" sem andamento
# gravado há mais que isto (o job grava a cada poucos segundos), é de um job
# que morreu (worker reiniciado no meio): o botão volta a criar outra.
_EMERGENCIA_VALE = timedelta(minutes=10)


@router.post("/emergencia", response_model=FlexEmergenciaOut)
async def emergencia(session: Sessao, user: Agir) -> FlexEmergenciaOut:
    """Desliga o Flex de tudo nas contas permitidas e tira toda aprovação.

    O pedido HTTP só tira as aprovações (na hora — nada volta a ligar) e põe
    na fila o job do worker (`flex_emergencia_run`) que desliga. Antes as
    escritas rodavam aqui dentro, em série, até 300 por clique: minutos com o
    botão girando e 7 cliques numa conta grande. A tela consulta o andamento
    em GET /emergencia/{id}. Em observar (ou desligado) só simula — sem
    efeito nenhum. Usuário com equipe: só as contas da equipe. Já há uma
    emergência na fila ou rodando: devolve ela (não cria outra)."""
    escopo = await _escopo(session, user)
    logger.warning(
        "flex_emergencia_pedida",
        user_id=str(user.id),
        contas_escopo=None if escopo is None else len(escopo),
    )
    corte = datetime.now(UTC) - _EMERGENCIA_VALE
    filtros = [
        or_(
            and_(FlexEmergencia.status == "na_fila", FlexEmergencia.pedido_em >= corte),
            and_(FlexEmergencia.status == "rodando", FlexEmergencia.atualizado_em >= corte),
        )
    ]
    if (f := _emergencias_visiveis(user, escopo)) is not None:
        filtros.append(f)
    andamento = (
        await session.execute(
            select(FlexEmergencia)
            .where(*filtros)
            .order_by(FlexEmergencia.pedido_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if andamento is not None:
        return _emergencia_out(andamento, ja_em_andamento=True)

    prep = await flex_motor.preparar_emergencia(por=user.id, contas_escopo=escopo)
    if prep.get("id") is None:
        resumo = {k: v for k, v in prep.items() if k != "id"}
        return FlexEmergenciaOut(
            modo=str(prep.get("modo")), escreve=bool(prep.get("escreve")), resumo=resumo
        )
    emergencia_id = int(prep["id"])
    try:
        pool = await worker_pool.get_arq_pool()
        await pool.enqueue_job(
            "flex_emergencia_run", emergencia_id, _job_id=f"flex_emergencia:{emergencia_id}"
        )
    except Exception as exc:  # noqa: BLE001 — sem fila: avisa (as aprovações já saíram)
        logger.error("flex_emergencia_fila_falhou", id=emergencia_id, erro=str(exc)[:200])
        linha = await session.get(FlexEmergencia, emergencia_id)
        if linha is not None:
            linha.status = "falhou"
            linha.erro = f"não deu para pôr o job na fila: {str(exc)[:300]}"
            linha.terminado_em = datetime.now(UTC)
            await session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "fila_indisponivel",
                "detalhe": "as aprovações já saíram, mas o desligamento não começou",
            },
        ) from exc
    linha = await session.get(FlexEmergencia, emergencia_id)
    return _emergencia_out(linha)


@router.get("/emergencia/ultima", response_model=FlexEmergenciaOut | None)
async def emergencia_ultima(session: Sessao, user: Ver) -> FlexEmergenciaOut | None:
    """A última emergência que o usuário pode ver — a tela retoma o
    andamento depois de recarregar a página."""
    q = select(FlexEmergencia).order_by(FlexEmergencia.pedido_em.desc()).limit(1)
    if (f := _emergencias_visiveis(user, await _escopo(session, user))) is not None:
        q = q.where(f)
    linha = (await session.execute(q)).scalar_one_or_none()
    return None if linha is None else _emergencia_out(linha)


@router.get("/emergencia/{emergencia_id}", response_model=FlexEmergenciaOut)
async def emergencia_andamento(
    emergencia_id: int, session: Sessao, user: Ver
) -> FlexEmergenciaOut:
    q = select(FlexEmergencia).where(FlexEmergencia.id == emergencia_id)
    if (f := _emergencias_visiveis(user, await _escopo(session, user))) is not None:
        q = q.where(f)
    linha = (await session.execute(q)).scalar_one_or_none()
    if linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "emergencia_nao_encontrada"})
    return _emergencia_out(linha)


@router.get("/log", response_model=list[FlexLogOut])
async def log(
    session: Sessao,
    user: Ver,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    integration_id: UUID | None = None,
    external_id: str | None = None,
    acao: str | None = None,
) -> list[FlexLogOut]:
    if acao is not None and acao not in FLEX_ACOES:
        raise HTTPException(422, detail={"code": "acao_invalida"})
    filtros = []
    if (f := _no_escopo(FlexLog.integration_id, await _escopo(session, user))) is not None:
        filtros.append(f)
    if integration_id is not None:
        filtros.append(FlexLog.integration_id == integration_id)
    if external_id:
        filtros.append(FlexLog.external_id == external_id.strip())
    if acao:
        filtros.append(FlexLog.acao == acao)
    rows = await session.execute(
        select(FlexLog, Integration.name)
        .outerjoin(Integration, Integration.id == FlexLog.integration_id)
        .where(*filtros)
        .order_by(FlexLog.criado_em.desc(), FlexLog.id.desc())
        .limit(limit)
    )
    out: list[FlexLogOut] = []
    for linha, nome in rows.all():
        item = FlexLogOut.model_validate(linha)
        item.conta = nome
        out.append(item)
    return out


def _pedido_no_escopo(scope):
    """Pedido Flex da equipe: pela conta gravada OU pela loja do pedido no
    Bling (o pedido marcado só pela Logística ainda não tem conta)."""
    if scope.unrestricted:
        return None
    ors = []
    if scope.integration_ids:
        ors.append(FlexPedido.integration_id.in_(list(scope.integration_ids)))
    if scope.bling_store_ids:
        ors.append(
            exists().where(
                BlingOrder.bling_id == FlexPedido.bling_id,
                BlingOrder.loja.in_(list(scope.bling_store_ids)),
            )
        )
    return or_(*ors) if ors else false()


def _saiu_sem_sp():
    """Saiu (em andamento/atendido) sem ter ido ao .sp e sem o acerto do
    estoque: continua na lista (e no desconto do saldo Flex). Filtro largo do
    banco — a conferência pelos SKUs (só conta peça com lote fora do .sp) é
    `flex_motor.acerto_pendente`, na resposta."""
    return and_(
        FlexPedido.no_sp.is_(False),
        FlexPedido.acertado_em.is_(None),
        exists().where(
            BlingOrder.bling_id == FlexPedido.bling_id,
            BlingOrder.situacao.in_(flex_motor.SITUACOES_SAIU),
        ),
    )


@router.get("/pedidos", response_model=list[FlexPedidoOut])
async def pedidos(
    session: Sessao,
    user: Ver,
    so_alerta: bool = False,
    # Só os que ainda esperam alguém: some o pedido que no Bling já saiu (em
    # andamento), foi atendido, cancelado ou excluído — a régua do saldo Flex.
    # Pedido que o espelho ainda não tem continua (não se sabe: mostra). O que
    # SAIU sem passar pelo .sp continua até o acerto do estoque.
    abertos: bool = False,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> list[FlexPedidoOut]:
    """Pedidos Flex detectados nos últimos `dias`, os com aviso (o .sp não
    cobre — etapa 2) primeiro, depois pelo prazo de despacho. `so_alerta`:
    os com aviso E os que saíram sem passar pelo .sp (acerto pendente) —
    estes, sem o corte de dias: valem até alguém acertar."""
    corte = datetime.now(UTC) - timedelta(days=dias)
    filtros = [or_(FlexPedido.detectado_em >= corte, _saiu_sem_sp())]
    if (f := _pedido_no_escopo(await resolve_team_scope(session, user))) is not None:
        filtros.append(f)
    if so_alerta:
        filtros.append(or_(FlexPedido.alerta.is_not(None), _saiu_sem_sp()))
    if abertos:
        filtros.append(
            or_(
                ~exists().where(
                    BlingOrder.bling_id == FlexPedido.bling_id,
                    BlingOrder.situacao.in_(flex_motor.SITUACOES_FECHADAS),
                ),
                _saiu_sem_sp(),
            )
        )
    rows = await session.execute(
        select(FlexPedido, Integration.name)
        .outerjoin(Integration, Integration.id == FlexPedido.integration_id)
        .where(and_(*filtros))
        .order_by(
            case((FlexPedido.alerta.is_not(None), 0), else_=1),
            FlexPedido.prazo.asc().nulls_last(),
            FlexPedido.detectado_em.desc(),
        )
        .limit(limit)
    )
    linhas = rows.all()
    ids = [fp.bling_id for fp, _ in linhas]
    itens: dict[int, dict] = {}
    if ids:
        for r in (
            await session.execute(
                select(
                    BlingOrder.bling_id,
                    BlingOrder.numero,
                    BlingOrder.situacao,
                    BlingOrder.item_codigo,
                )
                .where(BlingOrder.bling_id.in_(ids))
                .order_by(BlingOrder.bling_id, BlingOrder.item_index)
            )
        ).all():
            d = itens.setdefault(
                int(r.bling_id), {"numero": r.numero, "situacao": r.situacao, "skus": []}
            )
            if r.item_codigo:
                d["skus"].append(r.item_codigo)
    out: list[FlexPedidoOut] = []
    # Conta com a saída do Flex em outra cidade (12/11/2026): não espera acerto.
    fora = await flex_local.contas_fora_do_local(session) if linhas else frozenset()
    for fp, nome in linhas:
        info = itens.get(fp.bling_id, {})
        skus = info.get("skus", [])
        pendente = flex_motor.acerto_pendente(
            info.get("situacao"),
            fp.no_sp,
            fp.acertado_em,
            skus,
            fp.lote,
            do_local=fp.integration_id not in fora,
        )
        # O banco trouxe largo (`_saiu_sem_sp`); aqui só fica o que espera
        # mesmo o acerto — o resto segue as réguas de sempre.
        if not pendente:
            if fp.detectado_em < corte:
                continue
            if so_alerta and fp.alerta is None:
                continue
            if abertos and info.get("situacao") in flex_motor.SITUACOES_FECHADAS:
                continue
        out.append(
            FlexPedidoOut(
                bling_id=fp.bling_id,
                numero=info.get("numero"),
                plataforma=fp.plataforma,
                integration_id=fp.integration_id,
                conta=nome,
                numeroloja=fp.numeroloja,
                envio_tipo=fp.envio_tipo,
                prazo=fp.prazo,
                detectado_em=fp.detectado_em,
                no_sp=fp.no_sp,
                lote=fp.lote or "sp",
                alerta=fp.alerta,
                situacao=info.get("situacao"),
                skus=skus,
                acerto_pendente=pendente,
                acertado_em=fp.acertado_em,
            )
        )
    return out


@router.post("/pedidos/{bling_id}/acertado", response_model=FlexAcertoOut)
async def acertado(bling_id: int, session: Sessao, user: Agir) -> FlexAcertoOut:
    """A pessoa acertou no Bling o estoque do pedido Flex que saiu de São
    Bernardo sem passar pelo .sp (a transferência do lote vendido para o .sp):
    o saldo Flex para de descontar o pedido. Só para pedido que saiu."""
    escopo = await resolve_team_scope(session, user)
    filtros = [FlexPedido.bling_id == bling_id]
    if (f := _pedido_no_escopo(escopo)) is not None:
        filtros.append(f)
    fp = (await session.execute(select(FlexPedido).where(*filtros))).scalar_one_or_none()
    if fp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "pedido_nao_encontrado"})
    itens = (
        await session.execute(
            select(BlingOrder.situacao, BlingOrder.item_codigo).where(
                BlingOrder.bling_id == bling_id
            )
        )
    ).all()
    situacao = itens[0][0] if itens else None
    do_local = fp.integration_id not in await flex_local.contas_fora_do_local(session)
    if not flex_motor.acerto_pendente(
        situacao, fp.no_sp, fp.acertado_em, [c for _, c in itens], fp.lote, do_local=do_local
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "sem_acerto_pendente",
                "detalhe": (
                    f"o pedido não saiu sem passar pelo .{fp.lote or 'sp'} (ou já foi acertado)"
                ),
            },
        )
    agora = datetime.now(UTC)
    fp.acertado_em = agora
    fp.acertado_por = user.id
    fp.atualizado_em = agora
    session.add(
        FlexLog(
            integration_id=fp.integration_id,
            plataforma=fp.plataforma,
            bling_id=fp.bling_id,
            sku=", ".join(c for _, c in itens if c) or None,
            acao="acertar_estoque",
            modo=flex_config.modo(),
            resultado="ok",
            motivo=(
                "estoque do pedido Flex acertado no Bling "
                f"(saiu sem passar pelo .{fp.lote or 'sp'})"
            ),
            por=user.id,
        )
    )
    await session.commit()
    return FlexAcertoOut(bling_id=bling_id, acertado_em=agora)
