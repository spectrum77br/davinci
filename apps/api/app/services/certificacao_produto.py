"""Certificado de cada produto da Tabela de Preços (05/11/2026).

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
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financeiro import FinanceiroSuprimentos
from app.models.pricing import PricingProduct

if TYPE_CHECKING:
    from app.services.garantia_prazo import Mudanca

logger = structlog.get_logger(__name__)

# Campos do certificado que decidem quem o acha pelo nome (`chaves` e
# `_melhor`): mudou um deles, o certificado efetivo de algum produto pode mudar.
CAMPOS_DO_CASAMENTO = frozenset({"modelo", "nome_comercial", "numero", "fim"})

# Modelo achado no MEIO do nome (sem o " - código") precisa ser comprido:
# "UCW001", "UAF001 M1"; "F109"/"W69" ficam de fora.
_MIN_MODELO_NO_NOME = 6


def _norm(texto: str | None) -> str:
    """Maiúsculas, só letras e números separados por um espaço:
    "QZ-0180AE2H" → "QZ 0180AE2H"; "UAF001-M1" → "UAF001 M1"."""
    return " ".join(re.sub(r"[^0-9A-Za-z]+", " ", texto or "").upper().split())


def _codigo(texto: str) -> bool:
    """Palavra com letra E número (USM003, UAT001) — não "25" nem "Pro"."""
    return bool(re.search(r"[A-Z]", texto)) and bool(re.search(r"[0-9]", texto))


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

    def chaves(self) -> set[str]:
        """Códigos que, no fim do nome do produto, apontam para esta linha."""
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
    """O que vem depois do último " - " quando é uma palavra só."""
    if not nome or " - " not in nome:
        return None
    fim = nome.rsplit(" - ", 1)[1].strip()
    return fim if fim and " " not in fim else None


def _melhor(candidatos: list[Certificacao]) -> Certificacao | None:
    if not candidatos:
        return None
    uniq = {c.id: c for c in candidatos}.values()

    def peso(c: Certificacao) -> tuple[bool, date]:
        return (bool((c.numero or "").strip()), c.fim or date.min)

    ordenados = sorted(uniq, key=peso, reverse=True)
    if len(ordenados) > 1 and peso(ordenados[0]) == peso(ordenados[1]):
        return None  # empate: não dá para escolher sozinho
    return ordenados[0]


def certificacao_do_produto(
    nome: str | None, certificacoes: list[Certificacao]
) -> Certificacao | None:
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


def certificado_efetivo(
    certificacao_id: UUID | None, nome: str | None, certificacoes: list[Certificacao]
) -> tuple[Certificacao | None, bool]:
    """O certificado do produto: o gravado na linha (coluna Modelo) ou, sem
    ele, o achado pelo código no nome. Devolve (certificado, achado pelo nome).
    É o mesmo que a Tabela de Preços mostra (routers/pricing._product_out)."""
    if certificacao_id is not None:
        return next((c for c in certificacoes if c.id == certificacao_id), None), False
    cert = certificacao_do_produto(nome, certificacoes)
    return cert, cert is not None


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


async def produtos_do_certificado(
    session: AsyncSession,
    certificacao_id: UUID,
    certificacoes: list[Certificacao] | None = None,
) -> list[PricingProduct]:
    """Todas as linhas da Tabela de Preços (de qualquer usuário) cujo
    certificado efetivo é este: as que o gravaram na coluna Modelo e as sem
    vínculo gravado que o acham pelo código no nome."""
    if certificacoes is None:
        certificacoes = await carregar_certificacoes(session)
    # Só id e nome das linhas sem vínculo (são quase todas): a linha inteira
    # vem depois, só para as que acham este certificado pelo nome.
    sem_vinculo = (
        await session.execute(
            select(PricingProduct.id, PricingProduct.name).where(
                PricingProduct.certificacao_id.is_(None)
            )
        )
    ).all()
    pelo_nome = [
        pid
        for pid, nome in sem_vinculo
        if (c := certificacao_do_produto(nome, certificacoes)) is not None
        and c.id == certificacao_id
    ]
    filtro = PricingProduct.certificacao_id == certificacao_id
    if pelo_nome:
        filtro = or_(filtro, PricingProduct.id.in_(pelo_nome))
    return list(
        (
            await session.execute(select(PricingProduct).where(filtro).order_by(PricingProduct.sku))
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
    certificacoes: list[Certificacao] | None = None,
) -> list[Mudanca]:
    """Põe a Garantia (meses) do certificado em todos os produtos dele
    (`produtos_do_certificado`). Garantia vazia (None) não mexe em ninguém.
    Devolve as mudanças para `garantia_prazo.registrar_mudancas` (vigência +
    recálculo das garantias). NÃO commita."""
    from app.services import garantia_prazo

    if meses is None:
        return []
    linhas = await produtos_do_certificado(session, certificacao_id, certificacoes)
    return garantia_prazo.trocar_prazo(linhas, meses)


# Produto → id do certificado efetivo (None = sem certificado).
Retrato = dict[UUID, UUID | None]


async def certificado_de_cada_produto(
    session: AsyncSession, certificacoes: list[Certificacao] | None = None
) -> Retrato:
    """O certificado efetivo de cada linha da Tabela de Preços (de qualquer
    usuário), numa leitura só: o retrato de antes de uma mudança nos
    certificados, para `garantia_de_quem_trocou_de_certificado`."""
    if certificacoes is None:
        certificacoes = await carregar_certificacoes(session)
    ids = {c.id for c in certificacoes}
    linhas = (
        await session.execute(
            select(PricingProduct.id, PricingProduct.certificacao_id, PricingProduct.name)
        )
    ).all()
    out: Retrato = {}
    for pid, cid, nome in linhas:
        if cid is not None:
            out[pid] = cid if cid in ids else None
        else:
            cert = certificacao_do_produto(nome, certificacoes)
            out[pid] = cert.id if cert is not None else None
    return out


async def garantia_de_quem_trocou_de_certificado(
    session: AsyncSession, antes: Retrato
) -> list[Mudanca]:
    """Revisão de 06/11/2026: o produto que passou a OUTRO certificado efetivo
    por uma mudança nos certificados (Fim, Modelo ou Nº editados na
    Certificações ou a Validade pela Tabela de Preços, certificado novo ou
    excluído, sincronização oficial) e o novo tem garantia → pega a garantia
    dele ("ele passa a mudar de acordo com o certificado"). Foi para um
    certificado sem garantia, ou ficou sem nenhum: fica com o prazo que tem.

    `antes` = `certificado_de_cada_produto` de ANTES da mudança. A linha que
    não estava no retrato (cadastrada no meio) fica de fora: quem cadastrou já
    deu o prazo dela. Devolve as mudanças para
    `garantia_prazo.registrar_mudancas`. NÃO commita."""
    from app.services import garantia_prazo

    await session.flush()
    certs = await carregar_certificacoes(session)
    depois = await certificado_de_cada_produto(session, certs)
    garantia = {c.id: c.garantia_meses for c in certs if c.garantia_meses is not None}
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
    do robô (`garantia_de_quem_trocou_de_certificado`). Num savepoint próprio:
    se falhar, só fica no log — a sincronização dos certificados vale do mesmo
    jeito. NÃO commita."""
    from app.services import garantia_prazo

    try:
        async with session.begin_nested():
            mudancas = await garantia_de_quem_trocou_de_certificado(session, antes)
            await garantia_prazo.registrar_mudancas(session, mudancas, None)
    except Exception:
        logger.exception("certificacao.garantia_apos_sincronizacao_falhou")
