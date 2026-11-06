"""O SAC do site ligado logo depois da ponte do e-mail (06/11/2026)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import worker
from app.services import garantia_sac


class _Sessao:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _preparar(monkeypatch, ligada: bool):
    sessao = _Sessao()
    chamadas: list[datetime | None] = []

    @asynccontextmanager
    async def _scope():
        yield sessao

    async def _sincronizar(s, desde=None):
        assert s is sessao
        chamadas.append(desde)
        return {"casos_novos": 1, "ligados": 1, "desligados": 0}

    monkeypatch.setattr(worker, "session_scope", _scope)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(garantia_automatica=ligada))
    monkeypatch.setattr(garantia_sac, "sincronizar_sac", _sincronizar)
    return sessao, chamadas


@pytest.mark.asyncio
async def test_liga_o_sac_dos_ultimos_10_minutos_e_commita(monkeypatch):
    sessao, chamadas = _preparar(monkeypatch, ligada=True)
    await worker._sac_na_hora()
    assert sessao.commits == 1
    assert len(chamadas) == 1
    desde = chamadas[0]
    assert desde is not None
    assert timedelta(minutes=9) < datetime.now(UTC) - desde < timedelta(minutes=11)


@pytest.mark.asyncio
async def test_sem_a_garantia_automatica_nao_faz_nada(monkeypatch):
    sessao, chamadas = _preparar(monkeypatch, ligada=False)
    await worker._sac_na_hora()
    assert (sessao.commits, chamadas) == (0, [])


@pytest.mark.asyncio
async def test_falha_nao_derruba_a_ponte(monkeypatch):
    _preparar(monkeypatch, ligada=True)

    async def _quebra(s, desde=None):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(garantia_sac, "sincronizar_sac", _quebra)
    await worker._sac_na_hora()  # não levanta


@pytest.mark.parametrize(
    ("nome", "papel"),
    [
        ("US-26-0005-01-produto.jpg", "Foto do produto"),
        ("US-26-0005-02-etiqueta.jpg", "Foto da etiqueta"),
        ("US-26-0005-03-defeito-1.jpg", "Foto do defeito 1"),
        ("US-26-0005-04-defeito-2.JPG", "Foto do defeito 2"),
        ("US-26-0005-05-video.mp4", "Vídeo"),
        ("US-26-0012-03-embalagem.png", "Foto da embalagem"),
        ("US-26-0012-04-comprovante.pdf", "Comprovante"),
        ("IMG_0001.jpg", None),
        (None, None),
    ],
)
def test_papel_pelo_nome_que_o_site_da(nome, papel):
    """O site renomeia cada anexo pelo protocolo e pelo campo (teste de 06/11/2026)."""
    assert garantia_sac.papel_do_arquivo(nome) == papel
