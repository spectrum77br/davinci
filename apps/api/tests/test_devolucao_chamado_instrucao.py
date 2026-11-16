"""Chamado aberto pela instrução da IA (Vinicius, 16/11/2026): "o sim aliado à
instrução da IA, ele analisa e abre chamado".

A IA é de mentira aqui (`pergunta=`); o disparo (fila do worker) vira registro.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, DevolucaoRastreio, Devolution, StoreInfo, UserRole
from app.models.chamado import ChamadoMensagem
from app.services import chamados_devolucao as cd
from app.services import devolucao_chamado_instrucao as svc
from app.services import devolucao_conferencia as conferencia

pytestmark = pytest.mark.asyncio
AGORA = datetime.now(UTC)
TEXTO = (
    "O comprador diz que faltou o carregador; o vídeo da embalagem mostra o kit "
    "completo sendo lacrado."
)


def _resp(**kw) -> svc.Resposta:
    base = {
        "motivo": "Item faltando",
        "skus": ["a001.sp"],
        "texto": TEXTO,
        "precisa_pessoa": False,
        "por_que": "",
    }
    return svc.Resposta(**(base | kw))


def _ia(resposta: svc.Resposta | None, perguntas: list[str] | None = None):
    async def _f(texto):
        if perguntas is not None:
            perguntas.append(texto)
        return resposta

    return _f


@pytest.fixture
def ligado(monkeypatch):
    monkeypatch.setattr(conferencia, "ligada", lambda: True)
    agendados: list[str] = []

    async def _agendar(session, ch, dev):
        agendados.append(dev.pedido_bling)

    monkeypatch.setattr(cd, "agendar_disparo", _agendar)
    return agendados


async def _pedido(
    db: AsyncSession,
    make_user,
    pedido: str,
    *,
    instrucao: str
    | None = "cliente diz que veio sem carregador, o vídeo mostra completo — contestar",
    abrir: bool | None = True,
    motivo: str | None = None,
    skus: tuple[str, ...] = ("a001.sp", "b002.sp"),
    conta: str = "kfa",
    plataforma: str = "shopee",
    link_envio: str | None = "https://mega.nz/file/v#k",
    observacao: str | None = None,
    fila: str | None = None,
) -> None:
    if (await db.execute(select(StoreInfo).where(StoreInfo.account_name == conta))).first() is None:
        user = await make_user()
        db.add(
            StoreInfo(
                user_id=user.id,
                platform=plataforma,
                account_name=conta,
                bling_store_id=str(abs(hash(conta)) % 100000),
            )
        )
    for sku in skus:
        db.add(
            Devolution(
                pedido_bling=pedido,
                conta=conta,
                sku=sku,
                produtos=f"Produto {sku}",
                quantidade=1,
                condicao_produto="Novo",
                motivo_devolucao=motivo,
                link_envio=link_envio,
                observacao=observacao,
            )
        )
    db.add(
        DevolucaoRastreio(
            pedido_bling=pedido,
            abrir_chamado=abrir,
            instrucao_ia_pendente=instrucao,
            instrucao_ia_em=AGORA - timedelta(minutes=5) if instrucao else None,
            fila_manual=fila,
        )
    )
    await db.commit()


async def _rast(db: AsyncSession, pedido: str) -> DevolucaoRastreio:
    db.expire_all()
    return await db.get(DevolucaoRastreio, pedido)


async def _linhas(db: AsyncSession, pedido: str) -> list[Devolution]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(Devolution).where(Devolution.pedido_bling == pedido).order_by(Devolution.sku)
            )
        ).scalars()
    )


# ── Puras ────────────────────────────────────────────────────────────────


async def test_decidir() -> None:
    skus = ["a001.sp", "b002.sp"]
    d = svc.decidir(_resp(), skus)
    assert (d.motivo, d.skus, d.texto, d.pessoa) == ("Item faltando", ("a001.sp",), TEXTO, None)
    # SKU que a IA inventou não entra; nenhum conhecido = o pedido inteiro.
    assert svc.decidir(_resp(skus=["X999", "A001.SP"]), skus).skus == ("a001.sp",)
    assert svc.decidir(_resp(skus=["X999"]), skus).skus == ()
    # Pessoa: pediu, ou sem motivo, ou sem texto.
    assert (
        svc.decidir(_resp(precisa_pessoa=True, por_que="é Bloqueado"), skus).pessoa == "é Bloqueado"
    )
    assert svc.decidir(_resp(motivo=""), skus).pessoa
    assert svc.decidir(_resp(texto="ok"), skus).pessoa
    assert svc.decidir(None, skus) == svc.Decisao()
    # Texto longo: cortado numa frase.
    longo = svc.decidir(_resp(texto="Frase curta. " + "palavra " * 80), skus).texto
    assert len(longo) <= svc.MAX_CHARS_TEXTO and longo.endswith(".")


async def test_entrada_e_chave() -> None:
    t = svc.entrada(
        plataforma="shopee",
        pecas=[svc.Peca("a001.sp", "Fone", "Novo", "", "veio sem o carregador")],
        observacao_pedido="recado",
        instrucao="contestar faltou carregador",
        video_envio=True,
        video_abertura=False,
        fotos=2,
    )
    assert 'SKU "a001.sp"' in t and "veio sem o carregador" in t and "INSTRUÇÃO" in t
    assert (
        "contestar faltou carregador" in t and "fotos: 2" in t and "expedição (embalagem): sim" in t
    )
    k = svc.chave_da_instrucao(AGORA, "abc")
    assert k != svc.chave_da_instrucao(AGORA, "abcd") and k.startswith("[instrução de")
    obs = f"x\n{svc.MARCA} (16/11 10:00): {svc.NAO_ABRIU} — é caso de Bloqueado {k}"
    assert svc.ultimo_nao_abriu(obs) == "é caso de Bloqueado"
    assert svc.ultimo_nao_abriu("nada") is None
    assert conferencia.lancado_pela_ia(f"… {conferencia.MARCA_IA_INSTRUCAO} …")
    assert conferencia.lancado_pela_ia(f"… {conferencia.MARCA_ROBO} …")
    assert not conferencia.lancado_pela_ia("recado da equipe")


# ── A rodada ─────────────────────────────────────────────────────────────


async def test_sim_com_instrucao_a_ia_escolhe_o_motivo_e_abre(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "880101")
    perguntas: list[str] = []
    r = await svc.rodada(db, pergunta=_ia(_resp(), perguntas))
    assert r["abertos"] == 1, r
    assert "cliente diz que veio sem carregador" in perguntas[0]
    linhas = await _linhas(db, "880101")
    # Só a peça que a instrução cita ganha o motivo e o texto.
    assert [(d.sku, d.motivo_devolucao, d.observacao) for d in linhas] == [
        ("a001.sp", "Item faltando", TEXTO),
        ("b002.sp", None, None),
    ]
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "880101"))).scalar_one()
    assert ch.origem == "devolucao" and ligado == ["880101"]
    # A instrução entrou no chamado (a IA de Chamado lê) e saiu da espera.
    instr = (
        (
            await db.execute(
                select(ChamadoMensagem.texto).where(
                    ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "instrucao"
                )
            )
        )
        .scalars()
        .all()
    )
    assert any("sem carregador" in t for t in instr)
    rast = await _rast(db, "880101")
    assert rast.instrucao_ia_pendente is None
    # Marca de "feito pela IA": a conferência exige resposta antes de sair.
    assert conferencia.lancado_pela_ia(rast.observacao) and "Item faltando" in rast.observacao
    # Já tem chamado: sai da lista.
    assert (await svc.rodada(db, pergunta=_ia(_resp())))["candidatos"] == 0


async def test_ia_pede_pessoa_marca_e_nao_rele_ate_uma_instrucao_nova(
    db: AsyncSession, make_user, ligado, client, auth_as
):
    await _pedido(db, make_user, "880201")
    perguntas: list[str] = []
    ia = _ia(
        _resp(
            precisa_pessoa=True,
            motivo="",
            por_que="o aparelho voltou bloqueado — escolha Bloqueado",
        ),
        perguntas,
    )
    r = await svc.rodada(db, pergunta=ia)
    assert r["pessoa"] == 1 and r["abertos"] == 0, r
    assert (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == "880201"))
    ).first() is None
    # A janela mostra o porquê (etiqueta vermelha "IA não abriu").
    auth_as(await make_user(role=UserRole.ADMIN))
    j = (await client.get("/api/devolucoes-painel/880201/chamado")).json()
    assert j["ia_nao_abriu"] == "o aparelho voltou bloqueado — escolha Bloqueado", j
    # Mesma instrução: não pergunta de novo.
    await svc.rodada(db, pergunta=ia)
    assert len(perguntas) == 1
    # Instrução nova (pela rota da janela): olha de novo.
    from uuid import uuid4

    from app.models import BlingOrder

    db.add(
        BlingOrder(
            id=uuid4(),
            numero="880201",
            numeroloja="MKT880201",
            bling_id=880201,
            situacao="83957",
            loja="55",
            item_index=0,
            item_codigo="a001.sp",
        )
    )
    await db.commit()
    r2 = await client.post(
        "/api/devolucoes-painel/880201/instrucao", json={"texto": "contestar como item faltando"}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["ia_nao_abriu"] is None
    await svc.rodada(db, pergunta=_ia(_resp(), perguntas))
    assert len(perguntas) == 2
    assert (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == "880201"))
    ).first() is not None


async def test_quem_nao_entra(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "880301", abrir=None)  # ninguém marcou Sim
    await _pedido(db, make_user, "880302", instrucao=None)  # Sim sem instrução
    await _pedido(db, make_user, "880303", abrir=False)  # Não
    await _pedido(db, make_user, "880304", motivo="Golpe")  # o motivo já abre chamado
    perguntas: list[str] = []
    r = await svc.rodada(db, pergunta=_ia(_resp(), perguntas))
    assert r["abertos"] == 0 and perguntas == [] and r["candidatos"] == 0, r


async def test_ia_fora_do_ar_nao_marca_e_tenta_de_novo(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "880401")
    await _pedido(db, make_user, "880402")
    perguntas: list[str] = []
    r = await svc.rodada(db, pergunta=_ia(None, perguntas))
    assert r["abertos"] == 0 and len(perguntas) == 1, r  # para na 1ª falha
    assert svc.NAO_ABRIU not in ((await _rast(db, "880401")).observacao or "")
    assert (await svc.rodada(db, pergunta=_ia(_resp())))["abertos"] == 2


async def test_alguem_marca_nao_durante_a_leitura(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "880501")

    async def _le_e_alguem_diz_nao(texto):
        rast = await db.get(DevolucaoRastreio, "880501")
        rast.abrir_chamado = False
        await db.commit()
        return _resp()

    r = await svc.rodada(db, pergunta=_le_e_alguem_diz_nao)
    assert r["pedidos"]["880501"] == "mudou no meio" and r["abertos"] == 0
    assert (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == "880501"))
    ).first() is None


async def test_ia_desligada_nao_faz_nada(db: AsyncSession, make_user, monkeypatch):
    monkeypatch.setattr(conferencia, "ligada", lambda: False)
    await _pedido(db, make_user, "880601")
    perguntas: list[str] = []
    r = await svc.rodada(db, pergunta=_ia(_resp(), perguntas))
    assert r["abertos"] == 0 and perguntas == [] and r["ia_ligada"] is False


async def test_amazon_sem_api_registra_o_chamado_e_nao_volta(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "880701", conta="amz", plataforma="amazon")
    perguntas: list[str] = []
    r = await svc.rodada(db, pergunta=_ia(_resp(), perguntas))
    assert r["abertos"] == 1 and r["pedidos"]["880701"].startswith("registrado"), r
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "880701"))).scalar_one()
    assert ch.origem == "devolucao" and ligado == []  # sem disparo: é aberto na mão
    assert (await svc.rodada(db, pergunta=_ia(_resp(), perguntas)))["candidatos"] == 0
    assert len(perguntas) == 1


async def test_sku_inventado_vai_pra_pessoa(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "880801")
    r = await svc.rodada(db, pergunta=_ia(_resp(skus=["zz999.xx"])))
    assert r["pessoa"] == 1, r
    assert [d.motivo_devolucao for d in await _linhas(db, "880801")] == [None, None]


async def test_peca_editada_durante_a_leitura_nao_grava(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "880901")

    async def _le_e_alguem_edita(texto):
        d = (
            (await db.execute(select(Devolution).where(Devolution.pedido_bling == "880901")))
            .scalars()
            .first()
        )
        d.motivo_devolucao = "Item Incorreto"
        await db.commit()
        return _resp()

    r = await svc.rodada(db, pergunta=_le_e_alguem_edita)
    assert r["pedidos"]["880901"] == "mudou no meio", r
    assert (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == "880901"))
    ).first() is None


async def test_observacao_da_pessoa_fica_e_a_ia_soma(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "881001", observacao="pesamos a caixa: 180 g a menos")
    r = await svc.rodada(db, pergunta=_ia(_resp()))
    assert r["abertos"] == 1, r
    a001 = (await _linhas(db, "881001"))[0]
    assert a001.observacao.startswith("pesamos a caixa: 180 g a menos") and TEXTO in a001.observacao


async def test_trava_do_video_da_fraude_vale_aqui_tambem(db: AsyncSession, make_user, ligado):
    await _pedido(db, make_user, "881101", fila="fraude", link_envio=None)
    r = await svc.rodada(db, pergunta=_ia(_resp(motivo="Golpe")))
    assert r["pessoa"] == 1 and r["abertos"] == 0, r
    assert [d.motivo_devolucao for d in await _linhas(db, "881101")] == [None, None]
    assert svc.NAO_ABRIU in (await _rast(db, "881101")).observacao


async def test_resposta_ilegivel_vai_pra_pessoa() -> None:
    d = svc.decidir(svc.Resposta(precisa_pessoa=True, por_que=svc.ILEGIVEL), ["a001.sp"])
    assert d.pessoa == svc.ILEGIVEL


async def test_worker_chama_a_rodada_da_instrucao() -> None:
    import inspect

    from app import worker

    src = inspect.getsource(worker.devolucao_fraude_auto)
    assert "devolucao_chamado_instrucao" in src and "instrucao.rodada" in src
