"""A 0512 (M-69): coluna SKU da Certificações e a conversão da ligação pelo
nome, num schema rascunho com produtos ligados pelo gravado, pelo nome, com
conflito e sem SKU. Regra de ouro: depois dela o certificado efetivo de TODO
produto é igual ao de antes (calculado pela cópia congelada da ligação de
hoje; a cópia é conferida contra os casos de produção logo abaixo).
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from datetime import date
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base
from app.services.certificacao_produto import Certificacao, Ligacoes, certificado_efetivo

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0512_certificacoes_sku_anvisa.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0512_sku", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MIG = _carregar()


# ── A cópia congelada da ligação pelo nome acha o mesmo que o código vivo da
# 0511 achava (os casos de test_pricing_certificacao_produto, com os nomes e
# certificados da produção de 05/11/2026).

M = "M1, M2, M3, M4, M5, M6, M7, M8, M9, M10"
CERTS = [
    # (produto, modelo, nome comercial, número, fim)
    ("airfryer", "UAF001-M1", M, "MODERNA-0069/25", date(2032, 4, 30)),
    ("airfryer barbecue", "UAF002-M1", M, "MODERNA-0069/25", date(2032, 4, 30)),
    ("airtag", "AirTag UAT001", None, "06879-26-18234", date(2028, 9, 3)),
    ("bateria a17", None, None, None, None),
    ("bateria f109", "F109", None, "08214-25-18234", date(2028, 1, 19)),
    ("bateria f110", "F110", None, "08201-25-18234", date(2028, 1, 16)),
    ("carregador", "QZ-0180AE2H", None, "08217-25-18234", date(2028, 3, 11)),
    ("cofee machine", "UCM001-M1", M, "MODERNA-0070/25", date(2032, 5, 21)),
    ("cooktop de induçao", "UCI001", M, None, None),
    ("cookware", "UCW001", M, None, None),
    ("fone bluetooth", "A9 Pro", "UFB10", "06764-25-18234", date(2027, 11, 7)),
    ("fone com fio", "UFF001", M, None, None),
    ("slushie maker", "USL001-M1", M, "MODERNA-0071/25", date(2032, 7, 30)),
    ("smart cooking", "UCO001-M1", M, "MODERNA-0072/26", date(2032, 7, 30)),
    ("smart glass", "USG001", None, "07295-26-18234", date(2028, 8, 27)),
    ("smartphone 4G", "USM002", "FOSSIBOT F110, OUKITEL C2", "08216-25-18234", date(2028, 2, 24)),
    ("smartphone 4G", "USM003", "A17, A18, c17", "07197-26-18234", date(2028, 7, 30)),
    ("smartphone 4G", "S41 Plus", "DOOGEE 25, Fire3", "09604-24-17288", date(2026, 10, 28)),
    ("smartphone 5G", "USM001", "FOSSIBOT F109", "08215-25-18234", date(2028, 2, 20)),
    ("smartwatch", "W69", "USW10", "06763-25-18234", date(2027, 11, 6)),
    ("Transceptor", "EarPhone Doogee 25", "Doogee H25", "09607-24-17288", date(2026, 12, 19)),
]


def _congelados():
    return [MIG.CertCongelado(uuid.uuid4(), *c) for c in CERTS]


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("uranyx A17 pro max 12.128 prata/preto/laranja - USM003", "USM003"),
        ("uranyx A18 pro max - S5 edition 16.128 laranja/prata - USM003", "USM003"),
        ("uranyx F109 5G 24.256 Preto - USM001", "USM001"),
        ("uranyx F110 pro 5G 20.128 - USM002", "USM002"),
        ("uranyx airtag - UAT001", "AirTag UAT001"),
        ("uranyx carregador 20w - QZ-0180AE2H", "QZ-0180AE2H"),
        ("uranyx fone - UFB10", "A9 Pro"),
        ("uranyx relogio - USW10", "W69"),
        ("uranyx smart glass - USG001", "USG001"),
        ("uranyx fone com fio - UFF001", "UFF001"),
        ("airfryer vidro UAF001 M1 MODERNA-0069/25", "UAF001-M1"),
        ("cafeteira UCM001 M1 MODERNA-0070/25", "UCM001-M1"),
        ("cookware UCW001", "UCW001"),
        ("sluchie USL001 M1 MODERNA-0071/25", "USL001-M1"),
        ("smart cooking UCO001 M1 MODERNA-0072/26", "UCO001-M1"),
        ("airfryer barbecue UAF001 M2 MODERNA-0069/25", None),
        ("airfryer pote vidro 4L", None),
        ("apple watch S11 46mm - cinza/prata/preto/rosa", None),
        ("Oukitel C2 4+256 AVULSO", None),
        ("uranyx case", None),
        ("Mala Arco P2 tamanho 14 - Azul Escuro", None),
    ],
)
def test_copia_congelada_acha_pelo_nome_como_o_codigo_de_antes(nome, esperado):
    cert = MIG.certificacao_do_produto(nome, _congelados())
    assert (cert.modelo if cert else None) == esperado


def test_copia_congelada_desempata_como_antes():
    velho = MIG.CertCongelado(uuid.uuid4(), "s", "USM009", None, "1", date(2026, 1, 1))
    novo = MIG.CertCongelado(uuid.uuid4(), "s", "USM009", None, "2", date(2028, 1, 1))
    rascunho = MIG.CertCongelado(uuid.uuid4(), "s", "USM009", None, None, date(2030, 1, 1))
    gemeo = MIG.CertCongelado(uuid.uuid4(), "s", "USM009", None, "3", date(2028, 1, 1))
    assert MIG.certificacao_do_produto("x - USM009", [velho, novo]) is novo
    assert MIG.certificacao_do_produto("x - USM009", [rascunho, velho]) is velho
    assert MIG.certificacao_do_produto("x - USM009", [novo, gemeo]) is None
    assert MIG.codigo_do_nome("uranyx A18 pro max - S5 edition") is None
    # O gravado vence o nome; gravado que não existe = sem certificado.
    certs = [velho, novo]
    assert MIG.efetivo_de_hoje(velho.id, "x - USM009", certs) == velho.id
    assert MIG.efetivo_de_hoje(None, "x - USM009", certs) == novo.id
    assert MIG.efetivo_de_hoje(uuid.uuid4(), "x - USM009", certs) is None


def test_regra_nova_da_migration_e_a_do_servico():
    from app.services import certificacao_produto as svc

    for sku in ("dg019.ra", "uaf001m1.110, uaf001m1.220", "b005.20+a075,b006", "", None):
        assert MIG.familias_da_linha(sku) == svc.familias_da_linha(sku)
        assert MIG.familia(sku) == svc.familia(sku)


# ── A conversão num schema rascunho ──────────────────────────────────────────

CHECK_0083 = "certificado IS NULL OR certificado IN ('anatel', 'inmetro', 'isento', '')"


async def _montar(db: AsyncSession, rascunho: str, schema_model: str) -> None:
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    for tabela in ("financeiro_suprimentos", "pricing_products"):
        await db.execute(
            text(
                f'CREATE TABLE "{rascunho}".{tabela}'
                f' (LIKE "{schema_model}".{tabela} INCLUDING DEFAULTS INCLUDING INDEXES)'
            )
        )
    await db.execute(
        text(
            f'ALTER TABLE "{rascunho}".financeiro_suprimentos ADD CONSTRAINT'
            f" ck_financeiro_suprimentos_ck_financeiro_suprimentos_certificado CHECK ({CHECK_0083})"
        )
    )
    await db.execute(
        text(
            f'ALTER TABLE "{rascunho}".pricing_products ADD CONSTRAINT fk_pp_cert'
            f' FOREIGN KEY (certificacao_id) REFERENCES "{rascunho}".financeiro_suprimentos (id)'
            " ON DELETE SET NULL"
        )
    )


async def _cert(db, rascunho, modelo, numero="N", fim=date(2028, 1, 1), certificado="anatel"):
    cid = uuid.uuid4()
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".financeiro_suprimentos'
            " (id, produto, modelo, certificado, numero, fim, garantia_meses)"
            " VALUES (:id, 'prod', :m, :c, :n, :f, 12)"
        ),
        {"id": cid, "m": modelo, "c": certificado, "n": numero, "f": fim},
    )
    return cid


async def _prod(db, rascunho, sku, nome, cert=None, meses=3):
    pid = uuid.uuid4()
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".pricing_products'
            " (id, user_id, segment_id, sku, name, certificacao_id, garantia_meses, cost_kit1)"
            " VALUES (:id, :u, :s, :sku, :nome, :c, :g, 100)"
        ),
        {
            "id": pid,
            "u": uuid.uuid4(),
            "s": uuid.uuid4(),
            "sku": sku,
            "nome": nome,
            "c": cert,
            "g": meses,
        },
    )
    return pid


async def _linhas(db, rascunho) -> dict:
    return {
        r[0]: tuple(r[1:])
        for r in (
            await db.execute(
                text(
                    "SELECT id, sku, name, certificacao_id, garantia_meses, cost_kit1"
                    f' FROM "{rascunho}".pricing_products'
                )
            )
        ).all()
    }


async def _efetivo_de_hoje(db, rascunho) -> dict:
    """Pela cópia congelada (o que valia antes da conversão)."""
    certs = [
        MIG.CertCongelado(*r)
        for r in (
            await db.execute(
                text(
                    "SELECT id, produto, modelo, nome_comercial, numero, fim"
                    f' FROM "{rascunho}".financeiro_suprimentos'
                )
            )
        ).all()
    ]
    return {
        pid: MIG.efetivo_de_hoje(cid, nome, certs)
        for pid, (_sku, nome, cid, _g, _c) in (await _linhas(db, rascunho)).items()
    }


async def _efetivo_pelo_servico(db, rascunho) -> dict:
    """Pelo código VIVO (services/certificacao_produto.certificado_efetivo)."""
    certs = [
        Certificacao(*r)
        for r in (
            await db.execute(
                text(
                    "SELECT id, produto, modelo, nome_comercial, certificado, numero, fim,"
                    f' garantia_meses FROM "{rascunho}".financeiro_suprimentos'
                )
            )
        ).all()
    ]
    mapa = dict(
        (
            await db.execute(
                text(f'SELECT sku, suprimento_id FROM "{rascunho}".financeiro_suprimento_skus')
            )
        ).all()
    )
    lig = Ligacoes(certs, mapa)
    out = {}
    for pid, (sku, _nome, cid, _g, _c) in (await _linhas(db, rascunho)).items():
        cert, _ = certificado_efetivo(cid, sku, lig)
        out[pid] = cert.id if cert else None
    return out


def _rodar(mod, passo):
    def _f(conn):
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    return _f


async def test_conversao_nao_muda_o_certificado_de_ninguem(db: AsyncSession, capsys):
    mod = _carregar()
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0512sku"
    await _montar(db, rascunho, schema_model)
    a = await _cert(db, rascunho, "USM001", "08215-25-18234", date(2028, 2, 20))
    b = await _cert(db, rascunho, "USM003", "07197-26-18234", date(2028, 7, 30))
    c = await _cert(db, rascunho, "UAF001-M1", "MODERNA-0069/25", date(2032, 4, 30), "inmetro")
    p = {}
    # Gravado na linha (coluna Modelo).
    p["gravado"] = await _prod(db, rascunho, "dg002,dg003", "uranyx S7", a)
    # Pelo nome, com a família só dele → entra na coluna SKU.
    p["nome"] = await _prod(db, rascunho, "dg111", "uranyx F125 12.64 Preto - USM001")
    # Mesma família pedida pelo mesmo certificado em duas linhas → entra uma vez.
    p["dg050"] = await _prod(db, rascunho, "dg050", "uranyx X", a)
    p["dg050_51"] = await _prod(db, rascunho, "dg050,dg051", "uranyx Y - USM001")
    # Eletro: o airfryer (pelo nome do meio) e os potes sem certificado têm a
    # mesma família "uaf001m1" → conflito com "sem certificado": não entra;
    # o airfryer recebe o certificacao_id do certificado que já tinha.
    p["airfryer"] = await _prod(
        db, rascunho, "uaf001m1.110, uaf001m1.220", "airfryer vidro UAF001 M1 MODERNA-0069/25"
    )
    p["pote"] = await _prod(db, rascunho, "uaf001m1.2l", "airfryer pote vidro 2,5L")
    # Conflito entre dois certificados: dg900 pedida por B (nome) e por A
    # (gravado); dg901 só por A.
    p["conflito_nome"] = await _prod(db, rascunho, "dg900", "uranyx Z - USM003")
    p["conflito_gravado"] = await _prod(db, rascunho, "dg900,dg901", "uranyx W", a)
    # Sem SKU: com certificado pelo nome (recebe o certificacao_id) e sem nada.
    p["sem_sku_cert"] = await _prod(db, rascunho, "", "uranyx V - USM003")
    p["sem_sku"] = await _prod(db, rascunho, " , ", "sem nada")
    # Sem certificado.
    p["balanca"] = await _prod(db, rascunho, "a015", "Balança")
    await db.commit()

    antes = await _efetivo_de_hoje(db, rascunho)
    assert antes == {
        p["gravado"]: a,
        p["nome"]: a,
        p["dg050"]: a,
        p["dg050_51"]: a,
        p["airfryer"]: c,
        p["pote"]: None,
        p["conflito_nome"]: b,
        p["conflito_gravado"]: a,
        p["sem_sku_cert"]: b,
        p["sem_sku"]: None,
        p["balanca"]: None,
    }
    linhas_antes = await _linhas(db, rascunho)

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar(mod, "upgrade"))
        await db.commit()

        # A coluna SKU: só o que um certificado só pediu.
        mapa = dict(
            (
                await db.execute(
                    text(f'SELECT sku, suprimento_id FROM "{rascunho}".financeiro_suprimento_skus')
                )
            ).all()
        )
        assert mapa == {
            "dg002": a,
            "dg003": a,
            "dg111": a,
            "dg050": a,
            "dg051": a,
            "dg901": a,
        }
        # O certificado efetivo de TODA linha é o mesmo de antes — pelo código
        # vivo e pela cópia da regra nova da migration.
        assert await _efetivo_pelo_servico(db, rascunho) == antes
        # Só o certificacao_id das linhas que não deu para dizer pelo SKU mudou
        # (com o certificado que já valia); o resto da linha fica igual.
        depois = await _linhas(db, rascunho)
        mudou = {pid for pid in depois if depois[pid] != linhas_antes[pid]}
        assert mudou == {p["airfryer"], p["conflito_nome"], p["sem_sku_cert"]}
        for chave, cert in (("airfryer", c), ("conflito_nome", b), ("sem_sku_cert", b)):
            sku, nome, cid, meses, custo = depois[p[chave]]
            assert cid == cert
            assert (sku, nome, meses, custo) == (
                linhas_antes[p[chave]][0],
                linhas_antes[p[chave]][1],
                linhas_antes[p[chave]][3],
                linhas_antes[p[chave]][4],
            )

        # O relatório do deploy.
        log = capsys.readouterr().out
        assert "[0512] famílias inseridas na coluna SKU: 6" in log
        assert "[0512]   dg111 → USM001" in log
        assert "[0512] conflitos (família pedida por mais de um; NÃO inserida): 2" in log
        assert "[0512]   dg900 → USM001, USM003" in log
        assert "[0512]   uaf001m1 → UAF001-M1, sem certificado" in log
        assert "receberam certificacao_id (o certificado que já valia): 3" in log
        assert "[0512]   dg900 (uranyx Z - USM003) → USM003" in log
        assert "[0512] linhas sem SKU: 2" in log
        assert "Certificado efetivo de TODAS as linhas conferido: igual ao de antes." in log

        # A tabela como o model (PK = SKU; FK em cascata; índice).
        defs = (
            await db.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(co.oid) FROM pg_constraint co"
                    " JOIN pg_class cl ON cl.oid = co.conrelid JOIN pg_namespace n"
                    " ON n.oid = cl.relnamespace WHERE n.nspname = :s"
                    " AND cl.relname = 'financeiro_suprimento_skus' ORDER BY 1"
                ),
                {"s": rascunho},
            )
        ).all()
        assert [tuple(d) for d in defs if not d[0].endswith("_not_null")] == [
            (
                "fk_financeiro_suprimento_skus_suprimento_id_financeiro__332f",
                f"FOREIGN KEY (suprimento_id) REFERENCES {rascunho}.financeiro_suprimentos(id)"
                " ON DELETE CASCADE",
            ),
            ("pk_financeiro_suprimento_skus", "PRIMARY KEY (sku)"),
        ]
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable

        from app.models import FinanceiroSuprimentoSku

        ddl = str(
            CreateTable(FinanceiroSuprimentoSku.__table__).compile(dialect=postgresql.dialect())
        )
        assert all(
            f"CONSTRAINT {nome} " in ddl for nome, _ in defs if not nome.endswith("_not_null")
        )
        indices = (
            (
                await db.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = :s"
                        " AND tablename = 'financeiro_suprimento_skus' ORDER BY 1"
                    ),
                    {"s": rascunho},
                )
            )
            .scalars()
            .all()
        )
        assert indices == [
            "ix_financeiro_suprimento_skus_suprimento_id",
            "pk_financeiro_suprimento_skus",
        ]

        # Downgrade: a coluna SKU vai para a _bak (nada se perde) e sai.
        conn = await db.connection()
        await conn.run_sync(_rodar(mod, "downgrade"))
        await db.commit()
        assert "copiada para financeiro_suprimento_skus_bak_0512: 6 SKU(s)" in (
            capsys.readouterr().out
        )
        assert (
            await db.scalar(
                text(f"SELECT to_regclass('\"{rascunho}\".financeiro_suprimento_skus')")
            )
            is None
        )
        bak = (
            (
                await db.execute(
                    text(
                        f'SELECT sku FROM "{rascunho}".financeiro_suprimento_skus_bak_0512'
                        " ORDER BY 1"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert bak == sorted(mapa)
        # E sobe de novo (mesmo resultado).
        conn = await db.connection()
        await conn.run_sync(_rodar(mod, "upgrade"))
        await db.commit()
        assert await _efetivo_pelo_servico(db, rascunho) == antes
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


async def test_conversao_que_mudaria_um_produto_aborta_sem_gravar_nada(
    db: AsyncSession, monkeypatch
):
    mod = _carregar()
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0512abo"
    await _montar(db, rascunho, schema_model)
    a = await _cert(db, rascunho, "USM001")
    pid = await _prod(db, rascunho, "dg111", "uranyx - USM001")
    await db.commit()
    linhas_antes = await _linhas(db, rascunho)
    # Uma regra nova "quebrada" (sempre sem certificado): o passo 3 grava o
    # certificado de hoje, mas a conferência do passo 4 acha a diferença.
    monkeypatch.setattr(mod, "efetivo_novo", lambda *_a, **_k: None)
    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        with pytest.raises(RuntimeError, match=r"1 produto\(s\) mudariam de certificado") as erro:
            await conn.run_sync(_rodar(mod, "upgrade"))
        assert "dg111 (uranyx - USM001): antes USM001, depois sem certificado" in str(erro.value)
        await db.rollback()
        # Mesma transação: nada ficou (nem a tabela, nem o certificacao_id, nem a trava).
        assert (
            await db.scalar(
                text(f"SELECT to_regclass('\"{rascunho}\".financeiro_suprimento_skus')")
            )
            is None
        )
        assert await _linhas(db, rascunho) == linhas_antes
        assert linhas_antes[pid][2] is None and a
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
