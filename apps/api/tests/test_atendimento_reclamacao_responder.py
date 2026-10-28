# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""Responder a reclamação/mediação do ML pelo DaVinci (28/10/2026).

O pedido do Eduardo: "a gente deveria no mercado livre conseguir responder a
mediacao com meli como ocorre no duoke". A doc oficial ("Gerenciar mensagem
de uma reclamação"): POST /post-purchase/v1/claims/{id}/actions/send-message
com `receiver_role` (`complainant` na etapa `claim`, `mediator` na
`dispute`, pela ação da loja em `available_actions`) e `attachments` (os
`filename` do POST /claims/{id}/attachments, JPG/PNG/PDF até 5 MB).

Sem rede: o `MercadoLivreClient` de verdade sobre um transporte httpx falso
(o ML falso guarda cada pedido). O que se segura, na ordem do que custaria
mais caro:

- só PESSOA manda: a IA e a mensagem automática são recusadas antes de
  qualquer chamada;
- só para quem o ML libera: o destino fora de `dados.destinos` é recusado sem
  chamada; e, na hora do envio, o GET da reclamação confere a ação de novo
  (a etapa mudou → falha limpa, sem POST);
- o POST vai com o `receiver_role` certo, numa tentativa só: 4xx = falhou,
  5xx = revisar (ambíguo);
- a linha guarda `payload.para` e fica SEM id (o ML não devolve o id da
  mensagem): a leitura das reclamações a adota pelo texto, sem duplicar;
- o anexo sobe para a reclamação e vai na mensagem; PDF acima de 5 MB não sai;
- a rota: `para` no corpo, os destinos liberados no `envio` do detalhe.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    UserRole,
)
from app.routers import atendimento as rota_atendimento
from app.security.cipher import encrypt_json
from app.services.atendimento import clientes, enviar, foto, gravar, reclamacoes, validador
from app.services.atendimento.constantes import (
    AUTOR_LOJA,
    CANAL_RECLAMACAO,
    CONVERSA_BLOQUEADA,
    ORIGEM_IA,
)
from app.services.atendimento.enviar import EnvioRecusado
from app.services.marketplaces.ml import MercadoLivreClient

URL = "/api/atendimento"
SELLER = 999000111
CLAIM = "5601757827"
JPG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"1" * 200
_AsyncClientReal = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _validador_falso(monkeypatch):
    def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
        return " ".join((texto or "").split())

    def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
        return [] if texto.strip() else ["Resposta vazia."]

    monkeypatch.setattr(validador, "normalizar", normalizar)
    monkeypatch.setattr(validador, "validar", validar)


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_auto_ativo", True)
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    return s


class _MLFalso:
    """A reclamação no ML: o detalhe (com as ações da loja), o upload e o envio."""

    def __init__(self, monkeypatch, *, acoes=("send_message_to_mediator",), stage="dispute"):
        self.feitos: list[httpx.Request] = []
        self.acoes = list(acoes)
        self.stage = stage
        self.status = "opened"
        self.envio = (201, {})

        def registrar(request: httpx.Request) -> httpx.Response:
            self.feitos.append(request)
            return self.responder(request)

        def fabrica(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(registrar)
            return _AsyncClientReal(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", fabrica)

        async def cliente(integration):
            return MercadoLivreClient(
                {"access_token": "tok", "refresh_token": "rt", "user_id": SELLER}
            )

        monkeypatch.setattr(clientes, "cliente_da_integracao", cliente)

    def responder(self, request: httpx.Request) -> httpx.Response:
        caminho = request.url.path
        if request.method == "GET" and caminho == f"/post-purchase/v1/claims/{CLAIM}":
            return httpx.Response(
                200,
                json={
                    "id": int(CLAIM),
                    "status": self.status,
                    "stage": self.stage,
                    "players": [
                        {"role": "complainant", "type": "buyer", "available_actions": []},
                        {
                            "role": "respondent",
                            "type": "seller",
                            "user_id": SELLER,
                            "available_actions": [
                                {"action": a, "mandatory": True, "due_date": None}
                                for a in self.acoes
                            ],
                        },
                    ],
                },
            )
        if request.method == "POST" and caminho == f"/post-purchase/v1/claims/{CLAIM}/attachments":
            return httpx.Response(200, json={"user_id": SELLER, "filename": "777_nf.pdf"})
        if caminho == f"/post-purchase/v1/claims/{CLAIM}/actions/send-message":
            status, corpo = self.envio
            # Às vezes o ML devolve a própria reclamação (com o id DELA).
            return httpx.Response(status, json=corpo)
        return httpx.Response(404, json={"code": "not_found"})

    def caminhos(self) -> list[tuple[str, str]]:
        return [(p.method, p.url.path) for p in self.feitos]

    def enviados(self) -> list[dict]:
        return [json.loads(p.content) for p in self.feitos if p.url.path.endswith("/send-message")]


async def _cenario(db, make_user, *, destinos=("mediador",), modo="humano", situacao=None):
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.ML,
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    # O canal de leitura da loja (o pós-venda): é dele o modo.
    db.add(
        AtendimentoCanal(
            integration_id=integ.id, plataforma="ml", canal="pos_venda", modo=modo, status="ok"
        )
    )
    await db.flush()
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=integ,
        plataforma="ml",
        canal_nome=CANAL_RECLAMACAO,
        externo_id=CLAIM,
        comprador_id="111",
        pedido_marketplace="2000019150957778",
        dados={"claim_id": CLAIM, "status_ml": "opened", "destinos": list(destinos)},
    )
    if situacao:
        conversa.situacao = situacao
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="h-1",
        autor="mediador",
        texto="Vamos solicitar ao comprador as fotos. Quer que eu faça isso?",
        enviada_em=datetime.now(UTC) - timedelta(minutes=30),
    )
    await db.commit()
    return conversa, user


# ─────────────── só pessoa, só para quem o ML libera ───────────────


async def test_mediador_recebe_a_mensagem_com_o_receiver_role(db, make_user, monkeypatch):
    ml = _MLFalso(monkeypatch)
    ml.envio = (201, {"id": int(CLAIM), "status": "opened"})
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(
        db, conversa, "  Sim, pode solicitar as fotos.  ", user=user, para="mediador"
    )
    assert m.status == "enviada", m.erro
    assert (m.autor, m.texto) == (AUTOR_LOJA, "Sim, pode solicitar as fotos.")
    assert m.payload["para"] == "mediator"
    # O id da RECLAMAÇÃO na resposta não vira id da mensagem.
    assert m.externo_id is None
    assert ml.caminhos() == [
        ("GET", f"/post-purchase/v1/claims/{CLAIM}"),
        ("POST", f"/post-purchase/v1/claims/{CLAIM}/actions/send-message"),
    ]
    assert ml.enviados() == [
        {"receiver_role": "mediator", "message": "Sim, pode solicitar as fotos.", "attachments": []}
    ]


async def test_envio_sem_para_e_recusado_mas_a_faixa_usa_o_liberado(db, make_user, monkeypatch):
    """Ninguém escolheu o destino (tela antiga, a oferta de troca): nada sai.
    A faixa da tela (sem `para`) diz se dá para escrever, pelo primeiro liberado."""
    ml = _MLFalso(monkeypatch, acoes=("send_message_to_complainant",), stage="claim")
    conversa, user = await _cenario(db, make_user, destinos=("comprador",))
    assert await enviar.motivo_para_nao_enviar(db, conversa) is None
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Olá, vamos resolver.", user=user)
    assert e.value.code == enviar.RECUSA_DESTINO_INDISPONIVEL
    assert e.value.detail == enviar.MOTIVO_RECLAMACAO_SEM_PARA
    with pytest.raises(EnvioRecusado):
        await enviar.enviar_foto(
            db, conversa, foto.validar_foto(JPG, "a.jpg"), legenda="x", user=user
        )
    assert ml.feitos == []
    m = await enviar.enviar_resposta(
        db, conversa, "Olá, vamos resolver.", user=user, para="comprador"
    )
    assert m.status == "enviada", m.erro
    assert m.payload["para"] == "complainant"
    assert ml.enviados()[0]["receiver_role"] == "complainant"


async def test_mesmo_texto_ao_outro_destino_nao_e_repetido(db, make_user, monkeypatch):
    ml = _MLFalso(monkeypatch, acoes=("send_message_to_complainant", "send_message_to_mediator"))
    conversa, user = await _cenario(db, make_user, destinos=("comprador", "mediador"))
    await enviar.enviar_resposta(db, conversa, "Segue a NF.", user=user, para="comprador")
    m = await enviar.enviar_resposta(db, conversa, "Segue a NF.", user=user, para="mediador")
    assert m.status == "enviada"
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Segue a NF.", user=user, para="mediador")
    assert e.value.code == enviar.RECUSA_ENVIO_REPETIDO
    assert [x["receiver_role"] for x in ml.enviados()] == ["complainant", "mediator"]


async def test_o_modo_e_o_do_pos_venda_da_loja(db, make_user, monkeypatch):
    """Pós-venda em observar (o Duoke responde) com a pergunta em humano: não sai."""
    ml = _MLFalso(monkeypatch)
    conversa, user = await _cenario(db, make_user, modo="observar")
    db.add(
        AtendimentoCanal(
            integration_id=conversa.integration_id,
            plataforma="ml",
            canal="pergunta",
            modo="humano",
            status="ok",
        )
    )
    await db.commit()
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Oi", user=user, para="mediador")
    assert e.value.code == enviar.RECUSA_CANAL_EM_OBSERVACAO
    assert ml.feitos == []


async def test_recusa_com_block_nao_bloqueia_a_conversa(db, make_user, monkeypatch):
    """Quem bloqueia a reclamação é a leitura (pelas ações): o 4xx só falha o envio."""
    ml = _MLFalso(monkeypatch)
    ml.envio = (400, {"code": "claim_blocked", "message": "blocked"})
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Oi", user=user, para="mediador")
    assert m.status == "falhou"
    await db.refresh(conversa)
    assert conversa.situacao != CONVERSA_BLOQUEADA


async def test_destino_nao_liberado_recusa_sem_chamar_o_ml(db, make_user, monkeypatch):
    """Na mediação o ML não deixa falar direto com o comprador."""
    ml = _MLFalso(monkeypatch)
    conversa, user = await _cenario(db, make_user, destinos=("mediador",))
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Oi", user=user, para="comprador")
    assert e.value.code == enviar.RECUSA_DESTINO_INDISPONIVEL
    assert "mediador" in str(e.value.detail)
    assert ml.feitos == []
    assert await db.scalar(select(AtendimentoMensagem.id).where(
        AtendimentoMensagem.conversa_id == conversa.id, AtendimentoMensagem.autor == AUTOR_LOJA
    )) is None  # fmt: skip


async def test_sem_destino_liberado_recusa(db, make_user, monkeypatch):
    ml = _MLFalso(monkeypatch)
    conversa, user = await _cenario(db, make_user, destinos=())
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa is not None and recusa.code == enviar.RECUSA_CONVERSA_BLOQUEADA
    with pytest.raises(EnvioRecusado):
        await enviar.enviar_resposta(db, conversa, "Oi", user=user, para="mediador")
    assert ml.feitos == []


async def test_ia_e_automatica_nunca_mandam(db, make_user, monkeypatch):
    ml = _MLFalso(monkeypatch)
    conversa, user = await _cenario(db, make_user)
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Oi", user=None, origem=ORIGEM_IA)
    assert e.value.code == enviar.RECUSA_AUTO_DESLIGADO
    assert e.value.detail == enviar.MOTIVO_RECLAMACAO_SO_PESSOA
    with pytest.raises(EnvioRecusado) as e:
        await enviar._destino_automatica(db, conversa, codigo="qualquer")
    assert e.value.code == enviar.RECUSA_AUTO_DESLIGADO
    assert ml.feitos == []


async def test_loja_em_observar_nao_manda(db, make_user, monkeypatch):
    ml = _MLFalso(monkeypatch)
    conversa, user = await _cenario(db, make_user, modo="observar")
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Oi", user=user, para="mediador")
    assert e.value.code == enviar.RECUSA_CANAL_EM_OBSERVACAO
    assert ml.feitos == []


async def test_etapa_mudou_no_ml_falha_limpa_sem_post(db, make_user, monkeypatch):
    """O DaVinci achava mediador liberado; o ML já fechou a ação — nada sai."""
    ml = _MLFalso(monkeypatch, acoes=())
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Oi", user=user, para="mediador")
    assert m.status == "falhou"
    assert m.erro == "reclamacao_sem_acao:send_message_to_mediator"
    assert ml.caminhos() == [("GET", f"/post-purchase/v1/claims/{CLAIM}")]


async def test_recusa_4xx_falhou_e_5xx_revisar(db, make_user, monkeypatch):
    ml = _MLFalso(monkeypatch)
    ml.envio = (400, {"code": "invalid_message"})
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Primeira", user=user, para="mediador")
    assert (m.status, m.erro) == ("falhou", "HTTP 400 invalid_message")
    ml.envio = (502, {})
    m = await enviar.enviar_resposta(db, conversa, "Segunda", user=user, para="mediador")
    assert m.status == "revisar"
    # Uma tentativa só por envio (o 502 não é repetido).
    assert len(ml.enviados()) == 2


async def test_a_leitura_adota_a_nossa_mensagem_sem_duplicar(db, make_user, monkeypatch):
    _MLFalso(monkeypatch)
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Pode solicitar.", user=user, para="mediador")
    assert m.status == "enviada"
    volta = reclamacoes.mensagem_da_reclamacao(
        {
            "sender_role": "respondent",
            "receiver_role": "mediator",
            "message": "Pode solicitar.",
            "date_created": datetime.now(UTC).isoformat(),
            "hash": "5601757827_0_abc",
            "stage": "dispute",
            "status": "available",
        }
    )
    linha, criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=volta.externo_id,
        autor=volta.autor,
        texto=volta.texto,
        enviada_em=volta.enviada_em,
        payload=volta.payload,
    )
    assert criada is False and linha.id == m.id
    assert linha.externo_id == "5601757827_0_abc"
    assert linha.payload["para"] == "mediator"


# ─────────────── anexo ───────────────


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "NOTA FISCAL")
    return doc.tobytes()


async def test_pdf_sobe_para_a_reclamacao_e_vai_na_mensagem(db, make_user, monkeypatch):
    ml = _MLFalso(monkeypatch)
    conversa, user = await _cenario(db, make_user)
    assert foto.motivo_sem_foto(conversa) is None
    assert foto.como_vai_o_pdf(conversa) == "arquivo"
    anexo = foto.validar_para_a_caixa(conversa, _pdf(), "NF 15.pdf", "application/pdf")
    m = await enviar.enviar_foto(
        db, conversa, anexo, legenda="Segue a nota fiscal.", user=user, para="mediador"
    )
    assert m.status == "enviada", m.erro
    assert (m.tipo, m.texto, m.payload["para"]) == ("arquivo", "Segue a nota fiscal.", "mediator")
    assert ml.caminhos() == [
        ("GET", f"/post-purchase/v1/claims/{CLAIM}"),
        ("POST", f"/post-purchase/v1/claims/{CLAIM}/attachments"),
        ("POST", f"/post-purchase/v1/claims/{CLAIM}/actions/send-message"),
    ]
    upload = ml.feitos[1]
    assert b'name="file"' in upload.content and b'filename="NF_15.pdf"' in upload.content
    assert ml.enviados() == [
        {
            "receiver_role": "mediator",
            "message": "Segue a nota fiscal.",
            "attachments": ["777_nf.pdf"],
        }
    ]


async def test_foto_sem_acao_nao_sobe_o_anexo(db, make_user, monkeypatch):
    ml = _MLFalso(monkeypatch, acoes=())
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_foto(
        db, conversa, foto.validar_foto(JPG, "a.jpg"), legenda="Foto.", user=user, para="mediador"
    )
    assert m.status == "falhou"
    assert ml.caminhos() == [("GET", f"/post-purchase/v1/claims/{CLAIM}")]


async def test_pdf_acima_de_5_mb_nao_sai(db, make_user, monkeypatch):
    conversa, _ = await _cenario(db, make_user)
    grande = foto.Foto(
        conteudo=b"%PDF-" + b"0" * 5_000_001, nome="nf.pdf", mime=foto.PDF, sha256="x"
    )
    with pytest.raises(foto.FotoInvalida) as e:
        foto.para_a_caixa(conversa, grande)
    assert e.value.code == "pdf_grande"


# ─────────────── a rota ───────────────


async def test_rota_destinos_no_detalhe_e_para_no_corpo(
    db, client, make_user, auth_as, monkeypatch
):
    ml = _MLFalso(monkeypatch)
    conversa, _ = await _cenario(db, make_user)
    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"{URL}/conversas/{conversa.id}")
    assert r.status_code == 200, r.text
    envio = r.json()["envio"]
    assert envio["pode_enviar"] is True
    assert envio["modo"] == "humano"
    assert envio["limite_caracteres"] == 2000
    assert envio["destinos"] == [{"chave": "mediador", "rotulo": "Para o Mercado Livre (mediador)"}]
    r = await client.post(
        f"{URL}/conversas/{conversa.id}/responder", json={"texto": "Oi", "para": "comprador"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "destino_indisponivel"
    r = await client.post(
        f"{URL}/conversas/{conversa.id}/responder", json={"texto": "Pode.", "para": "mediador"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["mensagem"]["status"] == "enviada"
    assert ml.enviados()[-1]["receiver_role"] == "mediator"
    r = await client.post(
        f"{URL}/conversas/{conversa.id}/responder", json={"texto": "x", "para": "ml"}
    )
    assert r.status_code == 422


async def test_conversa_bloqueada_so_leitura_antiga_continua_recusando(db, make_user, monkeypatch):
    """Até a leitura seguinte trocar o bloqueio velho, a conversa segue bloqueada."""
    ml = _MLFalso(monkeypatch)
    conversa, user = await _cenario(db, make_user, situacao=CONVERSA_BLOQUEADA)
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Oi", user=user, para="mediador")
    assert e.value.code == enviar.RECUSA_CONVERSA_BLOQUEADA
    assert ml.feitos == []
