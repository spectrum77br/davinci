"""Etiqueta (RF1) na API da lista: filtros, contagens, busca e troca à mão.

- o menu Filtrar filtra pela etiqueta (Reclamação, Devolução, Ag.
  cancelamento) e os filtros Pré-venda/Pós-venda passaram a ser a etiqueta
  (a conversa ainda sem etiqueta cai na regra antiga, não some);
- `?etiqueta=` junta com qualquer filtro (Falta responder + Reclamação);
- o /resumo conta as conversas abertas por etiqueta (total, plataforma, loja);
- a busca acha pelo nº do Bling e pelo SKU (e pelo pack do ML);
- POST /conversas/{id}/etiqueta: troca à mão com o nome de quem trocou na
  linha do tempo do detalhe; etiqueta inválida 422, Instagram 409, sem
  permissão de edição 403, fora da equipe 404.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoReclamacao,
    BlingOrder,
    DmConversa,
    Integration,
    IntegrationPlatform,
    User,
)
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import etiqueta_cron, gravar
from app.services.atendimento.constantes import ETIQUETAS

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    # A trava "só admin" tem os testes dela (test_atendimento_so_admin.py).
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    s = get_settings()
    for nome in ("atendimento_leitura_ativa", "atendimento_envio_ativo", "atendimento_ia_ativa"):
        monkeypatch.setattr(s, nome, False)


@pytest.fixture
async def pessoa(db, make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    u.name = "Ana Atendente"
    await db.commit()
    auth_as(u)
    return u


async def _loja(db: AsyncSession, dono: User, nome: str, plataforma: str = "shopee"):
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform(plataforma),
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal_nome = "chat" if plataforma == "shopee" else "pos_venda"
    canal = AtendimentoCanal(
        integration_id=integ.id,
        plataforma=plataforma,
        canal=canal_nome,
        modo="observar",
        status="ok",
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _conversa(
    db: AsyncSession,
    integ: Integration,
    canal: AtendimentoCanal,
    externo_id: str,
    *,
    horas: float,
    pedido: str | None = None,
    plataforma: str = "shopee",
    dados: dict | None = None,
    responder: bool = False,
) -> AtendimentoConversa:
    c, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal.canal,
        externo_id=externo_id,
        pedido_marketplace=pedido,
        comprador_nome="Comprador",
        dados=dados,
    )
    await gravar.gravar_mensagem(
        db,
        c,
        externo_id=f"{externo_id}-c",
        autor="cliente",
        texto="olá",
        enviada_em=AGORA - timedelta(hours=horas),
    )
    if responder:
        await gravar.gravar_mensagem(
            db,
            c,
            externo_id=f"{externo_id}-l",
            autor="loja",
            texto="oi!",
            enviada_em=AGORA - timedelta(hours=horas) + timedelta(minutes=5),
        )
    await db.commit()
    return c


async def _bling(db: AsyncSession, numero: str, numeroloja: str, situacao: int, sku: str):
    db.add(
        BlingOrder(
            bling_id=int(numero),
            numero=numero,
            numeroloja=numeroloja,
            situacao=str(situacao),
            data=AGORA - timedelta(days=3),
            item_index=0,
            item_codigo=sku,
            itemvalor=Decimal("10"),
        )
    )
    await db.commit()


async def _cenario(db: AsyncSession, make_user) -> dict[str, AtendimentoConversa]:
    """Uma conversa por etiqueta na Shopee (+ a do ML), todas calculadas pelo motor."""
    dono = await make_user()
    integ, canal = await _loja(db, dono, "kfa")
    ml, canal_ml = await _loja(db, dono, "aguiar", "ml")
    await _bling(db, "297001", "2409CANC", 83955, "MALA-AZUL.ci")
    await _bling(db, "297002", "2409DEVO", 83957, "MALA-VERDE.sp")
    await _bling(db, "297840", "2000009999999999", 83953, "CELULAR-X.pi")
    c = {
        "pre": await _conversa(db, integ, canal, "PRE", horas=1),
        "pos": await _conversa(db, integ, canal, "POS", horas=2, pedido="2409POS", responder=True),
        "canc": await _conversa(db, integ, canal, "CANC", horas=3, pedido="2409CANC"),
        "devo": await _conversa(db, integ, canal, "DEVO", horas=4, pedido="2409DEVO"),
        "recl": await _conversa(db, integ, canal, "RECL", horas=5, pedido="2409RECL"),
        # O pack do ML: o Bling gravou o PACK em numeroloja.
        "pack": await _conversa(
            db,
            ml,
            canal_ml,
            "2000009999999999",
            horas=6,
            pedido="2000018509205724",
            plataforma="ml",
            dados={"pack_id": "2000009999999999"},
            responder=True,
        ),
        "fechada": await _conversa(db, integ, canal, "FECH", horas=7, pedido="2409FECH"),
    }
    db.add(
        AtendimentoReclamacao(
            plataforma="shopee",
            externo_id="DISP-1",
            tipo="reclamacao",
            status="OPEN",
            pedido_marketplace="2409RECL",
            aberta_em=AGORA - timedelta(days=1),
        )
    )
    c["fechada"].situacao = "fechada"
    await gravar.recalcular_conversa(db, c["fechada"])
    await db.commit()
    # O cron pega o 83955/83957 e a reclamação (que não passaram pelo sync).
    await etiqueta_cron.recalcular_ids([x.id for x in c.values()], motivo="cron")
    for x in c.values():
        await db.refresh(x)
    assert {k: x.etiqueta for k, x in c.items()} == {
        "pre": "pre_venda",
        "pos": "pos_venda",
        "canc": "ag_cancelamento",
        "devo": "devolucao",
        "recl": "reclamacao",
        "pack": "pos_venda",
        "fechada": "pos_venda",
    }
    return c


def _ids(r) -> list[str]:
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["itens"]]


async def test_filtros_por_etiqueta_e_pre_pos_pela_etiqueta(client, db, make_user, pessoa):
    c = await _cenario(db, make_user)
    ids = {k: str(v.id) for k, v in c.items()}

    async def lista(**params) -> list[str]:
        return _ids(await client.get(f"{URL}/conversas", params=params))

    assert await lista(filtro="reclamacao") == [ids["recl"]]
    assert await lista(filtro="devolucao") == [ids["devo"]]
    assert await lista(filtro="ag_cancelamento") == [ids["canc"]]
    # Pós-venda = a ETIQUETA pós-venda: a reclamação, a devolução e o Ag.
    # cancelamento (que têm pedido) não entram; a fechada também não.
    assert await lista(filtro="pos_venda") == [ids["pos"], ids["pack"]]
    assert await lista(filtro="pre_venda") == [ids["pre"]]
    # `?etiqueta=` junto de qualquer filtro.
    assert await lista(filtro="aguardando", etiqueta="reclamacao") == [ids["recl"]]
    assert await lista(filtro="aguardando", etiqueta="pos_venda") == []
    assert await lista(filtro="fechadas", etiqueta="pos_venda") == [ids["fechada"]]
    r = await client.get(f"{URL}/conversas", params={"etiqueta": "email"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "etiqueta_invalida")

    # Conversa ainda sem etiqueta (antes do preenchimento) não some do filtro:
    # vale a regra de pré/pós-venda de antes.
    c["pos"].etiqueta = None
    c["pre"].etiqueta = None
    await db.commit()
    assert ids["pos"] in await lista(filtro="pos_venda")
    assert ids["pre"] in await lista(filtro="pre_venda")
    item = next(
        i for i in (await client.get(f"{URL}/conversas")).json()["itens"] if i["id"] == ids["pos"]
    )
    assert item["etiqueta"] is None  # a API não inventa: só o filtro usa a regra


async def test_resumo_conta_por_etiqueta(client, db, make_user, pessoa):
    c = await _cenario(db, make_user)
    r = (await client.get(f"{URL}/resumo")).json()
    # Só as abertas (a fechada fica de fora).
    assert r["etiquetas"] == {
        "reclamacao": 1,
        "ag_cancelamento": 1,
        "devolucao": 1,
        "avaliacao": 0,
        "carrinho": 0,
        "pre_venda": 1,
        "pos_venda": 2,
        "midia": 0,
    }
    shopee = next(p for p in r["plataformas"] if p["plataforma"] == "shopee")
    assert shopee["etiquetas"] == {
        "reclamacao": 1,
        "ag_cancelamento": 1,
        "devolucao": 1,
        "avaliacao": 0,
        "carrinho": 0,
        "pre_venda": 1,
        "pos_venda": 1,
        "midia": 0,
    }
    loja_ml = next(lj for lj in r["lojas"] if lj["integration_id"] == str(c["pack"].integration_id))
    assert loja_ml["etiquetas"]["pos_venda"] == 1
    assert sum(loja_ml["etiquetas"].values()) == 1
    assert set(r["etiquetas"]) == set(ETIQUETAS)


async def test_busca_pelo_numero_do_bling_e_pelo_sku(client, db, make_user, pessoa):
    c = await _cenario(db, make_user)

    async def busca(q: str) -> list[str]:
        return _ids(await client.get(f"{URL}/conversas", params={"q": q}))

    assert await busca("297001") == [str(c["canc"].id)]
    assert await busca("mala-verde") == [str(c["devo"].id)]
    assert sorted(await busca("MALA-")) == sorted([str(c["canc"].id), str(c["devo"].id)])
    # O pack do ML: o Bling achou pelo pack (numeroloja), a conversa casa pelo `pack_id`.
    assert await busca("297840") == [str(c["pack"].id)]
    assert await busca("CELULAR-X") == [str(c["pack"].id)]
    # Curto demais para SKU (2 letras): só o nº exato do Bling conta.
    assert await busca("pi") == []
    # O curinga do LIKE é texto, não curinga.
    assert await busca("MALA_%") == []


async def test_troca_a_mao_pela_api_e_linha_do_tempo(client, db, make_user, auth_as, pessoa):
    c = await _cenario(db, make_user)
    conversa = c["recl"]
    url = f"{URL}/conversas/{conversa.id}/etiqueta"

    r = await client.post(url, json={"etiqueta": "pos_venda", "motivo": "resolvido no chat"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo["conversa"]["etiqueta"], corpo["conversa"]["etiqueta_manual"]) == (
        "pos_venda",
        True,
    )
    # A reclamação continua aberta: vira o indicador pequeno.
    assert corpo["conversa"]["etiquetas_secundarias"] == ["reclamacao"]
    ultima = corpo["etiqueta_historico"][-1]
    assert {
        k: ultima[k] for k in ("de", "para", "de_rotulo", "para_rotulo", "motivo", "por_nome")
    } == {
        "de": "reclamacao",
        "para": "pos_venda",
        "de_rotulo": "Reclamação",
        "para_rotulo": "Pós-venda",
        "motivo": "Trocada à mão: resolvido no chat",
        "por_nome": "Ana Atendente",
    }
    assert ultima["por_user_id"] == str(pessoa.id)

    # O cron passa e nada aconteceu: a troca continua valendo.
    await etiqueta_cron.recalcular_ids([conversa.id], motivo="cron")
    detalhe = (await client.get(f"{URL}/conversas/{conversa.id}")).json()
    assert (detalhe["conversa"]["etiqueta"], detalhe["conversa"]["etiqueta_manual"]) == (
        "pos_venda",
        True,
    )
    # A linha do tempo: o cron abriu a reclamação, a pessoa trocou à mão.
    assert [(h["para"], h["por_nome"]) for h in detalhe["etiqueta_historico"]] == [
        ("reclamacao", None),
        ("pos_venda", "Ana Atendente"),
    ]

    # De volta ao que o motor dá = automático.
    r = await client.post(url, json={"etiqueta": "reclamacao"})
    assert r.json()["conversa"]["etiqueta_manual"] is False
    assert r.json()["etiqueta_historico"][-1]["motivo"] == "Trocada à mão (de volta ao automático)"

    r = await client.post(url, json={"etiqueta": "sac"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "etiqueta_invalida")
    r = await client.post(
        f"{URL}/conversas/ig:{conversa.id}/etiqueta", json={"etiqueta": "pre_venda"}
    )
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "somente_leitura")
    r = await client.post(f"{URL}/conversas/lixo/etiqueta", json={"etiqueta": "pre_venda"})
    assert r.status_code == 404

    # Só ver não troca; fora da equipe não acha.
    auth_as(await make_user(permissions={"atendimento": {"view": True}}))
    assert (await client.post(url, json={"etiqueta": "pre_venda"})).status_code == 403


async def test_instagram_sem_etiqueta_e_sem_contagem(client, db, make_user, pessoa):
    await _cenario(db, make_user)
    db.add(DmConversa(conta="marca", participante_id="1", participante_nome="Fulano"))
    await db.commit()
    r = (await client.get(f"{URL}/resumo")).json()
    assert sum(r["etiquetas"].values()) == 6


async def test_nota_interna_nao_e_a_ultima_mensagem_da_lista(client, db, make_user, pessoa):
    """A nota (origem `davinci_nota`, tipo `nota`) não vira a prévia da lista
    (`ultima_mensagem_tipo`) e, no detalhe, sai com o nome de quem escreveu."""
    from app.models import AtendimentoMensagem

    dono = await make_user()
    integ, canal = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canal, "NOTA", horas=1)
    db.add(
        AtendimentoMensagem(
            conversa_id=c.id, autor="equipe", origem="davinci_nota", tipo="nota",
            texto="cliente ligou, já resolvi", enviada_em=AGORA, status="recebida",
            autor_user_id=pessoa.id,
        )
    )
    await db.commit()
    item = next(
        i for i in (await client.get(f"{URL}/conversas")).json()["itens"] if i["id"] == str(c.id)
    )
    assert item["ultima_mensagem_tipo"] == "texto"  # a do cliente, não "outro" (a nota)
    assert item["pendentes"] == 1  # a nota não é resposta
    detalhe = (await client.get(f"{URL}/conversas/{c.id}")).json()
    nota = next(m for m in detalhe["mensagens"] if m["tipo"] == "nota")
    assert nota["autor_nome"] == "Ana Atendente"
