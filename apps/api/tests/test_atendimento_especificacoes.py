"""AS ESPECIFICAÇÕES POR FONTE para o agente (20/10/2026, `especificacoes.py`).

A ordem do dono (decisão 3, 20/10/2026): o anúncio > o cadastro do DaVinci
> o site da marca. Conflito: vale o de cima, listado. O anúncio que se
contradiz: `conflito_no_anuncio` (o agente manda para pessoa, "corrigir
anúncio"). RAM física × virtual tratada à parte. As páginas do site são as
fixtures de `sites_marca` (produção, 20/10/2026).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Product, ProductLink
from app.models.atendimento import AtendimentoSiteProduto
from app.models.enums import IntegrationPlatform
from app.services.atendimento import especificacoes as e
from app.services.atendimento import ficha, sites_marca
from tests.test_atendimento_ficha_loja import SHOPEE, _loja

FIXTURES = Path(__file__).parent / "fixtures" / "sites_marca"
TITULO_A18 = "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium"
NOMES_A18 = [
    "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata + Fone UFB10",
    "Uranyx C17 16.128 - Azul + Fone U9",
]
SKUS_A18 = ["dg089.ci+a003.ci", "dg106.sp+a003.sp"]


def _site(marca: str, slug: str) -> sites_marca.PaginaProduto:
    html = (FIXTURES / f"{marca}__{slug}.html").read_text(encoding="utf-8")
    return sites_marca.ler_pagina(marca, html, slug, f"https://{marca}.com.br/produto/{slug}")


def _juntar(ficha_: dict | None, titulo: str | None, nomes=(), skus=(), site=None) -> dict:
    marca = sites_marca.marca_do_anuncio(titulo or (ficha_ or {}).get("titulo"), skus)
    return e.juntar(
        e.do_anuncio(ficha_, titulo),
        e.do_cadastro(list(nomes), list(skus), titulo or (ficha_ or {}).get("titulo"), marca),
        e.do_site(site.specs) if site else {},
        virtual_no_anuncio=e._virtual_no_anuncio(ficha_, titulo),
    )


def _campos(r: dict) -> dict[str, tuple[str, str]]:
    return {c["campo"]: (c["valor"], c["fonte"]) for c in r["campos"]}


def test_kia_a18_anuncio_depois_davinci_depois_site():
    ficha_a18 = {
        "titulo": TITULO_A18,
        "atributos": ["Marca: Uranyx", "Memória RAM: 8 GB", "Sistema operacional: Android 14"],
        "descricao": "Uranyx S5 + Fone UFB10\nMemória: 8GB + 8GB virtual\nBateria 5000mAh",
    }
    r = _juntar(ficha_a18, None, NOMES_A18, SKUS_A18, _site("uranyx", "s5"))
    campos = _campos(r)
    assert campos["ram"] == ("16 GB", "anuncio")
    # O anúncio separa (a descrição): 8 físicos + 8 virtuais — não é conflito com o "8 GB".
    assert campos["ram_fisica"] == ("8 GB", "anuncio") and campos["ram_virtual"] == (
        "8 GB",
        "anuncio",
    )
    assert campos["armazenamento"] == ("128 GB", "anuncio")
    assert campos["nfc"] == ("sim", "anuncio")
    assert campos["sistema"] == ("Android 14", "anuncio")
    assert campos["bateria"] == ("5.000 mAh", "anuncio")
    # O fato aprovado (`espec_celular`): o celular é desbloqueado.
    assert campos["desbloqueado"] == ("sim", "davinci")
    # Só o que faltou vem do site.
    assert campos["peso"] == ("218 g", "site") and campos["tela"] == ('6,9"', "site")
    assert r["conflitos"] == [] and r["conflito_no_anuncio"] is False
    # A ordem dos campos é a de `CAMPOS`.
    ordem = [c["campo"] for c in r["campos"]]
    assert ordem == sorted(ordem, key=e.CAMPOS.index)


def test_ram_fisica_e_virtual():
    # O anúncio diz 16 (título) e 8 (atributo) sem falar de virtual: contradição.
    contradiz = _juntar(
        {"titulo": TITULO_A18, "atributos": ["Memória RAM: 8 GB"], "descricao": "Celular"}, None
    )
    (ram,) = [c for c in contradiz["campos"] if c["campo"] == "ram"]
    assert ram == {
        "campo": "ram",
        "valor": "16 GB / 8 GB",
        "fonte": "anuncio",
        "conflito_no_anuncio": True,
    }
    assert contradiz["conflito_no_anuncio"] is True
    # Sem fonte que separe: a nota (o fato aprovado manda separar as duas).
    so_titulo = _juntar({"titulo": "Uranyx A17 Pro Max 12.128"}, None)
    (ram,) = [c for c in so_titulo["campos"] if c["campo"] == "ram"]
    assert ram["nota"] == e.NOTA_RAM_SEM_SEPARAR
    # O site separa (F110 Pro 5G: "20 GB de RAM (8 GB + até 12 GB…)") e bate com o anúncio.
    f110 = _juntar(
        {"titulo": "Uranyx Fossibot F110 Pro 5g 128 Gb Memoria 20 Gb Ram Preto"},
        None,
        site=_site("uranyx", "f110-pro-5g"),
    )
    campos = _campos(f110)
    assert campos["ram"] == ("20 GB", "anuncio")
    assert campos["ram_fisica"] == ("8 GB", "site") and campos["ram_virtual"] == (
        "até 12 GB",
        "site",
    )
    assert campos["rede_5g"] == ("sim", "anuncio")
    assert campos["processador"] == ("Dimensity 6300", "site")
    # O anúncio diz 12 e o site 20: vale o anúncio, o conflito é listado, e a
    # RAM separada do site (de outra RAM) não entra.
    outro = _juntar(
        {"titulo": "Uranyx Fossibot F110 Pro 5g 128 Gb Memoria 12 Gb Ram"},
        None,
        site=_site("uranyx", "f110-pro-5g"),
    )
    assert _campos(outro)["ram"] == ("12 GB", "anuncio") and "ram_fisica" not in _campos(outro)
    assert outro["conflitos"] == [
        {
            "campo": "ram",
            "valor": "12 GB",
            "fonte": "anuncio",
            "outro_valor": "20 GB",
            "outra_fonte": "site",
        }
    ]


def test_anuncio_que_se_contradiz_nao_e_tapado_pelo_site():
    """A Kia em maio: o título dizia "NFC" e a descrição "NFC - Não Tem"."""
    r = _juntar(
        {"titulo": "Uranyx A18 Pro Max NFC 16.128", "atributos": [], "descricao": "NFC - Não Tem"},
        None,
        NOMES_A18,
        SKUS_A18,
        _site("uranyx", "s5"),
    )
    (nfc,) = [c for c in r["campos"] if c["campo"] == "nfc"]
    assert nfc == {
        "campo": "nfc",
        "valor": "sim / não",
        "fonte": "anuncio",
        "conflito_no_anuncio": True,
    }
    assert r["conflito_no_anuncio"] is True
    # Variação não é contradição: "12.64 ou 12.128" e o atributo "128 GB".
    variacoes = _juntar(
        {
            "titulo": "Uranyx Hotwav A17 Pro Max 12.64 ou 12.128 GB Memoria",
            "atributos": ["Capacidade de armazenamento: 128 GB"],
        },
        None,
    )
    assert variacoes["conflito_no_anuncio"] is False
    assert _campos(variacoes)["armazenamento"] == ("64 GB ou 128 GB", "anuncio")


def test_cadastro_do_davinci_e_fatos_aprovados(monkeypatch):
    # Sem ficha: o nome dos produtos (RAM.ROM pelo `_RE_SPEC`, 5G pelo `_RE_5G`).
    campos = _campos(_juntar(None, "Uranyx A18 Pro Max", NOMES_A18, SKUS_A18))
    # O título não diz a memória: a dos produtos (as duas variações têm 16.128).
    assert campos["ram"] == ("16 GB", "davinci") and campos["armazenamento"] == (
        "128 GB",
        "davinci",
    )
    so_cadastro = _juntar(None, "Celular", ["Uranyx F110 Pro 5G 8.256 - Preto"], ["dg120"])
    c = _campos(so_cadastro)
    assert c["ram"] == ("8 GB", "davinci") and c["armazenamento"] == ("256 GB", "davinci")
    assert c["rede_5g"] == ("sim", "davinci") and c["desbloqueado"] == ("sim", "davinci")
    # O fato aprovado do S5 (NFC) vale para o produto Uranyx S5.
    assert _campos(_juntar(None, "Celular Uranyx", NOMES_A18, SKUS_A18))["nfc"] == (
        "sim",
        "davinci",
    )
    # Variações de memória diferentes: os dois valores, com a nota.
    duas = _juntar(None, "Uranyx S7", ["Uranyx S7 8.128 - Preto", "Uranyx S7 12.256 - Cinza"], [])
    (ram,) = [x for x in duas["campos"] if x["campo"] == "ram"]
    assert ram["valor"] == "8 GB ou 12 GB" and ram["nota"] == e.NOTA_VARIA
    # "2.5L" da air fryer não é RAM.ROM; e o fato aprovado: não é bivolt.
    fryer = _juntar(
        {"titulo": "Air Fryer Uranyx 4L 110V"},
        None,
        ["Air Fryer Uranyx 2.5L 110V"],
        ["uaf25"],
        _site("uranyx", "air-fryer"),
    )
    cf = _campos(fryer)
    assert "ram" not in cf and cf["voltagem"] == ("110 V", "anuncio")
    assert cf["capacidade"] == ("4 L", "anuncio") and cf["potencia"] == ("1500 W", "site")
    assert fryer["conflitos"] == []
    # O fato só vale enquanto o texto aprovado ainda o diz.
    monkeypatch.setitem(e.REGRAS_APROVADAS, "espec_celular", "Responder com o anúncio.")
    sem_fato = _campos(_juntar(None, "Celular", ["Uranyx F110 Pro 5G 8.256 - Preto"], ["dg120"]))
    assert "desbloqueado" not in sem_fato


def test_wifi_5ghz_nao_e_5g_e_linhas_do_site_que_a_ficha_nao_aceita():
    r = _juntar({"titulo": "Celular X 4G", "descricao": "Wi-Fi 5G dual band\nNFC: sim"}, None)
    campos = _campos(r)
    assert campos["wifi_5ghz"] == ("sim", "anuncio") and "rede_5g" not in campos
    (wifi,) = [c for c in r["campos"] if c["campo"] == "wifi_5ghz"]
    assert wifi["nota"] == e.NOTA_WIFI
    # Linha do site com resistência (regra de segurança 8): fora, com o que tiver junto.
    site = e.do_site(
        [
            {"rotulo": "Característica", "valor": "Resistente à água e poeira (IP68), NFC"},
            {"rotulo": "Característica", "valor": "Bateria de 10600 mAh"},
            {"rotulo": "Aviso", "valor": "A disponibilidade do 5G depende da operadora"},
            {"rotulo": "Opções", "valor": "110 V, 220 V"},
        ]
    )
    assert "nfc" not in site and "rede_5g" not in site
    assert [a.valor for a in site["bateria"]] == ["10.600 mAh"]
    assert [a.valor for a in site["voltagem"]] == ["110 V ou 220 V"]


def test_ficha_de_outro_anuncio_so_da_o_titulo():
    outro = {
        "fonte": f"{ficha.FONTE_OUTRO_ANUNCIO} (Mercado Livre)",
        "titulo": "Uranyx F110 Pro 5G",
        "titulo_do_outro_anuncio": "F110 Pro 5G 12GB",
        "atributos": ["Memória RAM: 12 GB"],
        "descricao": "12GB de RAM",
    }
    campos = _campos(_juntar(outro, None))
    assert campos == {"rede_5g": ("sim", "anuncio")}


def test_sem_nada():
    assert _juntar(None, None) == {"campos": [], "conflitos": [], "conflito_no_anuncio": False}


# ─────────────── banco ───────────────


@pytest.fixture
async def site_s5(db: AsyncSession):
    await db.execute(delete(AtendimentoSiteProduto))
    pagina = _site("uranyx", "s5")
    db.add(
        AtendimentoSiteProduto(
            marca=pagina.marca,
            slug=pagina.slug,
            url=pagina.url,
            titulo=pagina.titulo,
            modelo_chave=pagina.modelo_chave,
            specs=pagina.specs,
            resumo=pagina.resumo,
            garantia_texto=pagina.garantia_texto,
            lido_em=datetime.now(UTC),
            hash=pagina.hash(),
        )
    )
    await db.commit()
    yield
    await db.execute(delete(AtendimentoSiteProduto))
    await db.commit()


async def test_especificacoes_do_produto_no_banco(
    db: AsyncSession, make_user, site_s5, monkeypatch
):
    user = await make_user()
    ids = []
    for nome, sku in zip(NOMES_A18, SKUS_A18, strict=True):
        p = Product(user_id=user.id, sku=sku, name=nome, stock=3)
        db.add(p)
        await db.flush()
        ids.append(p.id)
    await db.commit()
    # A variação selecionada (o cartão/pedido): o A18 S5 — o cadastro e o site dele.
    ficha_a18 = {
        "titulo": TITULO_A18,
        "produtos_vinculados": [{"product_id": str(ids[0])}],
    }

    r = await e.especificacoes_do_produto(db, ficha_a18, [str(i) for i in ids], TITULO_A18)

    campos = _campos(r)
    assert r["site"] == "s5"
    assert campos["ram"] == ("16 GB", "anuncio") and campos["nfc"] == ("sim", "anuncio")
    assert campos["desbloqueado"] == ("sim", "davinci") and campos["peso"] == ("218 g", "site")
    assert r["conflitos"] == [] and r["conflito_no_anuncio"] is False
    # O anúncio liga DOIS modelos (A18 S5 + C17, revisão de 20/10/2026): o que o
    # anúncio diz do hardware pode ser do outro — `varia_por_modelo`.
    (nfc,) = [c for c in r["campos"] if c["campo"] == "nfc"]
    assert nfc["varia_por_modelo"] is True and nfc["nota"] == e.NOTA_VARIA_MODELO
    # Sem a variação selecionada, os dois modelos: o C17 não casa com a página
    # do S5 — nenhum site (antes: o site do S5 valia para o C17 também).
    todos = {**ficha_a18, "produtos_vinculados": [{"product_id": str(i)} for i in ids]}
    dois = await e.especificacoes_do_produto(db, todos, [str(i) for i in ids], TITULO_A18)
    assert dois["site"] is None and "peso" not in _campos(dois)
    # Os sites desligados: só o anúncio e o cadastro.
    monkeypatch.setattr(get_settings(), "atendimento_sites_marca_ativo", False)
    sem_site = await e.especificacoes_do_produto(db, ficha_a18, ids, TITULO_A18)
    assert sem_site["site"] is None and "peso" not in _campos(sem_site)
    # Os produtos do anúncio inteiro (todas as variações vivas).
    integ = await _loja(db, user, SHOPEE, "kia")
    for i, pid in enumerate(ids):
        db.add(
            ProductLink(
                user_id=user.id,
                product_id=pid,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id="58265493964",
                variation_id=str(i),
                morto_desde=datetime.now(UTC) if i else None,
            )
        )
    await db.commit()
    assert await e.produtos_do_anuncio(db, SHOPEE, integ.id, "58265493964") == [ids[0]]
    assert await e.produtos_do_anuncio(db, SHOPEE, None, "58265493964") == []
    # Id inválido é ignorado; sem ficha nem título, nada.
    vazio = await e.especificacoes_do_produto(db, None, ["nao-e-uuid"], None)
    assert vazio == {"campos": [], "conflitos": [], "conflito_no_anuncio": False, "site": None}
