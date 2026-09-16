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
Reprovar. Pedido que já andou (Atendido, Em digitação = etiqueta enviada
[21; 83965 "Enviado Etiqueta" é o legado], ...) não é segurado. E o motivo
"frete" fica de fora porque a própria listagem da Margem o exclui (NOT
_ATTENTION_FRETE_SQL): frete só se conhece depois do envio, não há o que
segurar. ML/Shopee/TikTok nunca entram por saldo (01/09):
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

AMAZON SEM REPASSE (Vinicius, 15/09): a Amazon só publica as taxas 3-4 dias
DEPOIS do envio, então em triagem o líquido real de um pedido Amazon nunca
existe — todo pedido Amazon era segurado por "aguardando saldo da
plataforma" e liberado na mão (13 em 8 dias; caso 297371). Agora líquido
NULL não é pendência na Amazon: a margem oficial dos gatilhos
(routers/margens._MARGEM_OFICIAL_SQL) cai na âncora Bling (valor_base −
frete − taxa + reembolso, sobre o custo) — saudável passa sem hold, abaixo
da mínima reprova direto como em qualquer plataforma. Divergência com
repasse real presente segue segurando.

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

MARGEM ABAIXO DA MÍNIMA REPROVA DIRETO (11/09/2026: "margem 16 e mínimo
18, nesse caso ele teria que reprovar o pedido automático" — antes, desde
02/09, só a margem NEGATIVA reprovava e a baixa positiva virava 'Pendente'
pra análise humana): toda linha com o gatilho de margem baixa ativo
(marketplace_margem < margem_minima, sem Condição Especial) grava o pino
'Reprovado' em vez de 'Pendente' — mesmos passos no Bling (recado + 83955),
mas a linha SAI da aba Pendentes (igual ao Reprovar no clique) e o aviso
Threema já diz "reprovado automaticamente", com o link de aprovar pelo
celular pra desfazer. O pino 'Pendente' (segurar sem reprovar) fica só pros
outros gatilhos: saldo divergente e 'Pendente' gravado na mão. O resgate:
link do aviso, ou "Buscar pedido" na aba (o lookup não filtra situação) —
Aprovar solta o pedido no Bling nos dois caminhos (exceção do segurado em
routers/margens._apply_bling_decision_by_pedido cobre 'Pendente' E
'Reprovado').

MARGEM FORA DO NORMAL (> 60%) SÓ AVISA (Eduardo 02/09): margem alta demais
costuma ser custo errado no cadastro — o mesmo tick manda UM alerta por
pedido no Threema (destinatários do margem_auto) e não toca no pedido.
Dedup pela auditoria (margem_audit, acao='alerta_margem_alta'), gravada só
depois de pelo menos um envio bem-sucedido — Threema fora do ar → tenta de
novo no próximo tick.

REAVALIAÇÃO DOS REPROVADOS PELO ROBÔ (Vinicius, 16/09/2026 — caso 297400):
o robô reprova 30 min depois de o pedido entrar, com o repasse que a
plataforma informava NAQUELE instante. Na Shopee esse número é provisório:
o 297400 (Hotwav, Condição Especial ≥16% vigente) foi reprovado às 14:17 com
margem < 16% e horas depois o escrow subiu pra R$ 585,07 (17,1%) — os 13
pedidos irmãos do mesmo produto passaram pela condição; só ele ficou preso,
porque nada revisitava um 'Reprovado'. Decisão: NÃO mexer na reprovação
imediata (regra de 11/09), mas o robô passa a REAVALIAR, de hora em hora
(cron `margem_reavaliar_reprovados`), os pedidos que ELE reprovou, que ainda
estão em Aguardando Cancelamento e cuja reprovação tem mais de
REAVALIACAO_IDADE_MINIMA. Antes de julgar, rebusca o financeiro do pedido na
plataforma e refresca o snapshot (`_atualizar_financeiro`), pra não depender
do re-sync de hora em hora. Se a margem oficial agora passa (mínima OU
Condição Especial) e nada mais pende → libera como o botão Aprovar (recado nas
Observações, 83955 → Atendido → Em aberto, pino 'Aprovado', auditoria do robô)
e avisa no Threema (`margem_auto`). Se passa mas sobrou saldo divergente →
só troca o pino pra 'Pendente' (fica segurado, volta pra aba pra alguém
decidir). Se continua baixa ou sem margem conhecida → não faz nada e não
avisa. Reprovação/aprovação feita por PESSOA nunca é reavaliada
(bling_orders.aprovado_por preenchido ou auditoria humana posterior), e o
robô nunca aprova só localmente: se o Bling recusar a situação, o pedido fica
como está e a próxima hora tenta de novo.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

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
    BLING_ORDERS_TABLE,
    SITUACAO_BLING_TABLE,
    SNAPSHOT_TABLE,
    patch_status_for_pedido,
    qualified_table,
)

logger = structlog.get_logger()

SITUACAO_EM_ABERTO = 6
# Degrau obrigatório do Bling pra sair de Aguardando Cancelamento (mesmo
# caminho do Aprovar da aba: routers/margens._apply_bling_decision_by_pedido).
SITUACAO_ATENDIDO = 9
SITUACAO_AGUARDANDO_CANCELAMENTO = 83955

# Margem acima disto é "fora do normal" (provável custo errado no cadastro):
# alerta no Threema, sem mexer no pedido. Fração, como o snapshot (0.60 = 60%).
MARGEM_ALTA_LIMIAR = 0.60

# Reavaliação dos reprovados pelo robô (ver docstring): idade mínima da
# reprovação antes da primeira revisita — dá tempo de a plataforma consolidar
# o repasse — e até quando insistir (mesma janela da aba Pendentes).
REAVALIACAO_IDADE_MINIMA = timedelta(minutes=90)
REAVALIACAO_JANELA_DIAS = 30

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
        _LUCRO_OFICIAL_SQL,
        _MARGEM_OFICIAL_SQL,
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
               -- Margem baixa reprova direto em vez de pino 'Pendente'
               -- (11/09, ver docstring).
               BOOL_OR({_ATTENTION_MARGEM_SQL}) AS margem_baixa,
               BOOL_OR({_ATTENTION_SALDO_SQL}
                       AND v.marketplace_liquido_base_margem_item IS NOT NULL)
                                                AS saldo_divergente,
               BOOL_OR({_ATTENTION_SALDO_SQL}
                       AND v.marketplace_liquido_base_margem_item IS NULL)
                                                AS saldo_pendente,
               -- ×100: o snapshot guarda margens como FRAÇÃO (0.069 = 6,9%);
               -- a mensagem mostra em % como a aba faz. Margem/lucro
               -- OFICIAIS: real da plataforma; Amazon sem repasse → Bling.
               MIN({_MARGEM_OFICIAL_SQL})
                   FILTER (WHERE {_ATTENTION_MARGEM_SQL}) * 100 AS margem,
               MAX(v.margem_minima)
                   FILTER (WHERE {_ATTENTION_MARGEM_SQL}) * 100 AS minima,
               SUM({_LUCRO_OFICIAL_SQL})        AS lucro,
               string_agg(DISTINCT NULLIF(btrim(v.produto), ''), '; ')
                                                AS produto
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
    #    Pino 'Reprovado' (margem abaixo da mínima) tira a linha da aba Pendentes na
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
            "Reprovado por margem abaixo do mínimo — situação movida para Aguardando "
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
            produto=r.get("produto"),
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
               SUM(v.marketplace_lucro)         AS lucro,
               string_agg(DISTINCT NULLIF(btrim(v.produto), ''), '; ')
                                                AS produto
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
                produto=r.get("produto"),
            ),
            cabecalho="DaVinci — Margem: margem fora do normal",
            rodape=(
                "Nada foi alterado no pedido — margem alta assim geralmente é "
                "custo errado. Confira o cadastro do produto.\n"
                # Sem ação de aprovar aqui; o link abre o pedido na aba Margem
                # (Eduardo 03/09: "o link na mensagem não está chegando").
                f"Ver no DaVinci: {aprovar_link.url_margem(str(r['pedido_bling']))}"
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
        # Margem abaixo da mínima → reprova direto (11/09). Saldo divergente
        # / 'Pendente' gravado na mão → só segura.
        reprovar = bool(r["margem_baixa"])
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


# --- Reavaliação dos reprovados pelo robô (ver docstring do módulo) ---------


def _reavaliacao_candidatos_sql() -> str:
    # Fonte do pino é bling_orders (sobrevive ao rebuild do snapshot). A
    # ÚLTIMA reprovação automática do pedido vem da auditoria (acao='status',
    # 'Reprovado', origem margens_auto, mudado_por NULL) — é ela que dá a
    # idade. `aprovado_por IS NULL`: Aprovar/Reprovar no clique grava o autor
    # (mesmo quando o Bling não é tocado, ex.: confirmar a reprovação de um
    # 83955), então autor preenchido = pessoa decidiu, não revisita. O NOT
    # EXISTS cobre qualquer outra ação humana depois da reprovação.
    return f"""
        WITH reprovo AS (
            SELECT DISTINCT ON (a.pedido_bling) a.pedido_bling, a.created_at
            FROM {_MARGEM_AUDIT_TABLE} a
            WHERE a.acao = 'status'
              AND a.valor_novo = 'Reprovado'
              AND a.origem = 'margens_auto'
              AND a.mudado_por IS NULL
            ORDER BY a.pedido_bling, a.created_at DESC
        )
        SELECT bo.numero            AS pedido_bling,
               MAX(bo.bling_id)     AS bling_id,
               r.created_at         AS reprovado_em
        FROM {BLING_ORDERS_TABLE} bo
        JOIN reprovo r ON r.pedido_bling = bo.numero
        WHERE bo.situacao = '{SITUACAO_AGUARDANDO_CANCELAMENTO}'
          AND bo.status = 'Reprovado'
          AND bo.aprovado_por IS NULL
          AND bo.bling_id IS NOT NULL
          AND r.created_at <= :limite
          AND r.created_at >= :inicio_janela
          AND NOT EXISTS (
                SELECT 1 FROM {_MARGEM_AUDIT_TABLE} h
                 WHERE h.pedido_bling = bo.numero
                   AND h.mudado_por IS NOT NULL
                   AND h.created_at > r.created_at)
        GROUP BY bo.numero, r.created_at
        ORDER BY bo.numero
    """


def _reavaliacao_julgar_sql() -> str:
    # Mesmos gatilhos da aba/robô (fonte única em routers/margens.py), agregados
    # por pedido. `margem_conhecida` exige margem oficial em TODAS as linhas:
    # sem repasse real não há o que julgar (nunca libera às cegas).
    from app.routers.margens import (
        _ATTENTION_MARGEM_SQL,
        _ATTENTION_SALDO_SQL,
        _LUCRO_OFICIAL_SQL,
        _MARGEM_DATA_ESPECIAL_SQL,
        _MARGEM_OFICIAL_SQL,
        _SITUACOES_SALDO_DIVERGENTE_IN,
    )

    # Saldo divergente só é triado em Em aberto/etiqueta (o pedido segurado
    # está em 83955, onde o gatilho é sempre falso). A pergunta aqui é "se eu
    # devolver pra Em aberto, o saldo vai pender?" — então o recorte de
    # situação é neutralizado só nesta expressão.
    guarda = f"(v.situacao IN ({_SITUACOES_SALDO_DIVERGENTE_IN})"
    if guarda not in _ATTENTION_SALDO_SQL:  # pragma: no cover — trava de manutenção
        raise RuntimeError("_ATTENTION_SALDO_SQL mudou de forma; ajuste a reavaliação")
    saldo_como_em_aberto = _ATTENTION_SALDO_SQL.replace(guarda, "(TRUE", 1)

    return f"""
        SELECT COUNT(*)                         AS linhas,
               MAX(COALESCE(v.plataforma_bling, v.plataforma_financeiro))
                                                AS plataforma,
               MAX(v.loja_nome)                 AS conta,
               BOOL_AND({_MARGEM_OFICIAL_SQL} IS NOT NULL)
                                                AS margem_conhecida,
               BOOL_OR({_ATTENTION_MARGEM_SQL}) AS margem_baixa,
               BOOL_OR({_MARGEM_DATA_ESPECIAL_SQL})
                                                AS condicao_especial,
               BOOL_OR({saldo_como_em_aberto}
                       AND v.marketplace_liquido_base_margem_item IS NOT NULL)
                                                AS saldo_divergente,
               MIN({_MARGEM_OFICIAL_SQL}) * 100 AS margem,
               MAX(v.margem_minima) * 100       AS minima,
               SUM({_LUCRO_OFICIAL_SQL})        AS lucro,
               string_agg(DISTINCT NULLIF(btrim(v.produto), ''), '; ')
                                                AS produto
        FROM {SNAPSHOT_TABLE} v
        WHERE v.bling_id = :bling_id
    """


async def _atualizar_financeiro(session: AsyncSession, bling_id: int) -> None:
    """Rebusca o repasse do pedido na plataforma e refresca o snapshot dele.

    A reprovação foi decidida com o número que a plataforma dava 30 min depois
    da venda; a reavaliação só faz sentido com o número de agora. Best-effort:
    API fora → loga e o julgamento segue com o snapshot atual (empate = nada
    muda, tenta de novo na próxima hora). Import tardio: marketplace_financials
    importa verificar_margem, como este módulo — evita ciclo no import.

    Mesmo advisory lock por pedido do job `sync_marketplace_financials_for_
    order_run` (worker.py): a fila do financeiro pode estar buscando este
    pedido neste instante (re-sync do Bling); sem o lock, dois upserts da mesma
    linha se atropelam."""
    from app.services.marketplace_financials import (
        run_sync_marketplace_financials_for_bling_order,
    )
    from app.services.verificar_margem import refresh_for_bling_id

    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"marketplace_financials:{bling_id}"},
        )
        await run_sync_marketplace_financials_for_bling_order(
            session, bling_order_id=bling_id, trigger="reavaliacao"
        )
        await session.commit()  # solta o lock junto
        await refresh_for_bling_id(session, bling_id)
    except Exception as e:  # noqa: BLE001 — julga com o que tem
        await session.rollback()
        logger.warning("margem_reavaliar_financeiro_falhou", bling_id=bling_id, erro=str(e)[:200])


def _mensagem_liberado(motivo: str) -> str:
    return (
        f"Margem DaVinci: pedido liberado automaticamente ({motivo}) — "
        "reprovação anterior revista com o repasse atualizado da plataforma; "
        "situação devolvida para Em aberto."
    )


async def _liberar_one(
    session: AsyncSession,
    client: BlingClient,
    *,
    pedido_bling: str,
    bling_id: int,
    motivo: str,
    hoje: date | None,
) -> bool:
    """Desfaz a reprovação automática como o Aprovar da aba faria.

    Lê o pedido no Bling e só age se ele AINDA está em Aguardando
    Cancelamento (ou parado no degrau Atendido de uma tentativa anterior):
    situação diferente = alguém mexeu no painel do Bling, o robô não toca e
    devolve False. Ordem das escritas igual ao hold: Observações primeiro
    (4xx = validação da venda, recado fica de fora), depois a situação —
    83955 → Atendido → Em aberto. Falha na situação propaga: NADA local muda
    (o robô nunca aprova só no DaVinci — deixaria a venda presa no Bling com
    pino Aprovado) e a próxima hora tenta de novo."""
    order = await client.get_order(bling_id)
    situacao_bling = str((order.get("situacao") or {}).get("id") or "").strip()
    if situacao_bling and situacao_bling not in (
        str(SITUACAO_AGUARDANDO_CANCELAMENTO),
        str(SITUACAO_ATENDIDO),
    ):
        logger.info(
            "margem_reavaliar_situacao_inesperada",
            pedido_bling=pedido_bling,
            bling_id=bling_id,
            situacao_bling=situacao_bling,
        )
        return False

    atual = order.get("observacoes")
    novo = compose_observacoes(atual, _mensagem_liberado(motivo), hoje=hoje)
    if novo != (atual or "").strip():
        try:
            await client.update_order(bling_id, build_observacoes_put_body(order, novo))
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status >= 500 or status == 429:
                raise
            logger.warning(
                "margem_reavaliar_obs_rejeitada",
                pedido_bling=pedido_bling,
                bling_id=bling_id,
                status=status,
                bling=e.response.text[:300],
            )

    steps: list[int] = []
    if situacao_bling != str(SITUACAO_ATENDIDO):
        steps.append(SITUACAO_ATENDIDO)
    steps.append(SITUACAO_EM_ABERTO)
    for step in steps:
        await client.update_order_situacao(bling_id, step)

    await session.execute(
        update(BlingOrder)
        .where(BlingOrder.bling_id == bling_id)
        .values(situacao=str(SITUACAO_EM_ABERTO), status="Aprovado")
    )
    await patch_status_for_pedido(
        session,
        pedido_bling=pedido_bling,
        status="Aprovado",
        situacao=str(SITUACAO_EM_ABERTO),
    )
    await record_margem_audit(
        session,
        acao="situacao",
        pedido_bling=pedido_bling,
        bling_id=bling_id,
        sku=None,
        valor_antigo=str(SITUACAO_AGUARDANDO_CANCELAMENTO),
        valor_novo=str(SITUACAO_EM_ABERTO),
        origem="margens_auto",
        mudado_por=None,
    )
    await record_margem_audit(
        session,
        acao="status",
        pedido_bling=pedido_bling,
        bling_id=bling_id,
        sku=None,
        valor_antigo="Reprovado",
        valor_novo="Aprovado",
        origem="margens_auto",
        mudado_por=None,
    )
    await session.commit()
    return True


async def _voltar_pendente_one(session: AsyncSession, *, pedido_bling: str, bling_id: int) -> None:
    """Margem passou, mas sobrou outra pendência (saldo divergente): o pedido
    continua segurado no Bling, só o pino vira 'Pendente' — a linha volta pra
    aba Pendentes (exceção do segurado na listagem) pra alguém decidir."""
    await session.execute(
        update(BlingOrder).where(BlingOrder.bling_id == bling_id).values(status="Pendente")
    )
    await patch_status_for_pedido(session, pedido_bling=pedido_bling, status="Pendente")
    await record_margem_audit(
        session,
        acao="status",
        pedido_bling=pedido_bling,
        bling_id=bling_id,
        sku=None,
        valor_antigo="Reprovado",
        valor_novo="Pendente",
        origem="margens_auto",
        mudado_por=None,
    )
    await session.commit()


async def _avisar_threema_reavaliacao(
    session: AsyncSession, r: Mapping, *, pedido_bling: str, motivo: str, liberado: bool
) -> None:
    """Aviso da reavaliação pros mesmos destinatários do hold (`margem_auto`).
    Best-effort como o do hold: falha de envio não desfaz nada."""
    recipients = await _recipients_margem_auto(session)
    if not recipients:
        return
    if liberado:
        cabecalho = "DaVinci — Margem: pedido liberado automaticamente"
        rodape = (
            "Reprovação anterior revista com o repasse atualizado da plataforma — "
            "situação devolvida para Em aberto, o pedido segue o fluxo normal.\n"
        )
    else:
        cabecalho = "DaVinci — Margem: pedido reprovado voltou para análise"
        rodape = (
            "A margem passou a atender, mas o saldo continua divergente — o pedido "
            "segue em Aguardando Cancelamento, na aba Pendentes, para decisão.\n"
        )
    msg = informar.mensagem_margem_pedido(
        informar.MargemPedido(
            pedido=pedido_bling,
            loja=_loja(r),
            motivo=motivo,
            margem=None if r["margem"] is None else float(r["margem"]),  # type: ignore[arg-type]
            minima=None if r["minima"] is None else float(r["minima"]),  # type: ignore[arg-type]
            lucro=None if r["lucro"] is None else float(r["lucro"]),  # type: ignore[arg-type]
            produto=r.get("produto"),
        ),
        cabecalho=cabecalho,
        rodape=rodape + f"Ver no DaVinci: {aprovar_link.url_margem(pedido_bling)}",
    )
    try:
        result = await threema.ThreemaClient().send_to_all(msg, recipients)
        logger.info(
            "margem_reavaliar_threema",
            pedido_bling=pedido_bling,
            liberado=liberado,
            sent=result.get("sent", []),
            failed=result.get("failed", []),
        )
    except Exception as e:  # noqa: BLE001 — aviso é acessório
        logger.warning(
            "margem_reavaliar_threema_falhou", pedido_bling=pedido_bling, erro=str(e)[:200]
        )


async def _ainda_reprovado_pelo_robo(session: AsyncSession, bling_id: int) -> bool:
    """Reconfere o espelho DEPOIS do refetch (que re-sincroniza o pedido): se
    o Bling já moveu o pedido ou alguém decidiu no meio, não toca."""
    row = (
        await session.execute(
            select(BlingOrder.situacao, BlingOrder.status, BlingOrder.aprovado_por)
            .where(BlingOrder.bling_id == bling_id)
            .order_by(BlingOrder.item_index.asc().nullsfirst())
            .limit(1)
        )
    ).first()
    return (
        row is not None
        and str(row.situacao or "") == str(SITUACAO_AGUARDANDO_CANCELAMENTO)
        and (row.status or "") == "Reprovado"
        and row.aprovado_por is None
    )


async def reavaliar_reprovados(
    session: AsyncSession,
    *,
    client: BlingClient | None = None,
    hoje: date | None = None,
    agora: datetime | None = None,
) -> dict:
    """Revisita os pedidos reprovados pelo robô ainda em Aguardando
    Cancelamento (ver docstring do módulo). Retorna contadores p/ log."""
    if not get_settings().margem_reavaliar_reprovados:
        return {"avaliados": 0, "liberados": 0, "pendentes": 0, "failed": 0, "skipped": "disabled"}

    agora = agora or datetime.now(UTC)
    rows = (
        (
            await session.execute(
                text(_reavaliacao_candidatos_sql()),
                {
                    "limite": agora - REAVALIACAO_IDADE_MINIMA,
                    "inicio_janela": agora - timedelta(days=REAVALIACAO_JANELA_DIAS),
                },
            )
        )
        .mappings()
        .all()
    )
    out: dict = {
        "avaliados": len(rows),
        "liberados": 0,
        "pendentes": 0,
        "ainda_baixa": 0,
        "sem_margem": 0,
        "failed": 0,
    }
    if not rows:
        return out

    for cand in rows:
        pedido_bling = str(cand["pedido_bling"])
        bling_id = int(cand["bling_id"])
        try:
            await _atualizar_financeiro(session, bling_id)
            if not await _ainda_reprovado_pelo_robo(session, bling_id):
                logger.info("margem_reavaliar_pulou_mexido", pedido_bling=pedido_bling)
                continue
            r = (
                (await session.execute(text(_reavaliacao_julgar_sql()), {"bling_id": bling_id}))
                .mappings()
                .one()
            )
            if not r["linhas"] or not r["margem_conhecida"]:
                out["sem_margem"] += 1
                continue
            if r["margem_baixa"]:
                out["ainda_baixa"] += 1
                continue

            motivo = (
                "margem passou a atender a Condição Especial do segmento"
                if r["condicao_especial"]
                else "margem passou a atender o mínimo"
            )
            if r["saldo_divergente"]:
                await _voltar_pendente_one(session, pedido_bling=pedido_bling, bling_id=bling_id)
                out["pendentes"] += 1
                logger.info("margem_reavaliar_pendente", pedido_bling=pedido_bling, motivo=motivo)
                await _avisar_threema_reavaliacao(
                    session, r, pedido_bling=pedido_bling, motivo=motivo, liberado=False
                )
                continue

            client = client or await _bling_client(session)
            if client is None:
                logger.warning("margem_reavaliar_sem_bling", pedido_bling=pedido_bling)
                out["failed"] += 1
                out["skipped"] = "bling_integration_missing"
                continue
            liberado = await _liberar_one(
                session,
                client,
                pedido_bling=pedido_bling,
                bling_id=bling_id,
                motivo=motivo,
                hoje=hoje,
            )
            if not liberado:
                continue
            out["liberados"] += 1
            logger.info(
                "margem_reavaliar_liberado",
                pedido_bling=pedido_bling,
                bling_id=bling_id,
                motivo=motivo,
                margem=None if r["margem"] is None else float(r["margem"]),
                minima=None if r["minima"] is None else float(r["minima"]),
            )
            await _avisar_threema_reavaliacao(
                session, r, pedido_bling=pedido_bling, motivo=motivo, liberado=True
            )
        except Exception as e:  # noqa: BLE001 — um pedido não derruba os demais
            out["failed"] += 1
            await session.rollback()
            erro = str(e)[:200]
            if isinstance(e, httpx.HTTPStatusError):
                erro = f"{erro} | bling: {e.response.text[:300]}"
            logger.warning("margem_reavaliar_falhou", pedido_bling=pedido_bling, erro=erro)
    return out
