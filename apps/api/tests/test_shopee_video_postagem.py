"""Shopee Vídeo no robô de postagem — anúncio, guardas, agenda e publicador.

Eduardo, 08/10/2026: "Shopee Vídeo como mais uma rede em Marketing ›
Criativos", começando pela loja Barbosa (Uranyx), poucos por dia, na grade
da conta. O que este arquivo insiste em provar:

  • O ANÚNCIO certo: o avulso dedicado do aparelho (o de menos aparelhos
    entre os que têm o SKU), nunca kit, nunca a vitrine quando o dedicado
    existe, nunca usado; sem anúncio = motivo DO VÍDEO (o robô pula).
  • As guardas do vídeo (3–60 s, 720p+, H.264) e da conta (loja, autorização).
  • A legenda: a da biblioteca sai curta (≤ 150, sem WhatsApp) SÓ na Shopee;
    a escrita à mão acima de 150 é recusada (quem escreveu decide o corte).
  • O publicador é RETOMÁVEL e nunca sobe o arquivo de novo depois que o
    `post_video` foi chamado; erro que só gente resolve vira frase clara e
    não volta pra fila.

Nada de rede: a Shopee é um dublê (`ShopeeFake`) e a conferência ao vivo
do anúncio (client da integração) é trocada por uma função do teste.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingLegendaModelo,
    MarketingPostagem,
    Product,
    ProductLink,
    RedeSocial,
    RedeSocialToken,
    User,
)
from app.security.cipher import encrypt_json
from app.services.marketing import autopostagem
from app.services.marketing import postagens as svc
from app.services.marketing import shopee_video as sv
from app.services.marketing import shopee_video_anuncio as anuncio
from app.services.marketing import shopee_video_conta as conta
from app.services.marketing import shopee_video_publicador as pub
from app.services.marketing.shopee_video import (
    ShopeeVideoAmbiguoError,
    ShopeeVideoError,
    ShopeeVideoRedeError,
)

# A conferência ao vivo de verdade (o shopee fixture a troca por um dublê).
_CONFERIR_REAL = anuncio.conferir_ao_vivo

pytestmark = pytest.mark.asyncio

SHOP_ID = 1725800210
USER_SHOPEE = 987654321
CHAVE_FALSA = "chave-falsa-do-app-de-video-0123456789"
TOKEN_FALSO = "token-falso-de-video-nunca-vaza"


# ═══════════════════════════════════════════════════════════════ fixtures


@pytest.fixture(autouse=True)
def _uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))


@pytest.fixture(autouse=True)
def _video_bom(monkeypatch):
    """O arquivo dos testes é falso: o ffprobe é trocado por um retrato de
    vídeo BOM (1080x1920, 24 s, H.264). Cada teste de formato troca de novo."""
    info = {"v": anuncio.InfoVideo(1080, 1920, 24.2, "h264")}

    async def sondar(_caminho: Path):
        return info["v"]

    monkeypatch.setattr(anuncio, "sondar_video", sondar)
    return info


@pytest.fixture(autouse=True)
async def _limpa(db: AsyncSession):
    yield
    for c in (await db.execute(select(MarketingCreative))).scalars().all():
        await db.delete(c)
    for m in (await db.execute(select(MarketingLegendaModelo))).scalars().all():
        await db.delete(m)
    await db.commit()


# ═══════════════════════════════════════════════════════════════ sementes


async def _loja(db: AsyncSession, user: User, nome: str = "Barbosa", shop_id: int = SHOP_ID):
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"shop_id": shop_id, "access_token": "da-loja", "partner_id": 1}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _link(
    db: AsyncSession,
    user: User,
    integ: Integration,
    *,
    item: str,
    sku: str,
    estoque: int = 10,
    nome: str | None = None,
    titulo: str | None = None,
    morto: bool = False,
) -> ProductLink:
    p = Product(user_id=user.id, sku=f"{sku}-{uuid.uuid4().hex[:6]}", name=nome or f"Uranyx {sku}")
    db.add(p)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.SHOPEE,
        external_id=item,
        variation_id=str(uuid.uuid4().int % 10**12),
        external_sku=sku,
        listing_title=titulo or f"anúncio {item}",
        stock=estoque,
        last_sync_status=LinkSyncStatus.OK,
        morto_desde=datetime.now(UTC) if morto else None,
    )
    db.add(link)
    await db.commit()
    return link


async def _marca(db: AsyncSession, nome: str = "Uranyx") -> Marca:
    m = Marca(nome=nome, slug=nome.lower())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _conta_shopee(
    db: AsyncSession,
    marca: Marca,
    integ: Integration | None,
    *,
    conta: str = "barbosa",
    autorizada: bool = True,
    status: str = "ok",
    auto: bool = True,
) -> RedeSocial:
    r = RedeSocial(
        marca_id=marca.id,
        plataforma="shopee",
        conta=conta,
        ativo=True,
        integration_id=integ.id if integ else None,
        postagem_auto=auto,
        postagem_hora_inicio=12,
        postagem_intervalo_min=400,
        postagem_max_dia=3,
    )
    db.add(r)
    await db.flush()
    blob: dict[str, Any] = {"partner_id": 2047721, "partner_key": CHAVE_FALSA}
    if autorizada:
        blob.update(
            access_token=TOKEN_FALSO,
            refresh_token="refresh-falso",
            expires_at=int(datetime.now(UTC).timestamp()) + 3600,
            user_id=USER_SHOPEE,
            shop_id=SHOP_ID,
        )
    db.add(
        RedeSocialToken(
            rede_social_id=r.id,
            provedor="shopee",
            external_user_id=str(USER_SHOPEE) if autorizada else None,
            external_username="Barbosa",
            status=status if autorizada else "pendente",
            token_enc=encrypt_json(blob),
            token_expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await db.commit()
    await db.refresh(r)
    return r


async def _conta_ig(db: AsyncSession, marca: Marca) -> RedeSocial:
    r = RedeSocial(
        marca_id=marca.id, plataforma="instagram", conta="uranyx_br", ativo=True, postagem_auto=True
    )
    db.add(r)
    await db.flush()
    db.add(
        RedeSocialToken(
            rede_social_id=r.id,
            external_user_id="1784",
            status="ok",
            token_enc=encrypt_json({"access_token": "ig-falso"}),
        )
    )
    await db.commit()
    await db.refresh(r)
    return r


async def _criativo(
    db: AsyncSession,
    marca: Marca,
    *,
    sku: str | None = "dg046.pi",
    nome: str = "video.mp4",
    criado: datetime | None = None,
    sha: str | None = None,
) -> tuple[MarketingCreative, MarketingCreativeFile]:
    c = MarketingCreative(
        modelo="F110L",
        marca=marca.slug,
        marca_id=marca.id,
        aprovado=True,
        sku=sku,
    )
    if criado:
        c.created_at = criado
    db.add(c)
    await db.flush()
    rel = f"creatives/{c.id}/{nome}"
    caminho = Path(get_settings().uploads_dir) / rel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 64)
    f = MarketingCreativeFile(
        creative_id=c.id,
        file_name=nome,
        file_mime="video/mp4",
        file_size=76,
        file_rel=rel,
        sha256=sha,
    )
    db.add(f)
    await db.commit()
    await db.refresh(c)
    await db.refresh(f)
    return c, f


async def _cenario_barbosa(db: AsyncSession, user: User) -> Integration:
    """O retrato da Barbosa de 08/10 (dados.md): o F110L tem o anúncio
    dedicado 58262693089 (F110 Pro + F110L) E está na vitrine 58269759596
    (vários aparelhos, mais estoque). O S5 só tem avulso no "A17 Pro Max"
    (que mistura dois aparelhos) e um KIT."""
    integ = await _loja(db, user)
    await _link(db, user, integ, item="58262693089", sku="dg046.pi", estoque=26)
    await _link(db, user, integ, item="58262693089", sku="dg047.pi", estoque=30)
    for sku in ("dg046.pi", "dg017.pi", "dg019.ra", "dg048.ra", "dg093.ra"):
        await _link(db, user, integ, item="58269759596", sku=sku, estoque=200)
    await _link(db, user, integ, item="22499563693", sku="dg052.ci", estoque=10)
    await _link(db, user, integ, item="22499563693", sku="dg088.ci", estoque=1391)
    await _link(db, user, integ, item="22499563693", sku="dg089.ci", estoque=1401)
    await _link(db, user, integ, item="58266122162", sku="dg088.ci+a001.ci", estoque=900)
    await _link(db, user, integ, item="58266122162", sku="dg089.ci+a001.ci", estoque=900)
    return integ


# ═══════════════════════════════════════════════════════ o anúncio certo


async def test_dedicado_ganha_da_vitrine_mesmo_com_menos_estoque(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, _f = await _criativo(db, marca, sku="dg046.sp")  # sufixo de origem diferente: casa pela base
    r = await anuncio.anuncio_para(db, c, rede)
    assert isinstance(r, anuncio.AnuncioEscolhido), r
    assert r.item_id == 58262693089
    assert r.bases_no_anuncio == 2
    assert r.estoque == 56


async def test_s5_so_tem_anuncio_proprio_em_kit_nao_cai_na_vitrine_do_a17(db, make_user):
    """O retrato real da Barbosa (dados.md): o anúncio PRÓPRIO do S5/A18
    (58266122162, 2 bases) é KIT, e o único avulso que carrega o S5 é o "A17
    Pro Max" (22499563693), que mistura dois aparelhos. Vincular o vídeo do S5
    ao anúncio do A17 é o "irrelevante" que a Shopee apaga (e tira ponto da
    conta) — o vídeo não sai nesta loja, nem o kit é vinculado."""
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    for sku in ("dg089.ci, dg088.ci", "dg088.ci"):
        c, _ = await _criativo(db, marca, sku=sku, nome=f"{sku[:5]}.mp4")
        assert await anuncio.anuncio_para(db, c, rede) == anuncio.SO_KIT, sku


async def test_dois_skus_no_mesmo_avulso_valem_e_kit_menos_especifico_nao_atrapalha(db, make_user):
    """O F112 tem várias cores (bases) no MESMO avulso dedicado: o vídeo com
    duas cores vale e cai nele. Um kit do aparelho que é TÃO vitrine quanto
    (ou mais) não muda nada — e nunca é o vinculado."""
    user = await make_user()
    integ = await _loja(db, user)
    for sku in ("dg082.ra", "dg084.ra", "dg085.ra"):
        await _link(db, user, integ, item="58208057789", sku=sku, estoque=20)
    for sku in ("dg082.ra+a001.ci", "dg084.ra+a001.ci", "dg085.ra+a001.ci", "dg086.ra+a001.ci"):
        await _link(db, user, integ, item="777", sku=sku, estoque=50)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, _ = await _criativo(db, marca, sku="dg082.ra, dg084.ra")
    r = await anuncio.anuncio_para(db, c, rede)
    assert isinstance(r, anuncio.AnuncioEscolhido), r
    assert r.item_id == 58208057789
    assert r.skus == ["dg082.ra", "dg084.ra"]


async def test_so_kit_na_loja_recusa(db, make_user):
    user = await make_user()
    integ = await _loja(db, user)
    await _link(db, user, integ, item="1", sku="dg088.ci+a001.ci")
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, _ = await _criativo(db, marca, sku="dg088.ci")
    assert await anuncio.anuncio_para(db, c, rede) == anuncio.SO_KIT


async def test_dois_skus_em_anuncios_diferentes_e_ambiguo(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, _ = await _criativo(db, marca, sku="dg046.pi, dg088.ci")
    assert await anuncio.anuncio_para(db, c, rede) == anuncio.SKU_AMBIGUO


@pytest.mark.parametrize(
    ("sku", "motivo"),
    [
        ("dg052-4", "sku_ambiguo"),
        ("", "criativo_sem_sku"),
        (None, "criativo_sem_sku"),
        ("dg999.pi", "sem_anuncio_na_loja"),
    ],
)
async def test_sku_que_nao_da_pra_escolher(db, make_user, sku, motivo):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, _ = await _criativo(db, marca, sku=sku)
    assert await anuncio.anuncio_para(db, c, rede) == motivo


async def test_usado_morto_e_outra_loja_nao_contam(db, make_user):
    user = await make_user()
    integ = await _loja(db, user)
    outra = await _loja(db, user, nome="Mega", shop_id=42)
    await _link(db, user, integ, item="10", sku="dg007.us")  # usado
    await _link(db, user, integ, item="11", sku="dg007.pi", morto=True)  # excluído na Shopee
    await _link(db, user, outra, item="12", sku="dg007.pi")  # outra loja
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, _ = await _criativo(db, marca, sku="dg007")
    assert await anuncio.anuncio_para(db, c, rede) == anuncio.SEM_ANUNCIO


async def test_dedicado_sem_estoque_nao_cai_pra_vitrine(db, make_user):
    user = await make_user()
    integ = await _loja(db, user)
    await _link(db, user, integ, item="58209528466", sku="dg093.ra", estoque=0)
    for sku in ("dg093.ra", "dg017.pi", "dg019.ra"):
        await _link(db, user, integ, item="58269759596", sku=sku, estoque=50)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, _ = await _criativo(db, marca, sku="dg093.ra")
    assert await anuncio.anuncio_para(db, c, rede) == anuncio.SEM_ESTOQUE


# ════════════════════════════════════════════════════════════ as guardas


async def test_guardas_da_conta(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    c, f = await _criativo(db, marca)
    sem_loja = await _conta_shopee(db, marca, None, conta="sem-loja")
    tok = (await svc.tokens_por_rede(db, [sem_loja.id])).get(sem_loja.id)
    assert await svc.pode_publicar(c, f, sem_loja, tok, session=db) == "conta_sem_loja"

    pendente = await _conta_shopee(db, marca, integ, conta="pendente", autorizada=False)
    tok = (await svc.tokens_por_rede(db, [pendente.id])).get(pendente.id)
    assert (
        await svc.pode_publicar(c, f, pendente, tok, session=db) == "conta_sem_autorizacao_shopee"
    )


async def test_cadeia_vencida_pede_reautorizar(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    c, f = await _criativo(db, marca)
    rede = await _conta_shopee(db, marca, integ, status="expirado")
    tok = (await svc.tokens_por_rede(db, [rede.id])).get(rede.id)
    assert svc.motivo_da_conta(rede, tok) == "conta_shopee_reautorizar"


@pytest.mark.parametrize(
    ("info", "motivo"),
    [
        (anuncio.InfoVideo(1080, 1920, 60.4, "h264"), "video_fora_da_duracao"),
        (anuncio.InfoVideo(1080, 1920, 3.0, "h264"), "video_fora_da_duracao"),
        (anuncio.InfoVideo(480, 854, 20.0, "h264"), "video_resolucao_baixa"),
        (anuncio.InfoVideo(1080, 1920, 25.4, "hevc"), "video_formato_nao_aceito"),
        (None, "video_ilegivel"),
    ],
)
async def test_guardas_do_formato(db, make_user, _video_bom, info, motivo):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca)
    _video_bom["v"] = info
    tok = (await svc.tokens_por_rede(db, [rede.id])).get(rede.id)
    assert await svc.pode_publicar(c, f, rede, tok, session=db) == motivo


async def test_mesmo_video_nao_vai_pra_duas_lojas_da_marca(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    mega = await _loja(db, user, nome="Mega", shop_id=99)
    await _link(db, user, mega, item="77", sku="dg046.pi")
    marca = await _marca(db)
    barbosa = await _conta_shopee(db, marca, integ)
    outra = await _conta_shopee(db, marca, mega, conta="mega")
    c, f = await _criativo(db, marca)
    db.add(
        MarketingPostagem(
            creative_id=c.id,
            file_id=f.id,
            rede_social_id=barbosa.id,
            plataforma="shopee",
            conta="barbosa",
            status="publicado",
            publicado_em=datetime.now(UTC) - timedelta(days=2),
        )
    )
    await db.commit()
    tok = (await svc.tokens_por_rede(db, [outra.id])).get(outra.id)
    assert await svc.pode_publicar(c, f, outra, tok, session=db) == anuncio.EM_OUTRA_LOJA


# ═══════════════════════════════════════════════════════════════ agendar


async def test_agendar_grava_snapshot_do_anuncio_e_aigc(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca)
    (p,) = await svc.agendar(db, creative=c, file=f, redes=[rede], legenda="F110L na lama #uranyx")
    sh = p.opcoes["shopee"]
    assert sh["item_id"] == 58262693089
    assert sh["aigc_label"] is True
    assert p.status == "pendente"


async def test_legenda_manual_acima_de_150_e_recusada(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca)
    with pytest.raises(svc.RoboError) as ei:
        await svc.agendar(db, creative=c, file=f, redes=[rede], legenda="x" * 151)
    assert ei.value.code == "legenda_longa_shopee"


LEGENDA_LONGA = (
    "O {{ marca }} F110L aguenta queda, água e poeira.\n"
    "Bateria que vai longe e tela grande pra ver tudo, o dia inteiro, sem medo de nada.\n"
    "Chama no WhatsApp (11) 98351-7003\n"
    "#uranyx #celularresistente #f110l #provadagua #rugged #ip68 #fossibot #resistente"
)


async def test_biblioteca_sai_curta_so_na_shopee(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    db.add(MarketingLegendaModelo(marca_id=marca.id, texto=LEGENDA_LONGA))
    await db.commit()
    shopee = await _conta_shopee(db, marca, integ)
    ig = await _conta_ig(db, marca)
    c, f = await _criativo(db, marca)
    criadas = await svc.agendar(db, creative=c, file=f, redes=[ig, shopee])
    por = {p.plataforma: p for p in criadas}
    assert "WhatsApp" in por["instagram"].legenda
    curta = por["shopee"].legenda
    assert len(curta.encode("utf-16-le")) // 2 <= 150
    assert "WhatsApp" not in curta and "98351" not in curta
    assert curta.startswith("O Uranyx F110L")
    # O rodízio continua girando: a variação usada é registrada.
    assert por["shopee"].legenda_modelo_id is not None


# ═════════════════════════════════════════════════════════ a autopostagem


async def test_robo_pula_os_videos_sem_anuncio_e_agenda_o_primeiro_bom(db, make_user):
    """Mais de 5 vídeos sem anúncio na loja na frente da fila: o robô não
    pode esbarrar no teto de pulos e desistir da conta."""
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    db.add(MarketingLegendaModelo(marca_id=marca.id, texto="{{ marca }} na Shopee #uranyx"))
    await db.commit()
    rede = await _conta_shopee(db, marca, integ)
    base = datetime(2026, 10, 1, 12, tzinfo=UTC)
    _bom, arquivo_bom = await _criativo(db, marca, sku="dg046.pi", criado=base)
    for i in range(7):
        await _criativo(
            db,
            marca,
            sku=f"dg9{i:02d}.pi",
            criado=base + timedelta(hours=i + 1),
            nome=f"sem-anuncio-{i}.mp4",
        )
    agora = datetime(2026, 10, 8, 15, 5, tzinfo=UTC)  # 12:05 BRT
    r = await autopostagem.rodada(db, agora=agora)
    assert r["agendadas"] == 1, r
    (p,) = (await db.execute(select(MarketingPostagem))).scalars().all()
    assert p.file_id == arquivo_bom.id
    assert p.rede_social_id == rede.id
    assert p.origem == "robo"
    assert p.opcoes["shopee"]["item_id"] == 58262693089


async def test_fila_da_tela_nao_promete_shopee_pra_video_sem_anuncio(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    _c1, f_bom = await _criativo(db, marca, sku="dg046.pi")
    _c2, f_sem = await _criativo(db, marca, sku="dg999.pi", nome="b.mp4")
    itens = await autopostagem.fila(db, marca.id)
    por_arquivo = {it.arquivo.id: [c.id for c in it.pendente_em] for it in itens}
    assert por_arquivo.get(f_bom.id) == [rede.id]
    assert f_sem.id not in por_arquivo


# ═════════════════════════════════════════════════════════════ publicador


class ShopeeFake:
    """A Shopee de mentira: anota cada chamada e responde o roteiro do teste."""

    def __init__(self) -> None:
        self.chamadas: list[str] = []
        self.status_upload = ["SUCCEEDED"]
        self.capas_respostas: list[Any] = [["https://img/1", "https://img/2", "https://img/3"]]
        self.erro_editar: list[ShopeeVideoError] = []
        self.erro_postar: list[ShopeeVideoError] = []
        self.detalhe_resposta: dict[str, Any] = {"status": 300}
        self.lista_resposta: dict[str, Any] = {"list": [], "has_more": False}
        self.editado: dict[str, Any] = {}
        self.apagados: list[str] = []
        self._token = TOKEN_FALSO

    async def iniciar_upload(self, *, file_name, file_size, duracao_s):
        self.chamadas.append("init")
        return f"br-{len([c for c in self.chamadas if c == 'init'])}", 32

    async def enviar_parte(self, vid, seq, conteudo):
        self.chamadas.append(f"parte{seq}")

    async def concluir_upload(self, vid):
        self.chamadas.append("concluir")

    async def resultado_upload(self, vid):
        self.chamadas.append("resultado")
        st = self.status_upload.pop(0) if len(self.status_upload) > 1 else self.status_upload[0]
        from app.services.marketing.shopee_video import ResultadoUpload

        return ResultadoUpload(status=st, motivo="formato inválido" if st == "FAILED" else None)

    async def capas(self, vid):
        self.chamadas.append("capas")
        r = (
            self.capas_respostas.pop(0)
            if len(self.capas_respostas) > 1
            else self.capas_respostas[0]
        )
        if isinstance(r, Exception):
            raise r
        return r

    async def editar(self, vid, *, legenda, capa, item_id, aigc_label=True):
        self.chamadas.append("editar")
        if self.erro_editar:
            raise self.erro_editar.pop(0)
        self.editado = {
            "vid": vid,
            "legenda": legenda,
            "capa": capa,
            "item_id": item_id,
            "aigc_label": aigc_label,
        }

    async def postar(self, vid):
        self.chamadas.append("postar")
        if self.erro_postar:
            raise self.erro_postar.pop(0)
        return "POST-ID-1=="

    async def detalhe(self, *, post_id=None, video_upload_id=None):
        self.chamadas.append("detalhe")
        return dict(self.detalhe_resposta)

    async def lista(self, *, publicados=True, pagina=1, por_pagina=20):
        self.chamadas.append("lista")
        return dict(self.lista_resposta)

    async def apagar_rascunho(self, vid):
        self.chamadas.append("apagar")
        self.apagados.append(vid)


@pytest.fixture
def shopee(monkeypatch):
    fake = ShopeeFake()
    recusados: list[str | None] = []

    async def credencial(rede_social_id, *, recusado=None, forcar=False, agora=None):
        recusados.append(recusado)
        cred = conta.Credencial(
            rede_social_id, 2047721, CHAVE_FALSA, TOKEN_FALSO, USER_SHOPEE, SHOP_ID, 0
        )
        return cred

    monkeypatch.setattr(conta.Credencial, "cliente", lambda self: fake)
    monkeypatch.setattr(conta, "credencial", credencial)
    fake.recusados = recusados  # type: ignore[attr-defined]

    conferencias: list[int] = []
    fake.conferencia = anuncio.Conferencia(ok=True, status="NORMAL", estoque=26)  # type: ignore[attr-defined]

    async def conferir(_s, _integ, item_id):
        conferencias.append(item_id)
        return fake.conferencia  # type: ignore[attr-defined]

    monkeypatch.setattr(anuncio, "conferir_ao_vivo", conferir)
    fake.conferencias = conferencias  # type: ignore[attr-defined]
    return fake


async def _sem_espera(_s: float) -> None:
    return None


# Orçamento folgado: o tique inteiro (subir → capa → rascunho → postar) cabe
# num só. O de produção (50 s) não espera os 65 s das capas — vira 2 tiques.
FOLGA = 600.0


async def _tique(db, pid, caminho, **kw):
    return await pub.publicar_postagem(
        db, pid, caminho, dormir=_sem_espera, **{"orcamento_s": FOLGA, **kw}
    )


async def _postagem_publicando(db, make_user, *, legenda="F110L na lama #uranyx"):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca)
    (p,) = await svc.agendar(db, creative=c, file=f, redes=[rede], legenda=legenda)
    (lease,) = await svc.proximas_para_publicar(db)
    assert lease.id == p.id and lease.status == "publicando"
    caminho = Path(get_settings().uploads_dir) / f.file_rel
    return p, caminho, rede


async def _relida(db: AsyncSession, pid) -> MarketingPostagem:
    return (
        await db.execute(
            select(MarketingPostagem)
            .where(MarketingPostagem.id == pid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_caminho_feliz_num_tique(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    acao = await _tique(db, p.id, caminho)
    assert acao == pub.PUBLICADO
    linha = await _relida(db, p.id)
    assert linha.status == "publicado"
    assert linha.post_external_id == "POST-ID-1=="
    assert linha.container_id == "br-1"
    assert linha.publicado_em is not None
    # O anúncio do snapshot, o aigc ligado, a capa do MEIO, a legenda do agendamento.
    assert shopee.editado == {
        "vid": "br-1",
        "legenda": "F110L na lama #uranyx",
        "capa": "https://img/2",
        "item_id": 58262693089,
        "aigc_label": True,
    }
    # Conferiu o anúncio ao vivo antes do rascunho (uma vez no tique).
    assert shopee.conferencias == [58262693089]
    assert shopee.chamadas.count("init") == 1
    assert shopee.chamadas.index("postar") > shopee.chamadas.index("editar")


async def test_processando_devolve_pra_fila_e_continua_sem_subir_de_novo(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.status_upload = ["PROCESSING"]
    acao = await _tique(db, p.id, caminho, orcamento_s=25)
    assert acao == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente"
    assert linha.container_id == "br-1"
    assert linha.attempts == 0
    assert linha.opcoes["shopee"]["progresso"]["etapa"] == "processando"
    # O próximo tique: a linha volta pelo lease e continua de onde parou.
    shopee.status_upload = ["SUCCEEDED"]
    (lease,) = await svc.proximas_para_publicar(db)
    assert lease.id == p.id
    acao = await _tique(db, p.id, caminho)
    assert acao == pub.PUBLICADO
    assert shopee.chamadas.count("init") == 1


async def test_post_sem_resposta_vai_pra_revisar_e_reconciliador_confirma(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [ShopeeVideoRedeError("sem_resposta", "ReadTimeout")]
    acao = await _tique(db, p.id, caminho)
    assert acao == pub.REVISAR
    linha = await _relida(db, p.id)
    assert linha.status == "revisar"
    assert linha.container_id == "br-1"
    assert "NÃO publique de novo" in linha.result
    # Retentar às cegas é recusado (o post pode estar no ar).
    with pytest.raises(svc.RoboError):
        await svc.retentar(db, linha)
    # O reconciliador acha o upload na lista de publicados.
    shopee.lista_resposta = {
        "list": [{"video_upload_id": "br-1", "post_id": "PID-RECON"}],
        "has_more": False,
    }
    acao = await pub.reconciliar(db, p.id)
    assert acao == pub.PUBLICADO
    linha = await _relida(db, p.id)
    assert linha.status == "publicado" and linha.post_external_id == "PID-RECON"
    assert shopee.chamadas.count("init") == 1


async def test_rascunho_que_nao_saiu_volta_pra_postar_o_mesmo_rascunho(db, make_user, shopee):
    """O post não respondeu e o reconciliador acha o vídeo ainda RASCUNHO: a
    linha volta pra fila na etapa de postar, com o MESMO video_upload_id — e
    nunca pede "publique de novo" (que subiria outro vídeo)."""
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [ShopeeVideoRedeError("sem_resposta", "ReadTimeout")]
    await _tique(db, p.id, caminho)
    shopee.detalhe_resposta = {"status": 200}
    assert await pub.reconciliar(db, p.id) == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente" and linha.container_id == "br-1"
    prog = linha.opcoes["shopee"]["progresso"]
    assert prog["etapa"] == "postar" and prog["video_upload_id"] == "br-1"
    assert prog["post_recusado"] is True and prog["post_chamado_em"]
    assert "publique de novo" not in linha.result.lower()
    # O próximo tique publica o MESMO rascunho: nada de subir de novo.
    (lease,) = await svc.proximas_para_publicar(db)
    assert lease.id == p.id
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert shopee.chamadas.count("init") == 1
    assert shopee.chamadas.count("postar") == 2
    assert (await _relida(db, p.id)).post_external_id == "POST-ID-1=="


@pytest.mark.parametrize(
    ("erro", "trecho"),
    [
        (
            ShopeeVideoError("copyright_not_agree", "Not Agree Shopee videos Terms of Service"),
            "Termos do Shopee Vídeo",
        ),
        (
            ShopeeVideoError(
                "unauthorized", "The user is not authorized and needs to be in toggle"
            ),
            "toggle",
        ),
    ],
)
async def test_erro_que_so_gente_resolve_vira_frase_e_nao_volta(
    db, make_user, shopee, erro, trecho
):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [erro]
    acao = await _tique(db, p.id, caminho)
    assert acao == pub.FALHOU
    linha = await _relida(db, p.id)
    assert linha.status == "falhou"
    assert trecho in linha.result
    # Não saiu: o container é zerado e o "tentar de novo" humano funciona.
    assert linha.container_id is None
    assert TOKEN_FALSO not in linha.result and CHAVE_FALSA not in linha.result
    voltou = await svc.retentar(db, linha)
    assert voltou.status == "pendente"


async def test_post_ja_feito_no_tique_que_morreu_vira_publicado(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [
        ShopeeVideoError("batch_process_failed", "task can not be process under the current status")
    ]
    shopee.detalhe_resposta = {"status": 300, "post_id": "JA-SAIU"}
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert (await _relida(db, p.id)).post_external_id == "JA-SAIU"


async def test_anuncio_sem_estoque_ao_vivo_nao_posta(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.conferencia = anuncio.Conferencia(
        ok=False, motivo="anúncio 58262693089 está sem estoque na Shopee"
    )
    acao = await _tique(db, p.id, caminho)
    assert acao == pub.FALHOU
    assert "postar" not in shopee.chamadas and "editar" not in shopee.chamadas
    assert "sem estoque" in (await _relida(db, p.id)).result


async def test_token_recusado_renova_uma_vez_e_segue(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_editar = [ShopeeVideoError("error_auth", "Invalid access_token")]
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert TOKEN_FALSO in shopee.recusados  # pediu a renovação COM o token recusado
    assert shopee.chamadas.count("editar") == 2


async def test_transcodificacao_recusada_falha_sem_ambiguo(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.status_upload = ["FAILED"]
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert "recusou o vídeo" in linha.result and linha.container_id is None


async def test_capa_ainda_nao_pronta_espera(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.capas_respostas = [ShopeeVideoError("error_param", "upload video less than 1 min")]
    acao = await _tique(db, p.id, caminho, orcamento_s=25)
    assert acao == pub.AGUARDANDO
    assert (await _relida(db, p.id)).opcoes["shopee"]["progresso"]["etapa"] == "capa"


async def test_worker_morto_antes_do_post_volta_pra_fila(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.status_upload = ["PROCESSING"]
    await _tique(db, p.id, caminho, orcamento_s=25)
    # Simula o lease seguinte morrendo no meio: linha `publicando`, sem post.
    (lease,) = await svc.proximas_para_publicar(db)
    # Com menos de 25 min o tique que pegou a linha pode estar vivo (o cron
    # tem timeout de 1200 s): o reconciliador não mexe.
    assert await pub.reconciliar(db, lease.id) == pub.AGUARDANDO
    assert (await _relida(db, p.id)).status == "publicando"
    await db.execute(
        update(MarketingPostagem)
        .where(MarketingPostagem.id == p.id)
        .values(claimed_at=datetime.now(UTC) - timedelta(minutes=26))
    )
    await db.commit()
    assert await pub.reconciliar(db, lease.id) == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente" and linha.container_id == "br-1"


async def test_worker_publica_shopee_pelo_tique(db, make_user, shopee, monkeypatch):
    """O cron de verdade (`marketing_postagens_publicar`) despacha a Shopee
    pro publicador retomável — revalidando as guardas antes."""
    from app import worker

    monkeypatch.setattr(worker._settings, "enable_marketing", True)
    monkeypatch.setattr(worker._settings, "marketing_postagem_commit", True)
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca)
    (p,) = await svc.agendar(db, creative=c, file=f, redes=[rede], legenda="F110L #uranyx")
    monkeypatch.setattr(pub, "ESPERA_CAPA_S", 0.0)
    await worker.marketing_postagens_publicar({})
    linha = await _relida(db, p.id)
    assert linha.status == "publicado", linha.result
    assert linha.post_external_id == "POST-ID-1=="


# ═══════════════════════════════════════════════════════════ desempenho


async def test_metricas_da_shopee_pela_lista_e_apagado_vira_removido(db, make_user, shopee):
    """O Desempenho lê a Shopee pela lista de publicados da conta (views,
    curtidas, comentários); o post que não está lá e volta APAGADO no
    detalhe é "removido", não falha de leitura."""
    from app.models import MarketingPostagemMetrica
    from app.services.marketing import metricas

    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca)
    _c2, f2 = await _criativo(db, marca, nome="b.mp4")
    agora = datetime.now(UTC)
    for arquivo, pid in ((f, "P-NO-AR"), (f2, "P-APAGADO")):
        db.add(
            MarketingPostagem(
                creative_id=c.id if arquivo is f else _c2.id,
                file_id=arquivo.id,
                rede_social_id=rede.id,
                plataforma="shopee",
                conta="barbosa",
                status="publicado",
                post_external_id=pid,
                publicado_em=agora - timedelta(hours=3),
            )
        )
    await db.commit()
    shopee.lista_resposta = {
        "list": [{"post_id": "P-NO-AR", "views": 120, "likes": 7, "comments": 2}],
        "has_more": False,
    }
    shopee.detalhe_resposta = {"status": 400}
    r = await metricas.coletar(db, modo="completo")
    assert r["total"] == 2
    linhas = {
        m.postagem_id: m
        for m in (await db.execute(select(MarketingPostagemMetrica))).scalars().all()
    }
    por_post = {
        p.post_external_id: linhas[p.id]
        for p in (await db.execute(select(MarketingPostagem))).scalars().all()
    }
    no_ar = por_post["P-NO-AR"]
    assert (no_ar.views, no_ar.curtidas, no_ar.comentarios) == (120, 7, 2)
    assert no_ar.compartilhamentos is None and no_ar.erro is None
    assert metricas.foi_removido(por_post["P-APAGADO"].erro)


# ═══════════════════════════════════════ correções da revisão (08/10/2026)


async def _token_da_conta(db: AsyncSession, rede_id) -> RedeSocialToken:
    return (
        await db.execute(
            select(RedeSocialToken)
            .where(RedeSocialToken.rede_social_id == rede_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


# ─────────────────────── o lote que falha, pelo cliente HTTP DE VERDADE


@pytest.fixture
def shopee_http(monkeypatch):
    """Credencial e conferência de anúncio dubladas, mas o cliente de vídeo é o
    de verdade: a Shopee responde por respx no formato REAL (corpo do lote)."""

    async def credencial(rede_social_id, *, recusado=None, forcar=False, agora=None):
        return conta.Credencial(
            rede_social_id, 2047721, CHAVE_FALSA, TOKEN_FALSO, USER_SHOPEE, SHOP_ID, 0
        )

    async def conferir(_s, _integ, item_id):
        return anuncio.Conferencia(ok=True, status="NORMAL", estoque=26)

    monkeypatch.setattr(conta, "credencial", credencial)
    monkeypatch.setattr(anuncio, "conferir_ao_vivo", conferir)


def _ok_http(resp: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200, json={"error": "", "message": "", "request_id": "rq", "response": resp or {}}
    )


def _lote_falho_http(motivo: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "error": "batch_process_failed",
            "message": "Please check failure_list for detailed reason",
            "request_id": "rq-lote",
            "response": {
                "success_list": [],
                "failure_list": [{"fail_video_upload_id": "br-1", "failed_reason": motivo}],
            },
        },
    )


def _shopee_ate_o_rascunho() -> dict[str, Any]:
    h = sv.API_HOST
    return {
        "init": respx.post(f"{h}{sv.PATH_INIT}").mock(
            return_value=_ok_http({"video_upload_id": "br-1", "part_size": 1024})
        ),
        "parte": respx.post(f"{h}{sv.PATH_PARTE}").mock(return_value=_ok_http()),
        "concluir": respx.post(f"{h}{sv.PATH_CONCLUIR}").mock(return_value=_ok_http()),
        "resultado": respx.get(f"{h}{sv.PATH_RESULTADO}").mock(
            return_value=_ok_http({"status": "SUCCEEDED", "video_info": {"duration": 24}})
        ),
        "capas": respx.get(f"{h}{sv.PATH_CAPAS}").mock(
            return_value=_ok_http({"image_url_list": ["https://img/1"]})
        ),
    }


@respx.mock
async def test_post_ja_feito_pelo_corpo_real_do_lote_vira_publicado(db, make_user, shopee_http):
    """O `post_video` volta `batch_process_failed` no topo, com "current
    status" SÓ na failure_list: a conferência pelo detalhe roda e acha o post
    no ar — nada de `falhou` (que liberaria subir e postar de novo)."""
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    rotas = _shopee_ate_o_rascunho()
    respx.post(f"{sv.API_HOST}{sv.PATH_EDITAR}").mock(
        return_value=_ok_http({"success_list": ["br-1"], "failure_list": []})
    )
    postar = respx.post(f"{sv.API_HOST}{sv.PATH_POSTAR}").mock(
        return_value=_lote_falho_http("task can not be process under the current status")
    )
    detalhe = respx.get(f"{sv.API_HOST}{sv.PATH_DETALHE}").mock(
        return_value=_ok_http({"status": 300, "post_id": "JA-NO-AR=="})
    )
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    linha = await _relida(db, p.id)
    assert linha.status == "publicado" and linha.post_external_id == "JA-NO-AR=="
    assert postar.call_count == 1 and detalhe.call_count == 1
    assert rotas["init"].call_count == 1


@respx.mock
async def test_please_retry_pelo_corpo_real_repete_o_edit_uma_vez(db, make_user, shopee_http):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    _shopee_ate_o_rascunho()
    editar = respx.post(f"{sv.API_HOST}{sv.PATH_EDITAR}").mock(
        side_effect=[
            _lote_falho_http("can not edit video info,please retry"),
            _ok_http({"success_list": ["br-1"], "failure_list": []}),
        ]
    )
    respx.post(f"{sv.API_HOST}{sv.PATH_POSTAR}").mock(
        return_value=_ok_http(
            {
                "success_list": [{"success_video_upload_id": "br-1", "post_id": "P1"}],
                "failure_list": [],
            }
        )
    )
    respx.get(f"{sv.API_HOST}{sv.PATH_DETALHE}").mock(return_value=_ok_http({"status": 300}))
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert editar.call_count == 2
    assert (await _relida(db, p.id)).post_external_id == "P1"


@respx.mock
async def test_post_aceito_sem_post_id_pelo_corpo_real_vai_pra_revisar(db, make_user, shopee_http):
    """`error` vazio, o vídeo na lista de sucesso SEM post_id: pode ter saído.
    `revisar` com o container mantido — o "tentar de novo" se recusa."""
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    _shopee_ate_o_rascunho()
    respx.post(f"{sv.API_HOST}{sv.PATH_EDITAR}").mock(
        return_value=_ok_http({"success_list": ["br-1"], "failure_list": []})
    )
    respx.post(f"{sv.API_HOST}{sv.PATH_POSTAR}").mock(
        return_value=_ok_http(
            {
                "success_list": [{"success_video_upload_id": "br-1", "post_id": ""}],
                "failure_list": [],
            }
        )
    )
    assert await _tique(db, p.id, caminho) == pub.REVISAR
    linha = await _relida(db, p.id)
    assert linha.status == "revisar" and linha.container_id == "br-1"
    with pytest.raises(svc.RoboError):
        await svc.retentar(db, linha)


@pytest.mark.parametrize(
    "erro",
    [
        ShopeeVideoAmbiguoError("post_sem_confirmacao", "sem post_id"),
        ShopeeVideoError("resposta_invalida", "HTTP 502 sem JSON"),
        ShopeeVideoError("error_server", "Internal error"),
    ],
)
async def test_post_sem_recusa_explicita_nunca_libera_subir_de_novo(db, make_user, shopee, erro):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [erro]
    assert await _tique(db, p.id, caminho) == pub.REVISAR
    linha = await _relida(db, p.id)
    assert linha.status == "revisar" and linha.container_id == "br-1"
    assert linha.opcoes["shopee"]["progresso"].get("post_recusado") is None
    assert shopee.apagados == []


# ───────────────────────────────────────── erro que só gente resolve PARA a conta


async def test_termos_nao_aceitos_param_a_conta_mantem_o_rascunho_e_o_robo_nao_agenda(
    db, make_user, shopee
):
    p, caminho, rede = await _postagem_publicando(db, make_user)
    rede_id = rede.id
    shopee.erro_postar = [
        ShopeeVideoError("copyright_not_agree", "Not Agree Shopee videos Terms of Service")
    ]
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert linha.status == "falhou" and linha.container_id is None
    prog = linha.opcoes["shopee"]["progresso"]
    # O rascunho pronto FICA (só a conta parou) — e não é apagado.
    assert prog["etapa"] == "postar" and prog["video_upload_id"] == "br-1"
    assert prog.get("post_chamado_em") is None and prog.get("post_recusado") is None
    assert shopee.apagados == []
    assert "tentar de novo" in linha.result
    tok = await _token_da_conta(db, rede_id)
    assert tok.status == "bloqueado" and "Termos" in tok.last_error
    assert svc.motivo_da_conta(await db.get(RedeSocial, rede_id), tok) == "conta_shopee_bloqueada"

    # O robô NÃO agenda outro vídeo nesta conta (cada vaga queimaria um vídeo).
    db.add(MarketingLegendaModelo(marca_id=rede.marca_id, texto="{{ marca }} #uranyx"))
    await db.commit()
    marca = await db.get(Marca, rede.marca_id)
    await _criativo(db, marca, sku="dg046.pi", nome="outro.mp4")
    r = await autopostagem.rodada(db, agora=datetime.now(UTC).replace(hour=23, minute=0))
    assert r["agendadas"] == 0 and r["recusadas"] == 1, r
    assert len((await db.execute(select(MarketingPostagem))).scalars().all()) == 1

    # Resolvido fora (Termos aceitos): libera e o "tentar de novo" só POSTA.
    await conta.liberar(db, rede_id)
    await db.commit()
    voltou = await svc.retentar(db, await _relida(db, p.id))
    assert voltou.status == "pendente"
    (lease,) = await svc.proximas_para_publicar(db)
    assert lease.id == p.id
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert shopee.chamadas.count("init") == 1  # sem subir o vídeo de novo
    assert shopee.chamadas.count("postar") == 2


async def test_autorizacao_desfeita_pede_autorizar_de_novo(db, make_user, shopee):
    p, caminho, rede = await _postagem_publicando(db, make_user)
    shopee.erro_editar = [ShopeeVideoError("user_no_linked", "Partner and user has no linked")]
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    tok = await _token_da_conta(db, rede.id)
    assert tok.status == "expirado"
    assert svc.motivo_da_conta(await db.get(RedeSocial, rede.id), tok) == "conta_shopee_reautorizar"


# ─────────────────────────────── "can not find video meta" não é laço eterno


async def test_video_meta_que_nao_aparece_espera_com_teto_e_recusa_upload_falho(
    db, make_user, shopee
):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    meta = ShopeeVideoError("error_param", "can not find video meta")
    shopee.erro_editar = [meta]
    assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente"
    assert linha.opcoes["shopee"]["progresso"]["etapa"] == "rascunho"
    assert shopee.conferencias == [58262693089]

    # Próximo tique (dentro de 10 min): NÃO pergunta à loja de novo antes do edit.
    shopee.erro_editar = [meta]
    await svc.proximas_para_publicar(db)
    assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
    assert shopee.conferencias == [58262693089]

    # Passado o teto de 30 min desde o pronto: desiste (nada foi publicado).
    linha = await _relida(db, p.id)
    opcoes = dict(linha.opcoes)
    opcoes["shopee"]["progresso"]["pronto_em"] = (
        datetime.now(UTC) - pub.TETO_META - timedelta(minutes=1)
    ).isoformat()
    await db.execute(
        update(MarketingPostagem).where(MarketingPostagem.id == p.id).values(opcoes=opcoes)
    )
    await db.commit()
    shopee.erro_editar = [meta]
    await svc.proximas_para_publicar(db)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    assert "30 min" in (await _relida(db, p.id)).result


async def test_video_meta_com_upload_falho_recusa_na_hora(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_editar = [ShopeeVideoError("error_param", "can not find video meta")]
    shopee.status_upload = ["SUCCEEDED", "FAILED"]
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    assert "FAILED" in (await _relida(db, p.id)).result


# ─────────────────────────────────── o upload não volta pro começo em laço


async def test_upload_task_not_found_sobe_de_novo_so_uma_vez(db, make_user, shopee, monkeypatch):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    perdido = ShopeeVideoError("error_param", "Upload task not found.")

    async def resultado(vid):
        shopee.chamadas.append("resultado")
        raise perdido

    monkeypatch.setattr(shopee, "resultado_upload", resultado)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    assert shopee.chamadas.count("init") == 2  # o original + UMA vez de novo
    assert "subido de novo" in (await _relida(db, p.id)).result


async def test_not_found_de_outra_coisa_nao_manda_subir_de_novo(db, make_user, shopee, monkeypatch):
    p, caminho, _rede = await _postagem_publicando(db, make_user)

    async def resultado(vid):
        raise ShopeeVideoError("error_param", "item not found in shop")

    monkeypatch.setattr(shopee, "resultado_upload", resultado)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    assert shopee.chamadas.count("init") == 1


async def test_sem_folga_no_ciclo_nem_comeca_a_subir(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    assert await _tique(db, p.id, caminho, orcamento_s=pub.MIN_SOBRA_SUBIR_S - 1) == pub.AGUARDANDO
    assert "init" not in shopee.chamadas
    assert (await _relida(db, p.id)).status == "pendente"


# ───────────────────────────────── a cerca do lease (nada de dois tiques na linha)


async def _outro_tique_pega(pid) -> None:
    """Outro tique (ou o reconciliador) assumiu a linha: claim novo."""
    import app.db as _db

    async with _db.SessionLocal() as s2:
        await s2.execute(
            update(MarketingPostagem)
            .where(MarketingPostagem.id == pid)
            .values(claimed_at=datetime.now(UTC) + timedelta(seconds=5))
        )
        await s2.commit()


async def test_cerca_do_lease_para_antes_de_subir_o_arquivo(db, make_user, shopee, monkeypatch):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    original = shopee.iniciar_upload

    async def iniciar(**kw):
        await _outro_tique_pega(p.id)
        return await original(**kw)

    monkeypatch.setattr(shopee, "iniciar_upload", iniciar)
    assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
    assert not any(c.startswith("parte") for c in shopee.chamadas)
    linha = await _relida(db, p.id)
    assert linha.status == "publicando" and linha.container_id is None


async def test_cerca_do_lease_para_antes_de_postar(db, make_user, shopee, monkeypatch):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    original = shopee.editar

    async def editar(vid, **kw):
        await original(vid, **kw)
        await _outro_tique_pega(p.id)

    monkeypatch.setattr(shopee, "editar", editar)
    assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
    assert "postar" not in shopee.chamadas
    prog = (await _relida(db, p.id)).opcoes["shopee"]["progresso"]
    assert prog.get("post_chamado_em") is None


# ─────────────────────── "current status" sem conseguir conferir = dúvida


async def test_current_status_sem_conferencia_vai_pra_revisar_nunca_pra_fila(
    db, make_user, shopee, monkeypatch
):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [
        ShopeeVideoError("batch_process_failed", "task can not be process under the current status")
    ]

    async def detalhe(**kw):
        raise conta.ContaShopeeError("renovacao_falhou", "a Shopee não respondeu")

    monkeypatch.setattr(shopee, "detalhe", detalhe)
    assert await _tique(db, p.id, caminho) == pub.REVISAR
    linha = await _relida(db, p.id)
    assert linha.status == "revisar" and linha.container_id == "br-1"


async def test_aguardar_com_post_possivelmente_no_ar_vira_revisar(
    db, make_user, shopee, monkeypatch
):
    """A renovação do token falha DEPOIS de o post ter sido chamado (recusado
    só pelo token): a linha não pode voltar pra `pendente`."""
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [ShopeeVideoError("error_auth", "Invalid access_token")]

    async def credencial(rede_social_id, *, recusado=None, forcar=False, agora=None):
        if recusado:
            raise conta.ContaShopeeError("renovacao_falhou", "sem resposta")
        return conta.Credencial(
            rede_social_id, 2047721, CHAVE_FALSA, TOKEN_FALSO, USER_SHOPEE, SHOP_ID, 0
        )

    monkeypatch.setattr(conta, "credencial", credencial)
    assert await _tique(db, p.id, caminho) == pub.REVISAR
    assert (await _relida(db, p.id)).status == "revisar"


# ─────────────────────────────────── rascunho órfão não fica na loja


async def test_falha_depois_do_rascunho_apaga_o_rascunho(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    # 1º tique: rascunho pronto, o post esbarra no limite (recusa explícita).
    shopee.erro_postar = [ShopeeVideoError("error_rate_limit", "Too many requests")]
    assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente"
    assert linha.opcoes["shopee"]["progresso"]["post_recusado"] is True
    # 2º tique: o estoque acabou → não posta, e o rascunho sai da Shopee.
    shopee.conferencia = anuncio.Conferencia(ok=False, motivo="anúncio sem estoque na Shopee")
    shopee.detalhe_resposta = {"status": 200}
    await svc.proximas_para_publicar(db)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert linha.status == "falhou" and linha.container_id is None
    assert shopee.apagados == ["br-1"]
    assert shopee.chamadas.count("postar") == 1


async def test_rascunho_que_ja_saiu_nunca_e_apagado(db, make_user, shopee):
    """A limpeza confere o status antes: o que a Shopee mostra publicado não
    é tocado."""
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [ShopeeVideoError("error_rate_limit", "Too many requests")]
    await _tique(db, p.id, caminho)
    shopee.conferencia = anuncio.Conferencia(ok=False, motivo="sem estoque")
    shopee.detalhe_resposta = {"status": 300, "post_id": "X"}
    await svc.proximas_para_publicar(db)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    assert shopee.apagados == []


# ───────────────────── soluço da API da LOJA não queima o vídeo (conferir_ao_vivo real)


@respx.mock
async def test_soluco_da_api_da_loja_espera_em_vez_de_falhar(db, make_user, shopee, monkeypatch):
    from app.models import Integration

    monkeypatch.setattr(anuncio, "conferir_ao_vivo", _CONFERIR_REAL)
    monkeypatch.setattr(get_settings(), "shopee_use_sandbox", False)
    p, caminho, rede = await _postagem_publicando(db, make_user)
    integ = await db.get(Integration, rede.integration_id)
    integ.credentials = encrypt_json(
        {
            "shop_id": SHOP_ID,
            "access_token": "da-loja",
            "refresh_token": "rt-loja",
            "partner_id": 1,
            "partner_key": "k",
            "expires_at": int(datetime.now(UTC).timestamp()) + 3600,
        }
    )
    await db.commit()
    respx.get("https://partner.shopeemobile.com/api/v2/product/get_item_base_info").mock(
        return_value=httpx.Response(
            200,
            json={
                "error": "",
                "response": {
                    "item_list": [
                        {"item_id": 58262693089, "item_status": "NORMAL", "has_model": True}
                    ]
                },
            },
        )
    )
    respx.get("https://partner.shopeemobile.com/api/v2/product/get_model_list").mock(
        return_value=httpx.Response(200, json={"error": "error_server", "message": "system busy"})
    )
    assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente" and linha.attempts == 0
    assert "editar" not in shopee.chamadas
    assert linha.opcoes["shopee"]["progresso"]["falhas_seguidas"] == 1


# ─────────────────────────────────────────────── o worker com as outras redes


async def test_worker_tique_misto_instagram_sai_antes_e_shopee_espera_sem_segurar(
    db, make_user, shopee, monkeypatch
):
    """IG e Shopee da mesma marca no MESMO tique (a grade das 12h): o Reel
    sai primeiro, a Shopee recebe no máximo o orçamento curto e, ainda
    processando, volta pra fila com o upload guardado."""
    from app import worker
    from app.services.marketing import meta_client

    monkeypatch.setattr(worker._settings, "enable_marketing", True)
    monkeypatch.setattr(worker._settings, "marketing_postagem_commit", True)
    monkeypatch.setattr(worker._settings, "marketing_postagem_upload", "binario")
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    shopee_conta = await _conta_shopee(db, marca, integ)
    ig = await _conta_ig(db, marca)
    c, f = await _criativo(db, marca)
    criadas = await svc.agendar(
        db, creative=c, file=f, redes=[shopee_conta, ig], legenda="F110L #uranyx"
    )
    por = {x.plataforma: x.id for x in criadas}
    # A da Shopee é a MAIS ANTIGA: o lease a devolve primeiro — é o tique
    # que tem de pô-la por último.
    await db.execute(
        update(MarketingPostagem)
        .where(MarketingPostagem.id == por["shopee"])
        .values(created_at=datetime.now(UTC) - timedelta(minutes=5))
    )
    await db.commit()
    shopee.status_upload = ["PROCESSING"]
    ordem: list[str] = []

    async def reel(**kw):
        ordem.append(f"ig:shopee_tocada={'init' in shopee.chamadas}")
        return meta_client.ResultadoPublicacao(
            ok=True, post_external_id="ig-1", post_url="https://ig/p/1"
        )

    monkeypatch.setattr(meta_client, "publicar_reel_instagram", reel)
    orcamentos: list[float] = []
    original = pub.publicar_postagem

    async def publicar(s, pid, caminho, **kw):
        orcamentos.append(kw.get("orcamento_s"))
        return await original(s, pid, caminho, dormir=_sem_espera, **kw)

    monkeypatch.setattr(pub, "publicar_postagem", publicar)
    await worker.marketing_postagens_publicar({})
    assert ordem == ["ig:shopee_tocada=False"]
    linha_ig = await _relida(db, por["instagram"])
    assert linha_ig.status == "publicado" and linha_ig.post_external_id == "ig-1"
    linha_sh = await _relida(db, por["shopee"])
    assert linha_sh.status == "pendente" and linha_sh.container_id == "br-1"
    assert orcamentos and 0 < orcamentos[0] <= pub.ORCAMENTO_TICK_S


async def test_reconciliador_do_worker_com_instagram_e_shopee(db, make_user, shopee, monkeypatch):
    from app import worker
    from app.services.marketing import meta_client

    monkeypatch.setattr(worker._settings, "enable_marketing", True)
    monkeypatch.setattr(worker._settings, "marketing_postagem_commit", True)
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    shopee_conta = await _conta_shopee(db, marca, integ)
    ig = await _conta_ig(db, marca)
    c, f = await _criativo(db, marca)
    _c2, f2 = await _criativo(db, marca, nome="b.mp4")
    agora = datetime.now(UTC)

    def presa(rede, arquivo, criativo_id, *, minutos, container, progresso=None):
        return MarketingPostagem(
            creative_id=criativo_id,
            file_id=arquivo.id,
            rede_social_id=rede.id,
            plataforma=rede.plataforma,
            conta=rede.conta,
            status="publicando",
            claimed_at=agora - timedelta(minutes=minutos),
            container_id=container,
            opcoes={"shopee": {"item_id": 58262693089, "progresso": progresso}}
            if progresso
            else {},
        )

    p_ig = presa(ig, f, c.id, minutos=20, container="ig-container")
    p_sh_morta = presa(
        shopee_conta,
        f,
        c.id,
        minutos=30,
        container="br-7",
        progresso={"etapa": "processando", "video_upload_id": "br-7"},
    )
    p_sh_viva = presa(
        shopee_conta,
        f2,
        _c2.id,
        minutos=20,
        container="br-8",
        progresso={"etapa": "processando", "video_upload_id": "br-8"},
    )
    db.add_all([p_ig, p_sh_morta, p_sh_viva])
    await db.commit()
    consultas: list[str] = []

    async def consultar(**kw):
        consultas.append(kw["container_id"])
        return meta_client.ResultadoPublicacao(
            ok=True, post_external_id="ig-9", post_url="https://ig/p/9"
        )

    monkeypatch.setattr(meta_client, "consultar_publicacao", consultar)
    await worker.marketing_postagens_reconciliar({})
    assert consultas == ["ig-container"]
    assert (await _relida(db, p_ig.id)).status == "publicado"
    morta = await _relida(db, p_sh_morta.id)
    assert morta.status == "pendente" and morta.container_id == "br-7"
    # Pega há 20 min: o tique (timeout 1200 s) pode estar vivo — intocada.
    assert (await _relida(db, p_sh_viva.id)).status == "publicando"
    assert "init" not in shopee.chamadas


async def test_worker_recusa_na_guarda_fecha_a_shopee_pela_maquina(
    db, make_user, shopee, monkeypatch
):
    """A guarda da hora de publicar recusou (criativo reprovado) uma linha
    Shopee que já tinha rascunho pronto: falhou com o container zerado (o
    "tentar de novo" funciona) e o rascunho sai da Shopee."""
    from app import worker

    monkeypatch.setattr(worker._settings, "enable_marketing", True)
    monkeypatch.setattr(worker._settings, "marketing_postagem_commit", True)
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [ShopeeVideoError("error_rate_limit", "Too many requests")]
    assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
    c = await db.get(MarketingCreative, p.creative_id)
    c.aprovado = False
    await db.commit()
    shopee.detalhe_resposta = {"status": 200}
    await worker.marketing_postagens_publicar({})
    linha = await _relida(db, p.id)
    assert linha.status == "falhou" and linha.container_id is None
    assert "criativo_nao_aprovado" in linha.result
    assert shopee.apagados == ["br-1"]


async def test_devolver_sem_tocar_na_shopee_e_sem_perder_o_progresso(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.status_upload = ["PROCESSING"]
    await _tique(db, p.id, caminho, orcamento_s=25)
    (lease,) = await svc.proximas_para_publicar(db)
    antes = list(shopee.chamadas)
    assert await pub.devolver(db, lease.id, "a fila do minuto encheu") == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente" and linha.container_id == "br-1"
    assert linha.opcoes["shopee"]["progresso"]["etapa"] == "processando"
    assert shopee.chamadas == antes


# ──────────────────────────────── desempenho: um post apagado não derruba os outros


async def test_metricas_post_sem_registro_vira_removido_e_os_outros_seguem(
    db, make_user, shopee, monkeypatch
):
    from app.models import MarketingPostagemMetrica
    from app.services.marketing import metricas

    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca)
    c2, f2 = await _criativo(db, marca, nome="b.mp4")
    c3, f3 = await _criativo(db, marca, nome="c.mp4")
    agora = datetime.now(UTC)
    for cr, arquivo, pid in ((c, f, "P-NO-AR"), (c2, f2, "P-SUMIU"), (c3, f3, "P-ERRO")):
        db.add(
            MarketingPostagem(
                creative_id=cr.id,
                file_id=arquivo.id,
                rede_social_id=rede.id,
                plataforma="shopee",
                conta="barbosa",
                status="publicado",
                post_external_id=pid,
                publicado_em=agora - timedelta(hours=3),
            )
        )
    await db.commit()
    shopee.lista_resposta = {
        "list": [{"post_id": "P-NO-AR", "views": 50, "likes": 3, "comments": 1}],
        "has_more": False,
    }

    async def detalhe(*, post_id=None, video_upload_id=None):
        if post_id == "P-SUMIU":
            raise ShopeeVideoError(
                "error_param", "videoUploadIdList all illegal,no record in database"
            )
        raise ShopeeVideoError("error_server", "system busy")

    monkeypatch.setattr(shopee, "detalhe", detalhe)
    await metricas.coletar(db, modo="completo")
    linhas = {
        m.postagem_id: m
        for m in (await db.execute(select(MarketingPostagemMetrica))).scalars().all()
    }
    por_post = {
        p.post_external_id: linhas[p.id]
        for p in (await db.execute(select(MarketingPostagem))).scalars().all()
    }
    assert por_post["P-NO-AR"].views == 50 and por_post["P-NO-AR"].erro is None
    assert metricas.foi_removido(por_post["P-SUMIU"].erro)
    assert por_post["P-ERRO"].erro and not metricas.foi_removido(por_post["P-ERRO"].erro)


# ─────────────────────── a fila da tela não promete Shopee pra vídeo que o robô pula


async def test_fila_da_tela_tira_da_shopee_o_video_em_formato_que_o_robo_pula(
    db, make_user, _video_bom
):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    shopee_conta = await _conta_shopee(db, marca, integ)
    ig = await _conta_ig(db, marca)
    ig.postagem_hora_inicio = 12
    await db.commit()
    _c, f = await _criativo(db, marca, sku="dg046.pi")
    db.add(
        MarketingPostagem(
            creative_id=_c.id,
            file_id=f.id,
            rede_social_id=ig.id,
            plataforma="instagram",
            conta=ig.conta,
            status="publicado",
            publicado_em=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await db.commit()
    _video_bom["v"] = anuncio.InfoVideo(1080, 1920, 25.4, "hevc")  # o flat_3c.mp4
    itens = await autopostagem.fila(db, marca.id)
    assert f.id not in {it.arquivo.id for it in itens}
    assert await autopostagem.proximo_criativo(db, shopee_conta) is None
    # Em H.264 o mesmo vídeo volta a esperar a Shopee — e o robô o pega.
    _video_bom["v"] = anuncio.InfoVideo(1080, 1920, 25.4, "h264")
    anuncio._CACHE.clear()
    itens = await autopostagem.fila(db, marca.id)
    assert {it.arquivo.id: [x.id for x in it.pendente_em] for it in itens}[f.id] == [
        shopee_conta.id
    ]
    assert (await autopostagem.proximo_criativo(db, shopee_conta))[1].id == f.id
