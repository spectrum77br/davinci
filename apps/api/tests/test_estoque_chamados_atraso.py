"""Chamados de ATRASO NA POSTAGEM em lote (Controle de Estoque › Pedidos) —
Eduardo 15/09: um chamado por loja; postado até 60 min depois do corte = fila,
depois disso no mesmo dia = energia; não postado sem etiqueta = problema na
emissão da etiqueta (com etiqueta gerada "trava"); o resto entra desmarcado e
pode virar "outro" com texto livre; ML pelo robô, Shopee na mão."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    BlingEnvioEvento,
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    ChamadoPedido,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.models.nf import NfEtiquetaArquivo
from app.services import chamados_atraso as svc

pytestmark = pytest.mark.asyncio

PERM_EDIT = {
    "controle_estoque": {"view": True, "edit": True, "delete": False},
    "chamados": {"view": True, "edit": True, "delete": False},
}
PERM_VIEW = {
    "controle_estoque": {"view": True, "edit": True, "delete": False},
    "chamados": {"view": True, "edit": False, "delete": False},
}
PREVIEW = "/api/estoque/pedidos/chamados-atraso/preview"
ABRIR = "/api/estoque/pedidos/chamados-atraso"


async def _user(db, perms: dict, *, role: UserRole = UserRole.ADMIN) -> User:
    u = User(
        open_id=f"email:at-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"at-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=role,
        status=UserStatus.ACTIVE,
        permissions=perms,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _corte(hora: int, minuto: int = 0, *, dias: int = 0) -> datetime:
    """Corte de HOJE (+dias) às hora:minuto BRT, em UTC — a regra do "mesmo
    dia" e do "corte futuro" olha o dia BRT."""
    d = datetime.now(svc.BRT).date() + timedelta(days=dias)
    return datetime(d.year, d.month, d.day, hora, minuto, tzinfo=svc.BRT).astimezone(UTC)


async def _lojas(db, user: User) -> None:
    db.add_all(
        [
            StoreInfo(user_id=user.id, platform="ml", account_name="aguiar", bling_store_id="55"),
            StoreInfo(
                user_id=user.id, platform="shopee", account_name="vortan", bling_store_id="77"
            ),
        ]
    )
    await db.commit()


async def _pedido(
    db,
    numero: str,
    *,
    loja: str = "55",
    corte: datetime | None,
    postagem: datetime | None,
    situacao: str | None = None,
    etiqueta_arquivo: bool = False,
    sku: str = "i200.sa",
) -> None:
    """Postado = situação 15 + evento no ledger (o trigger do ledger só carimba
    a 15, como em produção). Não postado = 6 (sem etiqueta) ou 21 (etiqueta
    gerada no Bling); `etiqueta_arquivo` = etiqueta já chegou no DaVinci."""
    bid = int(numero)
    db.add(
        BlingOrder(
            bling_id=bid,
            numero=numero,
            numeroloja=f"MK{numero}",
            item_codigo=sku,
            item_index=0,
            situacao=situacao or ("15" if postagem is not None else "6"),
            loja=loja,
            data=datetime.now(UTC) - timedelta(days=1),
            em_andamento_data=postagem.date() if postagem else None,
            marketplace_ship_deadline=corte,
        )
    )
    if postagem is not None:
        db.add(
            BlingEnvioEvento(
                bling_id=bid,
                item_index=0,
                item_codigo=sku,
                numero=numero,
                occurred_at=postagem,
                shipping_day=postagem.astimezone(svc.BRT).date(),
            )
        )
    if etiqueta_arquivo:
        db.add(
            NfEtiquetaArquivo(
                pedido_bling=numero,
                filename="etiqueta.pdf",
                content_type="application/pdf",
                size_bytes=3,
                blob=b"pdf",
            )
        )
    await db.commit()


async def _cenario(db, user: User) -> None:
    """ML aguiar — marcados: 940001 fila (12 min), 940002 energia (3h30),
    940006 etiqueta (não postado, sem etiqueta, corte hoje). Desmarcados:
    940004 no prazo, 940005 dia seguinte, 940007 sem corte, 940008 não postado
    com etiqueta gerada, 940009 corte amanhã. Shopee vortan: 940003 fila."""
    await _lojas(db, user)
    c13 = _corte(13)
    await _pedido(db, "940001", corte=c13, postagem=c13 + timedelta(minutes=12))
    await _pedido(db, "940002", corte=c13, postagem=c13 + timedelta(hours=3, minutes=30))
    await _pedido(db, "940003", loja="77", corte=_corte(12), postagem=_corte(12, 5))
    await _pedido(db, "940004", corte=c13, postagem=c13 - timedelta(minutes=10))
    await _pedido(db, "940005", corte=c13, postagem=c13 + timedelta(hours=12))
    await _pedido(db, "940006", corte=c13, postagem=None)
    await _pedido(db, "940007", corte=None, postagem=c13 + timedelta(minutes=5))
    await _pedido(db, "940008", corte=c13, postagem=None, situacao="21", etiqueta_arquivo=True)
    await _pedido(db, "940009", corte=_corte(13, dias=1), postagem=None)


TODOS = [f"94000{i}" for i in range(1, 10)] + ["999999"]


async def test_preview_classifica_marca_o_que_a_regra_pega_e_explica_o_resto(client, db, auth_as):
    user = await _user(db, PERM_EDIT)
    auth_as(user)
    await _cenario(db, user)

    r = await client.post(PREVIEW, json={"pedidos": TODOS})
    assert r.status_code == 200, r.text
    body = r.json()
    grupos = {g["loja"]: g for g in body["grupos"]}
    assert set(grupos) == {"ML aguiar", "SHOPEE vortan"}

    ml = grupos["ML aguiar"]
    assert ml["canal"] == "robo" and ml["plataforma"] == "ml" and ml["conta"] == "aguiar"
    por = {p["pedido_bling"]: p for p in ml["pedidos"]}
    assert {k: (v["incluir"], v["motivo"], v["situacao"]) for k, v in por.items()} == {
        "940001": (True, "fila", "fila"),
        "940002": (True, "energia", "energia"),
        "940006": (True, "etiqueta", "etiqueta"),
        "940004": (False, "outro", "no_prazo"),
        "940005": (False, "outro", "dia_seguinte"),
        "940007": (False, "outro", "sem_corte"),
        "940008": (False, "outro", "etiqueta_gerada"),
        "940009": (False, "outro", "corte_futuro"),
    }
    assert por["940001"]["atraso_min"] == 12 and por["940002"]["atraso_min"] == 210
    assert por["940008"]["etiqueta_gerada"] is True and por["940008"]["etiqueta_em"]
    assert por["940006"]["etiqueta_gerada"] is False and por["940006"]["postagem"] is None
    # loja com três motivos → um chamado só, texto misto com um bloco por motivo
    t = ml["texto"]
    assert "sobre 3 pedidos desta conta" in t
    assert "Fila na postagem:\n• MK940001 — despachar até 13:00, postagem confirmada às 13:12" in t
    assert "Queda de energia:\n• MK940002 — despachar até 13:00, postagem confirmada às 16:30" in t
    assert (
        "Problema na emissão da etiqueta (ainda não postados):\n"
        "• MK940006 — despachar até 13:00 (ainda não postado)" in t
    )
    assert "MK940004" not in t and "MK940008" not in t  # desmarcados ficam fora do texto

    sh = grupos["SHOPEE vortan"]
    assert sh["canal"] == "manual" and sh["plataforma"] == "shopee"
    assert [(p["motivo"], p["incluir"]) for p in sh["pedidos"]] == [("fila", True)]
    assert "fila na agência" in sh["texto"] and "queda de energia" not in sh["texto"].lower()

    assert [(e["pedido_bling"], e["motivo"]) for e in body["excluidos"]] == [
        ("999999", "pedido_nao_encontrado")
    ]


async def test_preview_respeita_as_escolhas_da_tela_e_trava_o_que_nao_pode(client, db, auth_as):
    user = await _user(db, PERM_EDIT)
    auth_as(user)
    await _cenario(db, user)
    r = await client.post(
        PREVIEW,
        json={
            "pedidos": ["940001", "940004", "940008", "940006"],
            # 940004 (no prazo) entra como "outro"; 940001 pede "etiqueta" (não
            # permitido: postado) → fica fila; 940008 pede "etiqueta" (não
            # permitido: etiqueta gerada) → fica outro; 940006 sai.
            "incluir": {"940004": True, "940008": True, "940006": False},
            "motivos": {"940001": "etiqueta", "940008": "etiqueta"},
            "motivo_outro": {"55": "A transportadora não fez a coleta hoje."},
        },
    )
    assert r.status_code == 200, r.text
    g = r.json()["grupos"][0]
    por = {p["pedido_bling"]: p for p in g["pedidos"]}
    assert (por["940001"]["incluir"], por["940001"]["motivo"]) == (True, "fila")
    assert (por["940004"]["incluir"], por["940004"]["motivo"]) == (True, "outro")
    assert (por["940008"]["incluir"], por["940008"]["motivo"]) == (True, "outro")
    assert (por["940006"]["incluir"], por["940006"]["motivo"]) == (False, "etiqueta")
    assert g["motivo_outro"] == "A transportadora não fez a coleta hoje."
    t = g["texto"]
    assert "A transportadora não fez a coleta hoje." in t
    assert "Outro motivo:\n" in t
    assert "• MK940004 — despachar até 13:00, postagem confirmada às 12:50" in t
    assert "• MK940008 — despachar até 13:00 (ainda não postado)" in t
    assert "Fila na postagem:\n• MK940001" in t and "MK940006" not in t
    assert "Problema na emissão da etiqueta" not in t


async def test_abrir_cria_um_chamado_por_loja_liga_os_pedidos_e_nao_repete(client, db, auth_as):
    user = await _user(db, PERM_EDIT)
    auth_as(user)
    await _cenario(db, user)
    prev = (await client.post(PREVIEW, json={"pedidos": TODOS})).json()
    grupos = []
    for g in prev["grupos"]:
        texto = g["texto"]
        pedidos = [
            {"pedido_bling": p["pedido_bling"], "motivo": p["motivo"], "incluir": p["incluir"]}
            for p in g["pedidos"]
        ]
        if g["loja"] == "ML aguiar":
            texto = "TEXTO EDITADO NA TELA\n" + texto  # a tela deixa ajustar antes de enviar
            # a pessoa marcou o 940004 (no prazo) com motivo livre
            for p in pedidos:
                if p["pedido_bling"] == "940004":
                    p["incluir"] = True
                    p["motivo"] = "outro"
        grupos.append(
            {
                "chave": g["chave"],
                "texto": texto,
                "motivo_outro": "Coleta não passou.",
                "pedidos": pedidos,
            }
        )

    r = await client.post(ABRIR, json={"grupos": grupos})
    assert r.status_code == 200, r.text
    abertos = [a for a in r.json()["abertos"] if a.get("chamado_id")]
    assert {(a["loja"], a["canal"], a["pedidos"]) for a in abertos} == {
        ("ML aguiar", "robo", 4),
        ("SHOPEE vortan", "manual", 1),
    }

    chamados = {
        c.conta: c
        for c in (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalars()
    }
    ml = chamados["aguiar"]
    assert ml.canal == "robo" and ml.plataforma == "ml" and ml.resolvido is False
    assert "4 pedido(s)" in (ml.observacao or "") and "MK940006" in (ml.observacao or "")
    # abertura PENDENTE no canal robô, sem protocolo = tarefa `abrir` do robô do formulário
    abertura_ml = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ml.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalar_one()
    assert abertura_ml.status == "pendente" and abertura_ml.canal == "robo"
    assert abertura_ml.direcao == "enviada"
    assert abertura_ml.texto.startswith("TEXTO EDITADO NA TELA")
    assert ml.chamado is None
    evento = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ml.id, ChamadoMensagem.tipo == "sistema"
            )
        )
    ).scalar_one()
    assert (
        "1 fila na postagem, 1 queda de energia, 1 problema na emissão da etiqueta, 1 outro motivo"
        in evento.texto
    )

    sh = chamados["vortan"]
    assert sh.canal == "manual" and sh.plataforma == "shopee"
    abertura_sh = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == sh.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalar_one()
    assert abertura_sh.status == "registrada" and abertura_sh.erro == "plataforma_sem_api"
    assert "• MK940003 — despachar até 12:00, postagem confirmada às 12:05" in abertura_sh.texto

    ligacoes = {cp.pedido_bling: cp for cp in (await db.execute(select(ChamadoPedido))).scalars()}
    assert {k: v.motivo for k, v in ligacoes.items()} == {
        "940001": "fila",
        "940002": "energia",
        "940003": "fila",
        "940004": "outro",
        "940006": "etiqueta",
    }
    assert ligacoes["940006"].chamado_id == ml.id and ligacoes["940006"].postagem_at is None
    assert ligacoes["940003"].chamado_id == sh.id

    # a aba Pedidos enxerga o chamado em cada linha
    por_pedido = await svc.chamados_por_pedido(db, ["940001", "940006", "940003", "940005"])
    assert set(por_pedido) == {"940001", "940006", "940003"}
    assert por_pedido["940001"]["status"] == "pendente" and por_pedido["940001"]["canal"] == "robo"
    assert (
        por_pedido["940003"]["status"] == "registrada" and por_pedido["940003"]["motivo"] == "fila"
    )

    # de novo: os que já têm chamado aberto não podem entrar; nada é aberto
    prev2 = (await client.post(PREVIEW, json={"pedidos": TODOS})).json()
    assert {e["pedido_bling"] for e in prev2["excluidos"] if e["motivo"] == "ja_tem_chamado"} == {
        "940001",
        "940002",
        "940003",
        "940004",
        "940006",
    }
    assert {
        p["pedido_bling"] for g in prev2["grupos"] for p in g["pedidos"] if p["incluir"]
    } == set()
    r = await client.post(ABRIR, json={"grupos": grupos})
    assert r.status_code == 200, r.text
    assert not [a for a in r.json()["abertos"] if a.get("chamado_id")]
    assert len((await db.execute(select(Chamado))).scalars().all()) == 2


async def test_outro_sem_texto_nao_abre(client, db, auth_as):
    user = await _user(db, PERM_EDIT)
    auth_as(user)
    await _cenario(db, user)
    r = await client.post(
        ABRIR,
        json={
            "grupos": [
                {
                    "chave": "55",
                    "pedidos": [{"pedido_bling": "940004", "motivo": "outro", "incluir": True}],
                }
            ]
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "motivo_outro_obrigatorio"
    assert (await db.execute(select(Chamado))).scalars().all() == []


async def test_precisa_de_permissao_de_chamados(client, db, auth_as):
    # admin passa por tudo: o teste precisa de um usuário comum só com view
    user = await _user(db, PERM_VIEW, role=UserRole.USER)
    auth_as(user)
    r = await client.post(PREVIEW, json={"pedidos": ["1"]})
    assert r.status_code == 403
    r = await client.post(
        ABRIR, json={"grupos": [{"chave": "55", "pedidos": [{"pedido_bling": "1"}]}]}
    )
    assert r.status_code == 403


def _p(
    numero: str,
    motivo: str,
    *,
    corte: datetime,
    postagem: datetime | None,
    incluir: bool = True,
) -> svc.PedidoAtraso:
    atraso = int((postagem - corte).total_seconds() // 60) if postagem else None
    return svc.PedidoAtraso(
        pedido_bling=numero,
        pedido_marketplace=f"MK{numero}",
        corte=corte.isoformat(),
        postagem=postagem.isoformat() if postagem else None,
        etiqueta_em=None,
        etiqueta_gerada=postagem is not None,
        atraso_min=atraso,
        situacao=motivo,
        incluir=incluir,
        motivo=motivo,
    )


async def test_render_texto_por_motivo():
    c = _corte(13)
    fila = _p("1", "fila", corte=c, postagem=c + timedelta(minutes=9))
    energia = _p("2", "energia", corte=c, postagem=c + timedelta(hours=4))
    etiqueta = _p("3", "etiqueta", corte=c, postagem=None)
    outro = _p("4", "outro", corte=c, postagem=c - timedelta(minutes=5))
    fora = _p("5", "fila", corte=c, postagem=c + timedelta(minutes=3), incluir=False)
    assert svc.motivo_por_atraso(60) == "fila" and svc.motivo_por_atraso(61) == "energia"

    t = svc.render_texto([fila, fora])
    assert t.startswith("Olá, equipe. Entramos em contato sobre 1 pedido(s)")
    assert (
        "fila na agência" in t and "• MK1 — despachar até 13:00, postagem confirmada às 13:09" in t
    )
    assert "MK5" not in t  # desmarcado não entra
    t = svc.render_texto([energia])
    assert "queda de energia elétrica" in t and "postagem confirmada às 17:00" in t
    t = svc.render_texto([etiqueta])
    assert "problema na emissão das etiquetas" in t
    assert "• MK3 — despachar até 13:00 (ainda não postado)" in t
    t = svc.render_texto([outro], "Coleta não passou hoje.")
    assert "Coleta não passou hoje." in t
    assert "• MK4 — despachar até 13:00, postagem confirmada às 12:55" in t
    t = svc.render_texto([fila, energia, etiqueta, outro], "Coleta não passou hoje.")
    assert t.startswith("Olá, equipe. Entramos em contato sobre 4 pedidos")
    for bloco in (
        "Fila na postagem:\n• MK1",
        "Queda de energia:\n• MK2",
        "Problema na emissão da etiqueta (ainda não postados):\n• MK3",
        "Outro motivo:\n• MK4",
    ):
        assert bloco in t, bloco
    assert "Coleta não passou hoje." in t and t.endswith("Obrigado pela atenção.")
    assert svc.render_texto([fora]) == ""
