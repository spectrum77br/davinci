"""Chamados: réplica e monitoramento — o robô da Ouvidoria da aba Chamados.

Vinicius, 22/09/2026: três buracos da aba que só apareciam quando alguém
abria o chamado na mão pra ver o que tinha acontecido.

1. **A fala nossa que não saiu** (`envio:<chamado_id>`). A réplica, a
   abertura da contestação e a prova adicional da Shopee falham em silêncio:
   a coluna Status passa a dizer "envio falhou", mas ninguém confere chamado
   por chamado. Quem abre a ocorrência é um HOOK no próprio ponto do envio,
   não uma varredura — só lá se sabe o erro exato e a tentativa —, e quem
   fecha é o ponto de SUCESSO do mesmo chamado (`fechar_por_chave`). É UMA
   linha por chamado, sempre sobre a ÚLTIMA fala nossa (o rastro por mensagem
   é o histórico do chamado) — ver `registrar_resultado_envio`.
2. **O caso que a consulta não lê mais** (`consulta:<chamado_id>`). As duas
   varreduras de hora em hora (o monitor do ML em `run_replica_automatica` e
   o `sync_respostas` das devoluções) engoliam a falha num `logger.warning`:
   o chamado continua na aba com o status de dias atrás e ninguém sabe que
   está defasado. O hook conta as falhas SEGUIDAS dentro da própria
   ocorrência (`dados.falhas_seguidas`) — a primeira é `info` (soluço de
   rede é normal e se resolve sozinho) e só depois de
   `consultas_falhas_seguidas` passadas vira coisa de gente.
3. **O Encerrado parado** (`encerrado:<chamado_id>`). A plataforma decidiu e
   ninguém concluiu pelo Resolver — o chamado fica sem custo e sem situação,
   e o resultado do mês não fecha. Esse é de ESTADO: a RODADA a cada 30 min
   lista, re-vê e fecha sozinha quando a pessoa conclui.

## Por que a rodada também RECONCILIA as duas primeiras
`envio:` e `consulta:` nascem por hook e só o sucesso as fecha, mas existem
caminhos em que o problema some sem passar por envio nenhum: a pessoa mexeu
na devolução e o `garantir_chamado` devolveu a abertura pra fila do robô, o
chamado foi concluído, ou a linha foi excluída. Sem a reconciliação a
ocorrência ficaria aberta pra sempre cobrando algo que já não existe. O
`fechar_nao_vistas` roda SÓ com `prefixo='encerrado:'`: sem o prefixo ele
mataria como "sumiu", a cada 30 min, justamente as falhas de envio que
ninguém tratou.

## O hook nunca derruba o envio
Tudo dentro de `begin_nested` (savepoint) com try/except, e ele nem começa se
a linha do robô não existe: quem sincroniza o catálogo é o worker
(`startup`), e o hook roda também no processo da API (réplica manual,
`/agent/resultado`) — logo depois de um deploy a FK ainda não existe e o
INSERT envenenaria a transação da pessoa.

## …e nem pesa nas varreduras
`consulta_ok` é chamado pra TODO chamado lido com sucesso, que é o caso normal.
Por isso as duas varreduras abrem uma `Passada` (`abrir_passada`) antes do
laço: 2 consultas por varredura em vez de ~4 por chamado, e o hook do caminho
feliz sai sem tocar no banco quando aquele chamado não tem `consulta:` aberta.

O robô nasce `silencioso` (`RoboDef.modo_padrao`): registra no painel e não
manda Threema até o Vinicius ligar na tela. A varredura antiga
(`chamados_pendencias.varrer`, abertura presa há 12 h) continua mandando o
Threema dela — as duas convivem até ela migrar pra Ouvidoria.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    Chamado,
    ChamadoMensagem,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
)
from app.services import chamados as chamados_svc
from app.services import ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "vigia_chamados"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x76636861  # ascii "vcha"

_TZ_BR = ZoneInfo("America/Sao_Paulo")

# Prefixos das três famílias de ocorrência. `envio:` e `consulta:` são de
# EVENTO (hook abre, sucesso fecha); `encerrado:` é de ESTADO (a rodada re-vê
# e o `fechar_nao_vistas` fecha) — por isso o prefixo importa.
PREFIXO_ENVIO = "envio:"
PREFIXO_CONSULTA = "consulta:"
PREFIXO_ENCERRADO = "encerrado:"
# 22/09: caso aberto na TELA que ninguém está lendo. Também de ESTADO — e existe
# justamente porque o silêncio não dispara hook nenhum: se o robô simplesmente
# NÃO pedir a fila de leitura, nenhuma falha acontece pra ser registrada. Era esse
# o buraco original (o caso do 292592 ficou 3 dias sem ninguém notar).
PREFIXO_LEITURA = "leitura:"

# Padrões quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com estes mesmos valores — services/ouvidoria.ROBOS).
_ENCERRADO_DIAS = 3
# Caso de tela sem leitura confirmada há mais que isto vira ocorrência. Folga
# generosa em cima da cadência de 24 h do caso frio: o alvo é "ninguém está
# lendo", não "atrasou uma rodada".
_LEITURA_PARADA_HORAS = 36
_CONSULTAS_FALHAS_SEGUIDAS = 3
# Depois de mais TANTAS falhas além do limite a ocorrência da consulta sobe de
# `baixa` pra `pessoa`: 3 h não é problema de credencial, 6 h já é.
_CONSULTA_FALHAS_ATE_URGIR = 3

ACAO_ENVIO = "Abrir o chamado e reenviar, ou responder à mão na plataforma"
ACAO_ABERTURA = (
    "Abrir o chamado e reenviar, ou abrir no Seller Center/formulário da plataforma "
    "e anotar o protocolo no chamado"
)
ACAO_CONSULTA = (
    "Conferir a integração da conta em Sistema › Integrações e o nº do caso; "
    "consultar à mão na plataforma"
)
ACAO_ENCERRADO = "Concluir pelo botão Resolver (custo + situação)"
ACAO_LEITURA = (
    "Abrir o caso na plataforma e ver se respondeu — e conferir se o robô de leitura "
    "está pedindo a fila (POST /api/chamados/agent/leitura)"
)

# Rótulo da fala que não saiu, pelo `tipo` da mensagem.
_ROTULO_TIPO = {
    "abertura": "Abertura",
    "replica": "Réplica",
    "replica_auto": "Réplica automática",
}
# Prefixo do texto da prova adicional da Shopee (= chamados_devolucao_sync.
# PROVA_PREFIXO). Ela é uma `replica`, mas no painel "Prova não enviada" é o
# que a operação entende; copiado aqui pra não puxar aquele módulo no hook.
_PROVA_PREFIXO = "Prova adicional enviada à Shopee"

_NOME_PLATAFORMA = {
    "ml": "Mercado Livre",
    "shopee": "Shopee",
    "tiktok": "TikTok Shop",
    "amazon": "Amazon",
}
_NOME_CANAL = {
    "api": "pela API",
    "robo": "pelo robô do navegador",
    "manual": "canal manual",
}
# Texto do status oficial final no detalhe do Encerrado.
_TEXTO_FINAL = {
    chamados_svc.STATUS_GANHAMOS: "ganhamos",
    chamados_svc.STATUS_PERDEMOS: "perdemos",
}
# Erro que NÃO é "envio falhou": o motivo deixou de abrir chamado nessa
# plataforma (chamados_devolucao: devolucao_motivo_sem_chamado). A abertura
# falha de vez de propósito, não há o que reenviar.
_ERRO_SEM_CHAMADO = "devolucao_motivo_sem_chamado"


# ─── leitura do robô ───────────────────────────────────────────────────────


async def _robo_ativo(session: AsyncSession) -> OuvidoriaRobo | None:
    """A linha do robô quando ele pode registrar agora; senão None.

    Duas perguntas numa: `ouvidoria.ativo` é o contrato (modo != `desligado`;
    `silencioso` registra e cala, o aviso é decidido no `avisar_pendentes`), e
    a LINHA precisa existir por causa da FK de `ouvidoria_ocorrencias` — quem
    sincroniza o catálogo é o worker, e o hook roda também no processo da API
    (réplica manual, `/agent/resultado`). Logo depois de um deploy a linha
    pode não existir e, sem esta guarda, o INSERT estouraria levando junto a
    transação da réplica da pessoa."""
    if not await ouvidoria.ativo(session, ROBO):
        return None
    return await session.get(OuvidoriaRobo, ROBO)


def _config(robo: OuvidoriaRobo | None) -> dict:
    return ouvidoria.config_do_robo(robo, ROBO)


@dataclass
class Passada:
    """O que os hooks de consulta precisam saber UMA vez por varredura.

    As duas varreduras de hora em hora (`run_replica_automatica` e
    `sync_respostas`) chamam `consulta_ok` pra CADA chamado lido — o caminho
    felizÍSSIMO, em que não há nada pra fechar. Sem isto, cada chamado custava
    um SELECT em `ouvidoria_robos` (o `modo` não fica no identity map: a
    consulta pede só a coluna), um `session.get` do robô, um SAVEPOINT (flush
    incondicional) e um SELECT de ocorrência — ~4 idas ao banco por chamado,
    por passada, pra confirmar que está tudo bem.

    Com a Passada: 2 consultas por VARREDURA. `robo` None = robô desligado (ou
    linha ainda não criada num deploy) → os hooks saem na hora; `abertas` são
    as chaves `consulta:` abertas agora, e quem não está lá não precisa de
    savepoint nenhum."""

    robo: OuvidoriaRobo | None
    abertas: set[str] = field(default_factory=set)


async def abrir_passada(session: AsyncSession) -> Passada:
    """Resolve o robô e carrega as `consulta:` abertas — chamada UMA vez, no
    começo de cada varredura, e passada aos hooks (`passada=`)."""
    robo = await _robo_ativo(session)
    if robo is None:
        return Passada(robo=None)
    chaves = (
        (
            await session.execute(
                select(OuvidoriaOcorrencia.chave).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                    OuvidoriaOcorrencia.chave.startswith(PREFIXO_CONSULTA),
                )
            )
        )
        .scalars()
        .all()
    )
    return Passada(robo=robo, abertas=set(chaves))


# ─── texto da ocorrência ───────────────────────────────────────────────────


def _curto(texto: str | None, n: int) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _hora_br(dt: datetime | None) -> str | None:
    return dt.astimezone(_TZ_BR).strftime("%d/%m %H:%M") if dt else None


def _plat(ch: Chamado) -> str | None:
    """`chamado.plataforma` normalizada (ml | shopee | tiktok | …), como o
    resto da aba faz. Import tardio: `chamados_devolucao` é pesado e puxa os
    clients das plataformas — o hook não pode pagar isso no boot."""
    from app.services.chamados_devolucao import plataforma_de  # lazy: ele importa chamados

    plat = plataforma_de(ch.plataforma)
    return plat[:20] if plat else None


def _nome_plat(plat: str | None) -> str:
    return _NOME_PLATAFORMA.get(plat or "", "plataforma")


def _referencia(ch: Chamado) -> str:
    """Como a operação chama este chamado: o pedido do Bling, e na falta dele
    o protocolo/claim ou o pedido do marketplace."""
    return (
        (ch.pedido_bling or "").strip()
        or (ch.chamado or "").strip()
        or (ch.pedido_marketplace or "").strip()
    )


def _link(ch: Chamado) -> str:
    """Link pra aba Chamados já procurando este chamado. A página só entende
    `?search=` (ela força mostrar='todos' e o GET faz ilike em pedido_bling,
    pedido_marketplace, conta, produto, sku, chamado e observação) — não
    existe `?id=` pra abrir o histórico direto."""
    ref = _referencia(ch)
    return f"/chamados?search={quote(ref)}" if ref else "/chamados"


def _rotulo_envio(msg: ChamadoMensagem) -> str:
    if (msg.texto or "").startswith(_PROVA_PREFIXO):
        return "Prova"
    return _ROTULO_TIPO.get(msg.tipo or "", "Mensagem")


def _titulo_envio(ch: Chamado, msg: ChamadoMensagem, plat: str | None) -> str:
    return f"{_rotulo_envio(msg)} não enviada — {_nome_plat(plat)} {_referencia(ch)}".strip()


def _detalhe_envio(msg: ChamadoMensagem) -> str:
    erro = (msg.erro or "").strip()
    partes = [chamados_svc.MOTIVO_DO_ERRO.get(erro, erro) or "sem motivo informado"]
    canal = (msg.canal or "").strip()
    if canal:
        partes.append(_NOME_CANAL.get(canal, f"canal {canal}"))
    if (msg.tentativas or 0) > 0:
        partes.append(f"tentativa {msg.tentativas} de {chamados_svc.MAX_TENTATIVAS_ROBO}")
    texto = _curto(msg.texto, 60)
    if texto:
        partes.append(f"texto: {texto}")
    return " · ".join(partes)


# ─── hooks (ocorrências de EVENTO) ─────────────────────────────────────────


async def registrar_resultado_envio(
    session: AsyncSession, ch: Chamado, msg: ChamadoMensagem | None
) -> None:
    """HOOK no ponto do envio de uma fala NOSSA (abertura, réplica, réplica
    automática, prova da Shopee): `falhou` abre a ocorrência `envio:`,
    `enviada` fecha a que estivesse aberta.

    A chave é UMA por chamado (`envio:<chamado_id>`) e ela segue sempre a
    ÚLTIMA fala nossa, seja ela qual for — o título diz qual é (Réplica,
    Abertura, Prova). Então uma prova que sobe DEPOIS de uma réplica que
    falhou fecha a linha das duas: é a mesma leitura da coluna Status da aba
    ("a nossa última fala não saiu") e é o que a reconciliação da rodada
    confere (`_envio_sumiu`). Não é um rastro por mensagem — o histórico
    mensagem a mensagem, com status e erro de cada uma, está no chamado.

    O que NÃO conta como "não enviada":
    - `pendente` — é retry/fila do robô, a tarefa ainda está com ele (a presa
      há 12 h é da `chamados_pendencias.varrer`);
    - `registrada` — canal manual, ou a abertura cancelada porque o chamado
      já estava Encerrado: não havia envio;
    - erro em `ERROS_ACOMPANHADOS` — disputa já contestada à mão, prazo
      esgotado, caso encerrado, ou o robô assumiu por outro caminho: o
      acompanhamento já segue esses, não é falha de envio;
    - `devolucao_motivo_sem_chamado` — o motivo deixou de abrir chamado
      nessa plataforma; a abertura falha de propósito.

    Best-effort: nunca levanta e nunca derruba o envio (savepoint)."""
    if msg is None:
        return
    try:
        robo = await _robo_ativo(session)
        if robo is None:
            return
        chave = f"{PREFIXO_ENVIO}{ch.id}"
        if msg.status == "enviada":
            async with session.begin_nested():
                await ouvidoria.fechar_por_chave(session, ROBO, chave)
            return
        if msg.status != "falhou":
            return
        erro = (msg.erro or "").strip()
        if erro in chamados_svc.ERROS_ACOMPANHADOS or erro == _ERRO_SEM_CHAMADO:
            return
        agora = datetime.now(UTC)
        plat = _plat(ch)
        async with session.begin_nested():
            await ouvidoria.registrar(
                session,
                ROBO,
                chave,
                titulo=_titulo_envio(ch, msg, plat),
                plataforma=plat,
                conta=(ch.conta or "")[:120] or None,
                pedido=(ch.pedido_bling or "")[:80] or None,
                detalhe=_detalhe_envio(msg),
                acao=ACAO_ABERTURA if msg.tipo == "abertura" else ACAO_ENVIO,
                link=_link(ch),
                severidade="pessoa",
                precisa_pessoa=True,
                dados={
                    "mensagem_id": str(msg.id),
                    "tipo": msg.tipo,
                    "canal": msg.canal,
                    "erro": erro or None,
                    "motivo": chamados_svc.MOTIVO_DO_ERRO.get(erro),
                    "tentativas": msg.tentativas or 0,
                    "autor": msg.autor_nome,
                    "texto_curto": _curto(msg.texto, 120) or None,
                    "falhou_em": agora.isoformat(),
                    "protocolo": (ch.chamado or "").strip() or None,
                },
                agora=agora,
            )
    except Exception as e:  # noqa: BLE001 — o vigia nunca derruba o envio
        logger.warning(
            "vigia_chamados_hook_falhou",
            hook="envio",
            chamado_id=str(getattr(ch, "id", "")),
            err=str(e)[:200],
        )


async def registrar_falha_consulta(
    session: AsyncSession,
    ch: Chamado,
    *,
    plat: str | None,
    erro: str,
    varredura: str,
    passada: Passada | None = None,
) -> None:
    """HOOK nas varreduras de hora em hora (`monitor_ml` e `sync_respostas`):
    a consulta do caso não leu. Conta as falhas SEGUIDAS na própria ocorrência
    (o sucesso a fecha, então a próxima falha começa de novo em 1):

    - antes de `consultas_falhas_seguidas`: `info`, sem pessoa — fica no
      painel e ninguém é avisado (soluço de rede se resolve sozinho);
    - a partir dele: `baixa` com pessoa (o status da aba está defasado);
    - mais `_CONSULTA_FALHAS_ATE_URGIR` passadas: `pessoa` (não é soluço, é
      credencial ou nº de caso errado).

    `passada` (quando a varredura abriu uma) poupa o SELECT do robô a cada
    chamado — ver `Passada`.

    Best-effort: nunca levanta (a exceção do caller pode ter deixado a sessão
    abortada — aí nem o savepoint salva, e tudo bem)."""
    try:
        robo = passada.robo if passada is not None else await _robo_ativo(session)
        if robo is None:
            return
        limite = int(_config(robo).get("consultas_falhas_seguidas") or _CONSULTAS_FALHAS_SEGUIDAS)
        chave = f"{PREFIXO_CONSULTA}{ch.id}"
        if passada is not None:
            # A partir de agora ESTE chamado tem `consulta:` aberta: é o que
            # faz o `consulta_ok` de uma passada futura ter o que fechar.
            passada.abertas.add(chave)
        agora = datetime.now(UTC)
        async with session.begin_nested():
            aberta = await ouvidoria._aberta(session, ROBO, chave)  # noqa: SLF001
            n = int((aberta.dados or {}).get("falhas_seguidas") or 0) + 1 if aberta else 1
            nome = _nome_plat(plat)
            ref = _referencia(ch)
            if n < limite:
                severidade, precisa = "info", False
                titulo = f"Consulta do caso falhou ({n}×) — {nome} {ref}".strip()
            else:
                severidade = "pessoa" if n >= limite + _CONSULTA_FALHAS_ATE_URGIR else "baixa"
                precisa = True
                titulo = f"Caso sem consulta há {n} passadas — {nome} {ref}".strip()
            await ouvidoria.registrar(
                session,
                ROBO,
                chave,
                titulo=titulo,
                plataforma=(plat or "")[:20] or None,
                conta=(ch.conta or "")[:120] or None,
                pedido=(ch.pedido_bling or "")[:80] or None,
                detalhe=(
                    f"{(erro or '').strip()[:300]} · "
                    + (
                        "o robô não conseguiu ler o caso na tela da plataforma"
                        if varredura == "leitura_robo"
                        else "a varredura de :25 não consegue ler o caso"
                    )
                    + "; o status da aba pode estar defasado"
                ),
                acao=ACAO_CONSULTA,
                link=_link(ch),
                severidade=severidade,
                precisa_pessoa=precisa,
                dados={
                    "falhas_seguidas": n,
                    "ultimo_erro": (erro or "").strip()[:300] or None,
                    "ultima_falha_em": agora.isoformat(),
                    "varredura": varredura,
                    "plataforma": plat,
                    "protocolo": (ch.chamado or "").strip() or None,
                },
                agora=agora,
            )
    except Exception as e:  # noqa: BLE001 — o vigia nunca derruba a varredura
        logger.warning(
            "vigia_chamados_hook_falhou",
            hook="consulta",
            chamado_id=str(getattr(ch, "id", "")),
            err=str(e)[:200],
        )


async def consulta_ok(
    session: AsyncSession, ch: Chamado, *, passada: Passada | None = None
) -> None:
    """A varredura conseguiu ler o caso: fecha a `consulta:` que estivesse
    aberta. É o que zera o contador — a próxima falha abre linha nova com 1.

    Com `passada`, o caso normal (nada aberto pra este chamado) sai SEM tocar
    no banco: é o hook que roda pra todo chamado de toda passada horária."""
    try:
        chave = f"{PREFIXO_CONSULTA}{ch.id}"
        if passada is not None:
            if passada.robo is None or chave not in passada.abertas:
                return
        elif await _robo_ativo(session) is None:
            return
        async with session.begin_nested():
            if await ouvidoria.fechar_por_chave(session, ROBO, chave) and passada is not None:
                passada.abertas.discard(chave)
    except Exception as e:  # noqa: BLE001 — o vigia nunca derruba a varredura
        logger.warning(
            "vigia_chamados_hook_falhou",
            hook="consulta_ok",
            chamado_id=str(getattr(ch, "id", "")),
            err=str(e)[:200],
        )


# ─── rodada (ocorrência de ESTADO + reconciliação) ─────────────────────────


async def _encerrados_parados(
    session: AsyncSession, *, corte: datetime
) -> list[Chamado]:
    """Chamados no estado Encerrado (status oficial final, ninguém concluiu)
    parados desde antes do `corte`. O "desde" é o `status_plataforma_at` — o
    mesmo que a coluna Status mostra —, com `updated_at` de reserva pra linha
    antiga que ficou sem ele."""
    return list(
        (
            await session.execute(
                select(Chamado)
                .where(
                    Chamado.resolvido.is_(False),
                    Chamado.status_plataforma.in_(sorted(chamados_svc.STATUS_FINAIS)),
                    func.coalesce(Chamado.status_plataforma_at, Chamado.updated_at) <= corte,
                )
                .order_by(Chamado.status_plataforma_at)
            )
        )
        .scalars()
        .all()
    )


async def _leitura_parada(
    session: AsyncSession, *, corte: datetime
) -> list[Chamado]:
    """Casos abertos na TELA, vivos, que ninguém confirmou ler desde `corte`
    (inclusive os que NUNCA foram lidos: `leitura_robo_at IS NULL`).

    Esta é a rede que enxerga o silêncio. As ocorrências de `consulta:` nascem de
    hook — alguém tentou e falhou. Aqui não há tentativa nenhuma pra falhar: se o
    robô de leitura não existir, não for ligado ou parar de pedir a fila, os casos
    simplesmente ficam parados. Sem isto, o buraco que originou tudo (resposta da
    Shopee de 19/09 invisível até 22/09) voltaria calado."""
    return list(
        (
            await session.execute(
                select(Chamado)
                .where(
                    chamados_svc.CASO_DE_TELA_SQL,
                    Chamado.resolvido.is_(False),
                    chamados_svc.NAO_ENCERRADO_SQL,
                    func.coalesce(func.trim(Chamado.chamado), "") != "",
                    or_(
                        Chamado.leitura_robo_at.is_(None),
                        Chamado.leitura_robo_at < corte,
                    ),
                    # Caso recém-aberto ainda não teve chance de ser lido.
                    Chamado.created_at < corte,
                )
                .order_by(Chamado.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _com_instrucao_pendente(
    session: AsyncSession, ids: list[UUID]
) -> set[UUID]:
    """Dos `ids`, os que têm instrução pendente pro robô — a última
    `instrucao` mais nova que a última `analise` (mesma regra da listagem,
    routers/chamados._instrucao_pendente). Na aba esses estão em Análise
    Robô, não em Encerrado: uma pessoa JÁ agiu e mandou o robô fazer algo, e
    cobrar o Resolver aqui seria cobrar duas vezes."""
    if not ids:
        return set()
    rows = (
        await session.execute(
            select(
                ChamadoMensagem.chamado_id,
                ChamadoMensagem.tipo,
                func.max(ChamadoMensagem.created_at),
            )
            .where(
                ChamadoMensagem.chamado_id.in_(ids),
                ChamadoMensagem.tipo.in_(("instrucao", "analise")),
            )
            .group_by(ChamadoMensagem.chamado_id, ChamadoMensagem.tipo)
        )
    ).all()
    por_chamado: dict[UUID, dict[str, datetime]] = {}
    for chamado_id, tipo, quando in rows:
        por_chamado.setdefault(chamado_id, {})[tipo] = quando
    return {
        cid
        for cid, m in por_chamado.items()
        if m.get("instrucao") is not None
        and (m.get("analise") is None or m["analise"] < m["instrucao"])
    }


def _detalhe_encerrado(ch: Chamado, desde: datetime | None) -> str:
    partes = [
        f"Plataforma {_TEXTO_FINAL.get(ch.status_plataforma or '', 'encerrou sem decisão')}"
        f"{' em ' + _hora_br(desde) if desde else ''}"
    ]
    if ch.valor_sugerido is not None:
        partes.append(f"robô sugere {chamados_svc.resultado_texto(ch.valor_sugerido)}")
    partes.append(f"origem {ch.origem}, canal {ch.canal}")
    # A data é a da PLATAFORMA (ml_quando / o "desde" da coluna Status) e pode
    # ser dias anterior ao momento em que o DaVinci leu — por isso um chamado
    # às vezes já nasce aqui com vários dias.
    partes.append("a data é a que a plataforma informou, o mesmo desde da coluna Status")
    return " · ".join(partes)


async def _ultimas_falas_nossas(
    session: AsyncSession, ids: list[UUID]
) -> dict[UUID, ChamadoMensagem]:
    """chamado_id → a última fala NOSSA dele, em UMA consulta (`DISTINCT ON`).

    A reconciliação roda 48×/dia sobre todas as abertas do robô e o passivo
    histórico de réplicas falhas não é pequeno: uma consulta por ocorrência
    eram O(n) idas ao banco pra uma lista que já está em memória."""
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(ChamadoMensagem)
                .where(
                    ChamadoMensagem.chamado_id.in_(ids),
                    ChamadoMensagem.direcao == "enviada",
                )
                .distinct(ChamadoMensagem.chamado_id)
                .order_by(ChamadoMensagem.chamado_id, ChamadoMensagem.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {m.chamado_id: m for m in rows}


def _id_da_chave(chave: str) -> UUID | None:
    try:
        return UUID(chave.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _envio_sumiu(ch: Chamado | None, ultima: ChamadoMensagem | None) -> bool:
    """A `envio:` deste chamado ainda faz sentido? Ela fala da mesma coisa que
    a coluna Status: "a nossa ÚLTIMA fala não saiu". Sumiu quando o chamado
    foi excluído ou concluído, quando não há mais fala nossa nenhuma, ou
    quando a última já não está `falhou` — caso do `garantir_chamado`, que
    devolve a abertura pra `pendente` quando a pessoa mexe na devolução (a
    tarefa voltou pro robô) — ou está com erro que o acompanhamento segue.

    `ultima` vem pronta do `_ultimas_falas_nossas` (uma consulta pro lote)."""
    if ch is None or ch.resolvido:
        return True
    if ultima is None or ultima.status != "falhou":
        return True
    return (ultima.erro or "").strip() in chamados_svc.ERROS_ACOMPANHADOS


def _consulta_sumiu(ch: Chamado | None) -> bool:
    """A `consulta:` some quando o chamado sai do escopo das varreduras:
    excluído, concluído, com status final (19/09: a plataforma já decidiu e
    ninguém consulta mais) — ou, desde 22/09, quando ele passa a ser caso de TELA.

    O último caso importa: os casos de tela tinham `consulta:` aberta justamente
    pelas falhas horárias contra a API que a marca `chamado_de_tela` veio calar.
    Sem isto a ocorrência ficaria aberta pra sempre, cobrando com `precisa_pessoa`
    uma varredura que não existe mais pra aquele chamado."""
    return (
        ch is None
        or ch.resolvido
        or ch.status_plataforma in chamados_svc.STATUS_FINAIS
        or bool(ch.chamado_de_tela)
    )


async def _reconciliar(session: AsyncSession, agora: datetime) -> int:
    """Fecha como "sumiu" as `envio:`/`consulta:` cujo motivo já não existe.
    Obrigatória: elas nascem por hook e o `fechar_nao_vistas` não as julga (o
    prefixo as protege), então sem isto ficariam abertas pra sempre quando o
    problema se resolve por fora do envio."""
    abertas = (
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
    # Duas consultas pro lote inteiro em vez de duas por ocorrência: os
    # chamados das chaves e, só pras `envio:`, a última fala nossa de cada um.
    de_envio = [o for o in abertas if o.chave.startswith(PREFIXO_ENVIO)]
    de_consulta = [o for o in abertas if o.chave.startswith(PREFIXO_CONSULTA)]
    ids = {
        o.chave: cid
        for o in de_envio + de_consulta
        if (cid := _id_da_chave(o.chave)) is not None
    }
    chamados = (
        {
            ch.id: ch
            for ch in (
                await session.execute(select(Chamado).where(Chamado.id.in_(set(ids.values()))))
            )
            .scalars()
            .all()
        }
        if ids
        else {}
    )
    falas = await _ultimas_falas_nossas(
        session, [cid for o in de_envio if (cid := ids.get(o.chave)) is not None]
    )
    n = 0
    for o in de_envio + de_consulta:
        cid = ids.get(o.chave)
        ch = chamados.get(cid) if cid else None
        sumiu = (
            _envio_sumiu(ch, falas.get(cid) if cid else None)
            if o.chave.startswith(PREFIXO_ENVIO)
            else _consulta_sumiu(ch)
        )
        if sumiu and await ouvidoria.fechar_por_chave(session, ROBO, o.chave, agora=agora):
            n += 1
    return n


async def _contar_abertas(session: AsyncSession, prefixo: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(OuvidoriaOcorrencia)
                .where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                    OuvidoriaOcorrencia.chave.startswith(prefixo),
                )
            )
        ).scalar()
        or 0
    )


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


async def vigia_chamados_run(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    """Uma rodada: lista os Encerrados parados, reconcilia as ocorrências de
    hook e avisa o que estiver pendente. Commit fica com a `Rodada`."""
    agora = agora or datetime.now(UTC)
    async with ouvidoria.Rodada(session, ROBO) as r:
        # Zerados na entrada pra a rodada sem achado nenhum também aparecer no
        # painel com os números (e não com o contador ausente).
        for k in (
            "encerrados", "novas", "persistem", "sumiram", "envios_falhos",
            "consultas_falhando", "reconciliadas", "com_instrucao", "leitura_parada",
        ):
            r.contadores[k] = 0
        cfg = _config(await session.get(OuvidoriaRobo, ROBO))
        encerrado_dias = int(cfg.get("encerrado_dias") or _ENCERRADO_DIAS)
        corte = agora - timedelta(days=encerrado_dias)

        rows = await _encerrados_parados(session, corte=corte)
        com_instrucao = await _com_instrucao_pendente(session, [ch.id for ch in rows])
        for ch in rows:
            if ch.id in com_instrucao:
                r.contadores["com_instrucao"] += 1
                continue
            desde = ch.status_plataforma_at or ch.updated_at
            dias = max(1, (agora - desde).days) if desde else encerrado_dias
            r.contadores["encerrados"] += 1
            plat = _plat(ch)
            linha = await r.registrar(
                chave=f"{PREFIXO_ENCERRADO}{ch.id}",
                titulo=f"Encerrado há {_plural(dias, 'dia', 'dias')} esperando o resolver",
                plataforma=plat,
                conta=(ch.conta or "")[:120] or None,
                pedido=(ch.pedido_bling or "")[:80] or None,
                detalhe=_detalhe_encerrado(ch, desde),
                acao=ACAO_ENCERRADO,
                link=_link(ch),
                severidade="baixa",
                precisa_pessoa=True,
                dados={
                    "status_plataforma": ch.status_plataforma,
                    "desde": desde.isoformat() if desde else None,
                    "dias": dias,
                    "valor_sugerido": (
                        str(ch.valor_sugerido) if ch.valor_sugerido is not None else None
                    ),
                    "protocolo": (ch.chamado or "").strip() or None,
                    "origem": ch.origem,
                    "canal": ch.canal,
                    "pedido_marketplace": (ch.pedido_marketplace or "").strip() or None,
                },
                agora=agora,
            )
            if linha.fechada_em is None and linha.aberta_em == agora:
                r.contadores["novas"] += 1
            else:
                r.contadores["persistem"] += 1

        # 22/09: caso de tela que ninguém leu. Ocorrência de ESTADO, como a de
        # Encerrado — e a única que enxerga o robô de leitura ausente.
        corte_leitura = agora - timedelta(hours=_LEITURA_PARADA_HORAS)
        for ch in await _leitura_parada(session, corte=corte_leitura):
            desde = ch.leitura_robo_at or ch.created_at
            horas = max(1, int((agora - desde).total_seconds() // 3600)) if desde else 0
            r.contadores["leitura_parada"] += 1
            nunca = ch.leitura_robo_at is None
            linha = await r.registrar(
                chave=f"{PREFIXO_LEITURA}{ch.id}",
                titulo=(
                    "Caso aberto na tela sem NENHUMA leitura"
                    if nunca
                    else f"Caso aberto na tela sem leitura há {_plural(horas, 'hora', 'horas')}"
                ),
                plataforma=_plat(ch),
                conta=(ch.conta or "")[:120] or None,
                pedido=(ch.pedido_bling or "")[:80] or None,
                detalhe=(
                    f"Protocolo {(ch.chamado or '').strip()} — a plataforma pode ter respondido "
                    "na tela e o painel não saberia. Nenhuma API lê este caso: quem lê é o robô "
                    "de leitura."
                ),
                acao=ACAO_LEITURA,
                link=_link(ch),
                severidade="baixa" if nunca else "info",
                precisa_pessoa=nunca,
                dados={
                    "protocolo": (ch.chamado or "").strip() or None,
                    "chamado_url": (ch.chamado_url or "").strip() or None,
                    "leitura_robo_at": (
                        ch.leitura_robo_at.isoformat() if ch.leitura_robo_at else None
                    ),
                    "horas_sem_leitura": horas,
                    "canal": ch.canal,
                    "origem": ch.origem,
                },
                agora=agora,
            )
            if linha.fechada_em is None and linha.aberta_em == agora:
                r.contadores["novas"] += 1
            else:
                r.contadores["persistem"] += 1

        # Só os prefixos de ESTADO são julgados pela rodada: as de hook (envio:,
        # consulta:) morreriam como "sumiu" a cada 30 min.
        r.contadores["sumiram"] = await r.fechar_nao_vistas(prefixo=PREFIXO_ENCERRADO)
        r.contadores["sumiram"] += await r.fechar_nao_vistas(prefixo=PREFIXO_LEITURA)
        r.contadores["reconciliadas"] = await _reconciliar(session, agora)
        r.contadores["envios_falhos"] = await _contar_abertas(session, PREFIXO_ENVIO)
        r.contadores["consultas_falhando"] = await _contar_abertas(session, PREFIXO_CONSULTA)

        r.resumo = " · ".join(
            [
                _plural(r.contadores["encerrados"], "encerrado parado", "encerrados parados"),
                _plural(r.contadores["envios_falhos"], "envio falho", "envios falhos"),
                _plural(r.contadores["consultas_falhando"], "consulta", "consultas") + " falhando",
                _plural(r.contadores["leitura_parada"], "caso de tela", "casos de tela")
                + " sem leitura",
            ]
        )

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_chamados_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory
    lock transacional numa sessão SÓ do lock (a de trabalho commita na saída da
    `Rodada`, e um commit soltaria o lock). O modo do robô NÃO é olhado aqui: o
    tick do worker é quem sai quando está `desligado`; o botão "Rodar agora"
    roda mesmo desligado (a pessoa pediu)."""
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
            return await vigia_chamados_run(session)
