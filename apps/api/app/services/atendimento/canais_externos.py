"""Canal EXTERNO do atendimento: sites e redes sociais (02/10/2026, migration 0362).

Até aqui todo canal tinha por onde ler: a integração de marketplace (API da
loja) ou o robô do Mac mini (perfil do AdsPower). O carrinho abandonado dos
sites (RF9) e os comentários das redes (RF7) não têm nenhum dos dois — o
canal é identificado por uma REFERÊNCIA externa em
`atendimento_canais.externo_ref`:

  site:<site>                      — "site:charlots", "site:uranyx"
  rede:instagram:<ig_user_id>      — a conta profissional do Instagram
  rede:facebook:<page_id>          — a Página do Facebook

O nome que a tela mostra ("Charlots", "@charlots_br") fica em
`cursor["externo"]["nome"]` (como o robô guarda o da loja em
`cursor["robo"]`); a conta do cadastro Redes Sociais por trás do canal de
rede, em `rede_social_id` — é por ela que a barra de lojas junta o Direct e
os comentários da mesma conta.

O cron do sync nunca vê estes canais (ele junta com `integrations`); quem os
cria é o leitor de cada um (`carrinhos.py`, `redes.py`), sempre por
`garantir_canal` — nasce em `observar`, como toda loja.

Nada aqui commita (só flush) e nada sai para fora.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoCanal
from app.services.atendimento.constantes import (
    CANAIS_EXTERNOS,
    MODO_OBSERVAR,
    MODOS_EXTERNOS,
    NOME_SITE,
    PLATAFORMA_SITE,
    PLATAFORMAS_REDE,
    SITES,
)

PREFIXO_SITE = "site:"
PREFIXO_REDE = "rede:"
# Onde o nome e o resto do que o leitor quer guardar moram no `cursor`.
CHAVE_CURSOR = "externo"
_NOME_MAX = 120


class ReferenciaInvalida(ValueError):  # noqa: N818 — nome do domínio, como FotoInvalida
    """A referência externa não tem o formato do contrato (erro de programa, não de dado)."""


def ref_do_site(site: str) -> str:
    """'charlots' → 'site:charlots'. Site fora de `constantes.SITES` → `ReferenciaInvalida`."""
    nome = (site or "").strip().lower()
    if nome not in SITES:
        raise ReferenciaInvalida(f"site desconhecido: {nome[:32]}")
    return f"{PREFIXO_SITE}{nome}"


def ref_da_rede(plataforma: str, conta_id: str | int) -> str:
    """('instagram', '1784…') → 'rede:instagram:1784…' (o id da CONTA da marca na rede)."""
    plat = (plataforma or "").strip().lower()
    conta = str(conta_id or "").strip()
    if plat not in PLATAFORMAS_REDE or not conta or ":" in conta:
        raise ReferenciaInvalida(f"conta de rede inválida: {plat[:16]}")
    return f"{PREFIXO_REDE}{plat}:{conta}"


def partes(ref: str | None) -> tuple[str, str, str] | None:
    """A referência em (tipo, plataforma, id): ('site', 'site', 'charlots'),
    ('rede', 'instagram', '1784…'). Formato estranho → None.
    """
    texto = (ref or "").strip()
    if texto.startswith(PREFIXO_SITE):
        site = texto[len(PREFIXO_SITE) :]
        return ("site", PLATAFORMA_SITE, site) if site else None
    if texto.startswith(PREFIXO_REDE):
        plat, _, conta = texto[len(PREFIXO_REDE) :].partition(":")
        if plat in PLATAFORMAS_REDE and conta:
            return ("rede", plat, conta)
    return None


def site_do_canal(canal: AtendimentoCanal | None) -> str | None:
    """O site ('charlots') do canal de site; None para os outros."""
    p = partes(canal.externo_ref) if canal is not None else None
    return p[2] if p and p[0] == "site" else None


def eh_externo(canal: AtendimentoCanal | None) -> bool:
    """Canal de site ou de rede (sem integração e sem robô)."""
    return (
        canal is not None
        and canal.integration_id is None
        and canal.robo_perfil_id is None
        and bool(canal.externo_ref)
    )


def dados_externo(canal: AtendimentoCanal) -> dict:
    bruto = (canal.cursor or {}).get(CHAVE_CURSOR)
    return dict(bruto) if isinstance(bruto, dict) else {}


def nome_do_canal(canal: AtendimentoCanal) -> str:
    """O nome da 'loja' na barra: o gravado pelo leitor; sem ele, o do site ou a referência."""
    nome = str(dados_externo(canal).get("nome") or "").strip()
    if nome:
        return nome
    p = partes(canal.externo_ref)
    if p and p[0] == "site":
        return NOME_SITE.get(p[2], p[2].capitalize())
    return canal.externo_ref or "—"


def descricao_da_origem(canal: AtendimentoCanal) -> str:
    """O texto de "por onde é lido" (o `integracao` da aba Lojas e o title da barra)."""
    p = partes(canal.externo_ref)
    if p and p[0] == "site":
        return f"site {NOME_SITE.get(p[2], p[2])} · leitura servidor a servidor"
    if p and p[0] == "rede":
        rede = "Instagram" if p[1] == "instagram" else "Facebook"
        return f"{rede} · token do DaVinci Publicador"
    return "origem externa"


def com_externo(canal: AtendimentoCanal, **campos: Any) -> None:
    """Mescla campos em `cursor["externo"]` (dicionário NOVO: o JSONB muda de verdade)."""
    canal.cursor = {**(canal.cursor or {}), CHAVE_CURSOR: {**dados_externo(canal), **campos}}


async def garantir_canal(
    session: AsyncSession,
    *,
    externo_ref: str,
    plataforma: str,
    canal: str,
    nome: str | None = None,
    rede_social_id: UUID | None = None,
) -> AtendimentoCanal:
    """Acha ou cria o canal externo (nasce em `observar`, status `novo`). Não commita.

    `ON CONFLICT DO NOTHING` no UNIQUE (externo_ref, canal): duas rodadas ao
    mesmo tempo não brigam. A plataforma e a caixa têm de bater com a
    referência e com `CANAIS_EXTERNOS` (erro de programa → `ReferenciaInvalida`).
    O nome e a conta do cadastro são atualizados quando vierem diferentes.
    """
    p = partes(externo_ref)
    if p is None or p[1] != plataforma:
        raise ReferenciaInvalida(f"referência e plataforma não batem: {plataforma[:16]}")
    if canal not in CANAIS_EXTERNOS.get(plataforma, ()):
        raise ReferenciaInvalida(f"caixa {canal[:16]} não existe em {plataforma[:16]}")
    nome_limpo = " ".join((nome or "").split())[:_NOME_MAX] or None
    await session.execute(
        pg_insert(AtendimentoCanal)
        .values(
            id=uuid4(),
            integration_id=None,
            robo_perfil_id=None,
            externo_ref=externo_ref,
            rede_social_id=rede_social_id,
            plataforma=plataforma,
            canal=canal,
            modo=MODO_OBSERVAR,
            status="novo",
            cursor={CHAVE_CURSOR: {"nome": nome_limpo}} if nome_limpo else {},
            auto_categorias=[],
        )
        .on_conflict_do_nothing(index_elements=["externo_ref", "canal"])
    )
    achado = (
        await session.execute(
            select(AtendimentoCanal)
            .where(AtendimentoCanal.externo_ref == externo_ref, AtendimentoCanal.canal == canal)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if nome_limpo and dados_externo(achado).get("nome") != nome_limpo:
        com_externo(achado, nome=nome_limpo)
    if rede_social_id is not None and achado.rede_social_id != rede_social_id:
        achado.rede_social_id = rede_social_id
    await session.flush()
    return achado


def modo_permitido(plataforma: str | None, modo: str | None) -> bool:
    """O modo que a aba Lojas aceita no canal externo (`constantes.MODOS_EXTERNOS`)."""
    return modo in MODOS_EXTERNOS.get(plataforma or "", ())
