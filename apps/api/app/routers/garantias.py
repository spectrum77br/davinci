"""Pós-venda › Garantias — Painel de Garantia Uranyx (07/10/2026).

Documento "Painel de Garantia — Uranyx (DaVinci)". As regras moram em
`services/garantia.py` (com os pontos do §8 decididos pelo dono); aqui ficam
permissão, escopo e o formato das respostas.

Permissões (§6 — Cadastrar, Consultar, Registrar atendimento; e o CPF):
  * `garantias` view = Consultar (lista, detalhe, anexos); edit = Cadastrar
    (cadastra, corrige o que foi informado, confere a entrega agora).
  * `garantias_atendimento` edit = Registrar atendimento ("Vincular à
    garantia" no Comunicador). Quem tem só ela busca a garantia para vincular
    e vê o alerta, sem abrir o painel. O `view` sozinho dessa linha não libera
    nada (o documento não tem esse nível).
  * `garantias_cpf` view = vê o CPF COMPLETO no detalhe (a lista é sempre
    mascarada). Admin tem as três. Só o GET do detalhe devolve o CPF completo
    — e grava "consultou"; cadastrar/corrigir/conferir respondem mascarado.
  * O log de quem consultou/alterou (`/{id}/log`) é do admin.

LGPD no transporte: a lista e a busca do vínculo são POST com o termo no
CORPO (nome e CPF digitados não vão para o log de acesso do uvicorn/Caddy,
que gravam a query); `mascarar_query_no_access_log` cobre o resto. A busca
pelo CPF completo tem teto por pessoa (`svc.LIMITES_BUSCA_CPF`) e cada
garantia achada ganha "buscou pelo CPF" no log dela.

Escopo por equipe: a garantia guarda a loja do pedido (`bling_orders.loja`) e
quem tem equipe vê só as lojas dela (como o Reembolso). A conversa do
Comunicador segue o escopo dela (integração) e a trava de quem LÊ a caixa
(`atendimento.acesso.pode_ver`). Nada aqui grava nas tabelas do /atendimento:
a garantia guarda uma CÓPIA do atendimento.
"""

import logging
import re
from datetime import UTC, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import false, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import (
    require_active_user,
    require_admin,
    require_permission,
)
from app.deps.team_scope import TeamScope, resolve_team_scope
from app.models import (
    Garantia,
    GarantiaAtendimento,
    GarantiaAtendimentoAnexo,
    GarantiaLog,
    User,
    UserRole,
)
from app.models.atendimento import AtendimentoConversa
from app.schemas.garantia import (
    AnexoOut,
    AtendimentoCreate,
    AtendimentoOut,
    BuscaVinculo,
    EntregaOut,
    GarantiaCreate,
    GarantiaLinhaOut,
    GarantiaListaOut,
    GarantiaSalvaOut,
    GarantiaUpdate,
    IndicadoresOut,
    ItemOut,
    ListaFiltro,
    LogOut,
    NotaSugeridaOut,
    PedidoParaCadastroOut,
    PermissoesOut,
    PessoaRef,
    PrazoOut,
    PrazosPreviaOut,
    RegrasOut,
    SituacaoConversaOut,
    VinculoOut,
)
from app.services import garantia as svc
from app.services.atendimento import acesso
from app.services.atendimento.contexto import _chaves_do_pedido
from app.services.rate_limit import RateLimitError, sliding_window_check

logger = structlog.get_logger()

router = APIRouter(prefix="/api/garantias", tags=["garantias"])

_consultar = require_permission("garantias", "view")
_cadastrar = require_permission("garantias", "edit")
_registrar = require_permission("garantias_atendimento", "edit")


def _pode(user: User, recurso: str, acao: str) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    return bool(((user.permissions or {}).get(recurso) or {}).get(acao))


async def _consultar_ou_registrar(user: Annotated[User, Depends(require_active_user)]) -> User:
    """Buscar para vincular, o bloco da conversa e as regras: quem CONSULTA
    (`garantias` view) ou REGISTRA atendimento (`garantias_atendimento`
    EDIT). O `view` sozinho de `garantias_atendimento` (a tela de Permissões
    mostra a caixinha) não libera nome/NF/pedido de ninguém."""
    if _pode(user, "garantias", "view") or _pode(user, "garantias_atendimento", "edit"):
        return user
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail={"code": "forbidden", "resource": "garantias", "action": "view"},
    )


def _ve_cpf(user: User) -> bool:
    return _pode(user, "garantias_cpf", "view")


def _erro(codigo: int, code: str, campo: str | None = None, **extra) -> HTTPException:
    """`campo` diz à tela embaixo de qual campo mostrar a mensagem."""
    detail = {"code": code, **extra}
    if campo:
        detail["campo"] = campo
    return HTTPException(codigo, detail=detail)


# ── Escopo ──────────────────────────────────────────────────────────────────


def _clausula(scope: TeamScope):
    if scope.unrestricted:
        return None
    if not scope.bling_store_ids:
        return false()
    return Garantia.loja.in_(scope.bling_store_ids)


def _pedido_no_escopo(scope: TeamScope, loja: str | None) -> bool:
    return scope.unrestricted or (loja is not None and loja in scope.bling_store_ids)


def _conversa_no_escopo(scope: TeamScope, integration_id: UUID | None) -> bool:
    return scope.unrestricted or (
        integration_id is not None and integration_id in scope.integration_ids
    )


async def _garantia_visivel(
    session: AsyncSession, garantia_id: int, user: User, *, travar: bool = False
) -> Garantia:
    g = await session.get(Garantia, garantia_id, with_for_update=travar)
    scope = await resolve_team_scope(session, user)
    if g is None or not _pedido_no_escopo(scope, g.loja):
        raise _erro(404, "garantia_nao_encontrada")
    return g


async def _conversa(session: AsyncSession, conversa_id: str, user: User) -> AtendimentoConversa:
    if not acesso.pode_ver(user):
        raise _erro(403, "atendimento_restrito")
    try:
        uid = UUID(str(conversa_id))
    except ValueError as e:  # Instagram ("ig:…") e lixo
        raise _erro(404, "conversa_nao_encontrada", "conversa_id") from e
    c = await session.get(AtendimentoConversa, uid)
    scope = await resolve_team_scope(session, user)
    if c is None or not _conversa_no_escopo(scope, c.integration_id):
        raise _erro(404, "conversa_nao_encontrada", "conversa_id")
    return c


# ── Montagem das respostas ──────────────────────────────────────────────────


async def _pessoas(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, User]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u for u in (await session.scalars(select(User).where(User.id.in_(ids)))).all()}


def _ref(pessoas: dict[UUID, User], uid: UUID | None) -> PessoaRef | None:
    if uid is None:
        return None
    return PessoaRef(id=uid, nome=svc.nome_pessoa(pessoas.get(uid)))


async def _contagem_atendimentos(session: AsyncSession, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    linhas = await session.execute(
        select(GarantiaAtendimento.garantia_id, func.count())
        .where(GarantiaAtendimento.garantia_id.in_(ids))
        .group_by(GarantiaAtendimento.garantia_id)
    )
    return dict(linhas.all())


def _linha(g: Garantia, n_atend: int, dia, situacao_bling: str | None = None) -> GarantiaLinhaOut:
    st = svc.status_da(g, dia)
    sem_data = svc.entregue_sem_data(g.data_inicio, situacao_bling)
    return GarantiaLinhaOut(
        id=g.id,
        cliente_nome=g.cliente_nome,
        cpf_mascarado=svc.mascarar_cpf(g.cpf),
        nf_numero=g.nf_numero,
        nf_serie=g.nf_serie,
        pedido_bling=g.pedido_bling,
        pedido_marketplace=g.pedido_marketplace,
        plataforma=g.plataforma,
        conta=g.conta,
        data_inicio=g.data_inicio,
        fim_hardware=g.fim_hardware,
        fim_software=g.fim_software,
        status=st,
        status_rotulo=svc.rotulo_do_status(st, sem_data),
        entregue_sem_data=sem_data,
        atendimentos=n_atend,
        criado_em=g.criado_em,
    )


async def _situacoes(session: AsyncSession, garantias: list[Garantia]) -> dict[str, str]:
    """A situação no Bling só das que estão sem data (as outras não precisam)."""
    return await svc.situacoes_dos_pedidos(
        session, [g.pedido_bling for g in garantias if g.data_inicio is None]
    )


async def _linhas(session: AsyncSession, garantias: list[Garantia], dia) -> list[GarantiaLinhaOut]:
    contagem = await _contagem_atendimentos(session, [g.id for g in garantias])
    situacoes = await _situacoes(session, garantias)
    return [_linha(g, contagem.get(g.id, 0), dia, situacoes.get(g.pedido_bling)) for g in garantias]


def _entrega(g: Garantia) -> EntregaOut:
    return EntregaOut(
        data=g.data_inicio,
        origem=g.entrega_origem,
        origem_rotulo=svc.ORIGEM_ROTULOS.get(g.entrega_origem) if g.entrega_origem else None,
        em=g.entrega_em,
        verificada_em=g.entrega_verificada_em,
    )


def _prazo(inicio, fim, dia) -> PrazoOut | None:
    info = svc.prazo_info(inicio, fim, dia)
    return PrazoOut(**info) if info else None


def _arquivo_url(anexo_id: int) -> str:
    return f"/api/garantias/anexos/{anexo_id}"


def _anexos_out(
    itens: list[dict], arquivos: list[tuple[int, UUID | None, str | None, str, int]]
) -> list[AnexoOut]:
    """Junta o que a conversa tinha (JSON do atendimento) com os arquivos
    guardados (pela mensagem + URL)."""
    por_chave = {(str(m) if m else None, u): (i, ct, t) for i, m, u, ct, t in arquivos}
    saida = []
    for item in itens or []:
        achado = por_chave.get((item.get("mensagem_id"), item.get("url")))
        saida.append(
            AnexoOut(
                mensagem_id=item.get("mensagem_id"),
                tipo=str(item.get("tipo") or "arquivo"),
                nome=item.get("nome"),
                url_original=item.get("url"),
                baixado=achado is not None,
                motivo=None if achado else item.get("motivo"),
                arquivo_url=_arquivo_url(achado[0]) if achado else None,
                content_type=achado[1] if achado else None,
                tamanho=achado[2] if achado else None,
            )
        )
    return saida


def _atendimento_out(
    a: GarantiaAtendimento,
    g: Garantia,
    arquivos: list[tuple[int, UUID | None, str | None, str, int]],
) -> AtendimentoOut:
    hoje_cob, _fim = svc.cobertura_em(g, a.tipo_problema, a.data_atendimento)
    return AtendimentoOut(
        id=a.id,
        garantia_id=a.garantia_id,
        data_atendimento=a.data_atendimento,
        atendente=PessoaRef(id=a.atendente_id, nome=a.atendente_nome),
        conversa_id=a.conversa_id,
        conversa_link=a.conversa_link,
        conversa_plataforma=a.conversa_plataforma,
        conversa_canal=a.conversa_canal,
        conversa_conta=a.conversa_conta,
        conversa_pedido=a.conversa_pedido,
        resumo=a.resumo,
        mensagens=list(a.mensagens or []),
        anexos=_anexos_out(a.anexos, arquivos),
        tipo_problema=a.tipo_problema,
        cobertura=a.cobertura,
        cobertura_rotulo=svc.COBERTURA_ROTULOS.get(a.cobertura, a.cobertura),
        fim_considerado=a.fim_considerado,
        cobertura_hoje=hoje_cob,
        cobertura_hoje_rotulo=svc.COBERTURA_ROTULOS[hoje_cob],
        solucao=a.solucao,
        criado_em=a.criado_em,
    )


async def _arquivos_dos(
    session: AsyncSession, atendimento_ids: list[int]
) -> dict[int, list[tuple[int, UUID | None, str | None, str, int]]]:
    if not atendimento_ids:
        return {}
    ax = GarantiaAtendimentoAnexo
    linhas = await session.execute(
        select(
            ax.atendimento_id, ax.id, ax.mensagem_id, ax.url_original, ax.content_type, ax.tamanho
        )
        .where(ax.atendimento_id.in_(atendimento_ids))
        .order_by(ax.id)
    )
    saida: dict[int, list] = {}
    for at_id, *resto in linhas.all():
        saida.setdefault(at_id, []).append(tuple(resto))
    return saida


async def _atendimentos(session: AsyncSession, g: Garantia) -> list[AtendimentoOut]:
    lista = list(
        (
            await session.execute(
                select(GarantiaAtendimento)
                .where(GarantiaAtendimento.garantia_id == g.id)
                .order_by(
                    GarantiaAtendimento.data_atendimento.desc(), GarantiaAtendimento.id.desc()
                )
            )
        ).scalars()
    )
    arquivos = await _arquivos_dos(session, [a.id for a in lista])
    return [_atendimento_out(a, g, arquivos.get(a.id, [])) for a in lista]


async def _detalhe(
    session: AsyncSession,
    g: Garantia,
    user: User,
    *,
    avisos: list[str] | None = None,
    revelar_cpf: bool = False,
) -> GarantiaSalvaOut:
    """`revelar_cpf`: só o GET do detalhe (que grava "consultou") passa True
    — e só para quem tem `garantias_cpf`."""
    await session.refresh(g)
    dia = svc.hoje()
    atend = await _atendimentos(session, g)
    pessoas = await _pessoas(session, {g.criado_por, g.atualizado_por})
    ve_cpf = _ve_cpf(user)
    completo = revelar_cpf and ve_cpf
    situacoes = await _situacoes(session, [g])
    base = _linha(g, len(atend), dia, situacoes.get(g.pedido_bling)).model_dump()
    return GarantiaSalvaOut(
        **base,
        cpf=svc.formatar_cpf(g.cpf) if completo else svc.mascarar_cpf(g.cpf),
        cpf_completo=completo,
        nf_chave=g.nf_chave,
        nf_emitente_cnpj=g.nf_emitente_cnpj,
        itens=[ItemOut(**i) for i in (g.itens or [])],
        entrega=_entrega(g),
        hardware=_prazo(g.data_inicio, g.fim_hardware, dia),
        software=_prazo(g.data_inicio, g.fim_software, dia),
        criado_por=_ref(pessoas, g.criado_por),
        atualizado_em=g.atualizado_em,
        atualizado_por=_ref(pessoas, g.atualizado_por),
        atendimentos_lista=atend,
        permissoes=PermissoesOut(
            cadastrar=_pode(user, "garantias", "edit"),
            registrar_atendimento=_pode(user, "garantias_atendimento", "edit"),
            ver_cpf=ve_cpf,
            ver_log=user.role == UserRole.ADMIN,
        ),
        avisos=avisos or [],
    )


def _nota_out(n: svc.NotaDoPedido | None) -> NotaSugeridaOut | None:
    if n is None:
        return None
    return NotaSugeridaOut(
        numero=n.numero,
        serie=n.serie,
        chave=n.chave,
        emitida_em=n.emitida_em,
        valor=n.valor,
        papel=n.papel,
    )


# ── Busca ───────────────────────────────────────────────────────────────────


def _filtro_busca(termo: str | None):
    """Nome (sem acento nem caixa), CPF (os 11 dígitos, válido), NF ou pedido
    (Bling ou marketplace). CPF parcial não busca: a lista mostra o CPF
    mascarado e pedaço de CPF serviria para adivinhar os dígitos escondidos."""
    t = (termo or "").strip()
    if not t:
        return None
    digitos = svc.so_digitos(t)
    conds = [
        svc.sem_acento_sql(Garantia.cliente_nome).contains(svc.sem_acento(t), autoescape=True),
        Garantia.pedido_bling == t,
        Garantia.pedido_marketplace == t,
    ]
    if digitos:
        if cpf := svc.cpf_da_busca(t):
            conds.append(Garantia.cpf == cpf)
        nf = svc.normalizar_nf(digitos)
        if nf and len(nf) <= 9:
            conds.append(Garantia.nf_numero == nf)
        conds.append(Garantia.pedido_bling == digitos)
    return or_(*conds)


async def _indicadores(session: AsyncSession, escopo, dia) -> IndicadoresOut:
    base = [escopo] if escopo is not None else []
    contar = {
        "ativas": svc.filtro_status(svc.STATUS_ATIVA, dia),
        "somente_software": svc.filtro_status(svc.STATUS_SOMENTE_SOFTWARE, dia),
        "hw_vence_30d": svc.filtro_status("hw_vence_30d", dia),
        "aguardando_entrega": svc.filtro_status(svc.STATUS_AGUARDANDO, dia),
        "entregue_sem_data": svc.filtro_status("entregue_sem_data", dia),
        "expiradas": svc.filtro_status(svc.STATUS_EXPIRADA, dia),
    }
    linha = (
        await session.execute(
            select(
                func.count().label("total"),
                *[func.count().filter(f).label(k) for k, f in contar.items()],
            )
            .select_from(Garantia)
            .where(*base)
        )
    ).one()
    ini, fim = svc.mes_atual(dia)
    q = (
        select(func.count())
        .select_from(GarantiaAtendimento)
        .join(Garantia, Garantia.id == GarantiaAtendimento.garantia_id)
        .where(
            GarantiaAtendimento.data_atendimento >= ini,
            GarantiaAtendimento.data_atendimento < fim,
            *base,
        )
    )
    return IndicadoresOut(
        **{k: getattr(linha, k) for k in contar},
        total=linha.total,
        atendimentos_no_mes=(await session.execute(q)).scalar_one(),
    )


# ── Log de acesso ───────────────────────────────────────────────────────────


def mascarar_query_no_access_log() -> None:
    """Filtro no logger de acesso do uvicorn (molde do
    `claude_conector.mascarar_token_no_access_log`): todo valor de query em
    /api/garantias vira *** antes de ir para o log. A lista e a busca já são
    POST (o termo vai no corpo); isto cobre o que sobrar (ex.: `?numero=` do
    pedido, ou alguém chamando com query na mão)."""

    class _Mascara(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            args = record.args
            if (
                isinstance(args, tuple)
                and len(args) >= 3
                and isinstance(args[2], str)
                and args[2].startswith("/api/garantias")
                and "?" in args[2]
            ):
                caminho, query = args[2].split("?", 1)
                partes = [f"{p.split('=', 1)[0]}=***" for p in query.split("&") if p]
                record.args = (*args[:2], f"{caminho}?{'&'.join(partes)}", *args[3:])
            return True

    logging.getLogger("uvicorn.access").addFilter(_Mascara())


# ── Rotas fixas (antes de /{garantia_id}) ───────────────────────────────────


async def _teto_da_busca_por_cpf(user: User) -> None:
    """LIMITES_BUSCA_CPF por pessoa (a máscara deixa só 1.000 candidatos).
    Redis fora do ar não trava o atendimento — como o conector do Claude:
    segue, com aviso no log."""
    for limite, janela in svc.LIMITES_BUSCA_CPF:
        try:
            await sliding_window_check(
                key=f"garantias:busca_cpf:{user.id}:{janela}", limit=limite, window_seconds=janela
            )
        except RateLimitError as e:
            erro = _erro(429, "muitas_buscas_por_cpf", retry_after=e.retry_after)
            erro.headers = {"Retry-After": str(e.retry_after)}
            raise erro from None
        except Exception as e:  # noqa: BLE001 — Redis fora do ar não bloqueia
            logger.warning("garantias_busca_cpf_sem_teto", err=type(e).__name__)
            return


async def _registrar_busca_por_cpf(
    session: AsyncSession, user: User, cpf: str, escopo, onde: str
) -> None:
    """Cada garantia (que a pessoa enxerga) com esse CPF ganha a linha
    "buscou pelo CPF" no log dela — CPF mascarado."""
    q = select(Garantia.id).where(Garantia.cpf == cpf)
    if escopo is not None:
        q = q.where(escopo)
    ids = list((await session.execute(q)).scalars())
    for gid in ids:
        svc.registrar_log(
            session, gid, "buscou_cpf", user, detalhe=f"{svc.mascarar_cpf(cpf)} · {onde}"
        )
    if ids:
        await session.commit()


@router.post("/lista", response_model=GarantiaListaOut)
async def listar(
    body: ListaFiltro,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar)],
) -> GarantiaListaOut:
    """A tela principal (§4.1). CPF sempre mascarado. Os indicadores do topo
    contam tudo o que a pessoa enxerga (sem os filtros), como os cartões que
    filtram ao clicar. POST: a busca (nome/CPF) vai no corpo, fora do log de
    acesso."""
    dia = svc.hoje()
    escopo = _clausula(await resolve_team_scope(session, user))
    cpf = svc.cpf_da_busca(body.busca)
    if cpf:
        await _teto_da_busca_por_cpf(user)
    filtros = [escopo] if escopo is not None else []
    if (f := _filtro_busca(body.busca)) is not None:
        filtros.append(f)
    if body.status != "todos":
        filtros.append(svc.filtro_status(body.status, dia))
    de, ate = body.de, body.ate
    if de or ate:
        if body.periodo == "entrega":
            if de:
                filtros.append(Garantia.data_inicio >= de)
            if ate:
                filtros.append(Garantia.data_inicio <= ate)
        else:
            # O dia do cadastro em São Paulo, de `de` a `ate` inclusive.
            if de:
                filtros.append(Garantia.criado_em >= datetime.combine(de, time(), svc.SAO_PAULO))
            if ate:
                fim = datetime.combine(ate + timedelta(days=1), time(), svc.SAO_PAULO)
                filtros.append(Garantia.criado_em < fim)
    if body.com_atendimento_no_mes:
        ini, fim = svc.mes_atual(dia)
        filtros.append(
            Garantia.id.in_(
                select(GarantiaAtendimento.garantia_id).where(
                    GarantiaAtendimento.data_atendimento >= ini,
                    GarantiaAtendimento.data_atendimento < fim,
                )
            )
        )
    total = (
        await session.execute(select(func.count()).select_from(Garantia).where(*filtros))
    ).scalar_one()
    garantias = list(
        (
            await session.execute(
                select(Garantia)
                .where(*filtros)
                .order_by(Garantia.criado_em.desc(), Garantia.id.desc())
                .limit(body.limite)
                .offset(body.offset)
            )
        ).scalars()
    )
    resposta = GarantiaListaOut(
        itens=await _linhas(session, garantias, dia),
        total=total,
        indicadores=await _indicadores(session, escopo, dia),
        hoje=dia,
    )
    if cpf:
        await _registrar_busca_por_cpf(session, user, cpf, escopo, "busca na lista")
    return resposta


@router.get("/regras", response_model=RegrasOut)
async def regras(_u: Annotated[User, Depends(_consultar_ou_registrar)]) -> RegrasOut:
    """Os prazos e os pontos decididos (§8), para a tela explicar a regra."""
    return RegrasOut(
        meses_hardware=svc.MESES_HARDWARE,
        meses_software=svc.MESES_SOFTWARE,
        ultimo_dia_coberto=svc.ULTIMO_DIA_COBERTO,
        cadastro_antes_da_entrega=svc.CADASTRO_ANTES_DA_ENTREGA,
        garantia_por=svc.GARANTIA_POR,
        vinculo_automatico=svc.VINCULO_AUTOMATICO,
        recalcular_quando_entrega_mudar=svc.RECALCULAR_QUANDO_ENTREGA_MUDAR,
        dias_alerta_hardware=svc.DIAS_ALERTA_HARDWARE,
        dias_pausa_atendimento=svc.DIAS_PAUSA_NOVO_ATENDIMENTO,
        status=svc.STATUS_ROTULOS,
        coberturas=svc.COBERTURA_ROTULOS,
        origens_entrega=svc.ORIGEM_ROTULOS,
    )


@router.get("/pedido", response_model=PedidoParaCadastroOut)
async def pedido_para_cadastro(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_cadastrar)],
    numero: Annotated[str, Query(min_length=1, max_length=64)],
) -> PedidoParaCadastroOut:
    """Ao informar o pedido: entrega, prazos, itens e as sugestões de NF,
    nome e CPF (a pessoa confere; os campos continuam informados por ela)."""
    pedido = await svc.buscar_pedido(session, numero)
    scope = await resolve_team_scope(session, user)
    if pedido is None or not _pedido_no_escopo(scope, pedido.loja):
        raise _erro(404, "pedido_nao_encontrado", "pedido")
    entrega = await svc.resolver_entrega(session, pedido.numero, pedido.numeroloja)
    dia = svc.hoje()
    inicio = entrega.dia if entrega else None
    fim_hw, fim_sw = svc.calcular_prazos(inicio) if inicio else (None, None)
    st = svc.status_em(inicio, fim_hw, fim_sw, dia)
    sem_data = svc.entregue_sem_data(inicio, pedido.situacao_id)
    nome, nome_origem = pedido.nome
    cpf = pedido.cpf
    existentes = await svc.garantias_do_cpf_ou_pedido(
        session, None, pedido.numero, _clausula(scope)
    )
    avisos = []
    if not pedido.produto_uranyx:
        avisos.append("pedido_sem_produto_uranyx")
    if entrega is None:
        avisos.append(pedido.aviso_sem_entrega)  # aguardando_entrega | entregue_sem_data
    if existentes:
        avisos.append("pedido_ja_tem_garantia")
    return PedidoParaCadastroOut(
        pedido_bling=pedido.numero,
        pedido_marketplace=pedido.numeroloja,
        plataforma=pedido.plataforma,
        conta=pedido.conta,
        data_pedido=pedido.data,
        situacao=pedido.situacao,
        itens=[ItemOut(**i) for i in pedido.itens],
        produto_uranyx=pedido.produto_uranyx,
        entrega=EntregaOut(
            data=inicio,
            origem=entrega.origem if entrega else None,
            origem_rotulo=svc.ORIGEM_ROTULOS.get(entrega.origem) if entrega else None,
            em=entrega.em if entrega else None,
        ),
        prazos=PrazosPreviaOut(
            data_inicio=inicio,
            fim_hardware=fim_hw,
            fim_software=fim_sw,
            status=st,
            status_rotulo=svc.rotulo_do_status(st, sem_data),
            entregue_sem_data=sem_data,
        ),
        notas=[_nota_out(n) for n in pedido.notas],
        nf_sugerida=_nota_out(pedido.nota_produto),
        nome_sugerido=nome,
        nome_origem=nome_origem,
        cpf_sugerido=svc.formatar_cpf(cpf) if cpf and _ve_cpf(user) else None,
        cpf_sugerido_mascarado=svc.mascarar_cpf(cpf),
        garantias_existentes=await _linhas(session, existentes, dia),
        avisos=avisos,
    )


@router.post("/busca", response_model=list[GarantiaLinhaOut])
async def buscar_para_vincular(
    body: BuscaVinculo,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar_ou_registrar)],
) -> list[GarantiaLinhaOut]:
    """Vincular à garantia (§5.1 passo 2): busca por CPF, NF ou pedido (e
    nome). Até 20, CPF mascarado. POST: o termo vai no corpo."""
    cpf = svc.cpf_da_busca(body.q)
    if cpf:
        await _teto_da_busca_por_cpf(user)
    filtros = [_filtro_busca(body.q)]
    escopo = _clausula(await resolve_team_scope(session, user))
    if escopo is not None:
        filtros.append(escopo)
    garantias = list(
        (
            await session.execute(
                select(Garantia).where(*filtros).order_by(Garantia.id.desc()).limit(20)
            )
        ).scalars()
    )
    resposta = await _linhas(session, garantias, svc.hoje())
    if cpf:
        await _registrar_busca_por_cpf(session, user, cpf, escopo, "busca do vínculo")
    return resposta


@router.get("/conversa/{conversa_id}", response_model=SituacaoConversaOut)
async def situacao_da_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar_ou_registrar)],
) -> SituacaoConversaOut:
    """O bloco "Garantia" da conversa: as garantias do CPF/pedido, a busca já
    preenchida e o alerta de CPF sem garantia (§5.3). O CPF nunca sai daqui:
    é resolvido no servidor pelo pedido da conversa."""
    c = await _conversa(session, conversa_id, user)
    pedido = None
    chaves = _chaves_do_pedido(c) if (c.pedido_marketplace or "").strip() else []
    for chave in chaves:
        pedido = await svc.buscar_pedido(session, chave)
        if pedido is not None:
            break
    cpf = pedido.cpf if pedido else None
    escopo = _clausula(await resolve_team_scope(session, user))
    garantias = await svc.garantias_do_cpf_ou_pedido(
        session, cpf, pedido.numero if pedido else None, escopo
    )
    # O alerta olha TODAS as garantias (não só as da equipe): CPF com
    # garantia em outra loja não é "sem garantia".
    cpf_tem = bool(cpf) and bool(
        await session.scalar(select(func.count()).where(Garantia.cpf == cpf))
    )
    q_vinc = (
        select(GarantiaAtendimento)
        .join(Garantia, Garantia.id == GarantiaAtendimento.garantia_id)
        .where(GarantiaAtendimento.conversa_id == c.id)
        .order_by(GarantiaAtendimento.data_atendimento.desc())
    )
    if escopo is not None:
        q_vinc = q_vinc.where(escopo)
    vinculos = list((await session.execute(q_vinc)).scalars())
    return SituacaoConversaOut(
        conversa_id=c.id,
        pedido_encontrado=pedido is not None,
        pedido_bling=pedido.numero if pedido else None,
        pedido_marketplace=pedido.numeroloja if pedido else None,
        produto_uranyx=bool(pedido and pedido.produto_uranyx),
        cpf_conhecido=cpf is not None,
        alerta_cpf_sem_garantia=bool(cpf and pedido and pedido.produto_uranyx and not cpf_tem),
        garantias=await _linhas(session, garantias, svc.hoje()),
        busca_sugerida=(pedido.numero if pedido else (chaves[0] if chaves else None)),
        vinculos=[
            VinculoOut(
                atendimento_id=a.id,
                garantia_id=a.garantia_id,
                data_atendimento=a.data_atendimento,
                tipo_problema=a.tipo_problema,
                cobertura=a.cobertura,
                cobertura_rotulo=svc.COBERTURA_ROTULOS.get(a.cobertura, a.cobertura),
            )
            for a in vinculos
        ],
    )


@router.get("/anexos/{anexo_id}")
async def baixar_anexo(
    anexo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar)],
) -> Response:
    a = await session.get(GarantiaAtendimentoAnexo, anexo_id)
    if a is None:
        raise _erro(404, "anexo_nao_encontrado")
    at = await session.get(GarantiaAtendimento, a.atendimento_id)
    try:
        await _garantia_visivel(session, at.garantia_id, user)
    except HTTPException as e:
        raise _erro(404, "anexo_nao_encontrado") from e
    nome = re.sub(r'["\\\r\n]', "", a.nome or f"anexo-{a.id}")
    tipo = (a.content_type or "application/octet-stream").lower()
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    # O arquivo veio do comprador: na origem do DaVinci (o /api é a mesma
    # origem da tela) nada dele pode rodar script. Foto e vídeo abrem na aba
    # com CSP sandbox; PDF abre sem (o leitor de PDF do Chrome não abre com
    # sandbox, e ele mesmo isola o PDF); o resto baixa — como as provas da
    # Denúncia. O download já recusa SVG (svc.tipo_de_anexo_aceito).
    if tipo == "application/pdf":
        disposicao = "inline"
    elif svc.tipo_de_anexo_aceito(tipo):
        disposicao = "inline"
        headers["Content-Security-Policy"] = "sandbox"
    else:
        disposicao = "attachment"
        headers["Content-Security-Policy"] = "sandbox"
    headers["Content-Disposition"] = f'{disposicao}; filename="{nome}"'
    return Response(content=a.blob, media_type=tipo, headers=headers)


@router.post("", response_model=GarantiaSalvaOut, status_code=status.HTTP_201_CREATED)
async def cadastrar(
    body: GarantiaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_cadastrar)],
) -> GarantiaSalvaOut:
    cpf, nf, serie, nome = _conferir_informados(
        body.cpf, body.nf_numero, body.nf_serie, body.cliente_nome
    )
    pedido = await _pedido_ou_422(session, body.pedido, user)
    nota = svc.nota_informada(pedido, nf, serie)
    serie = serie or (nota.serie if nota else "")
    scope = await resolve_team_scope(session, user)
    await _sem_duplicata(session, cpf, nf, serie, nota.chave if nota else None, None, scope)
    entrega = await svc.resolver_entrega(session, pedido.numero, pedido.numeroloja)
    if entrega is None and not svc.CADASTRO_ANTES_DA_ENTREGA:
        raise _erro(422, "pedido_sem_entrega", "pedido")
    g = Garantia(criado_por=user.id, cliente_nome=nome, cpf=cpf)
    _aplicar_pedido(g, pedido, nf, serie, nota)
    svc.aplicar_entrega(g, entrega)
    g.entrega_verificada_em = datetime.now(UTC)
    session.add(g)
    try:
        await session.flush()
    except IntegrityError as e:
        # Duas pessoas cadastrando a mesma NF ao mesmo tempo.
        await session.rollback()
        raise _erro(409, "garantia_duplicada", "nf_numero") from e
    svc.registrar_log(
        session,
        g.id,
        "cadastrou",
        user,
        detalhe=f"pedido {g.pedido_bling} · NF {g.nf_numero}"
        + (f" · entrega {svc._br(g.data_inicio)}" if g.data_inicio else " · aguardando entrega"),
    )
    outras = await _outras_do_pedido(session, g.pedido_bling, g.id)
    await session.commit()
    avisos = svc.avisos_do_cadastro(pedido, cpf, nota, outras_do_pedido=outras)
    if entrega is None:
        avisos.append(pedido.aviso_sem_entrega)
    return await _detalhe(session, g, user, avisos=avisos)


_RX_NF_DIGITADA = re.compile(r"[0-9.\s/-]+")


def _conferir_informados(
    cpf_bruto: str | None, nf_bruto: str | None, serie_bruta: str | None, nome_bruto: str | None
) -> tuple[str | None, str | None, str, str | None]:
    """CPF com dígito verificador (RN06), NF só com dígitos (pontos e espaço
    tolerados: "000.010.234" = 10234) e nome. None = campo não veio."""
    cpf = nf = nome = None
    if cpf_bruto is not None:
        if not svc.cpf_valido(cpf_bruto):
            raise _erro(422, "cpf_invalido", "cpf")
        cpf = svc.so_digitos(cpf_bruto)
    if nf_bruto is not None:
        nf = svc.normalizar_nf(nf_bruto)
        if not _RX_NF_DIGITADA.fullmatch(nf_bruto) or not nf or len(nf) > 9:
            raise _erro(422, "nf_invalida", "nf_numero")
    serie = svc.normalizar_serie(serie_bruta)
    if serie_bruta and (not _RX_NF_DIGITADA.fullmatch(serie_bruta) or len(serie) > 3):
        raise _erro(422, "nf_serie_invalida", "nf_serie")
    if nome_bruto is not None:
        nome = svc.normalizar_nome(nome_bruto)
        if len(nome) < 3:
            raise _erro(422, "nome_invalido", "cliente_nome")
    return cpf, nf, serie, nome


async def _pedido_ou_422(session: AsyncSession, termo: str, user: User) -> svc.PedidoInfo:
    pedido = await svc.buscar_pedido(session, termo)
    scope = await resolve_team_scope(session, user)
    if pedido is None or not _pedido_no_escopo(scope, pedido.loja):
        raise _erro(422, "pedido_nao_encontrado", "pedido")
    return pedido


async def _sem_duplicata(
    session: AsyncSession,
    cpf: str,
    nf: str,
    serie: str,
    chave: str | None,
    ignorar_id: int | None,
    scope: TeamScope,
) -> None:
    """RN06 + ponto 3. Série vazia (não informada e NF fora do XML do pedido)
    casa com qualquer série: o mesmo CPF com a mesma NF é a mesma nota. A
    regra vale entre equipes, mas o id da existente só vai para quem a
    enxerga (senão o 409 confirmaria o CPF de um cliente de outra equipe)."""
    serie_casa = or_(Garantia.nf_serie == serie, Garantia.nf_serie == "") if serie else true()
    conds = [(Garantia.cpf == cpf) & (Garantia.nf_numero == nf) & serie_casa]
    if chave:
        conds.append(Garantia.nf_chave == chave)
    q = select(Garantia.id, Garantia.loja).where(or_(*conds))
    if ignorar_id is not None:
        q = q.where(Garantia.id != ignorar_id)
    existente = (await session.execute(q.limit(1))).first()
    if existente is not None:
        extra = {"garantia_id": existente.id} if _pedido_no_escopo(scope, existente.loja) else {}
        raise _erro(409, "garantia_duplicada", "nf_numero", **extra)


async def _outras_do_pedido(session: AsyncSession, pedido_bling: str, ignorar_id: int) -> int:
    """Garantias que o pedido já tem (em outra NF) — aviso, não bloqueio."""
    return (
        await session.execute(
            select(func.count())
            .select_from(Garantia)
            .where(Garantia.pedido_bling == pedido_bling, Garantia.id != ignorar_id)
        )
    ).scalar_one()


def _aplicar_pedido(
    g: Garantia,
    pedido: svc.PedidoInfo,
    nf: str,
    serie: str,
    nota: svc.NotaDoPedido | None,
) -> None:
    g.pedido_bling = pedido.numero
    g.pedido_marketplace = pedido.numeroloja
    g.loja = pedido.loja
    g.plataforma = pedido.plataforma
    g.conta = pedido.conta
    g.itens = [
        {k: i.get(k) for k in ("descricao", "sku", "quantidade", "uranyx")} for i in pedido.itens
    ]
    _aplicar_nf(g, nf, serie, nota)


def _aplicar_nf(g: Garantia, nf: str, serie: str, nota: svc.NotaDoPedido | None) -> None:
    g.nf_numero = nf
    g.nf_serie = serie
    g.nf_chave = nota.chave if nota else None
    g.nf_emitente_cnpj = nota.emitente_cnpj if nota else None


# ── Rotas de uma garantia ───────────────────────────────────────────────────


@router.get("/{garantia_id}", response_model=GarantiaSalvaOut)
async def detalhe(
    garantia_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar)],
) -> GarantiaSalvaOut:
    """§4.2. Abrir o detalhe grava quem consultou (§6) — e se viu o CPF."""
    g = await _garantia_visivel(session, garantia_id, user)
    svc.registrar_log(
        session,
        g.id,
        "consultou",
        user,
        detalhe="viu o CPF completo" if _ve_cpf(user) else "CPF mascarado",
    )
    # get_session não comita sozinho: sem isto o log da consulta se perderia.
    await session.commit()
    return await _detalhe(session, g, user, revelar_cpf=True)


@router.put("/{garantia_id}", response_model=GarantiaSalvaOut)
async def corrigir(
    garantia_id: int,
    body: GarantiaUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_cadastrar)],
) -> GarantiaSalvaOut:
    """Corrige o que foi INFORMADO (pedido, NF, nome, CPF). Cada campo que
    muda vira linha no log (CPF mascarado). Trocar o pedido relê a entrega e
    recalcula os prazos; a data inicial nunca vem da pessoa (RN01)."""
    g = await _garantia_visivel(session, garantia_id, user, travar=True)
    cpf, nf, serie_in, nome = _conferir_informados(
        body.cpf, body.nf_numero, body.nf_serie, body.cliente_nome
    )
    nf_antes, serie_antes = g.nf_numero, g.nf_serie
    novo_pedido = None
    if body.pedido is not None and body.pedido not in (g.pedido_bling, g.pedido_marketplace):
        p = await _pedido_ou_422(session, body.pedido, user)
        if p.numero != g.pedido_bling:
            novo_pedido = p
    nf_final = nf if nf is not None else nf_antes
    serie_final = serie_in if body.nf_serie is not None else serie_antes
    cpf_final = cpf if cpf is not None else g.cpf
    muda_nf = novo_pedido is not None or nf_final != nf_antes or serie_final != serie_antes
    nota = None
    if muda_nf:
        ref = novo_pedido or await svc.buscar_pedido(session, g.pedido_bling)
        nota = svc.nota_informada(ref, nf_final, serie_final) if ref else None
        if not serie_final and nota:
            serie_final = nota.serie
    if muda_nf or cpf_final != g.cpf:
        chave = (nota.chave if nota else None) if muda_nf else g.nf_chave
        scope = await resolve_team_scope(session, user)
        await _sem_duplicata(session, cpf_final, nf_final, serie_final, chave, g.id, scope)

    mudancas: list[tuple[str, str | None, str | None]] = []
    avisos: list[str] = []
    if novo_pedido is not None:
        mudancas.append(("pedido", g.pedido_bling, novo_pedido.numero))
        anterior = g.data_inicio
        _aplicar_pedido(g, novo_pedido, nf_final, serie_final, nota)
        svc.aplicar_entrega(
            g, await svc.resolver_entrega(session, novo_pedido.numero, novo_pedido.numeroloja)
        )
        g.entrega_verificada_em = datetime.now(UTC)
        if g.data_inicio != anterior:
            mudancas.append(("data_inicio", svc._br(anterior), svc._br(g.data_inicio)))
        avisos = svc.avisos_do_cadastro(
            novo_pedido,
            cpf_final,
            nota,
            outras_do_pedido=await _outras_do_pedido(session, novo_pedido.numero, g.id),
        )
        if g.data_inicio is None:
            avisos.append(novo_pedido.aviso_sem_entrega)
    if muda_nf:
        _aplicar_nf(g, nf_final, serie_final, nota)
        if nf_final != nf_antes:
            mudancas.append(("nf_numero", nf_antes, nf_final))
        if serie_final != serie_antes:
            mudancas.append(("nf_serie", serie_antes or None, serie_final or None))
    if cpf is not None and cpf != g.cpf:
        mudancas.append(("cpf", svc.mascarar_cpf(g.cpf), svc.mascarar_cpf(cpf)))
        g.cpf = cpf
    if nome is not None and nome != g.cliente_nome:
        mudancas.append(("cliente_nome", g.cliente_nome, nome))
        g.cliente_nome = nome
    if not mudancas:
        return await _detalhe(session, g, user)
    g.atualizado_em = datetime.now(UTC)
    g.atualizado_por = user.id
    for campo, de, para in mudancas:
        svc.registrar_log(session, g.id, "alterou", user, campo=campo, anterior=de, novo=para)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise _erro(409, "garantia_duplicada", "nf_numero") from e
    return await _detalhe(session, g, user, avisos=avisos)


@router.post("/{garantia_id}/recalcular", response_model=GarantiaSalvaOut)
async def recalcular(
    garantia_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_cadastrar)],
) -> GarantiaSalvaOut:
    """Conferir a entrega agora — o mesmo que o robô faz de hora em hora
    (ponto 5). Mudou: linha no log em nome de quem clicou."""
    g = await _garantia_visivel(session, garantia_id, user, travar=True)
    entrega = await svc.resolver_entrega(session, g.pedido_bling, g.pedido_marketplace)
    await svc.recalcular(session, g, entrega, ator=user)
    await session.commit()
    return await _detalhe(session, g, user)


@router.post(
    "/{garantia_id}/atendimentos",
    response_model=AtendimentoOut,
    status_code=status.HTTP_201_CREATED,
)
async def registrar_atendimento(
    garantia_id: int,
    body: AtendimentoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_registrar)],
) -> AtendimentoOut:
    """Vincular à garantia (§5.1): copia data/hora, atendente, link/ID,
    mensagens e anexos da conversa, marca a cobertura pelo tipo informado e
    grava. Só adiciona (§5.3): não há rota de editar nem de apagar, e o banco
    recusa UPDATE/DELETE."""
    g = await _garantia_visivel(session, garantia_id, user)
    c = await _conversa(session, body.conversa_id, user)
    msgs = await svc.mensagens_para_copiar(session, c, body.mensagem_ids)
    if msgs is None:
        raise _erro(422, "mensagem_nao_encontrada", "mensagem_ids")
    agora = datetime.now(UTC)
    repetido = (
        await session.execute(
            select(GarantiaAtendimento.id)
            .where(
                GarantiaAtendimento.garantia_id == g.id,
                GarantiaAtendimento.conversa_id == c.id,
                GarantiaAtendimento.tipo_problema == body.tipo_problema,
                GarantiaAtendimento.solucao == body.solucao,
                GarantiaAtendimento.criado_em >= agora - svc.JANELA_ATENDIMENTO_REPETIDO,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if repetido is not None:
        raise _erro(409, "atendimento_repetido", atendimento_id=repetido)
    quando = svc.data_do_atendimento(c, msgs, bool(body.mensagem_ids), agora)
    cobertura, fim = svc.cobertura_em(g, body.tipo_problema, quando)
    # Solta a conexão enquanto baixa os anexos do CDN (até 10 × 8 s): nada
    # pendente, e a sessão não expira os objetos no commit.
    await session.commit()
    anexos = await svc.baixar_anexos(svc.anexos_das_mensagens(msgs))
    at = GarantiaAtendimento(
        garantia_id=g.id,
        data_atendimento=quando,
        atendente_id=user.id,
        atendente_nome=svc.nome_pessoa(user),
        conversa_id=c.id,
        conversa_link=svc.LINK_CONVERSA.format(id=c.id),
        conversa_plataforma=c.plataforma,
        conversa_canal=c.canal,
        conversa_conta=c.conta,
        conversa_externo_id=c.externo_id,
        conversa_pedido=c.pedido_marketplace,
        resumo=body.resumo or svc.resumo_automatico(msgs),
        mensagens=svc.copia_das_mensagens(msgs),
        anexos=[item for item, _blob, _tipo in anexos],
        tipo_problema=body.tipo_problema,
        cobertura=cobertura,
        fim_considerado=fim,
        solucao=body.solucao,
    )
    session.add(at)
    await session.flush()
    for item, blob, tipo in anexos:
        if blob is None:
            continue
        session.add(
            GarantiaAtendimentoAnexo(
                atendimento_id=at.id,
                mensagem_id=UUID(item["mensagem_id"]) if item.get("mensagem_id") else None,
                tipo=item.get("tipo") or "arquivo",
                nome=item.get("nome"),
                url_original=item.get("url"),
                content_type=tipo or "application/octet-stream",
                tamanho=len(blob),
                blob=blob,
            )
        )
    svc.registrar_log(
        session,
        g.id,
        "registrou_atendimento",
        user,
        detalhe=f"atendimento #{at.id} · {body.tipo_problema} · "
        f"{svc.COBERTURA_ROTULOS[cobertura]} · conversa {c.plataforma}",
    )
    await session.commit()
    await session.refresh(at)
    arquivos = await _arquivos_dos(session, [at.id])
    return _atendimento_out(at, g, arquivos.get(at.id, []))


@router.get("/{garantia_id}/log", response_model=list[LogOut])
async def log(
    garantia_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> list[LogOut]:
    """Quem consultou ou alterou a garantia (§6), do mais novo ao mais velho."""
    g = await _garantia_visivel(session, garantia_id, user)
    linhas = (
        await session.execute(
            select(GarantiaLog)
            .where(GarantiaLog.garantia_id == g.id)
            .order_by(GarantiaLog.em.desc(), GarantiaLog.id.desc())
        )
    ).scalars()
    return [
        LogOut(
            id=x.id,
            acao=x.acao,
            campo=x.campo,
            valor_anterior=x.valor_anterior,
            valor_novo=x.valor_novo,
            detalhe=x.detalhe,
            pessoa=PessoaRef(id=x.user_id, nome=x.user_nome),
            em=x.em,
        )
        for x in linhas
    ]
