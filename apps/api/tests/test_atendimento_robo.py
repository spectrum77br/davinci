"""A recepção do robô do Mac mini (Temu, AliExpress) — API, banco e a caixa.

Contrato robô → DaVinci (routers/atendimento_robo.py):

- token: setting vazia = desligado (404); token errado ou sem token = 401;
- pulso cria o canal da loja SEM integração (perfil do AdsPower), em
  `observar`; sem pulso há mais de N min a loja aparece `parado` na barra;
  sessão caída e URL com conversa aberta (marca lido) ficam à vista;
- eventos gravam conversa e mensagem pela porta única, de forma idempotente,
  cada loja com as suas conversas (mesmo id em duas lojas Temu não mistura);
- resposta dada no Seller Center tira da fila e aposenta a sugestão da IA;
  a do robô da Temu não;
- a prévia da lista do AliExpress é adotada pela mensagem de verdade;
- nada sai pelo DaVinci (responda no Seller Center), o modo não passa de
  observar/copiloto, e o sync da API nunca toca a loja do robô;
- a IA do cron pega a conversa do robô (copiloto).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    User,
)
from app.routers import atendimento as rota_caixa
from app.services.atendimento import clientes, enviar, ia, robo, sync

URL = "/api/atendimento/robo"
TOKEN = "segredo-do-robo"  # noqa: S105 — token de teste
AGORA = datetime.now(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
TEXTO = "Oi, cadê meu pedido? Meu CPF é 123.456.789-00"


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    """A caixa está SÓ ADMIN por enquanto (rota_caixa.SO_ADMIN, 30/09/2026). Os
    testes daqui usam não-admin com o recurso `atendimento` porque cobrem a
    permissão fina (view/edit/delete, equipe, `auto` só admin), que volta a
    valer quando a caixa abrir para a equipe. A trava de admin tem os testes
    dela em test_atendimento_so_admin.py."""
    monkeypatch.setattr(rota_caixa, "SO_ADMIN", False)


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_robo_token", TOKEN)
    monkeypatch.setattr(s, "atendimento_robo_parado_min", 5)
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    return s


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


async def _post(client, rota: str, corpo: dict, token: str | None = TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return await client.post(f"{URL}/{rota}", json=corpo, headers=headers)


def _pulso(perfil="k1dkegpc", plataforma="temu", loja="Barbosa", estado="lendo", **extra):
    return {
        "perfil_id": perfil,
        "plataforma": plataforma,
        "loja": loja,
        "estado": estado,
        "url": extra.pop("url", "https://br.seller.temu.com/chat.html"),
        "detalhe": extra.pop("detalhe", None),
        "versao": "1.0.0",
        **extra,
    }


def _ts(minutos_atras: float) -> int:
    return int((AGORA - timedelta(minutes=minutos_atras)).timestamp())


def _msg_temu(msg_id, conv, *, de, para, texto=TEXTO, minutos=10, **extra):
    return {
        "msg_id": msg_id,
        "conv_id": conv,
        "type": extra.pop("tipo", 0),
        "content": texto,
        "info": {},
        "ts": str(_ts(minutos)),
        "from": {"uid": "buyer-1" if de == 1 else "mall-1", "user_type": de},
        "to": {"uid": "mall-1" if de == 1 else "buyer-1", "user_type": para},
        "context": extra.pop("context", {"robot": 0}),
    }


def _evento_http(caminho: str, resultado: dict) -> dict:
    return {
        "tipo": "http",
        "url": f"https://br.seller.temu.com{caminho}",
        "metodo": "POST",
        "status": 200,
        "recebido_em": AGORA.isoformat(),
        "corpo": json.dumps({"success": True, "result": resultado}),
    }


def _conv_list(conv="conv-1", nome="Maria", msgs=None) -> dict:
    ultima = (msgs or [_msg_temu("m-1", conv, de=1, para=2)])[-1]
    return _evento_http(
        "/api/plateau/conv/getConvList",
        {
            "data": [
                {
                    "convId": conv,
                    "convInfo": {
                        "name": nome,
                        "avatar": "https://img.temu/a.png",
                        "unreadCount": 1,
                    },
                    "message": ultima,
                    "stateData": {"groupList": ["unReply"]},
                }
            ]
        },
    )


def _sync(*msgs) -> dict:
    return _evento_http(
        "/api/plateau/sync/message",
        {"syncData": [{"seqType": 1, "data": [{"message": m} for m in msgs]}]},
    )


def _eventos(eventos, perfil="k1dkegpc", plataforma="temu", loja="Barbosa") -> dict:
    return {"perfil_id": perfil, "plataforma": plataforma, "loja": loja, "eventos": eventos}


# Relidos do banco (`populate_existing`): a API gravou em outra sessão. Sem
# `expire_all`, que expiraria também o usuário que o `auth_as` devolve.
_RELER = {"populate_existing": True}


async def _canal(db: AsyncSession, perfil="k1dkegpc") -> AtendimentoCanal:
    await db.commit()
    return (
        await db.execute(
            select(AtendimentoCanal)
            .where(AtendimentoCanal.robo_perfil_id == perfil)
            .execution_options(**_RELER)
        )
    ).scalar_one()


async def _conversas(db: AsyncSession) -> list[AtendimentoConversa]:
    await db.commit()
    return list(
        (
            await db.execute(
                select(AtendimentoConversa)
                .order_by(AtendimentoConversa.externo_id)
                .execution_options(**_RELER)
            )
        )
        .scalars()
        .all()
    )


async def _mensagens(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    await db.commit()
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em)
                .execution_options(**_RELER)
            )
        )
        .scalars()
        .all()
    )


# ── Token ─────────────────────────────────────────────────────────────────


async def test_token_vazio_desliga_errado_recusa_certo_entra(client, _config, monkeypatch):
    assert (await _post(client, "pulso", _pulso(), token=None)).status_code == 401
    assert (await _post(client, "pulso", _pulso(), token="outro")).status_code == 401  # noqa: S106
    r = await client.post(f"{URL}/pulso", json=_pulso(), headers={"Authorization": TOKEN})
    assert r.status_code == 401  # sem o "Bearer"
    ok = await _post(client, "pulso", _pulso())
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    monkeypatch.setattr(_config, "atendimento_robo_token", "")
    desligado = await _post(client, "eventos", _eventos([]))
    assert desligado.status_code == 404
    assert desligado.json()["detail"]["code"] == "robo_desligado"


async def test_corpo_invalido_nao_ecoa_o_valor(client):
    corpo = _eventos([{"tipo": "ftp", "corpo": TEXTO}])
    r = await _post(client, "eventos", corpo)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "corpo_invalido"
    assert "CPF" not in r.text and "ftp" not in r.text
    # Campo informativo ilegível não derruba a leva (vira None).
    solto = _eventos([{**_conv_list(), "recebido_em": "lixo", "status": "x", "metodo": "M" * 40}])
    assert (await _post(client, "eventos", solto)).status_code == 200
    muitos = _eventos([{"tipo": "ws", "corpo": "x"}] * 201)
    assert (await _post(client, "eventos", muitos)).status_code == 422
    assert (await _post(client, "pulso", _pulso(perfil="com espaço"))).status_code == 422


# ── Pulso e saúde da loja ─────────────────────────────────────────────────


async def test_pulso_cria_canal_sem_integracao_em_observar(client, db):
    assert (await _post(client, "pulso", _pulso(estado="iniciando"))).status_code == 200
    canal = await _canal(db)
    assert canal.integration_id is None
    assert (canal.plataforma, canal.canal, canal.modo, canal.status) == (
        "temu",
        "chat",
        "observar",
        "novo",
    )
    assert canal.cursor["robo"]["loja"] == "Barbosa"
    # Pulso de novo: o mesmo canal, agora lendo.
    await _post(client, "pulso", _pulso())
    canal = await _canal(db)
    assert canal.status == "ok" and canal.ultimo_ok_em is not None
    assert await db.scalar(select(func.count()).select_from(AtendimentoCanal)) == 1
    # O perfil é de uma loja Temu: não vira AliExpress calado.
    r = await _post(client, "pulso", _pulso(plataforma="aliexpress"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "perfil_de_outra_plataforma"


async def test_sessao_caiu_e_aba_numa_conversa_ficam_a_vista(client, db):
    await _post(client, "pulso", _pulso(estado="sessao_caiu", detalhe="redirecionou para o login"))
    canal = await _canal(db)
    assert canal.status == "sessao_caiu"
    assert "entrar de novo" in canal.ultimo_erro
    # A aba com ?posn= marca como lida para a equipe inteira: erro, bem à vista.
    await _post(
        client, "pulso", _pulso(url="https://br.seller.temu.com/chat.html?posn=PO-211-1&x=1")
    )
    canal = await _canal(db)
    assert canal.status == "erro"
    assert "posn" in canal.ultimo_erro
    # A URL guardada não leva a query (pode ter token).
    assert canal.cursor["robo"]["url"] == "https://br.seller.temu.com/chat.html"


async def test_sem_pulso_a_loja_vira_leitura_parada_na_barra(client, db, pessoa):
    await _post(client, "pulso", _pulso(perfil="k1dkegpc", loja="Barbosa"))
    await _post(client, "pulso", _pulso(perfil="k1dof4hk", loja="JLAS"))
    await _post(
        client,
        "pulso",
        _pulso(
            perfil="k1do5vfw",
            plataforma="aliexpress",
            loja="Vita",
            url="https://gsp.aliexpress.com/m_apps/im-chat/im#/window",
        ),
    )
    # O robô da JLAS parou há 12 min.
    jlas = await _canal(db, "k1dof4hk")
    # Relógio de AGORA (não o do import: a suíte inteira leva minutos).
    velho = (datetime.now(UTC) - timedelta(minutes=12, seconds=20)).isoformat()
    jlas.cursor = {**jlas.cursor, "robo": {**jlas.cursor["robo"], "ultimo_pulso_em": velho}}
    await db.commit()

    r = await client.get("/api/atendimento/resumo")
    assert r.status_code == 200, r.text
    corpo = r.json()
    lojas = {(lj["plataforma"], lj["conta"]): lj for lj in corpo["lojas"]}
    barbosa, parada, vita = (
        lojas[("temu", "Barbosa")],
        lojas[("temu", "JLAS")],
        lojas[("aliexpress", "Vita")],
    )
    assert barbosa["integration_id"] is None and barbosa["canal_id"]
    assert barbosa["status_canal"] == "ok"
    assert parada["status_canal"] == "parado"
    assert parada["status_motivo"].startswith(
        "Leitura parada: o robô do Mac mini não dá sinal há 12 min"
    )
    assert vita["status_canal"] == "ok"
    # As plataformas do robô entram no resumo quando há loja delas.
    assert {"temu", "aliexpress"} <= {p["plataforma"] for p in corpo["plataformas"]}
    canais = {c["robo_perfil_id"]: c for c in corpo["canais"]}
    assert canais["k1dof4hk"]["status"] == "parado"
    assert canais["k1dkegpc"]["integration_id"] is None


# ── Eventos ───────────────────────────────────────────────────────────────


async def test_eventos_temu_gravam_e_sao_idempotentes(client, db, pessoa):
    corpo = _eventos(
        [
            _conv_list(),
            _sync(
                _msg_temu("m-1", "conv-1", de=1, para=2),
                _msg_temu("m-2", "conv-1", de=1, para=2, minutos=5),
            ),
            {
                "tipo": "ws",
                "url": "wss://br.seller.temu.com?ws-titan-request-sign=x",
                "corpo": "AAAA",
            },
            _evento_http("/api/plateau/rota/nova", {}),
        ]
    )
    r = await _post(client, "eventos", corpo)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "gravadas": 2, "conversas": 1, "ignorados": 2, "erros": 0}
    [conversa] = await _conversas(db)
    canal = await _canal(db)
    assert conversa.integration_id is None and conversa.canal_id == canal.id
    assert (conversa.plataforma, conversa.canal, conversa.externo_id) == ("temu", "chat", "conv-1")
    assert conversa.conta == "Barbosa"
    assert conversa.comprador_nome == "Maria" and conversa.nao_lidas == 1
    assert conversa.aguardando_resposta is True and conversa.prazo_resposta_em is not None
    assert conversa.dados["robo"]["grupos"] == ["unReply"]
    assert conversa.dados["robo"]["perfil_id"] == "k1dkegpc"
    msgs = await _mensagens(db, conversa.id)
    assert [(m.externo_id, m.autor, m.origem) for m in msgs] == [
        ("m-1", "cliente", "cliente"),
        ("m-2", "cliente", "cliente"),
    ]
    # O que o leitor não conhece fica no canal, pelo NOME.
    assert "http:/api/plateau/rota/nova" in canal.cursor["robo"]["desconhecidos"]
    assert canal.cursor["robo"]["ultimo_evento_em"]

    # A mesma leva de novo: nada duplica.
    de_novo = await _post(client, "eventos", corpo)
    assert de_novo.json()["gravadas"] == 0
    assert len(await _mensagens(db, conversa.id)) == 2
    assert len(await _conversas(db)) == 1

    # Aparece na lista (e a loja do robô filtra pelo canal).
    lista = await client.get(
        "/api/atendimento/conversas",
        params={"plataforma": "temu", "canal_id": str(canal.id), "filtro": "aguardando"},
    )
    assert lista.status_code == 200, lista.text
    assert [i["conta"] for i in lista.json()["itens"]] == ["Barbosa"]


async def test_mesma_conversa_em_duas_lojas_temu_nao_se_mistura(client, db, pessoa):
    await _post(
        client, "eventos", _eventos([_conv_list("conv-9")], perfil="k1dkegpc", loja="Barbosa")
    )
    await _post(client, "eventos", _eventos([_conv_list("conv-9")], perfil="k1docw56", loja="Atv"))
    conversas = await _conversas(db)
    assert sorted(c.conta for c in conversas) == ["Atv", "Barbosa"]
    assert len({c.canal_id for c in conversas}) == 2
    # Na barra, uma linha por loja (sem integração, a loja é o canal).
    r = await client.get("/api/atendimento/resumo")
    assert r.status_code == 200, r.text
    temu = [lj for lj in r.json()["lojas"] if lj["plataforma"] == "temu"]
    assert sorted((lj["conta"], lj["aguardando"], lj["nao_lidas"]) for lj in temu) == [
        ("Atv", 1, 1),
        ("Barbosa", 1, 1),
    ]
    assert {lj["canal_id"] for lj in temu} == {str(c.canal_id) for c in conversas}
    temu_plat = next(p for p in r.json()["plataformas"] if p["plataforma"] == "temu")
    assert temu_plat["aguardando"] == 2
    # As métricas também separam as duas lojas.
    m = await client.get("/api/atendimento/metricas")
    assert m.status_code == 200, m.text
    assert sorted(lj["conta"] for lj in m.json()["lojas"] if lj["plataforma"] == "temu") == [
        "Atv",
        "Barbosa",
    ]


async def test_resposta_no_seller_center_tira_da_fila_e_aposenta_sugestao(client, db):
    await _post(
        client, "eventos", _eventos([_sync(_msg_temu("m-1", "conv-1", de=1, para=2, minutos=20))])
    )
    [conversa] = await _conversas(db)
    [gatilho] = await _mensagens(db, conversa.id)
    rascunho = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=gatilho.id,
        texto="Já vou ver!",
        status="pendente",
    )
    db.add(rascunho)
    await db.commit()

    # O robô da Temu respondeu sozinho: NÃO é a equipe — continua na fila.
    robo_temu = _msg_temu(
        "m-2",
        "conv-1",
        de=2,
        para=1,
        texto="Olá! Sou o assistente.",
        minutos=15,
        context={"robot": 1},
    )
    await _post(client, "eventos", _eventos([_sync(robo_temu)]))
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is True
    msgs = await _mensagens(db, conversa.id)
    assert (msgs[-1].autor, msgs[-1].origem) == ("sistema", "sistema")
    assert msgs[-1].payload.get("robo_plataforma") is True

    # A equipe respondeu no Seller Center: sai da fila, a sugestão fica para trás.
    loja = _msg_temu("m-3", "conv-1", de=2, para=1, texto="Enviamos hoje.", minutos=5)
    await _post(client, "eventos", _eventos([_sync(loja)]))
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is False and conversa.situacao == "respondida"
    msgs = await _mensagens(db, conversa.id)
    assert (msgs[-1].autor, msgs[-1].origem, msgs[-1].status) == ("loja", "externo", "enviada")
    await db.refresh(rascunho)
    assert rascunho.status == "substituido"


def _sessao_ae(sid: str, conteudo: str, **dados_extra) -> dict:
    return {
        "tipo": "http",
        "url": "https://seller-acs.aliexpress.com/h5/"
        "mtop.gsp.im.use.web.seller.messagebox.querysessionlist/1.0/?t=1",
        "corpo": json.dumps(
            {
                "api": "mtop.gsp.im.use.web.seller.messagebox.querySessionList",
                "ret": ["SUCCESS::ok"],
                "data": {
                    "userIdToSessionView": {
                        "1": {
                            "nameSpaceToSessionView": {
                                "2": {
                                    "sessionViewDTOList": [
                                        {
                                            "sessionId": sid,
                                            "serverTime": _ts(8) * 1000,
                                            "target": {"targetId": "b-1", "userAccountType": 11},
                                            "sessionData": {
                                                "title": "John",
                                                "content": conteudo,
                                                "nonReadNumber": 1,
                                                "senderAccountType": 11,
                                                **dados_extra,
                                            },
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
            }
        ),
    }


def _sync_ae(
    mid: str, sid: str, texto: str | None, *, from_type=11, minutos=9, card=1, **type_data
) -> dict:
    item = {
        "header": {"type": 1},
        "body": {
            "typeData": {
                "mid": mid,
                "sid": sid,
                "from": "b-1" if from_type == 11 else "seller-1",
                "fromType": from_type,
                "sendTime": _ts(minutos) * 1000,
                **type_data,
            },
            "layoutData": {"card": card},
            "templateData": json.dumps(
                {"txt": texto} if card == 1 else {"imgUrl": "https://ae01.alicdn.com/f.jpg"}
            ),
        },
    }
    return {
        "tipo": "http",
        "url": "https://seller-acs.aliexpress.com/h5/mtop.gsp.web.seller.js.sync/1.0/?t=1",
        "corpo": json.dumps(
            {
                "ret": ["SUCCESS::ok"],
                "data": {
                    "syncDataValuesMap": {
                        "im": [{"syncBody": {"sellerUserId": 1}, "bizData": json.dumps(item)}]
                    }
                },
            }
        ),
    }


async def test_previa_do_aliexpress_e_adotada_pela_mensagem_de_verdade(client, db):
    vita = {"perfil": "k1do5vfw", "plataforma": "aliexpress", "loja": "Vita"}
    # A lista de sessões (o robô ligou depois da pergunta): só a prévia.
    r = await _post(client, "eventos", _eventos([_sessao_ae("s-1", "Where is my order?")], **vita))
    assert r.json()["gravadas"] == 1
    [conversa] = await _conversas(db)
    [previa] = await _mensagens(db, conversa.id)
    assert previa.externo_id.startswith("previa:s-1:")
    assert conversa.aguardando_resposta is True
    # A lista de novo: a mesma prévia, nada novo.
    assert (
        await _post(client, "eventos", _eventos([_sessao_ae("s-1", "Where is my order?")], **vita))
    ).json()["gravadas"] == 0
    # A mensagem de verdade chega pelo sync: ADOTA a prévia (uma linha só).
    r = await _post(
        client, "eventos", _eventos([_sync_ae("mid-1", "s-1", "Where is my order?")], **vita)
    )
    assert r.json()["gravadas"] == 0
    [msg] = await _mensagens(db, conversa.id)
    assert msg.externo_id == "mid-1" and msg.autor == "cliente"
    # E a prévia que volta depois, com a mesma frase, não duplica.
    await _post(client, "eventos", _eventos([_sessao_ae("s-1", "Where is my order?")], **vita))
    assert len(await _mensagens(db, conversa.id)) == 1


async def test_previa_com_id_da_ultima_mensagem_e_adotada_pelo_id(client, db):
    """A lista real (Vita, 30/09) traz `lastMessageId`: a mensagem de verdade
    com esse id adota a prévia mesmo com o texto da prévia resumido."""
    vita = {"perfil": "k1do5vfw", "plataforma": "aliexpress", "loja": "Vita"}
    lista = _sessao_ae("s-2", "[Image]", lastMessageId="mid-7")
    assert (await _post(client, "eventos", _eventos([lista], **vita))).json()["gravadas"] == 1
    [conversa] = await _conversas(db)
    real = _sync_ae("mid-7", "s-2", "Foto do defeito")
    r = await _post(client, "eventos", _eventos([real], **vita))
    assert r.json()["gravadas"] == 0
    [msg] = await _mensagens(db, conversa.id)
    assert msg.externo_id == "mid-7" and msg.autor == "cliente"
    # A lista de novo (ainda com o "[Image]"): já está lá pelo id, não duplica.
    await _post(client, "eventos", _eventos([lista], **vita))
    assert len(await _mensagens(db, conversa.id)) == 1


# ── Prévia repetida x mensagem nova com o mesmo texto (revisão 30/09) ─────
#
# A prévia é o que põe na fila a mensagem que chegou com o robô FORA (sessão
# caída, reinício): só a lista a traz. "Hello", "Ok", "[Image]" repetem — o
# texto igual a uma mensagem antiga não pode fazer a mensagem NOVA sumir.
# Três formas de lista: com o id da última mensagem (a real, Vita 30/09), só
# com a hora dela, e sem nenhum dos dois (só o serverTime da resposta).

VITA = {"perfil": "k1do5vfw", "plataforma": "aliexpress", "loja": "Vita"}
FORMAS_DA_LISTA = ("com_id", "so_hora", "so_servidor")


def _lista_ae(
    sid: str,
    conteudo: str,
    *,
    conta=11,
    minutos: float | None = None,
    ultimo_id: str | None = None,
    tags: list | None = None,
    servidor: float = 0.2,
) -> dict:
    """A lista de sessões como a real: `senderAccountType` e `lastMessageTime`
    em TEXTO; `serverTime` é a hora em que a lista foi servida."""
    dados = {
        "title": "John",
        "content": conteudo,
        "nonReadNumber": 1,
        "senderAccountType": str(conta),
    }
    if minutos is not None:
        dados["lastMessageTime"] = str(_ts(minutos) * 1000)
    if ultimo_id:
        dados["lastMessageId"] = ultimo_id
    if tags is not None:
        dados["tags"] = tags
    dto = {
        "sessionId": sid,
        "serverTime": _ts(servidor) * 1000,
        "target": {"targetId": "b-1", "userAccountType": 11},
        "sessionData": dados,
    }
    return {
        "tipo": "http",
        "url": "https://seller-acs.aliexpress.com/h5/"
        "mtop.gsp.im.use.web.seller.messagebox.querysessionlist/1.0/",
        "corpo": json.dumps(
            {
                "api": "mtop.gsp.im.use.web.seller.messagebox.querySessionList",
                "ret": ["SUCCESS::ok"],
                "data": {"x": {"sessionViewDTOList": [dto]}},
            }
        ),
    }


def _hora(forma: str, mid: str, minutos: float) -> dict:
    """O que a lista diz da última mensagem, em cada forma."""
    if forma == "com_id":
        return {"ultimo_id": mid, "minutos": minutos}
    if forma == "so_hora":
        return {"minutos": minutos}
    # Sem id nem hora da mensagem: só a hora em que a lista foi servida, que
    # é logo depois da mensagem.
    return {"servidor": minutos - 0.5}


async def _ae(client, *eventos) -> dict:
    r = await _post(client, "eventos", _eventos(list(eventos), **VITA))
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("forma", FORMAS_DA_LISTA)
async def test_previa_com_o_mesmo_texto_depois_da_resposta_e_mensagem_nova(client, db, forma):
    """A loja respondeu o "Hello"; dias depois, "Hello" de novo: é pergunta NOVA."""
    await _ae(client, _lista_ae("s-9", "Hello", **_hora(forma, "m1", 3 * 1440)))
    await _ae(client, _lista_ae("s-9", "Hi! How can I help?", conta=12, **_hora(forma, "m2", 2880)))
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is False
    novo = _lista_ae("s-9", "Hello", **_hora(forma, "m3", 60))
    assert (await _ae(client, novo))["gravadas"] == 1
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is True and conversa.ultima_autor == "cliente"
    # A lista de novo (servida um pouco depois): a mesma mensagem, nada novo.
    relida = _lista_ae("s-9", "Hello", **{**_hora(forma, "m3", 60), "servidor": 0.1})
    assert (await _ae(client, relida))["gravadas"] == 0
    assert len(await _mensagens(db, conversa.id)) == 3


@pytest.mark.parametrize("forma", FORMAS_DA_LISTA)
async def test_pergunta_repetida_pelo_comprador_entra_na_fila(client, db, forma):
    """O texto igual a uma das últimas mensagens não é, por si, a mesma mensagem."""
    await _ae(
        client,
        _sync_ae("m1", "s-8", "Where is my order?", minutos=3 * 1440),
        _sync_ae("m2", "s-8", "Shipped yesterday", from_type=12, minutos=3 * 1440 - 30),
    )
    r = await _ae(client, _lista_ae("s-8", "Where is my order?", **_hora(forma, "m3", 30)))
    assert r["gravadas"] == 1
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is True


@pytest.mark.parametrize("forma", FORMAS_DA_LISTA)
async def test_foto_nova_depois_da_resposta_entra_na_fila(client, db, forma):
    """Toda foto da sessão chega na lista como "[Image]"."""
    await _ae(client, _lista_ae("s-7", "[Image]", **_hora(forma, "m1", 3 * 1440)))
    await _ae(
        client,
        _sync_ae("m1", "s-7", None, card=3, minutos=3 * 1440),
        _sync_ae("m2", "s-7", "Please send another photo", from_type=12, minutos=2880),
    )
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is False
    await _ae(client, _lista_ae("s-7", "[Image]", **_hora(forma, "m3", 20)))
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is True and conversa.ultima_autor == "cliente"


@pytest.mark.parametrize("forma", FORMAS_DA_LISTA)
async def test_resposta_da_loja_com_o_texto_do_comprador_tira_da_fila(client, db, forma):
    """O comprador disse "Ok" (sync) e a loja respondeu "Ok" (só a lista):
    é outra mensagem, de outro autor — sem ela, a IA sugeriria em dobro."""
    await _ae(client, _sync_ae("m1", "s-6", "Ok", minutos=120))
    r = await _ae(client, _lista_ae("s-6", "Ok", conta=12, **_hora(forma, "m2", 60)))
    assert r["gravadas"] == 1
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is False and conversa.ultima_autor == "loja"


@pytest.mark.parametrize("forma", FORMAS_DA_LISTA)
async def test_lista_atrasada_nao_duplica_a_mensagem_ja_lida(client, db, forma):
    """A lista servida ANTES da resposta da loja (mesma leva do sync) traz a
    pergunta que já está lá: a mesma mensagem, não uma nova."""
    await _ae(
        client,
        _lista_ae("s-5", "Hello", **_hora(forma, "m1", 10)),
        _sync_ae("m1", "s-5", "Hello", minutos=10),
        _sync_ae("m2", "s-5", "Hi", from_type=12, minutos=5),
    )
    [conversa] = await _conversas(db)
    assert len(await _mensagens(db, conversa.id)) == 2
    assert conversa.aguardando_resposta is False


async def test_previa_gravada_com_o_id_antigo_nao_duplica(client, db):
    """A prévia gravada antes de 30/09 tinha o id pelo hash do texto (e o id
    da mensagem no payload): relida no formato novo, é a mesma."""
    lista = _lista_ae("s-2", "[Image]", minutos=60, ultimo_id="mid-7")
    await _ae(client, lista)
    [conversa] = await _conversas(db)
    [previa] = await _mensagens(db, conversa.id)
    previa.externo_id = "previa:s-2:0123456789abcdef"
    await db.commit()
    assert (await _ae(client, lista))["gravadas"] == 0
    assert len(await _mensagens(db, conversa.id)) == 1


# ── Resposta automática da plataforma lida só pela lista ──────────────────


async def test_temu_lista_com_resposta_do_robo_da_temu_fica_na_fila(client, db):
    """A 1ª leitura (ou a volta do robô) só vê a ÚLTIMA mensagem: a do robô
    da Temu. A Temu diz "sem resposta" (unReply): a pergunta que o robô não
    viu entra como marcador do comprador, e a conversa fica na fila."""
    robo_da_temu = _msg_temu(
        "m-9",
        "conv-9",
        de=2,
        para=1,
        texto="Olá! Sou o assistente.",
        minutos=30,
        context={"robot": 1},
    )
    lista = _evento_http(
        "/api/plateau/conv/getConvList",
        {
            "data": [
                {
                    "convId": "conv-9",
                    "convInfo": {"name": "Maria", "unreadCount": 1},
                    "message": robo_da_temu,
                    "stateData": {"groupList": ["unReply", "robotReplied"]},
                    "countDownInfo": {"lastUnReplyTime": _ts(31)},
                }
            ]
        },
    )
    await _post(client, "eventos", _eventos([lista]))
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is True and conversa.situacao == "aberta"
    msgs = await _mensagens(db, conversa.id)
    assert [m.autor for m in msgs] == ["cliente", "sistema"]
    assert "Seller Center" in msgs[0].texto and msgs[0].payload.get("nao_vista") is True
    # A lista de novo: o marcador não duplica.
    await _post(client, "eventos", _eventos([lista]))
    assert len(await _mensagens(db, conversa.id)) == 2
    # A pergunta de verdade chega (sync): ADOTA o marcador — uma linha só.
    await _post(
        client, "eventos", _eventos([_sync(_msg_temu("m-8", "conv-9", de=1, para=2, minutos=31))])
    )
    msgs = await _mensagens(db, conversa.id)
    assert [(m.externo_id, m.autor) for m in msgs] == [("m-8", "cliente"), ("m-9", "sistema")]
    assert msgs[0].texto == TEXTO
    await _post(client, "eventos", _eventos([lista]))
    assert len(await _mensagens(db, conversa.id)) == 2
    # A loja responde no Seller Center: sai da fila.
    await _post(
        client, "eventos", _eventos([_sync(_msg_temu("m-10", "conv-9", de=2, para=1, minutos=5))])
    )
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is False


async def test_aliexpress_previa_da_ia_da_plataforma_nao_e_resposta_da_equipe(client, db):
    """A IA do AliExpress responde com a conta do vendedor (senderAccountType
    12): na lista, sem o `fromCode`, só as tags dizem que não foi a equipe."""
    await _ae(client, _sync_ae("m1", "s-3", "Is it original?", minutos=90))
    [conversa] = await _conversas(db)
    [gatilho] = await _mensagens(db, conversa.id)
    rascunho = AtendimentoRascunho(
        conversa_id=conversa.id, mensagem_gatilho_id=gatilho.id, texto="Sim.", status="pendente"
    )
    db.add(rascunho)
    await db.commit()
    await _ae(
        client,
        _lista_ae(
            "s-3",
            "Hello, I am the AI assistant",
            conta=12,
            minutos=89,
            ultimo_id="m2",
            tags=["un-replied", "ai-serving"],
        ),
    )
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is True
    msgs = await _mensagens(db, conversa.id)
    assert [m.autor for m in msgs] == ["cliente", "sistema"]  # nada de marcador: m1 já está lá
    await db.refresh(rascunho)
    assert rascunho.status == "pendente"
    # Sessão que o robô só conhece pela lista, com a IA por último e "un-replied":
    # a pergunta que ninguém viu entra como marcador.
    await _ae(
        client,
        _lista_ae(
            "s-4",
            "Hello, I am the AI assistant",
            conta=12,
            minutos=30,
            ultimo_id="m7",
            tags=["un-replied", "ai-unresolved"],
        ),
    )
    nova = next(c for c in await _conversas(db) if c.externo_id == "s-4")
    assert nova.aguardando_resposta is True
    assert [m.autor for m in await _mensagens(db, nova.id)] == ["cliente", "sistema"]


# ── Mensagem envenenada e erro de formato (revisão 30/09) ─────────────────


async def test_adocao_da_previa_tira_o_nul_como_a_porta_unica(client, db):
    """A adoção grava pela mesma limpeza do `gravar_mensagem`: um NUL do
    comprador (ou da plataforma) no payload não derruba a leva."""
    await _ae(client, _lista_ae("s-5", "[Image]", minutos=60, ultimo_id="m1"))
    real = _sync_ae("m1", "s-5", "foto", minutos=60, summary="foto\x00")
    for _ in range(2):
        r = await _post(client, "eventos", _eventos([real], **VITA))
        assert r.status_code == 200, r.text
        assert r.json()["erros"] == 0
    [conversa] = await _conversas(db)
    [msg] = await _mensagens(db, conversa.id)
    assert msg.externo_id == "m1" and msg.autor == "cliente"
    assert "\\u0000" not in json.dumps(msg.payload)
    assert msg.payload["previa_adotada"].startswith("previa:s-5:")


async def test_erro_de_formato_nao_trava_e_erro_do_banco_nao_vaza(client, db, monkeypatch):
    """Erro de DADO numa conversa (DataError/IntegrityError) é do formato: vai
    para `erros` (200) e o robô segue. Só o erro de conexão/transação vira 500
    — e o 500 não leva detalhe nem põe o SQL (com texto de comprador) no log."""
    from sqlalchemy.exc import DataError, OperationalError

    marca = "TEXTO_DO_COMPRADOR_NO_SQL"
    erro: list[Exception] = [DataError("INSERT ...", {"texto": marca}, Exception(marca))]

    async def _falha(*_a, **_k):
        raise erro[0]

    monkeypatch.setattr(robo, "_gravar_conversa", _falha)
    r = await _post(client, "eventos", _eventos([_conv_list()]))
    assert r.status_code == 200, r.text
    assert (r.json()["conversas"], r.json()["erros"]) == (0, 1)

    erro[0] = OperationalError("UPDATE ...", {"texto": marca}, Exception(marca))
    with structlog.testing.capture_logs() as logs:
        r = await _post(client, "eventos", _eventos([_conv_list()]))
    assert r.status_code == 500
    assert marca not in r.text
    assert r.json()["detail"]["code"] == "robo_erro_interno"
    assert marca not in json.dumps(logs, default=str)
    assert any(log.get("erro") == "OperationalError" for log in logs)


# ── Envio, modo e o sync da API ───────────────────────────────────────────


async def test_nada_sai_pelo_davinci_e_o_modo_so_observa_ou_copila(client, db, pessoa):
    await _post(client, "eventos", _eventos([_conv_list()]))
    [conversa] = await _conversas(db)
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa is not None and recusa.code == enviar.RECUSA_SOMENTE_LEITURA
    assert "Seller Center" in str(recusa.detail)

    detalhe = await client.get(f"/api/atendimento/conversas/{conversa.id}")
    assert detalhe.status_code == 200, detalhe.text
    envio = detalhe.json()["envio"]
    assert envio["pode_enviar"] is False and envio["modo_observacao"] is True
    assert envio["limite_caracteres"] == 2000

    canal = await _canal(db)
    for modo in ("humano", "auto"):
        r = await client.patch(f"/api/atendimento/canais/{canal.id}", json={"modo": modo})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "modo_invalido_robo", modo
    r = await client.patch(f"/api/atendimento/canais/{canal.id}", json={"modo": "copiloto"})
    assert r.status_code == 200, r.text
    assert r.json()["modo"] == "copiloto" and r.json()["conta"] == "Barbosa"


async def test_sync_da_api_nunca_le_a_loja_do_robo(client, db, monkeypatch):
    await _post(client, "pulso", _pulso())
    canal = await _canal(db)

    async def _proibido(*_a, **_k):
        raise AssertionError("o sync tentou montar um cliente de API para a loja do robô")

    monkeypatch.setattr(clientes, "cliente_da_integracao", _proibido)
    assert await sync.garantir_canais(db) == 0
    await db.commit()
    assert canal.id not in await sync._canais_da_rodada(db)
    resultado = await sync.sincronizar_canal(canal.id)
    assert resultado.status == "desligado"
    await db.refresh(canal)
    assert canal.status == "ok"  # a saúde da loja do robô é dele, não do sync


async def test_ia_do_cron_sugere_para_a_loja_do_robo_em_copiloto(client, db, monkeypatch):
    await _post(client, "eventos", _eventos([_sync(_msg_temu("m-1", "conv-1", de=1, para=2))]))
    canal = await _canal(db)
    chamadas: list = []

    async def gerar_rascunho(session, conversa, **_k):
        chamadas.append(conversa.externo_id)
        return None

    monkeypatch.setattr(ia, "gerar_rascunho", gerar_rascunho)
    ia.esquecer_limites()
    assert await ia._gerar_pendentes(db, limite=10, modos=("copiloto",)) == 0
    assert chamadas == []  # em observar, o cron não gasta token
    canal.modo = "copiloto"
    await db.commit()
    await ia._gerar_pendentes(db, limite=10, modos=("copiloto",))
    assert chamadas == ["conv-1"]


def test_status_efetivo_sem_sinal_e_com_evento_recente():
    canal = AtendimentoCanal(
        robo_perfil_id="k1", plataforma="temu", canal="chat", status="ok", cursor={"robo": {}}
    )
    status, motivo = robo.status_efetivo(canal)
    assert status == "parado" and "ainda não deu sinal" in motivo
    # Um evento recente também prova que o robô vive (mesmo com o pulso velho).
    canal.cursor = {
        "robo": {
            "ultimo_pulso_em": (AGORA - timedelta(hours=3)).isoformat(),
            "ultimo_evento_em": (AGORA - timedelta(minutes=1)).isoformat(),
        }
    }
    assert robo.status_efetivo(canal)[0] == "ok"
