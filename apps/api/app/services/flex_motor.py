"""Flex por anúncio: o motor que decide e aplica (projeto Flex, etapa 3).

Procedimento: Downloads/procedimento-flex.md; fatos das APIs e críticas em
relatorios/Flex_analise_02-10-2026.md. As DECISÕES de 02/10/2026 mandam onde a
especificação diverge:

UNIDADE: o ANÚNCIO (integration_id, external_id), não a variação — o Flex do
ML (`/flex/sites/MLB/items/{id}/v2`) e o da Shopee (`logistic_info` do item)
valem para o anúncio inteiro.

REGRA (função pura `decidir`, testada em tabela):
  • liga só se TODAS as variações vendáveis do anúncio são de famílias com
    produto .sp ATIVO e, para cada família, saldo Flex >= flex_n_liga;
  • TODAS as variações do anúncio entram, não só as de vínculo vivo: a que
    só tem vínculo morto, ou que o anúncio tem na plataforma e nenhum
    vínculo cobre, deixa o anúncio inelegível (o Flex vale para o anúncio
    inteiro — ela também sairia de São Bernardo); a variação parada
    (estoque publicado 0) só fica fora da conta se tiver .sp ativo — sem
    .sp, volta a vender pelo Flex quando o estoque for republicado;
  • desliga quando qualquer família fica < flex_n_desliga; entre os dois,
    mantém o que está (histerese, pelo estado OBSERVADO na plataforma, ou a
    decisão anterior quando ainda não foi lido) — sem isso o Flex pisca a
    cada venda, e o ML pede para não ligar/desligar em sequência;
  • kit (SKU com '+') fica fora enquanto flex_kits=False;
  • estoque desconhecido (família sem .sp ativo) NUNCA liga;
  • no máximo flex_max_anuncios_por_familia anúncios com Flex por família
    (`decidir_lote`) — o mesmo .sp aparece em N anúncios (clássico, premium,
    várias contas) e cada um pode vender tudo.
  • negação por padrão: nas contas permitidas, o que não é elegível deve
    ficar DESLIGADO — inclusive anúncio sem vínculo vivo (vem da tabela de
    anúncios importados ou de um estado antigo).

SALDO FLEX (por SKU .sp): `products.stock` do .sp (saldo virtual do Bling,
ativo, gêmeos de SKU contados uma vez) MENOS os pedidos Flex ainda em aberto
que não foram baixados no .sp (`flex_pedido.no_sp = false`) — a venda Flex
reserva no lote do anúncio (.ci/.ra/.pi) até o robô de prioridade levá-la ao
.sp (etapa 2), mas a peça sai de São Bernardo —, MENOS os que já saíram sem
passar pelo .sp e esperam o acerto do estoque (`acertado_em`), MENOS os que
o robô levou ao .sp e o `products.stock` ainda não mostra (`sp_em`; ver
`calcular_saldos`). Antes de LIGAR, o saldo é conferido no Bling
(`_conferir_no_bling`): sem confirmação, não liga. O .sp é montado com
`prioridade_estoque.sku_alvo` e filtrado por LOTES_DE_VENDA (NÃO com
`estoque_familia.irmaos`: para `dg053.ci+x1` ele monta `dg053.sp+x1.sp`, que
não existe).

MODOS (`flex_modo`):
  desligado  não faz nada;
  observar   calcula, LÊ o estado real e grava estado + log, mas NUNCA
             escreve na plataforma (as escritas viram `simulado` no log);
  piloto / ativo  escrevem só nas contas de `flex_contas`, até
             `flex_teto_escritas_por_rodada` por rodada. DESLIGAR é
             automático; LIGAR fica `aguardando_aprovacao` até uma pessoa
             aprovar na tela (o ML pede: "evitar processos automáticos de
             ativação… decisão deliberada do vendedor"). A aprovação vale
             para UMA ligação: usada (ou a regra deixou de querer ligar), some.
  Shopee: só LÊ, a menos que `flex_shopee_escrita=True` (a escrita por
  anúncio saiu da lista de parâmetros da doc atual do update_item).

ESCRITA: uma de cada vez por anúncio — `pg_try_advisory_xact_lock` no
namespace SYNC do projeto, com o estado relido DENTRO da trava; só escreve com
o estado observado fresco (lido há pouco); falha passageira espera
(`proxima_tentativa`, com espera crescente); recusa da plataforma ao LIGAR
(403 "item down", 404) não se repete sozinha (`recusa`); ao DESLIGAR tenta de
novo em 1 h (anúncio pausado que volta a ficar ativo não pode ficar vendendo
Flex por um dia). Toda decisão que MUDA e toda escrita viram linha em
`flex_log` com o antes e o depois.

FATOS DAS CONTAS (revisão de 02/10/2026) — o que o motor confere antes:
  • a CONTA pode ter Flex? (`flex_conta`, relido de hora em hora) — ML: só a
    assinatura "in" de subscriptions/v1 (há contas "out", "pending", 404 e
    403); Shopee: a Entrega Direta (90022) ligada NA LOJA (está desligada nas
    14). Conta que não pode não tem leitura nem escrita por anúncio, e os
    anúncios dela ficam "não pode ter Flex" com o motivo da conta, sem pedir
    aprovação;
  • aprovação só se pede para anúncio LIDO desligado (o Flex já está ligado
    na maioria dos anúncios das contas "in"); a aprovação é para UMA
    ligação: lido já ligado, ela some — o motor nunca religa sozinho com uma
    aprovação velha;
  • o anúncio que não está ativo (pausado, em revisão, inativo, encerrado)
    não ocupa vaga da família, nunca pede aprovação e, com Flex ligado, é
    desligado — o status vem do mais novo entre a descoberta da conta, a
    leitura da Shopee e a importação (sem chamada extra por anúncio);
  • o anúncio que o DaVinci não conhece: uma vez por dia o motor lista TODOS
    os ids da conta do ML (ativos e pausados) e o que não tem vínculo nem
    importação entra no estado como "fora do DaVinci" — desligado;
  • a fila de leituras anda em rodízio entre as contas e pela última
    tentativa: leitura que falha espera (crescente) e uma conta com 403 não
    toma as vagas das outras.

QUEM CHAMA: o cron do worker (`flex_motor_tick`, de flex_intervalo_min em
flex_intervalo_min), o botão "Sincronizar" da tela (job), a aprovação (só o
anúncio aprovado; com a rodada ocupada, um job que tenta de novo), o gancho do
pedido Flex (`flex_reavaliar_run`: passe barato, sem leitura, que só DESLIGA
— ver worker) e a emergência (job `flex_emergencia_run`).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import (
    BlingOrder,
    FlexAnuncioEstado,
    FlexConta,
    FlexEmergencia,
    FlexLog,
    FlexPedido,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Product,
    ProductLink,
)
from app.models.flex import FLEX_STATUS_ANUNCIO
from app.services import flex_config, flex_envio, flex_local
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.bling_situacoes import (
    SITUACAO_ATENDIDO,
    SITUACAO_CANCELADO,
    SITUACAO_EM_ANDAMENTO,
)
from app.services.estoque_familia import LOTES_DE_VENDA, chave_familia, lote_de
from app.services.marketplaces import flex_api
from app.services.prioridade_estoque import analisa_codigo, sku_alvo
from app.services.vinculo_saude import eh_duplicado

logger = structlog.get_logger()

LIGADO = "ligado"
DESLIGADO = "desligado"
INELEGIVEL = "inelegivel"
# O lote do Flex (era "sp" fixo) vem do local de saída editável na aba Flex
# (services/flex_local, Eduardo 08/10/2026) — lido no começo de cada rodada.
def _lote_flex() -> str:
    return flex_local.lote()

# Trava da RODADA (uma de cada vez: cron, botão, aprovação, gancho). ASCII
# "flex"; o namespace é o SYNC compartilhado do projeto (advisory_lock.py).
_MOTOR_LOCK_KEY = 0x666C6578

# Leituras por rodada. ML: 1 GET por anúncio (o limite do Flex é 1000/min por
# aplicação, somando ligar/desligar e o Turbo) — 400 a cada 15 min fica longe
# dele; com mais anúncios, a fila anda pelos mais velhos/mais urgentes
# primeiro, em rodízio entre as contas. Shopee: 50 por chamada.
_TETO_LEITURAS_ML = 400
# Revisão de 05/10/2026: leitura mais velha que isto volta para a frente da
# fila mesmo com o Flex "batendo" com a regra — senão, com centenas de
# anúncios esperando desligar (em observar nunca desligam), o anúncio já certo
# nunca era relido e uma mudança feita no painel do ML não aparecia nunca.
_RELER_NO_MAXIMO = timedelta(hours=6)
# As leituras param aqui (contado do começo da rodada) para o `_gravar` rodar
# dentro do timeout do job (900 s): com o ML lento, 400 leituras passavam de
# 15 min, o arq cancelava e a rodada inteira se perdia (nada gravado).
_PRAZO_LEITURAS_S = 540
# 5xx/rede seguidos na mesma conta: o endpoint está ruim, para a conta nesta
# rodada (como o 429) em vez de gastar o prazo com ela.
_REPETIR_SEGUIDOS = 5
_TETO_LEITURAS_SHOPEE = 2000
# Leitura do ML com 403 sem "item down" (SEM_PERMISSAO): um anúncio isolado
# (de outro vendedor, vínculo trocado de conta) não interrompe a conta; só
# 401/refresh recusado ou esta quantidade seguida.
_SEM_PERMISSAO_SEGUIDOS = 3
# Emergência (job do worker): escritas por job — o resto fica em `restantes`
# e apertar de novo continua. Com `_EMERGENCIA_PARALELO` chamadas ao mesmo
# tempo (cada uma num anúncio diferente) fica perto de 200–250/min, abaixo
# dos 1000/min do ML: a maior conta (≈2.000 anúncios) sai num job só.
_TETO_EMERGENCIA = 3000
_EMERGENCIA_PARALELO = 4
# Andamento da emergência gravado no banco (a tela consulta) de tanto em tanto.
_EMERGENCIA_PROGRESSO_S = 2.0
# Emergência: quanto espera (s) a trava de um anúncio que a rodada está
# lendo ou escrevendo — uma chamada à plataforma leva poucos segundos.
_ESPERA_TRAVA_EMERGENCIA = 10.0
# A conta pode ter Flex? (assinatura do ML / canal da loja Shopee): vale por
# 1 h — a assinatura muda raramente e é uma chamada por conta.
_VALIDADE_CONTA = timedelta(hours=1)
# Descoberta dos anúncios da conta (ML): uma vez por dia (e no primeiro uso);
# a que falhou tenta de novo em 1 h. Até 100 páginas de 100 ids por status e
# no máximo 3 contas por rodada (a rodada não pode ficar presa nisso).
_DESCOBERTA_A_CADA = timedelta(hours=24)
_DESCOBERTA_FALHOU_ESPERA = timedelta(hours=1)
_DESCOBERTA_MAX_PAGINAS = 100
_DESCOBERTA_CONTAS_POR_RODADA = 3
# A emergência redescobre a conta se a última descoberta tiver mais que isto:
# "desligar tudo" tem de ver o anúncio novo que o DaVinci não conhece.
_DESCOBERTA_EMERGENCIA = timedelta(hours=1)
MOTIVO_FORA_DO_DAVINCI = "anúncio fora do DaVinci — sem vínculo"
# Pedido Flex que ainda desconta do saldo: aberto no Bling e detectado há no
# máximo isso (a mesma janela do shipment check que o registra).
_JANELA_PEDIDOS = timedelta(days=30)
_SITUACOES_FECHADAS = (
    str(SITUACAO_ATENDIDO),
    str(SITUACAO_CANCELADO),
    str(SITUACAO_EM_ANDAMENTO),
    "excluido",
)
# A tela usa a mesma régua na lista "Pedidos Flex sem peça em SP"
# (routers/flex.py, `abertos=true`): pedido que já saiu, foi atendido,
# cancelado ou excluído não espera mais ninguém — menos o que SAIU sem ter
# ido ao .sp (abaixo), que espera o acerto do estoque.
SITUACOES_FECHADAS = _SITUACOES_FECHADAS
# Pedido que saiu (o Bling baixou o estoque dele). Se saiu pelo Flex SEM ter
# ido ao .sp, o Bling baixou o outro lote (.ci/.ra/.pi) mas a peça saiu de
# São Bernardo: o .sp do Bling fica com peça a mais até alguém transferir.
# O desconto continua até a pessoa marcar `flex_pedido.acertado_em`.
SITUACOES_SAIU = (str(SITUACAO_EM_ANDAMENTO), str(SITUACAO_ATENDIDO))
# Recusa da plataforma ao DESLIGAR (403 "item down"/404 — o anúncio pausado
# pode responder assim): tenta de novo em 1 h. Antes eram 24 h, e o anúncio
# que o DaVinci reativava (estoque republicado) voltava a vender pelo Flex sem
# .sp até o dia seguinte.
_ESPERA_RECUSA_DESLIGAR = timedelta(hours=1)
_ESPERA_MAXIMA = timedelta(hours=6)


def _agora() -> datetime:
    return datetime.now(UTC)


# =============================================================================
# Regra pura
# =============================================================================


@dataclass(frozen=True)
class ConfigFlex:
    """Os números da regra (flex_* do .env)."""

    n_liga: int = 3
    n_desliga: int = 1
    kits: bool = False
    max_anuncios_por_familia: int = 2

    def __post_init__(self) -> None:
        # Ligar com saldo 0 nunca; e o piso de desligar não passa o de ligar
        # (configuração trocada viraria "liga e desliga na mesma rodada").
        object.__setattr__(self, "n_liga", max(1, int(self.n_liga)))
        object.__setattr__(self, "n_desliga", min(max(0, int(self.n_desliga)), self.n_liga))
        object.__setattr__(
            self, "max_anuncios_por_familia", max(0, int(self.max_anuncios_por_familia))
        )

    @classmethod
    def das_configuracoes(cls) -> ConfigFlex:
        s = get_settings()
        return cls(
            n_liga=s.flex_n_liga,
            n_desliga=s.flex_n_desliga,
            kits=bool(s.flex_kits),
            max_anuncios_por_familia=s.flex_max_anuncios_por_familia,
        )


# De onde veio a variação (ver `montar_anuncios`).
VINCULO_VIVO = "vivo"  # vínculo vivo: o DaVinci manda o estoque dela
VINCULO_MORTO = "morto"  # só vínculo morto: o DaVinci parou de mandar estoque
VINCULO_SEM = "sem"  # a plataforma tem a variação e o DaVinci nunca a vinculou


@dataclass(frozen=True)
class Variacao:
    """Uma variação do anúncio: a de um vínculo vivo — ou, para a regra NEGAR
    o Flex, a que o DaVinci não controla (vínculo morto, ou a variação que o
    anúncio tem na plataforma e nenhum vínculo cobre)."""

    sku: str | None  # products.sku do vínculo (None = produto não achado)
    ativo: bool = True  # products.situacao == 'A'
    # product_links.stock: o último número enviado ao anúncio. 0 = a variação
    # não está à venda agora; None = nunca enviado (não se sabe: conta).
    estoque_publicado: int | None = None
    vinculo: str = VINCULO_VIVO
    # Id da variação na plataforma (ML variation id, Shopee model_id) — só
    # para o motivo dizer QUAL variação, quando ela não tem SKU.
    ref: str | None = None


@dataclass(frozen=True)
class Anuncio:
    integration_id: UUID
    external_id: str
    plataforma: str  # 'ml' | 'shopee'
    variacoes: tuple[Variacao, ...] = ()
    observado: str | None = None  # 'ligado' | 'desligado' | None (não lido)
    desejado_anterior: str | None = None
    recusa: str | None = None
    # Uma pessoa aprovou ligar (e a aprovação ainda vale): passa na frente no
    # limite por família — a escolha explícita vale mais que a ordem.
    aprovado: bool = False
    # Vínculo mais antigo do anúncio: desempate do limite por família.
    desde: datetime | None = None
    # A CONTA não pode ter Flex (ML sem assinatura "in", Shopee com a Entrega
    # Direta desligada na loja) — o porquê; None = a conta pode.
    bloqueio: str | None = None
    # O DaVinci conhece o anúncio (vínculo ou importação)? False = só existe
    # no estado — achado pela descoberta da conta, ou o vínculo morreu e saiu
    # da importação.
    conhecido: bool = True
    # Status na plataforma (active, paused, under_review, inactive, closed) e
    # quando foi visto; None = não se sabe (vale como ativo).
    status: str | None = None
    status_em: datetime | None = None
    # O motor consegue LIGAR o Flex aqui? Shopee com `flex_shopee_escrita`
    # desligado: não — pedir aprovação seria um botão que não faz nada.
    pode_ligar: bool = True

    @property
    def chave(self) -> tuple[UUID, str]:
        return (self.integration_id, self.external_id)


@dataclass(frozen=True)
class SaldoSp:
    """Saldo Flex de UM SKU .sp."""

    sku_sp: str
    estoque: int | None  # products.stock do .sp ativo; None = não existe ativo
    # Pedidos Flex fora do .sp: os em aberto e os que já SAÍRAM sem passar pelo
    # .sp e ainda esperam o acerto do estoque (ver `calcular_saldos`).
    pendentes: int = 0
    # Pedidos Flex que o robô levou ao .sp, mas cuja reserva ainda não chegou
    # ao `products.stock` (o produto não foi atualizado depois da troca).
    movidos: int = 0

    @property
    def saldo(self) -> int | None:
        if self.estoque is None:
            return None
        return self.estoque - self.pendentes - self.movidos


@dataclass(frozen=True)
class Decisao:
    desejado: str  # ligado | desligado | inelegivel
    motivo: str
    familias: tuple[str, ...] = ()
    saldo: int | None = None  # menor saldo Flex entre as famílias
    skus_sp: tuple[str, ...] = field(default=(), compare=False)


def analisar_sku(sku: str | None, *, kits: bool) -> tuple[str, str] | str:
    """(família, SKU .sp) da variação, ou o motivo de não ter Flex.

    Família = `chave_familia` (sem lote; kit misturado, `cd`/`us` e SKU sem
    lote não formam). O .sp é `sku_alvo` só nos pedaços que têm lote
    (`dg053.ci+x1` → `dg053.sp+x1`)."""
    low = (sku or "").strip().lower()
    if not low:
        return "variação sem produto no DaVinci"
    familia = chave_familia(low)
    info = analisa_codigo(low)
    if familia is None or info is None:
        return f"{low}: sem lote de venda (ou kit com lotes misturados)"
    _base, tag = info
    if tag not in LOTES_DE_VENDA:  # defesa: chave_familia já filtra
        return f"{low}: lote .{tag} fora dos lotes de venda"
    if "+" in low and not kits:
        return f"{low}: kit — kits fora do Flex nesta fase (flex_kits)"
    return familia, sku_alvo(low, tag, _lote_flex())


def _estado_atual(anuncio: Anuncio) -> str:
    """Para a histerese: o que a plataforma mostrou; sem leitura, a decisão
    anterior."""
    if anuncio.observado in (LIGADO, DESLIGADO):
        return anuncio.observado
    return LIGADO if anuncio.desejado_anterior == LIGADO else DESLIGADO


def _nome_variacao(v: Variacao) -> str:
    sku = (v.sku or "").strip().lower()
    if sku:
        return sku
    return f"id {v.ref}" if v.ref else "sem SKU"


_STATUS_TEXTO = {
    "paused": "pausado",
    "under_review": "em revisão",
    "inactive": "inativo",
    "closed": "encerrado",
}
# Shopee sem `flex_shopee_escrita`: o motor só confere; ligar é à mão.
_SHOPEE_SO_LEITURA = "Shopee só leitura (flex_shopee_escrita)"


def ativo_na_plataforma(status: str | None) -> bool:
    """Status desconhecido vale como ativo (o anúncio do vínculo vivo que
    nunca foi importado nem descoberto) — a leitura e a regra seguem."""
    return status in (None, "", flex_api.STATUS_ATIVO)


def decidir(anuncio: Anuncio, saldos: Mapping[str, SaldoSp], cfg: ConfigFlex) -> Decisao:
    """O que a regra quer para UM anúncio (sem o limite por família — ver
    `decidir_lote`). Pura: nada de banco, nada de API."""
    if anuncio.bloqueio:
        # A conta não pode ter Flex: nada por anúncio adianta (nem pedir
        # aprovação — a aprovação não ligaria nada).
        return Decisao(INELEGIVEL, anuncio.bloqueio)
    if not anuncio.variacoes:
        if not anuncio.conhecido:
            return Decisao(DESLIGADO, MOTIVO_FORA_DO_DAVINCI)
        return Decisao(INELEGIVEL, "anúncio sem vínculo vivo com produto do DaVinci")
    if not ativo_na_plataforma(anuncio.status):
        # Pausado/em revisão/inativo: não vende agora — não ocupa vaga da
        # família e não pede aprovação. Com Flex ligado, desliga: quando for
        # reativado (o DaVinci republica o estoque e o ML reativa sozinho), não
        # pode voltar vendendo pelo Flex sem a regra ter olhado o saldo.
        rotulo = _STATUS_TEXTO.get(anuncio.status or "", anuncio.status or "")
        return Decisao(
            DESLIGADO, f"anúncio {rotulo} na plataforma — o Flex fica desligado até reativar"
        )
    # Variação que o DaVinci não controla: o Flex vale para o anúncio INTEIRO,
    # então ela também sairia de São Bernardo — e o DaVinci não sabe se há
    # peça. Vale mesmo com estoque 0 (o estoque dela não é mandado por aqui).
    for v in anuncio.variacoes:
        if v.vinculo == VINCULO_MORTO:
            return Decisao(
                INELEGIVEL, f"variação {_nome_variacao(v)} com vínculo morto no DaVinci"
            )
        if v.vinculo == VINCULO_SEM:
            return Decisao(
                INELEGIVEL, f"variação {_nome_variacao(v)} sem vínculo com produto do DaVinci"
            )
    # Variação parada (estoque publicado 0) só fica fora da conta quando o
    # .sp dela existe: quando o estoque voltar a ser publicado, ela volta a
    # vender pelo Flex sem passar pelo motor — sem .sp não há peça em SP.
    vendaveis: list[Variacao] = []
    for v in anuncio.variacoes:
        if v.estoque_publicado is not None and v.estoque_publicado <= 0:
            r = analisar_sku(v.sku, kits=cfg.kits)
            sp = None if isinstance(r, str) else saldos.get(r[1])
            if sp is not None and sp.estoque is not None:
                continue
            falta = r if isinstance(r, str) else f"{r[1]} não existe ativo"
            return Decisao(
                INELEGIVEL,
                f"variação {_nome_variacao(v)} parada sem .{_lote_flex()} ({falta}) — volta a "
                "vender quando o estoque for publicado",
            )
        vendaveis.append(v)
    if not vendaveis:
        return Decisao(DESLIGADO, "nenhuma variação com estoque publicado")

    pares: list[tuple[str, str]] = []
    for v in vendaveis:
        if not v.ativo:
            return Decisao(INELEGIVEL, f"{(v.sku or '?').lower()}: produto inativo ou excluído")
        r = analisar_sku(v.sku, kits=cfg.kits)
        if isinstance(r, str):
            return Decisao(INELEGIVEL, r)
        pares.append(r)
    familias = tuple(sorted({f for f, _ in pares}))
    skus_sp = tuple(sorted({s for _, s in pares}))

    saldos_sp: list[tuple[str, int]] = []
    for sku_sp in skus_sp:
        sp = saldos.get(sku_sp)
        if sp is None or sp.saldo is None:
            return Decisao(
                INELEGIVEL,
                f"{sku_sp} não existe ativo — sem estoque .{_lote_flex()} conhecido, nunca liga",
                familias,
                None,
                skus_sp,
            )
        saldos_sp.append((sku_sp, sp.saldo))
    sku_menor, menor = min(saldos_sp, key=lambda par: (par[1], par[0]))

    if menor < cfg.n_desliga:
        d = Decisao(
            DESLIGADO,
            f"saldo Flex {menor} em {sku_menor} abaixo de {cfg.n_desliga}",
            familias,
            menor,
            skus_sp,
        )
    elif menor >= cfg.n_liga:
        d = Decisao(
            LIGADO,
            f"saldo Flex {menor} em {sku_menor} (liga com {cfg.n_liga})",
            familias,
            menor,
            skus_sp,
        )
    elif _estado_atual(anuncio) == LIGADO:
        d = Decisao(
            LIGADO,
            f"saldo Flex {menor} em {sku_menor}: entre {cfg.n_desliga} e {cfg.n_liga}, "
            "continua ligado (histerese)",
            familias,
            menor,
            skus_sp,
        )
    else:
        d = Decisao(
            DESLIGADO,
            f"saldo Flex {menor} em {sku_menor}: precisa de {cfg.n_liga} para ligar",
            familias,
            menor,
            skus_sp,
        )
    if d.desejado == LIGADO and anuncio.recusa:
        return replace(
            d, desejado=INELEGIVEL, motivo=f"a plataforma recusou ligar: {anuncio.recusa}"
        )
    if d.desejado == LIGADO and not anuncio.pode_ligar and anuncio.observado != LIGADO:
        # O motor não liga aqui (Shopee só leitura): não pede aprovação nem
        # toma a vaga da família de um anúncio que ele consegue ligar. Ligado
        # à mão (observado ligado) continua contando — ele vende pelo Flex.
        return replace(d, desejado=DESLIGADO, motivo=f"{_SHOPEE_SO_LEITURA} — {d.motivo}")
    return d


def _ordem_limite(anuncio: Anuncio) -> tuple:
    """Quem fica com o Flex quando a família passa do limite — determinística:
    1º quem JÁ está ligado na plataforma (não troca o Flex de anúncio à toa);
    2º quem uma pessoa aprovou;
    3º quem a regra já queria ligado (aprovação pedida);
    4º o anúncio mais antigo no DaVinci (vínculo mais velho);
    5º o id (conta, anúncio). O DaVinci não guarda vendas por anúncio, por
    isso "mais vendas" não entra."""
    return (
        0 if anuncio.observado == LIGADO else 1,
        0 if anuncio.aprovado else 1,
        0 if anuncio.desejado_anterior == LIGADO else 1,
        anuncio.desde or datetime.max.replace(tzinfo=UTC),
        str(anuncio.integration_id),
        anuncio.external_id,
    )


def decidir_lote(
    anuncios: Iterable[Anuncio], saldos: Mapping[str, SaldoSp], cfg: ConfigFlex
) -> dict[tuple[UUID, str], Decisao]:
    """`decidir` de cada anúncio + o limite de anúncios ligados por família."""
    lista = list(anuncios)
    decisoes = {a.chave: decidir(a, saldos, cfg) for a in lista}
    contagem: Counter[str] = Counter()
    for a in sorted((a for a in lista if decisoes[a.chave].desejado == LIGADO), key=_ordem_limite):
        d = decisoes[a.chave]
        cheia = next(
            (f for f in d.familias if contagem[f] >= cfg.max_anuncios_por_familia), None
        )
        if cheia is None:
            contagem.update(d.familias)
            continue
        decisoes[a.chave] = replace(
            d,
            desejado=DESLIGADO,
            motivo=(
                f"limite de {cfg.max_anuncios_por_familia} anúncio(s) com Flex na família "
                f"{cheia} — {d.motivo}"
            ),
        )
    return decisoes


# =============================================================================
# Banco: anúncios, saldos, estados
# =============================================================================

_SHOPEE_ITEM_MODELO = re.compile(r"^([0-9]+)_[0-9]+$")


def _id_anuncio(plataforma: str, external_id: Any) -> str:
    """Id do anúncio como a plataforma o quer. A importação da Shopee às vezes
    grava `item_model` no anúncio importado — o anúncio é a primeira parte."""
    ext = str(external_id or "").strip()
    if plataforma == flex_envio.PLATAFORMA_SHOPEE:
        m = _SHOPEE_ITEM_MODELO.match(ext)
        if m:
            return m.group(1)
    return ext


async def integracoes_permitidas(
    session: AsyncSession, contas: Collection[UUID]
) -> dict[UUID, Integration]:
    """As contas de `flex_contas` que existem, são ML/Shopee e não estão
    arquivadas. Lista vazia = nenhuma (negação por padrão)."""
    if not contas:
        return {}
    rows = await session.execute(
        select(Integration).where(
            Integration.id.in_(list(contas)),
            Integration.platform.in_([IntegrationPlatform.ML, IntegrationPlatform.SHOPEE]),
            Integration.archived_at.is_(None),
        )
    )
    return {i.id: i for i in rows.scalars().all()}


def _plataforma_de(integ: Integration) -> str | None:
    return flex_envio.plataforma_flex(integ.platform)


def _chave_variacao(variation_id: Any) -> str:
    """Id da variação para casar vínculo × variação da plataforma. Anúncio sem
    variação: ML grava NULL, Shopee "0" — os dois viram ""."""
    v = str(variation_id if variation_id is not None else "").strip()
    return "" if v in ("", "0", "None") else v


def _sku_ml_variacao(variacao: Mapping[str, Any]) -> str | None:
    """SKU da variação do ML no `listings.raw_data` — a mesma ordem da
    importação (ml._iter_ml_variants): SELLER_SKU, seller_custom_field, sku."""
    for attr in variacao.get("attributes") or []:
        if isinstance(attr, Mapping) and str(attr.get("id") or "").upper() == "SELLER_SKU":
            sku = str(attr.get("value_name") or attr.get("value") or "").strip()
            if sku:
                return sku
    for campo in ("seller_custom_field", "sku"):
        sku = str(variacao.get(campo) or "").strip()
        if sku:
            return sku
    return None


def _int_ou_none(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def variacoes_da_plataforma(plataforma: str, raw: Any) -> list[tuple[str, str | None, int | None]]:
    """As variações que o anúncio tinha na plataforma na última importação
    (`listings.raw_data`): [(id da variação, SKU, estoque)].

    ML: o item inteiro, com `variations[]` (cada uma com SELLER_SKU e
    `available_quantity`); item sem variação não entra (o vínculo do anúncio
    o cobre). Shopee: a importação guarda UM modelo por linha (`{"item",
    "model"}`) — a lista completa só existe somando as linhas de `item_model`;
    o que a linha tiver entra."""
    if not isinstance(raw, Mapping):
        return []
    out: list[tuple[str, str | None, int | None]] = []
    if plataforma == flex_envio.PLATAFORMA_ML:
        for v in raw.get("variations") or []:
            if not isinstance(v, Mapping):
                continue
            ref = _chave_variacao(v.get("id"))
            if ref:
                out.append((ref, _sku_ml_variacao(v), _int_ou_none(v.get("available_quantity"))))
        return out
    if plataforma == flex_envio.PLATAFORMA_SHOPEE:
        modelo = raw.get("model")
        if isinstance(modelo, Mapping):
            ref = _chave_variacao(modelo.get("model_id"))
            if ref:
                estoque = ((modelo.get("stock_info_v2") or {}).get("summary_info") or {}).get(
                    "total_available_stock"
                )
                sku = str(modelo.get("model_sku") or "").strip() or None
                out.append((ref, sku, _int_ou_none(estoque)))
    return out


def _mais_novo(a: datetime | None, b: datetime | None) -> bool:
    """`a` é pelo menos tão novo quanto `b`? (sem data nunca ganha de data)."""
    return a is not None and (b is None or a >= b)


async def montar_anuncios(
    session: AsyncSession, integracoes: Mapping[UUID, Integration]
) -> list[Anuncio]:
    """Os anúncios das contas, agrupados por (conta, anúncio).

    Fonte principal: os vínculos VIVOS (`morto_desde IS NULL`) — cada um é uma
    variação com o produto (SKU, situação) e o último estoque enviado. A
    ligação anúncio → família é refeita a cada rodada (o SKU pode ter sido
    trocado no marketplace — crítica C4).

    O Flex vale para o anúncio INTEIRO, então a regra precisa ver TODAS as
    variações dele, não só as que o DaVinci controla. Entram também, para a
    regra NEGAR o Flex (revisão de 02/10/2026):
      • a variação que só tem vínculo MORTO (o DaVinci parou de mandar o
        estoque dela; o duplicado da 0333 não conta — outro vínculo vivo
        cobre a mesma variação);
      • a variação que o anúncio tem na plataforma (`listings.raw_data`, a
        foto da última importação) e nenhum vínculo vivo cobre — a cor que
        nunca foi vinculada continua vendendo.

    Negação por padrão: anúncio importado (`listings`, não encerrado) da conta
    que não tem vínculo vivo entra SEM variações vivas — a regra o dá como
    inelegível e, se o Flex estiver ligado nele, desliga.

    Status (revisão de 02/10/2026): o `listings.status` da última importação
    (com `imported_at`) vai para o anúncio — inclusive o encerrado que ainda
    tem vínculo vivo. `_com_estado` troca pelo do estado quando o do estado
    for mais novo (descoberta da conta, leitura da Shopee)."""
    if not integracoes:
        return []
    ids = list(integracoes)
    plataforma = {iid: _plataforma_de(i) for iid, i in integracoes.items()}
    variacoes: dict[tuple[UUID, str], list[Variacao]] = defaultdict(list)
    cobertas: dict[tuple[UUID, str], set[str]] = defaultdict(set)
    mortas: dict[tuple[UUID, str], dict[str, Variacao]] = defaultdict(dict)
    desde: dict[tuple[UUID, str], datetime] = {}
    rows = await session.execute(
        select(
            ProductLink.integration_id,
            ProductLink.external_id,
            ProductLink.variation_id,
            ProductLink.stock,
            ProductLink.created_at,
            ProductLink.morto_desde,
            ProductLink.morto_motivo,
            Product.sku,
            Product.situacao,
        )
        .join(Product, Product.id == ProductLink.product_id)
        .where(
            ProductLink.integration_id.in_(ids),
            ProductLink.platform.in_([IntegrationPlatform.ML, IntegrationPlatform.SHOPEE]),
        )
    )
    for r in rows.all():
        p = plataforma.get(r.integration_id)
        ext = _id_anuncio(p or "", r.external_id)
        if p is None or not ext:
            continue
        chave = (r.integration_id, ext)
        ref = _chave_variacao(r.variation_id)
        if r.morto_desde is not None:
            if not eh_duplicado(r.morto_motivo):
                mortas[chave].setdefault(
                    ref,
                    Variacao(
                        sku=r.sku,
                        ativo=(r.situacao or "") == "A",
                        estoque_publicado=r.stock,
                        vinculo=VINCULO_MORTO,
                        ref=ref or None,
                    ),
                )
            continue
        cobertas[chave].add(ref)
        variacoes[chave].append(
            Variacao(
                sku=r.sku,
                ativo=(r.situacao or "") == "A",
                estoque_publicado=r.stock,
                ref=ref or None,
            )
        )
        if r.created_at is not None and (chave not in desde or r.created_at < desde[chave]):
            desde[chave] = r.created_at

    # Vínculo morto de uma variação que outro vínculo vivo cobre (o vínculo foi
    # refeito) não pesa; o resto entra como variação sem controle. Só nos
    # anúncios que ainda têm vínculo vivo: o anúncio todo morto (excluído ou
    # encerrado na plataforma) não gasta leitura — se estiver na importação
    # ou já tiver estado gravado, a regra já o nega por não ter vínculo vivo.
    for chave, por_ref in mortas.items():
        if chave not in cobertas:
            continue
        for ref, v in por_ref.items():
            if ref not in cobertas[chave]:
                variacoes[chave].append(v)

    # Só os pedaços do `raw_data` que importam (as variações do ML, o modelo
    # da Shopee) — o item inteiro tem fotos, atributos e descrição, e a
    # rodada lê todos os anúncios das contas a cada 15 min.
    importados = await session.execute(
        select(
            Listing.integration_id,
            Listing.external_id,
            Listing.status,
            Listing.imported_at,
            Listing.raw_data["variations"].label("variations"),
            Listing.raw_data["model"].label("model"),
        ).where(Listing.integration_id.in_(ids))
    )
    vistas: dict[tuple[UUID, str], set[str]] = defaultdict(set)
    status: dict[tuple[UUID, str], tuple[str, datetime | None]] = {}
    for iid, ext_bruto, st, quando, ml_variacoes, shopee_modelo in importados.all():
        p = plataforma.get(iid)
        ext = _id_anuncio(p or "", ext_bruto)
        if p is None or not ext:
            continue
        chave = (iid, ext)
        valor = getattr(st, "value", st)
        # Shopee: uma linha por modelo — vale a importação mais nova.
        anterior = status.get(chave)
        if valor and (anterior is None or _mais_novo(quando, anterior[1])):
            status[chave] = (str(valor), quando)
        if st == ListingStatus.CLOSED:
            continue  # encerrado não entra pela importação (só o status, acima)
        variacoes.setdefault(chave, [])
        raw = {"variations": ml_variacoes, "model": shopee_modelo}
        for ref, sku, estoque in variacoes_da_plataforma(p, raw):
            if ref in cobertas.get(chave, set()) or ref in vistas[chave]:
                continue
            if ref in mortas.get(chave, {}):
                continue  # já entrou como vínculo morto
            vistas[chave].add(ref)
            variacoes[chave].append(
                Variacao(sku=sku, estoque_publicado=estoque, vinculo=VINCULO_SEM, ref=ref)
            )

    return [
        Anuncio(
            integration_id=iid,
            external_id=ext,
            plataforma=plataforma[iid] or "",
            variacoes=tuple(vs),
            desde=desde.get((iid, ext)),
            status=status[(iid, ext)][0] if (iid, ext) in status else None,
            status_em=status[(iid, ext)][1] if (iid, ext) in status else None,
        )
        for (iid, ext), vs in variacoes.items()
    ]


def _demanda_por_peca(
    itens: Iterable[tuple[str | None, Any]], lote_flex: str | None = None
) -> Counter[str]:
    """Peças (base sem lote) que pedidos Flex fora do .sp vão tirar de São
    Bernardo. Pedaço já no .sp não conta (o saldo virtual do .sp já desconta a
    reserva dele); kit conta cada peça com lote — o fone a001 de um kit sai do
    mesmo .sp que o a001 avulso."""
    demanda: Counter[str] = Counter()
    for codigo, qtd in itens:
        try:
            q = int(qtd or 1)
        except (TypeError, ValueError):
            q = 1
        for pedaco in (codigo or "").lower().split("+"):
            p = pedaco.strip()
            lote = lote_de(p) if p else None
            if lote in LOTES_DE_VENDA and lote != (lote_flex or _lote_flex()):
                demanda[p[: -(len(lote) + 1)]] += q
    return demanda


def acerto_pendente(
    situacao: str | None,
    no_sp: bool,
    acertado_em: datetime | None,
    skus: Iterable[str | None],
    lote: str | None = None,
) -> bool:
    """O pedido Flex SAIU (em andamento/atendido) sem ter ido ao .sp e ainda
    espera alguém acertar o estoque no Bling? Só conta quem tem peça com lote
    de venda fora do .sp (é o que o saldo Flex desconta)."""
    if no_sp or acertado_em is not None or str(situacao or "") not in SITUACOES_SAIU:
        return False
    # `lote`: o do PEDIDO (flex_pedido.lote) — trocar o local depois que ele
    # saiu não muda o que falta acertar.
    return bool(_demanda_por_peca(((s, 1) for s in skus), lote))


def _pendentes(sku_sp: str, demanda: Mapping[str, int]) -> int:
    """Quanto o .sp deve aos pedidos Flex: no kit, a peça mais pedida (o
    estoque do kit .sp já é o da peça mais escassa — conservador)."""
    pecas = [
        p.strip()[: -(len(_lote_flex()) + 1)]
        for p in sku_sp.split("+")
        if lote_de(p.strip()) == _lote_flex()
    ]
    return max((int(demanda.get(p, 0)) for p in pecas), default=0)


def _demanda_no_sp(itens: Iterable[tuple[str | None, Any]]) -> Counter[str]:
    """Peças (SKU .sp, minúsculo) que os pedidos Flex JÁ levados ao .sp
    reservam: cada pedaço do item que está no lote .sp."""
    demanda: Counter[str] = Counter()
    for codigo, qtd in itens:
        try:
            q = int(qtd or 1)
        except (TypeError, ValueError):
            q = 1
        for pedaco in (codigo or "").lower().split("+"):
            p = pedaco.strip()
            if p and lote_de(p) == _lote_flex():
                demanda[p] += q
    return demanda


async def calcular_saldos(session: AsyncSession, skus_sp: Collection[str]) -> dict[str, SaldoSp]:
    """Saldo Flex de cada SKU .sp pedido.

    Estoque: `products.stock` (saldo VIRTUAL do Bling — já desconta as
    reservas) do produto ATIVO com aquele SKU. SKU não é único em `products`:
    gêmeos do mesmo produto do Bling (`bling_product_id`) contam uma vez; dois
    produtos do Bling diferentes com o mesmo SKU valem o MENOR (nunca soma).
    Sem produto ativo: estoque None — desconhecido nunca liga.

    Descontos (revisão de 02/10/2026):
      • pedido Flex FORA do .sp (`no_sp = false`) ainda em aberto — reserva no
        outro lote, mas a peça sai de São Bernardo;
      • pedido Flex que SAIU (em andamento/atendido) sem ter ido ao .sp e
        ainda sem `acertado_em`: o Bling baixou o outro lote e o .sp ficou
        com peça a mais — sem janela de dias: vale até alguém acertar;
      • pedido Flex que o robô LEVOU ao .sp (`sp_em`), em aberto, enquanto o
        produto .sp não foi atualizado depois da troca: a reserva só chega
        ao `products.stock` pelo webhook do Bling, que às vezes não vem."""
    alvos = sorted({s.strip().lower() for s in skus_sp if s and s.strip()})
    if not alvos:
        return {}
    por_sku: dict[str, dict[Any, int]] = defaultdict(dict)
    rows = await session.execute(
        select(
            func.lower(Product.sku).label("sku"),
            Product.stock,
            Product.bling_product_id,
            Product.id,
        ).where(func.lower(Product.sku).in_(alvos), Product.situacao == "A")
    )
    for r in rows.all():
        chave = r.bling_product_id if r.bling_product_id is not None else r.id
        por_sku[r.sku][chave] = int(r.stock or 0)

    corte = _agora() - _JANELA_PEDIDOS
    situacao = func.coalesce(BlingOrder.situacao, "")
    itens = await session.execute(
        select(BlingOrder.item_codigo, BlingOrder.item_quantidade)
        .join(FlexPedido, FlexPedido.bling_id == BlingOrder.bling_id)
        .where(
            FlexPedido.no_sp.is_(False),
            # Só os pedidos do lote do local de agora: o que saiu de outro
            # local (antes de uma troca) não tira peça deste estoque.
            FlexPedido.lote == _lote_flex(),
            or_(
                and_(FlexPedido.detectado_em >= corte, situacao.notin_(_SITUACOES_FECHADAS)),
                and_(situacao.in_(SITUACOES_SAIU), FlexPedido.acertado_em.is_(None)),
            ),
        )
    )
    demanda = _demanda_por_peca(itens.all())

    # Levados ao .sp pelo robô e ainda não vistos no `products.stock`.
    movidos_rows = (
        await session.execute(
            select(BlingOrder.item_codigo, BlingOrder.item_quantidade, FlexPedido.sp_em)
            .join(FlexPedido, FlexPedido.bling_id == BlingOrder.bling_id)
            .where(
                FlexPedido.no_sp.is_(True),
                FlexPedido.lote == _lote_flex(),
                FlexPedido.sp_em.is_not(None),
                FlexPedido.detectado_em >= corte,
                situacao.notin_(_SITUACOES_FECHADAS),
            )
        )
    ).all()
    movidos: Counter[str] = Counter()
    if movidos_rows:
        pecas = set()
        for codigo, _q, _em in movidos_rows:
            pecas.update(_demanda_no_sp([(codigo, 1)]))
        # O produto foi atualizado DEPOIS da troca (webhook/sincronização do
        # Bling): o estoque dele já traz a reserva. Gêmeos: vale o mais velho.
        atualizado: dict[str, datetime] = {}
        for sku, quando in (
            await session.execute(
                select(func.lower(Product.sku), func.min(Product.updated_at))
                .where(func.lower(Product.sku).in_(sorted(pecas)), Product.situacao == "A")
                .group_by(func.lower(Product.sku))
            )
        ).all():
            atualizado[sku] = quando
        for codigo, qtd, sp_em in movidos_rows:
            for peca, q in _demanda_no_sp([(codigo, qtd)]).items():
                visto = atualizado.get(peca)
                if visto is None or visto <= sp_em:
                    movidos[peca[: -(len(_lote_flex()) + 1)]] += q

    out: dict[str, SaldoSp] = {}
    for sku in alvos:
        estoques = por_sku.get(sku)
        estoque = min(estoques.values()) if estoques else None
        if estoques and len(estoques) > 1:
            logger.info("flex_sku_sp_duplicado", sku=sku, produtos=len(estoques))
        out[sku] = SaldoSp(
            sku_sp=sku,
            estoque=estoque,
            pendentes=_pendentes(sku, demanda),
            movidos=_pendentes(sku, movidos),
        )
    return out


@dataclass
class _Estado:
    """Foto do `flex_anuncio_estado` no começo da rodada."""

    plataforma: str
    desejado: str
    observado: str | None
    observado_em: datetime | None
    recusa: str | None
    aprovado: bool = False
    # Fila de leitura (ver `_escolher_leituras`).
    leitura_em: datetime | None = None
    proxima_leitura: datetime | None = None
    status_anuncio: str | None = None
    status_em: datetime | None = None


async def _fotos_dos_estados(
    session: AsyncSession, ids: Collection[UUID]
) -> dict[tuple[UUID, str], _Estado]:
    if not ids:
        return {}
    rows = await session.execute(
        select(FlexAnuncioEstado).where(FlexAnuncioEstado.integration_id.in_(list(ids)))
    )
    return {
        (e.integration_id, e.external_id): _Estado(
            plataforma=e.plataforma,
            desejado=e.desejado,
            observado=e.observado,
            observado_em=e.observado_em,
            recusa=e.recusa,
            aprovado=e.aprovado_em is not None,
            leitura_em=e.leitura_em,
            proxima_leitura=e.proxima_leitura,
            status_anuncio=e.status_anuncio,
            status_em=e.status_em,
        )
        for e in rows.scalars().all()
    }


def _com_estado(
    anuncios: Iterable[Anuncio], estados: Mapping[tuple[UUID, str], _Estado]
) -> list[Anuncio]:
    """Junta o estado gravado em cada anúncio — e põe na lista o anúncio que
    só existe no estado (achado pela descoberta da conta, ou o vínculo morreu
    e saiu da importação): o DaVinci não o conhece, a regra o desliga
    (negação por padrão). O status do anúncio é o mais novo entre o da
    importação e o do estado."""
    out: list[Anuncio] = []
    vistos: set[tuple[UUID, str]] = set()
    for a in anuncios:
        e = estados.get(a.chave)
        vistos.add(a.chave)
        if e is not None:
            a = replace(
                a,
                observado=e.observado,
                desejado_anterior=e.desejado,
                recusa=e.recusa,
                aprovado=e.aprovado,
            )
            if e.status_anuncio and _mais_novo(e.status_em, a.status_em):
                a = replace(a, status=e.status_anuncio, status_em=e.status_em)
        out.append(a)
    for chave, e in estados.items():
        if chave in vistos:
            continue
        out.append(
            Anuncio(
                integration_id=chave[0],
                external_id=chave[1],
                plataforma=e.plataforma,
                observado=e.observado,
                desejado_anterior=e.desejado,
                recusa=e.recusa,
                aprovado=e.aprovado,
                conhecido=False,
                status=e.status_anuncio,
                status_em=e.status_em,
            )
        )
    return out


def _skus_sp(anuncios: Iterable[Anuncio], cfg: ConfigFlex) -> set[str]:
    out: set[str] = set()
    for a in anuncios:
        for v in a.variacoes:
            r = analisar_sku(v.sku, kits=cfg.kits)
            if not isinstance(r, str):
                out.add(r[1])
    return out


# =============================================================================
# Clientes e travas
# =============================================================================


async def montar_cliente(integ: Integration) -> Any:
    """Cliente da conta com a renovação de token segura (trava por integração,
    token novo gravado na hora — o mesmo do atendimento). Os testes trocam
    esta função por clientes falsos."""
    from app.services.atendimento.clientes import cliente_da_integracao

    return await cliente_da_integracao(integ)


async def _cliente(integ: Integration, cache: dict[UUID, Any]) -> Any:
    if integ.id in cache:
        return cache[integ.id]
    try:
        cli = await montar_cliente(integ)
    except Exception as exc:  # noqa: BLE001 — credencial quebrada: sem cliente
        logger.warning("flex_cliente_falhou", integration_id=str(integ.id), erro=str(exc)[:200])
        cli = None
    cache[integ.id] = cli
    return cli


def _chave_trava(integration_id: UUID, external_id: str) -> int:
    """int4 estável por anúncio para o advisory lock (namespace SYNC)."""
    h = hashlib.blake2b(f"flex:{integration_id}:{external_id}".encode(), digest_size=4).digest()
    v = int.from_bytes(h, "big", signed=False)
    return v - 2**32 if v >= 2**31 else v


async def _travar_conta(session: AsyncSession, iid: UUID) -> None:
    """Trava (esperando) a linha da conta em `flex_conta` até o commit."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :k)"),
        {"ns": SYNC_NAMESPACE, "k": _chave_trava(iid, "conta")},
    )


async def _travar(session: AsyncSession, chave: int) -> bool:
    r = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:ns, :k)"), {"ns": SYNC_NAMESPACE, "k": chave}
    )
    return bool(r.scalar())


def _canais_shopee() -> frozenset[str]:
    return flex_envio.canais_shopee_flex()


def _fresco(observado_em: datetime | None, agora: datetime) -> bool:
    """O estado lido ainda vale para decidir uma escrita?"""
    if observado_em is None:
        return False
    intervalo = max(1, int(get_settings().flex_intervalo_min or 15))
    return observado_em >= agora - max(timedelta(minutes=30), timedelta(minutes=2 * intervalo))


def _espera(tentativas: int) -> timedelta:
    """Espera crescente depois de uma falha passageira: o intervalo da
    varredura, dobrando, até 6 h."""
    intervalo = max(1, int(get_settings().flex_intervalo_min or 15))
    espera = timedelta(minutes=intervalo) * (2 ** max(0, tentativas - 1))
    return min(espera, _ESPERA_MAXIMA)


# =============================================================================
# Conta: pode ter Flex? E a descoberta dos anúncios dela
# =============================================================================


@dataclass(frozen=True)
class ContaFlex:
    """O que se sabe da conta (`flex_conta`). `ativo`: True = pode ter Flex;
    False = não pode; None = ainda não deu para saber."""

    plataforma: str
    ativo: bool | None = None
    status: str | None = None
    detalhe: str | None = None
    lido_em: datetime | None = None
    erro: str | None = None
    # De onde o motoboy do Flex sai (ML): CEP(s) só com dígitos e cidade(s).
    origem_cep: str | None = None
    origem_cidade: str | None = None
    origem_lida_em: datetime | None = None


def conta_flex_da_linha(c: FlexConta) -> ContaFlex:
    """O que o motor guardou da conta (`flex_conta`), no formato da regra."""
    return ContaFlex(
        plataforma=c.plataforma,
        ativo=c.flex_ativo,
        status=c.status,
        detalhe=c.detalhe,
        lido_em=c.lido_em,
        erro=c.erro,
        origem_cep=c.origem_cep,
        origem_cidade=c.origem_cidade,
        origem_lida_em=c.origem_lida_em,
    )


MOTIVO_SEM_ORIGEM = "não deu para ler de onde sai o Flex da conta no ML"
# Quanto tempo a origem lida vale sem uma leitura nova que a confirme (a
# plataforma fora do ar, a resposta sem `origin`): passou disso, a conta para
# até ler de novo. Um 5xx isolado na releitura de hora em hora não para nada.
_VALIDADE_ORIGEM = timedelta(hours=6)
_PREFIXO_FORA_DA_ORIGEM = "saída do Flex da conta fora de"


def _cep_txt(cep: str) -> str:
    """CEP de 8 dígitos com hífen: 13400123 → 13400-123."""
    return f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else cep


def bloqueio_da_origem(conta: ContaFlex) -> str | None:
    """A saída do Flex da conta (ML) é na cidade do local de saída (aba Flex,
    `flex_local` — São Bernardo do Campo de fábrica)? None = é.

    Eduardo, 08/10/2026: "o motoboy vai sair de São Bernardo" — e o local
    pode mudar. Com a saída em outra cidade, o Flex ligado pela peça do lote
    do local venderia para quem mora perto de lá, longe da peça. Conta "in"
    sem a cidade da origem lida: negação por padrão (o motor pergunta de novo
    na rodada seguinte)."""
    local = flex_local.atual()
    cidades = flex_local.cidades_da_origem(conta.origem_cidade)
    if not cidades:
        return MOTIVO_SEM_ORIGEM
    if conta.origem_lida_em is not None and conta.origem_lida_em < _agora() - _VALIDADE_ORIGEM:
        return MOTIVO_SEM_ORIGEM
    if flex_local.origem_ok(conta.origem_cidade, local):
        return None
    ceps = [c.strip() for c in (conta.origem_cep or "").split(",") if c.strip()]
    cep = f" (CEP {', '.join(_cep_txt(c) for c in ceps)})" if ceps else ""
    return f"{_PREFIXO_FORA_DA_ORIGEM} {local.cidade}: {', '.join(cidades)}{cep}"


def bloqueio_da_conta(plataforma: str, conta: ContaFlex | None) -> str | None:
    """O porquê de a conta não ter leitura nem escrita por anúncio (vira o
    motivo de cada anúncio dela); None = a conta pode ter Flex.

    Sem resposta da plataforma (nunca conferida, ou só erro de rede) também
    bloqueia: negação por padrão — sem saber se a conta tem Flex, o motor não
    pede aprovação nem escreve. O erro fica em `flex_conta.erro` (a tela).

    ML com Flex ativo mas a SAÍDA do Flex fora da cidade do local de saída
    (ou não lida) também bloqueia — `bloqueio_da_origem`. A emergência não passa por aqui:
    ela desliga tudo das contas com Flex, de onde quer que saiam."""
    ml = plataforma == flex_envio.PLATAFORMA_ML
    if conta is None or conta.ativo is None:
        if ml:
            return "não deu para conferir a assinatura do Flex da conta no ML"
        return "não deu para conferir a Entrega Direta da loja na Shopee"
    if conta.ativo:
        return bloqueio_da_origem(conta) if ml else None
    if ml:
        return f"conta sem Flex ativo no ML (status {conta.status or '?'})"
    if conta.status == "sem_canal":
        return "a loja não tem o canal Entrega Direta na Shopee"
    return "Entrega Direta desligada na loja — ligue no Seller Center primeiro"


async def _perguntar_conta(cli: Any, plataforma: str) -> flex_api.AssinaturaFlex:
    """Uma chamada: a assinatura do Flex (ML) ou o canal da loja (Shopee)."""
    if cli is None:
        return flex_api.AssinaturaFlex(None, None, "sem acesso à conta (credencial)")
    nome = (
        "ler_assinatura_flex" if plataforma == flex_envio.PLATAFORMA_ML else "ler_canal_loja_flex"
    )
    metodo = getattr(cli, nome, None)
    if metodo is None:
        return flex_api.AssinaturaFlex(None, None, "o cliente não confere a conta")
    try:
        if plataforma == flex_envio.PLATAFORMA_ML:
            return await metodo()
        return await metodo(_canais_shopee())
    except Exception as exc:  # noqa: BLE001 — os clientes classificam; isto é defesa
        return flex_api.assinatura_erro(exc)


async def _conferir_contas(
    integracoes: Mapping[UUID, Integration],
    clientes: dict[UUID, Any],
    agora: datetime,
    resumo: dict,
    *,
    perguntar: bool,
    forcar: Collection[UUID] = (),
    relidas: set[UUID] | None = None,
) -> dict[UUID, ContaFlex]:
    """A conta pode ter Flex? Vale o que está em `flex_conta` por até
    `_VALIDADE_CONTA`; depois disso (ou nunca conferida) pergunta à
    plataforma — uma chamada por conta. `perguntar=False` (o passe barato do
    pedido Flex): só o banco, qualquer idade.

    Resposta "não sei" (rede, 5xx) não apaga a anterior: fica o erro e a
    próxima rodada pergunta de novo. A ORIGEM (de onde o motoboy sai) só vale
    `_VALIDADE_ORIGEM` sem uma leitura que a confirme (`origem_lida_em`).

    `forcar`: contas perguntadas agora mesmo, sem cache — antes de LIGAR um
    anúncio aprovado e no "Sincronizar agora" (quem acabou de trocar o
    endereço do Flex no painel não espera 1 h). `relidas` recebe as contas
    que RESPONDERAM nesta rodada (o LIGAR só sai nelas)."""
    if not integracoes:
        return {}
    async with session_scope() as s:
        linhas = {
            c.integration_id: c
            for c in (
                await s.execute(
                    select(FlexConta).where(FlexConta.integration_id.in_(list(integracoes)))
                )
            )
            .scalars()
            .all()
        }
        out: dict[UUID, ContaFlex] = {}
        for iid, integ in integracoes.items():
            plat = _plataforma_de(integ) or ""
            c = linhas.get(iid)
            out[iid] = replace(conta_flex_da_linha(c), plataforma=plat) if c else ContaFlex(plat)
    if perguntar:
        respostas: dict[UUID, flex_api.AssinaturaFlex] = {}
        for iid, integ in integracoes.items():
            atual = out[iid]
            # Conta do ML com Flex e SEM a origem (lida antes da 0383, ou a
            # resposta não trouxe): pergunta já — sem a origem ela fica
            # bloqueada (bloqueio_da_origem), não espera a validade de 1 h.
            sem_origem = (
                atual.plataforma == flex_envio.PLATAFORMA_ML
                and atual.ativo is True
                and (not atual.origem_cidade or atual.origem_lida_em is None)
            )
            if (
                atual.ativo is not None
                and atual.lido_em is not None
                and atual.lido_em >= agora - _VALIDADE_CONTA
                and not sem_origem
                and iid not in forcar
            ):
                continue
            cli = await _cliente(integ, clientes)
            respostas[iid] = await _perguntar_conta(cli, atual.plataforma)
            resumo["contas_conferidas"] = resumo.get("contas_conferidas", 0) + 1
        if respostas:
            async with session_scope() as s:
                for iid, r in sorted(respostas.items(), key=lambda kv: str(kv[0])):
                    # A rodada e a emergência conferem/descobrem a mesma conta:
                    # uma de cada vez (a 2ª enxerga a linha que a 1ª gravou).
                    await _travar_conta(s, iid)
                    c = await s.get(FlexConta, iid)
                    if c is None:
                        c = FlexConta(integration_id=iid, plataforma=out[iid].plataforma)
                        s.add(c)
                    origem_antes = (c.origem_cep, c.origem_cidade)
                    if r.ativo is None:
                        # Não deu para saber: vale a resposta anterior.
                        c.erro = (r.detalhe or "sem resposta")[:500]
                    else:
                        c.flex_ativo = r.ativo
                        c.status = r.status
                        c.detalhe = (r.detalhe or None) and r.detalhe[:500]
                        if r.origem_ausente:
                            # "in" sem `origin` em NENHUMA assinatura: o formato
                            # da resposta mudou — fica a origem que já se sabia.
                            logger.warning("flex_conta_sem_origin", integration_id=str(iid))
                        else:
                            c.origem_cep = (r.origem_cep or None) and r.origem_cep[:1000]
                            c.origem_cidade = (r.origem_cidade or None) and r.origem_cidade[:1000]
                            c.origem_lida_em = agora if r.origem_cidade else None
                        c.lido_em = agora
                        c.erro = None
                        # "in" sem `origin`: a assinatura respondeu, mas a
                        # origem não foi confirmada — não libera o LIGAR.
                        if relidas is not None and not r.origem_ausente:
                            relidas.add(iid)
                    c.atualizado_em = agora
                    out[iid] = replace(conta_flex_da_linha(c), plataforma=out[iid].plataforma)
                    if (c.origem_cep, c.origem_cidade) != origem_antes:
                        logger.info(
                            "flex_conta_origem_mudou",
                            integration_id=str(iid),
                            origem_cep=c.origem_cep,
                            origem_cidade=c.origem_cidade,
                            antes=origem_antes[0],
                        )
                    if r.ativo is not None and r.ativo != (
                        linhas[iid].flex_ativo if iid in linhas else None
                    ):
                        logger.info(
                            "flex_conta_mudou",
                            integration_id=str(iid),
                            ativo=r.ativo,
                            status=r.status,
                        )
    resumo["contas_sem_flex"] = sum(1 for c in out.values() if not c.ativo)
    resumo["contas_fora_da_origem"] = sum(
        1
        for c in out.values()
        if c.ativo and c.plataforma == flex_envio.PLATAFORMA_ML and bloqueio_da_origem(c)
    )
    return out


def _com_contas(
    anuncios: Iterable[Anuncio], contas: Mapping[UUID, ContaFlex], *, shopee_escreve: bool
) -> list[Anuncio]:
    """Põe em cada anúncio o bloqueio da conta e se o motor consegue ligar."""
    out: list[Anuncio] = []
    for a in anuncios:
        conta = contas.get(a.integration_id)
        out.append(
            replace(
                a,
                bloqueio=bloqueio_da_conta(a.plataforma, conta),
                pode_ligar=shopee_escreve or a.plataforma != flex_envio.PLATAFORMA_SHOPEE,
            )
        )
    return out


async def _chaves_conhecidas(session: AsyncSession, iid: UUID, plataforma: str) -> set[str]:
    """Anúncios da conta que o DaVinci já conhece: vínculo vivo, importação
    não encerrada ou estado do Flex (ids normalizados)."""
    conhecidos: set[str] = set()
    for (ext,) in (
        await session.execute(
            select(ProductLink.external_id).where(
                ProductLink.integration_id == iid, ProductLink.morto_desde.is_(None)
            )
        )
    ).all():
        conhecidos.add(_id_anuncio(plataforma, ext))
    for (ext,) in (
        await session.execute(
            select(Listing.external_id).where(
                Listing.integration_id == iid, Listing.status != ListingStatus.CLOSED
            )
        )
    ).all():
        conhecidos.add(_id_anuncio(plataforma, ext))
    for (ext,) in (
        await session.execute(
            select(FlexAnuncioEstado.external_id).where(FlexAnuncioEstado.integration_id == iid)
        )
    ).all():
        conhecidos.add(ext)
    conhecidos.discard("")
    return conhecidos


async def _descobrir(
    integracoes: Mapping[UUID, Integration],
    contas: Mapping[UUID, ContaFlex],
    clientes: dict[UUID, Any],
    agora: datetime,
    resumo: dict,
    *,
    modo: str,
    idade: timedelta = _DESCOBERTA_A_CADA,
    maximo_contas: int | None = _DESCOBERTA_CONTAS_POR_RODADA,
) -> dict[tuple[UUID, str], str]:
    """Descoberta (ML): TODOS os ids da conta, ativos e pausados
    (`ids_da_conta`, busca por scroll), uma vez por `idade` — e no primeiro
    uso. O anúncio que o DaVinci não conhece (criado depois da última
    importação, SKU que não casa, vínculo morto) entra em `flex_anuncio_estado`
    como "fora do DaVinci — sem vínculo", desejado DESLIGADO: nas contas "in"
    quase todo anúncio nasce com Flex, e sem isto ele nunca seria lido nem
    desligado (nem pela emergência).

    Só contas do ML que podem ter Flex. Devolve o status visto de cada
    anúncio da conta — o motor usa como o status mais novo."""
    candidatas = [
        iid
        for iid, integ in integracoes.items()
        if _plataforma_de(integ) == flex_envio.PLATAFORMA_ML
        and (contas.get(iid) or ContaFlex("")).ativo
    ]
    if not candidatas:
        return {}
    async with session_scope() as s:
        linhas = {
            c.integration_id: c
            for c in (
                await s.execute(select(FlexConta).where(FlexConta.integration_id.in_(candidatas)))
            )
            .scalars()
            .all()
        }
    minimo = datetime.min.replace(tzinfo=UTC)
    vencidas: list[tuple[datetime, UUID]] = []
    for iid in candidatas:
        c = linhas.get(iid)
        quando = c.descoberta_em if c else None
        if quando is None:
            vencidas.append((minimo, iid))
        elif c is not None and c.descoberta_ok is False:
            if quando <= agora - min(idade, _DESCOBERTA_FALHOU_ESPERA):
                vencidas.append((quando, iid))
        elif quando <= agora - idade:
            vencidas.append((quando, iid))
    vencidas.sort(key=lambda par: (par[0], str(par[1])))
    if maximo_contas is not None:
        vencidas = vencidas[:maximo_contas]

    vistos: dict[tuple[UUID, str], str] = {}
    for _quando, iid in vencidas:
        cli = await _cliente(integracoes[iid], clientes)
        listar = getattr(cli, "ids_da_conta", None) if cli is not None else None
        if listar is None:
            continue
        por_id: dict[str, str] = {}
        erros: list[str] = []
        for status in ("active", "paused"):
            try:
                res = await listar(status, max_paginas=_DESCOBERTA_MAX_PAGINAS)
            except Exception as exc:  # noqa: BLE001 — o cliente não levanta; defesa
                res = flex_api.ListagemConta(erro=str(exc)[:300])
            for ext in res.ids:
                por_id.setdefault(str(ext).strip(), status)
            if not res.completo:
                erros.append(f"{status}: {res.erro or 'incompleta'}")
        por_id.pop("", None)
        novos = 0
        async with session_scope() as s:
            # A rodada e a emergência podem descobrir a mesma conta ao mesmo
            # tempo: uma de cada vez por conta (a 2ª vê o que a 1ª gravou).
            await s.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :k)"),
                {"ns": SYNC_NAMESPACE, "k": _chave_trava(iid, "descoberta")},
            )
            conhecidos = await _chaves_conhecidas(s, iid, flex_envio.PLATAFORMA_ML)
            for ext, status in por_id.items():
                vistos[(iid, ext)] = status
                if ext in conhecidos:
                    continue
                novos += 1
                s.add(
                    FlexAnuncioEstado(
                        integration_id=iid,
                        external_id=ext,
                        plataforma=flex_envio.PLATAFORMA_ML,
                        desejado=DESLIGADO,
                        motivo=MOTIVO_FORA_DO_DAVINCI,
                        aguardando_aprovacao=False,
                        tentativas=0,
                        leitura_falhas=0,
                        status_anuncio=status,
                        status_em=agora,
                        atualizado_em=agora,
                    )
                )
                s.add(
                    _log(
                        acao="decidir",
                        modo=modo,
                        resultado="ok",
                        integration_id=iid,
                        external_id=ext,
                        plataforma=flex_envio.PLATAFORMA_ML,
                        estado_depois=DESLIGADO,
                        motivo=f"{MOTIVO_FORA_DO_DAVINCI} (achado na conta do ML)",
                    )
                )
            await _travar_conta(s, iid)
            c = await s.get(FlexConta, iid)
            if c is None:
                c = FlexConta(integration_id=iid, plataforma=flex_envio.PLATAFORMA_ML)
                s.add(c)
            c.descoberta_em = agora
            c.descoberta_ok = not erros
            c.descoberta_total = len(por_id)
            c.descoberta_novos = novos
            c.descoberta_erro = "; ".join(erros)[:500] or None
            c.atualizado_em = agora
        resumo["contas_descobertas"] = resumo.get("contas_descobertas", 0) + 1
        resumo["descobertos"] = resumo.get("descobertos", 0) + len(por_id)
        if novos:
            resumo["fora_do_davinci_novos"] = resumo.get("fora_do_davinci_novos", 0) + novos
            logger.info("flex_descoberta_novos", integration_id=str(iid), novos=novos)
    return vistos


def _com_status(
    anuncios: Iterable[Anuncio], vistos: Mapping[tuple[UUID, str], str], agora: datetime
) -> list[Anuncio]:
    """O status que a descoberta acabou de ver (o mais novo de todos)."""
    if not vistos:
        return list(anuncios)
    return [
        replace(a, status=vistos[a.chave], status_em=agora) if a.chave in vistos else a
        for a in anuncios
    ]


# =============================================================================
# Leitura do estado real
# =============================================================================


def _rodizio(lista: list[Anuncio]) -> list[Anuncio]:
    """Intercala as contas: o 1º de cada conta, depois o 2º de cada uma…
    (a ordem dentro da conta e a das contas — pela melhor posição — ficam)."""
    filas: dict[UUID, list[Anuncio]] = {}
    for a in lista:
        filas.setdefault(a.integration_id, []).append(a)
    out: list[Anuncio] = []
    posicao = 0
    while len(out) < len(lista):
        for fila in filas.values():
            if posicao < len(fila):
                out.append(fila[posicao])
        posicao += 1
    return out


def _escolher_leituras(
    anuncios: list[Anuncio],
    decisoes: Mapping[tuple[UUID, str], Decisao],
    estados: Mapping[tuple[UUID, str], _Estado],
    alvo: set[tuple[UUID, str]] | None,
    agora: datetime,
) -> list[Anuncio]:
    """A ordem de leitura desta rodada (o teto é aplicado em `_ler`).

    Fila justa (revisão de 02/10/2026): antes a ordem era (precisa,
    observado_em) e a leitura que falhava não gravava data nenhuma — o
    anúncio que nunca era lido (404, conta com 403, token quebrado) ficava em
    1º lugar em TODA rodada e tomava as 400 vagas; as outras contas nunca eram
    relidas e nada desligava. Agora:
      • conta que não pode ter Flex não é lida (`bloqueio`);
      • leitura que falhou espera `proxima_leitura` (crescente);
      • ordem: aprovados por uma pessoa (o LIGAR espera a leitura fresca),
        depois quem pode precisar de escrita, depois pela última TENTATIVA
        de leitura (deu certo ou não) — e as contas em rodízio."""
    if alvo is not None:
        return [a for a in anuncios if a.chave in alvo and not a.bloqueio]
    minimo = datetime.min.replace(tzinfo=UTC)

    def prioridade(a: Anuncio) -> tuple:
        e = estados.get(a.chave)
        obs = e.observado if e else None
        quando = (e.leitura_em or e.observado_em) if e else None
        quer = decisoes[a.chave].desejado == LIGADO
        precisa = (
            obs is None
            or quer != (obs == LIGADO)
            or quando is None
            or quando < agora - _RELER_NO_MAXIMO
        )
        return (
            0 if (e is not None and e.aprovado) else 1,
            0 if precisa else 1,
            quando or minimo,
            str(a.integration_id),
            a.external_id,
        )

    candidatos = []
    for a in anuncios:
        if a.bloqueio:
            continue
        e = estados.get(a.chave)
        if e is not None and e.proxima_leitura is not None and e.proxima_leitura > agora:
            continue
        candidatos.append(a)
    ml = _rodizio(
        sorted((a for a in candidatos if a.plataforma == flex_envio.PLATAFORMA_ML), key=prioridade)
    )
    sh = _rodizio(
        sorted(
            (a for a in candidatos if a.plataforma == flex_envio.PLATAFORMA_SHOPEE),
            key=prioridade,
        )
    )
    return ml + sh


async def _ler(
    escolhidos: list[Anuncio],
    integracoes: Mapping[UUID, Integration],
    clientes: dict[UUID, Any],
    resumo: dict,
    *,
    prazo: float | None = None,
) -> dict[tuple[UUID, str], flex_api.ResultadoFlex]:
    """Lê o Flex dos anúncios escolhidos, na ordem dada, até o teto de cada
    plataforma ou o `prazo` (time.monotonic) — o que vier primeiro; o resto
    fica para a próxima rodada (`leituras_adiadas`). Só LEITURA.

    A leitura também passa pela trava do anúncio: nenhuma chamada ao mesmo
    anúncio ao mesmo tempo (a emergência e a aprovação escrevem fora da
    rodada). Anúncio travado fica para a próxima rodada.

    Conta interrompida (429, token recusado, `_SEM_PERMISSAO_SEGUIDOS` 403
    seguidos, sem cliente) sai da fila desta rodada SEM gastar vaga: as
    vagas dela vão para as outras contas."""
    out: dict[tuple[UUID, str], flex_api.ResultadoFlex] = {}

    def _contar(r: flex_api.ResultadoFlex) -> None:
        resumo["lidos"] = resumo.get("lidos", 0) + 1
        if not r.ok:
            resumo["leituras_falhas"] = resumo.get("leituras_falhas", 0) + 1

    interrompidas: set[UUID] = set()

    def _interromper(iid: UUID) -> None:
        if iid not in interrompidas:
            interrompidas.add(iid)
            resumo["contas_interrompidas"] = resumo.get("contas_interrompidas", 0) + 1

    async def _cli(iid: UUID) -> Any:
        integ = integracoes.get(iid)
        if integ is None:
            interrompidas.add(iid)
            return None
        cli = await _cliente(integ, clientes)
        if cli is None:
            interrompidas.add(iid)
            resumo["contas_sem_cliente"] = resumo.get("contas_sem_cliente", 0) + 1
        return cli

    # ---- ML: um GET por anúncio, em rodízio, até o teto -------------------
    def _estourou() -> bool:
        return prazo is not None and time.monotonic() >= prazo

    lidas = 0
    seguidos: Counter[UUID] = Counter()
    repetir: Counter[UUID] = Counter()
    for a in (x for x in escolhidos if x.plataforma == flex_envio.PLATAFORMA_ML):
        if lidas >= _TETO_LEITURAS_ML or _estourou():
            resumo["leituras_adiadas"] = resumo.get("leituras_adiadas", 0) + 1
            continue
        iid = a.integration_id
        if iid in interrompidas:
            continue
        cli = await _cli(iid)
        if cli is None:
            continue
        async with session_scope() as s:
            if not await _travar(s, _chave_trava(iid, a.external_id)):
                resumo["leituras_ocupadas"] = resumo.get("leituras_ocupadas", 0) + 1
                continue
            r = await cli.ler_flex(a.external_id)
        lidas += 1
        out[a.chave] = r
        _contar(r)
        if r.tipo == flex_api.REPETIR and r.status_http == 429:
            # Limite estourado: as próximas leituras da conta só bateriam no
            # mesmo muro. Fica para a próxima rodada.
            _interromper(iid)
        elif r.tipo == flex_api.REPETIR:
            # 5xx / rede: um anúncio pode falhar sozinho; vários seguidos é o
            # endpoint ruim — a conta para nesta rodada.
            repetir[iid] += 1
            if repetir[iid] >= _REPETIR_SEGUIDOS:
                _interromper(iid)
        elif r.tipo == flex_api.SEM_PERMISSAO:
            # 401 / refresh recusado: é o token da conta. 403 sem "item
            # down" pode ser UM anúncio (de outro vendedor) — só interrompe
            # quando se repete.
            seguidos[iid] += 1
            if r.status_http in (None, 401) or seguidos[iid] >= _SEM_PERMISSAO_SEGUIDOS:
                _interromper(iid)
        if r.tipo != flex_api.REPETIR:
            repetir[iid] = 0
        if r.tipo != flex_api.SEM_PERMISSAO:
            seguidos[iid] = 0

    # ---- Shopee: 50 anúncios por chamada, cada um com a sua trava ---------
    shopee = [x for x in escolhidos if x.plataforma == flex_envio.PLATAFORMA_SHOPEE]
    if len(shopee) > _TETO_LEITURAS_SHOPEE:
        resumo["leituras_adiadas"] = (
            resumo.get("leituras_adiadas", 0) + len(shopee) - _TETO_LEITURAS_SHOPEE
        )
        shopee = shopee[:_TETO_LEITURAS_SHOPEE]
    por_conta: dict[UUID, list[Anuncio]] = defaultdict(list)
    for a in shopee:
        por_conta[a.integration_id].append(a)
    for iid, lista in por_conta.items():
        cli = await _cli(iid)
        if cli is None:
            continue
        for inicio in range(0, len(lista), 50):
            if _estourou():
                resumo["leituras_adiadas"] = (
                    resumo.get("leituras_adiadas", 0) + len(lista) - inicio
                )
                break
            lote = lista[inicio : inicio + 50]
            async with session_scope() as s:
                livres = [
                    a
                    for a in lote
                    if await _travar(s, _chave_trava(a.integration_id, a.external_id))
                ]
                ocupados = len(lote) - len(livres)
                if ocupados:
                    resumo["leituras_ocupadas"] = resumo.get("leituras_ocupadas", 0) + ocupados
                if not livres:
                    continue
                res = await cli.ler_canais_flex([a.external_id for a in livres], _canais_shopee())
            for a in livres:
                r = res.get(a.external_id)
                if r is not None:
                    out[a.chave] = r
                    _contar(r)
    return out


def _com_leituras(
    anuncios: list[Anuncio],
    leituras: Mapping[tuple[UUID, str], flex_api.ResultadoFlex],
    agora: datetime,
) -> list[Anuncio]:
    out: list[Anuncio] = []
    for a in anuncios:
        r = leituras.get(a.chave)
        if r is not None and r.status_anuncio:
            # O status que veio na mesma leitura (Shopee) é o mais novo.
            a = replace(a, status=r.status_anuncio, status_em=agora)
        if r is not None and r.ok and r.has_flex is not None:
            obs = LIGADO if r.has_flex else DESLIGADO
            # A plataforma mostra o Flex ligado: o anúncio pode ter Flex, a
            # recusa antiga não vale mais.
            a = replace(a, observado=obs, recusa=None if r.has_flex else a.recusa)
        out.append(a)
    return out


# =============================================================================
# Gravação do estado (+ trilha) e escolha das escritas
# =============================================================================


@dataclass(frozen=True)
class _Acao:
    integration_id: UUID
    external_id: str
    plataforma: str
    acao: str  # ligar | desligar
    mudou: bool  # o estado mudou nesta rodada (o simulado só vira log aí)
    antes: str | None
    # Os .sp do anúncio: o LIGAR confere o saldo deles no Bling antes.
    skus_sp: tuple[str, ...] = ()


def _log(
    *,
    acao: str,
    modo: str,
    resultado: str,
    integration_id: UUID | None = None,
    external_id: str | None = None,
    plataforma: str | None = None,
    estado_antes: str | None = None,
    estado_depois: str | None = None,
    saldo_sp: int | None = None,
    sku: str | None = None,
    motivo: str | None = None,
    erro: str | None = None,
    por: UUID | None = None,
) -> FlexLog:
    return FlexLog(
        integration_id=integration_id,
        external_id=external_id,
        plataforma=plataforma,
        acao=acao,
        modo=modo,
        resultado=resultado,
        estado_antes=estado_antes,
        estado_depois=estado_depois,
        saldo_sp=saldo_sp,
        sku=(sku or None) and sku[:500],
        motivo=(motivo or None) and motivo[:500],
        erro=(erro or None) and erro[:500],
        por=por,
    )


async def _gravar(
    anuncios: list[Anuncio],
    decisoes: Mapping[tuple[UUID, str], Decisao],
    leituras: Mapping[tuple[UUID, str], flex_api.ResultadoFlex],
    *,
    alvo: set[tuple[UUID, str]] | None,
    agora: datetime,
    modo: str,
    por: UUID | None,
    so_desligar: bool,
    resumo: dict,
) -> list[_Acao]:
    """Grava desejado/observado/aprovação de cada anúncio avaliado e devolve
    as escritas que a rodada pode fazer. Só a MUDANÇA vira linha no log
    (decidir, ler, pedir_aprovacao) — a rodada de 15 em 15 min não enche a
    trilha com a mesma decisão."""
    lista = [a for a in anuncios if alvo is None or a.chave in alvo]
    if not lista:
        return []
    acoes: list[_Acao] = []
    async with session_scope() as s:
        ids = {a.integration_id for a in lista}
        rows = await s.execute(
            select(FlexAnuncioEstado).where(FlexAnuncioEstado.integration_id.in_(list(ids)))
        )
        existentes = {(e.integration_id, e.external_id): e for e in rows.scalars().all()}
        for a in lista:
            d = decisoes[a.chave]
            est = existentes.get(a.chave)
            novo = est is None
            if est is None:
                est = FlexAnuncioEstado(
                    integration_id=a.integration_id,
                    external_id=a.external_id,
                    plataforma=a.plataforma,
                    desejado=d.desejado,
                    aguardando_aprovacao=False,
                    tentativas=0,
                    leitura_falhas=0,
                )
                s.add(est)
            desejado_antes = None if novo else est.desejado
            observado_antes = None if novo else est.observado
            mudou_algo = novo

            lido = leituras.get(a.chave)
            mudou_obs = False
            if lido is not None:
                # A tentativa conta para o rodízio da fila, dando certo ou não.
                est.leitura_em = agora
                if lido.ok and lido.has_flex is not None:
                    est.observado = LIGADO if lido.has_flex else DESLIGADO
                    est.observado_em = agora
                    mudou_obs = est.observado != observado_antes
                    if lido.has_flex:
                        est.recusa = None
                    if (est.ultimo_erro or "").startswith("leitura:"):
                        est.ultimo_erro = None
                    est.leitura_falhas = 0
                    est.proxima_leitura = None
                    mudou_algo = True
                else:
                    # Falhou: espera crescente antes de ler de novo — não volta
                    # ao topo da fila tomando a vaga dos outros.
                    est.ultimo_erro = f"leitura: {lido.texto()}"[:500]
                    est.leitura_falhas = int(est.leitura_falhas or 0) + 1
                    est.proxima_leitura = agora + _espera(est.leitura_falhas)
                    mudou_algo = True
            if (
                a.status
                and a.status_em is not None
                and (a.status, a.status_em) != (est.status_anuncio, est.status_em)
                and _mais_novo(a.status_em, est.status_em)
            ):
                est.status_anuncio = a.status if a.status in FLEX_STATUS_ANUNCIO else None
                est.status_em = a.status_em
                mudou_algo = True

            familias = ",".join(d.familias) or None
            if (est.desejado, est.motivo, est.saldo_sp, est.familias) != (
                d.desejado,
                d.motivo,
                d.saldo,
                familias,
            ):
                mudou_algo = True
            est.desejado, est.motivo, est.saldo_sp, est.familias = (
                d.desejado,
                d.motivo,
                d.saldo,
                familias,
            )
            if d.desejado != LIGADO and (est.aprovado_em is not None or est.aprovado_por):
                # A regra deixou de querer ligar: a aprovação era para AQUELA
                # ligação — para voltar a ligar, uma pessoa aprova de novo.
                est.aprovado_em = None
                est.aprovado_por = None
                mudou_algo = True
            if est.observado == LIGADO and (est.aprovado_em is not None or est.aprovado_por):
                # Lido JÁ ligado: a aprovação foi usada (ou nem era preciso —
                # alguém ligou no painel). Se ficasse, quando o vendedor
                # desligasse no painel a rodada seguinte RELIGARIA sozinha com
                # uma aprovação velha (cenário do cético: aprovar um anúncio não
                # lido → lido ligado → vendedor desliga → religava).
                est.aprovado_em = None
                est.aprovado_por = None
                mudou_algo = True
            # Pede aprovação só de quem foi LIDO desligado: sem leitura não se
            # sabe (nas contas "in" o Flex já está ligado na maioria) — pedir
            # ali era um aviso falso, e aprovar deixava a aprovação pendurada.
            precisa = (
                d.desejado == LIGADO and est.observado == DESLIGADO and est.aprovado_em is None
            )
            pediu_agora = precisa and not est.aguardando_aprovacao
            if est.aguardando_aprovacao != precisa:
                est.aguardando_aprovacao = precisa
                mudou_algo = True
            if mudou_algo:
                est.atualizado_em = agora

            mudou_decisao = novo or desejado_antes != d.desejado
            sku_txt = ",".join(d.skus_sp) or None
            if mudou_decisao:
                resumo["decisoes_mudaram"] = resumo.get("decisoes_mudaram", 0) + 1
                s.add(
                    _log(
                        acao="decidir",
                        modo=modo,
                        resultado="ok",
                        integration_id=a.integration_id,
                        external_id=a.external_id,
                        plataforma=a.plataforma,
                        estado_antes=desejado_antes,
                        estado_depois=d.desejado,
                        saldo_sp=d.saldo,
                        sku=sku_txt,
                        motivo=d.motivo,
                        por=por,
                    )
                )
            if mudou_obs:
                s.add(
                    _log(
                        acao="ler",
                        modo=modo,
                        resultado="ok",
                        integration_id=a.integration_id,
                        external_id=a.external_id,
                        plataforma=a.plataforma,
                        estado_antes=observado_antes,
                        estado_depois=est.observado,
                        motivo=(
                            "primeira leitura"
                            if observado_antes is None
                            else "mudou fora do DaVinci"
                        ),
                        por=por,
                    )
                )
            if pediu_agora:
                resumo["pedir_aprovacao"] = resumo.get("pedir_aprovacao", 0) + 1
                s.add(
                    _log(
                        acao="pedir_aprovacao",
                        modo=modo,
                        resultado="pendente",
                        integration_id=a.integration_id,
                        external_id=a.external_id,
                        plataforma=a.plataforma,
                        estado_antes=est.observado,
                        estado_depois=LIGADO,
                        saldo_sp=d.saldo,
                        sku=sku_txt,
                        motivo=d.motivo,
                        por=por,
                    )
                )

            # Escrita possível nesta rodada? Conta que não pode ter Flex: nada
            # por anúncio (nem desligar — não há Flex na conta para tirar).
            if a.bloqueio:
                continue
            if est.proxima_tentativa is not None and est.proxima_tentativa > agora:
                if (est.observado == LIGADO and d.desejado != LIGADO) or (
                    d.desejado == LIGADO and est.observado == DESLIGADO
                ):
                    resumo["esperando_nova_tentativa"] = (
                        resumo.get("esperando_nova_tentativa", 0) + 1
                    )
                continue
            fresco = _fresco(est.observado_em, agora)
            mudou = mudou_decisao or mudou_obs
            if d.desejado != LIGADO and est.observado == LIGADO and (fresco or so_desligar):
                acoes.append(
                    _Acao(a.integration_id, a.external_id, a.plataforma, "desligar", mudou, LIGADO)
                )
            elif (
                not so_desligar
                and d.desejado == LIGADO
                and est.observado == DESLIGADO
                and fresco
                and est.aprovado_em is not None
            ):
                acoes.append(
                    _Acao(
                        a.integration_id,
                        a.external_id,
                        a.plataforma,
                        "ligar",
                        mudou,
                        DESLIGADO,
                        d.skus_sp,
                    )
                )
    resumo["avaliados"] = resumo.get("avaliados", 0) + len(lista)
    for d in (decisoes[a.chave] for a in lista):
        resumo[f"quer_{d.desejado}"] = resumo.get(f"quer_{d.desejado}", 0) + 1
    return acoes


# =============================================================================
# Escrita
# =============================================================================


async def _chamar(
    cliente: Any, plataforma: str, external_id: str, *, ligar: bool
) -> flex_api.ResultadoFlex:
    """UMA mudança de Flex na plataforma (já dentro da trava do anúncio)."""
    if plataforma == flex_envio.PLATAFORMA_ML:
        if ligar:
            return await cliente.ligar_flex(external_id)
        return await cliente.desligar_flex(external_id)
    canais = _canais_shopee()
    # Shopee: lê a lista COMPLETA de canais agora (nunca lista parcial nem
    # velha), escreve, e confere lendo de novo.
    lidos = await cliente.ler_canais_flex([external_id], canais)
    antes = lidos.get(external_id)
    if antes is None:
        return flex_api.ResultadoFlex(
            flex_api.INDISPONIVEL, detalhe="a Shopee não devolveu o anúncio"
        )
    if not antes.ok:
        return antes
    if antes.has_flex is ligar:
        return flex_api.ResultadoFlex(flex_api.OK, has_flex=ligar, detalhe="já estava assim")
    res = await cliente.atualizar_canal_flex(
        external_id, list(antes.canais or ()), ligar=ligar, canais_flex=canais
    )
    if not res.ok:
        return res
    depois = (await cliente.ler_canais_flex([external_id], canais)).get(external_id)
    if depois is None or not depois.ok:
        return flex_api.ResultadoFlex(
            flex_api.ERRO, detalhe="a Shopee aceitou, mas a conferência não leu o anúncio"
        )
    if depois.has_flex is not ligar:
        return flex_api.ResultadoFlex(
            flex_api.ERRO, detalhe="a Shopee aceitou, mas o canal Flex não mudou"
        )
    return flex_api.ResultadoFlex(flex_api.OK, has_flex=ligar, status_http=res.status_http)


def _aplicar_resultado(
    est: FlexAnuncioEstado, res: flex_api.ResultadoFlex, acao: str, agora: datetime
) -> None:
    if res.ok:
        est.observado = LIGADO if res.has_flex else DESLIGADO
        est.observado_em = agora
        est.aplicado_em = agora
        est.tentativas = 0
        est.proxima_tentativa = None
        est.ultimo_erro = None
        if res.has_flex:
            est.recusa = None
            est.aguardando_aprovacao = False
        if acao == "ligar":
            # A aprovação foi usada: se a plataforma (ou alguém no painel)
            # desligar depois, LIGAR de novo pede outra aprovação.
            est.aprovado_em = None
            est.aprovado_por = None
        est.atualizado_em = agora
        return
    est.tentativas = int(est.tentativas or 0) + 1
    est.ultimo_erro = f"{acao}: {res.texto()}"[:500]
    # A escrita falhou: o estado gravado não vale até ser lido de novo (a
    # rodada seguinte lê antes de tentar outra vez).
    est.observado_em = None
    if res.tipo in flex_api.TIPOS_RECUSA:
        if acao == "ligar":
            est.recusa = res.texto()
            est.desejado = INELEGIVEL
            est.motivo = f"a plataforma recusou ligar: {res.texto()}"
            est.aguardando_aprovacao = False
            est.aprovado_em = None
            est.aprovado_por = None
            est.proxima_tentativa = None
        else:
            # Desligar recusado (pausado responde "item down"?): tenta de novo
            # em 1 h — com o anúncio reativado, ele não pode ficar o dia
            # inteiro vendendo pelo Flex sem .sp.
            est.proxima_tentativa = agora + _ESPERA_RECUSA_DESLIGAR
    else:
        est.proxima_tentativa = agora + _espera(est.tentativas)
    est.atualizado_em = agora


async def montar_bling(session: AsyncSession) -> Any:
    """Cliente do Bling — o mesmo do robô de prioridade. Os testes trocam esta
    função (nenhuma chamada real ao Bling)."""
    from app.services import nf_emissao_gerar

    return await nf_emissao_gerar._bling_client_opt(session)


def _virtual_do_saldo(linha: Mapping[str, Any]) -> int | None:
    """Saldo VIRTUAL de uma linha do /estoques/saldos (a mesma leitura do botão
    "Atualizar do Bling", routers/estoque.py): a soma dos depósitos, ou o
    total quando a linha não traz depósitos."""
    depositos = linha.get("depositos") or []
    if depositos:
        return sum(int(float(d.get("saldoVirtual") or 0)) for d in depositos)
    total = linha.get("saldoVirtualTotal")
    return None if total is None else int(float(total))


async def saldos_bling(skus_sp: Collection[str]) -> dict[str, int] | None:
    """Saldo VIRTUAL de cada .sp lido AGORA no Bling (`GET /estoques/saldos`,
    pelo id do produto no Bling). None = não deu para confirmar (Bling fora,
    SKU sem produto ativo com id do Bling, produto que o Bling não devolveu):
    quem chama trata como desconhecido — e desconhecido não liga."""
    alvos = sorted({x.strip().lower() for x in skus_sp if x and x.strip()})
    if not alvos:
        return None
    ids: dict[str, set[int]] = defaultdict(set)
    async with session_scope() as s:
        for sku, bid in (
            await s.execute(
                select(func.lower(Product.sku), Product.bling_product_id).where(
                    func.lower(Product.sku).in_(alvos),
                    Product.situacao == "A",
                    Product.bling_product_id.is_not(None),
                )
            )
        ).all():
            ids[sku].add(int(bid))
        if any(not ids.get(sku) for sku in alvos):
            return None
        try:
            cliente = await montar_bling(s)
        except Exception as exc:  # noqa: BLE001 — sem Bling: não confirma
            logger.warning("flex_bling_cliente_falhou", erro=str(exc)[:200])
            return None
    if cliente is None:
        return None
    todos = sorted({b for v in ids.values() for b in v})
    virtual: dict[int, int] = {}
    try:
        for inicio in range(0, len(todos), 50):
            r = await cliente._request(
                "GET",
                "/estoques/saldos",
                params=[("idsProdutos[]", str(b)) for b in todos[inicio : inicio + 50]],
            )
            r.raise_for_status()
            for linha in (r.json() or {}).get("data") or []:
                try:
                    pid = int((linha.get("produto") or {}).get("id") or 0)
                except (TypeError, ValueError):
                    continue
                v = _virtual_do_saldo(linha)
                if pid and v is not None:
                    virtual[pid] = v
    except Exception as exc:  # noqa: BLE001 — 429/timeout/5xx: não confirma
        logger.warning("flex_bling_saldos_falhou", skus=alvos, erro=str(exc)[:200])
        return None
    out: dict[str, int] = {}
    for sku in alvos:
        if any(b not in virtual for b in ids[sku]):
            return None
        out[sku] = min(virtual[b] for b in ids[sku])  # gêmeos: o menor, como no banco
    return out


async def _conferir_no_bling(session: AsyncSession, skus_sp: Collection[str]) -> str | None:
    """Antes de LIGAR: o saldo Flex de cada .sp com o saldo virtual lido AGORA
    no Bling, e não o `products.stock` (que só muda quando o webhook do Bling
    chega — e o da reserva às vezes não vem). Devolve None quando o Bling
    confirma; senão, o porquê de não ligar agora.

    O Bling já traz a reserva dos pedidos que o robô levou ao .sp; os pedidos
    Flex FORA do .sp continuam descontados (a reserva deles está no outro
    lote, mas a peça sai de São Bernardo)."""
    skus = sorted({x.strip().lower() for x in skus_sp if x and x.strip()})
    if not skus:
        return f"o anúncio não tem .{_lote_flex()} para conferir no Bling"
    bling = await saldos_bling(skus)
    if bling is None:
        return f"não deu para conferir o saldo do .{_lote_flex()} no Bling agora"
    banco = await calcular_saldos(session, skus)
    cfg = ConfigFlex.das_configuracoes()
    for sku in skus:
        pendentes = banco[sku].pendentes if sku in banco else 0
        livre = bling[sku] - pendentes
        if livre < cfg.n_liga:
            return (
                f"o Bling mostra só {max(livre, 0)} peça(s) livre(s) em {sku} — "
                f"precisa de {cfg.n_liga} para ligar"
            )
    return None


async def _travar_esperando(session: AsyncSession, chave: int, espera: float) -> bool:
    """A trava do anúncio, esperando até `espera` segundos que o outro
    processo (a rodada lendo ou escrevendo nele) termine."""
    fim = time.monotonic() + max(0.0, espera)
    while True:
        if await _travar(session, chave):
            return True
        if time.monotonic() >= fim:
            return False
        await asyncio.sleep(0.2)


async def _escrever(
    cliente: Any,
    *,
    integration_id: UUID,
    external_id: str,
    plataforma: str,
    acao: str,
    modo: str,
    por: UUID | None,
    skus_sp: Collection[str] = (),
    esperar_trava: float = 0.0,
) -> str:
    """Uma escrita, serializada por anúncio: trava → relê o estado → confere
    se ainda é preciso → chama → grava estado + trilha (commit solta a trava).

    LIGAR confere antes o saldo do .sp no Bling (`_conferir_no_bling`): sem
    confirmação, não liga — fica a falha com espera, e a aprovação continua.

    Devolve o tipo do resultado, "ocupado" (outro processo está no anúncio),
    "mudou" (o estado relido já não pede a escrita) ou, na emergência,
    "ja_desligado" (relido dentro da trava: nada a desligar)."""
    agora = _agora()
    async with session_scope() as s:
        chave = _chave_trava(integration_id, external_id)
        travou = (
            await _travar_esperando(s, chave, esperar_trava)
            if esperar_trava > 0
            else await _travar(s, chave)
        )
        if not travou:
            return "ocupado"
        est = await s.get(FlexAnuncioEstado, (integration_id, external_id))
        if acao == "emergencia":
            if est is None:
                est = FlexAnuncioEstado(
                    integration_id=integration_id,
                    external_id=external_id,
                    plataforma=plataforma,
                    desejado=DESLIGADO,
                    tentativas=0,
                    aguardando_aprovacao=False,
                )
                s.add(est)
            est.desejado = DESLIGADO
            est.motivo = "emergência: desligado por uma pessoa"
            est.aguardando_aprovacao = False
            est.aprovado_em = None
            est.aprovado_por = None
            if est.observado == DESLIGADO:
                # Relido DENTRO da trava: a plataforma está desligada (a
                # rodada que segurava a trava não chegou a ligar). Nada a
                # chamar — a aprovação já saiu, nada volta a ligar sozinho.
                est.atualizado_em = agora
                return "ja_desligado"
        elif est is None:
            return "mudou"
        elif acao == "ligar" and not (
            est.desejado == LIGADO and est.aprovado_em is not None and est.observado == DESLIGADO
        ):
            return "mudou"
        elif acao == "desligar" and not (est.desejado != LIGADO and est.observado == LIGADO):
            return "mudou"
        antes = est.observado
        if acao == "ligar":
            porque = await _conferir_no_bling(s, skus_sp)
            if porque is not None:
                res = flex_api.ResultadoFlex(flex_api.REPETIR, detalhe=porque)
                _aplicar_resultado(est, res, acao, agora)
                s.add(
                    _log(
                        acao=acao,
                        modo=modo,
                        resultado="erro",
                        integration_id=integration_id,
                        external_id=external_id,
                        plataforma=plataforma,
                        estado_antes=antes,
                        estado_depois=antes,
                        saldo_sp=est.saldo_sp,
                        sku=",".join(sorted(skus_sp)) or None,
                        motivo=est.motivo,
                        erro=porque,
                        por=por,
                    )
                )
                return res.tipo
        try:
            res = await _chamar(cliente, plataforma, external_id, ligar=(acao == "ligar"))
        except Exception as exc:  # noqa: BLE001 — os clientes classificam; isto é defesa
            # Um anúncio com resposta inesperada não derruba as outras
            # escritas da rodada: vira erro com espera, como qualquer outro.
            logger.warning(
                "flex_escrita_inesperada", external_id=external_id, erro=str(exc)[:200]
            )
            res = flex_api.ResultadoFlex(flex_api.ERRO, detalhe=f"inesperado: {str(exc)[:200]}")
        _aplicar_resultado(est, res, "desligar" if acao == "emergencia" else acao, agora)
        s.add(
            _log(
                acao=acao,
                modo=modo,
                resultado="ok" if res.ok else "erro",
                integration_id=integration_id,
                external_id=external_id,
                plataforma=plataforma,
                estado_antes=antes,
                estado_depois=est.observado if res.ok else antes,
                saldo_sp=est.saldo_sp,
                motivo=est.motivo,
                erro=None if res.ok else res.texto(),
                por=por,
            )
        )
        return res.tipo


async def _aplicar(
    acoes: list[_Acao],
    integracoes: Mapping[UUID, Integration],
    clientes: dict[UUID, Any],
    *,
    modo: str,
    por: UUID | None,
    resumo: dict,
) -> None:
    """Faz (ou simula) as escritas da rodada. Desligar antes de ligar (é o
    lado seguro quando o teto corta a rodada) — mas com uma parte do teto
    GUARDADA para os LIGAR aprovados (1/5, no mínimo 1): no primeiro dia a
    fila de desligar passa de mil anúncios, e o anúncio que uma pessoa
    aprovou esperava horas por ela. Vaga guardada que sobra volta para os
    desligar na rodada seguinte."""
    if not acoes:
        return
    acoes = sorted(acoes, key=lambda a: (0 if a.acao == "desligar" else 1, str(a.integration_id)))
    if not flex_config.pode_escrever(modo):
        # observar: NENHUMA chamada de escrita. Fica a trilha do que faria —
        # uma vez, quando o estado muda (não a cada rodada).
        resumo["simulados"] = resumo.get("simulados", 0) + len(acoes)
        novas = [a for a in acoes if a.mudou]
        if novas:
            async with session_scope() as s:
                for a in novas:
                    s.add(
                        _log(
                            acao=a.acao,
                            modo=modo,
                            resultado="simulado",
                            integration_id=a.integration_id,
                            external_id=a.external_id,
                            plataforma=a.plataforma,
                            estado_antes=a.antes,
                            estado_depois=LIGADO if a.acao == "ligar" else DESLIGADO,
                            motivo=f"modo {modo}: não escreve na plataforma",
                            por=por,
                        )
                    )
        return

    shopee_escreve = bool(get_settings().flex_shopee_escrita)
    teto = max(0, int(get_settings().flex_teto_escritas_por_rodada or 0))
    escritas = 0
    ignoradas: list[_Acao] = []

    def _so_leitura(a: _Acao) -> bool:
        return a.plataforma == flex_envio.PLATAFORMA_SHOPEE and not shopee_escreve

    ligar = [a for a in acoes if a.acao == "ligar" and not _so_leitura(a)]
    reserva = min(len(ligar), max(1, teto // 5)) if teto else 0

    async def _fazer(lista: list[_Acao], limite: int) -> None:
        nonlocal escritas
        for a in lista:
            if _so_leitura(a):
                resumo["shopee_so_leitura"] = resumo.get("shopee_so_leitura", 0) + 1
                if a.mudou:
                    ignoradas.append(a)
                continue
            if escritas >= limite:
                resumo["adiados_teto"] = resumo.get("adiados_teto", 0) + 1
                continue
            integ = integracoes.get(a.integration_id)
            cli = await _cliente(integ, clientes) if integ is not None else None
            if cli is None:
                resumo["sem_cliente"] = resumo.get("sem_cliente", 0) + 1
                continue
            tipo = await _escrever(
                cli,
                integration_id=a.integration_id,
                external_id=a.external_id,
                plataforma=a.plataforma,
                acao=a.acao,
                modo=modo,
                por=por,
                skus_sp=a.skus_sp,
            )
            if tipo in ("ocupado", "mudou"):
                resumo[tipo] = resumo.get(tipo, 0) + 1
                continue
            escritas += 1
            chave = f"{a.acao}_{'ok' if tipo == flex_api.OK else 'falhou'}"
            resumo[chave] = resumo.get(chave, 0) + 1

    await _fazer([a for a in acoes if a.acao == "desligar"], teto - reserva)
    await _fazer([a for a in acoes if a.acao == "ligar"], teto)
    resumo["escritas"] = resumo.get("escritas", 0) + escritas
    if ignoradas:
        async with session_scope() as s:
            for a in ignoradas:
                s.add(
                    _log(
                        acao=a.acao,
                        modo=modo,
                        resultado="ignorado",
                        integration_id=a.integration_id,
                        external_id=a.external_id,
                        plataforma=a.plataforma,
                        estado_antes=a.antes,
                        motivo="escrita da Shopee desligada (flex_shopee_escrita)",
                        por=por,
                    )
                )


# =============================================================================
# Entradas
# =============================================================================


class FlexRegraError(Exception):
    """Pedido que a regra não deixa fazer (a tela mostra o `codigo`)."""

    def __init__(self, codigo: str, detalhe: str = "") -> None:
        super().__init__(detalhe or codigo)
        self.codigo = codigo
        self.detalhe = detalhe


async def rodar_motor(
    *,
    modo: str | None = None,
    ler: bool = True,
    so_desligar: bool = False,
    somente: Collection[tuple[UUID, str]] | None = None,
    por: UUID | None = None,
    origem: str = "cron",
    reler_contas: bool = False,
) -> dict:
    """Uma rodada do motor. Respeita o modo; uma rodada de cada vez.

    `reler_contas`: pergunta a assinatura (e a origem) de todas as contas sem
    o cache de 1 h — o "Sincronizar agora" (quem trocou o endereço do Flex
    no painel do ML vê na hora).

    `ler=False, so_desligar=True`: o passe barato do gancho do pedido Flex —
    só banco + os DESLIGAR que o novo saldo pede (pelo último estado lido).
    `somente`: grava/lê/escreve só esses anúncios (a aprovação) — a regra e o
    limite por família continuam olhando todos."""
    m = flex_config.modo(modo)
    resumo: dict[str, Any] = {"modo": m, "origem": origem, "rodou": False}
    if m == flex_config.MODO_DESLIGADO:
        return resumo
    contas = flex_config.contas()
    if not contas:
        resumo["motivo"] = "nenhuma conta em flex_contas"
        return resumo
    cfg = ConfigFlex.das_configuracoes()
    # O local de saída (cidade + lote), relido do banco e FIXADO: vale a rodada
    # inteira, mesmo que alguém troque no meio dela.
    local = await flex_local.carregar(forcar=True)
    resumo["local"] = f"{local.cidade} (.{local.lote})"
    async with session_scope() as trava:
        if not await _travar(trava, _MOTOR_LOCK_KEY):
            resumo["ocupado"] = True
            return resumo
        resumo["rodou"] = True
        with flex_local.fixar(local):
            await _rodada(
                cfg,
                contas,
                modo=m,
                ler=ler,
                so_desligar=so_desligar,
                alvo=set(somente) if somente is not None else None,
                por=por,
                resumo=resumo,
                reler_contas=reler_contas,
            )
    logger.info("flex_motor_rodada", **{k: v for k, v in resumo.items() if v not in (0, None)})
    return resumo


async def _contas_com_aprovacao(contas: Collection[UUID]) -> set[UUID]:
    """Contas com algum anúncio aprovado para LIGAR (a aprovação ainda não
    usada) — a rodada relê a assinatura delas antes de ligar."""
    if not contas:
        return set()
    async with session_scope() as s:
        rows = await s.execute(
            select(FlexAnuncioEstado.integration_id)
            .where(
                FlexAnuncioEstado.integration_id.in_(list(contas)),
                FlexAnuncioEstado.aprovado_em.is_not(None),
            )
            .distinct()
        )
        return {r[0] for r in rows.all()}


async def _rodada(
    cfg: ConfigFlex,
    contas: Collection[UUID],
    *,
    modo: str,
    ler: bool,
    so_desligar: bool,
    alvo: set[tuple[UUID, str]] | None,
    por: UUID | None,
    resumo: dict,
    reler_contas: bool = False,
) -> None:
    agora = _agora()
    # Conta desde o começo: a conferência das contas e a descoberta também
    # gastam o tempo do job.
    prazo = time.monotonic() + _PRAZO_LEITURAS_S
    async with session_scope() as s:
        integracoes = await integracoes_permitidas(s, contas)
    if not integracoes:
        resumo["motivo"] = "nenhuma conta permitida ativa (ML/Shopee)"
        return
    clientes: dict[UUID, Any] = {}
    # A conta pode ter Flex? (cache de 1 h; o passe barato só lê o banco.)
    # Antes de LIGAR (anúncio aprovado — o desta aprovação ou um que ficou
    # esperando), a assinatura e a origem são lidas de novo, sem o cache de
    # 1 h: o endereço do Flex pode ter saído da cidade do local há pouco.
    forcar: set[UUID] = set()
    if ler:
        if reler_contas:
            forcar = set(integracoes)
        else:
            if alvo:
                forcar |= {iid for iid, _ext in alvo}
            forcar |= await _contas_com_aprovacao(integracoes.keys())
    relidas: set[UUID] = set()
    contas_flex = await _conferir_contas(
        integracoes, clientes, agora, resumo, perguntar=ler, forcar=forcar, relidas=relidas
    )
    vistos: dict[tuple[UUID, str], str] = {}
    if ler and alvo is None:
        vistos = await _descobrir(integracoes, contas_flex, clientes, agora, resumo, modo=modo)
    async with session_scope() as s:
        estados = await _fotos_dos_estados(s, integracoes.keys())
        anuncios = _com_estado(await montar_anuncios(s, integracoes), estados)
        anuncios = [a for a in anuncios if a.integration_id in integracoes]
        anuncios = _com_status(anuncios, vistos, agora)
        anuncios = _com_contas(
            anuncios, contas_flex, shopee_escreve=bool(get_settings().flex_shopee_escrita)
        )
        saldos = await calcular_saldos(s, _skus_sp(anuncios, cfg))
    resumo["contas"] = len(integracoes)
    resumo["anuncios"] = len(anuncios)

    decisoes = decidir_lote(anuncios, saldos, cfg)
    leituras: dict[tuple[UUID, str], flex_api.ResultadoFlex] = {}
    if ler:
        escolhidos = _escolher_leituras(anuncios, decisoes, estados, alvo, agora)
        leituras = await _ler(escolhidos, integracoes, clientes, resumo, prazo=prazo)
        if leituras:
            anuncios = _com_leituras(anuncios, leituras, agora)
            decisoes = decidir_lote(anuncios, saldos, cfg)

    acoes = await _gravar(
        anuncios,
        decisoes,
        leituras,
        alvo=alvo,
        agora=agora,
        modo=modo,
        por=por,
        so_desligar=so_desligar,
        resumo=resumo,
    )
    # LIGAR só na conta cuja assinatura (e origem) foi relida AGORA: a
    # aprovação que chegou no meio da rodada, ou a releitura que falhou,
    # espera a próxima — a aprovação continua valendo (revisão de 08/10/2026).
    sem_releitura = [a for a in acoes if a.acao == "ligar" and a.integration_id not in relidas]
    if sem_releitura:
        resumo["ligar_esperando_releitura"] = len(sem_releitura)
        acoes = [a for a in acoes if not (a.acao == "ligar" and a.integration_id not in relidas)]
    await _aplicar(acoes, integracoes, clientes, modo=modo, por=por, resumo=resumo)


async def aprovar(integration_id: UUID, external_id: str, *, por: UUID | None) -> dict:
    """Uma pessoa aprova LIGAR o Flex no anúncio. Em piloto/ativo o motor roda
    na hora só para ele (relê o estado, recalcula com o saldo de agora e
    liga se ainda for o caso); em observar fica só a aprovação.

    Também é o "tente de novo" depois de uma recusa da plataforma — e depois
    de uma aprovação que ainda não ligou (Bling sem confirmar, rodada
    ocupada). Com a rodada ocupada, `ocupado=True`: quem chama (a tela)
    põe na fila um job que tenta de novo em seguida (worker
    `flex_aprovado_run`) — a aprovação continua valendo."""
    m = flex_config.modo()
    if m == flex_config.MODO_DESLIGADO:
        raise FlexRegraError("flex_desligado", "o Flex está com flex_modo=desligado")
    await flex_local.carregar()
    if integration_id not in flex_config.contas():
        raise FlexRegraError("conta_nao_permitida", "a conta não está em flex_contas")
    agora = _agora()
    async with session_scope() as s:
        est = await s.get(FlexAnuncioEstado, (integration_id, external_id))
        if est is None:
            raise FlexRegraError("nao_avaliado", "o motor ainda não avaliou este anúncio")
        shopee = est.plataforma == flex_envio.PLATAFORMA_SHOPEE
        if shopee and not get_settings().flex_shopee_escrita:
            # Aprovar não ligaria nada (e a aprovação ficaria pendurada até
            # alguém ligar a escrita da Shopee — aí dispararia sozinha).
            raise FlexRegraError(
                "shopee_so_leitura",
                "na Shopee o DaVinci só confere: ligue a Entrega Direta à mão no Seller Center",
            )
        conta = await s.get(FlexConta, integration_id)
        if conta is not None and conta.flex_ativo is False:
            raise FlexRegraError(
                "conta_sem_flex",
                bloqueio_da_conta(est.plataforma, conta_flex_da_linha(conta))
                or "a conta não pode ter Flex",
            )
        if conta is not None and conta.flex_ativo is True:
            # Saída do Flex fora da cidade do local: aprovar não pode ficar
            # pendurado até alguém trocar o endereço (aí ligaria sozinho).
            fora = bloqueio_da_conta(est.plataforma, conta_flex_da_linha(conta))
            if fora:
                raise FlexRegraError("conta_fora_da_origem", fora)
        if est.desejado != LIGADO and not est.recusa:
            raise FlexRegraError("nao_elegivel", est.motivo or "a regra não quer o Flex ligado")
        recusa_antes = est.recusa
        est.aprovado_por = por
        est.aprovado_em = agora
        est.aguardando_aprovacao = False
        est.recusa = None
        est.atualizado_em = agora
        s.add(
            _log(
                acao="aprovar",
                modo=m,
                resultado="ok",
                integration_id=integration_id,
                external_id=external_id,
                plataforma=est.plataforma,
                estado_antes=est.observado,
                estado_depois=LIGADO,
                saldo_sp=est.saldo_sp,
                motivo=(
                    f"tentar de novo depois da recusa: {recusa_antes}"
                    if recusa_antes
                    else est.motivo
                ),
                por=por,
            )
        )
    out: dict[str, Any] = {"aprovado": True, "modo": m, "aplicado": False, "ocupado": False}
    if flex_config.pode_escrever(m):
        out.update(await aplicar_aprovado(integration_id, external_id, por=por, modo=m))
    return out


async def aplicar_aprovado(
    integration_id: UUID, external_id: str, *, por: UUID | None, modo: str | None = None
) -> dict:
    """Roda o motor só para o anúncio aprovado (a aprovação e o job que tenta
    de novo quando a rodada estava ocupada)."""
    resumo = await rodar_motor(
        modo=modo, somente={(integration_id, external_id)}, por=por, origem="aprovacao"
    )
    return {
        "motor": resumo,
        "aplicado": bool(resumo.get("ligar_ok")),
        "ocupado": bool(resumo.get("ocupado")),
    }


# =============================================================================
# Emergência (job do worker)
# =============================================================================


def _resumo_emergencia(modo: str, escreve: bool) -> dict[str, Any]:
    return {
        "modo": modo,
        "escreve": escreve,
        "alvos": 0,
        "ligados_conhecidos": 0,
        "processados": 0,
        "desligados": 0,
        "ja_desligados": 0,
        "falhas": 0,
        "ocupados": 0,
        "simulados": 0,
        "restantes": 0,
        "aprovacoes": 0,
        "contas_sem_flex": 0,
    }


async def preparar_emergencia(
    *, por: UUID | None, contas_escopo: Collection[UUID] | None = None
) -> dict:
    """O que o botão faz DENTRO do pedido HTTP (rápido, só banco): tira toda
    aprovação das contas (em piloto/ativo — nada volta a ligar sem uma pessoa
    aprovar de novo) e cria a linha da `flex_emergencia` que o job
    (`executar_emergencia`, worker `flex_emergencia_run`) vai cumprir.

    Em observar/desligado não tira nada (a tela promete uma simulação):
    `aprovacoes` diz quantas sairiam. Sem conta: devolve `motivo` e nenhuma
    linha (`id` None).

    `contas_escopo`: só estas contas (usuário com equipe — routers/flex)."""
    m = flex_config.modo()
    escreve = flex_config.pode_escrever(m)
    resumo = _resumo_emergencia(m, escreve)
    contas = flex_config.contas()
    if contas_escopo is not None:
        contas = frozenset(contas) & frozenset(contas_escopo)
    if not contas:
        resumo["motivo"] = "nenhuma conta em flex_contas"
        return {"id": None, **resumo}
    agora = _agora()
    async with session_scope() as s:
        integracoes = await integracoes_permitidas(s, contas)
        if not integracoes:
            resumo["motivo"] = "nenhuma conta permitida ativa (ML/Shopee)"
            return {"id": None, **resumo}
        com_aprovacao = (
            FlexAnuncioEstado.integration_id.in_(list(integracoes)),
            FlexAnuncioEstado.aprovado_em.is_not(None),
        )
        if escreve:
            r = await s.execute(
                update(FlexAnuncioEstado)
                .where(*com_aprovacao)
                .values(aprovado_em=None, aprovado_por=None, atualizado_em=agora)
                .returning(FlexAnuncioEstado.integration_id, FlexAnuncioEstado.external_id)
            )
        else:
            r = await s.execute(
                select(FlexAnuncioEstado.integration_id, FlexAnuncioEstado.external_id).where(
                    *com_aprovacao
                )
            )
        aprovados = sorted({(str(iid), ext) for iid, ext in r.all()})
        resumo["aprovacoes"] = len(aprovados)
        linha = FlexEmergencia(
            por=por,
            escopo=None if contas_escopo is None else sorted(str(c) for c in contas),
            modo=m,
            status="na_fila",
            aprovados=[list(par) for par in aprovados],
            resumo=resumo,
            atualizado_em=agora,
        )
        s.add(linha)
        await s.flush()
        emergencia_id = int(linha.id)
    logger.warning(
        "flex_emergencia_pedida_job", id=emergencia_id, por=str(por) if por else None,
        aprovacoes=resumo["aprovacoes"], modo=m,
    )
    return {"id": emergencia_id, **resumo}


async def _gravar_andamento(
    emergencia_id: int, resumo: dict, *, status: str | None = None, erro: str | None = None
) -> None:
    agora = _agora()
    async with session_scope() as s:
        linha = await s.get(FlexEmergencia, emergencia_id)
        if linha is None:
            return
        linha.resumo = dict(resumo)
        linha.atualizado_em = agora
        if status is not None:
            linha.status = status
            if status == "rodando" and linha.iniciado_em is None:
                linha.iniciado_em = agora
            if status in ("concluida", "falhou"):
                linha.terminado_em = agora
        if erro is not None:
            linha.erro = erro[:500]


async def executar_emergencia(emergencia_id: int) -> dict:
    """O job da emergência: desliga o Flex de tudo nas contas da linha.

    Alvos: o que está ligado — ou que nunca foi lido — e o que tinha
    aprovação quando o botão foi apertado (a rodada pode estar no meio de
    ligá-lo), até `_TETO_EMERGENCIA` (`restantes` diz quanto falta: os que o
    teto cortou e os que outro processo segurava; apertar de novo continua).
    Primeiro os ligados conhecidos, os ativos antes dos pausados. Conta que
    a plataforma diz que não tem Flex (assinatura out/pending/404/403, canal
    desligado na loja) fica de fora — não há Flex lá para tirar, e eram
    centenas de DELETE inúteis. Conta que não deu para conferir entra (na
    dúvida, a emergência tenta). A descoberta da conta roda antes se tiver
    mais de 1 h: o anúncio que o DaVinci não conhece também desliga.

    Shopee sem `flex_shopee_escrita`: não escreve (fica em
    `shopee_so_leitura`). Em observar/desligado só SIMULA, sem efeito nenhum.

    Corrida com a rodada (revisão de 02/10/2026): (1) as aprovações saíram
    ANTES, no pedido HTTP — o `_escrever(ligar)` relê o estado dentro da trava
    do anúncio e não liga mais; (2) o anúncio que tinha aprovação também é
    alvo, mesmo lido desligado; (3) a emergência ESPERA a trava do anúncio
    (até `_ESPERA_TRAVA_EMERGENCIA` s) e relê o estado dentro dela — o que a
    rodada acabou de ligar é desligado em seguida.

    Andamento: `flex_emergencia.resumo`, regravado a cada
    `_EMERGENCIA_PROGRESSO_S` s (a tela consulta)."""
    local = await flex_local.carregar(forcar=True)
    async with session_scope() as s:
        linha = await s.get(FlexEmergencia, emergencia_id)
        if linha is None:
            return {"motivo": "emergência não encontrada"}
        if linha.status in ("concluida", "falhou"):
            return dict(linha.resumo or {})
        m = linha.modo
        por = linha.por
        escopo = None if linha.escopo is None else {UUID(str(x)) for x in linha.escopo}
        aprovados = {(UUID(str(iid)), str(ext)) for iid, ext in (linha.aprovados or [])}
        resumo: dict[str, Any] = {**_resumo_emergencia(m, False), **(linha.resumo or {})}
    # Escreve só se o modo do pedido E o de agora deixam (o modo pode ter
    # mudado entre o clique e o job — vale o lado seguro).
    escreve = flex_config.pode_escrever(m) and flex_config.pode_escrever()
    resumo["escreve"] = escreve
    try:
        await _gravar_andamento(emergencia_id, resumo, status="rodando")
        with flex_local.fixar(local):
            await _executar_emergencia(
                emergencia_id, resumo, modo=m, escreve=escreve, por=por, escopo=escopo,
                aprovados=aprovados,
            )
    except Exception as exc:  # noqa: BLE001 — a tela tem de ver que parou
        logger.exception("flex_emergencia_falhou", id=emergencia_id)
        await _gravar_andamento(emergencia_id, resumo, status="falhou", erro=str(exc))
        raise
    await _gravar_andamento(emergencia_id, resumo, status="concluida")
    logger.warning("flex_emergencia", id=emergencia_id, por=str(por) if por else None, **resumo)
    return resumo


async def _executar_emergencia(
    emergencia_id: int,
    resumo: dict,
    *,
    modo: str,
    escreve: bool,
    por: UUID | None,
    escopo: set[UUID] | None,
    aprovados: set[tuple[UUID, str]],
) -> None:
    m = modo
    contas = flex_config.contas()
    if escopo is not None:
        contas = frozenset(contas) & frozenset(escopo)
    agora = _agora()
    async with session_scope() as s:
        integracoes = await integracoes_permitidas(s, contas)
    if not integracoes:
        resumo["motivo"] = "nenhuma conta permitida ativa (ML/Shopee)"
        return
    clientes: dict[UUID, Any] = {}
    contas_flex = await _conferir_contas(integracoes, clientes, agora, {}, perguntar=True)
    sem_flex = {iid for iid, c in contas_flex.items() if c.ativo is False}
    resumo["contas_sem_flex"] = len(sem_flex)
    vistos = await _descobrir(
        integracoes,
        contas_flex,
        clientes,
        agora,
        {},
        modo=m,
        idade=_DESCOBERTA_EMERGENCIA,
        maximo_contas=None,
    )
    async with session_scope() as s:
        estados = await _fotos_dos_estados(s, integracoes.keys())
        anuncios = _com_status(
            _com_estado(await montar_anuncios(s, integracoes), estados), vistos, agora
        )
    alvos = [
        a
        for a in anuncios
        if a.integration_id in integracoes
        and a.integration_id not in sem_flex
        and (a.observado in (LIGADO, None) or (escreve and a.chave in aprovados))
    ]
    alvos.sort(
        key=lambda a: (
            0 if a.observado == LIGADO else 1,
            0 if ativo_na_plataforma(a.status) else 1,
            str(a.integration_id),
            a.external_id,
        )
    )
    resumo["alvos"] = len(alvos)
    resumo["ligados_conhecidos"] = sum(1 for a in alvos if a.observado == LIGADO)
    resumo["restantes"] = len(alvos)
    lote = alvos[:_TETO_EMERGENCIA]
    # A conferência das contas e a descoberta podem levar um tempo: a tela já
    # vê quantos alvos há (e que o job está vivo) antes da primeira escrita.
    await _gravar_andamento(emergencia_id, resumo)

    shopee_escreve = bool(get_settings().flex_shopee_escrita)
    logs: list[FlexLog] = []
    trava_andamento = asyncio.Lock()
    ultimo = [time.monotonic()]

    async def _andamento(forcar: bool = False) -> None:
        if not forcar and time.monotonic() - ultimo[0] < _EMERGENCIA_PROGRESSO_S:
            return
        async with trava_andamento:
            ultimo[0] = time.monotonic()
            resumo["restantes"] = (len(alvos) - resumo["processados"]) + resumo["ocupados"]
            await _gravar_andamento(emergencia_id, resumo)

    # Os clientes antes (um por conta, sem corrida entre as tarefas).
    if escreve:
        for iid in sorted({a.integration_id for a in lote}, key=str):
            await _cliente(integracoes[iid], clientes)

    paralelo = asyncio.Semaphore(_EMERGENCIA_PARALELO)

    async def _um(a: Anuncio) -> None:
        async with paralelo:
            resumo["processados"] += 1
            if not escreve:
                resumo["simulados"] += 1
                if a.observado == LIGADO:
                    logs.append(
                        _log(
                            acao="emergencia",
                            modo=m,
                            resultado="simulado",
                            integration_id=a.integration_id,
                            external_id=a.external_id,
                            plataforma=a.plataforma,
                            estado_antes=LIGADO,
                            estado_depois=DESLIGADO,
                            motivo=f"modo {m}: não escreve na plataforma",
                            por=por,
                        )
                    )
                return
            if a.plataforma == flex_envio.PLATAFORMA_SHOPEE and not shopee_escreve:
                resumo["shopee_so_leitura"] = resumo.get("shopee_so_leitura", 0) + 1
                return
            cli = clientes.get(a.integration_id)
            if cli is None:
                resumo["sem_cliente"] = resumo.get("sem_cliente", 0) + 1
                return
            tipo = await _escrever(
                cli,
                integration_id=a.integration_id,
                external_id=a.external_id,
                plataforma=a.plataforma,
                acao="emergencia",
                modo=m,
                por=por,
                esperar_trava=_ESPERA_TRAVA_EMERGENCIA,
            )
            if tipo == "ocupado":
                # Outro processo segurou o anúncio além da espera: NÃO foi
                # desligado — conta em `restantes` (apertar de novo continua).
                resumo["ocupados"] += 1
            elif tipo == "ja_desligado":
                resumo["ja_desligados"] += 1
            elif tipo == flex_api.OK:
                resumo["desligados"] += 1
            else:
                resumo["falhas"] += 1
        await _andamento()

    await asyncio.gather(*(_um(a) for a in lote))
    resumo["restantes"] = (len(alvos) - resumo["processados"]) + resumo["ocupados"]
    async with session_scope() as s:
        s.add_all(logs)
        s.add(
            _log(
                acao="emergencia",
                modo=m,
                resultado="ok" if escreve else "simulado",
                motivo=(
                    f"emergência: {resumo['alvos']} alvo(s), {resumo['desligados']} desligado(s), "
                    f"{resumo['falhas']} falha(s), {resumo['ocupados']} ocupado(s), "
                    f"{resumo['simulados']} simulado(s), {resumo['restantes']} restante(s), "
                    f"{resumo['contas_sem_flex']} conta(s) sem Flex, "
                    f"{resumo['aprovacoes']} aprovação(ões) "
                    + ("retirada(s)" if escreve else "que sairia(m)")
                ),
                por=por,
            )
        )


async def emergencia(
    *, por: UUID | None, contas_escopo: Collection[UUID] | None = None
) -> dict:
    """A emergência inteira de uma vez (preparar + executar) — o que o job
    faz, sem a fila. A tela usa `preparar_emergencia` + o job."""
    prep = await preparar_emergencia(por=por, contas_escopo=contas_escopo)
    if prep.get("id") is None:
        return prep
    resumo = await executar_emergencia(int(prep["id"]))
    return {"id": prep["id"], **resumo}
