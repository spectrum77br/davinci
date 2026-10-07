"""Regras do Painel de Garantia Uranyx (07/10/2026).

Documento "Painel de Garantia — Uranyx (DaVinci)" — RN01 a RN07, status por
data, cadastro, atendimentos do Comunicador (o /atendimento do DaVinci) e
LGPD. Tudo o que é REGRA mora aqui (o router só confere permissão e escopo);
os pontos que o dono decidiu (§8) estão logo abaixo, num lugar só, para
trocar sem caçar no código.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import Text, and_, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica, SituacaoBling, User
from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.models.bling_nota import BlingNotaEmitida
from app.models.garantia import Garantia, GarantiaLog
from app.models.nf import NfNota
from app.models.pricing import StoreInfo
from app.services.pos_vendas import _FRACAO_EMBALAGEM_MAX, _FRACAO_PRODUTO_MIN

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# ══ PONTOS DEFINIDOS (§8 do documento) — decididos pelo dono em 07/10/2026 ══
#
# 1. Último dia: a garantia vale ATÉ a data final, INCLUSIVE. Com False, ela
#    termina no dia anterior (o fim exibido continua início + N meses; muda só
#    a comparação — status, indicadores e cobertura dos atendimentos).
ULTIMO_DIA_COBERTO = True
# 2. Cadastro antes da entrega: PODE. Sem data de entrega a garantia fica
#    "Aguardando entrega" e o robô completa quando a data aparecer. Com False,
#    o cadastro de pedido sem entrega é recusado (422 `pedido_sem_entrega`).
CADASTRO_ANTES_DA_ENTREGA = True
# 3. Garantia por NF (não por produto/nº de série): UMA garantia por NF —
#    RN06 no banco (`uq_garantias_cpf_nf`: CPF + NF + série) e a chave de 44
#    dígitos única quando a NF casa com o XML do pedido. O detalhe mostra os
#    itens do pedido. Por produto/nº de série fica para depois: nenhum XML de
#    produto tem <nSerie> hoje (levantamento de 07/10/2026).
GARANTIA_POR = "nf"
# 4. Vínculo no Comunicador: MANUAL — o atendente clica em "Vincular à
#    garantia" e busca por CPF, NF ou pedido. O DaVinci só PREENCHE a busca com
#    o pedido da conversa (`busca_sugerida`) e acende o alerta de CPF sem
#    garantia; nunca vincula sozinho.
VINCULO_AUTOMATICO = False
# 5. Correção da data de entrega: a garantia é RECALCULADA sozinha (robô de
#    hora em hora, `recalcular_todas`), com linha no log da garantia. Com
#    False, o robô só preenche a data de quem está "Aguardando entrega".
RECALCULAR_QUANDO_ENTREGA_MUDAR = True
# ═════════════════════════════════════════════════════════════════════════════

MESES_HARDWARE = 3  # RN02
MESES_SOFTWARE = 12  # RN03

STATUS_AGUARDANDO = "aguardando_entrega"
STATUS_ATIVA = "ativa"
STATUS_SOMENTE_SOFTWARE = "somente_software"
STATUS_EXPIRADA = "expirada"
STATUS_ROTULOS = {
    STATUS_AGUARDANDO: "Aguardando entrega",
    STATUS_ATIVA: "Ativa",
    STATUS_SOMENTE_SOFTWARE: "Somente software",
    STATUS_EXPIRADA: "Expirada",
}
# Indicador "HW vence em 30d" do topo do painel.
DIAS_ALERTA_HARDWARE = 30

# "Aguardando entrega" de um pedido que o BLING já dá como entregue: nenhuma
# fonte do DaVinci tem a data (a Logística guarda a data só desde 15/07/2026,
# e o TikTok que já passou para COMPLETED perdeu o carimbo de entregue). O
# status continua "aguardando_entrega" (sem data não há prazo — RN01 proíbe
# digitar a data), mas a tela não diz "aguardando": diz que JÁ foi entregue e
# que falta a data. Situações do Bling que querem dizer "chegou ao cliente"
# (Resolvido = pós-venda resolvido de pedido entregue; as outras, como
# Manutenção e Problemas, não dizem com certeza e ficam como aguardando):
SITUACOES_BLING_ENTREGUE = {
    "83953": "Entregue",
    "545902": "Resolvido",
}
ROTULO_ENTREGUE_SEM_DATA = "Entregue — sem data no DaVinci"

COBERTO = "coberto"
FORA_DA_GARANTIA = "fora_da_garantia"
SEM_DATA_DE_ENTREGA = "sem_data_de_entrega"
COBERTURA_ROTULOS = {
    COBERTO: "Coberto",
    FORA_DA_GARANTIA: "Fora da garantia",
    SEM_DATA_DE_ENTREGA: "Sem data de entrega",
}

# Produto Uranyx = SKU no recorte da Uranyx: eletro `u*`, celulares
# `dg<dígito>` e acessórios `a0NN`. O MESMO recorte de
# routers/sites_estoque.ESCOPOS["uranyx"] (um teste confere). Nenhuma loja
# identifica a Uranyx: os celulares saem por 38 lojas "diversos".
SKU_URANYX = r"^(u|dg[0-9]|a0[0-9]{2})"
_RX_URANYX = re.compile(SKU_URANYX, re.I)


@dataclass(frozen=True)
class FonteEntrega:
    chave: str
    rotulo: str
    campo: str  # chave em logistica.meli_status / status_datas
    valor: str  # o status que quer dizer "entregue"
    aceita: frozenset[str]  # fontes do carimbo aceitas (logistica_datas)


# De onde vem a DATA DE ENTREGA (RN01), na ordem de confiança (levantamento
# de 07/10/2026). `logistica.status_datas` guarda a data OFICIAL da plataforma
# enquanto o status atual é o de entregue. A Shopee/ML/TikTok sem carimbo
# `plataforma` não entram: o carimbo `davinci` é o instante em que o DaVinci
# viu, e na 1ª leitura de um pedido antigo ele fica dias depois da entrega.
# O COMPLETED do TikTok NÃO é entrega (mediana 35 dias depois do despacho).
FONTES_ENTREGA: tuple[FonteEntrega, ...] = (
    FonteEntrega(
        "ml",
        "Mercado Livre — data oficial da entrega",
        "ship_status",
        "delivered",
        frozenset({"plataforma"}),
    ),
    FonteEntrega(
        "shopee",
        "Shopee — evento de entrega da transportadora",
        "logistics_status",
        "LOGISTICS_DELIVERY_DONE",
        frozenset({"plataforma"}),
    ),
    FonteEntrega(
        "tiktok",
        "TikTok — pedido Entregue",
        "order_status",
        "DELIVERED",
        frozenset({"plataforma"}),
    ),
    FonteEntrega(
        "amazon",
        "Amazon — EasyShip Entregue",
        "easyship_status",
        "Delivered",
        frozenset({"plataforma", "aprox"}),
    ),
)
# Último recurso: o rastreio (17track/EasyShip) visto pelo DaVinci. É o
# instante em que o robô do rastreio VIU a entrega (logistica_track_sync e
# logistica_amazon gravam `agora`), não a hora do evento: data aproximada —
# o rótulo diz isso na tela e no log.
FONTE_RASTREIO = "rastreio"
ORIGEM_ROTULOS = {f.chave: f.rotulo for f in FONTES_ENTREGA} | {
    FONTE_RASTREIO: "Rastreio (17track/EasyShip) — data aproximada: o dia em que o DaVinci viu"
    " a entrega",
}
# Confiança da origem para o recálculo: a data da plataforma (0) corrige a do
# rastreio (1), nunca o contrário — o pedido que voltou de "entregue" para
# "devolução" perde o carimbo da plataforma e sobra o do rastreio, que não é
# correção nenhuma.
CONFIANCA_ORIGEM = {f.chave: 0 for f in FONTES_ENTREGA} | {FONTE_RASTREIO: 1}

# Atendimento (§5).
MAX_MENSAGENS_COPIADAS = 100
MAX_TEXTO_MENSAGEM = 4000
MAX_RESUMO_AUTOMATICO = 1000
# Data do atendimento sem mensagens escolhidas (§5.1 "o cliente abre um
# atendimento"): a 1ª mensagem do cliente no TRECHO ATUAL da conversa — o
# trecho termina na última mensagem do cliente e volta enquanto não houver
# pausa maior que isto entre uma mensagem e a seguinte. A reclamação aberta
# dentro do prazo continua coberta se o cliente manda o vídeo depois do fim;
# a conversa que voltou meses depois ("cadê meu pedido" → defeito) começa de
# novo.
DIAS_PAUSA_NOVO_ATENDIMENTO = 7
# Clique duplo em "Vincular" não vira dois atendimentos (que não se apagam).
JANELA_ATENDIMENTO_REPETIDO = timedelta(minutes=10)
LINK_CONVERSA = "/atendimento?conversa={id}"

# Busca pelo CPF completo (lista e "Vincular à garantia"): a máscara
# ***.456.789-** deixa só 1.000 CPFs possíveis (os 2 dígitos verificadores
# saem da conta), então a busca exata por CPF tem teto por pessoa — (vezes,
# janela em segundos) — e cada garantia achada ganha "buscou pelo CPF" no log.
LIMITES_BUSCA_CPF = ((30, 3600), (100, 86400))

# Anexos: o arquivo é baixado do CDN da plataforma na hora do vínculo (a URL
# expira). Só HTTPS e só os CDNs que aparecem nas conversas (07/10/2026);
# o ML guarda só o nome do arquivo (baixar exige a API com token).
ANEXO_MAX_BYTES = 8 * 1024 * 1024
ANEXOS_MAX_POR_ATENDIMENTO = 10
ANEXOS_MAX_BYTES_TOTAL = 40 * 1024 * 1024
ANEXO_TIMEOUT_S = 8.0
ANEXO_HOSTS = (
    "susercontent.com",
    "shopee.sg",
    "shopee.com.br",
    "ibyteimg.com",
    "byteimg.com",
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokcdn-eu.com",
)
ANEXO_TIPOS = ("image/", "video/", "application/pdf")
# Imagem que é DOCUMENTO (SVG roda script se aberto na origem do DaVinci):
# fora — pedaços do content-type que recusam mesmo dentro de image/.
ANEXO_TIPOS_RECUSADOS = ("svg", "xml", "html")
# Redirect do CDN: seguido à mão, conferindo o host de cada salto ANTES do GET.
ANEXO_MAX_REDIRECTS = 3
_TIPOS_CARTAO = ("produto", "pedido")

ROTULO_SISTEMA = "Sistema (robô da entrega)"


# ── Datas e prazos ──────────────────────────────────────────────────────────


def hoje() -> date:
    """O dia de hoje em São Paulo. Função (e não constante) para os testes
    trocarem o relógio."""
    return datetime.now(SAO_PAULO).date()


def somar_meses(d: date, meses: int) -> date:
    """RN04: 30/11 + 3 meses = 28/02 (o dia que não existe vira o último)."""
    total = d.month - 1 + meses
    ano = d.year + total // 12
    mes = total % 12 + 1
    return date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def calcular_prazos(inicio: date) -> tuple[date, date]:
    """(fim do hardware, fim do software) — RN02, RN03, RN04."""
    return somar_meses(inicio, MESES_HARDWARE), somar_meses(inicio, MESES_SOFTWARE)


def coberto(fim: date | None, dia: date) -> bool:
    """O `dia` está dentro do prazo que termina em `fim`? (ponto 1)"""
    if fim is None:
        return False
    return dia <= fim if ULTIMO_DIA_COBERTO else dia < fim


def status_em(inicio: date | None, fim_hw: date | None, fim_sw: date | None, dia: date) -> str:
    if inicio is None:
        return STATUS_AGUARDANDO
    if coberto(fim_hw, dia):
        return STATUS_ATIVA
    if coberto(fim_sw, dia):
        return STATUS_SOMENTE_SOFTWARE
    return STATUS_EXPIRADA


def status_da(g: Garantia, dia: date | None = None) -> str:
    return status_em(g.data_inicio, g.fim_hardware, g.fim_software, dia or hoje())


def entregue_sem_data(data_inicio: date | None, situacao_bling: str | None) -> bool:
    """Sem data de entrega, mas o Bling já dá o pedido como entregue."""
    return data_inicio is None and str(situacao_bling or "") in SITUACOES_BLING_ENTREGUE


def rotulo_do_status(status: str, sem_data_entregue: bool = False) -> str:
    if status == STATUS_AGUARDANDO and sem_data_entregue:
        return ROTULO_ENTREGUE_SEM_DATA
    return STATUS_ROTULOS[status]


def _pedido_entregue_no_bling():
    """EXISTS: o pedido da garantia está numa situação de entregue no Bling."""
    return (
        select(BlingOrder.id)
        .where(
            BlingOrder.numero == Garantia.pedido_bling,
            BlingOrder.situacao.in_(list(SITUACOES_BLING_ENTREGUE)),
        )
        .exists()
    )


async def situacoes_dos_pedidos(session: AsyncSession, numeros: list[str]) -> dict[str, str]:
    """A situação de cada pedido no Bling (id), para a lista."""
    numeros = sorted({n for n in numeros if n})
    if not numeros:
        return {}
    linhas = await session.execute(
        select(BlingOrder.numero, func.max(BlingOrder.situacao))
        .where(BlingOrder.numero.in_(numeros))
        .group_by(BlingOrder.numero)
    )
    return {str(n): s for n, s in linhas.all() if s}


def dia_em_sp(quando: datetime) -> date:
    q = quando if quando.tzinfo else quando.replace(tzinfo=UTC)
    return q.astimezone(SAO_PAULO).date()


def cobertura_em(g: Garantia, tipo: str, quando: datetime) -> tuple[str, date | None]:
    """§5.2: Coberto se a data do atendimento ≤ fim da garantia do tipo
    informado; senão Fora da garantia. Sem entrega ainda: sem data."""
    if g.data_inicio is None:
        return SEM_DATA_DE_ENTREGA, None
    fim = g.fim_hardware if tipo == "hardware" else g.fim_software
    return (COBERTO if coberto(fim, dia_em_sp(quando)) else FORA_DA_GARANTIA), fim


def prazo_info(inicio: date | None, fim: date | None, dia: date) -> dict[str, Any] | None:
    """O que a barra de dias restantes precisa."""
    if inicio is None or fim is None:
        return None
    total = (fim - inicio).days
    restantes = max(0, min((fim - dia).days, total))
    return {
        "fim": fim,
        "dias_total": total,
        "dias_restantes": restantes,
        "coberto_hoje": coberto(fim, dia),
    }


# Mesma regra em SQL (filtro de status e indicadores).
def _coberto_sql(coluna, dia: date):
    return coluna >= dia if ULTIMO_DIA_COBERTO else coluna > dia


def filtro_status(status: str, dia: date):
    g = Garantia
    if status == STATUS_AGUARDANDO:
        return g.data_inicio.is_(None)
    if status == STATUS_ATIVA:
        return and_(g.data_inicio.is_not(None), _coberto_sql(g.fim_hardware, dia))
    if status == STATUS_SOMENTE_SOFTWARE:
        return and_(
            g.data_inicio.is_not(None),
            ~_coberto_sql(g.fim_hardware, dia),
            _coberto_sql(g.fim_software, dia),
        )
    if status == STATUS_EXPIRADA:
        return and_(g.data_inicio.is_not(None), ~_coberto_sql(g.fim_software, dia))
    if status == "hw_vence_30d":
        return and_(
            filtro_status(STATUS_ATIVA, dia),
            g.fim_hardware <= dia + timedelta(days=DIAS_ALERTA_HARDWARE),
        )
    if status == "entregue_sem_data":
        return and_(g.data_inicio.is_(None), _pedido_entregue_no_bling())
    raise ValueError(status)


def status_sql(dia: date):
    """CASE com o status (para ordenar/contar no banco)."""
    g = Garantia
    return case(
        (g.data_inicio.is_(None), literal(STATUS_AGUARDANDO)),
        (_coberto_sql(g.fim_hardware, dia), literal(STATUS_ATIVA)),
        (_coberto_sql(g.fim_software, dia), literal(STATUS_SOMENTE_SOFTWARE)),
        else_=literal(STATUS_EXPIRADA),
    )


def mes_atual(dia: date) -> tuple[datetime, datetime]:
    """[1º dia do mês, 1º dia do mês seguinte) em São Paulo."""
    ini = datetime(dia.year, dia.month, 1, tzinfo=SAO_PAULO)
    fim_ano, fim_mes = (dia.year + 1, 1) if dia.month == 12 else (dia.year, dia.month + 1)
    return ini, datetime(fim_ano, fim_mes, 1, tzinfo=SAO_PAULO)


# ── CPF, NF, nome ───────────────────────────────────────────────────────────


def so_digitos(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def cpf_valido(v: Any) -> bool:
    """RN06: 11 dígitos, dígitos verificadores certos e não repetidos
    (111.111.111-11 passa no cálculo, mas não existe). Pontuação de CPF
    (ponto, hífen, espaço) é tolerada; letra no meio, não."""
    if v is None or re.search(r"[^\d.\-\s]", str(v)):
        return False
    d = so_digitos(v)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(d[n]):
            return False
    return True


def formatar_cpf(cpf: str) -> str:
    d = so_digitos(cpf)
    if len(d) != 11:
        return d
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def mascarar_cpf(cpf: str | None) -> str | None:
    """§6: ***.456.789-** — o que a lista mostra."""
    d = so_digitos(cpf)
    if len(d) != 11:
        return None
    return f"***.{d[3:6]}.{d[6:9]}-**"


def cpf_da_busca(termo: str | None) -> str | None:
    """Os 11 dígitos quando a busca é um CPF COMPLETO e válido (formatado ou
    não). É só aí que a busca procura pelo CPF — e conta no teto
    (LIMITES_BUSCA_CPF). Pedaço de CPF nunca busca."""
    t = (termo or "").strip()
    if not t or re.search(r"[^\d.\-\s]", t):
        return None
    d = so_digitos(t)
    return d if len(d) == 11 and cpf_valido(d) else None


def normalizar_nf(v: Any) -> str:
    """Número da NF: só dígitos, sem zero à esquerda ("000.010.234" = 10234)."""
    return so_digitos(v).lstrip("0")


def normalizar_serie(v: Any) -> str:
    return so_digitos(v).lstrip("0")


def normalizar_nome(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


_ACENTOS = "áàâãäéèêëíìîïóòôõöúùûüçñ"
_SEM_ACENTO = "aaaaaeeeeiiiiooooouuuucn"


def sem_acento_sql(coluna):
    return func.translate(func.lower(coluna), _ACENTOS, _SEM_ACENTO)


def sem_acento(texto: str) -> str:
    return texto.lower().translate(str.maketrans(_ACENTOS, _SEM_ACENTO))


def e_uranyx(sku: str | None) -> bool:
    return bool(sku) and bool(_RX_URANYX.match(sku.strip()))


# ── Data de entrega (RN01) ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Entrega:
    em: datetime
    origem: str

    @property
    def dia(self) -> date:
        return dia_em_sp(self.em)


def _instante(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def entrega_da_logistica(lg: Logistica | None) -> Entrega | None:
    """A data de entrega de uma linha da Logística, pela ordem de FONTES_ENTREGA."""
    if lg is None:
        return None
    status = lg.meli_status if isinstance(lg.meli_status, dict) else {}
    datas = lg.status_datas if isinstance(lg.status_datas, dict) else {}
    for fonte in FONTES_ENTREGA:
        if str(status.get(fonte.campo) or "") != fonte.valor:
            continue
        carimbo = datas.get(fonte.campo)
        if not isinstance(carimbo, dict) or carimbo.get("fonte") not in fonte.aceita:
            continue
        em = _instante(carimbo.get("em"))
        if em is not None:
            return Entrega(em, fonte.chave)
    if lg.entregue_em is not None:
        return Entrega(_instante(lg.entregue_em) or lg.entregue_em, FONTE_RASTREIO)
    return None


async def logisticas_dos_pedidos(
    session: AsyncSession, pares: list[tuple[str, str | None]]
) -> dict[str, Logistica]:
    """A linha da Logística de cada pedido (pelo nº do Bling; sem ela, pelo
    nº do marketplace). Chave do resultado = nº do Bling."""
    if not pares:
        return {}
    numeros = {p for p, _ in pares}
    mkts = {m: p for p, m in pares if m}
    achadas: dict[str, Logistica] = {}
    lista = sorted(numeros)
    for i in range(0, len(lista), 1000):
        linhas = (
            await session.execute(
                select(Logistica)
                .where(Logistica.pedido_bling.in_(lista[i : i + 1000]))
                .order_by(Logistica.created_at)
            )
        ).scalars()
        for lg in linhas:
            achadas[str(lg.pedido_bling)] = lg  # a mais nova vence
    faltam = [m for m, p in mkts.items() if p not in achadas]
    for i in range(0, len(faltam), 1000):
        linhas = (
            await session.execute(
                select(Logistica)
                .where(Logistica.pedido_marketplace.in_(faltam[i : i + 1000]))
                .order_by(Logistica.created_at)
            )
        ).scalars()
        for lg in linhas:
            achadas[mkts[str(lg.pedido_marketplace)]] = lg
    return achadas


async def resolver_entrega(
    session: AsyncSession, pedido_bling: str, pedido_mkt: str | None
) -> Entrega | None:
    lgs = await logisticas_dos_pedidos(session, [(pedido_bling, pedido_mkt)])
    return entrega_da_logistica(lgs.get(pedido_bling))


def aplicar_entrega(g: Garantia, entrega: Entrega | None) -> None:
    if entrega is None:
        g.data_inicio = g.fim_hardware = g.fim_software = None
        g.entrega_origem = None
        g.entrega_em = None
        return
    g.data_inicio = entrega.dia
    g.fim_hardware, g.fim_software = calcular_prazos(entrega.dia)
    g.entrega_origem = entrega.origem
    g.entrega_em = entrega.em


def _br(d: date | None) -> str | None:
    return d.strftime("%d/%m/%Y") if d else None


async def recalcular(
    session: AsyncSession,
    g: Garantia,
    entrega: Entrega | None,
    *,
    ator: User | None,
    agora: datetime | None = None,
) -> str | None:
    """Confere a entrega de uma garantia contra a fonte (ponto 5).

    Devolve 'encontrada' (estava Aguardando e a data apareceu), 'corrigida'
    (a data mudou na origem) ou None. Fonte que PERDEU a data (o TikTok
    passou de DELIVERED para COMPLETED e o carimbo mudou de campo) não apaga a
    data que a garantia já tem: perder a fonte não é correção."""
    g.entrega_verificada_em = agora or datetime.now(UTC)
    if entrega is None:
        return None
    if g.data_inicio is None:
        resultado = "encontrada"
    elif entrega.dia != g.data_inicio:
        if not RECALCULAR_QUANDO_ENTREGA_MUDAR:
            return None
        if CONFIANCA_ORIGEM.get(entrega.origem, 9) > CONFIANCA_ORIGEM.get(g.entrega_origem, 9):
            return None  # fonte pior não corrige fonte melhor
        resultado = "corrigida"
    else:
        # Mesmo dia: só melhora a origem/instante, sem mexer nos prazos.
        melhor = CONFIANCA_ORIGEM.get(entrega.origem, 9) <= CONFIANCA_ORIGEM.get(
            g.entrega_origem, 9
        )
        if melhor and (g.entrega_origem, g.entrega_em) != (entrega.origem, entrega.em):
            g.entrega_origem, g.entrega_em = entrega.origem, entrega.em
        return None
    anterior = g.data_inicio
    aplicar_entrega(g, entrega)
    detalhe = (
        f"data de entrega encontrada — {ORIGEM_ROTULOS.get(entrega.origem, entrega.origem)}"
        if resultado == "encontrada"
        else "data de entrega corrigida na origem — "
        f"{ORIGEM_ROTULOS.get(entrega.origem, entrega.origem)}; prazos recalculados"
    )
    registrar_log(
        session,
        g.id,
        "recalculou",
        ator,
        campo="data_inicio",
        anterior=_br(anterior),
        novo=_br(g.data_inicio),
        detalhe=detalhe,
    )
    return resultado


async def recalcular_todas(session: AsyncSession) -> dict[str, int]:
    """O robô de hora em hora: relê a entrega de toda garantia e recalcula
    a que mudou (ponto 5). Uma leitura da Logística por lote de 1000."""
    garantias = list((await session.execute(select(Garantia).order_by(Garantia.id))).scalars())
    lgs = await logisticas_dos_pedidos(
        session, [(g.pedido_bling, g.pedido_marketplace) for g in garantias]
    )
    agora = datetime.now(UTC)
    contagem = {"conferidas": len(garantias), "encontradas": 0, "corrigidas": 0}
    for g in garantias:
        r = await recalcular(
            session, g, entrega_da_logistica(lgs.get(g.pedido_bling)), ator=None, agora=agora
        )
        if r == "encontrada":
            contagem["encontradas"] += 1
        elif r == "corrigida":
            contagem["corrigidas"] += 1
    return contagem


# ── Log (§6) ────────────────────────────────────────────────────────────────


def nome_pessoa(u: User | None) -> str:
    return ((u.name or u.email) if u else None) or "—"


def registrar_log(
    session: AsyncSession,
    garantia_id: int,
    acao: str,
    ator: User | None,
    *,
    campo: str | None = None,
    anterior: str | None = None,
    novo: str | None = None,
    detalhe: str | None = None,
) -> None:
    session.add(
        GarantiaLog(
            garantia_id=garantia_id,
            acao=acao,
            campo=campo,
            valor_anterior=anterior,
            valor_novo=novo,
            detalhe=detalhe,
            user_id=ator.id if ator else None,
            user_nome=nome_pessoa(ator) if ator else ROTULO_SISTEMA,
        )
    )


# ── Pedido (busca, pré-preenchimento) ───────────────────────────────────────


@dataclass
class NotaDoPedido:
    numero: str
    serie: str
    chave: str
    emitente_cnpj: str | None
    destinatario_doc: str | None
    destinatario_nome: str | None
    emitida_em: datetime | None
    valor: float | None
    papel: str  # 'produto' | 'embalagem' | 'indefinida'


@dataclass
class PedidoInfo:
    numero: str
    numeroloja: str | None
    loja: str | None
    plataforma: str | None
    conta: str | None
    data: datetime | None
    situacao: str | None
    total: float | None
    itens: list[dict] = field(default_factory=list)
    notas: list[NotaDoPedido] = field(default_factory=list)
    documento: str | None = None  # bling_orders.documento_destinatario
    nome_destinatario: str | None = None
    cpf_nota_emitida: str | None = None  # bling_notas_emitidas (jan–ago/2026)
    situacao_id: str | None = None  # bling_orders.situacao (o id)

    @property
    def produto_uranyx(self) -> bool:
        return any(i.get("uranyx") for i in self.itens)

    @property
    def entregue_no_bling(self) -> bool:
        return str(self.situacao_id or "") in SITUACOES_BLING_ENTREGUE

    @property
    def aviso_sem_entrega(self) -> str:
        """O aviso de quando não há data de entrega: o pedido ainda não chegou
        ou já chegou (no Bling) e o DaVinci não tem a data."""
        return "entregue_sem_data" if self.entregue_no_bling else "aguardando_entrega"

    @property
    def nota_produto(self) -> NotaDoPedido | None:
        return next((n for n in self.notas if n.papel == "produto"), None)

    @property
    def cpf(self) -> str | None:
        """CPF do pedido: o da NF de produto; senão o do contato no Bling;
        senão o da nota emitida casada pelo nº do marketplace."""
        candidatos = [
            self.nota_produto.destinatario_doc if self.nota_produto else None,
            self.documento,
            self.cpf_nota_emitida,
        ]
        for c in candidatos:
            d = so_digitos(c)
            if len(d) == 11 and cpf_valido(d):
                return d
        return None

    @property
    def nome(self) -> tuple[str | None, str | None]:
        """(nome sugerido, origem): o da NF de produto (comprador fiscal) ou o
        destinatário da entrega (pode não ser o comprador)."""
        nota = self.nota_produto
        if nota and normalizar_nome(nota.destinatario_nome):
            return normalizar_nome(nota.destinatario_nome), "nf"
        if normalizar_nome(self.nome_destinatario):
            return normalizar_nome(self.nome_destinatario), "destinatario_entrega"
        return None, None


def _papel_da_nota(valor: Any, total: Any) -> str:
    """A regra das duas notas do Pós Vendas (`pos_vendas._eh_embalagem`):
    ≥ 60% do pedido = produto, ≤ 30% = embalagem."""
    try:
        frac = float(valor) / float(total)
    except (TypeError, ValueError, ZeroDivisionError):
        return "indefinida"
    if frac >= _FRACAO_PRODUTO_MIN:
        return "produto"
    if frac <= _FRACAO_EMBALAGEM_MAX:
        return "embalagem"
    return "indefinida"


async def buscar_pedido(session: AsyncSession, termo: str) -> PedidoInfo | None:
    """O pedido pelo nº do Bling OU do marketplace (como `chamados.lookup_pedido`)."""
    termo = (termo or "").strip()
    if not termo:
        return None
    primeiro = (
        await session.execute(
            select(BlingOrder.numero)
            .where(
                or_(BlingOrder.numero == termo, BlingOrder.numeroloja == termo),
                BlingOrder.numero.is_not(None),
            )
            .order_by(BlingOrder.data.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    if primeiro is None:
        return None
    linhas = list(
        (
            await session.execute(
                select(BlingOrder)
                .where(BlingOrder.numero == primeiro)
                .order_by(BlingOrder.item_index)
            )
        ).scalars()
    )
    cab = linhas[0]
    itens = [
        {
            "descricao": r.item_descricao,
            "sku": r.item_codigo,
            "quantidade": r.item_quantidade,
            "uranyx": e_uranyx(r.item_codigo),
        }
        for r in linhas
        if r.item_descricao or r.item_codigo
    ]
    loja_info = None
    if cab.loja:
        loja_info = (
            await session.execute(
                select(StoreInfo.platform, StoreInfo.account_name)
                .where(StoreInfo.bling_store_id == str(cab.loja))
                .limit(1)
            )
        ).first()
    situacao = None
    if cab.situacao:
        situacao = (
            await session.execute(
                select(SituacaoBling.nome).where(cast(SituacaoBling.id, Text) == cab.situacao)
            )
        ).scalar_one_or_none() or cab.situacao
    total = next((r.total for r in linhas if r.total is not None), None)
    notas = [
        NotaDoPedido(
            numero=normalizar_nf(n.numero),
            serie=normalizar_serie(n.serie),
            chave=n.chave,
            emitente_cnpj=so_digitos(n.emitente_cnpj) or None,
            destinatario_doc=so_digitos(n.destinatario_doc) or None,
            destinatario_nome=n.destinatario_nome,
            emitida_em=n.data_emissao,
            valor=float(n.valor) if n.valor is not None else None,
            papel=_papel_da_nota(n.valor, total),
        )
        for n in (
            await session.execute(
                select(NfNota)
                .where(NfNota.pedido_bling == primeiro)
                .order_by(NfNota.data_emissao.desc().nulls_last())
            )
        ).scalars()
        if normalizar_nf(n.numero)
    ]
    cpf_nota_emitida = None
    if cab.numeroloja:
        cpf_nota_emitida = (
            await session.execute(
                select(BlingNotaEmitida.cpf_dest)
                .where(
                    BlingNotaEmitida.complemento == cab.numeroloja,
                    BlingNotaEmitida.cpf_dest.is_not(None),
                )
                .order_by(BlingNotaEmitida.data_emissao.desc().nulls_last())
                .limit(1)
            )
        ).scalar_one_or_none()
    return PedidoInfo(
        numero=str(primeiro),
        numeroloja=cab.numeroloja,
        loja=str(cab.loja) if cab.loja else None,
        plataforma=loja_info[0] if loja_info else None,
        conta=loja_info[1] if loja_info else None,
        data=cab.data,
        situacao=situacao,
        total=float(total) if total is not None else None,
        itens=itens,
        notas=notas,
        documento=cab.documento_destinatario,
        nome_destinatario=cab.nome_destinatario,
        cpf_nota_emitida=cpf_nota_emitida,
        situacao_id=str(cab.situacao) if cab.situacao else None,
    )


def nota_informada(pedido: PedidoInfo, numero: str, serie: str) -> NotaDoPedido | None:
    """A NF do pedido que tem o número (e a série, se veio) informado."""
    for n in pedido.notas:
        if n.numero == numero and (not serie or n.serie == serie):
            return n
    return None


def avisos_do_cadastro(
    pedido: PedidoInfo,
    cpf: str,
    nota: NotaDoPedido | None,
    *,
    outras_do_pedido: int = 0,
) -> list[str]:
    """Avisos que NÃO bloqueiam (a pessoa confere e segue). `outras_do_pedido`
    = garantias que o pedido JÁ tem em outra NF (ex.: cadastraram a NF de
    embalagem e agora a de produto — o mesmo aparelho com duas garantias)."""
    avisos: list[str] = []
    if not pedido.produto_uranyx:
        avisos.append("pedido_sem_produto_uranyx")
    if outras_do_pedido:
        avisos.append("pedido_ja_tem_garantia")
    cpf_pedido = pedido.cpf
    if cpf_pedido and cpf_pedido != cpf:
        avisos.append("cpf_diferente_do_pedido")
    if pedido.notas and nota is None:
        avisos.append("nf_nao_e_do_pedido")
    elif nota is not None and nota.papel == "embalagem":
        avisos.append("nf_de_embalagem")
    return avisos


# ── Atendimento (§5) ────────────────────────────────────────────────────────


def _iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _momento(m: AtendimentoMensagem) -> datetime:
    q = m.enviada_em or m.created_at
    return q if q.tzinfo else q.replace(tzinfo=UTC)


async def mensagens_para_copiar(
    session: AsyncSession, conversa: AtendimentoConversa, ids: list[UUID] | None
) -> list[AtendimentoMensagem] | None:
    """As mensagens que vão para a garantia, em ordem. Com `ids`, exatamente
    essas (None se alguma não é da conversa); sem, as mais recentes, sem as
    mensagens de sistema (cartões de status da plataforma)."""
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    if ids:
        msgs = list(
            (
                await session.execute(
                    select(AtendimentoMensagem).where(
                        AtendimentoMensagem.conversa_id == conversa.id,
                        AtendimentoMensagem.id.in_(ids),
                    )
                )
            ).scalars()
        )
        if len(msgs) != len(set(ids)):
            return None
        return sorted(msgs, key=_momento)
    msgs = list(
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.autor != "sistema",
                )
                .order_by(momento.desc(), AtendimentoMensagem.created_at.desc())
                .limit(MAX_MENSAGENS_COPIADAS)
            )
        ).scalars()
    )
    return list(reversed(msgs))


def inicio_do_trecho(msgs: list[AtendimentoMensagem]) -> datetime | None:
    """A 1ª mensagem do cliente no TRECHO ATUAL da conversa: o trecho termina
    na última mensagem do cliente e volta enquanto a pausa entre uma mensagem
    e a anterior for de até DIAS_PAUSA_NOVO_ATENDIMENTO dias (a resposta da
    loja no meio mantém o trecho vivo). None = nenhuma mensagem do cliente."""
    ordem = sorted(msgs, key=_momento)
    ultima = max((i for i, m in enumerate(ordem) if m.autor == "cliente"), default=None)
    if ultima is None:
        return None
    pausa = timedelta(days=DIAS_PAUSA_NOVO_ATENDIMENTO)
    inicio = ultima
    while inicio > 0 and _momento(ordem[inicio]) - _momento(ordem[inicio - 1]) <= pausa:
        inicio -= 1
    return next(_momento(m) for m in ordem[inicio : ultima + 1] if m.autor == "cliente")


def data_do_atendimento(
    conversa: AtendimentoConversa,
    msgs: list[AtendimentoMensagem],
    escolhidas: bool,
    agora: datetime,
) -> datetime:
    """Data/hora do atendimento = relógio da plataforma, não o do clique.

    Mensagens escolhidas: a 1ª do cliente entre elas (quando ele pediu
    ajuda). Sem escolha: a 1ª do cliente no trecho atual da conversa
    (`inicio_do_trecho`) — NÃO a última: a reclamação aberta dentro do prazo
    não vira "Fora da garantia" porque o cliente mandou o vídeo depois do fim."""
    do_cliente = [_momento(m) for m in msgs if m.autor == "cliente"]
    if escolhidas:
        if do_cliente:
            return min(do_cliente)
        if msgs:
            return _momento(msgs[0])
        return agora
    inicio = inicio_do_trecho(msgs)
    if inicio is not None:
        return inicio
    for c in (conversa.ultima_do_cliente_em, conversa.ultima_mensagem_em):
        if c:
            return c if c.tzinfo else c.replace(tzinfo=UTC)
    return agora


def copia_das_mensagens(msgs: list[AtendimentoMensagem]) -> list[dict]:
    return [
        {
            "id": str(m.id),
            "autor": m.autor,
            "origem": m.origem,
            "tipo": m.tipo,
            "texto": (m.texto or "")[:MAX_TEXTO_MENSAGEM] or None,
            "enviada_em": _iso(_momento(m)),
            "anexos": list(m.anexos or []),
        }
        for m in msgs
    ]


def resumo_automatico(msgs: list[AtendimentoMensagem]) -> str:
    textos = [
        (m.texto or "").strip() for m in msgs if m.autor == "cliente" and (m.texto or "").strip()
    ]
    if not textos:
        return "(sem texto do cliente nas mensagens copiadas)"
    return " / ".join(textos[-3:])[:MAX_RESUMO_AUTOMATICO]


def anexos_das_mensagens(msgs: list[AtendimentoMensagem]) -> list[dict]:
    """Fotos, vídeos e arquivos das mensagens (sem os cartões de pedido/produto)."""
    saida: list[dict] = []
    for m in msgs:
        for a in m.anexos or []:
            if not isinstance(a, dict) or a.get("tipo") in _TIPOS_CARTAO:
                continue
            if a.get("url"):
                saida.append(
                    {
                        "mensagem_id": str(m.id),
                        "tipo": str(a.get("tipo") or "arquivo"),
                        "url": str(a["url"]),
                        "nome": a.get("nome"),
                    }
                )
            elif a.get("arquivo") or a.get("nome"):
                # Mercado Livre: só o nome do arquivo, sem link.
                saida.append(
                    {
                        "mensagem_id": str(m.id),
                        "tipo": str(a.get("tipo") or "arquivo"),
                        "url": None,
                        "nome": a.get("nome") or a.get("arquivo"),
                    }
                )
    return saida


def host_permitido(url: str) -> bool:
    try:
        partes = urlsplit(url)
    except ValueError:
        return False
    host = (partes.hostname or "").lower()
    if partes.scheme != "https" or not host:
        return False
    return any(host == h or host.endswith("." + h) for h in ANEXO_HOSTS)


class AnexoRecusado(Exception):  # noqa: N818 — motivo de tela, não erro de programa
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def tipo_de_anexo_aceito(tipo: str) -> bool:
    """Foto, vídeo ou PDF — e nunca imagem que é documento (SVG)."""
    t = (tipo or "").lower()
    return t.startswith(ANEXO_TIPOS) and not any(x in t for x in ANEXO_TIPOS_RECUSADOS)


async def _ler_anexo(resp: httpx.Response) -> tuple[bytes, str]:
    if resp.status_code != 200:
        raise AnexoRecusado(
            "link_expirado" if resp.status_code in (403, 404, 410) else "erro_ao_baixar"
        )
    tipo = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not tipo_de_anexo_aceito(tipo):
        raise AnexoRecusado("tipo_nao_suportado")
    tamanho = int(resp.headers.get("content-length") or 0)
    if tamanho > ANEXO_MAX_BYTES:
        raise AnexoRecusado("maior_que_8mb")
    corpo = bytearray()
    async for parte in resp.aiter_bytes():
        corpo.extend(parte)
        if len(corpo) > ANEXO_MAX_BYTES:
            raise AnexoRecusado("maior_que_8mb")
    return bytes(corpo), tipo


async def baixar_anexo(
    url: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[bytes, str]:
    """Baixa um anexo do CDN da plataforma. Levanta AnexoRecusado com o
    motivo (host fora da lista, grande demais, tipo que não é mídia, erro).
    Redirect é seguido À MÃO (até ANEXO_MAX_REDIRECTS), conferindo o host do
    destino ANTES de pedir: um Location para host interno nunca é visitado.
    `transport` é dos testes."""
    if not host_permitido(url):
        raise AnexoRecusado("host_nao_permitido")
    try:
        async with httpx.AsyncClient(
            timeout=ANEXO_TIMEOUT_S, follow_redirects=False, transport=transport
        ) as client:
            atual = url
            for _salto in range(ANEXO_MAX_REDIRECTS + 1):
                async with client.stream("GET", atual) as resp:
                    if not resp.is_redirect:
                        return await _ler_anexo(resp)
                    atual = str(resp.url.join(resp.headers.get("location") or ""))
                if not host_permitido(atual):
                    raise AnexoRecusado("host_nao_permitido")
            raise AnexoRecusado("erro_ao_baixar")  # redirect demais
    except AnexoRecusado:
        raise
    except httpx.HTTPError as e:
        logger.warning("garantia_anexo_falhou", err=type(e).__name__)
        raise AnexoRecusado("erro_ao_baixar") from e


async def baixar_anexos(itens: list[dict]) -> list[tuple[dict, bytes | None, str | None]]:
    """Baixa o que dá (até ANEXOS_MAX_POR_ATENDIMENTO e ANEXOS_MAX_BYTES_TOTAL).
    Cada item ganha `baixado` e, quando não baixou, `motivo`."""
    saida: list[tuple[dict, bytes | None, str | None]] = []
    baixados = 0
    total = 0
    for item in itens:
        item = dict(item)
        blob = tipo = None
        if not item.get("url"):
            item["motivo"] = "sem_link"
        elif baixados >= ANEXOS_MAX_POR_ATENDIMENTO:
            item["motivo"] = "limite_de_anexos"
        else:
            try:
                blob, tipo = await baixar_anexo(item["url"])
                if total + len(blob) > ANEXOS_MAX_BYTES_TOTAL:
                    blob = tipo = None
                    item["motivo"] = "limite_de_anexos"
                else:
                    baixados += 1
                    total += len(blob)
            except AnexoRecusado as e:
                item["motivo"] = e.motivo
        item["baixado"] = blob is not None
        saida.append((item, blob, tipo))
    return saida


async def garantias_do_cpf_ou_pedido(
    session: AsyncSession, cpf: str | None, pedido_bling: str | None, filtro_escopo
) -> list[Garantia]:
    conds = []
    if cpf:
        conds.append(Garantia.cpf == cpf)
    if pedido_bling:
        conds.append(Garantia.pedido_bling == pedido_bling)
    if not conds:
        return []
    q = select(Garantia).where(or_(*conds))
    if filtro_escopo is not None:
        q = q.where(filtro_escopo)
    return list((await session.execute(q.order_by(Garantia.id.desc()).limit(20))).scalars())
