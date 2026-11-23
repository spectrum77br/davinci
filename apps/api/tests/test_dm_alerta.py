"""O aviso de que o robô desistiu.

M-20 (23/11/2026): o aviso é um alerta ADM no sino (antes o Telegram).

Duas coisas se testam aqui, e a segunda é a que evita vergonha:

  1. O texto do CLIENTE vai como texto simples, mascarado: o sino escapa na
     tela, então uma DM com "<" não vira marcação nem derruba o aviso.
  2. A enxurrada. O cron roda a cada minuto pegando até 10 conversas: uma
     queda do provedor vira 600 avisos por hora, e o único que importava
     ("pediu atendente") afoga no meio.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models import DmConversa
from app.redis_client import redis
from app.services import dm_alerta


@pytest.fixture(autouse=True)
async def _limpa_redis():
    async def apaga():
        chaves = [k async for k in redis.scan_iter("dm:alerta:*")]
        if chaves:
            await redis.delete(*chaves)

    await apaga()
    yield
    await apaga()


def _conversa(conta: str = "charlots_br") -> DmConversa:
    return DmConversa(id=uuid4(), conta=conta, participante_id="1")


@pytest.fixture
def enviado():
    with patch.object(dm_alerta, "avisar_administradores", new=AsyncMock(return_value=True)) as m:
        yield m


def _textos(mock) -> list[str]:
    """Título + mensagem de cada alerta (M-20: o alerta ADM no sino)."""
    saida = []
    for c in mock.call_args_list:
        titulo = c.args[0]
        mensagem = c.args[1] if len(c.args) > 1 else c.kwargs.get("mensagem")
        saida.append(f"{titulo}\n{mensagem or ''}")
    return saida


# ─────────────── o que vai no aviso ───────────────


async def test_avisa_com_a_conta_e_o_trecho(enviado):
    await dm_alerta.avisar(
        _conversa(),
        chave="pediu_humano",
        motivo="pediu atendente",
        texto="quero falar com uma pessoa",
    )
    (texto,) = _textos(enviado)
    assert "@charlots_br" in texto
    assert "Pediu atendente" in texto
    assert "quero falar com uma pessoa" in texto


async def test_texto_do_cliente_vai_como_texto_simples(enviado):
    """O "<" numa DM não derruba o aviso: vai como texto, sem escapar duas
    vezes (o sino escapa na tela; antes o Telegram exigia `html.escape`)."""
    await dm_alerta.avisar(
        _conversa(),
        chave="pediu_humano",
        motivo="pediu atendente",
        texto="a mala <b>grande</b> & a de 24 <script>",
    )
    (texto,) = _textos(enviado)
    assert "a mala <b>grande</b> & a de 24 <script>" in texto
    assert "&lt;" not in texto and "&amp;" not in texto


async def test_mascara_dado_pessoal_antes_do_sino(enviado):
    await dm_alerta.avisar(
        _conversa(),
        chave="sem_resposta",
        motivo="provedor devolveu 429",
        texto="meu cpf é 123.456.789-00 e o cartão 4111 1111 1111 1111",
    )
    (texto,) = _textos(enviado)
    assert "123.456.789-00" not in texto
    assert "4111" not in texto
    assert "[CPF]" in texto


async def test_motivo_aparece_quando_nao_e_caso_conhecido(enviado):
    await dm_alerta.avisar(_conversa(), chave="sem_resposta", motivo="provedor devolveu 429")
    (texto,) = _textos(enviado)
    assert "provedor devolveu 429" in texto


# ─────────────── dedup ───────────────


async def test_mesma_conversa_mesmo_problema_avisa_uma_vez(enviado):
    c = _conversa()
    for _ in range(5):
        await dm_alerta.avisar(c, chave="sem_resposta", motivo="429")
    assert enviado.await_count == 1


async def test_problema_diferente_na_mesma_conversa_avisa_de_novo(enviado):
    c = _conversa()
    await dm_alerta.avisar(c, chave="sem_resposta", motivo="429")
    await dm_alerta.avisar(c, chave="pediu_humano", motivo="pediu atendente")
    assert enviado.await_count == 2


async def test_conversas_diferentes_avisam_separado(enviado):
    await dm_alerta.avisar(_conversa(), chave="sem_resposta", motivo="429")
    await dm_alerta.avisar(_conversa(), chave="sem_resposta", motivo="429")
    assert enviado.await_count == 2


# ─────────────── a enxurrada ───────────────


async def test_teto_por_hora_silencia_e_avisa_que_silenciou(enviado):
    """Queda do provedor: sai o teto, mais UMA mensagem dizendo que calou."""
    for _ in range(dm_alerta.TETO_POR_HORA + 20):
        await dm_alerta.avisar(_conversa(), chave="sem_resposta", motivo="429")

    textos = _textos(enviado)
    assert len(textos) == dm_alerta.TETO_POR_HORA + 1
    assert "Silenciando" in textos[-1]
    assert "silenciados" in textos[-1]


# ─────────────── nunca derruba o envio de DM ───────────────


async def test_sino_fora_do_ar_nao_levanta():
    with patch.object(
        dm_alerta,
        "avisar_administradores",
        new=AsyncMock(side_effect=RuntimeError("banco caiu")),
    ):
        await dm_alerta.avisar(_conversa(), chave="sem_resposta", motivo="429")


async def test_redis_fora_do_ar_ainda_avisa(enviado):
    """Sem Redis o risco é avisar duas vezes. Perder o aviso é pior."""
    with patch.object(
        dm_alerta.redis, "set", new=AsyncMock(side_effect=RuntimeError("redis caiu"))
    ):
        await dm_alerta.avisar(_conversa(), chave="pediu_humano", motivo="x")
    assert enviado.await_count == 1
