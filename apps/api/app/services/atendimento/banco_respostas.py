"""O BANCO DE RESPOSTAS do agente (13/11/2026): o que a equipe respondeu, por tipo de pergunta.

O Eduardo, 13/11/2026: "no banco entra só as q a pessoa respondeu ou ajustou", e "em
prod é a mesma coisa do Obsidian". Cada linha de `atendimento_respostas` é um turno do
cliente e a resposta que uma PESSOA escreveu (DaVinci, Duoke, Seller Center) ou ajustou
a partir do rascunho da IA. O que o agente mandou sozinho e o rascunho enviado sem mexer
NÃO entram (só contam ponto na régua do tipo): o agente nunca aprende com ele mesmo.

Montagem (`respostas_da_conversa`, PURA), a mesma régua da colheita de 13/11/2026:

- as falas são as do `agente.pares` (`_entra`): sem nota interna, sem mensagem que
  falhou, sem as automáticas e sem o texto que a loja repete igual em 5+ conversas
  (aqui contado no histórico inteiro, não só em 90 dias); turno INTEIRO do cliente ×
  turno INTEIRO da loja logo depois;
- a conversa inteira fica fora com cara de instrução para a IA (no texto do cliente
  ou num rascunho marcado `ia.MOTIVO_INJECAO`);
- fora: o turno com resposta da IA, o rascunho enviado igual, o turno do cliente só
  com cartão ou só cortesia, o que tem cara de dado pessoal (antes E depois da
  máscara), o que ensina o que o agente não faz, a resposta só de cumprimento, a de um
  pedido só (status, código, data), a que manda "ver no anúncio" ou pergunta de volta,
  e a resposta curta demais (menos de 2 palavras ou 6 letras);
- só a versão generalizada é guardada (`pares.generalizar`: `ia.mascarar` com os
  nomes do pedido e do comprador, depois `_generalizar`), até 600 caracteres;
- o TIPO é o assunto que o agente declarou no rascunho daquele turno; sem rascunho
  (histórico, resposta de fora), a palavra-chave (`tipos_pergunta.tipo_de`);
- os MODELOS vêm do título do anúncio (`recursos_equipe.codigos_do`): o mesmo modelo é
  vendido em vários anúncios e lojas;
- `cliente_depois`: o que o cliente fez em até 72 h (agradeceu, seguiu, voltou
  reclamando ou repetindo a pergunta, não escreveu mais) — sinal aproximado.

Indexação (`indexar`): pelas conversas com fala desde o cursor (Redis, com a margem de
6 h; sem cursor, a resposta mais nova do banco; banco vazio, o histórico inteiro), em
lotes, com teto de conversas (40 mil) e de tempo (5 min) por rodada: a primeira carga
(o histórico inteiro, ~30 mil conversas; a colheita de 13/11 leu tudo em 83 s) cabe numa
rodada, e o que sobrar segue na próxima. Upsert pela `chave` (a conversa e a 1ª
mensagem da resposta); a resposta de uma conversa processada que deixou de valer vira
`aposentada` (nunca é apagada). Conflito entre respostas (a mais nova vale) fica para
a revisão do dia, com o juiz: por enquanto todas ficam `ativa`, e quem lê ordena pela
data.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import String, case, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import (
    AtendimentoAvaliacao,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
)
from app.models.atendimento import AtendimentoResposta
from app.models.product import ProductLink
from app.services.atendimento import (
    busca_texto,
    constantes,
    ia,
    recursos_equipe,
    tipos_pergunta,
    validador,
)
from app.services.atendimento.agente import pares

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

FONTE_FORA = "equipe_fora"
FONTE_ESCREVEU = "equipe_escreveu"
FONTE_AJUSTOU = "equipe_ajustou"
# A virada (13/11/2026, o Eduardo: "mandei — já foi pro banco pra próxima?"): a sugestão da IA que
# a pessoa mandou SEM mudar também é resposta que a equipe assinou embaixo — entra no banco, para a
# próxima pergunta igual achar o caso e sair sozinha.
FONTE_APROVOU = "equipe_aprovou"
STATUS_ATIVA = "ativa"
STATUS_SUBSTITUIDA = "substituida"
STATUS_APOSENTADA = "aposentada"
DEPOIS_RESOLVEU = "resolveu"
DEPOIS_CONTINUOU = "continuou"
DEPOIS_NAO_RESOLVEU = "nao_resolveu"
DEPOIS_SEM_RETORNO = "sem_retorno"

# As plataformas com resposta de pessoa no comunicador (o Zap fica de fora: a conta
# dele é o número da loja, e a IA não entra lá — premissa P8).
PLATAFORMAS = ("shopee", "ml", "tiktok", "amazon", "magalu", "carrefour", "temu", "aliexpress")
MIN_CHARS_RESPOSTA = 6
MIN_PALAVRAS_RESPOSTA = 2
JANELA_DEPOIS_H = 72
MAX_MENSAGENS_CONVERSA = 600
LOTE_CONVERSAS = 150
MAX_CONVERSAS_RODADA = 40000
TEMPO_MAX_RODADA_S = 300.0
MARGEM = timedelta(hours=6)
INICIO_HISTORICO = datetime(2024, 1, 1, tzinfo=UTC)
CHAVE_CURSOR = "atd:banco_respostas:cursor"

_ORIGENS_DE_PESSOA = frozenset({constantes.ORIGEM_EXTERNO, constantes.ORIGEM_HUMANO})
_ACAO_ENVIOU_IGUAL = "enviou_igual"
_ACAO_EDITOU = "editou"

# A resposta que não responde: manda ver o anúncio ou pergunta de volta.
_FRACA = re.compile(
    r"(verificar\s+na\s+descric|esta\s+(na|no)\s+(descric|anuncio)|(informad|descrit)\w*\s+no\s+anuncio"
    r"|no\s+anuncio\s+(esta|tem|consta|informa)|conforme\s+(o\s+)?anuncio|olhar?\s+(o\s+)?anuncio"
    r"|consta\s+no\s+anuncio|ver\s+no\s+anuncio|qual\s+(e\s+)?a\s+duvida|em\s+que\s+posso\s+ajudar"
    r"|como\s+posso\s+ajudar|pode\s+falar|pois\s+nao)"
)
# Guarda extra, além da régua do DaVinci: resto de contato, documento ou endereço.
_RESTO = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+|@\w{3,}|\d[\d .-]{7,}\d|\b\d{5}-?\d{3}\b"
    r"|\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|https?://\S*(wa\.me|whatsapp|instagram)"
    r"|\b(rua|avenida|av\.|apto|apartamento|bloco|cep)\b"
    r"|\[(CARTAO|CPF|CNPJ|TELEFONE|EMAIL|ENDERECO|NUMERO_PESSOAL|CEP)\]",
    re.IGNORECASE,
)
# O que o cliente escreve quando a resposta não resolveu (o "voltou reclamando").
_RECLAMA = re.compile(
    r"(absurdo|pessim|ridicul|vergonha|descaso|enganad|mentir|golpe|lixo|procon|reclame\s*aqui"
    r"|(abrir|abri|vou\s+abrir|fazer)\s+(uma\s+)?reclamac|denunci|processar|advogad|justica"
    r"|palhacada|ninguem\s+(me\s+)?(responde|resolve|ajuda)|nao\s+(me\s+)?(respond|resolv|ajud)\w*"
    r"|ja\s+(falei|disse|perguntei|expliquei|mandei)|de\s+novo|novamente|ate\s+agora|cade\b"
    r"|\be\s+ai\s*\?|\?\?\?|nao\s+entendi|nao\s+foi\s+isso|nao\s+e\s+isso|nao\s+perguntei"
    r"|insatisf|decepcion|falta\s+de\s+respeito|vou\s+cancelar|quero\s+cancelar|cancela\b"
    r"|quero\s+(devolver|meu\s+dinheiro|reembolso|o\s+reembolso|estorno))"
)
_AGRADECE = re.compile(
    r"^\W*(muito\s+)?(obrigad\w*|obg|brigad\w*|vlw|valeu|agradec\w*|ok\b|okay|certo|blz|beleza"
    r"|perfeito|entendi|tudo\s+bem|show|top|maravilh\w*|otimo|combinado|ta\s+bom|ta\s+certo"
    r"|aguardo|vou\s+aguardar|chegou|recebi|deu\s+certo|consegui|resolvido|joia|tranquilo)"
)
_MASCARA = re.compile(r"\[[a-z_]+\]")
# Na mensagem curta e sem pergunta, o agradecimento vale em qualquer lugar ("ah, obrigada!").
_AGRADECE_SOLTO = re.compile(
    r"\b(obrigad\w*|obg|brigad\w*|vlw|valeu|agradec\w*|perfeito|entendi|combinado|ta\s+bom"
    r"|ta\s+certo|aguardo|vou\s+aguardar|deu\s+certo|consegui|resolvido|joia|tranquilo|beleza|blz"
    r"|show|otimo|maravilh\w*)\b"
)


@dataclass(frozen=True)
class RespostaNova:
    """Uma resposta pronta para gravar (já generalizada). Os campos de `AtendimentoResposta`."""

    chave: str
    # None só na correção da revisão diária (`resposta_da_correcao`, ver lá embaixo).
    conversa_id: UUID | None
    resposta_msg_id: UUID
    plataforma: str
    canal: str
    integration_id: UUID | None
    loja: str | None
    item_id: str | None
    anuncio_titulo: str | None
    modelos: tuple[str, ...]
    tipo: str
    pergunta: str
    resposta: str
    fonte: str
    perguntado_em: datetime | None
    respondido_em: datetime
    cliente_depois: str


@dataclass
class Rascunhos:
    """O que os rascunhos de UMA conversa dizem: a ação da pessoa e o assunto do agente."""

    acao_por_rascunho: dict[UUID, str] = field(default_factory=dict)
    assunto_por_gatilho: dict[UUID, str] = field(default_factory=dict)
    injecao: bool = False


# ── Regras puras ─────────────────────────────────────────────────────────


def _plano(texto: str | None) -> str:
    return " ".join(_MASCARA.sub(" ", validador.plano_de(texto or "")).split())


def seguro(texto: str | None) -> bool:
    """O texto (já generalizado) pode sair do DaVinci sem dado pessoal? PURA."""
    t = texto or ""
    return bool(t.strip()) and not (
        pares.tem_dado_pessoal(t) or pares.tem_mascara_de_dado(t) or _RESTO.search(t)
    )


def sem_conteudo(resposta: str | None) -> bool:
    """A resposta não ensina nada a outro cliente? (cumprimento, um pedido só, "veja o anúncio",
    fora da régua do agente, cara de instrução) PURA."""
    t = pares.sem_citacao(resposta)
    plano = _plano(t)
    return (
        pares.so_cumprimento(t)
        or pares.so_de_um_pedido(t)
        or bool(_FRACA.search(plano))
        or pares.ensina_o_que_o_agente_nao_faz(t)
        or bool(pares._FORA_DA_REGUA_DO_AGENTE.search(plano))
        or ia._tem_injecao(t, agente=True)
    )


def curta_demais(pergunta: str, resposta: str) -> bool:
    palavras = re.findall(r"[a-z0-9]+", _plano(resposta))
    return (
        len(resposta.strip()) < MIN_CHARS_RESPOSTA
        or len(palavras) < MIN_PALAVRAS_RESPOSTA
        or not re.search(r"[a-z0-9]{2,}", _plano(pergunta))
    )


def classe_depois(pergunta: str | None, depois: str | None, horas: float | None) -> str:
    """O que o cliente fez depois da resposta (até 72 h). PURA."""
    if depois is None or horas is None or horas > JANELA_DEPOIS_H:
        return DEPOIS_SEM_RETORNO
    plano = _plano(depois)
    if _RECLAMA.search(plano):
        return DEPOIS_NAO_RESOLVEU
    tq = set(re.findall(r"[a-z0-9]{3,}", _plano(pergunta)))
    td = set(re.findall(r"[a-z0-9]{3,}", plano))
    if tq and len(td) >= 3 and len(tq & td) / len(tq | td) >= 0.5:
        return DEPOIS_NAO_RESOLVEU  # repetiu a mesma pergunta
    if len(plano) <= 80 and (
        _AGRADECE.search(plano) or ("?" not in plano and _AGRADECE_SOLTO.search(plano))
    ):
        return DEPOIS_RESOLVEU
    return DEPOIS_CONTINUOU


def chave_de(conversa_id: UUID, resposta_msg_id: UUID) -> str:
    """A chave fixa da resposta (o `id` da nota no Obsidian). PURA."""
    bruto = f"{conversa_id}|{resposta_msg_id}".encode()
    return "r" + hashlib.sha1(bruto, usedforsecurity=False).hexdigest()[:15]


def modelos_do(titulo: str | None) -> tuple[str, ...]:
    if not titulo:
        return ()
    return tuple(sorted({c.upper() for c in recursos_equipe.codigos_do(titulo)}))


def _somar(contagem: dict[str, int] | None, chave: str) -> None:
    if contagem is not None:
        contagem[chave] = contagem.get(chave, 0) + 1


def respostas_da_conversa(
    mensagens: Sequence[Any],
    *,
    conversa: Any,
    nomes: tuple[str | None, ...],
    repetidas: Collection[str] = frozenset(),
    rascunhos: Rascunhos | None = None,
    titulo: str | None = None,
    contagem: dict[str, int] | None = None,
) -> list[RespostaNova]:
    """As respostas de pessoa de UMA conversa (em ordem), filtradas e generalizadas. PURA."""
    rascunhos = rascunhos or Rascunhos()
    if rascunhos.injecao:
        _somar(contagem, "injecao")
        return []
    if pares._tem_injecao([m.texto for m in mensagens if m.autor == constantes.AUTOR_CLIENTE]):
        _somar(contagem, "injecao")
        return []
    plataforma, canal = conversa.plataforma, conversa.canal
    comprador = (getattr(conversa, "comprador_nome", None), getattr(conversa, "comprador_id", None))
    turnos = pares._turnos([m for m in mensagens if pares._entra(m, repetidas, comprador)])
    item_id = (getattr(conversa, "anuncio_id", None) or None) and str(conversa.anuncio_id)[:64]
    titulo = titulo or getattr(conversa, "anuncio_titulo", None) or None
    modelos = modelos_do(titulo)
    saida: dict[str, RespostaNova] = {}
    for i, turno in enumerate(turnos):
        if turno.autor != constantes.AUTOR_CLIENTE:
            continue
        if i + 1 >= len(turnos) or turnos[i + 1].autor != constantes.AUTOR_LOJA:
            continue
        _somar(contagem, "candidatos")
        equipe = turnos[i + 1].mensagens
        if any(getattr(m, "origem", None) not in _ORIGENS_DE_PESSOA for m in equipe):
            _somar(contagem, "da_ia")
            continue
        primeira = equipe[0]
        if primeira.origem == constantes.ORIGEM_EXTERNO:
            fonte = FONTE_FORA
        else:
            acao = rascunhos.acao_por_rascunho.get(getattr(primeira, "rascunho_id", None))
            if acao == _ACAO_ENVIOU_IGUAL:
                _somar(contagem, "rascunho_igual")
                fonte = FONTE_APROVOU
            else:
                fonte = FONTE_AJUSTOU if acao == _ACAO_EDITOU else FONTE_ESCREVEU
        pergunta_crua = turno.texto
        resposta_crua = pares.sem_citacao(turnos[i + 1].texto)
        if not pergunta_crua.strip() or not resposta_crua.strip():
            _somar(contagem, "sem_texto")
            continue
        if pares.tem_dado_pessoal(pergunta_crua) or pares.tem_dado_pessoal(resposta_crua):
            _somar(contagem, "dado_pessoal")
            continue
        if not ia._par_seguro(pergunta_crua, resposta_crua):
            _somar(contagem, "inseguro")
            continue
        if ia._ensina_o_proibido(resposta_crua, plataforma, canal, de_pessoa=True):
            _somar(contagem, "proibido")
            continue
        pergunta = pares.generalizar(pergunta_crua, nomes)
        resposta = pares.generalizar(resposta_crua, nomes)
        if not seguro(pergunta) or not seguro(resposta):
            _somar(contagem, "dado_pessoal")
            continue
        if pares.so_cumprimento(pergunta):
            _somar(contagem, "so_cortesia")
            continue
        if sem_conteudo(resposta):
            _somar(contagem, "sem_conteudo")
            continue
        if curta_demais(pergunta, resposta):
            _somar(contagem, "curta")
            continue
        respondido_em = pares._momento(primeira)
        if respondido_em is None:
            _somar(contagem, "sem_data")
            continue
        assunto = None
        for m in turno.mensagens:
            assunto = rascunhos.assunto_por_gatilho.get(m.id) or assunto
        tipo = (assunto or tipos_pergunta.tipo_de(pergunta_crua))[:32]
        depois = turnos[i + 2] if i + 2 < len(turnos) else None
        horas = None
        if depois is not None:
            inicio, fim = pares._momento(depois.mensagens[0]), pares._momento(equipe[-1])
            horas = (inicio - fim).total_seconds() / 3600 if inicio and fim else None
        chave = chave_de(conversa.id, primeira.id)
        saida[chave] = RespostaNova(
            chave=chave,
            conversa_id=conversa.id,
            resposta_msg_id=primeira.id,
            plataforma=plataforma,
            canal=canal,
            integration_id=getattr(conversa, "integration_id", None),
            loja=((getattr(conversa, "conta", None) or "").strip() or None),
            item_id=item_id,
            anuncio_titulo=(titulo or None) and titulo[:300],
            modelos=modelos,
            tipo=tipo,
            pergunta=pergunta,
            resposta=resposta,
            fonte=fonte,
            perguntado_em=pares._momento(turno.mensagens[-1]),
            respondido_em=respondido_em,
            cliente_depois=classe_depois(
                pergunta_crua, depois.texto if depois is not None else None, horas
            ),
        )
        _somar(contagem, "respostas")
    return sorted(saida.values(), key=lambda r: (r.respondido_em, r.chave))


# ── Banco ────────────────────────────────────────────────────────────────


def _linha(r: RespostaNova) -> dict[str, Any]:
    return {
        "chave": r.chave,
        "conversa_id": r.conversa_id,
        "resposta_msg_id": r.resposta_msg_id,
        "plataforma": r.plataforma,
        "canal": r.canal,
        "integration_id": r.integration_id,
        "loja": r.loja,
        "item_id": r.item_id,
        "anuncio_titulo": r.anuncio_titulo,
        "modelos": list(r.modelos),
        "tipo": r.tipo,
        "pergunta": r.pergunta,
        "resposta": r.resposta,
        "fonte": r.fonte,
        "perguntado_em": r.perguntado_em,
        "respondido_em": r.respondido_em,
        "cliente_depois": r.cliente_depois,
        "tsv": busca_texto.tsv_de(r.pergunta, r.resposta),
    }


_COLUNAS_ATUALIZADAS = (
    "conversa_id",
    "resposta_msg_id",
    "plataforma",
    "canal",
    "integration_id",
    "loja",
    "item_id",
    "anuncio_titulo",
    "modelos",
    "tipo",
    "pergunta",
    "resposta",
    "fonte",
    "perguntado_em",
    "respondido_em",
    "cliente_depois",
    "tsv",
)


async def _gravar(session: AsyncSession, respostas: Sequence[RespostaNova]) -> None:
    """Upsert pela `chave`; a aposentada que voltou a valer volta a ativa (a substituída fica)."""
    if not respostas:
        return
    t = AtendimentoResposta
    stmt = pg_insert(t).values([_linha(r) for r in respostas])
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["chave"],
            set_={
                **{c: stmt.excluded[c] for c in _COLUNAS_ATUALIZADAS},
                "status": case((t.status == STATUS_SUBSTITUIDA, t.status), else_=STATUS_ATIVA),
                "updated_at": func.now(),
            },
        )
    )


async def _conversas(
    session: AsyncSession, desde: datetime, limite: int
) -> list[tuple[UUID, datetime]]:
    c = AtendimentoConversa
    ultima = func.coalesce(c.ultima_mensagem_em, c.updated_at)
    linhas = (
        await session.execute(
            select(c.id, ultima.label("ultima"))
            .where(ultima >= desde, c.plataforma.in_(PLATAFORMAS))
            .order_by(ultima.asc(), c.id)
            .limit(limite)
        )
    ).all()
    return [(linha.id, linha.ultima) for linha in linhas]


async def _mensagens(session: AsyncSession, ids: Sequence[UUID]) -> dict[UUID, list[Any]]:
    m = AtendimentoMensagem
    momento = func.coalesce(m.enviada_em, m.created_at)
    linhas = (
        await session.execute(
            select(
                m.id,
                m.conversa_id,
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
            .where(m.conversa_id.in_(ids))
            .order_by(m.conversa_id, momento, m.created_at)
        )
    ).all()
    por: dict[UUID, list[Any]] = defaultdict(list)
    for linha in linhas:
        por[linha.conversa_id].append(linha)
    return {cid: lista[-MAX_MENSAGENS_CONVERSA:] for cid, lista in por.items()}


async def _rascunhos(session: AsyncSession, ids: Sequence[UUID]) -> dict[UUID, Rascunhos]:
    r, a = AtendimentoRascunho, AtendimentoAvaliacao
    linhas = (
        await session.execute(
            select(r.id, r.conversa_id, r.mensagem_gatilho_id, r.categoria, r.motivo, a.acao)
            .outerjoin(a, a.rascunho_id == r.id)
            .where(r.conversa_id.in_(ids))
            .order_by(r.created_at)
        )
    ).all()
    por: dict[UUID, Rascunhos] = defaultdict(Rascunhos)
    for linha in linhas:
        info = por[linha.conversa_id]
        if linha.acao:
            info.acao_por_rascunho[linha.id] = linha.acao
        if linha.categoria and linha.mensagem_gatilho_id:
            info.assunto_por_gatilho[linha.mensagem_gatilho_id] = linha.categoria
        if ia.MOTIVO_INJECAO in (linha.motivo or ""):
            info.injecao = True
    return por


async def _titulos(session: AsyncSession, itens: Collection[str]) -> dict[str, str]:
    if not itens:
        return {}
    p = ProductLink
    linhas = (
        await session.execute(
            select(p.external_id, func.max(p.listing_title))
            .where(p.external_id.in_(sorted(itens)), p.listing_title.is_not(None))
            .group_by(p.external_id)
        )
    ).all()
    return {str(e): t for e, t in linhas if t}


async def _repetidas(session: AsyncSession, integration_id: UUID) -> frozenset[str]:
    """O texto que a loja repete igual em 5+ conversas — no histórico inteiro (as automáticas
    antigas do Duoke também); `ia.modelos_automaticos` olha só 90 dias."""
    m, c = AtendimentoMensagem, AtendimentoConversa
    chaves = (
        select(
            ia._chave_sql(m.texto, (c.comprador_nome, c.comprador_id)).label("chave"),
            m.conversa_id.label("conversa_id"),
        )
        .select_from(m)
        .join(c, c.id == m.conversa_id)
        .where(
            c.integration_id == integration_id,
            m.autor == constantes.AUTOR_LOJA,
            m.texto.is_not(None),
        )
        .subquery()
    )
    achadas = (
        await session.execute(
            select(chaves.c.chave)
            .where(chaves.c.chave != "")
            .group_by(chaves.c.chave)
            .having(func.count(func.distinct(chaves.c.conversa_id)) >= ia.MODELO_MIN_CONVERSAS)
        )
    ).scalars()
    return frozenset(k for k in achadas if k)


# O texto repetido da loja muda devagar: 1 h de cache por processo (o envio por pessoa
# reindexa a conversa na hora e não pode varrer todas as mensagens da loja a cada vez).
_CACHE_REPETIDAS_S = 3600.0
_cache_repetidas: dict[tuple[str, UUID], tuple[float, frozenset[str]]] = {}


async def _repetidas_com_cache(session: AsyncSession, integration_id: UUID) -> frozenset[str]:
    from app.config import get_settings

    chave = (get_settings().database_schema, integration_id)
    agora = time.monotonic()
    guardado = _cache_repetidas.get(chave)
    if guardado is not None and guardado[0] > agora:
        return guardado[1]
    valor = await _repetidas(session, integration_id)
    _cache_repetidas[chave] = (agora + _CACHE_REPETIDAS_S, valor)
    return valor


async def indexar_conversa(session: AsyncSession, conversa: AtendimentoConversa) -> int:
    """Refaz AGORA as respostas DESTA conversa (o envio por pessoa: "entra na hora", o
    Eduardo, 13/11/2026). Mesmas regras do `indexar`; não faz commit. Devolve quantas valem."""
    if conversa.plataforma not in PLATAFORMAS:
        return 0
    msgs = (await _mensagens(session, [conversa.id])).get(conversa.id, [])
    if not any(m.autor == constantes.AUTOR_LOJA for m in msgs):
        return 0
    rascunhos = (await _rascunhos(session, [conversa.id])).get(conversa.id)
    titulo = None
    if conversa.anuncio_id:
        titulo = (await _titulos(session, {str(conversa.anuncio_id)})).get(str(conversa.anuncio_id))
    repetidas = (
        await _repetidas_com_cache(session, conversa.integration_id)
        if conversa.integration_id is not None
        else frozenset()
    )
    nomes = await ia._NomesDoPedido(session).de(
        conversa.comprador_nome,
        conversa.comprador_id,
        conversa.pedido_marketplace,
        conversa.integration_id,
    )
    novas = respostas_da_conversa(
        msgs,
        conversa=conversa,
        nomes=nomes,
        repetidas=repetidas,
        rascunhos=rascunhos,
        titulo=titulo,
    )
    await _gravar(session, novas)
    validas = [r.chave for r in novas]
    await session.execute(
        update(AtendimentoResposta)
        .where(
            AtendimentoResposta.conversa_id == conversa.id,
            AtendimentoResposta.status == STATUS_ATIVA,
            AtendimentoResposta.chave.not_in(validas) if validas else true(),
        )
        .values(status=STATUS_APOSENTADA, updated_at=func.now())
    )
    return len(novas)


async def _cursor(session: AsyncSession) -> datetime:
    from app.redis_client import redis

    try:
        bruto = await redis.get(CHAVE_CURSOR)
    except Exception:  # noqa: BLE001
        bruto = None
    if bruto:
        try:
            return datetime.fromisoformat(bruto) - MARGEM
        except ValueError:
            pass
    ultimo = await session.scalar(select(func.max(AtendimentoResposta.respondido_em)))
    return (ultimo - MARGEM) if ultimo is not None else INICIO_HISTORICO


async def _guardar_cursor(ate: datetime) -> None:
    from app.redis_client import redis

    try:
        await redis.set(CHAVE_CURSOR, ate.isoformat())
    except Exception:  # noqa: BLE001
        logger.warning("atendimento_banco_cursor_nao_gravado")


async def indexar(
    session: AsyncSession,
    *,
    desde: datetime | None = None,
    max_conversas: int = MAX_CONVERSAS_RODADA,
    tempo_max_s: float = TEMPO_MAX_RODADA_S,
    gravar: bool = True,
    guardar_cursor: bool = True,
) -> dict[str, int]:
    """Uma rodada: as conversas com fala desde o cursor, em lotes, até o teto de conversas ou
    de tempo. Commit por lote quando `gravar`. Devolve as contagens (nada de texto)."""
    t0 = time.monotonic()
    inicio = desde if desde is not None else await _cursor(session)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=UTC)
    alvos = await _conversas(session, inicio, max(1, max_conversas))
    contagem: dict[str, int] = {}
    resumo = {"conversas": 0, "respostas": 0, "aposentadas": 0, "lotes": 0, "mais": 0}
    nomes_svc = ia._NomesDoPedido(session)
    repetidas_por_loja: dict[UUID, frozenset[str]] = {}
    ate: datetime | None = None
    for i in range(0, len(alvos), LOTE_CONVERSAS):
        if time.monotonic() - t0 > tempo_max_s:
            resumo["mais"] = 1
            break
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
        mensagens = await _mensagens(session, ids)
        rascunhos = await _rascunhos(session, ids)
        titulos = await _titulos(
            session, {str(c.anuncio_id) for c in conversas.values() if c.anuncio_id}
        )
        novas: list[RespostaNova] = []
        for cid in ids:
            conversa = conversas.get(cid)
            msgs = mensagens.get(cid, [])
            if conversa is None or not msgs:
                continue
            resumo["conversas"] += 1
            if not any(m.autor == constantes.AUTOR_LOJA for m in msgs):
                continue
            repetidas: frozenset[str] = frozenset()
            if conversa.integration_id is not None:
                if conversa.integration_id not in repetidas_por_loja:
                    repetidas_por_loja[conversa.integration_id] = await _repetidas(
                        session, conversa.integration_id
                    )
                repetidas = repetidas_por_loja[conversa.integration_id]
            nomes = await nomes_svc.de(
                conversa.comprador_nome,
                conversa.comprador_id,
                conversa.pedido_marketplace,
                conversa.integration_id,
            )
            novas += respostas_da_conversa(
                msgs,
                conversa=conversa,
                nomes=nomes,
                repetidas=repetidas,
                rascunhos=rascunhos.get(cid),
                titulo=titulos.get(str(conversa.anuncio_id)) if conversa.anuncio_id else None,
                contagem=contagem,
            )
        resumo["respostas"] += len(novas)
        if gravar:
            await _gravar(session, novas)
            # A resposta da conversa processada que deixou de valer: aposentada, nunca apagada.
            validas = [r.chave for r in novas]
            res = await session.execute(
                update(AtendimentoResposta)
                .where(
                    AtendimentoResposta.conversa_id.in_(ids),
                    AtendimentoResposta.status == STATUS_ATIVA,
                    AtendimentoResposta.chave.not_in(validas) if validas else true(),
                )
                .values(status=STATUS_APOSENTADA, updated_at=func.now())
            )
            resumo["aposentadas"] += res.rowcount or 0
            await session.commit()
        resumo["lotes"] += 1
        ate = lote[-1][1]
    if len(alvos) >= max_conversas and not resumo["mais"]:
        resumo["mais"] = 1
    if gravar and guardar_cursor and ate is not None:
        await _guardar_cursor(ate)
    logger.info(
        "atendimento_banco_indexado",
        **resumo,
        ms=int((time.monotonic() - t0) * 1000),
        gravar=gravar,
        **{f"n_{k}": v for k, v in contagem.items()},
    )
    return {**resumo, **{f"n_{k}": v for k, v in contagem.items()}}


async def respostas_ativas(session: AsyncSession) -> list[AtendimentoResposta]:
    """Todas as respostas ativas (a exportação para o Obsidian), da mais nova para a mais antiga."""
    t = AtendimentoResposta
    return list(
        (
            await session.scalars(
                select(t).where(t.status == STATUS_ATIVA).order_by(t.respondido_em.desc(), t.id)
            )
        ).all()
    )


async def contagens_por_tipo(
    session: AsyncSession, *, agora: datetime | None = None, dias: int = 30
) -> dict[str, dict[str, Any]]:
    """Por tipo: respostas no banco nos últimos `dias`, e como a IA se saiu nas sugestões que a
    pessoa decidiu (enviou igual × editou/reescreveu) — o retrato de hoje da régua."""
    agora = agora or datetime.now(UTC)
    t = AtendimentoResposta
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for tipo, n in (
        await session.execute(
            select(t.tipo, func.count())
            .where(t.status == STATUS_ATIVA, t.respondido_em >= agora - timedelta(days=dias))
            .group_by(t.tipo)
        )
    ).all():
        out[tipo]["recentes"] = int(n)
    r, a = AtendimentoRascunho, AtendimentoAvaliacao
    for categoria, acao, n in (
        await session.execute(
            select(r.categoria, a.acao, func.count())
            .join(a, a.rascunho_id == r.id)
            .where(
                r.categoria.is_not(None),
                a.acao.in_(("enviou_igual", "editou", "escreveu_do_zero")),
                r.created_at >= agora - timedelta(days=60),
            )
            .group_by(r.categoria, a.acao)
        )
    ).all():
        out[categoria][acao] = int(n)
    return dict(out)


def ia_enviou_igual(contagem: Mapping[str, Any]) -> tuple[int, float | None]:
    """(decisões, fração enviada igual) das contagens de um tipo. PURA."""
    igual = int(contagem.get("enviou_igual", 0))
    total = igual + int(contagem.get("editou", 0)) + int(contagem.get("escreveu_do_zero", 0))
    return total, (igual / total if total else None)


# ── A busca do agente no banco (13/11/2026) ─────────────────────────────────
#
# O Eduardo: "ele vai procurar certinho nele?". A busca por palavra sozinha (a dos
# `atendimento_pares`) acha pergunta parecida de OUTRO modelo ou resposta fraca: na
# colheita, o par mais parecido serviria em ~1 de 3. Aqui os candidatos vêm de dois
# lados: (a) as respostas que casam com alguma palavra da pergunta, na mesma plataforma;
# (b) as do MESMO TIPO de pergunta e do MESMO MODELO ou anúncio, mesmo sem palavra em
# comum ("faz pagamento por aproximação?" × "tem NFC?"). A nota: o `ts_rank_cd` (com um
# piso para o lado b) × (1 + bônus de mesmo tipo, modelo, anúncio e loja) × a idade
# (meia-vida de 90 dias: a mais nova pesa mais). Quem decide se a resposta SERVE para o
# caso é o agente, e o verificador confere o lastro com as regras de sempre.

MEIA_VIDA_BANCO_DIAS = 90
# A virada (13/11/2026, o Eduardo: "se ele não sabe e alguém responde editando a sugestão que
# ele deu, ele tem que acertar essa na próxima ou parecida"): a CORREÇÃO da pessoa sobre o
# rascunho da IA (`FONTE_AJUSTOU`) vem antes das outras respostas na busca.
BONUS_CORRECAO_DA_IA = 1.5
BONUS_MESMO_TIPO = 1.0
BONUS_MESMO_MODELO = 0.75
BONUS_MESMO_ANUNCIO_BANCO = 0.75
BONUS_MESMA_LOJA_BANCO = 0.25
PISO_RANK_SEM_PALAVRA = 0.05
MAX_CANDIDATOS_BANCO = 60
MAX_SEM_PALAVRA = 12
# O que só vale para o PRODUTO (a ficha): a resposta de outra loja sobre o mesmo produto
# pode ser lastro. O resto (o que vem na caixa, estoque, preço, prazo, política) varia por
# loja — o Eduardo, 28/10/2026: "acho q só na loja tá ok porque pode variar" — e só vale da
# mesma loja.
TIPOS_SO_DO_PRODUTO = frozenset(
    {"espec_celular", "espec_mala", "espec_eletro", "espec_outros", "resistencia_agua"}
)


def nota_banco(
    rank: float,
    *,
    mesmo_tipo: bool,
    mesmo_modelo: bool,
    mesmo_anuncio: bool,
    mesma_loja: bool,
    idade_dias: float,
    correcao: bool = False,
) -> float:
    """A nota de uma resposta do banco na busca. PURA."""
    bonus = 1.0
    bonus += BONUS_CORRECAO_DA_IA if correcao else 0.0
    bonus += BONUS_MESMO_TIPO if mesmo_tipo else 0.0
    bonus += BONUS_MESMO_MODELO if mesmo_modelo else 0.0
    bonus += BONUS_MESMO_ANUNCIO_BANCO if mesmo_anuncio else 0.0
    bonus += BONUS_MESMA_LOJA_BANCO if mesma_loja else 0.0
    return max(0.0, rank) * bonus * 0.5 ** (max(0.0, idade_dias) / MEIA_VIDA_BANCO_DIAS)


async def buscar(
    session: AsyncSession,
    *,
    pergunta: str,
    plataforma: str,
    integration_id: UUID | None,
    item_ids: Sequence[str],
    modelos: Collection[str],
    tipo: str | None,
    conversa_id: UUID | None,
    as_of: datetime,
    limite: int = 10,
    so_itens: Sequence[str] | None = None,
) -> list[dict]:
    """As respostas do banco para a pergunta, as melhores primeiro (ver acima). Só lê.

    Cada uma: {"id" (`b<id>`), "pergunta", "resposta", "tipo", "fonte", "mesma_loja",
    "mesmo_anuncio", "mesmo_modelo", "mesmo_tipo", "idade_dias", "item_id",
    "integration_id"}. Nunca a da própria conversa nem a respondida depois de `as_of`
    (o replay). `so_itens` = só as desses anúncios.
    """
    if limite <= 0:
        return []
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    t = AtendimentoResposta
    itens = [i for i in dict.fromkeys(str(x or "").strip() for x in item_ids) if i]
    modelos = sorted({str(m).upper() for m in modelos if m})
    base = [
        t.status == STATUS_ATIVA,
        t.plataforma == plataforma,
        t.respondido_em < as_of,
        *((t.conversa_id.is_distinct_from(conversa_id),) if conversa_id is not None else ()),
        *((t.item_id.in_(list(so_itens)),) if so_itens else ()),
    ]
    colunas = (
        t.id,
        t.pergunta,
        t.resposta,
        t.tipo,
        t.fonte,
        t.respondido_em,
        t.integration_id,
        t.item_id,
        t.modelos,
    )
    achados: dict[int, tuple[Any, float]] = {}
    qualquer = busca_texto.consulta_de(pergunta)
    if qualquer is not None:
        rank = func.ts_rank_cd(t.tsv, qualquer)
        for linha in (
            await session.execute(
                select(*colunas, rank.label("rank"))
                .where(*base, t.tsv.op("@@")(qualquer))
                .order_by(rank.desc(), t.respondido_em.desc(), t.id)
                .limit(MAX_CANDIDATOS_BANCO)
            )
        ).all():
            achados[linha.id] = (linha, float(linha.rank or 0.0))
    if tipo and tipo != tipos_pergunta.INDEFINIDO and (modelos or itens):
        do_caso = [t.item_id.in_(itens)] if itens else []
        if modelos:
            do_caso.append(t.modelos.op("?|")(func.cast(modelos, ARRAY(String))))
        for linha in (
            await session.execute(
                select(*colunas)
                .where(*base, t.tipo == tipo, or_(*do_caso))
                .order_by(t.respondido_em.desc(), t.id)
                .limit(MAX_SEM_PALAVRA)
            )
        ).all():
            achados.setdefault(linha.id, (linha, PISO_RANK_SEM_PALAVRA))
    candidatos = []
    for linha, rank_texto in achados.values():
        idade = max(0.0, (as_of - linha.respondido_em).total_seconds() / 86400)
        mesmo_anuncio = bool(linha.item_id) and linha.item_id in itens
        mesmo_modelo = bool(modelos) and bool(set(linha.modelos or ()) & set(modelos))
        mesma_loja = integration_id is not None and linha.integration_id == integration_id
        mesmo_tipo = bool(tipo) and linha.tipo == tipo
        nota = nota_banco(
            rank_texto,
            mesmo_tipo=mesmo_tipo,
            mesmo_modelo=mesmo_modelo,
            mesmo_anuncio=mesmo_anuncio,
            mesma_loja=mesma_loja,
            idade_dias=idade,
            correcao=linha.fonte == FONTE_AJUSTOU,
        )
        candidatos.append(
            (
                -nota,
                -linha.respondido_em.timestamp(),
                linha.id,
                linha,
                idade,
                {
                    "mesmo_anuncio": mesmo_anuncio,
                    "mesmo_modelo": mesmo_modelo,
                    "mesma_loja": mesma_loja,
                    "mesmo_tipo": mesmo_tipo,
                },
            )
        )
    candidatos.sort(key=lambda c: c[:3])
    saida: list[dict] = []
    vistas: set[str] = set()
    for _nota, _quando, _id, linha, idade, marcas in candidatos:
        if pares.tem_dado_pessoal(linha.pergunta) or pares.tem_dado_pessoal(linha.resposta):
            continue
        if pares.ensina_o_que_o_agente_nao_faz(linha.resposta):
            continue
        chave = " ".join(linha.resposta.casefold().split())
        if chave in vistas:
            continue
        vistas.add(chave)
        saida.append(
            {
                "id": f"b{linha.id}",
                "pergunta": linha.pergunta,
                "resposta": linha.resposta,
                "tipo": linha.tipo,
                "fonte": linha.fonte,
                "idade_dias": int(idade),
                "item_id": linha.item_id,
                "integration_id": linha.integration_id,
                **marcas,
            }
        )
        if len(saida) >= limite:
            break
    return saida


async def exemplos_do_turno(
    session: AsyncSession,
    *,
    pergunta: str,
    plataforma: str,
    integration_id: UUID | None,
    item_ids: Sequence[str],
    conversa_id: UUID,
    as_of: datetime,
    titulos: Mapping[str, str | None] | None = None,
    limite: int = pares.MAX_PARES_PACOTE,
    virada: bool = False,
) -> list[dict]:
    """O `pares.exemplos_do_turno` lendo o BANCO DE RESPOSTAS (a chave
    `atendimento_agente_le_banco`): o mesmo formato e as mesmas regras de lastro.

    Cada um: {"id" (`par:b<id>`), "pergunta", "resposta", "mesma_loja", "mesmo_anuncio",
    "mesmo_produto", "fonte_de_produto", "com_anuncio", "idade_dias", "mesmo_tipo",
    "mesmo_modelo", "titulo_do_anuncio" (só no do mesmo anúncio)}. `fonte_de_produto` é a
    régua de sempre (a conversa com UM anúncio, de UM modelo, e a resposta do mesmo anúncio
    ou de um com o mesmo conjunto de produtos) e, fora da ficha do produto
    (`TIPOS_SO_DO_PRODUTO`), só da MESMA loja. Só lê.
    """
    from app.services.atendimento import ficha as ficha_svc
    from app.services.atendimento.agente import contexto as agente_ctx

    consulta = " ".join(str(pergunta or "").split())[: pares.MAX_CHARS_BUSCA_PACOTE]
    itens = [i for i in dict.fromkeys(str(x or "").strip() for x in item_ids) if i]
    titulos = titulos or {}
    modelos = {m for i in itens for m in modelos_do(titulos.get(i))}
    tipo = tipos_pergunta.tipo_de(consulta)
    comum = {
        "pergunta": consulta,
        "plataforma": plataforma,
        "integration_id": integration_id,
        "item_ids": itens,
        "modelos": modelos,
        "tipo": tipo,
        "conversa_id": conversa_id,
        "as_of": as_of,
        "limite": pares.CANDIDATOS_PACOTE,
    }
    plat = ficha_svc.plataforma_do_catalogo(plataforma)
    mesmos = await pares._anuncios_do_mesmo_produto(session, plat, integration_id, itens)
    primeiros = await buscar(session, **comum, so_itens=mesmos) if mesmos else []
    gerais = await buscar(session, **comum)
    vistos: set[str] = set()
    achados = []
    for a in [*primeiros, *gerais]:
        if a["id"] not in vistos:
            vistos.add(a["id"])
            achados.append(a)
    achados = [
        a
        for a in achados
        if pares.serve_ao_pacote(a["resposta"], virada=virada)
        and not pares.tem_mascara_de_dado(a["pergunta"])
        # A origem do envio só da MESMA loja (cada loja sai de um lugar).
        and (
            a["mesma_loja"] or not pares._ORIGEM_NO_PAR.search(pares._plano_simples(a["resposta"]))
        )
    ]
    if not achados or limite <= 0:
        return []
    produtos = await pares._produtos_dos_anuncios(
        session,
        plat,
        [
            *((a["integration_id"], (a["item_id"] or "").strip()) for a in achados),
            *((integration_id, i) for i in itens),
        ],
    )
    da_conversa = [produtos.get((integration_id, i)) for i in itens]
    unico = da_conversa[0] if len(itens) == 1 else None
    um_modelo = await pares._um_modelo_so(session, unico) if unico else False
    saida: list[tuple[tuple[int, int, int], dict]] = []
    for ordem, a in enumerate(achados):
        integ, item = a["integration_id"], (a["item_id"] or "").strip()
        mesmo_anuncio = bool(item) and item in itens
        dele = produtos.get((integ, item)) if item else None
        mesmo_produto = mesmo_anuncio or bool(dele and any(dele == c for c in da_conversa if c))
        fonte = um_modelo and (
            (mesmo_anuncio and integ == integration_id) or (dele is not None and dele == unico)
        )
        if a["tipo"] not in TIPOS_SO_DO_PRODUTO and not a["mesma_loja"]:
            fonte = False
        par = {
            "id": agente_ctx.id_par(a["id"]),
            "pergunta": pares.para_o_pacote(a["pergunta"], pares.MAX_CHARS_PERGUNTA_PACOTE),
            "resposta": pares.para_o_pacote(a["resposta"], pares.MAX_CHARS_RESPOSTA_PACOTE),
            "mesma_loja": bool(a["mesma_loja"]),
            "mesmo_anuncio": mesmo_anuncio,
            "mesmo_produto": mesmo_produto or bool(a["mesmo_modelo"]),
            "fonte_de_produto": bool(fonte),
            "com_anuncio": bool(item),
            "idade_dias": int(a["idade_dias"]),
            "mesmo_tipo": bool(a["mesmo_tipo"]),
            "mesmo_modelo": bool(a["mesmo_modelo"]),
            # 13/11/2026 (a virada): o TIPO da resposta no banco. Sem ele, a conferência pelo
            # sentido (`agente.confere_banco.candidatas`) e o `fatos.agente.banco_tipos` nunca
            # achavam a resposta do mesmo tipo — o "bateu com a do banco" não rodava.
            "tipo": a["tipo"],
        }
        if mesmo_anuncio and (titulo := " ".join(str(titulos.get(item) or "").split())):
            par["titulo_do_anuncio"] = titulo[: pares.MAX_CHARS_TITULO_PACOTE]
        # A virada: o MESMO tipo da pergunta primeiro (a resposta que a equipe deu a isto),
        # depois o mesmo produto e a mesma loja; dentro, a ordem da nota da busca.
        saida.append(
            (
                (
                    0 if a["mesmo_tipo"] else 1,
                    0 if par["mesmo_produto"] else 1,
                    0 if a["mesma_loja"] else 1,
                    ordem,
                ),
                par,
            )
        )
    saida.sort(key=lambda par: par[0])
    return [e for _chave, e in saida[:limite]]


async def exemplos_da_politica_de_entrega(
    session: AsyncSession,
    *,
    plataforma: str,
    integration_id: UUID | None,
    conversa_id: UUID,
    as_of: datetime,
) -> list[dict]:
    """O `pares.exemplos_da_politica_de_entrega` lendo o banco: a resposta-padrão da entrega
    da MESMA loja, no formato do pacote. Só lê. Sem a loja, nada."""
    from app.services.atendimento.agente import contexto as agente_ctx

    if integration_id is None:
        return []
    achados = await buscar(
        session,
        pergunta=pares.CONSULTA_DA_POLITICA_DE_ENTREGA,
        plataforma=plataforma,
        integration_id=integration_id,
        item_ids=(),
        modelos=(),
        tipo=None,
        conversa_id=conversa_id,
        as_of=as_of,
        limite=pares.CANDIDATOS_PACOTE,
    )
    saida: list[dict] = []
    for a in achados:
        if not a["mesma_loja"] or not pares.politica_da_entrega(a["resposta"]):
            continue
        if not pares.serve_ao_pacote(a["resposta"]) or pares.tem_mascara_de_dado(a["pergunta"]):
            continue
        saida.append(
            {
                "id": agente_ctx.id_par(a["id"]),
                "pergunta": pares.para_o_pacote(a["pergunta"], pares.MAX_CHARS_PERGUNTA_PACOTE),
                "resposta": pares.para_o_pacote(a["resposta"], pares.MAX_CHARS_RESPOSTA_PACOTE),
                "mesma_loja": True,
                "mesmo_anuncio": False,
                "mesmo_produto": False,
                "fonte_de_produto": False,
                "com_anuncio": False,
                "idade_dias": int(a["idade_dias"]),
                "tipo": a["tipo"],
            }
        )
        if len(saida) >= pares.MAX_PARES_DA_POLITICA:
            break
    return saida


# ── A correção da revisão diária (13/11/2026) ─────────────────────────────────
#
# A tela do banco (routers/atendimento_banco.py): no 👎 ao que a IA mandou SOZINHA, a correção
# da pessoa ENTRA NO BANCO como resposta AJUSTADA por pessoa (`FONTE_AJUSTOU`) para a pergunta
# daquele envio — o turno INTEIRO do cliente logo antes dele, com as falas de sempre
# (`pares._entra`). As regras são as da montagem (`respostas_da_conversa`): fora a conversa com
# cara de instrução, o que tem cara de dado pessoal (antes E depois da máscara), o que ensina o
# proibido, a pergunta só de cortesia, a resposta sem conteúdo ou curta demais; só a versão
# generalizada; o tipo é o assunto do rascunho (sem ele, a palavra-chave).
#
# A chave é a de sempre (`chave_de`: a conversa e a 1ª mensagem do envio da IA — que a
# montagem nunca rende, porque o turno com resposta da IA não entra) e a gravação é o upsert
# `_gravar`: a correção refeita troca a anterior (a mais nova vence; `respondido_em` = a hora
# da correção). A linha nasce SEM `conversa_id`: o `indexar` e o `indexar_conversa` aposentam a
# resposta da conversa que a montagem não rende de novo — e ela nunca rende a correção. O 👍
# depois do 👎, ou a correção nova que não serve, aposenta a anterior (nunca apaga).

CORRECAO_OK = "ok"
CORRECAO_FORA_DO_BANCO = "plataforma"
CORRECAO_SEM_ENVIO = "sem_envio"
CORRECAO_SEM_PERGUNTA = "sem_pergunta"
CORRECAO_INJECAO = "injecao"
# O envio da IA que conta como "saiu": `revisar` também (até alguém conferir, é resposta).
STATUS_QUE_SAIRAM = (constantes.MSG_ENVIADA, constantes.MSG_REVISAR)


def saiu_sozinha(m: Any, rascunho_id: UUID | None = None) -> bool:
    """A mensagem é o envio SOZINHO da IA (do rascunho `rascunho_id`, quando vem)? PURA."""
    return (
        getattr(m, "origem", None) == constantes.ORIGEM_IA
        and getattr(m, "autor", None) == constantes.AUTOR_LOJA
        and getattr(m, "status", None) in STATUS_QUE_SAIRAM
        and getattr(m, "rascunho_id", None) is not None
        and (rascunho_id is None or m.rascunho_id == rascunho_id)
    )


def turno_do_cliente_antes(
    mensagens: Sequence[Any],
    resposta_ids: Collection[UUID],
    *,
    comprador: tuple[str | None, ...] = (),
    repetidas: Collection[str] = frozenset(),
) -> Any | None:
    """O turno INTEIRO do cliente logo antes da resposta (a 1ª de `resposta_ids` na ordem das
    mensagens), com as falas do `agente.pares` (`_entra`). None sem a resposta entre as
    mensagens ou sem turno do cliente logo antes dela. PURA."""
    ids = set(resposta_ids)
    antes: list[Any] = []
    for m in mensagens:
        if m.id in ids:
            break
        antes.append(m)
    else:
        return None
    turnos = pares._turnos([m for m in antes if pares._entra(m, repetidas, comprador)])
    if not turnos or turnos[-1].autor != constantes.AUTOR_CLIENTE:
        return None
    return turnos[-1]


def resposta_da_correcao(
    turno: Any,
    correcao: str,
    *,
    conversa: Any,
    resposta_msg_id: UUID,
    nomes: tuple[str | None, ...],
    tipo: str | None,
    titulo: str | None,
    em: datetime,
) -> tuple[RespostaNova | None, str]:
    """A correção como resposta do banco, ou (None, o motivo — os códigos da contagem da
    montagem). As regras de `respostas_da_conversa`, na mesma ordem. PURA."""
    plataforma, canal = conversa.plataforma, conversa.canal
    pergunta_crua = turno.texto
    resposta_crua = pares.sem_citacao(correcao)
    if not pergunta_crua.strip() or not resposta_crua.strip():
        return None, "sem_texto"
    if pares.tem_dado_pessoal(pergunta_crua) or pares.tem_dado_pessoal(resposta_crua):
        return None, "dado_pessoal"
    if not ia._par_seguro(pergunta_crua, resposta_crua):
        return None, "inseguro"
    if ia._ensina_o_proibido(resposta_crua, plataforma, canal, de_pessoa=True):
        return None, "proibido"
    pergunta = pares.generalizar(pergunta_crua, nomes)
    resposta = pares.generalizar(resposta_crua, nomes)
    if not seguro(pergunta) or not seguro(resposta):
        return None, "dado_pessoal"
    if pares.so_cumprimento(pergunta):
        return None, "so_cortesia"
    if sem_conteudo(resposta):
        return None, "sem_conteudo"
    if curta_demais(pergunta, resposta):
        return None, "curta"
    titulo = titulo or getattr(conversa, "anuncio_titulo", None) or None
    nova = RespostaNova(
        chave=chave_de(conversa.id, resposta_msg_id),
        conversa_id=None,  # fora do refazer da conversa (ver acima)
        resposta_msg_id=resposta_msg_id,
        plataforma=plataforma,
        canal=canal,
        integration_id=getattr(conversa, "integration_id", None),
        loja=((getattr(conversa, "conta", None) or "").strip() or None),
        item_id=(getattr(conversa, "anuncio_id", None) or None) and str(conversa.anuncio_id)[:64],
        anuncio_titulo=(titulo or None) and titulo[:300],
        modelos=modelos_do(titulo),
        tipo=(tipo or tipos_pergunta.tipo_de(pergunta_crua))[:32],
        pergunta=pergunta,
        resposta=resposta,
        fonte=FONTE_AJUSTOU,
        perguntado_em=pares._momento(turno.mensagens[-1]),
        respondido_em=em,
        # O cliente não viu a correção: não há o "depois" dela.
        cliente_depois=DEPOIS_SEM_RETORNO,
    )
    return nova, CORRECAO_OK


async def retirar_correcao(session: AsyncSession, chave: str) -> int:
    """Aposenta a correção da revisão de `chave` (o 👍 depois do 👎, ou a correção nova que não
    serve). Só a linha da revisão (sem conversa, ajustada por pessoa); nunca apaga. Sem commit."""
    t = AtendimentoResposta
    res = await session.execute(
        update(t)
        .where(
            t.chave == chave,
            t.conversa_id.is_(None),
            t.fonte == FONTE_AJUSTOU,
            t.status == STATUS_ATIVA,
        )
        .values(status=STATUS_APOSENTADA, updated_at=func.now())
    )
    return res.rowcount or 0


async def gravar_correcao(
    session: AsyncSession,
    *,
    conversa: AtendimentoConversa,
    rascunho: AtendimentoRascunho,
    correcao: str,
    agora: datetime | None = None,
) -> tuple[RespostaNova | None, str]:
    """O 👎 da revisão diária: a correção entra no banco (ver acima). Sem commit.

    Devolve (a resposta gravada, `CORRECAO_OK`) ou (None, o motivo); a correção que não serve
    aposenta a anterior do mesmo envio. Nada de texto no log (quem chama registra o código).
    """
    if conversa.plataforma not in PLATAFORMAS:
        return None, CORRECAO_FORA_DO_BANCO
    msgs = (await _mensagens(session, [conversa.id])).get(conversa.id, [])
    envio = [m.id for m in msgs if saiu_sozinha(m, rascunho.id)]
    if not envio:
        return None, CORRECAO_SEM_ENVIO
    info = (await _rascunhos(session, [conversa.id])).get(conversa.id) or Rascunhos()
    nova: RespostaNova | None = None
    if info.injecao or pares._tem_injecao(
        [m.texto for m in msgs if m.autor == constantes.AUTOR_CLIENTE]
    ):
        motivo = CORRECAO_INJECAO
    else:
        repetidas = (
            await _repetidas_com_cache(session, conversa.integration_id)
            if conversa.integration_id is not None
            else frozenset()
        )
        turno = turno_do_cliente_antes(
            msgs,
            envio,
            comprador=(conversa.comprador_nome, conversa.comprador_id),
            repetidas=repetidas,
        )
        motivo = CORRECAO_SEM_PERGUNTA
        if turno is not None:
            nomes = await ia._NomesDoPedido(session).de(
                conversa.comprador_nome,
                conversa.comprador_id,
                conversa.pedido_marketplace,
                conversa.integration_id,
            )
            titulo = None
            if conversa.anuncio_id:
                item = str(conversa.anuncio_id)
                titulo = (await _titulos(session, {item})).get(item)
            nova, motivo = resposta_da_correcao(
                turno,
                correcao,
                conversa=conversa,
                resposta_msg_id=envio[0],
                nomes=nomes,
                tipo=rascunho.categoria,
                titulo=titulo,
                em=agora or datetime.now(UTC),
            )
    if nova is None:
        await retirar_correcao(session, chave_de(conversa.id, envio[0]))
        return None, motivo
    await _gravar(session, [nova])
    return nova, CORRECAO_OK
