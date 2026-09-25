"""A fila do robô de autopostagem — o vídeo que fura a fila.

Eduardo, 25/09/2026: "em criativos deveria dar pra ordenar a sequencia dos
videos pq dai eu ia querer um video mais antigo rodasse antes".

O robô tira o aprovado MAIS RECENTE que ainda não saiu na conta ("tacamos do
mais recente"). Isso continua sendo o padrão; `fila_posicao` é a exceção
escolhida à mão. O que estes testes defendem:

  - FURA A FILA DE VERDADE. O antigo priorizado sai antes do novo, e entre
    os priorizados manda o número.
  - É POR CONTA. Saiu no TikTok, o robô volta ao mais recente ali — e no
    Instagram o priorizado continua na frente até sair lá.
  - A TELA E O ROBÔ CONCORDAM. GET /fila usa a mesma ordem do robô; se cada
    lado tivesse a sua, a tela prometeria um vídeo e o robô postaria outro.
  - O PUT SÓ ACEITA O QUE O ROBÔ PUBLICARIA: da marca e aprovado.
  - O SELO NÃO MENTE. O que já saiu em todas as contas não aparece como
    "#1 na fila".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingPostagem,
    RedeSocial,
    UserRole,
)
from app.services.marketing import autopostagem as svc
from app.services.marketing.postagens import BRT

pytestmark = pytest.mark.asyncio

API = "/api/marketing/creatives"
AGORA = datetime.now(UTC)


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    """`app/main.py` só inclui os routers de Marketing com `enable_marketing`
    ligado; sem isto todo GET/PUT daqui viraria 404 e o arquivo passaria
    sem provar nada."""
    from app.main import app
    from app.routers import marketing_creatives

    if not any(getattr(r, "path", "").startswith(API) for r in app.routes):
        app.include_router(marketing_creatives.router)


@pytest.fixture(autouse=True)
def _uploads(tmp_path, monkeypatch):
    """A guarda `arquivo_sumiu` do `agendar()` lê o vídeo do disco."""
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


# ─────────────────────────────────────────────────────────────── sementes


def _rede(marca: Marca, **kw) -> RedeSocial:
    base = {
        "marca_id": marca.id, "plataforma": "tiktok", "conta": "tt", "ativo": True,
        "adspower_user_id": "k1h9eqn7", "postagem_auto": True,
        "postagem_hora_inicio": 18, "postagem_max_dia": 2, "postagem_intervalo_min": 60,
    }
    return RedeSocial(**{**base, **kw})


async def _marca(db: AsyncSession, nome: str = "Uranyx") -> Marca:
    m = Marca(nome=nome, slug=nome.lower())
    db.add(m)
    await db.commit()
    return m


async def _criativo(
    db: AsyncSession,
    marca: Marca,
    *,
    nome: str,
    dias: float = 0,
    aprovado: bool | None = True,
    fila: int | None = None,
    equipe: str | None = None,
) -> tuple[MarketingCreative, MarketingCreativeFile]:
    quando = AGORA - timedelta(days=dias)
    c = MarketingCreative(
        modelo=nome, marca=marca.slug, marca_id=marca.id, aprovado=aprovado,
        fila_posicao=fila, equipe=equipe,
    )
    c.created_at = quando
    db.add(c)
    await db.flush()
    rel = f"x/{c.id}.mp4"
    caminho = Path(get_settings().uploads_dir) / rel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"\x00\x00\x00\x18ftypmp42-fake")
    f = MarketingCreativeFile(
        creative_id=c.id, file_name=f"{nome}.mp4", file_mime="video/mp4", file_rel=rel,
    )
    f.created_at = quando
    db.add(f)
    await db.commit()
    return c, f


async def _saiu(db: AsyncSession, c, f, rede: RedeSocial, status: str = "publicado") -> None:
    db.add(MarketingPostagem(
        creative_id=c.id, file_id=f.id, rede_social_id=rede.id,
        plataforma=rede.plataforma, conta=rede.conta, status=status,
        publicado_em=AGORA - timedelta(minutes=30),
    ))
    await db.commit()


async def _pos(db: AsyncSession, c: MarketingCreative) -> int | None:
    return await db.scalar(
        select(MarketingCreative.fila_posicao)
        .where(MarketingCreative.id == c.id)
        .execution_options(populate_existing=True)
    )


# ─────────────────────────────────────────────────────── o robô escolhe


async def test_antigo_priorizado_sai_antes_do_novo(db):
    m = await _marca(db)
    r = _rede(m)
    db.add(r)
    await db.commit()
    _, f_antigo = await _criativo(db, m, nome="antigo", dias=10, fila=1)
    await _criativo(db, m, nome="novo", dias=0)

    achado = await svc.proximo_criativo(db, r)
    assert achado is not None and achado[1].id == f_antigo.id, "furou a fila"


async def test_sem_ninguem_priorizado_continua_o_mais_recente(db):
    """No dia do deploy a coluna nasce NULL em tudo: o robô escolhe o mesmo
    que escolhia ontem."""
    m = await _marca(db)
    r = _rede(m)
    db.add(r)
    await db.commit()
    await _criativo(db, m, nome="antigo", dias=10)
    _, f_novo = await _criativo(db, m, nome="novo", dias=0)

    achado = await svc.proximo_criativo(db, r)
    assert achado is not None and achado[1].id == f_novo.id


async def test_entre_os_priorizados_manda_o_numero(db):
    m = await _marca(db)
    r = _rede(m)
    db.add(r)
    await db.commit()
    c_seg, f_seg = await _criativo(db, m, nome="segundo", dias=3, fila=2)
    c_pri, f_pri = await _criativo(db, m, nome="primeiro", dias=9, fila=1)
    c_nov, f_nov = await _criativo(db, m, nome="novo", dias=0)

    ordem = []
    for _ in range(3):
        achado = await svc.proximo_criativo(db, r)
        assert achado is not None
        ordem.append(achado[0].modelo)
        await _saiu(db, achado[0], achado[1], r)
    assert ordem == ["primeiro", "segundo", "novo"]
    assert await svc.proximo_criativo(db, r) is None


async def test_saiu_numa_conta_volta_ao_mais_recente_la_e_segue_na_frente_na_outra(db):
    """A fila é por conta. O priorizado que já saiu no TikTok não prende o
    TikTok — e no Instagram continua na frente até sair lá."""
    m = await _marca(db)
    tt = _rede(m, conta="uranyx_tt")
    ig = _rede(m, plataforma="instagram", conta="uranyx_ig", adspower_user_id=None)
    db.add_all([tt, ig])
    await db.commit()
    c_ant, f_ant = await _criativo(db, m, nome="antigo", dias=10, fila=1)
    _, f_nov = await _criativo(db, m, nome="novo", dias=0)
    await _saiu(db, c_ant, f_ant, tt)

    no_tt = await svc.proximo_criativo(db, tt)
    no_ig = await svc.proximo_criativo(db, ig)
    assert no_tt is not None and no_tt[1].id == f_nov.id, "no TikTok volta ao mais recente"
    assert no_ig is not None and no_ig[1].id == f_ant.id, "no Instagram segue na frente"


async def test_priorizado_nao_aprovado_nao_fura_nada(db):
    """A posição só vale pra quem o robô publicaria. Reprovado com número
    velho no banco não pode voltar pela porta da fila."""
    m = await _marca(db)
    r = _rede(m)
    db.add(r)
    await db.commit()
    await _criativo(db, m, nome="reprovado", dias=10, aprovado=False, fila=1)
    await _criativo(db, m, nome="pendente", dias=9, aprovado=None, fila=2)
    _, f_nov = await _criativo(db, m, nome="novo", dias=0)

    achado = await svc.proximo_criativo(db, r)
    assert achado is not None and achado[1].id == f_nov.id


async def test_rodada_agenda_o_priorizado(db):
    """De ponta a ponta: a rodada das 18h30 agenda o antigo que furou a fila,
    não o mais recente."""
    from app.models import MarketingLegendaModelo

    m = await _marca(db)
    r = _rede(m, postagem_max_dia=1)
    db.add(r)
    db.add(MarketingLegendaModelo(marca_id=m.id, texto="Legenda da {{ marca }}"))
    await db.commit()
    _, f_ant = await _criativo(db, m, nome="antigo", dias=10, fila=1)
    await _criativo(db, m, nome="novo", dias=0)

    agora = AGORA.astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    assert (await svc.rodada(db, agora=agora))["agendadas"] == 1
    linha = (await db.execute(select(MarketingPostagem))).scalars().one()
    assert linha.file_id == f_ant.id


async def test_priorizado_que_sumiu_do_disco_nao_trava_a_conta(db):
    """O vídeo antigo passado na frente é o que mais tende a ter sumido do
    disco. Com `break` na recusa, `fila_posicao` o devolvia primeiro em toda
    rodada e a conta nunca mais postava nada — o "mais recente" que o robô
    recusa é desbancado pelo próximo vídeo novo; o priorizado, não."""
    from app.models import MarketingLegendaModelo

    m = await _marca(db)
    r = _rede(m, postagem_max_dia=1)
    db.add(r)
    db.add(MarketingLegendaModelo(marca_id=m.id, texto="Legenda da {{ marca }}"))
    await db.commit()
    _, f_velho = await _criativo(db, m, nome="velho", dias=30, fila=1)
    _, f_novo = await _criativo(db, m, nome="novo", dias=1)
    (Path(get_settings().uploads_dir) / f_velho.file_rel).unlink()

    agora = AGORA.astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    saida = await svc.rodada(db, agora=agora)
    assert saida["agendadas"] == 1 and saida["recusadas"] == 1
    linha = (await db.execute(select(MarketingPostagem))).scalars().one()
    assert linha.file_id == f_novo.id, "pulou o que sumiu e agendou o seguinte"


async def test_recusa_de_toda_a_marca_nao_varre_a_fila_inteira(db):
    """Marca sem legenda cadastrada: TODO vídeo é recusado por `sem_legenda`.
    Pular vídeo a vídeo tem teto — senão cada passada tentaria a fila toda."""
    m = await _marca(db)
    r = _rede(m, postagem_max_dia=1)
    db.add(r)
    await db.commit()
    for i in range(svc.MAX_PULOS_POR_CONTA + 3):
        await _criativo(db, m, nome=f"v{i}", dias=i)

    agora = AGORA.astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    saida = await svc.rodada(db, agora=agora)
    assert saida["agendadas"] == 0
    assert saida["recusadas"] == svc.MAX_PULOS_POR_CONTA + 1
    assert saida["sem_criativo"] == 0
    assert (await db.execute(select(MarketingPostagem))).first() is None


# ─────────────────────────────────────────────────────── GET /fila


async def test_get_fila_na_ordem_do_robo_com_pendencias(client: AsyncClient, db, admin):
    m = await _marca(db)
    tt = _rede(m, conta="uranyx_tt")
    ig = _rede(m, plataforma="instagram", conta="uranyx_ig", adspower_user_id=None)
    # Conta sem o robô ligado NÃO entra: a fila é a dele.
    fb = _rede(m, plataforma="facebook", conta="uranyx_fb", postagem_auto=False)
    db.add_all([tt, ig, fb])
    await db.commit()
    c_ant, f_ant = await _criativo(db, m, nome="antigo", dias=10, fila=1)
    c_meio, f_meio = await _criativo(db, m, nome="meio", dias=5)
    c_nov, f_nov = await _criativo(db, m, nome="novo", dias=1)
    await _criativo(db, m, nome="reprovado", dias=0, aprovado=False)
    # "meio" já saiu no TikTok (cancelado conta: o robô não repropõe) e
    # "novo" já saiu nas duas — some da fila.
    await _saiu(db, c_meio, f_meio, tt, status="cancelado")
    await _saiu(db, c_nov, f_nov, tt)
    await _saiu(db, c_nov, f_nov, ig)

    resp = await client.get(f"{API}/fila", params={"marca_id": str(m.id)})
    assert resp.status_code == 200, resp.text
    fila = resp.json()
    assert [x["modelo"] for x in fila] == ["antigo", "meio"]
    ant, meio = fila
    assert ant["creative_id"] == str(c_ant.id) and ant["file_id"] == str(f_ant.id)
    assert ant["fila_posicao"] == 1 and meio["fila_posicao"] is None
    assert ant["file_name"] == "antigo.mp4"
    assert ant["created_at"]
    assert {"sku", "equipe"} <= ant.keys()
    assert [p["conta"] for p in ant["pendente_em"]] == ["uranyx_ig", "uranyx_tt"]
    assert meio["pendente_em"] == [
        {"rede_id": str(ig.id), "plataforma": "instagram", "conta": "uranyx_ig"}
    ]
    assert ant["enviado_em"], "quando o VÍDEO chegou, além da data da linha"
    assert ant["bloqueado"] is None and meio["bloqueado"] is None


async def test_get_fila_avisa_o_video_que_o_robo_vai_pular(client: AsyncClient, db, admin):
    """O priorizado que sumiu do disco continua na posição (tirar da fila é
    decisão de gente), mas a tela diz que o robô vai pulá-lo."""
    m = await _marca(db)
    db.add(_rede(m))
    await db.commit()
    _, f_velho = await _criativo(db, m, nome="velho", dias=30, fila=1)
    await _criativo(db, m, nome="novo", dias=1)
    (Path(get_settings().uploads_dir) / f_velho.file_rel).unlink()

    fila = (await client.get(f"{API}/fila", params={"marca_id": str(m.id)})).json()
    assert [(x["modelo"], x["bloqueado"]) for x in fila] == [
        ("velho", "arquivo_sumiu"),
        ("novo", None),
    ]


async def test_get_fila_e_o_robo_concordam(client: AsyncClient, db, admin):
    """O primeiro da tela é o que o robô tira — a cada passo."""
    m = await _marca(db)
    r = _rede(m)
    db.add(r)
    await db.commit()
    await _criativo(db, m, nome="a", dias=8, fila=2)
    await _criativo(db, m, nome="b", dias=6)
    await _criativo(db, m, nome="c", dias=4, fila=1)
    await _criativo(db, m, nome="d", dias=2)

    vistos = []
    while True:
        tela = (await client.get(f"{API}/fila", params={"marca_id": str(m.id)})).json()
        achado = await svc.proximo_criativo(db, r)
        if not tela:
            assert achado is None
            break
        assert achado is not None and tela[0]["file_id"] == str(achado[1].id)
        vistos.append(tela[0]["modelo"])
        await _saiu(db, achado[0], achado[1], r)
    assert vistos == ["c", "a", "d", "b"]


async def test_get_fila_sem_robo_ligado_cai_nas_contas_ativas(client: AsyncClient, db, admin):
    """A marca que ainda não ligou o robô também precisa ver e arrumar a fila
    antes de ligar. Sem conta do robô, valem as ativas das plataformas que
    ele sabe publicar — twitter e conta inativa ficam de fora."""
    m = await _marca(db)
    ig = _rede(m, plataforma="instagram", conta="ig", postagem_auto=False)
    db.add_all([
        ig,
        _rede(m, plataforma="twitter", conta="tw", postagem_auto=False),
        _rede(m, plataforma="tiktok", conta="inativa", ativo=False),
    ])
    await db.commit()
    await _criativo(db, m, nome="a", dias=1)

    fila = (await client.get(f"{API}/fila", params={"marca_id": str(m.id)})).json()
    assert len(fila) == 1
    assert [p["conta"] for p in fila[0]["pendente_em"]] == ["ig"]


async def test_get_fila_respeita_a_equipe(client: AsyncClient, db, make_user, auth_as):
    m = await _marca(db)
    db.add(_rede(m))
    await db.commit()
    await _criativo(db, m, nome="deles", dias=5, fila=1, equipe="Beta")
    await _criativo(db, m, nome="meu", dias=1, fila=2, equipe="Alpha")
    u = await make_user(permissions={"marketing_criativos": {"view": True}})
    u.marketing_teams = ["alpha"]
    await db.commit()
    auth_as(u)

    fila = (await client.get(f"{API}/fila", params={"marca_id": str(m.id)})).json()
    assert [x["modelo"] for x in fila] == ["meu"]
    assert fila[0]["fila_posicao"] == 2, "a posição é a do robô, contada na marca inteira"


# ─────────────────────────────────────────────────────── PUT /fila


async def test_put_grava_a_ordem_e_zera_quem_ficou_de_fora(client: AsyncClient, db, admin):
    m = await _marca(db)
    outra = await _marca(db, "Poofy")
    a, _ = await _criativo(db, m, nome="a", dias=5)
    b, _ = await _criativo(db, m, nome="b", dias=3)
    velho, _ = await _criativo(db, m, nome="velho", dias=9, fila=1)
    alheio, _ = await _criativo(db, outra, nome="alheio", dias=2, fila=1)

    resp = await client.put(
        f"{API}/fila", json={"marca_id": str(m.id), "ids": [str(b.id), str(a.id), str(b.id)]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ids": [str(b.id), str(a.id)]}, "repetido entra uma vez só"
    assert await _pos(db, b) == 1
    assert await _pos(db, a) == 2
    assert await _pos(db, velho) is None, "ficou de fora volta pra ordem normal"
    assert await _pos(db, alheio) == 1, "a fila de outra marca não é tocada"


async def test_put_vazio_limpa_a_fila(client: AsyncClient, db, admin):
    m = await _marca(db)
    a, _ = await _criativo(db, m, nome="a", dias=5, fila=1)
    b, _ = await _criativo(db, m, nome="b", dias=3, fila=2)

    resp = await client.put(f"{API}/fila", json={"marca_id": str(m.id), "ids": []})
    assert resp.status_code == 200 and resp.json() == {"ids": []}
    assert await _pos(db, a) is None and await _pos(db, b) is None


async def test_put_recusa_criativo_de_outra_marca(client: AsyncClient, db, admin):
    m = await _marca(db)
    outra = await _marca(db, "Poofy")
    meu, _ = await _criativo(db, m, nome="meu", dias=1, fila=1)
    alheio, _ = await _criativo(db, outra, nome="alheio", dias=1)

    resp = await client.put(
        f"{API}/fila", json={"marca_id": str(m.id), "ids": [str(alheio.id)]}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "criativo_de_outra_marca"
    assert await _pos(db, meu) == 1, "recusa não mexe em nada"
    assert await _pos(db, alheio) is None


@pytest.mark.parametrize("aprovado", [None, False])
async def test_put_recusa_nao_aprovado(client: AsyncClient, db, admin, aprovado):
    m = await _marca(db)
    c, _ = await _criativo(db, m, nome="x", dias=1, aprovado=aprovado)
    resp = await client.put(f"{API}/fila", json={"marca_id": str(m.id), "ids": [str(c.id)]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "criativo_nao_aprovado"


async def test_put_marca_inexistente_da_404(client: AsyncClient, db, admin):
    resp = await client.put(
        f"{API}/fila",
        json={"marca_id": "00000000-0000-0000-0000-000000000000", "ids": []},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "marca_nao_encontrada"


async def test_put_exige_edit(client: AsyncClient, db, make_user, auth_as):
    m = await _marca(db)
    u = await make_user(permissions={"marketing_criativos": {"view": True}})
    auth_as(u)
    resp = await client.put(f"{API}/fila", json={"marca_id": str(m.id), "ids": []})
    assert resp.status_code == 403


async def test_put_de_quem_so_ve_a_equipe_nao_apaga_a_fila_dos_outros(
    client: AsyncClient, db, make_user, auth_as
):
    """Quem não vê não tira da fila: o priorizado de outra equipe fica, logo
    atrás dos que o usuário ordenou."""
    m = await _marca(db)
    deles, _ = await _criativo(db, m, nome="deles", dias=5, fila=1, equipe="Beta")
    meu, _ = await _criativo(db, m, nome="meu", dias=1, equipe="Alpha")
    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    u.marketing_teams = ["alpha"]
    await db.commit()
    auth_as(u)

    resp = await client.put(f"{API}/fila", json={"marca_id": str(m.id), "ids": [str(meu.id)]})
    assert resp.status_code == 200, resp.text
    assert await _pos(db, meu) == 1
    assert await _pos(db, deles) == 2

    fora = await client.put(f"{API}/fila", json={"marca_id": str(m.id), "ids": [str(deles.id)]})
    assert fora.status_code == 403


# ─────────────────────────────────────────────── a listagem e o ciclo de vida


async def test_listagem_traz_marca_id_e_posicao_efetiva(client: AsyncClient, db, admin):
    """O selo "#n na fila" é a posição que o robô vai seguir. O priorizado
    que já saiu em todas as contas some do selo, e o de trás vira #1."""
    m = await _marca(db)
    r = _rede(m)
    db.add(r)
    await db.commit()
    c_saiu, f_saiu = await _criativo(db, m, nome="ja_saiu", dias=9, fila=1)
    c_prox, _ = await _criativo(db, m, nome="proximo", dias=7, fila=2)
    c_normal, _ = await _criativo(db, m, nome="normal", dias=1)
    await _saiu(db, c_saiu, f_saiu, r)

    linhas = {x["modelo"]: x for x in (await client.get(API)).json()}
    assert linhas["proximo"]["marca_id"] == str(m.id)
    assert linhas["ja_saiu"]["fila_posicao"] is None
    assert linhas["proximo"]["fila_posicao"] == 1
    assert linhas["normal"]["fila_posicao"] is None


async def test_reprovar_tira_da_fila(client: AsyncClient, db, admin):
    m = await _marca(db)
    db.add(_rede(m))
    await db.commit()
    c, _ = await _criativo(db, m, nome="x", dias=3, fila=1)

    resp = await client.post(f"{API}/{c.id}/aprovar", json={"aprovado": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["fila_posicao"] is None
    assert await _pos(db, c) is None, "reaprovado não volta sozinho pra frente"


async def test_trocar_de_marca_tira_da_fila(client: AsyncClient, db, admin):
    m = await _marca(db)
    await _marca(db, "Poofy")
    db.add(_rede(m))
    await db.commit()
    c, _ = await _criativo(db, m, nome="x", dias=3, fila=1)

    mesma = await client.patch(f"{API}/{c.id}", json={"marca": "URANYX"})
    assert mesma.status_code == 200, mesma.text
    assert mesma.json()["fila_posicao"] == 1, "mesma marca (outra grafia) não mexe"

    resp = await client.patch(f"{API}/{c.id}", json={"marca": "poofy"})
    assert resp.status_code == 200, resp.text
    assert await _pos(db, c) is None
