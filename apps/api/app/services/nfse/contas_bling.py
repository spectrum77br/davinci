"""Tomadores automáticos: as contas Bling de NF (30/09/2026).

Eduardo: "colocar os bling de nf já cadastrados e deixar a opção de cadastrar
avulso também (precisa ser atualizado os bling toda vez que mudar)" — "são elas
mesmo, as que utilizamos para fazer as notas fiscais".

As contas Bling do NF Faturador (perfis 104/44/45…) são trocadas de tempos em
tempos e o cadastro do faturador não guarda o CNPJ delas. Quem sabe o CNPJ de
verdade é a própria NF-e autorizada: `nf_nota.emitente_cnpj`. Então a lista vem
das notas de produto emitidas nos últimos 90 dias por quem NÃO é empresa do
grupo (as do grupo emitem as notas de 1–2%/embalagem e são as prestadoras da
nota de serviço, não tomadoras). Ago–set/2026: COMERCIAL DL, PEDRO FIORAVANTE
DAL SASSO (conta nova do 104) e COMERCIAL TELES (Bling 44).

Conta nova emitindo → vira tomador na próxima vez que a lista abrir; nome e
endereço (do `enderEmit` do XML) acompanham a nota mais recente.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company
from app.models.nf import NfNota
from app.models.nfse import NfseTomador

JANELA = timedelta(days=90)
SP = ZoneInfo("America/Sao_Paulo")


@dataclass
class ContaBling:
    cnpj: str
    nome: str
    notas_mes: int  # notas emitidas no mês corrente
    notas: int  # nos últimos 90 dias
    primeira: datetime | None
    ultima: datetime | None
    endereco: dict = field(default_factory=dict)

    def resumo(self) -> dict:
        return {
            "notas_mes": self.notas_mes,
            "notas_90d": self.notas,
            "primeira": self.primeira.isoformat() if self.primeira else None,
            "ultima": self.ultima.isoformat() if self.ultima else None,
        }


def _digitos(v: str | None) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit())


def endereco_do_emitente(xml: bytes | None) -> dict:
    """`<emit><enderEmit>` da NF-e → campos do tomador (vazio se não achar)."""
    if not xml:
        return {}
    try:
        raiz = ET.fromstring(xml)  # noqa: S314 — XML nosso (nf_nota), só leitura
    except ET.ParseError:
        return {}
    ender = next((el for el in raiz.iter() if el.tag.rsplit("}", 1)[-1] == "enderEmit"), None)
    if ender is None:
        return {}
    campos = {el.tag.rsplit("}", 1)[-1]: (el.text or "").strip() for el in ender}
    out = {
        "logradouro": campos.get("xLgr") or None,
        "numero": campos.get("nro") or None,
        "complemento": campos.get("xCpl") or None,
        "bairro": campos.get("xBairro") or None,
        "cmun_ibge": _digitos(campos.get("cMun"))[:7] or None,
        "cep": _digitos(campos.get("CEP"))[:8] or None,
    }
    return {k: v for k, v in out.items() if v}


async def contas_de_nf(session: AsyncSession, *, agora: datetime | None = None) -> list[ContaBling]:
    """Quem emitiu NF de produto nos últimos 90 dias e não é empresa do grupo."""
    agora = agora or datetime.now(UTC)
    desde = agora - JANELA
    hoje_sp = agora.astimezone(SP).date()
    inicio_mes = datetime(hoje_sp.year, hoje_sp.month, 1, tzinfo=SP)

    do_grupo = {
        _digitos(c) for (c,) in (await session.execute(select(Company.cnpj))).all() if _digitos(c)
    }
    cnpj = func.regexp_replace(func.coalesce(NfNota.emitente_cnpj, ""), r"\D", "", "g")
    linhas = (
        await session.execute(
            select(
                cnpj.label("cnpj"),
                func.count().label("n"),
                func.count().filter(NfNota.data_emissao >= inicio_mes).label("n_mes"),
                func.min(NfNota.data_emissao).label("primeira"),
                func.max(NfNota.data_emissao).label("ultima"),
            )
            .where(NfNota.data_emissao >= desde)
            .group_by(cnpj)
        )
    ).all()
    candidatos = {r.cnpj: r for r in linhas if len(r.cnpj) == 14 and r.cnpj not in do_grupo}
    if not candidatos:
        return []
    # A nota mais nova de cada conta: nome e endereço de agora.
    recentes = (
        await session.execute(
            select(cnpj.label("cnpj"), NfNota.emitente_nome, NfNota.xml)
            .where(NfNota.data_emissao >= desde, cnpj.in_(list(candidatos)))
            .distinct(cnpj)
            .order_by(cnpj, NfNota.data_emissao.desc())
        )
    ).all()
    por_cnpj = {r.cnpj: r for r in recentes}
    out = []
    for c, r in candidatos.items():
        rec = por_cnpj.get(c)
        out.append(
            ContaBling(
                cnpj=c,
                nome=((rec.emitente_nome if rec else None) or c).strip(),
                notas_mes=int(r.n_mes or 0),
                notas=int(r.n or 0),
                primeira=r.primeira,
                ultima=r.ultima,
                endereco=endereco_do_emitente(rec.xml if rec else None),
            )
        )
    return sorted(out, key=lambda x: x.ultima or datetime.min.replace(tzinfo=UTC), reverse=True)


async def sincronizar_tomadores(session: AsyncSession) -> dict[str, ContaBling]:
    """Cada conta Bling de NF vira (ou atualiza) um tomador "de fora". Não apaga
    nem desativa nada: conta que parou de emitir continua na lista. Devolve
    CNPJ → conta, pra tela marcar quais tomadores são contas Bling."""
    contas = await contas_de_nf(session)
    if not contas:
        return {}
    existentes: dict[str, NfseTomador] = {}
    for t in (
        await session.execute(
            select(NfseTomador).where(
                NfseTomador.tipo == "externo",
                NfseTomador.documento.in_([c.cnpj for c in contas]),
            )
        )
    ).scalars():
        existentes.setdefault(t.documento or "", t)
    mudou = False
    for c in contas:
        t = existentes.get(c.cnpj)
        if t is None:
            session.add(NfseTomador(tipo="externo", documento=c.cnpj, nome=c.nome, **c.endereco))
            mudou = True
            continue
        if c.nome and t.nome != c.nome:
            t.nome = c.nome
            mudou = True
        for campo, valor in c.endereco.items():
            if not getattr(t, campo):  # completa, nunca troca o que alguém digitou
                setattr(t, campo, valor)
                mudou = True
    if mudou:
        await session.commit()
    return {c.cnpj: c for c in contas}
