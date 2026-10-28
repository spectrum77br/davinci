"""O REPLAY do agente: o que ele TERIA feito num gatilho que já passou (spec 8 e 9.2, 20/10/2026).

Refaz UM gatilho do cliente com o agente de verdade — os mesmos passos do
`ia._gerar_pelo_agente` (`como_o_gerar`: o que vai para pessoa antes do
modelo, `agente.responder` com o Tool Runner do SDK, a etiqueta Devolução e
a pergunta de produto) —, sem efeito nenhum na operação:

- `as_of` = o instante do gatilho: a transcrição só tem o que existia até
  ali (`mensagens_ate`), os exemplos da equipe só de antes (`pares.buscar`
  recebe o `as_of` pelo `ContextoAgente`), e o pedido COMPRADO depois do
  gatilho não existe (`RetratoConversa`: a conversa da pré-venda não "sabe"
  do pedido que veio depois). O resto — entrega, estoque, garantia — vale o
  estado de AGORA (spec 9.2: a nota avalia a coerência com o rastro);
- sem ida à loja (`sem_ida_a_loja`): a ficha e as variações vêm do cache e
  do banco (o `scripts/agente_eval/preaquecer.py` aquece antes do eval);
- nada é gravado nem enviado: nenhum rascunho, nenhuma mudança na Caixa
  Humano, nenhuma mensagem. A sessão de quem chama é SÓ LEITURA
  (`sessao_replay`: toda transação dela começa com `SET TRANSACTION READ
  ONLY`, inclusive depois do commit que o laço faz antes de chamar o modelo);
- no lugar do envio, a DECISÃO SIMULADA (`decisao_simulada`, pura): as
  travas de `ia._talvez_enviar` sem efeito, com a régua do agente do próprio
  `ia` (`ia.trava_do_agente`, spec 6.6: ação de pessoa = pessoa; assunto de
  orientação só sai com `canal.auto_orientacao`).

`gravar=True` grava UMA linha em `atendimento_agente_replay` (o replay
noturno; retenção de 30 dias, `expurgar`), numa sessão PRÓPRIA — tudo
mascarado: a proposta e a decisão pelo `mascarar` (com os nomes do pedido e
a cidade/bairro/endereço do destino), a resposta real da equipe também pelo
`_generalizar`. O rastro guarda o
conteúdo COMPLETO das ferramentas (lido do que foi à API: `Captura`), o
pacote e a transcrição; no rascunho de verdade fica só o enxuto.

O passo a passo do `_gerar` até o despacho para o agente (`montar_base`) é
uma CÓPIA dos passos 1-4 da spec 3.1 (o `_gerar` até o `BaseDoGerar`), com o
corte no `as_of` e sem a ida à loja: o `ia.py` ainda não tem um montador da
`BaseDoGerar` que o replay possa chamar (pedido no relatório). Mudou o
`_gerar`, mude aqui.

Onde roda: dentro do container (`scripts/agente_eval/rodar.py`, o eval; o
`replay_noturno`, o cron), fora das janelas de postagem (11:55–12:20 e
18:55–19:20 em São Paulo) e com a chave Claude.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import anthropic
import structlog
from sqlalchemy import event, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    BlingOrder,
)
from app.models.atendimento import AtendimentoAgenteReplay
from app.services.atendimento import agente, agente_config, constantes, ia, separa_email
from app.services.atendimento import contexto as contexto_svc
from app.services.atendimento import ficha as ficha_svc
from app.services.atendimento import manual as manual_svc
from app.services.atendimento.agente import acao_pessoa, custo, pacote, saida
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente.contexto import (
    BaseDoGerar,
    ContextoAgente,
    ExtraAgente,
    ordenar_acoes,
)
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    MODO_AUTO,
    MODO_COPILOTO,
    MSG_FALHOU,
    ORIGEM_EXTERNO,
    ORIGEM_HUMANO,
    ORIGEM_NOTA,
    PROMPT_VERSAO_AGENTE,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
)
from app.services.atendimento.pendente import resposta_de_pessoa

logger = structlog.get_logger()

# ── Quando NÃO rodar ──────────────────────────────────────────────────────
# As janelas das postagens automáticas (12h e 19h): replay/eval nelas
# disputa CPU e banco com o executor do TikTok (incidentes de 06 e 07/10).
# Meio-abertas: 11:55 até 12:20 inclusive.
JANELAS_DE_POSTAGEM: tuple[tuple[time, time], ...] = (
    (time(11, 55), time(12, 21)),
    (time(18, 55), time(19, 21)),
)

# ── O replay ──────────────────────────────────────────────────────────────
# Concorrência do eval e do replay noturno (spec 3.4): 4 conversas por vez.
CONCORRENCIA = 4
# As consultas da sessão do replay: um teto por comando (o legado não tem;
# aqui uma consulta presa seguraria a rodada da noite inteira).
STATEMENT_TIMEOUT_MS = 15_000
# A resposta REAL da equipe (a referência da nota, spec 9.2): as próximas
# mensagens de pessoa da loja depois do gatilho (Duoke ou DaVinci, nunca a
# automática nem a IA), até 3, em até 24 h.
MAX_RESPOSTAS_REAIS = 3
JANELA_RESPOSTA_REAL = timedelta(hours=24)
ORIGENS_DA_EQUIPE = frozenset({ORIGEM_EXTERNO, ORIGEM_HUMANO})
# O que o rastro do replay guarda (o pacote e cada resultado de ferramenta).
MAX_CHARS_PACOTE = 16_000
MAX_CHARS_FERRAMENTA = 5_000
MAX_FERRAMENTAS_RASTRO = 12
# O replay noturno: teto de gatilhos por noite (a Shopee inteira tem ~340).
MAX_REPLAY_NOITE = 600
# A retenção das linhas do replay (`atendimento_agente_replay`): 30 dias.
RETENCAO_DIAS = 30
# O turno lido para achar gatilhos: a conversa desde 2 dias antes do período
# (a rajada do cliente pode ter começado antes) até 1 dia depois (a resposta).
MARGEM_ANTES = timedelta(days=2)
MARGEM_DEPOIS = timedelta(days=1)

# As paradas que não viram linha no replay: nada a avaliar (o teto do dia, o
# provedor fora) — a próxima rodada pode refazer o gatilho.
PARADAS_SEM_LINHA = frozenset({agente.PARADA_TETO, agente.PARADA_PROVEDOR})

# Por que o agente nem rodou (`ReplayGatilho.pulado`).
PULADO_SEM_GATILHO = "sem_gatilho"
PULADO_CANAL_SEM_ENVIO = "canal_sem_envio"
PULADO_ATALHO = "atalho_aprovado"
# O turno só com o cartão do produto/pedido: sem rascunho (spec 6.2).
PULADO_SO_CARTAO = "so_cartao"
# O braço L0 do eval (`rascunho_do_legado`): o legado não escreveu sugestão
# para este gatilho (canal fora dos modos da IA, o cron não pegou...).
PULADO_SEM_LEGADO = "sem_rascunho_do_legado"
# A `parada` do registro do braço L0 (a sugestão do legado, lida do banco).
PARADA_LEGADO = "legado"

# A decisão simulada (`decisao_simulada`): o 1º motivo que segura o envio,
# na ordem de `ia._talvez_enviar` e com os mesmos códigos (os do assunto são
# os de `ia.trava_do_agente`); as travas de agora — atribuída, pausa, já
# respondida — não valem para um gatilho que já passou.
ENVIARIA = "enviaria"
SEM_RASCUNHO = "sem_rascunho"
RASCUNHO_NAO_PENDENTE = "rascunho_nao_pendente"
VALIDADOR_REPROVOU = "validador_reprovou"
ACAO_DE_PESSOA = "acao_pessoa"
EM_ANDAMENTO = "em_andamento"
PRECISA_HUMANO = "precisa_humano"
ASSUNTO_SO_HUMANO = "assunto_so_humano"
CONFIANCA_BAIXA = "confianca_baixa"
SEM_TEXTO = "sem_texto"
PLATAFORMA_SEM_AUTO = "plataforma_sem_auto"
CANAL_SEM_ENVIO = "canal_sem_envio"
RECLAMACAO_ABERTA = "reclamacao_aberta"
CANAL_FORA_DO_AUTO = "canal_fora_do_auto"
ORIENTACAO_NAO_LIBERADA = "orientacao_nao_liberada"
ASSUNTO_NAO_LIBERADO = "assunto_nao_liberado"
# Revisão de 20/10/2026 (os de `ia.trava_do_agente`): o aviso que só valeria
# no automático e o assunto fora do `auto_agente` (decisão 8).
AVISOS_AUTO = "avisos_auto"
ASSUNTO_NAO_LIBERADO_AGENTE = "assunto_nao_liberado_agente"


def agora_sp(agora: datetime | None = None) -> datetime:
    return (agora or datetime.now(UTC)).astimezone(ia.SAO_PAULO)


def janela_de_postagem(agora: datetime | None = None) -> str | None:
    """A janela ("11:55–12:20") em que `agora` (São Paulo) cai; fora delas, None. PURA."""
    hora = agora_sp(agora).time()
    for inicio, fim in JANELAS_DE_POSTAGEM:
        if inicio <= hora < fim:
            ultimo = (datetime.combine(date.min, fim) - timedelta(minutes=1)).time()
            return f"{inicio:%H:%M}–{ultimo:%H:%M}"
    return None


def dia_sp(quando: datetime) -> date:
    """O dia de São Paulo de um instante (o `dia` da linha do replay)."""
    return agora_sp(quando).date()


# ── A sessão só de leitura ────────────────────────────────────────────────


def _so_leitura(timeout_ms: int):
    def _no_comeco(_session: Any, _transacao: Any, conexao: Any) -> None:
        # O 1º comando de TODA transação desta sessão (também a que começa
        # depois do commit do laço): escrita por engano dá erro 25006.
        conexao.exec_driver_sql("SET TRANSACTION READ ONLY")
        conexao.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_ms)}")

    return _no_comeco


@asynccontextmanager
async def sessao_replay(*, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> AsyncIterator[AsyncSession]:
    """A sessão do replay: SÓ LEITURA em toda transação; sai com rollback.

    Diferente da `sessao.sessao_leitura` (das ferramentas, uma transação
    só): o laço do agente faz COMMIT na sessão de quem chama antes de ir ao
    modelo (spec 3.3.1, regra 8) e a transação seguinte (a `conferir_saida`)
    também tem de ser só leitura — por isso o `SET TRANSACTION READ ONLY` vai
    num evento `after_begin`, e não uma vez no começo. A conexão volta ao pool
    sem nada mudado (o `SET LOCAL` morre com a transação).
    """
    async with _db.SessionLocal() as session:
        ouvinte = _so_leitura(max(1, int(timeout_ms)))
        event.listen(session.sync_session, "after_begin", ouvinte)
        try:
            yield session
        finally:
            try:
                await session.rollback()
            finally:
                event.remove(session.sync_session, "after_begin", ouvinte)


# ── O que foi à API (para o rastro completo) ──────────────────────────────


@dataclass
class Captura:
    """Os corpos dos pedidos à API: o ÚLTIMO tem a conversa inteira do laço.

    É de lá que sai o conteúdo de cada ferramenta (o `tool_result`) — o
    `ContextoAgente` só guarda o rastro enxuto. Ouvinte do httpx2 (`event_hooks`
    "request"); nunca levanta (o replay não pode cair por causa do rastro).
    """

    corpo: dict | None = None
    pedidos: int = 0

    async def ouvir(self, request: Any) -> None:
        try:
            corpo = json.loads(bytes(request.content or b"{}"))
        except Exception:  # noqa: BLE001 — corpo que não é JSON não interessa
            return
        if isinstance(corpo, dict) and isinstance(corpo.get("messages"), list):
            self.corpo = corpo
            self.pedidos += 1

    def _mensagens(self) -> list[dict]:
        return [m for m in (self.corpo or {}).get("messages") or [] if isinstance(m, dict)]

    def pacote(self) -> str | None:
        """O texto da 1ª mensagem (o pacote que o modelo recebeu)."""
        mensagens = self._mensagens()
        if not mensagens:
            return None
        return _texto_do_conteudo(mensagens[0].get("content")) or None

    def ferramentas(self) -> list[dict]:
        """Cada chamada de ferramenta com o resultado que voltou ao modelo, na ordem."""
        pedidas: dict[str, dict] = {}
        ordem: list[str] = []
        resultados: dict[str, dict] = {}
        for m in self._mensagens():
            blocos = m.get("content")
            if not isinstance(blocos, list):
                continue
            for b in blocos:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("id"):
                    pedidas[b["id"]] = {"ferramenta": b.get("name"), "args": b.get("input") or {}}
                    ordem.append(b["id"])
                elif b.get("type") == "tool_result" and b.get("tool_use_id"):
                    resultados[b["tool_use_id"]] = {
                        "resultado": _texto_do_conteudo(b.get("content")),
                        "erro": bool(b.get("is_error")),
                    }
        saida_: list[dict] = []
        for i in ordem[:MAX_FERRAMENTAS_RASTRO]:
            item = dict(pedidas[i])
            item.update(resultados.get(i, {"resultado": None, "erro": False}))
            saida_.append(item)
        return saida_


def _texto_do_conteudo(conteudo: Any) -> str:
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        return "".join(
            str(b.get("text") or "")
            for b in conteudo
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def cliente_do_replay(
    chave: str,
    captura: Captura | None = None,
    *,
    transporte: Any | None = None,
    max_retries: int | None = None,
) -> anthropic.AsyncAnthropic:
    """O cliente da API para o replay: o MESMO do laço (base, timeout, retries) + a captura.

    `transporte` = um `httpx2` falso (teste). Nunca a chave do Groq: quem
    chama passa `agente_config.chave_do_agente`.
    """
    opcoes: dict[str, Any] = {}
    if captura is not None:
        opcoes["event_hooks"] = {"request": [captura.ouvir]}
    if transporte is not None:
        opcoes["transport"] = transporte
    return anthropic.AsyncAnthropic(
        api_key=chave,
        base_url=ia.BASE_CLAUDE,
        timeout=agente.TIMEOUT,
        max_retries=agente.MAX_RETRIES if max_retries is None else max_retries,
        http_client=anthropic.DefaultAsyncHttpxClient(**opcoes),
    )


# ── Máscara (o que fica gravado e o que vai para a nota) ──────────────────

# "São Paulo/SP", "Rio de Janeiro - RJ": a cidade com a UF (o status da
# transportadora, a fala da equipe). A do destino do pedido sai pelo nome.
_UFS = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"
_CIDADE_UF = re.compile(
    r"\b[A-ZÀ-Ú][\wÀ-ú'.-]*(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][\wÀ-ú'.-]*){0,3}"
    rf"\s*(?:/|\s[-–]\s)(?:{_UFS})\b"
)
# "moro em Campinas", "sou de Belo Horizonte", "aqui em Santos": a cidade que
# o comprador conta sem a UF (o estado na pergunta de cobertura — "enviam para
# o Rio de Janeiro?" — fica: é o dado da pergunta, não onde a pessoa mora).
_MORA_EM = re.compile(
    r"(?i:\b(?:moro|resido|sou|aqui|cidade)\s+(?:em|de|no|na|d[ao])\s+)"
    r"([A-ZÀ-Ú][\wÀ-ú'.-]*(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][\wÀ-ú'.-]*){0,3})"
)
_MIN_CHARS_TERMO = 3


def _sem_termos(texto: str, termos: Iterable[str | None]) -> str:
    for termo in sorted({(t or "").strip() for t in termos}, key=len, reverse=True):
        if len(termo) >= _MIN_CHARS_TERMO:
            texto = re.sub(re.escape(termo), "[LOCAL]", texto, flags=re.IGNORECASE)
    texto = _MORA_EM.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + "[LOCAL]", texto)
    return _CIDADE_UF.sub("[LOCAL]", texto)


def _mascarar_fora_das_evidencias(texto: str, nomes: tuple[str | None, ...]) -> str:
    """O `ia.mascarar` em tudo menos nos ids de evidência (`EVIDENCIA_NO_TEXTO`),
    que passam só pela máscara dos nomes. PURA."""
    partes: list[str] = []
    ultimo = 0
    for m in agente_ctx.EVIDENCIA_NO_TEXTO.finditer(texto):
        partes.append(ia.mascarar(texto[ultimo : m.start()], nomes))
        evidencia = m.group(0)
        for nome in nomes:
            evidencia = ia._mascarar_nome(evidencia, nome)
        partes.append(evidencia)
        ultimo = m.end()
    partes.append(ia.mascarar(texto[ultimo:], nomes))
    return "".join(partes)


def mascarar_texto(
    texto: str | None,
    nomes: Sequence[str | None] = (),
    termos: Iterable[str | None] = (),
    *,
    digitos: bool = True,
) -> str:
    """O `ia.mascarar` (CPF, telefone, e-mail, endereço, nomes) + cidade/bairro do destino. PURA.

    `termos` = a cidade, o bairro e o endereço do destino do pedido
    (`termos_do_pedido`). `digitos=False` (o pacote e o resultado das
    ferramentas, que já vêm mascarados): só nomes e termos — o id do anúncio
    da Shopee tem 11 dígitos e o `mascarar` o confundiria com telefone.

    Com `digitos`, o id de evidência escrito no texto ("evidência citada sem
    prova (variacoes:58265493964)", 21/10/2026) passa só pela máscara dos
    nomes: o `mascarar` o virava "variacoes:[CPF]", que não batia com a
    evidência citada (`rastro.evidencias`) nem com o registro.
    """
    t = texto or ""
    if digitos:
        t = _mascarar_fora_das_evidencias(t, tuple(nomes))
    else:
        for nome in nomes:
            t = ia._mascarar_nome(t, nome)
    return _sem_termos(t, termos)


def generalizar(
    texto: str | None, nomes: Sequence[str | None] = (), termos: Iterable[str | None] = ()
) -> str:
    """A fala da equipe sem dado de ninguém (`ia._generalizar` + cidade). PURA."""
    return _sem_termos(ia._generalizar(texto or "", tuple(nomes)), termos)


def relacunar(texto: str | None, valores: Mapping[str, str | None]) -> str:
    """O texto final com os VALORES das lacunas de volta a `{lacuna}`. PURA.

    O rascunho sai com o rastreio e o número do pedido preenchidos; o replay
    guarda a forma com lacuna (o que o modelo escreveu), sem o dado do pedido.
    """
    t = texto or ""
    pares = sorted(
        ((k, str(v).strip()) for k, v in valores.items() if v and len(str(v).strip()) >= 3),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    for nome, valor in pares:
        t = t.replace(valor, "{" + nome + "}")
    return t


async def termos_do_pedido(session: AsyncSession, conversa: Any) -> tuple[str, ...]:
    """A cidade, o bairro e o endereço do DESTINO do pedido da conversa (só para mascarar)."""
    numero = (getattr(conversa, "pedido_marketplace", None) or "").strip()
    if not numero:
        return ()

    async def _consulta() -> tuple[str, ...]:
        linhas = (
            await session.execute(
                select(
                    BlingOrder.cidade_destino,
                    BlingOrder.bairro_destino,
                    BlingOrder.endereco_destino,
                )
                .where(or_(BlingOrder.numeroloja == numero, BlingOrder.numero == numero))
                .limit(5)
            )
        ).all()
        return tuple(dict.fromkeys(t for linha in linhas for t in linha if t))

    return await contexto_svc._seguro(session, "termos_do_pedido", _consulta, (), conversa.id)


# ── A conversa como estava no `as_of` ─────────────────────────────────────


class RetratoConversa:
    """A conversa no `as_of`: o que mudou depois (o pedido comprado depois) sai.

    Só LEITURA de atributo (o resto vem da conversa do banco). Nunca vai à
    sessão: o replay não grava, e a `conferir_saida` roda com `forcar`
    (sem o `_loja_respondeu_depois`).
    """

    def __init__(self, conversa: Any, **troca: Any) -> None:
        object.__setattr__(self, "_conversa", conversa)
        object.__setattr__(self, "_troca", troca)

    def __getattr__(self, nome: str) -> Any:
        troca = object.__getattribute__(self, "_troca")
        if nome in troca:
            return troca[nome]
        return getattr(object.__getattribute__(self, "_conversa"), nome)

    def __setattr__(self, nome: str, valor: Any) -> None:
        raise AttributeError("o retrato da conversa é só leitura")


# As chaves de `dados` que ligam a conversa ao pedido (`contexto._chaves_do_pedido`).
_CHAVES_DO_PEDIDO_EM_DADOS = ("pack_id", "order_id", "pedido_mkt")


def _comprado_depois(ctx: Mapping[str, Any], as_of: datetime) -> bool:
    """O pedido foi comprado DEPOIS do gatilho (a pré-venda que virou compra)? PURA.

    `ctx["pedido"]["data"]` é só o DIA de São Paulo (`chamados.py`: a data do
    Bling já sai à meia-noite de SP). Ajuste r3: o dia era lido como meia-noite
    UTC (21:00 da véspera em SP) — a pergunta feita entre 21h e 24h via o pedido
    do dia seguinte. O dia da compra é comparado com o dia do gatilho, em SP:
    comprado depois = num DIA seguinte. A compra no mesmo dia fica (sem a hora,
    não dá para saber; a maioria das conversas do dia da compra é pós-venda).
    Com a hora (ISO completo), compara a hora.
    """
    pedido = ctx.get("pedido") if isinstance(ctx.get("pedido"), Mapping) else None
    bruto = (pedido or {}).get("data")
    if not bruto:
        return False
    texto = str(bruto).strip()
    momento = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
    try:
        if len(texto) <= 10:
            return date.fromisoformat(texto) > momento.astimezone(ia.SAO_PAULO).date()
        comprado = datetime.fromisoformat(texto)
    except ValueError:
        return False
    if comprado.tzinfo is None:
        comprado = comprado.replace(tzinfo=ia.SAO_PAULO)
    return comprado > momento


def sem_o_pedido(conversa: Any) -> RetratoConversa:
    """A conversa como era antes da compra: sem o pedido (nem as chaves dele em `dados`)."""
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    return RetratoConversa(
        conversa,
        pedido_marketplace=None,
        dados={k: v for k, v in dados.items() if k not in _CHAVES_DO_PEDIDO_EM_DADOS},
    )


# ── As mensagens até o `as_of` ────────────────────────────────────────────


async def gatilho_ate(
    session: AsyncSession, conversa: Any, as_of: datetime, *, gatilho_id: UUID | None = None
) -> AtendimentoMensagem | None:
    """A última mensagem do CLIENTE até o `as_of` (ou a `gatilho_id`, se for dele e de antes)."""
    m = AtendimentoMensagem
    consulta = select(m).where(
        m.conversa_id == conversa.id, m.autor == AUTOR_CLIENTE, ia._momento_col() <= as_of
    )
    if gatilho_id is not None:
        consulta = consulta.where(m.id == gatilho_id)
    return (
        await session.execute(
            consulta.order_by(ia._momento_col().desc(), m.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def mensagens_ate(
    session: AsyncSession, conversa: Any, as_of: datetime
) -> ia.HistoricoMensagens:
    """O `ia._mensagens_recentes` cortado no `as_of` (mesmos filtros, mesmo teto)."""
    m = AtendimentoMensagem
    visivel = separa_email.mensagem_visivel_sql(conversa)
    linhas = list(
        (
            await session.execute(
                select(m)
                .where(
                    m.conversa_id == conversa.id,
                    m.status != MSG_FALHOU,
                    m.origem != ORIGEM_NOTA,
                    ia._momento_col() <= as_of,
                    *([visivel] if visivel is not None else []),
                )
                .order_by(ia._momento_col().desc(), m.created_at.desc())
                .limit(ia.MAX_TROCAS_LIDAS + 1)
            )
        )
        .scalars()
        .all()
    )
    todas = list(reversed(linhas[: ia.MAX_TROCAS_LIDAS]))
    return ia.HistoricoMensagens(
        todas,
        pedido_marketplace=conversa.pedido_marketplace,
        turno_incompleto=(
            len(linhas) > ia.MAX_TROCAS_LIDAS and not any(ia.resposta_da_loja(x) for x in todas)
        ),
    )


def da_equipe(mensagem: Any) -> bool:
    """Resposta de PESSOA da equipe (Duoke ou DaVinci) — nunca a automática nem a IA. PURA."""
    return resposta_de_pessoa(mensagem) and getattr(mensagem, "origem", None) in ORIGENS_DA_EQUIPE


def resposta_da_equipe(mensagens: Sequence[Any], gatilho: Any) -> list[Any]:
    """As falas da equipe que responderam ao `gatilho` (até 3, em até 24 h). PURA.

    `mensagens` = as da conversa DEPOIS do gatilho, em ordem. Para na 1ª fala
    do cliente que vem depois de uma resposta (é outro turno) e na resposta
    da IA ou da automação que fechou a vez antes da equipe (não há resposta
    de pessoa para comparar).
    """
    limite = ia._momento(gatilho) + JANELA_RESPOSTA_REAL
    saida_: list[Any] = []
    for m in mensagens:
        if ia._momento(m) > limite:
            break
        if getattr(m, "autor", None) == AUTOR_CLIENTE:
            if saida_:
                break
            continue
        if da_equipe(m):
            saida_.append(m)
            if len(saida_) >= MAX_RESPOSTAS_REAIS:
                break
        elif ia.resposta_da_loja(m) and not saida_:
            # A IA (ou outra resposta que não é de pessoa) fechou a vez.
            break
    return saida_


async def mensagens_depois(
    session: AsyncSession, conversa: Any, gatilho: Any, *, limite: int = 20
) -> list[AtendimentoMensagem]:
    m = AtendimentoMensagem
    visivel = separa_email.mensagem_visivel_sql(conversa)
    momento = ia._momento(gatilho)
    return list(
        (
            await session.execute(
                select(m)
                .where(
                    m.conversa_id == conversa.id,
                    m.id != gatilho.id,
                    ia._momento_col() >= momento,
                    ia._momento_col() <= momento + JANELA_RESPOSTA_REAL,
                    *([visivel] if visivel is not None else []),
                )
                .order_by(ia._momento_col().asc(), m.created_at.asc())
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )


# ── A base do `_gerar`, cortada no `as_of` ────────────────────────────────


# Os textos aprovados dos atalhos do `_gerar` (os MESMOS objetos do `ia`).
TEXTO_NF_APROVADO = ia.TEXTO_NF_APROVADO
TEXTO_CARTAO_APROVADO = ia.TEXTO_CARTAO_APROVADO


@dataclass(frozen=True)
class Atalho:
    """O `_gerar` responderia SEM modelo (texto aprovado): o agente nem roda."""

    categoria: str
    origem: str  # "nota_fiscal" | "cartao" | "politica"
    texto: str


async def _fichas_sem_loja(
    session: AsyncSession, conversa: Any, recentes: Sequence[Any], textos: Sequence[str]
) -> list[dict]:
    """O `ficha.fichas_da_conversa` do legado SEM ida à loja (só cache e catálogo).

    É o que as políticas aprovadas (`politicas_atendimento.resposta_aprovada`)
    leem para decidir o atalho; o pacote do agente monta as dele.
    """
    plataforma = ficha_svc.plataforma_do_catalogo(conversa.plataforma)
    if plataforma is None:
        return []
    palavras = ficha_svc.palavras_da_pergunta(textos)
    fichas: list[dict] = []
    for anuncio in ficha_svc.anuncios_da_conversa(conversa, list(recentes)):
        achada = await ficha_svc.ficha_do_anuncio(
            session,
            plataforma,
            anuncio.item_id,
            conversa.integration_id,
            titulo=anuncio.titulo,
            variation_id=anuncio.variation_id,
            palavras=palavras,
            loja=None,
        )
        if achada is None:
            break
        fichas.append({"de_onde": anuncio.de_onde, **achada})
        if len(fichas) >= ficha_svc.MAX_ANUNCIOS_FICHA:
            break
    return ficha_svc.caber(fichas)


async def _sinais_sem_loja(session: AsyncSession, conversa: Any) -> list[str]:
    """Os sinais do cartão Cliente sem ida à loja: no ML o cartão consulta os
    pedidos do comprador AO VIVO — no replay, sem sinais lá."""
    if conversa.plataforma == "ml":
        return []
    return await ia._sinais_do_cliente(session, conversa)


async def montar_base(
    session: AsyncSession,
    conversa: Any,
    gatilho: Any,
    recentes: ia.HistoricoMensagens,
    *,
    as_of: datetime,
    ctx: dict,
    modo: str | None = None,
) -> tuple[BaseDoGerar, Atalho | None]:
    """A `BaseDoGerar` como o `_gerar` montaria no `as_of` (interfaces §7.1) e o atalho, se houver.

    Cópia dos passos do `_gerar` (ia:4457-4660): lacunas e fatos, rajada e
    foco, sinais com a régua do pedido de pessoa do agente
    (`acao_pessoa.sinais_do_agente`, 21/10/2026), os motivos que valem
    para o agente (sem a opção sensível do menu, a NF que exige pessoa, o
    recado ao entregador e a dúvida de remetente — spec 6.2), o cartão
    Cliente, os assuntos e o manual, e os atalhos aprovados (NF, cartão
    sozinho, política aprovada) que respondem sem modelo. `modo` = troca o
    do canal (o eval mede "como se o canal estivesse no automático").
    """
    from app.services.atendimento import politicas_atendimento, sem_demanda

    valores = ia.valores_das_lacunas(conversa, ctx)
    fatos = ia._fatos_para_o_modelo(conversa, ctx, valores)
    fatos_gravados = {**fatos, "lacunas": valores}

    rajada = ia.rajada_do_cliente(recentes, gatilho)
    textos_cliente = [m.texto for m in rajada if m.texto]
    foco = ia.perguntas_pendentes(recentes, gatilho)
    textos_rajada = [m.texto or "" for m in (foco or rajada)]
    # O pedido de pessoa pela régua do agente (21/10/2026), como o `_gerar`.
    sinais_texto = acao_pessoa.sinais_do_agente(
        ia.sinais_com_codigo(textos_cliente, agente=True), recentes, gatilho
    )
    insistiu = ia.insistiu_em_pessoa(recentes)
    motivos: list[str] = list(dict.fromkeys(f for _c, f in sinais_texto))
    opcoes_sensiveis = ia.assuntos_sensiveis_do_menu(recentes, gatilho)
    if ia.turno_incompleto(recentes, gatilho):
        motivos.append(ia.MOTIVO_TURNO_INCOMPLETO)

    sinais = await _sinais_sem_loja(session, conversa)
    if sinais:
        fatos["sinais_do_cliente"] = sinais
        fatos_gravados["sinais_do_cliente"] = sinais
        motivos += [ia.SINAIS_PARA_PESSOA[x] for x in sinais if x in ia.SINAIS_PARA_PESSOA]

    categorias = await contexto_svc._seguro(
        session,
        "categorias",
        lambda: manual_svc.categorias_ativas(session),
        manual_svc.categorias_padrao(),
        conversa.id,
    )
    ids_categorias = [c["id"] for c in categorias]
    todas_regras = await ia._regras_aplicaveis(session, conversa)
    fichas = await contexto_svc._seguro(
        session,
        "ficha_do_anuncio",
        lambda: _fichas_sem_loja(session, conversa, recentes, textos_rajada),
        [],
        conversa.id,
    )
    if fichas:
        fatos["ficha_do_anuncio"] = fatos_gravados["ficha_do_anuncio"] = fichas

    politica = politicas_atendimento.resposta_aprovada(
        conversa, textos_rajada, fatos, todas_regras, agora=as_of
    ) or sem_demanda.resposta_de_menu(conversa, recentes, gatilho, todas_regras)
    if opcoes_sensiveis:
        politica = None
    nf_politica_aprovada = ia.nf_antes_da_compra(
        textos_rajada,
        pedido=ctx.get("pedido"),
        pedido_informado=bool(conversa.pedido_marketplace),
    ) and ia._regra_basica_igual(todas_regras, "nota_fiscal", ia._REGRA_NF_APROVADA)
    atalho: Atalho | None = None
    if (
        "nota_fiscal" in ids_categorias
        and ia._somente_politica_nf(textos_rajada, ctx, conversa)
        and nf_politica_aprovada
    ):
        atalho = Atalho("nota_fiscal", "nota_fiscal", TEXTO_NF_APROVADO)
    elif (
        ia._so_cartao(rajada)
        and "saudacao_ou_vaga" in ids_categorias
        and ia._regra_basica_igual(todas_regras, "saudacao_ou_vaga", ia._REGRA_CARTAO_APROVADA)
    ):
        atalho = Atalho("saudacao_ou_vaga", "cartao", TEXTO_CARTAO_APROVADO)
    elif politica is not None and politica.categoria in ids_categorias:
        atalho = Atalho(politica.categoria, "politica", politica.texto)

    nomes = (
        conversa.comprador_nome,
        conversa.comprador_id,
        *await ia._nomes_do_pedido(session, conversa.pedido_marketplace),
    )
    if (
        ia.turno_incompleto(recentes, gatilho, nomes=nomes)
        and ia.MOTIVO_TURNO_INCOMPLETO not in motivos
    ):
        motivos.append(ia.MOTIVO_TURNO_INCOMPLETO)
    if modo is None:
        modo = MODO_AUTO if await ia._canal_no_automatico(session, conversa) else MODO_COPILOTO

    base = BaseDoGerar(
        recentes=recentes,
        rajada=rajada,
        foco=foco,
        textos_cliente=textos_cliente,
        textos_rajada=textos_rajada,
        ctx=ctx,
        valores=valores,
        fatos=fatos,
        fatos_gravados=fatos_gravados,
        categorias=categorias,
        regras=todas_regras,
        sinais=sinais_texto,
        motivos=motivos,
        opcoes_sensiveis=opcoes_sensiveis,
        sinais_do_cliente=sinais,
        nomes=nomes,
        modo=modo,
        forcar=False,
        nf_politica_aprovada=nf_politica_aprovada,
        insistiu=insistiu,
    )
    return base, atalho


# ── A decisão simulada ────────────────────────────────────────────────────


def _lista(valor: Any) -> list[str]:
    return [str(x) for x in valor] if isinstance(valor, list | tuple | set | frozenset) else []


def _do_canal(canal: Any, nome: str) -> Any:
    if canal is None:
        return None
    return canal.get(nome) if isinstance(canal, Mapping) else getattr(canal, nome, None)


def decisao_simulada(
    resultado: agente.ResultadoAgente,
    *,
    conversa: Any,
    canal: Any,
    ids_so_humano: Iterable[str] = (),
    fatos_gravados: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """O que o sistema faria com o resultado do agente — sem efeito. PURA.

    As travas de `ia._talvez_enviar` que não dependem do momento, na MESMA
    ordem (rascunho pendente, validador, pessoa, confiança, texto,
    reclamação aberta, plataforma, canal sem envio, canal no automático) e,
    no assunto, a régua do agente do próprio `ia` (`ia.trava_do_agente`, spec
    6.6): ação de pessoa, devolução ou chamado em andamento, os avisos do
    automático, orientação (e as pistas dela) só com `canal.auto_orientacao`,
    os outros por `auto_categorias` e `auto_agente`. As travas
    do agora (conversa já respondida, atribuída, pausa humana, Caixa Humano
    relida) não valem para um gatilho que já passou. `fatos_gravados` = os do
    `_gerar` (`BaseDoGerar.fatos_gravados`: chamados e devoluções).

    - `pessoa`: o rascunho iria para pessoa (Caixa Humano): sem rascunho,
      bloqueado, sem texto, `precisa_humano` ou ação de pessoa;
    - `sem_resposta`: o agente decidiu que não há o que responder (texto
      vazio, sem ação) — com nada que segure, o rascunho DESCARTADO (não vai
      para pessoa: `agente.PARADA_SEM_RESPOSTA`, revisão de 20/10/2026); com
      pergunta pendente ou outro motivo, bloqueado "texto vazio" (pessoa);
      o relatório conta à parte (item S da spec 14);
    - `apto_auto`: passou em TODAS as travas de conteúdo (só falta o canal
      liberar o assunto) — a base por assunto da fase 4 do rollout;
    - `enviaria`: sairia sozinho com a configuração de hoje do canal;
    - `motivo`: o 1º que segura (`ENVIARIA` quando sairia); os do assunto
      são os códigos do `ia.trava_do_agente`.
    """
    r = resultado
    status = r.status if r.gravar else None
    texto = (r.texto or "").strip()
    pessoa = r.parada != agente.PARADA_SEM_RESPOSTA and (
        not r.gravar
        or r.status == constantes.RASCUNHO_BLOQUEADO
        or not texto
        or bool(r.precisa_humano)
        or bool(r.acoes_pessoa)
    )
    erros = list(r.validador_erros or []) if r.gravar else []
    sem_resposta = bool(
        r.gravar
        and r.proposta is not None
        and r.proposta.acao == saida.RESPONDER
        and not texto
        and not r.acoes_pessoa
    )
    categoria = r.categoria
    # O rascunho como o envio o leria (`fatos.agente.acoes_pessoa`, chamados,
    # devoluções) — sem gravar nada.
    # O modo em que o replay rodou não conta: a decisão é simulada (o envio
    # real segura o rascunho que nasceu no copiloto — `ia.trava_do_agente`).
    rascunho = SimpleNamespace(
        categoria=categoria,
        fatos={
            **(fatos_gravados or {}),
            "agente": {k: v for k, v in (r.fatos_agente or {}).items() if k != "modo"},
        },
    )
    etiquetas = acao_pessoa.etiquetas_da_conversa(conversa)

    def _trava(do_canal: Any) -> str | None:
        motivo_ = ia.trava_do_agente(rascunho, do_canal, etiquetas=etiquetas, ligado=True)
        if (
            motivo_ is None
            and categoria not in constantes.ASSUNTOS_ORIENTACAO
            and categoria in set(ids_so_humano)
        ):
            motivo_ = ASSUNTO_SO_HUMANO
        return motivo_

    motivo: str | None = None
    if not r.gravar:
        motivo = SEM_RASCUNHO
    elif r.status != RASCUNHO_PENDENTE:
        motivo = RASCUNHO_NAO_PENDENTE
    elif not r.validador_ok:
        motivo = VALIDADOR_REPROVOU
    elif r.precisa_humano:
        motivo = PRECISA_HUMANO
    # Rodada "fechar o eval" (23/10/2026): o resultado é SEMPRE do agente — o
    # `ia.rascunho_do_agente(r)` lia o `prompt_versao` que o `ResultadoAgente` não tem e
    # cortava no 0,85 do legado (377ef84c9635, 72561421e33b e eb6a5eebe6cd, com 0,80).
    elif (r.confianca or 0.0) < ia.confianca_minima(True):
        motivo = CONFIANCA_BAIXA
    elif not texto:
        motivo = SEM_TEXTO
    elif constantes.reclamacao_aberta(conversa.dados):
        motivo = RECLAMACAO_ABERTA
    elif conversa.plataforma in constantes.PLATAFORMAS_SEM_AUTO:
        motivo = PLATAFORMA_SEM_AUTO
    elif constantes.motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados):
        motivo = CANAL_SEM_ENVIO
    else:
        # A régua do assunto com TUDO liberado: o que ainda segura é conteúdo.
        motivo = _trava(
            SimpleNamespace(
                auto_orientacao=list(constantes.ASSUNTOS_ORIENTACAO),
                auto_categorias=[categoria],
                auto_agente=[categoria],
            )
        )
    apto_auto = motivo is None
    if motivo is None:
        if canal is None or _do_canal(canal, "modo") != MODO_AUTO:
            motivo = CANAL_FORA_DO_AUTO
        else:
            motivo = _trava(
                SimpleNamespace(
                    auto_orientacao=_lista(_do_canal(canal, "auto_orientacao")),
                    auto_categorias=_lista(_do_canal(canal, "auto_categorias")),
                    auto_agente=_lista(_do_canal(canal, "auto_agente")),
                )
            )
    return {
        "rascunho": bool(r.gravar),
        "status": status,
        "pessoa": pessoa,
        "sem_resposta": sem_resposta,
        "precisa_humano": bool(r.precisa_humano),
        "acoes_pessoa": list(r.acoes_pessoa),
        "validador_ok": bool(r.validador_ok) if r.gravar else None,
        "erros": erros,
        "categoria": categoria,
        "confianca": r.confianca,
        "apto_auto": apto_auto,
        "enviaria": motivo is None,
        "motivo": motivo or ENVIARIA,
        "modo_canal": _do_canal(canal, "modo"),
        "avisos_auto": _lista((r.fatos_agente or {}).get("avisos_auto")),
    }


# ── O resultado de um gatilho ─────────────────────────────────────────────


@dataclass
class ReplayGatilho:
    """O que o agente teria feito num gatilho: tudo MASCARADO, pronto para gravar ou exportar."""

    conversa_id: UUID
    gatilho_id: UUID | None
    gatilho_em: datetime | None
    plataforma: str
    integration_id: UUID | None
    canal_id: UUID | None
    pulado: str | None = None
    atalho: Atalho | None = None
    parada: str | None = None
    proposta: dict = field(default_factory=dict)
    decisao: dict = field(default_factory=dict)
    rastro: dict = field(default_factory=dict)
    uso: dict = field(default_factory=dict)
    resposta_real: str | None = None
    texto_final: str | None = None
    acoes_pessoa: list[str] = field(default_factory=list)
    modelo: str | None = None
    effort: str | None = None
    turnos: int | None = None
    ms: int | None = None
    custo_usd: float = 0.0
    # O erro do provedor (sem rascunho): o motivo e o tipo, sem chave nem corpo.
    erro_provedor: dict | None = None
    # Com a parada "teto": qual (`custo.TETO_DIA` ou `custo.TETO_CONVERSA`).
    teto_qual: str | None = None
    linha_id: int | None = None
    # O resultado CRU do agente (não mascarado): só em memória, nunca gravado.
    resultado: agente.ResultadoAgente | None = field(default=None, repr=False)

    @property
    def dia(self) -> date | None:
        return dia_sp(self.gatilho_em) if self.gatilho_em else None

    def para_linha(self) -> dict[str, Any]:
        """A linha de `atendimento_agente_replay` (sem o id)."""
        if self.gatilho_id is None or self.gatilho_em is None:
            raise ValueError("replay sem gatilho não vira linha")
        return {
            "dia": self.dia,
            "conversa_id": self.conversa_id,
            "gatilho_id": self.gatilho_id,
            "gatilho_em": self.gatilho_em,
            "plataforma": self.plataforma,
            "integration_id": self.integration_id,
            "proposta": self.proposta,
            "decisao": {**self.decisao, "texto_final": self.texto_final},
            "rastro": self.rastro,
            "uso": self.uso,
            "resposta_real": self.resposta_real,
            "modelo": (self.modelo or "")[:128] or None,
            "effort": (self.effort or "")[:16] or None,
            "parada": (self.parada or "")[:32] or None,
            "turnos": self.turnos,
            "ms": self.ms,
            "custo_usd": Decimal(str(round(self.custo_usd, 6))),
        }

    def para_jsonl(self) -> dict[str, Any]:
        """O registro do eval (`resultados_<braço>.jsonl`): mascarado, sem o resultado cru."""
        return {
            "conversa_id": str(self.conversa_id),
            "gatilho_id": str(self.gatilho_id) if self.gatilho_id else None,
            "gatilho_em": self.gatilho_em.isoformat() if self.gatilho_em else None,
            "plataforma": self.plataforma,
            "integration_id": str(self.integration_id) if self.integration_id else None,
            "pulado": self.pulado,
            "atalho": self.atalho.categoria if self.atalho else None,
            "parada": self.parada,
            "proposta": self.proposta,
            "texto_final": self.texto_final,
            "decisao": self.decisao,
            "acoes_pessoa": self.acoes_pessoa,
            "rastro": self.rastro,
            "uso": self.uso,
            "resposta_real": self.resposta_real,
            "modelo": self.modelo,
            "effort": self.effort,
            "turnos": self.turnos,
            "ms": self.ms,
            "custo_usd": round(self.custo_usd, 6),
            "erro_provedor": self.erro_provedor,
            "teto_qual": self.teto_qual,
        }


def _proposta_mascarada(
    p: saida.Proposta | None,
    *,
    nomes: Sequence[str | None],
    termos: Sequence[str],
    valores: Mapping[str, str | None],
) -> dict:
    if p is None:
        return {}
    d = saida.para_fatos(p)
    d["texto"] = mascarar_texto(relacunar(d.get("texto"), valores), nomes, termos)
    d["motivo_pessoa"] = mascarar_texto(d.get("motivo_pessoa"), nomes, termos)
    return d


def _transcricao_mascarada(
    linhas: Sequence[str], nomes: Sequence[str | None], termos: Sequence[str]
) -> list[str]:
    return [mascarar_texto(linha, nomes, termos, digitos=False) for linha in linhas]


def _rastro(
    *,
    fatos_agente: Mapping[str, Any],
    captura: Captura | None,
    transcricao: list[str],
    nomes: Sequence[str | None],
    termos: Sequence[str],
) -> dict:
    passos = []
    for passo in fatos_agente.get("rastro") or []:
        p = dict(passo)
        p["args"] = {
            k: mascarar_texto(v, nomes, termos, digitos=False) if isinstance(v, str) else v
            for k, v in (p.get("args") or {}).items()
        }
        passos.append(p)
    rastro: dict[str, Any] = {
        "transcricao": _transcricao_mascarada(transcricao, nomes, termos),
        "passos": passos,
        "checagens": _lista(fatos_agente.get("checagens")),
        "evidencias": _lista(fatos_agente.get("evidencias")),
        "sistema": fatos_agente.get("sistema"),
    }
    if captura is not None and captura.corpo is not None:
        pac = captura.pacote()
        rastro["pacote"] = (
            mascarar_texto(pac, nomes, termos, digitos=False)[:MAX_CHARS_PACOTE] if pac else None
        )
        rastro["ferramentas"] = [
            {
                "ferramenta": f.get("ferramenta"),
                "args": {
                    k: mascarar_texto(v, nomes, termos, digitos=False) if isinstance(v, str) else v
                    for k, v in (f.get("args") or {}).items()
                },
                "resultado": mascarar_texto(f.get("resultado"), nomes, termos, digitos=False)[
                    :MAX_CHARS_FERRAMENTA
                ],
                "erro": bool(f.get("erro")),
            }
            for f in captura.ferramentas()
        ]
    return rastro


def _extra_do_replay(as_of: datetime, extra: Mapping[str, Any] | None) -> dict[str, Any]:
    """O `ctx_extra` do replay: o `as_of`, `modo_replay` e SEM ida à loja — sempre."""
    ext = dict(extra or {})
    ext.update({"as_of": as_of, "modo_replay": True, "sem_ida_a_loja": True})
    return ext


async def como_o_gerar(
    session: AsyncSession,
    conversa: Any,
    gatilho: Any,
    base: BaseDoGerar,
    extra: Mapping[str, Any],
) -> agente.ResultadoAgente | None:
    """O `ia._gerar_pelo_agente` SEM gravar: o resultado que viraria rascunho (None = nenhum).

    Os mesmos passos (ia:~4555): o turno só com o cartão não ganha rascunho;
    a instrução para a IA e o só anexo depois do pedido de evidência vão para
    pessoa SEM o agente (sem custo); senão `agente.responder` e, por cima, a
    etiqueta Devolução (`ia._andamento_pela_etiqueta`) e a pergunta de
    produto em `confirmar_variacao` (`ia.assunto_do_agente`). Sem a rodada do
    cron e sem o teto de chamadas do legado (o replay tem o teto em dólar).
    """
    antes = acao_pessoa.gatilhos_antes_do_modelo(base.recentes, gatilho, sinais=base.sinais)
    if antes.so_cartao:
        return None
    if antes.gatilhos:
        ctx = ContextoAgente.da_conversa(
            conversa, gatilho, modo=base.modo, nomes=base.nomes, extra=extra
        )
        ctx.acoes_codigo = ordenar_acoes([g.codigo for g in antes.gatilhos])
        motivo = "; ".join(dict.fromkeys([*acao_pessoa.motivos(antes.gatilhos), *base.motivos]))
        return agente.ResultadoAgente.sem_texto(
            ctx,
            parada=agente.PARADA_SEM_AGENTE,
            motivo=motivo,
            manual_hash=ia.manual_hash(ia.organizar_manual(list(base.regras), None)),
        )
    resultado = await agente.responder(session, conversa, gatilho, base, ctx_extra=extra)
    if resultado.parada == agente.PARADA_FIM:
        ia._andamento_pela_etiqueta(resultado, conversa, base)
    resultado.categoria = ia.assunto_do_agente(
        resultado.categoria, (resultado.fatos_agente or {}).get("acao"), base.ids_categorias
    )
    ia.anotar_pistas_alem(resultado)
    return resultado


async def rodar_gatilho(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    as_of: datetime,
    *,
    gravar: bool,
    gatilho_id: UUID | None = None,
    extra: ExtraAgente | Mapping[str, Any] | None = None,
    cliente: Any | None = None,
    captura: Captura | None = None,
    modo: str | None = None,
) -> ReplayGatilho:
    """O agente num gatilho que já passou, cortado no `as_of`. Não envia nem cria rascunho.

    `session` = de `sessao_replay()` (só leitura). `gatilho_id` = o gatilho
    da amostra (senão, a última mensagem do cliente até o `as_of`). `extra`
    = o braço do eval (`modelo`, `effort`, `max_turnos`, `ferramentas_fora`,
    `sem_fatos`); `as_of`, `modo_replay` e `sem_ida_a_loja` são sempre os do
    replay. `cliente` = o cliente da API já montado (teste, braço Sonnet);
    sem ele, o replay monta um com a chave do agente e a `Captura` (o
    rastro completo). `modo` = troca o modo do canal na conferência.
    `gravar=True` → uma linha em `atendimento_agente_replay` (sessão própria;
    o mesmo gatilho no mesmo dia é substituído).

    Exceção de código nosso (pacote, ferramentas, checagens) propaga: quem
    roda em lote registra o erro do item e segue.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of sem fuso")
    r = ReplayGatilho(
        conversa_id=conversa.id,
        gatilho_id=None,
        gatilho_em=None,
        plataforma=conversa.plataforma,
        integration_id=conversa.integration_id,
        canal_id=conversa.canal_id,
    )
    gatilho = await gatilho_ate(session, conversa, as_of, gatilho_id=gatilho_id)
    if gatilho is None:
        r.pulado = PULADO_SEM_GATILHO
        return r
    r.gatilho_id, r.gatilho_em = gatilho.id, ia._momento(gatilho)
    if constantes.motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados):
        r.pulado = PULADO_CANAL_SEM_ENVIO
        return r

    nomes_reais = (
        conversa.comprador_nome,
        conversa.comprador_id,
        *await ia._nomes_do_pedido(session, conversa.pedido_marketplace),
    )
    termos = await termos_do_pedido(session, conversa)
    ctx = await contexto_svc.contexto_da_conversa(session, conversa)
    vista: Any = conversa
    if _comprado_depois(ctx, as_of):
        vista = sem_o_pedido(conversa)
        ctx = await contexto_svc.contexto_da_conversa(session, vista)
    recentes = await mensagens_ate(session, vista, as_of)
    if gatilho.id not in {m.id for m in recentes}:
        recentes = ia.HistoricoMensagens(
            [gatilho, *recentes],
            turno_incompleto=getattr(recentes, "turno_incompleto", False),
            pedido_marketplace=vista.pedido_marketplace,
        )
    base, atalho = await montar_base(
        session, vista, gatilho, recentes, as_of=as_of, ctx=ctx, modo=modo
    )
    nomes = tuple(dict.fromkeys(n for n in (*base.nomes, *nomes_reais) if n))
    depois = await mensagens_depois(session, conversa, gatilho)
    equipe = resposta_da_equipe(depois, gatilho)
    r.resposta_real = (
        "\n".join(generalizar(m.texto, nomes, termos) for m in equipe if (m.texto or "").strip())
        or None
    )
    transcricao = pacote.transcricao(
        list(recentes),
        gatilho,
        tuple(nomes),
        pediu_evidencia=acao_pessoa.pediu_evidencia(list(recentes), gatilho),
    )
    if atalho is not None:
        r.pulado, r.atalho = PULADO_ATALHO, atalho
        r.texto_final = mascarar_texto(atalho.texto, nomes, termos)
        r.decisao = {"rascunho": True, "pessoa": False, "atalho": atalho.categoria}
        r.rastro = {"transcricao": _transcricao_mascarada(transcricao, nomes, termos)}
        return r

    ext = _extra_do_replay(as_of, extra)
    proprio = None
    if cliente is None:
        chave = agente_config.chave_do_agente(get_settings())
        if chave:
            captura = captura or Captura()
            proprio = cliente_do_replay(chave, captura)
            ext["cliente"] = proprio
    else:
        ext["cliente"] = cliente
    try:
        resultado = await como_o_gerar(session, vista, gatilho, base, ext)
    finally:
        if proprio is not None:
            await proprio.close()
    if resultado is None:
        r.pulado = PULADO_SO_CARTAO
        r.decisao = {"rascunho": False, "pessoa": False, "sem_resposta": True}
        r.rastro = {"transcricao": _transcricao_mascarada(transcricao, nomes, termos)}
        return r

    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id)
        if conversa.canal_id is not None
        else None
    )
    if modo is not None and canal is not None:
        canal = {
            "modo": modo,
            "auto_categorias": list(canal.auto_categorias or []),
            "auto_orientacao": list(getattr(canal, "auto_orientacao", None) or []),
            "auto_agente": list(getattr(canal, "auto_agente", None) or []),
        }
    fa = resultado.fatos_agente or {}
    r.resultado = resultado
    r.parada = resultado.parada
    r.teto_qual = resultado.teto_qual
    r.modelo = resultado.modelo or fa.get("modelo")
    r.effort = fa.get("effort")
    r.turnos = fa.get("turnos")
    r.ms = fa.get("ms")
    r.custo_usd = float(resultado.custo_usd or 0.0)
    r.acoes_pessoa = list(resultado.acoes_pessoa)
    r.uso = dict(fa.get("uso") or {})
    if (erro := resultado.erro_provedor) is not None:
        r.erro_provedor = {
            "motivo": erro.motivo,
            "sem_credito": bool(erro.sem_credito),
            "limite": bool(erro.limite),
            "definitivo": bool(erro.definitivo),
        }
    r.proposta = _proposta_mascarada(
        resultado.proposta, nomes=nomes, termos=termos, valores=base.valores
    )
    r.texto_final = (
        mascarar_texto(relacunar(resultado.texto, base.valores), nomes, termos)
        if resultado.texto
        else None
    )
    decisao = decisao_simulada(
        resultado,
        conversa=vista,
        canal=canal,
        ids_so_humano=manual_svc.ids_so_humano(base.categorias),
        fatos_gravados=base.fatos_gravados,
    )
    decisao["erros"] = [mascarar_texto(e, nomes, termos) for e in decisao["erros"]]
    decisao["motivos"] = [
        mascarar_texto(m, nomes, termos) for m in (resultado.motivo or "").split("; ") if m
    ]
    decisao["acao"] = resultado.proposta.acao if resultado.proposta else None
    decisao["acoes_codigo"] = _lista(fa.get("acoes_codigo"))
    decisao["acoes_modelo"] = _lista(fa.get("acoes_modelo"))
    decisao["modo"] = base.modo
    decisao["avisos_auto"] = [mascarar_texto(a, nomes, termos) for a in decisao["avisos_auto"]]
    r.decisao = decisao
    r.rastro = _rastro(
        fatos_agente=fa, captura=captura, transcricao=transcricao, nomes=nomes, termos=termos
    )
    if gravar and r.parada not in PARADAS_SEM_LINHA:
        r.linha_id = await gravar_replay(r)
    return r


async def rascunho_do_legado(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    as_of: datetime,
    *,
    gatilho_id: UUID | None = None,
) -> ReplayGatilho:
    """O braço L0 do eval (spec 9.1, "a base real"): a sugestão que o LEGADO
    (prompt v10, Groq) de fato escreveu para este gatilho — lida do banco,
    sem chamar modelo nenhum e sem mexer no legado (20/10/2026).

    O mesmo registro do `rodar_gatilho` (transcrição cortada no `as_of`, a
    resposta da equipe, tudo mascarado), com a sugestão mais antiga do
    legado para o gatilho (`prompt_versao` que não é a do agente): `pessoa`
    quando ela foi para pessoa (precisa de pessoa, bloqueada ou sem texto),
    `enviaria` quando ela de fato saiu sozinha. Sem sugestão do legado (o
    canal fora dos modos da IA, o cron não pegou): `pulado`
    `PULADO_SEM_LEGADO`. O L1 (o legado com o Haiku) precisaria refazer o
    `_gerar` do legado e não existe (estado_build.md).
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of sem fuso")
    r = ReplayGatilho(
        conversa_id=conversa.id,
        gatilho_id=None,
        gatilho_em=None,
        plataforma=conversa.plataforma,
        integration_id=conversa.integration_id,
        canal_id=conversa.canal_id,
    )
    gatilho = await gatilho_ate(session, conversa, as_of, gatilho_id=gatilho_id)
    if gatilho is None:
        r.pulado = PULADO_SEM_GATILHO
        return r
    r.gatilho_id, r.gatilho_em = gatilho.id, ia._momento(gatilho)
    nomes = tuple(
        dict.fromkeys(
            n
            for n in (
                conversa.comprador_nome,
                conversa.comprador_id,
                *await ia._nomes_do_pedido(session, conversa.pedido_marketplace),
            )
            if n
        )
    )
    termos = await termos_do_pedido(session, conversa)
    recentes = await mensagens_ate(session, conversa, as_of)
    if gatilho.id not in {m.id for m in recentes}:
        recentes = ia.HistoricoMensagens(
            [gatilho, *recentes],
            turno_incompleto=getattr(recentes, "turno_incompleto", False),
            pedido_marketplace=conversa.pedido_marketplace,
        )
    equipe = resposta_da_equipe(await mensagens_depois(session, conversa, gatilho), gatilho)
    r.resposta_real = (
        "\n".join(generalizar(m.texto, nomes, termos) for m in equipe if (m.texto or "").strip())
        or None
    )
    transcricao = pacote.transcricao(
        list(recentes),
        gatilho,
        nomes,
        pediu_evidencia=acao_pessoa.pediu_evidencia(list(recentes), gatilho),
    )
    r.rastro = {"transcricao": _transcricao_mascarada(transcricao, nomes, termos)}
    legado = await session.scalar(
        select(AtendimentoRascunho)
        .where(
            AtendimentoRascunho.conversa_id == conversa.id,
            AtendimentoRascunho.mensagem_gatilho_id == gatilho.id,
            or_(
                AtendimentoRascunho.prompt_versao.is_(None),
                AtendimentoRascunho.prompt_versao != PROMPT_VERSAO_AGENTE,
            ),
        )
        .order_by(AtendimentoRascunho.created_at.asc())
        .limit(1)
    )
    if legado is None:
        r.pulado = PULADO_SEM_LEGADO
        r.decisao = {"rascunho": False, "pessoa": True, "sem_resposta": False}
        return r
    ctx = await contexto_svc.contexto_da_conversa(session, conversa)
    valores = ia.valores_das_lacunas(conversa, ctx)
    texto = (legado.texto or "").strip()
    pessoa = bool(legado.precisa_humano) or legado.status == RASCUNHO_BLOQUEADO or not texto
    auto = (legado.fatos or {}).get(ia.FATO_AUTO_ENVIO) or {}
    r.parada = PARADA_LEGADO
    r.modelo = legado.modelo
    r.texto_final = mascarar_texto(relacunar(texto, valores), nomes, termos) if texto else None
    motivo = mascarar_texto(legado.motivo or "", nomes, termos)
    r.proposta = {
        "acao": saida.PESSOA if pessoa else saida.RESPONDER,
        "texto": r.texto_final or "",
        "assunto": legado.categoria,
        "confianca": legado.confianca,
        "acao_pessoa": None,
        "motivo_pessoa": motivo if pessoa else "",
        "evidencias": [],
    }
    r.decisao = {
        "rascunho": True,
        "status": legado.status,
        "pessoa": pessoa,
        "sem_resposta": False,
        "precisa_humano": bool(legado.precisa_humano),
        "acoes_pessoa": [],
        "validador_ok": bool(legado.validador_ok),
        "erros": [mascarar_texto(e, nomes, termos) for e in legado.validador_erros or []],
        "categoria": legado.categoria,
        "confianca": legado.confianca,
        "enviaria": auto.get("motivo") == "enviado",
        "motivo": auto.get("motivo"),
        "motivos": [m for m in motivo.split("; ") if m],
        # A versão do prompt do legado que escreveu (o relatório mostra a
        # cobertura por versão: o L0 só é o "legado v10" onde ela é v10).
        "prompt_versao": legado.prompt_versao,
    }
    return r


# ── Gravação (sessão própria) ─────────────────────────────────────────────


async def gravar_replay(r: ReplayGatilho) -> int:
    """Grava (ou substitui) a linha do gatilho no dia em `atendimento_agente_replay`."""
    linha = r.para_linha()
    t = AtendimentoAgenteReplay.__table__
    mudar = {k: v for k, v in linha.items() if k not in ("dia", "gatilho_id")}
    consulta = (
        pg_insert(t)
        .values(**linha)
        .on_conflict_do_update(index_elements=[t.c.dia, t.c.gatilho_id], set_=mudar)
        .returning(t.c.id)
    )
    async with _db.session_scope() as s:
        return int((await s.execute(consulta)).scalar_one())


async def expurgar(session: AsyncSession, *, agora: datetime | None = None) -> int:
    """Apaga as linhas do replay com mais de `RETENCAO_DIAS` (só flush; quem chama faz o commit)."""
    limite = dia_sp(agora or datetime.now(UTC)) - timedelta(days=RETENCAO_DIAS)
    t = AtendimentoAgenteReplay.__table__
    resultado = await session.execute(t.delete().where(t.c.dia < limite))
    return int(resultado.rowcount or 0)


async def ja_no_replay(session: AsyncSession, gatilhos: Iterable[UUID]) -> set[UUID]:
    """Os gatilhos que já têm linha no replay (a rodada que cai e volta não refaz)."""
    ids = list(dict.fromkeys(gatilhos))
    if not ids:
        return set()
    t = AtendimentoAgenteReplay
    return set(
        (await session.execute(select(t.gatilho_id).where(t.gatilho_id.in_(ids)))).scalars().all()
    )


# ── Os turnos (gatilho + resposta da equipe) de um período ────────────────


@dataclass(frozen=True)
class Turno:
    """Um turno do cliente e quem fechou a vez (a equipe, a IA, ninguém)."""

    gatilho: Any
    rajada: tuple[Any, ...]
    resposta: tuple[Any, ...]
    fechado_por: str | None  # "equipe" | "ia" | None (sem resposta)
    # As mensagens até o gatilho (inclusive), as últimas `ia.MAX_TROCAS_LIDAS`
    # do que foi lido — o que a amostra usa para rotular sem ir ao banco.
    historico: tuple[Any, ...] = ()


FECHADO_EQUIPE = "equipe"
FECHADO_IA = "ia"


def turnos(mensagens: Sequence[Any]) -> list[Turno]:
    """Os turnos do cliente numa conversa, em ordem. PURA.

    Turno = as falas do cliente depois da última resposta real da loja
    (`ia.resposta_da_loja`: a automática, a nota e a que falhou não fecham);
    o gatilho é a ÚLTIMA delas (a que o cron responderia). Fechado pela
    EQUIPE quando a 1ª resposta real é de pessoa (Duoke ou DaVinci) — a
    `resposta` são as falas dela (`resposta_da_equipe`); pela IA quando a IA
    (ou outra resposta que não é de pessoa) fechou antes.
    """
    ordem = sorted(
        mensagens,
        key=lambda m: (ia._momento(m), getattr(m, "created_at", None) or ia._momento(m)),
    )
    saida_: list[Turno] = []
    cliente: list[Any] = []
    for i, m in enumerate(ordem):
        if getattr(m, "autor", None) == AUTOR_CLIENTE:
            cliente.append(m)
            continue
        if not cliente or not ia.resposta_da_loja(m):
            continue
        gatilho = cliente[-1]
        historico = tuple(ordem[: ordem.index(gatilho) + 1][-ia.MAX_TROCAS_LIDAS :])
        if da_equipe(m):
            resposta = resposta_da_equipe(ordem[i:], gatilho)
            fechado = FECHADO_EQUIPE
        else:
            resposta, fechado = [], FECHADO_IA
        saida_.append(Turno(gatilho, tuple(cliente), tuple(resposta), fechado, historico))
        cliente = []
    if cliente:
        fim = ordem.index(cliente[-1]) + 1
        historico = tuple(ordem[:fim][-ia.MAX_TROCAS_LIDAS :])
        saida_.append(Turno(cliente[-1], tuple(cliente), (), None, historico))
    return saida_


async def turnos_em_lotes(
    session: AsyncSession,
    *,
    inicio: datetime,
    fim: datetime,
    plataformas: Iterable[str] | None = None,
    canal_ids: Iterable[UUID] | None = None,
    so_da_equipe: bool = True,
    lote: int = 200,
) -> AsyncIterator[list[tuple[AtendimentoConversa, Turno]]]:
    """Os turnos cujo gatilho caiu em [inicio, fim), um LOTE de conversas por vez.

    Lê as conversas com fala do cliente no período (das plataformas e
    canais pedidos) e, de cada uma, as mensagens de `MARGEM_ANTES` antes até
    `MARGEM_DEPOIS` depois (a rajada e a resposta). Sem o canal sem envio;
    com `so_da_equipe`, só o turno que a EQUIPE respondeu (a referência da
    nota). Os turnos de uma conversa vêm juntos, no mesmo lote. Depois de cada
    lote a sessão solta os objetos (`expunge_all`): a amostra da Shopee
    inteira não segura semanas de mensagens na memória — quem usa os turnos
    lê só o que já veio carregado.
    """
    c, m = AtendimentoConversa, AtendimentoMensagem
    filtros = [
        c.id.in_(
            select(m.conversa_id).where(
                m.autor == AUTOR_CLIENTE,
                ia._momento_col() >= inicio,
                ia._momento_col() < fim,
            )
        )
    ]
    if plataformas is not None:
        filtros.append(c.plataforma.in_(sorted(set(plataformas))))
    if canal_ids is not None:
        filtros.append(c.canal_id.in_(list(canal_ids)))
    ids = list((await session.execute(select(c.id).where(*filtros).order_by(c.id))).scalars())
    for i in range(0, len(ids), max(1, lote)):
        grupo = {
            x.id: x
            for x in (await session.execute(select(c).where(c.id.in_(ids[i : i + lote]))))
            .scalars()
            .all()
        }
        linhas = (
            (
                await session.execute(
                    select(m)
                    .where(
                        m.conversa_id.in_(list(grupo)),
                        ia._momento_col() >= inicio - MARGEM_ANTES,
                        ia._momento_col() < fim + MARGEM_DEPOIS,
                        m.status != MSG_FALHOU,
                        m.origem != ORIGEM_NOTA,
                    )
                    .order_by(m.conversa_id, ia._momento_col(), m.created_at)
                )
            )
            .scalars()
            .all()
        )
        por_conversa: dict[UUID, list[Any]] = {}
        for x in linhas:
            por_conversa.setdefault(x.conversa_id, []).append(x)
        achados: list[tuple[AtendimentoConversa, Turno]] = []
        for cid, mensagens in por_conversa.items():
            conversa = grupo[cid]
            if constantes.motivo_canal_sem_envio(
                conversa.canal, conversa.plataforma, conversa.dados
            ):
                continue
            for t in turnos(mensagens):
                if not inicio <= ia._momento(t.gatilho) < fim:
                    continue
                if so_da_equipe and not (t.fechado_por == FECHADO_EQUIPE and t.resposta):
                    continue
                achados.append((conversa, t))
        yield achados
        session.expunge_all()


async def turnos_no_periodo(
    session: AsyncSession,
    *,
    inicio: datetime,
    fim: datetime,
    plataformas: Iterable[str] | None = None,
    canal_ids: Iterable[UUID] | None = None,
    so_da_equipe: bool = True,
    lote: int = 200,
) -> list[tuple[AtendimentoConversa, Turno]]:
    """Todos os turnos do período (`turnos_em_lotes`), do gatilho mais antigo ao mais novo."""
    saida_: list[tuple[AtendimentoConversa, Turno]] = []
    async for achados in turnos_em_lotes(
        session,
        inicio=inicio,
        fim=fim,
        plataformas=plataformas,
        canal_ids=canal_ids,
        so_da_equipe=so_da_equipe,
        lote=lote,
    ):
        saida_.extend(achados)
    saida_.sort(key=lambda par: ia._momento(par[1].gatilho))
    return saida_


# ── O replay noturno (o cron `atendimento_replay_agente`) ─────────────────


async def _um(conversa_id: UUID, gatilho_id: UUID, as_of: datetime) -> ReplayGatilho | None:
    async with sessao_replay() as s:
        conversa = await s.get(AtendimentoConversa, conversa_id)
        if conversa is None:
            return None
        return await rodar_gatilho(s, conversa, as_of, gravar=True, gatilho_id=gatilho_id)


# A parte do teto do dia DOS TESTES que o replay, o eval e a calibração podem
# gastar. Desde 28/10/2026 (o dono) o dia dos testes é outro
# (`custo.CHAVE_DIA_REPLAY`, `atendimento_agente_teto_usd_dia_replay`): o agente
# AO VIVO tem o dia dele inteiro — em 27/10 o eval somou US$ 10 no dia único
# (que só reservava a metade, ajuste r3 de 20/10) e o ao vivo parou das 13:06 à
# meia-noite. Aqui a fração só deixa os testes pararem antes do próprio teto.
REPLAY_FRACAO_DO_DIA = 1.0


async def parte_do_dia_gasta(agora: datetime | None = None) -> bool:
    """O gasto dos testes no dia já chegou à parte deles (`REPLAY_FRACAO_DO_DIA` do teto deles)?"""
    teto = custo.teto_do_dia(replay=True)
    if teto <= 0:
        return False
    return await custo.gasto_do_dia(agora, replay=True) >= REPLAY_FRACAO_DO_DIA * teto


async def replay_noturno(
    *,
    dia: date | None = None,
    limite: int = MAX_REPLAY_NOITE,
    concorrencia: int = CONCORRENCIA,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """O replay da noite (spec 8, fase 2): os gatilhos de ONTEM com resposta da equipe.

    Só com `atendimento_agente_replay` ligado, a chave Claude e fora das
    janelas de postagem; as plataformas de `atendimento_agente_replay_plataformas`.
    Pula o gatilho que já tem linha; para de começar novos ao estourar o
    teto do dia (`custo`, replay incluído) ou ao entrar numa janela. No fim,
    apaga as linhas com mais de 30 dias (`expurgar`). A saída é só contagem
    (nada de texto).
    """
    s = get_settings()
    if not s.atendimento_agente_replay:
        return {"desligado": True}
    if agente_config.chave_do_agente(s) is None:
        return {"sem_chave": True}
    if janela := janela_de_postagem(agora):
        return {"janela": janela}
    dia = dia or (agora_sp(agora) - timedelta(days=1)).date()
    inicio = datetime.combine(dia, time(0), tzinfo=ia.SAO_PAULO)
    fim = inicio + timedelta(days=1)
    async with sessao_replay() as sessao:
        achados = await turnos_no_periodo(
            sessao,
            inicio=inicio,
            fim=fim,
            plataformas=agente_config.plataformas_do_replay(s),
        )
        feitos = await ja_no_replay(sessao, [t.gatilho.id for _c, t in achados])
        fila = [
            (conversa.id, t.gatilho.id, ia._momento(t.gatilho))
            for conversa, t in achados
            if t.gatilho.id not in feitos
        ][: max(0, limite)]
    resumo: dict[str, Any] = {
        "dia": dia.isoformat(),
        "turnos": len(achados),
        "ja_feitos": len(feitos),
        "rodados": 0,
        "pulados": 0,
        "erros": 0,
        "paradas": {},
        "custo_usd": 0.0,
    }
    trava = asyncio.Semaphore(max(1, concorrencia))
    parar = asyncio.Event()

    async def _rodar(cid: UUID, gid: UUID, as_of: datetime) -> None:
        async with trava:
            if parar.is_set():
                return
            if janela_de_postagem():
                parar.set()
                resumo["parou_na_janela"] = True
                return
            if await parte_do_dia_gasta():
                # A reserva do ao vivo (ajuste r3): o replay não come o dia todo.
                parar.set()
                resumo["parou_no_teto"] = True
                return
            try:
                r = await _um(cid, gid, as_of)
            except Exception as e:  # noqa: BLE001 — um gatilho com erro não para a noite
                resumo["erros"] += 1
                logger.warning("atendimento_replay_falhou", erro=type(e).__name__)
                return
            if r is None or r.pulado:
                resumo["pulados"] += 1
                return
            resumo["rodados"] += 1
            resumo["paradas"][r.parada] = resumo["paradas"].get(r.parada, 0) + 1
            resumo["custo_usd"] = round(resumo["custo_usd"] + r.custo_usd, 6)
            if r.parada == agente.PARADA_TETO and r.teto_qual != custo.TETO_CONVERSA:
                # O teto do DIA: nada mais roda hoje. O da conversa só pula
                # o gatilho (revisão de 20/10/2026).
                parar.set()
                resumo["parou_no_teto"] = True
            elif r.erro_provedor and (r.erro_provedor["sem_credito"] or r.erro_provedor["limite"]):
                # Sem crédito ou no limite: as próximas também falhariam.
                parar.set()
                resumo["parou_no_provedor"] = r.erro_provedor["motivo"]

    await asyncio.gather(*(_rodar(*item) for item in fila))
    async with _db.session_scope() as s:
        resumo["expurgados"] = await expurgar(s, agora=agora)
    logger.info("atendimento_replay_noturno", **{k: v for k, v in resumo.items() if k != "paradas"})
    return resumo
