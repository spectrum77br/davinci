"""Robô de postagem dos criativos — serviço + API (Eduardo, 15/09/2026).

Cobre `app/services/marketing/postagens.py` (as regras) e
`app/routers/marketing_postagens.py` (a borda) da feature que publica o vídeo
aprovado do Criativo nas contas de Redes Sociais da marca, com agendamento.

**Nada de rede aqui.** A Meta (Graph API) mora em `services/marketing/
meta_client.py` e não é tocada por este arquivo: o serviço não importa httpx
de propósito, então toda a decisão — quem pode postar, quando entra na fila,
o que fazer com o resultado — é testável contra o banco e mais nada. É o
mesmo desenho do test_marketing_command_consumer.py, onde "o cliente HTTP da
Shopee Ads é fakado ponta a ponta".

O que o arquivo insiste em provar, porque **publicar não tem desfazer**:
o índice único PARCIAL `uq_marketing_postagem_em_voo` barra de verdade no
Postgres (não só a pré-checagem em Python), o lease de `proximas_para_
publicar` é durável (o mesmo vídeo não sai duas vezes) e `retentar` se recusa
a rodar quando já existe id externo.

Tokens são FALSOS e nenhum endpoint pode devolvê-los — isso é asserção, não
confiança: `_sem_token` varre o corpo de todas as respostas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingPostagem,
    RedeSocial,
    RedeSocialToken,
    User,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services.marketing import postagens as svc

pytestmark = pytest.mark.asyncio

API = "/api/marketing/postagens"

# Token de mentira. Se este texto aparecer em QUALQUER corpo de resposta, a
# credencial de publicação da marca vazou — é o que `_sem_token` procura.
SEGREDO_FAKE = "EAAG-token-falso-nunca-pode-vazar"


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    """O `app/main.py` só inclui os routers de Marketing quando
    `enable_marketing` está ligado, e o ambiente de teste não liga a flag —
    sem isto todo GET/POST daqui viraria 404 e o arquivo passaria vazio."""
    from app.main import app
    from app.routers import marketing_postagens as router_mod

    if not any(getattr(r, "path", "").startswith(API) for r in app.routes):
        app.include_router(router_mod.router)


@pytest.fixture(autouse=True)
def _uploads(tmp_path, monkeypatch):
    """`settings.uploads_dir` vira o tmp_path do teste.

    A guarda `arquivo_sumiu` lê o vídeo do DISCO do servidor; sem apontar o
    diretório pra cá, todo criativo seria recusado (ou, pior, dependeria de
    /data/uploads existir na máquina de quem roda)."""
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))


@pytest.fixture(autouse=True)
async def _limpa_criativos(db: AsyncSession):
    """`marketing_creatives` não está no _CLEANUP_TABLES do conftest (as
    postagens estão) — limpo aqui pra um teste não herdar criativo do outro."""
    yield
    for c in (await db.execute(select(MarketingCreative))).scalars().all():
        await db.delete(c)
    await db.commit()


# ─────────────────────────────────────────────────────────────── sementes


async def _marca(db: AsyncSession, nome: str = "Poofy") -> Marca:
    m = Marca(nome=nome, slug=nome.lower().replace(" ", "-"))
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _criativo(
    db: AsyncSession,
    *,
    marca: Marca | None = None,
    aprovado: bool | None = True,
    equipe: str | None = None,
    sha256: str | None = None,
    no_disco: bool = True,
    file_rel: str | None = None,
) -> tuple[MarketingCreative, MarketingCreativeFile]:
    """Criativo + 1 arquivo, com o vídeo REALMENTE gravado em tmp_path."""
    c = MarketingCreative(
        modelo="Mala de bordo 20kg",
        marca=marca.slug if marca else None,
        marca_id=marca.id if marca else None,
        equipe=equipe,
        aprovado=aprovado,
        roteiro="cena 1: abre a mala",
    )
    db.add(c)
    await db.flush()
    rel = f"creatives/{c.id}/video.mp4" if file_rel is None else file_rel
    if no_disco and rel:
        caminho = Path(get_settings().uploads_dir) / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(b"\x00\x00\x00\x18ftypmp42-fake")
    f = MarketingCreativeFile(
        creative_id=c.id,
        file_name="video.mp4",
        file_mime="video/mp4",
        file_size=14,
        file_rel=rel,
        sha256=sha256,
    )
    db.add(f)
    await db.commit()
    await db.refresh(c)
    await db.refresh(f)
    return c, f


async def _conta(
    db: AsyncSession,
    marca: Marca,
    *,
    plataforma: str = "instagram",
    conta: str = "poofy.oficial",
    ativo: bool = True,
    token: bool = True,
    situacao: str = "ok",
    max_dia: int | None = None,
    intervalo_min: int | None = None,
) -> RedeSocial:
    r = RedeSocial(
        marca_id=marca.id,
        plataforma=plataforma,
        conta=conta,
        ativo=ativo,
        postagem_auto=True,
        postagem_max_dia=max_dia,
        postagem_intervalo_min=intervalo_min,
    )
    db.add(r)
    await db.flush()
    if token:
        db.add(
            RedeSocialToken(
                rede_social_id=r.id,
                external_user_id="17841400000000000",
                external_username=conta,
                status=situacao,
                token_enc=encrypt_json(
                    {"access_token": SEGREDO_FAKE, "expires_at": None, "scopes": []}
                ),
            )
        )
    await db.commit()
    await db.refresh(r)
    return r


async def _token(db: AsyncSession, rede: RedeSocial) -> RedeSocialToken | None:
    return (await svc.tokens_por_rede(db, [rede.id])).get(rede.id)


async def _postagem(
    db: AsyncSession,
    creative: MarketingCreative,
    file: MarketingCreativeFile,
    rede: RedeSocial,
    *,
    status: str = "publicado",
    agendado_para: datetime | None = None,
    publicado_em: datetime | None = None,
    **kw,
) -> MarketingPostagem:
    """Linha gravada DIRETO, sem passar pelo serviço — é assim que se monta o
    passado da conta (posts de ontem) e as corridas que só o banco barra."""
    p = MarketingPostagem(
        creative_id=creative.id,
        file_id=file.id,
        rede_social_id=rede.id,
        plataforma=rede.plataforma,
        conta=rede.conta,
        status=status,
        agendado_para=agendado_para,
        publicado_em=publicado_em,
        **kw,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _user_edit(make_user, auth_as, **kw) -> User:
    u = await make_user(
        permissions={"marketing_criativos": {"view": True, "edit": True}}, **kw
    )
    auth_as(u)
    return u


def _body(creative, file, redes, **kw) -> dict:
    return {
        "creative_id": str(creative.id),
        "file_id": str(file.id),
        "rede_social_ids": [str(r.id) for r in redes],
        **kw,
    }


def _code(r) -> str:
    """O router devolve sempre detail={"code": ...} — é o contrato do front."""
    return r.json()["detail"]["code"]


def _sem_token(r) -> None:
    """Nenhuma resposta pode conter o token (nem o campo, nem o valor).

    `has_token` é permitido de propósito (a tela precisa saber se existe
    credencial); o VALOR nunca sai — por isso a busca é pelo texto do token
    fake no corpo inteiro, e não só pelas chaves.
    """
    assert SEGREDO_FAKE not in r.text
    assert "token_enc" not in r.text
    corpo = r.json()
    itens = corpo if isinstance(corpo, list) else [corpo]
    for item in itens:
        assert "token" not in item
        assert "token_enc" not in item


# ═══════════════════════════════════════════════════════ (1) fuso horário


async def test_para_utc_offset_e_naive_caem_no_mesmo_instante():
    """O `datetime-local` do modal manda "2026-10-01T19:30" SEM offset: o
    operador digitou hora de Brasília. Tratar isso como UTC adiantaria todo
    post agendado em 3 horas — os dois caminhos têm que coincidir."""
    com_offset = datetime.fromisoformat("2026-10-01T19:30:00-03:00")
    naive = datetime.fromisoformat("2026-10-01T19:30:00")

    esperado = datetime(2026, 10, 1, 22, 30, tzinfo=UTC)
    assert svc.para_utc(com_offset) == esperado
    assert svc.para_utc(naive) == esperado
    assert svc.para_utc(None) is None


async def test_post_com_agendado_para_naive_e_lido_como_brt(
    client, db, make_user, auth_as
):
    """A borda repete a regra do serviço: 19:30 naive vira 22:30 UTC no banco."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    r = await client.post(
        API, json=_body(c, f, [rede], agendado_para="2026-10-01T19:30:00")
    )
    assert r.status_code == 201, r.text
    linha = (await db.execute(select(MarketingPostagem))).scalar_one()
    assert linha.agendado_para == datetime(2026, 10, 1, 22, 30, tzinfo=UTC)
    assert linha.status == "agendado"


# ════════════════════════════════════════════ (2) guardas do agendamento


async def test_criativo_nao_aprovado_barra_com_422(client, db, make_user, auth_as):
    """`aprovado` é tri-state e só `True` libera: publicar vídeo que o admin
    ainda não viu (NULL) é o erro caro desta tela."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca, aprovado=None)
    rede = await _conta(db, marca)

    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 422
    assert _code(r) == "criativo_nao_aprovado"


async def test_arquivo_sem_caminho_e_sem_arquivo(db):
    """Linha de arquivo sem `file_rel` (upload que morreu no meio) não é
    postagem nenhuma — o serviço recusa antes de olhar o disco."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca, file_rel="", no_disco=False)
    rede = await _conta(db, marca)

    motivo = svc.pode_publicar_local(c, f, rede, await _token(db, rede))
    assert motivo == "sem_arquivo"
    assert svc.pode_publicar_local(c, None, rede, await _token(db, rede)) == "sem_arquivo"


async def test_arquivo_sumiu_do_disco_vira_404(client, db, make_user, auth_as):
    """O vídeo é lido do disco do servidor na hora de publicar; se sumiu
    (limpeza, volume trocado), melhor descobrir no clique que no upload."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    (Path(get_settings().uploads_dir) / f.file_rel).unlink()
    rede = await _conta(db, marca)

    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 404
    assert _code(r) == "arquivo_sumiu"


async def test_conta_inativa_barra(client, db, make_user, auth_as):
    """Conta desligada em Cadastros › Redes Sociais não recebe post."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, ativo=False)

    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 422
    assert _code(r) == "conta_inativa"


async def test_conta_sem_token_barra(client, db, make_user, auth_as):
    """Sem credencial cifrada não há o que tentar — e a recusa é do modal,
    não um erro da Meta depois de subir 80 MB."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, token=False)

    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 422
    assert _code(r) == "conta_sem_token"


async def test_token_revogado_barra_mas_expirado_passa(db):
    """`revogado` é definitivo (tiraram o app da conta); `expirado` NÃO barra
    — o cron de refresh renova antes do tick, e recusar aqui seria perder o
    agendamento por um token que o robô mesmo conserta."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)

    revogada = await _conta(db, marca, conta="poofy.revogada", situacao="revogado")
    assert svc.pode_publicar_local(c, f, revogada, await _token(db, revogada)) == (
        "conta_sem_token"
    )

    expirada = await _conta(db, marca, conta="poofy.expirada", situacao="expirado")
    assert svc.pode_publicar_local(c, f, expirada, await _token(db, expirada)) is None


async def test_plataforma_nao_suportada_tiktok(client, db, make_user, auth_as):
    """TikTok está fora de escopo (as Content Sharing Guidelines exigem
    consentimento humano por upload): a conta aparece, mas com o motivo."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, plataforma="tiktok", conta="poofy.tk")

    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 422
    assert _code(r) == "plataforma_nao_suportada"


async def test_postagem_em_voo_devolve_409(client, db, make_user, auth_as):
    """Já existe uma postagem em voo pra (arquivo, conta): a segunda é
    conflito de ESTADO, não erro de formulário — 409."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    await _postagem(db, c, f, rede, status="agendado", agendado_para=None)

    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 409
    assert _code(r) == "postagem_em_voo"


async def test_video_ja_usado_em_outra_marca_devolve_409(
    client, db, make_user, auth_as
):
    """Instagram e TikTok punem o MESMO vídeo em contas diferentes em
    silêncio (a conta perde recomendação, nenhum erro de API). Trava só com
    sha256 nos dois lados E marca nos dois criativos."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    poofy = await _marca(db, "Poofy")
    locagil = await _marca(db, "Locagil")
    sha = "a" * 64

    c_outra, f_outra = await _criativo(db, marca=locagil, sha256=sha)
    rede_outra = await _conta(db, locagil, conta="locagil.oficial")
    await _postagem(db, c_outra, f_outra, rede_outra, publicado_em=datetime.now(UTC))

    c, f = await _criativo(db, marca=poofy, sha256=sha)
    rede = await _conta(db, poofy)
    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 409
    assert _code(r) == "video_ja_usado_em_outra_marca"


async def test_mesmo_video_sem_sha256_nao_trava(db):
    """Sem hash não dá pra AFIRMAR que é o mesmo vídeo — e travar por
    suspeita seria travar o trabalho de todo mundo."""
    poofy = await _marca(db, "Poofy")
    locagil = await _marca(db, "Locagil")

    c_outra, f_outra = await _criativo(db, marca=locagil, sha256=None)
    rede_outra = await _conta(db, locagil, conta="locagil.oficial")
    await _postagem(db, c_outra, f_outra, rede_outra, publicado_em=datetime.now(UTC))

    c, f = await _criativo(db, marca=poofy, sha256=None)
    rede = await _conta(db, poofy)
    assert (
        await svc.pode_publicar(c, f, rede, await _token(db, rede), session=db) is None
    )


async def test_mesmo_video_na_mesma_marca_nao_trava(db):
    """Repostar o mesmo vídeo em OUTRA conta DA MESMA MARCA é rotina (o Reel
    do Instagram e o do Facebook da marca) — a trava é entre marcas."""
    poofy = await _marca(db, "Poofy")
    sha = "b" * 64

    c1, f1 = await _criativo(db, marca=poofy, sha256=sha)
    ig = await _conta(db, poofy, conta="poofy.ig")
    await _postagem(db, c1, f1, ig, publicado_em=datetime.now(UTC) - timedelta(days=3))

    c2, f2 = await _criativo(db, marca=poofy, sha256=sha)
    fb = await _conta(db, poofy, plataforma="facebook", conta="poofy.fb")
    assert await svc.pode_publicar(c2, f2, fb, await _token(db, fb), session=db) is None


async def test_limite_diario_usa_o_padrao_do_servidor(db):
    """Conta sem teto próprio (NULL) herda `marketing_postagem_max_dia` = 2:
    o terceiro post do dia é recusado."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, max_dia=None)
    quando = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    for horas in (3, 6):
        c_ant, f_ant = await _criativo(db, marca=marca)
        await _postagem(
            db, c_ant, f_ant, rede, publicado_em=quando - timedelta(hours=horas)
        )

    motivo = await svc.pode_publicar(
        c, f, rede, await _token(db, rede), session=db, quando=quando
    )
    assert motivo == "limite_diario"


async def test_limite_diario_respeita_o_teto_proprio_da_conta(db):
    """A conta pode ter teto PRÓPRIO (`redes_sociais.postagem_max_dia`) — o
    Eduardo pediu limites configuráveis pra rodar sem babá. Com teto 1, um
    post nas últimas 24h já fecha a conta, mesmo o padrão do servidor sendo 2.

    (VERMELHO HOJE: `postagens.pode_publicar` lê só o settings — a coluna por
    conta que a 0279 criou e o models/marca.py documenta nunca é consultada.)"""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, max_dia=1)
    quando = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    c_ant, f_ant = await _criativo(db, marca=marca)
    await _postagem(db, c_ant, f_ant, rede, publicado_em=quando - timedelta(hours=6))

    motivo = await svc.pode_publicar(
        c, f, rede, await _token(db, rede), session=db, quando=quando
    )
    assert motivo == "limite_diario", (
        "teto da conta ignorado — postagens.pode_publicar usa "
        "settings.marketing_postagem_max_dia direto, sem olhar rede.postagem_max_dia"
    )


async def test_intervalo_curto_usa_o_padrao_do_servidor(db):
    """90 min de espaçamento (padrão): um post 30 min antes cola os dois.

    A janela vale nos DOIS sentidos — agendar ANTES de um post já marcado
    juntaria as publicações do mesmo jeito."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, intervalo_min=None)
    quando = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    c_ant, f_ant = await _criativo(db, marca=marca)
    await _postagem(
        db, c_ant, f_ant, rede, publicado_em=quando - timedelta(minutes=30)
    )

    motivo = await svc.pode_publicar(
        c, f, rede, await _token(db, rede), session=db, quando=quando
    )
    assert motivo == "intervalo_curto"

    # E o post FUTURO também segura: 30 min depois do instante pretendido.
    c2, f2 = await _criativo(db, marca=marca)
    rede2 = await _conta(db, marca, conta="poofy.segunda")
    c3, f3 = await _criativo(db, marca=marca)
    await _postagem(
        db, c3, f3, rede2, status="agendado", agendado_para=quando + timedelta(minutes=30)
    )
    assert (
        await svc.pode_publicar(
            c2, f2, rede2, await _token(db, rede2), session=db, quando=quando
        )
        == "intervalo_curto"
    )


async def test_intervalo_proprio_da_conta_manda_no_espacamento(db):
    """Conta com `postagem_intervalo_min` própria (10 min) aceita o que o
    padrão do servidor (90 min) recusaria — é o mesmo interruptor por conta
    do teto diário, e NULL é que significa "usa o do servidor".

    (VERMELHO HOJE: mesma causa do teto diário — a coluna da conta não é lida.)"""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, intervalo_min=10)
    quando = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    c_ant, f_ant = await _criativo(db, marca=marca)
    await _postagem(
        db, c_ant, f_ant, rede, publicado_em=quando - timedelta(minutes=30)
    )

    motivo = await svc.pode_publicar(
        c, f, rede, await _token(db, rede), session=db, quando=quando
    )
    assert motivo is None, (
        "espaçamento da conta ignorado — postagens.pode_publicar usa "
        "settings.marketing_postagem_intervalo_min direto, sem olhar "
        "rede.postagem_intervalo_min"
    )


# ══════════════════════════════════ (3) o índice parcial barra NO BANCO


async def test_indice_unico_parcial_barra_segunda_em_voo(db):
    """A pré-checagem em Python é só pra mensagem boa; quem garante que o
    vídeo não sai duas vezes é o índice. Duas abas clicando juntas passam
    pela checagem e param aqui.

    E o índice é PARCIAL de propósito: linha fora de voo (`cancelado`) não
    ocupa a vaga, senão cancelar um agendamento travaria a conta pra sempre."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    await _postagem(db, c, f, rede, status="agendado", agendado_para=None)
    # Ids soltos em variável: o rollback do IntegrityError expira os objetos
    # da sessão, e ler `c.id` depois dispararia I/O fora do greenlet.
    alvo = {
        "creative_id": c.id,
        "file_id": f.id,
        "rede_social_id": rede.id,
        "plataforma": rede.plataforma,
        "conta": rede.conta,
    }

    db.add(MarketingPostagem(**alvo, status="pendente"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()

    # Fora de voo o par (arquivo, conta) pode repetir à vontade.
    db.add(MarketingPostagem(**alvo, status="cancelado"))
    await db.commit()


async def test_router_devolve_409_quando_o_indice_pega(
    client, db, make_user, auth_as, monkeypatch
):
    """Corrida de verdade: com a pré-checagem cega (duas requisições que se
    cruzaram), o IntegrityError do índice tem que virar 409 `postagem_em_voo`
    — nunca um 500 com stacktrace na cara do operador."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    await _postagem(db, c, f, rede, status="agendado", agendado_para=None)

    async def _sempre_pode(*_a, **_k):
        return None

    monkeypatch.setattr(svc, "pode_publicar", _sempre_pode)
    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 409
    assert _code(r) == "postagem_em_voo"


# ════════════════════════════════════════════════ (4) promover_agendadas


async def test_promover_vencida_vira_pendente_com_origem_agenda(db):
    """Hora chegou → entra na fila do publicador, e a origem passa a ser
    `agenda` (foi o cron, não um clique) — é o que a tela mostra depois."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    agora = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    p = await _postagem(
        db, c, f, rede, status="agendado", agendado_para=agora - timedelta(minutes=2)
    )

    resumo = await svc.promover_agendadas(db, agora=agora)
    assert resumo == {"checados": 1, "promovidas": 1, "revisar": 0}
    await db.refresh(p)
    assert p.status == "pendente"
    assert p.origem == "agenda"


async def test_promover_nao_toca_na_futura(db):
    """Agendamento de amanhã continua `agendado` — o cron roda a cada minuto
    e não pode antecipar nada."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    agora = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    p = await _postagem(
        db, c, f, rede, status="agendado", agendado_para=agora + timedelta(hours=5)
    )

    resumo = await svc.promover_agendadas(db, agora=agora)
    assert resumo["checados"] == 0
    await db.refresh(p)
    assert p.status == "agendado"


async def test_promover_atraso_grande_vira_revisar_e_nao_publica(db):
    """Worker parado a noite inteira: despejar de madrugada os posts das 19h
    é justamente o que a agenda existe pra evitar. Vira `revisar` (alguém
    decide) e NÃO entra na fila do publicador."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    agora = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    atraso = get_settings().marketing_postagem_atraso_max_min + 30
    p = await _postagem(
        db, c, f, rede, status="agendado", agendado_para=agora - timedelta(minutes=atraso)
    )

    resumo = await svc.promover_agendadas(db, agora=agora)
    assert resumo == {"checados": 1, "promovidas": 0, "revisar": 1}
    await db.refresh(p)
    assert p.status == "revisar"
    assert p.completed_at is not None
    assert "atraso" in (p.result or "")
    assert await svc.proximas_para_publicar(db) == []


# ═════════════════════════════════════════════ (5) proximas_para_publicar


async def test_proximas_uma_por_conta_mais_antiga_primeiro(db):
    """Uma por conta POR TICK: o tick é de 1 minuto e dois Reels seguidos na
    mesma conta em segundos é o padrão que a Meta lê como spam.

    O claim é durável (status `publicando` + `claimed_at`, comitados): se a
    linha continuasse `pendente` durante o upload — que dura mais que o tick
    — o tick seguinte a pegaria de novo e o vídeo sairia DUAS vezes."""
    marca = await _marca(db)
    rede_a = await _conta(db, marca, conta="poofy.a")
    rede_b = await _conta(db, marca, conta="poofy.b")
    base = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)

    c1, f1 = await _criativo(db, marca=marca)
    c2, f2 = await _criativo(db, marca=marca)
    c3, f3 = await _criativo(db, marca=marca)
    velha_a = await _postagem(
        db, c1, f1, rede_a, status="pendente", agendado_para=base - timedelta(minutes=10)
    )
    nova_a = await _postagem(
        db, c2, f2, rede_a, status="pendente", agendado_para=base - timedelta(minutes=1)
    )
    unica_b = await _postagem(
        db, c3, f3, rede_b, status="pendente", agendado_para=base - timedelta(minutes=5)
    )

    primeira = await svc.proximas_para_publicar(db)
    assert [p.id for p in primeira] == [velha_a.id, unica_b.id]
    assert all(p.status == "publicando" and p.claimed_at is not None for p in primeira)

    segunda = await svc.proximas_para_publicar(db)
    assert [p.id for p in segunda] == [nova_a.id]


# ══════════════════════════════════════════════ (6) ciclo do resultado


async def test_container_id_gravado_antes_do_publicado(db):
    """`container_id` é carimbado ANTES de publicar: é ele que deixa a
    reconciliação perguntar "saiu ou não?" em vez de retentar cego. O passo
    seguinte não pode apagá-lo (campo None não zera o que já estava)."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    p = await _postagem(db, c, f, rede, status="publicando")

    await svc.registrar_resultado(db, p, status="containering", container_id="ct-1")
    await db.refresh(p)
    assert (p.status, p.container_id, p.publicado_em) == ("containering", "ct-1", None)

    await svc.registrar_resultado(
        db,
        p,
        status="publicado",
        post_external_id="1789",
        post_url="https://instagram.com/reel/1789",
        result="ok",
    )
    await db.refresh(p)
    assert p.status == "publicado"
    assert p.publicado_em is not None and p.completed_at is not None
    assert p.post_url == "https://instagram.com/reel/1789"
    assert p.container_id == "ct-1"


async def test_cancelar_so_antes_de_publicar(db):
    """`agendado`/`pendente` cancelam; o que já foi pra Meta, não — dizer
    "cancelado" na tela com o Reel no ar seria mentira."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    p = await _postagem(db, c, f, rede, status="agendado", agendado_para=None)

    await svc.cancelar(db, p)
    await db.refresh(p)
    assert p.status == "cancelado"
    assert p.completed_at is not None

    c2, f2 = await _criativo(db, marca=marca)
    publicada = await _postagem(
        db, c2, f2, rede, status="publicado", publicado_em=datetime.now(UTC)
    )
    with pytest.raises(svc.RoboError) as e:
        await svc.cancelar(db, publicada)
    assert e.value.code == "postagem_nao_cancelavel"


async def test_retentar_so_de_falhou(db):
    """Retentar algo em `publicando` podia render dois posts iguais na conta
    da marca, sem desfazer — quem resolve esse estado é a reconciliação."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    p = await _postagem(db, c, f, rede, status="falhou", attempts=3, result="erro 190")

    await svc.retentar(db, p)
    await db.refresh(p)
    assert p.status == "pendente"
    # Decisão nova e humana: as tentativas voltam do zero e a origem é manual.
    assert (p.attempts, p.origem, p.result, p.container_id) == (0, "manual", None, None)

    c2, f2 = await _criativo(db, marca=marca)
    rede2 = await _conta(db, marca, conta="poofy.b")
    em_voo = await _postagem(db, c2, f2, rede2, status="publicando")
    with pytest.raises(svc.RoboError) as e:
        await svc.retentar(db, em_voo)
    assert e.value.code == "postagem_nao_falhou"


async def test_retentar_recusa_quando_ja_tem_id_externo(db):
    """Cinto de segurança: se ficou `post_external_id`/`post_url`, o post
    SAIU (falhou depois, no carimbo). Retentar duplicaria na conta."""
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    com_id = await _postagem(db, c, f, rede, status="falhou", post_external_id="1789")
    with pytest.raises(svc.RoboError) as e:
        await svc.retentar(db, com_id)
    assert e.value.code == "postagem_ja_publicada"

    c2, f2 = await _criativo(db, marca=marca)
    rede2 = await _conta(db, marca, conta="poofy.b")
    com_url = await _postagem(
        db, c2, f2, rede2, status="falhou", post_url="https://instagram.com/reel/x"
    )
    with pytest.raises(svc.RoboError) as e:
        await svc.retentar(db, com_url)
    assert e.value.code == "postagem_ja_publicada"


# ════════════════════════════════════════════════════════════════ (7) API


async def test_get_contas_traz_pode_postar_e_motivo(client, db, make_user, auth_as):
    """O modal precisa do motivo POR CONTA pra desabilitar o checkbox com
    explicação — inclusive a conta que ainda não tem token."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    outra = await _marca(db, "Locagil")
    ok = await _conta(db, marca, conta="poofy.ok")
    sem_token = await _conta(db, marca, conta="poofy.sem.token", token=False)
    inativa = await _conta(db, marca, conta="poofy.inativa", ativo=False)
    tiktok = await _conta(db, marca, plataforma="tiktok", conta="poofy.tk")
    await _conta(db, outra, conta="locagil.oficial")

    r = await client.get(f"{API}/contas", params={"marca_id": str(marca.id)})
    assert r.status_code == 200, r.text
    _sem_token(r)
    por_id = {item["rede_social_id"]: item for item in r.json()}
    # Só as contas DA MARCA pedida.
    assert set(por_id) == {str(ok.id), str(sem_token.id), str(inativa.id), str(tiktok.id)}
    assert por_id[str(ok.id)]["pode_postar"] is True
    assert por_id[str(ok.id)]["motivo"] is None
    assert por_id[str(ok.id)]["has_token"] is True
    assert por_id[str(sem_token.id)] == {
        **por_id[str(sem_token.id)],
        "pode_postar": False,
        "motivo": "conta_sem_token",
        "has_token": False,
    }
    assert por_id[str(inativa.id)]["motivo"] == "conta_inativa"
    assert por_id[str(tiktok.id)]["motivo"] == "plataforma_nao_suportada"


async def test_post_cria_uma_linha_por_conta(client, db, make_user, auth_as):
    """Duas contas marcadas = duas linhas, cada uma com o SNAPSHOT de
    plataforma/conta (apagar a conta depois não pode apagar o histórico)."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    ig = await _conta(db, marca, conta="poofy.ig")
    fb = await _conta(db, marca, plataforma="facebook", conta="poofy.fb")

    r = await client.post(API, json=_body(c, f, [ig, fb], legenda="mala nova ✨"))
    assert r.status_code == 201, r.text
    _sem_token(r)
    corpo = r.json()
    assert len(corpo) == 2
    assert {item["plataforma"] for item in corpo} == {"instagram", "facebook"}
    assert {item["conta"] for item in corpo} == {"poofy.ig", "poofy.fb"}
    # Sem hora = "publicar agora": entra direto na fila do próximo tick.
    assert {item["status"] for item in corpo} == {"pendente"}
    assert {item["origem"] for item in corpo} == {"manual"}
    assert corpo[0]["legenda"] == "mala nova ✨"
    assert corpo[0]["file_name"] == "video.mp4"
    assert corpo[0]["marca_nome"] == "Poofy"


async def test_get_lista_com_filtros(client, db, make_user, auth_as):
    """A coluna "Publicação" do grid pede por criativo; a tela de marca, por
    marca; o painel de erro, por status."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    poofy = await _marca(db, "Poofy")
    locagil = await _marca(db, "Locagil")
    c1, f1 = await _criativo(db, marca=poofy)
    c2, f2 = await _criativo(db, marca=locagil)
    rede1 = await _conta(db, poofy, conta="poofy.ig")
    rede2 = await _conta(db, locagil, conta="locagil.ig")
    p1 = await _postagem(db, c1, f1, rede1, status="agendado", agendado_para=None)
    p2 = await _postagem(db, c2, f2, rede2, status="falhou")

    r = await client.get(API)
    assert r.status_code == 200
    _sem_token(r)
    assert {item["id"] for item in r.json()} == {str(p1.id), str(p2.id)}

    r = await client.get(API, params={"creative_id": str(c1.id)})
    assert [item["id"] for item in r.json()] == [str(p1.id)]

    r = await client.get(API, params={"status": "falhou"})
    assert [item["id"] for item in r.json()] == [str(p2.id)]

    r = await client.get(API, params={"marca_id": str(locagil.id)})
    assert [item["id"] for item in r.json()] == [str(p2.id)]
    assert r.json()[0]["marca_nome"] == "Locagil"


async def test_patch_muda_legenda_e_hora_so_enquanto_agendado(
    client, db, make_user, auth_as
):
    """Depois de `agendado` a linha já é do publicador: editar um `pendente`
    seria corrida com o worker."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    p = await _postagem(
        db, c, f, rede, status="agendado", agendado_para=datetime.now(UTC)
    )

    r = await client.patch(
        f"{API}/{p.id}",
        json={"legenda": "outra legenda", "agendado_para": "2026-10-02T08:00:00"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["legenda"] == "outra legenda"
    await db.refresh(p)
    # Naive = BRT também no PATCH (08:00 BRT = 11:00 UTC).
    assert p.agendado_para == datetime(2026, 10, 2, 11, 0, tzinfo=UTC)

    p.status = "pendente"
    await db.commit()
    r = await client.patch(f"{API}/{p.id}", json={"legenda": "tarde demais"})
    assert r.status_code == 409
    assert _code(r) == "postagem_nao_agendada"


async def test_delete_vira_cancelado_e_nao_apaga(client, db, make_user, auth_as):
    """DELETE aqui é CANCELAR: a linha fica (é o histórico e é ela que libera
    a conta ao sair do estado em voo)."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    p = await _postagem(db, c, f, rede, status="agendado", agendado_para=None)

    r = await client.delete(f"{API}/{p.id}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelado"
    await db.refresh(p)
    assert p.status == "cancelado"
    assert (await db.execute(select(MarketingPostagem))).scalars().all() != []

    # E a vaga da conta foi liberada: dá pra agendar de novo o mesmo arquivo.
    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 201, r.text


async def test_retentar_endpoint_devolve_pendente(client, db, make_user, auth_as):
    """O botão "Tentar de novo" da tela — e só de `falhou`."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    p = await _postagem(db, c, f, rede, status="falhou", attempts=3, result="erro 190")

    r = await client.post(f"{API}/{p.id}/retentar")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pendente"
    assert r.json()["attempts"] == 0

    p.status = "publicando"
    await db.commit()
    r = await client.post(f"{API}/{p.id}/retentar")
    assert r.status_code == 409
    assert _code(r) == "postagem_nao_falhou"


# ═════════════════════════════════════════════════════════ (8) permissões


async def test_sem_permissao_403(client, db, make_user, auth_as):
    """Usuário sem o recurso `marketing_criativos` não lê nem escreve."""
    u = await make_user(permissions={})
    auth_as(u)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    assert (await client.get(API)).status_code == 403
    assert (
        await client.get(f"{API}/contas", params={"marca_id": str(marca.id)})
    ).status_code == 403
    assert (await client.post(API, json=_body(c, f, [rede]))).status_code == 403


async def test_view_le_mas_nao_escreve(client, db, make_user, auth_as):
    """`view` é quem acompanha a agenda; mexer nela exige `edit` — a mesma
    divisão da aba Criativos, porque a tela é a mesma."""
    u = await make_user(permissions={"marketing_criativos": {"view": True}})
    auth_as(u)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)
    p = await _postagem(db, c, f, rede, status="agendado", agendado_para=None)

    assert (await client.get(API)).status_code == 200
    assert (await client.get(f"{API}/{p.id}")).status_code == 200
    assert (
        await client.get(f"{API}/contas", params={"marca_id": str(marca.id)})
    ).status_code == 200
    assert (await client.post(API, json=_body(c, f, [rede]))).status_code == 403
    assert (await client.patch(f"{API}/{p.id}", json={"legenda": "x"})).status_code == 403
    assert (await client.delete(f"{API}/{p.id}")).status_code == 403
    assert (await client.post(f"{API}/{p.id}/retentar")).status_code == 403


async def test_escopo_de_equipe_do_criativo(client, db, make_user, auth_as):
    """A postagem não tem equipe própria: quem manda é a linha do criativo.
    Usuário restrito a uma equipe não vê a agenda da outra nem agenda nela."""
    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    u.marketing_teams = ["alpha"]
    await db.commit()
    auth_as(u)

    marca = await _marca(db)
    minha, f_minha = await _criativo(db, marca=marca, equipe="Alpha")
    alheia, f_alheia = await _criativo(db, marca=marca, equipe="Bravo")
    rede_a = await _conta(db, marca, conta="poofy.a")
    rede_b = await _conta(db, marca, conta="poofy.b")
    p_minha = await _postagem(db, minha, f_minha, rede_a, status="agendado")
    p_alheia = await _postagem(db, alheia, f_alheia, rede_b, status="agendado")

    r = await client.get(API)
    assert [item["id"] for item in r.json()] == [str(p_minha.id)]

    assert (await client.get(f"{API}/{p_alheia.id}")).status_code == 403
    r = await client.post(API, json=_body(alheia, f_alheia, [rede_b]))
    assert r.status_code == 403
    assert _code(r) == "fora_da_sua_equipe"


async def test_get_de_id_inexistente_404(client, db, make_user, auth_as):
    """404 com código estável — o front distingue "sumiu" de "sem acesso"."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    r = await client.get(f"{API}/{uuid.uuid4()}")
    assert r.status_code == 404
    assert _code(r) == "postagem_not_found"


# ═══════════════════════════════════════════ (9) token nunca sai por API


async def test_nenhum_endpoint_devolve_token(client, db, make_user, auth_as):
    """A credencial de publicação é de MÁQUINA (publica sozinha) e não tem
    rota de revelar — diferente da senha da marca, que é senha de gente.
    Aqui a garantia é varrida em todos os corpos que a tela consome."""
    await _user_edit(make_user, auth_as, role=UserRole.ADMIN)
    marca = await _marca(db)
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    criada = await client.post(API, json=_body(c, f, [rede]))
    assert criada.status_code == 201, criada.text
    _sem_token(criada)
    _sem_token(await client.get(API))
    _sem_token(await client.get(f"{API}/contas", params={"marca_id": str(marca.id)}))
    _sem_token(await client.get(f"{API}/{criada.json()[0]['id']}"))

    # E o token continua lá, cifrado, no lugar dele.
    tok = (await db.execute(select(RedeSocialToken))).scalar_one()
    assert tok.token_enc and SEGREDO_FAKE.encode() not in tok.token_enc


# ══════════════════════════════════════════════════════════════════════════
# Regressões da revisão adversarial (15/09/2026). Cada teste abaixo FALHAVA
# antes da correção — e quase todos custavam um Reel duplicado ou um post na
# conta errada, que é o estrago que não tem desfazer.
# ══════════════════════════════════════════════════════════════════════════


async def test_lease_nao_entrega_a_mesma_linha_pra_dois_workers(db):
    """DOIS ticks simultâneos não podem levar a MESMA postagem.

    O bug: o filtro de status só existia dentro do subquery `IN (...)`. Sob
    READ COMMITTED, quando o `FOR UPDATE` do segundo worker destrava a linha
    que o primeiro acabou de virar `publicando`, o Postgres re-avalia APENAS
    o WHERE de fora — que não tinha o status. Os dois levavam a linha e o
    vídeo saía duas vezes no perfil da marca.
    """
    from app.db import SessionLocal

    marca = await _marca(db, "Lease")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="lease.oficial")
    p = await _postagem(db, c, f, rede, status="pendente", publicado_em=None)

    # A INVARIANTE, lida na própria query: o filtro de status tem que aparecer
    # FORA da subquery. A corrida em si (EvalPlanQual re-avaliando o WHERE
    # externo no microssegundo entre o commit de um worker e o lock do outro)
    # não é reproduzível em teste — mas a condição que a torna possível é, e é
    # ela que estava faltando.
    from sqlalchemy.dialects import postgresql

    sql = str(
        svc._query_do_lease(5).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    # Recorta o que vem DEPOIS da subquery do `IN (...)`, casando parênteses.
    i = sql.index("IN (") + 3
    nivel, fim = 0, i
    for fim in range(i, len(sql)):
        nivel += 1 if sql[fim] == "(" else -1 if sql[fim] == ")" else 0
        if nivel == 0:
            break
    de_fora = sql[fim:]
    assert "status" in de_fora, (
        "o filtro de status sumiu do WHERE externo do lease — sob READ "
        "COMMITTED dois workers podem levar a mesma postagem"
    )
    assert "FOR UPDATE" in sql and "SKIP LOCKED" in sql

    # E o comportamento: dois workers em sessões distintas não repetem linha.
    async with SessionLocal() as s1, SessionLocal() as s2:
        primeiro = await svc.proximas_para_publicar(s1)
        segundo = await svc.proximas_para_publicar(s2)

    ids1 = {x.id for x in primeiro}
    ids2 = {x.id for x in segundo}
    assert p.id in ids1, "o primeiro worker tinha que levar a linha"
    assert not (ids1 & ids2), "a MESMA postagem foi entregue a dois workers"
    await db.refresh(p)
    assert p.status == "publicando"


async def test_retentar_recusa_quando_tem_container(db):
    """Container gravado = o upload chegou na Meta e o publish pode ter saído.

    Antes, `retentar` zerava o `container_id` e devolvia pra fila — se a
    chamada tinha saído, o resultado era o segundo Reel na conta da marca.
    """
    marca = await _marca(db, "Container")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="container.oficial")
    p = await _postagem(
        db, c, f, rede, status="falhou", container_id="17999999999999999"
    )
    with pytest.raises(svc.RoboError) as e:
        await svc.retentar(db, p)
    assert e.value.code == "postagem_precisa_conferir_na_meta"
    await db.refresh(p)
    assert p.status == "falhou"
    assert p.container_id == "17999999999999999", "o id não pode ser apagado"


async def test_revalidar_barra_criativo_reprovado_depois_de_agendado(db):
    """Agendar não é autorizar pra sempre: o que vale é a situação NA HORA."""
    marca = await _marca(db, "Revalida")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="revalida.oficial")
    p = await _postagem(db, c, f, rede, status="publicando", publicado_em=None)
    assert await svc.revalidar(db, p) is None, "com tudo em ordem, segue"

    c.aprovado = False  # alguém reprovou o vídeo depois de agendado
    await db.commit()
    assert await svc.revalidar(db, p) == "criativo_nao_aprovado"


async def test_revalidar_barra_conta_com_postagem_auto_desligada(db):
    """O interruptor por conta vale pro que o ROBÔ faz sozinho — inclusive
    quando a decisão de ligar/desligar veio DEPOIS do agendamento."""
    marca = await _marca(db, "Interruptor")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="interruptor.oficial")
    p = await _postagem(
        db, c, f, rede, status="publicando", publicado_em=None, origem="agenda"
    )
    rede.postagem_auto = False
    await db.commit()
    assert await svc.revalidar(db, p) == "conta_sem_postagem_auto"

    p.origem = "manual"  # clique de gente continua passando
    await db.commit()
    assert await svc.revalidar(db, p) is None


async def test_revalidar_pega_o_espacamento_na_hora_de_publicar(db):
    """A rajada de catch-up: worker parado a noite toda, 3 posts vencidos, e
    todos saindo colados quando ele volta. O espaçamento só era conferido no
    AGENDAMENTO — agora também no instante de subir."""
    marca = await _marca(db, "Rajada")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="rajada.oficial", intervalo_min=90)
    agora = datetime.now(UTC)
    # Um post saiu há 10 minutos nesta conta.
    c2, f2 = await _criativo(db, marca=marca, file_rel=f"creatives/{uuid.uuid4()}/v.mp4")
    await _postagem(
        db, c2, f2, rede, status="publicado", publicado_em=agora - timedelta(minutes=10)
    )
    p = await _postagem(db, c, f, rede, status="publicando", publicado_em=None)
    assert await svc.revalidar(db, p) == "intervalo_curto"


async def test_adiar_devolve_pra_fila_sem_gastar_tentativa(db):
    marca = await _marca(db, "Adiar")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="adiar.oficial")
    p = await _postagem(
        db, c, f, rede, status="publicando", publicado_em=None, attempts=1
    )
    await svc.adiar(db, p, motivo="intervalo_curto")
    await db.refresh(p)
    assert p.status == "pendente"
    assert p.claimed_at is None
    assert p.attempts == 1, "adiar não é tentativa gasta"


async def test_adiar_velho_demais_vira_revisar(db):
    """Fila que anda pra sempre sozinha é fila que ninguém vê travada."""
    marca = await _marca(db, "AdiarVelho")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="adiarvelho.oficial")
    p = await _postagem(
        db,
        c,
        f,
        rede,
        status="publicando",
        publicado_em=None,
        agendado_para=datetime.now(UTC) - timedelta(hours=72),
    )
    await svc.adiar(db, p, motivo="limite_diario")
    await db.refresh(p)
    assert p.status == "revisar"


async def test_teto_diario_nao_e_furado_agendando_pra_tras(db):
    """A janela de 24h olhava só PRA TRÁS: marcar de madrugada, antes dos
    posts da tarde, não enxergava nenhum deles e passava."""
    marca = await _marca(db, "Teto")
    rede = await _conta(db, marca, conta="teto.oficial", max_dia=2)
    agora = datetime.now(UTC)
    for h in (1, 2):  # dois posts daqui a pouco — a conta já está cheia
        c2, f2 = await _criativo(
            db, marca=marca, file_rel=f"creatives/{uuid.uuid4()}/v.mp4"
        )
        await _postagem(
            db, c2, f2, rede, status="agendado", agendado_para=agora + timedelta(hours=h)
        )
    c, f = await _criativo(db, marca=marca, file_rel=f"creatives/{uuid.uuid4()}/v.mp4")
    motivo = await svc.pode_publicar(
        c, f, rede, await _token(db, rede), session=db, quando=agora - timedelta(hours=1)
    )
    assert motivo == "limite_diario"


async def test_revisar_segura_a_vaga_do_video_na_conta(db):
    """`revisar` quer dizer NÃO SABEMOS se saiu. Agendar por cima seria
    exatamente o caminho do post repetido."""
    marca = await _marca(db, "Duvida")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="duvida.oficial")
    await _postagem(db, c, f, rede, status="revisar", container_id="1799")
    motivo = await svc.pode_publicar(
        c, f, rede, await _token(db, rede), session=db
    )
    assert motivo == "postagem_em_revisao"


async def test_agendar_recusa_conta_de_outra_marca(client, db, make_user, auth_as):
    """O criativo é de UMA marca e só sai nas contas DELA — um id trocado no
    payload publicaria o vídeo de uma marca no perfil da outra."""
    await _user_edit(make_user, auth_as)
    dona = await _marca(db, "Dona")
    outra = await _marca(db, "Outra")
    c, f = await _criativo(db, marca=dona)
    alheia = await _conta(db, outra, conta="outra.oficial")
    r = await client.post(API, json=_body(c, f, [alheia]))
    assert r.status_code == 409
    assert _code(r) == "conta_de_outra_marca"


async def test_agendar_recusa_criativo_sem_marca(client, db, make_user, auth_as):
    await _user_edit(make_user, auth_as)
    marca = await _marca(db, "SemMarca")
    c, f = await _criativo(db, marca=None)
    rede = await _conta(db, marca, conta="semmarca.oficial")
    r = await client.post(API, json=_body(c, f, [rede]))
    assert r.status_code == 409
    assert _code(r) == "criativo_sem_marca"


async def test_agendar_recusa_data_absurda_e_passada(client, db, make_user, auth_as):
    await _user_edit(make_user, auth_as)
    marca = await _marca(db, "Horizonte")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="horizonte.oficial")
    longe = (datetime.now(UTC) + timedelta(days=400)).isoformat()
    r = await client.post(API, json=_body(c, f, [rede], agendado_para=longe))
    assert _code(r) == "agendamento_longe_demais"
    passado = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    r = await client.post(API, json=_body(c, f, [rede], agendado_para=passado))
    assert _code(r) == "agendamento_no_passado"


async def test_patch_sem_hora_vira_pendente(client, db, make_user, auth_as):
    """Tirar a hora de um `agendado` deixava a linha órfã: `promover_agendadas`
    filtra por `agendado_para IS NOT NULL` e nunca mais olhava pra ela."""
    await _user_edit(make_user, auth_as)
    marca = await _marca(db, "PatchNulo")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="patchnulo.oficial")
    p = await _postagem(
        db,
        c,
        f,
        rede,
        status="agendado",
        agendado_para=datetime.now(UTC) + timedelta(hours=3),
    )
    r = await client.patch(f"{API}/{p.id}", json={"agendado_para": None})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pendente"
    await db.refresh(p)
    assert p.agendado_para is None and p.status == "pendente"


async def test_patch_remarcando_re_checa_o_espacamento(client, db, make_user, auth_as):
    """Remarcar mudava a hora sem olhar o teto/espaçamento da conta no NOVO
    horário — dava pra colar dois posts editando um deles."""
    await _user_edit(make_user, auth_as)
    marca = await _marca(db, "Remarca")
    rede = await _conta(db, marca, conta="remarca.oficial", intervalo_min=90)
    agora = datetime.now(UTC)
    c1, f1 = await _criativo(db, marca=marca, file_rel=f"creatives/{uuid.uuid4()}/a.mp4")
    await _postagem(
        db, c1, f1, rede, status="agendado", agendado_para=agora + timedelta(hours=5)
    )
    c2, f2 = await _criativo(db, marca=marca, file_rel=f"creatives/{uuid.uuid4()}/b.mp4")
    p = await _postagem(
        db, c2, f2, rede, status="agendado", agendado_para=agora + timedelta(hours=9)
    )
    colado = (agora + timedelta(hours=5, minutes=10)).isoformat()
    r = await client.patch(f"{API}/{p.id}", json={"agendado_para": colado})
    assert r.status_code == 409
    assert _code(r) == "intervalo_curto"
    await db.refresh(p)
    assert p.agendado_para.astimezone(UTC).hour == (agora + timedelta(hours=9)).hour


# ─────────────── upload do criativo: hash e arquivo com postagem ───────────


@pytest.fixture
def _monta_criativos():
    """O router de Criativos também só entra com `enable_marketing` — aqui ele
    é preciso porque o hash do vídeo nasce no UPLOAD, não no agendamento."""
    from app.main import app
    from app.routers import marketing_creatives as mod

    if not any(getattr(r, "path", "").startswith(API_CRIATIVOS) for r in app.routes):
        app.include_router(mod.router)


API_CRIATIVOS = "/api/marketing/creatives"


async def test_upload_grava_o_sha256_do_video(
    client, db, make_user, auth_as, _monta_criativos
):
    """Sem o hash gravado, a trava de "esse vídeo já rodou em outra marca"
    NUNCA dispara — a regra existia e não valia pra nada, porque a coluna
    nascia NULL em todo upload."""
    u = await make_user(
        permissions={"marketing_criativos": {"view": True, "edit": True}}
    )
    auth_as(u)
    marca = await _marca(db, "Hash")
    c = MarketingCreative(modelo="Mala", marca=marca.slug, marca_id=marca.id)
    db.add(c)
    await db.commit()
    await db.refresh(c)

    conteudo = b"\x00\x00\x00\x18ftypmp42-bytes-do-video"
    r = await client.post(
        f"{API_CRIATIVOS}/{c.id}/arquivo",
        files={"files": ("video.mp4", conteudo, "video/mp4")},
    )
    assert r.status_code == 200, r.text

    import hashlib

    rec = (
        await db.execute(
            select(MarketingCreativeFile).where(MarketingCreativeFile.creative_id == c.id)
        )
    ).scalars().one()
    assert rec.sha256 == hashlib.sha256(conteudo).hexdigest()
    assert rec.file_size == len(conteudo)


async def test_reupload_do_mesmo_nome_nao_apaga_postagem(
    client, db, make_user, auth_as, _monta_criativos
):
    """Subir outro arquivo com o MESMO nome trocava o registro — e a FK das
    postagens é CASCADE: o histórico do que já foi publicado sumia calado,
    junto com as agendadas."""
    u = await make_user(
        permissions={"marketing_criativos": {"view": True, "edit": True}}
    )
    auth_as(u)
    marca = await _marca(db, "Reupload")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="reupload.oficial")
    p = await _postagem(db, c, f, rede, status="publicado", post_url="https://insta/x")

    r = await client.post(
        f"{API_CRIATIVOS}/{c.id}/arquivo",
        files={"files": ("video.mp4", b"outro-video-qualquer", "video/mp4")},
    )

    assert r.status_code == 409
    assert _code(r) == "arquivo_com_postagem"
    assert await db.get(MarketingPostagem, p.id) is not None, "o histórico sumiu"


# ─────────── regressões da revisão de segurança (16/09/2026) ───────────


async def test_mime_do_uploader_nao_volta_como_html(
    client, db, make_user, auth_as, _monta_criativos
):
    """O `file_mime` é o Content-Type que o NAVEGADOR DE QUEM SOBE mandou.

    Servir isso `inline` no mesmo domínio do DaVinci deixa alguém com
    permissão de editar criativo subir um `text/html` e rodar script com o
    cookie de sessão de quem abrir o "vídeo". Fora da allowlist o arquivo
    continua acessível, mas como anexo octet-stream — que o navegador não
    executa.
    """
    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    auth_as(u)
    marca = await _marca(db, "Mime")
    c = MarketingCreative(modelo="Mala", marca=marca.slug, marca_id=marca.id)
    db.add(c)
    await db.commit()
    await db.refresh(c)

    r = await client.post(
        f"{API_CRIATIVOS}/{c.id}/arquivo",
        files={"files": ("payload.html", b"<script>alert(document.cookie)</script>", "text/html")},
    )
    assert r.status_code == 200, r.text
    rec = (
        await db.execute(
            select(MarketingCreativeFile).where(MarketingCreativeFile.creative_id == c.id)
        )
    ).scalars().one()

    baixado = await client.get(f"{API_CRIATIVOS}/{c.id}/arquivo/{rec.id}")
    assert baixado.status_code == 200
    assert baixado.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in baixado.headers.get("content-disposition", "")
    assert baixado.headers.get("x-content-type-options") == "nosniff"
    assert "text/html" not in baixado.headers["content-type"]


async def test_video_publico_recusa_o_que_nao_e_video(
    client, db, make_user, auth_as, _monta_criativos
):
    """A porta pública existe só pra Meta baixar Reel. Ela não serve HTML, nem
    PDF, nem o MIME que o uploader escolheu — só vídeo."""
    from app.services.marketing import link_criativo

    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    auth_as(u)
    marca = await _marca(db, "MimePub")
    c = MarketingCreative(modelo="Mala", marca=marca.slug, marca_id=marca.id)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    await client.post(
        f"{API_CRIATIVOS}/{c.id}/arquivo",
        files={"files": ("x.html", b"<script>1</script>", "text/html")},
    )
    rec = (
        await db.execute(
            select(MarketingCreativeFile).where(MarketingCreativeFile.creative_id == c.id)
        )
    ).scalars().one()

    r = await client.get(f"{API_CRIATIVOS}/video/{link_criativo.gerar_token(rec.id)}")
    assert r.status_code == 404
    assert _code(r) == "arquivo_nao_e_video"


async def test_upload_tem_teto_de_tamanho(
    client, db, make_user, auth_as, _monta_criativos, monkeypatch
):
    """Sem teto, um POST sozinho enche o disco — que é o mesmo da API e do
    Postgres. O arquivo parcial não pode ficar no disco."""
    from app.routers import marketing_creatives as mod

    monkeypatch.setattr(mod, "MAX_BYTES_ARQUIVO", 1024)
    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    auth_as(u)
    marca = await _marca(db, "Teto")
    c = MarketingCreative(modelo="Mala", marca=marca.slug, marca_id=marca.id)
    db.add(c)
    await db.commit()
    await db.refresh(c)

    r = await client.post(
        f"{API_CRIATIVOS}/{c.id}/arquivo",
        files={"files": ("grande.mp4", b"\x00" * 5000, "video/mp4")},
    )
    assert r.status_code == 413
    assert _code(r) == "arquivo_grande_demais"
    caminho = Path(get_settings().uploads_dir) / f"creatives/{c.id}/grande.mp4"
    assert not caminho.exists(), "sobrou arquivo parcial no disco"


def test_sentry_apaga_query_com_segredo_e_preserva_o_resto():
    """A Graph API exige `?input_token=<token>` no debug_token, e a integração
    de httpx do Sentry grava a query em `span.data`. O EventScrubber não
    alcança (ele casa CHAVE, e a chave ali é `http.query`)."""
    from app.services.sentry import _sem_query_com_segredo

    evento = {
        "spans": [
            {"data": {
                "url": "https://graph.facebook.com/v26.0/debug_token?input_token=SEGREDO",
                "http.query": "input_token=SEGREDO",
            }},
            {"data": {"url": "https://api.exemplo/pedidos?page=2", "http.query": "page=2"}},
        ],
        "breadcrumbs": {"values": [{"data": {"url": "https://x/y?access_token=SEGREDO"}}]},
    }
    limpo = _sem_query_com_segredo(evento)
    bruto = str(limpo)
    assert "SEGREDO" not in bruto
    # O caminho continua legível — é ele que diz QUAL chamada falhou.
    assert "debug_token" in limpo["spans"][0]["data"]["url"]
    # Query sem segredo fica intacta: o trace continua servindo pra diagnóstico.
    assert limpo["spans"][1]["data"]["http.query"] == "page=2"


async def test_trocar_a_marca_na_celula_sincroniza_o_marca_id(
    client, db, make_user, auth_as, _monta_criativos
):
    """A célula Marca é texto; `marca_id` é a ligação com Cadastros › Marcas —
    e é ela que o robô usa pra decidir em quais contas o vídeo pode sair.

    Sem sincronizar, quem troca a marca na tela muda só o texto: o id continua
    na marca antiga e a publicação é recusada com "essa conta é de outra
    marca" numa linha que na tela parece certíssima. Foi exatamente o que
    aconteceu em produção no primeiro teste.
    """
    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    auth_as(u)
    antiga = await _marca(db, "Uranyx")
    # Marca renomeada: o nome mudou, o slug ficou o antigo — caso real da
    # Charlots, que tem slug "poofy".
    nova = Marca(nome="Charlots", slug="poofy")
    db.add(nova)
    await db.commit()
    await db.refresh(nova)
    c, _f = await _criativo(db, marca=antiga)
    assert c.marca_id == antiga.id

    r = await client.patch(f"{API_CRIATIVOS}/{c.id}", json={"marca": "poofy"})
    assert r.status_code == 200, r.text
    await db.refresh(c)
    assert c.marca == "poofy"
    assert c.marca_id == nova.id, "o id ficou na marca antiga"

    # Também casa pelo NOME, não só pelo slug.
    await client.patch(f"{API_CRIATIVOS}/{c.id}", json={"marca": "Charlots"})
    await db.refresh(c)
    assert c.marca_id == nova.id

    # Marca que não existe no cadastro zera o id — não deixa apontando pra
    # marca errada.
    await client.patch(f"{API_CRIATIVOS}/{c.id}", json={"marca": "marca-que-nao-existe"})
    await db.refresh(c)
    assert c.marca_id is None
