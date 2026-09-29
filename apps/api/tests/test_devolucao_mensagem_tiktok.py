# ruff: noqa: E501, F811 — fixtures importadas do teste da Shopee
"""Pedido de senha ao comprador da TikTok pelo robô do Mac Santiago.

Vinicius 29/09: "conseguimos fazer isso também no TikTok? solicitar a senha ao
cliente" → "podemos tentar via script". A API da TikTok não fala com o comprador
(falta o escopo `seller.customer_service`), mas a tela fala: o executor abre o
chat do pedido no AdsPower e escreve (testado no 296301, TikTok Barbosa).

Aqui o lado do DaVinci: o lançamento "Bloqueado" vira tarefa `tiktok_senha` na
fila do robô da Logística, e o que o robô devolve fecha a linha e conta no
chamado.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.config import get_settings
from app.models import DevolucaoAnexo, LogisticaRoboComando
from app.services import devolucao_mensagem_comprador as svc
from app.services import logistica_robo as robo
from tests.test_chamados_devolucao import PNG_1PX, _perms
from tests.test_devolucao_mensagem_comprador import (  # noqa: F401 — fixtures
    _chamado,
    _eventos_comprador,
    _lancar,
    _limpa_mensagens,
    _linha,
    adiar,
    inline,
    shopee,
)

TOKEN = "tok-teste-executor"  # noqa: S105 — token de teste
TIKTOK = {"conta": "injox", "loja": "77", "platform": "tiktok"}


@pytest.fixture
def agent_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "marketing_agent_token", TOKEN)
    return {"X-Agent-Token": TOKEN}


@pytest_asyncio.fixture(autouse=True)
async def _limpa_tarefas(db):
    await db.execute(delete(LogisticaRoboComando))
    await db.commit()
    yield
    await db.execute(delete(LogisticaRoboComando))
    await db.commit()


async def _tarefas(db) -> list[LogisticaRoboComando]:
    # populate_existing em vez de expire_all: a linha que o teste já leu
    # continua utilizável (expirar faria o próximo acesso ir ao banco sem await).
    return list(
        (
            await db.execute(
                select(LogisticaRoboComando)
                .where(LogisticaRoboComando.acao == robo.ACAO_TIKTOK_SENHA)
                .order_by(LogisticaRoboComando.created_at)
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
    )


async def _devolver(db, cmd: LogisticaRoboComando, *, ok: bool, **resultado) -> None:
    await robo.registrar_resultado(
        db, cmd.id, status="done" if ok else "failed", result=json.dumps(resultado)
    )


# ---------------------------------------------------------------- a tarefa nasce


async def test_bloqueado_na_tiktok_vira_tarefa_do_robo_e_nao_chama_a_shopee(
    client, make_user, auth_as, db, shopee
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="296301", order_sn="586006309818107238",
                          sku="dg080.pi", **TIKTOK)

    # a tela mostra "na fila" (o robô ainda não passou)
    assert body["senha_status"] == "pendente" and body["senha_erro"] is None
    linha = await _linha(db, "296301")
    assert (linha.status, linha.plataforma, linha.tentativas) == ("pendente", "tiktok", 0)

    tarefas = await _tarefas(db)
    assert len(tarefas) == 1
    t = tarefas[0]
    assert t.status == "pending" and t.logistica_id is None
    p = t.payload
    assert p["linha_id"] == str(linha.id)
    assert (p["pedido_bling"], p["pedido_tiktok"], p["conta"]) == ("296301", "586006309818107238", "injox")
    assert "do pedido 586006309818107238" in p["texto"] and "senha de desbloqueio da tela" in p["texto"]
    assert "Aqui é a loja do seu pedido." in p["texto"]
    # o nome que o comprador vê no chat é o da loja NA TIKTOK — o robô troca
    assert "Aqui é a loja {LOJA}." in p["texto_loja"]
    assert p["foto_anexo_id"] is None and p["commit"] is True
    # nada foi pro chat da Shopee
    assert shopee.enviadas == [] and shopee.buyers == []


async def test_salvar_de_novo_e_o_cron_nao_duplicam_a_tarefa(
    client, make_user, auth_as, db, shopee
):
    """A tela salva a linha e sobe as fotos uma a uma; o cron :25 passa por toda
    linha pendente. Duas tarefas = duas mensagens pro mesmo comprador."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="296302", order_sn="586006309818100001", **TIKTOK)
    r = await client.patch(f"/api/devolutions/{body['id']}", json={"motivo_devolucao": "Bloqueado"})
    assert r.status_code == 200, r.text
    await svc.processar_pendentes(db)
    await svc.processar_pendentes(db)
    assert len(await _tarefas(db)) == 1

    # com a tarefa COM o executor (claimed) também não nasce outra
    await robo.lease(db, acoes=[robo.ACAO_TIKTOK_SENHA])
    await svc.processar_pendentes(db)
    assert len(await _tarefas(db)) == 1


async def test_foto_da_devolucao_vai_na_tarefa_e_so_o_robo_com_a_tarefa_baixa(
    client, make_user, auth_as, db, shopee, adiar, agent_token
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="296303", order_sn="586006309818100002", **TIKTOK)
    up = await client.post(f"/api/devolutions/{body['id']}/anexos",
                           files={"file": ("tela-bloqueada.jpg", PNG_1PX, "image/jpeg")})
    assert up.status_code == 201, up.text
    await svc.processar_pendentes(db)  # o job de 60 s depois das fotos

    anexo_id = (await db.execute(select(DevolucaoAnexo.id))).scalars().one()
    (t,) = await _tarefas(db)
    assert t.payload["foto_anexo_id"] == str(anexo_id)

    # na fila (pending) a foto não sai: só com a tarefa na mão do executor
    r = await client.get(f"/api/logistica/agent/comandos/{t.id}/foto", headers=agent_token)
    assert r.status_code == 404
    r = await client.post("/api/logistica/agent/lease",
                          json={"limit": 5, "acoes": [robo.ACAO_TIKTOK_SENHA]}, headers=agent_token)
    assert r.status_code == 200, r.text
    (c,) = r.json()["comandos"]
    assert c["id"] == str(t.id) and c["logistica_id"] is None
    r = await client.get(f"/api/logistica/agent/comandos/{t.id}/foto", headers=agent_token)
    assert r.status_code == 200 and r.content == PNG_1PX
    assert r.headers["content-type"].startswith("image/jpeg")
    # sem token, nada
    r = await client.get(f"/api/logistica/agent/comandos/{t.id}/foto")
    assert r.status_code == 401


# ---------------------------------------------------------------- quem pega


async def test_so_o_executor_que_pede_tiktok_senha_recebe_a_tarefa(
    client, make_user, auth_as, db, shopee, agent_token
):
    """Exclusiva, como a suspensão do Melhor Envio: o executor do Eduardo (sem
    `acoes`) e o do Melhor Envio não pegam."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="296304", order_sn="586006309818100003", **TIKTOK)

    assert await robo.lease(db, acoes=None) == []
    assert await robo.lease(db, acoes=[robo.ACAO_SUSPENDER]) == []
    r = await client.post("/api/logistica/agent/lease",
                          json={"limit": 5, "acoes": [robo.ACAO_SUSPENDER, robo.ACAO_TIKTOK_SENHA]},
                          headers=agent_token)
    assert r.status_code == 200, r.text
    assert [c["acao"] for c in r.json()["comandos"]] == [robo.ACAO_TIKTOK_SENHA]


# ---------------------------------------------------------------- o robô devolve


async def test_robo_mandou_linha_enviada_e_o_chamado_conta(
    client, make_user, auth_as, db, shopee, agent_token
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="296305", order_sn="586006309818100004", **TIKTOK)
    (t,) = await _tarefas(db)
    await robo.lease(db, acoes=[robo.ACAO_TIKTOK_SENHA])

    texto = "Olá! Aqui é a loja Barbosa 34. Recebemos de volta o aparelho do pedido 586006309818100004 …"
    r = await client.post(
        f"/api/logistica/agent/comandos/{t.id}/resultado",
        json={"status": "done", "result": json.dumps({"ok": True, "texto": texto, "loja": "Barbosa 34", "com_foto": False})},
        headers=agent_token,
    )
    assert r.status_code == 200 and r.json()["status"] == "done"

    linha = await _linha(db, "296305")
    assert (linha.status, linha.erro) == ("enviada", None)
    assert linha.enviada_at is not None and linha.texto == texto and linha.anexo_id is None
    ch = await _chamado(db, "296305")
    if ch is not None:  # o chamado da devolução da TikTok é aberto pela aba
        (ev,) = await _eventos_comprador(db, ch.id)
        assert ev.direcao == "sistema" and "chat da TikTok" in ev.texto
    # enviada não volta pra fila
    await svc.processar_pendentes(db)
    assert len(await _tarefas(db)) == 1


async def test_perfil_em_uso_nao_gasta_tentativa_e_erro_da_loja_gasta_ate_desistir(
    client, make_user, auth_as, db, shopee
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="296306", order_sn="586006309818100005", **TIKTOK)

    (t,) = await _tarefas(db)
    await _devolver(db, t, ok=False, motivo="perfil_em_uso",
                    detalhe="perfil da loja aberto por outra pessoa — tento de novo depois")
    linha = await _linha(db, "296306")
    assert (linha.status, linha.tentativas) == ("pendente", 0)
    assert "aberto por outra pessoa" in linha.erro

    # o cron cria a próxima tarefa; agora erro de verdade (loja sem login)
    for n in (1, 2, 3):
        await svc.processar_pendentes(db)
        tarefas = await _tarefas(db)
        assert len(tarefas) == 1 + n
        await _devolver(db, tarefas[-1], ok=False, motivo="precisa_login",
                        detalhe="precisa entrar na loja TikTok injox no AdsPower")
        linha = await _linha(db, "296306")
        assert linha.tentativas == n
    assert linha.status == "falhou" and "precisa entrar" in linha.erro
    # falhou = acabou: o cron não cria mais tarefa
    await svc.processar_pendentes(db)
    assert len(await _tarefas(db)) == 4


async def test_chat_que_ja_fala_de_senha_encerra_na_hora_pra_alguem_olhar(
    client, make_user, auth_as, db, shopee
):
    """Mini 295333 (29/09): o comprador já tinha mandado "a senha é essa / 2706"
    no chat. O robô não repete o pedido e a linha fica vermelha na hora."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="296310", order_sn="586006309818100009", **TIKTOK)
    (t,) = await _tarefas(db)
    await _devolver(db, t, ok=False, motivo="ja_conversando",
                    detalhe="o chat com esse comprador já fala de senha — não mandei; alguém precisa olhar a conversa")
    linha = await _linha(db, "296310")
    assert (linha.status, linha.tentativas) == ("falhou", 0)
    assert "já fala de senha" in linha.erro
    await svc.processar_pendentes(db)
    assert len(await _tarefas(db)) == 1


async def test_motivo_trocado_antes_do_robo_cancela_e_o_robo_nao_reabre(
    client, make_user, auth_as, db, shopee
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="296307", order_sn="586006309818100006", **TIKTOK)
    r = await client.patch(
        f"/api/devolutions/{body['id']}",
        json={"motivo_devolucao": "Danificado (Outros)",
              "link_envio": "https://mega.nz/file/expedicao#K3yDoVideoNaMega0123456789abcdefghij"},
    )
    assert r.status_code == 200, r.text
    linha = await _linha(db, "296307")
    assert linha.status == "cancelada"
    (t,) = await _tarefas(db)
    await _devolver(db, t, ok=False, motivo="precisa_login", detalhe="x")
    assert (await _linha(db, "296307")).status == "cancelada"


async def test_flag_da_tiktok_desligada_nao_cria_tarefa(
    client, make_user, auth_as, db, shopee, monkeypatch
):
    monkeypatch.setattr(get_settings(), "tiktok_mensagens_comprador", False)
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="296308", order_sn="586006309818100007", **TIKTOK)
    assert (body["senha_status"], body["senha_erro"]) == ("pendente", "envio_desligado")
    assert await _tarefas(db) == []


async def test_lancamento_antigo_da_tiktok_entra_pela_varredura(
    client, make_user, auth_as, db, shopee
):
    """Os "Bloqueado" da TikTok de antes de 29/09 nasceram `sem_canal`; a
    varredura :25 reavalia sem_canal e agora eles viram tarefa do robô."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="296309", order_sn="586006309818100008", **TIKTOK)
    linha = await _linha(db, "296309")
    linha.status, linha.erro = "sem_canal", "sem_canal_tiktok"
    await db.commit()
    await db.execute(delete(LogisticaRoboComando))
    await db.commit()

    await svc.varrer_sem_mensagem(db, dias=30, limite=10)

    linha = await _linha(db, "296309")
    assert (linha.status, linha.erro) == ("pendente", None)
    assert len(await _tarefas(db)) == 1
