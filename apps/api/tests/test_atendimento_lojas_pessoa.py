# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""As lojas do /atendimento separadas por PESSOA (30/10/2026).

O Eduardo: "será separado as lojas no atendimento por pessoas então x usuario
vai poder ver x lojas e outros não isso também se aplica pra caixa de e-mail
das lojas delas"; da Garantia, "eles só vão poder ver das lojas deles"; e do
e-mail, "ninguém pode ver a não ser admin né, já no atendimento eles vão ver
só das caixas dele — só o admin vê também todos os atendimentos e todos os
emails".

O que não pode falhar:
  • quem não é admin vê SÓ as lojas dele (`atendimento_lojas_pessoa`): a lista
    e as abas, o /resumo (números, bolinhas e a barra de lojas), o detalhe por
    id (404 da loja de outro), responder (404 antes de qualquer envio), a loja
    do ROBÔ (Temu/AliExpress, sem integração) pelo canal;
  • sem loja nenhuma, nada; admin, tudo;
  • os e-mails das lojas: a barra (por loja, a ficha sem integração da pessoa
    também), a lista de uma loja, a conversa de e-mail de outra loja (404) e o
    filtro do e-mail da Central (`filtro_mensagem`, também no atalho);
  • a aba E-mail é só de admin — a permissão `atendimento_email` não abre mais;
  • a Garantia: só as das lojas da pessoa (pelo pedido do Bling da ficha);
  • a tela de Usuários: GET/PUT /api/users/{id}/lojas-atendimento, só admin.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoLojaPessoa,
    MailMessage,
    StoreInfo,
    UserRole,
)
from app.services.atendimento import acesso, escopo, gravar
from tests.lojas_da_pessoa import dar_lojas
from tests.test_atendimento_email_lojas import _ficha_sem_integracao, _na_pasta, _por_loja
from tests.test_atendimento_separa_email import OUTRO, URL, _chat, _ids, _ids_pela_barra
from tests.test_mail_ponte import AGORA, cena, conversa, entrar, meta_de, rodar

pytestmark = pytest.mark.lojas_por_pessoa  # a regra de verdade, sem o legado do conftest

EMAIL = f"{URL}/email"
EDITOR = "/api/users"


@pytest.fixture
async def vendedor(make_user, monkeypatch):
    """Pessoa ativa, não admin, que responde (como o londres, o israel, o marrocos)."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", OUTRO)
    return await make_user(role=UserRole.USER)


_dar = dar_lojas


async def _robo(db, loja: str = "Barbosa", plataforma: str = "temu") -> AtendimentoCanal:
    canal = AtendimentoCanal(
        plataforma=plataforma,
        canal="chat",
        robo_perfil_id=f"k1{uuid4().hex[:6]}",
        cursor={"robo": {"loja": loja}},
    )
    db.add(canal)
    await db.flush()
    c, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=None,
        plataforma=plataforma,
        canal_nome="chat",
        externo_id=f"robo-{uuid4().hex[:6]}",
        comprador_nome="Comprador",
    )
    await gravar.gravar_mensagem(
        db, c, externo_id=f"{c.externo_id}-1", autor="cliente", texto="oi", enviada_em=AGORA
    )
    await db.commit()
    return canal, c


async def _mundo(db, cena) -> dict:
    """Um chat na Shopee Barbosa, um no ML Oliveira, um na Temu (robô) e um e-mail do ML Barbosa."""
    shopee = await _chat(db, cena.shopee, "shopee", "chat-shopee")
    oliveira = await _chat(db, cena.oliveira_ml, "ml", "chat-oliveira", canal="pos_venda")
    canal_robo, temu = await _robo(db)
    mid = await entrar(db, cena.geral, assunto="Pergunta", corpo="Quando chega?")
    await rodar(db)
    email_ml = await conversa(db, (await meta_de(db, mid)).conversa_id)
    assert email_ml.integration_id == cena.ml.id
    return {
        "shopee": str(shopee.id),
        "oliveira": str(oliveira.id),
        "temu": str(temu.id),
        "canal_robo": canal_robo,
        "email_ml": str(email_ml.id),
        "mid_ml": mid,
    }


def _i(x) -> str:
    return escopo.chave(escopo.INTEGRACAO, x.id)


# ── Lista, resumo, detalhe e responder ──────────────────────────────────────


async def test_vendedor_ve_so_as_lojas_dele(db, cena, client, auth_as, vendedor):
    m = await _mundo(db, cena)
    await _dar(db, vendedor, _i(cena.shopee), escopo.chave(escopo.CANAL, m["canal_robo"].id))
    auth_as(vendedor)

    assert await _ids(client, filtro="todas") == {m["shopee"], m["temu"]}
    # As abas/filtros da Caixa seguem o mesmo escopo.
    assert await _ids(client, filtro="aguardando") <= {m["shopee"], m["temu"]}
    assert await _ids(client, integration_id=str(cena.oliveira_ml.id), filtro="todas") == set()

    r = (await client.get(f"{URL}/resumo")).json()
    lojas = {(lj["integration_id"], lj["canal_id"]) for lj in r["lojas"]}
    assert lojas == {(str(cena.shopee.id), None), (None, str(m["canal_robo"].id))}
    por_plataforma = {p["plataforma"]: p for p in r["plataformas"]}
    assert por_plataforma["ml"]["aguardando"] == 0, "o chat do ML de outro não conta"
    assert por_plataforma["shopee"]["aguardando"] == 1
    assert {c["integration_id"] for c in r["canais"]} <= {str(cena.shopee.id), None}

    # O detalhe por id: o da loja dele abre; o de outra loja, 404.
    assert (await client.get(f"{URL}/conversas/{m['shopee']}")).status_code == 200
    assert (await client.get(f"{URL}/conversas/{m['temu']}")).status_code == 200
    r = await client.get(f"{URL}/conversas/{m['oliveira']}")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    r = await client.get(f"{URL}/conversas/{m['email_ml']}")
    assert r.status_code == 404
    # Responder e as abas de outra loja: 404 antes de qualquer envio.
    r = await client.post(f"{URL}/conversas/{m['oliveira']}/responder", json={"texto": "oi"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    assert (await client.get(f"{URL}/conversas/{m['oliveira']}/abas")).status_code == 404


async def test_sem_loja_nao_ve_nada_e_admin_ve_tudo(db, cena, client, auth_as, vendedor):
    m = await _mundo(db, cena)
    auth_as(vendedor)
    assert await _ids(client, filtro="todas") == set()
    r = (await client.get(f"{URL}/resumo")).json()
    assert r["lojas"] == [] and all(p["aguardando"] == 0 for p in r["plataformas"])
    for cid in (m["shopee"], m["temu"], m["oliveira"]):
        assert (await client.get(f"{URL}/conversas/{cid}")).status_code == 404

    auth_as(cena.dono)
    assert {m["shopee"], m["temu"], m["oliveira"]} <= await _ids(client, filtro="todas")
    assert (await client.get(f"{URL}/conversas/{m['oliveira']}")).status_code == 200


async def test_a_equipe_de_vendas_nao_vale_mais_no_atendimento(db, cena, client, auth_as, vendedor):
    """A divisão 101/102/103 (sales_teams) não é a da planilha: no atendimento só
    valem as lojas da pessoa."""
    m = await _mundo(db, cena)
    db.add(StoreInfo(user_id=cena.dono.id, platform="ml", account_name="oliveira-ml", sales_team=7))
    vendedor.sales_teams = [7]
    await db.commit()
    auth_as(vendedor)
    assert await _ids(client, filtro="todas") == set()
    await _dar(db, vendedor, _i(cena.shopee))
    assert await _ids(client, filtro="todas") == {m["shopee"]}


# ── O escopo montado ─────────────────────────────────────────────────────────


async def test_o_escopo_completa_pela_ficha_e_traz_o_pedido_do_bling(db, cena, vendedor):
    cena.loja_ml.bling_store_id = "B-ML"
    await db.commit()
    # Só a FICHA da loja (como a linha "sem chat" da planilha): a integração vem pelo FK.
    await _dar(db, vendedor, escopo.chave(escopo.FICHA, cena.loja_ml.id))
    s = await escopo.escopo_do_atendimento(db, vendedor)
    assert not s.unrestricted
    assert s.integration_ids == {cena.ml.id}
    assert cena.loja_ml.id in s.store_info_ids
    assert s.bling_store_ids == {"B-ML"}
    assert s.canal_ids == set()
    # Admin: tudo, sem linha.
    assert (await escopo.escopo_do_atendimento(db, cena.dono)).unrestricted


# ── E-mails das lojas ────────────────────────────────────────────────────────


async def test_emails_das_lojas_so_da_pessoa(db, cena, client, auth_as, vendedor):
    m = await _mundo(db, cena)
    await _na_pasta(db, cena, m["email_ml"], "problemas")
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena)
    await _na_pasta(db, cena, ficha_cid, "envio")
    await _dar(db, vendedor, _i(cena.shopee), escopo.chave(escopo.FICHA, ficha))
    auth_as(vendedor)

    lojas = await _por_loja(client)
    assert set(lojas) == {str(cena.shopee.id), ficha}, (
        "só as lojas dele, a ficha sem integração também"
    )
    # A lista da ficha dele abre; a do ML Barbosa (de outro) vem vazia.
    assert await _ids_pela_barra(client, store_info_id=ficha, filtro="todas") == {ficha_cid}
    assert await _ids_pela_barra(client, integration_id=str(cena.ml.id), filtro="todas") == set()
    assert (await client.get(f"{URL}/conversas/{ficha_cid}")).status_code == 200
    assert (await client.get(f"{URL}/conversas/{m['email_ml']}")).status_code == 404
    assert (await client.get(f"{EMAIL}/conversas/{m['email_ml']}/emails")).status_code == 404

    # Quem tem o ML Barbosa vê o e-mail dele.
    await _dar(db, vendedor, _i(cena.ml), escopo.chave(escopo.FICHA, cena.loja_ml.id))
    assert m["email_ml"] in await _ids_pela_barra(
        client, integration_id=str(cena.ml.id), filtro="todas"
    )
    assert (await client.get(f"{URL}/conversas/{m['email_ml']}")).status_code == 200


async def test_a_aba_email_e_so_de_admin(db, cena, client, auth_as, vendedor):
    vendedor.permissions = {"atendimento_email": {"view": True, "edit": True}}
    await db.commit()
    assert not acesso.ve_aba_email(vendedor)
    assert not acesso.responde_email(vendedor)
    assert acesso.ve_aba_email(SimpleNamespace(role=UserRole.ADMIN))
    auth_as(vendedor)
    for rota in ("saude", "filas", "informativos", "envios", "pastas", "regras"):
        r = await client.get(f"{EMAIL}/{rota}")
        assert r.status_code == 403, (rota, r.text)
    r = await client.get(f"{URL}/conversas", params={"caixa": "email"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "aba_email_so_admin"


# ── Garantia ─────────────────────────────────────────────────────────────────


async def test_garantia_so_das_lojas_da_pessoa(db, client, make_user, auth_as):
    from tests.test_garantias_router import CADASTRA, CPF_2, REGISTRA, _admin, _cadastrar, _pedido

    dono = await _admin(make_user, auth_as)
    l1 = StoreInfo(user_id=dono.id, platform="shopee", account_name="um", bling_store_id="L1")
    l2 = StoreInfo(user_id=dono.id, platform="shopee", account_name="dois", bling_store_id="L2")
    db.add_all([l1, l2])
    await db.commit()
    await _pedido(db)
    await _pedido(db, numero="300002", numeroloja="251007ZZZZ0002", loja="L2", cpf=CPF_2)
    g1 = await _cadastrar(client)
    g2 = await _cadastrar(client, pedido="300002", cpf=CPF_2, nome="Maria Souza")

    pessoa = await make_user(permissions={**CADASTRA, **REGISTRA})
    auth_as(pessoa)
    assert (await client.post("/api/garantias/lista", json={})).json()["itens"] == []
    await _dar(db, pessoa, escopo.chave(escopo.FICHA, l2.id))
    lista = (await client.post("/api/garantias/lista", json={})).json()
    assert [i["id"] for i in lista["itens"]] == [g2["id"]]
    assert (await client.get(f"/api/garantias/{g1['id']}")).status_code == 404
    assert (await client.get(f"/api/garantias/{g2['id']}")).status_code == 200


# ── A tela de Usuários ───────────────────────────────────────────────────────


async def test_editor_so_admin_e_grava_a_loja_inteira(
    db, cena, client, auth_as, vendedor, make_user
):
    m = await _mundo(db, cena)
    auth_as(vendedor)
    assert (await client.get(f"{EDITOR}/{vendedor.id}/lojas-atendimento")).status_code == 403
    r = await client.put(f"{EDITOR}/{vendedor.id}/lojas-atendimento", json={"chaves": []})
    assert r.status_code == 403

    auth_as(cena.dono)
    d = (await client.get(f"{EDITOR}/{vendedor.id}/lojas-atendimento")).json()
    assert d["chaves"] == [] and d["admin"] is False
    por_chave = {lj["chave"]: lj for lj in d["lojas"]}
    # A ficha com a integração ligada é UMA loja, com as duas chaves.
    ml = por_chave[escopo.chave(escopo.FICHA, cena.loja_ml.id)]
    assert ml["chaves"] == [escopo.chave(escopo.FICHA, cena.loja_ml.id), _i(cena.ml)]
    assert ml["chat"] is True and ml["plataforma"] == "ml"
    # A ficha sem integração (JLAS2): sem chat. O robô sem ficha: linha própria.
    sem_chat = [lj for lj in d["lojas"] if lj["nome"] == "JLAS2"]
    assert len(sem_chat) == 1 and sem_chat[0]["chat"] is False
    robo = por_chave[escopo.chave(escopo.CANAL, m["canal_robo"].id)]
    assert robo["plataforma"] == "temu" and robo["chat"] is True

    # Formato inválido e loja que não existe: 422.
    r = await client.put(f"{EDITOR}/{vendedor.id}/lojas-atendimento", json={"chaves": ["x:1"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "chave_invalida"
    r = await client.put(
        f"{EDITOR}/{vendedor.id}/lojas-atendimento", json={"chaves": [f"i:{uuid4()}"]}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "loja_desconhecida"
    r = await client.put(f"{EDITOR}/{uuid4()}/lojas-atendimento", json={"chaves": []})
    assert r.status_code == 404

    r = await client.put(
        f"{EDITOR}/{vendedor.id}/lojas-atendimento", json={"chaves": ml["chaves"] + robo["chaves"]}
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["chaves"]) == sorted(ml["chaves"] + robo["chaves"])
    # Quem mais tem a loja aparece para a outra pessoa.
    outra = await make_user(role=UserRole.USER)
    d2 = (await client.get(f"{EDITOR}/{outra.id}/lojas-atendimento")).json()
    assert d2["outros"][_i(cena.ml)] == [vendedor.name or vendedor.email]

    # E vale na hora: o vendedor vê o chat da Temu e não o da Shopee.
    auth_as(vendedor)
    assert await _ids(client, filtro="todas") == {m["temu"]}
    # Tirar tudo: não vê mais nada.
    auth_as(cena.dono)
    assert (
        await client.put(f"{EDITOR}/{vendedor.id}/lojas-atendimento", json={"chaves": []})
    ).json()["chaves"] == []
    auth_as(vendedor)
    assert await _ids(client, filtro="todas") == set()


# ── A migration 0440 ─────────────────────────────────────────────────────────


def test_migration_0440_cria_a_tabela_do_modelo():
    import importlib.util
    from pathlib import Path

    from app.models.atendimento_lojas_pessoa import PADRAO_CHAVE_LOJA

    caminho = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0440_atendimento_lojas_pessoa.py"
    )
    spec = importlib.util.spec_from_file_location("m0440", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0440_atendimento_lojas_pessoa"
    assert mod.down_revision == "0439_garantia_pedido"
    assert mod.PADRAO_CHAVE == PADRAO_CHAVE_LOJA
    assert escopo.chave_valida(escopo.chave(escopo.FICHA, uuid4()))
    assert not escopo.chave_valida("x:1") and not escopo.chave_valida(f"i:{uuid4()}".upper())
