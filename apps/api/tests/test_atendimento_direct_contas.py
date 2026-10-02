"""O Direct do Instagram SEPARADO POR CONTA (frente C, 02/10/2026).

Antes a barra de lojas tinha UMA linha "Direct" juntando 7buyers, Charlots e
Uranyx. Agora:

- `instagram.contar_por_conta`: as contagens (aguardando, vencendo, vencidas,
  total, abertas) de cada conta, com o @ do CADASTRO (a conversa guarda o do
  dia em que nasceu) e as DMs de conta que saiu do cadastro numa linha só;
- /resumo: uma linha por conta (a bolinha = as esperando da conta), somando
  os comentários da MESMA conta; o total do Instagram é a soma das contas;
  quem não vê todas as equipes não vê o Direct;
- /conversas?rede_social_id=: só as da conta (Direct e comentários), em
  qualquer filtro, na paginação por recência e pelo prazo;
- a conta vai com o @ na lista e no detalhe ("@charlots_br"), e a busca por
  "@charlots" acha;
- caixa de comentários sem permissão (token sem o escopo novo) não apaga o
  Direct: a linha continua com a contagem do Direct (a tela a deixa acesa).

O adaptador continua SÓ LEITURA: nada aqui grava em `dm_*`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DmConversa, DmMensagem, User
from app.models.marca import Marca, RedeSocial
from app.routers import atendimento as rota
from app.services.atendimento import canais_externos, gravar, instagram
from app.services.atendimento.constantes import CANAL_COMENTARIO

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    """A caixa é só admin (rota.SO_ADMIN); os testes usam o recurso fino, como os outros."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


async def _rede(db: AsyncSession, marca: str, plataforma: str, conta: str) -> RedeSocial:
    m = (await db.execute(select(Marca).where(Marca.slug == marca))).scalar_one_or_none()
    if m is None:
        m = Marca(nome=marca.capitalize(), slug=marca)
        db.add(m)
        await db.flush()
    r = RedeSocial(marca_id=m.id, plataforma=plataforma, conta=conta)
    db.add(r)
    await db.commit()
    return r


async def _dm(
    db: AsyncSession,
    rede: RedeSocial | None,
    conta: str,
    quem: str,
    *,
    cliente_ha: timedelta,
    loja_ha: timedelta | None = None,
    status: str = "aberta",
) -> DmConversa:
    """Uma DM: a mensagem do cliente há `cliente_ha` e, se houver, o eco da loja."""
    c = DmConversa(
        rede_social_id=rede.id if rede else None,
        conta=conta,
        participante_id=quem,
        participante_nome=quem,
        status=status,
    )
    db.add(c)
    await db.flush()
    db.add(
        DmMensagem(
            conversa_id=c.id,
            mid=f"m-{quem}",
            direcao="recebida",
            texto="oi",
            ocorrido_em=AGORA - cliente_ha,
        )
    )
    if loja_ha is not None:
        db.add(
            DmMensagem(
                conversa_id=c.id,
                mid=f"e-{quem}",
                direcao="eco",
                texto="olá!",
                ocorrido_em=AGORA - loja_ha,
            )
        )
    await db.commit()
    return c


async def _cenario(db: AsyncSession) -> dict:
    """Três contas (como em produção) + uma DM de conta que saiu do cadastro.

    Charlots: p1 esperando há 1 h; p2 esperando há 23 h (vencendo: a janela de
    24 h fecha em 1 h); p3 respondida (eco); p4 silenciada (não conta).
    Uranyx: u1 esperando há 30 h (vencida). 7buyers: s1 esperando há 1 h — e a
    conversa guarda o @ antigo (a conta mudou de nome no cadastro).
    """
    ch = await _rede(db, "charlots", "instagram", "charlots_br")
    ur = await _rede(db, "uranyx", "instagram", "uranyx_br")
    sete = await _rede(db, "7buyers", "instagram", "7buyers_br")
    fb = await _rede(db, "charlots", "facebook", "Charlots Brasil")
    h = timedelta(hours=1)
    return {
        "ch": ch,
        "ur": ur,
        "7b": sete,
        "fb": fb,
        "p1": await _dm(db, ch, "charlots_br", "p1", cliente_ha=h),
        "p2": await _dm(db, ch, "charlots_br", "p2", cliente_ha=23 * h),
        "p3": await _dm(db, ch, "charlots_br", "p3", cliente_ha=3 * h, loja_ha=2 * h),
        "p4": await _dm(db, ch, "charlots_br", "p4", cliente_ha=5 * h, status="silenciada"),
        "u1": await _dm(db, ur, "uranyx_br", "u1", cliente_ha=30 * h),
        "s1": await _dm(db, sete, "7buyers_antigo", "s1", cliente_ha=h),
        "orfa": await _dm(db, None, "antiga_br", "o1", cliente_ha=4 * h),
    }


async def _comentario_charlots(db: AsyncSession, rede: RedeSocial):
    """A caixa de comentários da Charlots (frente B) com um comentário esperando."""
    canal = await canais_externos.garantir_canal(
        db,
        externo_ref="rede:instagram:17841473512063342",
        plataforma="instagram",
        canal=CANAL_COMENTARIO,
        nome="@charlots_br",
        rede_social_id=rede.id,
    )
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=None,
        plataforma="instagram",
        canal_nome=CANAL_COMENTARIO,
        externo_id="m1:ana",
        conta="@charlots_br",
        comprador_nome="@ana",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="c1",
        autor="cliente",
        texto="Tem em azul?",
        enviada_em=AGORA - timedelta(minutes=30),
        tipo="texto",
        anexos=[],
        payload={},
        origem=None,
    )
    await db.commit()
    return canal, conversa


def _ids(r) -> list[str]:
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["itens"]]


def _ig(*dms: DmConversa) -> list[str]:
    return [f"ig:{d.id}" for d in dms]


# ── contagem por conta (o adaptador) ──────────────────────────────────────


async def test_contar_por_conta_separa_e_nomeia_pelo_cadastro(db: AsyncSession):
    c = await _cenario(db)
    contas = await instagram.contar_por_conta(db)
    # Pelo @ do cadastro; a conta que saiu do cadastro no fim.
    assert [x["conta"] for x in contas] == [
        "@7buyers_br",
        "@charlots_br",
        "@uranyx_br",
        instagram.NOME_SEM_CADASTRO,
    ]
    por = {x["rede_social_id"]: x for x in contas}
    numeros = ("aguardando", "vencendo", "vencidas", "total", "abertas")
    # Charlots: p1 e p2 esperando (p2 vencendo); p3 respondida; p4 silenciada
    # (conta no total, não nas abertas nem esperando).
    assert tuple(por[c["ch"].id][k] for k in numeros) == (2, 1, 0, 4, 3)
    assert tuple(por[c["ur"].id][k] for k in numeros) == (1, 0, 1, 1, 1)
    assert tuple(por[c["7b"].id][k] for k in numeros) == (1, 0, 0, 1, 1)
    assert tuple(por[None][k] for k in numeros) == (1, 0, 0, 1, 1)
    # O total da plataforma = a soma das contas (o /resumo faz uma consulta só).
    assert instagram.somar(contas) == await instagram.contar(db)
    assert instagram.somar([]) == dict.fromkeys(numeros, 0)


def test_arroba():
    assert instagram.arroba("charlots_br") == "@charlots_br"
    assert instagram.arroba(" @uranyx_br ") == "@uranyx_br"
    assert instagram.arroba("") is None
    assert instagram.arroba(None) is None


# ── /resumo ───────────────────────────────────────────────────────────────


async def test_resumo_uma_linha_por_conta_com_a_bolinha_de_cada(client, db, pessoa):
    c = await _cenario(db)
    await _comentario_charlots(db, c["ch"])

    corpo = (await client.get(f"{URL}/resumo")).json()
    linhas = [lj for lj in corpo["lojas"] if lj["plataforma"] == "instagram"]
    # Uma linha por conta (a Charlots junta Direct e comentários); a da conta
    # fora do cadastro vem sem `rede_social_id` (a tela não a mostra: não há
    # por onde filtrar). Nenhuma linha única "Direct".
    assert [(lj["conta"], lj["rede_social_id"]) for lj in linhas] == [
        ("@7buyers_br", str(c["7b"].id)),
        ("@charlots_br", str(c["ch"].id)),
        ("@uranyx_br", str(c["ur"].id)),
        (instagram.NOME_SEM_CADASTRO, None),
    ]
    por = {lj["conta"]: lj for lj in linhas}
    # A bolinha de cada uma: as esperando (Direct + comentários da conta).
    assert [
        (por[n]["aguardando"], por[n]["direct_aguardando"], por[n]["vencidas"])
        for n in ("@7buyers_br", "@charlots_br", "@uranyx_br")
    ] == [(1, 1, 0), (3, 2, 0), (1, 1, 1)]
    # Mídia = DMs não silenciadas + o comentário aberto.
    assert por["@charlots_br"]["etiquetas"]["midia"] == 4
    assert por["@7buyers_br"]["etiquetas"]["midia"] == 1
    assert por["@7buyers_br"]["integracao"] == "Direct do Instagram (só leitura)"
    assert por["@7buyers_br"]["integration_id"] is None
    assert not [lj for lj in corpo["lojas"] if lj["conta"] == "Direct"]
    # A plataforma soma tudo: 5 DMs esperando + o comentário.
    ig = next(p for p in corpo["plataformas"] if p["plataforma"] == "instagram")
    assert (ig["aguardando"], ig["vencendo"], ig["vencidas"]) == (6, 1, 1)
    assert ig["etiquetas"]["midia"] == 7


async def test_resumo_sem_direct_para_quem_nao_ve_tudo(client, db, make_user, auth_as):
    await _cenario(db)
    membro = await make_user(permissions=PODE_TUDO)
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)
    corpo = (await client.get(f"{URL}/resumo")).json()
    assert not [lj for lj in corpo["lojas"] if lj["plataforma"] == "instagram"]
    assert all(p["plataforma"] != "instagram" for p in corpo["plataformas"])
    r = await client.get(f"{URL}/conversas", params={"plataforma": "instagram"})
    assert _ids(r) == []


async def test_caixa_de_comentarios_sem_permissao_nao_apaga_o_direct(client, db, pessoa):
    c = await _cenario(db)
    canal, _ = await _comentario_charlots(db, c["ch"])
    canal.status, canal.ultimo_erro = "sem_escopo", "(#10) falta instagram_manage_comments"
    await db.commit()
    por = {lj["conta"]: lj for lj in (await client.get(f"{URL}/resumo")).json()["lojas"]}
    ch = por["@charlots_br"]
    # O estado é o da caixa de comentários (a tela mostra o alerta)...
    assert ch["status_canal"] == "sem_escopo"
    assert ch["status_motivo"].startswith("Sem permissão no token do DaVinci Publicador")
    # ...e o Direct continua contando na linha (a tela a deixa acesa).
    assert (ch["aguardando"], ch["direct_aguardando"]) == (3, 2)
    # A 7buyers (só Direct) não herda o problema da Charlots.
    assert por["@7buyers_br"]["status_canal"] is None


# ── /conversas por conta ──────────────────────────────────────────────────


async def test_lista_filtra_por_conta(client, db, pessoa):
    c = await _cenario(db)
    _canal, comentario = await _comentario_charlots(db, c["ch"])
    ch, ur, sete = str(c["ch"].id), str(c["ur"].id), str(c["7b"].id)

    async def ids(**params) -> list[str]:
        return _ids(await client.get(f"{URL}/conversas", params=params))

    # A conta: o Direct (não silenciado) e os comentários dela, por recência.
    assert await ids(plataforma="instagram", rede_social_id=ch) == [
        str(comentario.id),
        *_ig(c["p1"], c["p3"], c["p2"]),
    ]
    assert await ids(rede_social_id=ch) == await ids(plataforma="instagram", rede_social_id=ch)
    assert await ids(plataforma="instagram", rede_social_id=ur) == _ig(c["u1"])
    assert await ids(plataforma="instagram", rede_social_id=sete) == _ig(c["s1"])
    # Só o Direct da conta; só os comentários da conta.
    assert await ids(plataforma="instagram", canal="dm", rede_social_id=ch) == _ig(
        c["p1"], c["p3"], c["p2"]
    )
    assert await ids(plataforma="instagram", canal="comentario", rede_social_id=ch) == [
        str(comentario.id)
    ]
    assert await ids(plataforma="instagram", canal="comentario", rede_social_id=sete) == []
    # "Falta responder" vai pelo prazo (a janela de 24 h): p2 fecha primeiro.
    assert await ids(
        plataforma="instagram", canal="dm", rede_social_id=ch, filtro="aguardando"
    ) == (_ig(c["p2"], c["p1"]))
    assert await ids(plataforma="instagram", rede_social_id=ur, filtro="vencidas") == _ig(c["u1"])
    assert await ids(plataforma="instagram", rede_social_id=ch, filtro="vencidas") == []
    assert await ids(plataforma="instagram", rede_social_id=ch, filtro="fechadas") == _ig(c["p4"])
    # Mídia (filtro ou etiqueta) com a conta: o Direct e o comentário dela.
    assert set(await ids(rede_social_id=ch, filtro="midia")) == {
        str(comentario.id),
        *_ig(c["p1"], c["p2"], c["p3"]),
    }
    assert await ids(rede_social_id=ur, etiqueta="midia") == _ig(c["u1"])
    # Etiqueta que o Direct não tem: nada da conta.
    assert await ids(rede_social_id=ch, filtro="pre_venda") == []
    # A Página do Facebook não tem Direct aqui.
    assert await ids(plataforma="facebook", rede_social_id=str(c["fb"].id)) == []
    assert await ids(rede_social_id=str(c["fb"].id)) == []


async def test_lista_por_conta_pagina_sem_misturar(client, db, pessoa):
    c = await _cenario(db)
    ch = str(c["ch"].id)
    vistos: list[str] = []
    proximo = None
    for _ in range(5):
        params = {"plataforma": "instagram", "canal": "dm", "rede_social_id": ch, "limite": 1}
        if proximo:
            params["antes_de"] = proximo
        r = await client.get(f"{URL}/conversas", params=params)
        vistos += _ids(r)
        proximo = r.json()["proximo"]
        if not proximo:
            break
    assert vistos == _ig(c["p1"], c["p3"], c["p2"])
    # Pelo prazo, idem (o corte do cursor de prazo é feito no router).
    p1 = await client.get(
        f"{URL}/conversas",
        params={"rede_social_id": ch, "filtro": "aguardando", "limite": 1},
    )
    assert _ids(p1) == _ig(c["p2"])
    p2 = await client.get(
        f"{URL}/conversas",
        params={
            "rede_social_id": ch,
            "filtro": "aguardando",
            "limite": 1,
            "antes_de": p1.json()["proximo"],
        },
    )
    assert (_ids(p2), p2.json()["proximo"]) == (_ig(c["p1"]), None)


async def test_conta_com_arroba_na_lista_no_detalhe_e_na_busca(client, db, pessoa):
    c = await _cenario(db)
    itens = (
        await client.get(f"{URL}/conversas", params={"plataforma": "instagram", "canal": "dm"})
    ).json()["itens"]
    contas = {i["id"]: (i["conta"], i["rede_social_id"]) for i in itens}
    assert contas[f"ig:{c['p1'].id}"] == ("@charlots_br", str(c["ch"].id))
    assert contas[f"ig:{c['orfa'].id}"] == ("@antiga_br", None)
    d = (await client.get(f"{URL}/conversas/ig:{c['u1'].id}")).json()["conversa"]
    assert (d["conta"], d["rede_social_id"], d["etiqueta"]) == (
        "@uranyx_br",
        str(c["ur"].id),
        "midia",
    )
    # A tela mostra "@charlots_br": buscar com o @ também acha (e só o Direct
    # da Charlots — a 7buyers guarda outro @).
    busca = await client.get(
        f"{URL}/conversas", params={"plataforma": "instagram", "canal": "dm", "q": "@charlots"}
    )
    assert set(_ids(busca)) == set(_ig(c["p1"], c["p2"], c["p3"]))
    assert (
        _ids(
            await client.get(
                f"{URL}/conversas", params={"plataforma": "instagram", "canal": "dm", "q": "@"}
            )
        )
        == []
    )
    # Nada foi gravado no Direct (o adaptador só lê).
    total = (await db.execute(select(DmMensagem))).scalars().all()
    assert len(total) == 8
