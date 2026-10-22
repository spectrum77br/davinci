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

Fase 1: `sondar` — registra e lê no 17track, devolve o que ele respondeu. Não
mexe em pedido nenhum.

Fase 2: `rodada` (cron a cada 15 min, só automático — Vinicius 21/10, sem
botão): pedidos Temu ainda vermelhos → código da etiqueta → 17track; o que já
tem evento de transportadora vai pra 15 no Bling (mesmas travas da Magalu,
`magalu_shipment_sync`) com a hora do 1º evento como hora do envio. O CPF da
J&T só vai no /register, uma vez; a leitura (`gettrackinfo`) é de graça.

Fase 3 (Vinicius, 22/10 — o 305438 coletado às 11:09 seguia "não encontrado"
no 17track às 15h): o 17track pergunta à transportadora no cadastro e depois
só a cada 6–12 h. Cadastrado logo na etiqueta, antes da coleta, a única
consulta rápida voltava vazia. Agora:
  * cadastro só 3 h depois da etiqueta;
  * se ainda não saiu, consulta na hora no modo econômico
    (`getRealTimeTrackInfo` sem `cacheLevel` = 1 crédito; devolve o que o
    17track buscou na transportadora nas últimas 3 h) de 3 em 3 h, das 8h às
    20h de Brasília, até 10 por código. Só Anjun: a J&T pediria o CPF de novo.
  * coletado → fica verde e sai da lista; as consultas param sozinhas.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fitz  # PyMuPDF
import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingEnvioEvento, BlingOrder, NfEtiquetaArquivo
from app.redis_client import redis
from app.services import logistica_track
from app.services.bling_situacoes import SITUACOES_ENVIADO_ETIQUETA_STR
from app.services.margem_audit import record_margem_audit

logger = structlog.get_logger()
_BRT = ZoneInfo("America/Sao_Paulo")

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


async def _preparar(
    session: AsyncSession, pedido: str
) -> tuple[CodigoEtiqueta | None, dict[str, Any] | None, str | None]:
    """(código, linha do /register, erro). A linha só leva o CPF/CNPJ na J&T."""
    blob = (
        await session.execute(
            select(NfEtiquetaArquivo.blob).where(NfEtiquetaArquivo.pedido_bling == pedido)
        )
    ).scalar_one_or_none()
    if not blob:
        return None, None, "sem_etiqueta"
    cod = codigo_da_etiqueta(blob)
    if cod is None:
        return None, None, "codigo_nao_achado_na_etiqueta"
    linha: dict[str, Any] = {"number": cod.codigo, "carrier": cod.carrier}
    if cod.carrier == JT_CARRIER:
        doc = await _documento_do_pedido(session, pedido)
        if doc is None:
            return cod, None, "jt_sem_cpf_no_pedido"
        linha["cpf_or_cnpj"] = doc
    return cod, linha, None


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
        cod, linha, erro = await _preparar(session, pedido)
        if cod is not None:
            item.update(codigo=cod.codigo, transportadora=cod.transportadora, carrier=cod.carrier)
        if linha is None:
            item["erro"] = erro
            continue
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


# ── Fase 2: rodada automática ──────────────────────────────────────────────

# "Ainda não saiu": 21 (Em digitação = etiqueta enviada), 83965 (legado) e 6
# (Em aberto com etiqueta — o Bling anda na frente do sync). Mesmo conjunto
# da Magalu.
_ABERTAS = (*SITUACOES_ENVIADO_ETIQUETA_STR, "6")
_JANELA = timedelta(days=14)  # o mesmo anti-zumbi da aba Pedidos
_RE_PO_TEMU = re.compile(r"PO-\d{3}-\d{10,}")
# latest_status do 17track que provam que a transportadora já pegou o pacote.
# NotFound, InfoReceived (só o aviso da etiqueta) e Expired não provam nada.
_SAIU = frozenset(
    {
        "InTransit",
        "AvailableForPickup",
        "OutForDelivery",
        "DeliveryFailure",
        "Delivered",
        "Exception",
    }
)
_LOTE = 40  # máximo do /register e do /gettrackinfo
_LOCK_RODADA = 0x74656D75  # ascii "temu"
_ERROS_BLING = frozenset({"bling_nao_confere", "bling_nao_confirmou", "bling_falhou"})

# Fase 3 — quando perguntar ao 17track (ver docstring do módulo).
_ESPERA_CADASTRO = timedelta(hours=3)  # da etiqueta ao cadastro
_INTERVALO_CONSULTA = timedelta(hours=3)  # o econômico reaproveita até 3 h
_HORARIO_CONSULTA = (8, 20)  # Brasília: das 8h às 19h59
_MAX_CONSULTAS = 10  # pagas, por código
# Redis: {"n": consultas pagas, "ultima": quando o 17track foi à transportadora
# por nossa conta (cadastro ou consulta)}. Some depois da janela de 14 dias.
_CHAVE_CONSULTA = "temu17:consulta:"
_TTL_CONSULTA = 15 * 24 * 3600


def _agora() -> datetime:
    return datetime.now(UTC)


async def _ler_consultas(codigos: list[str]) -> dict[str, dict[str, Any]] | None:
    """{código: {"n", "ultima"}} só dos que já têm registro. None se o Redis
    falhou — aí a rodada não paga consulta nenhuma (sem contador, sem limite)."""
    if not codigos:
        return {}
    try:
        valores = await redis.mget([f"{_CHAVE_CONSULTA}{c}" for c in codigos])
    except Exception as e:  # noqa: BLE001 — Redis fora não derruba a rodada
        logger.warning("temu_rastreio_redis_falhou", err=str(e)[:200])
        return None
    saida: dict[str, dict[str, Any]] = {}
    for codigo, valor in zip(codigos, valores, strict=True):
        if not valor:
            continue
        try:
            d = json.loads(valor)
            ultima = datetime.fromisoformat(d["ultima"]) if d.get("ultima") else None
            saida[codigo] = {"n": int(d.get("n") or 0), "ultima": ultima}
        except (ValueError, TypeError, KeyError):
            # Registro ilegível conta como teto atingido: não gasta às cegas.
            saida[codigo] = {"n": _MAX_CONSULTAS, "ultima": None}
    return saida


async def _anotar_consulta(codigo: str, n: int, quando: datetime) -> None:
    try:
        await redis.set(
            f"{_CHAVE_CONSULTA}{codigo}",
            json.dumps({"n": n, "ultima": quando.isoformat()}),
            ex=_TTL_CONSULTA,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("temu_rastreio_redis_falhou", err=str(e)[:200])


def _sync_17track(track_info: dict) -> datetime | None:
    """Última vez que o 17track foi à transportadora (`latest_sync_time`), nos
    dois lugares onde os `providers` aparecem."""
    vezes: list[datetime] = []
    for p in logistica_track._providers(track_info):
        try:
            dt = datetime.fromisoformat(str(p.get("latest_sync_time") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        vezes.append(dt if dt.tzinfo else dt.replace(tzinfo=UTC))
    return max(vezes, default=None)


def _motivo_sem_consulta(
    agora: datetime, anotado: dict[str, Any] | None, sync_17track: datetime | None
) -> str | None:
    """None = pode pagar a consulta agora; senão, o motivo de esperar."""
    if not (_HORARIO_CONSULTA[0] <= agora.astimezone(_BRT).hour < _HORARIO_CONSULTA[1]):
        return "fora_do_horario"
    anotado = anotado or {"n": 0, "ultima": None}
    if anotado["n"] >= _MAX_CONSULTAS:
        return "limite"
    # A última ida à transportadora: a nossa (cadastro/consulta) ou a do
    # próprio 17track (6–12 h). Menos de 3 h = o econômico devolveria o mesmo.
    ultimas = [t for t in (anotado["ultima"], sync_17track) if t is not None]
    if ultimas and agora - max(ultimas) < _INTERVALO_CONSULTA:
        return "aguardando"
    return None


async def _consultar_na_hora(c: httpx.AsyncClient, linha: dict[str, Any]) -> dict | None:
    """Consulta na hora, modo econômico (1 crédito). Sem `cacheLevel`: a doc
    da v2.4 diz 0 na tabela e "Standard" no texto — o padrão é o econômico."""
    body = await logistica_track._chamar(
        c,
        "getRealTimeTrackInfo",
        [{"number": linha["number"], "carrier": linha["carrier"]}],
        base=BASE_V24,
    )
    for it in (body.get("data") or {}).get("accepted") or []:
        if str((it or {}).get("number") or "").strip() == linha["number"]:
            return it.get("track_info") or {}
    return None


async def _candidatos(session: AsyncSession) -> list[BlingOrder]:
    corte = datetime.now(UTC) - _JANELA
    rows = (
        await session.execute(
            select(BlingOrder)
            .where(
                BlingOrder.numeroloja.like("PO-%"),
                BlingOrder.situacao.in_(_ABERTAS),
                BlingOrder.item_index == 0,
                BlingOrder.bling_id.isnot(None),
                BlingOrder.created_at >= corte,
            )
            .limit(500)
        )
    ).scalars()
    return [o for o in rows if _RE_PO_TEMU.fullmatch(o.numeroloja or "")]


async def _ler(
    c: httpx.AsyncClient, linhas: list[dict[str, Any]]
) -> tuple[dict[str, dict], list[str]]:
    """({código: track_info} dos que o 17track conhece, [códigos que ele não
    conhece]). Leitura de graça — sem CPF."""
    lidos: dict[str, dict] = {}
    nao_registrados: list[str] = []
    for i in range(0, len(linhas), _LOTE):
        if i:
            await asyncio.sleep(logistica_track._PAUSA_ENTRE_LOTES)
        lote = [{"number": li["number"], "carrier": li["carrier"]} for li in linhas[i : i + _LOTE]]
        body = await logistica_track._chamar(c, "gettrackinfo", lote, base=BASE_V24)
        data = body.get("data") or {}
        for it in data.get("accepted") or []:
            n = str((it or {}).get("number") or "").strip()
            if n:
                lidos[n] = it.get("track_info") or {}
        for it in data.get("rejected") or []:
            if logistica_track._erro_code(it) == logistica_track.ERRO_NAO_REGISTRADO:
                nao_registrados.append(str(it.get("number") or "").strip())
    return lidos, nao_registrados


async def _registrar(c: httpx.AsyncClient, linhas: list[dict[str, Any]]) -> list[str]:
    """Registra quem o 17track ainda não conhece (1 crédito cada) e devolve os
    aceitos. Sem saldo, para: o resto fica pra quando recarregarem."""
    feitos: list[str] = []
    for i in range(0, len(linhas), _LOTE):
        if i:
            await asyncio.sleep(logistica_track._PAUSA_ENTRE_LOTES)
        body = await logistica_track._chamar(c, "register", linhas[i : i + _LOTE], base=BASE_V24)
        aceitos, recusados = logistica_track._aceitos_e_recusados(body)
        feitos += aceitos
        if _ERRO_SEM_QUOTA in recusados.values():
            logger.warning("temu_rastreio_sem_quota", registrados=len(feitos))
            break
    return feitos


def _coletado_em(track_info: dict) -> datetime | None:
    """Hora do 1º evento da transportadora (o "Objeto coletado" da Anjun, a
    entrega na agência da J&T) — só quando o status prova que saiu."""
    status = str(((track_info.get("latest_status") or {}).get("status")) or "")
    if status not in _SAIU:
        return None
    eventos = logistica_track.eventos_de_track_info(track_info)
    return eventos[-1].quando if eventos else None


async def _aplicar(
    session: AsyncSession, bling: Any, cand: BlingOrder, coletado_em: datetime
) -> str:
    """Pedido Temu coletado → 15 no Bling e no DaVinci. Mesmas travas da Magalu
    (`magalu_shipment_sync.apply_magalu_shipment`): trava do ingest, relê as
    linhas e o Bling, só grava depois de LER 15 no Bling. Devolve o motivo."""
    bid, numero, po, loja = cand.bling_id, cand.numero, cand.numeroloja, cand.loja
    if coletado_em > datetime.now(UTC):
        return "hora_invalida"
    travou = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": f"ingest_bling_order:{bid}"},
    )
    if not travou:
        return "ingest_rodando"
    rows = (
        await session.scalars(
            select(BlingOrder)
            .where(BlingOrder.bling_id == bid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    if not rows or any(
        r.numero != numero
        or r.loja != loja
        or r.numeroloja != po
        or r.situacao not in _ABERTAS
        or r.item_index is None
        for r in rows
    ):
        return "mudou_no_davinci"
    # Já saiu antes e alguém voltou a situação à mão: não reescreve histórico.
    if (
        await session.scalar(
            select(BlingEnvioEvento.bling_id).where(BlingEnvioEvento.bling_id == bid).limit(1)
        )
        is not None
    ):
        return "ja_teve_envio"

    async def ler_bling() -> dict | None:
        r = await bling._request("GET", f"/pedidos/vendas/{bid}")
        if r.status_code != 200:
            return None
        d = r.json().get("data")
        if (
            not isinstance(d, dict)
            or str(d.get("id")) != str(bid)
            or str(d.get("numero")) != str(numero)
            or str(d.get("numeroLoja") or "").strip() != po
        ):
            return None
        return d

    try:
        vivo = await ler_bling()
        if vivo is None:
            return "bling_nao_confere"
        estado = str((vivo.get("situacao") or {}).get("id"))
        if estado not in (*_ABERTAS, "15"):
            return "mudou_no_bling"
        if estado != "15":
            # Mesmo se o PATCH falhar, só o 15 LIDO em seguida autoriza gravar.
            try:
                await bling.update_order_situacao(bid, 15)
            except httpx.HTTPError:
                pass
        confirmado = await ler_bling()
        if confirmado is None or str((confirmado.get("situacao") or {}).get("id")) != "15":
            return "bling_nao_confirmou"
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return "bling_falhou"

    antiga = rows[0].situacao
    for r in rows:
        r.situacao = "15"
        r.em_andamento_data = coletado_em.astimezone(_BRT).date()
    await session.flush()  # o gatilho cria o registro de envio (hora = agora)
    for ev in (
        await session.scalars(select(BlingEnvioEvento).where(BlingEnvioEvento.bling_id == bid))
    ).all():
        ev.occurred_at = coletado_em
        ev.shipping_day = (coletado_em.astimezone(_BRT) - timedelta(hours=10)).date()
    await record_margem_audit(
        session,
        acao="situacao",
        pedido_bling=numero,
        bling_id=bid,
        sku=rows[0].item_codigo,
        valor_antigo=antiga,
        valor_novo=15,
        origem="job_envio_temu",
        mudado_por=None,
    )
    await record_margem_audit(
        session,
        acao="data_envio",
        pedido_bling=numero,
        bling_id=bid,
        sku=rows[0].item_codigo,
        valor_antigo=None,
        valor_novo=coletado_em.isoformat(),
        origem="temu_17track_coleta",
        mudado_por=None,
    )
    return "confirmado"


async def rodada() -> dict[str, int]:
    """Uma passada: pedidos Temu vermelhos → 17track → os coletados ficam
    verdes. Contadores pro log (o cron não tem tela)."""
    from app.db import session_scope
    from app.services.advisory_lock import SYNC_NAMESPACE
    from app.services.marketplace_shipment_check import (
        _build_bling_client,
        _get_bling_integration,
    )

    resumo = dict.fromkeys(
        (
            "candidatos",
            "aguardando_3h",
            "sem_etiqueta",
            "sem_codigo",
            "jt_sem_cpf",
            "registrados",
            "consultas_pagas",
            "ainda_nao_saiu",
            "verdes",
            "erros",
        ),
        0,
    )
    contador = {
        "sem_etiqueta": "sem_etiqueta",
        "codigo_nao_achado_na_etiqueta": "sem_codigo",
        "jt_sem_cpf_no_pedido": "jt_sem_cpf",
    }
    async with session_scope() as session:
        travou = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :k)"),
                {"ns": SYNC_NAMESPACE, "k": _LOCK_RODADA},
            )
        ).scalar()
        if not travou:
            resumo["ocupado"] = 1
            return resumo
        cands = await _candidatos(session)
        resumo["candidatos"] = len(cands)
        agora = _agora()
        etiqueta_em = dict(
            (
                await session.execute(
                    select(NfEtiquetaArquivo.pedido_bling, NfEtiquetaArquivo.created_at).where(
                        NfEtiquetaArquivo.pedido_bling.in_([str(o.numero) for o in cands])
                    )
                )
            ).all()
        )
        linhas: dict[str, dict[str, Any]] = {}
        pedido_do_codigo: dict[str, BlingOrder] = {}
        for o in cands:
            # Antes da coleta o cadastro gasta a única consulta rápida do 17track.
            feita = etiqueta_em.get(str(o.numero))
            if feita is not None and agora - feita < _ESPERA_CADASTRO:
                resumo["aguardando_3h"] += 1
                continue
            _cod, linha, erro = await _preparar(session, str(o.numero))
            if linha is None:
                resumo[contador[erro or "sem_etiqueta"]] += 1
                continue
            linhas[linha["number"]] = linha
            pedido_do_codigo[linha["number"]] = o
        if not linhas:
            return resumo
        try:
            async with httpx.AsyncClient(timeout=40.0) as c:
                lidos, nao_registrados = await _ler(c, list(linhas.values()))
                anotados = await _ler_consultas(list(linhas))
                novos = [linhas[n] for n in nao_registrados if n in linhas]
                if novos:
                    aceitos = await _registrar(c, novos)
                    resumo["registrados"] = len(aceitos)
                    for codigo in aceitos:
                        pagas = ((anotados or {}).get(codigo) or {}).get("n", 0)
                        await _anotar_consulta(codigo, pagas, agora)
                coletados = {
                    n: h for n, ti in lidos.items() if n in linhas and (h := _coletado_em(ti))
                }
                # Sem Redis não há contador nem teto: não paga nada nesta rodada.
                pagaveis = lidos if anotados is not None else {}
                anotados = anotados or {}
                for codigo, ti in pagaveis.items():
                    if (
                        codigo not in linhas
                        or codigo in coletados
                        or linhas[codigo]["carrier"] != ANJUN_CARRIER
                        or _motivo_sem_consulta(agora, anotados.get(codigo), _sync_17track(ti))
                    ):
                        continue
                    pagas = (anotados.get(codigo) or {}).get("n", 0) + 1
                    # Anota antes de chamar: consulta que falhou também conta no teto.
                    await _anotar_consulta(codigo, pagas, agora)
                    if resumo["consultas_pagas"]:
                        await asyncio.sleep(logistica_track._PAUSA_ENTRE_LOTES)
                    resumo["consultas_pagas"] += 1
                    pedido = pedido_do_codigo[codigo].numero
                    try:
                        novo = await _consultar_na_hora(c, linhas[codigo])
                    except logistica_track.Track17Error as e:
                        logger.warning(
                            "temu_rastreio_consulta_falhou",
                            pedido=pedido,
                            n=pagas,
                            err=str(e)[:200],
                        )
                        resumo["erros"] += 1
                        continue
                    hora = _coletado_em(novo or {})
                    logger.info(
                        "temu_rastreio_consulta_paga",
                        pedido=pedido,
                        n=pagas,
                        status=((novo or {}).get("latest_status") or {}).get("status"),
                        coletado=hora is not None,
                    )
                    if hora is not None:
                        coletados[codigo] = hora
        except logistica_track.Track17Error as e:
            logger.warning("temu_rastreio_17track_falhou", err=str(e)[:200])
            resumo["erros"] += 1
            return resumo
        resumo["ainda_nao_saiu"] = len(linhas) - len(coletados)
        if not coletados:
            return resumo
        integracao = await _get_bling_integration(session)
        if integracao is None:
            logger.warning("temu_rastreio_sem_bling")
            resumo["erros"] += 1
            return resumo
        # Igual à Magalu: savepoint por pedido não pode travar a linha da
        # integração (a rotação de token grava numa transação própria).
        bling = await _build_bling_client(session, integracao, persist_in_session=False)
        for codigo, hora in coletados.items():
            o = pedido_do_codigo[codigo]
            try:
                async with session.begin_nested():
                    motivo = await _aplicar(session, bling, o, hora)
            except Exception as exc:  # noqa: BLE001 — um pedido não derruba os outros
                resumo["erros"] += 1
                logger.warning(
                    "temu_rastreio_aplicar_falhou", pedido=o.numero, erro=type(exc).__name__
                )
                continue
            resumo["verdes"] += int(motivo == "confirmado")
            resumo["erros"] += int(motivo in _ERROS_BLING)
            logger.info("temu_rastreio_aplicado", pedido=o.numero, motivo=motivo)
    logger.info("temu_rastreio_rodada", **resumo)
    return resumo
