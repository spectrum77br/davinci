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
fica em `revisar` pro reconciliador e pra gente.

Erro que só gente resolve (Termos do Shopee Vídeo não aceitos, conta fora da
lista da API — "toggle" —, autorização vencida, app errado) vira `falhou`
com a frase em português e NÃO volta pra fila: tentar de novo sozinho só
gastaria chamada e sujaria o painel.
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

# Quanto um tique espera a Shopee antes de devolver a linha pra fila.
ORCAMENTO_TICK_S = 240.0
POLL_PROCESSANDO_S = 10.0
# "upload video less than 1 min": as capas só saem 1 min depois do pronto.
ESPERA_CAPA_S = 65.0
POLL_CAPA_S = 20.0
# Teto do vídeo parado na Shopee sem ficar pronto (transcodificação + capas).
TETO_PROCESSAMENTO = timedelta(hours=2)
# Falhas passageiras seguidas (rede, loja fora do ar) antes de desistir.
MAX_FALHAS_SEGUIDAS = 5

# Ações (o que o worker conta).
PUBLICADO = "publicado"
AGUARDANDO = "aguardando"
FALHOU = "falhou"
REVISAR = "revisar"

# Erros que só uma pessoa resolve. Código/trecho → frase pra tela.
_PRECISA_GENTE: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("copyright_not_agree", "not agree"),
        "a loja ainda não aceitou os Termos do Shopee Vídeo — aceite na Central do Vendedor "
        "(login principal da loja) e publique de novo",
    ),
    (
        ("unauthorized", "toggle"),
        'a Shopee ainda não liberou esta loja para postar vídeo pela API ("toggle") — '
        "abra chamado na Open Platform; nada foi publicado",
    ),
    (
        ("error_sign", "wrong sign"),
        "a Shopee recusou a assinatura — confira o partner_id e a partner_key do app de vídeo "
        "nesta conta (Cadastros › Redes Sociais)",
    ),
    (
        ("error_api_permission", "no permission"),
        "o app desta conta não tem permissão de vídeo — o partner tem de ser o do app "
        "Shopee Video Management (DaVinci Videos), não o da integração da loja",
    ),
    (
        ("error_partner_key_expired",),
        "a partner_key do app de vídeo venceu — gere outra no Console da Shopee e digite na conta",
    ),
    (
        ("source_ip_undeclared",),
        "o app de vídeo tem lista de IPs ligada — declare o IP do servidor no Console da Shopee",
    ),
    (
        ("error_api_call_restricted", "restricted"),
        "a Shopee restringiu o app de vídeo — confira o Console da Open Platform",
    ),
    (
        ("user_banned",),
        "a Shopee diz que a conta da loja está suspensa",
    ),
    (
        ("user_no_linked", "no linked"),
        "a autorização do app de vídeo foi desfeita na Shopee — autorize de novo",
    ),
)
_TOKEN_RECUSADO = ("invalid_access_token", "invalid_acceess_token", "invalid access_token")
_PASSAGEIRO = (
    "error_rate_limit",
    "too many",
    "error_limit",
    "error_server",
    "error_busi",
    "sem_resposta",
    "please retry",
    "system busy",
    "resposta_invalida",
)


def precisa_gente(e: ShopeeVideoError) -> str | None:
    for trechos, frase in _PRECISA_GENTE:
        if e.tem(*trechos):
            return frase
    return None


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


class _Linha:
    """O retrato da postagem que a máquina carrega e regrava a cada passo.

    Grava por UPDATE direto (nunca pelo ORM): o worker pode ter dado rollback
    numa linha anterior do mesmo tique, e um objeto expirado faria lazy-load
    em sessão async."""

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

    def _opcoes(self) -> dict[str, Any]:
        sh = dict(self.sh)
        sh["progresso"] = dict(self.prog)
        return {**self.opcoes, "shopee": sh}

    async def salvar(self, **valores: Any) -> None:
        await self.s.execute(
            update(MarketingPostagem)
            .where(MarketingPostagem.id == self.id)
            .values(opcoes=self._opcoes(), **valores)
        )
        await self.s.commit()

    async def etapa(self, etapa: str, **campos: Any) -> None:
        self.prog["etapa"] = etapa
        self.prog.update(campos)
        await self.salvar()


# ─────────────────────────────────────────────────────────── desfechos


async def _publicado(ln: _Linha, post_id: str, *, texto: str) -> str:
    agora = _agora()
    ln.prog["post_id"] = post_id
    ln.prog["etapa"] = "publicado"
    await ln.salvar(
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
    próximo tique continua de onde parou. Não gasta tentativa."""
    ln.prog["falhas_seguidas"] = (int(ln.prog.get("falhas_seguidas") or 0) + 1) if falha else 0
    if falha and ln.prog["falhas_seguidas"] >= MAX_FALHAS_SEGUIDAS:
        return await _falhou(
            ln, f"{texto} — {MAX_FALHAS_SEGUIDAS} vezes seguidas; nada foi publicado"
        )
    ln.prog["aguardando_desde"] = ln.prog.get("aguardando_desde") or _iso(_agora())
    await ln.s.execute(
        update(MarketingPostagem)
        .where(MarketingPostagem.id == ln.id, MarketingPostagem.status == STATUS_PUBLICANDO)
        .values(
            opcoes=ln._opcoes(),
            status=STATUS_PENDENTE,
            claimed_at=None,
            result=f"Shopee: {texto}"[:2000],
        )
    )
    await ln.s.commit()
    return AGUARDANDO


async def _falhou(ln: _Linha, texto: str) -> str:
    """Desfecho definitivo em que o post NÃO saiu (`post_video` nunca foi
    chamado, ou voltou erro).

    Zera o `container_id` e o progresso de propósito: o rascunho que ficou na
    Shopee é invisível pra todo mundo, e sem zerar o "tentar de novo" da tela
    se recusaria ("precisa conferir na Meta") numa linha que comprovadamente
    não foi ao ar. A tentativa nova sobe o arquivo do zero."""
    if ln.prog.get("post_chamado_em") and not ln.prog.get("post_recusado"):
        # Cinto de segurança: se o post PODE ter saído, isto não é falha.
        return await _revisar(ln, texto)
    agora = _agora()
    historico = {k: ln.prog.get(k) for k in ("video_upload_id", "etapa") if ln.prog.get(k)}
    ln.prog = {"ultima_falha": {**historico, "em": _iso(agora)}}
    await ln.salvar(
        status=STATUS_FALHOU,
        container_id=None,
        completed_at=agora,
        attempts=ln.attempts + 1,
        result=f"Shopee: {texto}"[:2000],
    )
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
    p = await session.get(MarketingPostagem, postagem_id)
    if p is None or p.status != STATUS_PUBLICANDO:
        return AGUARDANDO
    ln = _Linha(session, p)
    try:
        return await _avancar(ln, caminho, orcamento_s=orcamento_s, dormir=dormir)
    except Exception as e:  # noqa: BLE001 — o tique nunca morre por causa de uma linha
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            logger.warning("shopee_video_rollback_falhou", postagem=str(postagem_id))
        logger.error("shopee_video_publicar_erro", postagem=str(postagem_id), err=type(e).__name__)
        texto = f"erro inesperado ({type(e).__name__})"
        if ln.prog.get("post_chamado_em"):
            return await _revisar(ln, texto)
        return await _aguardar(ln, texto, falha=True)


async def _avancar(
    ln: _Linha,
    caminho: Path,
    *,
    orcamento_s: float,
    dormir: Callable[[float], Awaitable[Any]],
) -> str:
    s = ln.s
    inicio = _agora()

    def sobra() -> float:
        return orcamento_s - (_agora() - inicio).total_seconds()

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

    # Começo da história desta tentativa — é dele que sai o teto de 2 h.
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
                    ln.prog.pop("video_upload_id", None)
                    ln.container_id = None
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
                    if e.tem("not found"):
                        # Upload expirou do lado dela: sobe de novo (nada postado).
                        await ln.etapa(SUBIR, video_upload_id=None)
                        ln.container_id = None
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
                await dormir(POLL_PROCESSANDO_S)
                continue

            if etapa == CAPA:
                pronto = _de_iso(ln.prog.get("pronto_em")) or _agora()
                falta = ESPERA_CAPA_S - (_agora() - pronto).total_seconds()
                if falta > 0:
                    if sobra() < falta:
                        return await _aguardar(ln, "esperando as capas do vídeo")
                    await dormir(falta)
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
                    await dormir(POLL_CAPA_S)
                    continue
                # O quadro do meio: o primeiro costuma ser a vinheta/preto.
                await ln.etapa(RASCUNHO, capa=capas[len(capas) // 2])
                continue

            if etapa == RASCUNHO:
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
                        return await _aguardar(ln, "a Shopee ainda está preparando o vídeo")
                    if e.tem("invalid video source") and not ln.prog.get("resubido"):
                        await ln.etapa(SUBIR, video_upload_id=None, resubido=True)
                        ln.container_id = None
                        continue
                    if e.tem("can not find item info"):
                        return await _falhou(
                            ln, f"anúncio {item_id} não encontrado na loja ({e.texto()})"
                        )
                    raise
                await ln.etapa(POSTAR, rascunho_em=_iso(_agora()))
                continue

            if etapa == POSTAR:
                if not ln.prog.get("post_chamado_em"):
                    # Estoque e status de NOVO, colados no post: item errado
                    # ou sem estoque não tem conserto depois de publicado.
                    motivo = await conferir()
                    if motivo:
                        return await _falhou(ln, motivo)
                # Carimbo ANTES da chamada: daqui em diante a linha nunca mais
                # volta pro começo (subir de novo poderia duplicar o post).
                ln.prog["post_chamado_em"] = _iso(_agora())
                ln.prog.pop("post_recusado", None)
                await ln.salvar()
                try:
                    post_id = await com_token(lambda c, v=vid: c.postar(v))
                except ShopeeVideoRedeError as e:
                    return await _revisar(
                        ln, f"a Shopee não respondeu ao publicar ({e.detalhe or e.code})"
                    )
                except ShopeeVideoError as e:
                    if e.tem("current status"):
                        # Pode já ter saído (tique anterior morreu depois do
                        # post): a Shopee diz em que estado o rascunho está.
                        d = await com_token(lambda c, v=vid: c.detalhe(video_upload_id=v))
                        if int(d.get("status") or 0) == STATUS_POSTADO and d.get("post_id"):
                            return await _publicado(
                                ln,
                                str(d["post_id"]),
                                texto=(
                                    f"publicado na Shopee Vídeo em {ln.conta} (anúncio {item_id})"
                                ),
                            )
                    # Resposta com erro = a Shopee NÃO publicou.
                    ln.prog["post_recusado"] = True
                    raise
                ln.prog["post_id"] = post_id
                await ln.salvar(post_external_id=post_id[:128])
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
        if ln.prog.get("post_chamado_em") and not ln.prog.get("post_recusado"):
            return await _revisar(ln, f"a Shopee não respondeu ({e.detalhe or e.code})")
        return await _aguardar(ln, f"a Shopee não respondeu ({e.detalhe or e.code})", falha=True)
    except ShopeeVideoError as e:
        gente = precisa_gente(e)
        if gente:
            return await _falhou(ln, f"{gente} ({e.texto()})")
        if e.tem(*_PASSAGEIRO):
            return await _aguardar(ln, f"a Shopee pediu para esperar ({e.texto()})", falha=True)
        return await _falhou(ln, f"a Shopee recusou ({e.texto()})")


class _PassageiroError(Exception):
    """Não deu pra perguntar agora — o próximo tique tenta de novo."""


# ──────────────────────────────────────────────────────────── reconciliação


async def reconciliar(session: AsyncSession, postagem_id: UUID) -> str:
    """Postagem da Shopee presa em `publicando` (worker morto) ou em
    `revisar` com upload: PERGUNTA à Shopee, nunca sobe de novo.

      • com post_id                → detalhe: no ar = publicado; apagado = revisar;
      • `post_video` nunca chamado → nada foi ao ar: volta pra fila e continua
                                     de onde parou (só se estava `publicando`);
      • `post_video` chamado sem id → procura o upload na lista de publicados;
                                     achou = publicado; senão = revisar.
    """
    p = await session.get(MarketingPostagem, postagem_id)
    if p is None:
        return REVISAR
    ln = _Linha(session, p)
    post_id = p.post_external_id or ln.prog.get("post_id")
    vid = p.container_id or ln.prog.get("video_upload_id")
    estava_publicando = p.status == STATUS_PUBLICANDO

    if not post_id and not ln.prog.get("post_chamado_em"):
        if estava_publicando:
            # Nenhuma chamada de publicar aconteceu: retomar é seguro.
            await ln.s.execute(
                update(MarketingPostagem)
                .where(MarketingPostagem.id == ln.id, MarketingPostagem.status == STATUS_PUBLICANDO)
                .values(
                    status=STATUS_PENDENTE,
                    claimed_at=None,
                    result="Shopee: o tique anterior parou no meio — continua de onde estava",
                )
            )
            await ln.s.commit()
            return AGUARDANDO
        return REVISAR

    if p.rede_social_id is None:
        return await _revisar(ln, "a conta foi apagada — não há como consultar a Shopee")
    try:
        cred = await conta_svc.credencial(p.rede_social_id)
    except conta_svc.ContaShopeeError as e:
        return await _revisar(ln, f"sem como consultar a Shopee: {e.mensagem}")
    cliente = cred.cliente()
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
            if int(d.get("status") or 0) == STATUS_RASCUNHO:
                return await _revisar(
                    ln,
                    "o vídeo ficou como RASCUNHO na Shopee e não foi publicado — "
                    "cancele esta linha e publique de novo",
                )
            if int(d.get("status") or 0) == STATUS_POSTADO and d.get("post_id"):
                return await _publicado(
                    ln, str(d["post_id"]), texto="reconciliado: a Shopee confirmou a publicação"
                )
    except ShopeeVideoError as e:
        return await _revisar(ln, f"a consulta à Shopee falhou ({e.texto()})")
    return await _revisar(ln, "a Shopee não encontrou o vídeo")
