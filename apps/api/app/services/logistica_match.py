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


def deve_monitorar(rules: list[LogisticaStatus]) -> bool:
    """True se alguma regra casada pede monitoramento (o pedido fica no painel
    pra acompanhar mesmo depois de resolvido)."""
    return any(bool(r.monitoramento) for r in rules)


def estado_resolvido(rules: list[LogisticaStatus], status_bling: str | None) -> bool:
    """True quando o pedido casa uma regra COM mudança de status, já chegou a um
    dos alvos da cadeia (status atual == algum `alterar_status_bling`) e não há
    mais transição pendente a partir daí — ou seja, o fim da máquina de estados.

    Serve pro painel esconder o que já foi tratado: `resolvido and not monitorar`.
    Usa o `status_bling` atual da linha (que o recarregar sincroniza com o Bling
    vivo). Regras sem `alterar_status_bling` (ex.: só mensagem/monitorar) NUNCA
    contam como resolvidas — não há status a concluir."""
    if not rules:
        return False
    atual = _norm(status_bling)
    if not atual:
        return False
    alvos = {_norm(r.alterar_status_bling) for r in rules if _norm(r.alterar_status_bling)}
    if atual not in alvos:
        return False  # ainda não chegou a nenhum alvo da cadeia
    for r in rules:
        alvo = _norm(r.alterar_status_bling)
        if not alvo:
            continue
        de = _norm(r.status_atual)
        if de:
            if de == atual and alvo != atual:
                return False  # há transição exata a seguir a partir daqui
        elif alvo != atual:
            return False  # curinga (vale de qualquer estado) ainda não no alvo
    return True


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
