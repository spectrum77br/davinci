"""Atendimentos do Melhor Envio na aba Chamados (13/11/2026, passo 2: só leitura).

Caso real: AD952521063BR (Bling 298756), "Falha na entrega" — nós abrimos no site do
ME em 19/10, a atendente respondeu, a conversa seguiu. O formato dos JSONs abaixo é
o que a sonda de 13/11 leu com o token da Conciliação.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem
from app.routers.chamados import _trabalho_do_cerebro
from app.services import chamados as chamados_svc
from app.services import chamados_melhor_envio as cme
from app.services import melhor_envio as me

ATIVO = 2065437
RESOLVIDO = 2011111


def _envio(numero: int, status: str, *, pedido: str, rastreio: str) -> dict:
    return {
        "protocol": f"ORD-{numero}",
        "tracking": rastreio,
        "status": "undelivered",
        "tags": [
            {"tag": "mi:reference_code", "url": pedido},
            {"tag": "mi:marketplace_code", "url": "701-7654805-6401806"},
        ],
        "tickets": [{"number": numero, "status": status, "summary": "Falha na entrega"}],
    }


def _comentario(texto: str, quando: str, *, nossa: bool, anexos: int = 0) -> dict:
    return {
        "description": texto,
        "description_html": f"<html><body>{texto.replace(chr(10), '<br/>')}</body></html>",
        "type_user": "Cliente" if nossa else "Agente",
        "date_creation_raw": quando,
        "person": {"name": "CASA E DECORAÇÃO" if nossa else "Natalia A S - Melhor Envio"},
        "attachments": [{"url": "https://x"}] * anexos,
    }


CONVERSA = [
    _comentario(
        "meu problema ainda nao foi resolvido\nNAO FOI ENTREGUE",
        "2026-10-19T12:20:43.517Z",
        nossa=True,
    ),
    _comentario(
        "Acionamos os Correios e reativamos a PI.", "2026-10-19T13:52:41.000Z", nossa=False
    ),
    _comentario("ok aguardando", "2026-11-11T12:15:02.564Z", nossa=True, anexos=1),
]


@pytest.fixture
def fake_me(monkeypatch):
    """O ME de mentira: lista da aba Atendimentos + conversa de cada atendimento."""
    estado = {
        "lista": [
            _envio(ATIVO, "Em andamento", pedido="298756", rastreio="AD952521063BR"),
            _envio(RESOLVIDO, "Resolvido", pedido="298931", rastreio="AD954534590BR"),
        ],
        "tickets": {
            str(ATIVO): {
                "number": ATIVO,
                "status": "Em andamento",
                "order_id": "a2d06455-ord",
                "interactions": {"comments": list(CONVERSA)},
            },
        },
        "lidos": [],
        "enviados": [],
        "envio_http": 200,
    }

    async def _token(session):
        return "tok", {}

    async def _envios(token, *, page=1, q=None, extra=None):
        assert extra == me.PARAMS_ATENDIMENTOS
        return 200, {"data": estado["lista"], "last_page": 1}

    async def _ticket(token, numero):
        estado["lidos"].append(numero)
        t = estado["tickets"].get(numero)
        return (200, t) if t else (404, {"message": "não achado"})

    async def _enviar(token, numero, *, order_id, texto, anexos=None):
        estado["enviados"].append(
            {"numero": numero, "order_id": order_id, "texto": texto, "anexos": anexos or []}
        )
        if estado["envio_http"] != 200:
            return estado["envio_http"], {"message": "This action is unauthorized."}
        # o ME guarda a nossa fala na conversa (volta na leitura seguinte)
        estado["tickets"][numero]["interactions"]["comments"].append(
            _comentario(texto, "2026-11-13T12:30:00.000Z", nossa=True, anexos=len(anexos or []))
        )
        return 200, {"ticket": estado["tickets"][numero]}

    monkeypatch.setattr(me, "token_do_painel", _token)
    monkeypatch.setattr(me, "enviar_no_atendimento", _enviar)
    monkeypatch.setattr(me, "ler_envios", _envios)
    monkeypatch.setattr(me, "ler_atendimento", _ticket)
    return estado


async def _chamados_me(db) -> list[Chamado]:
    return list((await db.execute(select(Chamado).where(Chamado.origem == cme.ORIGEM))).scalars())


async def _falas(db, ch: Chamado) -> list[ChamadoMensagem]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo != "sistema")
                .order_by(ChamadoMensagem.created_at)
            )
        ).scalars()
    )


def test_fala_do_comentario_nossa_e_do_atendente():
    nossa = cme.fala_do_comentario(CONVERSA[0])
    assert nossa.direcao == "enviada"
    assert nossa.texto == "meu problema ainda nao foi resolvido\nNAO FOI ENTREGUE"
    assert nossa.autor == "CASA E DECORAÇÃO · no site do Melhor Envio"
    assert nossa.quando == datetime(2026, 10, 19, 12, 20, 43, 517000, tzinfo=UTC)
    deles = cme.fala_do_comentario(CONVERSA[1])
    assert (deles.direcao, deles.autor) == ("recebida", "Natalia A S - Melhor Envio")
    # sem a assinatura do ME, o nome ganha o "· Melhor Envio"
    sem = cme.fala_do_comentario({**CONVERSA[1], "person": {"name": "Alessandra T"}})
    assert sem.autor == "Alessandra T · Melhor Envio"
    assert cme.fala_do_comentario(CONVERSA[2]).texto == "ok aguardando\n[1 anexo no Melhor Envio]"


def test_status_ativo_e_final():
    assert cme.ticket_ativo("Em andamento")
    assert not cme.ticket_ativo("Resolvido")
    assert not cme.ticket_ativo("RESOLVIDO")
    assert not cme.ticket_ativo("Concluído")
    assert not cme.ticket_ativo("")


async def test_so_o_atendimento_ativo_vira_linha_com_a_conversa(db, fake_me):
    r = await cme.sincronizar(db)
    await db.commit()
    assert (r["me_atendimentos"], r["me_novos"], r["me_falas"]) == (2, 1, 3)

    [ch] = await _chamados_me(db)
    assert ch.pedido_bling == "298756"
    assert ch.plataforma == "melhor envio"
    assert ch.canal == "manual"
    assert ch.chamado == str(ATIVO)
    assert ch.chamado_url == cme.URL_ATENDIMENTOS
    assert ch.data == date(2026, 10, 19)  # dia da abertura no ME, não o da leitura
    falas = await _falas(db, ch)
    assert [(m.direcao, m.tipo) for m in falas] == [
        ("enviada", "abertura"),
        ("recebida", "resposta"),
        ("enviada", "replica"),
    ]
    assert falas[1].autor_nome == "Natalia A S - Melhor Envio"
    # o resolvido nunca entrou: nem é lido
    assert fake_me["lidos"] == [str(ATIVO)]


async def test_segunda_passada_nao_duplica_e_traz_so_a_fala_nova(db, fake_me):
    await cme.sincronizar(db)
    await db.commit()
    fake_me["tickets"][str(ATIVO)]["interactions"]["comments"].append(
        _comentario("Ouvidoria aberta nos Correios.", "2026-11-13T10:00:00.000Z", nossa=False)
    )
    r = await cme.sincronizar(db)
    await db.commit()
    assert (r["me_novos"], r["me_falas"]) == (0, 1)
    [ch] = await _chamados_me(db)
    assert len(await _falas(db, ch)) == 4


async def test_resolvido_no_me_vai_pra_encerrado_sem_concluir(db, fake_me):
    await cme.sincronizar(db)
    await db.commit()
    fake_me["tickets"][str(ATIVO)]["status"] = "Resolvido"
    r = await cme.sincronizar(db)
    await db.commit()
    assert r["me_encerrados"] == 1
    [ch] = await _chamados_me(db)
    assert ch.status_plataforma == chamados_svc.STATUS_ENCERRADO
    assert ch.resolvido is False  # quem conclui é gente
    eventos = (
        (
            await db.execute(
                select(ChamadoMensagem.texto).where(
                    ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "sistema"
                )
            )
        )
        .scalars()
        .all()
    )
    assert any("Melhor Envio encerrou o atendimento (Resolvido)" in t for t in eventos)
    # passada seguinte não repete o evento
    await cme.sincronizar(db)
    await db.commit()
    eventos2 = (
        (
            await db.execute(
                select(ChamadoMensagem.id).where(
                    ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "sistema"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(eventos2) == len(eventos)


async def test_sem_token_nao_faz_nada(db, monkeypatch):
    async def _sem(session):
        return None, {}

    monkeypatch.setattr(me, "token_do_painel", _sem)
    r = await cme.sincronizar(db)
    assert r["me_sem_token"] == 1
    assert await _chamados_me(db) == []


async def test_ia_de_chamado_nao_pega_atendimento_do_melhor_envio(db, fake_me):
    await cme.sincronizar(db)
    await db.commit()
    [ch] = await _chamados_me(db)
    rows = (await db.execute(_trabalho_do_cerebro(None, ["robo", "api", "manual"]))).scalars()
    assert ch.id not in {c.id for c in rows}


async def _replicar(client, ch: Chamado, texto: str, *, foto: bool = False):
    files = {"files": ("foto.png", b"\x89PNG\r\n\x1a\nfake", "image/png")} if foto else None
    return await client.post(f"/api/chamados/{ch.id}/mensagens", data={"texto": texto}, files=files)


async def test_resposta_pela_aba_vai_pro_melhor_envio_e_nao_duplica(
    client, make_user, auth_as, db, fake_me
):
    auth_as(await make_user(permissions={"chamados": {"view": True, "edit": True}}))
    await cme.sincronizar(db)
    await db.commit()
    [ch] = await _chamados_me(db)

    r = await _replicar(client, ch, "Bom dia, alguma novidade?", foto=True)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "enviada"
    [env] = fake_me["enviados"]
    assert (env["numero"], env["order_id"], env["texto"]) == (
        str(ATIVO),
        "a2d06455-ord",
        "Bom dia, alguma novidade?",
    )
    assert [(nome, ctype) for nome, _blob, ctype in env["anexos"]] == [("foto.png", "image/png")]

    # a leitura seguinte traz a nossa fala de volta (com o aviso do anexo): não duplica
    r2 = await cme.sincronizar(db)
    await db.commit()
    assert r2["me_falas"] == 0
    nossas = [m for m in await _falas(db, ch) if "alguma novidade" in m.texto]
    assert len(nossas) == 1
    assert nossas[0].canal == "api"


async def test_resposta_recusada_pelo_me_fica_falhou(client, make_user, auth_as, db, fake_me):
    auth_as(await make_user(permissions={"chamados": {"view": True, "edit": True}}))
    await cme.sincronizar(db)
    await db.commit()
    [ch] = await _chamados_me(db)
    fake_me["envio_http"] = 403

    r = await _replicar(client, ch, "alguma novidade?")
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["status"] == "falhou"
    assert m["erro"].startswith("melhor_envio_403: This action is unauthorized")


async def test_atendimento_resolvido_nao_recebe_resposta(client, make_user, auth_as, db, fake_me):
    auth_as(await make_user(permissions={"chamados": {"view": True, "edit": True}}))
    await cme.sincronizar(db)
    await db.commit()
    [ch] = await _chamados_me(db)
    fake_me["tickets"][str(ATIVO)]["status"] = "Resolvido"

    r = await _replicar(client, ch, "alguma novidade?")
    assert r.json()["status"] == "falhou"
    assert r.json()["erro"] == cme.ERRO_ENCERRADO
    assert fake_me["enviados"] == []


async def test_nome_repetido_gravado_antes_se_ajeita_na_leitura(db, fake_me):
    """13/11 em produção: a 1ª versão gravou "Natalia A S - Melhor Envio · Melhor
    Envio". A leitura seguinte acerta o nome sem duplicar a fala."""
    await cme.sincronizar(db)
    await db.commit()
    [ch] = await _chamados_me(db)
    falas = await _falas(db, ch)
    falas[1].autor_nome = "Natalia A S - Melhor Envio · Melhor Envio"
    await db.commit()
    r = await cme.sincronizar(db)
    await db.commit()
    assert r["me_falas"] == 0
    falas = await _falas(db, ch)
    assert len(falas) == 3
    assert falas[1].autor_nome == "Natalia A S - Melhor Envio"
