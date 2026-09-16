"""Cliente da Graph API da Meta — tudo fakado com respx, ZERO rede.

Mesma escolha do `tests/test_marketing_command_consumer.py` ("o cliente HTTP da
Shopee Ads é fakado ponta a ponta"): aqui a Meta inteira é fakada, porque um
teste que encostasse na Graph API de verdade publicaria um Reel na conta de uma
marca — e publicar NÃO tem desfazer.

O que estes testes protegem, em ordem de gravidade:
  1. **Não republicar.** Container em ERROR/EXPIRED e estouro do teto de poll
     NUNCA podem chamar `media_publish`; o `container_id` volta pro worker pra
     reconciliação perguntar "será que saiu?" em vez de tentar de novo.
  2. **Ordem das fases.** O callback `ao_criar_container` grava o id no banco
     ANTES do passo irreversível; se o processo cair no meio, há por onde
     perguntar.
  3. **O token some.** Ele não vai na URL, não vai na mensagem de erro e não vai
     no log — nem quando a própria Meta devolve o token de volta no `message`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx
import structlog

from app.services.marketing import meta_client
from app.services.marketing.meta_client import (
    MetaError,
    publicar_reel_facebook,
    publicar_reel_instagram,
)

# Token FAKE e bem distinto: metade destes testes é justamente provar que esta
# string não sobra em lugar nenhum.
TOKEN = "TOKENSECRETO123"  # noqa: S105 — token de teste, não é segredo
PAGE_ID = "1122334455"
IG_USER_ID = "17841400000000000"
VIDEO_ID = "vid-9001"
CONTAINER_ID = "ctn-7001"
MEDIA_ID = "media-5001"
VIDEO_URL_PUBLICA = "https://exemplo/x.mp4"

# Bytes só o bastante pra `file_size` ter um valor conferível (o criativo real
# tem 26-41 MB; o que importa aqui é o header, não o tamanho).
CONTEUDO = b"\x00\x01FAKE-MP4-BYTES" * 64


def _base() -> str:
    """`https://graph.facebook.com/<versão>` — a versão vem do settings, então é
    lida na hora e não fixada no teste."""
    return f"{meta_client.GRAPH_HOST}/{meta_client.graph_version()}"


def _form(request: httpx.Request) -> dict[str, str]:
    """Corpo x-www-form-urlencoded → dict simples (a Meta só recebe escalares)."""
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


@pytest.fixture
def video(tmp_path: Path) -> Path:
    """Arquivo no disco de verdade: o cliente confere existência e tamanho antes
    de abrir qualquer container."""
    caminho = tmp_path / "criativo.mp4"
    caminho.write_bytes(CONTEUDO)
    return caminho


@pytest.fixture
def dormidas(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Registra (e não cumpre) os `asyncio.sleep` do poll.

    O poll do Instagram espera 1 min entre consultas: sem isso o teste do teto
    levaria 5 minutos reais. A lista guarda os intervalos pedidos, que é
    exatamente o que queremos asseverar — a cadência, não a espera."""
    registro: list[float] = []

    async def _sleep(segundos: float) -> None:
        registro.append(segundos)

    monkeypatch.setattr(meta_client.asyncio, "sleep", _sleep)
    return registro


# ───────────────────────────────── Facebook ─────────────────────────────────


async def test_facebook_tres_fases_na_ordem_com_binario_e_id_gravado_antes_do_finish(
    video: Path,
) -> None:
    """As 3 fases do Reels do FB (start → rupload → finish) têm ordem obrigatória,
    e o `video_id` precisa chegar ao banco ANTES do finish: se o worker morrer no
    meio, a reconciliação consulta esse id em vez de republicar às cegas."""
    upload_url = f"{meta_client.RUPLOAD_HOST}/video-upload/{meta_client.graph_version()}/{VIDEO_ID}"
    eventos: list[str] = []
    finish: dict[str, str] = {}
    enviado: dict[str, Any] = {}

    def _video_reels(request: httpx.Request) -> httpx.Response:
        corpo = _form(request)
        eventos.append(f"fb:{corpo['upload_phase']}")
        if corpo["upload_phase"] == "start":
            return httpx.Response(200, json={"video_id": VIDEO_ID, "upload_url": upload_url})
        finish.update(corpo)
        return httpx.Response(200, json={"success": True})

    def _rupload(request: httpx.Request) -> httpx.Response:
        eventos.append("fb:upload")
        enviado["headers"] = dict(request.headers)
        enviado["body"] = request.content
        return httpx.Response(200, json={"success": True})

    async def _ao_criar_container(container_id: str) -> None:
        eventos.append(f"banco:{container_id}")

    with respx.mock(assert_all_called=True) as router:
        rota_reels = router.post(f"{_base()}/{PAGE_ID}/video_reels").mock(side_effect=_video_reels)
        router.post(upload_url).mock(side_effect=_rupload)
        router.get(f"{_base()}/{VIDEO_ID}").mock(
            return_value=httpx.Response(200, json={"permalink_url": f"/reel/{VIDEO_ID}"}),
        )
        res = await publicar_reel_facebook(
            page_id=PAGE_ID,
            token=TOKEN,
            video_path=video,
            legenda="Mala de bordo 🧳",
            ao_criar_container=_ao_criar_container,
        )
        urls = [str(c.request.url) for c in router.calls]

    # A ordem é o teste: o id no banco cai entre a fase 1 e o upload, muito antes
    # do finish (que é o passo que publica).
    assert eventos == ["fb:start", f"banco:{VIDEO_ID}", "fb:upload", "fb:finish"]
    assert rota_reels.call_count == 2

    # O binário vai pro rupload com os 3 headers que a Meta exige.
    assert enviado["headers"]["authorization"] == f"OAuth {TOKEN}"
    assert enviado["headers"]["offset"] == "0"
    assert enviado["headers"]["file_size"] == str(len(CONTEUDO))
    assert enviado["body"] == CONTEUDO

    # `description` (não `caption`) é o campo do Reels do FB; sem data = PUBLISHED.
    assert finish["video_id"] == VIDEO_ID
    assert finish["description"] == "Mala de bordo 🧳"
    assert finish["video_state"] == "PUBLISHED"

    assert res.ok is True
    assert res.post_external_id == VIDEO_ID
    assert res.container_id == VIDEO_ID
    # Permalink relativo vira absoluto — é o link que aparece na tela.
    assert res.post_url == f"https://www.facebook.com/reel/{VIDEO_ID}"

    # Regra 1 do módulo: o token viaja no header, NUNCA na query string (a URL
    # entra em log de exceção do httpx).
    assert all(TOKEN not in u for u in urls)


# ───────────────────────────────── Instagram ────────────────────────────────


async def test_instagram_binario_container_upload_poll_ate_finished_e_publish(
    video: Path, dormidas: list[float]
) -> None:
    """Caminho preferido do IG (marca COM Página): o criativo sobe em binário e
    nunca aparece na internet. O poll acompanha a transcodificação — IN_PROGRESS
    duas vezes, FINISHED na terceira — e só então o passo irreversível
    (`media_publish`) acontece, depois de o container já estar no banco."""
    upload_url = (
        f"{meta_client.RUPLOAD_HOST}/ig-api-upload/{meta_client.graph_version()}/{CONTAINER_ID}"
    )
    eventos: list[str] = []
    container: dict[str, str] = {}
    publish: dict[str, str] = {}
    estados = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])

    def _media(request: httpx.Request) -> httpx.Response:
        eventos.append("ig:container")
        container.update(_form(request))
        return httpx.Response(200, json={"id": CONTAINER_ID, "uri": upload_url})

    def _rupload(request: httpx.Request) -> httpx.Response:
        eventos.append("ig:upload")
        assert request.headers["authorization"] == f"OAuth {TOKEN}"
        assert request.headers["file_size"] == str(len(CONTEUDO))
        return httpx.Response(200, json={"success": True})

    def _status(request: httpx.Request) -> httpx.Response:
        estado = next(estados)
        eventos.append(f"ig:poll:{estado}")
        return httpx.Response(200, json={"status_code": estado})

    def _publish(request: httpx.Request) -> httpx.Response:
        eventos.append("ig:publish")
        publish.update(_form(request))
        return httpx.Response(200, json={"id": MEDIA_ID})

    async def _ao_criar_container(container_id: str) -> None:
        eventos.append(f"banco:{container_id}")

    with respx.mock(assert_all_called=True) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(side_effect=_media)
        router.post(upload_url).mock(side_effect=_rupload)
        router.get(f"{_base()}/{CONTAINER_ID}").mock(side_effect=_status)
        router.post(f"{_base()}/{IG_USER_ID}/media_publish").mock(side_effect=_publish)
        router.get(f"{_base()}/{MEDIA_ID}").mock(
            return_value=httpx.Response(
                200, json={"permalink": "https://www.instagram.com/reel/abc/"}
            ),
        )
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="Mala de bordo",
            ao_criar_container=_ao_criar_container,
        )

    assert eventos == [
        "ig:container",
        f"banco:{CONTAINER_ID}",
        "ig:upload",
        "ig:poll:IN_PROGRESS",
        "ig:poll:IN_PROGRESS",
        "ig:poll:FINISHED",
        "ig:publish",
    ]
    # Primeira consulta é imediata; a cadência de 1 min só entra depois dela —
    # 3 polls = 2 esperas, no intervalo do módulo.
    assert dormidas == [meta_client.IG_POLL_INTERVALO_S] * 2

    assert container["media_type"] == "REELS"
    assert container["upload_type"] == "resumable"
    assert container["share_to_feed"] == "true"
    assert "video_url" not in container  # com Página, nada de link público
    assert publish["creation_id"] == CONTAINER_ID

    assert res.ok is True
    assert res.post_external_id == MEDIA_ID
    assert res.container_id == CONTAINER_ID
    assert res.post_url == "https://www.instagram.com/reel/abc/"


async def test_instagram_por_link_manda_video_url_e_nao_toca_no_rupload(
    video: Path, dormidas: list[float]
) -> None:
    """Marca SEM Página do Facebook: a Meta baixa o vídeo do link assinado
    (`video_url`), então o upload binário não acontece — é o caso padrão hoje
    (`marketing_postagem_upload="link"`). Fora isso o fluxo é idêntico: mesmo
    container, mesmo poll, mesmo publish."""
    eventos: list[str] = []
    container: dict[str, str] = {}

    def _media(request: httpx.Request) -> httpx.Response:
        eventos.append("ig:container")
        container.update(_form(request))
        return httpx.Response(200, json={"id": CONTAINER_ID})

    def _publish(request: httpx.Request) -> httpx.Response:
        eventos.append("ig:publish")
        return httpx.Response(200, json={"id": MEDIA_ID})

    async def _ao_criar_container(container_id: str) -> None:
        eventos.append(f"banco:{container_id}")

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(side_effect=_media)
        rupload = router.route(host="rupload.facebook.com")
        router.get(f"{_base()}/{CONTAINER_ID}").mock(
            return_value=httpx.Response(200, json={"status_code": "FINISHED"}),
        )
        router.post(f"{_base()}/{IG_USER_ID}/media_publish").mock(side_effect=_publish)
        router.get(f"{_base()}/{MEDIA_ID}").mock(
            return_value=httpx.Response(
                200, json={"permalink": "https://www.instagram.com/reel/abc/"}
            ),
        )
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="Mala de bordo",
            video_url=VIDEO_URL_PUBLICA,
            ao_criar_container=_ao_criar_container,
        )

    assert container["video_url"] == VIDEO_URL_PUBLICA
    assert "upload_type" not in container  # resumable e video_url são excludentes
    assert rupload.call_count == 0, "no caminho por link nenhum byte sai daqui"
    assert eventos == ["ig:container", f"banco:{CONTAINER_ID}", "ig:publish"]
    assert dormidas == []  # primeira consulta já veio FINISHED

    assert res.ok is True
    assert res.post_external_id == MEDIA_ID
    assert res.container_id == CONTAINER_ID


@pytest.mark.parametrize(
    ("estado", "trecho"),
    [
        ("ERROR", "falhou ao processar"),
        ("EXPIRED", "expirou"),
    ],
)
async def test_instagram_container_terminal_ruim_nao_chama_media_publish(
    video: Path, dormidas: list[float], estado: str, trecho: str
) -> None:
    """Container que morreu (ERROR) ou venceu as 24h (EXPIRED) devolve ok=False
    COM o container_id e sem tocar no `media_publish`.

    É o pior erro possível do robô: publicar de novo por engano deixa o Reel
    duplicado na conta da marca, e isso não tem desfazer. O container_id volta
    preenchido pra reconciliação conseguir perguntar pela mesma mídia."""
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(
            return_value=httpx.Response(200, json={"id": CONTAINER_ID}),
        )
        router.get(f"{_base()}/{CONTAINER_ID}").mock(
            return_value=httpx.Response(200, json={"status_code": estado}),
        )
        publish = router.post(f"{_base()}/{IG_USER_ID}/media_publish")
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="Mala de bordo",
            video_url=VIDEO_URL_PUBLICA,
        )

    assert publish.call_count == 0
    assert res.ok is False
    assert res.container_id == CONTAINER_ID
    assert res.post_external_id is None
    assert trecho in (res.erro or "")


async def test_instagram_estouro_do_teto_de_poll_devolve_o_container_sem_publicar(
    video: Path, dormidas: list[float]
) -> None:
    """Vídeo que não termina de transcodificar dentro do teto não vira falha
    definitiva nem publicação: o ciclo acaba, o container volta pro worker e a
    reconciliação decide depois. Publicar aqui seria apostar que a Meta terminou."""
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(
            return_value=httpx.Response(200, json={"id": CONTAINER_ID}),
        )
        status = router.get(f"{_base()}/{CONTAINER_ID}").mock(
            return_value=httpx.Response(200, json={"status_code": "IN_PROGRESS"}),
        )
        publish = router.post(f"{_base()}/{IG_USER_ID}/media_publish")
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="Mala de bordo",
            video_url=VIDEO_URL_PUBLICA,
        )

    # Teto de 5 min / cadência de 1 min = 6 consultas (a 1ª imediata) e 5 esperas.
    esperadas = int(meta_client.IG_POLL_MAX_S // meta_client.IG_POLL_INTERVALO_S) + 1
    assert status.call_count == esperadas
    assert dormidas == [meta_client.IG_POLL_INTERVALO_S] * (esperadas - 1)

    assert publish.call_count == 0
    assert res.ok is False
    assert res.container_id == CONTAINER_ID
    assert "processando" in (res.erro or "")


# ─────────────────────────── erros e sigilo do token ────────────────────────


def test_erro_da_meta_vira_meta_error_traduzido_por_code_e_por_subcode() -> None:
    """A Meta fala em inglês e por número; o operador lê a tela em português.
    `error_subcode` é mais específico que `code` e por isso ganha dele."""
    with pytest.raises(MetaError) as por_code:
        meta_client._resposta_json(
            httpx.Response(400, json={"error": {"code": 190, "message": "Invalid OAuth token"}}),
            token=TOKEN,
        )
    assert por_code.value.code == 190
    assert "reconecte a conta" in str(por_code.value)

    with pytest.raises(MetaError) as por_subcode:
        meta_client._resposta_json(
            httpx.Response(
                400,
                json={"error": {"code": 100, "error_subcode": 2207026, "message": "bad format"}},
            ),
            token=TOKEN,
        )
    assert por_subcode.value.subcode == 2207026
    # 100 sozinho seria o genérico "parâmetro inválido"; o subcode diz o que fazer.
    assert "formato de vídeo não suportado" in str(por_subcode.value)


def test_token_nunca_aparece_na_excecao_mesmo_quando_a_meta_devolve_ele() -> None:
    """A Meta ECOA o token no `message` de erro de OAuth. Se a gente repassasse a
    mensagem crua, o token cairia em `marketing_postagens.result`, na tela e no
    Sentry — vazamento de credencial por caminho de erro."""
    with pytest.raises(MetaError) as exc:
        meta_client._resposta_json(
            httpx.Response(
                400,
                json={
                    "error": {
                        "code": 4242,  # fora da tabela: cai no fallback com a mensagem crua
                        "message": f"Invalid OAuth access token {TOKEN}",
                        "type": "OAuthException",
                    }
                },
            ),
            token=TOKEN,
        )
    assert TOKEN not in str(exc.value)
    assert TOKEN not in repr(exc.value)
    assert "***" in str(exc.value)


async def test_token_nao_vaza_no_resultado_nem_no_log_da_falha(
    video: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Fim a fim: token fora do `erro` que o worker grava e fora de todo log —
    o structlog do módulo e o logging da stdlib."""
    caplog.set_level(logging.DEBUG)
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "code": 4242,
                        "message": f"Invalid OAuth access token {TOKEN}",
                        "type": "OAuthException",
                    }
                },
            ),
        )
        with structlog.testing.capture_logs() as logs:
            res = await publicar_reel_instagram(
                ig_user_id=IG_USER_ID,
                token=TOKEN,
                video_path=video,
                legenda="Mala de bordo",
                video_url=VIDEO_URL_PUBLICA,
            )
        urls = [str(c.request.url) for c in router.calls]

    assert res.ok is False
    # Falhou ANTES de existir container: não há nada pra reconciliação consultar.
    assert res.container_id is None
    assert TOKEN not in (res.erro or "")
    assert "***" in (res.erro or "")
    assert TOKEN not in str(logs)
    assert TOKEN not in caplog.text
    assert all(TOKEN not in u for u in urls)


# ══════════════════════════════════════════════════════════════════════════
# Regressões da revisão adversarial (15/09/2026).
# ══════════════════════════════════════════════════════════════════════════


def _base_ig() -> str:
    """`https://graph.instagram.com/<versão>` — trilha Instagram Login."""
    return f"{meta_client.GRAPH_HOST_IG}/{meta_client.graph_version()}"


async def test_timeout_depois_do_publish_e_ambiguo_e_nao_falha(
    video: Path, dormidas: list[float]
) -> None:
    """O erro mais caro do robô: `media_publish` SAIU e a resposta se perdeu.

    Tratar isso como "falhou" libera o retry — e o retry publica o segundo
    Reel no perfil da marca. O resultado tem que vir marcado `ambiguo`, que é
    o que manda o worker pra `revisar` em vez de pra fila.
    """
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(
            return_value=httpx.Response(200, json={"id": CONTAINER_ID})
        )
        router.get(f"{_base()}/{CONTAINER_ID}").mock(
            return_value=httpx.Response(200, json={"status_code": "FINISHED"})
        )
        router.post(f"{_base()}/{IG_USER_ID}/media_publish").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="x",
            video_url=VIDEO_URL_PUBLICA,
        )

    assert res.ok is False
    assert res.ambiguo is True, "timeout DEPOIS do publish não prova que não saiu"
    assert res.container_id == CONTAINER_ID


async def test_falha_antes_do_publish_nao_e_ambigua(
    video: Path, dormidas: list[float]
) -> None:
    """Contraprova: cair ANTES do passo que publica é falha comum, e falha
    comum pode ser retentada — senão toda intermitência viraria trabalho
    manual."""
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(
            side_effect=httpx.ConnectError("sem rede")
        )
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="x",
            video_url=VIDEO_URL_PUBLICA,
        )
    assert res.ok is False
    assert res.ambiguo is False


async def test_recusa_explicita_da_meta_no_publish_nao_e_ambigua(
    video: Path, dormidas: list[float]
) -> None:
    """Erro COM código da Meta é resposta definitiva de "não publiquei" — aí
    `ambiguo` seria mandar pro operador uma dúvida que não existe."""
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(
            return_value=httpx.Response(200, json={"id": CONTAINER_ID})
        )
        router.get(f"{_base()}/{CONTAINER_ID}").mock(
            return_value=httpx.Response(200, json={"status_code": "FINISHED"})
        )
        router.post(f"{_base()}/{IG_USER_ID}/media_publish").mock(
            return_value=httpx.Response(
                400, json={"error": {"code": 190, "message": "token"}}
            )
        )
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="x",
            video_url=VIDEO_URL_PUBLICA,
        )
    assert res.ok is False
    assert res.ambiguo is False


async def test_link_assinado_nao_sobra_na_mensagem_de_erro(
    video: Path, dormidas: list[float]
) -> None:
    """A Meta devolve a `video_url` inteira dentro da mensagem de erro, e esse
    texto vira o `result` que aparece na tela. O link baixa o criativo sem
    sessão nenhuma — ele tem que ser redigido junto com o token."""
    link = "https://davinci.exemplo/api/marketing/creatives/video/abc.def-ghi"
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{_base()}/{IG_USER_ID}/media").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "code": 100,
                        "message": f"could not fetch {link} for {TOKEN}",
                    }
                },
            )
        )
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="x",
            video_url=link,
        )
    assert res.ok is False
    assert link not in (res.erro or "")
    assert TOKEN not in (res.erro or "")


async def test_trilha_instagram_login_fala_com_graph_instagram(
    video: Path, dormidas: list[float]
) -> None:
    """Conta SEM Página: o host é graph.instagram.com, do container ao publish.

    Quem decide é a ORIGEM DO TOKEN, não a plataforma da linha — token da
    trilha Instagram Login mandado pro graph.facebook.com volta "sem permissão",
    erro que parece conta errada e não é.
    """
    with respx.mock(assert_all_called=True) as router:
        media = router.post(f"{_base_ig()}/{IG_USER_ID}/media").mock(
            return_value=httpx.Response(200, json={"id": CONTAINER_ID})
        )
        router.get(f"{_base_ig()}/{CONTAINER_ID}").mock(
            return_value=httpx.Response(200, json={"status_code": "FINISHED"})
        )
        publish = router.post(f"{_base_ig()}/{IG_USER_ID}/media_publish").mock(
            return_value=httpx.Response(200, json={"id": MEDIA_ID})
        )
        router.get(f"{_base_ig()}/{MEDIA_ID}").mock(
            return_value=httpx.Response(200, json={"permalink": "https://insta/x"})
        )
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="x",
            video_url=VIDEO_URL_PUBLICA,
            provedor=meta_client.PROVEDOR_INSTAGRAM,
        )
    assert res.ok is True
    assert media.call_count == 1 and publish.call_count == 1


async def test_trilha_instagram_login_sem_link_recusa_antes_de_gastar_container(
    video: Path,
) -> None:
    """rupload é exclusivo da trilha com Facebook Login: sem link não há como
    publicar, e tentar gastaria um container pra voltar um erro de permissão
    que não explica nada."""
    with respx.mock(assert_all_called=False) as router:
        media = router.post(f"{_base_ig()}/{IG_USER_ID}/media")
        res = await publicar_reel_instagram(
            ig_user_id=IG_USER_ID,
            token=TOKEN,
            video_path=video,
            legenda="x",
            video_url=None,
            provedor=meta_client.PROVEDOR_INSTAGRAM,
        )
    assert res.ok is False
    assert media.call_count == 0
    assert "link" in (res.erro or "")


async def test_consulta_do_facebook_nao_aceita_permalink_como_prova() -> None:
    """Vídeo agendado JÁ TEM `permalink_url`. Tratar o link como prova de que
    saiu marcaria como publicado o que ainda não foi ao ar — e o operador
    deixaria de acompanhar."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base()}/{VIDEO_ID}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "permalink_url": "/charlots/videos/1",
                    "status": {
                        "publishing_phase": {"status": "scheduled"},
                        "video_status": "processing",
                    },
                },
            )
        )
        res = await meta_client.consultar_publicacao(
            plataforma=meta_client.PLATAFORMA_FACEBOOK,
            token=TOKEN,
            post_external_id=VIDEO_ID,
        )
    assert res.ok is False
    assert res.ambiguo is True, "link sem 'complete' é dúvida, não ausência"


async def test_consulta_que_nao_responde_e_duvida_e_nao_ausencia() -> None:
    """`ok=False` por timeout seria lido como "não publicou" — e o retry
    publicaria de novo. Sem resposta = ambíguo, ponto."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{_base()}/{MEDIA_ID}").mock(side_effect=httpx.ReadTimeout("t"))
        res = await meta_client.consultar_publicacao(
            plataforma=meta_client.PLATAFORMA_INSTAGRAM,
            token=TOKEN,
            post_external_id=MEDIA_ID,
        )
    assert res.ok is False
    assert res.ambiguo is True


async def test_validade_do_token_le_o_debug_token() -> None:
    """Sem `token_expires_at` preenchido o cron de renovação não tem o que
    achar: o token morre calado e a primeira notícia é um post que não saiu."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base()}/debug_token").mock(
            return_value=httpx.Response(
                200, json={"data": {"expires_at": 1793000000, "is_valid": True}}
            )
        )
        quando = await meta_client.validade_do_token(TOKEN)
    assert quando is not None
    assert int(quando.timestamp()) == 1793000000


async def test_validade_do_token_permanente_e_none() -> None:
    """`expires_at = 0` é System User token que não expira."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base()}/debug_token").mock(
            return_value=httpx.Response(200, json={"data": {"expires_at": 0}})
        )
        assert await meta_client.validade_do_token(TOKEN) is None
