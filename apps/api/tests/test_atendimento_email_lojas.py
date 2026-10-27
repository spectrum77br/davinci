# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""Os e-mails de cada LOJA na barra de lojas da Caixa (27/10/2026).

O Eduardo, 27/10: "botar um AMPLIÁVEL que vai abrir os e-mails embaixo de
cada loja, com as 3 categorias linkadas pra cada loja — INFORMATIVO, CHAMADO
e VENDA". Sem rota nova: as que a aba E-mail já usa, com a loja.

O que não pode falhar:
  • GET …/email/contagens?por_loja=true — os números de CADA loja (a
    integração, ou a ficha sem integração), por tipo, com a MESMA régua das
    contagens da aba E-mail (só a conversa de e-mail, o filtro de acesso da
    pessoa, o escopo da equipe); sem `por_loja`, o contrato de sempre;
  • o chamado do site (sem loja) não vira linha de loja;
  • GET /conversas?caixa=email&integration_id=…|store_info_id=…&tipo_email=…
    — a lista da loja; `store_info_id` só no e-mail;
  • as linhas das pastas informativas (sem conversa) só para quem MEXE, e
    `/emails?fila=informativo` filtra pela loja sem mostrar mais que a fila;
  • a loja bloqueada na política de acesso some dos números da pessoa.
"""

from __future__ import annotations

from app.models import AtendimentoConversa, StoreInfo, UserRole
from tests.test_atendimento_separa_email import URL, _ids
from tests.test_atendimento_separa_email import admin as admin
from tests.test_atendimento_separa_email import quem_le as quem_le
from tests.test_atendimento_separa_email_ui import _historico
from tests.test_mail_admin_atendimento import _salvar
from tests.test_mail_ponte import _config, cena, conversa, entrar, meta_de, rodar

EMAIL = f"{URL}/email"
VAZIO = {"abertas": 0, "aguardando": 0, "total": 0, "linhas": 0}


async def _tipo(db, conversa_id, tipo: str, **campos):
    c = await db.get(AtendimentoConversa, conversa_id)
    c.dados = {**c.dados, "mail": {**c.dados["mail"], "tipo_email": tipo}}
    for k, v in campos.items():
        setattr(c, k, v)
    await db.commit()


async def _ficha_sem_integracao(db, cena) -> tuple[str, str]:
    """O e-mail da JLAS2 (ficha SEM integração, alias 16tr): a conversa da ficha."""
    mid = await entrar(db, cena.geral, para=["16tr@tuta.com"], de="ana@gmail.com")
    await rodar(db)
    meta = await meta_de(db, mid)
    c = await conversa(db, meta.conversa_id)
    assert c.integration_id is None and meta.store_info_id is not None
    await _tipo(db, c.id, "venda")
    return str(c.id), str(meta.store_info_id)


async def _por_loja(client) -> dict:
    r = await client.get(f"{EMAIL}/contagens", params={"por_loja": "true"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return {(i["integration_id"] or i["store_info_id"]): i for i in r.json()["por_loja"]}


async def test_contagens_por_loja_e_por_tipo(db, cena, client, admin):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    await _tipo(db, h["oliveira"], "chamado")
    _, ficha = await _ficha_sem_integracao(db, cena)

    # Sem `por_loja`: o contrato de sempre (a página antiga não muda).
    r = await client.get(f"{EMAIL}/contagens")
    assert "por_loja" not in r.json()

    lojas = await _por_loja(client)
    # O chamado do site não tem loja: fica na fila própria, não numa linha.
    assert set(lojas) == {str(cena.ml.id), str(cena.oliveira_ml.id), ficha}
    ml = lojas[str(cena.ml.id)]
    assert ml["plataforma"] == "ml" and ml["store_info_id"] is None and ml["total"] == 1
    # A respondida continua ABERTA (o número dos chips de "Todas as conversas").
    assert ml["tipos"] == {
        "informativo": VAZIO,
        "chamado": VAZIO,
        "venda": {"abertas": 1, "aguardando": 0, "total": 1, "linhas": 0},
    }
    assert lojas[str(cena.oliveira_ml.id)]["tipos"]["chamado"] == {
        "abertas": 1,
        "aguardando": 1,
        "total": 1,
        "linhas": 0,
    }
    # A ficha sem integração: pelo id da ficha, com o NOME (a barra acha a loja do robô).
    assert lojas[ficha]["integration_id"] is None and lojas[ficha]["loja"] == "JLAS2"
    assert lojas[ficha]["tipos"]["venda"]["abertas"] == 1

    # Fechou: sai das abertas, continua no total (a loja tem e-mail).
    await _tipo(db, h["ml"], "venda", situacao="fechada", aguardando_resposta=False)
    ml = (await _por_loja(client))[str(cena.ml.id)]
    assert ml["tipos"]["venda"] == {"abertas": 0, "aguardando": 0, "total": 1, "linhas": 0}
    # Os números de CIMA continuam os mesmos das lojas somadas (mesma régua).
    geral = (await client.get(f"{EMAIL}/contagens", params={"por_loja": "true"})).json()
    assert geral["por_tipo_email"] == {"informativo": 0, "chamado": 2, "venda": 1}


async def test_lista_da_loja_por_tipo_com_a_rota_da_caixa(db, cena, client, admin):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    await _tipo(db, h["oliveira"], "chamado")
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena)

    def ids(**p):
        return _ids(client, caixa="email", filtro="todas", **p)

    assert await ids(integration_id=str(cena.ml.id), tipo_email="venda") == {h["ml"]}
    assert await ids(integration_id=str(cena.ml.id), tipo_email="chamado") == set()
    assert await ids(integration_id=str(cena.oliveira_ml.id), tipo_email="chamado") == {
        h["oliveira"]
    }
    assert await ids(store_info_id=ficha, tipo_email="venda") == {ficha_cid}
    # A ficha de uma loja COM integração não traz a conversa dela por aqui.
    assert await ids(store_info_id=str(cena.loja_ml.id)) == set()
    # A ficha é a loja do e-mail: no chat, 422 (a loja do robô filtra pelo canal).
    r = await client.get(f"{URL}/conversas", params={"store_info_id": ficha})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "store_info_id_so_no_email"
    # A Caixa (o chat) continua sem e-mail nenhum, com a loja escolhida.
    assert await _ids(client, integration_id=str(cena.ml.id), filtro="todas") == set()
    assert await _ids(client, integration_id=str(cena.shopee.id), filtro="todas") == {h["chat"]}


async def test_linhas_informativas_so_para_quem_mexe(db, cena, client, admin, auth_as, make_user):
    await _historico(db, cena)
    # Uma linha de pasta INFORMATIVA da loja do ML (sem conversa).
    mid = await entrar(db, cena.geral, para=["21max@tuta.com"], de="avisos@mercadolivre.com.br")
    await rodar(db)
    meta = await meta_de(db, mid)
    meta.estado = "informativo"
    meta.conversa_id = None
    meta.mensagem_id = None
    await db.commit()
    assert meta.integration_id == cena.ml.id

    lojas = await _por_loja(client)
    assert lojas[str(cena.ml.id)]["tipos"]["informativo"]["linhas"] == 1
    r = await client.get(
        f"{EMAIL}/emails", params={"fila": "informativo", "integration_id": str(cena.ml.id)}
    )
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["itens"]] == [str(mid)]
    outra = await client.get(
        f"{EMAIL}/emails", params={"fila": "informativo", "integration_id": str(cena.shopee.id)}
    )
    assert outra.json()["itens"] == []
    ambigua = await client.get(
        f"{EMAIL}/emails",
        params={
            "fila": "informativo",
            "integration_id": str(cena.ml.id),
            "store_info_id": str(cena.loja_ml.id),
        },
    )
    assert ambigua.status_code == 422

    # A equipe de OUTRA loja (não mexe: só admin mexe): nem a linha nem o número dela.
    loja = await db.get(StoreInfo, cena.loja_oliveira_ml.id)
    loja.sales_team = 9
    membro = await make_user()
    membro.sales_teams = [9]
    await db.commit()
    auth_as(membro)
    assert str(cena.ml.id) not in await _por_loja(client)
    r = await client.get(
        f"{EMAIL}/emails", params={"fila": "informativo", "integration_id": str(cena.ml.id)}
    )
    assert r.status_code == 403


async def test_quem_so_le_ve_as_conversas_sem_as_linhas(db, cena, client, quem_le):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    mid = await entrar(db, cena.geral, para=["21max@tuta.com"], de="avisos@mercadolivre.com.br")
    await rodar(db)
    meta = await meta_de(db, mid)
    meta.estado = "informativo"
    meta.conversa_id = None
    meta.mensagem_id = None
    await db.commit()

    lojas = await _por_loja(client)
    assert lojas[str(cena.ml.id)]["tipos"]["venda"]["abertas"] == 1
    assert lojas[str(cena.ml.id)]["tipos"]["informativo"]["linhas"] == 0
    r = await client.get(
        f"{EMAIL}/emails", params={"fila": "informativo", "integration_id": str(cena.ml.id)}
    )
    assert r.status_code == 403


async def test_loja_bloqueada_e_escopo_somem_dos_numeros(
    db, cena, client, make_user, auth_as, admin
):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    await _tipo(db, h["oliveira"], "chamado")
    leitor = await make_user(role=UserRole.USER)
    await _salvar(client, auth_as, cena, leitor, lojas_bloqueadas=[f"i:{cena.ml.id}"])
    lojas = await _por_loja(client)
    assert str(cena.ml.id) not in lojas, "a loja bloqueada na política de acesso some"
    assert (
        await _ids(client, caixa="email", filtro="todas", integration_id=str(cena.ml.id)) == set()
    )

    # A equipe da Barbosa (só a loja do ML dela): só a linha dela.
    auth_as(cena.dono)
    loja = await db.get(StoreInfo, cena.loja_ml.id)
    loja.sales_team = 7
    membro = await make_user()
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)
    assert set(await _por_loja(client)) == {str(cena.ml.id)}
