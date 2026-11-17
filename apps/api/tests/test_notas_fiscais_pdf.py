"""Logística › Notas fiscais: o botão PDF e a marca de pedido cancelado
(17/11/2026, Novos procedimentos 7 — 407aa5da).

O que estes testes seguram:

- GET /api/notas-fiscais/pos-vendas/nota/{id}/pdf devolve a DANFE (PDF)
  desenhada do XML GUARDADO no DaVinci (`nf_nota.xml`) — a nota do coletor
  direto, a do espelho do Bling pela mesma chave de acesso — sem pedir nada
  ao Bling; sem o XML guardado → 404 `xml_nao_guardado`; XML que não vira
  DANFE → 422 `danfe_invalida`;
- a MESMA permissão (`notas_fiscais` view; só Logística não basta) e a MESMA
  cerca de equipe do XML (403 `pedido_de_outra_equipe` antes de desenhar);
- a lista marca `pedido_cancelado` (pedido em 12 no Bling: "Pedido cancelado
  — conferir NF") e `xml_guardado` em cada nota (é o que mostra o botão PDF),
  sem carregar a coluna `xml` (pesada).

Os testes "de mentira" (sessão falsa, sem banco) rodam em qualquer lugar; os
do fim usam o banco do conftest (`db`/`client`).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user
from app.models import (
    BlingNota,
    BlingNotaEmitida,
    BlingOrder,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.models.nf import NfNota
from app.routers import notas_fiscais as nf_router

URL = "/api/notas-fiscais/pos-vendas"
PERM_NOTAS = {"notas_fiscais": {"view": True, "edit": False, "delete": False}}
PERM_SO_LOGISTICA = {"logistica": {"view": True, "edit": True, "delete": False}}
CHAVE = "3526" + "11" + "00000000000191" + "55" + "001" + "000004923" + "1" + "00000001" + "7"
CHAVE_BLING = "3526" + "11" + "00000000000191" + "55" + "001" + "000004924" + "1" + "00000002" + "4"


def _xml(chave: str = CHAVE, numero: str = "4923") -> bytes:
    """Uma NF-e autorizada mínima (o desenho é testado em test_atendimento_danfe)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe{chave}"
versao="4.00"><ide><natOp>Venda de mercadoria</natOp><serie>1</serie><nNF>{numero}</nNF>
<dhEmi>2026-11-17T10:00:00-03:00</dhEmi><tpNF>1</tpNF><tpAmb>1</tpAmb><finNFe>1</finNFe></ide>
<emit><CNPJ>00000000000191</CNPJ><xNome>Empresa Teste Ltda</xNome><enderEmit><xLgr>Avenida
Teste</xLgr><nro>54</nro><xBairro>Centro</xBairro><xMun>Sao Paulo</xMun><UF>SP</UF>
<CEP>01001000</CEP></enderEmit><IE>111111111111</IE></emit><dest><CPF>12345678909</CPF>
<xNome>Cliente Teste</xNome><enderDest><xLgr>Rua Um</xLgr><nro>1</nro><xBairro>Bairro</xBairro>
<xMun>Sao Paulo</xMun><UF>SP</UF><CEP>01002000</CEP></enderDest></dest><det nItem="1"><prod>
<cProd>b096.1</cProd><xProd>Mala de teste</xProd><NCM>42021210</NCM><CFOP>5102</CFOP>
<uCom>UN</uCom><qCom>1.0000</qCom><vUnCom>424.0000000000</vUnCom><vProd>424.00</vProd></prod>
<imposto><ICMS><ICMSSN102><orig>0</orig><CSOSN>102</CSOSN></ICMSSN102></ICMS></imposto></det>
<total><ICMSTot><vBC>0.00</vBC><vICMS>0.00</vICMS><vBCST>0.00</vBCST><vST>0.00</vST>
<vProd>424.00</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc><vIPI>0.00</vIPI>
<vOutro>0.00</vOutro><vNF>424.00</vNF></ICMSTot></total><transp><modFrete>0</modFrete></transp>
</infNFe></NFe><protNFe versao="4.00"><infProt><tpAmb>1</tpAmb><chNFe>{chave}</chNFe>
<dhRecbto>2026-11-17T10:00:02-03:00</dhRecbto><nProt>135260000000001</nProt><cStat>100</cStat>
<xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe></nfeProc>""".encode()


def _texto(pdf: bytes) -> str:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return "\n".join(p.get_text() for p in doc)


# ─── sem banco: a rota com uma sessão de mentira ─────────────────────────


class _Sessao:
    """As notas por id (`get`) e o XML guardado por chave (`scalar`)."""

    def __init__(self, *objs: object, guardados: dict[str, bytes] | None = None) -> None:
        self.por_id = {o.id: o for o in objs}  # type: ignore[attr-defined]
        self.guardados = dict(guardados or {})
        self.chaves_lidas: list[str] = []

    async def get(self, modelo: type, ident: uuid.UUID):
        o = self.por_id.get(ident)
        return o if isinstance(o, modelo) else None

    async def scalar(self, stmt):
        # select(NfNota.xml).where(NfNota.chave == :chave) — um parâmetro só.
        assert [c.name for c in stmt.selected_columns] == ["xml"]
        (chave,) = stmt.compile().params.values()
        self.chaves_lidas.append(chave)
        return self.guardados.get(chave)


def _usuario(perms: dict | None = PERM_NOTAS, *, role: UserRole = UserRole.USER) -> User:
    return User(
        email="nf-pdf@davinci-test.com",
        name="nf-pdf",
        role=role,
        status=UserStatus.ACTIVE,
        permissions=perms,
        sales_teams=None,
    )


def _cliente(sessao: _Sessao, usuario: User) -> TestClient:
    app = FastAPI()
    app.include_router(nf_router.router)
    app.dependency_overrides[get_session] = lambda: sessao
    app.dependency_overrides[require_active_user] = lambda: usuario
    return TestClient(app)


def _coletada(xml: bytes = b"", *, chave: str = CHAVE, numero: str = "4923") -> NfNota:
    return NfNota(
        id=uuid4(),
        chave=chave,
        numero=numero,
        pedido_bling="900001",
        destinatario_doc="12345678909",
        data_emissao=datetime(2026, 11, 17, 13, tzinfo=UTC),
        situacao="100",
        xml=xml or _xml(chave, numero),
    )


def _emitida(chave: str | None = CHAVE_BLING) -> BlingNotaEmitida:
    return BlingNotaEmitida(
        id=uuid4(),
        conta_id=uuid4(),
        bling_id=4924,
        numero="4924",
        situacao=5,
        chave_acesso=chave,
        complemento="MK900001",
        cpf_dest="12345678909",
        data_emissao=datetime(2026, 11, 17, 10),
    )


@pytest.fixture
def bling_proibido(monkeypatch: pytest.MonkeyPatch) -> None:
    """O PDF nunca vai ao Bling: qualquer chamada derruba o teste."""

    async def _nao(*_a, **_k):
        raise AssertionError("o PDF não pode pedir nada ao Bling")

    monkeypatch.setattr(nf_router, "_fetch_nota_xml", _nao)
    monkeypatch.setattr(nf_router, "_ensure_token", _nao)


def test_pdf_da_nota_do_coletor_sai_do_xml_guardado(bling_proibido):
    n = _coletada()
    r = _cliente(_Sessao(n), _usuario()).get(f"{URL}/nota/{n.id}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="DANFE_{CHAVE}.pdf"'
    assert r.content.startswith(b"%PDF")
    texto = _texto(r.content)
    assert "DANFE" in texto and "004923" in texto


def test_pdf_da_nota_do_bling_usa_o_xml_guardado_pela_chave(bling_proibido):
    ne = _emitida()
    sessao = _Sessao(ne, guardados={CHAVE_BLING: _xml(CHAVE_BLING, "4924")})
    r = _cliente(sessao, _usuario()).get(f"{URL}/nota/{ne.id}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="DANFE_{CHAVE_BLING}.pdf"'
    assert "004924" in _texto(r.content)
    assert sessao.chaves_lidas == [CHAVE_BLING]


def test_pdf_sem_xml_guardado_404(bling_proibido):
    sem_guardado, sem_chave = _emitida(), _emitida(chave=None)
    sessao = _Sessao(sem_guardado, sem_chave)
    c = _cliente(sessao, _usuario())
    for ne in (sem_guardado, sem_chave):
        r = c.get(f"{URL}/nota/{ne.id}/pdf")
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["code"] == "xml_nao_guardado"
    # Sem chave nem consulta o XML guardado.
    assert sessao.chaves_lidas == [CHAVE_BLING]

    r = c.get(f"{URL}/nota/{uuid4()}/pdf")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nota_nao_encontrada"


def test_pdf_xml_que_nao_vira_danfe_422(bling_proibido):
    n = _coletada(b"<nfe>quebrado</nfe>")
    r = _cliente(_Sessao(n), _usuario()).get(f"{URL}/nota/{n.id}/pdf")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "danfe_invalida"


def test_pdf_tem_a_mesma_permissao_do_xml(bling_proibido):
    n = _coletada()
    for perms in (None, {}, PERM_SO_LOGISTICA, {"notas_fiscais": {"view": False}}):
        c = _cliente(_Sessao(n), _usuario(perms))
        for rota in ("pdf", "xml"):
            r = c.get(f"{URL}/nota/{n.id}/{rota}")
            assert r.status_code == 403, (perms, rota, r.text)
            assert r.json()["detail"] == {
                "code": "forbidden",
                "resource": "notas_fiscais",
                "action": "view",
            }
    # Admin, como sempre.
    r = _cliente(_Sessao(n), _usuario(None, role=UserRole.ADMIN)).get(f"{URL}/nota/{n.id}/pdf")
    assert r.status_code == 200, r.text


def test_pdf_tem_a_mesma_cerca_de_equipe_do_xml(
    monkeypatch: pytest.MonkeyPatch, bling_proibido
):
    """Quem é de equipe só leva a nota de pedido da equipe — a MESMA régua
    (`_nota_da_linha`) para o XML e o PDF, conferida ANTES de desenhar."""
    desenhos: list[bytes] = []
    gerar = nf_router.gerar_pdf

    def _gerar(xml: bytes) -> bytes:
        desenhos.append(xml)
        return gerar(xml)

    perguntas: list[dict] = []
    da_equipe = {"resposta": False}

    async def _lojas(_s, _u):
        return {"8831"}

    async def _nota_da_equipe(_s, lojas, **chaves):
        perguntas.append({"lojas": lojas, **chaves})
        return da_equipe["resposta"]

    monkeypatch.setattr(nf_router, "gerar_pdf", _gerar)
    monkeypatch.setattr(nf_router, "_lojas_da_equipe", _lojas)
    monkeypatch.setattr(nf_router, "_nota_da_equipe", _nota_da_equipe)

    n, ne = _coletada(), _emitida()
    c = _cliente(_Sessao(n, ne, guardados={CHAVE_BLING: _xml(CHAVE_BLING, "4924")}), _usuario())
    for nota in (n, ne):
        for rota in ("pdf", "xml"):
            r = c.get(f"{URL}/nota/{nota.id}/{rota}")
            assert r.status_code == 403, (rota, r.text)
            assert r.json()["detail"]["code"] == "pedido_de_outra_equipe"
    assert desenhos == []
    # As chaves do casamento, iguais nas duas rotas.
    do_coletor = {
        "lojas": {"8831"},
        "pedido": "900001",
        "complemento": None,
        "cpf": "12345678909",
        "emissao": n.data_emissao,
    }
    do_bling = {
        "lojas": {"8831"},
        "pedido": None,
        "complemento": "MK900001",
        "cpf": "12345678909",
        "emissao": ne.data_emissao,
    }
    assert perguntas == [do_coletor, do_coletor, do_bling, do_bling]

    da_equipe["resposta"] = True
    for nota in (n, ne):
        r = c.get(f"{URL}/nota/{nota.id}/pdf")
        assert r.status_code == 200, r.text
    assert len(desenhos) == 2


# ─── sem banco: a lista (montar_pos_vendas) ──────────────────────────────


class _Resultado:
    def __init__(self, linhas: list) -> None:
        self._linhas = linhas

    def all(self) -> list:
        return list(self._linhas)

    def scalars(self) -> _Resultado:
        return self


class _SessaoDaLista:
    """Responde cada SELECT da lista pela tabela do FROM."""

    def __init__(self, **por_tabela: list) -> None:
        self.por_tabela = por_tabela
        self.colunas_nf_nota: list[str] = []

    async def execute(self, stmt) -> _Resultado:
        sql = str(stmt.compile())
        for tabela in (
            "nf_etiqueta_arquivo",
            "store_info",
            "bling_notas_emitidas",
            "nf_nota",
            "bling_orders",
        ):
            if re.search(rf"FROM (\w+\.)?{tabela}\b", sql):
                if tabela == "nf_nota":
                    self.colunas_nf_nota = [c.name for c in stmt.selected_columns]
                return _Resultado(self.por_tabela.get(tabela, []))
        raise AssertionError(f"SELECT inesperado: {sql[:80]}")


async def test_lista_marca_pedido_cancelado_e_xml_guardado_sem_ler_o_xml():
    dia = date(2026, 11, 17)
    pedido = {
        "envio": dia,
        "loja": "8831",
        "skus": "b096.1",
        "produtos": "Mala de teste",
    }
    pedidos = [
        # Cancelado no Bling (12) depois da NF: a linha pede para conferir.
        SimpleNamespace(
            numero="900001", numeroloja="MK900001", total=200, doc="111", cancelado=True, **pedido
        ),
        SimpleNamespace(
            numero="900002", numeroloja="MK900002", total=300, doc="222", cancelado=False, **pedido
        ),
        # bool_or de situação nula vem NULL do banco: não é cancelado.
        SimpleNamespace(
            numero="900003", numeroloja="MK900003", total=100, doc="333", cancelado=None, **pedido
        ),
    ]
    loja = StoreInfo(
        user_id=uuid4(),
        platform="ml",
        account_name="loja-teste",
        bling_store_id="8831",
        cnpj="11111111000111",
    )
    conta = BlingNota(id=uuid4(), nome="conta-loja", cnpj="11111111000111", emitente="LOJA")
    emissao = datetime(2026, 11, 17, 9)
    # Embalagem do 900001: o XML dela também chegou pelo coletor (mesma chave).
    emb1 = BlingNotaEmitida(
        id=uuid4(), conta_id=conta.id, bling_id=1, numero="11", situacao=5,
        data_emissao=emissao, chave_acesso="CH-EMB-1", complemento="MK900001", valor=2,
    )
    # Embalagem do 900002: só no espelho do Bling (sem XML guardado).
    emb2 = BlingNotaEmitida(
        id=uuid4(), conta_id=conta.id, bling_id=2, numero="12", situacao=5,
        data_emissao=emissao, chave_acesso="CH-EMB-2", complemento="MK900002", valor=3,
    )
    coletada = {
        "pedido_bling": None,
        "emitente_cnpj": "33333333000133",
        "emitente_nome": "Avulsa",
        "destinatario_doc": None,
        "data_emissao": datetime(2026, 11, 17, 12, tzinfo=UTC),
    }
    xml_rows = [
        SimpleNamespace(id=uuid4(), chave="CH-EMB-1", numero="11", valor=2, **coletada),
        SimpleNamespace(
            id=uuid4(),
            chave="CH-PROD-2",
            numero="21",
            valor=300,
            **{**coletada, "pedido_bling": "900002"},
        ),
    ]
    sessao = _SessaoDaLista(
        bling_orders=pedidos,
        store_info=[loja],
        bling_notas_emitidas=[(emb1, conta), (emb2, conta)],
        nf_nota=xml_rows,
    )

    linhas = {
        i.pedido_bling: i
        for i in await nf_router.montar_pos_vendas(sessao, dia, dia + timedelta(days=1))
    }

    assert {p: linha.pedido_cancelado for p, linha in linhas.items()} == {
        "900001": True,
        "900002": False,
        "900003": False,
    }
    um, dois = linhas["900001"], linhas["900002"]
    assert um.nf_embalagem is not None and um.nf_embalagem.nota_id == emb1.id
    assert um.nf_embalagem.xml_guardado is True
    assert um.nf_produto is None
    assert dois.nf_embalagem is not None and dois.nf_embalagem.nota_id == emb2.id
    assert dois.nf_embalagem.xml_guardado is False
    assert dois.nf_produto is not None and dois.nf_produto.nota_id == xml_rows[1].id
    assert dois.nf_produto.xml_guardado is True
    # A coluna pesada não vem na lista.
    assert sessao.colunas_nf_nota and "xml" not in sessao.colunas_nf_nota


# ─── com o banco (conftest) ──────────────────────────────────────────────

_DIA = date(2026, 6, 9)


async def _user(db: AsyncSession, perms: dict | None) -> User:
    email = f"nf-pdf-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        name="nf-pdf",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=perms,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _pedido(numero: str, situacao: str) -> BlingOrder:
    return BlingOrder(
        bling_id=int(numero),
        numero=numero,
        numeroloja=f"MK{numero}",
        loja="8841",
        item_codigo=f"x{numero[-3:]}.ci",
        item_index=0,
        item_descricao=f"Produto {numero}",
        total=424,
        documento_destinatario="12345678909",
        situacao=situacao,
        em_andamento_data=_DIA,
        data=datetime(2026, 6, 9, 9, tzinfo=UTC),
    )


def _nota_do_coletor(numero: str, pedido: str, chave: str) -> NfNota:
    return NfNota(
        chave=chave,
        pedido_bling=pedido,
        numero=numero,
        emitente_cnpj="44444444000144",
        emitente_nome="Faturador",
        destinatario_doc="12345678909",
        valor=424,
        data_emissao=datetime(2026, 6, 9, 13, tzinfo=UTC),
        situacao="100",
        xml=_xml(chave, numero),
    )


async def test_lista_e_pdf_de_verdade(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
):
    chave_ok = CHAVE
    chave_cancelado = CHAVE_BLING
    db.add_all(
        [
            _pedido("884001", "9"),  # Atendido
            _pedido("884002", "12"),  # Cancelado depois da NF
            _nota_do_coletor("4923", "884001", chave_ok),
            _nota_do_coletor("4924", "884002", chave_cancelado),
        ]
    )
    await db.commit()

    auth_as(await _user(db, PERM_NOTAS))
    r = await client.get(f"{URL}?date_from={_DIA}&date_to={_DIA}")
    assert r.status_code == 200, r.text
    linhas = {i["pedido_bling"]: i for i in r.json()["items"]}
    assert linhas["884001"]["pedido_cancelado"] is False
    assert linhas["884002"]["pedido_cancelado"] is True
    nota_ok = linhas["884001"]["nf_produto"]
    assert nota_ok["xml_guardado"] is True

    r = await client.get(f"{URL}/nota/{nota_ok['nota_id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")

    r = await client.get(f"{URL}/nota/{uuid4()}/pdf")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nota_nao_encontrada"

    # Só Logística não abre as notas (nem o PDF).
    auth_as(await _user(db, PERM_SO_LOGISTICA))
    r = await client.get(f"{URL}/nota/{nota_ok['nota_id']}/pdf")
    assert r.status_code == 403
