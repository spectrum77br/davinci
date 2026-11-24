"""Aviso da Conferência Shopee no Threema quando a rodada fecha (contrato §7).

Molde do relatório da Denúncia (services/denuncia_relatorio_threema):

  • cada plataforma é um envio de Cadastros › Envios Threema (tarefa 16,
    F2/C9e: `marketing.conferencia_shopee`, `_ml` e `_amazon`, `CHAVE_ENVIO`):
    quem recebe e o liga/desliga vêm de lá (`ligado`). O `ativo` NULL segue a
    chave de hoje no .env (CONFERENCIA_SHOPEE_THREEMA, CONFERENCIA_ML_THREEMA,
    CONFERENCIA_AMAZON_THREEMA). A semente copiou o cadastro
    `conferencia_shopee` do Informar (o grupo "Conferência", o mesmo nos
    três), que fica congelado como reserva de emergência; o botão "Quem recebe
    no Threema" da aba virou atalho (só admin altera quem recebe);
  • remetente: o do catálogo, o ID do Threema de chamados (o mesmo da
    Denúncia) — o Threema não tem remetente de Marketing;
  • `threema_enviado_em` na execução carimba o envio: manda UMA vez, mesmo com
    restart. Desligado, sem ninguém ou com o envio falhando, não carimba — o
    varredor do worker tenta de novo enquanto a rodada for recente
    (`JANELA_REENVIO`, 6 h: desligado por mais que isso, o aviso daquela
    rodada se perde — o `descarta` do catálogo). Sem ninguém pela
    configuração antiga (emergência ou sem semente, com a linha vazia) fica
    calado, como antes; ninguém válido no Cadastro passa pelo núcleo, que
    grava o registro `ok=false` e acende o sino dos admins;
  • mensagem curta (≤ 3500 bytes, o limite do gateway básico): uma linha por
    grupo + Geral, quem ficou sem dados, afiliados incompletos, o link do
    Excel (baixa SEM login, assinado com o jwt_secret, vale 7 dias e só para
    aquela execução) e o link da aba no DaVinci.

Nada aqui faz commit: quem chama decide (rota do resultado ou worker).

Mercado Livre e Amazon (07/10/2026): o mesmo aviso e o mesmo cadastro "Quem
recebe", mas cada marketplace com a SUA chave — CONFERENCIA_ML_THREEMA e
CONFERENCIA_AMAZON_THREEMA, desligadas de fábrica: ligar a da Shopee depois do
piloto não começa a mandar o relatório do ML/Amazon (nem as rodadas de teste
de "Gerar agora") antes de ele ser aprovado. Desde a F2/C9e cada marketplace
é um envio próprio no Cadastro (liga, desliga e quem recebe separados; a
semente pôs o mesmo grupo nos três). O título diz a plataforma
("Conferência Mercado Livre — …"), o link do DaVinci abre a aba dela
(`&conf=ml`) e métrica com estado sai com o texto do estado ("invest.
aguardando acesso") em vez de "—". Com UMA das partes do investimento sem
número (ML: afiliados não coletados), a linha diz que o número é só do Ads.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ConferenciaShopeeExecucao
from app.services import threema_envios
from app.services.conferencia_shopee import calculo, plataformas, saida
from app.services.threema_catalogo import CATALOGO, K

logger = structlog.get_logger()

# A linha do Informar que era a lista dos três avisos (hoje só a reserva de
# emergência do Cadastro: o `Legado.informar` dos envios, no catálogo).
CONTEXTO = "conferencia_shopee"
VALE_LINK = timedelta(days=7)
# O varredor reenvia o que falhou enquanto a rodada for recente; rodada velha
# (Threema ligado depois, cadastro feito dias depois) não dispara aviso.
JANELA_REENVIO = timedelta(hours=6)
MAX_BYTES = 3500
# O envio do catálogo (Cadastros › Envios Threema) de cada plataforma: quem
# recebe, o liga/desliga e a marca do registro de cada mensagem.
CHAVE_ENVIO = {
    "shopee": K.MARKETING_CONFERENCIA_SHOPEE,
    "ml": K.MARKETING_CONFERENCIA_ML,
    "amazon": K.MARKETING_CONFERENCIA_AMAZON,
}
DESLIGADO = "aviso no Threema desligado"


# ─────────────────────────────────────────────── envio por plataforma


def chave_envio(plataforma: str | None) -> str:
    """O envio desta plataforma; vazia (rodada antiga) ou desconhecida =
    Shopee."""
    return CHAVE_ENVIO[plataformas.de({"plataforma": plataforma})]


async def ligado(plataforma: str | None, *, session: AsyncSession | None = None) -> bool:
    """O aviso desta plataforma está ligado? É o liga/desliga do envio dela em
    Cadastros › Envios Threema; com o `ativo` NULL, a chave de hoje no .env
    (cada marketplace tem a sua, desligadas de fábrica)."""
    return await threema_envios.ativo(chave_envio(plataforma), session=session)


async def plataformas_ligadas(session: AsyncSession | None = None) -> list[str]:
    return [p for p in plataformas.PLATAFORMAS if await ligado(p, session=session)]


# ─────────────────────────────────────────────── link do Excel (sem login)


def _assinatura(execucao_id: UUID | str, ate: int) -> str:
    chave = get_settings().jwt_secret.encode()
    msg = f"conferencia-shopee-excel:{execucao_id}:{ate}".encode()
    return hmac.new(chave, msg, hashlib.sha256).hexdigest()[:32]


def token_excel(execucao_id: UUID | str, agora: datetime) -> str:
    ate = int((agora + VALE_LINK).timestamp())
    return f"{ate}.{_assinatura(execucao_id, ate)}"


def confere_excel(execucao_id: UUID | str, token: str, agora: datetime) -> bool:
    """Token daquela execução, assinado aqui e ainda no prazo. O link é
    público (sem login): token torto é 403, nunca 500 — por isso só ASCII
    (o isdigit aceita "²" e o int() recusa; compare_digest de textos com
    acento levanta TypeError) e prazo de no máximo 12 dígitos (o int() recusa
    texto de mais de 4300)."""
    ate_txt, _, assinatura = (token or "").partition(".")
    if not (ate_txt.isascii() and ate_txt.isdigit() and len(ate_txt) <= 12):
        return False
    if not assinatura or not assinatura.isascii():
        return False
    if int(ate_txt) < agora.timestamp():
        return False
    return hmac.compare_digest(assinatura, _assinatura(execucao_id, int(ate_txt)))


def _base() -> str:
    return (get_settings().app_url or "").rstrip("/")


def link_excel(execucao_id: UUID | str, agora: datetime) -> str:
    return (
        f"{_base()}/api/marketing/conferencia-shopee/execucoes/{execucao_id}/excel/link"
        f"?t={token_excel(execucao_id, agora)}"
    )


def link_davinci(execucao_id: UUID | str, plataforma: str | None = None) -> str:
    conf = "" if (plataforma or plataformas.PADRAO) == plataformas.PADRAO else f"&conf={plataforma}"
    return f"{_base()}/marketing?aba=conferencia{conf}&execucao={execucao_id}"


# ─────────────────────────────────────────────── mensagem


# As duas partes do investimento (calculo.investimento) e como a mensagem as chama.
_PARTES_INVEST = {"invest_afiliados": "afiliados", "invest_ads": "Ads"}


def _entre_parenteses(var: Mapping[str, Any]) -> str:
    texto = var.get("texto") or "—"
    return "" if texto == "—" else f" ({texto})"


def _linha_grupo(
    rotulo: str, total: Mapping[str, Any], estados: Mapping[str, str] | None = None
) -> str:
    """"Mala: vendas R$ 120.345 (▲ 8,2%) · invest. R$ 9.876 · 8,2% s/ vendas
    (▼ 0,4 p.p.)" — S1 comparada com S2. Com estado nos dois investimentos
    (Amazon sem Ads): "invest. aguardando acesso · % s/ vendas aguardando
    acesso". Com estado em UM só (ML: afiliados não coletados), o número diz de
    qual parte é — "invest. Ads R$ 182 (afiliados não coletado) · 17,7% Ads s/
    vendas" —, para não parecer comparável com o investimento total da Shopee."""
    estados = estados or {}
    semanas = list(total.get("semanas") or [])
    s1 = semanas[0] if semanas else {}
    var_vendas, _ = calculo.variacoes(semanas, "vendas")
    var_pct, _ = calculo.variacoes(semanas, "pct")
    invest = f"invest. {calculo.dinheiro(calculo.investimento(s1), 0)}"
    s_vendas = "s/ vendas"
    com_estado = [k for k in _PARTES_INVEST if k in estados]
    if len(com_estado) == 2:
        invest = f"invest. {plataformas.rotulo_estado(estados['invest_ads'])}"
    elif com_estado:
        sem = com_estado[0]
        com = next(k for k in _PARTES_INVEST if k != sem)
        invest = (
            f"invest. {_PARTES_INVEST[com]} {calculo.dinheiro(calculo.investimento(s1), 0)}"
            f" ({_PARTES_INVEST[sem]} {plataformas.rotulo_estado(estados[sem])})"
        )
        s_vendas = f"{_PARTES_INVEST[com]} s/ vendas"
    pct = f"{calculo.percentual(s1.get('pct'))} {s_vendas}{_entre_parenteses(var_pct)}"
    if "pct" in estados:
        pct = f"% s/ vendas {plataformas.rotulo_estado(estados['pct'])}"
    return (
        f"{rotulo}: vendas {calculo.dinheiro(s1.get('vendas'), 0)}{_entre_parenteses(var_vendas)}"
        f" · {invest} · {pct}"
    )


def _lista(nomes: Sequence[str], limite: int | None) -> str:
    if limite is None or len(nomes) <= limite:
        return ", ".join(nomes)
    return ", ".join(nomes[:limite]) + f" e mais {len(nomes) - limite}"


def _ddmm(dia: str | None) -> str:
    d = dia or ""
    return f"{d[8:10]}/{d[5:7]}" if len(d) >= 10 else d


def texto(rel: Mapping[str, Any], link: str, link_davinci: str) -> str:
    """A mensagem inteira (≤ 3500 bytes: as listas encurtam se precisar)."""
    cabecalho = f"📊 {saida.titulo(rel)} ({saida.tipo_texto(rel)})"
    estados = saida.estados(rel)
    # M-23 (23/11/2026): os grupos em ordem alfabética (Celular, Eletro, Mala),
    # como na tela e no Excel; o Geral por último.
    grupos = [
        _linha_grupo(g.get("rotulo") or "", g.get("total") or {}, estados)
        for g in saida.grupos_em_ordem(rel)
        if (g.get("total") or {}).get("contas")
    ]
    grupos.append(_linha_grupo("Geral", rel.get("geral") or {}, estados))
    sem_dados = [
        f"{d.get('conta')} ({saida.ROTULO_STATUS.get(d.get('status') or '', d.get('status'))})"
        for d in rel.get("contas_sem_dados") or []
    ]
    incompletos = [
        f"{d.get('conta')} (até {_ddmm(d.get('ate'))})"
        for d in rel.get("afiliados_incompletos") or []
    ]
    rodape = [f"📎 Excel: {link}", f"🔗 No DaVinci: {link_davinci}"]

    msg = ""
    for limite in (None, 8, 3, 0):
        linhas = [cabecalho, *grupos]
        if sem_dados:
            linhas.append(f"⚠️ Sem dados: {_lista(sem_dados, limite)}")
        if incompletos:
            linhas.append(f"⚠️ Afiliados incompletos: {_lista(incompletos, limite)}")
        msg = "\n".join([*linhas, *rodape])
        if len(msg.encode("utf-8")) <= MAX_BYTES:
            return msg
    # Não cabe nem com as listas cortadas (app_url gigante?): os links ficam.
    corpo = "\n".join(rodape)
    sobra = MAX_BYTES - len(corpo.encode("utf-8")) - 1
    inicio = msg.encode("utf-8")[: max(0, sobra)].decode("utf-8", "ignore")
    if inicio:
        return f"{inicio}\n{corpo}"
    return corpo.encode("utf-8")[:MAX_BYTES].decode("utf-8", "ignore")


# ─────────────────────────────────────────────── envio


async def _por_que_nao_sai(session: AsyncSession, chave: str) -> str | None:
    """Por que o aviso NÃO sai agora (None = sai). Quem recebe vem de
    Cadastros › Envios Threema, com o remetente do catálogo:

    * o canal do remetente desligado no servidor (o `client.disabled` de
      antes, lido sem criar o cliente aqui);
    * o envio desligado;
    * ninguém pela configuração antiga (emergência ou sem semente, com a
      linha `conferencia_shopee` vazia): calado, como antes.

    Ninguém válido no Cadastro sai daqui como None: o envio passa pelo núcleo,
    que grava o registro `ok=false` e acende o sino dos admins."""
    if threema_envios.canal(CATALOGO[chave].remetente or "chamados") == "desativado":
        return "Threema de chamados desativado"
    quem = await threema_envios.resolver(chave, session=session)
    if not quem.ativo:
        return DESLIGADO
    if not quem.ids and quem.motivo != threema_envios.NINGUEM_VALIDO:
        return "ninguém cadastrado"
    return None


async def enviar_pendente(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao, agora: datetime | None = None
) -> dict:
    """Manda o aviso da execução pronta, se ainda não foi. Trava a linha da
    execução enquanto manda: a rota e o varredor juntos não mandam dois.

    Um destinatário por vez (`um_por_vez` do núcleo): o `send_to_all` só
    segura o erro do Threema, e um timeout de rede no 2º escapava depois de o
    1º já ter recebido — sem carimbo, o varredor reenviava a todos de 10 em
    10 min. Basta UM entregue para carimbar."""
    agora = agora or datetime.now(UTC)
    if not await ligado(getattr(execucao, "plataforma", None), session=session):
        return {"enviado": False, "motivo": DESLIGADO}
    row = (
        await session.execute(
            select(ConferenciaShopeeExecucao)
            .where(ConferenciaShopeeExecucao.id == execucao.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None or row.status != "pronto" or not row.relatorio:
        return {"enviado": False, "motivo": "sem relatório"}
    if row.threema_enviado_em is not None:
        return {"enviado": False, "motivo": "já enviado"}
    chave = chave_envio(row.plataforma)
    motivo = await _por_que_nao_sai(session, chave)
    if motivo:
        if motivo != DESLIGADO:
            logger.warning(
                "conferencia_shopee_threema_sem_destino", execucao=str(row.id), motivo=motivo
            )
        return {"enviado": False, "motivo": motivo}
    msg = texto(
        row.relatorio, link_excel(row.id, agora), link_davinci(row.id, row.plataforma)
    )
    r = await threema_envios.enviar(chave, msg, session=session, um_por_vez=True)
    if r.get("motivo"):
        # Desligou entre a leitura e o envio, ou ninguém válido no Cadastro (o
        # núcleo gravou o registro e acendeu o sino): não carimba — o varredor
        # tenta de novo enquanto a rodada for recente.
        if r["motivo"] == threema_envios.DESLIGADO:
            motivo = DESLIGADO
        elif r.get("via") == "legado":
            motivo = "ninguém cadastrado"
        else:
            motivo = "ninguém válido para receber"
        logger.warning("conferencia_shopee_threema_nao_saiu", execucao=str(row.id), motivo=motivo)
        return {"enviado": False, "motivo": motivo}
    if not r.get("sent"):
        logger.warning(
            "conferencia_shopee_threema_falhou",
            execucao=str(row.id),
            falhou=len(r.get("failed") or []),
            erro=r.get("erro"),
        )
        return {"enviado": False, "motivo": "envio falhou"}
    row.threema_enviado_em = agora
    await session.flush()
    logger.info(
        "conferencia_shopee_threema",
        execucao=str(row.id),
        sent=len(r["sent"]),
        failed=len(r.get("failed") or []),
        via=r.get("via"),
    )
    return {
        "enviado": True,
        "execucao": str(row.id),
        "sent": r["sent"],
        "failed": r.get("failed") or [],
    }


async def enviar_pendentes(session: AsyncSession, agora: datetime | None = None) -> list[dict]:
    """O varredor: avisa as execuções prontas nas últimas horas que ainda não
    foram avisadas (o envio na hora falhou ou não tinha ninguém cadastrado) —
    só das plataformas com o aviso ligado."""
    agora = agora or datetime.now(UTC)
    ligadas = await plataformas_ligadas(session)
    if not ligadas:
        return []
    pendentes = (
        await session.execute(
            select(ConferenciaShopeeExecucao)
            .where(
                ConferenciaShopeeExecucao.plataforma.in_(ligadas),
                ConferenciaShopeeExecucao.status == "pronto",
                ConferenciaShopeeExecucao.threema_enviado_em.is_(None),
                ConferenciaShopeeExecucao.finalizado_em >= agora - JANELA_REENVIO,
            )
            .order_by(ConferenciaShopeeExecucao.finalizado_em)
        )
    ).scalars().all()
    return [await enviar_pendente(session, ex, agora) for ex in pendentes]
