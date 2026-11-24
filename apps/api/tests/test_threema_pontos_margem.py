"""F3/C10b da tarefa 16: os avisos na hora do Robô da Margem — pedido segurado
ou reprovado (item 25), reavaliação dos reprovados (26) e margem fora do
normal (27) —, o Informar da Margem (28) e o resumo das pendências do Robô da
Margem na Ouvidoria (34) passam a ler o Cadastro (Cadastros › Envios
Threema). A lista "Avisar" do robô e a linha `margem` do Informar ficam
congeladas como reserva de emergência e o PUT delas responde 410 — "só ADM
altera quem recebe" (decisão do dono, 23/11): o gerente (o e-mail liberado
no botão da Margem) perde a edição, continua vendo quem recebe (só nomes) e
com o "Enviar agora". O liga/desliga do 27 é a caixinha "avisar margem alta"
do robô, gravada direto nela pela tela do Cadastro (interruptor D).

O modo do robô continua valendo ANTES (o "depende de"): silencioso age e cala
— nem sino, nem registro.

Um teste por ponto, PELO CAMINHO REAL (a rodada do robô `run`, a revisão
`reavaliar_reprovados`, o `avisar_pendentes` do robô e a rota do botão; só o
ThreemaClient e o Bling são falsos):

* (a) Cadastro semeado com a lista antiga VAZIA: manda para quem está no
  Cadastro, com a chave do envio e o remetente de hoje (`margem`);
* (b) desligado: não manda; o hold/a liberação seguem e o aviso se perde
  (25 e 26, `descarta`); a margem alta não grava a auditoria e, ao ligar a
  caixinha pelo Cadastro, avisa o que estiver alto (27, `acumula`); o resumo
  do robô não carimba e sai inteiro ao religar (34, `acumula`); o botão
  responde 422 (28, `recusa`);
* (c) ninguém válido (a única pessoa da lista foi suspensa): não manda,
  acende o sino dos admins e grava o registro `ok=false`; a margem alta não
  grava a auditoria e registra uma vez por rodada (não uma por pedido).

E a semente da fatia: `semear` + `conferir` das chaves dá 0 diferenças e,
depois dela, cada ponto manda EXATAMENTE para quem o legado mandaria. A lista
do robô vazia é o desligado de hoje (calado): 25, 26 e 34 nascem desligados e
o 27 nasce sem linha nenhuma — calado mesmo com a caixinha marcada.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaInformarConfig,
    UserRole,
    UserStatus,
)
from app.services import margem_auto_hold, ouvidoria, threema, threema_envios, threema_registro
from app.services.threema_catalogo import CATALOGO, K
from tests.test_margem_auto_hold import HOJE, FakeBling, _audits, _seed_pedido, _snapshot
from tests.test_margem_reavaliar_reprovados import AGORA, FakeBlingComSituacao, _seed_reprovado
from tests.test_threema_pontos_logistica_devolucoes import _Fake, _ligar, _registro, _sinos
from tests.test_threema_resolver import _usuario, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("limpar_ouvidoria")]

GERENTE = "sa.geral@tutamail.com"
ROBO = "vigia_margem"
HOJE_REAVALIACAO = date(2026, 9, 16)

# As chaves da C10b. Remetente de HOJE: o `contexto="margem"` dos três avisos
# do `margem_auto_hold`, o contexto do Informar e o `contexto` do RoboDef.
MINHAS = [
    K.MARGEM_PEDIDO_SEGURADO,
    K.MARGEM_REAVALIACAO,
    K.MARGEM_MARGEM_ALTA,
    K.MARGEM_INFORMAR,
    K.OUVIDORIA_VIGIA_MARGEM,
]
# Os que leem a lista do robô (a "Avisar"): a lista vazia é o desligado de hoje.
DO_ROBO = [
    K.MARGEM_PEDIDO_SEGURADO,
    K.MARGEM_REAVALIACAO,
    K.MARGEM_MARGEM_ALTA,
    K.OUVIDORIA_VIGIA_MARGEM,
]
AO_DESLIGAR = {
    K.MARGEM_PEDIDO_SEGURADO: "descarta",
    K.MARGEM_REAVALIACAO: "descarta",
    K.MARGEM_MARGEM_ALTA: "acumula",
    K.MARGEM_INFORMAR: "recusa",
    K.OUVIDORIA_VIGIA_MARGEM: "acumula",
}


class _FakeCanal(_Fake):
    """O ThreemaClient falso da bateria, com o canal que a tela do Cadastro
    lê (`threema_envios.canal`) ao responder a edição."""

    disabled = False
    canal = None


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _Fake.envios = []
    threema_envios.limpar_cache()
    threema_registro.reiniciar_disjuntor()
    s = get_settings()
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    monkeypatch.setattr(s, "threema_registro_ativo", True)
    # a configuração antiga sem reserva: o OUVIDORIA geral vazio
    monkeypatch.setattr(s, "ouvidoria_threema_recipients", "")
    monkeypatch.setattr(threema, "ThreemaClient", _FakeCanal)

    async def _sem_refetch(session, bling_id: int) -> None:
        """A reavaliação rebusca o financeiro (apagaria a linha semeada do
        snapshot): aqui o snapshot semeado JÁ É o dado atualizado."""

    monkeypatch.setattr(margem_auto_hold, "_atualizar_financeiro", _sem_refetch)
    yield
    threema_envios.limpar_cache()


async def _pessoas(db):
    admin = await _usuario(db, "admin c10b", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c10b", "TSTJ0006")
    sumido = await _usuario(db, "sumido c10b", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, sumido


async def _robo(
    db, *, modo: str = "ligado", lista: str | None = None, margem_alta: bool = False
) -> OuvidoriaRobo:
    await ouvidoria.sincronizar_catalogo(db)
    row = await db.get(OuvidoriaRobo, ROBO, populate_existing=True)
    row.modo = modo
    row.threema_recipients = lista
    row.config = {**(row.config or {}), "avisar_margem_alta": margem_alta}
    await db.commit()
    return row


async def _caixinha(db) -> bool:
    row = await db.get(OuvidoriaRobo, ROBO, populate_existing=True)
    return bool(ouvidoria.config_do_robo(row, ROBO).get("avisar_margem_alta", False))


async def _linha_informar(db, contexto: str, recipients: str) -> None:
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


def _envios() -> list[tuple]:
    return [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios]


# ─────────────────────────────────────── cada ponto pelo caminho real


async def _hold(db, pedido: str) -> dict:
    """Item 25: a rodada do robô reprova um pedido com margem abaixo da
    mínima (o aviso traz o link de aprovar pelo celular)."""
    await _seed_pedido(db, pedido=pedido, bling_id=int(pedido))
    return await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE)


async def _reavaliacao(db, pedido: str) -> dict:
    """Item 26: a revisão libera um reprovado pelo robô cuja margem passou."""
    await _seed_reprovado(db, pedido=pedido, bling_id=int(pedido), margem=0.20)
    return await margem_auto_hold.reavaliar_reprovados(
        db, client=FakeBlingComSituacao(), hoje=HOJE_REAVALIACAO, agora=AGORA
    )


async def _alta(db, *pedidos: str) -> dict:
    """Item 27: pedidos Em aberto com margem acima de 60% na rodada."""
    for p in pedidos:
        await _seed_pedido(db, pedido=p, bling_id=int(p), margem_baixa=False, margem=0.75)
    return await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE)


async def _pendencia(db, chave: str = "x:1") -> OuvidoriaOcorrencia:
    o = await ouvidoria.registrar(db, ROBO, chave, titulo=f"Problema {chave}", pedido=chave[2:])
    await db.commit()
    return o


async def _resumo(db, chave: str = "x:1") -> dict:
    """Item 34: o aviso das pendências do Robô da Margem na Ouvidoria."""
    await _pendencia(db, chave)
    r = await ouvidoria.avisar_pendentes(db, ROBO)
    await db.commit()
    return r


async def _ocorrencias(db) -> list[OuvidoriaOcorrencia]:
    await db.commit()
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(OuvidoriaOcorrencia.robo_chave == ROBO)
                .order_by(OuvidoriaOcorrencia.chave)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _botao(client):
    """Item 28: o "Enviar agora" do Informar da Margem (só pela API hoje)."""
    return await client.post("/api/informar/margem/enviar")


def test_as_chaves_da_c10b_estao_migradas_com_o_remetente_de_hoje():
    for chave in MINHAS:
        d = CATALOGO[chave]
        assert d.migrado and d.fatia == "F3", chave
        assert d.remetente == "margem", chave
        assert d.ao_desligar == AO_DESLIGAR[chave], chave
        # a lista do robô vazia é o desligado de hoje; o botão vazio é o 422
        assert threema_envios._lista_vazia_desliga(d) == (chave in DO_ROBO), chave
        if chave in DO_ROBO:
            assert d.legado.robo == ROBO, chave
            assert [x.alvo for x in d.depende_de] == [ROBO], chave
    assert ouvidoria.ROBOS[ROBO].contexto == "margem"
    # 27: o liga/desliga é a caixinha do robô, gravada direto nela (D)
    alta = CATALOGO[K.MARGEM_MARGEM_ALTA]
    assert (alta.interruptor, alta.interruptor_alvo) == (
        "robo_config",
        "vigia_margem.avisar_margem_alta",
    )
    # quem vê quem recebe: a porta do botão da Margem (admin e o gerente) e,
    # no 34, quem vê a Ouvidoria
    for chave in (K.MARGEM_PEDIDO_SEGURADO, K.MARGEM_REAVALIACAO, K.MARGEM_MARGEM_ALTA):
        assert CATALOGO[chave].leitura == "informar:margem_auto", chave
    assert CATALOGO[K.MARGEM_INFORMAR].leitura == "informar:margem"
    assert CATALOGO[K.OUVIDORIA_VIGIA_MARGEM].leitura == "perm:ouvidoria:view"


# ─────────────────────────────────────────────── (a) pelo Cadastro


async def test_hold_e_reavaliacao_mandam_pelo_cadastro_com_a_lista_antiga_vazia(db):
    _admin, joana, sumido = await _pessoas(db)
    await _robo(db, lista=None)
    # a lista do Cadastro tem quem foi suspenso também: ele fica de fora
    await semeado(db, K.MARGEM_PEDIDO_SEGURADO, joana, sumido)
    await semeado(db, K.MARGEM_REAVALIACAO, joana)
    res = await _hold(db, "291670")
    assert res["reprovados"] == 1
    assert _envios() == [(K.MARGEM_PEDIDO_SEGURADO, "cadastro", "margem", ["TSTJ0006"])]
    linhas = _Fake.envios[0]["texto"].splitlines()
    assert linhas[0] == "DaVinci — Margem: pedido reprovado automaticamente"
    assert linhas[-1].startswith("Aprovar pelo celular: ")
    _Fake.envios = []
    res = await _reavaliacao(db, "297401")
    assert res["liberados"] == 1
    assert _envios() == [(K.MARGEM_REAVALIACAO, "cadastro", "margem", ["TSTJ0006"])]
    assert _Fake.envios[0]["texto"].startswith("DaVinci — Margem: pedido liberado automaticamente")


async def test_margem_alta_manda_pelo_cadastro_e_grava_a_auditoria_uma_vez(db):
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=None, margem_alta=True)
    await semeado(db, K.MARGEM_MARGEM_ALTA, joana)
    res = await _alta(db, "501")
    assert res["alertas"] == 1
    assert _envios() == [(K.MARGEM_MARGEM_ALTA, "cadastro", "margem", ["TSTJ0006"])]
    assert [a["acao"] for a in await _audits(db, "501")] == ["alerta_margem_alta"]
    # o dedup é a auditoria: o tick seguinte não repete
    assert (await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE))["alertas"] == 0
    assert len(_Fake.envios) == 1
    # o pedido fica intocado
    assert (await _snapshot(db, "501"))["situacao"] == "6"


async def test_resumo_do_robo_manda_pelo_cadastro_e_carimba(db):
    _admin, joana, sumido = await _pessoas(db)
    await _robo(db, lista=None)
    await semeado(db, K.OUVIDORIA_VIGIA_MARGEM, joana, sumido)
    assert await _resumo(db) == {"avisadas": 1}
    assert _envios() == [(K.OUVIDORIA_VIGIA_MARGEM, "cadastro", "margem", ["TSTJ0006"])]
    (o,) = await _ocorrencias(db)
    assert o.avisada_em is not None


async def test_informar_da_margem_manda_pelo_cadastro(db, client, auth_as):
    admin, joana, _ = await _pessoas(db)
    await _linha_informar(db, "margem", "")
    await semeado(db, K.MARGEM_INFORMAR, joana)
    auth_as(admin)
    r = await _botao(client)
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == ["TSTJ0006"]
    assert _envios() == [(K.MARGEM_INFORMAR, "cadastro", "margem", ["TSTJ0006"])]
    assert _Fake.envios[0]["texto"].startswith("DaVinci — Margem: nenhum pedido pendente")


async def test_a_lista_antiga_nao_vale_mais_depois_de_migrar(db, client, auth_as):
    """A lista "Avisar" do robô e a linha `margem` do Informar ficam
    congeladas: com o Cadastro semeado, quem está só nelas não recebe."""
    admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=f"{ID['harry']}, {ID['roma']}", margem_alta=True)
    await _linha_informar(db, "margem", ID["harry"])
    for chave in MINHAS:
        await semeado(db, chave, joana)
    await _hold(db, "291670")
    await _reavaliacao(db, "297401")
    await _alta(db, "501")
    await _resumo(db)
    auth_as(admin)
    assert (await _botao(client)).status_code == 200
    assert {e[0] for e in _envios()} == set(MINHAS)
    assert {(tuple(e[3]), e[1]) for e in _envios()} == {(("TSTJ0006",), "cadastro")}


async def test_sem_semente_segue_a_lista_antiga_exata(db, client, auth_as):
    """Publicado antes de semear: a chave migrada sem semente usa o legado
    exato (a lista do robô e a linha do Informar)."""
    admin, _joana, _ = await _pessoas(db)
    await _robo(db, lista=ID["harry"], margem_alta=True)
    await _linha_informar(db, "margem", ID["roma"])
    await _hold(db, "291670")
    await _alta(db, "501")
    await _resumo(db)
    auth_as(admin)
    assert (await _botao(client)).status_code == 200
    assert _envios() == [
        (K.MARGEM_PEDIDO_SEGURADO, "legado", "margem", [ID["harry"]]),
        (K.MARGEM_MARGEM_ALTA, "legado", "margem", [ID["harry"]]),
        (K.OUVIDORIA_VIGIA_MARGEM, "legado", "margem", [ID["harry"]]),
        (K.MARGEM_INFORMAR, "legado", "margem", [ID["roma"]]),
    ]


# ─────────────────────────────────────────────── (b) desligado


async def test_hold_e_reavaliacao_desligados_seguem_sem_aviso(db):
    """25 e 26 desligados no Cadastro: o robô reprova e libera do mesmo
    jeito; o aviso daquele pedido se perde (o `descarta`), sem registro nem
    sino. A lista antiga tem gente: desligado não cai nela."""
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=ID["harry"])
    await semeado(db, K.MARGEM_PEDIDO_SEGURADO, joana, ativo=False)
    await semeado(db, K.MARGEM_REAVALIACAO, joana, ativo=False)
    assert (await _hold(db, "291670"))["reprovados"] == 1
    assert (await _snapshot(db, "291670"))["situacao"] == "83955"
    assert (await _reavaliacao(db, "297401"))["liberados"] == 1
    assert _Fake.envios == []
    for chave in (K.MARGEM_PEDIDO_SEGURADO, K.MARGEM_REAVALIACAO):
        assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    # religado: o pedido daquela rodada não volta (já foi tratado)
    await _ligar(db, K.MARGEM_PEDIDO_SEGURADO, None)
    threema_envios.limpar_cache()
    await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE)
    assert _Fake.envios == []
    await _hold(db, "291671")
    assert _envios() == [(K.MARGEM_PEDIDO_SEGURADO, "cadastro", "margem", ["TSTJ0006"])]


async def test_margem_alta_desligada_acumula_e_o_cadastro_liga_a_caixinha(
    db, client, auth_as, make_user
):
    """27: o liga/desliga é a caixinha "avisar margem alta" do robô (D).
    Desligada, nada sai e nada vai para a auditoria; o admin liga pelo
    Cadastro (grava direto na caixinha) e a rodada seguinte avisa o que
    estiver alto naquela hora (o `acumula`)."""
    admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=ID["harry"], margem_alta=False)
    await semeado(db, K.MARGEM_MARGEM_ALTA, joana)
    res = await _alta(db, "501", "502")
    assert res["alertas"] == 0 and _Fake.envios == []
    assert await _audits(db, "501") == [] and await _audits(db, "502") == []
    assert await _registro(db, K.MARGEM_MARGEM_ALTA) == []
    # o gerente não liga (o Cadastro é só de admin)
    auth_as(await make_user(email=GERENTE, role=UserRole.USER))
    r = await client.patch(f"/api/threema/envios/{K.MARGEM_MARGEM_ALTA}", json={"ativo": True})
    assert r.status_code == 403
    auth_as(admin)
    r = await client.patch(f"/api/threema/envios/{K.MARGEM_MARGEM_ALTA}", json={"ativo": True})
    assert r.status_code == 200, r.text
    assert await _caixinha(db) is True
    threema_envios.limpar_cache()
    res = await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE)
    assert res["alertas"] == 2
    assert _envios() == [(K.MARGEM_MARGEM_ALTA, "cadastro", "margem", ["TSTJ0006"])] * 2
    # desligar pelo Cadastro desmarca a caixinha
    r = await client.patch(f"/api/threema/envios/{K.MARGEM_MARGEM_ALTA}", json={"ativo": False})
    assert r.status_code == 200, r.text
    assert await _caixinha(db) is False


async def test_resumo_desligado_nao_carimba_e_ao_religar_sai_tudo(db):
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=ID["harry"])
    await semeado(db, K.OUVIDORIA_VIGIA_MARGEM, joana, ativo=False)
    assert await _resumo(db, "x:1") == {"avisadas": 0, "motivo": "envio_desligado"}
    assert await _resumo(db, "x:2") == {"avisadas": 0, "motivo": "envio_desligado"}
    assert _Fake.envios == []
    assert all(o.avisada_em is None for o in await _ocorrencias(db))
    await _ligar(db, K.OUVIDORIA_VIGIA_MARGEM, None)
    threema_envios.limpar_cache()
    assert await ouvidoria.avisar_pendentes(db, ROBO) == {"avisadas": 2}
    assert _envios() == [(K.OUVIDORIA_VIGIA_MARGEM, "cadastro", "margem", ["TSTJ0006"])]
    assert "2 ocorrências" in _Fake.envios[0]["texto"]


async def test_botao_desligado_responde_422(db, client, auth_as):
    admin, joana, _ = await _pessoas(db)
    await _linha_informar(db, "margem", ID["harry"])
    await semeado(db, K.MARGEM_INFORMAR, joana, ativo=False)
    auth_as(admin)
    r = await _botao(client)
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "envio_desligado")
    assert _Fake.envios == [] and await _registro(db, K.MARGEM_INFORMAR) == []


# ─────────────────────────────────────────────── (c) ninguém válido


async def test_ninguem_valido_no_hold_e_na_reavaliacao_registra_e_acende_o_sino(db):
    admin, joana, sumido = await _pessoas(db)
    # a lista antiga tem gente: o Cadastro sem ninguém válido NUNCA cai nela
    await _robo(db, lista="TSTZ0009")
    await semeado(db, K.MARGEM_PEDIDO_SEGURADO, sumido)
    await semeado(db, K.MARGEM_REAVALIACAO, sumido)
    assert (await _hold(db, "291670"))["reprovados"] == 1
    assert (await _hold(db, "291671"))["reprovados"] == 1
    assert (await _reavaliacao(db, "297401"))["liberados"] == 1
    assert _Fake.envios == []
    for chave, avisos in ((K.MARGEM_PEDIDO_SEGURADO, 2), (K.MARGEM_REAVALIACAO, 1)):
        linhas = await _registro(db, chave)
        # um registro por aviso perdido; o sino do dia é um só
        assert [(x.ok, x.erro, x.via, x.contexto) for x in linhas] == [
            (False, "sem_destinatario_valido", "cadastro", "margem")
        ] * avisos, chave
        assert [a.user_id for a in await _sinos(db, chave)] == [admin.id], chave
    # o hold segue: o pedido foi reprovado no Bling
    assert (await _snapshot(db, "291670"))["situacao"] == "83955"
    # acertada a lista, o aviso do pedido seguinte sai
    await semeado(db, K.MARGEM_PEDIDO_SEGURADO, joana)
    threema_envios.limpar_cache()
    await _hold(db, "291672")
    assert _envios() == [(K.MARGEM_PEDIDO_SEGURADO, "cadastro", "margem", ["TSTJ0006"])]


async def test_margem_alta_sem_ninguem_valido_nao_grava_a_auditoria(db):
    """Ninguém válido: um registro por RODADA (não um por pedido), sino, e
    nenhuma auditoria — quando alguém for posto na lista, os pedidos ainda
    Em aberto saem no tick seguinte."""
    admin, joana, sumido = await _pessoas(db)
    await _robo(db, lista=ID["harry"], margem_alta=True)
    await semeado(db, K.MARGEM_MARGEM_ALTA, sumido)
    res = await _alta(db, "501", "502")
    assert res["alertas"] == 0 and _Fake.envios == []
    assert await _audits(db, "501") == [] and await _audits(db, "502") == []
    (linha,) = await _registro(db, K.MARGEM_MARGEM_ALTA)
    assert (linha.ok, linha.erro, linha.via) == (False, "sem_destinatario_valido", "cadastro")
    assert [a.user_id for a in await _sinos(db, K.MARGEM_MARGEM_ALTA)] == [admin.id]
    await semeado(db, K.MARGEM_MARGEM_ALTA, joana)
    threema_envios.limpar_cache()
    res = await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE)
    assert res["alertas"] == 2
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]] * 2


async def test_resumo_sem_ninguem_valido_registra_e_nao_carimba(db):
    admin, _joana, sumido = await _pessoas(db)
    await _robo(db, lista="TSTZ0009")
    await semeado(db, K.OUVIDORIA_VIGIA_MARGEM, sumido)
    assert await _resumo(db) == {"avisadas": 0, "motivo": "ninguem_valido"}
    assert _Fake.envios == []
    (linha,) = await _registro(db, K.OUVIDORIA_VIGIA_MARGEM)
    assert (linha.ok, linha.erro, linha.contexto) == (False, "sem_destinatario_valido", "margem")
    assert [a.user_id for a in await _sinos(db, K.OUVIDORIA_VIGIA_MARGEM)] == [admin.id]
    (o,) = await _ocorrencias(db)
    assert o.avisada_em is None


async def test_botao_sem_ninguem_valido_registra_e_responde_422(db, client, auth_as):
    admin, _joana, sumido = await _pessoas(db)
    await _linha_informar(db, "margem", ID["harry"])
    await semeado(db, K.MARGEM_INFORMAR, sumido)
    auth_as(admin)
    r = await _botao(client)
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "sem_destinatarios")
    assert _Fake.envios == []
    (linha,) = await _registro(db, K.MARGEM_INFORMAR)
    assert (linha.ok, linha.erro) == (False, "sem_destinatario_valido")
    assert [a.user_id for a in await _sinos(db, K.MARGEM_INFORMAR)] == [admin.id]


# ─────────────────────────────────── depende de: o modo do robô


@pytest.mark.parametrize("modo", ["silencioso", "desligado"])
async def test_robo_fora_do_ligado_nao_avisa_nem_acende_o_sino(db, modo):
    """O modo do robô é lido ANTES do Cadastro: silencioso age e cala;
    desligado nem roda. Nem com ninguém válido no Cadastro há sino ou
    registro."""
    _admin, _joana, sumido = await _pessoas(db)
    await _robo(db, modo=modo, lista=ID["harry"], margem_alta=True)
    for chave in DO_ROBO:
        await semeado(db, chave, sumido)
    res = await _hold(db, "291670")
    if modo == "desligado":
        assert res["skipped"] == "desligado"
    else:
        assert res["reprovados"] == 1  # silencioso age igual
    await _reavaliacao(db, "297401")
    await _alta(db, "501")
    r = await _resumo(db)
    assert r == {"avisadas": 0, "motivo": f"modo_{modo}"}
    assert _Fake.envios == []
    for chave in DO_ROBO:
        assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    assert await _audits(db, "501") == []


async def test_configuracao_antiga_vazia_fica_calada_como_hoje(db, monkeypatch, client, auth_as):
    """Emergência (o piso do .env) com a lista antiga vazia: nada sai e nada
    vai para o registro nem para o sino; o botão responde 422 sem montar
    nada. Com gente na lista antiga, manda para ela (via legado)."""
    admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=None, margem_alta=True)
    await _linha_informar(db, "margem", "")
    for chave in MINHAS:
        await semeado(db, chave, joana)
    monkeypatch.setattr(get_settings(), "threema_cadastro_ativo", False)
    await _hold(db, "291670")
    await _reavaliacao(db, "297401")
    assert (await _alta(db, "501"))["alertas"] == 0
    assert await _resumo(db) == {"avisadas": 0, "motivo": "sem_destinatarios"}
    auth_as(admin)
    r = await _botao(client)
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "sem_destinatarios")
    assert _Fake.envios == []
    for chave in MINHAS:
        assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    assert await _audits(db, "501") == []
    await _robo(db, lista=ID["harry"], margem_alta=True)
    await _hold(db, "291671")
    # o 501 ainda Em aberto e sem auditoria sai na mesma rodada (o `acumula`)
    assert _envios() == [
        (K.MARGEM_PEDIDO_SEGURADO, "legado", "margem", [ID["harry"]]),
        (K.MARGEM_MARGEM_ALTA, "legado", "margem", [ID["harry"]]),
    ]


# ─────────────────────────────────────────────── as rotas


async def test_put_das_listas_da_margem_responde_410_e_o_get_mostra_a_antiga(
    db, client, auth_as, make_user
):
    """`margem` (o Informar, 28) e `margem_auto` (a lista "Avisar" do robô,
    25 a 27 e 34) ficam congeladas: mudar responde 410, também para o
    gerente; o GET mostra a lista antiga; a aba velha que só repete a lista
    (o modal antigo salvava a UNIÃO das duas antes de fechar) recebe 200 sem
    gravar."""
    admin = await make_user(role=UserRole.ADMIN)
    gerente = await make_user(email=GERENTE, role=UserRole.USER)
    await _robo(db, lista=ID["londres"])
    await _linha_informar(db, "margem", ID["harry"])
    for quem in (admin, gerente):
        auth_as(quem)
        for contexto in ("margem", "margem_auto"):
            r = await client.put(f"/api/informar/{contexto}", json={"recipients": [ID["joana"]]})
            assert r.status_code == 410, (contexto, r.text)
            detalhe = r.json()["detail"]
            assert detalhe["code"] == "quem_recebe_no_cadastro"
            esperado = [K.MARGEM_INFORMAR] if contexto == "margem" else DO_ROBO
            assert sorted(detalhe["envios"]) == sorted(esperado), contexto
        # a aba velha: a mesma lista (ou a união das duas) passa sem gravar
        uniao = [ID["harry"], ID["londres"]]
        for contexto in ("margem", "margem_auto"):
            r = await client.put(f"/api/informar/{contexto}", json={"recipients": uniao})
            assert r.status_code == 200, (contexto, r.text)
    r = await client.get("/api/informar/margem_auto")
    assert r.status_code == 200 and r.json()["recipients"] == [ID["londres"]]
    r = await client.get("/api/informar/margem")
    assert r.status_code == 200 and r.json()["recipients"] == [ID["harry"]]
    row = await db.get(OuvidoriaRobo, ROBO, populate_existing=True)
    assert row.threema_recipients == ID["londres"]


async def test_gerente_perde_a_edicao_mas_segue_enviando_e_vendo_quem_recebe(
    db, client, auth_as, make_user
):
    """O gerente (e-mail liberado no botão da Margem) não grava mais quem
    recebe (nem no Informar, nem no Cadastro, nem pela tela dos robôs), mas o
    "Enviar agora" e o "quem recebe" (só nomes) continuam para ele. Quem não
    pode usar o botão hoje continua sem poder."""
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=ID["harry"])
    for chave in MINHAS:
        await semeado(db, chave, joana)
    gerente = await make_user(
        email=GERENTE,
        role=UserRole.USER,
        permissions={"ouvidoria": {"view": True, "edit": True, "delete": False}},
    )
    auth_as(gerente)
    r = await client.patch(
        f"/api/threema/envios/{K.MARGEM_PEDIDO_SEGURADO}",
        json={"destinatarios": [{"tipo": "usuario", "id": str(joana.id)}]},
    )
    assert r.status_code == 403, r.text
    # a tela dos robôs: a lista, o re-aviso e a caixinha são ignorados, com aviso
    r = await client.patch(
        f"/api/ouvidoria/robos/{ROBO}",
        json={
            "threema_recipients": ID["londres"],
            "reaviso_horas": 2,
            "config": {"avisar_margem_alta": True},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["avisos"]) == 3
    assert body["config_no_cadastro"] == {"avisar_margem_alta": K.MARGEM_MARGEM_ALTA}
    assert body["threema_migrado"] is True and body["threema_via"] == "cadastro"
    assert body["threema_destinatarios"] == [{"id": "TS…", "nome": "joana c10b", "origem": None}]
    row = await db.get(OuvidoriaRobo, ROBO, populate_existing=True)
    assert row.threema_recipients == ID["harry"]
    assert await _caixinha(db) is False
    # o "Enviar agora" segue, pelo Cadastro
    r = await _botao(client)
    assert r.status_code == 200, r.text
    assert _envios() == [(K.MARGEM_INFORMAR, "cadastro", "margem", ["TSTJ0006"])]
    # quem recebe: só nomes, sem ID
    for chave in MINHAS:
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 200, (chave, r.text)
        q = r.json()
        assert q["usa_configuracao_antiga"] is False, chave
        assert "TSTJ0006" not in r.text, chave
        if chave == K.MARGEM_MARGEM_ALTA:
            # a caixinha desmarcada: desligado, dizendo quem desliga e quem receberia
            assert q["ativo"] is False and "caixinha" in q["desligado"]
            assert q["receberia"] == ["joana c10b"]
        else:
            assert q["nomes"] == ["joana c10b"], chave
    # quem não pode usar o botão da Margem continua sem poder
    auth_as(await make_user(role=UserRole.USER, permissions={"logistica": {"edit": True}}))
    assert (await _botao(client)).status_code == 403
    for chave in (K.MARGEM_PEDIDO_SEGURADO, K.MARGEM_INFORMAR):
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 403, chave
    for contexto in ("margem", "margem_auto"):
        r = await client.put(f"/api/informar/{contexto}", json={"recipients": []})
        assert r.status_code == 403, contexto


# ─────────────────────────────────────────────── a semente da fatia


@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_ponto_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, client, auth_as, make_user, variante
):
    await montar(db, monkeypatch, variante=variante)
    esperado = {
        c: list(threema_envios._dedupe((await threema_envios.legado(c, session=db)).ids))
        for c in MINHAS
    }
    await threema_envios.semear(db, fatia="F3", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in MINHAS]
    # as listas antigas ficam congeladas como reserva, mas os pontos não leem
    # mais: esvaziar não muda quem recebe (o modo do robô segue ligado — é o
    # "depende de" — e a caixinha da margem alta, marcada para o teste)
    monkeypatch.setattr(get_settings(), "ouvidoria_threema_recipients", "")
    await _robo(db, lista=None, margem_alta=True)
    await _linha_informar(db, "margem", "")
    threema_envios.limpar_cache()
    linhas = {c: await db.get(ThreemaEnvio, c, populate_existing=True) for c in MINHAS}
    _Fake.envios = []
    await _hold(db, "291670")
    await _reavaliacao(db, "297401")
    await _alta(db, "501")
    await _resumo(db)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await _botao(client)
    saiu = {e[0]: e for e in _envios()}
    for c in MINHAS:
        if esperado[c]:
            assert saiu[c] == (c, "cadastro", "margem", esperado[c]), c
            assert linhas[c].ativo is None, c
            continue
        assert c not in saiu, c
        if c == K.MARGEM_INFORMAR:
            # o botão vazio fica ligado sem ninguém, como os outros botões:
            # quem aperta recebe o 422 (e o registro/sino do "ninguém")
            assert (r.status_code, r.json()["detail"]["code"]) == (422, "sem_destinatarios")
            assert linhas[c].ativo is None
            continue
        if c != K.MARGEM_MARGEM_ALTA:
            # lista do robô vazia = o desligado de hoje: nasce desligado
            assert linhas[c].ativo is False, c
        # e calado, como hoje: nada de registro nem sino a cada rodada
        assert await _registro(db, c) == [] and await _sinos(db, c) == [], c
    if not esperado[K.MARGEM_MARGEM_ALTA]:
        # o 27 vazio nasce sem linha nenhuma: calado mesmo com a caixinha
        # marcada (a caixinha é o liga/desliga dele, não o `ativo`)
        assert linhas[K.MARGEM_MARGEM_ALTA].ativo is None
        assert await _caixinha(db) is True
        assert await _audits(db, "501") == []
        q = await threema_envios.resolver(K.MARGEM_MARGEM_ALTA)
        assert (q.ativo, q.motivo) == (False, threema_envios.DESLIGADO)
    if variante == "cheio":
        # a lista do robô de hoje: londres, harry potter e cairo sa (o ativo,
        # não o "cairo" suspenso de mesmo ID); o Informar: harry e joana
        assert esperado[K.MARGEM_PEDIDO_SEGURADO] == [ID["londres"], ID["harry"], ID["cairo_sa"]]
        assert esperado[K.MARGEM_INFORMAR] == [ID["harry"], ID["joana"]]
    if variante == "vazias":
        assert not any(esperado.values())


async def test_semente_vazia_com_a_caixinha_marcada_fica_calada_e_o_conferir_acompanha(db):
    """A lista do robô vazia com a caixinha "avisar margem alta" marcada: hoje
    o alerta fica calado (`if not recipients`). A semente não põe ninguém no
    27 (nem o grupo), o Cadastro o lê desligado — sem registro nem sino a
    cada rodada — e o `conferir` dá igual; pôr alguém na lista liga de novo
    (a caixinha continua marcada)."""
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=None, margem_alta=True)
    await threema_envios.semear(db, fatia="F3", chaves=DO_ROBO, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=DO_ROBO)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in DO_ROBO]
    linhas = (
        (
            await db.execute(
                select(ThreemaEnvioDestinatario).where(
                    ThreemaEnvioDestinatario.envio_chave.in_(DO_ROBO)
                )
            )
        )
        .scalars()
        .all()
    )
    assert linhas == []
    assert (await _alta(db, "501"))["alertas"] == 0
    assert _Fake.envios == []
    assert await _registro(db, K.MARGEM_MARGEM_ALTA) == []
    assert await _sinos(db, K.MARGEM_MARGEM_ALTA) == []
    await semeado(db, K.MARGEM_MARGEM_ALTA, joana)
    threema_envios.limpar_cache()
    res = await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE)
    assert res["alertas"] == 1
    assert _envios() == [(K.MARGEM_MARGEM_ALTA, "cadastro", "margem", ["TSTJ0006"])]


async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(db, monkeypatch):
    """O "cairo sa" suspenso depois da semente: sai de quem recebe os avisos
    da Margem, embora a lista antiga (a reserva) ainda o tenha."""
    c = await montar(db, monkeypatch)
    await threema_envios.semear(db, fatia="F3", chaves=MINHAS, aplicar=True)
    await db.commit()
    c.usuarios["cairo_sa"].status = UserStatus.SUSPENDED
    await db.commit()
    threema_envios.limpar_cache()
    await _hold(db, "291670")
    assert _envios() == [
        (K.MARGEM_PEDIDO_SEGURADO, "cadastro", "margem", [ID["londres"], ID["harry"]])
    ]
    leg = await threema_envios.legado(K.MARGEM_PEDIDO_SEGURADO, session=db)
    assert ID["cairo_sa"] in leg.ids


async def test_tela_dos_robos_mostra_o_cadastro_e_a_caixinha_so_leitura(
    db, client, auth_as, make_user
):
    """Ouvidoria › Robôs: o Robô da Margem mostra quem recebe pelo Cadastro
    (só nomes) e a caixinha "avisar margem alta" vem em `config_no_cadastro`
    (só leitura, com o link); a lixeira, o modo e o resto da config seguem."""
    admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=ID["harry"])
    await semeado(db, K.OUVIDORIA_VIGIA_MARGEM, joana)
    auth_as(admin)
    r = await client.get(f"/api/ouvidoria/robos/{ROBO}")
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["threema_envio"], b["threema_migrado"], b["threema_origem"]) == (
        K.OUVIDORIA_VIGIA_MARGEM,
        True,
        "cadastro",
    )
    assert b["config_no_cadastro"] == {"avisar_margem_alta": K.MARGEM_MARGEM_ALTA}
    assert [d["nome"] for d in b["threema_destinatarios"]] == ["joana c10b"]
    # o admin também muda a caixinha só pelo Cadastro: pela tela dos robôs,
    # ignorada com aviso; o modo segue gravando
    r = await client.patch(
        f"/api/ouvidoria/robos/{ROBO}",
        json={"modo": "silencioso", "config": {"avisar_margem_alta": True}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["modo"] == "silencioso" and len(r.json()["avisos"]) == 1
    assert await _caixinha(db) is False


async def test_atalho_diz_por_que_a_margem_alta_esta_desligada(db):
    """O quem-recebe leve do 27: com a caixinha desmarcada, o desligado é a
    caixinha; com ela marcada e sem ninguém na lista, é a lista vazia."""
    from app.services import threema_envios_tela as tela

    _admin, joana, _ = await _pessoas(db)
    await _robo(db, lista=None, margem_alta=False)
    await semeado(db, K.MARGEM_MARGEM_ALTA, joana)
    q = await tela.quem_recebe_leve(db, K.MARGEM_MARGEM_ALTA)
    assert q["ativo"] is False and "caixinha" in q["desligado"]
    assert "desmarcada" in q["desligado"] and q["receberia"] == ["joana c10b"]
    await _robo(db, lista=None, margem_alta=True)
    await db.execute(
        ThreemaEnvioDestinatario.__table__.delete().where(
            ThreemaEnvioDestinatario.envio_chave == K.MARGEM_MARGEM_ALTA
        )
    )
    await db.commit()
    threema_envios.limpar_cache()
    q = await tela.quem_recebe_leve(db, K.MARGEM_MARGEM_ALTA)
    assert q["ativo"] is False and "lista" in q["desligado"]
    assert "desmarcada" not in q["desligado"]
    # e a lista da tela (Cadastros › Envios Threema) diz o mesmo na faixa
    lista = await tela.listar(db)
    (e,) = [x for x in lista["envios"] if x["chave"] == K.MARGEM_MARGEM_ALTA]
    assert any("lista" in f["texto"] and f["situacao"] == "desligado" for f in e["faixas"])
