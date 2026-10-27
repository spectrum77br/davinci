# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""A tela "Números do WhatsApp" (Zap, RF10, Onda 4) — docs/zap/desenho.md §8.4, §8.5, §10.

A Graph API é de MENTIRA (`tests/zap_apoio.GraphFalso`, `httpx.MockTransport`):
nenhum teste fala com a Meta, e a chamada não prevista reprova.

O que se garante (27/10/2026):
- SÓ ADMINISTRADOR (decisão do Eduardo de 26/10/2026): quem não é admin leva
  404 em toda rota (o Zap nem existe para ela); o admin que só lê (fora do
  ATENDIMENTO_USUARIOS) vê, mas não mexe (403);
- a chave `atendimento_zap_cadastro_ativo` nasce desligada: desligada, a tela
  só mostra — e o "pausar"/"pausar todos" continuam valendo;
- o GET nunca devolve segredo (chave secreta, token, verify token, PIN); em
  repouso tudo fica cifrado; o Histórico não guarda nenhum deles — e "ver o
  código de verificação" e "ver o PIN" ficam no Histórico (`REVELACOES`);
- o cadastro da conta passa pela inspeção ESTRITA do token (sem escopo, de
  outro app, sem a WABA, inspeção que falha: nada é gravado; `expires_at=0`
  = nunca vence); fora de produção só entra WABA com número de teste;
- descobrir números (só GET), cadastrar o número (nasce em observar, sem
  envio), trocar token (o antigo só sai depois da conferência), trocar a
  chave secreta (rotina = 1 h; vazou = na hora), gerar outro verify token;
- inscrever (`POST subscribed_apps` + conferência pelo `GET`): alerta com
  outro app inscrito; testar (`webhook_configuration` apontando para outro
  lugar = alerta);
- "registrar": escondido fora de `migrando`, no caso A2, sem caso, depois de
  3 tentativas em 72 h e com o registro travado pela Meta (131031/133016) —
  a tentativa conta ANTES de chamar a Meta;
- ligar o envio só nas etapas que enviam; "pausar" e "pausar todos" na hora;
- reprocessar os eventos parados;
- a exclusão LGPD (§10.5) limpa contato, conversa, mensagens, mídias e
  eventos daquele contato — e não deixa cópia no Histórico.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text, update

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPar,
    AtendimentoZapApp,
    AtendimentoZapConta,
    AtendimentoZapContato,
    AtendimentoZapEvento,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
    HistoricoEvento,
    User,
    UserRole,
)
from app.security.cipher import decrypt_json
from app.security.jwt import issue_session_token
from app.services.atendimento import canais_externos, zap_contas, zap_midia
from tests.zap_apoio import (  # noqa: F401  (fixtures: zap, graph)
    API_URL,
    APP_ID,
    PNID,
    SEGREDO,
    WA_ID,
    WABA,
    cadastrar,
    conversa_do_numero,
    corpo,
    eventos,
    graph,
    gravar_corpo,
    processar,
    zap,
)

URL = "/api/atendimento/zap"
V = "v26.0"
TOKEN = "EAAzapTOKENdeMENTIRAcadastro000000000"
TOKEN_NOVO = "EAAzapTOKENnovoDEMENTIRA11111111111"
CHAVE = "chave-secreta-de-mentira-0123456789ab"
PIN = "246813"
NOVO_APP = "910000000000777"
NOVA_WABA = "920000000000777"
NUM_TESTE = "930000000000777"
DISPLAY_TESTE = "+1 555-169-8240"
PODE_TUDO = {"atendimento": {"view": True, "edit": True, "delete": True}}
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"j" * 300


@pytest.fixture
def cadastro(zap, monkeypatch):
    """A chave da tela LIGADA (nasce desligada; tem teste próprio)."""
    monkeypatch.setattr(zap.settings, "atendimento_zap_cadastro_ativo", True)
    monkeypatch.setattr(zap.settings, "atendimento_usuarios", "")
    return zap


@pytest_asyncio.fixture
async def admin(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    return u


@pytest.fixture
def uploads(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    return tmp_path


def _logar(client, user) -> None:
    """Login de VERDADE (cookie): o Histórico só registra quem passou pela autenticação."""
    token, _, _ = issue_session_token(sub=user.open_id, role=user.role.value)
    client.cookies.set(get_settings().cookie_name, token)


def _debug(*, app_id: str = NOVO_APP, waba: str = NOVA_WABA, **dados) -> dict:
    base = {
        "app_id": app_id,
        "type": "SYSTEM_USER",
        "is_valid": True,
        "expires_at": 0,
        "scopes": ["whatsapp_business_messaging", "whatsapp_business_management"],
        "granular_scopes": [
            {"scope": "whatsapp_business_messaging", "target_ids": [waba]},
            {"scope": "whatsapp_business_management", "target_ids": [waba]},
        ],
        "user_id": "61500000000777",
    }
    base.update(dados)
    return {"data": base}


def _meta_da_conta(
    graph,
    *,
    waba: str = NOVA_WABA,
    numeros: list[dict] | None = None,
    debug: dict | None = None,
) -> None:
    graph.quando("GET", f"/{V}/debug_token", debug or _debug(waba=waba))
    graph.quando("GET", f"/{V}/{waba}", {"id": waba, "name": "Conta de teste"})
    graph.quando(
        "GET",
        f"/{V}/{waba}/phone_numbers",
        {
            "data": numeros
            if numeros is not None
            else [
                {
                    "id": NUM_TESTE,
                    "display_phone_number": DISPLAY_TESTE,
                    "verified_name": "Test Number",
                    "name_status": "APPROVED",
                    "quality_rating": "GREEN",
                }
            ]
        },
    )


async def _novo_app(client, *, app_id: str = NOVO_APP) -> dict:
    r = await client.post(
        f"{URL}/apps", json={"nome": "DaVinci Zap Teste", "app_id": app_id, "app_secret": CHAVE}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _nova_conta(client, graph, app: dict, *, token: str = TOKEN) -> dict:
    _meta_da_conta(graph)
    r = await client.post(
        f"{URL}/contas",
        json={"app_ref": app["id"], "waba_id": NOVA_WABA, "access_token": token},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _novo_numero(client, graph, conta: dict) -> dict:
    _meta_da_conta(graph)
    r = await client.post(
        f"{URL}/numeros",
        json={"conta_ref": conta["conta"]["id"], "phone_number_id": NUM_TESTE},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _numero(db, pnid: str = NUM_TESTE) -> AtendimentoZapNumero:
    return (
        await db.execute(
            select(AtendimentoZapNumero)
            .where(AtendimentoZapNumero.phone_number_id == pnid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _tudo_do_historico(db) -> str:
    return (
        await db.execute(
            text(
                "SELECT coalesce(string_agg(t::text, ' '), '') FROM ("
                " SELECT row_to_json(a) AS t FROM historico_alteracao a"
                " UNION ALL SELECT row_to_json(e) FROM historico_evento e) x"
            )
        )
    ).scalar_one()


async def _acoes(db) -> list[HistoricoEvento]:
    return list(
        (
            await db.execute(
                select(HistoricoEvento)
                .order_by(HistoricoEvento.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


# ═══════════════════════ quem entra ══════════════════════════════════════


_ROTAS_DA_TELA = (
    ("GET", "/numeros"),
    ("POST", "/apps"),
    ("GET", "/apps/00000000-0000-0000-0000-000000000000/verify-token"),
    ("POST", "/contas"),
    ("POST", "/numeros/pausar-todos"),
    ("PATCH", "/numeros/00000000-0000-0000-0000-000000000000"),
    ("GET", "/numeros/00000000-0000-0000-0000-000000000000/pin"),
    ("POST", "/contatos/00000000-0000-0000-0000-000000000000/apagar"),
    ("POST", "/eventos/reprocessar"),
)


async def test_quem_nao_e_admin_nao_ve_o_zap(client, cadastro, make_user, auth_as, monkeypatch):
    """Na fase de observação, a trava da CAIXA vem antes (escrita de quem só lê
    = 403 "só leitura", como em toda rota de /api/atendimento); o GET cai na
    do Zap: 404. Sem a fase de observação, 404 em TODA rota."""
    from app.routers import atendimento as rota

    u = await make_user(permissions=PODE_TUDO, role=UserRole.USER)
    auth_as(u)
    for metodo, caminho in _ROTAS_DA_TELA:
        r = await client.request(metodo, f"{URL}{caminho}", json={})
        if metodo == "GET":
            assert r.status_code == 404, (metodo, caminho, r.status_code)
            assert r.json()["detail"]["code"] == "zap_nao_encontrado"
        else:
            assert r.status_code == 403, (metodo, caminho, r.status_code)
            assert r.json()["detail"]["code"] == "atendimento_so_leitura"
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    for metodo, rota_ in (
        ("GET", "/numeros"),
        ("POST", "/apps"),
        ("GET", "/apps/00000000-0000-0000-0000-000000000000/verify-token"),
        ("POST", "/contas"),
        ("POST", "/numeros/pausar-todos"),
        ("PATCH", "/numeros/00000000-0000-0000-0000-000000000000"),
        ("GET", "/numeros/00000000-0000-0000-0000-000000000000/pin"),
        ("POST", "/contatos/00000000-0000-0000-0000-000000000000/apagar"),
        ("POST", "/eventos/reprocessar"),
    ):
        r = await client.request(metodo, f"{URL}{rota_}", json={})
        assert r.status_code == 404, (metodo, rota_, r.status_code)
        assert r.json()["detail"]["code"] == "zap_nao_encontrado"


async def test_sem_login_401(client, cadastro):
    assert (await client.get(f"{URL}/numeros")).status_code == 401


async def test_admin_que_so_le_ve_mas_nao_mexe(
    client, cadastro, db, make_user, auth_as, monkeypatch
):
    await cadastrar(db)
    monkeypatch.setattr(cadastro.settings, "atendimento_usuarios", "outro@davinci-test.com")
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    r = await client.get(f"{URL}/numeros")
    assert r.status_code == 200, r.text
    assert [n["phone_number_id"] for n in r.json()["numeros"]] == [PNID]
    r = await client.post(f"{URL}/numeros/pausar-todos")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "atendimento_so_leitura"


# ═══════════════════════ a chave da tela ═════════════════════════════════


async def test_chave_desligada_so_mostra_mas_pausar_vale(client, zap, db, admin, graph):
    assert get_settings().atendimento_zap_cadastro_ativo is False
    _app, _conta, numero = await cadastrar(db)
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == numero.id)
        .values(envio_ligado=True)
    )
    await db.commit()
    r = await client.get(f"{URL}/numeros")
    assert r.status_code == 200
    assert r.json()["chaves"]["cadastro"] is False
    r = await client.post(
        f"{URL}/apps", json={"nome": "x", "app_id": NOVO_APP, "app_secret": CHAVE}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "zap_cadastro_desligado"
    r = await client.patch(f"{URL}/numeros/{numero.id}", json={"teto_dia": 50})
    assert r.status_code == 409
    # Nem o que só LÊ na Meta (com o token guardado) sai com a chave desligada.
    assert (await client.post(f"{URL}/numeros/{numero.id}/testar")).status_code == 409
    assert (await client.get(f"{URL}/contas/{_conta.id}/numeros-da-meta")).status_code == 409
    assert (await client.post(f"{URL}/contas/{_conta.id}/conferir-inscricao")).status_code == 409
    # Parar nunca espera chave.
    r = await client.post(f"{URL}/numeros/{numero.id}/pausar")
    assert r.status_code == 200, r.text
    assert r.json()["envio_ligado"] is False
    assert (await client.post(f"{URL}/numeros/pausar-todos")).status_code == 200
    assert graph.chamadas == []


# ═══════════════════════ o app ═══════════════════════════════════════════


async def test_cadastrar_app_cifra_e_nunca_devolve_o_segredo(client, cadastro, db, admin):
    app = await _novo_app(client)
    assert app["app_id"] == NOVO_APP
    assert app["webhook_url"] == f"{API_URL}/api/webhooks/meta/whatsapp/{NOVO_APP}"
    # Fora de produção todo app é de TESTE (a chave de produção nunca entra no computador).
    assert app["teste"] is True
    assert CHAVE not in json.dumps(app)
    linha = (
        await db.execute(select(AtendimentoZapApp).where(AtendimentoZapApp.app_id == NOVO_APP))
    ).scalar_one()
    assert CHAVE.encode() not in linha.segredo_enc
    assert decrypt_json(linha.segredo_enc)["atual"] == CHAVE
    verify = decrypt_json(linha.verify_token_enc)["verify_token"]
    assert len(verify) >= 40
    tela = (await client.get(f"{URL}/numeros")).text
    assert CHAVE not in tela and verify not in tela
    # O mesmo app_id de novo: recusado (sem trocar a chave de quem já existe).
    r = await client.post(
        f"{URL}/apps", json={"nome": "y", "app_id": NOVO_APP, "app_secret": "outra"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "zap_app_ja_existe"


@pytest.mark.parametrize(
    "corpo_ruim",
    [
        {"nome": "x", "app_id": "abc", "app_secret": CHAVE},
        {"nome": "x", "app_id": NOVO_APP, "app_secret": "   "},
    ],
)
async def test_cadastrar_app_recusa_formato(client, cadastro, admin, corpo_ruim):
    r = await client.post(f"{URL}/apps", json=corpo_ruim)
    assert r.status_code in (409, 422)


async def test_webhook_e_codigo_de_verificacao(client, cadastro, db, admin):
    app = await _novo_app(client)
    r = await client.get(f"{URL}/apps/{app['id']}/webhook")
    assert r.status_code == 200
    assert r.json()["url"] == f"{API_URL}/api/webhooks/meta/whatsapp/{NOVO_APP}"
    assert "messages" in r.json()["campos"] and "user_preferences" in r.json()["campos"]
    assert "verify_token" not in r.json()
    r = await client.get(f"{URL}/apps/{app['id']}/verify-token")
    primeiro = r.json()["verify_token"]
    assert len(primeiro) >= 40
    # "Gerar outro": o POST não devolve o código (só o GET, sob clique, revela).
    r = await client.post(f"{URL}/apps/{app['id']}/verify-token")
    assert r.status_code == 200 and "verify_token" not in r.json()
    segundo = (await client.get(f"{URL}/apps/{app['id']}/verify-token")).json()["verify_token"]
    assert segundo != primeiro


async def test_trocar_a_chave_rotina_e_vazou(client, cadastro, db, admin):
    app = await _novo_app(client)
    r = await client.post(
        f"{URL}/apps/{app['id']}/segredo", json={"app_secret": "chave-nova", "motivo": "rotina"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["anterior_ate"] is not None
    linha = (
        await db.execute(
            select(AtendimentoZapApp)
            .where(AtendimentoZapApp.app_id == NOVO_APP)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    s = decrypt_json(linha.segredo_enc)
    assert (s["atual"], s["anterior"]) == ("chave-nova", CHAVE)
    r = await client.post(
        f"{URL}/apps/{app['id']}/segredo", json={"app_secret": "chave-3", "motivo": "vazou"}
    )
    assert r.json()["anterior_ate"] is None
    await db.refresh(linha)
    s = decrypt_json(linha.segredo_enc)
    assert (s["atual"], s["anterior"]) == ("chave-3", None)
    r = await client.post(
        f"{URL}/apps/{app['id']}/segredo", json={"app_secret": "x", "motivo": "qualquer"}
    )
    assert r.status_code == 422


# ═══════════════════════ a conta (WABA) ══════════════════════════════════


async def test_cadastrar_conta_confere_o_token_e_acha_os_numeros(
    client, cadastro, db, admin, graph
):
    app = await _novo_app(client)
    r = await _nova_conta(client, graph, app)
    conta = r["conta"]
    assert conta["waba_id"] == NOVA_WABA and conta["status"] == "ok"
    assert conta["token"]["ok"] is True and conta["token"]["nunca_vence"] is True
    assert "nunca vence" in conta["token"]["frase"]
    assert [n["phone_number_id"] for n in r["numeros"]] == [NUM_TESTE]
    assert r["numeros"][0]["cadastrado"] is False and r["numeros"][0]["teste"] is True
    assert TOKEN not in json.dumps(r)
    # Só GET na Meta; o token no HEADER.
    assert {c.method for c in graph.chamadas} == {"GET"}
    assert all(c.headers["authorization"] == f"OAuth {TOKEN}" for c in graph.chamadas)
    linha = (
        await db.execute(
            select(AtendimentoZapConta).where(AtendimentoZapConta.waba_id == NOVA_WABA)
        )
    ).scalar_one()
    assert TOKEN.encode() not in linha.token_enc
    assert zap_contas.token_da_conta(linha) == TOKEN
    assert linha.token_nunca_vence is True and linha.token_conferido_em is not None
    # Descobrir de novo (só GET).
    r = await client.get(f"{URL}/contas/{conta['id']}/numeros-da-meta")
    assert [n["phone_number_id"] for n in r.json()["numeros"]] == [NUM_TESTE]
    assert (await client.get(f"{URL}/numeros")).text.count(TOKEN) == 0


async def test_conta_com_validade_mostra_quando_vence(client, cadastro, db, admin, graph):
    app = await _novo_app(client)
    vence = int((datetime.now(UTC) + timedelta(days=60)).timestamp())
    _meta_da_conta(graph, debug=_debug(expires_at=vence))
    r = await client.post(
        f"{URL}/contas",
        json={"app_ref": app["id"], "waba_id": NOVA_WABA, "access_token": TOKEN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["conta"]["token"]["nunca_vence"] is False
    assert r.json()["conta"]["token"]["expira_em"] is not None


@pytest.mark.parametrize(
    ("debug", "trecho"),
    [
        (_debug(scopes=["whatsapp_business_messaging"]), "escopo"),
        (_debug(app_id="910000000000999"), "OUTRO app"),
        (
            _debug(
                granular_scopes=[
                    {"scope": "whatsapp_business_messaging", "target_ids": ["999"]},
                    {"scope": "whatsapp_business_management", "target_ids": ["999"]},
                ]
            ),
            "não enxerga esta conta",
        ),
        (_debug(is_valid=False), "não é válido"),
    ],
)
async def test_token_recusado_nao_grava_nada(client, cadastro, db, admin, graph, debug, trecho):
    app = await _novo_app(client)
    _meta_da_conta(graph, debug=debug)
    r = await client.post(
        f"{URL}/contas",
        json={"app_ref": app["id"], "waba_id": NOVA_WABA, "access_token": TOKEN},
    )
    assert r.status_code == 409, r.text
    assert trecho in r.json()["detail"]["detail"]
    assert TOKEN not in r.text
    assert (await db.scalar(select(AtendimentoZapConta.id).limit(1))) is None


async def test_inspecao_que_falha_e_erro_e_nada_grava(client, cadastro, db, admin, graph):
    app = await _novo_app(client)
    graph.quando("GET", f"/{V}/debug_token", httpx.ConnectTimeout("tempo esgotado"))
    r = await client.post(
        f"{URL}/contas",
        json={"app_ref": app["id"], "waba_id": NOVA_WABA, "access_token": TOKEN},
    )
    assert r.status_code == 409
    assert "não deu para conferir o token" in r.json()["detail"]["detail"]
    assert (await db.scalar(select(AtendimentoZapConta.id).limit(1))) is None


async def test_fora_de_producao_so_entra_waba_de_teste(client, cadastro, db, admin, graph):
    app = await _novo_app(client)
    _meta_da_conta(
        graph,
        numeros=[
            {"id": NUM_TESTE, "display_phone_number": DISPLAY_TESTE},
            {"id": "930000000000888", "display_phone_number": "+55 11 98888-7777"},
        ],
    )
    r = await client.post(
        f"{URL}/contas",
        json={"app_ref": app["id"], "waba_id": NOVA_WABA, "access_token": TOKEN},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "zap_so_teste_fora_de_producao"
    # O token que enxerga OUTRA WABA também (pode ser a de produção).
    _meta_da_conta(
        graph,
        debug=_debug(
            granular_scopes=[
                {"scope": "whatsapp_business_messaging", "target_ids": [NOVA_WABA, "920999"]},
                {"scope": "whatsapp_business_management", "target_ids": [NOVA_WABA]},
            ]
        ),
    )
    r = await client.post(
        f"{URL}/contas",
        json={"app_ref": app["id"], "waba_id": NOVA_WABA, "access_token": TOKEN},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "zap_so_teste_fora_de_producao"
    assert (await db.scalar(select(AtendimentoZapConta.id).limit(1))) is None


_SEM_ALVOS = [
    {"scope": "whatsapp_business_messaging"},
    {"scope": "whatsapp_business_management"},
]


async def test_token_sem_target_ids_passa_pela_leitura_da_waba(
    client, cadastro, db, admin, graph
):
    """O token do usuário do sistema que a Meta entregou em 27/10/2026 vem SEM
    `target_ids`: passa (decisão do orquestrador), mas só com o GET da WABA dando
    certo; "outras WABAs" fica não sabido (None, não 0) e a tela avisa."""
    app = await _novo_app(client)
    _meta_da_conta(graph, debug=_debug(granular_scopes=_SEM_ALVOS))
    r = await client.post(
        f"{URL}/contas",
        json={"app_ref": app["id"], "waba_id": NOVA_WABA, "access_token": TOKEN},
    )
    assert r.status_code == 200, r.text
    assert "não disse quais contas" in r.json()["conta"]["token"]["frase"]
    assert any(c.url.path == f"/{V}/{NOVA_WABA}" for c in graph.chamadas)
    linha = (
        await db.execute(
            select(AtendimentoZapConta).where(AtendimentoZapConta.waba_id == NOVA_WABA)
        )
    ).scalar_one()
    assert linha.saude["alvos_detalhados"] is False
    assert linha.saude["outras_wabas"] is None
    await db.delete(linha)
    await db.commit()
    # A Meta recusa a WABA com esse token: nada é gravado.
    _meta_da_conta(graph, debug=_debug(granular_scopes=_SEM_ALVOS))
    graph.quando(
        "GET",
        f"/{V}/{NOVA_WABA}",
        (400, {"error": {"code": 100, "message": "Unsupported get request"}}),
    )
    r = await client.post(
        f"{URL}/contas",
        json={"app_ref": app["id"], "waba_id": NOVA_WABA, "access_token": TOKEN},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "zap_token_recusado"
    assert TOKEN not in r.text
    assert (await db.scalar(select(AtendimentoZapConta.id).limit(1))) is None


async def test_trocar_token_so_troca_depois_de_conferir(client, cadastro, db, admin, graph):
    app = await _novo_app(client)
    conta = (await _nova_conta(client, graph, app))["conta"]
    # O token novo de OUTRO app: recusado, e o antigo continua.
    _meta_da_conta(graph, debug=_debug(app_id="910000000000999"))
    r = await client.post(f"{URL}/contas/{conta['id']}/token", json={"access_token": TOKEN_NOVO})
    assert r.status_code == 409
    linha = await db.get(AtendimentoZapConta, conta["id"], populate_existing=True)
    assert zap_contas.token_da_conta(linha) == TOKEN
    # O certo: troca, e a tela lembra de revogar o antigo.
    _meta_da_conta(graph)
    r = await client.post(f"{URL}/contas/{conta['id']}/token", json={"access_token": TOKEN_NOVO})
    assert r.status_code == 200, r.text
    assert "revogue" in r.json()["lembrete"].lower()
    assert TOKEN_NOVO not in r.text
    linha = await db.get(AtendimentoZapConta, conta["id"], populate_existing=True)
    assert zap_contas.token_da_conta(linha) == TOKEN_NOVO
    assert linha.token_trocado_em is not None and linha.status == "ok"


# ═══════════════════════ inscrever e testar ══════════════════════════════


async def test_inscrever_confere_quem_ficou_inscrito(client, cadastro, db, admin, graph):
    app = await _novo_app(client)
    conta = (await _nova_conta(client, graph, app))["conta"]
    graph.quando("POST", f"/{V}/{NOVA_WABA}/subscribed_apps", {"success": True})
    graph.quando(
        "GET",
        f"/{V}/{NOVA_WABA}/subscribed_apps",
        {
            "data": [
                {"whatsapp_business_api_data": {"id": NOVO_APP, "name": "DaVinci Zap Teste"}},
                {"whatsapp_business_api_data": {"id": "777000111", "name": "Duoke"}},
            ]
        },
    )
    r = await client.post(f"{URL}/contas/{conta['id']}/inscrever")
    assert r.status_code == 200, r.text
    assert r.json()["inscrito"] is False
    assert [a["nome"] for a in r.json()["outros_apps"]] == ["Duoke"]
    assert "outro lugar" in r.json()["frase"] or "Duoke" in r.json()["frase"]
    linha = await db.get(AtendimentoZapConta, conta["id"], populate_existing=True)
    assert linha.inscrito_em is None
    assert linha.saude["outros_apps"][0]["id"] == "777000111"
    tela = (await client.get(f"{URL}/numeros")).json()
    assert any(a["codigo"] == "outro_app_inscrito" for a in tela["alertas"])
    # Só o nosso: inscrito.
    graph.quando(
        "GET",
        f"/{V}/{NOVA_WABA}/subscribed_apps",
        {"data": [{"whatsapp_business_api_data": {"id": NOVO_APP, "name": "DaVinci Zap Teste"}}]},
    )
    r = await client.post(f"{URL}/contas/{conta['id']}/inscrever")
    assert r.json()["inscrito"] is True and r.json()["outros_apps"] == []
    linha = await db.get(AtendimentoZapConta, conta["id"], populate_existing=True)
    assert linha.inscrito_em is not None
    metodos = [c.method for c in graph.chamadas if c.url.path.endswith("/subscribed_apps")]
    assert metodos == ["POST", "GET", "POST", "GET"]


async def test_cadastrar_numero_nasce_em_observar_sem_envio(client, cadastro, db, admin, graph):
    app = await _novo_app(client)
    conta = await _nova_conta(client, graph, app)
    n = await _novo_numero(client, graph, conta)
    assert n["phone_number_id"] == NUM_TESTE
    assert n["envio_ligado"] is False and n["modo"] == "observar"
    assert n["teste"] is True and n["etapa"] == "teste"
    numero = await _numero(db)
    canal = await db.get(AtendimentoCanal, numero.canal_id)
    assert canal.externo_ref == f"zap:{NUM_TESTE}" and canal.modo == "observar"
    # Número que não é da conta: recusado.
    _meta_da_conta(graph)
    r = await client.post(
        f"{URL}/numeros",
        json={"conta_ref": conta["conta"]["id"], "phone_number_id": "930000000000999"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "zap_numero_fora_da_conta"
    # O mesmo de novo: recusado.
    _meta_da_conta(graph)
    r = await client.post(
        f"{URL}/numeros",
        json={"conta_ref": conta["conta"]["id"], "phone_number_id": NUM_TESTE},
    )
    assert r.json()["detail"]["code"] == "zap_numero_ja_existe"


def _saude_do_numero(graph, pnid: str = NUM_TESTE, *, webhook: dict | None = None, **campos):
    dados = {
        "id": pnid,
        "display_phone_number": DISPLAY_TESTE,
        "verified_name": "Test Number",
        "name_status": "APPROVED",
        "quality_rating": "GREEN",
        "messaging_limit_tier": "TIER_1K",
        "code_verification_status": "VERIFIED",
        "throughput": {"level": "STANDARD"},
        "webhook_configuration": webhook
        or {"application": f"{API_URL}/api/webhooks/meta/whatsapp/{NOVO_APP}"},
    }
    dados.update(campos)
    graph.quando("GET", f"/{V}/{pnid}", dados)


async def test_testar_conexao_le_a_saude_e_alerta_webhook_em_outro_lugar(
    client, cadastro, db, admin, graph
):
    app = await _novo_app(client)
    conta = await _nova_conta(client, graph, app)
    n = await _novo_numero(client, graph, conta)
    _saude_do_numero(graph)
    r = await client.post(f"{URL}/numeros/{n['id']}/testar")
    assert r.status_code == 200, r.text
    corpo_r = r.json()
    assert corpo_r["webhook_outro"] == []
    assert corpo_r["numero"]["qualidade"] == "GREEN"
    assert corpo_r["numero"]["limite"] == "TIER_1K"
    pedido = next(c for c in graph.chamadas if c.url.path == f"/{V}/{NUM_TESTE}")
    assert "webhook_configuration" in pedido.url.params["fields"]
    # O Duoke deixou um endereço ALTERNATIVO no número: as mensagens vão para lá.
    _saude_do_numero(
        graph,
        webhook={
            "application": f"{API_URL}/api/webhooks/meta/whatsapp/{NOVO_APP}",
            "phone_number": "https://duoke.example/webhook",
        },
    )
    r = await client.post(f"{URL}/numeros/{n['id']}/testar")
    assert r.json()["webhook_outro"] == ["phone_number"]
    assert "outro lugar" in r.json()["frase"]
    tela = (await client.get(f"{URL}/numeros")).json()
    assert any(a["codigo"] == "webhook_em_outro_lugar" for a in tela["alertas"])


async def test_testar_fora_da_api_e_webhook_nao_conferido(client, cadastro, db, admin, graph):
    """platform_type ≠ CLOUD_API não é "Conexão ok"; a leitura que volta sem o
    webhook (a Meta recusou campos, code 100) não apaga o "fora" anterior — revisão
    de 27/10/2026."""
    app = await _novo_app(client)
    conta = await _nova_conta(client, graph, app)
    n = await _novo_numero(client, graph, conta)
    _saude_do_numero(graph, platform_type="NOT_APPLICABLE", status="CONNECTED")
    r = await client.post(f"{URL}/numeros/{n['id']}/testar")
    assert r.status_code == 200, r.text
    assert "NÃO está registrado" in r.json()["frase"] and "Registrar" in r.json()["frase"]
    assert "vêm para o DaVinci" not in r.json()["frase"]
    assert r.json()["numero"]["conexao"]["codigo"] == "nao_registrado"
    # O Duoke deixou o endereço alternativo (conferido) …
    _saude_do_numero(
        graph,
        platform_type="CLOUD_API",
        webhook={"phone_number": "https://duoke.example/webhook"},
    )
    assert (await client.post(f"{URL}/numeros/{n['id']}/testar")).json()["webhook_outro"] == [
        "phone_number"
    ]
    # … e depois a Meta recusa um campo (code 100) e só o mínimo responde, SEM webhook.
    pedidos: list[str] = []

    def _recusa_campos(request):
        campos = request.url.params["fields"]
        pedidos.append(campos)
        if campos != "id,display_phone_number,verified_name,quality_rating":
            return (400, {"error": {"code": 100, "message": "Tried accessing nonexisting field"}})
        return {"id": NUM_TESTE, "display_phone_number": DISPLAY_TESTE, "quality_rating": "GREEN"}

    graph.quando("GET", f"/{V}/{NUM_TESTE}", _recusa_campos)
    r = await client.post(f"{URL}/numeros/{n['id']}/testar")
    assert r.status_code == 200, r.text
    # Tentou o mínimo COM o webhook antes do mínimo puro.
    assert pedidos[1].endswith(",webhook_configuration") and len(pedidos) == 3
    assert r.json()["webhook_outro"] == ["phone_number"], "o fora anterior fica"
    assert "outro lugar" in r.json()["frase"] and "vêm para o DaVinci" not in r.json()["frase"]
    linha = await _numero(db)
    assert linha.saude["webhook_conferido"] is False
    tela = (await client.get(f"{URL}/numeros")).json()
    codigos = {a["codigo"] for a in tela["alertas"]}
    assert {"webhook_em_outro_lugar", "webhook_nao_conferido"} <= codigos


async def test_testar_com_token_caido_marca_a_conta(client, cadastro, db, admin, graph):
    app = await _novo_app(client)
    conta = await _nova_conta(client, graph, app)
    n = await _novo_numero(client, graph, conta)
    graph.quando(
        "GET",
        f"/{V}/{NUM_TESTE}",
        (401, {"error": {"code": 190, "message": f"Invalid OAuth access token {TOKEN}"}}),
    )
    r = await client.post(f"{URL}/numeros/{n['id']}/testar")
    assert r.status_code == 409
    assert TOKEN not in r.text
    assert "token novo" in r.json()["detail"]["detail"]
    linha = await db.get(AtendimentoZapConta, conta["conta"]["id"], populate_existing=True)
    assert linha.status == "token_invalido"


# ═══════════════════════ o número: etapa, envio, pausar ══════════════════


async def test_ligar_envio_so_nas_etapas_que_enviam(client, cadastro, db, admin):
    _app, _conta, numero = await cadastrar(db)
    r = await client.patch(f"{URL}/numeros/{numero.id}", json={"etapa": "migrando"})
    assert r.status_code == 200, r.text
    r = await client.patch(f"{URL}/numeros/{numero.id}", json={"envio_ligado": True})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "zap_etapa_nao_envia"
    r = await client.patch(
        f"{URL}/numeros/{numero.id}",
        json={
            "etapa": "ativo",
            "envio_ligado": True,
            "teto_dia": 120,
            "nome_na_barra": "Charlots · Zap novo",
            "destinos_teste": ["+55 (11) 9876-5432"],
            "caso_migracao": "A2",
        },
    )
    assert r.status_code == 200, r.text
    n = r.json()
    assert (n["etapa"], n["envio_ligado"], n["teto_dia"], n["caso_migracao"]) == (
        "ativo",
        True,
        120,
        "A2",
    )
    # A lista de teste guardada NORMALIZADA (com o 9º dígito).
    assert n["destinos_teste"] == ["5511998765432"]
    linha = await _numero(db, PNID)
    assert linha.migrado_em is not None
    canal = await db.get(AtendimentoCanal, linha.canal_id, populate_existing=True)
    assert canais_externos.nome_do_canal(canal) == "Charlots · Zap novo"
    # Pausado: o envio sai junto.
    r = await client.patch(f"{URL}/numeros/{numero.id}", json={"etapa": "pausado"})
    assert r.json()["envio_ligado"] is False
    # O teto volta ao padrão com null.
    r = await client.patch(f"{URL}/numeros/{numero.id}", json={"teto_dia": None})
    assert r.json()["teto_dia"] is None
    assert r.json()["teto_efetivo"] == get_settings().atendimento_zap_max_dia_numero
    for ruim in ({"etapa": "voando"}, {"caso_migracao": "C"}, {"teto_dia": -1}):
        assert (await client.patch(f"{URL}/numeros/{numero.id}", json=ruim)).status_code == 422


async def test_pausar_e_pausar_todos_valem_na_hora(client, cadastro, db, admin):
    _app, _conta, n1 = await cadastrar(db)
    _app, _conta, n2 = await cadastrar(db, pnid="930000000000002")
    await db.execute(update(AtendimentoZapNumero).values(envio_ligado=True))
    await db.commit()
    r = await client.post(f"{URL}/numeros/{n1.id}/pausar")
    assert r.json()["envio_ligado"] is False
    assert (await _numero(db, "930000000000002")).envio_ligado is True
    r = await client.post(f"{URL}/numeros/pausar-todos")
    assert r.status_code == 200 and r.json()["pausados"] == 1
    assert (await _numero(db, "930000000000002")).envio_ligado is False


# ═══════════════════════ registrar e o PIN ═══════════════════════════════


async def _migrando(client, numero, caso: str | None = "A") -> None:
    corpo_p = {"etapa": "migrando", "pin": PIN}
    if caso:
        corpo_p["caso_migracao"] = caso
    r = await client.patch(f"{URL}/numeros/{numero.id}", json=corpo_p)
    assert r.status_code == 200, r.text


async def test_registrar_escondido_fora_de_migrando_no_a2_e_sem_caso(client, cadastro, db, admin):
    _app, _conta, numero = await cadastrar(db)
    tela = lambda r: next(n for n in r.json()["numeros"] if n["id"] == str(numero.id))  # noqa: E731
    await client.patch(f"{URL}/numeros/{numero.id}", json={"etapa": "preparando"})
    r = await client.get(f"{URL}/numeros")
    assert tela(r)["registro"]["pode"] is False
    assert "Migrando" in tela(r)["registro"]["motivo"]
    await _migrando(client, numero, caso=None)
    assert "caso" in tela(await client.get(f"{URL}/numeros"))["registro"]["motivo"]
    await _migrando(client, numero, caso="A2")
    registro = tela(await client.get(f"{URL}/numeros"))["registro"]
    assert registro["pode"] is False and "A2" in registro["motivo"]
    r = await client.post(f"{URL}/numeros/{numero.id}/registrar", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "zap_registro_bloqueado"
    await _migrando(client, numero, caso="B")
    assert tela(await client.get(f"{URL}/numeros"))["registro"]["pode"] is True


async def test_registrar_uma_por_clique_e_para_em_3(client, cadastro, db, admin, graph):
    _app, _conta, numero = await cadastrar(db)
    await _migrando(client, numero, caso="A")
    graph.quando(
        "POST",
        f"/{V}/{PNID}/register",
        (400, {"error": {"code": 136025, "message": "Código de verificação errado"}}),
    )
    for _ in range(3):
        r = await client.post(f"{URL}/numeros/{numero.id}/registrar", json={})
        assert r.status_code == 409, r.text
    assert len([c for c in graph.chamadas if c.url.path.endswith("/register")]) == 3
    corpo_meta = json.loads(graph.chamadas[-1].content)
    assert corpo_meta == {"messaging_product": "whatsapp", "pin": PIN}
    linha = await _numero(db, PNID)
    assert len(linha.registro_tentativas) == 3
    r = await client.post(f"{URL}/numeros/{numero.id}/registrar", json={})
    assert r.status_code == 409 and "3 tentativas" in r.json()["detail"]["detail"]
    assert len([c for c in graph.chamadas if c.url.path.endswith("/register")]) == 3


async def test_tentativa_conta_mesmo_com_a_meta_fora(client, cadastro, db, admin, graph):
    _app, _conta, numero = await cadastrar(db)
    await _migrando(client, numero, caso="B")
    graph.quando("POST", f"/{V}/{PNID}/register", httpx.ReadTimeout("sem resposta"))
    r = await client.post(f"{URL}/numeros/{numero.id}/registrar", json={})
    assert r.status_code == 409
    assert len((await _numero(db, PNID)).registro_tentativas) == 1


@pytest.mark.parametrize("code", [131031, 133016])
async def test_meta_trava_o_registro(client, cadastro, db, admin, graph, code):
    _app, _conta, numero = await cadastrar(db)
    await _migrando(client, numero, caso="A")
    graph.quando("POST", f"/{V}/{PNID}/register", (400, {"error": {"code": code, "message": "x"}}))
    r = await client.post(f"{URL}/numeros/{numero.id}/registrar", json={})
    assert r.status_code == 409
    assert "suporte da Meta" in r.json()["detail"]["detail"]
    linha = await _numero(db, PNID)
    assert linha.saude["registro_travado"]["code"] == code
    registro = next(
        n
        for n in (await client.get(f"{URL}/numeros")).json()["numeros"]
        if n["id"] == str(numero.id)
    )["registro"]
    assert registro["pode"] is False and "suporte" in registro["motivo"]


async def test_registrar_certo(client, cadastro, db, admin, graph):
    _app, _conta, numero = await cadastrar(db)
    await _migrando(client, numero, caso="A")
    graph.quando("POST", f"/{V}/{PNID}/register", {"success": True})
    r = await client.post(f"{URL}/numeros/{numero.id}/registrar", json={})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert (await _numero(db, PNID)).saude.get("registrado_em")


async def test_registrar_o_numero_de_teste_e_limpar_o_nao_registrado(
    client, cadastro, db, admin, graph
):
    """O número de TESTE (etapa Teste) também precisou do register com PIN no ensaio
    real (133010): o botão aparece, com o mesmo contador, e o registro que dá certo
    limpa o "não registrado" — revisão de 27/10/2026."""
    _app, _conta, numero = await cadastrar(db)  # número de teste, etapa teste
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == numero.id)
        .values(
            saude_status="erro",
            saude={
                "plataforma": "NOT_APPLICABLE",
                "erro_meta": {"code": 133010, "frase": "não registrado", "em": "x"},
            },
        )
    )
    await db.commit()
    tela = lambda r: next(n for n in r.json()["numeros"] if n["id"] == str(numero.id))  # noqa: E731
    registro = tela(await client.get(f"{URL}/numeros"))["registro"]
    assert registro["pode"] is False and "PIN" in registro["motivo"]
    assert (await client.patch(f"{URL}/numeros/{numero.id}", json={"pin": PIN})).status_code == 200
    assert tela(await client.get(f"{URL}/numeros"))["registro"]["pode"] is True
    graph.quando("POST", f"/{V}/{PNID}/register", {"success": True})
    r = await client.post(f"{URL}/numeros/{numero.id}/registrar", json={})
    assert r.status_code == 200, r.text
    assert r.json()["numero"]["conexao"]["codigo"] != "erro"
    linha = await _numero(db, PNID)
    assert linha.saude_status == "ok" and "erro_meta" not in linha.saude
    assert "plataforma" not in linha.saude and linha.saude.get("registrado_em")
    assert len(linha.registro_tentativas) == 1
    # O número de verdade na etapa Teste não ganha o botão sem o caso da migração.
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == numero.id)
        .values(teste=False, registro_tentativas=[])
    )
    await db.commit()
    assert tela(await client.get(f"{URL}/numeros"))["registro"]["pode"] is False


async def test_pin_cifrado_e_so_sob_clique(client, cadastro, db, admin):
    _app, _conta, numero = await cadastrar(db)
    r = await client.patch(f"{URL}/numeros/{numero.id}", json={"pin": PIN})
    assert r.status_code == 200 and PIN not in r.text
    assert r.json()["tem_pin"] is True
    linha = await _numero(db, PNID)
    assert PIN.encode() not in linha.pin_enc
    assert PIN not in (await client.get(f"{URL}/numeros")).text
    assert (await client.get(f"{URL}/numeros/{numero.id}/pin")).json()["pin"] == PIN
    for ruim in ("12345", "abcdef", "1234567"):
        r = await client.patch(f"{URL}/numeros/{numero.id}", json={"pin": ruim})
        assert r.status_code == 422


# ═══════════════════════ reprocessar ═════════════════════════════════════


async def test_reprocessar_eventos_parados(client, cadastro, db, admin, zap):
    app, _conta, _numero_ = await cadastrar(db)
    await gravar_corpo(corpo("texto", pnid="930000000000555", wamid="wamid.SEM"), app=app)
    await processar()
    (ev,) = await eventos(db)
    assert ev.resultado == "numero_desconhecido"
    tela = (await client.get(f"{URL}/numeros")).json()
    assert tela["eventos_parados"]["total"] == 1
    r = await client.post(f"{URL}/eventos/{ev.id}/reprocessar")
    assert r.status_code == 200
    (ev,) = await eventos(db)
    assert ev.processado_em is None and ev.resultado is None
    await processar()
    r = await client.post(f"{URL}/eventos/reprocessar")
    assert r.json()["reabertos"] == 1
    # Evento já gravado não reabre.
    await processar()
    await gravar_corpo(corpo("texto", wamid="wamid.OK"), app=app)
    await processar()
    ok = next(e for e in await eventos(db) if e.chave == "m:wamid.OK")
    assert (await client.post(f"{URL}/eventos/{ok.id}/reprocessar")).status_code == 409


# ═══════════════════════ o Histórico (segredo nunca; revelação sempre) ═══


async def test_historico_sem_segredo_e_com_as_revelacoes(client, cadastro, db, make_user, graph):
    eu = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    _logar(client, eu)
    app = await _novo_app(client)
    conta = await _nova_conta(client, graph, app)
    n = await _novo_numero(client, graph, conta)
    r = await client.patch(f"{URL}/numeros/{n['id']}", json={"pin": PIN, "teto_dia": 90})
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{URL}/apps/{app['id']}/segredo", json={"app_secret": "chave-rotina-2", "motivo": "rotina"}
    )
    assert r.status_code == 200
    verify = (await client.get(f"{URL}/apps/{app['id']}/verify-token")).json()["verify_token"]
    assert (await client.get(f"{URL}/numeros/{n['id']}/pin")).json()["pin"] == PIN
    _meta_da_conta(graph)
    r = await client.post(
        f"{URL}/contas/{conta['conta']['id']}/token", json={"access_token": TOKEN_NOVO}
    )
    assert r.status_code == 200
    tudo = await _tudo_do_historico(db)
    for segredo in (CHAVE, "chave-rotina-2", TOKEN, TOKEN_NOVO, PIN, verify):
        assert segredo not in tudo, segredo
    acoes = [e.acao for e in await _acoes(db)]
    assert "viu o código de verificação do webhook do WhatsApp" in acoes
    assert "viu o PIN do número do WhatsApp" in acoes
    assert "trocou o token da conta do WhatsApp" in acoes
    assert "trocou a chave secreta do app do WhatsApp" in acoes


# ═══════════════════════ LGPD: apagar os dados de um contato ═════════════


async def test_apagar_contato_varre_tudo_e_nao_copia_no_historico(
    client, cadastro, db, make_user, uploads
):
    eu = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    _logar(client, eu)
    app, _conta, numero = await cadastrar(db)
    outro = "5511912345678"
    await gravar_corpo(corpo("texto", wamid="wamid.T1", texto="meu endereço é rua X 10"), app=app)
    await gravar_corpo(corpo("imagem", wamid="wamid.F1", media_id="MIDLGPD"), app=app)
    await gravar_corpo(corpo("localizacao", wamid="wamid.L1"), app=app)
    await gravar_corpo(corpo("ctwa", wamid="wamid.C1"), app=app)
    await gravar_corpo(corpo("status", alvo="wamid.T1", status="read", wa_id=WA_ID), app=app)
    await gravar_corpo(corpo("texto", wamid="wamid.OUTRO", wa_id=outro, nome="Bia"), app=app)
    await processar()
    midia = (
        await db.execute(
            select(AtendimentoZapMidia).where(AtendimentoZapMidia.media_id == "MIDLGPD")
        )
    ).scalar_one()
    origem = uploads / "foto.jpg"
    origem.write_bytes(JPG)
    from app import db as _db

    async with _db.SessionLocal() as s:
        assert await zap_midia.guardar_local(s, midia.id, origem) == "baixada"
    midia = await db.get(AtendimentoZapMidia, midia.id, populate_existing=True)
    arquivo = uploads / midia.caminho
    assert arquivo.exists()
    contato = (
        await db.execute(select(AtendimentoZapContato).where(AtendimentoZapContato.wa_id == WA_ID))
    ).scalar_one()
    conversa_id = contato.conversa_id
    # O par que o indexador do agente guardou da conversa (até 180 dias).
    resposta = (
        await db.execute(
            select(AtendimentoMensagem.id).where(AtendimentoMensagem.conversa_id == conversa_id)
        )
    ).scalars().first()
    db.add(
        AtendimentoPar(
            conversa_id=conversa_id,
            resposta_msg_id=resposta,
            plataforma="whatsapp",
            pergunta_gen="meu endereço é rua X 10",
            resposta_gen="ok",
            fonte="externo",
            respondido_em=datetime.now(UTC),
        )
    )
    # O corpo CRU que não virou evento de número (JSON inválido) cita o telefone.
    db.add(
        AtendimentoZapEvento(
            app_ref=app.id,
            chave=f"cru:{WA_ID}",
            tipo="ignorado",
            resultado="json_invalido",
            payload={"corpo": f'{{"from": "{WA_ID}", "text": "rua X'},
        )
    )
    await db.commit()

    r = await client.post(f"{URL}/contatos/{contato.id}/apagar")
    assert r.status_code == 200, r.text
    assert r.json()["mensagens"] >= 3 and r.json()["midias"] == 1

    c = await db.get(AtendimentoZapContato, contato.id, populate_existing=True)
    assert (c.wa_id, c.wa_id_norm, c.bsuid, c.nome_perfil) == (None, None, None, None)
    assert c.apagado_em is not None
    conversa = await db.get(AtendimentoConversa, conversa_id, populate_existing=True)
    assert conversa.comprador_id is None and conversa.comprador_nome is None
    assert not (conversa.dados or {}).get("zap", {}).get("ctwa")
    assert "comprador_plataforma" not in (conversa.dados or {})
    assert "rua X" not in (conversa.ultima_mensagem_resumo or "")
    msgs = (
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert msgs and all(m.texto == "[apagado a pedido]" for m in msgs)
    for m in msgs:
        z = (m.payload or {}).get("zap") or {}
        assert not ({"localizacao", "referral", "contato", "contexto_wamid", "lat"} & set(z))
        for a in m.anexos or []:
            assert "nome" not in a and "url" not in a
    m2 = await db.get(AtendimentoZapMidia, midia.id, populate_existing=True)
    assert m2.status == "apagada" and m2.nome_original is None and m2.caminho is None
    assert not arquivo.exists()
    # Os eventos DESTE contato saíram; os do outro cliente ficaram.
    chaves = {e.chave for e in await eventos(db)}
    assert not {"m:wamid.T1", "m:wamid.F1", "m:wamid.L1", "m:wamid.C1"} & chaves
    assert not any(ch.startswith("s:wamid.T1") for ch in chaves)
    assert "m:wamid.OUTRO" in chaves
    # O outro cliente continua inteiro.
    bia = (
        await db.execute(select(AtendimentoZapContato).where(AtendimentoZapContato.wa_id == outro))
    ).scalar_one()
    assert bia.nome_perfil == "Bia" and bia.apagado_em is None
    # A ação fica no Histórico, SEM corpo e sem nenhum dado do contato.
    (ev,) = [e for e in await _acoes(db) if e.acao == "apagou os dados de um contato do Zap"]
    assert ev.corpo is None
    tudo = await _tudo_do_historico(db)
    for dado in (WA_ID, "Ana Fictícia", "rua X"):
        assert dado not in tudo, dado
    # Apagar de novo: nada a fazer, sem erro.
    assert (await client.post(f"{URL}/contatos/{contato.id}/apagar")).status_code == 200
    # O contato apagado não recebe mais nada: a trava 10 do envio lê estes dois
    # (`enviar._destino_zap`; testada em test_atendimento_zap_envio).
    assert c.bloqueado is True and c.apagado_em is not None
    # O agente não guarda mais a fala dele; o corpo cru com o telefone saiu.
    assert (
        await db.scalar(select(AtendimentoPar.id).where(AtendimentoPar.conversa_id == conversa_id))
    ) is None
    assert f"cru:{WA_ID}" not in chaves
    assert numero is not None


async def test_apagar_por_telefone_acha_todos_os_contatos(client, cadastro, db, make_user):
    """O campo da tela: o telefone como o cliente escreve (sem o 55, com o 9) acha os
    contatos dele em TODOS os números; o Histórico não guarda o telefone."""
    eu = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    _logar(client, eu)
    app, _conta, _numero = await cadastrar(db)
    await cadastrar(db, pnid="930000000000002", nome_na_barra="Segundo")
    await gravar_corpo(corpo("texto", wamid="wamid.A1", texto="oi"), app=app)
    await gravar_corpo(
        corpo("texto", wamid="wamid.A2", texto="oi de novo", pnid="930000000000002"), app=app
    )
    await processar()
    contatos = (
        (
            await db.execute(
                select(AtendimentoZapContato).where(AtendimentoZapContato.wa_id == WA_ID)
            )
        )
        .scalars()
        .all()
    )
    assert len(contatos) == 2
    digitado = f"({WA_ID[2:4]}) {WA_ID[4:]}"  # sem o 55, como o cliente escreve
    r = await client.post(f"{URL}/contatos/apagar-por-telefone", json={"telefone": digitado})
    assert r.status_code == 200, r.text
    assert r.json()["contatos"] == 2 and r.json()["mensagens"] >= 2
    for c in contatos:
        c = await db.get(AtendimentoZapContato, c.id, populate_existing=True)
        assert c.apagado_em is not None and c.wa_id is None
    # De novo: nada a apagar (404); telefone curto: 422.
    r = await client.post(f"{URL}/contatos/apagar-por-telefone", json={"telefone": digitado})
    assert r.status_code == 404
    r = await client.post(f"{URL}/contatos/apagar-por-telefone", json={"telefone": "12345678"})
    assert r.status_code == 422
    tudo = await _tudo_do_historico(db)
    assert WA_ID not in tudo and WA_ID[4:] not in tudo
    assert any(
        e.acao == "apagou os dados de um contato do Zap (pelo telefone)" and e.corpo is None
        for e in await _acoes(db)
    )


async def test_apagar_contato_inexistente_404(client, cadastro, admin):
    r = await client.post(f"{URL}/contatos/00000000-0000-0000-0000-000000000000/apagar")
    assert r.status_code == 404
    r = await client.post(f"{URL}/contatos/nao-e-uuid/apagar")
    assert r.status_code == 404


# ═══════════════════════ a tela (GET /numeros) ═══════════════════════════


async def test_tela_lista_tudo_sem_segredo(client, cadastro, db, admin, zap):
    app, conta, numero = await cadastrar(db)
    await gravar_corpo(corpo("texto", wamid="wamid.T"), app=app)
    await processar()
    r = await client.get(f"{URL}/numeros")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["chaves"] == {
        "leitura": True,
        "envio": False,
        "cadastro": True,
        "producao": False,
        "lista_de_teste": False,
    }
    (a,) = t["apps"]
    assert a["app_id"] == APP_ID and a["webhook_url"].endswith(f"/whatsapp/{APP_ID}")
    (c,) = t["contas"]
    assert c["waba_id"] == WABA
    (n,) = t["numeros"]
    assert n["phone_number_id"] == PNID and n["modo"] == "observar"
    assert n["ultimo_recebido_em"] is not None
    assert n["conexao"]["codigo"] == "conectado"
    assert n["respostas_mes"] == 0 and n["enviados_hoje"] == 0
    assert n["teto_efetivo"] == get_settings().atendimento_zap_max_dia_numero
    assert "campos_do_webhook" in t and "account_alerts" in t["campos_do_webhook"]
    for segredo in (SEGREDO, "token-de-mentira", "verify-token-de-teste"):
        assert segredo not in r.text, segredo
