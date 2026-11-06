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
  * O log de quem consultou/alterou (`/{id}/log`) é do admin. O Resumo (a
    garantia por produto, com o saldo e o gasto em reais) também, desde
    04/11/2026 (403 `so_admin` para os outros).

Garantia × SAC (06/11/2026, "Painel de Garantia: alterações"): o prazo único do
produto (Tabela de Preços → Produtos) e o caso do SAC do site. Confirmar e
Encerrar o atendimento são de quem REGISTRA atendimento (`garantias_atendimento`
edit), na garantia que a pessoa enxerga; o SAC sem pedido (o SAC do site não tem
loja) só para quem tem escopo irrestrito.

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
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Integer, and_, any_, false, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import (
    require_active_user,
    require_admin,
    require_permission,
)
from app.deps.team_scope import TeamScope
from app.models import (
    BlingOrder,
    Garantia,
    GarantiaAtendimento,
    GarantiaAtendimentoAnexo,
    GarantiaLog,
    User,
    UserRole,
)
from app.models.atendimento import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
)
from app.models.devolution import Devolution
from app.models.garantia import GarantiaConversa, GarantiaPendencia, GarantiaSac
from app.models.mail import MailAttachment
from app.models.mail_atendimento import MailMessageMeta
from app.models.pricing import StoreInfo
from app.schemas.garantia import (
    AbaOut,
    AbasOut,
    AnexoOut,
    AtendimentoCreate,
    AtendimentoOut,
    BuscaVinculo,
    EmAtendimentoOut,
    EntregaOut,
    GarantiaCreate,
    GarantiaLinhaOut,
    GarantiaListaOut,
    GarantiaSalvaOut,
    GarantiaUpdate,
    HistoricoConversaOut,
    HistoricoMarcoOut,
    HistoricoMensagemOut,
    HistoricoOut,
    IndicadoresOut,
    ItemOut,
    ListaFiltro,
    LogOut,
    NotaSugeridaOut,
    PedidoMarcar,
    PedidoParaCadastroOut,
    PedidoSemGarantiaOut,
    PermissoesOut,
    PessoaRef,
    PrazoOut,
    PrazosPreviaOut,
    RegrasOut,
    ResumoOut,
    SacAnexoOut,
    SacCampoOut,
    SacConfirmar,
    SacDaLinhaOut,
    SacDetalheOut,
    SacOut,
    SacSecaoOut,
    SacSemPedidoListaOut,
    SacSemPedidoOut,
    SacVincular,
    SituacaoConversaOut,
    VinculoOut,
)
from app.security.cipher import decrypt_bytes, decrypt_json
from app.services import garantia as svc
from app.services import garantia_pedido, garantia_prazo, garantia_sac
from app.services.atendimento import acesso, cartoes_plataforma, separa_email
from app.services.atendimento.contexto import _chaves_do_pedido
from app.services.atendimento.escopo import escopo_do_atendimento
from app.services.mail_atendimento import administracao as mail_admin
from app.services.mail_atendimento.constantes import ESTADOS_COM_TEXTO
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
# Desde 30/10/2026 é o escopo do ATENDIMENTO (`escopo_do_atendimento`): quem não
# é admin vê só as garantias das lojas dele (o pedido do Bling da ficha) — o
# Eduardo: "a parte de garantia é vinculada com essa parte de atendimento ...
# eles só vão poder ver das lojas deles". Sem loja, nenhuma.


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
    scope = await escopo_do_atendimento(session, user)
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
    scope = await escopo_do_atendimento(session, user)
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


def _linha(
    g: Garantia,
    n_atend: int,
    dia,
    situacao_bling: str | None = None,
    conversa: dict | None = None,
) -> GarantiaLinhaOut:
    """A linha da lista. O status é o do PAINEL (06/11/2026, o prazo único):
    aguardando entrega, ativa, expirada, "Prazo não cadastrado" ou "Sem
    garantia" (o prazo 0 — nunca é "entregue sem data": não há prazo a correr)."""
    st = svc.status_painel_da(g, dia)
    sem_data = st != svc.STATUS_SEM_GARANTIA and svc.entregue_sem_data(
        g.data_inicio, situacao_bling
    )
    conversa = conversa or {}
    return GarantiaLinhaOut(
        id=g.id,
        cliente_nome=g.cliente_nome,
        nome=svc.nome_sem_apelido(g.cliente_nome),
        garantia_meses=g.prazo_meses,
        fim_garantia=g.fim_garantia,
        cpf_mascarado=svc.mascarar_cpf(g.cpf),
        nf_numero=g.nf_numero,
        nf_serie=g.nf_serie,
        pedido_bling=g.pedido_bling,
        pedido_marketplace=g.pedido_marketplace,
        plataforma=g.plataforma,
        conta=conversa.pop("conta", None) or g.conta,
        data_inicio=g.data_inicio,
        fim_hardware=g.fim_hardware,
        fim_software=g.fim_software,
        status=st,
        status_rotulo=svc.rotulo_do_painel(st, sem_data),
        entregue_sem_data=sem_data,
        atendimentos=n_atend,
        criado_em=g.criado_em,
        origem=g.origem or "manual",
        pedido_em=g.solicitada_em,
        pedido_origem=g.solicitacao_origem,
        pedido_origem_rotulo=garantia_pedido.ORIGEM_ROTULOS.get(g.solicitacao_origem or ""),
        nao_e_pedido=g.nao_solicitada_em is not None,
        **conversa,
    )


async def _conversas_das(
    session: AsyncSession, ids: list[int], user: User | None
) -> dict[int, dict]:
    """Garantia automática: quantas conversas cada garantia tem e a mais
    recente delas (quando, quem, de onde). A PRÉVIA do texto só vai para quem
    lê o Atendimento, de conversa no escopo dela e que não é de e-mail (o
    e-mail tem permissão por pessoa)."""
    if not ids:
        return {}
    gc, c = GarantiaConversa, AtendimentoConversa
    contagem = dict(
        (
            await session.execute(
                select(gc.garantia_id, func.count())
                .where(gc.garantia_id.in_(ids))
                .group_by(gc.garantia_id)
            )
        ).all()
    )
    if not contagem:
        return {}
    linhas = (
        await session.execute(
            select(
                gc.garantia_id,
                c.ultima_mensagem_em,
                c.ultima_mensagem_resumo,
                c.ultima_autor,
                c.plataforma,
                c.integration_id,
                separa_email.conversa_de_email_sql(c).label("email"),
            )
            .join(c, c.id == gc.conversa_id)
            .where(gc.garantia_id.in_(list(contagem)))
            .order_by(gc.garantia_id, c.ultima_mensagem_em.desc().nulls_last())
            .distinct(gc.garantia_id)
        )
    ).all()
    le = user is not None and acesso.pode_ver(user)
    scope = await escopo_do_atendimento(session, user) if le else None
    saida: dict[int, dict] = {}
    for r in linhas:
        previa = (
            r.ultima_mensagem_resumo
            if le
            and scope is not None
            and not r.email
            and _conversa_no_escopo(scope, r.integration_id)
            else None
        )
        saida[r.garantia_id] = {
            "conversas": contagem.get(r.garantia_id, 0),
            "ultima_mensagem_em": r.ultima_mensagem_em,
            "ultima_previa": (previa or "")[:160] or None,
            "ultima_autor": r.ultima_autor,
            "ultima_plataforma": r.plataforma,
        }
    return saida


async def _situacoes(
    session: AsyncSession, garantias: list[Garantia]
) -> dict[str, tuple[str, str]]:
    """A situação do pedido no Bling (o id e o nome) de TODAS as garantias:
    a coluna "Status do pedido" (06/11/2026) e o "Entregue — sem data"."""
    return await svc.situacoes_com_nome(session, [g.pedido_bling for g in garantias])


async def _contas_ao_vivo(session: AsyncSession, garantias: list[Garantia]) -> dict[str, str]:
    """loja do Bling → o nome da conta HOJE em `store_info` (06/11/2026: as
    contas renomeadas — Jlas → Atlas, Kia → Fiore — aparecem com o nome novo;
    sem a loja, fica o nome gravado na garantia). A loja ativa vence a
    arquivada."""
    lojas = sorted({g.loja for g in garantias if g.loja})
    if not lojas:
        return {}
    saida: dict[str, str] = {}
    for loja, conta in (
        await session.execute(
            select(StoreInfo.bling_store_id, StoreInfo.account_name)
            .where(StoreInfo.bling_store_id.in_(lojas), StoreInfo.account_name.is_not(None))
            .order_by(StoreInfo.bling_store_id, StoreInfo.archived_at.desc().nulls_first())
        )
    ).all():
        if (conta or "").strip():
            saida.setdefault(str(loja), conta.strip())
    return saida


async def _sacs_das(session: AsyncSession, ids: list[int]) -> dict[int, SacDaLinhaOut]:
    """O caso do SAC de cada garantia (06/11/2026): o confirmado e aberto mais
    antigo (o da aba Atendimento); senão o aberto; senão o encerrado mais
    recente (`garantia_sac.ordem_dos_casos`)."""
    if not ids:
        return {}
    s = GarantiaSac
    casos = list(
        (
            await session.execute(
                select(s)
                .where(s.garantia_id.in_(ids))
                .order_by(s.garantia_id, *garantia_sac.ordem_dos_casos(s))
            )
        ).scalars()
    )
    pessoas = await _pessoas(session, {k.confirmado_por for k in casos})
    saida: dict[int, SacDaLinhaOut] = {}
    for k in casos:
        if k.garantia_id not in saida:
            saida[k.garantia_id] = _sac_da_linha(k, pessoas)
    return saida


def _sac_da_linha(k: GarantiaSac, pessoas: dict[UUID, User]) -> SacDaLinhaOut:
    return SacDaLinhaOut(
        id=k.id,
        protocolo=k.protocolo,
        recebido_em=k.recebido_em,
        confirmado_em=k.confirmado_em,
        confirmado_por=_ref(pessoas, k.confirmado_por) if k.confirmado_em else None,
        encerrado_em=k.encerrado_em,
        encerrado_motivo=k.encerrado_motivo,
    )


async def _linhas(
    session: AsyncSession,
    garantias: list[Garantia],
    dia,
    user: User | None = None,
    *,
    com_aparelho: bool = False,
    mapa=None,
    leitura=None,
) -> list[GarantiaLinhaOut]:
    """`mapa` (`garantia_resumo.mapa_das_linhas`) = o departamento de cada
    uma; `leitura` (`garantia_atendimento.em_atendimento`) = o motivo de quem
    está em atendimento agora e o desfecho já lido de quem pediu. Só a lista
    manda os dois."""
    from app.services import garantia_resumo

    contagem = await _contagem_atendimentos(session, [g.id for g in garantias])
    situacoes = await _situacoes(session, garantias)
    conversas = await _conversas_das(session, [g.id for g in garantias], user)
    trechos = await _trechos_do_pedido(session, garantias, user)
    contas = await _contas_ao_vivo(session, garantias)
    sacs = await _sacs_das(session, [g.id for g in garantias])
    # O produto pelo SKU (06/11/2026): o nome da linha da Tabela de Preços;
    # fora dela, a descrição do item principal (com o alerta na tela).
    tabela = mapa.tabela if mapa is not None else await garantia_resumo._tabela_de_precos(session)
    nomes_da_tabela = {pid: nome for pid, nome, _bases in tabela.values()}

    def do_produto(g: Garantia) -> dict:
        item = garantia_resumo.item_principal(g.itens) or {}
        chave = (
            mapa.linha(g)
            if mapa is not None
            else garantia_resumo._linha_do_sku(item.get("sku"), tabela)
        )
        if chave.startswith("tabela:") and (nome := nomes_da_tabela.get(chave.split(":", 1)[1])):
            return {"produto_nome": nome, "produto_na_tabela": True}
        return {
            "produto_nome": garantia_resumo.nome_do_produto(item.get("descricao")) or None,
            "produto_na_tabela": False,
        }

    # O aparelho (30/10/2026): o selo "No técnico", "Consertado", "Perdido",
    # "Reembolso ao cliente", "Devolução a caminho"; os reais (o gasto da tela
    # Reembolso, o mesmo do Resumo) só para admin. Só a tela de Garantias
    # mostra (`com_aparelho`): a conversa do Atendimento, a busca para vincular
    # e o cadastro não pagam essa leitura. O desfecho de quem pediu já veio na
    # `leitura` do Atendimento: aqui só o das outras (a venda sem pedido, em
    # "Mostrar: todas as vendas").
    desfechos: dict = {}
    if com_aparelho:
        ja = leitura.desfechos if leitura is not None else {}
        lidas = leitura.lidas if leitura is not None else frozenset()
        faltam = [g for g in garantias if g.id not in lidas]
        tabela = mapa.tabela if mapa is not None else None
        novos = (
            await garantia_resumo.desfechos(session, faltam, dia, tabela=tabela) if faltam else {}
        )
        desfechos = {g.id: f for g in garantias if (f := novos.get(g.id) or ja.get(g.id))}
    com_valor = user is not None and user.role == UserRole.ADMIN

    def do_aparelho(g: Garantia) -> dict:
        if not (f := desfechos.get(g.id)) or not (f.situacao or f.gasto):
            return {}
        return {
            "devolucao_situacao": f.situacao,
            "devolucao_rotulo": garantia_resumo.rotulo_do_desfecho(f, dia, com_valor),
            "devolucao_vencido": f.vencido,
            "devolucao_gasto": f.gasto if com_valor else None,
        }

    def das_partes(g: Garantia) -> dict:
        extra = {}
        if mapa is not None:
            extra["departamento"] = mapa.departamento(g)
        if leitura is not None and (a := leitura.em_aberto.get(g.id)) is not None:
            extra["atendimento"] = EmAtendimentoOut(
                motivo=a.motivo, rotulo=a.rotulo, desde=a.desde, vencido=a.vencido
            )
        return extra

    return [
        _linha(
            g,
            contagem.get(g.id, 0),
            dia,
            (situacoes.get(g.pedido_bling) or (None, None))[0],
            {
                **(conversas.get(g.id) or {}),
                "pedido_trecho": trechos.get(g.id),
                **do_aparelho(g),
                **das_partes(g),
                **do_produto(g),
                "status_pedido": (situacoes.get(g.pedido_bling) or (None, None))[1],
                "conta": contas.get(g.loja or ""),
                "sac": sacs.get(g.id),
            },
        )
        for g in garantias
    ]


async def _trechos_do_pedido(
    session: AsyncSession, garantias: list[Garantia], user: User | None
) -> dict[int, str]:
    """A frase do PEDIDO de garantia de cada uma (28/10/2026: "aparecer as
    mensagens das pessoas que pediram a garantia daquele produto"): a mensagem
    do cliente que o robô achou — com a regra da prévia (quem lê o
    Atendimento, conversa no escopo, fora o e-mail) — ou o motivo da devolução
    por defeito."""
    saida: dict[int, str] = {}
    por_msg: dict[UUID, int] = {}
    por_dev: dict[UUID, int] = {}
    for g in garantias:
        if not g.solicitacao_ref or g.nao_solicitada_em is not None:
            continue
        try:
            ref = UUID(g.solicitacao_ref)
        except ValueError:
            continue
        if g.solicitacao_origem == garantia_pedido.ORIGEM_MENSAGEM:
            por_msg[ref] = g.id
        elif g.solicitacao_origem == garantia_pedido.ORIGEM_DEVOLUCAO:
            por_dev[ref] = g.id
    if por_dev:
        for did, motivo, condicao in (
            await session.execute(
                select(
                    Devolution.id, Devolution.motivo_devolucao, Devolution.condicao_produto
                ).where(Devolution.id.in_(list(por_dev)))
            )
        ).all():
            saida[por_dev[did]] = f"devolução: {motivo or condicao or 'por defeito'}"
    if por_msg and user is not None and acesso.pode_ver(user):
        scope = await escopo_do_atendimento(session, user)
        m, c = AtendimentoMensagem, AtendimentoConversa
        for mid, texto, integracao, email in (
            await session.execute(
                select(m.id, m.texto, c.integration_id, separa_email.conversa_de_email_sql(c))
                .join(c, c.id == m.conversa_id)
                .where(m.id.in_(list(por_msg)))
            )
        ).all():
            if texto and not email and _conversa_no_escopo(scope, integracao):
                saida[por_msg[mid]] = " ".join(texto.split())[:160]
    return saida


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
    base = (await _linhas(session, [g], dia, user))[0].model_dump()
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
        # A do robô não tem pessoa: "Sistema (garantia automática)".
        criado_por=_ref(pessoas, g.criado_por) or PessoaRef(nome=svc.ROTULO_GARANTIA_AUTOMATICA),
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


# O nº da compra no Mercado Livre (o pedido ou o PACK, a compra com vários
# pedidos): 16 dígitos começando por 2000.
_RX_NUMERO_ML = re.compile(r"2000\d{12}")


def _pedidos_do_pack(termo: str):
    """Os pedidos do ML de um pack (04/11/2026: o SAC procura pelo nº que o ML
    mostra, e o Bling guarda ora o pack, ora o pedido)."""
    return svc.pedidos_do_pack(termo)


def _filtro_busca(termo: str | None):
    """Nome (sem acento nem caixa), CPF (os 11 dígitos, válido), NF ou pedido
    (Bling ou marketplace — o pack do ML acha o pedido dele). CPF parcial não
    busca: a lista mostra o CPF mascarado e pedaço de CPF serviria para
    adivinhar os dígitos escondidos."""
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
    if _RX_NUMERO_ML.fullmatch(t):
        conds.append(Garantia.pedido_marketplace.in_(_pedidos_do_pack(t)))
    return or_(*conds)


def _entre(ids) -> object:
    """`Garantia.id` numa lista que pode ser grande (todas as de um
    departamento): um parâmetro só (o asyncpg aceita até 32.767)."""
    return Garantia.id == any_(literal(sorted(ids), ARRAY(Integer)))


def _acionada():
    """A garantia com o SAC confirmado (encerrado ou não): "Garantia acionada"."""
    return (
        select(GarantiaSac.id)
        .where(GarantiaSac.garantia_id == Garantia.id, GarantiaSac.confirmado_em.is_not(None))
        .exists()
    )


def _sac_confirmado_no_mes(dia):
    """As garantias com caso do SAC confirmado no mês (em São Paulo)."""
    ini, fim = svc.mes_atual(dia)
    return Garantia.id.in_(
        select(GarantiaSac.garantia_id).where(
            GarantiaSac.confirmado_em >= ini, GarantiaSac.confirmado_em < fim
        )
    )


async def _indicadores(session: AsyncSession, escopo, dia, do_departamento=None) -> IndicadoresOut:
    """Os cartões do topo (06/11/2026): Ativas · Vence em 30 dias · Sem prazo
    cadastrado · Atendimentos no mês (os casos do SAC CONFIRMADOS no mês), mais
    as acionadas e a linha embaixo (com as "sem garantia", o prazo 0). A base
    são todas as vendas vivas que a pessoa enxerga (não mais só quem pediu).
    Com a pílula (04/11/2026, `do_departamento` = a condição dela), só as do
    departamento: os cartões e a linha embaixo deles batem com o "Garantia
    (N)" da aba. Os filtros da barra continuam fora."""
    vivas = [escopo] if escopo is not None else []
    vivas.append(Garantia.descartada_em.is_(None))
    if do_departamento is not None:
        vivas.append(do_departamento)
    contar = {
        "ativas": svc.filtro_status(svc.STATUS_ATIVA, dia),
        "vence_30d": svc.filtro_status("vence_30d", dia),
        "sem_prazo": svc.filtro_status(svc.STATUS_SEM_PRAZO, dia),
        "sem_garantia": svc.filtro_status(svc.STATUS_SEM_GARANTIA, dia),
        "aguardando_entrega": svc.filtro_status(svc.STATUS_AGUARDANDO, dia),
        "entregue_sem_data": svc.filtro_status("entregue_sem_data", dia),
        "expiradas": svc.filtro_status(svc.STATUS_EXPIRADA, dia),
        "acionadas": _acionada(),
    }
    linha = (
        await session.execute(
            select(
                func.count().label("total"),
                *[func.count().filter(f).label(k) for k, f in contar.items()],
            )
            .select_from(Garantia)
            .where(*vivas)
        )
    ).one()
    ini, fim = svc.mes_atual(dia)
    q = (
        select(func.count())
        .select_from(GarantiaSac)
        .join(Garantia, Garantia.id == GarantiaSac.garantia_id)
        .where(GarantiaSac.confirmado_em >= ini, GarantiaSac.confirmado_em < fim, *vivas)
    )
    return IndicadoresOut(
        **{k: getattr(linha, k) for k in contar},
        total=linha.total,
        vendidas=linha.total,
        atendimentos_no_mes=(await session.execute(q)).scalar_one(),
    )


async def _abas(session: AsyncSession, escopo, mapa, em_aberto: dict) -> AbasOut:
    """As pílulas e as abas (04/11/2026): por departamento, todas as garantias
    vivas que a pessoa enxerga e os casos em atendimento agora (`em_aberto` =
    os casos do SAC confirmados e abertos, já do escopo dela) — sem os filtros
    da barra, como os cartões."""
    vivas = [Garantia.descartada_em.is_(None)]
    if escopo is not None:
        vivas.append(escopo)
    ids = (await session.execute(select(Garantia.id).where(*vivas))).scalars()
    garantias = Counter(mapa.departamento_do_id(gid) for gid in ids)
    atendimento = Counter(mapa.departamento_do_id(gid) for gid in em_aberto)
    return AbasOut(
        **{
            dep: AbaOut(garantias=garantias.get(dep, 0), atendimento=atendimento.get(dep, 0))
            for dep in ("celular", "mala", "eletro")
        }
    )


# ── O pedido que ainda não tem garantia (04/11/2026) ────────────────────────


async def _pedido_da_busca(session: AsyncSession, termo: str) -> svc.PedidoInfo | None:
    """O pedido pelo nº do Bling ou da plataforma; o pack do ML (o nº que o ML
    mostra na compra com vários pedidos) pelo pedido dele."""
    pedido = await svc.buscar_pedido(session, termo)
    if pedido is None and _RX_NUMERO_ML.fullmatch(termo):
        for numero in (await session.execute(_pedidos_do_pack(termo).distinct())).scalars():
            if (pedido := await svc.buscar_pedido(session, numero)) is not None:
                break
    return pedido


_SEM_GARANTIA_CANCELADO = {
    "12": "O pedido foi cancelado no Bling, e pedido cancelado não tem garantia.",
    "83955": "O pedido está aguardando o cancelamento no Bling, e pedido cancelado não tem "
    "garantia.",
    "excluido": "O pedido foi excluído do Bling, e pedido cancelado não tem garantia.",
}


async def _por_que_sem_garantia(session: AsyncSession, pedido: svc.PedidoInfo) -> tuple[str, str]:
    """(motivo, a frase da tela): o 1º que vale — cancelado, nenhum item
    Uranyx, comprado antes das notas fiscais de produto do DaVinci, o robô já
    olhou e anotou a pendência, sem a NF de produto, ou outro."""
    from app.services import garantia_auto

    situacao = str(pedido.situacao_id or "")
    if situacao in garantia_auto.SITUACOES_FORA:
        return "cancelado", _SEM_GARANTIA_CANCELADO.get(
            situacao, "O pedido está cancelado no Bling, e pedido cancelado não tem garantia."
        )
    if not pedido.produto_uranyx:
        return (
            "sem_produto_uranyx",
            "Nenhum item do pedido é da linha Uranyx, e a garantia do DaVinci é só da linha "
            "Uranyx.",
        )
    inicio = garantia_auto.INICIO_DAS_NOTAS
    if pedido.data is not None and svc.dia_em_sp(pedido.data) < inicio:
        return (
            "antes_das_notas",
            f"A compra é de {svc._br(svc.dia_em_sp(pedido.data))}, antes de {svc._br(inicio)}: "
            "o DaVinci só tem a nota fiscal de produto desde essa data, e a garantia "
            "automática nasce dela.",
        )
    pendencia = await session.get(GarantiaPendencia, pedido.numero)
    if pendencia is not None:
        o_que = garantia_auto.MOTIVOS.get(pendencia.motivo, pendencia.motivo)
        return (
            "pendencia",
            f"O robô da garantia já olhou este pedido e não criou a garantia: {o_que}.",
        )
    if pedido.nota_produto is None:
        return (
            "sem_nf_produto",
            "O pedido ainda não tem a nota fiscal de produto no DaVinci, e a garantia "
            "automática nasce dela.",
        )
    return "outro", "O robô da garantia ainda não criou a garantia deste pedido."


async def _pedido_sem_garantia(
    session: AsyncSession, termo: str, scope: TeamScope, user: User
) -> PedidoSemGarantiaOut | None:
    """A busca não achou garantia: o PEDIDO (no escopo da pessoa) e por que ele
    ainda não tem garantia — o SAC procura pelo nº da plataforma de um pedido
    que o robô não pegou. O pedido que já tem garantia (fora dos filtros da
    barra) não entra. Nunca CPF nem nome."""
    pedido = await _pedido_da_busca(session, termo)
    if pedido is None or not _pedido_no_escopo(scope, pedido.loja):
        return None
    tem = await session.scalar(
        select(func.count())
        .select_from(Garantia)
        .where(Garantia.pedido_bling == pedido.numero, Garantia.descartada_em.is_(None))
    )
    if tem:
        return None
    motivo, texto = await _por_que_sem_garantia(session, pedido)
    return PedidoSemGarantiaOut(
        pedido_bling=pedido.numero,
        pedido_marketplace=pedido.numeroloja,
        plataforma=pedido.plataforma,
        conta=pedido.conta,
        data=pedido.data,
        situacao=pedido.situacao,
        produto_uranyx=pedido.produto_uranyx,
        motivo=motivo,
        motivo_texto=texto,
        pode_cadastrar=_pode(user, "garantias", "edit"),
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


def _filtros_do_painel(body: ListaFiltro, escopo, dia) -> list:
    """Os filtros da barra (escopo, origem, status, período, atendimento no
    mês) — os MESMOS na lista e no Resumo. A busca e o "só com conversa" são
    da lista. A descartada (pedido cancelado) nunca entra."""
    filtros = [escopo] if escopo is not None else []
    filtros.append(Garantia.descartada_em.is_(None))
    if body.origem != "todas":
        filtros.append(Garantia.origem == body.origem)
    if body.status != "todos":
        filtros.append(svc.filtro_status(body.status, dia))
    de, ate = body.de, body.ate
    if (de or ate) and body.periodo == "venda":
        # As vendas feitas no período (05/11/2026): o dia da compra no Bling em
        # São Paulo, de `de` a `ate` inclusive — a mesma data da planilha de
        # rentabilidade. No Resumo, Vendidas, Saldo, pedidos e Gasto são todos
        # dessas vendas. Sem o pedido no Bling, o dia do cadastro.
        compra = func.coalesce(garantia_pedido.compra_da_garantia(), Garantia.criado_em)
        if de:
            filtros.append(compra >= datetime.combine(de, time(), svc.SAO_PAULO))
        if ate:
            filtros.append(
                compra < datetime.combine(ate + timedelta(days=1), time(), svc.SAO_PAULO)
            )
    elif (de or ate) and body.periodo != "pedido":
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
        # O cartão "Atendimentos no mês" (06/11/2026): o SAC confirmado no mês.
        filtros.append(_sac_confirmado_no_mes(dia))
    return filtros


def _filtros_do_pedido(body: ListaFiltro) -> list:
    """O período pela data do PEDIDO de garantia (o dia em São Paulo, de `de`
    a `ate` inclusive): na lista, filtra as garantias; no Resumo, só os
    pedidos (as vendidas são todas)."""
    if body.periodo != "pedido" or not (body.de or body.ate):
        return []
    filtros = []
    if body.de:
        filtros.append(Garantia.solicitada_em >= datetime.combine(body.de, time(), svc.SAO_PAULO))
    if body.ate:
        fim = datetime.combine(body.ate + timedelta(days=1), time(), svc.SAO_PAULO)
        filtros.append(Garantia.solicitada_em < fim)
    return filtros


def _vendidas_do_periodo(body: ListaFiltro) -> list:
    """Com o período pela data do PEDIDO, as vendidas são as compradas até o
    fim dele (a compra no Bling): o pedido do período não se divide pela venda
    que veio depois (revisão 28/10). A coluna Vendidas do Resumo e a linha
    dele aberta na lista (04/11/2026: "todas as garantias do produto") usam a
    mesma conta."""
    if body.periodo != "pedido" or not body.ate:
        return []
    fim = datetime.combine(body.ate + timedelta(days=1), time(), svc.SAO_PAULO)
    compra = func.coalesce(garantia_pedido.compra_da_garantia(), Garantia.criado_em)
    return [compra < fim]


@router.post("/resumo", response_model=ResumoOut)
async def resumo(
    body: ListaFiltro,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar)],
) -> ResumoOut:
    """Resumo (28/10/2026): a garantia por produto — as vendas com garantia e
    os pedidos de garantia por produto da Tabela de Preços e por departamento,
    com os filtros da barra (período, origem, status). Sem nome nem CPF. Só
    para admin desde 04/11/2026 ("resumo vai aparecer só para os admin": tem
    o saldo e o gasto em reais). Os três departamentos vêm sempre (a tela
    escolhe pela pílula); departamento, produto e atendimento são da lista."""
    from app.services import garantia_resumo

    if user.role != UserRole.ADMIN:
        raise _erro(403, "so_admin")
    dia = svc.hoje()
    escopo = _clausula(await escopo_do_atendimento(session, user))
    filtros = [*_filtros_do_painel(body, escopo, dia), *_vendidas_do_periodo(body)]
    dados = await garantia_resumo.resumo(
        session,
        filtros,
        dia,
        pedido=_filtros_do_pedido(body),
        # O saldo e o gasto em reais (só do admin).
        com_valores=user.role == UserRole.ADMIN,
    )
    return ResumoOut(**dados)


@router.post("/lista", response_model=GarantiaListaOut)
async def listar(
    body: ListaFiltro,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar)],
) -> GarantiaListaOut:
    """A tela principal (§4.1). CPF sempre mascarado. Os indicadores do topo
    contam tudo o que a pessoa enxerga no departamento da pílula (sem os
    filtros da barra), como os cartões que filtram ao clicar. POST: a busca
    (nome/CPF) vai no corpo, fora do log de acesso.

    As 3 partes (04/11/2026): `departamento` (a pílula) e `produto` (a linha
    do Resumo) filtram pela linha do produto da garantia; `em_atendimento` (a
    aba Atendimento) mostra só quem está em atendimento agora, o vencido e o
    mais antigo primeiro. As `abas` contam, por departamento, o que a pessoa
    enxerga. A busca que não achou garantia, mas achou o pedido, diz por que
    ele ainda não tem (`pedido_sem_garantia`)."""
    from app.services import garantia_atendimento, garantia_resumo

    dia = svc.hoje()
    scope = await escopo_do_atendimento(session, user)
    escopo = _clausula(scope)
    cpf = svc.cpf_da_busca(body.busca)
    if cpf:
        await _teto_da_busca_por_cpf(user)
    filtros = _filtros_do_painel(body, escopo, dia)
    if body.ids:
        filtros.append(Garantia.id.in_(body.ids))
    # A linha do Resumo aberta (o produto com "todas as vendas"): as MESMAS
    # vendas da coluna Vendidas — com o período pela data do pedido, as
    # compradas até o fim dele, e não só quem pediu no período.
    linha_do_resumo = bool(body.produto) and body.mostrar == "todas"
    do_pedido = [] if linha_do_resumo else _filtros_do_pedido(body)
    if (f := _filtro_busca(body.busca)) is not None:
        filtros.append(f)
    elif do_pedido:
        # O período pela data do PEDIDO de garantia: só quem pediu. Desde
        # 06/11/2026 a lista mostra todas as vendas com nota por padrão.
        filtros.append(garantia_pedido.filtro_pedido())
    if body.mostrar == "acionada":
        filtros.append(_acionada())
    filtros.extend(_vendidas_do_periodo(body) if linha_do_resumo else do_pedido)
    if body.com_conversa:
        filtros.append(
            select(GarantiaConversa.garantia_id)
            .where(GarantiaConversa.garantia_id == Garantia.id)
            .exists()
        )
    mapa = await garantia_resumo.mapa_das_linhas(session)
    do_departamento = (
        _entre(mapa.ids_do_departamento(body.departamento)) if body.departamento else None
    )
    if do_departamento is not None:
        filtros.append(do_departamento)
    if body.produto:
        filtros.append(_entre(mapa.ids_da_linha(body.produto)))
    # Os casos em atendimento agora (o SAC confirmado e não encerrado), entre
    # as que a pessoa enxerga (as abas contam; a aba Atendimento lista, o mais
    # antigo primeiro; a linha leva o selo e o aparelho).
    vivas = [escopo] if escopo is not None else []
    leitura = await garantia_atendimento.em_atendimento(session, vivas, dia, tabela=mapa.tabela)
    casos = leitura.casos
    if body.em_atendimento:
        filtros.append(_entre(casos))
        todas = list((await session.execute(select(Garantia).where(*filtros))).scalars())
        todas.sort(key=lambda g: (casos[g.id].recebido_em, casos[g.id].sac_id, g.id))
        total = len(todas)
        garantias = todas[body.offset : body.offset + body.limite]
    else:
        total = (
            await session.execute(select(func.count()).select_from(Garantia).where(*filtros))
        ).scalar_one()
        ordem = [Garantia.criado_em.desc(), Garantia.id.desc()]
        if body.ordem == "atividade":
            # A vista "Conversas": a garantia com mensagem mais nova em cima
            # (como a Caixa do Atendimento); sem conversa, pela data de cadastro.
            ultima = (
                select(func.max(AtendimentoConversa.ultima_mensagem_em))
                .join(GarantiaConversa, GarantiaConversa.conversa_id == AtendimentoConversa.id)
                .where(GarantiaConversa.garantia_id == Garantia.id)
                .scalar_subquery()
            )
            ordem = [ultima.desc().nulls_last(), *ordem]
        garantias = list(
            (
                await session.execute(
                    select(Garantia)
                    .where(*filtros)
                    .order_by(*ordem)
                    .limit(body.limite)
                    .offset(body.offset)
                )
            ).scalars()
        )
    sem_garantia = None
    if body.busca and not total and not cpf:
        sem_garantia = await _pedido_sem_garantia(session, body.busca, scope, user)
    resposta = GarantiaListaOut(
        itens=await _linhas(
            session, garantias, dia, user, com_aparelho=True, mapa=mapa, leitura=leitura
        ),
        total=total,
        indicadores=await _indicadores(session, escopo, dia, do_departamento),
        hoje=dia,
        abas=await _abas(session, escopo, mapa, casos),
        pedido_sem_garantia=sem_garantia,
        sac_sem_pedido=await _contar_sac_sem_pedido(session, scope),
    )
    if cpf:
        await _registrar_busca_por_cpf(session, user, cpf, escopo, "busca na lista")
    return resposta


async def _contar_sac_sem_pedido(session: AsyncSession, scope: TeamScope) -> int:
    """Os casos do SAC sem garantia ligada — só para quem tem escopo irrestrito
    (o SAC do site não tem loja: quem é de equipe não vê nenhum)."""
    if not scope.unrestricted:
        return 0
    return (
        await session.execute(
            select(func.count()).select_from(GarantiaSac).where(GarantiaSac.garantia_id.is_(None))
        )
    ).scalar_one()


@router.get("/regras", response_model=RegrasOut)
async def regras(_u: Annotated[User, Depends(_consultar_ou_registrar)]) -> RegrasOut:
    """Os prazos e os pontos decididos (§8), para a tela explicar a regra."""
    return RegrasOut(
        meses_hardware=svc.MESES_HARDWARE,
        meses_software=svc.MESES_SOFTWARE,
        ultimo_dia_coberto=svc.ULTIMO_DIA_COBERTO,
        cadastro_antes_da_entrega=svc.CADASTRO_ANTES_DA_ENTREGA,
        garantia_por=svc.GARANTIA_POR,
        # Desde 28/10/2026 o robô liga sozinho (quando ligado no .env).
        vinculo_automatico=svc.VINCULO_AUTOMATICO or get_settings().garantia_automatica,
        recalcular_quando_entrega_mudar=svc.RECALCULAR_QUANDO_ENTREGA_MUDAR,
        dias_alerta_hardware=svc.DIAS_ALERTA_HARDWARE,
        dias_pausa_atendimento=svc.DIAS_PAUSA_NOVO_ATENDIMENTO,
        # O status do painel (06/11/2026, o prazo único por produto).
        status=svc.STATUS_PAINEL_ROTULOS,
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
    scope = await escopo_do_atendimento(session, user)
    if pedido is None or not _pedido_no_escopo(scope, pedido.loja):
        raise _erro(404, "pedido_nao_encontrado", "pedido")
    entrega = await svc.resolver_entrega(session, pedido.numero, pedido.numeroloja)
    dia = svc.hoje()
    inicio = entrega.dia if entrega else None
    fim_hw, fim_sw = svc.calcular_prazos(inicio) if inicio else (None, None)
    # O status da prévia é o do painel (o prazo do produto na Tabela de Preços).
    prazo = await garantia_prazo.prazo_dos_itens(session, pedido.itens, pedido.data)
    st = svc.status_painel(inicio, prazo, svc.fim_da_garantia(inicio, prazo), dia)
    sem_data = st != svc.STATUS_SEM_GARANTIA and svc.entregue_sem_data(inicio, pedido.situacao_id)
    nome, nome_origem = pedido.nome
    cpf = pedido.cpf
    existentes = await svc.garantias_do_cpf_ou_pedido(
        session, None, pedido.numero, _clausula(scope)
    )
    avisos = []
    if not pedido.produto_uranyx:
        avisos.append("pedido_sem_produto_uranyx")
    # O produto vendido sem garantia (prazo 0) não espera a entrega para nada.
    if entrega is None and st != svc.STATUS_SEM_GARANTIA:
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
            status_rotulo=svc.rotulo_do_painel(st, sem_data),
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
    filtros = [_filtro_busca(body.q), Garantia.descartada_em.is_(None)]
    escopo = _clausula(await escopo_do_atendimento(session, user))
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
    escopo = _clausula(await escopo_do_atendimento(session, user))
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
    scope = await escopo_do_atendimento(session, user)
    await _sem_duplicata(session, cpf, nf, serie, nota.chave if nota else None, None, scope)
    entrega = await svc.resolver_entrega(session, pedido.numero, pedido.numeroloja)
    if entrega is None and not svc.CADASTRO_ANTES_DA_ENTREGA:
        raise _erro(422, "pedido_sem_entrega", "pedido")
    g = Garantia(criado_por=user.id, cliente_nome=nome, cpf=cpf)
    svc.aplicar_pedido(g, pedido, nf, serie, nota)
    svc.aplicar_entrega(g, entrega)
    g.entrega_verificada_em = datetime.now(UTC)
    # Quem cadastra à mão atende um pedido de garantia.
    garantia_pedido.marcar_pela_pessoa(g, g.entrega_verificada_em, garantia_pedido.ORIGEM_MANUAL)
    session.add(g)
    try:
        await session.flush()
    except IntegrityError as e:
        # Duas pessoas cadastrando a mesma NF ao mesmo tempo.
        await session.rollback()
        raise _erro(409, "garantia_duplicada", "nf_numero") from e
    # O prazo do produto na Tabela de Preços (06/11/2026): nasce com ele.
    await garantia_prazo.aplicar_prazos(session, ids=[g.id], registrar=False)
    # O UPDATE do aplicar_prazos não mexe no objeto: relê o prazo (o aviso de
    # entrega lá embaixo depende dele).
    await session.refresh(g, ["prazo_meses"])
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
    await _religar_conversas(session, g)
    avisos = svc.avisos_do_cadastro(pedido, cpf, nota, outras_do_pedido=outras)
    # O produto vendido sem garantia (prazo 0) não espera a entrega para nada
    # (a regra da prévia).
    if entrega is None and g.prazo_meses != 0:
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
    scope = await escopo_do_atendimento(session, user)
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


# ── O caso do SAC (06/11/2026) ──────────────────────────────────────────────
# Especificação "Painel de Garantia: alterações": a coluna Atendimento mostra o
# protocolo, a data e o botão Confirmar; confirmado, "Garantia acionada" e o caso
# entra na aba Atendimento até encerrar. "Nenhum e-mail é enviado ao cliente
# nesse clique." O SAC que o robô não ligou fica em "SAC sem pedido".


async def _caso_visivel(
    session: AsyncSession, sac_id: int, user: User, *, travar: bool = False
) -> tuple[GarantiaSac, Garantia | None]:
    """O caso e a garantia dele — a garantia tem de estar no escopo da pessoa;
    o caso sem garantia, só para quem tem escopo irrestrito. Fora disso, 404
    (não diz que o caso existe)."""
    caso = await session.get(GarantiaSac, sac_id, with_for_update=travar)
    if caso is None:
        raise _erro(404, "sac_nao_encontrado")
    if caso.garantia_id is None:
        if not (await escopo_do_atendimento(session, user)).unrestricted:
            raise _erro(404, "sac_nao_encontrado")
        return caso, None
    try:
        g = await _garantia_visivel(session, caso.garantia_id, user, travar=travar)
    except HTTPException as e:
        raise _erro(404, "sac_nao_encontrado") from e
    return caso, g


async def _sac_out(session: AsyncSession, caso: GarantiaSac) -> SacOut:
    pessoas = await _pessoas(session, {caso.confirmado_por, caso.encerrado_por})
    return SacOut(
        **_sac_da_linha(caso, pessoas).model_dump(),
        garantia_id=caso.garantia_id,
        conversa_id=caso.conversa_id,
        vinculo=caso.vinculo,
        encerrado_por=_ref(pessoas, caso.encerrado_por),
    )


@router.get("/sac/sem-pedido", response_model=SacSemPedidoListaOut)
async def sac_sem_pedido(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar_ou_registrar)],
) -> SacSemPedidoListaOut:
    """O "SAC sem pedido": o formulário que o robô não ligou a nenhuma
    garantia (pedido, NF e CPF + produto não acharam uma só — inclusive a
    compra em outro vendedor). Só para quem tem escopo irrestrito: o SAC do
    site não tem loja. Sem CPF."""
    if not (await escopo_do_atendimento(session, user)).unrestricted:
        return SacSemPedidoListaOut(itens=[], total=0)
    sem = GarantiaSac.garantia_id.is_(None)
    total = (
        await session.execute(select(func.count()).select_from(GarantiaSac).where(sem))
    ).scalar_one()
    casos = (
        await session.execute(
            select(GarantiaSac)
            .where(sem)
            .order_by(GarantiaSac.recebido_em.desc(), GarantiaSac.id.desc())
            .limit(200)
        )
    ).scalars()
    itens = []
    for k in casos:
        c = k.campos if isinstance(k.campos, dict) else {}
        itens.append(
            SacSemPedidoOut(
                id=k.id,
                protocolo=k.protocolo,
                recebido_em=k.recebido_em,
                nome=c.get("nome"),
                pedido_citado=c.get("pedido"),
                nota_fiscal=c.get("nota_fiscal"),
                modelo=c.get("modelo"),
                local_compra=c.get("local_compra"),
                loja_vendedor=c.get("loja_vendedor"),
            )
        )
    return SacSemPedidoListaOut(itens=itens, total=total)


@router.post("/sac/{sac_id}/confirmar", response_model=SacOut)
async def confirmar_sac(
    sac_id: int,
    body: SacConfirmar,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_registrar)],
) -> SacOut:
    """Confirmar atendimento: a garantia vira "Garantia acionada" e o caso entra
    na aba Atendimento; o log guarda quem confirmou e quando. Com a garantia
    vencida, a tela avisa antes (409 `garantia_vencida`) e repete com
    `ciente_vencida`. O produto vendido sem garantia (prazo 0, 06/11/2026:
    "se for avulso não tem garantia") também avisa antes (409
    `produto_sem_garantia`) e repete do mesmo jeito. Não manda e-mail. Clicar
    de novo não muda nada."""
    caso, g = await _caso_visivel(session, sac_id, user, travar=True)
    if g is None:
        raise _erro(409, "sac_sem_garantia")
    if caso.confirmado_em is not None:
        return await _sac_out(session, caso)
    dia = svc.hoje()
    sem_garantia = g.prazo_meses == 0
    vencida = g.fim_garantia is not None and not svc.coberto(g.fim_garantia, dia)
    if sem_garantia and not body.ciente_vencida:
        raise _erro(409, "produto_sem_garantia")
    if vencida and not body.ciente_vencida:
        raise _erro(409, "garantia_vencida", fim_garantia=g.fim_garantia.isoformat())
    agora = datetime.now(UTC)
    caso.confirmado_em = agora
    caso.confirmado_por = user.id
    # O SAC confirmado é pedido de garantia (o Resumo conta em "pediram").
    if g.solicitada_em is None:
        g.solicitada_em = caso.recebido_em
        g.solicitacao_origem = garantia_pedido.ORIGEM_SAC
        g.solicitacao_ref = caso.protocolo[:64]
    g.nao_solicitada_em = None
    g.nao_solicitada_por = None
    # Confirmou ciente: do produto vendido sem garantia, o prazo 0; da garantia
    # vencida, o fim que a tela avisou.
    if sem_garantia:
        svc.registrar_log(
            session,
            g.id,
            "confirmou_atendimento",
            user,
            campo="sem_garantia",
            novo="0 meses",
            detalhe=caso.protocolo,
        )
    else:
        svc.registrar_log(
            session,
            g.id,
            "confirmou_atendimento",
            user,
            campo="garantia_vencida" if vencida else None,
            novo=svc._br(g.fim_garantia) if vencida else None,
            detalhe=caso.protocolo,
        )
    await session.commit()
    return await _sac_out(session, caso)


@router.post("/sac/{sac_id}/encerrar", response_model=SacOut)
async def encerrar_sac(
    sac_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_registrar)],
) -> SacOut:
    """Encerrar atendimento: o caso sai da aba Atendimento, o tempo em aberto
    para e o log registra quem encerrou e quando. Só o caso confirmado."""
    caso, g = await _caso_visivel(session, sac_id, user, travar=True)
    if caso.confirmado_em is None or g is None:
        raise _erro(409, "sac_nao_confirmado")
    if caso.encerrado_em is not None:
        return await _sac_out(session, caso)
    caso.encerrado_em = datetime.now(UTC)
    caso.encerrado_por = user.id
    caso.encerrado_motivo = "pessoa"
    svc.registrar_log(session, g.id, "encerrou_atendimento", user, detalhe=caso.protocolo)
    await session.commit()
    return await _sac_out(session, caso)


@router.post("/sac/{sac_id}/reabrir", response_model=SacOut)
async def reabrir_sac(
    sac_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_registrar)],
) -> SacOut:
    """Reabrir atendimento (06/11/2026, Eduardo: "queria ver ele em
    atendimentos de volta"): o caso encerrado volta para a aba Atendimento e o
    tempo em aberto volta a contar desde o formulário. O log registra quem
    reabriu (`alterou`, campo `atendimento`: encerrado → reaberto)."""
    caso, g = await _caso_visivel(session, sac_id, user, travar=True)
    if caso.confirmado_em is None or g is None:
        raise _erro(409, "sac_nao_confirmado")
    if caso.encerrado_em is None:
        return await _sac_out(session, caso)
    caso.encerrado_em = None
    caso.encerrado_por = None
    caso.encerrado_motivo = None
    svc.registrar_log(
        session,
        g.id,
        "alterou",
        user,
        campo="atendimento",
        anterior="encerrado",
        novo="reaberto",
        detalhe=caso.protocolo,
    )
    await session.commit()
    return await _sac_out(session, caso)


@router.post("/sac/{sac_id}/vincular", response_model=SacOut)
async def vincular_sac(
    sac_id: int,
    body: SacVincular,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_registrar)],
) -> SacOut:
    """O SAC sem pedido ligado à mão (a busca da tela escolhe a garantia). Só
    quem tem escopo irrestrito (o SAC do site não tem loja), na garantia que
    enxerga. O caso já confirmado em outra garantia não muda (409)."""
    if not (await escopo_do_atendimento(session, user)).unrestricted:
        raise _erro(404, "sac_nao_encontrado")
    caso = await session.get(GarantiaSac, sac_id, with_for_update=True)
    if caso is None:
        raise _erro(404, "sac_nao_encontrado")
    g = await _garantia_visivel(session, body.garantia_id, user)
    if g.descartada_em is not None:
        raise _erro(404, "garantia_nao_encontrada")
    if caso.garantia_id == g.id:
        return await _sac_out(session, caso)
    if caso.confirmado_em is not None:
        raise _erro(409, "sac_ja_confirmado")
    # Outra pessoa escreveu com o protocolo (o alerta da ponte): a conversa não
    # é só do cliente do formulário — não vai para o histórico de uma garantia
    # (revisão de 06/11/2026; a tela mostra a frase).
    conv = await session.get(AtendimentoConversa, caso.conversa_id)
    if conv is not None and _com_outra_pessoa_no_sac(conv):
        raise _erro(409, "sac_outra_pessoa")
    if caso.garantia_id is not None:
        await garantia_sac.desligar_conversa(session, caso)
    await garantia_sac.ligar_conversa(session, caso, g.id, "manual", ator=user)
    await session.commit()
    return await _sac_out(session, caso)


@router.get("/sac/{sac_id}/anexos/{attachment_id}")
async def anexo_do_sac(
    sac_id: int,
    attachment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar_ou_registrar)],
) -> Response:
    """Um arquivo do e-mail do caso (as fotos, o vídeo e o comprovante do
    formulário), aberto no próprio painel. Só os anexos das mensagens da
    conversa do caso, com a trava de quem lê o e-mail. Foto, vídeo e PDF abrem
    (inline); o resto baixa. CSP sandbox sempre: o arquivo veio do cliente."""
    caso, _g = await _caso_visivel(session, sac_id, user)
    a = await session.get(MailAttachment, attachment_id)
    meta = await session.get(MailMessageMeta, a.message_id) if a is not None else None
    if (
        # Quem não lê a conversa do caso (a trava do Resumo do SAC e do
        # /historico) não baixa os arquivos dela.
        not await _caso_legivel(session, caso, user)
        or a is None
        or meta is None
        or meta.conversa_id != caso.conversa_id
        or meta.estado not in ESTADOS_COM_TEXTO
    ):
        raise _erro(404, "anexo_nao_encontrado")
    try:
        await mail_admin.exigir_mensagem(session, user, a.message_id)
    except HTTPException as e:
        raise _erro(404, "anexo_nao_encontrado") from e
    info = decrypt_json(a.metadata_enc)
    nome = str(info.get("filename") or "anexo").replace("\\", "/").rsplit("/", 1)[-1] or "anexo"
    tipo = str(info.get("content_type") or "application/octet-stream").split(";")[0]
    tipo = tipo.strip().lower() or "application/octet-stream"
    inline = svc.tipo_de_anexo_aceito(tipo)
    return Response(
        content=decrypt_bytes(a.content_enc),
        media_type=tipo if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f"{'inline' if inline else 'attachment'};"
            f" filename*=UTF-8''{quote(nome, safe='')}",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


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


# Garantia automática (28/10/2026): o histórico das conversas ligadas.
MAX_HISTORICO = 400
# Shopee e TikTok: UMA conversa por comprador com todos os pedidos dele — só
# conta o que veio depois da compra (o que veio antes é de outro pedido ou
# pré-venda).
_PLATAFORMAS_POR_COMPRADOR = ("shopee", "tiktok")


def _por_comprador(c: AtendimentoConversa, como: str) -> bool:
    return como == "comprador" or (c.plataforma in _PLATAFORMAS_POR_COMPRADOR and c.canal == "chat")


async def _data_da_compra(session: AsyncSession, pedido_bling: str) -> datetime | None:
    return await session.scalar(
        select(func.min(BlingOrder.data)).where(BlingOrder.numero == pedido_bling)
    )


@router.get("/{garantia_id}/historico", response_model=HistoricoOut)
async def historico(
    garantia_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar)],
) -> HistoricoOut:
    """As conversas ligadas à garantia, lidas NA HORA (sem cópia), no estilo
    do Atendimento: as mensagens uma embaixo da outra, com as fotos e os
    vídeos (o link do CDN da plataforma, como o Atendimento mostra), e os
    marcos (compra, entrega, outro pedido do mesmo comprador, atendimento
    registrado). As conversas seguem as regras do Atendimento: só para quem
    lê a caixa, no escopo da equipe, e o e-mail pela permissão de cada um.
    Abrir grava "consultou" no log da garantia (é texto do cliente)."""
    g = await _garantia_visivel(session, garantia_id, user)
    dia_compra = await _data_da_compra(session, g.pedido_bling)
    desde = (
        datetime.combine(svc.dia_em_sp(dia_compra), time(), svc.SAO_PAULO) if dia_compra else None
    )
    marcos: list[HistoricoMarcoOut] = []
    if dia_compra:
        marcos.append(
            HistoricoMarcoOut(
                tipo="compra",
                em=dia_compra,
                texto=f"Compra — pedido {g.pedido_bling}",
                sem_hora=True,
            )
        )
    if g.entrega_em or g.data_inicio:
        quando = g.entrega_em or datetime.combine(g.data_inicio, time(12), svc.SAO_PAULO)
        marcos.append(
            HistoricoMarcoOut(
                tipo="entrega",
                em=quando,
                texto="Entregue — começa a garantia"
                + (
                    f" ({svc.ORIGEM_ROTULOS.get(g.entrega_origem, '')})" if g.entrega_origem else ""
                ),
                sem_hora=g.entrega_em is None,
            )
        )
    for a in (
        await session.execute(
            select(GarantiaAtendimento).where(GarantiaAtendimento.garantia_id == g.id)
        )
    ).scalars():
        marcos.append(
            HistoricoMarcoOut(
                tipo="atendimento",
                em=a.data_atendimento,
                texto=f"Atendimento registrado por {a.atendente_nome} — "
                f"{svc.TIPO_PROBLEMA_ROTULOS.get(a.tipo_problema, a.tipo_problema)}"
                f" · {svc.COBERTURA_ROTULOS.get(a.cobertura, a.cobertura)} · {a.solucao}",
                atendimento_id=a.id,
            )
        )
    out = HistoricoOut(garantia_id=g.id, desde=desde, marcos=marcos)
    if not acesso.pode_ver(user):
        # Quem não lê o Atendimento: só se há conversa (escondida) ligada.
        out.restrito = bool(
            await session.scalar(
                select(func.count())
                .select_from(GarantiaConversa)
                .where(GarantiaConversa.garantia_id == g.id)
            )
        )
        out.marcos.sort(key=lambda m: m.em)
        return out

    scope = await escopo_do_atendimento(session, user)
    c, gc = AtendimentoConversa, GarantiaConversa
    # A conversa do caso do SAC ligado a ESTA garantia (06/11/2026) aparece
    # para quem vê a garantia, mesmo de equipe: o SAC do site não tem loja
    # (integração), e a garantia já está no escopo da pessoa. A trava de quem
    # lê o e-mail (`filtro_conversa`) continua valendo.
    casos_daqui = (
        await session.execute(
            select(GarantiaSac.conversa_id, GarantiaSac.confirmado_em, GarantiaSac.vinculo).where(
                GarantiaSac.garantia_id == g.id
            )
        )
    ).all()
    do_caso = {cid for cid, _conf, _vinc in casos_daqui}
    decididos = {cid for cid, conf, vinc in casos_daqui if _sac_decidido(conf, vinc)}
    ligadas = [
        (conv, como)
        for conv, como in (
            await session.execute(
                select(c, gc.como)
                .join(gc, gc.conversa_id == c.id)
                .where(gc.garantia_id == g.id, mail_admin.filtro_conversa(user, c))
                .order_by(c.ultima_mensagem_em.desc().nulls_last())
            )
        ).all()
        if (_conversa_no_escopo(scope, conv.integration_id) or conv.id in do_caso)
        # O SAC em que outra pessoa escreveu sai já (o robô desliga na volta)
        # — menos o caso que a pessoa já decidiu (confirmou ou vinculou à mão):
        # o robô não desfaz e a conversa continua na linha do tempo.
        and (conv.id in decididos or not _com_outra_pessoa_no_sac(conv))
    ]
    if not ligadas:
        out.restrito = bool(
            await session.scalar(select(func.count()).select_from(gc).where(gc.garantia_id == g.id))
        )
        out.marcos.sort(key=lambda m: m.em)
        return out

    m = AtendimentoMensagem
    # Como o detalhe do Atendimento: a hora é a do envio ou, sem ela (envio
    # que falhou ou ficou "a conferir"), a da gravação.
    quando = func.coalesce(m.enviada_em, m.created_at)
    filtros = []
    for conv, como in ligadas:
        conds = [m.conversa_id == conv.id]
        if _por_comprador(conv, como) and desde is not None:
            conds.append(quando >= desde)
        # O e-mail da ponte gravado numa conversa de chat não aparece no chat.
        if (visivel := separa_email.mensagem_visivel_sql(conv, m)) is not None:
            conds.append(visivel)
        filtros.append(and_(*conds))
    mensagens = list(
        (
            await session.execute(
                select(m)
                .where(or_(*filtros))
                .order_by(quando.desc(), m.id.desc())
                .limit(MAX_HISTORICO + 1)
            )
        ).scalars()
    )
    if len(mensagens) > MAX_HISTORICO:
        mensagens = mensagens[:MAX_HISTORICO]
        out.cortadas = 1
    mensagens.reverse()

    # Outro pedido do MESMO comprador depois desta compra: dali para baixo a
    # conversa (uma por comprador) pode ser daquele pedido.
    p = AtendimentoPedidoComprador
    for conv, como in ligadas:
        if not _por_comprador(conv, como) or not conv.comprador_id or not conv.integration_id:
            continue
        for outro in (
            await session.execute(
                select(p.pedido, p.criado_em).where(
                    p.integration_id == conv.integration_id,
                    p.comprador_id == conv.comprador_id,
                    p.pedido != (g.pedido_marketplace or ""),
                    p.criado_em > (desde or dia_compra or datetime.min.replace(tzinfo=UTC)),
                )
            )
        ).all():
            out.marcos.append(
                HistoricoMarcoOut(
                    tipo="outro_pedido",
                    em=outro.criado_em,
                    texto=f"O comprador fez OUTRO pedido ({outro.pedido}) — daqui para baixo a "
                    "conversa pode ser sobre esse outro pedido",
                )
            )

    # SAC do site: qual mensagem é o formulário (vira o cartão, pelo conteúdo).
    formularios = set()
    for conv, _como in ligadas:
        if garantia_sac.protocolo_da_conversa(conv):
            f = await garantia_sac.mensagem_do_formulario(session, conv)
            if f is not None:
                formularios.add(f.id)
    out.conversas = [
        HistoricoConversaOut(
            id=conv.id,
            plataforma=conv.plataforma,
            canal=conv.canal,
            comprador_nome=conv.comprador_nome,
            conta=g.conta,
            pedido_marketplace=conv.pedido_marketplace,
            como=como,
            link=svc.LINK_CONVERSA.format(id=conv.id),
            email=separa_email.e_conversa_de_email(conv),
            protocolo=garantia_sac.protocolo_da_conversa(conv),
        )
        for conv, como in ligadas
    ]
    out.mensagens = [
        HistoricoMensagemOut(
            id=x.id,
            conversa_id=x.conversa_id,
            autor=x.autor,
            origem=x.origem,
            tipo=x.tipo,
            # O cartão da plataforma gravado como "[Mensagem]" (devolução,
            # envio, variação) com o texto do cartão, como o Atendimento mostra.
            texto=cartoes_plataforma.texto_para_exibir(x),
            anexos=[a for a in (x.anexos or []) if isinstance(a, dict)],
            enviada_em=x.enviada_em or x.created_at,
            status=x.status,
            formulario_sac=x.id in formularios,
            pedido_garantia=(
                g.solicitacao_origem == garantia_pedido.ORIGEM_MENSAGEM
                and g.nao_solicitada_em is None
                and str(x.id) == g.solicitacao_ref
            ),
        )
        for x in mensagens
    ]
    out.marcos.sort(key=lambda mm: mm.em)
    svc.registrar_log(
        session, g.id, "consultou", user, detalhe=f"abriu o histórico ({len(ligadas)} conversa(s))"
    )
    await session.commit()
    return out


# O Resumo do SAC (06/11/2026): as seções e os rótulos da especificação
# ("Resumo do SAC"); a Documentação são os anexos. (seção, [(rótulo, campo)])
_SECAO_DO_CASO = "Atendimento"
_SECOES_DO_SAC = (
    (_SECAO_DO_CASO, (("Protocolo", "protocolo"), ("Data de envio", "data_de_envio"))),
    (
        "Comprador",
        (
            ("Nome", "nome"),
            ("CPF", "cpf"),
            ("E-mail", "email"),
            ("Telefone/WhatsApp", "telefone"),
            ("CEP", "cep"),
            ("Endereço", "endereco"),
        ),
    ),
    (
        "Compra",
        (
            ("Local da compra", "local_compra"),
            ("Loja/vendedor", "loja_vendedor"),
            ("Número do pedido", "pedido"),
            ("Data da compra", "data_compra"),
            ("Data de recebimento", "data_recebimento"),
            ("Nota fiscal", "nota_fiscal"),
            ("Valor pago", "valor_pago"),
        ),
    ),
    (
        "Produto",
        (
            ("Modelo", "modelo"),
            ("Cor", "cor"),
            ("Voltagem", "voltagem"),
            ("Número de série ou lote", "serie"),
        ),
    ),
    (
        "Defeito",
        (
            ("Data do problema", "data_problema"),
            ("Descrição", "descricao"),
            ("Sofreu queda, contato com líquido ou voltagem errada", "queda_liquido_voltagem"),
            ("Tipo de conexão", "tipo_conexao"),
        ),
    ),
)


async def _caso_legivel(session: AsyncSession, caso: GarantiaSac, user: User) -> bool:
    """A pessoa lê a conversa do caso? A mesma trava do /historico (revisão de
    06/11/2026): lê o Atendimento (`acesso.pode_ver`), a caixa/e-mail da
    conversa não está bloqueada para ela (`filtro_conversa`) e, se outra
    pessoa escreveu no protocolo, o caso já foi decidido (confirmado ou
    vinculado à mão)."""
    if not acesso.pode_ver(user):
        return False
    c = AtendimentoConversa
    conv = (
        await session.execute(
            select(c).where(c.id == caso.conversa_id, mail_admin.filtro_conversa(user, c))
        )
    ).scalar_one_or_none()
    if conv is None:
        return False
    return _sac_decidido(caso.confirmado_em, caso.vinculo) or not _com_outra_pessoa_no_sac(conv)


def _sac_decidido(confirmado_em, vinculo: str | None) -> bool:
    """A pessoa já decidiu que o caso é desta garantia (confirmou ou vinculou à
    mão): o alerta de outra pessoa no protocolo não tira mais a conversa."""
    return confirmado_em is not None or vinculo == "manual"


def _com_outra_pessoa_no_sac(conv: AtendimentoConversa) -> bool:
    return bool(garantia_sac.protocolo_da_conversa(conv)) and garantia_sac.com_outra_pessoa(conv)


def _cpf_do_sac(cpf: str | None, ve: bool) -> str | None:
    """O CPF do formulário como o resto do painel: mascarado; completo só para
    quem tem `garantias_cpf`. O que não é CPF de 11 dígitos não sai."""
    if not cpf:
        return None
    return svc.formatar_cpf(cpf) if ve and len(svc.so_digitos(cpf)) == 11 else svc.mascarar_cpf(cpf)


async def _anexos_do_sac(session: AsyncSession, caso: GarantiaSac, user: User) -> list[SacAnexoOut]:
    """Os arquivos do e-mail do formulário (a mensagem do caso), com o papel de
    cada um pelo nome que o formulário cita depois do rótulo da foto."""
    if caso.mensagem_id is None:
        return []
    m, a = MailMessageMeta, MailAttachment
    linhas = (
        await session.execute(
            select(a)
            .join(m, m.message_id == a.message_id)
            .where(
                m.mensagem_id == caso.mensagem_id,
                m.conversa_id == caso.conversa_id,
                m.estado.in_(ESTADOS_COM_TEXTO),
                mail_admin.filtro_mensagem(user, m.message_id),
            )
            .order_by(a.id)
        )
    ).scalars()
    campos = caso.campos if isinstance(caso.campos, dict) else {}
    papeis = {
        str(nome).strip().lower(): papel
        for papel, nome in (campos.get("arquivos") or {}).items()
        if nome
    }
    saida = []
    for x in linhas:
        info = decrypt_json(x.metadata_enc)
        nome = info.get("filename")
        saida.append(
            SacAnexoOut(
                id=str(x.id),
                nome=nome,
                tipo=info.get("content_type"),
                tamanho=x.size,
                papel=papeis.get(str(nome or "").strip().lower())
                or garantia_sac.papel_do_arquivo(nome),
                url=f"/api/garantias/sac/{caso.id}/anexos/{x.id}",
            )
        )
    # Na ordem do formulário (o site numera: 01-produto, 02-etiqueta, …).
    saida.sort(key=lambda s: (s.nome or "").lower())
    return saida


@router.get("/{garantia_id}/sac", response_model=SacDetalheOut | None)
async def sac_da_garantia(
    garantia_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_consultar_ou_registrar)],
) -> SacDetalheOut | None:
    """O Resumo do SAC no topo da conversa: o que o cliente preencheu no
    formulário, com os anexos — o MESMO caso da linha (`_sacs_das`: o
    confirmado e aberto mais antigo; senão o aberto; senão o encerrado mais
    recente). CPF mascarado como no resto do painel. O formulário (e-mail,
    telefone, endereço, descrição) segue a trava do /historico: quem não lê a
    conversa do caso recebe só o protocolo, as datas e o estado."""
    g = await _garantia_visivel(session, garantia_id, user)
    s = GarantiaSac
    caso = (
        await session.execute(
            select(s)
            .where(s.garantia_id == g.id)
            .order_by(*garantia_sac.ordem_dos_casos(s))
            .limit(1)
        )
    ).scalar_one_or_none()
    if caso is None:
        return None
    legivel = await _caso_legivel(session, caso, user)
    campos = dict(caso.campos) if isinstance(caso.campos, dict) and legivel else {}
    campos["protocolo"] = caso.protocolo
    campos["data_de_envio"] = caso.recebido_em.astimezone(svc.SAO_PAULO).strftime("%d/%m/%Y %H:%M")
    campos["cpf"] = _cpf_do_sac(campos.get("cpf"), _ve_cpf(user))
    pessoas = await _pessoas(session, {caso.confirmado_por, caso.encerrado_por})
    return SacDetalheOut(
        id=caso.id,
        protocolo=caso.protocolo,
        recebido_em=caso.recebido_em,
        conversa_id=caso.conversa_id,
        confirmado_em=caso.confirmado_em,
        confirmado_por=_ref(pessoas, caso.confirmado_por) if caso.confirmado_em else None,
        encerrado_em=caso.encerrado_em,
        encerrado_por=_ref(pessoas, caso.encerrado_por),
        encerrado_motivo=caso.encerrado_motivo,
        secoes=[
            SacSecaoOut(
                titulo=titulo,
                campos=[
                    SacCampoOut(rotulo=rotulo, valor=str(v) if (v := campos.get(chave)) else None)
                    for rotulo, chave in itens
                ],
            )
            for titulo, itens in _SECOES_DO_SAC
            # Sem a leitura da conversa: só a seção Atendimento (protocolo e data).
            if legivel or titulo == _SECAO_DO_CASO
        ],
        anexos=await _anexos_do_sac(session, caso, user) if legivel else [],
    )


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
        scope = await escopo_do_atendimento(session, user)
        await _sem_duplicata(session, cpf_final, nf_final, serie_final, chave, g.id, scope)

    mudancas: list[tuple[str, str | None, str | None]] = []
    avisos: list[str] = []
    if novo_pedido is not None:
        mudancas.append(("pedido", g.pedido_bling, novo_pedido.numero))
        anterior = g.data_inicio
        svc.aplicar_pedido(g, novo_pedido, nf_final, serie_final, nota)
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
    if muda_nf:
        svc.aplicar_nf(g, nf_final, serie_final, nota)
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
    if novo_pedido is not None:
        # Outro pedido = outro produto: o prazo da Tabela de Preços é relido.
        await garantia_prazo.aplicar_prazos(session, ids=[g.id], ator=user)
        # O aviso de entrega só depois do prazo do produto novo (relido: o
        # UPDATE do aplicar_prazos não mexe no objeto). O vendido sem garantia
        # (prazo 0) não espera a entrega (a regra da prévia).
        await session.refresh(g, ["prazo_meses"])
        if g.data_inicio is None and g.prazo_meses != 0:
            avisos.append(novo_pedido.aviso_sem_entrega)
        # O caso do SAC (revisão de 06/11/2026). O CONFIRMADO fica: a pessoa já
        # decidiu que o caso é desta garantia (é a mesma compra, corrigida), e
        # a conversa dele continua no histórico. O não confirmado volta para
        # "SAC sem pedido" (sem garantia e sem vínculo): o robô reavalia pelo
        # formulário e pode ligar de novo.
        casos = list(
            (
                await session.execute(
                    select(GarantiaSac).where(GarantiaSac.garantia_id == g.id).with_for_update()
                )
            ).scalars()
        )
        confirmadas = {k.conversa_id for k in casos if k.confirmado_em is not None}
        for k in casos:
            if k.confirmado_em is None:
                await garantia_sac.desligar_conversa(session, k)
        # As outras conversas eram do pedido ANTIGO (pode ser outra pessoa):
        # saem junto com a troca, com o robô ligado ou não.
        apagar = GarantiaConversa.__table__.delete().where(GarantiaConversa.garantia_id == g.id)
        if confirmadas:
            apagar = apagar.where(GarantiaConversa.conversa_id.not_in(confirmadas))
        await session.execute(apagar)
        # O pedido de garantia que o robô achou nelas (mensagem, SAC,
        # devolução) também era do pedido antigo: o robô reavalia o novo —
        # menos com o caso do SAC confirmado (a garantia foi acionada).
        if g.solicitacao_origem in garantia_pedido.ORIGENS_DO_ROBO and not confirmadas:
            svc.registrar_log(
                session,
                g.id,
                "alterou",
                user,
                campo="pedido_de_garantia",
                anterior=garantia_pedido.ORIGEM_ROTULOS.get(g.solicitacao_origem),
                novo="o robô reavalia pelo pedido novo",
            )
            g.solicitada_em = None
            g.solicitacao_origem = None
            g.solicitacao_ref = None
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise _erro(409, "garantia_duplicada", "nf_numero") from e
    if novo_pedido is not None:
        await _religar_conversas(session, g)
    return await _detalhe(session, g, user, avisos=avisos)


async def _religar_conversas(session: AsyncSession, g: Garantia) -> None:
    """Garantia automática (28/10/2026), com o robô ligado: a garantia
    cadastrada (ou que mudou de pedido) na tela ganha as conversas do pedido na
    hora. Melhor esforço, num SAVEPOINT: a falha não desfaz o cadastro (já
    gravado) nem expira a garantia da resposta — o robô liga depois."""
    if not get_settings().garantia_automatica:
        return
    from app.services import garantia_auto

    gid = g.id
    try:
        async with session.begin_nested():
            await garantia_auto.ligar_conversas(session, garantia_ids=[gid])
        await session.commit()
    except Exception:  # noqa: BLE001 — o cadastro já foi; o robô liga depois
        logger.warning("garantia_religar_conversas_falhou", garantia_id=gid, exc_info=True)


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
    # O atendimento registrado é pedido de garantia (se ainda não era). Relê
    # a garantia (com trava): o robô pode ter marcado durante o download.
    await session.refresh(g, with_for_update=True)
    garantia_pedido.marcar_pela_pessoa(g, quando, garantia_pedido.ORIGEM_ATENDIMENTO)
    svc.registrar_log(
        session,
        g.id,
        "registrou_atendimento",
        user,
        detalhe=f"atendimento #{at.id} · "
        f"{svc.TIPO_PROBLEMA_ROTULOS.get(body.tipo_problema, body.tipo_problema)} · "
        f"{svc.COBERTURA_ROTULOS[cobertura]} · conversa {c.plataforma}",
    )
    await session.commit()
    await session.refresh(at)
    arquivos = await _arquivos_dos(session, [at.id])
    return _atendimento_out(at, g, arquivos.get(at.id, []))


@router.put("/{garantia_id}/pedido", response_model=GarantiaSalvaOut)
async def marcar_pedido(
    garantia_id: int,
    body: PedidoMarcar,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_cadastrar)],
) -> GarantiaSalvaOut:
    """A pessoa corrige o robô (28/10/2026): "marcar como pedido de garantia"
    (entra no Painel) ou "não é pedido de garantia" (sai do Painel e o robô não
    marca de novo). Fica no log."""
    g = await _garantia_visivel(session, garantia_id, user)
    agora = datetime.now(UTC)
    if body.pedido:
        if g.solicitada_em is not None and g.nao_solicitada_em is None:
            return await _detalhe(session, g, user)
        garantia_pedido.marcar_pela_pessoa(g, agora)
        svc.registrar_log(
            session, g.id, "marcou_pedido", user, detalhe="marcou como pedido de garantia"
        )
    else:
        if g.nao_solicitada_em is not None:
            return await _detalhe(session, g, user)
        g.nao_solicitada_em = agora
        g.nao_solicitada_por = user.id
        svc.registrar_log(session, g.id, "tirou_pedido", user, detalhe="não é pedido de garantia")
    g.atualizado_em = agora
    g.atualizado_por = user.id
    await session.commit()
    return await _detalhe(session, g, user)


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
