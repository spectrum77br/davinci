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
.sp (etapa 2), mas a peça sai de São Bernardo. O .sp é montado com
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
(`proxima_tentativa`, com espera crescente); recusa da plataforma (403 "item
down", 404) não se repete sozinha (`recusa`). Toda decisão que MUDA e toda
escrita viram linha em `flex_log` com o antes e o depois.

QUEM CHAMA: o cron do worker (`flex_motor_tick`, de flex_intervalo_min em
flex_intervalo_min), o botão "Sincronizar" da tela (job), a aprovação (só o
anúncio aprovado) e o gancho do pedido Flex (`flex_reavaliar_run`: passe
barato, sem leitura, que só DESLIGA — ver worker).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import (
    BlingOrder,
    FlexAnuncioEstado,
    FlexLog,
    FlexPedido,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Product,
    ProductLink,
)
from app.services import flex_config, flex_envio
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.bling_situacoes import (
    SITUACAO_ATENDIDO,
    SITUACAO_CANCELADO,
    SITUACAO_EM_ANDAMENTO,
)
from app.services.estoque_familia import LOTES_DE_VENDA, chave_familia, lote_de
from app.services.marketplaces import flex_api
from app.services.prioridade_estoque import analisa_codigo, sku_alvo

logger = structlog.get_logger()

LIGADO = "ligado"
DESLIGADO = "desligado"
INELEGIVEL = "inelegivel"
_LOTE_FLEX = "sp"

# Trava da RODADA (uma de cada vez: cron, botão, aprovação, gancho). ASCII
# "flex"; o namespace é o SYNC compartilhado do projeto (advisory_lock.py).
_MOTOR_LOCK_KEY = 0x666C6578

# Leituras por rodada. ML: 1 GET por anúncio (o limite do Flex é 1000/min por
# aplicação, somando ligar/desligar e o Turbo) — 400 a cada 15 min fica longe
# dele; com mais anúncios, a fila anda pelos mais velhos/mais urgentes
# primeiro. Shopee: 50 por chamada.
_TETO_LEITURAS_ML = 400
_TETO_LEITURAS_SHOPEE = 2000
# Emergência: escritas por clique (o resto fica em `restantes`; clicar de
# novo continua). Abaixo dos 1000/min do ML.
_TETO_EMERGENCIA = 300
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
# cancelado ou excluído não espera mais ninguém.
SITUACOES_FECHADAS = _SITUACOES_FECHADAS
# Recusa da plataforma ao DESLIGAR (403/404): não adianta repetir já.
_ESPERA_RECUSA = timedelta(hours=24)
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


@dataclass(frozen=True)
class Variacao:
    """Uma variação (= um vínculo vivo) do anúncio."""

    sku: str | None  # products.sku do vínculo (None = produto não achado)
    ativo: bool = True  # products.situacao == 'A'
    # product_links.stock: o último número enviado ao anúncio. 0 = a variação
    # não está à venda agora; None = nunca enviado (não se sabe: conta).
    estoque_publicado: int | None = None


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

    @property
    def chave(self) -> tuple[UUID, str]:
        return (self.integration_id, self.external_id)


@dataclass(frozen=True)
class SaldoSp:
    """Saldo Flex de UM SKU .sp."""

    sku_sp: str
    estoque: int | None  # products.stock do .sp ativo; None = não existe ativo
    pendentes: int = 0  # pedidos Flex em aberto ainda fora do .sp

    @property
    def saldo(self) -> int | None:
        return None if self.estoque is None else self.estoque - self.pendentes


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
    return familia, sku_alvo(low, tag, _LOTE_FLEX)


def _estado_atual(anuncio: Anuncio) -> str:
    """Para a histerese: o que a plataforma mostrou; sem leitura, a decisão
    anterior."""
    if anuncio.observado in (LIGADO, DESLIGADO):
        return anuncio.observado
    return LIGADO if anuncio.desejado_anterior == LIGADO else DESLIGADO


def decidir(anuncio: Anuncio, saldos: Mapping[str, SaldoSp], cfg: ConfigFlex) -> Decisao:
    """O que a regra quer para UM anúncio (sem o limite por família — ver
    `decidir_lote`). Pura: nada de banco, nada de API."""
    if not anuncio.variacoes:
        return Decisao(INELEGIVEL, "anúncio sem vínculo vivo com produto do DaVinci")
    vendaveis = [
        v for v in anuncio.variacoes if v.estoque_publicado is None or v.estoque_publicado > 0
    ]
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
                f"{sku_sp} não existe ativo — sem estoque .sp conhecido, nunca liga",
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


async def montar_anuncios(
    session: AsyncSession, integracoes: Mapping[UUID, Integration]
) -> list[Anuncio]:
    """Os anúncios das contas, agrupados por (conta, anúncio).

    Fonte principal: os vínculos VIVOS (`morto_desde IS NULL`) — cada um é uma
    variação com o produto (SKU, situação) e o último estoque enviado. A
    ligação anúncio → família é refeita a cada rodada (o SKU pode ter sido
    trocado no marketplace — crítica C4).

    Negação por padrão: anúncio importado (`listings`, não encerrado) da conta
    que não tem vínculo vivo entra SEM variações — a regra o dá como
    inelegível e, se o Flex estiver ligado nele, desliga."""
    if not integracoes:
        return []
    ids = list(integracoes)
    plataforma = {iid: _plataforma_de(i) for iid, i in integracoes.items()}
    variacoes: dict[tuple[UUID, str], list[Variacao]] = defaultdict(list)
    desde: dict[tuple[UUID, str], datetime] = {}
    rows = await session.execute(
        select(
            ProductLink.integration_id,
            ProductLink.external_id,
            ProductLink.stock,
            ProductLink.created_at,
            Product.sku,
            Product.situacao,
        )
        .join(Product, Product.id == ProductLink.product_id)
        .where(
            ProductLink.integration_id.in_(ids),
            ProductLink.morto_desde.is_(None),
            ProductLink.platform.in_([IntegrationPlatform.ML, IntegrationPlatform.SHOPEE]),
        )
    )
    for r in rows.all():
        p = plataforma.get(r.integration_id)
        ext = _id_anuncio(p or "", r.external_id)
        if p is None or not ext:
            continue
        chave = (r.integration_id, ext)
        variacoes[chave].append(
            Variacao(
                sku=r.sku,
                ativo=(r.situacao or "") == "A",
                estoque_publicado=r.stock,
            )
        )
        if r.created_at is not None and (chave not in desde or r.created_at < desde[chave]):
            desde[chave] = r.created_at

    importados = await session.execute(
        select(Listing.integration_id, Listing.external_id)
        .where(Listing.integration_id.in_(ids), Listing.status != ListingStatus.CLOSED)
        .distinct()
    )
    for iid, ext_bruto in importados.all():
        p = plataforma.get(iid)
        ext = _id_anuncio(p or "", ext_bruto)
        if p is None or not ext:
            continue
        variacoes.setdefault((iid, ext), [])

    return [
        Anuncio(
            integration_id=iid,
            external_id=ext,
            plataforma=plataforma[iid] or "",
            variacoes=tuple(vs),
            desde=desde.get((iid, ext)),
        )
        for (iid, ext), vs in variacoes.items()
    ]


def _demanda_por_peca(itens: Iterable[tuple[str | None, Any]]) -> Counter[str]:
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
            if lote in LOTES_DE_VENDA and lote != _LOTE_FLEX:
                demanda[p[: -(len(lote) + 1)]] += q
    return demanda


def _pendentes(sku_sp: str, demanda: Mapping[str, int]) -> int:
    """Quanto o .sp deve aos pedidos Flex: no kit, a peça mais pedida (o
    estoque do kit .sp já é o da peça mais escassa — conservador)."""
    pecas = [
        p.strip()[: -(len(_LOTE_FLEX) + 1)]
        for p in sku_sp.split("+")
        if lote_de(p.strip()) == _LOTE_FLEX
    ]
    return max((int(demanda.get(p, 0)) for p in pecas), default=0)


async def calcular_saldos(session: AsyncSession, skus_sp: Collection[str]) -> dict[str, SaldoSp]:
    """Saldo Flex de cada SKU .sp pedido.

    Estoque: `products.stock` (saldo VIRTUAL do Bling — já desconta as
    reservas) do produto ATIVO com aquele SKU. SKU não é único em `products`:
    gêmeos do mesmo produto do Bling (`bling_product_id`) contam uma vez; dois
    produtos do Bling diferentes com o mesmo SKU valem o MENOR (nunca soma).
    Sem produto ativo: estoque None — desconhecido nunca liga."""
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
    itens = await session.execute(
        select(BlingOrder.item_codigo, BlingOrder.item_quantidade)
        .join(FlexPedido, FlexPedido.bling_id == BlingOrder.bling_id)
        .where(
            FlexPedido.no_sp.is_(False),
            FlexPedido.detectado_em >= corte,
            func.coalesce(BlingOrder.situacao, "").notin_(_SITUACOES_FECHADAS),
        )
    )
    demanda = _demanda_por_peca(itens.all())

    out: dict[str, SaldoSp] = {}
    for sku in alvos:
        estoques = por_sku.get(sku)
        estoque = min(estoques.values()) if estoques else None
        if estoques and len(estoques) > 1:
            logger.info("flex_sku_sp_duplicado", sku=sku, produtos=len(estoques))
        out[sku] = SaldoSp(sku_sp=sku, estoque=estoque, pendentes=_pendentes(sku, demanda))
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
        )
        for e in rows.scalars().all()
    }


def _com_estado(
    anuncios: Iterable[Anuncio], estados: Mapping[tuple[UUID, str], _Estado]
) -> list[Anuncio]:
    """Junta o estado gravado em cada anúncio — e põe na lista o anúncio que
    só existe no estado (vínculo morreu, saiu da importação): sem variações,
    a regra o desliga (negação por padrão)."""
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
# Leitura do estado real
# =============================================================================


def _escolher_leituras(
    anuncios: list[Anuncio],
    decisoes: Mapping[tuple[UUID, str], Decisao],
    estados: Mapping[tuple[UUID, str], _Estado],
    alvo: set[tuple[UUID, str]] | None,
) -> list[Anuncio]:
    """Quem é lido nesta rodada. Primeiro quem nunca foi lido ou onde a regra
    discorda do que foi lido (é ali que pode haver escrita), depois os lidos
    há mais tempo — dentro do teto de leituras."""
    if alvo is not None:
        return [a for a in anuncios if a.chave in alvo]
    minimo = datetime.min.replace(tzinfo=UTC)

    def prioridade(a: Anuncio) -> tuple:
        e = estados.get(a.chave)
        obs = e.observado if e else None
        quando = e.observado_em if e else None
        quer = decisoes[a.chave].desejado == LIGADO
        precisa = obs is None or quando is None or quer != (obs == LIGADO)
        return (0 if precisa else 1, quando or minimo, str(a.integration_id), a.external_id)

    ml = sorted((a for a in anuncios if a.plataforma == flex_envio.PLATAFORMA_ML), key=prioridade)
    sh = sorted(
        (a for a in anuncios if a.plataforma == flex_envio.PLATAFORMA_SHOPEE), key=prioridade
    )
    return ml[:_TETO_LEITURAS_ML] + sh[:_TETO_LEITURAS_SHOPEE]


async def _ler(
    escolhidos: list[Anuncio],
    integracoes: Mapping[UUID, Integration],
    clientes: dict[UUID, Any],
    resumo: dict,
) -> dict[tuple[UUID, str], flex_api.ResultadoFlex]:
    """Lê o Flex dos anúncios escolhidos, conta por conta. Só LEITURA.

    A leitura também passa pela trava do anúncio: nenhuma chamada ao mesmo
    anúncio ao mesmo tempo (a emergência e a aprovação escrevem fora da
    rodada). Anúncio travado fica para a próxima rodada."""
    por_conta: dict[UUID, list[Anuncio]] = defaultdict(list)
    for a in escolhidos:
        por_conta[a.integration_id].append(a)
    out: dict[tuple[UUID, str], flex_api.ResultadoFlex] = {}

    def _contar(r: flex_api.ResultadoFlex) -> None:
        resumo["lidos"] = resumo.get("lidos", 0) + 1
        if not r.ok:
            resumo["leituras_falhas"] = resumo.get("leituras_falhas", 0) + 1

    for iid, lista in por_conta.items():
        integ = integracoes.get(iid)
        if integ is None:
            continue
        cli = await _cliente(integ, clientes)
        if cli is None:
            resumo["contas_sem_cliente"] = resumo.get("contas_sem_cliente", 0) + 1
            continue
        if _plataforma_de(integ) == flex_envio.PLATAFORMA_ML:
            for a in lista:
                async with session_scope() as s:
                    if not await _travar(s, _chave_trava(a.integration_id, a.external_id)):
                        resumo["leituras_ocupadas"] = resumo.get("leituras_ocupadas", 0) + 1
                        continue
                    r = await cli.ler_flex(a.external_id)
                out[a.chave] = r
                _contar(r)
                # Limite estourado ou token recusado: as próximas leituras da
                # conta só bateriam no mesmo muro. Fica para a próxima rodada.
                if (r.tipo == flex_api.REPETIR and r.status_http == 429) or (
                    r.tipo == flex_api.SEM_PERMISSAO
                ):
                    resumo["contas_interrompidas"] = resumo.get("contas_interrompidas", 0) + 1
                    break
            continue
        # Shopee: 50 anúncios por chamada, cada um com a sua trava.
        for inicio in range(0, len(lista), 50):
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
    anuncios: list[Anuncio], leituras: Mapping[tuple[UUID, str], flex_api.ResultadoFlex]
) -> list[Anuncio]:
    out: list[Anuncio] = []
    for a in anuncios:
        r = leituras.get(a.chave)
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
                )
                s.add(est)
            desejado_antes = None if novo else est.desejado
            observado_antes = None if novo else est.observado
            mudou_algo = novo

            lido = leituras.get(a.chave)
            mudou_obs = False
            if lido is not None:
                if lido.ok and lido.has_flex is not None:
                    est.observado = LIGADO if lido.has_flex else DESLIGADO
                    est.observado_em = agora
                    mudou_obs = est.observado != observado_antes
                    if lido.has_flex:
                        est.recusa = None
                    if (est.ultimo_erro or "").startswith("leitura:"):
                        est.ultimo_erro = None
                    mudou_algo = True
                else:
                    est.ultimo_erro = f"leitura: {lido.texto()}"[:500]
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
            precisa = d.desejado == LIGADO and est.observado != LIGADO and est.aprovado_em is None
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

            # Escrita possível nesta rodada?
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
                    _Acao(a.integration_id, a.external_id, a.plataforma, "ligar", mudou, DESLIGADO)
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
            est.proxima_tentativa = agora + _ESPERA_RECUSA
    else:
        est.proxima_tentativa = agora + _espera(est.tentativas)
    est.atualizado_em = agora


async def _escrever(
    cliente: Any,
    *,
    integration_id: UUID,
    external_id: str,
    plataforma: str,
    acao: str,
    modo: str,
    por: UUID | None,
) -> str:
    """Uma escrita, serializada por anúncio: trava → relê o estado → confere
    se ainda é preciso → chama → grava estado + trilha (commit solta a trava).

    Devolve o tipo do resultado, "ocupado" (outro processo está no anúncio)
    ou "mudou" (o estado relido já não pede a escrita)."""
    agora = _agora()
    async with session_scope() as s:
        if not await _travar(s, _chave_trava(integration_id, external_id)):
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
        elif est is None:
            return "mudou"
        elif acao == "ligar" and not (
            est.desejado == LIGADO and est.aprovado_em is not None and est.observado == DESLIGADO
        ):
            return "mudou"
        elif acao == "desligar" and not (est.desejado != LIGADO and est.observado == LIGADO):
            return "mudou"
        antes = est.observado
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
    lado seguro quando o teto corta a rodada)."""
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
    for a in acoes:
        if a.plataforma == flex_envio.PLATAFORMA_SHOPEE and not shopee_escreve:
            resumo["shopee_so_leitura"] = resumo.get("shopee_so_leitura", 0) + 1
            if a.mudou:
                ignoradas.append(a)
            continue
        if escritas >= teto:
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
        )
        if tipo in ("ocupado", "mudou"):
            resumo[tipo] = resumo.get(tipo, 0) + 1
            continue
        escritas += 1
        chave = f"{a.acao}_{'ok' if tipo == flex_api.OK else 'falhou'}"
        resumo[chave] = resumo.get(chave, 0) + 1
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
) -> dict:
    """Uma rodada do motor. Respeita o modo; uma rodada de cada vez.

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
    async with session_scope() as trava:
        if not await _travar(trava, _MOTOR_LOCK_KEY):
            resumo["ocupado"] = True
            return resumo
        resumo["rodou"] = True
        await _rodada(
            cfg,
            contas,
            modo=m,
            ler=ler,
            so_desligar=so_desligar,
            alvo=set(somente) if somente is not None else None,
            por=por,
            resumo=resumo,
        )
    logger.info("flex_motor_rodada", **{k: v for k, v in resumo.items() if v not in (0, None)})
    return resumo


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
) -> None:
    agora = _agora()
    async with session_scope() as s:
        integracoes = await integracoes_permitidas(s, contas)
        if not integracoes:
            resumo["motivo"] = "nenhuma conta permitida ativa (ML/Shopee)"
            return
        estados = await _fotos_dos_estados(s, integracoes.keys())
        anuncios = _com_estado(await montar_anuncios(s, integracoes), estados)
        anuncios = [a for a in anuncios if a.integration_id in integracoes]
        saldos = await calcular_saldos(s, _skus_sp(anuncios, cfg))
    resumo["contas"] = len(integracoes)
    resumo["anuncios"] = len(anuncios)

    decisoes = decidir_lote(anuncios, saldos, cfg)
    clientes: dict[UUID, Any] = {}
    leituras: dict[tuple[UUID, str], flex_api.ResultadoFlex] = {}
    if ler:
        escolhidos = _escolher_leituras(anuncios, decisoes, estados, alvo)
        leituras = await _ler(escolhidos, integracoes, clientes, resumo)
        if leituras:
            anuncios = _com_leituras(anuncios, leituras)
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
    await _aplicar(acoes, integracoes, clientes, modo=modo, por=por, resumo=resumo)


async def aprovar(integration_id: UUID, external_id: str, *, por: UUID | None) -> dict:
    """Uma pessoa aprova LIGAR o Flex no anúncio. Em piloto/ativo o motor roda
    na hora só para ele (relê o estado, recalcula com o saldo de agora e
    liga se ainda for o caso); em observar fica só a aprovação.

    Também é o "tente de novo" depois de uma recusa da plataforma."""
    m = flex_config.modo()
    if m == flex_config.MODO_DESLIGADO:
        raise FlexRegraError("flex_desligado", "o Flex está com flex_modo=desligado")
    if integration_id not in flex_config.contas():
        raise FlexRegraError("conta_nao_permitida", "a conta não está em flex_contas")
    agora = _agora()
    async with session_scope() as s:
        est = await s.get(FlexAnuncioEstado, (integration_id, external_id))
        if est is None:
            raise FlexRegraError("nao_avaliado", "o motor ainda não avaliou este anúncio")
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
    out: dict[str, Any] = {"aprovado": True, "modo": m, "aplicado": False}
    if flex_config.pode_escrever(m):
        resumo = await rodar_motor(
            modo=m, somente={(integration_id, external_id)}, por=por, origem="aprovacao"
        )
        out["motor"] = resumo
        out["aplicado"] = bool(resumo.get("ligar_ok"))
    return out


async def emergencia(*, por: UUID | None) -> dict:
    """Botão de emergência: desliga o Flex de TUDO nas contas permitidas.

    Tira toda aprovação (nada volta a ligar sem uma pessoa aprovar de novo) e
    desliga o que está ligado — ou que nunca foi lido — até
    `_TETO_EMERGENCIA` por clique (`restantes` diz quanto falta). Em
    observar/desligado só SIMULA. Shopee sem `flex_shopee_escrita`: não
    escreve (fica em `shopee_so_leitura`)."""
    m = flex_config.modo()
    escreve = flex_config.pode_escrever(m)
    resumo: dict[str, Any] = {
        "modo": m,
        "escreve": escreve,
        "alvos": 0,
        "desligados": 0,
        "falhas": 0,
        "simulados": 0,
        "restantes": 0,
    }
    contas = flex_config.contas()
    if not contas:
        resumo["motivo"] = "nenhuma conta em flex_contas"
        return resumo
    agora = _agora()
    async with session_scope() as s:
        integracoes = await integracoes_permitidas(s, contas)
        if not integracoes:
            resumo["motivo"] = "nenhuma conta permitida ativa (ML/Shopee)"
            return resumo
        await s.execute(
            update(FlexAnuncioEstado)
            .where(
                FlexAnuncioEstado.integration_id.in_(list(integracoes)),
                FlexAnuncioEstado.aprovado_em.is_not(None),
            )
            .values(aprovado_em=None, aprovado_por=None, atualizado_em=agora)
        )
        estados = await _fotos_dos_estados(s, integracoes.keys())
        anuncios = _com_estado(await montar_anuncios(s, integracoes), estados)
    alvos = [
        a
        for a in anuncios
        if a.integration_id in integracoes and a.observado in (LIGADO, None)
    ]
    alvos.sort(
        key=lambda a: (0 if a.observado == LIGADO else 1, str(a.integration_id), a.external_id)
    )
    resumo["alvos"] = len(alvos)
    resumo["ligados_conhecidos"] = sum(1 for a in alvos if a.observado == LIGADO)

    clientes: dict[UUID, Any] = {}
    shopee_escreve = bool(get_settings().flex_shopee_escrita)
    feitos = 0
    logs: list[FlexLog] = []
    for a in alvos:
        if feitos >= _TETO_EMERGENCIA:
            break
        if not escreve:
            feitos += 1
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
            continue
        if a.plataforma == flex_envio.PLATAFORMA_SHOPEE and not shopee_escreve:
            resumo["shopee_so_leitura"] = resumo.get("shopee_so_leitura", 0) + 1
            continue
        cli = await _cliente(integracoes[a.integration_id], clientes)
        if cli is None:
            resumo["sem_cliente"] = resumo.get("sem_cliente", 0) + 1
            continue
        feitos += 1
        tipo = await _escrever(
            cli,
            integration_id=a.integration_id,
            external_id=a.external_id,
            plataforma=a.plataforma,
            acao="emergencia",
            modo=m,
            por=por,
        )
        if tipo == flex_api.OK:
            resumo["desligados"] += 1
        elif tipo == "ocupado":
            resumo["ocupados"] = resumo.get("ocupados", 0) + 1
        else:
            resumo["falhas"] += 1
    resumo["restantes"] = max(0, len(alvos) - feitos - resumo.get("shopee_so_leitura", 0))
    async with session_scope() as s:
        s.add_all(logs)
        s.add(
            _log(
                acao="emergencia",
                modo=m,
                resultado="ok" if escreve else "simulado",
                motivo=(
                    f"emergência: {resumo['alvos']} alvo(s), {resumo['desligados']} desligado(s), "
                    f"{resumo['falhas']} falha(s), {resumo['simulados']} simulado(s), "
                    f"{resumo['restantes']} restante(s)"
                ),
                por=por,
            )
        )
    logger.warning("flex_emergencia", por=str(por) if por else None, **resumo)
    return resumo
