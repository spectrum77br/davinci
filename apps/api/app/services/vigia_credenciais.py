"""Vigia de credenciais — a conta que perdeu (ou está perto de perder) o acesso à API.

Vinicius, 22/09/2026: quando uma conta cai, ela cai em silêncio. O token
vence, a chave do app expira (15/09: a Live API Partner Key de 9 lojas Shopee
venceu no mesmo dia), o escopo some — e a partir daí NENHUM robô da casa
enxerga aquela loja: o vigia de importação não confere os pedidos dela, a
Margem não busca repasse, a Logística não atualiza rastreio. Nada quebra com
estrondo; simplesmente para de acontecer. Este robô é o único que olha a
conta em si, de hora em hora.

## O que ele faz numa rodada (:21 de cada hora)
1. Uma **prova de vida** por conta: a chamada mais barata que cada client já
   tem (`test_connection` no ML/Amazon/Magalu/Bling, `get_shop_info` na
   Shopee, `/authorization/202309/shops` no TikTok, `GET /nfe?limite=1` nas
   contas de NF). NÃO força refresh: os clients só renovam quando o token JÁ
   venceu — e quando venceu é porque o cron de refresh falhou, então o
   refresh dentro do teste é exatamente a prova que interessa. O token novo é
   persistido e COMMITADO na hora (o refresh token do ML, da Magalu e do
   Bling é de uso único: perder o novo num rollback derruba a conta).
2. Falhou → ocorrência `conta:<integration_id>` (ou `conta:nf:<id>`). O erro
   é classificado (`_classe_do_erro`): credencial (token vencido, chave do app
   expirada, app inválido, 403 de escopo) vira `pessoa` NA HORA — esperar não
   resolve. Instabilidade (429, 5xx, timeout, HTML de bloqueio) fica `info` e
   só promove na 2ª rodada seguida: senão um 503 do ML mandaria REAUTORIZAR
   uma conta saudável, e reautorizar à toa queima refresh token de uso único.
3. Passou e a plataforma informa validade → `vence:<integration_id>` quando
   faltam ≤ `vencimento_dias` (config, padrão 7). Só duas informam: o TikTok
   (`refresh_token_expires_at`) e a Shopee (`expire_time` da autorização da
   loja). É o único aviso ANTES de a conta cair.
4. O que a rodada NÃO viu, fecha como "sumiu" — é assim que a ocorrência se
   fecha sozinha quando a pessoa reautoriza.

## Bling em cooldown Cloudflare: pula, não julga
O ban 1015 do Cloudflare é por IP e dura 1 h; enquanto ele está armado
(`bling:cf_cooldown_until` no redis) o Bling — principal e contas de NF — é
PULADO: não chama, não abre e não fecha (as chaves entram em `r.vistas`).
Como o robô é horário, contar o cooldown como instabilidade promoveria a
pessoa na 2ª rodada mandando "Reautorizar" uma conta que está perfeita.

## O que ele NÃO cobre (não dá — a API não informa)
A validade da Live API Partner Key da Shopee e a do client secret LWA da
Amazon (180 dias) não são expostas por nenhum endpoint: essas só aparecem
DEPOIS de vencer, pela prova de vida (403 `error_partner_key_expired` /
`invalid_client`). O `vence:` da Shopee cobre a autorização da loja, que é
outra coisa.

## Ele carimba o teste da tela Integrações — só quando a prova é conclusiva
Passou ou erro de credencial → grava `last_test_at/ok/error` (os MESMOS campos
do botão "Testar"), pra a página ficar verde/vermelha sem ninguém clicar. Na
INSTABILIDADE não toca em nada: pintar a conta de vermelho por um 503 tiraria
uma conta saudável do contador "conectadas" do dashboard e convidaria alguém a
Reautorizar — que queima refresh token de uso único (ver `_gravar_teste`).

BEST-EFFORT por conta: falha numa conta não derruba a rodada — nem a chamada
de saúde (cada `_saude_*` engole a própria exceção) nem o que vem DEPOIS dela
(gravar a ocorrência, carimbar o teste, commitar), que é o `try/except` por
conta do laço (`_conta_nao_olhada`). O sweep é serializado por advisory lock
transacional numa sessão SÓ do lock — a sessão de trabalho commita por conta
e um commit soltaria o lock se ele estivesse na mesma sessão.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Integration, IntegrationPlatform, OuvidoriaRobo
from app.models.bling_nota import BlingNota
from app.services import ouvidoria, vigia_importacao
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

ROBO = "vigia_credenciais"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x63726564  # ascii "cred"

_TZ_BR = ZoneInfo("America/Sao_Paulo")

# Padrão quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com este mesmo valor — services/ouvidoria.ROBOS).
_VENCIMENTO_DIAS = 7
# Quantas rodadas seguidas uma conta precisa falhar POR INSTABILIDADE pra
# virar "Conta sem acesso à API" e ir pro Threema — 2 rodadas ≈ 2 h.
_FALHAS_ATE_AVISAR = 2

# As 6 plataformas do robô. Temu e Shein ficam de fora: não têm integração
# via API na casa (a operação delas é por tela).
_PLATAFORMAS = (
    IntegrationPlatform.ML,
    IntegrationPlatform.SHOPEE,
    IntegrationPlatform.TIKTOK,
    IntegrationPlatform.AMAZON,
    IntegrationPlatform.MAGALU,
    IntegrationPlatform.BLING,
)

# Contadores de uma rodada (ouvidoria_rodadas.contadores), em linguagem de
# operação: contas = integrações testadas; notas = contas Bling de NF;
# ok = passaram na prova de vida; sem_acesso / instaveis = as duas famílias
# de falha; vencendo = autorização perto de vencer; puladas = Bling em
# cooldown Cloudflare; erro_interno = conta que a rodada não conseguiu olhar
# por erro NOSSO (não da API dela); novas / persistem = ocorrências;
# sumiram = fechadas.
_CONTADORES = (
    "contas", "notas", "ok", "sem_acesso", "instaveis", "vencendo", "puladas",
    "erro_interno", "novas", "persistem", "sumiram",
)

# A ocorrência de conta sem acesso mora no vigia de importação por histórico
# (era ele quem abria até 22/09) — o texto da ação é o mesmo.
ACAO_REAUTORIZAR = vigia_importacao.ACAO_REAUTORIZAR
# Conta de NF não tem tela: o convite novo é colado direto na coluna.
ACAO_REAUTORIZAR_NF = (
    "Gerar convite novo no app Bling da conta e colar em "
    "bling_notas.authorization_code — não há tela"
)
LINK_INTEGRACOES = "/integrations"
LINK_NOTAS = "/notas-fiscais"


@dataclass
class Prova:
    """O resultado da prova de vida de UMA conta. `vence_em`/`fonte` só vêm
    das duas plataformas que informam validade (TikTok e Shopee)."""

    ok: bool
    erro: str | None = None
    vence_em: datetime | None = None
    fonte: str | None = None


# ─── classificação do erro ─────────────────────────────────────────────────

# A ordem das famílias importa e está invertida de propósito: o que parece
# credencial mas é BLOQUEIO DE REDE tem que ser testado ANTES das marcas de
# acesso. O Bling responde 403 + HTML do Cloudflare sob carga
# (BlingCloudflareError "status=403 cf_html=True") e a Magalu leva 403 em
# HTML da Azion quando o MAGALU_PROXY_URL está vazio — um classificador que
# só olhasse "403" mandaria a pessoa reautorizar uma conta saudável.
_MARCAS_INSTAVEL = (
    "cf_html=true", "bling_cf_cooldown_active", "status=429", " 429", "status=5",
    "timeout", "timed out", "connect", "system busy", "<html",
)
# Shopee: a Live API Partner Key do app venceu (foi o que derrubou 9 lojas em
# 15/09). Não adianta reautorizar a loja — o app é que precisa de chave nova.
_MARCAS_CHAVE_EXPIRADA = ("error_partner_key_expired", "partner_key", "partner key")
_MARCAS_APP_INVALIDO = (
    "invalid_client", "unauthorized_client", "missing client_id", "client_secret",
)
_MARCAS_CREDENCIAL_INCOMPLETA = (
    "missing_credentials", "missing refresh_token", "sem refresh_token", "sem user_id",
    "missing_creds",
)
# "expired credentials"/105002/36009005 são do TikTok, que não usa 401 no
# corpo; "oauth/token" aparece na URL quando o próprio refresh foi recusado.
_MARCAS_TOKEN_VENCIDO = (
    "invalid_grant", "invalid_token", "refresh", "error_token_expired",
    "error_token_invalid", "error_auth", "expired credentials", "105002", "36009005",
    "401", "unauthorized", "oauth/token",
)
_MARCAS_ESCOPO = (
    "403", "forbidden", "error_permission", "access_denied", "105005", "no permission",
)

_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chave_expirada", _MARCAS_CHAVE_EXPIRADA),
    ("app_invalido", _MARCAS_APP_INVALIDO),
    ("credencial_incompleta", _MARCAS_CREDENCIAL_INCOMPLETA),
    ("token_vencido", _MARCAS_TOKEN_VENCIDO),
    ("escopo", _MARCAS_ESCOPO),
)

# Como a classe aparece pra quem lê a ocorrência.
_ROTULO_CLASSE = {
    "token_vencido": "Token vencido",
    "chave_expirada": "Chave do app expirada",
    "app_invalido": "App inválido",
    "credencial_incompleta": "Credencial incompleta",
    "escopo": "403 de permissão ou escopo",
}

_FONTE_TEXTO = {
    "tiktok_refresh_token": "refresh token do TikTok",
    "shopee_expire_time": "autorização da loja na Shopee",
}


def _classe_do_erro(erro: str | None) -> str | None:
    """Que tipo de problema de CREDENCIAL é este erro — ou None quando é
    instabilidade (rede, cota, bloqueio de borda), que não é culpa de
    ninguém e não deve mandar a pessoa reautorizar.

    Classificador próprio em vez do `vigia_importacao._erro_de_acesso`: as
    marcas dele ("token", "refresh") são largas demais pra este robô (casariam
    com "bling_cf_cooldown_active" e com "oauth/token" na URL de um 429) e
    faltam as do TikTok (105002 / "Expired credentials") e o HTML do
    Cloudflare/Azion."""
    texto = (erro or "").lower()
    if not texto:
        return None
    if any(m in texto for m in _MARCAS_INSTAVEL):
        return None
    for classe, marcas in _CLASSES:
        if any(m in texto for m in marcas):
            return classe
    return None


def _texto_do_erro(e: Exception) -> str:
    """Erro em texto, preservando o STATUS e o corpo quando a exceção carrega
    uma resposta HTTP — é no corpo que vêm `error_partner_key_expired`,
    `invalid_grant` e o HTML do Cloudflare, e é por eles que a classificação
    decide. `str(e)` sozinho de um HTTPStatusError esconde o corpo."""
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    if status is not None:
        corpo = str(getattr(resp, "text", "") or "")[:200]
        return f"status={status} body={corpo}"
    return str(e)


# ─── prova de vida por plataforma ──────────────────────────────────────────


async def _saude_padrao(client) -> Prova:
    """ML, Amazon, Magalu e Bling: o `test_connection()` do próprio client —
    1 chamada barata (GET /users/me, marketplaceParticipations, 1 SKU do
    portfólio, 1 produto) que já devolve TestResult com o detalhe do erro.
    Nenhum deles refresca token à toa: só quando o expires_at já venceu."""
    try:
        r = await client.test_connection()
    except Exception as e:  # noqa: BLE001 — best-effort: a conta vira ocorrência
        return Prova(ok=False, erro=_texto_do_erro(e))
    return Prova(ok=True) if r.ok else Prova(ok=False, erro=r.detail or "sem detalhe")


async def _saude_shopee(client) -> Prova:
    """Shopee: chamada própria ao get_shop_info em vez do `test_connection`,
    por dois motivos — o corpo desse endpoint traz o `expire_time` (a validade
    da autorização da loja, que o TestResult descarta) e o erro do refresh
    chega aqui com o CORPO (`error_partner_key_expired`), que é o que separa
    "reautorizar a loja" de "renovar a chave do app"."""
    try:
        r = await client._request("GET", "/api/v2/shop/get_shop_info")  # noqa: SLF001
    except httpx.HTTPStatusError as e:
        # O refresh da Shopee faz raise_for_status: 403 + error_partner_key_expired.
        return Prova(ok=False, erro=_texto_do_erro(e))
    except Exception as e:  # noqa: BLE001
        return Prova(ok=False, erro=str(e))
    if r.status_code != 200:
        return Prova(ok=False, erro=f"status={r.status_code} body={r.text[:200]}")
    try:
        body = r.json() or {}
    except ValueError:
        return Prova(ok=False, erro=f"resposta não-JSON: {r.text[:200]}")
    if body.get("error"):
        return Prova(ok=False, erro=f"{body.get('error')}: {body.get('message')}")
    return Prova(
        ok=True,
        vence_em=vigia_importacao._epoch(body.get("expire_time")),  # noqa: SLF001
        fonte="shopee_expire_time",
    )


async def _saude_tiktok(client) -> Prova:
    """TikTok: `_ensure_fresh_token` (que só renova quando faltam ≤ 5 min e
    persiste pelo callback) + o `fetch_shop_info` estático, que é público,
    não precisa de shop_cipher e faz raise_for_status — MANTÉM o status HTTP
    na exceção. O `test_connection` do client não serve aqui: em HTTP ≠ 200
    ele zera o corpo e devolve "TikTok error: Unknown", perdendo justamente o
    401/403 e o código 105002 de que a classificação precisa."""
    faltando = [k for k in ("app_key", "app_secret", "access_token") if not client.creds.get(k)]
    if faltando:
        return Prova(ok=False, erro=f"missing_credentials: {','.join(faltando)}")
    # Engole a própria falha (só loga) — um refresh que não foi aparece
    # logo abaixo como token cru vencido (105002 / 401).
    await client._ensure_fresh_token()  # noqa: SLF001
    try:
        await TikTokClient.fetch_shop_info(
            client.access_token, client.app_key, client.app_secret
        )
    except Exception as e:  # noqa: BLE001
        return Prova(ok=False, erro=_texto_do_erro(e))
    return Prova(
        ok=True,
        # Lido DEPOIS do refresh: o TikTok devolve um refresh token novo a
        # cada renovação, com validade nova. 0/ausente = não informou.
        vence_em=vigia_importacao._epoch(  # noqa: SLF001
            client.creds.get("refresh_token_expires_at")
        ),
        fonte="tiktok_refresh_token",
    )


async def _saude_bling_nota(session: AsyncSession, conta: BlingNota) -> Prova:
    """Conta Bling de emissão de NF (`bling_notas`, sem client próprio): o
    mesmo par que a tela Notas Fiscais usa — `_ensure_token` (refresh
    on-demand só se vencido, commitado por conta) + 1 página de `/nfe`.
    Importar helper de router num service é acoplamento que já existe
    (services/pos_vendas.py faz igual), não é novo."""
    if not conta.access_token and not conta.refresh_token:
        return Prova(ok=False, erro="conta sem refresh_token (reautorizar no Bling)")
    # Import tardio: `routers/notas_fiscais` importa services, e o import no
    # topo fecharia o ciclo.
    from app.routers import notas_fiscais as nf

    try:
        token = await nf._ensure_token(session, conta)  # noqa: SLF001
        await nf._bling_get(token, "/nfe", {"pagina": 1, "limite": 1})  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        return Prova(ok=False, erro=_texto_do_erro(e))
    return Prova(ok=True)


async def _prova_de_vida(
    session: AsyncSession, platform: IntegrationPlatform, integration: Integration
) -> Prova:
    """Monta o client da conta (com persistência+commit do refresh, igual ao
    vigia de importação) e faz a chamada de saúde da plataforma. Credencial
    que nem abre (cifra corrompida, JSON quebrado) já é falta de acesso."""
    try:
        client, _creds = vigia_importacao._cliente(session, integration)  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        return Prova(ok=False, erro=f"missing_creds: {e}")
    if platform is IntegrationPlatform.SHOPEE:
        return await _saude_shopee(client)
    if platform is IntegrationPlatform.TIKTOK:
        return await _saude_tiktok(client)
    return await _saude_padrao(client)


# ─── contas ────────────────────────────────────────────────────────────────


async def _bling_cooldown_ttl() -> int:
    """Segundos que faltam do cooldown Cloudflare do Bling (0 = sem cooldown).

    BEST-EFFORT de propósito: o redis pode estar fora (nos testes locais nem
    existe) e um pré-check não pode derrubar a rodada inteira — sem resposta
    o robô assume que dá pra testar. O ban 1015 é por IP, então a chave vale
    igualmente pro Bling principal e pras contas de NF."""
    try:
        from app.redis_client import redis as _redis

        bruto = await _redis.get("bling:cf_cooldown_until")
        if bruto is None:
            return 0
        return max(0, int(bruto) - int(time.time()))
    except Exception as e:  # noqa: BLE001
        logger.debug("vigia_credenciais_redis_indisponivel", err=str(e)[:120])
        return 0


async def _contas_nf(session: AsyncSession) -> list[tuple[BlingNota, str]]:
    """Contas Bling de NF ativas + rótulo "Bling NF <nome>". Linha que só tem
    `authorization_code` (convite gerado e ainda não trocado) fica de fora: o
    cron das :45 troca o código por token sozinho, e acusar antes disso seria
    alarme falso na conta que alguém acabou de cadastrar."""
    rows = (
        (
            await session.execute(
                select(BlingNota)
                .where(BlingNota.status == "active")
                .order_by(BlingNota.nome)
            )
        )
        .scalars()
        .all()
    )
    return [
        (c, f"Bling NF {c.nome}")
        for c in rows
        if c.access_token or c.refresh_token or not c.authorization_code
    ]


def _gravar_teste(integration: Integration, prova: Prova, agora: datetime) -> None:
    """Os MESMOS três campos que o botão "Testar" de Sistema › Integrações
    grava (routers/integrations.py): assim a página fica verde/vermelha na
    hora, sem ninguém precisar clicar — quem abriu a tela porque recebeu o
    aviso já vê a conta marcada. Commit fica com o chamador (é por conta).

    SÓ carimba quando a prova é CONCLUSIVA (passou, ou erro de credencial).
    Instabilidade (429, 5xx, timeout, HTML de bloqueio) não toca em nada: a
    ocorrência dela é `info` e diz "tentando de novo", e pintar a conta de
    vermelho pelo mesmo soluço faria três estragos fora da Ouvidoria — a tela
    de Integrações mostrando "falhou" numa conta saudável, o contador
    "conectadas" do dashboard (que filtra `last_test_ok IS TRUE`) perdendo a
    conta por uma hora, e o convite a Reautorizar, que queima refresh token de
    uso único. De quebra, um teste manual recém-feito não é apagado."""
    if not prova.ok and _classe_do_erro(prova.erro) is None:
        return
    integration.last_test_at = agora
    integration.last_test_ok = prova.ok
    integration.last_error = None if prova.ok else (prova.erro or "")[:1000]


# ─── ocorrências ───────────────────────────────────────────────────────────


async def _registrar_conta_falhou(
    r: ouvidoria.Rodada,
    *,
    chave: str,
    plataforma: str,
    conta: str,
    erro: str,
    acao: str,
    link: str,
    dados_id: dict,
    agora: datetime,
) -> str:
    """A prova de vida falhou. Erro de CREDENCIAL vira "Conta sem acesso à
    API" (pessoa + reautorizar) na hora — esperar não resolve. Instabilidade
    (429, 5xx, timeout, HTML de bloqueio) é soluço de rede/cota: na 1ª rodada
    só registra como `info` (não avisa ninguém; fecha sozinha quando a conta
    volta) e só promove pra pessoa quando repete `_FALHAS_ATE_AVISAR` rodadas
    seguidas. Devolve "acesso" | "instavel" (pro contador do resumo).

    O rótulo da conta é CORTADO em 120 aqui, no único ponto por onde ele passa:
    `ouvidoria_ocorrencias.conta` é String(120) e o nome vem de
    `StoreInfo.account_name` / `Integration.name`, que são Text sem limite — um
    nome comprido derrubaria o flush (DataError) e, com ele, a rodada."""
    conta = conta[:120]
    aberta = await ouvidoria._aberta(r.session, r.robo_chave, chave)  # noqa: SLF001
    seguidas = int(((aberta.dados if aberta else None) or {}).get("falhas_seguidas") or 0) + 1
    classe = _classe_do_erro(erro)
    acesso = classe is not None
    pessoa = acesso or seguidas >= _FALHAS_ATE_AVISAR
    erro_curto = erro[:300]
    if acesso:
        titulo = "Conta sem acesso à API"
        detalhe = (
            f"{_ROTULO_CLASSE.get(classe, 'Sem acesso')} em {conta}: {erro_curto}. "
            "Enquanto isso nenhum robô enxerga essa conta."
        )
    elif pessoa:
        titulo = "Conta sem acesso à API"
        detalhe = (
            f"A API de {conta} falhou em {seguidas} rodadas seguidas: {erro_curto}. "
            "Enquanto isso nenhum robô enxerga essa conta."
        )
    else:
        titulo = "Conta não respondeu (cota ou rede) — tentando de novo"
        detalhe = (
            f"A API de {conta} não respondeu nesta rodada: {erro_curto}. Pode ser "
            "instabilidade ou cota; se repetir na próxima rodada vira 'sem acesso' "
            "e avisa."
        )
    row = await r.registrar(
        chave=chave,
        plataforma=plataforma,
        conta=conta,
        titulo=titulo,
        detalhe=detalhe,
        acao=acao if pessoa else None,
        link=link if pessoa else None,
        severidade="pessoa" if pessoa else "info",
        precisa_pessoa=pessoa,
        dados={
            **dados_id,
            "erro": erro_curto,
            "erro_tipo": "acesso" if acesso else "instavel",
            "erro_classe": classe,
            "falhas_seguidas": seguidas,
            "plataforma": plataforma,
            "testado_em": agora.isoformat(),
            "cooldown": False,
        },
        agora=agora,
    )
    _contar(r, row, agora)
    return "acesso" if acesso else "instavel"


async def _registrar_vence(
    r: ouvidoria.Rodada,
    *,
    chave: str,
    plataforma: str,
    conta: str,
    vence_em: datetime,
    fonte: str,
    dias_limite: int,
    dados_id: dict,
    agora: datetime,
) -> bool:
    """Autorização perto de vencer. Só abre quando falta ≤ `dias_limite` —
    fora disso a chave nem entra em `vistas`, e uma ocorrência antiga fecha
    como "sumiu" na hora em que a pessoa reautoriza (a validade volta a ser
    longa). Devolve True quando abriu/re-viu. O rótulo da conta é cortado em
    120 pelo mesmo motivo do `_registrar_conta_falhou`."""
    if vence_em - agora > timedelta(days=dias_limite):
        return False
    conta = conta[:120]
    dias = max((vence_em - agora).days, 0)
    if vence_em <= agora:
        # Vencido no papel e a conta ainda responde: acontece quando a
        # plataforma estende a validade sem avisar. Não é urgência, mas a
        # pessoa precisa saber que está no vermelho.
        titulo = "Acesso venceu — ainda responde"
    elif dias == 0:
        titulo = "Acesso vence hoje"
    else:
        titulo = f"Acesso vence em {dias} dia{'s' if dias != 1 else ''}"
    data = vence_em.astimezone(_TZ_BR)
    await r.registrar(
        chave=chave,
        plataforma=plataforma,
        conta=conta,
        titulo=titulo,
        detalhe=(
            f"A autorização de {conta} vence em {data.strftime('%d/%m/%Y')} "
            f"(fonte: {_FONTE_TEXTO.get(fonte, fonte)}). Depois disso o token não "
            "renova mais e a conta cai."
        ),
        acao=f"Reautorizar em Sistema › Integrações antes de {data.strftime('%d/%m')}",
        link=LINK_INTEGRACOES,
        severidade="baixa",
        precisa_pessoa=True,
        dados={**dados_id, "vence_em": vence_em.isoformat(), "dias": dias, "fonte": fonte},
        agora=agora,
    )
    return True


def _contar(r: ouvidoria.Rodada, row, agora: datetime) -> None:
    """Ocorrência que nasceu nesta rodada é "nova"; a que já estava aberta
    "persiste" (mesma leitura do vigia de importação)."""
    if row.fechada_em is None and row.aberta_em == agora:
        r.contadores["novas"] += 1
    else:
        r.contadores["persistem"] += 1


# ─── uma conta ─────────────────────────────────────────────────────────────


async def _olhar_conta(
    r: ouvidoria.Rodada,
    *,
    platform: IntegrationPlatform,
    integration_id: UUID,
    conta: str,
    chave: str,
    chave_vence: str,
    dias_limite: int,
    agora: datetime,
) -> None:
    """A prova de vida de UMA conta de marketplace, do teste ao commit. Mora
    fora do laço pra o `try/except` por conta caber em duas linhas legíveis.

    Recebe o ID e recarrega a integração (`session.get` sai do identity map,
    sem consulta nova, quando ela não está expirada): depois de um rollback
    por conta os objetos da sessão ficam expirados, e ler qualquer campo deles
    no laço tentaria IO fora do contexto async (MissingGreenlet)."""
    integration = await r.session.get(Integration, integration_id)
    if integration is None:
        # Integração apagada/arquivada no meio da rodada: não olhou.
        r.vistas.update({chave, chave_vence})
        return
    r.contadores["contas"] += 1
    dados_id = {"integration_id": str(integration_id)}
    prova = await _prova_de_vida(r.session, platform, integration)
    _gravar_teste(integration, prova, agora)
    if prova.ok:
        r.contadores["ok"] += 1
    else:
        tipo = await _registrar_conta_falhou(
            r,
            chave=chave,
            plataforma=platform.value,
            conta=conta,
            erro=prova.erro or "sem detalhe",
            acao=ACAO_REAUTORIZAR,
            link=LINK_INTEGRACOES,
            dados_id=dados_id,
            agora=agora,
        )
        r.contadores["sem_acesso" if tipo == "acesso" else "instaveis"] += 1
    if not prova.ok:
        # Conta caída: a ocorrência viva é a `conta:`. O `vence:` fica como
        # visto pra não fechar e reabrir a cada rodada — quando a pessoa
        # reautorizar, os dois fecham juntos.
        r.vistas.add(chave_vence)
    elif prova.vence_em is not None and await _registrar_vence(
        r,
        chave=chave_vence,
        plataforma=platform.value,
        conta=conta,
        vence_em=prova.vence_em,
        fonte=prova.fonte or "",
        dias_limite=dias_limite,
        dados_id=dados_id,
        agora=agora,
    ):
        r.contadores["vencendo"] += 1
    # Commit por conta: o `_persist` do client já commitou o token novo; isto
    # grava a ocorrência e o last_test_* junto.
    await r.session.commit()


async def _conta_nao_olhada(
    r: ouvidoria.Rodada,
    *,
    chaves: tuple[str, ...],
    quem: str,
    conta: str,
    erro: Exception,
) -> None:
    """Erro NOSSO no meio de uma conta (gravar a ocorrência, carimbar o teste,
    commitar): a conta não foi olhada nesta rodada, e a rodada segue.

    É a promessa "best-effort por conta" do topo do módulo, no molde do
    `vigia_importacao_run`. Sem isto a rodada morria inteira e as contas
    SEGUINTES — a ordem é estável, `_contas` ordena por `created_at` — ficavam
    sem prova de vida hora após hora, justamente no robô que existe pra ver a
    conta que caiu. `rollback` porque o erro pode ter envenenado a transação
    (DataError, corrida no índice único parcial das ocorrências, sessão suja
    de um refresh de token) e aí o commit da próxima conta levaria a falha
    junto; as chaves entram em `vistas` porque "não olhei" não é "sumiu"."""
    await r.session.rollback()
    logger.warning(
        "vigia_credenciais_conta_falhou",
        integration=quem,
        conta=conta,
        err=str(erro)[:300],
    )
    r.contadores["erro_interno"] += 1
    r.vistas.update(chaves)
    # Deploy novo: se foi a própria `Rodada` que inseriu a linha do robô (o
    # worker ainda não tinha sincronizado o catálogo), o rollback acima a
    # levou — e sem ela nem a rodada consegue ser gravada no fim, por FK.
    if await r.session.get(OuvidoriaRobo, r.robo_chave) is None:
        await ouvidoria.sincronizar_catalogo(r.session)
        await r.session.commit()


# ─── rodada ────────────────────────────────────────────────────────────────


async def vigia_credenciais_run(session: AsyncSession) -> dict:
    """Uma varredura completa dentro de uma `Rodada` da Ouvidoria. A sessão
    commita por conta (o refresh de token não pode se perder num rollback) e
    a Rodada commita ao sair; quem chama só precisa garantir que não há outro
    sweep junto (advisory lock no `vigia_credenciais_sweep`)."""
    async with ouvidoria.Rodada(session, ROBO) as r:
        # Todos os contadores nascem em 0: a rodada gravada tem sempre as
        # mesmas chaves (a tela lê direto) e o dict devolvido também.
        for k in _CONTADORES:
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        dias_limite = int(cfg.get("vencimento_dias") or _VENCIMENTO_DIAS)
        agora = datetime.now(UTC)
        cooldown_s = await _bling_cooldown_ttl()

        for platform in _PLATAFORMAS:
            # (id, rótulo) em vez do objeto: o best-effort por conta pode dar
            # rollback, e depois dele os objetos da sessão ficam expirados —
            # ler `integration.id` aqui tentaria IO fora do contexto async.
            contas = [
                (integration.id, conta)
                for integration, conta in await vigia_importacao._contas(  # noqa: SLF001
                    session, platform
                )
            ]
            for integration_id, conta in contas:
                chave = f"conta:{integration_id}"
                chave_vence = f"vence:{integration_id}"
                if platform is IntegrationPlatform.BLING and cooldown_s > 0:
                    # Cooldown Cloudflare: não chama, não abre e não fecha.
                    # As duas chaves contam como VISTAS — o robô não olhou
                    # esta conta, e "não olhei" não é "sumiu".
                    r.vistas.update({chave, chave_vence})
                    r.contadores["puladas"] += 1
                    continue
                try:
                    await _olhar_conta(
                        r,
                        platform=platform,
                        integration_id=integration_id,
                        conta=conta,
                        chave=chave,
                        chave_vence=chave_vence,
                        dias_limite=dias_limite,
                        agora=agora,
                    )
                except Exception as e:  # noqa: BLE001 — best-effort por conta
                    await _conta_nao_olhada(
                        r,
                        chaves=(chave, chave_vence),
                        quem=str(integration_id),
                        conta=conta,
                        erro=e,
                    )

        # Contas Bling de NF: mesma ideia, sem `vence:` (o Bling não informa
        # validade de refresh token) e com a ação que explica que não há tela.
        # (id, rótulo) pelo mesmo motivo do laço acima.
        notas = [(c.id, rotulo) for c, rotulo in await _contas_nf(session)]
        for nota_id, rotulo in notas:
            chave = f"conta:nf:{nota_id}"
            if cooldown_s > 0:
                r.vistas.add(chave)
                r.contadores["puladas"] += 1
                continue
            try:
                conta_nf = await session.get(BlingNota, nota_id)
                if conta_nf is None:
                    r.vistas.add(chave)  # apagada no meio da rodada
                    continue
                r.contadores["notas"] += 1
                prova = await _saude_bling_nota(session, conta_nf)
                if prova.ok:
                    r.contadores["ok"] += 1
                else:
                    tipo = await _registrar_conta_falhou(
                        r,
                        chave=chave,
                        plataforma=IntegrationPlatform.BLING.value,
                        conta=rotulo,
                        erro=prova.erro or "sem detalhe",
                        acao=ACAO_REAUTORIZAR_NF,
                        link=LINK_NOTAS,
                        dados_id={"bling_nota_id": str(nota_id)},
                        agora=agora,
                    )
                    r.contadores["sem_acesso" if tipo == "acesso" else "instaveis"] += 1
                await session.commit()
            except Exception as e:  # noqa: BLE001 — best-effort por conta
                await _conta_nao_olhada(
                    r, chaves=(chave,), quem=str(nota_id), conta=rotulo, erro=e
                )

        # O que a rodada não viu, sumiu — a conta voltou a responder (ou a
        # validade voltou a ser longa) e a ocorrência fecha sozinha.
        r.contadores["sumiram"] = await r.fechar_nao_vistas()
        r.resumo = _resumo(r, cooldown_s)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


def _resumo(r: ouvidoria.Rodada, cooldown_s: int) -> str:
    """"12 contas ok · 1 sem acesso · 0 instáveis · 1 vencendo" — a frase que
    aparece na coluna Última rodada do painel."""
    ok = r.contadores["ok"]
    sem = r.contadores["sem_acesso"]
    inst = r.contadores["instaveis"]
    partes = [
        f"{ok} conta{'s' if ok != 1 else ''} ok",
        f"{sem} sem acesso",
        f"{inst} instáve{'is' if inst != 1 else 'l'}",
    ]
    if vencendo := r.contadores["vencendo"]:
        partes.append(f"{vencendo} vencendo")
    if puladas := r.contadores["puladas"]:
        partes.append(
            f"{puladas} Bling pulada{'s' if puladas != 1 else ''} "
            f"(cooldown Cloudflare {max(1, cooldown_s // 60)} min)"
        )
    if erros := r.contadores["erro_interno"]:
        # Erro NOSSO, não da API da conta: aparece no painel pra não passar em
        # branco que aquela conta ficou sem prova de vida nesta rodada.
        partes.append(f"{erros} não olhada{'s' if erros != 1 else ''} (erro interno)")
    return " · ".join(partes)


async def vigia_credenciais_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory
    lock transacional numa sessão SÓ do lock — a sessão de trabalho commita
    por conta e um commit soltaria o lock. O modo do robô NÃO é olhado aqui:
    o tick do worker (`vigia_credenciais_tick`) é quem sai quando está
    `desligado`; o botão "Rodar agora" roda mesmo desligado (a pessoa pediu)."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        async with session_scope() as session:
            return await vigia_credenciais_run(session)
