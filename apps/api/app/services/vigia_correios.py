"""Ocorrência grave nos Correios — o que a Logística já sabe, no painel.

Eduardo (10/09/2026): o pedido 295070 foi APREENDIDO pela Secretaria da
Fazenda e isso ficou numa coluna da tela esperando alguém reparar. Desde
então `logistica_track_sync.aplicar_leitura` carimba `problema_correios` na
PRIMEIRA leitura grave e manda um Threema na hora (`avisar_graves`) — o que
resolve o "ninguém viu", mas não o "ninguém tratou": o Threema passa, a
coluna fica, e semanas depois o pacote continua apreendido sem chamado.

Este robô (22/09/2026) é a outra metade: transforma o que a Logística já
sabe em ocorrência da Ouvidoria, que fica ABERTA até o caso terminar de
verdade e sai da tela sozinha quando terminar. Ele não conserta nada e não
fala com ninguém de fora — só lê o banco e o Redis a cada 15 min, dois
minutos depois do `logistica_track_sync` (cron :07/:22/:37/:52 × :05/:20/
:35/:50), então lê o que aquela rodada acabou de commitar.

## As três coisas que ele olha

1. **`pedido:<pedido_bling>`** (ou `linha:<id>` na linha manual sem número
   do Bling) — toda linha da Logística com `problema_correios_em` preenchido
   que ainda NÃO terminou. Terminou = situação do Bling Entregue / Cancelado
   / Resolvido / Perdimento (é o `situacao_bling.nome` que a ingestão
   realinha) ou `entregue_em` carimbado pelo 17track. Cancelado, Resolvido e
   Perdimento nem chegam aqui — o `cleanup_finalizados` apaga a linha e a
   ocorrência fecha como "sumiu" por não ser mais vista; Entregue fica 90
   dias na tabela, por isso é filtrado aqui na mão.

   NÃO fecha porque a localização atual perdeu a palavra grave:
   `problema_correios` nunca é limpo (de propósito — é a memória do evento),
   e o evento seguinte de uma apreensão ("objeto em análise de destinação")
   não tem palavra grave nenhuma enquanto o pacote segue retido.

   Com `logistica.chamado` preenchido a ocorrência CONTINUA aberta, mas cai
   pra `baixa` sem pessoa e ganha "(chamado aberto)" no título: apreensão e
   extravio duram semanas, e sem isso o "Tratado" de hoje viraria um Threema
   novo amanhã (`CARENCIA_TRATADA` de 24 h) até o caso acabar. A família "nova
   tentativa" (não entregue, endereço incorreto, recusado) nasce direto em
   `baixa` sem pessoa — é o dia a dia dos Correios, e `problema_correios`
   nunca é limpo (ver `_EVENTOS_NOVA_TENTATIVA`).

2. **`17track:saldo`** — a flag que o sync liga quando o 17track recusa por
   falta de crédito. Sem saldo nenhum rastreio novo é registrado e a coluna
   Localização inteira congela no proxy do marketplace: é `urgente`.

3. **`rastreio:<codigo>`** — número que o 17track recusou por motivo que não
   é saldo (formato, transportadora, 5xx) e que o sync deixou 1 dia em
   quarentena. `baixa` e sem pessoa: a quarentena também guarda erro
   transitório do serviço e se desfaz sozinha em 24 h — com pessoa, um 5xx
   num lote de 120 mandaria 120 linhas no Threema.

## Redis mudo ≠ problema resolvido

`sem_quota_desde()` e `em_quarentena()` do sync engolem a exceção do Redis
(devolvem None / set vazio) porque lá o certo é seguir o trabalho. Aqui isso
seria mentira: uma queda do Redis fecharia o saldo e TODAS as ocorrências de
rastreio como "sumiu" e reabriria tudo depois — histórico sujo e re-aviso.
Por isso o robô lê o Redis pelos wrappers daqui, que devolvem `REDIS_FALHOU`
em vez de "não tem nada lá"; nesse caso as ocorrências que dependem do Redis
são só RE-VISTAS (não fecham) e as de pedido continuam sendo julgadas
normalmente — elas vêm do banco, que respondeu.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Logistica, OuvidoriaOcorrencia, OuvidoriaRobo
from app.redis_client import redis
from app.services import logistica_rules, logistica_track, ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.logistica_track_sync import (
    CHAVE_SEM_QUOTA,
    JANELA_DIAS,
    PREFIXO_QUARENTENA,
    _na_varredura,
    _num,
)

logger = structlog.get_logger()

ROBO = "vigia_correios"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x76636F72  # ascii "vcor"

_TZ_BR = ZoneInfo("America/Sao_Paulo")

# As duas famílias de ocorrência que dependem do REDIS (e não do banco) — a
# rodada precisa saber quais re-ver quando o Redis não responde.
CHAVE_SALDO = "17track:saldo"
PREFIXO_RASTREIO = "rastreio:"

# Situação do Bling em que o caso acabou e o problema deixa de ser problema.
# É o mesmo conjunto que o resto da Logística usa (logistica_track_sync
# SITUACOES_ENCERRADAS_AMAZON, logistica_ingest._CLEANUP_SQL): `status_bling`
# guarda o NOME da situação do Bling, não o id.
SITUACOES_FINAIS = frozenset({"entregue", "cancelado", "resolvido", "perdimento"})

# Rótulos de `logistica.plataforma` (o que está gravado na coluna) → chave
# canônica da ocorrência e da aba da tela de Logística.
_MAGALU_PLATAFORMAS = frozenset({"magalu", "magazine luiza"})

ACAO_APREENSAO = "Acionar os Correios / SEFAZ e abrir chamado na plataforma"
ACAO_CHAMADO = "Abrir chamado na plataforma"
ACAO_AGUARDAR = "Aguardar o pacote voltar e conferir"
ACAO_NOVA_TENTATIVA = (
    "Acompanhar a nova tentativa dos Correios; se o pacote voltar, aguardar e conferir"
)
ACAO_SALDO = "Recarregar créditos em 17track.net (entrar na conta → Quota)"
ACAO_RASTREIO = (
    "Conferir o código de rastreio no Bling/na plataforma; se estiver certo, o robô "
    "tenta de novo sozinho em 1 dia"
)

# Palavra-chave que `logistica_track.evento_grave` devolve → (rótulo em
# linguagem de operação, o que a pessoa faz). Avaria e sinistro entram junto
# com extravio/roubo na AÇÃO (é tudo chamado na plataforma) mas mantêm o
# rótulo próprio, que é o que a pessoa vai procurar no pedido.
_EVENTOS: dict[str, tuple[str, str]] = {
    "apreendid": ("Apreensão/retenção fiscal", ACAO_APREENSAO),
    "extraviad": ("Extravio", ACAO_CHAMADO),
    "roubo": ("Roubo/furto", ACAO_CHAMADO),
    "roubad": ("Roubo/furto", ACAO_CHAMADO),
    "furtad": ("Roubo/furto", ACAO_CHAMADO),
    "sinistro": ("Roubo/furto", ACAO_CHAMADO),
    "avaria": ("Avaria", ACAO_CHAMADO),
    "danificad": ("Avaria", ACAO_CHAMADO),
    "devolvido ao remetente": ("Devolvido ao remetente", ACAO_AGUARDAR),
    "devolucao ao remetente": ("Devolvido ao remetente", ACAO_AGUARDAR),
    "nao entregue": ("Não entregue", ACAO_NOVA_TENTATIVA),
    "endereco incorreto": ("Endereço incorreto", ACAO_NOVA_TENTATIVA),
    "recusad": ("Recusado pelo destinatário", ACAO_NOVA_TENTATIVA),
}
# O texto tem `problema_correios_em` mas a palavra não casa mais com nenhuma
# das graves (mudou a redação dos Correios, ou veio do proxy do marketplace).
_EVENTO_DESCONHECIDO = ("Ocorrência grave", ACAO_CHAMADO)

# A família "nova tentativa" fica no painel em `baixa` e SEM pessoa: tentativa
# de entrega frustrada, endereço a confirmar e recusa são eventos do dia a dia
# dos Correios, que se resolvem na tentativa seguinte (ou viram "devolvido ao
# remetente", e AÍ tem pessoa). `problema_correios` nunca é limpo, então com
# `pessoa` cada uma dessas viraria cobrança permanente no Threema até o pedido
# chegar a situação final. `pessoa` fica pra apreensão, extravio, roubo,
# avaria e devolvido ao remetente.
_EVENTOS_NOVA_TENTATIVA = frozenset({"nao entregue", "endereco incorreto", "recusad"})

# Cada evento cai numa família, e cada família é uma caixinha na config do robô
# (Editar, em Ouvidoria › Robôs). Desmarcada, a rodada nem olha as linhas
# daquele tipo — e o `fechar_nao_vistas` fecha como "sumiu" as ocorrências que
# já estavam abertas ali, sem ninguém precisar limpar na mão. Vinicius,
# 22/09/2026: "eu quero só apreensão/retenção, extravio, roubo, furto e avaria;
# o resto não queria mais que ele olhasse por enquanto".
_CONFIG_DA_FAMILIA: dict[str, str] = {
    "apreendid": "olhar_apreensao",
    "extraviad": "olhar_extravio",
    "roubo": "olhar_roubo_furto",
    "roubad": "olhar_roubo_furto",
    "furtad": "olhar_roubo_furto",
    "sinistro": "olhar_roubo_furto",
    "avaria": "olhar_avaria",
    "danificad": "olhar_avaria",
    "devolvido ao remetente": "olhar_devolvido_ao_remetente",
    "devolucao ao remetente": "olhar_devolvido_ao_remetente",
    "nao entregue": "olhar_nova_tentativa",
    "endereco incorreto": "olhar_nova_tentativa",
    "recusad": "olhar_nova_tentativa",
}
# Texto com `problema_correios_em` cuja palavra não casa com nenhuma família
# (redação nova dos Correios, ou veio do proxy do marketplace). Caixinha
# própria porque é justamente o que pode esconder uma apreensão rebatizada.
_CONFIG_DESCONHECIDO = "olhar_ocorrencia_desconhecida"


def _olha(cfg: dict, evento: str | None) -> bool:
    """A caixinha dessa família está marcada? Chave ausente = olha (robô que
    ainda não recebeu a config nova não pode parar de vigiar sozinho)."""
    chave = _CONFIG_DA_FAMILIA.get(evento or "", _CONFIG_DESCONHECIDO)
    return bool(cfg.get(chave, True))

# Contadores de uma rodada (ouvidoria_rodadas.contadores), em linguagem de
# operação: linhas_graves = pedidos com ocorrência viva; finais_ignoradas =
# já Entregue/entregue_em (o problema acabou); novas/persistem = ocorrências;
# sumiram = fechadas nesta rodada; quarentena = rastreios recusados;
# fora_do_filtro = tipo que as caixinhas do Editar mandaram não olhar;
# sem_saldo = 1 quando o 17track está sem crédito; redis_falhou = 1 quando o
# Redis não respondeu (a rodada não julgou saldo nem quarentena).
_CONTADORES = (
    "linhas_graves", "finais_ignoradas", "fora_do_filtro", "novas", "persistem",
    "sumiram", "quarentena", "sem_saldo", "redis_falhou",
)


# ─── Redis: "não tem nada lá" tem que ser diferente de "não respondeu" ─────


class _RedisMudo:
    """Sentinela de "o Redis não respondeu". O tipo existe pra o `isinstance`
    da rodada — `None` e `set()` são respostas VÁLIDAS do Redis (sem saldo
    nenhum, nada em quarentena) e não podem significar a mesma coisa."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — só pra log/depuração
        return "REDIS_FALHOU"


REDIS_FALHOU = _RedisMudo()


async def _sem_saldo_desde() -> str | None | _RedisMudo:
    """ISO de quando o 17track começou a recusar por saldo, None (tem saldo)
    ou REDIS_FALHOU. É o `sem_quota_desde()` do sync com a falha VISÍVEL."""
    try:
        return await redis.get(CHAVE_SEM_QUOTA)
    except Exception as e:  # noqa: BLE001 — Redis fora do ar não derruba a rodada
        logger.warning("vigia_correios_redis_falhou", onde="saldo", err=str(e)[:200])
        return REDIS_FALHOU


async def _em_quarentena(numeros: list[str]) -> set[str] | _RedisMudo:
    """Quais destes números o sync deixou de castigo por 1 dia, ou
    REDIS_FALHOU. Mesma leitura do `em_quarentena()` do sync, idem."""
    if not numeros:
        return set()
    try:
        vals = await redis.mget([f"{PREFIXO_QUARENTENA}{n}" for n in numeros])
    except Exception as e:  # noqa: BLE001 — idem
        logger.warning("vigia_correios_redis_falhou", onde="quarentena", err=str(e)[:200])
        return REDIS_FALHOU
    return {n for n, v in zip(numeros, vals, strict=False) if v}


# ─── leitura da linha da Logística ─────────────────────────────────────────


def _hora_br(dt: datetime | None) -> str | None:
    return dt.astimezone(_TZ_BR).strftime("%d/%m %H:%M") if dt else None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _de_iso(txt: str | None) -> datetime | None:
    """ISO gravado no Redis → datetime (ou None se vier torto — o valor é
    escrito por outro processo e não pode derrubar a rodada)."""
    try:
        return datetime.fromisoformat(txt) if txt else None
    except ValueError:
        return None


def _plataforma_codigo(rotulo: str | None) -> str | None:
    """Rótulo gravado em `logistica.plataforma` ("Mercado Livre") → a chave
    canônica que a ocorrência e a aba da tela usam ("ml"). Rótulo que não
    conhecemos vira None: a ocorrência ABRE do mesmo jeito (o pacote está
    apreendido de qualquer jeito), só fica sem filtro de plataforma."""
    p = (rotulo or "").strip().lower()
    if p in logistica_rules._ML_PLATAFORMAS:
        return "ml"
    if p in logistica_rules._SHOPEE_PLATAFORMAS:
        return "shopee"
    if p in logistica_rules._TIKTOK_PLATAFORMAS:
        return "tiktok"
    if p in logistica_rules._AMAZON_PLATAFORMAS:
        return "amazon"
    if p in _MAGALU_PLATAFORMAS:
        return "magalu"
    return None


def _finalizada(row: Logistica) -> bool:
    """O caso acabou? Situação final no Bling ou entrega confirmada pelo
    17track. É a ÚNICA coisa que fecha a ocorrência de um pedido."""
    return (row.status_bling or "").strip().lower() in SITUACOES_FINAIS or (
        row.entregue_em is not None
    )


def _chave(row: Logistica) -> str:
    """`pedido:<numero do Bling>` — é por ele que a equipe fala do pedido e
    é ele que a busca da tela casa. Linha manual sem número do Bling cai no
    id da linha, pra não colidir com todas as outras sem número."""
    numero = (row.pedido_bling or "").strip()
    return f"pedido:{numero}" if numero else f"linha:{row.id}"


def _link(codigo: str | None, pedido_bling: str | None) -> str:
    """Botão "Logística" da tela de ocorrências: aba da plataforma + busca
    pelo número do Bling. O `onMounted` de pages/logistica.vue lê os dois
    (`?tab=`/`?q=`) desde 22/09; aba que a tela não conhece — magalu, por
    exemplo — é ignorada lá e a busca vale do mesmo jeito."""
    params = []
    if codigo:
        params.append(f"tab={codigo}")
    if pedido_bling and pedido_bling.strip():
        params.append(f"q={quote(pedido_bling.strip())}")
    return "/logistica" + (f"?{'&'.join(params)}" if params else "")


def _titulo(row: Logistica, rotulo: str, chamado: str) -> str:
    """"Apreensão/retenção fiscal — Correios AD828496989BR". Sem rastreio
    (linha manual) fica só o rótulo do evento."""
    titulo = rotulo
    rastreio = (row.rastreio or "").strip()
    if rastreio:
        servico = (row.servico_envio or "").strip() or "Correios"
        titulo = f"{rotulo} — {servico} {rastreio}"
    return f"{titulo} (chamado aberto)" if chamado else titulo


def _detalhe(row: Logistica, chamado: str) -> str:
    """Tudo que a pessoa precisa ler sem sair do balão da ocorrência (a tela
    só mostra alguns campos de `dados`, e rastreio/localização não estão
    entre eles).

    A localização só é rotulada "Correios" quando `localizacao_at` existe: o
    proxy do ML sobrescreve a coluna quando não há carimbo físico, e chamar
    aquilo de leitura dos Correios enganaria quem vai abrir o chamado. O
    EVENTO, esse, vem sempre de `problema_correios`, que é estável."""
    partes: list[str] = []
    loc = (row.localizacao or "").strip()
    if loc:
        fonte = (
            f"Correios, lido {_hora_br(row.localizacao_at)}"
            if row.localizacao_at
            else "informada pelo marketplace"
        )
        partes.append(f"Última localização: {loc} ({fonte})")
    if row.problema_correios:
        visto = _hora_br(row.problema_correios_em) or "—"
        partes.append(f"Ocorrência vista em {visto}: {row.problema_correios}")
    if row.status_bling:
        partes.append(f"Situação no Bling: {row.status_bling}")
    if chamado:
        partes.append(f"Chamado: {chamado}")
    return (" · ".join(partes) + ".") if partes else ""


async def _aberta(session: AsyncSession, chave: str) -> OuvidoriaOcorrencia | None:
    return (
        await session.execute(
            select(OuvidoriaOcorrencia).where(
                OuvidoriaOcorrencia.robo_chave == ROBO,
                OuvidoriaOcorrencia.chave == chave,
                OuvidoriaOcorrencia.fechada_em.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _abertas_com_prefixo(
    session: AsyncSession, prefixo: str
) -> list[OuvidoriaOcorrencia]:
    return list(
        (
            await session.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                    OuvidoriaOcorrencia.chave.startswith(prefixo),
                )
            )
        )
        .scalars()
        .all()
    )


def _contar(r: ouvidoria.Rodada, oco: OuvidoriaOcorrencia, agora: datetime) -> None:
    """Linha nova desta rodada ou problema que persiste? (`registrar` devolve
    a fechada quando a pessoa ignorou/tratou há pouco — essa não conta.)"""
    if oco.fechada_em is None and oco.aberta_em == agora:
        r.contadores["novas"] += 1
    else:
        r.contadores["persistem"] += 1


# ─── os três olhares da rodada ─────────────────────────────────────────────


async def _graves(r: ouvidoria.Rodada, agora: datetime, cfg: dict) -> None:
    """Pedido com ocorrência grave viva. Sem janela de data: o `cleanup` da
    ingestão já tira Cancelado/Resolvido/Perdimento da tabela, e uma apreensão
    de 3 meses continua sendo uma apreensão — uma janela aqui faria a linha
    sair da rodada e o `fechar_nao_vistas` fechar como "sumiu" o pacote que
    continua retido.

    Sem janela e sem LIMIT porque o ÍNDICE faz o recorte: `ix_logistica_
    problema_correios_em` é PARCIAL (`WHERE problema_correios_em IS NOT NULL`),
    então a leitura a cada 15 min é um index scan de dezenas de linhas, não um
    seq scan da `logistica` inteira (migração 0301).

    O filtro do que já terminou continua em Python, no `_finalizada`: é a única
    definição de "acabou" (situação final no Bling OU entrega confirmada) e é
    ele que alimenta o contador `finais_ignoradas` do painel — no SQL, essas
    linhas simplesmente desapareceriam da contagem."""
    rows = (
        (
            await r.session.execute(
                select(Logistica).where(Logistica.problema_correios_em.isnot(None))
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if _finalizada(row):
            # Chegou/cancelou: não é mais problema. Não é vista → o
            # fechar_nao_vistas fecha a ocorrência como "sumiu".
            r.contadores["finais_ignoradas"] += 1
            continue
        evento = logistica_track.evento_grave(row.problema_correios) or (
            logistica_track.evento_grave(row.localizacao)
        )
        if not _olha(cfg, evento):
            # Caixinha desmarcada no Editar: não é vista, então o
            # `fechar_nao_vistas` fecha como "sumiu" o que estava aberto.
            r.contadores["fora_do_filtro"] += 1
            continue
        r.contadores["linhas_graves"] += 1
        rotulo, acao = _EVENTOS.get(evento or "", _EVENTO_DESCONHECIDO)
        chamado = (row.chamado or "").strip()
        codigo = _plataforma_codigo(row.plataforma)
        # Chamado aberto (a operação já agiu) ou família "nova tentativa" (o
        # normal dos Correios, ver `_EVENTOS_NOVA_TENTATIVA`): fica no painel
        # sem cobrar pessoa.
        so_painel = bool(chamado) or (evento in _EVENTOS_NOVA_TENTATIVA)
        oco = await r.registrar(
            chave=_chave(row),
            titulo=_titulo(row, rotulo, chamado),
            plataforma=codigo,
            # As colunas da ocorrência são mais curtas que as da Logística
            # (Text lá, String(120)/String(80) aqui).
            conta=(row.conta or "").strip()[:120] or None,
            pedido=(row.pedido_bling or "").strip()[:80] or None,
            detalhe=_detalhe(row, chamado),
            acao=acao,
            link=_link(codigo, row.pedido_bling),
            # Com chamado aberto a operação já agiu: fica no painel, para de
            # cobrar pessoa (e de re-avisar a cada 24 h depois do "Tratado").
            severidade="baixa" if so_painel else "pessoa",
            precisa_pessoa=not so_painel,
            dados={
                "pedido_marketplace": row.pedido_marketplace,
                "rastreio": row.rastreio,
                "servico_envio": row.servico_envio,
                "localizacao": row.localizacao,
                "localizacao_at": _iso(row.localizacao_at),
                "problema_correios": row.problema_correios,
                "problema_correios_em": _iso(row.problema_correios_em),
                "status_bling": row.status_bling,
                "evento": evento,
                "chamado": chamado or None,
                "entregue_em": _iso(row.entregue_em),
            },
            agora=agora,
        )
        _contar(r, oco, agora)


async def _saldo(r: ouvidoria.Rodada, agora: datetime) -> bool:
    """A flag do 17track sem crédito. Devolve True se o Redis não respondeu
    (aí a ocorrência aberta é só re-vista, nunca fechada por engano)."""
    desde = await _sem_saldo_desde()
    if isinstance(desde, _RedisMudo):
        aberta = await _aberta(r.session, CHAVE_SALDO)
        if aberta is not None:
            r.rever(aberta, agora=agora)
        return True
    if not desde:
        return False
    r.contadores["sem_saldo"] = 1
    quando = _hora_br(_de_iso(desde)) or desde
    oco = await r.registrar(
        chave=CHAVE_SALDO,
        titulo="17track sem saldo — nada mais atualiza",
        plataforma="interno",
        detalhe=(
            f"Sem saldo desde {quando}. Nenhum rastreio novo é registrado e a "
            "Localização dos Correios não atualiza (a coluna mostra o que o "
            "marketplace informa)."
        ),
        acao=ACAO_SALDO,
        link="/logistica",
        severidade="urgente",
        dados={"desde": desde},
        agora=agora,
    )
    _contar(r, oco, agora)
    return False


async def _pendentes_de_registro(session: AsyncSession) -> dict[str, object]:
    """número normalizado → a linha da Logística que espera registro no 17track.

    Mesmo RECORTE do `_alvo` do sync (rastreio dos Correios, dentro da janela
    de dias, na varredura) — inclusive o `_na_varredura` dele, importado pra
    não existirem duas definições de "quem o sync olha" —, mas SÓ com as
    colunas que a ocorrência usa. O `_alvo` traz objetos ORM inteiros da janela
    de 45 dias, e reusá-lo aqui repetia, 2 min depois, a leitura mais cara da
    Logística (o sync roda em :05/:20/:35/:50 e este robô em :07/:22/:37/:52):
    a mesma varredura duas vezes por quarto de hora.

    Os pendentes são o universo certo: só quem foi ao `register` pode estar de
    castigo. Varrer as chaves do Redis traria também os rastreios de DEVOLUÇÃO
    (a quarentena é da conta do 17track, compartilhada com o
    devolucao_rastreio_sync), que não têm linha de Logística onde pendurar a
    ocorrência."""
    rows = (
        await session.execute(
            select(
                Logistica.pedido_bling,
                Logistica.pedido_marketplace,
                Logistica.plataforma,
                Logistica.conta,
                Logistica.status_bling,
                Logistica.rastreio,
                Logistica.rastreio_17track,
                Logistica.entregue_em,
            ).where(
                Logistica.rastreio.isnot(None),
                Logistica.data >= date.today() - timedelta(days=JANELA_DIAS),
            )
        )
    ).all()
    out: dict[str, object] = {}
    for row in rows:
        if not logistica_track.is_correios(row.rastreio):
            continue
        # `_na_varredura` só lê plataforma/status_bling/entregue_em — a Row
        # leve responde a esses três nomes igual à linha ORM.
        if not _na_varredura(row):  # type: ignore[arg-type]
            continue
        numero = _num(row.rastreio)
        if numero and numero != _num(row.rastreio_17track):
            out.setdefault(numero, row)
    return out


async def _quarentena(r: ouvidoria.Rodada, agora: datetime) -> bool:
    """Rastreio que o 17track recusou e o sync deixou 1 dia sem tentar. O
    universo é o dos PENDENTES de registro (`_pendentes_de_registro`). Devolve
    True se o Redis não respondeu."""
    pendentes = await _pendentes_de_registro(r.session)
    presos = await _em_quarentena(sorted(pendentes))
    if isinstance(presos, _RedisMudo):
        for aberta in await _abertas_com_prefixo(r.session, PREFIXO_RASTREIO):
            r.rever(aberta, agora=agora)
        return True
    for numero in sorted(presos):
        row = pendentes[numero]
        r.contadores["quarentena"] += 1
        codigo = _plataforma_codigo(row.plataforma)
        oco = await r.registrar(
            chave=f"{PREFIXO_RASTREIO}{numero}",
            titulo=f"Rastreio recusado pelo 17track — {numero}",
            plataforma=codigo,
            conta=(row.conta or "").strip()[:120] or None,
            pedido=(row.pedido_bling or "").strip()[:80] or None,
            detalhe=(
                "O 17track recusou o número (formato inválido, transportadora "
                "incompatível ou erro do serviço); fica 1 dia sem tentar de novo. "
                "Enquanto isso a Localização é a informada pelo marketplace."
            ),
            acao=ACAO_RASTREIO,
            link=_link(codigo, row.pedido_bling),
            # Só painel: a quarentena pega 5xx transitório do próprio 17track
            # e se desfaz sozinha em 24 h — não vale acordar ninguém.
            severidade="baixa",
            precisa_pessoa=False,
            dados={
                "rastreio": numero,
                "pedido_marketplace": row.pedido_marketplace,
                "status_bling": row.status_bling,
            },
            agora=agora,
        )
        _contar(r, oco, agora)
    return False


def _resumo(contadores: dict) -> str:
    graves = contadores.get("linhas_graves", 0)
    sumiram = contadores.get("sumiram", 0)
    quarentena = contadores.get("quarentena", 0)
    fora = contadores.get("fora_do_filtro", 0)
    partes = [
        f"{graves} grave{'s' if graves != 1 else ''}",
        f"{sumiram} fechou" if sumiram == 1 else f"{sumiram} fecharam",
        f"{quarentena} em quarentena",
        f"17track {'SEM SALDO' if contadores.get('sem_saldo') else 'ok'}",
    ]
    if fora:
        partes.insert(1, f"{fora} fora do filtro")
    if contadores.get("redis_falhou"):
        partes.append("Redis não respondeu")
    return " · ".join(partes)


# ─── rodada ────────────────────────────────────────────────────────────────


async def vigia_correios_run(session: AsyncSession) -> dict:
    """Uma rodada completa. A Rodada commita ao sair; quem chama só garante
    que não há outra rodada junto (advisory lock no `vigia_correios_sweep`).

    Da config o que a rodada usa são as caixinhas "Olhar …" (quais tipos de
    ocorrência vigiar); a `cadencia_min` é do PAINEL — é com ela que a coluna
    Saúde sabe dizer "parado". Não há janela nem teto pra ajustar: a rodada
    relê o estado inteiro (são dezenas de linhas, não milhares) toda vez."""
    async with ouvidoria.Rodada(session, ROBO) as r:
        # Todos os contadores nascem em 0: a rodada gravada tem sempre as
        # mesmas chaves (a tela lê direto) e o dict devolvido também.
        for k in _CONTADORES:
            r.contadores[k] = 0
        agora = datetime.now(UTC)
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)

        await _graves(r, agora, cfg)
        redis_falhou = await _saldo(r, agora)
        # O `or` vem DEPOIS da chamada de propósito: a quarentena é lida
        # mesmo que o saldo tenha falhado (pode ter sido um soluço).
        redis_falhou = await _quarentena(r, agora) or redis_falhou
        if redis_falhou:
            r.contadores["redis_falhou"] = 1

        # O que a rodada não viu, sumiu. As de saldo/rastreio que o Redis
        # deixou sem resposta já foram marcadas como vistas lá em cima, então
        # não fecham; as de pedido são julgadas normalmente (o banco
        # respondeu) — inclusive as `linha:<id>`, que um filtro por prefixo
        # deixaria abertas pra sempre.
        r.contadores["sumiram"] = await r.fechar_nao_vistas()
        r.resumo = _resumo(r.contadores)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_correios_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory
    lock transacional numa sessão SÓ do lock. O modo do robô NÃO é olhado
    aqui: o tick do worker (`vigia_correios_tick`) é quem sai quando está
    `desligado`; o botão "Rodar agora" roda mesmo desligado (a pessoa pediu)."""
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
            return await vigia_correios_run(session)
