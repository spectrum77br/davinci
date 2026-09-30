"""Notas de serviço em lote: baixar PDFs/XMLs num .zip e imprimir num PDF só.

30/09/2026 (Eduardo: marcar várias notas em "Notas enviadas" e baixar ou
imprimir de uma vez). A NFE.io não tem download em lote de NFS-e emitida: só
`/pdf` e `/xml` de uma nota (`/serviceinvoices/pdf` sem id dá 404, medido na
conta). Então o lote é montado aqui, em 3 fases — a AsyncSession não aceita
uso em paralelo, e chamar `emissao.baixar` com gather misturaria os commits:

1. banco, em série: separa o que não tem arquivo (com o motivo), tira o XML
   já guardado (`nfse_xml_b64`, sem ir à NFE.io) e acha a empresa na NFE.io;
2. rede, até 4 ao mesmo tempo e SEM a sessão (nem a conexão do banco): 1
   cliente da NFE.io, prazo de 40 s por arquivo e 90 s para o lote todo — uma
   NFE.io lenta não segura a tela por minutos;
3. banco, em série: uma linha em nfse_chamada por ida (nunca a chave), guarda
   o XML da nota emitida e 1 commit só.

Nota que falhou não derruba o lote: vira "faltou" com o motivo (no .zip, o
`_FALTARAM.txt`; na resposta, o cabeçalho X-Nfse-Faltaram).
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
import zipfile
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import fitz  # PyMuPDF
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company
from app.models.nfse import NfseEmissao
from app.services.nfse import emissao as svc
from app.services.nfse import empresas as E  # noqa: N812
from app.services.nfse import nfeio
from app.services.nfse import texto as T  # noqa: N812
from app.services.nfse.ambiente import eh_teste
from app.services.nfse.erros import NfseError

logger = structlog.get_logger()

Tipo = Literal["pdf", "xml"]

MAX_LOTE = 100
CONCORRENCIA = 4
PRAZO_POR_ARQUIVO_S = 40.0
# Teto do lote inteiro. O prazo de cada arquivo só conta depois que a nota pega
# uma das 4 vagas: com a NFE.io pendurada, 100 notas seriam 25 levas de 40 s
# (~17 min com a tela esperando). Passou disto, o que ainda não veio vira
# "a NFE.io não respondeu a tempo" e o resto sai.
PRAZO_TOTAL_S = 90.0

# Só estas têm arquivo na NFE.io. "cancelando" fica de fora: o PDF muda quando
# o cancelamento termina.
COM_ARQUIVO = ("emitida", "cancelada")

MOTIVO_NAO_ENCONTRADA = "nota não encontrada"
MOTIVO_NAO_AUTORIZADA = "ainda não autorizada pela prefeitura"
MOTIVO_RECUSADA = "nota recusada: não tem PDF nem XML"
MOTIVO_CANCELANDO = "cancelamento em andamento: tente de novo quando terminar"
MOTIVO_NAO_LIGADA = "empresa não ligada na NFE.io"
MOTIVO_SEM_CHAVE = "a chave da NFE.io não está configurada no servidor"
MOTIVO_CHAVE = "a NFE.io recusou a chave de acesso do servidor"
MOTIVO_PRAZO = "a NFE.io não respondeu a tempo"
MOTIVO_SEM_RESPOSTA = "sem resposta da NFE.io"
MOTIVO_PDF_CORROMPIDO = "o PDF da NFE.io veio corrompido"

MIME = {"pdf": "application/pdf", "xml": "application/xml"}
ROTULO = {"pdf": "PDF", "xml": "XML"}


@dataclass
class ItemLote:
    """Uma nota do lote. `motivo` preenchido = ficou de fora."""

    id: UUID
    emissao: NfseEmissao | None = None
    conteudo: bytes | None = None
    mime: str | None = None
    motivo: str | None = None
    # Só entre as fases (a fase 2 não encosta no objeto do banco).
    cid: str | None = None
    nfeio_id: str | None = None
    arquivo: nfeio.Arquivo | None = None
    foi_a_rede: bool = False
    chave_recusada: bool = False
    espera_ms: int = 0  # quanto esperou a NFE.io até o prazo cortar

    @property
    def ok(self) -> bool:
        return self.conteudo is not None and self.motivo is None


@dataclass
class Lote:
    tipo: Tipo
    itens: list[ItemLote]
    empresas: dict[UUID, Company] = field(default_factory=dict)

    @property
    def prontos(self) -> list[ItemLote]:
        return [i for i in self.itens if i.ok]

    @property
    def faltaram(self) -> list[ItemLote]:
        return [i for i in self.itens if not i.ok]


def _motivo_do_status(e: NfseEmissao) -> str | None:
    if e.status == "cancelando":
        return MOTIVO_CANCELANDO
    if e.status == "rejeitada":
        return MOTIVO_RECUSADA
    if e.status not in COM_ARQUIVO or not e.nfeio_id:
        return MOTIVO_NAO_AUTORIZADA
    return None


def _falhou_na_nfeio(it: ItemLote, tipo: Tipo) -> None:
    """A NFE.io respondeu sem o arquivo: o motivo em português de gente."""
    st = it.arquivo.resposta.status if it.arquivo is not None else None
    if st in (401, 403):
        it.motivo, it.chave_recusada = MOTIVO_CHAVE, True
    elif st == 404:
        it.motivo = svc.MSG_SEM_PDF if tipo == "pdf" else svc.MSG_SEM_XML
    else:
        it.motivo = MOTIVO_SEM_RESPOSTA


async def _buscar_na_rede(cliente: nfeio.ClienteNfeio, itens: list[ItemLote], tipo: Tipo) -> None:
    """FASE 2: sem sessão. Cada ida tem o seu prazo e o lote todo tem um teto;
    erro de uma não para as outras."""
    sem = asyncio.Semaphore(CONCORRENCIA)
    comeco: dict[UUID, float] = {}

    async def um(it: ItemLote) -> None:
        async with sem:
            it.foi_a_rede, comeco[it.id] = True, time.monotonic()
            ir = cliente.pdf if tipo == "pdf" else cliente.xml
            try:
                it.arquivo = await asyncio.wait_for(
                    ir(it.cid or "", it.nfeio_id or ""), PRAZO_POR_ARQUIVO_S
                )
            except TimeoutError:
                it.motivo, it.espera_ms = MOTIVO_PRAZO, int(PRAZO_POR_ARQUIVO_S * 1000)
            except Exception as ex:  # noqa: BLE001 — uma nota não derruba o lote
                logger.warning(
                    "nfse_lote_arquivo_falhou", emissao=str(it.id), erro=type(ex).__name__
                )
                it.motivo = MOTIVO_SEM_RESPOSTA

    try:
        async with asyncio.timeout(PRAZO_TOTAL_S):
            await asyncio.gather(*(um(it) for it in itens))
    except TimeoutError:
        # O teto cortou: as que estavam na NFE.io ou ainda na fila ficam de fora.
        agora = time.monotonic()
        for it in itens:
            if it.arquivo is None and it.motivo is None:
                it.motivo = MOTIVO_PRAZO
                if it.id in comeco:
                    it.espera_ms = int((agora - comeco[it.id]) * 1000)
        logger.warning(
            "nfse_lote_prazo_total",
            tipo=tipo,
            total=len(itens),
            cortadas=sum(1 for it in itens if it.motivo == MOTIVO_PRAZO),
        )


async def baixar_lote(
    session: AsyncSession,
    ids: list[UUID],
    tipo: Tipo,
    *,
    cli: nfeio.ClienteNfeio | None = None,
) -> Lote:
    """O PDF (ou XML) de cada nota, NA ORDEM dos ids (repetidos contam uma vez).
    Nenhuma com arquivo: 404 `sem_arquivo`; a NFE.io recusou a chave em todas
    as que foram até ela: 503 `chave_nfeio` (em vez de um .zip vazio); não
    respondeu em nenhuma: 502 `nfeio_sem_resposta`."""
    ids = list(dict.fromkeys(ids))
    rows = (
        (await session.execute(select(NfseEmissao).where(NfseEmissao.id.in_(ids)))).scalars().all()
    )
    por_id = {e.id: e for e in rows}
    itens = [ItemLote(id=i, emissao=por_id.get(i)) for i in ids]
    empresas_ids = {e.company_id for e in rows}
    empresas = (
        {
            c.id: c
            for c in (
                await session.execute(select(Company).where(Company.id.in_(empresas_ids)))
            ).scalars()
        }
        if empresas_ids
        else {}
    )

    # FASE 1 — banco, em série.
    pendentes: list[ItemLote] = []
    for it in itens:
        e = it.emissao
        if e is None:
            it.motivo = MOTIVO_NAO_ENCONTRADA
            continue
        it.motivo = _motivo_do_status(e)
        if it.motivo:
            continue
        if tipo == "xml" and e.nfse_xml_b64:
            it.conteudo, it.mime = base64.b64decode(e.nfse_xml_b64), MIME["xml"]
            continue
        it.cid, it.nfeio_id = await svc._cid(session, e), e.nfeio_id
        if not it.cid:
            it.motivo = MOTIVO_NAO_LIGADA
            continue
        pendentes.append(it)

    # FASE 2 — rede, concorrente e sem a sessão.
    if pendentes:
        # Fecha a transação da leitura (não há nada pendente): a conexão volta
        # ao pool enquanto a NFE.io responde, em vez de ficar presa até 90 s.
        # expire_on_commit=False (app/db.py): as notas seguem valendo na fase 3.
        await session.commit()
        if cli is None and not nfeio.chave_configurada():
            for it in pendentes:
                it.motivo, it.chave_recusada = MOTIVO_SEM_CHAVE, True
        else:
            async with svc._cliente(cli) as cliente:
                await _buscar_na_rede(cliente, pendentes, tipo)

        # FASE 3 — banco, em série, 1 commit só.
        for it in pendentes:
            e = it.emissao
            assert e is not None  # só entra em `pendentes` com a nota
            # Só registra quem chegou a ir (a que ficou na fila não chamou a NFE.io).
            if it.motivo == MOTIVO_PRAZO and it.foi_a_rede:
                svc._log(
                    session,
                    nfeio.Resposta(
                        tipo,
                        None,
                        erro_rede=f"prazo do lote esgotado ({it.espera_ms / 1000:.0f} s)",
                        duracao_ms=it.espera_ms,
                    ),
                    e.company_id,
                    e.id,
                    e.nfeio_ambiente,
                )
            if it.arquivo is None:
                continue
            svc._log(session, it.arquivo.resposta, e.company_id, e.id, e.nfeio_ambiente)
            if not it.arquivo.conteudo:
                _falhou_na_nfeio(it, tipo)
                continue
            it.conteudo = it.arquivo.conteudo
            it.mime = (it.arquivo.tipo or MIME[tipo]).split(";")[0].strip() or MIME[tipo]
            if tipo == "xml" and e.status == "emitida" and not e.nfse_xml_b64:
                e.nfse_xml_b64 = base64.b64encode(it.conteudo).decode("ascii")
        await session.commit()

    lote = Lote(tipo=tipo, itens=itens, empresas=empresas)
    _checar_que_sobrou(lote)
    logger.info(
        "nfse_lote_arquivos",
        tipo=tipo,
        total=len(itens),
        ok=len(lote.prontos),
        na_nfeio=len(pendentes),
    )
    return lote


def _checar_que_sobrou(lote: Lote) -> None:
    if lote.prontos:
        return
    foram = [i for i in lote.itens if i.foi_a_rede or i.chave_recusada or i.motivo == MOTIVO_PRAZO]
    if foram and all(i.chave_recusada for i in foram):
        sem_chave = any(i.motivo == MOTIVO_SEM_CHAVE for i in foram)
        raise NfseError(
            503, "chave_nfeio", nfeio.CHAVE_AUSENTE if sem_chave else nfeio.CHAVE_RECUSADA
        )
    # A NFE.io não respondeu (fora do ar, lenta) em todas as que foram até ela:
    # não é "sem arquivo" — é tentar de novo daqui a pouco, como no download de
    # uma nota só (502 nfeio_sem_resposta).
    if foram and all(i.motivo in (MOTIVO_PRAZO, MOTIVO_SEM_RESPOSTA) for i in foram):
        raise NfseError(502, "nfeio_sem_resposta", E.MSG_SEM_RESPOSTA)
    raise NfseError(
        404,
        "sem_arquivo",
        f"Nenhuma das notas marcadas tem {ROTULO[lote.tipo]} disponível na NFE.io.",
    )


# --- o .zip ---------------------------------------------------------------------------


def _apelido(lote: Lote, e: NfseEmissao) -> str:
    c = lote.empresas.get(e.company_id)
    if c is None:
        return ""
    return c.apelido or c.razao_social or c.cnpj or ""


def _pastas(lote: Lote) -> dict[tuple[UUID, bool], str]:
    """Uma pasta por empresa (o nº da NFS-e é POR EMPRESA: ATV nº 4, Rocha nº 4),
    com TESTE_ na frente das notas de teste. Resolvida uma vez por empresa: o
    apelido não é único, e dois que só mudam acento ou maiúscula ("Loca Fácil" e
    "LOCA-FACIL") cairiam na mesma pasta — ou em duas que se sobrescrevem ao
    extrair no Windows/Mac. A segunda ganha o CNPJ no nome."""
    pastas: dict[tuple[UUID, bool], str] = {}
    usados: set[str] = set()  # em minúsculas
    for it in lote.prontos:
        e = it.emissao
        assert e is not None
        chave = (e.company_id, eh_teste(e.nfeio_ambiente))
        if chave in pastas:
            continue
        c = lote.empresas.get(e.company_id)
        cnpj = T.so_digitos(c.cnpj if c else "")
        nome = svc.slug_arquivo(_apelido(lote, e)) or cnpj or "empresa"
        if chave[1]:
            nome = f"TESTE_{nome}"
        if nome.lower() in usados and cnpj and not nome.endswith(cnpj):
            nome = f"{nome}_{cnpj}"
        base, n = nome, 1
        while nome.lower() in usados:
            n += 1
            nome = f"{base}_{n}"
        usados.add(nome.lower())
        pastas[chave] = nome
    return pastas


def _nome(e: NfseEmissao, ext: str) -> str:
    comp = f"{e.competencia.year:04d}-{e.competencia.month:02d}" if e.competencia else "sem-mes"
    sufixo = "_CANCELADA" if e.status == "cancelada" else ""
    return f"NFSe_{svc.slug_arquivo(e.n_nfse) or 'sem-numero'}_{comp}{sufixo}.{ext}"


def _unico(caminho: str, usados: set[str]) -> str:
    """Nome repetido ganha _2, _3… antes da extensão. Compara sem olhar
    maiúsculas (`usados` guarda em minúsculas): no Windows e no Mac, "A.pdf" e
    "a.pdf" são o mesmo arquivo."""
    base, ponto, ext = caminho.rpartition(".")
    n, final = 1, caminho
    while final.lower() in usados:
        n += 1
        final = f"{base}_{n}{ponto}{ext}"
    usados.add(final.lower())
    return final


def texto_faltaram(lote: Lote) -> str:
    """O `_FALTARAM.txt`: nº, empresa, tomador e o motivo de cada uma."""
    falta = lote.faltaram
    linhas = [
        f"Notas que ficaram de fora ({len(falta)} de {len(lote.itens)}):",
        "",
    ]
    for it in falta:
        e = it.emissao
        if e is None:
            linhas.append(f"- nota {it.id}: {it.motivo}")
            continue
        tomador = ((e.snapshot or {}).get("tomador") or {}).get("nome") or "tomador sem nome"
        comp = f"{e.competencia.month:02d}/{e.competencia.year}" if e.competencia else "sem mês"
        linhas.append(
            f"- nº {e.n_nfse or 'sem número'} · {_apelido(lote, e) or 'empresa'} · {tomador}"
            f" · competência {comp}: {it.motivo}"
        )
    return "\n".join(linhas) + "\n"


def montar_zip(lote: Lote) -> bytes:
    buf = io.BytesIO()
    usados: set[str] = set()
    pastas = _pastas(lote)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for it in lote.prontos:
            e = it.emissao
            assert e is not None and it.conteudo is not None
            pasta = pastas[(e.company_id, eh_teste(e.nfeio_ambiente))]
            z.writestr(_unico(f"{pasta}/{_nome(e, lote.tipo)}", usados), it.conteudo)
        if lote.faltaram:
            z.writestr("_FALTARAM.txt", texto_faltaram(lote))
    return buf.getvalue()


# --- o PDF juntado (Imprimir) ---------------------------------------------------------


def juntar_pdfs(lote: Lote) -> bytes:
    """Um PDF só, NA ORDEM dos ids. PDF que não abre (ou vazio) vira "faltou"
    em vez de derrubar a impressão inteira (o `juntar_varios` das etiquetas
    derruba — por isso este é próprio)."""
    saida = fitz.open()
    try:
        for it in lote.prontos:
            try:
                with fitz.open(stream=it.conteudo, filetype="pdf") as doc:
                    if doc.page_count == 0:
                        raise ValueError("pdf_vazio")
                    saida.insert_pdf(doc)
            except Exception as ex:  # noqa: BLE001 — PDF ruim fica de fora, o resto sai
                logger.warning("nfse_lote_pdf_invalido", emissao=str(it.id), erro=type(ex).__name__)
                it.motivo = MOTIVO_PDF_CORROMPIDO
        if saida.page_count == 0:
            raise NfseError(
                404, "sem_arquivo", "Nenhuma das notas marcadas tem PDF disponível na NFE.io."
            )
        return saida.tobytes()
    finally:
        saida.close()
