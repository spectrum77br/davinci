"""Leitura diária da caixa do Tuta atrás dos códigos de devolução.

Eduardo (16/09/2026): "um agente que entra no tuta email, e pega os codigos de
devolução todo dia as 7:00 e encaminha a mensagem para o threema para o
usuário thatcher".

O Tuta não tem IMAP nem API pública e as regras de caixa dele não encaminham
para fora, então a leitura passa pelo robô do Mac. O robô devolve o texto cru
da tela; quem decide o que é devolução é este serviço — é isso que estes
testes travam.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text

from app.models import LogisticaRoboComando
from app.services import tuta_devolucoes as td

pytestmark = pytest.mark.asyncio


CAIXA = """Amazon
Autorização de postagem da devolução do pedido 702-555
ontem
Mercado Livre
Você tem uma nova venda
09:12
Correios
Código de postagem da logística reversa: PJ123456789BR
08:40
Newsletter
Promoções da semana
07:55
"""


def test_reconhece_as_linhas_de_devolucao_e_ignora_o_resto():
    achados = td._linhas_de_devolucao(CAIXA)
    assert any("Autorização de postagem da devolução" in ln for ln in achados)
    assert any("logística reversa" in ln for ln in achados)
    assert not any("Promoções da semana" in ln for ln in achados)
    assert not any("nova venda" in ln for ln in achados)


def test_mensagem_traz_os_achados():
    msg = td.montar_mensagem(json.dumps({"ok": True, "texto": CAIXA, "conta": "x@tuta.com"}))
    assert "x@tuta.com" in msg
    assert "PJ123456789BR" in msg
    assert "Promoções da semana" not in msg


def test_dia_sem_devolucao_mostra_o_que_havia_na_caixa():
    """Silêncio é ambíguo: quem lê precisa saber se o robô falhou ou se o dia
    foi vazio mesmo."""
    caixa = "Mercado Livre\nVocê tem uma nova venda\n09:12\nNewsletter\nPromoções\n07:55\n"
    msg = td.montar_mensagem(json.dumps({"ok": True, "texto": caixa}))
    assert "nenhum e-mail de devolução" in msg.lower()
    assert "nova venda" in msg


def test_falha_do_robo_vira_aviso_de_falha_nao_de_caixa_vazia():
    msg = td.montar_mensagem(
        json.dumps({"ok": False, "reason": "needs_manual_login: entre no Tuta"})
    )
    assert "não consegui ler" in msg.lower()
    assert "needs_manual_login" in msg


def test_resultado_ilegivel_nao_quebra():
    assert "não consegui ler" in td.montar_mensagem("isso não é json").lower()
    assert "não consegui ler" in td.montar_mensagem(None).lower()


async def test_enfileira_uma_vez_por_dia(db):
    await db.execute(text("DELETE FROM logistica_robo_comando"))
    await db.commit()

    primeiro = await td.enfileirar(db)
    assert primeiro is not None
    # O cron pode rodar duas vezes num deploy; não pode criar comando repetido.
    assert await td.enfileirar(db) is None

    linhas = (
        await db.execute(
            select(LogisticaRoboComando).where(LogisticaRoboComando.acao == td.ACAO)
        )
    ).scalars().all()
    assert len(linhas) == 1
    # Comando de caixa de e-mail não pertence a nenhum pedido.
    assert linhas[0].logistica_id is None
    assert linhas[0].status == "pending"


async def test_sem_destinatario_com_threema_nao_quebra(db, monkeypatch):
    await db.execute(text("UPDATE users SET threema = NULL WHERE lower(name) = 'thatcher'"))
    await db.commit()
    res = await td.entregar_resultado(db, json.dumps({"ok": True, "texto": CAIXA}))
    assert res["enviado"] is False
