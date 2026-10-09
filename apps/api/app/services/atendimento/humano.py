"""A Caixa Humano (09/10/2026): ao lado da Caixa, só o que a IA não pode responder.

O dono: "criaremos ao lado da Caixa uma Caixa Humano, que virá quando a IA não
puder responder a questão — não consegue e tem que encaminhar para um humano —
e então vai aparecer ali na Caixa Humano só as que falta responder por um
humano que a IA não conseguiu".

A RÉGUA é a do `ia._gerar` (o que ela marca `precisa_humano`), SEM chamar o
modelo: o Groq tem 1.000 pedidos por dia e a triagem passa por toda conversa
esperando resposta. Então a Caixa Humano mostra o que a IA mandaria para
pessoa, com as mesmas peças (`ia.sinais_com_codigo`, `ia.pistas_de_categoria`,
`ia.em_aguardando_cancelamento`, `contexto.contexto_da_conversa`...). Se a
régua da IA mudar, muda aqui junto.

QUEM ENTRA (`no_escopo` / `escopo_sql`, os dois iguais): conversa esperando
resposta ("Falta responder"), não fechada, num canal que TEM resposta — as
plataformas da caixa (`PLATAFORMAS_CAIXA`) fora do Zap e do e-mail antigo do
Tuta (`motivo_canal_sem_envio`) — ou o e-mail da PONTE da Central
(`dados.fonte = 'tuta'` + `dados.mail`). Ficam de fora: Zap,
e-mail antigo do Tuta, carrinho do site e comentário das redes.
A BLOQUEADA (a plataforma não deixa responder pelo DaVinci: a reclamação só
leitura, a avaliação que o ML não deixa responder, a janela do TikTok, o
pós-venda bloqueado do ML) é triada também, mas só entra com um motivo de
ESTADO (`HUMANO_DE_ESTADO`: reclamação, Ag. cancelamento, devolução, chamado,
avaliação ruim) — e leva junto o `bloqueada` ("responder na plataforma"):
quem resolve é uma pessoa, lá. Sem motivo de estado ela fica só na Caixa (a
IA também não responde bloqueada, mas não há o que responder por aqui).
SEM MENSAGEM DO CLIENTE também entra no escopo: a reclamação/devolução da
plataforma que só tem mensagem do sistema ou do mediador (09/10/2026: 16 da
Shopee, 8 do TikTok e 1 do ML esperando a loja) — o turno dela é a criação
da conversa (`turno_sql`), e ela entra pelo estado como qualquer outra.

OS MOTIVOS (`triar`, PURA — só códigos, `constantes.ORDEM_HUMANO`):
  estado do pedido/conversa —
    reclamacao       canal/etiqueta (ou secundária) Reclamação, reclamação
                     aberta da plataforma no contexto ou `claim_ids` do ML;
    ag_cancelamento  etiqueta (ou secundária) Ag. cancelamento ou o pedido
                     em 83955 (item 4: nunca sai sem pessoa — inclusive a
                     trava da Margem);
    devolucao        etiqueta (ou secundária) Devolução ou devolução no
                     contexto ("pedido com chamado ou devolução" do ia.py);
    chamado          chamado NÃO resolvido do pedido;
    avaliacao        o canal avaliação com nota 1–3 (`dados.estrelas`; sem a
                     nota, na dúvida, entra) ou avaliação pendente de nota
                     1–3 do pedido. A de 4–5 só entra por outro motivo (o
                     texto dela, o pedido não achado): o "obrigado pela
                     confiança" é das respostas prontas;
    e_mail           e-mail da ponte (`MOTIVO_EMAIL_SO_PESSOA`: a IA nunca
                     responde e-mail);
    pedido_nao_achado  fora da pergunta pré-venda, com nº de pedido que o
                     Bling não acha;
  texto do cliente (as 8 últimas trocas, como no `_gerar`) —
    alerta / xingamento / atendente / instrucao  (`ia.sinais_com_codigo`);
    assunto          pista de assunto só de pessoa (troca, cancelamento,
                     defeito, reembolso, garantia, endereço, desconto — e os
                     do manual marcados `so_humano`); os códigos vão em
                     `assuntos`;
    sem_dado         o que ele escreveu desde a última resposta de PESSOA
                     pergunta rastreio/prazo de envio/NF e falta o dado (sem
                     pedido, sem código de rastreio, sem NF) — "lacuna sem
                     dado vira pessoa";
    so_anexo         esse trecho — ou o que ele mandou depois da última fala
                     da loja, o robô incluído (a rajada do `ia._gerar`) — não
                     tem texto e traz foto/vídeo/arquivo/áudio de verdade;
  a IA —
    ia               a sugestão do turno atual (gatilho na última mensagem
                     do cliente ou depois), pendente ou bloqueada, marcou
                     precisa de pessoa, o validador reprovou ou veio sem
                     texto. Hoje a IA quase não gera sugestão: a régua NÃO
                     depende dela.
  E a IA PAUSADA na conversa: não é gravada — entra na hora, pela consulta
  (`caixa_humano_sql`), com o motivo `ia_pausada` (menos a bloqueada: ela só
  entra pelo estado).
  E a FALHA: a triagem da conversa deu erro — grava o motivo `falha` ("não
  deu para triar: confira"). Na dúvida, mostra; e, com o `em` de agora, a
  rodada só tenta de novo na revisão de 30 min (quem falha sempre não fica
  na frente da fila em toda rodada).

O QUE NÃO É MOTIVO (fica só na Caixa): o turno só com o cartão do produto ou
do pedido (o cliente ainda não perguntou nada), a figurinha, o MODO da loja
(observar/humano é configuração da loja, não da pergunta — senão a Caixa
Humano viraria cópia do "Falta responder"), ser Amazon, ou ter tido resposta
de pessoa nas últimas 24 h (essas são travas do envio automático).
DE PROPÓSITO FORA (a diferença para o `ia._gerar`): os sinais do cartão
"Cliente" (`cliente.cartao_cliente` — avaliou mal, reclamação aberta em
OUTRA conversa do mesmo comprador). Ele consulta o ML ao vivo, e a régua de
desatualizada não acompanha as outras conversas do comprador; a reclamação
DESTA conversa e a avaliação ruim DESTE pedido já entram pelo estado.
Também não entra o assunto que só o MODELO escolheria: as pistas de texto
(`ia.pistas_de_categoria`) são a régua — e são largas de propósito.

ONDE FICA: `atendimento_conversas.dados.humano` =
    {"v": VERSAO, "turno": <epoch de ultima_do_cliente_em (ou da criação)>,
     "em": <epoch do cálculo>, "motivos": [...], "assuntos": [...]}
Só códigos — nenhum texto de comprador nem o motivo escrito pelo modelo. O
`turno` é escrito e comparado pelo MESMO SQL (`turno_sql`: to_jsonb(extract
(epoch from coalesce(ultima_do_cliente_em, created_at)))): igualdade exata,
sem formatar data em Python e sem conversão de tipo que possa derrubar a
lista. Cliente escreveu de novo = turno diferente = a conversa sai da Caixa
Humano até ser triada de novo (na dúvida, não mostra; ela nunca sai da Caixa).

O CRON (`atendimento_humano`, a cada minuto; `worker.atendimento_humano`):
uma rodada por vez (trava no Redis), até `MAX_POR_RODADA` conversas
DESATUALIZADAS (`desatualizada_sql`: sem cálculo, versão velha, turno
diferente, etiqueta mais nova que o cálculo, sugestão da IA mexida depois
dele, ou cálculo com mais de 30 min — a rede de segurança para chamado,
Bling e devolução, que mudam fora do sync), as de turno novo primeiro,
depois pelo prazo. Grava com UM UPDATE protegido (`gravar_triagem`):
jsonb_set (não apaga as outras chaves de `dados`), na linha travada com
FOR NO KEY UPDATE SKIP LOCKED (a que o sync ou a tela estão mexendo fica
para a próxima rodada) e só se `ultima_do_cliente_em` ainda é a que foi lida
(mensagem nova no meio = resultado descartado). Commit por conversa: a trava
da linha dura só o UPDATE. O log leva só contagens.

ESCRITA PERDIDA (aceita): o `gravar.upsert_conversa` junta `dados` a partir
da cópia carregada antes; se o nosso UPDATE cair entre essa leitura e o
flush do sync, a chave some (ou volta a antiga). Conserta sozinho: a régua
de desatualizada recalcula na rodada seguinte (no pior caso, em 30 min).

Nunca chama o modelo nem a rede: só lê o banco do DaVinci e grava o cache.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import (
    DateTime,
    Text,
    and_,
    case,
    cast,
    exists,
    extract,
    false,
    func,
    literal,
    literal_column,
    null,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

import app.db as _db
from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoMensagem, AtendimentoRascunho
from app.redis_client import redis
from app.services.atendimento import contexto as contexto_svc
from app.services.atendimento import ia
from app.services.atendimento import manual as manual_svc
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    CANAL_AVALIACAO,
    CANAL_EMAIL,
    CANAL_PERGUNTA,
    CANAL_RECLAMACAO,
    CATEGORIAS_INFO,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    ETIQUETA_AG_CANCELAMENTO,
    ETIQUETA_DEVOLUCAO,
    ETIQUETA_RECLAMACAO,
    FONTE_TUTA,
    HUMANO_AG_CANCELAMENTO,
    HUMANO_ASSUNTO,
    HUMANO_AVALIACAO,
    HUMANO_BLOQUEADA,
    HUMANO_CHAMADO,
    HUMANO_DE_ESTADO,
    HUMANO_DEVOLUCAO,
    HUMANO_EMAIL,
    HUMANO_FALHA,
    HUMANO_IA,
    HUMANO_IA_PAUSADA,
    HUMANO_PEDIDO_NAO_ACHADO,
    HUMANO_RECLAMACAO,
    HUMANO_SEM_DADO,
    HUMANO_SO_ANEXO,
    MOTIVO_EMAIL_SO_PESSOA,
    NOTA_BAIXA_AVALIACAO,
    ORDEM_HUMANO,
    PLATAFORMAS_CAIXA,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
    e_cartao_do_comprador,
    e_mensagem_automatica,
    motivo_canal_sem_envio,
    reclamacao_aberta,
    rotulo_humano,
)

logger = structlog.get_logger()

# Muda quando a RÉGUA muda: a rodada recalcula tudo o que foi gravado com outra.
# 2 (09/10/2026, revisão): pistas de assunto mais largas, a avaliação de nota
# alta fora, a bloqueada pelo estado, a foto depois do robô, o motivo `falha`.
VERSAO = 2
# A chave em `atendimento_conversas.dados`.
CHAVE = "humano"
# Conversas por rodada do cron (o recálculo inicial, ~500, leva umas 3).
MAX_POR_RODADA = 200
# A rede de segurança: o que muda fora do sync (chamado, Bling, devolução).
REVISAO = timedelta(minutes=30)
# A rodada para antes do `timeout` do cron (120 s): o que sobrar fica para a próxima.
ORCAMENTO_S = 90.0
# Trava da rodada (por schema). Folga sobre o `timeout` do cron.
RODADA_TTL_S = 150
_CHAVE_RODADA = "atendimento:humano:rodada:{}"

# O anexo "de verdade" (não o cartão do produto/pedido, não a figurinha — que
# a leitura grava com o rótulo como texto).
TIPOS_ANEXO = ("imagem", "video", "arquivo", "audio")
# Os assuntos em que "sem pedido/sem dado" quer dizer "não dá para responder".
_CATEGORIAS_DE_PEDIDO = ("rastreio", "prazo_envio", "nota_fiscal")

_JSON_LISTA_VAZIA = literal_column("'[]'::jsonb", type_=JSONB)
_JSON_OBJETO_VAZIO = literal_column("'{}'::jsonb", type_=JSONB)


@dataclass(frozen=True)
class Triagem:
    """O que a triagem decidiu: os motivos (na ordem de `ORDEM_HUMANO`) e os assuntos."""

    motivos: tuple[str, ...] = ()
    assuntos: tuple[str, ...] = ()

    @property
    def para_pessoa(self) -> bool:
        return bool(self.motivos)


# ── A régua (pura) ────────────────────────────────────────────────────────


def no_escopo(conversa: Any) -> bool:
    """A conversa pode estar na Caixa Humano? PURA — a gêmea de `escopo_sql`."""
    if not conversa.aguardando_resposta:
        return False
    # A bloqueada entra no escopo: a régua decide (só pelo estado — `triar`).
    if conversa.situacao == CONVERSA_FECHADA:
        return False
    sem_envio = motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados)
    if sem_envio == MOTIVO_EMAIL_SO_PESSOA:
        return True
    return conversa.plataforma in PLATAFORMAS_CAIXA and sem_envio is None


def _etiquetas(conversa: Any) -> set[str]:
    """A etiqueta e as secundárias (indicador pequeno) da conversa."""
    saida = {e for e in (conversa.etiquetas_secundarias or []) if isinstance(e, str)}
    if conversa.etiqueta:
        saida.add(conversa.etiqueta)
    return saida


def _rajada(recentes: Sequence[Any], gatilho: Any | None) -> list[Any]:
    """O que o cliente escreveu desde a última resposta de PESSOA da loja.

    A mensagem automática (o robô do Duoke, a figurinha da campanha, o cartão
    da Shopee — `e_mensagem_automatica`) não é resposta: a mesma régua do
    "Falta responder". Sem nada depois dela, a última do cliente.
    """
    ultima_pessoa = max(
        (
            i
            for i, m in enumerate(recentes)
            if m.autor == AUTOR_LOJA
            and not e_mensagem_automatica(m.texto, getattr(m, "payload", None))
        ),
        default=-1,
    )
    rajada = [m for m in recentes[ultima_pessoa + 1 :] if m.autor == AUTOR_CLIENTE]
    if rajada:
        return rajada
    return [gatilho] if gatilho is not None else []


def _depois_da_loja(recentes: Sequence[Any], gatilho: Any | None) -> list[Any]:
    """O que o cliente mandou depois da última fala da LOJA, o robô incluído.

    É a rajada do `ia._gerar` (o "só foto" dela): cliente "oi", robô, cliente
    manda só a foto — a IA manda para pessoa; a `_rajada` (desde a última
    resposta de PESSOA) veria "oi" + foto e acharia texto.
    """
    ultima_loja = max((i for i, m in enumerate(recentes) if m.autor == AUTOR_LOJA), default=-1)
    rajada = [m for m in recentes[ultima_loja + 1 :] if m.autor == AUTOR_CLIENTE]
    if rajada:
        return rajada
    return [gatilho] if gatilho is not None else []


def _avaliacao_ruim(conversa: Any) -> bool:
    """O canal avaliação com nota baixa (1–3). Sem a nota (não devia faltar): na dúvida, entra."""
    if conversa.canal != CANAL_AVALIACAO:
        return False
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    estrelas = dados.get("estrelas")
    if isinstance(estrelas, bool) or not isinstance(estrelas, int):
        return True
    return estrelas <= NOTA_BAIXA_AVALIACAO


def _falta_dado(pistas: Sequence[str], ctx: dict, pedido_nao_achado: bool) -> bool:
    """Pergunta de rastreio/prazo/NF sem o dado para responder ("lacuna sem dado")."""
    pede = [p for p in pistas if p in _CATEGORIAS_DE_PEDIDO]
    if not pede:
        return False
    if ctx.get("pedido") is None:
        # Com nº de pedido que o Bling não acha, o "pedido não achado" já diz.
        return not pedido_nao_achado
    if "rastreio" in pede and not (ctx.get("logistica") or {}).get("rastreio"):
        return True
    return "nota_fiscal" in pede and not (ctx.get("nota_fiscal") or {}).get("numero")


def _so_anexo(rajada: Sequence[Any]) -> bool:
    """O trecho do cliente não tem texto e traz foto/vídeo/arquivo/áudio de verdade."""
    if not rajada or any((m.texto or "").strip() for m in rajada):
        return False
    return any(
        (m.tipo or "") in TIPOS_ANEXO and not e_cartao_do_comprador(getattr(m, "payload", None))
        for m in rajada
    )


def sugestao_pede_pessoa(sugestao: Any | None) -> bool:
    """A sugestão da IA (do turno) mandou para pessoa? PURA."""
    if sugestao is None or sugestao.status not in (RASCUNHO_PENDENTE, RASCUNHO_BLOQUEADO):
        return False
    return (
        bool(sugestao.precisa_humano)
        or not sugestao.validador_ok
        or not (sugestao.texto or "").strip()
    )


def triar(
    conversa: Any,
    recentes: Sequence[Any],
    ctx: dict,
    *,
    so_humano: Collection[str],
    sugestao: Any | None = None,
    gatilho: Any | None = None,
) -> Triagem:
    """Os motivos para PESSOA desta conversa (ver o topo). PURA: sem banco, sem modelo, sem rede.

    `recentes` = as últimas trocas, da mais antiga para a mais nova (sem as
    que falharam e sem nota interna — `ia._mensagens_recentes`); `ctx` =
    `contexto.contexto_da_conversa`; `so_humano` = os assuntos só de pessoa
    (`manual.ids_so_humano`); `sugestao` = a sugestão da IA do turno atual
    (ou None); `gatilho` = a última mensagem do cliente.
    """
    motivos: set[str] = set()
    etiquetas = _etiquetas(conversa)
    pedido = ctx.get("pedido")

    # ── Estado do pedido e da conversa ──
    if (
        conversa.canal == CANAL_RECLAMACAO
        or ETIQUETA_RECLAMACAO in etiquetas
        or ia._reclamacoes_abertas(ctx)
        or reclamacao_aberta(conversa.dados)
    ):
        motivos.add(HUMANO_RECLAMACAO)
    if ETIQUETA_AG_CANCELAMENTO in etiquetas or ia.em_aguardando_cancelamento(pedido):
        motivos.add(HUMANO_AG_CANCELAMENTO)
    if ETIQUETA_DEVOLUCAO in etiquetas or ctx.get("devolucoes"):
        motivos.add(HUMANO_DEVOLUCAO)
    if ctx.get("chamados"):
        motivos.add(HUMANO_CHAMADO)
    if _avaliacao_ruim(conversa) or any(
        n <= NOTA_BAIXA_AVALIACAO for n in ia._avaliacoes_pendentes(ctx)
    ):
        motivos.add(HUMANO_AVALIACAO)
    if motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados) == (
        MOTIVO_EMAIL_SO_PESSOA
    ):
        motivos.add(HUMANO_EMAIL)
    nao_achado = bool(
        conversa.canal != CANAL_PERGUNTA
        and (conversa.pedido_marketplace or "").strip()
        and pedido is None
    )
    if nao_achado:
        motivos.add(HUMANO_PEDIDO_NAO_ACHADO)

    # ── O que o cliente escreveu ──
    textos_cliente = [m.texto for m in recentes if m.autor == AUTOR_CLIENTE and m.texto]
    motivos.update(codigo for codigo, _frase in ia.sinais_com_codigo(textos_cliente))
    assuntos = list(
        dict.fromkeys(
            p for t in textos_cliente for p in ia.pistas_de_categoria(t) if p in so_humano
        )
    )
    if assuntos:
        motivos.add(HUMANO_ASSUNTO)
    rajada = _rajada(recentes, gatilho)
    pistas = ia.pistas_de_categoria(" ".join(m.texto or "" for m in rajada))
    if _falta_dado(pistas, ctx, nao_achado):
        motivos.add(HUMANO_SEM_DADO)
    if _so_anexo(rajada) or _so_anexo(_depois_da_loja(recentes, gatilho)):
        motivos.add(HUMANO_SO_ANEXO)

    # ── A sugestão da IA ──
    if sugestao_pede_pessoa(sugestao):
        motivos.add(HUMANO_IA)

    # ── A bloqueada: só pelo estado, e "responder na plataforma" junto ──
    if conversa.situacao == CONVERSA_BLOQUEADA:
        if not motivos & HUMANO_DE_ESTADO:
            return Triagem()
        motivos.add(HUMANO_BLOQUEADA)

    return Triagem(motivos=tuple(m for m in ORDEM_HUMANO if m in motivos), assuntos=tuple(assuntos))


def para_tela(cache: Any, *, ia_pausada: bool) -> dict[str, list[str]] | None:
    """O `humano` da linha da lista/detalhe a partir do que a consulta trouxe. PURA.

    `cache` = `triagem_da_linha_sql()`: None = fora da Caixa Humano; `{}` =
    dentro só pela IA pausada (o cálculo gravado é de outro turno); senão o
    `dados.humano` do turno atual.
    """
    if cache is None:
        return None
    dados = cache if isinstance(cache, dict) else {}
    motivos = [m for m in _lista(dados.get("motivos")) if m != HUMANO_IA_PAUSADA]
    if ia_pausada:
        motivos.append(HUMANO_IA_PAUSADA)
    assuntos = _lista(dados.get("assuntos"))
    return {
        "motivos": motivos,
        "rotulos": [rotulo_humano(m) for m in motivos],
        "assuntos": assuntos,
        "assuntos_rotulos": [CATEGORIAS_INFO.get(a, (a, ""))[0] for a in assuntos],
    }


def _lista(valor: Any) -> list[str]:
    if not isinstance(valor, list):
        return []
    return list(dict.fromkeys(v for v in valor if isinstance(v, str) and v))


# ── SQL (lista, /resumo, detalhe e o cron) ────────────────────────────────


def _campo(nome: str):
    """`dados #> '{humano,<nome>}'` (JSONB; NULL quando falta)."""
    return AtendimentoConversa.dados[(CHAVE, nome)]


def _epoch(expr):
    """to_jsonb(extract(epoch from <expr>)): o número que o cache grava e compara."""
    return func.to_jsonb(extract("epoch", expr), type_=JSONB)


def turno_sql():
    """O turno da conversa: a última mensagem do cliente, como o cache guarda.

    Sem mensagem do cliente (a reclamação/devolução só com mensagem do sistema
    ou do mediador): a criação da conversa — o turno muda quando o cliente
    escrever; o estado (etiqueta, devolução) é revisto pela régua de
    desatualizada.
    """
    c = AtendimentoConversa
    return _epoch(func.coalesce(c.ultima_do_cliente_em, c.created_at))


def escopo_sql():
    """WHERE de quem pode estar na Caixa Humano — a gêmea de `no_escopo`. Nunca NULL."""
    c = AtendimentoConversa
    fonte = func.coalesce(func.btrim(c.dados["fonte"].astext), "")
    ponte = and_(
        c.canal == CANAL_EMAIL,
        fonte == FONTE_TUTA,
        func.coalesce(func.jsonb_typeof(c.dados["mail"]), "") == "object",
    )
    caixa = and_(c.plataforma.in_(PLATAFORMAS_CAIXA), ~ia._sql_canal_sem_envio())
    return and_(
        c.aguardando_resposta.is_(True),
        c.situacao != CONVERSA_FECHADA,
        or_(caixa, ponte),
    )


def triada_sql():
    """O cálculo gravado é do turno atual? Nunca NULL (sem cálculo = não)."""
    return func.coalesce(_campo("turno") == turno_sql(), false())


def caixa_humano_sql():
    """A conversa está na Caixa Humano AGORA? Nunca NULL — lista, /resumo e detalhe.

    No escopo E (o cálculo do turno atual tem motivo OU a IA está pausada —
    menos na bloqueada, que só entra pelo estado). Sem conversão de tipo que
    possa dar erro: chave ausente vira NULL = não.
    """
    c = AtendimentoConversa
    com_motivo = and_(triada_sql(), func.coalesce(_campo("motivos") != _JSON_LISTA_VAZIA, false()))
    pausada = and_(c.ia_pausada.is_(True), c.situacao != CONVERSA_BLOQUEADA)
    return and_(escopo_sql(), or_(com_motivo, pausada))


def triagem_da_linha_sql():
    """O que a linha leva para `para_tela`: NULL fora; `{}` só pela IA pausada; o cache."""
    return case(
        (
            caixa_humano_sql(),
            case((triada_sql(), AtendimentoConversa.dados[CHAVE]), else_=_JSON_OBJETO_VAZIO),
        ),
        else_=null(),
    )


def desatualizada_sql(agora: datetime):
    """O cálculo gravado precisa ser refeito? Nunca NULL (ver "O CRON" no topo)."""
    c = AtendimentoConversa
    rascunho = aliased(AtendimentoRascunho)
    em = _campo("em")
    mexida_depois = (
        exists().where(rascunho.conversa_id == c.id, _epoch(rascunho.updated_at) > em).correlate(c)
    )
    limite = cast(literal(agora - REVISAO), DateTime(timezone=True))
    fresca = and_(
        _campo("v") == literal_column(f"'{int(VERSAO)}'::jsonb", type_=JSONB),
        triada_sql(),
        or_(c.etiqueta_desde.is_(None), em >= _epoch(c.etiqueta_desde)),
        ~mexida_depois,
        em >= _epoch(limite),
    )
    return ~func.coalesce(fresca, false())


async def desatualizadas(
    session: AsyncSession, *, limite: int = MAX_POR_RODADA, agora: datetime | None = None
) -> list[tuple[UUID, datetime | None]]:
    """(id, `ultima_do_cliente_em` lida) das que triar: turno novo primeiro, depois o prazo."""
    c = AtendimentoConversa
    agora = agora or datetime.now(UTC)
    linhas = (
        await session.execute(
            select(c.id, c.ultima_do_cliente_em)
            .where(escopo_sql(), desatualizada_sql(agora))
            .order_by(
                case((triada_sql(), 1), else_=0),
                c.prazo_resposta_em.asc().nulls_last(),
                c.id,
            )
            .limit(max(1, limite))
        )
    ).all()
    return [(i, u) for i, u in linhas]


async def gravar_triagem(
    session: AsyncSession, conversa_id: UUID, lido: datetime | None, triagem: Triagem
) -> bool:
    """Grava `dados.humano` com UM UPDATE protegido. Não commita. True = gravou.

    Só a chave `humano` (jsonb_set: as outras ficam), na linha travada com
    FOR NO KEY UPDATE SKIP LOCKED (ocupada = não grava, fica para a próxima)
    e só se a última mensagem do cliente ainda é a lida (`lido`). O `turno`
    sai da própria coluna; o `em` é o começo da transação (`now()`), ANTES
    das leituras da triagem — o que mudar depois delas cai na régua de
    desatualizada. O `updated_at` fica como estava: é cache, não mudança.
    """
    c = AtendimentoConversa
    alvo_c = aliased(AtendimentoConversa)
    alvo = (
        select(alvo_c.id)
        .where(
            alvo_c.id == conversa_id,
            # Sem mensagem do cliente (None): continua sem.
            alvo_c.ultima_do_cliente_em.is_not_distinct_from(lido),
        )
        .with_for_update(skip_locked=True, key_share=True)
        .scalar_subquery()
    )
    base = {"v": VERSAO, "motivos": list(triagem.motivos), "assuntos": list(triagem.assuntos)}
    valor = cast(literal(json.dumps(base), Text), JSONB).op("||", return_type=JSONB)(
        func.jsonb_build_object(
            literal_column("'turno'"),
            turno_sql(),
            literal_column("'em'"),
            _epoch(func.now()),
        )
    )
    resultado = await session.execute(
        update(c)
        .where(c.id == alvo)
        .values(
            dados=func.jsonb_set(
                func.coalesce(c.dados, _JSON_OBJETO_VAZIO),
                literal_column("ARRAY['humano']::text[]"),
                valor,
            ),
            updated_at=c.updated_at,
        )
        .execution_options(synchronize_session=False)
    )
    return (resultado.rowcount or 0) == 1


# ── A leitura de uma conversa ─────────────────────────────────────────────


async def assuntos_so_humano(session: AsyncSession) -> set[str]:
    """Os assuntos só de pessoa (manual base ou constantes). Nunca levanta."""
    categorias = await contexto_svc._seguro(
        session,
        "categorias",
        lambda: manual_svc.categorias_ativas(session),
        manual_svc.categorias_padrao(),
        None,
    )
    return manual_svc.ids_so_humano(categorias)


async def _sugestao_do_turno(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoRascunho | None:
    """A sugestão da IA (pendente ou bloqueada) que responde o turno atual, ou None.

    Pelo GATILHO (a mensagem do cliente que ela responde) na última mensagem
    do cliente ou depois; sem gatilho, pela hora em que ela nasceu.
    """
    if conversa.ultima_do_cliente_em is None:
        return None
    gatilho = aliased(AtendimentoMensagem)
    momento = func.coalesce(gatilho.enviada_em, gatilho.created_at, AtendimentoRascunho.created_at)
    return (
        await session.execute(
            select(AtendimentoRascunho)
            .outerjoin(gatilho, gatilho.id == AtendimentoRascunho.mensagem_gatilho_id)
            .where(
                AtendimentoRascunho.conversa_id == conversa.id,
                AtendimentoRascunho.status.in_((RASCUNHO_PENDENTE, RASCUNHO_BLOQUEADO)),
                momento >= conversa.ultima_do_cliente_em,
            )
            .order_by(AtendimentoRascunho.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def triar_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, *, so_humano: Collection[str]
) -> Triagem:
    """Lê o que a régua precisa (as mesmas leituras do `ia._gerar`) e tria. Só leitura."""
    gatilho = await ia._ultima_do_cliente(session, conversa)
    recentes = await ia._mensagens_recentes(session, conversa)
    if gatilho is not None and gatilho.id not in {m.id for m in recentes}:
        # A loja falou 8 vezes depois (o robô, as campanhas): a pergunta entra
        # na frente, como no `_gerar`.
        recentes = [gatilho, *recentes[-(ia.MAX_TROCAS - 1) :]]
    ctx = await contexto_svc.contexto_da_conversa(session, conversa)
    sugestao = await _sugestao_do_turno(session, conversa)
    return triar(conversa, recentes, ctx, so_humano=so_humano, sugestao=sugestao, gatilho=gatilho)


# ── A rodada ──────────────────────────────────────────────────────────────


async def recalcular(
    *,
    limite: int = MAX_POR_RODADA,
    agora: datetime | None = None,
    orcamento_s: float | None = ORCAMENTO_S,
) -> dict[str, int]:
    """Uma rodada: tria as desatualizadas e grava (sessão própria). Nunca levanta por conversa.

    `puladas` = mudou entre a escolha e a gravação (mensagem nova, saiu do
    escopo) ou a linha estava travada — ficam para a próxima rodada;
    `adiadas` = o orçamento de tempo acabou antes delas.
    """
    resumo = {
        "candidatas": 0,
        "gravadas": 0,
        "na_caixa": 0,
        "puladas": 0,
        "falhas": 0,
        "adiadas": 0,
    }
    inicio = time.monotonic()
    # `_db.SessionLocal` lido na hora da chamada (os testes trocam o engine).
    async with _db.SessionLocal() as session:
        so_humano = await assuntos_so_humano(session)
        alvos = await desatualizadas(session, limite=limite, agora=agora)
        await session.rollback()
        resumo["candidatas"] = len(alvos)
        for n, (conversa_id, lido) in enumerate(alvos):
            if orcamento_s is not None and time.monotonic() - inicio > orcamento_s:
                resumo["adiadas"] = len(alvos) - n
                break
            try:
                conversa = await session.get(
                    AtendimentoConversa, conversa_id, populate_existing=True
                )
                if (
                    conversa is None
                    or not no_escopo(conversa)
                    or conversa.ultima_do_cliente_em != lido
                ):
                    resumo["puladas"] += 1
                    await session.rollback()
                    continue
                triagem = await triar_conversa(session, conversa, so_humano=so_humano)
                if await gravar_triagem(session, conversa_id, lido, triagem):
                    resumo["gravadas"] += 1
                    resumo["na_caixa"] += triagem.para_pessoa
                else:
                    resumo["puladas"] += 1
                await session.commit()
            except Exception as e:  # noqa: BLE001 — uma conversa ruim não para a rodada
                resumo["falhas"] += 1
                logger.warning(
                    "atendimento_humano_conversa_falhou",
                    conversa_id=str(conversa_id),
                    err=type(e).__name__,
                )
                await session.rollback()
                await _marcar_falha(session, conversa_id, lido)
            finally:
                session.expunge_all()
    return resumo


async def _marcar_falha(session: AsyncSession, conversa_id: UUID, lido: datetime | None) -> None:
    """A triagem deu erro: grava o motivo `falha` (na dúvida, mostra). Nunca levanta.

    Com o `em` de agora, a régua de desatualizada só tenta de novo na revisão
    (30 min) ou quando algo mudar (mensagem nova, etiqueta, sugestão da IA):
    a conversa que falha sempre não fica na frente da fila em toda rodada.
    Mesmo UPDATE protegido da triagem (turno conferido, linha travada pulada).
    """
    try:
        await gravar_triagem(session, conversa_id, lido, Triagem(motivos=(HUMANO_FALHA,)))
        await session.commit()
    except Exception as e:  # noqa: BLE001 — o banco fora do ar: a próxima rodada tenta
        logger.warning(
            "atendimento_humano_falha_nao_marcada",
            conversa_id=str(conversa_id),
            err=type(e).__name__,
        )
        await session.rollback()


async def _trava_da_rodada() -> tuple[bool, str | None]:
    """SET NX da rodada (por schema) → (pegou, token). Redis fora do ar = roda sem trava.

    Sem a trava o pior caso é triar duas vezes: a gravação é protegida (linha
    travada, turno conferido) e o resultado é o mesmo.
    """
    chave = _CHAVE_RODADA.format(get_settings().database_schema)
    token = uuid4().hex
    try:
        pegou = await redis.set(chave, token, nx=True, ex=RODADA_TTL_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_humano_trava_indisponivel", err=type(e).__name__)
        return True, None
    return bool(pegou), token


async def _soltar_rodada(token: str | None) -> None:
    if token is None:
        return
    chave = _CHAVE_RODADA.format(get_settings().database_schema)
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            chave,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_humano_trava_soltar_falhou")


async def atendimento_humano(ctx: dict) -> dict[str, int] | None:
    """A cada minuto: tria para a Caixa Humano o que mudou. Nunca levanta.

    Só lê o banco do DaVinci e grava `dados.humano` — nada sai para a
    plataforma, o modelo não é chamado. Interruptor próprio
    (`atendimento_humano_ativa`, que nasce LIGADO: é só cache) junto com a
    leitura (`atendimento_leitura_ativa`). Uma rodada por vez (trava no Redis).
    """
    s = get_settings()
    if not (s.atendimento_leitura_ativa and s.atendimento_humano_ativa):
        return None
    pegou, token = await _trava_da_rodada()
    if not pegou:
        logger.info("atendimento_humano_rodada_ocupada")
        return None
    try:
        resumo = await recalcular()
    except Exception as e:  # noqa: BLE001
        logger.error("atendimento_humano_falhou", err=type(e).__name__)
        return None
    finally:
        await _soltar_rodada(token)
    if resumo["candidatas"]:
        logger.info("atendimento_humano_tick", **resumo)
    return resumo


# ── O recálculo inicial e a simulação (o script) ──────────────────────────


async def simular(*, limite: int | None = None) -> dict[str, Any]:
    """Tria TODAS as conversas do escopo numa transação SÓ LEITURA e desfaz. Só contagens.

    É o "seco" do `scripts/atendimento_humano_recalcular.py`: mostra quantas
    entrariam na Caixa Humano, por plataforma e por motivo, antes de gravar.
    Nada de comprador (nem nome, nem nº de pedido, nem texto).
    """
    c = AtendimentoConversa
    contagem: dict[str, Counter[str]] = defaultdict(Counter)
    principal: dict[str, Counter[str]] = defaultdict(Counter)
    assuntos: dict[str, Counter[str]] = defaultdict(Counter)
    quantos: Counter[int] = Counter()
    resumo: dict[str, Any] = {"seco": True, "no_escopo": 0, "na_caixa": 0, "falhas": 0}
    async with _db.SessionLocal() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        so_humano = await assuntos_so_humano(session)
        consulta = (
            select(c).where(escopo_sql()).order_by(c.prazo_resposta_em.asc().nulls_last(), c.id)
        )
        if limite is not None:
            consulta = consulta.limit(max(1, limite))
        conversas = list((await session.execute(consulta)).scalars().all())
        for conversa in conversas:
            p = conversa.plataforma
            resumo["no_escopo"] += 1
            contagem[p]["no_escopo"] += 1
            try:
                async with session.begin_nested():
                    triagem = await triar_conversa(session, conversa, so_humano=so_humano)
            except Exception as e:  # noqa: BLE001
                resumo["falhas"] += 1
                logger.warning(
                    "atendimento_humano_simular_falhou",
                    conversa_id=str(conversa.id),
                    err=type(e).__name__,
                )
                continue
            motivos = list(triagem.motivos)
            if conversa.ia_pausada and (motivos or conversa.situacao != CONVERSA_BLOQUEADA):
                motivos.append(HUMANO_IA_PAUSADA)
            quantos[len(motivos)] += 1
            if not motivos:
                continue
            resumo["na_caixa"] += 1
            contagem[p]["na_caixa"] += 1
            principal[p][motivos[0]] += 1
            for m in motivos:
                contagem[p][f"motivo:{m}"] += 1
            for a in triagem.assuntos:
                assuntos[p][a] += 1
        await session.rollback()
    resumo["por_plataforma"] = {p: dict(v) for p, v in sorted(contagem.items())}
    resumo["motivo_principal"] = {p: dict(v) for p, v in sorted(principal.items())}
    resumo["assuntos"] = {p: dict(v) for p, v in sorted(assuntos.items())}
    resumo["quantos_motivos"] = {str(k): v for k, v in sorted(quantos.items())}
    return resumo


async def preencher(*, lote: int = MAX_POR_RODADA, max_rodadas: int = 50) -> dict[str, Any]:
    """Grava o cálculo das desatualizadas até não sobrar nenhuma (o recálculo inicial).

    Rodadas de `recalcular` sem orçamento de tempo; para quando não há mais
    candidatas ou uma rodada não grava nada (o que sobrou está travado ou
    falhando — o cron pega depois). Usa a MESMA trava do cron: com uma
    rodada do cron no meio, espera ela acabar (até ~2,5 min).
    """
    total: Counter[str] = Counter()
    rodadas = 0
    for _ in range(max(1, max_rodadas)):
        pegou, token = await _trava_da_rodada()
        espera = 0
        while not pegou and espera < RODADA_TTL_S:
            await asyncio.sleep(1.0)
            espera += 1
            pegou, token = await _trava_da_rodada()
        try:
            parcial = await recalcular(limite=lote, orcamento_s=None)
        finally:
            await _soltar_rodada(token)
        rodadas += 1
        total.update(parcial)
        if not parcial["candidatas"] or not parcial["gravadas"]:
            break
    async with _db.SessionLocal() as session:
        agora = await contar(session)
    return {"seco": False, "rodadas": rodadas, **dict(total), "agora": agora}


async def contar(session: AsyncSession) -> dict[str, Any]:
    """Quantas estão na Caixa Humano agora, por plataforma e pelo motivo principal. Só contagens."""
    c = AtendimentoConversa
    # Rótulo: o GROUP BY vai pelo nome (a expressão repetida teria outros
    # parâmetros e o Postgres não a reconheceria como a mesma).
    motivo = case((triada_sql(), c.dados[(CHAVE, "motivos", "0")].astext), else_=None).label(
        "motivo_principal"
    )
    linhas = (
        await session.execute(
            select(c.plataforma, motivo, func.count())
            .where(caixa_humano_sql())
            .group_by(c.plataforma, motivo)
        )
    ).all()
    por_plataforma: Counter[str] = Counter()
    principal: dict[str, Counter[str]] = defaultdict(Counter)
    for plataforma, motivo, n in linhas:
        por_plataforma[plataforma] += int(n)
        principal[plataforma][motivo or HUMANO_IA_PAUSADA] += int(n)
    no_escopo = int(
        await session.scalar(select(func.count()).select_from(c).where(escopo_sql())) or 0
    )
    return {
        "no_escopo": no_escopo,
        "na_caixa": sum(por_plataforma.values()),
        "por_plataforma": dict(sorted(por_plataforma.items())),
        "motivo_principal": {p: dict(v) for p, v in sorted(principal.items())},
    }
