# ruff: noqa: S608 — schema interpolado vem de DATABASE_SCHEMA (config
# confiável, nunca input de usuário); mesmo padrão do job_details.
"""Produtos › botões Atualizar e Vincular (17/11/2026).

ATUALIZAR (`run_atualizar`, fila davinci_sync): só LÊ do Bling e dos
marketplaces e só GRAVA no DaVinci.
  1. Produtos novos do Bling: lista inteira, cria só o que falta — não
     reativa nem inativa ninguém (`alterar_situacao=False`), com a trava
     'pbln' do tick de 15 min, solta assim que a leitura do Bling termina.
  2. Lista de anúncios (tabela `listings`, a aba Anúncios) das contas do
     escopo de quem clicou: ML, Shopee, TikTok, Magalu e Carrefour, 4 ao
     mesmo tempo, cada uma na sua sessão. Só upsert: anúncio que não veio
     nunca é apagado; encerrado que o DaVinci não conhecia não é criado; o
     tipo do anúncio do ML (Margem) não muda. Amazon fica fora da lista.
  3. Vincular automático nas contas lidas (+ as Amazon do escopo, lidas ao
     vivo), com o espelho na aba Anúncios — o mesmo motor do botão Vincular.

  Decisão do dono (17/11/2026): o robô do Flex usa a lista do ML para
  DESLIGAR o Flex de anúncio com variação sem vínculo. Para a lista fresca
  não chegar a ele sem vínculo, o Atualizar segura a trava da rodada do Flex
  (a mesma `_MOTOR_LOCK_KEY` de services/flex_motor.py) — e só pelo tempo do
  que o Flex lê (revisão de 17/11/2026):
    a. listas do TikTok, Magalu e Carrefour (o Flex não lê): sem a trava;
    b. com a trava: o Vincular das contas ML e Shopee PRIMEIRO (ele lê a conta
       ao vivo, não a tabela), e só depois a lista de cada conta cujo Vincular
       terminou 'ok' — a conta pendente/com erro fica com a lista velha. Um
       cancelamento em qualquer ponto (publicação, timeout) deixa a lista velha
       ou uma lista já vinculada; nunca variação nova sem vínculo. Depois o
       espelho dessas contas e a trava solta;
    c. o Vincular do TikTok, Magalu e das Amazon, já sem a trava.
  Rodada do Flex em andamento: espera (tentando a cada 5 s) até 10 min; sem
  conseguir, não lê ML e Shopee (contas puladas) e segue com o resto.

  Enquanto o Atualizar segura a trava, quem chama a rodada do Flex recebe
  'ocupado' (sem erro): o cron sai e a rodada seguinte faz tudo; o botão
  Sincronizar do Flex, a aprovação e o gancho do pedido Flex tentam de novo a
  cada 1 min e, esgotadas as tentativas, deixam para a varredura (a aprovação
  continua valendo) — ver worker.py.

VINCULAR: o motor é `auto_link.vincular_contas` (o botão chama
`/api/jobs/auto-link`); aqui ficam a lista de contas do botão
(`contas_do_botao`) e o espelho na aba Anúncios (`espelhar_vinculos_na_lista`).
Nenhum dos dois escreve no marketplace nem no Bling.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.deps.team_scope import resolve_team_scope
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    User,
)
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.job_details import append_job_detail, sinal_de_vida

logger = structlog.get_logger()

# Plataformas com leitor da lista de anúncios (listings_import._client_for_listings).
PLATAFORMAS_LISTA = frozenset(
    {
        IntegrationPlatform.ML,
        IntegrationPlatform.SHOPEE,
        IntegrationPlatform.TIKTOK,
        IntegrationPlatform.MAGALU,
        IntegrationPlatform.CARREFOUR,
        IntegrationPlatform.TEMU,
    }
)
# Plataformas com leitor de vínculo (auto_link._ADAPTED_PLATFORMS sem o Bling).
PLATAFORMAS_VINCULO = frozenset(
    {
        IntegrationPlatform.ML,
        IntegrationPlatform.SHOPEE,
        IntegrationPlatform.TIKTOK,
        IntegrationPlatform.AMAZON,
        IntegrationPlatform.MAGALU,
        IntegrationPlatform.TEMU,
    }
)
# Listas que o robô do Flex lê (flex_motor.montar_anuncios: só ML e Shopee).
_LISTAS_DO_FLEX = frozenset({IntegrationPlatform.ML, IntegrationPlatform.SHOPEE})

# Contas lidas ao mesmo tempo (cada uma na sua sessão). Pool do worker_sync = 30.
ATUALIZAR_CONCORRENCIA = 4
# Espera pela rodada do Flex (que segura a trava de 4 a 8 min). Módulo-level
# para os testes encurtarem.
FLEX_ESPERA_MAX_S = 600.0
FLEX_ESPERA_INTERVALO_S = 5.0

MSG_FLEX_OCUPADO = "o robô do Flex estava rodando; tente de novo em alguns minutos"
MSG_SHOPEE_OCUPADA = "a rodada automática da Shopee está lendo agora"
MSG_VINCULO_NAO_CONFERIDO = (
    "o Vincular desta conta não terminou; a lista não foi gravada (tente de novo)"
)
# Prazo da leitura da lista de UMA conta (o mesmo teto do Vincular no TikTok):
# uma conta travada não segura o job (nem a trava do Flex) até o timeout do arq.
LISTA_TIMEOUT_S = 900.0
DICA_LENTO = "o marketplace está lento; tente de novo mais tarde"
# Os códigos do resumo do Bling, em texto para a pessoa.
MOTIVO_BLING_PULADO = {
    "rodada_automatica_em_andamento": (
        "a rodada automática de 15 min estava criando os mesmos produtos"
    ),
}

_TERMINAIS = (
    BackgroundJobStatus.SUCCEEDED,
    BackgroundJobStatus.FAILED,
    BackgroundJobStatus.CANCELLED,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _schema() -> str:
    return get_settings().database_schema


# ───────────────────────────── contas do botão ──────────────────────────────


async def contas_do_botao(
    session: AsyncSession,
    user: User,
    plataformas: Collection[IntegrationPlatform | str],
) -> list[Integration]:
    """As contas que o botão lê, montadas no SERVIDOR (o corpo do pedido só
    estreita): não arquivadas, sem o Bling, das `plataformas` pedidas e,
    para usuário de equipe (Londres/Israel/Marrocos), só as da equipe dele.
    Admin e quem não tem equipe: todas."""
    pedidas = [IntegrationPlatform(p) for p in plataformas]
    if not pedidas:
        return []
    stmt = (
        select(Integration)
        .where(
            Integration.archived_at.is_(None),
            Integration.platform != IntegrationPlatform.BLING,
            Integration.platform.in_(pedidas),
        )
        .order_by(Integration.platform, Integration.name)
    )
    escopo = await resolve_team_scope(session, user)
    if not escopo.unrestricted:
        if not escopo.integration_ids:
            return []
        stmt = stmt.where(Integration.id.in_(list(escopo.integration_ids)))
    return list((await session.execute(stmt)).scalars())


# ─────────────────────── espelho na aba Anúncios ────────────────────────────

_NORM = "lower(btrim(regexp_replace(COALESCE({c}, ''), '\\s+', ' ', 'g')))"
_AMOSTRA_ESPELHO = 50


async def espelhar_vinculos_na_lista(session: AsyncSession, ids: list[UUID]) -> dict[str, Any]:
    """Acerta o produto mostrado na aba Anúncios (`listings.product_id`) pelo
    que os vínculos VIVOS dizem, nas contas `ids` não arquivadas. Um UPDATE só.

    Para cada linha da lista:
      - o produto do vínculo vivo do mesmo anúncio (conta, plataforma e
        external_id) cujo SKU guardado é o SKU que a linha mostra (o mais
        recente, se houver mais de um);
      - senão, se os vínculos vivos do anúncio dão exatamente 1 produto, ele;
      - senão, nada muda.
    Só grava quando muda. NUNCA mexe em `listings.sku` (o SKU lido do
    marketplace) nem em anúncio sem vínculo vivo (pode ser ligação manual).
    Não dá commit: quem chama abre a sessão (com lock_timeout) e confirma."""
    vazio = {"passou_a_ter_produto": 0, "trocou_produto": 0, "amostra": []}
    if not ids:
        return vazio
    sc = _schema()
    sql = text(
        f"""
        WITH vivos AS (
            SELECT pl.integration_id, pl.platform, pl.external_id, pl.product_id,
                   {_NORM.format(c="pl.external_sku")} AS sku_n, pl.updated_at
            FROM "{sc}".product_links pl
            WHERE pl.integration_id = ANY(:ids)
              AND pl.morto_desde IS NULL
              AND pl.platform <> 'bling'
        ),
        por_anuncio AS (
            SELECT integration_id, platform, external_id,
                   (array_agg(DISTINCT product_id))[1] AS unico,
                   COUNT(DISTINCT product_id) AS n
            FROM vivos
            GROUP BY integration_id, platform, external_id
        ),
        lst AS (
            SELECT l.id, l.integration_id, l.platform, l.external_id,
                   l.product_id AS atual, {_NORM.format(c="l.sku")} AS sku_n
            FROM "{sc}".listings l
            JOIN "{sc}".integrations i ON i.id = l.integration_id
            WHERE l.integration_id = ANY(:ids)
              AND i.archived_at IS NULL
        ),
        pelo_sku AS (
            SELECT DISTINCT ON (lst.id) lst.id, v.product_id
            FROM lst
            JOIN vivos v
              ON v.integration_id = lst.integration_id
             AND v.platform = lst.platform
             AND v.external_id = lst.external_id
             AND v.sku_n = lst.sku_n
            WHERE lst.sku_n <> ''
            ORDER BY lst.id, v.updated_at DESC
        ),
        alvo AS (
            SELECT lst.id, lst.atual,
                   COALESCE(ps.product_id, CASE WHEN pa.n = 1 THEN pa.unico END) AS novo
            FROM lst
            LEFT JOIN pelo_sku ps ON ps.id = lst.id
            LEFT JOIN por_anuncio pa
              ON pa.integration_id = lst.integration_id
             AND pa.platform = lst.platform
             AND pa.external_id = lst.external_id
        )
        UPDATE "{sc}".listings l
           SET product_id = a.novo
          FROM alvo a
         WHERE l.id = a.id
           AND a.novo IS NOT NULL
           AND a.novo IS DISTINCT FROM a.atual
        RETURNING
            a.atual IS NULL AS passou,
            (SELECT i.name FROM "{sc}".integrations i WHERE i.id = l.integration_id) AS conta,
            l.external_id AS anuncio,
            l.sku AS sku_anuncio,
            (SELECT p.sku FROM "{sc}".products p WHERE p.id = a.atual) AS de,
            (SELECT p.sku FROM "{sc}".products p WHERE p.id = a.novo) AS para
        """
    )
    linhas = (await session.execute(sql, {"ids": list(ids)})).all()
    out = dict(vazio)
    out["passou_a_ter_produto"] = sum(1 for r in linhas if r.passou)
    out["trocou_produto"] = sum(1 for r in linhas if not r.passou)
    out["amostra"] = [
        {
            "conta": r.conta,
            "anuncio": r.anuncio,
            "sku_anuncio": r.sku_anuncio,
            "de": r.de,
            "para": r.para,
        }
        for r in linhas[:_AMOSTRA_ESPELHO]
    ]
    return out


# ───────────────────────────────── ATUALIZAR ─────────────────────────────────


def _dica(texto: str) -> str | None:
    """Dica para a pessoa quando a conta recusou a leitura por credencial
    (Magalu VR e vortan com invalid_grant desde 11/11/2026)."""
    t = texto.lower()
    chaves = (
        "invalid_grant",
        "invalid_token",
        "invalid access_token",
        "access token expired",
        "unauthorized",
        "status=401",
        " 401",
        "refresh_token",
    )
    if any(k in t for k in chaves):
        return "reconectar a conta em Integrações"
    return None


async def _tentar_trava(trava: AsyncSession, chave: int) -> bool:
    r = await trava.execute(
        text("SELECT pg_try_advisory_xact_lock(:ns, :k)"), {"ns": SYNC_NAMESPACE, "k": chave}
    )
    return bool(r.scalar())


async def _esperar_trava_flex(trava: AsyncSession, chave: int) -> tuple[bool, float]:
    """Tenta a trava da rodada do Flex a cada FLEX_ESPERA_INTERVALO_S até
    FLEX_ESPERA_MAX_S. Devolve (pegou, segundos esperando)."""
    inicio = time.monotonic()
    while True:
        if await _tentar_trava(trava, chave):
            return True, time.monotonic() - inicio
        esperou = time.monotonic() - inicio
        if esperou >= FLEX_ESPERA_MAX_S:
            return False, esperou
        await asyncio.sleep(FLEX_ESPERA_INTERVALO_S)


async def _avancar(session: AsyncSession, job_id: UUID) -> None:
    """processed += 1 RELATIVO (as contas gravam em paralelo, cada uma na sua
    sessão) + sinal de vida. Não dá commit."""
    await session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job_id)
        .values(
            processed=func.coalesce(BackgroundJob.processed, 0) + 1,
            last_heartbeat_at=_now(),
        )
    )


async def _etapa_bling(job_id: UUID) -> dict[str, Any]:
    """Produtos novos do Bling: lista inteira, só cria o que falta, com a
    trava 'pbln' (solta no fim desta etapa)."""
    from app.services.bling_produtos_novos import importar_produtos_novos_com_trava

    try:
        r = await importar_produtos_novos_com_trava(completo=True, alterar_situacao=False)
    except Exception as e:  # noqa: BLE001 — o Bling fora não derruba as listas
        logger.warning("atualizar_bling_falhou", job_id=str(job_id), erro=str(e)[:300])
        r = {"erro": f"{type(e).__name__}: {e}"[:300]}
    bling = {
        "lidos": int(r.get("lidos") or 0),
        "faltando": int(r.get("faltando") or 0),
        "criados": int(r.get("criados") or 0),
        "falhas": int(r.get("falhas") or 0),
        "ja_existia": int(r.get("ja_existia") or 0),
        "skus_criados": list(r.get("skus_criados") or [])[:50],
        "pulado": r.get("pulado"),
        "erro": r.get("erro"),
    }
    async with session_scope() as s:
        await append_job_detail(s, job_id, {"etapa": "bling", **bling})
        await _avancar(s, job_id)
    return bling


async def _ler_conta(
    job_id: UUID, iid: UUID, *, pular: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Lê a lista de UMA conta na sua sessão. Erro fica na conta (com dica),
    não derruba as outras. Toda conta grava o seu detalhe, inclusive a pulada
    (a tela conta os detalhes 'conta' para a barra). `pular` = por que NÃO ler
    ({'erro', e opcionais 'resultado' (padrão 'pulada') e 'dica'}): Flex
    ocupado, Shopee ocupada, Vincular da conta que não terminou."""
    from app.services.listings_import import importar_anuncios_conta

    det: dict[str, Any] = {
        "etapa": "conta",
        "integration_id": str(iid),
        "nome": None,
        "plataforma": None,
        "resultado": "pulada",
        "novos": 0,
        "atualizados": 0,
        "ignorados": 0,
        "leitura_completa": None,
        "erro": None,
        "dica": None,
    }
    async with session_scope() as cs:
        integ = await cs.get(Integration, iid)
        if integ is None or integ.archived_at is not None:
            det["erro"] = "conta não encontrada ou arquivada"
        else:
            plataforma = integ.platform
            det["nome"] = integ.name
            det["plataforma"] = plataforma.value
            if plataforma not in PLATAFORMAS_LISTA:
                det["erro"] = "sem leitura da lista"
            elif pular:
                det.update(
                    resultado=pular.get("resultado") or "pulada",
                    erro=pular.get("erro"),
                    dica=pular.get("dica"),
                )
            else:
                try:
                    r = await asyncio.wait_for(
                        importar_anuncios_conta(cs, integ), timeout=LISTA_TIMEOUT_S
                    )
                    det.update(
                        resultado="ok",
                        novos=int(r.get("created") or 0),
                        atualizados=int(r.get("updated") or 0),
                        ignorados=int(r.get("skipped") or 0),
                        leitura_completa=r.get("leitura_completa"),
                    )
                except Exception as e:  # noqa: BLE001 — uma conta não derruba as outras
                    await cs.rollback()
                    if isinstance(e, TimeoutError):
                        texto = str(e) or (
                            f"a leitura da lista passou de {LISTA_TIMEOUT_S / 60:.0f} min"
                        )
                        dica = DICA_LENTO
                    else:
                        texto = f"{type(e).__name__}: {e}"
                        dica = _dica(texto)
                    det.update(resultado="falha", erro=texto[:300], dica=dica)
                    logger.warning(
                        "atualizar_conta_falhou",
                        job_id=str(job_id),
                        integration_id=str(iid),
                        conta=det["nome"],
                        erro=texto[:300],
                    )
        await append_job_detail(cs, job_id, det)
        await _avancar(cs, job_id)
    return det


async def _ler_listas(
    job_id: UUID, ids: list[UUID], *, pular: dict[UUID, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(ATUALIZAR_CONCORRENCIA)
    pular = pular or {}

    async def _uma(iid: UUID) -> dict[str, Any]:
        async with sem:
            return await _ler_conta(job_id, iid, pular=pular.get(iid))

    # return_exceptions: todas terminam antes de seguir (a trava do Flex só é
    # solta depois que nenhuma conta está mais gravando lista).
    resultados = await asyncio.gather(*[_uma(i) for i in ids], return_exceptions=True)
    detalhes: list[dict[str, Any]] = []
    for iid, r in zip(ids, resultados, strict=True):
        if isinstance(r, BaseException):
            logger.warning(
                "atualizar_conta_quebrou", job_id=str(job_id), integration_id=str(iid),
                erro=str(r)[:300],
            )
            r = {
                "etapa": "conta", "integration_id": str(iid), "nome": None,
                "plataforma": None, "resultado": "falha", "novos": 0, "atualizados": 0,
                "ignorados": 0, "leitura_completa": None,
                "erro": f"{type(r).__name__}: {r}"[:300], "dica": None,
            }
        detalhes.append(r)
    return detalhes


def _resumo_anuncios(detalhes: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "contas": len(detalhes),
        "ok": sum(1 for d in detalhes if d["resultado"] == "ok"),
        "falhas": sum(1 for d in detalhes if d["resultado"] == "falha"),
        "puladas": sum(1 for d in detalhes if d["resultado"] == "pulada"),
        "novos": sum(int(d.get("novos") or 0) for d in detalhes),
        "atualizados": sum(int(d.get("atualizados") or 0) for d in detalhes),
    }


def _ids_do_vincular(detalhes: list[dict[str, Any]], amazon_ids: list[UUID]) -> list[UUID]:
    """O Vincular do fim (fora da trava do Flex) roda nas contas LIDAS que o
    Flex não lê (ok, ou falha no meio) com leitor de vínculo — TikTok e Magalu
    —, mais as Amazon do escopo (lidas ao vivo, fora da lista). ML e Shopee
    são vinculadas antes, dentro da trava."""
    ids: list[UUID] = []
    for d in detalhes:
        if d["resultado"] not in ("ok", "falha") or not d.get("plataforma"):
            continue
        if IntegrationPlatform(d["plataforma"]) in PLATAFORMAS_VINCULO:
            ids.append(UUID(d["integration_id"]))
    for i in amazon_ids:
        if i not in ids:
            ids.append(i)
    return ids


def _status_final(resultado: dict[str, Any]) -> tuple[BackgroundJobStatus, str | None]:
    """FAILED só quando NADA deu certo: nenhuma conta lida e o Bling também
    não rodou. O parcial fica SUCCEEDED, com as falhas/puladas no resultado.
    Vincular que quebrou ou deixou conta pendente: SUCCEEDED com o aviso em
    `error` ('Vincular incompleto: …'), como o botão Vincular já faz."""
    a = resultado.get("anuncios") or {}
    b = resultado.get("bling") or {}
    bling_ok = not b.get("erro") and not b.get("pulado")
    if not a.get("ok") and not bling_ok:
        partes = ["nenhuma lista lida"]
        if b.get("erro"):
            partes.append(f"Bling: {b['erro']}")
        elif b.get("pulado"):
            motivo = MOTIVO_BLING_PULADO.get(str(b["pulado"]), str(b["pulado"]))
            partes.append(f"Bling pulado: {motivo}")
        return BackgroundJobStatus.FAILED, "; ".join(partes)[:1000]
    v = resultado.get("vincular") or {}
    pendentes = int(v.get("pending_integrations") or 0)
    if resultado.get("vincular_erro") or pendentes:
        partes = []
        if pendentes:
            partes.append(f"{pendentes} conta(s) pendente(s)")
        if resultado.get("vincular_erro"):
            partes.append(str(resultado["vincular_erro"]))
        return BackgroundJobStatus.SUCCEEDED, ("Vincular incompleto: " + "; ".join(partes))[:1000]
    return BackgroundJobStatus.SUCCEEDED, None


async def _gravar_fim(
    job_id: UUID,
    status: BackgroundJobStatus,
    erro: str | None,
    resultado: dict[str, Any],
) -> None:
    """Estado final numa sessão limpa: o job nunca fica RUNNING sem dono (a
    trava de massa é global)."""
    try:
        async with session_scope() as s:
            await s.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .values(
                    status=status,
                    error=erro,
                    result=resultado,
                    finished_at=_now(),
                    last_heartbeat_at=_now(),
                )
            )
    except Exception:  # noqa: BLE001
        logger.exception("atualizar_fim_falhou", job_id=str(job_id))


async def _fase(job_id: UUID, fase: str, **extra: Any) -> None:
    """Marca na trilha do job em que fase o Atualizar está (a tela mostra o
    texto da última: esperando o Flex, vinculando, lendo ML/Shopee…)."""
    try:
        async with session_scope() as s:
            await append_job_detail(s, job_id, {"etapa": "fase", "fase": fase, **extra})
    except Exception:  # noqa: BLE001 — é só o texto da tela
        logger.warning("atualizar_fase_falhou", job_id=str(job_id), fase=fase)


async def _plataformas(ids: list[UUID]) -> dict[UUID, IntegrationPlatform]:
    if not ids:
        return {}
    async with session_scope() as s:
        linhas = await s.execute(
            select(Integration.id, Integration.platform).where(Integration.id.in_(ids))
        )
        return dict(linhas.tuples().all())


async def _vincular(
    job_id: UUID,
    ids: list[UUID],
    resumo_v: dict[str, Any],
    resultado: dict[str, Any],
) -> dict[UUID, dict[str, Any]]:
    """O Vincular (o motor do botão) nestas contas, somando em `resumo_v`.
    Erro inesperado vira `vincular_erro` (as contas que terminaram ficam no
    `por_conta` devolvido)."""
    from app.services.auto_link import vincular_contas

    por_conta: dict[UUID, dict[str, Any]] = {}
    resultado["vincular"] = resumo_v
    await _fase(job_id, "vincular", contas=len(ids))
    try:
        await vincular_contas(
            job_id, ids, resumo_v,
            etapa="vincular", contar_processed=False, por_conta=por_conta,
        )
    except Exception as e:  # noqa: BLE001 — o resto do Atualizar segue
        logger.exception("atualizar_vincular_falhou", job_id=str(job_id))
        anterior = resultado.get("vincular_erro")
        texto = f"{type(e).__name__}: {e}"[:300]
        resultado["vincular_erro"] = f"{anterior}; {texto}"[:600] if anterior else texto
    return por_conta


async def _espelho(job_id: UUID, ids: list[UUID], resumo_v: dict[str, Any]) -> None:
    """O espelho na aba Anúncios destas contas, somado ao que já foi espelhado
    (o Atualizar espelha em duas vezes: ML/Shopee dentro da trava, o resto
    depois)."""
    from app.services.auto_link import _espelhar_na_lista

    if not ids:
        return
    parcial: dict[str, Any] = {}
    await _espelhar_na_lista(job_id, ids, parcial)
    if "lista_anuncios" in parcial:
        novo = parcial["lista_anuncios"]
        acc = resumo_v.setdefault(
            "lista_anuncios", {"passou_a_ter_produto": 0, "trocou_produto": 0, "amostra": []}
        )
        acc["passou_a_ter_produto"] += int(novo.get("passou_a_ter_produto") or 0)
        acc["trocou_produto"] += int(novo.get("trocou_produto") or 0)
        acc["amostra"] = (list(acc.get("amostra") or []) + list(novo.get("amostra") or []))[
            :_AMOSTRA_ESPELHO
        ]
    if "lista_anuncios_erro" in parcial:
        resumo_v["lista_anuncios_erro"] = parcial["lista_anuncios_erro"]


def _sem_lista(info: dict[str, Any] | None) -> dict[str, Any]:
    """Por que a lista desta conta ML/Shopee não foi gravada (o Vincular dela
    não terminou 'ok'): com o erro que o Vincular viu é 'falha' (com a dica —
    ex.: reconectar a conta); sem erro conhecido, 'pulada' com o aviso."""
    erro = (info or {}).get("error")
    if not erro:
        return {"erro": MSG_VINCULO_NAO_CONFERIDO}
    texto = f"{erro} — o Vincular não conseguiu ler a conta; a lista não foi gravada"
    return {"resultado": "falha", "erro": texto[:300], "dica": _dica(str(erro))}


async def run_atualizar(job_id: UUID, integration_ids: list[UUID]) -> None:
    """O job do botão Atualizar (ver o cabeçalho do módulo).

    Idempotente: rodada repetida pelo arq com o job já terminado não faz
    nada; com o job ainda RUNNING (worker morto no meio) recomeça do zero —
    upsert na lista, criação do Bling pelo id/SKU e o Vincular pela chave
    única. Timeout/reinício no meio → FAILED 'interrompido'."""
    from app.services.auto_link import novo_resumo_vincular
    from app.services.flex_motor import _MOTOR_LOCK_KEY
    from app.services.listings_import import _SHOPEE_SWEEP_LOCK_KEY

    async with session_scope() as s:
        job = await s.get(BackgroundJob, job_id)
        if job is None:
            logger.error("atualizar_job_missing", job_id=str(job_id))
            return
        if job.finished_at is not None or job.status in _TERMINAIS:
            logger.info("atualizar_ja_terminado", job_id=str(job_id), status=str(job.status))
            return
        payload = dict(job.payload or {})
        job.status = BackgroundJobStatus.RUNNING
        job.started_at = _now()
        job.last_heartbeat_at = _now()
        job.processed = 0
        job.total = len(integration_ids) + 1  # as contas + o Bling
        job.error = None
        job.result = None
        # Rodada repetida (worker morto no meio): os detalhes da anterior saem,
        # senão a barra (detalhes 'conta' / contas) passava de 100%.
        await s.execute(
            text(f'DELETE FROM "{_schema()}".background_job_details WHERE job_id = :jid'),
            {"jid": str(job_id)},
        )

    amazon_ids = [UUID(str(i)) for i in payload.get("vincular_amazon_ids") or []]
    resultado: dict[str, Any] = {
        "bling": None,
        "anuncios": _resumo_anuncios([]),
        "vincular": None,
        "flex": {"esperou_s": 0, "conseguiu": None},
        "sem_leitura": list(payload.get("sem_leitura") or []),
    }
    status_final: BackgroundJobStatus | None = None
    erro: str | None = None
    try:
        async with sinal_de_vida(job_id):
            resultado["bling"] = await _etapa_bling(job_id)

            plataformas = await _plataformas(list(integration_ids))
            flex_ids = [i for i in integration_ids if plataformas.get(i) in _LISTAS_DO_FLEX]
            outras_ids = [i for i in integration_ids if i not in flex_ids]
            resumo_v = novo_resumo_vincular()
            detalhes: list[dict[str, Any]] = []

            # a. As listas que o Flex não lê: sem a trava.
            if outras_ids:
                await _fase(job_id, "listas")
                detalhes += await _ler_listas(job_id, outras_ids)
                resultado["anuncios"] = _resumo_anuncios(detalhes)

            # b. ML e Shopee com a trava do Flex (e a da rodada da Shopee):
            # Vincular primeiro, lista só de quem vinculou, espelho; sai do
            # bloco → commit → as travas soltam.
            if flex_ids:
                async with session_scope() as trava:
                    if not await _tentar_trava(trava, _MOTOR_LOCK_KEY):
                        await _fase(job_id, "esperando_flex")
                        flex_ok, esperou = await _esperar_trava_flex(trava, _MOTOR_LOCK_KEY)
                    else:
                        flex_ok, esperou = True, 0.0
                    resultado["flex"] = {"esperou_s": int(round(esperou)), "conseguiu": flex_ok}
                    pular: dict[UUID, dict[str, Any]] = {}
                    if not flex_ok:
                        logger.warning(
                            "atualizar_flex_ocupado", job_id=str(job_id), esperou_s=int(esperou)
                        )
                        pular = {i: {"erro": MSG_FLEX_OCUPADO} for i in flex_ids}
                    else:
                        if not await _tentar_trava(trava, _SHOPEE_SWEEP_LOCK_KEY):
                            for i in flex_ids:
                                if plataformas.get(i) == IntegrationPlatform.SHOPEE:
                                    pular[i] = {"erro": MSG_SHOPEE_OCUPADA}
                        a_vincular = [i for i in flex_ids if i not in pular]
                        if a_vincular:
                            por_conta = await _vincular(job_id, a_vincular, resumo_v, resultado)
                            for i in a_vincular:
                                info = por_conta.get(i)
                                if (info or {}).get("result") != "ok":
                                    pular[i] = _sem_lista(info)
                    await _fase(job_id, "listas_flex")
                    det_flex = await _ler_listas(job_id, flex_ids, pular=pular)
                    detalhes += det_flex
                    resultado["anuncios"] = _resumo_anuncios(detalhes)
                    gravadas = [
                        UUID(d["integration_id"])
                        for d in det_flex
                        if d["resultado"] in ("ok", "falha")
                        and UUID(d["integration_id"]) not in pular
                    ]
                    await _espelho(job_id, gravadas, resumo_v)

            # c. Vincular do resto (TikTok, Magalu e as Amazon), já sem a trava.
            ids_vincular = _ids_do_vincular(detalhes, amazon_ids)
            ids_vincular = [i for i in ids_vincular if i not in flex_ids]
            if ids_vincular:
                await _vincular(job_id, ids_vincular, resumo_v, resultado)
                await _espelho(job_id, ids_vincular, resumo_v)
        status_final, erro = _status_final(resultado)
    except Exception as e:  # noqa: BLE001
        logger.exception("atualizar_falhou", job_id=str(job_id))
        status_final = BackgroundJobStatus.FAILED
        erro = f"{type(e).__name__}: {e}"[:1000]
    finally:
        # Timeout do arq e reinício do worker chegam como CancelledError (não é
        # Exception): sem isto o job ficava 'running' para sempre na tela.
        if status_final is None:
            status_final = BackgroundJobStatus.FAILED
            erro = "interrompido (timeout/reinício)"
        await _gravar_fim(job_id, status_final, erro, resultado)
