from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import MARKETPLACES, Company, CompanyCertificate, Store, StoreInfo, User
from app.models.enums import UserRole
from app.schemas.companies import (
    CertificadoResumo,
    CompanyCreate,
    CompanyDetailOut,
    CompanyGridOut,
    CompanyGridRow,
    CompanyOut,
    CompanyPatch,
    CompanyResumo,
    DesbloqueioIn,
    DesbloqueioOut,
    GridStoreCell,
    StoreOut,
    _normalize_ip,
)
from app.security import senha_extra
from app.security.senha_extra import require_empresas_unlock

logger = structlog.get_logger()
router = APIRouter(prefix="/api/companies", tags=["companies"])


def _company_label(c: Company, override: str | None = None) -> str:
    return override or c.apelido


def _norm_conta(s: str | None) -> str:
    """Nome de conta comparável: minúsculas e SEM espaços ("dream 2" == "dream2").

    Os nomes são digitados à mão em telas diferentes (companies.apelido vs
    store_info.account_name), então espaço a mais/a menos não pode quebrar o
    casamento que pinta o X verde no grid.
    """
    return "".join((s or "").split()).lower()


async def _garante_ip_livre(session: AsyncSession, ip: str | None, *, exceto: UUID | None) -> None:
    """Recusa um IP que já é de outra empresa, dizendo DE QUAL.

    Um IP por empresa (Eduardo, 25/09/2026): o marketplace liga contas que
    aparecem pelo mesmo IP. O índice `uq_companies_ip` é quem garante de
    verdade; esta checagem existe só para a mensagem vir com o nome da empresa
    em vez de um erro de banco.
    """
    if not ip:
        return
    q = select(Company.apelido).where(func.lower(func.btrim(Company.ip)) == ip.strip().lower())
    if exceto is not None:
        q = q.where(Company.id != exceto)
    dono = (await session.execute(q)).scalars().first()
    if dono is not None:
        raise HTTPException(409, detail={"code": "ip_exists", "empresa": dono})


def _ip_ou_422(bruto: str | None) -> str | None:
    """Normaliza o IP ou responde 422 SÓ com o código do erro.

    O IP não é validado no formulário de propósito: o 422 padrão do FastAPI
    devolve o texto digitado, e colar a linha do proxy do AdsPower
    ("ip:porta:usuario:senha") mandaria a senha do proxy de volta na resposta.
    """
    try:
        return _normalize_ip(bruto)
    except ValueError as e:
        raise HTTPException(422, detail={"code": str(e)}) from None


def _conflito_de_unicidade(e: IntegrityError) -> HTTPException | None:
    msg = str(e.orig)
    if "uq_companies_cnpj" in msg:
        return HTTPException(409, detail={"code": "cnpj_exists"})
    if "uq_companies_ip" in msg:
        # Duas gravações ao mesmo tempo passaram pela checagem: o índice barrou.
        return HTTPException(409, detail={"code": "ip_exists"})
    return None


# Senha extra da tela Empresas (Eduardo, 25/09/2026): a mesma do Valuation, mas
# com desbloqueio próprio. Toda rota que entrega dado sensível ou altera empresa
# exige a chave; a lista aberta só devolve id, apelido e razão social.
_TRAVA = [Depends(require_empresas_unlock)]


@router.post("/unlock", response_model=DesbloqueioOut)
async def desbloquear_empresas(
    body: DesbloqueioIn,
    u: Annotated[User, Depends(require_permission("empresa", "view"))],
) -> DesbloqueioOut:
    """Confere a senha extra e devolve uma chave de 15 minutos. 5 erros
    seguidos travam a pessoa por 15 minutos (app/security/senha_extra.py)."""
    await senha_extra.conferir_senha(body.password, user_id=u.id, escopo="empresas")
    token, ttl = senha_extra.fazer_token("empresas")
    return DesbloqueioOut(token=token, expires_in=ttl)


@router.get("/grid", response_model=CompanyGridOut, dependencies=_TRAVA)
async def companies_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
) -> CompanyGridOut:
    companies = (await session.execute(select(Company).order_by(Company.apelido))).scalars().all()
    stores_rows = (await session.execute(select(Store))).scalars().all()
    by_company: dict[UUID, dict[str, Store]] = {}
    for s in stores_rows:
        by_company.setdefault(s.company_id, {})[s.marketplace.value] = s

    # store_info acts as a fallback presence signal: if a row exists with
    # platform=mk and account_name matching company.apelido, mark the cell
    # green even when no Store row was ever created. Lets operators see
    # accounts that exist on the Lojas page but were never wired through
    # /companies grid before.
    si_rows = (
        await session.execute(
            select(StoreInfo.platform, StoreInfo.account_name).where(
                StoreInfo.account_name.isnot(None)
            )
        )
    ).all()
    si_by_platform: dict[str, set[str]] = {}
    for plat, name in si_rows:
        if not name:
            continue
        si_by_platform.setdefault((plat or "").strip().lower(), set()).add(
            _norm_conta(name)
        )

    # Certificado digital: só admin vê (mesma regra das rotas de certificado).
    # Seleciona colunas uma a uma para NUNCA trazer o arquivo nem a senha
    # cifrados para a memória só para desenhar a tabela.
    certificados: dict[UUID, CertificadoResumo] = {}
    if _u.role == UserRole.ADMIN:
        cert_rows = (
            await session.execute(
                select(
                    CompanyCertificate.id,
                    CompanyCertificate.company_id,
                    CompanyCertificate.filename,
                    CompanyCertificate.password_enc.is_not(None),
                    CompanyCertificate.expires_at,
                ).order_by(CompanyCertificate.created_at.desc())
            )
        ).all()
        for cid, company_id, filename, tem_senha, vence in cert_rows:
            atual = certificados.get(company_id)
            if atual is None:
                # O mais recente representa a empresa; os outros só contam.
                certificados[company_id] = CertificadoResumo(
                    id=cid, filename=filename, has_password=bool(tem_senha), expires_at=vence
                )
            else:
                atual.total += 1

    rows: list[CompanyGridRow] = []
    for c in companies:
        cells: dict[str, GridStoreCell | None] = {}
        company_stores = by_company.get(c.id, {})
        apelido_lower = _norm_conta(c.apelido)
        for mk in MARKETPLACES:
            s = company_stores.get(mk)
            if s is not None:
                cells[mk] = GridStoreCell(
                    id=s.id,
                    status=s.status.value,
                    label=_company_label(c, s.apelido_override),
                    integration_id=s.integration_id,
                    bling_store_id=s.bling_store_id,
                )
            elif apelido_lower and apelido_lower in si_by_platform.get(mk, set()):
                # Synthetic cell — no Store row to act on; UI hides destructive
                # actions because `id` is None.
                cells[mk] = GridStoreCell(
                    id=None,
                    status="active",
                    label=c.apelido,
                    from_store_info=True,
                )
            else:
                cells[mk] = None
        rows.append(
            CompanyGridRow(
                company=CompanyOut.model_validate(c),
                stores=cells,
                certificado=certificados.get(c.id),
            )
        )
    return CompanyGridOut(marketplaces=list(MARKETPLACES), rows=rows)


@router.get("", response_model=list[CompanyResumo])
async def list_companies(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
) -> list[CompanyResumo]:
    """Lista aberta, para os menus de outras telas: só id, apelido e razão
    social. O resto dos dados da empresa exige a senha extra."""
    rows = (
        await session.execute(
            select(Company.id, Company.apelido, Company.razao_social).order_by(Company.apelido)
        )
    ).all()
    return [CompanyResumo(id=i, apelido=a, razao_social=r) for i, a, r in rows]


@router.get("/{company_id}", response_model=CompanyDetailOut, dependencies=_TRAVA)
async def get_company(
    company_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "view"))],
) -> CompanyDetailOut:
    c = (await session.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    stores = (await session.execute(select(Store).where(Store.company_id == c.id))).scalars().all()
    out = CompanyDetailOut.model_validate(c)
    out.stores = [StoreOut.model_validate(s) for s in stores]
    return out


@router.post(
    "", response_model=CompanyOut, status_code=status.HTTP_201_CREATED, dependencies=_TRAVA
)
async def create_company(
    body: CompanyCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> CompanyOut:
    ip = _ip_ou_422(body.ip)
    await _garante_ip_livre(session, ip, exceto=None)
    c = Company(**{**body.model_dump(), "ip": ip})
    session.add(c)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if (erro := _conflito_de_unicidade(e)) is not None:
            raise erro from e
        raise
    await session.refresh(c)
    return CompanyOut.model_validate(c)


@router.patch("/{company_id}", response_model=CompanyOut, dependencies=_TRAVA)
async def patch_company(
    company_id: UUID,
    body: CompanyPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "edit"))],
) -> CompanyOut:
    c = (await session.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    data = body.model_dump(exclude_unset=True)
    if "ip" in data:
        data["ip"] = _ip_ou_422(data["ip"])
        await _garante_ip_livre(session, data["ip"], exceto=c.id)
        if data["ip"] != c.ip:
            # IP novo: o erro do AdsPower era do IP antigo, e a confirmação
            # também. Zerar as duas faz o serviço do Mac pegar a empresa já na
            # próxima passada — inclusive quando alguém VOLTA ao IP anterior
            # depois de uma troca que parou no meio: sem zerar, a tela diria ✓
            # com perfis presos no IP novo.
            c.ip_adspower = None
            c.ip_adspower_erro = None
            c.ip_adspower_em = None
    for k, v in data.items():
        setattr(c, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if (erro := _conflito_de_unicidade(e)) is not None:
            raise erro from e
        raise
    await session.refresh(c)
    return CompanyOut.model_validate(c)


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_TRAVA)
async def delete_company(
    company_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("empresa", "delete"))],
) -> None:
    c = (await session.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    await session.delete(c)
    await session.commit()
    logger.info("company_deleted", id=str(company_id))
    return None
