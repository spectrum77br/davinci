"""Link assinado do vídeo do criativo: token HMAC + a rota pública que a Meta baixa.

Por que este arquivo existe: quando a marca não tem Página do Facebook, o robô
publica o Reel passando `video_url` — e a Meta BAIXA o arquivo desse endereço,
sem cookie nenhum. Ou seja, é a única porta do DaVinci que serve conteúdo sem
sessão. Então os dois lados precisam de teste:

  • o token (HMAC-SHA256 truncado em 128 bits sobre `{file_id}:{exp}`): só abre
    o arquivo certo, só por 15 min, e não aceita assinatura remendada;
  • a rota: devolve o arquivo pra quem não está logado (senão a Meta não baixa)
    e devolve 404 — nunca 401, nunca listagem — pra todo o resto.

Criativo é material NÃO lançado: um token que vaze ou que valha pra sempre é um
vídeo de campanha na mão de qualquer um.
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.main import app
from app.models.marketing import MarketingCreative, MarketingCreativeFile
from app.routers import marketing_creatives as creatives_router
from app.services.marketing.link_criativo import (
    VALIDADE_S,
    _assinar,
    _b64e,
    gerar_token,
    url_video,
    validar_token,
)

# `enable_marketing` nasce False (config.py), então o main.py não monta os
# routers de Marketing e a rota do link nem existiria no app de teste. Montamos
# aqui, uma vez só, pra exercitar a ROTA de verdade — o roteamento do FastAPI
# faz parte do que a Meta vai encontrar.
if not any(str(getattr(r, "path", "")).endswith("/video/{token}") for r in app.routes):
    app.include_router(creatives_router.router)

# Caminho real da rota, lido do próprio app: o teste não pode "saber" o prefixo
# por fora, senão deixa de perceber quando ele muda.
ROTA_VIDEO = next(
    str(r.path) for r in app.routes if str(getattr(r, "path", "")).endswith("/video/{token}")
)
PREFIXO_VIDEO = ROTA_VIDEO.replace("{token}", "")

CONTEUDO = b"\x00\x01FAKE-MP4-BYTES" * 32


@pytest.fixture(autouse=True)
async def _limpa_criativos(db):
    """`marketing_creatives` não está no _CLEANUP_TABLES do conftest; limpamos
    aqui pra uma linha de um teste não virar lixo do próximo."""
    async def _wipe() -> None:
        await db.execute(text("DELETE FROM marketing_creative_files"))
        await db.execute(text("DELETE FROM marketing_creatives"))
        await db.commit()

    await _wipe()
    yield
    await _wipe()


@pytest.fixture
async def arquivo(db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Criativo + arquivo, no banco E no disco (com `uploads_dir` apontando pro
    tmp_path): a rota confere as duas coisas."""
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    criativo = MarketingCreative(modelo="Mala 20kg", marca="Yins", sku="b042.30", aprovado=True)
    db.add(criativo)
    await db.flush()
    rec = MarketingCreativeFile(
        creative_id=criativo.id,
        file_name="reel.mp4",
        file_mime="video/mp4",
        file_size=len(CONTEUDO),
        file_rel=f"creatives/{criativo.id}/reel.mp4",
    )
    db.add(rec)
    await db.commit()
    caminho = tmp_path / rec.file_rel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(CONTEUDO)
    return rec, caminho


# ──────────────────────────────── o token ───────────────────────────────────


def test_token_valido_devolve_o_file_id_que_foi_assinado() -> None:
    """Ida e volta: é o file_id que diz qual arquivo servir, e ele tem que
    sobreviver ao base64url sem padding."""
    file_id = uuid4()
    assert validar_token(gerar_token(file_id)) == file_id
    # Aceita o id como string também — é assim que o publicador chama.
    assert validar_token(gerar_token(str(file_id))) == file_id


def test_assinatura_adulterada_nao_vale() -> None:
    """Sem isto, qualquer um trocaria o file_id do payload e baixaria OUTRO
    criativo — é a assinatura, e não o sigilo da URL, que segura a porta."""
    file_id = uuid4()
    payload_b64, sig_b64 = gerar_token(file_id).split(".", 1)

    # Assinatura mexida (um caractere), mantendo o formato.
    trocado = "B" if sig_b64[0] != "B" else "C"
    assert validar_token(f"{payload_b64}.{trocado}{sig_b64[1:]}") is None

    # Payload trocado por outro file_id, reaproveitando a assinatura antiga.
    outro = _b64e(f"{uuid4()}:{int(time.time()) + VALIDADE_S}".encode())
    assert validar_token(f"{outro}.{sig_b64}") is None

    # Lixo que nem tem o formato "payload.assinatura".
    assert validar_token("sem-ponto-nenhum") is None


def test_token_expirado_nao_vale() -> None:
    """15 min é o tempo de a Meta baixar o vídeo, não o tempo de vida do link.
    O `agora` injetado envelhece o token sem precisar esperar."""
    file_id = uuid4()
    passado = int(time.time()) - VALIDADE_S - 60
    vencido = gerar_token(file_id, agora=passado)
    assert validar_token(vencido) is None

    # Um segundo antes de vencer ainda vale — a borda não pode matar o link cedo.
    na_borda = gerar_token(file_id, agora=int(time.time()) - VALIDADE_S + 30)
    assert validar_token(na_borda) == file_id


def test_file_id_que_nao_e_uuid_nao_vale_nem_bem_assinado() -> None:
    """Payload assinado por NÓS, mas com um file_id que não é UUID: a validação
    não pode devolver uma string que viraria filtro solto no banco."""
    payload = f"nao-e-uuid:{int(time.time()) + VALIDADE_S}"
    token = f"{_b64e(payload.encode())}.{_b64e(_assinar(payload))}"
    assert validar_token(token) is None


def test_url_video_monta_o_endereco_publico_a_partir_do_app_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Meta baixa de fora: o link tem que sair com o domínio público (APP_URL),
    não com o localhost do processo. A barra sobrando no setting não pode virar
    "//api/..." (a Meta segue o link literalmente)."""
    monkeypatch.setattr(get_settings(), "app_url", "https://davinci.exemplo.com/")
    file_id = uuid4()
    url = url_video(file_id)

    partes = urlparse(url)
    assert partes.scheme == "https"
    assert partes.netloc == "davinci.exemplo.com"
    assert "//api" not in partes.path
    # O token que viajou na URL é o que reabre o arquivo certo.
    assert validar_token(partes.path.rsplit("/", 1)[-1]) == file_id


def test_url_video_aponta_pra_rota_que_o_app_realmente_serve() -> None:
    """O contrato entre as duas metades: o endereço que o publicador manda pra
    Meta tem que ser o endereço que o FastAPI atende.

    Se este teste falha, o caminho por LINK do Instagram (o padrão hoje, com
    `marketing_postagem_upload="link"`) morre em 404 na Meta — e o erro chega
    disfarçado de "a Meta não conseguiu baixar o vídeo"."""
    caminho = urlparse(url_video(uuid4())).path
    assert caminho.startswith(PREFIXO_VIDEO), (
        f"url_video() gera {caminho!r}, mas o app serve {ROTA_VIDEO!r}"
    )


# ──────────────────────────────── a rota ────────────────────────────────────


async def test_video_publico_serve_o_arquivo_sem_sessao(client, arquivo, auth_as) -> None:
    """O ponto da feature: SEM login a Meta recebe 200 e os bytes do vídeo. Se
    esta rota exigisse sessão, a marca sem Página do Facebook nunca publicaria."""
    auth_as(None)  # nenhuma dependência de auth sobrescrita: visitante anônimo
    rec, _ = arquivo

    resp = await client.get(f"{PREFIXO_VIDEO}{gerar_token(rec.id)}")

    assert resp.status_code == 200
    assert resp.content == CONTEUDO
    assert resp.headers["content-type"] == "video/mp4"
    # Cinto de segurança contra o navegador adivinhar o tipo do arquivo.
    assert resp.headers["x-content-type-options"] == "nosniff"


async def test_video_publico_nao_exige_login_mas_o_download_normal_exige(
    client, arquivo, auth_as
) -> None:
    """Contraste no MESMO estado (anônimo): o link assinado abre, o download de
    sempre continua fechado. É o que garante que a porta aberta é só esta, e só
    pra quem tem o token."""
    auth_as(None)
    rec, _ = arquivo

    publico = await client.get(f"{PREFIXO_VIDEO}{gerar_token(rec.id)}")
    privado = await client.get(f"/api/marketing/creatives/{rec.creative_id}/arquivo/{rec.id}")

    assert publico.status_code == 200
    assert privado.status_code == 401
    assert privado.json()["detail"]["code"] == "unauthenticated"


@pytest.mark.parametrize("caso", ["adulterado", "expirado"])
async def test_token_invalido_ou_vencido_devolve_404(client, arquivo, auth_as, caso: str) -> None:
    """404 (e não 401/403) de propósito: pra quem bate na porta com token ruim, a
    rota não confirma nem que aquele arquivo existe."""
    auth_as(None)
    rec, _ = arquivo
    if caso == "adulterado":
        payload_b64, sig_b64 = gerar_token(rec.id).split(".", 1)
        trocado = "B" if sig_b64[0] != "B" else "C"
        token = f"{payload_b64}.{trocado}{sig_b64[1:]}"
    else:
        token = gerar_token(rec.id, agora=int(time.time()) - VALIDADE_S - 60)

    resp = await client.get(f"{PREFIXO_VIDEO}{token}")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "link_invalido_ou_expirado"


async def test_arquivo_que_sumiu_do_disco_devolve_404(client, arquivo, auth_as) -> None:
    """Linha no banco sem arquivo no disco (upload perdido, volume remontado):
    404 com código próprio, e não um 500 que a Meta leria como erro nosso."""
    auth_as(None)
    rec, caminho = arquivo
    caminho.unlink()

    resp = await client.get(f"{PREFIXO_VIDEO}{gerar_token(rec.id)}")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "arquivo_sumiu"


async def test_token_de_file_id_inexistente_devolve_404(client, arquivo, auth_as) -> None:
    """Token nosso, bem assinado, mas apontando pra um id que não existe: a rota
    não pode estourar — 404 limpo."""
    auth_as(None)

    resp = await client.get(f"{PREFIXO_VIDEO}{gerar_token(uuid4())}")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "arquivo_nao_encontrado"
