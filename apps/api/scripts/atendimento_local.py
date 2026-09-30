"""Ambiente LOCAL da caixa `/atendimento` — para testar a tela sem produção.

O Eduardo revisa o atendimento unificado ANTES de qualquer subida
(docs/atendimento-unificado.md, seção "Como testar localmente"). Tudo aqui roda
NESTE Mac, num schema PRÓPRIO do Postgres local, com compradores inventados:

  preparar  — cria o schema do zero: tipos enum, `create_all` dos models e as
              funções/gatilhos do Histórico (os mesmos da migration 0331).
  semente   — o cenário do PRIMEIRO TESTE EM PRODUÇÃO (28/09/2026): só observar.
              As lojas como estão no Duoke (Shopee Inova, Marquezini, KFA, KIA,
              Poofy; TikTok Mini, Barbosa, ATV sem permissão; ML DREAM2, MEGA,
              POOFY, AGUIAR2, VELASCO; Amazon KFA), todos os canais em
              `observar`, 27 conversas com o pedido e o produto preenchidos como
              a API das lojas entrega (foto, título, SKU e preço de anúncios
              reais do catálogo), não lidas por conversa, e as sugestões da IA
              no modo observação: pendentes, substituídas pela resposta que a
              equipe deu por fora (Duoke), uma avaliada 👍 e outra 👎 com correção.
              Parte 2: integrações com o nome de SISTEMA ("mega") ligadas à loja
              do cadastro ("Shopee Marquezini" → a caixa mostra "Marquezini"),
              "Hora de envio"/"Tempo concluído" no pedido, o índice de pedidos
              e de avaliações por comprador (o cartão "Cliente": recorrente,
              avaliou 5★, avaliou 2★, com devolução, primeira compra), as 12
              categorias do manual e as regras por tipo (segurança, categoria,
              estilo) sem conflito.
  limpar    — DROP SCHEMA.
  api       — sobe a API apontando para esse schema com o ENVIO DESLIGADO (é o
              teste em produção: quem responde é o Duoke), a leitura desligada e
              qualquer chamada às lojas BARRADA. `ATENDIMENTO_ENVIO_ATIVO=true`
              liga o envio — para o SIMULADOR (nenhuma mensagem sai para comprador).

Por que um schema próprio e não o `davinci` do banco local: o `davinci` local é o
de desenvolvimento, defasado (parou na migration 0301). Mexer nele atrapalharia
quem usa o dev para outras telas, e subir 30 migrations nele só para ver uma tela
é trabalho à toa. O schema daqui se recria em segundos e se apaga sem deixar rastro.

Por que o `api` e não um `uvicorn app.main:app` com DATABASE_SCHEMA: os models
qualificam as TABELAS pelo schema, mas os tipos enum (`user_role`,
`integration_platform`...) e todo SQL cru do app vão pelo `search_path` da
conexão — e o usuário do Postgres local tem `search_path = "$user", public`, que
cai no schema `davinci` (o de dev). Resultado: o login quebra no enum ("coluna é
do tipo davinci_local_atendimento.user_role mas a expressão é davinci.user_role")
e um SQL cru leria o schema errado sem avisar. O `api` faz o mesmo que o
tests/conftest.py: fixa o `search_path` em toda conexão nova antes de importar o app.

TRAVAS (o script recusa, não pergunta):
  - schema `davinci`, `public`, `davinci_test*` ou nome fora de [a-z0-9_];
  - DATABASE_URL (ou o Redis, no `api`) com host que não seja deste Mac;
  - Postgres que não roda em macOS (um túnel SSH para produção também responde
    em 127.0.0.1 — o `version()` do servidor denuncia o Linux do servidor);
  - no `api`, requisição HTTP para Shopee, ML, TikTok, Amazon, Bling (e
    Telegram) morre antes de sair do Mac;
  - a caixa de e-mail da Amazon (IMAP/SMTP) do `.env` é zerada em todo
    comando: se o `.env` deste Mac tiver a caixa de verdade, ela não é lida.
As integrações da semente têm credenciais FALSAS (cifradas com a CREDENTIALS_KEY
local): mesmo que alguém ligasse a leitura, nenhum token de produção seria
renovado — refresh token da Shopee/ML é de uso único.

Os nomes das LOJAS são os de verdade (é o que o Eduardo reconhece na tela); os
COMPRADORES, as mensagens, os números de pedido, os rastreios, as avaliações e
as empresas do cadastro (razão social, sem CNPJ) são inventados.
As fotos são URLs públicas dos anúncios no CDN do Mercado Livre — o navegador
de quem abre a tela as busca, como no Duoke; os avatares de comprador são PNGs
genéricos em apps/web/public/atendimento-demo/ (a Shopee entrega a foto do
comprador; ML e Amazon não — a tela mostra as iniciais).

Uso (sempre de dentro de apps/api; --schema tem esse padrão e pode ser omitido):
    uv run python -m scripts.atendimento_local preparar --schema davinci_local_atendimento
    uv run python -m scripts.atendimento_local semente  --schema davinci_local_atendimento
    uv run python -m scripts.atendimento_local api      --schema davinci_local_atendimento \
        --porta 8011
    uv run python -m scripts.atendimento_local limpar   --schema davinci_local_atendimento

Os relógios da semente são RELATIVOS a agora ("vencendo" = faltam menos de 2 h;
o número do pedido da Shopee começa pela data da compra): rode `semente` de novo
no dia do teste para a fila mostrar cada estado.

Model mudou (coluna ou tabela nova)? `semente` e `api` recusam o schema
defasado e dizem o que falta: rode `preparar` de novo (e reinicie a `api`
que estiver no ar — o asyncpg guarda os tipos do schema antigo).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

SCHEMA_PADRAO = "davinci_local_atendimento"
PORTA_PADRAO = 8011
# Aparece em pg_stat_activity: dá para ver quem está conectado no schema local.
NOME_APP = "davinci-atendimento-local"

_SCHEMA_VALIDO = re.compile(r"[a-z][a-z0-9_]{2,62}")
# `davinci` = dev (e o nome do de produção); `davinci_test*` = os schemas que os
# testes apagam e recriam a cada rodada (um DROP aqui derrubaria a suíte alheia).
_SCHEMAS_PROIBIDOS = ("davinci", "public", "information_schema")
_PREFIXOS_PROIBIDOS = ("davinci_test", "pg_")
_HOSTS_LOCAIS = ("127.0.0.1", "localhost", "::1")


# ── Travas ────────────────────────────────────────────────────────────────


def _conferir_schema(schema: str) -> None:
    """Recusa schema que não seja claramente descartável."""
    if not _SCHEMA_VALIDO.fullmatch(schema):
        raise SystemExit(f"schema inválido: {schema!r} (use [a-z0-9_], começando por letra)")
    if schema in _SCHEMAS_PROIBIDOS or schema.startswith(_PREFIXOS_PROIBIDOS):
        raise SystemExit(
            f"recusado: o schema {schema!r} não é deste script "
            "(davinci = dev/produção; davinci_test* = testes)."
        )


def _conferir_hosts(*, com_redis: bool) -> None:
    """DATABASE_URL (e o Redis, se `com_redis`) apontando para este Mac.

    Imprime só o HOST quando recusa — nunca a URL (ela carrega a senha).
    """
    from sqlalchemy.engine import make_url

    from app.config import get_settings

    s = get_settings()
    host = make_url(s.database_url).host
    if host not in _HOSTS_LOCAIS:
        raise SystemExit(f"recusado: DATABASE_URL não é local (host={host!r}).")
    if com_redis:
        for nome, url in (("REDIS_URL", s.redis_url), ("ARQ_REDIS_URL", s.arq_redis_url)):
            host_redis = urlparse(url).hostname
            if host_redis not in _HOSTS_LOCAIS:
                raise SystemExit(f"recusado: {nome} não é local (host={host_redis!r}).")


async def _conferir_servidor(conn) -> None:
    """O Postgres do outro lado roda em macOS (é o deste Mac)?

    Host 127.0.0.1 não basta: `ssh -L 5433:...` para o servidor de produção
    também responde em 127.0.0.1. O servidor de produção é Linux (Docker); o
    local é o Postgres do Homebrew, compilado para darwin.
    """
    from sqlalchemy import text

    versao = (await conn.execute(text("SELECT version()"))).scalar_one()
    if "darwin" not in versao.lower():
        raise SystemExit(
            "recusado: o Postgres conectado não roda em macOS — não parece o deste Mac "
            f"({versao.split(',')[0]})."
        )


def _motor(schema: str, *, com_pool: bool = False):
    """Engine com o `search_path` fixado no schema local em TODA conexão nova.

    Mesma técnica do tests/conftest.py: via `server_settings` do asyncpg, que o
    Postgres aplica na abertura da conexão — não há conexão do pool que escape
    para o schema `davinci`.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import get_settings

    conexao = {
        "server_settings": {
            "search_path": f"{schema},public",
            "application_name": NOME_APP,
            "jit": "off",
        }
    }
    if com_pool:
        return create_async_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            connect_args=conexao,
        )
    return create_async_engine(
        get_settings().database_url, poolclass=NullPool, connect_args=conexao
    )


async def _schema_pronto(conn, schema: str) -> bool:
    from sqlalchemy import text

    tabela = await conn.execute(
        text("SELECT to_regclass(:nome)"), {"nome": f"{schema}.atendimento_conversas"}
    )
    return tabela.scalar_one() is not None


async def _defasagem(conn, schema: str) -> list[str]:
    """Tabelas e colunas dos models que o schema local ainda não tem.

    O schema nasce do `create_all` do dia em que se rodou `preparar`; um
    model que ganhou coluna depois (a parte 2 pôs `categoria`/`tipo`/
    `prioridade` no manual e três tabelas novas) faria a semente cair no
    meio com um erro de SQL — e a API, pior, cair só na tela que usa a coluna.
    Melhor recusar logo e dizer o que falta.
    """
    from sqlalchemy import text

    import app.models  # noqa: F401 — registra todos os models no metadata
    from app.models import Base

    linhas = await conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :s"
        ),
        {"s": schema},
    )
    existentes: dict[str, set[str]] = {}
    for tabela, coluna in linhas:
        existentes.setdefault(tabela, set()).add(coluna)
    faltando: list[str] = []
    for tabela in Base.metadata.sorted_tables:
        colunas = existentes.get(tabela.name)
        if colunas is None:
            faltando.append(tabela.name)
            continue
        faltando.extend(f"{tabela.name}.{c.name}" for c in tabela.columns if c.name not in colunas)
    return faltando


async def _conferir_atualizado(conn, schema: str, *, rode: str) -> None:
    """Recusa o schema que não está preparado ou que ficou atrás dos models."""
    if not await _schema_pronto(conn, schema):
        raise SystemExit(f"o schema {schema} não está preparado: rode {rode}.")
    faltando = await _defasagem(conn, schema)
    if faltando:
        resto = f" (+{len(faltando) - 8})" if len(faltando) > 8 else ""
        amostra = ", ".join(faltando[:8]) + resto
        raise SystemExit(
            f"o schema {schema} é de antes da última mudança nos models (falta: {amostra}): "
            f"rode {rode}."
        )


# ── preparar / limpar ─────────────────────────────────────────────────────

# Cópia de tests/conftest.py::_setup_schema (em sincronia com os models). Os
# models declaram os enums com `create_type=False` — em produção quem cria é a
# migration —, então o `create_all` sozinho não sobe. Não importamos de tests/:
# importar o conftest troca o engine global e aponta para o schema de teste.
_ENUMS: dict[str, tuple[str, ...]] = {
    "user_role": ("admin", "user"),
    "user_status": ("pending", "active", "suspended"),
    "marketplace": (
        "ml", "shopee", "amazon", "aliexpress", "temu", "tiktok", "shein", "magalu", "site",
    ),
    "store_status": ("active", "inactive", "closing", "banned", "pending", "under_review"),
    "cadastro_tipo": ("fone", "email", "dominio", "servidor"),
    "cadastro_status": ("active", "inactive", "excluded"),
    "integration_platform": (
        "bling", "ml", "shopee", "amazon", "tiktok", "temu", "shein", "magalu",
    ),
    "link_sync_status": ("ok", "skipped", "retryable", "fatal", "pending", "requires_review"),
    "background_job_type": (
        "sync_all", "sync_product", "auto_link", "audit", "sync_bling_costs",
        "import_listings", "import_bling_products", "push_prices_batch",
        "backfill_ml_stock", "ingest_bling_order",
    ),
    "background_job_status": ("pending", "running", "succeeded", "failed", "cancelled"),
    "sync_log_action": (
        "refresh_bling", "update_stock", "update_price", "store_status_change",
        "auto_link", "test_connection", "webhook_unmatched",
    ),
    "alert_type": (
        "low_stock", "sync_failure", "listing_banned", "requires_review",
        "daily_sync_completed", "token_expiring", "generic", "tarefa_atribuida",
    ),
    "alert_severity": ("info", "warning", "error", "success"),
    "listing_status": ("active", "paused", "closed", "under_review", "inactive"),
    "listing_request_status": ("pending", "in_progress", "completed", "rejected"),
    "department": ("celular", "mala", "eletro", "catalogo"),
    "pricing_platform": (
        "mercadolivre", "shopee", "temu", "amazon", "aliexpress", "tiktok", "magalu",
    ),
    "cell_status": ("auto", "manual", "locked", "disabled"),
}


async def preparar(schema: str) -> None:
    """Recria o schema: enums, tabelas e o gatilho do Histórico.

    Fica de fora o que só outras telas usam e não vem do `create_all` (a view
    `verificar_margem`, as tabelas de `valuation`, o gatilho de envios do
    `bling_orders`): este ambiente é da caixa de atendimento. O Histórico
    entra porque existe em produção desde a 0331 e grava o que a PESSOA muda
    pela tela — no atendimento, o modo da loja, o manual e as respostas
    prontas; as tabelas com texto de comprador ficam fora (historico/sql.py).
    """
    from sqlalchemy import text

    import app.models  # noqa: F401 — registra todos os models no metadata
    from app.historico import sql as historico_sql
    from app.models import Base

    if Base.metadata.schema != schema:
        raise SystemExit(f"metadata no schema {Base.metadata.schema!r}, esperado {schema!r}")

    motor = _motor(schema)
    try:
        async with motor.begin() as conn:
            await _conferir_servidor(conn)
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            for nome, valores in _ENUMS.items():
                lista = ", ".join(f"'{v}'" for v in valores)
                await conn.execute(text(f'CREATE TYPE "{schema}".{nome} AS ENUM ({lista})'))
            await conn.run_sync(Base.metadata.create_all)
            for comando in historico_sql.funcoes(schema):
                await conn.execute(text(comando))
            sem_gatilho = await conn.execute(
                text(historico_sql.TABELAS_SEM_GATILHO),
                {"schema": schema, "gatilho": historico_sql.NOME_GATILHO},
            )
            tabelas = historico_sql.a_cobrir([r[0] for r in sem_gatilho])
            for tabela in tabelas:
                await conn.execute(text(historico_sql.criar_gatilho(schema, tabela)))
            total = await conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
                ),
                {"s": schema},
            )
            print(
                f"schema {schema} pronto: {total.scalar_one()} tabelas, "
                f"{len(_ENUMS)} enums, gatilho do Histórico em {len(tabelas)} tabelas."
            )
    finally:
        await motor.dispose()


async def limpar(schema: str) -> None:
    from sqlalchemy import text

    motor = _motor(schema)
    try:
        async with motor.begin() as conn:
            await _conferir_servidor(conn)
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        print(f"schema {schema} apagado.")
    finally:
        await motor.dispose()



# ── semente: o catálogo ───────────────────────────────────────────────────
# Anúncios REAIS (amostra do catálogo de 28/09/2026: id, SKU do Bling, título,
# preço e a miniatura que a API do ML devolve). Copiados para cá de propósito:
# a semente não pode depender de arquivo fora do repositório nem de rede.


@dataclass(frozen=True)
class Produto:
    """Um anúncio do catálogo — o que a API da loja devolve para o cartão."""

    mlb: str  # id do anúncio no Mercado Livre
    sku: str  # SKU do Bling (é o que o Duoke mostra no item do pedido)
    titulo: str
    preco: float  # R$
    miniatura: str  # `thumbnail` como veio (http, versão pequena -I)
    preco_original: float | None = None  # preço "de" (riscado), quando há promoção


_CDN = "http://http2.mlstatic.com/"
_FOTO_PEQUENA = re.compile(r"-I\.(jpe?g|png|webp)$", re.IGNORECASE)

PRODUTOS: dict[str, Produto] = {
    "f109s": Produto(
        "MLB6588447948", "dg017.pi+a001.pi",
        "Uranyx Fossibot F109s 256 Gb Memoria 24 Gb Ram Preto", 1647.00,
        _CDN + "D_990184-MLB108806143566_032026-I.jpg",
    ),
    "g1s": Produto(
        "MLB4589356791", "dg069.pi+a004.pi",
        "Uranyx Oukitel G1s 128 Gb Memoria 8 Gb Ram Preto", 1306.00,
        _CDN + "D_967157-MLB109270875804_042026-I.webp",
    ),
    "c68_dourado": Produto(
        "MLB4625588145", "dg061.pi+a004.pi",
        "Caap ** Uranyx Oukitel C68 Plus 128 Gb Memoria 16 Gb Ram Dourado - 16.128", 1214.00,
        _CDN + "D_640860-MLB110119730463_042026-I.jpg",
    ),
    "c68_cinza": Produto(
        "MLB6634299050", "dg060.pi+a004.pi",
        "Uranyx Oukitel C68 Plus 128 Gb Memoria 16 Gb Ram Cinza - 16.128", 1226.00,
        _CDN + "D_930287-MLB110126013821_042026-I.jpg", preco_original=1499.00,
    ),
    "c2_roxo": Produto(
        "MLB6654249566", "dg077.pi+a003.pi",
        "Caap ** Uranyx Oukitel C2 128 Gb Memoria 16 Gb Ram Roxo", 773.00,
        _CDN + "D_916846-MLB109269196522_042026-I.jpg",
    ),
    "c2_preto": Produto(
        "MLB6654247594", "dg074.pi+a004.pi",
        "Caap ** Uranyx Oukitel C2 128 Gb Memoria 16 Gb Ram Preto", 825.00,
        _CDN + "D_972797-MLB110120570027_042026-I.jpg",
    ),
    "wp52": Produto(
        "MLB6654034010", "dg022.pi+a001.pi",
        "Caap ** Uranyx Oukitel Wp52 5g 256 Gb Memoria 16 Gb Ram Preto", 1493.00,
        _CDN + "D_921787-MLB108802370444_032026-I.jpg",
    ),
    "c3_azul": Produto(
        "MLB6574818068", "dg079.pi+a003.pi+a004.pi",
        "Uranyx Oukitel C3 128 Gb Memoria 16 Gb Ram Azul", 859.00,
        _CDN + "D_821151-MLB109229590628_042026-I.jpg",
    ),
    "s7_dourado": Produto(
        "MLB4625602645", "dg003.pi+a003.pi",
        "Cappa **uranyx Fossibot S7 8/128 Gb Memoria Dourado - 8.128", 1081.00,
        _CDN + "D_699733-MLB109795042479_032026-I.jpg",
    ),
    "f110l": Produto(
        "MLB6654449152", "dg046.pi+a001.pi",
        "Cappa **uranyx Fossibot F110l 128 Gb Memoria 8 Gb Ram Preto", 1272.00,
        _CDN + "D_988527-MLB110127841875_042026-I.jpg",
    ),
    "f112pro": Produto(
        "MLB6593278066", "dg083.ra+a003.ra",
        "Uranyx Fossibot F112 Pro 5g 256 Gb Memoria 24 Gb Ram Azul", 1894.00,
        _CDN + "D_917442-MLB110212549515_042026-I.jpg", preco_original=2290.00,
    ),
    "marine1": Produto(
        "MLB6666198804", "dg093.ra+a003",
        "Caap ** Uranyx Oscal Marine 1 128 Gb Memoria 12 Gb Ram Preto", 1500.00,
        _CDN + "D_940948-MLB110843850897_042026-I.jpg",
    ),
    "spark_go_dourado": Produto(
        "MLB4631738643", "dg030.ra", "Tecno Spark Go 1s Dourado", 694.00,
        _CDN + "D_706276-MLA86816102786_072025-I.jpg",
    ),
    "spark_go_branco": Produto(
        "MLB6674369506", "dg024.ra", "Tecno Spark Go 1s 64 Gb 3 Gb De Ram Branco", 630.00,
        _CDN + "D_983676-MLA99991146345_112025-I.jpg",
    ),
    "mala_bordo": Produto(
        "MLB4473151683", "b046.8.12.20",
        "Conjunto De Mala De Bordo 20 Polegadas Com 2 Frasqueiras M5 Prata", 420.00,
        _CDN + "D_958506-MLB106522932576_022026-I.jpg",
    ),
    "mala_24": Produto(
        "MLB6526838384", "b056.24", "Mala De Viagem 24'' Despacho M6 Verde-escuro", 343.00,
        _CDN + "D_728101-MLB109629510069_032026-I.jpg",
    ),
}


def _foto(p: Produto) -> str:
    """A foto como o enriquecer entrega: https e a versão maior (-O) da miniatura (-I).

    Mesma regra de `enriquecer._foto_ml` (`secure_thumbnail`, `-I.jpg` →
    `-O.jpg`, idem .webp/.png). Na demo, a Shopee usa a mesma foto (o anúncio é
    o mesmo produto; o CDN da Shopee exigiria o id real da imagem).
    """
    url = p.miniatura.replace("http://", "https://", 1)
    return _FOTO_PEQUENA.sub(r"-O.\1", url)


def _item_id(p: Produto, plataforma: str) -> str:
    """Id do anúncio no formato da plataforma (Shopee: numérico de 11 dígitos, inventado)."""
    return p.mlb if plataforma == "ml" else "2" + p.mlb.removeprefix("MLB")


def _cartao_produto(chave: str, plataforma: str) -> dict[str, Any]:
    """Cartão de produto (spec 2.2) — o mesmo formato que o sync grava nos anexos."""
    p = PRODUTOS[chave]
    return {
        "tipo": "produto",
        "item_id": _item_id(p, plataforma),
        "titulo": p.titulo,
        "imagem": _foto(p),
        "preco": p.preco,
        "preco_original": p.preco_original,
        "moeda": "BRL",
        # Permalink do ML sem o trecho do título: o ML redireciona. Na Shopee o
        # id é inventado — link nenhum é melhor que um link para página 404.
        "link": (
            f"https://produto.mercadolivre.com.br/MLB-{p.mlb.removeprefix('MLB')}-_JM"
            if plataforma == "ml"
            else None
        ),
    }


# ── semente: os pedidos ───────────────────────────────────────────────────
# Cada pedido vira DUAS coisas: o retrato do marketplace em
# `conversa.dados["pedido_mkt"]` (spec 2.3, o painel "Pedido" igual ao Duoke) e
# as linhas do espelho do Bling (o "No DaVinci" embaixo do painel).

SITUACOES_BLING = {6: "Em aberto", 9: "Atendido", 12: "Cancelado", 15: "Em andamento",
                   21: "Em digitação"}
# Loja no Bling por plataforma (o espelho guarda o id da loja do Bling).
LOJA_BLING = {"shopee": "204500001", "ml": "204500002", "amazon": "204500003"}


@dataclass
class P:
    """Um pedido do roteiro."""

    fonte: str  # shopee | ml | amazon (Amazon: sem retrato, só o espelho do Bling)
    numero: str  # nº no marketplace; na Shopee, "{d}" vira a data da compra (AAMMDD)
    itens: tuple[tuple[str, str | None, int], ...]  # (produto, variação, quantidade)
    ha_dias: float  # comprado há N dias
    status: str | None = None  # código do pedido na plataforma
    pagamento: str | None = None  # já em português, como o enriquecer traduz
    desconto: float = 0.0  # R$ de cupom/promoção sobre os itens
    frete: float = 0.0
    logistica: str | None = None  # código do estado da logística
    logistica_desc: str | None = None  # "descrição mais recente" (ML: o substatus)
    logistica_ha_h: float | None = None  # atualizada há N horas
    transportadora: str | None = None
    rastreio: str | None = None
    bling: str | None = None  # nº do pedido no Bling (None = não chegou no espelho)
    situacao_bling: str | None = None
    enviado_ha_dias: int | None = None  # despacho ("Em andamento" no Bling)
    nf: str | None = None  # nº da NF emitida
    # "Hora de envio" e "Tempo concluído" do painel (P4), no relógio da
    # PLATAFORMA — não do Bling: Shopee = coleta pela transportadora e
    # `update_time` do COMPLETED; ML = `date_shipped` e `date_delivered`.
    enviado_ha_h: float | None = None
    concluido_ha_h: float | None = None


def _criado_em(p: P, agora: datetime) -> datetime:
    return agora - timedelta(days=p.ha_dias)


def _numero(p: P, agora: datetime) -> str:
    """Nº do pedido no marketplace — o da Shopee começa pela data da compra, como o real."""
    return p.numero.format(d=_criado_em(p, agora).strftime("%y%m%d"))


def _iso(quando: datetime | None) -> str | None:
    """ISO UTC em segundos — como o enriquecer grava (a Shopee manda epoch em segundos)."""
    return quando.replace(microsecond=0).isoformat() if quando is not None else None


def _ha_horas(agora: datetime, horas: float | None) -> str | None:
    return _iso(agora - timedelta(hours=horas)) if horas is not None else None


def _valores(p: P) -> tuple[float, float]:
    """(total, valor pago) como cada plataforma entrega.

    Shopee: `total_amount` já é o que o comprador pagou (itens − desconto +
    frete). ML: `total_amount` são os itens; `paid_amount` soma o frete.
    """
    bruto = sum(PRODUTOS[chave].preco * qtd for chave, _, qtd in p.itens)
    if p.fonte == "shopee":
        total = round(bruto - p.desconto + p.frete, 2)
        return total, total
    total = round(bruto - p.desconto, 2)
    return total, round(total + p.frete, 2)


def _itens_do_pedido(p: P) -> list[dict[str, Any]]:
    return [
        {
            "titulo": PRODUTOS[chave].titulo,
            "imagem": _foto(PRODUTOS[chave]),
            "variacao": variacao,
            "sku": PRODUTOS[chave].sku,
            "quantidade": qtd,
            "preco": PRODUTOS[chave].preco,
        }
        for chave, variacao, qtd in p.itens
    ]


def _retrato(p: P, agora: datetime) -> dict[str, Any] | None:
    """`conversa.dados["pedido_mkt"]` (spec 2.3), no formato que o enriquecer grava.

    Os textos em português vêm do próprio `enriquecer` (um texto só para o
    mesmo código). Valores: `_valores`. Shopee: a NF vem do `invoice_data`
    ("pending" antes de emitida). ML: a "descrição" é o substatus do envio e
    a NF não vem no pedido (o painel mostra a do Bling). `enviado_em` e
    `concluido_em` (parte 2, P4): o que o Duoke mostra como "Hora de envio" e
    "Tempo concluído". Sem endereço, CPF, telefone nem nome do comprador.
    """
    from app.services.atendimento import enriquecer

    if p.fonte == "shopee":
        status_pedido = enriquecer.STATUS_PEDIDO_SHOPEE
        status_logistica = enriquecer.STATUS_LOGISTICA_SHOPEE
    elif p.fonte == "ml":
        status_pedido = enriquecer.STATUS_PEDIDO_ML
        status_logistica = enriquecer.STATUS_ENVIO_ML
    else:
        return None  # Amazon: o painel mostra o que o DaVinci sabe (Bling/Logística)
    criado = _criado_em(p, agora)
    pago = p.status not in ("UNPAID", "payment_required")
    # Boleto compensa no dia seguinte; cartão e Pix, na hora.
    pago_em = criado + (timedelta(hours=18) if p.pagamento == "Boleto" else timedelta(minutes=2))
    total, valor_pago = _valores(p)
    if p.fonte == "shopee":
        nf = {"numero": p.nf, "status": None if p.nf else "pending"}
    else:
        nf = {"numero": None, "status": None}
    return {
        "fonte": p.fonte,
        "pedido": _numero(p, agora),
        "status": p.status,
        "status_texto": status_pedido.get(p.status or "", p.status),
        "criado_em": _iso(criado),
        "pago_em": _iso(pago_em) if pago else None,
        "total": total,
        "valor_pago": valor_pago if pago else None,
        # Shopee: o `get_order_detail` não traz o frete que o COMPRADOR pagou
        # (só o custo da logística), e o enriquecer deixa vazio — igual aqui.
        "frete": p.frete if p.fonte == "ml" else None,
        "moeda": "BRL",
        "pagamento_metodo": p.pagamento,
        "itens": _itens_do_pedido(p),
        "logistica": {
            "transportadora": p.transportadora,
            "rastreio": p.rastreio,
            "status": p.logistica,
            "status_texto": (
                status_logistica.get(p.logistica, p.logistica) if p.logistica else None
            ),
            "descricao": p.logistica_desc,
            "atualizado_em": (
                _iso(agora - timedelta(hours=p.logistica_ha_h))
                if p.logistica_ha_h is not None
                else None
            ),
        },
        "nf": nf,
        "enviado_em": _ha_horas(agora, p.enviado_ha_h),
        "concluido_em": _ha_horas(agora, p.concluido_ha_h),
        # O retrato foi tirado há pouco: dentro dos 30 min, nada renova sozinho.
        "atualizado_em": _iso(agora - timedelta(minutes=12)),
    }


_XPRESS = "Shopee Xpress"
_ML_ENVIOS = "Mercado Envios"

PEDIDOS: dict[str, P] = {
    # ── Shopee ──────────────────────────────────────────────────────────────
    "inova_1": P(
        "shopee", "{d}KX4R8T2M", (("f109s", "Preto,24 GB / 256 GB", 1),), 3,
        status="SHIPPED", pagamento="Cartão de crédito", desconto=81.35,
        logistica="LOGISTICS_PICKUP_DONE", logistica_desc="Pedido coletado pela transportadora",
        logistica_ha_h=20, transportadora=_XPRESS, rastreio="BR265118307742X",
        bling="48211", situacao_bling="15", enviado_ha_dias=2,
        enviado_ha_h=20,
    ),
    "inova_2": P(
        "shopee", "{d}QH7N2W5C", (("c2_roxo", "Roxo,16 GB / 128 GB", 1),), 6,
        status="TO_CONFIRM_RECEIVE", pagamento="Pix",
        logistica="LOGISTICS_DELIVERY_DONE", logistica_desc="Pedido entregue",
        logistica_ha_h=7, transportadora=_XPRESS, rastreio="BR265034416518X",
        bling="48150", situacao_bling="9", enviado_ha_dias=5, nf="18398",
        enviado_ha_h=114,
    ),
    "inova_3": P(
        "shopee", "{d}ZT3M9B6P", (("wp52", "Preto,16 GB / 256 GB", 1),), 5,
        status="COMPLETED", pagamento="Cartão de crédito",
        logistica="LOGISTICS_DELIVERY_DONE", logistica_desc="Pedido assinado com sucesso",
        logistica_ha_h=30, transportadora=_XPRESS, rastreio="BR264987712035X",
        bling="48020", situacao_bling="9", enviado_ha_dias=4,
        enviado_ha_h=92, concluido_ha_h=26,
    ),
    "marq_1": P(
        "shopee", "{d}RB8K2L4V",
        (("s7_dourado", "Dourado,8 GB / 128 GB", 1), ("mala_bordo", "Prata", 1)), 1,
        status="READY_TO_SHIP", pagamento="Cartão de crédito", desconto=45.10,
        # Antes da coleta a Shopee não tem rastreio nem evento (medido em 28/09).
        logistica="LOGISTICS_READY", transportadora=_XPRESS,
        bling="48300", situacao_bling="21",
    ),
    "marq_2": P(
        "shopee", "{d}WN5C7Q1J", (("g1s", "Preto,8 GB / 128 GB", 1),), 4,
        status="SHIPPED", pagamento="Boleto",
        logistica="LOGISTICS_DELIVERY_FAILED",
        logistica_desc="Destinatário ausente: nova tentativa no próximo dia útil",
        logistica_ha_h=9, transportadora=_XPRESS, rastreio="BR264876620193X",
        bling="48100", situacao_bling="15", enviado_ha_dias=3,
        enviado_ha_h=70,
    ),
    "marq_3": P(
        "shopee", "{d}HD2F6S9K", (("spark_go_dourado", "Dourado,3 GB / 64 GB", 1),), 2,
        status="PROCESSED", pagamento="Pix",
        logistica="LOGISTICS_REQUEST_CREATED", logistica_desc="Coleta solicitada à transportadora",
        logistica_ha_h=10, transportadora=_XPRESS, rastreio="BR265207719364X",
        bling="48190", situacao_bling="21",
    ),
    "kfa_1": P(
        "shopee", "{d}PM6T3X8A", (("f110l", "Preto,8 GB / 128 GB", 1),), 0.2,
        status="IN_CANCEL", pagamento="Cartão de crédito", transportadora=_XPRESS,
        bling="48320", situacao_bling="6",
    ),
    "kfa_2": P(
        "shopee", "{d}JC9V4N2E", (("marine1", "Preto,12 GB / 128 GB", 1),), 9,
        status="COMPLETED", pagamento="Cartão de crédito", desconto=150.00,
        logistica="LOGISTICS_DELIVERY_DONE", logistica_desc="Pedido entregue",
        logistica_ha_h=100, transportadora=_XPRESS, rastreio="BR264601128857X",
        bling="47950", situacao_bling="9", enviado_ha_dias=8, nf="18311",
        enviado_ha_h=188, concluido_ha_h=96,
    ),
    "kfa_3": P(
        "shopee", "{d}TL1B7R3D", (("c68_dourado", "Dourado,16 GB / 128 GB", 1),), 12,
        status="TO_RETURN", pagamento="Pix",
        logistica="LOGISTICS_DELIVERY_DONE", logistica_desc="Pedido entregue",
        logistica_ha_h=200, transportadora=_XPRESS, rastreio="BR264420093318X",
        bling="47700", situacao_bling="9", enviado_ha_dias=11,
        enviado_ha_h=260,
    ),
    "kia_1": P(
        "shopee", "{d}NF4G8H2Y", (("c3_azul", "Azul,16 GB / 128 GB", 1),), 3,
        status="SHIPPED", pagamento="Cartão de crédito",
        logistica="LOGISTICS_PICKUP_DONE", logistica_desc="Pedido em trânsito",
        logistica_ha_h=30, transportadora=_XPRESS, rastreio="BR265090034471X",
        bling="48230", situacao_bling="15", enviado_ha_dias=2,
        enviado_ha_h=46,
    ),
    "kia_2": P(
        "shopee", "{d}BX7P1K5W", (("f112pro", "Azul,24 GB / 256 GB", 1),), 8,
        status="COMPLETED", pagamento="Cartão de crédito", desconto=94.70,
        logistica="LOGISTICS_DELIVERY_DONE", logistica_desc="Pedido entregue",
        logistica_ha_h=60, transportadora=_XPRESS, rastreio="BR264710055926X",
        bling="47990", situacao_bling="9", enviado_ha_dias=7,
        enviado_ha_h=166, concluido_ha_h=50,
    ),
    "kia_3": P(
        "shopee", "{d}CE3W9M6Q", (("spark_go_branco", "Branco,3 GB / 64 GB", 2),), 15,
        status="COMPLETED", pagamento="Pix",
        logistica="LOGISTICS_DELIVERY_DONE", logistica_desc="Pedido entregue",
        logistica_ha_h=250, transportadora=_XPRESS, rastreio="BR264215578801X",
        bling="47500", situacao_bling="9", enviado_ha_dias=14,
        enviado_ha_h=330, concluido_ha_h=240,
    ),
    # ── Mercado Livre (pós-venda) ───────────────────────────────────────────
    "dream2_1": P(
        "ml", "2000012345678901", (("c2_preto", "Cor: Preto", 1),), 3,
        status="paid", pagamento="Cartão de crédito",
        logistica="shipped", logistica_desc="Em trânsito",
        logistica_ha_h=12, transportadora=_ML_ENVIOS, rastreio="45121873460",
        bling="48210", situacao_bling="15", enviado_ha_dias=2, nf="18432",
        enviado_ha_h=44,
    ),
    "mega_1": P(
        "ml", "2000012345600011", (("mala_24", "Cor: Verde-escuro", 1),), 3,
        status="cancelled", pagamento="Saldo Mercado Pago", frete=24.90,
        logistica="cancelled", logistica_ha_h=48,
        transportadora=_ML_ENVIOS, bling="48060", situacao_bling="12",
    ),
    "mega_2": P(
        "ml", "2000012345633333", (("f109s", "Cor: Preto", 1),), 8,
        status="paid", pagamento="Cartão de crédito",
        logistica="shipped", logistica_desc="Atrasado",
        logistica_ha_h=50, transportadora=_ML_ENVIOS, rastreio="45120098761",
        bling="48055", situacao_bling="15", enviado_ha_dias=6,
        enviado_ha_h=140,
    ),
    # ── Amazon (sem retrato: só o espelho do Bling e a Logística) ───────────
    "amazon_1": P(
        "amazon", "702-4829174-3310257", (("mala_bordo", None, 1),), 6,
        bling="48070", situacao_bling="15", enviado_ha_dias=4,
    ),
    "amazon_2": P(
        "amazon", "701-5512093-8845120", (("spark_go_dourado", None, 1),), 9,
        bling="47600", situacao_bling="9", enviado_ha_dias=8,
    ),
}


# ── semente: as lojas ─────────────────────────────────────────────────────
# Como estão no Duoke (a barra de lojas da esquerda), na mesma ordem.
#
# Parte 2 (P2): a integração tem o nome de SISTEMA que alguém deu ao conectar
# ("mega", "kfa", "dream2") e a caixa mostra o nome da LOJA do cadastro
# (`stores.apelido_override` ou `companies.apelido`), sem o prefixo da
# plataforma — "Shopee Marquezini" vira "Marquezini", como no Duoke. As duas
# formas de vínculo que existem em produção aparecem aqui (`stores.
# integration_id` e `integrations.store_id`), e a VELASCO fica SEM loja ligada:
# a caixa cai no nome da integração.


@dataclass(frozen=True)
class Loja:
    """Uma integração do DaVinci e a loja do cadastro ligada a ela."""

    plataforma: str
    integracao: str  # `integrations.name` (o nome de sistema)
    nome: str  # o que a caixa TEM de mostrar (conferido depois da semente)
    empresa: str | None = None  # `companies.apelido`; None = sem loja ligada
    apelido: str | None = None  # `stores.apelido_override`
    # "loja" = `stores.integration_id` aponta a integração;
    # "integracao" = `integrations.store_id` aponta a loja.
    vinculo: str = "loja"


INTEGRACOES: dict[str, Loja] = {
    "shopee_inova": Loja("shopee", "Inova", "Inova", empresa="Inova"),
    # O caso que o Eduardo apontou: a integração "mega" é a Shopee Marquezini.
    "shopee_marquezini": Loja(
        "shopee", "mega", "Marquezini", empresa="Marquezini", apelido="Shopee Marquezini",
        vinculo="integracao",
    ),
    "shopee_kfa": Loja("shopee", "kfa", "KFA", empresa="KFA"),
    "shopee_kia": Loja("shopee", "Kia", "KIA", empresa="KIA"),
    "shopee_poofy": Loja("shopee", "Poofy", "Poofy", empresa="Poofy"),
    "tiktok_mini": Loja("tiktok", "mini", "Mini", empresa="Mini"),
    "tiktok_barbosa": Loja("tiktok", "Barbosa", "Barbosa", empresa="Barbosa"),
    "tiktok_atv": Loja("tiktok", "atv", "ATV", empresa="ATV", vinculo="integracao"),
    "ml_dream2": Loja(
        "ml", "dream2", "DREAM2", empresa="Dream", apelido="Mercado Livre DREAM2"
    ),
    "ml_mega": Loja("ml", "mega", "MEGA", empresa="MEGA", vinculo="integracao"),
    "ml_poofy": Loja(
        "ml", "poofy", "POOFY", empresa="Poofy", apelido="ML POOFY", vinculo="integracao"
    ),
    "ml_aguiar2": Loja("ml", "aguiar2", "AGUIAR2", empresa="Aguiar", apelido="ML AGUIAR2"),
    "ml_velasco": Loja("ml", "VELASCO", "VELASCO"),
    "amazon_kfa": Loja(
        "amazon", "kfa", "KFA", empresa="KFA", apelido="Amazon KFA", vinculo="integracao"
    ),
}

_SEM_ESCOPO_TIKTOK = "401 · 105005 Access denied: app sem o escopo seller.customer_service"

# Todo canal nasce em `observar` e lendo ok — é o teste em produção. Exceções:
# a TikTok, que o app ainda não tem escopo para ler, e um canal do ML com erro
# passageiro de leitura (a barra de lojas mostra os dois apagados, com o motivo).
# (integração, canal) → (status, último erro)
CANAIS_COM_PROBLEMA: dict[tuple[str, str], tuple[str, str]] = {
    ("tiktok_mini", "chat"): ("sem_escopo", _SEM_ESCOPO_TIKTOK),
    ("tiktok_barbosa", "chat"): ("sem_escopo", _SEM_ESCOPO_TIKTOK),
    ("tiktok_atv", "chat"): ("sem_escopo", _SEM_ESCOPO_TIKTOK),
    ("ml_velasco", "pos_venda"): (
        "erro", "HTTP 429 · muitas requisições (tenta de novo na próxima rodada)"
    ),
}


def _credencial_falsa(plataforma: str, n: int) -> dict[str, Any]:
    """Credencial FALSA de propósito: com ela, nenhuma chamada chega a um token real."""
    if plataforma == "shopee":
        return {"shop_id": 900_000_000 + n, "access_token": "local-falso"}
    if plataforma == "ml":
        return {"user_id": 900_000_000 + n, "access_token": "local-falso"}
    if plataforma == "tiktok":
        return {"shop_cipher": f"local-falso-{n}", "access_token": "local-falso"}
    return {"seller_id": f"LOCALFALSO{n}", "refresh_token": "local-falso"}


# ── semente: o roteiro ────────────────────────────────────────────────────
# Compradores, mensagens, rastreios: tudo INVENTADO. Os tempos são "minutos
# atrás" em relação a agora.
#
# Prazo (constantes.SLA_HORAS): Shopee 12 h, ML pergunta 1 h, ML pós-venda
# 24 h, Amazon 24 h. "Vencendo" = faltam < 2 h; "vencida" = passou.
#
# Modo observação: NADA sai pelo DaVinci. Resposta da loja aqui é sempre a que
# a equipe deu por fora (Duoke/Seller Center) — o gravar marca `externo` e
# aposenta a sugestão pendente, como no sync de verdade.


@dataclass
class M:
    """Uma mensagem do roteiro (grava pelo `gravar.gravar_mensagem`, como o sync)."""

    autor: str  # "cliente" | "loja" | "sistema"
    ha_min: float
    texto: str | None
    # Cartão/anexo: "pedido" (o pedido da conversa), "produto:<chave>" ou
    # "foto:<chave>" (foto que o comprador mandou — na demo, a do anúncio).
    cartao: str | None = None
    status: str | None = None  # força (ex.: "falhou" da moderação do ML)
    erro: str | None = None


@dataclass
class R:
    """Uma sugestão da IA, criada no ponto do roteiro em que aparece."""

    ha_min: float
    texto: str | None
    categoria: str
    confianca: float
    precisa_humano: bool
    motivo: str | None = None
    # Estado final esperado. `substituido` é o gravar que faz (resposta por
    # fora depois da pergunta); `bloqueado` é conferido contra o validador.
    status: str = "pendente"
    # Avaliação (modo observação: 👍 = "ok", 👎 = "erro" + correção).
    acao: str | None = None  # escreveu_do_zero (resposta por fora) | descartou
    nota: str | None = None
    correcao: str | None = None
    motivo_descarte: str | None = None
    avaliado_por: str | None = None


@dataclass
class C:
    """Uma conversa do roteiro."""

    chave: str
    integ: str | None  # chave em INTEGRACOES; None = Amazon sem conta identificada
    canal: str
    externo_id: str
    comprador_nome: str | None
    passos: list[M | R]
    comprador_id: str | None = None
    avatar: int | None = None  # nº do PNG em apps/web/public/atendimento-demo/
    pedido: str | None = None  # chave em PEDIDOS (ou o número cru, se não está lá)
    anuncio: str | None = None  # chave em PRODUTOS: o anúncio da pergunta / do chat
    nao_lidas: int = 0  # o número da plataforma (o mesmo do Duoke)
    situacao: str | None = None  # fechada | bloqueada (depois das mensagens)
    bloqueio_motivo: str | None = None
    sem_resposta: bool = False
    atribuido: str | None = None
    ia_pausada: bool = False
    dados: dict[str, Any] = field(default_factory=dict)


ADMIN = "admin@local.dev"  # o mesmo e-mail do /api/dev/mock-login
PAULA = "paula@local.dev"
RAFAEL = "rafael@local.dev"
USUARIOS = (
    # (e-mail, nome, admin?) — as atendentes com a permissão `atendimento`.
    (ADMIN, "Local Admin", True),
    (PAULA, "Paula (atendimento)", False),
    (RAFAEL, "Rafael (suporte)", False),
)

# A mensagem automática que o Duoke manda quando o pedido entra (prints do Duoke).
BOAS_VINDAS = (
    "Oi! Recebemos seu pedido e já estamos preparando pra envio. Em breve você receberá "
    "o código de rastreio. Obrigado pela compra!"
)
TEXTO_BLOQUEADO = (
    "Olá! Seu pedido chega em 3 dias úteis e o frete da próxima compra é grátis."
)

CONVERSAS: list[C] = [
    # ── Shopee Inova ────────────────────────────────────────────────────────
    C(
        "inova_rastreio_pendente", "shopee_inova", "chat", "518802937461550081",
        "carla.nunes.84", comprador_id="1184502931", avatar=1, pedido="inova_1", nao_lidas=2,
        passos=[
            M("loja", 4318, None, cartao="pedido"),
            M("loja", 4317.5, BOAS_VINDAS),
            M("cliente", 42, "Oi, boa tarde! Meu pedido já foi enviado? Ainda não recebi o "
              "código de rastreio"),
            M("cliente", 41, "Preciso dele até sábado"),
            R(40, "Olá, Carla! Seu pedido já foi coletado pela Shopee Xpress e está a caminho. "
              "O código de rastreio é BR265118307742X e você acompanha cada etapa na página "
              "do pedido, no app da Shopee.", "rastreio", 0.93, False),
        ],
    ),
    C(
        "inova_pre_venda_produto", "shopee_inova", "chat", "518802937461550082",
        "gui.mendes_", comprador_id="1320077415", anuncio="c68_cinza", nao_lidas=3,
        passos=[
            M("cliente", 25, None, cartao="produto:c68_cinza"),
            M("cliente", 24.5, "Boa tarde"),
            M("cliente", 24, "Esse vem com capinha e película? É original?"),
            R(23, "Olá! O Oukitel C68 Plus é original e novo, com nota fiscal. Sobre capinha "
              "e película, vou confirmar com a equipe o que vai na caixa e já te respondo "
              "por aqui.", "duvida_produto", 0.72, False),
        ],
    ),
    # 👍: a IA e a equipe (por fora) disseram a mesma coisa.
    C(
        "inova_nf_parecida_boa", "shopee_inova", "chat", "518802937461550083",
        "patricia.lemos", comprador_id="887341206", avatar=2, pedido="inova_2",
        passos=[
            M("loja", 8636, None, cartao="pedido"),
            M("cliente", 300, "Chegou hoje, amei! A nota fiscal vem por e-mail?"),
            R(299, "Olá, Patrícia! Que bom que chegou! A nota fiscal fica disponível na página "
              "do pedido, no app da Shopee, em Meus pedidos. Qualquer dúvida é só chamar!",
              "nota_fiscal", 0.9, False, status="substituido", acao="escreveu_do_zero",
              nota="ok", avaliado_por=PAULA),
            M("loja", 281, "Oii Patrícia! Que bom que chegou 😊 A nota fica disponível no app "
              "da Shopee, em Meus pedidos > detalhes do pedido. Qualquer coisa é só chamar!"),
        ],
    ),
    C(
        "inova_tela_riscada", "shopee_inova", "chat", "518802937461550084",
        "rodrigo_alves.m", comprador_id="1402958833", pedido="inova_3", nao_lidas=3,
        passos=[
            M("cliente", 845, "O celular chegou com a tela riscada"),
            M("cliente", 844, None, cartao="foto:wp52"),
            M("cliente", 843, "Quero trocar, como faço?"),
            R(842, "Sentimos muito pela tela riscada! Recebemos a sua foto e a equipe vai "
              "analisar o caso e responder aqui pelo chat o quanto antes.",
              "defeito", 0.82, True,
              motivo="assunto só para pessoa (defeito); pedido com devolução no DaVinci"),
        ],
    ),
    # ── Shopee Marquezini ───────────────────────────────────────────────────
    C(
        "marq_cartao_do_cliente", "shopee_marquezini", "chat", "518802937461550085",
        "ana.beatriz.s", comprador_id="1051736620", avatar=3, pedido="marq_1", nao_lidas=4,
        passos=[
            M("cliente", 95, None, cartao="pedido"),
            M("cliente", 94, "Oi! Consegue enviar hoje?"),
            M("cliente", 93, "É presente, preciso até sexta"),
            M("cliente", 60, "??"),
            R(59, "Olá, Ana! Seu pedido já está separado e com a etiqueta pronta, aguardando a "
              "coleta da Shopee Xpress. Assim que for coletado, o rastreio aparece na página "
              "do pedido.", "prazo_envio", 0.8, False),
        ],
    ),
    # 👎: a IA não leu a falha na entrega; a equipe (por fora) acertou.
    C(
        "marq_falha_entrega_ruim", "shopee_marquezini", "chat", "518802937461550086",
        "marcos.vinicius22", comprador_id="1277390148", pedido="marq_2",
        passos=[
            M("loja", 5755, None, cartao="pedido"),
            M("cliente", 480, "O entregador marcou que eu não estava, mas fiquei em casa o "
              "dia todo!"),
            R(479, "Olá, Marcos! Seu pedido está a caminho com a Shopee Xpress. Você acompanha "
              "cada etapa na página do pedido, no app da Shopee.", "rastreio", 0.71, False,
              status="substituido", acao="escreveu_do_zero", nota="erro",
              correcao="A logística mostra \"Falha na entrega\" (destinatário ausente). A "
              "resposta tem que reconhecer isso: pedir desculpas, dizer que a transportadora "
              "faz nova tentativa e que abrimos reclamação com ela.",
              avaliado_por=ADMIN),
            M("loja", 452, "Oi Marcos, sinto muito! Vi aqui que a transportadora registrou "
              "destinatário ausente. Já abrimos uma reclamação com a Shopee Xpress e eles "
              "fazem uma nova tentativa no próximo dia útil. Qualquer novidade te aviso "
              "por aqui."),
        ],
    ),
    C(
        "marq_mala_varias_nao_lidas", "shopee_marquezini", "chat", "518802937461550087",
        "julianarocha.rj", comprador_id="963018457", avatar=1, anuncio="mala_bordo",
        nao_lidas=8,
        passos=[
            M("cliente", 190, None, cartao="produto:mala_bordo"),
            M("cliente", 189.5, "Oi"),
            M("cliente", 189, "Essa mala cabe no bagageiro do avião?"),
            M("cliente", 188, "Qual o peso dela vazia?"),
            M("cliente", 150, "Tem em outra cor?"),
            M("cliente", 120, "Oi??"),
            M("cliente", 118, "Vou fechar a compra se me responder hoje"),
            M("cliente", 117, None, cartao="produto:mala_24"),
            R(116, "Olá! A mala de bordo do conjunto é do tamanho de bagagem de mão das "
              "principais companhias; confira as regras da sua companhia antes do embarque. "
              "O conjunto vem com 2 frasqueiras e, neste anúncio, a cor é prata. O peso da "
              "mala vazia eu confirmo com a equipe e já te respondo por aqui.",
              "duvida_produto", 0.77, False),
        ],
    ),
    # Sugestão barrada pelo validador (prometia prazo e frete grátis).
    C(
        "marq_sugestao_bloqueada", "shopee_marquezini", "chat", "518802937461550088",
        "fe.cardoso", comprador_id="1419906372", pedido="marq_3", nao_lidas=1,
        passos=[
            M("cliente", 170, "Quando chega? É presente de aniversário pra sábado"),
            R(169, TEXTO_BLOQUEADO, "prazo_envio", 0.62, True, status="bloqueado"),
        ],
    ),
    # ── Shopee KFA ──────────────────────────────────────────────────────────
    C(
        "kfa_cancelamento", "shopee_kfa", "chat", "518802937461550089",
        "leo.batista", comprador_id="1108842291", pedido="kfa_1", nao_lidas=3,
        passos=[
            M("cliente", 130, None, cartao="pedido"),
            M("sistema", 129.5, "O comprador solicitou o cancelamento do pedido."),
            M("cliente", 129, "Comprei o modelo errado, quero cancelar"),
            M("cliente", 128, "Já pedi o cancelamento no app, vocês aceitam?"),
            R(127, "Olá! Recebemos o seu pedido de cancelamento. A equipe vai conferir e "
              "responder aqui pelo chat o quanto antes.", "cancelamento", 0.9, True,
              motivo="assunto só para pessoa (cancelamento)"),
        ],
    ),
    C(
        "kfa_agradecimento", "shopee_kfa", "chat", "518802937461550090",
        "sandra.m.oliveira", comprador_id="742215903", avatar=2, pedido="kfa_2",
        passos=[
            M("loja", 12955, None, cartao="pedido"),
            M("cliente", 2900, "Chegou certinho, muito obrigada!"),
            M("loja", 2860, "Nós que agradecemos, Sandra! Boas compras 😊"),
        ],
    ),
    # Só cartão + foto: a IA não sugere sem texto para responder.
    C(
        "kfa_so_foto", "shopee_kfa", "chat", "518802937461550091",
        "joao_p.freitas", comprador_id="1236650078", pedido="kfa_3", nao_lidas=2,
        passos=[
            M("cliente", 75, None, cartao="pedido"),
            M("cliente", 74, None, cartao="foto:c68_dourado"),
        ],
    ),
    # ── Shopee KIA ──────────────────────────────────────────────────────────
    C(
        "kia_cnpj_minhas_descartada", "shopee_kia", "chat", "518802937461550092",
        "vitor.hugo.sp", comprador_id="1388120564", avatar=3, pedido="kia_1", nao_lidas=1,
        atribuido=ADMIN,
        passos=[
            M("cliente", 200, "Preciso da nota fiscal com o CNPJ da minha empresa, dá pra "
              "emitir?"),
            R(198, "Olá! A nota fiscal é emitida com os dados informados na compra e fica "
              "disponível na página do pedido.", "nota_fiscal", 0.66, False,
              status="descartado", acao="descartou", avaliado_por=ADMIN,
              motivo_descarte="NF com CNPJ precisa do fiscal conferir; a IA não sabe isso."),
        ],
    ),
    C(
        "kia_historico_ia_pausada", "shopee_kia", "chat", "518802937461550093",
        "renato.alm", comprador_id="1011945327", pedido="kia_2", nao_lidas=1, ia_pausada=True,
        passos=[
            M("cliente", 12500, None, cartao="produto:f112pro"),
            M("cliente", 12499, "Esse é 5G mesmo?"),
            M("loja", 12480, "Oi! É 5G sim, com 24 GB de RAM e 256 GB de armazenamento."),
            M("cliente", 12470, "Fechado, vou comprar"),
            M("loja", 11518, None, cartao="pedido"),
            M("loja", 11517.5, BOAS_VINDAS),
            M("cliente", 1600, "Chegou! Mas não achei o carregador na caixa, é normal?"),
            M("loja", 1590, "Oi Renato! O carregador fica embaixo da bandeja do aparelho, dá "
              "uma olhadinha ali?"),
            M("cliente", 110, "Achei! Obrigado"),
        ],
    ),
    C(
        "kia_fechada", "shopee_kia", "chat", "518802937461550094",
        "bruna_s.lima", comprador_id="1294403811", pedido="kia_3", situacao="fechada",
        passos=[
            M("cliente", 5000, "Meu pedido já saiu?"),
            M("loja", 4980, "Oi Bruna! Saiu sim, o rastreio está na página do pedido."),
            M("cliente", 4300, "Chegou tudo certo, obrigada!"),
        ],
    ),
    # ── ML DREAM2: perguntas (uma conversa por pergunta, `q:<id>`) ──────────
    # Texto que vai para o ML fica em ISO-8859-1 (sem emoji): a API recusa o resto.
    C(
        "dream2_pergunta_vencendo", "ml_dream2", "pergunta", "q:13500000001",
        "TECH.MARIANA", comprador_id="81234567", anuncio="f112pro", nao_lidas=1,
        passos=[
            M("cliente", 18, "Quanto de memoria RAM ele tem? E o armazenamento?"),
            R(17, "Olá! O Fossibot F112 Pro 5G tem 24 GB de RAM e 256 GB de armazenamento. "
              "Obrigado pela pergunta!", "duvida_produto", 0.94, False),
        ],
    ),
    # A MESMA compradora, no MESMO anúncio, ontem: no painel da pergunta de
    # hoje ela aparece em "Outras perguntas deste comprador".
    C(
        "dream2_pergunta_anterior", "ml_dream2", "pergunta", "q:13500000007",
        "TECH.MARIANA", comprador_id="81234567", anuncio="f112pro",
        passos=[
            M("cliente", 1600, "Tem NFC?"),
            M("loja", 1570, "Olá! Tem sim, NFC para pagamento por aproximação. Obrigado pela "
              "pergunta!"),
        ],
    ),
    C(
        "dream2_pergunta_desconto_vencida", "ml_dream2", "pergunta", "q:13500000002",
        "PAULO_R2019", comprador_id="73456120", anuncio="g1s", nao_lidas=1,
        passos=[
            M("cliente", 190, "Faz por 1100 no pix?"),
            R(189, "Olá! O preço do anúncio já é o melhor que conseguimos oferecer, e o "
              "pagamento pode ser parcelado pelo Mercado Pago. Obrigado pela pergunta!",
              "desconto", 0.85, True, motivo="assunto só para pessoa (desconto)"),
        ],
    ),
    # Respondida por fora, parecida com a da IA, AINDA sem avaliação: é aqui que
    # se testa o 👍/👎 do "A IA teria respondido".
    C(
        "dream2_pergunta_respondida_fora", "ml_dream2", "pergunta", "q:13500000003",
        "LU.TAVARES", comprador_id="65012398", anuncio="c68_dourado",
        passos=[
            M("cliente", 1500, "Vem com carregador?"),
            R(1499, "Olá! Vem sim, o Oukitel C68 Plus acompanha carregador e cabo USB. "
              "Obrigado pela pergunta!", "duvida_produto", 0.88, False, status="substituido"),
            M("loja", 1440, "Olá! Acompanha carregador, cabo, capinha e película. Obrigado "
              "pela pergunta!"),
        ],
    ),
    # ── ML DREAM2: pós-venda ────────────────────────────────────────────────
    C(
        "dream2_pos_venda_nf", "ml_dream2", "pos_venda", "2000012345678901",
        "HELENA_COSTA10", comprador_id="90112233", pedido="dream2_1", nao_lidas=1,
        passos=[
            M("cliente", 300, "Boa tarde, pode mandar a nota fiscal?"),
            R(299, "Olá, Helena! A nota fiscal do seu pedido já foi emitida e está disponível "
              "na página da compra, no Mercado Livre.", "nota_fiscal", 0.9, False),
        ],
    ),
    # ── ML MEGA: pós-venda ──────────────────────────────────────────────────
    C(
        "mega_bloqueada_cancelada", "ml_mega", "pos_venda", "2000012345600011",
        "RAFA.SOUZA88", comprador_id="91822002", pedido="mega_1", situacao="bloqueada",
        bloqueio_motivo="Pedido cancelado: o Mercado Livre bloqueou novas mensagens nesta "
        "compra.",
        dados={"status_ml": "blocked", "substatus_ml": "blocked_by_cancelled_order"},
        passos=[
            M("cliente", 3000, "Quero cancelar, comprei a cor errada."),
            M("loja", 2980, "Olá! Pedimos o cancelamento, o reembolso sai pelo Mercado Pago."),
            M("cliente", 2000, "Obrigado. E o estorno cai quando?"),
        ],
    ),
    # A resposta por fora foi REPROVADA na moderação do ML: o comprador não
    # recebeu, a conversa continua esperando (e a sugestão continua valendo).
    C(
        "mega_atraso_moderada", "ml_mega", "pos_venda", "2000012345633333",
        "CAMILA_ROCHA_SP", comprador_id="91822004", pedido="mega_2", nao_lidas=2,
        passos=[
            M("cliente", 1380, "Ola, quando chega meu pedido? Ja faz uma semana."),
            M("loja", 1370, "Ola! Pode chamar a gente no WhatsApp que resolvemos rapidinho.",
              status="falhou", erro="moderada_ml"),
            M("cliente", 1340, "Oi? Nao recebi resposta"),
            R(1339, "Olá, Camila! Sentimos pela demora. O seu pedido está com o Mercado "
              "Envios e o acompanhamento fica na página da compra. A equipe já abriu um "
              "chamado com a transportadora e responde por aqui assim que houver novidade.",
              "rastreio", 0.74, False),
        ],
    ),
    # ── ML AGUIAR2: pergunta respondida por fora, com texto DIFERENTE ───────
    C(
        "aguiar2_pergunta_medida", "ml_aguiar2", "pergunta", "q:13500000004",
        "SERGIO.VIAGENS", comprador_id="55310022", anuncio="mala_24",
        passos=[
            M("cliente", 900, "Qual a medida da mala?"),
            R(899, "Olá! A Mala de Viagem M6 é do tamanho de despacho. As medidas completas "
              "estão na ficha técnica do anúncio. Obrigado pela pergunta!",
              "duvida_produto", 0.7, False, status="substituido"),
            M("loja", 870, "Olá! A mala mede 67 x 44 x 27 cm, tamanho de despacho (24 "
              "polegadas). Obrigado pela pergunta!"),
        ],
    ),
    # ── ML VELASCO: perguntas (o pós-venda está com erro de leitura) ────────
    C(
        "velasco_pronta_entrega", "ml_velasco", "pergunta", "q:13500000005",
        "BRUNA_TECH", comprador_id="44009812", anuncio="spark_go_branco", nao_lidas=1,
        passos=[
            M("cliente", 12, "Tem pronta entrega?"),
            R(11, "Olá! Temos sim, o produto está disponível para compra imediata. Obrigado "
              "pela pergunta!", "duvida_produto", 0.83, False),
        ],
    ),
    # Sem sugestão ainda (a IA escreve depois de 90 s de silêncio e da rodada).
    C(
        "velasco_sem_sugestao", "ml_velasco", "pergunta", "q:13500000006",
        "DUDA_ALMEIDA", comprador_id="33221100", anuncio="c68_cinza", nao_lidas=1,
        passos=[M("cliente", 52, "É 4G ou 5G?")],
    ),
    # ── Amazon kfa (e-mail; sem retrato do pedido — só o que o DaVinci sabe) ─
    C(
        "amazon_rastreio", "amazon_kfa", "email",
        "k7m2x9q4w1ab3@marketplace.amazon.com.br|702-4829174-3310257", "Beatriz Lacerda",
        comprador_id="k7m2x9q4w1ab3@marketplace.amazon.com.br", pedido="amazon_1",
        dados={"assunto": "Pergunta sobre o pedido 702-4829174-3310257"},
        passos=[
            M("cliente", 1350, "Olá, meu pedido 702-4829174-3310257 ainda não chegou, a "
              "previsão era ontem. Podem verificar?"),
            R(1348, "Olá, Beatriz! Verificamos o pedido 702-4829174-3310257: ele está com a "
              "transportadora e o acompanhamento fica na página do pedido na Amazon.",
              "rastreio", 0.86, False),
        ],
    ),
    C(
        "amazon_conta_nao_identificada", None, "email",
        "z1x2c3v4b5n6m@marketplace.amazon.com.br|703-1188225-6600413", "Otávio Nogueira",
        comprador_id="z1x2c3v4b5n6m@marketplace.amazon.com.br", pedido="703-1188225-6600413",
        dados={"assunto": "Nota fiscal"},
        passos=[M("cliente", 1600, "Gostaria de saber se vocês emitem nota fiscal.")],
    ),
    C(
        "amazon_obrigado_sem_resposta", "amazon_kfa", "email",
        "r5t8y2u6i9op0@marketplace.amazon.com.br|701-5512093-8845120", "Sofia Brandão",
        comprador_id="r5t8y2u6i9op0@marketplace.amazon.com.br", pedido="amazon_2",
        sem_resposta=True, dados={"assunto": "Pedido 701-5512093-8845120"},
        passos=[M("cliente", 2000, "Recebi tudo certo, obrigada!")],
    ),
]

# ── semente: o cliente (parte 2, P5) ──────────────────────────────────────
# O cartão "Cliente" do painel lê dois ÍNDICES próprios, porque a Shopee não
# filtra pedido nem avaliação por comprador: `atendimento_pedidos_comprador`
# (cada retrato de pedido que o enriquecer tira grava a linha, com o
# `buyer_user_id` — é id, não dado pessoal — e o job de indexação pega o
# resto) e `atendimento_avaliacoes_loja` (o `get_comment` da loja, casado
# pelo `buyer_username` = nome do comprador na conversa e pelo pedido).
# A semente grava pelos mesmos `indice.registrar_*` do job.
#
# Os cinco retratos do roteiro (o resto dos compradores tem só o pedido da
# conversa — a "primeira compra" de quem nunca comprou antes):
#   ana.beatriz.s     (Marquezini) recorrente: cliente desde mar/2026, 3 compras
#   sandra.m.oliveira (KFA)        avaliou 5★ o pedido da conversa (respondida)
#   rodrigo_alves.m   (Inova)      avaliou 2★ — a tela riscada (sem resposta)
#   joao_p.freitas    (KFA)        pedido da conversa em devolução + 1 anterior
#   carla.nunes.84    (Inova)      primeira compra
# ML: os pedidos do comprador vêm AO VIVO do `/orders/search?buyer` (a API
# local barra a chamada), e o índice guarda só o pedido da conversa —
# o que o enriquecer gravaria. TikTok e Amazon: sem índice (sem inventar).

# chave da conversa → compras ANTERIORES à do roteiro (só no índice: não
# têm conversa, retrato nem espelho do Bling).
COMPRAS_ANTERIORES: dict[str, tuple[P, ...]] = {
    "marq_cartao_do_cliente": (
        P("shopee", "{d}A2M7K9Q4", (("c2_roxo", "Roxo,16 GB / 128 GB", 1),), 190,
          status="COMPLETED", desconto=38.65),
        P("shopee", "{d}F8R3T6W1", (("mala_24", "Verde-escuro", 1), ("mala_bordo", "Prata", 1)),
          75, status="COMPLETED", desconto=40.00),
    ),
    "inova_nf_parecida_boa": (
        P("shopee", "{d}G5N1Z8C3", (("spark_go_branco", "Branco,3 GB / 64 GB", 1),), 120,
          status="COMPLETED"),
    ),
    "kfa_agradecimento": (
        P("shopee", "{d}H4P9V2L7", (("spark_go_dourado", "Dourado,3 GB / 64 GB", 1),), 60,
          status="COMPLETED"),
    ),
    "kfa_so_foto": (
        P("shopee", "{d}J3D6M1S8", (("c2_preto", "Preto,16 GB / 128 GB", 1),), 40,
          status="COMPLETED"),
    ),
    # Comprou e cancelou há 20 dias; o da conversa está "Cancelando".
    "kfa_cancelamento": (
        P("shopee", "{d}K7B2X5R9", (("f109s", "Preto,24 GB / 256 GB", 1),), 20,
          status="CANCELLED"),
    ),
}


@dataclass(frozen=True)
class Av:
    """Uma avaliação da loja (`get_comment` da Shopee), casada com o comprador."""

    conversa: str  # chave da conversa: dá a loja e o `buyer_username`
    pedido: P  # o pedido avaliado (de PEDIDOS ou de COMPRAS_ANTERIORES)
    estrelas: int
    texto: str | None
    ha_dias: float
    resposta: str | None = None  # `comment_reply.reply` da loja


AVALIACOES_LOJA: tuple[Av, ...] = (
    Av("kfa_agradecimento", PEDIDOS["kfa_2"], 5,
       "Chegou antes do prazo, bem embalado e original. Recomendo a loja!", 3.8,
       resposta="Obrigado pela avaliação, Sandra! Volte sempre."),
    Av("inova_tela_riscada", PEDIDOS["inova_3"], 2,
       "O celular chegou com a tela riscada. Esperava mais cuidado na embalagem.", 0.6),
    Av("marq_cartao_do_cliente", COMPRAS_ANTERIORES["marq_cartao_do_cliente"][0], 5,
       "Celular muito bom, chegou rápido.", 180,
       resposta="Que bom que gostou! Obrigado pela compra."),
)


# ── semente: o manual da IA (parte 2, P7) ─────────────────────────────────
# As categorias ficam no banco (`atendimento_categorias`) e a IA classifica
# pela DESCRIÇÃO de cada uma; tabela vazia = `constantes.CATEGORIAS`. Estas 12
# são um EXEMPLO até chegar a taxonomia do manual base (que se importa com
# `scripts.atendimento_manual importar`): as 14 das constantes com garantia
# dentro de defeito e reembolso dentro de troca/devolução. `lacunas` = os
# fatos do sistema que a resposta daquela categoria pode usar
# (`constantes.LACUNAS`); `so_humano` = a IA sugere, quem envia é pessoa.
CATEGORIAS_MANUAL: tuple[dict[str, Any], ...] = (
    {"id": "rastreio", "nome": "Rastreio",
     "descricao": "O pedido JÁ FOI ENVIADO: onde está, código de rastreio, atraso, falha "
                  "na entrega.",
     "exemplos": ["Meu pedido já foi enviado?", "Qual o código de rastreio?",
                  "O entregador marcou que eu não estava"],
     "so_humano": False, "lacunas": ["numero_pedido", "rastreio", "transportadora",
                                     "previsao_entrega", "data_envio"]},
    {"id": "prazo_envio", "nome": "Prazo de envio",
     "descricao": "O pedido AINDA NÃO FOI ENVIADO: quando sai, se dá para enviar hoje, "
                  "pressa para uma data (presente, viagem).",
     "exemplos": ["Consegue enviar hoje?", "Chega até sábado?"],
     "so_humano": False, "lacunas": ["numero_pedido", "data_envio", "previsao_entrega"]},
    {"id": "nota_fiscal", "nome": "Nota fiscal",
     "descricao": "Pedido da NF, onde encontrar, dados da nota.",
     "exemplos": ["A nota fiscal vem por e-mail?", "Pode mandar a nota?"],
     "so_humano": False, "lacunas": ["numero_pedido", "nf_numero"]},
    {"id": "duvida_produto", "nome": "Dúvida sobre o produto",
     "descricao": "Pré-venda: memória, rede, medida, cor, o que vem na caixa, se é original.",
     "exemplos": ["É 4G ou 5G?", "Vem com carregador?", "Qual a medida da mala?"],
     "so_humano": False, "lacunas": []},
    {"id": "troca_devolucao", "nome": "Troca, devolução ou reembolso",
     "descricao": "Quer trocar, devolver ou receber o dinheiro de volta; arrependimento.",
     "exemplos": ["Quero devolver", "Quando cai o estorno?"],
     "so_humano": True, "lacunas": ["numero_pedido"]},
    {"id": "cancelamento", "nome": "Cancelamento",
     "descricao": "Pede para cancelar o pedido ou pergunta do cancelamento pedido no app.",
     "exemplos": ["Comprei errado, quero cancelar"],
     "so_humano": True, "lacunas": ["numero_pedido"]},
    {"id": "defeito", "nome": "Defeito ou garantia",
     "descricao": "Chegou quebrado, riscado, não liga, parou de funcionar; garantia.",
     "exemplos": ["Chegou com a tela riscada", "Parou de carregar"],
     "so_humano": True, "lacunas": ["numero_pedido"]},
    {"id": "endereco", "nome": "Endereço",
     "descricao": "Mudar ou corrigir endereço de entrega (dado pessoal: só pessoa).",
     "exemplos": ["Errei o número da casa"],
     "so_humano": True, "lacunas": ["numero_pedido"]},
    {"id": "desconto", "nome": "Desconto",
     "descricao": "Pede desconto, preço menor, condição no Pix ou cupom.",
     "exemplos": ["Faz por 1100 no pix?"],
     "so_humano": True, "lacunas": []},
    {"id": "reclamacao_forte", "nome": "Reclamação forte",
     "descricao": "Ameaça de Procon, Reclame Aqui, advogado; xingamento; mediação aberta.",
     "exemplos": ["Vou abrir reclamação no Procon"],
     "so_humano": True, "lacunas": []},
    {"id": "agradecimento", "nome": "Agradecimento",
     "descricao": "Só agradece ou confirma que recebeu; não pede nada.",
     "exemplos": ["Chegou certinho, obrigada!"],
     "so_humano": False, "lacunas": []},
    {"id": "outro", "nome": "Outro",
     "descricao": "Nada acima; a IA sugere com cautela e a pessoa confere.",
     "exemplos": [], "so_humano": False,
     # Categoria-coringa: marca o buraco do manual para a revisão semanal.
     "lacunas": []},
)


@dataclass(frozen=True)
class Rg:
    """Uma regra do manual (QUANDO → FAÇA), com o tipo e a ordem no prompt."""

    tipo: str  # seguranca (todas, primeiro) | categoria (só a da mensagem) | estilo (por último)
    quando: str
    faca: str
    categoria: str | None = None
    plataforma: str | None = None
    canal: str | None = None
    prioridade: int = 100


# 2 de segurança, 5 de categoria (uma por categoria/plataforma/canal: nenhuma
# em conflito — a semente confere com `manual.conflitos_existentes`) e 1 de
# estilo. A regra da "falha na entrega" da rodada anterior saiu: o que ela
# ensinava está na CORREÇÃO do 👎 da conversa do marcos.vinicius22, que é
# como a equipe ensina a IA sem regra nova (P3).
REGRAS: tuple[Rg, ...] = (
    Rg("seguranca", "Qualquer mensagem",
       "Nunca peça nem repita CPF, telefone, e-mail ou endereço, e nunca passe contato fora "
       "da plataforma (WhatsApp, link, @, Pix).", prioridade=10),
    Rg("seguranca", "O cliente fala em Procon, Reclame Aqui, advogado, estorno ou cancelamento",
       "Não prometa nada nem negue o direito de arrependimento de 7 dias: diga que a equipe "
       "vai analisar e responder por aqui.", prioridade=20),
    Rg("categoria", "O cliente pergunta onde está o pedido e o sistema tem rastreio",
       "Informe o código {rastreio} e a transportadora {transportadora}. Não prometa data de "
       "entrega que não veio do sistema.", categoria="rastreio"),
    Rg("categoria", "Pergunta de memória, armazenamento, rede (4G/5G) ou medida no anúncio",
       "Responda só com o que está no título e na ficha do anúncio. Se não souber, diga que vai "
       "confirmar com a equipe.", categoria="duvida_produto", plataforma="ml", canal="pergunta"),
    Rg("categoria", "O cliente pede a nota fiscal",
       "Diga que a NF {nf_numero} foi emitida e está disponível na página do pedido. Nunca "
       "mande por e-mail ou WhatsApp.", categoria="nota_fiscal"),
    Rg("categoria", "Produto chegou com defeito ou quebrado",
       "Peça desculpas, peça uma foto pelo chat da própria plataforma e diga que a equipe vai "
       "analisar. Não prometa troca nem reembolso.", categoria="defeito", plataforma="shopee",
       canal="chat"),
    Rg("categoria", "O cliente tem pressa (presente, viagem) ou pergunta se sai hoje",
       "Diga o estado real do pedido (separado, etiqueta pronta, aguardando coleta). Nunca "
       "prometa data de envio nem de entrega que não veio do sistema.", categoria="prazo_envio"),
    Rg("estilo", "Toda resposta",
       "Comece com \"Olá\" e o primeiro nome quando a plataforma mostrar; frases curtas, sem "
       "gíria; sem emoji no Mercado Livre e na Amazon.", prioridade=900),
)

MODELOS = (
    # (título, texto, plataforma, canal, categoria) — a categoria deixa a tela
    # sugerir a resposta pronta do assunto da conversa (P7).
    ("Rastreio disponível",
     "Olá! Seu pedido já foi enviado. O código de rastreio está na página do pedido, e por lá "
     "você acompanha cada etapa da entrega.", None, None, "rastreio"),
    ("Pedido em separação",
     "Olá! Seu pedido está em separação. Assim que for postado, o rastreio aparece na página "
     "do pedido.", None, None, "prazo_envio"),
    ("Nota fiscal",
     "Olá! A nota fiscal do seu pedido já foi emitida e está disponível na página da compra.",
     None, None, "nota_fiscal"),
    ("Defeito: pedir foto",
     "Sentimos muito! Pode mandar uma foto do problema aqui pelo chat? A equipe analisa e "
     "responde o quanto antes.", "shopee", "chat", "defeito"),
    ("Carregador na caixa",
     "Olá! O carregador vem na caixa, embaixo da bandeja do aparelho. Dá uma olhadinha ali?",
     None, None, "duvida_produto"),
)


# ── semente: a gravação ───────────────────────────────────────────────────


def _hash(*partes: str) -> str:
    return hashlib.sha1(":".join(partes).encode()).hexdigest()  # noqa: S324 — id de exemplo


def _id_mensagem(c: C, plataforma: str, i: int, m: M) -> str:
    """Um id no formato de cada plataforma (determinístico: semente repetível)."""
    h = _hash(c.chave, str(i))
    if plataforma == "ml" and c.canal == "pergunta":
        # ML: UMA conversa por pergunta (`q:<id>`, D3). A pergunta é a
        # mensagem `q:<id>` — o mesmo id da conversa — e a resposta, `a:<id>`.
        qid = c.externo_id.removeprefix("q:")
        return f"q:{qid}" if m.autor == "cliente" else f"a:{qid}"
    if plataforma == "ml":
        return h[:32]
    if plataforma == "amazon":
        dominio = "marketplace.amazon.com.br" if m.autor == "cliente" else "mail.local.dev"
        return f"<{h[:24]}@{dominio}>"
    return str(int(h[:15], 16))  # Shopee/TikTok: message_id numérico


def _avatar(n: int | None) -> str | None:
    """Foto do comprador: PNG genérico servido pela própria web local (`public/`)."""
    return f"/atendimento-demo/avatar-{n}.png" if n else None


def _conteudo(m: M, plataforma: str, retrato: dict | None) -> tuple[str, list[dict] | None]:
    """Tipo e anexos da mensagem — o cartão no formato que o sync grava (spec 2.2)."""
    if m.cartao is None:
        return "texto", None
    if m.cartao == "pedido":
        if retrato is None:
            raise SystemExit(f"cartão de pedido sem pedido no marketplace: {m!r}")
        from app.services.atendimento.enriquecer import cartao_pedido

        return "pedido", [cartao_pedido(retrato)]
    tipo, _, chave = m.cartao.partition(":")
    if tipo == "produto":
        return "produto", [_cartao_produto(chave, plataforma)]
    if tipo == "foto":
        return "imagem", [{"tipo": "imagem", "url": _foto(PRODUTOS[chave])}]
    raise SystemExit(f"cartão desconhecido: {m.cartao!r}")


async def _usuarios(s) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models import User, UserRole, UserStatus

    perm = {"atendimento": {"view": True, "edit": True, "delete": False}}
    saida: dict[str, Any] = {}
    for email, nome, admin in USUARIOS:
        u = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if u is None:
            # O /api/dev/mock-login acha o admin pelo e-mail: tanto faz quem cria.
            u = User(open_id=f"local-atendimento-{email.split('@')[0]}", email=email)
            s.add(u)
        u.name = nome
        u.role = UserRole.ADMIN if admin else UserRole.USER
        u.status = UserStatus.ACTIVE
        if not admin:
            u.permissions = perm
        saida[email] = u
    await s.flush()
    return saida


def _chave_nf(numero: str, emissao: datetime) -> str:
    """Chave de acesso de 44 dígitos no formato da SEFAZ (CNPJ e DV inventados)."""
    return (
        f"35{emissao.strftime('%y%m')}00000000000191" f"55001{int(numero):09d}1{int(numero):08d}0"
    )


async def _espelho(s, agora: datetime) -> int:
    """O "No DaVinci" do painel: espelho do Bling, logística, devolução, chamado e NF."""
    from app.models import BlingOrder, Chamado, Devolution, Logistica, NfNota, SituacaoBling

    for sid, nome in SITUACOES_BLING.items():
        s.add(SituacaoBling(id=sid, nome=nome))
    no_bling = 0
    for bling_id, p in enumerate(PEDIDOS.values(), start=17_000_000_001):
        if p.bling is None:
            continue
        no_bling += 1
        for idx, (chave, _variacao, qtd) in enumerate(p.itens):
            s.add(
                BlingOrder(
                    bling_id=bling_id,
                    numero=p.bling,
                    numeroloja=_numero(p, agora),
                    data=_criado_em(p, agora),
                    situacao=p.situacao_bling,
                    loja=LOJA_BLING[p.fonte],
                    item_index=idx,
                    item_codigo=PRODUTOS[chave].sku,
                    item_descricao=PRODUTOS[chave].titulo,
                    item_quantidade=qtd,
                    em_andamento_data=(
                        (agora - timedelta(days=p.enviado_ha_dias)).date()
                        if p.enviado_ha_dias is not None
                        else None
                    ),
                )
            )
        if p.nf:
            emissao = _criado_em(p, agora) + timedelta(days=1)
            s.add(
                NfNota(
                    chave=_chave_nf(p.nf, emissao), pedido_bling=p.bling, numero=p.nf,
                    serie="1", data_emissao=emissao, situacao="100",
                    xml=b"<nfeProc><!-- NF de exemplo, local --></nfeProc>",
                )
            )

    def _dia(delta_dias: int) -> date:
        return (agora + timedelta(days=delta_dias)).date()

    def _lg(chave: str, conta: str, **campos: Any) -> Any:
        p = PEDIDOS[chave]
        return Logistica(
            pedido_bling=p.bling, pedido_marketplace=_numero(p, agora), plataforma=p.fonte,
            conta=conta, rastreio=p.rastreio, servico_envio=p.transportadora, **campos,
        )

    s.add_all(
        [
            _lg("inova_1", "Inova", postagem_data=_dia(-2), previsao_correios=_dia(2),
                localizacao="Objeto em trânsito - por favor aguarde"),
            _lg("inova_3", "Inova", postagem_data=_dia(-4), previsao_correios=_dia(-1),
                entregue_em=agora - timedelta(hours=30)),
            _lg("kfa_2", "KFA", postagem_data=_dia(-8), previsao_correios=_dia(-4),
                entregue_em=agora - timedelta(hours=100)),
            _lg("mega_2", "MEGA", postagem_data=_dia(-6), previsao_correios=_dia(-1),
                problema_correios="atraso na unidade de distribuição"),
        ]
    )
    amazon = PEDIDOS["amazon_1"]
    s.add(
        Logistica(
            pedido_bling=amazon.bling, pedido_marketplace=amazon.numero, plataforma="amazon",
            conta="kfa", rastreio="TBA312345678901", servico_envio="Logística Amazon DBA",
            postagem_data=_dia(-4), prazo_entrega_amazon=_dia(-1),
            localizacao="Saiu para entrega",
        )
    )
    tela = PEDIDOS["inova_3"]
    s.add(
        Devolution(
            data=agora - timedelta(hours=10), pedido_bling=tela.bling,
            pedido_marketplace=_numero(tela, agora), conta="Inova",
            sku=PRODUTOS["wp52"].sku, produtos=PRODUTOS["wp52"].titulo,
            motivo_devolucao="Tela riscada na entrega",
            reembolso=False, devolver_estoque=False, manutencao=False, quantidade=1,
        )
    )
    atraso = PEDIDOS["mega_2"]
    s.add(
        Chamado(
            data=_dia(-1), pedido_bling=atraso.bling, pedido_marketplace=atraso.numero,
            plataforma="ml", conta="MEGA", origem="logistica", chamado="5234567891",
            canal="api", status_plataforma="em_analise", resolvido=False,
        )
    )
    await s.flush()
    return no_bling


async def _integracoes(s, dono, agora: datetime) -> tuple[dict[str, Any], dict[tuple, Any]]:
    """As lojas do Duoke e os canais de cada uma — todos em `observar`.

    Cada integração nasce com o nome de SISTEMA e, quando tem loja no
    cadastro, ligada a ela pela forma de vínculo do roteiro (`Loja.vinculo`).
    """
    from app.models import AtendimentoCanal, Company, Integration, Store
    from app.models.enums import IntegrationPlatform, Marketplace, StoreStatus
    from app.security.cipher import encrypt_json
    from app.services.atendimento.constantes import CANAIS_POR_PLATAFORMA, MODO_OBSERVAR

    empresas: dict[str, Any] = {}
    for loja in INTEGRACOES.values():
        if loja.empresa and loja.empresa not in empresas:
            # Sem CNPJ nem IP: as duas colunas são únicas no cadastro, e a
            # semente não pode inventar um documento que pareça de verdade.
            empresas[loja.empresa] = Company(
                razao_social=f"{loja.empresa} Comércio (exemplo local)", apelido=loja.empresa
            )
            s.add(empresas[loja.empresa])
    await s.flush()

    integ: dict[str, Any] = {}
    for n, (chave, loja) in enumerate(INTEGRACOES.items(), start=1):
        i = Integration(
            user_id=dono.id,
            platform=IntegrationPlatform(loja.plataforma),
            name=loja.integracao,
            credentials=encrypt_json({**_credencial_falsa(loja.plataforma, n), "local": True}),
            token_expires_at=agora + timedelta(days=365),
            status="active",
        )
        s.add(i)
        integ[chave] = i
    await s.flush()

    for chave, loja in INTEGRACOES.items():
        if loja.empresa is None:
            continue  # sem loja ligada: a caixa mostra o nome da integração
        store = Store(
            company_id=empresas[loja.empresa].id,
            marketplace=Marketplace(loja.plataforma),
            apelido_override=loja.apelido,
            status=StoreStatus.ACTIVE,
            integration_id=integ[chave].id if loja.vinculo == "loja" else None,
        )
        s.add(store)
        await s.flush()
        if loja.vinculo == "integracao":
            integ[chave].store_id = store.id
    await s.flush()

    # O número de não lidas do canal é o da plataforma: a soma das conversas.
    nao_lidas: dict[tuple[str | None, str], int] = {}
    for c in CONVERSAS:
        nao_lidas[(c.integ, c.canal)] = nao_lidas.get((c.integ, c.canal), 0) + c.nao_lidas

    canais: dict[tuple, Any] = {}
    for chave, loja in INTEGRACOES.items():
        for canal in CANAIS_POR_PLATAFORMA[loja.plataforma]:
            status, erro = CANAIS_COM_PROBLEMA.get((chave, canal), ("ok", None))
            c = AtendimentoCanal(
                integration_id=integ[chave].id,
                plataforma=loja.plataforma,
                canal=canal,
                modo=MODO_OBSERVAR,
                status=status,
                cursor={"ultima_rodada": (agora - timedelta(minutes=2)).isoformat()},
                nao_lidas_plataforma=(
                    None if status == "sem_escopo" else nao_lidas.get((chave, canal), 0)
                ),
                ultimo_ok_em=None if status == "sem_escopo" else agora - timedelta(minutes=2),
                ultimo_erro_em=agora - timedelta(minutes=1) if erro else None,
                ultimo_erro=erro,
            )
            if status == "erro":
                c.ultimo_ok_em = agora - timedelta(minutes=45)
            s.add(c)
            canais[(chave, canal)] = c
    await s.flush()
    return integ, canais


async def _conversa(s, c: C, *, agora, integ, canais, usuarios) -> dict[str, int]:
    """Grava UMA conversa do roteiro pelo mesmo caminho do sync (`gravar`)."""
    from app.models import AtendimentoAvaliacao, AtendimentoRascunho
    from app.services.atendimento import enriquecer, enviar, gravar, validador
    from app.services.atendimento.amazon_email import SEM_CONTA
    from app.services.atendimento.ia import PROMPT_VERSAO

    integracao = integ.get(c.integ) if c.integ else None
    plataforma = INTEGRACOES[c.integ].plataforma if c.integ else "amazon"
    canal = canais.get((c.integ, c.canal)) if c.integ else None
    pedido = PEDIDOS.get(c.pedido) if c.pedido else None
    numero = _numero(pedido, agora) if pedido is not None else c.pedido
    retrato = _retrato(pedido, agora) if pedido is not None else None
    produto = PRODUTOS[c.anuncio] if c.anuncio else None

    # `dados` como os adaptadores + o enriquecer deixam — inclusive o carimbo
    # da última tentativa (`enriquecimento`), para a conversa semeada contar
    # como recém-atualizada e o painel mostrar "atualizado há 12 min".
    dados = dict(c.dados)
    tirado_em = agora.replace(microsecond=0) - timedelta(minutes=12)
    carimbos: dict[str, dict[str, Any]] = {}
    if retrato is not None:
        dados["pedido_mkt"] = retrato
        carimbos["pedido_mkt"] = enriquecer.carimbo(numero, tirado_em)
    if plataforma == "ml" and c.canal == "pergunta" and produto is not None:
        respondida = any(isinstance(m, M) and m.autor == "loja" for m in c.passos)
        dados.update(
            {
                "item_id": produto.mlb,
                "from_id": c.comprador_id,
                "question_id": c.externo_id.removeprefix("q:"),
                "status_ml": "ANSWERED" if respondida else "UNANSWERED",
                # O cartão do anúncio da pergunta (aba "Produto" do painel).
                "produto": _cartao_produto(c.anuncio or "", "ml"),
            }
        )
        carimbos["produto"] = enriquecer.carimbo(produto.mlb, tirado_em)
    if carimbos:
        dados["enriquecimento"] = carimbos
    if plataforma == "ml" and c.canal == "pos_venda" and numero:
        dados = {"pack_id": numero, "order_id": numero, "status_ml": "active", **dados}

    conversa, _ = await gravar.upsert_conversa(
        s,
        canal=canal,
        integration=integracao,
        plataforma=plataforma,
        canal_nome=c.canal,
        externo_id=c.externo_id,
        conta=None if integracao is not None else SEM_CONTA,
        comprador_id=c.comprador_id,
        comprador_nome=c.comprador_nome,
        comprador_avatar=_avatar(c.avatar),
        pedido_marketplace=numero,
        anuncio_id=_item_id(produto, plataforma) if produto is not None else None,
        anuncio_titulo=produto.titulo if produto is not None else None,
        dados=dados or None,
    )
    conta = {"mensagens": 0, "rascunhos": 0, "avaliacoes": 0}
    # P2: a `conta` gravada é o nome da LOJA (o `upsert_conversa` pergunta ao
    # `lojas.nome_da_loja`), não o nome de sistema da integração. A semente
    # passa `conta=None` de propósito — é o caminho do sync que se confere.
    if c.integ and conversa.conta != INTEGRACOES[c.integ].nome:
        conta["conta_com_nome_da_integracao"] = 1
    ultima_do_cliente = None
    rascunhos: list[tuple[int, R, Any]] = []
    for i, passo in enumerate(c.passos):
        quando = agora - timedelta(minutes=passo.ha_min)
        if isinstance(passo, R):
            erros = validador.validar(
                passo.texto or "", plataforma=plataforma, canal=c.canal, origem="davinci_ia"
            )
            if passo.status == "bloqueado" and not validador.so_da_ia(erros):
                print(f"  aviso: {c.chave}: a sugestão 'bloqueada' não bate com o validador")
            elif passo.status != "bloqueado" and erros:
                print(f"  aviso: {c.chave}: a sugestão não passa no validador: {erros}")
            motivo = passo.motivo or ("validador: " + "; ".join(erros) if erros else None)
            r = AtendimentoRascunho(
                conversa_id=conversa.id,
                mensagem_gatilho_id=ultima_do_cliente.id if ultima_do_cliente else None,
                texto=passo.texto,
                categoria=passo.categoria,
                confianca=passo.confianca,
                precisa_humano=passo.precisa_humano or bool(erros),
                motivo=motivo,
                validador_ok=not erros,
                validador_erros=erros,
                fatos={"pedido": numero, "exemplo_local": True},
                modelo="exemplo-local (sem IA)",
                prompt_versao=PROMPT_VERSAO,
                manual_hash="local000",
                tokens_entrada=1800,
                tokens_saida=120,
                # Pendente até o fim do roteiro: é o gravar que aposenta a
                # sugestão quando a resposta por fora chega depois da pergunta.
                status="bloqueado" if passo.status == "bloqueado" else "pendente",
                created_at=quando,
                updated_at=quando,
            )
            s.add(r)
            await s.flush()
            rascunhos.append((i, passo, r))
            conta["rascunhos"] += 1
            continue

        tipo, anexos = _conteudo(passo, plataforma, retrato)
        msg, _ = await gravar.gravar_mensagem(
            s,
            conversa,
            externo_id=_id_mensagem(c, plataforma, i, passo),
            autor=passo.autor,
            texto=passo.texto,
            enviada_em=quando,
            tipo=tipo,
            anexos=anexos,
            payload={"exemplo_local": True},
        )
        if passo.status:
            msg.status = passo.status
            msg.erro = passo.erro
        if passo.autor == "cliente":
            ultima_do_cliente = msg
        conta["mensagens"] += 1
    await s.flush()

    # O que a pessoa fez com cada sugestão (o "IA × equipe" e as Métricas).
    for i, passo, r in rascunhos:
        if passo.status == "substituido" and r.status != "substituido":
            print(f"  aviso: {c.chave}: a resposta por fora não aposentou a sugestão")
        if passo.status not in ("pendente", "bloqueado", "substituido") and r.status in (
            "pendente",
            "substituido",
        ):
            r.status = passo.status
        if passo.acao is None:
            continue
        # A resposta real = a primeira da loja depois da sugestão que chegou
        # ao comprador (a reprovada na moderação não conta).
        resposta = next(
            (
                m
                for m in c.passos[i + 1 :]
                if isinstance(m, M) and m.autor == "loja" and m.status != "falhou"
            ),
            None,
        )
        texto_final = resposta.texto if resposta and passo.acao != "descartou" else None
        s.add(
            AtendimentoAvaliacao(
                rascunho_id=r.id,
                acao=passo.acao,
                texto_final=texto_final,
                similaridade=(
                    enviar.similaridade(texto_final, r.texto) if texto_final else None
                ),
                motivo=passo.motivo_descarte,
                nota=passo.nota,
                correcao=passo.correcao,
                user_id=usuarios[passo.avaliado_por].id if passo.avaliado_por else None,
            )
        )
        conta["avaliacoes"] += 1

    # Estado de pessoa/plataforma por último, e a fila refeita do banco.
    if c.situacao:
        conversa.situacao = c.situacao
        conversa.bloqueio_motivo = c.bloqueio_motivo
    conversa.sem_resposta_necessaria = c.sem_resposta
    conversa.atribuido_a = usuarios[c.atribuido].id if c.atribuido else None
    conversa.ia_pausada = c.ia_pausada
    conversa.nao_lidas = c.nao_lidas
    await gravar.recalcular_conversa(s, conversa)
    for _, _, r in rascunhos:
        conta[f"sugestao_{r.status}"] = conta.get(f"sugestao_{r.status}", 0) + 1
    return conta


async def _indices(s, agora: datetime, integ: dict[str, Any]) -> tuple[int, int]:
    """Os índices do cartão "Cliente" → (pedidos indexados, avaliações da loja).

    Pelos mesmos caminhos da produção: o pedido da conversa pelo
    `indice.registrar_do_retrato` (o que o enriquecer faz com cada retrato),
    as compras anteriores e as avaliações pelo `indice.registrar_*` (o que o
    job de indexação e a importação do histórico fazem). Só Shopee e ML; o
    comprador é o `comprador_id` da conversa — o `buyer_user_id` do pedido
    na Shopee, o `buyer.id` no ML.

    Por fim, a marca de COBERTURA das lojas Shopee (Redis), como se a
    importação do histórico já tivesse rodado: sem ela o cartão não afirma
    "primeira compra" (1 compra no índice pode ser só o que o job de 1 h viu).
    """
    from app.services.atendimento import indice

    pedidos = 0
    for c in CONVERSAS:
        if c.integ is None or not c.comprador_id:
            continue
        plataforma = INTEGRACOES[c.integ].plataforma
        if plataforma not in ("shopee", "ml"):
            continue
        integration_id = integ[c.integ].id
        if c.pedido in PEDIDOS:
            await indice.registrar_do_retrato(
                s,
                integration_id=integration_id,
                plataforma=plataforma,
                comprador_id=c.comprador_id,
                retrato=_retrato(PEDIDOS[c.pedido], agora),
            )
            pedidos += 1
        for p in COMPRAS_ANTERIORES.get(c.chave, ()):
            await indice.registrar_pedido(
                s,
                integration_id=integration_id,
                plataforma=plataforma,
                comprador_id=c.comprador_id,
                pedido=_numero(p, agora),
                criado_em=_criado_em(p, agora),
                total=_valores(p)[0],
                status=p.status,
                itens_resumo=indice.resumo_itens(
                    (PRODUTOS[chave].titulo, qtd) for chave, _, qtd in p.itens
                ),
            )
            pedidos += 1

    por_chave = {c.chave: c for c in CONVERSAS}
    for n, a in enumerate(AVALIACOES_LOJA, start=1):
        c = por_chave[a.conversa]
        if c.integ is None or INTEGRACOES[c.integ].plataforma != "shopee":
            raise SystemExit(f"avaliação da loja fora da Shopee: {a.conversa}")
        await indice.registrar_avaliacao(
            s,
            integration_id=integ[c.integ].id,
            plataforma="shopee",
            # `comment_id` é numérico na Shopee (determinístico: semente repetível).
            comentario_id=str(int(_hash("avaliacao", a.conversa, str(n))[:12], 16)),
            pedido=_numero(a.pedido, agora),
            comprador_nome_loja=c.comprador_nome,
            item_id=_item_id(PRODUTOS[a.pedido.itens[0][0]], "shopee"),
            estrelas=a.estrelas,
            texto=a.texto,
            resposta_loja=a.resposta,
            criado_em=agora - timedelta(days=a.ha_dias),
        )
    await s.flush()

    # A compra mais antiga do roteiro é de ~190 dias: a cobertura começa antes.
    desde = agora - timedelta(days=200)
    for chave, loja in INTEGRACOES.items():
        if loja.plataforma != "shopee":
            continue
        await indice.marcar_cobertura(integ[chave].id, desde)
        if await indice.cobertura(integ[chave].id) is None:
            print(
                f"  aviso: {chave}: sem a marca de cobertura no Redis local — o cartão "
                "Cliente não vai afirmar 'primeira compra'"
            )
    return pedidos, len(AVALIACOES_LOJA)


def _manual_como_arquivo() -> dict[str, Any]:
    """O manual da semente no formato do arquivo do manual base (P7)."""
    return {
        "categorias": [dict(cat) for cat in CATEGORIAS_MANUAL],
        "regras": [
            {
                "tipo": r.tipo, "categoria": r.categoria, "plataforma": r.plataforma,
                "canal": r.canal, "prioridade": r.prioridade, "quando": r.quando, "faca": r.faca,
            }
            for r in REGRAS
        ],
        "respostas_prontas": [
            {"titulo": t, "texto": x, "plataforma": p, "canal": c, "categoria": cat}
            for t, x, p, c, cat in MODELOS
        ],
    }


async def _manual(s, usuarios: dict[str, Any]) -> dict[str, int]:
    """Categorias, regras por tipo e respostas prontas — e a conferência de conflito."""
    from app.models import AtendimentoCategoria, AtendimentoModelo, AtendimentoRegra
    from app.services.atendimento import manual

    ids = {cat["id"] for cat in CATEGORIAS_MANUAL}
    for ordem, cat in enumerate(CATEGORIAS_MANUAL, start=1):
        s.add(AtendimentoCategoria(ativa=True, ordem=ordem * 10, **cat))
    # A categoria de cada sugestão e de cada regra tem de existir na tabela:
    # é dela que a tela tira o nome e a IA, a descrição.
    usadas = (
        {r.categoria for r in REGRAS if r.categoria}
        | {m[4] for m in MODELOS if m[4]}
        | {p.categoria for c in CONVERSAS for p in c.passos if isinstance(p, R)}
    )
    for fora in sorted(usadas - ids):
        print(f"  aviso: a categoria {fora!r} é usada na semente e não está no manual")

    for r in REGRAS:
        s.add(
            AtendimentoRegra(
                quando=r.quando, faca=r.faca, tipo=r.tipo, categoria=r.categoria,
                plataforma=r.plataforma, canal=r.canal, prioridade=r.prioridade,
                ativa=True, criado_por=usuarios[ADMIN].id, atualizado_por=usuarios[ADMIN].id,
            )
        )
    for ordem, (titulo, texto_modelo, plataforma, canal, categoria) in enumerate(MODELOS):
        s.add(
            AtendimentoModelo(
                titulo=titulo, texto=texto_modelo, plataforma=plataforma, canal=canal,
                categoria=categoria, ativo=True, ordem=ordem, criado_por=usuarios[ADMIN].id,
            )
        )
    await s.flush()

    conflitos = await manual.conflitos_existentes(s)
    if conflitos:
        print(f"  aviso: {len(conflitos)} conflito(s) entre regras do manual — a semente não "
              "deveria ter nenhum")
    # O manual da semente também tem de passar no IMPORTADOR (a seco, não
    # grava): é o exemplo vivo do formato do manual base — lacuna, tipo ou
    # resposta pronta que o importador recusaria não pode morar aqui.
    relatorio = await manual.importar_manual(s, _manual_como_arquivo(), seco=True)
    for problema in [*relatorio.erros, *relatorio.conflitos]:
        print(f"  aviso: o importador do manual recusaria a semente: {problema}")
    tipos: dict[str, int] = {}
    for r in REGRAS:
        tipos[r.tipo] = tipos.get(r.tipo, 0) + 1
    return {"categorias": len(await manual.categorias_ativas(s)), **tipos}


async def _esquecer_redis(conn) -> None:
    """Apaga do Redis local o que a semente anterior deixou lá, antes do TRUNCATE.

    A marca de cobertura do índice (400 dias) e o cache do cartão Cliente do
    ML são por integração; cada semente cria integrações novas, e sem isto a
    chave da rodada anterior ficaria órfã no Redis. Só as chaves das
    integrações DESTE schema — as de outro schema (o dev, os testes) ficam.
    """
    from sqlalchemy import text

    from app.redis_client import redis
    from app.services.atendimento import cliente, indice

    ids = [str(r[0]) for r in await conn.execute(text("SELECT id FROM integrations"))]
    try:
        for integration_id in ids:
            chaves = [indice.CHAVE_COBERTURA.format(integration_id)]
            padrao = cliente._CHAVE_ML.format(integration_id, "*")
            async for chave in redis.scan_iter(match=padrao):
                chaves.append(chave)
            await redis.delete(*chaves)
    except Exception as exc:  # noqa: BLE001 — Redis fora do ar não impede a semente
        print(f"  aviso: Redis local indisponível ({type(exc).__name__}): chaves antigas ficam")


async def semente(schema: str) -> None:
    """Apaga o que a semente gravou antes e grava de novo (repetível)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    motor = _motor(schema)
    try:
        async with motor.begin() as conn:
            await _conferir_servidor(conn)
            await _conferir_atualizado(conn, schema, rode="`preparar` antes")
            await _esquecer_redis(conn)
            # CASCADE leva junto o que depende (canais, conversas, mensagens,
            # os índices do cliente, as lojas do cadastro...). Usuários ficam:
            # o admin do mock-login pode já ter sessão aberta.
            await conn.execute(
                text(
                    "TRUNCATE atendimento_avaliacoes, atendimento_rascunhos, "
                    "atendimento_mensagens, atendimento_conversas, atendimento_canais, "
                    "atendimento_regras, atendimento_modelos, atendimento_categorias, "
                    "atendimento_pedidos_comprador, atendimento_avaliacoes_loja, "
                    "logistica, chamados, devolutions, nf_nota, bling_orders, situacao_bling, "
                    "integrations, stores, companies CASCADE"
                )
            )
        agora = datetime.now(UTC)
        sessoes = async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession)
        async with sessoes() as s:
            usuarios = await _usuarios(s)
            integ, canais = await _integracoes(s, usuarios[ADMIN], agora)
            no_bling = await _espelho(s, agora)
            totais: dict[str, int] = {"conversas": 0}
            for c in CONVERSAS:
                conta = await _conversa(
                    s, c, agora=agora, integ=integ, canais=canais, usuarios=usuarios
                )
                totais["conversas"] += 1
                for k, v in conta.items():
                    totais[k] = totais.get(k, 0) + v
            if totais.get("conta_com_nome_da_integracao"):
                # A tela mostra o nome da loja mesmo assim (o router troca na
                # resposta); o que fica errado é a `conta` guardada na conversa.
                print(
                    f"  aviso: {totais['conta_com_nome_da_integracao']} conversas gravadas com "
                    "outro nome em `conta` (ex.: 'mega' em vez de 'Marquezini'): confira o "
                    "gravar.upsert_conversa / lojas.nome_da_loja"
                )
            indexados, avaliacoes_loja = await _indices(s, agora, integ)
            manual_totais = await _manual(s, usuarios)
            await s.commit()
        sugestoes = ", ".join(
            f"{totais[k]} {k.removeprefix('sugestao_')}"
            for k in sorted(totais)
            if k.startswith("sugestao_")
        )
        ligadas = sum(1 for loja in INTEGRACOES.values() if loja.empresa)
        print(
            f"semente em {schema}: {len(USUARIOS)} usuários, {len(INTEGRACOES)} lojas "
            f"({ligadas} ligadas ao cadastro), "
            f"{len(canais)} canais (todos em observar), {totais['conversas']} conversas, "
            f"{totais['mensagens']} mensagens, {totais['rascunhos']} sugestões "
            f"({sugestoes}), {totais['avaliacoes']} avaliações, "
            f"{len(PEDIDOS)} pedidos ({no_bling} no espelho do Bling), "
            f"índice do cliente: {indexados} pedidos e {avaliacoes_loja} avaliações da loja, "
            f"{manual_totais['categorias']} categorias, {len(REGRAS)} regras "
            f"({manual_totais.get('seguranca', 0)} segurança, "
            f"{manual_totais.get('categoria', 0)} categoria, "
            f"{manual_totais.get('estilo', 0)} estilo), {len(MODELOS)} respostas prontas."
        )
    finally:
        await motor.dispose()


# ── api ───────────────────────────────────────────────────────────────────

# Domínios das lojas (e do Telegram): da API local, nada sai para eles.
_HOSTS_BARRADOS = (
    "shopeemobile.com", "shopee.com.br", "mercadolibre.com", "mercadolivre.com.br",
    "tiktokglobalshop.com", "tiktok-shops.com", "tiktokshop.com", "amazon.com",
    "amazon.com.br", "bling.com.br", "magalu.com", "temu.com", "telegram.org",
)


def _barrado(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in _HOSTS_BARRADOS)


def _barrar_lojas() -> None:
    """Toda requisição HTTP do app para uma loja morre aqui, antes de sair do Mac.

    Por que a credencial falsa não basta: a tela agora tem "atualizar pedido"
    (GET de pedido e rastreio na hora), e a Shopee assina a chamada com o
    partner_key do .env — com a loja falsa, ainda sairia uma requisição
    assinada de verdade para a Shopee. Os clientes das lojas usam httpx;
    barrar no `send` pega todos os caminhos (sync, envio, enriquecer, outras
    telas) sem depender de cada um conferir uma flag. A falha é um
    `ConnectError` — o mesmo de rede fora do ar —, que o código já trata como
    falha de API (o cartão fica com o que já tinha).
    """
    import httpx

    if getattr(httpx.AsyncClient.send, "_barreira_local", False):
        return  # já posta (o `main` e o `subir_api` chamam): não embrulha duas vezes
    envio_async = httpx.AsyncClient.send
    envio_sync = httpx.Client.send

    def _conferir(request: httpx.Request) -> None:
        if _barrado(request.url.host):
            raise httpx.ConnectError(
                f"API local: chamada a {request.url.host} barrada", request=request
            )

    async def send_async(self: httpx.AsyncClient, request: httpx.Request, *a, **kw):
        _conferir(request)
        return await envio_async(self, request, *a, **kw)

    def send_sync(self: httpx.Client, request: httpx.Request, *a, **kw):
        _conferir(request)
        return envio_sync(self, request, *a, **kw)

    httpx.AsyncClient.send = send_async  # type: ignore[method-assign]
    send_async._barreira_local = True  # type: ignore[attr-defined]
    httpx.Client.send = send_sync  # type: ignore[method-assign]


_VARS_CAIXA_AMAZON = (
    "ATENDIMENTO_AMAZON_IMAP_HOST",
    "ATENDIMENTO_AMAZON_IMAP_USUARIO",
    "ATENDIMENTO_AMAZON_IMAP_SENHA",
    "ATENDIMENTO_AMAZON_SMTP_HOST",
    "ATENDIMENTO_AMAZON_SMTP_USUARIO",
    "ATENDIMENTO_AMAZON_SMTP_SENHA",
    "ATENDIMENTO_AMAZON_REMETENTE",
)


def _ambiente_da_api(envio_real_amazon: str | None = None) -> None:
    """Os interruptores da API local, no ambiente ANTES da primeira leitura das settings.

    `get_settings()` é lida uma vez e fica em cache: o que for posto no
    ambiente depois não vale. Forçados: SIMULADOR ligado (se o envio for
    ligado, nada sai para comprador), LEITURA/AUTO/ALERTA desligados (nada
    chama loja, nada manda Telegram). O ENVIO nasce DESLIGADO — é o primeiro
    teste em produção: o DaVinci só lê e mostra o que a IA responderia, quem
    responde é o Duoke. Um `ATENDIMENTO_ENVIO_ATIVO=true` exportado liga o
    envio (para o simulador) para quem quiser testar a caixa de resposta.

    A exceção do simulador (`ATENDIMENTO_SIMULADOR_EXCETO`) é zerada: um
    valor esquecido no `.env` ou exportado não pode tirar loja nenhuma do
    simulador sem ninguém pedir.
    """
    os.environ["ATENDIMENTO_SIMULADOR"] = "true"
    os.environ["ATENDIMENTO_SIMULADOR_EXCETO"] = ""
    os.environ["ATENDIMENTO_LEITURA_ATIVA"] = "false"
    os.environ["ATENDIMENTO_AUTO_ATIVO"] = "false"
    os.environ["ATENDIMENTO_ALERTA_TELEGRAM"] = "false"
    os.environ.setdefault("ATENDIMENTO_ENVIO_ATIVO", "false")
    os.environ["APP_NAME"] = NOME_APP
    if envio_real_amazon:
        # `--envio-real-amazon <conversa>`: só a Amazon sai do simulador, e o
        # `subir_api` ainda trava o envio real naquela UMA conversa.
        os.environ["ATENDIMENTO_ENVIO_ATIVO"] = "true"
        os.environ["ATENDIMENTO_SIMULADOR_EXCETO"] = "amazon"


def subir_api(schema: str, porta: int, envio_real_amazon: str | None = None) -> None:
    """Sobe a API com o `search_path` fixado no schema local e as lojas barradas."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    import app.db as db

    async def _conferir() -> None:
        motor_teste = _motor(schema)
        try:
            async with motor_teste.connect() as conn:
                await _conferir_servidor(conn)
                await _conferir_atualizado(conn, schema, rode="`preparar` e `semente`")
        finally:
            await motor_teste.dispose()

    asyncio.run(_conferir())

    # Troca o engine ANTES de importar o app (mesma técnica do conftest):
    # quem faz `from app.db import engine/SessionLocal` pega o daqui.
    db.engine = _motor(schema, com_pool=True)
    db.SessionLocal = async_sessionmaker(db.engine, expire_on_commit=False, class_=AsyncSession)
    _barrar_lojas()

    import uvicorn

    from app.config import get_settings
    from app.main import app as aplicacao

    s = get_settings()
    if not s.atendimento_simulador or s.atendimento_leitura_ativa:
        raise SystemExit("recusado: a API local exige simulador ligado e leitura desligada.")
    if not all(_barrado(h) for h in ("partner.shopeemobile.com", "api.mercadolibre.com")):
        raise SystemExit("recusado: a barreira das lojas não pegou.")
    if s.atendimento_amazon_imap_host:
        raise SystemExit("recusado: a leitura da caixa de e-mail da Amazon não foi zerada.")
    if envio_real_amazon:
        _travar_envio_real_amazon(s, envio_real_amazon)
    elif s.atendimento_amazon_smtp_host:
        raise SystemExit("recusado: a caixa de e-mail da Amazon não foi zerada.")
    print(
        f"API local em http://127.0.0.1:{porta} · schema={schema} · "
        f"simulador={s.atendimento_simulador} envio={s.atendimento_envio_ativo} "
        f"leitura={s.atendimento_leitura_ativa} ia={s.atendimento_ia_ativa} "
        f"lojas=barradas mock_login={s.dev_mock_login and not s.is_prod}"
    )
    uvicorn.run(aplicacao, host="127.0.0.1", port=porta, log_level=s.log_level)


def _travar_envio_real_amazon(s, conversa_id: str) -> None:
    """Liga o envio REAL da Amazon (SMTP do Gmail) para UMA conversa só.

    Pedido do Eduardo (28/09): responder pela tela à mensagem de teste que ele
    mandou para a KFA e ver a resposta chegar no Seller Central. As conversas
    da semente têm endereço de retransmissão no formato real e inventado —
    "Enviar" nelas mandaria e-mail de verdade para um endereço que não existe.
    Por isso qualquer conversa que não seja a pedida é recusada antes do SMTP.
    """
    from uuid import UUID

    from app.services.atendimento import enviar
    from app.services.atendimento.constantes import ResultadoEnvio

    alvo = UUID(conversa_id)
    if not s.atendimento_envio_ativo:
        raise SystemExit("recusado: o envio não ligou.")
    fora = {p for p in (s.atendimento_simulador_exceto or "").split(",") if p.strip()} - {"amazon"}
    if fora:
        raise SystemExit("recusado: só a Amazon pode sair do simulador.")
    faltando = [
        nome
        for nome, valor in (
            ("ATENDIMENTO_AMAZON_SMTP_HOST", s.atendimento_amazon_smtp_host),
            ("ATENDIMENTO_AMAZON_REMETENTE", s.atendimento_amazon_remetente),
            (
                "login do SMTP (ou do IMAP)",
                s.atendimento_amazon_smtp_usuario or s.atendimento_amazon_imap_usuario,
            ),
            (
                "senha do SMTP (ou do IMAP)",
                s.atendimento_amazon_smtp_senha or s.atendimento_amazon_imap_senha,
            ),
        )
        if not (valor or "").strip()
    ]
    if faltando:
        raise SystemExit(f"recusado: falta no .env: {', '.join(faltando)}.")
    if s.atendimento_amazon_smtp_host.strip().lower() != "smtp.gmail.com":
        raise SystemExit("recusado: o SMTP da Amazon não é o do Gmail.")

    original = enviar._chamar_plataforma

    async def _so_a_conversa_do_teste(session, conversa, integration, texto):
        if conversa.plataforma != "amazon":
            return await original(session, conversa, integration, texto)
        if conversa.id != alvo:
            return ResultadoEnvio(ok=False, erro="teste_local: envio real só na conversa do teste")
        return await original(session, conversa, integration, texto)

    enviar._chamar_plataforma = _so_a_conversa_do_teste
    print(
        f"ENVIO REAL: amazon ({s.atendimento_amazon_smtp_host}:{s.atendimento_amazon_smtp_port}) "
        f"— só a conversa {alvo}; o resto da Amazon é recusado, Shopee/ML/TikTok no simulador."
    )


# ── entrada ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.atendimento_local",
        description="Ambiente local da caixa /atendimento (schema próprio, dados de exemplo).",
    )
    sub = parser.add_subparsers(dest="comando", required=True)
    for nome, ajuda in (
        ("preparar", "recria o schema (enums, tabelas, gatilho do Histórico)"),
        ("semente", "grava o cenário do teste em observação (apaga o anterior)"),
        ("limpar", "apaga o schema"),
        ("api", "sobe a API no schema local (envio desligado, lojas barradas)"),
    ):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument("--schema", default=SCHEMA_PADRAO)
        if nome == "api":
            p.add_argument("--porta", type=int, default=PORTA_PADRAO)
            p.add_argument(
                "--envio-real-amazon",
                metavar="CONVERSA_ID",
                default=None,
                help="manda de verdade (SMTP do Gmail do .env) só a resposta desta conversa",
            )
    args = parser.parse_args(argv)

    _conferir_schema(args.schema)
    # ANTES de qualquer import do app: os models e as settings leem o ambiente
    # uma vez só (o `_conferir_hosts` logo abaixo já fixa as settings).
    os.environ["DATABASE_SCHEMA"] = args.schema
    # A caixa de e-mail da Amazon (IMAP/SMTP) do `.env` é a de VERDADE quando
    # alguém a configurou neste Mac: a barreira das lojas só pega HTTP, e o
    # ambiente local nunca lê nem manda e-mail por ela. Zerada para TODO
    # comando, antes da primeira leitura das settings.
    envio_real = getattr(args, "envio_real_amazon", None) if args.comando == "api" else None
    for var in _VARS_CAIXA_AMAZON:
        # Com `--envio-real-amazon`, o SMTP (e o login, que ele reaproveita)
        # fica; a LEITURA continua zerada pelo IMAP_HOST.
        if envio_real and var != "ATENDIMENTO_AMAZON_IMAP_HOST":
            continue
        os.environ[var] = ""
    if args.comando == "api":
        _ambiente_da_api(envio_real)
    # A semente também escreve no Redis (a marca de cobertura do índice do
    # cliente): Redis de fora deste Mac é recusado como o banco.
    _conferir_hosts(com_redis=args.comando in ("api", "semente"))
    # A barreira das lojas vale para TODO comando, não só para a `api`: a
    # semente chama código do app (índice, manual, gravar) e basta um deles
    # passar a buscar algo na loja — como o cartão Cliente do ML, que busca
    # os pedidos do comprador ao vivo — para uma requisição sair do Mac.
    _barrar_lojas()

    if args.comando == "preparar":
        asyncio.run(preparar(args.schema))
    elif args.comando == "semente":
        asyncio.run(semente(args.schema))
    elif args.comando == "limpar":
        asyncio.run(limpar(args.schema))
    else:
        subir_api(args.schema, args.porta, envio_real)


if __name__ == "__main__":
    main(sys.argv[1:])
