"""Painel novo de Devoluções (Vinicius, 16/11/2026) — as contas da tela.

O painel antigo (abas Acompanhamento / Fraude / Lançamentos) continua igual; o
novo é UMA lista: entra quem cair em Aguardando Devolução no Bling e só sai no
botão Resolver. Aqui ficam as funções PURAS que a rota usa (etapa, condição
mostrada, prazo da etapa, conta do reembolso, "pronto pra resolver") — sem
banco, pra testar à vontade.

O que NÃO muda (dois revisores conferiram em 16/11): os valores gravados. A
condição no banco segue com os 7 nomes (Financeiro, Garantia, Conciliação e a
situação do Bling leem o NOME); só o rótulo da tela vira Novo / Usado /
Perdimento. Manutenção é etapa: condição "Manutenção" sem destino.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

SITUACAO_AGUARDANDO = "83957"
SITUACAO_MANUTENCAO = "84677"
# Situações em que o pedido ainda está "em devolução" no Bling.
SITUACOES_ABERTAS = (SITUACAO_AGUARDANDO, SITUACAO_MANUTENCAO)

AGUARDANDO = "aguardando"
CHEGOU = "chegou"
MANUTENCAO = "manutencao"
FRAUDE = "fraude"

# Condições que provam que o produto CHEGOU (as neutras e o Extraviado falam
# de pacote que não chegou).
_CONDICOES_COM_PRODUTO = {"Novo", "Usado", "Manutenção", "Sucata", "Trocado"}
# Estados da entrada no estoque que ainda pedem alguém (devolution_stock_return).
_ESTOQUE_PENDENTE = {"falhou", "conferindo", "sem_confirmacao"}
# Prazo da plataforma vencido há mais que isso não é mais pendência.
_PRAZO_VELHO = timedelta(days=2)

COMO_TERMINOU = (
    "Voltou pro estoque",
    "Ganhamos o reembolso",
    "Perdimento",
    "Extraviado",
    "Cliente não devolveu",
    "Cliente cancelou a devolução",
    "Entrou por engano",
)


@dataclass(frozen=True)
class Lancamento:
    """O que a conta precisa saber de UMA linha lançada (tabela devolutions)."""

    sku: str | None
    condicao: str | None
    motivo: str | None = None
    manutencao_destino: str | None = None
    troca_condicao: str | None = None
    estoque_status: str | None = None
    custo_produto: float | None = None
    custo_manutencao: float | None = None
    prazo_manutencao: datetime | None = None
    prazo_contestacao: datetime | None = None


def _txt(valor: str | None) -> str:
    return (valor or "").strip()


def manutencao_pendente(lanc: Lancamento) -> bool:
    """Com o técnico: condição Manutenção e ninguém escolheu ainda pra onde o
    produto vai (Novo / Usado / Sucata)."""
    return _txt(lanc.condicao) == "Manutenção" and not _txt(lanc.manutencao_destino)


def condicao_da_tela(lanc: Lancamento) -> str | None:
    """Rótulo da coluna Condição: Novo, Usado ou Perdimento (Vinicius: "não
    existe manutenção. ou é novo, ou usado ou perdimento"). Sucata = Perdimento.
    Manutenção com o técnico não tem condição ainda (é etapa); depois de
    voltar, vale o destino. Extraviado e as neutras (Não devolvido, Entregue)
    seguem com o nome que têm — são pacote que não chegou, não estado do
    produto, e o Bling/Financeiro distinguem Extraviado de Perdimento."""
    c = _txt(lanc.condicao)
    if not c:
        return None
    if c == "Manutenção":
        c = _txt(lanc.manutencao_destino)
        if not c:
            return None
    elif c == "Trocado":
        c = _txt(lanc.troca_condicao) or "Trocado"
    return "Perdimento" if c == "Sucata" else c


def etapa(
    *,
    fila: str | None,
    situacao_bling: str | None,
    chegou_em: date | None,
    lancamentos: list[Lancamento],
) -> str:
    """Onde o produto está. Manutenção ganha (produto com o técnico = o pacote
    chegou); Fraude é o painel de hoje (não volta pacote); Chegou = a
    plataforma/rastreio confirmou ou já existe lançamento de produto recebido."""
    if _txt(situacao_bling) == SITUACAO_MANUTENCAO or any(
        manutencao_pendente(x) for x in lancamentos
    ):
        return MANUTENCAO
    if fila == FRAUDE:
        return FRAUDE
    if chegou_em is not None or any(
        _txt(x.condicao) in _CONDICOES_COM_PRODUTO for x in lancamentos
    ):
        return CHEGOU
    return AGUARDANDO


def falta_lancar(etapa_: str, lancamentos: list[Lancamento]) -> bool:
    """Chegou e ninguém lançou ainda (o cartão "Chegou (falta lançar)")."""
    return etapa_ == CHEGOU and not lancamentos


def _fim_do_dia(dia: date) -> datetime:
    return datetime.combine(dia, time(23, 59), tzinfo=SAO_PAULO).astimezone(UTC)


def prazo_da_etapa(
    *,
    etapa_: str,
    agora: datetime,
    prazo_resposta: datetime | None,
    acao_resposta: str | None,
    prazo_45_ate: date | None,
    prazo_45_estado: str | None,
    amazon_cancelar: bool,
    lancamentos: list[Lancamento],
    situacao_bling: str | None = SITUACAO_AGUARDANDO,
    tem_chamado: bool = False,
) -> dict[str, Any]:
    """A coluna Prazo: um prazo só por linha, o MAIS PRÓXIMO de vencer entre os
    que estão correndo, com a palavra dizendo do que é (Vinicius: "muda o prazo
    a cada etapa"). Candidatos: resposta à plataforma (disputa na Fraude,
    responder nas outras), contestação do pacote recebido, técnico (30 dias) e
    pacote chegar (45 dias). `estado`: "vencido" | "urgente" (menos de 24 h,
    ou os 5 dias finais dos 45) | None.

    Pra não virar alarme falso (revisão de 16/11): o prazo de resposta só vale
    com o pedido em Aguardando Devolução — é só ali que o robô de 30 min relê
    a plataforma, fora dali o prazo fica congelado; a contestação só conta
    enquanto ninguém escolheu o motivo nem há chamado (depois disso ela já
    saiu); e prazo vencido há mais de 2 dias sai da coluna — passado o prazo
    a plataforma decide sozinha, não há mais o que fazer."""
    piso = agora - _PRAZO_VELHO
    candidatos: list[tuple[datetime, str, str | None]] = []
    if (
        prazo_resposta is not None
        and prazo_resposta >= piso
        and _txt(situacao_bling) == SITUACAO_AGUARDANDO
    ):
        candidatos.append(
            (prazo_resposta, "disputa" if etapa_ == FRAUDE else "responder", acao_resposta)
        )
    for lanc in lancamentos:
        if (
            lanc.prazo_contestacao is not None
            and lanc.prazo_contestacao >= piso
            and not _txt(lanc.motivo)
            and not tem_chamado
        ):
            candidatos.append((lanc.prazo_contestacao, "contestar", None))
        if manutencao_pendente(lanc) and lanc.prazo_manutencao is not None:
            candidatos.append((lanc.prazo_manutencao, "técnico", None))
    if prazo_45_ate is not None and etapa_ == AGUARDANDO:
        candidatos.append((_fim_do_dia(prazo_45_ate), "pacote chegar", None))
    vazio = {"prazo_em": None, "prazo_tipo": None, "prazo_estado": None, "prazo_dica": None}
    if amazon_cancelar and not candidatos:
        return {
            "prazo_em": None,
            "prazo_tipo": "cancelar na Amazon",
            "prazo_estado": "urgente",
            "prazo_dica": "cliente não postou em 14 dias: cancelar no Seller Central",
        }
    if not candidatos:
        return vazio
    em, tipo, dica = min(candidatos, key=lambda c: c[0])
    falta = em - agora
    estado = None
    if tipo == "pacote chegar":
        # A régua dos 45 dias é por DIA (devolucao_prazo_acompanhamento): no
        # dia 45 já "passou do prazo"; nos 5 dias finais avisa.
        if prazo_45_estado == "vencido" or falta <= timedelta(0):
            estado = "vencido"
        elif prazo_45_estado == "vence_logo":
            estado = "urgente"
    elif falta <= timedelta(0):
        estado = "vencido"
    elif falta <= timedelta(hours=24):
        estado = "urgente"
    return {"prazo_em": em, "prazo_tipo": tipo, "prazo_estado": estado, "prazo_dica": dica}


def conta_do_reembolso(
    *, lancamentos: list[Lancamento], chamado_resolvido: bool, chamado_valor: float | None
) -> dict[str, float] | None:
    """A conta do bloco Reembolso, por PEDIDO (o chamado é um por pedido — em
    kit o valor não pode repetir por linha): + recebido pelo chamado − custo do
    produto perdido − custo de manutenção = resultado.

    Regra combinada com o Vinicius em 16/11: "o chamado ganhar 700 reais mas
    ter um custo de manutenção de 100 então ficaria 600". `recebido` só conta
    chamado CONCLUÍDO com valor positivo; concluído com prejuízo conta 0 aqui
    (o prejuízo do chamado já É o produto perdido — descontar de novo dobraria).
    None = nada a mostrar (sem custo e sem chamado concluído).

    Limite conhecido (revisão de 16/11): `chamados.valor_recuperado` é o
    resultado que a pessoa digita ao concluir, não necessariamente o que a
    plataforma pagou; se ela já descontou a manutenção ali, a conta desconta
    duas vezes. O campo "valor recebido" separado fica pra próxima etapa."""
    custo_produto = sum(
        float(x.custo_produto or 0)
        for x in lancamentos
        if _txt(x.condicao) in ("Sucata", "Extraviado")
    )
    custo_manutencao = sum(float(x.custo_manutencao or 0) for x in lancamentos)
    recebido = 0.0
    if chamado_resolvido and chamado_valor is not None and float(chamado_valor) > 0:
        recebido = float(chamado_valor)
    if not (custo_produto or custo_manutencao or recebido):
        return None
    return {
        "recebido": round(recebido, 2),
        "custo_produto": round(custo_produto, 2),
        "custo_manutencao": round(custo_manutencao, 2),
        "resultado": round(recebido - custo_produto - custo_manutencao, 2),
    }


def pronto_pra_resolver(
    *,
    situacao_bling: str | None,
    situacao_nome: str | None,
    lancamentos: list[Lancamento],
    tem_chamado_aberto: bool,
    chamado_resolvido: bool,
    chamado_valor: float | None,
) -> str | None:
    """Motivo pra aparecer em "Pronto pra resolver" (None = ainda não).

    Produto com o técnico ou estoque que não entrou no Bling segura sempre.
    Lançamento terminado (todas as peças com condição) = a devolução está
    resolvida MESMO com o chamado aberto (Vinicius 16/11/2026: "se já voltou
    estoque tá resolvido… o chamado em aberto é outra coisa, podemos resolver
    a devolução e depois só ajusta o custo do chamado quando o chamado
    terminar" — a conta do Reembolso lê o chamado na hora). Sem lançamento
    terminado, chamado aberto segura (é ele que está resolvendo o caso); senão
    basta um: o Bling já saiu de Aguardando Devolução/Manutenção, ou o chamado
    foi concluído com valor ganho."""
    # Produto com o técnico ou entrada no estoque que não confirmou: ainda há
    # o que fazer, mesmo com o chamado ganho ou o Bling já em outra situação.
    if any(
        manutencao_pendente(x) or _txt(x.estoque_status) in _ESTOQUE_PENDENTE for x in lancamentos
    ):
        return None
    terminado = bool(lancamentos) and all(_txt(x.condicao) for x in lancamentos)
    if tem_chamado_aberto:
        if not terminado:
            return None
        rotulos = sorted({condicao_da_tela(x) or "" for x in lancamentos} - {""})
        base = "lançada: " + ", ".join(rotulos) if rotulos else "lançada"
        return f"{base} · o chamado segue aberto"
    sit = _txt(situacao_bling)
    if sit and sit not in SITUACOES_ABERTAS:
        return f"o Bling já está em {situacao_nome or sit}"
    if lancamentos and all(_txt(x.condicao) for x in lancamentos):
        rotulos = sorted({condicao_da_tela(x) or "" for x in lancamentos} - {""})
        return "lançada: " + ", ".join(rotulos) if rotulos else "lançada"
    if chamado_resolvido and chamado_valor is not None and float(chamado_valor) > 0:
        return "chamado concluído com valor ganho"
    return None


def lancamento_da_linha(
    sku: str | None, lancamentos: list[Lancamento], *, linhas_do_pedido: int
) -> tuple[Lancamento | None, int]:
    """Qual lançamento mostrar na linha do produto: o do mesmo SKU; pedido de
    um produto só (kit que virou peças no lançamento) mostra o primeiro e diz
    quantos são. Devolve (lançamento, quantos a mais)."""
    alvo = _txt(sku).lower()
    do_sku = [x for x in lancamentos if alvo and _txt(x.sku).lower() == alvo]
    if do_sku:
        return do_sku[0], len(do_sku) - 1
    if linhas_do_pedido == 1 and lancamentos:
        return lancamentos[0], len(lancamentos) - 1
    return None, 0
