"""As pastas de cada caixa com ponte (`mail_folders`).

Quem conta a pasta de um e-mail é o AGENTE, dentro do conteúdo cifrado da
Central: o v1 (IMAP, o agente do outro dev) manda só o nome em `folder`
("INBOX", "INBOX/vendas ml"); o conector v2 (etapa C) manda também o bloco
`tuta` (`folder_key`, `folder_kind`, `folder_path`). A ponte, ao ver um
e-mail que PASSOU no filtro da caixa (crítica de 08/10: a pasta de um e-mail
privado nunca vira linha em claro), garante a linha da pasta aqui — nova →
classificada pela regra; já existente → mantém o que uma pessoa escolheu.

A pasta nova nasce SEM categoria (21/10/2026): o que chega nela fica
`ignorado`/`pasta_sem_categoria` até uma pessoa escolher (Venda · Chamado ·
Informativo · Site (como hoje)); aí a ponte relê os guardados dos últimos
`RELER_DIAS` dias (`reler_ignorados`; os mais antigos ficam guardados — o
acumulado de semanas viraria conversa de uma vez) e `reler_ao_mover` quando o
conector v2 conta que o e-mail mudou para uma pasta categorizada.

Nada aqui commita.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail import MailMessage
from app.models.mail_atendimento import MailFolder, MailMessageMeta
from app.services.mail_atendimento import regras
from app.services.mail_atendimento.constantes import (
    ESTADO_IGNORADO,
    IGNORADOS_RELIDOS,
    KIND_LIXEIRA,
    KIND_RASCUNHOS,
    KIND_SPAM,
    RELER_DIAS,
)


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
        select(MailFolder)
        .where(MailFolder.mailbox_id == mailbox_id, MailFolder.chave == lida.chave)
        # A escolha de PESSOA (categoria, ignorar, plataforma) chega por outra
        # sessão (a rota): relê a linha, nunca a cópia guardada nesta sessão.
        .execution_options(populate_existing=True)
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


def e_criada(pasta: MailFolder) -> bool:
    """A pasta que a equipe criou (só ela tem categoria; a Entrada liga como sempre)."""
    return regras.e_criada(pasta.tipo_tuta)


def _relida():
    """A meta que a ponte pode reler sem duplicar nada: `ignorado` só pela
    categoria (sem categoria, chamado sem pedido), sem conversa, mensagem nem
    chamado."""
    m = MailMessageMeta
    return (
        m.estado == ESTADO_IGNORADO,
        m.motivo.in_(IGNORADOS_RELIDOS),
        m.conversa_id.is_(None),
        m.mensagem_id.is_(None),
        m.chamado_id.is_(None),
    )


def desde_os_relidos(agora: datetime | None = None) -> datetime:
    """O começo da janela do que se relê: os e-mails RECEBIDOS nos últimos
    `RELER_DIAS` dias (a tela mostra o mesmo número na confirmação)."""
    return (agora or datetime.now(UTC)) - timedelta(days=RELER_DIAS)


async def reler_ignorados(
    session: AsyncSession, pasta: MailFolder, *, agora: datetime | None = None
) -> int:
    """A pasta GANHOU categoria: apaga as metas `ignorado` que só ficaram assim
    pela falta dela (ou pelo chamado sem pedido) dos e-mails recebidos nos
    últimos `RELER_DIAS` dias — a volta seguinte da ponte as acha como
    pendentes (`ponte.pendentes`, DEPOIS dos e-mails que acabaram de chegar) e
    liga pela categoria nova. Os mais antigos ficam guardados (o `reprocessar`
    de pessoa ainda os relê um a um). Nada tinha conversa: reler não duplica.
    Devolve quantas."""
    if regras.categoria_que_vale(pasta) is None:
        return 0
    recentes = select(MailMessage.id).where(
        MailMessage.mailbox_id == pasta.mailbox_id,
        MailMessage.received_at >= desde_os_relidos(agora),
    )
    resultado = await session.execute(
        delete(MailMessageMeta)
        .where(
            MailMessageMeta.folder_id == pasta.id,
            *_relida(),
            MailMessageMeta.message_id.in_(recentes),
        )
        .execution_options(synchronize_session=False)
    )
    return int(resultado.rowcount or 0)


async def reler_ao_mover(session: AsyncSession, meta: MailMessageMeta, pasta: MailFolder) -> bool:
    """O conector v2 contou que o e-mail mudou para `pasta`: se ela tem categoria e
    a meta é das que se releem, apaga a meta (a ponte relê já na pasta nova)."""
    if regras.categoria_que_vale(pasta) is None:
        return False
    if not (
        meta.estado == ESTADO_IGNORADO
        and meta.motivo in IGNORADOS_RELIDOS
        and meta.conversa_id is None
        and meta.mensagem_id is None
        and meta.chamado_id is None
    ):
        return False
    await session.delete(meta)
    return True
