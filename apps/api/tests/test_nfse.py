"""NFS-e pela NFE.io — aba NF Faturador › Emissão de Serviço (29–30/09/2026).

Eduardo: "não vamos precisar mais fazer, só deixar o lugar dele onde será
emitido isso automaticamente… com regras". O motor é a API da NFE.io (aqui,
falsa via respx — nenhuma chamada sai para a NFE.io de verdade). O que estes
testes seguram:
- nota de verdade não sai de fora da produção liberada (nem um POST);
- o mesmo pedido de nota nunca vira duas notas (externalId, adoção, "incerta");
- a tabela de estados da NFE.io (emitida, recusada, "download stage", cancelamento);
- a regra de IR medida nas 137 notas reais (Lucro Presumido + PJ, 1,5%, dispensa ≤ R$ 10);
- o webhook: assinatura, repetição, nota desconhecida — e o corpo nunca decide;
- as regras nossas que ficaram (percentual, nota fixa, mês, Receita, municípios).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Company, User, UserRole
from app.models.nfse import CompanyFiscal, NfseChamada, NfseEmissao
from app.schemas.nfse import ItemIn
from app.services.nfse import emissao as svc
from app.services.nfse import empresas as svc_empresas
from app.services.nfse import municipios as svc_municipios
from app.services.nfse import nfeio
from app.services.nfse import texto as T  # noqa: N812

API = "https://api.nfe.io"
NFSE2 = "https://api.nfse.io"
CID = "68ace6448851481198e746d5"
SI = f"{API}/v3/companies/{CID}/serviceinvoices"
CNPJ_PREST = "11222333000181"
CNPJ_TOMA = "11444777000161"
NOTA_ID = "6abc8025e8d48e00016778b3"
BRASILAPI = "https://brasilapi.com.br/api/cnpj/v1"
SEGREDO = "segredo-do-webhook-de-teste-com-32+caracteres"


@pytest.fixture(autouse=True)
def _nfeio_de_teste(monkeypatch):
    """Chave falsa, ambiente local e sem produção liberada — como no localhost."""
    s = get_settings()
    monkeypatch.setattr(s, "nfeio_api_key", "chave-de-teste")
    monkeypatch.setattr(s, "nfeio_base_url", API)
    monkeypatch.setattr(s, "nfeio_nfse_base_url", NFSE2)
    monkeypatch.setattr(s, "nfeio_webhook_secret", SEGREDO)
    monkeypatch.setattr(s, "env", "development")
    monkeypatch.setattr(s, "nfse_producao_liberada", False)
    monkeypatch.setattr(nfeio, "ESPERA_GET", 0.0)
    svc.limpar_cache()
    yield
    svc.limpar_cache()


def _nota(flow: str = "Issued", status: str = "Issued", **kw) -> dict:
    n = {
        "id": NOTA_ID,
        "environment": "Development",
        "status": status,
        "flowStatus": flow,
        "flowMessage": None,
        "number": 2 if status == "Issued" else 0,
        "checkCode": "241714855866429",
        "rpsSerialNumber": "IO",
        "rpsNumber": 2,
        "issuedOn": "2026-09-30T00:21:09-03:00",
        "baseTaxAmount": 1000.0,
        "issRate": 0.02,
        "issTaxAmount": 0,
        "amountNet": 1000.0,
        "borrower": {"federalTaxNumber": int(CNPJ_TOMA)},
    }
    n.update(kw)
    return n


# --- funções puras --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "regime", "retencao", "retem", "ir"),
    [
        ("3252.35", "LucroPresumido", "auto", True, "48.79"),  # KFA 28/07
        ("670.00", "LucroPresumido", "auto", True, "10.05"),
        ("669.53", "LucroPresumido", "auto", True, "10.04"),  # Vita
        ("665.03", "LucroPresumido", "auto", False, None),  # JLAS: IR 9,98 dispensado
        ("666.67", "LucroPresumido", "auto", False, None),  # IR 10,00 exatos: dispensa
        ("3252.35", "SimplesNacional", "auto", False, None),
        ("3252.35", None, "auto", False, None),
        ("3252.35", "SimplesNacional", "sempre", True, "48.79"),
        ("3252.35", "LucroPresumido", "nunca", False, None),
    ],
)
def test_regra_do_ir_medida_nas_notas_reais(valor, regime, retencao, retem, ir):
    r = svc.calcular_ir(Decimal(valor), regime, retencao)
    assert r.retem is retem
    assert (str(r.valor) if r.retem else None) == ir
    assert r.motivo


def test_ir_so_para_tomador_pj():
    assert svc.calcular_ir(Decimal("5000"), "LucroPresumido", tomador_pj=False).retem is False


def test_bloco_de_retencoes_igual_ao_das_notas_reais():
    ir = svc.calcular_ir(Decimal("3252.35"), "LucroPresumido")
    txt = svc.descricao_final("Intermediação", Decimal("3252.35"), ir)
    assert txt == (
        "Intermediação\n\nRETENÇÕES CONFORME LEI 10.833/2003\n"
        "   IRRF   1,50%  R$ 48,79\n"
        "   PIS    0,00%  R$ 0,00\n"
        "   COFINS 0,00%  R$ 0,00\n"
        "   CSLL   0,00%  R$ 0,00\n"
        "   ISS    0,00%  R$ 0,00\n"
        "   INSS   0.00%  R$ 0,00\n"
        "VALOR LIQUIDO  R$ 3.203,56"
    )
    # Reenvio não duplica o bloco.
    assert svc.descricao_final(txt, Decimal("3252.35"), ir) == txt
    sem = svc.calcular_ir(Decimal("665.03"), "LucroPresumido")
    assert svc.descricao_final("Intermediação", Decimal("665.03"), sem) == "Intermediação"


def test_payload_sem_ir_e_com_ir():
    borrower = svc.montar_borrower(CNPJ_TOMA, "TOMADOR LTDA")
    base = {
        "borrower": borrower,
        "competencia": date(2026, 9, 1),
        "descricao": "Intermediação",
        "inf_comp": None,
        "codigos": ("6303", "10.05", "102010000"),
        "hoje": date(2026, 10, 5),
    }
    sem = svc.montar_payload(
        valor=Decimal("665.03"),
        ir=svc.calcular_ir(Decimal("665.03"), "LucroPresumido"),
        external="dv-x-1",
        **base,
    )
    assert sem == {
        "externalId": "dv-x-1",
        "borrower": {"type": "LegalEntity", "name": "TOMADOR LTDA", "federalTaxNumber": CNPJ_TOMA},
        "cityServiceCode": "6303",
        "federalServiceCode": "10.05",
        "nbsCode": "102010000",
        "description": "Intermediação",
        "servicesAmount": 665.03,
        "accrualOn": "2026-09-30",  # lote de setembro emitido em outubro
    }
    com = svc.montar_payload(
        valor=Decimal("3252.35"), ir=svc.calcular_ir(Decimal("3252.35"), "LucroPresumido"), **base
    )
    assert com["irAmountWithheld"] == 48.79
    for campo in ("pis", "cofins", "csll", "inss", "iss", "others"):
        assert com[f"{campo}AmountWithheld"] == 0.0
    # Nunca vão: a NFE.io numera e calcula o ISS.
    for proibido in ("rpsNumber", "issRate", "issTaxAmount", "environment"):
        assert proibido not in com and proibido not in sem


def test_external_id_estavel_e_curto():
    eid = uuid.uuid4()
    assert svc.external_id(eid, 1) == svc.external_id(eid, 1)
    assert svc.external_id(eid, 1) != svc.external_id(eid, 2)
    assert len(svc.external_id(eid, 99)) <= 40


def test_competencia_nunca_depois_de_hoje():
    assert T.data_competencia(date(2026, 9, 1), date(2026, 9, 14)) == date(2026, 9, 14)
    assert T.data_competencia(date(2026, 9, 1), date(2026, 10, 3)) == date(2026, 9, 30)


def test_id_da_empresa_aceita_link_e_os_dois_formatos():
    assert nfeio.normalizar_id(CID) == CID
    assert (
        nfeio.normalizar_id("92A6E7C65CC240728535F2644631F851")
        == "92a6e7c65cc240728535f2644631f851"
    )
    assert (
        nfeio.normalizar_id("https://app.nfe.io/companies/92a6e7c65cc240728535f2644631f851/notas")
        == "92a6e7c65cc240728535f2644631f851"
    )
    assert nfeio.normalizar_id("58.395.841/0001-25") is None
    assert nfeio.normalizar_id("") is None


def test_mensagens_de_erro_da_nfeio_em_todos_os_formatos():
    assert nfeio.mensagens({"message": "company is not active"})[0]["descricao"] == (
        "company is not active"
    )
    campo = nfeio.mensagens({"errors": {"$.borrower.name": ["The name field is required."]}})
    assert "required" in campo[0]["descricao"]
    assert nfeio.mensagens("pageCount must be between 1 and 50")[0]["descricao"].startswith(
        "pageCount"
    )


def test_tabela_de_estados_da_nfeio():
    def aplicado(nota: dict, antes: str = "processando") -> NfseEmissao:
        e = NfseEmissao(status=antes, alertas=None, erros=None)
        svc.aplicar_nota(e, nota)
        return e

    ok = aplicado(_nota())
    assert ok.status == "emitida" and ok.n_nfse == "2" and ok.check_code == "241714855866429"
    assert ok.p_aliq_aplic == Decimal("2.00") and ok.rps_numero == 2
    recusada = aplicado(
        _nota("IssueFailed", "Error", flowMessage="[1207] Prestador de Serviços não autorizado")
    )
    assert recusada.status == "rejeitada" and "1207" in json.dumps(recusada.erros)
    # "download stage": a nota EXISTE — emitida com alerta, nunca reemitir.
    so_pdf = aplicado(
        _nota("IssueFailed", "Issued", flowMessage="max retry reached on download stage")
    )
    assert so_pdf.status == "emitida" and so_pdf.alertas
    for flow in ("WaitingCalculateTaxes", "WaitingSend", "WaitingReturn", "PullFromCityHall"):
        assert aplicado(_nota(flow, "Created")).status == "processando"
    assert aplicado(_nota("Cancelled", "Cancelled"), "cancelando").status == "cancelada"
    assert aplicado(_nota("WaitingSendCancel", "Issued"), "emitida").status == "cancelando"
    volta = aplicado(_nota("CancelFailed", "Issued", flowMessage="prazo"), "cancelando")
    assert volta.status == "emitida" and "prazo" in json.dumps(volta.alertas, ensure_ascii=False)
    # Valor que a NFE.io não documentou: decide pelo status, sem quebrar.
    assert aplicado(_nota("AlgoNovo", "Issued")).status == "emitida"
    assert aplicado(_nota("AlgoNovo", "Error")).status == "rejeitada"


def test_pendencias_da_empresa():
    c = Company(razao_social="X LTDA", apelido="x", cnpj=CNPJ_PREST)
    hoje = date(2026, 9, 30)
    assert svc_empresas.pendencias_e_avisos(c, None, hoje=hoje)[0]  # não ligada
    f = CompanyFiscal(
        nfeio_company_id=CID,
        nfeio_ambiente="Production",
        nfeio_status_fiscal="Active",
        nfeio_regime="LucroPresumido",
        nfeio_cert_status="Overdue",
        nfeio_cert_expira=date(2026, 9, 24),
        city_service_code="6303",
        nfeio_resumo={"inscricoes": [{"city": {"code": "3550308"}}]},
    )
    p, _a = svc_empresas.pendencias_e_avisos(c, f, hoje=hoje)
    assert any("venceu" in x for x in p)
    # Em teste o certificado vencido só avisa.
    f.nfeio_ambiente = "Development"
    p, a = svc_empresas.pendencias_e_avisos(c, f, hoje=hoje)
    assert not p and any("venceu" in x for x in a)
    # [1207] na última nota avisa mesmo antiga (Rodrigues/Victor MEI).
    f.nfeio_cert_status, f.nfeio_cert_expira = "Active", date(2027, 1, 1)
    f.nfeio_resumo = {
        "inscricoes": [{"city": {"code": "3550308"}}],
        "ultima_nota": {
            "flowStatus": "IssueFailed",
            "status": "Error",
            "flowMessage": "[1207] Prestador de Serviços não autorizado a emitir NFS-e.",
            "createdOn": "2026-07-28T10:00:00-03:00",
        },
    }
    _p, a = svc_empresas.pendencias_e_avisos(c, f, hoje=hoje)
    assert any("1207" in x for x in a)


# --- rotas (banco + NFE.io falsa) ------------------------------------------------------


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


@pytest_asyncio.fixture
async def cenario(db: AsyncSession) -> dict:
    """Prestador ligado na NFE.io em TESTE (Development) + um tomador do grupo."""
    prest = Company(razao_social="EMPRESA TESTE LTDA", apelido="teste", cnpj=CNPJ_PREST)
    toma = Company(razao_social="TOMADOR LTDA", apelido="tomador", cnpj=CNPJ_TOMA)
    db.add_all([prest, toma])
    await db.flush()
    db.add(
        CompanyFiscal(
            company_id=prest.id,
            nfeio_company_id=CID,
            nfeio_ambiente="Development",
            nfeio_status_fiscal="Active",
            nfeio_regime="SimplesNacional",
            nfeio_cert_status="Active",
            nfeio_cert_expira=date.today() + timedelta(days=300),
            nfeio_municipio="São Paulo",
            nfeio_uf="SP",
            city_service_code="6303",
            federal_service_code="10.05",
        )
    )
    await db.commit()
    return {"prest": prest.id, "toma_company": toma.id}


async def _fiscal(db: AsyncSession, cen: dict, **campos) -> None:
    f = await db.get(CompanyFiscal, cen["prest"])
    for k, v in campos.items():
        setattr(f, k, v)
    await db.commit()


async def _montar(client: AsyncClient, cen: dict, *, valor: str = "1500.00") -> dict:
    r = await client.post(
        "/api/nfse/tomadores", json={"tipo": "grupo", "company_id": str(cen["toma_company"])}
    )
    assert r.status_code == 201, r.text
    tomador = r.json()
    r = await client.post(
        "/api/nfse/modelos",
        json={
            "company_id": str(cen["prest"]),
            "tomador_id": tomador["id"],
            "nome": "Intermediação mensal",
            "descricao": "Intermediação {competencia}",
            "valor": valor,
        },
    )
    assert r.status_code == 201, r.text
    return {"tomador": tomador, "modelo": r.json()}


def _ambiente(api: respx.MockRouter, ambiente: str = "Development") -> dict:
    """O ambiente da empresa AGORA na NFE.io (empresa + Inscrição Municipal),
    que o emitir consulta antes do POST."""
    return {
        "empresa": api.get(path=f"/v1/companies/{CID}", host="api.nfe.io").mock(
            return_value=httpx.Response(
                200, json={"companies": {"id": CID, "environment": ambiente, "address": {}}}
            )
        ),
        "im": api.get(path=f"/v2/companies/{CID}/municipaltaxes", host="api.nfse.io").mock(
            return_value=httpx.Response(
                200, json={"municipalTaxes": [{"environment": ambiente, "status": "Active"}]}
            )
        ),
    }


def _rotas(
    api: respx.MockRouter, *, lista: list | None = None, ambiente: str = "Development"
) -> dict:
    """Rotas da NFE.io falsa. Qualquer chamada fora delas falha o teste."""
    return {
        **_ambiente(api, ambiente),
        "lista": api.get(path=f"/v3/companies/{CID}/serviceinvoices", host="api.nfe.io").mock(
            return_value=httpx.Response(200, json={"serviceInvoices": lista or [], "page": 1})
        ),
        "post": api.post(path=f"/v3/companies/{CID}/serviceinvoices", host="api.nfe.io"),
        "nota": api.get(path=f"/v3/companies/{CID}/serviceinvoices/{NOTA_ID}", host="api.nfe.io"),
        "external": api.get(
            path__regex=rf"^/v3/companies/{CID}/serviceinvoices/external/.+$", host="api.nfe.io"
        ),
        "xml": api.get(
            path=f"/v3/companies/{CID}/serviceinvoices/{NOTA_ID}/xml", host="api.nfe.io"
        ).mock(return_value=httpx.Response(200, content=b"<Nfse><Numero>2</Numero></Nfse>")),
        "delete": api.delete(
            path=f"/v3/companies/{CID}/serviceinvoices/{NOTA_ID}", host="api.nfe.io"
        ),
    }


def _aceita(request: httpx.Request) -> httpx.Response:
    corpo = json.loads(request.content)
    assert request.headers["Authorization"] == "chave-de-teste"
    assert corpo["externalId"].startswith("dv-") and "rpsNumber" not in corpo
    return httpx.Response(
        202,
        json={"id": NOTA_ID, "environment": "Development", "flowStatus": "WaitingCalculateTaxes"},
        headers={"Location": f"http://api.nfe.io/v3/companies/{CID}/serviceinvoices/{NOTA_ID}"},
    )


@pytest.mark.asyncio
async def test_fluxo_previa_emite_processa_e_nao_deixa_repetir(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"modelo_id": m["modelo"]["id"]}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        r = await client.post(
            "/api/nfse/previa", json={"competencia": "2026-09-01", "itens": [item]}
        )
        assert r.status_code == 200, r.text
        prev = r.json()["itens"][0]
        assert prev["problemas"] == [], prev
        assert prev["teste"] is True and prev["ambiente"] == "Development"
        assert prev["descricao"] == "Intermediação 09/2026"
        assert prev["payload"]["cityServiceCode"] == "6303"
        assert prev["ir"]["retem"] is False and prev["valor_liquido"] == "1500.00"

        rotas["post"].mock(side_effect=_aceita)
        r = await client.post("/api/nfse/emitir", json={"competencia": "2026-09-01", "item": item})
        assert r.status_code == 200, r.text
        e = r.json()
        assert e["status"] == "processando" and e["nfeio_id"] == NOTA_ID and e["teste"] is True
        assert rotas["post"].call_count == 1

        # Mesmo modelo, mesmo mês: bloqueia sem ir à NFE.io.
        r = await client.post("/api/nfse/emitir", json={"competencia": "2026-09-01", "item": item})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_emitida"
        assert rotas["post"].call_count == 1

        rotas["nota"].mock(return_value=httpx.Response(200, json=_nota()))
        r = await client.post("/api/nfse/emissoes/atualizar", json={"ids": [e["id"]]})
        assert r.status_code == 200, r.text
        pronta = r.json()["emissoes"][0]
        assert pronta["status"] == "emitida" and pronta["n_nfse"] == "2"
        assert pronta["check_code"] == "241714855866429"
        assert rotas["xml"].called  # XML guardado na hora

    r = await client.get(f"/api/nfse/emissoes/{e['id']}/xml")
    assert r.status_code == 200 and b"<Numero>2</Numero>" in r.content
    chamadas = (await db.execute(select(NfseChamada))).scalars().all()
    assert chamadas and all(
        "chave-de-teste" not in json.dumps(c.__dict__, default=str) for c in chamadas
    )


@pytest.mark.asyncio
async def test_empresa_em_producao_nao_emite_fora_da_producao_liberada(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(operador)
    m = await _montar(client, cenario)
    await _fiscal(db, cenario, nfeio_ambiente="Production")
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api, ambiente="Production")
        rotas["post"].mock(side_effect=_aceita)
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "producao_bloqueada"
        # Liberar a flag num servidor que não é produção não basta.
        monkeypatch.setattr(get_settings(), "nfse_producao_liberada", True)
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "producao_bloqueada"
        assert rotas["post"].call_count == 0  # nem um POST
        # Produção de verdade + liberada: aí sim.
        monkeypatch.setattr(get_settings(), "env", "production")
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 200, r.text
        assert rotas["post"].call_count == 1


@pytest.mark.asyncio
async def test_sem_resposta_fica_incerta_e_a_conferencia_adota_a_nota(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(side_effect=httpx.ReadTimeout("sem resposta"))
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 200, r.text
        e = r.json()
        assert e["status"] == "incerta" and rotas["post"].call_count == 1
        # "Reenviar" não existe pra incerta: só rejeitada.
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/reenviar")
        assert r.status_code == 409 and rotas["post"].call_count == 1

        # A NFE.io ainda não mostra (indexação atrasa): continua incerta.
        rotas["external"].mock(return_value=httpx.Response(200, json={"serviceInvoices": []}))
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/conferir")
        assert r.json()["status"] == "incerta"
        # Apareceu: adota — uma nota só.
        rotas["external"].mock(
            return_value=httpx.Response(200, json={"serviceInvoices": [_nota()]})
        )
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/conferir")
        assert r.json()["status"] == "emitida" and r.json()["nfeio_id"] == NOTA_ID
        assert rotas["post"].call_count == 1


@pytest.mark.asyncio
async def test_incerta_que_nunca_apareceu_vira_rejeitada_depois_de_10_min(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(return_value=httpx.Response(504, text="Gateway Timeout"))
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        assert e["status"] == "incerta"
        rotas["external"].mock(return_value=httpx.Response(404, json={"message": "not found"}))
        linha = await db.get(NfseEmissao, uuid.UUID(e["id"]))
        linha.enviado_em = datetime.now(UTC) - timedelta(minutes=11)
        await db.commit()
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/conferir")
        assert r.json()["status"] == "rejeitada"
        # Agora pode reenviar: confere o envio anterior e manda com outro externalId.
        rotas["post"].mock(side_effect=_aceita)
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/reenviar")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "processando" and r.json()["tentativas"] == 2
        corpos = [json.loads(c.request.content) for c in rotas["post"].calls]
        assert corpos[0]["externalId"] != corpos[1]["externalId"]


@pytest.mark.asyncio
async def test_already_exists_adota_e_401_nao_cria_nada(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(return_value=httpx.Response(401, text="Unauthorized"))
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        assert e["status"] == "rejeitada" and "chave" in json.dumps(e["erros"], ensure_ascii=False)
        rotas["post"].mock(
            return_value=httpx.Response(
                400, text="service invoice with external id (dv-x) already exists"
            )
        )
        rotas["external"].mock(
            return_value=httpx.Response(200, json={"serviceInvoices": [_nota()]})
        )
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/reenviar")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "emitida"


@pytest.mark.asyncio
async def test_recusada_pela_prefeitura_explica_e_reenvio_usa_a_mesma_linha(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(side_effect=_aceita)
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        rotas["nota"].mock(
            return_value=httpx.Response(
                200,
                json=_nota("IssueFailed", "Error", flowMessage="[1207] Prestador não autorizado"),
            )
        )
        e = (await client.post("/api/nfse/emissoes/atualizar", json={"ids": [e["id"]]})).json()[
            "emissoes"
        ][0]
        assert e["status"] == "rejeitada" and "1207" in json.dumps(e["erros"])
        # Emitir o mesmo modelo de novo reaproveita a linha recusada.
        rotas["external"].mock(
            return_value=httpx.Response(
                200, json={"serviceInvoices": [_nota("IssueFailed", "Error", flowMessage="x")]}
            )
        )
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 200, r.text
        assert r.json()["id"] == e["id"] and r.json()["tentativas"] == 2
    n = (await db.execute(select(NfseEmissao))).scalars().all()
    assert len(n) == 1


@pytest.mark.asyncio
async def test_cancelar_200_202_e_recusado(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    motivo = {"c_motivo": 1, "x_motivo": "Valor digitado errado na nota"}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(return_value=httpx.Response(201, json=_nota()))
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        assert e["status"] == "emitida"
        # Justificativa curta nem sai daqui.
        r = await client.post(
            f"/api/nfse/emissoes/{e['id']}/cancelar", json={"c_motivo": 1, "x_motivo": "curta"}
        )
        assert r.status_code == 422 and rotas["delete"].call_count == 0
        # 202: espera a conferência.
        rotas["delete"].mock(return_value=httpx.Response(202))
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/cancelar", json=motivo)
        assert r.status_code == 200 and r.json()["status"] == "cancelando"
        rotas["nota"].mock(return_value=httpx.Response(200, json=_nota("Cancelled", "Cancelled")))
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/conferir")
        assert r.json()["status"] == "cancelada"
        ev = (await client.get(f"/api/nfse/emissoes/{e['id']}/eventos")).json()
        assert ev[0]["status"] == "registrado" and ev[0]["x_motivo"] == motivo["x_motivo"]

    # Outra nota: cancelamento recusado (4xx) volta a emitida com o motivo.
    await _fiscal(db, cenario)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        outra = "c" * 24
        rotas["post"].mock(return_value=httpx.Response(201, json=_nota(id=outra)))
        r = await client.post(
            "/api/nfse/emitir",
            json={
                "competencia": "2026-09-01",
                "item": {
                    "company_id": str(cenario["prest"]),
                    "tomador_id": m["tomador"]["id"],
                    "descricao": "Avulsa",
                    "valor": "10.00",
                },
            },
        )
        assert r.status_code == 200, r.text
        e2 = r.json()
        api.delete(path=f"/v3/companies/{CID}/serviceinvoices/{outra}", host="api.nfe.io").mock(
            return_value=httpx.Response(400, json={"message": "prazo de cancelamento expirado"})
        )
        r = await client.post(f"/api/nfse/emissoes/{e2['id']}/cancelar", json=motivo)
        assert r.json()["status"] == "emitida"
        assert "prazo" in json.dumps(r.json()["alertas"], ensure_ascii=False)


# --- webhook -------------------------------------------------------------------------


def _assinado(corpo: dict, segredo: str = SEGREDO) -> tuple[bytes, str]:
    cru = json.dumps(corpo).encode()
    return cru, "sha1=" + hmac.new(segredo.encode(), cru, hashlib.sha1).hexdigest().upper()


@pytest.mark.asyncio
async def test_webhook_assinatura_repeticao_e_o_corpo_nunca_decide(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(side_effect=_aceita)
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        assert e["status"] == "processando"
        auth_as(None)  # a NFE.io não tem login

        # O aviso diz "cancelada", mas a NFE.io (GET) diz emitida: vale o GET.
        cru, sig = _assinado({"action": "x", "payload": {"id": NOTA_ID, "flowStatus": "Cancelled"}})
        hook = uuid.uuid4().hex
        rotas["nota"].mock(return_value=httpx.Response(200, json=_nota()))
        h = {"X-Hub-Signature": sig, "X-Hook-Id": hook, "Content-Type": "application/json"}
        r = await client.post("/api/webhooks/nfeio", content=cru, headers=h)
        assert r.status_code == 200 and r.json()["status"] == "emitida", r.text
        chamadas = rotas["nota"].call_count
        r = await client.post("/api/webhooks/nfeio", content=cru, headers=h)
        assert r.json().get("duplicado") is True and rotas["nota"].call_count == chamadas

        ruim = {**h, "X-Hub-Signature": "sha1=" + "0" * 40, "X-Hook-Id": uuid.uuid4().hex}
        assert (
            await client.post("/api/webhooks/nfeio", content=cru, headers=ruim)
        ).status_code == 401
        sem = {"X-Hook-Id": uuid.uuid4().hex, "Content-Type": "application/json"}
        assert (
            await client.post("/api/webhooks/nfeio", content=cru, headers=sem)
        ).status_code == 401

        cru2, sig2 = _assinado({"payload": {"id": "f" * 24}})
        h2 = {"X-Hub-Signature": sig2, "X-Hook-Id": uuid.uuid4().hex}
        r = await client.post("/api/webhooks/nfeio", content=cru2, headers=h2)
        assert r.status_code == 200 and r.json()["ignorado"] == "nota_desconhecida"

        # O teste de ligação da NFE.io no cadastro (sem nota): 2xx, sem mexer em nada.
        r = await client.post("/api/webhooks/nfeio", content=b'{"ping": true}', headers=sem)
        assert r.status_code == 200 and r.json()["ignorado"] == "sem_id"
        r = await client.post("/api/webhooks/nfeio", content=b"", headers=sem)
        assert r.status_code == 200

        monkeypatch.setattr(get_settings(), "nfeio_webhook_secret", "")
        h3 = {"X-Hub-Signature": sig, "X-Hook-Id": uuid.uuid4().hex}
        assert (
            await client.post("/api/webhooks/nfeio", content=cru, headers=h3)
        ).status_code == 503


# --- empresas: ligar, sincronizar, sem senha da prefeitura ---------------------------


def _empresa_nfeio(**kw) -> dict:
    e = {
        "id": CID,
        "name": "EMPRESA TESTE LTDA",
        "federalTaxNumber": int(CNPJ_PREST),
        "municipalTaxNumber": "16115040",
        "environment": "Development",
        "fiscalStatus": "Active",
        "taxRegime": "LucroPresumido",
        "certificate": {"status": "Active", "expiresOn": "2027-01-07T00:00:00"},
        "address": {"city": {"code": "3550308", "name": "São Paulo"}, "state": "SP"},
    }
    e.update(kw)
    return e


def _mock_empresas(api: respx.MockRouter, empresas: list[dict]) -> None:
    api.get(path="/v1/companies", host="api.nfe.io").mock(
        return_value=httpx.Response(200, json={"companies": empresas, "page": 1})
    )
    api.get(path__regex=r"^/v2/companies/[0-9a-f]+/municipaltaxes$", host="api.nfse.io").mock(
        return_value=httpx.Response(
            200,
            json={
                "municipalTaxes": [
                    {
                        "environment": "Development",
                        "fiscalStatus": "Active",
                        "taxNumber": "16115040",
                        "city": {"code": "3550308", "name": "São Paulo", "state": "SP"},
                        "loginName": "usuario-da-prefeitura",
                        "loginPassword": "SENHA-DA-PREFEITURA",
                    }
                ]
            },
        )
    )
    api.get(path__regex=r"^/v3/companies/[0-9a-f]+/serviceinvoices$", host="api.nfe.io").mock(
        return_value=httpx.Response(200, json={"serviceInvoices": [], "page": 1})
    )


@pytest.mark.asyncio
async def test_sincronizar_casa_pelo_cnpj_e_nunca_guarda_a_senha_da_prefeitura(
    client: AsyncClient,
    db: AsyncSession,
    make_user,
    auth_as: Callable[[User | None], None],
):
    prest = Company(razao_social="EMPRESA TESTE LTDA", apelido="teste", cnpj=CNPJ_PREST)
    so_aqui = Company(razao_social="SO DAVINCI LTDA", apelido="so", cnpj=CNPJ_TOMA)
    db.add_all([prest, so_aqui])
    await db.commit()
    auth_as(await make_user(role=UserRole.ADMIN))
    fora = _empresa_nfeio(id="a" * 24, name="SO NFEIO LTDA", federalTaxNumber=4252011000110)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _mock_empresas(api, [_empresa_nfeio(), fora])
        r = await client.post("/api/nfse/nfeio/sincronizar")
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["apelido"] for x in d["ligadas"]] == ["teste"]
    assert [x["cnpj"] for x in d["so_na_nfeio"]] == ["04252011000110"]
    assert CNPJ_TOMA in [x["cnpj"] for x in d["so_no_davinci"]]
    f = await db.get(CompanyFiscal, prest.id)
    await db.refresh(f)
    assert f.nfeio_company_id == CID and f.nfeio_ambiente == "Development"
    assert f.nfeio_regime == "LucroPresumido" and f.city_service_code == "6303"
    sem_regime = CompanyFiscal()
    svc_empresas.aplicar(sem_regime, _empresa_nfeio(taxRegime="None"), [], None)
    assert sem_regime.nfeio_regime is None  # a NFE.io manda o texto "None"
    tudo = json.dumps(f.nfeio_resumo, ensure_ascii=False)
    assert "SENHA-DA-PREFEITURA" not in tudo and "usuario-da-prefeitura" not in tudo


@pytest.mark.asyncio
async def test_atualizar_do_topo_rele_o_certificado_de_todas_as_ligadas(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    auth_as: Callable[[User | None], None],
):
    # 01/10/2026 (Eduardo): trocou o certificado vencido na NFE.io e o "atualizar"
    # do topo não mudava nada — só o "Atualizar" de dentro da empresa relia.
    prest = Company(razao_social="EMPRESA TESTE LTDA", apelido="teste", cnpj=CNPJ_PREST)
    db.add(prest)
    await db.flush()
    f = svc_empresas.novo_fiscal(prest.id)
    f.nfeio_company_id = CID
    f.nfeio_cert_status = "Overdue"
    f.nfeio_cert_expira = date(2026, 8, 1)
    db.add(f)
    await db.commit()
    auth_as(operador)
    novo = {"status": "Active", "expiresOn": "2027-07-27T19:29:00+00:00"}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _mock_empresas(api, [_empresa_nfeio(certificate=novo)])
        r = await client.post("/api/nfse/nfeio/atualizar-ligadas")
    assert r.status_code == 200, r.text
    assert r.json()["atualizadas"] == 1
    await db.refresh(f)
    assert f.nfeio_cert_status == "Active" and f.nfeio_cert_expira == date(2027, 7, 27)


@pytest.mark.asyncio
async def test_ligar_pelo_link_colado_e_pelo_cnpj(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    auth_as: Callable[[User | None], None],
):
    prest = Company(razao_social="EMPRESA TESTE LTDA", apelido="teste", cnpj=CNPJ_PREST)
    db.add(prest)
    await db.commit()
    auth_as(operador)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _mock_empresas(api, [_empresa_nfeio()])
        api.get(path__regex=r"^/v1/companies/[^/]+$", host="api.nfe.io").mock(
            return_value=httpx.Response(200, json={"companies": _empresa_nfeio()})
        )
        r = await client.post(
            f"/api/nfse/prestadores/{prest.id}/nfeio/ligar",
            json={"ref": f"https://app.nfe.io/companies/{CID}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["nfeio"]["company_id"] == CID and r.json()["nfeio"]["teste"] is True
        assert r.json()["nfeio"]["retem_ir"] is True  # Lucro Presumido
        r = await client.post(f"/api/nfse/prestadores/{prest.id}/nfeio/ligar", json={"ref": ""})
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_rotina_do_worker_confere_so_as_pendentes(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(side_effect=_aceita)
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        rotas["nota"].mock(return_value=httpx.Response(200, json=_nota()))
        resumo = await svc.conferir_pendentes(db)
        assert resumo["conferidas"] == 1 and resumo["mudaram"] == 1
        resumo = await svc.conferir_pendentes(db)  # emitida não entra mais
        assert resumo == {"conferidas": 0}
    linha = await db.get(NfseEmissao, uuid.UUID(e["id"]))
    await db.refresh(linha)
    assert linha.status == "emitida"


@pytest.mark.asyncio
async def test_ir_na_emissao_de_lucro_presumido(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    await _fiscal(db, cenario, nfeio_regime="LucroPresumido")
    m = await _montar(client, cenario, valor="3252.35")
    enviados: list[dict] = []

    def _captura(request: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(request.content))
        return _aceita(request)

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        item = {"modelo_id": m["modelo"]["id"]}
        prev = (
            await client.post(
                "/api/nfse/previa", json={"competencia": "2026-09-01", "itens": [item]}
            )
        ).json()["itens"][0]
        assert prev["ir"]["retem"] is True and prev["ir"]["valor"] == "48.79"
        assert prev["valor_liquido"] == "3203.56"
        rotas["post"].mock(side_effect=_captura)
        e = (
            await client.post("/api/nfse/emitir", json={"competencia": "2026-09-01", "item": item})
        ).json()
    assert e["ir_retido"] == "48.79"
    assert enviados[0]["irAmountWithheld"] == 48.79
    assert "RETENÇÕES CONFORME LEI 10.833/2003" in enviados[0]["description"]


@pytest.mark.asyncio
async def test_aviso_de_nota_ja_existente_na_nfeio(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    painel = _nota(id="b" * 24, number=7, servicesAmount=1500.0, externalId=None)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _rotas(api, lista=[painel])
        prev = (
            await client.post(
                "/api/nfse/previa",
                json={"competencia": "2026-09-01", "itens": [{"modelo_id": m["modelo"]["id"]}]},
            )
        ).json()["itens"][0]
    assert prev["duplicadas_nfeio"] and prev["duplicadas_nfeio"][0]["numero"] in (7, "7")
    assert any("Já existe nota" in a for a in prev["avisos"])


@pytest.mark.asyncio
async def test_sem_permissao_nao_ve_nem_emite(
    client: AsyncClient, make_user, cenario: dict, auth_as: Callable[[User | None], None]
):
    auth_as(await make_user(permissions={}))
    assert (await client.get("/api/nfse/prestadores")).status_code == 403
    auth_as(await make_user(permissions={"emissao_servico": {"view": True}}))
    assert (await client.get("/api/nfse/prestadores")).status_code == 200
    r = await client.post(
        "/api/nfse/emitir",
        json={"competencia": "2026-09-01", "item": {"company_id": str(cenario["prest"])}},
    )
    assert r.status_code == 403
    assert (await client.post("/api/nfse/nfeio/sincronizar")).status_code == 403
    assert (await client.post("/api/nfse/nfeio/atualizar-ligadas")).status_code == 403


# --- regras nossas que ficaram: percentual, Receita, municípios -----------------------


def test_conta_do_percentual_arredonda_meio_pra_cima():
    assert T.valor_percentual(Decimal("200000.00"), Decimal("0.5")) == Decimal("1000.00")
    assert T.valor_percentual(Decimal("101.00"), Decimal("0.5")) == Decimal("0.51")
    assert T.valor_percentual(Decimal("1.00"), Decimal("0.5")) == Decimal("0.01")
    assert T.valor_percentual(Decimal("1234.56"), Decimal("0.1234")) == Decimal("1.52")


def test_dinheiro_digitado_do_jeito_brasileiro():
    it = ItemIn(base_calculo="200.000,00", percentual="0,5")
    assert it.base_calculo == Decimal("200000.00") and it.percentual == Decimal("0.5000")
    assert ItemIn(valor="1.500,00").valor == Decimal("1500.00")
    assert ItemIn(valor="0,50").valor == Decimal("0.50")
    for ambiguo in ("200.000", "1.000.000", "1.500"):
        with pytest.raises(ValueError, match="ponto de milhar"):
            ItemIn(base_calculo=ambiguo)
    assert ItemIn(percentual="0.500").percentual == Decimal("0.5000")


def test_marcadores_de_percentual_e_base():
    txt = T.resolver_descricao(
        "Comissão de {percentual} sobre {base} ({competencia})",
        date(2026, 9, 1),
        percentual=Decimal("0.5000"),
        base=Decimal("200000"),
    )
    assert txt == "Comissão de 0,5% sobre R$ 200.000,00 (09/2026)"
    assert T.fmt_percentual(Decimal("1.0000")) == "1%"
    assert T.fmt_reais(Decimal("1234567.8")) == "R$ 1.234.567,80"


def test_resolver_percentual_na_ordem_certa():
    r = svc.resolver_percentual
    e = Decimal("0.5")
    assert r(None, None, None) == (None, None)
    assert r(None, None, e) == (Decimal("0.5000"), "empresa")
    assert r(None, Decimal("1"), e) == (Decimal("1.0000"), "nota_fixa")
    assert r(Decimal("2"), Decimal("1"), e) == (Decimal("2.0000"), "item")


@pytest.mark.asyncio
async def test_previa_de_percentual_usa_a_porcentagem_da_empresa(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    c = await db.get(Company, cenario["prest"])
    c.percentual_servico = Decimal("0.5")
    await db.commit()
    r = await client.post(
        "/api/nfse/tomadores", json={"tipo": "grupo", "company_id": str(cenario["toma_company"])}
    )
    tomador = r.json()
    item = {
        "company_id": str(cenario["prest"]),
        "tomador_id": tomador["id"],
        "descricao": "Comissão de {percentual} sobre {base}",
        "base_calculo": "200000.00",
    }
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        prev = (
            await client.post(
                "/api/nfse/previa", json={"competencia": "2026-09-01", "itens": [item]}
            )
        ).json()["itens"][0]
        assert prev["valor"] == "1000.00" and prev["percentual_origem"] == "empresa", prev
        assert prev["descricao"] == "Comissão de 0,5% sobre R$ 200.000,00"
        # Sem base: problema, e nem vai à NFE.io.
        sem_base = {k: v for k, v in item.items() if k != "base_calculo"}
        r = await client.post(
            "/api/nfse/emitir", json={"competencia": "2026-09-01", "item": sem_base}
        )
        assert r.status_code == 422
        assert rotas["post"].call_count == 0


def test_normalizar_nome_de_municipio():
    assert svc_municipios.normalizar("Embu-Guaçu") == "embu guacu"
    assert svc_municipios.nome_do_codigo("3550308") == ("São Paulo", "SP")
    assert svc_municipios.nome_do_codigo("9999999") is None


@pytest.mark.asyncio
async def test_municipios_busca_sem_acento_e_por_codigo(
    client: AsyncClient, make_user, auth_as: Callable[[User | None], None]
):
    auth_as(await make_user(permissions={"emissao_servico": {"view": True}}))
    r = await client.get("/api/nfse/municipios", params={"q": "sao paulo"})
    assert r.json()[0] == {"cmun_ibge": "3550308", "nome": "São Paulo", "uf": "SP"}
    r = await client.get("/api/nfse/municipios", params={"q": "3550308"})
    assert r.json() == [{"cmun_ibge": "3550308", "nome": "São Paulo", "uf": "SP"}]


# --- revisão de 30/09: ambiente na hora, trava do mês, cliques duplos, conferência ----


@pytest.mark.asyncio
async def test_empresa_que_virou_producao_no_painel_nao_emite_nota_real(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    """Gravado: Development (última sincronização). Na NFE.io, AGORA: Production.
    O DaVinci confere na hora e não manda o POST."""
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api, ambiente="Production")
        rotas["post"].mock(side_effect=_aceita)
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "producao_bloqueada"
        assert rotas["post"].call_count == 0
    f = await db.get(CompanyFiscal, cenario["prest"])
    await db.refresh(f)
    assert f.nfeio_ambiente == "Production"  # a tela passa a mostrar PRODUÇÃO

    # Só a Inscrição Municipal virou Produção: também não sai.
    await _fiscal(db, cenario, nfeio_ambiente="Development")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        api.get(path=f"/v2/companies/{CID}/municipaltaxes", host="api.nfse.io").mock(
            return_value=httpx.Response(
                200, json={"municipalTaxes": [{"environment": "Production"}]}
            )
        )
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 409 and rotas["post"].call_count == 0


@pytest.mark.asyncio
async def test_sem_confirmar_o_ambiente_nao_emite(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["empresa"].mock(return_value=httpx.Response(503, text="Service Unavailable"))
        rotas["post"].mock(side_effect=_aceita)
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 502 and r.json()["detail"]["code"] == "nfeio_sem_resposta"
        assert rotas["post"].call_count == 0
    assert (await db.execute(select(NfseEmissao))).scalars().all() == []


@pytest.mark.asyncio
async def test_nota_real_do_mes_trava_mesmo_se_o_ambiente_mudou_e_teste_nao_trava_a_real(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(side_effect=_aceita)
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        assert e["status"] == "processando"
    # A nota de TESTE do mês não impede a de verdade (empresa foi pra Produção).
    monkeypatch.setattr(get_settings(), "env", "production")
    monkeypatch.setattr(get_settings(), "nfse_producao_liberada", True)
    await _fiscal(db, cenario, nfeio_ambiente="Production")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api, ambiente="Production")
        rotas["post"].mock(
            return_value=httpx.Response(
                202, json={"id": "d" * 24, "environment": "Production", "flowStatus": "WaitingSend"}
            )
        )
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 200, r.text
        real = r.json()
        assert real["nfeio_ambiente"] == "Production" and rotas["post"].call_count == 1
    # Já a nota REAL trava tudo — mesmo que a empresa volte pra Teste.
    monkeypatch.setattr(get_settings(), "env", "development")
    monkeypatch.setattr(get_settings(), "nfse_producao_liberada", False)
    await _fiscal(db, cenario, nfeio_ambiente="Development")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(side_effect=_aceita)
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_emitida"
        assert r.json()["detail"]["emissao_id"] == real["id"]
        assert rotas["post"].call_count == 0


@pytest.mark.asyncio
async def test_409_do_post_adota_em_vez_de_recusar(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(return_value=httpx.Response(409, json={"message": "conflict"}))
        rotas["external"].mock(
            return_value=httpx.Response(200, json={"serviceInvoices": [_nota()]})
        )
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 200 and r.json()["status"] == "emitida"


@pytest.mark.asyncio
async def test_conferencia_nao_grava_estado_velho_por_cima_de_mudanca_nova(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    """O worker leu a nota 'processando'; enquanto perguntava à NFE.io, a tela
    já a marcou emitida e alguém cancelou. O resultado velho é descartado."""
    from sqlalchemy import update

    from app.db import SessionLocal

    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(side_effect=_aceita)
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        linha = await db.get(NfseEmissao, uuid.UUID(e["id"]))
        assert linha.status == "processando"  # o que o worker "leu"

        async def _no_meio(request: httpx.Request) -> httpx.Response:
            async with SessionLocal() as outra:
                await outra.execute(
                    update(NfseEmissao).where(NfseEmissao.id == linha.id).values(status="cancelada")
                )
                await outra.commit()
            return httpx.Response(200, json=_nota())

        rotas["nota"].mock(side_effect=_no_meio)
        resumo = await svc.atualizar_varias(db, [linha])
    assert resumo["mudaram"] == 0
    await db.refresh(linha)
    assert linha.status == "cancelada"


@pytest.mark.asyncio
async def test_reenvio_de_nota_recusada_em_teste_nao_vira_nota_real(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(operador)
    m = await _montar(client, cenario)
    item = {"competencia": "2026-09-01", "item": {"modelo_id": m["modelo"]["id"]}}
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        rotas["post"].mock(return_value=httpx.Response(400, json={"message": "dados inválidos"}))
        e = (await client.post("/api/nfse/emitir", json=item)).json()
        assert e["status"] == "rejeitada" and e["nfeio_ambiente"] == "Development"
    # A empresa passou para Produção e o servidor está liberado: o reenvio da
    # linha de TESTE não pode sair como nota real.
    monkeypatch.setattr(get_settings(), "env", "production")
    monkeypatch.setattr(get_settings(), "nfse_producao_liberada", True)
    await _fiscal(db, cenario, nfeio_ambiente="Production")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api, ambiente="Production")
        rotas["external"].mock(return_value=httpx.Response(200, json={"serviceInvoices": []}))
        rotas["post"].mock(side_effect=_aceita)
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/reenviar")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "ambiente_mudou"
        assert rotas["post"].call_count == 0
        # Emitir o modelo de novo cria uma nota nova (real), sem reaproveitar a de teste.
        r = await client.post("/api/nfse/emitir", json=item)
        assert r.status_code == 200, r.text
        assert r.json()["id"] != e["id"] and rotas["post"].call_count == 1


# --- base = faturamento do mês da empresa (Eduardo, 30/09) ------------------------------


async def _vendas(db: AsyncSession, dono: User, cnpj: str) -> None:
    """2 lojas com o CNPJ da empresa + 1 de outro CNPJ; pedidos de setembro e de
    fora (cancelado, outubro, outra loja). bling_orders tem uma linha por item."""
    from app.models import BlingOrder, StoreInfo

    db.add_all(
        [
            StoreInfo(
                user_id=dono.id,
                platform="shopee",
                account_name="teste",
                cnpj=cnpj,
                bling_store_id="111",
            ),
            StoreInfo(
                user_id=dono.id,
                platform="ml",
                account_name="teste",
                cnpj=cnpj,
                bling_store_id="222",
            ),
            StoreInfo(
                user_id=dono.id,
                platform="ml",
                account_name="outra",
                cnpj=CNPJ_TOMA,
                bling_store_id="333",
            ),
        ]
    )

    def ped(bid: int, loja: str, total: str, sit: str, quando: str, item: int = 0) -> BlingOrder:
        return BlingOrder(
            bling_id=bid,
            loja=loja,
            total=Decimal(total),
            situacao=sit,
            data=datetime.fromisoformat(quando),
            item_index=item,
        )

    db.add_all(
        [
            ped(1, "111", "100.00", "6", "2026-09-05T10:00:00-03:00", 0),
            ped(
                1, "111", "100.00", "6", "2026-09-05T10:00:00-03:00", 1
            ),  # 2º item: não soma de novo
            ped(2, "222", "50.50", "83953", "2026-09-30T23:30:00-03:00"),  # último minuto de SP
            ped(3, "111", "999.00", "12", "2026-09-10T10:00:00-03:00"),  # cancelado: fora
            ped(4, "111", "70.00", "15", "2026-10-01T00:10:00-03:00"),  # outubro: fora
            ped(5, "333", "500.00", "6", "2026-09-10T10:00:00-03:00"),  # outro CNPJ: fora
        ]
    )
    await db.commit()


@pytest.mark.asyncio
async def test_faturamento_do_mes_mesma_regua_da_aba_faturamento(
    db: AsyncSession, operador: User, cenario: dict
):
    from app.services.nfse import faturamento as svc_fat

    await _vendas(db, operador, CNPJ_PREST)
    f = await svc_fat.faturamento_da_empresa(db, cenario["prest"], date(2026, 9, 1))
    assert f is not None
    assert f.valor == Decimal("150.50") and f.pedidos == 2
    assert {(x.plataforma, str(x.valor)) for x in f.lojas} == {
        ("shopee", "100.00"),
        ("ml", "50.50"),
    }
    out = await svc_fat.faturamento_da_empresa(db, cenario["prest"], date(2026, 10, 1))
    assert out.valor == Decimal("70.00")
    # A loja de outro CNPJ é de outra empresa (aqui, a tomadora).
    outra = await svc_fat.faturamento_da_empresa(db, cenario["toma_company"], date(2026, 9, 1))
    assert outra is not None and outra.valor == Decimal("500.00")
    # Empresa sem loja com o CNPJ dela: None (a base cai na sugerida ou é digitada).
    sem = Company(razao_social="SEM LOJA LTDA", apelido="semloja", cnpj="04252011000110")
    db.add(sem)
    await db.commit()
    assert await svc_fat.faturamento_da_empresa(db, sem.id, date(2026, 9, 1)) is None


@pytest.mark.asyncio
async def test_base_da_nota_de_percentual_vem_do_faturamento(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    await _vendas(db, operador, CNPJ_PREST)
    tomador = (
        await client.post(
            "/api/nfse/tomadores",
            json={"tipo": "grupo", "company_id": str(cenario["toma_company"])},
        )
    ).json()
    modelo = (
        await client.post(
            "/api/nfse/modelos",
            json={
                "company_id": str(cenario["prest"]),
                "tomador_id": tomador["id"],
                "nome": "Comissão",
                "descricao": "Comissão de {percentual} sobre {base}",
                "tipo_valor": "percentual",
                "percentual": "10",
                "base_padrao": "999999.00",
            },
        )
    ).json()
    r = await client.get("/api/nfse/faturamento", params={"competencia": "2026-09-01"})
    assert r.status_code == 200
    emp = {e["company_id"]: e for e in r.json()["empresas"]}
    assert emp[str(cenario["prest"])]["valor"] == "150.50"

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)

        async def previa(item: dict) -> dict:
            r = await client.post(
                "/api/nfse/previa", json={"competencia": "2026-09-01", "itens": [item]}
            )
            assert r.status_code == 200, r.text
            return r.json()["itens"][0]

        # Sem base: o faturamento (e não a base sugerida da nota fixa).
        p = await previa({"modelo_id": modelo["id"]})
        assert p["base_calculo"] == "150.50" and p["base_origem"] == "faturamento"
        assert p["valor"] == "15.05" and p["faturamento"]["pedidos"] == 2
        assert p["descricao"] == "Comissão de 10% sobre R$ 150,50"
        # A tela manda a base que mostrou: igual ao faturamento continua "faturamento".
        p = await previa({"modelo_id": modelo["id"], "base_calculo": "150.50"})
        assert p["base_origem"] == "faturamento"
        # Trocada à mão: vale a digitada.
        p = await previa({"modelo_id": modelo["id"], "base_calculo": "1000.00"})
        assert p["base_origem"] == "digitada" and p["valor"] == "100.00"

        # Emitir sem base: sai com o faturamento e grava a origem.
        rotas["post"].mock(side_effect=_aceita)
        e = (
            await client.post(
                "/api/nfse/emitir",
                json={"competencia": "2026-09-01", "item": {"modelo_id": modelo["id"]}},
            )
        ).json()
        assert e["base_calculo"] == "150.50" and e["valor_servico"] == "15.05"
        assert e["snapshot"]["servico"]["base_origem"] == "faturamento"
        assert json.loads(rotas["post"].calls[0].request.content)["servicesAmount"] == 15.05

    # Mês sem venda: cai na base sugerida da nota fixa.
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _rotas(api)
        r = await client.post(
            "/api/nfse/previa",
            json={"competencia": "2026-08-01", "itens": [{"modelo_id": modelo["id"]}]},
        )
        p = r.json()["itens"][0]
        assert p["base_origem"] == "nota_fixa" and p["base_calculo"] == "999999.00"


# --- mês da base: faturamento de OUTRO mês (Eduardo, 01/10/2026) ---------------------------
# "como virou o mês, o faturamento de outubro está zerado ainda… precisa ter a opção
# de eu escolher o mês, por exemplo setembro." A nota continua de outubro; só a base
# vem do faturamento de setembro. Em _vendas: setembro = R$ 150,50, outubro = R$ 70,00.


def test_mes_da_base_padrao_e_limites():
    out = date(2026, 10, 1)
    assert svc.mes_da_base(out, None) == out  # padrão: o mesmo mês da nota
    assert svc.mes_da_base(date(2026, 10, 20), date(2026, 9, 15)) == date(2026, 9, 1)
    assert svc.mes_da_base(out, date(2025, 10, 1)) == date(2025, 10, 1)  # 12 meses: pode
    for mes, msg in (
        (date(2026, 11, 1), svc.MES_BASE_FUTURO),
        (date(2025, 9, 1), svc.MES_BASE_ANTIGO),
    ):
        with pytest.raises(svc.NfseError) as e:
            svc.mes_da_base(out, mes)
        assert e.value.status == 422
        assert e.value.detail == {"code": "mes_da_base_invalido", "mensagem": msg}


@pytest.mark.asyncio
async def test_completar_base_usa_o_faturamento_do_mes_da_base(
    db: AsyncSession, operador: User, cenario: dict
):
    await _vendas(db, operador, CNPJ_PREST)

    def item(base_competencia: date | None = None) -> svc.Item:
        it = svc.Item(
            company_id=cenario["prest"],
            tomador_id=uuid.uuid4(),
            descricao="x",
            valor=None,
            base_competencia=base_competencia,
        )
        return it.usar_percentual(None, Decimal("10"))

    # Sem mês da base: o faturamento do mês da nota, como antes de 01/10.
    it = await svc.completar_base(db, item(), date(2026, 10, 1))
    assert it.base_calculo == Decimal("70.00") and it.base_origem == svc.BASE_FATURAMENTO
    assert it.base_competencia == date(2026, 10, 1) and it.valor == Decimal("7.00")
    # Base de setembro numa nota de outubro.
    it = await svc.completar_base(db, item(date(2026, 9, 1)), date(2026, 10, 1))
    assert it.base_calculo == Decimal("150.50") and it.base_origem == svc.BASE_FATURAMENTO
    assert it.base_competencia == date(2026, 9, 1) and it.faturamento.pedidos == 2
    assert it.valor == Decimal("15.05")
    # Base digitada igual ao faturamento de setembro continua "do faturamento".
    it = item(date(2026, 9, 1)).usar_percentual(Decimal("150.50"), Decimal("10"))
    await svc.completar_base(db, it, date(2026, 10, 1))
    assert it.base_origem == svc.BASE_FATURAMENTO
    # Mês da base depois do mês da nota: recusa.
    with pytest.raises(svc.NfseError):
        await svc.completar_base(db, item(date(2026, 11, 1)), date(2026, 10, 1))


@pytest.mark.asyncio
async def test_previa_emitir_e_reenvio_com_a_base_de_outro_mes(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    await _vendas(db, operador, CNPJ_PREST)
    tomador = (
        await client.post(
            "/api/nfse/tomadores",
            json={"tipo": "grupo", "company_id": str(cenario["toma_company"])},
        )
    ).json()

    async def modelo(**campos) -> dict:
        r = await client.post(
            "/api/nfse/modelos",
            json={
                "company_id": str(cenario["prest"]),
                "tomador_id": tomador["id"],
                "descricao": "Intermediação {competencia}",
                **campos,
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    pct = await modelo(nome="Comissão", tipo_valor="percentual", percentual="10")
    fixo = await modelo(nome="Mensalidade", valor="1500.00")
    setembro = {"modelo_id": pct["id"], "base_competencia": "2026-09-01"}

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)

        async def previa(*itens: dict) -> list[dict]:
            r = await client.post(
                "/api/nfse/previa", json={"competencia": "2026-10-01", "itens": list(itens)}
            )
            assert r.status_code == 200, r.text
            return r.json()["itens"]

        # Padrão (sem mês da base): o faturamento de outubro.
        (p,) = await previa({"modelo_id": pct["id"]})
        assert p["base_calculo"] == "70.00" and p["base_competencia"] == "2026-10-01"
        # Setembro: a base é o faturamento de setembro; a nota continua de outubro.
        p, f = await previa(setembro, {"modelo_id": fixo["id"], "base_competencia": "2026-09-01"})
        assert p["base_calculo"] == "150.50" and p["valor"] == "15.05"
        assert p["base_origem"] == "faturamento" and p["base_competencia"] == "2026-09-01"
        assert p["faturamento"]["valor"] == "150.50" and p["faturamento"]["pedidos"] == 2
        assert p["descricao"] == "Intermediação 10/2026" and p["problemas"] == []
        # Valor fixo não muda com o mês da base.
        assert f["valor"] == "1500.00" and f["base_competencia"] is None
        assert f["faturamento"] is None

        # Mês da base depois do mês da nota, ou mais de 12 meses antes: 422 na prévia.
        for mes, msg in (("2026-11-01", svc.MES_BASE_FUTURO), ("2025-09-01", svc.MES_BASE_ANTIGO)):
            r = await client.post(
                "/api/nfse/previa",
                json={
                    "competencia": "2026-10-01",
                    "itens": [{"modelo_id": pct["id"], "base_competencia": mes}],
                },
            )
            assert r.status_code == 422, r.text
            assert r.json()["detail"] == {"code": "mes_da_base_invalido", "mensagem": msg}

        # ... e no emitir, sem nenhum POST.
        rotas["post"].mock(side_effect=_aceita)
        r = await client.post(
            "/api/nfse/emitir",
            json={
                "competencia": "2026-10-01",
                "item": {"modelo_id": pct["id"], "base_competencia": "2026-11-01"},
            },
        )
        assert r.status_code == 422 and r.json()["detail"]["code"] == "mes_da_base_invalido"
        assert rotas["post"].call_count == 0

        # Emitir com a base de setembro: o retrato grava o mês da base.
        r = await client.post(
            "/api/nfse/emitir", json={"competencia": "2026-10-01", "item": setembro}
        )
        assert r.status_code == 200, r.text
        e = r.json()
        assert e["competencia"] == "2026-10-01"
        assert e["base_calculo"] == "150.50" and e["valor_servico"] == "15.05"
        serv = e["snapshot"]["servico"]
        assert serv["base_competencia"] == "2026-09-01" and serv["base_origem"] == "faturamento"
        assert serv["faturamento"]["valor"] == "150.50"
        assert json.loads(rotas["post"].calls[0].request.content)["servicesAmount"] == 15.05

        # Recusada: o reenvio vai com a mesma base E o mesmo mês da base (setembro),
        # não com o faturamento de outubro.
        rotas["nota"].mock(
            return_value=httpx.Response(200, json=_nota("IssueFailed", "Error", flowMessage="x"))
        )
        rotas["external"].mock(
            return_value=httpx.Response(
                200, json={"serviceInvoices": [_nota("IssueFailed", "Error", flowMessage="x")]}
            )
        )

        async def recusar(emissao_id: str) -> dict:
            r = await client.post("/api/nfse/emissoes/atualizar", json={"ids": [emissao_id]})
            (x,) = r.json()["emissoes"]
            assert x["status"] == "rejeitada"
            return x

        e = await recusar(e["id"])

        # Revisão de 01/10: a recusada que sai de novo pela aba "Emitir do mês" (o
        # /emitir, que reaproveita a linha) manda a base e o mês GRAVADOS nela — o
        # retrato continua "faturamento de setembro", não "base digitada" de outubro.
        r = await client.post(
            "/api/nfse/emitir",
            json={
                "competencia": "2026-10-01",
                "item": {
                    "modelo_id": pct["id"],
                    "base_calculo": "150.50",
                    "percentual": "10",
                    "base_competencia": "2026-09-01",
                },
            },
        )
        assert r.status_code == 200, r.text
        x = r.json()
        assert x["id"] == e["id"] and x["tentativas"] == 2 and x["base_calculo"] == "150.50"
        serv = x["snapshot"]["servico"]
        assert serv["base_competencia"] == "2026-09-01" and serv["base_origem"] == "faturamento"
        assert serv["faturamento"]["valor"] == "150.50"
        assert json.loads(rotas["post"].calls[1].request.content)["servicesAmount"] == 15.05

        # E o reenvio pela rota da recusada reaproveita o mês gravado sozinho.
        e = await recusar(x["id"])
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/reenviar")
        assert r.status_code == 200, r.text
        e = r.json()
        assert e["tentativas"] == 3 and e["base_calculo"] == "150.50"
        serv = e["snapshot"]["servico"]
        assert serv["base_competencia"] == "2026-09-01" and serv["base_origem"] == "faturamento"
        assert serv["faturamento"]["valor"] == "150.50"
        assert json.loads(rotas["post"].calls[2].request.content)["servicesAmount"] == 15.05


@pytest.mark.asyncio
async def test_reenvio_de_nota_gravada_antes_do_mes_da_base_usa_o_mes_da_nota(
    db: AsyncSession, cenario: dict
):
    """Nota de percentual gravada antes de 01/10 (sem base_competencia no
    retrato): o reenvio fica no mês da nota, como era."""
    e = NfseEmissao(
        company_id=cenario["prest"],
        competencia=date(2026, 9, 1),
        status="rejeitada",
        descricao="x",
        valor_servico=Decimal("15.05"),
        base_calculo=Decimal("150.50"),
        percentual=Decimal("10"),
        snapshot={"servico": {"base_origem": "faturamento", "percentual_origem": "nota_fixa"}},
    )
    it = svc.Item(company_id=cenario["prest"], tomador_id=uuid.uuid4(), descricao="x", valor=None)
    svc.repetir_valor(it, e)
    assert it.base_competencia is None and it.base_calculo == Decimal("150.50")
    await svc.completar_base(db, it, e.competencia)
    assert it.base_competencia == date(2026, 9, 1) and it.base_origem == "faturamento"


# --- tomadores automáticos: as contas Bling de NF (Eduardo, 30/09) ------------------------

_EMIT_XML = (
    '<NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe><emit><CNPJ>{cnpj}</CNPJ>'
    "<xNome>{nome}</xNome><enderEmit><xLgr>Rua Galvao Bueno</xLgr><nro>412</nro>"
    "<xBairro>Liberdade</xBairro><cMun>3550308</cMun><xMun>Sao Paulo</xMun><UF>SP</UF>"
    "<CEP>01506000</CEP></enderEmit></emit></infNFe></NFe>"
)


async def _nota_produto(db: AsyncSession, cnpj: str, nome: str, quando: datetime) -> None:
    from app.models.nf import NfNota

    db.add(
        NfNota(
            chave=f"35{uuid.uuid4().int % 10**42:042d}",
            numero=str(uuid.uuid4().int % 10000),
            emitente_cnpj=cnpj,
            emitente_nome=nome,
            data_emissao=quando,
            valor=Decimal("100.00"),
            xml=_EMIT_XML.format(cnpj=cnpj, nome=nome).encode(),
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_contas_bling_de_nf_viram_tomadores_sozinhas(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    agora = datetime.now(UTC)
    conta = "66829674000101"  # COMERCIAL DL
    await _nota_produto(db, conta, "COMERCIAL DL", agora - timedelta(days=3))
    await _nota_produto(db, conta, "COMERCIAL DL", agora - timedelta(days=1))
    await _nota_produto(db, CNPJ_PREST, "EMPRESA TESTE LTDA", agora)  # do grupo: prestadora
    await _nota_produto(
        db, "04252011000110", "CONTA VELHA", agora - timedelta(days=200)
    )  # fora da janela

    r = await client.get("/api/nfse/tomadores")
    assert r.status_code == 200, r.text
    por_doc = {t["documento"]: t for t in r.json() if t["tipo"] == "externo"}
    assert set(por_doc) == {conta}
    t = por_doc[conta]
    assert t["nome"] == "COMERCIAL DL" and t["conta_bling"]["notas_90d"] == 2
    assert (t["logradouro"], t["numero"], t["bairro"], t["cep"], t["cmun_ibge"]) == (
        "Rua Galvao Bueno",
        "412",
        "Liberdade",
        "01506000",
        "3550308",
    )

    # Alguém completou o e-mail à mão; a conta mudou o nome na NFe mais nova.
    r = await client.patch(
        f"/api/nfse/tomadores/{t['id']}",
        json={
            **{
                k: t[k]
                for k in (
                    "tipo",
                    "company_id",
                    "documento",
                    "nome",
                    "fone",
                    "cep",
                    "cmun_ibge",
                    "logradouro",
                    "numero",
                    "complemento",
                    "bairro",
                    "ativo",
                )
            },
            "email": "fin@dl.com.br",
        },
    )
    assert r.status_code == 200, r.text
    await _nota_produto(db, conta, "COMERCIAL DL LTDA", agora)
    r = await client.get("/api/nfse/tomadores")
    t2 = next(x for x in r.json() if x["documento"] == conta)
    assert t2["id"] == t["id"] and t2["nome"] == "COMERCIAL DL LTDA"
    assert t2["email"] == "fin@dl.com.br"  # o digitado fica
    assert len([x for x in r.json() if x["documento"] == conta]) == 1  # nunca duplica


def test_tomador_no_estilo_da_nfeio_e_endereco_do_xml():
    from app.services.nfse import contas_bling

    end = contas_bling.endereco_do_emitente(_EMIT_XML.format(cnpj="1", nome="X").encode())
    assert end["cep"] == "01506000" and end["cmun_ibge"] == "3550308"
    assert contas_bling.endereco_do_emitente(b"<nada/>") == {}
    assert contas_bling.endereco_do_emitente(b"nao e xml") == {}


# --- e-mail: nada sai sozinho; o envio manual é do DaVinci (30/09) -----------------------
# Eduardo (30/09): o tomador só recebe a nota quando alguém manda. O e-mail do
# tomador não vai mais à NFE.io (com ele a NFE.io avisa sozinha na emissão e no
# cancelamento) e o "Enviar por e-mail" sai pelo Mailjet do DaVinci. Com
# assert_all_mocked, qualquer PUT /sendemail derrubaria o teste.

PDF_FALSO = b"%PDF-1.4 nota falsa"
XML_GUARDADO = b"<Nfse><Numero>2</Numero></Nfse>"


class _Carteiro:
    """No lugar do Mailjet: guarda o que seria enviado (ou falha como ele)."""

    name = "falso"

    def __init__(self, falha: Exception | None = None):
        self.enviados: list[dict] = []
        self.falha = falha

    async def send(self, **kw) -> None:
        if self.falha is not None:
            raise self.falha
        self.enviados.append(kw)


@pytest.fixture
def carteiro(monkeypatch) -> _Carteiro:
    from app.services import email as email_svc

    c = _Carteiro()
    monkeypatch.setattr(email_svc, "get_email_sender", lambda: c)
    return c


async def _nota_no_banco(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    status: str = "emitida",
    n_nfse: str | None = "2",
    nfeio_id: str | None = NOTA_ID,
    xml: bytes | None = XML_GUARDADO,
    ambiente: str = "Development",
    cid: str | None = CID,
    tomador_email: str | None = None,
    tomador_nome: str = "TOMADOR LTDA",
    competencia: date = date(2026, 9, 1),
) -> NfseEmissao:
    """Nota já emitida (sem passar pela NFE.io), com o tomador de fora."""
    import base64

    from app.models.nfse import NfseTomador

    t = NfseTomador(tipo="externo", documento=CNPJ_TOMA, nome=tomador_nome, email=tomador_email)
    db.add(t)
    await db.flush()
    prestador = {"nome": "EMPRESA TESTE LTDA", "cnpj": CNPJ_PREST}
    if cid:
        prestador["nfeio_company_id"] = cid
    e = NfseEmissao(
        company_id=company_id,
        tomador_id=t.id,
        competencia=competencia,
        status=status,
        descricao="Intermediação 09/2026",
        valor_servico=Decimal("1500.00"),
        nfeio_id=nfeio_id,
        nfeio_ambiente=ambiente,
        n_nfse=n_nfse,
        snapshot={
            "prestador": prestador,
            "tomador": {"nome": tomador_nome, "documento": CNPJ_TOMA, "tipo": "externo"},
        },
        nfse_xml_b64=base64.b64encode(xml).decode() if xml else None,
    )
    db.add(e)
    await db.commit()
    return e


def _rota_pdf(api: respx.MockRouter, nota_id: str = NOTA_ID, cid: str = CID) -> respx.Route:
    return api.get(
        path=f"/v3/companies/{cid}/serviceinvoices/{nota_id}/pdf", host="api.nfe.io"
    ).mock(
        return_value=httpx.Response(
            200, content=PDF_FALSO, headers={"content-type": "application/pdf"}
        )
    )


@pytest.mark.asyncio
async def test_email_do_tomador_nunca_vai_para_a_nfeio(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    """Tomador COM e-mail: nem a prévia, nem o POST, nem o reenvio levam o
    e-mail — senão a NFE.io manda a nota sozinha ao tomador."""
    auth_as(operador)
    r = await client.post(
        "/api/nfse/tomadores",
        json={
            "tipo": "externo",
            "documento": CNPJ_TOMA,
            "nome": "TOMADOR LTDA",
            "email": "fin@tomador.com.br",
        },
    )
    assert r.status_code == 201, r.text
    item = {
        "company_id": str(cenario["prest"]),
        "tomador_id": r.json()["id"],
        "descricao": "Intermediação",
        "valor": "100.00",
    }
    enviados: list[dict] = []

    def _captura(status: int):
        def _f(request: httpx.Request) -> httpx.Response:
            enviados.append(json.loads(request.content))
            if status == 400:
                return httpx.Response(400, json={"message": "dados inválidos"})
            return _aceita(request)

        return _f

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        prev = (
            await client.post(
                "/api/nfse/previa", json={"competencia": "2026-09-01", "itens": [item]}
            )
        ).json()["itens"][0]
        assert prev["problemas"] == [], prev
        assert "email" not in prev["payload"]["borrower"]
        rotas["post"].mock(side_effect=_captura(400))
        e = (
            await client.post("/api/nfse/emitir", json={"competencia": "2026-09-01", "item": item})
        ).json()
        assert e["status"] == "rejeitada"
        # O reenvio remonta o tomador: também sem e-mail.
        rotas["external"].mock(return_value=httpx.Response(200, json={"serviceInvoices": []}))
        rotas["post"].mock(side_effect=_captura(202))
        r = await client.post(f"/api/nfse/emissoes/{e['id']}/reenviar")
        assert r.status_code == 200, r.text
    assert len(enviados) == 2
    for corpo in enviados:
        assert "email" not in corpo["borrower"], corpo["borrower"]
        assert "fin@tomador.com.br" not in json.dumps(corpo)


@pytest.mark.asyncio
async def test_email_mal_digitado_no_tomador_nao_trava_a_emissao(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    r = await client.post(
        "/api/nfse/tomadores",
        json={"tipo": "externo", "documento": CNPJ_TOMA, "nome": "TOMADOR LTDA", "email": "fin"},
    )
    item = {
        "company_id": str(cenario["prest"]),
        "tomador_id": r.json()["id"],
        "descricao": "Intermediação",
        "valor": "100.00",
    }
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _rotas(api)
        prev = (
            await client.post(
                "/api/nfse/previa", json={"competencia": "2026-09-01", "itens": [item]}
            )
        ).json()["itens"][0]
    assert prev["problemas"] == [] and prev["payload"] is not None
    assert not any("e-mail" in p for p in prev["problemas"])


@pytest.mark.asyncio
async def test_enviar_email_manual_anexa_pdf_e_xml(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    carteiro: _Carteiro,
):
    auth_as(operador)
    e = await _nota_no_banco(db, cenario["prest"], tomador_email="fin@tomador.com.br")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rotas = _rotas(api)
        pdf = _rota_pdf(api)
        r = await client.post(
            f"/api/nfse/emissoes/{e.id}/enviar-email",
            json={"para": ["fin@tomador.com.br", " Outro@Tomador.com.br "]},
        )
        assert r.status_code == 200, r.text
        assert pdf.call_count == 1
        assert not rotas["xml"].called  # o XML sai do banco
    assert r.json() == {"ok": True, "para": ["fin@tomador.com.br", "Outro@tomador.com.br"]}
    assert [m["to"] for m in carteiro.enviados] == ["fin@tomador.com.br", "Outro@tomador.com.br"]
    m = carteiro.enviados[0]
    # Empresa em TESTE na NFE.io: o assunto avisa (nota sem valor fiscal).
    assert m["subject"].startswith("[TESTE, sem valor fiscal] NFS-e nº 2")
    assert "EMPRESA TESTE LTDA" in m["subject"] and "09/2026" in m["subject"]
    assert m["from_name"] == "EMPRESA TESTE LTDA"
    assert [(nome, mime) for nome, mime, _ in m["attachments"]] == [
        ("NFSe_teste_2.pdf", "application/pdf"),
        ("NFSe_teste_2.xml", "application/xml"),
    ]
    assert m["attachments"][0][2] == PDF_FALSO and m["attachments"][1][2] == XML_GUARDADO
    assert "sem valor fiscal" in m["text"] and "R$ 1.500,00" in m["text"]
    # Nenhuma ida à NFE.io virou "enviar_email"; só o PDF ficou registrado.
    ops = [c.operacao for c in (await db.execute(select(NfseChamada))).scalars()]
    assert ops == ["pdf"]


@pytest.mark.asyncio
async def test_enviar_email_guarda_no_tomador_so_quando_pedido(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    carteiro: _Carteiro,
):
    from app.models.nfse import NfseTomador

    auth_as(operador)
    e = await _nota_no_banco(db, cenario["prest"])  # tomador sem e-mail
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _rotas(api)
        _rota_pdf(api)
        r = await client.post(
            f"/api/nfse/emissoes/{e.id}/enviar-email", json={"para": ["a@tomador.com.br"]}
        )
        assert r.status_code == 200, r.text
        t = await db.get(NfseTomador, e.tomador_id)
        await db.refresh(t)
        assert t.email is None  # não pediu pra guardar
        r = await client.post(
            f"/api/nfse/emissoes/{e.id}/enviar-email",
            json={"para": ["a@tomador.com.br", "b@tomador.com.br"], "salvar_no_tomador": True},
        )
        assert r.status_code == 200, r.text
    await db.refresh(t)
    assert t.email == "a@tomador.com.br, b@tomador.com.br"
    assert len(carteiro.enviados) == 3


@pytest.mark.asyncio
async def test_enviar_email_valida_os_enderecos(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    carteiro: _Carteiro,
):
    auth_as(operador)
    e = await _nota_no_banco(db, cenario["prest"])
    url = f"/api/nfse/emissoes/{e.id}/enviar-email"
    for para in ([], [" "], ["nao-e-email"], [f"x{i}@tomador.com.br" for i in range(6)]):
        r = await client.post(url, json={"para": para})
        assert r.status_code == 422, (para, r.text)
    assert (await client.post(url, json={})).status_code == 422
    assert carteiro.enviados == []


@pytest.mark.asyncio
async def test_enviar_email_so_de_nota_emitida(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    carteiro: _Carteiro,
):
    auth_as(operador)
    for i, status in enumerate(("processando", "rejeitada", "cancelada", "cancelando")):
        e = await _nota_no_banco(db, cenario["prest"], status=status, nfeio_id=f"{i:024x}")
        r = await client.post(
            f"/api/nfse/emissoes/{e.id}/enviar-email", json={"para": ["a@tomador.com.br"]}
        )
        assert r.status_code == 409, (status, r.text)
        assert r.json()["detail"]["code"] == "sem_nota"
    sem_id = await _nota_no_banco(db, cenario["prest"], nfeio_id=None)
    r = await client.post(
        f"/api/nfse/emissoes/{sem_id.id}/enviar-email", json={"para": ["a@tomador.com.br"]}
    )
    assert r.status_code == 409
    assert carteiro.enviados == []


@pytest.mark.asyncio
async def test_enviar_email_falha_do_mailjet_vira_502(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    carteiro: _Carteiro,
):
    auth_as(operador)
    e = await _nota_no_banco(db, cenario["prest"])
    pedido = httpx.Request("POST", "https://api.mailjet.com/v3.1/send")
    carteiro.falha = httpx.HTTPStatusError(
        "401", request=pedido, response=httpx.Response(401, request=pedido)
    )
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _rotas(api)
        _rota_pdf(api)
        r = await client.post(
            f"/api/nfse/emissoes/{e.id}/enviar-email",
            json={"para": ["a@tomador.com.br"], "salvar_no_tomador": True},
        )
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "email_falhou"
    assert "a@tomador.com.br" in r.json()["detail"]["mensagem"]
    from app.models.nfse import NfseTomador

    t = await db.get(NfseTomador, e.tomador_id)
    await db.refresh(t)
    assert t.email is None  # não saiu: não guarda


@pytest.mark.asyncio
async def test_enviar_email_responde_para_a_empresa_so_com_email_valido(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    carteiro: _Carteiro,
):
    """O e-mail da empresa não é conferido no cadastro: torto, o Mailjet recusaria
    o envio inteiro. Sai sem o "responder para"; com vários, vale o 1º."""
    auth_as(operador)
    e = await _nota_no_banco(db, cenario["prest"])
    url = f"/api/nfse/emissoes/{e.id}/enviar-email"
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _rotas(api)
        _rota_pdf(api)
        for email, esperado in (
            ("financeiro", None),
            (
                "Fin@Empresa.com.br; outro@empresa.com.br",
                ("Fin@empresa.com.br", "EMPRESA TESTE LTDA"),
            ),
            (None, None),
        ):
            await _fiscal(db, cenario, email=email)
            r = await client.post(url, json={"para": ["a@tomador.com.br"]})
            assert r.status_code == 200, (email, r.text)
            assert carteiro.enviados[-1]["reply_to"] == esperado, email


@pytest.mark.asyncio
async def test_enviar_email_nfeio_lenta_nao_segura_a_tela(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    carteiro: _Carteiro,
    monkeypatch,
):
    """O PDF do anexo tem prazo (como no lote): passou, 502 "a NFE.io não
    respondeu" e nada é enviado — em vez de minutos com a tela esperando."""
    import asyncio

    auth_as(operador)
    monkeypatch.setattr(svc, "PRAZO_ANEXO_S", 0.05)
    e = await _nota_no_banco(db, cenario["prest"])

    async def _trava(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, content=PDF_FALSO)

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _rotas(api)
        api.get(path=f"/v3/companies/{CID}/serviceinvoices/{NOTA_ID}/pdf", host="api.nfe.io").mock(
            side_effect=_trava
        )
        r = await client.post(
            f"/api/nfse/emissoes/{e.id}/enviar-email", json={"para": ["a@tomador.com.br"]}
        )
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "nfeio_sem_resposta"
    assert carteiro.enviados == []
    [chamada] = (await db.execute(select(NfseChamada))).scalars().all()
    assert chamada.operacao == "pdf" and "prazo" in (chamada.erro or "")


@pytest.mark.asyncio
async def test_enviar_email_sem_mailjet_fora_do_localhost_503(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """Sem as chaves do Mailjet o "envio" é só uma linha no log: em produção a
    tela diria "enviado" sem ter enviado nada. Recusa antes de ir à NFE.io."""
    from app.services import email as email_svc

    auth_as(operador)
    e = await _nota_no_banco(db, cenario["prest"])
    monkeypatch.setattr(email_svc, "get_email_sender", lambda: email_svc.ConsoleEmailSender())
    monkeypatch.setattr(get_settings(), "env", "production")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _rotas(api)
        pdf = _rota_pdf(api)
        r = await client.post(
            f"/api/nfse/emissoes/{e.id}/enviar-email", json={"para": ["a@tomador.com.br"]}
        )
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "email_nao_configurado"
        assert not pdf.called
        # No localhost (development) o Console vale: só registra no log.
        monkeypatch.setattr(get_settings(), "env", "development")
        r = await client.post(
            f"/api/nfse/emissoes/{e.id}/enviar-email", json={"para": ["a@tomador.com.br"]}
        )
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_enviar_email_exige_poder_editar(
    client: AsyncClient,
    db: AsyncSession,
    make_user,
    cenario: dict,
    auth_as: Callable[[User | None], None],
    carteiro: _Carteiro,
):
    e = await _nota_no_banco(db, cenario["prest"])
    auth_as(await make_user(permissions={"emissao_servico": {"view": True}}))
    r = await client.post(
        f"/api/nfse/emissoes/{e.id}/enviar-email", json={"para": ["a@tomador.com.br"]}
    )
    assert r.status_code == 403
    assert carteiro.enviados == []


def test_cliente_da_nfeio_nao_tem_mais_o_sendemail():
    """O PUT /sendemail manda para o e-mail gravado na nota (que não vai mais)."""
    assert not hasattr(nfeio.ClienteNfeio, "enviar_email")
