"""Empresas do DaVinci ↔ empresas da NFE.io (29/09/2026). Só GET na NFE.io.

- `ligar`: uma empresa, pelo id, pelo link colado do painel ou pelo CNPJ.
  Nunca liga a empresa da NFE.io de OUTRO CNPJ (emitiria nota em nome errado).
- `sincronizar` (botão do admin): casa as 25 empresas da conta com
  `companies.cnpj`, cria/atualiza `company_fiscal` e diz o que sobrou dos dois
  lados. Não cria empresa no DaVinci.
- `atualizar_ligadas` (worker, 1x/dia): relê ambiente, situação e certificado.
- `pendencias_e_avisos`: o que impede (ou só merece aviso) a empresa emitir.

Da resposta da NFE.io guarda-se só uma lista PERMITIDA de campos: a Inscrição
Municipal pode trazer login/senha da prefeitura, que nunca vão pro banco nem log.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company
from app.models.nfse import CompanyFiscal
from app.services.nfse import nfeio
from app.services.nfse import texto as T  # noqa: N812
from app.services.nfse.ambiente import eh_teste
from app.services.nfse.erros import NfseError

logger = structlog.get_logger()

# Padrão medido nas 137 notas reais do grupo (intermediação).
CITY_SERVICE_CODE_PADRAO = "6303"
FEDERAL_SERVICE_CODE_PADRAO = "10.05"
BARUERI = "3505708"
SAO_PAULO = "3550308"
AVISO_CERTIFICADO_DIAS = 15
ULTIMA_NOTA_RECENTE_DIAS = 60
EMISSOR_NACIONAL = "530000"
PARALELO = 5

CAMPOS_EMPRESA = (
    "id",
    "name",
    "tradeName",
    "federalTaxNumber",
    "municipalTaxNumber",
    "environment",
    "fiscalStatus",
    "status",
    "taxRegime",
    "specialTaxRegime",
    "legalNature",
    "rpsSerialNumber",
    "rpsNumber",
    "issRate",
)
CAMPOS_ENDERECO = (
    "postalCode",
    "street",
    "number",
    "additionalInformation",
    "district",
    "state",
    "country",
)
CAMPOS_INSCRICAO = (
    "id",
    "environment",
    "fiscalStatus",
    "status",
    "taxNumber",
    "rpsSerialNumber",
    "rpsSerialNumbers",
    "specialTaxRegime",
    "regionalTaxNumber",
    "issRate",
    "legalNature",
    "federalTaxDetermination",
    "municipalTaxDetermination",
)
CAMPOS_NOTA = ("id", "number", "status", "flowStatus", "createdOn", "issuedOn")

MSG_SEM_RESPOSTA = "A NFE.io não respondeu agora. Tente de novo em alguns minutos."
MSG_NAO_LIGADA = "Empresa não ligada na NFE.io (use Sincronizar ou Ligar na aba Empresas)."
PEND_SEM_CODIGO = "Falta o código do serviço no município (aba Empresas ou na nota fixa)."
PEND_BARUERI = (
    "Código do serviço em Barueri a confirmar com a contabilidade "
    "(a prefeitura de Barueri recusa o 6303 desde 03/2026)."
)


def _so(d: Any, campos: tuple[str, ...]) -> dict:
    if not isinstance(d, Mapping):
        return {}
    return {k: d[k] for k in campos if k in d and d[k] not in (None, "")}


def _cidade(d: Any) -> dict:
    c = d.get("city") if isinstance(d, Mapping) else None
    return _so(c, ("code", "name", "state", "country"))


def _data(v: Any) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def cnpj_da_nfeio(empresa: Mapping[str, Any]) -> str:
    """Na v1 o CNPJ vem como número (e perde o zero da frente)."""
    return T.normalizar_documento(empresa.get("federalTaxNumber"))


def nome_da_nfeio(empresa: Mapping[str, Any]) -> str:
    return str(empresa.get("name") or empresa.get("tradeName") or "").strip()


def resumo(empresa: Mapping[str, Any], inscricoes: list[dict], ultima: dict | None) -> dict:
    """Só campos permitidos (nunca login/senha da prefeitura)."""
    e = _so(empresa, CAMPOS_EMPRESA)
    if "federalTaxNumber" in e:
        e["federalTaxNumber"] = cnpj_da_nfeio(empresa)
    if "name" in e:
        e["name"] = nome_da_nfeio(empresa)
    end = _so(empresa.get("address"), CAMPOS_ENDERECO)
    cidade = _cidade(empresa.get("address"))
    if cidade:
        end["city"] = cidade
    cert = empresa.get("certificate") if isinstance(empresa.get("certificate"), Mapping) else {}
    return {
        "empresa": e,
        "endereco": end,
        "certificado": {
            "status": cert.get("status"),
            "expiresOn": cert.get("expiresOn") or cert.get("validUntil"),
        },
        "inscricoes": [{**_so(i, CAMPOS_INSCRICAO), "city": _cidade(i)} for i in inscricoes[:10]],
        "ultima_nota": (
            {**_so(ultima, CAMPOS_NOTA), "flowMessage": str(ultima.get("flowMessage") or "")[:300]}
            if ultima
            else None
        ),
    }


def escolher_inscricao(empresa: Mapping[str, Any], inscricoes: list[dict]) -> dict | None:
    """A Inscrição Municipal do município da empresa; senão a ativa; senão a primeira."""
    if not inscricoes:
        return None
    cod = _cidade(empresa.get("address")).get("code")
    for i in inscricoes:
        if cod and _cidade(i).get("code") == cod:
            return i
    for i in inscricoes:
        if i.get("status") == "Active":
            return i
    return inscricoes[0]


def aplicar(
    f: CompanyFiscal,
    empresa: Mapping[str, Any],
    inscricoes: list[dict],
    ultima: dict | None,
) -> None:
    im = escolher_inscricao(empresa, inscricoes) or {}
    end = empresa.get("address") if isinstance(empresa.get("address"), Mapping) else {}
    cert = empresa.get("certificate") if isinstance(empresa.get("certificate"), Mapping) else {}
    f.nfeio_company_id = str(empresa.get("id") or "").lower() or f.nfeio_company_id
    f.nfeio_ambiente = im.get("environment") or empresa.get("environment") or None
    f.nfeio_status_fiscal = im.get("fiscalStatus") or empresa.get("fiscalStatus") or None
    # A NFE.io manda o texto "None" quando o regime não foi informado (Injox,
    # Rodrigues): vira vazio aqui, senão a tela mostra "None".
    regime = str(empresa.get("taxRegime") or "").strip()
    f.nfeio_regime = regime if regime and regime != "None" else None
    f.nfeio_im = im.get("taxNumber") or empresa.get("municipalTaxNumber") or None
    f.nfeio_municipio = _cidade(end).get("name")
    f.nfeio_uf = (str(end.get("state") or "")[:2].upper()) or None
    f.nfeio_cert_status = cert.get("status") or None
    f.nfeio_cert_expira = _data(cert.get("expiresOn") or cert.get("validUntil"))
    f.nfeio_resumo = resumo(empresa, inscricoes, ultima)
    f.nfeio_sincronizado_em = datetime.now(UTC)


def novo_fiscal(company_id: UUID) -> CompanyFiscal:
    return CompanyFiscal(
        company_id=company_id,
        city_service_code=CITY_SERVICE_CODE_PADRAO,
        federal_service_code=FEDERAL_SERVICE_CODE_PADRAO,
        c_nbs="102010000",
        retencao_ir="auto",
    )


def codigo_municipio(f: CompanyFiscal | None) -> str | None:
    end = ((f.nfeio_resumo or {}).get("endereco") or {}) if f is not None else {}
    return (end.get("city") or {}).get("code")


def endereco_nfeio(f: CompanyFiscal | None) -> dict:
    """Endereço da empresa como a NFE.io tem (serve de endereço do tomador do grupo)."""
    if f is None:
        return {}
    return dict((f.nfeio_resumo or {}).get("endereco") or {})


def _emissor_nacional(f: CompanyFiscal) -> bool:
    return any(
        str(i.get("regionalTaxNumber") or "") == EMISSOR_NACIONAL
        for i in (f.nfeio_resumo or {}).get("inscricoes") or []
    )


def pendencias_e_avisos(
    c: Company, f: CompanyFiscal | None, *, hoje: date | None = None
) -> tuple[list[str], list[str]]:
    """Pendências bloqueiam a emissão; avisos só informam."""
    hoje = hoje or date.today()
    p: list[str] = []
    a: list[str] = []
    if not c.cnpj:
        p.append("Falta o CNPJ da empresa (Cadastros › Empresas).")
    if f is None or not f.nfeio_company_id:
        p.append(MSG_NAO_LIGADA)
        return p, a
    municipio = f.nfeio_municipio or "da empresa"
    st = f.nfeio_status_fiscal
    if st == "CityNotSupported":
        p.append(f"A NFE.io não atende a prefeitura de {municipio} (CityNotSupported).")
    elif st and st != "Active":
        p.append(f"O cadastro da empresa na NFE.io está pendente (situação {st}).")
    teste = eh_teste(f.nfeio_ambiente)
    exp = f.nfeio_cert_expira
    vencido = f.nfeio_cert_status == "Overdue" or (exp is not None and exp < hoje)
    if vencido:
        quando = f" em {exp:%d/%m/%Y}" if exp else ""
        if teste:
            a.append(
                f"O certificado digital na NFE.io venceu{quando} (em teste a NFE.io não exige)."
            )
        else:
            p.append(f"O certificado digital na NFE.io venceu{quando}: renove no painel da NFE.io.")
    elif exp is not None and (exp - hoje).days <= AVISO_CERTIFICADO_DIAS:
        dias = (exp - hoje).days
        a.append(
            f"O certificado digital na NFE.io vence em {dias} dia(s) ({exp:%d/%m/%Y}): "
            "renove antes para não parar a emissão."
        )
    codigo = (f.city_service_code or "").strip()
    if not codigo:
        p.append(PEND_SEM_CODIGO)
    elif codigo_municipio(f) == BARUERI and codigo == CITY_SERVICE_CODE_PADRAO:
        p.append(PEND_BARUERI)
    ultima = (f.nfeio_resumo or {}).get("ultima_nota") or {}
    criada = _data(ultima.get("createdOn"))
    if ultima.get("flowStatus") == "IssueFailed" and ultima.get("status") != "Issued":
        msg = str(ultima.get("flowMessage") or "").strip()
        recente = criada is not None and (hoje - criada).days <= ULTIMA_NOTA_RECENTE_DIAS
        if "[1207]" in msg:
            # Vale mesmo antiga: é a ÚLTIMA nota, então nada saiu depois dela
            # (Rodrigues/Victor MEI: as 3 notas de jun–jul/2026 deram 1207).
            a.append(
                "A prefeitura ainda não autorizou esta empresa a emitir NFS-e ([1207] na última "
                "nota da NFE.io). Confirme a autorização antes de emitir."
            )
        elif msg and recente:
            a.append(f"A última nota desta empresa na NFE.io foi recusada: {msg[:200]}")
    if (
        f.nfeio_regime == "SimplesNacional"
        and codigo_municipio(f) == SAO_PAULO
        and not _emissor_nacional(f)
    ):
        a.append(
            "A partir de 01/11/2026 a prefeitura de São Paulo exige o Emissor Nacional para "
            "empresas do Simples: confira essa opção na Inscrição Municipal da NFE.io."
        )
    return p, a


# --- leitura na NFE.io ------------------------------------------------------------


def _checar(r: nfeio.Resposta) -> None:
    if r.status in (401, 403):
        raise NfseError(503, "chave_nfeio", nfeio.CHAVE_RECUSADA)
    if not r.ok:
        raise NfseError(502, "nfeio_sem_resposta", MSG_SEM_RESPOSTA)


async def _detalhes(cli: nfeio.ClienteNfeio, cid: str) -> tuple[list[dict], dict | None]:
    """Inscrições Municipais + a nota mais nova (pra avisar recusa recente)."""
    ri, inscricoes = await cli.inscricoes_municipais(cid)
    if ri.status in (401, 403):
        raise NfseError(503, "chave_nfeio", nfeio.CHAVE_RECUSADA)
    _rn, notas = await cli.listar_notas(cid, por_pagina=5, max_paginas=1)
    return inscricoes, (notas[0] if notas else None)


async def _outra_ligada(session: AsyncSession, company_id: UUID, nfeio_id: str) -> Company | None:
    outro = (
        await session.execute(
            select(CompanyFiscal).where(
                CompanyFiscal.nfeio_company_id == nfeio_id,
                CompanyFiscal.company_id != company_id,
            )
        )
    ).scalar_one_or_none()
    return await session.get(Company, outro.company_id) if outro is not None else None


async def ligar(
    session: AsyncSession, company_id: UUID, ref: str | None, cli: nfeio.ClienteNfeio
) -> CompanyFiscal:
    c = await session.get(Company, company_id)
    if c is None:
        raise NfseError(404, "empresa_nao_encontrada", "Empresa não encontrada.")
    ref = (ref or "").strip()
    nid = nfeio.normalizar_id(ref)
    empresa: dict | None = None
    if nid:
        r = await cli.empresa(nid)
        if r.status in (400, 404):
            raise NfseError(404, "nao_encontrada", "Não achei essa empresa na NFE.io.")
        _checar(r)
        corpo = r.corpo if isinstance(r.corpo, Mapping) else {}
        achada = corpo.get("companies") if isinstance(corpo.get("companies"), Mapping) else corpo
        empresa = dict(achada) if achada.get("id") else None
    else:
        doc = T.normalizar_documento(ref or c.cnpj)
        if len(doc) != 14:
            raise NfseError(
                422,
                "referencia_invalida",
                "Cole o link da empresa no painel da NFE.io, o id dela ou o CNPJ.",
            )
        r, todas = await cli.listar_empresas()
        _checar(r)
        empresa = next((e for e in todas if cnpj_da_nfeio(e) == doc), None)
    if empresa is None:
        raise NfseError(404, "nao_encontrada", "Não achei essa empresa na NFE.io.")
    cnpj = cnpj_da_nfeio(empresa)
    if T.normalizar_documento(c.cnpj) != cnpj:
        raise NfseError(
            422,
            "cnpj_diferente",
            f"A empresa da NFE.io ({nome_da_nfeio(empresa)}, CNPJ {cnpj}) não é "
            f"{c.apelido or c.razao_social} (CNPJ {c.cnpj or 'sem CNPJ'}).",
        )
    nid = str(empresa.get("id") or "").lower()
    outra = await _outra_ligada(session, c.id, nid)
    if outra is not None:
        raise NfseError(
            409,
            "ja_ligada",
            f"Essa empresa da NFE.io já está ligada a {outra.apelido or outra.razao_social}.",
        )
    inscricoes, ultima = await _detalhes(cli, nid)
    f = await session.get(CompanyFiscal, c.id)
    if f is None:
        f = novo_fiscal(c.id)
        session.add(f)
    aplicar(f, empresa, inscricoes, ultima)
    await session.commit()
    logger.info("nfse_nfeio_ligada", company=str(c.id), nfeio=nid, ambiente=f.nfeio_ambiente)
    return f


async def atualizar(
    session: AsyncSession, company_id: UUID, cli: nfeio.ClienteNfeio
) -> CompanyFiscal:
    f = await session.get(CompanyFiscal, company_id)
    if f is None or not f.nfeio_company_id:
        raise NfseError(422, "nao_ligada", MSG_NAO_LIGADA)
    r = await cli.empresa(f.nfeio_company_id)
    if r.status == 404:
        raise NfseError(404, "nao_encontrada", "A NFE.io não tem mais essa empresa.")
    _checar(r)
    corpo = r.corpo if isinstance(r.corpo, Mapping) else {}
    empresa = corpo.get("companies") if isinstance(corpo.get("companies"), Mapping) else corpo
    inscricoes, ultima = await _detalhes(cli, f.nfeio_company_id)
    aplicar(f, dict(empresa), inscricoes, ultima)
    await session.commit()
    return f


async def _detalhes_de_todas(
    cli: nfeio.ClienteNfeio, ids: list[str]
) -> dict[str, tuple[list[dict], dict | None]]:
    sem = asyncio.Semaphore(PARALELO)

    async def um(cid: str) -> tuple[str, tuple[list[dict], dict | None]]:
        async with sem:
            return cid, await _detalhes(cli, cid)

    return dict(await asyncio.gather(*(um(i) for i in ids)))


async def sincronizar(session: AsyncSession, cli: nfeio.ClienteNfeio) -> dict:
    """Casa por CNPJ; liga/atualiza as que casam. Só GET na NFE.io."""
    r, todas = await cli.listar_empresas()
    _checar(r)
    empresas = list((await session.execute(select(Company).order_by(Company.apelido))).scalars())
    por_cnpj: dict[str, Company] = {}
    for c in empresas:
        doc = T.normalizar_documento(c.cnpj)
        if len(doc) == 14:
            por_cnpj.setdefault(doc, c)
    fiscais = {f.company_id: f for f in (await session.execute(select(CompanyFiscal))).scalars()}
    casadas: list[tuple[Company, dict]] = []
    so_na_nfeio = []
    usadas: set[UUID] = set()
    for e in todas:
        c = por_cnpj.get(cnpj_da_nfeio(e))
        if c is None or c.id in usadas:
            so_na_nfeio.append(
                {
                    "nfeio_id": str(e.get("id") or "").lower(),
                    "nome": nome_da_nfeio(e),
                    "cnpj": cnpj_da_nfeio(e) or None,
                    "ambiente": e.get("environment"),
                }
            )
            continue
        usadas.add(c.id)
        casadas.append((c, e))
    detalhes = await _detalhes_de_todas(cli, [str(e.get("id") or "").lower() for _, e in casadas])
    ligadas = []
    for c, e in casadas:
        nid = str(e.get("id") or "").lower()
        # Outra empresa do DaVinci presa ao mesmo id (CNPJ trocado): solta antes.
        for outro in fiscais.values():
            if outro.nfeio_company_id == nid and outro.company_id != c.id:
                outro.nfeio_company_id = None
        await session.flush()
        f = fiscais.get(c.id)
        if f is None:
            f = novo_fiscal(c.id)
            session.add(f)
            fiscais[c.id] = f
        inscricoes, ultima = detalhes[nid]
        aplicar(f, e, inscricoes, ultima)
        ligadas.append(
            {
                "company_id": str(c.id),
                "apelido": c.apelido,
                "nfeio_nome": nome_da_nfeio(e),
                "ambiente": f.nfeio_ambiente,
            }
        )
    await session.commit()
    so_no_davinci = [
        {"company_id": str(c.id), "apelido": c.apelido, "cnpj": c.cnpj}
        for c in empresas
        if c.id not in usadas
    ]
    logger.info(
        "nfse_nfeio_sincronizar",
        ligadas=len(ligadas),
        so_na_nfeio=len(so_na_nfeio),
        so_no_davinci=len(so_no_davinci),
    )
    return {"ligadas": ligadas, "so_na_nfeio": so_na_nfeio, "so_no_davinci": so_no_davinci}


async def atualizar_ligadas(session: AsyncSession, cli: nfeio.ClienteNfeio) -> dict:
    """Worker (1x/dia): relê ambiente, situação e certificado das já ligadas."""
    r, todas = await cli.listar_empresas()
    _checar(r)
    por_id = {str(e.get("id") or "").lower(): e for e in todas}
    fiscais = [
        f for f in (await session.execute(select(CompanyFiscal))).scalars() if f.nfeio_company_id
    ]
    achadas = [f for f in fiscais if f.nfeio_company_id in por_id]
    detalhes = await _detalhes_de_todas(cli, [f.nfeio_company_id for f in achadas])
    for f in achadas:
        inscricoes, ultima = detalhes[f.nfeio_company_id]
        aplicar(f, por_id[f.nfeio_company_id], inscricoes, ultima)
    await session.commit()
    sumidas = [f.nfeio_company_id for f in fiscais if f.nfeio_company_id not in por_id]
    return {"atualizadas": len(achadas), "sumidas_na_nfeio": len(sumidas)}
