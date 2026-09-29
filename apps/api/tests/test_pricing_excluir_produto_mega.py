# ruff: noqa: F811 — `editor` é fixture importada do teste das embalagens
"""Excluir produto da Tabela de Preços leva a pasta do MEGA para a lixeira.

Eduardo, 29/09/2026: "quando eu remover um produto, ele tem que remover do
MEGA também". Vai para a LIXEIRA do MEGA (recuperável), e só quando nenhuma
outra linha usa a mesma pasta. O sidecar é o de verdade (infra/megacmd/app.py)
com o MEGAcmd de mentira de test_pricing_mega_embalagens.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PricingProduct, User
from app.services.mega_fotos import MegaError
from tests.test_pricing_mega_embalagens import (  # noqa: F401 — fixtures
    PERM_FULL,
    SIDECAR,
    MegaFalso,
    _produto,
    _usuario,
    editor,
)


class MegaComLixeira(MegaFalso):
    """O fake de sempre + `mega-ls -l` no formato real (com a hora) e `mega-mv`."""

    def __init__(self) -> None:
        super().__init__()
        self.lixeira: list[tuple[str, str]] = []

    def run(self, cmd: list[str], *, timeout: int = 0, input_text: str | None = None):
        if cmd[0] == "mega-ls" and "-l" in cmd:
            raiz = cmd[-1]
            if raiz not in self.pastas:
                return 1, "not found"
            linhas = ["FLAGS VERS      SIZE            DATE       NAME"]
            for f in self._filhos(raiz):
                nome = f.rsplit("/", 1)[-1]
                if f in self.pastas:
                    linhas.append(f"d---    -            - 07Sep2026 20:14:04 {nome}")
                else:
                    linhas.append(f"----    1         1000 07Sep2026 20:14:04 {nome}")
            return 0, "\n".join(linhas)
        if cmd[0] == "mega-mv":
            origem, destino = cmd[1], cmd[2]
            self.lixeira.append((origem, destino))
            pref = origem + "/"
            self.pastas = {p for p in self.pastas if p != origem and not p.startswith(pref)}
            self.arquivos = {k: v for k, v in self.arquivos.items() if not k.startswith(pref)}
            return 0, ""
        return super().run(cmd, timeout=timeout, input_text=input_text)

    async def request(self, method: str, path: str, **kw: Any) -> dict:
        if path == "/lixeira":
            self.chamadas.append((method, path))
            if self.fora_do_ar:
                raise MegaError("sidecar MEGA inacessível: fora do ar", 503)
            try:
                return SIDECAR.lixeira(SIDECAR.LixeiraIn(path=kw["json"]["path"]), None)
            except HTTPException as exc:
                raise MegaError(str(exc.detail), exc.status_code) from exc
        return await super().request(method, path, **kw)


@pytest.fixture
def mega(monkeypatch) -> MegaComLixeira:
    m = MegaComLixeira()
    monkeypatch.setattr(SIDECAR, "run", m.run)
    for mod in ("app.routers.pricing_mega", "app.services.mega_midias", "app.routers.pricing"):
        monkeypatch.setattr(f"{mod}.sidecar_request", m.request)
    monkeypatch.setattr(get_settings(), "mega_fotos_root", "/")
    return m


async def _existe(db: AsyncSession, pid) -> bool:
    db.expire_all()
    return (
        await db.execute(select(PricingProduct.id).where(PricingProduct.id == pid))
    ).scalar_one_or_none() is not None


async def test_unico_dono_pasta_vai_para_a_lixeira(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaComLixeira,
):
    mega.arquivo("/Celular/Oukitel G1/a.jpg", "/Celular/Oukitel G1/Embalagens/caixa.pdf")
    p = await _produto(db, editor, nome="G1", pasta="/Celular/Oukitel G1")

    r = await client.delete(f"/api/pricing/products/{p.id}")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["mega"] == "lixeira" and corpo["pasta"] == "/Celular/Oukitel G1"
    assert not await _existe(db, p.id)
    [(origem, destino)] = mega.lixeira
    assert origem == "/Celular/Oukitel G1"
    assert destino.startswith("//bin/Oukitel G1 (excluído no DaVinci ")
    assert "/Celular/Oukitel G1" not in mega.pastas  # levou a subpasta de embalagens junto


async def test_pasta_dividida_fica_no_mega(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaComLixeira,
):
    """S7 16.128 e S7 32.256 dividem a pasta: excluir uma linha não apaga a outra."""
    mega.arquivo("/Celular/Fossibot S7/a.jpg")
    p1 = await _produto(db, editor, nome="S7 16", pasta="/Celular/Fossibot S7")
    p2 = await _produto(db, editor, nome="S7 32", pasta="/Celular/Fossibot S7")

    r = await client.delete(f"/api/pricing/products/{p1.id}")
    assert r.status_code == 200
    assert r.json()["mega"] == "mantida" and r.json()["usada_por"] == [p2.sku]
    assert mega.lixeira == [] and "/Celular/Fossibot S7" in mega.pastas

    # A última linha leva a pasta.
    r = await client.delete(f"/api/pricing/products/{p2.id}")
    assert r.json()["mega"] == "lixeira"
    assert [o for o, _d in mega.lixeira] == ["/Celular/Fossibot S7"]


async def test_pasta_usada_por_outro_usuario_tambem_fica(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaComLixeira,
):
    mega.arquivo("/Malas/ABS 12/a.jpg")
    outro = await _usuario(db, PERM_FULL)
    await _produto(db, outro, nome="ABS 12 (outro)", raiz="mala", pasta="/Malas/ABS 12")
    p = await _produto(db, editor, nome="ABS 12", raiz="mala", pasta="/Malas/ABS 12")
    r = await client.delete(f"/api/pricing/products/{p.id}")
    assert r.json()["mega"] == "mantida"
    assert mega.lixeira == []


async def test_sem_pasta_e_fora_das_raizes_nao_mexem_no_mega(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaComLixeira,
):
    p = await _produto(db, editor, nome="Sem pasta")
    r = await client.delete(f"/api/pricing/products/{p.id}")
    assert r.status_code == 200 and r.json()["mega"] == "sem_pasta"

    mega.pasta("/_geral/CONTABIL - servidor")
    p = await _produto(db, editor, nome="Estranho", pasta="/_geral/CONTABIL - servidor")
    r = await client.delete(f"/api/pricing/products/{p.id}")
    assert r.json()["mega"] == "fora_das_raizes"
    assert mega.lixeira == [] and "/_geral/CONTABIL - servidor" in mega.pastas


async def test_mega_fora_do_ar_nao_impede_excluir(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaComLixeira,
):
    mega.arquivo("/Celular/Oukitel C2/a.jpg")
    p = await _produto(db, editor, nome="C2", pasta="/Celular/Oukitel C2")
    mega.fora_do_ar = True
    r = await client.delete(f"/api/pricing/products/{p.id}")
    assert r.status_code == 200
    assert r.json()["mega"] == "erro"
    assert not await _existe(db, p.id)


async def test_pasta_que_ja_sumiu_do_mega(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaComLixeira,
):
    mega.pasta("/Celular")
    p = await _produto(db, editor, nome="Sumiu", pasta="/Celular/Sumiu")
    r = await client.delete(f"/api/pricing/products/{p.id}")
    assert r.status_code == 200 and r.json()["mega"] == "erro"
    assert mega.lixeira == []


def test_sidecar_lixeira_recusa_raiz_curinga_e_arquivo(monkeypatch):
    m = MegaComLixeira()
    m.arquivo("/Celular/X/foto.jpg")
    monkeypatch.setattr(SIDECAR, "run", m.run)
    for ruim in ("/Celular", "/", "/Celular/*", "/Celular/../x", "/Celular/./X"):
        with pytest.raises(HTTPException) as e:
            SIDECAR.lixeira(SIDECAR.LixeiraIn(path=ruim), None)
        assert e.value.status_code == 400, ruim
    # Arquivo não é pasta: não vai para a lixeira por aqui.
    with pytest.raises(HTTPException) as e:
        SIDECAR.lixeira(SIDECAR.LixeiraIn(path="/Celular/X/foto.jpg"), None)
    assert e.value.status_code == 404
    assert m.lixeira == []
    r = SIDECAR.lixeira(SIDECAR.LixeiraIn(path="/Celular/X"), None)
    assert r["ok"] and m.lixeira[0][0] == "/Celular/X"


async def test_quem_nao_pode_excluir_nao_mexe_no_mega(
    client: AsyncClient, db: AsyncSession, auth_as, mega: MegaComLixeira,
):
    dono = await _usuario(db, PERM_FULL)
    mega.arquivo("/Celular/Y/a.jpg")
    p = await _produto(db, dono, nome="Y", pasta="/Celular/Y")
    sem = await _usuario(
        db, {"tabela_precos_produtos": {"view": True, "edit": True, "delete": False}}
    )
    auth_as(sem)
    r = await client.delete(f"/api/pricing/products/{p.id}")
    assert r.status_code == 403
    assert mega.lixeira == []
