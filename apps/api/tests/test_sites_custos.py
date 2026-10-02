"""Custo "Kit 1" para os sites — o que a porta deixa passar e o que não.

Mesma ordem dos testes do estoque: primeiro o que trava a porta (sem
configuração, fechada), depois o recorte de cada site — o token diz o site, a
pasta de fotos diz de quem é a linha, a regex diz quais códigos saem —, os
números, e por fim a lista branca da resposta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.pricing import PricingProduct
from app.models.segment import Segment
from app.routers import sites_custos as rota
from app.services.rate_limit import RateLimitError

URL = "/api/sites/custos"
TOK_CH = "tok-site-charlots-0123456789"
TOK_UR = "tok-site-uranyx-9876543210"
TOK_SEM_RECORTE = "tok-site-poofy-5555555555"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


CH = _auth(TOK_CH)
UR = _auth(TOK_UR)


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setattr(
        get_settings(),
        "sites_estoque_tokens",
        f"{TOK_CH}:charlots, {TOK_UR}:uranyx,{TOK_SEM_RECORTE}:poofy",
    )


@pytest.fixture(autouse=True)
def chamadas_do_limite(monkeypatch) -> list[str]:
    """Sem Redis nos testes: o limite vira um contador das chaves pedidas."""
    chaves: list[str] = []

    async def _ok(*, key: str, limit: int, window_seconds: int) -> int:
        chaves.append(key)
        return limit

    monkeypatch.setattr(rota, "sliding_window_check", _ok)
    return chaves


async def _segmento(db: AsyncSession, make_user, nome: str = "Regular") -> Segment:
    dono = await make_user()
    seg = Segment(user_id=dono.id, name=nome, slug=f"seg-{uuid4().hex[:8]}")
    db.add(seg)
    await db.flush()
    return seg


async def _linha(
    db: AsyncSession,
    seg: Segment,
    sku: str,
    *,
    pasta: str | None = None,
    custo: str = "100.00",
    ativa: bool = True,
    nome: str | None = None,
    **extra,
) -> PricingProduct:
    p = PricingProduct(
        user_id=seg.user_id,
        segment_id=seg.id,
        sku=sku,
        name=nome or f"Produto {sku[:40]}",
        cost_kit1=Decimal(custo),
        fotos_path=pasta,
        is_active=ativa,
        **extra,
    )
    db.add(p)
    await db.commit()
    return p


async def _tabela(db: AsyncSession, make_user) -> None:
    """Um pouco de cada família da Tabela de Preços de verdade (02/10/2026)."""
    seg = await _segmento(db, make_user)
    apple = await _segmento(db, make_user, "Apple")
    await _linha(db, seg, "dg048", pasta="/Celular/Fossibot F117", custo="1150.00")
    await _linha(db, seg, "dg052,dg053,dg054", pasta="/Celular/hotwav A17 pro max", custo="560")
    await _linha(db, seg, "dg055,dg056,dg057", pasta="/Celular/hotwav A17 pro max", custo="495")
    await _linha(db, seg, "a003", pasta="/Celular/fone", custo="40")  # acessório da Uranyx
    await _linha(db, seg, "uaf001m1.110, uaf001m1.220", pasta="/uranyx/airfryer vidro", custo="240")
    await _linha(db, seg, "uaf001m1.2l", custo="60")  # sem pasta: vale a regex
    await _linha(db, seg, "dg101", custo="670")  # T8, sem pasta
    await _linha(db, seg, "b005.20,b006.20", pasta="/Malas/ABS 20", custo="150")
    await _linha(db, seg, "a075", pasta="/Malas/chaveiro", custo="9")  # acessório da Charlots
    await _linha(db, seg, "bp003,bp002,bp001", pasta="/Malas/mochila", custo="81")
    await _linha(db, seg, "b049", custo="900")  # mala sem pasta
    await _linha(db, apple, "i238,i239", pasta="/Celular/apple watch S11", custo="2060")
    await _linha(db, apple, "dg900", custo="999")  # Apple, mesmo casando a regex
    await _linha(db, seg, "z005.20.mala", custo="1")  # salvado: de ninguém
    await _linha(db, seg, "dg001", pasta="/Celular/desativado", custo="300", ativa=False)


def _skus(r) -> list[list[str]]:
    return [i["skus"] for i in r.json()["itens"]]


def _por_sku(r) -> dict[str, dict]:
    return {s: i for i in r.json()["itens"] for s in i["skus"]}


# ─────────────── fecha por padrão ───────────────


async def test_sem_configuracao_a_rota_fica_fechada(client: AsyncClient, monkeypatch):
    """Segredo vazio = 401, NUNCA aberto."""
    monkeypatch.setattr(get_settings(), "sites_estoque_tokens", "")
    r = await client.get(URL, headers=UR)
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "sites_nao_autorizado"}


async def test_sem_header_401(client: AsyncClient):
    r = await client.get(URL)
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "sites_nao_autorizado"}


@pytest.mark.parametrize(
    "valor",
    [TOK_UR, f"Basic {TOK_UR}", f"Token {TOK_UR}", "Bearer", "Bearer ", f"Bearer{TOK_UR}"],
)
async def test_esquema_errado_401(client: AsyncClient, valor: str):
    assert (await client.get(URL, headers={"Authorization": valor})).status_code == 401


async def test_token_errado_401(client: AsyncClient):
    assert (await client.get(URL, headers=_auth("chute"))).status_code == 401
    assert (await client.get(URL, headers=_auth(TOK_UR[:-1]))).status_code == 401
    r = await client.get(URL, headers={"Authorization": "Bearer tok-ção".encode()})
    assert r.status_code == 401


async def test_site_sem_recorte_401(client: AsyncClient, db: AsyncSession, make_user):
    await _tabela(db, make_user)
    r = await client.get(URL, headers=_auth(TOK_SEM_RECORTE))
    assert r.status_code == 401
    assert "custo" not in r.text


async def test_nao_autorizado_nem_chega_no_limite(
    client: AsyncClient, chamadas_do_limite: list[str]
):
    await client.get(URL)
    await client.get(URL, headers=_auth("chute"))
    assert chamadas_do_limite == []


# ─────────────── cada site vê o seu recorte ───────────────


async def test_uranyx_ve_celular_eletro_e_os_acessorios_dela(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _tabela(db, make_user)
    r = await client.get(URL, headers=UR)
    assert r.status_code == 200, r.text
    assert r.json()["site"] == "uranyx"
    assert _skus(r) == [
        ["a003"],
        ["dg048"],
        ["dg052", "dg053", "dg054"],
        ["dg055", "dg056", "dg057"],
        ["dg101"],
        ["uaf001m1.110", "uaf001m1.220"],
        ["uaf001m1.2l"],
    ]
    assert r.json()["total"] == 7


async def test_charlots_ve_malas_mochilas_e_os_acessorios_dela(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _tabela(db, make_user)
    r = await client.get(URL, headers=CH)
    assert r.status_code == 200, r.text
    assert r.json()["site"] == "charlots"
    assert _skus(r) == [["a075"], ["b005.20", "b006.20"], ["b049"], ["bp003", "bp002", "bp001"]]


async def test_acessorio_a0nn_fica_com_a_marca_da_pasta(
    client: AsyncClient, db: AsyncSession, make_user
):
    """`a0NN` casa as duas regex; a pasta decide de quem é o custo."""
    await _tabela(db, make_user)
    assert "a075" not in _por_sku(await client.get(URL, headers=UR))
    assert "a003" not in _por_sku(await client.get(URL, headers=CH))


async def test_apple_fica_de_fora_mesmo_casando_a_regex(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _tabela(db, make_user)
    r = await client.get(URL, headers=UR)
    assert "dg900" not in r.text and "i238" not in r.text
    assert 2060 not in [i["custo_kit1"] for i in r.json()["itens"]]


async def test_linha_inativa_nao_sai(client: AsyncClient, db: AsyncSession, make_user):
    await _tabela(db, make_user)
    r = await client.get(URL, headers=UR)
    assert "dg001" not in r.text
    assert 300 not in [i["custo_kit1"] for i in r.json()["itens"]]
    # Reativada, aparece.
    await db.execute(
        update(PricingProduct).where(PricingProduct.sku == "dg001").values(is_active=True)
    )
    await db.commit()
    assert _por_sku(await client.get(URL, headers=UR))["dg001"]["custo_kit1"] == 300


async def test_codigo_de_outra_familia_na_mesma_linha_nao_sai(
    client: AsyncClient, db: AsyncSession, make_user
):
    """Só os códigos que casam a regex do site saem; linha sem nenhum some."""
    seg = await _segmento(db, make_user)
    await _linha(
        db, seg, "dg060,i999,b005", pasta="/Celular/Oukitel C68 plus", custo="850", nome="C68"
    )
    await _linha(db, seg, "x001,y002", pasta="/uranyx/outra coisa", custo="10", nome="Outra")
    r = await client.get(URL, headers=UR)
    assert _skus(r) == [["dg060"]]
    assert "i999" not in r.text and "b005" not in r.text and "x001" not in r.text


async def test_mesmo_aparelho_em_duas_versoes_sai_em_duas_linhas(
    client: AsyncClient, db: AsyncSession, make_user
):
    """A17 12/128 (560) e 12/64 (495): quem escolhe o maior é o site."""
    await _tabela(db, make_user)
    itens = (await client.get(URL, headers=UR)).json()["itens"]
    a17 = [i["custo_kit1"] for i in itens if i["skus"][0] in ("dg052", "dg055")]
    assert sorted(a17) == [495, 560]


# ─────────────── os códigos ───────────────


async def test_codigos_normalizados(client: AsyncClient, db: AsyncSession, make_user):
    """Espaço, maiúscula, vírgula sobrando e repetição: tudo sai limpo."""
    seg = await _segmento(db, make_user)
    await _linha(db, seg, " UAF002M1.110 ,  uaf002m1.220,,UAF002M1.110, ", pasta="/uranyx/x")
    await _linha(db, seg, "DG082,dg083", pasta="/Celular/Fossibot F112 pro 5G")
    assert _skus(await client.get(URL, headers=UR)) == [
        ["dg082", "dg083"],
        ["uaf002m1.110", "uaf002m1.220"],
    ]


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("dg052,dg053,dg054", ["dg052", "dg053", "dg054"]),
        ("uaf001m1.110, uaf001m1.220", ["uaf001m1.110", "uaf001m1.220"]),
        (" A019 , a019,", ["a019"]),
        ("", []),
        (None, []),
        (" , ,", []),
    ],
)
def test_tokens(bruto, esperado):
    assert rota.tokens(bruto) == esperado


@pytest.mark.parametrize(
    ("pasta", "dono"),
    [
        ("/Celular/Fossibot F117", "uranyx"),
        ("/uranyx/airtag UAT001", "uranyx"),
        ("Celular/fone", "uranyx"),  # sem a barra do começo
        ("/celular/fone", "uranyx"),  # maiúscula não muda o dono
        ("/Celular", "uranyx"),
        ("/Malas/ABS 20", "charlots"),
        ("/Malas/_antigas/x", "charlots"),  # pasta escondida da mídia ainda é da Charlots
        ("/Malas/b006 M1 listrada verde claro  ", "charlots"),
        ("/Celular/../Malas/x", "charlots"),
        ("/Celulares/x", None),  # prefixo de nome não é a raiz
        ("/MalasExtra", None),
        ("/Fotos/Redmi 13C", None),
        ("", None),
        ("  /  ", None),
        (None, None),
    ],
)
def test_dono_da_pasta(pasta, dono):
    assert rota.dono_da_pasta(pasta) == dono


# ─────────────── os números ───────────────


async def test_custo_exato_e_zero_vira_null(client: AsyncClient, db: AsyncSession, make_user):
    """cost_kit1 é NOT NULL com padrão 0: 0 = não preenchido = null (o site não mexe)."""
    seg = await _segmento(db, make_user)
    await _linha(db, seg, "dg088,dg089", pasta="/Celular/Fossibot S5", custo="739.50")
    await _linha(db, seg, "dg048", pasta="/Celular/Fossibot F117", custo="1150.00")
    await _linha(db, seg, "a020", pasta="/Celular/smart glass", custo="0")
    await _linha(db, seg, "dg070", custo="-1")
    itens = _por_sku(await client.get(URL, headers=UR))
    assert itens["dg088"]["custo_kit1"] == 739.5
    assert itens["dg048"]["custo_kit1"] == 1150
    assert itens["a020"]["custo_kit1"] is None
    assert itens["dg070"]["custo_kit1"] is None


async def test_datas_em_utc(client: AsyncClient, db: AsyncSession, make_user):
    seg = await _segmento(db, make_user)
    p = await _linha(db, seg, "dg048", pasta="/Celular/Fossibot F117")
    quando = datetime(2026, 10, 2, 15, 27, 11, tzinfo=UTC)
    await db.execute(
        update(PricingProduct).where(PricingProduct.id == p.id).values(updated_at=quando)
    )
    await db.commit()
    corpo = (await client.get(URL, headers=UR)).json()
    assert corpo["itens"][0]["atualizado_em"] == "2026-10-02T15:27:11+00:00"
    gerado = datetime.fromisoformat(corpo["gerado_em"])
    assert gerado.utcoffset() == timedelta(0)
    assert abs((gerado - datetime.now(UTC)).total_seconds()) < 60


def test_recortar_sem_banco():
    """A regra pura, com linhas como as do SELECT."""
    agora = datetime.now(UTC)

    def _r(sku, pasta, custo="10"):
        return SimpleNamespace(
            sku=sku, name=sku, cost_kit1=Decimal(custo), updated_at=agora, fotos_path=pasta
        )

    linhas = [
        _r("a006,a073", "/Malas/Kit 2 de rodinhas"),
        _r("a001", "/Celular/fone com fio"),
        _r("dg1000,dg1033", None),
        _r("ucw001m1", ""),
    ]
    assert [i["skus"] for i in rota.recortar("uranyx", linhas)] == [
        ["a001"],
        ["dg1000", "dg1033"],
        ["ucw001m1"],
    ]
    assert [i["skus"] for i in rota.recortar("charlots", linhas)] == [["a006", "a073"]]


# ─────────────── limite por minuto ───────────────


async def test_limite_por_site(client: AsyncClient, chamadas_do_limite: list[str]):
    await client.get(URL, headers=CH)
    await client.get(URL, headers=UR)
    assert chamadas_do_limite == ["sites_custos:rl:charlots", "sites_custos:rl:uranyx"]


async def test_limite_estourado_429(client: AsyncClient, monkeypatch):
    async def _estourou(**kw):
        assert kw["limit"] == 30 and kw["window_seconds"] == 60
        raise RateLimitError(retry_after=42)

    monkeypatch.setattr(rota, "sliding_window_check", _estourou)
    r = await client.get(URL, headers=UR)
    assert r.status_code == 429
    assert r.json()["detail"] == {"code": "sites_limite"}
    assert r.headers["Retry-After"] == "42"


async def test_redis_fora_do_ar_nao_vira_500(
    client: AsyncClient, db: AsyncSession, make_user, monkeypatch
):
    seg = await _segmento(db, make_user)
    await _linha(db, seg, "dg048", pasta="/Celular/Fossibot F117")

    async def _caiu(**kw):
        raise ConnectionError("redis caiu")

    monkeypatch.setattr(rota, "sliding_window_check", _caiu)
    with structlog.testing.capture_logs() as logs:
        r = await client.get(URL, headers=UR)
    assert r.status_code == 200
    assert _skus(r) == [["dg048"]]
    assert any(e["event"] == "sites_custos_rate_limit_indisponivel" for e in logs)


# ─────────────── a lista branca ───────────────


async def test_resposta_so_com_os_campos_do_contrato(
    client: AsyncClient, db: AsyncSession, make_user
):
    seg = await _segmento(db, make_user)
    p = await _linha(
        db,
        seg,
        "dg048",
        pasta="/Celular/pasta-interna-f117",
        custo="1150",
        nome="Fossibot F117",
        bling_cost_price=Decimal("1111.11"),
        cost_kit2=Decimal("2222.22"),
        cost_kit8=Decimal("8888.88"),
        ean="7890000000017",
        model="F117-MODELO",
        description="descricao-interna",
    )
    r = await client.get(URL, headers=UR)
    corpo = r.json()
    assert set(corpo) == {"site", "gerado_em", "total", "itens"}
    assert len(corpo["itens"]) == 1
    item = corpo["itens"][0]
    assert set(item) == {"skus", "nome", "custo_kit1", "atualizado_em"}
    assert item["skus"] == ["dg048"] and item["nome"] == "Fossibot F117"
    assert item["custo_kit1"] == 1150
    for proibido in (
        str(p.id),
        str(p.user_id),
        str(seg.id),
        "1111.11",
        "2222.22",
        "8888.88",
        "7890000000017",
        "F117-MODELO",
        "descricao-interna",
        "pasta-interna",
        "Celular",
        "user_id",
        "segment",
        "fotos",
        "cost",
    ):
        assert proibido not in r.text, f"vazou {proibido!r}"


async def test_sem_cache(client: AsyncClient):
    r = await client.get(URL, headers=UR)
    assert r.headers["Cache-Control"] == "private, no-store"
    assert r.headers["Vary"] == "Authorization"


async def test_fora_do_openapi(client: AsyncClient):
    caminhos = (await client.get("/api/openapi.json")).json()["paths"]
    assert not any(c.startswith("/api/sites") for c in caminhos)
    assert "custos" not in str(caminhos)


async def test_log_sem_token_e_sem_custo(client: AsyncClient, db: AsyncSession, make_user):
    seg = await _segmento(db, make_user)
    await _linha(db, seg, "dg048", pasta="/Celular/Fossibot F117", custo="1150.37")
    await _linha(db, seg, "a020", pasta="/Celular/smart glass", custo="0")
    with structlog.testing.capture_logs() as logs:
        r = await client.get(URL, headers={**UR, "X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    assert r.status_code == 200
    evento = next(e for e in logs if e["event"] == "sites_custos")
    assert evento["site"] == "uranyx"
    assert evento["ip"] == "203.0.113.9"
    assert evento["total"] == 2
    assert evento["sem_custo"] == 1
    assert isinstance(evento["ms"], int)
    assert TOK_UR not in repr(logs)
    assert "1150" not in repr(logs)
