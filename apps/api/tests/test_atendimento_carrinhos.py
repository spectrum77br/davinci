# ruff: noqa: S105  (tokens de teste, nada real)
"""Carrinho abandonado dos sites Charlots e Uranyx (RF9, 02/10/2026) — a frente A no DaVinci.

O site é FALSO (httpx.MockTransport no formato EXATO do contrato do topo de
`services/atendimento/carrinhos.py`); nada sai do processo. O que se garante:

- a rodada só roda com os dois interruptores; uma por vez (trava no Redis);
  manda o token do site só no header, com horas/dias/desde, sem seguir
  redirecionamento;
- a saúde do canal na aba Lojas: ok, sem_escopo (401), sem_endpoint (404:
  a rota não foi publicada), desligado (sem token), erro (o resto); um site
  com erro não para o outro; o token e o dado do lojista nunca vão para o
  log nem para o `ultimo_erro`;
- o episódio: carrinho parado vira conversa (lojista) com etiqueta CARRINHO
  e entra na fila; a leitura seguinte não duplica; finalizado pelo WhatsApp
  → recuperado (Pós-venda); esvaziado → não recuperado; 7 dias → não
  recuperado; o evento atrasado corrige o prazo; o carrinho que ficou não
  reabre sem o lojista mexer; a sobra de um pedido não vira abandono; o
  que some da lista fica aberto com a marca;
- a lista branca do que vem do site (URL só https, SKU válido, campos do
  lojista);
- o cartão (GET /conversas/{id}/carrinho): lojista, itens com o estoque
  ATUAL do DaVinci pela regra do site (SKU exato, "base.*" = lotes de
  venda, desconhecido nunca vira zero, demanda das linhas que dividem SKU),
  anteriores, taxa e leitura; "Marcar como resolvido" (POST
  /carrinhos/{id}/resolvido), permissão e escopo por equipe.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoCarrinho,
    AtendimentoConversa,
    AtendimentoEtiquetaHistorico,
    AtendimentoMensagem,
    Product,
)
from app.routers import atendimento as rota
from app.services.atendimento import carrinhos
from app.services.atendimento.constantes import (
    CARRINHO_ABERTO,
    CARRINHO_NAO_RECUPERADO,
    CARRINHO_RECUPERADO,
    CARRINHO_RESOLVIDO,
    ETIQUETA_CARRINHO,
    ETIQUETA_POS_VENDA,
    ETIQUETA_PRE_VENDA,
    FIM_CARRINHO_ESVAZIADO,
    FIM_CARRINHO_FINALIZADO,
    FIM_CARRINHO_PRAZO,
    FIM_CARRINHO_RESOLVIDO,
)

URL = "/api/atendimento"
T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
PARADO = T0 - timedelta(hours=30)
TOKENS = {"charlots": "tok-charlots-segredo", "uranyx": "tok-uranyx-segredo"}
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
SO_VER = {"atendimento": {"view": True, "edit": False}}


def _z(quando: datetime) -> str:
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────── o site falso (o contrato) ───────────────


def item(
    *,
    produto_id: Any = 45,
    titulo: str = "Mala ABS M2",
    cor: str = "M2|Preto",
    quantidade: Any = 3,
    skus: Any = ("b1001.pi",),
    url: Any = "https://charlots.com.br/produto/mala-abs",
    imagem: Any = "https://charlots.com.br/uploads/mala.webp",
    preco: Any = 199.9,
) -> dict:
    return {
        "produto_id": produto_id,
        "titulo": titulo,
        "cor": cor,
        "cor_rotulo": cor.replace("|", " "),
        "quantidade": quantidade,
        "skus": list(skus) if isinstance(skus, tuple) else skus,
        "url": url,
        "imagem": imagem,
        "preco": preco,
    }


def carrinho(
    lojista_id: Any = 123, *, parado: datetime = PARADO, itens: list | None = None, **lojista
) -> dict:
    itens = itens if itens is not None else [item()]
    return {
        "lojista": {
            "id": lojista_id,
            "nome": "Fulana de Tal",
            "empresa": "Loja X",
            "email": "fulana@lojax.com.br",
            "telefone": "11999998888",
            "cidade": "São Paulo",
            "estado": "SP",
            "status": "aprovado",
            "cnpj": None,
            **lojista,
        },
        "parado_desde": _z(parado),
        "quantidade_total": sum(
            int(i["quantidade"]) for i in itens if str(i.get("quantidade")).isdigit()
        ),
        "itens": itens,
    }


def evento(
    lojista_id: Any = 123,
    *,
    tipo: str = "finalizado",
    criado: datetime,
    id: Any = 987,  # noqa: A002 — o nome do contrato
    restantes: int = 0,
) -> dict:
    return {
        "id": id,
        "tipo": tipo,
        "lojista_id": lojista_id,
        "criado_em": _z(criado),
        "itens": [{"produto_id": 45, "cor": "M2|Preto", "quantidade": 3, "skus": ["b1001.pi"]}],
        "restantes": restantes,
    }


class SiteFalso:
    """Os dois sites: o que cada um responde e o que o DaVinci pediu."""

    HOSTS = {"charlots.com.br": "charlots", "uranyx.com.br": "uranyx"}

    def __init__(self) -> None:
        self.respostas: dict[str, Any] = {}
        self.pedidos: list[httpx.Request] = []

    def responde(
        self,
        site: str,
        *,
        carrinhos_: list | None = None,
        eventos: list | None = None,
        truncado: bool = False,
        como: str | None = None,
        ativos: list | None = None,
    ) -> None:
        self.respostas[site] = {
            "site": como or site,
            "gerado_em": _z(T0),
            "horas": 24,
            "truncado": truncado,
            "carrinhos": carrinhos_ or [],
            "eventos": eventos or [],
            # O que o pacote do site (02/10) manda além do contrato da base.
            "dias": 30,
            "desde": _z(T0 - timedelta(days=30)),
            "ativos": ativos or [],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        site = self.HOSTS.get(request.url.host)
        if site is None or request.url.path != "/api/davinci/carrinhos":
            return httpx.Response(404, text="nao achei")
        if request.headers.get("authorization") != f"Bearer {TOKENS[site]}":
            return httpx.Response(401, json={"erro": "nao_autorizado"})
        r = self.respostas.get(site, None)
        if r is None:
            r = {"site": site, "carrinhos": [], "eventos": [], "truncado": False}
        if isinstance(r, httpx.Response):
            return r
        if isinstance(r, Exception):
            raise r
        return httpx.Response(200, json=r, headers={"Cache-Control": "no-store"})

    def cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def de(self, site: str) -> list[httpx.Request]:
        return [p for p in self.pedidos if self.HOSTS.get(p.url.host) == site]


class RedisFalso:
    def __init__(self) -> None:
        self.dados: dict[str, Any] = {}

    async def get(self, chave):
        return self.dados.get(chave)

    async def set(self, chave, valor, *, nx=False, ex=None):
        if nx and chave in self.dados:
            return None
        self.dados[chave] = valor
        return True

    async def eval(self, _script, _n, chave, token):
        if self.dados.get(chave) == token:
            del self.dados[chave]
            return 1
        return 0


class LogFalso:
    """Guarda tudo o que o módulo loga (para conferir que nada sensível vai)."""

    def __init__(self) -> None:
        self.linhas: list[tuple[str, dict]] = []

    def _guardar(self, evento: str, **kw: Any) -> None:
        self.linhas.append((evento, kw))

    info = warning = error = _guardar

    def texto(self) -> str:
        return json.dumps(self.linhas, default=str, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    """A caixa é só admin (rota.SO_ADMIN); os testes usam o recurso fino, como os outros."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture
def ligado(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_carrinhos_ativa", True)
    monkeypatch.setattr(s, "atendimento_carrinho_horas", 24)
    monkeypatch.setattr(s, "atendimento_sites_urls", "")
    monkeypatch.setattr(
        s, "sites_estoque_tokens", ",".join(f"{t}:{site}" for site, t in TOKENS.items())
    )
    return s


@pytest.fixture
def redis_falso(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(carrinhos, "redis", r)
    return r


@pytest.fixture
def log(monkeypatch) -> LogFalso:
    lf = LogFalso()
    monkeypatch.setattr(carrinhos, "logger", lf)
    return lf


@pytest.fixture
def site(ligado, redis_falso, log) -> SiteFalso:
    return SiteFalso()


async def rodada(site: SiteFalso, agora: datetime = T0) -> dict | None:
    async with site.cliente() as cliente:
        return await carrinhos.atendimento_carrinhos({}, cliente_http=cliente, agora=agora)


async def _canal(db: AsyncSession, nome: str) -> AtendimentoCanal:
    return (
        await db.execute(
            select(AtendimentoCanal)
            .where(AtendimentoCanal.externo_ref == f"site:{nome}")
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _carrinhos(db: AsyncSession, lojista: str = "123") -> list[AtendimentoCarrinho]:
    return list(
        (
            await db.execute(
                select(AtendimentoCarrinho)
                .where(AtendimentoCarrinho.lojista_id == lojista)
                .order_by(AtendimentoCarrinho.detectado_em)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _conversa(db: AsyncSession, cid) -> AtendimentoConversa:
    return (
        await db.execute(
            select(AtendimentoConversa)
            .where(AtendimentoConversa.id == cid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _mensagens(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
            )
        )
        .scalars()
        .all()
    )


# ─────────────── interruptores, trava e o pedido ao site ───────────────


async def test_desligado_nao_chama_ninguem(monkeypatch, redis_falso, log):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_carrinhos_ativa", False)
    falso = SiteFalso()
    assert await rodada(falso) is None
    monkeypatch.setattr(s, "atendimento_carrinhos_ativa", True)
    monkeypatch.setattr(s, "atendimento_leitura_ativa", False)
    assert await rodada(falso) is None
    assert falso.pedidos == []


async def test_uma_rodada_por_vez(site, redis_falso, db):
    redis_falso.dados[carrinhos.CHAVE_TRAVA] = "outra-rodada"
    assert await rodada(site) == {"pulado": True}
    assert site.pedidos == []
    # A trava é solta no fim (só a nossa).
    del redis_falso.dados[carrinhos.CHAVE_TRAVA]
    await rodada(site)
    assert carrinhos.CHAVE_TRAVA not in redis_falso.dados


async def test_o_pedido_ao_site_segue_o_contrato(site, ligado, monkeypatch, db):
    monkeypatch.setattr(ligado, "atendimento_carrinho_horas", 48)
    await rodada(site)
    assert {site.HOSTS[p.url.host] for p in site.pedidos} == {"charlots", "uranyx"}
    p = site.de("charlots")[0]
    assert p.method == "GET"
    assert str(p.url).startswith("https://charlots.com.br/api/davinci/carrinhos?")
    assert p.headers["authorization"] == f"Bearer {TOKENS['charlots']}"
    assert p.headers["accept"] == "application/json"
    assert dict(p.url.params) == {
        "horas": "48",
        "dias": "30",
        "desde": _z(T0 - timedelta(days=30)),
    }
    # O token só no header: nunca na URL.
    assert TOKENS["charlots"] not in str(p.url)


async def test_status_do_canal_na_aba_lojas(site, log, db, ligado, monkeypatch):
    # Charlots: a rota ainda não foi publicada (404); Uranyx lê normalmente.
    site.respostas["charlots"] = httpx.Response(404, text="<html>pagina</html>")
    site.responde("uranyx", carrinhos_=[carrinho(7, itens=[item(skus=["u100.pi"])])])
    await rodada(site)
    ch = await _canal(db, "charlots")
    assert ch.status == "sem_endpoint"
    assert "/api/davinci/carrinhos" in ch.ultimo_erro and "publicar o pacote" in ch.ultimo_erro
    ur = await _canal(db, "uranyx")
    assert (ur.status, ur.ultimo_ok_em is not None) == ("ok", True)
    assert (await _carrinhos(db, "7"))[0].site == "uranyx"

    casos = [
        (httpx.Response(401, json={"erro": "nao_autorizado"}), "sem_escopo", "HTTP 401"),
        (httpx.Response(500, text="boom"), "erro", "HTTP 500"),
        (httpx.Response(302, headers={"Location": "https://outro.com/x"}), "erro", "redirecionou"),
        (httpx.Response(422, json={"erro": "parametro_invalido"}), "erro", "HTTP 422"),
        (httpx.Response(200, text="<html>home</html>"), "erro", "não é JSON"),
        (httpx.Response(200, json={"site": "charlots"}), "erro", "sem a lista"),
        (httpx.ReadTimeout("lento"), "erro", "timeout"),
        (httpx.ConnectError("caiu"), "erro", "sem conexão"),
    ]
    for resposta, status, trecho in casos:
        site.respostas["charlots"] = resposta
        site.pedidos.clear()
        await rodada(site)
        ch = await _canal(db, "charlots")
        assert (ch.status, trecho in (ch.ultimo_erro or "")) == (status, True), (
            status,
            ch.ultimo_erro,
        )
        # Redirecionamento NÃO é seguido (o token iria junto).
        assert len(site.de("charlots")) == 1
    # O site que responde como OUTRO site não grava nada.
    site.responde("charlots", carrinhos_=[carrinho(55)], como="uranyx")
    await rodada(site)
    assert (await _canal(db, "charlots")).status == "erro"
    assert await _carrinhos(db, "55") == []

    # Sem o token do site no DaVinci: desligado, e o site nem é chamado.
    monkeypatch.setattr(ligado, "sites_estoque_tokens", f"{TOKENS['uranyx']}:uranyx")
    site.pedidos.clear()
    await rodada(site)
    ch = await _canal(db, "charlots")
    assert ch.status == "desligado" and "SITES_ESTOQUE_TOKENS" in ch.ultimo_erro
    assert site.de("charlots") == [] and len(site.de("uranyx")) == 1
    # Endereço inválido (http fora do localhost): erro, nada é chamado.
    monkeypatch.setattr(ligado, "atendimento_sites_urls", "uranyx=http://uranyx.com.br")
    site.pedidos.clear()
    await rodada(site)
    assert (await _canal(db, "uranyx")).status == "erro"
    assert site.de("uranyx") == []

    # Nunca o token nem dado do lojista no log ou no canal.
    tudo = log.texto() + json.dumps(
        [
            (c.ultimo_erro, c.cursor)
            for c in (await _canal(db, "charlots"), await _canal(db, "uranyx"))
        ],
        default=str,
    )
    for segredo in (*TOKENS.values(), "Fulana", "fulana@lojax.com.br", "11999998888"):
        assert segredo not in tudo


async def test_respostas_de_erro_do_proprio_site(site, log, db):
    """O JSON de erro do CarrinhosDavinci.php vira o texto certo (nada do corpo vaza)."""
    casos = [
        (
            httpx.Response(401, json={"erro": "nao_autorizado", "motivo": "sem_token"}),
            "sem_escopo",
            ("sem_token", ".htaccess"),
        ),
        (httpx.Response(401, json={"erro": "nao_autorizado"}), "sem_escopo", ("não confere",)),
        (httpx.Response(403, text="<html>WAF SEGREDO-403</html>"), "erro", ("WAF",)),
        (
            httpx.Response(404, json={"erro": "nao_encontrado"}),
            "desligado",
            ("davinci_carrinhos_ativo", "DAVINCI_ESTOQUE_TOKEN"),
        ),
        (
            httpx.Response(429, json={"erro": "muitas_tentativas"}, headers={"Retry-After": "900"}),
            "erro",
            ("muitas_tentativas", "15 min"),
        ),
        (
            httpx.Response(503, json={"erro": "falta_atualizacao_sql"}),
            "erro",
            ("adm_carrinho_eventos",),
        ),
        (httpx.Response(503, json={"erro": "banco_indisponivel"}), "erro", ("banco do site",)),
        (httpx.Response(500, json={"erro": "erro_interno"}), "erro", ("error_log",)),
        (
            httpx.Response(422, json={"erro": "parametro_invalido", "parametro": "desde"}),
            "erro",
            ("HTTP 422", "(desde)"),
        ),
        # Código fora do formato (ou corpo enorme) não entra no texto.
        (
            httpx.Response(422, json={"erro": "parametro_invalido", "parametro": "<b>SEGREDO</b>"}),
            "erro",
            ("HTTP 422",),
        ),
        (httpx.Response(500, text="SEGREDO-500 " * 2000), "erro", ("HTTP 500",)),
    ]
    for resposta, status, trechos in casos:
        site.respostas["charlots"] = resposta
        await rodada(site)
        ch = await _canal(db, "charlots")
        assert ch.status == status, (resposta.status_code, ch.status, ch.ultimo_erro)
        for trecho in trechos:
            assert trecho in (ch.ultimo_erro or ""), (trecho, ch.ultimo_erro)
        assert "SEGREDO" not in (ch.ultimo_erro or "")
    assert "SEGREDO" not in log.texto()


async def test_resumo_explica_o_site_sem_rota_e_desligado(client, db, pessoa, site):
    """A barra de lojas: rota não publicada (HTML 404) e rota desligada no site (JSON 404)."""
    site.respostas["charlots"] = httpx.Response(404, text="<html>pagina</html>")
    site.respostas["uranyx"] = httpx.Response(404, json={"erro": "nao_encontrado"})
    await rodada(site)
    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200, r.text
    lojas = {lj["conta"]: lj for lj in r.json()["lojas"] if lj["plataforma"] == "site"}
    assert lojas["Charlots"]["status_canal"] == "sem_endpoint"
    assert lojas["Charlots"]["status_motivo"].startswith(
        "O site ainda não tem a rota de carrinhos: publicar o pacote"
    )
    assert lojas["Uranyx"]["status_canal"] == "desligado"
    assert "davinci_carrinhos_ativo" in lojas["Uranyx"]["status_motivo"]


# ─────────────── o episódio ───────────────


async def test_carrinho_parado_vira_conversa_com_etiqueta_carrinho(site, db, log):
    site.responde("charlots", carrinhos_=[carrinho()])
    r = await rodada(site)
    assert r["charlots"]["novos"] == 1 and r["uranyx"]["status"] == "ok"
    [c] = await _carrinhos(db)
    assert (c.site, c.situacao, c.quantidade_total) == ("charlots", CARRINHO_ABERTO, 3)
    assert c.parado_desde == PARADO and c.detectado_em == T0
    assert c.lojista["empresa"] == "Loja X" and c.itens[0]["skus"] == ["b1001.pi"]
    conversa = await _conversa(db, c.conversa_id)
    assert (conversa.plataforma, conversa.canal, conversa.externo_id) == (
        "site",
        "carrinho",
        "lojista:123",
    )
    assert (conversa.conta, conversa.comprador_id, conversa.comprador_nome) == (
        "Charlots",
        "123",
        "Loja X",
    )
    assert conversa.etiqueta == ETIQUETA_CARRINHO
    # Entra na fila ("Falta responder"), com o SLA do carrinho (24 h).
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == T0 + timedelta(hours=24)
    [m] = await _mensagens(db, conversa.id)
    assert (m.autor, m.externo_id) == ("cliente", f"carrinho:{c.id}")
    assert "Mala ABS M2 (M2 Preto) × 3" in m.texto and "Charlots" in m.texto
    for pessoal in ("Fulana", "fulana@", "11999998888"):
        assert pessoal not in m.texto
    # A primeira classificação já é o Carrinho (sem "Pré-venda → Carrinho").
    hist = (
        (
            await db.execute(
                select(AtendimentoEtiquetaHistorico).where(
                    AtendimentoEtiquetaHistorico.conversa_id == conversa.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert hist == []
    canal = await _canal(db, "charlots")
    assert canal.status == "ok" and canal.cursor["externo"]["carrinhos_lidos"] == 1

    # A leitura seguinte não duplica nada; o retrato acompanha o site.
    site.responde(
        "charlots", carrinhos_=[carrinho(empresa="Loja X Ltda", itens=[item(quantidade=5)])]
    )
    r = await rodada(site, T0 + timedelta(minutes=30))
    assert (r["charlots"]["novos"], r["charlots"]["atualizados"]) == (0, 1)
    [c] = await _carrinhos(db)
    assert (c.quantidade_total, c.visto_em) == (5, T0 + timedelta(minutes=30))
    conversa = await _conversa(db, c.conversa_id)
    assert conversa.comprador_nome == "Loja X Ltda"
    assert len(await _mensagens(db, conversa.id)) == 1
    assert "Fulana" not in log.texto()


async def test_finalizado_pelo_whatsapp_recupera(site, db):
    site.responde("charlots", carrinhos_=[carrinho()])
    await rodada(site)
    # Um evento de ANTES de parar não fecha nada.
    site.responde("charlots", eventos=[evento(criado=PARADO - timedelta(hours=1))])
    await rodada(site, T0 + timedelta(hours=1))
    [c] = await _carrinhos(db)
    assert c.situacao == CARRINHO_ABERTO
    # O lojista finalizou pelo WhatsApp depois de parar.
    fim = T0 + timedelta(hours=5)
    site.responde("charlots", eventos=[evento(criado=fim, restantes=1, id=988)])
    r = await rodada(site, T0 + timedelta(hours=6))
    assert r["charlots"]["recuperados"] == 1
    [c] = await _carrinhos(db)
    assert (c.situacao, c.motivo_fim, c.recuperado_em) == (
        CARRINHO_RECUPERADO,
        FIM_CARRINHO_FINALIZADO,
        fim,
    )
    assert c.dados["evento_id"] == "988" and c.dados["itens_enviados"][0]["quantidade"] == 3
    conversa = await _conversa(db, c.conversa_id)
    assert conversa.etiqueta == ETIQUETA_POS_VENDA
    assert conversa.aguardando_resposta is False
    msgs = await _mensagens(db, conversa.id)
    assert [m.externo_id for m in msgs] == [f"carrinho:{c.id}", f"carrinho:{c.id}:fim"]
    assert msgs[1].autor == "sistema" and "recuperado" in msgs[1].texto.lower()
    assert "Ainda ficou 1 item no carrinho" in msgs[1].texto
    # O mesmo evento de novo (a janela de eventos é de 30 dias): nada muda.
    r = await rodada(site, T0 + timedelta(hours=7))
    assert r["charlots"]["recuperados"] == 0
    assert len(await _mensagens(db, conversa.id)) == 2


async def test_esvaziado_nao_recupera(site, db):
    site.responde("charlots", carrinhos_=[carrinho()])
    await rodada(site)
    site.responde("charlots", eventos=[evento(tipo="esvaziado", criado=T0 + timedelta(hours=2))])
    await rodada(site, T0 + timedelta(hours=3))
    [c] = await _carrinhos(db)
    assert (c.situacao, c.motivo_fim, c.recuperado_em) == (
        CARRINHO_NAO_RECUPERADO,
        FIM_CARRINHO_ESVAZIADO,
        None,
    )
    conversa = await _conversa(db, c.conversa_id)
    assert (conversa.etiqueta, conversa.aguardando_resposta) == (ETIQUETA_PRE_VENDA, False)
    assert "esvaziou" in (await _mensagens(db, conversa.id))[-1].texto


async def test_prazo_de_7_dias_e_so_reabre_quando_o_lojista_mexe(site, db):
    site.responde("charlots", carrinhos_=[carrinho()])
    await rodada(site)
    # Ainda dentro dos 7 dias.
    await rodada(site, T0 + timedelta(days=6, hours=23))
    assert (await _carrinhos(db))[0].situacao == CARRINHO_ABERTO
    r = await rodada(site, T0 + timedelta(days=7, minutes=1))
    assert r["prazo"]["vencidos"] == 1
    [c] = await _carrinhos(db)
    assert (c.situacao, c.motivo_fim) == (CARRINHO_NAO_RECUPERADO, FIM_CARRINHO_PRAZO)
    conversa = await _conversa(db, c.conversa_id)
    assert (conversa.etiqueta, conversa.aguardando_resposta) == (ETIQUETA_PRE_VENDA, False)
    # O site continua listando o MESMO carrinho: não reabre toda semana.
    r = await rodada(site, T0 + timedelta(days=7, minutes=31))
    assert (r["charlots"]["novos"], r["charlots"]["ignorados"]) == (0, 1)
    assert len(await _carrinhos(db)) == 1
    # O lojista voltou, mexeu e parou de novo: episódio novo, mesma conversa.
    novo_parado = T0 + timedelta(days=8)
    site.responde("charlots", carrinhos_=[carrinho(parado=novo_parado)])
    r = await rodada(site, T0 + timedelta(days=9, hours=1))
    assert r["charlots"]["novos"] == 1
    velho, novo = await _carrinhos(db)
    assert (novo.situacao, novo.conversa_id) == (CARRINHO_ABERTO, velho.conversa_id)
    conversa = await _conversa(db, novo.conversa_id)
    assert (conversa.etiqueta, conversa.aguardando_resposta) == (ETIQUETA_CARRINHO, True)
    assert len(await _mensagens(db, conversa.id)) == 3


async def test_evento_atrasado_corrige_o_prazo(site, db):
    site.responde("charlots", carrinhos_=[carrinho()])
    await rodada(site)
    # O site fica fora do ar e o prazo corre (o prazo é de relógio).
    site.respostas["charlots"] = httpx.Response(503)
    await rodada(site, T0 + timedelta(days=7, minutes=5))
    [c] = await _carrinhos(db)
    assert (c.situacao, c.motivo_fim) == (CARRINHO_NAO_RECUPERADO, FIM_CARRINHO_PRAZO)
    # Voltou, com a finalização do dia 6: foi recuperado, sim.
    site.responde("charlots", eventos=[evento(criado=T0 + timedelta(days=6))])
    r = await rodada(site, T0 + timedelta(days=7, hours=2))
    assert r["charlots"]["corrigidos"] == 1
    [c] = await _carrinhos(db)
    assert (c.situacao, c.motivo_fim) == (CARRINHO_RECUPERADO, FIM_CARRINHO_FINALIZADO)
    conversa = await _conversa(db, c.conversa_id)
    assert conversa.etiqueta == ETIQUETA_POS_VENDA
    ids = [m.externo_id for m in await _mensagens(db, conversa.id)]
    assert f"carrinho:{c.id}:fim:recuperado" in ids
    # Finalização DEPOIS dos 7 dias não corrige (é outra compra).
    site.responde("charlots", carrinhos_=[carrinho(99)])
    await rodada(site, T0 + timedelta(days=8))
    site.respostas["charlots"] = httpx.Response(503)
    await rodada(site, T0 + timedelta(days=15, minutes=5))
    site.responde("charlots", eventos=[evento(99, criado=T0 + timedelta(days=15, minutes=10))])
    await rodada(site, T0 + timedelta(days=15, hours=1))
    [c99] = await _carrinhos(db, "99")
    assert c99.situacao == CARRINHO_NAO_RECUPERADO


async def test_sobra_de_pedido_nao_vira_abandono(site, db):
    # O lojista finalizou parte (o evento é DEPOIS do parado_desde da sobra):
    # o que ficou no carrinho não é abandono novo.
    site.responde(
        "charlots",
        carrinhos_=[carrinho()],
        eventos=[evento(criado=PARADO + timedelta(hours=2), restantes=1)],
    )
    r = await rodada(site)
    assert (r["charlots"]["novos"], r["charlots"]["ignorados"]) == (0, 1)
    assert await _carrinhos(db) == []


async def test_some_da_lista_fica_aberto_com_a_marca(site, db):
    site.responde("charlots", carrinhos_=[carrinho()])
    await rodada(site)
    # Lista truncada não diz nada.
    site.responde("charlots", truncado=True)
    await rodada(site, T0 + timedelta(hours=1))
    [c] = await _carrinhos(db)
    assert "fora_da_lista_desde" not in c.dados
    assert "truncada" in (await _canal(db, "charlots")).ultimo_erro
    # Sumiu sem evento (o lojista voltou a mexer): aberto, com a marca.
    site.responde("charlots")
    r = await rodada(site, T0 + timedelta(hours=2))
    assert r["charlots"]["fora_da_lista"] == 1
    [c] = await _carrinhos(db)
    assert c.situacao == CARRINHO_ABERTO
    assert c.dados["fora_da_lista_desde"].startswith("2026-10-02T14:00")
    # Voltou a aparecer parado (com o parado_desde novo): a marca sai.
    site.responde("charlots", carrinhos_=[carrinho(parado=T0 + timedelta(hours=1))])
    await rodada(site, T0 + timedelta(days=1, hours=3))
    [c] = await _carrinhos(db)
    assert "fora_da_lista_desde" not in c.dados and c.parado_desde == T0 + timedelta(hours=1)


async def test_voltou_a_mexer_pelos_ativos_do_site(client, db, pessoa, site):
    """`ativos` (o site lista quem mexeu há menos de `horas`): não é abandono nem esvaziado."""
    site.responde("charlots", carrinhos_=[carrinho()])
    await rodada(site)
    # Sumiu da lista de parados e está nos ativos: aberto, com as duas marcas.
    mexido = T0 + timedelta(minutes=30)
    site.responde("charlots", ativos=[{"lojista_id": 123, "atualizado_em": _z(mexido)}])
    r = await rodada(site, T0 + timedelta(hours=1))
    assert (r["charlots"]["fora_da_lista"], r["charlots"]["voltou_a_mexer"]) == (1, 0)
    [c] = await _carrinhos(db)
    assert c.situacao == CARRINHO_ABERTO
    assert c.dados["mexido_em"] == mexido.isoformat(timespec="seconds")
    # Mexeu de novo (lista truncada não impede: o ativo é afirmação do site).
    mexido2 = T0 + timedelta(hours=2)
    site.responde(
        "charlots", truncado=True, ativos=[{"lojista_id": 123, "atualizado_em": _z(mexido2)}]
    )
    r = await rodada(site, T0 + timedelta(hours=3))
    assert r["charlots"]["voltou_a_mexer"] == 1
    [c] = await _carrinhos(db)
    assert c.dados["mexido_em"] == mexido2.isoformat(timespec="seconds")
    # O cartão mostra.
    k = (await client.get(f"{URL}/conversas/{c.conversa_id}/carrinho")).json()["carrinho"]
    assert k["mexido_em"].startswith("2026-10-02T14:00") and k["fora_da_lista_desde"]
    # Lixo em `ativos` é ignorado; hora no futuro vira "agora".
    site.responde(
        "charlots",
        ativos=[
            "x",
            {"lojista_id": "a b", "atualizado_em": _z(T0)},
            {"lojista_id": 999, "atualizado_em": "ontem"},
        ],
    )
    r = await rodada(site, T0 + timedelta(hours=4))
    assert r["charlots"]["voltou_a_mexer"] == 0
    # Parou de novo (voltou à lista): as marcas saem, o "parado desde" é o novo.
    site.responde("charlots", carrinhos_=[carrinho(parado=mexido2)])
    await rodada(site, T0 + timedelta(days=1, hours=3))
    [c] = await _carrinhos(db)
    assert "mexido_em" not in c.dados and "fora_da_lista_desde" not in c.dados
    assert c.parado_desde == mexido2


def test_lista_branca_do_que_vem_do_site():
    bruto = carrinho(
        "abc-1",
        senha="nao-guarda",
        email="sem-arroba",
        itens=[
            item(
                imagem="javascript:alert(1)",
                url="http://evil.com/x",
                skus=["B1001.PI", "sku com espaço", "b1001.*", 7, "b1001.pi"],
                preco="-3",
            ),
            item(quantidade=0),
            item(quantidade="2", imagem="http://localhost:8080/foto.webp", preco="10.5"),
        ],
    )
    lido = carrinhos.carrinho_do_site(bruto, T0)
    assert lido is not None and lido.lojista_id == "abc-1"
    assert "senha" not in lido.lojista and lido.lojista["email"] is None
    assert len(lido.itens) == 2
    um, dois = lido.itens
    assert (um["imagem"], um["url"], um["preco"]) == (None, None, None)
    assert um["skus"] == ["b1001.pi", "b1001.*"]
    assert (dois["quantidade"], dois["imagem"], dois["preco"]) == (
        2,
        "http://localhost:8080/foto.webp",
        10.5,
    )
    # Sem lojista, sem data ou sem item: fora.
    assert carrinhos.carrinho_do_site({**bruto, "lojista": {"id": "a b"}}, T0) is None
    assert carrinhos.carrinho_do_site({**bruto, "parado_desde": "ontem"}, T0) is None
    assert carrinhos.carrinho_do_site({**bruto, "itens": []}, T0) is None
    # O relógio adiantado do site não joga o carrinho para o futuro.
    futuro = carrinhos.carrinho_do_site(carrinho(parado=T0 + timedelta(days=1)), T0)
    assert futuro.parado_desde == T0
    assert (
        carrinhos.evento_do_site({"tipo": "apagado", "lojista_id": 1, "criado_em": _z(T0)}) is None
    )
    leitura = carrinhos.ler_resposta(
        json.dumps(
            {"site": "charlots", "carrinhos": [carrinho(), carrinho(), {"x": 1}], "eventos": []}
        ).encode(),
        "charlots",
        T0,
    )
    assert (len(leitura.carrinhos), leitura.descartados) == (1, 2)


# ─────────────── o cartão (GET) e o "Marcar como resolvido" ───────────────


@pytest.fixture
async def pessoa(db, make_user, auth_as):
    u = await make_user(permissions=PODE_TUDO)
    u.name = "Marco Atendente"
    await db.commit()
    auth_as(u)
    return u


async def _produto(db, dono, sku, stock, *, situacao="A", formato="S"):
    db.add(
        Product(
            user_id=dono.id,
            sku=sku,
            name=f"Nome {sku}",
            stock=stock,
            formato=formato,
            situacao=situacao,
        )
    )
    await db.commit()


async def _abrir_um(site: SiteFalso, db, itens: list) -> AtendimentoCarrinho:
    site.responde("charlots", carrinhos_=[carrinho(itens=itens)])
    await rodada(site)
    [c] = await _carrinhos(db)
    return c


async def test_cartao_com_o_estoque_atual_do_davinci(client, db, pessoa, site):
    await _produto(db, pessoa, "b1001.pi", 5)
    # "base.*": a soma dos lotes de VENDA ativos (o .cd e o inativo não entram).
    await _produto(db, pessoa, "b2002.ci", 2)
    await _produto(db, pessoa, "b2002.pi", -1)
    await _produto(db, pessoa, "b2002.sa", 4, situacao="I")
    await _produto(db, pessoa, "b2002.cd", 50)
    await _produto(db, pessoa, "b3003.pi", 0)
    await _produto(db, pessoa, "b3003.ci", 7)
    c = await _abrir_um(
        site,
        db,
        [
            item(skus=["b1001.pi"], quantidade=3, cor="M2|Preto"),
            item(skus=["b1001.pi"], quantidade=4, cor="M2|Preto Fosco", preco=None),
            item(skus=["b2002.*"], quantidade=1, cor="M1|Azul"),
            item(skus=["b3003.pi"], quantidade=1, cor="M3|Rosa"),
            item(skus=["x999.pi"], quantidade=1, cor="M1|Verde"),
            item(skus=[], quantidade=1, cor="M1|Branco"),
        ],
    )
    r = await client.get(f"{URL}/conversas/{c.conversa_id}/carrinho")
    assert r.status_code == 200, r.text
    corpo = r.json()
    k = corpo["carrinho"]
    assert (k["id"], k["situacao"], k["situacao_rotulo"], k["pode_resolver"]) == (
        str(c.id),
        "aberto",
        "Carrinho parado",
        True,
    )
    assert k["lojista"]["email"] == "fulana@lojax.com.br" and k["lojista"]["empresa"] == "Loja X"
    assert (k["site"], k["site_nome"], k["site_url"]) == (
        "charlots",
        "Charlots",
        "https://charlots.com.br",
    )
    assert k["prazo_em"].startswith("2026-10-09T12:00")
    # Um item sem preço: sem total (não inventa).
    assert k["valor_total"] is None
    est = [i["estoque"] for i in k["itens"]]
    # As duas linhas no MESMO SKU somam a demanda (3 + 4 = 7 > 5).
    assert (est[0]["status"], est[0]["disponivel"], est[0]["demanda"]) == ("acima", 5, 7)
    assert est[1]["status"] == "acima"
    # base.*: ci 2 + pi -1 (conta 0) = 2; o .sa inativo e o .cd não entram.
    assert (est[2]["status"], est[2]["disponivel"]) == ("ok", 2)
    assert {s["sku"] for s in est[2]["skus"]} == {"b2002.ci", "b2002.pi"}
    # SKU exato zerado, com peças em outro lote de venda.
    assert (est[3]["status"], est[3]["disponivel"], est[3]["outros_lotes"]) == ("zero", 0, 7)
    assert est[3]["texto"] == "Sem estoque no DaVinci"
    # SKU que o DaVinci não tem: desconhecido, nunca zero.
    assert (est[4]["status"], est[4]["disponivel"]) == ("desconhecido", None)
    assert est[5]["status"] == "sem_mapa"
    assert k["itens_sem_estoque"] == 3
    assert corpo["anteriores"] == []
    assert corpo["taxa"]["detectados"] == 1 and corpo["taxa"]["abertos"] == 1
    assert corpo["taxa"]["taxa"] is None
    assert corpo["leitura"]["status"] == "ok"
    assert "Nada é mandado ao lojista" in corpo["aviso"]


async def test_marcar_como_resolvido(client, db, pessoa, site):
    c = await _abrir_um(site, db, [item()])
    r = await client.post(
        f"{URL}/carrinhos/{c.id}/resolvido", json={"motivo": " liguei,  vai pedir "}
    )
    assert r.status_code == 200, r.text
    k = r.json()["carrinho"]
    assert (k["situacao"], k["motivo_fim"], k["pode_resolver"]) == (
        "resolvido",
        FIM_CARRINHO_RESOLVIDO,
        False,
    )
    assert (k["tratado_por_nome"], k["resolvido_motivo"]) == (
        "Marco Atendente",
        "liguei, vai pedir",
    )
    [c] = await _carrinhos(db)
    assert (c.situacao, c.tratado_por) == (CARRINHO_RESOLVIDO, pessoa.id)
    conversa = await _conversa(db, c.conversa_id)
    assert (conversa.etiqueta, conversa.aguardando_resposta) == (ETIQUETA_PRE_VENDA, False)
    msgs = await _mensagens(db, conversa.id)
    assert msgs[-1].autor == "sistema" and "resolvido" in msgs[-1].texto
    hist = (
        (
            await db.execute(
                select(AtendimentoEtiquetaHistorico).where(
                    AtendimentoEtiquetaHistorico.conversa_id == conversa.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(h.de, h.para) for h in hist] == [(ETIQUETA_CARRINHO, ETIQUETA_PRE_VENDA)]
    # Idempotente.
    r = await client.post(f"{URL}/carrinhos/{c.id}/resolvido", json={})
    assert r.status_code == 200 and len(await _mensagens(db, conversa.id)) == len(msgs)
    # O cartão mostra o resolvido; nenhum carrinho reabre na leitura seguinte.
    r = await rodada(site, T0 + timedelta(minutes=30))
    assert r["charlots"]["ignorados"] == 1
    g = (await client.get(f"{URL}/conversas/{c.conversa_id}/carrinho")).json()
    assert g["carrinho"]["situacao"] == "resolvido" and g["taxa"]["resolvidos"] == 1


async def test_resolvido_recusado_e_404(client, db, pessoa, site):
    c = await _abrir_um(site, db, [item()])
    site.responde("charlots", eventos=[evento(criado=T0 + timedelta(hours=1))])
    await rodada(site, T0 + timedelta(hours=2))
    r = await client.post(f"{URL}/carrinhos/{c.id}/resolvido", json={})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "carrinho_encerrado")
    for torto in ("nao-e-uuid", "00000000-0000-0000-0000-000000000000"):
        r = await client.post(f"{URL}/carrinhos/{torto}/resolvido", json={})
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "carrinho_nao_encontrado")
    # Conversa do site ainda sem carrinho ligado: o cartão vem vazio.
    outra = AtendimentoConversa(
        plataforma="site", canal="carrinho", externo_id="x", conta="Charlots", dados={}
    )
    db.add(outra)
    await db.commit()
    r = await client.get(f"{URL}/conversas/{outra.id}/carrinho")
    assert r.status_code == 200 and r.json()["carrinho"] is None


async def test_permissao_e_escopo_por_equipe(client, db, make_user, auth_as, site):
    c = await _abrir_um(site, db, [item()])
    so_ver = await make_user(permissions=SO_VER)
    auth_as(so_ver)
    assert (await client.get(f"{URL}/conversas/{c.conversa_id}/carrinho")).status_code == 200
    r = await client.post(f"{URL}/carrinhos/{c.id}/resolvido", json={})
    assert r.status_code == 403
    # Quem tem equipe só vê as lojas dela: a conversa do site (sem integração) some.
    de_equipe = await make_user(permissions=PODE_TUDO)
    de_equipe.sales_teams = [1]
    await db.commit()
    auth_as(de_equipe)
    assert (await client.get(f"{URL}/conversas/{c.conversa_id}/carrinho")).status_code == 404
    r = await client.post(f"{URL}/carrinhos/{c.id}/resolvido", json={})
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "carrinho_nao_encontrado")
    [c] = await _carrinhos(db)
    assert c.situacao == CARRINHO_ABERTO
