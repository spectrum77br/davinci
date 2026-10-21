"""OS SITES DA MARCA como 3ª fonte do agente (20/10/2026, `sites_marca.py`).

As páginas em `tests/fixtures/sites_marca/` são as de produção de 20/10/2026
(uranyx.com.br e charlots.com.br), enxutas (sem script, estilo nem
formulário) — nenhum teste vai à rede: o HTTP é um transporte falso.

- a leitura da página de PRODUTO (Uranyx: `.spec-row`, `.detail-medidas`,
  `p.aviso-legal`, `[data-cor-nome]`; Charlots: resumo, descrição, `dl`
  Tamanho/Material, medidas, opções e a frase da garantia de 90 dias) — as
  opções só da seção do produto (os "Produtos similares" também têm cores);
  a "Página não encontrada" e a página de outro produto não valem;
- as listas: o sitemap e o llms.txt da Uranyx, a página /produtos da
  Charlots (o sitemap dela está velho);
- o casamento anúncio → página, com títulos reais de anúncios;
- a gravação só quando muda (o `hash`), o cron e o script.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.atendimento import AtendimentoSiteProduto
from app.services.atendimento import sites_marca

FIXTURES = Path(__file__).parent / "fixtures" / "sites_marca"


def _html(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


_BASES = {"uranyx": "https://uranyx.com.br", "charlots": "https://charlots.com.br"}


def _pagina(marca: str, slug: str) -> sites_marca.PaginaProduto | None:
    url = f"{_BASES[marca]}/produto/{slug}"
    return sites_marca.ler_pagina(marca, _html(f"{marca}__{slug}.html"), slug, url)


def _specs(p: sites_marca.PaginaProduto, rotulo: str) -> list[str]:
    return [s["valor"] for s in p.specs if s["rotulo"] == rotulo]


# ─────────────── a página de produto (pura) ───────────────


def test_uranyx_s5_a18_pro_max():
    p = _pagina("uranyx", "s5")
    assert p is not None
    assert p.titulo == "A18 Pro Max - S5 Premium Edition"
    assert p.modelo_chave == "s5"
    assert p.resumo == "Acompanha case exclusiva."
    assert _specs(p, "Característica") == [
        "128 GB de armazenamento interno",
        "16 GB de RAM",
        "NFC",
        "Câmera tripla na traseira",
        "Acabamento fosco",
        "Acompanha case exclusiva",
    ]
    assert _specs(p, "Peso") == ["218 g"] and _specs(p, "Tela") == ['6,9" HD+ 120 Hz']
    assert _specs(p, "Opções") == ["Laranja, Branco"]
    assert _specs(p, "Categoria") == ["Celulares"]
    # A Uranyx não publica prazo de garantia.
    assert p.garantia_texto is None
    # Os cartões de "Continue explorando" (F110 Pro 5G…) não entram.
    assert not any("F110" in s["valor"] for s in p.specs)


def test_uranyx_f110_ram_virtual_aviso_5g_e_air_fryer():
    f110 = _pagina("uranyx", "f110-pro-5g")
    assert f110.modelo_chave == "f110 pro 5g"
    assert "20 GB de RAM (8 GB + até 12 GB de RAM expansível) + 128 GB de ROM" in _specs(
        f110, "Característica"
    )
    (aviso,) = _specs(f110, "Aviso")
    assert aviso.startswith("A disponibilidade do 5G depende da operadora")
    fryer = _pagina("uranyx", "air-fryer")
    assert _specs(fryer, "Voltagem") == ["110 V ou 220 V (não é bivolt)"]
    # Só as opções do PRODUTO (o cesto de 2,5/4/6 L dos similares fica fora).
    assert _specs(fryer, "Opções") == ["110 V, 220 V"]


def test_charlots_mala_20_abs_com_garantia_e_familias():
    p = _pagina("charlots", "mala-20-abs")
    assert p.titulo == "Mala de bordo 20 polegadas em ABS"
    assert p.modelo_chave == "mala abs 20"
    assert p.resumo == "Cabine · 10 kg · ABS chapa dupla · Rodas de gel"
    assert p.garantia_texto == "Garantia de 90 dias contra defeitos de fabricação"
    assert _specs(p, "Tamanho") == ["20 polegadas"] and _specs(p, "Material") == ["ABS"]
    assert _specs(p, "Carga máxima") == ["10 kg"] and _specs(p, "Linha") == ["ABS"]
    (descricao,) = _specs(p, "Descrição")
    assert descricao.startswith("Mala de bordo de 20 polegadas") and "\n" in descricao
    # P1–P8 e M1–M6 são FAMÍLIAS de cor/design, não tamanho.
    (opcoes,) = _specs(p, "Opções")
    assert opcoes.startswith("M1 Preto") and "M6 Bordo" in opcoes
    # O atacado (preço, pedido mínimo) não entra.
    assert not any("R$" in s["valor"] for s in p.specs)


def test_charlots_kit_pecas_e_paginas_que_nao_sao_de_produto():
    kit = _pagina("charlots", "kit-3-pecas-polipropileno")
    assert kit.modelo_chave == "kit polipropileno 14-20-24"
    assert _specs(kit, "Tamanho") == ["14, 20 e 24 polegadas"]
    # Os "Produtos similares" têm cores (Rodinhas M1-P4…): não são da balança.
    balanca = _pagina("charlots", "balanca-de-bagagem")
    assert balanca is not None and _specs(balanca, "Opções") == []
    # O sitemap velho aponta para a "Página não encontrada".
    assert _pagina("charlots", "mala-grande-28") is None
    # A página de OUTRO produto (o `canonical` não bate): não vale.
    assert (
        sites_marca.ler_pagina(
            "charlots", _html("charlots__mala-20-abs.html"), "mala-24-abs", "https://x"
        )
        is None
    )
    assert sites_marca.ler_pagina("uranyx", "<html><h1>Oi</h1></html>", "x", "u") is None
    assert sites_marca.ler_pagina("7buyers", _html("uranyx__s5.html"), "s5", "u") is None


def test_hash_so_muda_com_o_conteudo():
    a = _pagina("uranyx", "s5")
    b = _pagina("uranyx", "s5")
    assert a.hash() == b.hash() and len(a.hash()) == 64
    mudou = sites_marca.ler_pagina(
        "uranyx", _html("uranyx__s5.html").replace("218 g", "220 g"), "s5", a.url
    )
    assert mudou.hash() != a.hash()


def test_listas_de_produtos():
    uranyx = sites_marca.slugs_da_lista(_html("uranyx_sitemap.xml"), _html("uranyx_llms.txt"))
    assert len(uranyx) == 24 and "s5" in uranyx and "f110-pro-5g" in uranyx
    charlots = sites_marca.slugs_da_lista(_html("charlots_produtos.html"))
    assert len(charlots) == 30 and "mala-20-abs" in charlots
    assert "mala-grande-28" not in charlots  # o sitemap velho tinha; a /produtos não
    assert sites_marca.slugs_da_lista('<a href="/produto/Mala X">', None) == []
    assert sites_marca.LISTAS["charlots"] == ("/produtos",)


# ─────────────── o casamento anúncio → página (puro) ───────────────


@pytest.fixture(scope="module")
def paginas() -> list[sites_marca.PaginaProduto]:
    saida = []
    for nome in sorted(FIXTURES.glob("*__*.html")):
        marca, slug = nome.stem.split("__")
        if p := _pagina(marca, slug):
            saida.append(p)
    return saida


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        # A Kia (20/10/2026): o A18 Pro Max é a página "S5".
        ("Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium", "s5"),
        ("Uranyx Fossibot F110 Pro 5g 128 Gb Memoria 20 Gb Ram Preto", "f110-pro-5g"),
        ("Uranyx Air Fryer 4L 1500W 110V", "air-fryer"),
        # Acessório DO produto: não é a página do produto.
        ("Capa Uranyx S5 A18 Pro Max", None),
        ("Cesto de Vidro Air Fryer Uranyx 4L", None),
        # Modelo que o site não tem.
        ("Uranyx Oukitel C2 16.128 GB Memoria - Roxo - Acessorios", None),
        # Sem a marca no título (e sem SKU): não casa.
        ("Celular S5 Premium 128GB", None),
        ("Mala De Bordo 20 Polegadas 10Kg M4 (Prata)", "mala-20-abs"),
        (
            "Kit 3 Mala e Frasqueira De Viagem 14 20 24 Polegadas 10Kg e 23Kg Bordo Despacho P4",
            "kit-3-pecas-polipropileno",
        ),
        # P5 é família do polipropileno (a mala de 20" ABS só tem M1–M6).
        ("Mala De Viagem 20 Pequena De Bordo P5 Preto", None),
        ("Mala E Frasqueira De Viagem 12 E 18 Polegadas 10Kg Bordo M3", None),
        ("Balança digital de bagagem Charlots", None),
    ],
)
def test_casar_com_titulos_reais(paginas, titulo, esperado):
    marca = "charlots" if "Mala" in titulo or "Balança" in titulo else None
    p = sites_marca.casar(titulo, paginas, marca)
    assert (p.slug if p else None) == esperado


def test_empate_entre_modelos_nao_casa():
    paginas = [
        sites_marca.PaginaProduto("uranyx", "f109-5g", "u", "F109 5G", "f109 5g", [], None, None),
        sites_marca.PaginaProduto("uranyx", "f109-s", "u", "F109 S", "f109 s", [], None, None),
    ]
    titulo = "Uranyx Fossibot F109 5G 24.256 ou F109 S 24.256 - Resistente NFC"
    assert sites_marca.casar(titulo, paginas) is None
    assert sites_marca.casar("Uranyx F109 S 24GB", paginas).slug == "f109-s"
    assert sites_marca.casar(None, paginas) is None


def test_marca_do_anuncio():
    assert sites_marca.marca_do_anuncio("Uranyx A18 Pro Max") == "uranyx"
    assert sites_marca.marca_do_anuncio("Mala Charlots 20") == "charlots"
    assert sites_marca.marca_do_anuncio("Mala de bordo 20", ["b026.20"]) == "charlots"
    assert sites_marca.marca_do_anuncio("Celular A18", ["dg089.ci+a003.ci"]) == "uranyx"
    # O acessório a0NN é das duas; duas marcas no título: não se sabe.
    assert sites_marca.marca_do_anuncio("Fone", ["a003"]) is None
    assert sites_marca.marca_do_anuncio("Uranyx e Charlots") is None


# ─────────────── a leitura dos sites (HTTP falso) e a gravação ───────────────


class SitesFalsos:
    """O transporte HTTP: as fixtures por URL; conta os pedidos e guarda os cabeçalhos."""

    def __init__(self) -> None:
        self.paginas: dict[str, tuple[int, str]] = {
            "https://uranyx.com.br/sitemap.xml": (200, _html("uranyx_sitemap.xml")),
            "https://uranyx.com.br/llms.txt": (200, _html("uranyx_llms.txt")),
            "https://charlots.com.br/produtos": (200, _html("charlots_produtos.html")),
        }
        for nome in FIXTURES.glob("*__*.html"):
            marca, slug = nome.stem.split("__")
            url = f"https://{marca}.com.br/produto/{slug}"
            self.paginas[url] = (200, nome.read_text(encoding="utf-8"))
        self.pedidos: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        status, corpo = self.paginas.get(str(request.url), (404, "não achei"))
        return httpx.Response(status, text=corpo, headers={"Set-Cookie": "sessao=abc; Path=/"})

    def cliente(self) -> httpx.AsyncClient:
        cliente = sites_marca.novo_cliente()
        cliente._transport = httpx.MockTransport(self)  # o resto da configuração é a de produção
        return cliente


@pytest.fixture
async def sem_paginas(db: AsyncSession):
    await db.execute(delete(AtendimentoSiteProduto))
    await db.commit()
    yield
    await db.execute(delete(AtendimentoSiteProduto))
    await db.commit()


async def test_atualizar_sites_grava_so_o_que_mudou(db: AsyncSession, sem_paginas):
    sites = SitesFalsos()
    agora = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
    async with sites.cliente() as cliente:
        resumo = await sites_marca.atualizar_sites(
            db, cliente=cliente, pausa_s=0, agora=agora, forcar=True
        )
    await db.commit()

    # Uranyx: 24 na lista, 3 páginas nas fixtures (as outras: 404 = falha).
    assert resumo["uranyx"] == {
        "lista": 24,
        "lidas": 3,
        "nao_produto": 0,
        "falhas": 21,
        "nova": 3,
        "mudou": 0,
        "igual": 0,
    }
    assert resumo["charlots"]["lista"] == 30 and resumo["charlots"]["nova"] == 3
    linhas = (
        await db.scalars(select(AtendimentoSiteProduto).order_by(AtendimentoSiteProduto.slug))
    ).all()
    assert {(r.marca, r.slug) for r in linhas} >= {("uranyx", "s5"), ("charlots", "mala-20-abs")}
    s5 = next(r for r in linhas if r.slug == "s5")
    assert s5.url == "https://uranyx.com.br/produto/s5" and s5.modelo_chave == "s5"
    assert s5.lido_em == agora and len(s5.hash) == 64
    assert {"rotulo": "Característica", "valor": "NFC"} in s5.specs
    mala = next(r for r in linhas if r.slug == "mala-20-abs")
    assert mala.garantia_texto == "Garantia de 90 dias contra defeitos de fabricação"
    # Só GET, com o User-Agent próprio, nunca com cookie (o site manda um).
    assert {p.method for p in sites.pedidos} == {"GET"}
    assert all(p.headers["user-agent"] == sites_marca.USER_AGENT for p in sites.pedidos)
    assert not any("cookie" in p.headers for p in sites.pedidos)
    # Nunca a home nem página de categoria: só as listas e as páginas de produto.
    listas = {f"{_BASES[m]}{c}" for m, cs in sites_marca.LISTAS.items() for c in cs}
    assert all("/produto/" in str(p.url) or str(p.url) in listas for p in sites.pedidos)

    # De novo, igual: só o `lido_em` anda. Uma página mudou: grava.
    sites.paginas["https://uranyx.com.br/produto/s5"] = (
        200,
        _html("uranyx__s5.html").replace("218 g", "220 g"),
    )
    depois = agora + timedelta(days=1)
    async with sites.cliente() as cliente:
        resumo2 = await sites_marca.atualizar_sites(
            db, cliente=cliente, pausa_s=0, agora=depois, forcar=True, marcas=("uranyx",)
        )
    await db.commit()
    assert (resumo2["uranyx"]["mudou"], resumo2["uranyx"]["igual"]) == (1, 2)
    await db.refresh(s5)
    assert {"rotulo": "Peso", "valor": "220 g"} in s5.specs and s5.lido_em == depois


async def test_atualizar_sites_seco_desligado_e_pagina_que_nao_e_produto(
    db: AsyncSession, sem_paginas, monkeypatch
):
    sites = SitesFalsos()
    # A Charlots lista uma página que virou "Página não encontrada" (200).
    sites.paginas["https://charlots.com.br/produtos"] = (
        200,
        '<a href="/produto/mala-grande-28">x</a><a href="/produto/mala-20-abs">y</a>',
    )
    async with sites.cliente() as cliente:
        resumo = await sites_marca.atualizar_sites(
            db, cliente=cliente, pausa_s=0, gravar=False, forcar=True, marcas=("charlots",)
        )
    assert resumo["charlots"]["lidas"] == 1 and resumo["charlots"]["nao_produto"] == 1
    assert resumo["charlots"]["nova"] == 0
    assert (await db.scalars(select(AtendimentoSiteProduto))).all() == []
    # Desligado (sem forçar): nada é lido.
    monkeypatch.setattr(get_settings(), "atendimento_sites_marca_ativo", False)
    antes = len(sites.pedidos)
    async with sites.cliente() as cliente:
        assert await sites_marca.atualizar_sites(db, cliente=cliente, pausa_s=0) == {
            "desligado": True
        }
    assert len(sites.pedidos) == antes


async def test_casar_anuncio_com_site_no_banco(db: AsyncSession, sem_paginas):
    sites = SitesFalsos()
    agora = datetime.now(UTC)
    async with sites.cliente() as cliente:
        await sites_marca.atualizar_sites(db, cliente=cliente, pausa_s=0, agora=agora, forcar=True)
    await db.commit()

    pagina = await sites_marca.casar_anuncio_com_site(
        db, "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium"
    )
    assert pagina is not None and pagina.slug == "s5"
    assert (await sites_marca.casar_anuncio_com_site(db, "Mala De Bordo 20 Polegadas M4")) is None
    mala = await sites_marca.casar_anuncio_com_site(
        db, "Mala De Bordo 20 Polegadas M4", sites_marca.CHARLOTS
    )
    assert mala.slug == "mala-20-abs"
    # Página não relida há mais de 30 dias: não vale.
    depois = agora + timedelta(days=sites_marca.MAX_DIAS_SEM_LER + 1)
    assert (
        await sites_marca.casar_anuncio_com_site(
            db, "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium", agora=depois
        )
        is None
    )


async def test_ritmo_de_um_pedido_por_segundo(monkeypatch):
    esperas: list[float] = []

    async def dormir(segundos: float) -> None:
        esperas.append(segundos)

    monkeypatch.setattr(sites_marca.asyncio, "sleep", dormir)
    ritmo = sites_marca._Ritmo(1.0)
    await ritmo.esperar()
    await ritmo.esperar()
    assert len(esperas) == 1 and 0.9 <= esperas[0] <= 1.0


async def test_script_seco_e_forcado(db: AsyncSession, monkeypatch):
    from scripts import atendimento_sites_marca as script

    chamadas: list[dict] = []

    async def falso(session, **kw):
        chamadas.append(kw)
        return {"uranyx": {"lista": 1}}

    @asynccontextmanager
    async def escopo():
        yield db

    monkeypatch.setattr(sites_marca, "atualizar_sites", falso)
    monkeypatch.setattr(script._db, "session_scope", escopo)
    resumo = await script.rodar(gravar=False, marcas=("uranyx",))
    assert resumo == {"gravar": False, "uranyx": {"lista": 1}}
    assert chamadas == [{"marcas": ("uranyx",), "gravar": False, "forcar": True}]
