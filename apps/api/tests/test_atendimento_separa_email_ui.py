"""A aba E-mail › Filas › "Todas as conversas" (13/10/2026).

Até 13/10/2026 a Caixa achava QUALQUER conversa de e-mail pela busca (nome,
pedido, protocolo) e pelos filtros Todas/Fechadas. O e-mail saiu da Caixa
(`services/atendimento/separa_email.py`) e as filas de conversa da aba E-mail
(chamados dos sites, falta responder) só trazem as abertas que precisam de
alguém — o histórico (a respondida, a fechada, o chamado agrupado) sumia da
tela. "Todas as conversas" (AtendimentoMailFilas) é a MESMA rota da Caixa com
`?caixa=email`: as abertas (`filtro=todas`, respondidas ou não) e, no chip, as
fechadas (`filtro=fechadas`), com a mesma busca e o mesmo "carregar mais".

O que não pode falhar (sem mudar nada no backend — é a régua da Caixa):
  • `caixa=email` + Todas/Fechadas: só o e-mail, nunca o chat;
  • a busca acha pelo nome, pelo pedido e por QUALQUER protocolo do chamado
    (o agrupado também), nas abertas e nas fechadas;
  • o "carregar mais" (`antes_de`) passa por todas sem repetir;
  • o escopo da equipe vale como na Caixa (e nas contagens), e quem só lê vê
    o mesmo que via na Caixa.
"""

from __future__ import annotations

from datetime import timedelta

from app.models import AtendimentoConversa, StoreInfo
from app.services.mail_atendimento import chamados, ponte
from tests.test_atendimento_separa_email import (
    URL,
    _chat,
    _ids,
    _ids_pela_barra,
    _lista_inteira_recusada,
    _tipo_email,
)
from tests.test_atendimento_separa_email import (
    admin as admin,  # noqa: F401 — a fixture do dono autenticado
)
from tests.test_atendimento_separa_email import (
    quem_le as quem_le,  # noqa: F401 — a fixture de quem só lê
)
from tests.test_mail_ponte import AGORA, conversa, entrar, meta_de, rodar
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte

EMAIL = f"{URL}/email"
PEDIDO = "2000077776666"


async def _chamado(db, cena, protocolo: str, minutos: int):
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="sac@uranyx.com.br",
        de_nome="Site Uranyx",
        para=["sac@uranyx.com.br"],
        assunto=f"[{protocolo}] Dúvida",
        corpo="Nome: Maria\nE-mail: maria@gmail.com\nMensagem: oi",
        recebido_em=AGORA - timedelta(minutes=minutos),
    )
    await rodar(db)
    return await conversa(db, (await meta_de(db, mid)).conversa_id)


async def _historico(db, cena) -> dict:
    """O chat, o e-mail da loja RESPONDIDO (com pedido), o da Oliveira e dois
    chamados do site, o mais velho agrupado no mais novo (fica fechado) — cada
    um num horário (o empate de horário tem a regra própria de `_paginar`)."""
    chat = await _chat(db, cena.shopee, "shopee", "chat-1")
    mid_ml = await entrar(
        db,
        cena.geral,
        de="joana@gmail.com",
        de_nome="Joana Prado",
        recebido_em=AGORA - timedelta(minutes=10),
    )
    await rodar(db)
    ml = await conversa(db, (await meta_de(db, mid_ml)).conversa_id)
    mid_oli = await entrar(
        db,
        cena.goslin,
        de="pedro@gmail.com",
        de_nome="Pedro Lima",
        para=["mia30@tuta.com"],
        recebido_em=AGORA - timedelta(minutes=20),
    )
    await rodar(db)
    # 21/10/2026: na Entrada da Goslin, mia30 é das duas lojas da Oliveira (ML e
    # Shopee) — a pessoa escolhe a loja na fila "E-mail sem loja".
    rota = ponte.rotear.Rota(
        alias="mia30@tuta.com",
        store_info_id=cena.loja_oliveira_ml.id,
        integration_id=cena.oliveira_ml.id,
        plataforma="ml",
    )
    await ponte.reprocessar(db, mid_oli, rota_forcada=rota)
    await db.commit()
    oliveira = await conversa(db, (await meta_de(db, mid_oli)).conversa_id)
    velho = await _chamado(db, cena, "US-26-0001", 30)
    novo = await _chamado(db, cena, "US-26-0002", 40)
    await chamados.agrupar(db, velho, novo, cena.dono)
    # Respondida (não espera mais) e com o pedido: sai do "falta responder",
    # continua em "Todas as conversas" (as abertas).
    ml = await db.get(AtendimentoConversa, ml.id)
    ml.aguardando_resposta = False
    ml.situacao = "respondida"
    ml.pedido_marketplace = PEDIDO
    await db.commit()
    velho = await conversa(db, velho.id)
    assert velho.situacao == "fechada", "o agrupado fecha o mais velho"
    assert (ml.integration_id, oliveira.integration_id) == (cena.ml.id, cena.oliveira_ml.id)
    return {
        "chat": str(chat.id),
        "ml": str(ml.id),
        "oliveira": str(oliveira.id),
        "velho": str(velho.id),
        "novo": str(novo.id),
    }


async def test_todas_as_conversas_abertas_e_fechadas_so_do_email(db, cena, client, admin):
    h = await _historico(db, cena)
    abertas = {h["ml"], h["oliveira"], h["novo"]}
    assert await _ids(client, caixa="email", filtro="todas") == abertas, "a respondida também"
    assert await _ids(client, caixa="email", filtro="fechadas") == {h["velho"]}
    assert h["ml"] not in await _ids(client, caixa="email", filtro="aguardando")
    # A Caixa (Todas e Fechadas) não traz nenhuma delas.
    assert await _ids(client, filtro="todas") == {h["chat"]}
    assert await _ids(client, filtro="fechadas") == set()
    # O "carregar mais": uma por página, todas as abertas, sem repetir.
    vistos: list[str] = []
    antes_de = None
    for _ in range(10):
        params = {"caixa": "email", "filtro": "todas", "limite": 1}
        if antes_de:
            params["antes_de"] = antes_de
        r = await client.get(f"{URL}/conversas", params=params)
        assert r.status_code == 200, r.text
        vistos += [i["id"] for i in r.json()["itens"]]
        antes_de = r.json()["proximo"]
        if not antes_de:
            break
    assert sorted(vistos) == sorted(abertas) and len(vistos) == len(set(vistos))


async def test_busca_pelo_nome_pedido_e_qualquer_protocolo(db, cena, client, admin):
    h = await _historico(db, cena)

    async def achou(q: str, filtro: str = "todas") -> set[str]:
        return await _ids(client, caixa="email", filtro=filtro, q=q)

    assert await achou("Joana") == {h["ml"]}
    assert await achou(PEDIDO) == {h["ml"]}, "pelo pedido (a respondida também)"
    assert await achou("US-26-0002") == {h["novo"]}
    # O protocolo do agrupado: o chamado que ficou (aberto) e o fechado (no chip).
    assert await achou("US-26-0001") == {h["novo"]}
    assert await achou("US-26-0001", "fechadas") == {h["velho"]}
    assert await achou("Joana", "fechadas") == set()
    # A busca da aba E-mail não traz o chat.
    assert await achou("Comprador") == set()


async def test_escopo_da_equipe_e_quem_so_le(db, cena, client, make_user, auth_as, quem_le):
    h = await _historico(db, cena)
    for cid in (h["ml"], h["oliveira"]):
        await _tipo_email(db, cid, "chamado")
    # Quem só lê (sem equipe = sem corte): o mesmo que via na Caixa — desde
    # 27/10/2026 pela barra de lojas (a aba E-mail inteira é só de admin).
    await _lista_inteira_recusada(client)
    assert await _ids_pela_barra(client, integration_id=str(cena.ml.id), filtro="todas") == {
        h["ml"]
    }
    assert await _ids_pela_barra(
        client, integration_id=str(cena.oliveira_ml.id), filtro="todas"
    ) == {h["oliveira"]}
    contagens = (await client.get(f"{EMAIL}/contagens")).json()
    assert (contagens["aguardando"], contagens["chamados"]) == (2, 1), "a Oliveira e o chamado"
    # A equipe da Barbosa (só a loja do ML dela): só a conversa de e-mail dela.
    loja = await db.get(StoreInfo, cena.loja_ml.id)
    loja.sales_team = 7
    membro = await make_user()
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)
    assert await _ids_pela_barra(client, integration_id=str(cena.ml.id), filtro="todas") == {
        h["ml"]
    }
    assert (
        await _ids_pela_barra(client, integration_id=str(cena.ml.id), filtro="todas", q="Pedro")
        == set()
    )
    assert (
        await _ids_pela_barra(client, integration_id=str(cena.oliveira_ml.id), filtro="todas")
        == set()
    ), "a loja de fora do escopo"
    await _lista_inteira_recusada(client)
    r = await client.get(f"{EMAIL}/contagens")
    assert r.status_code == 200, r.text
    assert r.json()["aguardando"] == 0 and r.json()["chamados"] == 0
    assert (await client.get(f"{URL}/conversas/{h['oliveira']}")).status_code == 404
