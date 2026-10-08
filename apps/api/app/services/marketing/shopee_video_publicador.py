"""Publicador da Shopee Vídeo — uma máquina de estados RETOMÁVEL.

Na Meta e no YouTube a publicação cabe num tique do worker. Na Shopee não:
depois do upload ela transcodifica (sem prazo publicado), e a lista de capas
só aparece ~1 min depois disso. Segurar o worker esperando seria segurar a
fila de todas as contas. Então cada tique AVANÇA o que der e grava onde
parou, e a linha volta pra `pendente` ("aguardando a Shopee") até o próximo:

    subir → processando → capa → rascunho → postar → publicado

O progresso mora em `marketing_postagens.opcoes["shopee"]["progresso"]`, e o
`video_upload_id` vai pro `container_id` ANTES do primeiro byte subir — é a
mesma regra do Instagram: se o processo morrer no meio, o reconciliador sabe
que havia algo em voo e PERGUNTA, em vez de subir de novo.

Por que isto é seguro contra post duplicado: um upload vira um rascunho, e
um rascunho vira no máximo UM post — `post_video` duas vezes no mesmo
`video_upload_id` não duplica. O único jeito de duplicar seria subir o
arquivo DE NOVO depois de o post talvez ter saído; por isso, a partir do
instante em que `post_video` é chamado (`post_chamado_em`, gravado antes da
chamada), a linha nunca mais volta pro começo: ou confirma (publicado), ou
fica em `revisar` pro reconciliador, que pergunta à Shopee e, se o rascunho
continua lá, devolve a linha pra postar O MESMO rascunho.

Três cintos contra o post em dobro, além desse:

  • a CERCA do lease: toda escrita da máquina leva `status` e `claimed_at`
    lidos no começo do tique. Se outro tique (ou o reconciliador) assumiu a
    linha, a escrita não pega e este tique para — e o carimbo de "vou
    postar" é escrito assim ANTES de chamar a Shopee;
  • só recusa EXPLÍCITA da Shopee (`error` preenchido, ou o vídeo na lista
    de falha) marca o post como "não saiu". Resposta sem `post_id`, sem JSON,
    erro do servidor dela ou falha na conferência: `revisar`;
  • `_aguardar` e `_falhou` nunca devolvem pra fila (nem zeram o upload) uma
    linha cujo post pode ter saído — desviam pra `revisar`.

Erro que só gente resolve (Termos do Shopee Vídeo não aceitos, conta fora da
lista da API — "toggle" —, autorização desfeita, app errado) vira `falhou`
com a frase em português e PARA A CONTA (`bloqueado`/`expirado` na linha do
token): o robô não agenda mais nada ali até alguém liberar. O rascunho já
pronto fica na Shopee e o "tentar de novo" desta linha só o publica.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MarketingCreative, MarketingPostagem, RedeSocial
from app.models.marketing_postagem import (
    STATUS_FALHOU,
    STATUS_PENDENTE,
    STATUS_PUBLICADO,
    STATUS_PUBLICANDO,
    STATUS_REVISAR,
)
from app.services.marketing import shopee_video_anuncio as anuncio_svc
from app.services.marketing import shopee_video_conta as conta_svc
from app.services.marketing.shopee_video import (
    LEGENDA_MAX,
    STATUS_APAGADO,
    STATUS_POSTADO,
    STATUS_RASCUNHO,
    ClienteShopeeVideo,
    ShopeeVideoError,
    ShopeeVideoRedeError,
    partes_do_arquivo,
    tamanho_shopee,
)

logger = structlog.get_logger()

# Etapas (o que falta fazer).
SUBIR = "subir"
ENVIANDO = "enviando"
PROCESSANDO = "processando"
CAPA = "capa"
RASCUNHO = "rascunho"
POSTAR = "postar"

# Quanto um tique espera a Shopee antes de devolver a linha pra fila. Curto
# de propósito: a máquina é retomável e o tique roda todo minuto — esperar
# mais aqui só seguraria quem vem atrás no mesmo tique e empurraria o job
# pro timeout do cron (1200 s).
ORCAMENTO_TICK_S = 50.0
POLL_PROCESSANDO_S = 10.0
# "upload video less than 1 min": as capas só saem 1 min depois do pronto.
ESPERA_CAPA_S = 65.0
POLL_CAPA_S = 20.0
# Folga mínima no tique pra COMEÇAR a subir um arquivo (o envio em si não
# para no meio por causa do orçamento).
MIN_SOBRA_SUBIR_S = 20.0
# Teto do vídeo parado na Shopee sem ficar pronto (transcodificação + capas).
TETO_PROCESSAMENTO = timedelta(hours=2)
# "can not find video meta" com o upload já SUCCEEDED: a Shopee ainda está
# preparando o vídeo pro rascunho. Passado isto, não vai mais.
TETO_META = timedelta(minutes=30)
# A conferência ao vivo do anúncio (status + estoque, pelo app da LOJA) vale
# por este tempo antes do rascunho — sem isto o "esperando a Shopee" de cada
# minuto faria duas chamadas à API da loja por minuto. Antes do POST ela é
# SEMPRE refeita.
VALIDADE_CONFERENCIA = timedelta(minutes=10)
# O reconciliador só mexe numa linha `publicando` quando o tique que a pegou
# com certeza morreu: o cron do publicador tem timeout de 1200 s.
LEASE_MORTO = timedelta(minutes=25)
# Falhas passageiras seguidas (rede, loja fora do ar) antes de desistir.
MAX_FALHAS_SEGUIDAS = 5
# Quantas vezes UMA tentativa pode descartar o upload e subir o arquivo de
# novo ("avoid uploading the same video multiple times", diz a doc).
MAX_RESUBIDAS = 1

# Ações (o que o worker conta).
PUBLICADO = "publicado"
AGUARDANDO = "aguardando"
FALHOU = "falhou"
REVISAR = "revisar"

# Erros que só uma pessoa resolve: trechos → (frase pra tela, autorizar de
# novo?). `True` vira `expirado` na conta (a tela pede autorizar de novo); o
# resto vira `bloqueado` (alguém resolve fora e libera a conta).
_PRECISA_GENTE: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (
        ("copyright_not_agree", "not agree"),
        "a loja ainda não aceitou os Termos do Shopee Vídeo — aceite na Central do Vendedor "
        "(login principal da loja) e libere a conta em Cadastros › Redes Sociais",
        False,
    ),
    (
        ("unauthorized", "toggle"),
        'a Shopee ainda não liberou esta loja para postar vídeo pela API ("toggle") — '
        "abra chamado na Open Platform; nada foi publicado",
        False,
    ),
    (
        ("error_sign", "wrong sign"),
        "a Shopee recusou a assinatura — confira o partner_id e a partner_key do app de vídeo "
        "nesta conta (Cadastros › Redes Sociais)",
        False,
    ),
    (
        ("error_api_permission", "no permission"),
        "o app desta conta não tem permissão de vídeo — o partner tem de ser o do app "
        "Shopee Video Management (DaVinci Videos), não o da integração da loja",
        False,
    ),
    (
        ("error_partner_key_expired",),
        "a partner_key do app de vídeo venceu — gere outra no Console da Shopee, digite na conta "
        "e autorize de novo",
        True,
    ),
    (
        ("source_ip_undeclared",),
        "o app de vídeo tem lista de IPs ligada — declare o IP do servidor no Console da Shopee",
        False,
    ),
    (
        ("error_api_call_restricted", "restricted"),
        "a Shopee restringiu o app de vídeo — confira o Console da Open Platform",
        False,
    ),
    (
        ("user_banned",),
        "a Shopee diz que a conta da loja está suspensa",
        True,
    ),
    (
        ("user_no_linked", "no linked"),
        "a autorização do app de vídeo foi desfeita na Shopee — autorize de novo",
        True,
    ),
)
_TOKEN_RECUSADO = ("invalid_access_token", "invalid_acceess_token", "invalid access_token")
# A Shopee disse com todas as letras que NÃO fez agora — tenta no próximo tique.
_ESPERAR = (
    "error_rate_limit",
    "too many",
    "error_limit",
    "please retry",
)
# Problema do lado DELA (ou sem resposta legível): nas chamadas comuns é
# passageiro; no `post_video` é AMBÍGUO — pode ter publicado.
_SERVIDOR = (
    "error_server",
    "error_busi",
    "system busy",
    "sem_resposta",
    "resposta_invalida",
    "falha_http",
)


def _gente(e: ShopeeVideoError) -> tuple[str, bool] | None:
    for trechos, frase, reautorizar in _PRECISA_GENTE:
        if e.tem(*trechos):
            return frase, reautorizar
    return None


def precisa_gente(e: ShopeeVideoError) -> str | None:
    g = _gente(e)
    return g[0] if g else None


def _agora() -> datetime:
    return datetime.now(UTC)


def _iso(d: datetime) -> str:
    return d.isoformat()


def _de_iso(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


class _LinhaPerdidaError(Exception):
    """A cerca não pegou: outro tique (ou o reconciliador) assumiu a linha.
    Este tique para sem escrever mais nada — e sem chamar a Shopee."""


class _PassageiroError(Exception):
    """Não deu pra perguntar agora — o próximo tique tenta de novo."""


class _Linha:
    """O retrato da postagem que a máquina carrega e regrava a cada passo.

    Grava por UPDATE direto (nunca pelo ORM): o worker pode ter dado rollback
    numa linha anterior do mesmo tique, e um objeto expirado faria lazy-load
    em sessão async. E grava com CERCA: só pega se a linha ainda estiver no
    `status`/`claimed_at` lidos no começo (o lease deste tique)."""

    def __init__(self, session: AsyncSession, p: MarketingPostagem) -> None:
        self.s = session
        self.id: UUID = p.id
        self.opcoes: dict[str, Any] = dict(p.opcoes or {})
        self.sh: dict[str, Any] = dict(self.opcoes.get("shopee") or {})
        self.prog: dict[str, Any] = dict(self.sh.get("progresso") or {})
        self.container_id: str | None = p.container_id
        self.legenda: str = p.legenda or ""
        self.rede_social_id: UUID | None = p.rede_social_id
        self.creative_id: UUID = p.creative_id
        self.conta: str = p.conta or "conta sem @"
        self.attempts: int = p.attempts or 0
        self.status_lido: str = p.status
        self.claim_lido: datetime | None = p.claimed_at
        # O cliente de vídeo da conta, quando este tique já montou um (pra
        # apagar o rascunho órfão sem pedir a credencial de novo).
        self.cliente: ClienteShopeeVideo | None = None

    def _opcoes(self) -> dict[str, Any]:
        sh = dict(self.sh)
        sh["progresso"] = dict(self.prog)
        return {**self.opcoes, "shopee": sh}

    async def salvar(self, *, cerca: bool = True, **valores: Any) -> None:
        q = update(MarketingPostagem).where(MarketingPostagem.id == self.id)
        if cerca:
            q = q.where(MarketingPostagem.status == self.status_lido)
            q = q.where(
                MarketingPostagem.claimed_at.is_(None)
                if self.claim_lido is None
                else MarketingPostagem.claimed_at == self.claim_lido
            )
        r = await self.s.execute(q.values(opcoes=self._opcoes(), **valores))
        await self.s.commit()
        if r.rowcount == 0:
            raise _LinhaPerdidaError(str(self.id))

    async def etapa(self, etapa: str, **campos: Any) -> None:
        self.prog["etapa"] = etapa
        self.prog.update(campos)
        await self.salvar()


def _post_pode_ter_saido(ln: _Linha) -> bool:
    return bool(ln.prog.get("post_chamado_em")) and not ln.prog.get("post_recusado")


# ─────────────────────────────────────────────────────────── desfechos


async def _publicado(ln: _Linha, post_id: str, *, texto: str) -> str:
    """O post existe na Shopee: grava SEM cerca. É a verdade dela — se o
    reconciliador mexeu na linha no meio, o que vale é o post no ar."""
    agora = _agora()
    ln.prog["post_id"] = post_id
    ln.prog["etapa"] = "publicado"
    await ln.salvar(
        cerca=False,
        status=STATUS_PUBLICADO,
        post_external_id=post_id[:128],
        container_id=ln.container_id,
        publicado_em=agora,
        completed_at=agora,
        result=texto[:2000],
    )
    if ln.rede_social_id:
        await conta_svc.registrar_saude(ln.rede_social_id, erro=None)
    logger.info("shopee_video_publicado", postagem=str(ln.id))
    return PUBLICADO


async def _aguardar(ln: _Linha, texto: str, *, falha: bool = False) -> str:
    """Volta pra `pendente` mantendo o progresso e o `container_id`: o
    próximo tique continua de onde parou. Não gasta tentativa.

    Nunca com o post possivelmente no ar: aí a linha não pode voltar pra
    fila (cancelável na tela, sem reconciliação) — vai pra `revisar`."""
    if _post_pode_ter_saido(ln):
        return await _revisar(ln, texto)
    ln.prog["falhas_seguidas"] = (int(ln.prog.get("falhas_seguidas") or 0) + 1) if falha else 0
    if falha and ln.prog["falhas_seguidas"] >= MAX_FALHAS_SEGUIDAS:
        return await _falhou(
            ln, f"{texto} — {MAX_FALHAS_SEGUIDAS} vezes seguidas; nada foi publicado"
        )
    ln.prog["aguardando_desde"] = ln.prog.get("aguardando_desde") or _iso(_agora())
    await ln.salvar(
        status=STATUS_PENDENTE,
        claimed_at=None,
        result=f"Shopee: {texto}"[:2000],
    )
    return AGUARDANDO


async def _apagar_rascunho(ln: _Linha, vid: str) -> None:
    """Tira da Shopee o rascunho que esta tentativa deixou e que ninguém vai
    publicar (senão ele fica na lista de rascunhos da loja, e publicado à mão
    sairia em dobro sem o DaVinci saber). Só apaga o que a Shopee CONFIRMA
    que ainda é rascunho; qualquer falha só vai pro log."""
    cliente = ln.cliente
    try:
        if cliente is None:
            if ln.rede_social_id is None:
                return
            cliente = (await conta_svc.credencial(ln.rede_social_id)).cliente()
        d = await cliente.detalhe(video_upload_id=vid)
        if int(d.get("status") or 0) != STATUS_RASCUNHO:
            logger.info("shopee_video_rascunho_nao_apagado", postagem=str(ln.id))
            return
        await cliente.apagar_rascunho(vid)
        logger.info("shopee_video_rascunho_apagado", postagem=str(ln.id))
    except Exception as e:  # noqa: BLE001 — limpeza, nunca derruba o desfecho
        logger.warning(
            "shopee_video_rascunho_apagar_falhou",
            postagem=str(ln.id),
            err=type(e).__name__,
            code=getattr(e, "code", None),
        )


async def _falhou(ln: _Linha, texto: str, *, manter_rascunho: bool = False) -> str:
    """Desfecho definitivo em que o post NÃO saiu (`post_video` nunca foi
    chamado, ou a Shopee o recusou com todas as letras).

    Zera o `container_id` de propósito: sem isso o "tentar de novo" da tela se
    recusaria ("precisa conferir") numa linha que comprovadamente não foi ao
    ar. O rascunho que ficou na Shopee é APAGADO — a tentativa nova sobe o
    arquivo do zero —, a não ser com `manter_rascunho` (o que parou foi a
    CONTA, não o vídeo): aí o rascunho pronto fica, e o "tentar de novo" desta
    linha só o publica."""
    if _post_pode_ter_saido(ln):
        # Cinto de segurança: se o post PODE ter saído, isto não é falha.
        return await _revisar(ln, texto)
    agora = _agora()
    vid = ln.prog.get("video_upload_id")
    tem_rascunho = bool(vid and ln.prog.get("rascunho_em"))
    manter = manter_rascunho and tem_rascunho
    historico = {k: ln.prog.get(k) for k in ("video_upload_id", "etapa") if ln.prog.get(k)}
    ultima = {**historico, "em": _iso(agora)}
    if manter:
        guardado = {
            k: ln.prog.get(k)
            for k in ("video_upload_id", "capa", "rascunho_em", "upload_ok_em", "pronto_em")
            if ln.prog.get(k)
        }
        ln.prog = {**guardado, "etapa": POSTAR, "rascunho_mantido": True, "ultima_falha": ultima}
        texto = (
            f"{texto}. O rascunho já está pronto na Shopee: depois de resolver, use "
            "'tentar de novo' NESTA linha — ela só publica o rascunho, sem subir o vídeo de novo"
        )
    else:
        ln.prog = {"ultima_falha": ultima}
    await ln.salvar(
        status=STATUS_FALHOU,
        container_id=None,
        completed_at=agora,
        attempts=ln.attempts + 1,
        result=f"Shopee: {texto}"[:2000],
    )
    if tem_rascunho and not manter:
        await _apagar_rascunho(ln, str(vid))
    if ln.rede_social_id:
        await conta_svc.registrar_saude(ln.rede_social_id, erro=f"Shopee: {texto}")
    logger.info("shopee_video_falhou", postagem=str(ln.id), etapa=historico.get("etapa"))
    return FALHOU


async def _revisar(ln: _Linha, texto: str) -> str:
    agora = _agora()
    await ln.salvar(
        status=STATUS_REVISAR,
        container_id=ln.container_id,
        completed_at=agora,
        result=(
            f"Shopee: {texto}. NÃO publique de novo — o reconciliador confere o rascunho "
            "na Shopee sozinho; se ele não resolver, confira a loja antes de qualquer coisa."
        )[:2000],
    )
    logger.warning("shopee_video_revisar", postagem=str(ln.id))
    return REVISAR


# ───────────────────────────────────────────────────────────── a máquina


async def publicar_postagem(
    session: AsyncSession,
    postagem_id: UUID,
    caminho: Path,
    *,
    orcamento_s: float = ORCAMENTO_TICK_S,
    dormir: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> str:
    """Avança a postagem (já `publicando`, do lease) o quanto der. Nunca levanta.

    Devolve a ação: publicado | aguardando | falhou | revisar.
    """
    p = await session.get(MarketingPostagem, postagem_id, populate_existing=True)
    if p is None or p.status != STATUS_PUBLICANDO:
        return AGUARDANDO
    ln = _Linha(session, p)
    try:
        try:
            return await _avancar(ln, caminho, orcamento_s=orcamento_s, dormir=dormir)
        except _LinhaPerdidaError:
            raise
        except Exception as e:  # noqa: BLE001 — o tique nunca morre por causa de uma linha
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                logger.warning("shopee_video_rollback_falhou", postagem=str(postagem_id))
            logger.error(
                "shopee_video_publicar_erro", postagem=str(postagem_id), err=type(e).__name__
            )
            texto = f"erro inesperado ({type(e).__name__})"
            if ln.prog.get("post_chamado_em"):
                return await _revisar(ln, texto)
            return await _aguardar(ln, texto, falha=True)
    except _LinhaPerdidaError:
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            logger.warning("shopee_video_rollback_falhou", postagem=str(postagem_id))
        logger.warning("shopee_video_lease_perdido", postagem=str(postagem_id))
        return AGUARDANDO


async def _avancar(
    ln: _Linha,
    caminho: Path,
    *,
    orcamento_s: float,
    dormir: Callable[[float], Awaitable[Any]],
) -> str:
    s = ln.s
    inicio = _agora()
    dormido = 0.0

    def sobra() -> float:
        # O relógio de verdade OU o que já se dormiu, o que for maior: em
        # produção é o relógio; com o `dormir` de mentira dos testes o laço
        # de espera também termina.
        gasto = max((_agora() - inicio).total_seconds(), dormido)
        return orcamento_s - gasto

    async def esperar(segundos: float) -> None:
        nonlocal dormido
        dormido += segundos
        await dormir(segundos)

    rede = await s.get(RedeSocial, ln.rede_social_id) if ln.rede_social_id else None
    if rede is None or rede.integration_id is None:
        return await _falhou(ln, "a conta não está ligada a uma loja Shopee")

    # O anúncio: o que foi conferido no agendamento (snapshot). Postagem sem
    # snapshot (criada antes) escolhe agora, pela MESMA regra.
    item_id = ln.sh.get("item_id")
    if not item_id:
        creative = await s.get(MarketingCreative, ln.creative_id)
        escolha = await anuncio_svc.anuncio_para(s, creative, rede) if creative else "sem_criativo"
        if isinstance(escolha, str):
            return await _falhou(ln, f"sem anúncio para vincular ({escolha})")
        ln.sh.update(escolha.snapshot())
        item_id = escolha.item_id
    item_id = int(item_id)
    legenda = (ln.legenda or "").strip()
    if tamanho_shopee(legenda) > LEGENDA_MAX:
        return await _falhou(
            ln, f"a legenda passa de {LEGENDA_MAX} caracteres (legenda_longa_shopee)"
        )

    # Começo da história desta tentativa.
    if not ln.prog.get("inicio_em"):
        ln.prog["inicio_em"] = _iso(inicio)

    try:
        cred = await conta_svc.credencial(rede.id)
    except conta_svc.ReautorizarError as e:
        return await _falhou(ln, e.mensagem)
    except conta_svc.ContaShopeeError as e:
        if e.code in ("conta_sem_app_shopee", "conta_sem_autorizacao_shopee"):
            return await _falhou(ln, e.mensagem)
        return await _aguardar(ln, e.mensagem, falha=True)
    cliente = cred.cliente()
    ln.cliente = cliente

    async def com_token(chamada: Callable[[ClienteShopeeVideo], Awaitable[Any]]) -> Any:
        """Chamada User com UMA renovação se a Shopee recusar o token."""
        nonlocal cliente
        try:
            return await chamada(cliente)
        except ShopeeVideoError as e:
            if not e.tem(*_TOKEN_RECUSADO) and e.code != "error_auth":
                raise
            novo = await conta_svc.credencial(rede.id, recusado=cliente._token)
            cliente = novo.cliente()
            ln.cliente = cliente
            return await chamada(cliente)

    conferido_neste_tique = False

    async def conferir() -> str | None:
        """O anúncio ao vivo (NORMAL e com estoque). None = pode seguir;
        texto = motivo definitivo. Levanta `_PassageiroError` se não deu pra
        perguntar."""
        nonlocal conferido_neste_tique
        if conferido_neste_tique:
            return None
        try:
            c = await anuncio_svc.conferir_ao_vivo(s, rede.integration_id, item_id)
        except Exception as e:  # noqa: BLE001
            raise _PassageiroError(
                f"não deu pra conferir o anúncio {item_id} na loja ({type(e).__name__})"
            ) from None
        if not c.ok:
            return c.motivo or f"anúncio {item_id} indisponível"
        conferido_neste_tique = True
        ln.prog["conferido_em"] = _iso(_agora())
        return None

    def conferido_ha_pouco() -> bool:
        quando = _de_iso(ln.prog.get("conferido_em"))
        return quando is not None and _agora() - quando < VALIDADE_CONFERENCIA

    async def subir_de_novo(motivo: str) -> str | None:
        """Descarta o upload atual (NADA foi postado) e volta pro começo —
        no máximo `MAX_RESUBIDAS` vez por tentativa. Devolve o desfecho
        quando não dá mais."""
        feitas = int(ln.prog.get("resubidas") or 0)
        if feitas >= MAX_RESUBIDAS:
            return await _falhou(
                ln, f"{motivo} — o vídeo já foi subido de novo uma vez; nada foi publicado"
            )
        ln.container_id = None
        await ln.etapa(SUBIR, video_upload_id=None, resubidas=feitas + 1)
        return None

    try:
        while True:
            etapa = ln.prog.get("etapa") or SUBIR
            vid = ln.prog.get("video_upload_id") or None

            if etapa in (SUBIR, ENVIANDO) and not ln.prog.get("post_chamado_em"):
                if etapa == ENVIANDO and vid:
                    # O tique anterior morreu no meio do envio. Se a Shopee já
                    # recebeu tudo, só falta concluir; senão sobe do zero —
                    # seguro: nada foi postado, o upload velho fica órfão.
                    try:
                        r = await cliente.resultado_upload(vid)
                        st = r.status
                    except ShopeeVideoError:
                        st = ""
                    if st == "UPLOADED":
                        await cliente.concluir_upload(vid)
                        await ln.etapa(PROCESSANDO, upload_ok_em=_iso(_agora()))
                        continue
                    if st in ("PROCESSING", "SUCCEEDED"):
                        await ln.etapa(PROCESSANDO, upload_ok_em=_iso(_agora()))
                        continue
                    desfecho = await subir_de_novo("o envio anterior parou no meio")
                    if desfecho:
                        return desfecho
                    continue
                if sobra() < MIN_SOBRA_SUBIR_S:
                    return await _aguardar(ln, "esperando folga no ciclo pra subir o vídeo")
                info = await anuncio_svc.sondar_video(caminho)
                try:
                    tamanho = caminho.stat().st_size
                except OSError:
                    tamanho = None
                motivo = anuncio_svc.motivo_do_formato(info, tamanho)
                if motivo or info is None or tamanho is None:
                    return await _falhou(
                        ln, f"o vídeo não serve pra Shopee ({motivo or 'ilegível'})"
                    )
                vid, parte = await cliente.iniciar_upload(
                    file_name=caminho.name,
                    file_size=tamanho,
                    duracao_s=max(1, math.ceil(info.duracao or 1)),
                )
                ln.container_id = vid
                ln.prog["video_upload_id"] = vid
                ln.prog["etapa"] = ENVIANDO
                # ANTES do primeiro byte: se o processo morrer daqui pra
                # frente, a linha diz que havia upload em voo.
                await ln.salvar(container_id=vid)
                for seq, conteudo in partes_do_arquivo(caminho, parte):
                    try:
                        await cliente.enviar_parte(vid, seq, conteudo)
                    except ShopeeVideoError as e:
                        if not e.tem("md5", "sem_resposta"):
                            raise
                        # A doc manda reenviar a MESMA parte.
                        await cliente.enviar_parte(vid, seq, conteudo)
                await cliente.concluir_upload(vid)
                await ln.etapa(PROCESSANDO, upload_ok_em=_iso(_agora()))
                continue

            if etapa == PROCESSANDO:
                comeco = _de_iso(ln.prog.get("upload_ok_em")) or _agora()
                if _agora() - comeco > TETO_PROCESSAMENTO:
                    return await _falhou(ln, "a Shopee não terminou de processar o vídeo em 2 h")
                try:
                    r = await cliente.resultado_upload(vid)
                except ShopeeVideoError as e:
                    # Texto EXATO da doc: "not found" solto pegaria qualquer
                    # coisa e mandaria subir de novo em laço.
                    if e.tem("upload task not found"):
                        desfecho = await subir_de_novo(
                            "a Shopee perdeu o upload (Upload task not found)"
                        )
                        if desfecho:
                            return desfecho
                        continue
                    raise
                if r.status == "SUCCEEDED":
                    await ln.etapa(CAPA, pronto_em=_iso(_agora()))
                    continue
                if r.status in ("FAILED", "CANCELLED"):
                    return await _falhou(
                        ln, f"a Shopee recusou o vídeo ({r.status}: {r.motivo or 'sem motivo'})"
                    )
                if r.status == "UPLOADED":
                    await cliente.concluir_upload(vid)
                if sobra() < POLL_PROCESSANDO_S:
                    return await _aguardar(ln, "processando o vídeo")
                await esperar(POLL_PROCESSANDO_S)
                continue

            if etapa == CAPA:
                pronto = _de_iso(ln.prog.get("pronto_em")) or _agora()
                falta = ESPERA_CAPA_S - (_agora() - pronto).total_seconds()
                if falta > 0:
                    if sobra() < falta:
                        return await _aguardar(ln, "esperando as capas do vídeo")
                    await esperar(falta)
                try:
                    capas = await com_token(lambda c, v=vid: c.capas(v))
                except ShopeeVideoError as e:
                    if not e.tem("less than 1 min", "data not found", "submit task"):
                        raise
                    capas = []
                if not capas:
                    if _agora() - pronto > TETO_PROCESSAMENTO:
                        return await _falhou(ln, "a Shopee não gerou as capas do vídeo em 2 h")
                    if sobra() < POLL_CAPA_S:
                        return await _aguardar(ln, "esperando as capas do vídeo")
                    await esperar(POLL_CAPA_S)
                    continue
                # O quadro do meio: o primeiro costuma ser a vinheta/preto.
                await ln.etapa(RASCUNHO, capa=capas[len(capas) // 2])
                continue

            if etapa == RASCUNHO:
                if not conferido_ha_pouco():
                    motivo = await conferir()
                    if motivo:
                        return await _falhou(ln, motivo)
                try:
                    await com_token(
                        lambda c, v=vid: c.editar(
                            v,
                            legenda=legenda,
                            capa=str(ln.prog.get("capa") or ""),
                            item_id=item_id,
                            aigc_label=bool(ln.sh.get("aigc_label", True)),
                        )
                    )
                except ShopeeVideoError as e:
                    if e.tem("cover is illegal") and not ln.prog.get("capa_refeita"):
                        await ln.etapa(CAPA, capa=None, capa_refeita=True)
                        continue
                    if e.tem("please retry") and not ln.prog.get("edit_repetido"):
                        ln.prog["edit_repetido"] = True
                        continue
                    if e.tem("can not find video meta"):
                        # A doc: "esperar e repetir; se o upload deu FAILED,
                        # recusar" — e com teto, senão seria um laço eterno
                        # (com duas chamadas à API da loja por volta).
                        r = await cliente.resultado_upload(vid)
                        if r.status in ("FAILED", "CANCELLED"):
                            motivo_upload = r.motivo or "sem motivo"
                            return await _falhou(
                                ln, f"a Shopee recusou o vídeo ({r.status}: {motivo_upload})"
                            )
                        pronto = _de_iso(ln.prog.get("pronto_em")) or _agora()
                        if _agora() - pronto > TETO_META:
                            return await _falhou(
                                ln,
                                "a Shopee não encontrou o vídeo processado em 30 min "
                                f"({e.texto()})",
                            )
                        return await _aguardar(ln, "a Shopee ainda está preparando o vídeo")
                    if e.tem("invalid video source"):
                        desfecho = await subir_de_novo(f"a Shopee recusou o upload ({e.texto()})")
                        if desfecho:
                            return desfecho
                        continue
                    if e.tem("can not find item info"):
                        return await _falhou(
                            ln, f"anúncio {item_id} não encontrado na loja ({e.texto()})"
                        )
                    raise
                await ln.etapa(POSTAR, rascunho_em=_iso(_agora()))
                continue

            if etapa == POSTAR:
                if not ln.prog.get("post_chamado_em") or ln.prog.get("post_recusado"):
                    # Estoque e status de NOVO, colados no post: item errado
                    # ou sem estoque não tem conserto depois de publicado.
                    motivo = await conferir()
                    if motivo:
                        return await _falhou(ln, motivo)
                # Carimbo ANTES da chamada, COM a cerca do lease: se outro
                # tique assumiu a linha, isto não pega e a Shopee nem é
                # chamada. Daqui em diante a linha nunca mais volta pro
                # começo (subir de novo poderia duplicar o post).
                ln.prog["post_chamado_em"] = _iso(_agora())
                ln.prog.pop("post_recusado", None)
                ln.prog.pop("rascunho_mantido", None)
                ln.container_id = vid
                await ln.salvar(container_id=vid)
                try:
                    post_id = await com_token(lambda c, v=vid: c.postar(v))
                except ShopeeVideoRedeError as e:
                    # Timeout, conexão caída OU resposta aceita sem post_id.
                    return await _revisar(
                        ln, f"a Shopee não confirmou a publicação ({e.texto()})"
                    )
                except ShopeeVideoError as e:
                    if e.tem(*_SERVIDOR):
                        # Erro do lado dela: pode ter publicado.
                        return await _revisar(
                            ln, f"a Shopee respondeu com erro dela ao publicar ({e.texto()})"
                        )
                    if e.tem("current status"):
                        return await _status_do_rascunho(ln, com_token, vid, item_id, e)
                    # Resposta com erro explícito = a Shopee NÃO publicou.
                    ln.prog["post_recusado"] = True
                    raise
                ln.prog["post_id"] = post_id
                # O id é o bem mais precioso daqui: sem cerca.
                await ln.salvar(cerca=False, post_external_id=post_id[:128])
                # Conferência: o post existe e está no ar. Falhar aqui não
                # desfaz nada — o id já está gravado.
                status = None
                try:
                    d = await com_token(lambda c, p=post_id: c.detalhe(post_id=p))
                    status = int(d.get("status") or 0) or None
                except Exception:  # noqa: BLE001
                    status = None
                if status == STATUS_APAGADO:
                    return await _revisar(ln, "o vídeo aparece APAGADO logo depois de publicado")
                return await _publicado(
                    ln,
                    post_id,
                    texto=(
                        f"publicado na Shopee Vídeo em {ln.conta} (anúncio {item_id})"
                        + (
                            ""
                            if status in (None, STATUS_POSTADO)
                            else f" — status {status} na Shopee"
                        )
                    ),
                )

            # Etapa desconhecida (linha mexida à mão): recomeça com segurança.
            if ln.prog.get("post_chamado_em"):
                return await _revisar(ln, f"etapa desconhecida ({etapa})")
            await ln.etapa(SUBIR, video_upload_id=None)
            ln.container_id = None

    except _PassageiroError as e:
        return await _aguardar(ln, str(e), falha=True)
    except conta_svc.ReautorizarError as e:
        return await _falhou(ln, e.mensagem)
    except conta_svc.ContaShopeeError as e:
        return await _aguardar(ln, e.mensagem, falha=True)
    except ShopeeVideoRedeError as e:
        if _post_pode_ter_saido(ln):
            return await _revisar(ln, f"a Shopee não respondeu ({e.detalhe or e.code})")
        return await _aguardar(ln, f"a Shopee não respondeu ({e.detalhe or e.code})", falha=True)
    except ShopeeVideoError as e:
        gente = _gente(e)
        if gente:
            frase, reautorizar = gente
            texto = f"{frase} ({e.texto()})"
            # A CONTA para: sem isto cada vaga da grade subiria outro vídeo
            # inteiro só pra ouvir o mesmo "não" (e o tiraria da fila).
            await conta_svc.bloquear(rede.id, motivo=texto, reautorizar=reautorizar)
            return await _falhou(ln, texto, manter_rascunho=True)
        if e.tem(*_ESPERAR, *_SERVIDOR):
            return await _aguardar(ln, f"a Shopee pediu para esperar ({e.texto()})", falha=True)
        return await _falhou(ln, f"a Shopee recusou ({e.texto()})")


async def _status_do_rascunho(
    ln: _Linha,
    com_token: Callable[[Callable[[ClienteShopeeVideo], Awaitable[Any]]], Awaitable[Any]],
    vid: str,
    item_id: int,
    erro: ShopeeVideoError,
) -> str:
    """O `post_video` disse "task can not be process under the current
    status": o post pode já ter saído (um tique anterior morreu depois de
    postar). A Shopee diz em que estado o vídeo está. Só depois de ELA dizer
    que não está publicado a linha conta como "não saiu"; qualquer falha
    nessa pergunta é dúvida, e dúvida é `revisar`."""
    try:
        d = await com_token(lambda c, v=vid: c.detalhe(video_upload_id=v))
        st = int(d.get("status") or 0)
    except Exception as e:  # noqa: BLE001
        return await _revisar(
            ln,
            f"a Shopee recusou publicar ({erro.texto()}) e a conferência do vídeo falhou "
            f"({type(e).__name__})",
        )
    if st == STATUS_POSTADO:
        if d.get("post_id"):
            return await _publicado(
                ln,
                str(d["post_id"]),
                texto=f"publicado na Shopee Vídeo em {ln.conta} (anúncio {item_id})",
            )
        return await _revisar(ln, "a Shopee mostra o vídeo publicado, mas sem o post_id")
    ln.prog["post_recusado"] = True
    if st == STATUS_RASCUNHO:
        # Ainda rascunho e ela não deixou publicar agora: espera e tenta de
        # novo o MESMO rascunho (até o teto de falhas seguidas).
        return await _aguardar(
            ln, f"a Shopee ainda não aceitou publicar o rascunho ({erro.texto()})", falha=True
        )
    return await _falhou(
        ln, f"a Shopee mostra o vídeo com status {st} e não publicou ({erro.texto()})"
    )


# ──────────────────────────────────────────────────────── fora da máquina


async def devolver(session: AsyncSession, postagem_id: UUID, motivo: str) -> str:
    """Devolve pra fila, SEM chamar a Shopee, uma linha que o tique pegou e
    não teve tempo de tocar (o orçamento do minuto acabou). O progresso, o
    `container_id` e o contador de falhas ficam como estão — o
    `devolver_para_fila` genérico não serve aqui porque recusa linha com
    container, e a da Shopee retomada sempre tem."""
    p = await session.get(MarketingPostagem, postagem_id, populate_existing=True)
    if p is None or p.status != STATUS_PUBLICANDO:
        return AGUARDANDO
    ln = _Linha(session, p)
    if _post_pode_ter_saido(ln):
        # Não deveria existir pendente assim; se existir, quem decide é o
        # reconciliador (consultando), nunca a fila.
        return AGUARDANDO
    try:
        await ln.salvar(status=STATUS_PENDENTE, claimed_at=None, result=f"Shopee: {motivo}"[:2000])
    except _LinhaPerdidaError:
        return AGUARDANDO
    return AGUARDANDO


async def recusar(session: AsyncSession, postagem_id: UUID, texto: str) -> str:
    """A guarda da hora de publicar (`revalidar`) recusou uma linha Shopee
    que pode estar no meio do caminho (upload feito, rascunho pronto). Fecha
    como a máquina fecharia: apaga o rascunho órfão e zera o container, pra o
    "tentar de novo" funcionar — ou `revisar`, se o post pode ter saído."""
    p = await session.get(MarketingPostagem, postagem_id, populate_existing=True)
    if p is None:
        return FALHOU
    ln = _Linha(session, p)
    try:
        return await _falhou(ln, texto)
    except _LinhaPerdidaError:
        return AGUARDANDO


# ──────────────────────────────────────────────────────────── reconciliação


async def reconciliar(session: AsyncSession, postagem_id: UUID) -> str:
    """Postagem da Shopee presa em `publicando` (worker morto) ou em
    `revisar` com upload: PERGUNTA à Shopee, nunca sobe de novo.

      • `publicando` há menos de 25 min → não mexe (o tique pode estar vivo);
      • com post_id                → detalhe: no ar = publicado; apagado = revisar;
      • `post_video` nunca chamado → nada foi ao ar: volta pra fila e continua
                                     de onde parou (só se estava `publicando`);
      • `post_video` chamado sem id → procura o upload na lista de publicados;
                                     achou = publicado; ainda RASCUNHO = volta
                                     pra fila pra postar o MESMO rascunho.
    """
    p = await session.get(MarketingPostagem, postagem_id, populate_existing=True)
    if p is None:
        return REVISAR
    ln = _Linha(session, p)
    post_id = p.post_external_id or ln.prog.get("post_id")
    vid = p.container_id or ln.prog.get("video_upload_id")
    estava_publicando = p.status == STATUS_PUBLICANDO
    if estava_publicando:
        pego = p.claimed_at or p.updated_at
        if pego is not None and pego.tzinfo is None:
            pego = pego.replace(tzinfo=UTC)
        if pego is not None and pego > _agora() - LEASE_MORTO:
            return AGUARDANDO
    try:
        return await _reconciliar(ln, p.rede_social_id, post_id, vid, estava_publicando)
    except _LinhaPerdidaError:
        return AGUARDANDO


async def _reconciliar(
    ln: _Linha,
    rede_social_id: UUID | None,
    post_id: str | None,
    vid: str | None,
    estava_publicando: bool,
) -> str:
    if not post_id and not ln.prog.get("post_chamado_em"):
        if estava_publicando:
            # Nenhuma chamada de publicar aconteceu: retomar é seguro.
            await ln.salvar(
                status=STATUS_PENDENTE,
                claimed_at=None,
                result="Shopee: o tique anterior parou no meio — continua de onde estava",
            )
            return AGUARDANDO
        return REVISAR

    if rede_social_id is None:
        return await _revisar(ln, "a conta foi apagada — não há como consultar a Shopee")
    try:
        cred = await conta_svc.credencial(rede_social_id)
    except conta_svc.ContaShopeeError as e:
        return await _revisar(ln, f"sem como consultar a Shopee: {e.mensagem}")
    cliente = cred.cliente()
    ln.cliente = cliente
    try:
        if post_id:
            d = await cliente.detalhe(post_id=post_id)
            st = int(d.get("status") or 0)
            if st == STATUS_POSTADO:
                return await _publicado(
                    ln, str(post_id), texto="reconciliado: a Shopee confirmou a publicação"
                )
            if st == STATUS_APAGADO:
                return await _revisar(ln, "o vídeo foi APAGADO na Shopee (a API não diz o motivo)")
            return await _revisar(ln, f"a Shopee mostra o vídeo com status {st}")
        if vid:
            for pagina in range(1, 6):
                r = await cliente.lista(publicados=True, pagina=pagina, por_pagina=20)
                for v in r.get("list") or []:
                    if str(v.get("video_upload_id")) == str(vid) and v.get("post_id"):
                        return await _publicado(
                            ln,
                            str(v["post_id"]),
                            texto="reconciliado: o vídeo está publicado na Shopee",
                        )
                if not r.get("has_more"):
                    break
            d = await cliente.detalhe(video_upload_id=vid)
            st = int(d.get("status") or 0)
            if st == STATUS_RASCUNHO:
                # O post NÃO saiu e o rascunho continua lá: a linha volta pra
                # fila na etapa de postar, com o MESMO video_upload_id. Postar
                # o mesmo rascunho de novo não duplica (se o post antigo sair
                # atrasado, a Shopee responde "current status" e o detalhe
                # confirma); criar postagem nova subiria outro vídeo.
                ln.prog["etapa"] = POSTAR
                ln.prog["post_recusado"] = True
                ln.prog["video_upload_id"] = vid
                await ln.salvar(
                    status=STATUS_PENDENTE,
                    claimed_at=None,
                    completed_at=None,
                    container_id=vid,
                    result=(
                        "Shopee: o post não saiu e o rascunho continua lá — o robô publica "
                        "o MESMO rascunho no próximo ciclo (sem subir o vídeo de novo)"
                    ),
                )
                return AGUARDANDO
            if st == STATUS_POSTADO and d.get("post_id"):
                return await _publicado(
                    ln, str(d["post_id"]), texto="reconciliado: a Shopee confirmou a publicação"
                )
            if st == STATUS_APAGADO:
                return await _revisar(ln, "o vídeo foi APAGADO na Shopee (a API não diz o motivo)")
    except ShopeeVideoError as e:
        return await _revisar(ln, f"a consulta à Shopee falhou ({e.texto()})")
    return await _revisar(ln, "a Shopee não encontrou o vídeo")
