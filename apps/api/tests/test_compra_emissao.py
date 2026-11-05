"""Contabilidade › Compra — emissão automática pelo robô do Bling 44 (04/11/2026).

"Emitir" põe na fila; o robô do Mac pega uma por vez (lease), grava até onde
chegou (etapa) e devolve o XML, que o servidor confere de novo antes de marcar
`nf_emitida`. Da etapa `import_tentado` em diante o número está QUEIMADO (o pedido
pode já estar no Bling): nada de editar, reabrir, excluir, planilha ou emitir de novo.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compra import ETAPAS, CompraPedido, CompraPedidoItem, CompraVendedora, queimado
from app.models.nf import NfNota
from app.schemas.compra import Etapa
from app.services import compra as svc
from app.services import compra_emissao as emissao
from tests.test_nfse_compras import (
    BASE,
    CNPJ_BEZERRA,
    CNPJ_TELES,
    CNPJ_VITA,
    _compra,
    cenario,
    operador,
)

# Fixtures importadas do teste da aba (VITA, BEZERRA automática, TELES manual):
# o pytest acha pelo nome do parâmetro.
_FIXTURES = (cenario, operador)

AGENTE = "/api/nf-cadastro/agent/compras"
_TOKEN = "tok-compra-teste"  # noqa: S105
H = {"X-Agent-Token": _TOKEN}
SERIAL = "k13ksbpr"
CHAVE = "35261167285941000190550010000030311000030318"


@pytest.fixture(autouse=True)
def _robo(monkeypatch):
    """Token do agente ligado e o relógio dentro da janela (10:00 de São Paulo)."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    monkeypatch.setattr(emissao, "agora_sp", lambda: datetime(2026, 11, 4, 10, 0, tzinfo=svc.SP))


def _xml(**o) -> bytes:
    """nfeProc autorizado da compra padrão (450 × a001 a R$ 9,30 da BEZERRA para a VITA)."""
    v = {
        "chave": CHAVE,
        "nnf": "3031",
        "emit": CNPJ_BEZERRA,
        "dest": CNPJ_VITA,
        "ie": "125632321110",
        "cprod": "A001",
        "ncm": "39232110",
        "cfop": "5102",
        "qcom": "450.0000",
        "vun": "9.3000000000",
        "vnf": "4185.00",
        "digest": "q1w2e3r4t5y6u7i8o9p0asdfghj=",
        "digval": "q1w2e3r4t5y6u7i8o9p0asdfghj=",
        "cstat": "100",
        **o,
    }
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe>'
        f'<infNFe Id="NFe{v["chave"]}" versao="4.00">'
        f"<ide><serie>1</serie><nNF>{v['nnf']}</nNF>"
        "<dhEmi>2026-11-04T10:30:00-03:00</dhEmi></ide>"
        f"<emit><CNPJ>{v['emit']}</CNPJ><xNome>COMERCIAL BEZERRA</xNome></emit>"
        f"<dest><CNPJ>{v['dest']}</CNPJ><xNome>VITA ENXOVAIS LTDA</xNome>"
        f"<indIEDest>9</indIEDest><IE>{v['ie']}</IE></dest>"
        f'<det nItem="1"><prod><cProd>{v["cprod"]}</cProd><xProd>Embalagem</xProd>'
        f"<NCM>{v['ncm']}</NCM><CFOP>{v['cfop']}</CFOP><uCom>UN</uCom>"
        f"<qCom>{v['qcom']}</qCom><vUnCom>{v['vun']}</vUnCom></prod></det>"
        f"<total><ICMSTot><vNF>{v['vnf']}</vNF></ICMSTot></total></infNFe>"
        '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo><Reference>'
        f"<DigestValue>{v['digest']}</DigestValue></Reference></SignedInfo></Signature>"
        f"</NFe><protNFe><infProt><chNFe>{v['chave']}</chNFe><nProt>135260000000001</nProt>"
        f"<digVal>{v['digval']}</digVal><cStat>{v['cstat']}</cStat>"
        "<xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe></nfeProc>"
    ).encode()


def _ok(**o) -> dict:
    corpo = {
        "status": "nf_emitida",
        "nf_numero": "3031",
        "nf_chave": CHAVE,
        "xml_b64": base64.b64encode(_xml()).decode(),
    }
    corpo.update(o)
    return corpo


async def _ler(db: AsyncSession, cid: str) -> CompraPedido:
    # recarrega só a compra (expirar a sessão toda derrubaria o `operador` do auth_as)
    return await db.get(CompraPedido, uuid.UUID(cid), populate_existing=True)


async def _registrar(client: AsyncClient, cen: dict, **kw) -> dict:
    r = await client.post(BASE, json=_compra(cen, **kw))
    assert r.status_code == 201, r.text
    return r.json()


async def _lease(client: AsyncClient, serial: str = SERIAL) -> dict:
    r = await client.post(f"{AGENTE}/lease", headers=H, json={"serial": serial})
    assert r.status_code == 200, r.text
    return r.json()


async def _com_o_robo(client: AsyncClient, cen: dict, **kw) -> tuple[dict, dict]:
    """Compra registrada → na fila → com o robô (etapa lease)."""
    c = await _registrar(client, cen, **kw)
    assert (await client.post(f"{BASE}/{c['id']}/emitir")).status_code == 200
    r = await _lease(client)
    assert [x["id"] for x in r["compras"]] == [c["id"]], r
    return c, r["compras"][0]


async def _etapa(client: AsyncClient, cid: str, etapa: str, **kw) -> int:
    r = await client.post(f"{AGENTE}/{cid}/etapa", headers=H, json={"etapa": etapa, **kw})
    return r.status_code


def _code(r) -> str | None:
    d = r.json().get("detail")
    return d.get("code") if isinstance(d, dict) else None


# --- regras puras ------------------------------------------------------------------------


def test_etapas_iguais_no_modelo_e_no_schema():
    assert get_args(Etapa) == ETAPAS
    assert not queimado(None) and not queimado("lease")
    assert all(queimado(e) for e in ETAPAS[1:])


def test_janela_do_lease():
    sp = svc.SP
    assert not emissao.janela_aberta(datetime(2026, 11, 4, 7, 59, tzinfo=sp))
    assert emissao.janela_aberta(datetime(2026, 11, 4, 8, 0, tzinfo=sp))
    assert emissao.janela_aberta(datetime(2026, 11, 4, 19, 29, tzinfo=sp))
    assert not emissao.janela_aberta(datetime(2026, 11, 4, 19, 30, tzinfo=sp))
    # 22:30 UTC = 19:30 em São Paulo (fecha); 13:00 UTC = 10:00 (abre)
    assert not emissao.janela_aberta(datetime(2026, 11, 4, 22, 30, tzinfo=UTC))
    assert emissao.janela_aberta(datetime(2026, 11, 4, 13, 0, tzinfo=UTC))


def _pedido(**o) -> tuple[CompraPedido, list[CompraPedidoItem]]:
    p = CompraPedido(
        id=uuid.uuid4(),
        numero=8_000_001,
        vendedor_cnpj=CNPJ_BEZERRA,
        vendedor_nome="COMERCIAL BEZERRA",
        vendedor_uf="SP",
        modo="quantidade",
        percentual=None,
        faturamento=Decimal("113354.78"),
        cfop="5102",
        total=Decimal("4185.00"),
        comprador={
            "cnpj": CNPJ_VITA,
            "inscricao_estadual": "125632321110",
            "endereco": {"uf": "SP"},
        },
    )
    it = CompraPedidoItem(
        sku="a001", ncm="39232110", quantidade=450, valor_unitario=Decimal("9.30")
    )
    for k, v in o.items():
        setattr(p, k, v)
    return p, [it]


def _bezerra(**o) -> CompraVendedora:
    v = CompraVendedora(
        cnpj=CNPJ_BEZERRA, nome="COMERCIAL BEZERRA", adspower_serial=SERIAL, automatico=True
    )
    v.ativo = True
    for k, val in o.items():
        setattr(v, k, val)
    return v


def test_problemas_para_emitir():
    p, itens = _pedido()
    assert emissao.problemas_para_emitir(p, itens, _bezerra()) == []
    assert len(emissao.problemas_para_emitir(p, itens, None)) == 1
    assert "automático" in emissao.problemas_para_emitir(p, itens, _bezerra(automatico=False))[0]
    assert emissao.problemas_para_emitir(p, itens, _bezerra(ativo=False))
    # v1: só o perfil do Bling 44 tem robô (outro serial ficaria na fila para sempre)
    assert (
        "ainda não tem robô"
        in emissao.problemas_para_emitir(p, itens, _bezerra(adspower_serial="outro"))[0]
    )
    assert "1 produto" in emissao.problemas_para_emitir(p, itens * 2, _bezerra())[0]
    p6, _ = _pedido(cfop="6102")
    assert "CFOP 6102" in emissao.problemas_para_emitir(p6, itens, _bezerra())[0]
    # UF diferente: o sugerido é 6102, e 6102 passa
    prj, _ = _pedido(cfop="6102", comprador={**p.comprador, "endereco": {"uf": "RJ"}})
    assert emissao.problemas_para_emitir(prj, itens, _bezerra()) == []
    for ie in (None, "", "ISENTO"):
        pie, _ = _pedido(comprador={**p.comprador, "inscricao_estadual": ie})
        assert "inscrição estadual" in emissao.problemas_para_emitir(pie, itens, _bezerra())[0]
    # percentual: 1% de 113.354,78 a 9,30 = 121; 122 passa do %
    ppct, _ = _pedido(modo="percentual", percentual=Decimal("1"))
    itens[0].quantidade = 121
    assert emissao.problemas_para_emitir(ppct, itens, _bezerra()) == []
    itens[0].quantidade = 122
    assert "cabem 121" in emissao.problemas_para_emitir(ppct, itens, _bezerra())[0]


def test_conferir_xml_bate_e_cada_campo_divergente():
    p, itens = _pedido()
    nota, erros = emissao.conferir_xml(_xml(), p, itens, nf_numero="3031", nf_chave=CHAVE)
    assert erros == [] and nota.chave == CHAVE and nota.numero == "3031"
    casos = {
        "cStat": {"cstat": "204"},
        "vNF": {"vnf": "4194.30"},
        "emitente": {"emit": CNPJ_TELES},
        "destinatário": {"dest": "11444777000161"},
        "IE do destinatário": {"ie": "999"},
        "item 1 cProd": {"cprod": "b001"},
        "item 1 NCM": {"ncm": "42021210"},
        "item 1 CFOP": {"cfop": "6102"},
        "item 1 qCom": {"qcom": "451.0000"},
        "item 1 vUnCom": {"vun": "9.31"},
        "DigestValue": {"digest": "outro="},
    }
    for rotulo, o in casos.items():
        _, erros = emissao.conferir_xml(_xml(**o), p, itens, nf_numero="3031", nf_chave=CHAVE)
        assert len(erros) == 1 and erros[0].startswith(rotulo), (rotulo, erros)
    _, erros = emissao.conferir_xml(_xml(), p, itens, nf_numero="3032", nf_chave=CHAVE)
    assert erros == ["nNF 3031≠3032"]
    _, erros = emissao.conferir_xml(_xml(), p, itens, nf_numero="3031", nf_chave="1" * 44)
    assert erros[0].startswith("chave")
    sem_digval = _xml().replace(b"<digVal>q1w2e3r4t5y6u7i8o9p0asdfghj=</digVal>", b"")
    _, erros = emissao.conferir_xml(sem_digval, p, itens, nf_numero="3031", nf_chave=CHAVE)
    assert erros == ["XML sem DigestValue ou sem digVal (não é o XML autorizado)"]
    _, erros = emissao.conferir_xml(_xml(), p, itens * 2, nf_numero="3031", nf_chave=CHAVE)
    assert erros == ["itens 1≠2"]


# --- tela: emitir / tirar da fila ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_emitir_caminho_feliz_e_idempotente(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    c = await _registrar(client, cenario)
    r = await client.post(f"{BASE}/{c['id']}/emitir")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "na_fila" and r.json()["emissao_etapa"] is None
    pedida = r.json()["emissao_pedida_em"]
    assert pedida and r.json()["numero_queimado"] is False
    # clicar de novo (outra aba) não muda nada
    r = await client.post(f"{BASE}/{c['id']}/emitir", json={})
    assert r.status_code == 200 and r.json()["emissao_pedida_em"] == pedida
    # na fila: sem planilha, sem editar, sem anotar NF à mão, sem excluir
    assert (await client.get(f"{BASE}/{c['id']}/planilha")).status_code == 409
    assert (await client.put(f"{BASE}/{c['id']}", json=_compra(cenario))).status_code == 409
    assert (await client.post(f"{BASE}/{c['id']}/nf", json={"nf_numero": "1"})).status_code == 409
    assert (await client.delete(f"{BASE}/{c['id']}")).status_code == 409
    # tirar da fila volta para registrada
    r = await client.post(f"{BASE}/{c['id']}/tirar-da-fila")
    assert r.status_code == 200 and r.json()["status"] == "registrada"
    assert r.json()["emissao_pedida_em"] is None
    assert (await client.post(f"{BASE}/{c['id']}/tirar-da-fila")).status_code == 409
    # com o robô, tirar da fila não vale
    assert (await client.post(f"{BASE}/{c['id']}/emitir")).status_code == 200
    assert (await _lease(client))["compras"][0]["id"] == c["id"]
    r = await client.post(f"{BASE}/{c['id']}/tirar-da-fila")
    assert r.status_code == 409 and _code(r) == "robo_ja_pegou"
    assert (await client.post(f"{BASE}/{c['id']}/emitir")).json()["status"] == "emitindo"
    r = await client.post(f"{BASE}/{c['id']}/cancelar")
    assert r.status_code == 409 and _code(r) == "emitindo"


@pytest.mark.asyncio
async def test_emitir_recusa_o_que_o_robo_nao_resolve(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)

    async def recusa(compra: dict) -> list[str]:
        r = await client.post(f"{BASE}/{compra['id']}/emitir")
        assert r.status_code == 422 and _code(r) == "nao_emite_automatico", r.text
        return r.json()["detail"]["problemas"]

    teles = await _registrar(client, cenario, vendedor_cnpj=CNPJ_TELES)
    assert "não emite automático" in (await recusa(teles))[0]
    corpo = _compra(cenario)
    corpo["itens"].append(
        {"nome": "Fita", "sku": "f01", "ncm": "39191000", "quantidade": 3, "valor_unitario": "1"}
    )
    r = await client.post(BASE, json=corpo)
    assert "1 produto" in (await recusa(r.json()))[0]
    isento = await _registrar(client, cenario, inscricao_estadual="isento")
    assert "inscrição estadual" in (await recusa(isento))[0]
    end = {
        "logradouro": "Rua B",
        "numero": "5",
        "bairro": "Centro",
        "cep": "30110000",
        "municipio": "Belo Horizonte",
        "uf": "MG",
    }
    sem_ie = await _registrar(client, cenario, company_id=str(cenario["sem"]), endereco=end)
    assert sem_ie["comprador"]["inscricao_estadual"] is None and sem_ie["cfop"] == "6102"
    assert len(await recusa(sem_ie)) == 1
    cfop = await _registrar(client, cenario, cfop="6102")
    assert "CFOP 6102" in (await recusa(cfop))[0]
    # 1% de 15.050,50 a 9,30 = 16 unidades; 17 passa do %
    corpo = _compra(cenario, modo="percentual", percentual="1")
    corpo["itens"][0]["quantidade"] = 17
    r = await client.post(BASE, json=corpo)
    assert "cabem 16" in (await recusa(r.json()))[0]
    # sem nenhuma NF da vendedora no DaVinci o robô não confere o Bling (e recusaria)
    sem_nota = await _registrar(client, cenario)
    await db.execute(delete(NfNota).where(NfNota.emitente_cnpj == CNPJ_BEZERRA))
    await db.commit()
    assert "nenhuma NF de COMERCIAL BEZERRA" in (await recusa(sem_nota))[0]
    r = await client.get(f"{AGENTE}/previa", params={"numero": sem_nota["numero"]}, headers=H)
    assert "nenhuma NF" in r.json()["problemas"][0]
    # com NF anotada não vai para o robô
    nf = await _registrar(client, cenario)
    await client.post(f"{BASE}/{nf['id']}/nf", json={"nf_numero": "77"})
    r = await client.post(f"{BASE}/{nf['id']}/emitir")
    assert r.status_code == 409 and _code(r) == "nao_emite"


@pytest.mark.asyncio
async def test_emitir_pede_confirmacao_planilha_baixada_e_outra_no_mes(
    client: AsyncClient, operador, auth_as, cenario
):
    auth_as(operador)
    a = await _registrar(client, cenario)
    assert (await client.get(f"{BASE}/{a['id']}/planilha")).status_code == 200
    assert (await client.get(f"{BASE}/{a['id']}")).json()["planilha_baixada_em"]
    r = await client.post(f"{BASE}/{a['id']}/emitir")
    assert r.status_code == 409 and _code(r) == "planilha_ja_baixada"
    r = await client.post(
        f"{BASE}/{a['id']}/emitir", json={"confirmo_planilha_nao_importada": True}
    )
    assert r.status_code == 200 and r.json()["status"] == "na_fila"

    b = await _registrar(client, cenario)
    r = await client.post(f"{BASE}/{b['id']}/emitir")
    assert r.status_code == 409 and _code(r) == "outra_no_mes"
    assert r.json()["detail"]["numeros"] == [a["numero"]]
    r = await client.post(f"{BASE}/{b['id']}/emitir", json={"confirmo_outra_no_mes": True})
    assert r.status_code == 200 and r.json()["status"] == "na_fila"
    # outro mês não conta
    c = await _registrar(client, cenario, competencia="2026-08-01")
    assert (await client.post(f"{BASE}/{c['id']}/emitir")).status_code == 200
    # a planilha de várias também recusa as que estão na fila
    r = await client.post(f"{BASE}/planilha", json={"ids": [a["id"]]})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_outra_no_mes_conta_a_que_parou_depois_do_import(
    client: AsyncClient, operador, auth_as, cenario
):
    """Parou em erro depois do import (ex.: sefaz_tentado): a NF pode ter saído no
    Bling 44 — outra compra do mesmo mês pede confirmação. Erro de antes do import não."""
    auth_as(operador)

    async def erro(cid: str, etapa: str) -> None:
        corpo = {"status": "erro", "retentavel": False, "etapa": etapa, "motivo": "x"}
        r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=corpo)
        assert r.json()["status"] == "erro", r.text

    a, _ = await _com_o_robo(client, cenario)
    for e in ("import_tentado", "importado", "nf_gerada", "pronta_sefaz", "sefaz_tentado"):
        assert await _etapa(client, a["id"], e) == 200
    await erro(a["id"], "sefaz_tentado")
    b = await _registrar(client, cenario)
    r = await client.post(f"{BASE}/{b['id']}/emitir")
    assert r.status_code == 409 and _code(r) == "outra_no_mes"
    assert r.json()["detail"]["numeros"] == [a["numero"]]

    c, _ = await _com_o_robo(client, cenario, competencia="2026-08-01")
    await erro(c["id"], "lease")
    d = await _registrar(client, cenario, competencia="2026-08-01")
    r = await client.post(f"{BASE}/{d['id']}/emitir")
    assert r.status_code == 200 and r.json()["status"] == "na_fila"


@pytest.mark.asyncio
async def test_baixar_a_planilha_fica_no_historico(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    """O GET da planilha carimba `planilha_baixada_em` (o Emitir pergunta de novo): é
    GET que grava, então o Histórico registra quem baixou."""
    from app.config import get_settings
    from app.main import app
    from app.models import HistoricoEvento, UserStatus
    from app.security.jwt import issue_session_token
    from app.security.senha_extra import require_nfse_unlock

    auth_as(operador)
    c = await _registrar(client, cenario)
    # login de verdade (cookie): o Histórico só grava com a pessoa identificada; a senha
    # extra continua liberada como no resto do arquivo
    trava = app.dependency_overrides.get(require_nfse_unlock)
    auth_as(None)
    if trava is not None:
        app.dependency_overrides[require_nfse_unlock] = trava
    operador.status = UserStatus.ACTIVE
    await db.commit()
    token, _, _ = issue_session_token(sub=operador.open_id, role=operador.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    r = await client.get(f"{BASE}/{c['id']}/planilha")
    assert r.status_code == 200, r.text
    ev = (
        (
            await db.execute(
                select(HistoricoEvento).where(
                    HistoricoEvento.metodo == "GET",
                    HistoricoEvento.caminho == f"{BASE}/{c['id']}/planilha",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(ev) == 1 and ev[0].acao == "baixou a planilha da compra", ev
    assert ev[0].n_alteracoes >= 1


# --- robô: token, lease, fila, prévia ---------------------------------------------------------


@pytest.mark.asyncio
async def test_agente_exige_token_e_nao_a_senha_extra(
    client: AsyncClient, operador, auth_as, cenario
):
    from app.main import app
    from app.security.senha_extra import require_nfse_unlock

    auth_as(operador)
    c = await _registrar(client, cenario)
    for h in ({}, {"X-Agent-Token": "errado"}):
        r = await client.get(f"{AGENTE}/fila", params={"serial": SERIAL}, headers=h)
        assert r.status_code == 401 and _code(r) == "nf_agent_unauthorized"
        r = await client.post(f"{AGENTE}/lease", headers=h, json={"serial": SERIAL})
        assert r.status_code == 401
        r = await client.post(f"{AGENTE}/{c['id']}/etapa", headers=h, json={"etapa": "lease"})
        assert r.status_code == 401
    desvio = app.dependency_overrides.pop(require_nfse_unlock, None)
    try:
        assert (await client.get(f"{BASE}/{c['id']}")).status_code == 401  # a tela tem trava
        r = await client.get(f"{AGENTE}/fila", params={"serial": SERIAL}, headers=H)
        assert r.status_code == 200 and r.json() == {"na_fila": 0, "emitindo": []}
        r = await client.get(f"{AGENTE}/previa", params={"numero": c["numero"]}, headers=H)
        assert r.status_code == 200
    finally:
        if desvio is not None:
            app.dependency_overrides[require_nfse_unlock] = desvio


@pytest.mark.asyncio
async def test_lease_serial_horario_e_uma_por_vez(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario, monkeypatch
):
    auth_as(operador)
    a = await _registrar(client, cenario)
    b = await _registrar(client, cenario, competencia="2026-08-01")
    for c in (a, b):
        assert (await client.post(f"{BASE}/{c['id']}/emitir")).status_code == 200
    r = await client.get(f"{AGENTE}/fila", params={"serial": SERIAL}, headers=H)
    assert r.json() == {"na_fila": 2, "emitindo": []}
    # serial de outro perfil: nada
    assert await _lease(client, "outro") == {
        "compras": [],
        "ocupada": None,
        "motivo": "sem_vendedora",
    }
    # fora do horário: nada (o lote das 22:30 usa o mesmo perfil)
    monkeypatch.setattr(emissao, "agora_sp", lambda: datetime(2026, 11, 4, 20, 0, tzinfo=svc.SP))
    assert (await _lease(client))["motivo"] == "fora_do_horario"
    assert (await _ler(db, a["id"])).status == "na_fila"
    monkeypatch.setattr(emissao, "agora_sp", lambda: datetime(2026, 11, 4, 10, 0, tzinfo=svc.SP))
    # a mais antiga na fila primeiro
    r = await _lease(client)
    assert [x["numero"] for x in r["compras"]] == [a["numero"]] and r["ocupada"] is None
    x = r["compras"][0]
    assert x["status"] == "emitindo" and x["etapa"] == "lease" and x["tentativas"] == 1
    # a resposta do lease se perdeu (a compra ainda está na etapa lease = nada no
    # Bling): entrega a MESMA de novo, sem gastar tentativa, e não a outra
    r = await _lease(client)
    assert [x["id"] for x in r["compras"]] == [a["id"]] and r["compras"][0]["tentativas"] == 1
    assert (await _ler(db, a["id"])).emissao_tentativas == 1
    assert (await _ler(db, b["id"])).status == "na_fila"
    # do import em diante, o perfil está ocupado: não entrega nada
    assert await _etapa(client, a["id"], "import_tentado") == 200
    r = await _lease(client)
    assert r["compras"] == [] and r["motivo"] == "ocupada"
    assert r["ocupada"]["id"] == a["id"] and r["ocupada"]["etapa"] == "import_tentado"
    assert (await _ler(db, b["id"])).status == "na_fila"
    r = await client.get(f"{AGENTE}/fila", params={"serial": SERIAL}, headers=H)
    assert r.json()["na_fila"] == 1 and [e["numero"] for e in r.json()["emitindo"]] == [a["numero"]]
    # vendedora desligada: o perfil para de receber, mas a que está com o robô continua
    # na /fila (sumir = o robô larga a NF pela metade achando que foi tirada dele)
    v = await db.get(CompraVendedora, CNPJ_BEZERRA)
    v.automatico = False
    await db.commit()
    assert (await _lease(client))["motivo"] == "sem_vendedora"
    r = await client.get(f"{AGENTE}/fila", params={"serial": SERIAL}, headers=H)
    assert r.json()["na_fila"] == 0 and [e["id"] for e in r.json()["emitindo"]] == [a["id"]]


@pytest.mark.asyncio
async def test_payload_do_lease_e_previa(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    # a nota do cenário tem nº sorteado: fica 100, e a última da BEZERRA é a 3026
    await db.execute(
        update(NfNota).where(NfNota.emitente_cnpj == CNPJ_BEZERRA).values(numero="100")
    )
    db.add(NfNota(chave="35" + "7" * 42, numero="3026", emitente_cnpj=CNPJ_BEZERRA, xml=b"<x/>"))
    db.add(NfNota(chave="35" + "8" * 42, numero="ABC", emitente_cnpj=CNPJ_BEZERRA, xml=b"<x/>"))
    await db.commit()
    c = await _registrar(client, cenario)
    # prévia (DRY-RUN) com a compra registrada: não muda nada
    r = await client.get(f"{AGENTE}/previa", params={"numero": c["numero"]}, headers=H)
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["status"] == "registrada" and prev["problemas"] == []
    assert (await _ler(db, c["id"])).status == "registrada"
    assert (
        await client.get(f"{AGENTE}/previa", params={"numero": 1}, headers=H)
    ).status_code == 404

    assert (await client.post(f"{BASE}/{c['id']}/emitir")).status_code == 200
    x = (await _lease(client))["compras"][0]
    p = await _ler(db, c["id"])
    itens = (await svc.itens_de(db, [p.id]))[p.id]
    planilha = svc.planilha([p], {p.id: itens})
    assert base64.b64decode(x["planilha_b64"]) == planilha
    assert x["planilha_b64"] == prev["planilha_b64"]
    assert x["nome_arquivo"] == f"compra_{c['numero']}_comercial-bezerra.csv"
    assert x["vendedor"] == {
        "cnpj": CNPJ_BEZERRA,
        "nome": "COMERCIAL BEZERRA",
        "uf": "SP",
        "serial": SERIAL,
    }
    assert x["comprador"]["cnpj"] == CNPJ_VITA and x["comprador"]["ie"] == "125632321110"
    assert x["comprador"]["uf"] == "SP" and x["comprador"]["municipio"] == "São Paulo"
    assert x["cfop"] == "5102" and x["total"] == "4185.00"
    assert x["itens"] == [
        {
            "sku": "a001",
            "nome": "Embalagem",
            "ncm": "39232110",
            "unidade": "UN",
            "quantidade": 450,
            "valor_unitario": "9.30",
            "valor_total": "4185.00",
        }
    ]
    assert x["ultima_nf_vendedora"] == 3026  # o "ABC" não conta
    assert len(x["planilha_sha256"]) == 64
    # cancelada / com NF: a prévia recusa
    await client.post(f"{AGENTE}/{c['id']}/resultado", headers=H, json=_ok())
    r = await client.get(f"{AGENTE}/previa", params={"numero": c["numero"]}, headers=H)
    assert r.status_code == 409


# --- robô: etapas ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_etapa_so_avanca(client: AsyncClient, db: AsyncSession, operador, auth_as, cenario):
    auth_as(operador)
    c, _ = await _com_o_robo(client, cenario)
    cid = c["id"]
    assert await _etapa(client, cid, "import_tentado") == 200
    # o "Confirmar" não chegou a ser clicado: o único recuo
    assert await _etapa(client, cid, "lease") == 200
    assert (await _ler(db, cid)).emissao_etapa == "lease"
    for e in ("import_tentado", "importado", "nf_gerada"):
        assert await _etapa(client, cid, e) == 200, e
    assert await _etapa(client, cid, "nf_gerada", nf_numero="003031") == 200  # repetir = ok
    assert (await _ler(db, cid)).nf_numero == "3031"
    for e in ("pronta_sefaz", "sefaz_tentado"):
        assert await _etapa(client, cid, e) == 200
    r = await client.post(f"{AGENTE}/{cid}/etapa", headers=H, json={"etapa": "pronta_sefaz"})
    assert r.status_code == 409 and _code(r) == "etapa_invalida"
    assert await _etapa(client, cid, "lease") == 409
    assert await _etapa(client, cid, "devolvido") == 409  # só pelo /resultado
    assert await _etapa(client, cid, "nada") == 422
    p = await _ler(db, cid)
    assert p.status == "emitindo" and p.emissao_etapa == "sefaz_tentado"
    # tirar do robô (permissão de excluir): o próximo POST dele dá 409 e ele para
    r = await client.post(f"{BASE}/{cid}/parar-robo")
    assert r.status_code == 200 and r.json()["status"] == "erro"
    assert r.json()["emissao_erro"] == "tirada do robô por Operador Compra"
    assert r.json()["emissao_etapa"] == "sefaz_tentado" and r.json()["numero_queimado"]
    r = await client.post(f"{AGENTE}/{cid}/etapa", headers=H, json={"etapa": "autorizada"})
    assert r.status_code == 409 and _code(r) == "fora_do_robo"
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=_ok())
    assert r.status_code == 409 and _code(r) == "fora_do_robo"
    assert (await client.post(f"{BASE}/{cid}/parar-robo")).status_code == 409


# --- número queimado ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_numero_queimado_trava_tudo(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    c, _ = await _com_o_robo(client, cenario)
    u = f"{BASE}/{c['id']}"
    assert await _etapa(client, c["id"], "import_tentado") == 200
    r = await client.post(
        f"{AGENTE}/{c['id']}/resultado",
        headers=H,
        json={"status": "erro", "retentavel": False, "motivo": "CONFIRMAR_INCERTO"},
    )
    assert r.json()["status"] == "erro"
    c2 = (await client.get(u)).json()
    assert c2["numero_queimado"] and c2["emissao_erro"] == "CONFIRMAR_INCERTO"
    for r in (
        await client.put(u, json=_compra(cenario)),
        await client.get(f"{u}/planilha"),
        await client.post(f"{BASE}/planilha", json={"ids": [c["id"]]}),
        await client.post(f"{u}/emitir", json={"confirmo_planilha_nao_importada": True}),
        await client.delete(u),
    ):
        assert r.status_code == 409 and _code(r) == "numero_queimado", r.text
    r = await client.post(f"{u}/cancelar")
    assert r.status_code == 409 and _code(r) == "conferir_no_bling"
    r = await client.post(f"{u}/cancelar", params={"conferido": 1})
    assert r.status_code == 200 and r.json()["status"] == "cancelada"
    r = await client.post(f"{u}/reabrir")
    assert r.status_code == 409 and _code(r) == "numero_queimado"


@pytest.mark.asyncio
async def test_anotar_a_nf_de_compra_queimada(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    """O robô parou depois da SEFAZ: a pessoa confere no Bling 44 e anota nº e chave."""
    auth_as(operador)
    c, _ = await _com_o_robo(client, cenario)
    for e in ("import_tentado", "importado", "nf_gerada", "pronta_sefaz", "sefaz_tentado"):
        assert await _etapa(client, c["id"], e) == 200
    await client.post(f"{BASE}/{c['id']}/parar-robo")
    r = await client.post(
        f"{BASE}/{c['id']}/nf", json={"nf_numero": "3031", "nf_chave": "35 2611 " + CHAVE[6:]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "nf_emitida" and r.json()["nf_chave"] == CHAVE
    r = await client.post(f"{BASE}/{c['id']}/nf", json={"nf_numero": "3031", "nf_chave": "123"})
    assert r.status_code == 422
    r = await client.post(f"{BASE}/{c['id']}/nf", json={"nf_numero": "3032"})
    assert r.status_code == 409 and _code(r) == "numero_diferente_da_chave"
    # a mesma chave em outra compra: recusa
    outra = await _registrar(client, cenario)
    r = await client.post(f"{BASE}/{outra['id']}/nf", json={"nf_numero": "1", "nf_chave": CHAVE})
    assert r.status_code == 409 and _code(r) == "chave_em_outra_compra"


# --- robô: resultado ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resultado_ok_confere_o_xml_e_grava_a_nota(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    c, _ = await _com_o_robo(client, cenario)
    cid = c["id"]
    for e in ETAPAS[1:-1]:
        extra = {"nf_numero": "3031"} if e == "nf_gerada" else {}
        assert await _etapa(client, cid, e, **extra) == 200, e
    # cada campo divergente: 422 e a compra continua com o robô
    for o in (
        {"vnf": "4194.30"},
        {"emit": CNPJ_TELES},
        {"ie": "999"},
        {"cfop": "6102"},
        {"qcom": "451"},
        {"digest": "outro="},
    ):
        corpo = _ok(xml_b64=base64.b64encode(_xml(**o)).decode())
        r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=corpo)
        assert r.status_code == 422 and _code(r) == "xml_nao_confere", o
        assert len(r.json()["detail"]["erros"]) == 1, r.json()
    # nº diferente da NF gerada nessa compra (etapa nf_gerada) também não passa
    corpo = _ok(nf_numero="3032", xml_b64=base64.b64encode(_xml(nnf="3032")).decode())
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=corpo)
    assert r.status_code == 422 and "3032≠3031" in r.json()["detail"]["erros"][0]
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=_ok(xml_b64="não é base64"))
    assert r.status_code == 422 and r.json()["detail"]["erros"] == ["xml_b64_invalido"]
    r = await client.post(
        f"{AGENTE}/{cid}/resultado",
        headers=H,
        json=_ok(xml_b64=base64.b64encode(b"<nada/>").decode()),
    )
    assert r.status_code == 422 and r.json()["detail"]["erros"] == ["nf_xml_sem_nfe"]
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=_ok(xml_b64=None))
    assert r.status_code == 422 and _code(r) == "faltou_xml"
    p = await _ler(db, cid)
    assert p.status == "emitindo" and p.nf_chave is None
    assert (await db.execute(select(NfNota).where(NfNota.chave == CHAVE))).first() is None

    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=_ok())
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "nf_emitida"}
    p = await _ler(db, cid)
    assert (p.status, p.nf_numero, p.nf_chave) == ("nf_emitida", "3031", CHAVE)
    assert p.emissao_etapa == "devolvido" and p.nf_emitida_em is not None
    nota = (await db.execute(select(NfNota).where(NfNota.chave == CHAVE))).scalar_one()
    assert nota.pedido_bling is None and nota.numero == "3031" and nota.situacao == "100"
    assert nota.emitente_cnpj == CNPJ_BEZERRA and nota.destinatario_doc == CNPJ_VITA
    assert nota.valor == Decimal("4185.00") and nota.xml == _xml()
    # a resposta se perdeu e o robô manda de novo: 200 sem mudar nada
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=_ok())
    assert r.status_code == 200
    # NF com chave não troca de número à mão (a chave diz o nº)
    r = await client.post(f"{BASE}/{cid}/nf", json={"nf_numero": "9999"})
    assert r.status_code == 409 and _code(r) == "numero_diferente_da_chave"
    assert (await _ler(db, cid)).nf_numero == "3031"
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=_ok(nf_chave="1" * 44))
    assert r.status_code == 409 and _code(r) == "chave_diferente"
    # a tela baixa o XML
    r = await client.get(f"{BASE}/{cid}/xml")
    assert r.status_code == 200 and r.content == _xml()
    assert r.headers["content-type"].startswith("application/xml")
    assert 'filename="nfe_3031.xml"' in r.headers["content-disposition"]
    sem = await _registrar(client, cenario)
    assert (await client.get(f"{BASE}/{sem['id']}/xml")).status_code == 404
    # NF do robô: sem planilha; desfazer vai para cancelada guardando nº e chave
    assert (await client.get(f"{BASE}/{cid}/planilha")).status_code == 409
    r = await client.post(f"{BASE}/{cid}/desfazer-nf")
    assert r.status_code == 409 and _code(r) == "conferir_no_bling"
    r = await client.post(f"{BASE}/{cid}/desfazer-nf", params={"conferido": 1})
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["nf_numero"], r.json()["nf_chave"]) == (
        "cancelada",
        "3031",
        CHAVE,
    )
    assert (await client.post(f"{BASE}/{cid}/reabrir")).status_code == 409


@pytest.mark.asyncio
async def test_resultado_erro_tres_caminhos(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)

    async def erro(cid: str, **kw) -> dict:
        corpo = {"status": "erro", "motivo": "perfil não abriu", **kw}
        r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=corpo)
        assert r.status_code == 200, r.text
        return r.json()

    # 1) retentável antes de queimar o número: volta para a fila (até 3 tentativas)
    c, _ = await _com_o_robo(client, cenario)
    cid = c["id"]
    assert (await erro(cid, retentavel=True, etapa="lease"))["status"] == "na_fila"
    p = await _ler(db, cid)
    assert p.emissao_etapa is None and p.emissao_erro == "perfil não abriu"
    assert p.emissao_tentativas == 1
    for tentativa in (2, 3):
        x = (await _lease(client))["compras"][0]
        assert x["id"] == cid and x["tentativas"] == tentativa
        st = (await erro(cid, retentavel=True))["status"]
    assert st == "erro"  # a 3ª tentativa retentável vira erro
    p = await _ler(db, cid)
    assert p.status == "erro" and not queimado(p.emissao_etapa)
    # erro sem número queimado: dá para emitir de novo (e editar), com 3 tentativas novas
    r = await client.post(f"{BASE}/{cid}/emitir")
    assert r.json()["status"] == "na_fila" and r.json()["emissao_tentativas"] == 0
    assert (await client.post(f"{BASE}/{cid}/tirar-da-fila")).status_code == 200

    # 2) retentável com o número queimado: continua com o robô ("pausada")
    c2, _ = await _com_o_robo(client, cenario, competencia="2026-08-01")
    for e in ("import_tentado", "importado", "nf_gerada", "pronta_sefaz", "sefaz_tentado"):
        assert await _etapa(client, c2["id"], e) == 200
    res = await erro(
        c2["id"],
        retentavel=True,
        etapa="sefaz_tentado",
        motivo="Pendente após o envio",
        nf_numero="3031",
    )
    assert res["status"] == "emitindo"
    p = await _ler(db, c2["id"])
    assert p.emissao_etapa == "sefaz_tentado" and p.emissao_erro == "Pendente após o envio"
    assert p.nf_numero == "3031"
    r = await _lease(client)
    assert r["compras"] == [] and r["ocupada"]["erro"] == "Pendente após o envio"
    # o robô retoma e avança: a pausa sai
    assert await _etapa(client, c2["id"], "autorizada") == 200
    assert (await _ler(db, c2["id"])).emissao_erro is None

    # 3) não retentável: erro, etapa preservada (o robô diz até onde chegou)
    res = await erro(
        c2["id"], retentavel=False, etapa="xml_ok", motivo="XML não bate", nf_chave=CHAVE
    )
    p = await _ler(db, c2["id"])
    assert res["status"] == "erro" and p.emissao_etapa == "xml_ok"
    assert p.emissao_erro == f"XML não bate (chave {CHAVE})" and p.nf_chave is None
    r = await client.post(f"{AGENTE}/{c2['id']}/resultado", headers=H, json={"status": "erro"})
    assert r.status_code == 409 and _code(r) == "fora_do_robo"


@pytest.mark.asyncio
async def test_resultado_erro_com_a_nf_autorizada_guarda_a_chave(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    """A NF saiu autorizada mas o XML não bateu com a compra: a chave fica na compra (a
    pessoa anota sem digitar) e o XML só vai para nf_nota se for o autorizado (digest)."""
    auth_as(operador)

    async def ate_autorizada(**kw) -> str:
        c, _ = await _com_o_robo(client, cenario, **kw)
        for e in ETAPAS[1:7]:
            extra = {"nf_numero": "3031"} if e == "nf_gerada" else {}
            assert await _etapa(client, c["id"], e, **extra) == 200, e
        return c["id"]

    def erro(xml: bytes, **o) -> dict:
        return {
            "status": "erro",
            "etapa": "autorizada",
            "motivo": "XML não confere",
            "nf_numero": "3031",
            "nf_chave": CHAVE,
            "xml_b64": base64.b64encode(xml).decode(),
            **o,
        }

    # IE errada no XML autorizado: chave na compra e XML em nf_nota
    cid = await ate_autorizada()
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=erro(_xml(ie="999")))
    assert r.json()["status"] == "erro"
    p = await _ler(db, cid)
    assert (p.nf_chave, p.nf_numero) == (CHAVE, "3031")
    nota = (await db.execute(select(NfNota).where(NfNota.chave == CHAVE))).scalar_one()
    assert nota.pedido_bling is None
    r = await client.post(f"{BASE}/{cid}/nf", json={"nf_numero": "3031"})
    assert r.status_code == 200 and r.json()["nf_chave"] == CHAVE
    assert (await client.get(f"{BASE}/{cid}/xml")).status_code == 200

    # XML com outro digest (reenviado): a chave fica, o XML NÃO vai para nf_nota
    chave2 = CHAVE[:33] + "2" + CHAVE[34:]  # NF 3032
    cid = await ate_autorizada(competencia="2026-08-01")
    p = await _ler(db, cid)
    p.nf_numero = "3032"
    await db.commit()
    corpo = erro(_xml(chave=chave2, nnf="3032", digest="outro="), nf_chave=chave2, nf_numero="3032")
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=corpo)
    p = await _ler(db, cid)
    assert p.status == "erro" and p.nf_chave == chave2
    assert (await db.execute(select(NfNota).where(NfNota.chave == chave2))).first() is None

    # a chave de outra compra, ou XML de outra vendedora: só no texto do erro
    cid = await ate_autorizada(competencia="2026-07-01")
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=erro(_xml()))
    p = await _ler(db, cid)
    assert p.nf_chave is None and CHAVE in p.emissao_erro
    cid = await ate_autorizada(competencia="2026-06-01")
    p = await _ler(db, cid)
    p.nf_numero = "3033"
    await db.commit()
    chave3 = CHAVE[:33] + "3" + CHAVE[34:]  # NF 3033
    xml = _xml(chave=chave3, nnf="3033", emit=CNPJ_TELES)
    corpo = erro(xml, nf_chave=chave3, nf_numero="3033")
    r = await client.post(f"{AGENTE}/{cid}/resultado", headers=H, json=corpo)
    assert (await _ler(db, cid)).nf_chave is None


# --- banco ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_indice_unico_da_chave_e_etapa_valida(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    a = await _registrar(client, cenario)
    b = await _registrar(client, cenario)
    pa, pb = await _ler(db, a["id"]), await _ler(db, b["id"])
    pa.nf_chave = pb.nf_chave = CHAVE
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    pa = await _ler(db, a["id"])
    pa.emissao_etapa = "sefaz_enviada"  # etapa que não existe
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    # várias compras sem chave convivem
    pa, pb = await _ler(db, a["id"]), await _ler(db, b["id"])
    assert pa.nf_chave is None and pb.nf_chave is None
