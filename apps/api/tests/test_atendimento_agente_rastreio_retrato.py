"""Item 31 da auditoria ao vivo (22/10/2026 07:55, Barbosa Shopee, 79b5d2f5 — saiu
sozinho): a linha da logística parada em "Pedido postado" e o retrato fresco do MESMO
pedido com o último evento da Shopee ("…na área de transporte … aguardando veículo",
de 18/10). O agente passa a ver o evento do retrato; o "parado desde o dia 18" com o
último evento de 3+ dias vai para pessoa (abrir_chamado, só a etiqueta na Caixa)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.atendimento.agente import acao_pessoa, pacote

AGORA = datetime(2026, 10, 22, 10, 55, tzinfo=UTC)
EVENTO = (
    "Seu pedido já está na área de transporte em Piracicaba - SP aguardando veículo, "
    "aguarde novas atualizações."
)


def _retrato(
    *,
    fonte: str = "shopee",
    lido: datetime = AGORA - timedelta(minutes=4),
    evento_em: datetime = datetime(2026, 10, 18, 18, 0, tzinfo=UTC),
    descricao: str = EVENTO,
    status: str = "SHIPPED",
    pedido: str = "261017S666MD4M",
) -> dict:
    return {
        "fonte": fonte,
        "pedido": pedido,
        "status": status,
        "atualizado_em": lido.isoformat(),
        "logistica": {"descricao": descricao, "atualizado_em": evento_em.isoformat()},
    }


# ── O evento do retrato ────────────────────────────────────────────────────


def test_o_evento_do_retrato_fresco_da_shopee():
    texto, quando, lido = acao_pessoa.evento_do_retrato(_retrato(), AGORA)
    assert texto == EVENTO
    assert quando == datetime(2026, 10, 18, 18, 0, tzinfo=UTC)
    assert lido == AGORA - timedelta(minutes=4)


@pytest.mark.parametrize(
    "retrato",
    [
        _retrato(fonte="ml"),
        _retrato(lido=AGORA - timedelta(hours=7)),  # velho
        _retrato(lido=AGORA + timedelta(minutes=1)),  # depois do agora (replay)
        _retrato(evento_em=AGORA),  # evento depois da leitura
        _retrato(descricao=" "),
        None,
    ],
)
def test_sem_o_evento_fresco_nada(retrato):
    assert acao_pessoa.evento_do_retrato(retrato, AGORA) is None


# ── O texto do evento sem o lugar ──────────────────────────────────────────


@pytest.mark.parametrize(
    "texto, esperado",
    [
        (
            EVENTO,
            "Seu pedido já está na área de transporte aguardando veículo, "
            "aguarde novas atualizações.",
        ),
        (
            "Seu pedido saiu do centro logístico: Piracicaba - SP",
            "Seu pedido saiu do centro logístico",
        ),
        (
            "O pedido chegou ao centro de : São Bernardo do Campo - SP "
            "e segue para o próximo destino.",
            "O pedido chegou ao centro e segue para o próximo destino.",
        ),
    ],
)
def test_o_evento_sai_sem_a_cidade(texto, esperado):
    assert pacote.status_do_evento_sem_cidade(texto) == esperado


# ── O último status do bloco do pedido ─────────────────────────────────────


def test_o_retrato_fresco_troca_o_status_velho_da_linha():
    log = {"status": "Pedido postado Piracicaba - SP"}
    assert pacote._ultimo_status(log, _retrato(), entregue=False, agora=AGORA) == (
        "Seu pedido já está na área de transporte aguardando veículo, aguarde novas atualizações."
    )


def test_a_linha_lida_depois_do_retrato_fica():
    log = {"status": "Objeto em trânsito", "status_lido_em": AGORA.isoformat()}
    assert (
        pacote._ultimo_status(log, _retrato(), entregue=False, agora=AGORA) == "Objeto em trânsito"
    )


def test_entregue_na_linha_ou_no_retrato_nao_troca():
    log = {"status": "Pedido postado"}
    assert pacote._ultimo_status(log, _retrato(), entregue=True, agora=AGORA) == "Pedido postado"
    entregue = _retrato(status="COMPLETED")
    assert pacote._ultimo_status(log, entregue, entregue=False, agora=AGORA) == "Pedido postado"


def test_sem_retrato_fica_a_linha():
    log = {"status": "Pedido postado"}
    assert pacote._ultimo_status(log, None, entregue=False, agora=AGORA) == "Pedido postado"


# ── A reclamação do rastreio parado ────────────────────────────────────────


@pytest.mark.parametrize(
    "plano",
    [
        "ola consegue ve onde esta pq aqui esta parado desde do dia 18 nao atualiza",
        "sem movimentacao ha 4 dias",
        "meu pedido parou de atualizar",
        "o rastreio nao anda",
        "ta no mesmo lugar faz dias",
    ],
)
def test_reclama_que_esta_parado(plano):
    assert acao_pessoa.reclama_que_esta_parado(plano)


@pytest.mark.parametrize(
    "plano",
    [
        "a tela ficou parada",
        "o celular nao atualiza",
        "se continuar parado eu cancelo",
        "o aparelho parou de funcionar",
    ],
)
def test_nao_e_o_rastreio_parado(plano):
    assert not acao_pessoa.reclama_que_esta_parado(plano)


# ── A régua: rastreio parado com o retrato de 3+ dias ───────────────────────


def _conversa(retrato: dict) -> SimpleNamespace:
    return SimpleNamespace(dados={"pedido_mkt": retrato}, pedido_marketplace="261017S666MD4M")


def _ctx(**extra) -> dict:
    return {"pedido": {"numeroloja": "261017S666MD4M", "situacao": "Em andamento"}, **extra}


PLANO = "ola consegue ve onde esta pq aqui esta parado desde do dia 18 nao atualiza"


def test_o_79b5d2f5_vai_para_pessoa():
    assert acao_pessoa.rastreio_parado_no_retrato(
        _conversa(_retrato()), _ctx(), PLANO, postado=acao_pessoa.POSTADO_SIM, agora=AGORA
    )


def test_evento_recente_nao_e_parado():
    recente = _retrato(evento_em=AGORA - timedelta(days=1))
    assert not acao_pessoa.rastreio_parado_no_retrato(
        _conversa(recente), _ctx(), PLANO, postado=acao_pessoa.POSTADO_SIM, agora=AGORA
    )


def test_com_chamado_aberto_ou_nao_postado_nada():
    assert not acao_pessoa.rastreio_parado_no_retrato(
        _conversa(_retrato()),
        _ctx(chamados=[{"id": 1}]),
        PLANO,
        postado=acao_pessoa.POSTADO_SIM,
        agora=AGORA,
    )
    assert not acao_pessoa.rastreio_parado_no_retrato(
        _conversa(_retrato()), _ctx(), PLANO, postado=acao_pessoa.POSTADO_NAO, agora=AGORA
    )


def test_retrato_de_outro_pedido_nada():
    outro = _retrato(pedido="OUTRO123")
    assert not acao_pessoa.rastreio_parado_no_retrato(
        _conversa(outro), _ctx(), PLANO, postado=acao_pessoa.POSTADO_SIM, agora=AGORA
    )
