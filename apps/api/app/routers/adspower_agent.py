"""Ponte DaVinci → AdsPower para o IP de cada empresa.

Eduardo (25/09/2026): "quando colocarmos um ip novo para a empresa, já vai
diretamente para o ads power". A API do AdsPower só responde na máquina onde
ele roda (o Mac), então o servidor não fala com ele: um serviço no Mac busca
aqui o que está pendente, aplica nos perfis e devolve o resultado.

  GET  /api/agent/adspower/ip-pendentes   empresas cujo IP ainda não foi
                                          confirmado no AdsPower, com os perfis
  POST /api/agent/adspower/ip-resultado   o que o serviço conseguiu fazer

Versão 2 (26/09/2026, `?v=2`): a empresa pode ter o proxy inteiro cadastrado
(tipo, porta, usuário e senha — os proxies novos têm senha por IP) e perfis
"extras" que nenhuma loja aponta. O serviço novo recebe essas empresas com o
proxy e grava tudo; o serviço antigo (sem `v`) NÃO recebe empresa com proxy
cadastrado — ele tentaria descobrir a conta e marcaria ✗ por cima do novo.

Protegido pelo X-Agent-Token (`adspower_agent_token`); vazio = fechado.

Proteção que mora AQUI e não no Mac: perfil que atende lojas de MAIS DE UMA
empresa não é entregue para ser alterado. Trocar o proxy dele mudaria o IP das
duas empresas de uma vez — é justamente o compartilhamento que a coluna de IP
existe para acabar.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from app.config import get_settings
from app.db import get_session
from app.models import Company, StoreInfo
from app.security.cipher import decrypt_bytes
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

router = APIRouter(prefix="/api/agent/adspower", tags=["adspower-agent"])

_ESPERA_DEPOIS_DE_ERRO = timedelta(hours=1)
# Espelho do AdsPower: tabela sem modelo ORM, alimentada pelo adspower_sync.
_ESPELHO = f"{get_settings().database_schema}.adspower"


async def _require_adspower_agent_token(
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
) -> None:
    esperado = get_settings().adspower_agent_token
    # Em bytes: com um caractere não-ASCII no cabeçalho, compare_digest de dois
    # textos levanta TypeError e a resposta vira 500 em vez de 401.
    if (
        not esperado
        or not x_agent_token
        or not secrets.compare_digest(
            x_agent_token.encode("utf-8", "surrogateescape"), esperado.encode("utf-8")
        )
    ):
        raise HTTPException(401, detail={"code": "adspower_agent_unauthorized"})


def _ordem(p: "PerfilAdspower") -> tuple[int, str]:
    # Pelo número do perfil, não pelo texto ("84" antes de "119").
    return (int(p.profile_no), "") if p.profile_no.isdigit() else (10**9, p.profile_no)


def _norm(s: str | None) -> str:
    # Mesmo critério da tabela de Empresas (companies._norm_conta): a loja é
    # da empresa cujo apelido bate com o nome da conta, sem espaço e minúsculo.
    return "".join((s or "").split()).lower()


class PerfilAdspower(BaseModel):
    user_id: str
    profile_no: str
    nome: str | None = None
    # "loja" = algum servidor de loja aponta; "extra" = cadastrado na empresa.
    origem: str = "loja"


class ProxyAgente(BaseModel):
    """Proxy inteiro da empresa, só na versão 2 e só por esta rota com token."""

    tipo: str = "socks5"
    porta: int
    usuario: str
    senha: str


def tem_proxy_cadastrado(c: Company) -> bool:
    return bool(c.proxy_usuario and c.proxy_senha_enc is not None and c.proxy_porta)


def e_do_servico_novo(c: Company) -> bool:
    """Empresa que só o serviço v2 pode aplicar: tem proxy cadastrado ou perfis
    extras (o serviço antigo não conhece nenhum dos dois e marcaria ✓/✗ por
    cima de uma versão que não aplicou)."""
    return tem_proxy_cadastrado(c) or bool(c.adspower_perfis_extras)


class PendenciaIp(BaseModel):
    company_id: UUID
    apelido: str
    ip: str
    perfis: list[PerfilAdspower]
    # Perfis da empresa que também atendem outra empresa: NÃO devem ser
    # alterados. Vêm à parte só para o serviço reportar por que não aplicou.
    compartilhados: list[PerfilAdspower] = []
    # Lojas da empresa cujo servidor não existe no espelho do AdsPower.
    sem_perfil: list[str] = []
    # Versão do proxy (companies.proxy_rev): volta no resultado.
    rev: int = 0
    proxy: ProxyAgente | None = None
    # IPs de TODAS as outras empresas: o serviço não grava num perfil que hoje
    # está no IP de outra empresa (perfil extra digitado errado, por exemplo).
    ips_de_outras: list[str] = []


class ResultadoIp(BaseModel):
    company_id: UUID
    ip: str
    ok: bool
    # Texto curto e SEM credencial — o serviço do Mac nunca manda usuário nem
    # senha de proxy aqui. O limite impede que um log inteiro caia no banco.
    erro: str | None = Field(default=None, max_length=500)
    # Versão do proxy que o serviço aplicou (só o serviço novo manda).
    rev: int | None = None


async def _perfis_por_empresa(
    session: AsyncSession,
) -> tuple[dict[str, dict[str, PerfilAdspower]], dict[str, set[str]], dict[str, list[str]]]:
    """(perfis por apelido normalizado, empresas por perfil, lojas sem perfil)."""
    lojas = (
        await session.execute(
            select(StoreInfo.platform, StoreInfo.account_name, StoreInfo.server).where(
                StoreInfo.server.is_not(None),
                # Loja arquivada não conta: com servidor morto ela prenderia a
                # empresa num ✗ para sempre, e com perfil dividido travaria o
                # perfil de outra empresa.
                StoreInfo.archived_at.is_(None),
            )
        )
    ).all()
    # O espelho do AdsPower é uma tabela sem modelo ORM (vem do adspower_sync).
    espelho = {
        str(no).strip(): (uid, nome)
        for uid, no, nome in (
            # _ESPELHO vem da configuração, não de entrada de usuário.
            await session.execute(text(f"SELECT id, profile_no, name FROM {_ESPELHO}"))  # noqa: S608
        ).all()
        if no is not None
    }
    perfis: dict[str, dict[str, PerfilAdspower]] = {}
    empresas_do_perfil: dict[str, set[str]] = {}
    sem_perfil: dict[str, list[str]] = {}
    for plataforma, conta, servidor in lojas:
        dono = _norm(conta)
        no = (servidor or "").strip()
        if not dono or not no:
            continue
        achado = espelho.get(no)
        if achado is None:
            sem_perfil.setdefault(dono, []).append(f"{plataforma}/{conta} no servidor {no}")
            continue
        uid, nome = achado
        perfis.setdefault(dono, {})[uid] = PerfilAdspower(user_id=uid, profile_no=no, nome=nome)
        empresas_do_perfil.setdefault(uid, set()).add(dono)
    # Perfis extras cadastrados na empresa (os que nenhuma loja aponta, ex.: os
    # de Contabilidade/Financeiro 2). Entram na mesma conta de "de quem é o
    # perfil": extra de duas empresas também é compartilhado.
    extras = (
        await session.execute(
            select(Company.apelido, Company.adspower_perfis_extras).where(
                func.cardinality(Company.adspower_perfis_extras) > 0
            )
        )
    ).all()
    for apelido, lista in extras:
        dono = _norm(apelido)
        for no in lista or []:
            no = str(no).strip()
            achado = espelho.get(no)
            if achado is None:
                sem_perfil.setdefault(dono, []).append(f"perfil extra {no} não existe no AdsPower")
                continue
            uid, nome = achado
            if uid not in perfis.setdefault(dono, {}):
                perfis[dono][uid] = PerfilAdspower(
                    user_id=uid, profile_no=no, nome=nome, origem="extra"
                )
            empresas_do_perfil.setdefault(uid, set()).add(dono)
    return perfis, empresas_do_perfil, sem_perfil


@router.get(
    "/ip-pendentes",
    response_model=list[PendenciaIp],
    dependencies=[Depends(_require_adspower_agent_token)],
)
async def ip_pendentes(
    session: Annotated[AsyncSession, Depends(get_session)],
    v: Annotated[int, Query(ge=1, le=9)] = 1,
) -> list[PendenciaIp]:
    empresas = (
        (
            await session.execute(
                select(Company).where(
                    Company.ip.is_not(None),
                    Company.ip.is_distinct_from(Company.ip_adspower),
                    # Quem falhou volta de hora em hora, não a cada minuto: o
                    # motivo costuma ser algo que só uma pessoa resolve. Trocar
                    # o IP na tela zera o erro e ela volta na hora.
                    or_(
                        Company.ip_adspower_erro.is_(None),
                        Company.ip_adspower_em.is_(None),
                        Company.ip_adspower_em < datetime.now(UTC) - _ESPERA_DEPOIS_DE_ERRO,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    if v < 2:
        # Serviço antigo: empresa com proxy cadastrado ou extras é só do novo.
        empresas = [c for c in empresas if not e_do_servico_novo(c)]
    if not empresas:
        return []
    perfis, empresas_do_perfil, sem_perfil = await _perfis_por_empresa(session)
    # A loja é ligada à empresa pelo apelido normalizado. Duas empresas com o
    # mesmo apelido normalizado ("dream 2" e "Dream 2") pegariam os perfis uma
    # da outra como se fossem só seus — então nesse caso nenhum é exclusivo.
    todas_linhas = (
        await session.execute(select(Company.id, Company.apelido, Company.ip, Company.ip_adspower))
    ).all()
    todas = [a for _i, a, _ip, _ipa in todas_linhas]
    quantas_por_dono: dict[str, int] = {}
    for apelido in todas:
        quantas_por_dono[_norm(apelido)] = quantas_por_dono.get(_norm(apelido), 0) + 1
    saida: list[PendenciaIp] = []
    for c in empresas:
        dono = _norm(c.apelido)
        apelido_repetido = quantas_por_dono.get(dono, 0) > 1
        livres, compartilhados = [], []
        for p in perfis.get(dono, {}).values():
            atende_outras = apelido_repetido or len(empresas_do_perfil.get(p.user_id, set())) > 1
            (compartilhados if atende_outras else livres).append(p)
        proxy = None
        if tem_proxy_cadastrado(c):
            try:
                senha = decrypt_bytes(c.proxy_senha_enc).decode()
            except Exception:  # noqa: BLE001 - blob ilegível: não entrega meio proxy
                logger.error("adspower_proxy_senha_ilegivel", company=c.apelido)
                # ✗ com o motivo na tela, em vez de ⏳ para sempre culpando o Mac.
                c.ip_adspower_erro = (
                    "a senha do proxy guardada não pôde ser lida — "
                    "cadastre de novo no painel do proxy"
                )
                c.ip_adspower_em = datetime.now(UTC)
                await session.commit()
                continue
            proxy = ProxyAgente(
                tipo=c.proxy_tipo or "socks5",
                porta=c.proxy_porta,
                usuario=c.proxy_usuario,
                senha=senha,
            )
        saida.append(
            PendenciaIp(
                company_id=c.id,
                apelido=c.apelido,
                ip=c.ip,
                perfis=sorted(livres, key=_ordem),
                compartilhados=sorted(compartilhados, key=_ordem),
                sem_perfil=sem_perfil.get(dono, []),
                rev=c.proxy_rev or 0,
                proxy=proxy,
                ips_de_outras=sorted(
                    {x for i, _a, ip, ipa in todas_linhas if i != c.id for x in (ip, ipa) if x}
                )
                if v >= 2
                else [],
            )
        )
    return saida


@router.post(
    "/ip-resultado",
    dependencies=[Depends(_require_adspower_agent_token)],
)
async def ip_resultado(
    body: ResultadoIp,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    # Trava a linha: um PUT do painel no mesmo instante não pode ser apagado
    # por este resultado (nem o contrário).
    c = (
        await session.execute(
            select(Company).where(Company.id == body.company_id).with_for_update()
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "company_not_found"})
    # Alguém trocou o IP enquanto o serviço aplicava o anterior: esse
    # resultado é de um IP que não vale mais e não pode marcar o novo como
    # aplicado. O novo volta como pendente na próxima passada.
    if (c.ip or "") != body.ip:
        logger.info("adspower_ip_resultado_obsoleto", company=c.apelido)
        return {"registrado": False, "motivo": "ip_mudou"}
    # Proxy mudou (usuário, senha, porta, perfis) enquanto o serviço aplicava a
    # versão anterior — ou o resultado veio do serviço antigo para uma empresa
    # que agora tem proxy cadastrado: não vale para a versão atual.
    if (body.rev is not None or e_do_servico_novo(c)) and body.rev != (c.proxy_rev or 0):
        logger.info("adspower_ip_resultado_obsoleto", company=c.apelido, motivo="rev")
        return {"registrado": False, "motivo": "proxy_mudou"}
    agora = datetime.now(UTC)
    if body.ok:
        c.ip_adspower = body.ip
        c.ip_adspower_erro = None
    else:
        c.ip_adspower_erro = (body.erro or "falhou sem motivo").strip()[:500]
    c.ip_adspower_em = agora
    await session.commit()
    logger.info("adspower_ip_resultado", company=c.apelido, ok=body.ok)
    return {"registrado": True}
