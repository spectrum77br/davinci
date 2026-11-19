"""Chamado da devolução na janela do painel (2ª etapa, Vinicius 16/11/2026).

- "Abrir chamado: Sim ou Não": Não = nada vai pra plataforma (nem o disparo
  que já estava na fila); Sim depois de um Não = a contestação sai.
- Conferência da IA antes de a disputa sair: "se não bate, segurar e
  perguntar" — a abertura fica `segurada` e a pessoa decide (mandar como está,
  trocar o motivo, não abrir). IA sem resposta = sai como sempre saiu.
- "Instrução IA": vai pro chamado como instrução; sem chamado ainda, espera e
  entra quando ele nascer.
- Vídeo da abertura no texto da disputa (antes da Observação; fora do "Não
  recebido").
- Histórico antigo como resolvido ("feito antes de mudar o painel").
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.models import BlingOrder, Chamado, ChamadoMensagem, DevolucaoRastreio, Devolution
from app.routers.devolutions import PAINEL_LARGADA_HISTORICO
from app.services import chamados_devolucao as cd
from app.services import devolucao_conferencia as conferencia
from tests.test_chamados_devolucao import PNG_1PX as _PNG
from tests.test_chamados_devolucao import _abertura, _FakeML, _perms, _seed_pedido

pytestmark = pytest.mark.asyncio

MEGA = "https://mega.nz/file/expedicao#K3yDoVideoNaMega0123456789abcdefghij"
ABERTURA = "https://mega.nz/file/abertura#K3yDoVideoNaMega0123456789abcdefghij"


@pytest.fixture
def ml(monkeypatch):
    fake = _FakeML()

    async def _client(session, conta):
        return fake

    monkeypatch.setattr(cd.chamados_svc, "_ml_client_para", _client)
    monkeypatch.setattr(cd, "ENFILEIRAR", False)  # dispara inline (sem Redis)
    return fake


@pytest.fixture
def ia(monkeypatch):
    """A IA da conferência, de mentira: `respostas` é a fila do que ela responde
    (Veredito ou None = sem resposta); `perguntas` guarda o que ela leu."""

    class _IA:
        def __init__(self) -> None:
            self.respostas: list[conferencia.Veredito | None] = []
            self.perguntas: list[str] = []

        async def perguntar(self, texto: str):
            self.perguntas.append(texto)
            v = self.respostas.pop(0) if self.respostas else conferencia.Veredito(bate=True)
            return v, "claude-opus-5-5"

    fake = _IA()
    monkeypatch.setattr(conferencia, "ligada", lambda: True)
    monkeypatch.setattr(conferencia, "perguntar", fake.perguntar)
    return fake


def _lancamento(numero: str, numeroloja: str, **extra) -> dict:
    return {
        "conta": "aguiar",
        "pedido_bling": numero,
        "pedido_marketplace": numeroloja,
        "sku": "b001.26",
        "produtos": "Mala Listrada tamanho 26",
        **extra,
    }


async def _chamado(db, numero: str) -> Chamado:
    return (await db.execute(select(Chamado).where(Chamado.pedido_bling == numero))).scalar_one()


async def _eventos(db, ch: Chamado) -> list[str]:
    return [
        m.texto
        for m in (
            await db.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == ch.id)
                .order_by(ChamadoMensagem.created_at)
            )
        ).scalars()
    ]


# ── Abrir chamado: Sim / Não ──────────────────────────────────────────────


async def test_nao_antes_de_lancar_nao_abre_chamado(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880001", numeroloja="3880001")
    r = await client.post("/api/devolucoes-painel/880001/abrir-chamado", json={"abrir": False})
    assert r.status_code == 200, r.text
    assert r.json()["abrir_chamado"] is False and r.json()["chamado_id"] is None

    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880001",
            "3880001",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    assert r.status_code == 201, r.text
    assert (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == "880001"))
    ).first() is None
    assert ml.reviews == []

    # Sim de novo: fica marcado (a tela mostra "marcado à mão") e a contestação sai.
    r = await client.post("/api/devolucoes-painel/880001/abrir-chamado", json={"abrir": True})
    assert r.status_code == 200, r.text
    janela = r.json()
    assert janela["abrir_chamado"] is True and janela["abertura_status"] == "enviada"
    assert len(ml.reviews) == 1


async def test_nao_com_contestacao_pendente_cancela_e_o_disparo_da_fila_nao_manda(
    client, make_user, auth_as, db, ml
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880002", numeroloja="3880002")
    ml.acao = False  # o ML ainda não liberou: a abertura fica pendente na fila
    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880002",
            "3880002",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    assert r.status_code == 201, r.text
    ch = await _chamado(db, "880002")
    assert (await _abertura(db, ch.id)).status == "pendente"

    r = await client.post("/api/devolucoes-painel/880002/abrir-chamado", json={"abrir": False})
    assert r.status_code == 200, r.text
    await db.refresh(ch)
    ab = await _abertura(db, ch.id)
    assert ch.status_plataforma == "encerrado"
    assert ab.status == "registrada" and ab.erro == cd.ERRO_CONTESTACAO_CANCELADA

    # O job que já estava na fila roda depois — e não manda nada.
    ml.acao = True
    msg = await cd.disparar_por_id(db, ch.id)
    await db.commit()
    assert msg.status == "registrada" and ml.reviews == []

    # Sim de novo: sai do Encerrado e a contestação sai.
    r = await client.post("/api/devolucoes-painel/880002/abrir-chamado", json={"abrir": True})
    assert r.status_code == 200, r.text
    await db.refresh(ch)
    assert ch.status_plataforma is None and r.json()["abertura_status"] == "enviada"
    assert len(ml.reviews) == 1


async def test_nao_depois_que_saiu_e_recusado(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880003", numeroloja="3880003")
    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880003",
            "3880003",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    assert r.status_code == 201 and r.json()["chamado_ml_status"] == "enviada"
    r = await client.post("/api/devolucoes-painel/880003/abrir-chamado", json={"abrir": False})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "chamado_ja_saiu"


async def test_nao_saiu_pela_api_mostra_onde_esta_na_aba_chamados(
    client, make_user, auth_as, db, ml
):
    """306771 (18/11/2026): a TikTok já tinha reembolsado (quick refund) e a
    contestação não saiu pela API — mas o chamado já está na aba Chamados, que
    cuida dele dali pra frente. A janela traz o Status de lá pra dizer isso."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880004", numeroloja="3880004")
    ml.acao = False  # o ML ainda não liberou: a abertura fica na fila, nunca sai
    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880004",
            "3880004",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    assert r.status_code == 201, r.text
    ch = await _chamado(db, "880004")

    # Na fila: ainda é da janela, nada da aba Chamados.
    r = await client.get("/api/devolucoes-painel/880004/chamado")
    assert r.status_code == 200, r.text
    assert r.json()["abertura_status"] == "pendente" and r.json()["chamado_aba"] is None

    # Ficou pendente além do prazo: a abertura falha de vez sem nunca ter saído.
    ab = await _abertura(db, ch.id)
    ab.status, ab.erro = "falhou", "devolucao_prazo_esgotado"
    await db.commit()
    r = await client.get("/api/devolucoes-painel/880004/chamado")
    assert r.status_code == 200, r.text
    janela = r.json()
    assert janela["abertura_status"] == "falhou" and ml.reviews == []
    assert janela["chamado_aba"] == "analise_humano"
    assert "devolucao_prazo_esgotado" in (janela["chamado_aba_motivo"] or "")

    # O mesmo Status (e o mesmo porquê) que a coluna da aba Chamados mostra.
    r = await client.get("/api/chamados", params={"search": "880004"})
    assert r.status_code == 200, r.text
    (linha,) = [c for c in r.json()["items"] if c["id"] == str(ch.id)]
    assert (janela["chamado_aba"], janela["chamado_aba_motivo"]) == (
        linha["status_aba"],
        linha["status_aba_motivo"],
    )


# ── Conferência da IA ─────────────────────────────────────────────────────


async def test_ia_segura_quando_o_motivo_nao_bate_e_a_pessoa_troca_e_manda(
    client, make_user, auth_as, db, ml, ia
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880010", numeroloja="3880010")
    ia.respostas.append(
        conferencia.Veredito(
            bate=False,
            motivo_sugerido="Golpe",
            condicao_sugerida="Não devolvido",
            explicacao="A observação diz que a caixa chegou com um sabonete.",
        )
    )
    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880010",
            "3880010",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
            link_envio=MEGA,
            observacao="abrimos a caixa e veio um sabonete no lugar do celular",
        ),
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "segurada"
    assert ml.reviews == []  # nada foi pra plataforma
    assert len(ia.perguntas) == 1 and "sabonete" in ia.perguntas[0]
    assert 'motivo "Não recebido"' in ia.perguntas[0]
    ch = await _chamado(db, "880010")
    ab = await _abertura(db, ch.id)
    assert ab.status == "segurada" and ab.erro == "motivo_divergente"
    assert any("A IA segurou a contestação" in t for t in await _eventos(db, ch))

    # O cron das pendentes não mexe na segurada (ela não é `pendente`).
    resumo = await cd.processar_pendentes(db)
    await db.commit()
    assert ml.reviews == [] and resumo is not None

    janela = (await client.get("/api/devolucoes-painel/880010/chamado")).json()
    assert janela["abertura_status"] == "segurada"
    assert janela["conferencia"]["resultado"] == "nao_bate"
    assert janela["conferencia"]["motivo_sugerido"] == "Golpe"

    # Motivo que não abre chamado não serve pra "trocar e mandar".
    r = await client.post(
        "/api/devolucoes-painel/880010/chamado/decisao",
        json={"acao": "trocar", "motivo": "Item Incorreto"},
    )
    assert r.status_code == 422

    r = await client.post(
        "/api/devolucoes-painel/880010/chamado/decisao", json={"acao": "trocar", "motivo": "Golpe"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["abertura_status"] == "enviada"
    assert len(ia.perguntas) == 1, "a decisão da pessoa vale: a IA não confere de novo"
    assert len(ml.reviews) == 1
    _ret, reason, texto, _anexos = ml.reviews[0]
    assert reason == "SRF4"  # Golpe com o cartão do vídeo do envio = item diferente
    assert MEGA in texto and ABERTURA in texto
    dev = (
        await db.execute(select(Devolution).where(Devolution.pedido_bling == "880010"))
    ).scalar_one()
    assert dev.motivo_devolucao == "Golpe"
    await db.refresh(ch)
    assert ch.observacao.endswith("motivo: Golpe")
    assert any(
        'trocou o motivo de "Não recebido" para "Golpe"' in t for t in await _eventos(db, ch)
    )
    rast = await db.get(DevolucaoRastreio, "880010")
    assert rast.conferencia["resultado"] == "aceito" and rast.conferencia["decisao"] == "trocar"


async def test_mandar_como_esta_e_nao_abrir(client, make_user, auth_as, db, ml, ia):
    user = await make_user(permissions=_perms())
    auth_as(user)
    for numero in ("880011", "880012"):
        await _seed_pedido(db, user, numero=numero, numeroloja=f"3{numero}")
        ia.respostas.append(conferencia.Veredito(bate=False, explicacao="não bate"))
        r = await client.post(
            "/api/devolutions",
            json=_lancamento(
                numero,
                f"3{numero}",
                condicao_produto="Extraviado",
                motivo_devolucao="Não recebido",
                link_abertura=ABERTURA,
                observacao="veio outra coisa na caixa",
            ),
        )
        assert r.json()["chamado_ml_status"] == "segurada"

    r = await client.post("/api/devolucoes-painel/880011/chamado/decisao", json={"acao": "mandar"})
    assert r.status_code == 200 and r.json()["abertura_status"] == "enviada"
    assert len(ml.reviews) == 1

    r = await client.post(
        "/api/devolucoes-painel/880012/chamado/decisao", json={"acao": "nao_abrir"}
    )
    assert r.status_code == 200, r.text
    janela = r.json()
    assert janela["abrir_chamado"] is False and janela["abertura_status"] == "registrada"
    assert janela["status_plataforma"] == "encerrado"
    assert len(ml.reviews) == 1  # o 880012 não foi
    # Nada segurado: decidir de novo é recusado.
    r = await client.post("/api/devolucoes-painel/880011/chamado/decisao", json={"acao": "mandar"})
    assert r.status_code == 409


async def test_ia_sem_resposta_nao_segura(client, make_user, auth_as, db, ml, ia):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880013", numeroloja="3880013")
    ia.respostas.append(None)
    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880013",
            "3880013",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    assert r.json()["chamado_ml_status"] == "enviada"
    ch = await _chamado(db, "880013")
    assert any("não rodou" in t for t in await _eventos(db, ch))


async def test_conferencia_le_de_novo_so_quando_o_lancamento_muda():
    pecas = [conferencia.Peca("a", "A", "Golpe", "Não devolvido", "veio sabonete")]
    perguntas: list[str] = []

    async def _pergunta(texto):
        perguntas.append(texto)
        return conferencia.Veredito(bate=False, motivo_sugerido="Golpe", explicacao="x"), "m"

    r1 = await conferencia.conferir(
        pecas=pecas, observacao_pedido=None, plataforma="Shopee", anterior=None, pergunta=_pergunta
    )
    assert r1.segurar and r1.conferencia["resultado"] == "nao_bate"
    r2 = await conferencia.conferir(
        pecas=pecas,
        observacao_pedido=None,
        plataforma="Shopee",
        anterior=r1.conferencia,
        pergunta=_pergunta,
    )
    assert r2.segurar and r2.conferencia is None and len(perguntas) == 1
    aceito = {**r1.conferencia, "resultado": "aceito"}
    r3 = await conferencia.conferir(
        pecas=pecas,
        observacao_pedido=None,
        plataforma="Shopee",
        anterior=aceito,
        pergunta=_pergunta,
    )
    assert not r3.segurar and len(perguntas) == 1
    mudou = [conferencia.Peca("a", "A", "Golpe", "Não devolvido", "veio um sabonete e uma pedra")]
    r4 = await conferencia.conferir(
        pecas=mudou,
        observacao_pedido=None,
        plataforma="Shopee",
        anterior=aceito,
        pergunta=_pergunta,
    )
    assert r4.segurar and len(perguntas) == 2


async def test_resposta_da_ia_fora_da_lista_vira_vazio():
    class _Bloco:
        type = "text"
        text = (
            '{"bate": false, "motivo_sugerido": "Fraude total", "condicao_sugerida": "Usado",'
            ' "explicacao": "  não   bate  "}'
        )

    class _Msg:
        stop_reason = "end_turn"
        content = [_Bloco()]

    v = conferencia.veredito_da_mensagem(_Msg())
    assert v is not None and v.bate is False
    assert v.motivo_sugerido == "" and v.condicao_sugerida == "Usado" and v.explicacao == "não bate"

    class _Recusa(_Msg):
        stop_reason = "refusal"

    assert conferencia.veredito_da_mensagem(_Recusa()) is None
    args = conferencia.argumentos("x", modelo="claude-opus-5-5")
    assert "thinking" not in args and "temperature" not in args
    assert args["output_config"]["format"]["type"] == "json_schema"


# ── Instrução IA ──────────────────────────────────────────────────────────


async def test_instrucao_antes_do_chamado_espera_e_entra_quando_ele_nasce(
    client, make_user, auth_as, db, ml
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880020", numeroloja="3880020")
    r = await client.post(
        "/api/devolucoes-painel/880020/instrucao",
        json={"texto": "Se o ML pedir mais prova, mandar o vídeo da abertura."},
    )
    assert r.status_code == 200, r.text
    (inst,) = r.json()["instrucoes"]
    assert inst["esperando_chamado"] is True

    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880020",
            "3880020",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    assert r.status_code == 201
    ch = await _chamado(db, "880020")
    instrucoes = (
        (
            await db.execute(
                select(ChamadoMensagem).where(
                    ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "instrucao"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [m.texto for m in instrucoes] == [
        "Se o ML pedir mais prova, mandar o vídeo da abertura."
    ]
    assert instrucoes[0].autor_nome.startswith("Devoluções (")
    rast = await db.get(DevolucaoRastreio, "880020")
    assert rast.instrucao_ia_pendente is None

    # Com o chamado já aberto, entra direto nele.
    r = await client.post("/api/devolucoes-painel/880020/instrucao", json={"texto": "Aceitar 30%."})
    textos = [i["texto"] for i in r.json()["instrucoes"]]
    assert textos[-1] == "Aceitar 30%." and not r.json()["instrucoes"][-1]["esperando_chamado"]


# ── Texto da disputa e prévia ─────────────────────────────────────────────


async def test_video_da_abertura_vai_no_texto_antes_da_observacao():
    dev = Devolution(
        pedido_marketplace="2000",
        sku="b1",
        produtos="Mala",
        motivo_devolucao="Golpe",
        observacao="veio um sabonete",
        link_abertura=ABERTURA,
    )
    texto = cd.texto_padrao(
        dev,
        "SRF4",
        link_envio=MEGA,
        linhas=[dev],
        link_abertura=cd.link_abertura_do_pedido(dev, [dev]),
    )
    linhas = texto.splitlines()
    i_abertura = linhas.index(f"Vídeo da abertura do pacote: {ABERTURA}")
    assert i_abertura < next(i for i, x in enumerate(linhas) if x.startswith("Observação:"))
    # "Não recebido": o pacote não chegou — sem vídeo de abertura no texto.
    dev.motivo_devolucao = "Não recebido"
    assert cd.link_abertura_do_pedido(dev, [dev]) == ""
    # Texto digitado no lugar do link não vai.
    dev.motivo_devolucao = "Golpe"
    dev.link_abertura = "nao tem"
    assert cd.link_abertura_do_pedido(dev, [dev]) == ""


async def test_previa_mostra_o_texto_com_o_que_esta_na_janela(client, make_user, auth_as, db):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880030", numeroloja="3880030")
    url = "/api/devolucoes-painel/880030/chamado/previa"
    r = await client.post(
        url, json={"linhas": [{"sku": "b001.26", "motivo_devolucao": "Item Incorreto"}]}
    )
    assert r.status_code == 200 and r.json()["abre"] is False
    r = await client.post(
        url,
        json={
            "linhas": [
                {
                    "sku": "b001.26",
                    "produtos": "Mala",
                    "motivo_devolucao": "Golpe",
                    "observacao": "veio um sabonete",
                },
                {"sku": "a001", "produtos": "Fone"},
            ],
            "plataforma": "TikTok",
            "link_envio": MEGA,
            "link_abertura": ABERTURA,
            "fotos": 2,
        },
    )
    out = r.json()
    assert out["abre"] is True and out["motivo"] == "Golpe"
    assert "Pedido 3880030" in out["texto"] and "veio um sabonete" in out["texto"]
    assert f"Vídeo da abertura do pacote: {ABERTURA}" in out["texto"]
    assert "Seguem 2 foto(s)" in out["texto"] and MEGA in out["texto"]
    assert any("500" in a for a in out["avisos"])


# ── Histórico antigo ──────────────────────────────────────────────────────


async def test_historico_antigo_nasce_resolvido_e_aparece_em_resolvidas(
    client, make_user, auth_as, db
):
    """Migration 0485: pedido com lançamento que nunca entrou na lista do painel
    vira resolvido na marca da largada, na data do último lançamento."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    agora = datetime.now(UTC)

    async def pedido(numero: str, situacao: str, entrada=None) -> None:
        db.add(
            BlingOrder(
                id=uuid4(),
                numero=numero,
                numeroloja=f"M{numero}",
                bling_id=1,
                situacao=situacao,
                loja="55",
                data=agora - timedelta(days=120),
                item_index=0,
                item_codigo="a1",
                item_descricao="Produto",
                item_quantidade=1,
                aguardando_devolucao_data=entrada,
            )
        )

    await pedido("890001", "9")  # antigo, fora da lista: vira resolvido
    await pedido("890002", "83957", entrada=agora.date())  # está na lista: fica
    await pedido("890003", "9")  # já resolvido à mão: não muda
    await pedido("890004", "9")  # "nova devolução" lançada no painel novo: fica aberta
    for numero, dias in (
        ("890001", 100),
        ("890001", 90),
        ("890002", 1),
        ("890003", 80),
        ("890004", 0),
    ):
        d = Devolution(pedido_bling=numero, conta="aguiar", sku="a1", quantidade=1)
        d.created_at = agora - timedelta(days=dias)
        db.add(d)
    db.add(
        DevolucaoRastreio(
            pedido_bling="890003", resolvido_at=agora - timedelta(days=5), resolvido_como="Voltou"
        )
    )
    await db.commit()

    fonte = (
        Path(__file__).parents[1] / "alembic/versions/0485_devolucao_chamado_conferencia.py"
    ).read_text()
    sql = re.search(r'op\.execute\(\s*f"""(.*?)"""', fonte, re.S).group(1)
    sql = sql.replace('"{SCHEMA}".', "").replace("{TABELA}", "devolucao_rastreio")
    sql = sql.replace("{LARGADA}", PAINEL_LARGADA_HISTORICO)
    await db.execute(text(sql))
    await db.commit()

    r1 = await db.get(DevolucaoRastreio, "890001")
    await db.refresh(r1)
    assert r1.resolvido_como == PAINEL_LARGADA_HISTORICO
    assert abs((r1.resolvido_at - (agora - timedelta(days=90))).total_seconds()) < 5
    assert await db.get(DevolucaoRastreio, "890002") is None
    r3 = await db.get(DevolucaoRastreio, "890003")
    await db.refresh(r3)
    assert r3.resolvido_como == "Voltou"

    assert await db.get(DevolucaoRastreio, "890004") is None
    abertas = (await client.get("/api/devolucoes-painel")).json()["items"]
    assert {i["pedido_bling"] for i in abertas} == {"890002", "890004"}
    desde = (agora - timedelta(days=200)).date().isoformat()
    res = (await client.get(f"/api/devolucoes-painel?mostrar=resolvidas&desde={desde}")).json()
    como = {i["pedido_bling"]: i["resolvido_como"] for i in res["items"]}
    assert como == {"890001": PAINEL_LARGADA_HISTORICO, "890003": "Voltou"}
    # Sem o período, só os últimos 60 dias de Resolvidas.
    res60 = (await client.get("/api/devolucoes-painel?mostrar=resolvidas")).json()["items"]
    assert {i["pedido_bling"] for i in res60} == {"890003"}


# ── Achados da revisão cética (16/11) ─────────────────────────────────────


async def test_trocar_respeita_a_trava_do_link_de_envio(client, make_user, auth_as, db, ml, ia):
    """Mala "Não recebido" (não exige o vídeo do envio) segurada; trocar pra Golpe
    exige o vídeo, como no PATCH — 422 e nada muda."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880040", numeroloja="3880040")
    ia.respostas.append(conferencia.Veredito(bate=False, motivo_sugerido="Golpe", explicacao="x"))
    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880040",
            "3880040",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
            observacao="veio um sabonete",
        ),
    )
    assert r.json()["chamado_ml_status"] == "segurada"
    r = await client.post(
        "/api/devolucoes-painel/880040/chamado/decisao", json={"acao": "trocar", "motivo": "Golpe"}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "link_envio_obrigatorio"
    dev = (
        await db.execute(select(Devolution).where(Devolution.pedido_bling == "880040"))
    ).scalar_one()
    await db.refresh(dev)
    ch = await _chamado(db, "880040")
    assert dev.motivo_devolucao == "Não recebido"
    assert (await _abertura(db, ch.id)).status == "segurada" and ml.reviews == []


async def test_mandar_vale_mesmo_com_a_observacao_mudada_pela_janela(
    client, make_user, auth_as, db, ml, ia
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880041", numeroloja="3880041")
    ia.respostas.append(conferencia.Veredito(bate=False, explicacao="x"))
    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880041",
            "3880041",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
            observacao="veio outra coisa",
        ),
    )
    dev_id = r.json()["id"]
    # A observação do chamado é salva pela janela (PATCH só desse campo)…
    r = await client.patch(
        f"/api/devolutions/{dev_id}", json={"observacao": "veio outra coisa, foto anexa"}
    )
    assert r.status_code == 200
    # …e o "mandar como está" vale pro que está lá agora: a IA não segura de novo.
    r = await client.post("/api/devolucoes-painel/880041/chamado/decisao", json={"acao": "mandar"})
    assert r.status_code == 200 and r.json()["abertura_status"] == "enviada"
    assert len(ia.perguntas) == 1 and len(ml.reviews) == 1


async def test_nao_e_decisao_esperam_o_envio_em_andamento(client, make_user, auth_as, db, ml):
    """O job da fila segura a trava enquanto manda (a IA pode levar segundos):
    o "Não" não é escrito por cima — 409, a pessoa vê de novo em seguida."""
    from tests.conftest import _test_engine

    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880042", numeroloja="3880042")
    ml.acao = False
    await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880042",
            "3880042",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    ch = await _chamado(db, "880042")
    chave_lock = f"chamado_devolucao_disparar:{ch.id}"
    async with _test_engine.connect() as outra:
        await outra.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": chave_lock})
        r = await client.post("/api/devolucoes-painel/880042/abrir-chamado", json={"abrir": False})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "chamado_sendo_enviado"
        await outra.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": chave_lock})
    r = await client.post("/api/devolucoes-painel/880042/abrir-chamado", json={"abrir": False})
    assert r.status_code == 200 and r.json()["abertura_status"] == "registrada"


async def test_segurada_continua_segurada_com_a_ia_desligada(
    client, make_user, auth_as, db, ml, ia, monkeypatch
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880043", numeroloja="3880043")
    ia.respostas.append(conferencia.Veredito(bate=False, explicacao="x"))
    r = await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880043",
            "3880043",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    dev_id = r.json()["id"]
    monkeypatch.setattr(conferencia, "ligada", lambda: False)
    # Foto anexada: o chamado é "garantido" de novo e o disparo roda…
    up = await client.post(
        f"/api/devolutions/{dev_id}/anexos", files={"file": ("a.png", _PNG, "image/png")}
    )
    assert up.status_code == 201, up.text
    # …mas o que a IA segurou espera a decisão de alguém.
    assert up.json()["chamado_ml_status"] == "segurada" and ml.reviews == []


async def test_decidir_em_chamado_encerrado_e_recusado(client, make_user, auth_as, db, ml, ia):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="880044", numeroloja="3880044")
    ia.respostas.append(conferencia.Veredito(bate=False, explicacao="x"))
    await client.post(
        "/api/devolutions",
        json=_lancamento(
            "880044",
            "3880044",
            condicao_produto="Extraviado",
            motivo_devolucao="Não recebido",
            link_abertura=ABERTURA,
        ),
    )
    ch = await _chamado(db, "880044")
    ch.status_plataforma = "encerrado"
    await db.commit()
    r = await client.post("/api/devolucoes-painel/880044/chamado/decisao", json={"acao": "mandar"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "chamado_encerrado"


async def test_segurada_avisa_gente_na_varredura():
    from app.services import chamados_pendencias

    assert chamados_pendencias._rota("motivo_divergente") == "humano"


async def test_chave_nao_depende_da_ordem_das_pecas():
    a = conferencia.Peca("a", "A", "Golpe", "Novo", "x")
    b = conferencia.Peca("b", "B", "Golpe", "Usado", "y")
    assert conferencia.chave([a, b], None) == conferencia.chave([b, a], None)


async def test_reabre_o_que_foi_dado_como_resolvido_sem_voltar_pro_estoque(db):
    """Migration 0486 ("se já voltou estoque tá resolvido"): marca do sistema com
    peça no técnico ou estoque que não entrou volta pra abertas; sem condição e
    resolução feita por pessoa ficam."""
    agora = datetime.now(UTC)
    casos = {
        "895001": ("antes do painel novo", {"condicao_produto": "Manutenção"}),
        "895002": (
            "antes do painel novo (histórico)",
            {"condicao_produto": "Novo", "estoque_status": "falhou"},
        ),
        "895003": ("antes do painel novo", {"condicao_produto": None}),
        "895004": ("Voltou pro estoque", {"condicao_produto": "Novo", "estoque_status": "falhou"}),
        "895005": (
            "antes do painel novo",
            {
                "condicao_produto": "Manutenção",
                "manutencao_destino": "Usado",
                "estoque_status": "ok",
            },
        ),
    }
    for numero, (como, campos) in casos.items():
        db.add(Devolution(pedido_bling=numero, conta="aguiar", sku="a1", quantidade=1, **campos))
        db.add(DevolucaoRastreio(pedido_bling=numero, resolvido_at=agora, resolvido_como=como))
    await db.commit()

    fonte = (
        Path(__file__).parents[1] / "alembic/versions/0486_devolucao_reabre_o_que_nao_terminou.py"
    ).read_text()
    sql = re.search(r'op\.execute\(\s*f"""(.*?)"""', fonte, re.S).group(1)
    sql = sql.replace('"{SCHEMA}".', "").replace("{sem_condicao}", "")
    sql = sql.replace("{MARCAS[0]}", "antes do painel novo")
    sql = sql.replace("{MARCAS[1]}", "antes do painel novo (histórico)")
    await db.execute(text(sql))
    await db.commit()

    reabertos = set()
    for numero in casos:
        r = await db.get(DevolucaoRastreio, numero)
        await db.refresh(r)
        if r.resolvido_at is None:
            reabertos.add(numero)
    assert reabertos == {"895001", "895002"}
