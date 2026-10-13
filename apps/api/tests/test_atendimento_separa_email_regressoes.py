"""A conversa de E-MAIL da ponte nunca é "a conversa do pedido" (revisão, 13/10/2026).

Desde 13/10/2026 (`services/atendimento/separa_email.py`) o aviso por e-mail
das plataformas com API cai na conversa de E-MAIL da loja (canal `email` +
`dados.fonte = 'tuta'`), ligada ao pedido e com a mensagem MAIS NOVA que a do
chat. A sonda da revisão provou que ela virava a primeira de
`etiqueta_fatos.conversas_do_pedido` — e quem pega a primeira é a lista Ag.
cancelamento (o `conversa_id` e o retrato `dados.pedido_mkt`, que separa o
IN_CANCEL/CANCELLED da falta de estoque), a troca, a oferta de troca (que SAI
pela conversa) e a devolução (o `conversa_id` da linha).

O que estes testes seguram (Postgres local de verdade; o aviso entra pela
PONTE de verdade, `tests/test_mail_ponte.cena`, quando o cenário é do ML):
  • chat com mensagem + aviso pela ponte → o chat é a principal; a de e-mail
    vai por último (`conversas_do_pedido`) e a principal/troca nem a escolhem
    (`conversa_do_pedido`, `conversas_do_pedido_bling`);
  • a lista Ag. cancelamento aponta o chat e classifica pelo retrato dele;
  • a troca escolhida pela tela da conversa de e-mail classifica pelo chat
    (o IN_CANCEL do comprador continua recusando a troca);
  • a oferta nunca sai pela conversa de e-mail (painel e rota); o pedido SÓ
    com a conversa de e-mail é o pedido sem conversa (sem oferta pelo DaVinci);
  • a devolução liga o chat — e só com a conversa de e-mail, nenhuma;
  • o alerta do Telegram, o cartão Cliente do chat e a thread da Amazon pelo
    Gmail não pegam a conversa de e-mail.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoMensagem, BlingOrder, NfFaturamento
from app.services.atendimento import (
    amazon_email,
    etiqueta_fatos,
    gravar,
    painel,
    separa_email,
    troca,
    troca_oferta,
    troca_sugestoes,
    validador,
)
from app.services.atendimento import cliente as cliente_svc
from app.services.atendimento import sync as sync_svc
from tests import test_atendimento_reclamacoes_devolucoes as dev
from tests import test_atendimento_troca_oferta as of
from tests.test_mail_ponte import (
    AGORA,
    conversa,
    entrar,
    meta_de,
    pedido_existe,
    rodar,
)
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte

URL_LISTA = "/api/atendimento/ag-cancelamento"
PED = "2000012345678901"
PED_SO_EMAIL = "2000012345678902"
NUMERO_ML = "300201"
NUMERO_ML_SO_EMAIL = "300202"
LOJA_ML = "7101"


# ─────────────── as chaves e os falsos ───────────────


@pytest.fixture
def troca_ligada(monkeypatch):
    """A troca e a oferta ligadas (as chaves de `test_atendimento_troca_oferta`),
    o Bling e a Shopee do pedido proibidos, o validador e o chat da Shopee
    falsos: nada sai daqui. Devolve o chat falso (os textos que "saíram")."""
    s = get_settings()
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_simulador",
        "atendimento_troca_sugestoes_ativa",
        "atendimento_troca_lote_auto",
        "nf_auto_enfileirar",
        "nf_auto_ml_amazon",
        "estoque_familia_ativo",
        "estoque_familia_redireciona",
        "prioridade_substitui_item",
    ):
        monkeypatch.setattr(s, nome, False)
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_troca_ativa", True)
    monkeypatch.setattr(s, "atendimento_troca_pedidos", "")
    monkeypatch.setattr(s, "atendimento_troca_folga_prazo_horas", 2.0)
    monkeypatch.setattr(s, "atendimento_troca_aceite_max_dias", 7)
    monkeypatch.setattr(s, "atendimento_troca_teto_custo_pct", 5.0)
    monkeypatch.setattr(s, "atendimento_troca_piso_nivel2_pct", -10.0)
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "estoque_familia_prefixos", "")
    monkeypatch.setattr(troca, "ESPERAS_CONFERENCIA_S", (0.0, 0.0, 0.0))

    async def _proibido(*args, **kwargs):
        raise AssertionError("nada aqui fala com o Bling nem com o pedido na Shopee")

    async def _nada(*args, **kwargs):
        return None

    monkeypatch.setattr(troca, "_cliente_bling", _proibido)
    monkeypatch.setattr(troca, "_cliente_shopee", _proibido)
    monkeypatch.setattr(painel, "_cliente_bling", _nada)
    monkeypatch.setattr(painel, "invalidar_observacoes", _nada)

    def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
        return " ".join((texto or "").split())

    def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
        return [] if texto.strip() else ["Resposta vazia."]

    monkeypatch.setattr(validador, "normalizar", normalizar)
    monkeypatch.setattr(validador, "validar", validar)
    troca_sugestoes.limpar_memoria()
    yield of._Chat(monkeypatch)
    troca_sugestoes.limpar_memoria()


# ─────────────── fábrica ───────────────


async def _chat_ml(db, cena, *, externo: str, pedido: str, status: str | None = None):
    """O chat do pós-venda do ML do pedido, com uma fala do comprador de 3 h atrás."""
    chat, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=cena.ml,
        plataforma="ml",
        canal_nome="pos_venda",
        externo_id=externo,
        comprador_id="c1",
        comprador_nome="Comprador",
        pedido_marketplace=pedido,
    )
    if status:
        chat.dados = {**(chat.dados or {}), "pedido_mkt": {"pedido": pedido, "status": status}}
    await gravar.gravar_mensagem(
        db,
        chat,
        externo_id=f"{externo}-m1",
        autor="cliente",
        texto="quando chega?",
        enviada_em=AGORA - timedelta(hours=3),
    )
    await db.commit()
    return chat


async def _aviso_pela_ponte(db, cena, pedido: str) -> AtendimentoConversa:
    """O aviso do ML por e-mail ("Nova mensagem no pedido N") pela PONTE de verdade."""
    await pedido_existe(db, cena.ml, pedido)
    mid = await entrar(
        db,
        cena.geral,
        pasta="mensagens ml",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto=f"Nova mensagem no pedido {pedido}",
        corpo="O comprador mandou uma mensagem.",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.vinculado_por) == ("gravado", "pedido")
    email = await conversa(db, m.conversa_id)
    assert separa_email.e_conversa_de_email(email) and email.pedido_marketplace == pedido
    return email


async def _ml_em_83955(db, cena, numero: str, numeroloja: str) -> None:
    """O pedido do ML em 83955 pela falta de estoque do dg053.sp (a marca viva da NF)."""
    cena.loja_ml.bling_store_id = LOJA_ML
    db.add(
        BlingOrder(
            bling_id=int(numero),
            item_index=0,
            numero=numero,
            numeroloja=numeroloja,
            situacao="83955",
            loja=LOJA_ML,
            item_codigo="dg053.sp",
            item_descricao="Produto dg053.sp",
            item_quantidade=1,
            data=AGORA - timedelta(days=1),
            marketplace_ship_deadline=AGORA + timedelta(hours=20),
        )
    )
    quando = AGORA - timedelta(hours=4)
    db.add(
        NfFaturamento(
            pedido_bling=numero,
            status_faturamento="sem_estoque",
            erro_faturamento="Aguardando Cancelamento — saldo negativo: dg053.sp",
            created_at=quando,
            updated_at=quando,
        )
    )
    await db.commit()


async def _email_shopee(db, c, *, externo: str = "mail-fio:aviso-1") -> AtendimentoConversa:
    """A conversa de e-mail que a ponte cria para o aviso da Shopee (canal email +
    `dados.fonte = 'tuta'`), com o pedido e a mensagem mais nova que o chat."""
    email, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=c.integ,
        plataforma="shopee",
        canal_nome="email",
        externo_id=externo,
        comprador_id="noreply@shopee.com.br",
        comprador_nome="Shopee",
        pedido_marketplace=of.NUMEROLOJA,
        dados={"fonte": "tuta", "mail": {"alias": "kfa@tuta.com"}},
    )
    await gravar.gravar_mensagem(
        db,
        email,
        externo_id=f"mail:{externo}",
        autor="sistema",
        texto="E-mail da plataforma: o pedido mudou",
        enviada_em=of.AGORA - timedelta(minutes=1),
        payload={"mail": {"message_id": externo}},
    )
    await db.commit()
    assert separa_email.e_conversa_de_email(email)
    return email


async def _da_loja(db, conversa_id) -> list[AtendimentoMensagem]:
    """O que a LOJA mandou na conversa (resposta ou e-mail na fila da Central)."""
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem).where(
                    AtendimentoMensagem.conversa_id == conversa_id,
                    AtendimentoMensagem.autor == "loja",
                )
            )
        ).scalars()
    )


# ─────────────── a principal do pedido (a sonda da revisão) ───────────────


async def test_principal_do_pedido_e_o_chat_depois_do_aviso_pela_ponte(db, cena):
    """A sonda `test_sonda_principal_do_pedido_depois_do_aviso`: o chat do
    pedido tem mensagem e chega o AVISO por e-mail (mais novo). O chat é a
    principal; a de e-mail vai por último; só com ela, o pedido não tem
    conversa principal."""
    chat = await _chat_ml(db, cena, externo="pack-1", pedido=PED)
    email = await _aviso_pela_ponte(db, cena, PED)
    assert gravar._utc(email.ultima_mensagem_em) > gravar._utc(chat.ultima_mensagem_em)

    convs = await etiqueta_fatos.conversas_do_pedido(db, "ml", PED)
    assert [c.id for c in convs] == [chat.id, email.id]
    assert (await etiqueta_fatos.conversa_do_pedido(db, "ml", PED)).id == chat.id
    # O nome do cadastro ("mercadolivre") e sem plataforma: a mesma ordem.
    assert [c.id for c in await etiqueta_fatos.conversas_do_pedido(db, None, PED)] == [
        chat.id,
        email.id,
    ]

    # O pedido SÓ com o aviso: a de e-mail continua achada pelo pedido (quem
    # recalcula a etiqueta, por exemplo), mas não é a principal de ninguém.
    so_email = await _aviso_pela_ponte(db, cena, PED_SO_EMAIL)
    convs = await etiqueta_fatos.conversas_do_pedido(db, "ml", PED_SO_EMAIL)
    assert [c.id for c in convs] == [so_email.id]
    assert await etiqueta_fatos.conversa_do_pedido(db, "ml", PED_SO_EMAIL) is None


async def test_lista_ag_cancelamento_e_troca_pelo_chat_do_pedido(
    db, cena, client, auth_as, troca_ligada
):
    """A lista Ag. cancelamento aponta o CHAT e classifica pelo retrato dele (o
    `cancelled` do ML), não pela conversa de e-mail mais nova — que daria
    "falta de estoque" e sugeriria troca de um pedido já cancelado. A troca
    (pelo nº do Bling) também. O pedido só com o aviso: sem conversa."""
    await _ml_em_83955(db, cena, NUMERO_ML, PED)
    await _ml_em_83955(db, cena, NUMERO_ML_SO_EMAIL, PED_SO_EMAIL)
    chat = await _chat_ml(db, cena, externo="pack-1", pedido=PED, status="cancelled")
    email = await _aviso_pela_ponte(db, cena, PED)
    so_email = await _aviso_pela_ponte(db, cena, PED_SO_EMAIL)

    # Pelo nº do Bling (a lista, a troca, a oferta): só as da Caixa.
    assert [c.id for c in await etiqueta_fatos.conversas_do_pedido_bling(db, NUMERO_ML)] == [
        chat.id
    ]
    todas = await etiqueta_fatos.conversas_do_pedido_bling(db, NUMERO_ML, com_email=True)
    assert [c.id for c in todas] == [chat.id, email.id]
    assert await etiqueta_fatos.conversas_do_pedido_bling(db, NUMERO_ML_SO_EMAIL) == []

    auth_as(cena.dono)
    r = await client.get(URL_LISTA)
    assert r.status_code == 200, r.text
    por = {p["numero"]: p for p in r.json()["pedidos"]}
    assert por[NUMERO_ML]["conversa_id"] == str(chat.id)
    assert por[NUMERO_ML]["motivo"]["codigo"] == "cancelado_plataforma"
    assert por[NUMERO_ML]["motivo"]["pode_sugerir_troca"] is False
    # Só com a conversa de e-mail: o pedido sem conversa (a classificação de antes).
    assert por[NUMERO_ML_SO_EMAIL]["conversa_id"] is None
    assert por[NUMERO_ML_SO_EMAIL]["motivo"]["codigo"] == "sem_estoque"

    # A troca: a principal é o chat; escolhida a de e-mail (o painel dela), vale o chat.
    for escolhida in (None, chat.id, email.id):
        p = await troca._carregar(db, NUMERO_ML, escolhida)
        assert p.conversa is not None and p.conversa.id == chat.id, escolhida
        assert p.motivo.codigo == "cancelado_plataforma"
    p = await troca._carregar(db, NUMERO_ML_SO_EMAIL, so_email.id)
    assert p.conversa is None and p.motivo.codigo == "sem_estoque"
    with pytest.raises(troca.TrocaRecusada) as e:
        await troca._carregar(db, NUMERO_ML, so_email.id)  # a de e-mail de OUTRO pedido
    assert e.value.code == "conversa_de_outro_pedido"


# ─────────────── a troca e a oferta (Shopee) ───────────────


async def test_troca_pela_tela_do_email_classifica_pelo_chat(
    db, make_user, monkeypatch, troca_ligada
):
    """O comprador pediu o cancelamento (IN_CANCEL no retrato do chat): pela
    tela da conversa de e-mail (o `conversa_id` dela), a troca continua
    recusada — sem o retrato, a de e-mail daria "falta de estoque" e a troca
    seguiria."""
    c = await of._cenario(db, make_user, monkeypatch)
    c.conversa.dados = {**(c.conversa.dados or {}), "pedido_mkt": {"status": "IN_CANCEL"}}
    await db.commit()
    email = await _email_shopee(db, c)

    pv = await troca.previa(
        db,
        c.adm,
        numero_bling=of.NUMERO,
        sku_antigo=c.antigo,
        sku_novo=c.novo,
        conversa_id=email.id,
    )
    assert pv["pode"] is False and pv["conversa_id"] == str(c.conversa.id)
    falha = next(t for t in pv["travas"] if t["ok"] is False)
    assert falha["code"] == "motivo_nao_permite"
    p = await troca._carregar(db, of.NUMERO, email.id)
    assert (p.conversa.id, p.motivo.codigo) == (c.conversa.id, "pedido_cliente")


async def test_oferta_nunca_sai_pela_conversa_de_email(
    db, client, auth_as, make_user, monkeypatch, troca_ligada
):
    """O chat e a conversa de e-mail (mais nova) do mesmo pedido em falta de
    estoque: o botão do painel do chat pode; o do painel da conversa de e-mail,
    não (`conversa_de_email`); a rota com a de e-mail escolhida recusa sem
    enviar nada; sem escolher, a oferta sai pelo chat."""
    chat_falso = troca_ligada
    c = await of._cenario(db, make_user, monkeypatch)
    email = await _email_shopee(db, c)

    out = await painel.painel_da_conversa(db, email, user=c.adm)
    oferta = out["ag_cancelamento"]["oferta_envio"]
    assert (oferta["disponivel"], oferta["motivo"]) == (False, "conversa_de_email")
    assert oferta["texto_motivo"] == troca_oferta.TEXTOS["conversa_de_email"]
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["oferta_envio"]["disponivel"] is True

    auth_as(c.adm)
    r = await client.post(of.URL, json={"sku_novo": c.novo, "conversa_id": str(email.id)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conversa_de_email"
    assert chat_falso.textos == []
    assert await _da_loja(db, email.id) == [] and await _da_loja(db, c.conversa.id) == []

    # A lista: o chat, com o botão que pode.
    r = await client.get(URL_LISTA)
    assert r.status_code == 200, r.text
    [p] = r.json()["pedidos"]
    assert p["conversa_id"] == str(c.conversa.id)
    assert p["motivo"]["oferta_envio"]["disponivel"] is True

    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=of.NUMERO, sku_novo=c.novo)
    assert out["conversa_id"] == c.conversa.id and out["status"] == "enviada"
    assert chat_falso.textos == [of._texto_4b()]
    assert await _da_loja(db, email.id) == []


async def test_pedido_so_com_a_conversa_de_email_nao_tem_oferta(
    db, client, auth_as, make_user, monkeypatch, troca_ligada
):
    """Sem chat, só com a conversa de e-mail: é o pedido SEM conversa — a lista
    não aponta conversa, o botão diz `sem_conversa` e nada sai (nem e-mail
    para o remetente do aviso)."""
    chat_falso = troca_ligada
    c = await of._cenario(db, make_user, monkeypatch, conversa=False)
    email = await _email_shopee(db, c)

    auth_as(c.adm)
    r = await client.get(URL_LISTA)
    assert r.status_code == 200, r.text
    [p] = r.json()["pedidos"]
    assert p["conversa_id"] is None
    assert p["motivo"]["codigo"] == "sem_estoque"
    assert p["motivo"]["oferta_envio"]["motivo"] == "sem_conversa"

    r = await client.post(of.URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_conversa"
    r = await client.post(of.URL, json={"sku_novo": c.novo, "conversa_id": str(email.id)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conversa_de_email"
    assert chat_falso.textos == [] and await _da_loja(db, email.id) == []
    # A troca segue sem conversa (nem a de e-mail).
    assert (await troca._carregar(db, of.NUMERO)).conversa is None
    assert (await troca._carregar(db, of.NUMERO, email.id)).conversa is None


# ─────────────── a devolução ───────────────


def _email_dev(integ, *, horas: int = 1) -> AtendimentoConversa:
    return AtendimentoConversa(
        integration_id=integ.id,
        plataforma="shopee",
        canal="email",
        externo_id="mail-fio:aviso-dev",
        pedido_marketplace=dev.SN,
        dados={"fonte": "tuta", "mail": {"alias": "kfa@tuta.com"}},
        ultima_mensagem_em=dev.T0 - timedelta(hours=horas),
    )


async def test_devolucao_liga_o_chat_e_nunca_a_conversa_de_email(db, make_user):
    """O chat (velho) e a conversa de e-mail (nova) do pedido: o caso liga o
    chat e não cria a conversa da devolução."""
    integ = await dev._loja(db, make_user)
    chat_id = await dev._chat(db)
    db.add(_email_dev(integ))
    await db.commit()
    await dev._logistica(db)
    await dev._rastreio(db, prazo=dev.T0 + timedelta(days=2))

    resumo = await dev._ligar(db, fabrica_cliente=dev._fabrica(dev._Plataforma()))

    assert (resumo["novas"], resumo["conversas_novas"], resumo["erros"]) == (1, 0, 0)
    [linha] = await dev._linhas(db)
    assert linha.conversa_id == chat_id
    assert await dev._conversas(db, canal="reclamacao") == []


async def test_devolucao_encerrada_so_com_a_conversa_de_email_fica_sem_conversa(db, make_user):
    """O caso encerrado sem chat não ganha conversa — e não é ligado à conversa
    de e-mail (que mora na aba E-mail; o cartão ali ninguém veria)."""
    integ = await dev._loja(db, make_user)
    email = _email_dev(integ)
    db.add(email)
    await db.commit()
    email_id = email.id
    await dev._logistica(db, status="CANCELLED", mudou=dev.T0 - timedelta(days=2))

    resumo = await dev._ligar(db, fabrica_cliente=dev._fabrica(dev._Plataforma()))

    assert (resumo["novas"], resumo["conversas_novas"]) == (1, 0)
    [linha] = await dev._linhas(db)
    assert linha.encerrada_em is not None
    assert linha.conversa_id is None and linha.conversa_id != email_id


# ─────────────── o alerta, o cartão Cliente e a thread da Amazon ───────────────


async def test_alerta_do_telegram_nao_leva_a_conversa_de_email(db, cena, monkeypatch):
    """A sonda `test_sonda_alerta_telegram_leva_conversa_de_email`: a conversa de
    e-mail da ponte com o prazo vencendo não entra no alerta (o filtro
    "vencendo" é da Caixa); o chat vencendo, sim."""
    mid = await entrar(db, cena.geral, assunto="Pergunta", corpo="Quando chega?")
    await rodar(db)
    email = await conversa(db, (await meta_de(db, mid)).conversa_id)
    assert separa_email.e_conversa_de_email(email)
    email.prazo_resposta_em = AGORA + timedelta(hours=1)
    email.aguardando_resposta = True
    email.sem_resposta_necessaria = False
    await db.commit()
    email_id = email.id
    monkeypatch.setattr(get_settings(), "atendimento_alerta_telegram", True)
    enviados: list[str] = []

    class _Telegram:
        async def safe_send(self, texto):
            enviados.append(texto)
            return True

    async def _sempre(_chave):
        return True

    monkeypatch.setattr(sync_svc, "TelegramClient", _Telegram)
    monkeypatch.setattr(sync_svc, "_reservar_aviso", _sempre)

    assert await sync_svc.alertar_prazos(db) == 0
    assert enviados == []

    chat = await _chat_ml(db, cena, externo="pack-alerta", pedido=PED)
    chat.prazo_resposta_em = AGORA + timedelta(hours=1)
    chat.aguardando_resposta = True
    chat.sem_resposta_necessaria = False
    await db.commit()
    chat_id = chat.id
    assert await sync_svc.alertar_prazos(db) == 1
    [texto] = enviados
    assert str(chat_id) in texto and str(email_id) not in texto


async def test_cartao_cliente_e_thread_da_amazon_nunca_a_conversa_da_ponte(db, cena):
    """A sonda `test_sonda_cartao_cliente_do_chat_amazon`: a Amazon pelo GMAIL (o
    chat) e a conversa de e-mail da ponte do MESMO endereço de retransmissão. O
    cartão Cliente do chat não a mostra; o e-mail seguinte do comprador (sem
    pedido, citando o e-mail da ponte) entra no chat — e, sem o chat, não cai
    na conversa da ponte (nasce a dele)."""
    relay = "abc123@marketplace.amazon.com.br"
    gmail, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=cena.amazon,
        plataforma="amazon",
        canal_nome="email",
        externo_id=f"{relay}|-",
        comprador_id=relay,
        comprador_nome="Comprador",
    )
    await gravar.gravar_mensagem(
        db,
        gmail,
        externo_id="<g1@amazon>",
        autor="cliente",
        texto="oi",
        enviada_em=AGORA - timedelta(hours=2),
    )
    da_ponte, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=cena.amazon,
        plataforma="amazon",
        canal_nome="email",
        externo_id="mail-fio:x1",
        comprador_id=relay,
        comprador_nome="Comprador",
        dados={"fonte": "tuta", "mail": {"alias": "mike14@tuta.com"}},
    )
    await gravar.gravar_mensagem(
        db,
        da_ponte,
        externo_id="<x1@mail.teste>",
        autor="cliente",
        texto="outro e-mail",
        enviada_em=AGORA - timedelta(hours=1),
        payload={"mail": {"message_id": "<x1@mail.teste>"}},
    )
    await db.commit()
    assert separa_email.e_conversa_de_email(da_ponte)
    assert not separa_email.e_conversa_de_email(gmail)

    cartao = await cliente_svc.cartao_cliente(db, gmail)
    refs = [e.get("ref") for e in (cartao.get("linha_do_tempo") or [])]
    assert str(da_ponte.id) not in refs

    def _seguinte(referencias: list[str], remetente: str = relay) -> amazon_email.EmailAmazon:
        return amazon_email.EmailAmazon(
            message_id=f"<{uuid4().hex}@amazon>",
            remetente=remetente,
            remetente_nome="Comprador",
            tag=None,
            assunto="Re: oi",
            pedido=None,
            enviada_em=AGORA,
            texto="e aí?",
            referencias=referencias,
        )

    # Pelo endereço (a mais recente do mesmo comprador) e pela referência.
    for refs_ in ([], ["<x1@mail.teste>"]):
        achada = await amazon_email._conversa_da_thread(db, _seguinte(refs_), AGORA)
        assert achada is not None and achada.id == gmail.id, refs_
    # Outro comprador SÓ com a conversa da ponte (sem chat): nunca ela.
    sozinho = "def456@marketplace.amazon.com.br"
    so_ponte, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=cena.amazon,
        plataforma="amazon",
        canal_nome="email",
        externo_id="mail-fio:y1",
        comprador_id=sozinho,
        comprador_nome="Outro",
        dados={"fonte": "tuta", "mail": {"alias": "mike14@tuta.com"}},
    )
    await gravar.gravar_mensagem(
        db,
        so_ponte,
        externo_id="<y1@mail.teste>",
        autor="cliente",
        texto="e-mail",
        enviada_em=AGORA - timedelta(hours=1),
        payload={"mail": {"message_id": "<y1@mail.teste>"}},
    )
    await db.commit()
    for refs_ in ([], ["<y1@mail.teste>"]):
        seguinte = _seguinte(refs_, sozinho)
        assert await amazon_email._conversa_da_thread(db, seguinte, AGORA) is None, refs_
