"""Data de etiqueta/NF não deve ser apresentada como postagem ao comprador."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.atendimento import contexto, ia

PEDIDO = "2610191X0P2AWN"


def _conversa(*, plataforma="shopee", status="PROCESSED", enviado_em=None, pedido=PEDIDO):
    return SimpleNamespace(
        id=uuid4(),
        plataforma=plataforma,
        canal="chat",
        pedido_marketplace=PEDIDO,
        dados={
            "pedido_mkt": {
                "pedido": pedido,
                "status": status,
                "enviado_em": enviado_em,
                "logistica": {"status": "LOGISTICS_REQUEST_CREATED"},
            }
        },
    )


def _ctx(*, postagem=None):
    return {
        "pedido": {
            "numero": "304859",
            "numeroloja": PEDIDO,
            "situacao": "Em digitação",
            "enviado_em": "2026-10-19",
        },
        "logistica": {
            "rastreio": "BR264390750687U",
            "status": "Pedido em preparação",
            "data_envio": postagem,
        },
    }


@pytest.mark.parametrize("status", ["PROCESSED", "READY_TO_SHIP", ""])
def test_etiqueta_com_coleta_solicitada_nao_e_postagem(status):
    conversa = _conversa(status=status)
    ctx = _ctx()

    contexto._ajustar_data_envio_shopee(conversa, ctx)

    assert ctx["pedido"]["enviado_em"] is None
    assert ia.valores_das_lacunas(conversa, ctx)["data_envio"] is None
    # Gerar rastreio não prova que o pacote foi entregue à transportadora.
    assert ia.valores_das_lacunas(conversa, ctx)["rastreio"] == "BR264390750687U"


def test_postagem_real_da_logistica_prevalece_sobre_retrato_em_preparacao():
    conversa = _conversa()
    ctx = _ctx(postagem="2026-10-20")

    contexto._ajustar_data_envio_shopee(conversa, ctx)

    assert ia.valores_das_lacunas(conversa, ctx)["data_envio"] == "20/10/2026"


def test_data_de_envio_da_plataforma_prevalece_sobre_data_interna_do_bling():
    conversa = _conversa(status="SHIPPED", enviado_em="2026-10-20T15:00:00+00:00")
    ctx = _ctx()

    contexto._ajustar_data_envio_shopee(conversa, ctx)

    assert ia.valores_das_lacunas(conversa, ctx)["data_envio"] == "20/10/2026"


def test_data_invalida_no_retrato_nao_vira_fato_de_envio():
    conversa = _conversa(enviado_em="a confirmar")
    ctx = _ctx()

    contexto._ajustar_data_envio_shopee(conversa, ctx)

    assert ia.valores_das_lacunas(conversa, ctx)["data_envio"] is None


@pytest.mark.parametrize(
    "conversa",
    [
        _conversa(plataforma="ml"),
        _conversa(pedido="OUTRO_PEDIDO"),
        _conversa(status="SHIPPED"),
        _conversa(status="COMPLETED"),
        _conversa(status="IN_TRANSIT"),
    ],
)
def test_nao_altera_outro_pedido_plataforma_ou_envio_ja_confirmado(conversa):
    ctx = _ctx()
    antes = deepcopy(ctx)

    contexto._ajustar_data_envio_shopee(conversa, ctx)

    assert ctx == antes


def test_retrato_sem_informacao_de_preparacao_nao_inventa_data_nova():
    conversa = _conversa(status="")
    conversa.dados["pedido_mkt"]["logistica"] = None
    ctx = _ctx()
    antes = deepcopy(ctx)

    contexto._ajustar_data_envio_shopee(conversa, ctx)

    assert ctx == antes


async def test_contexto_real_aplica_protecao_antes_de_entregar_fatos(monkeypatch):
    conversa = _conversa()
    fonte = _ctx()

    async def _pedido(*args, **kwargs):
        return deepcopy(fonte["pedido"])

    async def _logistica(*args, **kwargs):
        return deepcopy(fonte["logistica"])

    async def _vazio(*args, **kwargs):
        return []

    async def _sem_nf(*args, **kwargs):
        return None

    class SessionSemConexao:
        def begin_nested(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(contexto, "_pedido", _pedido)
    monkeypatch.setattr(contexto, "_logistica", _logistica)
    monkeypatch.setattr(contexto, "_avaliacoes", _vazio)
    monkeypatch.setattr(contexto, "_chamados", _vazio)
    monkeypatch.setattr(contexto, "_devolucoes", _vazio)
    monkeypatch.setattr(contexto, "_reclamacoes", _vazio)
    monkeypatch.setattr(contexto, "_nota_fiscal", _sem_nf)

    ctx = await contexto.contexto_da_conversa(SessionSemConexao(), conversa)

    assert ctx["pedido"]["numero"] == "304859"
    assert ctx["pedido"]["enviado_em"] is None
    assert ia.valores_das_lacunas(conversa, ctx)["data_envio"] is None
