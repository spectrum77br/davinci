"""F2/C9e da tarefa 16: o aviso da Conferência quando a rodada fecha — Shopee
(item 45), Mercado Livre (46) e Amazon (47) — passa a ler o Cadastro
(Cadastros › Envios Threema). Cada marketplace é um envio, com o seu
liga/desliga: o `ativo` NULL segue a chave de hoje no .env
(CONFERENCIA_SHOPEE_THREEMA, CONFERENCIA_ML_THREEMA,
CONFERENCIA_AMAZON_THREEMA). A linha `conferencia_shopee` do Informar (a lista
dos três) fica congelada como reserva de emergência e o PUT dela responde 410
— "só ADM altera quem recebe" (decisão do dono, 23/11); o botão já era só de
admin.

Um teste por ponto, PELO CAMINHO REAL (`enviar_pendente`, o varredor do
worker e a rota do executor que fecha a rodada; só o ThreemaClient é falso):

* (a) Cadastro semeado com a linha do Informar VAZIA: manda para quem está no
  Cadastro, com a chave da plataforma e o remetente de hoje (chamados), UM
  destinatário por vez (a falha de rede de um não derruba os outros nem
  esconde quem recebeu: basta um entregue para carimbar);
* (b) desligado: não manda e não carimba; religado em até 6 h o varredor
  manda; rodada mais velha que isso se perde (o `descarta` do catálogo);
* (c) ninguém válido (a única pessoa da lista foi suspensa): não manda nem
  carimba, acende o sino dos admins e grava o registro `ok=false`; acertada a
  lista, a rodada ainda recente sai;
* a configuração antiga vazia (emergência) fica calada, como hoje;
* o portão do varredor do worker é o `ativo` dos três envios.

E a semente da fatia: `semear` + `conferir` das três chaves dá 0 diferenças
e, depois dela, cada plataforma manda EXATAMENTE para quem o legado mandaria.
A lista vazia NÃO nasce desligada aqui: o desligado de hoje é a chave do
.env (ligada com a lista vazia é "ninguém recebe", vermelho e sino).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import worker
from app.config import get_settings
from app.models import (
    ConferenciaShopeeExecucao,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaGrupo,
    UserRole,
    UserStatus,
)
from app.routers import informar as informar_router
from app.routers import marketing_conferencia as mc
from app.services import threema, threema_envios, threema_registro
from app.services.conferencia_shopee import fila
from app.services.conferencia_shopee import threema_aviso as ta
from app.services.threema_catalogo import CATALOGO, G_CONFERENCIA, K
from tests.test_conferencia_shopee_fila import AGORA, dados_loja, semear_contas
from tests.test_conferencia_shopee_threema import API, TOKEN, _pronta
from tests.test_threema_pontos_informar import _linha_informar
from tests.test_threema_pontos_logistica_devolucoes import _Fake, _ligar, _registro, _sinos
from tests.test_threema_resolver import _usuario, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.asyncio

SHOPEE = K.MARKETING_CONFERENCIA_SHOPEE
ML = K.MARKETING_CONFERENCIA_ML
AMAZON = K.MARKETING_CONFERENCIA_AMAZON
MINHAS = [SHOPEE, ML, AMAZON]
PLATAFORMA = {SHOPEE: "shopee", ML: "ml", AMAZON: "amazon"}
# A chave de hoje no .env de cada uma (o `ativo` NULL segue ela).
ENV = {
    SHOPEE: "conferencia_shopee_threema",
    ML: "conferencia_ml_threema",
    AMAZON: "conferencia_amazon_threema",
}
TITULO = {SHOPEE: "Shopee", ML: "Mercado Livre", AMAZON: "Amazon"}


class _FakeConf(_Fake):
    """O ThreemaClient falso da bateria, com o canal que o ponto lê antes de
    mandar (`threema_envios.canal`, o `client.disabled` de antes) e falha de
    rede para os IDs de `falhar`."""

    disabled = False
    canal = "chamados"
    falhar: set[str] = set()

    async def send_to_all(self, texto, destinos):
        if set(destinos) & _FakeConf.falhar:
            raise httpx.ConnectTimeout("gateway fora")
        return await super().send_to_all(texto, destinos)


def _liga_env(monkeypatch, nome: str, valor) -> None:
    """Muda a config dos DOIS lados: o worker guarda o objeto da subida
    (`worker._settings`)."""
    for cfg in {id(c): c for c in (get_settings(), worker._settings)}.values():
        monkeypatch.setattr(cfg, nome, valor)


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    from app.main import app

    if not any(getattr(r, "path", "").startswith(API) for r in app.routes):
        app.include_router(mc.router)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _Fake.envios = []
    _FakeConf.falhar = set()
    threema_envios.limpar_cache()
    threema_registro.reiniciar_disjuntor()
    for nome, valor in (
        ("threema_cadastro_ativo", True),
        ("threema_registro_ativo", True),
        ("app_url", "https://davinci.teste"),
        ("marketing_agent_token", TOKEN),
        ("conferencia_shopee_cron", False),
        # hoje: só a chave da Shopee ligada (as do ML e da Amazon nasceram
        # desligadas)
        ("conferencia_shopee_threema", True),
        ("conferencia_ml_threema", False),
        ("conferencia_amazon_threema", False),
    ):
        _liga_env(monkeypatch, nome, valor)
    monkeypatch.setattr(threema, "ThreemaClient", _FakeConf)
    monkeypatch.setattr(mc, "_agora", lambda: AGORA)
    yield
    threema_envios.limpar_cache()


async def _pessoas(db):
    admin = await _usuario(db, "admin c9e", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c9e", "TSTJ0006")
    sumido = await _usuario(db, "sumido c9e", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, sumido


def _envios() -> list[tuple]:
    return [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios]


async def _carimbo(db, ex) -> datetime | None:
    await db.commit()
    row = await db.get(ConferenciaShopeeExecucao, ex.id, populate_existing=True)
    return row.threema_enviado_em


def test_as_chaves_da_c9e_estao_migradas_com_o_remetente_de_hoje():
    for chave in MINHAS:
        d = CATALOGO[chave]
        assert d.migrado and d.fatia == "F2", chave
        # o remetente de hoje: o ThreemaClient(contexto="chamados") do ponto
        assert d.remetente == "chamados", chave
        assert d.legado.informar == (ta.CONTEXTO,), chave
        assert d.legado.grupo == G_CONFERENCIA, chave
        assert d.legado.interruptor_env == ENV[chave], chave
        assert d.interruptor == "proprio" and d.ao_desligar == "descarta", chave
        assert d.leitura == "informar:conferencia_shopee", chave
        # a lista vazia NÃO é o desligado de hoje: é a chave do .env
        assert not threema_envios._lista_vazia_desliga(d), chave
    assert {ta.chave_envio(p) for p in ("shopee", "ml", "amazon")} == set(MINHAS)
    # rodada antiga (sem plataforma) é da Shopee
    assert ta.chave_envio(None) == SHOPEE


# ─────────────────────────────────────────────── (a) manda pelo Cadastro


@pytest.mark.parametrize("chave", MINHAS)
async def test_com_a_linha_vazia_manda_pelo_cadastro_um_por_vez(db, chave):
    admin, joana, sumido = await _pessoas(db)
    await _linha_informar(db, ta.CONTEXTO, "")
    # a da Shopee segue a chave do .env (ligada); ML e Amazon, com a chave
    # desligada, ligam pelo Cadastro
    await semeado(db, chave, joana, admin, sumido, ativo=None if chave == SHOPEE else True)
    ex = await _pronta(db, plataforma=PLATAFORMA[chave])
    r = await ta.enviar_pendente(db, ex, AGORA)
    await db.commit()
    assert r["enviado"] is True, r
    assert r["sent"] == ["TSTJ0006", "TSTA0001"] and r["failed"] == []
    # um destinatário por envio, com a chave da plataforma e o remetente de hoje
    assert _envios() == [
        (chave, "cadastro", "chamados", ["TSTJ0006"]),
        (chave, "cadastro", "chamados", ["TSTA0001"]),
    ]
    assert _Fake.envios[0]["texto"].startswith(f"📊 Conferência {TITULO[chave]}")
    assert _Fake.envios[0]["texto"] == _Fake.envios[1]["texto"]
    assert await _carimbo(db, ex) == AGORA
    # de novo: não repete
    assert await ta.enviar_pendente(db, ex, AGORA) == {"enviado": False, "motivo": "já enviado"}
    assert len(_Fake.envios) == 2
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []


async def test_falha_de_rede_de_um_nao_derruba_os_outros(db):
    admin, joana, _ = await _pessoas(db)
    await semeado(db, SHOPEE, joana, admin)
    ex = await _pronta(db)
    _FakeConf.falhar = {"TSTJ0006"}
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r["enviado"] is True
    assert (r["sent"], r["failed"]) == (["TSTA0001"], ["TSTJ0006"])
    assert _envios() == [(SHOPEE, "cadastro", "chamados", ["TSTA0001"])]
    # basta um entregue para carimbar: o varredor não reenvia a quem recebeu
    assert await _carimbo(db, ex) == AGORA
    # todos falhando: não carimba (o varredor tenta de novo)
    _Fake.envios = []
    outra = await _pronta(db)
    _FakeConf.falhar = {"TSTJ0006", "TSTA0001"}
    r = await ta.enviar_pendente(db, outra, AGORA)
    assert r == {"enviado": False, "motivo": "envio falhou"}
    assert _Fake.envios == [] and await _carimbo(db, outra) is None


# ─────────────────────────────────────────────── (b) desligado


async def test_desligado_nao_carimba_religado_em_6h_manda_e_depois_se_perde(db):
    _admin, joana, _ = await _pessoas(db)
    # desligado no Cadastro, com a chave do .env ligada: vale o Cadastro
    await semeado(db, SHOPEE, joana, ativo=False)
    agora = datetime.now(UTC)
    recente = await _pronta(db, finalizado=agora - timedelta(hours=1))
    velha = await _pronta(db, finalizado=agora - timedelta(hours=7))
    r = await ta.enviar_pendente(db, recente, agora)
    assert r == {"enviado": False, "motivo": "aviso no Threema desligado"}
    await worker.conferencia_shopee_varrer({})
    assert _Fake.envios == []
    assert await _carimbo(db, recente) is None and await _carimbo(db, velha) is None
    assert await _registro(db, SHOPEE) == [] and await _sinos(db, SHOPEE) == []
    # religado (NULL = a chave do .env, ligada): a rodada recente sai no
    # varredor; a de 7 h atrás passou da janela de reenvio e se perde
    await _ligar(db, SHOPEE, None)
    threema_envios.limpar_cache()
    await worker.conferencia_shopee_varrer({})
    assert _envios() == [(SHOPEE, "cadastro", "chamados", ["TSTJ0006"])]
    assert await _carimbo(db, recente) is not None
    assert await _carimbo(db, velha) is None


async def test_ativo_nulo_segue_a_chave_do_env_e_o_cadastro_vence(db, monkeypatch):
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, ML, joana)  # ativo NULL: a chave CONFERENCIA_ML_THREEMA (desligada)
    ex = await _pronta(db, plataforma="ml")
    assert (await ta.enviar_pendente(db, ex, AGORA))["motivo"] == "aviso no Threema desligado"
    assert not await ta.ligado("ml") and await ta.plataformas_ligadas() == ["shopee"]
    _liga_env(monkeypatch, "conferencia_ml_threema", True)
    assert await ta.plataformas_ligadas() == ["shopee", "ml"]
    # a chave da Shopee desligada no .env, mas ligada no Cadastro: vale o Cadastro
    _liga_env(monkeypatch, "conferencia_shopee_threema", False)
    await semeado(db, SHOPEE, joana, ativo=True)
    assert await ta.plataformas_ligadas() == ["shopee", "ml"]
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r["enviado"] is True
    assert _envios() == [(ML, "cadastro", "chamados", ["TSTJ0006"])]


# ─────────────────────────────────────────────── (c) ninguém válido


async def test_ninguem_valido_registra_sino_e_nao_carimba(db):
    admin, joana, sumido = await _pessoas(db)
    # a lista antiga tem gente: o Cadastro sem ninguém válido NUNCA cai nela
    await _linha_informar(db, ta.CONTEXTO, "TSTZ0009")
    await semeado(db, SHOPEE, sumido)
    ex = await _pronta(db, finalizado=datetime.now(UTC) - timedelta(minutes=30))
    r = await ta.enviar_pendente(db, ex, datetime.now(UTC))
    assert r == {"enviado": False, "motivo": "ninguém válido para receber"}
    assert _Fake.envios == []
    assert await _carimbo(db, ex) is None
    assert [a.user_id for a in await _sinos(db, SHOPEE)] == [admin.id]
    (linha,) = await _registro(db, SHOPEE)
    assert (linha.ok, linha.erro, linha.via, linha.destinatario_id, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        None,
        "chamados",
    )
    # acertada a lista, a volta seguinte do varredor manda (a rodada é recente)
    await db.refresh(joana)
    await semeado(db, SHOPEE, joana)
    await worker.conferencia_shopee_varrer({})
    assert _envios() == [(SHOPEE, "cadastro", "chamados", ["TSTJ0006"])]
    assert await _carimbo(db, ex) is not None
    assert len(await _sinos(db, SHOPEE)) == 1


async def test_configuracao_antiga_vazia_fica_calada_como_hoje(db, monkeypatch):
    """Emergência (o piso do .env) com a lista antiga vazia: nada sai e nada
    vai para o registro nem para o sino — "ninguém cadastrado", sem carimbo,
    como antes da fatia. Com gente na lista antiga, a emergência manda para
    ela (via legado)."""
    _admin, joana, _ = await _pessoas(db)
    for chave in MINHAS:
        await semeado(db, chave, joana)
    await _linha_informar(db, ta.CONTEXTO, "")
    monkeypatch.setattr(get_settings(), "threema_cadastro_ativo", False)
    ex = await _pronta(db)
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r == {"enviado": False, "motivo": "ninguém cadastrado"}
    assert _Fake.envios == []
    assert await _registro(db, SHOPEE) == [] and await _sinos(db, SHOPEE) == []
    assert await _carimbo(db, ex) is None
    await _linha_informar(db, ta.CONTEXTO, ID["harry"])
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r["enviado"] is True
    assert _envios() == [(SHOPEE, "legado", "chamados", [ID["harry"]])]


async def test_canal_de_chamados_desativado_nao_manda(db, monkeypatch):
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, SHOPEE, joana)
    monkeypatch.setattr(_FakeConf, "disabled", True)
    ex = await _pronta(db)
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r == {"enviado": False, "motivo": "Threema de chamados desativado"}
    assert _Fake.envios == [] and await _carimbo(db, ex) is None


# ─────────────────────────────────────────────── worker e rota do executor


async def test_portao_do_varredor_e_o_ativo_dos_tres_envios(db, monkeypatch):
    _admin, joana, _ = await _pessoas(db)
    chamadas: list[int] = []
    original = ta.enviar_pendentes

    async def _conta(session, agora=None):
        chamadas.append(1)
        return await original(session, agora)

    monkeypatch.setattr(ta, "enviar_pendentes", _conta)
    agora = datetime.now(UTC)
    ex_ml = await _pronta(db, plataforma="ml", finalizado=agora - timedelta(minutes=20))
    # os três desligados (Shopee no Cadastro; ML e Amazon pela chave do .env):
    # o varredor nem procura aviso pendente
    await semeado(db, SHOPEE, joana, ativo=False)
    await semeado(db, ML, joana)
    await worker.conferencia_shopee_varrer({})
    assert chamadas == [] and _Fake.envios == []
    # o ML ligado SÓ no Cadastro (a chave do .env segue desligada): o varredor
    # manda o pendente dele
    await _ligar(db, ML, True)
    threema_envios.limpar_cache()
    await worker.conferencia_shopee_varrer({})
    assert chamadas == [1]
    assert _envios() == [(ML, "cadastro", "chamados", ["TSTJ0006"])]
    assert await _carimbo(db, ex_ml) is not None


async def test_ultima_loja_pelo_http_avisa_pelo_cadastro(client, db):
    """A rota do executor que fecha a rodada (o "Gerar agora" de quem edita o
    Marketing termina aqui) manda pelo Cadastro."""
    _admin, joana, _ = await _pessoas(db)
    await _linha_informar(db, ta.CONTEXTO, "")
    await semeado(db, SHOPEE, joana)
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ex = await fila.criar_execucao(db, "semanal", "agenda", None, AGORA)
    await db.commit()
    agente = {"X-Agent-Token": TOKEN}
    job = (await client.post(f"{API}/agent/lease", json={}, headers=agente)).json()["job"]
    r = await client.post(
        f"{API}/agent/coletas/{job['coleta_id']}/resultado",
        json={"status": "ok", "dados": dados_loja(ex.semanas)},
        headers=agente,
    )
    assert r.status_code == 200 and r.json()["execucao_pronta"] is True
    assert _envios() == [(SHOPEE, "cadastro", "chamados", ["TSTJ0006"])]
    assert _Fake.envios[0]["texto"].startswith("📊 Conferência Shopee — 27/09 a 03/10/2026")
    assert await _carimbo(db, ex) == AGORA


# ─────────────────────────────────────────────── as rotas


async def test_put_da_lista_responde_410_e_o_get_mostra_a_antiga(db, client, auth_as, make_user):
    await _linha_informar(db, ta.CONTEXTO, ID["harry"])
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.put(f"/api/informar/{ta.CONTEXTO}", json={"recipients": [ID["joana"]]})
    assert r.status_code == 410, r.text
    detalhe = r.json()["detail"]
    assert detalhe["code"] == "quem_recebe_no_cadastro"
    assert "Cadastros › Envios Threema" in detalhe["message"]
    assert sorted(detalhe["envios"]) == sorted(MINHAS)
    # a lista antiga não mudou (é a reserva de emergência) e o GET a mostra
    r = await client.get(f"/api/informar/{ta.CONTEXTO}")
    assert r.status_code == 200 and r.json()["recipients"] == [ID["harry"]]
    assert informar_router._lista_no_cadastro(ta.CONTEXTO)


async def test_so_admin_ve_e_altera_quem_recebe(db, client, auth_as, make_user):
    """Quem edita o Marketing (gera e mexe nas contas) não vê nem escolhe
    quem recebe — como hoje, o botão é só de admin; o admin escolhe em
    Cadastros › Envios Threema e o aviso segue a lista nova."""
    _admin, joana, _ = await _pessoas(db)
    harry = await _usuario(db, "harry c9e", "TSTH0002")
    for chave in MINHAS:
        await semeado(db, chave, joana)
    editor = await make_user(permissions={"marketing": {"view": True, "edit": True}})
    auth_as(editor)
    r = await client.put(f"/api/informar/{ta.CONTEXTO}", json={"recipients": [ID["harry"]]})
    assert r.status_code == 403, r.text
    for chave in MINHAS:
        r = await client.patch(
            f"/api/threema/envios/{chave}",
            json={"destinatarios": [{"tipo": "usuario", "id": str(harry.id)}]},
        )
        assert r.status_code == 403, (chave, r.text)
        r = await client.get(f"/api/threema/envios/{chave}/quem-recebe")
        assert r.status_code == 403, (chave, r.text)
    # o admin vê só nomes e troca a lista da Shopee
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"/api/threema/envios/{SHOPEE}/quem-recebe")
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["nomes"] == ["joana c9e"] and q["usa_configuracao_antiga"] is False
    assert "TSTJ0006" not in r.text
    r = await client.patch(
        f"/api/threema/envios/{SHOPEE}",
        json={"destinatarios": [{"tipo": "usuario", "id": str(harry.id)}]},
    )
    assert r.status_code == 200, r.text
    threema_envios.limpar_cache()
    ex = await _pronta(db)
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r["enviado"] is True
    assert _envios() == [(SHOPEE, "cadastro", "chamados", ["TSTH0002"])]
    # a do ML continua com a lista dela (e desligada: a chave do .env do ML)
    q = (await client.get(f"/api/threema/envios/{ML}/quem-recebe")).json()
    assert q["ativo"] is False
    linhas_ml = (
        await db.execute(
            select(ThreemaEnvioDestinatario.user_id).where(
                ThreemaEnvioDestinatario.envio_chave == ML
            )
        )
    ).scalars().all()
    assert linhas_ml == [joana.id]


# ─────────────────────────────────────────────── a semente da fatia


async def _no_grupo(db) -> set[str]:
    grupo = (
        await db.execute(select(ThreemaGrupo).where(ThreemaGrupo.nome == G_CONFERENCIA))
    ).scalar_one()
    return set(
        (
            await db.execute(
                select(ThreemaEnvioDestinatario.envio_chave).where(
                    ThreemaEnvioDestinatario.grupo_id == grupo.id
                )
            )
        ).scalars()
    )


@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_plataforma_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, variante
):
    await montar(db, monkeypatch, variante=variante)
    leg = {c: await threema_envios.legado(c, session=db) for c in MINHAS}
    esperado = {c: list(threema_envios._dedupe(leg[c].ids)) for c in MINHAS}
    await threema_envios.semear(db, fatia="F2", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in MINHAS]
    # o liga/desliga segue a chave do .env (NULL) e o grupo junta os três
    for chave in MINHAS:
        assert (await db.get(ThreemaEnvio, chave, populate_existing=True)).ativo is None
    assert await _no_grupo(db) == set(MINHAS)
    # a lista antiga fica congelada como reserva, mas o ponto não lê mais:
    # esvaziar não muda quem recebe
    await _linha_informar(db, ta.CONTEXTO, "")
    threema_envios.limpar_cache()
    for chave in MINHAS:
        _Fake.envios = []
        ex = await _pronta(db, plataforma=PLATAFORMA[chave])
        r = await ta.enviar_pendente(db, ex, AGORA)
        await db.commit()
        if not leg[chave].ativo:
            assert r == {"enviado": False, "motivo": "aviso no Threema desligado"}, chave
            assert _Fake.envios == []
            continue
        assert r["enviado"] is True, (chave, r)
        assert _envios() == [(chave, "cadastro", "chamados", [x]) for x in esperado[chave]]
    if variante == "cheio":
        assert esperado[SHOPEE] == [ID["harry"]] and leg[SHOPEE].ativo
        assert not leg[ML].ativo and not leg[AMAZON].ativo
    if variante == "reservas":
        assert leg[AMAZON].ativo and not leg[SHOPEE].ativo
    if variante == "vazias":
        # lista vazia e as três chaves desligadas: nada sai, nada acende
        assert all(not leg[c].ativo and not esperado[c] for c in MINHAS)


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_ligada_com_a_lista_vazia_acende_o_sino_em_vez_de_calar(db, monkeypatch):
    """Hoje, a chave do .env ligada com a lista vazia não manda e só deixa um
    log. Depois da semente (o mesmo "ninguém"), o aviso que não está saindo
    aparece: registro `ok=false` e sino — a rodada fica sem carimbo para sair
    assim que alguém for posto no grupo."""
    c = await montar(db, monkeypatch, variante="vazias")
    monkeypatch.setattr(get_settings(), "conferencia_shopee_threema", True)
    await threema_envios.semear(db, fatia="F2", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c_, "igual") for c_ in MINHAS]
    ex = await _pronta(db)
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r == {"enviado": False, "motivo": "ninguém válido para receber"}
    assert _Fake.envios == []
    assert [a.user_id for a in await _sinos(db, SHOPEE)] == [c.usuarios["londres"].id]
    assert await _carimbo(db, ex) is None


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(db, monkeypatch):
    """Variante `cheio`: só o harry na lista da Conferência. Suspenso depois da
    semente, a Shopee fica sem ninguém válido (sino), embora a lista antiga
    ainda o tenha — nunca cai nela."""
    c = await montar(db, monkeypatch)
    await threema_envios.semear(db, fatia="F2", chaves=MINHAS, aplicar=True)
    await db.commit()
    c.usuarios["harry"].status = UserStatus.SUSPENDED
    await db.commit()
    threema_envios.limpar_cache()
    ex = await _pronta(db)
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r == {"enviado": False, "motivo": "ninguém válido para receber"}
    assert _Fake.envios == []
    assert ID["harry"] in (await threema_envios.legado(SHOPEE, session=db)).ids
    assert [a.user_id for a in await _sinos(db, SHOPEE)] == [c.usuarios["londres"].id]
