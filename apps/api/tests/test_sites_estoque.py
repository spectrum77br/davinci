"""Estoque para os sites Charlots e Uranyx — o que a porta deixa passar e o que não.

Mesma ordem dos testes do portal das agências: primeiro o que trava a
porta (**sem configuração, fechada**), depois o recorte de cada site — o
token diz o site, o site diz os SKUs, e nada do request alarga isso —, e por
fim a lista branca da resposta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.enums import IntegrationPlatform, LinkSyncStatus, SyncLogAction
from app.models.product import Product
from app.models.sync_log import SyncLog
from app.routers import sites_estoque as rota
from app.services.rate_limit import RateLimitError

URL = "/api/sites/estoque"
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


async def _dono(make_user) -> UUID:
    return (await make_user()).id


async def _produto(
    db: AsyncSession,
    user_id: UUID,
    sku: str,
    *,
    stock: int = 5,
    situacao: str | None = "A",
    formato: str | None = "S",
    name: str | None = None,
) -> Product:
    p = Product(
        user_id=user_id,
        sku=sku,
        name=name or f"Produto {sku}",
        stock=stock,
        situacao=situacao,
        formato=formato,
        price=199,
        cost_price=87,
    )
    db.add(p)
    await db.commit()
    return p


async def _catalogo(db: AsyncSession, user_id: UUID) -> None:
    """Um pouco de cada família — o que cada site pode e não pode ver."""
    for sku, formato in (
        ("b009.24", "S"),  # mala avulsa
        ("b049", "E"),  # kit completo
        ("b005.20+a075+bp003", "S"),  # mala com acessório: kit pelo `+`
        ("bp001", "S"),  # mochila
        ("a003.pi", "S"),  # acessório: dos dois sites
        ("dg053.ci", "S"),  # celular
        ("dg053.pi+a003.pi", "E"),  # kit de celular
        ("uaf001m1.110", "S"),  # eletro
        ("z005.20.mala", "S"),  # salvado de devolução: de ninguém
        ("zb009.24", "S"),
        ("i15.128", "S"),  # Apple: revenda, de ninguém
        ("bx01", "S"),  # `b` sem dígito: não é mala
        ("a100", "S"),  # não é a0NN
    ):
        await _produto(db, user_id, sku, formato=formato)


def _skus(r) -> list[str]:
    return [i["sku"] for i in r.json()["itens"]]


# ─────────────── fecha por padrão ───────────────


async def test_sem_configuracao_a_rota_fica_fechada(client: AsyncClient, monkeypatch):
    """Segredo vazio = 401, NUNCA aberto."""
    monkeypatch.setattr(get_settings(), "sites_estoque_tokens", "")
    r = await client.get(URL, headers=CH)
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "sites_nao_autorizado"}


async def test_so_espacos_e_virgulas_tambem_fecha(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(get_settings(), "sites_estoque_tokens", " , :charlots, tok: ,")
    assert (await client.get(URL, headers=_auth("tok"))).status_code == 401


async def test_sem_header_401(client: AsyncClient):
    r = await client.get(URL)
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "sites_nao_autorizado"}


@pytest.mark.parametrize(
    "valor",
    [
        TOK_CH,  # sem esquema
        f"Basic {TOK_CH}",
        f"Token {TOK_CH}",
        "Bearer",
        "Bearer ",
        f"Bearer{TOK_CH}",
    ],
)
async def test_esquema_errado_401(client: AsyncClient, valor: str):
    r = await client.get(URL, headers={"Authorization": valor})
    assert r.status_code == 401


async def test_token_errado_401(client: AsyncClient):
    assert (await client.get(URL, headers=_auth("chute"))).status_code == 401
    # Prefixo de um token de verdade também não passa.
    assert (await client.get(URL, headers=_auth(TOK_CH[:-1]))).status_code == 401


async def test_token_com_acento_da_401_e_nao_500(client: AsyncClient):
    """`compare_digest` com str fora do ASCII levanta TypeError. Aqui é em bytes."""
    r = await client.get(URL, headers={"Authorization": "Bearer tok-ção".encode()})
    assert r.status_code == 401


async def test_site_sem_recorte_401(client: AsyncClient, db: AsyncSession, make_user):
    """Token válido de um site que não está em ESCOPOS = porta sem parede."""
    await _catalogo(db, await _dono(make_user))
    r = await client.get(URL, headers=_auth(TOK_SEM_RECORTE))
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "sites_nao_autorizado"}


async def test_nao_autorizado_nem_chega_no_limite(
    client: AsyncClient, chamadas_do_limite: list[str]
):
    """Quem sonda sem token não gasta a cota do site verdadeiro."""
    await client.get(URL)
    await client.get(URL, headers=_auth("chute"))
    assert chamadas_do_limite == []


async def test_esquema_bearer_em_minusculas_passa(client: AsyncClient):
    """RFC 7235: o esquema não diferencia maiúsculas."""
    r = await client.get(URL, headers={"Authorization": f"bearer {TOK_CH}"})
    assert r.status_code == 200


# ─────────────── cada site vê o seu recorte ───────────────


async def test_charlots_ve_so_malas_mochilas_e_acessorios(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _catalogo(db, await _dono(make_user))
    r = await client.get(URL, headers=CH)
    assert r.status_code == 200, r.text
    assert r.json()["site"] == "charlots"
    assert _skus(r) == ["a003.pi", "b005.20+a075+bp003", "b009.24", "b049", "bp001"]
    for sku in _skus(r):
        assert not sku.lower().startswith(("dg", "u", "z", "i"))


async def test_uranyx_ve_so_celular_eletro_e_acessorios(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _catalogo(db, await _dono(make_user))
    r = await client.get(URL, headers=UR)
    assert r.status_code == 200, r.text
    assert r.json()["site"] == "uranyx"
    assert _skus(r) == ["a003.pi", "dg053.ci", "dg053.pi+a003.pi", "uaf001m1.110"]
    for sku in _skus(r):
        assert not sku.lower().startswith(("b", "z", "i"))


async def test_sku_maiusculo_casa_o_recorte(client: AsyncClient, db: AsyncSession, make_user):
    dono = await _dono(make_user)
    await _produto(db, dono, "B010.20")
    await _produto(db, dono, "DG088.CI")
    assert _skus(await client.get(URL, headers=CH)) == ["B010.20"]
    assert _skus(await client.get(URL, headers=UR)) == ["DG088.CI"]


@pytest.mark.parametrize("situacao", ["E", "I", None])
async def test_excluido_inativo_ou_desconhecido_nao_sai(
    client: AsyncClient, db: AsyncSession, make_user, situacao
):
    """Excluído no Bling ainda carrega estoque velho (não recebe mais webhook)."""
    dono = await _dono(make_user)
    await _produto(db, dono, "b035.28", situacao=situacao, stock=40)
    await _produto(db, dono, "b035.20", stock=3)
    r = await client.get(URL, headers=CH)
    assert _skus(r) == ["b035.20"]
    assert r.json()["total"] == 1


async def test_mesmo_sku_em_dois_donos_sai_uma_vez(
    client: AsyncClient, db: AsyncSession, make_user
):
    """Sem filtro de user_id (kits dg0xx são de um admin); o dedupe é por lower(sku)."""
    a, b = await _dono(make_user), await _dono(make_user)
    await _produto(db, a, "dg061.pi+a003.pi", formato="E", stock=7)
    await _produto(db, b, "DG061.PI+A003.PI", formato="E", stock=7)
    await _produto(db, b, "dg062.ci", stock=2)
    r = await client.get(URL, headers=UR)
    assert [s.lower() for s in _skus(r)] == ["dg061.pi+a003.pi", "dg062.ci"]
    assert r.json()["total"] == 2


async def test_ordenado_por_sku_sem_diferenciar_maiusculas(
    client: AsyncClient, db: AsyncSession, make_user
):
    dono = await _dono(make_user)
    for sku in ("b020.24", "B009.24", "b001.10", "A006"):
        await _produto(db, dono, sku)
    assert _skus(await client.get(URL, headers=CH)) == ["A006", "b001.10", "B009.24", "b020.24"]


# ─────────────── os números ───────────────


async def test_estoque_negativo_vira_zero(client: AsyncClient, db: AsyncSession, make_user):
    """O webhook grava o valor cru; o Bling às vezes manda -1."""
    dono = await _dono(make_user)
    await _produto(db, dono, "b020.24", stock=-1)
    await _produto(db, dono, "b049", stock=11, formato="E")
    itens = {i["sku"]: i for i in (await client.get(URL, headers=CH)).json()["itens"]}
    assert itens["b020.24"]["estoque"] == 0
    assert itens["b049"]["estoque"] == 11


async def test_marca_de_kit(client: AsyncClient, db: AsyncSession, make_user):
    dono = await _dono(make_user)
    await _produto(db, dono, "b049", formato="E")
    await _produto(db, dono, "b005.20+a075", formato="S")
    await _produto(db, dono, "b005.20", formato="S")
    await _produto(db, dono, "b006.20", formato=None)
    kit = {i["sku"]: i["kit"] for i in (await client.get(URL, headers=CH)).json()["itens"]}
    assert kit == {"b005.20": False, "b005.20+a075": True, "b006.20": False, "b049": True}
    assert all(type(v) is bool for v in kit.values())


async def test_ultima_releitura_do_bling(client: AsyncClient, db: AsyncSession, make_user):
    dono = await _dono(make_user)
    agora = datetime.now(UTC).replace(microsecond=0)
    recente = agora - timedelta(hours=2)

    def _log(
        quando,
        *,
        status=LinkSyncStatus.OK,
        action=SyncLogAction.REFRESH_BLING,
        platform=IntegrationPlatform.BLING,
    ):
        db.add(
            SyncLog(
                user_id=dono, created_at=quando, platform=platform, action=action, status=status
            )
        )

    _log(recente)
    _log(agora - timedelta(hours=60))  # fora da janela de 48 h
    _log(agora - timedelta(minutes=5), status=LinkSyncStatus.FATAL)  # falhou: não conta
    _log(agora - timedelta(minutes=4), action=SyncLogAction.UPDATE_STOCK)  # outra ação
    _log(agora - timedelta(minutes=3), platform=IntegrationPlatform.ML)  # outro canal
    await db.commit()

    corpo = (await client.get(URL, headers=CH)).json()
    assert datetime.fromisoformat(corpo["ultima_releitura_bling_em"]) == recente
    assert corpo["ultima_releitura_bling_em"].endswith("+00:00")
    gerado = datetime.fromisoformat(corpo["gerado_em"])
    assert gerado.utcoffset() == timedelta(0)
    assert abs((gerado - datetime.now(UTC)).total_seconds()) < 60


async def test_sem_releitura_recente_e_null(client: AsyncClient, db: AsyncSession, make_user):
    dono = await _dono(make_user)
    db.add(
        SyncLog(
            user_id=dono,
            created_at=datetime.now(UTC) - timedelta(hours=49),
            platform=IntegrationPlatform.BLING,
            action=SyncLogAction.REFRESH_BLING,
            status=LinkSyncStatus.OK,
        )
    )
    await db.commit()
    assert (await client.get(URL, headers=CH)).json()["ultima_releitura_bling_em"] is None


# ─────────────── o filtro `sku` ───────────────


async def test_filtro_sku_exato_sem_diferenciar_maiusculas(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _catalogo(db, await _dono(make_user))
    r = await client.get(URL, headers=CH, params=[("sku", "B049"), ("sku", "b009.24")])
    assert r.status_code == 200
    assert _skus(r) == ["b009.24", "b049"]
    assert r.json()["total"] == 2
    # Exato: prefixo não traz a família inteira.
    assert _skus(await client.get(URL, headers=CH, params={"sku": "b00"})) == []


async def test_filtro_sku_nunca_sai_do_recorte(client: AsyncClient, db: AsyncSession, make_user):
    """Pedir um celular com o token da Charlots devolve nada — não 'o celular'."""
    await _catalogo(db, await _dono(make_user))
    r = await client.get(
        URL,
        headers=CH,
        params=[
            ("sku", "dg053.ci"),
            ("sku", "uaf001m1.110"),
            ("sku", "z005.20.mala"),
            ("sku", "i15.128"),
        ],
    )
    assert r.status_code == 200
    assert r.json()["itens"] == [] and r.json()["total"] == 0
    r = await client.get(URL, headers=UR, params=[("sku", "b049"), ("sku", "a003.pi")])
    assert _skus(r) == ["a003.pi"]


@pytest.mark.parametrize(
    "ruim",
    [
        "",
        " ",
        "b049 ",
        "b0 49",
        "b049\n",
        "-b049",
        ".b049",
        "b049;drop",
        "b%",
        "b_*",
        "b049/1",
        "ção",
        "a" * 101,
    ],
)
async def test_sku_invalido_422(client: AsyncClient, ruim: str):
    r = await client.get(URL, headers=CH, params=[("sku", "b049"), ("sku", ruim)])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "sites_sku_invalido"


async def test_sku_no_limite_passa_e_acima_422(client: AsyncClient):
    no_limite = [("sku", f"b{i:03d}.20") for i in range(rota.MAX_SKUS_POR_CONSULTA)]
    assert (await client.get(URL, headers=CH, params=no_limite)).status_code == 200
    demais = [*no_limite, ("sku", "b999.20")]
    r = await client.get(URL, headers=CH, params=demais)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "sites_skus_demais"


async def test_sku_valido_com_mais_e_underscore(client: AsyncClient, db: AsyncSession, make_user):
    dono = await _dono(make_user)
    await _produto(db, dono, "b005.20+a075+bp003+a076")
    await _produto(db, dono, "b005_teste-1")
    r = await client.get(
        URL,
        headers=CH,
        params=[("sku", "B005.20+A075+BP003+A076"), ("sku", "b005_teste-1")],
    )
    assert r.status_code == 200
    assert _skus(r) == ["b005.20+a075+bp003+a076", "b005_teste-1"]


# ─────────────── limite por minuto ───────────────


async def test_limite_por_site(client: AsyncClient, chamadas_do_limite: list[str]):
    await client.get(URL, headers=CH)
    await client.get(URL, headers=UR)
    assert chamadas_do_limite == ["sites_estoque:rl:charlots", "sites_estoque:rl:uranyx"]


async def test_limite_estourado_429(client: AsyncClient, monkeypatch):
    async def _estourou(**kw):
        assert kw["limit"] == 30 and kw["window_seconds"] == 60
        raise RateLimitError(retry_after=42)

    monkeypatch.setattr(rota, "sliding_window_check", _estourou)
    r = await client.get(URL, headers=CH)
    assert r.status_code == 429
    assert r.json()["detail"] == {"code": "sites_limite"}
    assert r.headers["Retry-After"] == "42"


async def test_redis_fora_do_ar_nao_vira_500(
    client: AsyncClient, db: AsyncSession, make_user, monkeypatch
):
    """O limite falha aberto: o site continua recebendo o estoque."""
    await _produto(db, await _dono(make_user), "b049", formato="E")

    async def _caiu(**kw):
        raise ConnectionError("redis caiu")

    monkeypatch.setattr(rota, "sliding_window_check", _caiu)
    with structlog.testing.capture_logs() as logs:
        r = await client.get(URL, headers=CH)
    assert r.status_code == 200
    assert _skus(r) == ["b049"]
    assert any(e["event"] == "sites_estoque_rate_limit_indisponivel" for e in logs)


# ─────────────── a lista branca ───────────────


async def test_resposta_so_com_os_campos_do_contrato(
    client: AsyncClient, db: AsyncSession, make_user
):
    dono = await _dono(make_user)
    p = await _produto(db, dono, "b049", formato="E", stock=11, name="Kit 6 Malas M5")
    r = await client.get(URL, headers=CH)
    corpo = r.json()
    assert set(corpo) == {"site", "gerado_em", "ultima_releitura_bling_em", "total", "itens"}
    assert corpo["itens"] == [{"sku": "b049", "nome": "Kit 6 Malas M5", "estoque": 11, "kit": True}]
    assert corpo["total"] == 1
    # id interno, dono, preço e custo ficam em casa.
    for proibido in (str(p.id), str(dono), "199", "87", "price", "cost", "user_id"):
        assert proibido not in r.text, f"vazou {proibido!r}"


async def test_cabecalhos_de_cache(client: AsyncClient):
    r = await client.get(URL, headers=CH)
    assert r.headers["Cache-Control"] == "private, max-age=60"
    assert r.headers["Vary"] == "Authorization"


async def test_fora_do_openapi(client: AsyncClient):
    """O /api/openapi.json é público: não anuncia esta porta."""
    caminhos = (await client.get("/api/openapi.json")).json()["paths"]
    assert not any(c.startswith("/api/sites") for c in caminhos)


async def test_log_sem_token(client: AsyncClient, db: AsyncSession, make_user):
    await _produto(db, await _dono(make_user), "b049")
    with structlog.testing.capture_logs() as logs:
        r = await client.get(URL, headers={**CH, "X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    assert r.status_code == 200
    evento = next(e for e in logs if e["event"] == "sites_estoque")
    assert evento["site"] == "charlots"
    assert evento["ip"] == "203.0.113.9"
    assert evento["total"] == 1
    assert isinstance(evento["ms"], int)
    assert TOK_CH not in repr(logs)
