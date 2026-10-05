"""O nome da LOJA por trás de uma integração, para a caixa de atendimento (28/09/2026).

A caixa mostrava o nome da INTEGRAÇÃO ("mega"), que é um apelido técnico de
quem conectou a conta. A equipe conhece a loja pelo nome do cadastro — e é o
que o Duoke mostra: a integração "mega" é a loja "Shopee Marquezini", e o
Duoke diz "Marquezini". Com o nome errado na barra de lojas, na lista e no
alerta, a pessoa responde achando que está numa loja e está em outra.

De onde vem o nome, em ordem:

  0. a conta do cadastro de Lojas (`store_info.account_name`) LIGADA à
     integração (`store_info.integration_id`), só quando ela tem OUTRO nome
     que o da integração (ver abaixo);
  1. `stores.apelido_override` da loja ligada à integração;
  2. `companies.apelido` da empresa dessa loja;
  3. sem loja ligada, o nome da própria integração.

O passo 0 (05/10/2026): em 23/09 as lojas Shopee "jlas" e "kia" foram
renomeadas no cadastro de Lojas para "atlas" e "fiore" (o nome da loja na
Shopee e no Bling), mas a integração e a Empresas continuaram com o nome do
dono ("Jlas", "Kia") — e a caixa mostrava "Jlas" e "Kia". Quando o cadastro de
Lojas está ligado à integração com outro nome, esse é o nome da loja. Com o
MESMO nome da integração (o caso de todas as outras: a ficha "kia" do ML na
integração "kia"), nada muda — vale a Empresas, com a grafia dela ("Kia",
"Marquezini"). Ficha arquivada, de outra plataforma ou duas fichas com nomes
diferentes na mesma integração não contam (na dúvida, fica o nome de antes).
Nome todo em minúsculas ganha a inicial maiúscula ("atlas" → "Atlas"), como os
outros da barra.

A loja se liga à integração de DOIS jeitos no banco (os dois existem em
produção, de épocas diferentes do cadastro): `stores.integration_id =
integrations.id` OU `integrations.store_id = stores.id`. Quando os dois
apontam para lojas diferentes, vale o da loja (`stores.integration_id`) — é o
que a tela de Empresas edita.

O prefixo da plataforma sai do nome da loja ("Shopee Marquezini" →
"Marquezini", "Amazon KFA" → "KFA", "Mercado Livre X"/"ML X" → "X"): a tela já
mostra o ícone da plataforma ao lado, e o Duoke também não repete. O nome da
integração (o último recurso) fica como está — é o que a equipe digitou.

Cache POR RODADA: a leitura de um canal chama isto a cada conversa; o nome
fica em `session.info` (uma sessão por canal por rodada no sync, uma por
pedido na API), então o cadastro editado vale na rodada seguinte sem nada
para invalidar.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, Integration, Store, StoreInfo

# Onde o cache mora dentro de `session.info` (dicionário livre da sessão).
_CHAVE_CACHE = "atendimento_nome_da_loja"

# O cadastro de Lojas escreve a plataforma do ML de mais de um jeito.
_PLATAFORMA_DA_FICHA = {
    "mercadolivre": "ml",
    "mercado_livre": "ml",
    "mercado livre": "ml",
    "meli": "ml",
}

# Prefixos que o cadastro repete no nome da loja, por plataforma. Só os da
# PRÓPRIA plataforma saem: uma Shopee cadastrada como "Amazon X" é erro de
# cadastro, e esconder o "Amazon" esconderia o erro. O mais longo primeiro
# ("tiktok shop" antes de "tiktok").
_PREFIXOS: dict[str, tuple[str, ...]] = {
    "shopee": ("shopee",),
    "ml": ("mercado livre", "mercadolivre", "meli", "ml"),
    "amazon": ("amazon",),
    "tiktok": ("tiktok shop", "tik tok shop", "tiktok", "tik tok", "tts"),
    "magalu": ("magazine luiza", "magalu"),
    "shein": ("shein",),
    "temu": ("temu",),
}
# Depois do prefixo: espaço, ou um separador com ou sem espaço ("Shopee - X",
# "Shopee: X", "Shopee | X"). Sem separador não é prefixo: "Shopeezinha" fica.
_SEPARADOR = r"(?:\s*[-–—:|/·]\s*|\s+)"


def _plataforma(integration: Integration) -> str:
    """O valor da plataforma da integração (`IntegrationPlatform` ou texto)."""
    valor: Any = getattr(integration, "platform", None)
    return str(getattr(valor, "value", valor) or "").lower()


def sem_prefixo(nome: str, plataforma: str) -> str:
    """O nome sem o prefixo da plataforma na frente; se sobrar nada, o nome inteiro.

    "Shopee Marquezini" → "Marquezini"; "Mercado Livre - KFA" → "KFA";
    "Shopee" → "Shopee" (o nome não pode sumir).
    """
    limpo = " ".join((nome or "").split())
    for prefixo in _PREFIXOS.get(plataforma, ()):
        m = re.match(rf"{re.escape(prefixo)}{_SEPARADOR}(?P<resto>.+)$", limpo, re.IGNORECASE)
        if m and m.group("resto").strip():
            return m.group("resto").strip()
    return limpo


def _cache(session: AsyncSession) -> dict[str, str]:
    return session.info.setdefault(_CHAVE_CACHE, {})


def esquecer(session: AsyncSession) -> None:
    """Esvazia o cache desta sessão (quem acabou de editar o cadastro na mesma sessão)."""
    session.info.pop(_CHAVE_CACHE, None)


async def _nome_do_cadastro(session: AsyncSession, integration: Integration) -> str | None:
    """`apelido_override` da loja ligada, senão o `apelido` da empresa; None sem loja."""
    ligacoes = [Store.integration_id == integration.id]
    if integration.store_id is not None:
        ligacoes.append(Store.id == integration.store_id)
    linha = (
        await session.execute(
            select(Store.apelido_override, Company.apelido)
            .join(Company, Company.id == Store.company_id)
            .where(or_(*ligacoes))
            # Os dois vínculos em lojas diferentes: vale o que a loja diz.
            .order_by(case((Store.integration_id == integration.id, 0), else_=1))
            .limit(1)
        )
    ).first()
    if linha is None:
        return None
    override, apelido = linha
    for candidato in (override, apelido):
        if isinstance(candidato, str) and candidato.strip():
            return candidato
    return None


def _chave(nome: str | None) -> str:
    """O nome para comparar: sem espaço nenhum e minúsculo (" Kia" = "kia")."""
    return "".join((nome or "").split()).lower()


def _com_inicial_maiuscula(nome: str) -> str:
    """Nome todo minúsculo ganha inicial maiúscula ("atlas" → "Atlas"); o resto fica."""
    if nome != nome.lower():
        return nome
    return " ".join(p[:1].upper() + p[1:] for p in nome.split())


async def _nome_da_ficha(
    session: AsyncSession, integration: Integration, plataforma: str
) -> str | None:
    """A conta do cadastro de Lojas ligada à integração, quando é OUTRO nome; senão None.

    Só a ficha ativa (`archived_at` nulo) da mesma plataforma; duas fichas
    ligadas com nomes diferentes = dúvida = None. O nome igual ao da
    integração também é None: aí o cadastro de Lojas não diz nada novo.
    """
    linhas = (
        await session.execute(
            select(StoreInfo.account_name, StoreInfo.platform)
            .where(
                StoreInfo.integration_id == integration.id,
                StoreInfo.archived_at.is_(None),
            )
            # A mesma conta escrita de dois jeitos: vale a grafia da mais antiga.
            .order_by(StoreInfo.created_at, StoreInfo.id)
        )
    ).all()
    nomes: dict[str, str] = {}
    for conta, plataforma_ficha in linhas:
        plat = " ".join((plataforma_ficha or "").split()).lower()
        if _PLATAFORMA_DA_FICHA.get(plat, plat) != plataforma:
            continue
        limpo = sem_prefixo(conta or "", plataforma)
        if _chave(limpo):
            nomes.setdefault(_chave(limpo), limpo)
    if len(nomes) != 1:
        return None
    chave, nome = next(iter(nomes.items()))
    if chave == _chave(integration.name):
        return None
    return _com_inicial_maiuscula(nome)


async def nome_da_loja(session: AsyncSession, integration: Integration | None) -> str:
    """O nome da loja para a caixa (ver o docstring do módulo); "" sem integração.

    Nunca vazio quando há integração com nome. Duas consultas por integração
    por sessão (Empresas e Lojas; cache em `session.info`); não escreve nada.
    """
    if integration is None:
        return ""
    chave = str(integration.id)
    cache = _cache(session)
    if chave in cache:
        return cache[chave]
    plataforma = _plataforma(integration)
    do_cadastro = await _nome_do_cadastro(session, integration)
    if do_cadastro is not None:
        nome = sem_prefixo(do_cadastro, plataforma)
    else:
        nome = " ".join((integration.name or "").split())
    # O cadastro de Lojas ligado com OUTRO nome (Jlas → Atlas). Se a Empresas
    # já diz o mesmo nome, fica a grafia dela.
    da_ficha = await _nome_da_ficha(session, integration, plataforma)
    if da_ficha is not None and _chave(da_ficha) != _chave(nome):
        nome = da_ficha
    cache[chave] = nome
    return nome
