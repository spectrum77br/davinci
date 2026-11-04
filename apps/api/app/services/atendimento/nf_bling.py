"""A NF cheia que não está no `nf_nota`, lida do Bling (04/11/2026, decisão D1 do Eduardo).

O `nf_nota` só enche pelo coletor do robô da nuvem (`POST /api/nf-cadastro/agent/nf-xml`, desde
19/08/2026): nota de pedido mais velho, ou que o robô não subiu, fica com a pessoa como
`nf:nf_nao_chegou`/`nf:nf_so_embalagem`. Nas lojas no automático, desde 27/10, foram 8 das 17
conversas com a ação da NF. O Eduardo: quando a cheia do pedido não está lá, buscar no Bling.

SÓ LEITURA, só GET, pelos clientes que já existem (a fila do Bling e o token de cada um), em duas
contas possíveis:

1. A conta principal: `get_order` (`GET /pedidos/vendas/{id}`) dá `notaFiscal.id`, e `get_nfe`
   (`GET /nfe/{id}`) dá o link assinado do XML.
2. As contas de emissão (`bling_notas`, só as `active`): a nota vem do espelho
   `bling_notas_emitidas` pelo complemento = pedido da loja (a chave exata do Pós Vendas) e o XML
   de `notas_fiscais._fetch_nota_xml`. O token é o que está na linha, renovado de 5 em 5 h pelo
   `bling_notas_token_refresh`: vencido, não renova aqui (o Bling troca o refresh token a cada
   uso e um corte no meio trancaria a conta) e a conta fica de fora nesta vez.

SÓ ENTRA A CHEIA DESTE PEDIDO (`_a_cheia_do_pedido`): XML autorizado, de saída normal, ≥ 60% do
total, com o destinatário igual ao CPF/CNPJ do pedido — a nota parcial, a devolução, a cancelada
no Bling e a de outra pessoa não são gravadas. A nota que já está ligada a OUTRO pedido não muda
de vínculo (`_gravar`). Depois de gravada, a `nf_automatica.conferir_nf` passa pela MESMA régua
de quem veio pelo coletor: nada sai só porque veio do Bling.

ONDE RODA: dentro do `nf_automatica.conferir_nf`, no worker (a rodada da IA; a sugestão pedida
pela tela não passa aqui), como a ida à plataforma que confere o comprador. Um job à parte só
serviria "no próximo turno", e o cliente que pediu a NF não escreve de novo: a pessoa já teria
respondido. Por isso o teto (`TEMPO_MAXIMO_S`, a rodada não fica presa num Bling lento) e UMA
tentativa por pedido em `REPETE_EM_S` (`_primeira_tentativa`, a NF da noite entra na seguinte).

NUNCA LEVANTA: qualquer falha devolve "não veio" e o caso fica com a pessoa, como antes.

O QUE ISTO ALCANÇA HOJE (medido em 04/11, só leitura): 13 das 14 contas `bling_notas` estão
`pausada_token_morto` desde 17/09; as 5 empresas que emitem 99% das cheias dos últimos 60 dias não
têm conta ali; e o espelho não tem a cheia de nenhum dos 8 pedidos. A conta principal não pôde ser
medida (a análise não chama o Bling). Conta nova ou reautorizada em `bling_notas` passa a valer
sem mudar código, depois de o `pos_vendas.sync_notas_emitidas` espelhar as notas dela.

Só ids e códigos no log, nunca dado do cliente.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder, NfNota
from app.models.bling_nota import BlingNota, BlingNotaEmitida
from app.redis_client import redis
from app.services import nf_xml
from app.services.atendimento import danfe
from app.services.pos_vendas import ISSUED_SITUACOES

logger = structlog.get_logger()

# O teto de TODA a leitura de um pedido: o `wait_for` corta o GET, nunca a renovação do token da
# conta principal (`clientes.blindar_renovacao`, o `painel._cliente_bling`).
TEMPO_MAXIMO_S = 20.0
# Quantas notas do espelho de um pedido (a reemitida também aparece): mais não é o caso simples.
MAX_NOTAS = 3
# Sem a cheia agora, a próxima tentativa do pedido só depois disto.
REPETE_EM_S = 6 * 3600
_CHAVE_TENTATIVA = "{}:atendimento:nf_bling:{}"


@dataclass(frozen=True)
class _Pedido:
    numero: str
    bling_id: int | None
    numerolojas: frozenset[str]
    total: Any
    documento: str


def _digitos(valor: Any) -> str:
    return "".join(c for c in str(valor or "") if c.isdigit())


def _documento_da_ordem(ordem: dict) -> str:
    """O CPF/CNPJ do pedido no GET do Bling (como o `bling_orders`: o contato ou o da entrega)."""
    contato = ordem.get("contato") if isinstance(ordem.get("contato"), dict) else {}
    transporte = ordem.get("transporte") if isinstance(ordem.get("transporte"), dict) else {}
    entrega = transporte.get("contato") if isinstance(transporte.get("contato"), dict) else {}
    return _digitos(contato.get("numeroDocumento") or entrega.get("numeroDocumento"))


def _a_cheia_do_pedido(xml: bytes, total: Any, documento: str) -> nf_xml.NotaXml | None:
    """A nota do XML, se for a cheia autorizada deste pedido e deste destinatário. PURA."""
    try:
        nota = nf_xml.parse_nfe(xml)
        # A homologação é "autorizada" também, sem valor fiscal.
        producao = danfe.ler_nota(xml).tp_amb == "1"
    except (nf_xml.NfXmlError, danfe.DanfeInvalida):
        return None
    if not producao or nota.situacao != danfe.SITUACAO_AUTORIZADA or not nota.protocolo:
        return None
    if not danfe._saida_normal(xml) or not danfe._eh_cheia(nota.valor, total):
        return None
    return nota if documento and nota.destinatario_doc == documento else None


async def _primeira_tentativa(numero: str) -> bool:
    """SET NX por pedido. Redis fora do ar: False (sem a marca e sem a fila, não vai ao Bling)."""
    chave = _CHAVE_TENTATIVA.format(get_settings().database_schema, numero)
    try:
        return bool(await redis.set(chave, "1", nx=True, ex=REPETE_EM_S))
    except Exception as e:  # noqa: BLE001
        logger.info("atendimento_nf_bling_marca_indisponivel", erro=type(e).__name__)
        return False


async def _o_pedido(session: AsyncSession, numero: str) -> _Pedido | None:
    linhas = (
        await session.execute(
            select(
                BlingOrder.bling_id,
                BlingOrder.numeroloja,
                BlingOrder.total,
                BlingOrder.documento_destinatario,
            ).where(BlingOrder.numero == numero)
        )
    ).all()
    if not linhas:
        return None
    ids = {b for b, *_ in linhas if b}
    docs = {_digitos(d) for *_, d in linhas} - {""}
    return _Pedido(
        numero=numero,
        bling_id=next(iter(ids)) if len(ids) == 1 else None,
        numerolojas=frozenset(str(n).strip() for _, n, *_ in linhas if n),
        total=max((t for _, _, t, _ in linhas if t is not None), default=None),
        # Dois documentos no mesmo pedido: nenhum vale.
        documento=next(iter(docs)) if len(docs) == 1 else "",
    )


async def _notas_das_contas(
    session: AsyncSession, pedido: _Pedido, agora: datetime
) -> list[tuple[str, dict]]:
    """(token, nota) deste pedido nas contas de emissão ativas com o token em dia."""
    if not pedido.numerolojas:
        return []
    linhas = (
        await session.execute(
            select(
                BlingNotaEmitida.bling_id,
                BlingNotaEmitida.situacao,
                BlingNotaEmitida.valor,
                BlingNota.access_token,
                BlingNota.token_expires_at,
            )
            .join(BlingNota, BlingNota.id == BlingNotaEmitida.conta_id)
            .where(
                BlingNota.status == "active",
                BlingNotaEmitida.complemento.in_(sorted(pedido.numerolojas)),
                BlingNotaEmitida.situacao.in_(ISSUED_SITUACOES),
            )
            .order_by(BlingNotaEmitida.data_emissao.desc().nulls_last())
            .limit(MAX_NOTAS)
        )
    ).all()
    em_dia = agora + timedelta(seconds=60)
    return [
        (n.access_token, {"id": n.bling_id, "situacao": n.situacao})
        for n in linhas
        if n.access_token
        and (n.token_expires_at is None or n.token_expires_at > em_dia)
        # O valor só existe depois do detalhe; a parcial conhecida nem vai ao Bling.
        and (n.valor is None or danfe._eh_cheia(n.valor, pedido.total))
    ]


async def _xml_do_detalhe(detalhe: dict) -> bytes | None:
    """O XML do detalhe da nota, se ela está emitida (a cancelada no Bling não vem)."""
    from app.routers import notas_fiscais as nf

    if detalhe.get("situacao") not in ISSUED_SITUACOES or not detalhe.get("xml"):
        return None
    return await nf._download_xml(str(detalhe["xml"]))


async def _ler_no_bling(
    pedido: _Pedido, cliente: Any, contas: list[tuple[str, dict]]
) -> tuple[list[bytes], str]:
    """Só rede, sem a sessão: os XMLs que o Bling tem do pedido e o CPF/CNPJ dele."""
    from app.routers import notas_fiscais as nf

    xmls: list[bytes] = []
    documento = pedido.documento
    if cliente is not None and pedido.bling_id:
        try:
            ordem = await cliente.get_order(pedido.bling_id)
            # Antes de 08/2026 o espelho não guardava o documento: vale o do pedido no Bling.
            documento = documento or _documento_da_ordem(ordem)
            nota = ordem.get("notaFiscal") if isinstance(ordem.get("notaFiscal"), dict) else {}
            if int(nota.get("id") or 0) > 0:
                xml = await _xml_do_detalhe(await cliente.get_nfe(int(nota["id"])))
                if xml:
                    xmls.append(xml)
        except Exception as e:  # noqa: BLE001 — falhou a principal, ainda há as de emissão
            logger.info(
                "atendimento_nf_bling_principal", pedido=pedido.numero, erro=type(e).__name__
            )
    for token, nota in contas:
        # `_fetch_nota_xml` não levanta: a falha vem no motivo e a conta fica de fora.
        detalhe, xml, _motivo = await nf._fetch_nota_xml(token, nota)
        if xml and detalhe.get("situacao") in ISSUED_SITUACOES:
            xmls.append(xml)
    return xmls, documento


async def _gravar(session: AsyncSession, nota: nf_xml.NotaXml, xml: bytes, numero: str) -> bool:
    """A nota no `nf_nota`, ligada ao pedido. A que já é de outro pedido não muda (False)."""
    linha = await session.scalar(select(NfNota).where(NfNota.chave == nota.chave))
    if linha is None:
        session.add(NfNota(**asdict(nota), xml=xml, pedido_bling=numero))
    elif not linha.pedido_bling:
        linha.pedido_bling = numero
    elif linha.pedido_bling != numero:
        logger.warning("atendimento_nf_bling_de_outro_pedido", pedido=numero, numero=nota.numero)
        return False
    await session.flush()
    return True


async def trazer_a_cheia(session: AsyncSession, numero: str) -> bool:
    """Lê no Bling a cheia do pedido e grava no `nf_nota`. True = gravou. Nunca levanta."""
    from app.services.atendimento import painel

    try:
        if not await _primeira_tentativa(numero):
            return False
        # Num SAVEPOINT: erro de banco aqui não quebra a transação de quem chamou.
        async with session.begin_nested():
            pedido = await _o_pedido(session, numero)
            if pedido is None:
                return False
            cliente = await painel._cliente_bling(session)
            contas = await _notas_das_contas(session, pedido, datetime.now(UTC))
        xmls, documento = await asyncio.wait_for(
            _ler_no_bling(pedido, cliente, contas), timeout=TEMPO_MAXIMO_S
        )
        cheias = [
            (nota, xml)
            for xml in xmls
            if (nota := _a_cheia_do_pedido(xml, pedido.total, documento)) is not None
        ]
        gravou = False
        # Grava todas: duas cheias diferentes seguram na `_nota_unica` (qual é a certa?).
        for nota, xml in cheias:
            async with session.begin_nested():
                gravou = await _gravar(session, nota, xml, numero) or gravou
    except Exception as e:  # noqa: BLE001 — o Bling nunca derruba o automático; fica com a pessoa
        logger.info("atendimento_nf_bling_falhou", pedido=numero, erro=type(e).__name__)
        return False
    logger.info(
        "atendimento_nf_bling", pedido=numero, lidas=len(xmls), cheias=len(cheias), gravou=gravou
    )
    return gravou


async def documento_no_bling(session: AsyncSession, numero: str) -> str:
    """O CPF/CNPJ do pedido no Bling, para o pedido sem documento no espelho. "" = não deu."""
    from app.services.atendimento import painel

    try:
        async with session.begin_nested():
            pedido = await _o_pedido(session, numero)
            cliente = await painel._cliente_bling(session)
        if pedido is None or cliente is None or not pedido.bling_id:
            return ""
        return _documento_da_ordem(
            await asyncio.wait_for(cliente.get_order(pedido.bling_id), timeout=TEMPO_MAXIMO_S)
        )
    except Exception as e:  # noqa: BLE001 — sem saber, `pedido_sem_documento`
        logger.info("atendimento_nf_bling_sem_documento", pedido=numero, erro=type(e).__name__)
        return ""
