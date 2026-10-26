# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""Webhook PÚBLICO do Zap (WhatsApp Cloud API, RF10, Onda 1) — docs/zap/desenho.md §5.1.

Molde: `test_webhook_dm_instagram.py`. Nenhum teste fala com a Meta: o app é
inventado, o Redis e a fila do arq são falsos (`tests/zap_apoio.py`) e os
corpos vêm das fixtures no formato real (`tests/fixtures/whatsapp/`).

O que se garante:
- o handshake (texto puro, token em bytes, app desconhecido 404);
- só o host principal responde; o app desconhecido cai no cache NEGATIVO;
- a assinatura dura (HMAC do corpo cru), com o segredo anterior só na janela
  de 1 h da troca de rotina; a falha conta SÓ no Redis, com chave própria;
- o freio por IP para quem errou (429 sem calcular o HMAC);
- o corpo em streaming com teto (413 sem `Content-Length`);
- nada some em silêncio: JSON inválido e objeto inesperado viram `ignorado`;
- o evento está COMMITADO antes do 200; banco fora = 500; a fila falhando
  ainda dá 200; 50 POSTs = 1 job (coalescido);
- a idempotência (mesmo corpo, `account_update` com outro `entry.time`), o
  lote grande inteiro, só o contato do remetente no payload;
- o Instagram intacto (`assinatura_meta_ok` é a parte pura dele).
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

import app.db as _db
from app.main import app as app_fastapi
from app.models import AtendimentoZapApp, AtendimentoZapEvento
from app.routers import webhooks, whatsapp_webhook
from app.services.atendimento import zap_contas, zap_entrada
from tests.zap_apoio import (  # noqa: F401
    API_URL,
    APP_ID,
    SEGREDO,
    URL,
    VERIFY,
    WA_ID,
    assinar,
    bruto,
    cadastrar,
    cliente_zap,
    corpo,
    eventos,
    postar,
    zap,
)

# ─────────────── handshake ───────────────


async def test_handshake_devolve_challenge_em_texto_puro(db, zap, cliente_zap):
    await cadastrar(db)
    r = await cliente_zap.get(
        URL,
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    assert r.status_code == 200
    assert r.text == "1158201444"
    assert r.headers["content-type"].startswith("text/plain")


async def test_handshake_token_errado_ou_byte_alto_403_nunca_500(db, zap, cliente_zap):
    await cadastrar(db)
    for token in ("errado", "ção", ""):
        r = await cliente_zap.get(
            URL, params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1"}
        )
        assert r.status_code == 403, token
    r = await cliente_zap.get(
        URL, params={"hub.mode": "unsubscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"}
    )
    assert r.status_code == 403


async def test_handshake_de_app_desconhecido_ou_inativo_404(db, zap, cliente_zap):
    r = await cliente_zap.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"}
    )
    assert r.status_code == 404
    app, _, _ = await cadastrar(db)
    app.ativo = False
    await db.commit()
    zap_contas.limpar_cache()
    r = await cliente_zap.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"}
    )
    assert r.status_code == 404


# ─────────────── host, app, cache negativo ───────────────


async def test_so_o_host_principal_responde(db, zap):
    await cadastrar(db)
    c = corpo("texto")
    for base in ("https://gestaoestoque.com", "http://test"):
        async with AsyncClient(transport=ASGITransport(app=app_fastapi), base_url=base) as outro:
            r = await postar(outro, c)
            assert r.status_code == 404, base
            r = await outro.get(
                URL,
                params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"},
            )
            assert r.status_code == 404, base
    assert await eventos(db) == []


async def test_app_desconhecido_404_e_o_cache_negativo_nao_volta_ao_banco(
    db, zap, cliente_zap, monkeypatch
):
    leituras: list[str] = []
    original = zap_contas._ler_app

    async def _contando(session, app_id):
        leituras.append(app_id)
        return await original(session, app_id)

    monkeypatch.setattr(zap_contas, "_ler_app", _contando)
    c = corpo("texto")
    for _ in range(3):
        r = await postar(cliente_zap, c)
        assert r.status_code == 404
    assert leituras == [APP_ID]  # uma ida ao banco em 60 s
    # Id fora do formato (só dígitos) nem consulta o banco.
    r = await cliente_zap.post(
        "/api/webhooks/meta/whatsapp/abc'--", content=bruto(c), headers={"X-Hub-Signature-256": "x"}
    )
    assert r.status_code == 404
    assert leituras == [APP_ID]
    assert await eventos(db) == []


# ─────────────── assinatura ───────────────


async def test_assinatura_de_outro_app_403_e_conta_so_no_redis(db, zap, cliente_zap):
    app, _, _ = await cadastrar(db)
    r = await postar(cliente_zap, corpo("texto"), segredo="segredo-de-OUTRO-app")
    assert r.status_code == 403
    sem_assinatura = await cliente_zap.post(URL, content=bruto(corpo("texto")))
    assert sem_assinatura.status_code == 403
    # Só o contador do Zap; nem o do Instagram, nem o do Bling.
    assert zap.redis.dados[whatsapp_webhook.SIG_FAIL_KEY] == 2
    assert whatsapp_webhook.SIG_FAIL_SNAPSHOT_KEY in zap.redis.dados
    assert webhooks.META_SIG_FAIL_KEY not in zap.redis.dados
    assert webhooks.SIG_FAIL_COUNTER_KEY not in zap.redis.dados
    # Nada no banco: nem evento, nem a saúde do app.
    assert await eventos(db) == []
    await db.refresh(app)
    assert app.ultimo_evento_em is None


async def test_ip_que_errou_demais_leva_429_sem_calcular_o_hmac(db, zap, cliente_zap, monkeypatch):
    await cadastrar(db)
    estranho = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(whatsapp_webhook.FREIO_MAX + 1):
        r = await postar(cliente_zap, corpo("texto"), segredo="chute", **estranho)
        assert r.status_code == 403
    calculos: list[int] = []
    original = webhooks.assinatura_meta_ok

    def _contando(*a, **k):
        calculos.append(1)
        return original(*a, **k)

    monkeypatch.setattr(webhooks, "assinatura_meta_ok", _contando)
    r = await postar(cliente_zap, corpo("texto"), **estranho)
    assert r.status_code == 429
    assert calculos == []  # o freio vem ANTES do HMAC (e da leitura do corpo)
    # A Meta (outro IP, que acerta) nunca é freada.
    r = await postar(cliente_zap, corpo("texto"), **{"X-Forwarded-For": "157.240.0.1"})
    assert r.status_code == 200
    assert calculos == [1]


async def test_freio_de_um_app_nao_freia_o_outro_e_o_ip_que_acerta_nunca_e_freado(
    db, zap, cliente_zap
):
    """Os IPs de saída da Meta são os mesmos para todos os apps: o app de teste
    com o segredo desatualizado (ou a troca de rotina, com a Meta já assinando
    com o novo) não pode congelar a entrega do app de produção."""
    await cadastrar(db)
    app_b, waba_b, pnid_b = "910000000000002", "920000000000002", "930000000000002"
    await cadastrar(
        db, app_id=app_b, segredo="segredo-velho-do-app-B", verify="v2", waba=waba_b, pnid=pnid_b
    )
    meta = {"X-Forwarded-For": "157.240.0.1"}
    url_b = f"/api/webhooks/meta/whatsapp/{app_b}"
    dados = bruto(corpo("texto", waba=waba_b, pnid=pnid_b))
    cabecalho = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": assinar(dados, "segredo-NOVO-do-app-B"),
        **meta,
    }
    for _ in range(whatsapp_webhook.FREIO_MAX + 1):
        r = await cliente_zap.post(url_b, content=dados, headers=cabecalho)
        assert r.status_code == 403
    # O app A, assinatura certa, o MESMO IP: entra.
    r = await postar(cliente_zap, corpo("texto", wamid="wamid.A1"), **meta)
    assert r.status_code == 200
    # O IP acertou agora: nem o app B o freia mais (vê a assinatura e dá 403).
    r = await cliente_zap.post(url_b, content=dados, headers=cabecalho)
    assert r.status_code == 403
    # Segredo corrigido no DaVinci: o app B volta na hora, sem esperar a janela.
    async with _db.SessionLocal() as s:
        app_db = (
            await s.execute(select(AtendimentoZapApp).where(AtendimentoZapApp.app_id == app_b))
        ).scalar_one()
        app_db.segredo_enc = zap_contas.cifrar_segredo_app("segredo-NOVO-do-app-B")
        await s.commit()
    zap_contas.limpar_cache()
    r = await cliente_zap.post(url_b, content=dados, headers=cabecalho)
    assert r.status_code == 200
    # Quem nunca acertou continua freado só no app em que errou.
    estranho = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(whatsapp_webhook.FREIO_MAX + 1):
        r = await postar(cliente_zap, corpo("texto"), segredo="chute", **estranho)
        assert r.status_code == 403
    assert (await postar(cliente_zap, corpo("texto"), **estranho)).status_code == 429
    r = await cliente_zap.post(url_b, content=dados, headers={**cabecalho, **estranho})
    assert r.status_code == 200


async def test_segredo_anterior_so_na_janela_de_1h_da_troca_de_rotina(
    db, zap, cliente_zap, monkeypatch
):
    app, _, _ = await cadastrar(db)
    zap_contas.trocar_segredo(app, "segredo-NOVO-da-meta", motivo="rotina")
    await db.commit()
    zap_contas.limpar_cache()
    r = await postar(cliente_zap, corpo("texto", wamid="wamid.ANTERIOR"), segredo=SEGREDO)
    assert r.status_code == 200
    assert zap.redis.dados[whatsapp_webhook.ANTERIOR_KEY] == 1
    r = await postar(
        cliente_zap, corpo("texto", wamid="wamid.NOVO"), segredo="segredo-NOVO-da-meta"
    )
    assert r.status_code == 200
    assert zap.redis.dados[whatsapp_webhook.ANTERIOR_KEY] == 1
    # Passada 1 h, o anterior não vale mais.
    depois = zap_contas._agora() + timedelta(hours=1, minutes=1)
    monkeypatch.setattr(zap_contas, "_agora", lambda: depois)
    r = await postar(cliente_zap, corpo("texto", wamid="wamid.TARDE"), segredo=SEGREDO)
    assert r.status_code == 403


async def test_troca_por_vazamento_recusa_o_anterior_na_hora(db, zap, cliente_zap):
    app, _, _ = await cadastrar(db)
    zap_contas.trocar_segredo(app, "segredo-NOVO-da-meta", motivo="vazou")
    await db.commit()
    zap_contas.limpar_cache()
    r = await postar(cliente_zap, corpo("texto"), segredo=SEGREDO)
    assert r.status_code == 403
    r = await postar(cliente_zap, corpo("texto"), segredo="segredo-NOVO-da-meta")
    assert r.status_code == 200
    with pytest.raises(ValueError):
        zap_contas.trocar_segredo(app, "x", motivo="esqueci")


# ─────────────── corpo ───────────────


async def test_corpo_acima_do_teto_413_lido_em_streaming_sem_content_length(
    db, zap, cliente_zap, monkeypatch
):
    await cadastrar(db)
    monkeypatch.setattr(whatsapp_webhook, "TETO_CORPO", 1024)
    lidos: list[int] = []

    async def _pedacos():
        for i in range(20):
            lidos.append(i)
            yield b"x" * 600

    r = await cliente_zap.post(
        URL, content=_pedacos(), headers={"X-Hub-Signature-256": assinar(b"qualquer")}
    )
    assert r.status_code == 413
    assert len(lidos) < 20  # parou de ler ao passar do teto
    assert zap.redis.dados[whatsapp_webhook.GRANDE_KEY] == 1
    # Com `Content-Length` acima do teto: nem lê.
    grande = b"{" + b" " * 2000 + b"}"
    r = await cliente_zap.post(
        URL, content=grande, headers={"X-Hub-Signature-256": assinar(grande)}
    )
    assert r.status_code == 413
    assert await eventos(db) == []


async def test_json_invalido_200_e_gravado_como_ignorado(db, zap, cliente_zap):
    await cadastrar(db)
    quebrado = b'{"object": "whatsapp_business_account", "entry": [ "corta\x00'
    r = await postar(cliente_zap, quebrado)
    assert r.status_code == 200
    (ev,) = await eventos(db)
    assert (ev.tipo, ev.resultado) == ("ignorado", "json_invalido")
    assert ev.chave.startswith("j:") and ev.processado_em is not None
    assert "whatsapp_business_account" in ev.payload["corpo"]
    assert zap.pool.jobs == []  # nada para o processador


async def test_surrogate_solto_no_corpo_assinado_200_nunca_500(db, zap, cliente_zap):
    """O nome do perfil cortado no meio de um emoji chega como `\\ud83d` (JSON
    válido): o hash do item `e:` não pode derrubar o webhook com 500 — a Meta
    reenviaria o lote inteiro por 7 dias, com as outras mensagens presas."""
    await cadastrar(db)
    pref = json.dumps(corpo("preferencia", nome="Ana \ud83d")).encode()  # ensure_ascii
    msg = json.dumps(corpo("texto", wamid="wamid.SUR", nome="Ana \ud83d", texto="oi \ud83d"))
    erro = corpo("erros")
    erro["entry"][0]["changes"][0]["value"]["errors"][0]["title"] = "falhou \udc00"
    for dados in (pref, msg.encode(), json.dumps(erro).encode()):
        r = await postar(cliente_zap, dados)
        assert r.status_code == 200, r.text
        assert r.json()["novos"] == 1
        assert (await postar(cliente_zap, dados)).json()["novos"] == 0  # a mesma chave
    por_tipo = {e.tipo: e for e in await eventos(db)}
    assert set(por_tipo) == {"preferencia", "mensagem", "erro"}
    assert por_tipo["preferencia"].payload["contato"]["profile"]["name"] == "Ana ?"
    assert por_tipo["mensagem"].payload["mensagem"]["text"]["body"] == "oi ?"


async def test_quebra_do_corpo_falhando_grava_cru_e_responde_200(db, zap, cliente_zap, monkeypatch):
    await cadastrar(db)

    def _explode(*a, **k):
        raise RuntimeError("formato que ninguém previu")

    monkeypatch.setattr(zap_entrada, "itens_do_corpo", _explode)
    r = await postar(cliente_zap, corpo("texto", wamid="wamid.CRU"))
    assert r.status_code == 200
    (ev,) = await eventos(db)
    assert (ev.tipo, ev.resultado) == ("ignorado", "json_invalido")
    assert "wamid.CRU" in ev.payload["corpo"]  # o corpo cru, para reprocessar à mão
    assert zap.pool.jobs == []


async def test_objeto_inesperado_gravado_como_ignorado(db, zap, cliente_zap):
    await cadastrar(db)
    r = await postar(cliente_zap, corpo("objeto_inesperado"))
    assert r.status_code == 200
    (ev,) = await eventos(db)
    assert (ev.tipo, ev.resultado) == ("ignorado", "objeto_inesperado")
    assert ev.payload["objeto"] == "instagram"
    assert ev.processado_em is not None


# ─────────────── gravação ───────────────


async def test_evento_gravado_e_commitado_antes_do_200(db, zap, cliente_zap):
    app, _, _ = await cadastrar(db)
    r = await postar(cliente_zap, corpo("texto", wamid="wamid.UM"))
    assert r.status_code == 200
    assert r.json() == {"ack": True, "novos": 1}
    # Outra sessão vê: o commit veio antes da resposta.
    async with _db.SessionLocal() as s:
        ev = (await s.execute(select(AtendimentoZapEvento))).scalar_one()
        assert (ev.chave, ev.tipo, ev.campo) == ("m:wamid.UM", "mensagem", "messages")
        assert ev.processado_em is None and ev.proxima_em is None and ev.tentativas == 0
        assert ev.app_ref == app.id and ev.phone_number_id and ev.waba_id
        salvo = await s.get(AtendimentoZapApp, app.id)
        assert salvo.ultimo_evento_em is not None
    assert zap.pool.jobs == ["atendimento_zap_processar"]


async def test_banco_fora_responde_500_para_a_meta_reenviar(db, zap, cliente_zap, monkeypatch):
    await cadastrar(db)

    async def _cai(*a, **k):
        raise ConnectionResetError("banco fora")

    monkeypatch.setattr(zap_entrada, "gravar_itens", _cai)
    r = await postar(cliente_zap, corpo("texto"))
    assert r.status_code == 500
    assert zap.pool.jobs == []


async def test_enfileirar_falhando_ainda_da_200(db, zap, cliente_zap):
    await cadastrar(db)
    zap.pool.falhar = True
    r = await postar(cliente_zap, corpo("texto"))
    assert r.status_code == 200
    assert len(await eventos(db)) == 1  # o cron de 1 min pega


async def test_50_posts_seguidos_enfileiram_um_job_so(db, zap, cliente_zap):
    await cadastrar(db)
    for i in range(50):
        r = await postar(cliente_zap, corpo("texto", wamid=f"wamid.RAJADA{i}"))
        assert r.status_code == 200
    assert zap.pool.jobs == ["atendimento_zap_processar"]
    assert len(await eventos(db)) == 50


async def test_chaves_desligadas_o_webhook_grava_mesmo_assim_e_nao_enfileira(
    db, zap, cliente_zap, monkeypatch
):
    await cadastrar(db)
    monkeypatch.setattr(zap.settings, "atendimento_zap_leitura_ativa", False)
    r = await postar(cliente_zap, corpo("texto"))
    assert r.status_code == 200
    (ev,) = await eventos(db)
    assert ev.processado_em is None
    assert zap.pool.jobs == []


async def test_mesmo_corpo_duas_vezes_mesmos_eventos(db, zap, cliente_zap):
    await cadastrar(db)
    c = corpo("texto", wamid="wamid.REENTREGA")
    assert (await postar(cliente_zap, c)).json()["novos"] == 1
    assert (await postar(cliente_zap, c)).json()["novos"] == 0
    st = corpo("status", alvo="wamid.NOSSA", status="delivered")
    assert (await postar(cliente_zap, st)).json()["novos"] == 1
    assert (await postar(cliente_zap, st)).json()["novos"] == 0
    chaves = [e.chave for e in await eventos(db)]
    assert chaves == ["m:wamid.REENTREGA", "s:wamid.NOSSA:delivered"]


async def test_account_update_igual_com_entry_time_diferente_sao_2_eventos(db, zap, cliente_zap):
    await cadastrar(db)
    um = corpo("account_update", valor="VERIFIED_ACCOUNT")
    outro = json.loads(json.dumps(um))
    outro["entry"][0]["time"] = um["entry"][0]["time"] + 86400 * 3
    assert (await postar(cliente_zap, um)).json()["novos"] == 1
    assert (await postar(cliente_zap, um)).json()["novos"] == 0  # a reentrega
    assert (await postar(cliente_zap, outro)).json()["novos"] == 1  # dias depois: novo
    evs = await eventos(db)
    assert [e.tipo for e in evs] == ["conta", "conta"]
    assert all(e.chave.startswith("e:") and e.campo == "account_update" for e in evs)


async def test_lote_com_mais_de_200_itens_grava_todos(db, zap, cliente_zap):
    await cadastrar(db)
    c = corpo("texto")
    valor = c["entry"][0]["changes"][0]["value"]
    modelo = valor["messages"][0]
    valor["messages"] = [{**modelo, "id": f"wamid.LOTE{i}"} for i in range(260)]
    valor["statuses"] = [
        {"id": f"wamid.ST{i}", "status": "read", "timestamp": "1760875200", "recipient_id": WA_ID}
        for i in range(240)
    ]
    r = await postar(cliente_zap, c)
    assert r.status_code == 200 and r.json()["novos"] == 500
    assert await db.scalar(select(func.count(AtendimentoZapEvento.id))) == 500


async def test_payload_so_com_o_contato_do_remetente(db, zap, cliente_zap):
    await cadastrar(db)
    c = corpo("texto", wamid="wamid.DA_ANA")
    valor = c["entry"][0]["changes"][0]["value"]
    valor["contacts"].append({"profile": {"name": "Bia Fictícia"}, "wa_id": "5511911110000"})
    valor["messages"].append(
        {**valor["messages"][0], "id": "wamid.DA_BIA", "from": "5511911110000"}
    )
    assert (await postar(cliente_zap, c)).json()["novos"] == 2
    por_chave = {e.chave: e for e in await eventos(db)}
    ana = por_chave["m:wamid.DA_ANA"].payload
    bia = por_chave["m:wamid.DA_BIA"].payload
    assert ana["contato"]["wa_id"] == WA_ID and "Bia" not in json.dumps(ana)
    assert bia["contato"]["profile"]["name"] == "Bia Fictícia" and "Ana" not in json.dumps(bia)
    assert "contacts" not in ana and ana["metadata"]["phone_number_id"]


async def test_user_preferences_vira_preferencia_e_erros_viram_erro(db, zap, cliente_zap):
    await cadastrar(db)
    await postar(cliente_zap, corpo("preferencia", valor="stop"))
    await postar(cliente_zap, corpo("erros"))
    await postar(cliente_zap, corpo("status_falhou", alvo="wamid.X"))
    tipos = {(e.tipo, e.campo) for e in await eventos(db)}
    assert tipos == {
        ("preferencia", "user_preferences"),
        ("erro", "messages"),
        ("status", "messages"),
    }


# ─────────────── o Instagram não muda ───────────────


def test_assinatura_meta_ok_e_a_parte_pura_do_instagram(monkeypatch):
    dados = '{"object":"instagram","texto":"ação 😀"}'.encode()
    ok = assinar(dados, "s1")
    assert webhooks.assinatura_meta_ok(dados, ok, [b"s1"])
    assert webhooks.assinatura_meta_ok(dados, ok, [b"s0", b"s1"])  # qualquer um dos segredos
    assert not webhooks.assinatura_meta_ok(dados, ok, [b"s0"])
    assert not webhooks.assinatura_meta_ok(dados, ok, [b""])  # segredo vazio nunca vale
    assert not webhooks.assinatura_meta_ok(dados, ok, [])
    assert not webhooks.assinatura_meta_ok(dados, None, [b"s1"])
    assert not webhooks.assinatura_meta_ok(dados, ok.replace("sha256=", "sha1="), [b"s1"])
    assert not webhooks.assinatura_meta_ok(dados, "sha256=çãõ", [b"s1"])  # byte alto: False
    # O invólucro do Instagram: o segredo dele, igual a antes.
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "instagram_app_secret", "")
    monkeypatch.setattr(s, "meta_app_secret", "s1")
    assert webhooks._verify_meta_signature(dados, ok)
    monkeypatch.setattr(s, "meta_app_secret", "")
    assert not webhooks._verify_meta_signature(dados, ok)
    assert API_URL.startswith("https://")  # o apoio usa o host principal de teste
    assert SEGREDO
