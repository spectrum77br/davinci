# ruff: noqa: S105, F811 — tokens de lojas falsas; fixtures importadas dos testes da IA
"""A FICHA DO ANÚNCIO buscada NA LOJA, sob demanda (14/10/2026, `ficha_loja.py`).

O dono: "quero que ela use as fichas; se necessário acessa o produto na loja
pra pegar os dados". Sem ficha fresca nem catálogo válido, a sugestão vai à
loja da conversa. Tudo com lojas FALSAS (nenhuma API de verdade):

- Shopee (`get_item_base_info`), ML (`/items/{id}` + `/description`; a
  fresca do enriquecimento sem descrição ganha só a descrição) e TikTok
  (`/product/202309/products/{id}`): sem cache → busca e usa, com a fonte
  "loja (…), agora"; com cache (positivo ou negativo) → não busca;
- a MESMA limpeza da ficha (sem garantia, contato) e a mesma chave da fresca;
- erro e tempo estourado → segue sem ficha (a sugestão sai), e a busca que
  demorou termina em segundo plano e deixa o cache pronto;
- cache negativo (não existe / sem acesso / sem ficha: 6 h; erro e 401 da
  TikTok: 15 min); o da ida só da descrição do ML é à parte;
- cotas: 2 por sugestão, por loja na rodada do cron, por loja por minuto, e a
  espera somada da rodada;
- ML: só anúncio desta conta (sem `user_id` na credencial, não vai);
- a credencial certa: o cliente é o da loja da CONVERSA (nunca de outra), e
  a renovação de token só pelo caminho de sempre (`clientes`: trava + token
  gravado na hora), nunca chamada daqui;
- leitura desligada, loja arquivada, canal desligado, id estranho, Redis
  fora: não vai;
- de ponta a ponta (modelo falso): a ficha da loja chega ao modelo; no
  automático, a resposta pela ficha continua indo para pessoa.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.token_refresh_lock as trava_token
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.atendimento import clientes, ficha, ficha_loja, ia
from app.services.atendimento.constantes import RASCUNHO_PENDENTE
from tests.test_atendimento_ficha import (
    ATRIBUTOS_ML,
    ITEM_KIA,
    ITEM_KIA_HOJE,
    TITULO_ATUAL,
    _conversa_kia,
    _fatos_do_modelo,
)
from tests.test_atendimento_ia import (  # noqa: F401 — fixtures
    _importar_manual_base,
    _msg,
    _saida,
    envios,
    ia_ligada,
    modelo,
)

SHOPEE, ML, TIKTOK = IntegrationPlatform.SHOPEE, IntegrationPlatform.ML, IntegrationPlatform.TIKTOK
ITEM_ML = "MLB4612514329"
ITEM_TIKTOK = "1735458254156498182"

DESCRICAO_ML = (
    "Acompanha: carregador e capa.\n"
    "Garantia de 90 dias direto com a loja.\n"
    "Dúvidas? Chama no WhatsApp (11) 98765-4321\n"
    "Tela de 6,9 polegadas."
)
CORPO_ML = {
    "id": ITEM_ML,
    "title": "Xiaomi Redmi 15c 128 GB",
    "seller_id": 555,
    "attributes": ATRIBUTOS_ML,
}
PRODUTO_TIKTOK = {
    "code": 0,
    "message": "Success",
    "data": {
        "id": ITEM_TIKTOK,
        "title": "Uranyx A17 Pro Max TikTok",
        "description": (
            "<p>Tela 6.75 120Hz</p><p>Garantia de 3 meses com a loja</p>"
            "<ul><li>NFC: Sim</li><li>Visite www.lojakia.com.br</li></ul>"
        ),
        "product_attributes": [
            {"id": "1", "name": "Memória RAM", "values": [{"id": "9", "name": "12 GB"}]},
            {"id": "2", "name": "Garantia", "values": [{"id": "3", "name": "3 meses"}]},
            {"id": "4", "name": "Vazio", "values": []},
        ],
        "brand": {"id": "7", "name": "Uranyx"},
        "skus": [{"id": "1", "seller_sku": "dg054.ci"}],
    },
}


# ─────────────── Redis e lojas falsas ───────────────


class RedisMem:
    """O Redis que a ficha, a busca e a trava de token usam, em memória (com o TTL)."""

    def __init__(self) -> None:
        self.dados: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.chaves_gravadas: list[str] = []
        self.fora = False

    def _checar(self) -> None:
        if self.fora:
            raise ConnectionError("redis fora")

    async def get(self, chave: str) -> str | None:
        self._checar()
        return self.dados.get(chave)

    async def set(self, chave: str, valor: Any, ex: int | None = None, nx: bool = False):
        self._checar()
        if nx and chave in self.dados:
            return None
        self.dados[chave] = str(valor)
        self.ttls[chave] = ex
        self.chaves_gravadas.append(chave)
        return True

    async def incr(self, chave: str) -> int:
        self._checar()
        n = int(self.dados.get(chave) or 0) + 1
        self.dados[chave] = str(n)
        return n

    async def expire(self, chave: str, segundos: int) -> bool:
        self.ttls[chave] = segundos
        return True

    async def ttl(self, chave: str) -> int:
        self._checar()
        if chave not in self.dados:
            return -2
        t = self.ttls.get(chave)
        return -1 if t is None else t

    async def delete(self, *chaves: str) -> int:
        n = 0
        for chave in chaves:
            n += self.dados.pop(chave, None) is not None
            self.ttls.pop(chave, None)
        return n

    def guardada(self, plataforma: str, integration_id, item_id: str) -> dict | None:
        bruto = self.dados.get(ficha.chave_ficha(plataforma, integration_id, item_id))
        return json.loads(bruto) if bruto else None

    def negativo(self, plataforma: str, integration_id, item_id: str) -> str | None:
        return self.dados.get(ficha_loja.chave_sem_ficha(plataforma, integration_id, item_id))


def _erro_http(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.exemplo/x?access_token=segredo")
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=req, response=httpx.Response(status, request=req)
    )


class Lojas:
    """O que `clientes.factory.client_for` devolve: um cliente falso por plataforma."""

    def __init__(self) -> None:
        self.shopee: dict[str, dict] = {}
        self.ml: dict[str, dict] = {}
        self.ml_descricao: dict[str, str | None] = {}
        self.tiktok: dict[str, dict] = {}
        self.erro: BaseException | None = None
        self.demora = 0.0
        self.vencer_token = False
        self.criados: list[tuple] = []  # (plataforma, creds, integration_id)
        self.chamadas: list[tuple] = []  # (plataforma, método, item, loja das credenciais)
        self.refreshes: list[str] = []

    def fabrica(self, platform, creds, on_token_refresh=None, integration_id=None):
        self.criados.append((platform, dict(creds), integration_id))
        classe = {SHOPEE: ShopeeFalsa, ML: MLFalso, TIKTOK: TikTokFalso}[platform]
        return classe(self, creds, on_token_refresh)

    def metodos(self) -> list[str]:
        return [c[1] for c in self.chamadas]


class _Falsa:
    plataforma = ""

    def __init__(self, lojas: Lojas, creds: dict, on_token_refresh) -> None:
        self.lojas = lojas
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    async def refresh(self) -> None:
        self.lojas.refreshes.append(self.plataforma)
        self.creds = {**self.creds, "access_token": "novo"}
        if self._on_refresh:
            await self._on_refresh(self.creds)

    async def _ida(self, metodo: str, item: Any) -> None:
        # Como o `_request` dos clientes: token vencido → `self.refresh()` — o
        # embrulhado por `clientes` (trava + token gravado na hora).
        if self.lojas.vencer_token and self.creds.get("access_token") != "novo":
            await self.refresh()
        self.lojas.chamadas.append((self.plataforma, metodo, str(item), self.creds.get("loja")))
        if self.lojas.demora:
            await asyncio.sleep(self.lojas.demora)
        if self.lojas.erro is not None:
            raise self.lojas.erro


class ShopeeFalsa(_Falsa):
    plataforma = "shopee"

    async def get_item_base_info(self, item_ids: list) -> list[dict]:
        await self._ida("get_item_base_info", item_ids[0])
        return [self.lojas.shopee[str(i)] for i in item_ids if str(i) in self.lojas.shopee]


class MLFalso(_Falsa):
    plataforma = "ml"

    async def get_item(self, item_id: str) -> dict:
        await self._ida("get_item", item_id)
        if item_id not in self.lojas.ml:
            raise _erro_http(404)
        return self.lojas.ml[item_id]

    async def descricao_do_item(self, item_id: str) -> str | None:
        await self._ida("descricao_do_item", item_id)
        return self.lojas.ml_descricao.get(item_id)


class TikTokFalso(_Falsa):
    plataforma = "tiktok"

    async def produto_bruto(self, product_id: str) -> dict:
        await self._ida("produto_bruto", product_id)
        return self.lojas.tiktok.get(product_id, {"code": 404, "message": "not found"})


@pytest.fixture(autouse=True)
def redis_mem(monkeypatch) -> RedisMem:
    r = RedisMem()
    monkeypatch.setattr(ficha, "redis", r)
    monkeypatch.setattr(ficha_loja, "redis", r)
    monkeypatch.setattr(trava_token, "redis", r)
    return r


@pytest.fixture(autouse=True)
def leitura(monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", True)
    return get_settings()


@pytest.fixture(autouse=True)
def lojas(monkeypatch) -> Lojas:
    falsas = Lojas()
    monkeypatch.setattr(clientes.factory, "client_for", falsas.fabrica)
    return falsas


@pytest.fixture(autouse=True)
async def sem_busca_pendurada():
    yield
    # A busca que passou do tempo segue em segundo plano: termina dentro do teste.
    if ficha_loja._EM_SEGUNDO_PLANO:
        await asyncio.gather(*ficha_loja._EM_SEGUNDO_PLANO, return_exceptions=True)


async def _segundo_plano() -> None:
    await asyncio.gather(*ficha_loja._EM_SEGUNDO_PLANO, return_exceptions=True)


async def _loja(
    db: AsyncSession, user, platform: IntegrationPlatform, nome: str, **creds
) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=platform,
        name=nome,
        credentials=encrypt_json(
            {"access_token": "velho", "refresh_token": "rt-velho", "loja": nome, **creds}
        ),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


def _conv(
    integ: Integration,
    plataforma: str = "shopee",
    *,
    anuncio: str | None = None,
    canal: str = "chat",
    dados: dict | None = None,
    canal_id=None,
) -> AtendimentoConversa:
    return AtendimentoConversa(
        plataforma=plataforma,
        canal=canal,
        integration_id=integ.id,
        anuncio_id=anuncio,
        dados=dados or {},
        canal_id=canal_id,
    )


def _cartao(item: str, titulo: str | None = None) -> list[AtendimentoMensagem]:
    return [
        AtendimentoMensagem(
            autor="cliente",
            anexos=[{"tipo": "produto", "item_id": item, "titulo": titulo}],
            enviada_em=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
        )
    ]


# ─────────────── as peças puras ───────────────


def test_fonte_da_loja_diz_a_idade_sem_numero():
    agora = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)

    def fonte(ha: timedelta | None) -> str:
        quando = None if ha is None else (agora - ha).isoformat()
        return ficha.fonte_da_loja(SHOPEE, quando, agora)

    assert fonte(timedelta(minutes=5)) == "loja (Shopee), agora"
    assert fonte(timedelta(hours=5)) == "loja (Shopee), há menos de um dia"
    assert fonte(timedelta(days=2)) == "loja (Shopee), há até três dias"
    assert fonte(None) == "loja (Shopee)"  # gravada antes da data
    assert ficha.fonte_da_loja(TIKTOK, agora.isoformat(), agora) == "loja (TikTok Shop), agora"
    # Nenhum número: os FATOS valem na conferência de número inventado.
    for ha in (timedelta(0), timedelta(hours=3), timedelta(days=3)):
        assert not re.search(r"\d", fonte(ha))


def test_ficha_bruta_da_tiktok_e_do_ml_com_descricao():
    b = ficha.ficha_bruta_tiktok(PRODUTO_TIKTOK["data"])
    assert b["titulo"] == "Uranyx A17 Pro Max TikTok" and "NFC: Sim" in b["descricao"]
    assert b["raw"]["brand"] == {"original_brand_name": "Uranyx"}
    assert {a["name"] for a in b["raw"]["product_attributes"]} == {"Memória RAM", "Garantia"}
    f = ficha.montar_ficha(b["titulo"], b["descricao"], b["raw"])
    # A MESMA limpeza: sem garantia (nome nem frase) e sem contato.
    assert f["atributos"] == ["Marca: Uranyx", "Memória RAM: 12 GB"]
    assert "Tela 6.75 120Hz" in f["descricao"] and "NFC: Sim" in f["descricao"]
    assert "Garantia" not in f["descricao"] and "lojakia" not in f["descricao"]
    assert ficha.ficha_bruta_tiktok({"title": "só título"}) is None

    ml = ficha.ficha_bruta_ml(CORPO_ML, DESCRICAO_ML)
    assert ml["descricao"] == DESCRICAO_ML
    assert ficha.ficha_bruta_ml(CORPO_ML)["descricao"] is None  # o enriquecimento


def test_falta_descricao_so_no_ml():
    assert ficha_loja.falta_descricao(ML, {"raw": {}, "descricao": None})
    assert not ficha_loja.falta_descricao(ML, {"raw": {}, "descricao": "x"})
    assert not ficha_loja.falta_descricao(ML, {"raw": {}, "descricao_buscada": True})
    assert not ficha_loja.falta_descricao(SHOPEE, {"raw": {}, "descricao": None})


# ─────────────── Shopee ───────────────


async def test_shopee_sem_cache_busca_na_loja_e_usa(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    """O cartão de hoje fora do catálogo e sem ficha fresca: a ficha vem da
    Shopee, agora — limpa, com a fonte —, e fica na chave da ficha fresca."""
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    lojas.shopee[ITEM_KIA] = ITEM_KIA_HOJE
    conversa = _conv(kia)

    (f,) = await ficha.fichas_da_conversa(
        db, conversa, _cartao(ITEM_KIA, TITULO_ATUAL), ["tem NFC?"]
    )

    assert f["de_onde"] == ficha.DE_ONDE_CARTAO and f["fonte"] == "loja (Shopee), agora"
    assert f["titulo"] == TITULO_ATUAL and "NFC - Tem" in f["descricao"]
    assert "Garantia" not in f["descricao"] and "Battery Capacity: 5000 mAh" in f["atributos"]
    assert lojas.chamadas == [("shopee", "get_item_base_info", ITEM_KIA, "kia")]
    # O cliente é o da loja da conversa, montado pelo caminho de sempre.
    ((plat, creds, integ_id),) = lojas.criados
    assert plat == SHOPEE and creds["loja"] == "kia" and integ_id == kia.id
    # Guardada na MESMA chave e no mesmo formato da ficha fresca do enriquecimento.
    guardada = redis_mem.guardada("shopee", kia.id, ITEM_KIA)
    assert guardada["titulo"] == TITULO_ATUAL and "attribute_list" in guardada["raw"]
    assert guardada["buscada_em"]
    assert redis_mem.ttls[ficha.chave_ficha("shopee", kia.id, ITEM_KIA)] == ficha.CACHE_FICHA_S
    # A marca de "em andamento" saiu.
    assert not [k for k in redis_mem.dados if k.startswith("atd:ficha_busca:")]

    # De novo (outra sugestão): do cache, sem ida à loja.
    (f2,) = await ficha.fichas_da_conversa(db, conversa, _cartao(ITEM_KIA, TITULO_ATUAL))
    assert f2["descricao"] == f["descricao"] and len(lojas.chamadas) == 1


async def test_com_ficha_fresca_ou_catalogo_valido_nao_vai_a_loja(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    lojas.shopee[ITEM_KIA] = ITEM_KIA_HOJE
    bruta = ficha.ficha_bruta_shopee(ITEM_KIA_HOJE)
    await redis_mem.set(ficha.chave_ficha("shopee", kia.id, ITEM_KIA), json.dumps(bruta), ex=100)

    (f,) = await ficha.fichas_da_conversa(db, _conv(kia), _cartao(ITEM_KIA, TITULO_ATUAL))

    # A fresca gravada antes da data (`buscada_em`): a fonte é só a loja.
    assert f["fonte"] == "loja (Shopee)" and "NFC - Tem" in f["descricao"]
    assert lojas.chamadas == [] and lojas.criados == []


async def test_leitura_desligada_nao_vai_a_loja(db: AsyncSession, make_user, lojas: Lojas, leitura):
    leitura.atendimento_leitura_ativa = False
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    lojas.shopee[ITEM_KIA] = ITEM_KIA_HOJE

    assert await ficha.fichas_da_conversa(db, _conv(kia, anuncio=ITEM_KIA), []) == []
    assert lojas.chamadas == [] and lojas.criados == []


async def test_erro_da_loja_segue_sem_ficha_e_espera_para_tentar_de_novo(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    lojas.shopee[ITEM_KIA] = ITEM_KIA_HOJE
    lojas.erro = RuntimeError("shopee_item_base_info error_server: algo com token=segredo")
    conversa = _conv(kia, anuncio=ITEM_KIA)

    assert await ficha.fichas_da_conversa(db, conversa, []) == []
    assert redis_mem.negativo("shopee", kia.id, ITEM_KIA) == ficha_loja.RESULTADO_ERRO
    chave = ficha_loja.chave_sem_ficha("shopee", kia.id, ITEM_KIA)
    assert redis_mem.ttls[chave] == ficha_loja.CACHE_FALHA_S
    # Dentro da espera: nem tenta (a loja já voltou, mas o cache manda).
    lojas.erro = None
    assert await ficha.fichas_da_conversa(db, conversa, []) == []
    assert len(lojas.chamadas) == 1
    # Passada a espera: busca.
    await redis_mem.delete(chave)
    (f,) = await ficha.fichas_da_conversa(db, conversa, [])
    assert f["fonte"] == "loja (Shopee), agora" and len(lojas.chamadas) == 2


async def test_tempo_estourado_segue_sem_ficha_e_a_busca_termina_em_segundo_plano(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem, monkeypatch
):
    monkeypatch.setattr(ficha_loja, "TEMPO_MAX_BUSCA_S", 0.05)
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    lojas.shopee[ITEM_KIA] = ITEM_KIA_HOJE
    lojas.demora = 0.3
    conversa = _conv(kia, anuncio=ITEM_KIA)

    assert await ficha.fichas_da_conversa(db, conversa, []) == []
    # Enquanto a busca anda, outra sugestão não abre uma busca nova.
    assert await ficha.fichas_da_conversa(db, conversa, []) == []
    assert len(lojas.chamadas) == 1

    await _segundo_plano()  # a busca BLINDADA termina e grava o cache
    lojas.demora = 0.0
    (f,) = await ficha.fichas_da_conversa(db, conversa, [])
    assert "NFC - Tem" in f["descricao"] and len(lojas.chamadas) == 1


async def test_cache_negativo_quando_a_loja_diz_que_nao_existe(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    """O cartão de um anúncio de OUTRA loja (a Shopee devolve vazio), o
    anúncio apagado (ML 404), o produto sem acesso (TikTok 105005): 6 h sem
    tentar."""
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    ml = await _loja(db, user, ML, "kia-ml", user_id=555)
    tiktok = await _loja(db, user, TIKTOK, "kia-tiktok")
    lojas.tiktok[ITEM_TIKTOK] = {"code": 105005, "message": "Access denied"}

    assert await ficha.fichas_da_conversa(db, _conv(kia), _cartao("999")) == []
    assert await ficha.fichas_da_conversa(db, _conv(ml, "ml", anuncio="MLB1"), []) == []
    assert (
        await ficha.fichas_da_conversa(db, _conv(tiktok, "tiktok", anuncio=ITEM_TIKTOK), []) == []
    )

    assert redis_mem.negativo("shopee", kia.id, "999") == ficha_loja.RESULTADO_SEM_FICHA
    assert redis_mem.negativo("ml", ml.id, "MLB1") == ficha_loja.RESULTADO_NAO_ENCONTRADO
    assert redis_mem.negativo("tiktok", tiktok.id, ITEM_TIKTOK) == (
        ficha_loja.RESULTADO_NAO_ENCONTRADO
    )
    for plat, integ, item in (
        ("shopee", kia, "999"),
        ("ml", ml, "MLB1"),
        ("tiktok", tiktok, ITEM_TIKTOK),
    ):
        chave = ficha_loja.chave_sem_ficha(plat, integ.id, item)
        assert redis_mem.ttls[chave] == ficha_loja.CACHE_SEM_FICHA_S
    feitas = len(lojas.chamadas)
    assert await ficha.fichas_da_conversa(db, _conv(kia), _cartao("999")) == []
    assert await ficha.fichas_da_conversa(db, _conv(ml, "ml", anuncio="MLB1"), []) == []
    assert len(lojas.chamadas) == feitas


# ─────────────── Mercado Livre ───────────────


async def test_ml_sem_cache_busca_o_anuncio_e_a_descricao(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    user = await make_user()
    ml = await _loja(db, user, ML, "kia-ml", user_id=555)
    lojas.ml[ITEM_ML] = CORPO_ML
    lojas.ml_descricao[ITEM_ML] = DESCRICAO_ML
    conversa = _conv(ml, "ml", canal="pergunta", anuncio=ITEM_ML, dados={"item_id": ITEM_ML})

    (f,) = await ficha.fichas_da_conversa(db, conversa, [], ["tem nfc?"])

    assert lojas.metodos() == ["get_item", "descricao_do_item"]
    assert f["fonte"] == "loja (Mercado Livre), agora" and f["de_onde"] == ficha.DE_ONDE_PERGUNTA
    assert f["atributos"][0] == "Com NFC: Sim"
    assert "Acompanha: carregador e capa." in f["descricao"]
    assert "Tela de 6,9 polegadas." in f["descricao"]
    assert "Garantia" not in f["descricao"] and "WhatsApp" not in f["descricao"]
    assert redis_mem.guardada("ml", ml.id, ITEM_ML)["descricao_buscada"] is True

    # Anúncio de OUTRA conta (o `/items` do ML é público): não é ficha desta loja.
    lojas.ml["MLB2"] = {**CORPO_ML, "id": "MLB2", "seller_id": 777}
    assert await ficha.fichas_da_conversa(db, _conv(ml, "ml", anuncio="MLB2"), []) == []
    assert redis_mem.negativo("ml", ml.id, "MLB2") == ficha_loja.RESULTADO_SEM_FICHA

    # Credencial sem `user_id` (não dá para conferir o vendedor): nem vai à loja.
    sem_conta = await _loja(db, user, ML, "ml-sem-conta")
    feitas = len(lojas.chamadas)
    assert await ficha.fichas_da_conversa(db, _conv(sem_conta, "ml", anuncio=ITEM_ML), []) == []
    assert len(lojas.chamadas) == feitas


async def test_ml_fresca_sem_descricao_ganha_so_a_descricao(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    """A ficha fresca do enriquecimento (o `/items` sem o texto): uma ida só,
    à descrição; entra na MESMA chave, com o tempo que ela ainda tinha."""
    user = await make_user()
    ml = await _loja(db, user, ML, "kia-ml")
    lojas.ml_descricao[ITEM_ML] = DESCRICAO_ML
    chave = ficha.chave_ficha("ml", ml.id, ITEM_ML)
    fresca = {**ficha.ficha_bruta_ml(CORPO_ML), "buscada_em": "2026-10-14T10:00:00+00:00"}
    await redis_mem.set(chave, json.dumps(fresca), ex=4321)
    conversa = _conv(ml, "ml", canal="pergunta", anuncio=ITEM_ML)

    (f,) = await ficha.fichas_da_conversa(db, conversa, [])

    assert lojas.metodos() == ["descricao_do_item"]
    assert "Acompanha: carregador e capa." in f["descricao"] and "Com NFC: Sim" in f["atributos"]
    guardada = redis_mem.guardada("ml", ml.id, ITEM_ML)
    assert guardada["descricao_buscada"] is True and guardada["buscada_em"] == fresca["buscada_em"]
    assert redis_mem.ttls[chave] == 4321

    # Sem descrição na loja (404): marcada, e não vai de novo.
    lojas.ml_descricao.clear()
    await redis_mem.set(chave, json.dumps(fresca), ex=4321)
    (f,) = await ficha.fichas_da_conversa(db, conversa, [])
    assert "descricao" not in f and "Com NFC: Sim" in f["atributos"]
    await ficha.fichas_da_conversa(db, conversa, [])
    assert lojas.metodos() == ["descricao_do_item", "descricao_do_item"]

    # Falhou a descrição: a ficha sai com os atributos (como antes).
    lojas.erro = _erro_http(500)
    await redis_mem.set(chave, json.dumps(fresca), ex=4321)
    (f,) = await ficha.fichas_da_conversa(db, conversa, [])
    assert "Com NFC: Sim" in f["atributos"] and "descricao" not in f


async def test_negativo_da_descricao_nao_trava_a_busca_inteira(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    """A descrição recusada (403) na ida só dela: o negativo é DELA. Vencida
    a fresca, a busca inteira (`/items` + descrição) vai normalmente."""
    user = await make_user()
    ml = await _loja(db, user, ML, "kia-ml", user_id=555)
    chave = ficha.chave_ficha("ml", ml.id, ITEM_ML)
    await redis_mem.set(chave, json.dumps(ficha.ficha_bruta_ml(CORPO_ML)), ex=4321)
    conversa = _conv(ml, "ml", canal="pergunta", anuncio=ITEM_ML)
    lojas.erro = _erro_http(403)

    (f,) = await ficha.fichas_da_conversa(db, conversa, [])

    assert "Com NFC: Sim" in f["atributos"] and "descricao" not in f
    sem_descricao = ficha_loja.chave_sem_descricao("ml", ml.id, ITEM_ML)
    assert redis_mem.dados[sem_descricao] == ficha_loja.RESULTADO_NAO_ENCONTRADO
    assert redis_mem.ttls[sem_descricao] == ficha_loja.CACHE_SEM_FICHA_S
    assert redis_mem.negativo("ml", ml.id, ITEM_ML) is None
    # Com a fresca ainda guardada: a descrição não vai de novo.
    await ficha.fichas_da_conversa(db, conversa, [])
    assert lojas.metodos() == ["descricao_do_item"]

    # A fresca venceu e a loja voltou: a busca inteira vai.
    await redis_mem.delete(chave)
    lojas.erro = None
    lojas.ml[ITEM_ML] = CORPO_ML
    lojas.ml_descricao[ITEM_ML] = DESCRICAO_ML
    (f,) = await ficha.fichas_da_conversa(db, conversa, [])
    assert lojas.metodos() == ["descricao_do_item", "get_item", "descricao_do_item"]
    assert "Acompanha: carregador e capa." in f["descricao"]


# ─────────────── TikTok ───────────────


async def test_tiktok_busca_o_produto_com_a_credencial_da_loja_da_conversa(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    user = await make_user()
    poofy = await _loja(db, user, TIKTOK, "poofy", shop_cipher="cipher-poofy")
    inova = await _loja(db, user, TIKTOK, "inova", shop_cipher="cipher-inova")
    lojas.tiktok[ITEM_TIKTOK] = PRODUTO_TIKTOK
    cartao = _cartao(ITEM_TIKTOK, "A17 TikTok")

    (f,) = await ficha.fichas_da_conversa(db, _conv(inova, "tiktok"), cartao)

    assert f["fonte"] == "loja (TikTok Shop), agora" and f["titulo"] == "Uranyx A17 Pro Max TikTok"
    assert f["atributos"] == ["Marca: Uranyx", "Memória RAM: 12 GB"]
    assert "NFC: Sim" in f["descricao"] and "Garantia" not in f["descricao"]
    # O cliente (e o shop_cipher) da INOVA — a loja da conversa —, nunca o da Poofy.
    ((plat, creds, integ_id),) = lojas.criados
    assert plat == TIKTOK and creds["shop_cipher"] == "cipher-inova" and integ_id == inova.id
    assert lojas.chamadas == [("tiktok", "produto_bruto", ITEM_TIKTOK, "inova")]
    assert redis_mem.guardada("tiktok", inova.id, ITEM_TIKTOK) is not None
    assert redis_mem.guardada("tiktok", poofy.id, ITEM_TIKTOK) is None

    # A Poofy não vê a ficha da Inova: busca com a credencial dela.
    (f2,) = await ficha.fichas_da_conversa(db, _conv(poofy, "tiktok"), cartao)
    assert f2["titulo"] == f["titulo"] and lojas.criados[-1][1]["shop_cipher"] == "cipher-poofy"

    # Erro passageiro da TikTok (código no corpo): sem ficha, espera curta.
    lojas.tiktok["555"] = {"code": 36009003, "message": "Internal error"}
    assert await ficha.fichas_da_conversa(db, _conv(inova, "tiktok", anuncio="555"), []) == []
    assert redis_mem.negativo("tiktok", inova.id, "555") == ficha_loja.RESULTADO_ERRO

    # HTTP 401 (o `_get` devolve o HTTP como `code`): autenticação, não "não
    # existe" — espera curta, mesmo com "not found" na mensagem.
    lojas.tiktok["556"] = {"code": 401, "message": "access token not found"}
    assert await ficha.fichas_da_conversa(db, _conv(inova, "tiktok", anuncio="556"), []) == []
    assert redis_mem.negativo("tiktok", inova.id, "556") == ficha_loja.RESULTADO_ERRO
    chave = ficha_loja.chave_sem_ficha("tiktok", inova.id, "556")
    assert redis_mem.ttls[chave] == ficha_loja.CACHE_FALHA_S


# ─────────────── token: só o caminho de sempre ───────────────


async def test_renovacao_do_token_so_pelo_caminho_dos_clientes(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    """O token venceu no meio da busca: quem renova é o `refresh` EMBRULHADO
    por `clientes` (trava da integração, token novo gravado na hora no banco)
    — uma vez só; a busca nunca renova por conta própria."""
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    lojas.shopee[ITEM_KIA] = ITEM_KIA_HOJE
    lojas.vencer_token = True

    (f,) = await ficha.fichas_da_conversa(db, _conv(kia, anuncio=ITEM_KIA), [])

    assert "NFC - Tem" in f["descricao"]
    assert lojas.refreshes == ["shopee"]
    assert f"token_refresh_lock:{kia.id}" in redis_mem.chaves_gravadas
    await db.refresh(kia)
    assert decrypt_json(kia.credentials)["access_token"] == "novo"


# ─────────────── cotas ───────────────


async def test_no_maximo_duas_idas_por_sugestao(db: AsyncSession, make_user, lojas: Lojas):
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    for item in ("1001", "1002", "1003"):
        lojas.shopee[item] = {**ITEM_KIA_HOJE, "item_id": int(item), "item_name": f"Item {item}"}
    conversa = _conv(
        kia, dados={"pedido_mkt": {"itens": [{"item_id": i} for i in ("1001", "1002", "1003")]}}
    )

    fichas = await ficha.fichas_da_conversa(db, conversa, [])

    assert [f["titulo"] for f in fichas] == ["Item 1001", "Item 1002"]
    assert [c[2] for c in lojas.chamadas] == ["1001", "1002"]
    busca = ficha_loja.Busca(kia.id, None, restam=0)
    assert await ficha_loja.ficha_da_loja(db, busca, SHOPEE, "1003") is None
    assert len(lojas.chamadas) == 2


async def test_cota_da_rodada_e_do_minuto_por_loja(
    db: AsyncSession, make_user, lojas: Lojas, monkeypatch
):
    monkeypatch.setattr(ficha_loja, "MAX_BUSCAS_POR_RODADA", 2)
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    mega = await _loja(db, user, SHOPEE, "mega")
    for item in ("1", "2", "3", "4"):
        lojas.shopee[item] = {**ITEM_KIA_HOJE, "item_id": int(item)}

    with ficha_loja.rodada():
        for item in ("1", "2", "3"):
            await ficha.fichas_da_conversa(db, _conv(kia, anuncio=item), [])
        # A cota é POR LOJA: a outra loja ainda vai.
        (f,) = await ficha.fichas_da_conversa(db, _conv(mega, anuncio="4"), [])
    assert [(c[2], c[3]) for c in lojas.chamadas] == [("1", "kia"), ("2", "kia"), ("4", "mega")]
    # Fora da rodada (o "Sugerir" da tela): o teto é o do minuto.
    await ficha.fichas_da_conversa(db, _conv(kia, anuncio="3"), [])
    assert lojas.chamadas[-1][2] == "3"

    monkeypatch.setattr(ficha_loja, "MAX_BUSCAS_POR_MINUTO", 3)  # a kia já foi 3 vezes
    lojas.shopee["5"] = {**ITEM_KIA_HOJE, "item_id": 5}
    assert await ficha.fichas_da_conversa(db, _conv(kia, anuncio="5"), []) == []
    assert len(lojas.chamadas) == 4


async def test_a_rodada_do_cron_abre_a_cota(db: AsyncSession, ia_ligada, monkeypatch):
    vistas: list = []

    async def _rodada(session, *, limite, modos):
        vistas.append(ficha_loja._RODADA.get())
        return 0

    monkeypatch.setattr(ia, "_gerar_pendentes", _rodada)
    assert ficha_loja._RODADA.get() is None
    await ia.gerar_pendentes(db)
    (vista,) = vistas
    assert vista is not None and vista.por_loja == {} and vista.espera_s == 0
    assert ficha_loja._RODADA.get() is None


async def test_a_espera_somada_da_rodada_tem_teto(
    db: AsyncSession, make_user, lojas: Lojas, monkeypatch
):
    """A rodada do cron também chama o modelo, dentro do tempo do job: passou
    do teto de espera somada, o resto da rodada segue sem ir à loja (nem a
    outra loja). Fora da rodada (o "Sugerir"), vai."""
    monkeypatch.setattr(ficha_loja, "MAX_ESPERA_POR_RODADA_S", 0.05)
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    mega = await _loja(db, user, SHOPEE, "mega")
    for item in ("1", "2"):
        lojas.shopee[item] = {**ITEM_KIA_HOJE, "item_id": int(item)}
    lojas.demora = 0.1

    with ficha_loja.rodada():
        (f,) = await ficha.fichas_da_conversa(db, _conv(kia, anuncio="1"), [])
        assert await ficha.fichas_da_conversa(db, _conv(mega, anuncio="2"), []) == []
    assert [c[2] for c in lojas.chamadas] == ["1"]

    (f,) = await ficha.fichas_da_conversa(db, _conv(mega, anuncio="2"), [])
    assert [c[2] for c in lojas.chamadas] == ["1", "2"]


# ─────────────── quando não vai ───────────────


async def test_loja_arquivada_canal_desligado_id_estranho_e_redis_fora_nao_vao(
    db: AsyncSession, make_user, lojas: Lojas, redis_mem: RedisMem
):
    user = await make_user()
    kia = await _loja(db, user, SHOPEE, "kia")
    lojas.shopee[ITEM_KIA] = ITEM_KIA_HOJE
    canal = AtendimentoCanal(integration_id=kia.id, plataforma="shopee", canal="chat")
    canal.status = "desligado"
    db.add(canal)
    await db.commit()

    # Canal desligado (o interruptor do 429).
    assert (
        await ficha.fichas_da_conversa(db, _conv(kia, anuncio=ITEM_KIA, canal_id=canal.id), [])
        == []
    )
    # Id que não é de anúncio (vai no caminho da URL).
    busca = ficha_loja.Busca(kia.id, None)
    assert await ficha_loja.ficha_da_loja(db, busca, SHOPEE, "../orders") is None
    # Redis fora: sem cache nem trava, não vai.
    redis_mem.fora = True
    assert await ficha.fichas_da_conversa(db, _conv(kia, anuncio=ITEM_KIA), []) == []
    redis_mem.fora = False
    # Plataforma sem busca (Magalu) e loja arquivada.
    magalu = await _loja(db, user, IntegrationPlatform.MAGALU, "magalu")
    assert await ficha.fichas_da_conversa(db, _conv(magalu, "magalu", anuncio="SKU-1"), []) == []
    kia.archived_at = datetime.now(UTC)
    await db.commit()
    assert await ficha.fichas_da_conversa(db, _conv(kia, anuncio=ITEM_KIA), []) == []
    assert lojas.chamadas == [] and lojas.criados == []


# ─────────────── de ponta a ponta (modelo falso) ───────────────


async def test_ficha_da_loja_chega_ao_modelo_e_no_automatico_vai_para_pessoa(
    db: AsyncSession, make_user, ia_ligada, modelo, envios, lojas: Lojas
):
    """A conversa da Kia sem ficha fresca e com o catálogo de maio (que não
    vale): a sugestão vai à Shopee e o modelo recebe a ficha de AGORA. No
    automático, a resposta tirada da ficha não sai sozinha."""
    ia_ligada.atendimento_auto_ativo = True
    await _importar_manual_base(db)
    lojas.shopee[ITEM_KIA] = ITEM_KIA_HOJE
    conversa = await _conversa_kia(
        db, make_user, None, fresca=False, modo="auto", auto_categorias=["conteudo_da_caixa"]
    )
    await _msg(db, conversa, "o que vem na caixa?", ha=timedelta(minutes=1))
    modelo.saida = _saida(
        "Olá! Acompanha apenas o cabo de carregamento.",
        categoria="conteudo_da_caixa",
        confianca=0.95,
    )

    r = await ia.gerar_rascunho(db, conversa)

    (f,) = _fatos_do_modelo(modelo.usuario)["ficha_do_anuncio"]
    assert f["fonte"] == "loja (Shopee), agora" and "NFC - Tem" in f["descricao"]
    assert "Memoria Interna" not in modelo.usuario  # o catálogo de maio não chega
    assert r.fatos["ficha_do_anuncio"] == [f]
    assert r.status == RASCUNHO_PENDENTE and r.precisa_humano
    assert ia.MOTIVO_FICHA_NO_AUTOMATICO in r.motivo
    assert envios == []


async def test_loja_fora_do_ar_a_sugestao_sai_sem_ficha(
    db: AsyncSession, make_user, ia_ligada, modelo, envios, lojas: Lojas
):
    lojas.erro = _erro_http(503)
    conversa = await _conversa_kia(db, make_user, None, fresca=False)
    modelo.saida = _saida(
        "Olá! A equipe confirma esse dado para você.",
        categoria="duvida_produto",
        precisa_humano=True,
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and r.status == RASCUNHO_PENDENTE
    assert "ficha_do_anuncio" not in r.fatos and "ficha_do_anuncio" not in modelo.usuario
    assert lojas.metodos() == ["get_item_base_info"]
