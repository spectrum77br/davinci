"""F3/C10a da tarefa 16: o aviso das pendências de cada robô da Ouvidoria
(itens 29 a 33 e 35 a 42, `ouvidoria.avisar_pendentes`) passa a ler o
Cadastro (Cadastros › Envios Threema). A lista "Avisar" do robô (com a reserva
de hoje: o .env do robô e o OUVIDORIA geral) fica congelada como reserva de
emergência e fonte da semente; o PATCH dos robôs ignora a lista e o re-aviso
do robô migrado, com aviso — "só ADM altera quem recebe" (decisão do dono,
23/11): o Cairo (`ouvidoria:edit`) perde a edição e continua vendo quem recebe
(só nomes); o re-aviso passa a ser editado no Cadastro (gravado no robô). O
do Robô da Margem (34) migra com a Margem, na C10b.

O modo do robô continua valendo antes (o "depende de"): silencioso ou
desligado não manda nada — nem sino, nem registro — como hoje.

Um teste por ponto, PELO CAMINHO REAL (`avisar_pendentes` com as ocorrências
do robô, a tela dos robôs e o "Rodar agora"; só o ThreemaClient é falso):

* (a) Cadastro semeado com a lista antiga VAZIA: manda para quem está no
  Cadastro, com a chave `ouvidoria.<robô>` e o remetente do robô, e carimba;
* (b) desligado no Cadastro: não manda nem carimba; as pendências se
  acumulam e, ao religar, saem numa mensagem só (o `acumula`);
* (c) ninguém válido (a única pessoa da lista foi suspensa): não manda,
  acende o sino dos admins e grava o registro `ok=false`, sem carimbo; sem
  pendência não acende nada;
* a configuração antiga vazia (emergência) fica calada, como hoje.

E a semente da fatia: `semear` + `conferir` das chaves dá 0 diferenças e,
depois dela, cada robô manda EXATAMENTE para quem o legado mandaria. A lista
antiga vazia é o desligado de hoje (`sem_destinatarios`, calado): a semente
grava o envio desligado (sem sino a cada rodada).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import db as db_mod
from app.config import get_settings
from app.models import OuvidoriaOcorrencia, OuvidoriaRobo, ThreemaEnvio, UserRole, UserStatus
from app.routers import ouvidoria as router_ouvidoria
from app.services import ouvidoria, threema, threema_envios, threema_registro
from app.services.threema_catalogo import CATALOGO, K, chave_ouvidoria
from tests.test_threema_pontos_logistica_devolucoes import _Fake, _ligar, _registro, _sinos
from tests.test_threema_resolver import _usuario, desmigrar, migrar, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("limpar_ouvidoria")]

# As chaves da C10a: o aviso de cada robô da Ouvidoria, menos o Relatório da
# Denúncia (envio próprio, F2) e o Robô da Margem (C10b).
MINHAS = [
    c for r in ouvidoria.ROBOS if (c := chave_ouvidoria(r)) is not None and r != "vigia_margem"
]
MARGEM = K.OUVIDORIA_VIGIA_MARGEM


def _robo_de(chave: str) -> str:
    return chave.removeprefix("ouvidoria.")


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
    # a configuração antiga sem reserva: o .env do robô e o OUVIDORIA geral vazios
    monkeypatch.setattr(s, "ouvidoria_threema_recipients", "")
    monkeypatch.setattr(s, "vigia_importacao_threema_recipients", "")
    monkeypatch.setattr(threema, "ThreemaClient", _FakeCanal)
    yield
    threema_envios.limpar_cache()


async def _pessoas(db):
    admin = await _usuario(db, "admin c10a", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c10a", "TSTJ0006")
    sumido = await _usuario(db, "sumido c10a", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, sumido


async def _robo(
    db, robo: str, *, modo: str = "ligado", lista: str | None = None, **campos
) -> OuvidoriaRobo:
    await ouvidoria.sincronizar_catalogo(db)
    row = await db.get(OuvidoriaRobo, robo, populate_existing=True)
    row.modo = modo
    row.threema_recipients = lista
    for k, v in campos.items():
        setattr(row, k, v)
    await db.commit()
    return row


async def _pendencia(db, robo: str, chave: str = "x:1") -> OuvidoriaOcorrencia:
    o = await ouvidoria.registrar(
        db, robo, chave, titulo=f"Problema {chave}", conta="Conta X", pedido=chave[2:]
    )
    await db.commit()
    return o


async def _ocorrencias(db, robo: str) -> list[OuvidoriaOcorrencia]:
    await db.commit()
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(OuvidoriaOcorrencia.robo_chave == robo)
                .order_by(OuvidoriaOcorrencia.chave)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


def _envios() -> list[tuple]:
    return [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios]


def _perms(*, edit: bool = False) -> dict:
    return {"ouvidoria": {"view": True, "edit": edit, "delete": False}}


def test_as_chaves_da_c10a_estao_migradas_com_o_remetente_do_robo():
    assert len(MINHAS) == 13
    for chave in MINHAS:
        d = CATALOGO[chave]
        robo = ouvidoria.ROBOS[_robo_de(chave)]
        assert d.migrado and d.fatia == "F3", chave
        assert d.remetente == robo.contexto, chave
        assert d.legado.robo == _robo_de(chave), chave
        assert d.ao_desligar == "acumula", chave
        assert d.interruptor == "proprio", chave
        assert [x.alvo for x in d.depende_de] == [_robo_de(chave)], chave
        # quem vê a tela dos robôs vê quem recebe (só nomes)
        assert d.leitura == "perm:ouvidoria:view", chave
        # a lista antiga vazia é o desligado de hoje (`sem_destinatarios`)
        assert threema_envios._lista_vazia_desliga(d), chave
    # o 34 foi com a Margem (C10b, `test_threema_pontos_margem`); o relatório
    # da Denúncia tem envio próprio
    assert CATALOGO[MARGEM].migrado and CATALOGO[MARGEM].fatia == "F3"
    assert chave_ouvidoria("denuncia_relatorio_threema") is None
    # os avisos da Margem que leem a lista do robô entraram na regra na C10b
    for chave in (K.MARGEM_PEDIDO_SEGURADO, K.MARGEM_REAVALIACAO, K.MARGEM_MARGEM_ALTA):
        assert threema_envios._lista_vazia_desliga(CATALOGO[chave]), chave


# ─────────────────────────────────────────────── (a) pelo Cadastro


@pytest.mark.parametrize("chave", MINHAS)
async def test_com_a_lista_antiga_vazia_manda_pelo_cadastro_e_carimba(db, chave):
    robo = _robo_de(chave)
    _admin, joana, sumido = await _pessoas(db)
    await _robo(db, robo, lista=None)
    # a lista do Cadastro tem quem foi suspenso também: ele fica de fora
    await semeado(db, chave, joana, sumido)
    await _pendencia(db, robo)
    r = await ouvidoria.avisar_pendentes(db, robo)
    await db.commit()
    assert r == {"avisadas": 1}
    assert _envios() == [(chave, "cadastro", ouvidoria.ROBOS[robo].contexto, ["TSTJ0006"])]
    texto = _Fake.envios[0]["texto"]
    assert texto.startswith(f"{ouvidoria.ROBOS[robo].nome} — 1 ocorrência:")
    assert texto.splitlines()[-1].endswith(f"/ouvidoria/robos?aba=ocorrencias&robo={robo}")
    (o,) = await _ocorrencias(db, robo)
    assert o.avisada_em is not None
    # logo em seguida: nada novo, nada mandado
    assert await ouvidoria.avisar_pendentes(db, robo) == {"avisadas": 0}
    assert len(_Fake.envios) == 1


async def test_a_lista_antiga_nao_vale_mais_depois_de_migrar(db):
    """A lista "Avisar" do robô fica congelada como reserva: com o Cadastro
    semeado, quem está só nela não recebe."""
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, "vigia_correios", lista=f"{ID['harry']}, {ID['roma']}")
    await semeado(db, K.OUVIDORIA_VIGIA_CORREIOS, joana)
    await _pendencia(db, "vigia_correios")
    assert (await ouvidoria.avisar_pendentes(db, "vigia_correios"))["avisadas"] == 1
    assert _envios() == [(K.OUVIDORIA_VIGIA_CORREIOS, "cadastro", "logistica", ["TSTJ0006"])]


async def test_sem_semente_segue_a_lista_antiga_exata(db, monkeypatch):
    """Publicado antes de semear: a chave migrada sem semente usa o legado
    exato (a lista do robô, depois o .env do robô, depois o geral)."""
    await _pessoas(db)
    await _robo(db, "vigia_importacao", lista=None)
    monkeypatch.setattr(get_settings(), "vigia_importacao_threema_recipients", ID["harry"])
    await _pendencia(db, "vigia_importacao")
    assert (await ouvidoria.avisar_pendentes(db, "vigia_importacao"))["avisadas"] == 1
    assert _envios() == [(K.OUVIDORIA_VIGIA_IMPORTACAO, "legado", "importacao", [ID["harry"]])]


# ─────────────────────────────────────────────── (b) desligado: acumula


async def test_desligado_nao_manda_nem_carimba_e_ao_religar_sai_tudo_numa_mensagem(db):
    chave, robo = K.OUVIDORIA_VIGIA_CORREIOS, "vigia_correios"
    _admin, joana, _ = await _pessoas(db)
    # a lista antiga tem gente: desligado no Cadastro não cai nela
    await _robo(db, robo, lista=ID["harry"])
    await semeado(db, chave, joana, ativo=False)
    await _pendencia(db, robo, "x:1")
    r = await ouvidoria.avisar_pendentes(db, robo)
    assert r == {"avisadas": 0, "motivo": "envio_desligado"}
    await _pendencia(db, robo, "x:2")
    r = await ouvidoria.avisar_pendentes(db, robo)
    assert r == {"avisadas": 0, "motivo": "envio_desligado"}
    assert _Fake.envios == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    assert all(o.avisada_em is None for o in await _ocorrencias(db, robo))
    # religado: as duas pendências saem numa mensagem só (o `acumula`)
    await _ligar(db, chave, None)
    threema_envios.limpar_cache()
    r = await ouvidoria.avisar_pendentes(db, robo)
    assert r == {"avisadas": 2}
    assert _envios() == [(chave, "cadastro", "logistica", ["TSTJ0006"])]
    assert "2 ocorrências" in _Fake.envios[0]["texto"]
    assert all(o.avisada_em is not None for o in await _ocorrencias(db, robo))


async def test_semente_vazia_com_ativo_nulo_fica_calada(db):
    """Semente rodada com um código que ainda não conhecia a regra (`ativo`
    NULL e nenhuma linha): é o desligado de hoje (lista vazia), não "ligado
    com ninguém" — sem registro nem sino a cada rodada."""
    chave, robo = K.OUVIDORIA_CHAMADO_FRETE_ML, "chamado_frete_ml"
    await _pessoas(db)
    await _robo(db, robo)
    await semeado(db, chave)
    await _pendencia(db, robo)
    r = await ouvidoria.avisar_pendentes(db, robo)
    assert r == {"avisadas": 0, "motivo": "envio_desligado"}
    assert _Fake.envios == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []


# ─────────────────────────────────────────────── (c) ninguém válido


async def test_ninguem_valido_registra_acende_o_sino_e_nao_carimba(db):
    chave, robo = K.OUVIDORIA_ROBO_JURIDICO, "robo_juridico"
    admin, joana, sumido = await _pessoas(db)
    # a lista antiga tem gente: o Cadastro sem ninguém válido NUNCA cai nela
    await _robo(db, robo, lista="TSTZ0009")
    await semeado(db, chave, sumido)
    # sem pendência: nada a dizer — nem registro, nem sino
    assert await ouvidoria.avisar_pendentes(db, robo) == {"avisadas": 0}
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    await _pendencia(db, robo)
    r = await ouvidoria.avisar_pendentes(db, robo)
    assert r == {"avisadas": 0, "motivo": "ninguem_valido"}
    assert _Fake.envios == []
    assert [a.user_id for a in await _sinos(db, chave)] == [admin.id]
    (linha,) = await _registro(db, chave)
    assert (linha.ok, linha.erro, linha.via, linha.destinatario_id, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        None,
        "chamados",
    )
    (o,) = await _ocorrencias(db, robo)
    assert o.avisada_em is None
    # acertada a lista, o tick seguinte avisa a mesma pendência
    await semeado(db, chave, joana)
    threema_envios.limpar_cache()
    assert (await ouvidoria.avisar_pendentes(db, robo))["avisadas"] == 1
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]
    assert len(await _sinos(db, chave)) == 1


@pytest.mark.parametrize("modo", ["silencioso", "desligado"])
async def test_robo_fora_do_ligado_nao_avisa_nem_acende_o_sino(db, modo):
    """O modo do robô é lido antes do Cadastro: o robô silencioso (o
    `chamado_frete_ml` hoje) ou desligado e arquivado (30 e 33) não manda
    nada — nem com ninguém válido no Cadastro."""
    _admin, _joana, sumido = await _pessoas(db)
    for chave in (
        K.OUVIDORIA_CHAMADO_FRETE_ML,
        K.OUVIDORIA_VIGIA_CREDENCIAIS,
        K.OUVIDORIA_VIGIA_MARKETING_COMANDOS,
    ):
        robo = _robo_de(chave)
        await _robo(db, robo, modo=modo, arquivado_em=datetime.now(UTC))
        await semeado(db, chave, sumido)
        await _pendencia(db, robo)
        r = await ouvidoria.avisar_pendentes(db, robo)
        assert r == {"avisadas": 0, "motivo": f"modo_{modo}"}, chave
        assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    assert _Fake.envios == []


async def test_configuracao_antiga_vazia_fica_calada_como_hoje(db, monkeypatch):
    """Emergência (o piso do .env) com a lista antiga vazia: nada sai e nada
    vai para o registro nem para o sino; com gente, manda para ela."""
    chave, robo = K.OUVIDORIA_VIGIA_FLEX, "vigia_flex"
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, robo, lista=None)
    await semeado(db, chave, joana)
    monkeypatch.setattr(get_settings(), "threema_cadastro_ativo", False)
    await _pendencia(db, robo)
    r = await ouvidoria.avisar_pendentes(db, robo)
    assert r == {"avisadas": 0, "motivo": "sem_destinatarios"}
    assert _Fake.envios == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    (o,) = await _ocorrencias(db, robo)
    assert o.avisada_em is None
    await _robo(db, robo, lista=ID["harry"])
    assert (await ouvidoria.avisar_pendentes(db, robo))["avisadas"] == 1
    assert _envios() == [(chave, "legado", "logistica", [ID["harry"]])]


async def test_robo_nao_migrado_segue_a_lista_avisar(db, monkeypatch):
    """O robô cujo aviso não migrou (o Robô da Margem, 34, até a C10b): mesmo
    com linhas no Cadastro, manda para a lista "Avisar" do robô (pelo mesmo
    caminho, configuração antiga exata) e, vazia, fica calado como antes."""
    desmigrar(monkeypatch, MARGEM)
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, "vigia_margem", lista=ID["harry"])
    await semeado(db, MARGEM, joana)
    await _pendencia(db, "vigia_margem")
    assert (await ouvidoria.avisar_pendentes(db, "vigia_margem"))["avisadas"] == 1
    assert _envios() == [(MARGEM, "legado", "margem", [ID["harry"]])]
    await _robo(db, "vigia_margem", lista=None)
    await _pendencia(db, "vigia_margem", "x:2")
    r = await ouvidoria.avisar_pendentes(db, "vigia_margem")
    assert r == {"avisadas": 0, "motivo": "sem_destinatarios"}
    assert await _registro(db, MARGEM) == [] and await _sinos(db, MARGEM) == []


# ─────────────────────────────────────────────── re-aviso e rotas


async def test_reaviso_e_editado_no_cadastro_e_vale_no_aviso(db, client, make_user, auth_as):
    chave, robo = K.OUVIDORIA_VIGIA_FLEX, "vigia_flex"
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, robo, reaviso_horas=24)
    await semeado(db, chave, joana)
    o = await _pendencia(db, robo)
    o.avisada_em = datetime.now(UTC) - timedelta(hours=7)
    await db.commit()
    assert await ouvidoria.avisar_pendentes(db, robo) == {"avisadas": 0}  # dentro das 24 h
    # quem edita a Ouvidoria (não admin) não muda o re-aviso: nem no Cadastro...
    editor = await make_user(permissions=_perms(edit=True))
    auth_as(editor)
    r = await client.patch(f"/api/threema/envios/{chave}", json={"reaviso_horas": 6})
    assert r.status_code == 403, r.text
    # ...nem pela tela dos robôs (ignorado, com aviso)
    r = await client.patch(f"/api/ouvidoria/robos/{robo}", json={"reaviso_horas": 6})
    assert r.status_code == 200, r.text
    assert r.json()["reaviso_horas"] == 24 and len(r.json()["avisos"]) == 1
    # o admin muda no Cadastro: grava no robô e o aviso segue
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.patch(f"/api/threema/envios/{chave}", json={"reaviso_horas": 6})
    assert r.status_code == 200, r.text
    assert r.json()["reaviso_horas"] == 6
    assert (await db.get(OuvidoriaRobo, robo, populate_existing=True)).reaviso_horas == 6
    assert await ouvidoria.avisar_pendentes(db, robo) == {"avisadas": 1}
    (o,) = await _ocorrencias(db, robo)
    assert o.reavisada_em is not None


async def test_patch_dos_robos_ignora_a_lista_e_o_reaviso_com_aviso(
    db, client, make_user, auth_as, monkeypatch
):
    """A aba aberta antes da publicação manda a lista e o re-aviso de sempre
    junto com o resto: grava o resto, sem aviso. Mudar a lista ou o
    re-aviso é ignorado (não 422) com aviso — também para o admin, que muda
    no Cadastro. O modo, a config e a lixeira seguem pela tela dos robôs."""
    robo = "vigia_importacao"
    await _robo(db, robo, lista=ID["harry"], reaviso_horas=24)
    auth_as(await make_user(permissions=_perms(edit=True)))
    r = await client.patch(
        f"/api/ouvidoria/robos/{robo}",
        json={
            "reaviso_horas": 24,
            "threema_recipients": ID["harry"].lower(),
            "config": {"tolerancia_min": 120},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["avisos"] == [] and r.json()["config"]["tolerancia_min"] == 120
    r = await client.patch(
        f"/api/ouvidoria/robos/{robo}",
        json={
            "modo": "silencioso",
            "reaviso_horas": 2,
            "threema_recipients": "",
            "config": {"tolerancia_min": 90},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["modo"] == "silencioso" and body["config"]["tolerancia_min"] == 90
    assert body["reaviso_horas"] == 24 and body["threema_recipients"] == ID["harry"]
    assert len(body["avisos"]) == 2
    assert all("Cadastros › Envios Threema (só admin)" in a for a in body["avisos"])
    # lista inválida também é ignorada (não 422)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.patch(f"/api/ouvidoria/robos/{robo}", json={"threema_recipients": "ABC"})
    assert r.status_code == 200, r.text
    assert len(r.json()["avisos"]) == 1
    row = await db.get(OuvidoriaRobo, robo, populate_existing=True)
    assert (row.threema_recipients, row.reaviso_horas) == (ID["harry"], 24)
    # o robô que não migrou (o 34, até a C10b) segue editável por aqui, e a
    # caixinha "avisar margem alta" também, enquanto o 27 não migrar
    desmigrar(monkeypatch, MARGEM, K.MARGEM_MARGEM_ALTA)
    await _robo(db, "vigia_margem")
    r = await client.patch(
        "/api/ouvidoria/robos/vigia_margem",
        json={
            "threema_recipients": ID["harry"],
            "reaviso_horas": 12,
            "config": {"avisar_margem_alta": True},
        },
    )
    assert r.status_code == 200 and r.json()["avisos"] == []
    assert (r.json()["threema_recipients"], r.json()["reaviso_horas"]) == (ID["harry"], 12)
    assert r.json()["config_no_cadastro"] == {}
    assert r.json()["config"]["avisar_margem_alta"] is True


async def test_caixinha_da_config_que_o_cadastro_governa_fica_so_leitura(
    db, client, make_user, auth_as, monkeypatch
):
    """Quando o envio que liga e desliga pela caixinha do robô migra (o 27,
    "avisar margem alta", na C10b), a tela dos robôs recebe a chave em
    `config_no_cadastro` (só leitura, com o link) e o PATCH ignora a caixinha
    com aviso — o resto da config segue gravando."""
    migrar(monkeypatch, K.MARGEM_MARGEM_ALTA)
    await _robo(db, "vigia_margem", config={"avisar_margem_alta": False, "cadencia_min": 30})
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get("/api/ouvidoria/robos/vigia_margem")
    assert r.json()["config_no_cadastro"] == {"avisar_margem_alta": K.MARGEM_MARGEM_ALTA}
    # a mesma caixinha de sempre (aba velha): grava o resto, sem aviso
    r = await client.patch(
        "/api/ouvidoria/robos/vigia_margem",
        json={"config": {"avisar_margem_alta": False, "cadencia_min": 45}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["avisos"] == [] and r.json()["config"]["cadencia_min"] == 45
    r = await client.patch(
        "/api/ouvidoria/robos/vigia_margem", json={"config": {"avisar_margem_alta": True}}
    )
    assert r.status_code == 200, r.text
    (aviso,) = r.json()["avisos"]
    assert "Cadastros › Envios Threema (só admin)" in aviso
    assert r.json()["config"]["avisar_margem_alta"] is False
    row = await db.get(OuvidoriaRobo, "vigia_margem", populate_existing=True)
    assert row.config["avisar_margem_alta"] is False


async def test_a_tela_dos_robos_mostra_quem_recebe_pelo_cadastro(
    db, client, make_user, auth_as, monkeypatch
):
    # o robô cujo aviso não migrou: o 34 (até a C10b), com a caixinha do 27
    desmigrar(monkeypatch, MARGEM, K.MARGEM_MARGEM_ALTA)
    _admin, joana, sumido = await _pessoas(db)
    await _robo(db, "vigia_importacao", lista=ID["harry"])
    await semeado(db, K.OUVIDORIA_VIGIA_IMPORTACAO, joana, sumido)
    await _robo(db, "vigia_correios", lista=ID["harry"])
    await semeado(db, K.OUVIDORIA_VIGIA_CORREIOS, joana, ativo=False)
    await _robo(db, "vigia_flex", lista=ID["harry"])  # sem semente: a lista antiga
    await _robo(db, "vigia_margem", lista=ID["harry"])
    auth_as(await make_user(permissions=_perms()))
    r = await client.get("/api/ouvidoria/robos")
    assert r.status_code == 200, r.text
    por = {b["chave"]: b for b in r.json()}
    imp = por["vigia_importacao"]
    assert (imp["threema_envio"], imp["threema_migrado"], imp["threema_via"]) == (
        K.OUVIDORIA_VIGIA_IMPORTACAO,
        True,
        "cadastro",
    )
    assert (imp["threema_ativo"], imp["threema_origem"]) == (True, "cadastro")
    assert imp["threema_destinatarios"] == [{"id": "TS…", "nome": "joana c10a", "origem": None}]
    cor = por["vigia_correios"]
    assert (cor["threema_ativo"], cor["threema_destinatarios"]) == (False, [])
    flex = por["vigia_flex"]
    assert (flex["threema_migrado"], flex["threema_via"], flex["threema_origem"]) == (
        True,
        "legado",
        "robo",
    )
    assert [d["id"] for d in flex["threema_destinatarios"]] == [ID["harry"]]
    margem = por["vigia_margem"]
    assert (margem["threema_migrado"], margem["threema_via"], margem["threema_ativo"]) == (
        False,
        None,
        None,
    )
    assert margem["threema_envio"] == MARGEM
    assert por["denuncia_relatorio_threema"]["threema_envio"] is None
    assert all(b["config_no_cadastro"] == {} and b["avisos"] == [] for b in r.json())
    # o ID de quem recebe pelo Cadastro não aparece
    assert "TSTJ0006" not in r.text
    det = (await client.get("/api/ouvidoria/robos/vigia_importacao")).json()
    assert det["threema_destinatarios"] == imp["threema_destinatarios"]


async def test_quem_recebe_leve_abre_para_quem_ve_a_ouvidoria(db, client, make_user, auth_as):
    """O atalho da tela dos robôs: quem vê a Ouvidoria vê os nomes (sem ID),
    não edita no Cadastro; quem não vê a Ouvidoria não vê."""
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, "vigia_nota_cheia")
    await semeado(db, K.OUVIDORIA_VIGIA_NOTA_CHEIA, joana)
    auth_as(await make_user(permissions=_perms(edit=True)))
    r = await client.get(f"/api/threema/envios/{K.OUVIDORIA_VIGIA_NOTA_CHEIA}/quem-recebe")
    assert r.status_code == 200, r.text
    q = r.json()
    assert (q["nomes"], q["ativo"], q["usa_configuracao_antiga"]) == (["joana c10a"], True, False)
    assert "TSTJ0006" not in r.text
    r = await client.patch(
        f"/api/threema/envios/{K.OUVIDORIA_VIGIA_NOTA_CHEIA}",
        json={"destinatarios": [{"tipo": "usuario", "id": str(joana.id)}]},
    )
    assert r.status_code == 403
    auth_as(await make_user(permissions={"chamados": {"view": True}}))
    r = await client.get(f"/api/threema/envios/{K.OUVIDORIA_VIGIA_NOTA_CHEIA}/quem-recebe")
    assert r.status_code == 403


async def test_rodar_agora_segue_para_quem_edita_a_ouvidoria(
    db, client, make_user, auth_as, monkeypatch
):
    """O "Rodar agora" (quem edita a Ouvidoria, não precisa ser admin) roda o
    robô e o aviso sai pelo Cadastro."""
    chave, robo = K.OUVIDORIA_VIGIA_ROBO_LEITURA, "vigia_robo_leitura"
    _admin, joana, _ = await _pessoas(db)
    await _robo(db, robo)
    await semeado(db, chave, joana)

    async def _sweep() -> dict:
        async with db_mod.session_scope() as s:
            await ouvidoria.registrar(s, robo, "x:9", titulo="Leitura parada")
            return await ouvidoria.avisar_pendentes(s, robo)

    monkeypatch.setitem(router_ouvidoria.RUNNERS, robo, lambda: _sweep)
    auth_as(await make_user(permissions=_perms(edit=True)))
    r = await client.post(f"/api/ouvidoria/robos/{robo}/rodar")
    assert r.status_code == 200, r.text
    assert _envios() == [(chave, "cadastro", "devolucoes", ["TSTJ0006"])]


# ─────────────────────────────────────────────── a semente da fatia


@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_robo_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, variante
):
    await montar(db, monkeypatch, variante=variante)
    leg = {c: await threema_envios.legado(c, session=db) for c in MINHAS}
    esperado = {c: list(threema_envios._dedupe(leg[c].ids)) for c in MINHAS}
    await threema_envios.semear(db, fatia="F3", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in MINHAS]
    # a lista antiga fica congelada como reserva, mas o aviso não lê mais:
    # esvaziar não muda quem recebe
    s = get_settings()
    monkeypatch.setattr(s, "ouvidoria_threema_recipients", "")
    monkeypatch.setattr(s, "vigia_importacao_threema_recipients", "")
    for c in MINHAS:
        # o modo é o "depende de": ligado aqui para o Cadastro decidir
        await _robo(db, _robo_de(c), lista=None)
    threema_envios.limpar_cache()
    for c in MINHAS:
        robo = _robo_de(c)
        _Fake.envios = []
        await _pendencia(db, robo)
        r = await ouvidoria.avisar_pendentes(db, robo)
        linha = await db.get(ThreemaEnvio, c, populate_existing=True)
        if esperado[c]:
            assert r == {"avisadas": 1}, c
            assert _envios() == [(c, "cadastro", ouvidoria.ROBOS[robo].contexto, esperado[c])]
            assert linha.ativo is None, c
        else:
            # lista antiga vazia = o desligado de hoje: nasce desligado, calado
            assert r == {"avisadas": 0, "motivo": "envio_desligado"}, c
            assert linha.ativo is False, c
            assert _Fake.envios == [], c
        assert await _registro(db, c) == [] and await _sinos(db, c) == [], c
    if variante == "cheio":
        # o geral (cairo sa) é a reserva de quem não tem lista nem .env
        assert esperado[K.OUVIDORIA_CHAMADO_FRETE_ML] == [ID["cairo_sa"]]
        assert esperado[K.OUVIDORIA_VIGIA_CORREIOS][-1] == ID["roma"]
    if variante == "reservas":
        # sem lista na tela: o .env do robô; sem .env, ninguém (geral vazio)
        assert esperado[K.OUVIDORIA_VIGIA_IMPORTACAO] == [ID["londres"], ID["harry"]]
        assert esperado[K.OUVIDORIA_VIGIA_FLEX] == []
    if variante == "vazias":
        assert not any(esperado.values())


async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(db, monkeypatch):
    """O "cairo sa" suspenso depois da semente: sai de quem recebe, embora a
    lista antiga ainda o tenha; o robô em que ele era o único fica sem
    ninguém válido — sino, sem cair na lista antiga."""
    c = await montar(db, monkeypatch)
    await threema_envios.semear(db, fatia="F3", chaves=MINHAS, aplicar=True)
    await db.commit()
    c.usuarios["cairo_sa"].status = UserStatus.SUSPENDED
    await db.commit()
    threema_envios.limpar_cache()
    await _pendencia(db, "vigia_robo_melhorenvio")
    r = await ouvidoria.avisar_pendentes(db, "vigia_robo_melhorenvio")
    assert r == {"avisadas": 1}
    assert _envios() == [
        (K.OUVIDORIA_VIGIA_ROBO_MELHORENVIO, "cadastro", "logistica", [ID["londres"]])
    ]
    _Fake.envios = []
    await _pendencia(db, "vigia_ingest_bling")
    r = await ouvidoria.avisar_pendentes(db, "vigia_ingest_bling")
    assert r == {"avisadas": 0, "motivo": "ninguem_valido"}
    assert _Fake.envios == []
    leg = await threema_envios.legado(K.OUVIDORIA_VIGIA_INGEST_BLING, session=db)
    assert ID["cairo_sa"] in leg.ids
    sinos = await _sinos(db, K.OUVIDORIA_VIGIA_INGEST_BLING)
    assert [a.user_id for a in sinos] == [c.usuarios["londres"].id]
