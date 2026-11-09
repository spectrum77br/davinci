"""O BRINDE do pedido não pago: o botão e a trava da NF (o dono, 05/11/2026).

3 h depois de um pedido criado e não finalizado, o motor manda a oferta (celular
ganha AirTag, mala ganha chaveiro). Se a pessoa comprar, alguém põe o brinde NO
MESMO PEDIDO DO BLING por um botão do Comunicador. Quem tem o brinde prometido é
a linha do motor em `atendimento_automacao_registros` (`AUTOMACOES_BRINDE`) para
(loja, pedido) em `enviado`, `enviando` ou `revisar` (`ESTADOS_OFERTA_BRINDE`: o envio
ambíguo pode ter entregue, e a pessoa pode ter lido e pago): só o MESMO pedido — quem
cancela e compra de novo fica de fora nesta versão. O motor não grava nada além
disso; `atendimento_brindes` só ganha linha quando alguém age ou quando o prazo vence.

A TRAVA DA NF (`segurados_pela_oferta`, chamada no `_candidatos` do sweep): o Bling
só recebe pedido PAGO, em situação 6, e o sweep pega em até 2 min — sem a trava a
NF sairia antes de alguém ver o botão. Fica fora da fila o pedido com oferta
enviada, sem linha `adicionado`/`dispensado`/`expirado`, que entrou no espelho há
menos de `BRINDE_SEGURA_NF_H`. Passado o prazo volta SOZINHO: nunca fica preso. A
ligação é `registro.pedido` = `bling_orders.numeroloja` (conferido em produção em
05/11: 234 de 234 na Shopee). Sem a loja no join de propósito: `store_info.
integration_id` está vazio nas 32 fichas Shopee/TikTok ativas, e o nº da plataforma
não se repete entre lojas. O relógio é `bling_orders.created_at` (o upsert do
pedido não o toca: é a 1ª vez que o espelho viu o pedido).

O PUT (`adicionar`) espelha a troca (`troca.py`): GET ao vivo, corpo do ÚLTIMO GET,
4xx = recusado e nada muda, qualquer outra falha = `incerto`, GETs decidem e o PUT
nunca se repete às cegas. Lock por pedido (`pg_advisory_xact_lock`).

O TOTAL DO PEDIDO NÃO MUDA (`compensar_desconto`): o item novo soma R$ 0,01 aos
produtos; as parcelas ficam como estão (o comprador pagou o mesmo) e o `total` o
Bling recalcula (`_COMPUTED_DROP`), então o `desconto` do PEDIDO sobe os mesmos R$
0,01. É o lugar onde a Shopee e o TikTok já põem o desconto, e o preço do item
vendido não muda. NÃO FOI TESTADO NO BLING REAL em 05/11: se o Bling recusar, é 4xx
e nada muda no pedido. Desconto em percentual: recusa, com a frase clara. Se o Bling
aceitar e o total ou as parcelas mudarem (`divergencia_de_valores`): `incerto`, com o
brinde no espelho e a NF segura, até bater ou o prazo vencer. Nunca `adicionado` em verde.

A MARGEM (conferido em 05/11): a linha de R$ 0,01 que custa R$ 22 tem margem de
-99,9% (`marketplace_margem` é por linha) e o robô (`margem_auto_hold`, BOOL_OR por
pedido) reprovaria TODO pedido com brinde quando o líquido da plataforma chegasse:
o pedido iria para 83955. Por isso o pino `Aprovado` no nome de quem clicou, com a
trilha, SÓ NA LINHA DO BRINDE: o WHERE do robô filtra por linha antes do BOOL_OR, a
linha de -99,9% sai da conta e o celular continua sendo julgado. O pino no pedido
inteiro faria quem atende aprovar uma Margem que o robô nem julgou (a troca é outro
caso: lá a decisão do dono é aprovar). Com uma linha decidida, o refresh direcionado
pula o pedido; os números do celular chegam pelo `rebuild_all`, que é por linha.
Pedido já `Pendente`/`Reprovado` é recusado: quem decide é a Margem. A prioridade de
estoque segue valendo: o lote de venda do brinde anda junto com o celular
(`_plano_estoque_unico`); `.cd` e sem lote ficam de fora dessa conta.

Texto de cliente nunca vai para o log nem para a tabela: só ids, códigos e SKUs.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import String, and_, cast, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoConversa,
    AtendimentoPedidoComprador,
    BlingOrder,
    NfFaturamento,
    StoreInfo,
    User,
)
from app.models.atendimento_brinde import AtendimentoBrinde
from app.services import bling_orders as bling_orders_svc
from app.services import verificar_margem
from app.services.atendimento import troca as _troca
from app.services.atendimento.automacoes_catalogo import ESTADOS_OFERTA_BRINDE, brinde_do_resumo
from app.services.atendimento.constantes import (
    AUTOMACOES_BRINDE,
    AUTOMACOES_NAO_PAGO_AVISO,
    BRINDE_SEGURA_NF_H,
    BRINDE_SKU,
    BRINDE_VALOR_NF,
)
from app.services.logistica_bling import build_observacoes_put_body, compose_observacoes
from app.services.margem_audit import record_margem_audit
from app.services.prioridade_estoque import (
    _ConsultaFalhouError,
    _produto_exato,
    _saldo,
    _tag_de,
)
from app.services.prioridade_estoque_movimentos import _SITUACOES_MORTAS

logger = structlog.get_logger()

# ── Estados (`atendimento_brindes.estado`) ────────────────────────────────
ADICIONADO = "adicionado"
DISPENSADO = "dispensado"
EXPIRADO = "expirado"
INCERTO = "incerto"
# Os que soltam a NF. `incerto` não solta: o brinde pode estar lá.
SOLTAM_NF = (ADICIONADO, DISPENSADO, EXPIRADO)

SITUACAO_EM_ABERTO = "6"
ORIGEM_MARGEM = "atendimento_brinde"
PINO_APROVADO = "Aprovado"
PINOS_EM_ANALISE = frozenset({"Pendente", "Reprovado"})

ROTULOS = {"airtag": "AirTag", "chaveiro": "chaveiro"}
# `.us` é capa de iPhone (`a019.us`, `a075.us`), não o brinde: nunca.
LOTE_PROIBIDO = "us"
LOTE_RESERVA = "cd"
# Acessório no kit (`dg053.sp+a001.sp`): o lote que vale é o do celular.
_ACESSORIO = re.compile(r"^a\d")

# Depois de um `incerto`: o PUT sem resposta pode chegar depois. O clique seguinte
# começa sempre pelo GET; sem o brinde lá, só tenta de novo passada esta folga.
FOLGA_INCERTO = timedelta(minutes=2)
# Os GETs de conferência depois do PUT sem resposta (s), como a troca.
ESPERAS_CONFERENCIA_S: tuple[float, ...] = _troca.ESPERAS_CONFERENCIA_S
# A janela de quem expira e de quem aparece na lista (a mesma do sweep).
JANELA = timedelta(days=7)
# Por quantas horas o alerta do brinde expirado fica na lista.
ALERTA_H = 48
MAX_EXPIRADOS_POR_PASSADA = 200

NOTA_EXPIROU = "Brinde não adicionado em 12 h: a NF seguiu sem ele."
NOTA_EXPIROU_INCERTO = (
    "Brinde: o Bling não confirmou a inclusão em 12 h e a NF seguiu. Confira o pedido no Bling."
)

TEXTOS = {
    "sem_oferta": "Este pedido não tem oferta de brinde enviada.",
    "ainda_nao_pago": "O pedido ainda não chegou ao Bling (o Bling só recebe pedido pago).",
    "pedido_em_dobro": "Há outro pedido vivo no Bling com o mesmo número da loja.",
    "dispensado": "Alguém já escolheu não adicionar este brinde.",
    "ja_adicionado": "O brinde já está no pedido.",
    "sem_tipo": "Não sei qual brinde este pedido ganha: confira os itens do pedido.",
    "sku_invalido": "Este produto não é o brinde deste pedido.",
    "nf_emitida": "O pedido já tem NF (emitida ou em emissão): o brinde ficaria fora dela.",
    "em_fila_nf": "O pedido está na fila da NF: o brinde ficaria fora dela.",
    "rastreio": "O pedido já tem rastreio.",
    "situacao_mudou": "O pedido não está Em aberto no Bling.",
    "margem_em_analise": "O pedido está em análise na Margem: decida lá primeiro.",
    "bling_indisponivel": (
        "O Bling não respondeu agora: tente de novo em instantes. Nada foi escrito."
    ),
    "sem_saldo": "Sem saldo do brinde no Bling, nem no lote do pedido, nem no CD.",
    "desconto_percentual": (
        "O pedido tem desconto em percentual: não dá para manter o total igual. Adicione à mão."
    ),
    "espere_incerto": (
        "O Bling não respondeu à última tentativa e ainda não mostra o brinde: "
        "espere 2 minutos e clique de novo."
    ),
    "incerto": (
        "O Bling não respondeu e não sei se o brinde entrou: clique de novo em instantes "
        "(ele confere antes de qualquer coisa)."
    ),
    "espelho_atrasado": (
        "O Bling já tem o brinde, mas o DaVinci não atualizou o pedido: clique de novo."
    ),
    "incerto_nao_dispensa": (
        "O brinde pode já estar no pedido: clique em adicionar para conferir."
    ),
    "sem_permissao": "Quem responde no atendimento é quem adiciona o brinde.",
}


class BrindeRecusado(Exception):  # noqa: N818 — nome do domínio, como TrocaRecusada
    """O brinde não pôde ser adicionado. `code` é estável; `status` é o HTTP."""

    def __init__(self, code: str, detail: str | None = None, *, status: int = 409) -> None:
        self.code = code
        self.detail = detail or TEXTOS.get(code, code)
        self.status = status
        super().__init__(code)


@dataclass
class _Espelho:
    """O pedido no espelho do Bling (a linha viva com este nº da plataforma)."""

    numero: str
    bling_id: int | None
    loja: str | None
    situacao: str | None
    pino: str | None
    aprovado_por: UUID | None
    entrou_em: datetime | None
    itens: list[dict[str, Any]] = field(default_factory=list)


def _agora() -> datetime:
    return datetime.now(UTC)


def _prazo() -> timedelta:
    return timedelta(hours=BRINDE_SEGURA_NF_H)


# ── Puras ─────────────────────────────────────────────────────────────────


def familia_do_sku(sku: str | None) -> str | None:
    """ "airtag", "chaveiro" ou None: o SKU é do brinde? `.us` nunca é. PURA."""
    limpo = (sku or "").strip().lower()
    for tipo, base in BRINDE_SKU.items():
        if limpo == base:
            return tipo
        if limpo.startswith(f"{base}.") and limpo.rsplit(".", 1)[1] != LOTE_PROIBIDO:
            return tipo
    return None


def lote_do_vendido(skus: Iterable[str | None]) -> str | None:
    """O lote do item vendido (`dg002.sp` → `sp`); no kit, o do celular. PURA.

    O brinde que já está no pedido não conta. Acessório só vale se não houver outro.
    """
    reserva: str | None = None
    for sku in skus:
        for pedaco in (sku or "").lower().split("+"):
            pedaco = pedaco.strip()
            if not pedaco or familia_do_sku(pedaco):
                continue
            lote = _tag_de(pedaco)
            if lote is None or lote == LOTE_PROIBIDO:
                continue
            if not _ACESSORIO.match(pedaco):
                return lote
            reserva = reserva or lote
    return reserva


def skus_candidatos(tipo: str, lote: str | None) -> list[str]:
    """Na ordem: o lote do item vendido, depois o CD. O chaveiro não tem lote. PURA."""
    base = BRINDE_SKU[tipo]
    if tipo != "airtag":
        return [base]
    nomes = [f"{base}.{lote}"] if lote and lote != LOTE_PROIBIDO else []
    reserva = f"{base}.{LOTE_RESERVA}"
    return nomes if reserva in nomes else [*nomes, reserva]


def linha_observacao(sku: str, nome: str) -> str:
    """A linha nas Observações (`compose_observacoes` põe o "dd/mm - " na frente). PURA."""
    return f"BRINDE {sku} (oferta do não pago, por {nome})"


def brinde_nos_itens(order: Mapping[str, Any], tipo: str) -> str | None:
    """O SKU do brinde se a linha de R$ 0,01 já está no pedido. PURA.

    O mesmo produto comprado de verdade (a preço cheio) não é o brinde.
    """
    for item in _troca._itens_vivos(order):
        codigo = str(item.get("codigo") or "").strip()
        if familia_do_sku(codigo) != tipo:
            continue
        try:
            valor = Decimal(str(item.get("valor") or 0))
        except InvalidOperation:
            continue
        if abs(valor - Decimal(str(BRINDE_VALOR_NF))) < Decimal("0.005"):
            return codigo
    return None


def compensar_desconto(body: dict[str, Any]) -> None:
    """O `desconto` do pedido sobe R$ 0,01: o total e as parcelas ficam iguais. Muda `body`."""
    atual = body.get("desconto") if isinstance(body.get("desconto"), Mapping) else {}
    unidade = str(atual.get("unidade") or "REAL").strip().upper()
    try:
        valor = Decimal(str(atual.get("valor") or 0))
    except InvalidOperation as e:
        raise BrindeRecusado("desconto_percentual") from e
    if unidade != "REAL" and valor != 0:
        raise BrindeRecusado("desconto_percentual")
    novo = (valor + Decimal(str(BRINDE_VALOR_NF))).quantize(Decimal("0.01"))
    body["desconto"] = {**atual, "valor": float(novo), "unidade": "REAL"}


def corpo_do_put(
    order: Mapping[str, Any], *, sku: str, produto: Mapping[str, Any], nome: str
) -> dict[str, Any]:
    """O corpo do último GET + o item novo SEM `id` + a linha nas Observações. PURA."""
    body = build_observacoes_put_body(
        dict(order), compose_observacoes(order.get("observacoes"), linha_observacao(sku, nome))
    )
    bruto = produto.get("raw") if isinstance(produto.get("raw"), Mapping) else {}
    body["itens"] = [
        *(body.get("itens") or []),
        {
            "codigo": sku,
            "descricao": produto.get("name") or sku,
            "unidade": bruto.get("unidade") or "UN",
            "quantidade": 1,
            "valor": BRINDE_VALOR_NF,
            "produto": {"id": int(produto["id"])},
        },
    ]
    compensar_desconto(body)
    return body


def _valores_das_parcelas(order: Mapping[str, Any]) -> list[Decimal]:
    parcelas = order.get("parcelas")
    if not isinstance(parcelas, list):
        return []
    valores = (_troca._dec(p.get("valor")) for p in parcelas if isinstance(p, Mapping))
    return [v for v in valores if v is not None]


def divergencia_de_valores(
    depois: Mapping[str, Any], antes: Mapping[str, Any] | None = None
) -> str | None:
    """O aviso se o total ou as parcelas não ficaram como o comprador pagou. PURA.

    Com o `antes` (o GET logo antes do PUT): o total e as parcelas têm de ser os mesmos.
    Sem ele (o clique seguinte, o brinde posto à mão): as parcelas têm de somar o total.
    """
    total = _troca._dec(depois.get("total"))
    parcelas = _valores_das_parcelas(depois)
    fim = "A NF segue segura: corrija no Bling e clique de novo."
    if antes is not None:
        total_antes = _troca._dec(antes.get("total"))
        if total_antes is not None and total is not None and total_antes != total:
            return f"Atenção: o total no Bling mudou de {total_antes} para {total}. {fim}"
        if _valores_das_parcelas(antes) != parcelas:
            return f"Atenção: as parcelas no Bling mudaram. {fim}"
        return None
    if total is not None and parcelas and sum(parcelas, Decimal(0)) != total:
        return f"Atenção: as parcelas não somam o total ({total}) no Bling. {fim}"
    return None


# ── A trava da NF ─────────────────────────────────────────────────────────


async def segurados_pela_oferta(session: AsyncSession, numeros: list[str]) -> set[str]:
    """Os nºs do Bling que ficam FORA da fila da NF agora. Erro sobe: o sweep trata."""
    if not numeros:
        return set()
    r = AtendimentoAutomacaoRegistro
    linhas = await session.execute(
        select(BlingOrder.numero)
        .join(
            r,
            and_(
                r.pedido == BlingOrder.numeroloja,
                r.automacao.in_(AUTOMACOES_BRINDE),
                r.estado.in_(ESTADOS_OFERTA_BRINDE),
            ),
        )
        .outerjoin(
            AtendimentoBrinde,
            and_(
                AtendimentoBrinde.integration_id == r.integration_id,
                AtendimentoBrinde.pedido == r.pedido,
            ),
        )
        .where(
            BlingOrder.numero.in_(numeros),
            or_(AtendimentoBrinde.id.is_(None), AtendimentoBrinde.estado.not_in(SOLTAM_NF)),
        )
        .group_by(BlingOrder.numero)
        .having(func.min(BlingOrder.created_at) > _agora() - _prazo())
    )
    return {n for n in linhas.scalars() if n}


# ── O filtro "Aguardando pagamento" ─────────────
# O dono, 06/11/2026: "da pra botar um filtro a mais ali em filtros aguardando
# pagamento pra gente ver essas". Quem ainda pode pagar: nos 30 dias medidos
# (05/10 a 02/11), 3 dias cobrem todos os pagamentos depois das 3 h (144 de 144),
# e a Shopee e o TikTok cancelam o não pago em cerca de 1 dia. O prazo protege
# do índice que não relê o pedido: o do TikTok só viu sair de `UNPAID` o cancelado.
JANELA_AGUARDANDO_PAGAMENTO = timedelta(days=3)


def aguardando_pagamento_sql(agora: datetime, c: Any = AtendimentoConversa) -> Any:
    """A conversa recebeu o lembrete ou a oferta e o pedido segue não pago? Nunca NULL.

    Não pago = `UNPAID` no índice, dentro de `JANELA_AGUARDANDO_PAGAMENTO` e fora
    do Bling (o Bling só recebe pedido pago, e o índice é lido de hora em hora).
    A oferta vale como em `_oferta` (`ESTADOS_OFERTA_BRINDE`). Subconsulta sem
    correlação: o /resumo conta por ela em cada loja e ela só roda uma vez.
    """
    r = AtendimentoAutomacaoRegistro
    p = AtendimentoPedidoComprador
    return c.id.in_(
        select(r.conversa_id)
        .join(p, and_(p.integration_id == r.integration_id, p.pedido == r.pedido))
        .where(
            r.automacao.in_(AUTOMACOES_NAO_PAGO_AVISO),
            r.estado.in_(ESTADOS_OFERTA_BRINDE),
            r.conversa_id.is_not(None),
            p.status == "UNPAID",
            p.criado_em >= agora - JANELA_AGUARDANDO_PAGAMENTO,
            ~select(BlingOrder.id).where(BlingOrder.numeroloja == r.pedido).exists(),
        )
    )


# ── As leituras ───────────────────────────────────────────────────────────


async def _oferta(
    session: AsyncSession, integration_id: UUID, pedido: str
) -> AtendimentoAutomacaoRegistro | None:
    r = AtendimentoAutomacaoRegistro
    return (
        await session.execute(
            select(r)
            .where(
                r.integration_id == integration_id,
                r.pedido == pedido,
                r.automacao.in_(AUTOMACOES_BRINDE),
                r.estado.in_(ESTADOS_OFERTA_BRINDE),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _linha(
    session: AsyncSession, integration_id: UUID, pedido: str
) -> AtendimentoBrinde | None:
    return (
        await session.execute(
            select(AtendimentoBrinde).where(
                AtendimentoBrinde.integration_id == integration_id,
                AtendimentoBrinde.pedido == pedido,
            )
        )
    ).scalar_one_or_none()


async def _espelho(session: AsyncSession, pedido: str) -> _Espelho | None:
    """O pedido vivo com este nº da plataforma. Dois vivos = `pedido_em_dobro`."""
    linhas = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.bling_id,
                BlingOrder.loja,
                BlingOrder.situacao,
                BlingOrder.status,
                BlingOrder.aprovado_por,
                BlingOrder.created_at,
                BlingOrder.item_codigo,
                BlingOrder.item_descricao,
                BlingOrder.item_quantidade,
            )
            .where(BlingOrder.numeroloja == pedido)
            .order_by(BlingOrder.numero, BlingOrder.item_index)
        )
    ).all()
    vivas = [x for x in linhas if x.situacao not in _SITUACOES_MORTAS]
    if not linhas:
        return None
    usadas = vivas or linhas
    if len({x.numero for x in vivas}) > 1:
        raise BrindeRecusado("pedido_em_dobro")
    primeira = usadas[0]
    return _Espelho(
        numero=primeira.numero,
        bling_id=next((int(x.bling_id) for x in usadas if x.bling_id), None),
        loja=primeira.loja,
        situacao=primeira.situacao,
        pino=next((x.status for x in usadas if (x.status or "").strip()), None),
        aprovado_por=next((x.aprovado_por for x in usadas if x.aprovado_por), None),
        entrou_em=min((x.created_at for x in usadas if x.created_at), default=None),
        itens=[
            {"sku": x.item_codigo, "descricao": x.item_descricao, "quantidade": x.item_quantidade}
            for x in usadas
            if x.item_codigo
        ],
    )


async def _tipo_do_pedido(
    session: AsyncSession, integration_id: UUID, pedido: str, itens: list[dict[str, Any]]
) -> str | None:
    """Pelo `itens_resumo` do índice (o que a oferta leu). O índice não tem TikTok:

    sem a linha lá, pelos nomes dos itens no espelho, sem o próprio brinde.
    """
    resumo = await session.scalar(
        select(AtendimentoPedidoComprador.itens_resumo).where(
            AtendimentoPedidoComprador.integration_id == integration_id,
            AtendimentoPedidoComprador.pedido == pedido,
        )
    )
    if (resumo or "").strip():
        return brinde_do_resumo(resumo)
    vendidos = [i for i in itens if not familia_do_sku(i.get("sku"))]
    return brinde_do_resumo(
        "; ".join(f"{i.get('quantidade') or 1}x {i.get('descricao') or ''}" for i in vendidos)
    )


async def _nf_bloqueia(session: AsyncSession, numero: str) -> str | None:
    """O código da trava da NF no banco, ou None. A planilha da NF congela na fila."""
    status_nf = await session.scalar(
        select(NfFaturamento.status_faturamento).where(NfFaturamento.pedido_bling == numero)
    )
    if (status_nf or "").strip().lower() in ("ok", "processando") or await _troca._tem_nf(
        session, numero
    ):
        return "nf_emitida"
    if await _troca._em_fila_nf(session, numero):
        return "em_fila_nf"
    return None


async def _conversa(
    session: AsyncSession,
    integration_id: UUID,
    pedido: str,
    conversa_id: UUID | None,
) -> AtendimentoConversa | None:
    if conversa_id is not None:
        c = await session.get(AtendimentoConversa, conversa_id)
        if c is not None:
            return c
    return (
        await session.execute(
            select(AtendimentoConversa)
            .where(
                AtendimentoConversa.integration_id == integration_id,
                AtendimentoConversa.pedido_marketplace == pedido,
            )
            .order_by(AtendimentoConversa.ultima_mensagem_em.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


# ── As escritas ───────────────────────────────────────────────────────────


async def _lock(session: AsyncSession, integration_id: UUID, pedido: str) -> None:
    """Duas pessoas no mesmo pedido: a segunda espera e lê o que a primeira fez."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"atendimento_brinde:{integration_id}:{pedido}"},
    )


async def _gravar(
    session: AsyncSession,
    linha: AtendimentoBrinde | None,
    *,
    integration_id: UUID,
    pedido: str,
    estado: str,
    tipo: str | None,
    sku: str | None = None,
    bling_id: int | None = None,
    por: UUID | None = None,
    erro: str | None = None,
) -> AtendimentoBrinde:
    if linha is None:
        linha = AtendimentoBrinde(integration_id=integration_id, pedido=pedido, estado=estado)
        session.add(linha)
    linha.estado = estado
    linha.tipo = tipo or linha.tipo
    linha.sku = sku or linha.sku
    linha.bling_id = bling_id or linha.bling_id
    linha.por = por or linha.por
    linha.em = _agora()
    linha.erro = erro
    await session.flush()
    return linha


async def _nota(
    session: AsyncSession,
    integration_id: UUID,
    pedido: str,
    conversa_id: UUID | None,
    texto: str,
    user_id: UUID | None,
) -> None:
    """Uma nota interna na conversa, quando houver, sem nunca derrubar quem chamou."""
    from app.services.atendimento import painel

    try:
        async with session.begin_nested():
            conversa = await _conversa(session, integration_id, pedido, conversa_id)
            if conversa is not None:
                await painel.criar_nota(session, conversa, texto, user_id=user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_brinde_nota_falhou", pedido=pedido, err=type(e).__name__)


def _saida(
    linha: AtendimentoBrinde | None,
    *,
    ok: bool,
    mensagem: str,
    numero: str | None = None,
    ja_estava: bool = False,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "estado": linha.estado if linha is not None else None,
        "sku": linha.sku if linha is not None else None,
        "numero_bling": numero,
        "mensagem": mensagem,
        "ja_estava": ja_estava,
    }


async def _get(cliente, bling_id: int) -> dict[str, Any]:
    try:
        order = await cliente.get_order(int(bling_id))
    except Exception as e:  # noqa: BLE001 — 429/timeout/5xx: não sei
        raise BrindeRecusado("bling_indisponivel") from e
    return order if isinstance(order, dict) else {}


def _conferir_vivo(order: Mapping[str, Any]) -> None:
    """Situação 6, sem NF e sem rastreio: o que o GET ao vivo diz."""
    if _troca._id_situacao(order) != SITUACAO_EM_ABERTO:
        raise BrindeRecusado("situacao_mudou")
    if _troca._nf_no_bling(order):
        raise BrindeRecusado("nf_emitida")
    if _troca._rastreio_no_bling(order):
        raise BrindeRecusado("rastreio")


async def _escolher_produto(
    cliente, tipo: str, lote: str | None, sku_pedido: str | None
) -> tuple[str, dict[str, Any]]:
    """O primeiro candidato ativo com saldo ao vivo >= 1. Sem nenhum, recusa."""
    candidatos = [sku_pedido.strip().lower()] if sku_pedido else skus_candidatos(tipo, lote)
    cache: dict = {}
    for sku in candidatos:
        try:
            produto = await _produto_exato(cliente, cache, sku)
        except _ConsultaFalhouError as e:
            raise BrindeRecusado("bling_indisponivel") from e
        saldo = _saldo(produto)
        if produto is not None and saldo is not None and saldo >= 1:
            return sku, produto
    raise BrindeRecusado("sem_saldo")


async def _aprovar_na_margem(session: AsyncSession, e: _Espelho, user: User, sku: str) -> None:
    """O pino `Aprovado` SÓ na linha do brinde: sem ele o robô reprovaria por ela.

    As outras linhas ficam como estão: o celular segue sendo julgado (05/11/2026). A
    linha de R$ 0,01 sem pino voltaria a contar no BOOL_OR do robô, também no pedido
    que já estava aprovado (daí o `aprovado_por` de quem aprovou).
    """
    pino = (e.pino or "").strip()
    await session.execute(
        update(BlingOrder)
        .where(
            BlingOrder.numero == e.numero,
            func.lower(func.btrim(BlingOrder.item_codigo)) == sku.strip().lower(),
            BlingOrder.status.is_(None),
        )
        .values(status=PINO_APROVADO, aprovado_por=e.aprovado_por or user.id, verificado=True)
        .execution_options(synchronize_session=False)
    )
    if pino != PINO_APROVADO:
        await record_margem_audit(
            session,
            acao="status",
            pedido_bling=e.numero,
            bling_id=e.bling_id,
            sku=sku,
            valor_antigo=pino or None,
            valor_novo=PINO_APROVADO,
            origem=ORIGEM_MARGEM,
            mudado_por=user.id,
        )


async def _confirmar(
    session: AsyncSession,
    cliente,
    order: dict[str, Any],
    *,
    integration_id: UUID,
    pedido: str,
    conversa_id: UUID | None,
    tipo: str,
    sku: str,
    e: _Espelho,
    user: User,
    ja_estava: bool,
    antes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """O brinde ESTÁ no pedido vivo: o espelho (a planilha da NF lê dele), o pino e o estado.

    Espelho que falhou = `incerto`, não `adicionado`: a NF seguiria sem a linha do
    brinde. O clique seguinte vê o brinde no GET e refaz só isto, sem PUT. Total ou
    parcelas divergentes também (`antes` = o último GET antes do PUT): o espelho e o
    pino valem (o brinde está lá, e o robô leria a linha), a NF não sai e o aviso fica
    no `erro`, que o painel mostra depois de recarregar. O `compensar_desconto` não foi
    testado no Bling real: esta é a defesa do 1º clique em pedido de cliente.
    """
    linha = await _linha(session, integration_id, pedido)
    uid = user.id
    try:
        async with session.begin_nested():
            await bling_orders_svc.upsert_order(session, order, client=cliente)
    except Exception as exc:  # noqa: BLE001
        # Só o tipo: a mensagem do SQLAlchemy traz os parâmetros (dados do comprador).
        logger.warning("atendimento_brinde_espelho_falhou", pedido=e.numero, err=type(exc).__name__)
        linha = await _gravar(
            session,
            linha,
            integration_id=integration_id,
            pedido=pedido,
            estado=INCERTO,
            tipo=tipo,
            sku=sku,
            bling_id=e.bling_id,
            por=uid,
            erro=TEXTOS["espelho_atrasado"],
        )
        await session.commit()
        return _saida(linha, ok=False, mensagem=TEXTOS["espelho_atrasado"], numero=e.numero)
    await _aprovar_na_margem(session, e, user, sku)
    aviso = divergencia_de_valores(order, antes)
    linha = await _gravar(
        session,
        linha,
        integration_id=integration_id,
        pedido=pedido,
        estado=INCERTO if aviso else ADICIONADO,
        tipo=tipo,
        sku=sku,
        bling_id=e.bling_id,
        por=uid,
        erro=aviso,
    )
    if aviso:
        mensagem = f"O brinde {sku} está no pedido {e.numero}. {aviso}"
    else:
        await _nota(
            session,
            integration_id,
            pedido,
            conversa_id,
            f"Brinde adicionado ao pedido {e.numero}: {sku} (por {_troca._nome(user)}).",
            uid,
        )
        mensagem = (
            f"O brinde {sku} já estava no pedido {e.numero}."
            if ja_estava
            else f"Brinde {sku} adicionado ao pedido {e.numero}."
        )
    # A resposta sai antes do que vem depois: um rollback lá expiraria a linha.
    saida = _saida(linha, ok=not aviso, mensagem=mensagem, numero=e.numero, ja_estava=ja_estava)
    await session.commit()
    if aviso:
        logger.warning("atendimento_brinde_valores_divergem", pedido=e.numero, sku=sku)
    else:
        logger.info("atendimento_brinde_adicionado", pedido=e.numero, sku=sku, ja_estava=ja_estava)
    # Depois do commit (falha aqui não desfaz nada): as Observações do painel e o
    # retrato da Margem, com a linha nova e o pino. O pino no retrato só na linha do
    # brinde: o refresh pula o pedido que já tem linha decidida.
    from app.services.atendimento import painel

    await painel.invalidar_observacoes(e.bling_id)
    await verificar_margem.refresh_silent(session, pedido_bling=e.numero)
    try:
        await session.execute(
            text(
                f"UPDATE {verificar_margem.SNAPSHOT_TABLE} "  # noqa: S608 — constante do módulo
                "SET bling_status_margem = :pino, "
                "    aprovado_por = CAST(:por AS uuid), "
                "    verificado = TRUE "
                "WHERE pedido_bling = :numero "
                "  AND lower(btrim(sku)) = :sku "
                "  AND bling_status_margem IS NULL"
            ),
            {
                "pino": PINO_APROVADO,
                "por": str(e.aprovado_por or uid),
                "numero": e.numero,
                "sku": sku.strip().lower(),
            },
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.warning(
            "atendimento_brinde_snapshot_falhou", pedido=e.numero, err=type(exc).__name__
        )
    return saida


async def _conferir_put(cliente, bling_id: int, tipo: str) -> dict[str, Any] | None:
    """Depois de um PUT sem resposta: o pedido, se algum GET já mostra o brinde."""
    for espera in ESPERAS_CONFERENCIA_S:
        if espera:
            await asyncio.sleep(espera)
        try:
            order = await cliente.get_order(int(bling_id))
        except Exception:  # noqa: BLE001, S112 — tenta o próximo
            continue
        if isinstance(order, dict) and brinde_nos_itens(order, tipo):
            return order
    return None


async def adicionar(
    session: AsyncSession,
    integration_id: UUID,
    pedido: str,
    user: User,
    sku: str | None = None,
) -> dict[str, Any]:
    """O botão. `BrindeRecusado` = nada mudou no pedido; `ok=False` = `incerto`."""
    pedido = (pedido or "").strip()
    await _lock(session, integration_id, pedido)
    oferta = await _oferta(session, integration_id, pedido)
    if oferta is None:
        raise BrindeRecusado("sem_oferta", status=404)
    linha = await _linha(session, integration_id, pedido)
    if linha is not None and linha.estado == ADICIONADO:
        return _saida(linha, ok=True, mensagem=TEXTOS["ja_adicionado"], ja_estava=True)
    if linha is not None and linha.estado == DISPENSADO:
        raise BrindeRecusado("dispensado")
    e = await _espelho(session, pedido)
    if e is None or e.bling_id is None:
        raise BrindeRecusado("ainda_nao_pago")
    tipo = await _tipo_do_pedido(session, integration_id, pedido, e.itens)
    if tipo is None:
        raise BrindeRecusado("sem_tipo")
    if sku and familia_do_sku(sku) != tipo:
        raise BrindeRecusado("sku_invalido", status=422)
    nf = await _nf_bloqueia(session, e.numero)
    if nf is not None:
        raise BrindeRecusado(nf)
    if (e.pino or "").strip() in PINOS_EM_ANALISE:
        raise BrindeRecusado("margem_em_analise")
    cliente = await _troca._cliente_bling(session)
    if cliente is None:
        raise BrindeRecusado("bling_indisponivel", "Sem integração do Bling ativa.")

    contexto = {
        "integration_id": integration_id,
        "pedido": pedido,
        "conversa_id": oferta.conversa_id,
        "tipo": tipo,
        "e": e,
        "user": user,
    }
    # 1º GET: o brinde já está lá (o `incerto` que valeu, ou alguém pôs à mão)?
    order = await _get(cliente, e.bling_id)
    presente = brinde_nos_itens(order, tipo)
    if presente:
        return await _confirmar(session, cliente, order, sku=presente, ja_estava=True, **contexto)
    _conferir_vivo(order)
    if (
        linha is not None
        and linha.estado == INCERTO
        and linha.em is not None
        and _agora() - linha.em < FOLGA_INCERTO
    ):
        raise BrindeRecusado("espere_incerto")

    vendidos = [str(i.get("codigo") or "") for i in _troca._itens_vivos(order)]
    sku_final, produto = await _escolher_produto(cliente, tipo, lote_do_vendido(vendidos), sku)

    # O ÚLTIMO GET antes do PUT: o corpo sai daqui.
    order = await _get(cliente, e.bling_id)
    presente = brinde_nos_itens(order, tipo)
    if presente:
        return await _confirmar(session, cliente, order, sku=presente, ja_estava=True, **contexto)
    _conferir_vivo(order)
    body = corpo_do_put(order, sku=sku_final, produto=produto, nome=_troca._nome(user))

    try:
        await cliente.update_order(int(e.bling_id), body)
    except httpx.HTTPStatusError as exc:
        st = exc.response.status_code
        if 400 <= st < 500 and st != 429:
            raise BrindeRecusado(
                "bling_recusou", _troca._erro_do_bling(exc).replace("a troca", "o brinde")
            ) from exc
        return await _put_incerto(session, cliente, linha, sku_final, antes=order, **contexto)
    except Exception:  # noqa: BLE001 — 429/503 (BlingCloudflareError), timeout, rede
        return await _put_incerto(session, cliente, linha, sku_final, antes=order, **contexto)

    # O PUT valeu. O GET de depois traz o pedido como o Bling o guardou.
    try:
        depois = await _get(cliente, e.bling_id)
    except BrindeRecusado:
        depois = {}
    if not brinde_nos_itens(depois, tipo):
        linha = await _gravar(
            session,
            linha,
            integration_id=integration_id,
            pedido=pedido,
            estado=INCERTO,
            tipo=tipo,
            sku=sku_final,
            bling_id=e.bling_id,
            por=user.id,
            erro=TEXTOS["espelho_atrasado"],
        )
        await session.commit()
        return _saida(linha, ok=False, mensagem=TEXTOS["espelho_atrasado"], numero=e.numero)
    # Não desfaz (o brinde entrou): `_confirmar` compara os valores e segura a NF.
    return await _confirmar(
        session, cliente, depois, sku=sku_final, ja_estava=False, antes=order, **contexto
    )


async def _put_incerto(
    session: AsyncSession,
    cliente,
    linha: AtendimentoBrinde | None,
    sku: str,
    *,
    antes: Mapping[str, Any],
    integration_id: UUID,
    pedido: str,
    conversa_id: UUID | None,
    tipo: str,
    e: _Espelho,
    user: User,
) -> dict[str, Any]:
    """O PUT falhou sem resposta clara: `incerto`, COMMIT e os GETs decidem. Nunca repete."""
    linha = await _gravar(
        session,
        linha,
        integration_id=integration_id,
        pedido=pedido,
        estado=INCERTO,
        tipo=tipo,
        sku=sku,
        bling_id=e.bling_id,
        por=user.id,
        erro=TEXTOS["incerto"],
    )
    await session.commit()
    logger.warning("atendimento_brinde_put_incerto", pedido=e.numero, sku=sku)
    visto = await _conferir_put(cliente, int(e.bling_id), tipo)
    if visto is None:
        return _saida(linha, ok=False, mensagem=TEXTOS["incerto"], numero=e.numero)
    await _lock(session, integration_id, pedido)
    return await _confirmar(
        session,
        cliente,
        visto,
        integration_id=integration_id,
        pedido=pedido,
        conversa_id=conversa_id,
        tipo=tipo,
        sku=brinde_nos_itens(visto, tipo) or sku,
        e=e,
        user=user,
        ja_estava=False,
        antes=antes,
    )


async def dispensar(
    session: AsyncSession, integration_id: UUID, pedido: str, user: User
) -> dict[str, Any]:
    """ "Não adicionar": solta a NF. O `incerto` confere primeiro (o brinde pode estar lá)."""
    pedido = (pedido or "").strip()
    await _lock(session, integration_id, pedido)
    oferta = await _oferta(session, integration_id, pedido)
    if oferta is None:
        raise BrindeRecusado("sem_oferta", status=404)
    linha = await _linha(session, integration_id, pedido)
    if linha is not None and linha.estado == ADICIONADO:
        raise BrindeRecusado("ja_adicionado")
    if linha is not None and linha.estado == INCERTO:
        raise BrindeRecusado("incerto_nao_dispensa")
    if linha is not None and linha.estado == DISPENSADO:
        return _saida(linha, ok=True, mensagem="Brinde já dispensado.", ja_estava=True)
    linha = await _gravar(
        session,
        linha,
        integration_id=integration_id,
        pedido=pedido,
        estado=DISPENSADO,
        tipo=linha.tipo if linha is not None else None,
        por=user.id,
    )
    await _nota(
        session,
        integration_id,
        pedido,
        oferta.conversa_id,
        f"Brinde não adicionado (por {_troca._nome(user)}): a NF segue sem ele.",
        user.id,
    )
    await session.commit()
    return _saida(linha, ok=True, mensagem="Brinde dispensado: a NF segue sem ele.")


async def expirar_vencidos(session: AsyncSession) -> int:
    """Uma vez só, passadas as 12 h: `expirado` + a nota na conversa.

    A trava da NF não depende disto (ela solta pelo relógio): aqui é o registro e
    o alerta. Sem commit: quem chama (`session_scope`) grava.
    """
    r = AtendimentoAutomacaoRegistro
    agora = _agora()
    entrou = func.min(BlingOrder.created_at)
    linhas = (
        await session.execute(
            select(
                r.integration_id,
                r.pedido,
                func.max(cast(r.conversa_id, String)).label("conversa"),
                func.max(BlingOrder.situacao).label("situacao"),
                func.max(BlingOrder.bling_id).label("bling_id"),
            )
            .join(BlingOrder, BlingOrder.numeroloja == r.pedido)
            .outerjoin(
                AtendimentoBrinde,
                and_(
                    AtendimentoBrinde.integration_id == r.integration_id,
                    AtendimentoBrinde.pedido == r.pedido,
                ),
            )
            .where(
                r.automacao.in_(AUTOMACOES_BRINDE),
                r.estado.in_(ESTADOS_OFERTA_BRINDE),
                or_(AtendimentoBrinde.id.is_(None), AtendimentoBrinde.estado == INCERTO),
            )
            .group_by(r.integration_id, r.pedido)
            .having(entrou <= agora - _prazo(), entrou >= agora - JANELA)
            .limit(MAX_EXPIRADOS_POR_PASSADA)
        )
    ).all()
    feitos = 0
    for x in linhas:
        try:
            async with session.begin_nested():
                travou = await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"atendimento_brinde:{x.integration_id}:{x.pedido}"},
                )
                if not travou:
                    continue
                linha = await _linha(session, x.integration_id, x.pedido)
                if linha is not None and linha.estado != INCERTO:
                    continue
                era_incerto = linha is not None
                resumo = await session.scalar(
                    select(AtendimentoPedidoComprador.itens_resumo).where(
                        AtendimentoPedidoComprador.integration_id == x.integration_id,
                        AtendimentoPedidoComprador.pedido == x.pedido,
                    )
                )
                await _gravar(
                    session,
                    linha,
                    integration_id=x.integration_id,
                    pedido=x.pedido,
                    estado=EXPIRADO,
                    tipo=brinde_do_resumo(resumo),
                    bling_id=int(x.bling_id) if x.bling_id else None,
                    erro=linha.erro if linha is not None else None,
                )
                # O pedido morto (cancelado) não tem NF para seguir: só o registro.
                if x.situacao not in _SITUACOES_MORTAS:
                    await _nota(
                        session,
                        x.integration_id,
                        x.pedido,
                        UUID(x.conversa) if x.conversa else None,
                        NOTA_EXPIROU_INCERTO if era_incerto else NOTA_EXPIROU,
                        None,
                    )
                feitos += 1
        except Exception as e:  # noqa: BLE001 — um pedido não derruba os outros
            logger.warning(
                "atendimento_brinde_expirar_falhou", pedido=x.pedido, err=type(e).__name__
            )
    if feitos:
        logger.info("atendimento_brinde_expirados", n=feitos)
    return feitos


# ── A tela ────────────────────────────────────────────────────────────────


def _sugestao(tipo: str | None, itens: list[dict[str, Any]]) -> str | None:
    if tipo is None:
        return None
    return skus_candidatos(tipo, lote_do_vendido(i.get("sku") for i in itens))[0]


def _horas(desde: datetime | None, agora: datetime) -> float | None:
    if desde is None:
        return None
    if desde.tzinfo is None:
        desde = desde.replace(tzinfo=UTC)
    return round((agora - desde).total_seconds() / 3600, 1)


async def bloco_do_painel(
    session: AsyncSession,
    *,
    integration_id: UUID | None,
    pedido: str | None,
    user: User | None,
) -> dict[str, Any] | None:
    """O bloco `brinde` do painel da conversa. Sem GET ao Bling (o clique confere).

    None = o pedido não tem oferta enviada, ou ainda não foi pago: nada aparece.
    """
    pedido = (pedido or "").strip()
    if integration_id is None or not pedido:
        return None
    if await _oferta(session, integration_id, pedido) is None:
        return None
    try:
        e = await _espelho(session, pedido)
    except BrindeRecusado as recusa:
        return {
            "integration_id": str(integration_id),
            "pedido": pedido,
            "tipo": None,
            "rotulo": None,
            "sku_sugerido": None,
            "estado": None,
            "pode_adicionar": False,
            "motivo": recusa.detail,
            "erro": None,
        }
    if e is None:
        return None
    linha = await _linha(session, integration_id, pedido)
    tipo = await _tipo_do_pedido(session, integration_id, pedido, e.itens)
    if linha is not None and linha.tipo:
        tipo = linha.tipo
    motivo: str | None = None
    if linha is not None and linha.estado == ADICIONADO:
        motivo = TEXTOS["ja_adicionado"]
    elif linha is not None and linha.estado == DISPENSADO:
        motivo = TEXTOS["dispensado"]
    elif tipo is None:
        motivo = TEXTOS["sem_tipo"]
    elif not _troca.pode_escrever(user):
        motivo = TEXTOS["sem_permissao"]
    elif (e.situacao or "").strip() != SITUACAO_EM_ABERTO:
        motivo = TEXTOS["situacao_mudou"]
    elif (e.pino or "").strip() in PINOS_EM_ANALISE:
        motivo = TEXTOS["margem_em_analise"]
    else:
        nf = await _nf_bloqueia(session, e.numero)
        if nf is not None:
            motivo = TEXTOS[nf]
    return {
        "integration_id": str(integration_id),
        "pedido": pedido,
        "numero_bling": e.numero,
        "tipo": tipo,
        "rotulo": ROTULOS.get(tipo or ""),
        "sku_sugerido": linha.sku if linha is not None and linha.sku else _sugestao(tipo, e.itens),
        "estado": linha.estado if linha is not None else None,
        "pode_adicionar": motivo is None,
        "motivo": motivo,
        "erro": linha.erro if linha is not None else None,
    }


async def pendentes(session: AsyncSession, scope: Any, user: User) -> dict[str, Any]:
    """A lista "Brindes a adicionar": os pagos em Em aberto sem decisão, e os alertas."""
    r = AtendimentoAutomacaoRegistro
    agora = _agora()
    q = (
        select(
            r.integration_id,
            r.pedido,
            r.plataforma,
            r.conversa_id,
            BlingOrder.numero,
            BlingOrder.loja,
            BlingOrder.created_at,
            AtendimentoBrinde.estado,
            AtendimentoBrinde.erro,
        )
        .join(BlingOrder, and_(BlingOrder.numeroloja == r.pedido, BlingOrder.item_index == 0))
        .outerjoin(
            AtendimentoBrinde,
            and_(
                AtendimentoBrinde.integration_id == r.integration_id,
                AtendimentoBrinde.pedido == r.pedido,
            ),
        )
        .where(
            r.automacao.in_(AUTOMACOES_BRINDE),
            r.estado.in_(ESTADOS_OFERTA_BRINDE),
            BlingOrder.situacao == SITUACAO_EM_ABERTO,
            BlingOrder.created_at >= agora - JANELA,
            or_(AtendimentoBrinde.id.is_(None), AtendimentoBrinde.estado == INCERTO),
        )
        .order_by(BlingOrder.created_at)
    )
    alertas_q = (
        select(AtendimentoBrinde, r.plataforma, r.conversa_id)
        .join(
            r,
            and_(
                r.integration_id == AtendimentoBrinde.integration_id,
                r.pedido == AtendimentoBrinde.pedido,
                r.automacao.in_(AUTOMACOES_BRINDE),
            ),
        )
        .where(
            AtendimentoBrinde.estado == EXPIRADO,
            AtendimentoBrinde.em >= agora - timedelta(hours=ALERTA_H),
        )
        .order_by(AtendimentoBrinde.em.desc())
    )
    if not scope.unrestricted:
        ids = scope.integration_ids or set()
        q = q.where(r.integration_id.in_(ids))
        alertas_q = alertas_q.where(AtendimentoBrinde.integration_id.in_(ids))
    linhas = (await session.execute(q)).all()
    alertas_linhas = (await session.execute(alertas_q)).all()

    numeros = [x.numero for x in linhas]
    pedidos_alerta = [a.pedido for a, *_ in alertas_linhas]
    itens: dict[str, list[dict[str, Any]]] = {}
    if numeros:
        for numero, sku, descricao, qtd in (
            await session.execute(
                select(
                    BlingOrder.numero,
                    BlingOrder.item_codigo,
                    BlingOrder.item_descricao,
                    BlingOrder.item_quantidade,
                )
                .where(BlingOrder.numero.in_(numeros))
                .order_by(BlingOrder.numero, BlingOrder.item_index)
            )
        ).all():
            if sku:
                itens.setdefault(numero, []).append(
                    {"sku": sku, "descricao": descricao, "quantidade": qtd}
                )
    nf_ocupada: set[str] = set()
    if numeros:
        nf_ocupada = {
            n
            for n, st in (
                await session.execute(
                    select(NfFaturamento.pedido_bling, NfFaturamento.status_faturamento).where(
                        NfFaturamento.pedido_bling.in_(numeros)
                    )
                )
            ).all()
            if (st or "").strip().lower() in ("ok", "processando")
        }
    lojas_alerta = (
        dict(
            (
                await session.execute(
                    select(BlingOrder.numeroloja, BlingOrder.loja).where(
                        BlingOrder.numeroloja.in_(pedidos_alerta)
                    )
                )
            ).all()
        )
        if pedidos_alerta
        else {}
    )
    ids_loja = {str(x.loja) for x in linhas if x.loja} | {
        str(v) for v in lojas_alerta.values() if v
    }
    nomes: dict[str, str | None] = {}
    if ids_loja:
        nomes = {
            str(b): nome
            for b, nome in (
                await session.execute(
                    select(StoreInfo.bling_store_id, StoreInfo.account_name).where(
                        StoreInfo.bling_store_id.in_(ids_loja)
                    )
                )
            ).all()
        }
    # A conversa do link: a da oferta, senão a do pedido na loja.
    sem_conversa = [x.pedido for x in linhas if x.conversa_id is None] + [
        a.pedido for a, _p, c in alertas_linhas if c is None
    ]
    conversas: dict[tuple[UUID, str], UUID] = {}
    if sem_conversa:
        for cid, iid, ped in (
            await session.execute(
                select(
                    AtendimentoConversa.id,
                    AtendimentoConversa.integration_id,
                    AtendimentoConversa.pedido_marketplace,
                ).where(AtendimentoConversa.pedido_marketplace.in_(sem_conversa))
            )
        ).all():
            if iid is not None:
                conversas.setdefault((iid, ped), cid)

    pode = _troca.pode_escrever(user)
    saida = []
    for x in linhas:
        tipo = await _tipo_do_pedido(session, x.integration_id, x.pedido, itens.get(x.numero, []))
        conversa = x.conversa_id or conversas.get((x.integration_id, x.pedido))
        motivo = None
        if tipo is None:
            motivo = TEXTOS["sem_tipo"]
        elif x.numero in nf_ocupada:
            motivo = TEXTOS["nf_emitida"]
        elif not pode:
            motivo = TEXTOS["sem_permissao"]
        saida.append(
            {
                "integration_id": str(x.integration_id),
                "pedido": x.pedido,
                "numero_bling": x.numero,
                "plataforma": x.plataforma,
                "loja": nomes.get(str(x.loja)) if x.loja else None,
                "tipo": tipo,
                "rotulo": ROTULOS.get(tipo or ""),
                "sku_sugerido": _sugestao(tipo, itens.get(x.numero, [])),
                "horas": _horas(x.created_at, agora),
                "horas_restantes": max(
                    0.0, round(BRINDE_SEGURA_NF_H - (_horas(x.created_at, agora) or 0.0), 1)
                ),
                "conversa_id": str(conversa) if conversa else None,
                "estado": x.estado,
                "erro": x.erro,
                "pode_adicionar": motivo is None,
                "motivo": motivo,
            }
        )
    alertas = []
    for a, plataforma, conversa_id in alertas_linhas:
        conversa = conversa_id or conversas.get((a.integration_id, a.pedido))
        loja = lojas_alerta.get(a.pedido)
        alertas.append(
            {
                "integration_id": str(a.integration_id),
                "pedido": a.pedido,
                "plataforma": plataforma,
                "loja": nomes.get(str(loja)) if loja else None,
                "rotulo": ROTULOS.get(a.tipo or ""),
                "horas": _horas(a.em, agora),
                "conversa_id": str(conversa) if conversa else None,
                "mensagem": NOTA_EXPIROU,
            }
        )
    return {"total": len(saida), "pendentes": saida, "alertas": alertas}
