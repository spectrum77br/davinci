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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financeiro import FinanceiroSuprimentos

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
            )
        )
    ).all()
    return [Certificacao(*r) for r in rows]
