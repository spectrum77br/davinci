"""Leitura do XML da NF-e autorizada (camada PURA — não toca banco nem rede).

O robô da nuvem baixa, pra cada nota emitida, o XML assinado que a SEFAZ
devolveu (`<nfeProc>` = a nota `<NFe>` + o protocolo `<protNFe>`). Aqui esse
arquivo vira campos: chave, número, valor, emitente, destinatário, protocolo.

O que o XML NÃO traz é o número do pedido no Bling: o `<xPed>` guarda o número
INTERNO do Upseller (ex. "UP2NYY224934"). Por isso o casamento com o pedido vai
pelo CPF/CNPJ do destinatário (+ nome, se houver mais de um candidato) — a
mesma técnica do lote de etiquetas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}


class NfXmlError(ValueError):
    """XML que não dá pra ler como NF-e (código em `.code`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NotaXml:
    chave: str
    numero: str
    serie: str | None
    emitente_cnpj: str | None
    emitente_nome: str | None
    destinatario_doc: str | None
    destinatario_nome: str | None
    valor: Decimal | None
    data_emissao: datetime | None
    protocolo: str | None
    situacao: str | None
    situacao_motivo: str | None
    upseller_pedido: str | None


def _txt(node, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path, NS)
    if found is None or found.text is None:
        return None
    v = found.text.strip()
    return v or None


def _digitos(v: str | None) -> str | None:
    if not v:
        return None
    d = re.sub(r"\D", "", v)
    return d or None


def parse_nfe(raw: bytes) -> NotaXml:
    """Lê o XML da nota. Aceita tanto o `<nfeProc>` (nota + protocolo) quanto a
    `<NFe>` solta (sem protocolo — nota ainda não transmitida)."""
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise NfXmlError("nf_xml_invalido") from exc

    inf = root.find(".//n:infNFe", NS)
    if inf is None:
        raise NfXmlError("nf_xml_sem_nfe")

    prot = root.find(".//n:protNFe/n:infProt", NS)

    # A chave está no atributo Id do infNFe como "NFe<44 dígitos>"; o protocolo
    # repete em chNFe. Fica o que der, priorizando o Id.
    chave = _digitos(inf.get("Id")) or _digitos(_txt(prot, "n:chNFe"))
    if not chave or len(chave) != 44:
        raise NfXmlError("nf_xml_sem_chave")

    ide = inf.find("n:ide", NS)
    numero = _txt(ide, "n:nNF")
    if not numero:
        raise NfXmlError("nf_xml_sem_numero")

    emit = inf.find("n:emit", NS)
    dest = inf.find("n:dest", NS)
    # Pessoa física vem em <CPF>, jurídica em <CNPJ> — só um dos dois existe.
    doc = _txt(dest, "n:CPF") or _txt(dest, "n:CNPJ")

    return NotaXml(
        chave=chave,
        numero=numero,
        serie=_txt(ide, "n:serie"),
        emitente_cnpj=_digitos(_txt(emit, "n:CNPJ")),
        emitente_nome=_txt(emit, "n:xNome"),
        destinatario_doc=_digitos(doc),
        destinatario_nome=_txt(dest, "n:xNome"),
        valor=_valor(inf),
        data_emissao=_data(_txt(ide, "n:dhEmi")),
        protocolo=_txt(prot, "n:nProt"),
        situacao=_txt(prot, "n:cStat"),
        situacao_motivo=_txt(prot, "n:xMotivo"),
        # <xPed> do 1º item = nº do pedido no Upseller (não serve pro Bling).
        upseller_pedido=_txt(inf, "n:det/n:prod/n:xPed"),
    )


def _valor(inf) -> Decimal | None:
    v = _txt(inf, "n:total/n:ICMSTot/n:vNF")
    if not v:
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _data(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None
