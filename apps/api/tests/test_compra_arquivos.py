"""Contabilidade › Compra — imprimir e baixar a NF de compra (04/11/2026).

Eduardo: "igual emissão de serviço, tem que dar para imprimir a nota etc.". O que
estes testes seguram (`services/compra_arquivos.py`):

- `GET /{id}/pdf`: a DANFE desenhada do XML autorizado (o mesmo desenho do
  Atendimento), inline, com o nome `danfe_nf<nº>.pdf`; sem o XML no DaVinci, 404;
  XML que não desenha, 422 (nunca 500);
- `POST /lote/imprimir`: um PDF só, na ordem pedida; a compra sem XML fica de fora
  e vai nos cabeçalhos X-Nfse-Total/Ok/Faltaram (os mesmos da NFS-e);
- `POST /lote/arquivos`: .zip com uma pasta por compradora, nome repetido ganha
  _2, e o `_FALTARAM.txt` diz o porquê de cada uma que ficou de fora;
- só NF EMITIDA entra no lote (cancelada não: a NF foi desfeita no Bling), e a
  DANFE avulsa da cancelada também não sai;
- só sai o XML AUTORIZADO da NF DESSA compra (corretor 04/11): emitente = vendedora,
  destinatário = compradora, cStat 100 e DigestValue = digVal — senão 409 (avulso)
  ou "faltou" (lote); anotar chave de NF de outra emitente é recusado;
- juntar 100 DANFEs não para o laço da API (1 processo: travaria tudo);
- imprimir/baixar em lote fica no Histórico (nada muda no banco: vai pela frase).
"""

from __future__ import annotations

import asyncio
import io
import uuid
import zipfile

import pymupdf
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compra import ETAPAS
from app.models.nf import NfNota
from app.services import compra_arquivos as arquivos
from app.services.atendimento import danfe
from tests.test_compra_emissao import (
    AGENTE,
    CHAVE,
    H,
    _code,
    _com_o_robo,
    _etapa,
    _ok,
    _registrar,
    _robo,
    _xml,
)
from tests.test_nfse_compras import (
    BASE,
    CNPJ_BEZERRA,
    CNPJ_RIO,
    CNPJ_TELES,
    cenario,
    operador,
)

# Fixtures dos outros testes da Compra (o pytest acha pelo nome): VITA, RIO,
# BEZERRA automática, TELES manual; token do robô e relógio dentro da janela.
_FIXTURES = (cenario, operador, _robo)

CHAVE_TELES = CHAVE[:6] + CNPJ_TELES + CHAVE[20:]  # outra vendedora, o MESMO nº 3031


def _chave(nnf: int) -> str:
    """A CHAVE padrão com outro nº de NF (posições 26–34)."""
    return CHAVE[:25] + f"{nnf:09d}" + CHAVE[34:]


async def _emitida_pelo_robo(client: AsyncClient, cen: dict, **kw) -> dict:
    """A compra que o robô emitiu: NF 3031 da BEZERRA para a VITA, XML conferido em nf_nota."""
    c, _ = await _com_o_robo(client, cen, **kw)
    for e in ETAPAS[1:-1]:
        extra = {"nf_numero": "3031"} if e == "nf_gerada" else {}
        assert await _etapa(client, c["id"], e, **extra) == 200, e
    r = await client.post(f"{AGENTE}/{c['id']}/resultado", headers=H, json=_ok())
    assert r.status_code == 200, r.text
    return c


async def _anotada(
    client: AsyncClient,
    db: AsyncSession,
    cen: dict,
    nf: str,
    *,
    chave: str | None = None,
    xml: bytes | None = None,
    **kw,
) -> dict:
    """Compra com a NF anotada à mão (com ou sem a chave); `xml` = o coletor já subiu."""
    c = await _registrar(client, cen, **kw)
    if xml is not None:
        assert chave
        emitente = kw.get("vendedor_cnpj", CNPJ_BEZERRA)
        db.add(NfNota(chave=chave, numero=nf, emitente_cnpj=emitente, xml=xml))
        await db.commit()
    corpo = {"nf_numero": nf, **({"nf_chave": chave} if chave else {})}
    r = await client.post(f"{BASE}/{c['id']}/nf", json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


def _paginas(pdf: bytes) -> list[str]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [p.get_text() for p in doc]


def _zip(conteudo: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        return {n: z.read(n) for n in z.namelist()}


# --- uma compra ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_danfe_da_compra_abre_na_aba(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    c = await _emitida_pelo_robo(client, cenario)
    r = await client.get(f"{BASE}/{c['id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'inline; filename="danfe_nf3031.pdf"'
    paginas = _paginas(r.content)
    assert len(paginas) == 1 and "DANFE" in paginas[0]
    assert danfe.chave_formatada(CHAVE) in paginas[0]
    # o mesmo desenho do Atendimento, do XML que o robô devolveu (determinístico)
    assert r.content == danfe.gerar_pdf(_xml())

    # sem NF, NF anotada sem a chave, chave sem o XML no DaVinci: 404 (nada a desenhar)
    sem_nf = await _registrar(client, cenario)
    sem_chave = await _anotada(client, db, cenario, "77")
    sem_xml = await _anotada(client, db, cenario, "3040", chave=_chave(3040))
    for x in (sem_nf, sem_chave, sem_xml):
        r = await client.get(f"{BASE}/{x['id']}/pdf")
        assert r.status_code == 404 and _code(r) == "sem_xml", (x["numero"], r.text)
    r = await client.get(f"{BASE}/{uuid.uuid4()}/pdf")
    assert r.status_code == 404 and _code(r) == "compra_nao_encontrada"

    # XML guardado que não é NF-e: 422 com o motivo, nunca 500
    ruim = await _anotada(client, db, cenario, "3041", chave=_chave(3041), xml=b"<nada/>")
    r = await client.get(f"{BASE}/{ruim['id']}/pdf")
    assert r.status_code == 422 and _code(r) == "danfe_invalida"
    assert r.json()["detail"]["erros"] == ["xml_sem_nfe"]

    # quem só VÊ também imprime (como o XML)
    so_ve = operador
    so_ve.permissions = {"emissao_servico": {"view": True}}
    await db.commit()
    auth_as(so_ve)
    assert (await client.get(f"{BASE}/{c['id']}/pdf")).status_code == 200


def test_nome_do_arquivo_sai_do_numero_ou_da_chave():
    from app.models.compra import CompraPedido

    def p(numero: str | None, chave: str | None) -> CompraPedido:
        return CompraPedido(nf_numero=numero, nf_chave=chave)

    assert arquivos.nome_pdf(p("3031", None)) == "danfe_nf3031.pdf"
    assert arquivos.nome_xml(p(None, _chave(3044))) == "nfe_3044.xml"
    assert arquivos.nome_pdf(p(None, None)) == "danfe_nfsem-numero.pdf"


# --- lote: imprimir -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lote_imprimir_junta_as_danfes_na_ordem(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    a = await _emitida_pelo_robo(client, cenario)  # VITA, NF 3031
    b = await _anotada(
        client,
        db,
        cenario,
        "3032",
        chave=_chave(3032),
        xml=_xml(chave=_chave(3032), nnf="3032", dest=CNPJ_RIO),
        company_id=str(cenario["rio"]),
    )
    sem_chave = await _anotada(client, db, cenario, "77")
    sem_nf = await _registrar(client, cenario)
    sumida = str(uuid.uuid4())
    # a repetida conta uma vez; a ordem é a pedida (é a ordem das folhas)
    ids = [b["id"], a["id"], sem_chave["id"], sem_nf["id"], a["id"], sumida]
    r = await client.post(f"{BASE}/lote/imprimir", json={"ids": ids})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith('inline; filename="nfs-de-compra_')
    assert (r.headers["x-nfse-total"], r.headers["x-nfse-ok"]) == ("5", "2")
    assert r.headers["x-nfse-faltaram"].split(",") == [sem_chave["id"], sem_nf["id"], sumida]
    assert "X-Nfse-Faltaram" in r.headers["access-control-expose-headers"]
    paginas = _paginas(r.content)
    assert len(paginas) == 2
    assert danfe.chave_formatada(_chave(3032)) in paginas[0]
    assert danfe.chave_formatada(CHAVE) in paginas[1]

    # todas juntas também: N compras, N folhas, nada faltou
    r = await client.post(f"{BASE}/lote/imprimir", json={"ids": [a["id"], b["id"]]})
    assert r.status_code == 200 and r.headers["x-nfse-faltaram"] == ""
    assert len(_paginas(r.content)) == 2

    # nenhuma com XML: 404 com a frase; a única com XML que não desenha: 422
    r = await client.post(f"{BASE}/lote/imprimir", json={"ids": [sem_chave["id"], sem_nf["id"]]})
    assert r.status_code == 404 and _code(r) == "sem_arquivo"
    ruim = await _anotada(client, db, cenario, "3041", chave=_chave(3041), xml=b"<nada/>")
    r = await client.post(f"{BASE}/lote/imprimir", json={"ids": [ruim["id"]]})
    assert r.status_code == 422 and _code(r) == "danfe_invalida"
    # ... e no meio de outras, fica de fora sem derrubar a impressão
    r = await client.post(f"{BASE}/lote/imprimir", json={"ids": [ruim["id"], a["id"]]})
    assert r.status_code == 200 and r.headers["x-nfse-faltaram"] == ruim["id"]
    assert len(_paginas(r.content)) == 1

    # vazio ou mais de 100: o schema recusa (igual à NFS-e)
    assert (await client.post(f"{BASE}/lote/imprimir", json={"ids": []})).status_code == 422
    muitas = [str(uuid.uuid4()) for _ in range(arquivos.MAX_LOTE + 1)]
    assert (await client.post(f"{BASE}/lote/imprimir", json={"ids": muitas})).status_code == 422


# --- lote: .zip -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lote_arquivos_zip_por_compradora_com_faltaram(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    a = await _emitida_pelo_robo(client, cenario)  # VITA, NF 3031 da BEZERRA
    xml_b = _xml(chave=_chave(3032), nnf="3032", dest=CNPJ_RIO)
    b = await _anotada(
        client, db, cenario, "3032", chave=_chave(3032), xml=xml_b, company_id=str(cenario["rio"])
    )
    # VITA de novo, NF 3031 da TELES: o nº da NF é por vendedora — não sobrescreve a da BEZERRA
    xml_t = _xml(chave=CHAVE_TELES, emit=CNPJ_TELES)
    t = await _anotada(
        client, db, cenario, "3031", chave=CHAVE_TELES, xml=xml_t, vendedor_cnpj=CNPJ_TELES
    )
    sem_chave = await _anotada(client, db, cenario, "77")
    ids = [a["id"], b["id"], t["id"], sem_chave["id"]]

    r = await client.post(f"{BASE}/lote/arquivos", json={"ids": ids, "tipo": "xml"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="nfs-de-compra_xml_')
    assert (r.headers["x-nfse-total"], r.headers["x-nfse-ok"]) == ("4", "3")
    assert r.headers["x-nfse-faltaram"] == sem_chave["id"]
    z = _zip(r.content)
    assert list(z) == [
        "vita/nfe_3031.xml",
        "rio/nfe_3032.xml",
        "vita/nfe_3031_2.xml",
        "_FALTARAM.txt",
    ]
    assert z["vita/nfe_3031.xml"] == _xml() and z["rio/nfe_3032.xml"] == xml_b
    assert z["vita/nfe_3031_2.xml"] == xml_t
    faltaram = z["_FALTARAM.txt"].decode()
    assert "(1 de 4)" in faltaram
    assert f"compra nº {sem_chave['numero']} · vita → " in faltaram
    assert "NF 77: " + arquivos.MOTIVO_SEM_CHAVE in faltaram

    r = await client.post(f"{BASE}/lote/arquivos", json={"ids": ids, "tipo": "pdf"})
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith('attachment; filename="nfs-de-compra_pdf_')
    z = _zip(r.content)
    assert list(z) == [
        "vita/danfe_nf3031.pdf",
        "rio/danfe_nf3032.pdf",
        "vita/danfe_nf3031_2.pdf",
        "_FALTARAM.txt",
    ]
    assert z["vita/danfe_nf3031.pdf"] == danfe.gerar_pdf(_xml())
    assert all(v.startswith(b"%PDF") for k, v in z.items() if k.endswith(".pdf"))

    # nada faltou: sem _FALTARAM.txt
    r = await client.post(f"{BASE}/lote/arquivos", json={"ids": [b["id"]], "tipo": "xml"})
    assert list(_zip(r.content)) == ["rio/nfe_3032.xml"]
    assert r.headers["x-nfse-faltaram"] == ""
    # tipo que não existe: 422
    r = await client.post(f"{BASE}/lote/arquivos", json={"ids": [b["id"]], "tipo": "exe"})
    assert r.status_code == 422

    # NF desfeita (compra cancelada, chave guardada): o XML avulso continua baixando,
    # mas ela não entra no lote — a NF foi cancelada no Bling
    r = await client.post(f"{BASE}/{a['id']}/desfazer-nf", params={"conferido": 1})
    assert r.status_code == 200 and r.json()["status"] == "cancelada"
    assert (await client.get(f"{BASE}/{a['id']}/xml")).status_code == 200
    # a DANFE avulsa dela sairia sem marca nenhuma (o XML não sabe do cancelamento)
    r = await client.get(f"{BASE}/{a['id']}/pdf")
    assert r.status_code == 409 and _code(r) == "compra_cancelada", r.text
    r = await client.post(f"{BASE}/lote/arquivos", json={"ids": [a["id"]], "tipo": "xml"})
    assert r.status_code == 404 and _code(r) == "sem_arquivo"
    r = await client.post(f"{BASE}/lote/arquivos", json={"ids": [a["id"], b["id"]], "tipo": "pdf"})
    assert r.status_code == 200
    assert arquivos.MOTIVO_CANCELADA in _zip(r.content)["_FALTARAM.txt"].decode()


# --- só o XML autorizado da NF da compra (corretor 04/11) ----------------------------------

CPF_CONSUMIDOR = "12345678909"


@pytest.mark.asyncio
async def test_so_sai_o_xml_autorizado_da_nf_da_compra(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    a = await _emitida_pelo_robo(client, cenario)  # a boa, para o lote
    # o coletor sobrescreveu o XML com o de uma NF reenviada depois de autorizada (19/10)
    div = await _anotada(
        client,
        db,
        cenario,
        "3050",
        chave=_chave(3050),
        xml=_xml(chave=_chave(3050), nnf="3050", digest="outro-digest="),
    )
    # NF denegada (ou sem autorização): a DANFE sairia como se valesse
    den = await _anotada(
        client,
        db,
        cenario,
        "3051",
        chave=_chave(3051),
        xml=_xml(chave=_chave(3051), nnf="3051", cstat="110"),
    )
    # chave da MESMA vendedora, mas de uma NF de venda para um consumidor (CPF)
    venda = await _anotada(
        client,
        db,
        cenario,
        "3052",
        chave=_chave(3052),
        xml=_xml(chave=_chave(3052), nnf="3052", dest=CPF_CONSUMIDOR),
    )
    casos = ((div, "xml_divergente"), (den, "nf_nao_autorizada"), (venda, "xml_de_outra_nf"))
    for c, code in casos:
        for tipo in ("pdf", "xml"):
            r = await client.get(f"{BASE}/{c['id']}/{tipo}")
            assert r.status_code == 409 and _code(r) == code, (code, tipo, r.text)
            assert CPF_CONSUMIDOR not in r.text
    # no lote ficam de fora, com o motivo; a boa sai sozinha
    ids = [div["id"], a["id"], den["id"], venda["id"]]
    r = await client.post(f"{BASE}/lote/imprimir", json={"ids": ids})
    assert r.status_code == 200, r.text
    assert (r.headers["x-nfse-total"], r.headers["x-nfse-ok"]) == ("4", "1")
    assert r.headers["x-nfse-faltaram"].split(",") == [div["id"], den["id"], venda["id"]]
    paginas = _paginas(r.content)
    assert len(paginas) == 1 and danfe.chave_formatada(CHAVE) in paginas[0]
    for tipo in ("pdf", "xml"):
        r = await client.post(f"{BASE}/lote/arquivos", json={"ids": ids, "tipo": tipo})
        assert r.status_code == 200, r.text
        z = _zip(r.content)
        assert len(z) == 2 and all(CPF_CONSUMIDOR.encode() not in v for v in z.values())
        faltaram = z["_FALTARAM.txt"].decode()
        assert f"NF 3050: {arquivos.MOTIVO_DIVERGENTE}" in faltaram
        assert f"NF 3051: {arquivos.MOTIVO_NAO_AUTORIZADA.format(cstat='110')}" in faltaram
        assert f"NF 3052: {arquivos.MOTIVO_OUTRA_NF}" in faltaram
    # só as ruins: 404 que diz o porquê
    r = await client.post(f"{BASE}/lote/imprimir", json={"ids": [div["id"]]})
    assert r.status_code == 404 and _code(r) == "sem_arquivo"
    assert arquivos.MOTIVO_DIVERGENTE in r.json()["detail"]["mensagem"]


@pytest.mark.asyncio
async def test_anotar_chave_de_nf_de_outra_emitente_e_recusado(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    """A chave tem o CNPJ de quem emitiu: a de uma NF da RIO (venda para CPF, por
    exemplo) anotada numa compra da BEZERRA abriria a DANFE/XML dela por aqui."""
    auth_as(operador)
    c = await _registrar(client, cenario)  # BEZERRA → VITA
    chave_rio = CHAVE[:6] + CNPJ_RIO + CHAVE[20:]
    db.add(
        NfNota(
            chave=chave_rio,
            numero="3031",
            emitente_cnpj=CNPJ_RIO,
            xml=_xml(chave=chave_rio, emit=CNPJ_RIO, dest=CPF_CONSUMIDOR),
        )
    )
    await db.commit()
    r = await client.post(f"{BASE}/{c['id']}/nf", json={"nf_numero": "3031", "nf_chave": chave_rio})
    assert r.status_code == 409 and _code(r) == "chave_de_outra_emitente", r.text
    r = await client.get(f"{BASE}/{c['id']}")
    assert r.json()["status"] == "registrada" and r.json()["nf_chave"] is None
    r = await client.get(f"{BASE}/{c['id']}/pdf")
    assert r.status_code == 404 and _code(r) == "sem_xml"
    # a da própria vendedora passa
    r = await client.post(f"{BASE}/{c['id']}/nf", json={"nf_numero": "3031", "nf_chave": CHAVE})
    assert r.status_code == 200 and r.json()["nf_chave"] == CHAVE


# --- não trava a API ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_juntar_100_danfes_nao_para_o_laco():
    """A API roda num processo só: juntar o lote não pode segurar o GIL (o garbage>=3
    do PyMuPDF segurava ~20 s com 100 DANFEs — robô, webhooks e telas parados)."""
    pdf = danfe.gerar_pdf(_xml())
    lote = arquivos.Lote(
        tipo="pdf",
        itens=[arquivos.ItemLote(id=uuid.uuid4(), conteudo=pdf) for _ in range(arquivos.MAX_LOTE)],
    )
    laco = asyncio.get_running_loop()
    atrasos: list[float] = []
    fim = asyncio.Event()

    async def relogio() -> None:
        while not fim.is_set():
            t = laco.time()
            await asyncio.sleep(0.01)
            atrasos.append(laco.time() - t - 0.01)

    tarefa = asyncio.create_task(relogio())
    await asyncio.sleep(0.05)
    try:
        saida = await arquivos.juntar_pdfs(lote)
    finally:
        fim.set()
        await tarefa
    assert len(_paginas(saida)) == arquivos.MAX_LOTE
    assert max(atrasos) < 0.5, f"laço parado {max(atrasos):.2f} s"


# --- trava e Histórico ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_e_lote_exigem_a_chave_da_pagina(client: AsyncClient, operador, auth_as, cenario):
    """Mesma trava do XML da compra: a chave INTEIRA (não há link de 60 s)."""
    from app.main import app
    from app.security import senha_extra
    from app.security.senha_extra import require_nfse_unlock

    auth_as(operador)
    c = await _emitida_pelo_robo(client, cenario)
    desvio = app.dependency_overrides.pop(require_nfse_unlock, None)
    try:
        for metodo, caminho, corpo in (
            ("GET", f"{BASE}/{c['id']}/pdf", None),
            ("POST", f"{BASE}/lote/imprimir", {"ids": [c["id"]]}),
            ("POST", f"{BASE}/lote/arquivos", {"ids": [c["id"]], "tipo": "pdf"}),
        ):
            r = await client.request(metodo, caminho, json=corpo)
            assert r.status_code == 401 and _code(r) == "nfse_locked", (caminho, r.text)
            # nem um link de arquivo VÁLIDO (o de 60 s da NFS-e, assinado agora para
            # esse id) no endereço abre: aqui só vale a chave inteira no cabeçalho
            link = senha_extra.fazer_link_arquivo("nfse", f"{c['id']}:pdf")
            assert senha_extra.link_arquivo_valido(link, "nfse", f"{c['id']}:pdf")
            r = await client.request(metodo, caminho, json=corpo, params={"chave": link})
            assert r.status_code == 401 and _code(r) == "nfse_locked", caminho
        # com a chave inteira, abre (a trava é a da página, não outra coisa)
        chave_pagina, _ = senha_extra.fazer_token("nfse")
        r = await client.get(f"{BASE}/{c['id']}/pdf", headers={"X-Nfse-Token": chave_pagina})
        assert r.status_code == 200, r.text
    finally:
        if desvio is not None:
            app.dependency_overrides[require_nfse_unlock] = desvio


@pytest.mark.asyncio
async def test_imprimir_e_baixar_em_lote_ficam_no_historico(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    """Nada muda no banco: o Histórico registra pela frase de `nomes.ACOES`."""
    from app.config import get_settings
    from app.main import app
    from app.models import HistoricoEvento, UserStatus
    from app.security.jwt import issue_session_token
    from app.security.senha_extra import require_nfse_unlock

    auth_as(operador)
    c = await _emitida_pelo_robo(client, cenario)
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

    r = await client.post(f"{BASE}/lote/imprimir", json={"ids": [c["id"]]})
    assert r.status_code == 200, r.text
    r = await client.post(f"{BASE}/lote/arquivos", json={"ids": [c["id"]], "tipo": "xml"})
    assert r.status_code == 200, r.text
    for caminho, frase in (
        (f"{BASE}/lote/imprimir", "imprimiu NFs de compra em lote"),
        (f"{BASE}/lote/arquivos", "baixou NFs de compra em lote (PDF/XML)"),
    ):
        ev = (
            (
                await db.execute(
                    select(HistoricoEvento).where(
                        HistoricoEvento.metodo == "POST", HistoricoEvento.caminho == caminho
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(ev) == 1 and ev[0].acao == frase, (caminho, ev)
