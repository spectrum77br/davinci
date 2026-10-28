"""Pedido de garantia (28/10/2026): o Painel mostra só quem PEDIU a garantia —
a mensagem do cliente com defeito, o SAC do site, a devolução por defeito, o
atendimento registrado e a garantia cadastrada à mão. A venda sem pedido fica
por trás (o "vendidas" do Resumo)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Garantia, GarantiaLog, UserRole
from app.models.atendimento import AtendimentoPedidoComprador
from app.models.devolution import Devolution
from app.services import garantia as svc
from app.services import garantia_pedido as gp
from tests.test_garantia_auto import COMPRA, _conversa, _garantias, _loja, _nf, _pedido, _rodar
from tests.test_garantia_sac import _formulario, _protocolo

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def _hoje(monkeypatch):
    monkeypatch.setattr(svc, "hoje", lambda: HOJE)


def test_pede_garantia_frase_a_frase():
    # Revisão nos dados reais (28/10): o que é e o que não é pedido.
    for texto in [
        "Bom dia, o celular parou de funcionar. Não liga de jeito nenhum",
        "Comprei este smartphone e sem tomar água nem quedas apresentou ela linha estranha hj",
        "ao menor queda já deu problema na tela",
        "chegou quebrado a tela",
        "Celular oukitel c 68 plus,n funciona, apagou do nada",
        "O touch da tela não está funcionando!",
        "A air fryer parou de funcionar",
        "a airfryer não esquenta mais",
        "Eca bateria não segura carga",
        "não sei oq aconteceu, parou de funcionar o áudio como faço pra acionar a garantia?",
        "O carregador veio com defeito",
    ]:
        assert gp.pede_garantia(texto), texto
    for texto in [
        "Preciso de um atendimento e não responde",
        "Já que não respondeu mais vou fazer o cancelamento",
        "conserteza voltarei sim",
        "No momento defeito nenhum, é só pra saber a validade da garantia msm",
        "Não é defeito a fonte veio com entrada de tipoc",
        "Ok o produto não vem com defeito",
        "Imagina se o produto estiver com defeito.",
        "Este aparelho se ficar sem uso por alguns dias ele desliga sozinho?",
        "mas se esse durante o ano der um defeito o que é que eu vou fazer me explique",
        "Ele nao esquenta não ne?",
        "Quanto tempo demora pra chegar ?",
        "o rastreio parou",
        "veio sem o carregador",
        "a caixa veio amassada",
        "tem garantia?",
        "",
        None,
    ]:
        assert not gp.pede_garantia(texto), texto


async def _a_garantia(db) -> Garantia:
    [g] = await _garantias(db)
    await db.refresh(g)
    return g


async def test_mensagem_de_defeito_marca_o_pedido(db):
    await _pedido(db)
    await _nf(db)
    quando = COMPRA + timedelta(days=10)
    await _conversa(
        db,
        pedido="261001ABCD1234",
        msgs=[
            ("cliente", "tem garantia?", COMPRA + timedelta(days=2), ()),
            ("loja", "tem sim, 1 ano", COMPRA + timedelta(days=2, hours=1), ()),
            ("cliente", "o celular parou de funcionar", quando, ()),
        ],
    )
    r = await _rodar(db)
    assert r.pedidos == 1
    g = await _a_garantia(db)
    assert (g.solicitada_em, g.solicitacao_origem) == (quando, gp.ORIGEM_MENSAGEM)
    acoes = list(
        (
            await db.execute(select(GarantiaLog.acao).where(GarantiaLog.garantia_id == g.id))
        ).scalars()
    )
    assert "pedido_automatico" in acoes
    assert (await _rodar(db)).pedidos == 0  # já marcada: não marca de novo


async def test_antes_da_compra_e_pergunta_nao_marcam(db):
    await _pedido(db)
    await _nf(db)
    await _conversa(
        db,
        pedido="261001ABCD1234",
        msgs=[
            ("cliente", "esse modelo trava muito?", COMPRA + timedelta(days=3), ()),
            ("cliente", "o meu antigo parou de funcionar", COMPRA - timedelta(days=2), ()),
            ("loja", "o aparelho não liga? mande um vídeo", COMPRA + timedelta(days=4), ()),
        ],
    )
    await _rodar(db)
    assert (await _a_garantia(db)).solicitada_em is None


async def test_conversa_do_comprador_para_no_proximo_pedido(db, make_user):
    """Shopee/TikTok: uma conversa por comprador. Depois do PRÓXIMO pedido dele,
    o defeito pode ser do outro aparelho — não marca esta garantia."""
    loja = await _loja(db, await make_user(role=UserRole.ADMIN))
    novo = COMPRA + timedelta(days=15)
    await _conversa(
        db,
        integ=loja,
        comprador="88",
        msgs=[("cliente", "o celular não liga", novo + timedelta(days=2), ())],
    )
    for pedido, criado in (("261001ABCD1234", COMPRA), ("261016NOVO0002", novo)):
        db.add(
            AtendimentoPedidoComprador(
                integration_id=loja,
                plataforma="shopee",
                comprador_id="88",
                pedido=pedido,
                criado_em=criado,
            )
        )
    await db.commit()
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    assert (await _a_garantia(db)).solicitada_em is None


async def test_devolucao_de_defeito_marca_e_bloqueado_nao(db):
    await _pedido(db)
    await _nf(db)
    await _pedido(db, numero="310002", numeroloja="261001ABCD9999")
    await _nf(db, pedido="310002", numero="2612", doc="11144477735")
    # `data` é a data da VENDA (o módulo Devoluções copia do pedido); o pedido
    # de garantia é a hora em que a devolução foi LANÇADA (revisão 28/10).
    lancada = datetime(2026, 10, 9, 13, tzinfo=UTC)
    db.add_all(
        [
            Devolution(
                pedido_bling="310001",
                conta="Loja Teste",
                motivo_devolucao="Dano funcional / Não funciona",
                data=COMPRA,
                created_at=lancada,
            ),
            # O cliente desistiu e devolveu com a senha dele: não é defeito,
            # nem quando vai para a manutenção desbloquear.
            Devolution(
                pedido_bling="310002",
                conta="Loja Teste",
                motivo_devolucao="Bloqueado",
                manutencao=True,
                data=COMPRA,
                created_at=lancada,
            ),
        ]
    )
    await db.commit()
    await _rodar(db)
    por_pedido = {g.pedido_bling: g for g in await _garantias(db)}
    for g in por_pedido.values():
        await db.refresh(g)
    assert por_pedido["310001"].solicitacao_origem == gp.ORIGEM_DEVOLUCAO
    assert por_pedido["310001"].solicitada_em == lancada
    assert por_pedido["310002"].solicitada_em is None


async def test_chat_da_shopee_ligado_pelo_pedido_tambem_para_no_proximo_pedido(db, make_user):
    """O chat da Shopee é do comprador mesmo quando ligou pelo nº do pedido."""
    loja = await _loja(db, await make_user(role=UserRole.ADMIN))
    novo = COMPRA + timedelta(days=15)
    await _conversa(
        db,
        pedido="261001ABCD1234",
        integ=loja,
        comprador="88",
        msgs=[("cliente", "o celular não liga", novo + timedelta(days=2), ())],
    )
    for pedido, criado in (("261001ABCD1234", COMPRA), ("261016NOVO0002", novo)):
        db.add(
            AtendimentoPedidoComprador(
                integration_id=loja,
                plataforma="shopee",
                comprador_id="88",
                pedido=pedido,
                criado_em=criado,
            )
        )
    await db.commit()
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    assert (await _a_garantia(db)).solicitada_em is None


async def test_marca_em_lotes(db, monkeypatch):
    """As candidatas vão às fontes em lotes (o teto de parâmetros do asyncpg)."""
    monkeypatch.setattr(gp, "_LOTE", 1)
    await _pedido(db)
    await _nf(db)
    await _pedido(db, numero="310002", numeroloja="261001ABCD9999")
    await _nf(db, pedido="310002", numero="2612", doc="11144477735")
    for pedido in ("261001ABCD1234", "261001ABCD9999"):
        await _conversa(
            db,
            pedido=pedido,
            msgs=[("cliente", "parou de funcionar", COMPRA + timedelta(days=5), ())],
        )
    assert (await _rodar(db)).pedidos == 2


async def test_sac_ligado_marca_o_pedido(db):
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    await _protocolo(db, _formulario(), autor="cliente")
    await _rodar(db)
    g = await _a_garantia(db)
    assert g.solicitacao_origem == gp.ORIGEM_SAC


async def test_painel_so_pedidos_busca_acha_todas_e_a_pessoa_corrige(
    client, db, make_user, auth_as
):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    [g] = await _garantias(db)

    async def lista(**corpo):
        r = await client.post("/api/garantias/lista", json=corpo)
        assert r.status_code == 200, r.text
        return r.json()

    # Sem pedido: fora do Painel; a busca e o "todas" acham.
    assert (await lista())["itens"] == []
    assert (await lista())["indicadores"]["vendidas"] == 1
    [item] = (await lista(busca="310001"))["itens"]
    assert (item["pedido_em"], item["nao_e_pedido"]) == (None, False)
    assert len((await lista(mostrar="todas"))["itens"]) == 1

    # A pessoa marca: entra no Painel.
    r = await client.put(f"/api/garantias/{g.id}/pedido", json={"pedido": True})
    assert r.status_code == 200, r.text
    assert r.json()["pedido_origem"] == gp.ORIGEM_PESSOA
    assert [i["id"] for i in (await lista())["itens"]] == [g.id]

    # "Não é pedido de garantia": sai, e o robô não marca de novo nem com defeito novo.
    r = await client.put(f"/api/garantias/{g.id}/pedido", json={"pedido": False})
    assert r.json()["nao_e_pedido"] is True
    assert (await lista())["itens"] == []
    await _conversa(
        db,
        pedido="261001ABCD1234",
        msgs=[("cliente", "parou de funcionar", COMPRA + timedelta(days=12), ())],
    )
    await _rodar(db)
    assert (await lista())["itens"] == []
    acoes = list(
        (
            await db.execute(select(GarantiaLog.acao).where(GarantiaLog.garantia_id == g.id))
        ).scalars()
    )
    assert {"marcou_pedido", "tirou_pedido"} <= set(acoes)
    assert (
        await client.put(f"/api/garantias/{g.id}/pedido", json={"pedido": "talvez"})
    ).status_code == 422


def test_marcar_pela_pessoa_desfaz_o_nao_e_pedido_e_guarda_o_mais_antigo():
    g = Garantia(origem="robo")
    antes = datetime(2026, 10, 1, tzinfo=UTC)
    g.solicitada_em, g.solicitacao_origem = antes, gp.ORIGEM_MENSAGEM
    g.nao_solicitada_em = datetime(2026, 10, 5, tzinfo=UTC)
    gp.marcar_pela_pessoa(g, datetime(2026, 10, 9, tzinfo=UTC), gp.ORIGEM_ATENDIMENTO)
    assert (g.solicitada_em, g.solicitacao_origem, g.nao_solicitada_em) == (
        antes,
        gp.ORIGEM_MENSAGEM,
        None,
    )
    nova = Garantia(origem="robo")
    gp.marcar_pela_pessoa(nova, antes, gp.ORIGEM_ATENDIMENTO)
    assert (nova.solicitada_em, nova.solicitacao_origem) == (antes, gp.ORIGEM_ATENDIMENTO)
