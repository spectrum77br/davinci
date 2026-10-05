"""Painel do pedido da conversa e nota interna (item 3 do Comunicador, 01/10/2026).

O que estes testes seguram:

- NOTA INTERNA: nunca mexe na fila (ultima_*, aguardando, prazo), não é a
  "última mensagem" da lista nem no recálculo do zero, não vai para a IA e
  tem autor `equipe` (não `sistema`, que a Magalu usa para a mediação);
- ESTOQUE: o lote comprado (cobre/não cobre a quantidade), os lotes irmãos
  e, no kit, cada componente com o que o pedido precisa — com a hora;
- MARGEM: a mesma regra da coluna "Margem" da aba Margem (pós-reembolso,
  senão a do Bling; ML/Shopee/TikTok sem repasse em branco), só para quem
  vê a Margem, lucro só para admin;
- OBSERVAÇÕES DO BLING: GET ao vivo, memória de 5 min, `invalidar`, falha
  que não derruba o painel;
- links (Bling e plataforma) só com nº limpo e https;
- AdsPower: o perfil pela integração, pelo nome da conta, ou o motivo de
  não haver; o registro de quem abriu;
- busca por nº do Bling e por SKU (o encaixe da lista);
- as rotas novas têm a MESMA trava de acesso da caixa (`_so_admin`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends
from fastapi.routing import APIRoute
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_active_user, require_admin, require_user
from app.main import app
from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    BlingKitComponent,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Product,
    StoreInfo,
    User,
    UserRole,
)
from app.routers import atendimento as rota
from app.routers import atendimento_painel
from app.security.cipher import encrypt_json
from app.services import links_shopee
from app.services.atendimento import gravar, ia, painel
from app.services.atendimento.constantes import e_nota

URL = "/api/atendimento"
AGORA = datetime.now(UTC)


# As rotas novas vêm do `app.main` (o router `atendimento_painel` registrado
# lá) — sem incluir à mão: se o main.py perder o router, estes testes caem.


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_envio_ativo",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_simulador",
    ):
        monkeypatch.setattr(s, nome, False)
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    return s


@pytest.fixture(autouse=True)
def _sem_cache(monkeypatch):
    """Memória das Observações limpa a cada teste (Redis falso: sempre falha)."""
    painel._OBS_LOCAL.clear()

    class _RedisFora:
        async def get(self, *a, **k):
            raise ConnectionError("sem redis no teste")

        async def set(self, *a, **k):
            raise ConnectionError("sem redis no teste")

        async def delete(self, *a, **k):
            raise ConnectionError("sem redis no teste")

    import app.redis_client as rc

    monkeypatch.setattr(rc, "redis", _RedisFora())
    yield
    painel._OBS_LOCAL.clear()


@pytest.fixture(autouse=True)
async def _limpa_kits(db):
    """`bling_kit_components` não está na limpeza do conftest."""
    yield
    await db.execute(text("DELETE FROM bling_kit_components"))
    await db.commit()


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN, email=f"adm-{uuid4().hex[:6]}@davinci-test.com")
    auth_as(u)
    return u


# ─────────────── fábrica ───────────────


async def _loja(db, dono, *, plataforma=IntegrationPlatform.SHOPEE, nome="kfa", canal="chat"):
    integ = Integration(
        user_id=dono.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    plat = plataforma.value
    c = AtendimentoCanal(
        integration_id=integ.id, plataforma=plat, canal=canal, modo="observar", status="ok"
    )
    db.add(c)
    await db.commit()
    return integ, c


async def _conversa(db, integ, canal, *, pedido="250925ABC", externo="conv-1", dados=None):
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=canal.plataforma,
        canal_nome=canal.canal,
        externo_id=externo,
        pedido_marketplace=pedido,
        comprador_nome="Comprador",
        dados=dados,
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{externo}-c",
        autor="cliente",
        texto="cadê meu pedido?",
        enviada_em=AGORA - timedelta(hours=1),
    )
    await db.commit()
    return conversa


async def _pedido_bling(
    db,
    *,
    numero="297840",
    numeroloja="250925ABC",
    itens=(("dg053.ci", 2),),
    situacao="6",
    bling_id=9001,
):
    for i, (sku, qtd) in enumerate(itens):
        db.add(
            BlingOrder(
                bling_id=bling_id,
                item_index=i,
                numero=numero,
                numeroloja=numeroloja,
                situacao=situacao,
                item_codigo=sku,
                item_descricao=f"Produto {sku}",
                item_quantidade=qtd,
                data=AGORA,
            )
        )
    await db.commit()


async def _produto(db, dono, sku, stock, *, formato="S", bling_product_id=None, situacao="A"):
    p = Product(
        user_id=dono.id,
        sku=sku,
        name=f"Nome {sku}",
        stock=stock,
        formato=formato,
        bling_product_id=bling_product_id,
        situacao=situacao,
    )
    db.add(p)
    await db.commit()
    return p


# ─────────────── nota interna ───────────────


async def test_nota_nao_mexe_na_fila_nem_na_ultima_mensagem(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    antes = (
        conversa.ultima_mensagem_em,
        conversa.ultima_mensagem_resumo,
        conversa.ultima_autor,
        conversa.ultima_do_cliente_em,
        conversa.ultima_da_loja_em,
        conversa.aguardando_resposta,
        conversa.prazo_resposta_em,
        conversa.situacao,
    )
    nota = await painel.criar_nota(db, conversa, "  cliente já ligou 2x  ", user_id=dono.id)
    await db.commit()
    assert nota.autor == painel.AUTOR_EQUIPE == "equipe"
    assert (nota.origem, nota.tipo, nota.texto) == ("davinci_nota", "nota", "cliente já ligou 2x")
    assert e_nota(nota.origem, nota.tipo)
    await db.refresh(conversa)
    depois = (
        conversa.ultima_mensagem_em,
        conversa.ultima_mensagem_resumo,
        conversa.ultima_autor,
        conversa.ultima_do_cliente_em,
        conversa.ultima_da_loja_em,
        conversa.aguardando_resposta,
        conversa.prazo_resposta_em,
        conversa.situacao,
    )
    assert depois == antes
    assert conversa.aguardando_resposta is True

    # Recalcular do zero (envio que falhou, fechar/reabrir) também pula a nota.
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    assert conversa.ultima_mensagem_resumo == "cadê meu pedido?"
    assert conversa.ultima_autor == "cliente"
    assert conversa.aguardando_resposta is True

    # E o recalcular "andando para frente" com a nota na mão também.
    gravar.recalcular(conversa, [nota])
    assert conversa.ultima_mensagem_resumo == "cadê meu pedido?"


async def test_nota_nao_vai_para_a_ia(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    await painel.criar_nota(db, conversa, "NÃO devolver: golpe conhecido", user_id=dono.id)
    await db.commit()
    mensagens = await ia._mensagens_recentes(db, conversa)
    assert [m.autor for m in mensagens] == ["cliente"]
    assert not any("golpe" in (m.texto or "") for m in mensagens)


async def test_nota_vazia_ou_longa_e_recusada(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    with pytest.raises(painel.NotaInvalida) as e:
        await painel.criar_nota(db, conversa, "   \x00 ", user_id=dono.id)
    assert e.value.code == "nota_vazia"
    with pytest.raises(painel.NotaInvalida) as e:
        await painel.criar_nota(db, conversa, "x" * 4001, user_id=dono.id)
    assert e.value.code == "nota_longa"


async def test_post_nota_grava_e_devolve_com_o_nome(db, client, admin, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    r = await client.post(f"{URL}/conversas/{conversa.id}/notas", json={"texto": "ligar amanhã"})
    assert r.status_code == 201, r.text
    m = r.json()["mensagem"]
    assert (m["tipo"], m["origem"], m["autor"]) == ("nota", "davinci_nota", "equipe")
    assert m["texto"] == "ligar amanhã"
    assert m["autor_nome"]
    linhas = (
        (
            await db.execute(
                select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa.id)
            )
        )
        .scalars()
        .all()
    )
    assert sum(1 for x in linhas if x.tipo == "nota") == 1

    r = await client.post(f"{URL}/conversas/{conversa.id}/notas", json={"texto": "  "})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "nota_vazia"
    r = await client.post(f"{URL}/conversas/ig:{uuid4()}/notas", json={"texto": "x"})
    assert r.status_code == 409


def test_rotas_novas_tem_a_trava_da_caixa():
    rotas = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.endpoint.__module__ == atendimento_painel.__name__
    ]
    assert {r.path for r in rotas} == {
        f"{URL}/conversas/{{conversa_id}}/painel",
        f"{URL}/conversas/{{conversa_id}}/notas",
        f"{URL}/conversas/{{conversa_id}}/foto",
        f"{URL}/adspower/aberto",
    }
    for r in rotas:
        assert rota._so_admin in [d.call for d in r.dependant.dependencies], r.path


async def test_rotas_novas_recusam_quem_nao_e_admin(db, client, make_user, auth_as):
    u = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    auth_as(u)
    r = await client.get(f"{URL}/conversas/{uuid4()}/painel")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_only"
    r = await client.post(f"{URL}/adspower/aberto", json={"resultado": "aberto"})
    assert r.status_code == 403


# ─────────────── estoque ───────────────


def test_lotes_e_irmaos():
    assert painel.lote_do_sku("DG053.CI") == "ci"
    assert painel.lote_do_sku("dg053.ci+a001.ci") == "ci"
    assert painel.lote_do_sku("dg053.ci+a075") == "ci"  # acessório sem lote não atrapalha
    assert painel.lote_do_sku("dg053.ci+a001.sp") is None  # misturado
    assert painel.lote_do_sku("uaf001m1.110") is None  # número não é lote
    assert painel.lote_do_sku("b009.8.12.20") is None
    irmaos = dict(painel.lotes_irmaos("dg053.ci+a001.ci"))
    assert irmaos["sp"] == "dg053.sp+a001.sp"
    assert irmaos["cd"] == "dg053.cd+a001.cd"
    assert set(irmaos) == {"ci", "pi", "ra", "sa", "sp", "cd", "us"}
    assert painel.lotes_irmaos("uaf001m1.110") == []


async def test_saldo_do_lote_comprado_e_dos_irmaos(db, make_user):
    dono = await make_user()
    await _produto(db, dono, "dg053.ci", 1)
    await _produto(db, dono, "dg053.sp", 7)
    await _produto(db, dono, "dg053.cd", 40)
    await _produto(db, dono, "dg053.us", 3)
    s = await painel.saldo_do_item(db, "dg053.ci", quantidade=2)
    assert (s["existe"], s["saldo"], s["quantidade"], s["cobre"]) == (True, 1, 2, False)
    assert s["atualizado_em"]
    lotes = {x["lote"]: x for x in s["lotes"]}
    assert set(lotes) == {"ci", "sp", "cd", "us"}
    assert lotes["ci"]["proprio"] is True
    assert lotes["cd"]["de_venda"] is False and "Distribuição" in lotes["cd"]["rotulo"]
    # Outros lotes DE VENDA: só o .sp (CD e usado não saem direto na venda).
    assert s["saldo_outros_lotes"] == 7
    assert s["kit"] is False

    cobre = await painel.saldo_do_item(db, "DG053.SP", quantidade="1")
    assert (cobre["saldo"], cobre["cobre"]) == (7, True)
    sem = await painel.saldo_do_item(db, "zz999.ci", quantidade=1)
    assert (sem["existe"], sem["saldo"], sem["cobre"]) == (False, None, None)


async def test_kit_mostra_os_componentes(db, make_user):
    dono = await make_user()
    await _produto(db, dono, "dg053.ci+a001.ci", 2, formato="E", bling_product_id=100)
    await _produto(db, dono, "dg053.ci", 5, bling_product_id=101)
    await _produto(db, dono, "a001.ci", 1, bling_product_id=102)
    db.add_all(
        [
            BlingKitComponent(
                kit_bling_product_id=100, component_bling_product_id=101, quantidade=1
            ),
            BlingKitComponent(
                kit_bling_product_id=100, component_bling_product_id=102, quantidade=2
            ),
        ]
    )
    await db.commit()
    s = await painel.saldo_do_item(db, "dg053.ci+a001.ci", quantidade=1)
    assert s["kit"] is True
    assert (s["saldo"], s["cobre"]) == (2, True)
    comps = {c["sku"]: c for c in s["componentes"]}
    assert comps["dg053.ci"]["necessario"] == 1 and comps["dg053.ci"]["cobre"] is True
    # O pedido leva 1 kit = 2 do a001.ci, e só há 1.
    assert comps["a001.ci"]["necessario"] == 2 and comps["a001.ci"]["cobre"] is False


# ─────────────── margem ───────────────


async def _linha_margem(
    db,
    *,
    pedido,
    sku,
    plataforma,
    custo,
    valorbase=None,
    liquido=None,
    margem_bling=None,
    minima=0.1,
    status=None,
):
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku, produto, quantidade,
                situacao, situacao_nome, plataforma_bling, loja_nome, item_proportion,
                margem_minima, bling_valorbase_item, bling_custo_produtos,
                bling_margem_calculado, marketplace_liquido_base_margem_item,
                bling_status_margem, data
            ) VALUES (
                :id, :pedido, 9001, :sku, 'Produto', 1, '6', 'Em aberto', :plataforma,
                'Loja', 1, :minima, :valorbase, :custo, :margem_bling, :liquido, :status, :data
            )
            """
        ),
        {
            "id": uuid4(),
            "pedido": pedido,
            "sku": sku,
            "plataforma": plataforma,
            "minima": minima,
            "valorbase": valorbase,
            "custo": custo,
            "margem_bling": margem_bling,
            "liquido": liquido,
            "status": status,
            "data": AGORA,
        },
    )
    await db.commit()


def test_margem_final_e_a_da_coluna_margem():
    # Pós-reembolso manda; nula ou zero, a do Bling.
    assert (
        painel.margem_final(
            {"plataforma": "amazon", "margem_pos_reembolso": 0.3, "margem_bling": 0.2}
        )
        == 0.3
    )
    assert (
        painel.margem_final(
            {"plataforma": "amazon", "margem_pos_reembolso": 0, "margem_bling": 0.2}
        )
        == 0.2
    )
    # ML/Shopee/TikTok sem repasse nem saldo manual: em branco.
    assert (
        painel.margem_final(
            {
                "plataforma": "ml",
                "margem_pos_reembolso": 0.3,
                "saldo_plataforma": None,
                "saldo_manual": None,
            }
        )
        is None
    )
    assert (
        painel.margem_final(
            {
                "plataforma": "ml",
                "margem_pos_reembolso": 0.3,
                "saldo_plataforma": None,
                "saldo_manual": 120,
            }
        )
        == 0.3
    )


async def test_margem_do_pedido_igual_a_aba_margem(db):
    # Amazon: saldo do Bling 150 sobre custo 100 = 50%.
    await _linha_margem(
        db,
        pedido="500",
        sku="a1",
        plataforma="amazon",
        custo=100,
        valorbase=150,
        margem_bling=0.4,
        minima=0.1,
    )
    m = await painel.margem_do_pedido(db, "500", ver_lucro=True)
    assert m["na_margem"] is True
    assert m["margem"] == pytest.approx(0.5)
    assert m["abaixo_da_minima"] is False
    assert m["lucro"] == pytest.approx(50)
    assert m["status"] in ("Aprovado", "Pendente")

    # Sem permissão de lucro: o R$ não vem.
    sem_lucro = await painel.margem_do_pedido(db, "500", ver_lucro=False)
    assert "lucro" not in sem_lucro and "lucro" not in sem_lucro["itens"][0]

    # Dois itens: Σlucro ÷ Σcusto; um ML sem repasse deixa o pedido em branco.
    await _linha_margem(db, pedido="501", sku="b1", plataforma="ml", custo=100, liquido=130)
    await _linha_margem(db, pedido="501", sku="b2", plataforma="ml", custo=100, liquido=None)
    m = await painel.margem_do_pedido(db, "501")
    itens = {i["sku"]: i for i in m["itens"]}
    assert itens["b1"]["margem"] == pytest.approx(0.3)
    assert itens["b2"]["margem"] is None and itens["b2"]["aguardando_repasse"] is True
    assert m["margem"] is None and "repasse" in m["aviso"]

    fora = await painel.margem_do_pedido(db, "999999")
    assert fora["na_margem"] is False and "Margem" in fora["aviso"]
    assert await painel.margem_do_pedido(db, None) is None


async def test_quem_ve_a_margem(make_user):
    adm = await make_user(role=UserRole.ADMIN)
    com = await make_user(permissions={"margem": {"view": True}})
    sem = await make_user(permissions={"atendimento": {"view": True}})
    assert painel.ve_margem(adm) and painel.ve_lucro(adm)
    assert painel.ve_margem(com) and not painel.ve_lucro(com)
    assert not painel.ve_margem(sem) and not painel.ve_margem(None)


# ─────────────── observações do Bling ───────────────


class _BlingFalso:
    def __init__(self, obs="23/09 - restrição de envio", falha: Exception | None = None):
        self.chamadas: list[int] = []
        self.obs = obs
        self.falha = falha

    async def get_order(self, bling_id: int) -> dict:
        self.chamadas.append(bling_id)
        if self.falha is not None:
            raise self.falha
        return {"id": bling_id, "observacoes": self.obs, "observacoesInternas": ""}


@pytest.fixture
def bling(monkeypatch):
    falso = _BlingFalso()

    async def cliente(session):
        return falso

    monkeypatch.setattr(painel, "_cliente_bling", cliente)
    return falso


async def test_observacoes_ao_vivo_com_memoria_de_5_min(db, bling):
    a = await painel.observacoes_bling(9001, session=db)
    assert a["observacoes"] == "23/09 - restrição de envio"
    assert a["observacoes_internas"] is None
    assert (a["do_cache"], a["erro"]) == (False, None)
    b = await painel.observacoes_bling("9001", session=db)
    assert b["do_cache"] is True
    assert bling.chamadas == [9001]  # a segunda veio da memória

    # Quem grava na observação invalida: a próxima vai ao Bling.
    bling.obs = "01/10 - TROCA dg053.ci -> dg053.sp"
    await painel.invalidar_observacoes(9001)
    c = await painel.observacoes_bling(9001, session=db)
    assert c["observacoes"].startswith("01/10 - TROCA")
    assert bling.chamadas == [9001, 9001]
    # `forcar` pula a memória (o "atualizar" da tela).
    await painel.observacoes_bling(9001, session=db, forcar=True)
    assert len(bling.chamadas) == 3


async def test_observacoes_falha_nao_levanta(db, bling):
    req = httpx.Request("GET", "https://api.bling.com.br/Api/v3/pedidos/vendas/1")
    bling.falha = httpx.HTTPStatusError(
        "404", request=req, response=httpx.Response(404, request=req)
    )
    r = await painel.observacoes_bling(1, session=db)
    assert r["observacoes"] is None
    assert r["codigo"] == "bling_nao_achou" and "não achou" in r["erro"]
    sem_id = await painel.observacoes_bling(None)
    assert sem_id["codigo"] == "sem_bling_id"


async def test_observacoes_sem_integracao_do_bling(db):
    r = await painel.observacoes_bling(5, session=db)
    assert r["codigo"] == "sem_integracao_bling"


# ─────────────── links ───────────────


async def test_links_do_pedido(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal, pedido="250925ABC")
    pedido = painel.PedidoBling(numero="297840", numeroloja="250925ABC", bling_id=9001)
    links = painel.links_do_pedido(conversa, pedido)
    assert links["bling"] == "https://www.bling.com.br/vendas.php#edit/9001"
    # Sem o order_id interno: a lista de pedidos buscando o order_sn (a
    # página `/portal/sale/order/<x>` só abre com o número interno).
    assert links["plataforma"] == {
        "url": "https://seller.shopee.com.br/portal/sale/order?search=250925ABC",
        "rotulo": "Abrir na Shopee",
    }
    # Com o order_id interno achado com segurança (`links_shopee`): a página do pedido.
    ids = links_shopee.IdsShopee(pedidos={(integ.id, "250925ABC"): "244141571124463"})
    assert painel.links_do_pedido(conversa, pedido, ids)["plataforma"] == {
        "url": "https://seller.shopee.com.br/portal/sale/order/244141571124463",
        "rotulo": "Abrir na Shopee",
    }
    # O número de OUTRA loja não serve.
    outra = links_shopee.IdsShopee(pedidos={(uuid4(), "250925ABC"): "244141571124463"})
    assert painel.links_do_pedido(conversa, pedido, outra)["plataforma"]["url"].endswith(
        "?search=250925ABC"
    )
    # Nº que mexeria na URL não entra.
    conversa.pedido_marketplace = "250925?x=1"
    assert painel.links_do_pedido(conversa, None) == {"bling": None, "plataforma": None}
    assert painel.link_bling("12a") is None


async def test_link_do_ml_usa_o_pack(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono, plataforma=IntegrationPlatform.ML, canal="pos_venda")
    conversa = await _conversa(
        db,
        integ,
        canal,
        pedido="2000018509205724",
        externo="2000009999",
        dados={"pack_id": "2000009999"},
    )
    links = painel.links_do_pedido(conversa, None)
    assert links["plataforma"] == {
        "url": "https://www.mercadolivre.com.br/vendas/2000009999/detalhe",
        "rotulo": "Abrir no Mercado Livre",
    }


# ─────────────── AdsPower ───────────────


async def test_perfil_pela_integracao_e_pelo_nome(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono, nome="Kfa ")
    conversa = await _conversa(db, integ, canal)

    sem = await painel.perfil_adspower(db, conversa)
    assert sem["perfil"] is None and sem["codigo"] == "sem_cadastro"

    db.add(StoreInfo(user_id=dono.id, platform="shopee", account_name="kfa", server=None))
    await db.commit()
    vazio = await painel.perfil_adspower(db, conversa)
    assert vazio["codigo"] == "sem_perfil" and "Servidor" in vazio["motivo"]

    linha = (await db.execute(select(StoreInfo))).scalars().one()
    linha.server = "84"
    await db.commit()
    pelo_nome = await painel.perfil_adspower(db, conversa)
    assert (pelo_nome["perfil"], pelo_nome["fonte"], pelo_nome["codigo"]) == ("84", "nome", None)

    # A ligação pela integração vale mais que o nome (outra linha, outro perfil).
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform="shopee",
            account_name="outra",
            server="99",
            integration_id=integ.id,
        )
    )
    await db.commit()
    pela_integ = await painel.perfil_adspower(db, conversa)
    assert (pela_integ["perfil"], pela_integ["fonte"]) == ("99", "integracao")


async def test_ml_do_cadastro_como_mercadolivre(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(
        db, dono, plataforma=IntegrationPlatform.ML, nome="aguiar", canal="pos_venda"
    )
    conversa = await _conversa(db, integ, canal, externo="p-1")
    db.add(StoreInfo(user_id=dono.id, platform="mercadolivre", account_name="AGUIAR", server="12"))
    await db.commit()
    p = await painel.perfil_adspower(db, conversa)
    assert p["perfil"] == "12"


async def test_post_adspower_aberto_registra(db, client, admin, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform="shopee",
            account_name="kfa",
            server="84",
            integration_id=integ.id,
        )
    )
    await db.commit()
    r = await client.post(
        f"{URL}/adspower/aberto",
        json={"conversa_id": str(conversa.id), "resultado": "enviado", "codigo": None},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"registrado": True, "perfil": "84"}
    r = await client.post(f"{URL}/adspower/aberto", json={"resultado": "quebrou"})
    assert r.status_code == 422


async def test_post_adspower_aberto_com_o_usuario_da_sessao_do_pedido(
    db, client, admin, make_user
):
    """Produção, 04/10/2026: todo clique em "Abrir no AdsPower" devolvia 500.

    Lá o `user` é lido na MESMA sessão da rota (`get_current_user` usa a do
    pedido), e a rota faz rollback antes de registrar: ler `user.id` depois
    disso era ida ao banco fora do greenlet (MissingGreenlet). O `auth_as`
    devolve um `user` de OUTRA sessão (a do `db`) e escondia o erro — aqui o
    usuário vem da sessão do pedido, como em produção.
    """
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform="shopee",
            account_name="kfa",
            server="84",
            integration_id=integ.id,
        )
    )
    await db.commit()
    admin_id = admin.id

    async def _da_sessao_do_pedido(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> User:
        return await session.get(User, admin_id)

    for dep in (require_user, require_active_user, require_admin):
        app.dependency_overrides[dep] = _da_sessao_do_pedido

    r = await client.post(
        f"{URL}/adspower/aberto",
        json={"conversa_id": str(conversa.id), "resultado": "aberto", "codigo": None},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"registrado": True, "perfil": "84"}
    # Só o clique, sem conversa: o mesmo rollback, o mesmo registro.
    r = await client.post(
        f"{URL}/adspower/aberto", json={"resultado": "erro", "codigo": "sem_resposta"}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"registrado": True, "perfil": None}


# ─────────────── o painel inteiro ───────────────


async def test_get_painel_monta_os_blocos(db, client, admin, make_user, bling):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal, pedido="250925ABC")
    await _pedido_bling(db, numero="297840", numeroloja="250925ABC", itens=(("dg053.ci", 2),))
    await _produto(db, dono, "dg053.ci", 1)
    await _produto(db, dono, "dg053.sp", 4)
    await _linha_margem(
        db, pedido="297840", sku="dg053.ci", plataforma="amazon", custo=100, valorbase=120
    )
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform="shopee",
            account_name="kfa",
            server="84",
            integration_id=integ.id,
        )
    )
    await db.commit()

    r = await client.get(f"{URL}/conversas/{conversa.id}/painel")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["pedido"]["numero_bling"] == "297840"
    item = p["estoque"]["itens"][0]
    assert (item["sku"], item["saldo"], item["quantidade"], item["cobre"]) == (
        "dg053.ci",
        1,
        2,
        False,
    )
    assert item["saldo_outros_lotes"] == 4
    assert p["estoque"]["fonte"] == "bling"
    assert p["ve_margem"] is True
    assert p["margem"]["margem"] == pytest.approx(0.2)
    assert p["observacoes_bling"]["observacoes"] == "23/09 - restrição de envio"
    assert p["links"]["bling"].endswith("#edit/9001")
    assert p["adspower"]["perfil"] == "84"
    # Envio desligado: o botão de foto vem desligado com o porquê.
    assert p["envio_foto"]["pode"] is False
    assert p["envio_foto"]["codigo"] == "envio_desligado"

    # Bling fora: o painel aparece do mesmo jeito, com o erro só no bloco.
    painel._OBS_LOCAL.clear()
    bling.falha = TimeoutError()
    r = await client.get(f"{URL}/conversas/{conversa.id}/painel?atualizar=1")
    assert r.status_code == 200
    assert r.json()["observacoes_bling"]["codigo"] == "bling_demorou"
    assert r.json()["estoque"]["itens"][0]["saldo"] == 1


async def test_painel_sem_permissao_de_margem(db, client, make_user, auth_as, monkeypatch, bling):
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    u = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    auth_as(u)
    integ, canal = await _loja(db, u)
    conversa = await _conversa(db, integ, canal, pedido="250925ABC")
    await _pedido_bling(db, numero="297840", numeroloja="250925ABC")
    r = await client.get(f"{URL}/conversas/{conversa.id}/painel")
    assert r.status_code == 200, r.text
    assert (r.json()["ve_margem"], r.json()["margem"]) == (False, None)


async def test_painel_sem_pedido_no_bling_usa_o_retrato(db, client, admin, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(
        db,
        integ,
        canal,
        pedido="250925QQQ",
        dados={
            "pedido_mkt": {"itens": [{"sku": "dg053.pi", "quantidade": 1, "titulo": "Celular"}]}
        },
    )
    r = await client.get(f"{URL}/conversas/{conversa.id}/painel")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["pedido"] is None and p["observacoes_bling"] is None
    assert p["estoque"]["fonte"] == "plataforma"
    assert p["estoque"]["itens"][0]["sku"] == "dg053.pi"
    assert p["links"]["plataforma"]["url"].endswith("/portal/sale/order?search=250925QQQ")


async def test_painel_shopee_abre_o_pedido_pelo_order_id_interno(db, client, admin, make_user):
    """O caso da Vortan (02/10/2026): o cartão "avalie o pedido" do chat traz o
    order_id interno; com o par conferido (1 pedido na conversa, data e hora
    batendo com a criação do pedido), o botão abre a página do pedido."""
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(
        db,
        integ,
        canal,
        pedido="26092743U4QU7F",
        dados={
            "pedido_mkt": {"pedido": "26092743U4QU7F", "criado_em": "2026-09-27T01:06:11+08:00"}
        },
    )
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id="crm-1",
            autor="loja",
            origem="externo",
            tipo="outro",
            payload={
                "message_type": "crm_order_rate",
                "content": {"unrated_order_reminder": {"order_id": 244141571124463}},
            },
        )
    )
    await db.commit()
    r = await client.get(f"{URL}/conversas/{conversa.id}/painel")
    assert r.status_code == 200, r.text
    assert r.json()["links"]["plataforma"] == {
        "url": "https://seller.shopee.com.br/portal/sale/order/244141571124463",
        "rotulo": "Abrir na Shopee",
    }


async def test_painel_do_instagram_e_vazio(client, admin):
    r = await client.get(f"{URL}/conversas/ig:{uuid4()}/painel")
    assert r.status_code == 200
    assert r.json()["adspower"]["perfil"] is None
    assert r.json()["envio_foto"]["pode"] is False
