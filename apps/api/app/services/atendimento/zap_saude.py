"""Zap (WhatsApp Cloud API) — a SAÚDE, os ALARMES e a LIMPEZA (RF10, Onda 4).

Desenho: `docs/zap/desenho.md` §12 "Onda 4" (a lista dos alarmes), §5.1
(alarme de assinatura por proporção), §10.1 (token), §10.2 (webhook e
inscrição), §10.5 (retenção). 27/10/2026.

Três partes:

1. **Os alarmes** (`calcular`) — o retrato de AGORA, só do banco e do Redis
   (nada vai à Meta): a tela "Números do WhatsApp" mostra a mesma lista que o
   cron avisa. Um alarme quando:
     - há falha de assinatura de um app E nenhum evento válido dele no mesmo
       período (proporção: um estranho com curl gera falhas, mas não para os
       eventos verdadeiros; a URL ou a chave erradas param todos);
     - a fila de eventos está parada há mais de 10 min;
     - há mídia a menos de 24 h de expirar na Meta sem ter sido baixada;
     - o token caiu, vence em 7 dias ou a inspeção diária falhou;
     - a Meta restringiu o número (368/131048, conta restrita) ou a qualidade
       caiu (amarela/vermelha, FLAGGED/DOWNGRADE);
     - o `webhook_configuration` do número ou da WABA aponta para outro lugar,
       ou há outro app inscrito na WABA (o do Duoke que ficou lá) — ou o
       nosso não está inscrito;
     - as respostas do mês chegam a 800 e a 1.000, ou o dia chega a 70 % do
       teto do número;
     - um número `ativo` com movimento nas últimas 24 h fica mais de 3 h sem
       webhook em horário comercial ("a Meta parou de mandar?").

2. **O cron `atendimento_zap_saude`** (`rodada`, a cada 15 min, só com a
   leitura do Zap ligada): renova da Meta o que só ela sabe (`renovar_da_meta`
   — só GET: a inspeção do token uma vez por dia, os apps inscritos e a saúde
   de cada número com o `webhook_configuration`), calcula os alarmes e AVISA
   os administradores (`avisar`: o Alerta do DaVinci, que vai ao Telegram de
   quem ligou; um por alarme por dia). O alarme de assinatura também roda no
   `webhook_signature_alert_scan` do worker (sem a chave: falha de
   assinatura importa antes de ligar a leitura) — com a MESMA chave de
   dedupe, nunca em dobro.

3. **O cron `atendimento_zap_limpeza`** (`limpeza`, uma vez por dia, só com a
   leitura do Zap ligada): a retenção do §10.5 — a mídia passada dos 180 dias
   perde o arquivo e o balão diz "mídia expirada" (`zap_midia.apagar_vencidas`,
   registro `apagada`); os eventos PROCESSADOS com mais de 30 dias somem (o
   pendente fica: apagar o que não foi processado perderia mensagem calado —
   o alarme da fila parada avisa); as sobras de download de um worker que
   caiu (`_partes/*.parte`) com mais de 1 dia.

Nada aqui leva texto do cliente, telefone ou token para o log ou o alerta: só
o nome do número/conta/app da tela, contagens e a frase.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AlertSeverity,
    AlertType,
    AtendimentoZapApp,
    AtendimentoZapConta,
    AtendimentoZapEvento,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
    User,
    UserRole,
    UserStatus,
)
from app.services.atendimento import (
    zap,
    zap_api,
    zap_cadastro,
    zap_contas,
    zap_entrada,
    zap_erros,
    zap_midia,
)

logger = structlog.get_logger()

_SP = ZoneInfo("America/Sao_Paulo")

# ── As réguas ─────────────────────────────────────────────────────────────
# Assinatura: falhas do app (o contador do Redis vive 1 h desde a última) e
# nenhum evento válido dele na mesma hora. 3, não 1: um curl perdido à noite,
# com o número quieto, não vira alarme; a chave errada gera uma falha por
# mensagem (e a Meta reenvia).
ASSINATURA_MINIMO = 3
ASSINATURA_JANELA = timedelta(hours=1)
FILA_PARADA = timedelta(minutes=10)
MIDIA_AVISO = timedelta(hours=24)
TOKEN_AVISO = timedelta(days=7)
INSPECAO_A_CADA = timedelta(hours=24)
TETO_AVISO = 0.7
SEM_WEBHOOK = timedelta(hours=3)
MOVIMENTO = timedelta(hours=24)
# Horário comercial (SP): segunda a sábado, das 8 h às 20 h.
HORA_ABRE, HORA_FECHA = 8, 20
QUALIDADE_RUIM = ("YELLOW", "RED")
EVENTO_QUALIDADE_RUIM = ("FLAGGED", "DOWNGRADE")

# A rodada (uma por vez) e a retenção.
CHAVE_TRAVA = "zap:saude:trava"
TRAVA_TTL_S = 840
LIMPEZA_LOTE = 500
LIMPEZA_MAX_LOTES = 40
PARTES_IDADE = timedelta(days=1)

# Os códigos (a tela e o Alerta usam os mesmos).
A_ASSINATURA = "assinatura_falhando"
A_FILA = "fila_parada"
A_MIDIA = "midia_vai_expirar"
A_TOKEN_INVALIDO = "token_invalido"  # noqa: S105 — código do alarme, não segredo
A_TOKEN_VENCENDO = "token_vencendo"  # noqa: S105
A_TOKEN_NAO_CONFERIDO = "token_nao_conferido"  # noqa: S105
A_RESTRITO = "numero_restrito"
A_ERRO_NUMERO = "numero_com_erro"
A_QUALIDADE = "qualidade_caiu"
A_WEBHOOK = "webhook_em_outro_lugar"
A_OUTRO_APP = "outro_app_inscrito"
A_NAO_INSCRITO = "app_nao_inscrito"
A_RESPOSTAS_800 = "respostas_800"
A_RESPOSTAS_1000 = "respostas_1000"
A_TETO = "teto_70"
A_SEM_WEBHOOK = "sem_webhook"
# O que o `GET /{pnid}` mostrou (revisão de 27/10/2026, `zap_cadastro.problemas_do_numero`).
A_NAO_REGISTRADO = "numero_nao_registrado"
A_STATUS_META = "numero_status_meta"
A_NOME_RECUSADO = "nome_recusado"
A_WEBHOOK_NAO_CONFERIDO = "webhook_nao_conferido"

ERRO = "erro"
AVISO = "aviso"


@dataclass(frozen=True)
class Alerta:
    """Um alarme do Zap (o que a tela lista e o que o cron avisa)."""

    codigo: str
    nivel: str  # "erro" | "aviso"
    frase: str
    app_ref: UUID | None = None
    conta_ref: UUID | None = None
    numero_ref: UUID | None = None

    @property
    def chave(self) -> str:
        alvo = self.numero_ref or self.conta_ref or self.app_ref or "geral"
        return f"{self.codigo}:{alvo}"


def _agora() -> datetime:
    return datetime.now(UTC)


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _data(bruto: object) -> datetime | None:
    if not isinstance(bruto, str) or not bruto:
        return None
    try:
        return _utc(datetime.fromisoformat(bruto))
    except ValueError:
        return None


def em_horario_comercial(agora: datetime) -> bool:
    """Segunda a sábado, das 8 h às 20 h (São Paulo). PURA."""
    local = agora.astimezone(_SP)
    return local.weekday() < 6 and HORA_ABRE <= local.hour < HORA_FECHA


def nome_do_numero(numero: AtendimentoZapNumero) -> str:
    return numero.nome_na_barra or numero.verified_name or numero.display_phone_number


def nome_da_conta(conta: AtendimentoZapConta) -> str:
    return conta.nome or f"conta {conta.waba_id}"


def nome_do_app(app: AtendimentoZapApp) -> str:
    return app.nome or f"app {app.app_id}"


def _redis(redis_cli: Any = None) -> Any:
    if redis_cli is not None:
        return redis_cli
    from app import redis_client

    return redis_client.redis


async def falhas_de_assinatura(app_refs: list[UUID], *, redis_cli: Any = None) -> dict[UUID, int]:
    """O contador de falha de assinatura de cada app (Redis). Sem Redis: zero (não alarma)."""
    cli = _redis(redis_cli)
    out: dict[UUID, int] = {}
    for ref in app_refs:
        try:
            bruto = await cli.get(zap_contas.CHAVE_FALHA_ASSINATURA_APP.format(app_ref=ref))
        except Exception as exc:  # noqa: BLE001 — o alarme é acessório
            logger.warning("zap_saude_redis_falhou", err=type(exc).__name__)
            return {}
        try:
            out[ref] = int(bruto or 0)
        except (TypeError, ValueError):
            out[ref] = 0
    return out


def assinatura_falhando(app: AtendimentoZapApp, falhas: int, agora: datetime) -> bool:
    """Falhas suficientes E nenhum evento válido do app na última hora. PURA."""
    if falhas < ASSINATURA_MINIMO:
        return False
    ultimo = _utc(app.ultimo_evento_em)
    return ultimo is None or agora - ultimo > ASSINATURA_JANELA


# ── A conexão de um número (a "pill" da tela) ─────────────────────────────


def conexao(
    numero: AtendimentoZapNumero,
    conta: AtendimentoZapConta | None,
    app: AtendimentoZapApp | None,
    falhas: int,
    agora: datetime,
) -> tuple[str, str]:
    """(código, frase) da coluna "Conexão" (§8.4), na ordem do que mais pesa. PURA."""
    if conta is not None and conta.status == "token_invalido":
        return "token_vencido", "Token vencido ou revogado"
    if conta is not None and conta.status == "sem_permissao":
        return "token_sem_permissao", "Token sem permissão"
    if app is not None and assinatura_falhando(app, falhas, agora):
        return "assinatura_falhando", "Assinatura falhando"
    if numero.saude_status == "restrito":
        return "restrito", "Restrito pela Meta"
    if numero.saude_status == "erro":
        return "erro", "Erro da Meta"
    problemas = dict(zap_cadastro.problemas_do_numero(numero))
    if zap_cadastro.P_STATUS_META in problemas:
        return "restrito", problemas[zap_cadastro.P_STATUS_META]
    if zap_cadastro.P_NAO_REGISTRADO in problemas:
        return "nao_registrado", "Não registrado na API do WhatsApp: use Registrar"
    if (numero.saude or {}).get("webhook_outro"):
        return "webhook_outro", "Mensagens indo para outro lugar"
    if zap_cadastro.P_WEBHOOK_NAO_CONFERIDO in problemas:
        return "webhook_nao_conferido", "Webhook não conferido na última leitura"
    if numero.ultimo_recebido_em is None:
        return "sem_eventos", "Sem eventos ainda"
    return "conectado", "Conectado"


# ── Os alarmes ────────────────────────────────────────────────────────────


def _alertas_do_app(
    apps: list[AtendimentoZapApp], falhas: dict[UUID, int], agora: datetime
) -> list[Alerta]:
    out: list[Alerta] = []
    for app in apps:
        n = falhas.get(app.id, 0)
        if assinatura_falhando(app, n, agora):
            out.append(
                Alerta(
                    A_ASSINATURA,
                    ERRO,
                    f"{nome_do_app(app)}: a Meta mandou {n} aviso(s) que o DaVinci recusou "
                    "(a assinatura não confere) e nenhum chegou certo na última hora. Confira "
                    "se a chave secreta do app é a mesma no DaVinci e na Meta.",
                    app_ref=app.id,
                )
            )
    return out


async def _alerta_da_fila(session: AsyncSession, agora: datetime) -> list[Alerta]:
    """O evento que JÁ devia ter sido processado há mais de 10 min (a fila parada)."""
    devido = func.coalesce(AtendimentoZapEvento.proxima_em, AtendimentoZapEvento.recebido_em)
    mais_velho = await session.scalar(
        select(func.min(devido)).where(
            AtendimentoZapEvento.processado_em.is_(None),
            AtendimentoZapEvento.tipo != zap_entrada.TIPO_IGNORADO,
        )
    )
    mais_velho = _utc(mais_velho)
    if mais_velho is None or agora - mais_velho <= FILA_PARADA:
        return []
    minutos = int((agora - mais_velho).total_seconds() // 60)
    return [
        Alerta(
            A_FILA,
            ERRO,
            f"A fila do WhatsApp está parada: há aviso da Meta esperando há {minutos} min. "
            "Veja se o worker do DaVinci está no ar (as mensagens não estão entrando).",
        )
    ]


async def _alertas_de_midia(
    session: AsyncSession, numeros: dict[UUID, AtendimentoZapNumero], agora: datetime
) -> list[Alerta]:
    linhas = (
        await session.execute(
            select(AtendimentoZapMidia.numero_ref, func.count(AtendimentoZapMidia.id))
            .where(
                AtendimentoZapMidia.direcao == "entrada",
                AtendimentoZapMidia.status.in_((zap_midia.M_PENDENTE, zap_midia.M_BAIXANDO)),
                AtendimentoZapMidia.expira_meta_em.is_not(None),
                AtendimentoZapMidia.expira_meta_em < agora + MIDIA_AVISO,
            )
            .group_by(AtendimentoZapMidia.numero_ref)
        )
    ).all()
    out: list[Alerta] = []
    for ref, n in linhas:
        numero = numeros.get(ref)
        nome = nome_do_numero(numero) if numero is not None else "um número"
        out.append(
            Alerta(
                A_MIDIA,
                AVISO,
                f"{nome}: {int(n)} foto(s)/áudio(s) do cliente vão sumir da Meta em menos de "
                "24 h sem terem sido baixados. Veja o token da conta e se o worker está no ar.",
                numero_ref=ref,
            )
        )
    return out


def _alertas_da_conta(
    conta: AtendimentoZapConta, tem_numero_vivo: bool, agora: datetime
) -> list[Alerta]:
    out: list[Alerta] = []
    nome = nome_da_conta(conta)
    saude = conta.saude or {}
    if conta.status in ("token_invalido", "sem_permissao"):
        out.append(
            Alerta(
                A_TOKEN_INVALIDO,
                ERRO,
                f"{nome}: a conexão com a Meta caiu ({conta.ultimo_erro or conta.status}). "
                "Gere um token novo na Meta e cole em Trocar token.",
                conta_ref=conta.id,
            )
        )
    elif not conta.token_nunca_vence and conta.token_expira_em is not None:
        falta = _utc(conta.token_expira_em) - agora
        if falta < TOKEN_AVISO:
            dias = max(0, falta.days)
            out.append(
                Alerta(
                    A_TOKEN_VENCENDO,
                    ERRO if falta.total_seconds() <= 0 else AVISO,
                    f"{nome}: o token "
                    + ("já venceu" if falta.total_seconds() <= 0 else f"vence em {dias} dia(s)")
                    + ". Gere outro com validade Nunca e cole em Trocar token.",
                    conta_ref=conta.id,
                )
            )
    if saude.get("inspecao_erro") and conta.status not in ("token_invalido", "sem_permissao"):
        out.append(
            Alerta(
                A_TOKEN_NAO_CONFERIDO,
                AVISO,
                f"{nome}: não deu para conferir o token na Meta hoje "
                f"({saude['inspecao_erro']}). Se repetir, use Trocar token.",
                conta_ref=conta.id,
            )
        )
    outros = saude.get("outros_apps") or []
    nosso_fora = saude.get("inscricao_em") and saude.get("nosso_inscrito") is False
    if outros:
        nomes = ", ".join(str(a.get("nome") or a.get("id")) for a in outros if isinstance(a, dict))
        out.append(
            Alerta(
                A_OUTRO_APP,
                ERRO,
                (
                    f"{nome}: o DaVinci NÃO está inscrito e {nomes} está. As mensagens dos "
                    "clientes vão só para lá — peça para removerem o app deles da conta."
                )
                if nosso_fora
                else (
                    f"{nome}: além do DaVinci, está inscrito: {nomes}. As mensagens dos "
                    "clientes também vão para lá — peça para removerem o app deles da conta."
                ),
                conta_ref=conta.id,
            )
        )
    # Independe de haver outro app: sem o nosso inscrito, nada chega ao DaVinci.
    if nosso_fora and tem_numero_vivo:
        out.append(
            Alerta(
                A_NAO_INSCRITO,
                AVISO,
                f"{nome}: o app do DaVinci não está inscrito nesta conta — sem isso nenhuma "
                "mensagem chega. Clique em Inscrever.",
                conta_ref=conta.id,
            )
        )
    return out


def _alertas_do_numero(numero: AtendimentoZapNumero, agora: datetime) -> list[Alerta]:
    out: list[Alerta] = []
    nome = nome_do_numero(numero)
    saude = numero.saude or {}
    if numero.saude_status == "restrito":
        out.append(
            Alerta(
                A_RESTRITO,
                ERRO,
                f"{nome}: {saude.get('restricao') or 'a Meta restringiu este número'}. "
                "O envio foi pausado; veja no WhatsApp Manager.",
                numero_ref=numero.id,
            )
        )
    elif numero.saude_status == "erro":
        erro = saude.get("erro_meta") if isinstance(saude.get("erro_meta"), dict) else {}
        out.append(
            Alerta(
                A_ERRO_NUMERO,
                ERRO,
                f"{nome}: {erro.get('frase') or numero.ultimo_erro or 'erro da Meta no número'}",
                numero_ref=numero.id,
            )
        )
    qualidade = str(saude.get("qualidade") or "").upper()
    evento = str(saude.get("qualidade_evento") or "").upper()
    if qualidade in QUALIDADE_RUIM or evento in EVENTO_QUALIDADE_RUIM:
        cor = {"YELLOW": "amarela", "RED": "vermelha"}.get(qualidade, "em queda")
        out.append(
            Alerta(
                A_QUALIDADE,
                ERRO if qualidade == "RED" else AVISO,
                f"{nome}: a qualidade do número na Meta está {cor}. Muitos clientes bloquearam "
                "ou denunciaram — reveja as últimas respostas antes que a Meta restrinja.",
                numero_ref=numero.id,
            )
        )
    fora = saude.get("webhook_outro") or []
    if fora:
        out.append(
            Alerta(
                A_WEBHOOK,
                ERRO,
                f"{nome}: " + (zap_cadastro.frase_do_webhook(fora) or "webhook em outro lugar"),
                numero_ref=numero.id,
            )
        )
    problemas = dict(zap_cadastro.problemas_do_numero(numero))
    erro_meta = saude.get("erro_meta") if isinstance(saude.get("erro_meta"), dict) else {}
    erro_code = erro_meta.get("code")
    # Fora da API: só nas etapas que mandam (na preparação e na migração é
    # o esperado até o Registrar) e sem repetir o alarme do 131045/133010.
    if (
        zap_cadastro.P_NAO_REGISTRADO in problemas
        and numero.etapa in zap.ETAPAS_QUE_ENVIAM
        and not (
            numero.saude_status == "erro" and erro_code in zap_cadastro.CODIGOS_NAO_REGISTRADO
        )
    ):
        out.append(
            Alerta(
                A_NAO_REGISTRADO,
                ERRO,
                f"{nome}: {problemas[zap_cadastro.P_NAO_REGISTRADO]}.",
                numero_ref=numero.id,
            )
        )
    if zap_cadastro.P_STATUS_META in problemas and numero.saude_status != "restrito":
        out.append(
            Alerta(
                A_STATUS_META,
                ERRO,
                f"{nome}: {problemas[zap_cadastro.P_STATUS_META]}.",
                numero_ref=numero.id,
            )
        )
    for codigo, alarme in (
        (zap_cadastro.P_NOME_RECUSADO, A_NOME_RECUSADO),
        (zap_cadastro.P_WEBHOOK_NAO_CONFERIDO, A_WEBHOOK_NAO_CONFERIDO),
    ):
        if codigo in problemas:
            out.append(Alerta(alarme, AVISO, f"{nome}: {problemas[codigo]}.", numero_ref=numero.id))
    ultimo = _utc(numero.ultimo_recebido_em)
    if (
        numero.etapa == "ativo"
        and ultimo is not None
        and SEM_WEBHOOK < agora - ultimo <= MOVIMENTO
        and em_horario_comercial(agora)
    ):
        horas = int((agora - ultimo).total_seconds() // 3600)
        out.append(
            Alerta(
                A_SEM_WEBHOOK,
                AVISO,
                f"{nome}: nenhuma mensagem chega há {horas} h, e o número costuma ter "
                "movimento. A Meta parou de mandar? Use Testar conexão.",
                numero_ref=numero.id,
            )
        )
    return out


async def _alertas_de_volume(
    session: AsyncSession, numeros: list[AtendimentoZapNumero], agora: datetime
) -> list[Alerta]:
    out: list[Alerta] = []
    com_canal = [n for n in numeros if n.canal_id is not None]
    respostas = await zap_entrada.respostas_do_mes(session, [n.canal_id for n in com_canal], agora)
    for n in com_canal:
        nome = nome_do_numero(n)
        todas, cobradas = respostas.get(n.canal_id, (0, 0))
        marcos = sorted(zap_entrada.ALERTAS_RESPOSTAS_MES)
        if todas >= marcos[-1]:
            out.append(
                Alerta(
                    A_RESPOSTAS_1000,
                    ERRO,
                    f"{nome}: {todas} respostas neste mês — passou das {marcos[-1]} grátis; "
                    f"as próximas são cobradas ({cobradas} já cobradas).",
                    numero_ref=n.id,
                )
            )
        elif todas >= marcos[0]:
            out.append(
                Alerta(
                    A_RESPOSTAS_800,
                    AVISO,
                    f"{nome}: {todas} respostas neste mês — perto das {marcos[-1]} grátis.",
                    numero_ref=n.id,
                )
            )
        if n.etapa not in zap.ETAPAS_QUE_ENVIAM:
            continue
        teto = zap.teto_do_numero(n)
        if teto <= 0:
            continue
        hoje = await zap.envios_do_numero_hoje(session, n.canal_id, agora)
        if hoje >= teto * TETO_AVISO:
            out.append(
                Alerta(
                    A_TETO,
                    AVISO if hoje < teto else ERRO,
                    f"{nome}: já mandou {hoje} de {teto} mensagens hoje "
                    f"({int(100 * hoje / teto)}% do teto do dia).",
                    numero_ref=n.id,
                )
            )
    return out


async def calcular(
    session: AsyncSession, *, agora: datetime | None = None, redis_cli: Any = None
) -> list[Alerta]:
    """Os alarmes de AGORA — só banco e Redis (nada vai à Meta). Só leitura."""
    agora = agora or _agora()
    apps = list(
        (await session.execute(select(AtendimentoZapApp).where(AtendimentoZapApp.ativo.is_(True))))
        .scalars()
        .all()
    )
    contas = list((await session.execute(select(AtendimentoZapConta))).scalars().all())
    numeros = list(
        (
            await session.execute(
                select(AtendimentoZapNumero).where(AtendimentoZapNumero.etapa != "desligado")
            )
        )
        .scalars()
        .all()
    )
    falhas = await falhas_de_assinatura([a.id for a in apps], redis_cli=redis_cli)
    out = _alertas_do_app(apps, falhas, agora)
    out += await _alerta_da_fila(session, agora)
    out += await _alertas_de_midia(session, {n.id: n for n in numeros}, agora)
    vivos = {n.conta_ref for n in numeros if n.etapa in ("teste", "migrando", "ativo")}
    for conta in contas:
        out += _alertas_da_conta(conta, conta.id in vivos, agora)
    for numero in numeros:
        out += _alertas_do_numero(numero, agora)
    out += await _alertas_de_volume(session, numeros, agora)
    return out


async def alarme_assinatura(
    session: AsyncSession, *, agora: datetime | None = None, redis_cli: Any = None
) -> list[Alerta]:
    """Só o alarme de assinatura (o `webhook_signature_alert_scan` do worker chama)."""
    agora = agora or _agora()
    apps = list(
        (await session.execute(select(AtendimentoZapApp).where(AtendimentoZapApp.ativo.is_(True))))
        .scalars()
        .all()
    )
    falhas = await falhas_de_assinatura([a.id for a in apps], redis_cli=redis_cli)
    return _alertas_do_app(apps, falhas, agora)


# ── Avisar ────────────────────────────────────────────────────────────────


_TITULOS = {
    A_ASSINATURA: "assinatura do webhook falhando",
    A_FILA: "fila de mensagens parada",
    A_MIDIA: "mídia do cliente vai expirar",
    A_TOKEN_INVALIDO: "conexão com a Meta caiu",
    A_TOKEN_VENCENDO: "token vencendo",
    A_TOKEN_NAO_CONFERIDO: "token não conferido",
    A_RESTRITO: "número restrito pela Meta",
    A_ERRO_NUMERO: "erro da Meta no número",
    A_QUALIDADE: "qualidade do número caiu",
    A_WEBHOOK: "mensagens indo para outro lugar",
    A_OUTRO_APP: "outro app inscrito na conta",
    A_NAO_INSCRITO: "app do DaVinci não inscrito",
    A_RESPOSTAS_800: "800 respostas no mês",
    A_RESPOSTAS_1000: "passou das 1.000 respostas grátis",
    A_TETO: "perto do teto do dia",
    A_SEM_WEBHOOK: "número sem mensagens há horas",
    A_NAO_REGISTRADO: "número fora da API do WhatsApp",
    A_STATUS_META: "número marcado pela Meta",
    A_NOME_RECUSADO: "nome de exibição recusado",
    A_WEBHOOK_NAO_CONFERIDO: "webhook não conferido",
}


async def avisar(
    session: AsyncSession, alertas: list[Alerta], *, agora: datetime | None = None
) -> int:
    """Cada alarme NOVO vira um Alerta do DaVinci para cada administrador ativo.

    Um por alarme por dia (a `dedupe_key`): a tela continua mostrando enquanto
    durar. O Alerta vai ao Telegram de quem ligou o aviso (o `emit_alert` de
    sempre). Devolve quantos alarmes foram avisados agora. Não commita.
    """
    if not alertas:
        return 0
    from app.services.alerts import emit_alert

    agora = agora or _agora()
    dia = agora.astimezone(_SP).date().isoformat()
    admins = list(
        (
            await session.execute(
                select(User.id).where(User.role == UserRole.ADMIN, User.status == UserStatus.ACTIVE)
            )
        )
        .scalars()
        .all()
    )
    novos = 0
    for a in alertas:
        algum = False
        for user_id in admins:
            feito = await emit_alert(
                session,
                user_id=user_id,
                type=AlertType.GENERIC,
                severity=AlertSeverity.ERROR if a.nivel == ERRO else AlertSeverity.WARNING,
                title=f"WhatsApp: {_TITULOS.get(a.codigo, a.codigo)}",
                message=a.frase,
                payload={
                    "zap": a.codigo,
                    "app_ref": str(a.app_ref) if a.app_ref else None,
                    "conta_ref": str(a.conta_ref) if a.conta_ref else None,
                    "numero_ref": str(a.numero_ref) if a.numero_ref else None,
                },
                dedupe_key=f"zap:{a.chave}:{dia}",
            )
            algum = algum or feito is not None
        if algum:
            novos += 1
            logger.error("zap_alerta", codigo=a.codigo, alvo=a.chave.split(":", 1)[1])
    return novos


# ── O que só a Meta sabe (o cron; só GET) ─────────────────────────────────


async def _conferir_token(
    session: AsyncSession,
    conta: AtendimentoZapConta,
    app: AtendimentoZapApp,
    token: str,
    *,
    cliente: Any,
    agora: datetime,
) -> None:
    """A inspeção diária ESTRITA. Recusa da Meta marca a conta; "não respondeu" é alerta."""
    try:
        insp = await zap_api.inspecionar_token(
            token, app_id=app.app_id, waba_id=conta.waba_id, cliente=cliente
        )
        if not insp.alvos_detalhados:
            await zap_api.ler_waba(conta.waba_id, token, cliente=cliente)
    except zap_api.ZapApiErro as exc:
        status = None
        if exc.motivo == "invalido":
            status = "token_invalido"
        elif exc.motivo in ("outro_app", "sem_escopo", "sem_waba"):
            status = "sem_permissao"
        elif exc.code is not None:
            efeito = zap_erros.traduzir(exc.code).efeito
            if efeito in (zap_erros.EFEITO_TOKEN_INVALIDO, zap_erros.EFEITO_SEM_PERMISSAO):
                status = efeito
        if status is not None:
            conta.status = status
            conta.ultimo_erro, conta.ultimo_erro_code, conta.ultimo_erro_em = (
                str(exc)[:300],
                exc.code,
                agora,
            )
            logger.error("zap_token_recusado_na_inspecao", conta=str(conta.id), status=status)
        else:
            conta.saude = {
                **(conta.saude or {}),
                "inspecao_erro": str(exc)[:200],
                "inspecao_erro_em": agora.isoformat(),
            }
            logger.warning("zap_inspecao_falhou", conta=str(conta.id))
        return
    zap_cadastro._aplicar_inspecao(conta, insp, agora)


async def renovar_da_meta(
    session: AsyncSession, *, agora: datetime | None = None
) -> dict[str, int]:
    """O cron: inspeção do token (1×/dia), apps inscritos e a saúde de cada número. SÓ GET.

    Um commit por conta e por número: a Meta lenta num não segura o resto.
    Conta com o token caído não vai à Meta (o alarme já diz). Nunca levanta
    pelo erro de UM item: o `rollback` expira TODOS os objetos da sessão,
    então cada volta relê o seu pelo id (`session.get`) e o log do erro usa
    só o id guardado antes — ler atributo expirado no async é MissingGreenlet,
    e a rodada inteira (alarmes inclusive) cairia no primeiro timeout.
    """
    agora = agora or _agora()
    conta_ids = list(
        (
            await session.execute(
                select(AtendimentoZapConta.id).order_by(AtendimentoZapConta.created_at)
            )
        )
        .scalars()
        .all()
    )
    apps_ativos = set(
        (
            await session.execute(
                select(AtendimentoZapApp.id).where(AtendimentoZapApp.ativo.is_(True))
            )
        )
        .scalars()
        .all()
    )
    resumo = {"contas": 0, "numeros": 0, "erros": 0}

    async def _credencial(conta_id: UUID) -> tuple[Any, Any, str] | None:
        conta = await session.get(AtendimentoZapConta, conta_id)
        if conta is None or conta.app_ref not in apps_ativos:
            return None
        app = await session.get(AtendimentoZapApp, conta.app_ref)
        token = zap.token_valido(conta)
        if app is None or token is None:
            return None
        return conta, app, token

    async with zap_api.novo_cliente() as http:
        for conta_id in conta_ids:
            try:
                achado = await _credencial(conta_id)
                if achado is None:
                    continue
                conta, app, token = achado
                conferido = _utc(conta.token_conferido_em)
                if conferido is None or agora - conferido >= INSPECAO_A_CADA:
                    await _conferir_token(session, conta, app, token, cliente=http, agora=agora)
                if zap.token_valido(conta) is not None:
                    await zap_cadastro.conferir_inscricao(
                        session, conta, app, token, cliente=http, agora=agora
                    )
                await session.commit()
                resumo["contas"] += 1
            except Exception as exc:  # noqa: BLE001 — uma conta não derruba as outras
                await session.rollback()
                resumo["erros"] += 1
                logger.warning(
                    "zap_saude_conta_falhou", conta=str(conta_id), err=type(exc).__name__
                )
        numeros = (
            await session.execute(
                select(AtendimentoZapNumero.id, AtendimentoZapNumero.conta_ref)
                .where(AtendimentoZapNumero.etapa != "desligado")
                .order_by(AtendimentoZapNumero.created_at)
            )
        ).all()
        for numero_id, conta_ref in numeros:
            try:
                achado = await _credencial(conta_ref)
                numero = await session.get(AtendimentoZapNumero, numero_id)
                if achado is None or numero is None:
                    continue
                _conta, app, token = achado
                await zap_cadastro.ler_saude_do_numero(
                    session, numero, app, token, cliente=http, agora=agora
                )
                await session.commit()
                resumo["numeros"] += 1
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                resumo["erros"] += 1
                logger.warning(
                    "zap_saude_numero_falhou", numero=str(numero_id), err=type(exc).__name__
                )
    return resumo


# ── A rodada (o cron de 15 min) ───────────────────────────────────────────


async def rodada(*, agora: datetime | None = None) -> dict | None:
    """O cron `atendimento_zap_saude`. Sai na hora com a leitura do Zap desligada. Nunca levanta."""
    if not zap_entrada.leitura_ligada():
        return None
    import app.db as _db

    cli = _redis()
    token: str | None = uuid4().hex
    try:
        pegou = await cli.set(CHAVE_TRAVA, token, nx=True, ex=TRAVA_TTL_S)
    except Exception:  # noqa: BLE001 — sem Redis, segue (o cron é de 15 min)
        pegou, token = True, None
    if not pegou:
        return {"pulado": "ocupado"}
    try:
        async with _db.SessionLocal() as session:
            meta = await renovar_da_meta(session, agora=agora)
            alertas = await calcular(session, agora=agora)
            avisados = await avisar(session, alertas, agora=agora)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("zap_saude_falhou", err=type(exc).__name__)
        return None
    finally:
        if token is not None:
            try:
                # Só solta a trava que é DESTA rodada (a que passou do TTL não
                # apaga a da rodada seguinte).
                await cli.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    CHAVE_TRAVA,
                    token,
                )
            except Exception:  # noqa: BLE001, S110 — o TTL solta
                pass
    resumo = {"meta": meta, "alertas": len(alertas), "avisados": avisados}
    if alertas:
        logger.info("zap_saude", alertas=len(alertas), avisados=avisados, **meta)
    return resumo


# ── A limpeza (a retenção do §10.5) ───────────────────────────────────────


def _partes_velhas() -> int:
    """As sobras de download (`_partes/*.parte`) de um worker que caiu, com mais de 1 dia.

    Pelo relógio do DISCO (a data do arquivo), não pelo `agora` da rodada.
    """
    pasta = zap_midia.caminho_absoluto(f"{zap_midia.PASTA_TEMP}/x")
    if pasta is None:
        return 0
    pasta = pasta.parent
    if not pasta.is_dir():
        return 0
    corte = time.time() - PARTES_IDADE.total_seconds()
    n = 0
    for arq in pasta.glob(f"*{zap_midia.SUFIXO_TEMP}"):
        try:
            if arq.is_file() and arq.stat().st_mtime < corte:
                arq.unlink(missing_ok=True)
                n += 1
        except OSError as exc:
            logger.warning("zap_limpeza_parte_falhou", err=type(exc).__name__)
    return n


async def limpar(session: AsyncSession, *, agora: datetime | None = None) -> dict[str, int]:
    """A retenção: mídia (180 dias) e eventos PROCESSADOS (30 dias). Commita por lote."""
    agora = agora or _agora()
    s = get_settings()
    midias = 0
    for _ in range(LIMPEZA_MAX_LOTES):
        n = await zap_midia.apagar_vencidas(session, agora=agora, limite=LIMPEZA_LOTE)
        midias += n
        if n < LIMPEZA_LOTE:
            break
    corte = agora - timedelta(days=s.atendimento_zap_retencao_eventos_dias)
    eventos = 0
    for _ in range(LIMPEZA_MAX_LOTES):
        ids = (
            (
                await session.execute(
                    select(AtendimentoZapEvento.id)
                    .where(
                        AtendimentoZapEvento.processado_em.is_not(None),
                        AtendimentoZapEvento.recebido_em < corte,
                    )
                    .limit(LIMPEZA_LOTE * 10)
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            break
        res = await session.execute(
            delete(AtendimentoZapEvento).where(AtendimentoZapEvento.id.in_(ids))
        )
        await session.commit()
        eventos += int(res.rowcount or 0)
    pendentes = int(
        await session.scalar(
            select(func.count(AtendimentoZapEvento.id)).where(
                AtendimentoZapEvento.processado_em.is_(None),
                AtendimentoZapEvento.recebido_em < corte,
            )
        )
        or 0
    )
    await session.rollback()
    if pendentes:
        # Ficam: apagar o que não foi processado perderia mensagem calado.
        logger.warning("zap_limpeza_pendentes_velhos", eventos=pendentes)
    partes = _partes_velhas()
    return {"midias": midias, "eventos": eventos, "pendentes_velhos": pendentes, "partes": partes}


async def limpeza(*, agora: datetime | None = None) -> dict | None:
    """O cron `atendimento_zap_limpeza`. Sai na hora com a leitura do Zap desligada."""
    if not zap_entrada.leitura_ligada():
        return None
    import app.db as _db

    try:
        async with _db.SessionLocal() as session:
            resumo = await limpar(session, agora=agora)
    except Exception as exc:  # noqa: BLE001
        logger.error("zap_limpeza_falhou", err=type(exc).__name__)
        return None
    if any(resumo.values()):
        logger.info("zap_limpeza", **resumo)
    return resumo


__all__ = [
    "Alerta",
    "alarme_assinatura",
    "avisar",
    "calcular",
    "conexao",
    "em_horario_comercial",
    "limpar",
    "limpeza",
    "renovar_da_meta",
    "rodada",
]
