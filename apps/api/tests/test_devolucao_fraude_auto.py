"""Fraude automática (Vinicius, 16/11/2026): o só reembolso pede o vídeo da
embalagem sozinho e, quando ele chega, lança "Não devolvido" e a resposta sai.

O caminho do disparo (chamado, IA, recusa na TikTok / disputa na Shopee) é o de
sempre e tem os testes dele em test_chamados_devolucao.py — aqui, o que é novo:
quem entra, o vídeo, o motivo tirado da alegação, o que fica pra pessoa, e um
caminho inteiro com a TikTok de mentira daqueles testes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    BlingOrder,
    Chamado,
    DevolucaoRastreio,
    Devolution,
    EstoquePedidoVideo,
    StoreInfo,
)
from app.models.chamado import ChamadoMensagem
from app.routers import devolutions as antigo
from app.services import chamados_devolucao as cd
from app.services import devolucao_conferencia as conferencia
from app.services import devolucao_fraude_auto as auto

pytestmark = pytest.mark.asyncio
_SCHEMA = get_settings().database_schema
AGORA = datetime.now(UTC)
VENDA = AGORA - timedelta(days=12)


@pytest.fixture
async def visao(db: AsyncSession):
    """A vw_devolucoes de produção não vem do create_all: uma igual em cima do
    bling_orders, com o nome da loja da store_info (é a `conta` do lançamento)."""
    await db.execute(
        text(
            f"""
            CREATE OR REPLACE VIEW "{_SCHEMA}".vw_devolucoes AS
            SELECT bo.data AS data, bo.numero::text AS pedido_bling,
                   bo.numeroloja::text AS pedido_marketplace,
                   (SELECT si.account_name FROM "{_SCHEMA}".store_info si
                     WHERE si.bling_store_id = bo.loja LIMIT 1) AS loja_nome,
                   NULL::bigint AS bling_loja_id, bo.item_codigo::text AS sku,
                   bo.item_descricao::text AS produto, bo.item_quantidade::int AS quantidade,
                   bo.id AS bling_order_item_id, NULL::text AS nome_destinatario,
                   NULL::text AS cep_destino, NULL::text AS endereco_destino,
                   NULL::text AS numero_destino, NULL::text AS complemento_destino,
                   NULL::text AS bairro_destino, NULL::text AS cidade_destino,
                   NULL::text AS uf_destino
            FROM "{_SCHEMA}".bling_orders bo
            WHERE bo.situacao IN ('83957', '83960', '83961', '83966', '84677', '545902')
            """  # noqa: S608
        )
    )
    await db.commit()
    yield
    await db.execute(text(f'DROP VIEW IF EXISTS "{_SCHEMA}".vw_devolucoes'))
    await db.commit()


@pytest.fixture
def ligado(monkeypatch):
    """Robô ligado e a IA "ligada" com uma resposta de mentira (bate). Sem a IA o
    robô não lança (ver test_ia_desligada_nao_lanca)."""
    monkeypatch.setattr(get_settings(), "devolucao_fraude_auto", auto.LIGADO)
    monkeypatch.setattr(conferencia, "ligada", lambda: True)

    async def _bate(texto):
        return conferencia.Veredito(bate=True, explicacao="ok"), "fake"

    monkeypatch.setattr(conferencia, "perguntar", _bate)


def _ler(caso: auto.Caso, lidos: list | None = None):
    async def _f(session, plataforma, rid, dev):
        if lidos is not None:
            lidos.append(rid)
        return caso

    return _f


def _sem_disparo(monkeypatch) -> list[str]:
    """Troca o disparo (fila do worker) por um registro."""
    agendados: list[str] = []

    async def _agendar(session, ch, dev):
        agendados.append(dev.pedido_bling)

    monkeypatch.setattr(cd, "agendar_disparo", _agendar)
    return agendados


async def _caso(
    db: AsyncSession,
    make_user,
    pedido: str,
    *,
    fonte: str = "shopee",
    acao: str = "SHOPEE_RESPONDER_SOLICITACAO",
    prazo: datetime | None = None,
    fila: str | None = None,
    tipo: str = "REFUND",
    situacao: str = "83957",
    itens: tuple[tuple[str, int], ...] = (("a17.sp", 1),),
    conta: str = "mini",
    loja: str = "88",
    **rast,
) -> None:
    if (
        await db.execute(select(StoreInfo).where(StoreInfo.bling_store_id == loja))
    ).scalar_one_or_none() is None:
        user = await make_user()
        db.add(StoreInfo(user_id=user.id, platform=fonte, account_name=conta, bling_store_id=loja))
    for i, (sku, qtd) in enumerate(itens):
        db.add(
            BlingOrder(
                id=uuid4(),
                numero=pedido,
                numeroloja=f"MKT{pedido}",
                bling_id=int(pedido) * 10 + i,
                situacao=situacao,
                loja=loja,
                data=VENDA,
                aguardando_devolucao_data=(AGORA - timedelta(days=2)).date(),
                item_index=i,
                item_codigo=sku,
                item_descricao=f"Produto {sku}",
                item_quantidade=qtd,
            )
        )
    db.add(
        DevolucaoRastreio(
            pedido_bling=pedido,
            fonte_auto=fonte,
            devolucao_id_auto=f"RET{pedido}",
            devolucao_tipo_auto=tipo,
            acao_auto=acao,
            prazo_acao_auto=prazo or AGORA + timedelta(days=2),
            fila_manual=fila,
            **rast,
        )
    )
    await db.commit()


async def _rast(db: AsyncSession, pedido: str) -> DevolucaoRastreio:
    db.expire_all()
    return await db.get(DevolucaoRastreio, pedido)


async def _lancamentos(db: AsyncSession, pedido: str) -> list[Devolution]:
    db.expire_all()
    return list(
        (await db.execute(select(Devolution).where(Devolution.pedido_bling == pedido))).scalars()
    )


# ── A alegação vira o motivo ─────────────────────────────────────────────


async def test_motivo_da_alegacao() -> None:
    m = auto.motivo_da_alegacao
    assert m("shopee", "NOT_RECEIPT", "") == "Não recebido"
    assert m("shopee", "SUSPICIOUS_PARCEL", "veio vazio") == "Golpe"
    assert m("shopee", "WRONG_ITEM", "") == "Golpe"
    assert m("shopee", "MISSING_ITEM", "") == "Item faltando"
    # O vídeo da embalagem não rebate dano/defeito nem "diferente do anúncio".
    assert m("shopee", "PHYSICAL_DMG", "") is None
    assert m("shopee", "FUNCTIONAL_DMG", "") is None
    assert m("shopee", "NOT_AS_DESCRIBED", "") is None
    # Código que o sistema não conhece: pessoa, mesmo com texto que parece golpe.
    assert m("shopee", "ITEM_DAMAGED", "chegou vazio") is None
    assert m("shopee", "", "chegou vazio") == "Golpe"
    assert m("tiktok", "Produto diferente da descrição", "") is None
    assert m("tiktok", "Não recebi o pedido", "") == "Não recebido"
    assert m("tiktok", "Package not received", "") == "Não recebido"
    assert m("tiktok", "Pacote vazio", "") == "Golpe"
    assert m("tiktok", "Pacote recebido, mas faltam alguns itens", "") == "Item faltando"
    assert m("tiktok", "Recebi produto errado", "") == "Golpe"
    assert m("tiktok", "Produto danificado", "") is None
    assert m("tiktok", "Mudei de ideia", "") is None
    assert m("tiktok", "", "") is None
    # O motivo padronizado vale antes da nota livre do comprador.
    assert m("tiktok", "Pacote vazio", "não recebi o carregador") == "Golpe"
    assert m("tiktok", "", "veio vazio") == "Golpe"


async def test_decidir() -> None:
    d = auto.decidir
    ok = auto.Caso(pendente=True, codigo="WRONG_ITEM", ida_entregue=None)
    assert d("shopee", ok) == ("Golpe", None)
    # Ida não entregue: é extravio, não se contesta.
    motivo, pessoa = d("shopee", auto.Caso(pendente=True, codigo="WRONG_ITEM", ida_entregue=False))
    assert motivo is None and "NÃO aconteceu" in pessoa
    # "Não recebi" só com a entrega confirmada.
    motivo, pessoa = d("shopee", auto.Caso(pendente=True, codigo="NOT_RECEIPT", ida_entregue=None))
    assert motivo is None and "não está confirmada" in pessoa
    assert d("shopee", auto.Caso(pendente=True, codigo="NOT_RECEIPT", ida_entregue=True)) == (
        "Não recebido",
        None,
    )
    motivo, pessoa = d("tiktok", auto.Caso(pendente=True, codigo="Produto danificado"))
    assert motivo is None and pessoa
    # Nada lido: tenta de novo (nem motivo nem pessoa).
    assert d("tiktok", auto.Caso(pendente=True)) == (None, None)


# ── Quem entra ───────────────────────────────────────────────────────────


async def test_so_entra_o_so_reembolso_esperando_a_nossa_resposta(db: AsyncSession, make_user):
    await _caso(db, make_user, "790001")  # entra
    await _caso(db, make_user, "790002", fonte="tiktok", acao="SELLER_RESPOND_REFUND", loja="89")
    await _caso(db, make_user, "790003", prazo=AGORA - timedelta(hours=1))  # prazo vencido
    await _caso(db, make_user, "790004", tipo="RETURN")  # volta pacote: Acompanhamento
    await _caso(db, make_user, "790005", fila="acompanhamento")  # pessoa tirou da Fraude
    # Pessoa pôs na Fraude um caso COM pacote ("chegou vazio"): outro fluxo, fica.
    await _caso(db, make_user, "790006", tipo="RETURN", fila="fraude")
    await _caso(db, make_user, "790007", abrir_chamado=False)  # "Abrir chamado: Não"
    await _caso(db, make_user, "790008", situacao="545902")  # Bling fora de Aguardando
    await _caso(db, make_user, "790009", acao="SHOPEE_CONFERIR_PACOTE")  # não é responder
    await _caso(db, make_user, "790010", fonte="ml", acao="SELLER_RESPOND_REFUND", loja="90")
    await _caso(db, make_user, "790011")
    db.add(Devolution(pedido_bling="790011", conta="mini", sku="a17.sp"))  # já lançado
    await _caso(db, make_user, "790012")
    db.add(Chamado(pedido_bling="790012", origem="devolucao", conta="mini"))  # já tem chamado
    await _caso(
        db, make_user, "790013", resolvido_at=AGORA, resolvido_como="Perdimento"
    )  # resolvido
    await _caso(db, make_user, "790014", fila="fraude")  # só reembolso fixado na Fraude: entra
    await _caso(db, make_user, "790015", acao="SHOPEE_RESPONDER_PROPOSTA")  # negociação: pessoa
    await db.commit()
    assert sorted(await auto.candidatos(db)) == ["790001", "790002", "790014"]


# ── O vídeo ──────────────────────────────────────────────────────────────


async def test_sem_video_le_a_alegacao_e_pede_a_equipe_uma_vez(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(db, make_user, "791001")
    lidos: list[str] = []
    monkeypatch.setattr(
        auto, "_ler_caso", _ler(auto.Caso(pendente=True, codigo="WRONG_ITEM"), lidos)
    )
    r = await auto.rodada(db)
    assert r["video_pedido"] == 1 and r["lancados"] == 0
    rast = await _rast(db, "791001")
    assert rast.video_solicitado_em is not None and rast.video_solicitado_por is None
    assert auto.VIDEO_PEDIDO in rast.observacao
    # A tela da equipe (aba Pedidos) mostra o pedido como vídeo pendente.
    assert (
        antigo._video_status(
            link=rast.video_link,
            solicitado_em=rast.video_solicitado_em,
            enviado_em=rast.video_enviado_em,
            sem_motivo=rast.video_sem_motivo,
        )
        == "pendente"
    )
    # Próxima rodada: não pede de novo nem relê a plataforma, espera.
    r = await auto.rodada(db)
    assert r["video_pedido"] == 0 and r["esperando_video"] == 1 and lidos == ["RET791001"]


async def test_danificado_nao_pede_video_a_equipe(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(db, make_user, "791101")
    monkeypatch.setattr(auto, "_ler_caso", _ler(auto.Caso(pendente=True, codigo="PHYSICAL_DMG")))
    r = await auto.rodada(db)
    assert r["video_pedido"] == 0 and r["pra_pessoa"] == 1
    rast = await _rast(db, "791101")
    assert rast.video_solicitado_em is None and auto.NAO_RESPONDE in rast.observacao


async def test_pedido_de_video_cancelado_nao_volta(db: AsyncSession, make_user, ligado):
    """O robô pediu, alguém cancelou ("cancelar pedido de vídeo"): não pede de novo."""
    await _caso(
        db, make_user, "791201", observacao=f"{auto.MARCA} (16/11 10:00): {auto.VIDEO_PEDIDO}."
    )
    r = await auto.rodada(db)
    assert r["video_pedido"] == 0 and r["pra_pessoa"] == 1
    rast = await _rast(db, "791201")
    assert rast.video_solicitado_em is None
    # Vai pro cartão "Fraude: responder à mão" (a tela procura este pedaço).
    assert auto.CARTAO_A_MAO in rast.observacao and auto.CARTAO_A_MAO in auto.NAO_RESPONDE


async def test_video_da_expedicao_entra_uma_vez_so(db: AsyncSession, make_user, ligado):
    """Usado uma vez e depois recusado/cancelado: o robô não põe o mesmo de volta."""
    await _caso(
        db,
        make_user,
        "791301",
        observacao=f"{auto.MARCA} (16/11 10:00): {auto.ACHADO_EXPEDICAO}.",
        video_solicitado_em=AGORA,
    )
    db.add(EstoquePedidoVideo(pedido_bling="791301", link="https://mega.nz/file/emb#k"))
    await db.commit()
    r = await auto.rodada(db)
    assert r["video_da_expedicao"] == 0
    assert (await _rast(db, "791301")).video_link is None


async def test_shopee_ja_decidido_no_seller_center_nao_espera_resposta(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """Alguém aceitou o reembolso no Seller Center antes do sync de 30 min: o robô
    relê, vê ACCEPTED e não lança."""
    from tests.test_chamados_devolucao import _FakeShopee

    fake = _FakeShopee()

    async def _det(return_sn):
        return {
            "return_sn": return_sn,
            "status": "ACCEPTED",
            "return_solution": 1,
            "needs_logistics": False,
            "reason": "SUSPICIOUS_PARCEL",
        }

    fake.get_return_detail = _det

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(cd, "_shopee_client_para", _c)
    await _caso(
        db, make_user, "791401", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    r = await auto.rodada(db)
    assert r["sem_resposta_pendente"] == 1 and r["lancados"] == 0
    assert fake.disputes == [] and await _lancamentos(db, "791401") == []


async def test_video_da_expedicao_e_usado_e_o_antigo_nao(
    db: AsyncSession, make_user, monkeypatch, visao
):
    monkeypatch.setattr(get_settings(), "devolucao_fraude_auto", auto.SO_VIDEO)
    monkeypatch.setattr(auto, "_ler_caso", _ler(auto.Caso(pendente=True, codigo="WRONG_ITEM")))
    await _caso(db, make_user, "792001")
    db.add(EstoquePedidoVideo(pedido_bling="792001", link="https://mega.nz/file/emb#k"))
    # Mesmo número de pedido de outro ano: o vídeo é de antes da venda — não serve.
    await _caso(db, make_user, "792002")
    velho = VENDA - timedelta(days=400)
    db.add(
        EstoquePedidoVideo(
            pedido_bling="792002",
            link="https://mega.nz/file/velho#k",
            created_at=velho,
            updated_at=velho,
        )
    )
    # Alguém recusou o vídeo ("apagar e pedir de novo"): o da expedição não volta.
    await _caso(
        db,
        make_user,
        "792003",
        video_solicitado_em=AGORA,
        video_refazer_motivo="vídeo de outro pedido",
    )
    db.add(EstoquePedidoVideo(pedido_bling="792003", link="https://mega.nz/file/recusado#k"))
    await db.commit()
    r = await auto.rodada(db)
    assert r["video_da_expedicao"] == 1 and r["video_pedido"] == 1, r
    a = await _rast(db, "792001")
    assert a.video_link == "https://mega.nz/file/emb#k" and a.video_enviado_em is not None
    assert "achado na expedição" in a.observacao
    b = await _rast(db, "792002")
    assert b.video_link is None and b.video_solicitado_em is not None
    c = await _rast(db, "792003")
    assert c.video_link is None
    # so_video: não lança.
    assert await _lancamentos(db, "792001") == []


async def test_equipe_sem_video_fica_pra_pessoa(db: AsyncSession, make_user, ligado):
    await _caso(
        db,
        make_user,
        "793001",
        video_solicitado_em=AGORA - timedelta(hours=5),
        video_enviado_em=AGORA - timedelta(hours=1),
        video_sem_motivo="não gravei",
    )
    r = await auto.rodada(db)
    assert r["pra_pessoa"] == 1 and r["lancados"] == 0
    assert await _lancamentos(db, "793001") == []


# ── O lançamento sozinho ─────────────────────────────────────────────────


async def test_com_video_lanca_nao_devolvido_com_o_motivo_da_alegacao(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db,
        make_user,
        "794001",
        itens=(("a17.sp", 1), ("f105.ci+a001.ci", 1)),
        video_link="https://mega.nz/file/v#k",
        video_enviado_em=AGORA,
    )
    lidos: list[str] = []
    monkeypatch.setattr(
        auto,
        "_ler_caso",
        _ler(
            auto.Caso(pendente=True, codigo="NOT_RECEIPT", texto="não chegou", ida_entregue=True),
            lidos,
        ),
    )
    agendados = _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    linhas = await _lancamentos(db, "794001")
    # Uma linha por peça (o kit abre em peças, como na janela).
    assert sorted(d.sku for d in linhas) == ["a001.ci", "a17.sp", "f105.ci"]
    for d in linhas:
        assert d.condicao_produto == "Não devolvido" and d.motivo_devolucao == "Não recebido"
        assert d.link_envio == "https://mega.nz/file/v#k" and d.conta == "mini"
        assert d.reembolso is True and d.quantidade == 1
        # A observação da linha vai pra plataforma: o sistema não escreve nela.
        assert not d.observacao
    assert lidos == ["RET794001"] and agendados == ["794001"]
    # O chamado nasceu junto, com a abertura pendente esperando o disparo.
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "794001"))).scalar_one()
    assert ch.origem == "devolucao"
    obs = (await _rast(db, "794001")).observacao
    assert conferencia.MARCA_ROBO in obs and "Não recebido" in obs and "não chegou" in obs
    # Já lançado: sai da lista de candidatos.
    assert "794001" not in await auto.candidatos(db)


async def test_caso_que_nao_espera_mais_resposta_nao_lanca(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db, make_user, "794101", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    monkeypatch.setattr(auto, "_ler_caso", _ler(auto.Caso(pendente=False)))
    r = await auto.rodada(db)
    assert r["sem_resposta_pendente"] == 1 and r["lancados"] == 0
    assert await _lancamentos(db, "794101") == []
    assert auto.NAO_RESPONDE not in ((await _rast(db, "794101")).observacao or "")


async def test_ia_desligada_nao_lanca(db: AsyncSession, make_user, ligado, visao, monkeypatch):
    monkeypatch.setattr(conferencia, "ligada", lambda: False)
    await _caso(
        db, make_user, "794201", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    monkeypatch.setattr(auto, "_ler_caso", _ler(auto.Caso(pendente=True, codigo="WRONG_ITEM")))
    r = await auto.rodada(db)
    assert r["lancados"] == 0 and await _lancamentos(db, "794201") == []


async def test_alegacao_de_danificado_fica_pra_pessoa_e_nao_rele(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db, make_user, "795001", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    lidos: list[str] = []
    monkeypatch.setattr(
        auto,
        "_ler_caso",
        _ler(auto.Caso(pendente=True, codigo="PHYSICAL_DMG", texto="tela quebrada"), lidos),
    )
    r = await auto.rodada(db)
    assert r["pra_pessoa"] == 1 and r["lancados"] == 0
    assert await _lancamentos(db, "795001") == []
    assert auto.NAO_RESPONDE in (await _rast(db, "795001")).observacao
    await auto.rodada(db)
    assert len(lidos) == 1  # não relê a plataforma a cada 10 min


async def test_alegacao_nao_lida_tenta_de_novo_sem_marcar(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db, make_user, "795101", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    monkeypatch.setattr(auto, "_ler_caso", _ler(auto.Caso(pendente=True)))
    r = await auto.rodada(db)
    assert r["falhas"] == 1 and r["pra_pessoa"] == 0 and r["lancados"] == 0
    assert auto.NAO_RESPONDE not in ((await _rast(db, "795101")).observacao or "")
    assert "795101" in await auto.candidatos(db)


async def test_pessoa_lancou_no_meio_o_robo_nao_duplica(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db, make_user, "795201", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )

    async def _le_e_alguem_lanca(session, plataforma, rid, dev):
        session.add(
            Devolution(pedido_bling="795201", conta="mini", sku="a17.sp", condicao_produto="Novo")
        )
        await session.commit()
        return auto.Caso(pendente=True, codigo="WRONG_ITEM")

    monkeypatch.setattr(auto, "_ler_caso", _le_e_alguem_lanca)
    _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["lancados"] == 0
    assert [d.condicao_produto for d in await _lancamentos(db, "795201")] == ["Novo"]


async def test_teto_de_lancamentos_por_rodada(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    for n in range(auto.TETO_LANCAMENTOS + 2):
        await _caso(
            db,
            make_user,
            f"7960{n:02d}",
            video_link="https://mega.nz/file/v#k",
            video_enviado_em=AGORA,
        )
    monkeypatch.setattr(auto, "_ler_caso", _ler(auto.Caso(pendente=True, codigo="WRONG_ITEM")))
    _sem_disparo(monkeypatch)
    assert (await auto.rodada(db))["lancados"] == auto.TETO_LANCAMENTOS
    assert (await auto.rodada(db))["lancados"] == 2


async def test_desligado_nao_faz_nada(db: AsyncSession, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "devolucao_fraude_auto", auto.DESLIGADO)
    await _caso(db, make_user, "797001")
    r = await auto.rodada(db)
    assert r["modo"] == "desligado" and r["candidatos"] == 0
    assert (await _rast(db, "797001")).video_solicitado_em is None


# ── O caminho inteiro, com a TikTok e a Shopee de mentira ────────────────


async def _tiktok(db: AsyncSession, make_user, monkeypatch):
    from tests.test_chamados_devolucao import OID_REEMB, RID_REEMB, _FakeTikTokReembolso

    fake = _FakeTikTokReembolso()

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(cd, "_tiktok_client_para", _c)
    monkeypatch.setattr(cd, "ENFILEIRAR", False)  # dispara inline (sem Redis)
    user = await make_user()
    db.add(StoreInfo(user_id=user.id, platform="tiktok", account_name="mini", bling_store_id="77"))
    db.add(
        BlingOrder(
            id=uuid4(),
            numero="296936",
            numeroloja=OID_REEMB,
            bling_id=1,
            situacao="83957",
            loja="77",
            data=VENDA,
            aguardando_devolucao_data=(AGORA - timedelta(days=1)).date(),
            item_index=0,
            item_codigo="b001.26",
            item_descricao="Mala Listrada tamanho 26",
            item_quantidade=1,
        )
    )
    db.add(
        DevolucaoRastreio(
            pedido_bling="296936",
            fonte_auto="tiktok",
            devolucao_id_auto=RID_REEMB,
            devolucao_tipo_auto="REFUND",
            devolucao_status_auto="RETURN_OR_REFUND_REQUEST_PENDING",
            acao_auto="SELLER_RESPOND_REFUND",
            prazo_acao_auto=AGORA + timedelta(days=1),
            video_link="https://mega.nz/file/embalagem#k",
            video_enviado_em=AGORA,
        )
    )
    await db.commit()
    return fake, RID_REEMB


async def _abertura(db: AsyncSession, pedido: str) -> ChamadoMensagem:
    db.expire_all()
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == pedido))).scalar_one()
    return (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalar_one()


async def test_tiktok_video_chega_e_a_recusa_do_reembolso_sai(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """Vídeo da equipe chegou → relê o caso → lança sozinho → chamado → a IA
    confere → a TikTok recebe a recusa do só reembolso com o vídeo no texto."""
    fake, rid = await _tiktok(db, make_user, monkeypatch)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    linhas = await _lancamentos(db, "296936")
    # "Package received but missing item" (o motivo da TikTok de mentira).
    assert [(d.condicao_produto, d.motivo_devolucao) for d in linhas] == [
        ("Não devolvido", "Item faltando")
    ]
    assert len(fake.rejects) == 1
    rj = fake.rejects[0]
    assert (rj["return_id"], rj["decision"]) == (rid, "REJECT_REFUND")
    assert "https://mega.nz/file/embalagem#k" in rj["comment"]
    assert (await _abertura(db, "296936")).status == "enviada"


async def test_lancamento_do_robo_sem_resposta_da_ia_segura(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    fake, _rid = await _tiktok(db, make_user, monkeypatch)

    async def _muda(texto):
        return None, "fake"

    monkeypatch.setattr(conferencia, "perguntar", _muda)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert fake.rejects == []
    msg = await _abertura(db, "296936")
    assert msg.status == "segurada"
    conf = (await _rast(db, "296936")).conferencia
    assert conf["resultado"] == "nao_bate" and "Fraude automática" in conf["explicacao"]


async def test_lancamento_do_robo_com_ia_desligada_no_disparo_segura(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """A IA foi desligada entre o lançamento e o disparo: segura em vez de sair."""
    fake, _rid = await _tiktok(db, make_user, monkeypatch)
    chamadas = {"n": 0}

    def _ligada():
        chamadas["n"] += 1
        return chamadas["n"] == 1  # liga só na checagem do lançamento

    monkeypatch.setattr(conferencia, "ligada", _ligada)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert fake.rejects == []
    assert (await _abertura(db, "296936")).status == "segurada"


async def test_shopee_pacote_vazio_video_chega_e_a_disputa_sai(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """Shopee: comprador alega pacote vazio (só reembolso) → lança Golpe sozinho
    → a disputa sai com o cartão do vídeo da embalagem e o texto do SÓ reembolso
    (nada de "Recebemos a devolução")."""
    from tests.test_chamados_devolucao import _FakeShopee

    fake = _FakeShopee()

    async def _det(return_sn):
        return {
            "return_sn": return_sn,
            "status": "REQUESTED",
            "return_solution": 1,
            "needs_logistics": False,
            "reason": "SUSPICIOUS_PARCEL",
            "text_reason": "recebi a caixa vazia",
            "seller_compensation": {"seller_compensation_status": "PENDING_REQUEST"},
        }

    async def _reasons(return_sn):
        return [
            {
                "dispute_reason": 53,
                "dispute_requirement": "Envie imagens dos itens enviados",
                "evidence_module_list": [
                    {
                        "module_index": 1,
                        "requirement": "Fotos/vídeos da expedição",
                        "is_required": True,
                    }
                ],
            },
        ]

    fake.get_return_detail = _det
    fake.get_return_dispute_reason = _reasons

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(cd, "_shopee_client_para", _c)
    monkeypatch.setattr(cd, "ENFILEIRAR", False)
    await _caso(
        db,
        make_user,
        "798001",
        conta="mega",
        loja="91",
        video_link="https://mega.nz/file/caixa798#k",
        video_enviado_em=AGORA,
    )
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    linhas = await _lancamentos(db, "798001")
    assert [(d.condicao_produto, d.motivo_devolucao) for d in linhas] == [
        ("Não devolvido", "Golpe")
    ]
    assert len(fake.disputes) == 1, fake.disputes
    d = fake.disputes[0]
    assert d["return_sn"] == "RET798001" and d["reason"] == 53
    assert d["image_list"] and d["image_list"][0]["image_url"]  # o cartão do vídeo
    assert "https://mega.nz/file/caixa798#k" in d["text"]
    assert "SEM devolução" in d["text"] and "Recebemos a devolução" not in d["text"], d["text"]
    assert "Contestamos a alegação" in d["text"]


async def test_worker_agenda_a_fraude_a_cada_10_minutos() -> None:
    from app.worker import WorkerSettings

    jobs = [c for c in WorkerSettings.cron_jobs if c.name.endswith("devolucao_fraude_auto")]
    assert len(jobs) == 1 and jobs[0].minute == set(range(7, 60, 10))
