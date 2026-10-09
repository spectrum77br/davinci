"""A RESPOSTA de e-mail pela conversa do /atendimento, saindo pela fila DA Central (08/10/2026).

O caminho único (POST /api/atendimento/conversas/{id}/responder → `enviar` →
`responder.enfileirar_resposta` → `mail_central.queue_reply`), as travas, o
endereço que recebeu (nunca o principal no lugar dele), o formulário do site
(RF6), o modo teste e o "não responder" — e a volta: o Mac (o agente) pega o
job pelo lease, manda o recibo e a ponte passa o status para a mensagem.
O "incerto" se resolve por pessoa, sem reenviar nada.

Postgres local de verdade + HTTP em processo; nunca conta do Tuta nem envio.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    MailMailbox,
    MailOutbox,
    UserRole,
)
from app.models.mail_atendimento import MailMailboxSettings, MailOutboxMeta
from app.security.cipher import decrypt_json
from app.services.atendimento import enviar
from app.services.atendimento.constantes import ORIGEM_IA
from app.services.mail_atendimento import ponte
from tests.test_mail_ponte import (
    GERAL,
    TOKEN_GERAL,
    entrar,
    meta_de,
    rodar,
)
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte

API = "/api/atendimento"
MAIL = "/api/mail"


async def _ligar_envio(db, caixa: MailMailbox, **config) -> None:
    caixa = await db.get(MailMailbox, caixa.id)
    caixa.send_enabled = True
    caixa.agent_can_send = True
    caixa.state = "online"
    caixa.last_seen_at = datetime.now(UTC)
    cfg = await db.get(MailMailboxSettings, caixa.id)
    for nome, valor in {"envio_modo": "real", **config}.items():
        setattr(cfg, nome, valor)
    await db.commit()


async def _conversa_do_email(db, cena, **kw) -> tuple[UUID, UUID]:
    mid = await entrar(db, cena.geral, **kw)
    await rodar(db)
    meta = await meta_de(db, mid)
    assert meta.conversa_id is not None, meta.estado
    return mid, meta.conversa_id


async def _responder(client, conversa_id, texto="Chega amanhã.", **extra):
    return await client.post(
        f"{API}/conversas/{conversa_id}/responder", json={"texto": texto, **extra}
    )


async def _jobs(db) -> list[MailOutbox]:
    return list(
        (
            await db.execute(
                select(MailOutbox)
                .order_by(MailOutbox.created_at)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


@pytest.fixture
async def admin(cena, auth_as):
    auth_as(cena.dono)
    return cena.dono


# ─────────────── o caminho feliz e a volta ───────────────


async def test_responde_pelo_alias_que_recebeu_pela_fila_dela_e_volta_enviada(
    db, cena, client, admin
):
    mid, conversa_id = await _conversa_do_email(db, cena, assunto="Pergunta sobre o pedido")
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id)
    assert r.status_code == 200, r.text
    msg = r.json()["mensagem"]
    assert msg["status"] == "enviando" and msg["email"]["tipo"] == "resposta"
    [job] = await _jobs(db)
    assert job.status == "queued" and str(job.request_id) == msg["id"]
    assert job.message_id == mid and job.author_user_id == cena.dono.id
    carga = decrypt_json(job.content_enc)
    assert carga["from_address"] == "21max@tuta.com" and carga["to"] == "maria@gmail.com"
    assert carga["subject"] == "Re: Pergunta sobre o pedido"
    assert carga["text"].startswith("Chega amanhã.\n\nAtenciosamente,\n")
    assert "escreveu:\n> Quando chega?" in carga["text"]
    liga = await db.get(MailOutboxMeta, job.id)
    assert str(liga.atendimento_mensagem_id) == msg["id"] and liga.conversa_id == conversa_id
    # A ligação é da conversa e lembra que o job nasceu na caixa da EMPRESA (a
    # regra do lease vale por isso, mesmo se a caixa voltar a ser privada).
    assert (liga.origem, liga.caixa_empresa) == ("conversa", True)
    # O Mac pega pelo lease (o contrato v1 dele) e manda o recibo.
    agente = f"{MAIL}/agent/{cena.geral.id}"
    cab = {"Authorization": f"Bearer {TOKEN_GERAL}"}
    r = await client.post(f"{agente}/outbox/lease", json={}, headers=cab)
    [tarefa] = r.json()["jobs"]
    assert tarefa["from_address"] == "21max@tuta.com"
    r = await client.post(
        f"{agente}/outbox/{tarefa['id']}/receipt",
        json={
            "lease_token": tarefa["lease_token"],
            "status": "sent",
            "message_id": "<enviado@tuta.com>",
        },
        headers=cab,
    )
    assert r.status_code == 200, r.text
    assert (await rodar(db))["envios"] == 1
    nossa = await db.get(AtendimentoMensagem, UUID(msg["id"]))
    await db.refresh(nossa)
    assert nossa.status == "enviada" and nossa.enviada_em is not None
    await db.refresh(liga)
    assert liga.enviado_mid_hash == ponte.hash_mid("<enviado@tuta.com>")
    c = await db.get(AtendimentoConversa, conversa_id)
    await db.refresh(c)
    assert c.aguardando_resposta is False


async def test_segundo_clique_nao_vira_dois_envios(db, cena, client, admin):
    _mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral)
    assert (await _responder(client, conversa_id)).status_code == 200
    r = await _responder(client, conversa_id, texto="Outra coisa.")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "envio_em_andamento"
    assert len(await _jobs(db)) == 1


# ─────────────── as travas ───────────────


async def test_modo_teste_so_responde_para_a_lista(db, cena, client, admin):
    _mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral, envio_modo="teste", destinatarios_teste=[])
    r = await _responder(client, conversa_id)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "fora_da_lista_de_teste"
    assert await _jobs(db) == []
    await _ligar_envio(db, cena.geral, envio_modo="teste", destinatarios_teste=["maria@gmail.com"])
    assert (await _responder(client, conversa_id)).status_code == 200


async def test_nao_responder_pede_confirmacao(db, cena, client, admin):
    _mid, conversa_id = await _conversa_do_email(
        db,
        cena,
        pasta="mensagens ml",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto="Nova mensagem",
    )
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "remetente_nao_responde"
    r = await _responder(client, conversa_id, confirmar_nao_responde=True)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    ("kw", "codigo"),
    [
        ({"pasta": "vendas ml"}, "email_historico"),
        (
            {"de": "suporte@mercadolivre-br.com", "de_nome": "Mercado Livre"},
            "remetente_suspeito",
        ),
        ({"pasta": "problema amazon", "para": ["mike14@tuta.com"]}, "resposta_pela_amazon"),
    ],
)
async def test_travas_do_email(db, cena, client, admin, kw, codigo):
    _mid, conversa_id = await _conversa_do_email(db, cena, **kw)
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id)
    assert r.status_code == 409 and r.json()["detail"]["code"] == codigo
    assert await _jobs(db) == []


async def test_caixa_sem_envio_e_mac_desconectado(db, cena, client, admin):
    _mid, conversa_id = await _conversa_do_email(db, cena)
    r = await _responder(client, conversa_id)
    assert r.json()["detail"]["code"] == "caixa_sem_envio"
    await _ligar_envio(db, cena.geral)
    caixa = await db.get(MailMailbox, cena.geral.id)
    caixa.last_seen_at = datetime.now(UTC) - timedelta(minutes=10)
    await db.commit()
    r = await _responder(client, conversa_id)
    assert r.json()["detail"]["code"] == "mac_desconectado"


async def test_freio_geral_do_env(db, cena, client, admin, monkeypatch):
    _mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral)
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", False)
    r = await _responder(client, conversa_id)
    assert r.json()["detail"]["code"] == "envio_desligado"


async def test_quem_so_le_nao_responde(db, cena, client, make_user, auth_as, monkeypatch):
    _mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral)
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "outra@davinci-test.com")
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await _responder(client, conversa_id)
    assert r.status_code == 403
    assert await _jobs(db) == []


async def test_a_ia_nunca_responde_email(db, cena, admin):
    _mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral)
    c = await db.get(AtendimentoConversa, conversa_id)
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_resposta(db, c, "oi", user=None, origem=ORIGEM_IA)
    assert e.value.code == "auto_desligado"
    # Nenhum outro caminho (foto, automática) sai com o e-mail.
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar._destino(db, c, origem="davinci_humano")
    assert e.value.code == "canal_sem_envio"
    # A faixa da tela diz o mesmo que o envio.
    assert await enviar.motivo_para_nao_enviar(db, c) is None


async def test_nunca_cai_no_endereco_principal(db, cena, client, admin):
    """A resposta do cliente que veio sem o nosso alias no Para (cópia oculta)
    entra pelo fio; a Central, com o remetente estrito desligado, cairia no
    endereço PRINCIPAL — a resposta pela conversa sai pelo alias da loja."""
    primeiro, conversa_id = await _conversa_do_email(db, cena, message_id="<a1@cliente.com>")
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        para=["outra@pessoa.com"],
        in_reply_to="<a1@cliente.com>",
        corpo="E aí?",
    )
    await rodar(db)
    assert (await meta_de(db, mid)).conversa_id == conversa_id
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id, mail_message_id=str(mid))
    assert r.status_code == 200, r.text
    [job] = await _jobs(db)
    assert decrypt_json(job.content_enc)["from_address"] == "21max@tuta.com" != GERAL
    # Com o remetente estrito ligado, sem alias que recebeu, não responde (nem pelo principal).
    await db.execute(MailOutbox.__table__.update().values(status="failed", error_code="teste"))
    await db.execute(
        AtendimentoMensagem.__table__.update()
        .where(AtendimentoMensagem.status == "enviando")
        .values(status="falhou")
    )
    await db.commit()
    await _ligar_envio(db, cena.geral, remetente_estrito=True)
    r = await _responder(client, conversa_id, texto="De novo.", mail_message_id=str(mid))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_alias"
    assert primeiro


# ─────────────── RF6: o chamado do site ───────────────


async def test_rf6_resposta_do_formulario_vai_ao_cliente_pelo_email_do_tipo(
    db, cena, client, admin
):
    _mid, conversa_id = await _conversa_do_email(
        db,
        cena,
        pasta="*uranyx atacado",
        de="atacado@uranyx.com.br",
        para=["atacado@uranyx.com.br"],
        assunto="Pedido de atacado",
        corpo="Protocolo: UA-26-0007\nE-mail: Compras@Revenda.com.br\nQuero 50 unidades.",
    )
    await _ligar_envio(db, cena.geral)
    r = await client.get(f"{API}/email/conversas/{conversa_id}/previa")
    previa = r.json()
    assert previa["formulario"] is True and previa["para"] == "compras@revenda.com.br"
    assert previa["de"] == "atacado@uranyx.com.br"
    assert previa["assunto"] == "Re: [UA-26-0007] Pedido de atacado"
    r = await _responder(client, conversa_id, texto="Segue a tabela de atacado.")
    assert r.status_code == 200, r.text
    [job] = await _jobs(db)
    carga = decrypt_json(job.content_enc)
    assert carga["to"] == "compras@revenda.com.br"
    assert carga["from_address"] == "atacado@uranyx.com.br"
    assert carga["subject"] == "Re: [UA-26-0007] Pedido de atacado"
    assert "Equipe Uranyx" in carga["text"]


async def test_rf6_protocolo_entra_no_assunto_com_um_re_so(db, cena, client, admin):
    _mid, conversa_id = await _conversa_do_email(
        db,
        cena,
        pasta="*uranyx duvidas",
        de="duvidas@uranyx.com.br",
        para=["duvidas@uranyx.com.br"],
        assunto="RE: Dúvida sobre o app",
        corpo="Protocolo UDS-26-0004\nE-mail: ana@gmail.com",
    )
    await _ligar_envio(db, cena.geral)
    assert (await _responder(client, conversa_id, texto="Oi Ana.")).status_code == 200
    [job] = await _jobs(db)
    assert decrypt_json(job.content_enc)["subject"] == "Re: [UDS-26-0004] Dúvida sobre o app"


async def test_rf6_formulario_sem_email_do_cliente_nao_responde(db, cena, client, admin):
    _mid, conversa_id = await _conversa_do_email(
        db,
        cena,
        pasta="*uranyx sac",
        de="sac@uranyx.com.br",
        para=["sac@uranyx.com.br"],
        assunto="[US-26-0009] Dúvida",
        corpo="Mensagem: sem e-mail do cliente",
    )
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "para_e_nosso"


# ─────────────── o incerto e a conferência ───────────────


async def _incerto(db, cena, client) -> tuple[UUID, UUID, MailOutbox]:
    _mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id)
    msg_id = UUID(r.json()["mensagem"]["id"])
    [job] = await _jobs(db)
    job.status = "uncertain"
    job.error_code = "receipt_timeout"
    await db.commit()
    await rodar(db)
    nossa = await db.get(AtendimentoMensagem, msg_id)
    await db.refresh(nossa)
    assert nossa.status == "revisar" and nossa.erro.startswith("mail:receipt_timeout")
    return conversa_id, msg_id, job


async def test_conferir_nao_saiu_resolve_o_job_dela_sem_reenviar(db, cena, client, admin):
    conversa_id, msg_id, job = await _incerto(db, cena, client)
    r = await client.post(f"{API}/mensagens/{msg_id}/conferir", json={"saiu": False})
    assert r.status_code == 200, r.text
    assert r.json()["mensagem"]["status"] == "falhou"
    await db.refresh(job)
    assert (job.status, job.error_code) == ("failed", "resolved_not_sent")
    liga = await db.get(MailOutboxMeta, job.id)
    await db.refresh(liga)
    assert (liga.resolucao, liga.resolvido_por) == ("nao_saiu", cena.dono.id)
    # Liberou o "1 resposta viva": dá para responder de novo.
    r = await _responder(client, conversa_id, texto="Tentando de novo.")
    assert r.status_code == 200, r.text
    assert await db.scalar(select(func.count()).select_from(MailOutbox)) == 2


async def test_resolver_pela_caixa_da_central_tambem_passa_para_a_conversa(db, cena, client, admin):
    _conversa_id, msg_id, job = await _incerto(db, cena, client)
    r = await client.post(f"{MAIL}/outbox/{job.id}/resolve", json={"saiu": True})
    assert r.status_code == 200, r.text
    nossa = await db.get(AtendimentoMensagem, msg_id)
    await db.refresh(nossa)
    assert nossa.status == "enviada"
    liga = await db.get(MailOutboxMeta, job.id)
    await db.refresh(liga)
    assert liga.resolucao == "saiu" and liga.status_visto == "sent"


async def test_resposta_com_job_vivo_nao_e_envio_preso(db, cena, client, admin):
    _mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id)
    msg_id = UUID(r.json()["mensagem"]["id"])
    # O Mac sumiu: o job fica na fila dela; a nossa mensagem não vira "revisar".
    await db.execute(
        AtendimentoMensagem.__table__.update()
        .where(AtendimentoMensagem.id == msg_id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=1))
    )
    await db.commit()
    assert await enviar.aposentar_envios_presos(db) == 0
    await db.commit()
    nossa = await db.get(AtendimentoMensagem, msg_id)
    await db.refresh(nossa)
    assert nossa.status == "enviando"
    c = await db.get(AtendimentoConversa, conversa_id)
    recusa = await enviar.motivo_para_nao_enviar(db, c)
    assert recusa is not None and recusa.code == "envio_em_andamento"


async def test_destinatario_do_formulario_so_na_caixa_da_empresa_e_de_endereco_nosso(
    db, cena, admin
):
    """O `form_recipient` (interno, nunca do navegador) da Central só vale numa
    caixa `empresa` e quando o e-mail original veio de um endereço DELA."""
    from app.schemas.mail import ReplyIn
    from app.services import mail_central

    await _ligar_envio(db, cena.geral)
    await _ligar_envio(db, cena.goslin)
    de_cliente = await entrar(db, cena.geral, de="maria@gmail.com")
    do_site_na_privada = await entrar(
        db, cena.goslin, pasta="INBOX", de="mia30@tuta.com", para=["mia30@tuta.com"]
    )
    casos = (
        (cena.geral.id, de_cliente),  # original de fora: não é formulário
        (cena.goslin.id, do_site_na_privada),  # caixa privada: nunca
    )
    dono_id = cena.dono.id
    for caixa_id, mid in casos:
        mailbox = await db.get(MailMailbox, caixa_id)
        message = await db.get(ponte.MailMessage, mid)
        dono = await db.get(type(cena.dono), dono_id)
        with pytest.raises(mail_central.MailError) as e:
            await mail_central.queue_reply(
                db,
                mailbox,
                message,
                dono,
                ReplyIn(request_id=uuid4(), text="oi", from_address=None),
                form_recipient="golpe@fora.com",
            )
        assert e.value.code == "form_recipient_not_allowed"
        await db.rollback()


# ─────────────── a crítica de 08/10 ───────────────


async def test_o_endereco_principal_nunca_responde_na_caixa_da_empresa(db, cena, client, admin):
    """O e-mail que chegou SÓ no principal (o login da conta do Tuta): escolher a
    loja não leva o principal como alias e a resposta não sai por ele."""
    mid = await entrar(db, cena.geral, para=[GERAL])
    await rodar(db)
    assert (await meta_de(db, mid)).estado == "sem_loja"
    r = await client.post(
        f"{API}/email/emails/{mid}/loja", json={"store_info_id": str(cena.loja_ml.id)}
    )
    assert r.status_code == 200, r.text
    meta = await meta_de(db, mid)
    assert meta.conversa_id is not None
    c = await db.get(AtendimentoConversa, meta.conversa_id)
    assert c.dados["mail"]["alias"] != GERAL
    await _ligar_envio(db, cena.geral)
    previa = (await client.get(f"{API}/email/conversas/{meta.conversa_id}/previa")).json()
    assert previa["de"] is None
    assert [b["codigo"] for b in previa["bloqueios"]] == ["so_endereco_principal"]
    r = await _responder(client, meta.conversa_id, mail_message_id=str(mid))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "so_endereco_principal"
    assert await _jobs(db) == []


async def test_respondido_pela_caixa_crua_pede_confirmacao_na_conversa(db, cena, client, admin):
    mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral)
    r = await client.post(
        f"{MAIL}/messages/{mid}/reply",
        json={"request_id": str(uuid4()), "text": "Pela caixa crua."},
    )
    assert r.status_code == 202, r.text
    [crua] = await _jobs(db)
    crua.status = "sent"
    crua.completed_at = datetime.now(UTC)
    await db.commit()
    previa = (await client.get(f"{API}/email/conversas/{conversa_id}/previa")).json()
    [trava] = [b for b in previa["bloqueios"] if b["codigo"] == "ja_respondido_pela_caixa"]
    assert trava["confirmavel"] is True
    r = await _responder(client, conversa_id)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_respondido_pela_caixa"
    r = await _responder(client, conversa_id, confirmar_nao_responde=True)
    assert r.status_code == 200, r.text
    # A da própria conversa não conta como "respondido pela caixa".
    previa = (await client.get(f"{API}/email/conversas/{conversa_id}/previa")).json()
    assert len([b for b in previa["bloqueios"] if b["codigo"] == "ja_respondido_pela_caixa"]) == 1


async def test_mac_que_sumiu_com_o_lease_vai_para_revisar_e_se_confere(db, cena, client, admin):
    _mid, conversa_id = await _conversa_do_email(db, cena)
    await _ligar_envio(db, cena.geral)
    r = await _responder(client, conversa_id)
    msg_id = UUID(r.json()["mensagem"]["id"])
    agente = f"{MAIL}/agent/{cena.geral.id}"
    cab = {"Authorization": f"Bearer {TOKEN_GERAL}"}
    [tarefa] = (await client.post(f"{agente}/outbox/lease", json={}, headers=cab)).json()["jobs"]
    # O Mac sumiu no meio: 20 min de lease, a mensagem de 6 h atrás.
    job = await db.get(MailOutbox, UUID(tarefa["id"]))
    job.leased_at = datetime.now(UTC) - timedelta(minutes=20)
    await db.execute(
        AtendimentoMensagem.__table__.update()
        .where(AtendimentoMensagem.id == msg_id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=6))
    )
    await db.commit()
    assert await enviar.aposentar_envios_presos(db) == 1
    await db.commit()
    nossa = await db.get(AtendimentoMensagem, msg_id)
    await db.refresh(nossa)
    assert nossa.status == "revisar"
    filas = (await client.get(f"{API}/email/filas")).json()
    assert filas["revisar_envio"] == 1
    itens = (await client.get(f"{API}/email/envios?estado=revisar")).json()["itens"]
    assert [i["id"] for i in itens] == [tarefa["id"]] and itens[0]["lease_vencido"] is True
    r = await client.post(f"{API}/mensagens/{msg_id}/conferir", json={"saiu": False})
    assert r.status_code == 200, r.text
    await db.refresh(job)
    assert (job.status, job.error_code) == ("failed", "resolved_not_sent")
    # O recibo do Mac que volta DEPOIS: a Central recusa, a conversa avisa.
    r = await client.post(
        f"{agente}/outbox/{tarefa['id']}/receipt",
        json={"lease_token": tarefa["lease_token"], "status": "sent", "message_id": "<t@x>"},
        headers=cab,
    )
    assert r.status_code == 409
    liga = await db.get(MailOutboxMeta, job.id)
    await db.refresh(liga)
    assert liga.recibo_tardio == "sent"
    notas = (
        (
            await db.execute(
                select(AtendimentoMensagem).where(
                    AtendimentoMensagem.conversa_id == conversa_id,
                    AtendimentoMensagem.externo_id == f"mail-recibo-tardio:{job.id}",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notas) == 1 and "DEPOIS da conferência" in notas[0].texto


async def test_resposta_da_conversa_na_fila_nao_sai_com_o_freio_fechado(
    db, cena, client, admin, monkeypatch
):
    """Na caixa PRIVADA também (a ponte da Goslin): o job que nasceu da conversa
    segue o freio geral na hora do lease, e passa de 2 h → failed."""
    mid = await entrar(db, cena.goslin, pasta="INBOX", para=["mia30@tuta.com"])
    await rodar(db)
    meta = await meta_de(db, mid)
    r = await client.post(
        f"{API}/email/emails/{mid}/loja", json={"store_info_id": str(cena.loja_oliveira_ml.id)}
    )
    assert r.status_code == 200, r.text
    meta = await meta_de(db, mid)
    await _ligar_envio(db, cena.goslin)
    r = await _responder(client, meta.conversa_id)
    assert r.status_code == 200, r.text
    msg_id = UUID(r.json()["mensagem"]["id"])
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", False)
    agente = f"{MAIL}/agent/{cena.goslin.id}"
    from tests.test_mail_ponte import TOKEN_GOSLIN

    cab = {"Authorization": f"Bearer {TOKEN_GOSLIN}"}
    assert (await client.post(f"{agente}/outbox/lease", json={}, headers=cab)).json()["jobs"] == []
    [job] = await _jobs(db)
    assert job.status == "queued"
    job.created_at = datetime.now(UTC) - timedelta(hours=3)
    await db.commit()
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    assert (await client.post(f"{agente}/outbox/lease", json={}, headers=cab)).json()["jobs"] == []
    await db.refresh(job)
    assert (job.status, job.error_code) == ("failed", "queued_timeout")
    await rodar(db)
    nossa = await db.get(AtendimentoMensagem, msg_id)
    await db.refresh(nossa)
    assert nossa.status == "falhou" and "mais de 2 h na fila" in nossa.erro
