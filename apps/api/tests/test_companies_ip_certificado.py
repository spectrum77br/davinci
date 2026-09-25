"""Empresas: coluna de IP (um por empresa) e resumo do certificado digital.

Eduardo (25/09/2026): "colocar um ip para cada empresa, e já verifique se não
tem nada duplicado" + "coluna nova de certificado digital, quando clicar
aparece um toggle para colocar a senha".

O que estes testes seguram:
- o IP é guardado na forma canônica e recusa o que não é IP;
- dois CNPJs NÃO podem ter o mesmo IP, e o erro diz de qual empresa ele é;
- editar outra coluna não apaga o IP (o mesmo cuidado que o CNPJ já tem);
- a tabela mostra o certificado só para admin, e nunca o arquivo nem a senha.
"""

import pytest

from app.models import CompanyCertificate, UserRole
from app.schemas.companies import _normalize_ip
from app.security.cipher import encrypt_bytes

EMPRESA_VIEW = {"empresa": {"view": True, "edit": True}}


async def _empresa(client, apelido: str, **extra) -> dict:
    r = await client.post(
        "/api/companies", json={"razao_social": apelido.upper(), "apelido": apelido, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- formato do IP -----------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "guardado"),
    [
        ("72.60.155.3", "72.60.155.3"),
        ("  72.60.155.3  ", "72.60.155.3"),
        ("72.60.155.3:1080", "72.60.155.3"),  # colado com a porta do proxy
        # a linha inteira do proxy, como o AdsPower exporta
        ("72.60.155.3:1080:usuario:senha", "72.60.155.3"),
        ("socks5://usuario:senha@72.60.155.3:1080", "72.60.155.3"),
        # o mesmo IPv4 escrito como IPv6: tem que virar o mesmo IP
        ("::ffff:72.60.155.3", "72.60.155.3"),
        ("::ffff:483c:9b03", "72.60.155.3"),
        ("[2606:4700::1111]:1080", "2606:4700::1111"),
        ("2606:4700::1111", "2606:4700::1111"),
        ("", None),
        (None, None),
    ],
)
def test_ip_e_guardado_na_forma_canonica(entrada, guardado):
    assert _normalize_ip(entrada) == guardado


@pytest.mark.parametrize("entrada", ["72.60.155", "abc", "999.1.1.1", "[2606:4700::1111"])
def test_o_que_nao_e_ip_e_recusado(entrada):
    with pytest.raises(ValueError, match="ip_invalido"):
        _normalize_ip(entrada)


@pytest.mark.parametrize(
    "entrada", ["192.168.0.10", "10.0.0.1", "127.0.0.1", "0.0.0.0", "100.64.0.1", "2001:db8::1"],  # noqa: S104
)
def test_ip_de_rede_interna_e_recusado(entrada):
    """Dez empresas com IPs internos diferentes podem sair todas pelo mesmo IP
    público: aceitar isso daria uma falsa garantia de que não há repetido."""
    with pytest.raises(ValueError, match="ip_nao_publico"):
        _normalize_ip(entrada)


def test_o_erro_nunca_carrega_o_texto_digitado():
    """O texto pode ser a linha do proxy, com a senha."""
    with pytest.raises(ValueError) as e:
        _normalize_ip("abc:SEGREDO123")
    assert "SEGREDO123" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


# --- um IP por empresa -------------------------------------------------------


@pytest.mark.asyncio
async def test_grava_o_ip_da_empresa(client, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa")
    r = await client.patch(f"/api/companies/{e['id']}", json={"ip": "72.60.156.216:1080"})
    assert r.status_code == 200, r.text
    assert r.json()["ip"] == "72.60.156.216"


@pytest.mark.asyncio
async def test_ip_de_outra_empresa_e_recusado_com_o_nome_dela(client, make_user, auth_as):
    """O caso que motivou a coluna: dois CNPJs no mesmo IP é o que o
    marketplace usa para ligar as contas."""
    auth_as(await make_user(role=UserRole.ADMIN))
    kfa = await _empresa(client, "kfa", ip="72.60.156.216")
    dream = await _empresa(client, "dream")
    r = await client.patch(f"/api/companies/{dream['id']}", json={"ip": " 72.60.156.216 "})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "ip_exists", "empresa": "kfa"}
    # e o IP da kfa continua lá
    r = await client.get(f"/api/companies/{kfa['id']}")
    assert r.json()["ip"] == "72.60.156.216"


@pytest.mark.asyncio
async def test_criar_empresa_com_ip_que_ja_existe_e_recusado(client, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _empresa(client, "nexus", ip="72.60.155.3")
    r = await client.post(
        "/api/companies", json={"razao_social": "MOVA", "apelido": "mova", "ip": "72.60.155.3"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["empresa"] == "nexus"


@pytest.mark.asyncio
async def test_regravar_o_proprio_ip_nao_e_conflito(client, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa", ip="72.60.156.216")
    r = await client.patch(f"/api/companies/{e['id']}", json={"ip": "72.60.156.216"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_varias_empresas_podem_ficar_sem_ip(client, make_user, auth_as):
    """Vazio não é "o mesmo IP": empresa nova ainda sem proxy não trava a outra."""
    auth_as(await make_user(role=UserRole.ADMIN))
    await _empresa(client, "a")
    await _empresa(client, "b")
    e = await _empresa(client, "c", ip="1.1.1.1")
    r = await client.patch(f"/api/companies/{e['id']}", json={"ip": ""})
    assert r.status_code == 200
    assert r.json()["ip"] is None


@pytest.mark.asyncio
async def test_editar_outra_coluna_nao_apaga_o_ip(client, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa", ip="72.60.156.216")
    r = await client.patch(f"/api/companies/{e['id']}", json={"obs": "qualquer coisa"})
    assert r.status_code == 200
    assert r.json()["ip"] == "72.60.156.216"


@pytest.mark.asyncio
async def test_ip_invalido_devolve_so_o_codigo(client, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa")
    r = await client.patch(f"/api/companies/{e['id']}", json={"ip": "72.60.155"})
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "ip_invalido"}


@pytest.mark.asyncio
async def test_senha_do_proxy_nunca_volta_na_resposta(client, make_user, auth_as):
    """Colar a linha do proxy grava só o IP; e se algo com senha for recusado,
    a resposta não pode trazer o texto de volta (o 422 padrão traria)."""
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa")
    r = await client.patch(
        f"/api/companies/{e['id']}", json={"ip": "72.60.156.216:1080:usuario:SEGREDO123"}
    )
    assert r.status_code == 200 and r.json()["ip"] == "72.60.156.216"
    assert "SEGREDO123" not in r.text
    r = await client.patch(f"/api/companies/{e['id']}", json={"ip": "usuario:SEGREDO123"})
    assert r.status_code == 422
    assert "SEGREDO123" not in r.text
    r = await client.post(
        "/api/companies", json={"razao_social": "X", "apelido": "x", "ip": "lixo:SEGREDO123"}
    )
    assert r.status_code == 422
    assert "SEGREDO123" not in r.text


@pytest.mark.asyncio
async def test_ip_interno_devolve_codigo_proprio(client, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa")
    r = await client.patch(f"/api/companies/{e['id']}", json={"ip": "192.168.0.10"})
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "ip_nao_publico"}


@pytest.mark.asyncio
async def test_ipv4_escrito_como_ipv6_nao_fura_a_regra(client, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _empresa(client, "kfa", ip="72.60.155.3")
    dream = await _empresa(client, "dream")
    r = await client.patch(f"/api/companies/{dream['id']}", json={"ip": "::ffff:72.60.155.3"})
    assert r.status_code == 409
    assert r.json()["detail"]["empresa"] == "kfa"


# --- certificado na tabela ---------------------------------------------------


async def _certificado(db, company_id, *, senha: str | None):
    cert = CompanyCertificate(
        company_id=company_id,
        filename="kfa.pfx",
        size_bytes=3,
        blob=encrypt_bytes(b"pfx"),
        password_enc=encrypt_bytes(senha.encode()) if senha else None,
    )
    db.add(cert)
    await db.commit()
    return cert


def _linha(grid: dict, apelido: str) -> dict:
    return next(r for r in grid["rows"] if r["company"]["apelido"] == apelido)


@pytest.mark.asyncio
async def test_admin_ve_o_certificado_na_tabela(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa")
    await _empresa(client, "sem-certificado")
    await _certificado(db, e["id"], senha="segredo")

    grid = (await client.get("/api/companies/grid")).json()
    cert = _linha(grid, "kfa")["certificado"]
    assert cert["filename"] == "kfa.pfx"
    assert cert["has_password"] is True
    assert _linha(grid, "sem-certificado")["certificado"] is None


@pytest.mark.asyncio
async def test_a_tabela_nunca_traz_o_arquivo_nem_a_senha(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa")
    await _certificado(db, e["id"], senha="segredo-que-nao-pode-vazar")

    r = await client.get("/api/companies/grid")
    assert "segredo-que-nao-pode-vazar" not in r.text
    cert = _linha(r.json(), "kfa")["certificado"]
    assert set(cert) == {"id", "filename", "has_password", "expires_at", "total"}


@pytest.mark.asyncio
async def test_certificado_sem_senha_aparece_como_sem_senha(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa")
    await _certificado(db, e["id"], senha=None)
    grid = (await client.get("/api/companies/grid")).json()
    assert _linha(grid, "kfa")["certificado"]["has_password"] is False


@pytest.mark.asyncio
async def test_quem_nao_e_admin_nao_ve_o_certificado(client, db, make_user, auth_as):
    """As rotas de certificado são só de admin; a tabela segue a mesma regra."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    e = await _empresa(client, "kfa")
    await _certificado(db, e["id"], senha="segredo")

    auth_as(await make_user(role=UserRole.USER, permissions=EMPRESA_VIEW))
    grid = (await client.get("/api/companies/grid")).json()
    assert _linha(grid, "kfa")["certificado"] is None


@pytest.mark.asyncio
async def test_varios_certificados_mostram_o_mais_recente_e_contam(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    e = await _empresa(client, "kfa")
    await _certificado(db, e["id"], senha=None)
    await _certificado(db, e["id"], senha="nova")
    cert = _linha((await client.get("/api/companies/grid")).json(), "kfa")["certificado"]
    assert cert["total"] == 2


@pytest.mark.asyncio
async def test_o_banco_barra_ip_repetido_mesmo_sem_passar_pela_api(db):
    """A checagem da API dá a mensagem bonita; quem garante de verdade é o
    índice. Duas gravações ao mesmo tempo passariam pela checagem — o índice
    não deixa."""
    from sqlalchemy.exc import IntegrityError

    from app.models import Company

    db.add(Company(razao_social="KFA", apelido="kfa", ip="72.60.156.216"))
    await db.commit()
    db.add(Company(razao_social="DREAM", apelido="dream", ip="72.60.156.216"))
    with pytest.raises(IntegrityError, match="uq_companies_ip"):
        await db.commit()
    await db.rollback()
