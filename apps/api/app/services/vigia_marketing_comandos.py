"""Comandos de Ads não aplicados — o robô que olha a fila do Marketing.

Vinicius, 22/09/2026: pausar/retomar anúncio, mexer em orçamento e duplicar a
Oferta Relâmpago da Shopee NÃO acontecem na hora — viram uma linha `pending`
em `marketing_commands` (o outbox) que alguém precisa executar:

- Shopee → `executor='browser'`: o executor do Mac (AdsPower) puxa por
  `/marketing/agent/lease`, vira `claimed`, aplica no Seller Center e reporta
  `done`/`failed` em `/agent/commands/<id>/result`;
- ML/Amazon → `executor='api'`: o consumidor interno (`services/marketing/
  commands.py`), que só roda no serviço `worker_marketing_agent`.

Quando qualquer uma das duas pontas para, NADA avisa: o comando fica na
tabela e a agenda (`services/marketing/reconcile.py`, 1 comando por minuto
enquanto o estado desejado ≠ o aplicado) continua empurrando sem que a loja
mude de estado. Foi o que aconteceu em produção: 8 comandos `claimed` desde
julho/2026 nas 4 lojas com agenda ligada — e como o reconciler pula conta com
comando "em voo" (`_has_open_command`), a agenda dessas lojas está parada há
dois meses sem ninguém saber. Este robô é o aviso que faltava.

## O que ele abre (e como fecha sozinho)

1. `comando:<id>` — comando MANUAL (ou Oferta Relâmpago) que voltou `failed`.
   Fecha quando um comando POSTERIOR da mesma conta + ação + campanha volta
   `done` ("refeito") ou quando a linha some.
2. `agenda:<account_id>` — a AGENDA daquela conta não está sendo aplicada:
   uma ocorrência por CONTA, não por comando. O reconciler enfileira 1 por
   minuto enquanto o executor falha (em jul/2026 foram ~3.4 mil `failed` em 9
   dias); uma ocorrência por comando encheria o painel e escondia o problema,
   que é sempre o mesmo. Fecha quando o último pause/resume completo da conta
   volta `done`.
3. `comando:<id>` — comando PENDENTE (ninguém pegou) ou PRESO (`claimed` e
   sem resposta) há mais que `pendente_min`. Nasce `baixa` e vira `pessoa`
   depois de 2× esse tempo. Fecha quando o comando conclui ou falha (aí quem
   abre é a regra 1 ou a 2).
4. `executor:offline` — o Mac parou de dar sinal de vida (a tabela
   `marketing_agent_heartbeat`, que o executor carimba a cada 60 s). Enquanto
   isso NENHUM comando da Shopee sai do lugar.
5. `executor:adspower` / `executor:trava` — o executor está online mas o
   AdsPower não responde (2ª causa de falha em produção) ou está com a trava
   `SELECTORS_CALIBRATED=false`, em que todo comando falha de propósito.

Tudo que ele olha é BANCO (nenhuma API externa, ~10 consultas por rodada), e
o fechamento é sempre pelo `fechar_nao_vistas`: a rodada re-vê o que ainda
vale e o que ela não viu fecha como "sumiu". A janela de 24 h
(`_JANELA_FALHAS`) só decide se uma falha ANTIGA vira ocorrência NOVA — sem
ela a primeira rodada abriria os milhares de `failed` históricos de julho
(a tabela nunca é limpa). Ocorrência já aberta continua sendo re-vista sem
limite de idade: fechamento por tempo não existe na Ouvidoria.

Ele só AVISA — nada aqui escreve em `marketing_commands`. Destravar um
comando `claimed` que morreu no meio continua sendo trabalho de gente (ou de
um expirador no reconciler, que ainda não existe).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import OuvidoriaOcorrencia, OuvidoriaRobo
from app.models.marketing import (
    AGENTE_LOGISTICA_PREFIXO,
    MarketingAccount,
    MarketingAgentHeartbeat,
    MarketingCommand,
)
from app.services import ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "vigia_marketing_comandos"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x766D6B74  # ascii "vmkt"

# Padrões quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com estes mesmos valores — services/ouvidoria.ROBOS).
_PENDENTE_MIN = 30
_EXECUTOR_OFFLINE_MIN = 10
# Idade máxima de uma falha pra ela virar ocorrência NOVA. Não fecha nada:
# aberta continua aberta enquanto o comando não for refeito. É só a peneira
# que impede a primeira rodada de despejar o passivo histórico no painel.
_JANELA_FALHAS = timedelta(hours=24)

_TZ_BR = ZoneInfo("America/Sao_Paulo")

# Contadores de uma rodada (ouvidoria_rodadas.contadores), em linguagem de
# operação: comandos_falhos = falha manual/Oferta Relâmpago; agendas_falhas =
# contas cuja agenda não está sendo aplicada; comandos_pendentes = ninguém
# pegou; comandos_presos = `claimed` sem resposta; executor_ok = 1 quando o
# Mac deu sinal dentro do prazo; executor_idade_min = há quanto tempo foi o
# último sinal; novas/persistem/sumiram = ocorrências.
_CONTADORES = (
    "comandos_falhos", "agendas_falhas", "comandos_pendentes", "comandos_presos",
    "novas", "persistem", "sumiram", "executor_ok", "executor_idade_min",
)

# Rótulo da plataforma NA FRENTE do nome da conta, como no vigia de
# importação ("Shopee Inova") — a operação fala assim.
_ROTULO_PLATAFORMA = {"shopee": "Shopee", "ml": "Mercado Livre", "amazon": "Amazon"}

# Ação do comando em português (os mesmos rótulos de `cmdActionLabel` em
# pages/marketing.vue, abertos por extenso porque aqui vira título).
_ROTULO_ACAO = {
    "pause": "Pausar anúncios",
    "resume": "Retomar anúncios",
    "set_budget": "Orçamento",
    "adjust_budget_pct": "Ajuste de orçamento",
    "flash_duplicate": "Duplicar Oferta Relâmpago",
}
# Quando o comando mira UMA campanha (`campaign_external_id`, caso do ML) e
# não a conta inteira (Shopee, em que o executor aplica em todos os anúncios).
_ROTULO_ACAO_CAMPANHA = {
    "pause": "Pausar campanha",
    "resume": "Retomar campanha",
    "set_budget": "Orçamento da campanha",
    "adjust_budget_pct": "Ajuste de orçamento da campanha",
}
_VERBO_AGENDA = {"pause": "pausar", "resume": "retomar"}
_FONTE = {"manual": "manual", "schedule": "agenda"}
# Quem devia ter executado — muda a frase e a ação da ocorrência.
_QUEM = {"browser": "O executor do Mac", "api": "O worker_marketing_agent"}

ACAO_GENERICA = (
    "Aplicar à mão no Seller Center ou corrigir a credencial e re-enfileirar"
)
ACAO_API = (
    "Conferir a credencial do ML em Sistema › Integrações e o serviço "
    "worker_marketing_agent"
)
ACAO_EXECUTOR_OFFLINE = (
    "Abrir o AdsPower e o executor no Mac "
    "(launchctl load ~/Library/LaunchAgents/com.davinci.executor.plist)"
)
ACAO_ADSPOWER = (
    "Abrir o AdsPower no Mac com a Local API ligada "
    "(http://local.adspower.net:50325)"
)
ACAO_PENDENTE_BROWSER = (
    "Conferir se o executor está rodando no Mac (badge Executor local em /marketing)"
)
ACAO_PRESO_BROWSER = (
    "Conferir o log do executor no Mac (/tmp/davinci-executor.err.log); se o "
    "comando não vai mais rodar, marcá-lo como falho pra agenda voltar a enfileirar"
)
ACAO_FILA_API = "Conferir o serviço worker_marketing_agent"

# Pedaço do `result` (minúsculo) → o que a pessoa faz. São as mensagens que o
# executor do Mac devolve de verdade (apps/executor/src/index.ts) e as causas
# que mais apareceram em produção — traduzir aqui é o que transforma
# "needs_manual_login" em uma instrução que a equipe consegue seguir.
_ACOES_POR_RESULTADO = (
    (
        "needs_manual_login",
        "Entrar na Shopee no perfil do AdsPower dessa loja e clicar de novo "
        "em /marketing",
    ),
    ("adspower inacess", "Abrir o AdsPower no Mac com a Local API ligada e refazer"),
    ("econnrefused", "Abrir o AdsPower no Mac com a Local API ligada e refazer"),
    ("sem adspower_user_id", "Cadastrar o perfil AdsPower da conta em Marketing"),
    ("account_has_no_integration", "Vincular a integração à conta em Marketing"),
    ("integration_not_found", "Vincular a integração à conta em Marketing"),
    ("não suportada", "Atualizar o executor do Mac (versão antiga)"),
    ("nao suportada", "Atualizar o executor do Mac (versão antiga)"),
)

LINK = "/marketing"


# ─── frases ────────────────────────────────────────────────────────────────


def _acao_por_resultado(resultado: str | None, executor: str) -> str:
    """O que a pessoa faz, lido do erro que a ponta devolveu. Sem casar nada,
    cai no genérico da ficha (ou no do ML, que é outro serviço)."""
    texto = (resultado or "").lower()
    for pedaco, acao in _ACOES_POR_RESULTADO:
        if pedaco in texto:
            return acao
    return ACAO_API if executor == "api" else ACAO_GENERICA


def _rotulo_conta(acc: MarketingAccount | None) -> str | None:
    """"Shopee Inova" / "Mercado Livre kfa". Em produção alguns nomes têm
    espaço na frente (" barbosa"), por isso o strip."""
    if acc is None:
        return None
    nome = (acc.name or "").strip()
    rotulo = _ROTULO_PLATAFORMA.get(acc.platform, acc.platform or "")
    return f"{rotulo} {nome}".strip()[:120] or None


def _rotulo_acao(cmd: MarketingCommand) -> str:
    """"Pausar anúncios" (conta inteira) ou "Pausar campanha 123" (ML)."""
    if cmd.campaign_external_id:
        base = _ROTULO_ACAO_CAMPANHA.get(cmd.action)
        if base:
            return f"{base} {cmd.campaign_external_id}"
    return _ROTULO_ACAO.get(cmd.action, cmd.action)


def _br(dt: datetime | None) -> str:
    """"18/07 09:32" no fuso de quem lê. Data junto porque comando preso em
    produção fica meses parado — "às 09:32" sozinho enganaria."""
    if dt is None:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_TZ_BR).strftime("%d/%m %H:%M")


def _minutos(inicio: datetime | None, agora: datetime) -> int:
    if inicio is None:
        return 0
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=UTC)
    return max(0, int((agora - inicio).total_seconds() // 60))


def _idade(minutos: int) -> str:
    """"40 min" / "6 h" / "63 dias". A ficha pedia sempre minutos, mas o caso
    real de produção é de MESES ("preso há 89.280 min" ninguém lê)."""
    if minutos < 90:
        return f"{minutos} min"
    if minutos < 48 * 60:
        return f"{minutos // 60} h"
    return f"{minutos // 1440} dias"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


# ─── rodada ────────────────────────────────────────────────────────────────


async def _abertas(session: AsyncSession) -> dict[str, OuvidoriaOcorrencia]:
    """As ocorrências abertas do robô, por chave. Servem pra decidir o que a
    rodada AINDA re-vê: uma falha fora da janela de 24 h não abre linha nova,
    mas se já existe uma aberta ela continua valendo (nada fecha por tempo)."""
    linhas = (
        (
            await session.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {o.chave: o for o in linhas}


def _ids_de_comando(abertas: dict[str, OuvidoriaOcorrencia]) -> list[UUID]:
    """Os comandos que já têm ocorrência aberta (`comando:<uuid>`) — a
    consulta de falhas precisa trazê-los mesmo fora da janela."""
    ids: list[UUID] = []
    for chave in abertas:
        if not chave.startswith("comando:"):
            continue
        try:
            ids.append(UUID(chave.split(":", 1)[1]))
        except ValueError:  # chave gravada à mão / formato antigo
            continue
    return ids


async def _registrar(r: ouvidoria.Rodada, agora: datetime, **campos) -> None:
    """`r.registrar` + os contadores novas/persistem. Ocorrência que o núcleo
    devolveu FECHADA (alguém marcou ignorada, ou tratada há < 24 h) não conta
    em nenhum dos dois: ninguém abriu nada."""
    row = await r.registrar(agora=agora, **campos)
    if row.fechada_em is not None:
        return
    if row.aberta_em == agora:
        r.contadores["novas"] += 1
    else:
        r.contadores["persistem"] += 1


async def _falhas_de_comando(
    session: AsyncSession,
    r: ouvidoria.Rodada,
    contas: dict[UUID, MarketingAccount],
    abertas: dict[str, OuvidoriaOcorrencia],
    *,
    agora: datetime,
) -> None:
    """Regra 1 — comando MANUAL ou Oferta Relâmpago que voltou `failed`.

    Os da AGENDA ficam de fora de propósito (vão na regra 2, agregados por
    conta): o reconciler enfileira 1 por minuto enquanto o executor falha, e
    uma ocorrência por comando viraria uma enxurrada de linhas iguais.
    """
    corte = agora - _JANELA_FALHAS
    falhas = (
        (
            await session.execute(
                select(MarketingCommand)
                .where(
                    MarketingCommand.status == "failed",
                    or_(
                        MarketingCommand.source == "manual",
                        MarketingCommand.action == "flash_duplicate",
                    ),
                    or_(
                        # Janela só pra ABRIR…
                        func.coalesce(
                            MarketingCommand.completed_at, MarketingCommand.updated_at
                        )
                        >= corte,
                        # …e o que já está aberto continua sendo re-visto.
                        MarketingCommand.id.in_(_ids_de_comando(abertas)),
                    ),
                )
                .order_by(MarketingCommand.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not falhas:
        return

    # "Refeito" = existe um comando POSTERIOR da mesma conta + ação + campanha
    # que voltou `done`. Uma consulta agrupada em vez de uma por falha (o
    # GROUP BY já junta campanha NULL com campanha NULL, que é o
    # "IS NOT DISTINCT FROM" que a gente quer).
    ultimo_done: dict[tuple, datetime] = {
        (acc, acao, camp): quando
        for acc, acao, camp, quando in (
            await session.execute(
                select(
                    MarketingCommand.account_id,
                    MarketingCommand.action,
                    MarketingCommand.campaign_external_id,
                    func.max(MarketingCommand.created_at),
                )
                .where(
                    MarketingCommand.status == "done",
                    MarketingCommand.account_id.in_({c.account_id for c in falhas}),
                )
                .group_by(
                    MarketingCommand.account_id,
                    MarketingCommand.action,
                    MarketingCommand.campaign_external_id,
                )
            )
        ).all()
    }

    for cmd in falhas:
        feito_em = ultimo_done.get(
            (cmd.account_id, cmd.action, cmd.campaign_external_id)
        )
        if feito_em is not None and feito_em > cmd.created_at:
            continue  # refeito depois → não vista → fecha como "sumiu"
        acc = contas.get(cmd.account_id)
        conta = _rotulo_conta(acc)
        quem = _QUEM.get(cmd.executor, _QUEM["browser"])
        resultado = (cmd.result or "").strip() or "sem mensagem"
        r.contadores["comandos_falhos"] += 1
        await _registrar(
            r,
            agora,
            chave=f"comando:{cmd.id}",
            plataforma=cmd.platform,
            conta=conta,
            titulo=f"{_rotulo_acao(cmd)} não aplicado — {conta or 'conta removida'}",
            detalhe=(
                f"{quem} devolveu: {resultado[:300]} · tentativa {cmd.attempts} · "
                f"pedido por {_FONTE.get(cmd.source, cmd.source)} às "
                f"{_br(cmd.created_at)} (BRT)"
            ),
            acao=_acao_por_resultado(cmd.result, cmd.executor),
            link=LINK,
            severidade="pessoa",
            precisa_pessoa=True,
            dados={
                "comando_id": str(cmd.id),
                "acao": cmd.action,
                "fonte": cmd.source,
                "executor": cmd.executor,
                "tentativas": cmd.attempts,
                "resultado": resultado[:300],
                "campanha": cmd.campaign_external_id,
                "criado_em": cmd.created_at.isoformat() if cmd.created_at else None,
                "concluido_em": (
                    cmd.completed_at.isoformat() if cmd.completed_at else None
                ),
            },
        )


async def _agendas_nao_aplicadas(
    session: AsyncSession,
    r: ouvidoria.Rodada,
    contas: dict[UUID, MarketingAccount],
    abertas: dict[str, OuvidoriaOcorrencia],
    *,
    agora: datetime,
) -> None:
    """Regra 2 — UMA ocorrência por CONTA cuja agenda não está sendo aplicada.

    A pergunta não é "quantos comandos falharam" (o reconciler cria um por
    minuto), é "o último pause/resume COMPLETO dessa loja deu certo?". Se deu,
    a agenda está viva e a ocorrência fecha; se não deu, ela fica aberta com
    o número de tentativas desde o último acerto.
    """
    corte = agora - _JANELA_FALHAS
    # DISTINCT ON: o último comando de agenda COMPLETO (done/failed) por conta.
    ultimos = (
        (
            await session.execute(
                select(MarketingCommand)
                .where(
                    MarketingCommand.source == "schedule",
                    MarketingCommand.action.in_(("pause", "resume")),
                    MarketingCommand.status.in_(("done", "failed")),
                )
                .order_by(
                    MarketingCommand.account_id, MarketingCommand.created_at.desc()
                )
                .distinct(MarketingCommand.account_id)
            )
        )
        .scalars()
        .all()
    )
    for cmd in ultimos:
        if cmd.status != "failed":
            continue  # a agenda pegou → não vista → fecha como "sumiu"
        chave = f"agenda:{cmd.account_id}"
        completo = cmd.completed_at or cmd.updated_at
        if (completo is None or completo < corte) and chave not in abertas:
            continue  # falha velha que ninguém chegou a abrir: não vira passivo

        # Desde quando a agenda não acerta: o último `done` da conta (ou a
        # janela, se ela nunca acertou) — é o que dá sentido ao "N tentativas".
        desde = (
            await session.execute(
                select(func.max(MarketingCommand.created_at)).where(
                    MarketingCommand.account_id == cmd.account_id,
                    MarketingCommand.source == "schedule",
                    MarketingCommand.action.in_(("pause", "resume")),
                    MarketingCommand.status == "done",
                )
            )
        ).scalar() or corte
        tentativas = (
            await session.execute(
                select(func.count())
                .select_from(MarketingCommand)
                .where(
                    MarketingCommand.account_id == cmd.account_id,
                    MarketingCommand.source == "schedule",
                    MarketingCommand.action.in_(("pause", "resume")),
                    MarketingCommand.status == "failed",
                    MarketingCommand.created_at > desde,
                )
            )
        ).scalar_one()

        acc = contas.get(cmd.account_id)
        conta = _rotulo_conta(acc)
        verbo = _VERBO_AGENDA.get(cmd.action, cmd.action)
        resultado = (cmd.result or "").strip() or "sem mensagem"
        r.contadores["agendas_falhas"] += 1
        await _registrar(
            r,
            agora,
            chave=chave,
            plataforma=cmd.platform,
            conta=conta,
            titulo=(
                f"Agenda não aplicada ({verbo}) — {conta or 'conta removida'}"
            ),
            detalhe=(
                f"A agenda tentou {verbo} {_plural(tentativas, 'vez', 'vezes')} "
                f"desde {_br(desde)} e {_QUEM.get(cmd.executor, _QUEM['browser']).lower()} "
                f"devolveu: {resultado[:300]}"
            ),
            acao=(
                _acao_por_resultado(cmd.result, cmd.executor)
                + " — a agenda tenta de novo sozinha a cada minuto"
            ),
            link=LINK,
            severidade="pessoa",
            precisa_pessoa=True,
            dados={
                "conta_id": str(cmd.account_id),
                "acao": cmd.action,
                "falhas_seguidas": int(tentativas),
                "ultimo_comando_id": str(cmd.id),
                "ultimo_resultado": resultado[:300],
                "desde": desde.isoformat() if desde else None,
                "applied_state": acc.applied_state if acc else None,
            },
        )


async def _em_voo(
    session: AsyncSession,
    r: ouvidoria.Rodada,
    contas: dict[UUID, MarketingAccount],
    *,
    agora: datetime,
    pendente_min: int,
) -> int:
    """Regra 3 — comando que ninguém pegou (`pending`) ou que o executor pegou
    e não respondeu (`claimed`). O `claimed` preso é o caso real de produção:
    ele BLOQUEIA o reconciler e o flash daquela loja (`_has_open_command`),
    ou seja, a agenda simplesmente para sem nenhum erro em lugar nenhum.

    Devolve quantos `pending` do executor do Mac existem (a ocorrência de
    executor offline usa o número pra dizer o que está esperando).
    """
    em_voo = (
        (
            await session.execute(
                select(MarketingCommand)
                .where(MarketingCommand.status.in_(("pending", "claimed")))
                .order_by(MarketingCommand.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    pendentes_browser = 0
    for cmd in em_voo:
        preso = cmd.status == "claimed"
        browser = cmd.executor == "browser"
        if not preso and browser:
            pendentes_browser += 1
        # Pendente conta de `created_at` (o que a operação sente: "era pra ter
        # pausado às 19:00"), não de `updated_at` — que o requeue por cooldown
        # mexe. Preso conta de quando o executor pegou.
        desde = (cmd.claimed_at or cmd.updated_at) if preso else cmd.created_at
        if desde is None:
            continue
        minutos = _minutos(desde, agora)
        if minutos < pendente_min:
            continue
        acc = contas.get(cmd.account_id)
        conta = _rotulo_conta(acc)
        pessoa = minutos > 2 * pendente_min
        if preso:
            estado = "preso no executor" if browser else "preso no worker_marketing_agent"
            acao = ACAO_PRESO_BROWSER if browser else ACAO_FILA_API
            onde = f"pegou às {_br(cmd.claimed_at or cmd.updated_at)} e não respondeu"
            r.contadores["comandos_presos"] += 1
        else:
            estado = "pendente" if browser else "esperando o worker_marketing_agent"
            acao = ACAO_PENDENTE_BROWSER if browser else ACAO_FILA_API
            onde = "ainda não pegou"
            r.contadores["comandos_pendentes"] += 1
        titulo = (
            f"{_rotulo_acao(cmd)} {estado} há {_idade(minutos)} — "
            f"{conta or 'conta removida'}"
        )
        quem = _QUEM.get(cmd.executor, _QUEM["browser"]).lower()
        detalhe = (
            f"Enfileirado às {_br(cmd.created_at)} por "
            f"{_FONTE.get(cmd.source, cmd.source)}; {quem} {onde} · "
            f"tentativas {cmd.attempts}"
        )
        if cmd.result:
            # `pending` COM result = quicou por cooldown da Shopee/ML
            # (services/marketing/commands._requeue) — não é erro, é espera.
            detalhe += f", último retorno: {cmd.result[:200]}"
        await _registrar(
            r,
            agora,
            chave=f"comando:{cmd.id}",
            plataforma=cmd.platform,
            conta=conta,
            titulo=titulo,
            detalhe=detalhe,
            acao=acao,
            link=LINK,
            severidade="pessoa" if pessoa else "baixa",
            precisa_pessoa=pessoa,
            dados={
                "comando_id": str(cmd.id),
                "acao": cmd.action,
                "fonte": cmd.source,
                "executor": cmd.executor,
                "status": cmd.status,
                "idade_min": minutos,
                "tentativas": cmd.attempts,
                "criado_em": cmd.created_at.isoformat() if cmd.created_at else None,
                # Sempre as duas chaves: `registrar` MESCLA `dados`, então uma
                # chave omitida ficaria com o valor da rodada anterior (o
                # `pego_em` de quando ainda era `claimed`, por exemplo).
                "pego_em": cmd.claimed_at.isoformat() if cmd.claimed_at else None,
            },
        )
    return pendentes_browser


async def _executor(
    session: AsyncSession,
    r: ouvidoria.Rodada,
    *,
    agora: datetime,
    offline: timedelta,
    pendentes_browser: int,
) -> str:
    """Regras 4 e 5 — o sinal de vida do executor do Mac. Devolve o pedaço do
    resumo (o estado do executor aparece SEMPRE, mesmo quando está tudo bem:
    é a pergunta que a operação faz primeiro)."""
    hb = (
        await session.execute(
            select(MarketingAgentHeartbeat)
            .where(~MarketingAgentHeartbeat.agent_name.startswith(AGENTE_LOGISTICA_PREFIXO))
            # DESC no Postgres põe NULL na frente: sem o nulls_last uma linha
            # que nunca carimbou `last_seen_at` roubaria o lugar da boa.
            .order_by(MarketingAgentHeartbeat.last_seen_at.desc().nulls_last()).limit(1)
        )
    ).scalar_one_or_none()

    if hb is None or hb.last_seen_at is None:
        # Ambiente sem executor (dev/local, ou marketing desligado) não pode
        # abrir ocorrência urgente: sem loja Shopee com perfil do AdsPower o
        # executor nem faria sentido ali.
        tem_shopee = (
            await session.execute(
                select(func.count())
                .select_from(MarketingAccount)
                .where(
                    MarketingAccount.platform == "shopee",
                    MarketingAccount.adspower_user_id.is_not(None),
                )
            )
        ).scalar_one()
        if not tem_shopee:
            # Vale a pena deixar rastro: sem esta linha alguém procuraria por
            # que o robô nunca cobra o Mac num ambiente sem loja do AdsPower.
            logger.debug("vigia_marketing_comandos_sem_executor")
            return "sem executor configurado"
        await _registrar(
            r,
            agora,
            chave="executor:offline",
            plataforma="shopee",
            titulo="Executor do Mac nunca reportou",
            detalhe=(
                "Nenhum sinal de vida do executor desde que o módulo existe; "
                "enquanto isso nenhum pause/resume/Oferta Relâmpago da Shopee "
                "é aplicado"
            ),
            acao=ACAO_EXECUTOR_OFFLINE,
            link=LINK,
            severidade="urgente",
            precisa_pessoa=True,
            dados={
                "agent_name": hb.agent_name if hb else None,
                "ultimo_sinal_em": None,
                "idade_min": None,
                "versao": None,
                "calibrado": None,
            },
        )
        return "executor nunca reportou"

    minutos = _minutos(hb.last_seen_at, agora)
    info = hb.info or {}
    r.contadores["executor_idade_min"] = minutos
    dados = {
        "agent_name": hb.agent_name,
        "ultimo_sinal_em": hb.last_seen_at.isoformat(),
        "idade_min": minutos,
        "versao": info.get("version"),
        "calibrado": info.get("calibrated"),
    }
    if agora - hb.last_seen_at > offline:
        detalhe = (
            f"Último sinal às {_br(hb.last_seen_at)} (BRT); enquanto isso nenhum "
            "pause/resume/Oferta Relâmpago da Shopee é aplicado"
        )
        if pendentes_browser:
            detalhe += (
                " — há "
                + _plural(pendentes_browser, "comando pendente", "comandos pendentes")
                + " esperando"
            )
        await _registrar(
            r,
            agora,
            chave="executor:offline",
            plataforma="shopee",
            titulo=f"Executor do Mac ({hb.agent_name}) sem sinal há {_idade(minutos)}",
            detalhe=detalhe,
            acao=ACAO_EXECUTOR_OFFLINE,
            link=LINK,
            severidade="urgente",
            precisa_pessoa=True,
            dados=dados,
        )
        return f"executor sem sinal há {_idade(minutos)}"

    r.contadores["executor_ok"] = 1
    partes = ["executor ok"]
    if hb.adspower_ok is False:
        # O AdsPower fechado foi a 2ª causa de falha em produção (482
        # comandos): o executor responde, mas não consegue abrir perfil nenhum.
        partes = ["executor online, AdsPower fechado"]
        await _registrar(
            r,
            agora,
            chave="executor:adspower",
            plataforma="shopee",
            titulo="Executor do Mac online, mas o AdsPower não responde",
            detalhe=(
                f"Sinal de vida às {_br(hb.last_seen_at)} (BRT) com a Local API "
                "do AdsPower fora do ar — todo comando da Shopee vai falhar"
            ),
            acao=ACAO_ADSPOWER,
            link=LINK,
            severidade="pessoa",
            precisa_pessoa=True,
            dados=dados,
        )
    if info.get("calibrated") is False:
        # Trava de segurança do executor (SELECTORS_CALIBRATED): ele conecta,
        # loga e FALHA de propósito. Sem este aviso alguém reautoriza loja por
        # loja atrás de um erro que é só uma variável de ambiente.
        await _registrar(
            r,
            agora,
            chave="executor:trava",
            plataforma="shopee",
            titulo="Executor do Mac com a trava de segurança ligada",
            detalhe=(
                "SELECTORS_CALIBRATED não está 'true' no .env do executor: ele "
                "conecta e recusa aplicar, então TODO comando volta como falho"
            ),
            acao="Virar SELECTORS_CALIBRATED pra true no .env do executor do Mac",
            link=LINK,
            severidade="info",
            precisa_pessoa=False,
            dados=dados,
        )
        partes.append("trava ligada")
    return " · ".join(partes)


async def vigia_marketing_comandos_run(session: AsyncSession) -> dict:
    """Uma varredura completa dentro de uma `Rodada` da Ouvidoria. Só leitura
    de banco (nada de API de marketplace), então um erro aqui derruba a rodada
    inteira e o `Rodada.__aexit__` grava `ok=False` sem fechar nada — o que é
    o certo: "não consegui olhar" nunca pode virar "sumiu"."""
    async with ouvidoria.Rodada(session, ROBO) as r:
        # Todos os contadores nascem em 0: a rodada gravada tem sempre as
        # mesmas chaves (a tela lê direto) e o dict devolvido também.
        for k in _CONTADORES:
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        pendente_min = int(cfg.get("pendente_min") or _PENDENTE_MIN)
        offline = timedelta(
            minutes=int(cfg.get("executor_offline_min") or _EXECUTOR_OFFLINE_MIN)
        )
        agora = datetime.now(UTC)

        contas = {
            a.id: a
            for a in (await session.execute(select(MarketingAccount))).scalars().all()
        }
        abertas = await _abertas(session)

        await _falhas_de_comando(session, r, contas, abertas, agora=agora)
        await _agendas_nao_aplicadas(session, r, contas, abertas, agora=agora)
        pendentes_browser = await _em_voo(
            session, r, contas, agora=agora, pendente_min=pendente_min
        )
        executor_txt = await _executor(
            session,
            r,
            agora=agora,
            offline=offline,
            pendentes_browser=pendentes_browser,
        )

        # Tudo que a rodada não re-viu, sumiu. Sem `excluir_contas` e sem
        # `prefixo`: a fonte é só o banco — não existe "conta que eu não
        # consegui olhar" — e todas as ocorrências deste robô são de ESTADO
        # (nenhum hook abre nada por fora).
        r.contadores["sumiram"] = await r.fechar_nao_vistas()

        partes = []
        if r.contadores["comandos_falhos"]:
            partes.append(
                _plural(r.contadores["comandos_falhos"], "falho", "falhos")
            )
        if r.contadores["agendas_falhas"]:
            partes.append(
                _plural(r.contadores["agendas_falhas"], "agenda parada", "agendas paradas")
            )
        if r.contadores["comandos_pendentes"]:
            partes.append(
                _plural(r.contadores["comandos_pendentes"], "pendente", "pendentes")
            )
        if r.contadores["comandos_presos"]:
            presos = r.contadores["comandos_presos"]
            partes.append(f"{_plural(presos, 'preso', 'presos')} no executor")
        partes.append(executor_txt)
        r.resumo = " · ".join(partes)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_marketing_comandos_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory
    lock transacional numa sessão SÓ do lock — a `Rodada` commita ao sair e um
    commit soltaria o lock se ele estivesse na mesma sessão. O modo do robô
    NÃO é olhado aqui: o tick do worker é quem sai quando está `desligado`; o
    botão "Rodar agora" roda mesmo desligado (a pessoa pediu)."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        async with session_scope() as session:
            return await vigia_marketing_comandos_run(session)
