"""As PASTAS de e-mail embaixo de cada loja na barra de lojas da Caixa (27/10/2026).

O Eduardo, 27/10: embaixo de cada loja, as três pastas do Tuta que ele
unificou — Problemas, Envios e Devolução (`GRUPOS_EMAIL_NA_BARRA_DE_LOJAS`,
o único lugar da escolha) —, cada uma com "opção de encolher ou abrir", só com
os e-mails DAQUELA loja. Toda loja da barra tem as três caixas, mesmo vazias: a
vazia diz POR QUÊ (o dono: "em tese toda loja recebe algum e-mail" — zero é um
vínculo que falta).

A pasta casa com o grupo pelo nome (a última parte do caminho) ou pela chave,
inteiros, sem acento e sem diferenciar maiúsculas ("Devoluçoes" =
"devolucoes"), em qualquer caixa com a ponte.

A CAIXA mostra TODO e-mail das pastas do grupo ligado à loja (o dono, 27/10:
"show EVERY e-mail of those folders for the store") — `email_da_caixa_sql`:
  • a conversa de e-mail (gravado, sem vínculo, resumo) — abre a conversa;
  • a LINHA informativa (a pasta com a categoria Informativo: sem conversa);
  • o que a ponte SEGUROU como segurança (`seguranca`): o dono, 27/10 — nestas
    três pastas são os códigos de postagem/devolução que a equipe precisa;
    fora delas, o e-mail de segurança continua escondido como sempre;
  • o que ela guardou sem ligar (`ignorado` por pasta só contada, sem
    categoria, chamado sem pedido) — o que chegou antes da pasta ser liberada;
  • o `duplicado` (o mesmo Message-ID noutra caixa/alias) só quando o original
    NÃO está nas pastas da barra (nunca aparece duas vezes).
  Nunca: privado (a caixa do dono), interno, sem loja, erro, nem o ignorado de
  pessoa, de endereço interno ou o aviso do Tuta.
  A LOJA do e-mail: a que a ponte gravou na meta; sem ela (segurança, só
  contar, duplicado: a ponte não grava loja), a provável do ÍNDICE leve
  (`mail_caixa_indice`, a mesma `rotear.rotear` da ponte, com o selo `loja`).
  O número da caixa é o de e-mails dos últimos `DIAS_DA_BARRA_DE_LOJAS` dias
  (a lista mostra os mesmos).

A conversa de e-mail também tem GRUPO (o filtro `grupo_email` da lista de
conversas e os números `abertas`/`aguardando`): a pasta do e-mail RECEBIDO
mais recente dela (o enviado por nós não conta) — um grupo só, ou nenhum.

O texto da caixa (`texto_da_caixa`): os links de ACESSO (login, senha,
confirmação) removidos e a senha escrita mascarada, como em todo lugar; o
CÓDIGO fica à mostra (é o que a equipe precisa nestas pastas — decisão do dono).

O PORQUÊ da caixa vazia (`status` de cada grupo e o diagnóstico de cada loja):
  • a pasta do grupo não existe em nenhuma caixa com a ponte (`sem_pasta`);
  • a pasta existe mas a ponte só CONTA o e-mail dela (`so_contar`: "envio" e
    "devoluções" estão em `PASTAS_SO_CONTAR`; a pasta sem plataforma também),
    guarda sem ligar (`sem_categoria`) ou o liga à Pós-venda (`outra_categoria`:
    Chamado) — a pasta ainda não está configurada para a barra;
  • a loja não tem endereço que a ponte conheça (`alias`: `sem_cadastro` =
    sem e-mail em Cadastros › Lojas; `fora_da_caixa` = o e-mail do cadastro
    não está em nenhuma caixa lida pela ponte);
  • a loja tem e-mail ligado, mas nenhum nesta pasta nos últimos dias.
Só números e códigos: nada de endereço, assunto ou remetente nos números.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, case, exists, literal, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import MailMailbox, MailMessage
from app.models.mail_atendimento import (
    MailCaixaIndice,
    MailFolder,
    MailMailboxSettings,
    MailMessageMeta,
)
from app.services.mail_atendimento import codigos, constantes, regras, rotear
from app.services.mail_atendimento.constantes import (
    CATEGORIA_INFORMATIVO,
    CATEGORIA_SITE,
    CATEGORIA_VENDA,
    ESTADO_DUPLICADO,
    ESTADO_GRAVADO,
    ESTADO_IGNORADO,
    ESTADO_INFORMATIVO,
    ESTADO_RESUMO,
    ESTADO_SEGURANCA,
    ESTADO_SEM_VINCULO,
    IGNORADO_CHAMADO_SEM_CHAMADO,
    IGNORADO_CHAMADO_SEM_PEDIDO,
    IGNORADO_PASTA_SEM_CATEGORIA,
    IGNORADO_PASTA_SO_CONTAR,
    KIND_LIXEIRA,
    KIND_RASCUNHOS,
    KIND_SPAM,
)

# O status da pasta de um grupo (do melhor para o pior): a ponte LIGA o e-mail
# dela à loja (conversa ou linha informativa); guarda sem ligar (pasta criada
# sem categoria); liga à Pós-venda (Chamado); só conta; não existe.
LIGA = "liga"
SEM_CATEGORIA = "sem_categoria"
OUTRA_CATEGORIA = "outra_categoria"
SO_CONTAR = "so_contar"
SEM_PASTA = "sem_pasta"
_ORDEM_STATUS = (LIGA, SEM_CATEGORIA, OUTRA_CATEGORIA, SO_CONTAR, SEM_PASTA)

_KINDS_SO_CONTAR = (KIND_SPAM, KIND_LIXEIRA, KIND_RASCUNHOS)

# O endereço da loja para a ponte.
ALIAS_NA_PONTE = "na_ponte"
ALIAS_FORA_DA_CAIXA = "fora_da_caixa"
ALIAS_SEM_CADASTRO = "sem_cadastro"

# Os e-mails que a caixa da barra mostra (pelo estado da ponte).
ESTADOS_NA_CAIXA = (
    ESTADO_GRAVADO,
    ESTADO_SEM_VINCULO,
    ESTADO_RESUMO,
    ESTADO_INFORMATIVO,
    ESTADO_SEGURANCA,
)
IGNORADOS_NA_CAIXA = (
    IGNORADO_PASTA_SO_CONTAR,
    IGNORADO_PASTA_SEM_CATEGORIA,
    IGNORADO_CHAMADO_SEM_PEDIDO,
    IGNORADO_CHAMADO_SEM_CHAMADO,
)
SELO_LOJA = "loja"


def normalizar(texto: str | None) -> str:
    """Sem acento, minúsculo, os espaços juntados. PURA."""
    sem = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return " ".join(sem.split()).casefold()


@dataclass(frozen=True)
class Grupo:
    chave: str
    rotulo: str
    pastas: tuple[str, ...]

    @property
    def nomes(self) -> frozenset[str]:
        return frozenset(normalizar(p) for p in self.pastas)


def grupos() -> tuple[Grupo, ...]:
    """Os grupos da barra, na ordem (lidos da constante na hora: os testes a trocam)."""
    return tuple(Grupo(c, r, tuple(p)) for c, r, p in constantes.GRUPOS_EMAIL_NA_BARRA_DE_LOJAS)


def chaves() -> tuple[str, ...]:
    return tuple(g.chave for g in grupos())


def grupo_da_pasta(
    nome: str | None, caminho: str | None = None, chave: str | None = None
) -> str | None:
    """O grupo de uma pasta pelo nome (a última parte do caminho) ou pela chave. PURA."""
    candidatos = {normalizar(nome), normalizar(regras.nome_da_pasta(caminho)), normalizar(chave)}
    candidatos.discard("")
    for g in grupos():
        if candidatos & g.nomes:
            return g.chave
    return None


def status_da_pasta(pasta: MailFolder) -> str:
    """O que a ponte faz HOJE com o e-mail desta pasta (a régua de `ponte.processar`).

    `liga` = vira conversa da loja (a Entrada, a pasta criada de Venda ou "Site
    (como hoje)") ou LINHA informativa da loja (a pasta Informativo): as duas
    aparecem na caixa da barra. `outra_categoria` = Chamado (vai à Pós-venda ›
    Chamados, não à barra)."""
    if pasta.ler != regras.LER_CORPO or pasta.tipo_tuta in _KINDS_SO_CONTAR:
        return SO_CONTAR
    if not regras.e_criada(pasta.tipo_tuta):
        return LIGA  # a Entrada (nunca tem categoria): liga como sempre
    categoria = regras.categoria_que_vale(pasta)
    if categoria in (CATEGORIA_VENDA, CATEGORIA_SITE, CATEGORIA_INFORMATIVO):
        return LIGA
    if categoria is None:
        return SEM_CATEGORIA
    return OUTRA_CATEGORIA


@dataclass
class PastasDaBarra:
    # grupo → as pastas (todas, de qualquer caixa: o e-mail antigo continua no grupo)
    por_grupo: dict[str, list[UUID]]
    # grupo → o melhor status entre as pastas das caixas com a ponte ligada
    status: dict[str, str]

    def ids(self, grupo: str | None = None) -> list[UUID]:
        if grupo:
            return self.por_grupo.get(grupo, [])
        return [i for lista in self.por_grupo.values() for i in lista]

    def grupo_sql(self, folder_id):
        """O grupo (a chave) da pasta, em SQL — NULL fora dos grupos."""
        ramos = [(folder_id.in_(ids), literal(g)) for g, ids in self.por_grupo.items() if ids]
        return case(*ramos, else_=None) if ramos else literal(None)


async def pastas_da_barra(session: AsyncSession) -> PastasDaBarra:
    """As pastas de cada grupo e o status delas (uma consulta)."""
    ligadas = set(
        (
            await session.execute(
                select(MailMailboxSettings.mailbox_id).where(
                    MailMailboxSettings.ponte_ligada.is_(True)
                )
            )
        ).scalars()
    )
    por_grupo: dict[str, list[UUID]] = {g: [] for g in chaves()}
    status: dict[str, str] = dict.fromkeys(chaves(), SEM_PASTA)
    for pasta in (await session.execute(select(MailFolder).order_by(MailFolder.id))).scalars():
        g = grupo_da_pasta(pasta.nome, pasta.caminho, pasta.chave)
        if g is None:
            continue
        por_grupo[g].append(pasta.id)
        if pasta.mailbox_id in ligadas and pasta.sumiu_em is None:
            s = status_da_pasta(pasta)
            if _ORDEM_STATUS.index(s) < _ORDEM_STATUS.index(status[g]):
                status[g] = s
    return PastasDaBarra(por_grupo=por_grupo, status=status)


# ── O e-mail da caixa (uma linha por e-mail) ───────────────────────────────


def indice_alias():
    """O índice leve (a loja provável) para o `outerjoin` das consultas da caixa."""
    return aliased(MailCaixaIndice)


def loja_do_email_sql(i, m=MailMessageMeta):
    """(integração, ficha) do e-mail: a da meta (a ponte) ou — sem ela — a do índice."""
    da_meta = or_(m.integration_id.is_not(None), m.store_info_id.is_not(None))
    do_indice = i.selo == SELO_LOJA
    integ = case((da_meta, m.integration_id), (do_indice, i.integration_id), else_=None)
    ficha = case((da_meta, m.store_info_id), (do_indice, i.store_info_id), else_=None)
    return integ, ficha


def email_da_caixa_sql(pastas: PastasDaBarra, grupo: str | None = None, m=MailMessageMeta):
    """WHERE do e-mail que a caixa de um grupo (ou de qualquer grupo) mostra."""
    ids = pastas.ids(grupo)
    if not ids:
        return (literal(False),)
    todas = pastas.ids()
    original = aliased(MailMessageMeta)
    original_na_barra = exists(
        select(literal(1)).where(
            original.message_id == m.duplicado_de, original.folder_id.in_(todas)
        )
    )
    return (
        m.folder_id.in_(ids),
        or_(
            m.estado.in_(ESTADOS_NA_CAIXA),
            and_(m.estado == ESTADO_IGNORADO, m.motivo.in_(IGNORADOS_NA_CAIXA)),
            and_(m.estado == ESTADO_DUPLICADO, not_(original_na_barra)),
        ),
    )


def texto_da_caixa(assunto: str | None, texto: str | None) -> tuple[str, str, int]:
    """(assunto, texto, links removidos) do e-mail na caixa da barra.

    Os links de ACESSO saem (todos, se o e-mail fala de senha/código/acesso) e a
    senha escrita vira "•" — como em todo lugar (`codigos.proteger`); o CÓDIGO
    fica à mostra: nas pastas da barra são os códigos de postagem/devolução que
    a equipe precisa (o dono, 27/10/2026)."""
    todos = codigos.fala_de_acesso(assunto, texto)
    no_assunto = codigos._fala_de_senha(assunto)  # noqa: SLF001 — a mesma régua do proteger
    assunto_ok, n1 = codigos.mascarar_links(assunto or "", todos=todos)
    texto_ok, n2 = codigos.mascarar_links(texto or "", todos=todos)
    assunto_ok, _ = codigos.mascarar_senha(assunto_ok, termo_no_assunto=no_assunto)
    texto_ok, _ = codigos.mascarar_senha(texto_ok, termo_no_assunto=no_assunto)
    return assunto_ok, texto_ok, n1 + n2


# ── A conversa de e-mail no grupo ──────────────────────────────────────────


def ultima_pasta_recebida():
    """(conversa_id, folder_id) do e-mail RECEBIDO mais recente de cada conversa."""
    m, mm = MailMessageMeta, MailMessage
    return (
        select(m.conversa_id.label("conversa_id"), m.folder_id.label("folder_id"))
        .join(mm, mm.id == m.message_id)
        .where(m.conversa_id.is_not(None), mm.direction != "sent")
        .distinct(m.conversa_id)
        .order_by(m.conversa_id, mm.received_at.desc(), mm.id.desc())
        .subquery("ultima_pasta")
    )


def conversas_do_grupo_sql(pastas: list[UUID]):
    """As conversas cujo e-mail recebido mais recente está numa destas pastas."""
    u = ultima_pasta_recebida()
    return select(u.c.conversa_id).where(u.c.folder_id.in_(pastas))


# ── O endereço da loja ─────────────────────────────────────────────────────


async def alias_das_lojas(
    session: AsyncSession,
) -> tuple[dict[UUID, str], dict[UUID, str]]:
    """O endereço de cada loja para a ponte: ({integração: status}, {ficha: status}).

    `na_ponte` — o e-mail do cadastro (Cadastros › Lojas) é um endereço de uma
    caixa com a ponte ligada (o principal ou um alias, do config da caixa);
    `fora_da_caixa` — o cadastro tem e-mail, mas nenhuma dessas caixas o tem.
    A loja que não aparece = `sem_cadastro`. A integração vale pelo melhor
    entre as fichas dela (o FK ou o par — a régua da ponte)."""
    from app.services.mail_atendimento import ponte  # import tardio: a ponte é pesada

    cad = await rotear.cadastro(session)
    caixas = (
        await session.execute(
            select(MailMailbox)
            .join(MailMailboxSettings, MailMailboxSettings.mailbox_id == MailMailbox.id)
            .where(MailMailboxSettings.ponte_ligada.is_(True))
        )
    ).scalars()
    enderecos: set[str] = set()
    for caixa in caixas:
        try:
            enderecos |= ponte.aliases_da_caixa(caixa)[1]
        except Exception:  # noqa: BLE001, S112 — um config ilegível não derruba a barra
            continue
    por_integracao: dict[UUID, str] = {}
    por_ficha: dict[UUID, str] = {}
    for lj in cad.lojas:
        st = ALIAS_NA_PONTE if any(lj.casa(e) for e in enderecos) else ALIAS_FORA_DA_CAIXA
        por_ficha[lj.store_info_id] = st
        integ = lj.integration_id
        if integ is not None and por_integracao.get(integ) != ALIAS_NA_PONTE:
            por_integracao[integ] = st
    return por_integracao, por_ficha
