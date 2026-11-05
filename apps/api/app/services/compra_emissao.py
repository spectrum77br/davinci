"""Contabilidade › Compra — emissão AUTOMÁTICA da NF pelo robô do Mac (04/11/2026).

O "Emitir" da tela põe a compra na fila (`na_fila`); o robô do perfil da
vendedora (~/nf-executor/compra_emitir.py, hoje só o Bling 44 = k13ksbpr) pega
uma por vez (`emitindo`), importa a planilha, gera a NF, acerta NCM e IE, manda à
SEFAZ UMA vez, exporta o XML e devolve. A cada passo ele grava a etapa aqui
(`CompraPedido.emissao_etapa`); antes do import e antes da SEFAZ ele só segue se
o DaVinci responder 200 — 409 = alguém tirou a compra do robô.

Regras deste arquivo (as rotas estão em `routers/nfse_compras.py`):
- `problemas_para_emitir`: o que impede a compra de ir para o robô (v1: vendedora
  automática, 1 item só, CFOP igual ao que o Bling gera, IE do comprador em
  números, quantidade dentro do %).
- `payload_agente`: o que o robô recebe (planilha + o que conferir no XML).
- `conferir_xml`: a MESMA régua do robô, rodada de novo no servidor antes de
  marcar `nf_emitida` (o robô não confia em si mesmo).
- `gravar_nf_nota`: o XML vai para `nf_nota` SEM o casamento por CNPJ do
  `/agent/nf-xml` (que acharia um pedido de venda da VITA, não a compra).
"""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from xml.etree import ElementTree

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compra import CompraPedido, CompraPedidoItem, CompraVendedora
from app.models.nf import NfNota
from app.services import compra as svc
from app.services.nf_xml import NS, NotaXml, parse_nfe

# Horário em que o lease entrega compra (BRT). Fora dele o robô recebe a fila
# vazia: o lote das 22:30 usa o MESMO perfil (k13ksbpr) e a compra não tem pressa.
JANELA = (time(8, 0), time(19, 30))
# Retentável antes de queimar o número: volta para a fila até 3 tentativas.
MAX_TENTATIVAS = 3
# v1: só o perfil do Bling 44 tem robô (~/nf-executor/compra_emitir.py — o conta_certa
# dele só conhece a BEZERRA). Vendedora automática noutro perfil ficaria na fila para sempre.
SERIAIS_COM_ROBO = ("k13ksbpr",)
_DS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}
_CENTAVO = Decimal("0.01")


def agora_sp() -> datetime:
    return datetime.now(svc.SP)


def janela_aberta(agora: datetime | None = None) -> bool:
    t = (agora or agora_sp()).astimezone(svc.SP).time()
    return JANELA[0] <= t < JANELA[1]


def _digitos(v: object) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _sem_zeros(v: object) -> str:
    return _digitos(v).lstrip("0") or "0"


def _dinheiro(v: Decimal) -> str:
    return str(Decimal(v).quantize(_CENTAVO, rounding=ROUND_HALF_UP))


# --- pode ir para o robô? ------------------------------------------------------------------


def problemas_para_emitir(
    p: CompraPedido, itens: list[CompraPedidoItem], v: CompraVendedora | None
) -> list[str]:
    """O que impede a compra de ir para o robô (lista vazia = pode). Não olha o
    status (isso é da rota): só o que o robô não sabe resolver sozinho."""
    out: list[str] = []
    if v is None or not v.automatico or not v.ativo or not v.adspower_serial:
        out.append(
            f"A vendedora {p.vendedor_nome} não emite automático: baixe a planilha e "
            "importe no Bling dela."
        )
    elif v.adspower_serial not in SERIAIS_COM_ROBO:
        out.append(
            f"O perfil {v.adspower_serial} de {p.vendedor_nome} ainda não tem robô (hoje só o "
            "Bling 44): baixe a planilha e importe no Bling dela."
        )
    if len(itens) != 1:
        out.append(
            f"O robô emite compra de 1 produto só (essa tem {len(itens)}): "
            "use a planilha ou registre uma compra por produto."
        )
    c = p.comprador or {}
    uf_comprador = (c.get("endereco") or {}).get("uf")
    sugerido = svc.cfop_sugerido(p.vendedor_uf, uf_comprador)
    if p.cfop != sugerido:
        # o CSV não leva CFOP: no Bling sai o da natureza de operação (5102/6102 pela UF)
        out.append(
            f"CFOP {p.cfop} diferente do que o Bling gera ({sugerido}, pela UF): "
            "a planilha não leva CFOP. Edite a compra ou emita pela planilha."
        )
    if len(_digitos(c.get("cnpj"))) != 14:
        out.append("O comprador precisa ter CNPJ (14 números).")
    ie = c.get("inscricao_estadual")
    if not ie or not str(ie).isdigit():
        # o robô acerta a IE no contato do Bling antes da SEFAZ (sem ela: rejeição 232)
        out.append(
            "A inscrição estadual do comprador precisa estar preenchida (só números): "
            "edite a compra."
        )
    if p.modo == "percentual" and len(itens) == 1:
        it = itens[0]
        limite = (
            svc.quantidade_pelo_percentual(p.faturamento, p.percentual, it.valor_unitario)
            if p.faturamento is not None and p.percentual is not None
            else 0
        )
        if it.quantidade > limite:
            out.append(
                f"A quantidade ({it.quantidade}) passa de {p.percentual}% do faturamento "
                f"(cabem {limite}): edite a compra."
            )
    return out


# --- o que o robô recebe ---------------------------------------------------------------------


async def ultima_nf(session: AsyncSession, cnpj: str) -> int | None:
    """Maior nº de NF da vendedora que o DaVinci conhece (o robô confere que o
    Bling dela está pelo menos aí antes de importar)."""
    return (
        await session.execute(
            select(func.max(cast(NfNota.numero, BigInteger))).where(
                NfNota.emitente_cnpj == cnpj, NfNota.numero.op("~")("^[0-9]+$")
            )
        )
    ).scalar_one_or_none()


async def problemas_da_compra(
    session: AsyncSession,
    p: CompraPedido,
    itens: list[CompraPedidoItem],
    v: CompraVendedora | None,
) -> list[str]:
    """`problemas_para_emitir` + o que depende do banco: sem nenhuma NF da vendedora em
    `nf_nota` o robô não confere se a lista do Bling veio inteira — e recusa a compra."""
    out = problemas_para_emitir(p, itens, v)
    if await ultima_nf(session, p.vendedor_cnpj) is None:
        out.append(
            f"O DaVinci não tem nenhuma NF de {p.vendedor_nome} (Notas fiscais): o robô não "
            "confere o Bling dela sem a última NF. Baixe a planilha e emita à mão."
        )
    return out


async def payload_agente(
    session: AsyncSession, p: CompraPedido, itens: list[CompraPedidoItem]
) -> dict:
    """A compra como o robô recebe no lease (e na prévia do DRY-RUN)."""
    v = await session.get(CompraVendedora, p.vendedor_cnpj)
    planilha = svc.planilha([p], {p.id: itens})
    c = p.comprador or {}
    end = c.get("endereco") or {}
    return {
        "id": str(p.id),
        "numero": p.numero,
        "status": p.status,
        "etapa": p.emissao_etapa,
        "tentativas": p.emissao_tentativas or 0,
        "nome_arquivo": svc.nome_da_planilha([p]),
        "planilha_b64": base64.b64encode(planilha).decode(),
        "planilha_sha256": hashlib.sha256(planilha).hexdigest(),
        "vendedor": {
            "cnpj": p.vendedor_cnpj,
            "nome": p.vendedor_nome,
            "uf": p.vendedor_uf,
            "serial": v.adspower_serial if v is not None else None,
        },
        "comprador": {
            "razao_social": c.get("razao_social"),
            "cnpj": c.get("cnpj"),
            "ie": c.get("inscricao_estadual"),
            "uf": end.get("uf"),
            "municipio": end.get("municipio"),
        },
        "cfop": p.cfop,
        "total": _dinheiro(p.total),
        "itens": [
            {
                "sku": i.sku,
                "nome": i.nome,
                "ncm": i.ncm,
                "unidade": i.unidade,
                "quantidade": i.quantidade,
                "valor_unitario": _dinheiro(i.valor_unitario),
                "valor_total": _dinheiro(i.valor_total),
            }
            for i in itens
        ],
        "ultima_nf_vendedora": await ultima_nf(session, p.vendedor_cnpj),
    }


# --- conferência do XML ----------------------------------------------------------------------


def _texto(node: ElementTree.Element | None, path: str, ns: dict | None = None) -> str | None:
    if node is None:
        return None
    achado = node.find(path, ns or NS)
    if achado is None or achado.text is None:
        return None
    return achado.text.strip() or None


def _decimal(v: str | None) -> Decimal | None:
    try:
        return Decimal(v) if v else None
    except InvalidOperation:
        return None


def conferir_xml(
    raw: bytes,
    p: CompraPedido,
    itens: list[CompraPedidoItem],
    *,
    nf_numero: str,
    nf_chave: str,
) -> tuple[NotaXml, list[str]]:
    """Confere o XML autorizado contra a compra. Devolve a nota lida e a lista do
    que não bate (vazia = confere). XML que nem lê como NF-e levanta `NfXmlError`.

    Régua (a mesma do robô): cStat 100; chave e nº iguais aos informados; emitente
    = vendedora; destinatário = CNPJ e IE do comprador; vNF = total; item a item
    (na ordem) SKU, NCM, CFOP, quantidade e unitário; DigestValue da assinatura =
    digVal do protocolo (XML de NF reenviada à SEFAZ não bate — memória 19/10)."""
    nota = parse_nfe(raw)
    raiz = ElementTree.fromstring(raw)  # noqa: S314 — XML do nosso robô (token), só leitura
    inf = raiz.find(".//n:infNFe", NS)
    prot = raiz.find(".//n:protNFe/n:infProt", NS)
    c = p.comprador or {}
    erros: list[str] = []

    def difere(rotulo: str, veio: object, esperado: object) -> None:
        erros.append(f"{rotulo} {veio if veio not in (None, '') else '(vazio)'}≠{esperado}")

    if nota.situacao != "100":
        difere("cStat", nota.situacao, "100")
    if nota.chave != _digitos(nf_chave):
        difere("chave", nota.chave, _digitos(nf_chave))
    ch_prot = _texto(prot, "n:chNFe")
    if ch_prot and _digitos(ch_prot) != nota.chave:
        difere("chNFe do protocolo", ch_prot, nota.chave)
    if _sem_zeros(nota.numero) != _sem_zeros(nf_numero):
        difere("nNF", nota.numero, _sem_zeros(nf_numero))
    if nota.emitente_cnpj != p.vendedor_cnpj:
        difere("emitente", nota.emitente_cnpj, p.vendedor_cnpj)
    if nota.destinatario_doc != _digitos(c.get("cnpj")):
        difere("destinatário", nota.destinatario_doc, _digitos(c.get("cnpj")))
    ie_xml = _digitos(_texto(inf.find("n:dest", NS) if inf is not None else None, "n:IE"))
    if ie_xml != _digitos(c.get("inscricao_estadual")):
        difere("IE do destinatário", ie_xml, _digitos(c.get("inscricao_estadual")))
    if nota.valor is None or nota.valor.quantize(_CENTAVO) != Decimal(p.total).quantize(_CENTAVO):
        difere("vNF", nota.valor, _dinheiro(p.total))

    dets = inf.findall("n:det", NS) if inf is not None else []
    if len(dets) != len(itens):
        difere("itens", len(dets), len(itens))
    for n, (det, it) in enumerate(zip(dets, itens, strict=False), start=1):
        prod = det.find("n:prod", NS)
        cprod = _texto(prod, "n:cProd") or ""
        if cprod.lower() != it.sku.lower():
            difere(f"item {n} cProd", cprod, it.sku)
        ncm = _digitos(_texto(prod, "n:NCM"))
        if ncm != it.ncm:
            difere(f"item {n} NCM", ncm, it.ncm)
        cfop = _texto(prod, "n:CFOP")
        if cfop != p.cfop:
            difere(f"item {n} CFOP", cfop, p.cfop)
        qcom = _decimal(_texto(prod, "n:qCom"))
        if qcom is None or qcom != Decimal(it.quantidade):
            difere(f"item {n} qCom", _texto(prod, "n:qCom"), it.quantidade)
        vun = _decimal(_texto(prod, "n:vUnCom"))
        if vun is None or vun.quantize(_CENTAVO, rounding=ROUND_HALF_UP) != it.valor_unitario:
            difere(f"item {n} vUnCom", _texto(prod, "n:vUnCom"), _dinheiro(it.valor_unitario))

    digest, dig_val = _digests(raiz)
    if not digest or not dig_val:
        erros.append("XML sem DigestValue ou sem digVal (não é o XML autorizado)")
    elif digest != dig_val:
        erros.append(f"DigestValue {digest}≠digVal {dig_val} (XML diferente do autorizado)")
    return nota, erros


def _digests(raiz: ElementTree.Element) -> tuple[str | None, str | None]:
    """(DigestValue da assinatura, digVal do protocolo de autorização)."""
    prot = raiz.find(".//n:protNFe/n:infProt", NS)
    return _texto(raiz, ".//ds:DigestValue", _DS), _texto(prot, "n:digVal")


def xml_autorizado(raw: bytes) -> bool:
    """O arquivo é o XML que a SEFAZ autorizou (DigestValue = digVal)? XML de NF
    reenviada depois de autorizada sai com outro digest (memória 19/10)."""
    try:
        digest, dig_val = _digests(ElementTree.fromstring(raw))  # noqa: S314 — só leitura
    except ElementTree.ParseError:
        return False
    return bool(digest) and digest == dig_val


def ler_xml_b64(xml_b64: str) -> bytes:
    """O XML que o robô manda em base64 (ValueError se não decodifica ou vier vazio)."""
    raw = base64.b64decode(xml_b64, validate=True)
    if not raw:
        raise ValueError("vazio")
    return raw


async def gravar_nf_nota(session: AsyncSession, nota: NotaXml, raw: bytes) -> None:
    """Grava (ou atualiza, pela chave) a nota em `nf_nota` — os mesmos campos do
    `/agent/nf-xml` (routers/nf.py), mas SEM `_casar_pedido_da_nota`: a compra não é
    pedido de venda, e casar pelo CNPJ do destinatário pegaria um pedido da VITA.
    `pedido_bling` fica como está (nulo numa nota nova)."""
    row = (
        await session.execute(select(NfNota).where(NfNota.chave == nota.chave))
    ).scalar_one_or_none()
    if row is None:
        row = NfNota(chave=nota.chave, numero=nota.numero, xml=raw)
        session.add(row)
    row.numero = nota.numero
    row.serie = nota.serie
    row.emitente_cnpj = nota.emitente_cnpj
    row.emitente_nome = nota.emitente_nome
    row.destinatario_doc = nota.destinatario_doc
    row.destinatario_nome = nota.destinatario_nome
    row.valor = nota.valor
    row.data_emissao = nota.data_emissao
    row.protocolo = nota.protocolo
    row.situacao = nota.situacao
    row.situacao_motivo = nota.situacao_motivo
    row.upseller_pedido = nota.upseller_pedido
    row.xml = raw
