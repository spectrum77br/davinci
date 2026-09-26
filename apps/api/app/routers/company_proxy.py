"""Proxy de cada empresa (IP + tipo/porta/usuário/senha + perfis do AdsPower).

Eduardo (26/09/2026): "colocarmos usuário e senha, um toogle bem organizado, aí
quando mudarmos o ip corrige corretamente e salva e já deixa no ar". Os proxies
novos (VPS próprias) têm usuário e senha POR IP: com eles aqui, o serviço do Mac
(apps/executor-adspower-ip) grava o proxy inteiro em todos os perfis da empresa,
testando antes. Sem eles, segue o jeito antigo (descobre a conta no AdsPower).

Só admin, e só com a tela Empresas desbloqueada (senha extra). A senha vai
cifrada para o banco e só sai por `GET .../proxy/senha` (admin) e pela ponte do
agente (`/api/agent/adspower/ip-pendentes?v=2`, protegida por token).

Salvar qualquer mudança (IP, tipo, porta, usuário, senha ou perfis extras) sobe
`proxy_rev` e zera a confirmação do AdsPower: o serviço pega a empresa na
próxima passada (até 1 minuto).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID

import structlog
from app.db import get_session
from app.deps.auth import require_admin
from app.models import Company, User
from app.routers.companies import _conflito_de_unicidade, _garante_ip_livre, _ip_ou_422
from app.security.cipher import decrypt_bytes, encrypt_bytes
from app.security.senha_extra import require_empresas_unlock
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.adspower_agent import _norm, _perfis_por_empresa

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/companies",
    tags=["company-proxy"],
    dependencies=[Depends(require_empresas_unlock)],
)

_PERFIL = re.compile(r"^\d{1,6}$")


class ProxyIn(BaseModel):
    # IP como já é gravado na coluna da tela (mesma validação, 409 se repetido).
    ip: str | None = None
    tipo: Literal["socks5", "http"] = "socks5"
    porta: int | None = Field(default=None, ge=1, le=65535)
    usuario: str | None = Field(default=None, max_length=200)
    # None = mantém a guardada; "" = apaga; texto = troca.
    senha: str | None = Field(default=None, max_length=200)
    perfis_extras: list[str] = Field(default_factory=list, max_length=40)
    # Versão que o painel abriu: se mudou nesse meio-tempo (outra pessoa, ou a
    # troca de IP na tabela), o PUT recusa em vez de desfazer a outra mudança.
    rev: int | None = None


class PerfilDaEmpresa(BaseModel):
    profile_no: str
    nome: str | None = None
    origem: Literal["loja", "extra"]
    # Também é de outra empresa: o serviço NÃO troca (mudaria o IP das duas).
    compartilhado: bool = False


class ProxyOut(BaseModel):
    ip: str | None = None
    tipo: str = "socks5"
    porta: int | None = None
    usuario: str | None = None
    tem_senha: bool = False
    perfis_extras: list[str] = []
    perfis: list[PerfilDaEmpresa] = []
    # Lojas cujo servidor não existe no AdsPower e extras que não existem lá.
    sem_perfil: list[str] = []
    ip_adspower: str | None = None
    ip_adspower_em: str | None = None
    ip_adspower_erro: str | None = None
    rev: int = 0


class SenhaOut(BaseModel):
    senha: str | None = None


def _normaliza_extras(brutos: list[str]) -> list[str]:
    vistos: list[str] = []
    for b in brutos:
        s = str(b).strip().lstrip("nN#").strip()
        if not s:
            continue
        if not _PERFIL.match(s):
            # Só o código: o texto digitado não volta na resposta.
            raise HTTPException(422, detail={"code": "perfil_invalido"})
        if s not in vistos:
            vistos.append(s)
    return vistos


async def _empresa(session: AsyncSession, company_id: UUID, *, travar: bool = False) -> Company:
    q = select(Company).where(Company.id == company_id)
    if travar:
        q = q.with_for_update()
    c = (await session.execute(q)).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    return c


async def _extras_de_outras_empresas(session: AsyncSession, c: Company, extras: list[str]) -> None:
    """Perfil extra que já é de OUTRA empresa (loja ou extra dela) é recusado:
    viraria compartilhado e as duas empresas deixariam de ser trocadas."""
    if not extras:
        return
    perfis, _empresas_do_perfil, _sem = await _perfis_por_empresa(session)
    dono = _norm(c.apelido)
    nomes = {
        _norm(a): a
        for (a,) in (await session.execute(select(Company.apelido).where(Company.id != c.id))).all()
    }
    for outro, lista in perfis.items():
        if outro == dono:
            continue
        for p in lista.values():
            if p.profile_no in extras:
                raise HTTPException(
                    409,
                    detail={
                        "code": "perfil_de_outra_empresa",
                        "perfil": p.profile_no,
                        "empresa": nomes.get(outro, outro),
                    },
                )


async def _saida(session: AsyncSession, c: Company) -> ProxyOut:
    perfis, empresas_do_perfil, sem_perfil = await _perfis_por_empresa(session)
    dono = _norm(c.apelido)
    todas = (await session.execute(select(Company.apelido))).scalars().all()
    apelido_repetido = sum(1 for a in todas if _norm(a) == dono) > 1
    lista = [
        PerfilDaEmpresa(
            profile_no=p.profile_no,
            nome=p.nome,
            origem="extra" if p.origem == "extra" else "loja",
            compartilhado=apelido_repetido or len(empresas_do_perfil.get(p.user_id, set())) > 1,
        )
        for p in perfis.get(dono, {}).values()
    ]
    lista.sort(key=lambda p: (int(p.profile_no) if p.profile_no.isdigit() else 10**9, p.profile_no))
    return ProxyOut(
        ip=c.ip,
        tipo=c.proxy_tipo or "socks5",
        porta=c.proxy_porta,
        usuario=c.proxy_usuario,
        tem_senha=c.proxy_senha_enc is not None,
        perfis_extras=list(c.adspower_perfis_extras or []),
        perfis=lista,
        sem_perfil=sem_perfil.get(dono, []),
        ip_adspower=c.ip_adspower,
        ip_adspower_em=c.ip_adspower_em.isoformat() if c.ip_adspower_em else None,
        ip_adspower_erro=c.ip_adspower_erro,
        rev=c.proxy_rev or 0,
    )


@router.get("/{company_id}/proxy", response_model=ProxyOut)
async def ler_proxy(
    company_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> ProxyOut:
    return await _saida(session, await _empresa(session, company_id))


@router.put("/{company_id}/proxy", response_model=ProxyOut)
async def gravar_proxy(
    company_id: UUID,
    body: ProxyIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> ProxyOut:
    c = await _empresa(session, company_id, travar=True)
    if body.rev is not None and body.rev != (c.proxy_rev or 0):
        raise HTTPException(409, detail={"code": "proxy_mudou_enquanto_editava"})
    ip = _ip_ou_422(body.ip)
    await _garante_ip_livre(session, ip, exceto=c.id)
    usuario = (body.usuario or "").strip() or None
    extras = _normaliza_extras(body.perfis_extras)
    await _extras_de_outras_empresas(session, c, extras)

    # Senha efetiva depois deste PUT: None = mantém; "" = apaga; texto = troca.
    # Só espaços = mantém (não apaga sem querer).
    senha_nova: str | None = None
    apagar = body.senha == ""
    if body.senha is not None and not apagar and body.senha.strip():
        senha_nova = body.senha.strip()
    tera_senha = senha_nova is not None or (c.proxy_senha_enc is not None and not apagar)
    # Tudo ou nada: o robô só usa o proxy inteiro. Meio cadastro não salva.
    partes = [body.porta is not None, usuario is not None, tera_senha]
    if any(partes) and not all(partes):
        raise HTTPException(422, detail={"code": "proxy_incompleto"})
    if all(partes) and not ip:
        raise HTTPException(422, detail={"code": "ip_obrigatorio"})

    antes = (
        c.ip,
        c.proxy_tipo or "socks5",
        c.proxy_porta,
        c.proxy_usuario,
        list(c.adspower_perfis_extras or []),
    )
    c.ip = ip
    c.proxy_tipo = body.tipo
    c.proxy_porta = body.porta
    c.proxy_usuario = usuario
    c.adspower_perfis_extras = extras
    mudou_senha = False
    if apagar:
        mudou_senha = c.proxy_senha_enc is not None
        c.proxy_senha_enc = None
    elif senha_nova is not None:
        try:
            atual = decrypt_bytes(c.proxy_senha_enc).decode() if c.proxy_senha_enc else None
        except Exception:  # noqa: BLE001 - guardada ilegível: a digitada substitui
            atual = None
        mudou_senha = atual != senha_nova
        c.proxy_senha_enc = encrypt_bytes(senha_nova.encode())
    depois = (c.ip, c.proxy_tipo, c.proxy_porta, c.proxy_usuario, list(c.adspower_perfis_extras))
    if antes != depois or mudou_senha:
        # Pendente de novo para o serviço do Mac (mesma regra da troca de IP
        # na tabela) e versão nova: resultado da versão velha não vale.
        c.proxy_rev = (c.proxy_rev or 0) + 1
        c.ip_adspower = None
        c.ip_adspower_erro = None
        c.ip_adspower_em = None
    elif c.ip and c.ip != c.ip_adspower and c.ip_adspower_erro:
        # Nada mudou, mas estava ✗: "Salvar e aplicar" = tentar de novo agora
        # (sem esperar a nova tentativa de 1 hora).
        c.ip_adspower_erro = None
        c.ip_adspower_em = None
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if (erro := _conflito_de_unicidade(e)) is not None:
            raise erro from e
        raise
    await session.refresh(c)
    logger.info(
        "company_proxy_gravado",
        company=c.apelido,
        by=str(admin.id),
        rev=c.proxy_rev,
        tem_senha=c.proxy_senha_enc is not None,
        extras=len(c.adspower_perfis_extras or []),
    )
    return await _saida(session, c)


@router.get("/{company_id}/proxy/senha", response_model=SenhaOut)
async def ver_senha_proxy(
    company_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> SenhaOut:
    c = await _empresa(session, company_id)
    if c.proxy_senha_enc is None:
        return SenhaOut(senha=None)
    try:
        senha = decrypt_bytes(c.proxy_senha_enc).decode()
    except Exception as e:  # noqa: BLE001 - blob corrompido/chave errada
        logger.error("company_proxy_senha_decrypt_failed", company=c.apelido)
        raise HTTPException(500, detail={"code": "decrypt_failed"}) from e
    logger.info("company_proxy_senha_vista", company=c.apelido, by=str(admin.id))
    return SenhaOut(senha=senha)
