"""M-69 (26/11/2026): coluna SKU na Certificações e ligação do certificado com
os produtos pelo SKU (contrato em relatorios/mudancas_26nov/contrato_M66_M74.md).

* SKU sem a tag (`familia`, a family() do ERP): "dg019.ra" → "dg019";
  "uaf001m1.110" → "uaf001m1". A entrada aceita vírgula, ponto e vírgula,
  espaço e quebra de linha.
* Um SKU num certificado só: 409 "dg019 já está na certificação USM001" e nada
  grava. SKU que não existe em produto nenhum grava e volta como aviso.
* Certificado efetivo: um certificado pela coluna SKU → ele; dois ou mais →
  conflito (vale o gravado e a tela avisa); nenhum → o gravado. O nome não
  conta mais.
* Coluna Modelo da Tabela de Preços só leitura: PATCH de certificacao_id = 400.

A tabela financeiro_suprimentos (e a coluna SKU, que sai junto em cascata) não
é limpa entre os testes (conftest): cada teste usa SKUs e modelos próprios e
apaga o que criou.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FinanceiroSuprimentos, FinanceiroSuprimentoSku, Product, UserRole
from app.models.pricing import PricingGarantiaPrazo, PricingProduct
from app.models.segment import Segment
from app.services.certificacao_produto import (
    Certificacao,
    Ligacoes,
    certificado_efetivo,
    certificados_pelos_skus,
    familia,
    familias_da_linha,
    ordenar_skus,
    produtos_do_certificado,
    skus_da_entrada,
)


def _sku() -> str:
    """Uma família só deste teste ("t3f9a1c")."""
    return f"t{uuid.uuid4().hex[:7]}"


def _cod() -> str:
    return f"T{uuid.uuid4().hex[:6].upper()}9"


@pytest_asyncio.fixture
async def certificados(db: AsyncSession):
    criados: list[uuid.UUID] = []

    async def _novo(modelo: str, skus: list[str] | None = None, **campos) -> FinanceiroSuprimentos:
        campos.setdefault("produto", "smartphone 4G")
        campos.setdefault("certificado", "anatel")
        campos.setdefault("numero", f"N-{modelo}")
        campos.setdefault("fim", date(2028, 7, 30))
        row = FinanceiroSuprimentos(modelo=modelo, **campos)
        db.add(row)
        await db.flush()
        for s in skus or []:
            db.add(FinanceiroSuprimentoSku(sku=s, suprimento_id=row.id))
        await db.commit()
        await db.refresh(row)
        criados.append(row.id)
        return row

    yield _novo
    await db.rollback()
    await db.execute(
        PricingProduct.__table__.update()
        .where(PricingProduct.__table__.c.certificacao_id.in_(criados))
        .values(certificacao_id=None)
    )
    await db.execute(delete(FinanceiroSuprimentos).where(FinanceiroSuprimentos.id.in_(criados)))
    await db.commit()


async def _segmento(db: AsyncSession) -> Segment:
    seg = (
        await db.execute(
            select(Segment).where(Segment.slug == "celular", Segment.parent_id.is_(None))
        )
    ).scalar_one_or_none()
    if seg is None:
        seg = Segment(name="Celular", slug="celular")
        db.add(seg)
        await db.commit()
    return seg


async def _produto(db, dono, sku, nome="uranyx teste", *, cert=None, meses=None):
    seg = await _segmento(db)
    p = PricingProduct(
        user_id=dono.id,
        sku=sku,
        name=nome,
        segment_id=seg.id,
        certificacao_id=cert.id if cert is not None else None,
        garantia_meses=meses,
    )
    db.add(p)
    await db.commit()
    return p


async def _skus_no_banco(db, cert) -> list[str]:
    return ordenar_skus(
        (
            await db.execute(
                select(FinanceiroSuprimentoSku.sku).where(
                    FinanceiroSuprimentoSku.suprimento_id == cert.id
                )
            )
        ).scalars()
    )


# ── Normalização ─────────────────────────────────────────────────────────────


def test_familia_e_a_family_do_erp():
    from app.erp_local.price_table import family

    for sku, esperado in [
        ("dg019.ra", "dg019"),
        ("DG019.SP", "dg019"),
        ("dg019.ra+a020.ra", "dg019"),
        ("uaf001m1.110", "uaf001m1"),
        ("uaf001m1.220", "uaf001m1"),
        (" a019 ", "a019"),
        ("ucw001m3", "ucw001m3"),
        ("b005.12.20", "b005"),
        ("", ""),
        (None, ""),
    ]:
        assert familia(sku) == esperado, sku
        assert family(sku) == esperado, sku


def test_skus_da_entrada_separa_corta_a_tag_junta_repetidos_e_ordena():
    assert skus_da_entrada("dg019, dg017;dg018.ra\ndg019.sp  dg1000 dg101") == [
        "dg017",
        "dg018",
        "dg019",
        "dg101",
        "dg1000",
    ]
    assert skus_da_entrada(["DG019.RA", " dg017 ", "", None, "dg019"]) == ["dg017", "dg019"]
    assert skus_da_entrada("uaf001m1.110, uaf001m1.220") == ["uaf001m1"]
    assert skus_da_entrada(" ,; \n ") == []
    assert skus_da_entrada("") == []
    assert skus_da_entrada(None) == []


def test_familias_da_linha_da_tabela_de_precos():
    assert familias_da_linha("dg052,dg053,dg054") == ["dg052", "dg053", "dg054"]
    assert familias_da_linha("uaf001m1.110, uaf001m1.220") == ["uaf001m1"]
    assert familias_da_linha("b005.20+a075+bp003+a076,b006.20+a075") == ["b005", "b006"]
    assert familias_da_linha("") == []


# ── Certificado efetivo (regra pura) ─────────────────────────────────────────


def _cert(modelo: str) -> Certificacao:
    return Certificacao(uuid.uuid4(), "smartphone", modelo, None, "anatel", "N", None)


def test_efetivo_com_um_dois_ou_nenhum_certificado_pelo_sku():
    a, b, c = _cert("USM001"), _cert("USM002"), _cert("USM003")
    lig = Ligacoes([a, b, c], {"dg017": a.id, "dg018": a.id, "dg052": b.id, "dg053": c.id})
    # 1 certificado pela coluna SKU → é ele (vence o gravado).
    assert certificado_efetivo(None, "dg017", lig) == (a, [])
    assert certificado_efetivo(c.id, "dg017,dg018", lig) == (a, [])
    assert certificado_efetivo(None, "dg017.ra", lig) == (a, [])
    # 2 ou mais → conflito: não escolhe sozinho; vale o gravado e avisa.
    assert certificado_efetivo(None, "dg052,dg053", lig) == (None, [b, c])
    assert certificado_efetivo(b.id, "dg052,dg053", lig) == (b, [b, c])
    assert certificados_pelos_skus("dg053,dg052,dg054", lig) == [c, b]
    # Nenhum → o gravado (ou nada).
    assert certificado_efetivo(c.id, "dg999", lig) == (c, [])
    assert certificado_efetivo(None, "dg999", lig) == (None, [])
    # O nome não conta mais.
    lig_nome = Ligacoes([a], {})
    assert certificado_efetivo(None, "dg777", lig_nome) == (None, [])


# ── API da Certificações: coluna SKU ─────────────────────────────────────────


async def test_coluna_sku_grava_sem_tag_e_aparece_na_lista(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    s1, s2, s3 = _sku(), _sku(), _sku()
    cert = await certificados(_cod())
    for s in (s1, s2, s3):
        await _produto(db, admin, f"{s}.ra")

    r = await client.patch(
        f"/api/financeiro/suprimentos/{cert.id}",
        json={"skus": f"{s3}.sp, {s1}.ra;{s2}\n{s1}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skus"] == ordenar_skus([s1, s2, s3])
    assert r.json()["avisos"] == []
    assert await _skus_no_banco(db, cert) == ordenar_skus([s1, s2, s3])
    # A coluna SKU não vira "campo editado à mão" (não mexe na situação da fonte).
    assert "skus" not in (r.json()["campos_manuais"] or [])

    r = await client.get("/api/financeiro/suprimentos")
    (linha,) = [x for x in r.json() if x["id"] == str(cert.id)]
    assert linha["skus"] == ordenar_skus([s1, s2, s3])
    assert linha["avisos"] == []

    # Lista também vale; tirar um SKU tira só ele; mandar igual não muda nada.
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"skus": [s1, s3]})
    assert r.json()["skus"] == ordenar_skus([s1, s3])
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"skus": [s3, s1]})
    assert r.status_code == 200 and r.json()["skus"] == ordenar_skus([s1, s3])
    # Outro campo sozinho: a coluna SKU fica como está.
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"numero": "X1"})
    assert r.json()["skus"] == ordenar_skus([s1, s3])
    # Vazio (ou null) = sem SKU.
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"skus": ""})
    assert r.json()["skus"] == []
    assert await _skus_no_banco(db, cert) == []
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"skus": [s2]})
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"skus": None})
    assert r.json()["skus"] == []


async def test_sku_em_outro_certificado_da_409_e_nada_grava(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    s1, s2 = _sku(), _sku()
    usm001 = await certificados("USM001", skus=[s1], nome_comercial="F109")
    usm002 = await certificados("USM002", skus=[s2])
    await _produto(db, admin, f"{s1}.ra")

    r = await client.patch(
        f"/api/financeiro/suprimentos/{usm002.id}",
        json={"skus": f"{s2}, {s1}.sp", "numero": "MUDOU"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "code": "sku_em_outro_certificado",
        "message": f"{s1} já está na certificação USM001",
    }
    # Nada grava: nem a coluna SKU, nem o outro campo da mesma edição.
    assert await _skus_no_banco(db, usm002) == [s2]
    assert await _skus_no_banco(db, usm001) == [s1]
    await db.refresh(usm002)
    assert usm002.numero == "N-USM002"

    # O nome da outra: Modelo, senão Nome comercial, senão Número, senão Produto.
    sem_modelo = await certificados("", skus=[], nome_comercial="Fone UFB10")
    usm001.modelo = None
    await db.commit()
    r = await client.patch(f"/api/financeiro/suprimentos/{sem_modelo.id}", json={"skus": [s1]})
    assert r.json()["detail"]["message"] == f"{s1} já está na certificação F109"
    # Na criação também.
    r = await client.post(
        "/api/financeiro/suprimentos", json={"produto": "x", "skus": [s2], "certificado": ""}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["message"].startswith(f"{s2} já está na certificação USM002")
    assert (
        await db.scalar(
            select(FinanceiroSuprimentos.id).where(FinanceiroSuprimentos.produto == "x")
        )
        is None
    )


async def test_sku_que_nao_existe_grava_com_aviso(client, db, make_user, auth_as, certificados):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    na_tabela, no_cadastro, nenhum = _sku(), _sku(), _sku()
    cert = await certificados(_cod())
    # Existe numa linha da Tabela de Preços com vários códigos (pela família)…
    await _produto(db, admin, f"outro1,{na_tabela}.110, outro2")
    # …ou só no cadastro de produtos (products.sku, também pela família).
    db.add(Product(user_id=admin.id, sku=f"{no_cadastro}.sp+a003.sp", name="Kit"))
    await db.commit()

    r = await client.patch(
        f"/api/financeiro/suprimentos/{cert.id}",
        json={"skus": [na_tabela, no_cadastro, nenhum]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skus"] == ordenar_skus([na_tabela, no_cadastro, nenhum])
    assert r.json()["avisos"] == [f"{nenhum} não existe em nenhum produto"]
    assert await _skus_no_banco(db, cert) == ordenar_skus([na_tabela, no_cadastro, nenhum])
    await db.execute(delete(Product).where(Product.sku == f"{no_cadastro}.sp+a003.sp"))
    await db.commit()


async def test_certificacao_nova_com_sku(client, db, make_user, auth_as, certificados):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    s1 = _sku()
    p = await _produto(db, admin, f"{s1}.ra", meses=3)
    r = await client.post(
        "/api/financeiro/suprimentos",
        json={"produto": "smartphone", "modelo": _cod(), "skus": f"{s1}.ra", "garantia_meses": 12},
    )
    assert r.status_code == 201, r.text
    novo = uuid.UUID(r.json()["id"])
    try:
        assert r.json()["skus"] == [s1] and r.json()["avisos"] == []
        assert "skus" not in (r.json()["campos_manuais"] or [])
        await db.refresh(p)
        assert p.garantia_meses == 12, "o produto pega a garantia do certificado novo"
    finally:
        await db.execute(delete(FinanceiroSuprimentos).where(FinanceiroSuprimentos.id == novo))
        await db.commit()


async def test_excluir_a_certificacao_tira_os_skus_dela(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    s1 = _sku()
    cert = await certificados(_cod(), skus=[s1])
    r = await client.delete(f"/api/financeiro/suprimentos/{cert.id}")
    assert r.status_code == 204
    assert (
        await db.scalar(
            select(FinanceiroSuprimentoSku.sku).where(FinanceiroSuprimentoSku.sku == s1)
        )
        is None
    )


async def test_so_quem_edita_grava_a_coluna_sku(client, db, make_user, auth_as, certificados):
    cert = await certificados(_cod())
    leitor = await make_user(permissions={"financeiro_suprimentos": {"view": True}})
    auth_as(leitor)
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"skus": [_sku()]})
    assert r.status_code == 403
    assert await _skus_no_banco(db, cert) == []


# ── Produtos do certificado e garantia ───────────────────────────────────────


async def test_produtos_do_certificado_pelo_sku_e_pelo_gravado(db, make_user, certificados):
    admin = await make_user(role=UserRole.ADMIN)
    s1, s2, s3, s4 = _sku(), _sku(), _sku(), _sku()
    a = await certificados(_cod(), skus=[s1, s2])
    b = await certificados(_cod(), skus=[s3])
    pelo_sku = await _produto(db, admin, f"{s1}")
    varios = await _produto(db, admin, f"{s2}.110, {s2}.220")
    # Gravado em B, mas a coluna SKU diz A: vale a coluna SKU.
    sku_vence = await _produto(db, admin, f"{s1}0,{s1}", cert=b)
    so_gravado = await _produto(db, admin, f"{s4}", cert=a)
    conflito_com_gravado = await _produto(db, admin, f"{s1},{s3}", cert=b)
    conflito_sem_gravado = await _produto(db, admin, f"{s2},{s3}")
    # O nome não liga mais ninguém.
    await _produto(db, admin, f"{_sku()}", f"uranyx - {a.modelo}")

    ids = {p.id for p in await produtos_do_certificado(db, a.id)}
    assert ids == {pelo_sku.id, varios.id, sku_vence.id, so_gravado.id}
    ids_b = {p.id for p in await produtos_do_certificado(db, b.id)}
    assert ids_b == {conflito_com_gravado.id}
    assert conflito_sem_gravado.id not in ids | ids_b


async def test_mudar_a_coluna_sku_passa_a_garantia_a_quem_entrou(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    s1, s2 = _sku(), _sku()
    com_garantia = await certificados(_cod(), garantia_meses=18)
    sem_garantia = await certificados(_cod(), skus=[s2])
    p = await _produto(db, admin, f"{s1}", meses=6)
    q = await _produto(db, admin, f"{s2}", meses=4)

    r = await client.patch(f"/api/financeiro/suprimentos/{com_garantia.id}", json={"skus": [s1]})
    assert r.status_code == 200, r.text
    await db.refresh(p)
    assert p.garantia_meses == 18
    vig = (
        await db.execute(
            select(PricingGarantiaPrazo.meses, PricingGarantiaPrazo.meses_anterior).where(
                PricingGarantiaPrazo.pricing_product_id == p.id
            )
        )
    ).all()
    assert [tuple(v) for v in vig] == [(18, 6)]
    # Foi para um certificado sem garantia: fica com o prazo que tem.
    r = await client.patch(f"/api/financeiro/suprimentos/{com_garantia.id}", json={"skus": []})
    r = await client.patch(
        f"/api/financeiro/suprimentos/{sem_garantia.id}", json={"skus": [s2, s1]}
    )
    await db.refresh(p)
    await db.refresh(q)
    assert (p.garantia_meses, q.garantia_meses) == (18, 4)


# ── Tabela de Preços › Produtos ──────────────────────────────────────────────


async def test_tabela_de_precos_mostra_o_certificado_pelo_sku_e_avisa_conflito(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    s1, s2 = _sku(), _sku()
    a = await certificados("USM001", skus=[s1], numero="08215-25-18234", fim=date(2028, 2, 20))
    b = await certificados("USM002", skus=[s2])
    um = await _produto(db, admin, f"{s1}")
    dois = await _produto(db, admin, f"{s1},{s2}")
    dois_gravado = await _produto(db, admin, f"{s2},{s1}", cert=b)

    r = await client.get("/api/pricing/products")
    linhas = {x["id"]: x for x in r.json()}
    assert linhas[str(um.id)]["certificacao"]["id"] == str(a.id)
    assert linhas[str(um.id)]["certificacao"]["numero"] == "08215-25-18234"
    assert linhas[str(um.id)]["certificacao"]["fim"] == "2028-02-20"
    assert linhas[str(um.id)]["certificacao_conflito"] == []
    assert "certificacao_pelo_nome" not in linhas[str(um.id)]
    assert linhas[str(dois.id)]["certificacao"] is None
    assert linhas[str(dois.id)]["certificacao_conflito"] == ["USM001", "USM002"]
    assert linhas[str(dois_gravado.id)]["certificacao"]["id"] == str(b.id)
    assert linhas[str(dois_gravado.id)]["certificacao_conflito"] == ["USM002", "USM001"]


async def test_modelo_da_tabela_de_precos_e_so_leitura(
    client, db, make_user, auth_as, certificados
):
    editor = await make_user(
        permissions={
            "tabela_precos": {"view": True, "edit": True},
            "tabela_precos_produtos": {"view": True, "edit": True},
        }
    )
    auth_as(editor)
    s1 = _sku()
    a = await certificados(_cod(), skus=[s1])
    b = await certificados(_cod())
    p = await _produto(db, editor, f"{s1}", cert=b)
    for valor in (str(a.id), None):
        r = await client.patch(
            f"/api/pricing/products/{p.id}", json={"certificacao_id": valor, "name": "mudou"}
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == {
            "code": "certificado_pela_certificacoes",
            "message": "Modelo: definido em Importação › Certificações.",
        }
    await db.refresh(p)
    assert (p.certificacao_id, p.name) == (b.id, "uranyx teste"), "nada mudou"
    # O resto da linha continua editável (e o certificado vem da coluna SKU).
    r = await client.patch(f"/api/pricing/products/{p.id}", json={"name": "uranyx novo"})
    assert r.status_code == 200
    assert r.json()["certificacao"]["id"] == str(a.id)


async def test_trocar_o_sku_da_linha_troca_o_certificado_e_pega_a_garantia(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    s1, s2 = _sku(), _sku()
    await certificados(_cod(), skus=[s1])
    b = await certificados(_cod(), skus=[s2], garantia_meses=24)
    p = await _produto(db, admin, f"{s1}", meses=6)
    r = await client.patch(f"/api/pricing/products/{p.id}", json={"sku": f"{s2}"})
    assert r.status_code == 200, r.text
    assert r.json()["certificacao"]["id"] == str(b.id)
    assert r.json()["garantia_meses"] == 24
    # Produto novo com SKU de um certificado com garantia nasce com ela.
    seg = await _segmento(db)
    r = await client.post(
        "/api/pricing/products",
        json={"sku": f"{s2}.ra", "name": "novo", "segment_id": str(seg.id)},
    )
    assert r.status_code == 201, r.text
    assert r.json()["garantia_meses"] == 24
    assert r.json()["certificacao"]["id"] == str(b.id)
