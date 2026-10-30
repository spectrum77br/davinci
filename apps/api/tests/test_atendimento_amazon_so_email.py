# ruff: noqa: F401, F811 — fixtures compartilhadas da Amazon e do envio
"""A conta Amazon SÓ DE E-MAIL (30/10/2026) e a Netshoes pela pasta do Tuta.

O Eduardo: "quero igual como é nas outras amazon" — a Amazon VR não tem a API
da loja (sem integração), mas o Seller Central dela já manda as mensagens ao
Gmail do atendimento com o "+vr". O canal "amazon:vr" (sem integração) é a loja:

  • o e-mail com "+vr" entra no canal dela (nunca em "conta não identificada"),
    e o seguinte da mesma thread também;
  • a tag que casa com uma integração Amazon continua indo para a integração;
  • responder sai pelo mesmo Gmail (o adaptador), e o canal sem a tag certa
    continua recusado como "sem conta";
  • o cursor da caixa não apaga o nome do canal;
  • só o vendedor com a chave "c:" do canal vê a conversa;
  • a Netshoes é plataforma de pasta (a regra "netshoes").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import AtendimentoCanal, AtendimentoConversa
from app.services.atendimento import amazon_email, canais_externos, enviar, escopo
from app.services.mail_atendimento import regras
from app.services.mail_atendimento.constantes import PLATAFORMAS_PASTA
from tests.lojas_da_pessoa import dar_lojas
from tests.test_atendimento_amazon_email import (
    PEDIDO_KFA,
    RELAY_JOAO,
    RELAY_MARIA,
    CaixaFalsa,
    _conta,
    _conversas,
    _email,
    _instalar_caixa,
    _mensagens,
    caixa_configurada,
    redis_falso,
)
from tests.test_atendimento_enviar import amazon, ligado
from tests.test_mail_ponte import cena

pytestmark = pytest.mark.lojas_por_pessoa


async def _canal_vr(db) -> AtendimentoCanal:
    canal = AtendimentoCanal(
        plataforma="amazon",
        canal="email",
        externo_ref=canais_externos.ref_da_amazon("VR"),
        modo="humano",
        status="ok",
        cursor={canais_externos.CHAVE_CURSOR: {"nome": "VR"}},
    )
    db.add(canal)
    await db.commit()
    return canal


def test_referencia_da_amazon_so_de_email():
    assert canais_externos.ref_da_amazon(" VR ") == "amazon:vr"
    assert canais_externos.partes("amazon:vr") == ("amazon", "amazon", "vr")
    assert canais_externos.partes("amazon:") is None
    with pytest.raises(canais_externos.ReferenciaInvalida):
        canais_externos.ref_da_amazon("v r")
    canal = AtendimentoCanal(plataforma="amazon", canal="email", externo_ref="amazon:vr")
    assert canais_externos.tag_da_amazon(canal) == "vr"
    assert canais_externos.nome_do_canal(canal) == "VR"
    assert "sem a API" in canais_externos.descricao_da_origem(canal)
    assert canais_externos.modo_permitido("amazon", "humano")
    assert not canais_externos.modo_permitido("amazon", "auto")


async def test_email_com_a_tag_entra_no_canal_so_de_email(
    db, make_user, caixa_configurada, redis_falso, monkeypatch
):
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "KFA")
    vr = await _canal_vr(db)
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                10: _email(),  # +kfa: a integração de sempre
                11: _email(
                    de=f'"João Souza - Amazon Marketplace" <{RELAY_JOAO}>',
                    para="atendimento+vr@loja-teste.com.br",
                    assunto="Pergunta sobre o pedido 704-4444444-4444444",
                    message_id="<vr1@amazon.com.br>",
                    texto="Tem nota fiscal?",
                ),
            }
        ),
    )
    r = await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()
    assert (r.status, r.conversas_novas) == ("ok", 2)
    conversas = {c.comprador_id: c for c in await _conversas(db)}
    maria, joao = conversas[RELAY_MARIA], conversas[RELAY_JOAO]
    assert maria.integration_id == kfa.id and maria.canal_id == canal_kfa.id
    assert joao.integration_id is None and joao.canal_id == vr.id
    assert joao.conta == "VR" and joao.aguardando_resposta is True
    [m] = await _mensagens(db, joao.id)
    assert m.payload["conta_por"] == "tag"
    # O cursor andou em todos os canais Amazon, sem apagar o nome do canal da VR.
    await db.refresh(vr)
    assert vr.cursor.get("ultimo_uid") == 11
    assert vr.cursor[canais_externos.CHAVE_CURSOR] == {"nome": "VR"}

    # O e-mail seguinte da thread, SEM pedido e sem "+": a mesma conversa da VR.
    caixa.emails[12] = _email(
        de=f'"João Souza - Amazon Marketplace" <{RELAY_JOAO}>',
        para="atendimento@loja-teste.com.br",
        assunto="Re: Pergunta",
        message_id="<vr2@amazon.com.br>",
        em_resposta_a="<vr1@amazon.com.br>",
        texto="Obrigado!",
        marcadores=False,
    )
    redis_falso.proxima_rodada()
    await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()
    assert len(await _mensagens(db, joao.id)) == 2
    assert len(await _conversas(db)) == 2


async def test_a_tag_de_uma_integracao_continua_na_integracao(
    db, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """Um canal só de e-mail com a MESMA tag de uma integração Amazon não rouba o e-mail."""
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "KFA")
    db.add(AtendimentoCanal(plataforma="amazon", canal="email", externo_ref="amazon:kfa"))
    await db.commit()
    _instalar_caixa(monkeypatch, CaixaFalsa({10: _email()}))
    await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()
    [maria] = await _conversas(db)
    assert maria.integration_id == kfa.id and maria.canal_id == canal_kfa.id


async def test_responder_na_amazon_so_de_email_sai_pelo_gmail(db, make_user, ligado, amazon):
    from app.services.atendimento import gravar

    user = await make_user()
    vr = await _canal_vr(db)
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=vr,
        integration=None,
        plataforma="amazon",
        canal_nome="email",
        externo_id=f"{RELAY_JOAO}|-",
        comprador_id=RELAY_JOAO,
        comprador_nome="João",
        dados={"assunto": "Pergunta"},
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="<p1@amazon.com.br>",
        autor="cliente",
        texto="Tem nota?",
        enviada_em=datetime.now(UTC) - timedelta(minutes=30),
    )
    await db.commit()
    m = await enviar.enviar_resposta(db, conversa, "Tem sim.", user=user)
    assert amazon.chamadas == [(RELAY_JOAO, "Tem sim.")]
    assert m.status == "enviada"

    # Sem conta (sem canal nenhum): continua recusado como antes.
    sem, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=None,
        plataforma="amazon",
        canal_nome="email",
        externo_id=f"{RELAY_MARIA}|{PEDIDO_KFA}",
        comprador_id=RELAY_MARIA,
    )
    await gravar.gravar_mensagem(
        db,
        sem,
        externo_id="<p2@amazon.com.br>",
        autor="cliente",
        texto="Oi",
        enviada_em=datetime.now(UTC) - timedelta(minutes=30),
    )
    await db.commit()
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_resposta(db, sem, "Oi", user=user)
    assert e.value.code == enviar.RECUSA_SEM_INTEGRACAO


async def test_so_quem_tem_o_canal_ve_a_conversa(db, make_user, client, auth_as):
    from app.services.atendimento import gravar

    vr = await _canal_vr(db)
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=vr,
        integration=None,
        plataforma="amazon",
        canal_nome="email",
        externo_id=f"{RELAY_JOAO}|-",
        comprador_id=RELAY_JOAO,
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="<p1@amazon.com.br>",
        autor="cliente",
        texto="Tem nota?",
        enviada_em=datetime.now(UTC) - timedelta(minutes=30),
    )
    await db.commit()
    israel, londres = await make_user(), await make_user()
    await dar_lojas(db, israel, escopo.chave(escopo.CANAL, vr.id))
    await dar_lojas(db, londres, escopo.chave(escopo.FICHA, vr.id))  # outra chave qualquer
    auth_as(israel)
    assert (await client.get(f"/api/atendimento/conversas/{conversa.id}")).status_code == 200
    r = (await client.get("/api/atendimento/resumo")).json()
    assert [(lj["canal_id"], lj["conta"]) for lj in r["lojas"]] == [(str(vr.id), "VR")]
    auth_as(londres)
    assert (await client.get(f"/api/atendimento/conversas/{conversa.id}")).status_code == 404


async def test_o_editor_junta_o_canal_da_amazon_com_a_ficha(db, make_user):
    from app.models import StoreInfo

    dono = await make_user()
    ficha = StoreInfo(user_id=dono.id, platform="amazon", account_name="VR ")
    db.add(ficha)
    vr = await _canal_vr(db)
    lojas = {lj["chave"]: lj for lj in await escopo.lojas_para_vincular(db)}
    linha = lojas[escopo.chave(escopo.FICHA, ficha.id)]
    assert linha["chaves"] == [
        escopo.chave(escopo.FICHA, ficha.id),
        escopo.chave(escopo.CANAL, vr.id),
    ]
    assert linha["chat"] is True and linha["nome_no_chat"] == "VR"


def test_netshoes_e_plataforma_de_pasta():
    from app.services.mail_atendimento import suspeito

    assert "netshoes" in PLATAFORMAS_PASTA
    # Sem pasta própria: o remetente oficial decide a loja Netshoes do alias.
    assert suspeito.plataforma_do_dominio("netshoes.com.br") == "netshoes"
    assert suspeito.plataforma_do_dominio("marketplace.netshoes.com.br") == "netshoes"
    assert suspeito.plataforma_do_dominio("netshoes-promo.com") is None
    # A regra da pasta entra pelo SQL do deploy (a semente é a da migration 0387).
    assert regras.TIPO_PLATAFORMA == "plataforma"


async def test_a_lista_aceita_a_netshoes(client, make_user, auth_as):
    """O menu Plataforma mostra a Netshoes (só e-mail): a lista do chat vem vazia, sem 422."""
    from app.models import UserRole

    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get("/api/atendimento/conversas", params={"plataforma": "netshoes"})
    assert r.status_code == 200, r.text
    assert r.json()["itens"] == []


async def test_email_da_netshoes_na_entrada_cai_na_caixa_problemas_da_loja(
    db, cena, client, auth_as
):
    """O e-mail da Netshoes chega no endereço que a Poofy usa em várias plataformas,
    na Entrada (sem pasta nova no Tuta): o remetente decide a loja Netshoes e a
    barra mostra o e-mail na caixa Problemas dela (30/10/2026)."""
    from app.models import StoreInfo
    from tests.test_atendimento_email_lojas import _pastas_ligadas, _por_loja
    from tests.test_mail_ponte import entrar, meta_de, rodar

    netshoes = StoreInfo(
        user_id=cena.dono.id, platform="netshoes", account_name="Oliveira", email="21max"
    )
    db.add(netshoes)
    await db.commit()
    await _pastas_ligadas(db, cena)
    mid = await entrar(
        db, cena.geral, pasta="INBOX", para=["21max@tuta.com"], de="sac@netshoes.com.br",
        assunto="Novo protocolo de atendimento",
    )  # fmt: skip
    await rodar(db)
    meta = await meta_de(db, mid)
    assert (meta.store_info_id, meta.plataforma) == (netshoes.id, "netshoes")
    auth_as(cena.dono)
    lojas = await _por_loja(client)
    assert lojas[str(netshoes.id)]["grupos"]["problemas"]["emails"] == 1
    r = await client.get(
        "/api/atendimento/email/lojas/emails",
        params={"grupo": "problemas", "store_info_id": str(netshoes.id)},
    )
    assert r.status_code == 200 and [i["id"] for i in r.json()["itens"]] == [str(mid)]
    r = await client.get(
        "/api/atendimento/email/lojas/emails",
        params={"grupo": "envios", "store_info_id": str(netshoes.id)},
    )
    assert r.json()["itens"] == []


async def test_zap_do_numero_so_para_quem_tem_o_numero(db, make_user, client, auth_as):
    """30/10/2026: o vendedor vê e responde o Zap dos números das lojas dele; o outro
    vendedor, não (404); o admin vê todos. A tela dos números continua só do admin."""
    from app.services.atendimento import gravar

    canal = AtendimentoCanal(
        plataforma="whatsapp",
        canal="zap",
        externo_ref="zap:123456",
        modo="humano",
        status="ok",
        cursor={canais_externos.CHAVE_CURSOR: {"nome": "Mini"}},
    )
    db.add(canal)
    await db.flush()
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=None,
        plataforma="whatsapp",
        canal_nome="zap",
        externo_id="5511999990000",
        comprador_id="5511999990000",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="wamid.1",
        autor="cliente",
        texto="oi",
        enviada_em=datetime.now(UTC) - timedelta(minutes=5),
    )
    await db.commit()
    londres, israel = await make_user(), await make_user()
    await dar_lojas(db, londres, escopo.chave(escopo.CANAL, canal.id))
    await dar_lojas(db, israel, escopo.chave(escopo.FICHA, canal.id))

    auth_as(londres)
    assert (await client.get(f"/api/atendimento/conversas/{conversa.id}")).status_code == 200
    r = (await client.get("/api/atendimento/resumo")).json()
    assert [lj["conta"] for lj in r["lojas"]] == ["Mini"]
    assert await enviar._responde_o_zap(db, londres, conversa) is True
    assert (await client.get("/api/atendimento/zap/numeros")).status_code == 404

    auth_as(israel)
    assert (await client.get(f"/api/atendimento/conversas/{conversa.id}")).status_code == 404
    assert await enviar._responde_o_zap(db, israel, conversa) is False

    lojas = {lj["chave"]: lj for lj in await escopo.lojas_para_vincular(db)}
    assert lojas[escopo.chave(escopo.CANAL, canal.id)]["plataforma"] == "whatsapp"
