"""Histórico › Envios Threema (tarefa 16, C2).

`GET /api/historico/threema` lê o registro de cada envio do Threema
(`threema_envio_registro`) agrupado por lote (uma chamada de envio). Mesma
porta do resto do Histórico: quem não foi liberado recebe o 404 de rota que
não existe — e a rota NÃO pode ser engolida pelo `/{evento_id}`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import get_settings
from app.models import (
    HistoricoAcesso,
    HistoricoAlteracao,
    HistoricoEvento,
    ThreemaEnvioRegistro,
    UserRole,
)
from app.security.jwt import issue_session_token
from app.services.threema_catalogo import CATALOGO, K


def _logar(client, user) -> None:
    token, _, _ = issue_session_token(sub=user.open_id, role=user.role.value)
    client.cookies.set(get_settings().cookie_name, token)


def _linha(lote, quando, **kw) -> ThreemaEnvioRegistro:
    base = {
        "lote_id": lote,
        "criado_em": quando,
        "origem": "automatico",
        "contexto": "logistica",
        "canal": "logistica",
        "ok": True,
        "http_status": 200,
    }
    base.update(kw)
    return ThreemaEnvioRegistro(**base)


async def _semear(db) -> dict[str, uuid.UUID]:
    agora = datetime.now(UTC)
    lotes = {n: uuid.uuid4() for n in ("vigia", "informar", "config", "velho", "sem_marca")}
    db.add_all(
        [
            # lote com 2 pessoas, uma falhou (402)
            _linha(
                lotes["vigia"],
                agora - timedelta(minutes=5),
                envio_chave=K.LOGISTICA_VIGIA_AUTOMACAO,
                titulo="Logística — automação parada:",
                destinatario_id="TSTA0001",
                destinatario_nome="Londres Teste",
                message_id="mid-1",
            ),
            _linha(
                lotes["vigia"],
                agora - timedelta(minutes=5),
                envio_chave=K.LOGISTICA_VIGIA_AUTOMACAO,
                titulo="Logística — automação parada:",
                destinatario_id="TSTB0002",
                destinatario_nome="Heisenberg Teste",
                ok=False,
                http_status=402,
                erro="http_402",
            ),
            # envio manual de uma pessoa
            _linha(
                lotes["informar"],
                agora - timedelta(minutes=1),
                envio_chave=K.LOGISTICA_INFORMAR,
                origem="manual",
                ator_nome="Fulano Admin",
                titulo="DaVinci — Logística (3)",
                destinatario_id="TSTA0001",
                destinatario_nome="Londres Teste",
            ),
            # erro de configuração: 1 linha sem destinatário
            _linha(
                lotes["config"],
                agora - timedelta(minutes=10),
                envio_chave=K.DEVOLUCOES_PRAZO_CONTESTACAO,
                contexto="devolucoes",
                ok=False,
                http_status=None,
                erro="threema_gateway_id_missing",
                titulo="Prazo de contestação",
            ),
            # fora do período de 7 dias
            _linha(
                lotes["velho"],
                agora - timedelta(days=20),
                envio_chave=K.LOGISTICA_LOJA_MUDA,
                destinatario_id="TSTA0001",
                titulo="antigo",
            ),
            # sem marca
            _linha(
                lotes["sem_marca"],
                agora - timedelta(minutes=20),
                envio_chave=None,
                destinatario_id="TSTC0003",
                titulo="ponto novo",
            ),
        ]
    )
    await db.commit()
    return lotes


@pytest.mark.asyncio
async def test_sem_acesso_e_404_igual_rota_inexistente(client, db, make_user):
    admin = await make_user(role=UserRole.ADMIN)
    _logar(client, admin)
    inexistente = await client.get("/api/nao-existe-mesmo")
    r = await client.get("/api/historico/threema")
    assert r.status_code == 404
    assert r.json() == inexistente.json()


@pytest.mark.asyncio
async def test_lista_por_lote_mais_novo_primeiro(client, db, make_user):
    eu = await make_user(role=UserRole.ADMIN)
    db.add(HistoricoAcesso(user_id=eu.id, pode_gerenciar=True))
    await db.commit()
    lotes = await _semear(db)
    _logar(client, eu)

    r = await client.get("/api/historico/threema")
    # se o `/{evento_id}` engolisse "threema", seria 404
    assert r.status_code == 200, r.text
    dados = r.json()
    assert dados["total"] == 4
    assert [i["lote_id"] for i in dados["items"]] == [
        str(lotes["informar"]),
        str(lotes["vigia"]),
        str(lotes["config"]),
        str(lotes["sem_marca"]),
    ]
    informar, vigia, config, sem_marca = dados["items"]

    assert informar["envio"] == {
        "chave": K.LOGISTICA_INFORMAR,
        "nome": CATALOGO[K.LOGISTICA_INFORMAR].nome,
        "painel": "Logística",
    }
    assert (informar["origem"], informar["ator"]) == ("manual", "Fulano Admin")

    assert (vigia["total"], vigia["entregues"], vigia["falhas"]) == (2, 1, 1)
    assert vigia["titulo"] == "Logística — automação parada:"
    assert vigia["destinatarios"] == [
        {
            "nome": "Londres Teste",
            "id": "TS••••••",
            "ok": True,
            "http_status": 200,
            "erro": None,
            "quando": vigia["destinatarios"][0]["quando"],
        },
        {
            "nome": "Heisenberg Teste",
            "id": "TS••••••",
            "ok": False,
            "http_status": 402,
            "erro": "http_402",
            "quando": vigia["destinatarios"][1]["quando"],
        },
    ]
    # o ID inteiro nunca sai
    assert "TSTA0001" not in r.text and "TSTB0002" not in r.text

    assert (config["total"], config["falhas"], config["destinatarios"]) == (0, 1, [])
    assert config["erro_geral"] == "threema_gateway_id_missing"

    assert sem_marca["envio"]["chave"] is None
    assert sem_marca["envio"]["nome"].startswith("Sem marca")

    # filtro de envios: só o que aparece no período, com o nome do catálogo
    chaves = {e["chave"] for e in dados["envios"]}
    assert chaves == {
        K.LOGISTICA_INFORMAR,
        K.LOGISTICA_VIGIA_AUTOMACAO,
        K.DEVOLUCOES_PRAZO_CONTESTACAO,
        "sem_chave",
    }


@pytest.mark.asyncio
async def test_filtros(client, db, make_user):
    eu = await make_user(role=UserRole.ADMIN)
    db.add(HistoricoAcesso(user_id=eu.id, pode_gerenciar=True))
    await db.commit()
    lotes = await _semear(db)
    _logar(client, eu)

    async def ids(**params) -> list[str]:
        r = await client.get("/api/historico/threema", params=params)
        assert r.status_code == 200, r.text
        return [i["lote_id"] for i in r.json()["items"]]

    assert await ids(ok="false") == [str(lotes["vigia"]), str(lotes["config"])]
    assert await ids(ok="true") == [str(lotes["informar"]), str(lotes["sem_marca"])]
    assert await ids(chave=K.LOGISTICA_VIGIA_AUTOMACAO) == [str(lotes["vigia"])]
    assert await ids(chave="sem_chave") == [str(lotes["sem_marca"])]
    assert await ids(origem="manual") == [str(lotes["informar"])]
    # a busca acha o lote e traz TODOS os destinatários dele
    r = await client.get("/api/historico/threema", params={"busca": "heisenberg"})
    (item,) = r.json()["items"]
    assert item["lote_id"] == str(lotes["vigia"]) and item["total"] == 2
    # % digitado é texto, não curinga
    assert await ids(busca="%") == []
    # 30 dias pega o antigo
    assert str(lotes["velho"]) in await ids(dias=30)
    # paginação
    r = await client.get("/api/historico/threema", params={"limit": 1, "offset": 1})
    assert r.json()["total"] == 4
    assert [i["lote_id"] for i in r.json()["items"]] == [str(lotes["vigia"])]


@pytest.mark.asyncio
async def test_alteracoes_nunca_mostram_o_id_inteiro(client, db, make_user):
    """Histórico › Alterações: o gatilho guarda a linha como ela é (o contato
    externo com o ID, o Threema do usuário, a lista do Informar) e o corpo do
    pedido guarda o que foi digitado. Quem lê vê só o começo do ID — também
    quem não é admin (o teste no navegador de 19/11 achou o ID inteiro no
    "cadastrou um contato externo do Threema")."""
    eu = await make_user(role=UserRole.USER)
    db.add(HistoricoAcesso(user_id=eu.id, pode_gerenciar=False))
    req = uuid.uuid4()
    db.add(
        HistoricoEvento(
            req_id=req,
            ator_id=eu.id,
            ator_nome="Fulano",
            metodo="POST",
            rota="/api/threema/contatos",
            caminho="/api/threema/contatos",
            tela="Cadastros › Envios Threema",
            acao="cadastrou um contato externo do Threema",
            status=201,
            corpo={"nome": "Lisboa", "threema_id": "TSTK0011", "observacao": "SKU DG053ABC"},
            n_alteracoes=3,
            itens_texto="Lisboa",
        )
    )
    db.add_all(
        [
            HistoricoAlteracao(
                req_id=req,
                ator_id=eu.id,
                tabela="threema_contato",
                operacao="I",
                registro_id=str(uuid.uuid4()),
                rotulo="Lisboa",
                depois={"nome": "Lisboa", "threema_id": "TSTK0011", "ativo": True},
            ),
            HistoricoAlteracao(
                req_id=req,
                ator_id=eu.id,
                tabela="threema_informar_config",
                operacao="U",
                registro_id=str(uuid.uuid4()),
                antes={"recipients": "TSTR0001:Roma"},
                depois={"recipients": "TSTK0011:Lisboa,TSTR0001:Roma"},
            ),
            HistoricoAlteracao(
                req_id=req,
                ator_id=eu.id,
                tabela="users",
                operacao="U",
                registro_id=str(eu.id),
                rotulo="Fulano",
                # mesmo começo: não pode parecer que nada mudou
                antes={"threema": "TSTA0001"},
                depois={"threema": "TSTB0002"},
            ),
        ]
    )
    await db.commit()
    _logar(client, eu)

    lista = await client.get("/api/historico")
    assert lista.status_code == 200, lista.text
    (evento,) = [e for e in lista.json()["items"] if e["acao"].startswith("cadastrou um contato")]
    r = await client.get(f"/api/historico/{evento['id']}")
    assert r.status_code == 200, r.text
    for texto in (lista.text, r.text):
        for rid in ("TSTK0011", "TSTR0001", "TSTA0001", "TSTB0002"):
            assert rid not in texto, f"{rid} inteiro no Histórico"
    d = r.json()
    assert d["corpo"]["threema_id"] == "TS••••••"
    assert d["corpo"]["observacao"] == "SKU DG053ABC", "fora das chaves do Threema, nada muda"
    campos = {(a["tabela"], c["campo"]): c for a in d["alteracoes"] for c in a["campos"]}
    assert campos[("threema_contato", "threema_id")]["depois"] == "TS••••••"
    assert campos[("threema_contato", "nome")]["depois"] == "Lisboa"
    rec = campos[("threema_informar_config", "recipients")]
    assert (rec["antes"], rec["depois"]) == ("TS••••••:Roma", "TS••••••:Lisboa,TS••••••:Roma")
    thr = campos[("users", "threema")]
    assert (thr["antes"], thr["depois"]) == ("TS••••••", "TS•••••• (outro ID)")
