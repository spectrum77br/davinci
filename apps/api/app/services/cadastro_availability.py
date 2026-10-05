"""Disponibilidade de recursos compartilhada pelo seletor e pela criação de contas."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Cadastro,
    CadastroStatus,
    CadastroStore,
    CadastroTipo,
    Company,
    Marketplace,
    Store,
    StoreInfo,
)

_STOREINFO_FIELD_FOR_TIPO = {
    CadastroTipo.FONE: StoreInfo.phone,
    CadastroTipo.EMAIL: StoreInfo.email,
    CadastroTipo.SERVIDOR: StoreInfo.server,
}

# Tipos cuja ocupação vale pra TODOS os marketplaces (Eduardo 17/09): um servidor
# é um perfil do AdsPower, ou seja UM navegador logado. Se ele já está numa conta
# de Mercado Livre, não pode aparecer como livre na Shopee — nem pra mesma
# empresa. Telefone e e-mail continuam reserváveis por marketplace (a mesma linha
# atende contas de plataformas diferentes).
_TIPOS_EXCLUSIVOS_GLOBAIS = frozenset({CadastroTipo.SERVIDOR})


def ocupacao_global(tipo: CadastroTipo) -> bool:
    """`True` quando o recurso, uma vez usado, some da lista de todo marketplace."""
    return tipo in _TIPOS_EXCLUSIVOS_GLOBAIS


def normalize_cadastro_code(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_platform(value: str | None) -> str:
    platform = normalize_cadastro_code(value)
    return "ml" if platform in {"mercadolivre", "mercado livre"} else platform


async def available_cadastros(
    session: AsyncSession, tipo: CadastroTipo, marketplace: Marketplace
) -> list[Cadastro]:
    """Retorna recursos ativos e livres pro marketplace escolhido.

    Um vínculo importado ainda não resolvido também reserva o recurso. A
    ocupação é comparada por tipo e código, para que outro cadastro com o mesmo
    código não permita reutilizar um telefone, e-mail ou perfil já vinculado.

    Telefone e e-mail são reservados POR marketplace (a mesma linha atende contas
    de plataformas diferentes); servidor é reservado em TODOS eles, porque é um
    perfil do AdsPower — ver `ocupacao_global`.
    """
    global_ = ocupacao_global(tipo)
    cadastros = (
        (
            await session.execute(
                select(Cadastro).where(Cadastro.tipo == tipo).order_by(Cadastro.codigo, Cadastro.id)
            )
        )
        .scalars()
        .all()
    )
    busy_codes = {
        normalize_cadastro_code(cadastro.codigo)
        for cadastro in cadastros
        if any(
            (global_ or _normalize_platform(platform) == marketplace.value)
            and (bool(value.strip()) if isinstance(value, str) else bool(value))
            for platform, value in (cadastro.raw_links or {}).items()
        )
    }

    linked_stmt = (
        select(Cadastro.codigo)
        .join(CadastroStore, CadastroStore.cadastro_id == Cadastro.id)
        .join(Store, Store.id == CadastroStore.store_id)
        .where(Cadastro.tipo == tipo)
    )
    if not global_:
        linked_stmt = linked_stmt.where(Store.marketplace == marketplace)
    linked_codes = (await session.execute(linked_stmt)).scalars().all()
    busy_codes.update(normalize_cadastro_code(code) for code in linked_codes)

    field = _STOREINFO_FIELD_FOR_TIPO.get(tipo)
    if field is not None:
        store_info_rows = (
            await session.execute(select(StoreInfo.platform, field).where(field.isnot(None)))
        ).all()
        busy_codes.update(
            normalize_cadastro_code(code)
            for platform, code in store_info_rows
            if global_ or _normalize_platform(platform) == marketplace.value
        )

    return [
        cadastro
        for cadastro in cadastros
        if cadastro.status == CadastroStatus.ACTIVE
        and (code := normalize_cadastro_code(cadastro.codigo))
        and code not in busy_codes
    ]


def chave_conta(nome: str | None) -> str:
    """Nome de conta sem espaços e minúsculo ("dream 2" == "dream2") — mesmo
    normalizador da matriz de Empresas e da criação de conta."""
    return "".join((nome or "").split()).lower()


async def quem_usa(
    session: AsyncSession,
    tipo: CadastroTipo,
    marketplace: Marketplace,
    codigo: str,
    *,
    conta: str,
    store_id=None,
) -> str | None:
    """Nome da OUTRA conta que já usa `codigo` neste marketplace, ou None.

    Mesmas fontes de ocupação de `available_cadastros` (vínculo importado,
    vínculo em Cadastros e dados da loja), mas ignorando o que pertence à
    própria conta — trocar o e-mail de uma conta não pode esbarrar nela mesma.
    Cadastro desativado/excluído não entra aqui: quem chama decide (a troca de
    e-mail responde `email_desativado`).
    """
    global_ = ocupacao_global(tipo)
    alvo = normalize_cadastro_code(codigo)
    minha = chave_conta(conta)

    field = _STOREINFO_FIELD_FOR_TIPO.get(tipo)
    if field is not None:
        for platform, nome, valor in (
            await session.execute(
                select(StoreInfo.platform, StoreInfo.account_name, field).where(field.isnot(None))
            )
        ).all():
            if normalize_cadastro_code(valor) != alvo or chave_conta(nome) == minha:
                continue
            if global_ or _normalize_platform(platform) == marketplace.value:
                return nome or "outra conta"

    cadastros = (
        (await session.execute(select(Cadastro).where(Cadastro.tipo == tipo))).scalars().all()
    )
    mesmos = [c for c in cadastros if normalize_cadastro_code(c.codigo) == alvo]
    for cadastro in mesmos:
        for platform, valor in (cadastro.raw_links or {}).items():
            if not (isinstance(valor, str) and valor.strip()) or chave_conta(valor) == minha:
                continue
            if global_ or _normalize_platform(platform) == marketplace.value:
                return valor.strip()

    if mesmos:
        stmt = (
            select(Company.apelido, Store.apelido_override)
            .select_from(CadastroStore)
            .join(Store, Store.id == CadastroStore.store_id)
            .join(Company, Company.id == Store.company_id, isouter=True)
            .where(CadastroStore.cadastro_id.in_([c.id for c in mesmos]))
        )
        if store_id is not None:
            stmt = stmt.where(Store.id != store_id)
        if not global_:
            stmt = stmt.where(Store.marketplace == marketplace)
        for apelido, override in (await session.execute(stmt)).all():
            nome = apelido or override
            if chave_conta(nome) != minha:
                return nome or "outra conta"
    return None
