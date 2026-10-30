"""Ajudante dos testes: as lojas do atendimento de uma pessoa (30/10/2026).

Desde as lojas separadas por pessoa (`services/atendimento/escopo.py`), quem não
é admin vê no atendimento e na Garantia só as lojas dele. Os testes que usam um
não-admin que lê dão a ele as lojas do cenário por aqui.
"""

from sqlalchemy import select

from app.models import AtendimentoLojaPessoa, Integration, StoreInfo
from app.services.atendimento import escopo


async def dar_lojas(db, user, *lojas) -> None:
    """Dá à pessoa as lojas — a chave pronta ("i:"/"f:"/"c:") ou a integração
    (o objeto ou o id). A que ela já tem fica."""
    ja = await escopo.chaves_da_pessoa(db, user.id)
    for loja in lojas:
        if isinstance(loja, str):
            chave = loja
        else:
            chave = escopo.chave(escopo.INTEGRACAO, getattr(loja, "id", loja))
        if chave not in ja:
            db.add(AtendimentoLojaPessoa(user_id=user.id, chave=chave))
            ja.add(chave)
    await db.commit()


async def dar_todas_as_lojas(db, user) -> None:
    """Todas as integrações e fichas que existem agora (o leitor de antes, que via tudo)."""
    integracoes = (await db.execute(select(Integration.id))).scalars().all()
    fichas = (await db.execute(select(StoreInfo.id))).scalars().all()
    await dar_lojas(db, user, *integracoes, *(escopo.chave(escopo.FICHA, f) for f in fichas))
