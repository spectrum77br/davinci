"""Cadastros › Envios Threema — leitura (tarefa 16, C6).

`/api/threema`:

* `GET /envios` (só admin): o catálogo inteiro com quem recebe (só nomes,
  nunca o ID), a situação, as faixas, do que depende e o último lote. Nesta
  fase nenhum envio está migrado: quem recebe é a configuração antiga, com a
  faixa âmbar e o lugar onde ela é editada hoje. Filtros: painel, onde roda,
  situação, busca e pessoa;
* `GET /envios/{chave}` (detalhe, com o "Antes" e as regras do item 1),
  `/registros` (lotes, ID mascarado), `/pessoas` e `/pessoas/{id}/envios`;
* `GET /envios/{chave}/quem-recebe`: só nomes, com a porta do catálogo
  (o gerente vê a Margem; quem não pode, 403);
* as rotas dos robôs do Mac, com o token de agente (401 sem ele): a lista de
  cada envio do Mac (`fonte: "vazio"` = o Mac usa o .env) e a gravação do
  registro (chave desconhecida vai com `envio_chave` vazio).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.config import get_settings
from app.historico import nomes
from app.models import (
    ThreemaCadastroEstado,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaEnvioRegistro,
    UserRole,
)
from app.services import ouvidoria, threema_envios
from app.services.threema_catalogo import CATALOGO, ENVIOS, K
from tests.test_threema_resolver import desmigrar, migrar, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.usefixtures("limpar_ouvidoria")

_TOKEN = "tok-threema-teste"  # noqa: S105 — token falso do teste


@pytest.fixture(autouse=True)
def _cache():
    threema_envios.limpar_cache()
    yield
    threema_envios.limpar_cache()


def _sem_id(resposta) -> None:
    """Nenhum ID do Threema inteiro sai para a tela."""
    texto = json.dumps(resposta.json(), ensure_ascii=False)
    for quem, rid in ID.items():
        assert rid not in texto, f"o ID de {quem} vazou"


def _por_chave(corpo: dict) -> dict[str, dict]:
    return {e["chave"]: e for e in corpo["envios"]}


async def _admin(db, make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


# ─────────────────────────────────────────────────────────── lista


@pytest.mark.asyncio
async def test_lista_mostra_a_configuracao_antiga_com_nomes_e_sem_id(
    db, monkeypatch, client, make_user, auth_as
):
    c = await montar(db, monkeypatch)
    # o Informar da Logística antes da F2 (o teste não depende da fatia publicada)
    desmigrar(monkeypatch, K.LOGISTICA_INFORMAR)
    await _admin(db, make_user, auth_as)
    r = await client.get("/api/threema/envios")
    assert r.status_code == 200, r.text
    _sem_id(r)
    corpo = r.json()
    assert corpo["total"] == len(ENVIOS) == 60
    assert corpo["emergencia"] == {
        "usar_legado": False,
        "alterado_por": None,
        "alterado_em": None,
        "env_desligado": False,
    }
    e = _por_chave(corpo)
    assert list(e) == [d.chave for d in ENVIOS], "na ordem do catálogo"

    # Informar da Logística: a lista do botão, com o lugar onde se muda.
    info = e[K.LOGISTICA_INFORMAR]
    assert [n["nome"] for n in info["quem_recebe"]["nomes"]] == ["harry potter", "heisenberg"]
    assert info["quem_recebe"]["via"] == "legado"
    assert info["situacao"] == "legado"
    assert info["editavel"] is False
    assert "Logística › Informar" in info["por_que_nao_edita"]
    faixa = info["faixas"][0]
    assert faixa["tipo"] == "ambar" and faixa["situacao"] == "legado"
    assert faixa["texto"].startswith("Usa a configuração antiga: Informar 'logistica'")
    assert "Para mudar quem recebe: Logística › Informar" in faixa["texto"]

    # Correios da Ouvidoria: usuários + o contato externo Roma.
    correios = e[K.OUVIDORIA_VIGIA_CORREIOS]
    assert [n["nome"] for n in correios["quem_recebe"]["nomes"]] == [
        "londres",
        "harry potter",
        "cairo sa",
        "joana dark",
        "Roma",
    ]
    assert correios["quem_recebe"]["nomes"][-1]["tipo"] == "contato"
    assert correios["depende_de"][0]["estado"] == "ligado"
    assert correios["tem_reaviso"] is True and correios["reaviso_horas"] >= 1

    # Ninguém: o prazo de resposta da loja (linha `devolucoes_auto` ausente).
    prazo = e[K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA]
    assert prazo["situacao"] == "ninguem"
    assert any(f["tipo"] == "vermelho" and "Ninguém recebe" in f["texto"] for f in prazo["faixas"])
    # Robô desligado na Ouvidoria e robô em silencioso: a coluna Ativo diz o
    # estado EFETIVO (o modo do robô), não "ligado".
    cred = e[K.OUVIDORIA_VIGIA_CREDENCIAIS]
    assert cred["situacao"] == "desligado"
    assert cred["depende_de"][0]["estado"] == "desligado"
    assert cred["ativo_texto"].startswith("desligado: o robô 'Vigia de API x Contas'"), cred
    assert any(
        f["texto"].startswith("Robô desligado em Ouvidoria › Robôs: Vigia de API x Contas.")
        for f in cred["faixas"]
    )
    # desligado de propósito: "ninguém recebe" não acende em vermelho
    assert not any(f["tipo"] == "vermelho" for f in cred["faixas"]), cred["faixas"]
    frete = e[K.OUVIDORIA_CHAMADO_FRETE_ML]
    assert "bloqueado" in frete["situacoes"]
    assert any("silencioso" in f["texto"] for f in frete["faixas"])
    assert frete["ativo_texto"].startswith("silencioso: o robô")
    # robô ligado: diz onde fica o interruptor
    assert correios["ativo_texto"] == (
        "ligado (robô 'Vigia Ocorrências Correio' ligado em Ouvidoria › Robôs)"
    )
    # Relatório da Denúncia: o modo do robô é o interruptor (desligado no cenário).
    rel = e[K.DENUNCIA_RELATORIO_DIARIO]
    assert rel["ativo"] is False and rel["situacao"] == "desligado"
    assert "Relatório" in rel["faixas"][0]["texto"] or "robô" in rel["faixas"][0]["texto"]
    # Conferência: a chave do .env desliga (ML desligada no cenário).
    conf_ml = e[K.MARKETING_CONFERENCIA_ML]
    assert conf_ml["ativo"] is False
    assert "CONFERENCIA_ML_THREEMA" in conf_ml["faixas"][0]["texto"]
    # Item 1: sem interruptor; a coluna conta as regras com lista própria.
    regra = e[K.LOGISTICA_MENSAGEM_REGRA]
    assert regra["desligavel"] is False
    assert regra["ativo_texto"].startswith("sempre")
    assert regra["por_que_nao_desliga"].startswith("Sem interruptor")
    por_regra = regra["quem_recebe"]["por_regra"]
    assert por_regra["regras"] >= 1, por_regra
    assert all(
        n not in [x["nome"] for x in regra["quem_recebe"]["nomes"]] for n in por_regra["extras"]
    )
    # remetente × conversa de verdade (servidor sim; Mac e informativo não)
    assert isinstance(info["canal"], str) and info["canal"]
    assert e[K.DEVOLUCOES_CODIGOS_ML_TUTA]["canal"] is None
    # Tuta do servidor: o usuário pelo nome.
    tuta = e[K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR]
    assert [n["nome"] for n in tuta["quem_recebe"]["nomes"]] == ["thatcher"]

    # Informativos, externo e Mac.
    assert e[K.PEDIDOS_CORRECAO_DIA_ENVIO]["situacao"] == "informativo"
    assert e[K.CONTROLE_ESTOQUE_HERMES_ESTOQUE_NEGATIVO]["situacao"] == "externo"
    assert e[K.CHAMADOS_AGENTE_ML_AVISOS]["situacao"] == "informativo"
    mac = e[K.DEVOLUCOES_CODIGOS_ML_TUTA]
    assert mac["situacao"] == "mac_env"
    assert mac["editavel"] is True, "o envio do Mac pode ser editado (C7)"
    assert "DEVOL_CODIGOS_THREEMA_TO" in mac["faixas"][0]["texto"]
    # ... mas o liga/desliga ainda é do Mac (até ele ler o Cadastro)
    assert mac["desligavel"] is False and "próprio Mac" in mac["por_que_nao_desliga"]
    assert mac["ativo_texto"].startswith("ligado no Mac")

    # Contagem do catálogo inteiro.
    assert corpo["contagem"]["informativo"] == 3
    assert corpo["contagem"]["externo"] == 1
    assert corpo["contagem"]["mac_env"] == 9
    assert sum(1 for x in corpo["envios"] if x["situacao"] == "legado") > 20
    assert c.roma is not None


@pytest.mark.asyncio
async def test_filtros(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    # o Informar da Logística e a Conferência Shopee antes da F2 (o teste não
    # depende da fatia publicada)
    desmigrar(monkeypatch, K.LOGISTICA_INFORMAR, K.MARKETING_CONFERENCIA_SHOPEE)
    await _admin(db, make_user, auth_as)
    ouv = (await client.get("/api/threema/envios", params={"painel": "Ouvidoria"})).json()
    assert {x["painel"] for x in ouv["envios"]} == {"Ouvidoria"}
    assert len(ouv["envios"]) == 14
    assert ouv["total"] == 60 and ouv["contagem"]["mac_env"] == 9, "contagem não filtra"
    mac = (await client.get("/api/threema/envios", params={"onde_roda": "mac"})).json()
    assert len(mac["envios"]) == 11
    nin = (await client.get("/api/threema/envios", params={"situacao": "ninguem"})).json()
    assert K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA in _por_chave(nin)
    assert all("ninguem" in x["situacoes"] for x in nin["envios"])
    roma = (await client.get("/api/threema/envios", params={"busca": "roma"})).json()
    assert set(_por_chave(roma)) >= {K.OUVIDORIA_VIGIA_CORREIOS, K.DENUNCIA_RELATORIO_DIARIO}
    # Só os envios de uma pessoa (pela configuração antiga, pelo ID dela).
    harry = c.usuarios["harry"]
    so = (await client.get("/api/threema/envios", params={"pessoa": str(harry.id)})).json()
    assert so["pessoa"]["nome"] == "harry potter"
    chaves = set(_por_chave(so))
    assert {K.LOGISTICA_INFORMAR, K.MARKETING_CONFERENCIA_SHOPEE} <= chaves
    assert K.LOGISTICA_LOJA_MUDA not in chaves
    # Situação desconhecida é ignorada (não zera a lista).
    tudo = (await client.get("/api/threema/envios", params={"situacao": "xyz"})).json()
    assert len(tudo["envios"]) == 60


@pytest.mark.asyncio
async def test_so_admin(db, monkeypatch, client, make_user, auth_as):
    await montar(db, monkeypatch)
    u = await make_user(role=UserRole.USER, permissions={"logistica": {"view": True}})
    auth_as(u)
    for url in (
        "/api/threema/envios",
        f"/api/threema/envios/{K.LOGISTICA_INFORMAR}",
        f"/api/threema/envios/{K.LOGISTICA_INFORMAR}/registros",
        "/api/threema/pessoas",
        f"/api/threema/pessoas/{u.id}/envios",
    ):
        r = await client.get(url)
        assert r.status_code == 403, url


@pytest.mark.asyncio
async def test_detalhe_regras_e_antes(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    await _admin(db, make_user, auth_as)
    r = await client.get(f"/api/threema/envios/{K.LOGISTICA_MENSAGEM_REGRA}")
    assert r.status_code == 200, r.text
    _sem_id(r)
    d = r.json()
    assert d["antes"]["fonte"] == "THREEMA_RECIPIENTS do servidor"
    assert [n["nome"] for n in d["antes"]["nomes"]] == ["churchill", "londres", "cairo sa"]
    assert d["destinatarios"] == []
    regras = {x["id"]: x for x in d["regras"]}
    assert set(regras) == {str(r.id) for r in c.regras.values()}
    com = regras[str(c.regras["com_pessoa"].id)]
    assert com["tem_mensagem"] and not com["usa_padrao"]
    assert [n["nome"] for n in com["antes"]["nomes"]] == ["harry potter", "joana dark"]
    sem_msg = regras[str(c.regras["pessoa_sem_mensagem"].id)]
    assert not sem_msg["tem_mensagem"] and not sem_msg["usa_padrao"]
    assert regras[str(c.regras["sem_pessoa"].id)]["usa_padrao"] is True
    nao = await client.get("/api/threema/envios/nao.existe")
    assert nao.status_code == 404
    assert nao.json()["detail"]["code"] == "envio_desconhecido"


@pytest.mark.asyncio
async def test_migrado_le_o_cadastro(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    await _admin(db, make_user, auth_as)
    migrar(monkeypatch, K.LOGISTICA_VIGIA_AUTOMACAO)
    await semeado(db, K.LOGISTICA_VIGIA_AUTOMACAO, c.usuarios["joana"], c.roma)
    e = _por_chave((await client.get("/api/threema/envios")).json())
    vigia = e[K.LOGISTICA_VIGIA_AUTOMACAO]
    assert vigia["editavel"] is True and vigia["migrado"] is True
    assert vigia["quem_recebe"]["via"] == "cadastro"
    assert [n["nome"] for n in vigia["quem_recebe"]["nomes"]] == ["joana dark", "Roma"]
    assert vigia["situacao"] == "ok" and vigia["faixas"] == []
    # O detalhe traz as linhas do Cadastro e o "Antes" (a lista do .env).
    d = (await client.get(f"/api/threema/envios/{K.LOGISTICA_VIGIA_AUTOMACAO}")).json()
    assert [(x["tipo"], x["nome"], x["valido"]) for x in d["destinatarios"]] == [
        ("usuario", "joana dark", True),
        ("contato", "Roma", True),
    ]
    assert [n["nome"] for n in d["antes"]["nomes"]] == [
        "londres",
        "harry potter",
        "heisenberg",
        "cairo sa",
    ]
    # Cenário M5: só "cairo" (suspenso) na lista → ninguém, faixa vermelha.
    migrar(monkeypatch, K.LOGISTICA_LOJA_MUDA)
    await semeado(db, K.LOGISTICA_LOJA_MUDA, c.usuarios["cairo"])
    threema_envios.limpar_cache()
    muda = _por_chave((await client.get("/api/threema/envios")).json())[K.LOGISTICA_LOJA_MUDA]
    assert muda["situacao"] == "ninguem"
    assert "cairo está suspenso" in muda["faixas"][0]["texto"]


@pytest.mark.asyncio
async def test_emergencia_aparece_na_lista(db, monkeypatch, client, make_user, auth_as):
    await montar(db, monkeypatch)
    await _admin(db, make_user, auth_as)
    estado = await db.get(ThreemaCadastroEstado, 1)
    if estado is None:
        estado = ThreemaCadastroEstado(id=1)
        db.add(estado)
    estado.usar_legado = True
    estado.alterado_por = "Fulano"
    await db.commit()
    try:
        corpo = (await client.get("/api/threema/envios")).json()
        assert corpo["emergencia"]["usar_legado"] is True
        assert corpo["emergencia"]["alterado_por"] == "Fulano"
    finally:
        estado.usar_legado = False
        estado.alterado_por = None
        await db.commit()


# ─────────────────────────────────────────────────────────── registro


def _reg(lote, quando, chave, **kw) -> ThreemaEnvioRegistro:
    base = {
        "lote_id": lote,
        "criado_em": quando,
        "envio_chave": chave,
        "origem": "automatico",
        "contexto": "logistica",
        "ok": True,
        "http_status": 200,
        "titulo": "Logística — automação parada:",
    }
    base.update(kw)
    return ThreemaEnvioRegistro(**base)


@pytest.mark.asyncio
async def test_ultimo_lote_e_registros(db, monkeypatch, client, make_user, auth_as):
    await montar(db, monkeypatch)
    await _admin(db, make_user, auth_as)
    agora = datetime.now(UTC)
    chave = K.LOGISTICA_VIGIA_AUTOMACAO
    velho, novo = uuid.uuid4(), uuid.uuid4()
    db.add_all(
        [
            _reg(velho, agora - timedelta(hours=3), chave, destinatario_id=ID["londres"]),
            _reg(
                novo,
                agora - timedelta(minutes=5),
                chave,
                destinatario_id=ID["londres"],
                destinatario_nome="londres",
            ),
            _reg(
                novo,
                agora - timedelta(minutes=5),
                chave,
                destinatario_id=ID["harry"],
                destinatario_nome="harry potter",
                ok=False,
                http_status=402,
                erro="http_402",
            ),
        ]
    )
    await db.commit()
    e = _por_chave((await client.get("/api/threema/envios")).json())[chave]
    assert e["ultimo"]["lote_id"] == str(novo)
    assert (e["ultimo"]["total"], e["ultimo"]["entregues"], e["ultimo"]["falhas"]) == (2, 1, 1)
    assert "falhando" in e["situacoes"]
    assert any("sem crédito no Threema (402)" in f["texto"] for f in e["faixas"])

    r = await client.get(f"/api/threema/envios/{chave}/registros", params={"limit": 1})
    assert r.status_code == 200
    _sem_id(r)
    corpo = r.json()
    assert corpo["mais"] is True
    [lote] = corpo["items"]
    assert lote["lote_id"] == str(novo)
    assert [(x["nome"], x["id"], x["ok"]) for x in lote["destinatarios"]] == [
        ("londres", "TS…", True),
        ("harry potter", "TS…", False),
    ]
    antes = (agora - timedelta(minutes=30)).isoformat()
    r2 = await client.get(f"/api/threema/envios/{chave}/registros", params={"antes": antes})
    assert [x["lote_id"] for x in r2.json()["items"]] == [str(velho)]


# ─────────────────────────────────────────────────────────── pessoas


@pytest.mark.asyncio
async def test_pessoas_e_envios_da_pessoa(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    # o Informar da Logística e a Conferência Shopee antes da F2 (o teste não
    # depende da fatia publicada)
    desmigrar(monkeypatch, K.LOGISTICA_INFORMAR, K.MARKETING_CONFERENCIA_SHOPEE)
    await _admin(db, make_user, auth_as)
    r = await client.get("/api/threema/pessoas")
    assert r.status_code == 200
    _sem_id(r)
    p = r.json()
    nomes = {u["nome"]: u for u in p["usuarios"]}
    assert "cairo" not in nomes, "suspenso fica de fora"
    assert nomes["harry potter"]["valido"] is True
    assert p["contatos"][0]["nome"] == "Roma"
    assert p["contatos"][0]["threema_id"] == "TS…"
    assert p["grupos"] == []

    harry = c.usuarios["harry"]
    r = await client.get(f"/api/threema/pessoas/{harry.id}/envios")
    assert r.status_code == 200
    uso = r.json()
    por = {x["chave"]: x for x in uso["envios"]}
    # Configuração antiga: a lista guarda o ID — suspender ou apagar o
    # Threema dela não faz o aviso parar de chegar. Nunca "única" aqui; o
    # aviso diz onde tirar.
    conf = por[K.MARKETING_CONFERENCIA_SHOPEE]
    assert conf["via"] == "legado" and conf["unico"] is False
    assert conf["onde"] == "Marketing › Conferência › quem recebe"
    assert por[K.LOGISTICA_INFORMAR]["unico"] is False
    assert uso["total"] == len(uso["envios"]) and uso["unicos"] == 0
    assert uso["pela_configuracao_antiga"] == uso["total"]
    # os envios do Mac usam a lista de lá: o DaVinci não sabe quem está nela
    n_mac = sum(1 for d in ENVIOS if d.onde_roda == "mac" and d.gerenciado)
    assert uso["envios_do_mac_no_env"] == n_mac == 9
    # Envio do Mac com a pessoa no Cadastro: conta pelo Cadastro.
    await threema_envios.sincronizar(db)
    db.add(
        ThreemaEnvioDestinatario(
            envio_chave=K.DEVOLUCOES_CODIGOS_ML_TUTA, tipo="usuario", user_id=harry.id
        )
    )
    await db.commit()
    uso = (await client.get(f"/api/threema/pessoas/{harry.id}/envios")).json()
    mac = {x["chave"]: x for x in uso["envios"]}[K.DEVOLUCOES_CODIGOS_ML_TUTA]
    assert mac["via"] == "mac_env" and mac["unico"] is False, "o Mac ainda não lê daqui"
    # Com o Mac mandando pela lista daqui, conta pelo Cadastro (e é a única).
    db.add(
        ThreemaEnvioRegistro(
            lote_id=uuid.uuid4(),
            envio_chave=K.DEVOLUCOES_CODIGOS_ML_TUTA,
            onde_roda="mac",
            origem="automatico",
            via="cadastro",
            ok=True,
        )
    )
    await db.commit()
    uso = (await client.get(f"/api/threema/pessoas/{harry.id}/envios")).json()
    mac = {x["chave"]: x for x in uso["envios"]}[K.DEVOLUCOES_CODIGOS_ML_TUTA]
    assert mac["via"] == "cadastro" and mac["unico"] is True
    assert uso["unicos"] == 1 and uso["envios_do_mac_no_env"] == 8
    assert (await client.get(f"/api/threema/pessoas/{uuid.uuid4()}/envios")).status_code == 404


@pytest.mark.asyncio
async def test_envios_da_pessoa_conta_o_item_1_pelo_da_regra(
    db, monkeypatch, client, make_user, auth_as
):
    """Item 1 na configuração antiga: quem está só no 👤 de uma regra também
    recebe (o aviso de Usuários antes de suspender e o filtro ?pessoa=)."""
    c = await montar(db, monkeypatch)
    await _admin(db, make_user, auth_as)
    joana, heis = c.usuarios["joana"], c.usuarios["heisenberg"]
    assert ID["joana"] not in get_settings().threema_recipients  # fora do padrão
    uso = (await client.get(f"/api/threema/pessoas/{joana.id}/envios")).json()
    regra = {x["chave"]: x for x in uso["envios"]}[K.LOGISTICA_MENSAGEM_REGRA]
    assert regra["via"] == "legado" and regra["regras"] == ["Regra com pessoa"]
    assert regra["unico"] is False  # harry também está na regra
    uso = (await client.get(f"/api/threema/pessoas/{heis.id}/envios")).json()
    regra = {x["chave"]: x for x in uso["envios"]}[K.LOGISTICA_MENSAGEM_REGRA]
    assert regra["regras"] == ["Regra com pessoa e sem mensagem"]
    assert regra["unico"] is False, "configuração antiga: o ID continua na lista"
    assert regra["onde"].startswith("Logística › Status")
    # o filtro ?pessoa= da tela usa a mesma conta
    e = _por_chave((await client.get(f"/api/threema/envios?pessoa={joana.id}")).json())
    assert K.LOGISTICA_MENSAGEM_REGRA in e


# ─────────────────────────────────────────────────────────── quem recebe (leve)


@pytest.mark.asyncio
async def test_quem_recebe_leve_com_a_porta_do_catalogo(
    db, monkeypatch, client, make_user, auth_as
):
    await montar(db, monkeypatch)
    gerente = await make_user(email="sa.geral@tutamail.com", role=UserRole.USER)
    auth_as(gerente)
    r = await client.get(f"/api/threema/envios/{K.MARGEM_PEDIDO_SEGURADO}/quem-recebe")
    assert r.status_code == 200, r.text
    _sem_id(r)
    q = r.json()
    assert q["nomes"] == ["londres", "harry potter", "cairo sa"]
    assert q["usa_configuracao_antiga"] is True and q["ninguem"] is False
    # Envio só de admin e painel sem permissão: 403.
    assert (
        await client.get(f"/api/threema/envios/{K.LOGISTICA_VIGIA_AUTOMACAO}/quem-recebe")
    ).status_code == 403
    assert (
        await client.get(f"/api/threema/envios/{K.LOGISTICA_MENSAGEM_REGRA}/quem-recebe")
    ).status_code == 403
    # Quem vê a Logística vê o item 1 (porta perm:logistica:view).
    operador = await make_user(role=UserRole.USER, permissions={"logistica": {"view": True}})
    auth_as(operador)
    r = await client.get(f"/api/threema/envios/{K.LOGISTICA_MENSAGEM_REGRA}/quem-recebe")
    assert r.status_code == 200
    assert r.json()["nomes"] == ["churchill", "londres", "cairo sa"]
    # Ninguém: o prazo de resposta da loja, para o admin.
    await _admin(db, make_user, auth_as)
    q = (
        await client.get(f"/api/threema/envios/{K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA}/quem-recebe")
    ).json()
    assert q["ninguem"] is True and q["nomes"] == []
    assert (await client.get("/api/threema/envios/x.y/quem-recebe")).status_code == 404


# ─────────────────────────────────────────────────────────── robôs do Mac


@pytest.mark.asyncio
async def test_robo_sem_token_401(db, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    assert (await client.get("/api/threema/agent/destinatarios")).status_code == 401
    r = await client.get("/api/threema/agent/destinatarios", headers={"X-Agent-Token": "errado"})
    assert r.status_code == 401
    r = await client.post("/api/threema/agent/registros", json={"itens": []})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_robo_le_a_lista_do_mac(db, monkeypatch, client):
    c = await montar(db, monkeypatch)
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    h = {"X-Agent-Token": _TOKEN}
    r = await client.get("/api/threema/agent/destinatarios", headers=h)
    assert r.status_code == 200, r.text
    corpo = r.json()
    macs = [d.chave for d in ENVIOS if d.onde_roda == "mac" and d.gerenciado]
    assert sorted(corpo["envios"]) == sorted(macs) and len(macs) == 9
    assert all(v == {"ativo": True, "ids": [], "fonte": "vazio"} for v in corpo["envios"].values())
    assert corpo["usar_legado"] is False

    await threema_envios.sincronizar(db)
    db.add_all(
        [
            ThreemaEnvioDestinatario(
                envio_chave=K.DEVOLUCOES_CODIGOS_ML_TUTA,
                tipo="usuario",
                user_id=c.usuarios["thatcher"].id,
                ordem=0,
            ),
            ThreemaEnvioDestinatario(
                envio_chave=K.DEVOLUCOES_CODIGOS_ML_TUTA,
                tipo="contato",
                contato_id=c.roma.id,
                ordem=1,
            ),
            # Só um suspenso: a expansão sai vazia → o Mac usa o .env.
            ThreemaEnvioDestinatario(
                envio_chave=K.CHAMADOS_AGENTE_RESUMO_DIARIO,
                tipo="usuario",
                user_id=c.usuarios["cairo"].id,
            ),
        ]
    )
    linha = await db.get(ThreemaEnvio, K.NOTAS_FISCAIS_LOTE100_RESUMO)
    linha.ativo = False
    await db.commit()
    env = (await client.get("/api/threema/agent/destinatarios", headers=h)).json()["envios"]
    assert env[K.DEVOLUCOES_CODIGOS_ML_TUTA] == {
        "ativo": True,
        "ids": [ID["thatcher"], ID["roma"]],
        "fonte": "cadastro",
    }
    assert env[K.CHAMADOS_AGENTE_RESUMO_DIARIO]["fonte"] == "vazio"
    assert env[K.NOTAS_FISCAIS_LOTE100_RESUMO] == {"ativo": False, "ids": [], "fonte": "cadastro"}

    # Emergência: todos "vazio" (o Mac volta ao .env).
    estado = await db.get(ThreemaCadastroEstado, 1)
    if estado is None:
        estado = ThreemaCadastroEstado(id=1)
        db.add(estado)
    estado.usar_legado = True
    await db.commit()
    try:
        corpo = (await client.get("/api/threema/agent/destinatarios", headers=h)).json()
        assert corpo["usar_legado"] is True
        assert {v["fonte"] for v in corpo["envios"].values()} == {"vazio"}
    finally:
        estado.usar_legado = False
        await db.commit()
    # O piso do .env também.
    monkeypatch.setattr(get_settings(), "threema_cadastro_ativo", False)
    corpo = (await client.get("/api/threema/agent/destinatarios", headers=h)).json()
    assert corpo["usar_legado"] is True
    r = await client.get("/api/threema/agent/destinatarios?onde_roda=servidor", headers=h)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_robo_grava_o_registro(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    h = {"X-Agent-Token": _TOKEN}
    quando = "2026-11-19T03:31:00"  # hora local do Mac, sem fuso
    itens = [
        {
            "chave": K.CHAMADOS_FRETE_ML_RELATORIO_NOITE,
            "criado_em": quando,
            "destinatario": ID["heisenberg"].lower(),
            "ok": True,
            "http_status": 200,
            "titulo": "Relatório da noite\nlinha 2",
            "host": "mac-escritorio",
            "via": "cadastro",
            "lote": "r-0330",
        },
        {
            "chave": K.CHAMADOS_FRETE_ML_RELATORIO_NOITE,
            "criado_em": quando,
            "destinatario": ID["cairo_sa"],
            "ok": False,
            "http_status": 402,
            "erro": "http_402",
            "titulo": "Relatório da noite",
            "via": "cadastro",
            "lote": "r-0330",
        },
        {"chave": "mac.chave_nova", "destinatario": ID["londres"], "ok": True, "via": "env"},
        {
            # o threema_send.py do Mac imprime o ID inteiro no erro mais comum
            "chave": K.CHAMADOS_FRETE_ML_RELATORIO_NOITE,
            "destinatario": ID["roma"],
            "ok": False,
            "http_status": 404,
            "erro": f"ERRO ao buscar public key de {ID['roma'].lower()}: HTTP 404 (TSTX0099)",
            "titulo": f"CORREIOS: aviso para {ID['roma']} e *TSTGW01",
        },
    ]
    r = await client.post("/api/threema/agent/registros", json={"itens": itens}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"gravados": 4, "chaves_desconhecidas": ["mac.chave_nova"]}
    linhas = (
        (await db.execute(select(ThreemaEnvioRegistro).order_by(ThreemaEnvioRegistro.id)))
        .scalars()
        .all()
    )
    assert [x.onde_roda for x in linhas] == ["mac"] * 4
    a, b, sem, roma = linhas
    # ID inteiro nunca fica no erro nem no título (aparecem no Histórico)
    assert roma.erro == "ERRO ao buscar public key de TS…: HTTP 404 (TS…)"
    assert roma.titulo == "CORREIOS: aviso para TS… e *T…"
    assert (roma.destinatario_nome, roma.contato_id) == ("Roma", c.roma.id)
    assert a.lote_id == b.lote_id != sem.lote_id
    assert a.envio_chave == K.CHAMADOS_FRETE_ML_RELATORIO_NOITE and sem.envio_chave is None
    assert a.destinatario_id == ID["heisenberg"] and a.destinatario_nome == "heisenberg"
    assert a.user_id == c.usuarios["heisenberg"].id
    assert a.titulo == "Relatório da noite" and a.host == "mac-escritorio"
    assert a.criado_em == datetime(2026, 11, 19, 6, 31, tzinfo=UTC)
    assert (b.ok, b.http_status, b.erro) == (False, 402, "http_402")
    assert a.via == "cadastro" and sem.via == "env" and a.origem == "automatico"

    # Com a lista no Cadastro e a evidência do Mac, o envio sai de "o Mac usa o .env".
    await threema_envios.sincronizar(db)
    db.add(
        ThreemaEnvioDestinatario(
            envio_chave=K.CHAMADOS_FRETE_ML_RELATORIO_NOITE,
            tipo="usuario",
            user_id=c.usuarios["heisenberg"].id,
        )
    )
    # a evidência é dos últimos 7 dias
    for x in (a, b):
        x.criado_em = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()
    await _admin(db, make_user, auth_as)
    e = _por_chave((await client.get("/api/threema/envios")).json())
    noite = e[K.CHAMADOS_FRETE_ML_RELATORIO_NOITE]
    assert noite["quem_recebe"]["via"] == "cadastro"
    assert "mac_env" not in noite["situacoes"]
    assert "falhando" in noite["situacoes"]
    assert e[K.CHAMADOS_FRETE_ML_RESPOSTA_RECEBIDA]["situacao"] == "mac_env"

    demais = [{"chave": "x.y", "ok": True}] * 201
    r = await client.post("/api/threema/agent/registros", json={"itens": demais}, headers=h)
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────── nomes do Histórico


def test_nomes_do_historico():
    assert nomes.tela_da_pagina("/envios-threema") == "Cadastros › Envios Threema"
    assert nomes.tela_da_api("/api/threema/envios") == "Cadastros › Envios Threema"
    assert all(d.chave in CATALOGO for d in ENVIOS)


@pytest.mark.asyncio
async def test_robo_da_ouvidoria_sem_linha_nao_aparece_ligado(
    db, monkeypatch, client, make_user, auth_as
):
    """`avisar_pendentes` não manda nada sem a linha do robô
    (`robo_nao_cadastrado`); a tela não pode mostrá-lo como ligado. Já a
    Margem (25 a 27) trata a falta da linha como `ligado`, como o ponto."""
    await montar(db, monkeypatch)
    await _admin(db, make_user, auth_as)
    await db.execute(
        text("DELETE FROM ouvidoria_robos WHERE chave IN ('vigia_flex', 'vigia_margem')")
    )
    await db.commit()
    e = _por_chave((await client.get("/api/threema/envios")).json())
    dep = e[K.OUVIDORIA_VIGIA_FLEX]["depende_de"][0]
    assert (dep["ok"], dep["estado"]) == (False, "sem linha")
    assert "bloqueado" in e[K.OUVIDORIA_VIGIA_FLEX]["situacoes"]
    assert any("não tem linha" in f["texto"] for f in e[K.OUVIDORIA_VIGIA_FLEX]["faixas"])
    assert (await ouvidoria.avisar_pendentes(db, "vigia_flex"))["motivo"] == "robo_nao_cadastrado"
    # o próprio aviso do Robô da Margem também não sai sem a linha…
    assert e[K.OUVIDORIA_VIGIA_MARGEM]["depende_de"][0]["ok"] is False
    # …mas o pedido segurado (25) sai: sem linha, o ponto trata como ligado
    seg = e[K.MARGEM_PEDIDO_SEGURADO]["depende_de"][0]
    assert (seg["ok"], seg["estado"]) == (True, "ligado")
