"""Casador da aba Status — liga cada pedido da Logística à regra que casa.

A aba Status é a tabela de regras: cada linha tem uma CHAVE
(`status_plataforma` = a assinatura PT do caso, ex. "Pago | Entregue") e o
conjunto de ações daquele caso (alterar status Bling, monitorar, abrir
chamado/reembolso, mensagens de chamado/Bling/Threema).

`find_matching_rule` acha, dado a assinatura + plataforma de um pedido, qual
linha da aba Status se aplica — prefere a regra ESPECÍFICA da plataforma e cai
na regra GERAL (plataforma vazia). É a MESMA função que a automação vai chamar
pra saber o que executar, então o que a UI mostra bate com o que o sistema fará.
"""

from __future__ import annotations

from app.models import LogisticaStatus


def _norm(v: str | None) -> str:
    return (v or "").strip().lower()


def find_matching_rule(
    rows: list[LogisticaStatus], *, assinatura: str | None, plataforma: str | None
) -> LogisticaStatus | None:
    """Regra da aba Status cuja chave casa com a assinatura do pedido.

    Casa por `status_plataforma` normalizado (trim + lower). Entre as que
    casam, prefere a específica da plataforma; senão a geral (plataforma
    vazia). None se nenhuma casar ou a assinatura estiver vazia.
    """
    chave = _norm(assinatura)
    if not chave:
        return None
    plat = _norm(plataforma)
    especifica: LogisticaStatus | None = None
    geral: LogisticaStatus | None = None
    for s in rows:
        if _norm(s.status_plataforma) != chave:
            continue
        sp = _norm(s.plataforma)
        if sp and sp == plat:
            especifica = especifica or s
        elif not sp:
            geral = geral or s
    return especifica or geral


def find_matching_rules(
    rows: list[LogisticaStatus], *, assinatura: str | None, plataforma: str | None
) -> list[LogisticaStatus]:
    """TODAS as regras da aba Status cuja chave casa com a assinatura do pedido.

    Como uma mesma assinatura do ML pode ter mais de uma linha (máquina de
    estados: a transição do Bling depende de onde o pedido está agora), isto
    devolve o conjunto de candidatas pra quem precisa desambiguar pela situação
    atual do pedido (ex.: o executor de Alterar Status Bling). Prefere as
    específicas da plataforma; se não houver nenhuma específica, cai nas gerais
    (plataforma vazia). Lista vazia se a assinatura estiver vazia ou nada casar.
    """
    chave = _norm(assinatura)
    if not chave:
        return []
    plat = _norm(plataforma)
    especificas: list[LogisticaStatus] = []
    gerais: list[LogisticaStatus] = []
    for s in rows:
        if _norm(s.status_plataforma) != chave:
            continue
        sp = _norm(s.plataforma)
        if sp and sp == plat:
            especificas.append(s)
        elif not sp:
            gerais.append(s)
    return especificas or gerais


def _aplicaveis_agora(
    rules: list[LogisticaStatus], status_bling: str | None
) -> list[LogisticaStatus]:
    """Regras cujo `status_atual` (o "DE" da transição) casa com a situação atual
    do pedido no Bling — ou curingas (sem `status_atual`, valem de qualquer
    estado). É a máquina de estados: as ações de uma regra só valem quando o
    pedido está no estado dela. Se nada casar, devolve só os curingas."""
    atual = _norm(status_bling)
    return [r for r in rules if not _norm(r.status_atual) or _norm(r.status_atual) == atual]


def regra_ativa(
    rules: list[LogisticaStatus], status_bling: str | None
) -> LogisticaStatus | None:
    """A regra a MOSTRAR/aplicar AGORA, desambiguada pela situação atual do Bling.

    Quando a mesma chave tem várias linhas (máquina de estados), o que aparece na
    UI (match/setinha/resumo de ações) tem que respeitar ONDE o pedido está: se há
    uma regra cujo `status_atual` casa com o Bling atual, é ELA; senão a curinga
    (sem `status_atual`, vale de qualquer estado); só em último caso a primeira."""
    if not rules:
        return None
    atual = _norm(status_bling)
    if atual:
        for r in rules:
            if _norm(r.status_atual) == atual:
                return r
    for r in rules:
        if not _norm(r.status_atual):
            return r
    return rules[0]


def deve_monitorar(
    rules: list[LogisticaStatus], status_bling: str | None = None
) -> bool:
    """True se alguma regra APLICÁVEL AO ESTADO ATUAL pede monitoramento (o pedido
    fica no painel pra acompanhar mesmo depois de resolvido). Uma regra de outro
    estado (`status_atual` diferente do Bling atual) não monitora agora."""
    aplicaveis = _aplicaveis_agora(rules, status_bling) if status_bling is not None else rules
    return any(bool(r.monitoramento) for r in aplicaveis)


def estado_resolvido(
    rules: list[LogisticaStatus],
    status_bling: str | None,
    *,
    threema_enviado: bool = False,
) -> bool:
    """True quando o pedido casa uma regra da aba Status e NÃO há mais nada a
    fazer — o painel esconde esses (`resolvido and not monitorar`).

    "Nada a fazer" cobre dois casos:
      - a regra casada não pede AÇÃO NENHUMA (sem mudança de status, sem chamado/
        reembolso, sem mensagens) — é só uma chave "conhecida/ok"; ou
      - a regra tem mudança de status e o pedido já chegou ao alvo final da
        cadeia (status atual == algum `alterar_status_bling`, sem transição
        exata pendente a partir dele).

    Qualquer ação MANUAL pendente (abrir chamado/reembolso ou mensagem de
    chamado/Bling/Threema) mantém a linha visível — ainda há trabalho. A
    Mensagem Threema deixa de contar quando `threema_enviado` é True (o operador
    já disparou o aviso da linha → feito). O monitoramento é ortogonal (o front
    combina com `not acao_monitorar`)."""
    if not rules:
        return False
    atual = _norm(status_bling)
    # Só pesam as regras APLICÁVEIS ao estado atual do Bling. Uma regra de OUTRO
    # estado (status_atual diferente do atual) não conta agora — máquina de
    # estados: ela vale quando o pedido chegar naquele estado.
    aplicaveis = _aplicaveis_agora(rules, status_bling)
    # Ação manual pendente no estado atual → ainda há o que fazer, não esconde.
    for r in aplicaveis:
        if r.abrir_chamado or r.abrir_reembolso:
            return False
        if (r.mensagem_chamado or "").strip() or (r.mensagem_bling or "").strip():
            return False
        if (r.mensagem_threema or "").strip() and not threema_enviado:
            return False
    # Transição de status ainda pendente a partir do estado atual → não esconde.
    for r in aplicaveis:
        alvo = _norm(r.alterar_status_bling)
        if alvo and alvo != atual:
            return False
    if aplicaveis:
        return True  # há regra pro estado atual e nada pendente → resolvido
    # Nenhuma regra aplicável ao estado atual (todas são de outros estados). Se
    # nenhuma delas muda status, é chave "conhecida/ok" → nada a fazer (some).
    # Senão, só resolve quando o pedido chegou a algum alvo da cadeia.
    alvos = {_norm(r.alterar_status_bling) for r in rules if _norm(r.alterar_status_bling)}
    if not alvos:
        return True
    return bool(atual) and atual in alvos


def resumo_acoes(rule: LogisticaStatus | None) -> list[str]:
    """Resumo legível do que o sistema faria pra essa regra (só as ações
    preenchidas). Lista vazia = regra sem nenhuma ação (nada a fazer)."""
    if rule is None:
        return []
    out: list[str] = []
    alvo = (rule.alterar_status_bling or "").strip()
    if alvo:
        out.append(f"Status Bling → {alvo}")
    if rule.monitoramento:
        out.append("Monitorar")
    if rule.abrir_chamado:
        out.append("Abrir chamado")
    if rule.abrir_reembolso:
        out.append("Abrir reembolso")
    if (rule.mensagem_chamado or "").strip():
        out.append("Mensagem do chamado")
    if (rule.mensagem_bling or "").strip():
        out.append("Mensagem Bling")
    if (rule.mensagem_threema or "").strip():
        out.append("Mensagem Threema")
    return out
