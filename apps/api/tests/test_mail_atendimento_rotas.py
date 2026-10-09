"""As rotas do e-mail no /atendimento (/api/atendimento/email/*) e QUEM VÊ O QUÊ (08/10/2026).

A regra (a crítica de 08/10): a caixa inteira continua do dono e dos admins
(a Central); a equipe vê pelo /atendimento só o que a ponte levou, na conversa
e no escopo da equipe; as FILAS (sem loja, sem vínculo, suspeitos, resumos),
as pastas e as regras só quem MEXE. O e-mail privado, o de segurança e o da
pasta que só se conta nunca aparecem aqui; a caixa privada não entra na
saúde de quem não é dono nem admin, nem na faixa vermelha que todos veem.
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
    MailAttachment,
    MailMailbox,
    StoreInfo,
    UserRole,
)
from app.models.mail_atendimento import MailFolder, MailMailboxSettings
from app.services import vigia_leitura_atendimento
from tests.test_mail_ponte import (
    GOSLIN,
    _loja,
    entrar,
    meta_de,
    pedido_existe,
    rodar,
)
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte

EMAIL = "/api/atendimento/email"
OUTRO = "quem-mexe@davinci-test.com"


@pytest.fixture
async def admin(cena, auth_as):
    auth_as(cena.dono)
    return cena.dono


@pytest.fixture
async def quem_le(make_user, auth_as, monkeypatch):
    """Pessoa ativa do DaVinci, sem equipe: LÊ o /atendimento (não mexe)."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", OUTRO)
    user = await make_user(role=UserRole.USER)
    auth_as(user)
    return user


async def _mundo_de_emails(db, cena) -> dict[str, UUID]:
    """Um de cada: gravado, sem loja, segurança, privado, só contar, suspeito."""
    ids = {
        "gravado": await entrar(
            db,
            cena.geral,
            assunto="Troca",
            corpo="Quero trocar. Entre: https://x.com/login?token=abc",
            anexos=[("foto.jpg", "image/jpeg", b"\xff\xd8\xffJPEG")],
        ),
        "sem_loja": await entrar(db, cena.geral, para=["hans21@tuta.com"], assunto="Oi loja"),
        "seguranca": await entrar(
            db, cena.geral, assunto="Seu código de verificação", corpo="Código: 482913"
        ),
        "privado": await entrar(db, cena.goslin, pasta="INBOX", para=[GOSLIN], assunto="Boleto"),
        "so_contar": await entrar(db, cena.geral, pasta="contabilidade", assunto="DRE"),
        "suspeito": await entrar(
            db, cena.geral, de="suporte@mercadolivre-br.com", de_nome="Mercado Livre"
        ),
    }
    await rodar(db)
    return ids


# ─────────────── as filas: só quem mexe ───────────────


async def test_filas_so_para_quem_mexe(db, cena, client, quem_le):
    await _mundo_de_emails(db, cena)
    for rota in ("/filas", "/emails?fila=sem_loja", "/pastas", "/regras", "/envios"):
        r = await client.get(f"{EMAIL}{rota}")
        assert r.status_code == 403, rota
        assert r.json()["detail"]["code"] == "atendimento_so_quem_mexe"


async def test_filas_e_o_que_nunca_aparece(db, cena, client, admin):
    ids = await _mundo_de_emails(db, cena)
    filas = (await client.get(f"{EMAIL}/filas")).json()
    assert filas["sem_loja"] == 1 and filas["suspeito"] == 1 and filas["erro"] == 0
    itens = (await client.get(f"{EMAIL}/emails?fila=sem_loja")).json()["itens"]
    assert [i["id"] for i in itens] == [str(ids["sem_loja"])]
    assert itens[0]["assunto"] == "Oi loja" and "texto" not in itens[0]
    # Segurança, privado e só contar: nem pelo id.
    for chave in ("seguranca", "privado", "so_contar"):
        r = await client.get(f"{EMAIL}/emails/{ids[chave]}")
        assert r.status_code == 404, chave
    detalhe = (await client.get(f"{EMAIL}/emails/{ids['gravado']}")).json()
    assert "token=abc" not in detalhe["texto"] and "[link de acesso removido]" in detalhe["texto"]
    assert detalhe["abrir_no_tuta"].startswith("https://app.tuta.com/mail?mail=")


async def test_escolher_a_loja_leva_o_email_para_a_conversa(db, cena, client, admin):
    ids = await _mundo_de_emails(db, cena)
    r = await client.post(
        f"{EMAIL}/emails/{ids['sem_loja']}/loja", json={"store_info_id": str(cena.loja_ml.id)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "sem_vinculo" and r.json()["vinculado_por"] == "manual"
    meta = await meta_de(db, ids["sem_loja"])
    c = await db.get(AtendimentoConversa, meta.conversa_id)
    assert c.integration_id == cena.ml.id
    # De novo: já não está sem loja.
    r = await client.post(
        f"{EMAIL}/emails/{ids['sem_loja']}/loja", json={"store_info_id": str(cena.loja_ml.id)}
    )
    assert r.status_code == 404


async def test_escolher_ficha_sem_integracao_e_a_marca_ambigua(db, cena, client, admin):
    """A ficha SEM integração (Temu, a loja da Goslin) é escolhível: o e-mail
    entra na conversa dela; a marca ambígua (domínio em duas marcas) também."""
    # A ficha da Temu sem e-mail no cadastro: o e-mail para hans21 cai em "sem loja".
    temu = await _loja(db, cena.dono, "temu", None, None, "Barbosa Temu")
    await db.commit()
    mid = await entrar(db, cena.geral, para=["hans21@tuta.com"], pasta="INBOX", assunto="Oi")
    await rodar(db)
    assert (await meta_de(db, mid)).estado == "sem_loja"
    r = await client.post(f"{EMAIL}/emails/{mid}/loja", json={"store_info_id": str(temu.id)})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["estado"] == "sem_vinculo" and corpo["sem_integracao"] is True
    assert corpo["loja"] == "Barbosa Temu" and corpo["integration_id"] is None
    meta = await meta_de(db, mid)
    c = await db.get(AtendimentoConversa, meta.conversa_id)
    assert (c.integration_id, c.plataforma, c.dados["mail"]["store_info_id"]) == (
        None,
        "temu",
        str(temu.id),
    )
    # A marca do site escolhida por pessoa.
    outro = await entrar(db, cena.geral, para=["hans21@tuta.com"], pasta="INBOX", assunto="Oi 2")
    await rodar(db)
    r = await client.post(f"{EMAIL}/emails/{outro}/loja", json={"marca_id": str(cena.marca.id)})
    assert r.status_code == 200, r.text
    c2 = await db.get(AtendimentoConversa, (await meta_de(db, outro)).conversa_id)
    assert (c2.plataforma, c2.dados["mail"]["marca"]) == ("site", "uranyx")


async def test_vincular_ao_pedido_e_ignorar(db, cena, client, admin):
    ids = await _mundo_de_emails(db, cena)
    await pedido_existe(db, cena.ml, "2000012345678901")
    r = await client.post(
        f"{EMAIL}/emails/{ids['suspeito']}/vincular", json={"pedido": "2000012345678901"}
    )
    assert r.status_code == 200, r.text
    meta = await meta_de(db, ids["suspeito"])
    assert (meta.estado, meta.vinculado_por) == ("gravado", "manual")
    c = await db.get(AtendimentoConversa, meta.conversa_id)
    await db.refresh(c)
    assert c.pedido_marketplace == "2000012345678901"
    r = await client.post(f"{EMAIL}/emails/{ids['sem_loja']}/ignorar")
    assert r.status_code == 200 and r.json()["estado"] == "ignorado"
    assert (await client.get(f"{EMAIL}/filas")).json()["sem_loja"] == 0


# ─────────────── na conversa: quem vê, no escopo da equipe ───────────────


async def test_cartoes_da_conversa_para_quem_le(db, cena, client, quem_le):
    ids = await _mundo_de_emails(db, cena)
    meta = await meta_de(db, ids["gravado"])
    r = await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")
    assert r.status_code == 200, r.text
    [cartao] = r.json()["emails"]
    assert cartao["alias"] == "21max@tuta.com" and cartao["pasta"] == "problema ml"
    assert "token=abc" not in cartao["texto"] and cartao["links_removidos"] == 1
    [anexo] = cartao["anexos"]
    assert anexo["filename"] == "foto.jpg"
    r = await client.get(f"{EMAIL}/anexos/{anexo['id']}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert r.content == b"\xff\xd8\xffJPEG"
    # A mensagem da conversa leva o resumo do e-mail (sem o texto do cartão).
    r = await client.get(f"/api/atendimento/conversas/{meta.conversa_id}")
    assert r.status_code == 200, r.text
    [msg] = r.json()["mensagens"]
    assert msg["email"]["tipo"] == "recebido" and msg["email"]["message_id"] == str(ids["gravado"])


async def test_equipe_de_outra_loja_nao_ve_o_email(db, cena, client, make_user, auth_as):
    ids = await _mundo_de_emails(db, cena)
    meta = await meta_de(db, ids["gravado"])
    outra = await _loja(db, cena.dono, "shopee", "zz99", None, "Outra")
    outra.sales_team = 7
    await db.commit()
    user = await make_user(role=UserRole.USER)
    user.sales_teams = [7]
    await db.commit()
    auth_as(user)
    assert (await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")).status_code == 404
    anexo = await db.scalar(
        select(MailAttachment).where(MailAttachment.message_id == ids["gravado"])
    )
    assert (await client.get(f"{EMAIL}/anexos/{anexo.id}")).status_code == 404


async def test_anexo_de_email_privado_ou_sem_conversa_nao_baixa(db, cena, client, quem_le):
    privado = await entrar(
        db,
        cena.goslin,
        pasta="INBOX",
        para=[GOSLIN],
        anexos=[("extrato.pdf", "application/pdf", b"%PDF")],
    )
    sem_loja = await entrar(
        db,
        cena.geral,
        para=["hans21@tuta.com"],
        anexos=[("contrato.pdf", "application/pdf", b"%PDF")],
    )
    await rodar(db)
    for mid in (privado, sem_loja):
        anexo = await db.scalar(select(MailAttachment).where(MailAttachment.message_id == mid))
        assert (await client.get(f"{EMAIL}/anexos/{anexo.id}")).status_code == 404


# ─────────────── RF6: agrupar chamados da mesma cliente ───────────────


async def test_rf6_sugere_e_agrupa_chamados_da_mesma_cliente(db, cena, client, admin):
    ids = []
    for protocolo in ("US-26-0001", "US-26-0002"):
        ids.append(
            await entrar(
                db,
                cena.geral,
                pasta="*uranyx sac",
                de="sac@uranyx.com.br",
                para=["sac@uranyx.com.br"],
                assunto=f"[{protocolo}] Dúvida",
                corpo="E-mail: maria@gmail.com\nMensagem: oi",
            )
        )
    await rodar(db)
    a, b = await meta_de(db, ids[0]), await meta_de(db, ids[1])
    assert a.conversa_id != b.conversa_id
    r = (await client.get(f"{EMAIL}/conversas/{b.conversa_id}/emails")).json()
    assert r["chamado"]["protocolo"] == "US-26-0002" and r["chamado"]["tipo_rotulo"] == "SAC"
    assert [o["protocolo"] for o in r["outros_chamados"]] == ["US-26-0001"]
    r = await client.post(
        f"{EMAIL}/conversas/{b.conversa_id}/agrupar", json={"conversa_id": str(a.conversa_id)}
    )
    assert r.status_code == 200, r.text
    destino = await db.get(AtendimentoConversa, a.conversa_id)
    origem = await db.get(AtendimentoConversa, b.conversa_id)
    await db.refresh(destino)
    await db.refresh(origem)
    assert origem.situacao == "fechada" and origem.dados["mail"]["agrupado_em"] == str(destino.id)
    assert destino.dados["mail"]["protocolos"] == ["US-26-0001", "US-26-0002"]
    msgs = (
        (
            await db.execute(
                select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == destino.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(msgs) == 3  # os dois formulários + a nota de quem agrupou
    assert (await meta_de(db, ids[1])).conversa_id == destino.id


async def test_rf6_nao_agrupa_outra_cliente(db, cena, client, admin):
    ids = []
    for n, cliente in enumerate(("maria@gmail.com", "joao@gmail.com")):
        ids.append(
            await entrar(
                db,
                cena.geral,
                pasta="*uranyx sac",
                de="sac@uranyx.com.br",
                para=["sac@uranyx.com.br"],
                assunto=f"[US-26-001{n}] Dúvida",
                corpo=f"E-mail: {cliente}",
            )
        )
    await rodar(db)
    a, b = await meta_de(db, ids[0]), await meta_de(db, ids[1])
    r = await client.post(
        f"{EMAIL}/conversas/{b.conversa_id}/agrupar", json={"conversa_id": str(a.conversa_id)}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "outra_cliente_ou_marca"


# ─────────────── saúde e a faixa "lojas sem ler" ───────────────


async def test_saude_caixa_privada_so_para_o_dono_e_admin(db, cena, client, quem_le, auth_as):
    r = (await client.get(f"{EMAIL}/saude")).json()
    assert [c["nome"] for c in r["caixas"]] == ["Geral — Tuta"]
    assert "lojas_sem_caixa_lida" not in r
    auth_as(cena.dono)
    r = (await client.get(f"{EMAIL}/saude")).json()
    assert sorted(c["nome"] for c in r["caixas"]) == ["Geral — Tuta", "Goslin — Tuta"]


async def test_saude_lojas_sem_caixa_lida_e_cadastro_incompleto(db, cena, client, admin):
    await _loja(db, cena.dono, "mercadolivre", "zz99", None, "Sem caixa")
    await _loja(db, cena.dono, "shopee", "sac@locagil", None, "Agil")
    await _loja(db, cena.dono, "mercadolivre", "sac@uranyx", cena.ml, "VR")
    await _loja(db, cena.dono, "shopee", "sac @ lixo", None, "Torto")
    await db.commit()
    r = (await client.get(f"{EMAIL}/saude")).json()
    fora = {lj["nome"]: lj["endereco"] for lj in r["lojas_sem_caixa_lida"]}
    assert "Sem caixa" in fora and "Barbosa" not in fora and "Oliveira" not in fora
    # "sac@uranyx" casa com sac@uranyx.com.br, que a geral lê; "sac@locagil" não
    # tem endereço em caixa nenhuma com ponte.
    assert "VR" not in fora and fora["Agil"] == "sac@locagil"
    assert [c["nome"] for c in r["cadastro_incompleto"]] == ["Torto"]
    # As fichas sem integração (nem FK nem par nome × plataforma): o dono liga.
    sem = {lj["nome"] for lj in r["lojas_sem_integracao"]}
    assert {"JLAS2", "Sem caixa", "Agil"} <= sem and "Barbosa" not in sem and "VR" not in sem


async def test_faixa_vermelha_so_com_caixa_da_empresa_parada(db, cena):
    for caixa in (cena.geral, cena.goslin):
        linha = await db.get(MailMailbox, caixa.id)
        linha.last_seen_at = datetime.now(UTC) - timedelta(hours=2)
    await db.commit()
    linhas = await vigia_leitura_atendimento.leitura_parada(db)
    emails = [lp for lp in linhas if lp.plataforma == "email"]
    assert [lp.loja for lp in emails] == ["Geral — Tuta"]
    assert emails[0].chave == f"mail:{cena.geral.id}" and emails[0].minutos >= 119
    # Ponte desligada (a caixa ainda nem leva nada à equipe): não acende.
    cfg = await db.get(MailMailboxSettings, cena.geral.id)
    cfg.ponte_ligada = False
    await db.commit()
    linhas = await vigia_leitura_atendimento.leitura_parada(db)
    assert not [lp for lp in linhas if lp.plataforma == "email"]


# ─────────────── pastas e regras ───────────────


async def test_pasta_e_regra_mudam_o_que_entra(db, cena, client, admin):
    await _mundo_de_emails(db, cena)
    pastas = (await client.get(f"{EMAIL}/pastas")).json()["itens"]
    contab = next(p for p in pastas if p["nome"] == "contabilidade")
    assert contab["ler"] == "so_contar" and contab["revisada"] is False
    r = await client.patch(f"{EMAIL}/pastas/{contab['id']}", json={"ignorar": True})
    assert r.status_code == 200 and r.json()["ler"] == "nao"
    # Nova plataforma = nova palavra (sem mexer no código).
    r = await client.put(
        f"{EMAIL}/regras", json={"tipo": "plataforma", "palavra": "Mercado", "valor": "ml"}
    )
    assert r.status_code == 200, r.text
    regras = (await client.get(f"{EMAIL}/regras")).json()["itens"]
    assert len(regras) == 25 and any(x["palavra"] == "mercado" for x in regras)
    nova = await entrar(db, cena.geral, pasta="mensagens mercado")
    await rodar(db)
    assert (await meta_de(db, nova)).plataforma == "ml"
    pasta = await db.scalar(select(MailFolder).where(MailFolder.chave == "mensagens mercado"))
    assert pasta.ler == "corpo"


# ─────────────── reprocessar ───────────────


async def test_reprocessar_a_fila_depois_do_cadastro(db, cena, client, admin):
    ids = await _mundo_de_emails(db, cena)
    loja = await db.scalar(select(StoreInfo).where(StoreInfo.email == "16tr"))
    loja.email = "hans21"
    loja.integration_id = cena.shopee.id
    loja.platform = "mercadolivre"
    await db.commit()
    # A ficha "hans21" agora é de uma loja (ML) com integração: o e-mail sai da fila.
    loja.integration_id = cena.ml.id
    await db.commit()
    r = await client.post(f"{EMAIL}/reprocessar")
    assert r.status_code == 200 and r.json() == {"total": 1, "resolvidos": 1}
    assert (await meta_de(db, ids["sem_loja"])).estado in ("sem_vinculo", "gravado")
