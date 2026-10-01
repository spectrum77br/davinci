"""Orquestra a NFS-e com o banco: prévia, emissão, atualização e cancelamento.

Motor: NFE.io (29/09/2026). A regra que manda em tudo continua: a linha da
emissão (com o `externalId` e o JSON exato) é gravada e commitada ANTES do
POST. Se a resposta se perder (timeout, 5xx), a linha fica `incerta` e quem
decide é o GET pelo `externalId` — nunca um segundo POST às cegas.

A NFE.io é assíncrona: o POST responde 202 e a nota fica `processando` até a
prefeitura responder. Quem termina é `atualizar()` (a tela logo depois de
enviar, a rotina de 2 em 2 min do worker e o webhook), sempre por GET.
"""

from __future__ import annotations

import asyncio
import base64
import html
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Company
from app.models.nfse import (
    STATUS_EM_ANDAMENTO,
    STATUS_VIVOS,
    CompanyFiscal,
    NfseEmissao,
    NfseEvento,
    NfseModelo,
    NfseTomador,
)
from app.services import email as email_svc
from app.services.nfse import chamadas, municipios, nfeio
from app.services.nfse import empresas as E  # noqa: N812
from app.services.nfse import texto as T  # noqa: N812
from app.services.nfse.ambiente import MSG_PRODUCAO_BLOQUEADA, eh_teste, pode_emitir
from app.services.nfse.erros import NfseError, explicar, explicar_texto
from app.services.nfse.faturamento import faturamento_da_empresa

logger = structlog.get_logger()

__all__ = ["NfseError"]

# Nota de percentual (29/09): o que pode faltar na conta base × %.
FALTA_BASE = (
    "Falta a base de cálculo: a empresa não teve venda no mês (ou não tem loja com o CNPJ "
    "dela em Cadastros › Lojas). Digite a base."
)
FALTA_PERCENTUAL = "Falta a porcentagem: defina na nota fixa ou na empresa (Cadastros › Empresas)."
VALOR_PEQUENO = "O valor calculado (base × %) dá menos de R$ 0,01."
MARCADOR_NO_FIXO = (
    "O texto da nota usa {percentual} ou {base}, mas a nota é de valor fixo: "
    "troque pelo texto ou mude a nota para percentual."
)
AVISO_TESTE = "Empresa em TESTE na NFE.io: nota simulada, sem valor fiscal."
AVISO_SEM_ENDERECO = (
    "O tomador está sem endereço completo: algumas prefeituras recusam a nota sem endereço."
)

# `incerta`/`enviando` sem nota na NFE.io só vira `rejeitada` depois disso: a
# busca pelo externalId demora uns segundos pra enxergar uma nota recém-criada.
ESPERA_INCERTA = timedelta(minutes=10)
# Cancelamento pedido e a nota ainda `Issued` na NFE.io: espera antes de desistir.
ESPERA_CANCELAMENTO = timedelta(minutes=10)
JANELA_CONFERENCIA = timedelta(days=15)
PARALELO = 5
CACHE_NOTAS_SEGUNDOS = 60.0

ALERTA_PDF = {
    "codigo": "",
    "descricao": "PDF pendente na NFE.io",
    "o_que_fazer": "A nota FOI emitida; só o PDF não ficou pronto. NÃO reemita: peça ao "
    "suporte da NFE.io para reprocessar o PDF.",
}
FLOW_EM_ANDAMENTO = {
    "WaitingCalculateTaxes",
    "WaitingDefineRpsNumber",
    "WaitingSend",
    "WaitingReturn",
    "WaitingDownload",
    "PullFromCityHall",
}


# --- o item (regra nossa, igual ao motor antigo) -----------------------------------


def _pct(v: Decimal | None) -> Decimal | None:
    return Decimal(v).quantize(T.QUATRO_CASAS) if v is not None else None


@dataclass
class Item:
    """Uma nota a emitir: de um modelo ou avulsa.

    `tipo_valor` 'percentual': valor = base_calculo × percentual ÷ 100 (conta
    feita aqui, nunca no front). `valor` None = falta a base ou o % (só na
    prévia). `percentual_origem`: de onde veio o % — ver `resolver_percentual`.
    Códigos do serviço vazios = os da empresa (company_fiscal).
    """

    company_id: UUID
    tomador_id: UUID
    descricao: str
    valor: Decimal | None
    modelo_id: UUID | None = None
    city_service_code: str | None = None
    federal_service_code: str | None = None
    c_nbs: str | None = None
    inf_comp: str | None = None
    tipo_valor: str = "fixo"
    base_calculo: Decimal | None = None
    percentual: Decimal | None = None
    percentual_origem: str | None = None
    # Percentual (30/09): de onde veio a base — ver `completar_base`.
    base_origem: str | None = None
    base_padrao: Decimal | None = None  # a base sugerida da nota fixa (reserva)
    faturamento: Any = None  # o faturamento do mês usado/mostrado (prévia)
    # 01/10/2026: de que mês é o faturamento da base (1º dia). None = o mesmo
    # mês da nota (a competência não muda) — ver `mes_da_base`.
    base_competencia: date | None = None

    def usar_fixo(self, valor: Decimal | None) -> Item:
        self.tipo_valor = "fixo"
        self.base_calculo = self.percentual = self.percentual_origem = None
        self.valor = valor
        return self

    def usar_percentual(
        self, base: Decimal | None, percentual: Decimal | None, origem: str | None = None
    ) -> Item:
        self.tipo_valor = "percentual"
        self.base_calculo = Decimal(base).quantize(T.CENTAVO) if base is not None else None
        self.percentual = _pct(percentual)
        self.percentual_origem = origem if self.percentual is not None else None
        self.valor = (
            T.valor_percentual(self.base_calculo, self.percentual)
            if self.base_calculo is not None and self.percentual is not None
            else None
        )
        return self


# De onde veio o % da nota de percentual (a prévia devolve em `percentual_origem`).
ORIGEM_ITEM = "item"  # digitado na hora (prévia/emitir)
ORIGEM_NOTA_FIXA = "nota_fixa"  # o % da própria nota fixa
ORIGEM_EMPRESA = "empresa"  # a porcentagem da empresa (Cadastros › Empresas)


def resolver_percentual(
    digitado: Decimal | None, nota_fixa: Decimal | None, empresa: Decimal | None
) -> tuple[Decimal | None, str | None]:
    """O % da nota de percentual (29/09, "a porcentagem de cada empresa"):
    o digitado → senão o da nota fixa → senão o da empresa → senão nenhum
    (vira o problema FALTA_PERCENTUAL).

    Digitado IGUAL ao que viria de qualquer jeito (a tela manda o % que mostrou,
    pra travar o que a pessoa viu) fica com a origem de onde ele veio: "0,5%
    (da empresa)" continua verdade mesmo vindo no item.
    """
    digitado, nota_fixa, empresa = _pct(digitado), _pct(nota_fixa), _pct(empresa)
    if nota_fixa is not None:
        padrao, origem = nota_fixa, ORIGEM_NOTA_FIXA
    elif empresa is not None:
        padrao, origem = empresa, ORIGEM_EMPRESA
    else:
        padrao, origem = None, None
    if digitado is None:
        return padrao, origem
    return digitado, (origem if digitado == padrao else ORIGEM_ITEM)


def completar_percentual(item: Item, c: Company) -> Item:
    """Nota de percentual ainda sem % (nem digitado nem da nota fixa): usa a
    porcentagem da empresa prestadora, se ela tiver."""
    if item.tipo_valor == "percentual" and item.percentual is None:
        pct, origem = resolver_percentual(None, None, c.percentual_servico)
        if pct is not None:
            item.usar_percentual(item.base_calculo, pct, origem)
    return item


# De onde veio a base da nota de percentual (a prévia devolve em `base_origem`).
BASE_FATURAMENTO = "faturamento"  # vendas do mês da empresa (aba Faturamento)
BASE_DIGITADA = "digitada"  # a pessoa trocou a base
BASE_NOTA_FIXA = "nota_fixa"  # a base sugerida da nota fixa (empresa sem loja/sem venda)

# Mês da base (01/10/2026, Eduardo: "como virou o mês, o faturamento de outubro
# está zerado ainda… precisa ter a opção de eu escolher o mês, por exemplo
# setembro"): a nota continua com a competência dela; só a BASE do % pode vir
# do faturamento de um mês anterior. Até 12 meses para trás, nunca para frente.
MESES_BASE_MAX = 12
MES_BASE_FUTURO = "O mês da base não pode ser depois do mês da nota."
MES_BASE_ANTIGO = f"O mês da base pode ser no máximo {MESES_BASE_MAX} meses antes do mês da nota."


def mes_da_base(competencia: date, base_competencia: date | None) -> date:
    """O mês do faturamento que vira a base da nota de percentual (sempre o
    1º dia). None = o mesmo mês da nota. Mês depois do da nota, ou mais de 12
    meses antes, é recusado (422) em vez de virar base calado."""
    competencia = competencia.replace(day=1)
    if base_competencia is None:
        return competencia
    mes = base_competencia.replace(day=1)
    distancia = (competencia.year - mes.year) * 12 + competencia.month - mes.month
    if distancia < 0:
        raise NfseError(422, "mes_da_base_invalido", MES_BASE_FUTURO)
    if distancia > MESES_BASE_MAX:
        raise NfseError(422, "mes_da_base_invalido", MES_BASE_ANTIGO)
    return mes


def _mes_gravado(v: Any) -> date | None:
    """'2026-09-01' do retrato da nota → date (notas de antes de 01/10: None)."""
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


async def completar_base(session: AsyncSession, item: Item, competencia: date) -> Item:
    """Nota de percentual (Eduardo, 30/09: "faz com base no faturamento já"):
    a base é o faturamento da empresa no mês — todas as lojas com o CNPJ dela,
    mesma régua da aba Faturamento. Sem base na nota: o faturamento (se houver
    venda), senão a base sugerida da nota fixa. Base que veio digitada igual ao
    faturamento continua "do faturamento" (a tela manda o que mostrou).

    01/10/2026: o faturamento é o do mês da base (`item.base_competencia`, ex.:
    setembro numa nota de outubro); sem ele, o do mês da nota, como antes."""
    if item.tipo_valor != "percentual":
        return item
    item.base_competencia = mes_da_base(competencia, item.base_competencia)
    fat = await faturamento_da_empresa(session, item.company_id, item.base_competencia)
    item.faturamento = fat
    tem_venda = fat is not None and fat.valor > 0
    if item.base_calculo is None:
        if tem_venda:
            item.base_origem = BASE_FATURAMENTO
            base = fat.valor
        elif item.base_padrao is not None:
            item.base_origem = BASE_NOTA_FIXA
            base = item.base_padrao
        else:
            return item
        return item.usar_percentual(base, item.percentual, item.percentual_origem)
    if item.base_origem is None:  # reenvio já traz a origem gravada
        mesmo = tem_venda and Decimal(item.base_calculo) == fat.valor
        item.base_origem = BASE_FATURAMENTO if mesmo else BASE_DIGITADA
    return item


def item_do_modelo(
    m: NfseModelo,
    valor: Decimal | None = None,
    *,
    base: Decimal | None = None,
    percentual: Decimal | None = None,
    pct_empresa: Decimal | None = None,
) -> Item:
    """Fixo: `valor` troca o do modelo no mês. Percentual: `base`/`percentual`
    trocam a base sugerida e o % do modelo (o `valor` digitado não vale: é conta);
    sem % no item nem no modelo, vale `pct_empresa` (a % da empresa)."""
    it = Item(
        company_id=m.company_id,
        tomador_id=m.tomador_id,
        descricao=m.descricao,
        valor=None,
        modelo_id=m.id,
        city_service_code=m.city_service_code,
        federal_service_code=m.federal_service_code,
        c_nbs=m.c_nbs,
        inf_comp=m.inf_comp,
    )
    if m.tipo_valor == "percentual":
        pct, origem = resolver_percentual(percentual, m.percentual, pct_empresa)
        it.base_padrao = m.base_padrao
        return it.usar_percentual(base, pct, origem)
    return it.usar_fixo(valor if valor is not None else m.valor)


def repetir_valor(item: Item, e: NfseEmissao) -> Item:
    """Reenvio da recusada: a mesma conta (mesma base, mesmo %) ou o mesmo valor,
    mesmo que a nota fixa — ou a porcentagem da empresa — tenha mudado depois."""
    if e.percentual is not None and e.base_calculo is not None:
        # A origem que a nota recusada gravou (None nas gravadas antes da % da empresa).
        serv = (e.snapshot or {}).get("servico") or {}
        item.base_origem = serv.get("base_origem")  # a base continua a mesma, e a origem dela
        # E o mês dela (01/10/2026): base de setembro numa nota de outubro volta
        # como base de setembro (None nas gravadas antes = o mês da nota).
        item.base_competencia = _mes_gravado(serv.get("base_competencia"))
        return item.usar_percentual(e.base_calculo, e.percentual, serv.get("percentual_origem"))
    return item.usar_fixo(e.valor_servico)


def problemas_do_valor(item: Item) -> list[str]:
    if item.tipo_valor != "percentual":
        return []
    p = []
    if item.base_calculo is None:
        p.append(FALTA_BASE)
    if item.percentual is None:
        p.append(FALTA_PERCENTUAL)
    if not p and (item.valor is None or item.valor < T.CENTAVO):
        p.append(VALOR_PEQUENO)
    return p


def _com_problemas_do_valor(
    item: Item, descricao: str, inf_comp: str | None, probs: list[str]
) -> list[str]:
    """No percentual, "valor tem que ser maior que zero" vira o motivo de verdade.
    No fixo, {percentual}/{base} não tem de onde sair e iria cru pra NFE.io."""
    if item.tipo_valor != "percentual":
        textos = f"{descricao} {inf_comp or ''}"
        if "{percentual}" in textos or "{base}" in textos:
            return [*probs, MARCADOR_NO_FIXO]
        return probs
    extra = problemas_do_valor(item)
    if not extra:
        return probs
    return [*extra, *(x for x in probs if x != T.VALOR_ZERO)]


def checar_conta(item: Item) -> None:
    """Emitir nota de percentual sem base ou sem %: para antes de ir à NFE.io
    (o % da empresa já tem que ter sido aplicado — `completar_percentual`)."""
    if item.tipo_valor != "percentual":
        return
    if item.base_calculo is None:
        raise NfseError(422, "sem_base_calculo", FALTA_BASE)
    if item.percentual is None:
        raise NfseError(422, "sem_percentual", FALTA_PERCENTUAL)


def _txt(v: Decimal | None, casas: Decimal) -> str | None:
    return str(Decimal(v).quantize(casas)) if v is not None else None


def _mes_txt(item: Item) -> str | None:
    """O mês da base ('2026-09-01') da nota de percentual; None na de valor fixo."""
    if item.tipo_valor != "percentual" or item.base_competencia is None:
        return None
    return item.base_competencia.isoformat()


# --- retenção de IR (regra medida nas 137 notas reais, 29/09) --------------------

ALIQUOTA_IR = Decimal("1.50")
DISPENSA_IR = Decimal("10.00")
MOTIVO_IR_RETIDO = "Lucro Presumido: IR de 1,5% retido"
MOTIVO_IR_DISPENSA = "IR menor que R$ 10,00: não retém"
MOTIVO_IR_SIMPLES = "Simples Nacional: não retém"
MOTIVO_IR_SEM_REGIME = "Regime da empresa não informado na NFE.io: não retém"
MOTIVO_IR_OUTRO_REGIME = "Retenção automática só no Lucro Presumido: não retém"
MOTIVO_IR_PF = "Tomador pessoa física: não retém"
MOTIVO_IR_SEM_VALOR = "Sem o valor da nota ainda não dá para calcular o IR"
MOTIVO_IR_SEMPRE = "definido na empresa: sempre"
MOTIVO_IR_NUNCA = "definido na empresa: nunca"


@dataclass(frozen=True)
class Ir:
    retem: bool
    valor: Decimal | None
    motivo: str
    aliquota: Decimal = ALIQUOTA_IR


def valor_ir(valor: Decimal) -> Decimal:
    """1,5% no centavo, meio pra cima (805,75 → 12,09; 669,53 → 10,04)."""
    return (Decimal(valor) * ALIQUOTA_IR / Decimal(100)).quantize(T.CENTAVO, rounding=ROUND_HALF_UP)


def calcular_ir(
    valor: Decimal | None,
    regime: str | None,
    retencao: str | None = "auto",
    *,
    tomador_pj: bool = True,
) -> Ir:
    """Lucro Presumido + tomador PJ: IRRF de 1,5%; até R$ 10,00 não retém
    (dispensa). Simples (ou sem regime): nunca. `retencao` 'sempre'/'nunca' é a
    contabilidade sobrepondo a regra (company_fiscal.retencao_ir)."""
    ir = valor_ir(valor) if valor is not None else None
    if retencao == "nunca":
        return Ir(False, None, MOTIVO_IR_NUNCA)
    if retencao == "sempre":
        return Ir(ir is not None, ir, MOTIVO_IR_SEMPRE)
    if regime != "LucroPresumido":
        if regime == "SimplesNacional":
            return Ir(False, None, MOTIVO_IR_SIMPLES)
        if not regime:
            return Ir(False, None, MOTIVO_IR_SEM_REGIME)
        return Ir(False, None, MOTIVO_IR_OUTRO_REGIME)
    if not tomador_pj:
        return Ir(False, None, MOTIVO_IR_PF)
    if ir is None:
        return Ir(False, None, MOTIVO_IR_SEM_VALOR)
    if ir <= DISPENSA_IR:
        return Ir(False, None, MOTIVO_IR_DISPENSA)
    return Ir(True, ir, MOTIVO_IR_RETIDO)


def valor_liquido(valor: Decimal | None, ir: Ir) -> Decimal | None:
    if valor is None:
        return None
    return Decimal(valor) - (ir.valor if ir.retem and ir.valor is not None else Decimal(0))


MARCA_RETENCOES = "RETENÇÕES CONFORME LEI 10.833/2003"


def bloco_retencoes(ir: Decimal, liquido: Decimal) -> str:
    """Igual às notas reais (inclusive o "0.00%" com ponto no INSS)."""
    return (
        f"\n\n{MARCA_RETENCOES}\n"
        f"   IRRF   1,50%  {T.fmt_reais(ir)}\n"
        "   PIS    0,00%  R$ 0,00\n"
        "   COFINS 0,00%  R$ 0,00\n"
        "   CSLL   0,00%  R$ 0,00\n"
        "   ISS    0,00%  R$ 0,00\n"
        "   INSS   0.00%  R$ 0,00\n"
        f"VALOR LIQUIDO  {T.fmt_reais(liquido)}"
    )


def descricao_final(descricao: str, valor: Decimal | None, ir: Ir) -> str:
    if not ir.retem or ir.valor is None or valor is None or MARCA_RETENCOES in descricao:
        return descricao
    return descricao + bloco_retencoes(ir.valor, valor_liquido(valor, ir) or Decimal(0))


# --- tomador → borrower ---------------------------------------------------------------


@dataclass
class TomadorNota:
    t: NfseTomador
    documento: str
    nome: str
    borrower: dict
    com_endereco: bool


def _cep(v: str | None) -> str | None:
    d = T.so_digitos(v)
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else None


def endereco_tomador(
    *,
    cep: str | None,
    cmun: str | None,
    logradouro: str | None,
    numero: str | None,
    bairro: str | None,
    complemento: str | None = None,
    nome_cidade: str | None = None,
    uf: str | None = None,
) -> dict | None:
    """Endereço pela metade dá 400 na NFE.io: ou vai completo, ou não vai."""
    cep_f = _cep(cep)
    cmun = T.so_digitos(cmun)
    if not (
        cep_f
        and len(cmun) == 7
        and (logradouro or "").strip()
        and (numero or "").strip()
        and (bairro or "").strip()
    ):
        return None
    oficial = municipios.nome_do_codigo(cmun)
    if oficial is not None:
        nome_cidade, uf = oficial
    if not (nome_cidade and uf and len(uf.strip()) == 2):
        return None
    end = {
        "country": "BRA",
        "postalCode": cep_f,
        "street": (logradouro or "").strip(),
        "number": (numero or "").strip(),
        "district": (bairro or "").strip(),
        "city": {"code": cmun, "name": nome_cidade},
        "state": uf.strip().upper(),
    }
    if (complemento or "").strip():
        end["additionalInformation"] = (complemento or "").strip()
    return end


def montar_borrower(
    documento: str,
    nome: str,
    *,
    fone: str | None = None,
    endereco: dict | None = None,
) -> dict:
    """O tomador como a NFE.io quer. O e-mail do tomador NUNCA vai (30/09/2026,
    Eduardo: nada de envio automático): com ele a NFE.io avisa o tomador
    sozinha na emissão e no cancelamento (notificação ligada de fábrica nas 25
    empresas da conta). O e-mail fica só no cadastro, para o envio manual pelo
    DaVinci (`enviar_por_email`)."""
    b: dict[str, Any] = {
        "type": "LegalEntity" if len(documento) == 14 else "NaturalPerson",
        "name": (nome or "").strip(),
        # v3: texto (aceita o CNPJ alfanumérico).
        "federalTaxNumber": documento,
    }
    fone_d = T.so_digitos(fone)
    if 7 <= len(fone_d) <= 19:
        b["phoneNumber"] = fone_d
    if endereco:
        b["address"] = endereco
    return b


async def tomador_nota(session: AsyncSession, tomador_id: UUID) -> TomadorNota:
    t = await session.get(NfseTomador, tomador_id)
    if t is None:
        raise NfseError(404, "tomador_nao_encontrado", "Tomador não encontrado.")
    doc, nome = T.normalizar_documento(t.documento), t.nome or ""
    end = endereco_tomador(
        cep=t.cep,
        cmun=t.cmun_ibge,
        logradouro=t.logradouro,
        numero=t.numero,
        bairro=t.bairro,
        complemento=t.complemento,
    )
    if t.tipo == "grupo" and t.company_id:
        c = await session.get(Company, t.company_id)
        if c is not None:
            doc, nome = T.normalizar_documento(c.cnpj), c.razao_social
        if end is None:
            # Empresa do grupo: o endereço que a NFE.io tem dela.
            ef = E.endereco_nfeio(await session.get(CompanyFiscal, t.company_id))
            if ef:
                cidade = ef.get("city") or {}
                end = endereco_tomador(
                    cep=ef.get("postalCode"),
                    cmun=cidade.get("code"),
                    logradouro=ef.get("street"),
                    numero=ef.get("number"),
                    bairro=ef.get("district"),
                    complemento=ef.get("additionalInformation"),
                    nome_cidade=cidade.get("name"),
                    uf=ef.get("state"),
                )
    return TomadorNota(
        t=t,
        documento=doc,
        nome=nome,
        borrower=montar_borrower(doc, nome, fone=t.fone, endereco=end),
        com_endereco=end is not None,
    )


# --- o JSON que vai à NFE.io ----------------------------------------------------------


def codigos_do_servico(
    item: Item, f: CompanyFiscal | None
) -> tuple[str | None, str | None, str | None]:
    """Do item/nota fixa, senão da empresa: (cityServiceCode, federalServiceCode, NBS)."""

    def _um(*vs: str | None) -> str | None:
        for v in vs:
            if v and v.strip():
                return v.strip()
        return None

    return (
        _um(item.city_service_code, f.city_service_code if f else None),
        _um(item.federal_service_code, f.federal_service_code if f else None),
        _um(item.c_nbs, f.c_nbs if f else None),
    )


def external_id(emissao_id: UUID, tentativa: int) -> str:
    """ "dv-<id sem hífens>-<tentativa>" (≤ 40 caracteres): cada reenvio tem o seu,
    porque a NFE.io recusa externalId repetido."""
    return f"dv-{emissao_id.hex}-{tentativa}"


def montar_payload(
    *,
    borrower: dict,
    valor: Decimal | None,
    competencia: date,
    descricao: str,
    inf_comp: str | None,
    codigos: tuple[str | None, str | None, str | None],
    ir: Ir,
    external: str | None = None,
    hoje: date | None = None,
) -> dict:
    """Puro. Sem rpsNumber (a NFE.io numera) e sem issRate/issTaxAmount (a
    NFE.io calcula o ISS pelo código do serviço). Com IR retido, TODAS as
    retenções vão explícitas (a NFE.io exige: as que não se aplicam com 0)."""
    city, federal, nbs = codigos
    p: dict[str, Any] = {}
    if external:
        p["externalId"] = external
    p["borrower"] = borrower
    p["cityServiceCode"] = city or ""
    if federal:
        p["federalServiceCode"] = federal
    if nbs:
        p["nbsCode"] = nbs
    p["description"] = descricao
    p["servicesAmount"] = float(Decimal(valor or 0).quantize(T.CENTAVO))
    p["accrualOn"] = T.data_competencia(competencia, hoje or date.today()).isoformat()
    if inf_comp:
        p["additionalInformation"] = inf_comp
    if ir.retem and ir.valor is not None:
        p["irAmountWithheld"] = float(ir.valor)
        for campo in (
            "pisAmountWithheld",
            "cofinsAmountWithheld",
            "csllAmountWithheld",
            "inssAmountWithheld",
            "issAmountWithheld",
            "othersAmountWithheld",
        ):
            p[campo] = 0.0
    return p


def problemas_payload(
    *,
    documento: str,
    nome: str,
    cnpj_prestador: str | None,
    valor: Decimal | None,
    descricao: str,
    inf_comp: str | None,
    codigos: tuple[str | None, str | None, str | None],
) -> list[str]:
    """O que a NFE.io recusaria com 400 e dá pra saber antes (vazio = pode ir).
    O e-mail do tomador saiu daqui em 30/09 (não vai mais à NFE.io): um e-mail
    mal digitado no cadastro não trava mais a emissão."""
    p: list[str] = []
    if not T.documento_valido(documento):
        p.append("O CNPJ/CPF do tomador não é válido.")
    elif documento == T.normalizar_documento(cnpj_prestador):
        p.append("O tomador não pode ser a própria empresa prestadora.")
    if not (nome or "").strip():
        p.append("Falta o nome do tomador.")
    elif len(nome.strip()) > 115:
        p.append("O nome do tomador passa de 115 caracteres (limite da NFE.io).")
    if valor is None or Decimal(valor) <= 0:
        p.append(T.VALOR_ZERO)
    if not (descricao or "").strip():
        p.append("Falta a descrição do serviço.")
    elif len(descricao) > 2000:
        p.append("A descrição do serviço passa de 2000 caracteres.")
    if inf_comp and len(inf_comp) > 500:
        p.append("As informações complementares passam de 500 caracteres (limite da NFE.io).")
    city, _federal, nbs = codigos
    if not city:
        p.append(E.PEND_SEM_CODIGO)
    if nbs and not (nbs.isdigit() and len(nbs) == 9):
        p.append("O código NBS tem 9 dígitos (ex.: 102010000).")
    return p


# --- banco -------------------------------------------------------------------------------


async def prestador(
    session: AsyncSession, company_id: UUID
) -> tuple[Company, CompanyFiscal | None]:
    c = await session.get(Company, company_id)
    if c is None:
        raise NfseError(404, "empresa_nao_encontrada", "Empresa não encontrada.")
    return c, await session.get(CompanyFiscal, company_id)


async def emissoes_do_modelo(
    session: AsyncSession, modelo_id: UUID, competencia: date
) -> list[NfseEmissao]:
    """Todas as notas do modelo no mês, de qualquer ambiente (quem decide o que
    trava é `trava_o_mes`)."""
    return list(
        (
            await session.execute(
                select(NfseEmissao)
                .where(
                    NfseEmissao.modelo_id == modelo_id,
                    NfseEmissao.competencia == competencia,
                )
                .order_by(NfseEmissao.created_at.desc())
            )
        ).scalars()
    )


def mesmo_lado(a: str | None, b: str | None) -> bool:
    """Teste com teste, real com real (ambiente vazio/desconhecido conta como real)."""
    return eh_teste(a) == eh_teste(b)


def trava_o_mes(e: NfseEmissao, ambiente: str | None) -> bool:
    """Nota viva do modelo no mês trava uma nova. Revisão de 30/09: nota REAL
    (ou de ambiente desconhecido) trava sempre — mesmo que a empresa tenha
    mudado de ambiente na NFE.io depois; nota de TESTE só trava outra de teste
    (o teste de setembro não impede a nota de verdade de setembro)."""
    return e.status in STATUS_VIVOS and (
        not eh_teste(e.nfeio_ambiente) or mesmo_lado(e.nfeio_ambiente, ambiente)
    )


def _log(
    session: AsyncSession,
    r: nfeio.Resposta | None,
    company_id: UUID | None,
    emissao_id: UUID | None,
    ambiente: str | None,
) -> None:
    """Uma linha por ida à NFE.io (01/10/2026: o corpo foi para
    `chamadas.registrar`, que a integração de empresas também usa)."""
    chamadas.registrar(session, r, company_id, emissao_id, ambiente)


@asynccontextmanager
async def _cliente(cli: nfeio.ClienteNfeio | None) -> AsyncIterator[nfeio.ClienteNfeio]:
    if cli is not None:
        yield cli
        return
    async with nfeio.ClienteNfeio() as novo:
        yield novo


async def _cid(session: AsyncSession, e: NfseEmissao) -> str | None:
    """Id da empresa na NFE.io gravado na hora de emitir (senão, o atual)."""
    cid = ((e.snapshot or {}).get("prestador") or {}).get("nfeio_company_id")
    if cid:
        return cid
    f = await session.get(CompanyFiscal, e.company_id)
    return f.nfeio_company_id if f is not None else None


# --- notas do mês na NFE.io (aviso de duplicada) --------------------------------------

_CACHE_NOTAS: dict[tuple[str, str], tuple[float, list[dict]]] = {}


def limpar_cache(cid: str | None = None) -> None:
    if cid is None:
        _CACHE_NOTAS.clear()
        return
    for k in [k for k in _CACHE_NOTAS if k[0] == cid]:
        _CACHE_NOTAS.pop(k, None)


def _fim_do_mes(competencia: date) -> date:
    prox = (competencia.replace(day=28) + timedelta(days=4)).replace(day=1)
    return prox - timedelta(days=1)


async def notas_do_mes(cli: nfeio.ClienteNfeio, cid: str, competencia: date) -> list[dict]:
    """Notas da empresa na competência (com cache curto: o lote pede a mesma
    empresa várias vezes). Só o que o aviso precisa."""
    chave = (cid, competencia.isoformat())
    agora = time.monotonic()
    hit = _CACHE_NOTAS.get(chave)
    if hit is not None and agora - hit[0] < CACHE_NOTAS_SEGUNDOS:
        return hit[1]
    r, notas = await cli.listar_notas(
        cid, de=competencia.isoformat(), ate=_fim_do_mes(competencia).isoformat()
    )
    if not r.ok:
        raise NfseError(502, "nfeio_sem_resposta", E.MSG_SEM_RESPOSTA)
    resumo = [
        {
            "id": n.get("id"),
            "externalId": n.get("externalId"),
            "numero": n.get("number") or None,
            "valor": n.get("servicesAmount"),
            "emitida_em": n.get("issuedOn") or n.get("createdOn"),
            "status": n.get("status"),
            "documento": T.normalizar_documento((n.get("borrower") or {}).get("federalTaxNumber")),
        }
        for n in notas
    ]
    _CACHE_NOTAS[chave] = (agora, resumo)
    return resumo


def duplicadas(notas: list[dict], documento: str, ignorar: set[str]) -> list[dict]:
    """Notas vivas (nem recusada nem cancelada) do mesmo tomador."""
    out = []
    for n in notas:
        if not documento or n["documento"] != documento:
            continue
        if n["id"] in ignorar or (n.get("externalId") or "") in ignorar:
            continue
        if n["status"] in ("Error", "Cancelled"):
            continue
        valor = n["valor"]
        out.append(
            {
                "numero": str(n["numero"]) if n["numero"] else None,
                "valor": str(Decimal(str(valor)).quantize(T.CENTAVO))
                if valor is not None
                else None,
                "emitida_em": n["emitida_em"],
                "status": n["status"],
            }
        )
    return out


# --- prévia ---------------------------------------------------------------------------------


@dataclass
class Montagem:
    """Tudo que a prévia mostra e a emissão manda, calculado num lugar só."""

    tomador: TomadorNota
    codigos: tuple[str | None, str | None, str | None]
    ir: Ir
    descricao_base: str
    descricao: str
    inf_comp: str | None
    problemas: list[str]
    avisos: list[str]


def montar(
    c: Company,
    f: CompanyFiscal | None,
    tn: TomadorNota,
    item: Item,
    competencia: date,
    *,
    hoje: date | None = None,
) -> Montagem:
    codigos = codigos_do_servico(item, f)
    ir = calcular_ir(
        item.valor,
        f.nfeio_regime if f else None,
        f.retencao_ir if f else "auto",
        tomador_pj=len(tn.documento) == 14,
    )
    base_desc = T.resolver_descricao(
        item.descricao, competencia, percentual=item.percentual, base=item.base_calculo
    )
    descricao = descricao_final(base_desc, item.valor, ir)
    inf_comp = (
        T.resolver_descricao(
            item.inf_comp, competencia, percentual=item.percentual, base=item.base_calculo
        )
        if item.inf_comp
        else None
    )
    probs = problemas_payload(
        documento=tn.documento,
        nome=tn.nome,
        cnpj_prestador=c.cnpj,
        valor=item.valor,
        descricao=descricao,
        inf_comp=inf_comp,
        codigos=codigos,
    )
    probs = _com_problemas_do_valor(item, base_desc, inf_comp, probs)
    pend, avisos = E.pendencias_e_avisos(c, f, hoje=hoje)
    # Código do serviço: vale o efetivo (nota fixa/avulsa sobrepõe a empresa).
    pend = [p for p in pend if p not in (E.PEND_SEM_CODIGO, E.PEND_BARUERI)]
    if (
        codigos[0] == E.CITY_SERVICE_CODE_PADRAO
        and f is not None
        and f.nfeio_company_id
        and E.codigo_municipio(f) == E.BARUERI
    ):
        pend.append(E.PEND_BARUERI)
    problemas = list(dict.fromkeys([*pend, *probs]))
    ambiente = f.nfeio_ambiente if f else None
    if f is not None and f.nfeio_company_id and not pode_emitir(ambiente):
        problemas.append(MSG_PRODUCAO_BLOQUEADA)
    avisos = list(avisos)
    if f is not None and f.nfeio_company_id and eh_teste(ambiente):
        avisos.insert(0, AVISO_TESTE)
    if not tn.com_endereco:
        avisos.append(AVISO_SEM_ENDERECO)
    return Montagem(tn, codigos, ir, base_desc, descricao, inf_comp, problemas, avisos)


async def previa(
    session: AsyncSession,
    item: Item,
    competencia: date,
    *,
    cli: nfeio.ClienteNfeio | None = None,
    hoje: date | None = None,
) -> dict:
    """Monta sem gravar nada e sem POST. Com `cli`, lista as notas do mês da
    empresa na NFE.io (GET) pra avisar nota já emitida pelo painel."""
    competencia = competencia.replace(day=1)
    c, f = await prestador(session, item.company_id)
    completar_percentual(item, c)
    await completar_base(session, item, competencia)
    tn = await tomador_nota(session, item.tomador_id)
    m = montar(c, f, tn, item, competencia, hoje=hoje)
    ambiente = f.nfeio_ambiente if f else None
    if cli is None and f is not None and f.nfeio_company_id and not nfeio.chave_configurada():
        m.problemas.append(nfeio.CHAVE_AUSENTE)

    ja = None
    ignorar: set[str] = set()
    if item.modelo_id:
        for e in await emissoes_do_modelo(session, item.modelo_id, competencia):
            ignorar |= {x for x in (e.nfeio_id, e.nfeio_external_id) if x}
            if ja is None and trava_o_mes(e, ambiente):
                ja = {"id": str(e.id), "status": e.status, "n_nfse": e.n_nfse}
    dups: list[dict] = []
    if cli is not None and f is not None and f.nfeio_company_id:
        try:
            dups = duplicadas(
                await notas_do_mes(cli, f.nfeio_company_id, competencia), tn.documento, ignorar
            )
        except NfseError:
            m.avisos.append("Não deu para conferir agora as notas do mês na NFE.io.")
    for d in dups:
        valor = T.fmt_reais(Decimal(d["valor"])) if d["valor"] else "valor não informado"
        m.avisos.append(
            "Já existe nota na NFE.io para este tomador neste mês "
            f"(nº {d['numero'] or 'sem número'}, {valor})"
        )
    payload = (
        montar_payload(
            borrower=tn.borrower,
            valor=item.valor,
            competencia=competencia,
            descricao=m.descricao,
            inf_comp=m.inf_comp,
            codigos=m.codigos,
            ir=m.ir,
            hoje=hoje,
        )
        if not m.problemas
        else None
    )
    liquido = valor_liquido(item.valor, m.ir)
    return {
        "modelo_id": str(item.modelo_id) if item.modelo_id else None,
        "company_id": str(c.id),
        "prestador": {"id": str(c.id), "nome": c.apelido or c.razao_social, "cnpj": c.cnpj},
        "tomador": {
            "id": str(tn.t.id),
            "nome": tn.nome,
            "documento": tn.documento,
            "tipo": tn.t.tipo,
        },
        "descricao": m.descricao,
        # Já calculado no percentual; None = falta a base.
        "valor": _txt(item.valor, T.CENTAVO),
        "tipo_valor": item.tipo_valor,
        "percentual": _txt(item.percentual, T.QUATRO_CASAS),
        # 'item' | 'nota_fixa' | 'empresa' | None (sem % ou nota de valor fixo).
        "percentual_origem": item.percentual_origem,
        # 30/09: 'faturamento' | 'digitada' | 'nota_fixa' (None = nota de valor fixo)
        "base_origem": item.base_origem,
        "faturamento": item.faturamento.resumo() if item.faturamento else None,
        # 01/10/2026: de que mês é o `faturamento` acima ('AAAA-MM-01'; None = fixo).
        "base_competencia": _mes_txt(item),
        "base_calculo": _txt(item.base_calculo, T.CENTAVO),
        "city_service_code": m.codigos[0],
        "federal_service_code": m.codigos[1],
        "c_nbs": m.codigos[2],
        "ambiente": ambiente,
        "teste": eh_teste(ambiente),
        "ir": {
            "retem": m.ir.retem,
            "aliquota": _txt(m.ir.aliquota, T.CENTAVO),
            "valor": _txt(m.ir.valor, T.CENTAVO) if m.ir.retem else None,
            "motivo": m.ir.motivo,
        },
        "valor_liquido": _txt(liquido, T.CENTAVO),
        "problemas": m.problemas,
        "avisos": m.avisos,
        "ja_emitida": ja,
        "duplicadas_nfeio": dups,
        "payload": payload,
    }


# --- o que volta da NFE.io --------------------------------------------------------------


def _dec2(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v)).quantize(T.CENTAVO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _quando(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _sem_alerta(alertas: list | None, descricao: str) -> list:
    return [
        a
        for a in (alertas or [])
        if not (isinstance(a, Mapping) and a.get("descricao") == descricao)
    ]


def _com_alerta(alertas: list | None, alerta: dict) -> list:
    return [*_sem_alerta(alertas, alerta["descricao"]), alerta]


def _dados_da_nota(e: NfseEmissao, nota: Mapping[str, Any]) -> None:
    num = nota.get("number")
    if num not in (None, 0, "0", ""):
        e.n_nfse = str(num)
    e.check_code = nota.get("checkCode") or e.check_code
    e.dh_proc = _quando(nota.get("issuedOn")) or e.dh_proc
    e.v_bc = _dec2(nota.get("baseTaxAmount")) or e.v_bc
    aliq = _dec2(Decimal(str(nota["issRate"])) * 100) if nota.get("issRate") is not None else None
    e.p_aliq_aplic = aliq if aliq is not None else e.p_aliq_aplic
    e.v_issqn = (
        _dec2(nota.get("issTaxAmount")) if nota.get("issTaxAmount") is not None else e.v_issqn
    )
    e.v_liq = _dec2(nota.get("amountNet")) or e.v_liq
    ir = _dec2(nota.get("irAmountWithheld"))
    if ir:
        e.ir_retido = ir
    e.rps_serie = nota.get("rpsSerialNumber") or e.rps_serie
    if nota.get("rpsNumber"):
        try:
            e.rps_numero = int(nota["rpsNumber"])
        except (TypeError, ValueError):
            pass


def aplicar_nota(
    e: NfseEmissao,
    nota: Mapping[str, Any],
    *,
    agora: datetime | None = None,
    cancelamento_pedido_em: datetime | None = None,
) -> None:
    """Traduz o estado da nota na NFE.io (flowStatus + status) para o DaVinci."""
    agora = agora or datetime.now(UTC)
    flow = nota.get("flowStatus")
    st = nota.get("status")
    msg = str(nota.get("flowMessage") or "").strip() or None
    if nota.get("id"):
        e.nfeio_id = str(nota["id"])
    if nota.get("environment"):
        e.nfeio_ambiente = str(nota["environment"])
    e.flow_status = flow
    e.flow_message = msg[:2000] if msg else None

    if e.status == "cancelando" and flow == "Issued" and st != "Cancelled":
        _dados_da_nota(e, nota)
        pedido = cancelamento_pedido_em
        if pedido is not None and agora - pedido < ESPERA_CANCELAMENTO:
            return  # o pedido ainda não chegou na NFE.io: espera
        e.status = "emitida"
        e.alertas = _com_alerta(
            e.alertas,
            {
                "codigo": "",
                "descricao": "O cancelamento não foi registrado na NFE.io.",
                "o_que_fazer": "A nota continua válida. Tente cancelar de novo.",
            },
        )
        return
    if flow == "Issued":
        _emitida(e, nota)
        return
    if flow == "IssueFailed":
        if st == "Issued":
            # "max retry reached on download stage": a nota EXISTE, só o PDF
            # falhou. Reemitir duplicaria a nota.
            _emitida(e, nota)
            e.alertas = _com_alerta(e.alertas, ALERTA_PDF)
            return
        _rejeitada(e, msg)
        return
    if flow == "Cancelled" or st == "Cancelled":
        _dados_da_nota(e, nota)
        e.status = "cancelada"
        e.cancelada_em = _quando(nota.get("cancelledOn")) or e.cancelada_em or agora
        return
    if flow == "CancelFailed":
        _dados_da_nota(e, nota)
        e.status = "emitida"
        e.alertas = _com_alerta(
            e.alertas,
            {
                "codigo": "",
                "descricao": f"O cancelamento foi recusado: {msg or 'sem motivo informado'}",
                "o_que_fazer": "A nota continua válida.",
            },
        )
        return
    if flow == "WaitingSendCancel":
        e.status = "cancelando"
        return
    if flow not in FLOW_EM_ANDAMENTO:
        # Valor que a NFE.io ainda não documentou: o `status` decide.
        if st == "Issued":
            _emitida(e, nota)
            return
        if st == "Error":
            _rejeitada(e, msg)
            return
    # Waiting*, PullFromCityHall: a NFE.io ainda está trabalhando.
    if e.status != "cancelando":
        e.status = "processando"


def _emitida(e: NfseEmissao, nota: Mapping[str, Any]) -> None:
    _dados_da_nota(e, nota)
    e.status = "emitida"
    e.erros = None
    e.alertas = _sem_alerta(e.alertas, ALERTA_PDF["descricao"]) or None


def _rejeitada(e: NfseEmissao, msg: str | None) -> None:
    e.status = "rejeitada"
    e.erros = explicar_texto(msg) or [
        {
            "codigo": "",
            "descricao": "A prefeitura recusou a nota sem dizer o motivo.",
            "o_que_fazer": "Conferir o cadastro da empresa na NFE.io e reenviar.",
        }
    ]


def _id_do_location(location: str | None) -> str | None:
    if not location:
        return None
    ultimo = location.rstrip("/").rsplit("/", 1)[-1]
    return ultimo or None


def chave_do_xml(xml: bytes) -> str | None:
    """Chave de acesso de 50 dígitos (só NFS-e do Emissor Nacional): no Id do
    infNFSe ("NFS" + chave). Prefeitura com padrão próprio não tem."""
    try:
        raiz = ET.fromstring(xml)  # noqa: S314 — XML da NFE.io, só leitura de um atributo
    except ET.ParseError:
        return None
    for el in raiz.iter():
        nome = el.tag.rsplit("}", 1)[-1]
        if nome == "infNFSe":
            ident = el.get("Id") or ""
            chave = ident[3:] if ident.startswith("NFS") else ""
            if len(chave) == 50 and chave.isdigit():
                return chave
    return None


async def _guardar_xml(
    session: AsyncSession, cli: nfeio.ClienteNfeio, e: NfseEmissao, cid: str
) -> None:
    arq = await cli.xml(cid, e.nfeio_id or "")
    _log(session, arq.resposta, e.company_id, e.id, e.nfeio_ambiente)
    if arq.conteudo:
        e.nfse_xml_b64 = base64.b64encode(arq.conteudo).decode("ascii")
        chave = chave_do_xml(arq.conteudo)
        if chave and not e.chave_acesso:
            dono = (
                await session.execute(
                    select(NfseEmissao.id).where(NfseEmissao.chave_acesso == chave)
                )
            ).scalar_one_or_none()
            if dono is None:
                e.chave_acesso = chave


async def buscar(
    cli: nfeio.ClienteNfeio, e: NfseEmissao, cid: str
) -> tuple[nfeio.Resposta | None, dict | None]:
    """GET pelo id da NFE.io (ou pelo externalId, se o POST não devolveu id)."""
    if e.nfeio_id:
        r = await cli.nota(cid, e.nfeio_id)
        if r.ok and isinstance(r.corpo, Mapping) and r.corpo.get("id"):
            return r, dict(r.corpo)
        if r.status != 404 or not e.nfeio_external_id:
            return r, None
    if e.nfeio_external_id:
        return await cli.nota_por_external(cid, e.nfeio_external_id)
    return None, None


def _nao_achou(r: nfeio.Resposta | None, nota: dict | None) -> bool:
    return nota is None and r is not None and (r.ok or r.status == 404)


async def _ultimo_evento(session: AsyncSession, e: NfseEmissao) -> NfseEvento | None:
    return (
        await session.execute(
            select(NfseEvento)
            .where(NfseEvento.emissao_id == e.id)
            .order_by(NfseEvento.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def aplicar_busca(
    session: AsyncSession,
    cli: nfeio.ClienteNfeio,
    e: NfseEmissao,
    cid: str,
    r: nfeio.Resposta | None,
    nota: dict | None,
    *,
    agora: datetime | None = None,
) -> None:
    agora = agora or datetime.now(UTC)
    if nota is None:
        if not _nao_achou(r, nota):
            return  # a consulta falhou: não muda nada, tenta de novo depois
        if (
            e.status in ("enviando", "incerta")
            and e.enviado_em is not None
            and agora - e.enviado_em >= ESPERA_INCERTA
        ):
            e.status = "rejeitada"
            e.erros = [
                {
                    "codigo": "",
                    "descricao": "A NFE.io não recebeu esta nota: nada foi emitido.",
                    "o_que_fazer": "Pode corrigir e reenviar.",
                }
            ]
        return
    ev = (
        await _ultimo_evento(session, e)
        if e.status == "cancelando"
        or nota.get("flowStatus") in ("Cancelled", "CancelFailed", "WaitingSendCancel")
        else None
    )
    antes = e.status
    aplicar_nota(
        e, nota, agora=agora, cancelamento_pedido_em=ev.created_at if ev is not None else None
    )
    if ev is not None:
        if e.status == "cancelada" and ev.status != "registrado":
            ev.status = "registrado"
        elif (
            antes == "cancelando"
            and e.status == "emitida"
            and ev.status
            in (
                "enviando",
                "incerto",
            )
        ):
            ev.status = "rejeitado"
            ev.erros = explicar_texto(e.flow_message) or [
                {"codigo": "", "descricao": "A NFE.io não registrou o cancelamento."}
            ]
    if e.status in ("emitida", "cancelada") and not e.nfse_xml_b64 and e.nfeio_id:
        try:
            await _guardar_xml(session, cli, e, cid)
        except Exception as ex:  # noqa: BLE001 — o XML fica pro download sob demanda
            logger.warning("nfse_xml_nao_baixado", emissao=str(e.id), erro=type(ex).__name__)


async def atualizar(
    session: AsyncSession, e: NfseEmissao, *, cli: nfeio.ClienteNfeio | None = None
) -> NfseEmissao:
    """Pergunta à NFE.io como está a nota (GET) e traduz. Nunca faz POST."""
    await atualizar_varias(session, [e], cli=cli)
    return e


async def atualizar_varias(
    session: AsyncSession,
    emissoes: list[NfseEmissao],
    *,
    cli: nfeio.ClienteNfeio | None = None,
) -> dict:
    alvo = []
    for e in emissoes:
        if e.provedor != "nfeio" or not (e.nfeio_id or e.nfeio_external_id):
            continue
        cid = await _cid(session, e)
        if cid:
            alvo.append((e, cid))
    if not alvo:
        return {"conferidas": 0}
    agora = datetime.now(UTC)
    async with _cliente(cli) as c:
        sem = asyncio.Semaphore(PARALELO)

        async def um(e: NfseEmissao, cid: str):
            async with sem:
                r, nota = await buscar(c, e, cid)
                return e, cid, r, nota

        carregado = {e.id: (e.status, e.nfeio_external_id) for e, _cid_ in alvo}
        resultados = await asyncio.gather(*(um(e, cid) for e, cid in alvo))
        mudou = 0
        for e, cid, r, nota in resultados:
            # Revisão de 30/09: tela (4 s), worker (2 min) e webhook conferem ao
            # mesmo tempo. Relê a linha travada e só aplica se ninguém mexeu nela
            # desde a leitura; grava uma por vez (estado velho não cobre novo).
            await session.refresh(e, with_for_update=True)
            if (e.status, e.nfeio_external_id) != carregado[e.id]:
                await session.commit()
                continue
            antes = e.status
            _log(session, r, e.company_id, e.id, e.nfeio_ambiente)
            await aplicar_busca(session, c, e, cid, r, nota, agora=agora)
            mudou += e.status != antes
            await session.commit()
    return {"conferidas": len(alvo), "mudaram": mudou}


async def conferir_pendentes(session: AsyncSession) -> dict:
    """Rotina do worker (2 em 2 min): notas ainda em andamento dos últimos 15 dias."""
    if not nfeio.chave_configurada():
        return {"conferidas": 0, "sem_chave": True}
    limite = datetime.now(UTC) - JANELA_CONFERENCIA
    rows = list(
        (
            await session.execute(
                select(NfseEmissao)
                .where(
                    NfseEmissao.status.in_(STATUS_EM_ANDAMENTO),
                    NfseEmissao.provedor == "nfeio",
                    NfseEmissao.created_at >= limite,
                )
                .order_by(NfseEmissao.created_at)
                .limit(200)
            )
        ).scalars()
    )
    return await atualizar_varias(session, rows)


# --- emissão --------------------------------------------------------------------------------


MSG_AMBIENTE_INCERTO = (
    "Não deu para confirmar na NFE.io se a empresa está em Teste ou em Produção agora; "
    "nada foi enviado. Tente de novo em instantes."
)


async def ambiente_na_hora(cliente: nfeio.ClienteNfeio, cid: str) -> str:
    """Ambiente da empresa lido AGORA na NFE.io (empresa + Inscrição Municipal).
    Se qualquer um dos dois não for de teste, vale o que não é (nota real).
    Leitura que falha = NfseError 502: sem confirmação, não emite."""
    r = await cliente.empresa(cid)
    emp = r.corpo.get("companies") if r.ok and isinstance(r.corpo, Mapping) else None
    if isinstance(emp, list):
        emp = emp[0] if emp else None
    ri, inscricoes = await cliente.inscricoes_municipais(cid)
    if not isinstance(emp, Mapping) or not ri.ok:
        if r.status in (401, 403) or ri.status in (401, 403):
            raise NfseError(503, "chave_nfeio", nfeio.CHAVE_RECUSADA)
        raise NfseError(502, "nfeio_sem_resposta", MSG_AMBIENTE_INCERTO)
    # Todas as Inscrições Municipais + a empresa: basta UMA em Produção.
    candidatos = [emp.get("environment")] + [i.get("environment") for i in inscricoes]
    ambientes = [str(a) for a in candidatos if a]
    if not ambientes:
        raise NfseError(502, "nfeio_sem_resposta", MSG_AMBIENTE_INCERTO)
    return next((a for a in ambientes if not eh_teste(a)), ambientes[0])


def _aplicar_post(e: NfseEmissao, r: nfeio.Resposta) -> str | None:
    """Resposta do POST. Devolve 'adotar' quando é preciso buscar pelo externalId."""
    if r.status in (200, 201, 202):
        corpo = r.corpo if isinstance(r.corpo, Mapping) else {}
        e.nfeio_id = str(corpo.get("id") or "") or _id_do_location(r.location)
        if corpo.get("environment"):
            e.nfeio_ambiente = str(corpo["environment"])
        e.flow_status = corpo.get("flowStatus")
        e.status = "processando"
        e.erros = None
        if corpo.get("flowStatus") and corpo.get("flowStatus") not in FLOW_EM_ANDAMENTO:
            aplicar_nota(e, corpo)  # 201 com a nota já pronta
        return None
    texto = nfeio.texto_do_erro(r).lower()
    # externalId repetido: a NFE.io responde 400 "already exists" (medido
    # pelo SDK) ou 409 (contrato novo). Nos dois, a nota pode existir: adota.
    if (r.status == 400 and "already exists" in texto) or r.status == 409:
        return "adotar"
    if r.status in (401, 403):
        e.status = "rejeitada"
        e.erros = [
            {
                "codigo": f"HTTP {r.status}",
                "descricao": "A NFE.io recusou a chave de acesso do servidor. Nada foi emitido.",
                "o_que_fazer": "Conferir a NFEIO_API_KEY no servidor e reenviar.",
            }
        ]
        return None
    if r.incerta:
        e.status = "incerta"
        causa = r.erro_rede or f"HTTP {r.status}"
        e.erros = [
            {
                "codigo": str(r.status or ""),
                "descricao": f"A NFE.io não respondeu com certeza ({causa}).",
                "o_que_fazer": "NÃO reenvie: a conferência automática procura a nota pelo "
                "código do envio em alguns minutos.",
            }
        ]
        return None
    e.status = "rejeitada"
    e.erros = explicar(r.msgs) or [
        {
            "codigo": f"HTTP {r.status}",
            "descricao": f"A NFE.io recusou o envio (HTTP {r.status}).",
            "o_que_fazer": "Corrigir e reenviar.",
        }
    ]
    return None


def _falhou(nota: Mapping[str, Any]) -> bool:
    return nota.get("status") == "Error" or (
        nota.get("flowStatus") == "IssueFailed" and nota.get("status") != "Issued"
    )


async def emitir(
    session: AsyncSession,
    item: Item,
    competencia: date,
    user_id: UUID,
    *,
    reenviar: NfseEmissao | None = None,
    cli: nfeio.ClienteNfeio | None = None,
) -> NfseEmissao:
    competencia = competencia.replace(day=1)
    c, f = await prestador(session, item.company_id)
    completar_percentual(item, c)
    await completar_base(session, item, competencia)
    checar_conta(item)
    if f is None or not f.nfeio_company_id:
        raise NfseError(422, "nao_ligada", E.MSG_NAO_LIGADA)
    # Pré-checagem pelo que está gravado (Produção fora da produção liberada
    # nem chega a consultar a NFE.io). A que vale é a do passo 0.
    if not pode_emitir(f.nfeio_ambiente or ""):
        raise NfseError(409, "producao_bloqueada", MSG_PRODUCAO_BLOQUEADA)
    cid = f.nfeio_company_id
    tn = await tomador_nota(session, item.tomador_id)
    m = montar(c, f, tn, item, competencia)
    if m.problemas:
        await session.rollback()
        raise NfseError(422, "dados_invalidos", " ".join(m.problemas), problemas=m.problemas)

    async with _cliente(cli) as cliente:
        # 0) Teste ou Produção AGORA (revisão de 30/09): o gravado é da última
        #    sincronização; se a contabilidade trocou a empresa para Produção no
        #    painel, a nota seria real. Sem confirmação, nada sai.
        ambiente = await ambiente_na_hora(cliente, cid)
        if ambiente != f.nfeio_ambiente:
            f.nfeio_ambiente = ambiente
        if not pode_emitir(ambiente):
            await session.commit()  # a tela passa a mostrar o ambiente certo
            raise NfseError(409, "producao_bloqueada", MSG_PRODUCAO_BLOQUEADA)

        linha = reenviar
        if linha is None and item.modelo_id:
            for e in await emissoes_do_modelo(session, item.modelo_id, competencia):
                if trava_o_mes(e, ambiente):
                    raise NfseError(
                        409,
                        "ja_emitida",
                        "Esse modelo já tem nota neste mês.",
                        emissao_id=str(e.id),
                        status_atual=e.status,
                    )
                if (
                    e.status == "rejeitada"
                    and linha is None
                    and mesmo_lado(e.nfeio_ambiente, ambiente)
                ):
                    linha = e  # reaproveita a linha: o histórico fica numa nota só
        if linha is not None:
            # Trava a linha até o commit do 'enviando': dois cliques (ou duas
            # abas) no mesmo reenvio não viram dois POSTs — o segundo espera e
            # já encontra a linha 'enviando'.
            await session.refresh(linha, with_for_update=True)
            if linha.status != "rejeitada":
                if reenviar is None:  # outro clique pegou a mesma recusada antes
                    raise NfseError(409, "ja_emitida", "Esse modelo já tem nota neste mês.")
                raise NfseError(409, "nao_reenviavel", "Só nota rejeitada pode ser reenviada.")
            # O reenvio segue as mesmas regras de uma nota nova (revisão de 30/09):
            # nota recusada em TESTE não vira nota REAL (nem o contrário) e não
            # passa por cima de nota viva do modelo no mês.
            if not mesmo_lado(linha.nfeio_ambiente, ambiente):
                raise NfseError(
                    409,
                    "ambiente_mudou",
                    "A empresa mudou de Teste para Produção (ou o contrário) na NFE.io desde "
                    "esta nota. Emita uma nota nova.",
                )
            if linha.modelo_id:
                for e in await emissoes_do_modelo(session, linha.modelo_id, linha.competencia):
                    if e.id != linha.id and trava_o_mes(e, ambiente):
                        raise NfseError(
                            409,
                            "ja_emitida",
                            "Esse modelo já tem nota neste mês.",
                            emissao_id=str(e.id),
                            status_atual=e.status,
                        )

        # 1) Já foi à NFE.io antes? Se aquele envio virou nota, adota — nunca 2 notas.
        if linha is not None and linha.nfeio_external_id:
            r0, anterior = await buscar(cliente, linha, cid)
            _log(session, r0, c.id, linha.id, ambiente)
            if anterior is None and not _nao_achou(r0, anterior):
                await session.commit()
                raise NfseError(
                    502,
                    "nfeio_sem_resposta",
                    "Não deu pra conferir na NFE.io se o envio anterior virou nota; nada foi "
                    "reenviado. Tente de novo em instantes.",
                )
            if anterior is not None and not _falhou(anterior):
                aplicar_nota(linha, anterior)
                await session.commit()
                return linha

        # 2) Grava 'enviando' com o JSON exato e dá commit ANTES do POST.
        if linha is None:
            linha = NfseEmissao(id=uuid4(), company_id=c.id, created_by=user_id)
            session.add(linha)
        tentativa = (linha.tentativas or 0) + 1
        ext = external_id(linha.id, tentativa)
        payload = montar_payload(
            borrower=tn.borrower,
            valor=item.valor,
            competencia=competencia,
            descricao=m.descricao,
            inf_comp=m.inf_comp,
            codigos=m.codigos,
            ir=m.ir,
            external=ext,
        )
        pct = item.tipo_valor == "percentual"
        linha.snapshot = {
            "prestador": {
                "cnpj": c.cnpj,
                "nome": c.razao_social,
                "nfeio_company_id": cid,
                "regime": f.nfeio_regime,
                "im": f.nfeio_im,
                "municipio": f.nfeio_municipio,
            },
            "tomador": {"documento": tn.documento, "nome": tn.nome, "tipo": tn.t.tipo},
            "servico": {
                "city_service_code": m.codigos[0],
                "federal_service_code": m.codigos[1],
                "c_nbs": m.codigos[2],
                # Avulsa não tem modelo: o reenvio refaz a nota a partir daqui.
                "inf_comp": m.inf_comp,
                # Sem o bloco de retenções (o reenvio recalcula o IR).
                "descricao_base": m.descricao_base,
                # Nota de percentual: a conta que deu o valor.
                "tipo_valor": item.tipo_valor,
                "base_calculo": _txt(item.base_calculo, T.CENTAVO),
                "percentual": _txt(item.percentual, T.QUATRO_CASAS),
                "percentual_origem": item.percentual_origem,
                "base_origem": item.base_origem,
                "faturamento": item.faturamento.resumo() if item.faturamento else None,
                # 01/10/2026: o mês do faturamento da base (o reenvio usa o mesmo).
                "base_competencia": _mes_txt(item),
            },
            "ir": {
                "retem": m.ir.retem,
                "valor": _txt(m.ir.valor, T.CENTAVO) if m.ir.retem else None,
                "motivo": m.ir.motivo,
                "retencao_ir": f.retencao_ir,
            },
            "accrual_on": payload["accrualOn"],
            "payload": payload,
        }
        linha.tomador_id = tn.t.id
        linha.modelo_id = item.modelo_id
        linha.competencia = competencia
        linha.provedor = "nfeio"
        linha.nfeio_ambiente = ambiente
        linha.nfeio_external_id = ext
        linha.nfeio_id = None
        linha.flow_status = linha.flow_message = linha.check_code = None
        linha.status = "enviando"
        linha.descricao = m.descricao
        linha.valor_servico = Decimal(item.valor or 0)
        linha.base_calculo = item.base_calculo if pct else None
        linha.percentual = item.percentual if pct else None
        linha.ir_retido = m.ir.valor if m.ir.retem else None
        linha.dh_emi = datetime.now(UTC)
        linha.erros = None
        linha.alertas = None
        linha.tentativas = tentativa
        linha.emitido_por = user_id
        linha.enviado_em = datetime.now(UTC)
        await session.commit()  # externalId e JSON gravados ANTES de sair pra NFE.io

        # 3) POST — nunca repete sozinho.
        r = await cliente.emitir(cid, payload)
        # Webhook/worker podem ter aplicado a nota enquanto o POST voltava:
        # relê travado e só aplica a resposta se a linha ainda está 'enviando'.
        await session.refresh(linha, with_for_update=True)
        _log(session, r, c.id, linha.id, ambiente)
        if linha.status != "enviando":
            await session.commit()
        elif _aplicar_post(linha, r) == "adotar":
            ra, nota = await cliente.nota_por_external(cid, ext)
            _log(session, ra, c.id, linha.id, ambiente)
            if nota is not None:
                aplicar_nota(linha, nota)
            else:
                linha.status = "incerta"
                linha.erros = [
                    {
                        "codigo": "",
                        "descricao": "A NFE.io diz que esta nota já existe, mas ainda não a "
                        "mostra.",
                        "o_que_fazer": "NÃO reenvie: a conferência automática busca a nota.",
                    }
                ]
        await session.commit()
    limpar_cache(cid)
    logger.info(
        "nfse_emitir",
        emissao=str(linha.id),
        status=linha.status,
        http=r.status,
        ambiente=linha.nfeio_ambiente,
    )
    return linha


# --- cancelamento, e-mail, PDF e XML -----------------------------------------------------


async def cancelar(
    session: AsyncSession,
    e: NfseEmissao,
    c_motivo: int,
    x_motivo: str,
    user_id: UUID,
    *,
    cli: nfeio.ClienteNfeio | None = None,
) -> NfseEmissao:
    """DELETE na NFE.io (a API não recebe motivo: o motivo fica só aqui)."""
    if e.status != "emitida" or not e.nfeio_id:
        raise NfseError(409, "nao_cancelavel", "Só nota emitida pode ser cancelada.")
    if c_motivo not in (1, 2, 9):
        raise NfseError(
            422, "motivo_invalido", "Motivo: 1 erro na emissão, 2 serviço não prestado, 9 outros."
        )
    x_motivo = (x_motivo or "").strip()
    if not 15 <= len(x_motivo) <= 255:
        raise NfseError(422, "justificativa_tamanho", "A justificativa tem de 15 a 255 caracteres.")
    if not pode_emitir(e.nfeio_ambiente):
        raise NfseError(409, "producao_bloqueada", MSG_PRODUCAO_BLOQUEADA)
    cid = await _cid(session, e)
    if not cid:
        raise NfseError(422, "nao_ligada", E.MSG_NAO_LIGADA)
    # Trava a linha até o commit do 'cancelando': dois cliques em "cancelar"
    # não viram dois DELETEs (o segundo espera e encontra 'cancelando').
    await session.refresh(e, with_for_update=True)
    if e.status != "emitida":
        await session.rollback()
        raise NfseError(409, "nao_cancelavel", "Só nota emitida pode ser cancelada.")
    async with _cliente(cli) as cliente:
        ev = NfseEvento(
            emissao_id=e.id,
            tipo_evento="101101",
            c_motivo=c_motivo,
            x_motivo=x_motivo,
            status="enviando",
            created_by=user_id,
        )
        session.add(ev)
        e.status = "cancelando"
        await session.commit()

        r = await cliente.cancelar(cid, e.nfeio_id)
        # Mesmo cuidado do emitir: se o webhook/worker já aplicou o resultado
        # enquanto o DELETE voltava, não cobre com a resposta.
        await session.refresh(e, with_for_update=True)
        _log(session, r, e.company_id, e.id, e.nfeio_ambiente)
        if e.status != "cancelando":
            pass
        elif r.status in (200, 201):
            nota = r.corpo if isinstance(r.corpo, Mapping) and r.corpo.get("id") else None
            if nota is not None:
                aplicar_nota(e, nota, cancelamento_pedido_em=datetime.now(UTC))
                if e.status == "cancelada":
                    ev.status = "registrado"
                elif e.status == "emitida":
                    ev.status = "rejeitado"
                    ev.erros = explicar_texto(e.flow_message)
        elif r.status == 202:
            pass  # WaitingSendCancel: a conferência termina
        elif r.incerta:
            ev.status = "incerto"
            ev.erros = [
                {"codigo": str(r.status or ""), "descricao": r.erro_rede or f"HTTP {r.status}"}
            ]
        else:
            ev.status = "rejeitado"
            if r.status in (401, 403):
                ev.erros = [{"codigo": f"HTTP {r.status}", "descricao": nfeio.CHAVE_RECUSADA}]
            else:
                ev.erros = explicar(r.msgs) or [
                    {"codigo": f"HTTP {r.status}", "descricao": "A NFE.io recusou o cancelamento."}
                ]
            e.status = "emitida"
            e.alertas = _com_alerta(
                e.alertas,
                {
                    "codigo": ev.erros[0].get("codigo", ""),
                    "descricao": "O cancelamento foi recusado: "
                    + (ev.erros[0].get("descricao") or f"HTTP {r.status}"),
                    "o_que_fazer": ev.erros[0].get("o_que_fazer") or "A nota continua válida.",
                },
            )
        await session.commit()
    limpar_cache(cid)
    return e


# --- envio manual por e-mail (30/09/2026) ------------------------------------------------
# Eduardo (30/09): nada sai sozinho para o tomador. O e-mail do tomador não vai
# mais à NFE.io (`montar_borrower`) e o "Enviar por e-mail" da tela sai pelo
# próprio DaVinci (o mesmo Mailjet da Logística), com o PDF e o XML anexados.
# O PUT /sendemail da NFE.io não serve: não aceita destinatário e manda só para
# o e-mail gravado na nota — que agora vai vazio.

MSG_EMAIL_SO_EMITIDA = "Só nota emitida pode ser enviada por e-mail."
MSG_EMAIL_NAO_CONFIGURADO = (
    "O envio de e-mail não está configurado neste servidor (faltam as chaves do Mailjet). "
    "Nada foi enviado."
)
# Prazo de cada anexo buscado na NFE.io (o mesmo do lote): sem ele, com as
# repetições do GET e o timeout de leitura de 75 s, uma NFE.io lenta segurava a
# tela uns 4 a 6 minutos antes de mandar o e-mail.
PRAZO_ANEXO_S = 40.0


def slug_arquivo(v: str | None) -> str:
    """Pedaço de nome de arquivo sem acento, só com letras, números, _ e -."""
    txt = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_-]+", "-", txt).strip("-_")[:40]


def nome_do_anexo(e: NfseEmissao, empresa: str | None, ext: str) -> str:
    """NFSe_<empresa>_<nº>.pdf: o nº da NFS-e é POR EMPRESA (ATV nº 4, Rocha nº 4)."""
    return f"NFSe_{slug_arquivo(empresa) or 'empresa'}_{e.n_nfse or 'sem-numero'}.{ext}"


def _responder_para(email: str | None, nome: str) -> tuple[str, str] | None:
    """O "responder para" do e-mail: o 1º endereço do e-mail da empresa, se
    for válido. O cadastro da empresa não confere o e-mail (FiscalIn só tira
    os espaços), e um endereço torto no ReplyTo faz o Mailjet recusar o envio
    inteiro: melhor mandar sem o "responder para" do que não mandar."""
    primeiro = next((x for x in re.split(r"[,;\s]+", email or "") if x), "")
    if not primeiro:
        return None
    try:
        return validate_email(primeiro, check_deliverability=False).normalized, nome
    except EmailNotValidError:
        logger.warning("nfse_email_responder_para_invalido")
        return None


def _texto_do_email(
    *, numero: str, razao: str, tomador: str, comp: str, valor: str, teste: bool
) -> tuple[str, str]:
    """(texto puro, HTML) curtos: quem recebe é o financeiro do tomador."""
    linhas = [
        "Olá,",
        f"Segue a nota fiscal de serviço (NFS-e) nº {numero} emitida por {razao}"
        + (f" para {tomador}." if tomador else "."),
        f"Competência: {comp} · Valor do serviço: {valor}.",
        "O PDF e o XML da nota estão anexados.",
    ]
    if teste:
        linhas.append("ATENÇÃO: nota de TESTE, sem valor fiscal.")
    linhas.append(f"Mensagem enviada pelo DaVinci em nome de {razao}.")
    texto = "\n\n".join(linhas)
    corpo = "".join(f"<p>{html.escape(x)}</p>" for x in linhas)
    return texto, f'<div style="font-family:Arial,sans-serif;font-size:14px">{corpo}</div>'


async def enviar_por_email(
    session: AsyncSession,
    e: NfseEmissao,
    para: list[str],
    *,
    salvar_no_tomador: bool = False,
    sender: email_svc.EmailSender | None = None,
    cli: nfeio.ClienteNfeio | None = None,
) -> list[str]:
    """Manda o PDF e o XML da nota pelo DaVinci para os endereços digitados
    (um e-mail por endereço). Só nota emitida (cancelada não vai). Com
    `salvar_no_tomador`, os endereços viram o e-mail do cadastro do tomador.
    Devolve para quem foi."""
    if e.status != "emitida" or not e.nfeio_id:
        raise NfseError(409, "sem_nota", MSG_EMAIL_SO_EMITIDA)
    para = list(dict.fromkeys(x.strip() for x in para if (x or "").strip()))
    if not para:
        raise NfseError(422, "sem_destinatario", "Digite para quem mandar o e-mail.")
    sender = sender or email_svc.get_email_sender()
    # Sem as chaves do Mailjet o envio vira só uma linha no log: fora do
    # localhost a tela diria "enviado" sem ter enviado nada.
    if getattr(sender, "name", "") == "console" and get_settings().env != "development":
        raise NfseError(503, "email_nao_configurado", MSG_EMAIL_NAO_CONFIGURADO)

    # Os anexos (o XML costuma vir do banco: é guardado na hora da emissão).
    async with _cliente(cli) as cliente:
        pdf, _ = await baixar(session, e, "pdf", cli=cliente, prazo=PRAZO_ANEXO_S)
        xml, _ = await baixar(session, e, "xml", cli=cliente, prazo=PRAZO_ANEXO_S)
    c = await session.get(Company, e.company_id)
    f = await session.get(CompanyFiscal, e.company_id)
    snap = e.snapshot or {}
    razao = (snap.get("prestador") or {}).get("nome") or (c.razao_social if c else "") or ""
    tomador = (snap.get("tomador") or {}).get("nome") or ""
    numero = e.n_nfse or "sem número"
    comp = f"{e.competencia.month:02d}/{e.competencia.year}"
    teste = eh_teste(e.nfeio_ambiente)
    assunto = f"NFS-e nº {numero} · {razao} · competência {comp}"
    if teste:
        assunto = f"[TESTE, sem valor fiscal] {assunto}"
    texto, corpo_html = _texto_do_email(
        numero=numero,
        razao=razao,
        tomador=tomador,
        comp=comp,
        valor=T.fmt_reais(e.valor_servico),
        teste=teste,
    )
    empresa = c.apelido if c else None
    anexos = [
        (nome_do_anexo(e, empresa, "pdf"), "application/pdf", pdf),
        (nome_do_anexo(e, empresa, "xml"), "application/xml", xml),
    ]
    # Resposta do tomador vai para o e-mail da empresa (se cadastrado e
    # válido), não para o no-reply do DaVinci.
    responder = _responder_para(f.email if f is not None else None, razao)

    enviados: list[str] = []
    for destino in para:
        try:
            await sender.send(
                to=destino,
                subject=assunto,
                html=corpo_html,
                text=texto,
                from_name=razao or None,
                reply_to=responder,
                attachments=anexos,
            )
        except httpx.HTTPError as ex:
            resposta = getattr(ex, "response", None)
            logger.warning(
                "nfse_email_falhou",
                emissao=str(e.id),
                http=getattr(resposta, "status_code", None),
                erro=type(ex).__name__,
            )
            ja = f" Já tinha ido para: {', '.join(enviados)}." if enviados else ""
            raise NfseError(
                502,
                "email_falhou",
                f"O e-mail para {destino} não saiu: o serviço de e-mail recusou ou não "
                f"respondeu. Tente de novo em instantes.{ja}",
                enviados=enviados,
            ) from ex
        enviados.append(destino)
    logger.info("nfse_email_enviado", emissao=str(e.id), destinos=len(enviados), teste=teste)

    if salvar_no_tomador and e.tomador_id:
        t = await session.get(NfseTomador, e.tomador_id)
        if t is not None:
            t.email = ", ".join(enviados)
            await session.commit()
    return enviados


MSG_SEM_PDF = "A NFE.io ainda não gerou o PDF"
MSG_SEM_XML = "A NFE.io ainda não gerou o XML"


async def baixar(
    session: AsyncSession,
    e: NfseEmissao,
    tipo: str,
    *,
    cli: nfeio.ClienteNfeio | None = None,
    prazo: float | None = None,
) -> tuple[bytes, str]:
    """PDF/XML da NFE.io. O XML da nota emitida fica guardado (nfse_xml_b64).
    `prazo` (segundos) vale só para a ida à NFE.io — o commit fica de fora,
    para o corte não pegar a gravação no meio. Estourou: 502 nfeio_sem_resposta."""
    sem = MSG_SEM_PDF if tipo == "pdf" else MSG_SEM_XML
    if tipo == "xml" and e.nfse_xml_b64:
        return base64.b64decode(e.nfse_xml_b64), "application/xml"
    if not e.nfeio_id or e.status not in ("emitida", "cancelando", "cancelada"):
        raise NfseError(404, "sem_arquivo", sem)
    cid = await _cid(session, e)
    if not cid:
        raise NfseError(404, "sem_arquivo", sem)
    try:
        async with _cliente(cli) as cliente:
            arq = await asyncio.wait_for(
                cliente.pdf(cid, e.nfeio_id) if tipo == "pdf" else cliente.xml(cid, e.nfeio_id),
                prazo,
            )
    except TimeoutError as ex:
        # Só o prazo corta assim: rede fora do ar já volta como Resposta sem status.
        espera = prazo or 0.0
        _log(
            session,
            nfeio.Resposta(
                tipo,
                None,
                erro_rede=f"prazo esgotado ({espera:.0f} s)",
                duracao_ms=int(espera * 1000),
            ),
            e.company_id,
            e.id,
            e.nfeio_ambiente,
        )
        await session.commit()
        raise NfseError(502, "nfeio_sem_resposta", E.MSG_SEM_RESPOSTA) from ex
    _log(session, arq.resposta, e.company_id, e.id, e.nfeio_ambiente)
    if arq.conteudo and tipo == "xml" and e.status == "emitida":
        e.nfse_xml_b64 = base64.b64encode(arq.conteudo).decode("ascii")
    await session.commit()
    if arq.conteudo:
        padrao = "application/pdf" if tipo == "pdf" else "application/xml"
        return arq.conteudo, (arq.tipo or padrao).split(";")[0].strip() or padrao
    if arq.resposta.status in (401, 403):
        raise NfseError(503, "chave_nfeio", nfeio.CHAVE_RECUSADA)
    if arq.resposta.status == 404:
        raise NfseError(404, "sem_arquivo", sem)
    raise NfseError(502, "nfeio_sem_resposta", E.MSG_SEM_RESPOSTA)
