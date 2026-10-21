# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e da caixa
"""Pesquisa de metadados cifrados: páginas reais, permissões e conteúdo visível."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import MailMessage, UserRole
from app.models.mail_admin import MailAcesso, MailLixeira
from app.models.mail_atendimento import MailCaixaIndice
from app.routers import mail_caixa
from app.services.mail_atendimento import indice, ponte
from tests.test_mail_caixa import _lista, _min, _pastas, _por_id
from tests.test_mail_ponte import AGORA, _config, cena, entrar


@pytest.mark.parametrize(
    "busca", ["devolucao", "DEVOLUÇÃO", "  DeVoLuCaO  ", "joao", "JOÃO SILVA", "joao@exemplo.com"]
)
async def test_busca_assunto_e_remetente_sem_acento_ou_caixa(db, cena, client, auth_as, busca):
    mid = await entrar(
        db,
        cena.geral,
        assunto="Devolução do produto",
        de="joao@exemplo.com",
        de_nome="João Silva",
        recebido_em=_min(1),
    )
    await entrar(db, cena.geral, assunto="Outro aviso", de="ana@exemplo.com", de_nome="Ana")
    auth_as(cena.dono)
    r = await client.get(f"/api/mail/mailboxes/{cena.geral.id}/lista", params={"q": busca})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert [i["id"] for i in r.json()["itens"]] == [str(mid)]
    assert r.json()["proximo"] is None


async def test_busca_loja_do_email_antigo_fora_da_primeira_pagina(db, cena, client, auth_as):
    antigo = await entrar(
        db,
        cena.geral,
        pasta="problema amazon",
        para=["mike14@tuta.com"],
        assunto="Aviso antigo",
        recebido_em=_min(90),
    )
    for n in range(55):
        await entrar(db, cena.geral, assunto="Outro assunto", recebido_em=_min(n))
    auth_as(cena.dono)
    assert str(antigo) not in _por_id(await _lista(client, cena.geral))
    result = await _lista(client, cena.geral, q="mike")
    assert [i["id"] for i in result["itens"]] == [str(antigo)]
    assert result["itens"][0]["selo"]["loja"] == "Mike"


async def test_busca_combina_pasta_loja_e_assunto(db, cena, client, auth_as):
    alvo = await entrar(
        db, cena.geral, pasta="problema ml", assunto="Devolução confirmada", recebido_em=_min(3)
    )
    await entrar(
        db, cena.geral, pasta="vendas ml", assunto="Devolução confirmada", recebido_em=_min(2)
    )
    await entrar(
        db,
        cena.geral,
        pasta="problema ml",
        para=["mia30@tuta.com"],
        assunto="Devolução confirmada",
        recebido_em=_min(1),
    )
    auth_as(cena.dono)
    sem_filtro = _por_id(await _lista(client, cena.geral))
    pasta = sem_filtro[str(alvo)]["pasta"]["id"]
    loja = sem_filtro[str(alvo)]["selo"]["chave"]
    result = await _lista(client, cena.geral, q="devolucao", pasta=pasta, loja=loja)
    assert [i["id"] for i in result["itens"]] == [str(alvo)]
    # Busca não altera as contagens das pastas/lojas, nem vira novo filtro salvo.
    assert (await _pastas(client, cena.geral))["pastas"][0]["total"] == 3


async def test_busca_nao_compara_corpo_nem_assunto_oculto(db, cena, client, auth_as):
    oculto = await entrar(
        db,
        cena.geral,
        assunto="Código de verificação 731604: acesse sua conta",
        corpo="Use o código 731604 para entrar.",
        recebido_em=_min(1),
    )
    await entrar(
        db,
        cena.geral,
        assunto="Aviso comum",
        corpo="FRASE_PRESENTE_SOMENTE_NO_CORPO",
        recebido_em=_min(2),
    )
    auth_as(cena.dono)
    item = _por_id(await _lista(client, cena.geral))[str(oculto)]
    assert item["assunto_oculto"] and "731604" not in item["assunto"]
    for q in ("731604", "acesse sua conta", "FRASE_PRESENTE_SOMENTE_NO_CORPO"):
        result = await _lista(client, cena.geral, q=q)
        assert result["itens"] == [] and result["proximo"] is None
    result = await _lista(client, cena.geral, q=indice.ASSUNTO_DE_SEGURANCA)
    assert [i["id"] for i in result["itens"]] == [str(oculto)]


async def test_busca_respeita_lixeira_local_e_restauracao(db, cena, client, auth_as):
    mid = await entrar(db, cena.geral, assunto="Pedido na lixeira local", recebido_em=_min(1))
    tr = MailLixeira(message_id=mid, excluido_em=AGORA)
    db.add(tr)
    await db.commit()
    auth_as(cena.dono)
    assert (await _lista(client, cena.geral, q="lixeira local"))["itens"] == []
    tr.excluido_em = None
    await db.commit()
    assert [i["id"] for i in (await _lista(client, cena.geral, q="lixeira local"))["itens"]] == [
        str(mid)
    ]


async def test_busca_filtrada_nao_decifra_ou_revela_loja_negada(
    db, cena, client, make_user, auth_as, monkeypatch
):
    permitido = await entrar(
        db, cena.geral, pasta="problema ml", assunto="Pedido permitido", recebido_em=_min(2)
    )
    negado = await entrar(
        db,
        cena.geral,
        pasta="problema shopee",
        # A Shopee da Barbosa no alias dela (22max, 21/10/2026).
        para=["22max@tuta.com"],
        assunto="Assunto invisível Shopee",
        recebido_em=_min(1),
    )
    await indice.rodar(db)
    pessoa = await make_user(role=UserRole.USER)
    db.add(
        MailAcesso(
            mailbox_id=cena.geral.id,
            user_id=pessoa.id,
            modo="selecionadas",
            lojas_permitidas=[f"f:{cena.loja_ml.id}", f"i:{cena.ml.id}"],
            lojas_bloqueadas=[],
            tipos_permitidos=["informativo", "chamado", "venda"],
            pedidos_permitidos=[],
        )
    )
    await db.commit()
    decifrados = []
    original = ponte.decifrar

    def medir(m):
        decifrados.append(m.id)
        return original(m)

    monkeypatch.setattr(ponte, "decifrar", medir)
    auth_as(pessoa)
    result = await _lista(client, cena.geral, q="Pedido permitido")
    assert [i["id"] for i in result["itens"]] == [str(permitido)]
    for q in ("invisivel", "Shopee"):
        result = await _lista(client, cena.geral, q=q)
        assert result["itens"] == [] and result["proximo"] is None and result["faltam"] == 0
    assert negado not in decifrados


async def test_busca_nao_concede_acesso_a_outra_caixa(db, cena, client, make_user, auth_as):
    pessoa = await make_user(role=UserRole.USER)
    await entrar(db, cena.geral, assunto="Assunto pesquisado")
    auth_as(pessoa)
    r = await client.get(f"/api/mail/mailboxes/{cena.geral.id}/lista", params={"q": "Assunto"})
    assert r.status_code == 404
    assert await db.scalar(select(func.count()).select_from(MailCaixaIndice)) == 0


async def test_pagina_vazia_tem_continuacao_ate_o_email_antigo(
    db, cena, client, auth_as, monkeypatch
):
    monkeypatch.setattr(mail_caixa, "BUSCA_LOTE", 2)
    monkeypatch.setattr(mail_caixa, "BUSCA_LOTES", 1)
    mid = await entrar(db, cena.geral, assunto="Alvo distante", recebido_em=_min(5))
    for n in range(4):
        await entrar(db, cena.geral, assunto="Outro assunto", recebido_em=_min(n))
    auth_as(cena.dono)
    primeira = await _lista(client, cena.geral, q="distante")
    assert primeira["itens"] == [] and primeira["proximo"]
    segunda = await _lista(client, cena.geral, q="distante", antes=primeira["proximo"])
    assert segunda["itens"] == [] and segunda["proximo"] != primeira["proximo"]
    terceira = await _lista(client, cena.geral, q="distante", antes=segunda["proximo"])
    assert [i["id"] for i in terceira["itens"]] == [str(mid)]
    assert terceira["proximo"] is None


async def test_cursor_da_busca_nao_pula_ou_repete_empates_nem_sobra_do_lote(
    db, cena, client, auth_as, monkeypatch
):
    monkeypatch.setattr(mail_caixa, "BUSCA_LOTE", 3)
    monkeypatch.setattr(mail_caixa, "BUSCA_LOTES", 2)
    ids = [
        await entrar(db, cena.geral, assunto="Mesmo aviso", recebido_em=_min(1)) for _ in range(5)
    ]
    auth_as(cena.dono)
    achados, ponto = [], None
    for _ in range(6):
        pagina = await _lista(
            client, cena.geral, q="Mesmo aviso", limite=2, **({"antes": ponto} if ponto else {})
        )
        achados.extend(i["id"] for i in pagina["itens"])
        ponto = pagina["proximo"]
        if not ponto:
            break
    assert achados == [str(i) for i in sorted(ids, reverse=True)]
    assert len(set(achados)) == 5 and ponto is None


async def test_busca_vazia_equivale_a_lista_e_termo_longo_recusado(db, cena, client, auth_as):
    await entrar(db, cena.geral, assunto="Qualquer assunto")
    auth_as(cena.dono)
    normal = await _lista(client, cena.geral)
    assert await _lista(client, cena.geral, q="   ") == normal
    r = await client.get(f"/api/mail/mailboxes/{cena.geral.id}/lista", params={"q": "x" * 121})
    assert r.status_code == 422


async def test_versao_de_acesso_muda_com_pedido_permitido_mesmo_com_iguais_contagens(
    db, cena, client, make_user, auth_as
):
    from tests.test_mail_admin import _salvar
    from tests.test_mail_ponte import pedido_existe
    from tests.test_mail_venda_rotas import X, Y, _caso

    await pedido_existe(db, cena.ml, X)
    await pedido_existe(db, cena.ml, Y)
    x = await _caso(db, cena, "12345678", X)
    y = await _caso(db, cena, "87654321", Y)
    pessoa = await make_user(role=UserRole.USER)
    auth_as(cena.dono)
    primeira = await _salvar(
        client,
        cena,
        pessoa,
        modo="selecionadas",
        lojas_permitidas=[f"i:{cena.ml.id}"],
        pedidos_permitidos=[{"loja": f"i:{cena.ml.id}", "numero": X}],
    )
    auth_as(pessoa)
    antes = await _pastas(client, cena.geral)
    assert antes["pastas"][0]["total"] == 1
    assert [i["id"] for i in (await _lista(client, cena.geral))["itens"]] == [str(x.message_id)]
    auth_as(cena.dono)
    await _salvar(
        client,
        cena,
        pessoa,
        modo="selecionadas",
        lojas_permitidas=[f"i:{cena.ml.id}"],
        pedidos_permitidos=[{"loja": f"i:{cena.ml.id}", "numero": Y}],
        expected_updated_at=primeira["updated_at"],
    )
    auth_as(pessoa)
    depois = await _pastas(client, cena.geral)
    assert depois["pastas"][0]["total"] == 1
    assert depois["acesso_versao"] != antes["acesso_versao"]
    assert [i["id"] for i in (await _lista(client, cena.geral))["itens"]] == [str(y.message_id)]
    assert len(depois["acesso_versao"]) == 64
    assert X not in depois["acesso_versao"] and Y not in depois["acesso_versao"]


async def test_versao_de_acesso_nao_muda_com_sync_ou_email_novo_mas_muda_com_lixeira(
    db, cena, client, auth_as
):
    from datetime import timedelta

    mid = await entrar(db, cena.geral, assunto="Aviso", recebido_em=_min(1))
    auth_as(cena.dono)
    antes = (await _pastas(client, cena.geral))["acesso_versao"]
    cena.geral.last_sync_at = AGORA + timedelta(minutes=1)
    cena.geral.state = "online"
    await db.commit()
    await entrar(db, cena.geral, assunto="Chegou agora", recebido_em=AGORA)
    assert (await _pastas(client, cena.geral))["acesso_versao"] == antes
    tr = MailLixeira(message_id=mid, excluido_em=AGORA)
    db.add(tr)
    await db.commit()
    excluido = (await _pastas(client, cena.geral))["acesso_versao"]
    assert excluido != antes
    tr.excluido_em = None
    await db.commit()
    assert (await _pastas(client, cena.geral))["acesso_versao"] != excluido


async def test_versao_de_acesso_cobre_configuracao_e_equipe_resolvida(
    db, cena, client, auth_as, make_user
):
    from app.models.mail_atendimento import MailMailboxSettings

    pessoa = await make_user(role=UserRole.USER)
    pessoa.sales_teams = [1]
    cena.loja_ml.sales_team = 1
    cena.loja_oliveira_ml.sales_team = 2
    settings = await db.get(MailMailboxSettings, cena.geral.id)
    settings.leitores = [str(pessoa.id)]
    await db.commit()
    auth_as(pessoa)
    antes = (await _pastas(client, cena.geral))["acesso_versao"]
    # A pessoa continua na mesma equipe; quem muda é uma loja da equipe.
    cena.loja_oliveira_ml.sales_team = 1
    await db.commit()
    equipe = (await _pastas(client, cena.geral))["acesso_versao"]
    assert equipe != antes
    settings.visibilidade = "privada"
    await db.commit()
    assert (await _pastas(client, cena.geral))["acesso_versao"] != equipe


async def test_cache_dos_selos_reusa_loja_entre_lotes(db, cena, client, auth_as, monkeypatch):
    monkeypatch.setattr(mail_caixa, "BUSCA_LOTE", 2)
    chamadas = []
    original = mail_caixa.rotulos

    async def medir(session, chaves, cad):
        chamadas.append(chaves)
        return await original(session, chaves, cad)

    monkeypatch.setattr(mail_caixa, "rotulos", medir)
    for n in range(5):
        await entrar(db, cena.geral, assunto="Outro aviso", recebido_em=_min(n))
    auth_as(cena.dono)
    r = await _lista(client, cena.geral, q="inexistente")
    assert r["itens"] == [] and r["proximo"] is None
    assert len(chamadas) == 1
