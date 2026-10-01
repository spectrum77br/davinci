"""Integrar empresa na NFE.io — Emissão de Serviço › Empresas (01/10/2026).

Eduardo: "algumas empresas nossas não estão integradas no nfe.io, precisa
integrar, aí podemos deixar lá em emissão de serviços - empresas, e com filtro
para ver só os pendentes". A NFE.io aqui é falsa (respx): nenhuma chamada sai.
O que estes testes seguram:
- o corpo que vai para a NFE.io (empresa e Inscrição Municipal, sempre em TESTE);
- CNPJ que já existe lá não é criado de novo; POST sem resposta não repete;
- certificado recusado deixa a empresa ligada e "incompleta", e completar não duplica;
- fora do servidor liberado nada sai (nem a listagem);
- a senha do certificado não aparece em resposta, log, histórico ou banco.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
import respx
import structlog
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Company, User, UserRole
from app.models.company_certificate import CompanyCertificate
from app.models.nfse import CompanyFiscal, NfseChamada
from app.models.pricing import StoreInfo
from app.schemas.nfse import IntegrarIn
from app.security.cipher import encrypt_bytes
from app.services.nfse import ambiente, certificado, integracao, nfeio, receita
from app.services.nfse import emissao as svc
from app.services.nfse import empresas as svc_empresas

API = "https://api.nfe.io"
NFSE2 = "https://api.nfse.io"
CNPJ_PREST = "11222333000181"
CNPJ_OUTRO = "11444777000161"
BRASILAPI = "https://brasilapi.com.br/api/cnpj/v1"
NID = "0123456789abcdef0123456789abcdef"  # id de 32 hex (empresa criada pela v2)
SENHA_CERT = "SENHA-DO-CERT-7319"

RECEITA = {
    "cnpj": CNPJ_PREST,
    "razao_social": "EMPRESA TESTE LTDA",
    "nome_fantasia": "TESTE",
    "descricao_tipo_de_logradouro": "RUA",
    "logradouro": "EXEMPLO",
    "numero": "100",
    "complemento": "SALA 1",
    "bairro": "CENTRO",
    "cep": "01001000",
    "uf": "SP",
    "municipio": "SAO PAULO",
    "codigo_municipio_ibge": 3550308,
    "codigo_natureza_juridica": 2062,
    "natureza_juridica": "Sociedade Empresária Limitada",
    "opcao_pelo_simples": True,
    "opcao_pelo_mei": False,
    "email": "Contato@Empresa.com.br ",
    "descricao_situacao_cadastral": "ATIVA",
    "regime_tributario": [
        {"ano": 2024, "forma_de_tributacao": "LUCRO PRESUMIDO"},
        {"ano": 2025, "forma_de_tributacao": "SIMPLES NACIONAL"},
    ],
}


@pytest.fixture(autouse=True)
def _nfeio_de_teste(monkeypatch):
    """Chave falsa, ambiente local e sem produção liberada — como no localhost."""
    s = get_settings()
    monkeypatch.setattr(s, "nfeio_api_key", "chave-de-teste")
    monkeypatch.setattr(s, "nfeio_base_url", API)
    monkeypatch.setattr(s, "nfeio_nfse_base_url", NFSE2)
    monkeypatch.setattr(s, "env", "development")
    monkeypatch.setattr(s, "nfse_producao_liberada", False)
    monkeypatch.setattr(s, "nfse_integrar_liberado", False)
    monkeypatch.setattr(nfeio, "ESPERA_GET", 0.0)
    svc.limpar_cache()
    yield
    svc.limpar_cache()


class RedisFalso:
    """Redis falso: `set nx` e os dois scripts da trava (soltar/renovar só se o
    token ainda for o dono)."""

    def __init__(self):
        self.d: dict[str, str] = {}
        self.renovacoes = 0

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d:
            return False
        self.d[k] = v
        return True

    async def eval(self, script, numkeys, k, token, *args):
        if self.d.get(k) != token:
            return 0
        if "'del'" in script:
            del self.d[k]
        else:
            self.renovacoes += 1
        return 1


@pytest.fixture(autouse=True)
def redis_falso(monkeypatch) -> RedisFalso:
    r = RedisFalso()

    async def _redis():
        return r

    monkeypatch.setattr(integracao, "_redis", _redis)
    return r


@pytest.fixture
def liberado(monkeypatch):
    monkeypatch.setattr(ambiente, "integrar_liberado", lambda: True)


@pytest_asyncio.fixture
async def operador(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:nfse-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"nfse-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        permissions={"emissao_servico": {"view": True, "edit": True, "delete": True}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# --- ajudantes ---------------------------------------------------------------------


def _pfx(cnpj: str, validade: date, senha: str) -> bytes:
    """Certificado A1 de verdade (chave EC P-256), CN "RAZÃO:CNPJ" como o e-CNPJ."""
    chave = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"EMPRESA TESTE LTDA:{cnpj}")])
    fim = datetime(validade.year, validade.month, validade.day, 12, tzinfo=UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(fim - timedelta(days=800))
        .not_valid_after(fim)
        .sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"teste", chave, cert, None, BestAvailableEncryption(senha.encode())
    )


async def _cert(
    db: AsyncSession,
    company_id,
    pfx: bytes = b"pfx",
    senha: str | None = SENHA_CERT,
    expires_at: date | None = None,
    filename: str = "empresa teste.pfx",
) -> CompanyCertificate:
    c = CompanyCertificate(
        company_id=company_id,
        filename=filename,
        size_bytes=len(pfx),
        blob=encrypt_bytes(pfx),
        password_enc=encrypt_bytes(senha.encode()) if senha is not None else None,
        expires_at=expires_at,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _empresa(db: AsyncSession, cnpj: str = CNPJ_PREST, **kw) -> Company:
    c = Company(razao_social="EMPRESA TESTE LTDA", apelido="teste", cnpj=cnpj, **kw)
    db.add(c)
    await db.commit()
    return c


def _valido() -> date:
    return date.today() + timedelta(days=400)


def _empresa_nfeio(**kw) -> dict:
    e = {
        "id": NID,
        "name": "EMPRESA TESTE LTDA",
        "federalTaxNumber": int(CNPJ_PREST),
        "environment": "Development",
        "fiscalStatus": "Active",
        "status": "Active",
        "taxRegime": "SimplesNacional",
        "address": {
            "city": {"code": "3550308", "name": "São Paulo"},
            "state": "SP",
            "street": "RUA EXEMPLO",
            "number": "100",
            "district": "CENTRO",
            "postalCode": "01001000",
            "country": "BRA",
        },
    }
    e.update(kw)
    return e


def _pascal(obj):
    """Chaves com inicial maiúscula (formato da doc da v2)."""
    if isinstance(obj, dict):
        return {k[:1].upper() + k[1:]: _pascal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_pascal(x) for x in obj]
    return obj


IM = {
    "id": "im1",
    "environment": "Development",
    "fiscalStatus": "Active",
    "status": "Active",
    "taxNumber": "12345678",
    "city": {"code": "3550308", "name": "São Paulo", "state": "SP"},
    "loginPassword": "SENHA-DA-PREFEITURA",
}


class NfeioFalsa:
    """A NFE.io falsa com estado: lista, empresa por id, IMs e os 3 POSTs."""

    def __init__(self, api: respx.MockRouter, *, lista=None, ims=None, cert_ativo=False):
        self.lista: list[dict] = list(lista or [])
        self.ims: list[dict] = list(ims or [])
        self.cert_ativo = cert_ativo
        self.criada: dict | None = None
        # A v1 (adaptador) ainda não mostra a empresa criada pela v2.
        self.v1_atrasada = False
        self.respx = api
        self.brasilapi = api.get(url__startswith=BRASILAPI).mock(
            return_value=httpx.Response(200, json=RECEITA)
        )
        self.listar = api.get(path="/v1/companies", host="api.nfe.io").mock(
            side_effect=lambda req: httpx.Response(
                200, json={"companies": [] if self.v1_atrasada else self.lista, "page": 1}
            )
        )
        self.uma = api.get(path__regex=r"^/v1/companies/[^/]+$", host="api.nfe.io").mock(
            side_effect=self._uma
        )
        # v2 em PascalCase, como na doc da NFE.io (a conta real manda camelCase).
        self.listar_v2 = api.get(path="/v2/companies", host="api.nfse.io").mock(
            side_effect=lambda req: httpx.Response(
                200, json={"companies": [_pascal(e) for e in self.lista], "hasMore": False}
            )
        )
        self.uma_v2 = api.get(path__regex=r"^/v2/companies/[0-9a-f]+$", host="api.nfse.io").mock(
            side_effect=self._uma_v2
        )
        self.get_ims = api.get(
            path__regex=r"^/v2/companies/[0-9a-f]+/municipaltaxes$", host="api.nfse.io"
        ).mock(side_effect=lambda req: httpx.Response(200, json={"municipalTaxes": self.ims}))
        api.get(path__regex=r"^/v3/companies/[0-9a-f]+/serviceinvoices$", host="api.nfe.io").mock(
            return_value=httpx.Response(200, json={"serviceInvoices": [], "page": 1})
        )
        self.criar = api.post(path="/v2/companies", host="api.nfse.io").mock(
            side_effect=self._criar
        )
        self.certificado = api.post(
            path__regex=r"^/v2/companies/[0-9a-f]+/certificates$", host="api.nfse.io"
        ).mock(side_effect=self._certificado_ok)
        self.inscricao = api.post(
            path__regex=r"^/v2/companies/[0-9a-f]+/municipaltaxes$", host="api.nfse.io"
        ).mock(side_effect=self._inscricao)

    def empresa(self) -> dict:
        e = _empresa_nfeio()
        if self.cert_ativo:
            e["certificate"] = {"status": "Active", "expiresOn": "2027-11-30T00:00:00"}
        return e

    def _uma(self, req: httpx.Request) -> httpx.Response:
        if self.v1_atrasada or (not self.lista and self.criada is None):
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json={"companies": self.empresa()})

    def _uma_v2(self, req: httpx.Request) -> httpx.Response:
        if not self.lista and self.criada is None:
            return httpx.Response(404, json={"errors": [{"code": 40401, "message": "not found"}]})
        return httpx.Response(200, json={"Company": _pascal(self.empresa())})

    def _criar(self, req: httpx.Request) -> httpx.Response:
        self.criada = json.loads(req.content)
        self.lista = [_empresa_nfeio()]
        return httpx.Response(200, json={"company": {"id": NID, "name": "EMPRESA TESTE LTDA"}})

    def _certificado_ok(self, req: httpx.Request) -> httpx.Response:
        self.cert_ativo = True
        return httpx.Response(200, json={"certificate": {"status": "Active"}})

    def _inscricao(self, req: httpx.Request) -> httpx.Response:
        self.ims = [IM]
        return httpx.Response(200, json={"municipalTax": {**IM, "id": "im-nova"}})

    def posts(self) -> int:
        return self.criar.call_count + self.certificado.call_count + self.inscricao.call_count


def _corpo(cert_id=None, **kw) -> dict:
    corpo = {
        "razao_social": "EMPRESA TESTE LTDA",
        "nome_fantasia": "TESTE",
        "regime": "SimplesNacional",
        "natureza_juridica": "SociedadeEmpresariaLimitada",
        "endereco": {
            "logradouro": "RUA EXEMPLO",
            "numero": "100",
            "complemento": "SALA 1",
            "bairro": "CENTRO",
            "cep": "01001-000",
            "cmun_ibge": "3550308",
        },
        "inscricao_municipal": "1.234.567-8",
        "email": "contato@empresa.com.br",
        "certificado_id": str(cert_id) if cert_id else None,
    }
    corpo.update(kw)
    return corpo


def _url(c: Company) -> str:
    return f"/api/nfse/prestadores/{c.id}/nfeio/integrar"


# --- funções puras --------------------------------------------------------------------


def test_corpo_da_empresa_e_da_inscricao():
    dados = receita.mapear(CNPJ_PREST, RECEITA)
    assert dados["endereco"]["logradouro"] == "RUA EXEMPLO"
    assert (
        receita.mapear(CNPJ_PREST, {**RECEITA, "logradouro": "RUA EXEMPLO"})["endereco"][
            "logradouro"
        ]
        == "RUA EXEMPLO"
    )  # nunca "RUA RUA EXEMPLO"
    assert receita.mapear(CNPJ_PREST, {**RECEITA, "numero": ""})["endereco"]["numero"] == "S/N"
    assert dados["regime_tributario_receita"] == "SIMPLES NACIONAL"  # o ano mais recente
    assert dados["regime_tributario_ano"] == 2025
    assert dados["natureza_juridica_codigo"] == "2062"
    assert dados["email"] == "contato@empresa.com.br"
    assert dados["situacao_cadastral"] == "ATIVA"
    assert integracao.NATUREZA_POR_CODIGO["2062"] == "SociedadeEmpresariaLimitada"

    c = Company(razao_social="EMPRESA TESTE LTDA", apelido="teste", cnpj=CNPJ_PREST)
    sug, motivos = integracao.sugestao(c, None, dados)
    assert sug["regime"] == "SimplesNacional" and sug["natureza_juridica"] == (
        "SociedadeEmpresariaLimitada"
    )
    assert motivos["natureza_texto"] == "2062 · Sociedade Empresária Limitada"
    lp = receita.mapear(CNPJ_PREST, {**RECEITA, "opcao_pelo_simples": False})
    lp["regime_tributario_receita"], lp["regime_tributario_ano"] = "LUCRO PRESUMIDO", 2025
    assert integracao.sugestao(c, None, lp)[0]["regime"] == "LucroPresumido"

    d = IntegrarIn(**_corpo())
    assert integracao.corpo_empresa(c, d) == {
        "company": {
            "name": "EMPRESA TESTE LTDA",
            "tradeName": "TESTE",
            "federalTaxNumber": 11222333000181,
            "taxRegime": "SimplesNacional",
            "address": {
                "state": "SP",
                "city": {"code": "3550308", "name": "São Paulo"},
                "district": "CENTRO",
                "street": "RUA EXEMPLO",
                "number": "100",
                "additionalInformation": "SALA 1",
                "postalCode": "01001000",
                "country": "BRA",
            },
        }
    }
    assert "accountId" not in json.dumps(integracao.corpo_empresa(c, d))
    cidade = {"code": "3550308", "name": "São Paulo", "state": "SP"}
    im = integracao.corpo_inscricao(d, cidade, "SimplesNacional")["municipalTax"]
    assert im == {
        "city": {"code": "3550308", "name": "São Paulo", "state": "SP", "country": "BRA"},
        "taxNumber": "12345678",
        "environment": "Development",
        "specialTaxRegime": "MicroempresarioEmpresaPequenoPorte",
        "legalNature": "SociedadeEmpresariaLimitada",
        "email": "contato@empresa.com.br",
        "rpsSerialNumber": "IO",
        "rpsNumber": 1,
        "lastRpsSent": 0,
    }
    assert "loginPassword" not in im and "regionalTaxNumber" not in im
    lucro = integracao.corpo_inscricao(d, cidade, "LucroPresumido")["municipalTax"]
    assert lucro["specialTaxRegime"] == "Nenhum"
    assert len(integracao.LEGAL_NATURE_NFEIO) == 62


def test_certificado_ler():
    validade = date(2027, 3, 7)
    pfx = _pfx(CNPJ_PREST, validade, SENHA_CERT)
    info = certificado.ler(pfx, SENHA_CERT)
    assert info is not None and info.validade == validade and info.cnpj == CNPJ_PREST
    assert certificado.ler(pfx, "senha-errada") is None
    assert certificado.ler(b"pfx", SENHA_CERT) is None
    assert integracao.nome_do_arquivo("empresa teste (1).PFX") == "empresa_teste__1_.PFX"
    assert integracao.nome_do_arquivo("certificado") == "certificado.pfx"


def test_servidor_travado_unitario(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "env", "development")
    monkeypatch.setattr(s, "nfse_integrar_liberado", True)
    assert ambiente.integrar_liberado() is False
    monkeypatch.setattr(s, "env", "production")
    assert ambiente.integrar_liberado() is True
    monkeypatch.setattr(s, "nfse_integrar_liberado", False)
    assert ambiente.integrar_liberado() is False


# --- prévia ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_previa_mostra_o_que_vai_e_nao_escreve_nada(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    await _cert(db, c.id, _pfx(CNPJ_PREST, date(2020, 1, 1), SENHA_CERT), filename="velho.pfx")
    await _cert(db, c.id, senha=None, expires_at=_valido(), filename="sem-senha.pfx")
    bom = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT), filename="bom.pfx")
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        r = await client.get(_url(c))
        assert n.posts() == 0
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["modo"] == "criar" and p["liberado"] is True and p["receita_ok"] is True
    assert p["bloqueios"] == []
    assert p["certificado"]["id"] == str(bom.id)
    assert p["certificado"]["validade_conferida"] is True
    assert p["certificado"]["cnpj_confere"] is True
    assert p["dados"]["regime"] == "SimplesNacional"
    assert p["dados"]["natureza_juridica"] == "SociedadeEmpresariaLimitada"
    assert p["dados"]["endereco"]["logradouro"] == "RUA EXEMPLO"
    assert p["dados"]["endereco"]["cep"] == "01001000"
    assert p["dados"]["endereco"]["cmun_ibge"] == "3550308"
    assert [x["situacao"] for x in p["passos"]] == ["fazer"] * 4
    assert "bom.pfx" in p["passos"][1]["titulo"]
    assert "São Paulo/SP" in p["passos"][2]["titulo"]
    assert any("TESTE" in a for a in p["avisos"])
    assert SENHA_CERT not in r.text


@pytest.mark.asyncio
async def test_prestadores_em_operacao_e_integracao(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, make_user
):
    dono = await make_user(role=UserRole.ADMIN)
    com_loja = await _empresa(db)
    so_arquivada = Company(razao_social="ARQ LTDA", apelido="arq", cnpj=CNPJ_OUTRO)
    vencido = Company(razao_social="VENC LTDA", apelido="venc", cnpj="11444777000242")
    sem_senha = Company(razao_social="SS LTDA", apelido="ss", cnpj="22333444000155")
    db.add_all([so_arquivada, vencido, sem_senha])
    await db.flush()
    db.add_all(
        [
            StoreInfo(user_id=dono.id, platform="ml", account_name="a", cnpj="11.222.333/0001-81"),
            StoreInfo(
                user_id=dono.id,
                platform="ml",
                account_name="b",
                cnpj=CNPJ_OUTRO,
                archived_at=datetime.now(UTC),
            ),
        ]
    )
    await db.commit()
    await _cert(db, com_loja.id, expires_at=_valido())
    await _cert(db, vencido.id, expires_at=date(2020, 1, 1))
    await _cert(db, sem_senha.id, senha=None, expires_at=_valido())
    auth_as(operador)
    r = await client.get("/api/nfse/prestadores")
    assert r.status_code == 200, r.text
    por = {x["apelido"]: x for x in r.json()}
    assert por["teste"]["em_operacao"] is True
    assert por["arq"]["em_operacao"] is False
    assert por["teste"]["integracao"] == "nao_integrada"
    assert por["teste"]["certificado_guardado"] == {"situacao": "ok", "expira": str(_valido())}
    assert por["venc"]["certificado_guardado"]["situacao"] == "vencido"
    assert por["ss"]["certificado_guardado"]["situacao"] == "sem_senha"
    assert por["arq"]["certificado_guardado"] == {"situacao": "nenhum", "expira": None}
    assert "filename" not in r.text and SENHA_CERT not in r.text
    st = (await client.get("/api/nfse/status")).json()
    assert st["integrar_liberado"] is False


# --- integrar ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cnpj_ja_na_nfeio_so_liga_sem_criar(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api, lista=[_empresa_nfeio()], ims=[IM], cert_ativo=True)
        n.lista = [n.empresa()]
        r = await client.post(_url(c), json={})
        assert n.posts() == 0
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True, d
    assert [x["situacao"] for x in d["passos"]] == ["pular", "pular", "pular", "feito"]
    assert d["prestador"]["integracao"] == "ok"
    assert d["mensagem"] == "Integração da teste completa (Teste)."
    f = await db.get(CompanyFiscal, c.id)
    await db.refresh(f)
    assert f.nfeio_company_id == NID


@pytest.mark.asyncio
async def test_cria_manda_certificado_cadastra_inscricao_e_liga(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        previa = (await client.get(_url(c))).json()
        corpo = {
            **previa["dados"],
            "inscricao_municipal": "12345678",
            "certificado_id": previa["certificado"]["id"],
        }
        r = await client.post(_url(c), json=corpo)
        assert r.status_code == 200, r.text
        assert n.criar.call_count == 1
        assert n.certificado.call_count == 1 and n.inscricao.call_count == 1
        enviado = json.loads(n.criar.calls.last.request.content)
        multipart = n.certificado.calls.last.request.content
        im_enviada = json.loads(n.inscricao.calls.last.request.content)
    d = r.json()
    assert d["ok"] is True, d
    assert [x["situacao"] for x in d["passos"]] == ["feito"] * 4
    assert "em TESTE" in d["mensagem"]
    pr = d["prestador"]
    assert pr["integracao"] == "ok" and pr["nfeio"]["ambiente"] == "Development"
    assert svc_empresas.PEND_SEM_CERTIFICADO not in pr["pendencias"]
    assert svc_empresas.PEND_SEM_INSCRICAO not in pr["pendencias"]
    assert enviado == integracao.corpo_empresa(c, IntegrarIn(**corpo))
    assert b'name="File"; filename="empresa_teste.pfx"' in multipart
    assert b'name="Password"' in multipart
    assert SENHA_CERT.encode() in multipart  # foi para a NFE.io (e só para ela)
    assert im_enviada["municipalTax"]["environment"] == "Development"
    assert im_enviada["municipalTax"]["taxNumber"] == "12345678"
    ops = {
        x.operacao: x.http_status
        for x in (await db.execute(select(NfseChamada))).scalars()
        if x.company_id == c.id
    }
    assert ops == {"criar_empresa": 200, "enviar_certificado": 200, "criar_inscricao": 200}
    assert cert.id  # o certificado guardado continua lá


@pytest.mark.asyncio
async def test_certificado_recusado_fica_ligada_com_pendencia_e_repetir_nao_duplica(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        n.certificado.mock(
            side_effect=lambda req: httpx.Response(
                400,
                json={"errors": [{"code": 40001, "message": "Certificate password is invalid"}]},
            )
        )
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is False
        passo = {x["id"]: x for x in d["passos"]}
        assert passo["certificado"]["situacao"] == "falhou"
        assert "recusou a senha do certificado" in passo["certificado"]["detalhe"]
        assert passo["inscricao"]["situacao"] == "feito"
        assert "ficou ligada na NFE.io" in d["mensagem"]
        pr = d["prestador"]
        assert pr["nfeio"] is not None and pr["integracao"] == "incompleta"
        assert svc_empresas.PEND_SEM_CERTIFICADO in pr["pendencias"]

        n.certificado.mock(side_effect=n._certificado_ok)
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True, d
        assert [x["situacao"] for x in d["passos"]] == ["pular", "feito", "pular", "feito"]
        assert d["prestador"]["integracao"] == "ok"
        assert n.criar.call_count == 1
        assert n.inscricao.call_count == 1


@pytest.mark.asyncio
async def test_criacao_sem_resposta_nao_repete_e_a_proxima_acha_pelo_cnpj(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        n.criar.mock(side_effect=httpx.ReadTimeout("sem resposta"))
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 502, r.text
        det = r.json()["detail"]
        assert det["code"] == "nfeio_incerta"
        assert {x["id"]: x["situacao"] for x in det["passos"]}["criar_empresa"] == "falhou"
        assert n.criar.call_count == 1 and n.certificado.call_count == 0
        f = await db.get(CompanyFiscal, c.id)
        assert f is None or f.nfeio_company_id is None

        # Ela tinha sido criada: na próxima a listagem acha pelo CNPJ.
        n.lista = [_empresa_nfeio()]
        n.ims = [IM]
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True, d
        assert [x["situacao"] for x in d["passos"]] == ["pular", "feito", "pular", "feito"]
        assert n.criar.call_count == 1 and n.certificado.call_count == 1
    f = await db.get(CompanyFiscal, c.id)
    await db.refresh(f)
    assert f.nfeio_company_id == NID


@pytest.mark.asyncio
async def test_ja_integrada_e_integracao_em_andamento(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    auth_as: Callable,
    liberado,
    redis_falso: RedisFalso,
):
    ligada = await _empresa(db)
    db.add(
        CompanyFiscal(
            company_id=ligada.id,
            nfeio_company_id=NID,
            nfeio_ambiente="Production",
            nfeio_cert_status="Active",
            nfeio_cert_expira=_valido(),
            nfeio_resumo={"inscricoes": [{"taxNumber": "12345678"}]},
        )
    )
    outra = Company(razao_social="OUTRA LTDA", apelido="outra", cnpj=CNPJ_OUTRO)
    db.add(outra)
    await db.commit()
    auth_as(operador)
    redis_falso.d[f"davinci:nfse:integrar:{outra.id}"] = "1"
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        r = await client.post(_url(ligada), json={})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_integrada"
        r = await client.post(_url(outra), json={})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "integracao_em_andamento"
        assert api.calls.call_count == 0
    assert f"davinci:nfse:integrar:{ligada.id}" not in redis_falso.d  # a trava foi solta


@pytest.mark.asyncio
async def test_servidor_travado_nao_manda_nada(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        NfeioFalsa(api)
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "integrar_travado"
        assert api.calls.call_count == 0  # nem a listagem
        r = await client.get(_url(c))
    assert r.status_code == 200, r.text
    assert r.json()["liberado"] is False


@pytest.mark.asyncio
async def test_bloqueios_antes_de_qualquer_post(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    auth_as(operador)
    uranyx = Company(razao_social="URANYX", apelido="uranyx", cnpj="123456789")
    db.add(uranyx)
    await db.commit()
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        r = await client.get(_url(uranyx))
        assert r.status_code == 200, r.text
        assert "CNPJ brasileiro válido" in r.json()["bloqueios"][0]
        assert n.brasilapi.call_count == 0 and n.listar.call_count == 0
        r = await client.post(_url(uranyx), json=_corpo())
        assert r.status_code == 422 and r.json()["detail"]["code"] == "cnpj_invalido"

        c = await _empresa(db)
        r = await client.post(_url(c), json=_corpo())
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "sem_certificado_guardado"

        velho = await _cert(db, c.id, _pfx(CNPJ_PREST, date(2020, 1, 1), SENHA_CERT))
        r = await client.post(_url(c), json=_corpo(velho.id))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "certificado_vencido"
        assert "01/01/2020" in r.json()["detail"]["mensagem"]

        outro = await _cert(db, c.id, _pfx(CNPJ_OUTRO, _valido(), SENHA_CERT))
        r = await client.post(_url(c), json=_corpo(outro.id))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "certificado_outro_cnpj"

        bom = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
        for im in ("", "1234567"):
            r = await client.post(_url(c), json=_corpo(bom.id, inscricao_municipal=im))
            assert r.status_code == 422, r.text
            det = r.json()["detail"]
            assert det["code"] == "dados_incompletos" and "inscricao_municipal" in det["campos"]
        r = await client.post(
            _url(c),
            json=_corpo(bom.id, endereco={"logradouro": "R", "cep": "123"}, regime=None),
        )
        campos = r.json()["detail"]["campos"]
        assert {"regime", "endereco.logradouro", "endereco.cep", "endereco.bairro"} <= set(campos)
        assert n.posts() == 0


@pytest.mark.asyncio
async def test_sem_permissao_nao_ve_nem_integra(
    client: AsyncClient, db: AsyncSession, make_user, operador: User, auth_as: Callable, liberado
):
    from app.main import app
    from app.security.senha_extra import require_nfse_unlock

    c = await _empresa(db)
    auth_as(await make_user(permissions={"emissao_servico": {"view": True}}))
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        r = await client.get(_url(c))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
        r = await client.post(_url(c), json={})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
        auth_as(operador)
        app.dependency_overrides.pop(require_nfse_unlock, None)
        for metodo in ("GET", "POST"):
            r = await client.request(metodo, _url(c), json={})
            assert r.status_code == 401 and r.json()["detail"]["code"] == "nfse_locked"
        assert api.calls.call_count == 0


async def _com_sessao_de_verdade(client: AsyncClient, make_user) -> User:
    from app.main import app
    from app.security.jwt import issue_session_token
    from app.security.senha_extra import require_nfse_unlock

    eu = await make_user(role=UserRole.ADMIN)
    token, _, _ = issue_session_token(sub=eu.open_id, role=eu.role.value)
    client.cookies.set(get_settings().cookie_name, token)

    async def _liberado() -> None:
        return None

    app.dependency_overrides[require_nfse_unlock] = _liberado
    return eu


@pytest.mark.asyncio
async def test_senha_do_certificado_nunca_vaza(
    client: AsyncClient, db: AsyncSession, make_user, liberado
):
    from app.main import app
    from app.models import HistoricoAlteracao, HistoricoEvento
    from app.security.senha_extra import require_nfse_unlock

    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    await _com_sessao_de_verdade(client, make_user)
    try:
        with (
            structlog.testing.capture_logs() as logs,
            respx.mock(assert_all_mocked=True, assert_all_called=False) as api,
        ):
            n = NfeioFalsa(api)
            # Erro que ECOA a senha no texto: tem de sair ***.
            n.certificado.mock(
                side_effect=lambda req: httpx.Response(
                    400,
                    json={
                        "errors": [
                            {"code": 40099, "message": f"Bad certificate secret {SENHA_CERT}"}
                        ]
                    },
                )
            )
            previa = await client.get(_url(c))
            r = await client.post(_url(c), json=_corpo(cert.id))
    finally:
        app.dependency_overrides.pop(require_nfse_unlock, None)
    assert previa.status_code == 200 and r.status_code == 200, r.text
    detalhe = {x["id"]: x for x in r.json()["passos"]}["certificado"]["detalhe"]
    assert "***" in detalhe
    for texto in (previa.text, r.text, str(logs)):
        assert SENHA_CERT not in texto
    chamadas = [
        {k: getattr(x, k) for k in ("operacao", "http_status", "codigos", "erro")}
        for x in (await db.execute(select(NfseChamada))).scalars()
    ]
    assert chamadas and SENHA_CERT not in json.dumps(chamadas, default=str)
    eventos = (
        (await db.execute(select(HistoricoEvento).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    rota = "/api/nfse/prestadores/{company_id}/nfeio/integrar"
    assert any(ev.rota == rota for ev in eventos)
    for ev in eventos:
        linha = {k: getattr(ev, k) for k in ev.__mapper__.c.keys()}
        assert SENHA_CERT not in json.dumps(linha, default=str)
    alteracoes = (await db.execute(select(HistoricoAlteracao))).scalars().all()
    for a in alteracoes:
        linha = {k: getattr(a, k) for k in a.__mapper__.c.keys()}
        assert SENHA_CERT not in json.dumps(linha, default=str)
    f = await db.get(CompanyFiscal, c.id)
    await db.refresh(f)
    assert SENHA_CERT not in json.dumps(f.nfeio_resumo, default=str)


@pytest.mark.asyncio
async def test_integrar_fica_no_historico(
    client: AsyncClient, db: AsyncSession, make_user, liberado
):
    from app.historico.nomes import ACOES
    from app.main import app
    from app.models import HistoricoEvento
    from app.security.senha_extra import require_nfse_unlock

    c = await _empresa(db)
    await _com_sessao_de_verdade(client, make_user)
    try:
        with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
            n = NfeioFalsa(api, ims=[IM], cert_ativo=True)
            n.lista = [n.empresa()]
            r = await client.post(_url(c), json={})
    finally:
        app.dependency_overrides.pop(require_nfse_unlock, None)
    assert r.status_code == 200, r.text
    rota = "/api/nfse/prestadores/{company_id}/nfeio/integrar"
    q = select(HistoricoEvento).where(HistoricoEvento.rota == rota)
    [ev] = (await db.execute(q.execution_options(populate_existing=True))).scalars().all()
    assert ev.acao == ACOES[("POST", rota)]
    assert ev.tela == "Cadastros › Emissão de Serviço"


# --- revisão de 01/10/2026 --------------------------------------------------------------


def _ligada_incompleta(company_id, **kw) -> CompanyFiscal:
    """Ligada a NID, com IM, mas sem certificado na NFE.io ("incompleta")."""
    dados = {
        "company_id": company_id,
        "nfeio_company_id": NID,
        "nfeio_ambiente": "Development",
        "nfeio_resumo": {"inscricoes": [{"taxNumber": "12345678"}]},
    }
    dados.update(kw)
    return CompanyFiscal(**dados)


@pytest.mark.asyncio
async def test_ligada_que_sumiu_da_nfeio_nunca_e_criada_de_novo(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    """Ligada ("incompleta") + id 404 na v1 E na v2 + CNPJ fora das listas: para
    com 409 e nenhum POST (antes caía em "criar" e gravava outro id por cima)."""
    c = await _empresa(db)
    db.add(_ligada_incompleta(c.id))
    await db.commit()
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "nfeio_ligada_sumiu"
        assert n.uma.call_count >= 1 and n.uma_v2.call_count >= 1
        assert n.listar_v2.call_count >= 1
        p = await client.get(_url(c))
        assert p.status_code == 200, p.text
        assert any("não foi achada na NFE.io" in b for b in p.json()["bloqueios"])
        assert n.posts() == 0
    f = await db.get(CompanyFiscal, c.id)
    await db.refresh(f)
    assert f.nfeio_company_id == NID  # o id ligado não foi trocado


@pytest.mark.asyncio
async def test_ligada_que_so_a_v2_mostra_completa_sem_criar(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    """A v1 ainda não mostra a empresa criada pela v2 (sem IM): a v2 acha pelo id
    e o "Completar integração" só manda o que falta."""
    c = await _empresa(db)
    db.add(_ligada_incompleta(c.id))
    await db.commit()
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api, lista=[_empresa_nfeio()], ims=[IM])
        n.v1_atrasada = True
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 200, r.text
        d = r.json()
        assert n.criar.call_count == 0 and n.certificado.call_count == 1
    assert [x["situacao"] for x in d["passos"]] == ["pular", "feito", "pular", "feito"], d
    assert d["ok"] is True and d["prestador"]["integracao"] == "ok"


@pytest.mark.asyncio
async def test_criacao_com_id_em_pascalcase_conta_como_criada(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)

        def _criar_pascal(req):
            n._criar(req)
            return httpx.Response(200, json={"Company": {"Id": NID.upper(), "Name": "X"}})

        n.criar.mock(side_effect=_criar_pascal)
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 200, r.text
    d = r.json()
    passo = {x["id"]: x for x in d["passos"]}["criar_empresa"]
    assert passo["situacao"] == "feito" and passo["detalhe"] is None
    assert n.criar.call_count == 1 and d["ok"] is True


@pytest.mark.asyncio
async def test_falha_de_leitura_da_im_nao_vira_pendencia(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable
):
    """IM 503 (mesmo depois das repetições) numa ligada não é "sem inscrição":
    fica a lista da leitura anterior e a empresa continua emitindo."""
    c = await _empresa(db)
    db.add(
        _ligada_incompleta(
            c.id,
            nfeio_cert_status="Active",
            nfeio_cert_expira=_valido(),
            nfeio_resumo={"inscricoes": [{"taxNumber": "12345678", "environment": "Production"}]},
        )
    )
    sem_resumo = Company(razao_social="OUTRA LTDA", apelido="outra", cnpj=CNPJ_OUTRO)
    db.add(sem_resumo)
    await db.flush()
    db.add(CompanyFiscal(company_id=sem_resumo.id, nfeio_company_id="f" * 32))
    await db.commit()
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        api.get(path=f"/v1/companies/{'f' * 32}", host="api.nfe.io").mock(
            return_value=httpx.Response(
                200,
                json={
                    "companies": _empresa_nfeio(
                        id="f" * 32,
                        federalTaxNumber=int(CNPJ_OUTRO),
                        certificate={"status": "Active", "expiresOn": "2027-11-30T00:00:00"},
                    )
                },
            )
        )
        n = NfeioFalsa(api, lista=[_empresa_nfeio()], cert_ativo=True)
        n.get_ims.mock(return_value=httpx.Response(503, json={"message": "fora"}))
        r = await client.post(f"/api/nfse/prestadores/{c.id}/nfeio/atualizar")
        assert r.status_code == 200, r.text
        r2 = await client.post(f"/api/nfse/prestadores/{sem_resumo.id}/nfeio/atualizar")
        assert r2.status_code == 200, r2.text
    pr = r.json()
    assert svc_empresas.PEND_SEM_INSCRICAO not in pr["pendencias"]
    assert pr["integracao"] == "ok"
    assert svc_empresas.PEND_SEM_INSCRICAO not in r2.json()["pendencias"]
    f = await db.get(CompanyFiscal, c.id)
    await db.refresh(f)
    assert f.nfeio_resumo["inscricoes"][0]["taxNumber"] == "12345678"
    assert f.nfeio_resumo["inscricoes_lidas"] is True
    assert f.nfeio_ambiente == "Production"  # o da IM lida antes, não o da empresa
    # Leitura que respondeu e veio vazia continua sendo pendência.
    solta = CompanyFiscal(company_id=uuid.uuid4(), nfeio_resumo=dict(f.nfeio_resumo))
    svc_empresas.aplicar(solta, _empresa_nfeio(), [], None)
    assert solta.nfeio_resumo["inscricoes_lidas"] is True
    assert svc_empresas.sem_inscricao_na_nfeio(solta) is True
    svc_empresas.aplicar(solta, _empresa_nfeio(), None, None)  # falhou de novo: segue vazia
    assert svc_empresas.sem_inscricao_na_nfeio(solta) is True


@pytest.mark.asyncio
async def test_certificado_e_cnpj_de_outra_empresa_e_email_invalido_nao_mandam_nada(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    bom = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    b = Company(razao_social="OUTRA LTDA", apelido="outra", cnpj=CNPJ_OUTRO)
    db.add(b)
    await db.commit()
    dela = await _cert(db, b.id, _pfx(CNPJ_OUTRO, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        # (a) certificado guardado de OUTRA empresa
        r = await client.post(_url(c), json=_corpo(dela.id))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "certificado_invalido"
        # (b) e-mail digitado inválido
        r = await client.post(_url(c), json=_corpo(bom.id, email="nao e email"))
        assert r.status_code == 422, r.text
        det = r.json()["detail"]
        assert det["code"] == "dados_incompletos" and "email" in det["campos"]
        # (c) razão social / nome fantasia longos: PT-BR, no campo (não 422 do pydantic)
        r = await client.post(
            _url(c), json=_corpo(bom.id, razao_social="X" * 61, nome_fantasia="Y" * 61)
        )
        assert r.status_code == 422, r.text
        det = r.json()["detail"]
        assert {"razao_social", "nome_fantasia"} <= set(det["campos"])
        assert "60 letras" in det["campos"]["nome_fantasia"]
        assert n.posts() == 0

    # (d) ligada a uma empresa da NFE.io de OUTRO CNPJ
    db.add(_ligada_incompleta(c.id))
    await db.commit()
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api, lista=[_empresa_nfeio(federalTaxNumber=int(CNPJ_OUTRO))])
        n.empresa = lambda: _empresa_nfeio(federalTaxNumber=int(CNPJ_OUTRO))
        r = await client.post(_url(c), json=_corpo(bom.id))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "cnpj_diferente"
        p = await client.get(_url(c))
        assert p.status_code == 200, p.text
        assert any("não é teste" in x for x in p.json()["bloqueios"])
        assert n.posts() == 0


@pytest.mark.asyncio
async def test_completar_ignora_nome_longo_da_receita(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    """No "completar" a empresa não é criada: razão social/nome fantasia nem são
    usados, então um nome da Receita com mais de 60 letras não trava."""
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api, lista=[_empresa_nfeio()], ims=[IM])
        r = await client.post(
            _url(c),
            json=_corpo(cert.id, razao_social="X" * 90, nome_fantasia="Y" * 90, endereco={}),
        )
        assert r.status_code == 200, r.text
        assert n.criar.call_count == 0
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_trava_tem_dono_e_renova(redis_falso: RedisFalso):
    cid = uuid.uuid4()
    chave = f"davinci:nfse:integrar:{cid}"
    async with integracao.trava(cid) as t:
        assert await t.renovar() is True
        redis_falso.d[chave] = "outro-pedido"  # venceu e outro clique pegou
        assert await t.renovar() is False
    assert redis_falso.d[chave] == "outro-pedido"  # não apagou a trava do outro
    del redis_falso.d[chave]
    async with integracao.trava(cid):
        with pytest.raises(integracao.NfseError) as e:
            async with integracao.trava(cid):
                pass
        assert e.value.status == 409
    assert chave not in redis_falso.d
    assert integracao.TRAVA_SEGUNDOS >= 900


@pytest.mark.asyncio
async def test_trava_vencida_antes_do_post_nao_cria(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    auth_as: Callable,
    liberado,
    monkeypatch,
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)

    async def _perdeu(self) -> bool:
        return False

    monkeypatch.setattr(integracao.Trava, "renovar", _perdeu)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "integracao_sem_trava"
        assert n.posts() == 0


# --- revisão final (01/10/2026) --------------------------------------------------------


@pytest.mark.asyncio
async def test_im_recusada_e_releitura_sem_ims_deixa_completar(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    """1ª ligação: o POST da IM é recusado e, na releitura, a leitura das IMs não
    responde. A linha não pode ficar "ok" (sem o Completar) nem dar 409."""
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        n.inscricao.mock(
            return_value=httpx.Response(
                400, json={"errors": [{"code": 40000, "message": "invalid tax number"}]}
            )
        )
        n.get_ims.mock(return_value=httpx.Response(503, json={"message": "fora"}))
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is False
        assert d["prestador"]["integracao"] == "incompleta"
        assert svc_empresas.PEND_SEM_INSCRICAO not in d["prestador"]["pendencias"]
        assert svc_empresas.AVISO_IM_NAO_LIDA in d["prestador"]["avisos"]
        # NFE.io ainda fora: a prévia não dá 409 e não oferece "criar".
        p = await client.get(_url(c))
        assert p.status_code == 200, p.text
        pv = p.json()
        assert pv["modo"] == "completar" and pv["passos"] == [] and pv["bloqueios"]
        # Voltou: completa só a IM, sem criar de novo.
        n.get_ims.mock(side_effect=lambda req: httpx.Response(200, json={"municipalTaxes": n.ims}))
        n.inscricao.mock(side_effect=n._inscricao)
        p = await client.get(_url(c))
        assert p.status_code == 200, p.text
        assert p.json()["modo"] == "completar"
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True, d
        assert n.criar.call_count == 1 and n.inscricao.call_count == 2
    assert d["prestador"]["integracao"] == "ok"


@pytest.mark.asyncio
async def test_releitura_que_falha_tem_mensagem_propria(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        n.uma.mock(return_value=httpx.Response(503, json={"message": "fora"}))
        n.uma_v2.mock(return_value=httpx.Response(503, json={"message": "fora"}))
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is False
    assert {x["id"]: x["situacao"] for x in d["passos"]}["ligar"] == "falhou"
    assert d["mensagem"].count("ficou ligada") == 1
    assert "Atualizar" in d["mensagem"] and "Corrija" not in d["mensagem"]


@pytest.mark.asyncio
async def test_receita_baixada_ou_sem_cnpj_bloqueia_tambem_no_post(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable, liberado
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        n = NfeioFalsa(api)
        n.brasilapi.mock(
            return_value=httpx.Response(
                200, json={**RECEITA, "descricao_situacao_cadastral": "BAIXADA"}
            )
        )
        p = await client.get(_url(c))
        assert any("BAIXADA" in b for b in p.json()["bloqueios"])
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "cnpj_baixado"

        n.brasilapi.mock(return_value=httpx.Response(404, json={"message": "not found"}))
        p = await client.get(_url(c))
        pv = p.json()
        assert any("não achou este CNPJ" in b for b in pv["bloqueios"])
        assert integracao.MSG_RECEITA_FORA not in pv["avisos"]
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "cnpj_nao_encontrado"

        # Receita fora do ar não bloqueia (D14).
        n.brasilapi.mock(return_value=httpx.Response(502))
        p = await client.get(_url(c))
        assert p.json()["bloqueios"] == [] and integracao.MSG_RECEITA_FORA in p.json()["avisos"]
        assert n.posts() == 0


def test_certificado_com_data_vazia_do_dotnet_conta_como_sem_certificado():
    vazio = {"status": "None", "expiresOn": "0001-01-01T00:00:00"}
    f = CompanyFiscal(company_id=uuid.uuid4())
    svc_empresas.aplicar(f, _empresa_nfeio(certificate=vazio), [IM], None)
    assert f.nfeio_cert_expira is None
    assert svc_empresas.sem_certificado_na_nfeio(f) is True
    assert svc_empresas.situacao_integracao(f) == "incompleta"
    assert integracao._tem_certificado(_empresa_nfeio(certificate=vazio)) is False
    assert integracao._tem_certificado(
        _empresa_nfeio(certificate={"status": "Active", "expiresOn": "2027-11-30T00:00:00"})
    )


@pytest.mark.asyncio
async def test_redis_fora_nao_manda_nada(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    auth_as: Callable,
    liberado,
    monkeypatch,
):
    c = await _empresa(db)
    cert = await _cert(db, c.id, _pfx(CNPJ_PREST, _valido(), SENHA_CERT))
    auth_as(operador)

    async def _fora():
        raise ConnectionError("redis fora")

    monkeypatch.setattr(integracao, "_redis", _fora)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        NfeioFalsa(api)
        r = await client.post(_url(c), json=_corpo(cert.id))
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "integracao_sem_trava"
        assert api.calls.call_count == 0
