# ruff: noqa: S105, S106  (tokens de teste, nada real)
"""Comentários e menções das redes no atendimento (RF7, frente B, 02/10/2026).

Tudo com a Graph API de MENTIRA (`httpx.MockTransport`): nenhum teste fala
com a Meta, e o cliente padrão (`redes.novo_cliente`) é trocado por um que
REPROVA qualquer chamada não prevista.

O que se garante:
- a leitura (cron): canal por conta, uma conversa por (pessoa, publicação),
  a resposta da marca como mensagem da loja na conversa certa, a menção
  (IG /tags), a pergunta com prazo curto, a janela de 30 dias, a contagem
  que evita reler, a idempotência e o token DA PÁGINA no Facebook;
- sem o escopo novo: `sem_escopo` claro (o comentário vem SEM autor — como
  medido em produção — e o /tags dá #10), nada gravado, nada quebra, e o
  token nunca aparece no erro;
- o cron desligado por padrão e a trava;
- o painel da publicação (GET) e as ações (responder em público, no
  Direct, ocultar): BLOQUEADAS com 409 `envio_desligado` antes de tudo; com
  o envio ligado (só em teste), o caminho inteiro com HTTP falso.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoComentario,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPublicacao,
    User,
)
from app.models.marca import Marca, RedeSocial
from app.models.marketing_postagem import RedeSocialToken
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import redes
from app.services.atendimento.constantes import (
    CHAVE_PRAZO_PLATAFORMA,
    CONVERSA_ABERTA,
    CONVERSA_RESPONDIDA,
    ETIQUETA_MIDIA,
)

URL = "/api/atendimento"
T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
IG = "17841400000000001"
PAGINA = "1390000000000001"
IG_7B = "17841400000000777"
TOKEN_SISTEMA = "EAAsistemaIG000000000000000000000000"
TOKEN_PAGINA_IG = "EAApaginaIG0000000000000000000000000"
TOKEN_SISTEMA_FB = "EAAsistemaFB000000000000000000000000"
TOKEN_PAGINA_FB = "EAApaginaFB0000000000000000000000000"


def _ts(quando: datetime) -> str:
    """O formato da Graph: '2026-09-28T12:00:00+0000'."""
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


# ─────────────── a Graph de mentira ───────────────


class Graph:
    """Responde por (método, caminho sem a versão); anota cada chamada."""

    def __init__(self) -> None:
        self.rotas: dict[tuple[str, str], Any] = {}
        self.chamadas: list[dict] = []
        # O serviço engole exceção de rede (vira "a Meta não respondeu"): a
        # chamada não prevista fica anotada e o fixture reprova no fim.
        self.inesperadas: list[str] = []

    def quando(self, metodo: str, caminho: str, resposta: Any) -> None:
        self.rotas[(metodo, caminho)] = resposta

    def chamadas_de(self, metodo: str, caminho: str) -> list[dict]:
        return [c for c in self.chamadas if c["metodo"] == metodo and c["caminho"] == caminho]

    def handler(self, request: httpx.Request) -> httpx.Response:
        partes = request.url.path.strip("/").split("/", 1)
        caminho = partes[1] if len(partes) > 1 else ""
        corpo = request.content.decode() if request.content else ""
        self.chamadas.append(
            {
                "metodo": request.method,
                "caminho": caminho,
                "params": dict(request.url.params),
                "auth": request.headers.get("authorization"),
                "corpo": corpo,
            }
        )
        resposta = self.rotas.get((request.method, caminho))
        if resposta is None:
            self.inesperadas.append(f"{request.method} {caminho}")
            raise AssertionError(f"chamada não prevista: {request.method} {caminho}")
        if isinstance(resposta, Exception):
            raise resposta
        if callable(resposta):
            resposta = resposta(request)
        if isinstance(resposta, httpx.Response):
            return resposta
        status, json = resposta if isinstance(resposta, tuple) else (200, resposta)
        return httpx.Response(status, json=json)

    def cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def erro_meta(code: int, msg: str, subcode: int | None = None, http: int = 400):
    e: dict[str, Any] = {"message": msg, "type": "OAuthException", "code": code}
    if subcode is not None:
        e["error_subcode"] = subcode
    return (http, {"error": e})


class RedisFalso:
    def __init__(self) -> None:
        self.dados: dict[str, Any] = {}

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


@pytest.fixture
def graph(monkeypatch):
    g = Graph()
    monkeypatch.setattr(redes, "novo_cliente", g.cliente)
    yield g
    assert g.inesperadas == [], g.inesperadas


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    monkeypatch.setattr(redes, "_agora", lambda: T0)
    monkeypatch.setattr(redes, "redis", RedisFalso())
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_envio_ativo", False)
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_redes_ativa", True)
    # Nada sai para a rede de verdade, nem por engano (o `graph` troca este).
    monkeypatch.setattr(redes, "novo_cliente", Graph().cliente)


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


async def _marca(db: AsyncSession, nome: str, slug: str) -> Marca:
    m = Marca(nome=nome, slug=slug)
    db.add(m)
    await db.flush()
    return m


async def _conta(
    db: AsyncSession,
    marca: Marca,
    plataforma: str,
    conta: str,
    *,
    externo: str | None,
    segredos: dict | None,
) -> RedeSocial:
    r = RedeSocial(marca_id=marca.id, plataforma=plataforma, conta=conta)
    db.add(r)
    await db.flush()
    if externo is not None:
        db.add(
            RedeSocialToken(
                rede_social_id=r.id,
                external_user_id=externo,
                external_username=conta,
                provedor="facebook",
                token_enc=encrypt_json(segredos) if segredos else None,
            )
        )
    await db.flush()
    return r


@pytest.fixture
async def contas(db: AsyncSession) -> dict[str, RedeSocial]:
    """A Charlots (slug "poofy", como em produção) com IG e Página; a 7buyers (fora)."""
    charlots = await _marca(db, "charlots", "poofy")
    sete = await _marca(db, "7buyers", "7buyers")
    out = {
        "ig": await _conta(
            db,
            charlots,
            "instagram",
            "charlots_br",
            externo=IG,
            segredos={"access_token": TOKEN_SISTEMA, "page_access_token": TOKEN_PAGINA_IG},
        ),
        "fb": await _conta(
            db,
            charlots,
            "facebook",
            "Charlots Brasil",
            externo=PAGINA,
            segredos={"access_token": TOKEN_SISTEMA_FB, "page_access_token": TOKEN_PAGINA_FB},
        ),
        "7b": await _conta(
            db,
            sete,
            "instagram",
            "7buyers_br",
            externo=IG_7B,
            segredos={"access_token": "EAA7buyers00000000000000000000"},
        ),
    }
    await db.commit()
    return out


# ─────────────── os dados da rede ───────────────

MIDIA = "18000000000000001"
MIDIA_SEM = "18000000000000002"
MENCAO = "18000000000000099"
POST = f"{PAGINA}_555"


def _midias(comentarios: int = 3) -> dict:
    return {
        "data": [
            {
                "id": MIDIA,
                "caption": "Mala nova",
                "media_type": "VIDEO",
                "media_product_type": "REELS",
                "thumbnail_url": "https://cdn.exemplo/capa.jpg",
                "media_url": "https://cdn.exemplo/video.mp4",
                "permalink": "https://www.instagram.com/reel/abc/",
                "timestamp": _ts(T0 - timedelta(days=4)),
                "username": "charlots_br",
                "like_count": 40,
                "comments_count": comentarios,
            },
            {
                "id": MIDIA_SEM,
                "media_type": "IMAGE",
                "media_product_type": "FEED",
                "media_url": "https://cdn.exemplo/foto.jpg",
                "timestamp": _ts(T0 - timedelta(days=2)),
                "comments_count": 0,
            },
        ]
    }


def _comentarios_ig(*, com_autor: bool = True, extra: list[dict] | None = None) -> dict:
    def autor(id_, user):
        return {"username": user, "from": {"id": id_, "username": user}} if com_autor else {}

    return {
        "data": [
            {
                "id": "c-ana",
                "text": "Qual o preço?",
                "timestamp": _ts(T0 - timedelta(hours=10)),
                "like_count": 1,
                "hidden": False,
                **autor("p-ana", "ana"),
                "replies": {
                    "data": [
                        {
                            "id": "r-marca",
                            "text": "Oi Ana! R$ 199 no site.",
                            "timestamp": _ts(T0 - timedelta(hours=9)),
                            "parent_id": "c-ana",
                            **autor(IG, "charlots_br"),
                        }
                    ]
                },
            },
            {
                "id": "c-bia",
                "text": "tem no azul",
                "timestamp": _ts(T0 - timedelta(hours=2)),
                "hidden": False,
                **autor("p-bia", "bia"),
            },
            *(extra or []),
        ]
    }


def _tags() -> dict:
    return {
        "data": [
            {
                "id": MENCAO,
                "caption": "Amei minha mala @charlots_br",
                "media_type": "IMAGE",
                "media_product_type": "FEED",
                "media_url": "https://cdn.exemplo/mencao.jpg",
                "permalink": "https://www.instagram.com/p/xyz/",
                "timestamp": _ts(T0 - timedelta(days=1)),
                "username": "carla",
                "comments_count": 0,
            }
        ]
    }


def _posts() -> dict:
    return {
        "data": [
            {
                "id": POST,
                "created_time": _ts(T0 - timedelta(days=3)),
                "permalink_url": "https://www.facebook.com/charlots/posts/555",
                "full_picture": "https://cdn.exemplo/post.jpg",
                "status_type": "added_photos",
                "message": "Promoção",
                "comments": {"data": [], "summary": {"total_count": 2}},
                "reactions": {"data": [], "summary": {"total_count": 7}},
            }
        ]
    }


def _comentarios_fb(*, com_autor: bool = True) -> dict:
    return {
        "data": [
            {
                "id": f"{POST}_c1",
                "created_time": _ts(T0 - timedelta(hours=5)),
                "message": "Vocês entregam em BH?",
                **({"from": {"id": "fb-caio", "name": "Caio Souza"}} if com_autor else {}),
                "like_count": 0,
                "is_hidden": False,
            },
            {
                "id": f"{POST}_c2",
                "created_time": _ts(T0 - timedelta(hours=4)),
                "message": "Entregamos sim!",
                **({"from": {"id": PAGINA, "name": "Charlots Brasil"}} if com_autor else {}),
                "parent": {"id": f"{POST}_c1"},
                "is_hidden": False,
            },
        ]
    }


def _graph_tudo_ok(g: Graph) -> None:
    g.quando("GET", f"{IG}/media", _midias())
    g.quando("GET", f"{MIDIA}/comments", _comentarios_ig())
    g.quando("GET", f"{IG}/tags", _tags())
    g.quando("GET", f"{PAGINA}/posts", _posts())
    g.quando("GET", f"{POST}/comments", _comentarios_fb())


async def _conversas(db: AsyncSession) -> dict[str, AtendimentoConversa]:
    linhas = (await db.execute(select(AtendimentoConversa))).scalars().all()
    for c in linhas:
        await db.refresh(c)
    return {c.externo_id: c for c in linhas}


async def _mensagens(db: AsyncSession, conversa: AtendimentoConversa) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa.id)
                .order_by(AtendimentoMensagem.enviada_em)
            )
        )
        .scalars()
        .all()
    )


async def _canal(db: AsyncSession, ref: str) -> AtendimentoCanal:
    c = (
        await db.execute(select(AtendimentoCanal).where(AtendimentoCanal.externo_ref == ref))
    ).scalar_one()
    await db.refresh(c)
    return c


# ─────────────── puros ───────────────


def test_puros_datas_origem_e_autor():
    assert redes._data("2026-09-28T12:00:00+0000") == datetime(2026, 9, 28, 12, tzinfo=UTC)
    assert redes._data("2026-09-28T12:00:00Z") == datetime(2026, 9, 28, 12, tzinfo=UTC)
    assert redes._data("torto") is None and redes._data(None) is None
    pub = AtendimentoPublicacao(
        formato="REELS", tipo="propria", publicada_em=datetime(2026, 9, 28, 14, tzinfo=UTC)
    )
    assert redes.origem_da_publicacao(pub) == "Reels 28/09"
    pub.tipo, pub.formato = "mencao", "IMAGE"
    assert redes.origem_da_publicacao(pub) == "menção · Foto 28/09"
    conta = redes.ContaRede(
        rede_social_id=None, marca="charlots", plataforma="instagram", conta_id=IG,
        nome="@charlots_br", username="charlots_br", token="segredo",
    )  # fmt: skip
    assert "segredo" not in repr(conta)
    cs = redes.comentarios_ig(_comentarios_ig()["data"], conta)
    assert [(c.externo_id, c.pai, c.da_marca) for c in cs] == [
        ("c-ana", None, False),
        ("r-marca", "c-ana", True),
        ("c-bia", None, False),
    ]
    assert not redes._comentarios_sem_escopo(cs)
    sem = redes.comentarios_ig(_comentarios_ig(com_autor=False)["data"], conta)
    assert redes._comentarios_sem_escopo(sem)
    assert redes.pessoa_de(None, "ana") == "@ana" and redes.pessoa_de("1", "ana") == "1"


@pytest.mark.parametrize(
    ("code", "msg", "permissao", "token", "passageiro"),
    [
        (10, "(#10) Application does not have permission for this action", True, False, False),
        (100, "(#100) Missing Permission", True, False, False),
        (200, "Permissions error", True, False, False),
        (190, "Invalid OAuth 2.0 Access Token", False, True, False),
        (4, "Application request limit reached", False, False, True),
        (100, "Unsupported get request", False, False, False),
    ],
)
def test_classificacao_da_recusa(code, msg, permissao, token, passageiro):
    r = redes.Resposta(erro=f"{msg} (code {code})", code=code, http=400)
    assert (r.sem_permissao, r.token_recusado, r.passageiro) == (permissao, token, passageiro)


# ─────────────── a leitura ───────────────


async def test_rodada_cria_conversas_por_pessoa_e_publicacao(db, graph, contas):
    _graph_tudo_ok(graph)
    resumo = await redes.atendimento_redes(None)
    assert resumo is not None and resumo["contas"] == 2  # a 7buyers fica fora

    # O canal de cada conta (a 7buyers não é lida: nenhum GET com o id dela).
    ig = await _canal(db, f"rede:instagram:{IG}")
    fb = await _canal(db, f"rede:facebook:{PAGINA}")
    assert (ig.status, ig.ultimo_erro, ig.rede_social_id, ig.modo) == (
        "ok",
        None,
        contas["ig"].id,
        "observar",
    )
    assert ig.cursor["externo"]["nome"] == "@charlots_br"
    assert (fb.status, fb.cursor["externo"]["nome"]) == ("ok", "Charlots Brasil")
    assert not [c for c in graph.chamadas if IG_7B in c["caminho"]]
    # Só a publicação com comentário teve os comentários lidos; o token vai no
    # CABEÇALHO, nunca na URL.
    assert len(graph.chamadas_de("GET", f"{MIDIA}/comments")) == 1
    assert not graph.chamadas_de("GET", f"{MIDIA_SEM}/comments")
    assert all("access_token" not in c["params"] for c in graph.chamadas)
    assert graph.chamadas_de("GET", f"{IG}/media")[0]["auth"] == f"OAuth {TOKEN_SISTEMA}"
    # Facebook: posts e comentários com o token DA PÁGINA (o do sistema dá 190).
    assert {c["auth"] for c in graph.chamadas if PAGINA in c["caminho"]} == {
        f"OAuth {TOKEN_PAGINA_FB}"
    }
    assert graph.chamadas_de("GET", f"{POST}/comments")[0]["params"]["filter"] == "stream"

    conversas = await _conversas(db)
    assert set(conversas) == {
        f"{MIDIA}:p-ana",
        f"{MIDIA}:p-bia",
        f"{MENCAO}:@carla",
        f"{POST}:fb-caio",
    }
    ana = conversas[f"{MIDIA}:p-ana"]
    assert (ana.plataforma, ana.canal, ana.comprador_nome, ana.conta) == (
        "instagram",
        "comentario",
        "@ana",
        "@charlots_br",
    )
    assert (ana.anuncio_id, ana.anuncio_titulo, ana.etiqueta) == (
        MIDIA,
        "Reels 28/09",
        ETIQUETA_MIDIA,
    )
    # A marca respondeu a Ana: a resposta é mensagem da LOJA na conversa dela.
    msgs = await _mensagens(db, ana)
    assert [(m.autor, m.externo_id, m.origem) for m in msgs] == [
        ("cliente", "c-ana", "cliente"),
        ("loja", "r-marca", "externo"),
    ]
    assert (ana.situacao, ana.aguardando_resposta) == (CONVERSA_RESPONDIDA, False)
    assert ana.dados["eh_pergunta"] is False and ana.dados[CHAVE_PRAZO_PLATAFORMA] is None
    # A Bia perguntou ("tem no azul") e ninguém respondeu: prazo curto (4 h).
    bia = conversas[f"{MIDIA}:p-bia"]
    assert (bia.situacao, bia.aguardando_resposta) == (CONVERSA_ABERTA, True)
    assert bia.dados["eh_pergunta"] is True
    assert bia.prazo_resposta_em == T0 - timedelta(hours=2) + timedelta(hours=4)
    # A menção: conversa com a pessoa que marcou, a legenda como mensagem.
    carla = conversas[f"{MENCAO}:@carla"]
    assert (carla.dados["tipo"], carla.anuncio_titulo, carla.etiqueta) == (
        "mencao",
        "menção · Foto 01/10",
        ETIQUETA_MIDIA,
    )
    assert [m.externo_id for m in await _mensagens(db, carla)] == [f"mencao:{IG}:{MENCAO}"]
    # Não é pergunta: nasce "não precisa de resposta" (a resposta da marca no
    # post de outra pessoa nunca volta — ficaria esperando para sempre).
    assert (carla.sem_resposta_necessaria, carla.aguardando_resposta) == (True, False)
    # Facebook: a resposta da Página fica na conversa do Caio.
    caio = conversas[f"{POST}:fb-caio"]
    assert (caio.comprador_nome, caio.anuncio_titulo, caio.situacao) == (
        "Caio Souza",
        "Foto 29/09",
        CONVERSA_RESPONDIDA,
    )
    assert [m.autor for m in await _mensagens(db, caio)] == ["cliente", "loja"]
    # A publicação: retrato e contagem (a mídia sem comentário não é guardada).
    pubs = {p.externo_id: p for p in (await db.execute(select(AtendimentoPublicacao))).scalars()}
    assert set(pubs) == {MIDIA, MENCAO, POST}
    assert (pubs[MIDIA].formato, pubs[MIDIA].comentarios, pubs[MIDIA].miniatura_url) == (
        "REELS",
        3,
        "https://cdn.exemplo/capa.jpg",
    )
    assert (pubs[MENCAO].tipo, pubs[MENCAO].autor_username) == ("mencao", "carla")
    assert (pubs[POST].curtidas, pubs[POST].formato) == (7, "IMAGE")
    marca = (
        await db.execute(
            select(AtendimentoComentario).where(AtendimentoComentario.externo_id == "r-marca")
        )
    ).scalar_one()
    assert (marca.da_marca, marca.pai_externo_id, marca.conversa_id) == (True, "c-ana", ana.id)

    # Segunda rodada: a contagem não mudou → não relê os comentários; nada duplica.
    antes = await db.scalar(select(func.count(AtendimentoMensagem.id)))
    await redes.atendimento_redes(None)
    assert len(graph.chamadas_de("GET", f"{MIDIA}/comments")) == 1
    assert await db.scalar(select(func.count(AtendimentoMensagem.id))) == antes
    assert await db.scalar(select(func.count(AtendimentoConversa.id))) == 4


async def test_resposta_da_marca_com_arroba_vai_para_quem_foi_citado(db, graph, contas):
    """No fio da Ana, a Duda respondeu e a marca escreveu "@duda …": é da Duda."""
    dados = _comentarios_ig()
    dados["data"][0]["replies"]["data"] = [
        {
            "id": "r-duda",
            "text": "Também quero saber",
            "timestamp": _ts(T0 - timedelta(hours=8)),
            "parent_id": "c-ana",
            "username": "duda",
            "from": {"id": "p-duda", "username": "duda"},
        },
        {
            "id": "r-marca-duda",
            "text": "@duda é R$ 199!",
            "timestamp": _ts(T0 - timedelta(hours=7)),
            "parent_id": "c-ana",
            "username": "charlots_br",
            "from": {"id": IG, "username": "charlots_br"},
        },
    ]
    graph.quando("GET", f"{IG}/media", _midias())
    graph.quando("GET", f"{MIDIA}/comments", dados)
    graph.quando("GET", f"{IG}/tags", {"data": []})
    graph.quando("GET", f"{PAGINA}/posts", {"data": []})
    await redes.atendimento_redes(None)
    conversas = await _conversas(db)
    duda = conversas[f"{MIDIA}:p-duda"]
    assert [m.externo_id for m in await _mensagens(db, duda)] == ["r-duda", "r-marca-duda"]
    ana = conversas[f"{MIDIA}:p-ana"]
    assert [m.autor for m in await _mensagens(db, ana)] == ["cliente"]
    assert ana.aguardando_resposta is True


async def test_comentario_velho_fica_so_no_cartao(db, graph, contas):
    velho = {
        "id": "c-velho",
        "text": "Linda",
        "timestamp": _ts(T0 - timedelta(days=45)),
        "username": "zeca",
        "from": {"id": "p-zeca", "username": "zeca"},
    }
    graph.quando("GET", f"{IG}/media", _midias(comentarios=4))
    graph.quando("GET", f"{MIDIA}/comments", _comentarios_ig(extra=[velho]))
    graph.quando("GET", f"{IG}/tags", {"data": []})
    graph.quando("GET", f"{PAGINA}/posts", {"data": []})
    await redes.atendimento_redes(None)
    linha = (
        await db.execute(
            select(AtendimentoComentario).where(AtendimentoComentario.externo_id == "c-velho")
        )
    ).scalar_one()
    assert linha.conversa_id is None
    assert f"{MIDIA}:p-zeca" not in await _conversas(db)


async def test_sem_escopo_comentario_sem_autor_e_tags_negado(client, db, graph, contas, pessoa):
    """O que o token de HOJE faz (medido em 02/10): texto sem autor e /tags #10."""
    graph.quando("GET", f"{IG}/media", _midias())
    graph.quando("GET", f"{MIDIA}/comments", _comentarios_ig(com_autor=False))
    graph.quando(
        "GET",
        f"{IG}/tags",
        erro_meta(10, "(#10) Application does not have permission for this action"),
    )
    # A Meta às vezes ecoa o token na mensagem: ele não pode ir para a tela.
    graph.quando(
        "GET", f"{PAGINA}/posts", erro_meta(10, f"(#10) no permission for {TOKEN_PAGINA_FB}")
    )
    resumo = await redes.atendimento_redes(None)
    assert resumo is not None
    ig = await _canal(db, f"rede:instagram:{IG}")
    fb = await _canal(db, f"rede:facebook:{PAGINA}")
    assert ig.status == fb.status == "sem_escopo"
    assert "instagram_manage_comments" in ig.ultimo_erro
    assert "sem o autor" in ig.ultimo_erro and "/tags" in ig.ultimo_erro
    assert TOKEN_PAGINA_FB not in fb.ultimo_erro and "***" in fb.ultimo_erro
    # Nada gravado de pessoa: sem conversa, sem comentário; a publicação fica
    # sem a contagem (relê quando o token novo chegar).
    assert await db.scalar(select(func.count(AtendimentoConversa.id))) == 0
    assert await db.scalar(select(func.count(AtendimentoComentario.id))) == 0
    pub = (await db.execute(select(AtendimentoPublicacao))).scalar_one()
    assert pub.comentarios is None
    # A barra de lojas explica o porquê.
    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200, r.text
    canais = {c["externo_ref"]: c for c in r.json()["canais"] if c["externo_ref"]}
    assert canais[f"rede:instagram:{IG}"]["status"] == "sem_escopo"
    loja = next(lj for lj in r.json()["lojas"] if lj["conta"] == "@charlots_br")
    assert "Sem permissão no token do DaVinci Publicador" in (loja["status_motivo"] or "")


async def test_comentario_sem_autor_so_em_parte(db, graph, contas):
    """Revisão 02/10: a marca com autor e as pessoas sem = falta o escopo (nada
    gravado); só ALGUNS sem autor = o resto entra, mas o canal avisa (não
    some calado com "ok") e a publicação fica sem a contagem, para ser relida."""
    # 1) Só a resposta da marca vem com o autor (o token de hoje).
    dados = _comentarios_ig(com_autor=False)
    dados["data"][0]["replies"]["data"][0].update(
        {"username": "charlots_br", "from": {"id": IG, "username": "charlots_br"}}
    )
    graph.quando("GET", f"{IG}/media", _midias(3))
    graph.quando("GET", f"{MIDIA}/comments", dados)
    graph.quando("GET", f"{IG}/tags", {"data": []})
    graph.quando("GET", f"{PAGINA}/posts", {"data": []})
    await redes.atendimento_redes(None)
    ig = await _canal(db, f"rede:instagram:{IG}")
    assert ig.status == "sem_escopo" and "sem o autor" in ig.ultimo_erro
    assert await db.scalar(select(func.count(AtendimentoConversa.id))) == 0
    assert await db.scalar(select(func.count(AtendimentoComentario.id))) == 0
    # 2) Só a Bia sem o autor: a Ana (e a resposta da marca) entram; a Bia
    # não some calada.
    dados = _comentarios_ig()
    for chave in ("username", "from"):
        dados["data"][1].pop(chave)
    graph.quando("GET", f"{MIDIA}/comments", dados)
    await redes.atendimento_redes(None)
    ig = await _canal(db, f"rede:instagram:{IG}")
    assert ig.status == "sem_escopo"
    assert "1 comentário(s) vieram sem o autor" in ig.ultimo_erro
    assert "instagram_manage_comments" in ig.ultimo_erro
    assert set(await _conversas(db)) == {f"{MIDIA}:p-ana"}
    assert ig.cursor["externo"]["leitura"]["sem_autor"] == 1
    pub = (
        await db.execute(
            select(AtendimentoPublicacao).where(AtendimentoPublicacao.externo_id == MIDIA)
        )
    ).scalar_one()
    await db.refresh(pub)
    assert pub.comentarios is None  # relida na rodada seguinte
    leituras = len(graph.chamadas_de("GET", f"{MIDIA}/comments"))
    # 3) O autor chegou (o token novo): a Bia entra e o canal volta a "ok".
    graph.quando("GET", f"{MIDIA}/comments", _comentarios_ig())
    await redes.atendimento_redes(None)
    assert len(graph.chamadas_de("GET", f"{MIDIA}/comments")) == leituras + 1
    ig = await _canal(db, f"rede:instagram:{IG}")
    assert (ig.status, ig.ultimo_erro) == ("ok", None)
    assert set(await _conversas(db)) == {f"{MIDIA}:p-ana", f"{MIDIA}:p-bia"}
    await db.refresh(pub)
    assert pub.comentarios == 3


async def test_facebook_comentario_sem_from_vira_sem_escopo(db, graph, contas):
    """Sem `pages_read_user_content` a Página manda o comentário sem `from`."""
    graph.quando("GET", f"{IG}/media", {"data": []})
    graph.quando("GET", f"{IG}/tags", {"data": []})
    graph.quando("GET", f"{PAGINA}/posts", _posts())
    graph.quando("GET", f"{POST}/comments", _comentarios_fb(com_autor=False))
    await redes.atendimento_redes(None)
    fb = await _canal(db, f"rede:facebook:{PAGINA}")
    assert fb.status == "sem_escopo"
    assert "sem o autor" in fb.ultimo_erro and "pages_read_user_content" in fb.ultimo_erro
    assert (await _canal(db, f"rede:instagram:{IG}")).status == "ok"
    assert await db.scalar(select(func.count(AtendimentoConversa.id))) == 0
    assert await db.scalar(select(func.count(AtendimentoComentario.id))) == 0
    pub = (await db.execute(select(AtendimentoPublicacao))).scalar_one()
    assert pub.comentarios is None


async def test_mencao_nao_fica_esperando_para_sempre(db, graph, contas):
    """A resposta da marca à menção (no post de OUTRA pessoa) nunca volta: a
    menção comum nasce "não precisa de resposta"; a que é pergunta fica na
    fila, com o prazo curto, até alguém resolver."""
    tags = _tags()
    tags["data"].append(
        {
            **tags["data"][0],
            "id": "18000000000000098",
            "caption": "Onde compro essa mala? @charlots_br",
            "username": "duda",
            "timestamp": _ts(T0 - timedelta(hours=3)),
        }
    )
    graph.quando("GET", f"{IG}/media", {"data": []})
    graph.quando("GET", f"{IG}/tags", tags)
    graph.quando("GET", f"{PAGINA}/posts", {"data": []})
    for _ in range(2):
        await redes.atendimento_redes(None)
    conversas = await _conversas(db)
    carla = conversas[f"{MENCAO}:@carla"]
    assert (carla.sem_resposta_necessaria, carla.aguardando_resposta) == (True, False)
    assert (carla.prazo_resposta_em, carla.situacao, carla.etiqueta) == (
        None,
        CONVERSA_ABERTA,
        ETIQUETA_MIDIA,
    )
    duda = conversas["18000000000000098:@duda"]
    assert (duda.sem_resposta_necessaria, duda.aguardando_resposta) == (False, True)
    assert duda.dados["eh_pergunta"] is True
    assert duda.prazo_resposta_em == T0 - timedelta(hours=3) + timedelta(hours=4)
    # Nada é lido do post de outra pessoa (a Meta não deixa).
    assert not [c for c in graph.chamadas if c["caminho"].endswith("/comments")]
    # Quem desfaz à mão ("precisa de resposta") não é desfeito pela leitura seguinte.
    carla.sem_resposta_necessaria = False
    await db.commit()
    await redes.atendimento_redes(None)
    carla = (await _conversas(db))[f"{MENCAO}:@carla"]
    assert (carla.sem_resposta_necessaria, carla.aguardando_resposta) == (False, True)


async def test_token_recusado_e_rede_fora_nao_quebram(db, graph, contas):
    graph.quando("GET", f"{IG}/media", erro_meta(190, "Invalid OAuth 2.0 Access Token", 2069032))
    graph.quando("GET", f"{PAGINA}/posts", httpx.ConnectError("caiu"))
    resumo = await redes.atendimento_redes(None)
    assert resumo is not None and resumo["contas"] == 2  # a 7buyers fica fora
    ig = await _canal(db, f"rede:instagram:{IG}")
    fb = await _canal(db, f"rede:facebook:{PAGINA}")
    assert ig.status == "erro" and "Cadastros › Redes Sociais" in ig.ultimo_erro
    assert fb.status == "erro" and "não respondeu" in fb.ultimo_erro
    assert ig.ultimo_ok_em is None


async def test_conta_sem_token_e_pagina_sem_token_de_pagina(db, graph):
    m = await _marca(db, "uranyx", "uranyx")
    await _conta(db, m, "instagram", "uranyx_br", externo="17841499", segredos=None)
    await _conta(
        db, m, "facebook", "Uranyx", externo="1374", segredos={"access_token": TOKEN_SISTEMA_FB}
    )
    await db.commit()
    # Sem o token da Página no blob: pede (GET) com o do sistema.
    graph.quando("GET", "1374", {"access_token": TOKEN_PAGINA_FB, "id": "1374"})
    graph.quando("GET", "1374/posts", {"data": []})
    await redes.atendimento_redes(None)
    ig = await _canal(db, "rede:instagram:17841499")
    fb = await _canal(db, "rede:facebook:1374")
    assert ig.status == "erro" and "sem token" in ig.ultimo_erro
    assert fb.status == "ok"
    assert graph.chamadas_de("GET", "1374")[0]["auth"] == f"OAuth {TOKEN_SISTEMA_FB}"
    assert graph.chamadas_de("GET", "1374/posts")[0]["auth"] == f"OAuth {TOKEN_PAGINA_FB}"


async def test_cron_desligado_por_padrao_e_trava(monkeypatch, graph, contas):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_redes_ativa", False)
    assert await redes.atendimento_redes(None) is None
    monkeypatch.setattr(s, "atendimento_redes_ativa", True)
    monkeypatch.setattr(s, "atendimento_leitura_ativa", False)
    assert await redes.atendimento_redes(None) is None
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    await redes.redis.set(redes.CHAVE_TRAVA, "outra", nx=True)
    assert await redes.atendimento_redes(None) == {"pulado": "ocupado"}
    assert graph.chamadas == []
    # O padrão do código (sem .env) nasce desligado.
    from app.config import Settings

    assert Settings.model_fields["atendimento_redes_ativa"].default is False
    assert Settings.model_fields["atendimento_envio_ativo"].default is False


# ─────────────── o painel e as ações ───────────────


async def _lido(db, graph) -> dict[str, AtendimentoConversa]:
    _graph_tudo_ok(graph)
    await redes.atendimento_redes(None)
    return await _conversas(db)


async def _comentario(db, externo_id: str) -> AtendimentoComentario:
    c = (
        await db.execute(
            select(AtendimentoComentario).where(AtendimentoComentario.externo_id == externo_id)
        )
    ).scalar_one()
    await db.refresh(c)
    return c


async def _modo(db, ref: str, modo: str) -> None:
    canal = await _canal(db, ref)
    canal.modo = modo
    await db.commit()


async def test_painel_da_publicacao(client, db, graph, contas, pessoa):
    conversas = await _lido(db, graph)
    bia = conversas[f"{MIDIA}:p-bia"]
    r = await client.get(f"{URL}/conversas/{bia.id}/publicacao")
    assert r.status_code == 200, r.text
    d = r.json()
    pub = d["publicacao"]
    assert (pub["origem"], pub["link"], pub["comentarios"], pub["miniatura_vencida"]) == (
        "Reels 28/09",
        "https://www.instagram.com/reel/abc/",
        3,
        False,
    )
    # Em fio, os mais recentes primeiro; a resposta da marca aninhada.
    assert [c["externo_id"] for c in d["comentarios"]] == ["c-bia", "c-ana"]
    ana = d["comentarios"][1]
    assert [r_["externo_id"] for r_ in ana["respostas"]] == ["r-marca"]
    assert ana["respondido"] is True and ana["desta_conversa"] is False
    bia_c = d["comentarios"][0]
    assert (bia_c["desta_conversa"], bia_c["respondido"], bia_c["eh_pergunta"]) == (
        True,
        False,
        True,
    )
    # Envio desligado: tudo bloqueado, com o porquê.
    assert d["envio"]["ativo"] is False and "ATENDIMENTO_ENVIO_ATIVO" in d["envio"]["motivo"]
    assert bia_c["pode_responder"] is False and "desligado" in bia_c["motivo_responder"]
    assert d["envio"]["limite_publico"] == 2200 and d["canal_status"] == "ok"
    # O Direct não tem publicação; nem a conversa de outro canal.
    assert (await client.get(f"{URL}/conversas/ig:{bia.id}/publicacao")).status_code == 404
    r = await client.get(f"{URL}/conversas/00000000-0000-0000-0000-000000000000/publicacao")
    assert r.status_code == 404


async def test_miniatura_vencida_e_renovada(client, db, graph, contas, pessoa, monkeypatch):
    conversas = await _lido(db, graph)
    # A API não vê o ATENDIMENTO_REDES_ATIVA ligado só no worker (o
    # get_settings é lido na subida): a renovação depende só da leitura.
    monkeypatch.setattr(get_settings(), "atendimento_redes_ativa", False)
    pub = (
        await db.execute(
            select(AtendimentoPublicacao).where(AtendimentoPublicacao.externo_id == MIDIA)
        )
    ).scalar_one()
    pub.miniatura_lida_em = T0 - timedelta(hours=7)
    await db.commit()
    graph.quando("GET", MIDIA, {"thumbnail_url": "https://cdn.exemplo/capa-nova.jpg", "id": MIDIA})
    r = await client.get(f"{URL}/conversas/{conversas[f'{MIDIA}:p-bia'].id}/publicacao")
    assert r.json()["publicacao"]["miniatura_url"] == "https://cdn.exemplo/capa-nova.jpg"
    assert r.json()["publicacao"]["miniatura_vencida"] is False
    # Fresca: não pede de novo.
    await client.get(f"{URL}/conversas/{conversas[f'{MIDIA}:p-bia'].id}/publicacao")
    assert len(graph.chamadas_de("GET", MIDIA)) == 1
    # Com a leitura desligada, nada sai.
    monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", False)
    await db.refresh(pub)
    pub.miniatura_lida_em = T0 - timedelta(hours=7)
    await db.commit()
    r = await client.get(f"{URL}/conversas/{conversas[f'{MIDIA}:p-bia'].id}/publicacao")
    assert r.json()["publicacao"]["miniatura_vencida"] is True
    assert len(graph.chamadas_de("GET", MIDIA)) == 1


async def test_interagiu_antes_liga_mencao_e_comentario_pelo_arroba(
    client, db, graph, contas, pessoa
):
    """A menção guarda a pessoa pelo @ (o /tags não traz o id); o comentário, pelo id."""
    carla = {
        "id": "c-carla",
        "text": "Chegou!",
        "timestamp": _ts(T0 - timedelta(hours=1)),
        "username": "carla",
        "from": {"id": "p-carla", "username": "carla"},
    }
    graph.quando("GET", f"{IG}/media", _midias(comentarios=4))
    graph.quando("GET", f"{MIDIA}/comments", _comentarios_ig(extra=[carla]))
    graph.quando("GET", f"{IG}/tags", _tags())
    graph.quando("GET", f"{PAGINA}/posts", {"data": []})
    await redes.atendimento_redes(None)
    conversas = await _conversas(db)
    for chave, esperado in (
        (f"{MIDIA}:p-carla", 1),
        (f"{MENCAO}:@carla", 1),
        (f"{MIDIA}:p-bia", 0),
    ):
        r = await client.get(f"{URL}/conversas/{conversas[chave].id}/publicacao")
        assert r.json()["interacoes_anteriores"] == esperado, chave


async def test_pergunta_vai_para_o_topo_da_lista(client, db, graph, contas, pessoa):
    """RF7: a pergunta passa à frente do comentário comum (mesmo vencido) em
    "Falta responder" e vem primeiro no filtro Mídia; a linha traz o selo; a
    paginação (cursor com o grupo) não perde nem repete ninguém."""

    def comentario(id_, quem, texto, horas):
        return {
            "id": id_,
            "text": texto,
            "timestamp": _ts(T0 - timedelta(hours=horas)),
            "username": quem,
            "from": {"id": f"p-{quem}", "username": quem},
        }

    extra = [
        comentario("c-cris", "cris", "Linda demais", 30),  # comum, vencido
        comentario("c-dani", "dani", "onde fica a loja de vocês", 20),  # pergunta
        comentario("c-eva", "eva", "❤️", 1),  # comum, recente
    ]
    graph.quando("GET", f"{IG}/media", _midias(comentarios=6))
    graph.quando("GET", f"{MIDIA}/comments", _comentarios_ig(extra=extra))
    graph.quando("GET", f"{IG}/tags", _tags())
    graph.quando("GET", f"{PAGINA}/posts", _posts())
    graph.quando("GET", f"{POST}/comments", _comentarios_fb())
    await redes.atendimento_redes(None)
    ids = {k.rsplit(":", 1)[-1]: str(v.id) for k, v in (await _conversas(db)).items()}
    nome = {v: k for k, v in ids.items()}

    async def todas_as_paginas(filtro: str, limite: int) -> tuple[list[str], list[str]]:
        vistos, cursores, cursor = [], [], None
        for _ in range(20):
            params = {"filtro": filtro, "limite": limite, "plataforma": "instagram"}
            if cursor:
                params["antes_de"] = cursor
            r = await client.get(f"{URL}/conversas", params=params)
            assert r.status_code == 200, r.text
            vistos += [nome[i["id"]] for i in r.json()["itens"]]
            cursor = r.json()["proximo"]
            if not cursor:
                return vistos, cursores
            cursores.append(cursor)
        raise AssertionError("a paginação não terminou")

    # "Falta responder": as perguntas pelo prazo (Dani vence antes da Bia);
    # o comentário comum no FIM, mesmo o da Cris, já vencido.
    r = await client.get(f"{URL}/conversas", params={"filtro": "aguardando"})
    itens = r.json()["itens"]
    assert [nome[i["id"]] for i in itens] == ["p-dani", "p-bia", "p-cris", "p-eva"]
    assert [i["eh_pergunta"] for i in itens] == [True, True, False, False]
    vistos, cursores = await todas_as_paginas("aguardando", 3)
    assert vistos == ["p-dani", "p-bia", "p-cris", "p-eva"]
    assert cursores[0].startswith("prazo:comum:") and cursores[0].endswith(f"|{ids['p-cris']}")
    for limite in (1, 2):
        assert (await todas_as_paginas("aguardando", limite))[0] == vistos

    # Mídia: as perguntas pendentes primeiro (pela recência), depois o resto.
    r = await client.get(f"{URL}/conversas", params={"filtro": "midia", "plataforma": "instagram"})
    ordem = [nome[i["id"]] for i in r.json()["itens"]]
    assert ordem == ["p-bia", "p-dani", "p-eva", "p-ana", "@carla", "p-cris"]
    for limite in (1, 2, 3):
        vistos, cursores = await todas_as_paginas("midia", limite)
        assert vistos == ordem, limite
    vistos, cursores = await todas_as_paginas("midia", 1)
    assert cursores[0].startswith("pergunta:") and cursores[1].startswith("pergunta:")
    assert not cursores[2].startswith("pergunta:")
    # Em "Todas", a ordem continua a da recência (a pergunta só ganha o selo).
    r = await client.get(f"{URL}/conversas", params={"plataforma": "instagram"})
    assert [nome[i["id"]] for i in r.json()["itens"]] == [
        "p-eva", "p-bia", "p-ana", "p-dani", "@carla", "p-cris",
    ]  # fmt: skip
    assert r.json()["itens"][1]["eh_pergunta"] is True
    # Cursor torto do grupo: 422.
    for errado in ("pergunta:", "prazo:comum:lixo|x"):
        r = await client.get(
            f"{URL}/conversas",
            params={"filtro": "midia" if errado.startswith("pergunta") else "aguardando",
                    "antes_de": errado},
        )  # fmt: skip
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "cursor_invalido"), errado


async def test_acoes_bloqueadas_com_o_envio_desligado(client, db, graph, contas, pessoa):
    await _lido(db, graph)
    bia = await _comentario(db, "c-bia")
    antes = len(graph.chamadas)
    msgs = await db.scalar(select(func.count(AtendimentoMensagem.id)))
    for caminho, corpo in (
        ("responder", {"texto": "Temos!", "confirmar": True}),
        ("responder-direct", {"texto": "Temos!", "confirmar": True}),
        ("ocultar", {"confirmar": True}),
    ):
        r = await client.post(f"{URL}/comentarios/{bia.id}/{caminho}", json=corpo)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "envio_desligado"
        # ANTES de qualquer coisa: nem o comentário inexistente dá 404.
        r = await client.post(
            f"{URL}/comentarios/00000000-0000-0000-0000-000000000000/{caminho}", json=corpo
        )
        assert r.json()["detail"]["code"] == "envio_desligado"
    assert len(graph.chamadas) == antes
    assert await db.scalar(select(func.count(AtendimentoMensagem.id))) == msgs
    await db.refresh(bia)
    assert bia.oculto is False


@pytest.fixture
def envio_ligado(monkeypatch):
    """SÓ EM TESTE: o caminho com o envio ligado, contra a Graph de mentira."""
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)


async def test_responder_em_publico_caminho_inteiro(
    client, db, graph, contas, pessoa, envio_ligado
):
    conversas = await _lido(db, graph)
    bia = await _comentario(db, "c-bia")
    url = f"{URL}/comentarios/{bia.id}/responder"
    # Sem confirmar: "Responder em PÚBLICO?".
    r = await client.post(url, json={"texto": "Temos sim!"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "confirmar_publico")
    # A conta em observar (o padrão): só lê.
    r = await client.post(url, json={"texto": "Temos sim!", "confirmar": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "canal_em_observacao")
    await _modo(db, f"rede:instagram:{IG}", "humano")
    # Texto vazio / longo demais: 422.
    r = await client.post(url, json={"texto": "  ", "confirmar": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "texto_invalido")
    r = await client.post(url, json={"texto": "x" * 2201, "confirmar": True})
    assert r.status_code == 422
    assert not graph.chamadas_de("POST", "c-bia/replies")

    graph.quando("POST", "c-bia/replies", {"id": "r-nova"})
    r = await client.post(
        url, json={"texto": "Temos sim! https://charlots.com.br", "confirmar": True}
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo["mensagem"]["status"], corpo["mensagem"]["autor"]) == ("enviada", "loja")
    assert corpo["comentario"]["respondido"] is False  # a resposta é OUTRA linha
    chamada = graph.chamadas_de("POST", "c-bia/replies")[0]
    assert chamada["auth"] == f"OAuth {TOKEN_SISTEMA}"
    assert parse_qs(chamada["corpo"])["message"] == ["Temos sim! https://charlots.com.br"]
    conversa = (await _conversas(db))[f"{MIDIA}:p-bia"]
    assert conversa.id == conversas[f"{MIDIA}:p-bia"].id
    assert (conversa.situacao, conversa.aguardando_resposta) == (CONVERSA_RESPONDIDA, False)
    assert conversa.dados["eh_pergunta"] is False
    nova = await _comentario(db, "r-nova")
    assert (nova.da_marca, nova.pai_externo_id, nova.conversa_id) == (True, "c-bia", conversa.id)
    m = (
        await db.execute(
            select(AtendimentoMensagem).where(AtendimentoMensagem.externo_id == "r-nova")
        )
    ).scalar_one()
    assert (m.origem, m.autor_user_id) == ("davinci_humano", pessoa.id)
    # A leitura seguinte traz a mesma resposta: não duplica.
    dados = _comentarios_ig()
    dados["data"][1]["replies"] = {
        "data": [
            {
                "id": "r-nova",
                "text": "Temos sim! https://charlots.com.br",
                "timestamp": _ts(T0),
                "parent_id": "c-bia",
                "username": "charlots_br",
                "from": {"id": IG, "username": "charlots_br"},
            }
        ]
    }
    graph.quando("GET", f"{IG}/media", _midias(comentarios=4))
    graph.quando("GET", f"{MIDIA}/comments", dados)
    await redes.atendimento_redes(None)
    assert len(await _mensagens(db, conversa)) == 2


async def test_resposta_falhou_ou_ambigua(client, db, graph, contas, pessoa, envio_ligado):
    await _lido(db, graph)
    await _modo(db, f"rede:instagram:{IG}", "humano")
    bia = await _comentario(db, "c-bia")
    url = f"{URL}/comentarios/{bia.id}/responder"
    graph.quando("POST", "c-bia/replies", erro_meta(100, "(#100) Invalid parameter"))
    r = await client.post(url, json={"texto": "Temos!", "confirmar": True})
    assert r.status_code == 200
    assert r.json()["mensagem"]["status"] == "falhou"
    assert "Invalid parameter" in r.json()["mensagem"]["erro"]
    conversa = (await _conversas(db))[f"{MIDIA}:p-bia"]
    assert conversa.aguardando_resposta is True  # não saiu: continua na fila
    graph.quando("POST", "c-bia/replies", httpx.ReadTimeout("lento"))
    r = await client.post(url, json={"texto": "Temos sim!", "confirmar": True})
    assert r.json()["mensagem"]["status"] == "revisar"


async def test_responder_a_resposta_vai_para_o_topo_e_facebook_usa_a_pagina(
    client, db, graph, contas, pessoa, envio_ligado
):
    await _lido(db, graph)
    await _modo(db, f"rede:facebook:{PAGINA}", "humano")
    caio = await _comentario(db, f"{POST}_c1")
    graph.quando("POST", f"{POST}_c1/comments", {"id": f"{POST}_c3"})
    r = await client.post(
        f"{URL}/comentarios/{caio.id}/responder", json={"texto": "Até amanhã!", "confirmar": True}
    )
    assert r.status_code == 200, r.text
    assert graph.chamadas_de("POST", f"{POST}_c1/comments")[0]["auth"] == f"OAuth {TOKEN_PAGINA_FB}"
    # Comentário da própria marca não se responde.
    da_pagina = await _comentario(db, f"{POST}_c2")
    r = await client.post(
        f"{URL}/comentarios/{da_pagina.id}/responder", json={"texto": "x", "confirmar": True}
    )
    assert r.json()["detail"]["code"] == "comentario_da_marca"


async def test_responder_no_direct(client, db, graph, contas, pessoa, envio_ligado):
    await _lido(db, graph)
    await _modo(db, f"rede:instagram:{IG}", "humano")
    bia = await _comentario(db, "c-bia")
    url = f"{URL}/comentarios/{bia.id}/responder-direct"
    graph.quando("POST", f"{IG}/messages", {"recipient_id": "p-bia", "message_id": "mid-1"})
    r = await client.post(url, json={"texto": "Oi Bia, temos no azul!", "confirmar": True})
    assert r.status_code == 200, r.text
    assert r.json()["mensagem"]["status"] == "enviada"
    chamada = graph.chamadas_de("POST", f"{IG}/messages")[0]
    # Token DA PÁGINA e o comentário como destinatário (Messenger Platform).
    assert chamada["auth"] == f"OAuth {TOKEN_PAGINA_IG}"
    assert '"comment_id":"c-bia"' in chamada["corpo"].replace(" ", "")
    assert r.json()["comentario"]["resposta_privada_em"] is not None
    # UMA por comentário.
    r = await client.post(url, json={"texto": "de novo", "confirmar": True})
    assert r.json()["detail"]["code"] == "resposta_privada_ja_enviada"
    # Menção não tem resposta privada.
    mencao = await _comentario(db, f"mencao:{IG}:{MENCAO}")
    r = await client.post(
        f"{URL}/comentarios/{mencao.id}/responder-direct", json={"texto": "oi", "confirmar": True}
    )
    assert r.json()["detail"]["code"] == "sem_resposta_privada"
    # Mais de 7 dias depois do comentário: a Meta não deixa.
    ana = await _comentario(db, "c-ana")
    ana.criado_em = T0 - timedelta(days=8)
    await db.commit()
    r = await client.post(
        f"{URL}/comentarios/{ana.id}/responder-direct", json={"texto": "oi", "confirmar": True}
    )
    assert r.json()["detail"]["code"] == "prazo_resposta_privada"
    assert len(graph.chamadas_de("POST", f"{IG}/messages")) == 1


async def test_responder_a_mencao(client, db, graph, contas, pessoa, envio_ligado):
    await _lido(db, graph)
    await _modo(db, f"rede:instagram:{IG}", "humano")
    mencao = await _comentario(db, f"mencao:{IG}:{MENCAO}")
    graph.quando("POST", f"{IG}/mentions", {"id": "c-resp-mencao"})
    r = await client.post(
        f"{URL}/comentarios/{mencao.id}/responder", json={"texto": "Obrigada!", "confirmar": True}
    )
    assert r.status_code == 200, r.text
    corpo = parse_qs(graph.chamadas_de("POST", f"{IG}/mentions")[0]["corpo"])
    assert corpo == {"media_id": [MENCAO], "message": ["Obrigada!"]}


async def test_ocultar_e_mostrar(client, db, graph, contas, pessoa, envio_ligado):
    conversas = await _lido(db, graph)
    bia = await _comentario(db, "c-bia")
    url = f"{URL}/comentarios/{bia.id}/ocultar"
    r = await client.post(url, json={"confirmar": True})
    assert r.json()["detail"]["code"] == "canal_em_observacao"
    await _modo(db, f"rede:instagram:{IG}", "humano")
    r = await client.post(url, json={})
    assert r.json()["detail"]["code"] == "confirmar_publico"
    graph.quando("POST", "c-bia", {"success": True})
    r = await client.post(url, json={"confirmar": True})
    assert r.status_code == 200, r.text
    assert r.json()["comentario"]["oculto"] is True
    assert parse_qs(graph.chamadas_de("POST", "c-bia")[0]["corpo"]) == {"hide": ["true"]}
    # Fica registrado quem fez (linha do tempo da conversa).
    msgs = await _mensagens(db, conversas[f"{MIDIA}:p-bia"])
    assert msgs[-1].autor == "sistema" and "ocultado" in msgs[-1].texto
    # Oculto não recebe resposta pública; mostrar de novo.
    r = await client.post(
        f"{URL}/comentarios/{bia.id}/responder", json={"texto": "x", "confirmar": True}
    )
    assert r.json()["detail"]["code"] == "comentario_oculto"
    r = await client.post(url, json={"confirmar": True, "ocultar": False})
    assert r.status_code == 200 and r.json()["comentario"]["oculto"] is False
    # A rede recusou: nada muda aqui.
    graph.quando("POST", "c-bia", erro_meta(10, "(#10) no permission"))
    r = await client.post(url, json={"confirmar": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "rede_recusou")
    await db.refresh(bia)
    assert bia.oculto is False


async def test_ocultar_no_facebook_usa_is_hidden_com_o_token_da_pagina(
    client, db, graph, contas, pessoa, envio_ligado
):
    await _lido(db, graph)
    await _modo(db, f"rede:facebook:{PAGINA}", "humano")
    caio = await _comentario(db, f"{POST}_c1")
    graph.quando("POST", f"{POST}_c1", {"success": True})
    r = await client.post(f"{URL}/comentarios/{caio.id}/ocultar", json={"confirmar": True})
    assert r.status_code == 200, r.text
    chamada = graph.chamadas_de("POST", f"{POST}_c1")[0]
    assert (chamada["auth"], parse_qs(chamada["corpo"])) == (
        f"OAuth {TOKEN_PAGINA_FB}",
        {"is_hidden": ["true"]},
    )


async def test_direct_sem_resposta_da_rede_nao_deixa_repetir(
    client, db, graph, contas, pessoa, envio_ligado
):
    """Timeout no Direct: pode ter saído, e a Meta só aceita UMA — não tenta às cegas."""
    await _lido(db, graph)
    await _modo(db, f"rede:instagram:{IG}", "humano")
    bia = await _comentario(db, "c-bia")
    url = f"{URL}/comentarios/{bia.id}/responder-direct"
    graph.quando("POST", f"{IG}/messages", httpx.ReadTimeout("lento"))
    r = await client.post(url, json={"texto": "Oi!", "confirmar": True})
    assert r.status_code == 200 and r.json()["mensagem"]["status"] == "revisar"
    r = await client.post(url, json={"texto": "Oi de novo", "confirmar": True})
    assert r.json()["detail"]["code"] == "resposta_privada_ja_enviada"
    assert len(graph.chamadas_de("POST", f"{IG}/messages")) == 1


async def test_responder_comentario_velho_cria_a_conversa(
    client, db, graph, contas, pessoa, envio_ligado
):
    """Comentário de fora da janela (só no cartão): ao responder, ganha a conversa."""
    velho = {
        "id": "c-velho",
        "text": "Linda",
        "timestamp": _ts(T0 - timedelta(days=45)),
        "username": "zeca",
        "from": {"id": "p-zeca", "username": "zeca"},
    }
    graph.quando("GET", f"{IG}/media", _midias(comentarios=4))
    graph.quando("GET", f"{MIDIA}/comments", _comentarios_ig(extra=[velho]))
    graph.quando("GET", f"{IG}/tags", {"data": []})
    graph.quando("GET", f"{PAGINA}/posts", {"data": []})
    await redes.atendimento_redes(None)
    await _modo(db, f"rede:instagram:{IG}", "humano")
    zeca = await _comentario(db, "c-velho")
    assert zeca.conversa_id is None
    graph.quando("POST", "c-velho/replies", {"id": "r-zeca"})
    r = await client.post(
        f"{URL}/comentarios/{zeca.id}/responder", json={"texto": "Obrigada!", "confirmar": True}
    )
    assert r.status_code == 200, r.text
    conversa = (await _conversas(db))[f"{MIDIA}:p-zeca"]
    assert [m.autor for m in await _mensagens(db, conversa)] == ["cliente", "loja"]
    assert conversa.situacao == CONVERSA_RESPONDIDA
    await db.refresh(zeca)
    assert zeca.conversa_id == conversa.id
