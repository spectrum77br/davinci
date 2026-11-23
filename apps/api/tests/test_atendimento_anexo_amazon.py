# ruff: noqa: S105, S106, F401, F811  (senha de caixa falsa; fixtures do teste do leitor)
"""A foto/PDF que o comprador manda na Amazon (por e-mail) APARECE na conversa (23/11/2026).

O Eduardo, com o print da Poofy: "a amazon as imagens vem como image.jpg mas não
ela em si [...] tem que fazer aparecer as imagens em atendimento". A foto vem
DENTRO do e-mail do Seller Central (parte MIME `attachment`, base64 — o formato
conferido em 23/11/2026 nos 15 e-mails com anexo da caixa); o leitor guardava
só o nome. O que estes testes seguram (`services/atendimento/anexo_amazon.py`):

- o leitor guarda a cópia da foto/PDF no disco com o nome gerado AQUI (sha256 +
  a extensão do tipo de VERDADE) e o anexo ganha `arquivo`, `tipo` (o mime) e
  `tamanho`; o "image/jpg" que a Amazon declara vira "image/jpeg"; várias
  fotos na mesma mensagem; o PDF; o agente passa a ver "[com foto]";
- HTML com nome de foto, HEIC, TXT, acima do teto, além do máximo por e-mail:
  fica só o nome (como antes); disco que falha: o e-mail entra do mesmo jeito;
- o mesmo e-mail relido grava o MESMO arquivo (sem cópia repetida);
- a rota da conversa (a do anexo do ML) entrega a cópia: `inline` a foto,
  `attachment` o PDF, `nosniff`, `sandbox`, `private`; só o `arquivo` de uma
  mensagem DESTA conversa; formato estranho, nota interna, arquivo sumido ou
  trocado no disco → 404; login, `pode_ver` e o escopo da equipe;
- os que já tinham chegado (só o nome): `recuperar_antigos` relê pelo UID
  (EXAMINE + BODY.PEEK[]); seco só conta e não grava nada; aplicar completa;
  outro e-mail no UID, anexos diferentes ou UID sumido → a mensagem fica como está;
- nada do comprador no log (nome do arquivo, texto).

Nada aqui abre rede: a caixa IMAP e o Redis são os falsos do teste do leitor.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from app.config import get_settings
from app.deps.team_scope import TeamScope
from app.models import AtendimentoMensagem, Integration, IntegrationPlatform, UserRole
from app.routers import atendimento as rota_atendimento
from app.security.cipher import encrypt_json
from app.services.atendimento import amazon_email, anexo_amazon, gravar
from app.services.atendimento.agente import acao_pessoa
from app.services.atendimento.constantes import AUTOR_CLIENTE, AUTOR_EQUIPE, ORIGEM_NOTA, TIPO_NOTA
from tests.test_atendimento_amazon_email import (
    PEDIDO_KFA,
    RELAY_MARIA,
    CaixaFalsa,
    _conta,
    _conversas,
    _instalar_caixa,
    _mensagens,
    caixa_configurada,
    redis_falso,
)

URL = "/api/atendimento"
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"1" * 300
JPG_2 = b"\xff\xd8\xff\xe1\x00\x10Exif\x00" + b"2" * 280
PNG = b"\x89PNG\r\n\x1a\n" + b"3" * 120
PDF = b"%PDF-1.4\n" + b"4" * 90
HEIC = b"\x00\x00\x00\x18ftypheic" + b"5" * 60
HTML = b"<html><script>alert(1)</script></html>"
TEXTO = "Eu preciso das 3 do mesmo tamanho"
MID = "<anexo1@amazon.com.br>"


def _arquivo(dados: bytes, ext: str) -> str:
    return f"{hashlib.sha256(dados).hexdigest()}{ext}"


def _email(
    anexos: list[tuple[str, bytes, str]],
    *,
    texto: str | None = TEXTO,
    message_id: str = MID,
) -> bytes:
    """O e-mail do comprador como a Amazon manda: o modelo em volta e a foto em anexo."""
    msg = EmailMessage()
    msg["From"] = f'"Larissa Teste - Amazon Marketplace" <{RELAY_MARIA}>'
    msg["To"] = "atendimento+kfa@loja-teste.com.br"
    msg["Subject"] = f"Pergunta sobre o pedido {PEDIDO_KFA}"
    msg["Date"] = "Fri, 21 Nov 2026 14:52:00 -0300"
    msg["Message-ID"] = message_id
    corpo = (
        "Você recebeu uma mensagem.\n\n"
        "------------- Mensagem: -------------\n\n"
        f"{texto or ''}\n\n"
        "------------- Fim da mensagem -------------\n"
    )
    msg.set_content(corpo)
    msg.add_alternative(f"<p>{texto or ''}</p>", subtype="html")
    for nome, dados, tipo in anexos:
        principal, sub = tipo.split("/")
        msg.add_attachment(dados, maintype=principal, subtype=sub, filename=nome)
    return msg.as_bytes()


@pytest.fixture(autouse=True)
def _disco(tmp_path, monkeypatch) -> Path:
    """O `uploads_dir` é uma pasta temporária; o atendimento sem lista de pessoas."""
    s = get_settings()
    monkeypatch.setattr(s, "uploads_dir", str(tmp_path))
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    return tmp_path


def _no_disco(tmp_path: Path, arquivo: str) -> Path:
    return tmp_path / "atendimento" / "amazon" / arquivo[:2] / arquivo


# ─────────────── o e-mail → o anexo da mensagem (puro) ───────────────


def test_foto_do_email_vira_anexo_com_a_copia_e_o_tipo_de_verdade():
    em = amazon_email.interpretar_email(_email([("image.jpg", JPG, "image/jpg")]))
    assert em.texto == TEXTO
    arquivo = _arquivo(JPG, ".jpg")
    # O "image/jpg" que a Amazon declara vira o tipo de verdade (pela assinatura).
    assert em.anexos == [
        {"tipo": "image/jpeg", "nome": "image.jpg", "arquivo": arquivo, "tamanho": len(JPG)}
    ]
    [copia] = em.copias
    assert (copia.arquivo, copia.mime, copia.dados) == (arquivo, "image/jpeg", JPG)
    # Os bytes nunca aparecem no repr (log, depuração).
    assert "JFIF" not in repr(em) and "JFIF" not in repr(copia)


def test_varias_fotos_e_o_pdf_na_mesma_mensagem():
    em = amazon_email.interpretar_email(
        _email(
            [
                ("image.jpg", JPG, "image/jpeg"),
                ("image1.jpg", JPG_2, "image/jpeg"),
                ("print.png", PNG, "image/png"),
                ("nota fiscal.pdf", PDF, "application/pdf"),
            ]
        )
    )
    assert [(a["tipo"], a["nome"], a["arquivo"]) for a in em.anexos] == [
        ("image/jpeg", "image.jpg", _arquivo(JPG, ".jpg")),
        ("image/jpeg", "image1.jpg", _arquivo(JPG_2, ".jpg")),
        ("image/png", "print.png", _arquivo(PNG, ".png")),
        ("application/pdf", "nota fiscal.pdf", _arquivo(PDF, ".pdf")),
    ]
    assert len(em.copias) == 4


def test_o_que_nao_e_foto_nem_pdf_fica_so_com_o_nome(monkeypatch):
    em = amazon_email.interpretar_email(
        _email(
            [
                # HTML com nome e tipo de foto: a assinatura manda.
                ("foto.jpg", HTML, "image/jpeg"),
                # HEIC: o navegador não mostra (o ML baixa; aqui fica o nome).
                ("IMG_0001.HEIC", HEIC, "image/heic"),
                ("leia.txt", b"so texto", "text/plain"),
                ("vazio.png", b"", "image/png"),
            ]
        )
    )
    assert em.anexos == [
        {"tipo": "arquivo", "nome": "foto.jpg"},
        {"tipo": "arquivo", "nome": "IMG_0001.HEIC"},
        {"tipo": "arquivo", "nome": "leia.txt"},
        {"tipo": "arquivo", "nome": "vazio.png"},
    ]
    assert em.copias == []
    # Acima do teto: só o nome.
    monkeypatch.setattr(anexo_amazon, "MAX_BYTES", len(JPG) - 1)
    grande = amazon_email.interpretar_email(_email([("image.jpg", JPG, "image/jpeg")]))
    assert grande.anexos == [{"tipo": "arquivo", "nome": "image.jpg"}]
    assert grande.copias == []


def test_anexo_sem_nome_fica_anexo_como_a_leitura_sempre_gravou():
    """O nome é o que a recuperação dos antigos confere: sem nome, "anexo" (como antes)."""
    msg = EmailMessage()
    msg["From"] = f"<{RELAY_MARIA}>"
    msg["To"] = "atendimento+kfa@loja-teste.com.br"
    msg["Subject"] = f"Pergunta sobre o pedido {PEDIDO_KFA}"
    msg["Message-ID"] = MID
    msg.set_content("Segue foto")
    msg.add_attachment(JPG, maintype="image", subtype="jpeg")
    em = amazon_email.interpretar_email(msg.as_bytes())
    assert em.anexos == [
        {
            "tipo": "image/jpeg",
            "nome": "anexo",
            "arquivo": _arquivo(JPG, ".jpg"),
            "tamanho": len(JPG),
        }
    ]


def test_alem_do_maximo_por_email_so_o_nome(monkeypatch):
    monkeypatch.setattr(anexo_amazon, "MAX_POR_EMAIL", 2)
    em = amazon_email.interpretar_email(
        _email(
            [
                ("a.jpg", JPG, "image/jpeg"),
                ("b.png", PNG, "image/png"),
                ("c.pdf", PDF, "application/pdf"),
            ]
        )
    )
    assert [a.get("arquivo") is not None for a in em.anexos] == [True, True, False]
    assert em.anexos[2] == {"tipo": "arquivo", "nome": "c.pdf"}


def test_o_agente_ve_com_foto_como_no_ml():
    em = amazon_email.interpretar_email(_email([("image.jpg", JPG, "image/jpg")]))
    m = AtendimentoMensagem(tipo="texto", texto=em.texto, anexos=em.anexos)
    assert acao_pessoa.midia_da_mensagem(m) == "foto"


def test_arquivo_valido_so_o_nome_que_o_modulo_gera():
    assert anexo_amazon.arquivo_valido(_arquivo(JPG, ".jpg"))
    assert anexo_amazon.arquivo_valido(_arquivo(PDF, ".pdf"))
    for ruim in (
        "image.jpg",
        _arquivo(JPG, ".exe"),
        _arquivo(JPG, ".jpg").upper(),
        "../" + _arquivo(JPG, ".jpg"),
        _arquivo(JPG, ".jpg") + "/x",
        "a" * 63 + ".jpg",
        None,
        123,
    ):
        assert not anexo_amazon.arquivo_valido(ruim), ruim
        assert anexo_amazon.caminho(ruim) is None, ruim


# ─────────────── o leitor grava a cópia (e-mail → disco → mensagem) ───────────────


async def test_leitor_guarda_a_foto_no_disco_e_a_mensagem_aponta_para_ela(
    db,
    make_user,
    caixa_configurada,
    redis_falso,
    monkeypatch,
    _disco,
):
    bruto = _email([("image.jpg", JPG, "image/jpg"), ("image1.jpg", JPG_2, "image/jpg")])
    caixa = _instalar_caixa(monkeypatch, CaixaFalsa({255: bruto}))
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    with capture_logs() as logs:
        r = await amazon_email.sincronizar(db, canal, kfa, None)
        await db.commit()
    assert (r.status, r.mensagens_novas) == ("ok", 1)
    [conversa] = await _conversas(db)
    [m] = await _mensagens(db, conversa.id)
    assert m.texto == TEXTO and m.tipo == "texto"
    assert m.anexos == [
        {
            "tipo": "image/jpeg",
            "nome": "image.jpg",
            "arquivo": _arquivo(JPG, ".jpg"),
            "tamanho": len(JPG),
        },
        {
            "tipo": "image/jpeg",
            "nome": "image1.jpg",
            "arquivo": _arquivo(JPG_2, ".jpg"),
            "tamanho": len(JPG_2),
        },
    ]
    assert _no_disco(_disco, _arquivo(JPG, ".jpg")).read_bytes() == JPG
    assert _no_disco(_disco, _arquivo(JPG_2, ".jpg")).read_bytes() == JPG_2
    # Nenhuma sobra de gravação pela metade.
    assert not list(_disco.rglob(f"*{anexo_amazon.SUFIXO_TEMP}"))
    # A caixa continua só leitura (EXAMINE; o falso barra FETCH sem PEEK).
    assert caixa.comandos("select") == [("select", '"INBOX"', True)]
    # Nada do comprador no log: nem o nome do arquivo, nem o texto.
    assert "image1.jpg" not in repr(logs) and TEXTO not in repr(logs)


async def test_disco_que_falha_nao_segura_o_email(
    db,
    make_user,
    caixa_configurada,
    redis_falso,
    monkeypatch,
    tmp_path,
):
    # O `uploads_dir` é um ARQUIVO: a pasta da cópia não pode ser criada.
    bloqueio = tmp_path / "nao-e-pasta"
    bloqueio.write_bytes(b"x")
    monkeypatch.setattr(get_settings(), "uploads_dir", str(bloqueio))
    _instalar_caixa(monkeypatch, CaixaFalsa({255: _email([("image.jpg", JPG, "image/jpeg")])}))
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    with capture_logs() as logs:
        r = await amazon_email.sincronizar(db, canal, kfa, None)
        await db.commit()
    assert (r.status, r.mensagens_novas) == ("ok", 1)
    [conversa] = await _conversas(db)
    [m] = await _mensagens(db, conversa.id)
    assert m.texto == TEXTO
    assert m.anexos == [{"tipo": "arquivo", "nome": "image.jpg"}]
    assert any(e.get("event") == "atendimento_amazon_anexo_disco_falhou" for e in logs)


async def test_o_mesmo_arquivo_gravado_de_novo_nao_repete(_disco):
    em = amazon_email.interpretar_email(_email([("image.jpg", JPG, "image/jpeg")]))
    primeira = await anexo_amazon.guardar(em.anexos, em.copias)
    segunda = await anexo_amazon.guardar(em.anexos, em.copias)
    assert primeira == segunda == em.anexos
    assert [p.name for p in _disco.rglob("*") if p.is_file()] == [_arquivo(JPG, ".jpg")]


# ─────────────── a rota da conversa entrega a cópia ───────────────


async def _loja(db, user, nome: str = "kfa") -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.AMAZON,
        name=nome,
        credentials=encrypt_json({"refresh_token": "rt"}),
    )
    db.add(integ)
    await db.flush()
    return integ


async def _conversa_amazon(
    db,
    integ,
    *,
    externo_id: str = f"{RELAY_MARIA}|{PEDIDO_KFA}",
    anexos: list[dict],
    plataforma: str = "amazon",
    canal: str = "email",
):
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal,
        externo_id=externo_id,
        comprador_id=RELAY_MARIA,
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"<m-{externo_id}@amazon.com.br>",
        autor=AUTOR_CLIENTE,
        texto=TEXTO,
        tipo="texto",
        anexos=anexos,
        enviada_em=datetime.now(UTC) - timedelta(minutes=5),
    )
    await db.commit()
    return conversa


async def _com_foto(db, integ, dados: bytes = JPG, nome: str = "image.jpg", **kw):
    em = amazon_email.interpretar_email(_email([(nome, dados, "image/jpeg")]))
    anexos = await anexo_amazon.guardar(em.anexos, em.copias)
    return await _conversa_amazon(db, integ, anexos=anexos, **kw), anexos[0]["arquivo"]


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


def _rota(conversa, arquivo: str) -> str:
    return f"{URL}/conversas/{conversa.id}/anexos/{arquivo}"


async def test_rota_entrega_a_foto_inline_e_o_pdf_para_baixar(db, client, admin):
    integ = await _loja(db, admin)
    conversa, arquivo = await _com_foto(db, integ)
    r = await client.get(_rota(conversa, arquivo))
    assert r.status_code == 200, r.text
    assert r.content == JPG
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith("inline;")
    assert 'filename="image.jpg"' in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["cache-control"].startswith("private")

    em = amazon_email.interpretar_email(_email([("nota fiscal.pdf", PDF, "application/pdf")]))
    anexos = await anexo_amazon.guardar(em.anexos, em.copias)
    com_pdf = await _conversa_amazon(db, integ, externo_id="outro|pdf", anexos=anexos)
    r = await client.get(_rota(com_pdf, anexos[0]["arquivo"]))
    assert r.status_code == 200, r.text
    assert r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"


async def test_so_o_arquivo_desta_conversa_e_o_formato_da_rota(db, client, admin):
    integ = await _loja(db, admin)
    com_foto, arquivo = await _com_foto(db, integ)
    outra, outro_arquivo = await _com_foto(db, integ, dados=PNG, nome="print.png", externo_id="b|2")
    # O arquivo é da OUTRA conversa (e existe no disco): 404.
    r = await client.get(_rota(outra, arquivo))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "anexo_nao_encontrado"
    assert (await client.get(_rota(com_foto, outro_arquivo))).status_code == 404
    # Inventado, com outra extensão, maiúsculo, com `..`.
    for ruim in (
        _arquivo(b"nunca existiu", ".jpg"),
        arquivo.replace(".jpg", ".png"),
        arquivo.upper(),
        "a..b.jpg",
        "image.jpg",
    ):
        assert (await client.get(_rota(com_foto, ruim))).status_code == 404, ruim
    # O da própria conversa: vai.
    assert (await client.get(_rota(com_foto, arquivo))).status_code == 200


async def test_nota_interna_com_o_arquivo_nao_abre(db, client, admin):
    integ = await _loja(db, admin)
    _conversa, arquivo = await _com_foto(db, integ)
    sem_foto = await _conversa_amazon(
        db, integ, externo_id="c|3", anexos=[{"tipo": "arquivo", "nome": "x.jpg"}]
    )
    db.add(
        AtendimentoMensagem(
            conversa_id=sem_foto.id,
            externo_id=None,
            autor=AUTOR_EQUIPE,
            origem=ORIGEM_NOTA,
            tipo=TIPO_NOTA,
            texto="olha a foto",
            anexos=[{"arquivo": arquivo, "tipo": "image/jpeg"}],
            status="enviada",
        )
    )
    await db.commit()
    assert (await client.get(_rota(sem_foto, arquivo))).status_code == 404


async def test_fora_da_amazon_por_email_nao_le_o_disco(db, client, admin):
    integ = await _loja(db, admin)
    em = amazon_email.interpretar_email(_email([("image.jpg", JPG, "image/jpeg")]))
    anexos = await anexo_amazon.guardar(em.anexos, em.copias)
    # Uma Shopee com o mesmo anexo gravado (não acontece; se acontecesse, não serve o disco).
    shopee = await _conversa_amazon(
        db, integ, externo_id="s|1", anexos=anexos, plataforma="shopee", canal="chat"
    )
    assert (await client.get(_rota(shopee, anexos[0]["arquivo"]))).status_code == 404


async def test_arquivo_sumido_ou_trocado_no_disco_e_404(db, client, admin, _disco):
    integ = await _loja(db, admin)
    conversa, arquivo = await _com_foto(db, integ)
    alvo = _no_disco(_disco, arquivo)
    # Trocado no disco (outros bytes com o mesmo nome): não sai.
    alvo.write_bytes(JPG_2)
    with capture_logs() as logs:
        assert (await client.get(_rota(conversa, arquivo))).status_code == 404
    assert any(e.get("event") == "atendimento_amazon_anexo_nao_confere" for e in logs)
    # Sumido: 404 (a tela volta ao nome).
    alvo.unlink()
    assert (await client.get(_rota(conversa, arquivo))).status_code == 404


async def test_travas_de_quem_le_a_conversa(db, client, make_user, auth_as, monkeypatch):
    dono = await make_user(role=UserRole.ADMIN)
    da_equipe = await _loja(db, dono, "loja-da-equipe")
    de_fora = await _loja(db, dono, "loja-de-fora")
    minha, arquivo = await _com_foto(db, da_equipe)
    alheia, arquivo_alheio = await _com_foto(db, de_fora, dados=PNG, nome="p.png", externo_id="d|4")
    # Sem login: 401.
    assert (await client.get(_rota(minha, arquivo))).status_code == 401
    # Quem não vê o atendimento: 403.
    operador = await make_user(role=UserRole.USER)
    operador.stock_tags = ["estoque"]
    await db.commit()
    auth_as(operador)
    r = await client.get(_rota(minha, arquivo))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "atendimento_restrito"
    # A equipe só vê a conversa da loja dela.
    ids = {da_equipe.id}

    async def escopo(session, user):
        return TeamScope(unrestricted=False, integration_ids=set(ids), email_user=user)

    monkeypatch.setattr(rota_atendimento, "escopo_do_atendimento", escopo)
    vendedora = await make_user(role=UserRole.USER)
    auth_as(vendedora)
    r = await client.get(_rota(alheia, arquivo_alheio))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conversa_nao_encontrada"
    assert (await client.get(_rota(minha, arquivo))).status_code == 200


# ─────────────── os que já tinham chegado (só o nome) ───────────────


async def _antiga(
    db, integ, *, uid: int | None = 255, message_id: str = MID, anexos: list[dict] | None = None
):
    """A mensagem como o leitor gravava até 23/11/2026: só o nome do anexo."""
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=integ,
        plataforma="amazon",
        canal_nome="email",
        externo_id=f"{RELAY_MARIA}|{message_id}",
        comprador_id=RELAY_MARIA,
    )
    m, _nova = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=message_id,
        autor=AUTOR_CLIENTE,
        texto=TEXTO,
        tipo="texto",
        anexos=anexos if anexos is not None else [{"tipo": "arquivo", "nome": "image.jpg"}],
        enviada_em=datetime.now(UTC) - timedelta(days=2),
        payload={"imap_uid": uid, "conta_por": "tag"} if uid is not None else {"conta_por": "tag"},
    )
    await db.commit()
    return m


async def _anexos_de(db, m) -> list:
    await db.refresh(m)
    return m.anexos


async def test_recuperar_antigos_seco_so_conta_e_aplicar_completa(
    db,
    make_user,
    caixa_configurada,
    monkeypatch,
    _disco,
):
    caixa = _instalar_caixa(
        monkeypatch, CaixaFalsa({255: _email([("image.jpg", JPG, "image/jpg")])})
    )
    user = await make_user()
    integ = await _loja(db, user)
    m = await _antiga(db, integ)
    # Já com a cópia: nem entra na conta.
    await _conversa_amazon(
        db,
        integ,
        externo_id="ja|ok",
        anexos=[
            {"tipo": "image/png", "nome": "p.png", "arquivo": _arquivo(PNG, ".png"), "tamanho": 1}
        ],
    )

    seco = await anexo_amazon.recuperar_antigos(db)
    assert seco["aplicar"] is False
    assert (seco["mensagens"], seco["recuperadas"], seco["anexos_guardados"]) == (1, 1, 1)
    assert await _anexos_de(db, m) == [{"tipo": "arquivo", "nome": "image.jpg"}]
    assert not [p for p in _disco.rglob("*") if p.is_file()], "seco não grava no disco"
    # Só leitura na caixa: EXAMINE e o FETCH só com PEEK (o falso barra o resto).
    assert caixa.comandos("select") == [("select", '"INBOX"', True)]
    assert [c[1:3] for c in caixa.comandos("uid")] == [("FETCH", "255")]

    with capture_logs() as logs:
        feito = await anexo_amazon.recuperar_antigos(db, aplicar=True)
    assert (feito["recuperadas"], feito["anexos_guardados"], feito["disco_falhou"]) == (1, 1, 0)
    assert await _anexos_de(db, m) == [
        {
            "tipo": "image/jpeg",
            "nome": "image.jpg",
            "arquivo": _arquivo(JPG, ".jpg"),
            "tamanho": len(JPG),
        }
    ]
    assert _no_disco(_disco, _arquivo(JPG, ".jpg")).read_bytes() == JPG
    assert "image.jpg" not in repr(logs) and TEXTO not in repr(logs)
    # Rodar de novo não acha mais nada.
    assert (await anexo_amazon.recuperar_antigos(db, aplicar=True))["mensagens"] == 0


async def test_recuperar_antigos_so_com_o_mesmo_email_e_os_mesmos_anexos(
    db,
    make_user,
    caixa_configurada,
    monkeypatch,
    _disco,
):
    _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                # O UID 255 agora é OUTRO e-mail (Message-ID diferente).
                255: _email([("image.jpg", JPG, "image/jpeg")], message_id="<outro@amazon.com.br>"),
                # O 256 tem outros anexos (o nome não bate).
                256: _email([("foto.png", PNG, "image/png")], message_id="<m256@amazon.com.br>"),
            }
        ),
    )
    user = await make_user()
    integ = await _loja(db, user)
    outro = await _antiga(db, integ, uid=255)
    diferente = await _antiga(db, integ, uid=256, message_id="<m256@amazon.com.br>")
    sumido = await _antiga(db, integ, uid=999, message_id="<m999@amazon.com.br>")
    sem_uid = await _antiga(db, integ, uid=None, message_id="<msem@amazon.com.br>")
    r = await anexo_amazon.recuperar_antigos(db, aplicar=True)
    assert (r["mensagens"], r["recuperadas"]) == (4, 0)
    assert (r["outro_email"], r["anexos_diferentes"], r["email_nao_achado"], r["sem_uid"]) == (
        1,
        1,
        1,
        1,
    )
    for m in (outro, diferente, sumido, sem_uid):
        assert await _anexos_de(db, m) == [{"tipo": "arquivo", "nome": "image.jpg"}]
    assert not [p for p in _disco.rglob("*") if p.is_file()]
