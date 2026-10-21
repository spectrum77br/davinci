"""As respostas da equipe como exemplos de TOM — `atendimento_pares` (spec 5.4, 20/10/2026).

O `buscar_respostas_equipe` do agente: como a PRÓPRIA equipe responde a uma
pergunta parecida. Exemplo de tom, nunca fato nem instrução — o número, o
prazo e a característica de produto de um par são de OUTRO cliente (nada
daqui entra nos permitidos do agente; o id `par:` só vai ao rastro).

Montagem (`pares_da_conversa`, PURA):

- a conversa sem nota interna, sem mensagem que falhou, sem autor de sistema/
  mediador, sem as automáticas (`constantes.e_mensagem_automatica`, origem
  `davinci_auto`) e sem o texto que a loja repete igual em 5+ conversas
  (`ia.modelos_automaticos`: o modelo do Duoke e a resposta pronta) — quem
  sobra é o cliente e a equipe;
- turno INTEIRO do cliente × turno INTEIRO da equipe logo depois; a chave é
  a 1ª mensagem da equipe no turno (o turno que cresce depois atualiza o
  mesmo par, não cria outro);
- só resposta de PESSOA: origem `externo` (Duoke, Seller Center) ou
  `davinci_humano` — e esta vira `davinci_igual`/`davinci_editou` quando
  saiu de um rascunho que a pessoa mandou igual ou editou. Turno com
  resposta do agente/IA enviada SOZINHA (`davinci_ia`) nunca vira par;
- a correção da avaliação negativa (👎 com texto) vira par `correcao` com o
  turno do cliente que o rascunho respondia (spec 5.5).

Filtros: turno com menos de 25 caracteres (o do cliente e o da equipe, cada
um inteiro), `ia._par_seguro` (sinal do cliente ou resposta com cara de
instrução), `ia._ensina_o_proibido` (o que o validador barraria na IA) e a
conversa INTEIRA fora quando qualquer fala do cliente tem cara de instrução
(`ia.sinais_com_codigo` → `HUMANO_INSTRUCAO`) ou quando um rascunho dela foi
marcado "parece instrução para a IA" (`ia.MOTIVO_INJECAO`). Só a versão
generalizada é guardada: `ia.mascarar` e depois `ia._generalizar` (nome,
pedido, rastreio, data, valor, número), até 600 caracteres.

Indexação (`indexar`): incremental a partir do último `respondido_em` (com
margem), ou de `desde` (o backfill do script); nunca antes da retenção de
180 dias (LGPD — `expurgar` apaga o que passou). A conversa processada fica
com EXATAMENTE os pares que valem hoje: o par que deixou de valer (a
conversa ganhou uma fala com cara de instrução) sai.

Busca (`buscar`): mesma plataforma; em canal no automático só da mesma loja
ou de autor admin, e a correção de quem não é admin só na mesma loja e nunca
no automático (a régua de `ia._avaliacao_vale_aqui`); fora a própria
conversa e tudo com `respondido_em >= as_of` (replay). Texto: primeiro os
que casam com TODAS as palavras (`websearch_to_tsquery`), depois os que casam
com alguma; dentro, `ts_rank_cd` × peso da fonte × bônus (mesmo anúncio,
mesma loja) × decaimento pela idade (meia-vida de 60 dias). 5 pares de até
600 caracteres.
"""

from __future__ import annotations

import re
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import and_, case, delete, false, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import (
    AtendimentoAvaliacao,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    User,
    UserRole,
)
from app.models.atendimento import AtendimentoPar
from app.services.atendimento import busca_texto, constantes, ia, validador

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

MAX_PARES = 5
MAX_CHARS_PAR = 600
MIN_CHARS_TURNO = 25
MIN_CONVERSAS_REPETIDA = 5
RETENCAO_DIAS = 180
MEIA_VIDA_DIAS = 60
# A data do backfill único (spec 5.4): a mensagem mais antiga do banco de
# produção. Na prática começa na retenção (nada antes de 180 dias é guardado).
BACKFILL_DESDE = "2024-10-09"
# O peso de cada fonte no ranking (a correção e a resposta editada pela
# pessoa valem mais que a de fora).
PESOS_FONTE: dict[str, float] = {
    constantes.PAR_FONTE_CORRECAO: 1.5,
    constantes.PAR_FONTE_EDITOU: 1.3,
    constantes.PAR_FONTE_HUMANO: 1.2,
    constantes.PAR_FONTE_IGUAL: 1.0,
    constantes.PAR_FONTE_EXTERNO: 1.0,
}
# O bônus do ranking (multiplica): o par do MESMO anúncio responde a mesma
# dúvida de produto; o da mesma loja tem o tom e a política dela.
BONUS_MESMO_ANUNCIO = 0.5
BONUS_MESMA_LOJA = 0.25
# Candidatos lidos do banco antes do ranking final (decaimento e repetidos).
CANDIDATOS_BUSCA = 50
# O cron de hora em hora relê a conversa cujo turno da equipe pode ter
# crescido depois da última rodada.
MARGEM_INCREMENTAL = timedelta(hours=6)
# Sem nenhum par ainda (antes do backfill), o cron olha só o último dia.
JANELA_SEM_PARES = timedelta(days=1)
# Mensagens lidas por conversa (as mais recentes): conversa enorme não
# trava a rodada.
MAX_MENSAGENS_CONVERSA = 400
LOTE_CONVERSAS = 100
# As origens de resposta de PESSOA.
_ORIGENS_DE_PESSOA = frozenset({constantes.ORIGEM_EXTERNO, constantes.ORIGEM_HUMANO})
# O que a avaliação do rascunho diz da resposta que saiu dele.
_FONTE_DA_ACAO = {
    "enviou_igual": constantes.PAR_FONTE_IGUAL,
    "editou": constantes.PAR_FONTE_EDITOU,
}


@dataclass(frozen=True)
class ParNovo:
    """Um par pronto para gravar (já generalizado). Os campos de `AtendimentoPar`."""

    conversa_id: UUID
    resposta_msg_id: UUID
    plataforma: str
    integration_id: UUID | None
    canal_id: UUID | None
    item_id: str | None
    assunto: str | None
    pergunta_gen: str
    resposta_gen: str
    fonte: str
    peso: float
    autor_admin: bool
    perguntado_em: datetime | None
    respondido_em: datetime


@dataclass(frozen=True)
class Correcao:
    """A correção de uma avaliação negativa (👎 com texto) de um rascunho da conversa."""

    gatilho_id: UUID
    texto: str
    autor_admin: bool
    em: datetime


@dataclass
class _Turno:
    autor: str
    mensagens: list[Any] = field(default_factory=list)

    @property
    def texto(self) -> str:
        return " ".join(
            " ".join(str(m.texto).split()) for m in self.mensagens if (m.texto or "").strip()
        )


def _momento(m: Any) -> datetime | None:
    return getattr(m, "enviada_em", None) or getattr(m, "created_at", None)


def _somar(contagem: dict[str, int] | None, chave: str) -> None:
    if contagem is not None:
        contagem[chave] = contagem.get(chave, 0) + 1


def _entra(m: Any, repetidas: Collection[str], nomes_comprador: tuple[str | None, ...]) -> bool:
    """A mensagem é fala do cliente ou da equipe? (o resto some da conversa)"""
    origem = getattr(m, "origem", None)
    if constantes.e_nota(origem, getattr(m, "tipo", None)):
        return False
    if getattr(m, "status", None) == constantes.MSG_FALHOU:
        return False
    if m.autor == constantes.AUTOR_CLIENTE:
        return True
    if m.autor != constantes.AUTOR_LOJA or origem == constantes.ORIGEM_AUTO:
        return False
    if constantes.e_mensagem_automatica(m.texto, getattr(m, "payload", None)):
        return False
    # O texto que a loja manda igual em 5+ conversas: modelo do Duoke ou
    # resposta pronta — não é a equipe respondendo AQUELA pergunta.
    return not (repetidas and ia.chave_modelo(m.texto, nomes_comprador) in repetidas)


def _turnos(mensagens: Sequence[Any]) -> list[_Turno]:
    turnos: list[_Turno] = []
    for m in mensagens:
        if turnos and turnos[-1].autor == m.autor:
            turnos[-1].mensagens.append(m)
        else:
            turnos.append(_Turno(m.autor, [m]))
    return turnos


def _tem_injecao(textos: Sequence[str | None]) -> bool:
    return any(
        codigo == constantes.HUMANO_INSTRUCAO
        for codigo, _frase in ia.sinais_com_codigo([t for t in textos if t], agente=True)
    )


def generalizar(texto: str, nomes: tuple[str | None, ...]) -> str:
    """O texto como é guardado: `mascarar` e depois `_generalizar` (até 600). PURA."""
    return ia._generalizar(ia.mascarar(texto, nomes), nomes)[:MAX_CHARS_PAR].strip()


# O dado pessoal que a máscara do legado (`ia.mascarar`/`_generalizar`, com os
# nomes do PEDIDO) não pega (revisão de 20/10/2026): a rua sem número, o
# condomínio, o bairro, o nome de um terceiro ("meu marido Roberto",
# "entregue para Joana"), o "sou Ana" sem artigo. O par vai para OUTRAS
# conversas, como exemplo: na dúvida, ele sai (não se tenta mascarar).
# Sobre o texto CRU, com maiúscula: o nome próprio é o que pesa.
_NOME = (
    r"(?!(?:Shopee|Mercado|Correios|Loggi|Jadlog|Amazon|Tik|Total|Uranyx|Charlots)\b)"
    r"[A-ZÀ-Ý][a-zà-ÿ]{2,}"
)
_DADO_PESSOAL_DO_PAR = re.compile(
    r"\b(?:[Rr]ua|R\.|[Aa]venida|[Aa]v\.?|[Tt]ravessa|[Aa]lameda|[Ee]strada|[Rr]odovia"
    r"|[Cc]ondom[ií]nio|[Rr]esidencial|[Bb]airro|[Vv]ila|[Jj]ardim)\s+(?:d[aeo]s?\s+)?"
    + _NOME
    + r"|\b(?:[Aa]pto|[Aa]partamento|[Bb]loco)\s*\.?\s*\d"
    r"|\b(?:[Mm]eu|[Mm]inha)\s+(?:marido|esposa|mulher|m[aã]e|pai|filh[oa]|irm[aã]o?|vizinh[oa]"
    r"|namorad[oa]|noiv[oa]|sogr[oa]|cunhad[oa]|ti[oa]|av[oóô]|primo|prima|porteiro)\s*,?\s+"
    + _NOME
    + r"|\b(?:entregue|recebid[oa]|recebeu|assinou|retirou)\s+(?:para|por|pel[ao]|a|o)\s+"
    + _NOME
    # "sou Ana", "aqui é Carla" SEM artigo (com artigo, "me chamo" e "meu nome
    # é", a máscara do legado já tira: `ia._APRESENTACAO`).
    + r"|\b(?:[Ss]ou|[Aa]qui\s+(?:[ée]|eh))\s+"
    + _NOME
)


# A mesma régua SEM depender da maiúscula (revisão 2 de 20/10/2026: no chat
# da Shopee quase todo comprador escreve em minúscula — "meu marido roberto
# recebeu", "foi entregue para joana minha vizinha", "moro na rua das flores",
# "deixaram com a dona maria"). Sobre o texto PLANO (sem acento, minúsculo):
# o parente, "dona"/"seu" ou quem recebeu seguido de uma palavra que não é
# das comuns; a rua/avenida/condomínio/bairro seguida de qualquer palavra. Na
# dúvida, o par sai.
_COMUNS_DEPOIS_DO_NOME = frozenset(
    """
    que e foi esta estava ta tava ja nao recebeu recebe pegou disse falou comprou mora morava
    abriu assinou viu vai vem vai tambem tb mesmo so la aqui ai de da do das dos no na nos nas
    com por pelo pela para pra pro um uma o a os as ele ela eles elas voce vc mim eu nos isso
    esse essa este esta aquele aquela meu minha seu sua cliente comprador compradora vendedor
    loja transportadora correios entregador entregadora motorista carteiro portaria porteiro
    produto pacote pedido item aparelho encomenda caixa celular relogio mala vizinho vizinha
    destinatario remetente equipe shopee mercado plataforma app casa trabalho empresa servico
    hoje ontem amanha agora depois antes quando onde sim ok obrigado obrigada correio
    domicilio tempo prazo transportador entregadores sistema rastreio codigo voces senhor
    senhora mesma mesmo nada ninguem alguem outra outro engano erro
    endereco nome cpf recepcao condominio guarita sindico zelador ti si vcs gente pessoa pessoas
    familia familiar parente responsavel titular quem qualquer todos todas outras outros
    cidade estado bairro rua apartamento predio bloco portao correto certo errado mesma propria
    proprio vizinhanca loja empresa
    """.split()
)
_PARENTE = (
    r"(?:marido|esposa|esposo|mulher|mae|pai|filh[oa]|irma|irmao|vizinh[oa]|namorad[oa]|noiv[oa]"
    r"|sogr[oa]|cunhad[oa]|ti[oa]|avo|primo|prima|sobrinh[oa]|genro|nora|enteado|enteada"
    r"|madrasta|padrasto|amig[oa]|colega|chefe|patra[oa]|cuidadora|empregada|diarista|baba)"
)
_DADO_PESSOAL_PLANO = re.compile(
    r"\b(?:meu|minha|o\s+meu|a\s+minha)\s+" + _PARENTE + r"\s*,?\s+(?P<n1>[a-z]{3,})\b"
    r"|\b(?:dona|seu)\s+(?P<n2>[a-z]{3,})\b(?=\s+(?:do|da|que|recebeu|pegou|assinou|la|ali|aqui"
    r"|vizinh)|\s*[,.!?]|$)"
    r"|\b(?:dona)\s+(?P<n3>[a-z]{3,})\b"
    r"|\b(?:entregue|entregaram|entregou|recebid[oa]|recebeu|assinou|assinado|retirou|retirado"
    r"|deixaram|deixou|deixado)\s+(?:para|por|pel[ao]|a|o|com)\s+(?:a\s+|o\s+|seu\s+|dona\s+)?"
    r"(?P<n4>[a-z]{3,})\b"
    r"|\b(?:rua|avenida|av|travessa|alameda|estrada|rodovia|condominio|residencial|bairro"
    r"|loteamento|conjunto|quadra)\s+(?:d[aeo]s?\s+)?[a-z0-9]{2,}"
    r"|\b(?:apto|apartamento|bloco|casa)\s*\.?\s*(?:n\s*)?\d"
    # Revisão "furos" de 20/10/2026: quem recebe/recebeu dito de outras formas
    # ("foi a joana que recebeu", "quem recebeu foi o carlos", "pode entregar
    # pro joao", "deixar com a maria", "o nome do destinatario e maria",
    # "no nome da fernanda", "o pedido e pra juliana", "o porteiro jose",
    # "recebido por sr. antonio").
    r"|\bfoi\s+(?:o|a)\s+(?P<n5>[a-z]{3,})\s+(?:que|quem)\s+(?:recebeu|pegou|assinou|retirou)\b"
    r"|\bquem\s+(?:recebeu|vai\s+receber|recebe|pegou|assinou|retirou|vai\s+pegar)\s+(?:foi|e|eh"
    r"|sera|vai\s+ser|e\s+(?:o|a))\s+(?:o\s+|a\s+|seu\s+|dona\s+|sr\.?\s+|sra\.?\s+)?"
    r"(?P<n6>[a-z]{3,})"
    r"|\b(?:entregar|entrega|entregue|mandar|manda|mande|enviar|envia|envie|deixar|deixa|deixe"
    r"|deixaram|entregaram)\s+(?:\w+\s+){0,2}?(?:pro|pra|para\s+(?:o|a)|com\s+(?:o|a)|ao|a)\s+"
    r"(?P<n7>[a-z]{3,})"
    r"|\bnome\s+d[oa]\s+(?:destinatari[oa]|recebedor|quem\s+(?:vai\s+)?recebe\w*)\s+(?:e|eh)\s+"
    r"(?P<n8>[a-z]{3,})"
    r"|\b(?:no|em|com\s+o|pelo)\s+nome\s+d[aeo]s?\s+(?P<n9>[a-z]{3,})"
    r"|\b(?:pedido|encomenda|pacote|compra|presente)\s+(?:e|eh|foi|sera)\s+(?:pra|para|pro)\s+"
    r"(?:o\s+|a\s+)?(?P<n10>[a-z]{3,})"
    r"|\b(?:porteiro|porteira|zelador|zeladora|sindico|sindica|recepcionista|seguranca)\s+"
    r"(?P<n11>[a-z]{3,})"
    r"|\b(?:sr|sra|srta|dr|dra)\.?\s+(?P<n12>[a-z]{3,})"
)


def tem_dado_pessoal(texto: str | None) -> bool:
    """O texto de um par tem dado pessoal de terceiro ou endereço? PURA (revisão 2 de 20/10/2026).

    A régua de antes (com maiúscula, sobre o texto cru) e a sem maiúscula
    (sobre o plano): "meu marido roberto", "entregue para joana", "rua das
    flores", "dona maria".
    """
    cru = texto or ""
    if _DADO_PESSOAL_DO_PAR.search(cru):
        return True
    plano = " ".join(validador.plano_de(cru).split())
    for m in _DADO_PESSOAL_PLANO.finditer(plano):
        nome = next(
            (m.group(k) for k in (f"n{i}" for i in range(1, 13)) if m.group(k)),
            None,
        )
        if nome is None or nome not in _COMUNS_DEPOIS_DO_NOME:
            return True
    return False


def _filtro(
    cliente: str,
    resposta: str,
    *,
    plataforma: str,
    canal: str,
    contagem: dict[str, int] | None,
) -> bool:
    """O par passa nos filtros? (textos crus, antes da máscara, como no legado)"""
    if len(cliente) < MIN_CHARS_TURNO or len(resposta) < MIN_CHARS_TURNO:
        _somar(contagem, "curtos")
        return False
    if tem_dado_pessoal(cliente) or tem_dado_pessoal(resposta):
        _somar(contagem, "dado_pessoal")
        return False
    if not ia._par_seguro(cliente, resposta):
        _somar(contagem, "inseguros")
        return False
    if ia._ensina_o_proibido(resposta, plataforma, canal):
        _somar(contagem, "proibidos")
        return False
    return True


def pares_da_conversa(
    mensagens: Sequence[Any],
    *,
    conversa: Any,
    nomes: tuple[str | None, ...],
    autores_admin: frozenset[UUID] = frozenset(),
    repetidas: Collection[str] = frozenset(),
    fontes_por_rascunho: Mapping[UUID, str] | None = None,
    correcoes: Sequence[Correcao] = (),
    contagem: dict[str, int] | None = None,
) -> list[ParNovo]:
    """Os pares de UMA conversa (mensagens em ordem), já filtrados e generalizados. PURA.

    `nomes` = os nomes a mascarar (o comprador e o destinatário dos pedidos
    dele: `ia._NomesDoPedido`); `autores_admin` = os `users.id` admin entre
    os autores; `repetidas` = as chaves (`ia.chave_modelo`) do texto que a
    loja repete em 5+ conversas (`ia.modelos_automaticos`);
    `fontes_por_rascunho` = rascunho_id → `davinci_igual`/`davinci_editou`
    (a avaliação do rascunho que a pessoa mandou); `correcoes` = as
    avaliações negativas com correção; `contagem` = soma os descartes por
    motivo (o resumo do script).
    """
    fontes_por_rascunho = fontes_por_rascunho or {}
    plataforma = conversa.plataforma
    canal = conversa.canal
    comprador = (getattr(conversa, "comprador_nome", None), getattr(conversa, "comprador_id", None))
    do_cliente = [m for m in mensagens if m.autor == constantes.AUTOR_CLIENTE]
    if _tem_injecao([m.texto for m in do_cliente]):
        _somar(contagem, "injecao")
        return []
    turnos = _turnos([m for m in mensagens if _entra(m, repetidas, comprador)])
    item_id = (getattr(conversa, "anuncio_id", None) or None) and str(conversa.anuncio_id)[:64]
    pares: list[ParNovo] = []
    turno_do_cliente: dict[UUID, _Turno] = {}

    def _par(
        cliente: _Turno,
        resposta_texto: str,
        *,
        chave: UUID,
        fonte: str,
        admin: bool,
        respondido_em: datetime | None,
    ) -> None:
        _somar(contagem, "candidatos")
        pergunta_crua = cliente.texto
        if not _filtro(
            pergunta_crua, resposta_texto, plataforma=plataforma, canal=canal, contagem=contagem
        ):
            return
        if respondido_em is None:
            _somar(contagem, "vazios")
            return
        pergunta = generalizar(pergunta_crua, nomes)
        resposta = generalizar(resposta_texto, nomes)
        if not pergunta or not resposta:
            _somar(contagem, "vazios")
            return
        pares.append(
            ParNovo(
                conversa_id=conversa.id,
                resposta_msg_id=chave,
                plataforma=plataforma,
                integration_id=getattr(conversa, "integration_id", None),
                canal_id=getattr(conversa, "canal_id", None),
                item_id=item_id,
                assunto=(ia.categoria_provavel(pergunta_crua, canal) or None),
                pergunta_gen=pergunta,
                resposta_gen=resposta,
                fonte=fonte,
                peso=PESOS_FONTE[fonte],
                autor_admin=admin,
                perguntado_em=_momento(cliente.mensagens[-1]),
                respondido_em=respondido_em,
            )
        )

    for i, turno in enumerate(turnos):
        if turno.autor != constantes.AUTOR_CLIENTE:
            continue
        for m in turno.mensagens:
            turno_do_cliente[m.id] = turno
        if i + 1 >= len(turnos) or turnos[i + 1].autor != constantes.AUTOR_LOJA:
            continue
        equipe = turnos[i + 1].mensagens
        if any(getattr(m, "origem", None) not in _ORIGENS_DE_PESSOA for m in equipe):
            # A resposta do agente/IA enviada SOZINHA nunca vira par (nem o
            # que a pessoa mandou logo depois dela, no mesmo turno).
            _somar(contagem, "da_ia")
            continue
        primeira = equipe[0]
        fonte = (
            constantes.PAR_FONTE_EXTERNO
            if primeira.origem == constantes.ORIGEM_EXTERNO
            else fontes_por_rascunho.get(
                getattr(primeira, "rascunho_id", None), constantes.PAR_FONTE_HUMANO
            )
        )
        _par(
            turno,
            turnos[i + 1].texto,
            chave=primeira.id,
            fonte=fonte,
            admin=any(getattr(m, "autor_user_id", None) in autores_admin for m in equipe),
            respondido_em=_momento(primeira),
        )
    for c in correcoes:
        cliente = turno_do_cliente.get(c.gatilho_id)
        if cliente is None:
            continue
        _par(
            cliente,
            " ".join((c.texto or "").split()),
            chave=c.gatilho_id,
            fonte=constantes.PAR_FONTE_CORRECAO,
            admin=c.autor_admin,
            respondido_em=c.em,
        )
    # Uma linha por chave (duas correções de rascunhos do mesmo gatilho: vale a
    # última) — o upsert de várias linhas não aceita a mesma chave duas vezes.
    por_chave = {p.resposta_msg_id: p for p in sorted(pares, key=lambda p: p.respondido_em)}
    return sorted(por_chave.values(), key=lambda p: (p.respondido_em, str(p.resposta_msg_id)))


# ── Indexação ─────────────────────────────────────────────────────────────


def _linha(p: ParNovo) -> dict[str, Any]:
    return {
        "conversa_id": p.conversa_id,
        "resposta_msg_id": p.resposta_msg_id,
        "plataforma": p.plataforma,
        "integration_id": p.integration_id,
        "canal_id": p.canal_id,
        "item_id": p.item_id,
        "assunto": (p.assunto or None) and p.assunto[:32],
        "pergunta_gen": p.pergunta_gen,
        "resposta_gen": p.resposta_gen,
        "fonte": p.fonte,
        "peso": p.peso,
        "autor_admin": p.autor_admin,
        "perguntado_em": p.perguntado_em,
        "respondido_em": p.respondido_em,
        "tsv": busca_texto.tsv_de(p.pergunta_gen, p.resposta_gen),
    }


async def _gravar(session: AsyncSession, pares: Sequence[ParNovo]) -> None:
    """Upsert por (conversa_id, resposta_msg_id), no Core (com o `tsv` calculado)."""
    if not pares:
        return
    stmt = pg_insert(AtendimentoPar).values([_linha(p) for p in pares])
    colunas = (
        "plataforma",
        "integration_id",
        "canal_id",
        "item_id",
        "assunto",
        "pergunta_gen",
        "resposta_gen",
        "fonte",
        "peso",
        "autor_admin",
        "perguntado_em",
        "respondido_em",
        "tsv",
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["conversa_id", "resposta_msg_id"],
            set_={**{c: stmt.excluded[c] for c in colunas}, "updated_at": func.now()},
        )
    )


async def _conversas_a_indexar(
    session: AsyncSession, inicio: datetime, limite: int
) -> list[tuple[UUID, datetime]]:
    """As conversas com fala desde `inicio` (ou correção nova), a mais antiga antes."""
    c = AtendimentoConversa
    ultima = func.coalesce(c.ultima_mensagem_em, c.updated_at)
    com_correcao = (
        select(AtendimentoRascunho.conversa_id)
        .join(AtendimentoAvaliacao, AtendimentoAvaliacao.rascunho_id == AtendimentoRascunho.id)
        .where(
            AtendimentoAvaliacao.nota == constantes.NOTA_ERRO,
            AtendimentoAvaliacao.correcao.is_not(None),
            AtendimentoAvaliacao.updated_at >= inicio,
        )
    )
    linhas = (
        await session.execute(
            select(c.id, ultima.label("ultima"))
            .where(or_(ultima >= inicio, c.id.in_(com_correcao)))
            .order_by(ultima.asc(), c.id)
            .limit(limite)
        )
    ).all()
    return [(linha.id, linha.ultima) for linha in linhas]


async def _mensagens(session: AsyncSession, conversa_id: UUID) -> list[Any]:
    m = AtendimentoMensagem
    momento = func.coalesce(m.enviada_em, m.created_at)
    linhas = (
        await session.execute(
            select(
                m.id,
                m.autor,
                m.origem,
                m.tipo,
                m.texto,
                m.payload,
                m.status,
                m.enviada_em,
                m.created_at,
                m.rascunho_id,
                m.autor_user_id,
            )
            .where(m.conversa_id == conversa_id)
            .order_by(momento.desc(), m.created_at.desc())
            .limit(MAX_MENSAGENS_CONVERSA)
        )
    ).all()
    return list(reversed(linhas))


async def _avaliacoes(
    session: AsyncSession, conversa_ids: Sequence[UUID]
) -> tuple[dict[UUID, dict[UUID, str]], dict[UUID, list[Correcao]]]:
    """Por conversa: rascunho_id → fonte (igual/editou) e as correções."""
    r, a = AtendimentoRascunho, AtendimentoAvaliacao
    linhas = (
        await session.execute(
            select(
                r.id,
                r.conversa_id,
                r.mensagem_gatilho_id,
                a.acao,
                a.nota,
                a.correcao,
                a.updated_at,
                User.role,
            )
            .join(a, a.rascunho_id == r.id)
            .outerjoin(User, User.id == a.user_id)
            .where(r.conversa_id.in_(conversa_ids))
        )
    ).all()
    fontes: dict[UUID, dict[UUID, str]] = {}
    correcoes: dict[UUID, list[Correcao]] = {}
    for linha in linhas:
        if linha.acao in _FONTE_DA_ACAO:
            fontes.setdefault(linha.conversa_id, {})[linha.id] = _FONTE_DA_ACAO[linha.acao]
        texto = (linha.correcao or "").strip()
        if linha.nota == constantes.NOTA_ERRO and texto and linha.mensagem_gatilho_id:
            correcoes.setdefault(linha.conversa_id, []).append(
                Correcao(
                    gatilho_id=linha.mensagem_gatilho_id,
                    texto=texto,
                    autor_admin=linha.role == UserRole.ADMIN,
                    em=linha.updated_at,
                )
            )
    return fontes, correcoes


async def _com_injecao(session: AsyncSession, conversa_ids: Sequence[UUID]) -> set[UUID]:
    """As conversas com rascunho marcado "parece instrução para a IA" (fora inteiras)."""
    r = AtendimentoRascunho
    return set(
        (
            await session.scalars(
                select(r.conversa_id)
                .where(r.conversa_id.in_(conversa_ids), r.motivo.contains(ia.MOTIVO_INJECAO))
                .distinct()
            )
        ).all()
    )


async def _admins(session: AsyncSession, ids: Collection[UUID]) -> frozenset[UUID]:
    if not ids:
        return frozenset()
    return frozenset(
        (
            await session.scalars(
                select(User.id).where(User.id.in_(sorted(ids)), User.role == UserRole.ADMIN)
            )
        ).all()
    )


async def _existentes(session: AsyncSession, conversa_ids: Sequence[UUID]) -> set[tuple]:
    p = AtendimentoPar
    return {
        (linha.conversa_id, linha.resposta_msg_id)
        for linha in (
            await session.execute(
                select(p.conversa_id, p.resposta_msg_id).where(p.conversa_id.in_(conversa_ids))
            )
        ).all()
    }


async def indexar(
    session: AsyncSession,
    *,
    desde: datetime | None = None,
    limite_conversas: int = 500,
    gravar: bool = True,
) -> dict[str, int]:
    """Indexa os pares novos (incremental a partir do último `respondido_em`, ou `desde`).

    Upsert por (conversa_id, resposta_msg_id) com `tsv=busca_texto.tsv_de(
    pergunta_gen, resposta_gen)` no Core; a conversa processada perde o par
    que deixou de valer; com `gravar`, também o expurgo da retenção. Só
    `flush` (os comandos do Core já vão ao banco); quem chama faz commit.
    `gravar=False` (o `--dry-run`): só lê e conta.

    Devolve {"conversas", "pares", "novos", "removidos", "descartados",
    "expurgados", "ate_ts", "mais"} e as contagens por motivo de descarte;
    `ate_ts` = o instante (epoch) da última conversa processada e `mais` = 1
    quando bateu no `limite_conversas` (o script continua dali).
    """
    t0 = time.monotonic()
    agora = datetime.now(UTC)
    corte = agora - timedelta(days=RETENCAO_DIAS)
    if desde is None:
        ultimo = await session.scalar(select(func.max(AtendimentoPar.respondido_em)))
        desde = (ultimo - MARGEM_INCREMENTAL) if ultimo is not None else agora - JANELA_SEM_PARES
    if desde.tzinfo is None:
        desde = desde.replace(tzinfo=UTC)
    inicio = max(desde, corte)
    alvos = await _conversas_a_indexar(session, inicio, max(1, limite_conversas))
    contagem: dict[str, int] = {}
    resumo = {
        "conversas": 0,
        "pares": 0,
        "novos": 0,
        "removidos": 0,
        "descartados": 0,
        "expurgados": 0,
        "ate_ts": 0,
        "mais": int(len(alvos) >= max(1, limite_conversas)),
    }
    nomes_svc = ia._NomesDoPedido(session)
    repetidas_por_loja: dict[UUID, frozenset[str]] = {}
    for i in range(0, len(alvos), LOTE_CONVERSAS):
        lote = alvos[i : i + LOTE_CONVERSAS]
        ids = [cid for cid, _ in lote]
        conversas = {
            c.id: c
            for c in (
                await session.scalars(
                    select(AtendimentoConversa).where(AtendimentoConversa.id.in_(ids))
                )
            ).all()
        }
        injecao = await _com_injecao(session, ids)
        fontes, correcoes = await _avaliacoes(session, ids)
        existentes = await _existentes(session, ids)
        novos_pares: list[ParNovo] = []
        manter: dict[UUID, set[UUID]] = {cid: set() for cid in ids}
        for cid in ids:
            conversa = conversas.get(cid)
            if conversa is None:
                continue
            resumo["conversas"] += 1
            if cid in injecao:
                _somar(contagem, "injecao")
                continue
            mensagens = await _mensagens(session, cid)
            if not any(m.autor == constantes.AUTOR_LOJA for m in mensagens) and cid not in (
                correcoes
            ):
                continue
            if conversa.integration_id is not None:
                if conversa.integration_id not in repetidas_por_loja:
                    repetidas_por_loja[conversa.integration_id] = await ia.modelos_automaticos(
                        session, conversa.integration_id
                    )
                repetidas = repetidas_por_loja[conversa.integration_id]
            else:
                repetidas = frozenset()
            nomes = await nomes_svc.de(
                conversa.comprador_nome,
                conversa.comprador_id,
                conversa.pedido_marketplace,
                conversa.integration_id,
            )
            autores = {m.autor_user_id for m in mensagens if m.autor_user_id is not None}
            pares = pares_da_conversa(
                mensagens,
                conversa=conversa,
                nomes=nomes,
                autores_admin=await _admins(session, autores),
                repetidas=repetidas,
                fontes_por_rascunho=fontes.get(cid),
                correcoes=correcoes.get(cid, ()),
                contagem=contagem,
            )
            # Só o que está dentro da retenção.
            pares = [p for p in pares if p.respondido_em >= corte]
            novos_pares += pares
            manter[cid] = {p.resposta_msg_id for p in pares}
        resumo["pares"] += len(novos_pares)
        resumo["novos"] += sum(
            1 for p in novos_pares if (p.conversa_id, p.resposta_msg_id) not in existentes
        )
        sair = [(cid, msg) for cid, msg in existentes if msg not in manter.get(cid, set())]
        resumo["removidos"] += len(sair)
        if gravar:
            for cid in {cid for cid, _ in sair}:
                await session.execute(
                    delete(AtendimentoPar).where(
                        AtendimentoPar.conversa_id == cid,
                        AtendimentoPar.resposta_msg_id.in_([msg for c2, msg in sair if c2 == cid]),
                    )
                )
            await _gravar(session, novos_pares)
            await session.flush()
    if alvos:
        resumo["ate_ts"] = int(alvos[-1][1].timestamp()) if alvos[-1][1] else 0
    if gravar:
        resumo["expurgados"] = await expurgar(session, agora=agora)
    resumo["descartados"] = sum(v for k, v in contagem.items() if k != "candidatos")
    logger.info(
        "atendimento_pares_indexados",
        **resumo,
        ms=int((time.monotonic() - t0) * 1000),
        gravar=gravar,
    )
    return {**resumo, **{f"descartados_{k}": v for k, v in contagem.items() if k != "candidatos"}}


async def indexar_conversa(session: AsyncSession, conversa: AtendimentoConversa) -> int:
    """Refaz AGORA os pares DESTA conversa — o corpo por conversa do `indexar`.

    O laço de correções na hora (spec 5.5, 20/10/2026): a sugestão que a
    pessoa mandou (`davinci_igual`/`davinci_editou`) e o 👎 com correção
    (`correcao`) entram já, e o que deixou de valer (a correção apagada, a
    nota que virou 👍) sai — as MESMAS peças do cron (montagem, filtros,
    máscara, retenção; a conversa com cara de instrução para a IA não tem
    par). Quem chama: `enviar.indexar_pares_da_conversa`. Só flush; quem
    chama faz commit. Devolve quantos pares a conversa ficou tendo.
    """
    cid = conversa.id
    pares: list[ParNovo] = []
    if cid not in await _com_injecao(session, [cid]):
        mensagens = await _mensagens(session, cid)
        fontes, correcoes = await _avaliacoes(session, [cid])
        repetidas = (
            await ia.modelos_automaticos(session, conversa.integration_id)
            if conversa.integration_id is not None
            else frozenset()
        )
        nomes = await ia._NomesDoPedido(session).de(
            conversa.comprador_nome,
            conversa.comprador_id,
            conversa.pedido_marketplace,
            conversa.integration_id,
        )
        autores = {m.autor_user_id for m in mensagens if m.autor_user_id is not None}
        corte = datetime.now(UTC) - timedelta(days=RETENCAO_DIAS)
        pares = [
            p
            for p in pares_da_conversa(
                mensagens,
                conversa=conversa,
                nomes=nomes,
                autores_admin=await _admins(session, autores),
                repetidas=repetidas,
                fontes_por_rascunho=fontes.get(cid),
                correcoes=correcoes.get(cid, ()),
            )
            if p.respondido_em >= corte
        ]
    manter = [p.resposta_msg_id for p in pares]
    sair = delete(AtendimentoPar).where(AtendimentoPar.conversa_id == cid)
    if manter:
        sair = sair.where(AtendimentoPar.resposta_msg_id.not_in(manter))
    await session.execute(sair)
    await _gravar(session, pares)
    await session.flush()
    return len(pares)


async def expurgar(session: AsyncSession, *, agora: datetime | None = None) -> int:
    """Apaga os pares com `respondido_em` além da retenção (180 dias). Devolve quantos."""
    corte = (agora or datetime.now(UTC)) - timedelta(days=RETENCAO_DIAS)
    resultado = await session.execute(
        delete(AtendimentoPar).where(AtendimentoPar.respondido_em < corte)
    )
    return int(resultado.rowcount or 0)


async def expurgar_dado_pessoal(
    session: AsyncSession, *, gravar: bool = True, lote: int = 2000
) -> dict[str, int]:
    """Apaga os pares já gravados que a régua de dado pessoal de hoje
    (`tem_dado_pessoal`) tiraria (revisão 2 de 20/10/2026: a de antes exigia
    maiúscula). Lê em lotes pelo id; só `flush` (quem chama faz commit).
    `gravar=False`: só conta. Devolve {"lidos", "apagados"}.
    """
    p = AtendimentoPar
    lidos = apagados = 0
    ultimo: UUID | None = None
    while True:
        consulta = select(p.id, p.pergunta_gen, p.resposta_gen).order_by(p.id).limit(lote)
        if ultimo is not None:
            consulta = consulta.where(p.id > ultimo)
        linhas = (await session.execute(consulta)).all()
        if not linhas:
            break
        ultimo = linhas[-1].id
        lidos += len(linhas)
        sair = [
            linha.id
            for linha in linhas
            if tem_dado_pessoal(linha.pergunta_gen) or tem_dado_pessoal(linha.resposta_gen)
        ]
        apagados += len(sair)
        if sair and gravar:
            await session.execute(delete(p).where(p.id.in_(sair)))
            await session.flush()
        if len(linhas) < lote:
            break
    return {"lidos": lidos, "apagados": apagados}


# ── Busca ─────────────────────────────────────────────────────────────────


def _cortar(texto: str | None) -> str:
    t = " ".join(str(texto or "").split())
    return t if len(t) <= MAX_CHARS_PAR else t[: MAX_CHARS_PAR - 1].rstrip() + "…"


def pontuar(
    rank: float, peso: float, *, mesmo_anuncio: bool, mesma_loja: bool, idade_dias: float
) -> float:
    """A nota de um par no ranking. PURA.

    `ts_rank_cd` × peso da fonte × (1 + bônus de mesmo anúncio e mesma loja)
    × decaimento pela idade (meia-vida de `MEIA_VIDA_DIAS`).
    """
    bonus = 1.0 + (BONUS_MESMO_ANUNCIO if mesmo_anuncio else 0.0)
    bonus += BONUS_MESMA_LOJA if mesma_loja else 0.0
    return max(0.0, rank) * peso * bonus * 0.5 ** (max(0.0, idade_dias) / MEIA_VIDA_DIAS)


async def buscar(
    session: AsyncSession,
    *,
    pergunta: str,
    plataforma: str,
    integration_id: UUID | None,
    item_id: str | None,
    conversa_id: UUID,
    as_of: datetime,
    canal_no_automatico: bool,
    limite: int = MAX_PARES,
) -> list[dict]:
    """Os exemplos para `buscar_respostas_equipe`.

    Cada um: {"id", "pergunta", "resposta", "fonte", "mesma_loja",
    "mesmo_anuncio", "idade_dias"} (`id` só para o rastro, como `par:<id>`).
    """
    qualquer = busca_texto.consulta_de(pergunta)
    if qualquer is None or limite <= 0:
        return []
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    todas = busca_texto.consulta_de(pergunta, todas=True)
    p = AtendimentoPar
    mesma_loja = p.integration_id == integration_id if integration_id is not None else false()
    item = (item_id or "").strip()
    mesmo_anuncio = p.item_id == item if item else false()
    casa_todas = p.tsv.op("@@")(todas) if todas is not None else false()
    rank = func.ts_rank_cd(p.tsv, qualquer)
    correcao_sem_admin = and_(p.fonte == constantes.PAR_FONTE_CORRECAO, p.autor_admin.is_(False))
    if canal_no_automatico:
        # O que sai sozinho só aprende com a loja dela ou com quem é admin.
        escopo = or_(p.autor_admin.is_(True), and_(mesma_loja, ~correcao_sem_admin))
    else:
        escopo = or_(~correcao_sem_admin, mesma_loja)
    linhas = (
        await session.execute(
            select(
                p.id,
                p.pergunta_gen,
                p.resposta_gen,
                p.fonte,
                p.peso,
                p.respondido_em,
                rank.label("rank"),
                case((casa_todas, True), else_=False).label("todas"),
                case((mesma_loja, True), else_=False).label("mesma_loja"),
                case((mesmo_anuncio, True), else_=False).label("mesmo_anuncio"),
            )
            .where(
                p.plataforma == plataforma,
                p.conversa_id != conversa_id,
                p.respondido_em < as_of,
                p.respondido_em >= as_of - timedelta(days=RETENCAO_DIAS),
                p.tsv.op("@@")(qualquer),
                escopo,
            )
            .order_by(
                case((casa_todas, 0), else_=1),
                (rank * p.peso).desc(),
                p.respondido_em.desc(),
                p.id,
            )
            .limit(max(CANDIDATOS_BUSCA, limite * 4))
        )
    ).all()
    candidatos = []
    for linha in linhas:
        idade = max(0.0, (as_of - linha.respondido_em).total_seconds() / 86400)
        nota = pontuar(
            float(linha.rank or 0.0),
            float(linha.peso or 1.0),
            mesmo_anuncio=bool(linha.mesmo_anuncio),
            mesma_loja=bool(linha.mesma_loja),
            idade_dias=idade,
        )
        candidatos.append((not linha.todas, -nota, linha.id, linha, idade))
    candidatos.sort(key=lambda c: c[:3])
    saida: list[dict] = []
    vistas: set[str] = set()
    for _sem_todas, _nota, _id, linha, idade in candidatos:
        if tem_dado_pessoal(linha.pergunta_gen) or tem_dado_pessoal(linha.resposta_gen):
            # O par gravado antes da régua sem maiúscula (revisão 2 de
            # 20/10/2026) nunca vai ao provedor — `expurgar_dado_pessoal` o apaga.
            continue
        resposta = _cortar(linha.resposta_gen)
        chave = resposta.casefold()
        if chave in vistas:
            continue
        vistas.add(chave)
        saida.append(
            {
                "id": linha.id,
                "pergunta": _cortar(linha.pergunta_gen),
                "resposta": resposta,
                "fonte": linha.fonte,
                "mesma_loja": bool(linha.mesma_loja),
                "mesmo_anuncio": bool(linha.mesmo_anuncio),
                "idade_dias": int(idade),
            }
        )
        if len(saida) >= limite:
            break
    return saida
