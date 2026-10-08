"""Shopee Vídeo × integração da loja — a ÚNICA fronteira entre os dois.

O postador de vídeo encosta na integração Shopee da loja (Stock Sync Hub, o
app de pedidos/estoque/anúncios) num ponto só: `conferir_ao_vivo`, que pergunta
ao anúncio se ele está NORMAL e com estoque antes de vincular o vídeo. A regra
do dono é que a integração não pode mudar de comportamento por causa do vídeo.
O que este arquivo prova, pelo `ShopeeClient` DE VERDADE (respx, zero rede):

  • a resposta da Shopee é lida certo: status do item, estoque somado pelas
    variações (`get_model_list`) no anúncio com variação;
  • com o token da loja válido, a conferência é SÓ LEITURA: as credenciais
    da integração e o `token_expires_at` ficam idênticos, byte a byte;
  • com o token da loja vencido, a renovação passa pelo caminho seguro de
    sempre (`gravar_credenciais`, sessão própria, sob a trava) — nunca pela
    sessão de quem chamou;
  • "não deu pra perguntar" (a lista de variações falhou, o número não veio)
    LEVANTA — nunca vira "sem estoque", que queimaria o vídeo naquela conta.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import Integration, IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.atendimento import clientes
from app.services.marketing import shopee_video_anuncio as anuncio

pytestmark = pytest.mark.asyncio

HOST = "https://partner.shopeemobile.com"
ITEM = 58262693089


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(get_settings(), "shopee_use_sandbox", False)


async def _integracao(db: AsyncSession, user, *, expira_em_s: int) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name="Barbosa",
        credentials=encrypt_json(
            {
                "shop_id": 1725800210,
                "partner_id": 2032110,
                "partner_key": "chave-da-integracao-falsa",
                "access_token": "access-da-loja",
                "refresh_token": "refresh-da-loja",
                "expires_at": int(datetime.now(UTC).timestamp()) + expira_em_s,
            }
        ),
        token_expires_at=datetime.fromtimestamp(2_000_000_000, tz=UTC),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


def _item(**extra) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "error": "",
            "response": {
                "item_list": [
                    {"item_id": ITEM, "item_status": "NORMAL", "has_model": True, **extra}
                ]
            },
        },
    )


def _modelos(*estoques: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "error": "",
            "response": {
                "model": [
                    {
                        "model_id": i,
                        "stock_info_v2": {"summary_info": {"total_available_stock": n}},
                    }
                    for i, n in enumerate(estoques, start=1)
                ]
            },
        },
    )


async def _no_banco(integration_id) -> tuple[bytes, datetime | None]:
    async with _db.SessionLocal() as s:
        integ = await s.get(Integration, integration_id)
        return bytes(integ.credentials), integ.token_expires_at


@respx.mock
async def test_conferencia_le_status_e_soma_variacoes_sem_tocar_na_credencial(db, make_user):
    user = await make_user()
    integ = await _integracao(db, user, expira_em_s=3600)
    antes = await _no_banco(integ.id)
    base = respx.get(f"{HOST}/api/v2/product/get_item_base_info").mock(return_value=_item())
    modelos = respx.get(f"{HOST}/api/v2/product/get_model_list").mock(return_value=_modelos(26, 30))
    refresh = respx.post(f"{HOST}/api/v2/auth/access_token/get")

    c = await anuncio.conferir_ao_vivo(db, integ.id, ITEM)

    assert c.ok is True and c.status == "NORMAL" and c.estoque == 56
    assert base.call_count == 1 and modelos.call_count == 1 and refresh.call_count == 0
    # Só leitura: o blob e a validade da integração idênticos, byte a byte.
    assert await _no_banco(integ.id) == antes
    assert integ not in db.dirty
    # E a pergunta saiu pelo app da INTEGRAÇÃO (é o único com permissão de produto).
    q = base.calls.last.request.url.params
    assert q["partner_id"] == "2032110" and q["shop_id"] == "1725800210"


@respx.mock
async def test_item_pausado_ou_sem_estoque_e_motivo_definitivo(db, make_user):
    user = await make_user()
    integ = await _integracao(db, user, expira_em_s=3600)
    respx.get(f"{HOST}/api/v2/product/get_item_base_info").mock(
        return_value=_item(item_status="UNLIST")
    )
    c = await anuncio.conferir_ao_vivo(db, integ.id, ITEM)
    assert c.ok is False and "UNLIST" in c.motivo

    respx.get(f"{HOST}/api/v2/product/get_item_base_info").mock(return_value=_item())
    respx.get(f"{HOST}/api/v2/product/get_model_list").mock(return_value=_modelos(0, 0))
    c = await anuncio.conferir_ao_vivo(db, integ.id, ITEM)
    assert c.ok is False and c.estoque == 0 and "sem estoque" in c.motivo


@pytest.mark.parametrize(
    "falha",
    [
        httpx.Response(200, json={"error": "error_server", "message": "system busy"}),
        httpx.Response(429, json={"error": "error_rate_limit", "message": "Too many requests"}),
        httpx.Response(200, json={"error": "", "response": {"model": []}}),
    ],
)
@respx.mock
async def test_nao_deu_pra_perguntar_levanta_nunca_vira_sem_estoque(db, make_user, falha):
    """Anúncio com variação (todos os da Barbosa): o item não traz o estoque.
    Sem a lista de variações, a resposta é "não sei" — quem chama espera e
    pergunta de novo no próximo tique."""
    user = await make_user()
    integ = await _integracao(db, user, expira_em_s=3600)
    respx.get(f"{HOST}/api/v2/product/get_item_base_info").mock(return_value=_item())
    respx.get(f"{HOST}/api/v2/product/get_model_list").mock(return_value=falha)
    with pytest.raises(Exception):  # noqa: B017 — qualquer uma: o publicador trata igual
        await anuncio.conferir_ao_vivo(db, integ.id, ITEM)


@respx.mock
async def test_resumo_do_item_basta_quando_a_lista_de_variacoes_falha(db, make_user):
    user = await make_user()
    integ = await _integracao(db, user, expira_em_s=3600)
    respx.get(f"{HOST}/api/v2/product/get_item_base_info").mock(
        return_value=_item(stock_info_v2={"summary_info": {"total_available_stock": 12}})
    )
    respx.get(f"{HOST}/api/v2/product/get_model_list").mock(
        return_value=httpx.Response(200, json={"error": "error_server", "message": "busy"})
    )
    c = await anuncio.conferir_ao_vivo(db, integ.id, ITEM)
    assert c.ok is True and c.estoque == 12


@respx.mock
async def test_token_da_loja_vencido_renova_pelo_caminho_seguro_da_integracao(
    db, make_user, monkeypatch
):
    """A renovação do token DA LOJA (uso único) acontece como em todo lugar
    que já lê a loja: sob a trava, gravada por `gravar_credenciais` numa
    sessão própria — a sessão de quem chamou nunca regrava credencial."""
    trava: list[str] = []

    @asynccontextmanager
    async def _lock(integration_id):
        trava.append(str(integration_id))
        yield True

    monkeypatch.setattr(clientes, "token_refresh_lock", _lock)
    gravacoes: list[dict] = []
    gravar_original = clientes.gravar_credenciais

    async def gravar(integration_id, creds):
        gravacoes.append(dict(creds))
        return await gravar_original(integration_id, creds)

    monkeypatch.setattr(clientes, "gravar_credenciais", gravar)

    user = await make_user()
    integ = await _integracao(db, user, expira_em_s=-60)
    iid = integ.id  # o rollback do fim expira `integ`
    refresh = respx.post(f"{HOST}/api/v2/auth/access_token/get").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "access-novo",
                "refresh_token": "refresh-novo",
                "expire_in": 14400,
            },
        )
    )
    respx.get(f"{HOST}/api/v2/product/get_item_base_info").mock(return_value=_item())
    respx.get(f"{HOST}/api/v2/product/get_model_list").mock(return_value=_modelos(5))

    c = await anuncio.conferir_ao_vivo(db, iid, ITEM)

    assert c.ok is True and c.estoque == 5
    assert refresh.call_count == 1
    assert trava == [str(iid)]
    assert len(gravacoes) == 1 and gravacoes[0]["refresh_token"] == "refresh-novo"
    # Gravado no banco por fora da sessão do chamador — e ela não ficou suja.
    blob, _expira = await _no_banco(iid)
    novas = decrypt_json(blob)
    assert novas["access_token"] == "access-novo" and novas["refresh_token"] == "refresh-novo"
    assert novas["partner_id"] == 2032110 and novas["shop_id"] == 1725800210
    assert integ not in db.dirty
    await db.rollback()  # o rollback do chamador não desfaz o token novo
    relida = (
        await db.execute(select(Integration.credentials).where(Integration.id == iid))
    ).scalar_one()
    assert decrypt_json(relida)["refresh_token"] == "refresh-novo"
