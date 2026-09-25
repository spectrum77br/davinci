# ruff: noqa: S608
"""Robô da Margem — a rodada inteira: o robô age e o fiscal confere.

UM ROBÔ SÓ (25/09/2026, pedido do Cairo depois de desligar o fiscal pra
testar e ver a Margem seguir segurando): antes eram duas coisas — o
`margem_auto_hold` (segura/reprova no Bling, com kill-switch no .env) e este
fiscal (só lia, com o botão do painel). Agora a rodada de :15/:45 é uma só
(`vigia_margem_sweep`):

1. números frescos (rebuild do snapshot `verificar_margem`);
2. o robô AGE (`margem_auto_hold.run`) — se o modo não for `desligado`;
3. o fiscal confere (tudo abaixo) e a rodada grava as duas coisas: o que o
   robô fez desde a rodada anterior (lido da auditoria, então conta também o
   que o "atualizar"/auto-refresh da aba e a revisão de hora em hora fizeram)
   e o que ficou pendente.

O modo do painel manda em tudo (ver `margem_auto_hold`): `desligado` não
segura, não reprova, não revisa e não confere; `silencioso` age e confere sem
Threema; `ligado` avisa — o aviso na hora de cada pedido (com o link de
aprovar pelo celular) e o resumo das pendências vão pra MESMA lista "Avisar".

O fiscal, de 22/09 — o que o auto-hold faz e ninguém mais olha:

Vinicius, 22/09/2026. O robô da Margem (`margem_auto_hold`) já segura, reprova
e reavalia pedido sozinho, e avisa no Threema na hora. O que faltava era o
DEPOIS: três buracos que só apareciam quando alguém ia procurar.

1. **Pedido segurado e esquecido.** O hold move o pedido pra Aguardando
   Cancelamento com o pino 'Pendente' — ele continua na aba Margem › Pendentes
   esperando alguém Aprovar ou Reprovar. Se ninguém decide, o pedido fica
   parado no Bling: não gera etiqueta, não gera NF, não vende. O aviso do
   Threema saiu UMA vez, no minuto do hold; depois disso, silêncio.
   → `segurado:<pedido>` abre quando o hold tem mais de `segurado_horas` e
   fecha sozinha no instante em que alguém decide (Aprovar, Reprovar) ou em
   que a própria reavaliação do robô libera o pedido.

2. **O robô não conseguiu mexer no Bling.** Falha de rede, 4xx da situação,
   Bling fora do ar: hoje isso é um `logger.warning` que ninguém lê, e o
   pedido fica no limbo — a Margem acha que ele está segurado, o Bling acha
   que não. → `falha:<pedido>` é aberta por HOOK no próprio
   `margem_auto_hold` (no ponto da falha) e fechada no ponto de SUCESSO da
   mesma operação. A rodada é só a rede de segurança: confere o espelho e
   fecha a `falha:` quando o pedido já está na situação que a operação
   pretendia (alguém resolveu na mão, ou um tick posterior conseguiu).

3. **Margem fora do normal (> 60%).** O `_alertar_margem_alta` manda UM
   Threema por pedido e grava a auditoria de dedup — se o custo continuar
   errado no cadastro, nada volta a cobrar. → `margem_alta:<pedido>` fica no
   painel enquanto a margem estiver alta e fecha sozinha quando o custo é
   corrigido (ou o pedido sai de "Em aberto"). O Threema e o dedup atuais
   continuam exatamente como estão: esta rodada NÃO manda mensagem por conta
   própria e NÃO olha a auditoria `alerta_margem_alta` — se olhasse, a linha
   sumiria da consulta depois do 1º envio e o `fechar_nao_vistas` mataria a
   ocorrência como "sumiu" na rodada seguinte.

## Onde cada coisa é lida (e por que daí)
- O **pino** do hold vem de `bling_orders` (situacao 83955 + status 'Pendente'
  + `aprovado_por IS NULL`), não do snapshot: o espelho da Margem é
  reconstruído inteiro a cada 30 min e um pedido antigo pode não ter linha
  lá. É a mesma fonte que a reavaliação dos reprovados usa.
- A **idade** do hold vem da última auditoria automática do pedido
  (`margem_audit`, acao='situacao' → '83955', origem 'margens_auto',
  `mudado_por IS NULL`): é o único carimbo de QUANDO o robô segurou.
- Os **números** (margem oficial, mínima, lucro, produto, plataforma/conta)
  vêm do snapshot `verificar_margem` por LEFT JOIN — pedido sem linha lá
  ainda abre ocorrência (está preso no Bling de verdade), só sem os números.
- Os **gatilhos** (margem baixa, saldo divergente) são importados de
  `routers/margens.py` por import tardio, como o `margem_auto_hold` faz: a
  definição de "pendente" mora lá e o robô tem que dizer o mesmo que a aba.

A CONFERÊNCIA É SÓ LEITURA do lado da Margem: ela não escreve em
`bling_orders`, `verificar_margem` nem `margem_audit`, e não chama Bling nem
Threema por conta própria — só as tabelas `ouvidoria_*` mudam. Quem mexe em
pedido é o passo 2 (`margem_auto_hold.run`), com as regras e os avisos dele.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import OuvidoriaOcorrencia, OuvidoriaRobo, OuvidoriaRodada
from app.services import margem_auto_hold, ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.margem_auto_hold import (
    MARGEM_ALTA_LIMIAR,
    SITUACAO_AGUARDANDO_CANCELAMENTO,
    SITUACAO_EM_ABERTO,
)
from app.services.verificar_margem import (
    BLING_ORDERS_TABLE,
    SNAPSHOT_TABLE,
    qualified_table,
    rebuild_all,
)

logger = structlog.get_logger()

ROBO = "vigia_margem"

# Advisory lock do sweep (namespace SYNC compartilhado). "mgrm".
_SWEEP_LOCK_KEY = 0x6D67726D

# Padrão quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com este mesmo valor — services/ouvidoria.ROBOS).
_SEGURADO_HORAS = 24

_MARGEM_AUDIT_TABLE = qualified_table("margem_audit")
_TZ_BR = ZoneInfo("America/Sao_Paulo")

# "O que o robô fez" é contado desde o início da rodada anterior; robô que
# ficou parado (desligado, worker fora) não despeja dias de ações na primeira
# rodada de volta — a janela nunca passa disto.
_JANELA_ACOES_MAX = timedelta(hours=2)

# Prefixos das chaves. Ocorrência de ESTADO (a rodada re-vê e fecha pelo
# `fechar_nao_vistas`) vs ocorrência de EVENTO (`falha:`, aberta por hook e
# fechada pelo ponto de sucesso) — por isso o fechamento SEMPRE vai com
# prefixo: sem ele a rodada mataria como "sumiu" justamente a falha que
# ninguém tratou.
PREFIXO_SEGURADO = "segurado:"
PREFIXO_MARGEM_ALTA = "margem_alta:"
PREFIXO_FALHA = "falha:"

ACAO_DECIDIR = "Aprovar ou reprovar o pedido na aba Margem › Pendentes"
ACAO_CUSTO = "Conferir o custo do produto no cadastro"
ACAO_CONFERIR_BLING = "Conferir a situação do pedido na Margem e no Bling"

# Operação que o hook grava em `dados['operacao']` → situação/pino que o
# pedido tem quando essa operação FINALMENTE deu certo. É o que a rodada
# confere pra fechar uma `falha:` que já se resolveu por fora (alguém mexeu
# na mão, ou um tick posterior conseguiu) sem esperar um novo sucesso.
#
# O PINO importa porque 83955 sozinho não distingue os dois desfechos que o
# `margem_auto_hold._hold_one` escreve em `bling_orders.status`: 'Pendente' é
# "segurado esperando decisão" e 'Reprovado' é "reprovado" (sai da aba
# Pendentes na hora). Sem o pino, uma `falha:` de REPROVAR fechava como
# resolvida quando o pedido estava apenas segurado — cobrando nada, e o pedido
# nunca reprovado. Já SEGURAR aceita qualquer pino: reprovado também está
# segurado (é o hold mais forte), e exigir 'Pendente' deixaria a ocorrência
# aberta depois de alguém reprovar o pedido na mão.
_OPERACOES = {
    "segurar": (str(SITUACAO_AGUARDANDO_CANCELAMENTO), None),
    "reprovar": (str(SITUACAO_AGUARDANDO_CANCELAMENTO), "Reprovado"),
    "liberar": (str(SITUACAO_EM_ABERTO), "Aprovado"),
    "voltar_pendente": (None, "Pendente"),
}
_TITULO_OPERACAO = {
    "segurar": "Não consegui segurar o pedido {pedido} no Bling",
    "reprovar": "Não consegui reprovar o pedido {pedido} no Bling",
    "liberar": "Não consegui liberar o pedido {pedido} no Bling",
    "voltar_pendente": "Não consegui devolver o pedido {pedido} para a aba Pendentes",
}


def titulo_falha(operacao: str | None, pedido: str) -> str:
    """Título da ocorrência de falha, na voz do robô ("não consegui")."""
    modelo = _TITULO_OPERACAO.get(
        operacao or "", "Não consegui mexer no pedido {pedido} no Bling"
    )
    return modelo.format(pedido=pedido)


# ─── consultas ─────────────────────────────────────────────────────────────


def _saldo_como_em_aberto() -> str:
    """`_ATTENTION_SALDO_SQL` com o recorte de situação neutralizado.

    O gatilho de saldo só é triado em Em aberto / etiqueta (6, 21, 83965), e o
    pedido segurado está em 83955 — lido como está, ele seria SEMPRE falso e o
    robô diria "pendente de análise" pra todo segurado, escondendo o motivo
    real do hold. A pergunta aqui é a mesma da reavaliação dos reprovados ("o
    que ainda pende neste pedido?"), então o recorte de situação sai só desta
    expressão — mesma cirurgia (e mesma trava de manutenção) de
    `margem_auto_hold._reavaliacao_julgar_sql`."""
    from app.routers.margens import (
        _ATTENTION_SALDO_SQL,
        _SITUACOES_SALDO_DIVERGENTE_IN,
    )

    guarda = f"(v.situacao IN ({_SITUACOES_SALDO_DIVERGENTE_IN})"
    if guarda not in _ATTENTION_SALDO_SQL:  # pragma: no cover — trava de manutenção
        raise RuntimeError("_ATTENTION_SALDO_SQL mudou de forma; ajuste o vigia_margem")
    return _ATTENTION_SALDO_SQL.replace(guarda, "(TRUE", 1)


def _segurados_sql() -> str:
    """Pedidos que o robô segurou e ninguém decidiu, com os números da aba.

    Import tardio dos gatilhos pela mesma razão do `margem_auto_hold`: a
    definição canônica de "pendente" mora em routers/margens.py, que importa
    serviços — importar aqui em cima criaria ciclo.

    Ordem das CTEs importa: `preso` nasce em `bling_orders` (seletivo: são
    poucos 83955 com pino 'Pendente') e só depois cada pedido busca a SUA
    última auditoria de hold pelo índice de `pedido_bling`. O caminho
    inverso varreria a auditoria inteira, que só cresce.

    `aprovado_por IS NULL`: Aprovar/Reprovar no clique grava o autor mesmo
    quando o Bling não é tocado, então autor preenchido = pessoa já decidiu.
    O JOIN LATERAL é INNER de propósito — 83955 sem auditoria do robô é o
    83955 de OUTRO fluxo (controle de estoque, que grava status NULL, ou um
    cancelamento feito no painel do Bling), e aí não há hold nosso a cobrar.
    """
    from app.routers.margens import (
        _ATTENTION_MARGEM_SQL,
        _LUCRO_OFICIAL_SQL,
        _MARGEM_OFICIAL_SQL,
    )

    saldo = _saldo_como_em_aberto()
    return f"""
        WITH preso AS (
            SELECT bo.numero              AS pedido_bling,
                   MAX(bo.bling_id)       AS bling_id
            FROM {BLING_ORDERS_TABLE} bo
            WHERE bo.situacao = '{SITUACAO_AGUARDANDO_CANCELAMENTO}'
              AND bo.status = 'Pendente'
              AND bo.aprovado_por IS NULL
              AND bo.bling_id IS NOT NULL
              AND bo.numero IS NOT NULL
            GROUP BY bo.numero
        ), hold AS (
            SELECT p.pedido_bling, p.bling_id, a.created_at AS segurado_em
            FROM preso p
            JOIN LATERAL (
                SELECT a.created_at
                FROM {_MARGEM_AUDIT_TABLE} a
                WHERE a.pedido_bling = p.pedido_bling
                  AND a.acao = 'situacao'
                  AND a.valor_novo = '{SITUACAO_AGUARDANDO_CANCELAMENTO}'
                  AND a.origem = 'margens_auto'
                  AND a.mudado_por IS NULL
                ORDER BY a.created_at DESC
                LIMIT 1
            ) a ON TRUE
            WHERE a.created_at <= :limite
        ), numeros AS (
            SELECT v.pedido_bling,
                   MAX(v.pedido_marketplace)        AS pedido_marketplace,
                   MAX(COALESCE(v.plataforma_bling, v.plataforma_financeiro))
                                                    AS plataforma,
                   MAX(v.loja_nome)                 AS conta,
                   BOOL_OR({_ATTENTION_MARGEM_SQL}) AS margem_baixa,
                   BOOL_OR({saldo}
                           AND v.marketplace_liquido_base_margem_item IS NOT NULL)
                                                    AS saldo_divergente,
                   BOOL_OR({saldo}
                           AND v.marketplace_liquido_base_margem_item IS NULL)
                                                    AS saldo_pendente,
                   -- ×100: o snapshot guarda margem como FRAÇÃO (0.069 =
                   -- 6,9%); o texto da ocorrência mostra em %, como a aba.
                   MIN({_MARGEM_OFICIAL_SQL}) * 100 AS margem,
                   MAX(v.margem_minima) * 100       AS minima,
                   SUM({_LUCRO_OFICIAL_SQL})        AS lucro,
                   string_agg(DISTINCT NULLIF(btrim(v.produto), ''), '; ')
                                                    AS produto
            FROM {SNAPSHOT_TABLE} v
            WHERE v.pedido_bling IN (SELECT pedido_bling FROM hold)
            GROUP BY v.pedido_bling
        )
        SELECT h.pedido_bling, h.bling_id, h.segurado_em,
               n.pedido_marketplace, n.plataforma, n.conta,
               n.margem_baixa, n.saldo_divergente, n.saldo_pendente,
               n.margem, n.minima, n.lucro, n.produto,
               (n.pedido_bling IS NULL) AS sem_snapshot
        FROM hold h
        LEFT JOIN numeros n ON n.pedido_bling = h.pedido_bling
        ORDER BY h.segurado_em
    """


def _margem_alta_sql() -> str:
    """Margem fora do normal — a MESMA forma de
    `margem_auto_hold._alerta_margem_alta_sql`, MENOS o `NOT EXISTS` da
    auditoria.

    Aquele `NOT EXISTS` é o dedup do THREEMA: depois do 1º envio o pedido
    some da consulta pra sempre. Numa ocorrência de estado isso seria fatal —
    a rodada seguinte não re-veria a linha e o `fechar_nao_vistas` a fecharia
    como "sumiu" sem nada ter sido corrigido. Aqui a regra é o estado: a
    ocorrência fica enquanto a margem estiver alta.

    Situação 6 (Em aberto) = a janela de triagem, a mesma do hold: pedido
    novo, cadastro ainda corrigível antes de faturar. MAX = a MAIOR margem
    entre os itens do pedido.
    """
    return f"""
        SELECT v.pedido_bling,
               MAX(v.bling_id)                  AS bling_id,
               MAX(v.pedido_marketplace)        AS pedido_marketplace,
               MAX(COALESCE(v.plataforma_bling, v.plataforma_financeiro))
                                                AS plataforma,
               MAX(v.loja_nome)                 AS conta,
               MAX(v.marketplace_margem) * 100  AS margem,
               SUM(v.marketplace_lucro)         AS lucro,
               string_agg(DISTINCT NULLIF(btrim(v.produto), ''), '; ')
                                                AS produto
        FROM {SNAPSHOT_TABLE} v
        WHERE v.situacao = '{SITUACAO_EM_ABERTO}'
          AND v.marketplace_margem > {MARGEM_ALTA_LIMIAR}
          AND v.pedido_bling IS NOT NULL
        GROUP BY v.pedido_bling
        ORDER BY v.pedido_bling
    """


async def _estado_no_bling(session: AsyncSession, numeros: list[str]) -> dict[str, tuple]:
    """numero do pedido → (situacao, status) do espelho `bling_orders`.

    Primeira linha-item de cada pedido, mesma ordenação de
    `margem_auto_hold._ainda_reprovado_pelo_robo` (o hold escreve situação e
    pino em TODAS as linhas do bling_id, então qualquer uma serve — fixar a
    ordem só evita resposta instável)."""
    if not numeros:
        return {}
    rows = (
        await session.execute(
            text(
                f"""
                SELECT DISTINCT ON (bo.numero) bo.numero, bo.situacao, bo.status
                FROM {BLING_ORDERS_TABLE} bo
                WHERE bo.numero = ANY(:numeros)
                ORDER BY bo.numero, bo.item_index ASC NULLS FIRST
                """
            ),
            {"numeros": numeros},
        )
    ).all()
    return {r.numero: (r.situacao, r.status) for r in rows}


def _acoes_sql() -> str:
    """O que o ROBÔ fez no Bling na janela — pela auditoria dele
    (`origem='margens_auto'`, `mudado_por IS NULL`), a mesma que o
    `margem_auto_hold` grava em cada hold/reprovação/liberação. Pela auditoria
    e não pelo retorno do `run` porque o robô também age fora da rodada: o
    "atualizar" e o auto-refresh da aba Margem e a revisão de hora em hora
    (:35). Por pedido (DISTINCT): a reprovação grava duas linhas (situação e
    pino) e segurar não conta o que foi reprovado."""
    return f"""
        WITH a AS (
            SELECT pedido_bling, acao, valor_antigo, valor_novo
            FROM {_MARGEM_AUDIT_TABLE}
            WHERE origem = 'margens_auto'
              AND mudado_por IS NULL
              AND created_at >= :desde
              AND created_at < :ate
        ), reprovados AS (
            SELECT DISTINCT pedido_bling FROM a
            WHERE acao = 'status' AND valor_novo = 'Reprovado'
        )
        SELECT
            (SELECT COUNT(*) FROM reprovados) AS reprovou,
            (SELECT COUNT(DISTINCT pedido_bling) FROM a
              WHERE acao = 'situacao'
                AND valor_novo = '{SITUACAO_AGUARDANDO_CANCELAMENTO}'
                AND pedido_bling NOT IN (SELECT pedido_bling FROM reprovados))
                                                    AS segurou,
            (SELECT COUNT(DISTINCT pedido_bling) FROM a
              WHERE acao = 'status' AND valor_antigo = 'Reprovado'
                AND valor_novo = 'Aprovado')        AS liberou,
            (SELECT COUNT(DISTINCT pedido_bling) FROM a
              WHERE acao = 'status' AND valor_antigo = 'Reprovado'
                AND valor_novo = 'Pendente')        AS voltou_analise
    """


async def _inicio_da_janela(session: AsyncSession, agora: datetime) -> datetime:
    """Início da rodada anterior deste robô — as ações contadas nesta rodada
    são as de depois dela — limitado a `_JANELA_ACOES_MAX` pra trás."""
    anterior = (
        await session.execute(
            select(func.max(OuvidoriaRodada.iniciada_em)).where(
                OuvidoriaRodada.robo_chave == ROBO,
                OuvidoriaRodada.iniciada_em < agora,
            )
        )
    ).scalar()
    piso = agora - _JANELA_ACOES_MAX
    return max(anterior, piso) if anterior is not None else piso


def operacao_concluida(operacao: str | None, situacao: str | None, status: str | None) -> bool:
    """A operação que tinha falhado já está feita? (fecha a `falha:`)"""
    esperado = _OPERACOES.get(operacao or "")
    if esperado is None:
        return False
    sit, pino = esperado
    if sit is not None and str(situacao or "") != sit:
        return False
    return not (pino is not None and (status or "") != pino)


# ─── texto das ocorrências ─────────────────────────────────────────────────


def _pct(v: float | None) -> str | None:
    """Percentual em pt-BR: inteiro sem casas (`75%`), senão 1 casa (`6,9%`).
    Arredonda ANTES de decidir — o valor chega de `fração × 100` e a dízima
    binária faria 7.000000000000001 virar "7,0%"."""
    if v is None:
        return None
    v = round(float(v), 1)
    return f"{int(v)}%" if v == int(v) else f"{v:.1f}%".replace(".", ",")


def _reais(v: float | None) -> str | None:
    if v is None:
        return None
    s = f"{float(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _hora_br(dt: datetime | None) -> str | None:
    return dt.astimezone(_TZ_BR).strftime("%d/%m %H:%M") if dt else None


def _corta(v: Any, n: int) -> str | None:
    """Campo de texto da ocorrência tem tamanho no banco (plataforma 20,
    conta 120, pedido 80). Nome de loja longo não pode derrubar a rodada."""
    s = str(v).strip() if v is not None else ""
    return s[:n] or None


def motivo_do_hold(r: Any) -> str:
    """Por que o pedido está segurado, no tom do `margem_auto_hold._motivo`.

    Pino 'Pendente' (é o que esta consulta pega) quer dizer "segurado, não
    reprovado": margem baixa reprova direto desde 11/09, então na prática o
    motivo vem do saldo. Ainda assim o gatilho de margem entra na frase — a
    margem pode ter caído DEPOIS do hold, e quem for decidir precisa ver."""
    partes = []
    if r["margem_baixa"]:
        partes.append("margem abaixo do mínimo")
    if r["saldo_divergente"]:
        partes.append("saldo divergente")
    if r["saldo_pendente"]:
        # Nada diverge AINDA: o marketplace não confirmou o repasse.
        partes.append("aguardando saldo da plataforma")
    return " e ".join(partes) or "pendente de análise"


def detalhe_segurado(r: Any, *, horas: int) -> str:
    partes = [f"Segurado pelo robô em {_hora_br(r['segurado_em'])} (há {horas} h)"]
    if r["sem_snapshot"]:
        # Pedido antigo, ou linha que o rebuild do snapshot não trouxe: ele
        # está preso no Bling de verdade, só não temos os números pra mostrar.
        partes.append("sem dados de margem no snapshot")
        return " · ".join(partes) + "."
    partes.append(f"motivo: {motivo_do_hold(r)}")
    if (margem := _pct(r["margem"])) is not None:
        minima = _pct(r["minima"])
        partes.append(f"margem {margem}" + (f" (mínimo {minima})" if minima else ""))
    if (lucro := _reais(r["lucro"])) is not None:
        partes.append(f"lucro {lucro}")
    if produto := (r["produto"] or "").strip():
        partes.append(produto)
    return " · ".join(partes) + "."


def detalhe_margem_alta(r: Any) -> str:
    partes = [f"Margem {_pct(r['margem'])} com o pedido ainda Em aberto"]
    if (lucro := _reais(r["lucro"])) is not None:
        partes.append(f"lucro {lucro}")
    if produto := (r["produto"] or "").strip():
        partes.append(produto)
    return (
        " · ".join(partes)
        + ". Margem tão alta costuma ser custo errado no cadastro do produto — "
        "o pedido NÃO foi alterado."
    )


def link_margem(pedido_bling: str) -> str:
    """Aba Margem já em "Buscar pedido" com este pedido (margem.vue lê
    `?pedido=`; o lookup não filtra situação, então acha o segurado). Caminho
    relativo: o painel da Ouvidoria rotula link que começa com /margem."""
    return f"/margem?pedido={pedido_bling}"


# ─── rodada ────────────────────────────────────────────────────────────────


class _RoboNaoRodou(Exception):  # noqa: N818 — é o texto que a rodada grava
    """O passo que AGE não rodou (snapshot não reconstruiu, ou o `run` caiu
    inteiro). Sobe DENTRO da Rodada depois de a conferência ser gravada, pra a
    rodada sair `falhando` na coluna Saúde sem perder as ocorrências."""


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def resumo_da_rodada(c: dict) -> str:
    """"2 reprovados · 1 segurado · 0 liberados · 3 sem decisão · 0 falhas ·
    1 margem alta": primeiro o que o robô FEZ desde a rodada anterior, depois
    o que ficou pendente (`sem decisão` = segurado há mais de
    `segurado_horas` sem ninguém decidir). "voltou pra análise" só aparece
    quando aconteceu — é raro e alongaria toda linha."""
    partes = [
        _plural(c.get("reprovou", 0), "reprovado", "reprovados"),
        _plural(c.get("segurou", 0), "segurado", "segurados"),
        _plural(c.get("liberou", 0), "liberado", "liberados"),
    ]
    if c.get("voltou_analise"):
        partes.append(f"{c['voltou_analise']} voltou pra análise")
    partes += [
        f"{c.get('segurados', 0)} sem decisão",
        _plural(c.get("falhas_abertas", 0), "falha", "falhas"),
        f"{c.get('margem_alta', 0)} margem alta",
    ]
    return " · ".join(partes)


async def vigia_margem_run(
    session: AsyncSession,
    *,
    agora: datetime | None = None,
    erro_robo: str | None = None,
) -> dict:
    """A conferência dentro de uma `Rodada` da Ouvidoria, com o que o robô
    fez desde a rodada anterior. `erro_robo` = o passo que age não rodou
    (ver `_RoboNaoRodou`): a conferência roda igual e a rodada sai com falha.

    Só leitura do lado da Margem; quem commita é a Rodada (ao sair) e o
    `session_scope` de quem chama."""
    agora = agora or datetime.now(UTC)
    desde = await _inicio_da_janela(session, agora)
    rodada = ouvidoria.Rodada(session, ROBO)
    try:
        await _conferir(session, rodada, agora=agora, desde=desde, erro_robo=erro_robo)
    except _RoboNaoRodou:
        pass  # a Rodada já gravou a falha; o aviso das pendências sai igual
    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    out = {**dict(rodada.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": rodada.resumo}
    if erro_robo:
        out["robo_falhou"] = erro_robo
    return out


async def _conferir(
    session: AsyncSession,
    rodada: ouvidoria.Rodada,
    *,
    agora: datetime,
    desde: datetime,
    erro_robo: str | None,
) -> None:
    async with rodada as r:
        # Todos em 0 primeiro: contador ausente e contador zerado dizem
        # coisas diferentes na coluna de contadores da rodada.
        for k in (
            "reprovou",
            "segurou",
            "liberou",
            "voltou_analise",
            "segurados",
            "segurados_novos",
            "margem_alta",
            "margem_alta_novas",
            "falhas_abertas",
            "falhas_fechadas",
            "sumiram",
        ):
            r.contadores[k] = 0

        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        horas_limite = int(cfg.get("segurado_horas", _SEGURADO_HORAS))

        # 0) O que o robô fez no Bling desde a rodada anterior.
        feitas = (
            (await session.execute(text(_acoes_sql()), {"desde": desde, "ate": agora}))
            .mappings()
            .one()
        )
        for k in ("reprovou", "segurou", "liberou", "voltou_analise"):
            r.contadores[k] = int(feitas[k] or 0)

        # 1) Segurados pelo robô sem decisão há mais de `segurado_horas`.
        segurados = (
            (
                await session.execute(
                    text(_segurados_sql()),
                    {"limite": agora - timedelta(hours=horas_limite)},
                )
            )
            .mappings()
            .all()
        )
        for s in segurados:
            pedido = str(s["pedido_bling"])
            horas = int((agora - s["segurado_em"]).total_seconds() // 3600)
            row = await r.registrar(
                chave=f"{PREFIXO_SEGURADO}{pedido}",
                plataforma=_corta(s["plataforma"], 20),
                conta=_corta(s["conta"], 120),
                # O número do BLING, não o da plataforma: é o que a aba
                # Margem, o link e a operação usam pra achar o pedido. O da
                # plataforma fica em `dados`.
                pedido=_corta(pedido, 80),
                titulo=f"Pedido segurado há {horas} h sem decisão",
                detalhe=detalhe_segurado(s, horas=horas),
                acao=ACAO_DECIDIR,
                link=link_margem(pedido),
                # O robô já protegeu (o pedido não vira etiqueta nem NF);
                # o que falta é alguém decidir — é a definição de
                # `robo_segurou` em models/ouvidoria.
                severidade="robo_segurou",
                precisa_pessoa=True,
                dados={
                    "pedido_marketplace": s["pedido_marketplace"],
                    "bling_id": s["bling_id"],
                    "segurado_em": s["segurado_em"].isoformat(),
                    "horas": horas,
                    "motivo": motivo_do_hold(s) if not s["sem_snapshot"] else None,
                    "margem": _pct(s["margem"]),
                    "minima": _pct(s["minima"]),
                    "lucro": _reais(s["lucro"]),
                    "produto": s["produto"],
                },
                agora=agora,
            )
            r.contadores["segurados"] += 1
            if row.fechada_em is None and row.aberta_em == agora:
                r.contadores["segurados_novos"] += 1

        # 2) Margem fora do normal (> 60%) em Em aberto.
        altas = (await session.execute(text(_margem_alta_sql()))).mappings().all()
        for a in altas:
            pedido = str(a["pedido_bling"])
            row = await r.registrar(
                chave=f"{PREFIXO_MARGEM_ALTA}{pedido}",
                plataforma=_corta(a["plataforma"], 20),
                conta=_corta(a["conta"], 120),
                pedido=_corta(pedido, 80),
                titulo=f"Margem fora do normal ({_pct(a['margem'])}) — confira o custo",
                detalhe=detalhe_margem_alta(a),
                acao=ACAO_CUSTO,
                link=link_margem(pedido),
                # 'baixa' + precisa_pessoa=False: o aviso na hora já é do
                # `margem_auto` (Threema próprio, dedup próprio). Marcar
                # `precisa_pessoa` aqui faria o MESMO pedido avisar duas
                # vezes no celular de quem recebe os dois.
                severidade="baixa",
                precisa_pessoa=False,
                dados={
                    "pedido_marketplace": a["pedido_marketplace"],
                    "bling_id": a["bling_id"],
                    "margem": _pct(a["margem"]),
                    "lucro": _reais(a["lucro"]),
                    "produto": a["produto"],
                },
                agora=agora,
            )
            r.contadores["margem_alta"] += 1
            if row.fechada_em is None and row.aberta_em == agora:
                r.contadores["margem_alta_novas"] += 1

        # 3) Rede de segurança das `falha:` (a rodada NÃO as abre — quem abre
        #    é o hook no ponto da falha). Fecha a que já se resolveu por fora:
        #    alguém mexeu na mão, ou um tick posterior conseguiu e o espelho
        #    já está na situação que a operação pretendia.
        falhas = (
            (
                await session.execute(
                    select(OuvidoriaOcorrencia).where(
                        OuvidoriaOcorrencia.robo_chave == ROBO,
                        OuvidoriaOcorrencia.fechada_em.is_(None),
                        OuvidoriaOcorrencia.chave.like(f"{PREFIXO_FALHA}%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        estados = await _estado_no_bling(
            session, [o.chave[len(PREFIXO_FALHA) :] for o in falhas]
        )
        for o in falhas:
            pedido = o.chave[len(PREFIXO_FALHA) :]
            situacao, status = estados.get(pedido, (None, None))
            if not operacao_concluida((o.dados or {}).get("operacao"), situacao, status):
                continue
            await ouvidoria.fechar_por_chave(session, ROBO, o.chave, agora=agora)
            r.contadores["falhas_fechadas"] += 1
        r.contadores["falhas_abertas"] = len(falhas) - r.contadores["falhas_fechadas"]

        # 4) O que a rodada não viu, sumiu — SEMPRE por prefixo: as `falha:`
        #    são de evento e não passam por `registrar` aqui, então um
        #    fechamento sem prefixo as mataria como "sumiu".
        r.contadores["sumiram"] = await r.fechar_nao_vistas(prefixo=PREFIXO_SEGURADO)
        r.contadores["sumiram"] += await r.fechar_nao_vistas(prefixo=PREFIXO_MARGEM_ALTA)

        r.resumo = resumo_da_rodada(r.contadores)
        if erro_robo:
            # A conferência fica gravada; a rodada sai com falha (Saúde).
            r.resumo = f"o robô não agiu nesta rodada · {r.resumo}"
            await session.commit()
            raise _RoboNaoRodou(erro_robo)


async def vigia_margem_sweep() -> dict:
    """A rodada inteira do Robô da Margem (ver docstring do módulo): números
    frescos → o robô age → o fiscal confere e grava a rodada.

    Quem chama: o `vigia_margem_tick` do worker (:15/:45 — ele mesmo sai
    quando o robô está `desligado`, só reconstruindo o snapshot) e o "Rodar
    agora" do painel, que roda mesmo desligado, como todo robô do painel —
    mas desligado NÃO mexe em pedido: só reconstrói e confere. Snapshot que
    não reconstruiu → o robô não age (os números seriam velhos) e a rodada
    sai com falha.

    Serializado por advisory lock transacional numa sessão SÓ do lock (as de
    trabalho commitam, e um commit soltaria o lock se ele estivesse nelas).
    Cada passo tem sessão própria: o hold commita por pedido e não pode
    conviver com o advisory lock do rebuild nem com a Rodada."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}

        out: dict = {}
        erro_robo: str | None = None
        try:
            async with session_scope() as s:
                out["rebuilt"] = await rebuild_all(s)
        except Exception as e:  # noqa: BLE001 — a conferência roda igual
            logger.warning("verificar_margem_snapshot_failed", error=str(e)[:200])
            erro_robo = f"não consegui atualizar os números da Margem: {str(e)[:200]}"

        async with session_scope() as s:
            modo = await ouvidoria.modo(s, ROBO)
        if modo != "desligado" and erro_robo is None:
            try:
                async with session_scope() as s:
                    out["robo"] = await margem_auto_hold.run(s)
            except Exception as e:  # noqa: BLE001 — a conferência roda igual
                logger.warning("margem_auto_hold_cron_failed", error=str(e)[:200])
                erro_robo = f"o robô caiu antes de terminar: {str(e)[:200]}"

        async with session_scope() as session:
            return {**out, **await vigia_margem_run(session, erro_robo=erro_robo)}
