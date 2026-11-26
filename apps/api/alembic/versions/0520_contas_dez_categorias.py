"""Aba Contas: até 10 categorias de venda por tabela (M-93) — posições 6 a 10 da conta

Pedido M-93 do Harry (26/11/2026, aprovado): cada tabela de Contas (Celular, Eletro, Mala) aceita
até 10 categorias de venda (eram 5). A categoria liga o produto à margem/frete da conta pela
POSIÇÃO (services/categorias_venda.py): a de sort_order N-1 usa margin{N}/shipping{N} e o vínculo
slot{N}_segment_id de cada linha de `pricing_accounts` (kit, as duas linhas do par do ML e a
coluna de catálogo — onde margin{N} são as margens próprias, o `margens_catalogo`). O número 10
mora em services/pricing/posicoes.MAX_CATEGORIAS_VENDA.

ESTRUTURA (só aditiva) em `pricing_accounts`:
- margin6..margin10 NUMERIC(6,4) e shipping6..shipping10 NUMERIC(8,2), os tipos de margin1..5 /
  shipping1..5;
- slot6_segment_id..slot10_segment_id UUID com FK para segments ON DELETE SET NULL e índice, como
  a 0048 fez para slot1..5 (mesmos nomes: fk_/ix_pricing_accounts_slot{N}_segment).
TODAS nascem vazias (NULL) e nenhum dado existente é reescrito: margens, fretes e vínculos de
1..5 ficam como estão. Não existe outro lugar guardado por posição: a margem própria do catálogo
é o margin{N} da própria linha de catálogo (vem junto), e o Histórico grava a linha inteira (as
colunas novas entram nele sozinhas, com nome em historico/nomes.py).

FRETE "PROJETADO" DA MARGEM: as views vw_conciliacao_margens_marketplace(_all) e as funções
conciliacao_margens_for_bling_id / refunds_freight_for_pedido casam o produto com a conta pelo
vínculo slot1..5 (JOIN `leaf.id = pa.slot1_segment_id OR … slot5`) e pegam o frete pelo CASE
`WHEN pa.slot{N}_segment_id = leaf.id THEN pa.shipping{N}`. Sem as posições 6..10 ali, o produto de
uma categoria 6+ ficaria sem conta e sem frete projetado na página Margem. A definição ATUAL de
cada uma é lida do banco (pg_get_viewdef / pg_get_functiondef) e só esses dois trechos ganham as
posições 6..10 (o resto do texto fica igual) — nenhuma cópia de SQL antigo. Como slot6..10 nascem
vazios, `leaf.id = pa.slot6_segment_id` é NULL (não casa) e o CASE não pega nenhum frete novo:
no dia em que for ao ar nenhuma linha da Margem, do snapshot verificar_margem ou do reembolso muda.
Objeto que não existe é pulado; objeto que existe sem o trecho esperado PARA a migration (nada
muda) em vez de seguir pela metade.

NO DIA EM QUE FOR AO AR nenhum preço, margem, frete, Custo total (M-86), cartão vermelho ou envio
às plataformas muda: as posições novas estão vazias e só uma categoria criada depois (a 6ª em
diante) usa alguma delas. O único caso que mudaria é dado antigo: categoria de venda já gravada
com sort_order 5 ou mais E com produto na Tabela de Preços — até aqui o cálculo prendia o tipo em
5 (margin5/shipping5); daqui em diante usa a posição dela (vazia = sem preço). A migration conta
esses casos e AVISA no log (não para); o esperado é zero (desde a M-75 a nova não passa da 5ª).

`SET LOCAL lock_timeout` como na 0511/0519: ALTER em `pricing_accounts`, lida a toda hora pela
grade; se algo segurar a tabela, desiste em 5 s e é só rodar de novo.

DOWNGRADE: volta as views/funções ao texto com slot1..5 (antes de apagar as colunas, de que as
views dependem) e apaga só o que a 0520 criou (colunas, FKs e índices das posições 6..10). Margem,
frete e vínculo gravados nas posições 6..10 somem junto; 1..5 ficam como estão.

Revision ID: 0520_contas_dez_categorias
Revises: 0519_contas_margem_minima
Create Date: 2026-11-26
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0520_contas_dez_categorias"
down_revision: str | None = "0519_contas_margem_minima"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

TABELA = "pricing_accounts"
# As posições que a 0520 cria (1..5 já existiam). Fixo AQUI de propósito: a migration não pode
# mudar se a constante do app (services/pricing/posicoes) mudar um dia.
ANTES = 5
NOVAS = tuple(range(6, 11))

# Os objetos do banco que casam o frete projetado pela posição (ver o docstring).
VIEWS = ("vw_conciliacao_margens_marketplace_all", "vw_conciliacao_margens_marketplace")
FUNCOES = ("conciliacao_margens_for_bling_id", "refunds_freight_for_pedido")


def _fk(n: int) -> str:
    return f"fk_pricing_accounts_slot{n}_segment"


def _ix(n: int) -> str:
    return f"ix_pricing_accounts_slot{n}_segment"


# ------------------------------------------------------------------ o texto das views/funções
# Cada comparação aparece com parênteses (pg_get_viewdef sem "pretty") ou sem (o corpo das
# funções, gravado como foi escrito). Os parênteses só entram como PAR: o ")" que fecha o grupo
# do OR nunca é engolido pela última comparação.


def _when(n: int) -> str:
    cmp = rf"pa\.slot{n}_segment_id\s*=\s*leaf\.id"
    return rf"WHEN\s+(?:\(\s*{cmp}\s*\)|{cmp})\s+THEN\s+pa\.shipping{n}\b"


def _ou(n: int) -> str:
    cmp = rf"leaf\.id\s*=\s*pa\.slot{n}_segment_id\b"
    return rf"(?:\(\s*{cmp}\s*\)|{cmp})"


_SEP_CASE = r"\s+"
_SEP_OU = r"\s+OR\s+"


def _cinco(pedaco, sep: str) -> str:
    """As comparações 1..5 em sequência (grupo `cinco`); a 5ª e o separador antes dela ficam em
    grupos (`sep`, `ultima`) para as novas saírem no mesmo formato."""
    corpo = sep.join(pedaco(n) for n in range(1, ANTES))
    return f"(?P<cinco>{corpo}(?P<sep>{sep})(?P<ultima>{pedaco(ANTES)}))"


def _dez(pedaco, sep: str) -> str:
    """A forma estendida (1..10): o grupo `cinco` (1..5) seguido das posições 6..10."""
    cinco = sep.join(pedaco(n) for n in range(1, ANTES + 1))
    novas = "".join(f"(?:{sep}{pedaco(n)})" for n in NOVAS)
    return f"(?P<cinco>{cinco}){novas}"


# Sem a 6ª logo depois (nunca estende duas vezes).
_CASE_5 = re.compile(_cinco(_when, _SEP_CASE) + f"(?!{_SEP_CASE}{_when(6)})")
_OU_5 = re.compile(_cinco(_ou, _SEP_OU) + f"(?!{_SEP_OU}{_ou(6)})")
# Para o downgrade voltar ao texto de 1..5.
_CASE_10 = re.compile(_dez(_when, _SEP_CASE))
_OU_10 = re.compile(_dez(_ou, _SEP_OU))


def _estender(m: re.Match) -> str:
    """O trecho de 1..5 + as posições 6..10 no mesmo formato da 5ª."""
    ultima = m.group("ultima")
    novas = "".join(
        m.group("sep")
        + ultima.replace(f"slot{ANTES}_segment_id", f"slot{n}_segment_id").replace(
            f"shipping{ANTES}", f"shipping{n}"
        )
        for n in NOVAS
    )
    return m.group("cinco") + novas


def _subir_texto(nome: str, texto: str) -> str:
    """O texto com as posições 6..10 no CASE e no JOIN. Para (RuntimeError) se não achar os dois
    trechos, ou se acharem quantidades diferentes."""
    novo, n_case = _CASE_5.subn(_estender, texto)
    novo, n_ou = _OU_5.subn(_estender, novo)
    if n_case == 0 or n_ou == 0 or n_case != n_ou:
        raise RuntimeError(
            f"0520: {nome} não tem o trecho esperado do frete projetado (slot1..5): "
            f"CASE {n_case}, JOIN {n_ou}. Nada foi mudado; confira a definição."
        )
    return novo


def _descer_texto(nome: str, texto: str) -> str:
    novo, n_case = _CASE_10.subn(lambda m: m.group("cinco"), texto)
    novo, n_ou = _OU_10.subn(lambda m: m.group("cinco"), novo)
    if re.search(r"slot(?:6|7|8|9|10)_segment_id|shipping(?:6|7|8|9|10)\b", novo):
        raise RuntimeError(
            f"0520 (downgrade): {nome} ainda usa as posições 6..10 fora do trecho do frete "
            f"projetado (CASE {n_case}, JOIN {n_ou}); confira a definição."
        )
    return novo


def _ja_tem_6(texto: str) -> bool:
    return re.search(r"\bslot6_segment_id\b", texto) is not None


def _views_e_funcoes(subir: bool) -> None:
    bind = op.get_bind()
    trocar = _subir_texto if subir else _descer_texto
    for view in VIEWS:
        qual = f'"{SCHEMA}".{view}'
        existe = bind.execute(sa.text("SELECT to_regclass(:q)"), {"q": qual}).scalar()
        if existe is None:
            print(f"0520: view {qual} não existe; pulada.")
            continue
        texto = bind.execute(
            sa.text("SELECT pg_get_viewdef(to_regclass(:q), false)"), {"q": qual}
        ).scalar_one()
        if subir and _ja_tem_6(texto):
            continue
        if not subir and not _ja_tem_6(texto):
            continue
        corpo = trocar(qual, texto).rstrip().rstrip(";")
        # Mesmas colunas, mesmo tipo: CREATE OR REPLACE mantém dono, permissões e dependentes.
        # Texto do próprio banco, mandado como está (sem o text() do SQLAlchemy, que leria um
        # ":palavra" dentro de um literal como parâmetro).
        bind.exec_driver_sql(f"CREATE OR REPLACE VIEW {qual} AS {corpo}")
    for funcao in FUNCOES:
        oids = (
            bind.execute(
                sa.text(
                    "SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = :s AND p.proname = :f ORDER BY p.oid"
                ),
                {"s": SCHEMA, "f": funcao},
            )
            .scalars()
            .all()
        )
        if not oids:
            print(f'0520: função "{SCHEMA}".{funcao} não existe; pulada.')
        for oid in oids:
            texto = bind.execute(sa.text("SELECT pg_get_functiondef(:o)"), {"o": oid}).scalar_one()
            if subir and _ja_tem_6(texto):
                continue
            if not subir and not _ja_tem_6(texto):
                continue
            # pg_get_functiondef já é um "CREATE OR REPLACE FUNCTION …" completo (mesma
            # assinatura: dono, permissões e atributos ficam).
            bind.exec_driver_sql(trocar(f'"{SCHEMA}".{funcao}', texto))


def _avisar_dado_antigo() -> None:
    """Categoria de venda já gravada depois da 5ª posição (sort_order 5 ou mais) com produto na
    Tabela de Preços: o preço dela deixa de usar a margem/frete da 5ª posição (ver o docstring).
    Só avisa. (Posição abaixo de 1 continua caindo na 1ª, como antes.)"""
    linhas = (
        op.get_bind()
        .execute(
            sa.text(
                f'SELECT r.name, s.name, s.sort_order, count(pp.id) FROM "{SCHEMA}".segments s '  # noqa: S608
                f'JOIN "{SCHEMA}".segments r ON r.id = s.parent_id AND r.parent_id IS NULL '
                f'JOIN "{SCHEMA}".pricing_products pp ON pp.segment_id = s.id '
                f"WHERE s.sort_order >= {ANTES} "
                "GROUP BY r.name, s.name, s.sort_order ORDER BY r.name, s.sort_order"
            )
        )
        .all()
    )
    for raiz, nome, ordem, n in linhas:
        print(
            f"0520 AVISO: categoria de venda “{nome}” ({raiz}) está na posição {ordem + 1} e tem "
            f"{n} produto(s) na Tabela de Preços: até aqui o preço deles usava a margem/frete da "
            f"posição 5; agora usa a da posição {min(ordem + 1, NOVAS[-1])}."
        )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for n in NOVAS:
        op.add_column(
            TABELA, sa.Column(f"margin{n}", sa.Numeric(6, 4), nullable=True), schema=SCHEMA
        )
        op.add_column(
            TABELA, sa.Column(f"shipping{n}", sa.Numeric(8, 2), nullable=True), schema=SCHEMA
        )
    for n in NOVAS:
        op.add_column(
            TABELA,
            sa.Column(f"slot{n}_segment_id", PG_UUID(as_uuid=True), nullable=True),
            schema=SCHEMA,
        )
        op.create_foreign_key(
            _fk(n),
            TABELA,
            "segments",
            [f"slot{n}_segment_id"],
            ["id"],
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
            ondelete="SET NULL",
        )
        op.create_index(_ix(n), TABELA, [f"slot{n}_segment_id"], schema=SCHEMA)
    _views_e_funcoes(subir=True)
    _avisar_dado_antigo()


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # ver o upgrade
    # Antes das colunas: as views dependem de slot6..10/shipping6..10.
    _views_e_funcoes(subir=False)
    for n in reversed(NOVAS):
        op.drop_index(_ix(n), table_name=TABELA, schema=SCHEMA)
        op.drop_constraint(_fk(n), TABELA, schema=SCHEMA, type_="foreignkey")
        op.drop_column(TABELA, f"slot{n}_segment_id", schema=SCHEMA)
    for n in reversed(NOVAS):
        op.drop_column(TABELA, f"shipping{n}", schema=SCHEMA)
        op.drop_column(TABELA, f"margin{n}", schema=SCHEMA)
