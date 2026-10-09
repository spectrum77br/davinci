"""Leitura parada do /atendimento — a loja que deveria estar lendo e não lê.

Checagem geral de 05/10/2026, antes de sair do Duoke: nada avisava quando uma
leitura parava. A Temu ficou sem ler desde 01/10 sem ninguém saber, o Lucas MEI
está com o token vencido desde 04/09 e o ML Poofy nunca leu.

## Onde avisa: no próprio /atendimento

Decisão do Eduardo (05/10/2026): o aviso NÃO vai para a Ouvidoria — "já avisa
ali no próprio atendimento". O resumo da tela (`GET /api/atendimento/resumo`,
que a Caixa já carrega e recarrega a cada 30 s) traz a lista
(`leitura_parada`): a Caixa mostra a faixa vermelha "N lojas sem ler" (loja,
plataforma, há quanto tempo, motivo curto) com o link para a aba "Lojas e
modo", e a aba ganha a marca com a contagem. Só quem vê o /atendimento vê: a
trava é a da própria rota (só admin, e só quem está em `ATENDIMENTO_USUARIOS`).

## Só leitura: o retrato de agora

Este módulo não grava nada e não chama plataforma nenhuma: lê
`atendimento_canais` (com a integração) e os dois carimbos das rodadas sem
caixa no Redis — quem grava os carimbos é o worker (`carimbar_rodada`,
`esquecer_rodada`). Sem ocorrência, sem Threema e sem histerese: a loja que
volta a ler sai da lista na recarga seguinte. A Magalu, que falha à noite, não
pisca porque o limite dela (6 h) já passa a noite medida.

## O que ele olha

`atendimento_canais.ultimo_ok_em` — a última leitura COM SUCESSO de cada caixa,
gravada por quem lê:

- loja por API (Shopee, ML, TikTok, Amazon pelo Gmail, Magalu): o cron do sync,
  a cada 2 min (`atendimento/sync.py`);
- loja do robô do Mac mini (Temu, AliExpress): o pulso "lendo", a cada ~1 min
  (`atendimento/robo.py`);
- carrinho dos sites: a cada 30 min (`atendimento/carrinhos.py`);
- comentários das redes: a cada 15 min (`atendimento/redes.py`);
- caixa de e-mail da EMPRESA com a ponte ligada (08/10/2026): o sinal do
  agente do Mac na Central de e-mail (`mail_mailboxes.last_seen_at`, a cada
  ≤ 3 min) — 30 min sem sinal, ou o agente pedindo login/avisando erro; o
  Mac com sinal mas sem LER há 30 min; ou a ponte do worker parada com
  e-mail esperando há 15 min (`mail_atendimento/saude.caixas_paradas`). A
  caixa PRIVADA nunca entra (todo o /atendimento vê esta lista).

E as duas rodadas que não têm caixa — reclamações (10 min) e avaliações
(30 min) —, pelo carimbo no Redis: o último sucesso e a primeira vez que a
rodada rodou ligada (carência: carimbo que nunca existiu — Redis reiniciado,
primeira subida — não acusa nada até a rodada rodar).

O que está desligado de propósito fica de fora: o interruptor do .env
(`ATENDIMENTO_LEITURA_ATIVA`, o de cada rodada, carrinho e redes), o robô sem
token, a caixa `desligado` e a integração arquivada.

## O limite é pelo ritmo de cada leitura (`LIMITES`)

    loja por API 30 min · Magalu 6 h · robô 60 min · redes 60 min ·
    carrinho 2 h · reclamações 60 min · avaliações 2 h · e-mail 30 min

Medido em produção em 05/10 (13 h de log do worker): nenhuma loja por API
passou de 6 min sem ler; o robô, 31 min (o perfil do AliExpress aberto por
alguém às 07:19); a Magalu Poofy, que falha ~90% à noite (o proxy sai pelo Mac
do dono), ficou 4 h 12 (o log começa no meio dessa), 3 h 30 e 2 h 42 sem
nenhuma leitura boa. Com 30 min ela apareceria toda noite; 6 h passa a noite
medida.

## Uma linha por loja

- loja por API: as caixas da MESMA conta (ML pergunta + pós-venda; Magalu
  pergunta + chat + SAC) viram UMA linha (`loja:<integration_id>`) — o token,
  a permissão, o proxy são um só —, então o ML Poofy aparece uma vez, não duas;
- loja do robô, site e conta de rede: uma linha por caixa (`canal:<id>`); as
  rodadas, `rodada:<nome>`;
- a leitura inteira parada vira UMA linha geral em vez de uma por loja:
  `geral:lojas` quando nenhuma loja pela API lê (o worker caiu — e com ele o
  carrinho, as redes e as rodadas), `geral:robo` quando o robô do Mac mini não
  dá sinal de loja nenhuma. Junto dela só ficam as que JÁ estavam paradas antes
  de tudo parar (a última leitura boa delas é mais velha que a última leitura
  de qualquer loja, além do limite delas): o Temu sem login continua na lista;
  as 25 lojas que pararam junto, não.

Texto de comprador nunca entra: só nome da loja, caixa, horário e o
`ultimo_erro` do canal (texto de operação — código/HTTP —, o mesmo que a aba
"Lojas e modo" já mostra).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, Integration
from app.redis_client import redis
from app.services.atendimento import canais_externos, lojas
from app.services.atendimento import robo as robo_atd
from app.services.atendimento.constantes import (
    PLATAFORMA_SITE,
    PLATAFORMAS,
    PLATAFORMAS_REDE,
    PLATAFORMAS_ROBO,
    STATUS_CANAL_PARADO,
    STATUS_CANAL_SEM_ENDPOINT,
    STATUS_CANAL_SESSAO_CAIU,
)

logger = structlog.get_logger()

# Carimbos das rodadas SEM caixa (o worker grava, a tela lê): o último sucesso
# e a primeira vez que a rodada rodou ligada (a carência). Separados pelo mesmo
# motivo do vigia da Logística (worker._LOGISTICA_VISTO_KEY): gravar "agora" no
# carimbo de sucesso faria uma rodada que NUNCA terminou parecer saudável.
CHAVE_OK = "davinci:atendimento:ultimo_ok"
CHAVE_VISTO = "davinci:atendimento:primeira_observacao"

# Status gravados em `atendimento_canais.status` (vocabulário de `constantes`).
STATUS_DESLIGADO = "desligado"
STATUS_SEM_ESCOPO = "sem_escopo"
STATUS_ERRO = "erro"
# O código que o adaptador do ML/Magalu grava quando o refresh falha
# (`sync.ERRO_TOKEN_NAO_RENOVOU` — copiado para não puxar o sync inteiro).
ERRO_TOKEN_NAO_RENOVOU = "token_nao_renovou"  # noqa: S105 — código de erro, não segredo

# ── Ritmo de cada tipo de leitura ─────────────────────────────────────────

CLASSE_LOJA = "loja"  # Shopee, ML, TikTok, Amazon (Gmail): o sync, a cada 2 min
CLASSE_MAGALU = "magalu"  # o MESMO sync, mas a leitura falha à noite (proxy)
CLASSE_ROBO = "robo"  # Temu, AliExpress: o pulso do robô do Mac mini
CLASSE_SITE = "site"  # carrinho dos sites, a cada 30 min
CLASSE_REDES = "redes"  # comentários do Instagram/Facebook, a cada 15 min
RODADA_RECLAMACOES = "reclamacoes"  # a cada 10 min
RODADA_AVALIACOES = "avaliacoes"  # a cada 30 min

# Quanto tempo sem ler vira "sem ler" (medido em produção em 05/10/2026 — ver
# o docstring do módulo).
LIMITES: dict[str, timedelta] = {
    CLASSE_LOJA: timedelta(minutes=30),
    CLASSE_MAGALU: timedelta(hours=6),
    CLASSE_ROBO: timedelta(minutes=60),
    CLASSE_SITE: timedelta(hours=2),
    CLASSE_REDES: timedelta(minutes=60),
    RODADA_RECLAMACOES: timedelta(minutes=60),
    RODADA_AVALIACOES: timedelta(hours=2),
}

# A linha da tela.
TIPO_LOJA = "loja"
TIPO_GERAL = "geral"
TIPO_RODADA = "rodada"
PLATAFORMA_INTERNO = "interno"


@dataclass(frozen=True)
class LeituraParada:
    """Uma linha da faixa "lojas sem ler" (o `LeituraParadaOut` da API)."""

    # `loja:<integration_id>` | `canal:<id>` | `rodada:<nome>` | `geral:lojas` | `geral:robo`
    chave: str
    tipo: str  # TIPO_LOJA | TIPO_GERAL | TIPO_RODADA
    # O código da plataforma (shopee, temu, site…); "interno" na geral e nas rodadas.
    plataforma: str
    loja: str
    # Curto, para a faixa ("sem permissão", "sessão caiu"…).
    motivo: str
    # O que fazer (o title da faixa).
    acao: str
    # A última leitura boa; sem nenhuma, desde quando deveria estar lendo
    # (a criação da caixa; na rodada, a primeira vez que rodou ligada).
    desde: datetime | None
    nunca_leu: bool
    minutos: int
    limite_min: int
    integration_id: UUID | None = None
    canal_id: UUID | None = None
    # As caixas paradas da loja, quando ela tem mais de uma (ML, Magalu).
    caixas: tuple[str, ...] = ()
    # O erro de operação da caixa (código/HTTP), quando há.
    detalhe: str | None = None


def classe_do_canal(canal: AtendimentoCanal) -> str | None:
    """Tipo de leitura do canal (pela origem dele), ou None se não é vigiado."""
    if canal.robo_perfil_id is not None:
        return CLASSE_ROBO if canal.plataforma in PLATAFORMAS_ROBO else None
    if canal.integration_id is not None:
        if canal.plataforma not in PLATAFORMAS:
            return None
        return CLASSE_MAGALU if canal.plataforma == "magalu" else CLASSE_LOJA
    if canal.externo_ref:
        if canal.plataforma == PLATAFORMA_SITE:
            return CLASSE_SITE
        if canal.plataforma in PLATAFORMAS_REDE:
            return CLASSE_REDES
    return None


@dataclass(frozen=True)
class Idade:
    """Há quanto tempo a caixa não lê com sucesso."""

    parado: bool
    # A última leitura boa; sem nenhuma, a criação do canal (é desde quando
    # ele deveria estar lendo).
    desde: datetime | None
    nunca_leu: bool
    minutos: int


def medir(
    ultimo_ok_em: datetime | None,
    criado_em: datetime | None,
    agora: datetime,
    limite: timedelta,
) -> Idade:
    """Puro: a caixa passou do limite sem ler?"""
    ok = _utc(ultimo_ok_em)
    desde = ok or _utc(criado_em)
    if desde is None:
        return Idade(parado=False, desde=None, nunca_leu=ok is None, minutos=0)
    return Idade(
        parado=agora - desde > limite,
        desde=desde,
        nunca_leu=ok is None,
        minutos=_minutos(desde, agora),
    )


# ── Textos (linguagem de operação, sem dado de comprador) ─────────────────

ROTULO_CANAL = {
    "chat": "Chat",
    "pergunta": "Perguntas",
    "pos_venda": "Pós-venda",
    "email": "E-mail (Gmail)",
    "sac": "SAC",
    "carrinho": "Carrinho abandonado",
    "comentario": "Comentários",
}
RODADAS: dict[str, tuple[str, str]] = {
    # rodada → (nome na tela, interruptor em Settings)
    RODADA_RECLAMACOES: ("Reclamações e devoluções", "atendimento_reclamacoes_ativa"),
    RODADA_AVALIACOES: ("Avaliações de venda", "atendimento_avaliacoes_ativa"),
}
NOME_GERAL_LOJAS = "Leitura das lojas"
NOME_GERAL_ROBO = "Robô do Mac mini (Temu e AliExpress)"

MOTIVO_SEM_ESCOPO = "sem permissão"
MOTIVO_TOKEN = "token não renova"  # noqa: S105 — texto da tela, não segredo
MOTIVO_ERRO = "erro na leitura"
# A caixa sem erro gravado e sem leitura boa: a rodada não passou por ela.
MOTIVO_NAO_RODOU = "a leitura não passou por ela"
MOTIVO_ROBO_SEM_SINAL = "robô sem sinal"
MOTIVO_ROBO_SESSAO = "sessão caiu no AdsPower"
MOTIVO_ROBO_ERRO = "erro no robô"
MOTIVO_SITE_SEM_ROTA = "rota do carrinho não publicada"
MOTIVO_SITE_TOKEN = "o site recusou o token"  # noqa: S105 — texto da tela
MOTIVO_REDE_SEM_ESCOPO = "token sem permissão"
MOTIVO_GERAL_LOJAS = "nenhuma loja lê"
MOTIVO_GERAL_ROBO = "Mac mini sem sinal"
MOTIVO_RODADA = "rodada não termina lendo"

# Sem permissão (`sem_escopo`): o que falta depende da plataforma.
ACAO_SEM_ESCOPO = {
    "tiktok": (
        "Autorizar o app \"DaVinci Atendimento\" da loja com Atendimento ao "
        "Cliente (pelo AdsPower, como nas outras lojas TikTok) e reconectar em "
        "Integrações"
    ),
    "ml": (
        "Ver no Mercado Livre se a conta tem restrição (403 \"por política\") e "
        "reautorizar o app em Integrações"
    ),
}
ACAO_SEM_ESCOPO_PADRAO = "Reautorizar a loja em Integrações com a permissão que falta"
ACAO_RECONECTAR = "Reconectar a conta em Integrações: o token não renova mais sozinho"
ACAO_MAGALU = (
    "A leitura da Magalu sai pelo proxy do Mac do dono: ver se esse Mac dormiu, "
    "desligou ou ficou sem internet"
)
ACAO_AMAZON = "Ver a caixa do Gmail da Amazon (login IMAP): o erro aparece em Lojas e modo"
ACAO_LOJA = (
    "Ver o erro da loja em Lojas e modo: se for da plataforma (5xx, timeout), "
    "esperar; se não voltar, reconectar a conta em Integrações"
)
ACAO_ROBO_SEM_SINAL = (
    "Ver se o Mac mini está ligado e acordado, com o AdsPower aberto e o robô do "
    "atendimento rodando"
)
ACAO_ROBO_SESSAO = (
    "Entrar de novo no Seller Center no perfil da loja no AdsPower (Mac mini) — o "
    "robô nunca digita senha"
)
ACAO_ROBO_ERRO = (
    "Ver o perfil da loja no AdsPower (Mac mini): aberto por alguém, numa conversa "
    "ou noutra página — o motivo aparece em Lojas e modo"
)
ACAO_SITE = (
    "Ver a rota de leitura do carrinho no site (o pacote do carrinho publicado na "
    "Hostinger) e o token do estoque do site"
)
ACAO_REDES = (
    "Ver o token do DaVinci Publicador em Redes Sociais (e o escopo de "
    "comentários): o erro aparece em Lojas e modo"
)
ACAO_GERAL_LOJAS = (
    "Ver se o worker do DaVinci está no ar e com a leitura ligada — enquanto isso "
    "nenhuma mensagem nova de loja nenhuma entra no atendimento"
)
ACAO_GERAL_ROBO = (
    "Ver se o Mac mini está ligado e acordado e o robô do atendimento rodando "
    "(AdsPower aberto) — enquanto isso Temu e AliExpress não entram no atendimento"
)
ACAO_RODADA = (
    "Ver o log do worker (atendimento_{nome}_falhou) — enquanto isso o que é novo "
    "não entra no atendimento"
)
# A caixa de e-mail da empresa (a Central de e-mail, lida pelo agente do Mac).
PLATAFORMA_EMAIL = "email"
ROTULO_CAIXA_EMAIL = "E-mail"
ACAO_CAIXA_EMAIL = (
    "Ver o agente do Mac mini desta caixa (aba E-mail › Saúde): ligado, com a sessão do "
    "Tuta aberta e a chave da caixa certa — enquanto isso os e-mails das lojas não entram"
)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _minutos(inicio: datetime | None, agora: datetime) -> int:
    inicio = _utc(inicio)
    if inicio is None:
        return 0
    return max(0, int((agora - inicio).total_seconds() // 60))


def _limite_min(limite: timedelta) -> int:
    return int(limite.total_seconds() // 60)


def _epoch(valor: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(valor), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _texto_curto(texto: str | None, tamanho: int = 160) -> str | None:
    t = " ".join((texto or "").split())
    return t[:tamanho] or None


# ── As caixas ─────────────────────────────────────────────────────────────


@dataclass
class _Caixa:
    canal: AtendimentoCanal
    integration: Integration | None
    classe: str
    idade: Idade

    @property
    def rotulo(self) -> str:
        return ROTULO_CANAL.get(self.canal.canal, self.canal.canal)


def _entra(c: _Caixa, corte: datetime | None) -> bool:
    """A caixa vai para a lista? Parada — e, com a leitura inteira parada
    (`corte` = a última leitura de qualquer loja), só se já estava parada
    ANTES: a última leitura boa dela é mais velha que o corte, além do limite."""
    if not c.idade.parado:
        return False
    if corte is None or c.idade.desde is None:
        return True
    return corte - c.idade.desde > LIMITES[c.classe]


async def _caixas(session: AsyncSession, agora: datetime) -> list[_Caixa]:
    """As caixas que DEVERIAM estar lendo agora (o que está desligado de
    propósito fica de fora)."""
    s = get_settings()
    ligadas = {
        CLASSE_LOJA: s.atendimento_leitura_ativa,
        CLASSE_MAGALU: s.atendimento_leitura_ativa,
        # O robô manda pulso pela API com o token: sem token, ele está
        # desligado (o router devolve 404).
        CLASSE_ROBO: bool((s.atendimento_robo_token or "").strip()),
        CLASSE_SITE: s.atendimento_leitura_ativa and s.atendimento_carrinhos_ativa,
        CLASSE_REDES: s.atendimento_leitura_ativa and s.atendimento_redes_ativa,
    }
    if not any(ligadas.values()):
        return []
    linhas = (
        await session.execute(
            select(AtendimentoCanal, Integration)
            .outerjoin(Integration, Integration.id == AtendimentoCanal.integration_id)
            .order_by(AtendimentoCanal.plataforma, AtendimentoCanal.created_at)
        )
    ).all()
    out: list[_Caixa] = []
    for canal, integration in linhas:
        classe = classe_do_canal(canal)
        if classe is None or not ligadas.get(classe):
            continue
        if canal.status == STATUS_DESLIGADO:
            continue
        if canal.integration_id is not None and (
            integration is None or integration.archived_at is not None
        ):
            continue
        idade = medir(canal.ultimo_ok_em, canal.created_at, agora, LIMITES[classe])
        out.append(_Caixa(canal=canal, integration=integration, classe=classe, idade=idade))
    return out


def _geral_lojas(caixas: list[_Caixa], agora: datetime) -> LeituraParada | None:
    """A leitura das lojas pela API inteira parada (o cron do sync, logo o
    worker). A régua são as lojas de ritmo firme — sem a Magalu, que falha à
    noite por conta própria e acusaria "nenhuma loja lê" numa casa só com ela."""
    firmes = [c for c in caixas if c.classe == CLASSE_LOJA]
    lidas = [_utc(c.canal.ultimo_ok_em) for c in firmes if c.canal.ultimo_ok_em is not None]
    mais_recente = max(lidas) if lidas else None
    limite = LIMITES[CLASSE_LOJA]
    if len(firmes) < 2 or mais_recente is None or agora - mais_recente <= limite:
        return None
    return LeituraParada(
        chave="geral:lojas",
        tipo=TIPO_GERAL,
        plataforma=PLATAFORMA_INTERNO,
        loja=NOME_GERAL_LOJAS,
        motivo=MOTIVO_GERAL_LOJAS,
        acao=ACAO_GERAL_LOJAS,
        desde=mais_recente,
        nunca_leu=False,
        minutos=_minutos(mais_recente, agora),
        limite_min=_limite_min(limite),
        detalhe=f"{len(caixas)} caixas pela API; a leitura roda a cada 2 min",
    )


def _geral_robo(caixas: list[_Caixa], agora: datetime) -> LeituraParada | None:
    """O robô do Mac mini sem sinal de loja nenhuma (Mac desligado/dormindo)."""
    sinais = [s for c in caixas if (s := robo_atd.ultimo_sinal(c.canal)) is not None]
    sinal = max(sinais) if sinais else None
    limite = LIMITES[CLASSE_ROBO]
    if len(caixas) < 2 or sinal is None or agora - sinal <= limite:
        return None
    return LeituraParada(
        chave="geral:robo",
        tipo=TIPO_GERAL,
        plataforma=PLATAFORMA_INTERNO,
        loja=NOME_GERAL_ROBO,
        motivo=MOTIVO_GERAL_ROBO,
        acao=ACAO_GERAL_ROBO,
        desde=sinal,
        nunca_leu=False,
        minutos=_minutos(sinal, agora),
        limite_min=_limite_min(limite),
        detalhe=f"{len(caixas)} lojas pelo robô; ele pulsa a cada ~1 min",
    )


def _motivo_loja(paradas: list[_Caixa], plataforma: str) -> tuple[str, str]:
    """(motivo curto, ação) pela causa mais provável, olhando as caixas paradas."""
    if any(c.canal.status == STATUS_SEM_ESCOPO for c in paradas):
        return MOTIVO_SEM_ESCOPO, ACAO_SEM_ESCOPO.get(plataforma, ACAO_SEM_ESCOPO_PADRAO)
    if any(ERRO_TOKEN_NAO_RENOVOU in (c.canal.ultimo_erro or "") for c in paradas):
        return MOTIVO_TOKEN, ACAO_RECONECTAR
    if plataforma == "magalu":
        acao = ACAO_MAGALU
    elif plataforma == "amazon":
        acao = ACAO_AMAZON
    else:
        acao = ACAO_LOJA
    if any(c.canal.status == STATUS_ERRO for c in paradas):
        return MOTIVO_ERRO, acao
    return MOTIVO_NAO_RODOU, acao


async def _lojas_api(
    session: AsyncSession,
    caixas: list[_Caixa],
    corte: datetime | None,
    nomes: Mapping[UUID, str],
) -> list[LeituraParada]:
    """Lojas por API (o sync): UMA linha por integração."""
    por_loja: dict[UUID, list[_Caixa]] = defaultdict(list)
    for c in caixas:
        por_loja[c.canal.integration_id].append(c)
    out: list[LeituraParada] = []
    for integration_id, grupo in por_loja.items():
        paradas = sorted((c for c in grupo if _entra(c, corte)), key=lambda c: c.canal.canal)
        if not paradas:
            continue
        plataforma = grupo[0].canal.plataforma
        nome = nomes.get(integration_id) or await lojas.nome_da_loja(
            session, grupo[0].integration
        )
        # A loja parou quando parou a caixa que leu por último.
        mais_nova = min(paradas, key=lambda c: c.idade.minutos)
        motivo, acao = _motivo_loja(paradas, plataforma)
        erro = next((e for c in paradas if (e := _texto_curto(c.canal.ultimo_erro))), None)
        out.append(
            LeituraParada(
                chave=f"loja:{integration_id}",
                tipo=TIPO_LOJA,
                plataforma=plataforma,
                loja=nome or plataforma,
                motivo=motivo,
                acao=acao,
                desde=mais_nova.idade.desde,
                nunca_leu=all(c.idade.nunca_leu for c in paradas),
                minutos=mais_nova.idade.minutos,
                limite_min=_limite_min(max(LIMITES[c.classe] for c in paradas)),
                integration_id=integration_id,
                caixas=tuple(c.rotulo for c in paradas) if len(grupo) > 1 else (),
                detalhe=erro,
            )
        )
    return out


def _robo(caixas: list[_Caixa], agora: datetime, corte: datetime | None) -> list[LeituraParada]:
    """Lojas do robô do Mac mini: uma linha por perfil."""
    out: list[LeituraParada] = []
    for c in caixas:
        if not _entra(c, corte):
            continue
        canal = c.canal
        status, motivo_robo = robo_atd.status_efetivo(canal, agora)
        if status == STATUS_CANAL_PARADO:
            motivo, acao = MOTIVO_ROBO_SEM_SINAL, ACAO_ROBO_SEM_SINAL
        elif status == STATUS_CANAL_SESSAO_CAIU:
            motivo, acao = MOTIVO_ROBO_SESSAO, ACAO_ROBO_SESSAO
        elif status == STATUS_ERRO:
            motivo, acao = MOTIVO_ROBO_ERRO, ACAO_ROBO_ERRO
        else:
            motivo, acao = MOTIVO_NAO_RODOU, ACAO_ROBO_ERRO
        explica = _texto_curto(motivo_robo)
        out.append(
            LeituraParada(
                chave=f"canal:{canal.id}",
                tipo=TIPO_LOJA,
                plataforma=canal.plataforma,
                loja=robo_atd.nome_da_loja(canal),
                motivo=motivo,
                acao=acao,
                desde=c.idade.desde,
                nunca_leu=c.idade.nunca_leu,
                minutos=c.idade.minutos,
                limite_min=_limite_min(LIMITES[CLASSE_ROBO]),
                canal_id=canal.id,
                detalhe=f"perfil {canal.robo_perfil_id} no AdsPower"
                + (f" · {explica}" if explica else ""),
            )
        )
    return out


def _externos(caixas: list[_Caixa], corte: datetime | None) -> list[LeituraParada]:
    """Carrinho dos sites e comentários das redes: uma linha por caixa."""
    out: list[LeituraParada] = []
    for c in caixas:
        if not _entra(c, corte):
            continue
        canal = c.canal
        site = c.classe == CLASSE_SITE
        if canal.status == STATUS_CANAL_SEM_ENDPOINT:
            motivo, acao = MOTIVO_SITE_SEM_ROTA, ACAO_SITE
        elif canal.status == STATUS_SEM_ESCOPO:
            motivo = MOTIVO_SITE_TOKEN if site else MOTIVO_REDE_SEM_ESCOPO
            acao = ACAO_SITE if site else ACAO_REDES
        elif canal.status == STATUS_ERRO:
            motivo, acao = MOTIVO_ERRO, ACAO_SITE if site else ACAO_REDES
        else:
            motivo, acao = MOTIVO_NAO_RODOU, ACAO_SITE if site else ACAO_REDES
        erro = _texto_curto(canal.ultimo_erro)
        out.append(
            LeituraParada(
                chave=f"canal:{canal.id}",
                tipo=TIPO_LOJA,
                plataforma=canal.plataforma,
                loja=canais_externos.nome_do_canal(canal),
                motivo=motivo,
                acao=acao,
                desde=c.idade.desde,
                nunca_leu=c.idade.nunca_leu,
                minutos=c.idade.minutos,
                limite_min=_limite_min(LIMITES[c.classe]),
                canal_id=canal.id,
                caixas=(c.rotulo,),
                detalhe=canais_externos.descricao_da_origem(canal)
                + (f" · {erro}" if erro else ""),
            )
        )
    return out


# ── As rodadas sem caixa (reclamações, avaliações) ────────────────────────


def rodada_ok(rodada: str, resumo: Any) -> bool:
    """O resumo que o cron devolveu é de uma rodada que LEU? (`None` = quebrou;
    `pulado` = outra rodada estava com a trava; todas as contas com erro = não
    leu nada.)"""
    if not isinstance(resumo, dict) or resumo.get("pulado"):
        return False
    if rodada == RODADA_RECLAMACOES:
        parte, total, erros = resumo.get("ml"), "contas", "contas_com_erro"
    elif rodada == RODADA_AVALIACOES:
        parte, total, erros = resumo.get("shopee"), "lojas", "lojas_com_erro"
    else:
        return False
    if not isinstance(parte, dict):
        return False
    try:
        n, falhas = int(parte.get(total) or 0), int(parte.get(erros) or 0)
    except (TypeError, ValueError):
        return False
    return n == 0 or falhas < n


async def carimbar_rodada(rodada: str, resumo: Any, *, agora: datetime | None = None) -> bool:
    """O worker chama depois de cada rodada LIGADA de reclamações/avaliações:
    marca a primeira vez que ela rodou (a carência) e, se leu, o último
    sucesso. Devolve se carimbou o sucesso. Nunca levanta (o carimbo é
    acessório: sem ele, a tela só acusa depois do limite)."""
    agora = agora or datetime.now(UTC)
    momento = str(int(agora.timestamp()))
    try:
        await redis.hsetnx(CHAVE_VISTO, rodada, momento)
        if not rodada_ok(rodada, resumo):
            return False
        await redis.hset(CHAVE_OK, rodada, momento)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_carimbo_rodada_falhou", rodada=rodada, err=type(e).__name__)
        return False
    return True


async def esquecer_rodada(rodada: str) -> None:
    """A rodada está desligada de propósito: apaga os carimbos dela — ao
    religar, a carência recomeça e o carimbo velho não acusa "parada há 3
    semanas". Nunca levanta."""
    try:
        await redis.hdel(CHAVE_OK, rodada)
        await redis.hdel(CHAVE_VISTO, rodada)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_carimbo_rodada_falhou", rodada=rodada, err=type(e).__name__)


async def _rodadas(agora: datetime, corte: datetime | None) -> list[LeituraParada]:
    """Reclamações e avaliações pelo carimbo do Redis (só leitura)."""
    s = get_settings()
    ligadas = [
        (rodada, nome)
        for rodada, (nome, interruptor) in RODADAS.items()
        if s.atendimento_leitura_ativa and getattr(s, interruptor, False)
    ]
    if not ligadas:
        return []
    try:
        oks = await redis.hgetall(CHAVE_OK) or {}
        vistos = await redis.hgetall(CHAVE_VISTO) or {}
    except Exception as e:  # noqa: BLE001
        # Sem Redis não dá para olhar: a rodada fica fora da lista (o resto vale).
        logger.warning("atendimento_leitura_parada_redis_falhou", err=type(e).__name__)
        return []
    out: list[LeituraParada] = []
    for rodada, nome in ligadas:
        limite = LIMITES[rodada]
        ok = _epoch(oks.get(rodada))
        if ok is not None:
            desde, nunca = ok, False
        else:
            desde, nunca = _epoch(vistos.get(rodada)), True
            if desde is None:
                continue  # ainda não rodou desde que ligou (ou o Redis reiniciou)
        if agora - desde <= limite:
            continue
        if corte is not None and corte - desde <= limite:
            continue  # parou junto com a leitura inteira: está na linha geral
        out.append(
            LeituraParada(
                chave=f"rodada:{rodada}",
                tipo=TIPO_RODADA,
                plataforma=PLATAFORMA_INTERNO,
                loja=nome,
                motivo=MOTIVO_RODADA,
                acao=ACAO_RODADA.format(nome=rodada),
                desde=desde,
                nunca_leu=nunca,
                minutos=_minutos(desde, agora),
                limite_min=_limite_min(limite),
                detalhe=(
                    "nenhuma rodada terminou lendo desde que ela foi ligada"
                    if nunca
                    else None
                ),
            )
        )
    return out


# ── As caixas de e-mail da empresa (a Central de e-mail) ──────────────────


async def _caixas_de_email(session: AsyncSession, agora: datetime) -> list[LeituraParada]:
    """As caixas `empresa` com a ponte ligada que não leem (nunca as privadas)."""
    from app.services.mail_atendimento import saude as mail_saude

    try:
        # SAVEPOINT: uma consulta que falhe aqui não estraga a transação do /resumo.
        async with session.begin_nested():
            paradas = await mail_saude.caixas_paradas(session, agora=agora)
    except Exception as e:  # noqa: BLE001 — o vigia nunca derruba o /resumo
        logger.warning("atendimento_leitura_parada_email_falhou", err=type(e).__name__)
        return []
    return [
        LeituraParada(
            chave=f"mail:{p.mailbox_id}",
            tipo=TIPO_LOJA,
            plataforma=PLATAFORMA_EMAIL,
            loja=p.nome,
            motivo=p.motivo,
            acao=p.acao or ACAO_CAIXA_EMAIL,
            desde=p.desde,
            nunca_leu=p.nunca_leu,
            minutos=p.minutos,
            limite_min=_limite_min(p.limite),
            caixas=(ROTULO_CAIXA_EMAIL,),
            detalhe=_texto_curto(p.detalhe),
        )
        for p in paradas
    ]


# ── A lista ───────────────────────────────────────────────────────────────

_ORDEM_TIPO = {TIPO_GERAL: 0, TIPO_LOJA: 1, TIPO_RODADA: 2}


async def leitura_parada(
    session: AsyncSession,
    *,
    agora: datetime | None = None,
    nomes: Mapping[UUID, str] | None = None,
) -> list[LeituraParada]:
    """As lojas/canais sem ler além do limite, agora. SÓ LEITURA.

    `nomes` (integration_id → nome da loja) é opcional: o resumo da tela já
    tem o nome de cada loja (`lojas.nome_da_loja`) e passa aqui, para não
    perguntar de novo; o que faltar, este módulo pergunta.

    Ordem: as gerais, as lojas (por plataforma e nome) e as rodadas.
    """
    agora = agora or datetime.now(UTC)
    caixas = await _caixas(session, agora)
    por_classe: dict[str, list[_Caixa]] = defaultdict(list)
    for c in caixas:
        por_classe[c.classe].append(c)

    out: list[LeituraParada] = []
    api = por_classe[CLASSE_LOJA] + por_classe[CLASSE_MAGALU]
    geral = _geral_lojas(api, agora)
    # O worker caiu: a última leitura de qualquer loja é o corte — junto da
    # linha geral só fica o que já estava parado antes (sites, redes e rodadas
    # também rodam no worker).
    corte = geral.desde if geral is not None else None
    if geral is not None:
        out.append(geral)
    if api:
        out.extend(await _lojas_api(session, api, corte, nomes or {}))

    robo = por_classe[CLASSE_ROBO]
    geral_robo = _geral_robo(robo, agora)
    if geral_robo is not None:
        out.append(geral_robo)
    out.extend(_robo(robo, agora, geral_robo.desde if geral_robo is not None else None))

    out.extend(_externos(por_classe[CLASSE_SITE] + por_classe[CLASSE_REDES], corte))
    out.extend(await _rodadas(agora, corte))
    out.extend(await _caixas_de_email(session, agora))
    return sorted(
        out,
        key=lambda x: (_ORDEM_TIPO.get(x.tipo, 9), x.plataforma, x.loja.lower(), x.chave),
    )
