"""F2/C9a, C9b e C9c da tarefa 16: o Informar da Logística (item 2), o
Informar da aba Amazon (3), os prazos da Amazon (4), o Informar do Controle
de Estoque (17), o prazo de resposta da loja nas Devoluções (10) e o Informar
das Devoluções (11) passam a ler o Cadastro (Cadastros › Envios Threema). A
lista do Informar (`threema_informar_config`) fica congelada como reserva de
emergência e o PUT dela responde 410 — "só ADM altera quem recebe" (decisão
do dono, 23/11): quem hoje marcava a lista no modal (o gerente, na aba
Amazon) perde a edição, mas o "Enviar agora" continua para quem pode usar o
botão hoje.

Os itens 10 e 11 hoje não chegam a ninguém (a linha `devolucoes_auto` não
existe em produção e a `devolucoes` está vazia): depois da semente o robô do
prazo nasce DESLIGADO (calado, sem registro nem sino a cada rodada, como
hoje) e o botão segue recusando com 422.

Um teste por ponto, PELO CAMINHO REAL (a rota do botão e a rodada do robô; só
o ThreemaClient é falso):

* (a) Cadastro semeado com a linha do Informar VAZIA: manda para quem está no
  Cadastro, com a chave do envio e o remetente de hoje (o contexto);
* (b) desligado: o botão recusa (422 `envio_desligado`, o `recusa` do
  catálogo) e o robô não manda nem carimba — ao religar, os pendentes saem de
  uma vez (o `acumula`);
* (c) ninguém válido (a única pessoa da lista foi suspensa): não manda,
  acende o sino dos admins e grava o registro `ok=false`; o botão responde
  422 `sem_destinatarios` e o robô não carimba nada.

As rotas: o PUT da lista de cada contexto migrado responde 410 (também para
o gerente), o GET continua mostrando a lista antiga, não-admin não edita
quem recebe no Cadastro, o "Enviar agora" segue para quem pode hoje e o
"quem recebe" leve abre com a porta do botão.

E a semente da fatia: `semear` + `conferir` das chaves dá 0 diferenças e,
depois dela, cada ponto manda EXATAMENTE para quem o legado mandaria (as duas
leis do desenho: nenhum aviso para de sair; nenhum vai para quem foi
suspenso). A linha vazia do robô é o desligado de hoje (calado): a semente
grava o envio desligado e a rodada segue sem mensagem, registro nem sino.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.config import get_settings
from app.models import (
    DevolucaoRastreio,
    Logistica,
    ThreemaEnvio,
    ThreemaInformarConfig,
    UserRole,
    UserStatus,
)
from app.routers import informar as informar_router
from app.services import (
    devolucao_acao_avisos,
    logistica_amazon_avisos,
    threema,
    threema_envios,
    threema_registro,
)
from app.services.threema_catalogo import CATALOGO, K
from tests.test_threema_pontos_logistica_devolucoes import _Fake, _ligar, _registro, _sinos
from tests.test_threema_resolver import _usuario, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.asyncio

GERENTE = "sa.geral@tutamail.com"

# As chaves da C9a, da C9b e da C9c, o contexto do Informar de cada uma (a
# lista antiga) e o remetente de HOJE (o `contexto` que o código passava ao
# ThreemaClient).
REMETENTE_DE_HOJE = {
    K.LOGISTICA_INFORMAR: "logistica",
    K.LOGISTICA_AMAZON_INFORMAR: "logistica_amazon",
    K.LOGISTICA_AMAZON_PRAZOS: "logistica",
    K.CONTROLE_ESTOQUE_INFORMAR: "controle_estoque",
    # C9c
    K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA: "devolucoes",
    K.DEVOLUCOES_INFORMAR: "devolucoes",
}
LISTA_DE_HOJE = {
    K.LOGISTICA_INFORMAR: "logistica",
    K.LOGISTICA_AMAZON_INFORMAR: "logistica_amazon",
    K.LOGISTICA_AMAZON_PRAZOS: "logistica_amazon_auto",
    K.CONTROLE_ESTOQUE_INFORMAR: "controle_estoque",
    K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA: "devolucoes_auto",
    K.DEVOLUCOES_INFORMAR: "devolucoes",
}
BOTOES = {
    K.LOGISTICA_INFORMAR: "logistica",
    K.LOGISTICA_AMAZON_INFORMAR: "logistica_amazon",
    K.CONTROLE_ESTOQUE_INFORMAR: "controle_estoque",
    K.DEVOLUCOES_INFORMAR: "devolucoes",
}
# Os robôs (envio automático, lista do Informar): o vazio de hoje é o desligado.
ROBOS = (K.LOGISTICA_AMAZON_PRAZOS, K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA)
MINHAS = list(REMETENTE_DE_HOJE)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _Fake.envios = []
    threema_envios.limpar_cache()
    threema_registro.reiniciar_disjuntor()
    s = get_settings()
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    monkeypatch.setattr(s, "threema_registro_ativo", True)
    monkeypatch.setattr(threema, "ThreemaClient", _Fake)
    yield
    threema_envios.limpar_cache()


@pytest.fixture
async def vw_devolucoes(db):
    """A `vw_devolucoes` de produção não vem do create_all: uma VAZIA, com as
    colunas que a aba Acompanhamento lê — o relatório do Informar das
    Devoluções sai de verdade ("nenhum pedido aguardando devolução")."""
    schema = get_settings().database_schema
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT NULL::timestamptz AS data, NULL::text AS pedido_bling,
                   NULL::text AS pedido_marketplace, NULL::text AS situacao,
                   NULL::text AS plataforma_bling, NULL::text AS loja_nome,
                   NULL::bigint AS bling_loja_id, NULL::text AS sku,
                   NULL::text AS produto, NULL::integer AS quantidade,
                   NULL::uuid AS bling_order_item_id, NULL::text AS nome_destinatario,
                   NULL::text AS cidade_destino, NULL::text AS uf_destino
            WHERE false
            """  # noqa: S608
        )
    )
    await db.commit()
    yield
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.commit()


async def _pessoas(db):
    admin = await _usuario(db, "admin c9a", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c9a", "TSTJ0006")
    sumido = await _usuario(db, "sumido c9a", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, sumido


async def _linha_informar(db, contexto: str, recipients: str) -> None:
    """A lista antiga do Informar (a reserva de emergência)."""
    row = (
        await db.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == contexto)
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(ThreemaInformarConfig(contexto=contexto, recipients=recipients))
    else:
        row.recipients = recipients
    await db.commit()


def _pedido_amazon(pedido: str = "296762") -> Logistica:
    """Envio próprio da Amazon com a previsão dos Correios vencida e 2 dias
    para a data máxima: dois avisos devidos hoje (previsão e 3 dias)."""
    hoje = logistica_amazon_avisos.hoje_brt()
    return Logistica(
        data=date.today(),
        pedido_bling=pedido,
        pedido_marketplace=f"701-{pedido}",
        plataforma="Amazon",
        conta="kia",
        amazon_canal="proprio",
        rastreio="AD912266053BR",
        previsao_correios=hoje - timedelta(days=3),
        prazo_entrega_amazon=hoje + timedelta(days=2),
        status_bling="Em digitação",
    )


async def _botao(client, contexto: str):
    return await client.post(f"/api/informar/{contexto}/enviar")


async def _caso_devolucao(db, pedido: str = "294865", *, horas: float = 9) -> DevolucaoRastreio:
    """Devolução TikTok em Aguardando Devolução com a loja tendo de confirmar
    o pacote recebido em `horas` (dentro da janela de 24 h: aviso devido)."""
    db.add(
        Logistica(
            data=date.today(),
            pedido_bling=pedido,
            pedido_marketplace=f"57{pedido}",
            plataforma="TikTok",
            conta="mini",
            cliente_nome="Fulano de Tal",
        )
    )
    caso = DevolucaoRastreio(
        pedido_bling=pedido,
        fonte_auto="tiktok",
        devolucao_status_auto="BUYER_SHIPPED_ITEM",
        devolucao_tipo_auto="RETURN_AND_REFUND",
        acao_auto="SELLER_RESPOND_RECEIVE_PACKAGE",
        prazo_acao_auto=datetime.now(UTC) + timedelta(hours=horas),
        rastreio_auto="AP1BR",
        transportadora_auto="Correios",
    )
    db.add(caso)
    await db.commit()
    return caso


async def _prazo_resposta(db, pedido: str = "294865") -> dict[str, int]:
    """Item 10: a rodada do rastreio das devoluções (:10/:40) chega aos avisos
    com o caso relido nesta rodada (o `pedidos` que o sync passa)."""
    return await devolucao_acao_avisos.run(db, pedidos=[pedido])


async def _carimbo(db, caso: DevolucaoRastreio):
    await db.refresh(caso)
    return caso.aviso_prazo_acao_at


async def _amazon_prazos(db) -> Logistica:
    """Item 4: a rodada das 08:06/16:06 com um pedido de Envio próprio."""
    row = _pedido_amazon()
    db.add(row)
    await db.commit()
    await logistica_amazon_avisos.run(db)
    return row


def _envios() -> list[tuple]:
    return [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios]


def test_as_chaves_da_c9a_c9b_e_c9c_estao_migradas_com_o_remetente_de_hoje():
    for chave, remetente in REMETENTE_DE_HOJE.items():
        d = CATALOGO[chave]
        assert d.migrado and d.fatia == "F2", chave
        assert d.remetente == remetente, chave
        assert d.legado.informar == (LISTA_DE_HOJE[chave],), chave
    assert {c: informar_router._CHAVE_ENVIO[ctx] for c, ctx in BOTOES.items()} == {
        c: c for c in BOTOES
    }
    assert {CATALOGO[c].ao_desligar for c in ROBOS} == {"acumula"}
    assert {CATALOGO[c].ao_desligar for c in BOTOES} == {"recusa"}
    # a lista vazia de hoje é o desligado do robô (nasce desligado), não do botão
    assert all(threema_envios._lista_vazia_desliga(CATALOGO[c]) for c in ROBOS)
    assert not any(threema_envios._lista_vazia_desliga(CATALOGO[c]) for c in BOTOES)


# ─────────────────────────────────────────────── (a) Cadastro com a linha vazia


@pytest.mark.usefixtures("vw_devolucoes")
@pytest.mark.parametrize("chave", list(BOTOES))
async def test_botao_com_a_linha_vazia_manda_pelo_cadastro(db, monkeypatch, client, auth_as, chave):
    admin, joana, sumido = await _pessoas(db)
    await _linha_informar(db, LISTA_DE_HOJE[chave], "")
    # a lista do Cadastro tem quem foi suspenso também: ele fica de fora
    await semeado(db, chave, joana, sumido)
    auth_as(admin)
    r = await _botao(client, BOTOES[chave])
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == ["TSTJ0006"] and r.json()["mensagens"] == 1
    assert _envios() == [(chave, "cadastro", REMETENTE_DE_HOJE[chave], ["TSTJ0006"])]


async def test_amazon_prazos_com_a_linha_vazia_manda_pelo_cadastro_e_carimba(db):
    _admin, joana, sumido = await _pessoas(db)
    await _linha_informar(db, "logistica_amazon_auto", "")
    await semeado(db, K.LOGISTICA_AMAZON_PRAZOS, joana, sumido)
    row = await _amazon_prazos(db)
    chave = K.LOGISTICA_AMAZON_PRAZOS
    assert _envios() == [(chave, "cadastro", "logistica", ["TSTJ0006"])] * 2
    await db.refresh(row)
    assert row.aviso_previsao_correios_at is not None
    assert row.aviso_prazo_amazon_3d_at is not None
    # rodada seguinte: nada de novo
    _Fake.envios = []
    resumo = await logistica_amazon_avisos.run(db)
    assert resumo["avisos"] == 0 and _Fake.envios == []


async def test_prazo_resposta_com_a_linha_vazia_manda_pelo_cadastro_e_carimba(db):
    _admin, joana, sumido = await _pessoas(db)
    await _linha_informar(db, "devolucoes_auto", "")
    await semeado(db, K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA, joana, sumido)
    caso = await _caso_devolucao(db)
    resumo = await _prazo_resposta(db)
    assert (resumo["devidos"], resumo["enviados"]) == (1, 1)
    chave = K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA
    assert _envios() == [(chave, "cadastro", "devolucoes", ["TSTJ0006"])]
    assert "faltam 8 h pra responder na plataforma" in _Fake.envios[0]["texto"]
    assert await _carimbo(db, caso) is not None
    assert caso.aviso_prazo_acao_para == caso.prazo_acao_auto
    # rodada seguinte, mesmo prazo: nada de novo
    _Fake.envios = []
    resumo = await _prazo_resposta(db)
    assert resumo["enviados"] == 0 and _Fake.envios == []


# ─────────────────────────────────────────────── (b) desligado


@pytest.mark.usefixtures("vw_devolucoes")
@pytest.mark.parametrize("chave", list(BOTOES))
async def test_botao_desligado_recusa(db, client, auth_as, chave):
    admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    auth_as(admin)
    r = await _botao(client, BOTOES[chave])
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "envio_desligado"
    assert "Cadastros › Envios Threema" in r.json()["detail"]["message"]
    assert _Fake.envios == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    r = await _botao(client, BOTOES[chave])
    assert r.status_code == 200, r.text
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]


async def test_amazon_prazos_desligado_acumula(db):
    chave = K.LOGISTICA_AMAZON_PRAZOS
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    row = await _amazon_prazos(db)
    assert _Fake.envios == []
    await db.refresh(row)
    assert row.aviso_previsao_correios_at is None and row.aviso_prazo_amazon_3d_at is None
    # a rodada da tarde, ainda desligado: nada sai, nada é carimbado
    resumo = await logistica_amazon_avisos.run(db)
    assert (resumo["avisos"], resumo.get("desligado"), resumo["enviados"]) == (2, 1, 0)
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    resumo = await logistica_amazon_avisos.run(db)
    # religado: os pendentes saem de uma vez, uma mensagem por aviso
    assert resumo["enviados"] == 2
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"], ["TSTJ0006"]]
    textos = [e["texto"] for e in _Fake.envios]
    assert "previsão da transportadora passou" in textos[0]
    assert "faltam 2 dia(s)" in textos[1]
    await db.refresh(row)
    assert row.aviso_previsao_correios_at is not None and row.aviso_prazo_amazon_3d_at is not None


async def test_prazo_resposta_desligado_acumula(db):
    chave = K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    caso = await _caso_devolucao(db)
    resumo = await _prazo_resposta(db)
    assert (resumo["devidos"], resumo.get("desligado"), resumo["enviados"]) == (1, 1, 0)
    assert _Fake.envios == []
    assert await _carimbo(db, caso) is None
    # a rodada seguinte, ainda desligado: nada sai, nada é carimbado
    resumo = await _prazo_resposta(db)
    assert (resumo.get("desligado"), resumo["enviados"]) == (1, 0)
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    resumo = await _prazo_resposta(db)
    # religado: o aviso pendente (ainda na janela) sai na rodada seguinte
    assert resumo["enviados"] == 1
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]
    assert await _carimbo(db, caso) is not None


# ─────────────────────────────────────────────── (c) ninguém válido


@pytest.mark.usefixtures("vw_devolucoes")
@pytest.mark.parametrize("chave", list(BOTOES))
async def test_botao_ninguem_valido_registra_acende_o_sino_e_recusa(
    db, monkeypatch, client, auth_as, chave
):
    admin, _joana, sumido = await _pessoas(db)
    # a lista antiga tem gente: o Cadastro sem ninguém válido NUNCA cai nela
    await _linha_informar(db, LISTA_DE_HOJE[chave], "TSTZ0009")
    monkeypatch.setattr(get_settings(), "threema_recipients", "TSTZ0008")
    await semeado(db, chave, sumido)
    auth_as(admin)
    r = await _botao(client, BOTOES[chave])
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "sem_destinatarios"
    assert _Fake.envios == []
    assert [a.user_id for a in await _sinos(db, chave)] == [admin.id]
    (linha,) = await _registro(db, chave)
    assert (linha.ok, linha.erro, linha.via, linha.destinatario_id, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        None,
        REMETENTE_DE_HOJE[chave],
    )


async def test_amazon_prazos_ninguem_valido_registra_acende_o_sino_e_nao_carimba(db):
    chave = K.LOGISTICA_AMAZON_PRAZOS
    admin, joana, sumido = await _pessoas(db)
    await _linha_informar(db, "logistica_amazon_auto", "TSTZ0009")
    await semeado(db, chave, sumido)
    row = await _amazon_prazos(db)
    assert _Fake.envios == []
    await db.refresh(row)
    assert row.aviso_previsao_correios_at is None and row.aviso_prazo_amazon_3d_at is None
    assert [a.user_id for a in await _sinos(db, chave)] == [admin.id]
    (linha,) = await _registro(db, chave)
    assert (linha.ok, linha.erro, linha.contexto) == (False, "sem_destinatario_valido", "logistica")
    # a rodada da tarde registra de novo (1 por rodada), mas o sino do dia é um só
    resumo = await logistica_amazon_avisos.run(db)
    assert resumo["sem_destinatarios"] == 1 and resumo["enviados"] == 0
    assert len(await _registro(db, chave)) == 2
    assert len(await _sinos(db, chave)) == 1
    # alguém posto na lista: os pendentes saem na rodada seguinte
    await semeado(db, chave, joana)
    resumo = await logistica_amazon_avisos.run(db)
    assert resumo["enviados"] == 2
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"], ["TSTJ0006"]]


async def test_prazo_resposta_ninguem_valido_registra_acende_o_sino_e_nao_carimba(db):
    chave = K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA
    admin, joana, sumido = await _pessoas(db)
    # a lista antiga tem gente: o Cadastro sem ninguém válido NUNCA cai nela
    await _linha_informar(db, "devolucoes_auto", "TSTZ0009")
    await semeado(db, chave, sumido)
    caso = await _caso_devolucao(db)
    resumo = await _prazo_resposta(db)
    assert (resumo["sem_destinatarios"], resumo["enviados"]) == (1, 0)
    assert _Fake.envios == []
    assert await _carimbo(db, caso) is None
    assert [a.user_id for a in await _sinos(db, chave)] == [admin.id]
    (linha,) = await _registro(db, chave)
    assert (linha.ok, linha.erro, linha.via, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        "devolucoes",
    )
    # a rodada seguinte registra de novo (1 por rodada), mas o sino do dia é um só
    resumo = await _prazo_resposta(db)
    assert resumo["sem_destinatarios"] == 1 and resumo["enviados"] == 0
    assert len(await _registro(db, chave)) == 2
    assert len(await _sinos(db, chave)) == 1
    # alguém posto na lista: o pendente sai na rodada seguinte
    await semeado(db, chave, joana)
    resumo = await _prazo_resposta(db)
    assert resumo["enviados"] == 1
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]
    assert await _carimbo(db, caso) is not None


async def test_configuracao_antiga_vazia_fica_calada_como_hoje(db, monkeypatch, client, auth_as):
    """Emergência (o piso do .env) com a lista antiga vazia: o botão responde
    422 sem montar nada e o robô sai sem mensagem, registro nem sino — o
    comportamento de antes da fatia."""
    admin, joana, _ = await _pessoas(db)
    for chave in MINHAS:
        await semeado(db, chave, joana)
        await _linha_informar(db, LISTA_DE_HOJE[chave], "")
    monkeypatch.setattr(get_settings(), "threema_cadastro_ativo", False)
    auth_as(admin)
    for contexto in BOTOES.values():
        r = await _botao(client, contexto)
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "sem_destinatarios")
    row = await _amazon_prazos(db)
    await db.refresh(row)
    assert row.aviso_previsao_correios_at is None
    caso = await _caso_devolucao(db)
    resumo = await _prazo_resposta(db)
    assert (resumo["sem_destinatarios"], resumo["enviados"]) == (1, 0)
    assert await _carimbo(db, caso) is None
    assert _Fake.envios == []
    for chave in MINHAS:
        assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    # e com gente na lista antiga, a emergência manda para ela (via legado)
    await _linha_informar(db, "logistica", ID["harry"])
    r = await _botao(client, "logistica")
    assert r.status_code == 200, r.text
    assert _envios() == [(K.LOGISTICA_INFORMAR, "legado", "logistica", [ID["harry"]])]


# ─────────────────────────────────────────────── as rotas


async def test_put_da_lista_migrada_responde_410_e_o_get_mostra_a_antiga(
    db, client, auth_as, make_user
):
    admin = await make_user(role=UserRole.ADMIN)
    await _linha_informar(db, "logistica", ID["harry"])
    auth_as(admin)
    for contexto in LISTA_DE_HOJE.values():
        r = await client.put(f"/api/informar/{contexto}", json={"recipients": [ID["joana"]]})
        assert r.status_code == 410, (contexto, r.text)
        detalhe = r.json()["detail"]
        assert detalhe["code"] == "quem_recebe_no_cadastro"
        assert "Cadastros › Envios Threema" in detalhe["message"]
        assert detalhe["envios"] == [c for c in MINHAS if LISTA_DE_HOJE[c] == contexto], contexto
    # a lista antiga não mudou (é a reserva de emergência) e o GET a mostra
    r = await client.get("/api/informar/logistica")
    assert r.status_code == 200 and r.json()["recipients"] == [ID["harry"]]
    # contexto ainda não migrado continua gravando
    r = await client.put("/api/informar/margem", json={"recipients": []})
    assert r.status_code == 200, r.text


def test_o_put_so_recusa_quando_todos_os_envios_da_lista_leem_o_cadastro(monkeypatch):
    """A lista do `chamados_ia` é lida pela IA de Chamado (15) e, de reserva,
    pelo robô da Denúncia (44); a do `margem_auto` é o Avisar do Robô da
    Margem (25 a 27 e o robô 34). Só quando todos migram o PUT recusa."""
    from tests.test_threema_resolver import desmigrar, migrar

    envios = {
        ctx: {d.chave for d in informar_router._envios_da_lista(ctx)}
        for ctx in informar_router._CONTEXTOS
    }
    assert envios["chamados_ia"] == {K.CHAMADOS_IA_PARADA, K.DENUNCIA_ROBO_PRECISA_ALGUEM}
    assert envios["margem_auto"] == {
        K.MARGEM_PEDIDO_SEGURADO,
        K.MARGEM_REAVALIACAO,
        K.MARGEM_MARGEM_ALTA,
        K.OUVIDORIA_VIGIA_MARGEM,
    }
    assert envios["logistica_amazon_auto"] == {K.LOGISTICA_AMAZON_PRAZOS}
    assert all(envios.values()), "toda lista do Informar é lida por algum envio"
    # o código de antes da C9d (os dois ainda na configuração antiga)
    desmigrar(monkeypatch, K.CHAMADOS_IA_PARADA, K.DENUNCIA_ROBO_PRECISA_ALGUEM)
    assert not informar_router._lista_no_cadastro("chamados_ia")
    migrar(monkeypatch, K.CHAMADOS_IA_PARADA)
    assert not informar_router._lista_no_cadastro("chamados_ia")
    migrar(monkeypatch, K.DENUNCIA_ROBO_PRECISA_ALGUEM)
    assert informar_router._lista_no_cadastro("chamados_ia")


async def test_gerente_perde_a_edicao_mas_segue_enviando_e_vendo_quem_recebe(
    db, client, auth_as, make_user
):
    """Aba Amazon: o gerente (e-mail liberado no botão) não grava mais a lista
    (nem no Informar, nem no Cadastro, que é só de admin), mas o "Enviar
    agora" e o "quem recebe" continuam para ele. Quem não pode usar o botão
    hoje continua sem poder."""
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, K.LOGISTICA_AMAZON_INFORMAR, joana)
    await semeado(db, K.LOGISTICA_AMAZON_PRAZOS, joana)
    gerente = await make_user(email=GERENTE, role=UserRole.USER)
    auth_as(gerente)
    for contexto in ("logistica_amazon", "logistica_amazon_auto"):
        r = await client.put(f"/api/informar/{contexto}", json={"recipients": [ID["harry"]]})
        assert r.status_code == 410, r.text
    r = await client.patch(
        f"/api/threema/envios/{K.LOGISTICA_AMAZON_INFORMAR}",
        json={"destinatarios": [{"tipo": "usuario", "id": str(joana.id)}]},
    )
    assert r.status_code == 403, r.text
    r = await _botao(client, "logistica_amazon")
    assert r.status_code == 200, r.text
    assert _envios() == [
        (K.LOGISTICA_AMAZON_INFORMAR, "cadastro", "logistica_amazon", ["TSTJ0006"])
    ]
    for chave in (K.LOGISTICA_AMAZON_INFORMAR, K.LOGISTICA_AMAZON_PRAZOS):
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 200, r.text
        q = r.json()
        assert q["nomes"] == ["joana c9a"] and q["usa_configuracao_antiga"] is False
        assert "TSTJ0006" not in r.text
    # os Informar da Logística, do Controle de Estoque e das Devoluções seguem
    # só de admin (botão e quem recebe; nas Devoluções, também o do prazo)
    for chave in (K.LOGISTICA_INFORMAR, K.CONTROLE_ESTOQUE_INFORMAR, K.DEVOLUCOES_INFORMAR):
        assert (await _botao(client, BOTOES[chave])).status_code == 403, chave
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 403, chave
    r = await client.get(f"/api/threema/envios/{K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA}/quem-recebe")
    assert r.status_code == 403
    for contexto in ("devolucoes", "devolucoes_auto"):
        r = await client.put(f"/api/informar/{contexto}", json={"recipients": [ID["harry"]]})
        assert r.status_code == 403, r.text
    operador = await make_user(role=UserRole.USER, permissions={"logistica": {"edit": True}})
    auth_as(operador)
    assert (await _botao(client, "logistica_amazon")).status_code == 403
    r = await client.put("/api/informar/logistica_amazon", json={"recipients": []})
    assert r.status_code == 403


# ─────────────────────────────────────────────── a semente da fatia


@pytest.mark.usefixtures("limpar_ouvidoria", "vw_devolucoes")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_ponto_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, client, auth_as, make_user, variante
):
    await montar(db, monkeypatch, variante=variante)
    esperado = {
        c: list(threema_envios._dedupe((await threema_envios.legado(c, session=db)).ids))
        for c in MINHAS
    }
    await threema_envios.semear(db, fatia="F2", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in MINHAS]
    # a lista antiga fica congelada como reserva, mas o ponto não lê mais:
    # esvaziar não muda quem recebe
    for contexto in LISTA_DE_HOJE.values():
        await _linha_informar(db, contexto, "")
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    for chave, contexto in BOTOES.items():
        _Fake.envios = []
        r = await _botao(client, contexto)
        if esperado[chave]:
            assert r.status_code == 200, (chave, r.text)
            assert _envios() == [(chave, "cadastro", contexto, esperado[chave])], chave
        else:
            assert (r.status_code, r.json()["detail"]["code"]) == (422, "sem_destinatarios")
            assert _Fake.envios == []
    _Fake.envios = []
    await _amazon_prazos(db)
    chave = K.LOGISTICA_AMAZON_PRAZOS
    if esperado[chave]:
        assert _envios() == [(chave, "cadastro", "logistica", esperado[chave])] * 2
    else:
        # linha vazia hoje = calado: a semente desligou, nada de registro nem sino
        linha = await db.get(ThreemaEnvio, chave, populate_existing=True)
        assert linha.ativo is False
        assert _Fake.envios == []
        assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    if variante == "cheio":
        assert esperado[K.LOGISTICA_INFORMAR] == [ID["harry"], ID["heisenberg"]]
        assert esperado[K.CONTROLE_ESTOQUE_INFORMAR] == [ID["harry"], ID["heisenberg"]]
        assert esperado[chave] == [ID["londres"], ID["harry"], ID["cairo_sa"]]
    # Prazo de resposta das Devoluções (10): em todas as variantes a linha
    # `devolucoes_auto` não existe ou está vazia (produção: não existe) — hoje
    # ninguém recebe, calado. A semente desliga e a rodada segue calada: nada
    # de registro nem sino a cada :10/:40.
    chave = K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA
    assert esperado[chave] == [] and esperado[K.DEVOLUCOES_INFORMAR] == []
    linha = await db.get(ThreemaEnvio, chave, populate_existing=True)
    assert linha.ativo is False
    _Fake.envios = []
    caso = await _caso_devolucao(db)
    resumo = await _prazo_resposta(db)
    assert (resumo["devidos"], resumo.get("desligado"), resumo["enviados"]) == (1, 1, 0)
    assert await _carimbo(db, caso) is None
    assert _Fake.envios == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    # o botão (11) vazio fica ligado sem ninguém, como os outros botões: o
    # "Enviar agora" segue recusando com 422 (o atalho nem deixa clicar)
    linha = await db.get(ThreemaEnvio, K.DEVOLUCOES_INFORMAR, populate_existing=True)
    assert linha.ativo is None


@pytest.mark.usefixtures("limpar_ouvidoria", "vw_devolucoes")
async def test_devolucoes_com_gente_na_lista_semente_confere_e_suspenso_para_de_receber(
    db, monkeypatch, client, auth_as, make_user
):
    """Se as listas das Devoluções tiverem gente na hora da semente (hoje não
    têm), o Cadastro nasce com as mesmas pessoas (o contato externo também),
    ligado, e cada ponto manda EXATAMENTE para elas; quem for suspenso depois
    para de receber, embora a lista antiga (a reserva) ainda o tenha."""
    c = await montar(db, monkeypatch)
    await _linha_informar(db, "devolucoes", f"{ID['harry']},{ID['cairo_sa']}")
    await _linha_informar(db, "devolucoes_auto", f"{ID['londres']},{ID['roma']}")
    minhas = [K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA, K.DEVOLUCOES_INFORMAR]
    await threema_envios.semear(db, fatia="F2", chaves=minhas, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=minhas)
    assert [(x.chave, x.situacao) for x in itens] == [(k, "igual") for k in minhas]
    for k in minhas:
        assert (await db.get(ThreemaEnvio, k, populate_existing=True)).ativo is None, k
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await _botao(client, "devolucoes")
    assert r.status_code == 200, r.text
    caso = await _caso_devolucao(db)
    await _prazo_resposta(db)
    assert _envios() == [
        # "cairo sa" ativo, mesmo com o "cairo" suspenso de mesmo ID (M5)
        (K.DEVOLUCOES_INFORMAR, "cadastro", "devolucoes", [ID["harry"], ID["cairo_sa"]]),
        (K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA, "cadastro", "devolucoes", [ID["londres"], ID["roma"]]),
    ]
    assert await _carimbo(db, caso) is not None
    # suspensos depois da semente: param de receber
    c.usuarios["harry"].status = UserStatus.SUSPENDED
    c.usuarios["londres"].status = UserStatus.SUSPENDED
    await db.commit()
    threema_envios.limpar_cache()
    _Fake.envios = []
    r = await _botao(client, "devolucoes")
    assert r.status_code == 200, r.text
    await _caso_devolucao(db, "294866")
    await _prazo_resposta(db, "294866")
    assert [e["destinos"] for e in _Fake.envios] == [[ID["cairo_sa"]], [ID["roma"]]]
    # a lista antiga ainda mandaria para eles
    assert ID["harry"] in (await threema_envios.legado(K.DEVOLUCOES_INFORMAR, session=db)).ids
    leg = await threema_envios.legado(K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA, session=db)
    assert ID["londres"] in leg.ids


@pytest.mark.usefixtures("vw_devolucoes")
async def test_operador_das_devolucoes_nao_edita_quem_recebe_e_o_admin_segue_enviando(
    db, client, auth_as, make_user
):
    """O Informar das Devoluções sempre foi só de admin (botão e lista): quem
    tem `devolucoes:edit` continua sem o botão e não edita quem recebe nem no
    Informar nem no Cadastro. O admin não grava mais a lista antiga (410), mas
    o "Enviar agora" segue com ele."""
    admin, joana, _ = await _pessoas(db)
    await _linha_informar(db, "devolucoes", ID["harry"])
    await semeado(db, K.DEVOLUCOES_INFORMAR, joana)
    await semeado(db, K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA, joana)
    operador = await make_user(
        role=UserRole.USER, permissions={"devolucoes": {"view": True, "edit": True}}
    )
    auth_as(operador)
    assert (await _botao(client, "devolucoes")).status_code == 403
    for contexto in ("devolucoes", "devolucoes_auto"):
        r = await client.put(f"/api/informar/{contexto}", json={"recipients": []})
        assert r.status_code == 403, (contexto, r.text)
    for chave in (K.DEVOLUCOES_INFORMAR, K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA):
        r = await client.patch(
            f"/api/threema/envios/{chave}",
            json={"destinatarios": [{"tipo": "usuario", "id": str(joana.id)}]},
        )
        assert r.status_code == 403, (chave, r.text)
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 403, (chave, r.text)
    assert _Fake.envios == []
    auth_as(admin)
    for contexto in ("devolucoes", "devolucoes_auto"):
        r = await client.put(f"/api/informar/{contexto}", json={"recipients": [ID["joana"]]})
        assert r.status_code == 410, (contexto, r.text)
    r = await client.get("/api/informar/devolucoes")
    assert r.status_code == 200 and r.json()["recipients"] == [ID["harry"]]
    r = await _botao(client, "devolucoes")
    assert r.status_code == 200, r.text
    assert _envios() == [(K.DEVOLUCOES_INFORMAR, "cadastro", "devolucoes", ["TSTJ0006"])]
    for chave in (K.DEVOLUCOES_INFORMAR, K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA):
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 200, r.text
        assert r.json()["nomes"] == ["joana c9a"] and "TSTJ0006" not in r.text


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(
    db, monkeypatch, client, auth_as, make_user
):
    c = await montar(db, monkeypatch)
    await threema_envios.semear(db, fatia="F2", chaves=MINHAS, aplicar=True)
    await db.commit()
    c.usuarios["londres"].status = UserStatus.SUSPENDED
    c.usuarios["heisenberg"].status = UserStatus.SUSPENDED
    await db.commit()
    await _amazon_prazos(db)
    auth_as(await make_user(role=UserRole.ADMIN))
    for contexto in ("logistica", "controle_estoque"):
        r = await _botao(client, contexto)
        assert r.status_code == 200, r.text
    assert [e["destinos"] for e in _Fake.envios] == [
        [ID["harry"], ID["cairo_sa"]],
        [ID["harry"], ID["cairo_sa"]],
        [ID["harry"]],
        [ID["harry"]],
    ]
    # a lista antiga ainda mandaria para os dois
    leg = await threema_envios.legado(K.LOGISTICA_AMAZON_PRAZOS, session=db)
    assert ID["londres"] in leg.ids
    for chave in (K.LOGISTICA_INFORMAR, K.CONTROLE_ESTOQUE_INFORMAR):
        leg = await threema_envios.legado(chave, session=db)
        assert ID["heisenberg"] in leg.ids, chave
