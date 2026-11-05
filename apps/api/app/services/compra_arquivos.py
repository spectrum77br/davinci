"""Contabilidade › Compra — imprimir e baixar a NF de compra (04/11/2026).

Eduardo: "igual emissão de serviço, tem que dar para imprimir a nota etc.". A NF
de compra é NF-e (sai no Bling da vendedora), não NFS-e: o documento é o XML
AUTORIZADO que o robô devolveu (ou que já estava no coletor, pela chave anotada)
em `nf_nota`, e a DANFE é DESENHADA dele — o mesmo desenho da NF que o
Atendimento anexa (`services/atendimento/danfe.gerar_pdf`). Nada vai ao Bling.

Igual à Emissão de Serviço (`services/nfse/lote_arquivos.py`): uma compra (a
DANFE abre na aba; o XML baixa) e o lote — um PDF só para imprimir, na ordem
pedida, ou um .zip com uma pasta por compradora e o `_FALTARAM.txt`. Só entra no
lote compra com NF emitida e o XML no DaVinci; a que não tem (NF anotada sem a
chave, chave sem XML, sem NF, cancelada, DANFE que não desenha) não derruba o
lote: vira "faltou" com o motivo (no .zip e no cabeçalho X-Nfse-Faltaram).

O XML só sai (e só vira DANFE) se for o AUTORIZADO da NF DESSA compra
(`problema_do_xml`, corretor 04/11): emitente = vendedora e destinatário = CNPJ da
compradora (a chave anotada à mão podia ser de qualquer NF do `nf_nota`, até a de
venda para um CPF), cStat 100 e DigestValue = digVal (o coletor sobrescreve o XML
sem conferir: NF reenviada depois de autorizada — memória 19/10 — sairia "limpa").
"""

from __future__ import annotations

import asyncio
import io
import re
import zipfile
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import fitz  # PyMuPDF
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compra import CompraPedido
from app.models.nf import NfNota
from app.services.atendimento.danfe import DanfeInvalida, gerar_pdf
from app.services.compra_emissao import xml_autorizado
from app.services.nf_xml import NfXmlError, parse_nfe
from app.services.nfse.emissao import slug_arquivo
from app.services.nfse.erros import NfseError

logger = structlog.get_logger()

Tipo = Literal["pdf", "xml"]

# Igual à NFS-e (lote_arquivos.MAX_LOTE e o schema LoteArquivosIn).
MAX_LOTE = 100

MOTIVO_NAO_ENCONTRADA = "compra não encontrada"
MOTIVO_CANCELADA = "compra cancelada"
MOTIVO_SEM_NF = "a NF ainda não saiu"
MOTIVO_SEM_CHAVE = "NF anotada sem a chave: o XML não está no DaVinci"
MOTIVO_SEM_XML = "o XML dessa NF não está no DaVinci"
MOTIVO_DANFE = "não deu para desenhar a DANFE desse XML"
MOTIVO_OUTRA_NF = (
    "o XML guardado com essa chave é de outra NF (emitente ou destinatário não são os da compra)"
)
MOTIVO_NAO_AUTORIZADA = "a NF não está autorizada na SEFAZ (cStat {cstat})"
MOTIVO_DIVERGENTE = "o XML guardado não é o autorizado (DigestValue ≠ digVal)"

ROTULO = {"pdf": "PDF (DANFE)", "xml": "XML"}


# --- uma compra -----------------------------------------------------------------------


async def xml_da_compra(session: AsyncSession, p: CompraPedido) -> bytes | None:
    """O XML da NF da compra, pela chave (o que o robô devolveu ou o coletor subiu)."""
    if not p.nf_chave:
        return None
    xml = await session.scalar(select(NfNota.xml).where(NfNota.chave == p.nf_chave))
    return bytes(xml) if xml else None


@dataclass(frozen=True)
class ProblemaXml:
    """Por que o XML guardado não sai (nem vira DANFE): `code` da resposta 409 e o
    `motivo` curto do `_FALTARAM.txt` / X-Nfse-Faltaram."""

    code: str
    motivo: str


def problema_do_xml(p: CompraPedido, raw: bytes) -> ProblemaXml | None:
    """None = é o XML AUTORIZADO da NF dessa compra. XML que nem lê como NF-e passa
    daqui (a DANFE recusa com `danfe_invalida`; o coletor e o robô só gravam em
    `nf_nota` o que o `parse_nfe` lê)."""
    try:
        nota = parse_nfe(raw)
    except NfXmlError:
        return None
    comprador = re.sub(r"\D", "", str((p.comprador or {}).get("cnpj") or ""))
    if (
        nota.chave != p.nf_chave
        or nota.emitente_cnpj != p.vendedor_cnpj
        or not comprador
        or nota.destinatario_doc != comprador
    ):
        return ProblemaXml("xml_de_outra_nf", MOTIVO_OUTRA_NF)
    if nota.situacao != "100":
        cstat = nota.situacao or "sem protocolo"
        return ProblemaXml("nf_nao_autorizada", MOTIVO_NAO_AUTORIZADA.format(cstat=cstat))
    if not xml_autorizado(raw):
        return ProblemaXml("xml_divergente", MOTIVO_DIVERGENTE)
    return None


def numero_da_nf(p: CompraPedido) -> str:
    """O nº da NF para o nome do arquivo: o anotado; sem ele, o da chave (posições 26–34)."""
    n = re.sub(r"\D", "", p.nf_numero or "")
    if n:
        return n
    if p.nf_chave and len(p.nf_chave) == 44 and p.nf_chave.isdigit():
        return str(int(p.nf_chave[25:34]))
    return "sem-numero"


def nome_pdf(p: CompraPedido) -> str:
    return f"danfe_nf{numero_da_nf(p)}.pdf"


def nome_xml(p: CompraPedido) -> str:
    return f"nfe_{numero_da_nf(p)}.xml"


async def danfe(xml: bytes) -> bytes:
    """A DANFE do XML. PyMuPDF é síncrono (centenas de ms): fora do laço.
    Levanta `DanfeInvalida`."""
    return await asyncio.to_thread(gerar_pdf, xml)


# --- lote -------------------------------------------------------------------------------


@dataclass
class ItemLote:
    """Uma compra do lote. `motivo` preenchido = ficou de fora."""

    id: UUID
    compra: CompraPedido | None = None
    xml: bytes | None = None
    conteudo: bytes | None = None
    motivo: str | None = None

    @property
    def ok(self) -> bool:
        return self.conteudo is not None and self.motivo is None


@dataclass
class Lote:
    tipo: Tipo
    itens: list[ItemLote]

    @property
    def prontos(self) -> list[ItemLote]:
        return [i for i in self.itens if i.ok]

    @property
    def faltaram(self) -> list[ItemLote]:
        return [i for i in self.itens if not i.ok]


def _motivo(p: CompraPedido | None, xmls: dict[str, bytes]) -> str | None:
    if p is None:
        return MOTIVO_NAO_ENCONTRADA
    if p.status == "cancelada":
        # a NF foi desfeita/cancelada no Bling: imprimir a DANFE dela engana
        return MOTIVO_CANCELADA
    if p.status != "nf_emitida":
        return MOTIVO_SEM_NF
    if not p.nf_chave:
        return MOTIVO_SEM_CHAVE
    xml = xmls.get(p.nf_chave)
    if not xml:
        return MOTIVO_SEM_XML
    problema = problema_do_xml(p, xml)
    return problema.motivo if problema else None


def _desenhar(itens: list[ItemLote]) -> None:
    """Síncrono (roda numa thread): a DANFE de cada compra com XML. Só mexe no
    item (nunca no objeto do banco)."""
    for it in itens:
        if it.motivo or it.xml is None:
            continue
        try:
            it.conteudo = gerar_pdf(it.xml)
        except DanfeInvalida as e:
            logger.warning("compra_danfe_invalida", compra=str(it.id), code=e.code)
            it.motivo = MOTIVO_DANFE


async def montar_lote(session: AsyncSession, ids: list[UUID], tipo: Tipo) -> Lote:
    """O PDF (DANFE) ou o XML de cada compra, NA ORDEM dos ids (repetidos contam
    uma vez). Nenhuma com arquivo: 404 `sem_arquivo` (ou 422 `danfe_invalida`
    quando todas as que tinham XML não desenharam)."""
    ids = list(dict.fromkeys(ids))
    compras = {
        p.id: p
        for p in (
            await session.execute(select(CompraPedido).where(CompraPedido.id.in_(ids)))
        ).scalars()
    }
    chaves = {p.nf_chave for p in compras.values() if p.status == "nf_emitida" and p.nf_chave}
    xmls: dict[str, bytes] = {}
    if chaves:
        linhas = await session.execute(
            select(NfNota.chave, NfNota.xml).where(NfNota.chave.in_(chaves))
        )
        xmls = {chave: bytes(xml) for chave, xml in linhas.all() if xml}
    itens: list[ItemLote] = []
    for i in ids:
        p = compras.get(i)
        it = ItemLote(id=i, compra=p, motivo=_motivo(p, xmls))
        if it.motivo is None and p is not None and p.nf_chave:
            it.xml = xmls[p.nf_chave]
            if tipo == "xml":
                it.conteudo = it.xml
        itens.append(it)
    if tipo == "pdf" and any(it.xml is not None for it in itens):
        # Fecha a transação da leitura (nada pendente) antes de desenhar: a conexão
        # volta ao pool enquanto o PyMuPDF trabalha (100 DANFEs levam segundos).
        # expire_on_commit=False (app/db.py): as compras seguem valendo depois.
        await session.commit()
        await asyncio.to_thread(_desenhar, itens)
    lote = Lote(tipo=tipo, itens=itens)
    _checar_que_sobrou(lote)
    logger.info("compra_lote_arquivos", tipo=tipo, total=len(itens), ok=len(lote.prontos))
    return lote


def _checar_que_sobrou(lote: Lote) -> None:
    if lote.prontos:
        return
    if any(i.motivo == MOTIVO_DANFE for i in lote.itens):
        raise NfseError(
            422,
            "danfe_invalida",
            "Não deu para desenhar a DANFE das compras marcadas: baixe o XML "
            "(ou a DANFE no Bling).",
        )
    motivos = "; ".join(dict.fromkeys(i.motivo for i in lote.itens if i.motivo))
    raise NfseError(
        404,
        "sem_arquivo",
        f"Nenhuma das compras marcadas tem a NF emitida com o XML autorizado no DaVinci "
        f"({ROTULO[lote.tipo]}): {motivos}.",
    )


# --- o .zip ---------------------------------------------------------------------------


def _quem_compra(p: CompraPedido) -> str:
    c = p.comprador or {}
    return str(c.get("apelido") or c.get("razao_social") or c.get("cnpj") or "")


def _pastas(lote: Lote) -> dict[UUID, str]:
    """Uma pasta por compradora (company_id), resolvida uma vez: dois apelidos que só
    mudam acento ou maiúscula cairiam na mesma pasta (ou em duas que se sobrescrevem
    ao extrair no Windows/Mac) — a segunda ganha o CNPJ no nome."""
    pastas: dict[UUID, str] = {}
    usados: set[str] = set()  # em minúsculas
    for it in lote.prontos:
        p = it.compra
        assert p is not None
        if p.company_id in pastas:
            continue
        cnpj = re.sub(r"\D", "", str((p.comprador or {}).get("cnpj") or ""))
        nome = slug_arquivo(_quem_compra(p)) or cnpj or "compradora"
        if nome.lower() in usados and cnpj and not nome.endswith(cnpj):
            nome = f"{nome}_{cnpj}"
        base, n = nome, 1
        while nome.lower() in usados:
            n += 1
            nome = f"{base}_{n}"
        usados.add(nome.lower())
        pastas[p.company_id] = nome
    return pastas


def _unico(caminho: str, usados: set[str]) -> str:
    """Nome repetido (o nº da NF é POR VENDEDORA: BEZERRA nº 3031 e TELES nº 3031)
    ganha _2, _3… antes da extensão, sem olhar maiúsculas."""
    base, ponto, ext = caminho.rpartition(".")
    n, final = 1, caminho
    while final.lower() in usados:
        n += 1
        final = f"{base}_{n}{ponto}{ext}"
    usados.add(final.lower())
    return final


def texto_faltaram(lote: Lote) -> str:
    """O `_FALTARAM.txt`: nº da compra, quem compra → de quem, a NF e o motivo."""
    falta = lote.faltaram
    linhas = [f"Compras que ficaram de fora ({len(falta)} de {len(lote.itens)}):", ""]
    for it in falta:
        p = it.compra
        if p is None:
            linhas.append(f"- compra {it.id}: {it.motivo}")
            continue
        nf = f" · NF {p.nf_numero}" if p.nf_numero else ""
        linhas.append(
            f"- compra nº {p.numero} · {_quem_compra(p) or 'compradora'} → {p.vendedor_nome}"
            f"{nf}: {it.motivo}"
        )
    return "\n".join(linhas) + "\n"


def montar_zip(lote: Lote) -> bytes:
    buf = io.BytesIO()
    usados: set[str] = set()
    pastas = _pastas(lote)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for it in lote.prontos:
            p = it.compra
            assert p is not None and it.conteudo is not None
            nome = nome_pdf(p) if lote.tipo == "pdf" else nome_xml(p)
            z.writestr(_unico(f"{pastas[p.company_id]}/{nome}", usados), it.conteudo)
        if lote.faltaram:
            z.writestr("_FALTARAM.txt", texto_faltaram(lote))
    return buf.getvalue()


# --- o PDF juntado (imprimir) ---------------------------------------------------------


def _juntar(lote: Lote) -> bytes:
    saida = fitz.open()
    try:
        for it in lote.prontos:
            with fitz.open(stream=it.conteudo, filetype="pdf") as doc:
                saida.insert_pdf(doc)
        # garbage=1, NUNCA 3/4: a deduplicação do garbage>=3 é quadrática no nº de
        # páginas e segura o GIL (o to_thread não salva) — 100 DANFEs paravam a API
        # (1 processo) por ~20 s. Com 1: ~0,4 s e o arquivo só 0,6% maior.
        return saida.tobytes(garbage=1, deflate=True)
    finally:
        saida.close()


async def juntar_pdfs(lote: Lote) -> bytes:
    """Um PDF só, NA ORDEM dos ids (as DANFEs desenhadas aqui: não há PDF de fora
    que possa vir corrompido)."""
    return await asyncio.to_thread(_juntar, lote)
