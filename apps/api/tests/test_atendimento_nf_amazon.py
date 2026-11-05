# ruff: noqa: F401, F811 — fixtures compartilhadas da Amazon e do envio
"""A NF na resposta da Amazon (05/11/2026), "igual às outras, mesmo padrão".

O que estes testes seguram:

- a NF é achada pelo pedido da Amazon (701-…) → o pedido correlato no Bling
  (`numeroloja`) → a nota cheia dele, como nas outras plataformas;
- o PDF sai no MESMO e-mail da resposta ao endereço de retransmissão, com o
  texto, pela régua do envio (enviada, falhou, revisar) e a marca da chave;
- LGPD: o nº do pedido do MODELO da Amazon manda; o digitado pelo comprador
  liga a conversa, mas não tira a NF;
- o e-mail informativo (sem comprador) não recebe anexo, e nada sai sozinho.
"""

from __future__ import annotations

import smtplib
from email import message_from_bytes
from email import policy as politica_email
from types import SimpleNamespace

import pytest

from app.models import AtendimentoConversa, UserRole
from app.services.atendimento import amazon_email, danfe, enviar, foto, nf_automatica, painel
from app.services.atendimento.constantes import PLATAFORMAS_SEM_AUTO
from tests.test_atendimento_amazon_email import (
    CAIXA,
    PEDIDO_KFA,
    PEDIDO_KIA,
    RELAY_MARIA,
    SmtpFalso,
    _instalar_smtp,
    caixa_configurada,
)
from tests.test_atendimento_danfe import _nota, _pedido, _xml
from tests.test_atendimento_enviar import RELAY, _cenario_amazon, ligado

URL = "/api/atendimento"
PEDIDO_BLING = "920001"


def _chave(numero: int) -> str:
    return f"352610{'00000000000191'}55001{numero:09d}1{numero % 10**8:08d}0"


async def _cena(db, make_user, *, pedido: str = PEDIDO_KFA) -> tuple[AtendimentoConversa, object]:
    """A conversa de um comprador com o pedido 701-… e a NF do pedido correlato no Bling."""
    conversa, user = await _cenario_amazon(db, make_user)
    conversa.pedido_marketplace = pedido
    await db.commit()
    await _pedido(db, PEDIDO_BLING, pedido, "424.00")
    await _nota(db, PEDIDO_BLING, "2009", "424.00", chave=_chave(2009))
    return conversa, user


def _pdf() -> foto.Foto:
    return foto.validar_foto(
        danfe.gerar_pdf(_xml(chave=_chave(2009), numero="2009")), "NF_2009.pdf"
    )


def _enviado(smtp: SmtpFalso):
    return message_from_bytes(smtp.enviado, policy=politica_email.default)


# ─────────────── de onde vem o pedido do e-mail ───────────────


def _corpo(miolo: str, *, antes: str = "", depois: str = "") -> str:
    return (
        f"{antes}\n------------- Iniciar mensagem -------------\n{miolo}\n"
        f"------------- Encerrar mensagem -------------\n{depois}\n"
    )


@pytest.mark.parametrize(
    ("assunto", "corpo", "pedido", "do_texto"),
    [
        # O assunto da Amazon manda.
        (f"Pedido {PEDIDO_KFA}", _corpo("oi"), PEDIDO_KFA, False),
        # O modelo, antes ou depois da mensagem.
        ("Pergunta", _corpo("oi", antes=f"ID do pedido: {PEDIDO_KFA}"), PEDIDO_KFA, False),
        ("Pergunta", _corpo("oi", depois=f"Order ID: {PEDIDO_KFA}"), PEDIDO_KFA, False),
        # O comprador cita OUTRO pedido: vale o do modelo, mesmo com o dele antes no texto.
        (
            "Pergunta",
            _corpo(f"é o {PEDIDO_KIA}", depois=f"Order ID: {PEDIDO_KFA}"),
            PEDIDO_KFA,
            False,
        ),
        # Só no que ele escreveu: liga, mas marcado.
        ("Pergunta", _corpo(f"meu pedido é {PEDIDO_KIA}"), PEDIDO_KIA, True),
        (
            "Pergunta",
            f"Sem marcador nenhum, pedido {PEDIDO_KIA}",
            PEDIDO_KIA,
            True,
        ),
        ("Pergunta", _corpo("sem pedido"), None, False),
    ],
)
def test_pedido_do_modelo_manda(assunto, corpo, pedido, do_texto):
    assert amazon_email._pedido_do_email(assunto, corpo) == (pedido, do_texto)


def test_marca_do_pedido_digitado_e_confirmada_pelo_modelo():
    def email(**kw):
        base = {
            "message_id": "<m@a>",
            "remetente": RELAY_MARIA,
            "remetente_nome": None,
            "tag": None,
            "assunto": "x",
            "pedido": PEDIDO_KFA,
            "enviada_em": None,
            "texto": "oi",
        }
        return amazon_email.EmailAmazon(**{**base, **kw})

    chave = amazon_email.CHAVE_PEDIDO_DO_TEXTO
    dados = amazon_email._dados_do_email(email(pedido_do_texto=True), {}, mais_nova=True)
    assert dados[chave] is True
    # O modelo da Amazon confirma de vez…
    assert amazon_email._dados_do_email(email(), {chave: True}, mais_nova=True)[chave] is False
    # …e o digitado depois não desfaz.
    dados = amazon_email._dados_do_email(
        email(pedido_do_texto=True), {chave: False}, mais_nova=True
    )
    assert chave not in dados


# ─────────────── o e-mail com o anexo ───────────────


def test_montar_resposta_com_o_pdf():
    msg = amazon_email.montar_resposta(
        remetente=CAIXA,
        para=RELAY_MARIA,
        assunto="Pergunta",
        texto="Segue a nota fiscal do seu pedido.",
        pedido=PEDIDO_KFA,
        em_resposta_a="<x@amazon>",
        anexos=[("NF_2009.pdf", b"%PDF-1.7 teste", "application/pdf")],
    )
    lido = message_from_bytes(bytes(msg), policy=politica_email.default)
    assert lido["In-Reply-To"] == "<x@amazon>"
    assert f"Pedido: {PEDIDO_KFA}" in lido.get_body(("plain",)).get_content()
    (anexo,) = list(lido.iter_attachments())
    assert anexo.get_filename() == "NF_2009.pdf"
    assert anexo.get_content_type() == "application/pdf"
    assert anexo.get_content() == b"%PDF-1.7 teste"


async def test_pessoa_manda_a_nf_em_pdf(db, make_user, ligado, caixa_configurada, monkeypatch):
    smtp = _instalar_smtp(monkeypatch, SmtpFalso())
    conversa, user = await _cena(db, make_user)
    assert foto.para_a_caixa(conversa, _pdf()).mime == foto.PDF  # não vira imagem
    m = await enviar.enviar_foto(
        db,
        conversa,
        _pdf(),
        legenda="Segue a nota fiscal do seu pedido.",
        user=user,
        nf_chave=_chave(2009),
    )
    assert m.status == "enviada", m.erro
    assert m.tipo == "arquivo" and m.texto == "Segue a nota fiscal do seu pedido."
    assert m.payload["nf"]["chave"] == _chave(2009)
    assert ("rcpt", RELAY) in smtp.chamadas
    enviado = _enviado(smtp)
    assert m.externo_id == enviado["Message-ID"]  # a devolução da Amazon casa por ele
    (anexo,) = list(enviado.iter_attachments())
    assert anexo.get_filename() == "NF_2009.pdf"
    assert anexo.get_content().startswith(b"%PDF-")
    assert "Segue a nota fiscal" in enviado.get_body(("plain",)).get_content()


async def test_varios_anexos_vao_no_mesmo_email(
    db, make_user, ligado, caixa_configurada, monkeypatch
):
    smtp = _instalar_smtp(monkeypatch, SmtpFalso())
    conversa, user = await _cena(db, make_user)
    outro = foto.validar_foto(
        danfe.gerar_pdf(_xml(chave=_chave(2010), numero="2010")), "NF_2010.pdf"
    )
    m = await enviar.enviar_foto(db, conversa, [_pdf(), outro], legenda="Seguem.", user=user)
    assert m.status == "enviada", m.erro
    assert [a.get_filename() for a in _enviado(smtp).iter_attachments()] == [
        "NF_2009.pdf",
        "NF_2010.pdf",
    ]


async def test_sem_texto_nada_sai(db, make_user, ligado, caixa_configurada, monkeypatch):
    smtp = _instalar_smtp(monkeypatch, SmtpFalso())
    conversa, user = await _cena(db, make_user)
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_foto(db, conversa, _pdf(), legenda=" ", user=user)
    assert e.value.code == enviar.RECUSA_TEXTO_INVALIDO
    assert smtp.chamadas == []


@pytest.mark.parametrize(
    ("smtp", "status"),
    [
        # O servidor recusou: não saiu.
        (lambda: SmtpFalso(data=(554, b"5.7.1 rejected")), "falhou"),
        # Caiu no meio do DATA: pode ter saído, nunca se reenvia sozinho.
        (lambda: SmtpFalso(erro_em="data", erro=smtplib.SMTPServerDisconnected("x")), "revisar"),
    ],
)
async def test_a_regua_do_resultado_e_a_do_texto(
    db, make_user, ligado, caixa_configurada, monkeypatch, smtp, status
):
    _instalar_smtp(monkeypatch, smtp())
    conversa, user = await _cena(db, make_user)
    m = await enviar.enviar_foto(db, conversa, _pdf(), legenda="Segue.", user=user)
    assert m.status == status


# ─────────────── a NF pelo pedido correlato ───────────────


async def test_nf_vem_do_pedido_correlato_no_bling(db, make_user):
    conversa, _ = await _cena(db, make_user)
    nf = await danfe.nf_da_conversa(db, conversa)
    assert (nf.numero, nf.pedido_bling, nf.chave) == ("2009", PEDIDO_BLING, _chave(2009))


async def test_pedido_digitado_pelo_comprador_nao_tira_a_nf(db, make_user):
    conversa, _ = await _cena(db, make_user)
    conversa.dados = {**conversa.dados, amazon_email.CHAVE_PEDIDO_DO_TEXTO: True}
    await db.commit()
    with pytest.raises(danfe.NfIndisponivel) as e:
        await danfe.nf_da_conversa(db, conversa)
    assert e.value.code == "nf_pedido_do_texto" and "Seller Central" in e.value.detail


async def test_rota_e_painel(
    db, client, make_user, auth_as, ligado, caixa_configurada, monkeypatch
):
    smtp = _instalar_smtp(monkeypatch, SmtpFalso())
    conversa, _ = await _cena(db, make_user)
    auth_as(await make_user(role=UserRole.ADMIN))

    s = await painel.situacao_da_foto(db, conversa)
    assert (s["pode"], s["pdf"], s["nf_numero"]) == (True, "arquivo", "2009")
    assert s["legenda_obrigatoria"] is True
    assert s["max_anexos"] == foto.MAX_ANEXOS_NUMA_MENSAGEM

    r = await client.get(f"{URL}/conversas/{conversa.id}/nf")
    assert r.status_code == 200, r.text
    assert r.headers["x-nf-numero"] == "2009" and r.headers["x-nf-chave"] == _chave(2009)

    r = await client.post(
        f"{URL}/conversas/{conversa.id}/foto",
        files={"arquivo": ("NF_2009.pdf", r.content, "application/pdf")},
        data={"legenda": "Segue a nota fiscal do seu pedido.", "nf_chave": _chave(2009)},
    )
    assert r.status_code == 200, r.text
    m = r.json()["mensagem"]
    assert (m["status"], m["tipo"]) == ("enviada", "arquivo")
    assert m["anexos"][0]["nome"] == "NF_2009.pdf"
    assert [a.get_filename() for a in _enviado(smtp).iter_attachments()] == ["NF_2009.pdf"]


# ─────────────── onde NÃO vale ───────────────


def test_email_informativo_sem_comprador_nao_recebe_anexo():
    conversa = SimpleNamespace(plataforma="amazon", canal="email", comprador_id=None)
    assert "não veio de um comprador" in foto.motivo_sem_foto(conversa)
    assert foto.max_anexos(conversa) == 0
    assert foto.como_vai_o_pdf(conversa) is None


def test_nada_sai_sozinho_na_amazon():
    """O agente oferece a NF no cartão da sugestão; quem manda é a pessoa."""
    assert "amazon" in PLATAFORMAS_SEM_AUTO
    assert not any(plataforma == "amazon" for plataforma, _ in nf_automatica.CAIXAS)
