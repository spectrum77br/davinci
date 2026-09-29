"""Embalagens no MEGA + a arrumação das fotos da Tabela de Preços (29/09/2026).

Eduardo: "um campo do lado de fotos, chamado EMBALAGENS, onde vamos concentrar
todas as nossas fotos de embalagens, caixa etc, vai ir para o mega normal,
mesmo processo de fotos, só que um campo separado".

O MEGA aqui é FALSO, mas o sidecar é o de verdade: as rotas do sidecar
(infra/megacmd/app.py) rodam com o `run` trocado por uma árvore de pastas em
memória — é a mesma `_classificar` que vai para produção que decide o que é
foto, vídeo e embalagem nestes testes. Só o /upload e o /file (que mexem com
arquivo em disco) são imitados.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PricingProduct, Segment, User, UserRole, UserStatus
from app.models.enums import Department
from app.services.mega_fotos import MegaError

# ─────────────── o sidecar de verdade, carregado pelo caminho ───────────────

_SIDECAR_PATH = Path(__file__).resolve().parents[3] / "infra" / "megacmd" / "app.py"


def _carregar_sidecar():
    spec = importlib.util.spec_from_file_location("megacmd_sidecar_app", _SIDECAR_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["megacmd_sidecar_app"] = mod
    spec.loader.exec_module(mod)
    return mod


SIDECAR = _carregar_sidecar()


class MegaFalso:
    """Conta MEGA em memória + o sidecar real por cima dela."""

    def __init__(self) -> None:
        self.pastas: set[str] = {"/"}
        self.arquivos: dict[str, bytes] = {}
        self.chamadas: list[tuple[str, str]] = []
        self.exportados: list[str] = []
        # Simula o container ANTES do rebuild: /media_counts sem embalagens.
        self.formato_antigo = False
        self.fora_do_ar = False
        self.thumb = "ok"
        self.lados: list[int] = []

    # --- montar a árvore ---
    def pasta(self, *paths: str) -> None:
        for p in paths:
            partes = [x for x in p.split("/") if x]
            for i in range(1, len(partes) + 1):
                self.pastas.add("/" + "/".join(partes[:i]))

    def arquivo(self, *paths: str, conteudo: bytes = b"bytes") -> None:
        for p in paths:
            pai = p.rsplit("/", 1)[0]
            if pai:
                self.pasta(pai)
            self.arquivos[p] = conteudo

    # --- o MEGAcmd de mentira que o sidecar chama ---
    def _nos(self) -> list[str]:
        return sorted({*self.pastas, *self.arquivos} - {"/"})

    def _filhos(self, raiz: str) -> list[str]:
        pref = raiz.rstrip("/") + "/"
        return [n for n in self._nos() if n.startswith(pref) and "/" not in n[len(pref) :]]

    def run(self, cmd: list[str], *, timeout: int = 0, input_text: str | None = None):
        prog = cmd[0]
        if prog == "mega-find":
            raiz = cmd[1]
            if raiz not in self.pastas:
                return 1, f"[err: find] Couldn't find {raiz}"
            pref = raiz.rstrip("/") + "/"
            linhas = [n for n in self._nos() if n.startswith(pref)] + [raiz]
            return 0, "\n".join(linhas)
        if prog == "mega-ls":
            longo = "-l" in cmd
            raiz = cmd[-1]
            if raiz not in self.pastas:
                return 1, "not found"
            filhos = self._filhos(raiz)
            nomes = [f.rsplit("/", 1)[-1] for f in filhos]
            if not longo:
                return 0, "\n".join(nomes)
            linhas = ["FLAGS VERS SIZE DATE NAME"] + [
                ("d---    -    - 29Sep2026 " if f in self.pastas else "----    1   10 29Sep2026 ")
                + n
                for f, n in zip(filhos, nomes, strict=True)
            ]
            return 0, "\n".join(linhas)
        if prog == "mega-export":
            alvo = cmd[-1]
            if alvo not in self.pastas:
                return 1, "not found"
            return 0, f"Exported {alvo}: https://mega.nz/folder/L{len(alvo)}#{abs(hash(alvo))}"
        raise AssertionError(f"comando inesperado: {cmd}")

    # --- a API HTTP do sidecar, como o DaVinci a vê ---
    async def request(self, method: str, path: str, *, json: Any = None, params: Any = None,
                      data: Any = None, files: Any = None, timeout: float = 0) -> dict:
        self.chamadas.append((method, path))
        if self.fora_do_ar:
            raise MegaError("sidecar MEGA inacessível: fora do ar", 503)
        try:
            if path == "/upload":
                dest = data["dest"]
                self.pasta(dest)
                for _campo, (nome, fh, _ct) in files:
                    self.arquivos[f"{dest}/{nome}"] = fh.read()
                return {"uploaded": len(files), "dest": dest}
            if path == "/export":
                self.exportados.append(json["path"])
                return SIDECAR.export(SIDECAR.ExportIn(path=json["path"]), None)
            if path == "/media_counts":
                out = SIDECAR.media_counts_one(params["path"], None)
                if self.formato_antigo:
                    out.pop("embalagens")
                    out.pop("embalagens_pasta")
                return out
            if path == "/files":
                return SIDECAR.files(params["path"], params.get("tipo", "imagens"), None)
            if path == "/folders":
                return SIDECAR.folders(
                    params["root"], params.get("depth", 1), params.get("media_counts", 0), None
                )
        except HTTPException as exc:
            raise MegaError(str(exc.detail), exc.status_code) from exc
        raise AssertionError(f"rota inesperada: {path}")

    async def bytes_(self, path: str, *, params: dict, timeout: float = 0) -> bytes:
        self.chamadas.append(("GET", path))
        alvo = params["path"]
        if path == "/thumb":
            self.lados.append(params.get("lado"))
            # thumb: "ok" reduz; "antigo" = container sem a rota (404 do
            # FastAPI); "nao_abre" = Pillow não decodifica (422).
            if self.thumb == "antigo":
                raise MegaError("sidecar HTTP 404", 404)
            if self.thumb == "nao_abre":
                raise MegaError("sidecar HTTP 422", 422)
            if self.thumb == "fora":
                raise MegaError("sidecar MEGA inacessível", 503)
            if alvo not in self.arquivos:
                raise MegaError("sidecar HTTP 404", 404)
            return b"MINI:" + self.arquivos[alvo]
        if alvo not in self.arquivos:
            raise MegaError("sidecar HTTP 404", 404)
        return self.arquivos[alvo]

    async def stream(self, path: str, *, params: dict, timeout: float = 0):
        conteudo = await self.bytes_(path, params=params)

        async def pedacos():
            yield conteudo

        async def fechar() -> None:
            return None

        return pedacos(), fechar


@pytest.fixture
def mega(monkeypatch) -> MegaFalso:
    m = MegaFalso()
    monkeypatch.setattr(SIDECAR, "run", m.run)
    for mod in (
        "app.routers.pricing_mega",
        "app.services.mega_midias",
        "app.routers.portal_criativos",
    ):
        monkeypatch.setattr(f"{mod}.sidecar_request", m.request)
    monkeypatch.setattr("app.routers.pricing_mega.sidecar_stream", m.stream)
    monkeypatch.setattr("app.routers.pricing_mega.sidecar_bytes", m.bytes_)
    monkeypatch.setattr("app.services.mega_midias.sidecar_bytes", m.bytes_)
    monkeypatch.setattr("app.routers.portal_criativos.sidecar_bytes", m.bytes_)
    monkeypatch.setattr(get_settings(), "mega_fotos_root", "/")
    return m


# ─────────────── usuários, segmentos e produtos ───────────────

PERM_FULL = {"tabela_precos_produtos": {"view": True, "edit": True, "delete": True}}
PERM_VIEW = {"tabela_precos_produtos": {"view": True, "edit": False, "delete": False}}


async def _usuario(db: AsyncSession, perms: dict) -> User:
    email = f"emb-{uuid.uuid4().hex[:8]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.USER,
             status=UserStatus.ACTIVE, permissions=perms)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def editor(db: AsyncSession, auth_as: Callable[[User | None], None]) -> User:
    u = await _usuario(db, PERM_FULL)
    auth_as(u)
    return u


async def _folha(db: AsyncSession, slug_raiz: str) -> Segment:
    """Segmento folha sob a raiz `slug_raiz` — é a raiz que diz o departamento."""
    raiz = Segment(name=slug_raiz, slug=slug_raiz, sort_order=0)
    db.add(raiz)
    await db.flush()
    folha = Segment(name=f"{slug_raiz} 1", slug=f"{slug_raiz}-1", parent_id=raiz.id, sort_order=0)
    db.add(folha)
    await db.flush()
    return folha


async def _produto(db: AsyncSession, dono: User, *, nome: str, raiz: str = "celular",
                   pasta: str | None = None, url: str | None = None,
                   department: Department | None = None, **extra: Any) -> PricingProduct:
    seg = await _folha(db, raiz)
    p = PricingProduct(user_id=dono.id, segment_id=seg.id, sku=f"sku-{uuid.uuid4().hex[:6]}",
                       name=nome, fotos_path=pasta, fotos_url=url, department=department,
                       **extra)
    db.add(p)
    await db.commit()
    return p


async def _recarregar(db: AsyncSession, pid) -> PricingProduct:
    return (
        await db.execute(
            select(PricingProduct)
            .where(PricingProduct.id == pid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def _arquivos(*nomes: str) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (n, b"conteudo", "application/octet-stream")) for n in nomes]


# ─────────────── envio: pasta nasce no departamento certo ───────────────


@pytest.mark.parametrize(
    ("raiz", "department", "esperado"),
    [
        ("celular", None, "/Celular/Fossibot F117"),
        ("mala", None, "/Malas/Fossibot F117"),
        ("eletro", None, "/uranyx/Fossibot F117"),
        # Segmento raiz sem as três famílias: vale a coluna legada.
        ("outra-coisa", Department.MALA, "/Malas/Fossibot F117"),
        # Nem segmento nem coluna: como era antes (raiz da conta).
        ("outra-coisa", None, "/Fossibot F117"),
    ],
)
async def test_upload_de_fotos_sem_pasta_cria_no_departamento(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
    raiz: str, department: Department | None, esperado: str,
):
    """Antes a pasta nascia SOLTA na raiz da conta ("/Fossibot F117")."""
    p = await _produto(db, editor, nome="Fossibot F117", raiz=raiz, department=department)

    r = await client.post(
        f"/api/pricing/mega/products/{p.id}/fotos/upload", files=_arquivos("frente.jpg", "a.mp4")
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tipo"] == "fotos"
    assert body["uploaded"] == 2
    assert body["fotos_path"] == esperado
    assert body["fotos_url"].startswith("https://mega.nz/folder/")
    assert (body["fotos_count"], body["videos_count"], body["embalagens_count"]) == (1, 1, 0)
    assert body["midias_contadas_em"]
    assert f"{esperado}/frente.jpg" in mega.arquivos

    row = await _recarregar(db, p.id)
    assert row.fotos_path == esperado and row.fotos_count == 1
    assert row.midias_contadas_em is not None


async def test_upload_de_embalagens_cria_subpasta_e_vale_para_a_linha(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    """A caixa é da LINHA: os dois produtos da mesma pasta ganham o link.
    E foto de caixa não infla a contagem de fotos."""
    mega.arquivo("/Malas/ABS 12/M1 listrada/b005.jpg")
    a = await _produto(db, editor, nome="ABS 12", raiz="mala", pasta="/Malas/ABS 12",
                       url="https://mega.nz/folder/antigo")
    b = await _produto(db, editor, nome="ABS 12 kit", raiz="mala", pasta="/Malas/ABS 12",
                       url="https://mega.nz/folder/antigo")
    outro = await _produto(db, editor, nome="ABS 18", raiz="mala", pasta="/Malas/ABS 18")

    r = await client.post(
        f"/api/pricing/mega/products/{a.id}/embalagens/upload",
        files=_arquivos("caixa.pdf", "caixa frente.jpg", "arte.ai"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tipo"] == "embalagens"
    assert body["embalagens_path"] == "/Malas/ABS 12/Embalagens"
    assert body["embalagens_url"].startswith("https://mega.nz/folder/")
    # A pasta de fotos não mudou, e o link dela também não.
    assert body["fotos_path"] == "/Malas/ABS 12"
    assert body["fotos_url"] == "https://mega.nz/folder/antigo"
    # A foto da caixa conta como embalagem, não como foto.
    assert (body["fotos_count"], body["embalagens_count"]) == (1, 3)
    assert "/Malas/ABS 12/Embalagens/caixa.pdf" in mega.arquivos

    irmao = await _recarregar(db, b.id)
    assert irmao.embalagens_path == "/Malas/ABS 12/Embalagens"
    assert irmao.embalagens_url == body["embalagens_url"]
    assert (irmao.fotos_count, irmao.embalagens_count) == (1, 3)
    longe = await _recarregar(db, outro.id)
    assert longe.embalagens_path is None and longe.embalagens_count is None


async def test_embalagem_em_produto_sem_pasta_cria_as_duas(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    p = await _produto(db, editor, nome="Oukitel C3")

    r = await client.post(
        f"/api/pricing/mega/products/{p.id}/embalagens/upload", files=_arquivos("caixa.png")
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fotos_path"] == "/Celular/Oukitel C3"
    assert body["embalagens_path"] == "/Celular/Oukitel C3/Embalagens"
    assert body["fotos_url"] and body["embalagens_url"]
    assert body["fotos_url"] != body["embalagens_url"]
    assert (body["fotos_count"], body["embalagens_count"]) == (0, 1)
    assert set(mega.exportados) == {"/Celular/Oukitel C3", "/Celular/Oukitel C3/Embalagens"}


async def test_embalagem_usa_a_subpasta_feita_a_mao(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    """Pasta "embalagem" criada direto no MEGA: o envio usa ela em vez de
    criar uma SEGUNDA pasta "Embalagens" ao lado."""
    mega.arquivo("/Celular/Oscal Flat 3C/embalagem/velha.jpg")
    p = await _produto(db, editor, nome="Oscal Flat 3C", pasta="/Celular/Oscal Flat 3C",
                       url="https://mega.nz/folder/x")

    r = await client.post(
        f"/api/pricing/mega/products/{p.id}/embalagens/upload", files=_arquivos("nova.pdf")
    )
    assert r.status_code == 200, r.text
    assert r.json()["embalagens_path"] == "/Celular/Oscal Flat 3C/embalagem"
    assert r.json()["embalagens_count"] == 2
    assert "/Celular/Oscal Flat 3C/Embalagens" not in mega.pastas


@pytest.mark.parametrize(
    ("tipo", "nomes", "recusados"),
    [
        ("fotos", ["ok.jpg", "manual.pdf"], ["manual.pdf"]),
        ("fotos", ["script.html"], ["script.html"]),
        ("fotos", ["caixa.af"], ["caixa.af"]),
        ("embalagens", ["caixa.pdf", "unboxing.mp4"], ["unboxing.mp4"]),
        ("embalagens", ["sem_extensao"], ["sem_extensao"]),
    ],
)
async def test_extensao_fora_da_lista_e_recusada_no_servidor(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
    tipo: str, nomes: list[str], recusados: list[str],
):
    """O `accept` do <input> é só sugestão; a trava é aqui, e nada sobe."""
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    r = await client.post(f"/api/pricing/mega/products/{p.id}/{tipo}/upload",
                          files=_arquivos(*nomes))
    assert r.status_code == 400
    assert r.json()["detail"] == {"code": "tipo_nao_aceito", "arquivos": recusados}
    assert mega.chamadas == []


async def test_embalagem_aceita_os_formatos_de_grafica(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    # .af = Affinity, onde as caixas da Uranyx são desenhadas (e as extensões
    # antigas do Affinity).
    nomes = ["a.PDF", "b.ai", "c.psd", "d.eps", "e.cdr", "f.svg", "g.zip", "h.jpeg",
             "Caixa Panela 17.09.af", "j.afdesign", "k.afpub", "l.afphoto"]
    r = await client.post(f"/api/pricing/mega/products/{p.id}/embalagens/upload",
                          files=_arquivos(*nomes))
    assert r.status_code == 200, r.text
    assert r.json()["embalagens_count"] == len(nomes)


async def test_tipo_de_envio_desconhecido_e_422(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    r = await client.post(f"/api/pricing/mega/products/{p.id}/manuais/upload",
                          files=_arquivos("a.jpg"))
    assert r.status_code == 422


async def test_quem_so_ve_nao_envia(
    client: AsyncClient, db: AsyncSession, mega: MegaFalso,
    auth_as: Callable[[User | None], None],
):
    dono = await _usuario(db, PERM_FULL)
    p = await _produto(db, dono, nome="X", pasta="/Celular/X")
    auth_as(await _usuario(db, PERM_VIEW))
    r = await client.post(f"/api/pricing/mega/products/{p.id}/embalagens/upload",
                          files=_arquivos("a.jpg"))
    assert r.status_code == 403
    mega.pasta("/Celular/X")
    r = await client.get(f"/api/pricing/mega/products/{p.id}/midias")
    assert r.status_code == 200


# ─────────────── recontar ───────────────


async def test_recontar_grava_nos_irmaos_e_liga_embalagem_feita_a_mao(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    mega.arquivo(
        "/Celular/Fossibot F117/frente.jpg",
        "/Celular/Fossibot F117/review.mp4",
        "/Celular/Fossibot F117/EMBALAGÉNS /caixa.jpg",
        "/Celular/Fossibot F117/EMBALAGÉNS /Thumbs.db",
    )
    a = await _produto(db, editor, nome="F117 4/128", pasta="/Celular/Fossibot F117")
    b = await _produto(db, editor, nome="F117 8/256", pasta="/Celular/Fossibot F117")

    r = await client.post(f"/api/pricing/mega/products/{a.id}/recontar")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["fotos_count"], body["videos_count"], body["embalagens_count"]) == (1, 1, 1)
    assert body["embalagens_path"] == "/Celular/Fossibot F117/EMBALAGÉNS "
    assert body["embalagens_url"]
    assert body["midias_contadas_em"]
    # Link exportado UMA vez, para os dois.
    assert mega.exportados == ["/Celular/Fossibot F117/EMBALAGÉNS "]
    irmao = await _recarregar(db, b.id)
    assert (irmao.fotos_count, irmao.embalagens_count) == (1, 1)
    assert irmao.embalagens_url == body["embalagens_url"]


async def test_recontar_solta_link_de_embalagem_que_sumiu_do_mega(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    mega.arquivo("/Celular/X/a.jpg")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X",
                       embalagens_path="/Celular/X/Embalagens",
                       embalagens_url="https://mega.nz/folder/morto", embalagens_count=4)
    r = await client.post(f"/api/pricing/mega/products/{p.id}/recontar")
    assert r.status_code == 200
    assert r.json()["embalagens_path"] is None and r.json()["embalagens_url"] is None
    assert r.json()["embalagens_count"] == 0


async def test_sidecar_antigo_nao_apaga_a_embalagem(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    """Janela do deploy: API nova, container velho (sem a chave `embalagens`).
    A contagem de fotos vale; a embalagem não pode ser apagada por engano."""
    mega.arquivo("/Celular/X/a.jpg", "/Celular/X/b.jpg")
    mega.formato_antigo = True
    p = await _produto(db, editor, nome="X", pasta="/Celular/X",
                       embalagens_path="/Celular/X/Embalagens",
                       embalagens_url="https://mega.nz/folder/vivo", embalagens_count=4)
    r = await client.post(f"/api/pricing/mega/products/{p.id}/recontar")
    assert r.status_code == 200
    assert r.json()["fotos_count"] == 2
    assert r.json()["embalagens_url"] == "https://mega.nz/folder/vivo"
    assert r.json()["embalagens_count"] == 4


async def test_recontar_sem_pasta_e_mega_fora(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    p = await _produto(db, editor, nome="X")
    r = await client.post(f"/api/pricing/mega/products/{p.id}/recontar")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "sem_pasta"

    q = await _produto(db, editor, nome="Y", pasta="/Celular/Y")
    mega.fora_do_ar = True
    r = await client.post(f"/api/pricing/mega/products/{q.id}/recontar")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "mega_sidecar"


async def test_botao_recontar_da_aba_e_cron_gravam_embalagens(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    """O /counts/refresh e o cron da madrugada usam a mesma recontagem.
    Pasta fora da listagem (um nível mais fundo) é contada sozinha."""
    mega.arquivo(
        "/Celular/F117/a.jpg",
        "/Celular/F117/Embalagens/caixa.pdf",
        "/Malas/_geral/Linha PP/Modelo 1/x.jpg",
        "/Malas/_geral/Linha PP/Modelo 1/y.jpg",
    )
    a = await _produto(db, editor, nome="F117", pasta="/Celular/F117")
    fundo = await _produto(db, editor, nome="PP", raiz="mala",
                           pasta="/Malas/_geral/Linha PP/Modelo 1")

    r = await client.post("/api/pricing/mega/counts/refresh", json={})
    assert r.status_code == 200, r.text
    assert r.json()["products_with_folder"] == 2
    assert r.json()["pastas_avulsas"] == 1
    row = await _recarregar(db, a.id)
    assert (row.fotos_count, row.embalagens_count) == (1, 1)
    assert row.embalagens_path == "/Celular/F117/Embalagens"
    assert (await _recarregar(db, fundo.id)).fotos_count == 2

    # O cron (sem usuário) faz o mesmo; sidecar fora do ar só loga.
    from app.worker import mega_midias_recontar

    mega.arquivo("/Celular/F117/b.jpg")
    await mega_midias_recontar({})
    assert (await _recarregar(db, a.id)).fotos_count == 2
    mega.fora_do_ar = True
    await mega_midias_recontar({})  # não levanta
    assert (await _recarregar(db, a.id)).fotos_count == 2


# ─────────────── ver e baixar pela tela ───────────────


async def test_midias_lista_por_tipo_com_nome_relativo(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    mega.arquivo(
        "/Malas/ABS 12/M1 listrada/b005.jpg",
        "/Malas/ABS 12/foto.heic",
        "/Malas/ABS 12/review.mp4",
        "/Malas/ABS 12/Embalagens/caixa.pdf",
        "/Malas/ABS 12/Embalagens/caixa.jpg",
    )
    p = await _produto(db, editor, nome="ABS 12", raiz="mala", pasta="/Malas/ABS 12",
                       url="https://mega.nz/folder/f",
                       embalagens_path="/Malas/ABS 12/Embalagens",
                       embalagens_url="https://mega.nz/folder/e")
    base = f"/api/pricing/mega/products/{p.id}/midias"

    fotos = (await client.get(base)).json()
    assert fotos["pasta"] == "/Malas/ABS 12" and fotos["url"] == "https://mega.nz/folder/f"
    assert fotos["arquivos"] == [
        {"nome": "M1 listrada/b005.jpg", "ext": "jpg", "imagem": True},
        # heic é foto, mas o navegador não desenha: vira ícone, não miniatura.
        {"nome": "foto.heic", "ext": "heic", "imagem": False},
    ]
    videos = (await client.get(base, params={"tipo": "videos"})).json()
    assert [a["nome"] for a in videos["arquivos"]] == ["review.mp4"]
    emb = (await client.get(base, params={"tipo": "embalagens"})).json()
    assert emb["pasta"] == "/Malas/ABS 12/Embalagens"
    assert emb["url"] == "https://mega.nz/folder/e"
    assert emb["arquivos"] == [
        {"nome": "Embalagens/caixa.jpg", "ext": "jpg", "imagem": True},
        {"nome": "Embalagens/caixa.pdf", "ext": "pdf", "imagem": False},
    ]

    sem = await _produto(db, editor, nome="Sem pasta")
    r = await client.get(f"/api/pricing/mega/products/{sem.id}/midias",
                         params={"tipo": "embalagens"})
    assert r.json() == {"pasta": None, "url": None, "arquivos": []}


async def test_midias_de_pasta_apagada_no_mega_e_erro_claro(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    p = await _produto(db, editor, nome="X", pasta="/Celular/Apagada")
    r = await client.get(f"/api/pricing/mega/products/{p.id}/midias")
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "mega_sidecar"


async def test_arquivo_imagem_inline_resto_download(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    mega.arquivo("/Celular/X/sub/frente.jpg", conteudo=b"JPEGDATA")
    mega.arquivo("/Celular/X/Embalagens/caixa.pdf", conteudo=b"%PDF")
    mega.arquivo("/Celular/X/Embalagens/vetor.svg", conteudo=b"<svg/>")
    mega.arquivo("/Celular/X/详情2_01.jpg", conteudo=b"CN")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    url = f"/api/pricing/mega/products/{p.id}/midias/arquivo"

    r = await client.get(url, params={"nome": "sub/frente.jpg"})
    assert r.status_code == 200 and r.content == b"JPEGDATA"
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=86400"

    r = await client.get(url, params={"nome": "sub/frente.jpg", "baixar": 1})
    assert r.headers["content-disposition"].startswith("attachment;")

    for nome in ("Embalagens/caixa.pdf", "Embalagens/vetor.svg"):
        r = await client.get(url, params={"nome": nome})
        assert r.status_code == 200, nome
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["content-disposition"].startswith("attachment;")

    r = await client.get(url, params={"nome": "详情2_01.jpg"})
    assert r.status_code == 200
    assert "filename*=UTF-8''" in r.headers["content-disposition"]

    r = await client.get(url, params={"nome": "nao/existe.jpg"})
    assert r.status_code == 404


async def test_miniatura_reduzida_e_volta_ao_original_quando_nao_da(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    """A grade pede `miniatura=1`: 24 fotos originais do fornecedor eram ~25 MB
    por aba. Sidecar antigo (sem /thumb) ou imagem que o Pillow não abre caem
    no arquivo inteiro — a tela nunca fica sem a foto por causa disso."""
    mega.arquivo("/Celular/X/frente.png", conteudo=b"PNGDATA")
    mega.arquivo("/Celular/X/Embalagens/arte.zip", conteudo=b"PK")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    url = f"/api/pricing/mega/products/{p.id}/midias/arquivo"

    r = await client.get(url, params={"nome": "frente.png", "miniatura": 1})
    assert r.status_code == 200 and r.content == b"MINI:PNGDATA"
    assert r.headers["content-type"] == "image/jpeg"
    # É um JPEG: "Salvar imagem como…" tem de sair .jpg, não .png/.pdf.
    assert r.headers["content-disposition"].startswith('inline; filename="frente.jpg"')
    assert r.headers["cache-control"] == "private, max-age=86400"

    for modo in ("antigo", "nao_abre"):
        mega.thumb = modo
        r = await client.get(url, params={"nome": "frente.png", "miniatura": 1})
        assert r.status_code == 200 and r.content == b"PNGDATA", modo
        assert r.headers["content-type"] == "image/png"

    # MEGA fora do ar não vira "foto quebrada" silenciosa: erro do sidecar.
    mega.thumb = "fora"
    r = await client.get(url, params={"nome": "frente.png", "miniatura": 1})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "mega_sidecar"

    # Arquivo sem prévia (ZIP) ignora o pedido de miniatura e continua download.
    mega.thumb = "ok"
    r = await client.get(url, params={"nome": "Embalagens/arte.zip", "miniatura": 1})
    assert r.content == b"PK"
    assert r.headers["content-disposition"].startswith("attachment;")
    # Baixar sempre entrega o original.
    r = await client.get(url, params={"nome": "frente.png", "miniatura": 1, "baixar": 1})
    assert r.content == b"PNGDATA"
    assert r.headers["content-disposition"].startswith("attachment;")
    # Arquivo que não existe: 404 do /thumb cai para o /file, que diz 404.
    r = await client.get(url, params={"nome": "some.jpg", "miniatura": 1})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "arquivo_nao_encontrado"


async def test_previa_de_pdf_e_affinity_na_aba_embalagens(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    """Eduardo 29/09: a caixa aparecia só como ícone "PDF". PDF/AI/Affinity/PSD
    ganham prévia (JPEG do sidecar); o arquivo em si nunca sai inline, e arte
    sem prévia fica no ícone (404 sem_previa) — nunca o PDF aberto na tela."""
    mega.arquivo("/Celular/X/Embalagens/caixa.pdf", conteudo=b"%PDF")
    mega.arquivo("/Celular/X/Embalagens/caixa.af", conteudo=b"AFFINITY")
    mega.arquivo("/Celular/X/Embalagens/faca.eps", conteudo=b"%!PS")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    url = f"/api/pricing/mega/products/{p.id}/midias/arquivo"

    for nome, bruto in (("Embalagens/caixa.pdf", b"%PDF"), ("Embalagens/caixa.af", b"AFFINITY")):
        r = await client.get(url, params={"nome": nome, "miniatura": 1})
        assert r.status_code == 200 and r.content == b"MINI:" + bruto, nome
        assert r.headers["content-type"] == "image/jpeg"
        assert r.headers["content-disposition"].startswith("inline;")
    assert mega.lados == [320, 320]

    # Visor: prévia grande.
    r = await client.get(url, params={"nome": "Embalagens/caixa.pdf", "miniatura": 1, "grande": 1})
    assert r.status_code == 200 and mega.lados[-1] == 1600

    # Sem miniatura (ou com baixar) continua download do original.
    for extra in ({}, {"miniatura": 1, "baixar": 1}):
        r = await client.get(url, params={"nome": "Embalagens/caixa.pdf", **extra})
        assert r.content == b"%PDF"
        assert r.headers["content-disposition"].startswith("attachment;")

    # Prévia que não sai (sidecar antigo ou PDF que o poppler não abre): 404,
    # e NÃO o PDF inline.
    for modo in ("antigo", "nao_abre"):
        mega.thumb = modo
        r = await client.get(url, params={"nome": "Embalagens/caixa.pdf", "miniatura": 1})
        assert r.status_code == 404 and r.json()["detail"]["code"] == "sem_previa", modo

    # EPS não tem prévia: o pedido de miniatura é ignorado (download, sem /thumb).
    mega.thumb = "ok"
    antes = len(mega.lados)
    r = await client.get(url, params={"nome": "Embalagens/faca.eps", "miniatura": 1})
    assert r.content == b"%!PS" and r.headers["content-disposition"].startswith("attachment;")
    assert len(mega.lados) == antes


@pytest.mark.parametrize(
    ("nome", "code"),
    [
        ("../Outra/segredo.jpg", "nome_invalido"),
        ("sub/../../x.jpg", "nome_invalido"),
        ("/Celular/Outro/x.jpg", "nome_invalido"),
        ("   ", "nome_invalido"),
        ("a\x00.jpg", "nome_invalido"),
        # Nome de pasta, "." e trecho vazio: o mega-get do sidecar é
        # recursivo e baixaria a pasta inteira para o disco antes do 404.
        (".", "nome_invalido"),
        ("M1 listrada", "nome_invalido"),
        ("Embalagens", "nome_invalido"),
        ("M1 listrada/.", "nome_invalido"),
        ("M1 listrada/", "nome_invalido"),
        ("a//b.jpg", "nome_invalido"),
        ("./Embalagens/caixa.jpg", "nome_invalido"),
        # Curinga: o MEGAcmd trata como padrão ("*" = a pasta toda; "clip.mp?"
        # também passava por fora da trava de vídeo).
        ("*", "nome_invalido"),
        ("*.jpg", "nome_invalido"),
        ("clip.mp?", "nome_invalido"),
        ("review.mp4", "video_abre_no_mega"),
        ("Embalagens/unboxing.MOV", "video_abre_no_mega"),
    ],
)
async def test_arquivo_recusa_caminho_e_video(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
    nome: str, code: str,
):
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    r = await client.get(f"/api/pricing/mega/products/{p.id}/midias/arquivo",
                         params={"nome": nome})
    assert r.status_code == 400, nome
    assert r.json()["detail"]["code"] == code
    assert not any(c[1] == "/file" for c in mega.chamadas)


# ─────────────── portal das agências ───────────────


async def test_portal_nao_lista_nem_baixa_embalagem(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso, monkeypatch,
):
    monkeypatch.setattr(get_settings(), "portal_tokens", "tok-agencia-emb-123456:alpha")
    h = {"X-Portal-Token": "tok-agencia-emb-123456"}
    mega.arquivo("/Celular/F117/frente.jpg", "/Celular/F117/Embalagens/caixa.jpg",
                 "/Celular/F117/embalagem/arte.jpg")
    p = await _produto(db, editor, nome="F117", pasta="/Celular/F117", fotos_count=1)

    r = await client.get(f"/api/portal/produtos/{p.id}/fotos", headers=h)
    assert r.status_code == 200, r.text
    assert [f["nome"] for f in r.json()["fotos"]] == ["frente.jpg"]

    for nome in ("Embalagens/caixa.jpg", "embalagem/arte.jpg", "EMBALAGÉNS/x.jpg"):
        r = await client.get(f"/api/portal/produtos/{p.id}/foto", params={"nome": nome},
                             headers=h)
        assert r.status_code == 400, nome
        assert r.json()["detail"]["code"] == "nome_invalido"
    r = await client.get(f"/api/portal/produtos/{p.id}/foto", params={"nome": "frente.jpg"},
                         headers=h)
    assert r.status_code == 200


@pytest.mark.parametrize(
    "nome",
    [
        # O MEGAcmd resolve "." e curinga (conferido em produção): todos estes
        # chegariam em Embalagens/caixa.jpg sem o 1º trecho ser "Embalagens".
        "./Embalagens/caixa.jpg",
        ".//Embalagens/caixa.jpg",
        " ./Embalagens/caixa.jpg",
        "Embalagen?/caixa.jpg",
        "*/caixa.jpg",
        "Emb*/caixa.jpg",
        "./embalagem/arte.jpg",
        "sub//frente.jpg",
    ],
)
async def test_portal_nao_baixa_embalagem_por_ponto_ou_curinga(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso, monkeypatch,
    nome: str,
):
    monkeypatch.setattr(get_settings(), "portal_tokens", "tok-agencia-emb-123456:alpha")
    h = {"X-Portal-Token": "tok-agencia-emb-123456"}
    mega.arquivo("/Celular/F117/frente.jpg", "/Celular/F117/Embalagens/caixa.jpg",
                 "/Celular/F117/embalagem/arte.jpg")
    p = await _produto(db, editor, nome="F117", pasta="/Celular/F117", fotos_count=1)

    r = await client.get(f"/api/portal/produtos/{p.id}/foto", params={"nome": nome}, headers=h)
    assert r.status_code == 400, nome
    assert r.json()["detail"]["code"] == "nome_invalido"
    # Recusado ANTES de ir ao MEGA.
    assert not any(c[1] == "/file" for c in mega.chamadas)


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Embalagens/caixa.jpg", True),
        ("./Embalagens/caixa.jpg", True),
        (".//embalagem/x.jpg", True),
        ("/EMBALAGÉNS//y.pdf", True),
        ("M1 listrada/Embalagens/x.jpg", False),
        ("frente.jpg", False),
        ("", False),
        ("./", False),
    ],
)
def test_eh_de_embalagens_pula_trecho_vazio_e_ponto(nome: str, esperado: bool):
    from app.services.mega_fotos import eh_de_embalagens

    assert eh_de_embalagens(nome) is esperado


@pytest.mark.parametrize(
    "path",
    [
        "/Celular/F117/*",
        "/Celular/F117/clip.mp?",
        "/Cel*lar/F117/caixa.jpg",
        "/Celular/F117/./Embalagens/caixa.jpg",
        "/Celular/F117/Embalagens/.",
        "/Celular/F117/../Outra/x.jpg",
    ],
)
def test_sidecar_file_recusa_curinga_e_ponto_sem_chamar_o_mega(monkeypatch, path: str):
    """A trava do sidecar vale para TODOS que chamam /file (Tabela de Preços e
    portal): o mega-get nem roda."""
    chamadas: list[list[str]] = []

    def run(cmd, **_kw):
        chamadas.append(cmd)
        return 0, ""

    monkeypatch.setattr(SIDECAR, "run", run)
    with pytest.raises(HTTPException) as exc:
        SIDECAR.file(path, None)
    assert exc.value.status_code == 400
    assert chamadas == []


# ─────────────── sincronização, PATCH e trocar/desligar pasta ───────────────


async def test_sync_only_missing_nao_troca_pasta_de_quem_ja_tem(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    """Medido em 29/09/2026: 3 malas "PP premium" mudariam de pasta sem o
    link mudar junto — o próximo envio iria para a pasta errada."""
    mega.pasta("/Malas/PP premium", "/Malas/PP premium antiga/x")
    tem = await _produto(db, editor, nome="PP premium", raiz="mala",
                         pasta="/Malas/PP premium antiga", url="https://mega.nz/folder/a")
    so_link = await _produto(db, editor, nome="PP premium", raiz="mala",
                             url="https://mega.nz/folder/colado")

    r = await client.post("/api/pricing/mega/sync", json={"dry_run": False})
    assert r.status_code == 200, r.text
    row = await _recarregar(db, tem.id)
    assert row.fotos_path == "/Malas/PP premium antiga"
    assert row.fotos_url == "https://mega.nz/folder/a"
    # Quem tinha só o link colado à mão ganha o caminho (e fica com o link).
    row = await _recarregar(db, so_link.id)
    assert row.fotos_path == "/Malas/PP premium"
    assert row.fotos_url == "https://mega.nz/folder/colado"


@pytest.mark.parametrize("vazio", ["", "   ", None])
async def test_patch_com_link_vazio_desliga_a_pasta(
    client: AsyncClient, db: AsyncSession, editor: User, vazio: str | None,
):
    p = await _produto(db, editor, nome="X", pasta="/Celular/X", url="https://mega.nz/folder/x",
                       embalagens_path="/Celular/X/Embalagens",
                       embalagens_url="https://mega.nz/folder/e",
                       fotos_count=3, videos_count=1, embalagens_count=2)
    r = await client.patch(f"/api/pricing/products/{p.id}", json={"fotos_url": vazio})
    assert r.status_code == 200, r.text
    out = r.json()
    for campo in ("fotos_url", "fotos_path", "embalagens_url", "embalagens_path",
                  "fotos_count", "videos_count", "embalagens_count", "midias_contadas_em"):
        assert out[campo] is None, campo


async def test_patch_sem_mexer_no_link_mantem_a_pasta(
    client: AsyncClient, db: AsyncSession, editor: User,
):
    p = await _produto(db, editor, nome="X", pasta="/Celular/X", url="https://mega.nz/folder/x",
                       embalagens_path="/Celular/X/Embalagens", embalagens_count=2)
    r = await client.patch(f"/api/pricing/products/{p.id}", json={"model": "novo"})
    assert r.status_code == 200
    assert r.json()["fotos_path"] == "/Celular/X"
    assert r.json()["embalagens_path"] == "/Celular/X/Embalagens"
    assert r.json()["embalagens_count"] == 2


async def test_pastas_ligar_e_desligar(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    mega.arquivo(
        "/Celular/Oukitel C3/a.jpg",
        "/Celular/Oukitel C3/Embalagens/caixa.pdf",
        "/Celular/Fossibot S5/b.jpg",
        "/Malas/ABS 12/M1/c.jpg",
        "/uranyx/airtag UAT001/a.jpg",
        "/Eletro/.keep",
        # Pastas da conta que NÃO são de produto (existem em produção): a
        # listagem de 2 níveis a partir de "/" traz todas como folha.
        "/_geral/CONTABIL - servidor/clientes/nota.pdf",
        "/_geral/Anatel/cert.pdf",
        "/charlots park/x/y.jpg",
    )
    mega.pasta("/Malas/Embalagens", "/uranyx/vazia")
    mega.arquivo("/leia.txt")

    r = await client.get("/api/pricing/mega/pastas")
    assert r.status_code == 200, r.text
    paths = [p["path"] for p in r.json()["pastas"]]
    # Folhas, em ordem, só dentro de /Celular, /Malas e /uranyx; container
    # (/Celular) fora, pasta "Embalagens" fora, arquivo solto na raiz fora,
    # /_geral (contabilidade), /Eletro e /charlots park fora.
    assert paths == [
        "/Celular/Fossibot S5", "/Celular/Oukitel C3", "/Malas/ABS 12",
        "/uranyx/airtag UAT001", "/uranyx/vazia",
    ]
    assert r.json()["pastas"][0] == {"path": "/Celular/Fossibot S5", "name": "Fossibot S5"}

    p = await _produto(db, editor, nome="C3", pasta="/Celular/Fossibot S5",
                       url="https://mega.nz/folder/velho",
                       embalagens_path="/Celular/Fossibot S5/Embalagens",
                       embalagens_url="https://mega.nz/folder/velho-emb", embalagens_count=9)
    irmao = await _produto(db, editor, nome="S5", pasta="/Celular/Fossibot S5",
                           url="https://mega.nz/folder/velho")
    url = f"/api/pricing/mega/products/{p.id}/pasta"

    for ruim in ("/Celular/Nao Existe", "Celular/Oukitel C3", "/Celular/../Malas/ABS 12",
                 "/Celular", "/Malas/Embalagens", "/_geral/CONTABIL - servidor",
                 "/_geral/Anatel", "/Eletro", "/charlots park/x"):
        r = await client.put(url, json={"path": ruim})
        assert r.status_code == 400, ruim
        assert r.json()["detail"]["code"] == "pasta_invalida"
    # Nenhum link público foi gerado para pasta recusada.
    assert mega.exportados == []

    r = await client.put(url, json={"path": "/Celular/Oukitel C3"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fotos_path"] == "/Celular/Oukitel C3"
    assert body["fotos_url"] != "https://mega.nz/folder/velho"
    # A embalagem da pasta antiga saiu; a da nova foi achada na contagem.
    assert body["embalagens_path"] == "/Celular/Oukitel C3/Embalagens"
    assert (body["fotos_count"], body["embalagens_count"]) == (1, 1)
    # Só esta linha mudou.
    assert (await _recarregar(db, irmao.id)).fotos_path == "/Celular/Fossibot S5"

    r = await client.delete(url)
    assert r.status_code == 200
    assert all(v is None for v in r.json().values())
    row = await _recarregar(db, p.id)
    assert row.fotos_path is None and row.embalagens_path is None
    # Nada foi apagado no MEGA.
    assert "/Celular/Oukitel C3/Embalagens/caixa.pdf" in mega.arquivos


# ─────────────── a função pura do sidecar ───────────────

# Saída REAL do mega-find em produção (29/09/2026), inclusive o espaço no fim
# do nome da pasta de cor.
_SAIDA_MALAS = """/Malas/ABS 12/M1 listrada/b005 M1 listrada preto/b005.12.jpg
/Malas/ABS 12/M1 listrada/b005 M1 listrada preto
/Malas/ABS 12/M1 listrada/b006 M1 listrada verde claro  /b006.12.jpg
/Malas/ABS 12/M1 listrada/b006 M1 listrada verde claro
/Malas/ABS 12/M1 listrada/b014 M1 listrada prata  /b014.12.png
/Malas/ABS 12/M1 listrada/b014 M1 listrada prata
/Malas/ABS 12/M1 listrada
/Malas/ABS 12"""


def test_classificar_saida_real_de_producao():
    c = SIDECAR._classificar("/Malas/ABS 12", _SAIDA_MALAS.splitlines())
    assert (c["fotos"], c["videos"], c["embalagens"]) == (3, 0, 0)
    assert c["embalagens_pasta"] is None
    assert "M1 listrada/b006 M1 listrada verde claro  /b006.12.jpg" in [
        a["nome"] for a in c["arquivos"]
    ]
    c = SIDECAR._classificar(
        "/uranyx/airtag UAT001",
        ["/uranyx/airtag UAT001/Airtag Caixa.pdf", "/uranyx/airtag UAT001"],
    )
    # PDF solto na pasta de fotos não é foto nem embalagem (não está na subpasta).
    assert (c["fotos"], c["videos"], c["embalagens"]) == (0, 0, 0)
    assert c["arquivos"] == [{"nome": "Airtag Caixa.pdf", "ext": "pdf", "grupo": "outro"}]


def test_classificar_embalagens_nao_contam_como_foto():
    linhas = [
        "/Celular/X/a.jpg",
        "/Celular/X/b.MP4",
        "/Celular/X/Embalagens/caixa.jpg",
        "/Celular/X/Embalagens/unboxing.mp4",
        "/Celular/X/Embalagens/arte/caixa.ai",
        "/Celular/X/Embalagens/arte",
        "/Celular/X/Embalagens/Thumbs.db",
        "/Celular/X/Embalagens/.DS_Store",
        "/Celular/X/Embalagens",
        "/Celular/X",
    ]
    c = SIDECAR._classificar("/Celular/X", linhas)
    assert (c["fotos"], c["videos"], c["embalagens"]) == (1, 1, 3)
    assert c["embalagens_pasta"] == "/Celular/X/Embalagens"
    emb = sorted(a["nome"] for a in c["arquivos"] if a["grupo"] == "embalagem")
    assert emb == ["Embalagens/arte/caixa.ai", "Embalagens/caixa.jpg", "Embalagens/unboxing.mp4"]


@pytest.mark.parametrize(
    "nome", ["embalagem", "EMBALAGENS", "Embalagéns", "Embalagem ", "embalagens_"]
)
def test_classificar_reconhece_o_nome_como_o_operador_digita(nome: str):
    c = SIDECAR._classificar("/C/X", [f"/C/X/{nome}/caixa.jpg", f"/C/X/{nome}", "/C/X/a.jpg"])
    assert (c["fotos"], c["embalagens"]) == (1, 1)
    assert c["embalagens_pasta"] == f"/C/X/{nome}"


def test_classificar_casos_de_borda():
    # Subpasta de embalagens VAZIA: a pasta é achada mesmo assim.
    c = SIDECAR._classificar("/C/X", ["/C/X/Embalagens", "/C/X"])
    assert c["embalagens_pasta"] == "/C/X/Embalagens" and c["embalagens"] == 0
    # "Embalagens" que não é o 1º segmento é foto normal (é de um modelo).
    c = SIDECAR._classificar("/C/X", ["/C/X/M1/Embalagens/a.jpg", "/C/X/M1/Embalagens", "/C/X/M1"])
    assert (c["fotos"], c["embalagens"], c["embalagens_pasta"]) == (1, 0, None)
    # Arquivo chamado "Embalagens.pdf" não é a subpasta.
    c = SIDECAR._classificar("/C/X", ["/C/X/Embalagens.pdf"])
    assert (c["embalagens"], c["embalagens_pasta"]) == (0, None)
    # Pasta com ponto no nome e conteúdo dentro não vira arquivo fantasma.
    c = SIDECAR._classificar("/C/X", ["/C/X/v1.2/a.jpg", "/C/X/v1.2"])
    assert c["fotos"] == 1 and [a["nome"] for a in c["arquivos"]] == ["v1.2/a.jpg"]
    # Duas pastas de embalagem: as duas contam, destino é a de nome padrão.
    c = SIDECAR._classificar("/C/X", ["/C/X/embalagem/a.jpg", "/C/X/Embalagens/b.pdf"])
    assert c["embalagens"] == 2 and c["embalagens_pasta"] == "/C/X/Embalagens"


def test_files_do_sidecar_padrao_continua_so_imagens(monkeypatch):
    """O portal chama /files sem `tipo`: continua recebendo só imagens, agora
    sem a subpasta de embalagens."""
    saida = "\n".join([
        "/C/X/a.jpg", "/C/X/b.mp4", "/C/X/Embalagens/caixa.jpg",
        "/C/X/Embalagens/caixa.pdf", "/C/X/Embalagens", "/C/X",
    ])
    monkeypatch.setattr(SIDECAR, "run", lambda cmd, **kw: (0, saida))
    assert SIDECAR.files("/C/X", _=None)["arquivos"] == [
        {"nome": "a.jpg", "ext": "jpg", "imagem": True}
    ]
    assert [a["nome"] for a in SIDECAR.files("/C/X", "embalagens", None)["arquivos"]] == [
        "Embalagens/caixa.jpg", "Embalagens/caixa.pdf"
    ]
    assert [a["nome"] for a in SIDECAR.files("/C/X", "videos", None)["arquivos"]] == ["b.mp4"]
    with pytest.raises(HTTPException):
        SIDECAR.files("/C/X", "tudo", None)
    mc = SIDECAR.media_counts_one("/C/X", None)
    assert mc == {"path": "/C/X", "fotos": 1, "videos": 1, "embalagens": 2,
                  "embalagens_pasta": "/C/X/Embalagens"}


async def test_sidecar_stream_confere_status_antes_do_primeiro_byte(monkeypatch):
    """O download de embalagem não segura o arquivo inteiro na memória da API,
    e erro do sidecar vira MegaError com o status dele (404 = não achou)."""
    import httpx

    from app.services import mega_fotos

    def responder(req: httpx.Request) -> httpx.Response:
        if req.url.params["path"].endswith("some.pdf"):
            return httpx.Response(404, json={"detail": "não baixou"})
        return httpx.Response(200, content=b"A" * 70_000)

    real = httpx.AsyncClient

    def cliente(**kw):
        return real(transport=httpx.MockTransport(responder), **kw)

    monkeypatch.setattr(mega_fotos.httpx, "AsyncClient", cliente)
    pedacos, fechar = await mega_fotos.sidecar_stream("/file", params={"path": "/C/X/ok.pdf"})
    recebido = b"".join([p async for p in pedacos])
    await fechar()
    assert recebido == b"A" * 70_000
    with pytest.raises(MegaError) as e:
        await mega_fotos.sidecar_stream("/file", params={"path": "/C/X/some.pdf"})
    assert e.value.status_code == 404


async def test_aquecer_previas_pede_uma_vez_cada_foto_e_arte(
    db: AsyncSession, editor: User, mega: MegaFalso,
):
    """Pré-aquecimento (29/09: "para todos precisa ser rápido"): toda foto e
    toda arte com prévia de cada pasta de produto, uma vez só mesmo com duas
    linhas na mesma pasta; vídeo e arte sem prévia (ZIP) ficam de fora."""
    from app.services.mega_midias import aquecer_previas

    mega.arquivo("/Celular/S7/a.jpg", "/Celular/S7/sub/b.png", "/Celular/S7/clip.mp4")
    mega.arquivo("/Celular/S7/Embalagens/caixa.pdf", "/Celular/S7/Embalagens/caixa.af",
                 "/Celular/S7/Embalagens/foto caixa.jpg", "/Celular/S7/Embalagens/arte.zip")
    mega.arquivo("/Malas/ABS/M1/c.jpg")
    await _produto(db, editor, nome="S7 16", pasta="/Celular/S7")
    await _produto(db, editor, nome="S7 32", pasta="/Celular/S7")
    await _produto(db, editor, nome="ABS", raiz="mala", pasta="/Malas/ABS")
    mega.thumb = "ok"

    r = await aquecer_previas(db, paralelo=2)
    pedidos = sorted(p for m_, p in mega.chamadas if p == "/thumb")
    assert r == {"pastas": 2, "arquivos": 6, "prontos": 6, "sem_previa": 0, "pastas_com_erro": 0}
    assert len(pedidos) == 6 and set(mega.lados) == {320}


async def test_nome_com_dois_pontos_dentro_nao_e_subir_de_nivel(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso,
):
    """"manual-uranyx-p01..af" é um arquivo real (Panelas ferro, 29/09): ".."
    só é proibido como trecho do caminho."""
    mega.arquivo("/Celular/X/Embalagens/manual-uranyx-p01..af", conteudo=b"AF")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    url = f"/api/pricing/mega/products/{p.id}/midias/arquivo"
    r = await client.get(url, params={"nome": "Embalagens/manual-uranyx-p01..af"})
    assert r.status_code == 200 and r.content == b"AF"
    for nome in ("../x.jpg", "a/../b.jpg", "a/..", ".."):
        r = await client.get(url, params={"nome": nome})
        assert r.status_code == 400, nome


def test_sidecar_identidade_so_aceita_o_proprio_arquivo(monkeypatch):
    """Pasta também responde ao `mega-ls -l`; só a linha com o NOME INTEIRO do
    arquivo vale (formato conferido em produção em 29/09/2026)."""
    saidas = {
        "/C/X/a b (1).jpeg": "FLAGS VERS      SIZE            DATE       NAME\n"
                             "----    1       199502 08Sep2026 12:41:11 a b (1).jpeg",
        "/C/X/M2 v1.5": "/C/X/M2 v1.5: \nFLAGS VERS      SIZE            DATE       NAME\n"
                        "----    1        10 29Sep2026 10:00:00 capa M2 v1.5",
    }
    def run(cmd, **_k):
        return (0, saidas[cmd[-1]]) if cmd[-1] in saidas else (1, "nf")

    monkeypatch.setattr(SIDECAR, "run", run)
    assert SIDECAR._identidade("/C/X/a b (1).jpeg") == "199502|08Sep2026 12:41:11"
    assert SIDECAR._identidade("/C/X/M2 v1.5") is None
    assert SIDECAR._identidade("/C/X/nao.jpg") is None
    assert SIDECAR._validar_caminho("/C/X/manual-uranyx-p01..af") == "manual-uranyx-p01..af"
    for ruim in ("/C/../x.jpg", "/C/./x.jpg", "/C/*.jpg", "/C/x?.jpg"):
        with pytest.raises(HTTPException):
            SIDECAR._validar_caminho(ruim)

