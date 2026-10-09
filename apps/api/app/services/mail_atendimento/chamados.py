"""Os CHAMADOS dos sites (RF6): o formulário vira chamado com o protocolo do site.

O site (Uranyx, Charlots, 7Buyers, Locagil) manda o formulário DE sac@ PARA
sac@ (ou atacado@/duvidas@), com o cliente no corpo e o PROTOCOLO no assunto
("[US-26-0014] Troca"). A ponte (`ponte.py`) já faz o principal:

  • lê o protocolo (nunca gera outro) e o TIPO pelo prefixo — S = SAC,
    A = Atacado, DS = Dúvidas e sugestões; o tipo do protocolo vale mais que
    a caixa que recebeu (com alerta);
  • a conversa do chamado é uma por protocolo (`mail-protocolo:<nº>`, sem
    integração: plataforma `site`), com a marca, o tipo e o protocolo em
    `dados.mail`; o cliente (o e-mail do corpo) é o `comprador_id`;
  • a resposta sai do e-mail do TIPO da marca (`marca_emails`: sac@uranyx…)
    quando a caixa tem ele (`responder.de_alias`), para o cliente do corpo
    (calculado no servidor), com o [protocolo] no assunto.

Aqui fica o resto: SUGERIR AGRUPAR quando a mesma cliente tem outro chamado
ABERTO na mesma marca (`outros_chamados`) e o AGRUPAR por clique de quem mexe
(`agrupar`: as mensagens vão para o chamado escolhido; nada se apaga).
Nunca junta sozinho: dois protocolos podem ser dois assuntos.

MESMA CLIENTE (`mesma_cliente`, RF6): o mesmo e-mail, o mesmo telefone (só
dígitos) ou o mesmo nº de pedido escrito no formulário — sempre dentro da
mesma marca. Telefone e pedido são texto livre de quem preenche o site: só
valem quando os NOMES não são de pessoas diferentes ("Ana Lima" × "Bruno
Costa" nunca é a mesma cliente, nem para sugerir, nem para agrupar). O chamado
que FICA é o mais antigo (`ordem_do_agrupamento`); o "Não agrupar" fica
lembrado nos dois (`recusar_agrupar`) e a sugestão some.
O pedido citado no formulário é procurado no Bling só como SUGESTÃO
(`pedido_sugerido`): nunca liga sozinho (pode ser o pedido de outra pessoa).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import TeamScope
from app.models import AtendimentoConversa, AtendimentoMensagem, BlingOrder, User
from app.models.mail_atendimento import MailMessageMeta, MailOutboxMeta
from app.services.atendimento import gravar
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_SISTEMA,
    CANAL_EMAIL,
    CONVERSA_FECHADA,
    FONTE_TUTA,
    MSG_ENVIANDO,
)
from app.services.mail_atendimento import enderecos, ponte
from app.services.mail_atendimento.constantes import (
    PLATAFORMA_SITE,
    ROTULO_ALERTA_DO_CHAMADO,
    ROTULO_STATUS_CHAMADO,
    ROTULO_TIPO_CAIXA,
    STATUS_CHAMADO_ABERTO,
    STATUS_CHAMADO_AGUARDANDO,
    STATUS_CHAMADO_RESOLVIDO,
)

MAX_OUTROS = 10
# Quantos pedidos do Bling com o nº citado mostrar (o espelho tem 1 linha por item).
MAX_PEDIDOS_SUGERIDOS = 3
MOTIVO_MESMO_EMAIL = "email"
MOTIVO_MESMO_TELEFONE = "telefone"
MOTIVO_MESMO_PEDIDO = "pedido"
ROTULO_MOTIVO = {
    MOTIVO_MESMO_EMAIL: "mesmo e-mail",
    MOTIVO_MESMO_TELEFONE: "mesmo telefone",
    MOTIVO_MESMO_PEDIDO: "mesmo nº de pedido",
}
# Telefone e pedido com e-mails diferentes: a tela diz, para a pessoa conferir.
SUFIXO_EMAIL_DIFERENTE = ", e-mail diferente"


class AgruparError(ValueError):
    def __init__(self, codigo: str, status: int = 409):
        self.codigo = codigo
        self.status = status
        super().__init__(codigo)


def e_chamado(conversa: AtendimentoConversa) -> bool:
    """A conversa é um chamado de site da ponte (plataforma `site`, e-mail)."""
    return conversa.plataforma == PLATAFORMA_SITE and ponte.e_conversa_da_ponte(conversa)


def protocolos(conversa: AtendimentoConversa) -> list[str]:
    """Todos os protocolos do chamado: o principal primeiro e os agrupados nele."""
    mail = ponte.dados_mail(conversa)
    todos = [mail.get("protocolo"), *(mail.get("protocolos") or [])]
    return list(dict.fromkeys(p for p in todos if isinstance(p, str) and p))


def status_do_chamado(conversa: AtendimentoConversa) -> str:
    """Aberto · Aguardando cliente · Resolvido (RF6), derivado da conversa.

    Fechada = resolvido; esperando a loja = aberto; a loja respondeu por
    último = aguardando cliente; "não precisa de resposta" = resolvido. (A
    resposta ainda na fila do Mac já conta como dada; o rótulo diz "resposta
    na fila" — `resumo_do_chamado(..., resposta_na_fila=True)`.)"""
    if conversa.situacao == CONVERSA_FECHADA:
        return STATUS_CHAMADO_RESOLVIDO
    if conversa.aguardando_resposta:
        return STATUS_CHAMADO_ABERTO
    da_loja = gravar._utc(conversa.ultima_da_loja_em)
    do_cliente = gravar._utc(conversa.ultima_do_cliente_em)
    if da_loja is not None and (do_cliente is None or da_loja >= do_cliente):
        return STATUS_CHAMADO_AGUARDANDO
    if conversa.sem_resposta_necessaria:
        return STATUS_CHAMADO_RESOLVIDO
    return STATUS_CHAMADO_ABERTO


def _recusados(conversa: AtendimentoConversa) -> set[str]:
    """Os chamados que alguém disse "Não agrupar" com este (a sugestão some)."""
    return {
        str(r.get("conversa_id"))
        for r in (ponte.dados_mail(conversa).get("nao_agrupar") or [])
        if isinstance(r, dict) and r.get("conversa_id")
    }


async def resposta_na_fila(session: AsyncSession, conversa: AtendimentoConversa) -> bool:
    """A resposta da loja ainda está na fila (o Mac não mandou): `enviando`."""
    return (
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.status == MSG_ENVIANDO,
            )
            .limit(1)
        )
    ) is not None


def resumo_do_chamado(conversa: AtendimentoConversa, *, resposta_na_fila: bool = False) -> dict:
    mail = ponte.dados_mail(conversa)
    tipo = mail.get("caixa")
    status = status_do_chamado(conversa)
    rotulo = ROTULO_STATUS_CHAMADO[status]
    if status == STATUS_CHAMADO_AGUARDANDO and resposta_na_fila:
        rotulo = f"{rotulo} (resposta na fila)"
    return {
        "conversa_id": str(conversa.id),
        "protocolo": mail.get("protocolo"),
        "protocolos": protocolos(conversa),
        "marca": mail.get("marca"),
        "marca_nome": mail.get("marca_nome"),
        "tipo": tipo,
        "tipo_rotulo": ROTULO_TIPO_CAIXA.get(tipo or ""),
        "situacao": conversa.situacao,
        "status": status,
        "status_rotulo": rotulo,
        "criado_em": gravar._utc(conversa.created_at),
        "ultima_mensagem_em": gravar._utc(conversa.ultima_mensagem_em),
        "cliente_nome": conversa.comprador_nome,
        "telefone": mail.get("telefone"),
        "pedido_citado": mail.get("pedido_citado"),
        "alertas": [
            {"codigo": c, "texto": ROTULO_ALERTA_DO_CHAMADO[c]}
            for c in (mail.get("alertas") or [])
            if c in ROTULO_ALERTA_DO_CHAMADO
        ],
    }


def mesma_cliente(a: AtendimentoConversa, b: AtendimentoConversa) -> str | None:
    """Os dois chamados são da MESMA cliente? → o porquê (e-mail, telefone,
    pedido) ou None. Só na mesma marca (nunca Uranyx com Charlots).

    O telefone e o pedido (texto livre do formulário) só valem quando os
    nomes não são de pessoas DIFERENTES (`enderecos.mesmo_nome` = False):
    duas clientes que escreveram o mesmo nº nunca viram "a mesma". Vale para a
    sugestão e para o agrupar (a rota responde 409 outra_cliente_ou_marca)."""
    ma, mb = ponte.dados_mail(a), ponte.dados_mail(b)
    if not ma.get("marca") or ma.get("marca") != mb.get("marca"):
        return None
    if ponte.clientes_da_conversa(a) & ponte.clientes_da_conversa(b):
        return MOTIVO_MESMO_EMAIL
    if enderecos.mesmo_nome(a.comprador_nome, b.comprador_nome) is False:
        return None
    if ma.get("telefone") and ma.get("telefone") == mb.get("telefone"):
        return MOTIVO_MESMO_TELEFONE
    if ma.get("pedido_citado") and ma.get("pedido_citado") == mb.get("pedido_citado"):
        return MOTIVO_MESMO_PEDIDO
    return None


async def outros_chamados(session: AsyncSession, conversa: AtendimentoConversa) -> list[dict]:
    """Outros chamados ABERTOS da MESMA cliente na MESMA marca (sugestão de agrupar).

    A mesma cliente: o mesmo e-mail, o mesmo telefone ou o mesmo pedido
    citado (`mesma_cliente`). O "Não agrupar" lembrado tira o par da lista."""
    if not e_chamado(conversa):
        return []
    mail = ponte.dados_mail(conversa)
    marca = mail.get("marca")
    if not marca:
        return []
    clientes = sorted(ponte.clientes_da_conversa(conversa))
    casa: list[ColumnElement[bool]] = []
    if clientes:
        casa.append(AtendimentoConversa.comprador_id.in_(clientes))
        # O chamado que já agrupou outro guarda os e-mails dos dois (`clientes`).
        casa.extend(
            AtendimentoConversa.dados[ponte.CHAVE]["clientes"].contains([c]) for c in clientes
        )
    if mail.get("telefone"):
        casa.append(AtendimentoConversa.dados[ponte.CHAVE]["telefone"].astext == mail["telefone"])
    if mail.get("pedido_citado"):
        casa.append(
            AtendimentoConversa.dados[ponte.CHAVE]["pedido_citado"].astext == mail["pedido_citado"]
        )
    if not casa:
        return []
    recusados = _recusados(conversa)
    candidatos = (
        await session.execute(
            select(AtendimentoConversa)
            .where(
                AtendimentoConversa.plataforma == PLATAFORMA_SITE,
                AtendimentoConversa.canal == CANAL_EMAIL,
                AtendimentoConversa.integration_id.is_(None),
                or_(*casa),
                AtendimentoConversa.id != conversa.id,
                AtendimentoConversa.situacao != CONVERSA_FECHADA,
                AtendimentoConversa.dados["fonte"].astext == FONTE_TUTA,
                AtendimentoConversa.dados[ponte.CHAVE]["marca"].astext == marca,
            )
            .order_by(AtendimentoConversa.ultima_mensagem_em.desc().nulls_last())
            .limit(MAX_OUTROS + len(recusados))
        )
    ).scalars()
    linhas = [
        c
        for c in candidatos
        if mesma_cliente(conversa, c) is not None
        and str(c.id) not in recusados
        and str(conversa.id) not in _recusados(c)
    ]
    inicio = await inicio_dos_chamados(session, [conversa.id, *(c.id for c in linhas)])
    saida: list[dict] = []
    for c in linhas:
        motivo = mesma_cliente(conversa, c) or MOTIVO_MESMO_EMAIL
        rotulo = ROTULO_MOTIVO[motivo]
        if (
            motivo != MOTIVO_MESMO_EMAIL
            and ponte.clientes_da_conversa(conversa)
            and ponte.clientes_da_conversa(c)
        ):
            rotulo += SUFIXO_EMAIL_DIFERENTE
        saida.append(
            {
                **resumo_do_chamado(c),
                "motivo": motivo,
                "motivo_rotulo": rotulo,
                # O chamado que FICA ao agrupar é o mais antigo dos dois.
                "fica_este": _mais_antigo_fica(conversa, c, inicio)[1].id == c.id,
            }
        )
        if len(saida) >= MAX_OUTROS:
            break
    return saida


async def inicio_dos_chamados(
    session: AsyncSession, ids: list[UUID] | tuple[UUID, ...]
) -> dict[UUID, datetime]:
    """Quando cada chamado COMEÇOU: a 1ª mensagem da cliente (o formulário).
    Sem nenhuma, fica de fora (vale a criação da conversa)."""
    if not ids:
        return {}
    linhas = (
        await session.execute(
            select(
                AtendimentoMensagem.conversa_id,
                func.min(
                    func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
                ),
            )
            .where(
                AtendimentoMensagem.conversa_id.in_(list(ids)),
                AtendimentoMensagem.autor == AUTOR_CLIENTE,
            )
            .group_by(AtendimentoMensagem.conversa_id)
        )
    ).all()
    saida: dict[UUID, datetime] = {}
    for cid, quando in linhas:
        momento = gravar._utc(quando)
        if momento is not None:
            saida[cid] = momento
    return saida


def _chave_da_idade(c: AtendimentoConversa, inicio: dict[UUID, datetime]) -> tuple:
    """Mais antigo = começou antes; no empate (o mesmo minuto), o protocolo menor
    (a sequência do site), depois a criação da conversa e o id."""
    nunca = datetime.max.replace(tzinfo=UTC)
    criada = gravar._utc(c.created_at) or nunca
    return (
        inicio.get(c.id) or criada,
        ponte.dados_mail(c).get("protocolo") or "~",
        criada,
        str(c.id),
    )


def _mais_antigo_fica(
    a: AtendimentoConversa, b: AtendimentoConversa, inicio: dict[UUID, datetime]
) -> tuple[AtendimentoConversa, AtendimentoConversa]:
    if _chave_da_idade(b, inicio) <= _chave_da_idade(a, inicio):
        return (a, b)
    return (b, a)


async def ordem_do_agrupamento(
    session: AsyncSession, a: AtendimentoConversa, b: AtendimentoConversa
) -> tuple[AtendimentoConversa, AtendimentoConversa]:
    """(origem, destino) ao agrupar dois chamados: FICA o mais antigo (RF6: "o
    cabeçalho mostra o protocolo principal, o mais antigo")."""
    return _mais_antigo_fica(a, b, await inicio_dos_chamados(session, (a.id, b.id)))


async def pedido_sugerido(
    session: AsyncSession, conversa: AtendimentoConversa, scope: TeamScope | None = None
) -> dict | None:
    """O pedido que a cliente escreveu no formulário, no espelho do Bling — SÓ
    SUGESTÃO (RF6: "pedido citado: busca no Bling pelo nº informado").

    Nunca liga sozinho: a pessoa confere. `confere` diz se o destinatário do
    pedido tem o nome da cliente (True), outro nome (False: pode ser o pedido
    de OUTRA pessoa — o nome dela nunca sai daqui) ou se não dá para saber
    (None). Sai só o nº (Bling e loja) e a data. Quem tem o escopo da equipe
    restrito não recebe sugestão (a busca é no Bling inteiro; o chamado do
    site, sem integração, hoje nem abre para essa pessoa)."""
    numero = ponte.dados_mail(conversa).get("pedido_citado")
    if not isinstance(numero, str) or not numero:
        return None
    if scope is not None and not scope.unrestricted:
        return None
    linhas = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.numeroloja,
                BlingOrder.data,
                BlingOrder.nome_destinatario,
            )
            .where(or_(BlingOrder.numero == numero, BlingOrder.numeroloja == numero))
            .order_by(BlingOrder.data.desc().nulls_last())
            .limit(20)
        )
    ).all()
    pedidos: dict[str, dict] = {}
    for numero_bling, numero_loja, data, nome in linhas:
        chave = str(numero_bling or numero_loja or "")
        if not chave or chave in pedidos:
            continue
        pedidos[chave] = {
            "numero_bling": numero_bling,
            "numero_loja": numero_loja,
            "data": gravar._utc(data),
            "confere": enderecos.mesmo_nome(nome, conversa.comprador_nome),
        }
        if len(pedidos) >= MAX_PEDIDOS_SUGERIDOS:
            break
    return {"numero": numero, "achado": bool(pedidos), "pedidos": list(pedidos.values())}


async def agrupar(
    session: AsyncSession,
    origem: AtendimentoConversa,
    destino: AtendimentoConversa,
    user: User,
) -> AtendimentoConversa:
    """Junta o chamado `origem` no `destino` (mesma cliente, mesma marca), por clique.

    As mensagens e os e-mails (`mail_message_meta`, a ligação das respostas)
    passam para o destino; o de origem fica FECHADO e "não precisa de
    resposta", com a marca de para onde foi. O destino fica com TODOS os
    protocolos e e-mails de cliente dos dois (a busca e o casamento pelo
    protocolo continuam achando). Uma nota de sistema registra quem agrupou.
    As duas conversas já vêm travadas por quem chama. Não commita. (Quem
    escolhe qual fica é a rota: o mais antigo — `ordem_do_agrupamento`.)
    """
    if origem.id == destino.id:
        raise AgruparError("mesmo_chamado")
    if not (e_chamado(origem) and e_chamado(destino)):
        raise AgruparError("nao_e_chamado")
    if mesma_cliente(origem, destino) is None:
        raise AgruparError("outra_cliente_ou_marca")
    em_voo = await session.scalar(
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id.in_((origem.id, destino.id)),
            AtendimentoMensagem.status == MSG_ENVIANDO,
        )
        .limit(1)
    )
    if em_voo is not None:
        # Uma resposta na fila: o "uma em voo por conversa" não deixa juntar agora.
        raise AgruparError("envio_em_andamento")
    await session.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id == origem.id)
        .values(conversa_id=destino.id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(MailMessageMeta)
        .where(MailMessageMeta.conversa_id == origem.id)
        .values(conversa_id=destino.id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(MailOutboxMeta)
        .where(MailOutboxMeta.conversa_id == origem.id)
        .values(conversa_id=destino.id)
        .execution_options(synchronize_session=False)
    )
    agora = datetime.now(UTC)
    dados = dict(destino.dados or {})
    mail = dict(dados.get(ponte.CHAVE) or {})
    mail["protocolos"] = list(dict.fromkeys([*protocolos(destino), *protocolos(origem)]))
    mail["clientes"] = sorted(
        ponte.clientes_da_conversa(destino) | ponte.clientes_da_conversa(origem)
    )
    for chave in ("telefone", "pedido_citado"):
        if not mail.get(chave) and ponte.dados_mail(origem).get(chave):
            mail[chave] = ponte.dados_mail(origem)[chave]
    mail["alertas"] = list(
        dict.fromkeys(
            [*(mail.get("alertas") or []), *(ponte.dados_mail(origem).get("alertas") or [])]
        )
    )
    dados[ponte.CHAVE] = mail
    destino.dados = dados
    if not destino.comprador_nome and origem.comprador_nome:
        destino.comprador_nome = origem.comprador_nome
    origem.situacao = CONVERSA_FECHADA
    origem.sem_resposta_necessaria = True
    origem.dados = {
        **(origem.dados or {}),
        ponte.CHAVE: {**ponte.dados_mail(origem), "agrupado_em": str(destino.id)},
    }
    await session.flush()
    await gravar.gravar_mensagem(
        session,
        destino,
        externo_id=f"mail-agrupado:{origem.id}",
        autor=AUTOR_SISTEMA,
        texto=(
            f"Chamado {ponte.dados_mail(origem).get('protocolo') or 'sem protocolo'} agrupado "
            f"neste por {user.name or user.email}."
        ),
        enviada_em=agora,
        payload={ponte.CHAVE: {"agrupado_de": str(origem.id)}},
    )
    await gravar.recalcular_conversa(session, destino)
    await gravar.recalcular_conversa(session, origem)
    return destino


async def recusar_agrupar(
    session: AsyncSession, a: AtendimentoConversa, b: AtendimentoConversa, user: User
) -> None:
    """ "Não agrupar": a sugestão deste par some — lembrado NOS DOIS chamados
    (`dados.mail.nao_agrupar`, com quem e quando). Agrupar à mão continua
    possível. As duas conversas já vêm travadas por quem chama. Não commita."""
    if a.id == b.id:
        raise AgruparError("mesmo_chamado")
    if not (e_chamado(a) and e_chamado(b)):
        raise AgruparError("nao_e_chamado")
    agora = datetime.now(UTC).isoformat()
    for este, outro in ((a, b), (b, a)):
        if str(outro.id) in _recusados(este):
            continue
        mail = dict(ponte.dados_mail(este))
        mail["nao_agrupar"] = [
            *(mail.get("nao_agrupar") or []),
            {"conversa_id": str(outro.id), "por": str(user.id), "em": agora},
        ]
        este.dados = {**(este.dados or {}), ponte.CHAVE: mail}
    await session.flush()


async def conversa_por_id(session: AsyncSession, conversa_id: UUID) -> AtendimentoConversa | None:
    return await session.get(AtendimentoConversa, conversa_id)
