"""Zap (WhatsApp Cloud API) — o ADAPTADOR de saída (RF10, Onda 2: responder TEXTO).

Desenho: `docs/zap/desenho.md` §6 (envio): as chaves (§6.1), o desvio no
caminho único (§6.2), a ordem das travas (§6.3, em `enviar._destino_zap`), o
adaptador (§6.4) e os erros (§6.5, em `zap_erros`).

O Zap NÃO entra em `enviar.ADAPTADORES` (de propósito): as automações e a
foto genérica nunca o pegam por engano. Quem chega aqui é SÓ a resposta de
texto de uma PESSOA, pelo `enviar.enviar_resposta` (a linha em voo, o índice
"uma em voo por conversa", a régua enviada/revisar/falhou e o freio único são
os de sempre). O `_destino_zap` confere as 14 travas e monta o `ZapDestino`
(o número, a conta, o contato e o token decifrado SÓ na memória); o
`enviar_texto` daqui confere de novo o que não pode falhar (cinto: as duas
chaves, a lista de teste e a janela) e faz o `POST /{pnid}/messages`.

Nada aqui grava no banco: o efeito de um erro (pausar o número, marcar o
token, fechar a janela) volta descrito em `ResultadoEnvio.payload['zap']
['efeito']` e o `enviar_resposta` o aplica DEPOIS de gravar o resultado
(`zap_erros.aplicar_efeito`). Texto, telefone e token nunca vão para o log.

Marcar como LIDA na Meta (o tique azul, decisão do Eduardo de 26/10/2026)
mora aqui também (`marcar_lida`), atrás da chave `atendimento_zap_marcar_lida`
(desligada) e das mesmas chaves do envio — quem chama é o `lida.py`.

FOTO (Onda 3, 27/10/2026 — §6.8): `enviar_imagem` sobe a foto (`POST
/{pnid}/media`) e manda a mensagem `type=image` com a legenda OPCIONAL (já
passada pelo validador no `enviar._preparar_foto`). Chega aqui pelo
`enviar.enviar_foto`, com as MESMAS travas do texto (`_destino_zap`) e o
mesmo cinto. O upload que falha não mandou nada a ninguém (`falhou`, nunca
ambíguo); a mensagem segue a régua do texto. A nossa cópia (o balão) já está
no disco antes daqui (`zap_midia.guardar_saida`): o WhatsApp não devolve URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoZapConta,
    AtendimentoZapContato,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
)
from app.services.atendimento import zap_api, zap_contas, zap_erros, zap_midia
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    MSG_ENVIADA,
    MSG_ENVIANDO,
    MSG_REVISAR,
    ORIGENS_DAVINCI,
    ResultadoEnvio,
)

logger = structlog.get_logger()

_SP = ZoneInfo("America/Sao_Paulo")

# A janela do WhatsApp (resposta livre) e a margem do servidor: a mensagem
# sai com 60 s de folga — o relógio da Meta manda, e um envio no último
# segundo voltaria 131047.
JANELA_24H = timedelta(hours=24)
MARGEM_JANELA = timedelta(seconds=60)
# Contra laço (trava 13): envios ao MESMO contato nesta janela.
JANELA_LACO = timedelta(minutes=10)
# Etapas em que o número pode mandar (trava 7): o número de teste da Meta e
# o número já migrado. Em `preparando`/`aguardando_duoke`/`migrando` quem
# responde ainda é o Duoke; `pausado`/`desligado` falam por si.
ETAPAS_QUE_ENVIAM = ("teste", "ativo")
# O que conta no teto do dia (trava 14): o que SAIU ou pode ter saído.
_STATUS_QUE_CONTAM_NO_TETO = (MSG_ENVIANDO, MSG_ENVIADA, MSG_REVISAR)
# A Meta só aceita marcar como lida até 30 dias depois de receber.
LIDA_VALE_DIAS = 30
# O prefixo do wamid que o script local inventa (`scripts/atendimento_zap_local.py`).
WAMID_SIMULADO = "wamid.LOCAL"
# Conta com estes status não manda (trava 9).
_CONTA_SEM_CREDENCIAL = ("token_invalido", "sem_permissao")

# O erro gravado na mensagem quando o CINTO do adaptador barrou (nada saiu).
ERRO_CINTO = "zap_cinto"


def _agora() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ZapDestino:
    """Por onde a resposta do Zap sai — montado pelo `enviar._destino_zap`.

    O token vive SÓ na memória do processo e fica fora do `repr`: um
    `logger.info(destino=…)` ou uma exceção não o levam no texto (o Sentry
    já não guarda variáveis locais, mas o log não tem essa proteção).
    """

    numero_id: UUID
    phone_number_id: str
    conta_id: UUID
    contato_id: UUID
    # O `wa_id` do contato COMO VEIO (é o `to`); a lista de teste compara
    # pelo normalizado.
    to: str
    # O fim da janela de 24 h, já com a margem (§6.3, trava 12).
    janela_ate: datetime
    # A lista de teste (global + número), normalizada; vazia = sem lista.
    destinos_teste: frozenset[str]
    producao: bool
    token: str = field(repr=False)


# ── As réguas (puras) ─────────────────────────────────────────────────────


def destinos_teste_globais() -> frozenset[str]:
    """`ATENDIMENTO_ZAP_DESTINOS_TESTE` (wa_ids por vírgula), normalizados."""
    bruto = get_settings().atendimento_zap_destinos_teste or ""
    return frozenset(
        n for n in (zap_contas.normalizar_wa_id(p) for p in bruto.split(",") if p.strip()) if n
    )


def lista_de_teste(numero: AtendimentoZapNumero) -> frozenset[str]:
    """A lista de teste global SOMADA à do número, normalizada (com e sem o 9º dígito
    caem no mesmo). Ela só RESTRINGE: nenhuma lista religa uma chave desligada."""
    do_numero = frozenset(
        n for n in (zap_contas.normalizar_wa_id(p) for p in (numero.destinos_teste or [])) if n
    )
    return destinos_teste_globais() | do_numero


def destino_liberado(to: str | None, lista: frozenset[str], *, producao: bool) -> bool:
    """O `to` pode receber? Com lista: só quem está nela. Sem lista: só em produção.

    Fail-closed fora de produção (§6.1): no ambiente local nada barra o
    `graph.facebook.com` — sem lista de teste preenchida, o Zap não manda.
    """
    norm = zap_contas.normalizar_wa_id(to)
    if norm is None:
        return False
    if lista:
        return norm in lista
    return producao


def fim_da_janela(
    ultima_recebida_em: datetime | None, pode_enviar_ate: datetime | None
) -> datetime | None:
    """O último instante em que dá para responder (trava 12). None = nunca abriu.

    UMA fonte só: `min(contato.ultima_recebida_em + 24 h, conversa.pode_enviar_ate)
    − 60 s`. O 131047 fecha `conversa.pode_enviar_ate` (`zap_erros`, §5.6):
    sem o `min`, a trava continuaria deixando enviar e a Meta devolveria
    131047 a cada tentativa. Um fechamento ANTERIOR à última mensagem do
    cliente não vale (revisão, 26/10/2026): ela reabriu a janela — o status
    da falha pode ser processado depois dela (adiado ou reentregue).
    """
    if ultima_recebida_em is None:
        return None
    recebida = _utc(ultima_recebida_em)
    fim = recebida + JANELA_24H
    if pode_enviar_ate is not None and _utc(pode_enviar_ate) >= recebida:
        fim = min(fim, _utc(pode_enviar_ate))
    return fim - MARGEM_JANELA


def _utc(quando: datetime) -> datetime:
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def teto_do_numero(numero: AtendimentoZapNumero) -> int:
    """O teto de envios por dia DESTE número (o padrão quando não tem o próprio)."""
    if numero.teto_dia is not None and numero.teto_dia >= 0:
        return int(numero.teto_dia)
    return int(get_settings().atendimento_zap_max_dia_numero)


def corpo_texto(to: str, texto: str, *, mensagem_id: UUID | None) -> dict[str, Any]:
    """O corpo do `POST /{pnid}/messages` (§6.4).

    `preview_url: false`: o WhatsApp não monta a prévia de link que a pessoa
    não viu. `biz_opaque_callback_data` = o id da NOSSA mensagem: volta em
    todo status (§5.6) e concilia o envio ambíguo sem depender do wamid.
    """
    corpo: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": texto, "preview_url": False},
    }
    if mensagem_id is not None:
        corpo["biz_opaque_callback_data"] = str(mensagem_id)
    return corpo


def corpo_imagem(
    to: str, media_id: str, legenda: str | None, *, mensagem_id: UUID | None
) -> dict[str, Any]:
    """O corpo do `POST /{pnid}/messages` da FOTO (§6.8): o id da mídia subida e a legenda.

    Sem legenda, a chave `caption` nem vai (a foto sai sozinha).
    """
    imagem: dict[str, Any] = {"id": media_id}
    if legenda:
        imagem["caption"] = legenda
    corpo: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": imagem,
    }
    if mensagem_id is not None:
        corpo["biz_opaque_callback_data"] = str(mensagem_id)
    return corpo


# ── Leituras do banco (as travas) ─────────────────────────────────────────


async def numero_do_pnid(session: AsyncSession, pnid: str | None) -> AtendimentoZapNumero | None:
    """O número RELIDO do banco (`populate_existing`): o "pausar" vale na hora."""
    if not pnid:
        return None
    return (
        await session.execute(
            select(AtendimentoZapNumero)
            .where(AtendimentoZapNumero.phone_number_id == str(pnid))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def conta_do_numero(
    session: AsyncSession, numero: AtendimentoZapNumero
) -> AtendimentoZapConta | None:
    return await session.get(AtendimentoZapConta, numero.conta_ref, populate_existing=True)


def token_valido(conta: AtendimentoZapConta | None) -> str | None:
    """O token decifrado — ou None (sem conta, token caído ou que não decifra).

    Nunca levanta e nunca loga o token: a trava 9 só precisa saber se há.
    """
    if conta is None or conta.status in _CONTA_SEM_CREDENCIAL:
        return None
    try:
        return zap_contas.token_da_conta(conta)
    except Exception as exc:  # noqa: BLE001 — cifra trocada, token vazio: sem credencial
        logger.warning("zap_token_indisponivel", conta=str(conta.id), err=type(exc).__name__)
        return None


async def contato_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoZapContato | None:
    """O contato pelo `externo_id` da conversa (= o id do contato, premissa P3)."""
    try:
        contato_id = UUID(str(conversa.externo_id))
    except (TypeError, ValueError):
        return None
    return await session.get(AtendimentoZapContato, contato_id, populate_existing=True)


async def envios_ao_contato(session: AsyncSession, conversa_id: UUID, agora: datetime) -> int:
    """Envios do DaVinci a esta conversa (= este contato) nos últimos 10 min.

    Conta TODA tentativa (inclusive a que falhou): o que a trava pega é o
    defeito em laço, que martelaria a Meta com ou sem sucesso.
    """
    return int(
        await session.scalar(
            select(func.count(AtendimentoMensagem.id)).where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.autor == AUTOR_LOJA,
                AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                AtendimentoMensagem.created_at >= agora - JANELA_LACO,
            )
        )
        or 0
    )


def inicio_do_dia(agora: datetime) -> datetime:
    """A meia-noite de HOJE em São Paulo (o "dia" do teto é o da equipe)."""
    local = agora.astimezone(_SP)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def envios_do_numero_hoje(session: AsyncSession, canal_id: UUID, agora: datetime) -> int:
    """O que o número mandou HOJE (saiu ou pode ter saído) — o teto do dia."""
    return int(
        await session.scalar(
            select(func.count(AtendimentoMensagem.id))
            .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoMensagem.conversa_id)
            .where(
                AtendimentoConversa.canal_id == canal_id,
                AtendimentoMensagem.autor == AUTOR_LOJA,
                AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                AtendimentoMensagem.status.in_(_STATUS_QUE_CONTAM_NO_TETO),
                AtendimentoMensagem.created_at >= inicio_do_dia(agora),
            )
        )
        or 0
    )


# ── O envio ───────────────────────────────────────────────────────────────


def _cinto(destino: ZapDestino, agora: datetime) -> str | None:
    """O que o adaptador confere DE NOVO (§6.4). None = pode; senão, o porquê."""
    s = get_settings()
    if not (s.atendimento_envio_ativo and s.atendimento_zap_envio_ativo):
        return "envio_desligado"
    if not destino_liberado(destino.to, destino.destinos_teste, producao=destino.producao):
        return "fora_dos_testes"
    if agora > destino.janela_ate:
        return "fora_da_janela"
    return None


async def enviar_texto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    destino: ZapDestino,
    texto: str,
    *,
    mensagem_id: UUID | None,
) -> ResultadoEnvio:
    """`POST /{pnid}/messages` com o texto. Nunca levanta (o contrato `ResultadoEnvio`).

      200 com `messages[0].id`  → ok, `externo_id` = wamid, `payload.zap.message_status`
                                  (vai parar em `payload.envio.zap` da mensagem;
                                  `held_for_quality_assessment` = segurada pela Meta);
      200 sem wamid             → ambíguo (pode ter saído);
      erro com código           → `falhou` (`erro="meta:<code>"`) com o efeito da
                                  tabela em `payload.zap.efeito`; os "erros
                                  desconhecidos" da tabela → ambíguo;
      timeout, 5xx/3xx sem código, conexão cortada → ambíguo: `revisar`, nunca
                                  retentado (a conciliação é pelo status, §5.6).

    `session` não é usada (o adaptador não grava nada); fica pela assinatura
    dos adaptadores (`enviar._chamar_plataforma`).
    """
    del session  # o adaptador não grava: o efeito volta no resultado
    agora = _agora()
    log = {"conversa_id": str(conversa.id), "numero": str(destino.numero_id)}
    barrou = _cinto(destino, agora)
    if barrou is not None:
        # O `_destino_zap` já conferiu: chegar aqui é a chave/lista/janela que
        # mudou no meio (ou um caminho novo que pulou as travas). Nada sai.
        logger.error("zap_envio_cinto", motivo=barrou, **log)
        return ResultadoEnvio(ok=False, erro=f"{ERRO_CINTO}:{barrou}")
    corpo = corpo_texto(destino.to, texto, mensagem_id=mensagem_id)
    return await _postar(destino, corpo, log)


async def _postar(
    destino: ZapDestino, corpo: dict[str, Any], log: dict[str, str]
) -> ResultadoEnvio:
    """O `POST /{pnid}/messages` e a régua do resultado (texto e foto). Nunca levanta."""
    try:
        resposta = await zap_api.enviar_mensagem(destino.phone_number_id, destino.token, corpo)
    except zap_api.ZapApiErro as exc:
        if zap_api.eh_ambiguo(exc):
            logger.warning("zap_envio_ambiguo", code=exc.code, http=exc.http_status, **log)
            return ResultadoEnvio(
                ok=False,
                ambiguo=True,
                erro=f"meta:{exc.code}" if exc.code is not None else "meta:sem_resposta",
                payload={"zap": {"code": exc.code, "http": exc.http_status}},
            )
        info = zap_erros.traduzir(exc.code)
        logger.warning(
            "zap_envio_recusado", code=exc.code, subcode=exc.subcode, classe=info.classe, **log
        )
        return ResultadoEnvio(
            ok=False,
            erro=f"meta:{exc.code}" if exc.code is not None else f"meta:http_{exc.http_status}",
            payload={
                "zap": {
                    "code": exc.code,
                    "subcode": exc.subcode,
                    "classe": info.classe,
                    "efeito": info.efeito,
                }
            },
        )
    mensagens = resposta.get("messages")
    primeira = (
        mensagens[0]
        if isinstance(mensagens, list) and mensagens and isinstance(mensagens[0], dict)
        else {}
    )
    wamid = str(primeira.get("id") or "").strip()
    if not wamid:
        # A Meta respondeu 200 sem o id: não dá para dizer que saiu nem que não.
        logger.warning("zap_envio_sem_wamid", **log)
        return ResultadoEnvio(ok=False, ambiguo=True, erro="meta:sem_wamid", payload={"zap": {}})
    situacao = primeira.get("message_status")
    zap: dict[str, Any] = {"message_status": str(situacao)[:40] if situacao else None}
    logger.info("zap_envio_ok", message_status=zap["message_status"], **log)
    return ResultadoEnvio(ok=True, externo_id=wamid[:191], payload={"zap": zap})


async def enviar_imagem(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    destino: ZapDestino,
    copia: zap_midia.CopiaDeSaida,
    legenda: str | None,
    *,
    mensagem_id: UUID | None,
) -> ResultadoEnvio:
    """A FOTO (§6.8): `POST /{pnid}/media` e depois a mensagem `type=image`. Nunca levanta.

      upload recusado/sem resposta → `falhou` (nada chegou ao cliente: a
                                     mensagem nem foi pedida); com código da
                                     Meta, `erro="meta:<code>"` e o efeito da
                                     tabela (o 190 derruba o token igual);
      mensagem                     → a régua do texto (`_postar`): o wamid, o
                                     ambíguo, o erro com efeito.
    O `payload` leva o id da mídia na Meta (`zap.media_id`) e a URL RELATIVA
    da nossa cópia (`imagem_url`), que o `enviar._aplicar_resultado` põe no
    balão. `session` não é usada (o adaptador não grava).
    """
    del session
    agora = _agora()
    log = {"conversa_id": str(conversa.id), "numero": str(destino.numero_id)}
    barrou = _cinto(destino, agora)
    if barrou is not None:
        logger.error("zap_envio_cinto", motivo=barrou, foto=True, **log)
        return ResultadoEnvio(ok=False, erro=f"{ERRO_CINTO}:{barrou}")
    try:
        media_id = await zap_api.subir_midia(
            destino.phone_number_id,
            destino.token,
            nome=copia.nome,
            dados=copia.dados,
            mime=copia.mime,
        )
    except zap_api.ZapApiErro as exc:
        logger.warning("zap_foto_upload_falhou", code=exc.code, http=exc.http_status, **log)
        if exc.code is not None:
            info = zap_erros.traduzir(exc.code)
            return ResultadoEnvio(
                ok=False,
                erro=f"meta:{exc.code}",
                payload={
                    "zap": {
                        "code": exc.code,
                        "subcode": exc.subcode,
                        "classe": info.classe,
                        "efeito": info.efeito,
                        "etapa": "upload",
                    }
                },
            )
        motivo = f"http_{exc.http_status}" if exc.http_status else "sem_resposta"
        return ResultadoEnvio(
            ok=False,
            erro=f"zap_upload:{motivo}",
            payload={"zap": {"http": exc.http_status, "etapa": "upload"}},
        )
    corpo = corpo_imagem(destino.to, media_id, legenda, mensagem_id=mensagem_id)
    resultado = await _postar(destino, corpo, log)
    zap = dict((resultado.payload or {}).get("zap") or {})
    zap["media_id"] = media_id[:64]
    resultado.payload = {**(resultado.payload or {}), "zap": zap, "imagem_url": copia.url}
    return resultado


async def depois_do_envio(
    session: AsyncSession,
    destino: ZapDestino,
    *,
    conversa_id: UUID,
    resultado: ResultadoEnvio,
    midia_id: UUID | None = None,
) -> None:
    """Depois de gravar o resultado: o "último envio" do número e o EFEITO do erro.

    O efeito vem da resposta da NOSSA chamada (pelo TLS do graph.facebook.com):
    vale na primeira vez (§5.6 item 4 — o do webhook espera 2 ocorrências).
    Foto (Onda 3): a nossa cópia ganha o id da mídia na Meta (`media_id`).
    Não commita (o `enviar._depois_do_zap` commita numa transação própria).
    """
    agora = _agora()
    if midia_id is not None:
        media_id = ((resultado.payload or {}).get("zap") or {}).get("media_id")
        midia = await session.get(AtendimentoZapMidia, midia_id)
        if midia is not None and isinstance(media_id, str) and media_id:
            midia.media_id = media_id[:64]
    if resultado.ok:
        numero = await session.get(AtendimentoZapNumero, destino.numero_id, populate_existing=True)
        if numero is not None:
            numero.ultimo_enviado_em = agora
            if numero.saude_status == "erro":
                # O número voltou a mandar: o erro da Meta (pagamento, não
                # registrado) passou — a barra volta a `ok` no próximo evento
                # (`zap_entrada._saude_ok`). Restrito não: só a Meta reabilita.
                numero.saude_status = "ok"
                numero.saude = {
                    k: v
                    for k, v in (numero.saude or {}).items()
                    if k not in ("erro_meta", "pausado_pela_meta")
                }
    dados = (resultado.payload or {}).get("zap") if not resultado.ok else None
    efeito = dados.get("efeito") if isinstance(dados, dict) else None
    if efeito:
        code = dados.get("code")
        await zap_erros.aplicar_efeito(
            session,
            efeito,
            numero_id=destino.numero_id,
            conta_id=destino.conta_id,
            conversa_id=conversa_id,
            contato_id=destino.contato_id,
            code=code if isinstance(code, int) else None,
            origem="api",
            agora=agora,
        )
    await session.flush()


# ── Marcar como lida (o tique azul, §6.7) ─────────────────────────────────


async def ultima_de_entrada(
    session: AsyncSession, conversa: AtendimentoConversa, agora: datetime
) -> str | None:
    """O wamid da última mensagem DO CLIENTE (com id da Meta) dos últimos 30 dias.

    Fora o "oi" SIMULADO pelo script local (`wamid.LOCAL…`, revisão de
    26/10/2026): a Meta não conhece esse id, e o ensaio do §11.5 com o tique
    azul ligado mandaria um `message_id` inventado a cada abertura.
    """
    wamid = await session.scalar(
        select(AtendimentoMensagem.externo_id)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor == AUTOR_CLIENTE,
            AtendimentoMensagem.externo_id.like("wamid.%"),
            AtendimentoMensagem.externo_id.not_like(f"{WAMID_SIMULADO}%"),
            func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
            >= agora - timedelta(days=LIDA_VALE_DIAS),
        )
        .order_by(
            func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at).desc()
        )
        .limit(1)
    )
    return str(wamid) if wamid else None


async def marcar_lida(phone_number_id: str, token: str, wamid: str) -> str | None:
    """Marca na Meta. None = marcou; senão, o erro curto (sem token, sem telefone)."""
    try:
        ok = await zap_api.marcar_lida(phone_number_id, token, wamid)
    except zap_api.ZapApiErro as exc:
        return f"meta:{exc.code}" if exc.code is not None else "meta:sem_resposta"
    return None if ok else "meta:sem_sucesso"
