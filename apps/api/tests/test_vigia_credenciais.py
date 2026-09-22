"""Vigia de credenciais (robô da Ouvidoria) — prova de vida por conta,
classificação do erro, vencimento e cooldown do Bling.

O que este arquivo trava (22/09/2026):

- a classificação do erro: 401/403/invalid_grant/error_partner_key_expired/
  "Expired credentials" são CREDENCIAL (abre pessoa na hora); 429, 5xx,
  timeout, "cf_html=True" e 403 em HTML (Cloudflare do Bling, Azion da
  Magalu) são INSTABILIDADE — e instabilidade nunca manda reautorizar;
- conta que passa na prova de vida não abre nada e fecha como "sumiu" a
  ocorrência que estava aberta;
- conta instável entra como `info` na 1ª rodada e só vira `pessoa` na 2ª
  seguida (`falhas_seguidas` em `dados`);
- Bling em cooldown Cloudflare é PULADO: não chama, não abre e não fecha
  (nem o Bling principal nem as contas de NF);
- `vence:` abre em `baixa` quando a plataforma informa validade e faltam
  ≤ `vencimento_dias`, e fecha sozinho quando a pessoa reautoriza;
- conta Bling de NF sem token abre ocorrência com a ação que explica que
  não há tela; linha com convite ainda não trocado é pulada;
- a integração recebe `last_test_at/ok/error` quando a prova é CONCLUSIVA (os
  mesmos 3 campos do botão Testar) e NÃO recebe nada na instabilidade — senão
  um 503 pintaria de vermelho, na tela Integrações, uma conta saudável;
- erro NOSSO no meio de uma conta (gravar a ocorrência, commitar) é
  best-effort: a conta seguinte é olhada, a rodada fica `ok` e a conta
  problemática conta em `erro_interno` sem fechar nada;
- rótulo de conta comprido é cortado em 120 (a coluna é String(120) e
  `integrations.name` é Text);
- o tick do worker sai sem rodar com o robô `desligado`.

As chamadas HTTP são substituídas por monkeypatch — as provas de vida por
plataforma têm teste próprio aqui (`_saude_shopee`/`_saude_tiktok`) e os
clients já são cobertos pelos testes de marketplace.
"""
# ruff: noqa: S608, S105, S106 — "access_token"/"refresh_token" aqui são
# valores de teste ("at", "rt"), não segredos.
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    OuvidoriaRodada,
    User,
)
from app.models.bling_nota import BlingNota
from app.security.cipher import encrypt_json
from app.services import ouvidoria as svc
from app.services import vigia_credenciais as vc

pytestmark = pytest.mark.asyncio

ROBO = vc.ROBO

# Guardado antes de qualquer monkeypatch: o teste da conta de NF sem token
# roda a prova de vida DE VERDADE (a guarda que evita bater no Bling).
_SAUDE_NF_REAL = vc._saude_bling_nota


# ─── fixtures ──────────────────────────────────────────────────────────────


async def _limpar(db: AsyncSession) -> None:
    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos", "bling_notas"):
        await db.execute(text(f"DELETE FROM {tbl}"))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa_ouvidoria(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


class _Threema:
    enviados: list[tuple[str, list[str]]] = []

    async def send_to_all(self, texto: str, recipients=None) -> dict:
        self.enviados.append((texto, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}


@pytest.fixture(autouse=True)
def _sem_threema(monkeypatch) -> list[tuple[str, list[str]]]:
    _Threema.enviados = []
    monkeypatch.setattr(svc.threema, "ThreemaClient", _Threema)
    return _Threema.enviados


@pytest_asyncio.fixture
async def user(make_user) -> User:
    return await make_user()


async def _integ(
    db: AsyncSession, user: User, platform: IntegrationPlatform, nome: str
) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=platform,
        name=nome,
        credentials=encrypt_json({"access_token": "x", "shop_id": 1, "expires_at": 9999999999}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _nota(db: AsyncSession, nome: str, **kw) -> BlingNota:
    conta = BlingNota(
        nome=nome,
        client_id=f"cid-{nome}",
        basic_auth_b64="Ym9tZGlh",
        **kw,
    )
    db.add(conta)
    await db.commit()
    await db.refresh(conta)
    return conta


class _Provas:
    """Substitui `_prova_de_vida`: devolve a prova configurada pelo NOME da
    integração (padrão: passou). Guarda quem foi testado — é assim que o
    teste do cooldown afirma que o Bling nem foi chamado."""

    def __init__(self) -> None:
        self.por_conta: dict[str, vc.Prova] = {}
        self.testadas: list[str] = []

    def fake(self):
        async def _f(session, platform, integration):
            self.testadas.append(integration.name)
            return self.por_conta.get(integration.name) or vc.Prova(ok=True)

        return _f


class _ProvasNf:
    """Idem pras contas Bling de NF (por `bling_notas.nome`)."""

    def __init__(self) -> None:
        self.por_conta: dict[str, vc.Prova] = {}
        self.testadas: list[str] = []

    def fake(self):
        async def _f(session, conta):
            self.testadas.append(conta.nome)
            return self.por_conta.get(conta.nome) or vc.Prova(ok=True)

        return _f


@pytest.fixture
def cenario(monkeypatch):
    """Provas de vida falsas (integrações e contas de NF) + cooldown do Bling
    controlado pelo teste. Devolve (provas, provas_nf, cooldown)."""
    provas, provas_nf, cooldown = _Provas(), _ProvasNf(), {"s": 0}

    async def _ttl() -> int:
        return cooldown["s"]

    monkeypatch.setattr(vc, "_prova_de_vida", provas.fake())
    monkeypatch.setattr(vc, "_saude_bling_nota", provas_nf.fake())
    monkeypatch.setattr(vc, "_bling_cooldown_ttl", _ttl)
    return provas, provas_nf, cooldown


async def _abertas(db: AsyncSession) -> dict[str, OuvidoriaOcorrencia]:
    rows = (
        await db.execute(
            select(OuvidoriaOcorrencia).where(OuvidoriaOcorrencia.fechada_em.is_(None))
        )
    ).scalars()
    return {o.chave: o for o in rows}


async def _todas(db: AsyncSession, chave: str) -> list[OuvidoriaOcorrencia]:
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(OuvidoriaOcorrencia.chave == chave)
                .order_by(OuvidoriaOcorrencia.aberta_em)
            )
        ).scalars()
    )


async def _rodadas(db: AsyncSession) -> list[OuvidoriaRodada]:
    return list(
        (
            await db.execute(select(OuvidoriaRodada).order_by(OuvidoriaRodada.iniciada_em))
        ).scalars()
    )


# ─── classificação do erro (pura) ──────────────────────────────────────────


async def test_classe_do_erro_reconhece_cada_familia_de_credencial():
    assert vc._classe_do_erro("status=401 body={}") == "token_vencido"
    assert vc._classe_do_erro('error: {"error":"invalid_grant"}') == "token_vencido"
    assert vc._classe_do_erro("TikTok: Expired credentials (105002)") == "token_vencido"
    assert vc._classe_do_erro("error_token_expired: token expirado") == "token_vencido"
    assert vc._classe_do_erro("status=403 body=error_partner_key_expired") == "chave_expirada"
    assert vc._classe_do_erro("magalu_refresh_failed body=invalid_client") == "app_invalido"
    assert vc._classe_do_erro("error: missing client_id or client_secret") == "app_invalido"
    assert vc._classe_do_erro("missing_credentials: app_key,app_secret") == "credencial_incompleta"
    assert (
        vc._classe_do_erro("conta sem refresh_token (reautorizar no Bling)")
        == "credencial_incompleta"
    )
    assert vc._classe_do_erro("status=403 body=error_permission") == "escopo"
    assert vc._classe_do_erro("TikTok 105005: no permission") == "escopo"


async def test_classe_do_erro_nao_confunde_bloqueio_de_rede_com_credencial():
    """429/5xx/timeout NUNCA mandam reautorizar — e o 403 em HTML do
    Cloudflare (Bling) e da Azion (Magalu) também não: são bloqueio de
    borda numa conta saudável."""
    assert vc._classe_do_erro("status=429 body=too many requests") is None
    assert vc._classe_do_erro("status=503 body=service unavailable") is None
    assert vc._classe_do_erro("error: ReadTimeout") is None
    assert vc._classe_do_erro("error: status=403 cf_html=True") is None
    assert vc._classe_do_erro("bling_cf_cooldown_active ttl_s=2400") is None
    assert vc._classe_do_erro("status=403 body=<html><title>Access Denied</title>") is None
    assert vc._classe_do_erro("http_error: [Errno 61] Connection refused") is None
    assert vc._classe_do_erro("") is None
    assert vc._classe_do_erro("algo que ninguém previu") is None


async def test_texto_do_erro_preserva_status_e_corpo_da_resposta():
    """É no CORPO que vem `error_partner_key_expired` — `str(e)` de um
    HTTPStatusError esconderia justamente o que decide a classe."""
    req = httpx.Request("GET", "https://partner.shopeemobile.com/x")
    resp = httpx.Response(403, text="error_partner_key_expired", request=req)
    e = httpx.HTTPStatusError("boom", request=req, response=resp)
    assert vc._texto_do_erro(e) == "status=403 body=error_partner_key_expired"
    assert vc._classe_do_erro(vc._texto_do_erro(e)) == "chave_expirada"
    assert vc._texto_do_erro(RuntimeError("missing refresh_token")) == "missing refresh_token"


# ─── provas de vida por plataforma ─────────────────────────────────────────


async def test_saude_padrao_usa_o_test_connection_do_client():
    from app.services.marketplaces.base import TestResult

    class _Client:
        def __init__(self, r):
            self.r = r
            self.chamadas = 0

        async def test_connection(self):
            self.chamadas += 1
            if isinstance(self.r, Exception):
                raise self.r
            return self.r

    c = _Client(TestResult(ok=True, info={"id": 1}))
    assert (await vc._saude_padrao(c)).ok and c.chamadas == 1
    p = await vc._saude_padrao(_Client(TestResult(ok=False, detail="status=401 body={}")))
    assert not p.ok and p.erro == "status=401 body={}"
    p = await vc._saude_padrao(_Client(RuntimeError("status=403 cf_html=True")))
    assert not p.ok and vc._classe_do_erro(p.erro) is None


async def test_saude_shopee_le_o_expire_time_e_o_corpo_do_erro():
    class _Client:
        def __init__(self, resp=None, exc=None):
            self.resp, self.exc = resp, exc
            self.paths: list[str] = []

        async def _request(self, method, path):
            self.paths.append(path)
            if self.exc:
                raise self.exc
            return self.resp

    vence = int((datetime.now(UTC) + timedelta(days=5)).timestamp())
    c = _Client(httpx.Response(200, json={"shop_name": "injox", "expire_time": vence}))
    p = await vc._saude_shopee(c)
    assert p.ok and p.fonte == "shopee_expire_time"
    assert p.vence_em == datetime.fromtimestamp(vence, tz=UTC)
    assert c.paths == ["/api/v2/shop/get_shop_info"]

    # Erro no corpo de um 200 (a Shopee responde assim).
    p = await vc._saude_shopee(
        _Client(httpx.Response(200, json={"error": "error_auth", "message": "invalid token"}))
    )
    assert not p.ok and p.erro == "error_auth: invalid token"
    assert vc._classe_do_erro(p.erro) == "token_vencido"

    # Refresh recusado: a chave do app venceu (15/09) — status E corpo.
    req = httpx.Request("POST", "https://partner.shopeemobile.com/token")
    resp = httpx.Response(403, text="error_partner_key_expired", request=req)
    p = await vc._saude_shopee(
        _Client(exc=httpx.HTTPStatusError("x", request=req, response=resp))
    )
    assert not p.ok and vc._classe_do_erro(p.erro) == "chave_expirada"

    p = await vc._saude_shopee(_Client(httpx.Response(500, text="oops")))
    assert not p.ok and p.erro == "status=500 body=oops"


async def test_saude_tiktok_le_a_validade_depois_do_refresh(monkeypatch):
    chamadas: list[tuple] = []

    async def _fetch(access_token, app_key, app_secret):
        chamadas.append((access_token, app_key, app_secret))
        if access_token == "ruim":
            req = httpx.Request("GET", "https://open-api.tiktokglobalshop.com/x")
            raise httpx.HTTPStatusError(
                "x", request=req, response=httpx.Response(401, text="105002", request=req)
            )
        return {"id": "7"}

    monkeypatch.setattr(vc.TikTokClient, "fetch_shop_info", staticmethod(_fetch))
    vence = int((datetime.now(UTC) + timedelta(days=30)).timestamp())

    class _Client:
        def __init__(self, creds, novo=None):
            self.creds = dict(creds)
            self.novo = novo or {}

        @property
        def access_token(self):
            return str(self.creds.get("access_token") or "")

        @property
        def app_key(self):
            return str(self.creds.get("app_key") or "")

        @property
        def app_secret(self):
            return str(self.creds.get("app_secret") or "")

        async def _ensure_fresh_token(self):
            self.creds.update(self.novo)

    base = {"app_key": "k", "app_secret": "s", "access_token": "velho"}
    # O refresh trocou o token E a validade: a prova lê o valor NOVO.
    c = _Client(base | {"refresh_token_expires_at": 1}, {"access_token": "bom",
                                                         "refresh_token_expires_at": vence})
    p = await vc._saude_tiktok(c)
    assert p.ok and p.fonte == "tiktok_refresh_token"
    assert p.vence_em == datetime.fromtimestamp(vence, tz=UTC)
    assert chamadas == [("bom", "k", "s")]

    # Sem validade informada (0) não dá pra avisar vencimento.
    p = await vc._saude_tiktok(_Client(base | {"refresh_token_expires_at": 0}))
    assert p.ok and p.vence_em is None

    # HTTP ≠ 200 mantém o status (o test_connection do client perderia).
    p = await vc._saude_tiktok(_Client(base | {"access_token": "ruim"}))
    assert not p.ok and vc._classe_do_erro(p.erro) == "token_vencido"

    # Credencial incompleta nem chama.
    antes = len(chamadas)
    p = await vc._saude_tiktok(_Client({"app_key": "k"}))
    assert not p.ok and len(chamadas) == antes
    assert vc._classe_do_erro(p.erro) == "credencial_incompleta"


async def test_saude_bling_nota_nao_bate_no_bling_sem_token(db, monkeypatch):
    from app.routers import notas_fiscais as nf

    conta = await _nota(db, "semtoken")
    p = await _SAUDE_NF_REAL(db, conta)
    assert not p.ok and vc._classe_do_erro(p.erro) == "credencial_incompleta"

    gets: list[tuple] = []

    async def _tok(session, c):
        return "tok"

    async def _get(token, path, params=None):
        gets.append((token, path, params))
        return {"data": []}

    monkeypatch.setattr(nf, "_ensure_token", _tok)
    monkeypatch.setattr(nf, "_bling_get", _get)
    conta.refresh_token = "rt"
    await db.commit()
    assert (await _SAUDE_NF_REAL(db, conta)).ok
    assert gets == [("tok", "/nfe", {"pagina": 1, "limite": 1})]

    async def _tok_ruim(session, c):
        raise RuntimeError("bling http 401: invalid_grant")

    monkeypatch.setattr(nf, "_ensure_token", _tok_ruim)
    p = await _SAUDE_NF_REAL(db, conta)
    assert not p.ok and vc._classe_do_erro(p.erro) == "token_vencido"


# ─── a rodada ──────────────────────────────────────────────────────────────


async def test_rodada_conta_ok_nao_abre_nada_e_fecha_a_aberta_como_sumiu(db, user, cenario):
    provas, _nf, _cd = cenario
    integ = await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    provas.por_conta["marquezini"] = vc.Prova(ok=False, erro="status=401 body={}")

    r1 = await vc.vigia_credenciais_run(db)
    assert r1["contas"] == 1 and r1["novas"] == 1 and r1["sem_acesso"] == 1
    assert set(await _abertas(db)) == {f"conta:{integ.id}"}

    provas.por_conta.pop("marquezini")
    r2 = await vc.vigia_credenciais_run(db)
    assert r2["ok"] == 1 and r2["sumiram"] == 1 and await _abertas(db) == {}
    fechada = (await _todas(db, f"conta:{integ.id}"))[-1]
    assert fechada.fechamento == "sumiu" and fechada.fechada_por == "robô"
    assert r2["resumo"] == "1 conta ok · 0 sem acesso · 0 instáveis"


async def test_rodada_erro_de_credencial_vira_pessoa_na_primeira_rodada(db, user, cenario):
    provas, _nf, _cd = cenario
    integ = await _integ(db, user, IntegrationPlatform.SHOPEE, "injox")
    provas.por_conta["injox"] = vc.Prova(
        ok=False, erro="status=403 body=error_partner_key_expired"
    )

    r = await vc.vigia_credenciais_run(db)

    o = (await _abertas(db))[f"conta:{integ.id}"]
    assert o.titulo == "Conta sem acesso à API" and o.severidade == "pessoa"
    assert o.precisa_pessoa and o.acao == vc.ACAO_REAUTORIZAR and o.link == "/integrations"
    assert o.plataforma == "shopee" and o.conta == "Shopee injox"
    assert o.detalhe.startswith("Chave do app expirada em Shopee injox:")
    assert "nenhum robô enxerga essa conta" in o.detalhe
    assert o.dados["erro_tipo"] == "acesso" and o.dados["erro_classe"] == "chave_expirada"
    assert o.dados["falhas_seguidas"] == 1 and o.dados["integration_id"] == str(integ.id)
    assert o.dados["cooldown"] is False and o.dados["plataforma"] == "shopee"
    assert r["sem_acesso"] == 1 and r["instaveis"] == 0
    assert r["resumo"] == "0 contas ok · 1 sem acesso · 0 instáveis"


async def test_rodada_instabilidade_fica_info_e_so_promove_na_segunda_seguida(db, user, cenario):
    provas, _nf, _cd = cenario
    integ_id = (await _integ(db, user, IntegrationPlatform.ML, "marquezini")).id
    provas.por_conta["marquezini"] = vc.Prova(ok=False, erro="status=503 body=oops")

    r1 = await vc.vigia_credenciais_run(db)
    o = (await _abertas(db))[f"conta:{integ_id}"]
    assert o.titulo.startswith("Conta não respondeu") and o.severidade == "info"
    assert o.precisa_pessoa is False and o.acao is None and o.link is None
    assert o.dados["erro_tipo"] == "instavel" and o.dados["erro_classe"] is None
    assert o.dados["falhas_seguidas"] == 1
    assert r1["instaveis"] == 1 and r1["sem_acesso"] == 0 and r1["novas"] == 1

    r2 = await vc.vigia_credenciais_run(db)
    o = (await _abertas(db))[f"conta:{integ_id}"]
    assert o.titulo == "Conta sem acesso à API" and o.severidade == "pessoa"
    assert o.precisa_pessoa and o.acao == vc.ACAO_REAUTORIZAR
    assert "falhou em 2 rodadas seguidas" in o.detalhe
    assert o.dados["falhas_seguidas"] == 2 and o.dados["erro_tipo"] == "instavel"
    # Promovida não é linha nova: é a MESMA ocorrência, que persiste.
    assert r2["novas"] == 0 and r2["persistem"] == 1 and len(await _todas(db, o.chave)) == 1


async def test_rodada_grava_last_test_na_integracao(db, user, cenario):
    """Os 3 campos do botão Testar: Sistema › Integrações fica verde/vermelho
    sem ninguém clicar."""
    provas, _nf, _cd = cenario
    integ_id = (await _integ(db, user, IntegrationPlatform.AMAZON, "KFA")).id
    provas.por_conta["KFA"] = vc.Prova(ok=False, erro="status=401 body=invalid_grant")

    await vc.vigia_credenciais_run(db)
    integ = await db.get(Integration, integ_id)
    assert integ.last_test_ok is False and integ.last_test_at is not None
    assert integ.last_error == "status=401 body=invalid_grant"

    provas.por_conta.pop("KFA")
    await vc.vigia_credenciais_run(db)
    integ = await db.get(Integration, integ_id)
    assert integ.last_test_ok is True and integ.last_error is None


async def test_instabilidade_nao_carimba_o_last_test_da_tela(db, user, cenario):
    """Um 503 do ML não pode pintar de vermelho uma conta saudável em Sistema ›
    Integrações (nem tirá-la do contador "conectadas" do dashboard): o carimbo
    só vale pra prova CONCLUSIVA. E o erro de um teste manual recém-feito
    continua onde estava."""
    provas, _nf, _cd = cenario
    integ_id = (await _integ(db, user, IntegrationPlatform.ML, "marquezini")).id
    integ = await db.get(Integration, integ_id)
    integ.last_test_ok = True
    integ.last_error = "erro do teste manual"
    marcado_em = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    integ.last_test_at = marcado_em
    await db.commit()
    provas.por_conta["marquezini"] = vc.Prova(ok=False, erro="status=503 body=oops")

    r = await vc.vigia_credenciais_run(db)

    integ = await db.get(Integration, integ_id)
    assert integ.last_test_ok is True and integ.last_error == "erro do teste manual"
    assert integ.last_test_at == marcado_em
    # A ocorrência, sim, registra o soluço (info, sem pessoa).
    assert r["instaveis"] == 1
    assert (await _abertas(db))[f"conta:{integ_id}"].severidade == "info"


async def test_falha_nossa_numa_conta_nao_derruba_a_rodada(db, user, cenario, monkeypatch):
    """O erro fora da chamada de saúde (gravar a ocorrência, o commit) é
    best-effort POR CONTA: a conta seguinte é olhada, a rodada fica ok e as
    chaves da conta problemática contam como vistas — "não olhei" não é
    "sumiu"."""
    provas, _nf, _cd = cenario
    # Os ids ficam guardados como texto: o rollback do best-effort expira os
    # objetos DESTA sessão também, e ler `integ.id` depois erraria no teste.
    ruim = str((await _integ(db, user, IntegrationPlatform.ML, "aguiar")).id)
    boa = str((await _integ(db, user, IntegrationPlatform.ML, "marquezini")).id)
    provas.por_conta["aguiar"] = vc.Prova(ok=False, erro="status=401 body={}")
    provas.por_conta["marquezini"] = vc.Prova(ok=False, erro="status=401 body={}")

    # 1ª rodada normal: as duas viram ocorrência.
    r1 = await vc.vigia_credenciais_run(db)
    assert r1["sem_acesso"] == 2 and set(await _abertas(db)) == {
        f"conta:{ruim}", f"conta:{boa}"
    }

    # 2ª rodada: gravar a ocorrência da 1ª conta (a ordem é por created_at)
    # explode — DataError, corrida no índice único, sessão suja.
    original = vc._registrar_conta_falhou
    chamadas: list[str] = []

    async def _explode(r, **kw):
        chamadas.append(kw["conta"])
        if kw["conta"] == "Mercado Livre aguiar":
            raise RuntimeError("DataError: value too long")
        return await original(r, **kw)

    monkeypatch.setattr(vc, "_registrar_conta_falhou", _explode)
    r2 = await vc.vigia_credenciais_run(db)

    assert chamadas == ["Mercado Livre aguiar", "Mercado Livre marquezini"]
    assert r2["erro_interno"] == 1 and r2["sem_acesso"] == 1 and r2["sumiram"] == 0
    assert set(await _abertas(db)) == {f"conta:{ruim}", f"conta:{boa}"}
    rodada = (await _rodadas(db))[-1]
    assert rodada.ok is True and rodada.contadores["erro_interno"] == 1
    assert "1 não olhada (erro interno)" in (rodada.resumo or "")


async def test_nome_de_conta_comprido_e_cortado_em_120(db, user, cenario):
    """`ouvidoria_ocorrencias.conta` é String(120) e `integrations.name` é Text
    sem limite — sem o corte o flush derrubaria a rodada inteira."""
    provas, _nf, _cd = cenario
    nome = "z" * 200
    integ = await _integ(db, user, IntegrationPlatform.TIKTOK, nome)
    provas.por_conta[nome] = vc.Prova(ok=False, erro="status=401 body={}")

    r = await vc.vigia_credenciais_run(db)

    o = (await _abertas(db))[f"conta:{integ.id}"]
    assert len(o.conta) == 120 and o.conta.startswith("TikTok zzz")
    assert r["sem_acesso"] == 1 and (await _rodadas(db))[-1].ok is True


async def test_bling_em_cooldown_cloudflare_e_pulado_sem_abrir_nem_fechar(db, user, cenario):
    provas, provas_nf, cooldown = cenario
    bling = await _integ(db, user, IntegrationPlatform.BLING, "principal")
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    await _nota(db, "josefinaapp", refresh_token="rt")
    provas.por_conta["principal"] = vc.Prova(ok=False, erro="status=401 body={}")

    r1 = await vc.vigia_credenciais_run(db)
    assert set(await _abertas(db)) == {f"conta:{bling.id}"} and r1["puladas"] == 0

    cooldown["s"] = 3600
    provas.testadas.clear()
    provas_nf.testadas.clear()
    r2 = await vc.vigia_credenciais_run(db)

    # Nem o Bling principal nem a conta de NF foram tocados; a ocorrência
    # aberta continua de pé (não olhou ≠ sumiu) e ninguém foi promovido.
    assert provas.testadas == ["marquezini"] and provas_nf.testadas == []
    assert r2["puladas"] == 2 and r2["contas"] == 1 and r2["notas"] == 0
    assert r2["sumiram"] == 0 and r2["novas"] == 0 and r2["persistem"] == 0
    assert set(await _abertas(db)) == {f"conta:{bling.id}"}
    assert "2 Bling puladas (cooldown Cloudflare 60 min)" in r2["resumo"]


async def test_vence_abre_em_baixa_e_fecha_quando_a_pessoa_reautoriza(db, user, cenario):
    provas, _nf, _cd = cenario
    agora = datetime.now(UTC)
    longe = await _integ(db, user, IntegrationPlatform.TIKTOK, "longe")
    tres = await _integ(db, user, IntegrationPlatform.TIKTOK, "tres")
    hoje = await _integ(db, user, IntegrationPlatform.TIKTOK, "hoje")
    passou = await _integ(db, user, IntegrationPlatform.SHOPEE, "passou")
    provas.por_conta["longe"] = vc.Prova(
        ok=True, vence_em=agora + timedelta(days=30), fonte="tiktok_refresh_token"
    )
    provas.por_conta["tres"] = vc.Prova(
        ok=True, vence_em=agora + timedelta(days=3, hours=1), fonte="tiktok_refresh_token"
    )
    provas.por_conta["hoje"] = vc.Prova(
        ok=True, vence_em=agora + timedelta(minutes=30), fonte="tiktok_refresh_token"
    )
    provas.por_conta["passou"] = vc.Prova(
        ok=True, vence_em=agora - timedelta(hours=2), fonte="shopee_expire_time"
    )

    r1 = await vc.vigia_credenciais_run(db)

    abertas = await _abertas(db)
    assert set(abertas) == {f"vence:{tres.id}", f"vence:{hoje.id}", f"vence:{passou.id}"}
    assert f"vence:{longe.id}" not in abertas  # 30 dias > vencimento_dias (7)
    o = abertas[f"vence:{tres.id}"]
    assert o.titulo == "Acesso vence em 3 dias" and o.severidade == "baixa"
    assert o.precisa_pessoa and o.link == "/integrations"
    assert o.acao.startswith("Reautorizar em Sistema › Integrações antes de ")
    assert "refresh token do TikTok" in o.detalhe and "A autorização de TikTok tres" in o.detalhe
    assert o.dados["fonte"] == "tiktok_refresh_token" and o.dados["dias"] == 3
    assert abertas[f"vence:{hoje.id}"].titulo == "Acesso vence hoje"
    assert abertas[f"vence:{passou.id}"].titulo == "Acesso venceu — ainda responde"
    assert "autorização da loja na Shopee" in abertas[f"vence:{passou.id}"].detalhe
    assert r1["vencendo"] == 3 and r1["ok"] == 4
    assert r1["resumo"] == "4 contas ok · 0 sem acesso · 0 instáveis · 3 vencendo"

    # A pessoa reautorizou as três: a validade volta a ser longa e o robô
    # fecha sozinho.
    for nome in ("tres", "hoje", "passou"):
        p = provas.por_conta[nome]
        provas.por_conta[nome] = vc.Prova(ok=True, vence_em=agora + timedelta(days=365),
                                          fonte=p.fonte)
    r2 = await vc.vigia_credenciais_run(db)
    assert r2["sumiram"] == 3 and await _abertas(db) == {}


async def test_conta_caida_nao_fecha_nem_reabre_o_vence(db, user, cenario):
    """Enquanto a conta está fora, a ocorrência viva é a `conta:` — o
    `vence:` fica parado até a pessoa reautorizar, e aí os dois fecham."""
    provas, _nf, _cd = cenario
    integ = await _integ(db, user, IntegrationPlatform.TIKTOK, "injox")
    agora = datetime.now(UTC)
    provas.por_conta["injox"] = vc.Prova(
        ok=True, vence_em=agora + timedelta(days=2), fonte="tiktok_refresh_token"
    )
    await vc.vigia_credenciais_run(db)
    assert set(await _abertas(db)) == {f"vence:{integ.id}"}

    provas.por_conta["injox"] = vc.Prova(ok=False, erro="status=401 body={}")
    r2 = await vc.vigia_credenciais_run(db)
    assert r2["sumiram"] == 0
    assert set(await _abertas(db)) == {f"vence:{integ.id}", f"conta:{integ.id}"}

    provas.por_conta["injox"] = vc.Prova(ok=True)
    r3 = await vc.vigia_credenciais_run(db)
    assert r3["sumiram"] == 2 and await _abertas(db) == {}


async def test_contas_de_nf_entram_e_convite_pendente_fica_de_fora(db, cenario, monkeypatch):
    from app.routers import notas_fiscais as nf

    _provas, provas_nf, _cd = cenario
    # Token válido em mão: o `_ensure_token` devolve sem bater no Bling.
    ok = await _nota(db, "josefinaapp", access_token="at", refresh_token="rt")
    sem = await _nota(db, "semtoken")
    await _nota(db, "convite", authorization_code="code-novo")

    async def _get(token, path, params=None):
        return {"data": []}

    monkeypatch.setattr(nf, "_bling_get", _get)
    monkeypatch.setattr(vc, "_saude_bling_nota", _SAUDE_NF_REAL)

    r = await vc.vigia_credenciais_run(db)

    # A linha que só tem o convite é pulada: o cron das :45 troca sozinho.
    assert r["notas"] == 2 and provas_nf.testadas == []
    abertas = await _abertas(db)
    assert set(abertas) == {f"conta:nf:{sem.id}"}
    o = abertas[f"conta:nf:{sem.id}"]
    assert o.titulo == "Conta sem acesso à API" and o.severidade == "pessoa"
    assert o.conta == "Bling NF semtoken" and o.plataforma == "bling"
    assert o.acao == vc.ACAO_REAUTORIZAR_NF and o.link == "/notas-fiscais"
    assert o.dados["bling_nota_id"] == str(sem.id)
    assert o.dados["erro_classe"] == "credencial_incompleta"
    assert str(ok.id)  # a conta boa não virou ocorrência
    assert r["ok"] == 1 and r["sem_acesso"] == 1


async def test_rodada_grava_contadores_e_resumo_no_painel(db, user, cenario):
    provas, _nf, _cd = cenario
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    await _integ(db, user, IntegrationPlatform.MAGALU, "magalu")
    provas.por_conta["magalu"] = vc.Prova(ok=False, erro="status=429 body=slow down")

    r = await vc.vigia_credenciais_run(db)

    rodadas = await _rodadas(db)
    assert len(rodadas) == 1 and rodadas[0].ok
    assert rodadas[0].contadores["contas"] == 2 and rodadas[0].contadores["instaveis"] == 1
    # Todos os contadores nascem em 0 — a tela lê direto do dict.
    assert set(vc._CONTADORES) <= set(rodadas[0].contadores)
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.ultima_rodada_ok is True and robo.ultima_rodada_resumo == r["resumo"]
    assert r["resumo"] == "1 conta ok · 0 sem acesso · 1 instável"


async def test_rodada_avisa_no_threema_so_quando_ligado(db, user, cenario, _sem_threema):
    provas, _nf, _cd = cenario
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    provas.por_conta["marquezini"] = vc.Prova(ok=False, erro="status=401 body={}")
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.threema_recipients = "ABCDEFGH"
    await db.commit()

    # Nasce silencioso: registra no painel e não manda nada.
    assert robo.modo == "silencioso"
    r = await vc.vigia_credenciais_run(db)
    assert r["novas"] == 1 and r["avisadas"] == 0 and _sem_threema == []

    robo.modo = "ligado"
    await db.commit()
    r = await vc.vigia_credenciais_run(db)
    assert r["avisadas"] == 1 and len(_sem_threema) == 1
    texto, quem = _sem_threema[0]
    assert quem == ["ABCDEFGH"]
    assert "Conta sem acesso à API" in texto and vc.ACAO_REAUTORIZAR in texto


async def test_rodada_com_erro_grava_rodada_falha_e_sobe(db, user, cenario, monkeypatch):
    async def _quebra(session) -> list:
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(vc, "_contas_nf", _quebra)
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    with pytest.raises(RuntimeError, match="banco caiu"):
        await vc.vigia_credenciais_run(db)
    rodada = (await _rodadas(db))[-1]
    assert rodada.ok is False and "banco caiu" in (rodada.erro or "")
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.ultima_rodada_ok is False


async def test_cooldown_do_bling_e_best_effort_sem_redis(monkeypatch):
    """Redis fora do ar não pode derrubar a rodada inteira por causa de um
    pré-check — sem resposta o robô assume que dá pra testar."""
    import app.redis_client as rc

    class _Redis:
        def __init__(self, valor=None, exc=None):
            self.valor, self.exc = valor, exc

        async def get(self, key):
            if self.exc:
                raise self.exc
            return self.valor

    monkeypatch.setattr(rc, "redis", _Redis(exc=RuntimeError("connection refused")))
    assert await vc._bling_cooldown_ttl() == 0
    monkeypatch.setattr(rc, "redis", _Redis(valor=None))
    assert await vc._bling_cooldown_ttl() == 0
    import time as _time

    monkeypatch.setattr(rc, "redis", _Redis(valor=str(int(_time.time()) + 600)))
    assert 590 <= await vc._bling_cooldown_ttl() <= 600
    monkeypatch.setattr(rc, "redis", _Redis(valor=str(int(_time.time()) - 60)))
    assert await vc._bling_cooldown_ttl() == 0


async def test_sweep_e_serializado_pelo_advisory_lock(db, monkeypatch):
    """Sessão que segura o lock do sweep → o outro sweep sai na hora."""
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vc._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vc.vigia_credenciais_sweep() == {"skipped": "lock_busy"}
    await db.rollback()  # solta o lock

    chamado = []

    async def _run(session):
        chamado.append(1)
        return {"ok": True}

    monkeypatch.setattr(vc, "vigia_credenciais_run", _run)
    assert await vc.vigia_credenciais_sweep() == {"ok": True} and chamado == [1]


# ─── tick do worker ────────────────────────────────────────────────────────


async def test_tick_nao_roda_com_o_robo_desligado(db, monkeypatch):
    from app import worker

    chamadas: list[int] = []

    async def _sweep():
        chamadas.append(1)
        return {"novas": 0}

    monkeypatch.setattr(vc, "vigia_credenciais_sweep", _sweep)
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.modo = "desligado"
    await db.commit()
    await worker.vigia_credenciais_tick({})
    assert chamadas == [] and await _rodadas(db) == []

    for modo in ("silencioso", "ligado"):
        robo.modo = modo
        await db.commit()
        await worker.vigia_credenciais_tick({})
    assert chamadas == [1, 1]
