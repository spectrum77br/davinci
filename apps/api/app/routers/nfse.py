"""Cadastros › Emissão de Serviço — NFS-e pela NFE.io (28/09/2026; motor NFE.io 29/09).

Recurso de permissão `emissao_servico` (view/edit/delete). Emitir, reenviar,
conferir e ligar empresa pedem `edit`; cancelar pede `delete`; sincronizar
todas as empresas com a NFE.io é de admin. Quem assina e manda à prefeitura é
a NFE.io: a senha do certificado A1 saiu da emissão (29/09). Continuam a
permissão, o "digite EMITIR" da tela e a trava de produção
(`services/nfse/ambiente`): empresa em Production na NFE.io só emite com
ENV=production e NFSE_PRODUCAO_LIBERADA=true.

Nenhuma rota devolve a chave da NFE.io nem dado sensível da prefeitura.

Senha extra (30/09/2026, Eduardo: "o mesmo esquema de senha do empresas"):
TODAS as rotas deste router exigem a chave `X-Nfse-Token`
(app/security/senha_extra.py). A chave sai de `POST /api/nfse/unlock`, que fica
num router à parte (`router_desbloqueio`) justamente por não poder exigir a
chave que ele mesmo entrega. PDF e XML (`router_arquivos`) aceitam a chave OU
um link de 60 s só daquele arquivo, pedido com a chave em
`POST /emissoes/{id}/link`: link direto do navegador não leva cabeçalho, e a
aba do PDF precisa abrir com o nome do arquivo.

Lote (30/09/2026): `POST /emissoes/lote/arquivos` (.zip de PDFs ou XMLs) e
`POST /emissoes/lote/imprimir` (um PDF só) ficam no router trancado — a tela
manda a chave no cabeçalho — e nunca terminam em /pdf ou /xml (essas são as
do link de 60 s). E-mail da nota: sai pelo DaVinci, nunca mais pela NFE.io.

Integrar empresa (01/10/2026, Eduardo: "algumas empresas nossas não estão
integradas no nfe.io, precisa integrar"): `GET /prestadores/{id}/nfeio/integrar`
mostra o que vai (Receita + NFE.io + certificado guardado, só leitura) e
`POST` cria a empresa na NFE.io (ou acha pelo CNPJ), manda o certificado
guardado, cadastra a Inscrição Municipal em TESTE e liga. Pede `edit` + a senha
extra, e o POST só sai com ENV=production E NFSE_INTEGRAR_LIBERADO=true (o
.env do localhost tem a chave real: lá dá 403 antes de qualquer chamada).
"""

from __future__ import annotations

import contextlib
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin, require_permission
from app.models import Company, User
from app.models.nfse import (
    STATUS_EM_ANDAMENTO,
    CompanyFiscal,
    NfseEmissao,
    NfseEvento,
    NfseModelo,
    NfseTomador,
)
from app.schemas.companies import DesbloqueioIn, DesbloqueioOut
from app.schemas.nfse import (
    AtualizarIn,
    CancelarIn,
    CertificadoGuardadoOut,
    EmailNotaIn,
    EmissaoOut,
    EmitirIn,
    FiscalIn,
    FiscalOut,
    IntegrarIn,
    IntegrarOut,
    IntegrarPreviaOut,
    ItemIn,
    LigarIn,
    LoteArquivosIn,
    LoteImprimirIn,
    ModeloIn,
    ModeloOut,
    MunicipioOut,
    NfeioOut,
    PrestadorOut,
    PreviaIn,
    ReceitaOut,
    TomadorIn,
    TomadorOut,
)
from app.security import senha_extra
from app.security.senha_extra import require_nfse_unlock
from app.services.nfse import ambiente, municipios, nfeio, receita
from app.services.nfse import contas_bling as svc_contas_bling
from app.services.nfse import emissao as svc
from app.services.nfse import empresas as svc_empresas
from app.services.nfse import faturamento as svc_faturamento
from app.services.nfse import integracao as svc_integracao
from app.services.nfse import lote_arquivos as svc_lote
from app.services.nfse import texto as T  # noqa: N812
from app.services.nfse.ambiente import eh_teste, producao_liberada

logger = structlog.get_logger()
# A trava vale para o router inteiro: rota nova já nasce trancada.
router = APIRouter(prefix="/api/nfse", tags=["nfse"], dependencies=[Depends(require_nfse_unlock)])
router_desbloqueio = APIRouter(prefix="/api/nfse", tags=["nfse"])
# PDF e XML: trava própria (a chave OU o link de 60 s daquele arquivo).
router_arquivos = APIRouter(prefix="/api/nfse", tags=["nfse"])

_view = require_permission("emissao_servico", "view")
_edit = require_permission("emissao_servico", "edit")
_delete = require_permission("emissao_servico", "delete")


@router_desbloqueio.post("/unlock", response_model=DesbloqueioOut)
async def desbloquear_nfse(
    body: DesbloqueioIn, u: Annotated[User, Depends(_view)]
) -> DesbloqueioOut:
    """Confere a senha extra (a mesma de Empresas e do Valuation) e devolve uma
    chave de 30 minutos só da Emissão de Serviço (30/09/2026, Eduardo: "aumente
    o tempo de acesso para 30 min"; Empresas e Valuation seguem com 15). Os
    erros somam com as outras telas: 5 seguidos travam a pessoa por 15 minutos."""
    await senha_extra.conferir_senha(body.password, user_id=u.id, escopo="nfse")
    token, ttl = senha_extra.fazer_token("nfse")
    return DesbloqueioOut(token=token, expires_in=ttl)


Sess = Annotated[AsyncSession, Depends(get_session)]


def _http(e: svc.NfseError) -> HTTPException:
    return HTTPException(e.status, detail=e.detail)


@contextlib.asynccontextmanager
async def _nfeio():
    """Cliente da NFE.io; sem chave configurada vira 503 `chave_nfeio`."""
    try:
        cli = nfeio.ClienteNfeio()
    except svc.NfseError as e:
        raise _http(e) from e
    async with cli:
        yield cli


# --- status -------------------------------------------------------------------


@router.get("/status")
async def status_(_u: Annotated[User, Depends(_view)]) -> dict:
    return {
        "provedor": "nfeio",
        "chave_configurada": nfeio.chave_configurada(),
        "producao_liberada": producao_liberada(),
        "integrar_liberado": ambiente.integrar_liberado(),
    }


# --- prestadores (empresas do grupo) -----------------------------------------


def _fiscal_out(f: CompanyFiscal | None) -> FiscalOut | None:
    if f is None:
        return None
    return FiscalOut(
        city_service_code=f.city_service_code,
        federal_service_code=f.federal_service_code,
        c_nbs=f.c_nbs,
        retencao_ir=f.retencao_ir or "auto",  # type: ignore[arg-type]
        email=f.email,
        fone=f.fone,
    )


def _nfeio_out(f: CompanyFiscal | None) -> NfeioOut | None:
    if f is None or not f.nfeio_company_id:
        return None
    ir = svc.calcular_ir(Decimal("1000"), f.nfeio_regime, f.retencao_ir)
    return NfeioOut(
        company_id=f.nfeio_company_id,
        link=nfeio.link(f.nfeio_company_id),
        ambiente=f.nfeio_ambiente,
        teste=eh_teste(f.nfeio_ambiente),
        status_fiscal=f.nfeio_status_fiscal,
        regime=f.nfeio_regime,
        # "Retém IR" = a regra retém numa nota comum (tomador PJ, acima da dispensa).
        retem_ir=ir.retem,
        inscricao_municipal=f.nfeio_im,
        municipio=f.nfeio_municipio,
        uf=f.nfeio_uf,
        cert_status=f.nfeio_cert_status,
        cert_expira=f.nfeio_cert_expira,
        sincronizado_em=f.nfeio_sincronizado_em,
    )


def _prestador_out(
    c: Company,
    f: CompanyFiscal | None,
    *,
    em_operacao: bool = False,
    cert: dict | None = None,
) -> PrestadorOut:
    pend, avisos = svc_empresas.pendencias_e_avisos(c, f)
    return PrestadorOut(
        company_id=c.id,
        apelido=c.apelido,
        razao_social=c.razao_social,
        cnpj=c.cnpj,
        percentual_servico=c.percentual_servico,
        fiscal=_fiscal_out(f),
        nfeio=_nfeio_out(f),
        pronto=not pend,
        pendencias=pend,
        avisos=avisos,
        em_operacao=em_operacao,
        integracao=svc_empresas.situacao_integracao(f),
        certificado_guardado=CertificadoGuardadoOut(**(cert or {})),
    )


def _em_operacao(c: Company, com_loja: set[str]) -> bool:
    doc = T.normalizar_documento(c.cnpj)
    return bool(doc) and doc in com_loja


async def _prestador(session: AsyncSession, company_id: UUID) -> PrestadorOut:
    c = await session.get(Company, company_id)
    if c is None:
        raise HTTPException(
            404, detail={"code": "empresa_nao_encontrada", "mensagem": "Empresa não encontrada."}
        )
    f = await session.get(CompanyFiscal, company_id)
    if f is not None:
        await session.refresh(f)
    com_loja, certs = await svc_empresas.contexto_lista(session, [company_id])
    return _prestador_out(c, f, em_operacao=_em_operacao(c, com_loja), cert=certs.get(company_id))


@router.get("/faturamento")
async def faturamento_do_mes(
    session: Sess,
    _u: Annotated[User, Depends(_view)],
    competencia: date = Query(...),
) -> dict:
    """Faturamento do mês de cada empresa (base das notas de percentual, 30/09):
    todas as lojas com o CNPJ dela, mesma régua da aba Faturamento. Empresa sem
    loja cadastrada não aparece."""
    fat = await svc_faturamento.faturamento_das_empresas(session, competencia.replace(day=1))
    return {
        "competencia": competencia.replace(day=1).isoformat(),
        "empresas": [{"company_id": str(cid), **f.resumo()} for cid, f in fat.items()],
    }


@router.get("/prestadores", response_model=list[PrestadorOut])
async def listar_prestadores(
    session: Sess, _u: Annotated[User, Depends(_view)]
) -> list[PrestadorOut]:
    empresas = (await session.execute(select(Company).order_by(Company.apelido))).scalars().all()
    fiscais = {f.company_id: f for f in (await session.execute(select(CompanyFiscal))).scalars()}
    com_loja, certs = await svc_empresas.contexto_lista(session)
    return [
        _prestador_out(
            c, fiscais.get(c.id), em_operacao=_em_operacao(c, com_loja), cert=certs.get(c.id)
        )
        for c in empresas
    ]


@router.put("/prestadores/{company_id}/fiscal", response_model=PrestadorOut)
async def salvar_fiscal(
    company_id: UUID, body: FiscalIn, session: Sess, user: Annotated[User, Depends(_edit)]
) -> PrestadorOut:
    c = await session.get(Company, company_id)
    if c is None:
        raise HTTPException(
            404, detail={"code": "empresa_nao_encontrada", "mensagem": "Empresa não encontrada."}
        )
    f = await session.get(CompanyFiscal, company_id)
    if f is None:
        f = svc_empresas.novo_fiscal(company_id)
        session.add(f)
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    f.updated_by = user.id
    await session.commit()
    return await _prestador(session, company_id)


@router.post("/prestadores/{company_id}/nfeio/ligar", response_model=PrestadorOut)
async def ligar_nfeio(
    company_id: UUID, body: LigarIn, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> PrestadorOut:
    """Liga a empresa à da NFE.io (id, link colado ou CNPJ). Só GET na NFE.io."""
    async with _nfeio() as cli:
        try:
            await svc_empresas.ligar(session, company_id, body.ref, cli)
        except svc.NfseError as e:
            raise _http(e) from e
    return await _prestador(session, company_id)


@router.post("/prestadores/{company_id}/nfeio/atualizar", response_model=PrestadorOut)
async def atualizar_nfeio(
    company_id: UUID, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> PrestadorOut:
    """Relê da NFE.io só esta empresa (ambiente, situação, certificado). Só GET."""
    async with _nfeio() as cli:
        try:
            await svc_empresas.atualizar(session, company_id, cli)
        except svc.NfseError as e:
            raise _http(e) from e
    return await _prestador(session, company_id)


@router.post("/nfeio/atualizar-ligadas")
async def atualizar_ligadas_nfeio(session: Sess, _u: Annotated[User, Depends(_edit)]) -> dict:
    """Relê da NFE.io TODAS as empresas ligadas (ambiente, situação, certificado,
    inscrição) — o mesmo do worker diário. Só GET. 01/10/2026 (Eduardo: "quando
    clique no botão de atualizar, ele atualize tudo também"): o "atualizar" do
    topo só relia o DaVinci; certificado trocado na NFE.io só aparecia no
    "Atualizar" de dentro da empresa."""
    async with _nfeio() as cli:
        try:
            return await svc_empresas.atualizar_ligadas(session, cli)
        except svc.NfseError as e:
            raise _http(e) from e


@router.get("/prestadores/{company_id}/nfeio/integrar", response_model=IntegrarPreviaOut)
async def previa_integrar(
    company_id: UUID, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> IntegrarPreviaOut:
    """Mostra o que vai para a NFE.io. Só LÊ (Receita, NFE.io e o certificado guardado):
    nada é enviado."""
    async with _nfeio() as cli:
        try:
            return IntegrarPreviaOut(**await svc_integracao.previa(session, company_id, cli))
        except svc.NfseError as e:
            raise _http(e) from e


@router.post("/prestadores/{company_id}/nfeio/integrar", response_model=IntegrarOut)
async def integrar_nfeio(
    company_id: UUID, body: IntegrarIn, session: Sess, user: Annotated[User, Depends(_edit)]
) -> IntegrarOut:
    """Cria a empresa na NFE.io (ou acha pelo CNPJ), manda o certificado guardado, cadastra a
    inscrição municipal (em TESTE) e liga. Nunca repete POST; nunca cria em dobro."""
    async with _nfeio() as cli:
        try:
            r = await svc_integracao.integrar(session, company_id, body, user.id, cli)
        except svc.NfseError as e:
            raise _http(e) from e
    return IntegrarOut(**r, prestador=await _prestador(session, company_id))


@router.post("/nfeio/sincronizar")
async def sincronizar_nfeio(session: Sess, _a: Annotated[User, Depends(require_admin)]) -> dict:
    """Casa as empresas da conta NFE.io com as do DaVinci pelo CNPJ. Só GET."""
    async with _nfeio() as cli:
        try:
            return await svc_empresas.sincronizar(session, cli)
        except svc.NfseError as e:
            raise _http(e) from e


@router.get("/prestadores/{company_id}/receita", response_model=ReceitaOut)
async def consultar_receita(
    company_id: UUID, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> ReceitaOut:
    """Ajuda a preencher a empresa com o que a Receita diz (29/09). Só consulta:
    não grava nada."""
    c = await session.get(Company, company_id)
    if c is None:
        raise HTTPException(
            404, detail={"code": "empresa_nao_encontrada", "mensagem": "Empresa não encontrada."}
        )
    try:
        return ReceitaOut(**await receita.consultar(c.cnpj or ""))
    except svc.NfseError as e:
        raise _http(e) from e


@router.get("/municipios", response_model=list[MunicipioOut])
async def buscar_municipios(
    _u: Annotated[User, Depends(_view)],
    q: str = Query(default="", max_length=80),
    uf: str | None = Query(default=None, max_length=2),
) -> list[MunicipioOut]:
    """Município pelo nome (sem acento/maiúscula) ou pelo código IBGE — lista do
    IBGE guardada no repositório (ver `services/nfse/municipios.py`)."""
    return [MunicipioOut(**m) for m in municipios.buscar(q, uf)]


# --- tomadores --------------------------------------------------------------------


async def _tomador_out(session: AsyncSession, t: NfseTomador) -> TomadorOut:
    out = TomadorOut.model_validate(t)
    out.nome_nota, out.documento_nota = t.nome, t.documento
    if t.tipo == "grupo" and t.company_id:
        c = await session.get(Company, t.company_id)
        if c is not None:
            out.nome_nota, out.documento_nota = c.razao_social, c.cnpj
    return out


@router.get("/tomadores", response_model=list[TomadorOut])
async def listar_tomadores(session: Sess, _u: Annotated[User, Depends(_view)]) -> list[TomadorOut]:
    # As contas Bling de NF entram (ou se atualizam) sozinhas a cada abertura —
    # Eduardo, 30/09: "precisa ser atualizado os bling toda vez que mudar".
    contas = await svc_contas_bling.sincronizar_tomadores(session)
    rows = (await session.execute(select(NfseTomador).order_by(NfseTomador.created_at))).scalars()
    out = []
    for t in rows:
        o = await _tomador_out(session, t)
        conta = contas.get(t.documento or "") if t.tipo == "externo" else None
        o.conta_bling = conta.resumo() if conta else None
        out.append(o)
    return out


def _checar_tomador(body: TomadorIn) -> None:
    from stdnum.br import cnpj as std_cnpj
    from stdnum.br import cpf as std_cpf

    if body.tipo == "grupo":
        if body.company_id is None:
            raise HTTPException(422, detail={"code": "escolha_a_empresa"})
        return
    doc = body.documento or ""
    ok = (len(doc) == 14 and std_cnpj.is_valid(doc)) or (len(doc) == 11 and std_cpf.is_valid(doc))
    if not ok:
        raise HTTPException(
            422, detail={"code": "documento_invalido", "mensagem": "CNPJ/CPF do tomador inválido."}
        )
    if not (body.nome or "").strip():
        raise HTTPException(422, detail={"code": "nome_obrigatorio"})


@router.post("/tomadores", response_model=TomadorOut, status_code=201)
async def criar_tomador(
    body: TomadorIn, session: Sess, user: Annotated[User, Depends(_edit)]
) -> TomadorOut:
    _checar_tomador(body)
    dados = body.model_dump()
    if body.tipo == "grupo":
        dados.update(documento=None, nome=None)
    else:
        dados["company_id"] = None
    t = NfseTomador(**dados, created_by=user.id)
    session.add(t)
    await session.commit()
    return await _tomador_out(session, t)


@router.patch("/tomadores/{tomador_id}", response_model=TomadorOut)
async def editar_tomador(
    tomador_id: UUID, body: TomadorIn, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> TomadorOut:
    t = await session.get(NfseTomador, tomador_id)
    if t is None:
        raise HTTPException(404, detail={"code": "tomador_nao_encontrado"})
    _checar_tomador(body)
    dados = body.model_dump()
    if body.tipo == "grupo":
        dados.update(documento=None, nome=None)
    else:
        dados["company_id"] = None
    for k, v in dados.items():
        setattr(t, k, v)
    await session.commit()
    return await _tomador_out(session, t)


@router.delete("/tomadores/{tomador_id}", status_code=204)
async def desativar_tomador(
    tomador_id: UUID, session: Sess, _u: Annotated[User, Depends(_delete)]
) -> Response:
    """Não apaga (as notas antigas apontam pra ele): só desativa."""
    t = await session.get(NfseTomador, tomador_id)
    if t is None:
        raise HTTPException(404, detail={"code": "tomador_nao_encontrado"})
    t.ativo = False
    await session.commit()
    return Response(status_code=204)


# --- modelos (as notas de todo mês) -------------------------------------------


async def _nomes(session: AsyncSession) -> tuple[dict[UUID, str], dict[UUID, str]]:
    todas = list((await session.execute(select(Company))).scalars())
    empresas = {c.id: (c.apelido or c.razao_social) for c in todas}
    razao = {c.id: c.razao_social for c in todas}
    tomadores = {}
    for t in (await session.execute(select(NfseTomador))).scalars():
        tomadores[t.id] = razao.get(t.company_id) if t.tipo == "grupo" else t.nome
    return empresas, tomadores


@router.get("/modelos", response_model=list[ModeloOut])
async def listar_modelos(session: Sess, _u: Annotated[User, Depends(_view)]) -> list[ModeloOut]:
    empresas, tomadores = await _nomes(session)
    rows = (
        await session.execute(select(NfseModelo).order_by(NfseModelo.ordem, NfseModelo.created_at))
    ).scalars()
    out = []
    for m in rows:
        o = ModeloOut.model_validate(m)
        o.prestador_nome = empresas.get(m.company_id)
        o.tomador_nome = tomadores.get(m.tomador_id)
        out.append(o)
    return out


async def _checar_modelo(session: AsyncSession, body: ModeloIn) -> None:
    if await session.get(Company, body.company_id) is None:
        raise HTTPException(422, detail={"code": "empresa_nao_encontrada"})
    t = await session.get(NfseTomador, body.tomador_id)
    if t is None:
        raise HTTPException(422, detail={"code": "tomador_nao_encontrado"})
    if t.tipo == "grupo" and t.company_id == body.company_id:
        raise HTTPException(
            422,
            detail={
                "code": "tomador_igual_prestador",
                "mensagem": "O tomador não pode ser a própria empresa.",
            },
        )


@router.post("/modelos", response_model=ModeloOut, status_code=201)
async def criar_modelo(
    body: ModeloIn, session: Sess, user: Annotated[User, Depends(_edit)]
) -> ModeloOut:
    await _checar_modelo(session, body)
    m = NfseModelo(**body.model_dump(), created_by=user.id)
    session.add(m)
    await session.commit()
    return ModeloOut.model_validate(m)


@router.patch("/modelos/{modelo_id}", response_model=ModeloOut)
async def editar_modelo(
    modelo_id: UUID, body: ModeloIn, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> ModeloOut:
    m = await session.get(NfseModelo, modelo_id)
    if m is None:
        raise HTTPException(404, detail={"code": "modelo_nao_encontrado"})
    await _checar_modelo(session, body)
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    await session.commit()
    return ModeloOut.model_validate(m)


@router.delete("/modelos/{modelo_id}", status_code=204)
async def excluir_modelo(
    modelo_id: UUID, session: Sess, _u: Annotated[User, Depends(_delete)]
) -> Response:
    m = await session.get(NfseModelo, modelo_id)
    if m is None:
        raise HTTPException(404, detail={"code": "modelo_nao_encontrado"})
    usado = (
        await session.execute(
            select(func.count()).select_from(NfseEmissao).where(NfseEmissao.modelo_id == m.id)
        )
    ).scalar_one()
    if usado:
        m.ativo = False  # já tem nota: só desativa, o histórico continua apontando
    else:
        await session.delete(m)
    await session.commit()
    return Response(status_code=204)


# --- prévia e emissão -------------------------------------------------------------


async def _pct_empresa(session: AsyncSession, company_id: UUID) -> Decimal | None:
    """A porcentagem da empresa (Cadastros › Empresas): o % padrão das notas de
    percentual que ela emite."""
    c = await session.get(Company, company_id)
    return c.percentual_servico if c is not None else None


async def _item(session: AsyncSession, i: ItemIn, competencia: date) -> svc.Item:
    """O item com o mês da base (01/10/2026): mês depois do da nota, ou mais de
    12 meses antes, recusa o pedido inteiro (422) — a prévia não mostra uma
    base que a emissão recusaria."""
    try:
        svc.mes_da_base(competencia, i.base_competencia)
    except svc.NfseError as e:
        raise _http(e) from e
    it = await _item_da_nota(session, i)
    it.base_competencia = i.base_competencia.replace(day=1) if i.base_competencia else None
    return it


async def _item_da_nota(session: AsyncSession, i: ItemIn) -> svc.Item:
    """O % da nota de percentual: o do item → o da nota fixa → o da empresa
    (`svc.resolver_percentual`); sem nenhum, a prévia diz o que falta."""
    if i.modelo_id:
        m = await session.get(NfseModelo, i.modelo_id)
        if m is None:
            raise HTTPException(404, detail={"code": "modelo_nao_encontrado"})
        pct_empresa = (
            await _pct_empresa(session, m.company_id) if m.tipo_valor == "percentual" else None
        )
        it = svc.item_do_modelo(
            m, i.valor, base=i.base_calculo, percentual=i.percentual, pct_empresa=pct_empresa
        )
        if i.descricao:
            it.descricao = i.descricao
        return it
    # Avulsa: OU o valor, OU a base + o percentual (o servidor faz a conta). Sem
    # o percentual (e sem valor), vale o da empresa.
    por_percentual = i.base_calculo is not None and (i.percentual is not None or not i.valor)
    if not (i.company_id and i.tomador_id and i.descricao and (i.valor or por_percentual)):
        raise HTTPException(
            422,
            detail={
                "code": "avulsa_incompleta",
                "mensagem": "Nota avulsa precisa de empresa, tomador, descrição e valor "
                "(ou a base de cálculo e o percentual).",
            },
        )
    it = svc.Item(
        company_id=i.company_id,
        tomador_id=i.tomador_id,
        descricao=i.descricao,
        valor=i.valor,
        city_service_code=i.city_service_code,
        federal_service_code=i.federal_service_code,
        c_nbs=i.c_nbs,
        inf_comp=i.inf_comp,
    )
    if not por_percentual:
        return it
    pct, origem = svc.resolver_percentual(
        i.percentual, None, await _pct_empresa(session, i.company_id)
    )
    return it.usar_percentual(i.base_calculo, pct, origem)


@router.post("/previa")
async def previa(body: PreviaIn, session: Sess, _u: Annotated[User, Depends(_view)]) -> dict:
    """Monta cada nota sem gravar nada e sem POST. Com a chave configurada, lista
    (GET, cache curto) as notas do mês de cada empresa na NFE.io pra avisar
    nota já emitida pelo painel."""
    itens = []
    cli = nfeio.ClienteNfeio() if nfeio.chave_configurada() else None
    try:
        for i in body.itens:
            try:
                itens.append(
                    await svc.previa(
                        session,
                        await _item(session, i, body.competencia),
                        body.competencia,
                        cli=cli,
                    )
                )
            except svc.NfseError as e:
                itens.append(
                    {
                        "modelo_id": str(i.modelo_id) if i.modelo_id else None,
                        "problemas": [e.detail["mensagem"]],
                        "avisos": [],
                    }
                )
    finally:
        if cli is not None:
            await cli.__aexit__(None, None, None)
    return {"competencia": body.competencia.isoformat(), "itens": itens}


async def _emissao_out(session: AsyncSession, e: NfseEmissao) -> EmissaoOut:
    out = EmissaoOut.model_validate(e)
    out.teste = eh_teste(e.nfeio_ambiente)
    c = await session.get(Company, e.company_id)
    out.prestador_nome = (c.apelido or c.razao_social) if c else None
    out.tomador_nome = (e.snapshot or {}).get("tomador", {}).get("nome")
    return out


@router.post("/emitir", response_model=EmissaoOut)
async def emitir(
    body: EmitirIn, session: Sess, user: Annotated[User, Depends(_edit)]
) -> EmissaoOut:
    item = await _item(session, body.item, body.competencia)
    try:
        # Percentual sem base (nem faturamento) ou sem %: o emitir recusa antes
        # de ir à NFE.io (checar_conta depois de completar a base).
        e = await svc.emitir(session, item, body.competencia, user.id)
    except svc.NfseError as err:
        raise _http(err) from err
    except IntegrityError as err:
        # Duas emissões do mesmo modelo no mesmo mês ao mesmo tempo.
        await session.rollback()
        raise HTTPException(
            409, detail={"code": "ja_emitida", "mensagem": "Esse modelo já tem nota neste mês."}
        ) from err
    return await _emissao_out(session, e)


async def _emissao(session: AsyncSession, emissao_id: UUID) -> NfseEmissao:
    e = await session.get(NfseEmissao, emissao_id)
    if e is None:
        raise HTTPException(404, detail={"code": "emissao_nao_encontrada"})
    return e


@router.post("/emissoes/atualizar")
async def atualizar_emissoes(
    body: AtualizarIn, session: Sess, _u: Annotated[User, Depends(_view)]
) -> dict:
    """A tela chama a cada ~4 s depois de enviar: pergunta à NFE.io (GET) só as
    que ainda estão em andamento e devolve todas."""
    rows = list(
        (await session.execute(select(NfseEmissao).where(NfseEmissao.id.in_(body.ids)))).scalars()
    )
    pendentes = [e for e in rows if e.status in STATUS_EM_ANDAMENTO]
    if pendentes:
        try:
            await svc.atualizar_varias(session, pendentes)
        except svc.NfseError as err:
            raise _http(err) from err
    ordem = {i: n for n, i in enumerate(body.ids)}
    rows.sort(key=lambda e: ordem.get(e.id, 0))
    return {"emissoes": [(await _emissao_out(session, e)).model_dump(mode="json") for e in rows]}


# --- lote: baixar e imprimir várias notas (30/09/2026) --------------------------------
# Declaradas ANTES das /emissoes/{emissao_id}/…: "lote" não é id de nota.


def _carimbo() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%d-%H%M")


def _cabecalhos_do_lote(lote: svc_lote.Lote, disposicao: str) -> dict[str, str]:
    """Quantas foram, quantas saíram e quais faltaram (ids separados por
    vírgula; vazio = nenhuma): a tela avisa o nº e a empresa das que faltaram."""
    return {
        "Content-Disposition": disposicao,
        "Cache-Control": "no-store",
        "X-Nfse-Total": str(len(lote.itens)),
        "X-Nfse-Ok": str(len(lote.prontos)),
        "X-Nfse-Faltaram": ",".join(str(i.id) for i in lote.faltaram),
        "Access-Control-Expose-Headers": (
            "X-Nfse-Total, X-Nfse-Ok, X-Nfse-Faltaram, Content-Disposition"
        ),
    }


@router.post("/emissoes/lote/arquivos")
async def lote_arquivos(
    body: LoteArquivosIn, session: Sess, _u: Annotated[User, Depends(_view)]
) -> Response:
    """Os PDFs (ou os XMLs) das notas marcadas num .zip: uma pasta por empresa
    e, na raiz, o `_FALTARAM.txt` com as que não vieram e o motivo. Só entram
    notas emitidas ou canceladas; o XML já guardado nem vai à NFE.io."""
    try:
        lote = await svc_lote.baixar_lote(session, body.ids, body.tipo)
    except svc.NfseError as err:
        raise _http(err) from err
    nome = f"notas-de-servico_{body.tipo}_{_carimbo()}.zip"
    return Response(
        svc_lote.montar_zip(lote),
        media_type="application/zip",
        headers=_cabecalhos_do_lote(lote, f'attachment; filename="{nome}"'),
    )


@router.post("/emissoes/lote/imprimir")
async def lote_imprimir(
    body: LoteImprimirIn, session: Sess, _u: Annotated[User, Depends(_view)]
) -> Response:
    """Os PDFs das notas marcadas juntados num PDF só, na ordem em que vieram,
    para imprimir de uma vez. PDF corrompido fica de fora (e conta em
    X-Nfse-Faltaram) em vez de derrubar a impressão."""
    try:
        lote = await svc_lote.baixar_lote(session, body.ids, "pdf")
        pdf = svc_lote.juntar_pdfs(lote)
    except svc.NfseError as err:
        raise _http(err) from err
    return Response(
        pdf,
        media_type="application/pdf",
        headers=_cabecalhos_do_lote(lote, f'inline; filename="notas-de-servico_{_carimbo()}.pdf"'),
    )


@router.post("/emissoes/{emissao_id}/reenviar", response_model=EmissaoOut)
async def reenviar(
    emissao_id: UUID, session: Sess, user: Annotated[User, Depends(_edit)]
) -> EmissaoOut:
    """Rejeitada → corrige o cadastro e manda de novo (antes, confere na NFE.io
    se o envio anterior não virou nota)."""
    e = await _emissao(session, emissao_id)
    if e.status != "rejeitada":
        raise HTTPException(
            409,
            detail={"code": "nao_reenviavel", "mensagem": "Só nota rejeitada pode ser reenviada."},
        )
    serv = (e.snapshot or {}).get("servico", {})
    if e.modelo_id and (m := await session.get(NfseModelo, e.modelo_id)) is not None:
        item = svc.item_do_modelo(m)
    else:
        item = svc.Item(
            company_id=e.company_id,
            tomador_id=e.tomador_id,
            descricao=e.descricao,
            valor=e.valor_servico,
            city_service_code=serv.get("city_service_code"),
            federal_service_code=serv.get("federal_service_code"),
            c_nbs=serv.get("c_nbs"),
            inf_comp=serv.get("inf_comp"),
        )
    # A descrição que foi (sem o bloco de retenções: o IR é recalculado).
    item.descricao = serv.get("descricao_base") or e.descricao
    # Mesmo valor — ou a mesma base, o mesmo % e o mesmo mês da base — da nota recusada.
    svc.repetir_valor(item, e)
    try:
        e = await svc.emitir(session, item, e.competencia, user.id, reenviar=e)
    except svc.NfseError as err:
        raise _http(err) from err
    except IntegrityError as err:
        # Já existe nota viva do modelo no mês (índice único): nada foi enviado.
        await session.rollback()
        raise HTTPException(
            409, detail={"code": "ja_emitida", "mensagem": "Esse modelo já tem nota neste mês."}
        ) from err
    return await _emissao_out(session, e)


@router.post("/emissoes/{emissao_id}/conferir", response_model=EmissaoOut)
async def conferir(
    emissao_id: UUID, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> EmissaoOut:
    """Atualizar da NFE.io (GET): nunca reenvia."""
    e = await _emissao(session, emissao_id)
    try:
        await svc.atualizar(session, e)
    except svc.NfseError as err:
        raise _http(err) from err
    return await _emissao_out(session, e)


@router.post("/emissoes/{emissao_id}/cancelar", response_model=EmissaoOut)
async def cancelar(
    emissao_id: UUID, body: CancelarIn, session: Sess, user: Annotated[User, Depends(_delete)]
) -> EmissaoOut:
    e = await _emissao(session, emissao_id)
    try:
        e = await svc.cancelar(session, e, body.c_motivo, body.x_motivo, user.id)
    except svc.NfseError as err:
        raise _http(err) from err
    return await _emissao_out(session, e)


@router.post("/emissoes/{emissao_id}/enviar-email")
async def enviar_email(
    emissao_id: UUID, body: EmailNotaIn, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> dict:
    """Envio MANUAL da nota por e-mail (30/09/2026): o DaVinci manda o PDF e o
    XML para os endereços digitados — nada sai automático na emissão, e a
    NFE.io não manda mais nada ao tomador. Só nota emitida."""
    e = await _emissao(session, emissao_id)
    try:
        para = await svc.enviar_por_email(
            session, e, body.para, salvar_no_tomador=body.salvar_no_tomador
        )
    except svc.NfseError as err:
        raise _http(err) from err
    return {"ok": True, "para": para}


@router.get("/emissoes", response_model=list[EmissaoOut])
async def listar_emissoes(
    session: Sess,
    _u: Annotated[User, Depends(_view)],
    competencia: date | None = None,
    company_id: UUID | None = None,
    status: str | None = None,
    # Compatível com a tela antiga: 1 = produção (nota real), 2 = teste.
    tp_amb: int | None = Query(default=None, ge=1, le=2),
) -> list[EmissaoOut]:
    q = select(NfseEmissao).order_by(NfseEmissao.created_at.desc()).limit(500)
    if competencia:
        q = q.where(NfseEmissao.competencia == competencia.replace(day=1))
    if company_id:
        q = q.where(NfseEmissao.company_id == company_id)
    if status:
        q = q.where(NfseEmissao.status == status)
    if tp_amb == 1:
        q = q.where(NfseEmissao.nfeio_ambiente == "Production")
    elif tp_amb == 2:
        q = q.where(NfseEmissao.nfeio_ambiente != "Production")
    return [await _emissao_out(session, e) for e in (await session.execute(q)).scalars()]


@router.get("/emissoes/{emissao_id}/eventos")
async def eventos(
    emissao_id: UUID, session: Sess, _u: Annotated[User, Depends(_view)]
) -> list[dict]:
    rows = (
        await session.execute(
            select(NfseEvento)
            .where(NfseEvento.emissao_id == emissao_id)
            .order_by(NfseEvento.created_at)
        )
    ).scalars()
    return [
        {
            "id": str(v.id),
            "tipo_evento": v.tipo_evento,
            "c_motivo": v.c_motivo,
            "x_motivo": v.x_motivo,
            "status": v.status,
            "erros": v.erros,
            "created_at": v.created_at.isoformat(),
        }
        for v in rows
    ]


def _nome_arquivo(e: NfseEmissao, ext: str) -> str:
    return f"NFSe_{e.n_nfse or e.nfeio_id or e.id}.{ext}"


def _trava_arquivo(tipo: str):
    """A chave da página OU o link de 60 s deste arquivo (desta nota, deste
    tipo). Os dois só valem com a sessão e a permissão de ver."""

    async def _dep(
        emissao_id: UUID,
        chave: Annotated[str | None, Query(max_length=200)] = None,
        x_nfse_token: Annotated[str | None, Header(alias="X-Nfse-Token")] = None,
    ) -> None:
        if senha_extra.token_valido(x_nfse_token, "nfse"):
            return
        if senha_extra.link_arquivo_valido(chave, "nfse", f"{emissao_id}:{tipo}"):
            return
        raise HTTPException(401, detail={"code": "nfse_locked"})

    _dep.__qualname__ = f"_trava_arquivo_{tipo}"
    return _dep


# Nomes fixos: os testes que não cuidam da senha desviam destas duas também.
trava_pdf = _trava_arquivo("pdf")
trava_xml = _trava_arquivo("xml")


@router.post("/emissoes/{emissao_id}/link")
async def link_arquivo(
    emissao_id: UUID,
    session: Sess,
    _u: Annotated[User, Depends(_view)],
    tipo: str = Query(pattern="^(pdf|xml)$"),
) -> dict:
    """Link de 60 s para abrir o PDF (ou baixar o XML) desta nota numa aba."""
    await _emissao(session, emissao_id)
    chave = senha_extra.fazer_link_arquivo("nfse", f"{emissao_id}:{tipo}")
    return {"url": f"/api/nfse/emissoes/{emissao_id}/{tipo}?chave={chave}"}


@router_arquivos.get("/emissoes/{emissao_id}/xml", dependencies=[Depends(trava_xml)])
async def baixar_xml(
    emissao_id: UUID,
    session: Sess,
    _u: Annotated[User, Depends(_view)],
    tipo: str = Query(default="nfse", pattern="^(nfse|dps)$"),
) -> Response:
    e = await _emissao(session, emissao_id)
    if tipo == "dps":
        # Não existe mais DPS nossa: quem monta é a NFE.io.
        raise HTTPException(404, detail={"code": "sem_xml", "mensagem": svc.MSG_SEM_XML})
    try:
        conteudo, _mime = await svc.baixar(session, e, "xml")
    except svc.NfseError as err:
        raise _http(err) from err
    return Response(
        conteudo,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{_nome_arquivo(e, "xml")}"'},
    )


@router_arquivos.get("/emissoes/{emissao_id}/pdf", dependencies=[Depends(trava_pdf)])
async def baixar_pdf(
    emissao_id: UUID, session: Sess, _u: Annotated[User, Depends(_view)]
) -> Response:
    e = await _emissao(session, emissao_id)
    try:
        conteudo, _mime = await svc.baixar(session, e, "pdf")
    except svc.NfseError as err:
        raise _http(err) from err
    return Response(
        conteudo,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{_nome_arquivo(e, "pdf")}"'},
    )


# Admin: as últimas idas à NFE.io (diagnóstico). Sem corpo, sem chave.
@router.get("/chamadas")
async def chamadas(session: Sess, _a: Annotated[User, Depends(require_admin)]) -> list[dict]:
    from app.models.nfse import NfseChamada

    rows = (
        await session.execute(select(NfseChamada).order_by(NfseChamada.id.desc()).limit(100))
    ).scalars()
    return [
        {
            "id": r.id,
            "operacao": r.operacao,
            "tp_amb": r.tp_amb,
            "ambiente": r.ambiente,
            "http": r.http_status,
            "codigos": r.codigos,
            "erro": r.erro,
            "ms": r.duracao_ms,
            "em": r.created_at.isoformat(),
        }
        for r in rows
    ]
