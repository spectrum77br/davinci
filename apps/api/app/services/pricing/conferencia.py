"""Conferência de preços da Tabela de Preços (02/11/2026).

Eduardo, tarefa "Melhorias na Tabela de Preços": botão só de administrador com
"% margem de erro" que compara o preço da Tabela com o preço do anúncio em
cada conta e marca (!) na célula quando passa da margem. Este job lê o preço
VIVO dos anúncios de um departamento e grava em `pricing_conferencia` (uma
linha por célula, o resultado da última conferência); a tela compara com a
margem que o administrador digitar.

Quais células e quais anúncios — EXATAMENTE os do envio:
  * as células do GET /api/pricing/grid do departamento (contas sem loja ou
    integração arquivada, coluna de catálogo só junto da base, cálculo com a
    conta base no catálogo), só as que mandariam preço (`celula_manda_preco`
    e preço calculado);
  * os anúncios do resolvedor do envio (`resolver_anuncios` com os argumentos
    do `push.push_one`: D3 só na conta com o catálogo ligado, e o anúncio de
    catálogo disputado por duas linhas fica de fora das duas). Sem anúncio →
    'sem_anuncio', com o motivo que o envio daria.

Leitura por integração (o catálogo lê pela integração da conta base), com o
leitor da plataforma (`conferencia_leitores.LEITORES`) e o cliente com a
renovação de token segura (`atendimento.clientes.cliente_da_integracao`). No
máximo 3 contas ao mesmo tempo, uma loja Shopee por vez (a cota de chamadas é
do partner-id), e nenhuma sessão do banco aberta durante a chamada ao
marketplace: tudo é carregado antes, e cada conta grava o seu pedaço numa
sessão nova assim que termina (o progresso aparece na tela). Grava só as
células cuja conta e linha ainda existem: excluir uma linha ou conta no meio
da leitura leva só as células dela. Plataforma sem leitor → 'nao_suportada';
a conta inteira falhou (token, acesso) → 'erro_conta' e o job segue com as
outras.

A conferência anterior do departamento fica até esta terminar: cada célula é
substituída quando a sua conta é lida, e só no fim, com sucesso, saem as que
não mandam mais preço. A que não terminou — passou do prazo
('tempo_esgotado') ou foi cancelada de fora, deploy/reinício do worker
('interrompida') — deixa as células não lidas com a leitura anterior.

Só no servidor (`conferencia_liberada`): o refresh token do ML e da Shopee é
de uso único, e o banco local pode ter as credenciais da produção — uma
leitura daqui renovaria o token e derrubaria a loja lá.
"""

from __future__ import annotations

import asyncio
import importlib
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from arq.jobs import Job as ArqJob
from arq.jobs import JobStatus as ArqJobStatus
from sqlalchemy import and_, bindparam, delete, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app import worker_pool
from app.config import get_settings
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Integration,
    PricingAccount,
    PricingConferencia,
    PricingOverride,
    PricingProduct,
    Product,
    ProductLink,
)
from app.models.pricing_conferencia import (
    SITUACAO_CONFERIDA,
    SITUACAO_ERRO_CONTA,
    SITUACAO_NAO_SUPORTADA,
    SITUACAO_SEM_ANUNCIO,
    SITUACOES,
)
from app.services.atendimento.clientes import cliente_da_integracao
from app.services.pricing import conferencia_leitores
from app.services.pricing.anuncios import (
    BLOQUEIO_SO_CATALOGO,
    CANAL_CATALOGO,
    CANAL_KIT,
    TEXTO_BLOQUEIO,
    Resolucao,
    celula_manda_preco,
    kit_pula_catalogo,
    resolver_anuncios,
    separar_anuncios_disputados,
    texto_bloqueio_envio,
)
from app.services.pricing.calc import calculate
from app.services.pricing.conferencia_leitores import Leitor, LeituraAnuncio
from app.services.pricing.push import bases_com_catalogo_ligado, integracoes_com_marca_lida
from app.services.pricing.sku_match import variants_of

logger = structlog.get_logger()

# Um módulo por plataforma, cada um registra o seu leitor ao ser importado.
# Import tolerante: o que ainda não existe deixa a plataforma "não suportada".
PACOTE_DOS_LEITORES = "app.services.pricing"
MODULOS_DE_LEITOR = (
    "conferencia_ml",
    "conferencia_amazon",
    "conferencia_shopee",
    "conferencia_tiktok",
)

# O leitor da Amazon endereça o anúncio pelo SKU do PRODUTO vinculado, como o
# envio (`conferencia_amazon.sku_do_envio`): o núcleo põe o SKU em cada vínculo.
ATRIBUTO_SKU_DO_PRODUTO = "sku_do_produto"

CONTAS_AO_MESMO_TEMPO = 3
# Plataformas lidas uma loja por vez: a cota de chamadas da Shopee é do
# partner-id (a mesma do resto do sistema), não da loja.
UMA_LOJA_POR_VEZ = frozenset({"shopee"})
# Teto do job no arq (`worker.py`: func(conferir_precos_run, timeout=3600)) e
# o prazo próprio, antes dele: quem passa do prazo é 'tempo_esgotado'; o
# cancelamento que chega de fora antes disso é deploy ou reinício do worker
# ('interrompida').
TETO_DO_ARQ_S = 3600
FOLGA_ANTES_DO_TETO_S = 100
PRAZO_S = TETO_DO_ARQ_S - FOLGA_ANTES_DO_TETO_S
# Sinal de vida do job (o `background_jobs_gc` e a trava abaixo olham isto).
BATIMENTO_S = 60
# Job "rodando" sem sinal de vida há mais que isto morreu (deploy, worker caiu).
SEM_SINAL = timedelta(minutes=5)
# Job "pendente" mais velho que isto nunca vai ser pego (o teto do job é 1 h).
PENDENTE_ESQUECIDO = timedelta(hours=2)
# Job "pendente" há mais que isto já devia ter começado: pergunta ao arq se ele
# ainda está na fila (o worker no código velho, sem a função, descarta o job
# na hora com "function not found" e a linha daqui ficaria 'pending' por 2 h).
PENDENTE_SUSPEITO = timedelta(minutes=10)
# Status do job no arq que quer dizer "não vai mais rodar": terminou sem passar
# pelo `_comecar` ou sumiu da fila (o resultado só fica 1 h, `keep_result`).
STATUS_ARQ_SEM_VOLTA = frozenset({ArqJobStatus.complete, ArqJobStatus.not_found})
# A consulta ao Redis não pode prender o GET da tela.
CONSULTA_AO_ARQ_S = 3
# A mesma trava no clique e no começo do job: uma conferência por departamento.
TRAVA = "conferir_precos"
MOTIVO_MAX = 200

ERRO_SO_NO_SERVIDOR = "so_no_servidor"
ERRO_DEPARTAMENTO = "invalid_department"
ERRO_EM_ANDAMENTO = "conferencia_em_andamento"
ERRO_TEMPO = "tempo_esgotado"
ERRO_INTERROMPIDA = "interrompida"
ERRO_WORKER_NAO_EXECUTOU = "worker_nao_executou"

MOTIVO_SEM_INTEGRACAO = "conta sem integração"
MOTIVO_SEM_VINCULO = "sem anúncio vinculado"
MOTIVO_SEM_LEITURA = "sem leitura"
MOTIVO_FALHA_AO_GRAVAR = "falha ao gravar o resultado"


class DepartamentoInvalidoError(ValueError):
    """O slug não é de nenhuma aba (segmento raiz)."""


@dataclass(slots=True)
class Celula:
    conta: PricingAccount
    produto: PricingProduct
    preco: Decimal
    integracao: Integration | None = None
    links: list[ProductLink] = field(default_factory=list)
    # Já decidida na montagem (sem_anuncio / nao_suportada); None = ler.
    situacao: str | None = None
    motivo: str | None = None


@dataclass(slots=True)
class Grupo:
    """Uma integração a ler: as células dela e os vínculos, sem repetir."""

    integracao: Integration
    plataforma: str
    celulas: list[Celula] = field(default_factory=list)
    links: list[ProductLink] = field(default_factory=list)


@dataclass(slots=True)
class Desfecho:
    lidos: int = 0
    # A conta inteira falhou na leitura (vira 'erro_conta').
    erro: str | None = None
    gravou: bool = True
    # Células gravadas (as de linha ou conta excluída no meio ficam de fora).
    gravadas: int = 0


@dataclass(slots=True)
class _Comeco:
    celulas: list[Celula]
    grupos: list[Grupo]
    leitores: dict[str, Leitor]
    # Situação das células gravadas já no começo (as que não precisam de leitura).
    imediatas: Counter[str]


def conferencia_liberada() -> bool:
    """A conferência chama os marketplaces: só no servidor (ver o topo)."""
    return get_settings().is_prod


def _agora() -> datetime:
    return datetime.now(UTC)


def _plataforma(integ: Integration) -> str:
    return getattr(integ.platform, "value", integ.platform)


def _motivo_curto(e: BaseException) -> str:
    return (str(e).strip() or type(e).__name__)[:MOTIVO_MAX]


def carregar_leitores() -> dict[str, Leitor]:
    """{plataforma: leitor} dos módulos de leitor que existem."""
    for nome in MODULOS_DE_LEITOR:
        modulo = f"{PACOTE_DOS_LEITORES}.{nome}"
        try:
            importlib.import_module(modulo)
        except ModuleNotFoundError as e:
            if e.name == modulo:
                logger.info("conferencia_leitor_ausente", modulo=nome)
            else:
                logger.exception("conferencia_leitor_quebrado", modulo=nome)
        except Exception:
            logger.exception("conferencia_leitor_quebrado", modulo=nome)
    return dict(conferencia_leitores.LEITORES)


# ------------------------------------------------------------ trava e estado


async def travar_departamento(session: AsyncSession, department: str) -> None:
    """Trava da transação: o clique e o começo do job não correm juntos."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{TRAVA}:{department}"}
    )


def _vivo_em_sql(agora: datetime):
    return or_(
        and_(
            BackgroundJob.status == BackgroundJobStatus.PENDING,
            BackgroundJob.created_at >= agora - PENDENTE_ESQUECIDO,
        ),
        and_(
            BackgroundJob.status == BackgroundJobStatus.RUNNING,
            func.coalesce(
                BackgroundJob.last_heartbeat_at,
                BackgroundJob.started_at,
                BackgroundJob.created_at,
            )
            >= agora - SEM_SINAL,
        ),
    )


def job_vivo(job: BackgroundJob, *, agora: datetime | None = None) -> bool:
    """O job pendente/rodando ainda vai terminar? (mesma regra da trava)"""
    agora = agora or _agora()
    if job.finished_at is not None:
        return False
    if job.status == BackgroundJobStatus.PENDING:
        return job.created_at >= agora - PENDENTE_ESQUECIDO
    if job.status == BackgroundJobStatus.RUNNING:
        sinal = job.last_heartbeat_at or job.started_at or job.created_at
        return sinal >= agora - SEM_SINAL
    return False


async def worker_nao_executou(job: BackgroundJob, *, agora: datetime | None = None) -> bool:
    """O job está 'pending' há mais de PENDENTE_SUSPEITO e o arq diz que ele
    não vai rodar? (o worker_sync no código velho, sem a função, dá "function
    not found" sem chamar o job; ou o job sumiu da fila). Sem resposta do
    Redis = não sabe (False): vale a regra de sempre (PENDENTE_ESQUECIDO)."""
    agora = agora or _agora()
    if (
        job.status != BackgroundJobStatus.PENDING
        or job.finished_at is not None
        or not job.arq_job_id
        or job.created_at > agora - PENDENTE_SUSPEITO
    ):
        return False
    try:
        async with asyncio.timeout(CONSULTA_AO_ARQ_S):
            pool = await worker_pool.get_arq_sync_pool()
            situacao = await ArqJob(
                job.arq_job_id, pool, _queue_name=worker_pool.ARQ_SYNC_QUEUE
            ).status()
    except Exception as e:  # noqa: BLE001 — Redis fora: a regra de sempre
        logger.warning("conferencia_arq_sem_resposta", job_id=str(job.id), erro=_motivo_curto(e))
        return False
    if situacao not in STATUS_ARQ_SEM_VOLTA:
        return False
    logger.warning(
        "conferencia_worker_nao_executou",
        job_id=str(job.id),
        arq_job_id=job.arq_job_id,
        status_arq=getattr(situacao, "value", situacao),
    )
    return True


async def baixar_se_nao_executou(session: AsyncSession, job: BackgroundJob) -> bool:
    """O job pendente que o worker descartou sem rodar (`worker_nao_executou`)
    vira FAILED 'worker_nao_executou' — só se ainda estiver pendente. True = o
    job não segura mais ninguém. O commit é de quem chama; se o job rodar
    depois mesmo assim, o `_comecar` o barra (não está mais 'pending')."""
    if not await worker_nao_executou(job):
        return False
    agora = _agora()
    await session.execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.id == job.id,
            BackgroundJob.status == BackgroundJobStatus.PENDING,
            BackgroundJob.finished_at.is_(None),
        )
        .values(
            status=BackgroundJobStatus.FAILED,
            error=ERRO_WORKER_NAO_EXECUTOU,
            finished_at=agora,
        )
        .execution_options(synchronize_session=False)
    )
    # O estado de verdade (outro clique pode ter mexido no meio).
    await session.refresh(job)
    return job.finished_at is not None


async def job_em_andamento(
    session: AsyncSession,
    department: str,
    *,
    exceto: UUID | None = None,
    perguntar_ao_arq: bool = False,
) -> BackgroundJob | None:
    """A conferência pendente/rodando (e viva) deste departamento, se houver.
    `perguntar_ao_arq` (o clique): a pendente que o worker descartou sem rodar
    é dada como falha ali mesmo (`baixar_se_nao_executou`, o commit é de quem
    chama) e não segura o clique."""
    stmt = select(BackgroundJob).where(
        BackgroundJob.type == BackgroundJobType.CONFERIR_PRECOS,
        BackgroundJob.payload["department"].astext == department,
        BackgroundJob.finished_at.is_(None),
        _vivo_em_sql(_agora()),
    )
    if exceto is not None:
        stmt = stmt.where(BackgroundJob.id != exceto)
    vivos = (await session.execute(stmt.order_by(BackgroundJob.created_at.desc()))).scalars().all()
    for job in vivos:
        if perguntar_ao_arq and await baixar_se_nao_executou(session, job):
            continue
        return job
    return None


async def ultimo_job(
    session: AsyncSession, department: str, *, so_concluido: bool = False
) -> BackgroundJob | None:
    """O último job de conferência do departamento; `so_concluido`: o último
    que terminou bem (a tela usa os dois para saber que leitura ainda vale —
    a da conferência que parou no meio ou da conta que não gravou, não)."""
    stmt = select(BackgroundJob).where(
        BackgroundJob.type == BackgroundJobType.CONFERIR_PRECOS,
        BackgroundJob.payload["department"].astext == department,
    )
    if so_concluido:
        stmt = stmt.where(BackgroundJob.status == BackgroundJobStatus.SUCCEEDED)
    return (
        await session.execute(stmt.order_by(BackgroundJob.created_at.desc()).limit(1))
    ).scalar_one_or_none()


# ------------------------------------------------------------ montagem


def _chaves_do_sku(sku: str) -> set[str]:
    """Pedaços do SKU do anúncio por onde o resolvedor pode casá-lo com uma
    linha: o SKU inteiro, o lado esquerdo do kit e o código base de cada um.
    Serve só para não passar TODOS os vínculos da conta em cada célula (o
    resolvedor é quem decide, com as regras de sempre)."""
    principal = sku.split("+", 1)[0]
    return {sku, principal, principal.split(".", 1)[0], sku.split(".", 1)[0]}


def _chaves_da_linha(pricing_sku: str | None) -> set[str]:
    variantes = [v.lower() for v in variants_of(pricing_sku)]
    return set(variantes) | {v.split(".", 1)[0] for v in variantes}


@dataclass(slots=True)
class _Vinculos:
    """Os vínculos de UMA integração, carregados uma vez, com o índice."""

    links: list[ProductLink] = field(default_factory=list)
    sku_por_produto: dict[UUID, str] = field(default_factory=dict)
    posicoes: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    candidatos: dict[UUID, list[ProductLink]] = field(default_factory=dict)

    def adicionar(self, link: ProductLink, sku: str | None) -> None:
        if sku:
            self.sku_por_produto[link.product_id] = sku
            for chave in _chaves_do_sku(sku.lower()):
                self.posicoes[chave].append(len(self.links))
        self.links.append(link)

    def da_linha(self, produto: PricingProduct) -> list[ProductLink]:
        """Superconjunto dos vínculos que casam com a linha, na ordem em que o
        banco devolveu (a mesma lista que o envio passaria ao resolvedor,
        menos os que não têm como casar)."""
        if produto.id not in self.candidatos:
            achados: set[int] = set()
            for chave in _chaves_da_linha(produto.sku):
                achados.update(self.posicoes.get(chave, ()))
            self.candidatos[produto.id] = [self.links[i] for i in sorted(achados)]
        return self.candidatos[produto.id]


async def _carregar_vinculos(
    session: AsyncSession, integracao_ids: Iterable[UUID]
) -> dict[UUID, _Vinculos]:
    ids = list(integracao_ids)
    por_integ: dict[UUID, _Vinculos] = {i: _Vinculos() for i in ids}
    if not ids:
        return por_integ
    linhas = await session.execute(
        select(ProductLink, Product.sku)
        .outerjoin(Product, Product.id == ProductLink.product_id)
        .where(ProductLink.integration_id.in_(ids))
    )
    for link, sku in linhas.all():
        setattr(link, ATRIBUTO_SKU_DO_PRODUTO, sku or None)
        por_integ[link.integration_id].adicionar(link, sku)
    return por_integ


def _motivo_sem_anuncio(canal: str, resolucao: Resolucao, *, marca_nao_lida: bool) -> str:
    """O motivo que o envio daria para não mandar a esta célula."""
    if canal == CANAL_CATALOGO:
        bloqueio = texto_bloqueio_envio(resolucao, marca_nao_lida=marca_nao_lida)
        if bloqueio is not None:
            return bloqueio[1]
    if resolucao.so_catalogo:
        ids = ", ".join(lk.external_id for lk in resolucao.so_catalogo)
        return f"{TEXTO_BLOQUEIO[BLOQUEIO_SO_CATALOGO]} ({ids})"
    return MOTIVO_SEM_VINCULO


async def montar_celulas(
    session: AsyncSession, department: str, plataformas_lidas: Collection[str]
) -> list[Celula]:
    """As células do departamento que mandariam preço, cada uma com os
    anúncios que receberiam o preço (ou a situação já decidida)."""
    # Os mesmos filtros do GET /grid (import tardio, como os outros serviços
    # que reaproveitam regra de router).
    from app.routers.pricing import (
        _exclude_archived_accounts,
        _segment_index,
        _so_filhas_com_base,
    )

    _roots, leaves_by_id, root_id_by_slug = await _segment_index(session)
    raiz = root_id_by_slug.get(department)
    if raiz is None:
        raise DepartamentoInvalidoError(department)
    folhas = [lid for lid, (slug, _tipo) in leaves_by_id.items() if slug == department]
    contas = _so_filhas_com_base(
        (
            await session.execute(
                _exclude_archived_accounts(
                    select(PricingAccount).where(PricingAccount.segment_id == raiz)
                ).order_by(PricingAccount.sort_order, PricingAccount.name)
            )
        )
        .scalars()
        .all()
    )
    produtos = (
        (
            await session.execute(
                select(PricingProduct)
                .where(PricingProduct.segment_id.in_(folhas))
                .order_by(PricingProduct.sku)
            )
        )
        .scalars()
        .all()
    )
    if not contas or not produtos:
        return []

    por_id = {a.id: a for a in contas}
    overrides = {
        (o.pricing_product_id, o.pricing_account_id): o
        for o in (
            await session.execute(
                select(PricingOverride).where(PricingOverride.pricing_account_id.in_(list(por_id)))
            )
        )
        .scalars()
        .all()
    }

    def _dona(conta: PricingAccount) -> PricingAccount:
        # Coluna de catálogo: a integração e os números são os da conta base.
        return por_id[conta.conta_base_id] if conta.canal == CANAL_CATALOGO else conta

    integ_ids = {_dona(a).integration_id for a in contas} - {None}
    integracoes = {
        i.id: i
        for i in (await session.execute(select(Integration).where(Integration.id.in_(integ_ids))))
        .scalars()
        .all()
    }
    lidas = {i for i, integ in integracoes.items() if _plataforma(integ) in plataformas_lidas}
    # Vínculos de TODAS as integrações: a plataforma sem leitor (Magalu)
    # também passa pelo resolvedor, para "sem anúncio" não virar "não conferido".
    vinculos = await _carregar_vinculos(session, set(integracoes))
    com_catalogo = await bases_com_catalogo_ligado(
        session, [a.id for a in contas if a.canal != CANAL_CATALOGO]
    )
    marca_lida = await integracoes_com_marca_lida(
        session, {_dona(a).integration_id for a in contas if a.canal == CANAL_CATALOGO}
    )

    celulas: list[Celula] = []
    resolucoes: dict[tuple[UUID, UUID], Resolucao] = {}
    # coluna de catálogo → {linha: resolução}: duas linhas no mesmo anúncio
    # de catálogo = disputa (como no /grid e no `push._separar_disputados`).
    disputa: dict[UUID, dict[UUID, Resolucao]] = defaultdict(dict)
    for prod in produtos:
        par = leaves_by_id.get(prod.segment_id)
        tipo = par[1] if par else None
        for conta in contas:
            ovr = overrides.get((prod.id, conta.id))
            base = por_id.get(conta.conta_base_id) if conta.canal == CANAL_CATALOGO else None
            outcome = calculate(conta, prod, ovr, tipo, conta_base=base)
            if not celula_manda_preco(outcome, ovr) or outcome.price is None:
                continue
            dona = _dona(conta)
            integ = integracoes.get(dona.integration_id) if dona.integration_id else None
            celula = Celula(conta=conta, produto=prod, preco=outcome.price, integracao=integ)
            celulas.append(celula)
            if integ is None:
                celula.situacao, celula.motivo = SITUACAO_SEM_ANUNCIO, MOTIVO_SEM_INTEGRACAO
                continue
            plataforma = _plataforma(integ)
            canal = conta.canal or CANAL_KIT
            # D3 só na conta ML de Kit com a coluna Catálogo ligada (push_one).
            pula_catalogo = (
                canal == CANAL_KIT
                and plataforma == "ml"
                and kit_pula_catalogo(conta.id in com_catalogo)
            )
            fonte = vinculos[integ.id]
            resolucao = resolver_anuncios(
                fonte.da_linha(prod),
                fonte.sku_por_produto,
                pricing_sku=prod.sku,
                dept=department,
                plataforma=plataforma,
                canal=canal,
                listing_type_conta=conta.listing_type,
                pula_catalogo=pula_catalogo,
            )
            resolucoes[(prod.id, conta.id)] = resolucao
            if canal == CANAL_CATALOGO:
                disputa[conta.id][prod.id] = resolucao

    skus_das_linhas = {prod.id: prod.sku or "?" for prod in produtos}
    for por_linha in disputa.values():
        separar_anuncios_disputados(por_linha, skus_das_linhas)

    for celula in celulas:
        if celula.situacao is not None:
            continue
        resolucao = resolucoes[(celula.produto.id, celula.conta.id)]
        if resolucao.links and celula.integracao.id not in lidas:
            # Tem anúncio, mas a plataforma não tem leitor de preço (Magalu).
            celula.situacao = SITUACAO_NAO_SUPORTADA
            celula.motivo = (
                f"sem leitor de preço para a plataforma {_plataforma(celula.integracao)}"
            )
            continue
        if resolucao.links:
            celula.links = list(resolucao.links)
            continue
        celula.situacao = SITUACAO_SEM_ANUNCIO
        celula.motivo = _motivo_sem_anuncio(
            celula.conta.canal or CANAL_KIT,
            resolucao,
            marca_nao_lida=celula.integracao.id not in marca_lida,
        )
    return celulas


def agrupar(celulas: Iterable[Celula]) -> list[Grupo]:
    """As células a ler, por integração (vínculos sem repetir), em ordem de nome."""
    grupos: dict[UUID, Grupo] = {}
    vistos: dict[UUID, set[UUID]] = defaultdict(set)
    for celula in celulas:
        if celula.situacao is not None:
            continue
        integ = celula.integracao
        grupo = grupos.get(integ.id)
        if grupo is None:
            grupo = grupos[integ.id] = Grupo(integracao=integ, plataforma=_plataforma(integ))
        grupo.celulas.append(celula)
        for lk in celula.links:
            if lk.id not in vistos[integ.id]:
                vistos[integ.id].add(lk.id)
                grupo.links.append(lk)
    return sorted(grupos.values(), key=lambda g: (g.integracao.name or "", str(g.integracao.id)))


# ------------------------------------------------------------ gravação


def _numero(valor: Decimal | None) -> float | None:
    return float(valor) if valor is not None else None


def _anuncio(link: ProductLink, leitura: LeituraAnuncio | None, *, lida: bool) -> dict[str, Any]:
    if not isinstance(leitura, LeituraAnuncio):
        leitura = None
    return {
        "anuncio": link.external_id,
        "variacao": link.variation_id,
        "preco": _numero(leitura.preco) if leitura else None,
        "preco_promocional": _numero(leitura.preco_promocional) if leitura else None,
        "ativo": leitura.ativo if leitura else None,
        "motivo": leitura.motivo if leitura else (MOTIVO_SEM_LEITURA if lida else None),
    }


def _linha(
    celula: Celula,
    *,
    department: str,
    job_id: UUID,
    agora: datetime,
    leituras: dict[UUID, LeituraAnuncio] | None = None,
    erro: str | None = None,
) -> dict[str, Any]:
    if celula.situacao is not None:
        situacao, motivo, anuncios = celula.situacao, celula.motivo, []
    elif erro is not None:
        situacao, motivo = SITUACAO_ERRO_CONTA, erro
        anuncios = [_anuncio(lk, None, lida=False) for lk in celula.links]
    else:
        situacao, motivo = SITUACAO_CONFERIDA, None
        anuncios = [_anuncio(lk, (leituras or {}).get(lk.id), lida=True) for lk in celula.links]
    return {
        "pricing_account_id": celula.conta.id,
        "pricing_product_id": celula.produto.id,
        "department": department,
        "preco_tabela": celula.preco,
        "situacao": situacao,
        "motivo": motivo,
        "anuncios": anuncios,
        "job_id": job_id,
        "conferido_em": agora,
    }


async def _gravar(session: AsyncSession, linhas: list[dict[str, Any]]) -> None:
    """Upsert por célula (a chave é a conta × a linha)."""
    if not linhas:
        return
    tabela = PricingConferencia.__table__
    stmt = pg_insert(tabela)
    stmt = stmt.on_conflict_do_update(
        index_elements=[tabela.c.pricing_account_id, tabela.c.pricing_product_id],
        set_={
            nome: stmt.excluded[nome]
            for nome in (
                "department",
                "preco_tabela",
                "situacao",
                "motivo",
                "anuncios",
                "job_id",
                "conferido_em",
            )
        },
    )
    await session.execute(stmt, linhas)


async def _ids_que_existem(session: AsyncSession, modelo: Any, ids: set[UUID]) -> set[UUID]:
    """Os ids que ainda existem, travados até o fim da transação com a mesma
    trava que a FK usa (FOR KEY SHARE): ninguém os apaga entre esta consulta e
    o INSERT (quem apaga espera o commit, e o CASCADE leva a célula gravada)."""
    if not ids:
        return set()
    linhas = await session.execute(
        select(modelo.id)
        .where(modelo.id.in_(ids))
        .order_by(modelo.id)
        .with_for_update(read=True, key_share=True)
    )
    return set(linhas.scalars().all())


async def _gravar_vivas(
    session: AsyncSession, linhas: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Upsert só das células cuja conta e linha ainda existem. A linha da
    Tabela, a conta ou a coluna de catálogo excluída enquanto as contas eram
    lidas (minutos) perde só as células dela — antes a violação de FK
    derrubava a gravação da integração inteira. Devolve as linhas gravadas."""
    contas = await _ids_que_existem(
        session, PricingAccount, {ln["pricing_account_id"] for ln in linhas}
    )
    produtos = await _ids_que_existem(
        session, PricingProduct, {ln["pricing_product_id"] for ln in linhas}
    )
    vivas = [
        ln
        for ln in linhas
        if ln["pricing_account_id"] in contas and ln["pricing_product_id"] in produtos
    ]
    if len(vivas) < len(linhas):
        logger.info("conferencia_celulas_excluidas_no_meio", descartadas=len(linhas) - len(vivas))
    await _gravar(session, vivas)
    return vivas


async def _limpar_celulas_que_sairam(job_id: UUID, department: str, celulas: list[Celula]) -> int:
    """Fim da conferência com sucesso: tira do departamento as células de
    conferências anteriores que não estão nesta — não mandam mais preço (NA,
    desligada, sem preço, conta arquivada, linha que mudou de aba). A célula
    desta conferência que não conseguiu gravar fica com a leitura anterior."""
    tipo = ARRAY(PG_UUID(as_uuid=True))
    desta = select(
        func.unnest(bindparam("contas", [c.conta.id for c in celulas], type_=tipo)),
        func.unnest(bindparam("linhas", [c.produto.id for c in celulas], type_=tipo)),
    )
    async with _db.SessionLocal() as s:
        apagadas = await s.execute(
            delete(PricingConferencia).where(
                PricingConferencia.department == department,
                PricingConferencia.job_id.is_distinct_from(job_id),
                tuple_(
                    PricingConferencia.pricing_account_id, PricingConferencia.pricing_product_id
                ).not_in(desta),
            )
        )
        await s.commit()
    return apagadas.rowcount


async def _atualizar_job(job_id: UUID, *, so_se_aberto: bool = False, **valores: Any) -> None:
    stmt = update(BackgroundJob).where(BackgroundJob.id == job_id)
    if so_se_aberto:
        # Não passa por cima do desfecho já gravado.
        stmt = stmt.where(BackgroundJob.finished_at.is_(None))
    async with _db.SessionLocal() as s:
        await s.execute(stmt.values(**valores))
        await s.commit()


async def _encerrar(
    job_id: UUID,
    status: BackgroundJobStatus,
    *,
    erro: str | None = None,
    resultado: dict | None = None,
    so_se_aberto: bool = False,
) -> None:
    agora = _agora()
    valores: dict[str, Any] = {"status": status, "finished_at": agora, "last_heartbeat_at": agora}
    if erro is not None:
        valores["error"] = erro
    if resultado is not None:
        valores["result"] = resultado
    await _atualizar_job(job_id, so_se_aberto=so_se_aberto, **valores)


async def _bater_coracao(job_id: UUID, parar: asyncio.Event) -> None:
    """Carimba o sinal de vida enquanto as contas são lidas (uma conta grande
    passa minutos sem terminar)."""
    while not parar.is_set():
        try:
            await asyncio.wait_for(parar.wait(), timeout=BATIMENTO_S)
        except TimeoutError:
            try:
                await _atualizar_job(job_id, last_heartbeat_at=_agora())
            except Exception:  # noqa: BLE001 — o próximo batimento tenta de novo
                logger.warning("conferencia_batimento_falhou", job_id=str(job_id))


async def _conferir_grupo(
    grupo: Grupo,
    leitor: Leitor,
    semaforo: asyncio.Semaphore,
    *,
    job_id: UUID,
    department: str,
    vez_da_plataforma: asyncio.Semaphore | None = None,
) -> Desfecho:
    integ = grupo.integracao
    # A vez da plataforma (uma loja Shopee por vez) ANTES da vaga geral: quem
    # espera a vez não ocupa vaga das outras plataformas, e a leitura de quem
    # tem a vez conta nas 3 vagas como qualquer outra.
    async with vez_da_plataforma or nullcontext(), semaforo:
        try:
            cliente = await cliente_da_integracao(integ)
            leituras = await leitor(cliente, grupo.links)
            if not isinstance(leituras, dict):
                raise TypeError(f"o leitor de {grupo.plataforma} não devolveu as leituras")
            erro = None
        except Exception as e:  # noqa: BLE001 — a conta inteira vira 'erro_conta'
            leituras, erro = {}, _motivo_curto(e)
            logger.warning(
                "conferencia_conta_falhou",
                integration_id=str(integ.id),
                plataforma=grupo.plataforma,
                erro=erro,
            )
    lidos = sum(
        1
        for lk in grupo.links
        if isinstance(leituras.get(lk.id), LeituraAnuncio) and leituras[lk.id].preco is not None
    )
    agora = _agora()
    linhas = [
        _linha(c, department=department, job_id=job_id, agora=agora, leituras=leituras, erro=erro)
        for c in grupo.celulas
    ]
    gravou = True
    try:
        async with _db.SessionLocal() as s:
            gravadas = len(await _gravar_vivas(s, linhas))
            await s.commit()
    except Exception:  # noqa: BLE001 — as outras contas seguem
        gravou, gravadas = False, 0
        logger.exception("conferencia_gravar_falhou", integration_id=str(integ.id))
    try:
        await _atualizar_job(
            job_id, processed=BackgroundJob.processed + 1, last_heartbeat_at=_agora()
        )
    except Exception:  # noqa: BLE001 — progresso é só para a tela
        logger.warning("conferencia_progresso_falhou", job_id=str(job_id))
    return Desfecho(lidos=lidos, erro=erro, gravou=gravou, gravadas=gravadas)


async def _comecar(job_id: UUID, department: str) -> _Comeco | None:
    """Marca o job como rodando, monta as células e grava as que não precisam
    de leitura. A conferência anterior do departamento fica: cada célula é
    substituída quando a conta dela é lida, e as que saíram só vão embora no
    fim com sucesso (`_limpar_celulas_que_sairam`). None = o job já foi
    encerrado aqui (com o motivo)."""
    async with _db.SessionLocal() as s:
        job = await s.get(BackgroundJob, job_id)
        if job is None:
            logger.error("conferencia_job_sumiu", job_id=str(job_id))
            return None
        if job.status != BackgroundJobStatus.PENDING:
            # Já tratado (ex.: o clique viu a fila falhar e marcou "failed").
            logger.warning("conferencia_job_ja_tratado", job_id=str(job_id), status=job.status)
            return None
        # TRAVA antes de qualquer cliente: fora da produção não chama nada.
        if not conferencia_liberada():
            await _encerrar(job_id, BackgroundJobStatus.FAILED, erro=ERRO_SO_NO_SERVIDOR)
            return None
        await travar_departamento(s, department)
        outro = await job_em_andamento(s, department, exceto=job_id)
        if outro is not None and outro.status == BackgroundJobStatus.RUNNING:
            await s.rollback()
            await _encerrar(job_id, BackgroundJobStatus.FAILED, erro=ERRO_EM_ANDAMENTO)
            return None
        agora = _agora()
        job.status = BackgroundJobStatus.RUNNING
        job.started_at = agora
        job.last_heartbeat_at = agora
        job.processed = 0
        job.total = 0
        await s.commit()

        leitores = carregar_leitores()
        try:
            celulas = await montar_celulas(s, department, set(leitores))
        except DepartamentoInvalidoError:
            await s.rollback()
            await _encerrar(job_id, BackgroundJobStatus.FAILED, erro=ERRO_DEPARTAMENTO)
            return None
        imediatas = await _gravar_vivas(
            s,
            [
                _linha(c, department=department, job_id=job_id, agora=agora)
                for c in celulas
                if c.situacao is not None
            ],
        )
        grupos = agrupar(celulas)
        job.total = len(grupos)
        job.last_heartbeat_at = _agora()
        await s.commit()
    return _Comeco(
        celulas=celulas,
        grupos=grupos,
        leitores=leitores,
        imediatas=Counter(ln["situacao"] for ln in imediatas),
    )


async def run_conferir_precos(*, job_id: UUID, department: str, user_id: UUID) -> None:
    """O job (`worker.conferir_precos_run`): confere um departamento inteiro."""
    log = logger.bind(job_id=str(job_id), department=department, user_id=str(user_id))
    prazo = asyncio.get_running_loop().time() + PRAZO_S
    try:
        await _conferir(job_id, department, prazo=prazo, log=log)
    except asyncio.CancelledError:
        # Cancelado de fora antes do prazo próprio: deploy ou reinício do
        # worker_sync (no SIGTERM o arq cancela o job em andamento e, com
        # max_tries=1, não roda de novo). Registra e deixa o cancelamento seguir.
        log.warning("conferencia_interrompida")
        await asyncio.shield(
            _encerrar(job_id, BackgroundJobStatus.FAILED, erro=ERRO_INTERROMPIDA, so_se_aberto=True)
        )
        raise


async def _conferir(job_id: UUID, department: str, *, prazo: float, log: Any) -> None:
    try:
        comeco = await _comecar(job_id, department)
    except Exception as e:
        log.exception("conferencia_falhou_ao_comecar")
        await _encerrar(job_id, BackgroundJobStatus.FAILED, erro=_motivo_curto(e))
        return
    if comeco is None:
        return

    parar = asyncio.Event()
    batimento = asyncio.create_task(_bater_coracao(job_id, parar))
    semaforo = asyncio.Semaphore(CONTAS_AO_MESMO_TEMPO)
    vezes = {plataforma: asyncio.Semaphore(1) for plataforma in UMA_LOJA_POR_VEZ}
    try:
        async with asyncio.timeout_at(prazo):
            desfechos = await asyncio.gather(
                *(
                    _conferir_grupo(
                        g,
                        comeco.leitores[g.plataforma],
                        semaforo,
                        job_id=job_id,
                        department=department,
                        vez_da_plataforma=vezes.get(g.plataforma),
                    )
                    for g in comeco.grupos
                ),
                return_exceptions=True,
            )
    except TimeoutError:
        # O prazo próprio, antes do teto do arq: as contas já lidas ficam
        # gravadas e as outras, com a leitura anterior.
        log.warning("conferencia_tempo_esgotado")
        await _encerrar(job_id, BackgroundJobStatus.FAILED, erro=ERRO_TEMPO)
        return
    finally:
        parar.set()
        await batimento

    situacoes: Counter[str] = Counter(comeco.imediatas)
    gravadas = sum(comeco.imediatas.values())
    lidos = 0
    contas_com_erro: list[dict[str, str]] = []
    for grupo, desfecho in zip(comeco.grupos, desfechos, strict=True):
        if isinstance(desfecho, BaseException):
            log.error("conferencia_conta_quebrou", erro=_motivo_curto(desfecho))
            desfecho = Desfecho(erro=_motivo_curto(desfecho), gravou=False)
        lidos += desfecho.lidos
        if desfecho.erro is not None:
            contas_com_erro.append({"conta": grupo.integracao.name, "motivo": desfecho.erro})
        if not desfecho.gravou:
            contas_com_erro.append(
                {"conta": grupo.integracao.name, "motivo": MOTIVO_FALHA_AO_GRAVAR}
            )
            continue
        gravadas += desfecho.gravadas
        situacao = SITUACAO_ERRO_CONTA if desfecho.erro is not None else SITUACAO_CONFERIDA
        situacoes[situacao] += desfecho.gravadas
    anuncios = sum(len(g.links) for g in comeco.grupos)
    resultado = {
        "celulas": gravadas,
        "anuncios": anuncios,
        "lidos": lidos,
        "sem_leitura": anuncios - lidos,
        "contas_com_erro": contas_com_erro,
        "situacoes": {s: situacoes.get(s, 0) for s in SITUACOES},
    }
    try:
        saidas = await _limpar_celulas_que_sairam(job_id, department, comeco.celulas)
    except Exception:  # noqa: BLE001 — a conferência valeu; sobram células velhas
        saidas = 0
        log.exception("conferencia_limpeza_falhou")
    await _encerrar(job_id, BackgroundJobStatus.SUCCEEDED, resultado=resultado)
    log.info(
        "conferencia_precos_fim",
        celulas_que_sairam=saidas,
        **{k: v for k, v in resultado.items() if k != "situacoes"},
    )
