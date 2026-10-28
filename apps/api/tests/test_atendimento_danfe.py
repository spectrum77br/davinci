"""A NF do pedido em PDF para anexar na resposta (28/10/2026, `danfe.py`).

O que estes testes seguram:

- a DANFE sai do XML autorizado com a MESMA chave, número, série, protocolo e
  valores (é a nota do Bling, só desenhada aqui); código de barras CODE-128C
  da chave com o dígito de controle certo; muitos itens viram mais folhas;
  homologação leva "SEM VALOR FISCAL";
- a escolha da nota: só a CHEIA (≥ 60% do pedido), autorizada; a de
  embalagem nunca; sem pedido / sem NF / só embalagem dizem o porquê;
- a rota GET /conversas/{id}/nf devolve o PDF (ou 404 com a frase) e o
  painel diz o número da NF e como o PDF chega em cada plataforma.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pymupdf
import pytest

from app.models import (
    AtendimentoCanal,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    NfNota,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import danfe, foto, gravar, painel

URL = "/api/atendimento"
CHAVE = "3526" + "10" + "00000000000191" + "55" + "001" + "000001883" + "1" + "00000001" + "0"


def _xml(
    *,
    chave: str = CHAVE,
    numero: str = "1883",
    itens: int = 1,
    tp_amb: str = "1",
    v_nf: str = "424.00",
    protocolo: bool = True,
    tp_nf: str = "1",
    fin_nfe: str = "1",
    duplicatas: int = 0,
) -> bytes:
    cobr = (
        "<cobr>"
        + "".join(
            f"<dup><nDup>{i:03d}</nDup><dVenc>2026-11-{i:02d}</dVenc><vDup>141.33</vDup></dup>"
            for i in range(1, duplicatas + 1)
        )
        + "</cobr>"
        if duplicatas
        else ""
    )
    det = "".join(
        f"""<det nItem="{i}"><prod><cProd>b096.{i}</cProd><xProd>Mala de teste {i} com um nome
        comprido o bastante para quebrar a linha da descrição</xProd><NCM>42021210</NCM>
        <CFOP>5102</CFOP><uCom>UN</uCom><qCom>1.0000</qCom><vUnCom>424.0000000000</vUnCom>
        <vProd>424.00</vProd></prod><imposto><ICMS><ICMSSN102><orig>0</orig><CSOSN>102</CSOSN>
        </ICMSSN102></ICMS></imposto></det>"""
        for i in range(1, itens + 1)
    )
    prot = (
        f"""<protNFe versao="4.00"><infProt><tpAmb>{tp_amb}</tpAmb><chNFe>{chave}</chNFe>
        <dhRecbto>2026-10-27T12:19:02-03:00</dhRecbto><nProt>135260000000001</nProt>
        <cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe>"""
        if protocolo
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe{chave}"
versao="4.00"><ide><natOp>Venda de mercadoria</natOp><serie>1</serie><nNF>{numero}</nNF>
<dhEmi>2026-10-27T12:19:00-03:00</dhEmi><dhSaiEnt>2026-10-27T12:19:08-03:00</dhSaiEnt>
<tpNF>{tp_nf}</tpNF><tpAmb>{tp_amb}</tpAmb><finNFe>{fin_nfe}</finNFe></ide><emit><CNPJ>00000000000191</CNPJ><xNome>Empresa
Teste Ltda</xNome><enderEmit><xLgr>Avenida Teste</xLgr><nro>54</nro><xBairro>Centro</xBairro>
<xMun>Sao Paulo</xMun><UF>SP</UF><CEP>01001000</CEP></enderEmit><IE>111111111111</IE></emit>
<dest><CPF>12345678909</CPF><xNome>Cliente Teste</xNome><enderDest><xLgr>Rua Um</xLgr>
<nro>1</nro><xBairro>Bairro</xBairro><xMun>Sao Paulo</xMun><UF>SP</UF><CEP>01002000</CEP>
</enderDest></dest>{det}{cobr}<total><ICMSTot><vBC>0.00</vBC><vICMS>0.00</vICMS><vBCST>0.00</vBCST>
<vST>0.00</vST><vProd>{v_nf}</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc>
<vIPI>0.00</vIPI><vOutro>0.00</vOutro><vNF>{v_nf}</vNF><vTotTrib>159.21</vTotTrib></ICMSTot>
</total><transp><modFrete>0</modFrete></transp><infAdic><infCpl>Pedido de teste</infCpl>
</infAdic></infNFe></NFe>{prot}</nfeProc>""".encode()


def _texto(pdf: bytes) -> str:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return "\n".join(p.get_text() for p in doc)


# ─────────────── o desenho ───────────────


def test_danfe_traz_a_mesma_nota_do_xml():
    pdf = danfe.gerar_pdf(_xml())
    # O mesmo XML, os mesmos bytes: a trava de anexo repetido (sha256) pega a NF.
    assert danfe.gerar_pdf(_xml()) == pdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert doc.page_count == 1
    texto = _texto(pdf)
    assert "DANFE" in texto
    assert danfe.chave_formatada(CHAVE) in texto
    # O padrão visual do Bling (28/10/2026): "Nº 001883", "SERIE: 1", protocolo
    # com o fuso, CEP "01.002-000", CFOP "5.102", os blocos com os nomes do Bling.
    assert "Nº 001883" in texto and "SERIE: 1" in texto and "Série 1" in texto
    assert "135260000000001 27/10/2026 12:19:02-03:00" in texto
    assert "01.002-000" in texto and "5.102" in texto
    for bloco in (
        "Destinatário/Remetente",
        "Faturas",
        "Cálculo do imposto",
        "Transportador/Volumes transportados",
        "Itens da nota fiscal",
        "Cálculo do ISSQN",
        "Dados adicionais",
        "Controle do Fisco",
    ):
        assert bloco in texto, bloco
    assert doc[0].rect.width == 612 and doc[0].rect.height == 792  # folha carta, como o Bling
    assert "00.000.000/0001-91" in texto and "123.456.789-09" in texto
    assert "424,00" in texto and "Venda de mercadoria" in texto
    assert "SEM VALOR FISCAL" not in texto


def test_code128c_com_digito_de_controle():
    # "12" → início C (105) + 12 + controle (105 + 1×12) % 103 = 14 + fim.
    larguras = danfe.code128c("12")
    esperado = "211232" + "112232" + "122231" + "2331112"
    assert "".join(map(str, larguras)) == esperado
    assert sum(danfe.code128c(CHAVE)) == 11 * 24 + 13
    with pytest.raises(ValueError):
        danfe.code128c("123")


def test_muitos_itens_viram_mais_folhas_e_homologacao_sem_valor():
    pdf = danfe.gerar_pdf(_xml(itens=40))
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert doc.page_count >= 2
    assert "Página: 2 de" in doc[1].get_text()
    # A última folha leva o ISSQN e os dados adicionais (e nunca a tabela vazia).
    assert "Dados adicionais" in doc[-1].get_text() and "Mala de teste" in doc[-1].get_text()
    assert "SEM VALOR FISCAL" in _texto(danfe.gerar_pdf(_xml(tp_amb="2")))
    assert "SEM VALOR FISCAL" in _texto(danfe.gerar_pdf(_xml(protocolo=False)))


def test_xml_que_nao_e_nfe_e_recusado():
    with pytest.raises(danfe.DanfeInvalida):
        danfe.gerar_pdf(b"<nada/>")
    with pytest.raises(danfe.DanfeInvalida):
        danfe.gerar_pdf(b"isto nao e xml")


def test_pdf_vira_imagem_so_onde_o_chat_nao_aceita_arquivo():
    pdf = danfe.gerar_pdf(_xml())
    anexo = foto.validar_foto(pdf, "NF_1883.pdf", "application/pdf")
    assert (anexo.mime, anexo.nome) == (foto.PDF, "NF_1883.pdf")
    imagem = foto.pdf_em_imagem(anexo)
    assert imagem.mime == "image/jpeg" and imagem.nome == "NF_1883.jpg"
    assert imagem.conteudo.startswith(b"\xff\xd8\xff")
    largura, altura = foto.dimensoes(imagem.conteudo)
    assert altura > largura and largura * altura <= foto.MAX_PIXELS_PDF_IMAGEM * 1.05


def test_pdf_com_senha_ou_quebrado_e_recusado():
    with pytest.raises(foto.FotoInvalida) as e:
        foto.validar_foto(b"%PDF-1.7 lixo", "a.pdf")
    assert e.value.code == "pdf_invalido"
    doc = pymupdf.open()
    doc.new_page()
    trancado = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="dono", user_pw="cliente"
    )
    with pytest.raises(foto.FotoInvalida) as e:
        foto.validar_foto(trancado, "a.pdf")
    assert e.value.code == "pdf_senha"


def test_pdf_grande_demais_para_virar_imagem():
    doc = pymupdf.open()
    for _ in range(foto.MAX_PAGINAS_IMAGEM + 1):
        doc.new_page()
    anexo = foto.validar_foto(doc.tobytes(), "a.pdf")
    with pytest.raises(foto.FotoInvalida) as e:
        foto.pdf_em_imagem(anexo)
    assert e.value.code == "pdf_paginas"


# ─────────────── qual nota ───────────────

AGORA = datetime.now(UTC)


async def _conversa(db, make_user, *, plataforma="shopee", canal="chat", pedido="250925NF"):
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform(plataforma),
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    c = AtendimentoCanal(
        integration_id=integ.id, plataforma=plataforma, canal=canal, modo="humano", status="ok"
    )
    db.add(c)
    await db.flush()
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=c,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal,
        externo_id=f"conv-{pedido}",
        comprador_id="555",
        pedido_marketplace=pedido,
    )
    await db.commit()
    return conversa


async def _pedido(db, numero: str, numeroloja: str, total: str, situacao: str = "6") -> None:
    db.add(
        BlingOrder(
            bling_id=int(numero),
            item_index=0,
            numero=numero,
            numeroloja=numeroloja,
            total=Decimal(total),
            item_codigo="b096.20",
            situacao=situacao,
            data=AGORA,
        )
    )
    await db.commit()


async def _nota(
    db, pedido: str, numero: str, valor: str, *, chave: str, quando=AGORA, tp_nf: str = "1"
):
    db.add(
        NfNota(
            chave=chave,
            pedido_bling=pedido,
            numero=numero,
            serie="1",
            valor=Decimal(valor),
            data_emissao=quando,
            situacao="100",
            xml=_xml(chave=chave, numero=numero, v_nf=valor, tp_nf=tp_nf),
        )
    )
    await db.commit()


async def test_escolhe_a_nota_cheia_nunca_a_de_embalagem(db, make_user):
    conversa = await _conversa(db, make_user, pedido="250925NF1")
    await _pedido(db, "900101", "250925NF1", "400.00")
    # A de embalagem é a MAIS NOVA (o lote da noite pode vir em qualquer ordem).
    await _nota(db, "900101", "11", "400.00", chave="1" * 44, quando=AGORA - timedelta(hours=2))
    await _nota(db, "900101", "12", "4.00", chave="2" * 44)
    nf = await danfe.nf_da_conversa(db, conversa)
    assert (nf.numero, nf.pedido_bling, nf.nome_arquivo) == ("11", "900101", "NF_11.pdf")
    texto = _texto(await danfe.pdf_da_nf(db, nf))
    assert danfe.chave_formatada("1" * 44) in texto


async def test_sem_pedido_sem_nf_ou_so_embalagem_dizem_o_porque(db, make_user):
    sem_pedido = await _conversa(db, make_user, pedido="")
    with pytest.raises(danfe.NfIndisponivel) as e:
        await danfe.nf_da_conversa(db, sem_pedido)
    assert e.value.code == "nf_sem_pedido"

    sem_nf = await _conversa(db, make_user, pedido="250925NF2")
    await _pedido(db, "900102", "250925NF2", "100.00")
    with pytest.raises(danfe.NfIndisponivel) as e:
        await danfe.nf_da_conversa(db, sem_nf)
    assert e.value.code == "nf_nao_chegou" and "Bling" in e.value.detail

    so_embalagem = await _conversa(db, make_user, pedido="250925NF3")
    await _pedido(db, "900103", "250925NF3", "100.00")
    await _nota(db, "900103", "13", "1.00", chave="3" * 44)
    # Faixa do meio (45%): o DaVinci não escolhe.
    await _nota(db, "900103", "14", "45.00", chave="4" * 44)
    with pytest.raises(danfe.NfIndisponivel) as e:
        await danfe.nf_da_conversa(db, so_embalagem)
    assert e.value.code == "nf_so_embalagem"


# ─────────────── rota e painel ───────────────


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def test_rota_devolve_o_pdf_ou_404(db, client, admin, make_user):
    conversa = await _conversa(db, make_user, pedido="250925NF4")
    r = await client.get(f"{URL}/conversas/{conversa.id}/nf")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nf_nao_chegou" or (
        r.json()["detail"]["code"] == "nf_sem_pedido"
    )

    await _pedido(db, "900104", "250925NF4", "250.00")
    await _nota(db, "900104", "15", "250.00", chave="5" * 44)
    r = await client.get(f"{URL}/conversas/{conversa.id}/nf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-nf-numero"] == "15"
    assert 'filename="NF_15.pdf"' in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-")


async def test_painel_diz_a_nf_e_como_o_pdf_chega(db, make_user):
    shopee = await _conversa(db, make_user, pedido="250925NF5")
    await _pedido(db, "900105", "250925NF5", "80.00")
    await _nota(db, "900105", "16", "80.00", chave="6" * 44)
    s = await painel.situacao_da_foto(db, shopee)
    assert (s["pdf"], s["nf_numero"], s["nf_motivo"]) == ("imagem", "16", None)

    ml = await _conversa(db, make_user, plataforma="ml", canal="pos_venda", pedido="2000001")
    s = await painel.situacao_da_foto(db, ml)
    assert s["pdf"] == "arquivo" and s["nf_numero"] is None and s["nf_motivo"]

    amazon = await _conversa(db, make_user, plataforma="amazon", canal="email", pedido="701-1")
    s = await painel.situacao_da_foto(db, amazon)
    assert s["pdf"] is None and "Seller Central" in s["motivo"]


async def test_devolucao_cancelado_e_carrinho_ficam_com_a_pessoa(db, make_user):
    # A NF de devolução (entrada, valor cheio, a MAIS NOVA) nunca é a escolhida.
    devolucao = await _conversa(db, make_user, pedido="250925NF6")
    await _pedido(db, "900106", "250925NF6", "300.00")
    await _nota(db, "900106", "17", "300.00", chave="7" * 44, quando=AGORA - timedelta(days=9))
    await _nota(db, "900106", "18", "300.00", chave="8" * 44, tp_nf="0")
    assert (await danfe.nf_da_conversa(db, devolucao)).numero == "17"

    cancelado = await _conversa(db, make_user, pedido="250925NF7")
    await _pedido(db, "900107", "250925NF7", "50.00", situacao="12")
    await _nota(db, "900107", "19", "50.00", chave="9" * 44)
    with pytest.raises(danfe.NfIndisponivel) as e:
        await danfe.nf_da_conversa(db, cancelado)
    assert e.value.code == "nf_pedido_cancelado"

    # Pacote do ML com 2 pedidos no Bling (cada um com a sua NF): não escolhe.
    carrinho = await _conversa(db, make_user, plataforma="ml", canal="pos_venda", pedido="2000777")
    await _pedido(db, "900108", "2000777", "70.00")
    await _pedido(db, "900109", "2000777", "90.00")
    with pytest.raises(danfe.NfIndisponivel) as e:
        await danfe.nf_da_conversa(db, carrinho)
    assert e.value.code == "nf_varios_pedidos" and "2 pedidos" in e.value.detail


def test_infcpl_sem_espaco_nao_trava():
    import time

    longo = _xml().replace(b"<infCpl>", b"<infCpl>" + b"A" * 5000 + b" ")
    inicio = time.monotonic()
    danfe.gerar_pdf(longo)
    assert time.monotonic() - inicio < 5


def test_pagina_de_formato_absurdo_nao_vira_imagem():
    doc = pymupdf.open()
    doc.new_page(width=200, height=14400)
    anexo = foto.validar_foto(doc.tobytes(), "a.pdf")
    with pytest.raises(foto.FotoInvalida) as e:
        foto.pdf_em_imagem(anexo)
    assert e.value.code == "pdf_pagina"
    # Páginas de larguras diferentes viram a mesma largura (sem esticar a altura).
    doc = pymupdf.open()
    doc.new_page(width=1190, height=1684)
    doc.new_page(width=297, height=420)
    imagem = foto.pdf_em_imagem(foto.validar_foto(doc.tobytes(), "b.pdf"))
    largura, altura = foto.dimensoes(imagem.conteudo)
    assert largura * altura <= foto.MAX_PIXELS_PDF_IMAGEM * 1.05


def test_duplicatas_entram_em_faturas_e_empurram_o_resto():
    sem = pymupdf.open(stream=danfe.gerar_pdf(_xml()), filetype="pdf")[0]
    com = pymupdf.open(stream=danfe.gerar_pdf(_xml(duplicatas=4)), filetype="pdf")[0]
    texto = com.get_text()
    assert "001" in texto and "03/11/2026" in texto and "141,33" in texto
    assert "004" in texto  # 4 duplicatas: duas fileiras
    titulo = lambda pg: pg.search_for("Cálculo do imposto")[0].y0  # noqa: E731
    assert titulo(com) > titulo(sem) + 10
