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

import pytest
from sqlalchemy import select
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
from app.services.marketing import shopee_video_anuncio as anuncio
from app.services.marketing import shopee_video_conta as conta
from app.services.marketing import shopee_video_publicador as pub
from app.services.marketing.shopee_video import ShopeeVideoError, ShopeeVideoRedeError

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


async def test_nunca_kit_e_dois_skus_no_mesmo_anuncio_valem(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, _ = await _criativo(db, marca, sku="dg089.ci, dg088.ci")
    r = await anuncio.anuncio_para(db, c, rede)
    assert isinstance(r, anuncio.AnuncioEscolhido), r
    assert r.item_id == 22499563693  # o avulso — nunca o kit 58266122162
    assert r.skus == ["dg089.ci", "dg088.ci"]


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
    acao = await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera)
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
    acao = await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera, orcamento_s=0)
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
    acao = await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera)
    assert acao == pub.PUBLICADO
    assert shopee.chamadas.count("init") == 1


async def test_post_sem_resposta_vai_pra_revisar_e_reconciliador_confirma(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [ShopeeVideoRedeError("sem_resposta", "ReadTimeout")]
    acao = await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera)
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


async def test_rascunho_que_nao_saiu_fica_em_revisar_com_frase_clara(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_postar = [ShopeeVideoRedeError("sem_resposta", "ReadTimeout")]
    await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera)
    shopee.detalhe_resposta = {"status": 200}
    assert await pub.reconciliar(db, p.id) == pub.REVISAR
    linha = await _relida(db, p.id)
    assert "RASCUNHO" in linha.result


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
    acao = await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera)
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
    assert await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera) == pub.PUBLICADO
    assert (await _relida(db, p.id)).post_external_id == "JA-SAIU"


async def test_anuncio_sem_estoque_ao_vivo_nao_posta(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.conferencia = anuncio.Conferencia(
        ok=False, motivo="anúncio 58262693089 está sem estoque na Shopee"
    )
    acao = await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera)
    assert acao == pub.FALHOU
    assert "postar" not in shopee.chamadas and "editar" not in shopee.chamadas
    assert "sem estoque" in (await _relida(db, p.id)).result


async def test_token_recusado_renova_uma_vez_e_segue(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.erro_editar = [ShopeeVideoError("error_auth", "Invalid access_token")]
    assert await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera) == pub.PUBLICADO
    assert TOKEN_FALSO in shopee.recusados  # pediu a renovação COM o token recusado
    assert shopee.chamadas.count("editar") == 2


async def test_transcodificacao_recusada_falha_sem_ambiguo(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.status_upload = ["FAILED"]
    assert await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert "recusou o vídeo" in linha.result and linha.container_id is None


async def test_capa_ainda_nao_pronta_espera(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.capas_respostas = [ShopeeVideoError("error_param", "upload video less than 1 min")]
    acao = await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera, orcamento_s=0)
    assert acao == pub.AGUARDANDO
    assert (await _relida(db, p.id)).opcoes["shopee"]["progresso"]["etapa"] == "capa"


async def test_worker_morto_antes_do_post_volta_pra_fila(db, make_user, shopee):
    p, caminho, _rede = await _postagem_publicando(db, make_user)
    shopee.status_upload = ["PROCESSING"]
    await pub.publicar_postagem(db, p.id, caminho, dormir=_sem_espera, orcamento_s=0)
    # Simula o lease seguinte morrendo no meio: linha `publicando`, sem post.
    (lease,) = await svc.proximas_para_publicar(db)
    assert await pub.reconciliar(db, lease.id) == pub.AGUARDANDO
    assert (await _relida(db, p.id)).status == "pendente"


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
