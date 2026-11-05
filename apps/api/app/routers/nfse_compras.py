"""Cadastros › Contabilidade › Compra (23/10/2026).

Eduardo: "o nome emissao de serviço vai ser trocado por contabilidade, dentro vai
ter o nome emissao de servico e do lado [...] a nova aba compra". Mora na mesma
página (`/emissao-servico`) e debaixo da mesma senha extra e da mesma permissão
(`emissao_servico`): ver = view; cadastrar produto, registrar compra, marcar a
NF e baixar a planilha = edit; excluir = delete. Regras em `services/compra.py`.

Emissão automática (04/11/2026, `services/compra_emissao.py`): "Emitir" põe na
fila, "tirar da fila" e "tirar do robô" desfazem. O robô do Mac usa o
`agent_router` lá embaixo (`/api/nf-cadastro/agent/compras`), FORA da senha extra
(ele não tem a chave de 30 min) e guardado pelo X-Agent-Token das NFs.

Imprimir a NF (04/11/2026, Eduardo: "igual emissão de serviço, tem que dar para
imprimir a nota etc.", `services/compra_arquivos.py`): `GET /{id}/pdf` (a DANFE
desenhada do XML autorizado, inline), `POST /lote/imprimir` (um PDF só) e
`POST /lote/arquivos` (.zip de PDFs ou XMLs, uma pasta por compradora). Tudo atrás
da chave INTEIRA da página, como o XML e a planilha: a tela baixa com o
cabeçalho e abre o PDF na aba (não há o link de 60 s da NFS-e). Só sai o XML
AUTORIZADO da NF dessa compra (`compra_arquivos.problema_do_xml`: emitente,
destinatário, cStat 100, DigestValue = digVal), senão 409; compra cancelada não
tem DANFE (409); `marcar_nf` recusa chave de NF de outra emitente.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import User
from app.models.compra import (
    ETAPAS,
    CompraPedido,
    CompraPedidoItem,
    CompraProduto,
    CompraVendedora,
    etapa_indice,
    queimado,
)
from app.routers.nf import _require_nf_agent_token
from app.schemas.compra import (
    AgenteEtapaIn,
    AgenteLeaseIn,
    AgenteResultadoIn,
    CompradorOut,
    CompraIn,
    CompraOut,
    EmitirIn,
    FaturamentoOut,
    ItemOut,
    NfIn,
    OpcoesOut,
    PlanilhaIn,
    ProdutoIn,
    ProdutoOut,
)
from app.schemas.nfse import LoteArquivosIn, LoteImprimirIn
from app.security.senha_extra import require_nfse_unlock
from app.services import compra as svc
from app.services import compra_arquivos as arquivos
from app.services import compra_emissao as emissao
from app.services.atendimento.danfe import DanfeInvalida
from app.services.nf_xml import NfXmlError, parse_nfe
from app.services.nfse.erros import NfseError

logger = structlog.get_logger()
router = APIRouter(
    prefix="/api/nfse/compras", tags=["nfse-compras"], dependencies=[Depends(require_nfse_unlock)]
)


_view = require_permission("emissao_servico", "view")
_edit = require_permission("emissao_servico", "edit")
_delete = require_permission("emissao_servico", "delete")
Sess = Annotated[AsyncSession, Depends(get_session)]


def _http(e: NfseError) -> HTTPException:
    return HTTPException(e.status, detail=e.detail)


def _erro(status: int, code: str, mensagem: str, **extra: object) -> HTTPException:
    return HTTPException(status, detail={"code": code, "mensagem": mensagem, **extra})


def _agora() -> datetime:
    return datetime.now(UTC)


def _queimado(p: CompraPedido) -> HTTPException:
    return _erro(
        409,
        "numero_queimado",
        f"O robô chegou a importar o pedido {p.numero} no Bling de {p.vendedor_nome}: "
        "esse número não volta (pode já estar lá). Registre uma compra nova.",
    )


async def _nomes(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    linhas = (
        await session.execute(select(User.id, User.name, User.email).where(User.id.in_(ids)))
    ).all()
    return {r.id: (r.name or r.email) for r in linhas}


def _saida(p: CompraPedido, itens: list[CompraPedidoItem], nomes: dict[UUID, str]) -> CompraOut:
    return CompraOut(
        id=p.id,
        numero=p.numero,
        company_id=p.company_id,
        comprador=p.comprador,
        vendedor_cnpj=p.vendedor_cnpj,
        vendedor_nome=p.vendedor_nome,
        vendedor_uf=p.vendedor_uf,
        competencia=p.competencia,
        data_pedido=p.data_pedido,
        faturamento=p.faturamento,
        modo=p.modo,
        percentual=p.percentual,
        cfop=p.cfop,
        total=p.total,
        status=p.status,
        nf_numero=p.nf_numero,
        nf_chave=p.nf_chave,
        emissao_pedida_em=p.emissao_pedida_em,
        emissao_iniciada_em=p.emissao_iniciada_em,
        emissao_tentativas=p.emissao_tentativas or 0,
        emissao_erro=p.emissao_erro,
        emissao_etapa=p.emissao_etapa,
        nf_emitida_em=p.nf_emitida_em,
        planilha_baixada_em=p.planilha_baixada_em,
        numero_queimado=queimado(p.emissao_etapa),
        observacoes=p.observacoes,
        itens=[ItemOut.model_validate(i) for i in itens],
        created_at=p.created_at,
        updated_at=p.updated_at,
        criado_por=nomes.get(p.created_by) if p.created_by else None,
    )


async def _uma(session: AsyncSession, pedido_id: UUID, *, travar: bool = False) -> CompraPedido:
    q = select(CompraPedido).where(CompraPedido.id == pedido_id)
    if travar:
        q = q.with_for_update()
    p = (await session.execute(q)).scalar_one_or_none()
    if p is None:
        raise _erro(404, "compra_nao_encontrada", "Compra não encontrada.")
    return p


async def _devolver(session: AsyncSession, p: CompraPedido) -> CompraOut:
    itens = (await svc.itens_de(session, [p.id]))[p.id]
    return _saida(p, itens, await _nomes(session, {p.created_by}))


# --- o que a tela precisa para montar a compra ----------------------------------------


@router.get("/opcoes", response_model=OpcoesOut)
async def opcoes(session: Sess, _u: Annotated[User, Depends(_view)]) -> OpcoesOut:
    """As contas Bling de NF (quem vende) e os produtos ativos."""
    return OpcoesOut(
        vendedoras=await svc.vendedoras(session),
        produtos=await svc.produtos(session, so_ativos=True),
    )


@router.get("/comprador/{company_id}", response_model=CompradorOut)
async def comprador(
    company_id: UUID, session: Sess, _u: Annotated[User, Depends(_view)]
) -> CompradorOut:
    try:
        return CompradorOut(**await svc.comprador(session, company_id))
    except NfseError as e:
        raise _http(e) from e


@router.get("/faturamento", response_model=FaturamentoOut)
async def faturamento(
    session: Sess,
    _u: Annotated[User, Depends(_view)],
    company_id: UUID = Query(...),
    competencia: date = Query(...),
) -> FaturamentoOut:
    return FaturamentoOut(**await svc.faturamento(session, company_id, competencia))


# --- produtos ---------------------------------------------------------------------------


@router.get("/produtos", response_model=list[ProdutoOut])
async def listar_produtos(session: Sess, _u: Annotated[User, Depends(_view)]) -> list[ProdutoOut]:
    return [ProdutoOut(**p) for p in await svc.produtos(session)]


@router.post("/produtos", response_model=ProdutoOut, status_code=201)
async def criar_produto(
    body: ProdutoIn, session: Sess, u: Annotated[User, Depends(_edit)]
) -> ProdutoOut:
    if await svc.nome_em_uso(session, body.nome):
        raise _erro(409, "produto_repetido", f'Já existe um produto chamado "{body.nome}".')
    p = CompraProduto(**body.model_dump(), created_by=u.id)
    session.add(p)
    await session.commit()
    await session.refresh(p)
    logger.info("compra_produto_criado", produto=str(p.id), nome=p.nome, user=str(u.id))
    return ProdutoOut.model_validate(p)


@router.put("/produtos/{produto_id}", response_model=ProdutoOut)
async def editar_produto(
    produto_id: UUID, body: ProdutoIn, session: Sess, u: Annotated[User, Depends(_edit)]
) -> ProdutoOut:
    p = await session.get(CompraProduto, produto_id)
    if p is None:
        raise _erro(404, "produto_nao_encontrado", "Produto não encontrado.")
    if await svc.nome_em_uso(session, body.nome, exceto=produto_id):
        raise _erro(409, "produto_repetido", f'Já existe um produto chamado "{body.nome}".')
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    await session.commit()
    await session.refresh(p)
    usado = next((x["usado"] for x in await svc.produtos(session) if x["id"] == p.id), 0)
    logger.info("compra_produto_editado", produto=str(p.id), user=str(u.id))
    return ProdutoOut(**{**ProdutoOut.model_validate(p).model_dump(), "usado": usado})


@router.delete("/produtos/{produto_id}", status_code=204)
async def excluir_produto(
    produto_id: UUID, session: Sess, u: Annotated[User, Depends(_delete)]
) -> Response:
    p = await session.get(CompraProduto, produto_id)
    if p is None:
        raise _erro(404, "produto_nao_encontrado", "Produto não encontrado.")
    usado = next((x["usado"] for x in await svc.produtos(session) if x["id"] == p.id), 0)
    if usado:
        raise _erro(
            409,
            "produto_em_uso",
            f"Esse produto está em {usado} compra(s): desative em vez de excluir "
            "(as compras ficam como estão).",
        )
    await session.delete(p)
    await session.commit()
    logger.info("compra_produto_excluido", produto=str(produto_id), user=str(u.id))
    return Response(status_code=204)


# --- compras ------------------------------------------------------------------------------


@router.get("", response_model=list[CompraOut])
async def listar(
    session: Sess,
    _u: Annotated[User, Depends(_view)],
    competencia: date | None = Query(None),
    company_id: UUID | None = Query(None),
    status: str | None = Query(None),
) -> list[CompraOut]:
    q = select(CompraPedido).order_by(CompraPedido.numero.desc())
    if competencia is not None:
        q = q.where(CompraPedido.competencia == competencia.replace(day=1))
    if company_id is not None:
        q = q.where(CompraPedido.company_id == company_id)
    if status:
        q = q.where(CompraPedido.status == status)
    pedidos = list((await session.execute(q.limit(500))).scalars())
    itens = await svc.itens_de(session, [p.id for p in pedidos])
    nomes = await _nomes(session, {p.created_by for p in pedidos})
    return [_saida(p, itens[p.id], nomes) for p in pedidos]


# --- imprimir e baixar várias NFs (04/11/2026, como a NFS-e) ------------------------------
# Declaradas ANTES das /{pedido_id}/…: "lote" não é id de compra. Nunca terminam em
# /pdf ou /xml (regra da senha extra, tests/test_nfse_senha_extra.py).


def _carimbo() -> str:
    return datetime.now(svc.SP).strftime("%Y%m%d-%H%M")


def _cabecalhos_do_lote(lote: arquivos.Lote, disposicao: str) -> dict[str, str]:
    """Os mesmos da NFS-e (routers/nfse.py): quantas foram, quantas saíram e quais
    faltaram (ids das compras separados por vírgula; vazio = nenhuma)."""
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


@router.post("/lote/imprimir")
async def lote_imprimir(
    body: LoteImprimirIn, session: Sess, _u: Annotated[User, Depends(_view)]
) -> Response:
    """As DANFEs das compras marcadas num PDF só, na ordem em que vieram, para
    imprimir de uma vez. A que não tem o XML fica de fora (X-Nfse-Faltaram)."""
    try:
        lote = await arquivos.montar_lote(session, body.ids, "pdf")
    except NfseError as e:
        raise _http(e) from e
    pdf = await arquivos.juntar_pdfs(lote)
    return Response(
        pdf,
        media_type="application/pdf",
        headers=_cabecalhos_do_lote(lote, f'inline; filename="nfs-de-compra_{_carimbo()}.pdf"'),
    )


@router.post("/lote/arquivos")
async def lote_arquivos(
    body: LoteArquivosIn, session: Sess, _u: Annotated[User, Depends(_view)]
) -> Response:
    """As DANFEs (ou os XMLs) das compras marcadas num .zip: uma pasta por
    compradora e, na raiz, o `_FALTARAM.txt` com as que não vieram e o motivo."""
    try:
        lote = await arquivos.montar_lote(session, body.ids, body.tipo)
    except NfseError as e:
        raise _http(e) from e
    nome = f"nfs-de-compra_{body.tipo}_{_carimbo()}.zip"
    return Response(
        arquivos.montar_zip(lote),
        media_type="application/zip",
        headers=_cabecalhos_do_lote(lote, f'attachment; filename="{nome}"'),
    )


@router.get("/{pedido_id}", response_model=CompraOut)
async def uma(pedido_id: UUID, session: Sess, _u: Annotated[User, Depends(_view)]) -> CompraOut:
    return await _devolver(session, await _uma(session, pedido_id))


@router.post("", response_model=CompraOut, status_code=201)
async def registrar(body: CompraIn, session: Sess, u: Annotated[User, Depends(_edit)]) -> CompraOut:
    try:
        m = await svc.montar(session, body)
    except NfseError as e:
        raise _http(e) from e
    p = CompraPedido(**m["pedido"], status="registrada", created_by=u.id, updated_by=u.id)
    session.add(p)
    await session.flush()
    for it in m["itens"]:
        session.add(CompraPedidoItem(pedido_id=p.id, **it))
    await session.commit()
    await session.refresh(p)
    logger.info(
        "compra_registrada", compra=str(p.id), numero=p.numero, total=str(p.total), user=str(u.id)
    )
    return await _devolver(session, p)


@router.put("/{pedido_id}", response_model=CompraOut)
async def editar(
    pedido_id: UUID, body: CompraIn, session: Sess, u: Annotated[User, Depends(_edit)]
) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status not in ("registrada", "erro"):
        raise _erro(
            409,
            "compra_travada",
            "Só dá para editar compra sem NF e fora da fila (registrada ou com erro). "
            "Tire da fila, desfaça a NF ou reabra a compra antes.",
        )
    if queimado(p.emissao_etapa):
        raise _queimado(p)
    atual = {"cnpj": p.vendedor_cnpj, "nome": p.vendedor_nome, "uf": p.vendedor_uf}
    foto = p.comprador if body.company_id == p.company_id else None
    try:
        m = await svc.montar(session, body, vendedora_atual=atual, comprador_atual=foto)
    except NfseError as e:
        raise _http(e) from e
    for k, v in m["pedido"].items():
        setattr(p, k, v)
    p.updated_by = u.id
    for it in (await svc.itens_de(session, [p.id]))[p.id]:
        await session.delete(it)
    await session.flush()
    for it in m["itens"]:
        session.add(CompraPedidoItem(pedido_id=p.id, **it))
    await session.commit()
    await session.refresh(p)
    logger.info("compra_editada", compra=str(p.id), numero=p.numero, user=str(u.id))
    return await _devolver(session, p)


@router.post("/{pedido_id}/nf", response_model=CompraOut)
async def marcar_nf(
    pedido_id: UUID, body: NfIn, session: Sess, u: Annotated[User, Depends(_edit)]
) -> CompraOut:
    """Anota o nº da NF que saiu no Bling da vendedora (ou corrige o número). Vale
    também para a compra que o robô deixou em erro depois de importar (04/11): a
    pessoa conferiu no Bling e anota a NF que saiu (com a chave, se tiver)."""
    p = await _uma(session, pedido_id, travar=True)
    if p.status == "cancelada":
        raise _erro(409, "compra_cancelada", "Compra cancelada: reabra antes de anotar a NF.")
    if p.status in ("na_fila", "emitindo"):
        raise _erro(
            409,
            "na_fila",
            "A compra está na fila do robô: tire da fila antes de anotar a NF à mão.",
        )
    if body.nf_chave and body.nf_chave != p.nf_chave:
        # a chave diz o CNPJ de quem emitiu (posições 7–20): chave de NF de outra
        # empresa abriria a DANFE/XML dela (venda para CPF, inclusive) por esta compra
        if body.nf_chave[6:20] != p.vendedor_cnpj:
            raise _erro(
                409,
                "chave_de_outra_emitente",
                f"Essa chave é de uma NF emitida pelo CNPJ {body.nf_chave[6:20]}, não por "
                f"{p.vendedor_nome}: confira a chave.",
            )
        outra = (
            await session.execute(
                select(CompraPedido.numero).where(
                    CompraPedido.nf_chave == body.nf_chave, CompraPedido.id != p.id
                )
            )
        ).scalar_one_or_none()
        if outra is not None:
            raise _erro(
                409, "chave_em_outra_compra", f"Essa chave já está anotada na compra {outra}."
            )
        p.nf_chave = body.nf_chave
    # a chave diz o nº da NF (posições 26–34): NF com chave (do XML do robô ou digitada)
    # não troca de número à mão — senão o nº e o XML/chave da compra apontam NFs diferentes
    if p.nf_chave and len(p.nf_chave) == 44 and int(p.nf_chave[25:34]) != int(body.nf_numero):
        raise _erro(
            409,
            "numero_diferente_da_chave",
            f"A chave {p.nf_chave} é da NF {int(p.nf_chave[25:34])}, não da {body.nf_numero}: "
            "confira o número.",
        )
    p.status, p.nf_numero, p.updated_by = "nf_emitida", body.nf_numero, u.id
    p.emissao_erro = None
    await session.commit()
    await session.refresh(p)
    logger.info("compra_nf", compra=str(p.id), numero=p.numero, nf=p.nf_numero, user=str(u.id))
    return await _devolver(session, p)


@router.post("/{pedido_id}/desfazer-nf", response_model=CompraOut)
async def desfazer_nf(
    pedido_id: UUID,
    session: Sess,
    u: Annotated[User, Depends(_edit)],
    conferido: bool = Query(False),
) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status != "nf_emitida":
        raise _erro(409, "sem_nf", "Essa compra não tem NF anotada.")
    if queimado(p.emissao_etapa):
        # NF do robô: o pedido está no Bling da vendedora, o número não volta. Vai
        # para cancelada guardando nº e chave (nunca para registrada = emitir de novo).
        if not conferido:
            raise _erro(
                409,
                "conferir_no_bling",
                f"A NF {p.nf_numero} saiu pelo robô: cancele a NF no Bling de "
                f"{p.vendedor_nome} e confirme que conferiu. A compra vai para cancelada "
                "(o número e a chave ficam anotados).",
            )
        p.status, p.updated_by = "cancelada", u.id
        await session.commit()
        await session.refresh(p)
        logger.info("compra_nf_robo_cancelada", compra=str(p.id), nf=p.nf_numero, user=str(u.id))
        return await _devolver(session, p)
    p.status, p.nf_numero, p.nf_chave, p.nf_emitida_em, p.updated_by = (
        "registrada",
        None,
        None,
        None,
        u.id,
    )
    await session.commit()
    await session.refresh(p)
    return await _devolver(session, p)


@router.post("/{pedido_id}/cancelar", response_model=CompraOut)
async def cancelar(
    pedido_id: UUID,
    session: Sess,
    u: Annotated[User, Depends(_edit)],
    conferido: bool = Query(False),
) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status == "nf_emitida":
        raise _erro(
            409,
            "tem_nf",
            f"A NF {p.nf_numero} já saiu: cancele a NF no Bling da vendedora "
            "e depois use 'Desfazer NF'.",
        )
    if p.status == "emitindo":
        raise _erro(409, "emitindo", "O robô está emitindo essa NF agora: espere terminar.")
    if p.status == "cancelada":
        return await _devolver(session, p)
    if queimado(p.emissao_etapa) and not conferido:
        raise _erro(
            409,
            "conferir_no_bling",
            f"O robô chegou a importar o pedido {p.numero} no Bling de {p.vendedor_nome}: "
            "confira lá (pedido e NF) antes de cancelar.",
        )
    p.status, p.updated_by = "cancelada", u.id
    await session.commit()
    await session.refresh(p)
    logger.info("compra_cancelada", compra=str(p.id), numero=p.numero, user=str(u.id))
    return await _devolver(session, p)


@router.post("/{pedido_id}/reabrir", response_model=CompraOut)
async def reabrir(pedido_id: UUID, session: Sess, u: Annotated[User, Depends(_edit)]) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status != "cancelada":
        raise _erro(409, "nao_cancelada", "Só compra cancelada pode ser reaberta.")
    if queimado(p.emissao_etapa):
        raise _queimado(p)
    p.status, p.updated_by = "registrada", u.id
    await session.commit()
    await session.refresh(p)
    return await _devolver(session, p)


@router.delete("/{pedido_id}", status_code=204)
async def excluir(pedido_id: UUID, session: Sess, u: Annotated[User, Depends(_delete)]) -> Response:
    p = await _uma(session, pedido_id, travar=True)
    if p.status == "nf_emitida":
        raise _erro(
            409,
            "tem_nf",
            "Compra com NF não se exclui: desfaça a NF antes (e cancele a NF no Bling).",
        )
    if p.status in ("na_fila", "emitindo"):
        raise _erro(409, "na_fila", "A compra está na fila do robô: tire da fila antes de excluir.")
    if queimado(p.emissao_etapa):
        raise _queimado(p)
    await session.delete(p)
    await session.commit()
    logger.info("compra_excluida", compra=str(pedido_id), numero=p.numero, user=str(u.id))
    return Response(status_code=204)


# --- emissão automática (robô do Mac) ------------------------------------------------------


async def _vendedora_e_itens(
    session: AsyncSession, p: CompraPedido
) -> tuple[CompraVendedora | None, list[CompraPedidoItem]]:
    itens = (await svc.itens_de(session, [p.id]))[p.id]
    return await session.get(CompraVendedora, p.vendedor_cnpj), itens


@router.post("/{pedido_id}/emitir", response_model=CompraOut)
async def emitir(
    pedido_id: UUID,
    session: Sess,
    u: Annotated[User, Depends(_edit)],
    body: EmitirIn | None = None,
) -> CompraOut:
    """Põe a compra na fila do robô da vendedora (ele emite a NF de verdade).
    Clicar de novo com a compra já na fila (ou com o robô) não muda nada."""
    body = body or EmitirIn()
    p = await _uma(session, pedido_id, travar=True)
    if p.status in ("na_fila", "emitindo"):
        return await _devolver(session, p)
    if queimado(p.emissao_etapa):
        raise _queimado(p)
    if p.status not in ("registrada", "erro") or p.nf_numero or p.nf_chave:
        raise _erro(
            409,
            "nao_emite",
            "Só compra sem NF (registrada ou com erro) vai para o robô"
            + (f": essa já tem a NF {p.nf_numero}." if p.nf_numero else "."),
        )
    v, itens = await _vendedora_e_itens(session, p)
    problemas = await emissao.problemas_da_compra(session, p, itens, v)
    if problemas:
        raise _erro(
            422,
            "nao_emite_automatico",
            "Essa compra não vai para o robô: " + " ".join(problemas),
            problemas=problemas,
        )
    if p.planilha_baixada_em is not None and not body.confirmo_planilha_nao_importada:
        raise _erro(
            409,
            "planilha_ja_baixada",
            "A planilha dessa compra já foi baixada. Se ela foi importada no Bling, o robô "
            "faz pedido e NF em dobro: confirme que NÃO foi importada.",
        )
    outras = list(
        (
            await session.execute(
                select(CompraPedido.numero)
                .where(
                    CompraPedido.company_id == p.company_id,
                    CompraPedido.competencia == p.competencia,
                    CompraPedido.vendedor_cnpj == p.vendedor_cnpj,
                    or_(
                        CompraPedido.status.in_(("na_fila", "emitindo", "nf_emitida")),
                        # parou depois do import: a NF dela pode ter saído no Bling
                        and_(
                            CompraPedido.status == "erro",
                            CompraPedido.emissao_etapa.in_(ETAPAS[1:]),
                        ),
                    ),
                    CompraPedido.id != p.id,
                )
                .order_by(CompraPedido.numero)
            )
        ).scalars()
    )
    if outras and not body.confirmo_outra_no_mes:
        raise _erro(
            409,
            "outra_no_mes",
            "Já há compra dessa empresa, nesse mês, dessa vendedora na fila, com NF ou parada "
            f"depois do import (nº {', '.join(str(n) for n in outras)}): confirme que é outra "
            "compra.",
            numeros=outras,
        )
    p.status, p.emissao_pedida_em, p.updated_by = "na_fila", _agora(), u.id
    # Emitir de novo (de um erro antes do import) = 3 tentativas novas
    p.emissao_erro, p.emissao_etapa, p.emissao_tentativas = None, None, 0
    await session.commit()
    await session.refresh(p)
    logger.info("compra_na_fila", compra=str(p.id), numero=p.numero, user=str(u.id))
    return await _devolver(session, p)


@router.post("/{pedido_id}/tirar-da-fila", response_model=CompraOut)
async def tirar_da_fila(
    pedido_id: UUID, session: Sess, u: Annotated[User, Depends(_edit)]
) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status == "emitindo":
        raise _erro(
            409,
            "robo_ja_pegou",
            "O robô já pegou essa compra: use 'tirar do robô' (e confira no Bling).",
        )
    if p.status != "na_fila":
        raise _erro(409, "fora_da_fila", "Essa compra não está na fila do robô.")
    p.status, p.emissao_pedida_em, p.updated_by = "registrada", None, u.id
    await session.commit()
    await session.refresh(p)
    logger.info("compra_fora_da_fila", compra=str(p.id), numero=p.numero, user=str(u.id))
    return await _devolver(session, p)


@router.post("/{pedido_id}/parar-robo", response_model=CompraOut)
async def parar_robo(
    pedido_id: UUID, session: Sess, u: Annotated[User, Depends(_delete)]
) -> CompraOut:
    """Tira a compra do robô no meio (vira erro, a etapa fica). O robô recebe 409
    no próximo passo e para sem mexer no Bling."""
    p = await _uma(session, pedido_id, travar=True)
    if p.status != "emitindo":
        raise _erro(409, "fora_do_robo", "O robô não está com essa compra.")
    p.status, p.updated_by = "erro", u.id
    p.emissao_erro = f"tirada do robô por {u.name or u.email}"
    await session.commit()
    await session.refresh(p)
    logger.info(
        "compra_tirada_do_robo",
        compra=str(p.id),
        numero=p.numero,
        etapa=p.emissao_etapa,
        user=str(u.id),
    )
    return await _devolver(session, p)


def _n(p: CompraPedido) -> str:
    return f" {p.nf_numero}" if p.nf_numero else ""


def _recusar_xml(p: CompraPedido, xml: bytes) -> None:
    """409 se o XML guardado não for o autorizado da NF dessa compra (outra NF, não
    autorizada ou divergente): nem o XML nem a DANFE dele saem daqui."""
    problema = arquivos.problema_do_xml(p, xml)
    if problema is None:
        return
    logger.warning("compra_xml_recusado", compra=str(p.id), code=problema.code)
    raise _erro(
        409,
        problema.code,
        f"O XML da NF{_n(p)} no DaVinci não sai: {problema.motivo}. "
        f"Baixe o XML/DANFE autorizado no Bling de {p.vendedor_nome}.",
    )


@router.get("/{pedido_id}/xml")
async def xml_da_nf(
    pedido_id: UUID, session: Sess, _u: Annotated[User, Depends(_view)]
) -> Response:
    p = await _uma(session, pedido_id)
    xml = await arquivos.xml_da_compra(session, p)
    if not xml:
        raise _erro(404, "sem_xml", "O XML dessa NF não está no DaVinci.")
    _recusar_xml(p, xml)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{arquivos.nome_xml(p)}"'},
    )


@router.get("/{pedido_id}/pdf")
async def danfe_da_nf(
    pedido_id: UUID, session: Sess, _u: Annotated[User, Depends(_view)]
) -> Response:
    """A DANFE da NF de compra, desenhada do XML autorizado (o mesmo desenho da NF
    do Atendimento): inline, a tela abre numa aba para imprimir. Mesma trava do
    XML: a chave inteira da página."""
    p = await _uma(session, pedido_id)
    if p.status == "cancelada":
        # a NF foi desfeita/cancelada no Bling: a DANFE sairia sem marca nenhuma (o XML
        # autorizado não sabe do cancelamento). O XML avulso continua baixando.
        raise _erro(
            409,
            "compra_cancelada",
            f"Compra cancelada: a NF{_n(p)} foi desfeita, não há DANFE para imprimir.",
        )
    xml = await arquivos.xml_da_compra(session, p)
    if not xml:
        raise _erro(404, "sem_xml", "O XML dessa NF não está no DaVinci: sem ele não há DANFE.")
    _recusar_xml(p, xml)
    try:
        pdf = await arquivos.danfe(xml)
    except DanfeInvalida as e:
        logger.warning("compra_danfe_invalida", compra=str(p.id), code=e.code)
        raise _erro(
            422,
            "danfe_invalida",
            "Não deu para desenhar a DANFE desse XML: baixe o XML (ou a DANFE no Bling).",
            erros=[e.code],
        ) from e
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{arquivos.nome_pdf(p)}"',
            "Cache-Control": "no-store",
        },
    )


# --- planilha do Bling ---------------------------------------------------------------------


def _csv(conteudo: bytes, nome: str) -> Response:
    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.get("/{pedido_id}/planilha")
async def planilha_uma(
    pedido_id: UUID, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> Response:
    p = await _uma(session, pedido_id, travar=True)
    if p.status == "cancelada":
        raise _erro(409, "compra_cancelada", "Compra cancelada não gera planilha.")
    if p.status in ("na_fila", "emitindo"):
        # importar à mão enquanto o robô importa = pedido/NF em dobro no Bling
        raise _erro(
            409, "na_fila", "A compra está com o robô: tire da fila antes de baixar a planilha."
        )
    if queimado(p.emissao_etapa):
        raise _queimado(p)
    itens = await svc.itens_de(session, [p.id])
    conteudo, nome = svc.planilha([p], itens), svc.nome_da_planilha([p])
    # baixou = pode ter importado: o "Emitir" passa a pedir confirmação
    p.planilha_baixada_em = _agora()
    await session.commit()
    return _csv(conteudo, nome)


@router.post("/planilha")
async def planilha_varias(
    body: PlanilhaIn, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> Response:
    """Várias compras num arquivo só — todas da MESMA vendedora (cada planilha
    é importada no Bling de uma empresa)."""
    ids = list(dict.fromkeys(body.ids))
    pedidos = list(
        (
            await session.execute(
                select(CompraPedido)
                .where(CompraPedido.id.in_(ids))
                .order_by(CompraPedido.numero)
                .with_for_update()
            )
        ).scalars()
    )
    if len(pedidos) != len(ids):
        raise _erro(404, "compra_nao_encontrada", "Alguma das compras escolhidas não existe mais.")
    if any(p.status == "cancelada" for p in pedidos):
        raise _erro(409, "compra_cancelada", "Tire as compras canceladas da seleção.")
    com_nf = [p for p in pedidos if p.status in ("nf_emitida", "na_fila", "emitindo")]
    if com_nf:
        # o pedido já está no Bling: número repetido = o Bling recusa o arquivo INTEIRO
        raise _erro(
            409,
            "compra_com_nf",
            "Tire da seleção as compras que já têm NF (o pedido já está no Bling e o número "
            "repetido faz o Bling recusar o arquivo todo): nº "
            + ", ".join(str(p.numero) for p in com_nf)
            + ".",
        )
    queimadas = [p for p in pedidos if queimado(p.emissao_etapa)]
    if queimadas:
        raise _erro(
            409,
            "numero_queimado",
            "Tire da seleção as compras que o robô chegou a importar (o número pode já "
            "estar no Bling): nº " + ", ".join(str(p.numero) for p in queimadas) + ".",
        )
    if len({p.vendedor_cnpj for p in pedidos}) > 1:
        raise _erro(
            422,
            "vendedoras_diferentes",
            "Escolha compras de uma empresa que vende só: cada planilha é importada "
            "no Bling de uma empresa.",
        )
    itens = await svc.itens_de(session, [p.id for p in pedidos])
    conteudo, nome = svc.planilha(pedidos, itens), svc.nome_da_planilha(pedidos)
    agora = _agora()
    for p in pedidos:
        p.planilha_baixada_em = agora
    await session.commit()
    return _csv(conteudo, nome)


# --- robô do Mac (~/nf-executor/compra_emitir.py) -----------------------------------------
#
# Fora da senha extra (o robô não tem a chave de 30 min) e com prefixo próprio:
# `/api/nfse/compras/{pedido_id}` (UUID) engoliria `/agent`. Guardado pelo mesmo
# X-Agent-Token das NFs (`routers/nf.py`); vazio = fechado (401).
#   GET  /fila?serial=        quantas na fila + a que está com o robô
#   GET  /previa?numero=      o que o lease entregaria, sem mudar nada (DRY-RUN)
#   POST /lease               na_fila → emitindo (uma por vez por perfil)
#   POST /{id}/etapa          até onde chegou (só avança; recuo import_tentado → lease)
#   POST /{id}/resultado      nf_emitida (XML conferido de novo aqui) | erro

agent_router = APIRouter(
    prefix="/api/nf-cadastro/agent/compras",
    tags=["compras-agent"],
    dependencies=[Depends(_require_nf_agent_token)],
)


def _fora_do_robo(p: CompraPedido) -> HTTPException:
    return _erro(
        409,
        "fora_do_robo",
        f"A compra {p.numero} não está com o robô (status {p.status}): pare sem mexer no Bling.",
        status_atual=p.status,
    )


async def _cnpjs_do_perfil(
    session: AsyncSession, serial: str, *, travar: bool, so_automaticas: bool = True
) -> list[str]:
    q = select(CompraVendedora.cnpj).where(CompraVendedora.adspower_serial == serial)
    if so_automaticas:
        q = q.where(CompraVendedora.automatico.is_(True), CompraVendedora.ativo.is_(True))
    if travar:
        # serializa os leases do mesmo perfil (dois robôs = uma compra só)
        q = q.with_for_update()
    return list((await session.execute(q)).scalars())


def _ocupada(p: CompraPedido) -> dict:
    return {
        "id": str(p.id),
        "numero": p.numero,
        "etapa": p.emissao_etapa,
        "desde": p.emissao_iniciada_em.isoformat() if p.emissao_iniciada_em else None,
        "erro": p.emissao_erro,
    }


@agent_router.get("/fila")
async def agente_fila(session: Sess, serial: str = Query(..., min_length=1)) -> dict:
    cnpjs = await _cnpjs_do_perfil(session, serial, travar=False)
    # a que está com o robô aparece mesmo se a vendedora deixou de ser automática no
    # meio: sumir daqui = o robô entende "tirada do robô" e larga a NF pela metade
    todas = await _cnpjs_do_perfil(session, serial, travar=False, so_automaticas=False)
    if not todas:
        return {"na_fila": 0, "emitindo": []}
    na_fila = (
        await session.execute(
            select(func.count())
            .select_from(CompraPedido)
            .where(CompraPedido.vendedor_cnpj.in_(cnpjs), CompraPedido.status == "na_fila")
        )
    ).scalar_one()
    emitindo = (
        await session.execute(
            select(CompraPedido)
            .where(CompraPedido.vendedor_cnpj.in_(todas), CompraPedido.status == "emitindo")
            .order_by(CompraPedido.emissao_iniciada_em)
        )
    ).scalars()
    return {"na_fila": int(na_fila), "emitindo": [_ocupada(p) for p in emitindo]}


@agent_router.get("/previa")
async def agente_previa(session: Sess, numero: int = Query(...)) -> dict:
    """O que o lease entregaria dessa compra, sem mudar nada (DRY-RUN do robô;
    vale já com a compra registrada). `problemas` = o que o "Emitir" recusaria."""
    p = (
        await session.execute(select(CompraPedido).where(CompraPedido.numero == numero))
    ).scalar_one_or_none()
    if p is None:
        raise _erro(404, "compra_nao_encontrada", f"Compra {numero} não encontrada.")
    if p.status in ("cancelada", "nf_emitida"):
        raise _erro(
            409, "fora_do_robo", f"A compra {numero} está {p.status}.", status_atual=p.status
        )
    v, itens = await _vendedora_e_itens(session, p)
    return {
        **await emissao.payload_agente(session, p, itens),
        "problemas": await emissao.problemas_da_compra(session, p, itens, v),
    }


@agent_router.post("/lease")
async def agente_lease(body: AgenteLeaseIn, session: Sess) -> dict:
    vazio: dict = {"compras": [], "ocupada": None, "motivo": None}
    if not emissao.janela_aberta():
        return {**vazio, "motivo": "fora_do_horario"}
    cnpjs = await _cnpjs_do_perfil(session, body.serial, travar=True)
    if not cnpjs:
        return {**vazio, "motivo": "sem_vendedora"}
    # uma por vez por perfil: a que está com o robô (mesmo pausada) segura a fila
    atual = (
        await session.execute(
            select(CompraPedido)
            .where(CompraPedido.vendedor_cnpj.in_(cnpjs), CompraPedido.status == "emitindo")
            .order_by(CompraPedido.emissao_iniciada_em)
            .limit(1)
        )
    ).scalar_one_or_none()
    if atual is not None:
        if atual.emissao_etapa == "lease":
            # a resposta do lease se perdeu (o robô não tem estado local dela): nada foi
            # feito no Bling — o import só sai depois do 200 no import_tentado. Entrega a
            # mesma de novo, sem gastar tentativa (a trava do AdsPower = um robô por perfil).
            itens = (await svc.itens_de(session, [atual.id]))[atual.id]
            compra = await emissao.payload_agente(session, atual, itens)
            await session.commit()
            logger.info("compra_lease_de_novo", compra=str(atual.id), numero=atual.numero)
            return {**vazio, "compras": [compra]}
        return {**vazio, "ocupada": _ocupada(atual), "motivo": "ocupada"}
    p = (
        await session.execute(
            select(CompraPedido)
            .where(CompraPedido.vendedor_cnpj.in_(cnpjs), CompraPedido.status == "na_fila")
            .order_by(CompraPedido.emissao_pedida_em.nulls_last(), CompraPedido.numero)
            .limit(1)
            .with_for_update(skip_locked=True, of=CompraPedido)
        )
    ).scalar_one_or_none()
    if p is None:
        return vazio
    p.status, p.emissao_iniciada_em, p.emissao_etapa = "emitindo", _agora(), "lease"
    p.emissao_tentativas = (p.emissao_tentativas or 0) + 1
    p.emissao_erro = None
    await session.flush()
    itens = (await svc.itens_de(session, [p.id]))[p.id]
    compra = await emissao.payload_agente(session, p, itens)
    await session.commit()
    logger.info("compra_lease", compra=str(p.id), numero=p.numero, tentativa=p.emissao_tentativas)
    return {**vazio, "compras": [compra]}


@agent_router.post("/{pedido_id}/etapa")
async def agente_etapa(pedido_id: UUID, body: AgenteEtapaIn, session: Sess) -> dict:
    """Até onde o robô chegou. Antes do import (`import_tentado`) e da SEFAZ
    (`sefaz_tentado`) ele só segue com 200 daqui."""
    p = await _uma(session, pedido_id, travar=True)
    if p.status != "emitindo":
        raise _fora_do_robo(p)
    atual, nova = etapa_indice(p.emissao_etapa), etapa_indice(body.etapa)
    recuo = p.emissao_etapa == "import_tentado" and body.etapa == "lease"
    if body.etapa == ETAPAS[-1] or (nova < atual and not recuo):
        # `devolvido` só pelo /resultado; recuo só o do import que não chegou a clicar
        raise _erro(
            409,
            "etapa_invalida",
            f"Etapa {body.etapa} depois de {p.emissao_etapa}: a etapa só avança.",
            etapa=p.emissao_etapa,
        )
    if nova != atual:
        p.emissao_etapa = body.etapa
        p.emissao_erro = None  # a pausa (erro retentável) ficou para trás
    if body.nf_numero:
        p.nf_numero = body.nf_numero
    await session.commit()
    logger.info(
        "compra_etapa",
        compra=str(p.id),
        numero=p.numero,
        etapa=p.emissao_etapa,
        nf=p.nf_numero,
        detalhe=body.detalhe,
    )
    return {"ok": True, "etapa": p.emissao_etapa}


async def _resultado_ok(session: AsyncSession, p: CompraPedido, body: AgenteResultadoIn) -> dict:
    if not body.nf_numero or not body.nf_chave or not body.xml_b64:
        raise _erro(422, "faltou_xml", "NF emitida precisa de nf_numero, nf_chave e xml_b64.")
    if p.status == "nf_emitida" and p.nf_chave:
        # o mesmo resultado de novo (resposta perdida no caminho) = nada a fazer
        if p.nf_chave == body.nf_chave:
            return {"ok": True, "status": p.status}
        raise _erro(
            409,
            "chave_diferente",
            f"A compra {p.numero} já tem a NF {p.nf_numero} (outra chave).",
        )
    if p.status != "emitindo":
        raise _fora_do_robo(p)
    itens = (await svc.itens_de(session, [p.id]))[p.id]
    try:
        raw = emissao.ler_xml_b64(body.xml_b64)
        nota, erros = emissao.conferir_xml(
            raw, p, itens, nf_numero=body.nf_numero, nf_chave=body.nf_chave
        )
    except ValueError as e:  # base64 torto (binascii.Error) ou não é NF-e (NfXmlError)
        code = e.code if isinstance(e, NfXmlError) else "xml_b64_invalido"
        raise _erro(422, "xml_nao_confere", "XML ilegível.", erros=[code]) from e
    if p.nf_numero and p.nf_numero != body.nf_numero:
        erros.append(f"nNF {body.nf_numero}≠{p.nf_numero} (a NF gerada para a compra)")
    if erros:
        raise _erro(
            422, "xml_nao_confere", "O XML não bate com a compra: " + "; ".join(erros), erros=erros
        )
    outra = (
        await session.execute(
            select(CompraPedido.numero).where(
                CompraPedido.nf_chave == nota.chave, CompraPedido.id != p.id
            )
        )
    ).scalar_one_or_none()
    if outra is not None:
        raise _erro(409, "chave_em_outra_compra", f"Essa chave já está na compra {outra}.")
    await emissao.gravar_nf_nota(session, nota, raw)
    p.status, p.nf_numero, p.nf_chave = "nf_emitida", body.nf_numero, nota.chave
    p.nf_emitida_em, p.emissao_etapa, p.emissao_erro = _agora(), "devolvido", None
    await session.commit()
    logger.info("compra_nf_emitida_robo", compra=str(p.id), numero=p.numero, nf=p.nf_numero)
    return {"ok": True, "status": p.status}


async def _guardar_nf_do_erro(
    session: AsyncSession, p: CompraPedido, body: AgenteResultadoIn
) -> None:
    """O robô parou com a NF AUTORIZADA mas o XML não bateu com a compra (IE, valor…):
    a chave fica na compra (a pessoa anota a NF sem digitar a chave, e o índice único
    protege) e, se o XML for o autorizado (DigestValue = digVal), ele vai para `nf_nota`
    (baixar XML). Só se for a NF dessa vendedora, com a chave e o nº que o robô diz, e a
    chave não estiver noutra compra; senão fica só no texto do erro."""
    try:
        raw = emissao.ler_xml_b64(body.xml_b64 or "")
        nota = parse_nfe(raw)
    except ValueError:
        return
    numero = p.nf_numero or body.nf_numero  # o nº gravado na etapa nf_gerada manda
    if (
        nota.situacao != "100"
        or nota.chave != body.nf_chave
        or nota.emitente_cnpj != p.vendedor_cnpj
        or not numero
        or int(nota.chave[25:34]) != int(numero)
    ):
        return
    outra = (
        await session.execute(
            select(CompraPedido.id).where(
                CompraPedido.nf_chave == nota.chave, CompraPedido.id != p.id
            )
        )
    ).first()
    if outra is not None:
        return
    if emissao.xml_autorizado(raw):
        await emissao.gravar_nf_nota(session, nota, raw)
    p.nf_chave = nota.chave
    p.nf_numero = p.nf_numero or str(int(numero))


@agent_router.post("/{pedido_id}/resultado")
async def agente_resultado(pedido_id: UUID, body: AgenteResultadoIn, session: Sess) -> dict:
    p = await _uma(session, pedido_id, travar=True)
    if body.status == "nf_emitida":
        return await _resultado_ok(session, p, body)
    if p.status != "emitindo":
        raise _fora_do_robo(p)
    # vale a etapa mais adiantada entre a gravada aqui e a que o robô diz
    if body.etapa and etapa_indice(body.etapa) > etapa_indice(p.emissao_etapa):
        p.emissao_etapa = body.etapa
    if body.nf_numero and not p.nf_numero and queimado(p.emissao_etapa):
        p.nf_numero = body.nf_numero  # a NF que ficou pela metade (para quem for conferir)
    motivo = (body.motivo or "erro sem motivo").strip()
    if body.nf_chave:
        motivo += f" (chave {body.nf_chave})"
    if body.nf_chave and body.xml_b64 and not p.nf_chave:
        await _guardar_nf_do_erro(session, p, body)
    p.emissao_erro = motivo
    if body.retentavel and not queimado(p.emissao_etapa):
        # nada chegou ao Bling: volta para a fila (até 3 tentativas)
        if (p.emissao_tentativas or 0) < emissao.MAX_TENTATIVAS:
            p.status, p.emissao_etapa = "na_fila", None
        else:
            p.status = "erro"
    elif not body.retentavel:
        p.status = "erro"
    # retentável com o número queimado: continua com o robô ("pausada"), que retoma
    # do estado local dele; a fila do perfil espera (ou alguém usa "tirar do robô")
    await session.commit()
    logger.info(
        "compra_resultado_erro",
        compra=str(p.id),
        numero=p.numero,
        status=p.status,
        etapa=p.emissao_etapa,
        retentavel=body.retentavel,
        motivo=motivo,
    )
    return {"ok": True, "status": p.status}
