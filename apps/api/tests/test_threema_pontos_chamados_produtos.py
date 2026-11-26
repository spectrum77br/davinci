"""F1/C8c da tarefa 16: os avisos de Chamados e de Produtos que liam o .env
passam a ler o Cadastro (Cadastros › Envios Threema) — itens 13 (TikTok só
reembolso), 14 (abertura de chamado presa, com o remetente da ORIGEM do
chamado) e 24 (soma por família atrasada).

Um teste por ponto, PELO CAMINHO REAL da função (só o ThreemaClient é falso;
a TikTok e os anúncios são os falsos dos testes de cada módulo):

* (a) Cadastro semeado com o .env VAZIO: manda para quem está no Cadastro,
  com a chave do envio e o remetente de hoje ("devolucoes" no 13; no 14,
  "devolucoes" para chamado de devolução ou de reembolso e "logistica" para
  o resto, uma mensagem por remetente; "estoque" no 24);
* (b) desligado: não manda, e o carimbo segue o `ao_desligar` do catálogo —
  os três descartam: o 13 carimba `aviso_prazo_acao_at` mesmo sem envio (a
  C0 é tarefa à parte), o 14 grava o carimbo da rota no histórico do chamado
  antes do aviso e o 24 não tem carimbo (a rodada seguinte avisa o estado de
  agora);
* (c) ninguém válido (a única pessoa da lista foi suspensa): não manda,
  acende o sino dos admins e grava o registro `ok=false`.

E a semente da fatia: `semear` + `conferir` das 3 chaves dá 0 diferenças e,
depois dela, cada ponto manda EXATAMENTE para quem o legado mandaria (as duas
leis do desenho: nenhum aviso para de sair; nenhum vai para quem foi
suspenso). O .env vazio é o desligado de hoje destes três (o 24 nasceu
assim de propósito): a semente grava o envio desligado e a rodada segue
calada — sem mensagem, sem registro e sem sino, como antes.
"""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    DevolucaoRastreio,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    UserRole,
    UserStatus,
)
from app.services import (
    chamados_pendencias,
    estoque_familia,
    threema,
    threema_envios,
    threema_registro,
    vigia_estoque_familia,
)
from app.services import chamados_tiktok_reembolso as tiktok
from app.services.threema_catalogo import CATALOGO, K
from tests.test_chamados_pendencias import _caso, _notas
from tests.test_chamados_tiktok_reembolso import _em, _FakeTikTok, _seed
from tests.test_threema_pontos_logistica_devolucoes import _Fake, _ligar, _registro, _sinos
from tests.test_threema_resolver import _usuario, semeado
from tests.test_vigia_estoque_familia import _anuncio, _Config
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.asyncio

# As 3 chaves da C8c e o remetente de HOJE de cada ponto (o `contexto` que o
# código passava ao ThreemaClient antes da fatia). O 14 é dinâmico: aqui, o
# de um chamado de devolução (o de logística tem teste próprio).
REMETENTE_DE_HOJE = {
    K.CHAMADOS_TIKTOK_SO_REEMBOLSO: "devolucoes",
    K.CHAMADOS_ABERTURAS_PRESAS: "devolucoes",
    K.PRODUTOS_VIGIA_SOMA_FAMILIA: "estoque",
}
MINHAS = list(REMETENTE_DE_HOJE)
# O .env que estes pontos liam (fica congelado como reserva de emergência).
_ENVS = (
    "tiktok_reembolso_threema_recipients",
    "nf_sem_estoque_threema_recipients",
    "vigia_estoque_familia_threema_recipients",
    "threema_recipients",
)
_SEQ = itertools.count(1)
# O cenário que os ajudantes criam uma vez por teste (a loja TikTok com o caso
# falso, os anúncios da família) e reusam nas rodadas seguintes.
_CENARIO: dict[str, object] = {}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _Fake.envios = []
    _CENARIO.clear()
    threema_envios.limpar_cache()
    threema_registro.reiniciar_disjuntor()
    s = get_settings()
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    monkeypatch.setattr(s, "threema_registro_ativo", True)
    for nome in _ENVS:  # o .env VAZIO: quem recebe só pode vir do Cadastro
        monkeypatch.setattr(s, nome, "")
    monkeypatch.setattr(threema, "ThreemaClient", _Fake)
    # A soma por família ligada só para "dg053" (como em test_vigia_estoque_familia).
    cfg = _Config(ativo=True, prefixos="dg053", destinatarios="")
    monkeypatch.setattr(estoque_familia, "get_settings", lambda: cfg)
    monkeypatch.setattr(vigia_estoque_familia, "get_settings", lambda: cfg)
    yield
    threema_envios.limpar_cache()
    _CENARIO.clear()


async def _pessoas(db):
    admin = await _usuario(db, "admin c8c", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c8c", "TSTJ0006")
    sumido = await _usuario(db, "sumido c8c", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, sumido


# ─────────────────────────────────────────────── os pontos, pelo caminho real


async def _tiktok_so_reembolso(db, monkeypatch, make_user, *, agora=None) -> dict:
    """Item 13: o vigia da TikTok (:10/:40) acha o caso só reembolso sem
    resposta faltando menos de 12 h (o aviso de 12 h)."""
    if "tiktok" not in _CENARIO:
        _CENARIO["tiktok"] = _FakeTikTok()
        await _seed(db, make_user, _CENARIO["tiktok"], monkeypatch)
    return await tiktok.run_vigia(db, agora=agora or _em(11))


async def _aberturas_presas(db, monkeypatch, make_user, *, origem: str = "devolucao") -> str:
    """Item 14: a varredura das :25 acha a abertura presa há 2 dias por falta
    de foto (precisa de gente)."""
    pedido = f"77{next(_SEQ):04d}"
    await _caso(db, pedido=pedido, erro="devolucao_sem_foto", origem=origem)
    await chamados_pendencias.varrer(db)
    return pedido


async def _soma_familia(db, monkeypatch, make_user) -> dict:
    """Item 24: a rodada das 09:20/16:20 acha o anúncio do .ci preso no total
    velho (vendeu no .sp; o .ci não foi republicado)."""
    if "anuncios" not in _CENARIO:
        u = await make_user()
        await _anuncio(db, u, "dg053.sp", estoque=900, publicado=1190, sincronizado_ha_horas=1)
        await _anuncio(db, u, "dg053.ci", estoque=290, publicado=217, sincronizado_ha_horas=48)
        _CENARIO["anuncios"] = True
    return await vigia_estoque_familia.vigia_estoque_familia_sweep()


PONTOS = {
    K.CHAMADOS_TIKTOK_SO_REEMBOLSO: _tiktok_so_reembolso,
    K.CHAMADOS_ABERTURAS_PRESAS: _aberturas_presas,
    K.PRODUTOS_VIGIA_SOMA_FAMILIA: _soma_familia,
}


def test_as_chaves_da_c8c_estao_migradas_com_o_remetente_de_hoje():
    assert set(PONTOS) == set(MINHAS)
    for chave in MINHAS:
        d = CATALOGO[chave]
        assert d.migrado and d.fatia == "F1", chave
    assert CATALOGO[K.CHAMADOS_TIKTOK_SO_REEMBOLSO].remetente == "devolucoes"
    # dinâmico: quem passa o remetente é o ponto, pela origem do chamado
    assert CATALOGO[K.CHAMADOS_ABERTURAS_PRESAS].remetente is None
    assert CATALOGO[K.PRODUTOS_VIGIA_SOMA_FAMILIA].remetente == "estoque"


# ─────────────────────────────────────────────── (a) Cadastro com o .env vazio


@pytest.mark.parametrize("chave", MINHAS)
async def test_cadastro_com_env_vazio_manda_pela_chave_e_pelo_remetente_de_hoje(
    db, monkeypatch, make_user, chave
):
    _admin, joana, sumido = await _pessoas(db)
    # a lista do Cadastro tem quem foi suspenso também: ele fica de fora
    await semeado(db, chave, joana, sumido)
    await PONTOS[chave](db, monkeypatch, make_user)
    assert [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios] == [
        (chave, "cadastro", REMETENTE_DE_HOJE[chave], ["TSTJ0006"])
    ]


async def test_tiktok_so_reembolso_manda_o_aviso_inteiro(db, monkeypatch, make_user):
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, K.CHAMADOS_TIKTOK_SO_REEMBOLSO, joana)
    resumo = await _tiktok_so_reembolso(db, monkeypatch, make_user)
    assert resumo["avisos_12h"] == 1, resumo
    (envio,) = _Fake.envios
    assert "SÓ REEMBOLSO" in envio["texto"] and "293798" in envio["texto"]


@pytest.mark.parametrize(
    ("origem", "remetente"),
    [
        ("devolucao", "devolucoes"),
        ("logistica", "logistica"),
        ("manual", "logistica"),
    ],
)
async def test_aberturas_presas_o_remetente_segue_a_origem_do_chamado(
    db, monkeypatch, make_user, origem, remetente
):
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, K.CHAMADOS_ABERTURAS_PRESAS, joana)
    pedido = await _aberturas_presas(db, monkeypatch, make_user, origem=origem)
    assert [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios] == [
        (K.CHAMADOS_ABERTURAS_PRESAS, "cadastro", remetente, ["TSTJ0006"])
    ]
    assert pedido in _Fake.envios[0]["texto"]


async def test_aberturas_presas_lote_misto_uma_mensagem_por_remetente_mesma_lista(
    db, monkeypatch, make_user
):
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, K.CHAMADOS_ABERTURAS_PRESAS, joana)
    await _caso(db, pedido="900111", erro="devolucao_sem_foto")
    await _caso(db, pedido="900112", erro="devolucao_sem_foto", origem="logistica")
    await _caso(
        db,
        pedido="900113",
        erro="devolucao_sem_return",
        origem="vendas",
        origem_ref="tiktok_reembolso:antigo",
    )
    r = await chamados_pendencias.varrer(db)
    assert r["novo"] == 3
    por_remetente = {e["contexto"]: e for e in _Fake.envios}
    assert sorted(por_remetente) == ["devolucoes", "logistica"]
    assert {e["chave"] for e in _Fake.envios} == {K.CHAMADOS_ABERTURAS_PRESAS}
    assert all(e["destinos"] == ["TSTJ0006"] for e in _Fake.envios)
    assert "900111" in por_remetente["devolucoes"]["texto"]
    assert "900113" in por_remetente["devolucoes"]["texto"]
    assert "900112" in por_remetente["logistica"]["texto"]


async def test_soma_familia_resumo_conta_quem_recebeu(db, monkeypatch, make_user):
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, K.PRODUTOS_VIGIA_SOMA_FAMILIA, joana)
    resumo = await _soma_familia(db, monkeypatch, make_user)
    assert (resumo["atrasados"], resumo["avisados"], resumo["falharam"]) == (1, 1, 0)
    assert "dg053.ci" in _Fake.envios[0]["texto"]


# ─────────────────────────────────────────────── (c) ninguém válido


@pytest.mark.parametrize("chave", MINHAS)
async def test_ninguem_valido_nao_manda_acende_o_sino_e_registra(db, monkeypatch, make_user, chave):
    admin, _joana, sumido = await _pessoas(db)
    # o .env tem gente: lista vazia NUNCA pode cair nele
    monkeypatch.setattr(get_settings(), "threema_recipients", "TSTZ0009")
    monkeypatch.setattr(get_settings(), "tiktok_reembolso_threema_recipients", "TSTZ0009")
    monkeypatch.setattr(get_settings(), "vigia_estoque_familia_threema_recipients", "TSTZ0009")
    await semeado(db, chave, sumido)
    await PONTOS[chave](db, monkeypatch, make_user)
    assert _Fake.envios == []
    assert [(a.user_id, a.escopo) for a in await _sinos(db, chave)] == [(None, "adm")]
    (linha,) = await _registro(db, chave)
    assert (linha.ok, linha.erro, linha.via, linha.destinatario_id, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        None,
        REMETENTE_DE_HOJE[chave],
    )


async def test_soma_familia_ninguem_valido_vira_recipients_vazio_no_resumo(
    db, monkeypatch, make_user
):
    """Ligado com a única pessoa da lista suspensa: não manda, o resumo diz
    `recipients_vazio` e o sino dos admins acende (uma vez por dia)."""
    admin, _joana, sumido = await _pessoas(db)
    await semeado(db, K.PRODUTOS_VIGIA_SOMA_FAMILIA, sumido)
    resumo = await _soma_familia(db, monkeypatch, make_user)
    assert resumo["atrasados"] == 1 and resumo["aviso"] == "recipients_vazio"
    assert "avisados" not in resumo
    assert _Fake.envios == []
    assert [(a.user_id, a.escopo) for a in await _sinos(db, K.PRODUTOS_VIGIA_SOMA_FAMILIA)] == [
        (None, "adm")
    ]
    # a rodada das 16:20 registra de novo, mas o sino do dia é um só
    await _soma_familia(db, monkeypatch, make_user)
    assert len(await _registro(db, K.PRODUTOS_VIGIA_SOMA_FAMILIA)) == 2
    assert len(await _sinos(db, K.PRODUTOS_VIGIA_SOMA_FAMILIA)) == 1


async def test_soma_familia_semeada_com_o_env_vazio_nasce_desligada_e_sem_sino(
    db, monkeypatch, make_user
):
    """O item 24 de hoje: o .env do vigia vazio de propósito (mede e loga, não
    avisa ninguém). A semente da F1 grava o envio DESLIGADO — `ativo` NULL
    seria "ligado com ninguém", com registro `ok=false` e sino com Telegram
    para cada admin a cada rodada — e a conferência dá igual. A rodada segue
    como hoje: `envio_desligado` no resumo, sem mensagem, registro nem sino."""
    chave = K.PRODUTOS_VIGIA_SOMA_FAMILIA
    await _pessoas(db)  # há admin para receber o sino, se ele acendesse
    res = await threema_envios.semear(db, fatia="F1", chaves=[chave], aplicar=True)
    await db.commit()
    (plano,) = res.planos
    assert (plano.refs, plano.ativo) == ([], False)
    assert any("nasce DESLIGADO" in linha for linha in threema_envios._texto_semente(res))
    linha = await db.get(ThreemaEnvio, chave, populate_existing=True)
    assert linha.semeado_em is not None and linha.ativo is False
    (item,) = await threema_envios.conferir(db, chaves=[chave])
    assert (item.situacao, item.ativo_legado, item.ativo_cadastro) == ("igual", False, False)
    for _ in range(2):  # 09:20 e 16:20
        resumo = await _soma_familia(db, monkeypatch, make_user)
        assert resumo["atrasados"] == 1 and resumo["aviso"] == "envio_desligado"
    assert _Fake.envios == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []


async def test_conferir_acusa_soma_familia_ligada_sem_ninguem(db, monkeypatch, make_user):
    """Semente sem o desligado (a de antes desta correção): `ativo` NULL sem
    nenhuma linha é o desligado de hoje deste envio (revisão da F2,
    `_interruptor_cadastro`) — igual ao legado, calado, sem registro nem sino.
    Ligado à mão sem ninguém, o portão da fatia tem de mostrar a diferença:
    com o .env vazio o legado está desligado, e o Cadastro ligado sem ninguém
    acende o sino."""
    chave = K.PRODUTOS_VIGIA_SOMA_FAMILIA
    await _pessoas(db)  # há admin para receber o sino, se ele acendesse
    await semeado(db, chave)  # semeado sem ninguém, `ativo` NULL
    (item,) = await threema_envios.conferir(db, chaves=[chave])
    assert (item.situacao, item.ativo_legado, item.ativo_cadastro) == ("igual", False, False)
    resumo = await _soma_familia(db, monkeypatch, make_user)
    assert resumo["atrasados"] == 1 and resumo["aviso"] == "envio_desligado"
    assert _Fake.envios == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    await _ligar(db, chave, True)
    (item,) = await threema_envios.conferir(db, chaves=[chave])
    assert (item.situacao, item.ativo_legado, item.ativo_cadastro) == ("diferente", False, True)
    assert item.so_no_legado == [] and item.so_no_cadastro == []
    await db.commit()  # a linha de comando abre a sessão dela
    saida: list[str] = []
    assert await threema_envios.executar(["conferir", "--chaves", chave], saida.append) == 1
    texto = "\n".join(saida)
    assert "lista do .env vazia" in texto and "desligue em Cadastros › Envios Threema" in texto


# ─────────────────────────────────────────────── (b) desligado, ponto a ponto


async def test_tiktok_so_reembolso_desligado_descarta(db, monkeypatch, make_user):
    chave = K.CHAMADOS_TIKTOK_SO_REEMBOLSO
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    resumo = await _tiktok_so_reembolso(db, monkeypatch, make_user)
    assert resumo["avisos_12h"] == 1, resumo  # a rodada achou o aviso devido
    assert _Fake.envios == []
    # e carimbou do mesmo jeito (C0 à parte): o aviso de 12 h se perdeu
    rast = await db.get(DevolucaoRastreio, "293798", populate_existing=True)
    assert rast.aviso_prazo_acao_at is not None
    assert await _registro(db, chave) == []  # desligado não grava registro
    assert await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    # religado na mesma faixa: o aviso de 12 h não volta
    resumo = await _tiktok_so_reembolso(db, monkeypatch, make_user, agora=_em(10))
    assert resumo["avisos_12h"] == 0 and _Fake.envios == []
    # o de 3 h sai normalmente
    resumo = await _tiktok_so_reembolso(db, monkeypatch, make_user, agora=_em(2))
    assert resumo["avisos_3h"] == 1
    assert [(e["destinos"], e["texto"][:1]) for e in _Fake.envios] == [(["TSTJ0006"], "🚨")]
    assert "ÚLTIMO AVISO" in _Fake.envios[0]["texto"]


async def test_aberturas_presas_desligado_descarta(db, monkeypatch, make_user):
    chave = K.CHAMADOS_ABERTURAS_PRESAS
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    ch = await _caso(db, pedido="900201", erro="devolucao_sem_foto")
    r = await chamados_pendencias.varrer(db)
    assert r["novo"] == 1 and _Fake.envios == []
    # o carimbo da rota foi gravado no histórico do chamado
    assert [n for n in await _notas(db, ch) if chamados_pendencias.MARCA in n]
    assert await _registro(db, chave) == []
    assert await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    # religado: a mesma abertura não volta (o aviso daquele período se perdeu)
    r = await chamados_pendencias.varrer(db)
    assert r["novo"] == 0 and _Fake.envios == []
    # abertura nova sai normalmente
    await _caso(db, pedido="900202", erro="devolucao_sem_foto")
    await chamados_pendencias.varrer(db)
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]
    assert "900202" in _Fake.envios[0]["texto"] and "900201" not in _Fake.envios[0]["texto"]


async def test_soma_familia_desligado_descarta(db, monkeypatch, make_user):
    chave = K.PRODUTOS_VIGIA_SOMA_FAMILIA
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    resumo = await _soma_familia(db, monkeypatch, make_user)
    # mede e loga, mas não manda
    assert resumo["atrasados"] == 1 and resumo["aviso"] == "envio_desligado"
    assert _Fake.envios == []
    assert await _registro(db, chave) == []
    assert await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    # religado: sai o estado de AGORA, uma mensagem (nada acumulado)
    resumo = await _soma_familia(db, monkeypatch, make_user)
    assert resumo["avisados"] == 1
    (envio,) = _Fake.envios
    assert envio["destinos"] == ["TSTJ0006"] and "1 de 2 anúncios" in envio["texto"]


# ─────────────────────────────────────────────── a semente da fatia


_SOMA_NO_ENV = {"cheio": ("londres", "harry"), "reservas": (), "vazias": ()}


@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_ponto_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, make_user, variante
):
    await montar(db, monkeypatch, variante=variante)
    # o .env do vigia da soma (o cenário de produção não tem): com gente no
    # "cheio", para a semente copiar alguém
    soma = ",".join(ID[q] for q in _SOMA_NO_ENV[variante])
    monkeypatch.setattr(get_settings(), "vigia_estoque_familia_threema_recipients", soma)
    esperado = {
        c: list(threema_envios._dedupe((await threema_envios.legado(c, session=db)).ids))
        for c in MINHAS
    }
    await threema_envios.semear(db, fatia="F1", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in MINHAS]
    # o .env fica congelado como reserva, mas o ponto não lê mais: esvaziar
    # não muda quem recebe
    for nome in _ENVS:
        monkeypatch.setattr(get_settings(), nome, "")
    for chave in MINHAS:
        _Fake.envios = []
        await PONTOS[chave](db, monkeypatch, make_user)
        enviados = [(e["chave"], e["via"], e["destinos"]) for e in _Fake.envios]
        assert enviados == ([(chave, "cadastro", esperado[chave])] if esperado[chave] else []), (
            chave
        )
        if not esperado[chave]:
            # .env vazio hoje = calado: a semente desligou, nada de sino
            assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    if variante == "cheio":
        assert esperado[K.CHAMADOS_TIKTOK_SO_REEMBOLSO] == [ID["heisenberg"], ID["cairo_sa"]]
        assert esperado[K.CHAMADOS_ABERTURAS_PRESAS] == [ID["heisenberg"], ID["cairo_sa"]]
        assert esperado[K.PRODUTOS_VIGIA_SOMA_FAMILIA] == [ID["londres"], ID["harry"]]
    if variante == "reservas":
        # TikTok vazio no .env: a reserva (o grupo do sem estoque) foi semeada
        reserva = [ID["londres"], ID["harry"], ID["heisenberg"], ID["cairo_sa"]]
        assert esperado[K.CHAMADOS_TIKTOK_SO_REEMBOLSO] == reserva
        assert esperado[K.CHAMADOS_ABERTURAS_PRESAS] == reserva


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(db, monkeypatch, make_user):
    c = await montar(db, monkeypatch)
    await threema_envios.semear(db, fatia="F1", chaves=MINHAS, aplicar=True)
    await db.commit()
    c.usuarios["heisenberg"].status = UserStatus.SUSPENDED
    await db.commit()
    await _tiktok_so_reembolso(db, monkeypatch, make_user)
    await _aberturas_presas(db, monkeypatch, make_user)
    assert [(e["chave"], e["destinos"]) for e in _Fake.envios] == [
        (K.CHAMADOS_TIKTOK_SO_REEMBOLSO, [ID["cairo_sa"]]),
        (K.CHAMADOS_ABERTURAS_PRESAS, [ID["cairo_sa"]]),
    ]
    # o legado (o .env) ainda mandaria para os dois
    leg = await threema_envios.legado(K.CHAMADOS_TIKTOK_SO_REEMBOLSO, session=db)
    assert leg.ids == (ID["heisenberg"], ID["cairo_sa"])
    # o grupo da semente é o mesmo para os dois envios (13 e 14)
    grupos = (
        await db.execute(
            select(ThreemaEnvioDestinatario.grupo_id).where(
                ThreemaEnvioDestinatario.envio_chave.in_(
                    [K.CHAMADOS_TIKTOK_SO_REEMBOLSO, K.CHAMADOS_ABERTURAS_PRESAS]
                )
            )
        )
    ).scalars()
    assert len(set(grupos)) == 1
