"""TROCA DE PRODUTO no pedido em "Aguardando Cancelamento" (item 4, fase 4c, 07/10/2026).

O pedido caiu em 83955 por falta de estoque (`ag_cancelamento.classificar`
→ `sem_estoque`) e há um parecido com estoque (`troca_sugestoes`): o item em
falta sai e o parecido entra NO MESMO PEDIDO DO BLING — um PUT, o mesmo
mecanismo do robô de lote (`prioridade_estoque.aplicar_trocas_nos_itens`,
SEMPRE com `substituir=True`: item novo sem `id`, o Bling refaz a composição
do kit) —, a Margem fica aprovada por quem clicou e o pedido volta para Em
aberto (83955 → 9 → 6), com a NF de volta à fila do sweep.

QUEM TROCA (Eduardo, 07/10/2026):
  • nível 0 (o MESMO produto em outro lote: dg053.ci → dg053.sp) — o robô
    troca SOZINHO, sem aceite (`executar(..., automatica=True)`, chamado SÓ
    pelo cron `atendimento_troca_lote` — `troca_lote_auto`, que escolhe os
    pedidos e retoma as trocas dele paradas no meio), só com
    `atendimento_troca_ativa` E `atendimento_troca_lote_auto` e a lista
    piloto ESCRITA ("*" = todos); a pessoa também pode, sem a caixinha;
  • níveis 1 e 2 (outra cor, outro modelo) — só pessoa, com a CAIXINHA
    "o cliente aceitou a troca" (`confirmar`). A PROVA é opcional: a
    mensagem do cliente na mesma conversa (`davinci`), a resposta colada do
    Duoke com data e hora (`duoke`) ou nenhuma (`declarado`: vale o nome de
    quem clicou).
  Tudo nasce desligado e com a lista piloto (`atendimento_troca_pedidos`).

AS TRAVAS (desenho §3.3 com a crítica) rodam na prévia e de novo no clique:
  no banco — a chave, o piloto, o motivo (só `sem_estoque`; o item escolhido
  é o que falta, e só ele), a plataforma (só a Shopee tem conferência ao
  vivo), NF emitida ou na fila (inclusive o comando falho que o
  `nf_recuperar` ainda devolve à fila), etiqueta, rastreio, pedido que já
  saiu, pedido em dobro, prazo de envio, kit com lotes misturados e a
  sugestão — a regra da 4b SEM o estoque do DaVinci: o saldo quem diz é o
  Bling ao vivo (crítica M13);
  ao vivo, NESTA ordem (crítica M3) — a Shopee (`READY_TO_SHIP` e sem NF no
  pedido), o produto novo (ativo e com saldo; a peça de kit dividida com
  outro item do pedido confere o saldo dela), os outros itens (nenhum
  negativo: um item por troca), o pedido em dobro no Bling e, POR ÚLTIMO, o
  `get_order` (83955, sem NF, sem rastreio, os itens do espelho) — o corpo
  do PUT é o desse último GET. 429, timeout ou 5xx = `bling_indisponivel`
  (`plataforma_indisponivel` na Shopee): nada é escrito.

OS PASSOS DO CLIQUE. Cada um grava em `passos` e faz COMMIT — o fato no
Bling já aconteceu. Em cada passo vale o `pg_advisory_xact_lock` do pedido, e
a marca `em_execucao_ate` segura o `retomar` enquanto alguém conduz (o lock
do banco cai a cada commit — crítica M5):
  1. INSERT `iniciada` (uma troca aberta por pedido; a `idem_key` repetida
     devolve a que já existe, sem refazer nada);
  2. as travas ao vivo (falha = `abortada` + 409), com o lock de cada peça
     que entra — o SKU novo e os componentes do kit novo (duas trocas não
     gastam a mesma peça, nem de dentro de kits diferentes — crítica M14);
  3. UM PUT (os itens + a linha "TROCA a -> b (...)" nas Observações). 4xx
     = o Bling recusou a venda inteira (`abortada`); qualquer outra falha =
     `incerta`, e GETs espaçados decidem (crítica M4) — nunca repete o PUT;
  4. no banco: o espelho `bling_orders` (com o custo novo), a trilha `sku` e
     — só com pessoa — o pino `Aprovado` (+ trilha `status`), ANTES do
     PATCH para 6: o robô da Margem só pega `Pendente` ou vazio → `item_trocado`;
  5. GET; 83955 → PATCH 9 (`em_atendido`) → PATCH 6; "já estava" conta como
     feito; outra situação = `abortada` (`situacao_mudou_no_meio`), com nota.
     Depois do 6: o espelho e a trilha `situacao` 83955→6 → `em_aberto`;
  6. a marca da NF limpa COM COMPARE-AND-SET (crítica A3: só se ainda é a
     mesma 'sem_estoque'/'restricao' e sem NF nem comando na fila) →
     `nf_liberada`. O sweep pega em até 2 min e confere o estoque de novo;
  7. a nota na conversa, as Observações do painel esquecidas e a etiqueta
     recalculada (falha aqui não desfaz nada) → `concluida`.

O RETOMAR (`retomar`): sempre GET primeiro e só PARA FRENTE; NUNCA faz PUT
(o corpo leva a situação do GET e desfaria um PATCH). Em `iniciada`/`incerta`
o item novo no Bling refaz o passo 4; o antigo aborta. Antes de mover a
situação, confere de novo a Shopee e a NF.

O ROBÔ (nível 0 automático) NÃO aprova a Margem — não há pessoa: o pedido
volta para 6 com o pino como estava e o robô da Margem faz a conta com o
custo do lote novo, como depois da troca do robô de lote.

Texto de comprador nunca vai para o log — só ids, códigos e SKUs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoTroca,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Logistica,
    NfCommand,
    NfEtiquetaArquivo,
    NfFaturamento,
    NfNota,
    StoreInfo,
    User,
    UserRole,
)
from app.services.atendimento import etiqueta_fatos, troca_sugestoes
from app.services.atendimento.ag_cancelamento import (
    NF_RESTRICAO,
    NF_SEM_ESTOQUE,
    SEM_ESTOQUE,
    Motivo,
    classificar,
    status_na_plataforma,
)
from app.services.atendimento.constantes import AUTOR_CLIENTE, AUTOR_LOJA, TIPO_NOTA
from app.services.bling_situacoes import (
    SITUACAO_AGUARDANDO_CANCELAMENTO,
    SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
    SITUACAO_ATENDIDO,
    SITUACAO_CANCELADO,
    SITUACAO_EM_ABERTO,
)
from app.services.logistica_bling import build_observacoes_put_body, compose_observacoes
from app.services.margem_audit import record_margem_audit
from app.services.prioridade_estoque import _tag_de, aplicar_trocas_nos_itens
from app.services.prioridade_estoque_movimentos import (
    _SITUACOES_MORTAS,
    _pedido_saiu,
    pecas_que_mudam,
)
from app.services.verificar_margem import patch_status_for_pedido

logger = structlog.get_logger()

# ── Estados (`atendimento_trocas.estado`) ─────────────────────────────────
INICIADA = "iniciada"
ITEM_TROCADO = "item_trocado"
EM_ATENDIDO = "em_atendido"
EM_ABERTO = "em_aberto"
NF_LIBERADA = "nf_liberada"
CONCLUIDA = "concluida"
ABORTADA = "abortada"
INCERTA = "incerta"
ESTADOS = (
    INICIADA,
    ITEM_TROCADO,
    EM_ATENDIDO,
    EM_ABERTO,
    NF_LIBERADA,
    CONCLUIDA,
    ABORTADA,
    INCERTA,
)
FECHADOS = frozenset({CONCLUIDA, ABORTADA})

# ── A prova do aceite (`aceite_fonte`; NULL só no nível 0) ────────────────
ACEITE_DAVINCI = "davinci"
ACEITE_DUOKE = "duoke"
ACEITE_DECLARADO = "declarado"

# A trilha da Margem (`margem_audit.origem`) de tudo o que a troca muda.
ORIGEM_TROCA = "atendimento_troca"
# Quem "clicou" na troca do robô (`criado_por` NULL).
NOME_ROBO = "Robô de lote (troca automática)"
PINO_APROVADO = "Aprovado"

# A Shopee: só o pedido pronto para enviar, sem etiqueta gerada (premissa 6).
SHOPEE_PRONTO = "READY_TO_SHIP"
SHOPEE_CANCELADO = frozenset({"IN_CANCEL", "CANCELLED", "CANCELED"})

# A marca `em_execucao_ate`: renovada a cada passo. Um processo que morreu no
# meio libera o `retomar` depois disto.
VEZ = timedelta(minutes=5)
# Depois de um PUT sem resposta: os GETs de conferência, espaçados (s). O
# primeiro é na hora; só com todos vendo o item antigo a troca aborta (M4).
ESPERAS_CONFERENCIA_S: tuple[float, ...] = (0.0, 2.0, 5.0)
# A resposta colada do Duoke: teto do texto e a folga do relógio (futuro).
ACEITE_TEXTO_MAX = 2000
_FOLGA_RELOGIO = timedelta(minutes=5)
# Quantas mensagens do cliente a prévia oferece como prova.
MAX_ACEITES_POSSIVEIS = 10
_BRT = ZoneInfo("America/Sao_Paulo")

# ── As frases das travas (o `detail` do 409 e o texto da prévia) ──────────
TEXTOS = {
    "troca_desligada": "A troca de produto está desligada (ATENDIMENTO_TROCA_ATIVA).",
    "troca_lote_auto_desligada": (
        "A troca automática de lote está desligada (ATENDIMENTO_TROCA_LOTE_AUTO)."
    ),
    "pedido_fora_do_piloto": "Este pedido não está na lista piloto da troca de produto.",
    "piloto_vazio_no_automatico": (
        "A troca automática de lote só roda com a lista piloto escrita "
        "(ATENDIMENTO_TROCA_PEDIDOS; '*' = todos)."
    ),
    "pedido_nao_encontrado": "O pedido não está no espelho do Bling do DaVinci.",
    "conversa_de_outro_pedido": "A conversa escolhida não é deste pedido.",
    "motivo_nao_permite": (
        "Só o pedido em Aguardando Cancelamento por falta de estoque pode trocar de produto."
    ),
    "plataforma_sem_conferencia": (
        "Por enquanto só a Shopee: é a única plataforma com a conferência ao vivo do pedido."
    ),
    "nf_emitida": "O pedido já tem NF (emitida ou em emissão): a troca mudaria o que foi faturado.",
    "em_fila_nf": "O pedido está na fila da NF: espere a fila ou tire o comando antes.",
    "etiqueta_gerada": "A etiqueta do pedido já foi gerada.",
    "rastreio": "O pedido já tem rastreio.",
    "pedido_saiu": "O pacote já saiu.",
    "pedido_em_dobro": "Há outro pedido vivo no Bling com o mesmo número da loja.",
    "prazo_vencido": "O prazo de envio na plataforma venceu (ou vence em menos da folga).",
    "sugestao_invalida": "O produto escolhido não é um parecido válido para este item.",
    "custo_acima": "O produto escolhido custa mais que o teto da troca.",
    "custo_abaixo_piso": "O produto escolhido custa bem menos: seria rebaixar o produto.",
    "kit_lotes_misturados": "Kit com lotes misturados: fica fora da troca.",
    "outro_item_sem_estoque": (
        "Outro item do pedido também está sem estoque: uma troca não resolve o pedido."
    ),
    "nivel_exige_pessoa": "Outra cor ou outro modelo: só pessoa troca, com o aceite do cliente.",
    "bling_indisponivel": (
        "O Bling não respondeu agora — tente de novo em instantes. Nada foi escrito."
    ),
    "plataforma_indisponivel": (
        "A Shopee não respondeu sobre o pedido agora — tente de novo em instantes. "
        "Nada foi escrito."
    ),
    "situacao_mudou": "O pedido já não está em Aguardando Cancelamento no Bling.",
    "item_divergente": "Os itens do pedido no Bling não batem com o DaVinci.",
    "produto_novo_inativo": "O produto novo não está ativo no Bling.",
    "saldo_insuficiente": "O produto novo não tem saldo no Bling para a quantidade do pedido.",
    "cancelado_na_plataforma": "O pedido está cancelado (ou em cancelamento) na Shopee.",
    "plataforma_status": "O pedido não está pronto para enviar na Shopee.",
    "peca_em_disputa": "Outra troca está conferindo este produto agora — tente de novo.",
    "aceite_obrigatorio": "Marque que o cliente aceitou a troca.",
    "aceite_invalido": "A prova do aceite não vale para esta troca.",
    "previa_mudou": "O pedido mudou desde a prévia — confira a prévia de novo.",
    "troca_em_andamento": "Já há uma troca em andamento neste pedido.",
    "troca_nao_encontrada": "Troca não encontrada.",
    "idem_key_reusada": "Esta chave de envio já foi usada em outro pedido.",
    "bling_recusou": "O Bling recusou a troca. Nada mudou no pedido.",
    "put_nao_aplicado": "O Bling não trocou o item — nada mudou; pode tentar de novo.",
    "situacao_mudou_no_meio": (
        "O pedido mudou de situação no meio da troca: uma pessoa decide o que fazer."
    ),
    "erro_interno": "A troca parou por um erro do DaVinci: use Retomar.",
}


class TrocaRecusada(Exception):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """A troca não pôde ser feita. `code` é estável (a tela traduz); `status` o HTTP."""

    def __init__(
        self,
        code: str,
        detail: str | None = None,
        *,
        status: int = 409,
        troca_id: UUID | None = None,
    ) -> None:
        self.code = code
        self.detail = detail or TEXTOS.get(code, code)
        self.status = status
        self.troca_id = troca_id
        super().__init__(code)


@dataclass(frozen=True)
class Aceite:
    """A prova do aceite que veio da tela (tudo opcional; ver `_validar_aceite`)."""

    mensagem_aceite_id: UUID | None = None
    fonte: str | None = None
    texto: str | None = None
    em: datetime | None = None


@dataclass
class _Pedido:
    """O pedido do Bling com tudo o que as travas leem (uma consulta por fato)."""

    numero: str
    bling_id: int | None
    numeroloja: str | None
    loja: str | None
    data: datetime | None
    prazo: datetime | None
    pino: str | None
    # [{sku, quantidade, produto_id, descricao}] — o mesmo SKU em duas linhas soma.
    itens: list[dict[str, Any]]
    pedido: etiqueta_fatos.PedidoBling | None
    motivo: Motivo | None
    conversa: AtendimentoConversa | None
    plataforma: str | None = None
    integration_id: UUID | None = None
    loja_info: Any = None
    nf_status: str | None = None
    nf_erro: str | None = None
    nf_etiqueta: str | None = None

    def item(self, sku: str) -> dict[str, Any] | None:
        low = (sku or "").strip().lower()
        return next((i for i in self.itens if i["sku"].lower() == low), None)


@dataclass
class _Escolha:
    """O produto novo pela regra da 4b (`troca_sugestoes.candidatos`)."""

    nivel: int
    mesmo_produto: bool
    nome: str | None
    custo_antigo: Decimal | None
    custo_novo: Decimal | None
    dif_custo_pct: float | None


@dataclass
class _Vivo:
    """O que a conferência ao vivo leu (o corpo do PUT sai do `order`)."""

    order: dict[str, Any] = field(default_factory=dict)
    produto_id: int | None = None
    nome_novo: str | None = None
    saldo: Decimal | None = None
    valor_unitario: Decimal | None = None
    quantidade: int | None = None
    codigo_antigo: str | None = None


def _trava(code: str, ok: bool, texto: str | None = None) -> dict[str, Any]:
    return {"code": code, "ok": ok, "texto": texto or TEXTOS.get(code, code)}


def _agora() -> datetime:
    return datetime.now(UTC)


def _nome(user: User | None) -> str:
    if user is None:
        return NOME_ROBO
    return (user.name or "").strip() or (user.email or "").strip() or str(user.id)


def _dec(valor: Any, casas: str = "0.01") -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        return Decimal(str(valor)).quantize(Decimal(casas))
    except (InvalidOperation, ValueError):
        return None


def _hora(quando: datetime | None) -> str:
    """dd/mm HH:MM no horário de Brasília."""
    if quando is None:
        return "?"
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return quando.astimezone(_BRT).strftime("%d/%m %H:%M")


# ── Chaves ────────────────────────────────────────────────────────────────


# A lista piloto com "*": todos os pedidos, DITO com todas as letras (o robô
# não aceita a lista vazia — `conferir_chaves`).
PILOTO_TODOS = "*"


def pedidos_do_piloto() -> frozenset[str]:
    bruto = get_settings().atendimento_troca_pedidos or ""
    return frozenset(n.strip() for n in bruto.split(",") if n.strip())


def no_piloto(numero: str) -> bool:
    """Lista piloto vazia (ou com "*") = todos; preenchida = só os dela."""
    lista = pedidos_do_piloto()
    return not lista or PILOTO_TODOS in lista or numero.strip() in lista


def piloto_preenchido() -> bool:
    """A lista piloto foi escrita (os números, ou "*" para todos)? O robô só roda assim."""
    return bool(pedidos_do_piloto())


def conferir_ativa() -> None:
    """A chave geral: desligada, nada da troca roda (nem o retomar) e a tabela nem é lida."""
    if not get_settings().atendimento_troca_ativa:
        raise TrocaRecusada("troca_desligada")


def conferir_chaves(numero: str, *, automatica: bool = False) -> None:
    """A chave da troca (e a do robô), e o piloto — antes de qualquer consulta.

    O robô (decisão (e) do dono: a primeira troca é acompanhada) pede a lista
    piloto ESCRITA: ligar as duas chaves e esquecer a lista não solta o robô
    em todos os pedidos de uma vez — para todos, `ATENDIMENTO_TROCA_PEDIDOS=*`.
    A pessoa segue a regra de sempre (vazia = todos, §3.9 do desenho).
    """
    s = get_settings()
    conferir_ativa()
    if automatica and not s.atendimento_troca_lote_auto:
        raise TrocaRecusada("troca_lote_auto_desligada")
    if automatica and not piloto_preenchido():
        raise TrocaRecusada("piloto_vazio_no_automatico")
    if not no_piloto(numero):
        raise TrocaRecusada("pedido_fora_do_piloto")


# ── O pedido ──────────────────────────────────────────────────────────────


async def _carregar(session: AsyncSession, numero: str, conversa_id: UUID | None = None) -> _Pedido:
    """O pedido do espelho, o motivo do 83955, a loja, a conversa e a marca da NF."""
    linhas = (
        await session.execute(
            select(
                BlingOrder.bling_id,
                BlingOrder.numeroloja,
                BlingOrder.loja,
                BlingOrder.data,
                BlingOrder.marketplace_ship_deadline,
                BlingOrder.status,
                BlingOrder.item_codigo,
                BlingOrder.item_quantidade,
                BlingOrder.item_produto_id,
                BlingOrder.item_descricao,
            )
            .where(BlingOrder.numero == numero)
            .order_by(BlingOrder.item_index)
        )
    ).all()
    if not linhas:
        raise TrocaRecusada("pedido_nao_encontrado", status=404)
    itens: list[dict[str, Any]] = []
    for r in linhas:
        sku = (r.item_codigo or "").strip()
        if not sku:
            continue
        atual = next((i for i in itens if i["sku"].lower() == sku.lower()), None)
        if atual is None:
            itens.append(
                {
                    "sku": sku,
                    "quantidade": int(r.item_quantidade or 1),
                    "produto_id": int(r.item_produto_id) if r.item_produto_id else None,
                    "descricao": r.item_descricao,
                }
            )
        else:
            atual["quantidade"] += int(r.item_quantidade or 1)
    primeira = linhas[0]
    conversas = await etiqueta_fatos.conversas_do_pedido_bling(session, numero)
    conversa = conversas[0] if conversas else None
    if conversa_id is not None:
        conversa = next((c for c in conversas if c.id == conversa_id), None)
        if conversa is None:
            raise TrocaRecusada("conversa_de_outro_pedido", status=422)
    pedido = await etiqueta_fatos.pedido_bling(session, numero)
    motivo = classificar(
        pedido, status_plataforma=status_na_plataforma(conversa.dados) if conversa else None
    )
    p = _Pedido(
        numero=numero,
        bling_id=next((int(r.bling_id) for r in linhas if r.bling_id), None),
        numeroloja=next((r.numeroloja for r in linhas if (r.numeroloja or "").strip()), None),
        loja=primeira.loja,
        data=next((r.data for r in linhas if r.data), None),
        prazo=next(
            (r.marketplace_ship_deadline for r in linhas if r.marketplace_ship_deadline), None
        ),
        pino=next((r.status for r in linhas if r.status), None),
        itens=itens,
        pedido=pedido,
        motivo=motivo,
        conversa=conversa,
    )
    if p.loja:
        info = (
            await session.execute(
                select(StoreInfo).where(StoreInfo.bling_store_id == str(p.loja)).limit(1)
            )
        ).scalar_one_or_none()
        if info is not None:
            p.loja_info = info
            p.plataforma = etiqueta_fatos.normalizar_plataforma(info.platform)
            p.integration_id = info.integration_id
    if p.plataforma is None and conversa is not None:
        p.plataforma = conversa.plataforma
    if p.integration_id is None and conversa is not None:
        p.integration_id = conversa.integration_id
    nf = (
        await session.execute(select(NfFaturamento).where(NfFaturamento.pedido_bling == numero))
    ).scalar_one_or_none()
    if nf is not None:
        p.nf_status = (nf.status_faturamento or "").strip() or None
        p.nf_erro = nf.erro_faturamento
        p.nf_etiqueta = (nf.status_etiqueta or "").strip() or None
    return p


async def _em_fila_nf(session: AsyncSession, numero: str) -> bool:
    """Comando de NF ativo do pedido — ou falho que o `nf_recuperar` ainda devolve à fila.

    Toda a etapa do faturamento (`nf_recuperar._ETAPA_FATURAMENTO`: a
    planilha `import_avulsa` e as emissões que vêm depois dela), não só a
    planilha: com a emissão na fila, a NF sairia com o item velho.
    """
    # Import tardio: o router da NF puxa a esteira inteira.
    from app.routers.nf import _em_fila
    from app.services.nf_recuperar import _ETAPA_FATURAMENTO, _MAX_TENTATIVAS, _RETRY_JANELA

    for action in _ETAPA_FATURAMENTO:
        if numero in await _em_fila(session, action):
            return True
    falhos = (
        await session.execute(
            select(NfCommand.numeros).where(
                NfCommand.action.in_(_ETAPA_FATURAMENTO),
                NfCommand.status == "failed",
                NfCommand.attempts < _MAX_TENTATIVAS,
                NfCommand.completed_at.is_not(None),
                NfCommand.completed_at >= _agora() - _RETRY_JANELA,
            )
        )
    ).scalars()
    return any(numero in [str(n) for n in (arr or [])] for arr in falhos)


async def _tem_nf(session: AsyncSession, numero: str) -> bool:
    return (
        await session.execute(select(NfNota.id).where(NfNota.pedido_bling == numero).limit(1))
    ).first() is not None


def _lotes(sku: str) -> set[str]:
    return {t for t in (_tag_de(p.strip().lower()) for p in sku.split("+") if p.strip()) if t}


def _cortar_dif(valor: float | None) -> float | None:
    return round(valor, 1) if valor is not None else None


async def travas_do_pedido(session: AsyncSession, p: _Pedido) -> list[dict[str, Any]]:
    """As travas do banco que valem para o PEDIDO inteiro (não dependem do produto).

    A plataforma (só a Shopee na v1), a NF (emitida ou na fila), a etiqueta,
    o rastreio, o pacote que saiu, o pedido em dobro e o prazo de envio —
    as mesmas da troca, na mesma ordem. A oferta (`troca_oferta`) e o botão
    "Trocar" (`situacao_da_troca`) conferem estas antes: oferecer o que a
    troca recusa com certeza seria prometer à toa.
    """
    s = get_settings()
    travas: list[dict[str, Any]] = []
    travas.append(
        _trava(
            "plataforma_sem_conferencia",
            p.plataforma == "shopee",
            None if p.plataforma != "shopee" else "Shopee: conferência ao vivo do pedido.",
        )
    )
    nf_emitida = (p.nf_status or "").lower() in ("ok", "processando") or await _tem_nf(
        session, p.numero
    )
    travas.append(_trava("nf_emitida", not nf_emitida, None if nf_emitida else "Sem NF."))
    fila = await _em_fila_nf(session, p.numero)
    travas.append(_trava("em_fila_nf", not fila, None if fila else "Fora da fila da NF."))
    etiqueta = (
        bool(p.nf_etiqueta)
        or (
            await session.execute(
                select(NfEtiquetaArquivo.id)
                .where(NfEtiquetaArquivo.pedido_bling == p.numero)
                .limit(1)
            )
        ).first()
        is not None
    )
    travas.append(
        _trava("etiqueta_gerada", not etiqueta, None if etiqueta else "Sem etiqueta gerada.")
    )
    rastreio = (
        await session.execute(
            select(Logistica.id)
            .where(
                Logistica.pedido_bling == p.numero,
                or_(
                    func.coalesce(func.trim(Logistica.rastreio), "") != "",
                    func.coalesce(func.trim(Logistica.rastreio_17track), "") != "",
                ),
            )
            .limit(1)
        )
    ).first() is not None
    travas.append(_trava("rastreio", not rastreio, None if rastreio else "Sem rastreio."))
    saiu = await _pedido_saiu(session, p.numero)
    travas.append(_trava("pedido_saiu", not saiu, None if saiu else "O pacote não saiu."))
    dobro = False
    if p.numeroloja:
        # Vivo = nem cancelado nem EXCLUÍDO no Bling (o webhook `pedido.exclusao`
        # carimba 'excluido' no espelho): o excluído e reimportado não é dobro.
        dobro = (
            await session.execute(
                select(BlingOrder.numero)
                .where(
                    BlingOrder.numeroloja == p.numeroloja,
                    BlingOrder.numero != p.numero,
                    (BlingOrder.loja == p.loja) if p.loja else BlingOrder.loja.is_(None),
                    or_(
                        BlingOrder.situacao.is_(None),
                        BlingOrder.situacao.not_in(_SITUACOES_MORTAS),
                    ),
                )
                .limit(1)
            )
        ).first() is not None
    travas.append(
        _trava("pedido_em_dobro", not dobro, None if dobro else "Um pedido só com este número.")
    )
    folga = timedelta(hours=float(s.atendimento_troca_folga_prazo_horas))
    vencido = p.prazo is not None and p.prazo < _agora() + folga
    travas.append(
        _trava(
            "prazo_vencido",
            not vencido,
            None
            if vencido
            else (f"Enviar até {_hora(p.prazo)}." if p.prazo else "Sem prazo de envio gravado."),
        )
    )
    return travas


async def _travas_banco(
    session: AsyncSession,
    p: _Pedido,
    sku_antigo: str,
    sku_novo: str,
    *,
    automatica: bool,
    cat: troca_sugestoes.Catalogo,
) -> tuple[list[dict[str, Any]], _Escolha | None]:
    """As travas do banco, TODAS (a prévia mostra cada uma) + o produto novo pela regra da 4b."""
    s = get_settings()
    travas: list[dict[str, Any]] = []
    antigo = sku_antigo.strip().lower()
    item = p.item(sku_antigo)

    # O motivo: falta de estoque viva, do item escolhido — e só dele.
    m = p.motivo
    em_falta = [x.lower() for x in (m.skus if m else ())]
    if m is None or m.codigo != SEM_ESTOQUE:
        travas.append(_trava("motivo_nao_permite", False))
    elif antigo not in em_falta or item is None:
        travas.append(
            _trava(
                "motivo_nao_permite",
                False,
                "O item escolhido não é o que está em falta neste pedido.",
            )
        )
    else:
        travas.append(_trava("motivo_nao_permite", True, "Falta de estoque do item escolhido."))
    outros = [x for x in em_falta if x != antigo]
    travas.append(
        _trava(
            "outro_item_sem_estoque",
            not outros,
            f"Outro item também está em falta: {', '.join(outros)}."
            if outros
            else "Só este item falta.",
        )
    )
    travas += await travas_do_pedido(session, p)
    misturado = len(_lotes(sku_antigo)) > 1 or len(_lotes(sku_novo)) > 1
    travas.append(
        _trava(
            "kit_lotes_misturados",
            not misturado,
            None if misturado else "Sem kit de lotes misturados.",
        )
    )

    # A sugestão: a regra da 4b, sem o estoque do DaVinci (o saldo é ao vivo).
    escolha: _Escolha | None = None
    qtd = item["quantidade"] if item else 1
    cands = troca_sugestoes.candidatos(
        cat,
        sku_antigo.strip(),
        qtd,
        teto_pct=float(s.atendimento_troca_teto_custo_pct),
        piso_n2_pct=float(s.atendimento_troca_piso_nivel2_pct),
    )
    sug = next((c for c in cands if c.sku.strip().lower() == sku_novo.strip().lower()), None)
    if sug is None:
        travas.append(_trava("sugestao_invalida", False))
    elif sug.motivo_fora in (
        troca_sugestoes.FORA_CUSTO_ACIMA,
        troca_sugestoes.FORA_CUSTO_ABAIXO_PISO,
    ):
        travas.append(_trava(sug.motivo_fora, False))
    else:
        travas.append(_trava("sugestao_invalida", True, "Parecido válido pela regra da troca."))
    if sug is not None:
        o = cat.produtos.get(antigo)
        c = cat.produtos.get(sku_novo.strip().lower())
        escolha = _Escolha(
            nivel=sug.nivel,
            mesmo_produto=sug.mesmo_produto,
            nome=sug.nome or None,
            custo_antigo=_dec(o.custo_proprio, "0.0001") if o else None,
            custo_novo=_dec(c.custo_proprio, "0.0001") if c else None,
            dif_custo_pct=_cortar_dif(sug.dif_custo_pct),
        )
        if automatica and sug.nivel != troca_sugestoes.NIVEL_LOTE:
            travas.append(_trava("nivel_exige_pessoa", False))
    return travas, escolha


def _id_no_catalogo(cat: troca_sugestoes.Catalogo, sku: str) -> int:
    produto = cat.produtos.get(sku.strip().lower())
    return int(produto.bling_product_id or 0) if produto is not None else 0


def _primeira_falha(travas: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return next((t for t in travas if t.get("ok") is False), None)


# ── Os clientes (os testes trocam estes dois) ─────────────────────────────


async def _cliente_bling(session: AsyncSession):
    """O cliente do Bling da conta principal (o mesmo do painel), num SAVEPOINT."""
    from app.services.atendimento import painel

    async with session.begin_nested():
        return await painel._cliente_bling(session)


async def _cliente_shopee(session: AsyncSession, p: _Pedido):
    """O cliente Shopee da loja do pedido (cadastro de Lojas; senão a conversa).

    O do atendimento (`clientes.cliente_da_integracao`): o refresh token da
    Shopee é de uso único, e o token renovado no meio da troca vai para uma
    sessão PRÓPRIA (commit na hora, sob a `token_refresh_lock`) — o rollback
    da troca (`_erro_interno`, a exceção da prévia) não o leva junto, e o
    sync de 2 em 2 min não renova a mesma loja ao mesmo tempo.
    """
    from app.services.atendimento import clientes

    integ = None
    for iid in (p.integration_id, p.conversa.integration_id if p.conversa else None):
        if iid is None:
            continue
        integ = await session.get(Integration, iid)
        if integ is not None and integ.platform == IntegrationPlatform.SHOPEE:
            break
        integ = None
    if integ is None:
        return None
    return await clientes.cliente_da_integracao(integ)


# ── A conferência ao vivo ─────────────────────────────────────────────────


class _Indisponivel(Exception):  # noqa: N818
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _id_situacao(order: Mapping[str, Any]) -> str:
    return str((order.get("situacao") or {}).get("id") or "").strip()


def _nf_no_bling(order: Mapping[str, Any]) -> bool:
    nf = order.get("notaFiscal")
    try:
        return isinstance(nf, Mapping) and int(nf.get("id") or 0) > 0
    except (TypeError, ValueError):
        return False


def _rastreio_no_bling(order: Mapping[str, Any]) -> bool:
    transporte = order.get("transporte") if isinstance(order.get("transporte"), Mapping) else {}
    for vol in transporte.get("volumes") or []:
        if isinstance(vol, Mapping) and str(vol.get("codigoRastreamento") or "").strip():
            return True
    return False


def _itens_vivos(order: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [i for i in (order.get("itens") or []) if isinstance(i, Mapping)]


def _quantidades(itens: Sequence[Mapping[str, Any]]) -> Counter:
    soma: Counter = Counter()
    for it in itens:
        cod = str(it.get("codigo") or "").strip().lower()
        if cod:
            try:
                soma[cod] += int(float(it.get("quantidade") or 0))
            except (TypeError, ValueError):
                soma[cod] += 0
    return soma


def estado_do_item(order: Mapping[str, Any], sku_antigo: str, sku_novo: str) -> str:
    """'antigo' (o PUT não valeu), 'novo' (valeu) ou 'outro' (nenhum dos dois). PURA.

    O PUT troca TODAS as linhas do código antigo: antigo presente = não trocou.
    """
    codigos = set(_quantidades(_itens_vivos(order)))
    if sku_antigo.strip().lower() in codigos:
        return "antigo"
    if sku_novo.strip().lower() in codigos:
        return "novo"
    return "outro"


async def _shopee_ao_vivo(session: AsyncSession, p: _Pedido) -> list[dict[str, Any]]:
    """Status do pedido na Shopee e a NF dele lá (crítica M2) — UM GET."""
    cliente = await _cliente_shopee(session, p)
    if cliente is None or not p.numeroloja:
        raise _Indisponivel("plataforma_indisponivel")
    try:
        detalhe = await cliente.get_order_detail_completo(p.numeroloja)
    except Exception as e:  # noqa: BLE001 — erro da Shopee = "não sei", nunca "cancelado"
        logger.info("atendimento_troca_shopee_falhou", pedido=p.numero, err=type(e).__name__)
        raise _Indisponivel("plataforma_indisponivel") from e
    if not detalhe:
        # A Shopee não devolveu o pedido: não sei (crítica M7), não é "cancelado".
        raise _Indisponivel("plataforma_indisponivel")
    status = str(detalhe.get("order_status") or "").strip().upper()
    travas = []
    if status in SHOPEE_CANCELADO:
        travas.append(_trava("cancelado_na_plataforma", False, f"Shopee: {status}."))
    elif status != SHOPEE_PRONTO:
        travas.append(
            _trava(
                "plataforma_status",
                False,
                f"Shopee: {status or 'sem status'} (só {SHOPEE_PRONTO} troca)."
                + (" A etiqueta já foi gerada na Shopee." if status == "PROCESSED" else ""),
            )
        )
    else:
        travas.append(_trava("cancelado_na_plataforma", True, f"Shopee: {status}."))
    nota = detalhe.get("invoice_data") if isinstance(detalhe.get("invoice_data"), Mapping) else {}
    tem_nf = bool(str(nota.get("number") or "").strip())
    travas.append(
        _trava(
            "nf_emitida",
            not tem_nf,
            "A Shopee já tem NF neste pedido." if tem_nf else "Sem NF na Shopee.",
        )
    )
    return travas


async def _produto(cliente, sku: str) -> dict | None:
    try:
        return await cliente.find_active_product_by_sku(sku, estrito=True)
    except Exception as e:  # noqa: BLE001 — 429/timeout/5xx: não sei
        raise _Indisponivel("bling_indisponivel") from e


async def _bling_ao_vivo(
    cliente, p: _Pedido, sku_antigo: str, sku_novo: str, vivo: _Vivo
) -> list[dict[str, Any]]:
    """O produto novo, os outros itens e o pedido em dobro — antes do GET do pedido."""
    travas: list[dict[str, Any]] = []
    item = p.item(sku_antigo) or {"quantidade": 1}
    qtd = int(item["quantidade"] or 1)
    prod = await _produto(cliente, sku_novo)
    exato = (
        prod is not None
        and prod.get("id")
        and str(prod.get("sku") or "").strip().lower() == sku_novo.strip().lower()
    )
    if not exato:
        travas.append(_trava("produto_novo_inativo", False))
        return travas
    vivo.produto_id = int(prod["id"])
    vivo.nome_novo = prod.get("name") or None
    vivo.saldo = _dec(prod.get("stock"))
    travas.append(_trava("produto_novo_inativo", True, f"{sku_novo} ativo no Bling."))
    if vivo.saldo is None or vivo.saldo < qtd:
        travas.append(
            _trava(
                "saldo_insuficiente",
                False,
                f"Saldo de {sku_novo} no Bling: {vivo.saldo if vivo.saldo is not None else '?'}"
                f" (o pedido pede {qtd}).",
            )
        )
        return travas
    # A peça que ENTRA no kit e que outro item do pedido também usa: o saldo
    # do kit é o do componente mais escasso, e a peça é conferida por ela.
    outros = [i for i in p.itens if i["sku"].lower() != sku_antigo.strip().lower()]
    pecas_dos_outros = {x.strip().lower() for i in outros for x in i["sku"].split("+") if x.strip()}
    _saem, entram = pecas_que_mudam(sku_antigo, sku_novo)
    for peca, mult in sorted(entram.items()):
        if peca not in pecas_dos_outros:
            continue
        pp = await _produto(cliente, peca)
        saldo = _dec(pp.get("stock")) if pp else None
        if saldo is None or saldo < qtd * mult:
            travas.append(
                _trava(
                    "saldo_insuficiente",
                    False,
                    f"A peça {peca} é dividida com outro item do pedido e o saldo dela não cobre.",
                )
            )
            return travas
    travas.append(_trava("saldo_insuficiente", True, f"Saldo de {sku_novo}: {vivo.saldo}."))
    # Os outros itens do pedido: nenhum negativo (a mesma régua do sweep de NF;
    # aqui erro de consulta RECUSA — `bling_indisponivel`).
    negativos = []
    for it in outros:
        try:
            if it.get("produto_id"):
                dados = await cliente.get_product(int(it["produto_id"]))
                saldo = (dados.get("estoque") or {}).get("saldoVirtualTotal")
            else:
                pr = await cliente.find_active_product_by_sku(it["sku"], estrito=True)
                saldo = pr.get("stock") if pr else None
        except Exception as e:  # noqa: BLE001
            raise _Indisponivel("bling_indisponivel") from e
        if saldo is not None and float(saldo) < 0:
            negativos.append(it["sku"])
    travas.append(
        _trava(
            "outro_item_sem_estoque",
            not negativos,
            f"Sem estoque no Bling: {', '.join(negativos)}."
            if negativos
            else "Os outros itens têm saldo.",
        )
    )
    if negativos:
        return travas
    # O pedido em dobro, ao vivo e pela loja (o mesmo nº pode existir em outra).
    if p.numeroloja:
        try:
            achados = await cliente.list_pedidos_vendas(numeros_lojas=[p.numeroloja])
        except Exception as e:  # noqa: BLE001
            raise _Indisponivel("bling_indisponivel") from e
        vivos = []
        for o in achados or []:
            if not isinstance(o, Mapping) or str(o.get("numero") or "") == p.numero:
                continue
            loja = str((o.get("loja") or {}).get("id") or "")
            if p.loja and loja and loja != str(p.loja):
                continue
            if _id_situacao(o) == str(SITUACAO_CANCELADO):
                continue
            vivos.append(str(o.get("numero") or "?"))
        travas.append(
            _trava(
                "pedido_em_dobro",
                not vivos,
                f"Outro pedido vivo no Bling: {', '.join(vivos)}."
                if vivos
                else "Um pedido só no Bling.",
            )
        )
    return travas


async def _pedido_ao_vivo(
    cliente, p: _Pedido, sku_antigo: str, vivo: _Vivo
) -> list[dict[str, Any]]:
    """O GET do pedido — o ÚLTIMO antes do PUT (o corpo sai daqui)."""
    try:
        order = await cliente.get_order(int(p.bling_id))
    except Exception as e:  # noqa: BLE001
        raise _Indisponivel("bling_indisponivel") from e
    vivo.order = order if isinstance(order, dict) else {}
    travas = []
    sit = _id_situacao(vivo.order)
    travas.append(
        _trava(
            "situacao_mudou",
            sit == SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
            None if sit != SITUACAO_AGUARDANDO_CANCELAMENTO_STR else "Em Aguardando Cancelamento.",
        )
    )
    tem_nf = _nf_no_bling(vivo.order)
    travas.append(
        _trava("nf_emitida", not tem_nf, "NF no Bling." if tem_nf else "Sem NF no Bling.")
    )
    rastreio = _rastreio_no_bling(vivo.order)
    travas.append(_trava("rastreio", not rastreio, None if rastreio else "Sem rastreio no Bling."))
    itens = _itens_vivos(vivo.order)
    qtd_vivo = _quantidades(itens)
    qtd_espelho = Counter({i["sku"].lower(): int(i["quantidade"]) for i in p.itens})
    antigo = sku_antigo.strip().lower()
    linha = next((i for i in itens if str(i.get("codigo") or "").strip().lower() == antigo), None)
    if linha is None or qtd_vivo != qtd_espelho:
        travas.append(_trava("item_divergente", False))
    else:
        vivo.codigo_antigo = str(linha.get("codigo")).strip()
        vivo.quantidade = int(qtd_vivo[antigo])
        vivo.valor_unitario = _dec(linha.get("valor"))
        travas.append(_trava("item_divergente", True, "Os itens batem com o DaVinci."))
    return travas


async def _ao_vivo(
    session: AsyncSession,
    cliente,
    p: _Pedido,
    sku_antigo: str,
    sku_novo: str,
    *,
    todas: bool,
) -> tuple[list[dict[str, Any]], _Vivo]:
    """As travas ao vivo na ordem da crítica M3. `todas=False` para na 1ª falha.

    Erro de consulta vira a trava `bling_indisponivel`/`plataforma_indisponivel`
    (ok=False) e PARA: sem saber, não se segue.
    """
    vivo = _Vivo()
    travas: list[dict[str, Any]] = []
    etapas = (
        lambda: _shopee_ao_vivo(session, p),
        lambda: _bling_ao_vivo(cliente, p, sku_antigo, sku_novo, vivo),
        lambda: _pedido_ao_vivo(cliente, p, sku_antigo, vivo),
    )
    for etapa in etapas:
        try:
            novas = await etapa()
        except _Indisponivel as e:
            travas.append(_trava(e.code, False))
            return travas, vivo
        travas += novas
        if _primeira_falha(novas) is not None and not todas:
            return travas, vivo
    return travas, vivo


# ── A prévia ──────────────────────────────────────────────────────────────


def hash_previa(
    *,
    numero: str,
    sku_antigo: str,
    sku_novo: str,
    produto_id: int | None,
    quantidade: int | None,
    valor_unitario: Decimal | None,
    custo_antigo: Decimal | None,
    custo_novo: Decimal | None,
    nivel: int | None,
) -> str:
    """O que a pessoa viu na prévia; o clique confere de novo (`previa_mudou`). PURA."""
    corpo = json.dumps(
        [
            numero,
            sku_antigo.strip().lower(),
            sku_novo.strip().lower(),
            produto_id,
            quantidade,
            str(valor_unitario) if valor_unitario is not None else None,
            str(custo_antigo) if custo_antigo is not None else None,
            str(custo_novo) if custo_novo is not None else None,
            nivel,
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(corpo.encode()).hexdigest()[:32]


def linha_observacao(
    *,
    sku_antigo: str,
    sku_novo: str,
    nivel: int,
    fonte: str | None,
    aceite_em: datetime | None,
    nome: str,
    automatica: bool,
) -> str:
    """A linha das Observações do Bling — começa com "TROCA " (o contrato do painel). PURA.

    `compose_observacoes` põe o "dd/mm - " na frente.
    """
    if automatica:
        return f"TROCA {sku_antigo} -> {sku_novo} (mesmo produto, outro lote) - {NOME_ROBO}"
    if nivel == troca_sugestoes.NIVEL_LOTE:
        return f"TROCA {sku_antigo} -> {sku_novo} (mesmo produto, outro lote) - Atendimento/{nome}"
    if fonte == ACEITE_DECLARADO or fonte is None:
        return f"TROCA {sku_antigo} -> {sku_novo} (aceite declarado) - Atendimento/{nome}"
    return (
        f"TROCA {sku_antigo} -> {sku_novo} (aceite em {_hora(aceite_em)}, {fonte})"
        f" - Atendimento/{nome}"
    )


def _janela_do_aceite() -> tuple[datetime, datetime]:
    """(desde, até) em que a prova do aceite vale: os dias da chave, sem futuro."""
    agora = _agora()
    return (
        agora - timedelta(days=int(get_settings().atendimento_troca_aceite_max_dias)),
        agora + _FOLGA_RELOGIO,
    )


async def aceites_possiveis(session: AsyncSession, p: _Pedido) -> list[AtendimentoMensagem]:
    """As mensagens do cliente que valem como prova (decisão (b) do dono, 07/10/2026).

    Da conversa do pedido, do CLIENTE (nunca nota nem fala da loja), de no
    máximo `atendimento_troca_aceite_max_dias`. A mais recente primeiro. A
    prova é opcional e quem escolhe é a pessoa (a caixinha é o aceite):
    "depois da oferta" (crítica M11) é só a dica da tela (`oferta_em`), não
    recusa — o cliente pode ter aceitado antes de a loja oferecer.
    """
    if p.conversa is None:
        return []
    desde, ate = _janela_do_aceite()
    return list(
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == p.conversa.id,
                    AtendimentoMensagem.autor == AUTOR_CLIENTE,
                    AtendimentoMensagem.tipo != TIPO_NOTA,
                    AtendimentoMensagem.enviada_em >= desde,
                    AtendimentoMensagem.enviada_em <= ate,
                )
                .order_by(AtendimentoMensagem.enviada_em.desc())
                .limit(MAX_ACEITES_POSSIVEIS)
            )
        )
        .scalars()
        .all()
    )


async def oferta_em(session: AsyncSession, p: _Pedido) -> datetime | None:
    """A 1ª fala da LOJA na conversa depois da marca da falta de estoque (a oferta).

    Só a dica da prévia (`aceites_possiveis[].depois_da_oferta`): a oferta
    feita pelo Duoke chega pela leitura como mensagem da loja.
    """
    if p.conversa is None:
        return None
    desde, _ = _janela_do_aceite()
    marca = p.pedido.nf_marcada_em if p.pedido is not None else None
    if marca is not None and marca.tzinfo is None:
        marca = marca.replace(tzinfo=UTC)
    return await session.scalar(
        select(func.min(AtendimentoMensagem.enviada_em)).where(
            AtendimentoMensagem.conversa_id == p.conversa.id,
            AtendimentoMensagem.autor == AUTOR_LOJA,
            AtendimentoMensagem.enviada_em >= (max(desde, marca) if marca else desde),
        )
    )


def _aviso_nf(p: _Pedido) -> str | None:
    """Quando o sweep de NF NÃO vai pegar o pedido depois da troca: "enfileire na aba NF"."""
    from app.services.nf_auto_enfileirar import _CANDIDATE_WINDOW, _plataformas_ativas

    s = get_settings()
    fim = "— depois da troca, enfileire na aba NF."
    if not s.nf_auto_enfileirar:
        return f"O envio automático da NF está desligado {fim}"
    if p.data is not None and p.data < _agora() - _CANDIDATE_WINDOW:
        return f"Pedido com mais de 7 dias: o envio automático da NF não pega {fim}"
    plataformas = _plataformas_ativas(
        ml_amazon=bool(s.nf_auto_ml_amazon), so_ml=bool(getattr(s, "nf_auto_ml", False))
    )
    info = p.loja_info
    if (
        info is None
        or p.plataforma not in plataformas
        or getattr(info, "archived_at", None) is not None
        or getattr(info, "nf_faturador_id", None) is None
    ):
        return f"A loja está fora do envio automático da NF {fim}"
    return None


def _aceite_out(m: AtendimentoMensagem, oferta: datetime | None) -> dict[str, Any]:
    texto = " ".join((m.texto or "").split())
    enviada = m.enviada_em
    if enviada is not None and enviada.tzinfo is None:
        enviada = enviada.replace(tzinfo=UTC)
    return {
        "id": str(m.id),
        "texto": texto if len(texto) <= 300 else texto[:299] + "…",
        "enviada_em": enviada.isoformat() if enviada else None,
        "depois_da_oferta": bool(oferta is not None and enviada is not None and enviada > oferta),
    }


async def previa(
    session: AsyncSession,
    user: User | None,
    *,
    numero_bling: str,
    sku_antigo: str,
    sku_novo: str,
    conversa_id: UUID | None = None,
) -> dict[str, Any]:
    """A prévia da troca: as travas (do banco e ao vivo), o antes e o depois, os
    passos, os aceites possíveis e o `previa_hash` que o clique confere.

    Só GETs no Bling e na Shopee; nada da troca é gravado. O commit do fim só
    guarda o token renovado da Shopee, se houver (crítica M7).
    """
    from app.services.atendimento.painel import ve_margem

    numero = numero_bling.strip()
    ve, nome = ve_margem(user), _nome(user)
    conferir_chaves(numero)
    p = await _carregar(session, numero, conversa_id)
    # A MESMA fonte do clique (relida): o `previa_hash` leva os custos, e o
    # catálogo de 10 min de OUTRO processo daria `previa_mudou` à toa.
    cat = await troca_sugestoes.catalogo(session, forcar=True)
    travas, escolha = await _travas_banco(
        session, p, sku_antigo, sku_novo, automatica=False, cat=cat
    )
    aberta = await troca_aberta_do_pedido(session, numero)
    if aberta is not None:
        travas.insert(0, _trava("troca_em_andamento", False))
    vivo = _Vivo()
    ao_vivo = False
    if p.bling_id is None:
        travas.append(_trava("pedido_nao_encontrado", False, "O pedido não tem o id do Bling."))
    if _primeira_falha(travas) is None:
        cliente = await _cliente_bling(session)
        if cliente is None:
            travas.append(_trava("bling_indisponivel", False, "Sem integração do Bling ativa."))
        else:
            novas, vivo = await _ao_vivo(session, cliente, p, sku_antigo, sku_novo, todas=True)
            travas += novas
            ao_vivo = True
    pode = ao_vivo and _primeira_falha(travas) is None
    item = p.item(sku_antigo) or {}
    nivel = escolha.nivel if escolha else None
    exige_aceite = nivel is not None and nivel != troca_sugestoes.NIVEL_LOTE
    aceites: list[dict[str, Any]] = []
    if exige_aceite:
        oferta = await oferta_em(session, p)
        aceites = [_aceite_out(m, oferta) for m in await aceites_possiveis(session, p)]
    previa_hash = None
    if pode and escolha is not None:
        previa_hash = hash_previa(
            numero=numero,
            sku_antigo=sku_antigo,
            sku_novo=sku_novo,
            produto_id=vivo.produto_id,
            quantidade=vivo.quantidade,
            valor_unitario=vivo.valor_unitario,
            custo_antigo=escolha.custo_antigo,
            custo_novo=escolha.custo_novo,
            nivel=escolha.nivel,
        )
    passos = ["PUT itens + observação no Bling", "83955 → 9 → 6"]
    passos.append(f"Margem Aprovado por {nome}")
    passos.append("NF volta à fila")
    resposta = {
        "pode": pode,
        "travas": travas,
        "ao_vivo": ao_vivo,
        "numero_bling": numero,
        "bling_id": p.bling_id,
        "numeroloja": p.numeroloja,
        "plataforma": p.plataforma,
        "conversa_id": str(p.conversa.id) if p.conversa else None,
        "nivel": nivel,
        "mesmo_produto": bool(escolha and escolha.mesmo_produto),
        "exige_aceite": exige_aceite,
        "antes": {
            "sku": sku_antigo.strip(),
            "nome": item.get("descricao"),
            "quantidade": item.get("quantidade"),
            "produto_id": item.get("produto_id"),
            "custo": float(escolha.custo_antigo)
            if ve and escolha and escolha.custo_antigo
            else None,
        },
        "depois": {
            "sku": sku_novo.strip(),
            "nome": vivo.nome_novo or (escolha.nome if escolha else None),
            "quantidade": vivo.quantidade or item.get("quantidade"),
            "produto_id": vivo.produto_id,
            "saldo_ao_vivo": float(vivo.saldo) if vivo.saldo is not None else None,
            "custo": float(escolha.custo_novo) if ve and escolha and escolha.custo_novo else None,
        },
        "valor_unitario": float(vivo.valor_unitario) if vivo.valor_unitario is not None else None,
        "observacao": compose_observacoes(
            None,
            linha_observacao(
                sku_antigo=sku_antigo.strip(),
                sku_novo=sku_novo.strip(),
                nivel=nivel if nivel is not None else 1,
                fonte=ACEITE_DECLARADO,
                aceite_em=None,
                nome=nome,
                automatica=False,
            ),
        ),
        "passos_previstos": passos,
        "aviso_nf": _aviso_nf(p),
        "aviso_prazo": None if p.prazo else "Sem prazo de envio gravado: confira na Shopee.",
        "dif_custo_pct": escolha.dif_custo_pct if ve and escolha else None,
        "aceites_possiveis": aceites,
        "aceite_max_dias": int(get_settings().atendimento_troca_aceite_max_dias),
        "previa_hash": previa_hash,
        "troca_aberta": aberta,
        "lido_em": _agora().isoformat(),
    }
    # Nada da troca foi escrito; só o token renovado da Shopee, se houver.
    await session.commit()
    return resposta


# ── O aceite ──────────────────────────────────────────────────────────────


def _como_aceite(aceite: Aceite | Mapping[str, Any] | None) -> Aceite | None:
    if aceite is None or isinstance(aceite, Aceite):
        return aceite
    return Aceite(
        mensagem_aceite_id=aceite.get("mensagem_aceite_id"),
        fonte=aceite.get("fonte"),
        texto=aceite.get("texto"),
        em=aceite.get("em"),
    )


async def _validar_aceite(
    session: AsyncSession,
    p: _Pedido,
    nivel: int,
    aceite: Aceite | None,
    *,
    confirmar: bool,
    automatica: bool,
) -> dict[str, Any]:
    """{fonte, mensagem_aceite_id, texto, em} — a decisão (b) do dono.

    Nível 0: sem aceite (NULL). Níveis 1 e 2: a caixinha é obrigatória
    (`aceite_obrigatorio`); a prova, opcional — a mensagem do cliente (das
    `aceites_possiveis`: a mesma conversa, do cliente, dentro da janela), a
    resposta colada do Duoke (texto + data e hora dentro da janela, sem
    futuro) ou nenhuma (`declarado`).
    """
    vazio = {"fonte": None, "mensagem_aceite_id": None, "texto": None, "em": None}
    if automatica or nivel == troca_sugestoes.NIVEL_LOTE:
        return vazio
    if not confirmar:
        raise TrocaRecusada("aceite_obrigatorio")
    if aceite is None or (
        aceite.mensagem_aceite_id is None and not aceite.fonte and not (aceite.texto or "").strip()
    ):
        return {**vazio, "fonte": ACEITE_DECLARADO, "em": _agora()}
    if aceite.mensagem_aceite_id is not None:
        if aceite.fonte not in (None, ACEITE_DAVINCI):
            raise TrocaRecusada(
                "aceite_invalido", "Escolha a mensagem OU cole a resposta do Duoke."
            )
        validas = {m.id: m for m in await aceites_possiveis(session, p)}
        msg = validas.get(aceite.mensagem_aceite_id)
        if msg is None:
            raise TrocaRecusada(
                "aceite_invalido",
                "A mensagem escolhida não vale: tem de ser do cliente, nesta conversa e de no "
                f"máximo {int(get_settings().atendimento_troca_aceite_max_dias)} dias.",
            )
        return {
            "fonte": ACEITE_DAVINCI,
            "mensagem_aceite_id": msg.id,
            "texto": msg.texto,
            "em": msg.enviada_em,
        }
    if aceite.fonte != ACEITE_DUOKE:
        raise TrocaRecusada("aceite_invalido", "A prova do aceite é a mensagem ou o Duoke.")
    texto = (aceite.texto or "").replace("\x00", "").strip()
    if not texto or len(texto) > ACEITE_TEXTO_MAX or aceite.em is None:
        raise TrocaRecusada(
            "aceite_invalido", "Cole a resposta do cliente no Duoke com a data e a hora."
        )
    em = aceite.em if aceite.em.tzinfo is not None else aceite.em.replace(tzinfo=_BRT)
    desde, ate = _janela_do_aceite()
    if em > ate or em < desde:
        raise TrocaRecusada(
            "aceite_invalido",
            "A data do aceite no Duoke tem de ser de no máximo "
            f"{int(get_settings().atendimento_troca_aceite_max_dias)} dias e não no futuro.",
        )
    return {"fonte": ACEITE_DUOKE, "mensagem_aceite_id": None, "texto": texto, "em": em}


# ── Os passos ─────────────────────────────────────────────────────────────


def _passo(troca: AtendimentoTroca, passo: str, ok: bool, detalhe: str | None = None) -> None:
    """Uma linha em `passos` (lista NOVA: o JSONB só grava o que foi reatribuído) + a vez."""
    troca.passos = [
        *(troca.passos or []),
        {"passo": passo, "em": _agora().isoformat(), "ok": ok, "detalhe": detalhe},
    ]
    troca.em_execucao_ate = _agora() + VEZ


async def _lock_pedido(session: AsyncSession, numero: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"atendimento_troca:{numero}"}
    )


def chaves_das_pecas(sku_antigo: str, sku_novo: str) -> list[str]:
    """O que a troca GASTA: o SKU novo e cada peça que entra no kit, em ordem estável. PURA.

    A peça escassa é a mesma no produto simples e dentro de um kit
    (dg053.ci → dg053.sp e dg053.ci+a001.ci → dg053.sp+a001.sp gastam o
    mesmo dg053.sp): o lock é por peça, não pelo texto do SKU do kit.
    """
    _saem, entram = pecas_que_mudam(sku_antigo, sku_novo)
    return sorted({sku_novo.strip().lower(), *entram})


async def _lock_pecas(session: AsyncSession, sku_antigo: str, sku_novo: str) -> bool:
    """O lock de cada peça que entra, até o commit do PUT (M14). False = outra troca está nela.

    `pg_try_` (nunca espera, nunca trava em deadlock), na ordem de
    `chaves_das_pecas`; o que já pegou cai sozinho no fim da transação.
    """
    for chave in chaves_das_pecas(sku_antigo, sku_novo):
        pegou = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
            {"k": f"atendimento_troca_sku:{chave}"},
        )
        if not pegou:
            return False
    return True


async def _abortar(
    session: AsyncSession, troca: AtendimentoTroca, code: str, detail: str | None = None
) -> None:
    troca.estado = ABORTADA
    troca.codigo_erro = code
    troca.erro = detail or TEXTOS.get(code, code)
    _passo(troca, "abortada", False, troca.erro)
    troca.em_execucao_ate = None
    await session.commit()
    logger.info(
        "atendimento_troca_abortada", troca=str(troca.id), pedido=troca.pedido_bling, code=code
    )


async def _parar(
    session: AsyncSession, troca: AtendimentoTroca, passo: str, code: str, detail: str | None
) -> None:
    """Parou no meio (o estado fica): o porquê em `passos`, e a vez solta para o retomar."""
    troca.codigo_erro = code
    troca.erro = detail or TEXTOS.get(code, code)
    _passo(troca, passo, False, troca.erro)
    troca.em_execucao_ate = None
    await session.commit()
    logger.info(
        "atendimento_troca_parou",
        troca=str(troca.id),
        pedido=troca.pedido_bling,
        passo=passo,
        code=code,
    )


def _erro_do_bling(e: httpx.HTTPStatusError) -> str:
    """A frase do 4xx do Bling (as mensagens dos campos), sem o corpo inteiro."""
    try:
        erro = e.response.json().get("error") or {}
    except ValueError:
        return f"O Bling recusou a troca ({e.response.status_code}). Nada mudou no pedido."
    msgs = [
        str(f.get("msg") or "").strip()
        for f in (erro.get("fields") or [])
        if isinstance(f, Mapping) and str(f.get("msg") or "").strip()
    ]
    texto = "; ".join(msgs) or str(erro.get("description") or erro.get("message") or "").strip()
    return f"O Bling recusou: {texto[:300] or e.response.status_code}. Nada mudou no pedido."


async def _conferir_put(cliente, troca: AtendimentoTroca) -> str | None:
    """Depois de um PUT sem resposta: 'novo', 'antigo' ou None (não deu para ler).

    GETs espaçados (`ESPERAS_CONFERENCIA_S`): o item novo em qualquer um =
    trocou; só com TODOS os GETs vendo o antigo, não trocou.
    """
    viu_antigo = False
    for espera in ESPERAS_CONFERENCIA_S:
        if espera:
            await asyncio.sleep(espera)
        try:
            order = await cliente.get_order(int(troca.bling_id))
        except Exception:  # noqa: BLE001, S112 — tenta o próximo
            continue
        estado = estado_do_item(order, troca.sku_antigo, troca.sku_novo)
        if estado == "novo":
            return "novo"
        if estado != "antigo":
            return None
        viu_antigo = True
    return "antigo" if viu_antigo else None


async def _registrar_item_trocado(session: AsyncSession, troca: AtendimentoTroca) -> None:
    """Passo 4 (idempotente): o espelho, a trilha `sku` e — com pessoa — o pino Aprovado."""
    await _lock_pedido(session, troca.pedido_bling)
    valores: dict[str, Any] = {
        "item_codigo": troca.sku_novo,
        "item_produto_id": int(troca.produto_novo_id),
        # O custo que a Margem carimba no item (sem esperar o webhook do 6).
        "preco_custo": float(troca.custo_novo) if troca.custo_novo is not None else None,
    }
    if troca.descricao_nova:
        valores["item_descricao"] = troca.descricao_nova
    await session.execute(
        update(BlingOrder)
        .where(
            BlingOrder.numero == troca.pedido_bling,
            BlingOrder.bling_id == int(troca.bling_id),
            func.lower(BlingOrder.item_codigo) == troca.sku_antigo.strip().lower(),
        )
        .values(**valores)
        .execution_options(synchronize_session=False)
    )
    await record_margem_audit(
        session,
        acao="sku",
        pedido_bling=troca.pedido_bling,
        bling_id=troca.bling_id,
        sku=troca.sku_antigo,
        valor_antigo=troca.sku_antigo,
        valor_novo=troca.sku_novo,
        origem=ORIGEM_TROCA,
        mudado_por=troca.criado_por,
    )
    detalhe = "espelho e trilha do SKU"
    if not troca.automatica and troca.criado_por is not None:
        # A troca aprova a Margem (decisão do dono): o pino ANTES do PATCH 6.
        await session.execute(
            update(BlingOrder)
            .where(BlingOrder.numero == troca.pedido_bling)
            .values(status=PINO_APROVADO, aprovado_por=troca.criado_por, verificado=True)
            .execution_options(synchronize_session=False)
        )
        await patch_status_for_pedido(
            session,
            pedido_bling=troca.pedido_bling,
            status=PINO_APROVADO,
            aprovado_por=str(troca.criado_por),
            verificado=True,
        )
        await record_margem_audit(
            session,
            acao="status",
            pedido_bling=troca.pedido_bling,
            bling_id=troca.bling_id,
            sku=None,
            valor_antigo=troca.pino_anterior,
            valor_novo=PINO_APROVADO,
            origem=ORIGEM_TROCA,
            mudado_por=troca.criado_por,
        )
        detalhe += f"; Margem Aprovado por {troca.criado_por_nome}"
    else:
        detalhe += "; Margem mantida (troca automática não aprova a Margem)"
    troca.estado = ITEM_TROCADO
    troca.codigo_erro = None
    troca.erro = None
    _passo(troca, "item_trocado", True, detalhe)
    await session.commit()


async def _shopee_ainda_pronta(session: AsyncSession, troca: AtendimentoTroca) -> str | None:
    """No retomar, antes de mover a situação: None = segue; senão o código da trava."""
    p = await _carregar(session, troca.pedido_bling)
    travas: list[dict[str, Any]] = []
    try:
        travas = await _shopee_ao_vivo(session, p)
    except _Indisponivel as e:
        return e.code
    falha = _primeira_falha(travas)
    return str(falha["code"]) if falha else None


async def _voltar_para_em_aberto(
    session: AsyncSession,
    cliente,
    troca: AtendimentoTroca,
    *,
    order: Mapping[str, Any] | None = None,
    conferir_shopee: bool = False,
) -> bool:
    """Passo 5: 83955 → 9 → 6 pelo GET de agora. True = chegou em `em_aberto`."""
    if order is None:
        try:
            order = await cliente.get_order(int(troca.bling_id))
        except Exception:  # noqa: BLE001
            await _parar(session, troca, "situacao", "bling_indisponivel", None)
            return False
    if estado_do_item(order, troca.sku_antigo, troca.sku_novo) != "novo":
        await _abortar(
            session,
            troca,
            "item_divergente",
            "O item do pedido no Bling já não é o novo: alguém mexeu — uma pessoa decide.",
        )
        await _nota_segura(session, troca, "a troca parou: o item do pedido no Bling mudou.")
        return False
    sit = _id_situacao(order)
    passos: list[int] = []
    if sit == SITUACAO_AGUARDANDO_CANCELAMENTO_STR:
        passos = [SITUACAO_ATENDIDO, SITUACAO_EM_ABERTO]
    elif sit == str(SITUACAO_ATENDIDO):
        passos = [SITUACAO_EM_ABERTO]
    elif sit != str(SITUACAO_EM_ABERTO):
        await _abortar(
            session,
            troca,
            "situacao_mudou_no_meio",
            f"O pedido foi para a situação {sit or '?'} no meio da troca (o item já é o novo): "
            "uma pessoa decide.",
        )
        await _nota_segura(
            session,
            troca,
            f"a troca parou: o pedido foi para a situação {sit or '?'} no Bling no meio dela "
            "(o item já é o novo).",
        )
        return False
    # A NF no Bling, em QUALQUER situação — inclusive já em 6, sem PATCH a
    # fazer: a NF que saiu por fora (o lote das 22:30, a Upseller, alguém à
    # mão) só chega à `nf_nota` com o coletor de XML (30 min); limpar a marca
    # por cima dela faria o sweep emitir a segunda.
    if _nf_no_bling(order):
        await _abortar_nf_no_bling(session, troca)
        return False
    if passos:
        if conferir_shopee:
            code = await _shopee_ainda_pronta(session, troca)
            if code in ("plataforma_indisponivel",):
                await _parar(session, troca, "situacao", code, None)
                return False
            if code is not None:
                await _abortar(session, troca, code, None)
                await _nota_segura(
                    session,
                    troca,
                    f"a troca parou antes de voltar para Em aberto: {TEXTOS.get(code, code)}",
                )
                return False
    for alvo in passos:
        try:
            await cliente.update_order_situacao(int(troca.bling_id), alvo)
        except Exception:  # noqa: BLE001
            await _parar(
                session,
                troca,
                f"situacao_{alvo}",
                "bling_indisponivel",
                "O Bling não mudou a situação agora: use Retomar.",
            )
            return False
        if alvo == SITUACAO_ATENDIDO:
            troca.estado = EM_ATENDIDO
            _passo(troca, "situacao_9", True, "83955 → 9 (Atendido)")
            await session.commit()
    # No Bling, em 6: o espelho, o snapshot da Margem e a trilha.
    await _lock_pedido(session, troca.pedido_bling)
    if not troca.automatica and troca.criado_por is not None:
        await session.execute(
            update(BlingOrder)
            .where(BlingOrder.bling_id == int(troca.bling_id))
            .values(situacao=str(SITUACAO_EM_ABERTO))
            .execution_options(synchronize_session=False)
        )
        await patch_status_for_pedido(
            session,
            pedido_bling=troca.pedido_bling,
            status=PINO_APROVADO,
            situacao=str(SITUACAO_EM_ABERTO),
        )
    else:
        from app.services.margem_auto_hold import _espelhar_situacao

        await _espelhar_situacao(
            session, bling_id=int(troca.bling_id), situacao=str(SITUACAO_EM_ABERTO)
        )
    await record_margem_audit(
        session,
        acao="situacao",
        pedido_bling=troca.pedido_bling,
        bling_id=troca.bling_id,
        sku=None,
        valor_antigo=str(SITUACAO_AGUARDANDO_CANCELAMENTO),
        valor_novo=str(SITUACAO_EM_ABERTO),
        origem=ORIGEM_TROCA,
        mudado_por=troca.criado_por,
    )
    troca.estado = EM_ABERTO
    troca.codigo_erro = None
    troca.erro = None
    _passo(troca, "situacao_6", True, "Em aberto (6)")
    await session.commit()
    return True


async def _abortar_nf_no_bling(session: AsyncSession, troca: AtendimentoTroca) -> None:
    """O pedido ganhou NF no Bling no meio da troca: para, a marca da NF FICA e a nota avisa."""
    await _abortar(
        session,
        troca,
        "nf_emitida",
        "O pedido ganhou NF no Bling no meio da troca: a marca da NF ficou — uma pessoa decide.",
    )
    await _nota_segura(
        session,
        troca,
        "a troca parou: o pedido já tem NF no Bling (saiu por fora). A marca da NF ficou: "
        "confira a nota antes de emitir de novo.",
    )


async def _liberar_nf(
    session: AsyncSession, troca: AtendimentoTroca, *, order: Mapping[str, Any] | None = None
) -> None:
    """Passo 6: a marca da NF limpa com compare-and-set (crítica A3).

    `order` = o GET do retomar (a troca parou em `em_aberto`): com NF no
    Bling, NÃO limpa — a NF de fora ainda não chegou à `nf_nota` e o sweep
    emitiria a segunda (`_abortar_nf_no_bling`).
    """
    if order is not None and _nf_no_bling(order):
        await _abortar_nf_no_bling(session, troca)
        return
    await _lock_pedido(session, troca.pedido_bling)
    detalhe = "marca da NF limpa: o sweep pega em até 2 min"
    if await _tem_nf(session, troca.pedido_bling) or await _em_fila_nf(session, troca.pedido_bling):
        detalhe = "NF já seguiu por outro caminho (nota ou comando na fila): nada a limpar"
    else:
        r = await session.execute(
            update(NfFaturamento)
            .where(
                NfFaturamento.pedido_bling == troca.pedido_bling,
                func.lower(func.trim(NfFaturamento.status_faturamento)).in_(
                    (NF_SEM_ESTOQUE, NF_RESTRICAO)
                ),
                NfFaturamento.erro_faturamento.is_not_distinct_from(troca.nf_erro_anterior),
            )
            .values(status_faturamento=None, erro_faturamento=None)
            .execution_options(synchronize_session=False)
        )
        if not r.rowcount:
            detalhe = "NF já seguiu por outro caminho (a marca mudou): nada a limpar"
    troca.estado = NF_LIBERADA
    _passo(troca, "nf_liberada", True, detalhe)
    await session.commit()


def _texto_da_nota(troca: AtendimentoTroca) -> str:
    if troca.automatica:
        quem = "Troca automática de lote (robô), sem aceite: o mesmo produto, de outro lote."
    elif troca.nivel == troca_sugestoes.NIVEL_LOTE:
        quem = f"Feita por {troca.criado_por_nome} (o mesmo produto, de outro lote: sem aceite)."
    elif troca.aceite_fonte == ACEITE_DECLARADO:
        quem = f"Aceite do cliente declarado por {troca.criado_por_nome}."
    else:
        onde = "nesta conversa" if troca.aceite_fonte == ACEITE_DAVINCI else "no Duoke"
        quem = (
            f"Aceite do cliente {onde} em {_hora(troca.aceite_em)}; feita por "
            f"{troca.criado_por_nome}."
        )
    return (
        f"Troca de produto no pedido {troca.pedido_bling}: {troca.sku_antigo} → "
        f"{troca.sku_novo} (nível {troca.nivel}). {quem} O pedido voltou para Em aberto e "
        "a NF volta à fila."
    )


async def _conversa_da_troca(
    session: AsyncSession, troca: AtendimentoTroca
) -> AtendimentoConversa | None:
    if troca.conversa_id is None:
        return None
    return await session.get(AtendimentoConversa, troca.conversa_id)


async def _nota_segura(session: AsyncSession, troca: AtendimentoTroca, texto: str) -> None:
    """Uma nota interna na conversa (se houver), sem nunca derrubar quem chamou."""
    from app.services.atendimento import painel

    try:
        async with session.begin_nested():
            conversa = await _conversa_da_troca(session, troca)
            if conversa is not None:
                await painel.criar_nota(
                    session,
                    conversa,
                    f"Troca de produto ({troca.sku_antigo} → {troca.sku_novo}): {texto}",
                    user_id=troca.criado_por,
                )
        await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_troca_nota_falhou", troca=str(troca.id), err=type(e).__name__)


async def _fechar(session: AsyncSession, troca: AtendimentoTroca) -> None:
    """Passo 7: nota, Observações do painel, etiqueta — falha aqui não desfaz nada."""
    from app.services.atendimento import etiqueta, painel

    erros: list[str] = []
    conversa = None
    try:
        async with session.begin_nested():
            conversa = await _conversa_da_troca(session, troca)
            if conversa is not None:
                await painel.criar_nota(
                    session, conversa, _texto_da_nota(troca), user_id=troca.criado_por
                )
    except Exception as e:  # noqa: BLE001
        erros.append(f"nota: {type(e).__name__}")
    try:
        await painel.invalidar_observacoes(troca.bling_id)
    except Exception as e:  # noqa: BLE001
        erros.append(f"observações: {type(e).__name__}")
    if conversa is not None:
        try:
            async with session.begin_nested():
                await etiqueta.recalcular_etiqueta(session, conversa, motivo="troca de produto")
        except Exception as e:  # noqa: BLE001
            erros.append(f"etiqueta: {type(e).__name__}")
    troca.estado = CONCLUIDA
    troca.concluida_em = _agora()
    troca.codigo_erro = None
    troca.erro = None
    _passo(
        troca,
        "concluida",
        not erros,
        ("o fechamento falhou em parte (" + "; ".join(erros) + "): a etiqueta se acerta pelo cron")
        if erros
        else ("nota na conversa, observações e etiqueta" if conversa else "sem conversa: sem nota"),
    )
    troca.em_execucao_ate = None
    await session.commit()
    logger.info("atendimento_troca_concluida", troca=str(troca.id), pedido=troca.pedido_bling)


async def _seguir(
    session: AsyncSession,
    cliente,
    troca: AtendimentoTroca,
    *,
    order: Mapping[str, Any] | None = None,
    conferir_shopee: bool = False,
) -> AtendimentoTroca:
    """Do estado atual em diante (passos 5 a 7)."""
    if troca.estado in (ITEM_TROCADO, EM_ATENDIDO):
        ok = await _voltar_para_em_aberto(
            session, cliente, troca, order=order, conferir_shopee=conferir_shopee
        )
        if not ok:
            return troca
    if troca.estado == EM_ABERTO:
        await _liberar_nf(session, troca, order=order)
    if troca.estado == NF_LIBERADA:
        await _fechar(session, troca)
    return troca


async def _erro_interno(session: AsyncSession, troca_id: UUID, etapa: str, e: Exception) -> None:
    """Um erro do DaVinci no meio: o estado fica, o porquê vai para `passos`, a vez solta."""
    logger.warning(
        "atendimento_troca_erro_interno", troca=str(troca_id), etapa=etapa, err=type(e).__name__
    )
    try:
        await session.rollback()
        troca = await session.get(AtendimentoTroca, troca_id, populate_existing=True)
        if troca is not None and troca.estado not in FECHADOS:
            await _parar(
                session,
                troca,
                etapa,
                "erro_interno",
                f"{TEXTOS['erro_interno']} ({type(e).__name__})",
            )
    except Exception:  # noqa: BLE001 — o registro do erro nunca derruba de novo
        await session.rollback()


# ── Executar ──────────────────────────────────────────────────────────────


async def executar(
    session: AsyncSession,
    user: User | None,
    *,
    numero_bling: str,
    sku_antigo: str,
    sku_novo: str,
    idem_key: UUID,
    conversa_id: UUID | None = None,
    aceite: Aceite | Mapping[str, Any] | None = None,
    confirmar: bool = False,
    previa_hash: str | None = None,
    automatica: bool = False,
) -> AtendimentoTroca:
    """O clique "Trocar" (ou o robô de lote, `automatica=True` e `user=None`).

    Devolve a troca no estado em que parou (concluída, ou parada num passo
    com o porquê em `codigo_erro`/`passos`). Levanta `TrocaRecusada` quando
    uma trava recusa (nada foi escrito no Bling; a troca, se já existia a
    linha, fica `abortada` e o `troca_id` vai na exceção).

    Pessoa: `confirmar` obrigatório nos níveis 1 e 2 (a caixinha), prova do
    aceite opcional (`aceite`), `previa_hash` da prévia. Robô: só nível 0,
    sem aceite e sem prévia; `criado_por` NULL.
    """
    if automatica and user is not None:
        raise ValueError("a troca automática não tem pessoa")
    if not automatica and user is None:
        raise ValueError("a troca de pessoa precisa de quem clicou")
    numero = numero_bling.strip()
    antigo, novo = sku_antigo.strip(), sku_novo.strip()
    # As chaves antes de qualquer leitura (a tabela pode nem existir ainda:
    # o push no main publicado antes do alembic nasce com a chave desligada).
    conferir_chaves(numero, automatica=automatica)
    # A mesma idem_key = a troca que já existe, sem refazer nada.
    existente = await session.scalar(
        select(AtendimentoTroca).where(AtendimentoTroca.idem_key == idem_key)
    )
    if existente is not None:
        if existente.pedido_bling != numero:
            raise TrocaRecusada("idem_key_reusada", status=422)
        return existente
    aberta = await troca_aberta_do_pedido(session, numero)
    if aberta is not None:
        raise TrocaRecusada("troca_em_andamento", troca_id=UUID(aberta["id"]))

    # As travas do banco (de novo: nunca confiar na tela).
    p = await _carregar(session, numero, conversa_id)
    # A pessoa: o catálogo relido (o mesmo da prévia, para o `previa_hash`). O
    # robô: o da memória (10 min), o mesmo com que a rodada escolheu — sem
    # prévia a conferir, e o saldo quem diz é o Bling ao vivo.
    cat = await troca_sugestoes.catalogo(session, forcar=not automatica)
    travas, escolha = await _travas_banco(session, p, antigo, novo, automatica=automatica, cat=cat)
    falha = _primeira_falha(travas)
    if falha is not None:
        raise TrocaRecusada(str(falha["code"]), str(falha["texto"]))
    if escolha is None or p.bling_id is None:
        raise TrocaRecusada("sugestao_invalida")
    prova = await _validar_aceite(
        session, p, escolha.nivel, _como_aceite(aceite), confirmar=confirmar, automatica=automatica
    )
    # A oferta que o DaVinci mandou na conversa (fase 4d, crítica M11): só a referência.
    from app.services.atendimento.troca_oferta import oferta_enviada_id

    oferta_id = (
        None
        if automatica or p.conversa is None
        else await oferta_enviada_id(session, p.conversa.id, numero, novo)
    )

    # 1. INSERT `iniciada` (uma troca aberta por pedido).
    item = p.item(antigo) or {"quantidade": 1}
    troca = AtendimentoTroca(
        pedido_bling=numero,
        bling_id=int(p.bling_id),
        numeroloja=p.numeroloja,
        plataforma=p.plataforma,
        conversa_id=p.conversa.id if p.conversa else None,
        motivo_codigo=p.motivo.codigo if p.motivo else SEM_ESTOQUE,
        sku_antigo=antigo,
        sku_novo=novo,
        # Até a conferência ao vivo, o id do catálogo (o passo 2 grava o do Bling).
        produto_novo_id=_id_no_catalogo(cat, novo),
        descricao_nova=escolha.nome,
        quantidade=int(item["quantidade"] or 1),
        nivel=int(escolha.nivel),
        automatica=automatica,
        custo_antigo=escolha.custo_antigo,
        custo_novo=escolha.custo_novo,
        aceite_fonte=prova["fonte"],
        mensagem_aceite_id=prova["mensagem_aceite_id"],
        oferta_mensagem_id=oferta_id,
        aceite_texto=prova["texto"],
        aceite_em=prova["em"],
        pino_anterior=p.pino,
        nf_status_anterior=p.nf_status,
        nf_erro_anterior=p.nf_erro,
        estado=INICIADA,
        passos=[],
        idem_key=idem_key,
        criado_por=user.id if user is not None else None,
        criado_por_nome=_nome(user),
    )
    _passo(troca, "iniciada", True, "robô de lote" if automatica else f"por {_nome(user)}")
    session.add(troca)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        repetida = await session.scalar(
            select(AtendimentoTroca).where(AtendimentoTroca.idem_key == idem_key)
        )
        if repetida is not None and repetida.pedido_bling == numero:
            return repetida
        raise TrocaRecusada("troca_em_andamento") from e
    logger.info(
        "atendimento_troca_iniciada",
        troca=str(troca.id),
        pedido=numero,
        de=antigo,
        para=novo,
        nivel=troca.nivel,
        automatica=automatica,
    )
    try:
        return await _executar_no_bling(session, troca, p, user, previa_hash=previa_hash)
    except TrocaRecusada:
        raise
    except Exception as e:  # noqa: BLE001 — o estado fica e o retomar segue
        await _erro_interno(session, troca.id, "executar", e)
        await session.refresh(troca)
        return troca


async def _executar_no_bling(
    session: AsyncSession,
    troca: AtendimentoTroca,
    p: _Pedido,
    user: User | None,
    *,
    previa_hash: str | None,
) -> AtendimentoTroca:
    """Passos 2 a 7 (o 1, o INSERT, já foi)."""
    cliente = await _cliente_bling(session)
    if cliente is None:
        await _abortar(session, troca, "bling_indisponivel", "Sem integração do Bling ativa.")
        raise TrocaRecusada(
            "bling_indisponivel", "Sem integração do Bling ativa.", troca_id=troca.id
        )

    # 2. As travas ao vivo, com o lock do pedido e o de cada peça que entra até o PUT.
    await _lock_pedido(session, troca.pedido_bling)
    if not await _lock_pecas(session, troca.sku_antigo, troca.sku_novo):
        await _abortar(session, troca, "peca_em_disputa")
        raise TrocaRecusada("peca_em_disputa", troca_id=troca.id)
    travas, vivo = await _ao_vivo(
        session, cliente, p, troca.sku_antigo, troca.sku_novo, todas=False
    )
    falha = _primeira_falha(travas)
    if falha is None and not troca.automatica:
        conferido = hash_previa(
            numero=troca.pedido_bling,
            sku_antigo=troca.sku_antigo,
            sku_novo=troca.sku_novo,
            produto_id=vivo.produto_id,
            quantidade=vivo.quantidade,
            valor_unitario=vivo.valor_unitario,
            custo_antigo=troca.custo_antigo,
            custo_novo=troca.custo_novo,
            nivel=troca.nivel,
        )
        if previa_hash != conferido:
            falha = _trava("previa_mudou", False)
    if falha is not None:
        await _abortar(session, troca, str(falha["code"]), str(falha["texto"]))
        raise TrocaRecusada(str(falha["code"]), str(falha["texto"]), troca_id=troca.id)
    troca.produto_novo_id = int(vivo.produto_id)
    troca.descricao_nova = vivo.nome_novo or troca.descricao_nova
    troca.saldo_ao_vivo = vivo.saldo
    troca.valor_unitario = vivo.valor_unitario
    troca.quantidade = int(vivo.quantidade or troca.quantidade)
    _passo(troca, "conferencia", True, f"ao vivo: saldo de {troca.sku_novo} = {vivo.saldo}")

    # 3. UM PUT: itens + a linha "TROCA ..." nas Observações.
    order = vivo.order
    linha = linha_observacao(
        sku_antigo=troca.sku_antigo,
        sku_novo=troca.sku_novo,
        nivel=troca.nivel,
        fonte=troca.aceite_fonte,
        aceite_em=troca.aceite_em,
        nome=troca.criado_por_nome,
        automatica=troca.automatica,
    )
    body = build_observacoes_put_body(order, compose_observacoes(order.get("observacoes"), linha))
    body["itens"], aplicadas = aplicar_trocas_nos_itens(
        body.get("itens") or [],
        [
            {
                "antigo": vivo.codigo_antigo or troca.sku_antigo,
                "alvo": troca.sku_novo,
                "alvo_id": int(troca.produto_novo_id),
                "alvo_nome": troca.descricao_nova,
            }
        ],
        # SEMPRE substituir: o Bling refaz a composição do kit (sem compensar
        # por fora), mesmo com `prioridade_substitui_item` desligado.
        substituir=True,
    )
    if not aplicadas:
        await _abortar(session, troca, "item_divergente")
        raise TrocaRecusada("item_divergente", troca_id=troca.id)
    try:
        await cliente.update_order(int(troca.bling_id), body)
    except httpx.HTTPStatusError as e:
        st = e.response.status_code
        if 400 <= st < 500 and st != 429:
            await _abortar(session, troca, "bling_recusou", _erro_do_bling(e))
            raise TrocaRecusada("bling_recusou", troca.erro, troca_id=troca.id) from e
        resultado = await _put_incerto(session, cliente, troca)
        if resultado is not None:
            return resultado
    except Exception:  # noqa: BLE001 — 429/503 (BlingCloudflareError), timeout, rede
        resultado = await _put_incerto(session, cliente, troca)
        if resultado is not None:
            return resultado
    else:
        _passo(troca, "put", True, f"itens + observação: {linha}")
        await session.commit()  # solta os locks: o PUT valeu

    # 4. No banco, depois do PUT.
    await _registrar_item_trocado(session, troca)
    # 5 a 7.
    return await _seguir(session, cliente, troca)


async def _put_incerto(
    session: AsyncSession, cliente, troca: AtendimentoTroca
) -> AtendimentoTroca | None:
    """O PUT falhou sem resposta clara: `incerta` e os GETs decidem. None = trocou, segue."""
    troca.estado = INCERTA
    _passo(troca, "put", False, "o Bling não respondeu ao PUT: conferindo por GET")
    await session.commit()
    visto = await _conferir_put(cliente, troca)
    if visto == "novo":
        _passo(troca, "put", True, "conferido por GET: o item novo está no pedido")
        await session.commit()
        return None
    if visto == "antigo":
        await _abortar(
            session,
            troca,
            "bling_indisponivel",
            "O Bling não aplicou a troca (o item antigo continua) — tente de novo em alguns "
            "minutos.",
        )
        raise TrocaRecusada("bling_indisponivel", troca.erro, troca_id=troca.id)
    await _parar(
        session,
        troca,
        "put",
        "bling_indisponivel",
        "Não sei se o Bling trocou o item: use Retomar (ele confere antes de qualquer coisa).",
    )
    return troca


# ── Retomar ───────────────────────────────────────────────────────────────


async def _tomar_a_vez(session: AsyncSession, troca_id: UUID) -> bool:
    """A marca `em_execucao_ate`: ninguém conduzindo (ou a vez venceu) → é minha."""
    r = await session.execute(
        update(AtendimentoTroca)
        .where(
            AtendimentoTroca.id == troca_id,
            AtendimentoTroca.estado.not_in(tuple(FECHADOS)),
            or_(
                AtendimentoTroca.em_execucao_ate.is_(None),
                AtendimentoTroca.em_execucao_ate < func.now(),
            ),
        )
        .values(em_execucao_ate=func.now() + VEZ)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return bool(r.rowcount)


async def retomar(session: AsyncSession, user: User | None, troca_id: UUID) -> AtendimentoTroca:
    """Segue a troca de onde parou: GET primeiro, só para frente, NUNCA PUT.

    Respeita a chave geral (`troca_desligada`: desligar é o freio de tudo); o
    piloto não — a troca já começou num pedido que estava nele.
    """
    conferir_ativa()
    troca = await session.get(AtendimentoTroca, troca_id)
    if troca is None:
        raise TrocaRecusada("troca_nao_encontrada", status=404)
    if troca.estado in FECHADOS:
        return troca
    if not await _tomar_a_vez(session, troca.id):
        raise TrocaRecusada("troca_em_andamento", "Alguém está conduzindo esta troca agora.")
    await session.refresh(troca)
    quem = _nome(user)
    try:
        cliente = await _cliente_bling(session)
        if cliente is None:
            await _parar(
                session, troca, "retomar", "bling_indisponivel", "Sem integração do Bling ativa."
            )
            return troca
        try:
            order = await cliente.get_order(int(troca.bling_id))
        except Exception:  # noqa: BLE001
            await _parar(session, troca, "retomar", "bling_indisponivel", None)
            return troca
        _passo(
            troca, "retomar", True, f"por {quem}; situação no Bling: {_id_situacao(order) or '?'}"
        )
        await session.commit()
        if troca.estado in (INICIADA, INCERTA):
            visto = estado_do_item(order, troca.sku_antigo, troca.sku_novo)
            if visto == "antigo":
                # Nunca PUT no retomar: o item antigo = o PUT não valeu.
                await _abortar(session, troca, "put_nao_aplicado")
                return troca
            if visto != "novo":
                await _abortar(session, troca, "item_divergente")
                return troca
            _passo(troca, "put", True, "conferido por GET no retomar: o item novo está no pedido")
            await _registrar_item_trocado(session, troca)
        return await _seguir(session, cliente, troca, order=order, conferir_shopee=True)
    except Exception as e:  # noqa: BLE001 — o estado fica; o porquê vai para os passos
        await _erro_interno(session, troca.id, "retomar", e)
        await session.refresh(troca)
        return troca


# ── O botão "Trocar" (o painel e a lista) ─────────────────────────────────

SO_LEITURA = "atendimento_so_leitura"
TEXTO_SO_LEITURA = (
    "Só leitura por enquanto: a troca é de quem cuida do Atendimento e mexe na Margem."
)
# O `troca_envio` quando a conferência quebrou (o resto do bloco segue).
TROCA_NAO_CONFERIDA = {
    "disponivel": False,
    "motivo": "falhou",
    "texto_motivo": "Não consegui conferir agora se a troca pode ser feita.",
}


def pode_escrever(user: User | None) -> bool:
    """Passaria nas travas das rotas que ESCREVEM (prévia, troca, retomar e a oferta)?

    Decisão (g) do dono (07/10/2026): `_so_admin` (na fase de observação,
    só quem `acesso.pode_mexer`) + `atendimento.edit` + `margem.edit` — a
    troca aprova a Margem, e a oferta promete a troca ao comprador. Admin
    passa nas duas permissões (como no `require_permission`). O botão
    desliga sem; a rota recusa do mesmo jeito.
    """
    if user is None:
        return False
    # Import tardio: a chave da fase de observação mora no router da caixa.
    from app.routers import atendimento as caixa
    from app.services.atendimento import acesso

    if caixa.SO_ADMIN and not acesso.pode_mexer(user):
        return False
    if user.role == UserRole.ADMIN:
        return True
    perms = user.permissions or {}
    return bool((perms.get("atendimento") or {}).get("edit")) and bool(
        (perms.get("margem") or {}).get("edit")
    )


async def primeira_trava_do_pedido(
    session: AsyncSession, numero: str, *, memo: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """A 1ª trava do PEDIDO que recusa (`travas_do_pedido`), ou None. Só banco.

    `memo` ({nº: trava}) = o painel e a lista conferem o "Trocar" e a oferta
    do mesmo pedido com UMA leitura.
    """
    if memo is not None and numero in memo:
        return memo[numero]
    p = await _carregar(session, numero)
    falha = _primeira_falha(await travas_do_pedido(session, p))
    saida = dict(falha) if falha is not None else None
    if memo is not None:
        memo[numero] = saida
    return saida


def _indisponivel(code: str, texto: Any) -> dict[str, Any]:
    return {"disponivel": False, "motivo": code, "texto_motivo": str(texto) if texto else None}


async def situacao_da_troca(
    session: AsyncSession,
    *,
    numero: str,
    motivo: Mapping[str, Any] | None,
    user: User | None,
    memo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """O `troca_envio` do bloco Ag. cancelamento: o "Trocar" pode, ou o porquê. Só banco.

    `motivo` = o bloco (`painel.bloco_do_motivo`, com o `troca_aberta` já
    lido). As travas que não dependem do produto escolhido, na ordem da
    troca: quem pode, a chave e o piloto, o motivo (a falta de estoque de UM
    item), a troca aberta e as do pedido (`travas_do_pedido`: a plataforma,
    a NF, a etiqueta, o prazo…) — com a troca desligada (o padrão) ou o
    pedido fora do piloto, o botão já nasce cinza com o porquê, em vez de
    abrir a prévia para ela recusar. Nada é gravado; nenhum GET.
    """
    try:
        if not pode_escrever(user):
            raise TrocaRecusada(SO_LEITURA, TEXTO_SO_LEITURA)
        conferir_chaves(numero)
        m = motivo or {}
        if m.get("codigo") != SEM_ESTOQUE or not m.get("pode_sugerir_troca"):
            raise TrocaRecusada("motivo_nao_permite")
        if len([x for x in (m.get("skus") or ()) if x]) > 1:
            raise TrocaRecusada("outro_item_sem_estoque")
        if m.get("troca_aberta"):
            raise TrocaRecusada("troca_em_andamento")
    except TrocaRecusada as e:
        return _indisponivel(e.code, e.detail)
    falha = await primeira_trava_do_pedido(session, numero, memo=memo)
    if falha is not None:
        return _indisponivel(str(falha["code"]), falha["texto"])
    return {"disponivel": True, "motivo": None, "texto_motivo": None}


# ── Leitura (a lista, o painel e a tela) ─────────────────────────────────


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


def _float(valor: Decimal | None) -> float | None:
    return float(valor) if valor is not None else None


def pode_retomar(troca: AtendimentoTroca, agora: datetime | None = None) -> bool:
    if troca.estado in FECHADOS:
        return False
    vez = troca.em_execucao_ate
    if vez is None:
        return True
    if vez.tzinfo is None:
        vez = vez.replace(tzinfo=UTC)
    return vez < (agora or _agora())


def troca_out(troca: AtendimentoTroca, *, ve_custo: bool) -> dict[str, Any]:
    """O `TrocaOut` — o custo só para quem vê a Margem."""
    return {
        "id": str(troca.id),
        "pedido_bling": troca.pedido_bling,
        "bling_id": troca.bling_id,
        "numeroloja": troca.numeroloja,
        "plataforma": troca.plataforma,
        "conversa_id": str(troca.conversa_id) if troca.conversa_id else None,
        "motivo_codigo": troca.motivo_codigo,
        "sku_antigo": troca.sku_antigo,
        "sku_novo": troca.sku_novo,
        "produto_novo_id": troca.produto_novo_id,
        "descricao_nova": troca.descricao_nova,
        "quantidade": troca.quantidade,
        "valor_unitario": _float(troca.valor_unitario),
        "nivel": troca.nivel,
        "automatica": troca.automatica,
        "custo_antigo": _float(troca.custo_antigo) if ve_custo else None,
        "custo_novo": _float(troca.custo_novo) if ve_custo else None,
        "saldo_ao_vivo": _float(troca.saldo_ao_vivo),
        "aceite_fonte": troca.aceite_fonte,
        "mensagem_aceite_id": str(troca.mensagem_aceite_id) if troca.mensagem_aceite_id else None,
        "aceite_texto": troca.aceite_texto,
        "aceite_em": _iso(troca.aceite_em),
        "estado": troca.estado,
        "aberta": troca.estado not in FECHADOS,
        "pode_retomar": pode_retomar(troca),
        "codigo_erro": troca.codigo_erro,
        "erro": troca.erro,
        "passos": list(troca.passos or []),
        "criado_por_nome": troca.criado_por_nome,
        "created_at": _iso(troca.created_at),
        "concluida_em": _iso(troca.concluida_em),
    }


def resumo_aberta(troca: AtendimentoTroca) -> dict[str, Any]:
    """O `troca_aberta` do bloco Ag. cancelamento (painel e lista): sem custo."""
    return {
        "id": str(troca.id),
        "estado": troca.estado,
        "sku_antigo": troca.sku_antigo,
        "sku_novo": troca.sku_novo,
        "nivel": troca.nivel,
        "automatica": troca.automatica,
        "criado_por_nome": troca.criado_por_nome,
        "created_at": _iso(troca.created_at),
        "codigo_erro": troca.codigo_erro,
        "erro": troca.erro,
        "pode_retomar": pode_retomar(troca),
    }


async def trocas_abertas_por_pedido(
    session: AsyncSession, numeros: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """{nº do Bling: resumo da troca aberta} — uma consulta (a lista Ag. cancelamento)."""
    lista = sorted({n for n in numeros if n})
    if not lista:
        return {}
    linhas = (
        await session.execute(
            select(AtendimentoTroca).where(
                AtendimentoTroca.pedido_bling.in_(lista),
                AtendimentoTroca.estado.not_in(tuple(FECHADOS)),
            )
        )
    ).scalars()
    return {t.pedido_bling: resumo_aberta(t) for t in linhas}


async def troca_aberta_do_pedido(session: AsyncSession, numero: str) -> dict[str, Any] | None:
    return (await trocas_abertas_por_pedido(session, [numero])).get(numero)


async def listar(
    session: AsyncSession, *, abertas: bool = True, pedido: str | None = None, limite: int = 200
) -> list[AtendimentoTroca]:
    """As trocas (as abertas primeiro na tela: as paradas no meio pedem Retomar)."""
    consulta = select(AtendimentoTroca)
    if abertas:
        consulta = consulta.where(AtendimentoTroca.estado.not_in(tuple(FECHADOS)))
    if pedido:
        consulta = consulta.where(AtendimentoTroca.pedido_bling == pedido.strip())
    consulta = consulta.order_by(AtendimentoTroca.created_at.desc()).limit(limite)
    return list((await session.execute(consulta)).scalars().all())
