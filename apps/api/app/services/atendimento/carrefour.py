"""Adaptador do Carrefour (Mirakl): as mensagens do vendedor (30/10/2026).

O Eduardo (30/10): "pode integrar o carrefour". Uma caixa por loja (`chat`),
lida e respondida pelo `CarrefourClient` da integração — a mesma chave
(`api_key` + `shop_id`) da leitura de ofertas e pedidos. Pesquisa em
`docs/carrefour-integracao.md` (seção Mensagens).

- UMA CONVERSA POR THREAD da Mirakl (`externo_id` = id da thread). A thread
  nasce do PEDIDO (`MMP_ORDER`, o comprador ou o Carrefour falando do
  pedido), da OFERTA (`MMP_OFFER`, a pergunta antes da compra) ou do próprio
  Carrefour com a loja (`SELLER_OPERATOR`); a entidade vai para `dados` e o
  pedido para `pedido_marketplace`.
- A LISTA é o M11 (`GET /inbox/threads`) com `updated_since` (o cursor, com
  `MARGEM_CURSOR` de folga) e `with_messages=true`: cada thread que mexeu
  já vem com as últimas 100 mensagens — uma leitura por página, nenhuma por
  conversa. A paginação é por `page_token` (com ele a Mirakl ignora os outros
  filtros). Toda a leitura HTTP vem ANTES de qualquer escrita, e o cursor só
  anda quando a lista veio inteira (sem `next_page_token`).
- A VEZ É DA LOJA quando a Mirakl diz: `metadata.shop_reply_needed_since`
  ("desde quando a última mensagem espera a loja; nulo quando a loja
  respondeu") vai em `dados[CHAVE_VEZ_DA_LOJA]` e o `gravar` obedece, como no
  SAC da Magalu — o Carrefour (operador) cobrando a loja entra na fila, e a
  mensagem marcada "não precisa de resposta" no portal sai.
- Quem fala: `CUSTOMER_USER` = cliente, `SHOP_USER` = loja, `OPERATOR_USER`
  (o atendimento do Carrefour) = mediador. A loja respondendo pelo portal do
  Carrefour entra como `loja`/`externo` (o `gravar` decide) e tira da fila.
- A RESPOSTA é o M12 (`POST /inbox/threads/{id}/message`), a ÚNICA escrita
  liberada no cliente: vai para quem falou por último que não é a loja (o
  comprador, ou o Carrefour quando é ele cobrando) e só para quem a thread
  autoriza. O 201 devolve o `message_id`, que vira o id externo da nossa
  linha — a leitura seguinte a reconhece por ele (sem ele, pelo texto).
- A Mirakl não tem "não lida": o número da Caixa é o das mensagens do
  comprador (`pendente`), e nada aqui marca coisa alguma como lida.
- 401/403 = a chave recusada (gerada de novo no portal, ou sem o papel de
  mensagens) → `sem_escopo` com o que fazer; o sync só tenta de hora em hora,
  ou na hora em que a integração for atualizada com a chave nova. 429 segura
  o canal até o `Retry-After`.

Texto de comprador nunca vai para o log — só ids e contagens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, Integration
from app.services.atendimento import gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_MEDIADOR,
    AUTOR_SISTEMA,
    CANAL_CHAT,
    CHAVE_VEZ_DA_LOJA,
    MSG_ENVIADA,
    MSG_REVISAR,
    ORIGENS_DAVINCI,
    ResultadoEnvio,
    ResultadoSync,
    limite_caracteres,
)
from app.services.marketplaces.carrefour import REPLY_BODY_MIN, CarrefourError

logger = structlog.get_logger()

# Um dos valores de `constantes.PLATAFORMAS`.
PLATAFORMA = "carrefour"

STATUS_OK = "ok"
STATUS_SEM_ESCOPO = "sem_escopo"
STATUS_ERRO = "erro"

MOTIVO_SEM_PERMISSAO = (
    "chave recusada: gere a chave de API no portal do Carrefour (usuário com acesso às "
    "mensagens) e atualize em Integrações"
)

PAGINA = 100
# 20 páginas de 100 threads por rodada; passou disso, o cursor não anda e a
# rodada seguinte lê de novo (idempotente) — só a primeira leitura chega perto.
MAX_PAGINAS = 20
JANELA_PRIMEIRA_LEITURA = timedelta(days=30)
# Folga do `updated_since`: relógio da Mirakl × o nosso.
MARGEM_CURSOR = timedelta(minutes=15)
# Sem `Retry-After` no 429: espera isto.
ESPERA_429 = timedelta(minutes=1)

_DE_CLIENTE = "CUSTOMER_USER"
_DE_LOJA = "SHOP_USER"
_DE_OPERADOR = "OPERATOR_USER"
PARA_CLIENTE = "CUSTOMER"
PARA_CARREFOUR = "OPERATOR"
_ENTIDADES_PEDIDO = ("MMP_ORDER", "MPS_ORDER")
_ENTIDADE_OFERTA = "MMP_OFFER"
MAX_ENTIDADES = 10
_EXTENSOES_IMAGEM = (".png", ".jpg", ".jpeg", ".jpe", ".gif", ".webp")


def _agora() -> datetime:
    """Relógio do adaptador (os testes trocam)."""
    return datetime.now(UTC)


# ── Utilitários ───────────────────────────────────────────────────────────


def _id(bruto: Any) -> str:
    return "" if bruto is None or isinstance(bruto, bool) else str(bruto).strip()


def _texto(bruto: Any) -> str | None:
    return bruto if isinstance(bruto, str) and bruto.strip() else None


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


def _lista(bruto: Any) -> list[dict]:
    return [x for x in bruto if isinstance(x, dict)] if isinstance(bruto, list) else []


def _data(bruto: Any) -> datetime | None:
    """Data da Mirakl (UTC com Z) → UTC. Sem fuso = UTC; ilegível = None."""
    if isinstance(bruto, datetime):
        quando = bruto
    elif isinstance(bruto, str) and bruto.strip():
        try:
            quando = datetime.fromisoformat(bruto.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def _carimbo(quando: datetime | None) -> str | None:
    """A data como a conversa guarda (`dados`), para comparar entre rodadas."""
    return quando.isoformat(timespec="seconds") if quando is not None else None


def _participantes(thread: dict, chave: str) -> list[dict]:
    return [p for p in _lista(thread.get(chave)) if _id(p.get("type"))]


def _tipos(participantes: list[dict]) -> list[str]:
    return sorted({_id(p.get("type")).upper() for p in participantes})


def _comprador(thread: dict) -> tuple[str | None, str | None]:
    """(id, nome) do comprador da thread — o participante CUSTOMER autorizado."""
    for chave in ("authorized_participants", "current_participants"):
        for p in _participantes(thread, chave):
            if _id(p.get("type")).upper() == PARA_CLIENTE:
                return _id(p.get("id")) or None, _texto(p.get("display_name"))
    return None, None


def _entidades(thread: dict) -> list[dict]:
    saida = []
    for e in _lista(thread.get("entities"))[:MAX_ENTIDADES]:
        tipo, eid = _id(e.get("type")).upper(), _id(e.get("id"))
        if tipo and eid:
            item = {"tipo": tipo[:40], "id": eid[:64]}
            if _texto(e.get("label")):
                item["rotulo"] = e["label"].strip()[:200]
            saida.append(item)
    return saida


# ── Erro de leitura ───────────────────────────────────────────────────────


def _texto_do_erro(e: CarrefourError) -> str:
    """Só o HTTP e o código do cliente — o cliente já não carrega corpo de resposta."""
    partes = [f"HTTP {e.http_status}" if e.http_status else "", e.code]
    return " ".join(p for p in partes if p) or "erro"


def _sem_escopo(e: CarrefourError) -> bool:
    return e.http_status in (401, 403)


# ── Mensagens ─────────────────────────────────────────────────────────────


@dataclass
class _Mensagem:
    externo_id: str
    autor: str
    texto: str | None
    enviada_em: datetime | None
    tipo: str
    anexos: list[dict]
    payload: dict = field(default_factory=dict)


def _anexos(bruto: Any) -> list[dict]:
    """Nome, tamanho e id do anexo (o download é o M13, autenticado): nunca uma URL."""
    saida = []
    for a in _lista(bruto):
        nome = (_texto(a.get("name")) or "").strip()[:200]
        if not nome:
            continue
        item: dict[str, Any] = {
            "nome": nome,
            "tipo": "image" if nome.lower().endswith(_EXTENSOES_IMAGEM) else "arquivo",
        }
        if _id(a.get("id")):
            item["id"] = _id(a.get("id"))[:64]
        if isinstance(a.get("size"), int) and not isinstance(a.get("size"), bool):
            item["tamanho"] = a["size"]
        saida.append(item)
    return saida


def _tipo(texto: str | None, anexos: list[dict]) -> str:
    if texto:
        return "texto"
    if anexos:
        return "imagem" if all(a.get("tipo") == "image" for a in anexos) else "arquivo"
    return "outro"


def _mensagem(m: dict) -> _Mensagem | None:
    mid = _id(m.get("id"))
    if not mid:
        return None
    de = _id(_dict(m.get("from")).get("type")).upper()
    if de == _DE_CLIENTE:
        autor = AUTOR_CLIENTE
    elif de == _DE_LOJA:
        autor = AUTOR_LOJA
    elif de == _DE_OPERADOR:
        # O atendimento do Carrefour: como a mediação, não é o comprador nem a
        # loja — a vez da loja quem diz é a Mirakl (`shop_reply_needed_since`).
        autor = AUTOR_MEDIADOR
    else:
        autor = AUTOR_SISTEMA
    texto = _texto(m.get("body"))
    anexos = _anexos(m.get("attachments"))
    payload: dict[str, Any] = {}
    para = _tipos(_lista(m.get("to")))
    if para:
        payload["para"] = para
    if autor == AUTOR_MEDIADOR:
        payload["remetente"] = "carrefour"
    return _Mensagem(
        externo_id=mid,
        autor=autor,
        texto=texto,
        enviada_em=_data(m.get("date_created")),
        tipo=_tipo(texto, anexos),
        anexos=anexos,
        payload=payload,
    )


async def _adotar_nossa(
    session: AsyncSession, conversa: AtendimentoConversa, item: _Mensagem
) -> None:
    """A resposta que saiu daqui voltando pela leitura sem o id na nossa linha.

    O 201 traz o `message_id` (a linha já o tem, e o `gravar` a acha por ele);
    sem ele — o envio que ficou `revisar` (timeout) —, casa pelo texto, sem o
    teto de 15 min do `gravar`: senão a mesma resposta viraria uma segunda
    linha `externo`, que ainda cala a IA como "respondida por fora".
    """
    existente = await session.scalar(
        select(AtendimentoMensagem.id).where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.externo_id == item.externo_id,
        )
    )
    if existente is not None:
        return
    alvo = gravar.normalizar_para_comparar(item.texto)
    if not alvo:
        return
    candidatas = (
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.autor == AUTOR_LOJA,
                    AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                    AtendimentoMensagem.externo_id.is_(None),
                    AtendimentoMensagem.status.in_((MSG_ENVIADA, MSG_REVISAR)),
                )
                .order_by(AtendimentoMensagem.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    escolhida = next(
        (m for m in candidatas if gravar.normalizar_para_comparar(m.texto) == alvo), None
    )
    if escolhida is None:
        return
    escolhida.externo_id = item.externo_id
    escolhida.status = MSG_ENVIADA
    escolhida.erro = None
    if item.enviada_em is not None:
        escolhida.enviada_em = item.enviada_em
    gravar.recalcular(conversa, [escolhida])
    await session.flush()
    logger.info(
        "atendimento_carrefour_nossa_adotada",
        conversa_id=str(conversa.id),
        mensagem_id=str(escolhida.id),
    )


async def _gravar_mensagens(
    session: AsyncSession, conversa: AtendimentoConversa, itens: list[_Mensagem], agora: datetime
) -> int:
    """Grava as mensagens (idempotente, pelo id da Mirakl). → quantas são novas."""
    novas = 0
    for item in sorted(itens, key=lambda x: x.enviada_em or agora):
        if item.autor == AUTOR_LOJA:
            await _adotar_nossa(session, conversa, item)
        _msg, criada = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=item.externo_id,
            autor=item.autor,
            texto=item.texto,
            enviada_em=item.enviada_em,
            tipo=item.tipo,
            anexos=item.anexos,
            payload=item.payload or None,
        )
        novas += int(criada)
    return novas


# ── Leitura ───────────────────────────────────────────────────────────────


@dataclass
class _Rodada:
    resultado: ResultadoSync
    novas: set[UUID] = field(default_factory=set)
    atualizadas: set[UUID] = field(default_factory=set)
    avisos: list[str] = field(default_factory=list)

    def fechar(self) -> ResultadoSync:
        self.resultado.conversas_novas = len(self.novas)
        self.resultado.conversas_atualizadas = len(self.atualizadas - self.novas)
        if self.avisos:
            self.resultado.erro = "; ".join(self.avisos)
        return self.resultado


async def _existentes(
    session: AsyncSession, canal: AtendimentoCanal, externos: list[str]
) -> dict[str, AtendimentoConversa]:
    if not externos:
        return {}
    linhas = await session.execute(
        select(AtendimentoConversa).where(
            AtendimentoConversa.integration_id == canal.integration_id,
            AtendimentoConversa.plataforma == PLATAFORMA,
            AtendimentoConversa.canal == CANAL_CHAT,
            AtendimentoConversa.externo_id.in_(externos),
        )
    )
    return {c.externo_id: c for c in linhas.scalars()}


async def _listar(
    cliente: Any, desde: datetime
) -> tuple[dict[str, dict], bool, CarrefourError | None]:
    """As threads que mexeram desde `desde` (todas as páginas, até o teto). Só HTTP.

    → (threads por id, a lista veio inteira?, o erro que parou no meio).
    O erro na PRIMEIRA página sobe: não há nada para gravar.
    """
    threads: dict[str, dict] = {}
    token: str | None = None
    for pagina in range(MAX_PAGINAS):
        try:
            corpo = await cliente.list_threads(
                updated_since=None if token else desde, page_token=token, limit=PAGINA
            )
        except CarrefourError as e:
            if pagina == 0:
                raise
            return threads, False, e
        for t in _lista(corpo.get("data")):
            if _id(t.get("id")):
                threads[_id(t.get("id"))] = t
        token = _texto(corpo.get("next_page_token"))
        if token is None:
            return threads, True, None
    return threads, False, None


async def _processar_thread(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    thread: dict,
    agora: datetime,
    rodada: _Rodada,
    *,
    existente: AtendimentoConversa | None,
    conta: str | None,
) -> None:
    tid = _id(thread.get("id"))
    atualizada = _data(thread.get("date_updated"))
    lida_ate = _carimbo(atualizada)
    if existente is not None and _dict(existente.dados).get("lida_ate") == lida_ate:
        return  # nada mexeu desde a última leitura (a folga do cursor a trouxe de novo)
    meta = _dict(thread.get("metadata"))
    entidades = _entidades(thread)
    pedido = next((e["id"] for e in entidades if e["tipo"] in _ENTIDADES_PEDIDO), None)
    oferta = next((e for e in entidades if e["tipo"] == _ENTIDADE_OFERTA), None)
    topico = _dict(thread.get("topic"))
    comprador_id, comprador_nome = _comprador(thread)
    dados: dict[str, Any] = {
        "thread_id": tid,
        "lida_ate": lida_ate,
        "relida_em": _carimbo(agora),
        "entidades": entidades,
        "autorizados": _tipos(_participantes(thread, "authorized_participants")),
        # A vez da loja é a da Mirakl (None = a loja já respondeu ou marcou
        # "não precisa de resposta" no portal).
        CHAVE_VEZ_DA_LOJA: _carimbo(_data(meta.get("shop_reply_needed_since"))),
    }
    if _texto(topico.get("value")):
        dados["assunto"] = topico["value"].strip()[:200]
        dados["assunto_tipo"] = _id(topico.get("type"))[:40] or None
    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=integration,
        plataforma=PLATAFORMA,
        canal_nome=CANAL_CHAT,
        externo_id=tid,
        conta=conta,
        comprador_id=comprador_id,
        comprador_nome=comprador_nome,
        pedido_marketplace=pedido,
        anuncio_id=oferta["id"] if oferta else None,
        anuncio_titulo=oferta.get("rotulo") if oferta else None,
        dados=dados,
    )
    itens = [x for x in (_mensagem(m) for m in _lista(thread.get("messages"))) if x is not None]
    novas = await _gravar_mensagens(session, conversa, itens, agora)
    # A vez pode ter mudado sem mensagem nova (respondida ou dispensada no
    # portal): a fila se refaz pelos `dados` de agora.
    gravar.recalcular(conversa)
    await session.flush()
    rodada.resultado.mensagens_novas += novas
    if criada:
        rodada.novas.add(conversa.id)
    else:
        rodada.atualizadas.add(conversa.id)


async def sincronizar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
) -> ResultadoSync:
    """Uma rodada de leitura do canal. Commita por conversa; nunca levanta por HTTP.

    Falha na lista (a primeira página) vira `ResultadoSync(status="erro"|
    "sem_escopo")`; falha numa página seguinte grava o que veio, avisa e não
    anda o cursor.
    """
    agora = _agora()
    if canal.canal != CANAL_CHAT:
        return ResultadoSync(status=STATUS_ERRO, erro=f"canal desconhecido: {canal.canal}")
    cursor = dict(canal.cursor or {})
    espera = _data(cursor.get("espera_ate"))
    if espera is not None and agora < espera:
        return ResultadoSync(
            status=STATUS_ERRO,
            erro=f"HTTP 429: limite do Carrefour — nova leitura depois de {_carimbo(espera)}",
        )
    desde = _data(cursor.get("desde")) or (agora - JANELA_PRIMEIRA_LEITURA)
    rodada = _Rodada(ResultadoSync(status=STATUS_OK))
    try:
        threads, inteira, parou = await _listar(cliente, desde - MARGEM_CURSOR)
    except CarrefourError as e:
        return _falha_da_lista(canal, e, agora)
    if parou is not None:
        if parou.http_status == 429:
            _segurar(canal, parou, agora)
        rodada.avisos.append(f"leitura interrompida: {_texto_do_erro(parou)}")
    elif not inteira:
        rodada.avisos.append(f"mais de {MAX_PAGINAS * PAGINA} conversas: o resto na próxima")

    existentes = await _existentes(session, canal, list(threads))
    conta = await lojas.nome_da_loja(session, integration) or None

    def _quando(t: dict) -> datetime:
        return _data(t.get("date_updated")) or agora

    for tid, thread in sorted(threads.items(), key=lambda par: _quando(par[1])):
        await _processar_thread(
            session,
            canal,
            integration,
            thread,
            agora,
            rodada,
            existente=existentes.get(tid),
            conta=conta,
        )
        await gravar.fim_do_item(session)
    novo = dict(canal.cursor or {})
    if inteira:
        # A lista inteira foi lida: tudo o que mexeu até o começo da rodada
        # está gravado (a folga cobre o relógio da Mirakl).
        novo["desde"] = _carimbo(max(desde, agora))
    if (_data(novo.get("espera_ate")) or agora) <= agora:
        novo.pop("espera_ate", None)  # a espera de um 429 antigo sai; a desta rodada fica
    canal.cursor = {**novo, "ultima_rodada": _carimbo(agora)}
    resultado = rodada.fechar()
    logger.info(
        "atendimento_carrefour_rodada",
        canal_id=str(canal.id),
        status=resultado.status,
        threads=len(threads),
        conversas_novas=resultado.conversas_novas,
        conversas_atualizadas=resultado.conversas_atualizadas,
        mensagens_novas=resultado.mensagens_novas,
        inteira=inteira,
    )
    return resultado


def _segurar(canal: AtendimentoCanal, e: CarrefourError, agora: datetime) -> None:
    """429: o canal só volta a ler depois do `Retry-After` (ou de `ESPERA_429`)."""
    espera = timedelta(seconds=e.retry_after) if e.retry_after else ESPERA_429
    canal.cursor = {**(canal.cursor or {}), "espera_ate": _carimbo(agora + espera)}


def _falha_da_lista(canal: AtendimentoCanal, e: CarrefourError, agora: datetime) -> ResultadoSync:
    texto = _texto_do_erro(e)
    logger.warning(
        "atendimento_carrefour_leitura_falhou",
        canal_id=str(canal.id),
        http=e.http_status,
        codigo=e.code,
    )
    if _sem_escopo(e):
        return ResultadoSync(status=STATUS_SEM_ESCOPO, erro=f"{MOTIVO_SEM_PERMISSAO} ({texto})")
    if e.http_status == 429:
        _segurar(canal, e, agora)
        return ResultadoSync(status=STATUS_ERRO, erro=f"{texto}: limite de leituras do Carrefour")
    return ResultadoSync(status=STATUS_ERRO, erro=texto)


# ── Envio ─────────────────────────────────────────────────────────────────


async def _destino(session: AsyncSession, conversa: AtendimentoConversa) -> str:
    """Para quem vai a resposta: quem falou por último que não é a loja.

    O Carrefour (operador) cobrando a loja recebe a resposta; senão, o
    comprador. Só quem a thread autoriza: a thread só com o Carrefour (sem
    comprador) responde ao Carrefour.
    """
    autorizados = _dict(conversa.dados).get("autorizados") or []
    ultimo = await session.scalar(
        select(AtendimentoMensagem.autor)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor.in_((AUTOR_CLIENTE, AUTOR_MEDIADOR)),
        )
        .order_by(AtendimentoMensagem.enviada_em.desc(), AtendimentoMensagem.created_at.desc())
        .limit(1)
    )

    def pode(tipo: str) -> bool:
        # Sem a lista gravada (não deveria acontecer): vale só quem falou por último.
        return not autorizados or tipo in autorizados

    if ultimo == AUTOR_MEDIADOR and pode(PARA_CARREFOUR):
        return PARA_CARREFOUR
    if pode(PARA_CLIENTE):
        return PARA_CLIENTE
    return PARA_CARREFOUR


def _codigo_http(r: httpx.Response) -> str:
    return f"HTTP {r.status_code}"


async def enviar_texto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration | None,
    cliente: Any,
    texto: str,
) -> ResultadoEnvio:
    """Responde a thread desta conversa (M12). Timeout/5xx = ambiguo. Nunca levanta.

    201 = saiu, com o `message_id` como id externo · 4xx = não saiu (401/403:
    a chave recusada) · 5xx / sem resposta depois de mandar = AMBÍGUO (pode
    ter saído: `revisar`, ninguém retenta; o cliente não repete o POST).
    Falha de CONEXÃO é recusa limpa: o pedido nem chegou ao Carrefour.
    """
    if not (texto or "").strip():
        return ResultadoEnvio(ok=False, erro="texto_vazio")
    limite = limite_caracteres(PLATAFORMA, conversa.canal)
    if len(texto) > limite:
        return ResultadoEnvio(ok=False, erro=f"envio_invalido:acima_do_limite_{limite}")
    if len(texto.strip()) < REPLY_BODY_MIN:
        return ResultadoEnvio(ok=False, erro=f"envio_invalido:abaixo_do_minimo_{REPLY_BODY_MIN}")
    if conversa.canal != CANAL_CHAT:
        return ResultadoEnvio(ok=False, erro=f"canal_desconhecido:{conversa.canal}")
    destino = await _destino(session, conversa)
    para: dict[str, Any] = {"type": destino}
    if destino == PARA_CLIENTE and conversa.comprador_id:
        para["id"] = conversa.comprador_id
    try:
        r = await cliente.reply_thread(conversa.externo_id, texto, [para])
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as e:
        return ResultadoEnvio(ok=False, erro=f"sem_conexao:{type(e).__name__}")
    except httpx.HTTPError as e:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"sem_resposta:{type(e).__name__}")
    except ValueError as e:
        # O cliente recusou antes de mandar (id da thread, tamanho, destino).
        return ResultadoEnvio(ok=False, erro=f"envio_invalido:{str(e).split(' ', 1)[0]}")
    except CarrefourError as e:
        # Credencial inválida no cadastro: nada saiu.
        return ResultadoEnvio(ok=False, erro=f"credencial_invalida:{e.code}")
    except Exception as e:  # noqa: BLE001 — erro da plataforma nunca levanta daqui
        logger.exception("atendimento_carrefour_envio_inesperado", conversa_id=str(conversa.id))
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"inesperado:{type(e).__name__}")

    payload: dict[str, Any] = {"http": r.status_code, "destino": destino}
    if 200 <= r.status_code < 300:
        try:
            corpo = _dict(r.json())
        except ValueError:
            corpo = {}
        message_id = _id(corpo.get("message_id")) or None
        if message_id:
            payload["message_id"] = message_id
        return ResultadoEnvio(ok=True, externo_id=message_id, payload=payload)
    logger.info(
        "atendimento_carrefour_envio_recusado",
        conversa_id=str(conversa.id),
        http=r.status_code,
    )
    if r.status_code >= 500:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=_codigo_http(r), payload=payload)
    if r.status_code in (401, 403):
        erro = f"{_codigo_http(r)} sem_escopo: {MOTIVO_SEM_PERMISSAO}"
    elif r.status_code == 429:
        erro = f"{_codigo_http(r)}: limite do Carrefour, tente de novo em 1 minuto"
    else:
        erro = _codigo_http(r)
    return ResultadoEnvio(ok=False, erro=erro, payload=payload)
