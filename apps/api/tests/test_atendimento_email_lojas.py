# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""Os e-mails de cada LOJA na barra de lojas da Caixa (27/10/2026).

O Eduardo, 27/10: "botar um AMPLIÁVEL que vai abrir os e-mails embaixo de
cada loja". No mesmo dia: embaixo de cada loja só Informativo e Chamado (a
Venda fica na aba E-mail; ele ainda escolhe quais entram — a escolha é UMA
constante, `TIPOS_EMAIL_NA_BARRA_DE_LOJAS`) e a aba E-mail inteira só para
admin (quem não é admin vê e-mail SÓ pela barra). Sem rota nova: as que a aba
E-mail já usa, com a loja.

O que não pode falhar:
  • GET …/email/contagens?por_loja=true — os números de CADA loja (a
    integração, ou a ficha sem integração), nos tipos da barra, com a MESMA
    régua das contagens da aba E-mail (só a conversa de e-mail, o filtro de
    acesso da pessoa, o escopo da equipe), numa consulta só: os números de
    cima são os mesmos do caminho sem `por_loja`; sem `por_loja`, o contrato
    de sempre;
  • a ficha cuja integração existe (o par nome + plataforma, a régua da
    ponte) soma na loja da integração — na barra e na lista;
  • o chamado do site (sem loja) não vira linha de loja;
  • GET /conversas?caixa=email&integration_id=…|store_info_id=…&tipo_email=…
    — a lista da loja; `store_info_id` só no e-mail;
  • as linhas das pastas informativas (sem conversa) só para quem MEXE e só
    das pastas que valem (a régua de GET /informativos);
  • quem não é admin: só a lista de UMA loja num tipo da barra (o resto da
    aba E-mail é 403 `aba_email_so_admin`); admin: tudo como antes;
  • a loja bloqueada na política de acesso some dos números da pessoa.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import AtendimentoConversa, MailFolder, StoreInfo, UserRole
from app.services.mail_atendimento import classificacao
from app.services.mail_atendimento import constantes as mail_constantes
from tests.test_atendimento_separa_email import URL, _ids
from tests.test_atendimento_separa_email import admin as admin
from tests.test_atendimento_separa_email import quem_le as quem_le
from tests.test_atendimento_separa_email_ui import _historico
from tests.test_mail_admin_atendimento import _salvar
from tests.test_mail_ponte import _config, cena, conversa, entrar, meta_de, rodar

EMAIL = f"{URL}/email"
VAZIO = {"abertas": 0, "aguardando": 0, "total": 0, "linhas": 0}
SO_ADMIN = "aba_email_so_admin"


async def _tipo(db, conversa_id, tipo: str, **campos):
    c = await db.get(AtendimentoConversa, conversa_id)
    c.dados = {**c.dados, "mail": {**c.dados["mail"], "tipo_email": tipo}}
    for k, v in campos.items():
        setattr(c, k, v)
    await db.commit()


async def _ficha_sem_integracao(db, cena, tipo: str = "chamado") -> tuple[str, str]:
    """O e-mail da JLAS2 (ficha SEM integração, alias 16tr): a conversa da ficha."""
    mid = await entrar(db, cena.geral, para=["16tr@tuta.com"], de="ana@gmail.com")
    await rodar(db)
    meta = await meta_de(db, mid)
    c = await conversa(db, meta.conversa_id)
    assert c.integration_id is None and meta.store_info_id is not None
    await _tipo(db, c.id, tipo)
    return str(c.id), str(meta.store_info_id)


async def _linha_informativa(db, cena, *, pasta: str = "avisos ml") -> tuple:
    """Uma linha de pasta INFORMATIVA da loja do ML (sem conversa), numa pasta
    CRIADA com a categoria informativo (a que vale em GET /informativos)."""
    mid = await entrar(db, cena.geral, para=["21max@tuta.com"], de="avisos@mercadolivre.com.br")
    await rodar(db)
    meta = await meta_de(db, mid)
    folder = MailFolder(
        mailbox_id=cena.geral.id,
        chave=pasta,
        nome=pasta,
        caminho=pasta,
        tipo_tuta="0",
        categoria="informativo",
    )
    db.add(folder)
    await db.flush()
    meta.estado = "informativo"
    meta.conversa_id = None
    meta.mensagem_id = None
    meta.folder_id = folder.id
    await db.commit()
    assert meta.integration_id == cena.ml.id
    return mid, folder


async def _resposta(client) -> dict:
    r = await client.get(f"{EMAIL}/contagens", params={"por_loja": "true"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


async def _por_loja(client) -> dict:
    return {
        (i["integration_id"] or i["store_info_id"]): i
        for i in (await _resposta(client))["por_loja"]
    }


async def _linhas_do_ml(client, cena) -> int:
    item = (await _por_loja(client)).get(str(cena.ml.id))
    return item["tipos"]["informativo"]["linhas"] if item else 0


def test_a_escolha_do_dono_e_uma_constante_so():
    # Informativo e Chamado (27/10/2026); a Venda fica na aba E-mail.
    assert mail_constantes.TIPOS_EMAIL_NA_BARRA_DE_LOJAS == ("informativo", "chamado")
    assert set(mail_constantes.TIPOS_EMAIL_NA_BARRA_DE_LOJAS) <= set(classificacao.TIPOS_EMAIL)


async def test_contagens_por_loja_so_dos_tipos_da_barra(db, cena, client, admin):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    await _tipo(db, h["oliveira"], "chamado")
    _, ficha = await _ficha_sem_integracao(db, cena)

    # Sem `por_loja`: o contrato de sempre (a aba E-mail não muda).
    simples = (await client.get(f"{EMAIL}/contagens")).json()
    assert "por_loja" not in simples and "tipos_na_barra" not in simples

    r = await _resposta(client)
    assert r["tipos_na_barra"] == ["informativo", "chamado"]
    # Os números de cima: os MESMOS do caminho sem `por_loja` (uma consulta só, agrupada).
    assert {k: r[k] for k in simples} == simples
    lojas = {(i["integration_id"] or i["store_info_id"]): i for i in r["por_loja"]}
    # A loja só com Venda NÃO aparece (a Venda não está na barra); o chamado do
    # site não tem loja: fica na fila própria.
    assert set(lojas) == {str(cena.oliveira_ml.id), ficha}
    for item in lojas.values():
        assert set(item["tipos"]) == {"informativo", "chamado"}, "a Venda fica fora da barra"
        assert "total" not in item
    assert lojas[str(cena.oliveira_ml.id)]["tipos"] == {
        "informativo": VAZIO,
        "chamado": {"abertas": 1, "aguardando": 1, "total": 1, "linhas": 0},
    }
    assert lojas[str(cena.oliveira_ml.id)]["loja"], "o nome da loja (o da aba E-mail)"
    # A ficha sem integração: pelo id da ficha, com o NOME (a barra acha a loja do robô).
    assert lojas[ficha]["integration_id"] is None and lojas[ficha]["loja"] == "JLAS2"
    assert lojas[ficha]["tipos"]["chamado"]["abertas"] == 1

    # Fechou: sai das abertas, fica no total.
    await _tipo(db, h["oliveira"], "chamado", situacao="fechada", aguardando_resposta=False)
    item = (await _por_loja(client))[str(cena.oliveira_ml.id)]
    assert item["tipos"]["chamado"] == {"abertas": 0, "aguardando": 0, "total": 1, "linhas": 0}
    geral = await _resposta(client)
    assert geral["por_tipo_email"] == {"informativo": 0, "chamado": 2, "venda": 1}
    simples = (await client.get(f"{EMAIL}/contagens")).json()
    assert {k: geral[k] for k in simples} == simples


async def test_trocar_a_constante_troca_a_barra_e_o_que_quem_le_lista(
    db, cena, client, admin, auth_as, quem_le, monkeypatch
):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    auth_as(cena.dono)
    assert str(cena.ml.id) not in await _por_loja(client)
    # O dono escolhe outra lista: é SÓ esta constante (números, tipos e o acesso).
    monkeypatch.setattr(mail_constantes, "TIPOS_EMAIL_NA_BARRA_DE_LOJAS", ("venda",))
    r = await _resposta(client)
    assert r["tipos_na_barra"] == ["venda"]
    [ml] = [i for i in r["por_loja"] if i["integration_id"] == str(cena.ml.id)]
    assert ml["tipos"] == {"venda": {"abertas": 1, "aguardando": 0, "total": 1, "linhas": 0}}
    auth_as(quem_le)
    venda = await client.get(
        f"{URL}/conversas",
        params={"caixa": "email", "integration_id": str(cena.ml.id), "tipo_email": "venda"},
    )
    assert venda.status_code == 200, venda.text
    assert [i["id"] for i in venda.json()["itens"]] == [h["ml"]]


async def test_ficha_com_integracao_pelo_par_soma_na_loja(db, cena, client, admin):
    """A conversa que a ponte ligou só à FICHA (FK vazio), cuja integração
    existe pelo par nome + plataforma (a Magalu "vortan" em produção): na linha
    da loja da integração — os números e a lista."""
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena)
    ids = {"integration_id": str(cena.oliveira_ml.id), "caixa": "email", "filtro": "todas"}
    assert ficha_cid not in await _ids(client, **ids, tipo_email="chamado")
    antes = (await _por_loja(client))[str(cena.oliveira_ml.id)]["tipos"]["chamado"]["abertas"]
    # A ficha JLAS2 passa a ter o nome da integração "oliveira-ml" (mesma plataforma).
    f = await db.get(StoreInfo, ficha)
    f.account_name = "Oliveira-ML "
    await db.commit()
    lojas = await _por_loja(client)
    assert ficha not in lojas, "a ficha não fica sozinha: soma na loja da integração"
    assert lojas[str(cena.oliveira_ml.id)]["tipos"]["chamado"]["abertas"] == antes + 1
    assert ficha_cid in await _ids(client, **ids, tipo_email="chamado")
    # A loja vizinha não ganha nada.
    assert ficha_cid not in await _ids(
        client, caixa="email", filtro="todas", integration_id=str(cena.ml.id), tipo_email="chamado"
    )
    # Duas integrações com o mesmo par: ninguém adivinha — a ficha fica sozinha.
    f.account_name = "barbosa-ml"
    await db.commit()
    lojas = await _por_loja(client)
    assert lojas[str(cena.ml.id)]["tipos"]["chamado"]["abertas"] == 1
    from tests.test_mail_ponte import _integ

    await _integ(db, cena.dono, cena.ml.platform, "barbosa-ml")
    await db.commit()
    lojas = await _por_loja(client)
    assert lojas[ficha]["loja"] == "barbosa-ml"
    assert str(cena.ml.id) not in lojas


async def test_lista_da_loja_por_tipo_com_a_rota_da_caixa(db, cena, client, admin):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    await _tipo(db, h["oliveira"], "chamado")
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena, tipo="informativo")

    def ids(**p):
        return _ids(client, caixa="email", filtro="todas", **p)

    # Admin: a aba E-mail inteira, como antes (a Venda também).
    assert await ids(integration_id=str(cena.ml.id), tipo_email="venda") == {h["ml"]}
    assert await ids(integration_id=str(cena.ml.id), tipo_email="chamado") == set()
    assert await ids(integration_id=str(cena.oliveira_ml.id), tipo_email="chamado") == {
        h["oliveira"]
    }
    assert await ids(store_info_id=ficha, tipo_email="informativo") == {ficha_cid}
    assert h["novo"] in await ids(), "o chamado do site (a aba E-mail inteira)"
    # A ficha de uma loja COM integração não traz a conversa dela por aqui.
    assert await ids(store_info_id=str(cena.loja_ml.id)) == set()
    # A ficha é a loja do e-mail: no chat, 422 (a loja do robô filtra pelo canal).
    r = await client.get(f"{URL}/conversas", params={"store_info_id": ficha})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "store_info_id_so_no_email"
    # A Caixa (o chat) continua sem e-mail nenhum, com a loja escolhida.
    assert await _ids(client, integration_id=str(cena.ml.id), filtro="todas") == set()
    assert await _ids(client, integration_id=str(cena.shopee.id), filtro="todas") == {h["chat"]}


async def test_quem_nao_e_admin_ve_email_so_pela_barra(db, cena, client, quem_le, auth_as):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    await _tipo(db, h["oliveira"], "chamado")
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena, tipo="informativo")
    auth_as(quem_le)

    async def lista(**p):
        return await client.get(f"{URL}/conversas", params={"caixa": "email", **p})

    # A barra: os números por loja e a lista de UMA loja num tipo da barra.
    lojas = await _por_loja(client)
    assert set(lojas) == {str(cena.oliveira_ml.id), ficha}
    r = await lista(integration_id=str(cena.oliveira_ml.id), tipo_email="chamado")
    assert r.status_code == 200 and [i["id"] for i in r.json()["itens"]] == [h["oliveira"]]
    r = await lista(store_info_id=ficha, tipo_email="informativo", filtro="fechadas")
    assert r.status_code == 200, r.text
    # A conversa de e-mail aberta pela barra (os cartões) continua.
    assert (await client.get(f"{EMAIL}/conversas/{h['oliveira']}/emails")).status_code == 200
    # O resto da aba E-mail: 403 — todas as lojas, sem tipo, a Venda, o site, a saúde.
    for p in (
        {},
        {"filtro": "aguardando"},
        {"tipo_email": "chamado"},
        {"integration_id": str(cena.ml.id)},
        {"integration_id": str(cena.ml.id), "tipo_email": "venda"},
        {"tipo_chamado": "sac"},
    ):
        r = await lista(**p)
        assert r.status_code == 403, (p, r.text)
        assert r.json()["detail"]["code"] == SO_ADMIN
    for rota in ("saude", "filas", "informativos", "envios", "pastas", "regras"):
        r = await client.get(f"{EMAIL}/{rota}")
        assert r.status_code == 403, (rota, r.text)
    r = await client.get(f"{EMAIL}/saude")
    assert r.json()["detail"]["code"] == SO_ADMIN
    # O chat da Caixa não muda.
    assert await _ids(client, integration_id=str(cena.shopee.id), filtro="todas") == {h["chat"]}


async def test_admin_continua_com_a_aba_inteira(db, cena, client, admin):
    await _historico(db, cena)
    for rota in ("saude", "filas", "informativos", "envios", "pastas", "regras"):
        r = await client.get(f"{EMAIL}/{rota}")
        assert r.status_code == 200, (rota, r.text)
    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "filtro": "todas"})
    assert r.status_code == 200 and r.json()["itens"]


async def test_linhas_informativas_so_para_quem_mexe_e_das_pastas_que_valem(
    db, cena, client, admin, auth_as, make_user
):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    mid, folder = await _linha_informativa(db, cena)

    lojas = await _por_loja(client)
    assert lojas[str(cena.ml.id)]["tipos"]["informativo"]["linhas"] == 1
    r = await client.get(
        f"{EMAIL}/emails", params={"fila": "informativo", "integration_id": str(cena.ml.id)}
    )
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["itens"]] == [str(mid)]
    # O item Informativos da aba conta a MESMA linha.
    itens = (await client.get(f"{EMAIL}/informativos")).json()["itens"]
    assert [i["total"] for i in itens if i["pasta_id"] == str(folder.id)] == [1]
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

    # A pasta deixou de ser informativa: some da barra e da lista da loja juntos
    # (como some do item Informativos da aba).
    folder.categoria = None
    await db.commit()
    assert await _linhas_do_ml(client, cena) == 0
    r = await client.get(
        f"{EMAIL}/emails", params={"fila": "informativo", "integration_id": str(cena.ml.id)}
    )
    assert r.json()["itens"] == []
    assert not [
        i for i in (await client.get(f"{EMAIL}/informativos")).json()["itens"] if i["total"]
    ]
    folder.categoria = "informativo"
    await db.commit()

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
    await _tipo(db, h["ml"], "chamado")
    await _linha_informativa(db, cena)

    lojas = await _por_loja(client)
    assert lojas[str(cena.ml.id)]["tipos"]["chamado"]["abertas"] == 1
    assert lojas[str(cena.ml.id)]["tipos"]["informativo"]["linhas"] == 0
    r = await client.get(
        f"{EMAIL}/emails", params={"fila": "informativo", "integration_id": str(cena.ml.id)}
    )
    assert r.status_code == 403


async def test_loja_bloqueada_e_escopo_somem_dos_numeros(
    db, cena, client, make_user, auth_as, admin
):
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "chamado")
    await _tipo(db, h["oliveira"], "chamado")
    leitor = await make_user(role=UserRole.USER)
    await _salvar(client, auth_as, cena, leitor, lojas_bloqueadas=[f"i:{cena.ml.id}"])
    lojas = await _por_loja(client)
    assert str(cena.ml.id) not in lojas, "a loja bloqueada na política de acesso some"
    assert (
        await _ids(
            client,
            caixa="email",
            filtro="todas",
            integration_id=str(cena.ml.id),
            tipo_email="chamado",
        )
        == set()
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


async def test_pasta_sem_categoria_nao_conta_linha(db, cena, client, admin):
    """A linha numa pasta do SISTEMA (a Entrada: nunca tem categoria que vale)
    não entra no número da barra nem na lista — como no item Informativos."""
    h = await _historico(db, cena)
    await _tipo(db, h["ml"], "venda")
    mid = await entrar(db, cena.geral, para=["21max@tuta.com"], de="avisos@mercadolivre.com.br")
    await rodar(db)
    meta = await meta_de(db, mid)
    meta.estado = "informativo"
    meta.conversa_id = None
    meta.mensagem_id = None
    await db.commit()
    entrada = await db.get(MailFolder, meta.folder_id)
    entrada.categoria = "informativo"
    await db.commit()
    assert await _linhas_do_ml(client, cena) == 0
    r = await client.get(
        f"{EMAIL}/emails", params={"fila": "informativo", "integration_id": str(cena.ml.id)}
    )
    assert r.json()["itens"] == []
    pastas = (await db.execute(select(MailFolder.id))).scalars().all()
    assert entrada.id in pastas
