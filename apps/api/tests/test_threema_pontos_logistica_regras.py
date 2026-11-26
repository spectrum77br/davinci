"""F4/C11 da tarefa 16: a Mensagem Threema das regras da aba Status da
Logística (item 1, `logistica.mensagem_regra`) passa a ler o Cadastro
(Cadastros › Envios Threema), com ESCOPO por regra: a lista própria da regra
(o id da regra) ou, sem ela, a lista padrão do envio. A semente copia o 👤 de
cada regra (com ou sem mensagem) e o THREEMA_RECIPIENTS (o grupo "Avisos
gerais"); o 👤 fica congelado como reserva de emergência.

O envio NÃO desliga: o interruptor de cada regra é a própria Mensagem Threema
dela (o `estado_resolvido` não muda). "Só ADM altera quem recebe" (decisão do
dono, 23/11): o POST e o PATCH das regras ignoram o 👤 com aviso, a escolha
avulsa do modal também; o "Enviar" continua para quem tem `logistica:edit`.

Um teste por ponto, PELO CAMINHO REAL (o motor de 5 min, o botão da regra e
o botão da linha; só o ThreemaClient é falso):

* (a) Cadastro semeado com o .env e o 👤 de antes DIFERENTES: manda para a
  lista da regra (ou a padrão), com a chave, o escopo e o remetente de hoje;
* (b) não desliga: `ativo` gravado à mão não para nada; regra sem Mensagem
  não manda (é o liga/desliga);
* (c) ninguém válido: não manda, grava o registro `ok=false` e acende o sino
  (o motor no máximo uma vez por hora, sem carimbo — acertada a lista, a linha
  é avisada na rodada seguinte; os botões respondem 422);
* a configuração antiga vazia (sem semente, sem 👤 e sem .env) fica calada.

E: a junção das regras repetidas só junta regras com o MESMO escopo no
Cadastro (o log leva o escopo); o atalho de quem recebe por regra (só nomes,
porta da Logística); a semente da fatia + `conferir` com 0 diferenças e,
depois dela, cada ponto manda EXATAMENTE para quem o legado mandaria.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from structlog.testing import capture_logs

from app.config import get_settings
from app.models import (
    Logistica,
    LogisticaStatus,
    ThreemaCadastroEstado,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    UserRole,
    UserStatus,
)
from app.services import (
    logistica_bling,
    logistica_match,
    logistica_rules,
    threema,
    threema_envios,
    threema_registro,
)
from app.services.threema_catalogo import CATALOGO, K
from tests.test_denuncia_robo_aviso import RedisFake
from tests.test_threema_pontos_logistica_devolucoes import _registro, _sinos
from tests.test_threema_resolver import _usuario, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.asyncio

CHAVE = K.LOGISTICA_MENSAGEM_REGRA
MELI_A = {"order_status": "paid", "ship_status": "delivered"}
MELI_B = {"order_status": "paid", "ship_status": "shipped"}


class _Fake:
    """ThreemaClient falso, do jeito dos fakes da bateria (`contexto` no
    construtor, `send_to_all(texto, destinos)` sem `**kw`). Guarda o
    remetente, os destinos e a marca do registro (chave, escopo e via)."""

    envios: list[dict] = []

    def __init__(self, *, contexto=None):
        self.contexto = contexto
        # a tela do Cadastro lê a conversa de verdade do remetente
        self.canal = contexto or "geral"
        self.disabled = False

    async def send_to_all(self, texto, destinos):
        marca = threema_registro.marca_atual()
        _Fake.envios.append(
            {
                "chave": marca.chave if marca else None,
                "escopo": marca.escopo if marca else None,
                "via": marca.via if marca else None,
                "contexto": self.contexto,
                "destinos": list(destinos),
                "texto": texto,
            }
        )
        return {"sent": list(destinos), "failed": []}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _Fake.envios = []
    threema_envios.limpar_cache()
    threema_registro.reiniciar_disjuntor()
    s = get_settings()
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    monkeypatch.setattr(s, "threema_registro_ativo", True)
    # o .env VAZIO: a lista padrão só pode vir do Cadastro
    monkeypatch.setattr(s, "threema_recipients", "")
    monkeypatch.setattr(threema, "ThreemaClient", _Fake)
    yield
    threema_envios.limpar_cache()


@pytest.fixture(autouse=True)
def redis_fake(monkeypatch):
    """A marca da hora do "ninguém válido" do motor."""
    r = RedisFake()
    monkeypatch.setattr(logistica_bling, "redis", r)
    return r


def _envios() -> list[tuple]:
    return [(e["chave"], e["escopo"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios]


async def _pessoas(db):
    admin = await _usuario(db, "admin c11", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c11", "TSTJ0006")
    harry = await _usuario(db, "harry c11", "TSTH0002")
    sumido = await _usuario(db, "sumido c11", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, harry, sumido


async def _regra(db, meli: dict | None = None, *, mensagem="Confira o pedido", pessoa=None):
    """Regra de status geral (vale de qualquer plataforma e estado). `pessoa`
    = o 👤 de antes da F4 (congelado; a API não grava mais)."""
    r = LogisticaStatus(
        status_plataforma=logistica_rules.assinatura_pt(meli or MELI_A),
        mensagem_threema=mensagem,
        threema_recipients=pessoa,
    )
    db.add(r)
    await db.commit()
    return r


async def _linha(db, meli: dict | None = None, pedido: str | None = None) -> Logistica:
    pedido = pedido or f"ML{uuid.uuid4().hex[:6]}"
    row = Logistica(
        plataforma="Mercado Livre",
        conta="loja",
        pedido_marketplace=pedido,
        pedido_bling=pedido.removeprefix("ML"),
        meli_status=meli or MELI_A,
        status_bling="Entregue",
    )
    db.add(row)
    await db.commit()
    return row


async def _motor(db) -> dict:
    """O motor da aba Status (a cada 5 min, `logistica_ingest`)."""
    return await logistica_bling.enviar_threema_em_lote(db, None)


async def _carimbo(db, row: Logistica):
    await db.refresh(row)
    return row.threema_enviado_at


async def _operador(make_user, auth_as, *, edit=True):
    """Não-admin com a Logística (churchill, israel, londres, cairo sa e
    marrocos em produção): perde a edição do 👤 e continua com o envio."""
    u = await make_user(role=UserRole.USER, permissions={"logistica": {"view": True, "edit": edit}})
    auth_as(u)
    return u


def test_a_chave_da_c11_esta_migrada_sem_interruptor_e_com_escopo_por_regra():
    d = CATALOGO[CHAVE]
    assert d.migrado and d.fatia == "F4"
    assert d.remetente == "logistica"  # o `contexto` que os 3 pontos passavam
    assert d.interruptor == "fixo" and d.ao_desligar == "—"
    assert d.escopo_tipo == "regra_status"
    assert d.leitura == "perm:logistica:view"


# ───────────────────────────────────────────── (a) pela lista do Cadastro


async def test_motor_manda_pela_lista_da_regra_ou_pela_padrao(db):
    """O motor lê quem recebe cada regra (escopo = o id da regra): a regra com
    lista própria vai para ela; a sem lista, para a padrão. O 👤 de antes
    (outra pessoa) não é lido. Carimba as linhas que saíram."""
    _admin, joana, harry, _sumido = await _pessoas(db)
    propria = await _regra(db, MELI_A, pessoa=ID["heisenberg"])
    padrao = await _regra(db, MELI_B)
    await semeado(db, CHAVE, harry)  # a padrão
    await semeado(db, CHAVE, joana, regra=propria.id)
    la = await _linha(db, MELI_A, "ML701")
    lb = await _linha(db, MELI_B, "ML702")
    out = await _motor(db)
    assert out == {"enviados": 2, "falhas": 0}
    assert sorted(_envios(), key=lambda e: e[4]) == sorted(
        [
            (CHAVE, str(propria.id), "cadastro", "logistica", ["TSTJ0006"]),
            (CHAVE, str(padrao.id), "cadastro", "logistica", ["TSTH0002"]),
        ],
        key=lambda e: e[4],
    )
    textos = {e["destinos"][0]: e["texto"] for e in _Fake.envios}
    assert textos["TSTJ0006"] == "Pedido ML701 | Loja Mercado Livre\nConfira o pedido"
    assert await _carimbo(db, la) and await _carimbo(db, lb)
    # carimbada, a linha não volta
    _Fake.envios = []
    assert await _motor(db) == {"enviados": 0, "falhas": 0}
    assert _Fake.envios == []


async def test_botao_da_regra_manda_pela_lista_da_regra_para_quem_tem_edit(
    db, client, make_user, auth_as
):
    """O "Enviar" da regra continua para o não-admin com `logistica:edit`: vai
    para a lista da regra no Cadastro. A escolha avulsa de uma aba velha é
    ignorada, com aviso — a mensagem vai para a lista da regra."""
    _admin, joana, harry, _sumido = await _pessoas(db)
    regra = await _regra(db, pessoa=ID["heisenberg"])
    await semeado(db, CHAVE, harry)
    await semeado(db, CHAVE, joana, regra=regra.id)
    await _operador(make_user, auth_as)
    url = f"/api/logistica/status/{regra.id}/enviar-threema"
    r = await client.post(url, json={"pedido": "701-9", "loja": "Amazon"})
    assert r.status_code == 200, r.text
    assert r.json() == {"sent": ["TSTJ0006"], "failed": [], "avisos": []}
    assert _envios() == [(CHAVE, str(regra.id), "cadastro", "logistica", ["TSTJ0006"])]
    assert _Fake.envios[0]["texto"] == "Pedido 701-9 | Loja Amazon\nConfira o pedido"
    _Fake.envios = []
    r = await client.post(url, json={"recipients": ["TSTH0002", ID["heisenberg"]]})
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == ["TSTJ0006"]
    assert "Cadastros › Envios Threema" in r.json()["avisos"][0]
    assert _envios() == [(CHAVE, str(regra.id), "cadastro", "logistica", ["TSTJ0006"])]
    # só quem vê a Logística: o botão segue exigindo `logistica:edit`
    await _operador(make_user, auth_as, edit=False)
    assert (await client.post(url)).status_code == 403


async def test_aba_velha_segue_enviando_pela_rota_do_seletor_de_antes(
    db, client, make_user, auth_as, monkeypatch
):
    """A aba aberta antes da F4 (revisão F4): o modal velho chama GET
    /api/logistica/threema/destinatarios ao clicar em Enviar. Sem a rota
    (404), regra sem 👤 ficava com "Nenhum destinatário configurado" e o
    Enviar travado. A rota fica, só com o item do Cadastro (sem ID do
    Threema, nem com o .env cheio): o modal velho o marca, o POST leva
    `recipients` e a mensagem vai para quem recebe a regra no Cadastro, com
    aviso. O "Salvar" do 👤 velho é ignorado com aviso."""
    _admin, joana, harry, _sumido = await _pessoas(db)
    sem_pessoa = await _regra(db, MELI_A)
    com_pessoa = await _regra(db, MELI_B, pessoa=ID["heisenberg"])
    await semeado(db, CHAVE, harry)
    await semeado(db, CHAVE, joana, regra=com_pessoa.id)
    # o .env de antes cheio: a rota NÃO o devolve
    monkeypatch.setattr(get_settings(), "threema_recipients", "TSTL0001,TSTE0003")
    monkeypatch.setattr(get_settings(), "threema_recipient_names", "TSTL0001:londres")
    seletor = "/api/logistica/threema/destinatarios"
    auth_as(await make_user(role=UserRole.USER, permissions={}))
    assert (await client.get(seletor)).status_code == 403
    await _operador(make_user, auth_as, edit=False)  # a porta de antes: ver a Logística
    r = await client.get(seletor)
    assert r.status_code == 200, r.text
    itens = r.json()
    assert len(itens) == 1 and "Cadastros › Envios Threema" in itens[0]["nome"]
    for rid in ("TSTL0001", "TSTE0003", "TSTJ0006", "TSTH0002", "londres"):
        assert rid not in r.text
    assert not threema.threema_id_valido(itens[0]["id"]), "não é um ID do Threema"
    # o que o modal velho faz com ela (pages/logistica.vue de bda84917):
    # regra sem 👤 → marca o item; regra com 👤 → marca o 👤 salvo
    await _operador(make_user, auth_as)
    for regra, marcados, quem in (
        (sem_pessoa, [itens[0]["id"]], ["TSTH0002"]),
        (com_pessoa, [ID["heisenberg"]], ["TSTJ0006"]),
    ):
        _Fake.envios = []
        r = await client.post(
            f"/api/logistica/status/{regra.id}/enviar-threema", json={"recipients": marcados}
        )
        assert r.status_code == 200, r.text
        assert r.json()["sent"] == quem and r.json()["failed"] == []
        assert "Cadastros › Envios Threema" in r.json()["avisos"][0]
        assert _envios() == [(CHAVE, str(regra.id), "cadastro", "logistica", quem)]
    # o "Salvar" do 👤 velho com o item marcado: ignorado, com aviso
    r = await client.patch(
        f"/api/logistica/status/{sem_pessoa.id}", json={"threema_recipients": itens[0]["id"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["threema_recipients"] is None and r.json()["avisos"]


async def test_botao_da_linha_manda_pela_regra_casada_e_carimba(db, client, make_user, auth_as):
    _admin, joana, harry, _sumido = await _pessoas(db)
    regra = await _regra(db)
    await semeado(db, CHAVE, harry)
    linha = await _linha(db, pedido="ML703")
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(f"/api/logistica/{linha.id}/enviar-threema")
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == ["TSTH0002"]
    assert _envios() == [(CHAVE, str(regra.id), "cadastro", "logistica", ["TSTH0002"])]
    assert _Fake.envios[0]["texto"].startswith("Pedido ML703 | Loja Mercado Livre\n")
    assert await _carimbo(db, linha)
    # a linha resolve (a Mensagem Threema deixa de contar como pendência)
    assert logistica_match.estado_resolvido([regra], "Entregue", threema_enviado=True)
    # a lista própria da regra vale também aqui
    await semeado(db, CHAVE, joana, regra=regra.id)
    threema_envios.limpar_cache()
    _Fake.envios = []
    outra = await _linha(db, pedido="ML704")
    r = await client.post(f"/api/logistica/{outra.id}/enviar-threema", json={"recipients": []})
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == ["TSTJ0006"] and r.json()["avisos"]


# ───────────────────────────────────────────── (b) não desliga


async def test_nao_desliga_o_interruptor_e_a_mensagem_da_regra(db, client, make_user, auth_as):
    """O item 1 não tem liga/desliga no Cadastro (422 `nao_desligavel`); um
    `ativo=false` gravado à mão não para nada. O liga/desliga de cada regra
    é a Mensagem Threema dela: sem mensagem, nada sai e a linha não fica
    pendente por isso."""
    _admin, _joana, harry, _sumido = await _pessoas(db)
    com = await _regra(db, MELI_A)
    sem = await _regra(db, MELI_B, mensagem=None)
    await semeado(db, CHAVE, harry)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.patch(f"/api/threema/envios/{CHAVE}", json={"ativo": False})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "nao_desligavel"
    linha = await db.get(ThreemaEnvio, CHAVE, populate_existing=True)
    linha.ativo = False
    await db.commit()
    threema_envios.limpar_cache()
    la = await _linha(db, MELI_A)
    lb = await _linha(db, MELI_B)
    assert await _motor(db) == {"enviados": 1, "falhas": 0}
    assert _envios() == [(CHAVE, str(com.id), "cadastro", "logistica", ["TSTH0002"])]
    assert await _carimbo(db, la) and not await _carimbo(db, lb)
    r = await client.post(f"/api/logistica/status/{sem.id}/enviar-threema")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "logistica_sem_mensagem_threema"
    assert logistica_match.estado_resolvido([sem], "Entregue") is True


# ───────────────────────────────────────────── (c) ninguém válido


async def test_motor_ninguem_valido_registra_uma_vez_por_hora_sem_carimbo(db, redis_fake):
    """A padrão só com quem foi suspenso: o motor não manda, a 1ª rodada da
    hora grava o registro `ok=false` (com o escopo da regra) e acende o sino;
    as rodadas seguintes da hora não repetem. Sem carimbo: acertada a lista,
    a rodada seguinte avisa a mesma linha."""
    admin, joana, _harry, sumido = await _pessoas(db)
    regra = await _regra(db, pessoa=ID["heisenberg"])
    await semeado(db, CHAVE, sumido)
    linha = await _linha(db)
    for _ in range(3):  # três rodadas dentro da mesma hora
        assert await _motor(db) == {"enviados": 0, "falhas": 1}
    assert _Fake.envios == []
    (reg,) = await _registro(db, CHAVE)
    assert not reg.ok and reg.erro == "sem_destinatario_valido"
    assert reg.escopo == str(regra.id) and reg.contexto == "logistica"
    assert [(a.user_id, a.escopo) for a in await _sinos(db, CHAVE)] == [(None, "adm")]
    assert not await _carimbo(db, linha)
    # vencida a marca da hora, a tentativa seguinte volta ao registro
    redis_fake.d.clear()
    await _motor(db)
    assert len(await _registro(db, CHAVE)) == 2
    # a lista da regra só com quem não vale: cai na padrão (também sem ninguém)
    await semeado(db, CHAVE, sumido, regra=regra.id)
    threema_envios.limpar_cache()
    redis_fake.d.clear()
    assert await _motor(db) == {"enviados": 0, "falhas": 1}
    assert _Fake.envios == []
    # acertada a padrão, a regra (sem ninguém válido na dela) usa a padrão
    await semeado(db, CHAVE, joana)
    threema_envios.limpar_cache()
    assert await _motor(db) == {"enviados": 1, "falhas": 0}
    assert _envios() == [(CHAVE, str(regra.id), "cadastro", "logistica", ["TSTJ0006"])]
    assert await _carimbo(db, linha)


async def test_motor_ninguem_valido_com_redis_fora_nao_grava_a_cada_rodada(db, monkeypatch):
    class _RedisFora:
        async def set(self, *a, **k):
            raise ConnectionError("redis fora")

    monkeypatch.setattr(logistica_bling, "redis", _RedisFora())
    _admin, joana, _harry, sumido = await _pessoas(db)
    await _regra(db)
    await semeado(db, CHAVE, sumido)
    linha = await _linha(db)
    assert await _motor(db) == {"enviados": 0, "falhas": 1}
    assert await _registro(db, CHAVE) == [] and await _sinos(db, CHAVE) == []
    assert not await _carimbo(db, linha)
    # com gente válida, sai normalmente (não depende do Redis)
    await semeado(db, CHAVE, joana)
    threema_envios.limpar_cache()
    assert await _motor(db) == {"enviados": 1, "falhas": 0}


async def test_botoes_ninguem_valido_registram_acendem_o_sino_e_recusam(
    db, client, make_user, auth_as
):
    admin, _joana, _harry, sumido = await _pessoas(db)
    regra = await _regra(db)
    await semeado(db, CHAVE, sumido)
    linha = await _linha(db)
    await _operador(make_user, auth_as)
    r = await client.post(f"/api/logistica/status/{regra.id}/enviar-threema")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "sem_destinatarios"
    assert "Cadastros › Envios Threema" in r.json()["detail"]["message"]
    auth_as(admin)
    r = await client.post(f"/api/logistica/{linha.id}/enviar-threema")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_destinatarios"
    assert _Fake.envios == []
    regs = await _registro(db, CHAVE)
    assert len(regs) == 2 and not any(x.ok for x in regs)
    assert {x.escopo for x in regs} == {str(regra.id)}
    assert [(a.user_id, a.escopo) for a in await _sinos(db, CHAVE)] == [(None, "adm")]
    assert not await _carimbo(db, linha)


async def test_configuracao_antiga_vazia_fica_calada(db, client, make_user, auth_as, redis_fake):
    """Sem semente (o código da F4 publicado antes do `semear`), sem 👤 e com
    o THREEMA_RECIPIENTS vazio: hoje nada sai (o `send_to_all` recusava sem
    alvos). O motor fica calado — sem registro, sino nem carimbo, e sem gastar
    a marca da hora —; o botão recusa (422)."""
    await _pessoas(db)
    regra = await _regra(db)
    linha = await _linha(db)
    assert await _motor(db) == {"enviados": 0, "falhas": 0}
    assert redis_fake.d == {}
    assert await _registro(db, CHAVE) == [] and await _sinos(db, CHAVE) == []
    assert not await _carimbo(db, linha)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(f"/api/logistica/status/{regra.id}/enviar-threema")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_destinatarios"
    assert "configuração antiga" in r.json()["detail"]["message"]
    assert _Fake.envios == [] and await _sinos(db, CHAVE) == []


async def test_emergencia_volta_ao_pessoa_congelado(db, monkeypatch):
    """A emergência (Cadastros › Envios Threema): todos os envios voltam à
    configuração antiga — aqui, o 👤 congelado da regra e o .env."""
    _admin, joana, harry, _sumido = await _pessoas(db)
    com = await _regra(db, MELI_A, pessoa=ID["heisenberg"])
    await _regra(db, MELI_B)
    await semeado(db, CHAVE, harry)
    await semeado(db, CHAVE, joana, regra=com.id)
    monkeypatch.setattr(get_settings(), "threema_recipients", "TSTL0001")  # o .env de antes
    db.add(ThreemaCadastroEstado(id=1, usar_legado=True))
    await db.commit()
    await _linha(db, MELI_A)
    await _linha(db, MELI_B)
    assert await _motor(db) == {"enviados": 2, "falhas": 0}
    assert sorted(e["destinos"][0] for e in _Fake.envios) == [ID["heisenberg"], "TSTL0001"]
    assert {e["via"] for e in _Fake.envios} == {"legado"}


# ───────────────────────────────────────────── rotas


async def test_editores_antigos_ignoram_o_pessoa_e_so_admin_edita_quem_recebe(
    db, client, make_user, auth_as
):
    """O POST e o PATCH das regras ignoram o 👤 com aviso (não 422), também
    para o admin (quem recebe só se escolhe em Cadastros › Envios Threema); o
    não-admin com `logistica:edit` não edita a lista da regra lá (403)."""
    _admin, joana, _harry, _sumido = await _pessoas(db)
    regra = await _regra(db, pessoa=ID["heisenberg"])
    await semeado(db, CHAVE, joana)
    await _operador(make_user, auth_as)
    r = await client.patch(
        f"/api/logistica/status/{regra.id}", json={"threema_recipients": "TSTJ0006"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["threema_recipients"] == ID["heisenberg"] and r.json()["avisos"]
    r = await client.put(
        f"/api/threema/envios/{CHAVE}/regras/{regra.id}",
        json={"destinatarios": [{"tipo": "usuario", "id": str(joana.id)}]},
    )
    assert r.status_code == 403
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(
        "/api/logistica/status",
        json={"status_plataforma": "z", "mensagem_threema": "oi", "threema_recipients": "TSTJ0006"},
    )
    assert r.status_code == 201 and r.json()["threema_recipients"] is None
    assert r.json()["avisos"]
    # pelo Cadastro, o admin dá uma lista própria à regra
    r = await client.put(
        f"/api/threema/envios/{CHAVE}/regras/{regra.id}",
        json={"destinatarios": [{"tipo": "usuario", "id": str(joana.id)}]},
    )
    assert r.status_code == 200, r.text
    linhas = (
        (
            await db.execute(
                select(ThreemaEnvioDestinatario).where(
                    ThreemaEnvioDestinatario.logistica_status_id == regra.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [x.user_id for x in linhas] == [joana.id]


async def test_quem_recebe_por_regra_so_nomes_com_a_porta_da_logistica(
    db, client, make_user, auth_as
):
    _admin, joana, harry, sumido = await _pessoas(db)
    propria = await _regra(db, MELI_A, pessoa=ID["heisenberg"])
    padrao = await _regra(db, MELI_B)
    so_pessoa = await _regra(db, {"order_status": "cancelled"}, mensagem=None)
    sem_nada = await _regra(db, {"order_status": "paid"}, mensagem=None)
    torta = await _regra(db, {"order_status": "x"})
    await semeado(db, CHAVE, harry)
    await semeado(db, CHAVE, joana, regra=propria.id)
    await semeado(db, CHAVE, joana, regra=so_pessoa.id)
    await semeado(db, CHAVE, sumido, regra=torta.id)
    url = f"/api/threema/envios/{CHAVE}/quem-recebe/regras"
    auth_as(await make_user(role=UserRole.USER, permissions={}))
    assert (await client.get(url)).status_code == 403
    await _operador(make_user, auth_as, edit=False)
    r = await client.get(url)
    assert r.status_code == 200, r.text
    for rid in (*ID.values(), "TSTJ0006", "TSTH0002", "TSTS0009"):
        assert rid not in r.text
    q = r.json()
    assert q["via"] == "cadastro" and q["usa_configuracao_antiga"] is False
    assert q["padrao"]["nomes"] == ["harry c11"]
    regras = q["regras"]
    assert set(regras) == {str(x.id) for x in (propria, padrao, so_pessoa, torta)}
    assert str(sem_nada.id) not in regras
    assert regras[str(propria.id)] == {
        "propria": True,
        "lista_da_regra_sem_ninguem": False,
        "antiga": False,
        "ativo": True,
        "nomes": ["joana c11"],
        "ninguem": False,
        "avisos": [],
    }
    assert regras[str(padrao.id)]["propria"] is False
    assert regras[str(padrao.id)]["lista_da_regra_sem_ninguem"] is False
    assert regras[str(padrao.id)]["nomes"] == ["harry c11"]
    # lista própria sem ninguém válido: quem recebe é a PADRÃO, e a tela diz
    # "lista padrão" (não "lista própria da regra"), com o aviso (revisão F4)
    assert regras[str(torta.id)]["nomes"] == ["harry c11"]
    assert regras[str(torta.id)]["propria"] is False
    assert regras[str(torta.id)]["lista_da_regra_sem_ninguem"] is True
    assert any("vale a lista padrão" in a for a in regras[str(torta.id)]["avisos"])
    # e é o mesmo do envio de verdade: vai para a padrão
    res = await threema_envios.resolver(CHAVE, escopo=torta.id)
    assert res.ids == ("TSTH0002",) and res.caiu_na_padrao is True
    assert (await threema_envios.resolver(CHAVE, escopo=propria.id)).caiu_na_padrao is False
    # sem semente (ou na emergência): o 👤 da regra e o .env
    await db.execute(
        update(ThreemaEnvio).where(ThreemaEnvio.chave == CHAVE).values(semeado_em=None)
    )
    await db.commit()
    threema_envios.limpar_cache()
    q = (await client.get(url)).json()
    assert q["usa_configuracao_antiga"] is True
    assert q["regras"][str(propria.id)]["propria"] is True
    assert q["regras"][str(propria.id)]["antiga"] is True  # a tela diz "👤 de antes"
    assert q["regras"][str(propria.id)]["nomes"] == ["Sem nome (TS…)"]  # o 👤 de antes
    assert q["padrao"]["antiga"] is True
    # a regra sem mensagem cuja lista só existe no Cadastro some (o 👤 dela é vazio)
    assert str(so_pessoa.id) not in q["regras"]
    assert q["regras"][str(torta.id)] == {
        "propria": False,
        "lista_da_regra_sem_ninguem": False,
        "antiga": True,
        "ativo": True,
        "nomes": [],
        "ninguem": True,
        "avisos": [],
    }
    assert q["padrao"]["ninguem"] is True
    # envio sem regras
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"/api/threema/envios/{K.LOGISTICA_INFORMAR}/quem-recebe/regras")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_regras"


# ───────────────────────────────────────────── a junção das regras repetidas


async def _duas_iguais(db, **kw) -> tuple[LogisticaStatus, LogisticaStatus]:
    chave = "Pago | Pronto p/ envio | Aguardando NF"
    a = LogisticaStatus(
        plataforma="Mercado Livre",
        status_plataforma=chave,
        status_atual="Em aberto",
        mensagem_threema="avisar",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        **kw,
    )
    b = LogisticaStatus(
        plataforma="Mercado Livre",
        status_plataforma=chave,
        status_atual="Em andamento",
        mensagem_threema="avisar",
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
        **kw,
    )
    db.add_all([a, b])
    await db.commit()
    return a, b


async def test_juntar_so_junta_regras_com_o_mesmo_escopo_e_o_log_leva_o_escopo(
    db, client, make_user, auth_as
):
    _admin, joana, harry, _sumido = await _pessoas(db)
    a, b = await _duas_iguais(db)
    auth_as(await make_user(role=UserRole.ADMIN))
    # sem semente: compara o 👤 (iguais, os dois sem) → juntam
    assert len((await client.get("/api/logistica/status/repetidas")).json()["grupos"]) == 1
    await semeado(db, CHAVE, harry)
    await semeado(db, CHAVE, joana, regra=a.id)
    # com semente: escopos diferentes (a tem lista própria, b usa a padrão)
    assert (await client.get("/api/logistica/status/repetidas")).json()["grupos"] == []
    await semeado(db, CHAVE, joana, regra=b.id)
    prev = (await client.get("/api/logistica/status/repetidas")).json()
    (g,) = prev["grupos"]
    assert {g["manter_id"], *g["apagar_ids"]} == {str(a.id), str(b.id)}
    with capture_logs() as logs:
        r = await client.post(
            "/api/logistica/status/juntar",
            json={"grupos": [{"manter_id": g["manter_id"], "apagar_ids": g["apagar_ids"]}]},
        )
    assert r.json() == {"juntados": 1, "linhas_apagadas": 1, "pulados": 0}
    (log,) = [x for x in logs if x["event"] == "logistica_status_juntada"]
    esperado = [f"usuario:{joana.id}"]
    assert log["escopo_threema"] == esperado
    assert [x["escopo_threema"] for x in log["apagadas"]] == [esperado]
    # a apagada leva junto a lista própria dela; a que ficou mantém a sua
    restantes = (
        (
            await db.execute(
                select(ThreemaEnvioDestinatario.logistica_status_id).where(
                    ThreemaEnvioDestinatario.logistica_status_id.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    assert restantes == [a.id]


async def test_juntar_com_o_mesmo_escopo_nao_olha_o_pessoa_congelado(db):
    """Depois da semente o 👤 não decide mais quem recebe: duas regras com o
    mesmo escopo no Cadastro juntam mesmo com o 👤 congelado diferente; antes
    dela, o 👤 separa (como hoje)."""
    _admin, joana, harry, _sumido = await _pessoas(db)
    a, b = await _duas_iguais(db)
    await db.execute(
        update(LogisticaStatus)
        .where(LogisticaStatus.id == a.id)
        .values(threema_recipients="TSTX0001")
    )
    await db.commit()
    rows = [a, b]
    await db.refresh(a)
    escopos = await threema_envios.escopo_das_regras(db, CHAVE)
    assert escopos is None
    assert logistica_match.regras_repetidas(rows, escopos=escopos) == ([], [])
    await semeado(db, CHAVE, harry)
    escopos = await threema_envios.escopo_das_regras(db, CHAVE)
    assert escopos == {}
    assert len(logistica_match.regras_repetidas(rows, escopos=escopos)[0]) == 1


# ───────────────────────────────────────────── a semente da fatia


@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_ponto_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, client, make_user, auth_as, variante
):
    c = await montar(db, monkeypatch, variante=variante)
    s = get_settings()
    # mais duas regras do cenário, com a chave de verdade de um pedido do ML
    # (para o motor e o botão da linha): uma com 👤 e uma sem
    com_meli = await _regra(db, MELI_A, pessoa=f"{ID['joana']}, {ID['roma']}, {ID['joana']}")
    sem_meli = await _regra(db, MELI_B)
    regras = {**c.regras, "com_meli": com_meli, "sem_meli": sem_meli}
    esperado = {
        n: list(
            threema_envios._dedupe(
                (await threema_envios.legado(CHAVE, escopo=r.id, session=db)).ids
            )
        )
        for n, r in regras.items()
    }
    await threema_envios.semear(db, fatia="F4", aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, fatia="F4")
    assert {(x.escopo, x.situacao) for x in itens} == {
        (None, "igual"),
        (str(c.regras["com_pessoa"].id), "igual"),
        (str(c.regras["pessoa_sem_mensagem"].id), "igual"),
        (str(com_meli.id), "igual"),
    }
    await db.commit()  # o `conferir` sincroniza o catálogo (trava as linhas até o commit)
    saida: list[str] = []
    assert await threema_envios.executar(["conferir", "--fatia", "F4"], saida.append) == 0
    # o 👤 e o .env ficam congelados como reserva, mas o ponto não lê mais:
    # esvaziar não muda quem recebe
    monkeypatch.setattr(s, "threema_recipients", "")
    await db.execute(update(LogisticaStatus).values(threema_recipients=None))
    await db.commit()
    threema_envios.limpar_cache()
    auth_as(await make_user(role=UserRole.ADMIN))
    # o botão da regra
    for nome in ("com_pessoa", "sem_pessoa", "com_meli", "sem_meli"):
        _Fake.envios = []
        r = await client.post(f"/api/logistica/status/{regras[nome].id}/enviar-threema")
        if esperado[nome]:
            assert r.status_code == 200, (nome, r.text)
            assert _envios() == [
                (CHAVE, str(regras[nome].id), "cadastro", "logistica", esperado[nome])
            ], nome
        else:
            assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_destinatarios"
            assert _Fake.envios == [], nome
    # o motor e o botão da linha
    la = await _linha(db, MELI_A)
    lb = await _linha(db, MELI_B)
    _Fake.envios = []
    await _motor(db)
    assert sorted(_envios(), key=lambda e: e[1]) == sorted(
        [
            (CHAVE, str(r.id), "cadastro", "logistica", esperado[n])
            for n, r in (("com_meli", com_meli), ("sem_meli", sem_meli))
            if esperado[n]
        ],
        key=lambda e: e[1],
    )
    assert bool(await _carimbo(db, la)) == bool(esperado["com_meli"])
    assert bool(await _carimbo(db, lb)) == bool(esperado["sem_meli"])
    # dedupe: o 👤 repetido vira uma linha só (sai uma vez para cada um)
    assert esperado["com_meli"] == [ID["joana"], ID["roma"]]
    if variante != "vazias":
        assert esperado["sem_pessoa"] == [ID["churchill"], ID["londres"], ID["cairo_sa"]]
    else:
        assert esperado["sem_pessoa"] == []


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(
    db, monkeypatch, client, make_user, auth_as
):
    """O "cairo sa" suspenso depois da semente sai da lista padrão (o grupo
    "Avisos gerais"), embora o .env de antes ainda o tenha."""
    c = await montar(db, monkeypatch)
    await threema_envios.semear(db, fatia="F4", aplicar=True)
    await db.commit()
    c.usuarios["cairo_sa"].status = UserStatus.SUSPENDED
    await db.commit()
    threema_envios.limpar_cache()
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(f"/api/logistica/status/{c.regras['sem_pessoa'].id}/enviar-threema")
    assert r.status_code == 200, r.text
    assert _Fake.envios[0]["destinos"] == [ID["churchill"], ID["londres"]]
    leg = await threema_envios.legado(CHAVE, session=db)
    assert ID["cairo_sa"] in leg.ids
