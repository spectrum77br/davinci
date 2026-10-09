"""O que faltava do PROJETO-COMUNICADOR no e-mail das lojas (09/10/2026).

RF6 (os chamados dos sites): os campos do formulário (nome, telefone, pedido),
o protocolo repetido de OUTRA cliente numa conversa própria, os alertas do
chamado (sem protocolo, formato errado, repetido), a busca por TODOS os
protocolos do chamado agrupado, a sugestão de agrupar ("Não agrupar", fica o
mais antigo, todos os protocolos), o status derivado e o pedido citado só como
sugestão. RF5/§8: a auditoria do "Escolher loja"/"Vincular", a faixa vermelha
que acende com a leitura ou a ponte paradas, o {protocolo} das respostas
prontas, o limite de caracteres do e-mail e a frase do "Sugerir resposta".

Postgres local de verdade (o mundo `cena` da ponte); nunca conta do Tuta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    BlingOrder,
    MailMailbox,
    MailMessage,
    UserRole,
)
from app.models.mail_atendimento import MailAgenteV2, MailMessageMeta
from app.security.cipher import decrypt_json
from app.services import vigia_leitura_atendimento
from app.services.atendimento.constantes import (
    MOTIVO_EMAIL_SO_PESSOA,
    MOTIVO_TUTA_SEM_ENVIO,
    motivo_canal_sem_envio,
)
from app.services.mail_atendimento import chamados, enderecos, pedido, saude
from app.services.mail_atendimento.constantes import RESPOSTA_MAX_CARACTERES
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte
from tests.test_mail_ponte import (
    contar,
    conversa,
    entrar,
    mensagens,
    meta_de,
    pedido_existe,
    rodar,
)
from tests.test_mail_responder import _jobs, _ligar_envio, _responder

API = "/api/atendimento"
EMAIL = f"{API}/email"
OUTRO = "quem-mexe@davinci-test.com"


@pytest.fixture
async def admin(cena, auth_as):
    auth_as(cena.dono)
    return cena.dono


@pytest.fixture
async def quem_le(make_user, auth_as, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", OUTRO)
    user = await make_user(role=UserRole.USER)
    auth_as(user)
    return user


async def _formulario(
    db,
    cena,
    protocolo: str | None,
    corpo: str,
    *,
    caixa: str = "sac",
    assunto: str | None = None,
    recebido_em: datetime | None = None,
) -> UUID:
    """O formulário do site como chega: DE sac@ PARA sac@, o cliente no corpo."""
    return await entrar(
        db,
        cena.geral,
        pasta=f"*uranyx {caixa}",
        de=f"{caixa}@uranyx.com.br",
        de_nome="Site Uranyx",
        para=[f"{caixa}@uranyx.com.br"],
        assunto=assunto if assunto is not None else f"[{protocolo}] Dúvida",
        corpo=corpo,
        recebido_em=recebido_em,
    )


def _codigos(meta: MailMessageMeta) -> set[str]:
    return {a["codigo"] for a in meta.alertas or []}


# ─────────────── S1: os campos do formulário ───────────────


def test_campos_do_formulario_puro():
    c = enderecos.campos_do_formulario(
        "Nome: Maria Souza\nE-mail: maria@gmail.com\nTelefone: (11) 98765-4321\n"
        "Nº do pedido: #45210\nMensagem: oi"
    )
    assert c == {"nome": "Maria Souza", "telefone": "11987654321", "pedido": "45210"}
    # O HTML do site que virou UMA linha: cada valor para no próximo rótulo.
    c = enderecos.campos_do_formulario(
        "Nome: João da Silva E-mail: j@x.com Telefone/WhatsApp: +55 11 3456-7890 "
        "Pedido: 2000012345678901 Mensagem: oi"
    )
    assert c == {"nome": "João da Silva", "telefone": "1134567890", "pedido": "2000012345678901"}
    # Sem cara do que é: fica de fora (nunca o e-mail como nome, nunca "não tenho").
    c = enderecos.campos_do_formulario(
        "Sobrenome: Souza\nSeu nome: maria@gmail.com\nTel: 123\nPedido: não tenho"
    )
    assert c == {"nome": None, "telefone": None, "pedido": None}
    assert enderecos.telefone_digitos("0 11 98765-4321") == "11987654321"


async def test_s1_nome_telefone_e_pedido_do_formulario_vao_para_o_chamado(db, cena):
    mid = await _formulario(
        db,
        cena,
        "US-26-0101",
        "Nome: Maria Souza\nE-mail: maria@gmail.com\nTelefone: (11) 98765-4321\n"
        "Pedido: 45210\nMensagem: quero trocar",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    c = await conversa(db, m.conversa_id)
    # O nome do chamado é o da cliente, nunca o do site que mandou o e-mail.
    assert c.comprador_nome == "Maria Souza" and c.comprador_id == "maria@gmail.com"
    mail = c.dados["mail"]
    assert (mail["cliente_nome"], mail["telefone"], mail["pedido_citado"]) == (
        "Maria Souza",
        "11987654321",
        "45210",
    )
    # O pedido citado NUNCA liga sozinho.
    assert c.pedido_marketplace is None
    # A meta não ganha texto do e-mail (só ids, estados e contagens).
    assert "Maria" not in str(m.alertas) and m.pedido_marketplace is None


async def test_s1_nome_com_codigo_nao_entra_e_o_texto_vem_protegido(db, cena):
    """O campo é lido do texto JÁ protegido: o que a equipe não vê não vira nome."""
    mid = await _formulario(
        db,
        cena,
        "US-26-0102",
        "Nome: https://x.com/login?token=abc\nE-mail: ana@gmail.com\nMensagem: oi",
    )
    await rodar(db)
    c = await conversa(db, (await meta_de(db, mid)).conversa_id)
    assert c.comprador_nome is None and "token" not in str(c.dados)


async def test_s1_pedido_citado_so_como_sugestao(db, cena, client, admin):
    mid = await _formulario(
        db,
        cena,
        "US-26-0103",
        "Nome: Maria Souza\nE-mail: maria@gmail.com\nPedido: 777123",
    )
    db.add(
        BlingOrder(
            numero="55001",
            numeroloja="777123",
            data=datetime.now(UTC) - timedelta(days=3),
            nome_destinatario="MARIA APARECIDA SOUZA",
            item_index=0,
        )
    )
    db.add(BlingOrder(numero="55001", numeroloja="777123", item_index=1))
    await db.commit()
    await rodar(db)
    m = await meta_de(db, mid)
    r = (await client.get(f"{EMAIL}/conversas/{m.conversa_id}/emails")).json()
    sug = r["chamado"]["pedido_sugerido"]
    assert sug["numero"] == "777123" and sug["achado"] is True
    [p] = sug["pedidos"]  # uma linha por PEDIDO (o espelho tem uma por item)
    assert (p["numero_bling"], p["numero_loja"], p["confere"]) == ("55001", "777123", True)
    # O pedido de OUTRA pessoa: aparece "não confere", sem o nome dela.
    pedido_de_outra = await db.scalar(
        select(BlingOrder).where(BlingOrder.numero == "55001", BlingOrder.item_index == 0)
    )
    pedido_de_outra.nome_destinatario = "JOSE PEREIRA"
    await db.commit()
    r = (await client.get(f"{EMAIL}/conversas/{m.conversa_id}/emails")).json()
    [p] = r["chamado"]["pedido_sugerido"]["pedidos"]
    assert p["confere"] is False and "JOSE" not in str(r["chamado"])
    c = await conversa(db, m.conversa_id)
    assert c.pedido_marketplace is None


# ─────────────── S2 e S6: o protocolo e os alertas do chamado ───────────────


async def test_s2_protocolo_repetido_de_outra_cliente_abre_conversa_propria(
    db, cena, client, admin
):
    a = await _formulario(db, cena, "US-26-0200", "Nome: Maria\nE-mail: maria@gmail.com")
    await rodar(db)
    b = await _formulario(db, cena, "US-26-0200", "Nome: Joana\nE-mail: joana@gmail.com")
    await rodar(db)
    ma, mb = await meta_de(db, a), await meta_de(db, b)
    assert ma.conversa_id != mb.conversa_id
    ca, cb = await conversa(db, ma.conversa_id), await conversa(db, mb.conversa_id)
    assert (ca.comprador_id, cb.comprador_id) == ("maria@gmail.com", "joana@gmail.com")
    assert ca.externo_id == "mail-protocolo:US-26-0200"
    assert cb.externo_id.startswith("mail-protocolo:US-26-0200:")
    # O alerta: no cartão do e-mail e na faixa do chamado da 2ª cliente.
    assert "protocolo_repetido" in _codigos(mb) and "protocolo_repetido" not in _codigos(ma)
    assert cb.dados["mail"]["alertas"] == ["protocolo_repetido"]
    r = (await client.get(f"{EMAIL}/conversas/{cb.id}/emails")).json()
    assert [x["codigo"] for x in r["chamado"]["alertas"]] == ["protocolo_repetido"]
    assert "OUTRA cliente" in r["chamado"]["alertas"][0]["texto"]
    # Cada uma tem a sua: a mensagem nova de cada cliente cai na conversa dela.
    a2 = await _formulario(db, cena, "US-26-0200", "Nome: Maria\nE-mail: maria@gmail.com")
    b2 = await _formulario(db, cena, "US-26-0200", "Nome: Joana\nE-mail: joana@gmail.com")
    await rodar(db)
    assert (await meta_de(db, a2)).conversa_id == ca.id
    assert (await meta_de(db, b2)).conversa_id == cb.id
    # As duas nunca se sugerem agrupar (outra cliente).
    assert (await client.get(f"{EMAIL}/conversas/{cb.id}/emails")).json()["outros_chamados"] == []


async def test_s2_email_direto_com_o_protocolo_continua_no_chamado(db, cena, client, admin):
    """A regra da cliente vale para o FORMULÁRIO (o site que repetiu o número).
    O e-mail que a pessoa escreve direto com o protocolo no assunto entra no
    chamado (o ÚNICO desse protocolo), com o alerta no cartão do e-mail e o de
    OUTRO endereço na faixa — a resposta para ele pede confirmação."""
    a = await _formulario(db, cena, "US-26-0220", "Nome: Maria\nE-mail: maria@gmail.com")
    await rodar(db)
    direto = await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        de="maria.souza@empresa.com",
        de_nome="Maria Souza",
        para=["sac@uranyx.com.br"],
        assunto="Re: [US-26-0220] Dúvida",
        corpo="Escrevo do e-mail do trabalho.",
        recebido_em=datetime.now(UTC) - timedelta(minutes=1),
    )
    await rodar(db)
    ma, md = await meta_de(db, a), await meta_de(db, direto)
    assert md.conversa_id == ma.conversa_id and md.vinculado_por == "protocolo"
    assert "protocolo_repetido" in _codigos(md)
    c = await conversa(db, ma.conversa_id)
    assert c.dados["mail"]["alertas"] == ["outro_remetente"]
    # O outro endereço nunca vira "cliente" do chamado por ter escrito.
    assert c.comprador_id == "maria@gmail.com" and "clientes" not in c.dados["mail"]
    r = (await client.get(f"{EMAIL}/conversas/{c.id}/emails")).json()
    [alerta] = r["chamado"]["alertas"]
    assert alerta["codigo"] == "outro_remetente" and "OUTRO endereço" in alerta["texto"]
    await _ligar_envio(db, cena.geral)
    previa = (await client.get(f"{EMAIL}/conversas/{c.id}/previa")).json()
    assert previa["para"] == "maria.souza@empresa.com"
    [trava] = [b for b in previa["bloqueios"] if b["codigo"] == "para_fora_do_chamado"]
    assert trava["confirmavel"] is True and "maria@gmail.com" in trava["texto"]


async def test_s6_formulario_sem_protocolo_gera_alerta(db, cena, client, admin):
    mid = await _formulario(
        db, cena, None, "Nome: Ana\nE-mail: ana@gmail.com", assunto="Contato pelo site"
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "gravado" and m.protocolo is None
    assert _codigos(m) == {"sem_protocolo"}
    r = (await client.get(f"{EMAIL}/conversas/{m.conversa_id}/emails")).json()
    assert r["chamado"]["protocolo"] is None and r["chamado"]["protocolos"] == []
    assert [x["codigo"] for x in r["chamado"]["alertas"]] == ["sem_protocolo"]


@pytest.mark.parametrize(
    ("assunto", "corpo"),
    [
        ("[US-2026-0001] Troca", "E-mail: ana@gmail.com"),
        ("[UX-26-0001] Troca", "E-mail: ana@gmail.com"),
        ("[US-26-001] Troca", "E-mail: ana@gmail.com"),
        ("Contato pelo site", "Protocolo: US-26-1\nE-mail: ana@gmail.com"),
    ],
)
async def test_s6_protocolo_com_formato_errado_gera_o_alerta_proprio(db, cena, assunto, corpo):
    mid = await _formulario(db, cena, None, corpo, assunto=assunto)
    await rodar(db)
    m = await meta_de(db, mid)
    # O número errado nunca vira a chave do chamado; o alerta diz o que houve.
    assert m.protocolo is None and _codigos(m) == {"protocolo_formato"}
    c = await conversa(db, m.conversa_id)
    assert c.dados["mail"]["alertas"] == ["protocolo_formato"]


def test_s6_formato_errado_puro_nao_pega_pedido_nem_texto_comum():
    assert pedido.protocolo_fora_do_formato("[US-2026-0001] Oi", "") is True
    # Pedido da Amazon, telefone e "protocolo" no meio da frase: não é protocolo.
    assert pedido.protocolo_fora_do_formato("Pedido 701-1234567-1234567", "") is False
    assert pedido.protocolo_fora_do_formato("Oi", "qual o protocolo para trocar?") is False
    assert pedido.protocolo_fora_do_formato("Oi", "Telefone: 11-9876-5432") is False


async def test_s6_protocolo_repetido_da_mesma_cliente_nao_alerta(db, cena):
    a = await _formulario(db, cena, "US-26-0210", "E-mail: maria@gmail.com\nMensagem: 1")
    await rodar(db)
    b = await _formulario(db, cena, "US-26-0210", "E-mail: maria@gmail.com\nMensagem: 2")
    await rodar(db)
    ma, mb = await meta_de(db, a), await meta_de(db, b)
    assert ma.conversa_id == mb.conversa_id and mb.vinculado_por == "protocolo"
    assert "protocolo_repetido" not in _codigos(mb)


# ─────────────── S3: a busca por todos os protocolos ───────────────


async def test_s3_busca_acha_o_chamado_agrupado_por_qualquer_protocolo(db, cena, client, admin):
    ids = []
    for protocolo in ("US-26-0301", "US-26-0302"):
        ids.append(await _formulario(db, cena, protocolo, "E-mail: maria@gmail.com\nMensagem: oi"))
        await rodar(db)
    a, b = await meta_de(db, ids[0]), await meta_de(db, ids[1])
    r = await client.post(
        f"{EMAIL}/conversas/{b.conversa_id}/agrupar", json={"conversa_id": str(a.conversa_id)}
    )
    assert r.status_code == 200, r.text
    # Mensagem nova no chamado que ficou: o resumo da última já não tem o 2º protocolo.
    volta = await _formulario(
        db, cena, "US-26-0301", "E-mail: maria@gmail.com\nMensagem: alguma novidade?"
    )
    await rodar(db)
    assert (await meta_de(db, volta)).conversa_id == a.conversa_id
    for q in ("US-26-0302", "us-26-0301", "26-0302"):
        r = await client.get(f"{API}/conversas", params={"q": q, "filtro": "todas"})
        assert r.status_code == 200, r.text
        assert [i["id"] for i in r.json()["itens"]] == [str(a.conversa_id)], q
    r = await client.get(f"{API}/conversas", params={"q": "US-26-0399"})
    assert r.json()["itens"] == []


# ─────────────── S4: a sugestão de agrupar ───────────────


async def test_s4_sugere_pelo_telefone_e_pelo_pedido_e_fica_o_mais_antigo(db, cena, client, admin):
    velho = await _formulario(
        db,
        cena,
        "UDS-26-0401",
        "Nome: Maria\nE-mail: maria@gmail.com\nTelefone: (11) 98765-4321",
        caixa="duvidas",
        recebido_em=datetime.now(UTC) - timedelta(hours=3),
    )
    novo = await _formulario(
        db,
        cena,
        "US-26-0402",
        "Nome: Maria S.\nE-mail: maria.trabalho@empresa.com\nTelefone: +55 11 98765-4321",
    )
    outro_pedido = await _formulario(
        db, cena, "US-26-0403", "E-mail: zeca@gmail.com\nPedido: 99887"
    )
    mesmo_pedido = await _formulario(
        db, cena, "US-26-0404", "E-mail: zeca.outro@gmail.com\nPedido: 99887"
    )
    await rodar(db)
    mv, mn = await meta_de(db, velho), await meta_de(db, novo)
    # Pelo telefone (e-mails diferentes), na mesma marca; todos os campos na faixa.
    r = (await client.get(f"{EMAIL}/conversas/{mn.conversa_id}/emails")).json()
    [sug] = r["outros_chamados"]
    assert sug["protocolo"] == "UDS-26-0401" and sug["motivo"] == "telefone"
    assert sug["motivo_rotulo"] == "mesmo telefone, e-mail diferente" and sug["fica_este"] is True
    assert r["chamado"]["status"] == "aberto" and r["chamado"]["status_rotulo"] == "Aberto"
    # Pelo nº de pedido escrito no formulário.
    mp = await meta_de(db, mesmo_pedido)
    r = (await client.get(f"{EMAIL}/conversas/{mp.conversa_id}/emails")).json()
    assert [(o["protocolo"], o["motivo"]) for o in r["outros_chamados"]] == [
        ("US-26-0403", "pedido")
    ]
    assert (await meta_de(db, outro_pedido)).conversa_id != mp.conversa_id
    # Agrupar clicando do lado do VELHO: fica o velho mesmo assim.
    r = await client.post(
        f"{EMAIL}/conversas/{mv.conversa_id}/agrupar", json={"conversa_id": str(mn.conversa_id)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["conversa_id"] == str(mv.conversa_id)
    assert r.json()["protocolos"] == ["UDS-26-0401", "US-26-0402"]
    destino = await conversa(db, mv.conversa_id)
    assert destino.dados["mail"]["clientes"] == [
        "maria.trabalho@empresa.com",
        "maria@gmail.com",
    ]
    assert (await conversa(db, mn.conversa_id)).situacao == "fechada"
    # A faixa do chamado que ficou mostra TODOS os protocolos.
    r = (await client.get(f"{EMAIL}/conversas/{mv.conversa_id}/emails")).json()
    assert r["chamado"]["protocolos"] == ["UDS-26-0401", "US-26-0402"]
    # O 2º e-mail da cliente pelo protocolo do agrupado (do OUTRO e-mail dela)
    # entra no que ficou — nunca vira "protocolo repetido".
    volta = await _formulario(
        db, cena, "US-26-0402", "E-mail: maria.trabalho@empresa.com\nMensagem: e aí?"
    )
    await rodar(db)
    mvolta = await meta_de(db, volta)
    assert mvolta.conversa_id == destino.id and "protocolo_repetido" not in _codigos(mvolta)
    # Um chamado NOVO do outro e-mail dela (sem telefone): a sugestão acha o
    # chamado que ficou pelos e-mails guardados nele (`clientes`).
    terceiro = await _formulario(
        db, cena, "US-26-0405", "E-mail: maria.trabalho@empresa.com\nMensagem: outro assunto"
    )
    await rodar(db)
    m3 = await meta_de(db, terceiro)
    r = (await client.get(f"{EMAIL}/conversas/{m3.conversa_id}/emails")).json()
    assert [(o["protocolo"], o["motivo"]) for o in r["outros_chamados"]] == [
        ("UDS-26-0401", "email")
    ]


async def test_s4_agrupado_de_agrupado_guarda_todos_os_protocolos(db, cena):
    ids = []
    for protocolo in ("US-26-0411", "US-26-0412", "US-26-0413"):
        ids.append(await _formulario(db, cena, protocolo, "E-mail: maria@gmail.com"))
        await rodar(db)
    a, b, c = [await conversa(db, (await meta_de(db, i)).conversa_id) for i in ids]
    await chamados.agrupar(db, c, b, cena.dono)
    await db.commit()
    await chamados.agrupar(db, b, a, cena.dono)
    await db.commit()
    a = await conversa(db, a.id)
    assert chamados.protocolos(a) == ["US-26-0411", "US-26-0412", "US-26-0413"]


async def test_s4_nao_agrupar_lembra_a_recusa_nos_dois(db, cena, client, admin, auth_as):
    ids = []
    for protocolo in ("US-26-0421", "US-26-0422"):
        ids.append(await _formulario(db, cena, protocolo, "E-mail: maria@gmail.com"))
        await rodar(db)
    a, b = await meta_de(db, ids[0]), await meta_de(db, ids[1])
    r = (await client.get(f"{EMAIL}/conversas/{b.conversa_id}/emails")).json()
    assert [o["protocolo"] for o in r["outros_chamados"]] == ["US-26-0421"]
    r = await client.post(
        f"{EMAIL}/conversas/{b.conversa_id}/nao-agrupar",
        json={"conversa_id": str(a.conversa_id)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["outros_chamados"] == []
    for lado in (a.conversa_id, b.conversa_id):
        r = (await client.get(f"{EMAIL}/conversas/{lado}/emails")).json()
        assert r["outros_chamados"] == [], lado
    cb = await conversa(db, b.conversa_id)
    [recusa] = cb.dados["mail"]["nao_agrupar"]
    assert recusa["conversa_id"] == str(a.conversa_id) and recusa["por"] == str(cena.dono.id)
    assert recusa["em"]
    # De novo: não duplica. E agrupar à mão continua possível.
    await client.post(
        f"{EMAIL}/conversas/{a.conversa_id}/nao-agrupar",
        json={"conversa_id": str(b.conversa_id)},
    )
    assert len((await conversa(db, b.conversa_id)).dados["mail"]["nao_agrupar"]) == 1
    r = await client.post(
        f"{EMAIL}/conversas/{b.conversa_id}/agrupar", json={"conversa_id": str(a.conversa_id)}
    )
    assert r.status_code == 200


async def test_s4_nao_agrupar_so_quem_mexe(db, cena, client, quem_le):
    ids = []
    for protocolo in ("US-26-0431", "US-26-0432"):
        ids.append(await _formulario(db, cena, protocolo, "E-mail: maria@gmail.com"))
    await rodar(db)
    a, b = await meta_de(db, ids[0]), await meta_de(db, ids[1])
    r = await client.post(
        f"{EMAIL}/conversas/{b.conversa_id}/nao-agrupar",
        json={"conversa_id": str(a.conversa_id)},
    )
    assert r.status_code == 403


# ─────────────── S9: o status do chamado ───────────────


async def test_s9_status_aberto_aguardando_cliente_e_resolvido(db, cena):
    mid = await _formulario(db, cena, "US-26-0901", "E-mail: maria@gmail.com")
    await rodar(db)
    c = await conversa(db, (await meta_de(db, mid)).conversa_id)
    assert chamados.status_do_chamado(c) == "aberto"
    agora = datetime.now(UTC)
    db.add(
        AtendimentoMensagem(
            conversa_id=c.id,
            autor="loja",
            origem="davinci_humano",
            tipo="texto",
            texto="Respondido.",
            status="enviada",
            enviada_em=agora,
            payload={},
        )
    )
    c.aguardando_resposta = False
    c.ultima_da_loja_em = agora
    await db.commit()
    c = await conversa(db, c.id)
    assert chamados.status_do_chamado(c) == "aguardando_cliente"
    assert chamados.resumo_do_chamado(c)["status_rotulo"] == "Aguardando cliente"
    c.situacao = "fechada"
    await db.commit()
    assert chamados.status_do_chamado(await conversa(db, c.id)) == "resolvido"


# ─────────────── S7: quem escolheu a loja e quem vinculou ───────────────


async def test_s7_escolher_loja_e_vincular_registram_quem_e_quando(db, cena, client, admin):
    mid = await entrar(db, cena.geral, para=["hans21@tuta.com"], assunto="Oi loja")
    await rodar(db)
    assert (await meta_de(db, mid)).estado == "sem_loja"
    r = await client.post(
        f"{EMAIL}/emails/{mid}/loja", json={"store_info_id": str(cena.loja_ml.id)}
    )
    assert r.status_code == 200, r.text
    m = await meta_de(db, mid)
    [registro] = [a for a in m.alertas if a["codigo"] == "loja_escolhida"]
    assert registro["por"] == str(cena.dono.id) and registro["em"]
    assert "loja escolhida à mão por" in registro["texto"]
    notas = [x for x in await mensagens(db, m.conversa_id) if x.autor == "sistema"]
    assert len(notas) == 1 and "à mão" in notas[0].texto
    # E o cartão mostra (o mesmo registro).
    cartao = r.json()
    assert any(a["codigo"] == "loja_escolhida" for a in cartao["alertas"])
    # Vincular: a nota (já existia) e o registro no cartão do e-mail.
    await pedido_existe(db, cena.ml, "2000012345678901")
    r = await client.post(f"{EMAIL}/emails/{mid}/vincular", json={"pedido": "2000012345678901"})
    assert r.status_code == 200, r.text
    m = await meta_de(db, mid)
    [vinculo] = [a for a in m.alertas if a["codigo"] == "vinculado_por_pessoa"]
    assert "2000012345678901" in vinculo["texto"] and vinculo["por"] == str(cena.dono.id)


async def test_s7_ignorar_registra_quando(db, cena, client, admin):
    mid = await entrar(db, cena.geral, para=["hans21@tuta.com"], assunto="Oi loja")
    await rodar(db)
    assert (await client.post(f"{EMAIL}/emails/{mid}/ignorar")).status_code == 200
    alertas = (await meta_de(db, mid)).alertas
    [registro] = [a for a in alertas if a["codigo"] == "ignorado_por_pessoa"]
    assert registro["por"] and registro["em"] and "ignorado por" in registro["texto"]


# ─────────────── S8 e S10: a resposta do e-mail ───────────────


async def test_s8_lacuna_protocolo_preenchida_no_chamado(db, cena, client, admin):
    mid = await _formulario(db, cena, "US-26-0801", "E-mail: maria@gmail.com")
    await rodar(db)
    conversa_id = (await meta_de(db, mid)).conversa_id
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id, texto="Seu chamado {protocolo} foi resolvido.")
    assert r.status_code == 200, r.text
    assert r.json()["mensagem"]["texto"] == "Seu chamado US-26-0801 foi resolvido."
    [job] = await _jobs(db)
    assert decrypt_json(job.content_enc)["text"].startswith("Seu chamado US-26-0801 foi resolvido.")


async def test_s8_lacuna_protocolo_fora_de_chamado_segura_o_envio(db, cena, client, admin):
    mid = await entrar(db, cena.geral)
    await rodar(db)
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, (await meta_de(db, mid)).conversa_id, texto="Chamado {protocolo}")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "texto_invalido"
    assert await _jobs(db) == []


async def test_s10_limite_da_resposta_do_email_e_o_do_email(db, cena, client, admin):
    mid = await entrar(db, cena.geral)
    await rodar(db)
    conversa_id = (await meta_de(db, mid)).conversa_id
    r = await client.get(f"{API}/conversas/{conversa_id}")
    assert r.status_code == 200, r.text
    assert r.json()["envio"]["limite_caracteres"] == RESPOSTA_MAX_CARACTERES
    # A resposta longa (mais que os 1000 do canal) sai.
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id, texto="x" * 1500)
    assert r.status_code == 200, r.text


# ─────────────── S11: a frase do "Sugerir resposta" ───────────────


def test_s11_frase_do_email_da_ponte_e_a_velha_so_fora_dela():
    ponte_dados = {"fonte": "tuta", "mail": {"mailbox_id": "x"}}
    assert motivo_canal_sem_envio("email", "ml", ponte_dados) == MOTIVO_EMAIL_SO_PESSOA
    assert motivo_canal_sem_envio("email", "site", ponte_dados) == MOTIVO_EMAIL_SO_PESSOA
    assert motivo_canal_sem_envio("email", "ml", {"fonte": "tuta"}) == MOTIVO_TUTA_SEM_ENVIO
    assert "ainda não existe" not in MOTIVO_EMAIL_SO_PESSOA


async def test_s11_sugerir_no_email_da_ponte_diz_a_frase_certa(db, cena, client, admin):
    mid = await entrar(db, cena.geral)
    await rodar(db)
    conversa_id = (await meta_de(db, mid)).conversa_id
    r = await client.post(f"{API}/conversas/{conversa_id}/rascunho")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["detail"] == MOTIVO_EMAIL_SO_PESSOA


# ─────────────── S5: a faixa vermelha (leitura e ponte paradas) ───────────────


class _RedisFalso:
    def __init__(self) -> None:
        self.valores: dict[str, str] = {}

    async def get(self, chave):
        return self.valores.get(chave)

    async def set(self, chave, valor, **_k):
        self.valores[chave] = str(valor)
        return True


class _RedisFora:
    async def get(self, *_a, **_k):
        raise ConnectionError("fora")

    async def set(self, *_a, **_k):
        raise ConnectionError("fora")


@pytest.fixture
def redis_falso(monkeypatch) -> _RedisFalso:
    r = _RedisFalso()
    monkeypatch.setattr(saude, "redis", r)
    return r


async def _emails_parados(db) -> list:
    return [
        lp for lp in await vigia_leitura_atendimento.leitura_parada(db) if lp.plataforma == "email"
    ]


async def test_s5_mac_com_sinal_mas_sem_ler_acende(db, cena, redis_falso):
    geral = await db.get(MailMailbox, cena.geral.id)
    geral.last_seen_at = datetime.now(UTC)
    geral.last_sync_at = datetime.now(UTC) - timedelta(minutes=50)
    await db.commit()
    [lp] = await _emails_parados(db)
    assert lp.loja == "Geral — Tuta" and lp.motivo == saude.MOTIVO_SEM_LEITURA
    assert lp.minutos >= 49 and lp.limite_min == 30 and not lp.nunca_leu
    # O /v2/sync do nosso conector (a cada volta que leu as pastas) é leitura.
    db.add(MailAgenteV2(mailbox_id=cena.geral.id, instancia="i1", visto_em=datetime.now(UTC)))
    await db.commit()
    assert await _emails_parados(db) == []


async def test_s5_caixa_que_nunca_leu_acende_depois_de_30_min(db, cena, redis_falso):
    geral = await db.get(MailMailbox, cena.geral.id)
    geral.created_at = datetime.now(UTC) - timedelta(hours=1)
    geral.last_sync_at = None
    await db.commit()
    [lp] = await _emails_parados(db)
    assert lp.motivo == saude.MOTIVO_SEM_LEITURA and lp.nunca_leu


async def _parado_na_central(db, cena, minutos: int) -> UUID:
    """Um e-mail que chegou à Central há `minutos` e a ponte não levou (sem meta)."""
    mid = await entrar(db, cena.geral, recebido_em=datetime.now(UTC) - timedelta(minutes=minutos))
    message = await db.get(MailMessage, mid)
    message.created_at = datetime.now(UTC) - timedelta(minutes=minutos)
    geral = await db.get(MailMailbox, cena.geral.id)
    geral.last_sync_at = datetime.now(UTC)
    await db.commit()
    return mid


async def test_s5_ponte_parada_com_email_esperando_acende(db, cena, redis_falso):
    await _parado_na_central(db, cena, 20)
    # Sem carimbo da ponte (nunca girou): acende, com a ação do servidor.
    [lp] = await _emails_parados(db)
    assert lp.motivo == saude.MOTIVO_PONTE and lp.acao == saude.ACAO_PONTE
    assert lp.limite_min == 15 and lp.minutos >= 19
    # A ponte girou há 2 min: o e-mail espera de propósito (o Gmail, o recibo).
    await saude.carimbar_ponte()
    assert await _emails_parados(db) == []
    # A última volta foi há 40 min: parada.
    redis_falso.valores[saude.CHAVE_PONTE_OK] = str(
        int((datetime.now(UTC) - timedelta(minutes=40)).timestamp())
    )
    assert [lp.motivo for lp in await _emails_parados(db)] == [saude.MOTIVO_PONTE]
    # A ponte levou o e-mail: apaga.
    await rodar(db)
    assert await _emails_parados(db) == []


async def test_s5_email_recente_ou_meta_em_novo(db, cena, redis_falso):
    # Chegou há 5 min: ainda não é parado.
    mid = await _parado_na_central(db, cena, 5)
    assert await _emails_parados(db) == []
    # A meta presa em `novo` (a volta que reservou e não terminou) há 20 min: conta.
    message = await db.get(MailMessage, mid)
    message.created_at = datetime.now(UTC) - timedelta(minutes=20)
    db.add(MailMessageMeta(message_id=mid, mailbox_id=cena.geral.id, estado="novo"))
    await db.commit()
    assert [lp.motivo for lp in await _emails_parados(db)] == [saude.MOTIVO_PONTE]


async def test_s5_sem_redis_nao_quebra_a_faixa(db, cena, monkeypatch):
    monkeypatch.setattr(saude, "redis", _RedisFora())
    await _parado_na_central(db, cena, 20)
    await saude.carimbar_ponte()  # nunca levanta
    assert [lp.motivo for lp in await _emails_parados(db)] == [saude.MOTIVO_PONTE]


async def test_s5_a_privada_nunca_entra_na_faixa(db, cena, redis_falso):
    goslin = await db.get(MailMailbox, cena.goslin.id)
    goslin.last_sync_at = datetime.now(UTC) - timedelta(hours=5)
    await db.commit()
    assert await _emails_parados(db) == []


async def test_s5_worker_carimba_a_volta_da_ponte(db, cena, redis_falso, monkeypatch):
    from contextlib import asynccontextmanager

    from app import worker

    @asynccontextmanager
    async def _sessao():
        yield db

    monkeypatch.setattr(worker, "session_scope", _sessao)

    class _Trava:
        async def set(self, *_a, **_k):
            return True

        async def delete(self, *_a, **_k):
            return 1

    import app.redis_client as rc

    monkeypatch.setattr(rc, "redis", _Trava())
    assert saude.CHAVE_PONTE_OK not in redis_falso.valores
    resumo = await worker.mail_ponte({})
    assert resumo is not None and "processados" in resumo
    assert saude.CHAVE_PONTE_OK in redis_falso.valores
    assert await saude.ultima_volta_da_ponte() is not None


# ─────────────── e o resto continua igual ───────────────


async def test_formulario_sem_campos_continua_como_antes(db, cena):
    mid = await _formulario(db, cena, "US-26-0999", "E-mail: maria@gmail.com\nMensagem: oi")
    await rodar(db)
    c = await conversa(db, (await meta_de(db, mid)).conversa_id)
    assert c.comprador_id == "maria@gmail.com" and c.comprador_nome is None
    assert "telefone" not in c.dados["mail"] and "alertas" not in c.dados["mail"]
    assert await contar(db, AtendimentoConversa) == 1


# ─────────────── as correções da revisão (09/10/2026) ───────────────


async def _direto(db, cena, de: str, protocolo: str, corpo: str = "E o meu pedido?") -> UUID:
    """O e-mail que a pessoa escreve DIRETO (sem fio nosso) com o protocolo no assunto."""
    return await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        de=de,
        de_nome=de.split("@")[0],
        para=["sac@uranyx.com.br"],
        assunto=f"Re: [{protocolo}] Dúvida",
        corpo=corpo,
        # Depois do formulário (o e-mail que a resposta padrão responde).
        recebido_em=datetime.now(UTC) - timedelta(minutes=1),
    )


async def test_s2_email_direto_da_segunda_cliente_vai_para_a_conversa_dela(db, cena, client, admin):
    """O número repetido de duas clientes: o e-mail direto da 2ª (respondendo a
    confirmação do site, sem fio nosso) cai na conversa DELA, nunca na da 1ª."""
    a = await _formulario(db, cena, "US-26-0500", "Nome: Maria\nE-mail: maria@gmail.com")
    await rodar(db)
    b = await _formulario(db, cena, "US-26-0500", "Nome: Joana\nE-mail: joana@gmail.com")
    await rodar(db)
    ma, mb = await meta_de(db, a), await meta_de(db, b)
    direto = await _direto(db, cena, "joana@gmail.com", "US-26-0500")
    await rodar(db)
    md = await meta_de(db, direto)
    assert md.conversa_id == mb.conversa_id and md.vinculado_por == "protocolo"
    # E a resposta da conversa da Maria continua indo para a Maria.
    await _ligar_envio(db, cena.geral)
    previa = (await client.get(f"{EMAIL}/conversas/{ma.conversa_id}/previa")).json()
    assert previa["para"] == "maria@gmail.com"
    assert not [b for b in previa["bloqueios"] if b["codigo"] == "para_fora_do_chamado"]


async def test_s2_email_direto_de_estranho_avisa_e_a_resposta_so_sai_confirmando(
    db, cena, client, admin
):
    """Outro endereço escreve com o protocolo (adivinhável) de UMA cliente: entra
    no chamado dela (o único do número), a faixa avisa e a resposta para ele só
    sai com a pessoa confirmando."""
    a = await _formulario(
        db, cena, "US-26-0931", "Nome: Ana Lima\nE-mail: ana@gmail.com\nTelefone: 11911112222"
    )
    await rodar(db)
    estranho = await _direto(db, cena, "estranho@gmail.com", "US-26-0931")
    await rodar(db)
    ma, me = await meta_de(db, a), await meta_de(db, estranho)
    assert me.conversa_id == ma.conversa_id
    r = (await client.get(f"{EMAIL}/conversas/{ma.conversa_id}/emails")).json()
    assert [x["codigo"] for x in r["chamado"]["alertas"]] == ["outro_remetente"]
    await _ligar_envio(db, cena.geral)
    previa = (await client.get(f"{EMAIL}/conversas/{ma.conversa_id}/previa")).json()
    assert previa["para"] == "estranho@gmail.com"
    [trava] = [b for b in previa["bloqueios"] if b["codigo"] == "para_fora_do_chamado"]
    assert trava["confirmavel"] and "Ana Lima · ana@gmail.com" in trava["texto"]
    r = await _responder(client, ma.conversa_id, texto="Seu chamado {protocolo}.")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "para_fora_do_chamado"
    assert await _jobs(db) == []
    # Confirmando ("enviar mesmo assim"), sai.
    r = await _responder(
        client, ma.conversa_id, texto="Seu chamado {protocolo}.", confirmar_nao_responde=True
    )
    assert r.status_code == 200, r.text
    assert len(await _jobs(db)) == 1


async def test_s2_email_direto_com_o_numero_de_duas_clientes_abre_conversa_propria(db, cena):
    a = await _formulario(db, cena, "US-26-0540", "Nome: Maria\nE-mail: maria@gmail.com")
    await rodar(db)
    b = await _formulario(db, cena, "US-26-0540", "Nome: Joana\nE-mail: joana@gmail.com")
    await rodar(db)
    estranho = await _direto(db, cena, "estranho@gmail.com", "US-26-0540")
    await rodar(db)
    ma, mb, me = await meta_de(db, a), await meta_de(db, b), await meta_de(db, estranho)
    assert me.conversa_id not in (ma.conversa_id, mb.conversa_id)
    c = await conversa(db, me.conversa_id)
    assert c.externo_id.startswith("mail-protocolo:US-26-0540:")
    assert c.comprador_id == "estranho@gmail.com"
    assert c.dados["mail"]["alertas"] == ["protocolo_repetido"]
    assert "protocolo_repetido" in _codigos(me)


@pytest.mark.parametrize(
    ("corpo", "mesma"),
    [
        # Outro telefone: outra cliente (conversa própria, com o alerta).
        ("Nome: Bruno Costa\nTelefone: 21999990000", False),
        # Sem telefone, outro nome: outra cliente.
        ("Nome: Bruno Costa\nMensagem: oi", False),
        # O mesmo telefone (a cliente que esqueceu o e-mail): a mesma conversa.
        ("Nome: Ana\nTelefone: (11) 91111-2222", True),
        # Nada para comparar: vale o protocolo (como antes).
        ("Mensagem: sou eu de novo", True),
    ],
)
async def test_s2_formulario_sem_email_desempata_pelo_telefone_e_pelo_nome(db, cena, corpo, mesma):
    a = await _formulario(
        db, cena, "US-26-0510", "Nome: Ana Lima\nE-mail: ana@gmail.com\nTelefone: 11911112222"
    )
    await rodar(db)
    b = await _formulario(db, cena, "US-26-0510", corpo)
    await rodar(db)
    ma, mb = await meta_de(db, a), await meta_de(db, b)
    if mesma:
        assert mb.conversa_id == ma.conversa_id and "protocolo_repetido" not in _codigos(mb)
        return
    assert mb.conversa_id != ma.conversa_id
    assert "protocolo_repetido" in _codigos(mb)
    cb = await conversa(db, mb.conversa_id)
    assert cb.comprador_nome == "Bruno Costa" and cb.comprador_id is None
    assert cb.dados["mail"]["alertas"] == ["protocolo_repetido"]
    # A conversa da Ana continua só dela.
    ca = await conversa(db, ma.conversa_id)
    assert ca.comprador_nome == "Ana Lima" and "alertas" not in ca.dados["mail"]


async def test_s4_pedido_ou_telefone_com_nomes_diferentes_nunca_e_a_mesma_cliente(
    db, cena, client, admin
):
    a = await _formulario(
        db,
        cena,
        "US-26-0951",
        "Nome: Ana Lima\nE-mail: ana@gmail.com\nTelefone: 11911112222\nPedido: 445566",
        recebido_em=datetime.now(UTC) - timedelta(hours=2),
    )
    b = await _formulario(
        db,
        cena,
        "US-26-0952",
        "Nome: Bruno Costa\nE-mail: bruno@gmail.com\nTelefone: 11911112222\nPedido: 445566",
    )
    await rodar(db)
    ma, mb = await meta_de(db, a), await meta_de(db, b)
    for lado in (ma.conversa_id, mb.conversa_id):
        r = (await client.get(f"{EMAIL}/conversas/{lado}/emails")).json()
        assert r["outros_chamados"] == [], lado
    r = await client.post(
        f"{EMAIL}/conversas/{mb.conversa_id}/agrupar", json={"conversa_id": str(ma.conversa_id)}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "outra_cliente_ou_marca"
    cb = await conversa(db, mb.conversa_id)
    assert cb.situacao != "fechada" and "clientes" not in cb.dados["mail"]


async def test_s4_sugestao_por_pedido_mostra_o_nome_e_que_o_email_e_outro(db, cena, client, admin):
    await _formulario(
        db, cena, "US-26-0961", "Nome: Maria Souza\nE-mail: maria@gmail.com\nPedido: 99887"
    )
    b = await _formulario(
        db, cena, "US-26-0962", "Nome: MARIA A. SOUZA\nE-mail: m.souza@empresa.com\nPedido: 99887"
    )
    await rodar(db)
    mb = await meta_de(db, b)
    r = (await client.get(f"{EMAIL}/conversas/{mb.conversa_id}/emails")).json()
    [sug] = r["outros_chamados"]
    assert sug["motivo"] == "pedido" and sug["cliente_nome"] == "Maria Souza"
    assert sug["motivo_rotulo"] == "mesmo nº de pedido, e-mail diferente"


async def test_s4_pedido_e_telefone_de_enfeite_nao_juntam_duas_clientes(db, cena, client, admin):
    corpo = "E-mail: {}\nTelefone: (00) 00000-0000\nPedido: 000"
    a = await _formulario(db, cena, "US-26-0520", corpo.format("maria@gmail.com"))
    b = await _formulario(db, cena, "US-26-0521", corpo.format("joana@gmail.com"))
    c = await _formulario(
        db, cena, "US-26-0522", "E-mail: zeca@gmail.com\nTelefone: 1111111111\nPedido: 00000"
    )
    await rodar(db)
    for mid in (a, b, c):
        m = await meta_de(db, mid)
        conv = await conversa(db, m.conversa_id)
        assert "telefone" not in conv.dados["mail"] and "pedido_citado" not in conv.dados["mail"]
        r = (await client.get(f"{EMAIL}/conversas/{m.conversa_id}/emails")).json()
        assert r["outros_chamados"] == []
    ma, mb = await meta_de(db, a), await meta_de(db, b)
    r = await client.post(
        f"{EMAIL}/conversas/{mb.conversa_id}/agrupar", json={"conversa_id": str(ma.conversa_id)}
    )
    assert r.status_code == 409


def test_campos_do_formulario_ramal_e_rotulos_dentro_da_mensagem():
    # O ramal (e o 2º número) não entram no telefone.
    c = enderecos.campos_do_formulario("Telefone: +55 (11) 4002-8922 ramal 3")
    assert c["telefone"] == "1140028922"
    c = enderecos.campos_do_formulario("Telefone: (11) 98765-4321 / (11) 3456-7890")
    assert c["telefone"] == "11987654321"
    # O rótulo no começo da linha ganha do "nome:" escrito dentro da mensagem.
    assert enderecos.campos_do_formulario("Mensagem: meu nome: zé\nNome: Ana")["nome"] == "Ana"
    # O "pedido:" dentro da mensagem nunca vira pedido citado.
    c = enderecos.campos_do_formulario("Nome: Ana\nMensagem: o pedido: 99999 chegou quebrado")
    assert c == {"nome": "Ana", "telefone": None, "pedido": None}
    # O HTML que virou uma linha: o que vem depois da "Mensagem:" é da cliente.
    c = enderecos.campos_do_formulario(
        "Nome: Ana Lima E-mail: a@x.com Pedido: 445566 Mensagem: meu nome: zé pedido: 55555"
    )
    assert (c["nome"], c["pedido"]) == ("Ana Lima", "445566")
    # Enfeite: pedido curto ou de dígitos iguais; telefone de zeros ou de DDD 0x.
    for pedido_falso in ("000", "1234", "00000", "99999-9"):
        assert enderecos.campos_do_formulario(f"Pedido: {pedido_falso}")["pedido"] is None
    for tel in ("(00) 00000-0000", "0000000000", "1111111111", "(10) 98765-4321"):
        assert enderecos.telefone_digitos(tel) is None, tel
    assert enderecos.mesmo_nome("Ana Lima", "Bruno Costa") is False
    assert enderecos.mesmo_nome("Maria Souza", "MARIA APARECIDA SOUZA") is True
    assert enderecos.mesmo_nome(None, "Ana") is None


def test_s6_formato_errado_nao_pega_cupom_nem_nota_fiscal():
    for assunto in (
        "Dúvida sobre o cupom BF-25-10",
        "NF-1-123 enviada",
        "Re: Pedido ABC-12-345 oi",
    ):
        assert pedido.protocolo_fora_do_formato(assunto, "") is False, assunto
    # Entre colchetes, qualquer letra: é o site mandando o protocolo errado.
    assert pedido.protocolo_fora_do_formato("[XX-26-0001] Oi", "") is True
    assert pedido.protocolo_fora_do_formato("UX-26-0001 troca", "") is True


async def test_s9_status_com_a_resposta_ainda_na_fila(db, cena, client, admin):
    mid = await _formulario(db, cena, "US-26-0911", "E-mail: maria@gmail.com")
    await rodar(db)
    conversa_id = (await meta_de(db, mid)).conversa_id
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id, texto="Já vamos ver.")
    assert r.status_code == 200, r.text
    chamado = (await client.get(f"{EMAIL}/conversas/{conversa_id}/emails")).json()["chamado"]
    assert chamado["status"] == "aguardando_cliente"
    assert chamado["status_rotulo"] == "Aguardando cliente (resposta na fila)"


async def test_pedido_sugerido_nao_sai_para_quem_tem_escopo_restrito(db, cena):
    from app.deps.team_scope import TeamScope

    mid = await _formulario(
        db, cena, "US-26-0921", "Nome: Ana\nE-mail: ana@gmail.com\nPedido: 778899"
    )
    db.add(BlingOrder(numero="66001", numeroloja="778899", item_index=0))
    await db.commit()
    await rodar(db)
    c = await conversa(db, (await meta_de(db, mid)).conversa_id)
    assert (await chamados.pedido_sugerido(db, c, TeamScope(unrestricted=True)))["achado"] is True
    assert await chamados.pedido_sugerido(db, c, TeamScope(unrestricted=False)) is None
