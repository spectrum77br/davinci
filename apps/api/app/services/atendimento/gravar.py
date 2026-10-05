"""A única porta de escrita de conversa e mensagem do atendimento.

Todo adaptador (Shopee, ML, TikTok, Amazon) e o envio passam por aqui. Três
coisas que ninguém pode esquecer ficam num lugar só:

1. IDEMPOTÊNCIA — a consulta periódica traz a mesma mensagem em toda rodada.
   A chave é (conversa, id externo), no banco (UNIQUE) e aqui (busca antes de
   inserir; se outro processo ganhou a corrida, o SAVEPOINT desfaz só a linha).

2. AUTOR × ORIGEM — mensagem da loja que chega pelo sync pode ser:
     • a NOSSA resposta voltando (enviamos pelo DaVinci e a plataforma a
       devolve na próxima leitura) → ADOTA a linha que já temos em vez de
       duplicar a conversa com a mesma frase duas vezes;
     • resposta dada FORA do DaVinci (Duoke, Seller Center, celular) → origem
       `externo`. É ela que diz "alguém já respondeu, a IA se cala" e que
       aposenta o rascunho pendente.

3. A FILA — `aguardando_resposta` e `prazo_resposta_em` são derivados das
   mensagens aqui (`recalcular`), para a lista ordenar e filtrar sem varrer
   mensagem e para o alerta de prazo ter uma coluna só para olhar. Mensagem
   automática (`constantes.e_mensagem_automatica`) não é resposta: não fecha
   a vez do comprador (menos no turno em que ele só mandou cartão) nem
   aposenta a sugestão da IA.

4. A ORDEM DAS TRAVAS — quem muda mensagem trava a CONVERSA antes (sync,
   envio e a tela). Com a mesma ordem em todo lugar, o sync que adota a
   nossa resposta e o envio que grava o resultado dela não se travam um ao
   outro (deadlock), e ninguém deriva a fila de um retrato velho da conversa.

5. A ETIQUETA (status atual, 01/10/2026) — recalculada AQUI quando a leitura
   traz o que a decide: conversa nova, pedido ligado ou trocado, o pack/order
   do ML, as reclamações do pack (`claim_ids`); e na primeira mensagem nova
   de uma conversa que ainda não tem etiqueta. Mensagem comum não muda o
   status: não recalcula (o sync passa por milhares delas). Quem calcula é
   `etiqueta.recalcular_etiqueta`; o que muda fora da leitura (Bling,
   Margem, reclamações) é com o cron (`etiqueta_cron`).

Nada aqui commita: quem chama decide a transação (o sync commita no fim da
rodada do canal; o envio commita antes e depois de chamar a plataforma).

Texto de comprador nunca vai para o log — só ids e contagens.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, case, false, func, inspect, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
)
from app.services.atendimento import etiqueta as etiqueta_svc
from app.services.atendimento import lojas
from app.services.atendimento.constantes import (
    AUTOMACOES_QUE_FECHAM_A_VEZ,
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_MEDIADOR,
    AUTOR_SISTEMA,
    AUTORES,
    AVALIACAO_OBSERVOU,
    CARTOES_DO_COMPRADOR_SHOPEE,
    CARTOES_DO_COMPRADOR_TIKTOK,
    CHAVE_AUTOMACAO,
    CHAVE_PRAZO_LIDO_EM,
    CHAVE_PRAZO_PLATAFORMA,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_ABERTA,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    CONVERSA_RESPONDIDA,
    ESPACOS,
    FIGURINHA_CAMPANHA_DUOKE,
    FONTE_SHOPEE_API,
    FONTES_SHOPEE_DA_PLATAFORMA,
    INICIO_AUTOMATICA,
    MSG_ENVIADA,
    MSG_ENVIANDO,
    MSG_FALHOU,
    MSG_RECEBIDA,
    MSG_REVISAR,
    ORIGEM_CLIENTE,
    ORIGEM_EXTERNO,
    ORIGEM_NOTA,
    ORIGEM_SISTEMA,
    ORIGENS_DAVINCI,
    PADRAO_MENSAGEM_AUTOMATICA,
    PADRAO_RESPOSTA_AUTOMATICA,
    PAPEL_TIKTOK_ATENDIMENTO,
    PRIMEIRAS_LETRAS_AUTOMATICA,
    RASCUNHO_PENDENTE,
    RASCUNHO_SUBSTITUIDO,
    TIPO_SHOPEE_CARTAO_PEDIDO,
    TIPO_SHOPEE_FIGURINHA,
    TIPO_TIKTOK_CARTAO_PEDIDO,
    TRECHOS_CAMPANHA_COM_USUARIO,
    e_mensagem_automatica,
    e_nota,
    e_resposta_automatica,
    marca_automacao,
    sla_horas,
)

logger = structlog.get_logger()

# O que a lista mostra de cada conversa: uma linha.
RESUMO_MAX = 160

# Quanto a mensagem que volta pelo sync pode se afastar da nossa para ainda
# ser "a mesma". Cobre a diferença entre o relógio da plataforma e o nosso e
# a demora da própria plataforma para listar a mensagem.
JANELA_ADOCAO = timedelta(minutes=15)

# Linhas NOSSAS que podem ser adotadas: em voo, ambígua (o timeout que na
# verdade saiu — a adoção é a prova) ou enviada sem id da plataforma.
_STATUS_ADOTAVEIS = (MSG_ENVIANDO, MSG_REVISAR, MSG_ENVIADA)

# A ação da avaliação quando a sugestão foi substituída pela resposta de
# fora (a mesma do router: `_ACAO_PELO_STATUS[substituido]`).
ACAO_SUBSTITUIDO = "escreveu_do_zero"

# Situações que só pessoa (ou a plataforma) muda: o recálculo não mexe.
_SITUACOES_FIXAS = (CONVERSA_FECHADA, CONVERSA_BLOQUEADA)


# ── Texto ─────────────────────────────────────────────────────────────────


def sem_nul(valor):
    """Tira o caractere NUL (\\x00) de texto e, recursivamente, de dict/list.

    O Postgres recusa NUL em TEXT (CharacterNotInRepertoire) e em JSONB
    (\\u0000, UntranslatableCharacter). Um comprador que manda um NUL (ou uma
    plataforma que o devolve no JSON) derrubaria a gravação — e, como o sync
    desfaz a rodada inteira e o cursor não anda, a mesma mensagem voltaria
    primeiro em toda rodada: a loja pararia de entrar para sempre. Aqui, na
    porta única, o NUL some antes de chegar ao banco.
    """
    if isinstance(valor, str):
        return valor.replace("\x00", "") if "\x00" in valor else valor
    if isinstance(valor, dict):
        return {sem_nul(k): sem_nul(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [sem_nul(v) for v in valor]
    return valor


def similaridade(a: str | None, b: str | None) -> float:
    """Quanto o texto enviado se parece com a sugestão (0–1, difflib).

    Compara com espaços normalizados: quebra de linha a mais não é edição.
    Mora aqui (e o `enviar` a usa) porque a resposta que chega de FORA — o
    Duoke respondendo no teste em observação — também é comparada com a
    sugestão da IA, na gravação (`_promover_observacao`).
    """
    x = " ".join((a or "").split())
    y = " ".join((b or "").split())
    if not x and not y:
        return 1.0
    return round(difflib.SequenceMatcher(None, x, y).ratio(), 4)


def resumo(texto: str | None) -> str:
    """Uma linha de até 160 caracteres para a lista de conversas."""
    uma_linha = " ".join((texto or "").split())
    if len(uma_linha) <= RESUMO_MAX:
        return uma_linha
    return uma_linha[: RESUMO_MAX - 1].rstrip() + "…"


# A prévia da lista ("[Pedido]", "[Produto]", "[Imagem]", como no Duoke).
# Vídeo, arquivo e o que vier de novo caem em "outro".
TIPOS_PREVIA = ("texto", "imagem", "produto", "pedido")


def tipo_previa(tipo: str | None) -> str | None:
    """Tipo da mensagem → o da prévia da lista (texto|imagem|produto|pedido|outro)."""
    if tipo is None:
        return None
    return tipo if tipo in TIPOS_PREVIA else "outro"


def normalizar_para_comparar(texto: str | None) -> str:
    """Forma canônica para decidir se duas mensagens são "o mesmo texto".

    A plataforma pode devolver a nossa resposta com espaço a menos, aspas
    trocadas, emoji removido (o ML só aceita ISO-8859-1) ou acento
    normalizado. Sem acento, sem pontuação, sem caixa, espaço único — o que
    sobra é o conteúdo. Serve SÓ para comparar, nunca para exibir.
    """
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(ch for ch in decomposto if not unicodedata.combining(ch))
    so_palavras = re.sub(r"[\W_]+", " ", sem_acento.casefold())
    return " ".join(so_palavras.split())


def mensagem_automatica_sql(texto: Any, payload: Any = None) -> Any:
    """`constantes.e_mensagem_automatica` em SQL — nunca NULL (texto vazio = não).

    Para quem conta ou escolhe mensagens no banco sem trazê-las ao Python (a
    métrica de tempo de resposta, os exemplos da IA): o mesmo regex, sobre o
    mesmo começo do texto em minúsculas, e — com a coluna `payload` — a
    figurinha da campanha e os cartões da Shopee. O teste compara os dois lados.
    """
    inicio = func.left(func.coalesce(texto, ""), INICIO_AUTOMATICA)
    minusculo = func.lower(inicio)
    regex = minusculo.regexp_match(PADRAO_MENSAGEM_AUTOMATICA)
    # Os pré-filtros são condição necessária do regex: o resultado é o mesmo,
    # e só ~1/3 das mensagens paga o regex. A primeira letra depois dos
    # espaços vale para todos, menos as campanhas que começam pelo usuário do
    # comprador — essas, só se o texto tiver o trecho fixo delas.
    primeira = func.left(func.ltrim(inicio, ESPACOS), 1)
    pelo_texto = case(
        (primeira.in_(sorted(PRIMEIRAS_LETRAS_AUTOMATICA)), regex),
        (or_(*(minusculo.like(t) for t in TRECHOS_CAMPANHA_COM_USUARIO)), regex),
        else_=false(),
    )
    if payload is None:
        return pelo_texto
    return or_(pelo_texto, automatica_pelo_payload_sql(payload))


def automatica_pelo_payload_sql(payload: Any) -> Any:
    """`constantes.e_automatica_pelo_payload` em SQL (a coluna JSONB `payload`)."""
    fonte = payload["source"].astext
    return func.coalesce(
        or_(
            # A marca do motor de automações: um objeto em `automacao`.
            # `->` (e não o subscrito): sobre um valor com cast o subscrito vira
            # sintaxe errada (o teste compara com o Python sobre literais).
            func.jsonb_typeof(payload.op("->")(CHAVE_AUTOMACAO)) == "object",
            fonte.in_(FONTES_SHOPEE_DA_PLATAFORMA),
            and_(
                fonte == FONTE_SHOPEE_API,
                payload["message_type"].astext == TIPO_SHOPEE_CARTAO_PEDIDO,
            ),
            and_(
                payload["type"].astext == TIPO_TIKTOK_CARTAO_PEDIDO,
                payload[("sender", "role")].astext == PAPEL_TIKTOK_ATENDIMENTO,
            ),
            and_(
                fonte == FONTE_SHOPEE_API,
                payload["message_type"].astext == TIPO_SHOPEE_FIGURINHA,
                # Caminho (`#>>`), não `["content"]["sticker_id"]`: o subscrito
                # do JSONB vira sintaxe errada sobre um valor com cast (o teste).
                payload[("content", "sticker_id")].astext == FIGURINHA_CAMPANHA_DUOKE,
            ),
        ),
        false(),
    )


def resposta_automatica_sql(texto: Any) -> Any:
    """`constantes.e_resposta_automatica` (a régua ANTIGA) em SQL — nunca NULL.

    Só o turno em que o comprador só mandou cartão usa (`recalcular_conversa`):
    poucas linhas por conversa, sem pré-filtro. O teste compara com o Python.
    """
    inicio = func.left(func.coalesce(texto, ""), INICIO_AUTOMATICA)
    return func.lower(inicio).regexp_match(PADRAO_RESPOSTA_AUTOMATICA)


def cartao_do_comprador_sql(payload: Any) -> Any:
    """`constantes.e_cartao_do_comprador` em SQL (a coluna JSONB `payload`) — nunca NULL."""
    return func.coalesce(
        or_(
            payload["message_type"].astext.in_(CARTOES_DO_COMPRADOR_SHOPEE),
            payload["type"].astext.in_(CARTOES_DO_COMPRADOR_TIKTOK),
        ),
        false(),
    )


# ── Relógio ───────────────────────────────────────────────────────────────


def _utc(quando: datetime | None) -> datetime | None:
    """Datas sempre com fuso. Adaptador que mandar data ingênua está em UTC."""
    if quando is None:
        return None
    if quando.tzinfo is None:
        return quando.replace(tzinfo=UTC)
    return quando.astimezone(UTC)


def _quando(m: AtendimentoMensagem) -> datetime:
    """Momento da mensagem: o relógio da plataforma; sem ele, quando a linha nasceu.

    `created_at` é lido do estado já carregado (nunca dispara ida ao banco —
    numa sessão assíncrona isso estouraria fora do greenlet).
    """
    if m.enviada_em is not None:
        return _utc(m.enviada_em)
    criada = inspect(m).dict.get("created_at")
    return _utc(criada) if criada is not None else datetime.now(UTC)


async def _atributo(session: AsyncSession, obj: object, nome: str):
    """Lê um atributo que pode não ter vindo do banco (default do servidor)."""
    if nome in inspect(obj).unloaded:
        await session.refresh(obj, [nome])
    return getattr(obj, nome)


# ── Trava ─────────────────────────────────────────────────────────────────

# Quanto quem age pela TELA (Enviar, Fechar, trocar o modo) espera pela trava
# da linha. A rodada do sync pode segurar a conversa por minutos; é melhor a
# pessoa ver "tente de novo" do que a tela pendurada.
ESPERA_TRAVA_TELA = "5s"


def _trava_indisponivel(e: BaseException) -> bool:
    """O Postgres desistiu de esperar a trava (`lock_timeout`, SQLSTATE 55P03)?"""
    orig = getattr(e, "orig", None)
    causa = getattr(orig, "__cause__", None) or orig
    return getattr(causa, "sqlstate", None) == "55P03"


def _cabe(valor: str, tamanho: int) -> str:
    """Corta no tamanho da coluna: um id anômalo da plataforma (maior que a
    coluna) daria erro de banco em TODA rodada e congelaria o canal — a
    mesma "mensagem envenenada" do NUL. Corte determinístico: a leitura
    seguinte chega ao mesmo id e continua idempotente."""
    return valor[:tamanho]


# URL de foto maior que isto não é foto de perfil de CDN: é lixo (ou um
# `data:` inteiro) que só incharia a lista de conversas.
URL_AVATAR_MAX = 2048


def url_avatar(valor: object) -> str | None:
    """A URL da foto do comprador, se servir para um `<img>` da tela; senão None.

    Só `https://`/`http://` (a CDN da plataforma) ou um caminho da própria
    web (`/atendimento-demo/...`, a semente local). `javascript:`, `data:`,
    `//outro-site` e vazio viram None — e None, no upsert, NÃO apaga a foto
    que já estava (a Shopee manda `to_avatar: ""` quando a pessoa tirou a foto
    ou quando a lista vem incompleta).
    """
    if not isinstance(valor, str):
        return None
    url = sem_nul(valor).strip()
    if not url or len(url) > URL_AVATAR_MAX or any(c.isspace() for c in url):
        return None
    minusculo = url.lower()
    if minusculo.startswith(("https://", "http://")):
        return url
    if url.startswith("/") and not url.startswith("//"):
        return url
    return None


async def fim_do_item(session: AsyncSession) -> None:
    """Commit entre uma conversa e a próxima na rodada do sync (só os adaptadores).

    A rodada de um canal fala com a API da loja conversa por conversa. Numa
    transação só, cada conversa gravada ficava TRAVADA até o fim da rodada
    (dezenas de chamadas HTTP depois) — e o "Enviar", o "Fechar" e a troca de
    modo da tela esperavam por ela. Commitando por conversa, a trava dura só
    a gravação daquela conversa. É seguro: a gravação é idempotente (id
    externo) e o cursor só anda no fim da rodada — uma rodada que cai no meio
    relê, na próxima, o que já estava gravado, sem duplicar.
    """
    await session.commit()


async def travar_linha(session: AsyncSession, obj, *, espera: str | None = None) -> bool:
    """Trava a linha de `obj` (FOR NO KEY UPDATE) e relê o objeto do banco; False = ocupada.

    Com `espera` (ex.: "5s"), desiste depois desse tempo e devolve False, sem
    estragar a transação de quem chamou: a tentativa roda num SAVEPOINT.
    `SET LOCAL lock_timeout` vale até o fim da transação — quem chama commita
    logo (a tela) ou não passa `espera` (o sync, o resultado de um envio que
    já saiu: esses esperam o que for preciso).

    Relê DEPOIS de travar: o que a pessoa ou outro processo commitou enquanto
    este objeto estava em memória (fechou, pausou a IA, respondeu) passa a
    valer aqui. O que estava pendente no objeto é gravado antes (flush).
    """
    await session.flush()
    modelo = type(obj)
    chave = inspect(obj).identity
    if chave is None:  # objeto que nunca foi ao banco: não há o que travar
        return True
    try:
        async with session.begin_nested():
            if espera:
                # Valor fixo daqui (nunca de fora): SET não aceita parâmetro.
                await session.execute(text(f"SET LOCAL lock_timeout = '{espera}'"))
            # FOR NO KEY UPDATE (key_share), a mesma trava de um UPDATE comum:
            # segura quem muda a linha, mas NÃO quem só a referencia — a IA
            # gravando um rascunho (FK para a conversa) não espera a rodada do
            # sync acabar.
            await session.execute(
                select(modelo.id).where(modelo.id == chave[0]).with_for_update(key_share=True)
            )
    except DBAPIError as e:
        if not _trava_indisponivel(e):
            raise
        return False
    await session.refresh(obj)
    return True


# ── Conversa ──────────────────────────────────────────────────────────────

# O acontecimento que vai entre parênteses na linha do tempo da etiqueta.
MOTIVO_ETIQUETA_LEITURA = "leitura da plataforma"
MOTIVO_ETIQUETA_MENSAGEM = "mensagem nova"


def _o_que_decide_a_etiqueta(conversa: AtendimentoConversa) -> tuple:
    """O que a LEITURA grava e muda a etiqueta: canal, pedido, pack/order, claims e substatus do ML.

    Se nada disto mudou numa leitura, a etiqueta também não mudou por causa
    dela — e o sync não paga o recálculo (3 consultas) a cada conversa.
    """
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    claims = dados.get("claim_ids")
    return (
        conversa.plataforma,
        conversa.canal,
        (conversa.pedido_marketplace or "").strip(),
        str(dados.get("pack_id") or ""),
        str(dados.get("order_id") or ""),
        tuple(str(c) for c in claims) if isinstance(claims, list) else (),
        # O chat bloqueado pela reclamação é o que liga a reserva dos claims
        # (`etiqueta_fatos._claims_do_pack`).
        str(dados.get("substatus_ml") or ""),
    )


async def _recalcular_etiqueta(
    session: AsyncSession, conversa: AtendimentoConversa, *, motivo: str, travar: bool
) -> None:
    """A etiqueta da conversa pelos fatos de agora. Nunca derruba a gravação.

    `travar` = trava (e relê) a conversa antes: uma troca à mão feita na tela
    enquanto a leitura rodava não pode ser sobrescrita por um retrato velho
    de `etiqueta_manual`. A conversa recém-criada não precisa (ninguém a viu).
    Um fato que não se lê deixa a etiqueta como está (`recalcular_etiqueta`
    roda os fatos num SAVEPOINT).
    """
    if travar:
        await travar_linha(session, conversa)
    await etiqueta_svc.recalcular_etiqueta(session, conversa, motivo=motivo)
    # Como o resto do gravar: sai daqui gravado (flush), sem nada pendente.
    await session.flush()


async def _nome_da_loja(session: AsyncSession, integration: Integration | None) -> str | None:
    """O `conta` de quando o adaptador não mandou um: o nome da LOJA (parte 2, P2).

    Os adaptadores já passam `lojas.nome_da_loja`; isto cobre quem grava sem
    ele (a semente local, um adaptador novo) — sem isto a conversa nascia com
    o apelido técnico da integração ("mega" em vez de "Marquezini"). A
    consulta roda num SAVEPOINT: o cadastro de lojas com problema não pode
    derrubar a gravação da conversa (fica o nome da integração).
    """
    if integration is None:
        return None
    try:
        async with session.begin_nested():
            nome = await lojas.nome_da_loja(session, integration)
    except Exception as e:  # noqa: BLE001 — o nome é enfeite; a conversa entra
        logger.warning(
            "atendimento_nome_da_loja_falhou",
            integration_id=str(integration.id),
            erro=type(e).__name__,
        )
        nome = None
    return nome or integration.name


async def _buscar_conversa(
    session: AsyncSession,
    *,
    integration_id,
    plataforma: str,
    canal_nome: str,
    externo_id: str,
    canal_robo_id=None,
) -> AtendimentoConversa | None:
    q = select(AtendimentoConversa).where(
        AtendimentoConversa.canal == canal_nome,
        AtendimentoConversa.externo_id == externo_id,
    )
    if integration_id is None:
        # Sem integração (conta que o e-mail da Amazon não identificou): o
        # UNIQUE não cobre NULL, então a plataforma entra na chave aqui.
        q = q.where(
            AtendimentoConversa.integration_id.is_(None),
            AtendimentoConversa.plataforma == plataforma,
        )
        if canal_robo_id is not None:
            # Loja do ROBÔ (Temu/AliExpress, canal sem integração): a chave é o
            # canal — duas lojas da mesma plataforma podem repetir o id de
            # conversa (`uq_atendimento_conversas_robo`, migration 0347).
            q = q.where(AtendimentoConversa.canal_id == canal_robo_id)
    else:
        q = q.where(AtendimentoConversa.integration_id == integration_id)
    return (await session.execute(q.limit(1))).scalar_one_or_none()


async def upsert_conversa(
    session: AsyncSession,
    *,
    canal: AtendimentoCanal | None,
    integration: Integration | None,
    plataforma: str,
    canal_nome: str,
    externo_id: str,
    conta: str | None = None,
    comprador_id: str | None = None,
    comprador_nome: str | None = None,
    pedido_marketplace: str | None = None,
    anuncio_id: str | None = None,
    anuncio_titulo: str | None = None,
    nao_lidas: int | None = None,
    situacao: str | None = None,
    bloqueio_motivo: str | None = None,
    pode_enviar_ate: datetime | None = None,
    dados: dict | None = None,
    comprador_avatar: str | None = None,
) -> tuple[AtendimentoConversa, bool]:
    """Acha ou cria a conversa de (integração, canal, id externo) → (conversa, criada).

    Campo None NÃO sobrescreve o que já existe: uma leitura parcial (a lista
    de conversas da Shopee não traz o pedido; o detalhe traz) não pode apagar
    o que uma leitura anterior achou. `dados` é MESCLADO (chave a chave).
    `comprador_avatar` passa por `url_avatar` (URL que não serve para `<img>`
    conta como None). Nunca commita (só flush).

    Recalcula a ETIQUETA na conversa nova e quando a leitura muda o que a
    decide (`_o_que_decide_a_etiqueta`) — ou quando ela ainda não tem.
    """
    externo_id = _cabe(sem_nul(str(externo_id)), 191)
    conta = sem_nul(conta)
    comprador_nome = sem_nul(comprador_nome)
    anuncio_titulo = sem_nul(anuncio_titulo)
    bloqueio_motivo = sem_nul(bloqueio_motivo)
    dados = sem_nul(dados)
    integration_id = (
        integration.id if integration is not None else (canal.integration_id if canal else None)
    )
    # Canal sem integração: as lojas do robô (migration 0347) e os canais
    # externos — sites e redes sociais (0362). A chave da conversa é o canal.
    canal_robo_id = canal.id if canal is not None and integration_id is None else None
    campos = {
        "comprador_id": None if comprador_id is None else _cabe(sem_nul(str(comprador_id)), 128),
        "comprador_nome": comprador_nome,
        "comprador_avatar": url_avatar(comprador_avatar),
        "pedido_marketplace": (
            None if pedido_marketplace is None else _cabe(sem_nul(str(pedido_marketplace)), 64)
        ),
        "anuncio_id": None if anuncio_id is None else _cabe(sem_nul(str(anuncio_id)), 64),
        "anuncio_titulo": anuncio_titulo,
        "nao_lidas": nao_lidas,
        "situacao": situacao,
        "bloqueio_motivo": bloqueio_motivo,
        "pode_enviar_ate": _utc(pode_enviar_ate),
    }

    conversa = await _buscar_conversa(
        session,
        integration_id=integration_id,
        plataforma=plataforma,
        canal_nome=canal_nome,
        externo_id=externo_id,
        canal_robo_id=canal_robo_id,
    )
    if conversa is None:
        iniciais = {"nao_lidas": 0, "situacao": CONVERSA_ABERTA}
        iniciais.update({k: v for k, v in campos.items() if v is not None})
        nova = AtendimentoConversa(
            canal_id=canal.id if canal is not None else None,
            integration_id=integration_id,
            plataforma=plataforma,
            canal=canal_nome,
            externo_id=externo_id,
            # Snapshot do nome: a integração pode ser apagada; a conversa fica.
            conta=conta if conta is not None else await _nome_da_loja(session, integration),
            dados=dict(dados or {}),
            **iniciais,
        )
        # O que já estava pendente vai ANTES do SAVEPOINT: dentro dele fica só
        # esta linha, e um rollback do savepoint não leva junto o trabalho alheio.
        await session.flush()
        try:
            # SAVEPOINT: se outro processo criou a mesma conversa no meio, só
            # esta linha é desfeita — o resto da rodada do sync continua.
            async with session.begin_nested():
                session.add(nova)
                await session.flush()
        except IntegrityError:
            conversa = await _buscar_conversa(
                session,
                integration_id=integration_id,
                plataforma=plataforma,
                canal_nome=canal_nome,
                externo_id=externo_id,
                canal_robo_id=canal_robo_id,
            )
            if conversa is None:
                raise
        else:
            await _recalcular_etiqueta(
                session, nova, motivo=MOTIVO_ETIQUETA_LEITURA, travar=False
            )
            return nova, True

    decide_antes = _o_que_decide_a_etiqueta(conversa)
    if canal is not None and conversa.canal_id != canal.id:
        conversa.canal_id = canal.id
    if conta is not None:
        conversa.conta = conta
    elif conversa.conta is None and integration is not None:
        conversa.conta = await _nome_da_loja(session, integration)
    for nome, valor in campos.items():
        if valor is not None:
            setattr(conversa, nome, valor)
    if dados:
        # Dicionário NOVO: mutar o JSONB no lugar não marca a coluna como suja.
        conversa.dados = {**(conversa.dados or {}), **dados}
    if situacao is not None and conversa.ultima_mensagem_em is not None:
        # Situação vinda do adaptador ("aberta" ao desbloquear) precisa
        # voltar a bater com as mensagens: aberta × respondida é derivada.
        recalcular(conversa)
    await session.flush()
    if conversa.etiqueta is None or _o_que_decide_a_etiqueta(conversa) != decide_antes:
        await _recalcular_etiqueta(
            session, conversa, motivo=MOTIVO_ETIQUETA_LEITURA, travar=True
        )
    return conversa, False


# ── Mensagem ──────────────────────────────────────────────────────────────


async def _mensagem_por_externo(
    session: AsyncSession, conversa_id, externo_id: str
) -> AtendimentoMensagem | None:
    return (
        await session.execute(
            select(AtendimentoMensagem)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.externo_id == externo_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _nossa_para_adotar(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    texto: str | None,
    enviada_em: datetime | None,
) -> AtendimentoMensagem | None:
    """A resposta que NÓS mandamos e que o sync está trazendo de volta.

    Linha nossa (pessoa ou IA), ainda sem id da plataforma, em voo / ambígua /
    enviada, com o mesmo texto normalizado, a até 15 min da que chegou. A
    mais antiga ganha: se mandamos a mesma frase duas vezes, cada volta do
    sync adota uma.
    """
    alvo = normalizar_para_comparar(texto)
    if not alvo:
        return None
    referencia = enviada_em or datetime.now(UTC)
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    candidatas = (
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                    AtendimentoMensagem.externo_id.is_(None),
                    AtendimentoMensagem.status.in_(_STATUS_ADOTAVEIS),
                    momento >= referencia - JANELA_ADOCAO,
                    momento <= referencia + JANELA_ADOCAO,
                )
                .order_by(momento.asc())
            )
        )
        .scalars()
        .all()
    )
    for m in candidatas:
        if normalizar_para_comparar(m.texto) == alvo:
            return m
    return None


def sanear_mensagem(
    *,
    externo_id: str | None,
    texto: str | None,
    anexos: list | None,
    payload: dict | None,
) -> tuple[str | None, str | None, list, dict]:
    """A limpeza da porta única para o que vai numa linha de mensagem.

    Sem NUL (o Postgres recusa em TEXT e em JSONB — ver `sem_nul`) e o id
    cortado no tamanho da coluna (`_cabe`). Quem grava mensagem por outro
    caminho — a adoção da prévia do robô (`robo._adotar_previa`) — usa ESTA
    função: sem ela, o NUL de um comprador derrubava a leva inteira ali, em
    toda tentativa (revisão 30/09).
    """
    externo_id = None if externo_id in (None, "") else _cabe(sem_nul(str(externo_id)), 191)
    return externo_id, sem_nul(texto), sem_nul(list(anexos or [])), sem_nul(dict(payload or {}))


def _reabrir_se_nova(conversa: AtendimentoConversa, mensagem: AtendimentoMensagem) -> None:
    """Cliente escreveu DE NOVO depois de a conversa ser fechada: volta para a fila.

    Fechar é "está resolvido"; uma pergunta nova não pode sumir numa conversa
    fechada (é assim que se perde o prazo da Amazon). Idem o "não precisa de
    resposta": vale para o que já estava lá, não para o que chega depois.
    Mensagem ANTIGA chegando atrasada (primeira leitura, reenvio) não reabre.
    """
    anterior = conversa.ultima_do_cliente_em
    if anterior is not None and _quando(mensagem) <= _utc(anterior):
        return
    if conversa.situacao == CONVERSA_FECHADA:
        conversa.situacao = CONVERSA_ABERTA
    conversa.sem_resposta_necessaria = False


async def gravar_mensagem(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    externo_id: str | None,
    autor: str,
    texto: str | None,
    enviada_em: datetime | None,
    tipo: str = "texto",
    anexos: list | None = None,
    payload: dict | None = None,
    origem: str | None = None,
) -> tuple[AtendimentoMensagem, bool]:
    """Grava uma mensagem vinda da plataforma → (mensagem, criada).

    Idempotente por (conversa, externo_id): a mesma mensagem na rodada
    seguinte devolve a linha que já existe, sem mexer nela.

    Origem, quando o adaptador não disser:
      cliente → `cliente`; sistema → `sistema`;
      loja    → ADOTA a nossa linha com o mesmo texto (±15 min), se houver:
                preenche o id externo e confirma `enviada` (devolve criada=False);
                senão é resposta dada fora do DaVinci: `externo`.

    `enviada_em` é o relógio da PLATAFORMA; sem ele, vale a hora em que
    vimos. Recalcula a fila da conversa e aposenta o rascunho pendente quando
    a loja respondeu depois da mensagem que ele responde. Nunca commita.

    Antes de mexer, TRAVA a conversa e a relê do banco (`travar_linha`): a
    rodada do sync carregou a conversa minutos antes, e a pessoa pode tê-la
    fechado (ou marcado "não precisa de resposta") nesse meio. Derivar a fila
    do retrato velho faria a mensagem nova do cliente sumir numa conversa
    fechada. Travar a conversa ANTES da mensagem é também a ordem do envio.
    """
    if autor not in AUTORES:
        raise ValueError(f"autor desconhecido: {autor!r}")
    externo_id, texto, anexos, payload = sanear_mensagem(
        externo_id=externo_id, texto=texto, anexos=anexos, payload=payload
    )
    enviada_em = _utc(enviada_em)

    if externo_id is not None:
        existente = await _mensagem_por_externo(session, conversa.id, externo_id)
        if existente is not None:
            return existente, False

    await travar_linha(session, conversa)

    if origem is None:
        if autor == AUTOR_CLIENTE:
            origem = ORIGEM_CLIENTE
        elif autor in (AUTOR_SISTEMA, AUTOR_MEDIADOR):
            # O mediador (a plataforma na reclamação, 0353) é como o sistema:
            # não é resposta da loja — nada de adotar nem de `externo`.
            origem = ORIGEM_SISTEMA
        else:
            nossa = await _nossa_para_adotar(session, conversa, texto, enviada_em)
            if nossa is not None:
                nossa.externo_id = externo_id
                nossa.status = MSG_ENVIADA
                nossa.erro = None
                if enviada_em is not None:
                    nossa.enviada_em = enviada_em
                if payload:
                    nossa.payload = {**(nossa.payload or {}), "sync": payload}
                recalcular(conversa, [nossa])
                # O envio que adotamos pode ter morrido antes de avaliar a
                # sugestão (deploy no meio): a que ficou pendente não vale mais.
                await _aposentar_rascunho(session, conversa, nossa)
                await session.flush()
                logger.info(
                    "atendimento_mensagem_adotada",
                    conversa_id=str(conversa.id),
                    mensagem_id=str(nossa.id),
                )
                return nossa, False
            origem = ORIGEM_EXTERNO

    mensagem = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=externo_id,
        autor=autor,
        origem=origem,
        tipo=tipo or "texto",
        texto=texto,
        anexos=anexos,
        enviada_em=enviada_em or datetime.now(UTC),
        # Mensagem da loja que chegou pela plataforma JÁ saiu — `enviada`.
        status=MSG_ENVIADA if autor == AUTOR_LOJA else MSG_RECEBIDA,
        payload=payload,
    )
    await session.flush()  # pendências alheias fora do SAVEPOINT (ver upsert_conversa)
    try:
        async with session.begin_nested():
            session.add(mensagem)
            await session.flush()
    except IntegrityError:
        # Outro processo gravou a mesma mensagem no meio (o UNIQUE barrou).
        if externo_id is None:
            raise
        existente = await _mensagem_por_externo(session, conversa.id, externo_id)
        if existente is None:
            raise
        return existente, False

    if autor == AUTOR_CLIENTE:
        _reabrir_se_nova(conversa, mensagem)
    recalcular(conversa, [mensagem])
    if _fechava_a_vez(mensagem):
        # A automática que a régua antiga contava (a figurinha 0007, o cartão
        # da Shopee, a campanha "já segue"): fecha a vez se o comprador só
        # mandou cartão desde a última resposta de pessoa — só o banco sabe.
        await recalcular_conversa(session, conversa)
    if autor == AUTOR_LOJA:
        # Qualquer resposta da loja (por fora ou pelo DaVinci) depois da
        # mensagem que a sugestão responde deixa a sugestão para trás — menos
        # a mensagem automática (o próprio `_aposentar_rascunho` confere).
        await _aposentar_rascunho(session, conversa, mensagem)
    await session.flush()
    if conversa.etiqueta is None:
        # Conversa de antes da etiqueta (ainda sem): ganha a dela na primeira
        # mensagem nova. Já está travada (acima).
        await _recalcular_etiqueta(
            session, conversa, motivo=MOTIVO_ETIQUETA_MENSAGEM, travar=False
        )
    return mensagem, True


# ── Fila e prazo ──────────────────────────────────────────────────────────


def _iso_utc(bruto: object) -> datetime | None:
    """Data ISO guardada em `dados` → UTC; None sem ela ou ilegível. Sem fuso é UTC."""
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    try:
        quando = datetime.fromisoformat(bruto.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(quando)


def prazo_da_plataforma(dados: object) -> datetime | None:
    """O prazo oficial guardado pelo adaptador em `dados` (ISO); None sem ele ou ilegível.

    Data sem fuso é UTC (como o resto da gravação).
    """
    return _iso_utc(dados.get(CHAVE_PRAZO_PLATAFORMA) if isinstance(dados, dict) else None)


def _derivar(conversa: AtendimentoConversa) -> None:
    """aguardando / prazo / situação a partir dos carimbos `ultima_*`.

    Quando a PLATAFORMA diz de quem é a vez (`dados[CHAVE_VEZ_DA_LOJA]` — SAC
    da Magalu: o status do protocolo), vale ela, não quem falou por último: a
    mediação que cobra a loja é `sistema`, e o cliente que fala com a Magalu
    não pede nada à loja.
    """
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    do_cliente = _utc(conversa.ultima_do_cliente_em)
    da_loja = _utc(conversa.ultima_da_loja_em)
    cliente_por_ultimo = do_cliente is not None and (da_loja is None or do_cliente > da_loja)
    vez_desde = None
    if CHAVE_VEZ_DA_LOJA in dados:
        # A vez é da loja desde a leitura; a resposta dada DEPOIS dela (o
        # envio pelo DaVinci) tira da fila até a leitura seguinte decidir.
        vez_desde = _iso_utc(dados.get(CHAVE_VEZ_DA_LOJA))
        vez_da_loja = vez_desde is not None and (da_loja is None or da_loja <= vez_desde)
    else:
        vez_da_loja = cliente_por_ultimo
    # Fechada = "resolvido" por pessoa: sai da fila (e do alerta) até o
    # cliente escrever de novo (`_reabrir_se_nova`). Bloqueada continua na
    # fila: a plataforma não deixa responder por ali, mas alguém precisa agir.
    aguardando = (
        vez_da_loja
        and not conversa.sem_resposta_necessaria
        and conversa.situacao != CONVERSA_FECHADA
    )
    conversa.aguardando_resposta = bool(aguardando)
    prazo = None
    if aguardando:
        # A vez que veio da plataforma sem o cliente por último (a mediação
        # cobrou): o SLA conta da última mensagem.
        inicio = (
            do_cliente
            if cliente_por_ultimo
            else (_utc(conversa.ultima_mensagem_em) or vez_desde or do_cliente)
        )
        prazo = inicio + timedelta(hours=sla_horas(conversa.plataforma, conversa.canal))
        # O prazo que a PRÓPRIA plataforma deu (SAC da Magalu: `due_date` do
        # protocolo) vale mais que o número fixo. LIDO depois da última
        # mensagem do cliente (a mesma leitura), vale mesmo vencido — é a
        # verdade da plataforma. Sem isso, só se for posterior à mensagem: um
        # prazo velho (de antes de o cliente voltar a escrever, ainda não
        # relido) daria "vencida" para quem acabou de chegar.
        oficial = prazo_da_plataforma(dados)
        if oficial is not None:
            lido_em = _iso_utc(dados.get(CHAVE_PRAZO_LIDO_EM))
            fresco = lido_em is not None and (do_cliente is None or lido_em >= do_cliente)
            if fresco or oficial > inicio:
                prazo = oficial
    conversa.prazo_resposta_em = prazo
    if conversa.situacao not in _SITUACOES_FIXAS:
        # "Respondida" é a loja ter falado por último (ou, quando a plataforma
        # diz de quem é a vez, não ser a vez da loja). O "não precisa de
        # resposta" tira da fila (aguardando/prazo acima) mas NÃO vira
        # respondida — ninguém respondeu; a tela mostra o selo pelo campo.
        conversa.situacao = CONVERSA_ABERTA if vez_da_loja else CONVERSA_RESPONDIDA


def recalcular(
    conversa: AtendimentoConversa,
    mensagens_recentes: Iterable[AtendimentoMensagem] = (),
    *,
    fecham_a_vez: Iterable[AtendimentoMensagem] = (),
) -> None:
    """Dobra mensagens novas nos carimbos da conversa e rededuz a fila. Sem banco.

    Só ANDA para frente: mensagem mais velha que a última conhecida não muda
    a última. Resposta da loja que FALHOU não conta como resposta (o cliente
    continua esperando); em voo e ambígua contam — responder de novo por
    cima de uma que pode ter saído é o pior erro, e a tela mostra o
    `revisar`. Para refazer tudo do zero (status mudou, mensagem sumiu), use
    `recalcular_conversa`.

    NOTA INTERNA (`constantes.e_nota`) não conta para nada: não é a última
    mensagem da lista, nem fala do cliente, nem resposta da loja.

    MENSAGEM AUTOMÁTICA (`constantes.e_mensagem_automatica`, a mesma régua da
    métrica de tempo de resposta — 05/10/2026) não é resposta: o robô e as
    campanhas do Duoke (inclusive a "fulano já segue nossa loja aqui" do
    TikTok), a figurinha 0007 da campanha e os cartões `server`/`crm` da
    Shopee (pelo `payload`) e a senha da devolução que o DaVinci manda
    sozinho. Ela continua sendo a última mensagem da lista; só não fecha a
    vez do comprador.

    TURNO SÓ DE CARTÃO (05/10/2026, `constantes.e_cartao_do_comprador`): se,
    desde a última resposta de pessoa, o comprador só mandou o cartão do
    produto/pedido, vale a régua de antes — a automática que ela contava
    (tudo menos o robô do Duoke, `e_resposta_automatica`: a figurinha 0007
    26 h depois, o cartão da Shopee, a campanha "já segue") fecha a vez. Só
    o banco sabe o que o comprador mandou: quem decide é `recalcular_conversa`,
    que passa essa mensagem em `fecham_a_vez` (a mesma instância da lista).
    """
    fecham = tuple(fecham_a_vez)
    for m in mensagens_recentes:
        if e_nota(m.origem, m.tipo):
            continue
        quando = _quando(m)
        ultima = _utc(conversa.ultima_mensagem_em)
        if ultima is None or quando >= ultima:
            conversa.ultima_mensagem_em = quando
            conversa.ultima_mensagem_resumo = resumo(m.texto) or f"[{m.tipo or 'outro'}]"
            conversa.ultima_autor = m.autor
        if m.autor == AUTOR_CLIENTE:
            atual = _utc(conversa.ultima_do_cliente_em)
            if atual is None or quando > atual:
                conversa.ultima_do_cliente_em = quando
        elif (
            m.autor == AUTOR_LOJA
            and m.status != MSG_FALHOU
            # A mensagem automática não responde o comprador (menos a que
            # fecha o turno só de cartão).
            and (
                not e_mensagem_automatica(m.texto, getattr(m, "payload", None))
                or any(m is f for f in fecham)
            )
        ):
            atual = _utc(conversa.ultima_da_loja_em)
            if atual is None or quando > atual:
                conversa.ultima_da_loja_em = quando
    _derivar(conversa)


def _fechava_a_vez(m: AtendimentoMensagem) -> bool:
    """Mensagem automática que a régua ANTIGA contava como resposta? PURA.

    Da loja, não falhou, automática pela régua nova e não pela antiga (o
    robô e as campanhas do Duoke nunca fecharam a vez): a figurinha 0007, os
    cartões `server`/`crm` da Shopee, o cartão de pedido da campanha, a
    campanha "já segue nossa loja" e a senha da devolução. Só ela pode fechar
    o turno só de cartão. A do motor de automações do DaVinci (05/10/2026)
    só nas partes equivalentes às do Duoke que fechavam
    (`constantes.AUTOMACOES_QUE_FECHAM_A_VEZ`): a fila fica igual depois da
    troca. O SQL é o `fechou` de `recalcular_conversa`.
    """
    payload = getattr(m, "payload", None)
    marca = marca_automacao(payload)
    if marca is not None:
        if (marca.get("codigo"), marca.get("parte")) not in AUTOMACOES_QUE_FECHAM_A_VEZ:
            return False
    return (
        m.autor == AUTOR_LOJA
        and m.status != MSG_FALHOU
        and e_mensagem_automatica(m.texto, payload)
        and not e_resposta_automatica(m.texto)
    )


def _marca_fecha_a_vez_sql(payload: Any) -> Any:
    """A parte do `_fechava_a_vez` sobre a marca do motor, em SQL — nunca NULL.

    Sem a marca: vale (decide o resto). Com ela: só as partes de
    `AUTOMACOES_QUE_FECHAM_A_VEZ`.
    """
    tem_marca = func.coalesce(
        func.jsonb_typeof(payload.op("->")(CHAVE_AUTOMACAO)) == "object", false()
    )
    chave = func.concat(
        payload[(CHAVE_AUTOMACAO, "codigo")].astext,
        ":",
        payload[(CHAVE_AUTOMACAO, "parte")].astext,
    )
    return or_(
        ~tem_marca,
        chave.in_(sorted(f"{c}:{p}" for c, p in AUTOMACOES_QUE_FECHAM_A_VEZ)),
    )


async def recalcular_conversa(session: AsyncSession, conversa: AtendimentoConversa) -> None:
    """Refaz os carimbos da conversa do zero, lendo as mensagens do banco.

    Para quem MUDA mensagem que já existia (o envio que virou `falhou`, a
    pessoa que fechou/reabriu, o "não precisa de resposta") — e para a
    mensagem automática nova que a régua antiga contava (`gravar_mensagem`):
    só aqui se sabe se o comprador só mandou cartão. Lê só as linhas que
    importam — a última, a última do cliente, a última resposta de pessoa
    da loja e, no turno só de cartão, a automática que o fecha —, não a
    conversa inteira. Nunca commita.
    """
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    # A nota interna fica de fora (não é a "última mensagem" da conversa).
    base = select(AtendimentoMensagem).where(
        AtendimentoMensagem.conversa_id == conversa.id,
        AtendimentoMensagem.origem != ORIGEM_NOTA,
    )

    async def _uma(q) -> AtendimentoMensagem | None:
        return (await session.execute(q.limit(1))).scalar_one_or_none()

    ultima = await _uma(
        base.order_by(momento.desc(), AtendimentoMensagem.created_at.desc())
    )
    do_cliente = await _uma(
        base.where(AtendimentoMensagem.autor == AUTOR_CLIENTE).order_by(momento.desc())
    )
    # A última resposta da loja que NÃO é mensagem automática — no próprio
    # SQL (`mensagem_automatica_sql`, a gêmea do Python que o `recalcular`
    # usa): a figurinha da campanha e os cartões da Shopee chegam em fileira,
    # e olhar só as N últimas da loja podia não achar a da pessoa.
    da_loja = await _uma(
        base.where(
            AtendimentoMensagem.autor == AUTOR_LOJA,
            AtendimentoMensagem.status != MSG_FALHOU,
            ~mensagem_automatica_sql(AtendimentoMensagem.texto, AtendimentoMensagem.payload),
        ).order_by(momento.desc(), AtendimentoMensagem.created_at.desc())
    )
    # TURNO SÓ DE CARTÃO (`recalcular`): depois da resposta de pessoa, a
    # última automática que a régua antiga contava (`_fechava_a_vez`, no
    # SQL) ANTES da primeira mensagem do comprador que não é cartão — o que
    # veio depois de ele escrever não fecha nada. É o mesmo que a gravação
    # faz mensagem a mensagem.
    escreveu = base.where(
        AtendimentoMensagem.autor == AUTOR_CLIENTE,
        ~cartao_do_comprador_sql(AtendimentoMensagem.payload),
    )
    fechou = base.where(
        AtendimentoMensagem.autor == AUTOR_LOJA,
        AtendimentoMensagem.status != MSG_FALHOU,
        mensagem_automatica_sql(AtendimentoMensagem.texto, AtendimentoMensagem.payload),
        ~resposta_automatica_sql(AtendimentoMensagem.texto),
        _marca_fecha_a_vez_sql(AtendimentoMensagem.payload),
    )
    if da_loja is not None:
        escreveu = escreveu.where(momento > _quando(da_loja))
        fechou = fechou.where(momento > _quando(da_loja))
    primeira_escrita = await _uma(
        escreveu.order_by(momento.asc(), AtendimentoMensagem.created_at.asc())
    )
    if primeira_escrita is not None:
        fechou = fechou.where(momento < _quando(primeira_escrita))
    so_cartao = await _uma(fechou.order_by(momento.desc(), AtendimentoMensagem.created_at.desc()))

    conversa.ultima_mensagem_em = None
    conversa.ultima_mensagem_resumo = None
    conversa.ultima_autor = None
    conversa.ultima_do_cliente_em = None
    conversa.ultima_da_loja_em = None
    # A última por último: em empate de horário, é ela que dá o resumo.
    recalcular(
        conversa,
        [m for m in (do_cliente, da_loja, so_cartao, ultima) if m is not None],
        fecham_a_vez=() if so_cartao is None else (so_cartao,),
    )
    if da_loja is not None:
        await _aposentar_rascunho(session, conversa, da_loja)
    await session.flush()


async def _aposentar_rascunho(
    session: AsyncSession, conversa: AtendimentoConversa, resposta: AtendimentoMensagem
) -> None:
    """A loja respondeu DEPOIS da mensagem que a sugestão responde: ela vira `substituido`.

    Sem isto, a caixa de resposta mostraria uma sugestão para uma pergunta
    que já foi respondida — e alguém poderia enviá-la por cima (o comprador
    recebe duas respostas).

    A comparação é com o GATILHO da sugestão (a mensagem do cliente), não com
    a hora em que ela foi criada: a IA escreve minutos depois (90 s de
    silêncio + a rodada do sync), e o Duoke pode ter respondido nesse meio —
    a resposta dele é "mais velha" que a sugestão, mas é posterior à
    pergunta. Resposta que falhou não conta (o cliente continua esperando).

    Mensagem automática (`constantes.e_mensagem_automatica`, 05/10/2026)
    também não: ela não responde o comprador — a conversa continua em
    "Falta responder", e a sugestão continua valendo para a pessoa. Sem
    isto, a figurinha 0007 da campanha (26 h depois) aposentava a sugestão:
    em copiloto a IA escrevia de novo (o modelo pago duas vezes) e, em
    observação, a avaliação virava "escreveu do zero" com "[Figurinha]".
    Nem a que fecha o turno só de cartão: o comprador não perguntou nada, e
    a sugestão continua lá se ele escrever.
    """
    if resposta.status == MSG_FALHOU or e_mensagem_automatica(
        resposta.texto, getattr(resposta, "payload", None)
    ):
        return
    rascunho = (
        await session.execute(
            select(AtendimentoRascunho)
            .where(
                AtendimentoRascunho.conversa_id == conversa.id,
                AtendimentoRascunho.status == RASCUNHO_PENDENTE,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if rascunho is None:
        return
    gatilho = (
        await session.get(AtendimentoMensagem, rascunho.mensagem_gatilho_id)
        if rascunho.mensagem_gatilho_id is not None
        else None
    )
    if gatilho is not None:
        referencia = _quando(gatilho)
    else:
        # Sem gatilho (a mensagem sumiu): vale o nosso relógio.
        referencia = _utc(await _atributo(session, rascunho, "created_at"))
    # Mesmo segundo conta como depois: o relógio da Shopee é em segundos, e
    # deixar uma sugestão velha na caixa é o erro caro (resposta em dobro).
    if _quando(resposta) >= referencia:
        rascunho.status = RASCUNHO_SUBSTITUIDO
        logger.info(
            "atendimento_rascunho_substituido",
            conversa_id=str(conversa.id),
            rascunho_id=str(rascunho.id),
            mensagem_id=str(resposta.id),
        )
        if resposta.origem not in ORIGENS_DAVINCI:
            await _promover_observacao(session, rascunho, resposta)


async def _promover_observacao(
    session: AsyncSession, rascunho: AtendimentoRascunho, resposta: AtendimentoMensagem
) -> None:
    """A nota do modo observação ganha a ação de verdade quando a loja responde POR FORA.

    No teste em observação a pessoa dá 👍/👎 na sugestão ainda pendente
    (ação provisória `observou`) e quem responde é o Duoke. Quando a
    resposta dele chega, a sugestão vira `substituido` — e a avaliação
    passa a `escreveu_do_zero`, com a resposta real e a similaridade: é a
    comparação IA × equipe. Sem isto a linha ficava `observou` para sempre
    (a tela esconde o 👍 já dado, ninguém reenviava a nota) e a métrica
    perdia justamente os casos do teste. A nota e a correção ficam.

    Só resposta de FORA: a que saiu pelo DaVinci é avaliada pelo `enviar`
    (enviou igual/editou, com o que a pessoa escolheu) — promover aqui, na
    adoção pelo sync, tomaria o lugar da avaliação certa. E só de PESSOA: a
    mensagem automática não é "a equipe escreveu do zero" (a comparação IA ×
    equipe registraria uma figurinha) — a mesma régua do `_aposentar_rascunho`.
    """
    if e_mensagem_automatica(resposta.texto, getattr(resposta, "payload", None)):
        return
    av = (
        await session.execute(
            select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == rascunho.id)
        )
    ).scalar_one_or_none()
    if av is None or av.acao != AVALIACAO_OBSERVOU:
        return
    av.acao = ACAO_SUBSTITUIDO
    av.texto_final = resposta.texto
    av.similaridade = (
        similaridade(resposta.texto, rascunho.texto) if resposta.texto is not None else None
    )
    logger.info(
        "atendimento_avaliacao_promovida",
        rascunho_id=str(rascunho.id),
        mensagem_id=str(resposta.id),
    )
