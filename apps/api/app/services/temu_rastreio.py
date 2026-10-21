"""Rastreio físico dos pedidos Temu — "postado de verdade" (Vinicius, 21/10/2026).

Por que existe: a Temu marca o pedido "Enviado" quando a etiqueta é COMPRADA
(não quando a transportadora pega o pacote), e o Bling recebe o pedido Temu com
Tracking "N/A". Nenhum dos dois diz se o pacote saiu — o pedido fica vermelho
no Controle de Estoque mesmo postado.

O código da transportadora só existe na etiqueta que o DaVinci já guarda
(`nf_etiqueta_arquivo`, PDF da Temu com fonte CID — grep cru não acha, o
PyMuPDF decodifica):
  * AnjunExpress — `AJ` + 15 dígitos (ex. AJ261021123794101);
  * J&T Express  — `888` + 12 dígitos (ex. 888002704060957).

O 17track rastreia as duas (catálogo `apicarrier.all.json`):
  * Anjunexpress (BR), carrier 191915 — só com o código;
  * J&T Express (BR), carrier 100797 — exige o CPF/CNPJ do destinatário
    (`cpf_or_cnpj`, campo do /register da v2.4). Mandar o CPF do comprador ao
    17track foi decisão do Vinicius (21/10), ciente da regra de dados da Temu e
    da LGPD. O CPF sai de `bling_orders.documento_destinatario` na hora e nunca
    volta em resposta nem vai pro log.

Fase 1 (este módulo): `sondar` — registra e lê no 17track, devolve o que ele
respondeu. Não mexe em pedido nenhum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import fitz  # PyMuPDF
import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, NfEtiquetaArquivo
from app.services import logistica_track

logger = structlog.get_logger()

# A v2.4 é a que documenta o `cpf_or_cnpj` no /register.
BASE_V24 = "https://api.17track.net/track/v2.4"

ANJUN_CARRIER = 191915
JT_CARRIER = 100797

# Fronteiras nos dois lados: a etiqueta tem PK-<19 dígitos>, PO-029-<17> e a
# NF junta pode ter a chave de 44 dígitos — nenhum pedaço deles pode casar.
_RE_ANJUN = re.compile(r"(?<![A-Z0-9])AJ\d{15}(?!\d)")
_RE_JT = re.compile(r"(?<!\d)888\d{12}(?!\d)")

# Erros do /register que importam aqui (os demais voltam como número).
_ERRO_JA_REGISTRADO = logistica_track.ERRO_JA_REGISTRADO
_ERRO_SEM_QUOTA = logistica_track.ERRO_SEM_QUOTA

_MAX_EVENTOS = 8


@dataclass(frozen=True)
class CodigoEtiqueta:
    codigo: str
    carrier: int
    transportadora: str  # "Anjun" | "J&T"


def codigo_da_etiqueta(pdf: bytes) -> CodigoEtiqueta | None:
    """Código de rastreio impresso na etiqueta Temu; None se não achou ou se o
    PDF não abre. Anjun primeiro: é a transportadora que a Temu usa desde
    16/10 (a Barbosa era J&T até 13/10)."""
    try:
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            texto = "\n".join(p.get_text() for p in doc)
    except Exception:  # noqa: BLE001 — PDF corrompido vale "sem código"
        return None
    if m := _RE_ANJUN.search(texto):
        return CodigoEtiqueta(m.group(0), ANJUN_CARRIER, "Anjun")
    if m := _RE_JT.search(texto):
        return CodigoEtiqueta(m.group(0), JT_CARRIER, "J&T")
    return None


def _so_digitos(doc: str | None) -> str | None:
    d = re.sub(r"\D", "", doc or "")
    return d if len(d) in (11, 14) else None


async def _documento_do_pedido(session: AsyncSession, pedido: str) -> str | None:
    docs = (
        await session.execute(
            select(BlingOrder.documento_destinatario).where(
                BlingOrder.numero == pedido,
                BlingOrder.documento_destinatario.isnot(None),
            )
        )
    ).scalars()
    return next((d for d in map(_so_digitos, docs) if d), None)


def _registro(code: int | None) -> str:
    if code is None:
        return "aceito"
    if code == _ERRO_JA_REGISTRADO:
        return "ja_registrado"
    if code == _ERRO_SEM_QUOTA:
        return "sem_quota"
    return f"recusado:{code}"


async def sondar(session: AsyncSession, pedidos: list[str]) -> dict[str, Any]:
    """Pra cada pedido Bling: acha o código na etiqueta, registra no 17track
    (1 crédito na 1ª vez; repetir não cobra) e lê o que ele já tem. Pode rodar
    de novo depois de uns minutos — o 17track busca na transportadora por conta
    própria e o 1º evento costuma aparecer horas depois da coleta."""
    itens: list[dict[str, Any]] = []
    registrar: list[dict[str, Any]] = []
    por_codigo: dict[str, dict[str, Any]] = {}
    for pedido in dict.fromkeys(p.strip() for p in pedidos if p and p.strip()):
        item: dict[str, Any] = {"pedido": pedido}
        itens.append(item)
        blob = (
            await session.execute(
                select(NfEtiquetaArquivo.blob).where(NfEtiquetaArquivo.pedido_bling == pedido)
            )
        ).scalar_one_or_none()
        if not blob:
            item["erro"] = "sem_etiqueta"
            continue
        cod = codigo_da_etiqueta(blob)
        if cod is None:
            item["erro"] = "codigo_nao_achado_na_etiqueta"
            continue
        item.update(codigo=cod.codigo, transportadora=cod.transportadora, carrier=cod.carrier)
        linha: dict[str, Any] = {"number": cod.codigo, "carrier": cod.carrier}
        if cod.carrier == JT_CARRIER:
            doc = await _documento_do_pedido(session, pedido)
            if doc is None:
                item["erro"] = "jt_sem_cpf_no_pedido"
                continue
            linha["cpf_or_cnpj"] = doc
        item["cpf_enviado"] = "cpf_or_cnpj" in linha
        registrar.append(linha)
        por_codigo[cod.codigo] = item

    saida: dict[str, Any] = {"pedidos": itens}
    if not registrar:
        return saida
    try:
        async with httpx.AsyncClient(timeout=40.0) as c:
            reg = await logistica_track._chamar(c, "register", registrar, base=BASE_V24)
            aceitos, recusados = logistica_track._aceitos_e_recusados(reg)
            for n in aceitos:
                if n in por_codigo:
                    por_codigo[n]["registro"] = _registro(None)
            for n, code in recusados.items():
                if n in por_codigo:
                    por_codigo[n]["registro"] = _registro(code)
            info = await logistica_track._chamar(
                c,
                "gettrackinfo",
                [{"number": li["number"], "carrier": li["carrier"]} for li in registrar],
                base=BASE_V24,
            )
            for it in (info.get("data") or {}).get("accepted") or []:
                item = por_codigo.get(str((it or {}).get("number") or "").strip())
                if item is None:
                    continue
                ti = it.get("track_info") or {}
                ult = ti.get("latest_status") or {}
                item["status"] = ult.get("status")
                item["sub_status"] = ult.get("sub_status")
                item["eventos"] = [
                    {"quando": e.quando.isoformat(), "descricao": e.descricao, "local": e.local}
                    for e in logistica_track.eventos_de_track_info(ti)[:_MAX_EVENTOS]
                ]
    except logistica_track.Track17Error as e:
        saida["erro_17track"] = str(e)[:300]
    saida["quota_restante"] = await logistica_track.quota_restante()
    logger.info(
        "temu_rastreio_sonda",
        pedidos=len(itens),
        registrados=len(registrar),
        com_evento=sum(1 for i in itens if i.get("eventos")),
    )
    return saida
