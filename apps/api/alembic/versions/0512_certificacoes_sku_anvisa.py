"""Certificações: Anvisa no Certificado (M-68) e SKU na certificação (M-69)

Pedido M-68 (aprovado pelo administrador em 26/11/2026; contrato em
relatorios/mudancas_26nov/contrato_M66_M74.md): a coluna Certificado da aba
Importação › Certificações ganha a opção "Anvisa", só manual (não existe
consulta de Anvisa). O valor continua gravado em minúsculas no mesmo campo
`financeiro_suprimentos.certificado`.

ESTRUTURA (upgrade):
- Refaz a trava do campo `certificado`, que passa a aceitar também 'anvisa':
  NULL, '', 'anatel', 'inmetro', 'anvisa' ou 'isento'.
- Nome da trava: a 0083 declarou `ck_financeiro_suprimentos_certificado`, mas a
  convenção de nomes do Base (ck_%(table_name)s_%(constraint_name)s) foi
  aplicada por cima e o nome que existe no banco é
  `ck_financeiro_suprimentos_ck_financeiro_suprimentos_certificado` (conferido
  na cópia da produção, previa52). A trava nova fica com ESSE nome (o mesmo de
  hoje). Por segurança, apaga também a grafia curta com IF EXISTS.
- O model não declara essa trava (models/financeiro.py); quem barra valor
  fora da lista na API é o schema (SuprimentosPatch).
- Nenhum dado muda: todos os valores aceitos antes continuam aceitos.

M-69 (aprovado em 26/11/2026; "O SKU passa a ser a ligação entre certificado
e produto"), NESTA MESMA migration, depois da trava:

- Tabela `financeiro_suprimento_skus` (a coluna SKU da Certificações): `sku`
  (PK, já sem a tag — um SKU fica num certificado só), `suprimento_id` (FK
  ON DELETE CASCADE, com índice) e `criado_em`. Igual ao model
  `FinanceiroSuprimentoSku`.
- CONVERSÃO (regra de ouro: nenhum produto troca de certificado, validade,
  garantia ou preço). Usa uma CÓPIA CONGELADA da ligação de hoje (gravado na
  linha → código no nome, como `services/certificacao_produto.py` fazia até a
  0511) e uma cópia da regra nova; não importa `app.services`.
  1. O certificado efetivo de HOJE de cada linha de `pricing_products`.
  2. Cada família (SKU sem a tag) de cada linha "pede" o certificado efetivo
     da linha — inclusive "nenhum", quando a linha não tem certificado. Pedida
     por um certificado só → entra na coluna SKU dele. Pedida por dois ou
     mais (ou por um certificado e por uma linha sem certificado, como
     "uaf001m1": o airfryer tem o UAF001 e os potes uaf001m1.2l/4l/6l não têm
     nenhum) → NÃO entra: vai para o relatório de conflitos.
  3. O efetivo de cada linha pela regra NOVA (coluna SKU; dois ou mais ou
     nenhum → o gravado). Diferente do de hoje → grava
     `pricing_products.certificacao_id` = o certificado de hoje na linha (só
     põe o valor que já valia; não apaga nada).
  4. Confere TODAS as linhas de novo, lendo do banco: o efetivo novo tem de
     ser igual ao de hoje. Uma diferente → RuntimeError com a lista, e o
     deploy aborta (nada fica gravado: é a mesma transação).
  5. Imprime o relatório no log do deploy: famílias inseridas, conflitos,
     linhas que receberam o `certificacao_id`, linhas sem SKU.
  Validade e garantia são do certificado efetivo e a garantia do produto
  está na própria linha (não mexida): mesmo certificado = mesma validade e
  mesma garantia. Preço não passa por aqui.

`SET LOCAL lock_timeout` como na 0505…0511: o ALTER segura a tabela por um
instante (são poucas dezenas de linhas); se algo a estiver segurando, desiste
em 5 s e é só rodar de novo.

DOWNGRADE: volta a trava antiga (mesmo nome). Se houver
certificação marcada como 'anvisa', PARA com RuntimeError e não muda nada:
nenhum dado é apagado nem trocado sozinho (troque o Certificado dessas linhas
na tela antes de voltar). A coluna SKU sai, mas antes é copiada para
`financeiro_suprimento_skus_bak_0512` (fora do Histórico pelo nome): nada se
perde. O `certificacao_id` que a conversão gravou fica (era o certificado que
já valia; com a ligação pelo nome de volta, continua o mesmo).

Revision ID: 0512_certificacoes_sku_anvisa
Revises: 0511_contas_ml_par_custo_total
"""

# ruff: noqa: S608 — SQL montado só com nomes e valores fixos desta migration (schema), sem entrada de fora
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from uuid import UUID

import sqlalchemy as sa

from alembic import op

revision: str = "0512_certificacoes_sku_anvisa"
down_revision: str | None = "0511_contas_ml_par_custo_total"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TABELA = "financeiro_suprimentos"
# O nome que a 0083 deixou no banco (a convenção do Base aplicada por cima do
# nome declarado). A trava nova usa o mesmo nome.
TRAVA_CERTIFICADO = "ck_financeiro_suprimentos_ck_financeiro_suprimentos_certificado"
# A grafia que a 0083 declarou; só por segurança (não existe nos bancos conferidos).
TRAVA_CERTIFICADO_CURTA = "ck_financeiro_suprimentos_certificado"

# Valores aceitos no campo `certificado` (sempre em minúsculas; '' = sem).
CERTIFICADOS_0083 = ("anatel", "inmetro", "isento", "")
CERTIFICADOS = ("anatel", "inmetro", "anvisa", "isento", "")


def condicao_certificado(valores: Sequence[str]) -> str:
    """O CHECK do campo `certificado` para estes valores (o texto da 0083)."""
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"certificado IS NULL OR certificado IN ({lista})"


def trocar_trava_certificado(bind, valores: Sequence[str]) -> None:
    """Apaga a trava do `certificado` (qualquer das duas grafias) e cria a
    mesma trava com estes valores."""
    for antigo in (TRAVA_CERTIFICADO, TRAVA_CERTIFICADO_CURTA):
        bind.execute(sa.text(f"ALTER TABLE {SCHEMA}.{TABELA} DROP CONSTRAINT IF EXISTS {antigo}"))
    bind.execute(
        sa.text(
            f"ALTER TABLE {SCHEMA}.{TABELA} ADD CONSTRAINT {TRAVA_CERTIFICADO}"
            f" CHECK ({condicao_certificado(valores)})"
        )
    )


def anvisa_no_certificado(bind) -> None:
    """M-68: a trava passa a aceitar 'anvisa'."""
    trocar_trava_certificado(bind, CERTIFICADOS)


def conferir_sem_anvisa(bind) -> None:
    """Linha com 'anvisa' → RuntimeError com a lista (o downgrade para)."""
    linhas = (
        bind.execute(
            sa.text(
                f"SELECT coalesce(nullif(trim(modelo), ''), nullif(trim(produto), ''), id::text)"
                f" FROM {SCHEMA}.{TABELA} WHERE certificado = 'anvisa' ORDER BY 1"
            )
        )
        .scalars()
        .all()
    )
    if linhas:
        raise RuntimeError(
            f"{len(linhas)} certificação(ões) com Certificado = Anvisa: "
            + ", ".join(linhas)
            + ". Troque o Certificado dessas linhas na tela antes de voltar a migration."
        )


def tirar_anvisa_do_certificado(bind) -> None:
    """Volta a trava da 0083. Linha com 'anvisa' → para sem mudar nada."""
    conferir_sem_anvisa(bind)
    trocar_trava_certificado(bind, CERTIFICADOS_0083)


# ── M-69: coluna SKU ─────────────────────────────────────────────────────────

TABELA_SKUS = "financeiro_suprimento_skus"
TABELA_SKUS_BAK = "financeiro_suprimento_skus_bak_0512"
# O nome que a convenção do Base dá à FK (passa de 63 letras: o SQLAlchemy
# corta e põe o final do hash). Igual ao model, para o create_all e a
# migration darem o mesmo banco.
FK_SKUS = "fk_financeiro_suprimento_skus_suprimento_id_financeiro__332f"


def criar_tabela_skus(bind) -> None:
    """A coluna SKU da Certificações (igual ao model FinanceiroSuprimentoSku)."""
    bind.execute(
        sa.text(
            f"CREATE TABLE {SCHEMA}.{TABELA_SKUS} ("
            " sku text NOT NULL,"
            " suprimento_id uuid NOT NULL,"
            " criado_em timestamptz NOT NULL DEFAULT now(),"
            f" CONSTRAINT pk_{TABELA_SKUS} PRIMARY KEY (sku),"
            f" CONSTRAINT {FK_SKUS} FOREIGN KEY (suprimento_id)"
            f"  REFERENCES {SCHEMA}.{TABELA} (id) ON DELETE CASCADE)"
        )
    )
    bind.execute(
        sa.text(
            f"CREATE INDEX ix_{TABELA_SKUS}_suprimento_id ON {SCHEMA}.{TABELA_SKUS} (suprimento_id)"
        )
    )


def tirar_tabela_skus(bind) -> None:
    """Copia a coluna SKU para a tabela _bak (nada se perde) e tira a tabela."""
    bind.execute(
        sa.text(
            f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{TABELA_SKUS_BAK} ("
            " sku text, suprimento_id uuid, criado_em timestamptz,"
            " copiado_em timestamptz NOT NULL DEFAULT now())"
        )
    )
    copiados = bind.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.{TABELA_SKUS_BAK} (sku, suprimento_id, criado_em)"
            f" SELECT sku, suprimento_id, criado_em FROM {SCHEMA}.{TABELA_SKUS}"
        )
    ).rowcount
    bind.execute(sa.text(f"DROP TABLE {SCHEMA}.{TABELA_SKUS}"))
    print(f"[0512] coluna SKU copiada para {TABELA_SKUS_BAK}: {copiados} SKU(s)")  # noqa: T201 — log do deploy


# ── CÓPIA CONGELADA da ligação de HOJE (services/certificacao_produto.py até a
# 0511). NÃO mexer: é o que decide o certificado efetivo de antes da conversão.


def _norm(texto: str | None) -> str:
    return " ".join(re.sub(r"[^0-9A-Za-z]+", " ", texto or "").upper().split())


def _codigo(texto: str) -> bool:
    return bool(re.search(r"[A-Z]", texto)) and bool(re.search(r"[0-9]", texto))


_MIN_MODELO_NO_NOME = 6


class CertCongelado:
    """A linha da Certificações como a ligação pelo nome a via. Classe simples
    (sem @dataclass: o alembic carrega a migration fora do sys.modules)."""

    __slots__ = ("fim", "id", "modelo", "nome_comercial", "numero", "produto")

    def __init__(self, id, produto, modelo, nome_comercial, numero, fim) -> None:  # noqa: A002
        self.id: UUID = id
        self.produto: str | None = produto
        self.modelo: str | None = modelo
        self.nome_comercial: str | None = nome_comercial
        self.numero: str | None = numero
        self.fim: date | None = fim

    def chaves(self) -> set[str]:
        out: set[str] = set()
        modelo = _norm(self.modelo)
        if modelo:
            out.add(modelo)
            out.update(p for p in modelo.split() if _codigo(p))
        comercial = (self.nome_comercial or "").strip()
        if comercial and "," not in comercial:
            n = _norm(comercial)
            if n and _codigo(n.replace(" ", "")):
                out.add(n)
        return out


def codigo_do_nome(nome: str | None) -> str | None:
    if not nome or " - " not in nome:
        return None
    fim = nome.rsplit(" - ", 1)[1].strip()
    return fim if fim and " " not in fim else None


def _melhor(candidatos: list[CertCongelado]) -> CertCongelado | None:
    if not candidatos:
        return None
    uniq = {c.id: c for c in candidatos}.values()

    def peso(c: CertCongelado) -> tuple[bool, date]:
        return (bool((c.numero or "").strip()), c.fim or date.min)

    ordenados = sorted(uniq, key=peso, reverse=True)
    if len(ordenados) > 1 and peso(ordenados[0]) == peso(ordenados[1]):
        return None
    return ordenados[0]


def certificacao_do_produto(
    nome: str | None, certificacoes: list[CertCongelado]
) -> CertCongelado | None:
    codigo = codigo_do_nome(nome)
    if codigo:
        alvo = _norm(codigo)
        achou = _melhor([c for c in certificacoes if alvo in c.chaves()])
        if achou is not None:
            return achou
    nome_n = f" {_norm(nome)} "
    no_meio = [
        c
        for c in certificacoes
        if len(_norm(c.modelo).replace(" ", "")) >= _MIN_MODELO_NO_NOME
        and _codigo(_norm(c.modelo).replace(" ", ""))
        and f" {_norm(c.modelo)} " in nome_n
    ]
    return _melhor(no_meio)


def efetivo_de_hoje(
    certificacao_id: UUID | None, nome: str | None, certificacoes: list[CertCongelado]
) -> UUID | None:
    """O gravado na linha ou, sem ele, o achado pelo nome (o id; None = sem)."""
    if certificacao_id is not None:
        return certificacao_id if any(c.id == certificacao_id for c in certificacoes) else None
    cert = certificacao_do_produto(nome, certificacoes)
    return cert.id if cert is not None else None


# ── Cópia da regra NOVA (services/certificacao_produto.py desde a M-69) ──────


def familia(codigo: object) -> str:
    return str(codigo or "").split("+")[0].split(".")[0].strip().lower()


def familias_da_linha(sku: str | None) -> list[str]:
    vistas: dict[str, None] = {}
    for codigo in str(sku or "").split(","):
        f = familia(codigo)
        if f:
            vistas.setdefault(f, None)
    return list(vistas)


def efetivo_novo(
    certificacao_id: UUID | None, sku: str | None, existentes: set[UUID], mapa: dict[str, UUID]
) -> UUID | None:
    """Coluna SKU: um certificado → ele; dois ou mais ou nenhum → o gravado."""
    achados = {mapa[f] for f in familias_da_linha(sku) if f in mapa and mapa[f] in existentes}
    if len(achados) == 1:
        return next(iter(achados))
    return certificacao_id if certificacao_id in existentes else None


# ── A conversão ──────────────────────────────────────────────────────────────


def _ler_certificados(bind) -> list[CertCongelado]:
    return [
        CertCongelado(*r)
        for r in bind.execute(
            sa.text(
                f"SELECT id, produto, modelo, nome_comercial, numero, fim FROM {SCHEMA}.{TABELA}"
                " ORDER BY id"
            )
        ).all()
    ]


def _ler_produtos(bind) -> list[tuple]:
    return bind.execute(
        sa.text(
            f"SELECT id, sku, name, certificacao_id FROM {SCHEMA}.pricing_products"
            " ORDER BY sku, id"
        )
    ).all()


def _nome_cert(c: CertCongelado | None) -> str:
    if c is None:
        return "sem certificado"
    for campo in (c.modelo, c.nome_comercial, c.numero, c.produto):
        if campo and campo.strip():
            return campo.strip()
    return str(c.id)


def converter_ligacao_pelo_sku(bind) -> dict:
    """Preenche a coluna SKU pelos produtos ligados hoje e garante que o
    certificado efetivo de TODA linha da Tabela de Preços continua o mesmo
    (veja o topo). Devolve o relatório (e o upgrade imprime)."""
    certs = _ler_certificados(bind)
    por_id = {c.id: c for c in certs}
    existentes = set(por_id)
    produtos = _ler_produtos(bind)

    # 1. O efetivo de hoje.
    hoje = {pid: efetivo_de_hoje(cid, nome, certs) for pid, _sku, nome, cid in produtos}

    # 2. Cada família pede o certificado da linha (inclusive "nenhum").
    pedidos: dict[str, set[UUID | None]] = defaultdict(set)
    for pid, sku, _nome, _cid in produtos:
        for f in familias_da_linha(sku):
            pedidos[f].add(hoje[pid])
    inserir = {
        f: next(iter(ids)) for f, ids in pedidos.items() if len(ids) == 1 and None not in ids
    }
    conflitos = {f: ids for f, ids in pedidos.items() if len(ids) > 1}
    for f, cid in sorted(inserir.items()):
        bind.execute(
            sa.text(f"INSERT INTO {SCHEMA}.{TABELA_SKUS} (sku, suprimento_id) VALUES (:s, :c)"),
            {"s": f, "c": cid},
        )

    # 3. A regra nova diferente da de hoje → grava o de hoje na linha.
    gravadas: list[tuple] = []
    for pid, sku, nome, cid in produtos:
        if hoje[pid] is not None and efetivo_novo(cid, sku, existentes, inserir) != hoje[pid]:
            bind.execute(
                sa.text(
                    f"UPDATE {SCHEMA}.pricing_products SET certificacao_id = :c WHERE id = :id"
                ),
                {"c": hoje[pid], "id": pid},
            )
            gravadas.append((sku, nome, _nome_cert(por_id.get(hoje[pid]))))

    # 4. Confere tudo de novo, lendo do banco.
    mapa = dict(
        bind.execute(sa.text(f"SELECT sku, suprimento_id FROM {SCHEMA}.{TABELA_SKUS}")).all()
    )
    diferentes = [
        f"{sku} ({nome}): antes {_nome_cert(por_id.get(hoje[pid]))},"
        f" depois {_nome_cert(por_id.get(efetivo_novo(cid, sku, existentes, mapa)))}"
        for pid, sku, nome, cid in _ler_produtos(bind)
        if pid in hoje and efetivo_novo(cid, sku, existentes, mapa) != hoje[pid]
    ]
    if diferentes:
        raise RuntimeError(
            f"0512: {len(diferentes)} produto(s) mudariam de certificado com a conversão"
            " pelo SKU; nada foi gravado: " + "; ".join(diferentes)
        )

    return {
        "certificados": len(certs),
        "produtos": len(produtos),
        "inseridas": sorted((f, _nome_cert(por_id[c])) for f, c in inserir.items()),
        "conflitos": sorted(
            (f, sorted(_nome_cert(por_id.get(c)) for c in ids)) for f, ids in conflitos.items()
        ),
        "gravadas": sorted(gravadas),
        "sem_sku": sorted(
            (nome or "", str(pid)) for pid, sku, nome, _cid in produtos
            if not familias_da_linha(sku)
        ),
        "com_certificado": sum(1 for v in hoje.values() if v is not None),
    }


def relatorio_em_texto(rel: dict) -> str:
    linhas = [
        f"[0512] conversão da ligação pelo SKU: {rel['produtos']} linhas da Tabela de Preços"
        f" ({rel['com_certificado']} com certificado), {rel['certificados']} certificações."
        " Certificado efetivo de TODAS as linhas conferido: igual ao de antes.",
        f"[0512] famílias inseridas na coluna SKU: {len(rel['inseridas'])}",
    ]
    linhas += [f"[0512]   {f} → {nome}" for f, nome in rel["inseridas"]]
    linhas.append(
        f"[0512] conflitos (família pedida por mais de um; NÃO inserida): {len(rel['conflitos'])}"
    )
    linhas += [f"[0512]   {f} → {', '.join(nomes)}" for f, nomes in rel["conflitos"]]
    linhas.append(
        "[0512] linhas que receberam certificacao_id (o certificado que já valia):"
        f" {len(rel['gravadas'])}"
    )
    linhas += [f"[0512]   {sku} ({nome}) → {cert}" for sku, nome, cert in rel["gravadas"]]
    linhas.append(f"[0512] linhas sem SKU: {len(rel['sem_sku'])}")
    linhas += [f"[0512]   {nome} ({pid})" for nome, pid in rel["sem_sku"]]
    return "\n".join(linhas)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind = op.get_bind()
    anvisa_no_certificado(bind)
    # M-69: a coluna SKU e a conversão (aborta se um produto mudaria).
    criar_tabela_skus(bind)
    print(relatorio_em_texto(converter_ligacao_pelo_sku(bind)))  # noqa: T201 — log do deploy


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind = op.get_bind()
    # Com linha Anvisa para antes de mexer em qualquer coisa.
    conferir_sem_anvisa(bind)
    tirar_tabela_skus(bind)
    tirar_anvisa_do_certificado(bind)
