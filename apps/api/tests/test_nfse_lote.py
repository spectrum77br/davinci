"""Notas de serviço em lote: .zip de PDFs/XMLs e um PDF só para imprimir (30/09/2026).

Eduardo (30/09): marcar várias notas em "Notas enviadas" e baixar ou imprimir
de uma vez. A NFE.io não tem download em lote: o DaVinci busca nota por nota
(aqui, NFE.io falsa via respx — nada sai para a NFE.io de verdade). O que estes
testes seguram:
- uma pasta por empresa (o nº da NFS-e é POR empresa: nº 1 da ATV ≠ nº 1 da Rocha);
- o XML já guardado não vai à NFE.io;
- nota que falhou não derruba o lote: vira "faltou" com o motivo (_FALTARAM.txt
  e o cabeçalho X-Nfse-Faltaram);
- no máximo 4 downloads ao mesmo tempo, prazo por arquivo e teto para o lote todo
  (sem prender a conexão do banco enquanto espera a NFE.io);
- NFE.io fora do ar em todas = 502 "não respondeu" (e não "sem arquivo");
- o PDF juntado sai na ordem da seleção, e PDF corrompido fica de fora;
- chave recusada em todas = 503 (e não um .zip vazio); nunca a chave no banco.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
import zipfile
from collections.abc import Callable
from datetime import date

import fitz  # PyMuPDF
import httpx
import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Company, User, UserRole
from app.models.nfse import NfseChamada, NfseEmissao
from app.services.nfse import emissao as svc
from app.services.nfse import lote_arquivos as svc_lote
from app.services.nfse import nfeio
from tests.test_nfse import API, CNPJ_TOMA, NFSE2, _nota_no_banco

CID_ROCHA = "b" * 24
URL_ARQUIVOS = "/api/nfse/emissoes/lote/arquivos"
URL_IMPRIMIR = "/api/nfse/emissoes/lote/imprimir"


@pytest.fixture(autouse=True)
def _nfeio_de_teste(monkeypatch):
    """Chave falsa e ambiente local — como no localhost."""
    s = get_settings()
    monkeypatch.setattr(s, "nfeio_api_key", "chave-de-teste")
    monkeypatch.setattr(s, "nfeio_base_url", API)
    monkeypatch.setattr(s, "nfeio_nfse_base_url", NFSE2)
    monkeypatch.setattr(s, "env", "development")
    monkeypatch.setattr(nfeio, "ESPERA_GET", 0.0)


@pytest_asyncio.fixture
async def operador(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:lote-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"lote-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        permissions={"emissao_servico": {"view": True}},  # baixar/imprimir = só ver
    )
    db.add(u)
    await db.commit()
    return u


@pytest_asyncio.fixture
async def empresas(db: AsyncSession) -> dict:
    """ATV (em teste na NFE.io) e Rocha (produção), cada uma com a sua nº 1."""
    atv = Company(razao_social="ATV SERVICOS LTDA", apelido="ATV", cnpj="11222333000181")
    rocha = Company(razao_social="ROCHA SERVICOS LTDA", apelido="Rocha Serviços", cnpj=CNPJ_TOMA)
    db.add_all([atv, rocha])
    await db.commit()
    return {"atv": atv.id, "rocha": rocha.id}


def _pdf(texto: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), texto)
    return doc.tobytes()


def _nota_id(n: int) -> str:
    return f"{n:024x}"


async def _nota(db: AsyncSession, company_id: uuid.UUID, n: int, **kw) -> NfseEmissao:
    kw.setdefault("n_nfse", str(n))
    kw.setdefault("xml", None)
    return await _nota_no_banco(db, company_id, nfeio_id=_nota_id(n), **kw)


def _servidor(api: respx.MockRouter, tipo: str, respostas: dict[str, object]) -> respx.Route:
    """NFE.io falsa para /pdf ou /xml de qualquer nota: `respostas[nota_id]` é
    bytes (200), um httpx.Response ou uma função async (request) -> Response."""

    async def _f(request: httpx.Request) -> httpx.Response:
        nota = request.url.path.rsplit("/", 2)[-2]
        r = respostas.get(nota, httpx.Response(404, json={"message": "not found"}))
        if callable(r):
            return await r(request)
        if isinstance(r, bytes):
            mime = "application/pdf" if tipo == "pdf" else "application/xml"
            return httpx.Response(200, content=r, headers={"content-type": mime})
        return r

    return api.get(
        path__regex=rf"^/v3/companies/[0-9a-f]+/serviceinvoices/[0-9a-f]+/{tipo}$",
        host="api.nfe.io",
    ).mock(side_effect=_f)


def _zip(r: httpx.Response) -> zipfile.ZipFile:
    assert r.headers["content-type"] == "application/zip", r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def _faltaram(r: httpx.Response) -> list[str]:
    v = r.headers["x-nfse-faltaram"]
    return v.split(",") if v else []


# --- .zip ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zip_de_pdf_uma_pasta_por_empresa_sem_colisao(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    # As duas empresas têm a nota nº 1: sem pasta, uma apagaria a outra.
    a = await _nota(db, empresas["atv"], 1, n_nfse="1")
    b = await _nota_no_banco(
        db,
        empresas["rocha"],
        nfeio_id=_nota_id(2),
        n_nfse="1",
        xml=None,
        cid=CID_ROCHA,
        ambiente="Production",
        competencia=date(2026, 8, 1),
    )
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(api, "pdf", {_nota_id(1): b"%PDF ATV", _nota_id(2): b"%PDF ROCHA"})
        r = await client.post(URL_ARQUIVOS, json={"ids": [str(a.id), str(b.id)], "tipo": "pdf"})
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith(
        'attachment; filename="notas-de-servico_pdf_'
    )
    assert (r.headers["x-nfse-total"], r.headers["x-nfse-ok"], _faltaram(r)) == ("2", "2", [])
    z = _zip(r)
    assert sorted(z.namelist()) == [
        "Rocha-Servicos/NFSe_1_2026-08.pdf",  # produção: sem TESTE_, sem acento
        "TESTE_ATV/NFSe_1_2026-09.pdf",  # empresa em teste na NFE.io
    ]
    assert z.read("TESTE_ATV/NFSe_1_2026-09.pdf") == b"%PDF ATV"
    assert z.read("Rocha-Servicos/NFSe_1_2026-08.pdf") == b"%PDF ROCHA"
    assert "_FALTARAM.txt" not in z.namelist()


@pytest.mark.asyncio
async def test_zip_de_xml_usa_o_guardado_e_guarda_o_que_veio(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    guardada = await _nota(db, empresas["atv"], 1, xml=b"<Nfse>guardado</Nfse>")
    nova = await _nota(db, empresas["atv"], 2)
    cancelada = await _nota(db, empresas["atv"], 3, status="cancelada")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rota = _servidor(
            api,
            "xml",
            {_nota_id(2): b"<Nfse>da nfeio</Nfse>", _nota_id(3): b"<Nfse>cancelada</Nfse>"},
        )
        ids = [str(guardada.id), str(nova.id), str(cancelada.id)]
        r = await client.post(URL_ARQUIVOS, json={"ids": ids, "tipo": "xml"})
    assert r.status_code == 200, r.text
    assert r.headers["x-nfse-ok"] == "3"
    # Só as 2 sem XML guardado foram à NFE.io.
    pedidas = sorted(c.request.url.path.rsplit("/", 2)[-2] for c in rota.calls)
    assert pedidas == [_nota_id(2), _nota_id(3)]
    z = _zip(r)
    assert z.read("TESTE_ATV/NFSe_1_2026-09.xml") == b"<Nfse>guardado</Nfse>"
    assert z.read("TESTE_ATV/NFSe_2_2026-09.xml") == b"<Nfse>da nfeio</Nfse>"
    assert z.read("TESTE_ATV/NFSe_3_2026-09_CANCELADA.xml") == b"<Nfse>cancelada</Nfse>"
    # A emitida ficou guardada (próxima vez sai do banco); a cancelada não.
    for e in (nova, cancelada):
        await db.refresh(e)
    assert base64.b64decode(nova.nfse_xml_b64) == b"<Nfse>da nfeio</Nfse>"
    assert cancelada.nfse_xml_b64 is None


@pytest.mark.asyncio
async def test_falha_parcial_vira_faltou_com_o_motivo(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    ok = await _nota(db, empresas["atv"], 1)
    sem_pdf = await _nota(db, empresas["atv"], 2, tomador_nome="CLIENTE DOIS LTDA")
    fora = await _nota(db, empresas["atv"], 3)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rota = _servidor(
            api,
            "pdf",
            {
                _nota_id(1): b"%PDF ok",
                _nota_id(2): httpx.Response(404, json={"message": "not found"}),
                _nota_id(3): httpx.Response(500, text="erro"),
            },
        )
        ids = [str(ok.id), str(sem_pdf.id), str(fora.id)]
        r = await client.post(URL_ARQUIVOS, json={"ids": ids, "tipo": "pdf"})
    assert r.status_code == 200, r.text
    assert r.headers["x-nfse-ok"] == "1" and r.headers["x-nfse-total"] == "3"
    assert _faltaram(r) == [str(sem_pdf.id), str(fora.id)]
    z = _zip(r)
    assert sorted(z.namelist()) == ["TESTE_ATV/NFSe_1_2026-09.pdf", "_FALTARAM.txt"]
    txt = z.read("_FALTARAM.txt").decode()
    assert "(2 de 3)" in txt
    assert "nº 2 · ATV · CLIENTE DOIS LTDA · competência 09/2026: " + svc.MSG_SEM_PDF in txt
    assert "nº 3 · ATV" in txt and svc_lote.MOTIVO_SEM_RESPOSTA in txt
    # 500 repete sozinho (GET): 1 + 1 + 3 idas.
    assert rota.call_count == 5


@pytest.mark.asyncio
async def test_so_emitida_e_cancelada_vao_a_nfeio(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    emitida = await _nota(db, empresas["atv"], 1)
    outras = [
        await _nota(db, empresas["atv"], 2, status="processando", n_nfse=None),
        await _nota(db, empresas["atv"], 3, status="rejeitada", n_nfse=None),
        await _nota(db, empresas["atv"], 4, status="cancelando"),
        # Empresa sem o id da NFE.io (nem na nota nem no cadastro).
        await _nota(db, empresas["rocha"], 5, cid=None),
    ]
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rota = _servidor(api, "pdf", {_nota_id(1): b"%PDF"})
        ids = [str(emitida.id), *(str(e.id) for e in outras)]
        r = await client.post(URL_ARQUIVOS, json={"ids": ids, "tipo": "pdf"})
    assert r.status_code == 200, r.text
    assert rota.call_count == 1  # só a emitida
    assert _faltaram(r) == [str(e.id) for e in outras]
    txt = _zip(r).read("_FALTARAM.txt").decode()
    for motivo in (
        svc_lote.MOTIVO_NAO_AUTORIZADA,
        svc_lote.MOTIVO_RECUSADA,
        svc_lote.MOTIVO_CANCELANDO,
        svc_lote.MOTIVO_NAO_LIGADA,
    ):
        assert motivo in txt, motivo


@pytest.mark.asyncio
async def test_nenhuma_com_arquivo_e_404(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    e = await _nota(db, empresas["atv"], 1, status="processando")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rota = _servidor(api, "pdf", {})
        for url, body in (
            (URL_ARQUIVOS, {"ids": [str(e.id)], "tipo": "pdf"}),
            (URL_IMPRIMIR, {"ids": [str(e.id), str(uuid.uuid4())]}),
        ):
            r = await client.post(url, json=body)
            assert r.status_code == 404, r.text
            assert r.json()["detail"]["code"] == "sem_arquivo"
            assert "Nenhuma das notas marcadas" in r.json()["detail"]["mensagem"]
    assert not rota.called


@pytest.mark.asyncio
async def test_limites_repetidas_e_nota_que_nao_existe(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    e = await _nota(db, empresas["atv"], 1)
    for body in (
        {"ids": [], "tipo": "pdf"},
        {"ids": [str(uuid.uuid4()) for _ in range(101)], "tipo": "pdf"},
        {"ids": [str(e.id)], "tipo": "ambos"},
        {"ids": [str(e.id)]},
        {"ids": ["nao-e-id"], "tipo": "pdf"},
    ):
        assert (await client.post(URL_ARQUIVOS, json=body)).status_code == 422, body
    for body in ({"ids": []}, {"ids": [str(uuid.uuid4()) for _ in range(101)]}):
        assert (await client.post(URL_IMPRIMIR, json=body)).status_code == 422, body

    sumida = str(uuid.uuid4())
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        rota = _servidor(api, "pdf", {_nota_id(1): b"%PDF"})
        r = await client.post(
            URL_ARQUIVOS, json={"ids": [str(e.id), sumida, str(e.id)], "tipo": "pdf"}
        )
    assert r.status_code == 200, r.text
    assert rota.call_count == 1  # a repetida conta uma vez
    assert r.headers["x-nfse-total"] == "2" and _faltaram(r) == [sumida]
    assert (
        f"nota {sumida}: {svc_lote.MOTIVO_NAO_ENCONTRADA}" in _zip(r).read("_FALTARAM.txt").decode()
    )


@pytest.mark.asyncio
async def test_no_maximo_4_ao_mesmo_tempo_e_uma_linha_por_ida_sem_a_chave(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    notas = [await _nota(db, empresas["atv"], n) for n in range(1, 11)]
    agora = {"ativos": 0, "pico": 0}

    async def _devagar(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "chave-de-teste"
        agora["ativos"] += 1
        agora["pico"] = max(agora["pico"], agora["ativos"])
        await asyncio.sleep(0.03)
        agora["ativos"] -= 1
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(api, "pdf", {_nota_id(n): _devagar for n in range(1, 11)})
        r = await client.post(URL_ARQUIVOS, json={"ids": [str(e.id) for e in notas], "tipo": "pdf"})
    assert r.status_code == 200, r.text
    assert r.headers["x-nfse-ok"] == "10"
    assert 2 <= agora["pico"] <= svc_lote.CONCORRENCIA == 4
    chamadas = (await db.execute(select(NfseChamada))).scalars().all()
    assert len(chamadas) == 10 and {c.operacao for c in chamadas} == {"pdf"}
    assert {c.emissao_id for c in chamadas} == {e.id for e in notas}
    assert all("chave-de-teste" not in json.dumps(c.__dict__, default=str) for c in chamadas)


@pytest.mark.asyncio
async def test_nfeio_lenta_numa_nota_nao_segura_o_lote(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(operador)
    monkeypatch.setattr(svc_lote, "PRAZO_POR_ARQUIVO_S", 0.05)
    rapida = await _nota(db, empresas["atv"], 1)
    lenta = await _nota(db, empresas["atv"], 2)

    async def _trava(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, content=b"%PDF tarde")

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(api, "pdf", {_nota_id(1): b"%PDF", _nota_id(2): _trava})
        r = await client.post(
            URL_ARQUIVOS, json={"ids": [str(rapida.id), str(lenta.id)], "tipo": "pdf"}
        )
    assert r.status_code == 200, r.text
    assert _faltaram(r) == [str(lenta.id)]
    assert svc_lote.MOTIVO_PRAZO in _zip(r).read("_FALTARAM.txt").decode()
    # A ida que estourou o prazo também fica registrada (diagnóstico).
    erros = [
        c.erro
        for c in (await db.execute(select(NfseChamada))).scalars()
        if c.emissao_id == lenta.id
    ]
    assert erros and "prazo" in erros[0]


@pytest.mark.asyncio
async def test_chave_recusada_em_todas_e_503_nao_zip_vazio(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(operador)
    a = await _nota(db, empresas["atv"], 1)
    b = await _nota(db, empresas["atv"], 2)
    parada = await _nota(db, empresas["atv"], 3, status="processando")
    ids = [str(a.id), str(b.id), str(parada.id)]
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        recusa = httpx.Response(401, text="Unauthorized")
        _servidor(api, "pdf", {_nota_id(1): recusa, _nota_id(2): recusa})
        for url, body in (
            (URL_ARQUIVOS, {"ids": ids, "tipo": "pdf"}),
            (URL_IMPRIMIR, {"ids": ids}),
        ):
            r = await client.post(url, json=body)
            assert r.status_code == 503, r.text
            assert r.json()["detail"] == {"code": "chave_nfeio", "mensagem": nfeio.CHAVE_RECUSADA}

    # Sem chave no servidor: o XML guardado ainda sai; o que precisa da NFE.io, não.
    monkeypatch.setattr(get_settings(), "nfeio_api_key", "")
    guardada = await _nota(db, empresas["atv"], 4, xml=b"<Nfse/>")
    with respx.mock(assert_all_mocked=True, assert_all_called=False):
        r = await client.post(URL_ARQUIVOS, json={"ids": [str(a.id)], "tipo": "xml"})
        assert r.status_code == 503 and r.json()["detail"]["mensagem"] == nfeio.CHAVE_AUSENTE
        r = await client.post(
            URL_ARQUIVOS, json={"ids": [str(guardada.id), str(a.id)], "tipo": "xml"}
        )
        assert r.status_code == 200, r.text
        assert _faltaram(r) == [str(a.id)]


@pytest.mark.asyncio
async def test_lote_todo_tem_teto_mesmo_com_a_fila(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """O prazo de cada arquivo só conta depois da vaga: com a NFE.io pendurada,
    100 notas seriam 25 levas de 40 s. O teto do lote corta as que estavam na
    NFE.io e as que ainda esperavam na fila — a que veio sai."""
    auth_as(operador)
    monkeypatch.setattr(svc_lote, "PRAZO_POR_ARQUIVO_S", 30.0)
    monkeypatch.setattr(svc_lote, "PRAZO_TOTAL_S", 0.3)
    rapida = await _nota(db, empresas["atv"], 1)
    penduradas = [await _nota(db, empresas["atv"], n) for n in range(2, 7)]

    async def _pendura(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200, content=b"%PDF tarde")

    comeco = time.monotonic()
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(
            api, "pdf", {_nota_id(1): b"%PDF", **{_nota_id(n): _pendura for n in range(2, 7)}}
        )
        ids = [str(rapida.id), *(str(e.id) for e in penduradas)]
        r = await client.post(URL_ARQUIVOS, json={"ids": ids, "tipo": "pdf"})
    assert time.monotonic() - comeco < 5
    assert r.status_code == 200, r.text
    assert r.headers["x-nfse-ok"] == "1"
    assert _faltaram(r) == [str(e.id) for e in penduradas]
    assert _zip(r).read("_FALTARAM.txt").decode().count(svc_lote.MOTIVO_PRAZO) == 5
    # Só as 4 que chegaram a chamar a NFE.io ficam registradas (a 5ª estava na fila).
    prazos = [c for c in (await db.execute(select(NfseChamada))).scalars() if c.erro]
    assert len(prazos) == svc_lote.CONCORRENCIA and all("prazo" in c.erro for c in prazos)


@pytest.mark.asyncio
async def test_nfeio_fora_do_ar_em_todas_e_502_nao_sem_arquivo(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """A NFE.io não respondeu (500 depois das repetições, ou passou do prazo)
    em todas: é "tente de novo daqui a pouco", igual a baixar uma nota só —
    não "as notas não têm arquivo"."""
    auth_as(operador)
    monkeypatch.setattr(svc_lote, "PRAZO_POR_ARQUIVO_S", 0.05)
    fora = await _nota(db, empresas["atv"], 1)
    lenta = await _nota(db, empresas["atv"], 2)
    parada = await _nota(db, empresas["atv"], 3, status="processando")

    async def _trava(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, content=b"%PDF tarde")

    ids = [str(fora.id), str(lenta.id), str(parada.id)]
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(api, "pdf", {_nota_id(1): httpx.Response(500, text="erro"), _nota_id(2): _trava})
        for url, body in (
            (URL_ARQUIVOS, {"ids": ids, "tipo": "pdf"}),
            (URL_IMPRIMIR, {"ids": ids}),
        ):
            r = await client.post(url, json=body)
            assert r.status_code == 502, r.text
            assert r.json()["detail"]["code"] == "nfeio_sem_resposta"
    # A NFE.io disse 404 (não tem o PDF): aí sim é "sem arquivo".
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(api, "pdf", {})
        r = await client.post(URL_ARQUIVOS, json={"ids": ids[:1], "tipo": "pdf"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "sem_arquivo"


@pytest.mark.asyncio
async def test_conexao_do_banco_fica_livre_enquanto_espera_a_nfeio(
    db: AsyncSession, empresas: dict, monkeypatch
):
    """Fase 2 sem transação aberta: a conexão volta ao pool enquanto a NFE.io
    responde (até 90 s), e as notas continuam valendo na fase 3."""
    e = await _nota(db, empresas["atv"], 1)
    original = svc_lote._buscar_na_rede
    durante: list[bool] = []

    async def _espiando(cliente, itens, tipo):
        durante.append(db.in_transaction())
        await original(cliente, itens, tipo)

    monkeypatch.setattr(svc_lote, "_buscar_na_rede", _espiando)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(api, "xml", {_nota_id(1): b"<Nfse>1</Nfse>"})
        lote = await svc_lote.baixar_lote(db, [e.id], "xml")
    assert durante == [False]
    assert [i.conteudo for i in lote.prontos] == [b"<Nfse>1</Nfse>"]
    await db.refresh(e)
    assert base64.b64decode(e.nfse_xml_b64) == b"<Nfse>1</Nfse>"  # a fase 3 gravou


# --- imprimir: um PDF só ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imprimir_junta_na_ordem_e_pula_o_pdf_corrompido(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    a = await _nota(db, empresas["atv"], 1)
    b = await _nota(db, empresas["atv"], 2)
    c = await _nota(db, empresas["rocha"], 3, cid=CID_ROCHA)
    ruim = await _nota(db, empresas["atv"], 4)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(
            api,
            "pdf",
            {
                _nota_id(1): _pdf("NOTA A"),
                _nota_id(2): _pdf("NOTA B"),
                _nota_id(3): _pdf("NOTA C"),
                _nota_id(4): b"isto nao e um pdf",
            },
        )
        # A ordem é a da seleção, não a do banco.
        ids = [str(c.id), str(ruim.id), str(a.id), str(b.id)]
        r = await client.post(URL_IMPRIMIR, json={"ids": ids})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith('inline; filename="notas-de-servico_')
    assert r.headers["x-nfse-total"] == "4" and r.headers["x-nfse-ok"] == "3"
    assert _faltaram(r) == [str(ruim.id)]
    assert "X-Nfse-Faltaram" in r.headers["access-control-expose-headers"]
    with fitz.open(stream=r.content, filetype="pdf") as doc:
        assert [p.get_text().strip() for p in doc] == ["NOTA C", "NOTA A", "NOTA B"]


@pytest.mark.asyncio
async def test_so_pdf_corrompido_e_404(
    client: AsyncClient,
    db: AsyncSession,
    operador: User,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    auth_as(operador)
    e = await _nota(db, empresas["atv"], 1)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        _servidor(api, "pdf", {_nota_id(1): b"lixo"})
        r = await client.post(URL_IMPRIMIR, json={"ids": [str(e.id)]})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "sem_arquivo"


@pytest.mark.asyncio
async def test_sem_permissao_de_ver_nao_baixa(
    client: AsyncClient,
    db: AsyncSession,
    make_user,
    empresas: dict,
    auth_as: Callable[[User | None], None],
):
    e = await _nota(db, empresas["atv"], 1)
    auth_as(await make_user(permissions={"empresa": {"view": True}}))
    assert (
        await client.post(URL_ARQUIVOS, json={"ids": [str(e.id)], "tipo": "pdf"})
    ).status_code == 403
    assert (await client.post(URL_IMPRIMIR, json={"ids": [str(e.id)]})).status_code == 403


@pytest.mark.asyncio
async def test_baixar_em_lote_fica_no_historico(client, db, make_user):
    """POST que não muda o banco (XML guardado) só vira evento por estar em
    ACOES: "baixou notas de serviço em lote"."""
    from app.historico.nomes import ACOES
    from app.main import app
    from app.models import HistoricoEvento
    from app.security.jwt import issue_session_token
    from app.security.senha_extra import require_nfse_unlock

    eu = await make_user(role=UserRole.ADMIN)
    token, _, _ = issue_session_token(sub=eu.open_id, role=eu.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    atv = Company(razao_social="ATV SERVICOS LTDA", apelido="ATV", cnpj="11222333000181")
    db.add(atv)
    await db.commit()
    e = await _nota(db, atv.id, 1, xml=b"<Nfse/>")

    async def _liberado() -> None:
        return None

    app.dependency_overrides[require_nfse_unlock] = _liberado
    try:
        r = await client.post(URL_ARQUIVOS, json={"ids": [str(e.id)], "tipo": "xml"})
    finally:
        app.dependency_overrides.pop(require_nfse_unlock, None)
    assert r.status_code == 200, r.text
    q = select(HistoricoEvento).where(HistoricoEvento.rota == URL_ARQUIVOS)
    [ev] = (await db.execute(q.execution_options(populate_existing=True))).scalars().all()
    assert ev.acao == ACOES[("POST", URL_ARQUIVOS)]
    assert ev.tela == "Cadastros › Emissão de Serviço"


def test_zip_puro_nomes_repetidos_ganham_sufixo():
    """Mesma empresa, mesmo nº e mês (nota reemitida à mão no painel): _2. Sem
    olhar maiúsculas: no Windows e no Mac "ATV" e "atv" são a mesma pasta."""
    usados: set[str] = set()
    assert svc_lote._unico("ATV/NFSe_1_2026-09.pdf", usados) == "ATV/NFSe_1_2026-09.pdf"
    assert svc_lote._unico("ATV/NFSe_1_2026-09.pdf", usados) == "ATV/NFSe_1_2026-09_2.pdf"
    assert svc_lote._unico("ATV/NFSe_1_2026-09.pdf", usados) == "ATV/NFSe_1_2026-09_3.pdf"
    assert svc_lote._unico("atv/nfse_1_2026-09.pdf", usados) == "atv/nfse_1_2026-09_4.pdf"
    assert svc.slug_arquivo("Rocha Serviços/ÇÃO") == "Rocha-Servicos-CAO"


def test_zip_puro_apelidos_parecidos_nao_dividem_pasta():
    """O apelido não é único: "Loca Fácil", "Loca Facil" e "LOCA-FACIL" são 3
    empresas. Cada uma na sua pasta (a repetida ganha o CNPJ), sem duas que se
    sobrescrevem ao extrair no Windows/Mac."""
    empresas = [
        Company(id=uuid.uuid4(), apelido=apelido, razao_social=apelido, cnpj=cnpj)
        for apelido, cnpj in (
            ("Loca Fácil", "11222333000181"),
            ("Loca Facil", "22333444000155"),
            ("LOCA-FACIL", "33444555000100"),
        )
    ]
    itens = [
        svc_lote.ItemLote(
            id=uuid.uuid4(),
            emissao=NfseEmissao(
                id=uuid.uuid4(),
                company_id=c.id,
                n_nfse="4",
                competencia=date(2026, 9, 1),
                status="emitida",
                nfeio_ambiente="Production",
            ),
            conteudo=f"%PDF {c.cnpj}".encode(),
        )
        for c in empresas
    ]
    lote = svc_lote.Lote(tipo="pdf", itens=itens, empresas={c.id: c for c in empresas})
    z = zipfile.ZipFile(io.BytesIO(svc_lote.montar_zip(lote)))
    assert z.namelist() == [
        "Loca-Facil/NFSe_4_2026-09.pdf",
        "Loca-Facil_22333444000155/NFSe_4_2026-09.pdf",
        "LOCA-FACIL_33444555000100/NFSe_4_2026-09.pdf",
    ]
    assert len({n.lower() for n in z.namelist()}) == 3
    assert z.read("LOCA-FACIL_33444555000100/NFSe_4_2026-09.pdf") == b"%PDF 33444555000100"
