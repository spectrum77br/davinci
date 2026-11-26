"""Certificado de cada produto da Tabela de Preços (05/11/2026; pelo SKU desde
a M-69, 26/11/2026 — veja o fim deste texto).

Eduardo: na aba Produtos, colunas "modelo e número de certificado e prazo",
"que são as informações que já tem na certificação, é só copiar". Não há
vínculo gravado entre as duas tabelas: o nome do produto já traz o código do
certificado (convenção da casa) e a linha da Certificações é achada por ele.
Mudou o certificado (renovação, número novo) → a Tabela de Preços acompanha.

* Celular: o código vem no fim, depois do último " - "
  ("uranyx A17 pro max 12.128 - USM003", "uranyx fone - UFB10",
  "uranyx carregador 20w - QZ-0180AE2H"). Ele casa com o modelo do
  certificado inteiro (USM003), com uma palavra do modelo que tenha letra e
  número ("AirTag UAT001" → UAT001) ou com o nome comercial quando ele é um
  código só (o fone A9 Pro é vendido como UFB10).
* Eletro: o modelo vem no meio do nome ("airfryer vidro UAF001 M1
  MODERNA-0069/25" → UAF001-M1). Só vale para modelo com 6+ caracteres,
  para "F109" (bateria) não pegar o celular F109.

Nada casou, ou casou com dois certificados empatados → sem certificado
("—" na tela). Com mais de um, fica o que tem número e a validade mais longa
(certificado renovado cadastrado em linha nova).

Desde 05/11 à tarde o certificado também é GRAVADO na linha
(`pricing_products.certificacao_id`, 0457): o gravado vence; sem ele, o nome.
Esse é o "certificado efetivo" do produto (`certificado_efetivo`).

Garantia e validade pelo certificado (06/11/2026, Eduardo: "em certificações
adicione o campo garantia, e aí ele passa a mudar de acordo com o
certificado"; "se eu mudar em certificações muda a coluna validade lá na
tabela de preços, e deixe a opção de editar na tabela de preços"):

* O certificado tem Garantia (meses) (`financeiro_suprimentos.garantia_meses`,
  0461). Mudou → todos os produtos cujo certificado efetivo é ele passam a ter
  esse prazo (`produtos_do_certificado` + `garantia_prazo.trocar_prazo`), pelo
  mesmo caminho do PATCH do produto (vigência + recálculo). Vazio (NULL) não
  mexe nos produtos.
* Editar a Garantia ou a Validade na Tabela de Preços de um produto com
  certificado grava NO CERTIFICADO (`gravar_no_certificado`) e vale para todos
  os produtos dele.
* O produto que passa a OUTRO certificado sem ninguém mexer nele (a Validade,
  o Modelo ou o Nº do certificado mudou, um certificado foi criado ou
  excluído, a sincronização oficial atualizou a linha) e o novo tem garantia:
  pega a garantia dele (`garantia_de_quem_trocou_de_certificado`, revisão de
  06/11/2026). Foi para um certificado sem garantia, ou ficou sem nenhum:
  fica com o prazo que tem.

M-69 (26/11/2026, Harry: "O SKU passa a ser a ligação entre certificado e
produto"; contrato em relatorios/mudancas_26nov/contrato_M66_M74.md):

* A certificação ganhou a coluna SKU (`financeiro_suprimento_skus`, 0512):
  vários SKUs por certificado, cada SKU num certificado só, guardado SEM a tag
  (`familia`: "dg019.ra" → "dg019"; "uaf001m1.110" → "uaf001m1").
* Certificado efetivo de uma linha da Tabela de Preços (`certificado_efetivo`):
  1. as famílias da linha (`pricing_products.sku` separado por vírgula, cada
     código pela família) → os certificados delas na coluna SKU;
  2. um certificado → é ele;
  3. mais de um → conflito: não escolhe sozinho; vale o gravado na linha
     (`certificacao_id`), se houver, e a tela avisa (`conflito`);
  4. nenhum → o gravado na linha (só sobra para o que a conversão da 0512 não
     conseguiu dizer pelo SKU).
* A LIGAÇÃO PELO NOME SAIU do código vivo. A cópia congelada dela, que a
  conversão da 0512 usou para não mudar o certificado de ninguém, está na
  própria migration. Por isso a Validade, o Modelo e o Nº de um certificado
  não mudam mais quem é dono de qual produto: só a coluna SKU, um certificado
  excluído e o SKU da linha da Tabela de Preços mudam.
* A coluna Modelo da Tabela de Preços ficou só para leitura (o PATCH de
  `certificacao_id` por lá é recusado, `MODELO_SO_LEITURA`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financeiro import FinanceiroSuprimentos, FinanceiroSuprimentoSku
from app.models.pricing import PricingProduct
from app.models.product import Product

if TYPE_CHECKING:
    from app.services.garantia_prazo import Mudanca

logger = structlog.get_logger(__name__)

# A frase da coluna Modelo da Tabela de Preços (M-69): ela só mostra o
# certificado; quem liga é a coluna SKU da Certificações.
MODELO_SO_LEITURA = "definido em Importação › Certificações"


# ── SKU sem a tag (M-69) ──────────────────────────────────────────────────────


def familia(codigo: object) -> str:
    """O SKU sem a tag, igual à family() do ERP (erp_local/price_table.py):
    corta no "+" e no ".". "dg019.ra" → "dg019"; "dg019.ra+a020.ra" → "dg019";
    "uaf001m1.110" → "uaf001m1" (a voltagem também sai: o certificado cobre o
    110 e o 220). É A regra do servidor para o SKU da certificação."""
    return str(codigo or "").split("+")[0].split(".")[0].strip().lower()


def familias_da_linha(sku: str | None) -> list[str]:
    """As famílias de uma linha da Tabela de Preços ("dg052,dg053,dg054" ou
    "uaf001m1.110, uaf001m1.220"): separadas por vírgula, cada código pela
    família, sem repetir, na ordem."""
    vistas: dict[str, None] = {}
    for codigo in str(sku or "").split(","):
        f = familia(codigo)
        if f:
            vistas.setdefault(f, None)
    return list(vistas)


_SEPARADORES = re.compile(r"[,;\s]+")


def _ordem_natural(sku: str) -> tuple:
    """"dg017" < "dg101" < "dg1000" (o número pelo valor)."""
    return tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.findall(r"\d+|\D+", sku)
    )


def ordenar_skus(skus: Iterable[str]) -> list[str]:
    return sorted(set(skus), key=_ordem_natural)


def skus_da_entrada(valor: str | Iterable[str] | None) -> list[str]:
    """O que alguém escreveu na coluna SKU: vírgula, ponto e vírgula, espaço e
    quebra de linha separam; cada SKU vai pela família (sem a tag); vazios saem
    e repetidos viram um só. Lista (também aceita) = cada item passa pela mesma
    regra. Sai em ordem ("dg017, dg018, dg019")."""
    if valor is None:
        return []
    partes = [valor] if isinstance(valor, str) else [str(v) for v in valor if v is not None]
    out: list[str] = []
    for parte in partes:
        out.extend(f for f in (familia(p) for p in _SEPARADORES.split(parte)) if f)
    return ordenar_skus(out)


# ── Certificados ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Certificacao:
    id: UUID
    produto: str | None
    modelo: str | None
    nome_comercial: str | None
    certificado: str | None
    numero: str | None
    fim: date | None
    garantia_meses: int | None = None


def nome_da_certificacao(c: Certificacao | FinanceiroSuprimentos) -> str:
    """Como a certificação é chamada nos avisos ("dg019 já está na certificação
    USM001"): o Modelo, senão o Nome comercial, senão o Número, senão o
    Produto."""
    for campo in (c.modelo, c.nome_comercial, c.numero, c.produto):
        if campo and campo.strip():
            return campo.strip()
    return "sem nome"


@dataclass
class Ligacoes:
    """Tudo o que decide o certificado de um produto, lido de uma vez: as
    linhas da Certificações e a coluna SKU delas (família → certificado)."""

    certificacoes: list[Certificacao]
    skus: dict[str, UUID] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.por_id = {c.id: c for c in self.certificacoes}


def certificados_pelos_skus(sku: str | None, ligacoes: Ligacoes) -> list[Certificacao]:
    """Os certificados que a coluna SKU dá para as famílias desta linha, sem
    repetir, na ordem das famílias."""
    out: dict[UUID, Certificacao] = {}
    for f in familias_da_linha(sku):
        cid = ligacoes.skus.get(f)
        if cid is not None and cid in ligacoes.por_id:
            out.setdefault(cid, ligacoes.por_id[cid])
    return list(out.values())


def certificado_efetivo(
    certificacao_id: UUID | None, sku: str | None, ligacoes: Ligacoes
) -> tuple[Certificacao | None, list[Certificacao]]:
    """O certificado do produto (M-69): o da coluna SKU da Certificações; com
    dois ou mais (códigos da linha em certificados diferentes) ou nenhum, o
    gravado na linha. Devolve (certificado, conflito): `conflito` = os
    certificados da coluna SKU quando são mais de um (a tela avisa; o sistema
    não escolhe sozinho). É o mesmo que a Tabela de Preços mostra
    (routers/pricing._product_out)."""
    achados = certificados_pelos_skus(sku, ligacoes)
    if len(achados) == 1:
        return achados[0], []
    gravado = ligacoes.por_id.get(certificacao_id) if certificacao_id is not None else None
    return gravado, achados if len(achados) > 1 else []


async def carregar_certificacoes(session: AsyncSession) -> list[Certificacao]:
    rows = (
        await session.execute(
            select(
                FinanceiroSuprimentos.id,
                FinanceiroSuprimentos.produto,
                FinanceiroSuprimentos.modelo,
                FinanceiroSuprimentos.nome_comercial,
                FinanceiroSuprimentos.certificado,
                FinanceiroSuprimentos.numero,
                FinanceiroSuprimentos.fim,
                FinanceiroSuprimentos.garantia_meses,
            )
        )
    ).all()
    return [Certificacao(*r) for r in rows]


async def carregar_skus(session: AsyncSession) -> dict[str, UUID]:
    """A coluna SKU de todas as certificações: família → certificado."""
    rows = await session.execute(
        select(FinanceiroSuprimentoSku.sku, FinanceiroSuprimentoSku.suprimento_id)
    )
    return dict(rows.all())


async def carregar_ligacoes(session: AsyncSession) -> Ligacoes:
    return Ligacoes(await carregar_certificacoes(session), await carregar_skus(session))


async def skus_por_certificado(
    session: AsyncSession, ids: Iterable[UUID] | None = None
) -> dict[UUID, list[str]]:
    """A coluna SKU de cada certificação (em ordem). `ids` = só estas."""
    stmt = select(FinanceiroSuprimentoSku.suprimento_id, FinanceiroSuprimentoSku.sku)
    if ids is not None:
        stmt = stmt.where(FinanceiroSuprimentoSku.suprimento_id.in_(list(ids)))
    out: dict[UUID, list[str]] = defaultdict(list)
    for cid, sku in (await session.execute(stmt)).all():
        out[cid].append(sku)
    return {cid: ordenar_skus(skus) for cid, skus in out.items()}


# ── Coluna SKU: conferência e gravação ───────────────────────────────────────


class SkuEmOutroCertificadoError(Exception):
    """Um SKU pedido já está em outra certificação: nada grava (409)."""

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem


async def conferir_skus_livres(
    session: AsyncSession, certificacao_id: UUID | None, skus: list[str]
) -> None:
    """SKU que já está em OUTRA certificação → SkuEmOutroCertificadoError com
    "dg019 já está na certificação USM001" (um por SKU, separados por "; ")."""
    if not skus:
        return
    stmt = (
        select(FinanceiroSuprimentoSku.sku, FinanceiroSuprimentos)
        .join(
            FinanceiroSuprimentos,
            FinanceiroSuprimentos.id == FinanceiroSuprimentoSku.suprimento_id,
        )
        .where(FinanceiroSuprimentoSku.sku.in_(skus))
    )
    if certificacao_id is not None:
        stmt = stmt.where(FinanceiroSuprimentoSku.suprimento_id != certificacao_id)
    ocupados = sorted((await session.execute(stmt)).all(), key=lambda r: _ordem_natural(r[0]))
    if ocupados:
        raise SkuEmOutroCertificadoError(
            "; ".join(
                f"{sku} já está na certificação {nome_da_certificacao(dono)}"
                for sku, dono in ocupados
            )
        )


async def gravar_skus(session: AsyncSession, certificacao_id: UUID, skus: list[str]) -> bool:
    """Deixa a coluna SKU desta certificação exatamente com `skus` (já
    normalizados e conferidos). Devolve se mudou. NÃO commita."""
    atuais = set(
        (
            await session.execute(
                select(FinanceiroSuprimentoSku.sku).where(
                    FinanceiroSuprimentoSku.suprimento_id == certificacao_id
                )
            )
        ).scalars()
    )
    novos = set(skus)
    if atuais == novos:
        return False
    if atuais - novos:
        await session.execute(
            FinanceiroSuprimentoSku.__table__.delete().where(
                FinanceiroSuprimentoSku.__table__.c.suprimento_id == certificacao_id,
                FinanceiroSuprimentoSku.__table__.c.sku.in_(sorted(atuais - novos)),
            )
        )
    for sku in ordenar_skus(novos - atuais):
        session.add(FinanceiroSuprimentoSku(sku=sku, suprimento_id=certificacao_id))
    await session.flush()
    return True


def _familia_sql(coluna):
    """A `familia` em SQL (para `products.sku`)."""
    return func.lower(func.trim(func.split_part(func.split_part(coluna, "+", 1), ".", 1)))


async def skus_sem_produto(session: AsyncSession, skus: list[str]) -> list[str]:
    """Os SKUs (já sem a tag) que não existem em produto nenhum: nem numa linha
    da Tabela de Preços (cada código pela família), nem no cadastro de
    produtos (`products.sku` pela família). Só aviso: grava assim mesmo (o
    produto pode ser cadastrado depois)."""
    if not skus:
        return []
    conhecidos: set[str] = set()
    for (sku,) in (await session.execute(select(PricingProduct.sku))).all():
        conhecidos.update(familias_da_linha(sku))
    faltam = [s for s in skus if s not in conhecidos]
    if faltam:
        fam = _familia_sql(Product.sku)
        conhecidos.update(
            (await session.execute(select(fam).where(fam.in_(faltam)).distinct())).scalars()
        )
    return [s for s in skus if s not in conhecidos]


def aviso_sem_produto(sku: str) -> str:
    return f"{sku} não existe em nenhum produto"


# ── Produtos de um certificado ────────────────────────────────────────────────


async def _linhas_e_efetivo(
    session: AsyncSession, ligacoes: Ligacoes
) -> list[tuple[UUID, UUID | None]]:
    """(id da linha, id do certificado efetivo) de toda a Tabela de Preços (de
    qualquer usuário), lendo só id, SKU e o gravado."""
    linhas = (
        await session.execute(
            select(PricingProduct.id, PricingProduct.sku, PricingProduct.certificacao_id)
        )
    ).all()
    out: list[tuple[UUID, UUID | None]] = []
    for pid, sku, cid in linhas:
        cert, _ = certificado_efetivo(cid, sku, ligacoes)
        out.append((pid, cert.id if cert is not None else None))
    return out


async def produtos_do_certificado(
    session: AsyncSession,
    certificacao_id: UUID,
    ligacoes: Ligacoes | None = None,
) -> list[PricingProduct]:
    """Todas as linhas da Tabela de Preços (de qualquer usuário) cujo
    certificado efetivo é este (`certificado_efetivo`: pela coluna SKU ou, sem
    ela ou com conflito, pelo gravado na linha)."""
    if ligacoes is None:
        ligacoes = await carregar_ligacoes(session)
    efetivos = await _linhas_e_efetivo(session, ligacoes)
    ids = [pid for pid, cid in efetivos if cid == certificacao_id]
    if not ids:
        return []
    return list(
        (
            await session.execute(
                select(PricingProduct).where(PricingProduct.id.in_(ids)).order_by(PricingProduct.sku)
            )
        ).scalars()
    )


def gravar_no_certificado(row: FinanceiroSuprimentos, campo: str, valor: object) -> bool:
    """Grava um campo do certificado vindo de outra tela (a Tabela de Preços)
    como a tela de Certificações grava: o campo mudado entra em
    `campos_manuais`, e a sincronização oficial (Anatel/Inmetro) não o
    sobrescreve mais. Devolve se mudou."""
    if getattr(row, campo) == valor:
        return False
    row.campos_manuais = sorted(set(row.campos_manuais or []) | {campo})
    setattr(row, campo, valor)
    return True


async def passar_garantia_aos_produtos(
    session: AsyncSession,
    certificacao_id: UUID,
    meses: int | None,
    ligacoes: Ligacoes | None = None,
) -> list[Mudanca]:
    """Põe a Garantia (meses) do certificado em todos os produtos dele
    (`produtos_do_certificado`). Garantia vazia (None) não mexe em ninguém.
    Devolve as mudanças para `garantia_prazo.registrar_mudancas` (vigência +
    recálculo das garantias). NÃO commita."""
    from app.services import garantia_prazo

    if meses is None:
        return []
    linhas = await produtos_do_certificado(session, certificacao_id, ligacoes)
    return garantia_prazo.trocar_prazo(linhas, meses)


# Produto → id do certificado efetivo (None = sem certificado).
Retrato = dict[UUID, UUID | None]


async def certificado_de_cada_produto(
    session: AsyncSession, ligacoes: Ligacoes | None = None
) -> Retrato:
    """O certificado efetivo de cada linha da Tabela de Preços (de qualquer
    usuário), numa leitura só: o retrato de antes de uma mudança nos
    certificados (coluna SKU, certificado excluído), para
    `garantia_de_quem_trocou_de_certificado`."""
    if ligacoes is None:
        ligacoes = await carregar_ligacoes(session)
    return dict(await _linhas_e_efetivo(session, ligacoes))


async def garantia_de_quem_trocou_de_certificado(
    session: AsyncSession, antes: Retrato
) -> list[Mudanca]:
    """Revisão de 06/11/2026: o produto que passou a OUTRO certificado efetivo
    por uma mudança nos certificados (desde a M-69: a coluna SKU mudou, um
    certificado novo com SKU, um certificado excluído) e o novo tem garantia →
    pega a garantia dele ("ele passa a mudar de acordo com o certificado").
    Foi para um certificado sem garantia, ou ficou sem nenhum: fica com o
    prazo que tem.

    `antes` = `certificado_de_cada_produto` de ANTES da mudança. A linha que
    não estava no retrato (cadastrada no meio) fica de fora: quem cadastrou já
    deu o prazo dela. Devolve as mudanças para
    `garantia_prazo.registrar_mudancas`. NÃO commita."""
    from app.services import garantia_prazo

    await session.flush()
    ligacoes = await carregar_ligacoes(session)
    depois = await certificado_de_cada_produto(session, ligacoes)
    garantia = {
        c.id: c.garantia_meses for c in ligacoes.certificacoes if c.garantia_meses is not None
    }
    por_certificado: dict[UUID, list[UUID]] = defaultdict(list)
    for pid, cid in depois.items():
        if cid in garantia and pid in antes and antes[pid] != cid:
            por_certificado[cid].append(pid)
    mudancas: list[Mudanca] = []
    for cid, pids in por_certificado.items():
        linhas = (
            await session.execute(
                select(PricingProduct)
                .where(PricingProduct.id.in_(pids))
                .order_by(PricingProduct.sku)
            )
        ).scalars()
        mudancas += garantia_prazo.trocar_prazo(linhas, garantia[cid])
    if mudancas:
        logger.info(
            "certificacao.garantia_de_quem_trocou",
            produtos=len(mudancas),
            certificados=len(por_certificado),
        )
    return mudancas


async def garantia_apos_sincronizacao(session: AsyncSession, antes: Retrato) -> None:
    """A sincronização oficial (Anatel/Inmetro) mudou certificados: o produto
    que passou a outro certificado com garantia pega a garantia dele, em nome
    do robô (`garantia_de_quem_trocou_de_certificado`). Desde a M-69 a
    sincronização não liga produto (as linhas novas chegam sem SKU), então
    isto quase nunca acha ninguém; fica como rede de segurança. Num savepoint
    próprio: se falhar, só fica no log — a sincronização dos certificados vale
    do mesmo jeito. NÃO commita."""
    from app.services import garantia_prazo

    try:
        async with session.begin_nested():
            mudancas = await garantia_de_quem_trocou_de_certificado(session, antes)
            await garantia_prazo.registrar_mudancas(session, mudancas, None)
    except Exception:
        logger.exception("certificacao.garantia_apos_sincronizacao_falhou")
