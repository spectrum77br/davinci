"""Marketing › Conferência — relatório semanal por loja (Shopee 06/10/2026; ML e Amazon 07/10).

Doc para a equipe: docs/conferencia-shopee.md. A regra mora em
services/conferencia_shopee (fila, cálculo, arquivos, aviso no Threema); aqui
só as portas.

Executor do Mac (X-Agent-Token = `marketing_agent_token`, o mesmo do robô de
horários da Shopee; vazio = fechado):
  POST /agent/lease                         uma loja por vez (ou job null)
  POST /agent/coletas/{id}/resultado        status + números (até 3 MB; forma
                                            do ColetaDados v1 conferida → 422)

Tela (Marketing ver/editar):
  GET  /execucoes?plataforma=               as últimas rodadas da plataforma
  POST /execucoes?plataforma=               "Gerar agora" (editar)
  GET  /execucoes/{id}                      rodada + coletas + relatório
  POST /execucoes/{id}/recalcular           refaz o relatório (editar)
  POST /execucoes/{id}/cancelar             para a rodada (editar)
  GET  /execucoes/{id}/arquivo/{fmt}        xlsx | csv | md | json | html
  GET  /execucoes/{id}/excel/link?t=…       o Excel do Threema, SEM login
  GET  /contas?plataforma= · PUT /contas/{id}
                                            a lista de lojas (PUT: editar)
  GET  /contas/integracoes?plataforma=      integrações do DaVinci para ligar
                                            uma conta do ML/Amazon

`plataforma` = shopee (padrão) | ml | amazon. ML e Amazon são coletados pelo
SERVIDOR (services/conferencia_shopee/servidor.py): "Gerar agora" cria a
rodada e enfileira o job no worker. O executor do Mac (/agent/*) só recebe
coleta da Shopee.

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
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, is_unique_violation
from app.deps.auth import require_permission
from app.models import (
    ConferenciaShopeeColeta,
    ConferenciaShopeeConta,
    ConferenciaShopeeExecucao,
    Integration,
    User,
)
from app.services.conferencia_shopee import (
    calculo,
    classificacao,
    fila,
    plataformas,
    saida,
    servidor,
    threema_aviso,
)
from app.services.conferencia_shopee.dados import ColetaDadosV1, ConfereDados

# Reexportado: o modelo do ColetaDados morava aqui até 07/10/2026.
__all__ = ["ColetaDadosV1", "router"]

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
    "plataforma_invalida": 422,
    "status_invalido": 422,
}

Plataforma = Literal["shopee", "ml", "amazon"]

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


# ColetaDados versao 1 (contrato §4; apps/executor/src/conferencia_util.ts): a
# forma conferida mora em services/conferencia_shopee/dados.py (o job do
# servidor usa a mesma régua).


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
    """Fecha a execução se não sobrou loja na fila e manda o aviso (o mesmo do
    job do servidor: servidor.fechar_e_avisar)."""
    return await servidor.fechar_e_avisar(session, execucao_id, agora)


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
            ConfereDados.model_validate({"dados": body.dados})
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
        "plataforma": ex.plataforma,
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


def _conta_out(c: ConferenciaShopeeConta, integracao: Integration | None = None) -> dict[str, Any]:
    """`integracao_nome`: o que a tela mostra no ML/Amazon no lugar do perfil
    do AdsPower (a Shopee continua com `adspower_user_id`)."""
    return {
        "id": c.id,
        "plataforma": c.plataforma,
        "adspower_user_id": c.adspower_user_id,
        "integration_id": c.integration_id,
        "integracao_nome": integracao.name if integracao is not None else None,
        "integracao_arquivada": (
            integracao.archived_at is not None if integracao is not None else None
        ),
        "bling_loja_id": c.bling_loja_id,
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
    plataforma: Plataforma = "shopee",
) -> list[dict]:
    """As últimas rodadas da plataforma, mais nova primeiro, com o placar das
    lojas (`ok` = com números, ok ou parcial; `sem_dados` = encerradas sem)."""
    execucoes = (
        await session.execute(
            select(ConferenciaShopeeExecucao)
            .where(ConferenciaShopeeExecucao.plataforma == plataforma)
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
            "plataforma": e.plataforma,
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
    # Também pode vir na query (?plataforma=); o corpo vence.
    plataforma: Plataforma | None = None


@router.post("/execucoes")
async def criar_execucao(
    body: ExecucaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
    plataforma: Plataforma = "shopee",
) -> dict:
    """"Gerar agora": parcial numa segunda vira semanal (não há dia ainda). No
    ML e na Amazon a coleta é do servidor: a rodada nasce e o job entra na
    fila do worker na hora (Redis fora → o varredor enfileira em até 10 min)."""
    plataforma = body.plataforma or plataforma
    agora = _agora()
    try:
        ex = await fila.criar_execucao(
            session, body.tipo, "manual", user.email, agora, plataforma=plataforma
        )
    except fila.FilaError as e:
        raise _http(e) from e
    await session.commit()
    if plataformas.do_servidor(plataforma):
        await servidor.enfileirar(ex.id, agora)
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
    plataforma: Plataforma = "shopee",
) -> list[dict]:
    linhas = (
        await session.execute(
            select(ConferenciaShopeeConta, Integration)
            .outerjoin(Integration, Integration.id == ConferenciaShopeeConta.integration_id)
            .where(ConferenciaShopeeConta.plataforma == plataforma)
        )
    ).all()
    linhas = sorted(linhas, key=lambda r: (r[0].ordem, classificacao.sem_acento(r[0].nome)))
    return [_conta_out(c, i) for c, i in linhas]


@router.get("/contas/integracoes")
async def listar_integracoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    plataforma: Literal["ml", "amazon"] = "ml",
) -> list[dict]:
    """As integrações do DaVinci da plataforma, para ligar uma conta do
    ML/Amazon (`integration_id` no PUT). Arquivadas vêm marcadas, no fim."""
    integracoes = (
        await session.execute(
            select(Integration).where(Integration.platform == plataforma)
        )
    ).scalars().all()
    integracoes = sorted(
        integracoes,
        key=lambda i: (i.archived_at is not None, classificacao.sem_acento(i.name)),
    )
    return [
        {"id": i.id, "nome": i.name, "arquivada": i.archived_at is not None}
        for i in integracoes
    ]


class ContaIn(BaseModel):
    nome: str | None = Field(default=None, max_length=80)
    grupo: Literal["mala", "celular"] | None = None
    ativo: bool | None = None
    ordem: int | None = Field(default=None, ge=0, le=9999)
    observacao: str | None = Field(default=None, max_length=500)
    # Só ML/Amazon: a integração do DaVinci e a loja do Bling (null desliga).
    integration_id: UUID | None = None
    bling_loja_id: str | None = Field(default=None, max_length=20)

    @field_validator("bling_loja_id")
    @classmethod
    def _loja(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not (v.isascii() and v.isdigit()):
            raise ValueError("o id da loja do Bling é só número")
        return v

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
    """Muda nome/grupo/ativo/ordem/observação (e, no ML/Amazon, a integração e
    a loja do Bling). Vale da PRÓXIMA rodada em diante: a rodada guarda a foto
    do nome e do grupo de cada loja.

    ML/Amazon: a integração tem de ser da mesma plataforma
    (`integracao_invalida`) e não estar em outra conta (`integracao_em_uso`);
    a loja do Bling também não pode estar em outra conta da plataforma
    (`loja_bling_em_uso` — contaria as mesmas vendas duas vezes); conta ativa
    precisa das duas ligações (`conta_sem_integracao`, `conta_sem_loja_bling`)
    — sem elas a coleta do servidor não tem de onde ler."""
    conta = await session.get(ConferenciaShopeeConta, conta_id)
    if conta is None:
        raise HTTPException(404, detail={"code": "conta_nao_encontrada"})
    campos = body.model_fields_set
    servidor_ = plataformas.do_servidor(conta.plataforma)
    if not servidor_ and campos & {"integration_id", "bling_loja_id"}:
        raise HTTPException(422, detail={"code": "campo_so_ml_amazon"})
    integracao: Integration | None = None
    if "integration_id" in campos and body.integration_id is not None:
        integracao = await session.get(Integration, body.integration_id)
        if integracao is None or str(integracao.platform) != conta.plataforma:
            raise HTTPException(422, detail={"code": "integracao_invalida"})
    if "bling_loja_id" in campos and body.bling_loja_id:
        # Antes de mexer na conta (o autoflush do SELECT gravaria a loja nova).
        outra = (
            await session.execute(
                select(ConferenciaShopeeConta.nome)
                .where(
                    ConferenciaShopeeConta.plataforma == conta.plataforma,
                    ConferenciaShopeeConta.bling_loja_id == body.bling_loja_id,
                    ConferenciaShopeeConta.id != conta.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if outra is not None:
            raise HTTPException(409, detail={"code": "loja_bling_em_uso", "conta": outra})
    for campo in ("nome", "grupo", "ativo", "ordem"):
        valor = getattr(body, campo)
        if campo in campos and valor is not None:
            setattr(conta, campo, valor)
    if "observacao" in campos:
        conta.observacao = (body.observacao or "").strip() or None
    if "integration_id" in campos:
        conta.integration_id = body.integration_id
    if "bling_loja_id" in campos:
        conta.bling_loja_id = body.bling_loja_id
    if servidor_ and conta.ativo and conta.integration_id is None:
        await session.rollback()
        raise HTTPException(422, detail={"code": "conta_sem_integracao"})
    if servidor_ and conta.ativo and not conta.bling_loja_id:
        await session.rollback()
        raise HTTPException(422, detail={"code": "conta_sem_loja_bling"})
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if is_unique_violation(e):
            # Dois salvando ao mesmo tempo: o único parcial diz qual ligação bateu.
            loja = "bling_loja_id" in str(e.orig)
            codigo = "loja_bling_em_uso" if loja else "integracao_em_uso"
            raise HTTPException(409, detail={"code": codigo}) from e
        raise
    await session.refresh(conta)
    if integracao is None and conta.integration_id is not None:
        integracao = await session.get(Integration, conta.integration_id)
    return _conta_out(conta, integracao)
