"""Shopee Vídeo — a credencial da conta (troca do code e renovação).

O refresh token do app de vídeo é de USO ÚNICO: renovou, o velho morre. O que
este arquivo prova, com a Shopee fakada por HTTP (respx) — zero rede:

  • o par novo é GRAVADO e COMITADO antes de ser devolvido pra uso;
  • duas renovações ao mesmo tempo viram UMA chamada à Shopee (trava de
    linha): a segunda espera e relê o token novo;
  • "token recusado" só renova se o do banco ainda for o recusado;
  • a cadeia morta (refresh vencido) vira `expirado` e pede autorizar de
    novo — sem tentar em loop;
  • o retorno da autorização só aceita a loja da integração.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Marca, RedeSocial, RedeSocialToken
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketing import shopee_video as sv
from app.services.marketing import shopee_video_conta as conta

pytestmark = pytest.mark.asyncio

CHAVE = "chave-falsa-do-app-de-video-0123456789"
USER = 987654321
REFRESH_URL = f"{sv.API_HOST}{sv.PATH_RENOVAR}"


async def _conta(
    db: AsyncSession, *, expira_em_s: int, status: str = "ok", refresh: str = "refresh-1"
) -> RedeSocial:
    m = Marca(nome="Uranyx", slug="uranyx")
    db.add(m)
    await db.flush()
    r = RedeSocial(marca_id=m.id, plataforma="shopee", conta="barbosa", ativo=True)
    db.add(r)
    await db.flush()
    db.add(
        RedeSocialToken(
            rede_social_id=r.id,
            provedor="shopee",
            external_user_id=str(USER),
            status=status,
            token_enc=encrypt_json(
                {
                    "partner_id": 2047721,
                    "partner_key": CHAVE,
                    "access_token": "access-1",
                    "refresh_token": refresh,
                    "expires_at": int(datetime.now(UTC).timestamp()) + expira_em_s,
                    "user_id": USER,
                    "shop_id": 1725800210,
                }
            ),
            token_expires_at=datetime.now(UTC) + timedelta(days=5),
        )
    )
    await db.commit()
    await db.refresh(r)
    return r


async def _blob(db: AsyncSession, rede_id) -> tuple[dict, RedeSocialToken]:
    tok = (
        await db.execute(
            select(RedeSocialToken)
            .where(RedeSocialToken.rede_social_id == rede_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return decrypt_json(tok.token_enc), tok


@respx.mock
async def test_token_valido_nao_chama_a_shopee(db):
    r = await _conta(db, expira_em_s=3600)
    rota = respx.post(REFRESH_URL)
    cred = await conta.credencial(r.id)
    assert cred.access_token == "access-1" and cred.user_id == USER
    assert rota.call_count == 0
    assert "access-1" not in repr(cred) and CHAVE not in repr(cred)


@respx.mock
async def test_renova_grava_antes_de_usar_e_recarimba_30_dias(db):
    r = await _conta(db, expira_em_s=60)  # vence em 1 min (< 5 min de margem)
    rota = respx.post(REFRESH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "error": "",
                "access_token": "access-2",
                "refresh_token": "refresh-2",
                "expire_in": 14400,
                "request_id": "rq",
            },
        )
    )
    cred = await conta.credencial(r.id)
    assert cred.access_token == "access-2"
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo == {"refresh_token": "refresh-1", "partner_id": 2047721, "user_id": USER}
    blob, tok = await _blob(db, r.id)
    assert blob["refresh_token"] == "refresh-2" and blob["access_token"] == "access-2"
    assert tok.token_expires_at > datetime.now(UTC) + timedelta(days=29)
    assert tok.status == "ok"
    # Em claro no banco, nada do segredo (nem pelo hex do bytea).
    linha = (
        await db.execute(
            text("SELECT t::text FROM redes_sociais_tokens t WHERE rede_social_id = :r"),
            {"r": r.id},
        )
    ).scalar_one()
    for segredo in ("access-2", "refresh-2", CHAVE):
        assert segredo not in linha
        assert segredo.encode().hex() not in linha


async def test_duas_renovacoes_juntas_viram_uma_chamada(db, monkeypatch):
    r = await _conta(db, expira_em_s=10)
    chamadas: list[str] = []

    async def renovar(self, refresh_token, user_id):
        chamadas.append(refresh_token)
        await asyncio.sleep(0.3)  # segura a trava enquanto a outra chega
        return {
            "access_token": f"access-{len(chamadas) + 1}",
            "refresh_token": f"refresh-{len(chamadas) + 1}",
            "expire_in": 14400,
        }

    monkeypatch.setattr(sv.ClienteShopeeVideo, "renovar", renovar)
    a, b = await asyncio.gather(conta.credencial(r.id), conta.credencial(r.id))
    assert chamadas == ["refresh-1"]  # UMA renovação, com o refresh vivo
    assert a.access_token == b.access_token == "access-2"


async def test_recusado_so_renova_se_ainda_for_o_do_banco(db, monkeypatch):
    r = await _conta(db, expira_em_s=3600)
    chamadas: list[str] = []

    async def renovar(self, refresh_token, user_id):
        chamadas.append(refresh_token)
        return {"access_token": "access-novo", "refresh_token": "refresh-novo", "expire_in": 14400}

    monkeypatch.setattr(sv.ClienteShopeeVideo, "renovar", renovar)
    # Outro processo já renovou: o recusado não é mais o do banco.
    cred = await conta.credencial(r.id, recusado="access-velho")
    assert chamadas == [] and cred.access_token == "access-1"
    cred = await conta.credencial(r.id, recusado="access-1")
    assert chamadas == ["refresh-1"] and cred.access_token == "access-novo"


@respx.mock
async def test_cadeia_morta_vira_expirado_e_pede_autorizar(db):
    r = await _conta(db, expira_em_s=10)
    respx.post(REFRESH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "error": "error_param",
                "message": "refresh_token_expired",
                "request_id": "rq-9",
            },
        )
    )
    with pytest.raises(conta.ReautorizarError) as ei:
        await conta.credencial(r.id)
    assert "autorize de novo" in ei.value.mensagem
    _blob_atual, tok = await _blob(db, r.id)
    assert tok.status == "expirado"
    assert "rq-9" in tok.last_error and CHAVE not in tok.last_error
    # E não tenta de novo: a próxima já recusa sem chamar a Shopee.
    with pytest.raises(conta.ReautorizarError):
        await conta.credencial(r.id)
    assert respx.calls.call_count == 1


@respx.mock
async def test_shopee_fora_do_ar_nao_mata_a_cadeia(db):
    r = await _conta(db, expira_em_s=10)
    respx.post(REFRESH_URL).mock(side_effect=httpx.ConnectTimeout("x"))
    with pytest.raises(conta.ContaShopeeError) as ei:
        await conta.credencial(r.id)
    assert not isinstance(ei.value, conta.ReautorizarError)
    _b, tok = await _blob(db, r.id)
    assert tok.status == "ok"


async def test_cron_renova_so_quem_esta_perto_de_vencer(db, monkeypatch):
    r = await _conta(db, expira_em_s=3600)  # token_expires_at = +5 dias
    chamadas: list[str] = []

    async def renovar(self, refresh_token, user_id):
        chamadas.append(refresh_token)
        return {"access_token": "a2", "refresh_token": "r2", "expire_in": 14400}

    monkeypatch.setattr(sv.ClienteShopeeVideo, "renovar", renovar)
    out = await conta.renovar_todas()
    assert out["renovadas"] == 1 and chamadas == ["refresh-1"]
    # Recarimbado pra +30 dias: a próxima rodada não renova de novo.
    out = await conta.renovar_todas()
    assert out["contas"] == 0
    assert r.id is not None


def test_escolhe_o_user_id_da_loja_certa():
    resp = {"shop_id_list": [111, 1725800210], "user_id_list": [5, USER]}
    assert conta.escolher_user_id(resp, 1725800210) == (USER, 1725800210)
    with pytest.raises(conta.ContaShopeeError) as ei:
        conta.escolher_user_id(resp, 999)
    assert ei.value.code == "loja_errada"
    with pytest.raises(conta.ContaShopeeError) as ei:
        conta.escolher_user_id({"shop_id_list": [1725800210], "user_id_list": []}, 1725800210)
    assert ei.value.code == "autorizacao_sem_user_id"


@pytest.mark.parametrize(
    ("erro", "mensagem", "reautorizar"),
    [
        # A autorização (até 365 dias) venceu: só autorizando de novo.
        ("user_access_expired", "The access of user is expired", True),
        ("shop_access_expired", "Your shop authorization is expired", True),
        # Relógio fora de hora NÃO é autorização vencida.
        ("error_param", "Timestamp is expired.", False),
    ],
)
@respx.mock
async def test_autorizacao_vencida_pede_autorizar_e_relogio_nao(db, erro, mensagem, reautorizar):
    r = await _conta(db, expira_em_s=10)
    respx.post(REFRESH_URL).mock(
        return_value=httpx.Response(
            200, json={"error": erro, "message": mensagem, "request_id": "rq-exp"}
        )
    )
    with pytest.raises(conta.ContaShopeeError) as ei:
        await conta.credencial(r.id)
    assert isinstance(ei.value, conta.ReautorizarError) is reautorizar
    _b, tok = await _blob(db, r.id)
    assert tok.status == ("expirado" if reautorizar else "ok")


def test_conta_principal_sem_lista_de_lojas_so_com_a_loja_provada():
    """Retorno pela conta principal (`main_account_id`): a resposta sem
    `shop_id_list` não diz de que loja é o user_id. Sem o `shop_id` do
    retorno batendo com o da integração, nada é gravado."""
    resp = {"shop_id_list": [], "user_id_list": [USER]}
    with pytest.raises(conta.ContaShopeeError) as ei:
        conta.escolher_user_id(resp, 1725800210)
    assert ei.value.code == "loja_nao_confirmada"
    assert conta.escolher_user_id(resp, 1725800210, loja_confirmada=True) == (USER, 1725800210)
    # Mesmo provada, dois usuários sem loja continuam ambíguos.
    with pytest.raises(conta.ContaShopeeError) as ei:
        conta.escolher_user_id(
            {"shop_id_list": [], "user_id_list": [1, 2]}, 1725800210, loja_confirmada=True
        )
    assert ei.value.code == "autorizacao_ambigua"


async def test_conta_bloqueada_segue_renovando_e_nao_se_libera_sozinha(db, monkeypatch):
    """Bloqueada (Termos/toggle) pode levar semanas: o cron renova a cadeia
    mesmo assim — e renovar NÃO libera a conta nem apaga o motivo."""
    r = await _conta(db, expira_em_s=10, status="bloqueado")
    await db.execute(
        text(
            "UPDATE redes_sociais_tokens SET last_error = 'Shopee: Termos'"
            " WHERE rede_social_id = :r"
        ),
        {"r": r.id},
    )
    await db.commit()
    chamadas: list[str] = []

    async def renovar(self, refresh_token, user_id):
        chamadas.append(refresh_token)
        return {"access_token": "a2", "refresh_token": f"r{len(chamadas) + 1}", "expire_in": 14400}

    monkeypatch.setattr(sv.ClienteShopeeVideo, "renovar", renovar)
    cred = await conta.credencial(r.id)  # o reconciliador e as métricas ainda leem
    assert cred.access_token == "a2" and chamadas == ["refresh-1"]
    blob, tok = await _blob(db, r.id)
    assert tok.status == "bloqueado" and tok.last_error == "Shopee: Termos"
    assert blob["refresh_token"] == "r2"
    # O cron diário também a renova (token_expires_at recarimbado → volta pro cron só perto).
    await db.execute(
        text(
            "UPDATE redes_sociais_tokens SET token_expires_at = now() + interval '3 days'"
            " WHERE rede_social_id = :r"
        ),
        {"r": r.id},
    )
    await db.commit()
    out = await conta.renovar_todas()
    assert out["renovadas"] == 1 and chamadas == ["refresh-1", "r2"]
    _b, tok = await _blob(db, r.id)
    assert tok.status == "bloqueado"


async def test_bloquear_registrar_saude_e_liberar(db):
    rid = (await _conta(db, expira_em_s=3600)).id  # o rollback abaixo expira `r`
    await conta.bloquear(rid, motivo="não aceitou os Termos (copyright_not_agree)")
    _b, tok = await _blob(db, rid)
    assert tok.status == "bloqueado" and "Termos" in tok.last_error
    # O erro de uma postagem (ou o sucesso de outra) não apaga o motivo.
    await conta.registrar_saude(rid, erro="Shopee: outra coisa")
    await conta.registrar_saude(rid, erro=None)
    _b, tok = await _blob(db, rid)
    assert tok.status == "bloqueado" and "Termos" in tok.last_error
    # Expirado não é rebaixado pra bloqueado; reautorizar=True ganha.
    await conta.bloquear(rid, motivo="autorização desfeita", reautorizar=True)
    await conta.bloquear(rid, motivo="toggle")
    _b, tok = await _blob(db, rid)
    assert tok.status == "expirado" and "desfeita" in tok.last_error
    # Liberar não inventa autorização: expirado continua pedindo autorizar.
    with pytest.raises(conta.ReautorizarError):
        await conta.liberar(db, rid)
    await db.rollback()
    await db.execute(
        text("UPDATE redes_sociais_tokens SET status = 'bloqueado' WHERE rede_social_id = :r"),
        {"r": rid},
    )
    await db.commit()
    tok = await conta.liberar(db, rid)
    await db.commit()
    _b, tok = await _blob(db, rid)
    assert tok.status == "ok" and tok.last_error is None
