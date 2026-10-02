# ruff: noqa: S105  (tokens de teste, nada real)
"""A BASE comum do carrinho dos sites (RF9) e das redes (RF7), 02/10/2026.

O que se garante aqui (as frentes constroem em cima):

- o vocabulário: Carrinho entre Avaliação e Pré-venda; Mídia é BASE (nunca
  indicador); a pergunta do comentário (RF7) por palavra inteira;
- o motor da etiqueta: carrinho ABERTO → CARRINHO; recuperado → PÓS-VENDA
  ("virou pedido"); encerrado sem pedido → PRÉ-VENDA; comentário → MÍDIA;
- o canal externo (`canais_externos`): referência, idempotência, o CHECK
  `tem_origem` e o modo que a aba Lojas aceita;
- o /resumo e a lista: o grupo do site (`externo_ref`), o Direct do Instagram
  SEPARADO POR CONTA (`rede_social_id`, somando os comentários da conta), o
  filtro Mídia com o Direct e o Carrinho;
- o endereço e o token do site (sem nunca sair do processo) e os crons
  desligados por padrão.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoCarrinho,
    AtendimentoConversa,
    AtendimentoEtiquetaHistorico,
    DmConversa,
    DmMensagem,
    User,
    UserRole,
)
from app.models.marca import Marca, RedeSocial
from app.routers import atendimento as rota
from app.services.atendimento import canais_externos, carrinhos, etiqueta, gravar
from app.services.atendimento.constantes import (
    CANAL_CARRINHO,
    CANAL_COMENTARIO,
    CARRINHO_ABERTO,
    CARRINHO_NAO_RECUPERADO,
    CARRINHO_RECUPERADO,
    ETIQUETA_AVALIACAO,
    ETIQUETA_CARRINHO,
    ETIQUETA_MIDIA,
    ETIQUETA_POS_VENDA,
    ETIQUETA_PRE_VENDA,
    ETIQUETAS_BASE,
    PRIORIDADE_ETIQUETAS,
    e_pergunta,
)
from app.services.atendimento.etiqueta import Calculo, FatosEtiqueta

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


# ── Vocabulário e motor (puro) ────────────────────────────────────────────


def test_prioridade_e_bases():
    assert PRIORIDADE_ETIQUETAS == (
        "reclamacao",
        "ag_cancelamento",
        "devolucao",
        "avaliacao",
        "carrinho",
        "pre_venda",
        "pos_venda",
        "midia",
    )
    assert ETIQUETAS_BASE == ("pre_venda", "pos_venda", "midia")


@pytest.mark.parametrize(
    ("texto", "pergunta"),
    [
        ("Qual o preço?", True),
        ("tem no azul", True),
        ("TEM NO AZUL", True),
        ("Preço da M2", True),
        ("quanto fica o frete pra BH", True),
        ("Linda demais 😍", False),
        ("temos que comprar", False),
        ("", False),
        (None, False),
    ],
)
def test_e_pergunta(texto, pergunta):
    assert e_pergunta(texto) is pergunta


def test_calcular_carrinho_e_midia():
    aberto = FatosEtiqueta(carrinho_aberto=True, motivo_carrinho="Carrinho parado")
    assert etiqueta.calcular(aberto) == Calculo(ETIQUETA_CARRINHO, [], "Carrinho parado")
    # Avaliação é mais urgente: o carrinho vira o indicador.
    os_dois = FatosEtiqueta(carrinho_aberto=True, avaliacao_pendente=True, tem_pedido=True)
    assert etiqueta.calcular(os_dois).etiqueta == ETIQUETA_AVALIACAO
    assert etiqueta.calcular(os_dois).secundarias == [ETIQUETA_CARRINHO]
    # Mídia é a base da conversa das redes.
    assert etiqueta.calcular(FatosEtiqueta(midia=True, motivo_midia="Comentário no Instagram")) == (
        Calculo(ETIQUETA_MIDIA, [], "Comentário no Instagram")
    )
    # Base nunca é indicador.
    calc = Calculo(ETIQUETA_CARRINHO, [ETIQUETA_MIDIA], "x")
    assert etiqueta.secundarias_exibidas(calc, ETIQUETA_MIDIA) == [ETIQUETA_CARRINHO]
    assert etiqueta.secundarias_exibidas(Calculo(ETIQUETA_MIDIA, [], "x"), ETIQUETA_PRE_VENDA) == []


# ── Canal externo ─────────────────────────────────────────────────────────


def test_referencias():
    assert canais_externos.ref_do_site("Charlots") == "site:charlots"
    assert canais_externos.ref_da_rede("instagram", 17841473512063342) == (
        "rede:instagram:17841473512063342"
    )
    assert canais_externos.partes("site:uranyx") == ("site", "site", "uranyx")
    assert canais_externos.partes("rede:facebook:139") == ("rede", "facebook", "139")
    for torta in ("", "site:", "rede:tiktok:1", "rede:instagram:", "loja:x", None):
        assert canais_externos.partes(torta) is None, torta
    for chamada in (
        lambda: canais_externos.ref_do_site("7buyers"),
        lambda: canais_externos.ref_da_rede("tiktok", "1"),
        lambda: canais_externos.ref_da_rede("instagram", "a:b"),
    ):
        with pytest.raises(canais_externos.ReferenciaInvalida):
            chamada()


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


async def test_garantir_canal_idempotente_e_check(db: AsyncSession):
    c1 = await canais_externos.garantir_canal(
        db, externo_ref="site:charlots", plataforma="site", canal=CANAL_CARRINHO, nome="Charlots"
    )
    await db.commit()
    c2 = await canais_externos.garantir_canal(
        db, externo_ref="site:charlots", plataforma="site", canal=CANAL_CARRINHO
    )
    await db.commit()
    assert c1.id == c2.id
    assert (c2.modo, c2.status, c2.integration_id, c2.robo_perfil_id) == (
        "observar",
        "novo",
        None,
        None,
    )
    assert canais_externos.eh_externo(c2) and canais_externos.nome_do_canal(c2) == "Charlots"
    assert canais_externos.site_do_canal(c2) == "charlots"
    # A rede: o nome e a conta do cadastro se atualizam.
    rede = await _rede(db, "charlots", "instagram", "charlots_br")
    ig = await canais_externos.garantir_canal(
        db,
        externo_ref="rede:instagram:1784",
        plataforma="instagram",
        canal=CANAL_COMENTARIO,
        nome="@charlots_br",
        rede_social_id=rede.id,
    )
    await db.commit()
    assert (ig.rede_social_id, canais_externos.nome_do_canal(ig)) == (rede.id, "@charlots_br")
    assert canais_externos.descricao_da_origem(ig).startswith("Instagram")
    # Caixa que não existe na plataforma, ou referência de outra: erro de programa.
    for kw in (
        {"externo_ref": "site:charlots", "plataforma": "site", "canal": CANAL_COMENTARIO},
        {"externo_ref": "site:charlots", "plataforma": "instagram", "canal": CANAL_COMENTARIO},
    ):
        with pytest.raises(canais_externos.ReferenciaInvalida):
            await canais_externos.garantir_canal(db, **kw)
    # Canal sem origem nenhuma: o banco recusa (CHECK `tem_origem`).
    db.add(AtendimentoCanal(plataforma="site", canal=CANAL_CARRINHO))
    with pytest.raises(IntegrityError, match="tem_origem"):
        await db.commit()
    await db.rollback()
    assert canais_externos.modo_permitido("site", "observar")
    assert not canais_externos.modo_permitido("site", "humano")
    assert canais_externos.modo_permitido("instagram", "humano")
    assert not canais_externos.modo_permitido("facebook", "auto")


# ── Etiqueta da conversa do carrinho e do comentário ─────────────────────


async def _conversa_carrinho(db: AsyncSession, lojista: str = "123") -> AtendimentoConversa:
    canal = await canais_externos.garantir_canal(
        db, externo_ref="site:charlots", plataforma="site", canal=CANAL_CARRINHO, nome="Charlots"
    )
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=None,
        plataforma="site",
        canal_nome=CANAL_CARRINHO,
        externo_id=f"lojista:{lojista}",
        conta="Charlots",
        comprador_id=lojista,
        comprador_nome="Loja X",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"carrinho:{lojista}",
        autor="cliente",
        texto="Carrinho parado: 3 peças",
        enviada_em=AGORA - timedelta(hours=30),
        tipo="texto",
        anexos=[],
        payload={},
        origem=None,
    )
    await db.commit()
    return conversa


async def test_etiqueta_do_carrinho_abre_recupera_e_encerra(db: AsyncSession):
    conversa = await _conversa_carrinho(db)
    # Sem carrinho ainda: pré-venda (não há pedido).
    await etiqueta.recalcular_etiqueta(db, conversa, motivo="teste")
    assert conversa.etiqueta == ETIQUETA_PRE_VENDA
    carrinho = AtendimentoCarrinho(
        site="charlots",
        canal_id=conversa.canal_id,
        conversa_id=conversa.id,
        lojista_id="123",
        lojista={"nome": "Fulana"},
        itens=[{"produto_id": 1, "quantidade": 3, "skus": ["b1001.pi"]}],
        quantidade_total=3,
        parado_desde=AGORA - timedelta(hours=30),
        detectado_em=AGORA - timedelta(hours=1),
    )
    db.add(carrinho)
    await db.flush()
    assert await etiqueta.recalcular_etiqueta(db, conversa, motivo="leitura do site")
    assert conversa.etiqueta == ETIQUETA_CARRINHO
    # Recuperado (finalizou pelo WhatsApp): "virou pedido" → Pós-venda.
    carrinho.situacao = CARRINHO_RECUPERADO
    await etiqueta.recalcular_etiqueta(db, conversa, motivo="carrinho recuperado")
    assert conversa.etiqueta == ETIQUETA_POS_VENDA
    await db.commit()
    linhas = (
        (
            await db.execute(
                select(AtendimentoEtiquetaHistorico)
                .where(AtendimentoEtiquetaHistorico.conversa_id == conversa.id)
                .order_by(AtendimentoEtiquetaHistorico.em)
            )
        )
        .scalars()
        .all()
    )
    assert [(h.de, h.para) for h in linhas] == [
        (ETIQUETA_PRE_VENDA, ETIQUETA_CARRINHO),
        (ETIQUETA_CARRINHO, ETIQUETA_POS_VENDA),
    ]
    assert linhas[0].motivo.startswith("Carrinho parado desde")
    assert "Carrinho encerrado" in linhas[1].motivo
    assert "Fulana" not in " ".join(h.motivo or "" for h in linhas)
    # Um carrinho novo, depois, que não foi recuperado: o MAIS RECENTE decide.
    db.add(
        AtendimentoCarrinho(
            site="charlots",
            conversa_id=conversa.id,
            lojista_id="123",
            parado_desde=AGORA - timedelta(hours=2),
            detectado_em=AGORA,
            situacao=CARRINHO_NAO_RECUPERADO,
        )
    )
    await db.flush()
    await etiqueta.recalcular_etiqueta(db, conversa, motivo="prazo")
    assert conversa.etiqueta == ETIQUETA_PRE_VENDA
    await db.commit()


async def test_um_carrinho_aberto_por_lojista(db: AsyncSession):
    def novo(lojista: str) -> AtendimentoCarrinho:
        return AtendimentoCarrinho(
            site="charlots", lojista_id=lojista, parado_desde=AGORA, situacao=CARRINHO_ABERTO
        )

    db.add_all([novo("1"), novo("2")])
    await db.commit()
    db.add(novo("1"))
    with pytest.raises(IntegrityError, match="uq_atendimento_carrinhos_aberto"):
        await db.commit()
    await db.rollback()


async def test_conversa_de_comentario_e_midia(db: AsyncSession):
    canal = await canais_externos.garantir_canal(
        db,
        externo_ref="rede:instagram:1784",
        plataforma="instagram",
        canal=CANAL_COMENTARIO,
        nome="@charlots_br",
    )
    conversa, criada = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=None,
        plataforma="instagram",
        canal_nome=CANAL_COMENTARIO,
        externo_id="m1:p1",
        conta="@charlots_br",
        comprador_nome="@maria",
        dados={"tipo": "mencao"},
    )
    await db.commit()
    assert criada
    # A conversa nova já nasce com a etiqueta (o gravar recalcula).
    assert conversa.etiqueta == ETIQUETA_MIDIA


# ── /resumo e lista ───────────────────────────────────────────────────────


def _ids(r) -> list[str]:
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["itens"]]


async def _dm(db: AsyncSession, rede: RedeSocial | None, conta: str, quem: str) -> DmConversa:
    c = DmConversa(
        rede_social_id=rede.id if rede else None,
        conta=conta,
        participante_id=quem,
        participante_nome=quem,
    )
    db.add(c)
    await db.flush()
    db.add(
        DmMensagem(
            conversa_id=c.id,
            mid=f"m-{quem}",
            direcao="recebida",
            texto="oi",
            ocorrido_em=AGORA - timedelta(hours=1),
        )
    )
    await db.commit()
    return c


async def test_resumo_e_lista_com_site_e_contas_de_rede(client, db, pessoa):
    carrinho = await _conversa_carrinho(db)
    rede_ch = await _rede(db, "charlots", "instagram", "charlots_br")
    rede_7b = await _rede(db, "7buyers", "instagram", "7buyers_br")
    dm_ch = await _dm(db, rede_ch, "charlots_br", "p1")
    dm_7b = await _dm(db, rede_7b, "7buyers_br", "p2")
    # A caixa de comentários da Charlots, com uma conversa esperando.
    canal_ig = await canais_externos.garantir_canal(
        db,
        externo_ref="rede:instagram:1784",
        plataforma="instagram",
        canal=CANAL_COMENTARIO,
        nome="@charlots_br",
        rede_social_id=rede_ch.id,
    )
    comentario, _ = await gravar.upsert_conversa(
        db,
        canal=canal_ig,
        integration=None,
        plataforma="instagram",
        canal_nome=CANAL_COMENTARIO,
        externo_id="m1:p9",
        conta="@charlots_br",
        comprador_nome="@ana",
    )
    await gravar.gravar_mensagem(
        db,
        comentario,
        externo_id="c1",
        autor="cliente",
        texto="Qual o preço?",
        enviada_em=AGORA - timedelta(minutes=20),
        tipo="texto",
        anexos=[],
        payload={},
        origem=None,
    )
    await db.commit()

    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200, r.text
    corpo = r.json()
    plats = {p["plataforma"]: p for p in corpo["plataformas"]}
    assert {"site", "instagram"} <= set(plats)
    # Instagram = 1 comentário + 2 DMs esperando; as DMs contam na Mídia.
    assert plats["instagram"]["aguardando"] == 3
    assert plats["instagram"]["etiquetas"]["midia"] == 3
    assert plats["site"]["etiquetas"]["carrinho"] == 0
    assert plats["site"]["etiquetas"]["pre_venda"] == 1
    lojas = {(lj["plataforma"], lj["conta"]): lj for lj in corpo["lojas"]}
    site = lojas[("site", "Charlots")]
    assert (site["externo_ref"], site["integration_id"], site["aguardando"]) == (
        "site:charlots",
        None,
        1,
    )
    assert site["status_canal"] == "novo"
    # Uma linha por conta: a Charlots soma o Direct e o comentário; a 7buyers
    # (só Direct) ganha a dela.
    ch = lojas[("instagram", "@charlots_br")]
    assert (ch["rede_social_id"], ch["aguardando"], ch["direct_aguardando"]) == (
        str(rede_ch.id),
        2,
        1,
    )
    sete = lojas[("instagram", "@7buyers_br")]
    assert (sete["rede_social_id"], sete["aguardando"], sete["direct_aguardando"]) == (
        str(rede_7b.id),
        1,
        1,
    )
    assert sete["etiquetas"]["midia"] == 1
    assert not [lj for lj in corpo["lojas"] if lj["conta"] == "Direct"]
    canais = {c["externo_ref"]: c for c in corpo["canais"] if c["externo_ref"]}
    assert canais["site:charlots"]["integracao"].startswith("site Charlots")
    assert canais["rede:instagram:1784"]["rede_social_id"] == str(rede_ch.id)

    # Lista pela linha do site e pela conta (o Direct e os comentários dela).
    assert _ids(await client.get(f"{URL}/conversas", params={"externo_ref": "site:charlots"})) == [
        str(carrinho.id)
    ]
    assert set(
        _ids(await client.get(f"{URL}/conversas", params={"rede_social_id": str(rede_ch.id)}))
    ) == {str(comentario.id), f"ig:{dm_ch.id}"}
    assert _ids(
        await client.get(
            f"{URL}/conversas",
            params={"plataforma": "instagram", "rede_social_id": str(rede_7b.id)},
        )
    ) == [f"ig:{dm_7b.id}"]
    # Só o Direct (`canal=dm`) e só os comentários.
    assert set(
        _ids(
            await client.get(f"{URL}/conversas", params={"plataforma": "instagram", "canal": "dm"})
        )
    ) == {f"ig:{dm_ch.id}", f"ig:{dm_7b.id}"}
    assert _ids(
        await client.get(
            f"{URL}/conversas", params={"plataforma": "instagram", "canal": "comentario"}
        )
    ) == [str(comentario.id)]
    # O filtro Mídia traz o comentário E o Direct; o Carrinho, o site.
    assert set(_ids(await client.get(f"{URL}/conversas", params={"filtro": "midia"}))) == {
        str(comentario.id),
        f"ig:{dm_ch.id}",
        f"ig:{dm_7b.id}",
    }
    assert _ids(await client.get(f"{URL}/conversas", params={"plataforma": "site"})) == [
        str(carrinho.id)
    ]
    item_dm = next(
        i
        for i in (await client.get(f"{URL}/conversas", params={"canal": "dm"})).json()["itens"]
        if i["id"] == f"ig:{dm_7b.id}"
    )
    assert (item_dm["etiqueta"], item_dm["rede_social_id"]) == ("midia", str(rede_7b.id))
    r = await client.get(f"{URL}/conversas", params={"externo_ref": "loja:x"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "externo_ref_invalido")
    # O detalhe da conversa do site abre (sem pedido, sem envio).
    d = await client.get(f"{URL}/conversas/{carrinho.id}")
    assert d.status_code == 200, d.text
    assert d.json()["envio"]["pode_enviar"] is False


async def test_modo_do_canal_externo(client, db, pessoa, make_user, auth_as):
    site = await canais_externos.garantir_canal(
        db, externo_ref="site:uranyx", plataforma="site", canal=CANAL_CARRINHO, nome="Uranyx"
    )
    ig = await canais_externos.garantir_canal(
        db, externo_ref="rede:facebook:139", plataforma="facebook", canal=CANAL_COMENTARIO
    )
    await db.commit()
    r = await client.patch(f"{URL}/canais/{site.id}", json={"modo": "humano"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "modo_invalido_externo")
    r = await client.patch(f"{URL}/canais/{ig.id}", json={"modo": "copiloto"})
    assert r.status_code == 422
    # Rede em humano = responder/ocultar EM PÚBLICO como a marca (com o envio
    # ligado): só admin libera, como o automático (revisão 02/10/2026).
    r = await client.patch(f"{URL}/canais/{ig.id}", json={"modo": "humano"})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    await db.refresh(ig)
    assert ig.modo == "observar"
    admin = await make_user(role=UserRole.ADMIN, permissions=PODE_TUDO)
    auth_as(admin)
    r = await client.patch(f"{URL}/canais/{ig.id}", json={"modo": "humano"})
    assert (r.status_code, r.json()["modo"]) == (200, "humano")
    assert r.json()["externo_ref"] == "rede:facebook:139"
    # Voltar para observar qualquer um com edição pode.
    auth_as(pessoa)
    r = await client.patch(f"{URL}/canais/{ig.id}", json={"modo": "observar"})
    assert (r.status_code, r.json()["modo"]) == (200, "observar")


# ── Site: endereço, token e cron ──────────────────────────────────────────


def test_endereco_e_token_do_site(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_sites_urls", "")
    monkeypatch.setattr(s, "sites_estoque_tokens", "tok-c:charlots, tok-u:Uranyx ,torto")
    assert carrinhos.url_do_site("charlots") == "https://charlots.com.br"
    assert carrinhos.rota_do_site("uranyx") == "https://uranyx.com.br/api/davinci/carrinhos"
    assert carrinhos.url_do_site("7buyers") is None
    assert carrinhos.token_do_site("uranyx") == "tok-u"
    assert carrinhos.token_do_site("charlots") == "tok-c"
    assert carrinhos.token_do_site("7buyers") is None
    # O ensaio local aponta para o PHP no localhost; http fora dele, não.
    monkeypatch.setattr(
        s,
        "atendimento_sites_urls",
        "charlots=http://localhost:8080/charlots/,uranyx=http://exemplo.com",
    )
    assert carrinhos.url_do_site("charlots") == "http://localhost:8080/charlots"
    assert carrinhos.url_do_site("uranyx") is None
    assert carrinhos.mesmo_token("a", "a") and not carrinhos.mesmo_token("a", "b")
    assert not carrinhos.mesmo_token(None, "a")


async def test_crons_nascem_desligados(monkeypatch):
    from app import worker

    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_carrinhos_ativa", False)
    monkeypatch.setattr(s, "atendimento_redes_ativa", False)
    assert await worker.atendimento_carrinhos({}) is None
    assert await worker.atendimento_redes({}) is None
    campos = type(get_settings()).model_fields
    assert campos["atendimento_carrinhos_ativa"].default is False
    assert campos["atendimento_redes_ativa"].default is False
    assert campos["atendimento_carrinho_horas"].default == 24


async def test_sem_escopo_da_rede_e_do_site_explica_o_que_fazer(client, db, pessoa):
    ig = await canais_externos.garantir_canal(
        db,
        externo_ref="rede:instagram:1784",
        plataforma="instagram",
        canal=CANAL_COMENTARIO,
        nome="@charlots_br",
    )
    site = await canais_externos.garantir_canal(
        db, externo_ref="site:uranyx", plataforma="site", canal=CANAL_CARRINHO, nome="Uranyx"
    )
    ig.status, ig.ultimo_erro = "sem_escopo", "(#10) falta instagram_manage_comments"
    site.status = "sem_escopo"
    await db.commit()
    corpo = (await client.get(f"{URL}/resumo")).json()
    lojas = {lj["conta"]: lj for lj in corpo["lojas"]}
    assert lojas["@charlots_br"]["status_canal"] == "sem_escopo"
    assert lojas["@charlots_br"]["status_motivo"].startswith(
        "Sem permissão no token do DaVinci Publicador"
    )
    assert "instagram_manage_comments" in lojas["@charlots_br"]["status_motivo"]
    assert lojas["Uranyx"]["status_motivo"].startswith("O site recusou o token")
