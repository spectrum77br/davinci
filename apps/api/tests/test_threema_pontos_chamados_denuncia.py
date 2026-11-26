"""F2/C9d da tarefa 16: a IA de Chamado parada (item 15), o Encaminhar ao
jurídico (16), o relatório do dia da Denúncia (43) e o robô de Denúncia
precisando de alguém (44) passam a ler o Cadastro (Cadastros › Envios
Threema). Vão juntos por causa da reserva `chamados_ia`: o 44 lia a linha
`denuncia_robo` e, sem ela, a do 15 — na semente as duas viram o grupo "IA de
Chamado". As linhas do Informar (`chamados_ia`, `juridico`,
`denuncia_relatorio`, `denuncia_robo`) ficam congeladas como reserva de
emergência e o PUT delas responde 410 — "só ADM altera quem recebe" (decisão
do dono, 23/11): o Cairo, que escolhia nas três da IA e da Denúncia, perde a
edição e continua vendo quem recebe; o botão Encaminhar ao jurídico e o teste
do relatório continuam para quem pode usar hoje.

Um teste por ponto, PELO CAMINHO REAL (a rota da análise da IA, a rota do
jurídico, a rodada do relatório e a volta do robô; só o ThreemaClient é
falso):

* (a) Cadastro semeado com a linha do Informar VAZIA: manda para quem está no
  Cadastro, com a chave do envio e o remetente de hoje;
* (b) desligado: não manda — o 15 diz no histórico do chamado e não tenta de
  novo, o 16 recusa (422 `envio_desligado`) sem mexer no chamado, o 43 não
  carimba o dia mas só manda o de ontem, o 44 marca as ocorrências (o
  `descarta` do catálogo);
* (c) ninguém válido (a única pessoa da lista foi suspensa): não manda,
  acende o sino dos admins e grava o registro `ok=false`; o 43 não carimba e
  a rodada sai com falha; o 44 não gasta as marcas (no máximo uma volta por
  hora chega ao núcleo) e, acertada a lista, avisa;
* a configuração antiga vazia (emergência) fica calada, como hoje.

E a semente da fatia: `semear` + `conferir` das chaves dá 0 diferenças e,
depois dela, cada ponto manda EXATAMENTE para quem o legado mandaria. A linha
`denuncia_robo` salva vazia é o desligado de hoje do 44: a semente o desliga
sem pô-lo no grupo do 15.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.config import get_settings
from app.models import (
    Chamado,
    DenunciaRelatorio,
    DenunciaRoboStatus,
    OuvidoriaRobo,
    OuvidoriaRodada,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaGrupo,
    UserRole,
    UserStatus,
)
from app.routers import informar as informar_router
from app.services import denuncia_relatorio_threema as rt
from app.services import (
    denuncia_robo_aviso,
    ouvidoria,
    threema,
    threema_envios,
    threema_registro,
)
from app.services.threema_catalogo import CATALOGO, G_IA_CHAMADO, K
from tests.test_chamados_ia_aviso import IA, _decisao, _sistema, cid  # noqa: F401 — fixture
from tests.test_chamados_juridico import _criar_chamado, _perms
from tests.test_denuncia_relatorio_threema import AS_7H07, ONTEM, _dia
from tests.test_denuncia_robo_aviso import AGORA, CAPTCHA_ML, RedisFake, _resumo, _status
from tests.test_threema_pontos_informar import _linha_informar
from tests.test_threema_pontos_logistica_devolucoes import _Fake, _ligar, _registro, _sinos
from tests.test_threema_resolver import _usuario, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.asyncio

GERENTE = "sa.geral@tutamail.com"

# As chaves da C9d, a lista antiga de cada uma (a 1ª linha do Informar) e o
# remetente de HOJE (o `contexto` que o código passava ao ThreemaClient).
REMETENTE_DE_HOJE = {
    K.CHAMADOS_IA_PARADA: "chamados",
    K.CHAMADOS_ENCAMINHAR_JURIDICO: "juridico",
    K.DENUNCIA_RELATORIO_DIARIO: "chamados",
    K.DENUNCIA_ROBO_PRECISA_ALGUEM: "chamados",
}
LISTA_DE_HOJE = {
    K.CHAMADOS_IA_PARADA: "chamados_ia",
    K.CHAMADOS_ENCAMINHAR_JURIDICO: "juridico",
    K.DENUNCIA_RELATORIO_DIARIO: "denuncia_relatorio",
    K.DENUNCIA_ROBO_PRECISA_ALGUEM: "denuncia_robo",
}
MINHAS = list(REMETENTE_DE_HOJE)
IA_PARADA = K.CHAMADOS_IA_PARADA
JURIDICO = K.CHAMADOS_ENCAMINHAR_JURIDICO
RELATORIO = K.DENUNCIA_RELATORIO_DIARIO
ROBO = K.DENUNCIA_ROBO_PRECISA_ALGUEM


class _FakeCanal(_Fake):
    """O ThreemaClient falso da bateria, com o canal que o ponto lê antes de
    mandar (`threema_envios.canal`, o `client.disabled` de antes)."""

    disabled = False
    canal = "logistica"

    async def send_to_all(self, texto, destinos):
        r = await super().send_to_all(texto, destinos)
        marca = threema_registro.marca_atual()
        _Fake.envios[-1]["origem"] = marca.origem if marca else None
        return r


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _Fake.envios = []
    threema_envios.limpar_cache()
    threema_registro.reiniciar_disjuntor()
    s = get_settings()
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    monkeypatch.setattr(s, "threema_registro_ativo", True)
    monkeypatch.setattr(threema, "ThreemaClient", _FakeCanal)
    yield
    threema_envios.limpar_cache()


@pytest.fixture(autouse=True)
async def _denuncia_limpa(db):
    """O conftest não limpa as tabelas da Denúncia."""
    await db.execute(delete(DenunciaRelatorio))
    await db.execute(delete(DenunciaRoboStatus))
    await db.commit()
    yield
    await db.rollback()
    await db.execute(delete(DenunciaRelatorio))
    await db.execute(delete(DenunciaRoboStatus))
    await db.commit()


@pytest.fixture
def redis_fake(monkeypatch):
    r = RedisFake()
    monkeypatch.setattr(denuncia_robo_aviso, "redis", r)
    return r


async def _pessoas(db):
    admin = await _usuario(db, "admin c9d", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c9d", "TSTJ0006")
    sumido = await _usuario(db, "sumido c9d", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, sumido


def _envios() -> list[tuple]:
    return [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios]


async def _modo_relatorio(db, modo: str) -> None:
    await ouvidoria.sincronizar_catalogo(db)
    (await db.get(OuvidoriaRobo, rt.ROBO, populate_existing=True)).modo = modo
    await db.commit()


async def _rodadas_relatorio(db) -> list[OuvidoriaRodada]:
    await db.commit()
    return list(
        (
            await db.execute(
                select(OuvidoriaRodada)
                .where(OuvidoriaRodada.robo_chave == rt.ROBO)
                .order_by(OuvidoriaRodada.iniciada_em)
            )
        ).scalars()
    )


async def _analise(client, chamado_id: str, parado: str = "captcha") -> str:
    """Item 15: a IA devolve `humano` parada na tela (a rota de verdade)."""
    r = await client.post(
        "/api/chamados/agent/analise", headers=IA, json=_decisao(chamado_id, parado=parado)
    )
    assert r.status_code == 200, r.text
    return r.json()["aviso"]


async def _encaminhar(client, chamado_id: str):
    """Item 16: o botão Encaminhar ao jurídico do chamado."""
    return await client.post(f"/api/chamados/{chamado_id}/juridico", json={"observacao": "x"})


async def _chamado(db, chamado_id) -> Chamado:
    await db.commit()
    return await db.get(Chamado, chamado_id, populate_existing=True)


async def _robo_com_captcha(db) -> None:
    """Item 44: o Mac mini mandou o status com o captcha do ML na tela."""
    await _status(db, _resumo([CAPTCHA_ML]), AGORA)


def test_as_chaves_da_c9d_estao_migradas_com_o_remetente_de_hoje():
    for chave, remetente in REMETENTE_DE_HOJE.items():
        d = CATALOGO[chave]
        assert d.migrado and d.fatia == "F2", chave
        assert d.remetente == remetente, chave
        assert d.legado.informar[0] == LISTA_DE_HOJE[chave], chave
    # a reserva `chamados_ia` do 44 e o grupo dos dois
    assert CATALOGO[ROBO].legado.informar == ("denuncia_robo", "chamados_ia")
    assert CATALOGO[IA_PARADA].legado.grupo == CATALOGO[ROBO].legado.grupo == G_IA_CHAMADO
    assert {c: CATALOGO[c].ao_desligar for c in MINHAS} == {
        IA_PARADA: "descarta",
        JURIDICO: "recusa",
        RELATORIO: "descarta",
        ROBO: "descarta",
    }
    # o 43 liga e desliga pelo modo do robô (a tela grava lá); o 16 depende do canal
    assert CATALOGO[RELATORIO].interruptor == "robo_modo"
    assert [x.alvo for x in CATALOGO[JURIDICO].depende_de] == ["juridico"]
    # a linha vazia de hoje é o desligado dos automáticos por lista (15 e 44);
    # o botão (16) vazio é o 422, e o 43 tem o modo do robô
    assert {c: threema_envios._lista_vazia_desliga(CATALOGO[c]) for c in MINHAS} == {
        IA_PARADA: True,
        JURIDICO: False,
        RELATORIO: False,
        ROBO: True,
    }


# ─────────────────────────────────────────────── item 15: IA de Chamado parada


async def test_ia_parada_com_a_linha_vazia_manda_pelo_cadastro(db, client, cid):  # noqa: F811
    _admin, joana, sumido = await _pessoas(db)
    await _linha_informar(db, "chamados_ia", "")
    # a lista do Cadastro tem quem foi suspenso também: ele fica de fora
    await semeado(db, IA_PARADA, joana, sumido)
    aviso = await _analise(client, cid)
    assert aviso == "aviso enviado no Threema pra joana c9d"
    assert _envios() == [(IA_PARADA, "cadastro", "chamados", ["TSTJ0006"])]
    assert 'parou no "não sou robô"' in _Fake.envios[0]["texto"]
    assert any("enviado no Threema pra joana c9d" in t for t in await _sistema(db))


async def test_ia_parada_desligado_nao_manda_e_o_aviso_se_perde(db, client, cid):  # noqa: F811
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, IA_PARADA, joana, ativo=False)
    aviso = await _analise(client, cid)
    assert aviso == "aviso não saiu: o aviso está desligado em Cadastros › Envios Threema"
    assert _Fake.envios == []
    assert await _registro(db, IA_PARADA) == [] and await _sinos(db, IA_PARADA) == []
    # o histórico do chamado diz por quê
    assert any("desligado em Cadastros" in t for t in await _sistema(db))
    # religado, a parada que passou não é reenviada (sem retentativa: descarta);
    # a próxima parada sai, uma mensagem só
    await _ligar(db, IA_PARADA, None)
    threema_envios.limpar_cache()
    aviso = await _analise(client, cid, "login")
    assert aviso == "aviso enviado no Threema pra joana c9d"
    assert [e["texto"].splitlines()[0] for e in _Fake.envios] == [
        "🤖 IA de Chamado parou numa tela de login/código e precisa de você"
    ]


async def test_ia_parada_ninguem_valido_registra_e_acende_o_sino(db, client, cid):  # noqa: F811
    admin, _joana, sumido = await _pessoas(db)
    # a lista antiga tem gente: o Cadastro sem ninguém válido NUNCA cai nela
    await _linha_informar(db, "chamados_ia", "TSTZ0009")
    await semeado(db, IA_PARADA, sumido)
    aviso = await _analise(client, cid, "login")
    assert aviso.startswith("aviso não saiu: ninguém válido recebe este aviso")
    assert _Fake.envios == []
    assert [(a.user_id, a.escopo) for a in await _sinos(db, IA_PARADA)] == [(None, "adm")]
    (linha,) = await _registro(db, IA_PARADA)
    assert (linha.ok, linha.erro, linha.via, linha.destinatario_id, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        None,
        "chamados",
    )
    assert any("tela de login" in t and "ninguém válido" in t for t in await _sistema(db))


# ─────────────────────────────────────────────── item 16: Encaminhar ao jurídico


async def test_juridico_com_a_linha_vazia_manda_pelo_cadastro(db, client, make_user, auth_as):
    """O botão segue para quem edita chamados (não precisa ser admin)."""
    _admin, joana, sumido = await _pessoas(db)
    await _linha_informar(db, "juridico", "")
    await semeado(db, JURIDICO, joana, sumido)
    auth_as(await make_user(permissions=_perms()))
    ch = await _criar_chamado(client)
    r = await _encaminhar(client, ch["id"])
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == ["TSTJ0006"]
    assert _envios() == [(JURIDICO, "cadastro", "juridico", ["TSTJ0006"])]
    assert r.json()["link"] in _Fake.envios[0]["texto"]
    c = await _chamado(db, ch["id"])
    assert c.juridico_enviado_at is not None and c.juridico_destinatarios == "TSTJ0006"


async def test_juridico_desligado_recusa_sem_mexer_no_chamado(db, client, make_user, auth_as):
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, JURIDICO, joana, ativo=False)
    auth_as(await make_user(permissions=_perms()))
    ch = await _criar_chamado(client)
    r = await _encaminhar(client, ch["id"])
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "envio_desligado"
    assert _Fake.envios == []
    assert await _registro(db, JURIDICO) == [] and await _sinos(db, JURIDICO) == []
    c = await _chamado(db, ch["id"])
    assert c.juridico_enviado_at is None and c.juridico_token is None
    await _ligar(db, JURIDICO, None)
    r = await _encaminhar(client, ch["id"])
    assert r.status_code == 200, r.text
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]


async def test_juridico_ninguem_valido_registra_acende_o_sino_e_recusa(
    db, client, make_user, auth_as
):
    admin, _joana, sumido = await _pessoas(db)
    await _linha_informar(db, "juridico", "TSTZ0009")
    await semeado(db, JURIDICO, sumido)
    auth_as(await make_user(permissions=_perms()))
    ch = await _criar_chamado(client)
    r = await _encaminhar(client, ch["id"])
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "sem_destinatarios"
    assert _Fake.envios == []
    assert [(a.user_id, a.escopo) for a in await _sinos(db, JURIDICO)] == [(None, "adm")]
    (linha,) = await _registro(db, JURIDICO)
    assert (linha.ok, linha.erro, linha.via, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        "juridico",
    )
    c = await _chamado(db, ch["id"])
    assert c.juridico_enviado_at is None and c.juridico_token is None


# ─────────────────────────────────────────────── item 43: relatório da Denúncia


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_relatorio_com_a_linha_vazia_manda_pelo_cadastro_e_carimba(db):
    _admin, joana, sumido = await _pessoas(db)
    await _modo_relatorio(db, "ligado")
    await _linha_informar(db, "denuncia_relatorio", "")
    await semeado(db, RELATORIO, joana, sumido)
    await _dia(db)
    r = await rt.enviar_pendente(db, AS_7H07)
    await db.commit()
    assert r["enviado"] and r["sent"] == ["TSTJ0006"]
    assert _envios() == [(RELATORIO, "cadastro", "chamados", ["TSTJ0006"])]
    assert _Fake.envios[0]["texto"].startswith("📊 Relatório geral")
    assert (await db.get(DenunciaRelatorio, ONTEM)).threema_enviado_em is not None
    (rodada,) = await _rodadas_relatorio(db)
    assert rodada.ok and rodada.resumo == "relatório de 05/10 para 1 pessoa(s)"
    # a hora seguinte não manda de novo
    r = await rt.enviar_pendente(db, AS_7H07 + timedelta(hours=1))
    assert r == {"enviado": False, "motivo": "já enviado"} and len(_Fake.envios) == 1


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_relatorio_desligado_nao_carimba_e_o_dia_se_perde(db):
    """O interruptor do 43 é o modo do robô (a tela do Cadastro grava lá):
    desligado não manda nem carimba, mas só sai o relatório de ONTEM — o dia
    do período desligado não volta (o `descarta`)."""
    _admin, joana, _ = await _pessoas(db)
    await _modo_relatorio(db, "desligado")
    await semeado(db, RELATORIO, joana)
    await _dia(db)
    r = await rt.enviar_pendente(db, AS_7H07)
    assert r == {"enviado": False, "motivo": "desligado na Ouvidoria › Robôs"}
    assert (await threema_envios.resolver(RELATORIO, session=db)).ativo is False
    assert _Fake.envios == []
    assert (await db.get(DenunciaRelatorio, ONTEM)).threema_enviado_em is None
    assert await _registro(db, RELATORIO) == [] and await _sinos(db, RELATORIO) == []
    assert await _rodadas_relatorio(db) == []
    # religado no dia seguinte: o "ontem" já é outro dia; o 05/10 se perdeu
    await _modo_relatorio(db, "ligado")
    r = await rt.enviar_pendente(db, AS_7H07 + timedelta(days=1))
    assert r == {"enviado": False, "motivo": "relatório de ontem ainda não fechado"}
    assert _Fake.envios == []


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_relatorio_ninguem_valido_registra_sino_e_rodada_com_falha(db):
    _admin, joana, sumido = await _pessoas(db)
    await _modo_relatorio(db, "ligado")
    await _linha_informar(db, "denuncia_relatorio", "TSTZ0009")
    await semeado(db, RELATORIO, sumido)
    await _dia(db)
    r = await rt.enviar_pendente(db, AS_7H07)
    assert r == {"enviado": False, "motivo": "ninguém válido para receber"}
    assert _Fake.envios == []
    assert (await db.get(DenunciaRelatorio, ONTEM)).threema_enviado_em is None
    assert [(a.user_id, a.escopo) for a in await _sinos(db, RELATORIO)] == [(None, "adm")]
    (linha,) = await _registro(db, RELATORIO)
    assert (linha.ok, linha.erro, linha.via, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        "chamados",
    )
    (rodada,) = await _rodadas_relatorio(db)
    assert rodada.ok is False and "ninguém válido para receber" in rodada.resumo
    # acertada a lista, a hora seguinte manda (o dia não foi carimbado)
    await db.refresh(joana)
    await semeado(db, RELATORIO, joana)
    r = await rt.enviar_pendente(db, AS_7H07 + timedelta(hours=1))
    assert r["enviado"] and r["sent"] == ["TSTJ0006"]
    assert len(await _sinos(db, RELATORIO)) == 1


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_teste_do_relatorio_vai_so_para_o_escolhido_mesmo_desligado(
    db, client, make_user, auth_as
):
    """O teste da tela (quem edita a Denúncia, não precisa ser admin) sai pelo
    mesmo envio, origem `teste`, só para o escolhido — com o robô desligado e
    fora da lista do Cadastro — e não carimba o dia."""
    _admin, joana, _ = await _pessoas(db)
    await _modo_relatorio(db, "desligado")
    await semeado(db, RELATORIO, ativo=False)
    await _dia(db)
    editor = await make_user(permissions={"denuncia": {"view": True, "edit": True}})
    auth_as(editor)
    r = await client.post(
        "/api/denuncia/relatorios/2026-10-05/threema/teste", json={"para": "tstj0006"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == ["TSTJ0006"]
    assert _envios() == [(RELATORIO, "forcado", "chamados", ["TSTJ0006"])]
    assert _Fake.envios[0]["origem"] == "teste"
    assert (await db.get(DenunciaRelatorio, ONTEM)).threema_enviado_em is None
    # fora do diretório (quem não é usuário ativo nem contato) não manda
    r = await client.post(
        "/api/denuncia/relatorios/2026-10-05/threema/teste", json={"para": "TSTS0009"}
    )
    assert r.status_code == 422 and len(_Fake.envios) == 1


# ─────────────────────────────────────────────── item 44: robô de Denúncia


async def test_robo_com_a_linha_vazia_manda_pelo_cadastro(db, redis_fake):
    _admin, joana, sumido = await _pessoas(db)
    await _linha_informar(db, "denuncia_robo", "")
    await _linha_informar(db, "chamados_ia", "")
    await semeado(db, ROBO, joana, sumido)
    await _robo_com_captcha(db)
    r = await denuncia_robo_aviso.rodar(db, AGORA)
    assert r["avisadas"] == 1
    assert _envios() == [(ROBO, "cadastro", "chamados", ["TSTJ0006"])]
    assert "🧩" in _Fake.envios[0]["texto"]
    # 2 min depois o painel ainda mostra o captcha: não repete
    r = await denuncia_robo_aviso.rodar(db, AGORA + timedelta(minutes=2))
    assert r["avisadas"] == 0 and len(_Fake.envios) == 1


async def test_robo_desligado_marca_e_o_aviso_se_perde(db, redis_fake):
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, ROBO, joana, ativo=False)
    await _robo_com_captcha(db)
    r = await denuncia_robo_aviso.rodar(db, AGORA)
    assert r == {"avisadas": 0, "erro": "desligado em Cadastros › Envios Threema"}
    assert _Fake.envios == []
    assert await _registro(db, ROBO) == [] and await _sinos(db, ROBO) == []
    # as marcas ficam (descarta): religado, o captcha que já passou não sai
    assert redis_fake.d and denuncia_robo_aviso.NINGUEM not in redis_fake.d
    await _ligar(db, ROBO, None)
    threema_envios.limpar_cache()
    r = await denuncia_robo_aviso.rodar(db, AGORA + timedelta(minutes=2))
    assert r == {"avisadas": 0} and _Fake.envios == []


async def test_robo_ninguem_valido_nao_gasta_as_marcas(db, redis_fake):
    admin, joana, sumido = await _pessoas(db)
    # a reserva antiga tem gente: o Cadastro sem ninguém válido NUNCA cai nela
    await _linha_informar(db, "chamados_ia", "TSTZ0009")
    await semeado(db, ROBO, sumido)
    await _robo_com_captcha(db)
    r = await denuncia_robo_aviso.rodar(db, AGORA)
    assert r == {"avisadas": 0, "erro": "ninguém válido para receber"}
    assert _Fake.envios == []
    assert [(a.user_id, a.escopo) for a in await _sinos(db, ROBO)] == [(None, "adm")]
    (linha,) = await _registro(db, ROBO)
    assert (linha.ok, linha.erro, linha.via, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        "chamados",
    )
    # nenhuma marca de ocorrência gasta (só a da hora do ninguém)
    assert set(redis_fake.d) == {denuncia_robo_aviso.NINGUEM}
    # a volta seguinte, na mesma hora: calada, sem registro novo e sem marcar
    r = await denuncia_robo_aviso.rodar(db, AGORA + timedelta(minutes=2))
    assert r == {"avisadas": 0}
    assert len(await _registro(db, ROBO)) == 1
    assert set(redis_fake.d) == {denuncia_robo_aviso.NINGUEM}
    # acertada a lista, a volta seguinte avisa o mesmo captcha
    await semeado(db, ROBO, joana)
    r = await denuncia_robo_aviso.rodar(db, AGORA + timedelta(minutes=4))
    assert r["avisadas"] == 1
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_configuracao_antiga_vazia_fica_calada_como_hoje(
    db, monkeypatch, client, make_user, auth_as, redis_fake, cid  # noqa: F811
):
    """Emergência (o piso do .env) com as listas antigas vazias: nada sai e
    nada vai para o registro nem para o sino — o comportamento de antes da
    fatia (o jurídico responde 422, o relatório e o robô dizem "ninguém
    cadastrado", o robô marca as ocorrências como antes)."""
    _admin, joana, _ = await _pessoas(db)
    for chave in MINHAS:
        await semeado(db, chave, joana)
    for ctx in ("chamados_ia", "juridico", "denuncia_relatorio", "denuncia_robo"):
        await _linha_informar(db, ctx, "")
    monkeypatch.setattr(get_settings(), "threema_cadastro_ativo", False)
    aviso = await _analise(client, cid)
    assert "ninguém cadastrado" in aviso
    ch = await _criar_chamado(client)
    r = await _encaminhar(client, ch["id"])
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "sem_destinatarios")
    await _modo_relatorio(db, "ligado")
    await _dia(db)
    assert (await rt.enviar_pendente(db, AS_7H07))["motivo"] == "ninguém cadastrado"
    await _robo_com_captcha(db)
    r = await denuncia_robo_aviso.rodar(db, AGORA)
    assert r == {"avisadas": 0, "erro": "ninguém cadastrado"}
    assert _Fake.envios == []
    for chave in MINHAS:
        assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    assert await _rodadas_relatorio(db) == []
    # e com gente na lista antiga, a emergência manda para ela (via legado)
    await _linha_informar(db, "juridico", ID["harry"])
    r = await _encaminhar(client, ch["id"])
    assert r.status_code == 200, r.text
    assert _envios() == [(JURIDICO, "legado", "juridico", [ID["harry"]])]


# ─────────────────────────────────────────────── as rotas


async def test_put_das_listas_migradas_responde_410_e_o_get_mostra_a_antiga(
    db, client, auth_as, make_user
):
    admin = await make_user(role=UserRole.ADMIN)
    await _linha_informar(db, "chamados_ia", ID["cairo_sa"])
    auth_as(admin)
    envios = {
        "chamados_ia": [IA_PARADA, ROBO],
        "juridico": [JURIDICO],
        "denuncia_relatorio": [RELATORIO],
        "denuncia_robo": [ROBO],
    }
    for contexto, chaves in envios.items():
        r = await client.put(f"/api/informar/{contexto}", json={"recipients": [ID["joana"]]})
        assert r.status_code == 410, (contexto, r.text)
        detalhe = r.json()["detail"]
        assert detalhe["code"] == "quem_recebe_no_cadastro"
        assert "Cadastros › Envios Threema" in detalhe["message"]
        assert sorted(detalhe["envios"]) == sorted(chaves), contexto
    # a lista antiga não mudou (é a reserva de emergência) e o GET a mostra
    r = await client.get("/api/informar/chamados_ia")
    assert r.status_code == 200 and r.json()["recipients"] == [ID["cairo_sa"]]
    assert all(informar_router._lista_no_cadastro(ctx) for ctx in envios)


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_gerente_perde_a_edicao_mas_segue_vendo_quem_recebe(
    db, client, auth_as, make_user
):
    """O Cairo escolhia quem recebe na IA de Chamado e nas duas da Denúncia:
    agora não grava mais (nem no Informar, nem no Cadastro, só de admin), mas
    continua vendo quem recebe (só nomes). O jurídico segue só de admin."""
    _admin, joana, _ = await _pessoas(db)
    for chave in MINHAS:
        await semeado(db, chave, joana)
    await _modo_relatorio(db, "ligado")  # o 43 desligado não mostra ninguém
    gerente = await make_user(email=GERENTE, role=UserRole.USER)
    auth_as(gerente)
    for contexto in ("chamados_ia", "denuncia_robo", "denuncia_relatorio"):
        r = await client.put(f"/api/informar/{contexto}", json={"recipients": [ID["harry"]]})
        assert r.status_code == 410, (contexto, r.text)
        assert (await client.get(f"/api/informar/{contexto}")).status_code == 200
    assert (await client.put("/api/informar/juridico", json={"recipients": []})).status_code == 403
    for chave in MINHAS:
        r = await client.patch(
            f"/api/threema/envios/{chave}",
            json={"destinatarios": [{"tipo": "usuario", "id": str(joana.id)}]},
        )
        assert r.status_code == 403, (chave, r.text)
    for chave in (IA_PARADA, RELATORIO, ROBO):
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 200, (chave, r.text)
        q = r.json()
        assert q["nomes"] == ["joana c9d"] and q["usa_configuracao_antiga"] is False
        assert "TSTJ0006" not in r.text
    r = await client.get(f"/api/threema/envios/{JURIDICO}/quem-recebe")
    assert r.status_code == 403
    # quem não tem o botão hoje continua sem ver
    operador = await make_user(permissions=_perms())
    auth_as(operador)
    for chave in MINHAS:
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 403, chave
    # o admin vê o do jurídico
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"/api/threema/envios/{JURIDICO}/quem-recebe")
    assert r.status_code == 200 and r.json()["nomes"] == ["joana c9d"]


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_atalho_do_relatorio_desligado_diz_que_e_o_robo_e_mostra_a_lista(
    db, client, auth_as, make_user
):
    """Revisão da F2: com o robô do relatório desligado em Ouvidoria › Robôs
    (`ativo` NULL no Cadastro), o atalho não diz "Desligado em Cadastros" (o
    Cairo nem tem acesso a Cadastros): diz que é o robô e mostra quem está
    na lista (quem recebe ao religar), só nomes. Desligado no Cadastro, diz
    que foi lá e por quem."""
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, RELATORIO, joana)
    await _modo_relatorio(db, "desligado")
    auth_as(await make_user(email=GERENTE, role=UserRole.USER))
    r = await client.get(f"/api/threema/envios/{RELATORIO}/quem-recebe")
    assert r.status_code == 200, r.text
    q = r.json()
    assert (q["ativo"], q["nomes"], q["receberia"]) == (False, [], ["joana c9d"])
    assert "Ouvidoria › Robôs" in q["desligado"] and "Cadastros" not in q["desligado"]
    assert "TSTJ0006" not in r.text
    # ligado, a lista é a mesma
    await _modo_relatorio(db, "ligado")
    q = (await client.get(f"/api/threema/envios/{RELATORIO}/quem-recebe")).json()
    assert (q["ativo"], q["nomes"], q["receberia"], q["desligado"]) == (
        True,
        ["joana c9d"],
        ["joana c9d"],
        None,
    )
    # o robô de Denúncia desligado no Cadastro pelo admin
    await semeado(db, ROBO, joana)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.patch(f"/api/threema/envios/{ROBO}", json={"ativo": False})
    assert r.status_code == 200, r.text
    q = (await client.get(f"/api/threema/envios/{ROBO}/quem-recebe")).json()
    assert (q["ativo"], q["receberia"]) == (False, ["joana c9d"])
    assert q["desligado"].startswith("Desligado em Cadastros › Envios Threema por "), q


# ─────────────────────────────────────────────── a semente da fatia


@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_ponto_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, client, auth_as, make_user, redis_fake, variante
):
    await montar(db, monkeypatch, variante=variante)
    leg = {c: await threema_envios.legado(c, session=db) for c in MINHAS}
    esperado = {c: list(threema_envios._dedupe(leg[c].ids)) for c in MINHAS}
    await threema_envios.semear(db, fatia="F2", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in MINHAS]
    # a lista antiga fica congelada como reserva, mas o ponto não lê mais:
    # esvaziar não muda quem recebe
    for ctx in ("chamados_ia", "juridico", "denuncia_relatorio", "denuncia_robo"):
        await _linha_informar(db, ctx, "")
    threema_envios.limpar_cache()

    # 16: o botão do chamado (quem edita chamados)
    auth_as(await make_user(permissions=_perms()))
    ch = await _criar_chamado(client)
    r = await _encaminhar(client, ch["id"])
    if esperado[JURIDICO]:
        assert r.status_code == 200, r.text
        assert _envios() == [(JURIDICO, "cadastro", "juridico", esperado[JURIDICO])]
    else:
        # botão vazio: ligado sem ninguém, como os outros botões (422)
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "sem_destinatarios")
        assert _Fake.envios == []

    # 15: a IA de Chamado parada (direto no serviço, com o chamado do botão)
    from app.services import chamados_ia_aviso

    _Fake.envios = []
    c = await _chamado(db, ch["id"])
    aviso = await chamados_ia_aviso.avisar(db, c, "captcha", "parou no captcha")
    await db.commit()
    assert esperado[IA_PARADA] == [ID["cairo_sa"]]  # `chamados_ia` tem o Cairo nas três
    assert aviso.startswith("aviso enviado"), aviso
    assert _envios() == [(IA_PARADA, "cadastro", "chamados", esperado[IA_PARADA])]

    # 43: o relatório do dia (o modo do robô é o interruptor: ligado só em `reservas`)
    _Fake.envios = []
    await _dia(db)
    r = await rt.enviar_pendente(db, AS_7H07)
    await db.commit()
    if leg[RELATORIO].ativo:
        assert r["enviado"], r
        assert _envios() == [(RELATORIO, "cadastro", "chamados", esperado[RELATORIO])]
    else:
        assert r == {"enviado": False, "motivo": "desligado na Ouvidoria › Robôs"}
        assert _Fake.envios == []

    # 44: o robô de Denúncia (a reserva `chamados_ia` quando não há `denuncia_robo`)
    _Fake.envios = []
    await _robo_com_captcha(db)
    r = await denuncia_robo_aviso.rodar(db, AGORA)
    linha = await db.get(ThreemaEnvio, ROBO, populate_existing=True)
    if esperado[ROBO]:
        assert r["avisadas"] == 1
        assert _envios() == [(ROBO, "cadastro", "chamados", esperado[ROBO])]
        assert linha.ativo is None
    else:
        # `denuncia_robo` salva vazia = ninguém hoje (não cai na reserva):
        # nasce desligado, calado (sem registro nem sino a cada 2 min)
        assert r == {"avisadas": 0, "erro": "desligado em Cadastros › Envios Threema"}
        assert linha.ativo is False
        assert _Fake.envios == []
        assert await _registro(db, ROBO) == [] and await _sinos(db, ROBO) == []

    # o grupo "IA de Chamado" junta o 15 e o 44 — salvo quando a linha do 44 é
    # o desligado de hoje: ele fica de fora do grupo (sem brigar com o 15)
    grupo = (
        await db.execute(select(ThreemaGrupo).where(ThreemaGrupo.nome == G_IA_CHAMADO))
    ).scalar_one()
    no_grupo = set(
        (
            await db.execute(
                select(ThreemaEnvioDestinatario.envio_chave).where(
                    ThreemaEnvioDestinatario.grupo_id == grupo.id
                )
            )
        ).scalars()
    )
    assert no_grupo == ({IA_PARADA, ROBO} if esperado[ROBO] else {IA_PARADA})
    if variante == "cheio":
        assert esperado[ROBO] == [ID["cairo_sa"]]  # a reserva
        assert esperado[JURIDICO] == []  # hoje ninguém
        assert esperado[RELATORIO] == [ID["harry"], ID["cairo_sa"], ID["roma"]]
    if variante == "reservas":
        assert esperado[JURIDICO] == [ID["joana"]]
    if variante == "vazias":
        assert esperado[ROBO] == []


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(
    db, monkeypatch, redis_fake
):
    """Variante `reservas` (o robô do relatório ligado): o harry suspenso
    depois da semente sai do relatório, embora a lista antiga ainda o tenha;
    o "cairo sa" suspenso esvazia o grupo "IA de Chamado" — o robô de Denúncia
    não cai na lista antiga: ninguém válido, sino."""
    c = await montar(db, monkeypatch, variante="reservas")
    await threema_envios.semear(db, fatia="F2", chaves=MINHAS, aplicar=True)
    await db.commit()
    c.usuarios["harry"].status = UserStatus.SUSPENDED
    c.usuarios["cairo_sa"].status = UserStatus.SUSPENDED
    await db.commit()
    threema_envios.limpar_cache()
    await _dia(db)
    r = await rt.enviar_pendente(db, AS_7H07)
    await db.commit()
    assert r["enviado"]
    assert _envios() == [(RELATORIO, "cadastro", "chamados", [ID["roma"]])]
    leg = await threema_envios.legado(RELATORIO, session=db)
    assert ID["harry"] in leg.ids
    _Fake.envios = []
    await _robo_com_captcha(db)
    r = await denuncia_robo_aviso.rodar(db, AGORA)
    assert r == {"avisadas": 0, "erro": "ninguém válido para receber"}
    assert _Fake.envios == []
    assert ID["cairo_sa"] in (await threema_envios.legado(ROBO, session=db)).ids
    assert [(a.user_id, a.escopo) for a in await _sinos(db, ROBO)] == [(None, "adm")]
