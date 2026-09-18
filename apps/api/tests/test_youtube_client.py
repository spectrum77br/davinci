"""Publicação de Shorts no YouTube — tudo fakado com respx, ZERO rede.

O que estes testes protegem, em ordem de importância:

1. **A conferência depois do upload.** A resposta do `videos.insert` ecoa o que
   foi PEDIDO, não o que valeu. A doc do Google diz que projeto não auditado
   força privado; provamos em 18/09/2026 que não força — mas se um dia forçar,
   o publicador não pode gravar "publicado" numa linha cujo vídeo ninguém vê.
2. **O ambíguo.** Timeout no envio do arquivo NÃO é falha: o YouTube pode ter
   recebido tudo e a resposta ter se perdido. Republicar às cegas duplica
   vídeo no canal, e isso não tem desfazer bonito.
3. **O carimbo da sessão ANTES de subir os bytes**, pelo mesmo motivo.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.services.marketing.youtube_client import (
    _titulo_e_descricao,
    publicar_video_youtube,
)

OAUTH = "https://oauth2.googleapis.com/token"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
SESSAO = "https://upload.googleapis.com/sessao-fake-123"
VID = "QW-0JkUsCU0"


@pytest.fixture(autouse=True)
def _credenciais(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "youtube_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "youtube_client_secret", "segredo")
    monkeypatch.setattr(s, "youtube_privacidade", "public")


@pytest.fixture
def video(tmp_path: Path) -> Path:
    p = tmp_path / "criativo.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 4096)
    return p


def _oauth_ok(router):
    router.post(OAUTH).mock(return_value=httpx.Response(200, json={"access_token": "at-1"}))


# ─────────────── título e descrição ───────────────


def test_primeira_linha_vira_titulo():
    t, d = _titulo_e_descricao("Mala de bordo\n\nCasco rígido e TSA.")
    assert t == "Mala de bordo"
    assert "Casco rígido" in d


def test_titulo_nunca_passa_de_100():
    assert len(_titulo_e_descricao("x" * 400)[0]) == 100


def test_legenda_vazia_tem_titulo():
    # Título vazio faz a API recusar a chamada INTEIRA, depois do upload.
    assert _titulo_e_descricao("   ")[0] == "Novo vídeo"


def test_escapa_sinais_que_o_youtube_recusa():
    t, _ = _titulo_e_descricao("Promo <b>agora</b>")
    assert "<" not in t and ">" not in t


# ─────────────── o caminho feliz ───────────────


async def test_publica_e_confere_a_privacidade(video: Path):
    ordem: list[str] = []

    async def _carimbo(sessao: str) -> None:
        ordem.append("banco:sessao")

    with respx.mock(assert_all_called=True) as router:
        _oauth_ok(router)
        router.post(url__startswith=UPLOAD).mock(
            side_effect=lambda r: (
                ordem.append("abre"),
                httpx.Response(200, headers={"Location": SESSAO}),
            )[1]
        )
        router.put(SESSAO).mock(
            side_effect=lambda r: (
                ordem.append("envia"),
                httpx.Response(200, json={"id": VID}),
            )[1]
        )
        router.get(url__startswith=VIDEOS).mock(
            return_value=httpx.Response(
                200, json={"items": [{"status": {"privacyStatus": "public"}}]}
            )
        )
        res = await publicar_video_youtube(
            refresh_token="rt", video_path=video, legenda="Oi", ao_abrir_sessao=_carimbo
        )

    assert res.ok is True
    assert res.post_external_id == VID
    assert res.post_url == f"https://youtu.be/{VID}"
    # O carimbo cai ANTES do envio — é o que impede republicar às cegas.
    assert ordem == ["abre", "banco:sessao", "envia"]


async def test_subiu_mas_ficou_privado_nao_e_sucesso(video: Path):
    """Se a trava do YouTube um dia for aplicada, tem que aparecer."""
    with respx.mock(assert_all_called=True) as router:
        _oauth_ok(router)
        router.post(url__startswith=UPLOAD).mock(
            return_value=httpx.Response(200, headers={"Location": SESSAO})
        )
        router.put(SESSAO).mock(return_value=httpx.Response(200, json={"id": VID}))
        router.get(url__startswith=VIDEOS).mock(
            return_value=httpx.Response(
                200, json={"items": [{"status": {"privacyStatus": "private"}}]}
            )
        )
        res = await publicar_video_youtube(
            refresh_token="rt", video_path=video, legenda="Oi"
        )

    assert res.ok is False
    assert res.post_external_id == VID  # o vídeo existe, alguém precisa achar
    assert "private" in (res.erro or "")
    assert "auditoria" in (res.erro or "")


# ─────────────── o que não pode virar retry ───────────────


async def test_timeout_no_envio_vira_ambiguo(video: Path):
    with respx.mock(assert_all_called=False) as router:
        _oauth_ok(router)
        router.post(url__startswith=UPLOAD).mock(
            return_value=httpx.Response(200, headers={"Location": SESSAO})
        )
        router.put(SESSAO).mock(side_effect=httpx.ReadTimeout("estourou"))
        res = await publicar_video_youtube(
            refresh_token="rt", video_path=video, legenda="Oi"
        )

    assert res.ok is False
    assert res.ambiguo is True, "timeout no upload NÃO pode virar retry cego"
    assert "republicar" in (res.erro or "")


async def test_falha_ao_abrir_sessao_nao_e_ambigua(video: Path):
    """Falhar antes de enviar byte nenhum é seguro: nada foi criado."""
    with respx.mock(assert_all_called=False) as router:
        _oauth_ok(router)
        router.post(url__startswith=UPLOAD).mock(
            return_value=httpx.Response(403, json={"error": {"message": "sem permissão"}})
        )
        res = await publicar_video_youtube(
            refresh_token="rt", video_path=video, legenda="Oi"
        )

    assert res.ok is False
    assert res.ambiguo is False
    assert "sem permissão" in (res.erro or "")


# ─────────────── credencial ───────────────


async def test_refresh_invalido_diz_o_que_fazer(video: Path):
    with respx.mock(assert_all_called=False) as router:
        router.post(OAUTH).mock(
            return_value=httpx.Response(
                400, json={"error": {"message": "invalid_grant: bad request"}}
            )
        )
        res = await publicar_video_youtube(
            refresh_token="rt-velho", video_path=video, legenda="Oi"
        )

    assert res.ok is False
    # O motivo quase sempre é um só, e dizer isso poupa meia hora de caça.
    assert "reconecte" in (res.erro or "")
    assert "modo de testes" in (res.erro or "")


async def test_sem_credencial_do_app_falha_claro(video: Path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "youtube_client_id", "")
    res = await publicar_video_youtube(refresh_token="rt", video_path=video, legenda="Oi")
    assert res.ok is False
    assert "YOUTUBE_CLIENT_ID" in (res.erro or "")


async def test_arquivo_vazio_nao_chega_na_rede(tmp_path: Path):
    vazio = tmp_path / "vazio.mp4"
    vazio.write_bytes(b"")
    with respx.mock(assert_all_called=False):
        res = await publicar_video_youtube(
            refresh_token="rt", video_path=vazio, legenda="Oi"
        )
    assert res.ok is False
    assert "vazio" in (res.erro or "")
