"""Studio DaVinci, 13/10/2026: ideias sem teto, o "já foi feita?", ideia nova
pelo portal, o relatório de vídeos e a duração/IA de cada envio.

O que estes testes travam, na ordem do pedido do Marco:

1. A lista de ideias não esconde mais nada (havia 264 visíveis e a API cortava
   em 200), e o corpo continua o de antes — o portal no ar lê os dois.
2. `entrega` por ideia RAIZ e por agência: a versão mostra o vídeo entregue
   pela ideia, e a outra agência nunca vê a entrega alheia.
3. `POST /roteiros` cria ideia NOVA, nunca edita; "todas" vira
   `equipe_destino` NULL; personagem desligado é recusado; 30 por minuto.
4. O relatório conta só vídeo, na janela do ARQUIVO, e trata duração NULL à
   parte (nunca como zero).
5. Duração e IA: medidas no envio sem nunca barrar o envio.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Base, UserRole
from app.models.marketing import MarketingCreative, MarketingCreativeFile
from app.models.marketing_personagem import MarketingPersonagem
from app.models.marketing_postagem import MarketingPostagem
from app.models.marketing_roteiro import MarketingRoteiro, MarketingRoteiroPersonagem
from app.routers import portal_criativos as rota
from app.services.marketing import medidas_video
from app.services.marketing.shopee_video_anuncio import InfoVideo
from app.services.rate_limit import RateLimitError

TOK_A = "tok-agencia-a-0123456789"
TOK_B = "tok-agencia-b-9876543210"
A = {"X-Portal-Token": TOK_A}
B = {"X-Portal-Token": TOK_B}

_ALEMBIC = Path(__file__).resolve().parent.parent / "alembic"


@pytest.fixture(autouse=True)
def _dois_tokens(monkeypatch):
    monkeypatch.setattr(get_settings(), "portal_tokens", f"{TOK_A}:alpha,{TOK_B}:beta")


@pytest.fixture(autouse=True)
def limite(monkeypatch):
    """Janela de 1 min em memória no lugar do Redis — conta de verdade, para
    o teste do teto de 30 poder estourar."""
    contas: dict[str, int] = {}

    async def _janela(*, key: str, limit: int, window_seconds: int) -> int:
        assert window_seconds == 60
        contas[key] = contas.get(key, 0) + 1
        if contas[key] > limit:
            raise RateLimitError(retry_after=17)
        return limit - contas[key]

    monkeypatch.setattr(rota, "sliding_window_check", _janela)
    return contas


@pytest.fixture(autouse=True)
def sonda(monkeypatch):
    """O ffprobe de mentira: por padrão "não leu" (bytes de teste não são
    vídeo). O teste que quer medida põe a resposta em `sonda["info"]`."""
    estado: dict = {"info": None, "erro": None, "chamadas": 0}

    async def _sondar(caminho: Path):
        estado["chamadas"] += 1
        if estado["erro"] is not None:
            raise estado["erro"]
        return estado["info"]

    monkeypatch.setattr(medidas_video, "sondar_video", _sondar)
    return estado


# ─────────────── sementes ───────────────


async def _ideia(
    db: AsyncSession,
    *,
    titulo: str = "Mala de bordo 20kg",
    texto: str = "cena 1: abre a mala",
    destino: str | None = None,
    origem_id: UUID | None = None,
    sku: str | None = "dgd23",
    marca: str | None = "poofy",
    criado_em: datetime | None = None,
) -> MarketingRoteiro:
    r = MarketingRoteiro(
        titulo=titulo,
        texto=texto,
        marca=marca,
        sku=sku,
        equipe_destino=destino,
        origem_id=origem_id,
        ativo=True,
    )
    if criado_em is not None:
        r.created_at = criado_em
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _entrega(
    db: AsyncSession,
    *,
    equipe: str,
    roteiro_id: UUID | None = None,
    aprovado: bool | None = None,
    com_arquivo: bool = True,
    enviado_em: datetime | None = None,
    duracao: str | None = None,
    mime: str = "video/mp4",
    nome: str = "video.mp4",
    ia_modelo: str | None = None,
    ia_creditos: str | None = None,
    modelo: str = "Mala de bordo 20kg",
) -> tuple[MarketingCreative, MarketingCreativeFile | None]:
    c = MarketingCreative(
        modelo=modelo,
        marca="poofy",
        sku="dgd23",
        equipe=equipe,
        roteiro_id=roteiro_id,
        aprovado=aprovado,
        ia_modelo=ia_modelo,
        ia_creditos=Decimal(ia_creditos) if ia_creditos is not None else None,
    )
    db.add(c)
    await db.flush()
    f = None
    if com_arquivo:
        f = MarketingCreativeFile(
            creative_id=c.id,
            file_name=nome,
            file_mime=mime,
            file_size=1234,
            file_rel=f"creatives/{c.id}/{nome}",
            sha256=uuid4().hex + uuid4().hex,
            duracao_s=Decimal(duracao) if duracao is not None else None,
        )
        if enviado_em is not None:
            f.created_at = enviado_em
        db.add(f)
    await db.commit()
    return c, f


async def _publicar(db: AsyncSession, c: MarketingCreative, f: MarketingCreativeFile, status):
    db.add(
        MarketingPostagem(
            creative_id=c.id,
            file_id=f.id,
            plataforma="instagram",
            status=status,
        )
    )
    await db.commit()


async def _personagem(db: AsyncSession, nome: str, *, ativo: bool = True) -> MarketingPersonagem:
    p = MarketingPersonagem(nome=nome, descricao=f"{nome} de teste", ativo=ativo)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _lista(client: AsyncClient, headers=A, **params) -> list[dict]:
    r = await client.get("/api/portal/roteiros", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()["roteiros"]


def _por_titulo(itens: list[dict]) -> dict[str, dict]:
    return {i["titulo"]: i for i in itens}


# ─────────────── 1. sem teto escondido, corpo de sempre ───────────────


async def test_lista_passa_de_200_e_o_total_vem_no_cabecalho(client: AsyncClient, db):
    """264 ideias visíveis em 13/10/2026; o teto de 200 escondia 64 sem aviso."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    db.add_all(
        MarketingRoteiro(
            titulo=f"Ideia {i:03d}",
            texto="briefing",
            ativo=True,
            created_at=base + timedelta(minutes=i),
        )
        for i in range(264)
    )
    await db.commit()
    r = await client.get("/api/portal/roteiros", headers=A)
    assert r.status_code == 200
    assert len(r.json()["roteiros"]) == 264
    assert r.headers["X-Total-Count"] == "264"
    # O corpo é o de antes (mais `entrega`): o portal no ar continua lendo.
    assert set(r.json()) == {"equipe", "roteiros"}

    p = await client.get("/api/portal/roteiros", headers=A, params={"limite": 50, "offset": 250})
    assert [x["titulo"] for x in p.json()["roteiros"]] == [
        f"Ideia {i:03d}" for i in range(13, -1, -1)
    ]
    assert p.headers["X-Total-Count"] == "264"
    for ruim in ({"limite": 0}, {"limite": 5001}, {"offset": -1}):
        assert (await client.get("/api/portal/roteiros", headers=A, params=ruim)).status_code == 422


async def test_resumo_corta_o_texto_so_na_lista(client: AsyncClient, db):
    longo = ("palavra " * 200).strip()
    r0 = await _ideia(db, texto=longo)
    curto = (await _lista(client, resumo=1))[0]["texto"]
    assert len(curto) <= rota.RESUMO_CARACTERES + 1
    assert curto.endswith("…")
    assert longo.startswith(curto[:-1])
    assert (await _lista(client))[0]["texto"] == longo
    d = (await client.get(f"/api/portal/roteiros/{r0.id}", headers=A)).json()["roteiro"]
    assert d["texto"] == longo, "o detalhe é sempre o texto inteiro"
    # Texto curto passa inteiro, sem reticências.
    await _ideia(db, titulo="curta", texto="curtinho")
    assert _por_titulo(await _lista(client, resumo=1))["curta"]["texto"] == "curtinho"


# ─────────────── 2. entrega por ideia raiz e por agência ───────────────


async def test_sem_video_entrega_e_nula(client: AsyncClient, db):
    r0 = await _ideia(db)
    # Linha aberta SEM arquivo (a que `_sincronizar_entregas` criava) não é
    # vídeo mandado.
    await _entrega(db, equipe="alpha", roteiro_id=r0.id, com_arquivo=False)
    assert (await _lista(client))[0]["entrega"] is None


async def test_entrega_e_da_agencia_do_token_e_a_outra_nao_ve(client: AsyncClient, db):
    r0 = await _ideia(db, destino=None)
    quando = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    await _entrega(db, equipe="alpha", roteiro_id=r0.id, enviado_em=quando)

    a = (await _lista(client, A))[0]["entrega"]
    assert a == {
        "estado": "em_analise",
        "videos": 1,
        "ultimo_em": quando.isoformat(),
        "publicado": False,
    }
    assert (await _lista(client, B))[0]["entrega"] is None, "a beta não vê o vídeo da alpha"
    d = (await client.get(f"/api/portal/roteiros/{r0.id}", headers=B)).json()["roteiro"]
    assert d["entrega"] is None


async def test_entrega_feita_pela_versao_aparece_na_ideia_e_na_versao(client: AsyncClient, db):
    raiz = await _ideia(db, titulo="Ideia da casa")
    versao = await _ideia(db, titulo="Leitura da alpha", destino="alpha", origem_id=raiz.id)
    e = await client.post(
        f"/api/portal/roteiros/{versao.id}/entrega",
        headers=A,
        files={"files": ("v.mp4", b"x" * 100, "video/mp4")},
    )
    assert e.status_code == 200
    itens = _por_titulo(await _lista(client))
    assert itens["Ideia da casa"]["entrega"]["videos"] == 1
    assert itens["Leitura da alpha"]["entrega"]["videos"] == 1
    d = (await client.get(f"/api/portal/roteiros/{versao.id}", headers=A)).json()["roteiro"]
    assert d["entrega"]["estado"] == "em_analise"


async def test_aprovado_vence_e_publicado_e_so_o_que_saiu(client: AsyncClient, db):
    r0 = await _ideia(db)
    t1 = datetime(2026, 10, 1, tzinfo=UTC)
    t2 = datetime(2026, 10, 5, tzinfo=UTC)
    await _entrega(db, equipe="alpha", roteiro_id=r0.id, aprovado=False, enviado_em=t1)
    c, f = await _entrega(db, equipe="alpha", roteiro_id=r0.id, aprovado=True, enviado_em=t2)
    await _publicar(db, c, f, "falhou")
    e = (await _lista(client))[0]["entrega"]
    assert e["estado"] == "aprovado"
    assert e["videos"] == 2
    assert e["ultimo_em"] == t2.isoformat()
    assert e["publicado"] is False, "postagem que falhou não é vídeo no ar"

    await _publicar(db, c, f, "publicado")
    assert (await _lista(client))[0]["entrega"]["publicado"] is True


async def test_so_recusado_e_recusado(client: AsyncClient, db):
    r0 = await _ideia(db)
    await _entrega(db, equipe="alpha", roteiro_id=r0.id, aprovado=False)
    assert (await _lista(client))[0]["entrega"]["estado"] == "recusado"


async def test_filtro_de_situacao(client: AsyncClient, db):
    nada = await _ideia(db, titulo="nada")
    analise = await _ideia(db, titulo="analise")
    aprovada = await _ideia(db, titulo="aprovada")
    recusada = await _ideia(db, titulo="recusada")
    mista = await _ideia(db, titulo="mista")
    await _entrega(db, equipe="alpha", roteiro_id=analise.id, aprovado=None)
    await _entrega(db, equipe="alpha", roteiro_id=aprovada.id, aprovado=True)
    await _entrega(db, equipe="alpha", roteiro_id=recusada.id, aprovado=False)
    # Com um aprovado, é "aprovada" — mesmo com outro vídeo esperando.
    await _entrega(db, equipe="alpha", roteiro_id=mista.id, aprovado=True)
    await _entrega(db, equipe="alpha", roteiro_id=mista.id, aprovado=None)
    # Linha vazia não faz a ideia "feita".
    await _entrega(db, equipe="alpha", roteiro_id=nada.id, com_arquivo=False)

    async def titulos(headers=A, **p):
        r = await client.get("/api/portal/roteiros", headers=headers, params=p)
        assert r.headers["X-Total-Count"] == str(len(r.json()["roteiros"]))
        return sorted(x["titulo"] for x in r.json()["roteiros"])

    assert await titulos(situacao="a_fazer") == ["nada"]
    assert await titulos(situacao="feitas") == ["analise", "aprovada", "mista", "recusada"]
    assert await titulos(situacao="em_analise") == ["analise"]
    assert await titulos(situacao="aprovadas") == ["aprovada", "mista"]
    assert len(await titulos(situacao="todas")) == 5
    # Para a beta, nada disso foi feito.
    assert await titulos(B, situacao="feitas") == []
    assert len(await titulos(B, situacao="a_fazer")) == 5
    r = await client.get("/api/portal/roteiros", headers=A, params={"situacao": "qualquer"})
    assert r.status_code == 422


async def test_filtro_de_personagem_e_sem_personagem(client: AsyncClient, db):
    livia = await _personagem(db, "Lívia")
    desligada = await _personagem(db, "Desligada", ativo=False)
    com_livia = await _ideia(db, titulo="com a Lívia")
    so_desligada = await _ideia(db, titulo="só a desligada")
    await _ideia(db, titulo="sem ninguém")
    db.add_all(
        [
            MarketingRoteiroPersonagem(roteiro_id=com_livia.id, personagem_id=livia.id),
            MarketingRoteiroPersonagem(roteiro_id=so_desligada.id, personagem_id=desligada.id),
        ]
    )
    await db.commit()

    assert [x["titulo"] for x in await _lista(client, personagem_id=str(livia.id))] == [
        "com a Lívia"
    ]
    # Personagem desligado não aparece no card; a ideia conta como "sem".
    assert sorted(x["titulo"] for x in await _lista(client, personagem_id="nenhum")) == [
        "sem ninguém",
        "só a desligada",
    ]
    assert await _lista(client, personagem_id=str(desligada.id)) == []
    r = await client.get("/api/portal/roteiros", headers=A, params={"personagem_id": "lívia"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "personagem_invalido"


async def test_filtro_desde_dias_e_busca(client: AsyncClient, db):
    agora = datetime.now(UTC)
    await _ideia(db, titulo="Antiga", sku="ABC123", criado_em=agora - timedelta(days=40))
    await _ideia(db, titulo="Nova", sku="dgd23", criado_em=agora - timedelta(days=2))
    await _ideia(db, titulo="Promo 50% off", sku=None, marca=None, texto="desconto")

    assert sorted(x["titulo"] for x in await _lista(client, desde_dias=30)) == [
        "Nova",
        "Promo 50% off",
    ]
    assert [x["titulo"] for x in await _lista(client, q="abc123")] == ["Antiga"]
    assert [x["titulo"] for x in await _lista(client, q="DESCONTO")] == ["Promo 50% off"]
    # "%" é letra, não curinga.
    assert [x["titulo"] for x in await _lista(client, q="50%")] == ["Promo 50% off"]
    assert [x["titulo"] for x in await _lista(client, q="dgd23", desde_dias=30)] == ["Nova"]
    r = await client.get("/api/portal/roteiros", headers=A, params={"desde_dias": 0})
    assert r.status_code == 422


# ─────────────── 3. ideia nova pelo portal ───────────────


async def test_cria_ideia_so_para_a_propria_agencia(client: AsyncClient, db):
    livia = await _personagem(db, "Lívia")
    r = await client.post(
        "/api/portal/roteiros",
        headers=A,
        json={
            "titulo": "  Mala na esteira  ",
            "texto": "0-3s: a mala cai da esteira e não abre.",
            "marca": "poofy",
            "sku": "dgd23",
            "personagem_ids": [str(livia.id), str(livia.id)],
        },
    )
    assert r.status_code == 201, r.text
    corpo = r.json()
    assert corpo["equipe"] == "alpha"
    ideia = corpo["roteiro"]
    assert ideia["titulo"] == "Mala na esteira"
    assert ideia["entrega"] is None
    assert [p["nome"] for p in ideia["personagens"]] == ["Lívia"]

    row = await db.get(MarketingRoteiro, UUID(ideia["id"]))
    assert row.equipe_destino == "alpha"
    assert row.ativo is True
    assert row.created_by is None
    assert row.origem_id is None
    # Mesmo formato do detalhe, e a beta não enxerga.
    d = await client.get(f"/api/portal/roteiros/{ideia['id']}", headers=A)
    assert d.json()["roteiro"] == ideia
    assert (await client.get(f"/api/portal/roteiros/{ideia['id']}", headers=B)).status_code == 404


async def test_cria_ideia_para_as_duas(client: AsyncClient, db):
    r = await client.post(
        "/api/portal/roteiros",
        headers=A,
        json={"titulo": "Para todo mundo", "texto": "briefing", "para": "todas"},
    )
    assert r.status_code == 201
    rid = r.json()["roteiro"]["id"]
    assert (await db.get(MarketingRoteiro, UUID(rid))).equipe_destino is None
    assert (await client.get(f"/api/portal/roteiros/{rid}", headers=B)).status_code == 200


async def test_ideia_nova_resolve_a_marca_pelo_texto(client: AsyncClient, db):
    from app.models.marca import Marca

    marca = Marca(nome="Charlot's", slug="charlots")
    db.add(marca)
    await db.commit()
    r = await client.post(
        "/api/portal/roteiros",
        headers=A,
        json={"titulo": "Com marca", "texto": "briefing", "marca": "charlots"},
    )
    assert r.status_code == 201
    row = await db.get(MarketingRoteiro, UUID(r.json()["roteiro"]["id"]))
    assert row.marca_id == marca.id


@pytest.mark.parametrize(
    ("corpo", "status", "code"),
    [
        ({"titulo": "   ", "texto": "x"}, 400, "titulo_obrigatorio"),
        ({"titulo": "x", "texto": "  \n "}, 400, "texto_obrigatorio"),
        ({"titulo": "x"}, 422, None),
        ({"titulo": "x" * 161, "texto": "x"}, 422, None),
        ({"titulo": "x", "texto": "x" * 20001}, 422, None),
        ({"titulo": "x", "texto": "x", "marca": "m" * 65}, 422, None),
        ({"titulo": "x", "texto": "x", "sku": "s" * 513}, 422, None),
        ({"titulo": "x", "texto": "x", "para": "beta"}, 422, None),
        ({"titulo": "x", "texto": "x", "personagem_ids": ["nao-e-uuid"]}, 422, None),
        (
            {"titulo": "x", "texto": "x", "personagem_ids": [str(uuid4()) for _ in range(6)]},
            422,
            None,
        ),
    ],
)
async def test_ideia_nova_validacoes(client: AsyncClient, db, corpo, status, code):
    r = await client.post("/api/portal/roteiros", headers=A, json=corpo)
    assert r.status_code == status, r.text
    if code:
        assert r.json()["detail"]["code"] == code
    assert (await db.execute(select(MarketingRoteiro))).scalars().all() == []


async def test_ideia_nova_com_personagem_desligado_e_recusada(client: AsyncClient, db):
    desligada = await _personagem(db, "Desligada", ativo=False)
    inventado = uuid4()
    r = await client.post(
        "/api/portal/roteiros",
        headers=A,
        json={"titulo": "x", "texto": "x", "personagem_ids": [str(desligada.id), str(inventado)]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "personagem_nao_encontrado"
    assert set(r.json()["detail"]["personagem_ids"]) == {str(desligada.id), str(inventado)}
    assert (await db.execute(select(MarketingRoteiro))).scalars().all() == []


async def test_ideia_nova_sem_token_401(client: AsyncClient):
    r = await client.post("/api/portal/roteiros", json={"titulo": "x", "texto": "x"})
    assert r.status_code == 401


async def test_trinta_ideias_por_minuto_por_agencia(client: AsyncClient, db, limite):
    corpo = {"titulo": "laço", "texto": "briefing"}
    for _ in range(rota.LIMITE_IDEIAS_MIN):
        assert (await client.post("/api/portal/roteiros", headers=A, json=corpo)).status_code == 201
    r = await client.post("/api/portal/roteiros", headers=A, json=corpo)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "limite_de_ideias"
    assert r.headers["Retry-After"] == "17"
    # O teto é por agência: a beta continua criando.
    assert (await client.post("/api/portal/roteiros", headers=B, json=corpo)).status_code == 201
    total = len((await db.execute(select(MarketingRoteiro))).scalars().all())
    assert total == rota.LIMITE_IDEIAS_MIN + 1


async def test_redis_fora_do_ar_nao_impede_criar(client: AsyncClient, monkeypatch):
    async def _caiu(**kw):
        raise ConnectionError("redis caiu")

    monkeypatch.setattr(rota, "sliding_window_check", _caiu)
    r = await client.post("/api/portal/roteiros", headers=A, json={"titulo": "x", "texto": "x"})
    assert r.status_code == 201


# ─────────────── 4. relatório ───────────────


async def test_relatorio_janela_faixas_mediana_e_ia(client: AsyncClient, db):
    agora = datetime.now(UTC)
    ideia = await _ideia(db, titulo="Ideia visível")
    so_beta = await _ideia(db, titulo="Só da beta", destino="beta")
    duracoes = ["10.00", "10.50", "15.00", "20.00", "30.00", "45.00", "46.00"]
    for i, d in enumerate(duracoes):
        await _entrega(
            db,
            equipe="alpha",
            roteiro_id=ideia.id,
            duracao=d,
            enviado_em=agora - timedelta(days=1, minutes=i),
            ia_modelo="Kling" if i < 4 else None,
            ia_creditos="12.50" if i == 0 else None,
        )
    # Sem duração (antes do backfill): conta como vídeo, não como zero.
    await _entrega(db, equipe="alpha", enviado_em=agora - timedelta(days=3), ia_modelo="Veo")
    # Fora da janela de 30, dentro da de 60.
    await _entrega(db, equipe="alpha", duracao="8.00", enviado_em=agora - timedelta(days=40))
    # Não é vídeo; e vídeo da outra agência.
    await _entrega(db, equipe="alpha", mime="image/png", nome="capa.png", duracao="1.00")
    await _entrega(db, equipe="beta", duracao="99.00")
    # Vídeo da alpha ligado a ideia que a alpha NÃO enxerga.
    await _entrega(
        db,
        equipe="alpha",
        roteiro_id=so_beta.id,
        duracao="12.00",
        enviado_em=agora - timedelta(days=2),
    )

    r = await client.get("/api/portal/relatorio", headers=A)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["dias"] == 30
    assert d["total_videos"] == 9
    assert d["sem_duracao"] == 1
    medidas = [10, 10.5, 15, 20, 30, 45, 46, 12]
    assert d["total_segundos"] == pytest.approx(sum(medidas))
    assert d["media_s"] == pytest.approx(round(sum(medidas) / len(medidas), 2))
    assert d["mediana_s"] == pytest.approx(17.5)
    assert d["faixas"] == {
        "ate_10": 1,
        "11_15": 3,
        "16_20": 1,
        "21_30": 1,
        "31_45": 1,
        "acima_45": 1,
    }
    assert d["por_ia"] == {"Kling": 4, "nao_informada": 4, "Veo": 1}
    assert list(d["por_ia"]) == ["Kling", "nao_informada", "Veo"]

    videos = d["videos"]
    assert [v["enviado_em"] for v in videos] == sorted(
        (v["enviado_em"] for v in videos), reverse=True
    ), "do mais novo para o mais antigo"
    primeiro = videos[0]
    assert primeiro["duracao_s"] == 10.0
    assert primeiro["ia_modelo"] == "Kling"
    assert primeiro["ia_creditos"] == 12.5
    assert primeiro["roteiro_id"] == str(ideia.id)
    assert primeiro["roteiro_titulo"] == "Ideia visível"
    assert set(primeiro) == {
        "creative_id",
        "arquivo_id",
        "titulo",
        "roteiro_id",
        "roteiro_titulo",
        "marca",
        "sku",
        "enviado_em",
        "duracao_s",
        "largura",
        "altura",
        "aprovado",
        "publicado",
        "ia_modelo",
        "ia_creditos",
    }
    escondida = next(v for v in videos if v["duracao_s"] == 12.0)
    assert escondida["roteiro_id"] is None and escondida["roteiro_titulo"] is None

    longa = (await client.get("/api/portal/relatorio", headers=A, params={"dias": 60})).json()
    assert longa["total_videos"] == 10
    vazio = (await client.get("/api/portal/relatorio", headers=B, params={"dias": 15})).json()
    assert vazio["total_videos"] == 1  # só o vídeo da própria beta
    for ruim in (0, 366):
        r = await client.get("/api/portal/relatorio", headers=A, params={"dias": ruim})
        assert r.status_code == 422


async def test_relatorio_sem_videos_e_publicado_por_arquivo(client: AsyncClient, db):
    vazio = (await client.get("/api/portal/relatorio", headers=A)).json()
    assert vazio["total_videos"] == 0
    assert vazio["media_s"] is None and vazio["mediana_s"] is None
    assert vazio["total_segundos"] == 0

    c, f = await _entrega(db, equipe="alpha", duracao="9.00")
    await _publicar(db, c, f, "publicado")
    v = (await client.get("/api/portal/relatorio", headers=A)).json()["videos"][0]
    assert v["publicado"] is True


async def test_relatorio_sem_token_401(client: AsyncClient):
    assert (await client.get("/api/portal/relatorio")).status_code == 401


# ─────────────── 5. duração e IA no envio ───────────────


async def test_entrega_grava_ia_e_duracao(client: AsyncClient, db, sonda):
    sonda["info"] = InfoVideo(largura=1080, altura=1920, duracao=12.5, codec="h264")
    r0 = await _ideia(db)
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/entrega",
        headers=A,
        data={"ia_modelo": " Kling ", "ia_creditos": "12,5"},
        files={"files": ("peca.mp4", b"bytes de video", "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ia_modelo"] == "Kling"
    assert r.json()["ia_creditos"] == 12.5
    row = (
        await db.execute(select(MarketingCreative).where(MarketingCreative.roteiro_id == r0.id))
    ).scalar_one()
    assert row.ia_modelo == "Kling"
    assert row.ia_creditos == Decimal("12.50")
    f = row.files[0]
    assert (f.duracao_s, f.largura, f.altura) == (Decimal("12.50"), 1080, 1920)

    lista = (await client.get("/api/portal/criativos", headers=A)).json()["criativos"][0]
    assert (lista["ia_modelo"], lista["ia_creditos"]) == ("Kling", 12.5)


async def test_ia_vazia_e_nula_e_ffprobe_que_falha_nao_barra(client: AsyncClient, db, sonda):
    sonda["erro"] = RuntimeError("ffprobe explodiu")
    r0 = await _ideia(db)
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/entrega",
        headers=A,
        data={"ia_modelo": "", "ia_creditos": "  "},
        files={"files": ("peca.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert sonda["chamadas"] == 1
    row = (
        await db.execute(select(MarketingCreative).where(MarketingCreative.roteiro_id == r0.id))
    ).scalar_one()
    assert row.ia_modelo is None and row.ia_creditos is None
    assert row.files[0].duracao_s is None and row.files[0].largura is None


@pytest.mark.parametrize(
    ("dados", "code"),
    [
        ({"ia_creditos": "abc"}, "ia_creditos_invalido"),
        ({"ia_creditos": "-1"}, "ia_creditos_invalido"),
        ({"ia_creditos": "1.234"}, "ia_creditos_invalido"),
        ({"ia_creditos": "NaN"}, "ia_creditos_invalido"),
        ({"ia_creditos": "100000000"}, "ia_creditos_invalido"),
        ({"ia_modelo": "m" * 65}, "ia_modelo_invalido"),
    ],
)
async def test_ia_invalida_recusa_antes_de_gravar(client: AsyncClient, db, dados, code):
    r0 = await _ideia(db)
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/entrega",
        headers=A,
        data=dados,
        files={"files": ("peca.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == code
    assert (await db.execute(select(MarketingCreative))).scalars().all() == []


async def test_reenvio_sem_ia_nao_apaga_o_que_ja_foi_dito(client: AsyncClient, db):
    c, _ = await _entrega(
        db, equipe="alpha", com_arquivo=False, ia_modelo="Veo", ia_creditos="3.00"
    )
    r = await client.post(
        f"/api/portal/criativos/{c.id}/arquivo",
        headers=A,
        files={"files": ("novo.mp4", b"x" * 10, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["ia_modelo"], r.json()["ia_creditos"]) == ("Veo", 3.0)
    r = await client.post(
        f"/api/portal/criativos/{c.id}/arquivo",
        headers=A,
        data={"ia_modelo": "Sora", "ia_creditos": "0"},
        files={"files": ("outro.mp4", b"y" * 10, "video/mp4")},
    )
    assert (r.json()["ia_modelo"], r.json()["ia_creditos"]) == ("Sora", 0.0)


async def test_video_autoral_grava_ia(client: AsyncClient, db, sonda):
    sonda["info"] = InfoVideo(largura=720, altura=1280, duracao=None, codec="h264")
    r = await client.post(
        "/api/portal/criativos/proposta",
        headers=A,
        data={"titulo": "Meu vídeo", "ia_modelo": "Higgsfield", "ia_creditos": "40"},
        files={"files": ("meu.mp4", b"z" * 10, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    row = await db.get(MarketingCreative, UUID(r.json()["id"]))
    assert (row.ia_modelo, row.ia_creditos) == ("Higgsfield", Decimal("40.00"))
    # Sem duração no cabeçalho: a resolução entra, a duração fica NULL.
    assert (row.files[0].largura, row.files[0].duracao_s) == (720, None)


async def test_upload_interno_tambem_mede(client: AsyncClient, db, sonda, auth_as, make_user):
    sonda["info"] = InfoVideo(largura=1080, altura=1920, duracao=7.25, codec="h264")
    auth_as(await make_user(role=UserRole.ADMIN))
    c, _ = await _entrega(db, equipe="alpha", com_arquivo=False)
    r = await client.post(
        f"/api/marketing/creatives/{c.id}/arquivo",
        files={"files": ("interno.mp4", b"x" * 10, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    f = (
        await db.execute(
            select(MarketingCreativeFile).where(MarketingCreativeFile.creative_id == c.id)
        )
    ).scalar_one()
    assert (f.duracao_s, f.largura, f.altura) == (Decimal("7.25"), 1080, 1920)


async def test_anexo_que_nao_e_video_nao_chama_o_ffprobe(client: AsyncClient, db, sonda):
    c, _ = await _entrega(db, equipe="alpha", com_arquivo=False)
    r = await client.post(
        f"/api/portal/criativos/{c.id}/arquivo",
        headers=A,
        files={"files": ("capa.png", b"\x89PNG", "image/png")},
    )
    assert r.status_code == 200
    assert sonda["chamadas"] == 0


def test_eh_video_pelo_tipo_ou_pela_extensao():
    assert medidas_video.eh_video("video/mp4", "a.bin")
    assert medidas_video.eh_video("application/octet-stream", "Clip.MOV")
    assert not medidas_video.eh_video("image/png", "capa.png")
    assert not medidas_video.eh_video(None, None)


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="sem ffmpeg nesta máquina"
)
async def test_ffprobe_de_verdade_mede_um_mp4(db, monkeypatch, tmp_path):
    """A sonda REAL (sem o dublê), num MP4 de 1 s gerado aqui."""
    from app.services.marketing import shopee_video_anuncio

    monkeypatch.setattr(medidas_video, "sondar_video", shopee_video_anuncio.sondar_video)
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    c, _ = await _entrega(db, equipe="alpha", com_arquivo=False)
    destino = tmp_path / "creatives" / str(c.id) / "real.mp4"
    destino.parent.mkdir(parents=True)
    subprocess.run(  # noqa: S603 — ffmpeg local, arquivo do próprio teste
        [  # noqa: S607
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x128:d=1",
            "-pix_fmt",
            "yuv420p",
            str(destino),
        ],
        check=True,
    )
    rec = MarketingCreativeFile(
        creative_id=c.id,
        file_name="real.mp4",
        file_mime="video/mp4",
        file_rel=f"creatives/{c.id}/real.mp4",
    )
    assert await medidas_video.medir(rec) is True
    assert (rec.largura, rec.altura) == (64, 128)
    assert rec.duracao_s == Decimal("1.00")


# ─────────────── backfill ───────────────


async def test_backfill_mede_so_o_que_falta_e_e_idempotente(db, sonda, monkeypatch, tmp_path):
    from scripts import creatives_duracao_backfill as backfill

    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    sonda["info"] = InfoVideo(largura=1080, altura=1920, duracao=21.0, codec="h264")
    _, no_disco = await _entrega(db, equipe="alpha")
    _, sumido = await _entrega(db, equipe="alpha", nome="sumiu.mp4")
    _, ja_medido = await _entrega(db, equipe="alpha", duracao="5.00", nome="ok.mp4")
    await _entrega(db, equipe="alpha", mime="image/png", nome="capa.png")
    for rec in (no_disco, ja_medido):
        caminho = tmp_path / rec.file_rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(b"x")

    seco = await backfill.rodar(seco=True)
    assert seco == {
        "dry_run": True,
        "candidatos": 2,
        "com_arquivo": 1,
        "sem_arquivo": 1,
        "medidos": 0,
        "nao_lidos": 0,
    }
    assert sonda["chamadas"] == 0
    await db.refresh(no_disco)
    assert no_disco.duracao_s is None, "--dry-run não grava"

    feito = await backfill.rodar(seco=False)
    assert (feito["medidos"], feito["sem_arquivo"]) == (1, 1)
    await db.refresh(no_disco)
    await db.refresh(ja_medido)
    assert no_disco.duracao_s == Decimal("21.00")
    assert ja_medido.duracao_s == Decimal("5.00")

    de_novo = await backfill.rodar(seco=False)
    assert (de_novo["candidatos"], de_novo["medidos"]) == (1, 0)
    assert sumido.id  # o sem arquivo continua candidato, sem estrago


# ─────────────── a migration ───────────────


def _carregar_0392():
    caminho = _ALEMBIC / "versions" / "0392_criativos_duracao_ia.py"
    spec = importlib.util.spec_from_file_location("migration_0392_teste", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_0392_uma_ponta_so_e_depois_da_0391():
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    nossa = scripts.get_revision("0392_criativos_duracao_ia")
    assert nossa.down_revision == "0391_email_fora_do_chat"


_COLUNAS_0392 = {
    ("marketing_creative_files", "duracao_s"),
    ("marketing_creative_files", "largura"),
    ("marketing_creative_files", "altura"),
    ("marketing_creatives", "ia_modelo"),
    ("marketing_creatives", "ia_creditos"),
}


async def _colunas(db: AsyncSession, schema: str) -> dict[tuple[str, str], tuple]:
    linhas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale,"
                " character_maximum_length, is_nullable, column_default"
                " FROM information_schema.columns WHERE table_schema = :s"
                " AND table_name IN ('marketing_creative_files', 'marketing_creatives')"
            ),
            {"s": schema},
        )
    ).all()
    return {(r[0], r[1]): tuple(r[2:]) for r in linhas if (r[0], r[1]) in _COLUNAS_0392}


async def test_0392_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    nossa = _carregar_0392()
    rascunho = f"{Base.metadata.schema}_mig0392"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o esqueleto das duas tabelas: a 0392 só acrescenta coluna.
    await db.execute(text(f'CREATE TABLE "{rascunho}".marketing_creatives (id uuid PRIMARY KEY)'))
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".marketing_creative_files (id uuid PRIMARY KEY)')
    )
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(nossa, passo)()

    nossa.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        da_migration = await _colunas(db, rascunho)
        do_model = await _colunas(db, Base.metadata.schema)
        assert set(da_migration) == _COLUNAS_0392
        assert da_migration == do_model
        assert da_migration[("marketing_creative_files", "duracao_s")][:3] == ("numeric", 8, 2)
        assert da_migration[("marketing_creatives", "ia_creditos")][:3] == ("numeric", 10, 2)
        assert da_migration[("marketing_creatives", "ia_modelo")][3] == 64
        assert all(v[4] == "YES" and v[5] is None for v in da_migration.values())

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert await _colunas(db, rascunho) == {}
    finally:
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
