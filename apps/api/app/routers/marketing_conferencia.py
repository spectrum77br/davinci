"""Marketing › Conferência Shopee — relatório semanal por loja (06/10/2026).

Doc para a equipe: docs/conferencia-shopee.md. A regra mora em
services/conferencia_shopee (fila, cálculo, arquivos, aviso no Threema); aqui
só as portas.

Executor do Mac (X-Agent-Token = `marketing_agent_token`, o mesmo do robô de
horários da Shopee; vazio = fechado):
  POST /agent/lease                         uma loja por vez (ou job null)
  POST /agent/coletas/{id}/resultado        status + números (até 3 MB; forma
                                            do ColetaDados v1 conferida → 422)

Tela (Marketing ver/editar):
  GET  /execucoes                           as últimas rodadas
  POST /execucoes                           "Gerar agora" (editar)
  GET  /execucoes/{id}                      rodada + coletas + relatório
  POST /execucoes/{id}/recalcular           refaz o relatório (editar)
  POST /execucoes/{id}/cancelar             para a rodada (editar)
  GET  /execucoes/{id}/arquivo/{fmt}        xlsx | csv | md | json | html
  GET  /execucoes/{id}/excel/link?t=…       o Excel do Threema, SEM login
  GET  /contas · PUT /contas/{id}           a lista de lojas (PUT: editar)

As rotas /agent/* vêm antes de qualquer rota com {id}. Montado no main.py só
com `enable_marketing`, como o resto do Marketing.
"""

import json
import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    ConferenciaShopeeColeta,
    ConferenciaShopeeConta,
    ConferenciaShopeeExecucao,
    User,
)
from app.services.conferencia_shopee import calculo, classificacao, fila, saida, threema_aviso

logger = structlog.get_logger()

router = APIRouter(prefix="/api/marketing/conferencia-shopee", tags=["marketing-conferencia"])
_ver = require_permission("marketing", "view")
_editar = require_permission("marketing", "edit")

# Corpo do resultado: ~1 MB numa loja grande (centenas de itens × 4 semanas);
# acima disto é engano do executor, e ele manda só o status.
MAX_CORPO = 3 * 1024 * 1024

_STATUS_HTTP = {
    "conferencia_em_andamento": 409,
    "conferencia_sem_contas": 409,
    "conferencia_nao_pronta": 409,
    "conferencia_nao_coletando": 409,
    "coleta_ja_concluida": 409,
    "coleta_nao_encontrada": 404,
    "tipo_invalido": 422,
    "origem_invalida": 422,
    "status_invalido": 422,
}

_ARQUIVOS: dict[str, tuple[str, str]] = {
    # fmt → (extensão, media type)
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "csv": ("csv", "text/csv; charset=utf-8"),
    "md": ("md", "text/markdown; charset=utf-8"),
    "json": ("json", "application/json"),
    "html": ("html", "text/html; charset=utf-8"),
}


def _agora() -> datetime:
    return datetime.now(UTC)


def _http(e: fila.FilaError) -> HTTPException:
    return HTTPException(_STATUS_HTTP.get(e.code, 400), detail={"code": e.code})


# ─── executor do Mac ──────────────────────────────────────────────────


async def _require_agent_token(
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
) -> None:
    esperado = get_settings().marketing_agent_token
    # Em bytes (como no adspower_agent): com um caractere não-ASCII no
    # cabeçalho, compare_digest de dois textos levanta TypeError e a resposta
    # vira 500 em vez de 401.
    if (
        not esperado
        or not x_agent_token
        or not secrets.compare_digest(
            x_agent_token.encode("utf-8", "surrogateescape"), esperado.encode("utf-8")
        )
    ):
        raise HTTPException(401, detail={"code": "agent_unauthorized"})


class LeaseIn(BaseModel):
    # O nome é cortado em 100 no serviço: nome comprido não pode travar o executor.
    agente: str = "executor"


# ColetaDados versao 1 (contrato §4; apps/executor/src/conferencia_util.ts).
# Só CONFERE a forma — o que se guarda é o dict cru que chegou. Campo a mais
# passa; lista de coisa que não é objeto, número que não é número ou versão
# desconhecida → 422: um item torto guardado derrubaria o cálculo da rodada.
_Numero = float | None


class _Forma(BaseModel):
    model_config = ConfigDict(extra="allow")


class _AfiliadosTotais(_Forma):
    vendas: _Numero = None
    comissao: _Numero = None
    pedidos: _Numero = None


class _AfiliadoItem(_Forma):
    item_id: str | int | None = None
    nome: str | None = None
    categoria_id: int | str | None = None
    vendas: _Numero = None
    comissao: _Numero = None
    pedidos: _Numero = None


class _AdsTotais(_Forma):
    impressoes: _Numero = None
    cliques: _Numero = None
    gasto: _Numero = None
    vendas: _Numero = None
    pedidos: _Numero = None


class _AdsItem(_AdsTotais):
    item_id: str | int | None = None
    nome: str | None = None
    tipo: str | None = None


class _VendasTotais(_Forma):
    valor: _Numero = None
    pedidos: _Numero = None


class _VendaItem(_VendasTotais):
    item_id: str | int | None = None
    nome: str | None = None


class _SemanaDados(_Forma):
    inicio: str
    fim: str
    afiliados: _AfiliadosTotais | None = None
    afiliados_itens: list[_AfiliadoItem] | None = None
    ads: _AdsTotais | None = None
    ads_itens: list[_AdsItem] | None = None
    vendas: _VendasTotais | None = None
    vendas_itens: list[_VendaItem] | None = None
    avisos: list[str] | None = None


class _LoginLoja(_Forma):
    username: str | None = None
    shopid: int | str | None = None
    shop_name: str | None = None


class ColetaDadosV1(_Forma):
    versao: Literal[1]
    coletado_em: str | None = None
    duracao_s: _Numero = None
    chamadas: _Numero = None
    login: _LoginLoja | None = None
    login_auto_usado: bool | None = None
    saldo_ads: _Numero = None
    afiliados_ultimo_dia: str | None = None
    semanas: list[_SemanaDados]
    avisos: list[str] | None = None


class _ConfereDados(BaseModel):
    # Embrulho só para o erro apontar ("dados", "semanas", 0, …).
    dados: ColetaDadosV1


class ResultadoIn(BaseModel):
    status: Literal[
        "ok",
        "parcial",
        "deslogada",
        "perfil_em_uso",
        "aguardando_afiliados",
        "sem_automacao",
        "bloqueada",
        "interrompida",
        "erro",
    ]
    erro: str | None = None
    dados: dict[str, Any] | None = None


async def _fechar_e_avisar(session: AsyncSession, execucao_id: UUID, agora: datetime) -> bool:
    """Fecha a execução se não sobrou loja na fila e manda o aviso. Depois do
    commit do resultado: se o cálculo falhar, o resultado da loja já está
    salvo e o varredor do worker tenta fechar de novo. O Threema nunca
    derruba a resposta."""
    try:
        execucao = await fila.fechar_se_terminou(session, execucao_id, agora)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("conferencia_shopee_fechar_falhou", execucao=str(execucao_id))
        return False
    if execucao is None:
        return False
    try:
        await threema_aviso.enviar_pendente(session, execucao, agora)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("conferencia_shopee_threema_falhou", execucao=str(execucao_id))
    return True


@router.post("/agent/lease")
async def agent_lease(
    body: LeaseIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _tok: Annotated[None, Depends(_require_agent_token)],
) -> dict:
    """Uma loja para o executor coletar agora; `{"job": null}` = nada (sem
    rodada coletando, lojas adiadas para depois, ou passou do corte)."""
    agora = _agora()
    job, esgotadas = await fila.lease(
        session, body.agente, agora, login_auto=get_settings().conferencia_shopee_login_auto
    )
    await session.commit()
    # Loja encerrada por "muitas tentativas" pode ter sido a última da rodada.
    for execucao_id in esgotadas:
        await _fechar_e_avisar(session, execucao_id, agora)
    return {"job": job}


async def _corpo_limitado(request: Request) -> bytes:
    """O corpo cru, recusando acima de MAX_CORPO SEM ler tudo (Content-Length
    primeiro; sem ele ou mentindo, conta o que chega)."""
    tamanho = request.headers.get("content-length")
    if tamanho and tamanho.isdigit() and int(tamanho) > MAX_CORPO:
        raise HTTPException(413, detail={"code": "conferencia_payload_grande"})
    partes: list[bytes] = []
    lido = 0
    async for parte in request.stream():
        lido += len(parte)
        if lido > MAX_CORPO:
            raise HTTPException(413, detail={"code": "conferencia_payload_grande"})
        partes.append(parte)
    return b"".join(partes)


@router.post("/agent/coletas/{coleta_id}/resultado")
async def agent_resultado(
    coleta_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    _tok: Annotated[None, Depends(_require_agent_token)],
) -> dict:
    """O executor devolve uma loja. `perfil_em_uso`/`aguardando_afiliados`
    voltam para a fila em 10 min; o resto encerra a loja e, se era a última,
    fecha o relatório e avisa no Threema na mesma chamada."""
    bruto = await _corpo_limitado(request)
    try:
        body = ResultadoIn.model_validate(json.loads(bruto or b"null"))
        if body.dados is not None:
            _ConfereDados.model_validate({"dados": body.dados})
    except (ValueError, RecursionError, ValidationError) as e:
        erros = (
            e.errors(include_url=False, include_input=False, include_context=False)
            if isinstance(e, ValidationError)
            else None
        )
        raise HTTPException(
            422, detail={"code": "conferencia_payload_invalido", "erros": erros}
        ) from e
    agora = _agora()
    try:
        coleta = await fila.registrar_resultado(
            session, coleta_id, body.status, body.erro, body.dados, agora
        )
    except fila.FilaError as e:
        raise _http(e) from e
    await session.commit()
    # Lido antes de fechar: um rollback lá expira o objeto.
    resposta = {
        "ok": True,
        "status": coleta.status,
        "reagendada": coleta.status == "pendente",
        "disponivel_apos": coleta.disponivel_apos if coleta.status == "pendente" else None,
        "execucao_pronta": False,
    }
    if coleta.status not in fila.NA_FILA:
        resposta["execucao_pronta"] = await _fechar_e_avisar(session, coleta.execucao_id, agora)
    return resposta


# ─── tela ─────────────────────────────────────────────────────────────


def _execucao_out(ex: ConferenciaShopeeExecucao) -> dict[str, Any]:
    return {
        "id": ex.id,
        "tipo": ex.tipo,
        "origem": ex.origem,
        "status": ex.status,
        "criado_por": ex.criado_por,
        "semanas": ex.semanas,
        "afiliados_ate": ex.afiliados_ate,
        "esperar_afiliados_ate": ex.esperar_afiliados_ate,
        "corte": ex.corte,
        "prazo": ex.prazo,
        "criado_em": ex.criado_em,
        "finalizado_em": ex.finalizado_em,
        "threema_enviado_em": ex.threema_enviado_em,
    }


def _coleta_out(c: ConferenciaShopeeColeta) -> dict[str, Any]:
    return {
        "id": c.id,
        "conta_id": c.conta_id,
        "nome": c.nome,
        "grupo": c.grupo,
        "status": c.status,
        "erro": c.erro,
        "tentativas": c.tentativas,
        "adiamentos": c.adiamentos,
        "adiamentos_perfil": c.adiamentos_perfil,
        "disponivel_apos": c.disponivel_apos,
        "concluido_em": c.concluido_em,
    }


def _conta_out(c: ConferenciaShopeeConta) -> dict[str, Any]:
    return {
        "id": c.id,
        "adspower_user_id": c.adspower_user_id,
        "nome": c.nome,
        "grupo": c.grupo,
        "ativo": c.ativo,
        "ordem": c.ordem,
        "conta_key": c.conta_key,
        "observacao": c.observacao,
    }


async def _execucao(session: AsyncSession, execucao_id: UUID) -> ConferenciaShopeeExecucao:
    ex = await session.get(ConferenciaShopeeExecucao, execucao_id)
    if ex is None:
        raise HTTPException(404, detail={"code": "conferencia_nao_encontrada"})
    return ex


async def _detalhe(session: AsyncSession, ex: ConferenciaShopeeExecucao) -> dict[str, Any]:
    coletas = (
        await session.execute(
            select(ConferenciaShopeeColeta)
            .where(ConferenciaShopeeColeta.execucao_id == ex.id)
            .order_by(ConferenciaShopeeColeta.criado_em, ConferenciaShopeeColeta.id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    return {
        "execucao": _execucao_out(ex),
        "coletas": [_coleta_out(c) for c in coletas],
        # Enquanto coleta, a tela mostra o andamento (relatório ainda não há).
        "relatorio": ex.relatorio if ex.status != "coletando" else None,
    }


@router.get("/execucoes")
async def listar_execucoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    limite: Annotated[int, Query(ge=1, le=200)] = 30,
) -> list[dict]:
    """As últimas rodadas, mais nova primeiro, com o placar das lojas
    (`ok` = com números, ok ou parcial; `sem_dados` = encerradas sem)."""
    execucoes = (
        await session.execute(
            select(ConferenciaShopeeExecucao)
            .order_by(ConferenciaShopeeExecucao.criado_em.desc())
            .limit(limite)
        )
    ).scalars().all()
    ids = [e.id for e in execucoes]
    placar: dict[UUID, dict[str, int]] = {}
    if ids:
        c = ConferenciaShopeeColeta
        linhas = (
            await session.execute(
                select(
                    c.execucao_id,
                    func.count(),
                    func.count().filter(c.status.in_(calculo.STATUS_COM_DADOS)),
                    func.count().filter(
                        c.status.not_in((*fila.NA_FILA, *calculo.STATUS_COM_DADOS))
                    ),
                )
                .where(c.execucao_id.in_(ids))
                .group_by(c.execucao_id)
            )
        ).all()
        placar = {
            execucao_id: {"contas": contas, "ok": ok, "sem_dados": sem}
            for execucao_id, contas, ok, sem in linhas
        }
    return [
        {
            "id": e.id,
            "tipo": e.tipo,
            "origem": e.origem,
            "status": e.status,
            "criado_em": e.criado_em,
            "finalizado_em": e.finalizado_em,
            "semanas": e.semanas,
            "resumo": placar.get(e.id, {"contas": 0, "ok": 0, "sem_dados": 0}),
        }
        for e in execucoes
    ]


class ExecucaoIn(BaseModel):
    tipo: Literal["semanal", "parcial"]


@router.post("/execucoes")
async def criar_execucao(
    body: ExecucaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict:
    """"Gerar agora": parcial numa segunda vira semanal (não há dia ainda)."""
    try:
        ex = await fila.criar_execucao(session, body.tipo, "manual", user.email, _agora())
    except fila.FilaError as e:
        raise _http(e) from e
    await session.commit()
    return await _detalhe(session, ex)


@router.get("/execucoes/{execucao_id}")
async def detalhe_execucao(
    execucao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    return await _detalhe(session, await _execucao(session, execucao_id))


@router.post("/execucoes/{execucao_id}/recalcular")
async def recalcular_execucao(
    execucao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_editar)],
) -> dict:
    """Refaz o relatório com os números guardados (vínculo novo no DaVinci,
    loja mudada de grupo não conta: o grupo é a foto da rodada)."""
    ex = await _execucao(session, execucao_id)
    try:
        ex = await fila.recalcular(session, ex, _agora())
    except fila.FilaError as e:
        raise _http(e) from e
    await session.commit()
    return await _detalhe(session, ex)


@router.post("/execucoes/{execucao_id}/cancelar")
async def cancelar_execucao(
    execucao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_editar)],
) -> dict:
    ex = await _execucao(session, execucao_id)
    try:
        ex = await fila.cancelar(session, ex, _agora())
    except fila.FilaError as e:
        raise _http(e) from e
    await session.commit()
    return await _detalhe(session, ex)


def _arquivo(rel: dict, fmt: str) -> Response:
    ext, media = _ARQUIVOS[fmt]
    if fmt == "xlsx":
        conteudo: bytes | str = saida.excel(rel).getvalue()
    elif fmt == "csv":
        conteudo = saida.csv(rel)
    elif fmt == "md":
        conteudo = saida.markdown(rel)
    elif fmt == "json":
        conteudo = saida.json_bytes(rel)
    else:
        conteudo = saida.html(rel)
    if isinstance(conteudo, str):
        conteudo = conteudo.encode("utf-8")
    return Response(
        content=conteudo,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{saida.nome_arquivo(rel, ext)}"'
        },
    )


async def _relatorio(session: AsyncSession, execucao_id: UUID) -> dict:
    ex = await _execucao(session, execucao_id)
    if not ex.relatorio or ex.status == "coletando":
        raise HTTPException(404, detail={"code": "conferencia_sem_relatorio"})
    return ex.relatorio


@router.get("/execucoes/{execucao_id}/arquivo/{fmt}")
async def arquivo_execucao(
    execucao_id: UUID,
    fmt: Literal["xlsx", "csv", "md", "json", "html"],
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> Response:
    """O relatório congelado em arquivo (o HTML é uma página só, tema claro)."""
    return _arquivo(await _relatorio(session, execucao_id), fmt)


@router.get("/execucoes/{execucao_id}/excel/link")
async def excel_link(
    execucao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    t: str = "",
) -> Response:
    """O Excel do link que vai no Threema — baixa SEM login, só com o token
    assinado DAQUELA execução, que vale 7 dias (services/conferencia_shopee/
    threema_aviso). O token sai do access log (mascarar_link_no_access_log)."""
    if not threema_aviso.confere_excel(execucao_id, t, _agora()):
        raise HTTPException(403, detail={"code": "conferencia_link_invalido"})
    return _arquivo(await _relatorio(session, execucao_id), "xlsx")


_RX_LINK_NO_LOG = re.compile(
    r"(/api/marketing/conferencia-shopee/execucoes/[^/\s?]+/excel/link\?(?:[^\s]*&)?t=)[^\s&]+"
)


def mascarar_link_no_access_log() -> None:
    """Tira o token do link do Excel do access log do uvicorn: quem lê o log
    do container ganharia 7 dias de acesso ao relatório sem login. Mesmo
    molde do marketing_creatives.mascarar_link_no_access_log."""

    class _Mascara(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            args = record.args
            if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
                record.args = (*args[:2], _RX_LINK_NO_LOG.sub(r"\1***", args[2]), *args[3:])
            return True

    logging.getLogger("uvicorn.access").addFilter(_Mascara())


# ─── lista de lojas ───────────────────────────────────────────────────


@router.get("/contas")
async def listar_contas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> list[dict]:
    contas = (await session.execute(select(ConferenciaShopeeConta))).scalars().all()
    contas = sorted(contas, key=lambda c: (c.ordem, classificacao.sem_acento(c.nome)))
    return [_conta_out(c) for c in contas]


class ContaIn(BaseModel):
    nome: str | None = Field(default=None, max_length=80)
    grupo: Literal["mala", "celular"] | None = None
    ativo: bool | None = None
    ordem: int | None = Field(default=None, ge=0, le=9999)
    observacao: str | None = Field(default=None, max_length=500)

    @field_validator("nome")
    @classmethod
    def _nome(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("nome vazio")
        return v


@router.put("/contas/{conta_id}")
async def editar_conta(
    conta_id: UUID,
    body: ContaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_editar)],
) -> dict:
    """Muda nome/grupo/ativo/ordem/observação. Vale da PRÓXIMA rodada em
    diante: a rodada guarda a foto do nome e do grupo de cada loja."""
    conta = await session.get(ConferenciaShopeeConta, conta_id)
    if conta is None:
        raise HTTPException(404, detail={"code": "conta_nao_encontrada"})
    campos = body.model_fields_set
    for campo in ("nome", "grupo", "ativo", "ordem"):
        valor = getattr(body, campo)
        if campo in campos and valor is not None:
            setattr(conta, campo, valor)
    if "observacao" in campos:
        conta.observacao = (body.observacao or "").strip() or None
    await session.commit()
    await session.refresh(conta)
    return _conta_out(conta)
