# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""A MÍDIA do Zap (WhatsApp Cloud API, RF10, Onda 3) — docs/zap/desenho.md §5.5, §6.8, §10.4.

A Graph API e a CDN da Meta são de MENTIRA (`tests/zap_apoio.GraphFalso`,
`httpx.MockTransport`): nenhum teste fala com a Meta, e a chamada não
prevista reprova. O disco é uma pasta temporária (`uploads_dir`).

O que se garante (27/10/2026):
- o BAIXADOR (§5.5): `GET /{media_id}` com o token no header → a URL da CDN
  → download com o Bearer SÓ para host da lista (`lookaside.fbsbx.com`,
  `*.fbcdn.net`, `graph.facebook.com`), conferido A CADA redirecionamento
  (`follow_redirects=False`, no máximo 3 saltos); teto de tamanho (antes e
  durante o download, sem sobrar arquivo pela metade); `sha256` da Meta em
  hex OU base64; o TIPO pela assinatura do arquivo (o HTML que diz ser JPEG
  é recusado; o PNG declarado como octet-stream passa); o caminho gerado
  pelo servidor; a URL vencida (404) pede a URL de novo; erro vira
  `pendente` com backoff (1 → 5 → 30 min → 2 h → 12 h) até o prazo, e
  depois `expirada`; duas rodadas não baixam a mesma mídia; chave de
  leitura desligada não baixa; token nunca no log nem no `erro`;
- a ROTA `GET /api/atendimento/zap/midia/{id}`: só admin (quem não é leva
  404, como a conversa do Zap), `nosniff`, `Content-Security-Policy:
  sandbox`, `inline` só para imagem/áudio/vídeo, documento como
  `attachment` com nome seguro, sem path traversal, só o que está baixado;
- a FOTO de saída (§6.8): `POST /{pnid}/media` (multipart) e a mensagem
  `type=image` com a legenda OPCIONAL (até 1024, pelo validador), pelas
  MESMAS travas do texto (chave, número pausado, janela, lista de teste,
  só admin), sem `cliente_indisponivel`; a nossa cópia no disco e a URL
  RELATIVA no anexo; upload que falha = nada saiu; mensagem sem resposta =
  `revisar`; o efeito da Meta (368) pausa o número; o painel do Zap libera o
  botão de foto com a legenda opcional;
- a retenção de 180 dias registrada (`apagar_em`) e a limpeza (Onda 4)
  apagando o arquivo e deixando o registro "apagada";
- a fila: o `varrer` enfileira as pendentes vencidas (um job por tentativa)
  e o worker mínimo do localhost (`WorkerSettingsZap`) tem só o Zap;
- o script local: `simular --media-id`, a cópia de um arquivo local para o
  fictício e o `subir-midia` (só com o app de TESTE).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import structlog
from sqlalchemy import select, update

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    AtendimentoZapConta,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
    User,
    UserRole,
)
from app.routers import atendimento as rota
from app.services.atendimento import (
    canais_externos,
    clientes,
    enviar,
    foto,
    zap_entrada,
    zap_midia,
)
from app.services.atendimento.enviar import EnvioRecusado
from tests.zap_apoio import (  # noqa: F401  (fixtures: zap, graph, envio)
    PNID,
    WA_ID,
    cadastrar,
    conversa_do_numero,
    corpo,
    envio,
    graph,
    gravar_corpo,
    processar,
    zap,
)

TOKEN = "token-de-mentira"  # o do `zap_apoio.cadastrar` (cifrado no banco)
URL = "/api/atendimento"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
V = "v26.0"
CDN = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=MID1&ext=1&hash=abc"
CAMINHO_CDN = "/whatsapp_business/attachments/"

JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"j" * 300
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x10" * 2 + b"p" * 200
WEBP = b"RIFF\x00\x01\x00\x00WEBPVP8 " + b"w" * 200
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"m" * 200
GP3 = b"\x00\x00\x00\x14ftyp3gp4\x00\x00\x00\x00" + b"g" * 200
M4A = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00" + b"a" * 200
OGG = b"OggS\x00\x02" + b"o" * 200
AMR = b"#!AMR\n" + b"r" * 200
MP3 = b"ID3\x04\x00\x00" + b"3" * 200
AAC = b"\xff\xf1\x50\x80" + b"c" * 200
PDF = b"%PDF-1.7\n" + b"d" * 300
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200
EXE = b"MZ\x90\x00" + b"e" * 200
HTML = b"<!DOCTYPE html><html><script>alert(1)</script></html>"
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'


def _zip(nomes: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in nomes:
            z.writestr(n, "x")
    return buf.getvalue()


DOCX = _zip(["[Content_Types].xml", "word/document.xml"])
XLSX = _zip(["[Content_Types].xml", "xl/workbook.xml"])
PPTX = _zip(["[Content_Types].xml", "ppt/presentation.xml"])
ZIP_COMUM = _zip(["leia.txt", "programa.exe"])


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture
def uploads(tmp_path, monkeypatch) -> Path:
    """O disco da mídia: uma pasta só deste teste (nunca o /data/uploads)."""
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def admin(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    return u


def _sha_hex(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _meta_da_midia(
    graph,
    media_id: str = "MID1",
    *,
    conteudo: bytes = JPG,
    url: str = CDN,
    mime: str = "image/jpeg",
    sha: str | None = None,
    tamanho: int | None = None,
) -> None:
    """O `GET /{media_id}` da Graph: a URL da CDN, o mime, o sha256 e o tamanho."""
    graph.quando(
        "GET",
        f"/{V}/{media_id}",
        lambda req: httpx.Response(
            200,
            json={
                "messaging_product": "whatsapp",
                "url": url,
                "mime_type": mime,
                "sha256": sha if sha is not None else _sha_hex(conteudo),
                "file_size": tamanho if tamanho is not None else len(conteudo),
                "id": media_id,
            },
        ),
    )


def _cdn(graph, conteudo: bytes = JPG, *, caminho: str = CAMINHO_CDN, status: int = 200) -> None:
    graph.quando(
        "GET",
        caminho,
        lambda req: httpx.Response(
            status, content=conteudo, headers={"Content-Type": "application/octet-stream"}
        ),
    )


async def _midia(db, media_id: str = "MID1") -> AtendimentoZapMidia:
    return (
        await db.execute(
            select(AtendimentoZapMidia)
            .where(AtendimentoZapMidia.media_id == media_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _mensagem(db, midia: AtendimentoZapMidia) -> AtendimentoMensagem:
    return await db.get(AtendimentoMensagem, midia.mensagem_id, populate_existing=True)


async def _baixar(midia_id: UUID) -> str:
    async with _db.SessionLocal() as s:
        return await zap_midia.baixar(s, midia_id)


async def _vencer(db, midia_id: UUID, **valores) -> None:
    """Põe a próxima tentativa no passado (e o que mais vier) — o relógio do teste."""
    valores.setdefault("proxima_em", datetime.now(UTC) - timedelta(seconds=1))
    await db.execute(
        update(AtendimentoZapMidia).where(AtendimentoZapMidia.id == midia_id).values(**valores)
    )
    await db.commit()


@pytest_asyncio.fixture
async def recebida(db, zap, graph, uploads):
    """O cliente mandou uma FOTO (media_id "MID1"): a mídia registrada `pendente`."""
    app, conta, numero = await cadastrar(db)
    await gravar_corpo(corpo("imagem", wamid="wamid.FOTO", media_id="MID1"), app=app)
    assert (await processar())["processados"] == 1
    m = await _midia(db)
    assert (m.status, m.direcao, m.tentativas) == ("pendente", "entrada", 0)
    return m


# ═══════════════════════ as réguas (puras) ═══════════════════════════════


def test_tipo_pela_assinatura_e_nao_pelo_nome_nem_pelo_mime_declarado():
    casos = {
        JPG: "image/jpeg",
        PNG: "image/png",
        WEBP: "image/webp",
        MP4: "video/mp4",
        GP3: "video/3gpp",
        M4A: "audio/mp4",
        OGG: "audio/ogg",
        AMR: "audio/amr",
        MP3: "audio/mpeg",
        AAC: "audio/aac",
        PDF: "application/pdf",
        DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    for dados, esperado in casos.items():
        # O declarado não manda: até "image/jpeg" para um PDF dá PDF.
        assert zap_midia.detectar_tipo(dados, declarado="image/jpeg") == esperado, esperado
    # O DOC/XLS/PPT antigo (OLE2) só com o declarado de um dos três; o MSI
    # (instalador, também OLE2) e o declarado vazio, não.
    assert zap_midia.detectar_tipo(OLE, declarado="application/msword") == "application/msword"
    assert zap_midia.detectar_tipo(OLE, declarado="application/vnd.ms-excel") == (
        "application/vnd.ms-excel"
    )
    assert zap_midia.detectar_tipo(OLE, declarado="application/x-msi") is None
    assert zap_midia.detectar_tipo(OLE, declarado=None) is None
    # Texto puro só declarado como texto, e sem marcação.
    assert zap_midia.detectar_tipo(b"pedido 123\nobrigado", declarado="text/plain") == "text/plain"
    assert zap_midia.detectar_tipo(b"pedido 123", declarado="application/pdf") is None
    assert zap_midia.detectar_tipo(b"oi <script>x</script>", declarado="text/plain") is None
    # NUNCA: HTML, SVG, executável, zip comum, vazio — nem declarados como JPEG.
    for proibido in (HTML, SVG, EXE, ZIP_COMUM, b"", b"\x00\x01\x02"):
        assert zap_midia.detectar_tipo(proibido, declarado="image/jpeg") is None
        assert zap_midia.detectar_tipo(proibido, declarado="text/plain") is None


def test_especie_e_inline_so_para_imagem_audio_e_video():
    assert zap_midia.eh_inline("image/jpeg") and zap_midia.eh_inline("audio/ogg")
    assert zap_midia.eh_inline("video/mp4")
    assert not zap_midia.eh_inline("application/pdf")
    assert not zap_midia.eh_inline("text/plain")
    assert not zap_midia.eh_inline("text/html")  # fora da lista: nunca inline
    assert "image/svg+xml" not in zap_midia.TIPOS_ACEITOS
    assert "text/html" not in zap_midia.TIPOS_ACEITOS


def test_bearer_so_para_host_da_lista():
    ok = (
        "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1",
        "https://scontent.xx.fbcdn.net/v/t61/abc.jpg",
        "https://graph.facebook.com/v26.0/123",
    )
    for url in ok:
        assert zap_midia.host_permitido(url), url
    nao = (
        "http://lookaside.fbsbx.com/x",  # sem TLS
        "https://lookaside.fbsbx.com.evil.com/x",
        "https://evilfbcdn.net/x",  # sufixo sem o ponto
        "https://fbcdn.net.evil.com/x",
        "https://lookaside.fbsbx.com@evil.com/x",  # usuário na URL
        "https://user:senha@lookaside.fbsbx.com/x",
        "https://lookaside.fbsbx.com:8443/x",  # porta estranha
        "ftp://lookaside.fbsbx.com/x",
        "//lookaside.fbsbx.com/x",
        "",
        "https://127.0.0.1/x",
    )
    for url in nao:
        assert not zap_midia.host_permitido(url), url


def test_sha256_da_meta_em_hex_ou_base64():
    digest = hashlib.sha256(JPG).digest()
    assert zap_midia.sha256_confere(digest.hex(), digest) is True
    assert zap_midia.sha256_confere(digest.hex().upper(), digest) is True
    assert zap_midia.sha256_confere(base64.b64encode(digest).decode(), digest) is True
    assert zap_midia.sha256_confere(_sha_hex(b"outro"), digest) is False
    assert zap_midia.sha256_confere(base64.b64encode(b"x" * 32).decode(), digest) is False
    # Formato que não é nenhum dos dois (os das fixtures): não dá para conferir.
    assert zap_midia.sha256_confere("Q1VTVE9NRVJfSU1BR0VfU0hBMjU2X0JBU0U2NA==", digest) is None
    assert zap_midia.sha256_confere(None, digest) is None


def test_caminho_gerado_pelo_servidor_e_sem_traversal(uploads):
    numero, midia = uuid4(), uuid4()
    quando = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    rel = zap_midia.caminho_relativo(numero, quando, midia, ".jpg")
    assert rel == f"atendimento/zap/{numero}/2026/10/{midia}.jpg"
    assert zap_midia.caminho_absoluto(rel) == uploads / rel
    for ruim in (
        "../../etc/passwd",
        "/etc/passwd",
        "atendimento/zap/../../segredo",
        "atendimento/outra/x.jpg",
        "",
        None,
    ):
        assert zap_midia.caminho_absoluto(ruim) is None, ruim


# ═══════════════════════ o baixador (§5.5) ═══════════════════════════════


async def test_baixa_com_o_bearer_e_guarda_a_copia(db, recebida, graph, uploads):
    _meta_da_midia(graph)
    _cdn(graph)
    with structlog.testing.capture_logs() as logs:
        assert await _baixar(recebida.id) == "baixada"
    m = await _midia(db)
    assert (m.status, m.mime_detectado, m.tamanho, m.tentativas) == (
        "baixada",
        "image/jpeg",
        len(JPG),
        1,
    )
    assert m.sha256_local == _sha_hex(JPG) and m.baixada_em is not None and m.erro is None
    assert m.caminho.startswith(f"atendimento/zap/{m.numero_ref}/") and m.caminho.endswith(
        f"/{m.id}.jpg"
    )
    assert (uploads / m.caminho).read_bytes() == JPG
    # Nada pela metade na pasta temporária.
    assert not list(uploads.rglob("*.parte"))
    # O balão: a URL RELATIVA da nossa cópia, o tipo de verdade e o tamanho.
    (anexo,) = (await _mensagem(db, m)).anexos
    assert anexo["url"] == f"/api/atendimento/zap/midia/{m.id}"
    assert (anexo["status"], anexo["mime"], anexo["tamanho"]) == (
        "baixada",
        "image/jpeg",
        len(JPG),
    )
    # 1º a Graph (o token no header), depois a CDN (o Bearer no header).
    graph_get, cdn_get = graph.chamadas
    assert graph_get.url.host == "graph.facebook.com" and graph_get.url.path == f"/{V}/MID1"
    assert cdn_get.url.host == "lookaside.fbsbx.com"
    assert cdn_get.headers["authorization"] == f"Bearer {TOKEN}"
    for req in graph.chamadas:
        assert TOKEN not in str(req.url)
    assert TOKEN not in repr(logs) and "lookaside" not in repr(logs)


async def test_sha256_em_base64_tambem_confere(db, recebida, graph, uploads):
    _meta_da_midia(graph, sha=base64.b64encode(hashlib.sha256(JPG).digest()).decode())
    _cdn(graph)
    assert await _baixar(recebida.id) == "baixada"


async def test_host_fora_da_lista_nunca_recebe_o_bearer(db, recebida, graph, uploads):
    _meta_da_midia(graph, url="https://evil.example.com/whatsapp_business/attachments/?mid=1")
    _cdn(graph)
    assert await _baixar(recebida.id) == "pendente"
    assert [r.url.host for r in graph.chamadas] == ["graph.facebook.com"]
    m = await _midia(db)
    assert m.status == "pendente" and "fora da lista" in (m.erro or "")
    assert m.proxima_em is not None and m.caminho is None


async def test_redirecionamento_conferido_a_cada_salto(db, recebida, graph, uploads):
    # Para outro host da lista: segue, com o Bearer.
    _meta_da_midia(graph)
    graph.quando(
        "GET",
        CAMINHO_CDN,
        lambda req: httpx.Response(
            302, headers={"Location": "https://scontent.xx.fbcdn.net/v/foto.jpg"}
        ),
    )
    _cdn(graph, caminho="/v/foto.jpg")
    assert await _baixar(recebida.id) == "baixada"
    assert [r.url.host for r in graph.chamadas] == [
        "graph.facebook.com",
        "lookaside.fbsbx.com",
        "scontent.xx.fbcdn.net",
    ]
    assert graph.chamadas[-1].headers["authorization"] == f"Bearer {TOKEN}"


async def test_redirecionamento_para_fora_da_lista_e_barrado(db, recebida, graph, uploads):
    _meta_da_midia(graph)
    graph.quando(
        "GET",
        CAMINHO_CDN,
        lambda req: httpx.Response(302, headers={"Location": "https://evil.example.com/pega"}),
    )
    assert await _baixar(recebida.id) == "pendente"
    assert "evil.example.com" not in [r.url.host for r in graph.chamadas]
    m = await _midia(db)
    assert "fora da lista" in (m.erro or "")


async def test_redirecionamentos_demais(db, recebida, graph, uploads):
    _meta_da_midia(graph)
    graph.quando(
        "GET",
        CAMINHO_CDN,
        lambda req: httpx.Response(302, headers={"Location": CDN}),
    )
    assert await _baixar(recebida.id) == "pendente"
    # 1 da Graph + o primeiro + 3 saltos, e para.
    assert len([r for r in graph.chamadas if r.url.host == "lookaside.fbsbx.com"]) == 4
    assert "redirecion" in ((await _midia(db)).erro or "")


async def test_url_vencida_pede_a_url_de_novo(db, recebida, graph, uploads):
    """A URL da CDN vale ~5 min: 404 no download = pede outra à Graph, na hora."""
    _meta_da_midia(graph)
    respostas = iter([httpx.Response(404), httpx.Response(200, content=JPG)])
    graph.quando("GET", CAMINHO_CDN, lambda req: next(respostas))
    assert await _baixar(recebida.id) == "baixada"
    caminhos = [r.url.path for r in graph.chamadas]
    assert caminhos == [f"/{V}/MID1", CAMINHO_CDN, f"/{V}/MID1", CAMINHO_CDN]


async def test_retentativa_com_backoff_e_prazo(db, recebida, graph, uploads):
    graph.quando("GET", f"/{V}/MID1", lambda req: httpx.Response(500, text="fora"))
    esperas = []
    for _ in range(6):
        antes = datetime.now(UTC)
        assert await _baixar(recebida.id) == "pendente"
        m = await _midia(db)
        esperas.append(round((m.proxima_em - antes).total_seconds() / 60))
        # Antes da hora, nada acontece (nem HTTP).
        n = len(graph.chamadas)
        assert await _baixar(recebida.id) == "ocupada"
        assert len(graph.chamadas) == n
        await _vencer(db, m.id)
    # 1 → 5 → 30 min → 2 h → 12 h (e fica em 12 h).
    assert esperas == [1, 5, 30, 120, 720, 720]
    assert (await _midia(db)).tentativas == 6
    # Passado o prazo (30 dias do registro), `expirada` — sem ir à Meta.
    await _vencer(db, recebida.id, created_at=datetime.now(UTC) - timedelta(days=31))
    n = len(graph.chamadas)
    assert await _baixar(recebida.id) == "expirada"
    assert len(graph.chamadas) == n
    m = await _midia(db)
    assert m.status == "expirada" and m.proxima_em is None
    (anexo,) = (await _mensagem(db, m)).anexos
    assert anexo["status"] == "expirada" and "url" not in anexo


async def test_midia_que_a_meta_ja_apagou_expira_depois_dos_7_dias(db, recebida, graph, uploads):
    """Depois do `expira_meta_em` (7 dias), a Meta dizendo que a mídia não existe = expirada."""
    graph.quando(
        "GET",
        f"/{V}/MID1",
        lambda req: httpx.Response(
            400,
            json={"error": {"message": "Object does not exist", "code": 100, "error_subcode": 33}},
        ),
    )
    # Dentro dos 7 dias: ainda tenta de novo (a Meta pode ter tropeçado).
    assert await _baixar(recebida.id) == "pendente"
    await _vencer(db, recebida.id, expira_meta_em=datetime.now(UTC) - timedelta(minutes=1))
    assert await _baixar(recebida.id) == "expirada"


async def test_grande_demais_pelo_tamanho_da_meta_nem_baixa(db, recebida, graph, uploads, zap):
    _meta_da_midia(graph, tamanho=33 * 1024 * 1024)
    _cdn(graph)
    assert await _baixar(recebida.id) == "grande_demais"
    assert [r.url.host for r in graph.chamadas] == ["graph.facebook.com"]
    m = await _midia(db)
    assert m.status == "grande_demais" and m.proxima_em is None and m.caminho is None
    (anexo,) = (await _mensagem(db, m)).anexos
    assert anexo["status"] == "grande_demais"


async def test_grande_demais_no_meio_do_download_nao_deixa_sobra(
    db, recebida, graph, uploads, zap, monkeypatch
):
    monkeypatch.setattr(zap.settings, "atendimento_zap_midia_max_mb", 1)
    grande = JPG + b"x" * (1024 * 1024)
    # A Meta não disse o tamanho (0) e a CDN manda em pedaços, sem
    # Content-Length: o teto vale DURANTE o download.
    _meta_da_midia(graph, conteudo=grande, tamanho=0)

    async def pedacos():
        yield grande[:600_000]
        yield grande[600_000:]

    graph.quando("GET", CAMINHO_CDN, lambda req: httpx.Response(200, content=pedacos()))
    assert await _baixar(recebida.id) == "grande_demais"
    assert [p for p in uploads.rglob("*") if p.is_file()] == []


async def test_hash_divergente_falha_e_nao_guarda(db, recebida, graph, uploads):
    _meta_da_midia(graph, sha=_sha_hex(b"outro arquivo"))
    _cdn(graph)
    assert await _baixar(recebida.id) == "falhou"
    m = await _midia(db)
    assert m.status == "falhou" and "sha256" in (m.erro or "") and m.caminho is None
    assert [p for p in uploads.rglob("*") if p.is_file()] == []


async def test_html_que_diz_ser_jpeg_e_recusado(db, recebida, graph, uploads):
    _meta_da_midia(graph, conteudo=HTML, mime="image/jpeg")
    _cdn(graph, HTML)
    assert await _baixar(recebida.id) == "falhou"
    m = await _midia(db)
    assert (m.status, m.erro) == ("falhou", "tipo de arquivo não aceito")
    assert [p for p in uploads.rglob("*") if p.is_file()] == []
    (anexo,) = (await _mensagem(db, m)).anexos
    assert anexo["status"] == "falhou" and "url" not in anexo


async def test_png_declarado_como_octet_stream_vale_pelo_conteudo(db, recebida, graph, uploads):
    _meta_da_midia(graph, conteudo=PNG, mime="application/octet-stream")
    _cdn(graph, PNG)
    assert await _baixar(recebida.id) == "baixada"
    m = await _midia(db)
    assert (m.mime, m.mime_detectado) == ("image/jpeg", "image/png")  # o declarado fica anotado
    assert m.caminho.endswith(".png")


async def test_leitura_desligada_nao_baixa(db, recebida, graph, uploads, zap, monkeypatch):
    monkeypatch.setattr(zap.settings, "atendimento_zap_leitura_ativa", False)
    assert await _baixar(recebida.id) == "desligado"
    assert graph.chamadas == []
    assert (await _midia(db)).status == "pendente"


async def test_sem_token_fica_pendente_sem_ir_a_meta(db, recebida, graph, uploads):
    await db.execute(update(AtendimentoZapConta).values(status="token_invalido"))
    await db.commit()
    assert await _baixar(recebida.id) == "pendente"
    assert graph.chamadas == []
    assert "token" in ((await _midia(db)).erro or "")


async def test_exclusao_lgpd_no_meio_do_download_nao_ressuscita_a_foto(
    db, recebida, graph, uploads, monkeypatch
):
    """A corrida da revisão de 27/10/2026: o cliente pede exclusão ENQUANTO o
    baixador fala com a Meta (mídia `baixando`, nenhuma trava aberta). O download
    que termina depois não pode voltar o arquivo, o status nem a URL do balão."""
    from app.models import AtendimentoZapContato
    from app.services.atendimento import zap_cadastro

    _meta_da_midia(graph)
    _cdn(graph)
    buscar_de_verdade = zap_midia._buscar

    async def _buscar_com_exclusao_no_meio(midia, token, teto):
        async with _db.SessionLocal() as outra:
            contato_id = await outra.scalar(
                select(AtendimentoZapContato.id).where(AtendimentoZapContato.wa_id == WA_ID)
            )
            await zap_cadastro.apagar_contato(outra, contato_id)
        return await buscar_de_verdade(midia, token, teto)

    monkeypatch.setattr(zap_midia, "_buscar", _buscar_com_exclusao_no_meio)
    assert await _baixar(recebida.id) == "apagada"
    m = await _midia(db)
    assert (m.status, m.caminho, m.nome_original) == ("apagada", None, None)
    # Nenhum arquivo no disco (nem o final, nem o temporário).
    assert not [x for x in uploads.rglob("*") if x.is_file()]
    (anexo,) = (await _mensagem(db, m)).anexos
    assert "url" not in anexo and anexo["status"] == zap_cadastro.STATUS_ANEXO_APAGADO
    # E não volta para a fila.
    assert await _baixar(recebida.id) == "ocupada"


async def test_duas_rodadas_nao_baixam_a_mesma_midia(db, recebida, graph, uploads):
    # Uma rodada pegou (`baixando`, com prazo): a outra não mexe.
    await _vencer(
        db, recebida.id, status="baixando", proxima_em=datetime.now(UTC) + timedelta(minutes=5)
    )
    assert await _baixar(recebida.id) == "ocupada"
    assert graph.chamadas == []
    # O worker caiu no meio (o prazo da `baixando` passou): outra rodada retoma.
    await _vencer(db, recebida.id, status="baixando")
    _meta_da_midia(graph)
    _cdn(graph)
    assert await _baixar(recebida.id) == "baixada"
    # Baixada não baixa de novo.
    assert await _baixar(recebida.id) == "ocupada"


async def test_token_nunca_no_erro_nem_no_log(db, recebida, graph, uploads):
    graph.quando(
        "GET",
        f"/{V}/MID1",
        lambda req: httpx.Response(
            400, json={"error": {"message": f"token ruim: {TOKEN}", "code": 190}}
        ),
    )
    with structlog.testing.capture_logs() as logs:
        assert await _baixar(recebida.id) == "pendente"
    m = await _midia(db)
    assert TOKEN not in (m.erro or "") and TOKEN not in repr(logs)


async def test_varrer_enfileira_as_pendentes_vencidas(db, recebida, zap, monkeypatch):
    from app.services.atendimento import zap_midia as modulo

    pool = zap.pool

    async def _pool():
        return pool

    monkeypatch.setattr(modulo, "get_arq_pool", _pool)
    ids = []

    async def enqueue_job(nome, *args, _job_id=None, **kw):
        ids.append(_job_id)
        pool.jobs.append((nome, *args))

    monkeypatch.setattr(pool, "enqueue_job", enqueue_job)
    # A rodada do worker (job/cron) processa os eventos E enfileira a mídia.
    resumo = await zap_entrada.rodada(origem="cron")
    assert resumo is not None and resumo["midias"] == 1
    assert pool.jobs == [("atendimento_zap_baixar_midia", str(recebida.id))]
    # Mesma tentativa = mesmo id (o arq não enfileira duas vezes).
    assert ids == [f"zap-midia:{recebida.id}:0"]
    # Em backoff (proxima_em no futuro): não enfileira.
    await _vencer(db, recebida.id, proxima_em=datetime.now(UTC) + timedelta(minutes=5))
    pool.jobs.clear()
    await zap_entrada.rodada(origem="cron")
    assert pool.jobs == []
    # Baixada, expirada ou falhou: nunca.
    for st in ("baixada", "expirada", "falhou", "grande_demais"):
        await _vencer(db, recebida.id, status=st)
        await zap_entrada.rodada(origem="cron")
    assert pool.jobs == []


def test_worker_minimo_do_localhost_so_tem_o_zap():
    from app import worker

    nomes = {getattr(f, "name", None) or f.__name__ for f in worker.WorkerSettingsZap.functions}
    assert nomes == {"atendimento_zap_processar", "atendimento_zap_baixar_midia"}
    crons = {c.name for c in worker.WorkerSettingsZap.cron_jobs}
    assert crons == {"cron:atendimento_zap_varrer"}
    # O job também está no worker de sempre (produção).
    default = {getattr(f, "name", None) or f.__name__ for f in worker.WorkerSettings.functions}
    assert "atendimento_zap_baixar_midia" in default


async def test_job_sai_cedo_com_a_leitura_desligada(monkeypatch):
    from app import worker

    s = get_settings()
    monkeypatch.setattr(s, "atendimento_zap_leitura_ativa", False)
    # O worker lê o `_settings` dele (pego na importação): outro teste que limpa o
    # cache do `get_settings` (o do DM do Instagram) deixa os dois diferentes.
    monkeypatch.setattr(worker._settings, "atendimento_zap_leitura_ativa", False)
    assert await worker.atendimento_zap_baixar_midia({}, str(uuid4())) is None


# ═══════════════════════ a rota (§5.5) ═══════════════════════════════════


@pytest_asyncio.fixture
async def baixada(db, recebida, graph, uploads) -> AtendimentoZapMidia:
    _meta_da_midia(graph)
    _cdn(graph)
    assert await _baixar(recebida.id) == "baixada"
    return await _midia(db)


async def test_rota_entrega_a_imagem_com_os_cabecalhos(client, admin, baixada):
    r = await client.get(f"{URL}/zap/midia/{baixada.id}")
    assert r.status_code == 200, r.text
    assert r.content == JPG
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["cache-control"].startswith("private")


async def test_rota_so_admin_e_404_para_os_outros(client, make_user, auth_as, baixada):
    # Sem login: 401.
    r = await client.get(f"{URL}/zap/midia/{baixada.id}")
    assert r.status_code == 401
    # Quem mexe na caixa mas não é admin: 404 (o Zap nem existe para ela).
    u = await make_user(permissions=PODE_TUDO, role=UserRole.USER)
    auth_as(u)
    r = await client.get(f"{URL}/zap/midia/{baixada.id}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "midia_nao_encontrada"


async def test_documento_vai_como_attachment_com_nome_seguro(db, client, admin, baixada, uploads):
    rel = baixada.caminho.replace(".jpg", ".pdf")
    (uploads / rel).write_bytes(PDF)
    await _vencer(
        db,
        baixada.id,
        proxima_em=None,
        caminho=rel,
        mime_detectado="application/pdf",
        nome_original='nota "fiscal"\r\nX-Injetado: 1.pdf',
    )
    r = await client.get(f"{URL}/zap/midia/{baixada.id}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert "\r" not in cd and "\n" not in cd and '"fiscal"' not in cd
    assert "x-injetado" not in {k.lower() for k in r.headers}


async def test_rota_sem_path_traversal_e_so_o_que_esta_baixado(db, client, admin, baixada):
    url = f"{URL}/zap/midia/{baixada.id}"
    await _vencer(db, baixada.id, proxima_em=None, caminho="../../../etc/passwd")
    assert (await client.get(url)).status_code == 404
    await _vencer(
        db, baixada.id, proxima_em=None, caminho=baixada.caminho, mime_detectado="text/html"
    )
    assert (await client.get(url)).status_code == 404
    await _vencer(db, baixada.id, proxima_em=None, mime_detectado="image/jpeg", status="pendente")
    assert (await client.get(url)).status_code == 404
    assert (await client.get(f"{URL}/zap/midia/nao-e-uuid")).status_code == 404
    assert (await client.get(f"{URL}/zap/midia/{uuid4()}")).status_code == 404


# ═══════════════════════ a retenção (180 dias) ═══════════════════════════


async def test_retencao_de_180_dias_registrada_e_limpeza_apaga(db, baixada, uploads):
    assert get_settings().atendimento_zap_retencao_midia_dias == 180
    msg = await _mensagem(db, baixada)
    assert abs((baixada.apagar_em - msg.enviada_em) - timedelta(days=180)) < timedelta(seconds=1)
    arquivo = uploads / baixada.caminho
    async with _db.SessionLocal() as s:
        assert await zap_midia.apagar_vencidas(s, agora=datetime.now(UTC)) == 0
    assert arquivo.exists()
    async with _db.SessionLocal() as s:
        n = await zap_midia.apagar_vencidas(s, agora=datetime.now(UTC) + timedelta(days=181))
    assert n == 1
    m = await _midia(db)
    assert m.status == "apagada" and m.caminho is None
    assert not arquivo.exists()
    (anexo,) = (await _mensagem(db, m)).anexos
    assert anexo["status"] == "apagada" and "url" not in anexo


# ═══════════════════════ a foto de SAÍDA (§6.8) ══════════════════════════


def _upload_ok(graph, media_id: str = "UP1") -> None:
    graph.quando(
        "POST", f"/{V}/{PNID}/media", lambda req: httpx.Response(200, json={"id": media_id})
    )


def _corpos_json(graph) -> list[dict]:
    return [
        json.loads(r.content)
        for r in graph.chamadas
        if r.method == "POST" and r.url.path.endswith("/messages")
    ]


@pytest_asyncio.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    return u


async def test_foto_do_zap_sobe_e_manda_com_legenda(db, client, envio, pessoa, uploads):
    _upload_ok(envio.graph)
    r = await client.post(
        f"{URL}/conversas/{envio.conversa.id}/foto",
        files={"arquivo": ("mala.jpg", JPG, "image/jpeg")},
        data={"legenda": "Segue a foto da mala"},
    )
    assert r.status_code == 200, r.text
    m = r.json()["mensagem"]
    assert (m["status"], m["tipo"], m["texto"]) == ("enviada", "imagem", "Segue a foto da mala")
    upload, mensagem = envio.graph.chamadas
    # 1. O upload: multipart com o arquivo, o tipo e o messaging_product.
    assert upload.url.path == f"/{V}/{PNID}/media"
    assert upload.headers["content-type"].startswith("multipart/form-data")
    bruto = upload.content
    assert b'name="messaging_product"' in bruto and b"whatsapp" in bruto
    assert b'name="type"' in bruto and b"image/jpeg" in bruto
    assert b'name="file"' in bruto and JPG in bruto
    # 2. A mensagem com o id da mídia, a legenda e o id da NOSSA mensagem.
    assert json.loads(mensagem.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": WA_ID,
        "type": "image",
        "image": {"id": "UP1", "caption": "Segue a foto da mala"},
        "biz_opaque_callback_data": m["id"],
    }
    # O token SÓ no header.
    for req in envio.graph.chamadas:
        assert req.headers["authorization"].endswith(TOKEN)
        assert TOKEN not in str(req.url) and TOKEN.encode() not in req.content
    # A nossa cópia no disco e a URL RELATIVA no balão.
    midia = (
        await db.execute(select(AtendimentoZapMidia).where(AtendimentoZapMidia.direcao == "saida"))
    ).scalar_one()
    assert (midia.status, midia.media_id, midia.mime_detectado) == ("baixada", "UP1", "image/jpeg")
    assert (uploads / midia.caminho).read_bytes() == JPG
    assert midia.apagar_em is not None
    assert m["anexos"][0]["url"] == f"/api/atendimento/zap/midia/{midia.id}"
    # E a rota serve a nossa cópia.
    r = await client.get(m["anexos"][0]["url"])
    assert r.status_code == 200 and r.content == JPG


async def test_foto_do_zap_sem_legenda_vai_sozinha(db, envio, pessoa, uploads):
    _upload_ok(envio.graph)
    imagem = foto.validar_foto(PNG, "x.png", "image/png")
    m = await enviar.enviar_foto(db, envio.conversa, imagem, user=pessoa)
    assert m.status == "enviada" and m.texto is None
    (corpo_msg,) = _corpos_json(envio.graph)
    assert corpo_msg["image"] == {"id": "UP1"}


async def test_legenda_passa_pelo_validador_e_nada_sobe(db, client, envio, pessoa, uploads):
    r = await client.post(
        f"{URL}/conversas/{envio.conversa.id}/foto",
        files={"arquivo": ("mala.jpg", JPG, "image/jpeg")},
        data={"legenda": "faz um pix na chave 11999998888 que eu mando"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "texto_invalido"
    assert envio.graph.chamadas == []
    assert [p for p in uploads.rglob("*") if p.is_file()] == []
    assert (await db.scalar(select(AtendimentoZapMidia.id))) is None


async def test_legenda_acima_de_1024_recusada(db, envio, pessoa, uploads):
    imagem = foto.validar_foto(JPG, "x.jpg", "image/jpeg")
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, envio.conversa, imagem, legenda="a" * 1025, user=pessoa)
    assert e.value.code == "texto_invalido"
    assert any("1024" in m for m in e.value.detail)
    assert envio.graph.chamadas == []


async def test_foto_passa_pelas_mesmas_travas_do_texto(db, envio, pessoa, uploads, monkeypatch):
    imagem = foto.validar_foto(JPG, "x.jpg", "image/jpeg")

    async def recusa():
        # Relidos: o rollback de cada volta expira os objetos da sessão.
        conversa = await conversa_do_numero(db)
        await db.refresh(pessoa)
        with pytest.raises(EnvioRecusado) as e:
            await enviar.enviar_foto(db, conversa, imagem, user=pessoa)
        await db.rollback()
        return e.value.code

    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", False)
    assert await recusa() == "zap_envio_desligado"
    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", True)
    await db.execute(update(AtendimentoZapNumero).values(envio_ligado=False))
    await db.commit()
    assert await recusa() == "zap_numero_pausado"
    await db.execute(update(AtendimentoZapNumero).values(envio_ligado=True))
    await db.commit()
    monkeypatch.setattr(envio.settings, "atendimento_zap_destinos_teste", "5511900001111")
    assert await recusa() == "zap_fora_dos_testes"
    monkeypatch.setattr(envio.settings, "atendimento_zap_destinos_teste", WA_ID)
    from app.models import AtendimentoZapContato

    await db.execute(
        update(AtendimentoZapContato).values(
            ultima_recebida_em=datetime.now(UTC) - timedelta(hours=25)
        )
    )
    await db.commit()
    assert await recusa() == "fora_da_janela"
    # Nada subiu para a Meta e nada ficou no disco.
    assert envio.graph.chamadas == []
    assert [p for p in uploads.rglob("*") if p.is_file()] == []


async def test_foto_do_zap_so_admin(db, envio, make_user, uploads):
    quem = await make_user(permissions=PODE_TUDO, role=UserRole.USER)
    imagem = foto.validar_foto(JPG, "x.jpg", "image/jpeg")
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, envio.conversa, imagem, user=quem)
    assert e.value.code == "zap_so_admin"
    assert envio.graph.chamadas == []


async def test_foto_do_zap_nunca_pede_cliente_da_loja(db, envio, pessoa, uploads, monkeypatch):
    """O Zap não tem loja: o ramo do Zap vem ANTES do `cliente_da_integracao`."""

    async def nunca(integration):
        raise AssertionError("o Zap não tem cliente de loja")

    monkeypatch.setattr(clientes, "cliente_da_integracao", nunca)
    _upload_ok(envio.graph)
    imagem = foto.validar_foto(JPG, "x.jpg", "image/jpeg")
    m = await enviar.enviar_foto(db, envio.conversa, imagem, user=pessoa)
    assert m.status == "enviada" and "cliente_indisponivel" not in (m.erro or "")


async def test_upload_que_falha_nao_saiu(db, envio, pessoa, uploads):
    envio.graph.quando("POST", f"/{V}/{PNID}/media", lambda req: httpx.Response(500, text="erro"))
    imagem = foto.validar_foto(JPG, "x.jpg", "image/jpeg")
    m = await enviar.enviar_foto(db, envio.conversa, imagem, user=pessoa)
    # Nada chegou ao cliente: `falhou` (não `revisar`), e a mensagem nem foi pedida.
    assert m.status == "falhou" and m.erro.startswith("zap_upload")
    assert [r.url.path for r in envio.graph.chamadas] == [f"/{V}/{PNID}/media"]


async def test_mensagem_da_foto_sem_resposta_e_ambigua(db, envio, pessoa, uploads):
    _upload_ok(envio.graph)
    envio.graph.mensagens(resposta=httpx.ReadTimeout("devagar"))
    imagem = foto.validar_foto(JPG, "x.jpg", "image/jpeg")
    m = await enviar.enviar_foto(db, envio.conversa, imagem, user=pessoa)
    assert m.status == "revisar"
    # Nunca retentada.
    assert len(_corpos_json(envio.graph)) == 1


async def test_efeito_da_meta_na_foto_pausa_o_numero(db, envio, pessoa, uploads):
    _upload_ok(envio.graph)
    envio.graph.mensagens(
        resposta=lambda req: httpx.Response(
            400, json={"error": {"message": "restrito", "code": 368}}
        )
    )
    imagem = foto.validar_foto(JPG, "x.jpg", "image/jpeg")
    m = await enviar.enviar_foto(db, envio.conversa, imagem, user=pessoa)
    assert m.status == "falhou" and m.erro == "meta:368"
    numero = await db.get(AtendimentoZapNumero, envio.numero.id, populate_existing=True)
    assert numero.envio_ligado is False


async def test_simulador_com_foto_do_zap_guarda_a_copia(db, envio, pessoa, uploads, monkeypatch):
    monkeypatch.setattr(envio.settings, "atendimento_simulador", True)
    imagem = foto.validar_foto(JPG, "x.jpg", "image/jpeg")
    m = await enviar.enviar_foto(db, envio.conversa, imagem, user=pessoa)
    assert m.status == "enviada" and m.externo_id.startswith("sim:")
    assert envio.graph.chamadas == []
    # O balão mostra a nossa cópia mesmo no simulador.
    assert m.anexos[0]["url"].startswith("/api/atendimento/zap/midia/")


async def test_aplicar_resultado_aceita_so_a_url_relativa_segura(db, envio):
    from app.services.atendimento.constantes import ResultadoEnvio

    async def url_gravada(url: str) -> str | None:
        conversa = await conversa_do_numero(db)  # relida: o rollback expira tudo
        msg = AtendimentoMensagem(
            conversa_id=conversa.id,
            autor="loja",
            origem="davinci_humano",
            tipo="imagem",
            texto=None,
            anexos=[{"tipo": "imagem"}],
            status="enviando",
            payload={},
        )
        db.add(msg)
        await db.flush()
        await enviar._aplicar_resultado(
            db, conversa, msg, ResultadoEnvio(ok=True, payload={"imagem_url": url})
        )
        valor = msg.anexos[0].get("url")
        await db.rollback()
        return valor

    assert await url_gravada("/api/atendimento/zap/midia/abc") == "/api/atendimento/zap/midia/abc"
    assert await url_gravada("https://cf.shopee.com.br/file/abc") == (
        "https://cf.shopee.com.br/file/abc"
    )
    for ruim in ("//evil.com/x", "javascript:alert(1)", "http://x/y", "/api/x y", "data:x"):
        assert await url_gravada(ruim) is None, ruim


async def test_painel_do_zap_libera_a_foto_com_legenda_opcional(
    db, client, envio, pessoa, monkeypatch
):
    r = await client.get(f"{URL}/conversas/{envio.conversa.id}/painel")
    assert r.status_code == 200, r.text
    ef = r.json()["envio_foto"]
    assert (ef["pode"], ef["legenda_permitida"], ef["legenda_obrigatoria"], ef["legenda_max"]) == (
        True,
        True,
        False,
        1024,
    )
    # Com a chave do Zap desligada, o botão diz o porquê (a mesma trava do texto).
    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", False)
    ef = (await client.get(f"{URL}/conversas/{envio.conversa.id}/painel")).json()["envio_foto"]
    assert (ef["pode"], ef["codigo"]) == (False, "zap_envio_desligado")


def test_caixas_com_foto_e_legenda():
    assert ("whatsapp", "zap") in foto.CAIXAS_COM_FOTO
    assert foto.MAX_BYTES_PLATAFORMA["whatsapp"] == 5 * 1024 * 1024

    class C:
        def __init__(self, plataforma, canal):
            self.plataforma, self.canal = plataforma, canal

    zap_c, ml_c, shopee_c = C("whatsapp", "zap"), C("ml", "pos_venda"), C("shopee", "chat")
    assert (foto.legenda_permitida(zap_c), foto.legenda_obrigatoria(zap_c)) == (True, False)
    assert (foto.legenda_permitida(ml_c), foto.legenda_obrigatoria(ml_c)) == (True, True)
    assert (foto.legenda_permitida(shopee_c), foto.legenda_obrigatoria(shopee_c)) == (False, False)
    assert foto.limite_da_legenda(zap_c) == 1024 and foto.limite_da_legenda(shopee_c) is None
    # O Zap no formato antigo (com a plataforma da venda) continua sem foto.
    assert foto.motivo_sem_foto(C("shopee", "zap")) is not None


# ═══════════════════════ o script local ══════════════════════════════════


async def test_script_simula_cada_midia_e_guarda_um_arquivo_local(
    db, zap, uploads, tmp_path, monkeypatch
):
    from scripts import atendimento_zap_local as script

    monkeypatch.setattr(zap.settings, "api_url", "http://127.0.0.1:8000")
    async with _db.SessionLocal() as s:
        await script.cadastrar_ficticio(s)
    tipos = {
        "imagem": ("imagem", JPG),
        "audio": ("audio", OGG),
        "video": ("video", MP4),
        "documento": ("arquivo", PDF),
        "figurinha": ("figurinha", WEBP),
    }
    for tipo, (anexo_tipo, conteudo) in tipos.items():
        arquivo = tmp_path / f"origem-{tipo}"
        arquivo.write_bytes(conteudo)
        async with _db.SessionLocal() as s:
            saida = await script.simular(
                s, tipo=tipo, direto=True, processar=True, arquivo=str(arquivo)
            )
        assert saida["processado"]["resultados"] == {"gravado": 1}, tipo
        assert saida["midia"]["status"] == "baixada", tipo
        m = (
            await db.execute(
                select(AtendimentoZapMidia).where(AtendimentoZapMidia.media_id == saida["media_id"])
            )
        ).scalar_one()
        assert (uploads / m.caminho).read_bytes() == conteudo
        (anexo,) = (await _mensagem(db, m)).anexos
        assert anexo["tipo"] == anexo_tipo and anexo["url"].startswith(
            "/api/atendimento/zap/midia/"
        )
    # Um arquivo que não é do tipo aceito não vira cópia.
    ruim = tmp_path / "pagina.html"
    ruim.write_bytes(HTML)
    async with _db.SessionLocal() as s:
        saida = await script.simular(
            s, tipo="documento", direto=True, processar=True, arquivo=str(ruim)
        )
    assert saida["midia"]["status"] == "falhou"


def test_script_monta_a_midia_com_o_media_id():
    from scripts import atendimento_zap_local as script

    c = script.montar_corpo(
        "imagem", waba_id="1", pnid="2", display="5511900000000", media_id="REAL123"
    )
    msg = c["entry"][0]["changes"][0]["value"]["messages"][0]
    assert msg["image"]["id"] == "REAL123"


async def test_subir_midia_so_com_o_app_de_teste(db, zap, graph, uploads, tmp_path, monkeypatch):
    from scripts import atendimento_zap_local as script

    monkeypatch.setattr(zap.settings, "api_url", "http://127.0.0.1:8000")
    arquivo = tmp_path / "foto.jpg"
    arquivo.write_bytes(JPG)
    # Só o fictício cadastrado (token de mentira): recusa, sem ir à Meta.
    async with _db.SessionLocal() as s:
        await script.cadastrar_ficticio(s)
    async with _db.SessionLocal() as s:
        with pytest.raises(script.Recusado):
            await script.subir_midia(s, arquivo=str(arquivo))
    assert graph.chamadas == []
    # Com o app de TESTE (número +1 555): sobe e devolve o id.
    _app, _conta, numero = await cadastrar(
        db,
        app_id=script.APP_ID_TESTE,
        waba="922501156908169",
        pnid="937856119421901",
        display="+1 555-169-8240",
    )
    graph.quando(
        "POST",
        f"/{V}/937856119421901/media",
        lambda req: httpx.Response(200, json={"id": "META777"}),
    )
    async with _db.SessionLocal() as s:
        saida = await script.subir_midia(s, arquivo=str(arquivo))
    assert saida == {"media_id": "META777", "mime": "image/jpeg", "bytes": len(JPG)}
    (req,) = graph.chamadas
    assert req.headers["authorization"].endswith(TOKEN) and TOKEN.encode() not in req.content
    # Arquivo que não é do tipo aceito: recusa antes de subir.
    ruim = tmp_path / "x.html"
    ruim.write_bytes(HTML)
    async with _db.SessionLocal() as s:
        with pytest.raises(script.Recusado):
            await script.subir_midia(s, arquivo=str(ruim))
    assert len(graph.chamadas) == 1


# ═══════════════════════ a entrada continua igual ════════════════════════


async def test_a_midia_nasce_pendente_com_prazo_e_retencao(db, recebida):
    msg = await _mensagem(db, recebida)
    assert recebida.expira_meta_em - msg.enviada_em == timedelta(days=7)
    assert recebida.apagar_em - msg.enviada_em == timedelta(days=180)
    (anexo,) = msg.anexos
    assert anexo == {
        "tipo": "imagem",
        "midia_id": str(recebida.id),
        "mime": "image/jpeg",
        "status": "pendente",
    }
    # O canal do número continua o mesmo (nada de loja).
    canal = (
        await db.execute(
            select(AtendimentoCanal).where(
                AtendimentoCanal.externo_ref == canais_externos.ref_do_zap(PNID)
            )
        )
    ).scalar_one()
    assert canal.integration_id is None
