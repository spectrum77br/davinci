"""Cadastros › Envios Threema — edição (tarefa 16, C7).

Só admin. A edição de quem recebe vale só para o envio que lê o Cadastro: o
do servidor migrado (aqui, com `migrado=True` só no catálogo do teste) e o do
Mac. Nesta fase nenhum envio do servidor está migrado — e a REGRA DE OURO é
conferida no fim: depois de editar tudo o que a tela deixa, nenhum envio do
servidor muda de destinatário nem de liga/desliga.

* PATCH /envios/{chave}: 422 `envio_nao_migrado` (com onde mudar),
  `envio_informativo`, `nao_desligavel` (item 1), `sem_destinatarios_ativo`,
  pessoa sem Threema ou suspensa, contato inativo e o teto de 20; o liga do
  item 43 grava o modo do robô e o do item 27 a caixinha da Margem; o
  re-aviso grava no robô da Ouvidoria;
* PUT/DELETE das regras (item 1);
* POST /teste: "[TESTE] <nome>" para quem recebe agora, com o remetente do
  envio, origem `teste` no registro (não muda quem recebe) — só no envio que
  já lê o Cadastro: no que usa a configuração antiga, 422 sem mandar nada;
* grupos (409 em uso), contatos externos (409 "já é de Fulano", espelho
  antigo atualizado), emergência;
* Usuários: o campo Threema só aceita o formato do Threema.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.config import get_settings
from app.models import (
    OuvidoriaRobo,
    ThreemaCadastroEstado,
    ThreemaContato,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaEnvioRegistro,
    ThreemaGrupo,
    ThreemaInformarConfig,
    User,
    UserRole,
    UserStatus,
)
from app.services import threema, threema_envios
from app.services.threema_catalogo import CATALOGO, ENVIOS, K
from tests.test_threema_resolver import migrar, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.usefixtures("limpar_ouvidoria")

_MAC = K.DEVOLUCOES_CODIGOS_ML_TUTA


@pytest.fixture(autouse=True)
def _cache():
    threema_envios.limpar_cache()
    yield
    threema_envios.limpar_cache()


async def _admin(make_user, auth_as) -> User:
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


def _ref(obj) -> dict:
    tipo = (
        "usuario"
        if isinstance(obj, User)
        else "contato"
        if isinstance(obj, ThreemaContato)
        else "grupo"
    )
    return {"tipo": tipo, "id": str(obj.id)}


async def _legado_de_todos(db) -> dict[str, tuple]:
    out = {}
    for d in ENVIOS:
        d = CATALOGO[d.chave]
        if d.onde_roda == "servidor" and d.gerenciado:
            leg = await threema_envios.legado(d.chave, session=db)
            out[d.chave] = (leg.ids, leg.ativo)
    return out


async def _linhas(db, chave: str) -> list[ThreemaEnvioDestinatario]:
    return list(
        (
            await db.execute(
                select(ThreemaEnvioDestinatario)
                .where(ThreemaEnvioDestinatario.envio_chave == chave)
                .order_by(ThreemaEnvioDestinatario.ordem)
            )
        ).scalars()
    )


# ─────────────────────────────────────────────────────────── só admin


@pytest.mark.asyncio
async def test_nao_admin_recebe_403_em_toda_edicao(db, monkeypatch, client, make_user, auth_as):
    await montar(db, monkeypatch)
    gerente = await make_user(email="sa.geral@tutamail.com", role=UserRole.USER)
    auth_as(gerente)
    x = str(uuid.uuid4())
    for metodo, url, corpo in (
        ("PATCH", f"/api/threema/envios/{_MAC}", {"observacao": "x"}),
        (
            "PUT",
            f"/api/threema/envios/{K.LOGISTICA_MENSAGEM_REGRA}/regras/{x}",
            {"destinatarios": []},
        ),
        ("DELETE", f"/api/threema/envios/{K.LOGISTICA_MENSAGEM_REGRA}/regras/{x}", None),
        ("POST", f"/api/threema/envios/{K.LOGISTICA_INFORMAR}/teste", None),
        ("POST", "/api/threema/emergencia", {"usar_legado": True}),
        ("GET", "/api/threema/grupos", None),
        ("POST", "/api/threema/grupos", {"nome": "x"}),
        ("PATCH", f"/api/threema/grupos/{x}", {"nome": "y"}),
        ("DELETE", f"/api/threema/grupos/{x}", None),
        ("GET", "/api/threema/contatos", None),
        ("POST", "/api/threema/contatos", {"nome": "x", "threema_id": "TSTZ0001"}),
        ("PATCH", f"/api/threema/contatos/{x}", {"nome": "y"}),
        ("DELETE", f"/api/threema/contatos/{x}", None),
    ):
        r = await client.request(metodo, url, json=corpo)
        assert r.status_code == 403, (metodo, url, r.text)
    assert (await db.execute(select(func.count()).select_from(ThreemaGrupo))).scalar_one() == 0


# ─────────────────────────────────────────────────────────── PATCH


@pytest.mark.asyncio
async def test_envio_do_servidor_nao_migrado_nao_se_edita(
    db, monkeypatch, client, make_user, auth_as
):
    c = await montar(db, monkeypatch)
    await _admin(make_user, auth_as)
    antes = await _legado_de_todos(db)
    for corpo in (
        {"destinatarios": [_ref(c.usuarios["joana"])]},
        {"ativo": False},
        {"observacao": "anotação"},
    ):
        r = await client.patch(f"/api/threema/envios/{K.LOGISTICA_VIGIA_AUTOMACAO}", json=corpo)
        assert r.status_code == 422, r.text
        det = r.json()["detail"]
        assert det["code"] == "envio_nao_migrado"
        assert "NF_SEM_ESTOQUE_THREEMA_RECIPIENTS" in det["onde_configurar"]
        assert "configuração antiga" in det["message"]
    assert await _linhas(db, K.LOGISTICA_VIGIA_AUTOMACAO) == []
    linha = await db.get(ThreemaEnvio, K.LOGISTICA_VIGIA_AUTOMACAO)
    assert linha is None or (linha.ativo is None and linha.observacao is None)
    assert await _legado_de_todos(db) == antes
    r = await client.patch(
        f"/api/threema/envios/{K.PEDIDOS_CORRECAO_DIA_ENVIO}", json={"observacao": "x"}
    )
    assert r.json()["detail"]["code"] == "envio_informativo"
    r = await client.patch("/api/threema/envios/nao.existe", json={"observacao": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_envio_do_mac_se_edita(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    admin = await _admin(make_user, auth_as)
    corpo = {
        "destinatarios": [_ref(c.usuarios["thatcher"]), _ref(c.roma), _ref(c.usuarios["thatcher"])],
        "observacao": "  o Thatcher confere o ID  ",
    }
    r = await client.patch(f"/api/threema/envios/{_MAC}", json=corpo)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["avisos_da_edicao"] == [
        "O Mac ainda usa o .env: esta lista passa a valer quando o Mac ler o Cadastro."
    ]
    assert [(x["tipo"], x["nome"]) for x in d["destinatarios"]] == [
        ("usuario", "thatcher"),
        ("contato", "Roma"),
    ], "repetido vira um só, na ordem"
    assert d["observacao"] == "o Thatcher confere o ID"
    linhas = await _linhas(db, _MAC)
    assert [x.criado_por for x in linhas] == [admin.email] * 2
    # desligar: grava no Cadastro (o Mac ainda não lê)
    r = await client.patch(f"/api/threema/envios/{_MAC}", json={"ativo": False, "observacao": None})
    assert r.status_code == 200
    linha = await db.get(ThreemaEnvio, _MAC, populate_existing=True)
    assert linha.ativo is False and linha.observacao is None
    assert linha.ativo_alterado_por == admin.email
    # lista vazia no Mac pode (volta à reserva do .env)
    r = await client.patch(f"/api/threema/envios/{_MAC}", json={"destinatarios": [], "ativo": True})
    assert r.status_code == 200
    assert await _linhas(db, _MAC) == []
    assert r.json()["quem_recebe"]["via"] == "mac_env"
    # re-aviso só na Ouvidoria
    r = await client.patch(f"/api/threema/envios/{_MAC}", json={"reaviso_horas": 4})
    assert r.json()["detail"]["code"] == "sem_reaviso"
    # teste não sai do servidor para envio do Mac
    r = await client.post(f"/api/threema/envios/{_MAC}/teste")
    assert r.json()["detail"]["code"] == "teste_no_mac"


@pytest.mark.asyncio
async def test_validacoes_de_quem_recebe(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    await _admin(make_user, auth_as)
    sem = User(
        open_id="email:sem@davinci-test.com",
        email="sem@davinci-test.com",
        name="Sem Threema",
        status=UserStatus.ACTIVE,
    )
    db.add(sem)
    c.roma.ativo = False
    await db.commit()
    url = f"/api/threema/envios/{_MAC}"
    casos = [
        ([_ref(sem)], "pessoa_invalida", "Sem Threema sem Threema"),
        ([_ref(c.usuarios["cairo"])], "pessoa_invalida", "cairo está suspenso"),
        ([_ref(c.roma)], "contato_inativo", "Roma"),
        ([{"tipo": "grupo", "id": str(uuid.uuid4())}], "grupo_inexistente", "grupo"),
        ([{"tipo": "usuario", "id": str(uuid.uuid4())}], "pessoa_inexistente", "pessoa"),
    ]
    for refs, code, frase in casos:
        r = await client.patch(url, json={"destinatarios": refs})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == code
        assert frase in r.json()["detail"]["message"]
    # teto de 20
    muitos = []
    for i in range(21):
        u = User(
            open_id=f"email:m{i}@davinci-test.com",
            email=f"m{i}@davinci-test.com",
            name=f"m{i}",
            threema=f"TSTN{i:04d}",
            status=UserStatus.ACTIVE,
        )
        db.add(u)
        muitos.append(u)
    await db.commit()
    r = await client.patch(url, json={"destinatarios": [_ref(u) for u in muitos]})
    assert r.json()["detail"]["code"] == "destinatarios_demais"
    r = await client.patch(url, json={"destinatarios": [{"tipo": "outro", "id": str(sem.id)}]})
    assert r.status_code == 422
    assert await _linhas(db, _MAC) == []


@pytest.mark.asyncio
async def test_migrado_liga_desliga_e_fontes_diretas(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    await _admin(make_user, auth_as)
    # vigia de automação migrado e semeado com a joana
    chave = K.LOGISTICA_VIGIA_AUTOMACAO
    migrar(monkeypatch, chave)
    await semeado(db, chave, c.usuarios["joana"])
    r = await client.patch(f"/api/threema/envios/{chave}", json={"destinatarios": []})
    assert r.json()["detail"]["code"] == "sem_destinatarios_ativo"
    r = await client.patch(
        f"/api/threema/envios/{chave}", json={"destinatarios": [], "ativo": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["situacao"] == "desligado"
    r = await client.patch(f"/api/threema/envios/{chave}", json={"ativo": True})
    assert r.json()["detail"]["code"] == "sem_destinatarios_ativo", "religar com a lista vazia"
    r = await client.patch(
        f"/api/threema/envios/{chave}",
        json={"ativo": True, "destinatarios": [_ref(c.usuarios["harry"])]},
    )
    assert r.status_code == 200
    assert [n["nome"] for n in r.json()["quem_recebe"]["nomes"]] == ["harry potter"]
    res = await threema_envios.resolver(chave)
    assert res.via == "cadastro" and res.ids == (ID["harry"],)

    # Item 1: sem interruptor.
    migrar(monkeypatch, K.LOGISTICA_MENSAGEM_REGRA)
    r = await client.patch(
        f"/api/threema/envios/{K.LOGISTICA_MENSAGEM_REGRA}", json={"ativo": False}
    )
    assert r.json()["detail"]["code"] == "nao_desligavel"

    # Item 43: o liga grava o modo do robô do Relatório da Denúncia.
    migrar(monkeypatch, K.DENUNCIA_RELATORIO_DIARIO)
    await semeado(db, K.DENUNCIA_RELATORIO_DIARIO, c.usuarios["harry"])
    r = await client.patch(
        f"/api/threema/envios/{K.DENUNCIA_RELATORIO_DIARIO}", json={"ativo": True}
    )
    assert r.status_code == 200, r.text
    robo = await db.get(OuvidoriaRobo, "denuncia_relatorio_threema", populate_existing=True)
    assert robo.modo == "ligado" and robo.modo_alterado_por
    linha = await db.get(ThreemaEnvio, K.DENUNCIA_RELATORIO_DIARIO, populate_existing=True)
    assert linha.ativo is None, "a fonte é o modo do robô"

    # Item 27: a caixinha da margem alta do Robô da Margem.
    migrar(monkeypatch, K.MARGEM_MARGEM_ALTA)
    await semeado(db, K.MARGEM_MARGEM_ALTA, c.usuarios["londres"])
    r = await client.patch(f"/api/threema/envios/{K.MARGEM_MARGEM_ALTA}", json={"ativo": True})
    assert r.status_code == 200, r.text
    margem = await db.get(OuvidoriaRobo, "vigia_margem", populate_existing=True)
    assert margem.config["avisar_margem_alta"] is True

    # Ouvidoria: o re-aviso grava no robô.
    migrar(monkeypatch, K.OUVIDORIA_VIGIA_FLEX)
    await semeado(db, K.OUVIDORIA_VIGIA_FLEX, c.usuarios["cairo_sa"])
    r = await client.patch(
        f"/api/threema/envios/{K.OUVIDORIA_VIGIA_FLEX}", json={"reaviso_horas": 6}
    )
    assert r.status_code == 200, r.text
    flex = await db.get(OuvidoriaRobo, "vigia_flex", populate_existing=True)
    assert flex.reaviso_horas == 6
    assert r.json()["reaviso_horas"] == 6
    r = await client.patch(
        f"/api/threema/envios/{K.OUVIDORIA_VIGIA_FLEX}", json={"reaviso_horas": 0}
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_regras_do_item_1(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    await _admin(make_user, auth_as)
    chave = K.LOGISTICA_MENSAGEM_REGRA
    regra = c.regras["sem_pessoa"]
    url = f"/api/threema/envios/{chave}/regras/{regra.id}"
    corpo = {"destinatarios": [_ref(c.usuarios["joana"])]}
    r = await client.put(url, json=corpo)
    assert r.json()["detail"]["code"] == "envio_nao_migrado", "item 1 não migrado"
    migrar(monkeypatch, chave)
    r = await client.put(url, json=corpo)
    assert r.status_code == 200, r.text
    regras = {x["id"]: x for x in r.json()["regras"]}
    assert [x["nome"] for x in regras[str(regra.id)]["cadastro"]] == ["joana dark"]
    assert len(await _linhas(db, chave)) == 1
    assert (await client.put(url, json={"destinatarios": []})).json()["detail"]["code"] == (
        "lista_vazia"
    )
    outra = f"/api/threema/envios/{chave}/regras/{uuid.uuid4()}"
    assert (await client.put(outra, json=corpo)).status_code == 404
    r = await client.delete(url)
    assert r.status_code == 200
    assert await _linhas(db, chave) == []
    r = await client.put(
        f"/api/threema/envios/{K.LOGISTICA_VIGIA_AUTOMACAO}/regras/{regra.id}", json=corpo
    )
    assert r.json()["detail"]["code"] == "sem_regras"


# ─────────────────────────────────────────────────────────── teste


@pytest.fixture
def _gateway(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "threema_gateway_id", "*TSTGW01")
    monkeypatch.setattr(s, "threema_gateway_secret", "s3cr3t-do-teste")
    monkeypatch.setattr(s, "threema_separate_chats", False)
    monkeypatch.setattr(s, "threema_context_channels", {})
    monkeypatch.setattr(s, "threema_registro_ativo", True)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_gateway")
async def test_envio_de_teste(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    admin = await _admin(make_user, auth_as)
    antes = await _legado_de_todos(db)
    # Envio que ainda usa a configuração antiga: nada sai (fase 1 sem envio novo).
    with respx.mock(base_url=threema.THREEMA_API_BASE, assert_all_called=False) as router:
        rota = router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid-0"))
        for chave in (K.LOGISTICA_INFORMAR, K.DENUNCIA_RELATORIO_DIARIO, K.OUVIDORIA_VIGIA_FLEX):
            r = await client.post(f"/api/threema/envios/{chave}/teste")
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["code"] == "teste_nao_migrado"
            assert r.json()["detail"]["onde_configurar"]
    assert rota.calls.call_count == 0
    # Migrado: o teste vai para a lista do Cadastro.
    migrar(monkeypatch, K.LOGISTICA_INFORMAR, K.DENUNCIA_RELATORIO_DIARIO)
    migrar(monkeypatch, K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA)
    u = c.usuarios
    await semeado(db, K.LOGISTICA_INFORMAR, u["harry"], u["heisenberg"])
    await semeado(db, K.DENUNCIA_RELATORIO_DIARIO, u["harry"], u["cairo_sa"], c.roma)
    await semeado(db, K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA)
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        rota = router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid-1"))
        r = await client.post(f"/api/threema/envios/{K.LOGISTICA_INFORMAR}/teste")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "enviados": ["harry potter", "heisenberg"],
        "falharam": [],
        "remetente": "logistica",
    }
    enviados = [dict(httpx.QueryParams(c.request.content.decode())) for c in rota.calls]
    assert [x["to"] for x in enviados] == [ID["harry"], ID["heisenberg"]]
    assert all(x["text"].startswith("[TESTE] Informar da Logística\n") for x in enviados)
    linhas = (
        (await db.execute(select(ThreemaEnvioRegistro).order_by(ThreemaEnvioRegistro.id)))
        .scalars()
        .all()
    )
    assert [(x.envio_chave, x.origem, x.via, x.ok) for x in linhas] == [
        (K.LOGISTICA_INFORMAR, "teste", "forcado", True)
    ] * 2
    assert linhas[0].titulo == "[TESTE] Informar da Logística"
    assert "s3cr3t" not in str([x.erro for x in linhas])
    # desligado também testa (o Relatório da Denúncia está desligado no cenário:
    # o robô dele, que é o interruptor do envio)
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(402, text="sem credito"))
        r = await client.post(f"/api/threema/envios/{K.DENUNCIA_RELATORIO_DIARIO}/teste")
    assert r.status_code == 200
    assert r.json()["falharam"] == ["harry potter", "cairo sa", "Roma"]
    # ninguém para receber, informativo
    r = await client.post(f"/api/threema/envios/{K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA}/teste")
    assert r.json()["detail"]["code"] == "sem_destinatarios"
    r = await client.post(f"/api/threema/envios/{K.PEDIDOS_CORRECAO_DIA_ENVIO}/teste")
    assert r.json()["detail"]["code"] == "envio_informativo"
    # remetente sem credencial: 502 com frase
    monkeypatch.setattr(get_settings(), "threema_gateway_id", "")
    r = await client.post(f"/api/threema/envios/{K.LOGISTICA_INFORMAR}/teste")
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "threema_config"
    assert await _legado_de_todos(db) == antes, "o teste não muda quem recebe"
    assert admin is not None


# ─────────────────────────────────────────────────────────── grupos


@pytest.mark.asyncio
async def test_grupos(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    await _admin(make_user, auth_as)
    r = await client.post(
        "/api/threema/grupos",
        json={
            "nome": "  Chamados   ML (Mac) ",
            "descricao": "quem recebe os avisos do frete",
            "membros": [_ref(c.usuarios["heisenberg"]), _ref(c.usuarios["cairo_sa"])],
        },
    )
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["nome"] == "Chamados ML (Mac)"
    r = await client.post("/api/threema/grupos", json={"nome": "chamados ml (mac)"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "grupo_existe"
    r = await client.post(
        "/api/threema/grupos",
        json={"nome": "Outro", "membros": [{"tipo": "grupo", "id": g["id"]}]},
    )
    assert r.status_code == 422, "grupo dentro de grupo não"
    r = await client.post(
        "/api/threema/grupos", json={"nome": "Outro", "membros": [_ref(c.usuarios["cairo"])]}
    )
    assert r.json()["detail"]["code"] == "pessoa_invalida"
    # editar membros e nome
    r = await client.patch(
        f"/api/threema/grupos/{g['id']}",
        json={"nome": "Chamados ML", "membros": [_ref(c.usuarios["heisenberg"]), _ref(c.roma)]},
    )
    assert r.status_code == 200, r.text
    lista = (await client.get("/api/threema/grupos")).json()
    assert [(m["nome"], m["tipo"]) for m in lista[0]["membros"]] == [
        ("heisenberg", "usuario"),
        ("Roma", "contato"),
    ]
    # usado por um envio do Mac: não apaga (409 com a lista)
    r = await client.patch(
        f"/api/threema/envios/{K.CHAMADOS_FRETE_ML_RELATORIO_NOITE}",
        json={"destinatarios": [{"tipo": "grupo", "id": g["id"]}]},
    )
    assert r.status_code == 200, r.text
    assert [n["nome"] for n in r.json()["quem_recebe"]["nomes"]] == ["heisenberg", "Roma"]
    r = await client.delete(f"/api/threema/grupos/{g['id']}")
    assert r.status_code == 409
    det = r.json()["detail"]
    assert det["code"] == "grupo_em_uso"
    assert det["envios"][0]["chave"] == K.CHAMADOS_FRETE_ML_RELATORIO_NOITE
    assert "Relatório da noite do frete ML" in det["message"]
    await client.patch(
        f"/api/threema/envios/{K.CHAMADOS_FRETE_ML_RELATORIO_NOITE}", json={"destinatarios": []}
    )
    assert (await client.delete(f"/api/threema/grupos/{g['id']}")).status_code == 204
    assert (await client.delete(f"/api/threema/grupos/{g['id']}")).status_code == 404


# ─────────────────────────────────────────────────────────── contatos


async def _espelho(db) -> str:
    row = (
        await db.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == "diretorio")
        )
    ).scalar_one_or_none()
    await db.refresh(row) if row is not None else None
    return row.recipients if row is not None else ""


@pytest.mark.asyncio
async def test_contatos(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    await _admin(make_user, auth_as)
    r = await client.post("/api/threema/contatos", json={"nome": "Lisboa", "threema_id": "abc"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "contato_invalido"
    r = await client.post(
        "/api/threema/contatos", json={"nome": "Outro Harry", "threema_id": ID["harry"]}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "id_de_usuario"
    assert "harry potter" in r.json()["detail"]["message"]
    r = await client.post(
        "/api/threema/contatos", json={"nome": "Roma 2", "threema_id": ID["roma"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "contato_existe"
    r = await client.post(
        "/api/threema/contatos",
        json={"nome": "Lisboa", "threema_id": " tstk0011 ", "observacao": "fornecedor"},
    )
    assert r.status_code == 201, r.text
    novo = r.json()
    assert novo["threema_id"] == "TS…" and novo["origem"] == "tela"
    assert "TSTK0011:Lisboa" in await _espelho(db)
    # o seletor antigo (Ouvidoria / Informar) enxerga o contato novo
    assert {"id": "TSTK0011", "nome": "Lisboa", "origem": "contato"} in await threema.diretorio(db)
    # renomear, desativar e reativar
    r = await client.patch(f"/api/threema/contatos/{novo['id']}", json={"nome": "Lisboa SP"})
    assert r.status_code == 200 and r.json()["nome"] == "Lisboa SP"
    r = await client.patch(f"/api/threema/contatos/{novo['id']}", json={"ativo": False})
    assert r.json()["ativo"] is False
    assert "TSTK0011" not in await _espelho(db)
    r = await client.patch(f"/api/threema/contatos/{novo['id']}", json={"ativo": True})
    assert r.json()["ativo"] is True
    r = await client.patch(f"/api/threema/contatos/{novo['id']}", json={"threema_id": ID["joana"]})
    assert r.json()["detail"]["code"] == "id_de_usuario"
    # em uso por um envio do Mac: não apaga
    await client.patch(f"/api/threema/envios/{_MAC}", json={"destinatarios": [_ref(c.roma)]})
    r = await client.delete(f"/api/threema/contatos/{c.roma.id}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "contato_em_uso"
    assert (await client.delete(f"/api/threema/contatos/{novo['id']}")).status_code == 204
    assert "TSTK0011" not in await _espelho(db)
    lista = (await client.get("/api/threema/contatos")).json()
    assert [x["nome"] for x in lista] == ["Roma"]
    assert lista[0]["usado_em"][0]["chave"] == _MAC
    assert ID["roma"] not in str(lista)


@pytest.mark.asyncio
async def test_contato_usado_pela_configuracao_antiga(db, monkeypatch, client, make_user, auth_as):
    """O Roma recebe HOJE pelo Correios da Ouvidoria e pelo relatório da
    Denúncia (listas antigas, com o ID). Fase 1, Cadastro vazio: a tela não
    pode dizer "não usado" — mostra o uso antigo e não deixa apagar."""
    c = await montar(db, monkeypatch)
    await _admin(make_user, auth_as)
    r = await client.get("/api/threema/pessoas")
    assert ID["roma"] not in r.text
    roma = {x["nome"]: x for x in r.json()["contatos"]}["Roma"]
    antigos = {u["chave"]: u for u in roma["usado_em"] if u.get("legado")}
    assert set(antigos) == {K.OUVIDORIA_VIGIA_CORREIOS, K.DENUNCIA_RELATORIO_DIARIO}
    assert antigos[K.OUVIDORIA_VIGIA_CORREIOS]["nome"].endswith("· configuração antiga")
    r = await client.delete(f"/api/threema/contatos/{c.roma.id}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "contato_em_uso_antigo"
    assert "configuração antiga" in r.json()["detail"]["message"]
    assert ID["roma"] not in r.text
    assert await db.get(ThreemaContato, c.roma.id) is not None


# ─────────────────────────────────────────────────────────── emergência


@pytest.mark.asyncio
async def test_emergencia(db, monkeypatch, client, make_user, auth_as):
    c = await montar(db, monkeypatch)
    admin = await _admin(make_user, auth_as)
    chave = K.LOGISTICA_VIGIA_AUTOMACAO
    migrar(monkeypatch, chave)
    await semeado(db, chave, c.usuarios["joana"])
    assert (await threema_envios.resolver(chave)).via == "cadastro"
    try:
        r = await client.post("/api/threema/emergencia", json={"usar_legado": True})
        assert r.status_code == 200, r.text
        assert r.json()["usar_legado"] is True and r.json()["alterado_por"] == admin.email
        res = await threema_envios.resolver(chave)
        assert res.via == "legado" and res.motivo == threema_envios.EMERGENCIA
        corpo = (await client.get("/api/threema/envios")).json()
        assert corpo["emergencia"]["usar_legado"] is True
    finally:
        r = await client.post("/api/threema/emergencia", json={"usar_legado": False})
    assert r.json()["usar_legado"] is False
    estado = await db.get(ThreemaCadastroEstado, 1, populate_existing=True)
    assert estado.usar_legado is False


# ─────────────────────────────────────────────────────────── regra de ouro


@pytest.mark.asyncio
async def test_editar_tudo_nao_muda_quem_recebe_nenhum_envio_do_servidor(
    db, monkeypatch, client, make_user, auth_as
):
    """Nesta fase nenhum envio do servidor está migrado: grupos, contatos,
    listas do Mac, regras e emergência não mudam quem recebe nem o liga."""
    c = await montar(db, monkeypatch)
    await _admin(make_user, auth_as)
    antes = await _legado_de_todos(db)
    resolvido_antes = {k: (await threema_envios.resolver(k)).ids for k in antes}
    g = (
        await client.post(
            "/api/threema/grupos",
            json={"nome": "Todos", "membros": [_ref(c.usuarios["joana"])]},
        )
    ).json()
    await client.post("/api/threema/contatos", json={"nome": "Lisboa", "threema_id": "TSTK0011"})
    await client.patch(f"/api/threema/contatos/{c.roma.id}", json={"ativo": False})
    for d in ENVIOS:
        if d.onde_roda == "mac" and d.gerenciado:
            r = await client.patch(
                f"/api/threema/envios/{d.chave}",
                json={"destinatarios": [{"tipo": "grupo", "id": g["id"]}], "ativo": False},
            )
            assert r.status_code == 200, r.text
    await client.post("/api/threema/emergencia", json={"usar_legado": True})
    await client.post("/api/threema/emergencia", json={"usar_legado": False})
    threema_envios.limpar_cache()
    assert await _legado_de_todos(db) == antes
    assert {k: (await threema_envios.resolver(k)).ids for k in antes} == resolvido_antes
    vias = [(await threema_envios.resolver(k)).via for k in antes]
    assert set(vias) == {"legado"}, "todo envio do servidor segue na configuração antiga"


# ─────────────────────────────────────────────────────────── Usuários


@pytest.mark.asyncio
async def test_usuarios_validam_o_formato_do_threema(db, client, make_user, auth_as):
    await _admin(make_user, auth_as)
    r = await client.post("/api/users", json={"email": "novo@davinci-test.com", "threema": "ABC"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "threema_invalido"
    r = await client.post(
        "/api/users", json={"email": "novo@davinci-test.com", "threema": " abcd1234 "}
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["threema"] == "ABCD1234"
    r = await client.patch(f"/api/users/{uid}", json={"threema": "ABCD12345"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "threema_invalido"
    r = await client.patch(f"/api/users/{uid}", json={"threema": "*abc1234"})
    assert r.status_code == 200 and r.json()["threema"] == "*ABC1234", "ID de Gateway vale"
    r = await client.patch(f"/api/users/{uid}", json={"threema": "  "})
    assert r.status_code == 200 and r.json()["threema"] is None, "vazio apaga"
    # outro campo sem mexer no Threema não valida o que já está lá
    u = await db.get(User, uuid.UUID(uid))
    u.threema = "antigo-torto"
    await db.commit()
    r = await client.patch(f"/api/users/{uid}", json={"name": "Novo Nome"})
    assert r.status_code == 200
