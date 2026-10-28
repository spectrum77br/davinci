# ruff: noqa: S105, S106  (tokens de teste, nada real)
"""A resposta automática da continuação nos comentários do Instagram (28/10/2026).

O cenário é o de produção em 27/10: a @uranyx_br posta "O Lacre" (Ep. 1/6 a
6/6) às 12h e às 19h pelo robô (3 por dia, intervalo 400, início 12 — a grade
12:00/18:40, que ele pega às 12:02/19:02). A Graph é de MENTIRA
(`httpx.MockTransport`, a mesma de `test_atendimento_redes`): nenhuma chamada
fora do previsto passa.

O que se garante:
- o pedido (puro): "Continua", "parte 2e3", "cadê a parte 2???" sim; "o meu
  continua ligando", marcação de amigo e link, não;
- o episódio pelo título do criativo e a hora em texto ("hoje às 19h");
- a rodada: "a parte 4 sai hoje às 19h" no Ep. 3 depois das 12h, "a parte 2
  já está no perfil" no Ep. 1, "amanhã às 12h" no Ep. 4 depois das 19h;
  post avulso e último episódio ficam para a equipe; comentário anterior ao
  interruptor nem é olhado; a mesma pessoa no mesmo post só uma vez; a
  leitura seguinte não duplica;
- espera: perto da hora da postagem, episódio seguinte fora da fila
  (responde quando ele entra), o limite por rodada;
- nada sai com o envio desligado ou o interruptor desligado; a falha da rede
  para a rodada e não volta sozinha;
- o interruptor na aba Lojas: só admin, só comentário do Instagram.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoComentario,
    AtendimentoConversa,
    AtendimentoMensagem,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingPostagem,
    User,
    UserRole,
)
from app.models.marca import Marca, RedeSocial
from app.models.marketing_postagem import RedeSocialToken
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import canais_externos, redes
from app.services.atendimento import redes_continuacao as cont
from app.services.atendimento.constantes import CANAL_COMENTARIO, CONVERSA_RESPONDIDA
from tests.test_atendimento_redes import Graph, RedisFalso, erro_meta

URL = "/api/atendimento"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
IG = "17841478036222318"
TOKEN = "EAAsistemaUranyx0000000000000000000"
REF = f"rede:instagram:{IG}"


def _brt(dia: int, hora: int, minuto: int = 0) -> datetime:
    """Outubro de 2026, horário de Brasília (UTC−3) → UTC."""
    return datetime(2026, 10, dia, hora + 3, minuto, tzinfo=UTC)


def _ts(quando: datetime) -> str:
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


LIGADO_EM = _brt(27, 10)
# As mídias do Instagram (o id é o `post_external_id` da postagem).
M_AVULSO = "180000000000000"
M1, M2, M3, M4 = (f"18000000000000{n}" for n in range(1, 5))
PUBLICADO = {
    M_AVULSO: _brt(25, 12, 4),
    M1: _brt(26, 12, 4),
    M2: _brt(26, 19, 4),
    M3: _brt(27, 12, 5),
    M4: _brt(27, 19, 4),
}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    monkeypatch.setattr(redes, "redis", RedisFalso())
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_redes_ativa", True)
    monkeypatch.setattr(redes, "novo_cliente", Graph().cliente)


@pytest.fixture
def graph(monkeypatch):
    g = Graph()
    monkeypatch.setattr(redes, "novo_cliente", g.cliente)
    yield g
    assert g.inesperadas == [], g.inesperadas


# ─────────────── o cenário ───────────────


async def _criativo(
    db: AsyncSession, marca: Marca, modelo: str, *, aprovado: bool = True, fila: int | None = None
) -> tuple[MarketingCreative, MarketingCreativeFile]:
    c = MarketingCreative(
        modelo=modelo, marca=marca.slug, marca_id=marca.id, aprovado=aprovado, fila_posicao=fila
    )
    db.add(c)
    await db.flush()
    f = MarketingCreativeFile(
        creative_id=c.id, file_name="v.mp4", file_mime="video/mp4", file_rel=f"x/{c.id}.mp4"
    )
    db.add(f)
    await db.flush()
    return c, f


async def _publicar(db: AsyncSession, rede: RedeSocial, criativo, midia: str) -> None:
    c, f = criativo
    db.add(
        MarketingPostagem(
            creative_id=c.id,
            file_id=f.id,
            rede_social_id=rede.id,
            plataforma="instagram",
            conta=rede.conta,
            status="publicado",
            origem="robo",
            publicado_em=PUBLICADO[midia],
            post_external_id=midia,
        )
    )
    await db.flush()


@pytest.fixture
async def serie(db: AsyncSession) -> dict:
    """A @uranyx_br em 27/10 13:00: Ep. 1–3 no ar, o Ep. 4 na frente da fila."""
    marca = Marca(nome="uranyx", slug="uranyx")
    db.add(marca)
    await db.flush()
    rede = RedeSocial(
        marca_id=marca.id, plataforma="instagram", conta="uranyx_br", ativo=True,
        postagem_auto=True, postagem_hora_inicio=12, postagem_max_dia=3,
        postagem_intervalo_min=400,
    )  # fmt: skip
    db.add(rede)
    await db.flush()
    db.add(
        RedeSocialToken(
            rede_social_id=rede.id,
            external_user_id=IG,
            external_username="uranyx_br",
            provedor="facebook",
            token_enc=encrypt_json({"access_token": TOKEN}),
        )
    )
    ep = {}
    for n in range(1, 7):
        ep[n] = await _criativo(
            db,
            marca,
            f'Microdrama "O Lacre" — Ep. {n}/6: Título {n} (Roberval × Uranyx F109)',
            aprovado=n <= 4,
            fila=1 if n == 4 else None,
        )
    avulso = await _criativo(db, marca, "video 15s")
    await _publicar(db, rede, avulso, M_AVULSO)
    for n, midia in ((1, M1), (2, M2), (3, M3)):
        await _publicar(db, rede, ep[n], midia)
    await db.commit()
    return {"marca": marca, "rede": rede, "ep": ep}


def _midias(*ids: str) -> dict:
    return {
        "data": [
            {
                "id": m,
                "caption": "Molhou, caiu, sujou — e continua ligando",
                "media_type": "VIDEO",
                "media_product_type": "REELS",
                "thumbnail_url": "https://cdn.exemplo/capa.jpg",
                "permalink": f"https://www.instagram.com/reel/{m}/",
                "timestamp": _ts(PUBLICADO[m]),
                "username": "uranyx_br",
                "comments_count": 1,
            }
            for m in ids
        ]
    }


def _c(id_: str, texto: str, quem: str, quando: datetime, **extra) -> dict:
    return {
        "id": id_,
        "text": texto,
        "timestamp": _ts(quando),
        "hidden": False,
        "username": quem,
        "from": {"id": f"p-{quem}", "username": quem},
        **extra,
    }


def _ler(graph: Graph, comentarios: dict[str, list[dict]], *, contagem: int = 1) -> None:
    """A Graph da leitura: as mídias (com a contagem nova) e os comentários de cada uma."""
    midias = _midias(*comentarios)
    for m in midias["data"]:
        m["comments_count"] = contagem
    graph.quando("GET", f"{IG}/media", midias)
    graph.quando("GET", f"{IG}/tags", {"data": []})
    for midia, itens in comentarios.items():
        graph.quando("GET", f"{midia}/comments", {"data": itens})


async def _ligar(db: AsyncSession, desde: datetime = LIGADO_EM) -> AtendimentoCanal:
    canal = await canais_externos.garantir_canal(
        db,
        externo_ref=REF,
        plataforma="instagram",
        canal=CANAL_COMENTARIO,
        nome="@uranyx_br",
    )
    canal.auto_continuacao_desde = desde
    await db.commit()
    return canal


async def _rodar(graph: Graph, agora: datetime) -> dict:
    resumo = await redes.atendimento_redes(None, cliente=graph.cliente(), agora=agora)
    assert resumo is not None
    return resumo["por_conta"][f"instagram:{IG}"]


def _respostas(graph: Graph) -> dict[str, str]:
    """comentário → texto publicado (o POST /{comentário}/replies)."""
    out = {}
    for c in graph.chamadas:
        if c["metodo"] == "POST" and c["caminho"].endswith("/replies"):
            out[c["caminho"].split("/")[0]] = parse_qs(c["corpo"])["message"][0]
    return out


async def _estado(db: AsyncSession, externo_id: str) -> dict | None:
    c = (
        await db.execute(
            select(AtendimentoComentario).where(AtendimentoComentario.externo_id == externo_id)
        )
    ).scalar_one()
    await db.refresh(c)
    return (c.dados or {}).get(cont.CHAVE)


# ─────────────── puros ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "Continua", "continuação", "Continuação!!", "CONTINUAAAA", "parte2", "parte 2e3",
        "continuação parte 2e3e4", "continua na parte 4 e 5", "ver parte três", "p 2", "P2",
        "cadê a parte 2???", "quero ver o final", "kd a continuação 😭", "Continua pfvr",
        "quando sai a parte 3?", "próximo episódio", "segunda parte", "manda a continuação kkkk",
    ],
)  # fmt: skip
def test_pede_continuacao(texto):
    assert cont.pede_continuacao(texto)


@pytest.mark.parametrize(
    "texto",
    [
        None, "", "co", "segue", "❤️", "não entendi nada", "tem case para ele?",
        "o meu continua ligando depois de cair na água",
        "@amiga continua", "continua em https://exemplo.com", "partiu comprar",
        "curtar comenta parte2", "continua " * 20, "qual é a resolução de tela dele?",
    ],
)  # fmt: skip
def test_nao_pede_continuacao(texto):
    assert not cont.pede_continuacao(texto)


def test_episodio_pelo_titulo():
    ep = cont.episodio('Microdrama "O Lacre" — Ep. 1/6: Caixa Lacrada (Roberval × Uranyx F109)')
    assert (ep.serie, ep.chave, ep.n, ep.total) == (
        'Microdrama "O Lacre"',
        "microdrama o lacre",
        1,
        6,
    )
    ep = cont.episodio("Debaixo do Domo ep. 3 — A Planta da Menina (Elias e o Uranyx F117)")
    assert (ep.chave, ep.n, ep.total) == ("debaixo do domo", 3, None)
    for avulso in ("video 15s", "Grau no Reino dos Dragões — Roberval", "Ep. 2", None, "X ep. 7/6"):
        assert cont.episodio(avulso) is None, avulso


def test_hora_em_texto_e_minuto_do_robo():
    agora = _brt(27, 13)
    assert cont.quando_sai(_brt(27, 19, 2), agora) == "hoje às 19h"
    assert cont.quando_sai(_brt(28, 12, 2), agora) == "amanhã às 12h"
    assert cont.quando_sai(_brt(29, 15, 30), agora) == "dia 29/10 às 15h30"
    assert cont.minuto_do_robo(_brt(27, 12)) == _brt(27, 12, 2)
    assert cont.minuto_do_robo(_brt(27, 18, 40)) == _brt(27, 19, 2)


# ─────────────── a rodada ───────────────


async def test_responde_com_a_hora_e_o_ja_saiu(db, graph, serie):
    await _ligar(db)
    agora = _brt(27, 13)
    _ler(
        graph,
        {
            M3: [
                _c("c31", "Continuação", "ana", _brt(27, 12, 30)),
                _c("c32", "Continua", "ana", _brt(27, 12, 40)),
                _c("c33", "o meu continua ligando depois de cair na água", "bia", _brt(27, 12, 41)),
                _c("c34", "parte 2e3", "caio", _brt(27, 12, 50)),
                _c("c35", "Continua", "dani", _brt(27, 9)),
            ],
            M1: [_c("c11", "cadê a parte 2???", "eva", _brt(27, 12, 20))],
            M_AVULSO: [_c("c01", "continua", "fred", _brt(27, 11))],
        },
    )
    for c in ("c31", "c34", "c11"):
        graph.quando("POST", f"{c}/replies", {"id": f"r-{c}"})
    r = await _rodar(graph, agora)
    assert r["continuacao"]["respondidos"] == 3, r
    textos = _respostas(graph)
    assert set(textos) == {"c31", "c34", "c11"}
    assert textos["c31"].startswith("@ana ") and "parte 4" in textos["c31"]
    assert "hoje às 19h" in textos["c31"] and "hoje às 19h" in textos["c34"]
    assert textos["c11"].startswith("@eva ") and "parte 2" in textos["c11"]
    assert "já" in textos["c11"] and "19h" not in textos["c11"]
    assert (await _estado(db, "c31"))["status"] == cont.RESPONDIDO
    assert (await _estado(db, "c32"))["motivo"] == "ja_respondido"
    assert (await _estado(db, "c01"))["motivo"] == "nao_e_serie"
    # Não é pedido, e o de antes do interruptor: nem marcados.
    assert await _estado(db, "c33") is None and await _estado(db, "c35") is None
    m = (
        await db.execute(
            select(AtendimentoMensagem).where(AtendimentoMensagem.externo_id == "r-c31")
        )
    ).scalar_one()
    assert (m.origem, m.autor, m.status) == ("davinci_auto", "loja", "enviada")
    assert m.payload["continuacao"] == {"parte": 4, "motivo": "proximo_da_fila"}
    conversa = await db.get(AtendimentoConversa, m.conversa_id)
    await db.refresh(conversa)
    assert conversa.situacao == CONVERSA_RESPONDIDA

    # A leitura seguinte traz as respostas da marca: nada de novo sai.
    graph.chamadas.clear()
    respondido = {
        M3: [
            _c("c31", "Continuação", "ana", _brt(27, 12, 30), replies={"data": [
                _c("r-c31", textos["c31"], "uranyx_br", agora, parent_id="c31")
                | {"from": {"id": IG, "username": "uranyx_br"}},
            ]}),
            _c("c32", "Continua", "ana", _brt(27, 12, 40)),
            _c("c34", "parte 2e3", "caio", _brt(27, 12, 50)),
        ],
    }  # fmt: skip
    _ler(graph, respondido, contagem=9)
    r = await _rodar(graph, agora + timedelta(minutes=15))
    assert not _respostas(graph)
    assert r["continuacao"]["respondidos"] == 0
    assert (
        await db.execute(
            select(AtendimentoMensagem).where(AtendimentoMensagem.externo_id == "r-c31")
        )
    ).scalar_one().id == m.id


async def test_depois_das_19h_amanha_as_12h(db, graph, serie):
    await _ligar(db)
    await _publicar(db, serie["rede"], serie["ep"][4], M4)
    serie["ep"][5][0].aprovado = True
    serie["ep"][5][0].fila_posicao = 2
    await db.commit()
    _ler(graph, {M4: [_c("c41", "Continua", "gil", _brt(27, 19, 40))]})
    graph.quando("POST", "c41/replies", {"id": "r-c41"})
    r = await _rodar(graph, _brt(27, 20))
    assert r["continuacao"]["respondidos"] == 1, r
    texto = _respostas(graph)["c41"]
    assert "parte 5" in texto and "amanhã às 12h" in texto


async def test_ultimo_episodio_e_seguinte_fora_da_fila(db, graph, serie):
    await _ligar(db)
    rede, ep = serie["rede"], serie["ep"]
    await _publicar(db, rede, ep[4], M4)
    await db.commit()
    # Ep. 4 no ar e o 5 ainda sem aprovação: o robô das 12h pegaria outro.
    _ler(graph, {M4: [_c("c41", "Continua", "gil", _brt(27, 19, 40))]})
    r = await _rodar(graph, _brt(27, 20))
    assert r["continuacao"]["esperando"] == 1, r
    assert r["continuacao"]["motivos"] == {"outro_na_frente": 1}
    assert await _estado(db, "c41") is None and not _respostas(graph)
    # Aprovado e na frente da fila: a rodada seguinte responde.
    ep[5][0].aprovado = True
    ep[5][0].fila_posicao = 2
    await db.commit()
    graph.quando("POST", "c41/replies", {"id": "r-c41"})
    r = await _rodar(graph, _brt(27, 20, 15))
    assert "amanhã às 12h" in _respostas(graph)["c41"]
    # O último episódio: não há continuação — fica para a equipe.
    for n in (5, 6):
        ep[n][0].aprovado = True
    await db.flush()
    db.add(MarketingPostagem(
        creative_id=ep[6][0].id, file_id=ep[6][1].id, rede_social_id=rede.id,
        plataforma="instagram", conta="uranyx_br", status="publicado", origem="robo",
        publicado_em=_brt(28, 19, 4), post_external_id="180000000000006",
    ))  # fmt: skip
    await db.commit()
    PUBLICADO["180000000000006"] = _brt(28, 19, 4)
    try:
        graph.chamadas.clear()
        _ler(graph, {"180000000000006": [_c("c61", "parte 7", "hugo", _brt(28, 19, 50))]})
        r = await _rodar(graph, _brt(28, 20))
        assert (await _estado(db, "c61"))["motivo"] == "ultimo_episodio"
        assert not _respostas(graph)
    finally:
        PUBLICADO.pop("180000000000006")


async def test_espera_na_hora_da_postagem_e_no_limite_da_rodada(db, graph, serie, monkeypatch):
    await _ligar(db)
    comentarios = {
        M3: [_c(f"c3{i}", "Continua", f"p{i}", _brt(27, 12, 6 + i)) for i in range(3)],
    }
    _ler(graph, comentarios)
    # 12:10: o robô das 12h ainda pode estar publicando.
    r = await _rodar(graph, _brt(27, 12, 10))
    assert r["continuacao"] == {"pulado": "janela_de_postagem"}
    assert not _respostas(graph)
    monkeypatch.setattr(cont, "MAX_POR_RODADA", 2)
    for i in range(3):
        graph.quando("POST", f"c3{i}/replies", {"id": f"r-c3{i}"})
    r = await _rodar(graph, _brt(27, 12, 39))
    assert (r["continuacao"]["respondidos"], r["continuacao"]["esperando"]) == (2, 1)
    r = await _rodar(graph, _brt(27, 12, 54))
    assert r["continuacao"]["respondidos"] == 1
    assert set(_respostas(graph)) == {"c30", "c31", "c32"}


async def test_limite_por_post_e_por_pessoa(db, graph, serie, monkeypatch):
    await _ligar(db)
    monkeypatch.setattr(cont, "MAX_POR_POST_HORA", 1)
    _ler(
        graph,
        {
            M3: [
                _c("c30", "Continua", "ana", _brt(27, 12, 30)),
                _c("c31", "Continua", "bia", _brt(27, 12, 31)),
            ],
            M2: [_c("c21", "Continua", "ana", _brt(27, 12, 32))],
            M1: [_c("c11", "Continua", "ana", _brt(27, 12, 33))],
        },
    )
    for c in ("c30", "c31", "c21", "c11"):
        graph.quando("POST", f"{c}/replies", {"id": f"r-{c}"})
    monkeypatch.setattr(cont, "MAX_POR_PESSOA_DIA", 2)
    r = await _rodar(graph, _brt(27, 13))
    # A Ana: Ep. 3 e Ep. 2 (2 por dia); o Ep. 1 dela fica de fora. A Bia
    # espera: o Ep. 3 já teve a resposta da hora.
    assert set(_respostas(graph)) == {"c30", "c21"}, r
    assert (await _estado(db, "c11"))["motivo"] == "limite_pessoa"
    assert await _estado(db, "c31") is None
    assert r["continuacao"]["motivos"]["limite_post"] == 1


async def test_nada_sai_sem_o_envio_ou_sem_o_interruptor(db, graph, serie, monkeypatch):
    _ler(graph, {M3: [_c("c31", "Continua", "ana", _brt(27, 12, 30))]})
    # Sem o interruptor (o padrão): a leitura nem chama a continuação.
    r = await _rodar(graph, _brt(27, 13))
    assert "continuacao" not in r
    await _ligar(db)
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", False)
    r = await _rodar(graph, _brt(27, 13, 15))
    assert r["continuacao"] == {"pulado": "envio_desligado"}
    assert not _respostas(graph) and await _estado(db, "c31") is None


async def test_falha_da_rede_para_a_rodada(db, graph, serie):
    await _ligar(db)
    _ler(
        graph,
        {
            M3: [
                _c("c31", "Continua", "ana", _brt(27, 12, 30)),
                _c("c32", "Continua", "bia", _brt(27, 12, 31)),
            ]
        },
    )
    graph.quando("POST", "c31/replies", erro_meta(10, "(#10) Application does not have permission"))
    r = await _rodar(graph, _brt(27, 13))
    assert (r["continuacao"]["falhas"], r["continuacao"]["respondidos"]) == (1, 0)
    assert (await _estado(db, "c31"))["status"] == cont.FALHOU
    assert await _estado(db, "c32") is None  # nem tentou
    assert [c["caminho"] for c in graph.chamadas if c["metodo"] == "POST"] == ["c31/replies"]
    # A rodada seguinte não repete a que falhou.
    graph.quando("POST", "c32/replies", {"id": "r-c32"})
    await _rodar(graph, _brt(27, 13, 15))
    assert [c["caminho"] for c in graph.chamadas if c["metodo"] == "POST"] == [
        "c31/replies",
        "c32/replies",
    ]


# ─────────────── o interruptor na aba Lojas ───────────────


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


async def test_interruptor_so_admin_e_so_instagram(client, db, pessoa, make_user, auth_as):
    ig = await canais_externos.garantir_canal(
        db, externo_ref=REF, plataforma="instagram", canal=CANAL_COMENTARIO, nome="@uranyx_br"
    )
    fb = await canais_externos.garantir_canal(
        db, externo_ref="rede:facebook:139", plataforma="facebook", canal=CANAL_COMENTARIO
    )
    await db.commit()
    r = await client.get(f"{URL}/canais")
    por_id = {c["id"]: c for c in r.json()}
    assert por_id[str(ig.id)]["auto_continuacao_disponivel"] is True
    assert por_id[str(fb.id)]["auto_continuacao_disponivel"] is False
    assert por_id[str(ig.id)]["auto_continuacao_desde"] is None
    r = await client.patch(f"{URL}/canais/{ig.id}", json={"auto_continuacao": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    auth_as(await make_user(role=UserRole.ADMIN, permissions=PODE_TUDO))
    r = await client.patch(f"{URL}/canais/{fb.id}", json={"auto_continuacao": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "continuacao_so_instagram")
    r = await client.patch(f"{URL}/canais/{ig.id}", json={"auto_continuacao": True})
    assert r.status_code == 200, r.text
    assert r.json()["auto_continuacao_desde"] is not None and r.json()["modo"] == "observar"
    desde = r.json()["auto_continuacao_desde"]
    # Ligar de novo não mexe na hora (comentário de antes continua de fora).
    r = await client.patch(f"{URL}/canais/{ig.id}", json={"auto_continuacao": True})
    assert r.json()["auto_continuacao_desde"] == desde
    r = await client.patch(f"{URL}/canais/{ig.id}", json={"auto_continuacao": False})
    assert (r.status_code, r.json()["auto_continuacao_desde"]) == (200, None)


async def test_recusa_da_conta_espera_e_nao_marca(db, graph, serie):
    """Sem o token na hora de responder: nada sai, nada fica marcado, a rodada para."""
    await _ligar(db)
    _ler(
        graph,
        {
            M3: [
                _c("c31", "Continua", "ana", _brt(27, 12, 8)),
                _c("c32", "Continua", "bia", _brt(27, 12, 9)),
            ]
        },
    )
    await _rodar(graph, _brt(27, 12, 10))  # lê (na janela da postagem: não responde)
    tok = (
        await db.execute(
            select(RedeSocialToken).where(RedeSocialToken.rede_social_id == serie["rede"].id)
        )
    ).scalar_one()
    tok.token_enc = encrypt_json({})
    await db.commit()
    r = await _rodar(graph, _brt(27, 12, 40))
    assert r["continuacao"]["motivos"]["sem_token"] == 1, r
    assert (r["continuacao"]["esperando"], r["continuacao"]["respondidos"]) == (1, 0)
    assert await _estado(db, "c31") is None and await _estado(db, "c32") is None
    assert not _respostas(graph)
    # O token volta: a rodada seguinte responde os dois.
    tok.token_enc = encrypt_json({"access_token": TOKEN})
    await db.commit()
    for c in ("c31", "c32"):
        graph.quando("POST", f"{c}/replies", {"id": f"r-{c}"})
    r = await _rodar(graph, _brt(27, 12, 55))
    assert r["continuacao"]["respondidos"] == 2, r
    assert set(_respostas(graph)) == {"c31", "c32"}
