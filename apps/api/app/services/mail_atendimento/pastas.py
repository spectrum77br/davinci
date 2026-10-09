"""As pastas de cada caixa com ponte (`mail_folders`).

Quem conta a pasta de um e-mail é o AGENTE, dentro do conteúdo cifrado da
Central: o v1 (IMAP, o agente do outro dev) manda só o nome em `folder`
("INBOX", "INBOX/vendas ml"); o conector v2 (etapa C) manda também o bloco
`tuta` (`folder_key`, `folder_kind`, `folder_path`). A ponte, ao ver um
e-mail que PASSOU no filtro da caixa (crítica de 08/10: a pasta de um e-mail
privado nunca vira linha em claro), garante a linha da pasta aqui — nova →
classificada pela regra; já existente → mantém o que uma pessoa escolheu.

Nada aqui commita.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_atendimento import MailFolder
from app.services.mail_atendimento import regras
from app.services.mail_atendimento.constantes import KIND_LIXEIRA, KIND_RASCUNHOS, KIND_SPAM


@dataclass(frozen=True)
class PastaDoEmail:
    """A pasta como o agente contou (sem banco)."""

    chave: str
    nome: str
    caminho: str
    tipo_tuta: str


def _curto(valor: object, tamanho: int) -> str:
    return " ".join(str(valor or "").split())[:tamanho]


def do_conteudo(conteudo: dict) -> PastaDoEmail:
    """A pasta de um e-mail decifrado da Central. PURA.

    v2: `tuta.folder_key` (a chave que não muda se a pessoa renomear) e
    `tuta.folder_kind`; v1: só `folder` (o tipo sai do nome).
    """
    tuta = conteudo.get("tuta") if isinstance(conteudo.get("tuta"), dict) else {}
    caminho = _curto(tuta.get("folder_path") or conteudo.get("folder"), 500)
    nome = regras.nome_da_pasta(caminho) or caminho[:200] or "?"
    chave = _curto(tuta.get("folder_key") or conteudo.get("folder"), 191) or nome
    kind = _curto(tuta.get("folder_kind"), 8) or regras.tipo_pelo_nome(nome)
    return PastaDoEmail(chave=chave, nome=nome, caminho=caminho or nome, tipo_tuta=kind)


async def garantir(
    session: AsyncSession,
    mailbox_id: UUID,
    lida: PastaDoEmail,
    *,
    agora: datetime | None = None,
) -> tuple[MailFolder, regras.Classe]:
    """A linha da pasta (criada na primeira vez) e a classe que vale agora."""
    agora = agora or datetime.now(UTC)
    r = await regras.carregar(session)
    # Duas voltas da ponte vendo a mesma pasta nova não brigam pelo INSERT.
    await session.execute(
        pg_insert(MailFolder)
        .values(
            mailbox_id=mailbox_id,
            chave=lida.chave,
            nome=lida.nome,
            caminho=lida.caminho,
            tipo_tuta=lida.tipo_tuta,
            vista_em=agora,
        )
        .on_conflict_do_nothing(index_elements=["mailbox_id", "chave"])
    )
    pasta = await session.scalar(
        select(MailFolder).where(
            MailFolder.mailbox_id == mailbox_id, MailFolder.chave == lida.chave
        )
    )
    assert pasta is not None  # acabou de ser garantida
    if pasta.nome != lida.nome and lida.nome != "?":
        # Renomeada (a chave do v2 não muda): reclassifica, menos o que é de pessoa.
        pasta.nome = lida.nome
        pasta.caminho = lida.caminho
    pasta.vista_em = agora
    pasta.sumiu_em = None
    classe = regras.reclassificar(pasta, r)
    return pasta, classe


def so_contar(pasta: MailFolder) -> bool:
    """A pasta não leva o corpo ao /atendimento (`so_contar`, `nao`, Lixeira, Spam…)."""
    return pasta.ler != regras.LER_CORPO or pasta.tipo_tuta in (
        KIND_SPAM,
        KIND_LIXEIRA,
        KIND_RASCUNHOS,
    )
