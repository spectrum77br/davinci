# ruff: noqa: S608
"""Auto-hold da Margem: pendente "Em aberto" vira Aguardando Cancelamento.

Pedido do dono (21/08): "todo pedido que cair em margem pendente para
analisar, já deve mudar automaticamente no bling para aguardando
cancelamento, e escrever na descrição". Ou seja: a linha caiu na aba
Pendentes da Margem e o pedido AINDA está "Em aberto" (situação 6) → o robô
segura o pedido no Bling (situação 83955, que o tira das filas de
etiqueta/NF) e deixa um recado datado nas Observações com o motivo. A
decisão continua humana: a linha PERMANECE na aba Pendentes (a listagem abre
exceção pros segurados — ver o filtro em routers/margens.py) e os botões de
sempre resolvem — Aprovar devolve o pedido pro fluxo (Atendido → Em aberto),
Reprovar confirma o cancelamento.

Por que só situação 6: é a janela pré-etiqueta — a mesma trava do botão
Reprovar. Pedido que já andou (Atendido, Enviado Etiqueta, ...) não é
segurado. E o motivo "frete" fica de fora porque a própria listagem da
Margem o exclui (NOT _ATTENTION_FRETE_SQL): frete só se conhece depois do
envio, não há o que segurar. ML/Shopee/TikTok nunca entram por saldo (01/09):
o saldo da plataforma é a fonte da verdade. Enquanto o líquido real não
sincroniza, a linha fica "aguardando saldo da plataforma" SÓ na aba
(_ATTENTION_SALDO_AGUARDANDO_SQL, 01/09 à noite — sem margem oficial, nada
aprova às cegas); o robô NÃO segura por esse motivo: o WHERE de
_candidatos_sql o exclui explicitamente, como faz com o frete. Antes o robô
chegou a segurar 13 pedidos saudáveis num só dia por "saldo divergente" de
centavos ou por líquido que ainda nem tinha sincronizado; a divergência
segue isenta na origem (_ATTENTION_SALDO_SQL exclui as confiáveis). Margem
baixa continua segurando ML/Shopee/TikTok normalmente — ela só existe com o
líquido real presente (nunca vem de projeção).

Ordem das escritas por pedido: Observações primeiro (GET → compose → PUT,
a mesma caneta do fluxo Logística — preserva o texto existente e não duplica
a linha do dia), situação depois. Se um passo falhar por erro transiente
(rede, 5xx, 429), o pedido fica como está e o próximo tick (cron 30min /
botão) tenta de novo; um pedido com erro não derruba os demais. EXCEÇÃO: se o
Bling recusar o PUT das Observações com 4xx, é validação da VENDA — o PUT
reenvia o pedido inteiro e o Bling revalida tudo (caso real 291676: erro 67,
"saldo de estoque insuficiente" num dos itens; retry nunca resolveria) — aí o
recado fica de fora (logado com o corpo do erro) e o hold SEGUE para a
situação, que usa endpoint dedicado e não revalida a venda. Segurar o pedido
é o essencial; o recado é acessório. `mudado_por=None` na auditoria = ação do
robô.

Espelhos locais atualizados na hora (bling_orders + snapshot
verificar_margem): situacao='83955' e bling_status_margem='Pendente'. O
'Pendente' GRAVADO é o pino que (a) mantém a linha na aba mesmo em 83955,
(b) impede re-hold depois de um Aprovar (status vira 'Aprovado') e (c)
distingue dos 83955 do controle de estoque (status NULL → seguem fora da
Margem, e fora do aviso Threema de estoque, que filtra pelos seus próprios
marcados).

MARGEM NEGATIVA REPROVA DIRETO (Eduardo 02/09: "margem negativa reprovar
automatico tbm pra poder aprovar pelo threma"): se a pior margem que
disparou o gatilho é < 0, o pino gravado é 'Reprovado' em vez de 'Pendente'
— mesmos passos no Bling (recado + 83955), mas a linha SAI da aba Pendentes
(igual ao Reprovar no clique) e o aviso Threema já diz "reprovado
automaticamente", com o link de aprovar pelo celular pra desfazer. Margem
baixa POSITIVA (ex.: 5% < 9%) continua virando 'Pendente' pra análise
humana. O resgate: link do aviso, ou "Buscar pedido" na aba (o lookup não
filtra situação) — Aprovar solta o pedido no Bling nos dois caminhos
(exceção do segurado em routers/margens._apply_bling_decision_by_pedido
cobre 'Pendente' E 'Reprovado').

MARGEM FORA DO NORMAL (> 60%) SÓ AVISA (Eduardo 02/09): margem alta demais
costuma ser custo errado no cadastro — o mesmo tick manda UM alerta por
pedido no Threema (destinatários do margem_auto) e não toca no pedido.
Dedup pela auditoria (margem_audit, acao='alerta_margem_alta'), gravada só
depois de pelo menos um envio bem-sucedido — Threema fora do ar → tenta de
novo no próximo tick.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import httpx
import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    ThreemaInformarConfig,
)
from app.security.cipher import decrypt_json
from app.services import aprovar_link, informar, threema
from app.services.logistica_bling import build_observacoes_put_body, compose_observacoes
from app.services.margem_audit import record_margem_audit
from app.services.marketplaces.bling import BlingClient
from app.services.verificar_margem import (
    SITUACAO_BLING_TABLE,
    SNAPSHOT_TABLE,
    qualified_table,
)

logger = structlog.get_logger()

SITUACAO_EM_ABERTO = 6
SITUACAO_AGUARDANDO_CANCELAMENTO = 83955

# Margem acima disto é "fora do normal" (provável custo errado no cadastro):
# alerta no Threema, sem mexer no pedido. Fração, como o snapshot (0.60 = 60%).
MARGEM_ALTA_LIMIAR = 0.60

_MARGEM_AUDIT_TABLE = qualified_table("margem_audit")


def _mensagem(motivo: str, *, reprovado: bool = False) -> str:
    acao = "pedido reprovado automaticamente" if reprovado else "pedido segurado para análise"
    return (
        f"Margem DaVinci: {acao} ({motivo}) — "
        "situação movida para Aguardando Cancelamento. "
        "Aprovar na aba Margem devolve o pedido ao fluxo."
    )


def _motivo(margem_baixa: bool, saldo_divergente: bool, saldo_pendente: bool) -> str:
    partes = []
    if margem_baixa:
        partes.append("margem abaixo do mínimo")
    if saldo_divergente:
        partes.append("saldo divergente")
    if saldo_pendente:
        # Ramo "líquido NULL" do gatilho: nada diverge AINDA — o marketplace
        # não confirmou o repasse (Amazon liquida dias depois). O recado no
        # Bling precisa dizer isso, não acusar divergência que não existe.
        partes.append("aguardando saldo da plataforma")
    # 'Pendente' gravado sem gatilho ativo (hold manual da edição de saldo).
    return " e ".join(partes) or "pendente de análise"


def _candidatos_sql() -> str:
    # Import tardio DE PROPÓSITO: a definição canônica de "Pendente" (gatilhos
    # de atenção) mora em routers/margens.py, que por sua vez importa serviços
    # — importar aqui em cima criaria ciclo. Buscar lá garante que o robô
    # segura EXATAMENTE o que a aba mostra como pendente (se a regra da aba
    # mudar, o robô muda junto).
    from app.routers.margens import (
        _ATTENTION_FRETE_SQL,
        _ATTENTION_MARGEM_SQL,
        _ATTENTION_SALDO_AGUARDANDO_SQL,
        _ATTENTION_SALDO_SQL,
        NEEDS_ATTENTION_SQL,
    )

    # NOT _ATTENTION_SALDO_AGUARDANDO_SQL: linha de plataforma confiável
    # (ML/Shopee/TikTok) só aguardando o líquido real fica na aba, mas NÃO é
    # motivo de hold (ver docstring). A exclusão é por LINHA: um pedido misto
    # ainda entra pelas linhas com gatilho real (margem baixa exige líquido
    # presente, então nunca coexiste com "aguardando" na mesma linha).
    return f"""
        SELECT v.pedido_bling,
               MAX(v.bling_id)                  AS bling_id,
               MAX(COALESCE(v.plataforma_bling, v.plataforma_financeiro))
                                                AS plataforma,
               MAX(v.loja_nome)                 AS conta,
               BOOL_OR({_ATTENTION_MARGEM_SQL}) AS margem_baixa,
               -- Margem NEGATIVA (entre as linhas que dispararam o gatilho):
               -- reprova direto em vez de pino 'Pendente' (ver docstring).
               BOOL_OR({_ATTENTION_MARGEM_SQL}
                       AND v.marketplace_margem < 0) AS margem_negativa,
               BOOL_OR({_ATTENTION_SALDO_SQL}
                       AND v.marketplace_liquido_base_margem_item IS NOT NULL)
                                                AS saldo_divergente,
               BOOL_OR({_ATTENTION_SALDO_SQL}
                       AND v.marketplace_liquido_base_margem_item IS NULL)
                                                AS saldo_pendente,
               -- ×100: o snapshot guarda margens como FRAÇÃO (0.069 = 6,9%);
               -- a mensagem mostra em % como a aba faz.
               MIN(v.marketplace_margem)
                   FILTER (WHERE {_ATTENTION_MARGEM_SQL}) * 100 AS margem,
               MAX(v.margem_minima)
                   FILTER (WHERE {_ATTENTION_MARGEM_SQL}) * 100 AS minima,
               SUM(v.marketplace_lucro)         AS lucro
        FROM {SNAPSHOT_TABLE} v
        WHERE v.situacao = '{SITUACAO_EM_ABERTO}'
          AND v.bling_id IS NOT NULL
          AND NOT {_ATTENTION_FRETE_SQL}
          AND NOT {_ATTENTION_SALDO_AGUARDANDO_SQL}
          AND (v.bling_status_margem = 'Pendente'
               OR (v.bling_status_margem IS NULL AND {NEEDS_ATTENTION_SQL}))
        GROUP BY v.pedido_bling
        ORDER BY v.pedido_bling
    """


async def _bling_client(session: AsyncSession) -> BlingClient | None:
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .where(Integration.status == "active")
            .where(Integration.store_id.is_(None))
            .order_by(Integration.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    return BlingClient(decrypt_json(integ.credentials), integration_id=integ.id)


async def _hold_one(
    session: AsyncSession,
    client: BlingClient,
    *,
    pedido_bling: str,
    bling_id: int,
    motivo: str,
    hoje: date | None,
    reprovar: bool = False,
) -> None:
    # 1) Observações (o recado) — antes da situação: se o PUT falhar por erro
    #    transiente, o pedido continua candidato e o próximo tick refaz os dois
    #    passos. 4xx (menos 429) = o Bling recusou a VENDA em validação (ex.:
    #    erro 67, estoque insuficiente) — determinístico, retry não resolve:
    #    loga o corpo do erro e segue pro passo essencial (segurar).
    order = await client.get_order(bling_id)
    atual = order.get("observacoes")
    novo = compose_observacoes(atual, _mensagem(motivo, reprovado=reprovar), hoje=hoje)
    if novo != (atual or "").strip():
        try:
            await client.update_order(bling_id, build_observacoes_put_body(order, novo))
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status >= 500 or status == 429:
                raise
            logger.warning(
                "margem_auto_hold_obs_rejeitada",
                pedido_bling=pedido_bling,
                bling_id=bling_id,
                status=status,
                bling=e.response.text[:300],
            )

    # 2) Situação: endpoint dedicado do Bling (não reenvia o pedido).
    await client.update_order_situacao(bling_id, SITUACAO_AGUARDANDO_CANCELAMENTO)

    # 3) Espelhos locais (todas as linhas-item do pedido) + auditoria.
    #    Pino 'Reprovado' (margem negativa) tira a linha da aba Pendentes na
    #    hora — mesmo efeito do Reprovar no clique; 'Pendente' mantém pra
    #    análise humana (ver docstring).
    pino = "Reprovado" if reprovar else "Pendente"
    await session.execute(
        update(BlingOrder)
        .where(BlingOrder.bling_id == bling_id)
        .values(situacao=str(SITUACAO_AGUARDANDO_CANCELAMENTO), status=pino)
    )
    await session.execute(
        text(
            f"UPDATE {SNAPSHOT_TABLE} "
            "SET situacao = :sit, bling_status_margem = :pino, "
            "    situacao_nome = COALESCE("
            f"       (SELECT s.nome FROM {SITUACAO_BLING_TABLE} s"
            "         WHERE s.id::text = :sit), situacao_nome) "
            "WHERE bling_id = :bling_id"
        ),
        {"sit": str(SITUACAO_AGUARDANDO_CANCELAMENTO), "bling_id": bling_id, "pino": pino},
    )
    await record_margem_audit(
        session,
        acao="situacao",
        pedido_bling=pedido_bling,
        bling_id=bling_id,
        sku=None,
        valor_antigo=str(SITUACAO_EM_ABERTO),
        valor_novo=str(SITUACAO_AGUARDANDO_CANCELAMENTO),
        origem="margens_auto",
        mudado_por=None,
    )
    if reprovar:
        await record_margem_audit(
            session,
            acao="status",
            pedido_bling=pedido_bling,
            bling_id=bling_id,
            sku=None,
            valor_antigo=None,
            valor_novo="Reprovado",
            origem="margens_auto",
            mudado_por=None,
        )
    await session.commit()


def _loja(r: Mapping) -> str:
    return " ".join(
        p
        for p in (
            str(r["plataforma"] or "").strip(),
            str(r["conta"] or "").strip(),
        )
        if p
    )


async def _recipients_margem_auto(session: AsyncSession) -> list[str]:
    row = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == "margem_auto")
        )
    ).scalar_one_or_none()
    return threema.parse_recipients(row.recipients if row else "")


async def _avisar_threema(
    session: AsyncSession, r: Mapping, motivo: str, *, reprovado: bool = False
) -> None:
    """Aviso Threema NA HORA do hold, pros destinatários do cadastro
    `margem_auto` (segunda lista do modal Informar da Margem). Uma mensagem
    por pedido, com conta, motivo, margem vs mínima e lucro — pedido do
    Eduardo (02/09): avisar na hora pra ele decidir do celular. `reprovado`
    troca cabeçalho e rodapé (o pedido já foi reprovado; o link desfaz).
    Best-effort: sem destinatários cadastrados não manda nada; falha de envio
    é logada e NÃO desfaz nem conta contra o hold (o essencial é segurar)."""
    recipients = await _recipients_margem_auto(session)
    if not recipients:
        return
    if reprovado:
        cabecalho = "DaVinci — Margem: pedido reprovado automaticamente"
        rodape_acao = (
            "Reprovado por margem negativa — situação movida para Aguardando "
            "Cancelamento. Se quiser manter a venda, aprove pelo link.\n"
        )
    else:
        cabecalho = "DaVinci — Margem: pedido segurado para análise"
        rodape_acao = (
            "Situação movida para Aguardando Cancelamento. Aprovar devolve o pedido ao fluxo.\n"
        )
    msg = informar.mensagem_margem_pedido(
        informar.MargemPedido(
            pedido=str(r["pedido_bling"]),
            loja=_loja(r),
            motivo=motivo,
            margem=None if r["margem"] is None else float(r["margem"]),  # type: ignore[arg-type]
            minima=None if r["minima"] is None else float(r["minima"]),  # type: ignore[arg-type]
            lucro=None if r["lucro"] is None else float(r["lucro"]),  # type: ignore[arg-type]
        ),
        cabecalho=cabecalho,
        rodape=(
            rodape_acao
            # Link público assinado — abre a página de confirmação e aprova
            # sem precisar logar (services/aprovar_link.py).
            + f"Aprovar pelo celular: {aprovar_link.url_aprovar(str(r['pedido_bling']))}"
        ),
    )
    try:
        result = await threema.ThreemaClient().send_to_all(msg, recipients)
        logger.info(
            "margem_auto_hold_threema",
            pedido_bling=str(r["pedido_bling"]),
            sent=result.get("sent", []),
            failed=result.get("failed", []),
        )
    except Exception as e:  # noqa: BLE001 — aviso é acessório, hold já feito
        logger.warning(
            "margem_auto_hold_threema_falhou",
            pedido_bling=str(r["pedido_bling"]),
            erro=str(e)[:200],
        )


def _alerta_margem_alta_sql() -> str:
    # Situação 6 = janela de triagem (mesma do hold): pedido novo, cadastro
    # ainda corrigível antes de faturar. MAX = a MAIOR margem entre os itens.
    # NOT EXISTS na auditoria = um alerta por pedido, pra sempre.
    return f"""
        SELECT v.pedido_bling,
               MAX(COALESCE(v.plataforma_bling, v.plataforma_financeiro))
                                                AS plataforma,
               MAX(v.loja_nome)                 AS conta,
               MAX(v.marketplace_margem) * 100  AS margem,
               SUM(v.marketplace_lucro)         AS lucro
        FROM {SNAPSHOT_TABLE} v
        WHERE v.situacao = '{SITUACAO_EM_ABERTO}'
          AND v.marketplace_margem > {MARGEM_ALTA_LIMIAR}
          AND NOT EXISTS (
                SELECT 1 FROM {_MARGEM_AUDIT_TABLE} a
                 WHERE a.pedido_bling = v.pedido_bling
                   AND a.acao = 'alerta_margem_alta')
        GROUP BY v.pedido_bling
        ORDER BY v.pedido_bling
    """


async def _alertar_margem_alta(session: AsyncSession) -> int:
    """Margem fora do normal (> 60%): SÓ avisa no Threema — nada muda no
    pedido (Eduardo 02/09: "margem fora do normal acima de 60% enviar
    mensagem de alerta"). Margem alta assim costuma ser custo errado no
    cadastro. A auditoria (acao='alerta_margem_alta') é o dedup: gravada
    apenas quando pelo menos um destinatário recebeu — Threema fora do ar ou
    sem cadastro → nada gravado, tenta de novo no próximo tick."""
    rows = (await session.execute(text(_alerta_margem_alta_sql()))).mappings().all()
    if not rows:
        return 0
    recipients = await _recipients_margem_auto(session)
    if not recipients:
        return 0
    enviados = 0
    for r in rows:
        margem = float(r["margem"])
        msg = informar.mensagem_margem_pedido(
            informar.MargemPedido(
                pedido=str(r["pedido_bling"]),
                loja=_loja(r),
                motivo="margem fora do normal (acima de 60%)",
                margem=margem,
                minima=None,
                lucro=None if r["lucro"] is None else float(r["lucro"]),  # type: ignore[arg-type]
            ),
            cabecalho="DaVinci — Margem: margem fora do normal",
            rodape=(
                "Nada foi alterado no pedido — margem alta assim geralmente é "
                "custo errado. Confira o cadastro do produto."
            ),
        )
        try:
            result = await threema.ThreemaClient().send_to_all(msg, recipients)
        except Exception as e:  # noqa: BLE001 — um pedido não derruba os demais
            logger.warning(
                "margem_alerta_alta_falhou",
                pedido_bling=str(r["pedido_bling"]),
                erro=str(e)[:200],
            )
            continue
        logger.info(
            "margem_alerta_alta_threema",
            pedido_bling=str(r["pedido_bling"]),
            margem=margem,
            sent=result.get("sent", []),
            failed=result.get("failed", []),
        )
        if not result.get("sent"):
            continue  # ninguém recebeu → sem dedup, retenta no próximo tick
        await record_margem_audit(
            session,
            acao="alerta_margem_alta",
            pedido_bling=str(r["pedido_bling"]),
            bling_id=None,
            sku=None,
            valor_antigo=None,
            valor_novo=f"{margem:.1f}%",
            origem="margens_auto",
            mudado_por=None,
        )
        await session.commit()
        enviados += 1
    return enviados


async def run(
    session: AsyncSession,
    *,
    client: BlingClient | None = None,
    hoje: date | None = None,
) -> dict:
    """Segura/reprova os pendentes "Em aberto" e alerta margens fora do
    normal. Retorna contadores p/ log/response."""
    if not get_settings().margem_auto_hold:
        return {"held": 0, "reprovados": 0, "failed": 0, "alertas": 0, "skipped": "disabled"}

    rows = (await session.execute(text(_candidatos_sql()))).mappings().all()
    held = reprovados = failed = 0
    skipped: str | None = None
    if rows:
        client = client or await _bling_client(session)
        if client is None:
            logger.warning("margem_auto_hold_sem_bling", candidatos=len(rows))
            failed = len(rows)
            skipped = "bling_integration_missing"
            rows = []
    for r in rows:
        reprovar = bool(r["margem_negativa"])
        motivo = _motivo(
            bool(r["margem_baixa"]),
            bool(r["saldo_divergente"]),
            bool(r["saldo_pendente"]),
        )
        try:
            await _hold_one(
                session,
                client,
                pedido_bling=str(r["pedido_bling"]),
                bling_id=int(r["bling_id"]),
                motivo=motivo,
                hoje=hoje,
                reprovar=reprovar,
            )
            if reprovar:
                reprovados += 1
            else:
                held += 1
            logger.info(
                "margem_auto_hold_pedido",
                pedido_bling=str(r["pedido_bling"]),
                bling_id=int(r["bling_id"]),
                motivo=motivo,
                reprovado=reprovar,
            )
            await _avisar_threema(session, r, motivo, reprovado=reprovar)
        except Exception as e:  # noqa: BLE001 — um pedido não derruba os demais
            failed += 1
            await session.rollback()
            erro = str(e)[:200]
            if isinstance(e, httpx.HTTPStatusError):
                # o corpo da resposta do Bling diz O QUE foi rejeitado
                erro = f"{erro} | bling: {e.response.text[:300]}"
            logger.warning(
                "margem_auto_hold_falhou",
                pedido_bling=str(r["pedido_bling"]),
                erro=erro,
            )
    # Alerta de margem fora do normal (> 60%): independe do Bling (não toca
    # no pedido) — roda mesmo sem candidatos de hold ou sem integração.
    alertas = await _alertar_margem_alta(session)
    out: dict = {"held": held, "reprovados": reprovados, "failed": failed, "alertas": alertas}
    if skipped:
        out["skipped"] = skipped
    return out
