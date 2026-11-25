"""Fraude automática (Vinicius, 16/11/2026): todo pedido da Fraude pede o vídeo da
embalagem sozinho e, quando ele chega, a IA lê o que o comprador alegou e escreve a
defesa; o robô lança "Não devolvido" com o motivo da alegação e o chamado sai.

O caminho do disparo (chamado, conferência, recusa na TikTok / disputa na Shopee) é
o de sempre e tem os testes dele em test_chamados_devolucao.py — aqui, o que é novo:
quem entra, o vídeo, a leitura da IA (de mentira), o que fica pra pessoa, o "só
propor" e caminhos inteiros com a TikTok e a Shopee de mentira daqueles testes.
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
    UserRole,
)
from app.models.chamado import ChamadoMensagem
from app.routers import devolutions as antigo
from app.services import chamados_devolucao as cd
from app.services import devolucao_conferencia as conferencia
from app.services import devolucao_fraude_auto as auto
from app.services import devolucao_fraude_defesa as defesa_ia

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


DEFESA = (
    "O comprador alega que recebeu um sabonete no lugar do celular; o vídeo mostra o "
    "celular conferido, embalado e lacrado antes do envio."
)


def _ia(
    monkeypatch,
    alegacao: str | None = "vazio_ou_outro_produto",
    *,
    defesa: str = DEFESA,
    pessoa: str = "",
    lidos: list | None = None,
) -> None:
    """A IA da defesa de mentira. `alegacao=None` = a IA não respondeu."""

    async def _f(texto):
        if lidos is not None:
            lidos.append(texto)
        if alegacao is None:
            return None, "fake"
        return (
            defesa_ia.Resposta(
                alegacao=alegacao,
                resumo_alegacao="recebeu um sabonete no lugar do celular",
                defesa=defesa,
                precisa_pessoa=bool(pessoa),
                por_que=pessoa,
            ),
            "fake",
        )

    monkeypatch.setattr(defesa_ia, "perguntar", _f)


@pytest.fixture
def ligado(monkeypatch):
    """Robô ligado (lançando, sem o "só propor") e as duas IAs de mentira: a da
    defesa (vazio/outro produto) e a conferência (bate). Sem a IA o robô não lança
    (ver test_ia_desligada_nao_lanca)."""
    monkeypatch.setattr(get_settings(), "devolucao_fraude_auto", auto.LIGADO)
    monkeypatch.setattr(auto, "SO_PROPOR", False)
    monkeypatch.setattr(conferencia, "ligada", lambda: True)

    async def _bate(texto):
        return conferencia.Veredito(bate=True, explicacao="ok"), "fake"

    monkeypatch.setattr(conferencia, "perguntar", _bate)
    _ia(monkeypatch)


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


# ── A leitura da IA (defesa) ─────────────────────────────────────────────

_OK = auto.Caso(
    situacao=auto.RESPONDER, codigo="WRONG_ITEM", texto="veio um sabonete", ida_entregue=True
)


def _fatos(**kw) -> defesa_ia.Fatos:
    base = {
        "plataforma": "Shopee",
        "situacao": defesa_ia.RESPONDER,
        "codigo": "WRONG_ITEM",
        "texto": "veio um sabonete",
        "produtos": ("Celular A17",),
        "ida_entregue": True,
        "entregue_em": "10/11",
    }
    return defesa_ia.Fatos(**(base | kw))


def _resp(
    alegacao: str = "vazio_ou_outro_produto", defesa: str = DEFESA, pessoa: str = ""
) -> defesa_ia.Resposta:
    return defesa_ia.Resposta(
        alegacao=alegacao,
        resumo_alegacao="recebeu um sabonete",
        defesa=defesa,
        precisa_pessoa=bool(pessoa),
        por_que=pessoa,
    )


async def test_a_alegacao_vira_o_motivo_e_a_defesa_vai_inteira() -> None:
    d = defesa_ia.decidir
    r = d(_fatos(), _resp())
    assert (r.motivo, r.defesa, r.pessoa) == ("Golpe", DEFESA, None)
    assert d(_fatos(), _resp("faltou_item")).motivo == "Item faltando"
    assert d(_fatos(), _resp("nao_recebeu")).motivo == "Não recebido"
    # Dano/defeito e "diferente do anúncio" também viram defesa (307238, 16/11).
    assert d(_fatos(), _resp("danificado_ou_defeito")).motivo == "Danificado (Outros)"
    assert d(_fatos(), _resp("diferente_do_anuncio")).motivo == "Golpe"
    # O que não se entende fica com a pessoa, com o porquê.
    r = d(_fatos(), _resp("outra", pessoa="o comprador só diz que desistiu"))
    assert r.motivo is None and r.pessoa == "o comprador só diz que desistiu"
    assert d(_fatos(), _resp("outra")).pessoa
    # A entrega do pedido NÃO aconteceu: extravio, nunca contesta.
    r = d(_fatos(ida_entregue=False), _resp())
    assert r.motivo is None and "NÃO aconteceu" in r.pessoa
    # "Não recebi" só com a entrega confirmada.
    r = d(_fatos(ida_entregue=None), _resp("nao_recebeu"))
    assert r.motivo is None and "não está confirmada" in r.pessoa
    # A IA pediu uma pessoa mesmo com a alegação conhecida.
    assert d(_fatos(), _resp(pessoa="a alegação não bate com o pedido")).pessoa
    # Defesa vazia ou curta demais: pessoa.
    assert d(_fatos(), _resp(defesa="ok")).pessoa
    # Sem resposta da IA: nada — tenta de novo na próxima rodada.
    assert d(_fatos(), None) == defesa_ia.Decisao()


async def test_dano_e_diferente_do_anuncio_passam_pela_ia_e_viram_defesa() -> None:
    """307238 (16/11): "O item não corresponde à descrição" caía numa trava fixa e ia
    pra pessoa; agora a IA lê e a loja se defende com o vídeo."""
    perguntas: list[str] = []

    def _p(alegacao: str):
        async def _f(texto):
            perguntas.append(texto)
            return _resp(alegacao), "fake"

        return _f

    r = await defesa_ia.analisar(
        _fatos(plataforma="TikTok Shop", codigo="O item não corresponde à descrição"),
        _p("diferente_do_anuncio"),
    )
    assert r.motivo == "Golpe" and r.defesa == DEFESA and "não corresponde" in perguntas[-1]
    r = await defesa_ia.analisar(_fatos(codigo="PHYSICAL_DMG"), _p("danificado_ou_defeito"))
    assert r.motivo == "Danificado (Outros)"
    # Sem alegação nenhuma: não pergunta (tenta de novo).
    antes = len(perguntas)
    assert await defesa_ia.analisar(_fatos(codigo="", texto=""), _p("outra")) == defesa_ia.Decisao()
    assert len(perguntas) == antes


async def test_defesa_longa_e_cortada_numa_frase_inteira() -> None:
    longa = "O comprador alega que recebeu um sabonete. " + (
        "O vídeo mostra o celular conferido e lacrado antes do envio. " * 8
    )
    r = defesa_ia.decidir(_fatos(), _resp(defesa=longa))
    assert len(r.defesa) <= defesa_ia.MAX_CHARS_DEFESA and r.defesa.endswith(".")


async def test_entrada_da_ia_tem_os_fatos_e_a_situacao() -> None:
    t = defesa_ia.entrada(
        _fatos(situacao=defesa_ia.PAGO, transportadora="SPX", comprador_mandou_prova=False)
    )
    assert "JÁ REEMBOLSOU" in t and "WRONG_ITEM" in t and "veio um sabonete" in t
    assert "entregue ao comprador em 10/11 pela SPX" in t and "Celular A17" in t
    assert "mandou foto ou vídeo: não" in t
    t = defesa_ia.entrada(_fatos(prazo="18/11 14:00", ida_entregue=False))
    assert "ESPERA a nossa resposta até 18/11 14:00" in t and "NÃO foi entregue" in t
    assert "ANALISANDO" in defesa_ia.entrada(_fatos(situacao=defesa_ia.EM_ANALISE))


async def test_resposta_da_ia_lida_do_json() -> None:
    class _Bloco:
        type = "text"
        text = (
            '{"alegacao": "faltou_item", "resumo_alegacao": "faltou o carregador", '
            '"defesa": "x", "precisa_pessoa": false, "por_que": ""}'
        )

    class _Msg:
        stop_reason = "end_turn"
        content = [_Bloco()]

    assert defesa_ia.resposta_da_mensagem(_Msg()).alegacao == "faltou_item"
    _Bloco.text = (
        '{"alegacao": "inventada", "resumo_alegacao": "", "defesa": "", '
        '"precisa_pessoa": false, "por_que": ""}'
    )
    assert defesa_ia.resposta_da_mensagem(_Msg()) is None
    _Msg.stop_reason = "max_tokens"
    assert defesa_ia.resposta_da_mensagem(_Msg()) is None
    assert defesa_ia.esquema()["properties"]["alegacao"]["enum"] == list(defesa_ia.ALEGACOES)


# ── Quem entra ───────────────────────────────────────────────────────────


async def test_entra_todo_so_reembolso_da_fraude_com_prazo_ou_sem(db: AsyncSession, make_user):
    await _caso(db, make_user, "790001")  # prazo correndo
    await _caso(db, make_user, "790002", fonte="tiktok", acao="SELLER_RESPOND_REFUND", loja="89")
    # Prazo vencido ou nada pendente (a plataforma já reembolsou / está analisando):
    # entra também — "painel fraude tem que abrir chamado".
    await _caso(db, make_user, "790003", prazo=AGORA - timedelta(hours=1))
    await _caso(db, make_user, "790009", acao="", prazo=AGORA - timedelta(days=3))
    await _caso(db, make_user, "790004", tipo="RETURN")  # volta pacote: Acompanhamento
    await _caso(db, make_user, "790005", fila="acompanhamento")  # pessoa tirou da Fraude
    # Pessoa pôs na Fraude um caso COM pacote ("chegou vazio"): outro fluxo, fica.
    await _caso(db, make_user, "790006", tipo="RETURN", fila="fraude")
    await _caso(db, make_user, "790007", abrir_chamado=False)  # "Abrir chamado: Não"
    await _caso(db, make_user, "790008", situacao="545902")  # Bling fora de Aguardando
    await _caso(db, make_user, "790010", fonte="ml", acao="SELLER_RESPOND_REFUND", loja="90")
    await _caso(db, make_user, "790011")
    db.add(Devolution(pedido_bling="790011", conta="mini", sku="a17.sp"))  # já lançado
    await _caso(db, make_user, "790012")
    db.add(Chamado(pedido_bling="790012", origem="devolucao", conta="mini"))  # já tem chamado
    await _caso(
        db, make_user, "790013", resolvido_at=AGORA, resolvido_como="Perdimento"
    )  # resolvido
    await _caso(db, make_user, "790014", fila="fraude")  # só reembolso fixado na Fraude: entra
    await db.commit()
    lista = await auto.candidatos(db)
    assert sorted(lista) == ["790001", "790002", "790003", "790009", "790014"]
    # Quem a plataforma ainda espera responder vem primeiro (o teto da rodada).
    assert set(lista[:3]) == {"790001", "790002", "790014"}


# ── O vídeo ──────────────────────────────────────────────────────────────


async def test_sem_video_pede_a_equipe_sem_ler_a_plataforma(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(db, make_user, "791001")
    lidos: list[str] = []
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK, lidos))
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
    # Próxima rodada: não pede de novo, espera; a plataforma nunca foi lida.
    r = await auto.rodada(db)
    assert r["video_pedido"] == 0 and r["esperando_video"] == 1 and lidos == []


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


async def test_video_da_expedicao_e_usado_e_o_antigo_nao(
    db: AsyncSession, make_user, monkeypatch, visao
):
    monkeypatch.setattr(get_settings(), "devolucao_fraude_auto", auto.SO_VIDEO)
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
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


async def test_com_video_a_ia_escreve_a_defesa_e_o_robo_lanca(
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
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK, lidos))
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    agendados = _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    linhas = await _lancamentos(db, "794001")
    # Uma linha por peça (o kit abre em peças, como na janela).
    assert sorted(d.sku for d in linhas) == ["a001.ci", "a17.sp", "f105.ci"]
    for d in linhas:
        # "Veio um sabonete" é Golpe — nunca "Não recebido".
        assert d.condicao_produto == "Não devolvido" and d.motivo_devolucao == "Golpe"
        assert d.link_envio == "https://mega.nz/file/v#k" and d.conta == "mini"
        assert d.reembolso is True and d.quantidade == 1
        # A defesa é a "Observação pro chamado": vai no texto da plataforma.
        assert d.observacao == DEFESA
    assert lidos == ["RET794001"] and agendados == ["794001"]
    # A IA leu a alegação, a situação e os produtos do pedido.
    assert "veio um sabonete" in perguntas[0] and "ESPERA a nossa resposta" in perguntas[0]
    assert "Produto a17.sp" in perguntas[0]
    # O chamado nasceu junto, com a abertura pendente esperando o disparo.
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "794001"))).scalar_one()
    assert ch.origem == "devolucao"
    obs = (await _rast(db, "794001")).observacao
    assert conferencia.MARCA_ROBO in obs and "Golpe" in obs and "sabonete" in obs
    # Já lançado: sai da lista de candidatos.
    assert "794001" not in await auto.candidatos(db)


async def test_ja_reembolsado_tambem_ganha_a_defesa(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db,
        make_user,
        "794051",
        acao="",
        prazo=AGORA - timedelta(days=2),
        video_link="https://mega.nz/file/v#k",
        video_enviado_em=AGORA,
    )
    monkeypatch.setattr(
        auto,
        "_ler_caso",
        _ler(auto.Caso(situacao=auto.PAGO, codigo="SUSPICIOUS_PARCEL", ida_entregue=True)),
    )
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert "JÁ REEMBOLSOU" in perguntas[0]
    assert [d.motivo_devolucao for d in await _lancamentos(db, "794051")] == ["Golpe"]


async def test_cancelado_pelo_comprador_nao_lanca_nem_marca(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db, make_user, "794101", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    monkeypatch.setattr(auto, "_ler_caso", _ler(auto.Caso(situacao=auto.CANCELADO)))
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    r = await auto.rodada(db)
    assert r["sem_resposta_pendente"] == 1 and r["lancados"] == 0 and perguntas == []
    assert await _lancamentos(db, "794101") == []
    assert auto.NAO_RESPONDE not in ((await _rast(db, "794101")).observacao or "")


async def test_ja_contestado_ou_negociacao_fica_pra_pessoa_sem_perguntar_a_ia(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    for pedido in ("794151", "794152"):
        await _caso(
            db, make_user, pedido, video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
        )

    async def _por_pedido(session, plataforma, rid, dev):
        return auto.Caso(situacao=auto.CONTESTADO if rid == "RET794151" else auto.NEGOCIACAO)

    monkeypatch.setattr(auto, "_ler_caso", _por_pedido)
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    r = await auto.rodada(db)
    assert r["pra_pessoa"] == 2 and r["lancados"] == 0 and perguntas == []
    assert "contestado" in (await _rast(db, "794151")).observacao
    assert "proposta" in (await _rast(db, "794152")).observacao
    for pedido in ("794151", "794152"):
        assert auto.NAO_RESPONDE in (await _rast(db, pedido)).observacao


async def test_ia_desligada_nao_lanca(db: AsyncSession, make_user, ligado, visao, monkeypatch):
    monkeypatch.setattr(conferencia, "ligada", lambda: False)
    await _caso(
        db, make_user, "794201", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
    r = await auto.rodada(db)
    assert r["lancados"] == 0 and await _lancamentos(db, "794201") == []


async def test_alegacao_que_a_ia_nao_entende_fica_pra_pessoa_e_nao_rele(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db, make_user, "795001", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    lidos: list[str] = []
    caso = auto.Caso(situacao=auto.RESPONDER, codigo="OTHER", texto="mudei de ideia")
    monkeypatch.setattr(auto, "_ler_caso", _ler(caso, lidos))
    _ia(monkeypatch, "outra", pessoa="o comprador só diz que mudou de ideia")
    r = await auto.rodada(db)
    assert r["pra_pessoa"] == 1 and r["lancados"] == 0
    assert await _lancamentos(db, "795001") == []
    obs = (await _rast(db, "795001")).observacao
    assert auto.NAO_RESPONDE in obs and "mudou de ideia" in obs
    await auto.rodada(db)
    assert len(lidos) == 1  # não relê a plataforma a cada 10 min


async def test_dano_com_video_lanca_danificado_e_a_disputa_sai_com_o_cartao(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """Sem devolução não há foto do dano: o cartão do vídeo da embalagem é a prova
    (exige_foto não vale pra "Não devolvido")."""
    from tests.test_chamados_devolucao import _FakeShopee

    fake = _FakeShopee()

    async def _det(return_sn):
        return {
            "return_sn": return_sn,
            "status": "REQUESTED",
            "return_solution": 1,
            "needs_logistics": False,
            "reason": "PHYSICAL_DMG",
            "text_reason": "veio quebrada",
        }

    async def _reasons(return_sn):
        return [
            {
                "dispute_reason": 43,  # "enviei em bom estado" (tabela da Shopee)
                "dispute_reason_text": "I have shipped out the item in good working condition",
                "evidence_module_list": [
                    {"module_index": 1, "requirement": "Fotos", "is_required": True}
                ],
            }
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
        "798301",
        conta="mega",
        loja="94",
        video_link="https://mega.nz/file/caixa798301#k",
        video_enviado_em=AGORA,
    )
    _ia(monkeypatch, "danificado_ou_defeito")
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert [d.motivo_devolucao for d in await _lancamentos(db, "798301")] == ["Danificado (Outros)"]
    msg = await _abertura(db, "798301")
    assert msg.status == "enviada", (msg.status, msg.erro)
    d = fake.disputes[0]
    assert d["reason"] == 43 and d["image_list"] and d["image_list"][0]["image_url"]  # o cartão


async def test_ia_sem_resposta_tenta_de_novo_sem_marcar(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db, make_user, "795101", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
    _ia(monkeypatch, None)
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
        return _OK

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
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
    _sem_disparo(monkeypatch)
    assert (await auto.rodada(db))["lancados"] == auto.TETO_LANCAMENTOS
    assert (await auto.rodada(db))["lancados"] == 2


async def test_desligado_nao_faz_nada(db: AsyncSession, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "devolucao_fraude_auto", auto.DESLIGADO)
    await _caso(db, make_user, "797001")
    r = await auto.rodada(db)
    assert r["modo"] == "desligado" and r["candidatos"] == 0
    assert (await _rast(db, "797001")).video_solicitado_em is None


async def test_so_propor_escreve_a_defesa_e_nao_lanca_ate_soltar(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """1ª subida: com o "só propor", o robô pede o vídeo e escreve a defesa proposta na
    Observação do pedido — nada é lançado nem vai pra plataforma até o OK."""
    monkeypatch.setattr(auto, "SO_PROPOR", True)
    assert auto.modo() == auto.PROPOR
    await _caso(
        db, make_user, "797101", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    await _caso(db, make_user, "797102")  # sem vídeo: pede, como no ligado
    lidos: list[str] = []
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK, lidos))
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    agendados = _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["propostos"] == 1 and r["lancados"] == 0 and r["video_pedido"] == 1, r
    assert await _lancamentos(db, "797101") == [] and agendados == []
    obs = (await _rast(db, "797101")).observacao
    assert auto.PROPOSTA in obs and DEFESA in obs and 'motivo "Golpe"' in obs
    # Próxima rodada: não pergunta de novo.
    r = await auto.rodada(db)
    assert r["propostos"] == 0 and len(lidos) == 1
    # Solto: lança com a defesa que foi aprovada — relê o caso (ainda dá pra contestar?)
    # mas não pergunta de novo à IA (a resposta nova podia mudar o que ele viu).
    monkeypatch.setattr(auto, "SO_PROPOR", False)
    _ia(monkeypatch, "faltou_item", defesa="Outra defesa que ninguém aprovou, bem diferente.")
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert len(perguntas) == 1 and len(lidos) == 2
    linhas = await _lancamentos(db, "797101")
    assert [(d.motivo_devolucao, d.observacao) for d in linhas] == [("Golpe", DEFESA)]


async def test_proposta_anterior_e_loja_aceitou() -> None:
    obs = (
        "recado da equipe\n"
        f'{auto.MARCA} (16/11 18:00): {auto.PROPOSTA} (motivo "Item faltando") — o comprador '
        'alega "faltou o carregador"; defesa: "O comprador diz que faltou o carregador; o vídeo '
        'mostra o kit completo embalado e lacrado". Esperando o OK pra lançar e abrir o chamado.'
    )
    assert auto.proposta_anterior(obs) == (
        "Item faltando",
        "O comprador diz que faltou o carregador; o vídeo mostra o kit completo embalado e lacrado",
    )
    assert auto.proposta_anterior("nada aqui") is None
    assert auto.proposta_anterior(obs.replace("Item faltando", "Inventado")) is None
    assert auto._loja_aceitou([{"event": "SELLER_AGREE_REFUND"}])
    assert auto._loja_aceitou([{"event": "SELLER_AGGREE_RETURN"}])
    assert not auto._loja_aceitou([{"event": "SELLER_REJECT_APPLICATION_TIMEOUT_REFUND"}])
    assert not auto._loja_aceitou([{"event": "ORDER_REFUND"}, {"event": "REFUND_SUCCESS"}])


async def test_alegacao_vazia_nao_pergunta_e_nao_marca(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """A TikTok devolve vazio quando a API falha: tenta de novo, sem marcar pra pessoa e
    sem parar os outros pedidos da rodada."""
    for pedido in ("797201", "797202"):
        await _caso(
            db, make_user, pedido, video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
        )

    async def _vazio_no_1(session, plataforma, rid, dev):
        if rid == "RET797201":
            return auto.Caso(situacao=auto.RESPONDER, ida_entregue=None)
        return _OK

    monkeypatch.setattr(auto, "_ler_caso", _vazio_no_1)
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["falhas"] == 1 and r["lancados"] == 1 and len(perguntas) == 1, r
    assert r["pedidos"]["797201"] == "alegação não lida"
    assert auto.NAO_RESPONDE not in ((await _rast(db, "797201")).observacao or "")


async def test_ia_fora_do_ar_para_de_perguntar_na_rodada(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    for pedido in ("797301", "797302", "797303"):
        await _caso(
            db, make_user, pedido, video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
        )
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
    perguntas: list[str] = []
    _ia(monkeypatch, None, lidos=perguntas)
    r = await auto.rodada(db)
    assert len(perguntas) == 1 and r["falhas"] == 1 and r["lancados"] == 0, r
    assert r["pedidos"]["797303"] == "fica pra próxima rodada"


async def test_abrir_chamado_nao_no_meio_da_leitura_nao_lanca(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db, make_user, "797401", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )

    async def _le_e_alguem_diz_nao(session, plataforma, rid, dev):
        await session.execute(
            text(
                f'UPDATE "{_SCHEMA}".devolucao_rastreio SET abrir_chamado = false, '  # noqa: S608
                "observacao = 'nao abrir: cliente devolveu por fora' WHERE pedido_bling = '797401'"
            )
        )
        await session.commit()
        return _OK

    monkeypatch.setattr(auto, "_ler_caso", _le_e_alguem_diz_nao)
    agendados = _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["lancados"] == 0 and agendados == [] and await _lancamentos(db, "797401") == []
    assert "cliente devolveu por fora" in (await _rast(db, "797401")).observacao


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
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    # A IA leu a nota do comprador ("caixa de sabonete") e a entrega da J&T.
    assert "caixa de sabonete" in perguntas[0] and "J&T Express Brazil" in perguntas[0]
    linhas = await _lancamentos(db, "296936")
    assert [(d.condicao_produto, d.motivo_devolucao) for d in linhas] == [
        ("Não devolvido", "Golpe")
    ]
    assert len(fake.rejects) == 1
    rj = fake.rejects[0]
    assert (rj["return_id"], rj["decision"]) == (rid, "REJECT_REFUND")
    assert "https://mega.nz/file/embalagem#k" in rj["comment"]
    # A defesa da IA vai no comentário, dentro dos 500 bytes da TikTok.
    assert "sabonete no lugar do celular" in rj["comment"], rj["comment"]
    assert len(rj["comment"].encode()) <= 500
    assert (await _abertura(db, "296936")).status == "enviada"


async def test_tiktok_ja_pago_a_defesa_vai_pela_tela(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """Só reembolso que a TikTok já pagou sem a nossa resposta: antes o chamado falhava
    ("já saiu de pendente"); agora vai pro robô da Central do Vendedor com a defesa."""
    fake, _rid = await _tiktok(db, make_user, monkeypatch)
    fake.status_reembolso = "RETURN_OR_REFUND_REQUEST_COMPLETE"
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert "JÁ REEMBOLSOU" in perguntas[0]
    assert fake.rejects == []
    msg = await _abertura(db, "296936")
    assert (msg.status, msg.canal) == ("pendente", "robo"), (msg.status, msg.erro)
    assert DEFESA in msg.texto and "https://mega.nz/file/embalagem#k" in msg.texto
    assert "A TikTok aprovou o reembolso de R$ 803.2" in msg.texto
    assert "devolução" not in msg.texto.replace("SEM devolver", "")


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
    assert DEFESA in d["text"], d["text"]


async def test_shopee_em_analise_sem_nos_a_defesa_vai_pela_tela(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """A Shopee analisa o só reembolso sozinha (JUDGING, sem contestação nossa): antes
    o chamado falhava como "já contestada"; agora a defesa vai pela tela."""
    from tests.test_chamados_devolucao import _FakeShopee

    fake = _FakeShopee()

    async def _det(return_sn):
        return {
            "return_sn": return_sn,
            "status": "JUDGING",
            "return_solution": 1,
            "needs_logistics": False,
            "reason": "WRONG_ITEM",
            "text_reason": "veio um sabonete",
            "refund_amount": 499.9,
        }

    fake.get_return_detail = _det

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(cd, "_shopee_client_para", _c)
    monkeypatch.setattr(cd, "ENFILEIRAR", False)
    await _caso(
        db,
        make_user,
        "798101",
        conta="mega",
        loja="92",
        acao="",
        video_link="https://mega.nz/file/caixa798101#k",
        video_enviado_em=AGORA,
    )
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert "ANALISANDO" in perguntas[0]
    assert fake.disputes == []
    msg = await _abertura(db, "798101")
    assert (msg.status, msg.canal) == ("pendente", "robo"), (msg.status, msg.erro)
    assert DEFESA in msg.texto and "https://mega.nz/file/caixa798101#k" in msg.texto


async def test_shopee_em_processamento_sem_motivo_liberado_vai_pela_tela(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    from tests.test_chamados_devolucao import _FakeShopee

    fake = _FakeShopee()

    async def _det(return_sn):
        return {
            "return_sn": return_sn,
            "status": "PROCESSING",
            "return_solution": 1,
            "needs_logistics": False,
            "reason": "MISSING_ITEM",
            "text_reason": "faltou o carregador",
        }

    async def _sem_motivos(return_sn):
        return []

    fake.get_return_detail = _det
    fake.get_return_dispute_reason = _sem_motivos

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(cd, "_shopee_client_para", _c)
    monkeypatch.setattr(cd, "ENFILEIRAR", False)
    await _caso(
        db,
        make_user,
        "798201",
        conta="mega",
        loja="93",
        acao="",
        video_link="https://mega.nz/file/caixa798201#k",
        video_enviado_em=AGORA,
    )
    _ia(monkeypatch, "faltou_item")
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert [d.motivo_devolucao for d in await _lancamentos(db, "798201")] == ["Item faltando"]
    assert fake.disputes == []
    msg = await _abertura(db, "798201")
    assert (msg.status, msg.canal) == ("pendente", "robo"), (msg.status, msg.erro)


# ── "Abrir chamado: Sim" + "Instrução IA" na Fraude (16/11/2026) ──────────

_LARGADO = (
    'Fraude automática (16/11 16:37): o comprador alega "desisti da compra" — o sistema '
    "não entendeu; o sistema não responde esse caso sozinho (responder à mão)."
)
_INSTR = "o cliente diz que desistiu, mas recebeu o produto certo — contestar com o vídeo"


async def test_sim_com_instrucao_reabre_o_que_ficou_pra_pessoa_e_responde(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db,
        make_user,
        "799101",
        video_link="https://mega.nz/file/v#k",
        video_enviado_em=AGORA,
        observacao=_LARGADO,
        abrir_chamado=True,
        instrucao_ia_pendente=_INSTR,
        instrucao_ia_em=AGORA - timedelta(minutes=3),
    )
    monkeypatch.setattr(
        auto,
        "_ler_caso",
        _ler(auto.Caso(situacao=auto.PAGO, codigo="OTHER", texto="desisti", ida_entregue=True)),
    )
    perguntas: list[str] = []
    _ia(monkeypatch, "vazio_ou_outro_produto", lidos=perguntas)
    agendados = _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    # A IA leu a instrução da equipe junto com a alegação.
    assert "INSTRUÇÃO da equipe" in perguntas[0] and "desistiu" in perguntas[0]
    assert [d.motivo_devolucao for d in await _lancamentos(db, "799101")] == ["Golpe"]
    assert agendados == ["799101"]
    rast = await _rast(db, "799101")
    assert "seguindo a instrução da equipe" in rast.observacao
    # A instrução entrou no chamado (a IA de Chamado lê) e saiu da espera.
    assert rast.instrucao_ia_pendente is None


async def test_instrucao_lida_e_ainda_pra_pessoa_nao_rele_e_a_janela_mostra(
    db: AsyncSession, make_user, ligado, visao, monkeypatch, client, auth_as
):
    await _caso(
        db,
        make_user,
        "799201",
        video_link="https://mega.nz/file/v#k",
        video_enviado_em=AGORA,
        observacao=_LARGADO,
        abrir_chamado=True,
        instrucao_ia_pendente="ver isso aí",
        instrucao_ia_em=AGORA - timedelta(minutes=3),
    )
    lidos: list[str] = []
    monkeypatch.setattr(
        auto,
        "_ler_caso",
        _ler(auto.Caso(situacao=auto.PAGO, codigo="OTHER", texto="?", ida_entregue=True), lidos),
    )
    _ia(monkeypatch, "outra", pessoa="a instrução não diz o que contestar")
    r = await auto.rodada(db)
    assert r["pra_pessoa"] == 1 and r["lancados"] == 0, r
    await auto.rodada(db)
    assert len(lidos) == 1  # a mesma instrução não é relida
    auth_as(await make_user(role=UserRole.ADMIN))
    j = (await client.get("/api/devolucoes-painel/799201/chamado")).json()
    assert j["fraude"] is True and j["tem_lancamento"] is False
    assert j["ia_nao_abriu"] == "a instrução não diz o que contestar", j


async def test_reembolso_aceito_pela_loja_so_contesta_com_instrucao(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    aceito = auto.Caso(
        situacao=auto.ACEITO_PELA_LOJA, codigo="Pacote vazio", texto="vazio", ida_entregue=True
    )
    monkeypatch.setattr(auto, "_ler_caso", _ler(aceito))
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    _sem_disparo(monkeypatch)
    # Sem instrução: pessoa.
    await _caso(
        db, make_user, "799301", video_link="https://mega.nz/file/v#k", video_enviado_em=AGORA
    )
    r = await auto.rodada(db)
    assert r["pra_pessoa"] == 1 and perguntas == [], r
    # Com Sim + instrução: contesta, e a IA sabe que a plataforma já reembolsou.
    rast = await _rast(db, "799301")
    rast.abrir_chamado = True
    rast.instrucao_ia_pendente = (
        "foi aceito sem querer, contestar: veio vazio segundo ele, o vídeo mostra completo"
    )
    rast.instrucao_ia_em = AGORA
    await db.commit()
    r = await auto.rodada(db)
    assert r["lancados"] == 1, r
    assert "JÁ REEMBOLSOU" in perguntas[0]


async def test_worker_agenda_a_fraude_a_cada_10_minutos() -> None:
    from app.worker import WorkerSettings

    jobs = [c for c in WorkerSettings.cron_jobs if c.name.endswith("devolucao_fraude_auto")]
    assert len(jobs) == 1 and jobs[0].minute == set(range(7, 60, 10))


# ── 24/11/2026: "negociação" da Shopee e "não tenho o vídeo" ──────────────


async def test_negociacao_so_e_proposta_com_valor_menor_que_o_pedido() -> None:
    """PENDING_RESPOND sozinho é o próprio pedido de reembolso esperando a loja
    (308634, 307496, 308570): não é proposta. Só valor menor que o pedido é."""
    pendente = {"negotiation_status": "PENDING_RESPOND"}
    assert auto._proposta_de_verdade({"refund_amount": 600}, pendente) == ""
    assert (
        auto._proposta_de_verdade(
            {"refund_amount": 600},
            pendente | {"latest_offer_amount": 600, "latest_offer_creator": "buyer"},
        )
        == ""
    )
    assert (
        auto._proposta_de_verdade({"refund_amount": 1600}, pendente | {"latest_offer_amount": 30})
        == "R$ 30,00 (o pedido era R$ 1.600,00)"
    )
    # Sem o refund_amount do caso: a soma dos itens.
    itens = {"item": [{"refund_amount": 300}, {"refund_amount": 199.9}]}
    assert auto._proposta_de_verdade(itens, pendente | {"latest_offer_amount": 100}) == (
        "R$ 100,00 (o pedido era R$ 499,90)"
    )
    # Sem saber o pedido, ou negociação que não espera a loja: não é proposta.
    assert auto._proposta_de_verdade({}, pendente | {"latest_offer_amount": 30}) == ""
    assert (
        auto._proposta_de_verdade(
            {"refund_amount": 600}, {"negotiation_status": "TERMINATED", "latest_offer_amount": 30}
        )
        == ""
    )


def _shopee_com_negociacao(monkeypatch, *, oferta: float | None, status: str = "JUDGING"):
    from tests.test_chamados_devolucao import _FakeShopee

    fake = _FakeShopee()
    neg = {"negotiation_status": "PENDING_RESPOND", "latest_solution": "REFUND"}
    if oferta is not None:
        neg |= {"latest_offer_amount": oferta, "latest_offer_creator": "buyer"}

    async def _det(return_sn):
        return {
            "return_sn": return_sn,
            "status": status,
            "return_solution": 1,
            "needs_logistics": False,
            "reason": "WRONG_ITEM",
            "text_reason": "veio um sabonete",
            "refund_amount": 499.9,
            "negotiation": neg,
        }

    fake.get_return_detail = _det

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(cd, "_shopee_client_para", _c)
    monkeypatch.setattr(cd, "ENFILEIRAR", False)
    return fake


async def test_shopee_pedido_esperando_a_loja_nao_e_negociacao_e_o_chamado_nasce(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """307496/308570: vídeo na mão, negociação PENDING_RESPOND com a oferta = o pedido.
    Antes ia pra pessoa ("o comprador fez uma proposta"); agora lança e o chamado nasce."""
    _shopee_com_negociacao(monkeypatch, oferta=499.9)
    await _caso(
        db,
        make_user,
        "799601",
        conta="mega",
        loja="96",
        acao="",
        video_link="https://mega.nz/file/caixa799601#k",
        video_enviado_em=AGORA,
    )
    r = await auto.rodada(db)
    assert r["lancados"] == 1 and r["pra_pessoa"] == 0, r
    assert auto.NAO_RESPONDE not in ((await _rast(db, "799601")).observacao or "")
    msg = await _abertura(db, "799601")
    assert DEFESA in msg.texto


async def test_shopee_proposta_de_reembolso_parcial_fica_pra_pessoa_com_o_valor(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    _shopee_com_negociacao(monkeypatch, oferta=30, status="PROCESSING")
    perguntas: list[str] = []
    _ia(monkeypatch, lidos=perguntas)
    await _caso(
        db,
        make_user,
        "799602",
        conta="mega",
        loja="97",
        acao="",
        video_link="https://mega.nz/file/v#k",
        video_enviado_em=AGORA,
    )
    r = await auto.rodada(db)
    assert r["lancados"] == 0 and r["pra_pessoa"] == 1 and perguntas == [], r
    obs = (await _rast(db, "799602")).observacao
    assert "reembolso parcial de R$ 30,00 (o pedido era R$ 499,90)" in obs, obs
    assert auto.NAO_RESPONDE in obs
    assert await _lancamentos(db, "799602") == []


async def test_equipe_sem_video_o_robo_pede_de_novo_uma_vez(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    """309043: a equipe respondeu "Não tô em casa" e o robô parou pra sempre. Agora,
    12 h depois da resposta, pede de novo (a equipe vê "refazer: …"); se vier sem
    vídeo outra vez, fica pra pessoa e não pede mais."""
    await _caso(
        db,
        make_user,
        "799701",
        fonte="tiktok",
        acao="SELLER_RESPOND_REFUND",
        observacao=f"{auto.MARCA} (20/11 19:17): {auto.VIDEO_PEDIDO}.",
        video_solicitado_em=AGORA - timedelta(days=3, hours=14),
        video_enviado_em=AGORA - timedelta(days=3),
        video_sem_motivo="Não tô em casa",
    )
    lidos: list[str] = []
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK, lidos))
    r = await auto.rodada(db)
    assert r["video_pedido"] == 1 and r["pedidos"]["799701"] == "vídeo pedido de novo", r
    rast = await _rast(db, "799701")
    assert (
        antigo._video_status(
            link=rast.video_link,
            solicitado_em=rast.video_solicitado_em,
            enviado_em=rast.video_enviado_em,
            sem_motivo=rast.video_sem_motivo,
        )
        == "pendente"
    )
    assert rast.video_refazer_motivo == 'pedido de novo — a 1ª resposta foi "Não tô em casa"'
    assert auto.VIDEO_PEDIDO_DE_NOVO in rast.observacao
    assert auto.NAO_RESPONDE not in rast.observacao and lidos == []
    # Esperando a equipe: não pede de novo nem lê a plataforma.
    r = await auto.rodada(db)
    assert r["esperando_video"] == 1 and r["video_pedido"] == 0 and lidos == []
    # A 2ª resposta sem vídeo, dias depois: pessoa, sem 3º pedido.
    rast = await _rast(db, "799701")
    rast.video_enviado_em = AGORA
    rast.video_sem_motivo = "não gravamos esse pedido"
    await db.commit()
    for quando in (AGORA, AGORA + timedelta(days=2)):
        r = await auto.rodada(db, agora=quando)
        assert r["video_pedido"] == 0 and r["pedidos"]["799701"] == "sem vídeo (equipe)", r
    assert (await _rast(db, "799701")).video_solicitado_em is not None


async def test_equipe_sem_video_e_o_video_chega_no_2o_pedido_o_robo_lanca(
    db: AsyncSession, make_user, ligado, visao, monkeypatch
):
    await _caso(
        db,
        make_user,
        "799702",
        video_solicitado_em=AGORA - timedelta(days=1),
        video_enviado_em=AGORA - timedelta(hours=13),
        video_sem_motivo="Não tô em casa",
    )
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
    agendados = _sem_disparo(monkeypatch)
    r = await auto.rodada(db)
    assert r["video_pedido"] == 1, r
    # A equipe cola o link (a mesma rota da aba Pedidos grava assim).
    rast = await _rast(db, "799702")
    rast.video_link = "https://mega.nz/file/depois#k"
    rast.video_enviado_em = AGORA
    await db.commit()
    r = await auto.rodada(db)
    assert r["lancados"] == 1 and agendados == ["799702"], r


# ── Saiu da Fraude com o vídeo do robô pendente (25/11/2026, 308495) ──────


def _status_video(rast: DevolucaoRastreio) -> str:
    return antigo._video_status(
        link=rast.video_link,
        solicitado_em=rast.video_solicitado_em,
        enviado_em=rast.video_enviado_em,
        sem_motivo=rast.video_sem_motivo,
    )


async def _bling(db: AsyncSession, pedido: str, situacao: str) -> None:
    await db.execute(
        text(f'UPDATE "{_SCHEMA}".bling_orders SET situacao = :s WHERE numero = :p'),  # noqa: S608
        {"s": situacao, "p": pedido},
    )
    await db.commit()


async def test_porque_saiu_e_cancelado_pelo_robo() -> None:
    def porque(**kw):
        base = {
            "tipo": "REFUND",
            "fila_manual": "",
            "abrir_chamado": None,
            "aguardando": True,
            "resolvido": False,
        }
        return auto._porque_saiu(**(base | kw))

    assert "Acompanhamento" in porque(fila_manual="acompanhamento")
    assert "pacote volta" in porque(tipo="")
    assert "Abrir chamado" in porque(abrir_chamado=False)
    assert "Bling" in porque(aguardando=False)
    assert "resolvido" in porque(resolvido=True)
    pediu = f"{auto.MARCA} (19/11 21:47): {auto.VIDEO_PEDIDO}."
    cancelou = (
        f"{auto.MARCA} (25/11 08:00): o pedido saiu da Fraude (x) — "
        f"{auto.VIDEO_CANCELADO_SAIU}, a equipe não precisa mais mandar."
    )
    de_novo = f"{auto.MARCA} (26/11 08:00): {auto.VIDEO_PEDIDO_DE_NOVO}."
    assert not auto._cancelado_pelo_robo("")
    assert not auto._cancelado_pelo_robo(pediu)
    assert auto._cancelado_pelo_robo(f"{pediu}\n{cancelou}")
    # Pediu de novo depois do cancelamento do robô: o último é o pedido.
    assert not auto._cancelado_pelo_robo(f"{pediu}\n{cancelou}\n{pediu}")
    assert not auto._cancelado_pelo_robo(f"{pediu}\n{cancelou}\n{de_novo}")
    # A espera de 6 h conta do último cancelamento (hora de São Paulo no recado).
    sp = datetime(2026, 11, 25, 8, 0, tzinfo=UTC) + timedelta(hours=3)  # 08:00 em SP
    assert auto._cancelou_ha_pouco(cancelou, sp + timedelta(hours=5))
    assert not auto._cancelou_ha_pouco(cancelou, sp + timedelta(hours=7))
    assert not auto._cancelou_ha_pouco(pediu, sp)
    # Recado de dezembro lido em janeiro: é do ano passado.
    dez = cancelou.replace("25/11 08:00", "31/12 23:00")
    assert auto._cancelou_ha_pouco(dez, datetime(2027, 1, 1, 4, 0, tzinfo=UTC))


async def test_pacote_volta_pela_ida_o_robo_cancela_o_proprio_pedido_de_video(
    db: AsyncSession, make_user, ligado, monkeypatch
):
    """308495: o robô pediu o vídeo na Fraude; a entrega falhou, a Shopee aceitou e o
    pacote voltou pela ida — o sync troca o tipo (deixa de ser só reembolso) e o
    pedido vai pro Acompanhamento. O pedido de vídeo ficava travando a aba Pedidos da
    equipe por dias, sem servir pra nada."""
    await _caso(db, make_user, "799801")
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
    assert (await auto.rodada(db))["video_pedido"] == 1
    rast = await _rast(db, "799801")
    rast.devolucao_tipo_auto = None
    await db.commit()

    r = await auto.rodada(db)
    assert r["candidatos"] == 0 and r["video_cancelado"] == 1, r
    assert r["pedidos"]["799801"] == "fora da Fraude automática: pedido de vídeo cancelado"
    rast = await _rast(db, "799801")
    assert rast.video_solicitado_em is None and rast.video_refazer_motivo is None
    assert _status_video(rast) == "nao_solicitado"
    assert auto.VIDEO_CANCELADO_SAIU in rast.observacao and "pacote volta" in rast.observacao
    assert auto.NAO_RESPONDE not in rast.observacao
    # Nada mais nas próximas rodadas.
    assert (await auto.rodada(db))["video_cancelado"] == 0


async def test_movido_pro_acompanhamento_ou_bling_fora_tambem_cancela(
    db: AsyncSession, make_user, ligado, monkeypatch
):
    for pedido in ("799802", "799803", "799804"):
        await _caso(db, make_user, pedido)
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
    assert (await auto.rodada(db))["video_pedido"] == 3
    rast = await _rast(db, "799802")
    rast.fila_manual = "acompanhamento"  # alguém tirou da Fraude na mão
    await db.commit()
    await _bling(db, "799803", "83953")  # o caso foi cancelado e o Bling voltou pra Entregue

    r = await auto.rodada(db)
    assert r["video_cancelado"] == 2 and r["pedidos"]["799804"] == "esperando o vídeo", r
    assert "Acompanhamento" in (await _rast(db, "799802")).observacao
    assert "Bling" in (await _rast(db, "799803")).observacao
    # O que segue na Fraude continua pedido pra equipe.
    assert _status_video(await _rast(db, "799804")) == "pendente"


async def test_video_fica_com_lancamento_ou_pedido_feito_por_pessoa(
    db: AsyncSession, make_user, ligado, monkeypatch
):
    """Quem já mexeu decide: com lançamento (ou chamado) o vídeo ainda pode servir, o
    pedido feito pelo botão é da pessoa, não do robô, e o fixado na Fraude à mão
    continua no painel (é da pessoa)."""
    pessoa = await make_user()
    pediu = f"{auto.MARCA} (19/11 21:47): {auto.VIDEO_PEDIDO}."
    await _caso(db, make_user, "799805", observacao=pediu, video_solicitado_em=AGORA)
    db.add(Devolution(pedido_bling="799805", conta="mini", sku="a17.sp", condicao_produto="Novo"))
    await _caso(
        db,
        make_user,
        "799806",
        fila="acompanhamento",
        observacao=pediu,
        video_solicitado_em=AGORA,
        video_solicitado_por=pessoa.id,
    )
    await _caso(
        db,
        make_user,
        "799808",
        fila="fraude",
        tipo="",
        observacao=pediu,
        video_solicitado_em=AGORA,
    )
    await db.commit()
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))

    r = await auto.rodada(db)
    assert r["video_cancelado"] == 0, r
    for pedido in ("799805", "799806", "799808"):
        rast = await _rast(db, pedido)
        assert _status_video(rast) == "pendente"
        assert auto.VIDEO_CANCELADO_SAIU not in rast.observacao


async def test_voltou_pra_fraude_depois_do_cancelamento_do_robo_pede_de_novo(
    db: AsyncSession, make_user, ligado, monkeypatch
):
    """O cancelamento do robô não é "alguém cancelou": se o pedido volta pra Fraude, o
    robô pede o vídeo de novo — mas só 6 h depois (a leitura do rastreio da ida que
    falha devolve "só reembolso" por meia hora; a equipe não pode travar e destravar a
    cada falha). Se depois uma PESSOA cancela, aí fica pra pessoa."""
    await _caso(db, make_user, "799807")
    monkeypatch.setattr(auto, "_ler_caso", _ler(_OK))
    assert (await auto.rodada(db))["video_pedido"] == 1
    rast = await _rast(db, "799807")
    rast.devolucao_tipo_auto = None
    await db.commit()
    assert (await auto.rodada(db))["video_cancelado"] == 1
    rast = await _rast(db, "799807")
    rast.devolucao_tipo_auto = "REFUND"  # a leitura da ida falhou nesta meia hora
    await db.commit()

    r = await auto.rodada(db)
    assert r["video_pedido"] == 0, r
    assert r["pedidos"]["799807"] == "voltou pra Fraude há pouco (espera pra pedir)"
    assert _status_video(await _rast(db, "799807")) == "nao_solicitado"
    # Sync bom de novo: sai, e não há o que cancelar.
    rast = await _rast(db, "799807")
    rast.devolucao_tipo_auto = None
    await db.commit()
    assert (await auto.rodada(db))["video_cancelado"] == 0
    # Voltou de verdade e ficou: passadas as 6 h, pede de novo.
    rast = await _rast(db, "799807")
    rast.devolucao_tipo_auto = "REFUND"
    await db.commit()
    depois = datetime.now(UTC) + auto.VOLTOU_ESPERA + timedelta(minutes=5)
    r = await auto.rodada(db, agora=depois)
    assert r["video_pedido"] == 1 and r["pedidos"]["799807"] == "vídeo pedido", r
    rast = await _rast(db, "799807")
    assert _status_video(rast) == "pendente" and "voltou pra Fraude" in rast.observacao
    assert not auto._cancelado_pelo_robo(rast.observacao)
    # Agora uma pessoa cancela pelo ✕ da coluna Vídeo: não pede mais.
    rast = await _rast(db, "799807")
    rast.video_solicitado_em = None
    await db.commit()
    r = await auto.rodada(db, agora=depois + timedelta(minutes=10))
    assert r["video_pedido"] == 0 and r["pedidos"]["799807"] == "pedido de vídeo cancelado", r
