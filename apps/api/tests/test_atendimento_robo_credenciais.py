# ruff: noqa: S105, S106, S608 — segredos e tabela descartáveis dos testes
"""Login automático usa o cadastro da loja DO PERFIL, sem senhas em outros retornos."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.models import StoreInfo
from app.schemas.atendimento_robo import CredenciaisOut
from app.security.cipher import encrypt
from app.services.atendimento import robo_credenciais

URL = "/api/atendimento/robo/credenciais"
TOKEN = "token-robo-credenciais-teste"
H = {"Authorization": f"Bearer {TOKEN}"}
CORPO = {"perfil_id": "k1atv", "plataforma": "temu", "loja": "Nome antigo da configuração"}
USUARIO = "usuario-privado"
SENHA = " senha-privada com espaço "


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_robo_token", TOKEN)


@pytest.fixture
async def espelho(db):
    tabela = f'"{get_settings().database_schema}".adspower'
    await db.execute(
        text(f"CREATE TABLE IF NOT EXISTS {tabela} " "(id text PRIMARY KEY, profile_no text)")
    )
    await db.execute(text(f"TRUNCATE {tabela}"))
    await db.execute(
        text(f"INSERT INTO {tabela} (id, profile_no) VALUES (:id, :no)"),
        {"id": "k1atv", "no": "138"},
    )
    await db.commit()
    return tabela


@pytest.fixture
async def loja(db, make_user, espelho):
    usuario = await make_user()
    loja = StoreInfo(
        user_id=usuario.id,
        platform="Temu",
        account_name="ATV",
        server="138",
        email=USUARIO,
        password_enc=encrypt(SENHA),
    )
    db.add(loja)
    await db.commit()
    return loja


async def test_token_antes_de_ler_corpo_e_buscar_credenciais(client, monkeypatch):
    def proibido(*args, **kwargs):
        pytest.fail("Não pode buscar credenciais sem autorização")

    monkeypatch.setattr(robo_credenciais, "buscar", proibido)
    assert (await client.post(URL, content="{invalido")).status_code == 401
    assert (
        await client.post(URL, json=CORPO, headers={"Authorization": "Bearer errado"})
    ).status_code == 401
    monkeypatch.setattr(get_settings(), "atendimento_robo_token", "")
    assert (await client.post(URL, json=CORPO, headers=H)).status_code == 404


async def test_credenciais_pelo_servidor_e_atualizadas_sem_cache(client, db, loja):
    # O nome informado não casa com o cadastro; a relação é somente pelo perfil.
    r = await client.post(URL, json=CORPO, headers=H)
    assert r.status_code == 200
    assert r.json() == {
        "perfil_id": "k1atv",
        "plataforma": "temu",
        "loja": "ATV",
        "usuario": f"{USUARIO}@tuta.com",
        "senha": SENHA,
    }
    assert r.headers["cache-control"] == "no-store"
    loja.email = "novo-usuario"
    loja.password_enc = encrypt("nova-senha")
    await db.commit()
    r = await client.post(URL, json=CORPO, headers=H)
    assert r.json()["usuario"] == "novo-usuario@tuta.com"
    assert r.json()["senha"] == "nova-senha"


@pytest.mark.parametrize(
    ("cadastrado", "esperado"),
    [
        ("Usuario@Exemplo.com", "usuario@exemplo.com"),
        ("sac@parcial", "sac@parcial"),
    ],
)
async def test_email_completo_resolvido_e_parcial_preservado(
    client, db, loja, cadastrado, esperado
):
    loja.email = cadastrado
    await db.commit()
    r = await client.post(URL, json=CORPO, headers=H)
    assert r.status_code == 200
    assert r.json()["usuario"] == esperado


async def test_cadastro_com_nome_igual_em_outro_perfil_nao_vaza(client, db, loja):
    loja.server = "999"
    loja.account_name = CORPO["loja"]
    await db.commit()
    r = await client.post(URL, json=CORPO, headers=H)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "robo_loja_nao_encontrada"
    assert SENHA not in r.text and USUARIO not in r.text


async def test_so_temu_sem_aceitar_segredo_no_erro_de_validacao(client):
    r = await client.post(URL, json={**CORPO, "plataforma": "aliexpress"}, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "corpo_invalido"
    r = await client.post(URL, json={**CORPO, "perfil_id": SENHA}, headers=H)
    assert r.status_code == 422
    assert "senha-privada" not in r.text


@pytest.mark.parametrize("campo", ["email", "password_enc"])
async def test_credencial_ausente_sem_dados_no_erro(client, db, loja, campo):
    setattr(loja, campo, None)
    await db.commit()
    r = await client.post(URL, json=CORPO, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "robo_credenciais_incompletas"
    assert r.headers["cache-control"] == "no-store"
    assert SENHA not in r.text and USUARIO not in r.text


async def test_loja_arquivada_ou_plataforma_diferente_nao_entrega(client, db, loja):
    loja.archived_at = datetime.now(UTC)
    await db.commit()
    assert (await client.post(URL, json=CORPO, headers=H)).status_code == 404
    loja.archived_at = None
    loja.platform = "aliexpress"
    await db.commit()
    assert (await client.post(URL, json=CORPO, headers=H)).status_code == 404


async def test_perfil_sem_numero_ou_desconhecido_nao_entrega(client, db, loja, espelho):
    r = await client.post(URL, json={**CORPO, "perfil_id": "nao-existe"}, headers=H)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "robo_perfil_nao_encontrado"
    await db.execute(text(f"UPDATE {espelho} SET profile_no = NULL"))
    await db.commit()
    assert (await client.post(URL, json=CORPO, headers=H)).status_code == 404


async def test_duas_lojas_ativas_no_mesmo_perfil_nao_escolhe_senha(client, db, loja):
    db.add(
        StoreInfo(
            user_id=loja.user_id,
            platform="temu",
            account_name="Outra",
            server="138",
            email="outro-privado",
            password_enc=encrypt("outra-senha-privada"),
        )
    )
    await db.commit()
    r = await client.post(URL, json=CORPO, headers=H)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "robo_loja_ambigua"
    assert "privad" not in r.text


async def test_falha_inesperada_nao_vaza_texto_do_erro(client, monkeypatch, caplog):
    async def falhar(*args, **kwargs):
        raise ValueError(f"Falhou com {USUARIO} {SENHA}")

    monkeypatch.setattr(robo_credenciais, "buscar", falhar)
    r = await client.post(URL, json=CORPO, headers=H)
    assert r.status_code == 500
    assert r.json() == {"detail": {"code": "robo_erro_interno"}}
    assert r.headers["cache-control"] == "no-store"
    assert SENHA not in r.text + caplog.text and USUARIO not in r.text + caplog.text


async def test_senha_cifrada_invalida_vira_erro_saneado(client, db, loja):
    loja.password_enc = "nao-e-cifra"
    await db.commit()
    r = await client.post(URL, json=CORPO, headers=H)
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "robo_erro_interno"
    assert "nao-e-cifra" not in r.text


def test_repr_dos_dados_nao_mostra_credenciais():
    dados = {"perfil_id": "k1atv", "loja": "ATV", "usuario": USUARIO, "senha": SENHA}
    for modelo in (CredenciaisOut(**dados), robo_credenciais.CredenciaisLoja(**dados)):
        assert USUARIO not in repr(modelo)
        assert SENHA not in repr(modelo)
