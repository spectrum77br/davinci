"""Abrir o atendimento do Melhor Envio pela Logística (18/11/2026, passo 5).

Vinicius: "nos Amazon envio próprio … a gente clica, Abrir chamado, vem os motivos
do Melhor Envio e a gente abre o chamado". O ME de mentira abaixo devolve o que a
tela dele recebe (lido no dash.js e na janela "Iniciar atendimento" em 18/11):
motivos `{id, name, slug}`, a consulta `ticket_details`/`order_details`/
`configs_type.current_status` e, na abertura, `{"ticket": {"number": …}}`.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoMensagem, Logistica, User, UserRole, UserStatus
from app.services import chamados_melhor_envio as cme
from app.services import chamados_melhor_envio_abrir as abrir
from app.services import melhor_envio as me

RASTREIO = "AE144386427BR"
ENVIO_ID = "a3353e9f-08d2-4d64-b238-8d1b4cded00a"
MOTIVOS = [
    {"id": 7, "name": "Alteração de endereço", "slug": "address_change"},
    {"id": 1, "name": "Atraso na entrega", "slug": "delayed_shipping"},
    {"id": 17, "name": "Comprovante de entrega", "slug": "delivery_receipt"},
    {"id": 8, "name": "Entrega não reconhecida", "slug": "confrontation"},
    {"id": 12, "name": "Extravio, Furto ou Roubo", "slug": "loss_theft_robbery"},
    {"id": 4, "name": "Produto avariado", "slug": "damage"},
]


def _config(status: str) -> dict:
    return {
        "order_details": [
            {"key": "shipping_company", "title": "Transportadora", "value": "Correios"},
            {"key": "delivered_at", "title": "Data de entrega", "value": "13/11/2026"},
            {
                "key": "remaining_deadline",
                "title": "Prazo restante para solicitar",
                "value": "25 dias",
            },
        ],
        "configs_type": {
            "type": "confrontation",
            "allows_attachments": True,
            "current_status": status,
        },
        "ticket_details": {
            "title": "Entrega confirmada, mas não recebida",
            "description": (
                "Seu envio consta como entregue, mas o destinatário não recebeu?<br>Vamos resolver."
            ),
            "additional_info": "Caso o destinatário não reconheça quem recebeu o pacote, siga.",
        },
    }


@pytest.fixture
def fake_me(monkeypatch):
    estado = {
        "envios": [
            {
                "id": ENVIO_ID,
                "protocol": "ORD-202611126547000",
                "status": "delivered",
                "tracking": RASTREIO,
                "tags": [{"tag": "308773", "url": "mi:reference_code"}],
                "tickets": [],
                "ticket_opening": {"allowed": True},
            }
        ],
        "config": {"confrontation": _config("confrontation_opening_allowed")},
        "abertos": [],
        "categorias": [],
        "abrir_http": 200,
        "proximo": 2140001,
    }

    async def _token(session):
        return "tok", {}

    async def _envios(token, *, page=1, q=None, extra=None):
        return 200, {"data": [e for e in estado["envios"] if q in (e["tracking"], e["protocol"])]}

    async def _motivos(token, categoria):
        estado["categorias"].append(categoria)
        return 200, list(MOTIVOS)

    async def _config_me(token, tipo, envio_id):
        assert envio_id == ENVIO_ID
        return 200, estado["config"].get(tipo, {"configs_type": {"current_status": ""}})

    async def _abrir(token, *, envio_id, tipo, descricao=None, recebedor=None, anexos=None):
        if estado["abrir_http"] != 200:
            return estado["abrir_http"], {
                "message": "The given data was invalid.",
                "errors": {"description": ["muito curta"]},
            }
        numero = estado["proximo"]
        estado["abertos"].append(
            {
                "envio_id": envio_id,
                "tipo": tipo,
                "descricao": descricao,
                "recebedor": recebedor,
                "anexos": anexos or [],
            }
        )
        return 201, {"ticket": {"number": numero, "status": "Novo"}}

    async def _ticket(token, numero):
        texto = (
            "Nome de quem recebeu o pedido: <br> Não informado <br>"
            "Descrição da ocorrência: <br> Cliente diz que não recebeu."
        )
        return 200, {
            "number": int(numero),
            "status": "Novo",
            "interactions": {
                "comments": [
                    {
                        "description_html": texto,
                        "type_user": "Cliente",
                        "date_creation_raw": "2026-11-18T13:00:00Z",
                        "person": {"name": "CASA E DECORAÇÃO"},
                    }
                ]
            },
        }

    monkeypatch.setattr(me, "token_do_painel", _token)
    monkeypatch.setattr(me, "ler_envios", _envios)
    monkeypatch.setattr(me, "ler_motivos_atendimento", _motivos)
    monkeypatch.setattr(me, "ler_config_atendimento", _config_me)
    monkeypatch.setattr(me, "abrir_atendimento", _abrir)
    monkeypatch.setattr(me, "ler_atendimento", _ticket)
    return estado


def _linha(**kw) -> Logistica:
    base = {
        "data": date(2026, 11, 12),
        "pedido_bling": "308773",
        "pedido_marketplace": "701-3959618-7955435",
        "plataforma": "Amazon",
        "conta": "kia",
        "amazon_canal": "proprio",
        "rastreio": RASTREIO,
    }
    base.update(kw)
    return Logistica(**base)


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        name="Cairo",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ---------------------------------------------------------------- regras puras


def test_como_e_liberado_de_cada_motivo():
    assert abrir.como_do_motivo("confrontation") == abrir.COMO_ACAREACAO
    assert abrir.como_do_motivo("delayed_shipping") == abrir.COMO_ATRASO
    assert abrir.como_do_motivo("damage") == abrir.COMO_SITE
    assert abrir.como_do_motivo("loss_theft_robbery") == abrir.COMO_DESCRICAO
    assert abrir.liberado("confrontation", "confrontation_opening_allowed")
    assert not abrir.liberado("confrontation", "confrontation_blocked_by_48")
    assert not abrir.liberado("delayed_shipping", "shipping_not_delayed")
    assert abrir.liberado("delayed_shipping", "shipping_delayed_late")
    assert abrir.liberado("delivery_receipt", None)
    assert not abrir.liberado("delivery_receipt", "x_blocked_by_status")


def test_categoria_do_envio_igual_a_tela_do_me():
    assert me.categoria_do_envio("released") == me.CATEGORIA_PRE_POSTAGEM
    assert me.categoria_do_envio("delivered") == me.CATEGORIA_POS_POSTAGEM
    assert me.categoria_do_envio(None) == me.CATEGORIA_POS_POSTAGEM


def test_linha_sem_envio_proprio_nao_tem_botao():
    assert abrir.problema_da_linha(_linha()) is None
    assert abrir.problema_da_linha(_linha(amazon_canal="dba"))
    assert abrir.problema_da_linha(_linha(rastreio=None))


def test_campos_seguem_a_tela_do_me():
    ok = {"descricao": "Cliente diz que não recebeu.", "recebedor": "", "anexos": []}
    abrir.validar_campos(abrir.COMO_ACAREACAO, **ok)
    with pytest.raises(abrir.AberturaRecusadaError) as e:
        abrir.validar_campos(abrir.COMO_ACAREACAO, descricao="curta", recebedor="", anexos=[])
    assert e.value.code == "descricao_tamanho"
    with pytest.raises(abrir.AberturaRecusadaError) as e:
        abrir.validar_campos(abrir.COMO_ACAREACAO, **{**ok, "recebedor": "Ana"})
    assert e.value.code == "recebedor_tamanho"
    with pytest.raises(abrir.AberturaRecusadaError) as e:
        abrir.validar_campos(
            abrir.COMO_ACAREACAO, **{**ok, "anexos": [("a.txt", b"x", "text/plain")]}
        )
    assert e.value.code == "anexo_tipo"
    with pytest.raises(abrir.AberturaRecusadaError) as e:
        big = [("f.jpg", b"x" * (abrir.ANEXO_MAX_BYTES + 1), "image/jpeg")]
        abrir.validar_campos(abrir.COMO_ACAREACAO, **{**ok, "anexos": big})
    assert e.value.code == "anexo_grande"
    with pytest.raises(abrir.AberturaRecusadaError) as e:
        abrir.validar_campos(abrir.COMO_SITE, **ok)
    assert e.value.code == "motivo_so_no_site"
    # Atraso e comprovante: sem campo obrigatório; recebedor/anexo só na acareação.
    abrir.validar_campos(abrir.COMO_ATRASO, descricao="", recebedor="", anexos=[])
    with pytest.raises(abrir.AberturaRecusadaError) as e:
        abrir.validar_campos(abrir.COMO_ATRASO, descricao="", recebedor="Fulano de Tal", anexos=[])
    assert e.value.code == "campo_so_na_acareacao"


def test_resumo_da_consulta_tira_o_html():
    r = abrir.resumo_da_consulta("confrontation", _config("confrontation_blocked_by_48"))
    assert r["liberado"] is False
    assert r["titulo"] == "Entrega confirmada, mas não recebida"
    assert "<br>" not in (r["texto"] or "")
    assert {
        "chave": "remaining_deadline",
        "titulo": "Prazo restante para solicitar",
        "valor": "25 dias",
    } in r["detalhes"]
    assert r["aceita_anexos"] is True


# ---------------------------------------------------------------- pela API


async def test_preparar_traz_o_envio_e_os_motivos(client: AsyncClient, db, auth_as, admin, fake_me):
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.get(f"/api/logistica/{row.id}/melhor-envio/abrir")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["envio"]["id"] == ENVIO_ID and corpo["envio"]["rastreio"] == RASTREIO
    assert [m["slug"] for m in corpo["motivos"]] == [m["slug"] for m in MOTIVOS]
    como = {m["slug"]: m["como"] for m in corpo["motivos"]}
    assert como["confrontation"] == "acareacao" and como["damage"] == "site"
    assert fake_me["categorias"] == [me.CATEGORIA_POS_POSTAGEM]

    r = await client.get(f"/api/logistica/{row.id}/melhor-envio/abrir/consulta?tipo=confrontation")
    assert r.status_code == 200 and r.json()["liberado"] is True


async def test_dba_e_envio_ja_com_atendimento_nao_abrem(
    client: AsyncClient, db, auth_as, admin, fake_me
):
    dba = _linha(amazon_canal="dba", pedido_bling="309530")
    db.add(dba)
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.get(f"/api/logistica/{dba.id}/melhor-envio/abrir")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "logistica_sem_melhor_envio"

    fake_me["envios"][0]["tickets"] = [{"number": 2134149, "status": "Novo"}]
    r = await client.get(f"/api/logistica/{row.id}/melhor-envio/abrir")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "melhor_envio_ja_tem_atendimento"
    assert "2134149" in r.json()["detail"]["mensagem"]


async def test_abrir_acareacao_cria_o_chamado_e_a_coluna_fica_aberto(
    client: AsyncClient, db, auth_as, admin, fake_me, monkeypatch
):
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.post(
        f"/api/logistica/{row.id}/melhor-envio/abrir",
        data={
            "tipo": "confrontation",
            "recebedor": "Não informado",
            "descricao": "Bom dia, o cliente informa que não recebeu o produto.",
        },
        files=[("files", ("conversa.png", b"\x89PNG-fake", "image/png"))],
    )
    assert r.status_code == 200, r.text
    linha = r.json()
    assert linha["chamado_aba"]["chamado"] == "2140001"
    assert (
        linha["chamado_aba"]["origem"] == cme.ORIGEM and linha["chamado_aba"]["resolvido"] is False
    )

    [aberto] = fake_me["abertos"]
    assert (aberto["envio_id"], aberto["tipo"], aberto["recebedor"]) == (
        ENVIO_ID,
        "confrontation",
        "Não informado",
    )
    assert aberto["descricao"].startswith("Bom dia")
    assert [(n, t) for n, _, t in aberto["anexos"]] == [("conversa.png", "image/png")]

    ch = (await db.execute(select(Chamado).where(Chamado.origem == cme.ORIGEM))).scalars().one()
    assert (ch.pedido_bling, ch.origem_ref, ch.canal) == ("308773", "2140001", "manual")
    textos = (
        (await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)))
        .scalars()
        .all()
    )
    assert any("aberto no Melhor Envio pela Logística por Cairo" in m.texto for m in textos)
    # a 1ª fala, do jeito que o ME guardou, já está no histórico
    assert any(m.tipo == "abertura" and "Nome de quem recebeu" in m.texto for m in textos)

    # Segunda vez: o pedido já tem chamado vivo.
    r = await client.post(
        f"/api/logistica/{row.id}/melhor-envio/abrir",
        data={"tipo": "loss_theft_robbery", "descricao": "Pacote extraviado nos Correios."},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "chamado_ja_aberto"
    assert len(fake_me["abertos"]) == 1

    # A leitura do :25 encontra o mesmo atendimento e não cria outro.
    async def _lista(token, *, page=1, q=None, extra=None):
        envio = dict(fake_me["envios"][0], tickets=[{"number": 2140001, "status": "Novo"}])
        return 200, {"data": [envio], "last_page": 1}

    monkeypatch.setattr(me, "ler_envios", _lista)
    await cme.sincronizar(db)
    await db.commit()
    assert (
        len((await db.execute(select(Chamado).where(Chamado.origem == cme.ORIGEM))).scalars().all())
        == 1
    )


async def test_acareacao_antes_das_48h_nao_abre(client: AsyncClient, db, auth_as, admin, fake_me):
    fake_me["config"]["confrontation"] = _config("confrontation_blocked_by_48")
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.post(
        f"/api/logistica/{row.id}/melhor-envio/abrir",
        data={"tipo": "confrontation", "descricao": "Cliente diz que não recebeu o produto."},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "melhor_envio_nao_libera"
    assert fake_me["abertos"] == []
    assert (await db.execute(select(Chamado))).scalars().all() == []


async def test_recusa_do_me_aparece_e_nao_cria_chamado(
    client: AsyncClient, db, auth_as, admin, fake_me
):
    fake_me["abrir_http"] = 422
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.post(
        f"/api/logistica/{row.id}/melhor-envio/abrir",
        data={"tipo": "loss_theft_robbery", "descricao": "Pacote extraviado nos Correios."},
    )
    assert r.status_code == 502
    assert "description: muito curta" in r.json()["detail"]["mensagem"]
    assert (await db.execute(select(Chamado))).scalars().all() == []


async def test_motivo_do_site_nao_abre_pelo_davinci(
    client: AsyncClient, db, auth_as, admin, fake_me
):
    row = _linha()
    db.add(row)
    await db.commit()
    auth_as(admin)
    r = await client.post(
        f"/api/logistica/{row.id}/melhor-envio/abrir",
        data={"tipo": "damage", "descricao": "Produto chegou quebrado."},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "motivo_so_no_site"
    assert fake_me["abertos"] == []
