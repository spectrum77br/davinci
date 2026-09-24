"""Métricas por vídeo publicado (services/marketing/metricas.py).

Pedido do Eduardo (23/09/2026): ver views e interações POR MARCA no DaVinci,
com o total somado e o detalhe de cada rede — e por vídeo publicado pelo
automático, não o número geral do canal.

O que estes testes defendem, e por quê:

  - NULO não é ZERO. Cada rede entrega um conjunto diferente de números (o
    YouTube não tem "salvamento"; o Instagram só dá views com uma permissão que
    o token de hoje não tem). Somar tratando nulo como zero é como a tela
    mentiria sem ninguém perceber.
  - RETRATO DATADO. As três redes devolvem acumulado, nunca o do dia. Duas
    coletas no mesmo dia atualizam a MESMA linha; no dia seguinte nasce outra,
    e a diferença entre elas é o que rendeu. Sobrescrever o dia anterior
    apagaria a curva, que é metade do valor pedido.
  - FALHA ISOLADA. Coleta falha BAIXO: a tela mostra número velho e ninguém
    nota. Uma rede fora do ar não pode derrubar as outras, e o motivo tem que
    ficar gravado na linha.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import MarketingPostagemMetrica
from app.services.marketing import metricas as svc

pytestmark = pytest.mark.asyncio


# ---------- o dia da coleta (fuso) ----------


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
async def test_dia_e_o_dia_de_brasilia_nao_o_de_utc():
    """23h de segunda em Brasília é 02h de terça em UTC.

    Se o dia saísse de UTC, a coleta da noite e a da manhã seguinte cairiam em
    dias diferentes sem que nenhum dia tivesse virado pro Eduardo — e a curva
    ganharia um degrau que não existiu.
    """
    noite = datetime(2026, 9, 22, 23, 30, tzinfo=UTC).astimezone(svc.BRT)
    madrugada = datetime(2026, 9, 23, 2, 30, tzinfo=UTC)  # ainda 23h de 22/09 em BRT
    assert svc._dia(madrugada).date() == svc._dia(noite.astimezone(UTC)).date()
    assert svc._dia(madrugada).date().isoformat() == "2026-09-22"
    # E o dia seguinte é outro retrato.
    manha = datetime(2026, 9, 23, 13, 0, tzinfo=UTC)  # 10h em BRT
    assert svc._dia(manha).date().isoformat() == "2026-09-23"


# ---------- credencial NUNCA entra em mensagem de erro ----------


def test_erro_nao_pode_carregar_token():
    """Vazamento real de 23/09/2026: a Graph recebe o token na QUERY STRING, e
    o HTTPStatusError do httpx traz a URL inteira na mensagem. A primeira
    coleta gravou quatro tokens de produção em texto puro na coluna `erro` —
    que a tela mostra no tooltip.

    O token saiu da URL (vai no cabeçalho agora), mas isto aqui é a segunda
    camada: erro de biblioteca que a gente não controla também passa por aqui.
    """
    sujo = (
        "HTTPStatusError: Client error '400 Bad Request' for url "
        "'https://graph.facebook.com/v26.0/181043?fields=like_count"
        "&access_token=EAAO66XJZBvlQBSRIxtUqsEhAZBcrhmztNAV67Hs5AE'"
    )
    limpo = svc.sem_segredo(sujo)
    assert "EAAO66XJZBvlQBSRIxtUqsEh" not in limpo, "o token não pode sobreviver"
    assert "access_token=(removido)" in limpo
    assert "400 Bad Request" in limpo, "o que interessa do erro continua legível"
    assert "like_count" in limpo, "o resto da URL ajuda a diagnosticar e fica"

    # As outras credenciais que podem aparecer numa mensagem da Meta/Google.
    for chave in ("refresh_token", "client_secret", "token"):
        assert "sup3r-s3cr3t" not in svc.sem_segredo(f"erro: {chave}=sup3r-s3cr3t&x=1")
    assert svc.sem_segredo("") == ""
    assert svc.sem_segredo(None) == ""


# ---------- leitura do TikTok (sem API, da página pública) ----------


def _pagina_tiktok(stats: dict | None, *, status: int | str = 0) -> str:
    corpo = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "statusCode": 0 if not status else 10204,
                "statusMsg": "" if not status else str(status),
                "itemInfo": {"itemStruct": {"id": "123", "statsV2": stats}} if stats else {},
            }
        }
    }
    return (
        '<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(corpo)
        + "</script></html>"
    )


class _RespostaFalsa:
    def __init__(self, texto: str) -> None:
        self.text = texto

    def raise_for_status(self) -> None:
        pass


class _ClienteFalso:
    def __init__(self, texto: str) -> None:
        self._t = texto

    async def get(self, *a, **kw) -> _RespostaFalsa:
        return _RespostaFalsa(self._t)


async def test_tiktok_le_os_numeros_da_pagina_publica():
    pagina = _pagina_tiktok(
        {
            "playCount": "1234",
            "diggCount": "56",
            "commentCount": "7",
            "shareCount": "8",
            "collectCount": "9",
        }
    )
    d = await svc.do_tiktok("https://tiktok.com/@x/video/123", client=_ClienteFalso(pagina))
    assert d["views"] == 1234, "playCount é o que o Eduardo chama de views"
    assert d["curtidas"] == 56
    assert d["comentarios"] == 7
    assert d["compartilhamentos"] == 8
    assert d["salvamentos"] == 9
    assert d["bruto"]["playCount"] == "1234", "o cru fica guardado pra conferência"


async def test_video_apagado_e_REMOVIDO_nao_falha_de_leitura():
    """Post apagado devolve statusCode != 0. Duas coisas de uma vez:

    Gravar ZERO aí diria que o vídeo está no ar sem render nada, e a média da
    marca desabaria calada. Por isso vira erro, não número.

    Mas também não é "a leitura falhou": a leitura funcionou e a resposta foi
    "isto não está mais aqui". O Eduardo apaga vídeo de teste de propósito, e
    pôr um triângulo de alerta em cima disso some com o alerta de verdade no
    meio do ruído. Daí o prefixo canônico, que a tela lê pra separar os dois.
    """
    pagina = _pagina_tiktok(None, status="item_privacy_authorization&status_deleted")
    with pytest.raises(RuntimeError) as e:
        await svc.do_tiktok("https://tiktok.com/@x/video/123", client=_ClienteFalso(pagina))
    assert svc.foi_removido(str(e.value)), "apagado tem que ser reconhecível pela tela"


async def test_recusa_que_NAO_e_exclusao_continua_sendo_falha():
    """Bloqueio, captcha ou mudança de layout são problema de verdade e não
    podem se disfarçar de 'vídeo apagado' — senão o alerta some pra sempre."""
    pagina = _pagina_tiktok(None, status="rate_limit")
    with pytest.raises(RuntimeError) as e:
        await svc.do_tiktok("https://tiktok.com/@x/video/123", client=_ClienteFalso(pagina))
    assert not svc.foi_removido(str(e.value))
    assert "recusou a página" in str(e.value)


async def test_tiktok_layout_mudou_vira_erro_explicito():
    """Sem API não há contrato: o TikTok pode trocar a estrutura sem aviso. O
    coletor precisa gritar, não gravar zero."""
    with pytest.raises(RuntimeError, match="JSON embutido"):
        await svc.do_tiktok("https://x", client=_ClienteFalso("<html>nada aqui</html>"))


async def test_tiktok_numero_ilegivel_vira_nulo_nao_zero():
    pagina = _pagina_tiktok({"playCount": None, "diggCount": "3"})
    d = await svc.do_tiktok("https://x", client=_ClienteFalso(pagina))
    assert d["views"] is None, "não deu o número ≠ deu zero"
    assert d["curtidas"] == 3


# ---------- o retrato datado (o coração do desenho) ----------


async def _cenario(db, *, plataforma: str = "tiktok"):
    """Marca + criativo + conta + uma postagem PUBLICADA."""
    from app.models import (
        Marca,
        MarketingCreative,
        MarketingCreativeFile,
        MarketingPostagem,
        RedeSocial,
    )

    m = Marca(nome="Uranyx", slug="uranyx")
    db.add(m)
    await db.flush()
    c = MarketingCreative(modelo="F109S 256 GB", marca="uranyx", marca_id=m.id, aprovado=True)
    db.add(c)
    await db.flush()
    f = MarketingCreativeFile(
        creative_id=c.id, file_name="v.mp4", file_mime="video/mp4", file_rel=f"x/{c.id}.mp4"
    )
    db.add(f)
    r = RedeSocial(marca_id=m.id, plataforma=plataforma, conta="uranyx_brasil", ativo=True)
    db.add(r)
    await db.flush()
    p = MarketingPostagem(
        creative_id=c.id,
        file_id=f.id,
        rede_social_id=r.id,
        plataforma=plataforma,
        conta="uranyx_brasil",
        status="publicado",
        publicado_em=datetime.now(UTC) - timedelta(hours=2),
        post_url="https://www.tiktok.com/@uranyx_brasil/video/123",
        post_external_id="123",
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return m, p


async def _linhas(db, postagem_id):
    return (
        (
            await db.execute(
                select(MarketingPostagemMetrica)
                .where(MarketingPostagemMetrica.postagem_id == postagem_id)
                .order_by(MarketingPostagemMetrica.dia)
            )
        )
        .scalars()
        .all()
    )


async def test_duas_coletas_no_mesmo_dia_atualizam_a_mesma_linha(db, monkeypatch):
    """O número é ACUMULADO: duas leituras no mesmo dia são duas medições do
    MESMO ponto, e a última é a boa. Inserir de novo inventaria um degrau na
    curva que não existiu."""
    m, p = await _cenario(db)

    async def falso(url, *, client):
        return {"views": falso.n, "curtidas": 1, "bruto": {}}

    falso.n = 100
    monkeypatch.setattr(svc, "do_tiktok", falso)

    await svc.coletar(db)
    falso.n = 175
    await svc.coletar(db)

    linhas = await _linhas(db, p.id)
    assert len(linhas) == 1, "mesmo dia = um retrato só"
    assert linhas[0].views == 175, "vale a última medição do dia"
    assert linhas[0].marca_id == m.id, "a marca vai em snapshot na linha"


async def test_dia_seguinte_e_outro_retrato_e_a_curva_aparece(db, monkeypatch):
    """É isto que responde 'quanto rendeu esta semana': a diferença entre dois
    retratos. Sem a linha de ontem, só existe o acumulado de hoje."""
    _, p = await _cenario(db)

    async def falso(url, *, client):
        return {"views": falso.n, "bruto": {}}

    falso.n = 100
    monkeypatch.setattr(svc, "do_tiktok", falso)
    ontem = datetime.now(UTC) - timedelta(days=1)
    await svc.coletar(db, agora=ontem)
    falso.n = 260
    await svc.coletar(db)

    linhas = await _linhas(db, p.id)
    assert len(linhas) == 2, "dias diferentes = dois retratos"
    assert [l.views for l in linhas] == [100, 260]
    assert linhas[1].views - linhas[0].views == 160, "o que rendeu no dia é uma subtração"


async def test_rede_que_nao_da_um_numero_grava_NULO_e_nao_zero(db, monkeypatch):
    """O YouTube não tem 'salvamento'. Gravar 0 diria que ninguém salvou —
    diferente de 'esta rede não mede isso'. A tela soma só o que existe."""
    _, p = await _cenario(db)

    async def falso(url, *, client):
        return {"views": 10, "curtidas": 2, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", falso)
    await svc.coletar(db)

    linha = (await _linhas(db, p.id))[0]
    assert linha.views == 10
    assert linha.salvamentos is None, "não veio ≠ veio zero"
    assert linha.alcance is None


async def test_falha_de_uma_postagem_nao_derruba_a_rodada_e_fica_gravada(db, monkeypatch):
    """Coleta falha BAIXO: a tela mostraria número velho e ninguém notaria. O
    motivo tem que ficar na linha pra a tela poder mostrar 'coletado em'."""
    _, p = await _cenario(db)

    async def explode(url, *, client):
        raise RuntimeError("o TikTok mudou o layout")

    monkeypatch.setattr(svc, "do_tiktok", explode)
    r = await svc.coletar(db)

    assert r == {"total": 1, "ok": 0, "falhou": 1}
    linha = (await _linhas(db, p.id))[0]
    assert linha.views is None
    assert "mudou o layout" in (linha.erro or ""), "o motivo fica gravado, não se perde"


async def test_so_coleta_o_que_foi_publicado_e_dentro_da_janela(db, monkeypatch):
    """Postagem que falhou não tem número, e vídeo velho só acumula resíduo."""
    _, p = await _cenario(db)
    p.status = "falhou"
    await db.commit()

    chamou = []

    async def falso(url, *, client):
        chamou.append(url)
        return {"views": 1, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", falso)
    assert (await svc.coletar(db))["total"] == 0
    assert not chamou, "postagem que não publicou não é consultada"

    p.status = "publicado"
    p.publicado_em = datetime.now(UTC) - timedelta(days=svc.JANELA_DIAS + 1)
    await db.commit()
    assert (await svc.coletar(db))["total"] == 0, "fora da janela também não"


# ---------- a tela: acumulado x no período ----------

API_M = "/api/marketing/metricas"


@pytest.fixture(scope="module", autouse=True)
def _monta_router_metricas():
    """`main.py` só inclui os routers de Marketing com `enable_marketing`
    ligado, e o ambiente de teste não liga a flag."""
    from app.main import app
    from app.routers import marketing_metricas as mod

    if not any(getattr(r, "path", "") == API_M for r in app.routes):
        app.include_router(mod.router)


async def _ve(make_user, auth_as):
    u = await make_user(permissions={"marketing_criativos": {"view": True}})
    auth_as(u)
    return u


async def test_tela_separa_acumulado_do_que_rendeu_no_periodo(
    client, db, make_user, auth_as, monkeypatch
):
    """São duas contas diferentes, e confundi-las é o jeito mais fácil de a
    tela mentir. ACUMULADO é quanto o vídeo tem hoje — vem do retrato mais
    novo. NO PERÍODO é quanto ganhou na janela — é a diferença entre o novo e
    o velho. Somar todos os retratos multiplicaria o mesmo vídeo."""
    await _ve(make_user, auth_as)
    _, p = await _cenario(db)

    async def falso(url, *, client):
        return {"views": falso.n, "curtidas": falso.n // 10, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", falso)
    falso.n = 100
    await svc.coletar(db, agora=datetime.now(UTC) - timedelta(days=2))
    falso.n = 180
    await svc.coletar(db, agora=datetime.now(UTC) - timedelta(days=1))
    falso.n = 300
    await svc.coletar(db)

    r = await client.get(API_M, params={"dias": 30})
    assert r.status_code == 200, r.text
    marca = r.json()["marcas"][0]

    assert marca["marca"] == "Uranyx"
    assert marca["posts"] == 1, "um vídeo, não três — os retratos não se somam"
    assert marca["acumulado"]["views"] == 300, "acumulado = o retrato mais novo"
    assert marca["no_periodo"]["views"] == 200, "rendeu no período = 300 - 100"

    plat = marca["plataformas"][0]
    assert plat["plataforma"] == "tiktok"
    assert plat["acumulado"]["views"] == 300
    assert plat["no_periodo"]["views"] == 200
    assert plat["coletado_em"] is not None, "a tela precisa dizer quando leu"


async def test_janela_curta_so_conta_o_que_rendeu_dentro_dela(
    client, db, make_user, auth_as, monkeypatch
):
    """Pedindo 1 dia, o ganho tem que ser o do dia — não o histórico inteiro."""
    await _ve(make_user, auth_as)
    _, p = await _cenario(db)

    async def falso(url, *, client):
        return {"views": falso.n, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", falso)
    falso.n = 100
    await svc.coletar(db, agora=datetime.now(UTC) - timedelta(days=10))
    falso.n = 900
    await svc.coletar(db)

    largo = (await client.get(API_M, params={"dias": 30})).json()["marcas"][0]
    assert largo["no_periodo"]["views"] == 800

    curto = (await client.get(API_M, params={"dias": 1})).json()["marcas"][0]
    assert curto["acumulado"]["views"] == 900, "o acumulado é o mesmo sempre"
    assert not curto["no_periodo"], "só um retrato na janela = não dá pra saber o ganho"


async def test_metrica_que_nenhuma_rede_deu_nao_vira_zero_na_tela(
    client, db, make_user, auth_as, monkeypatch
):
    """Se ninguém reportou `salvamentos`, o campo não pode aparecer como 0 — a
    tela escreveria que ninguém salvou, e isso a gente não sabe."""
    await _ve(make_user, auth_as)
    await _cenario(db)

    async def falso(url, *, client):
        return {"views": 5, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", falso)
    await svc.coletar(db)

    marca = (await client.get(API_M)).json()["marcas"][0]
    assert marca["acumulado"]["views"] == 5
    assert "salvamentos" not in marca["acumulado"], "ausente ≠ zero"


async def test_sem_permissao_nao_ve(client, db, make_user, auth_as):
    u = await make_user(permissions={})
    auth_as(u)
    assert (await client.get(API_M)).status_code == 403


async def test_abrir_a_plataforma_mostra_cada_video_com_seus_numeros(
    client, db, make_user, auth_as, monkeypatch
):
    """O nível que o Eduardo pediu: "a marca fez 9 views" não diz QUAL vídeo
    fez, e é o qual que serve pra decidir o que produzir."""
    await _ve(make_user, auth_as)
    _, p = await _cenario(db)
    p.legenda = "Tecnologia que aguenta o teu dia 🔋\nsegunda linha que não entra no título"
    await db.commit()

    async def falso(url, *, client):
        return {"views": 42, "curtidas": 7, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", falso)
    await svc.coletar(db)

    plat = (await client.get(API_M)).json()["marcas"][0]["plataformas"][0]
    assert len(plat["videos"]) == 1
    v = plat["videos"][0]
    assert v["acumulado"]["views"] == 42
    assert v["post_url"] == p.post_url, "o link abre o vídeo na plataforma"
    assert v["titulo"] == "Tecnologia que aguenta o teu dia 🔋", "só a 1ª linha da legenda"
    assert v["removido"] is False


async def test_marca_sem_nenhum_video_no_ar_some_da_tela(
    client, db, make_user, auth_as, monkeypatch
):
    """Pedido do Eduardo (24/09/2026): ele apagou os testes da charlots e da
    7buyers, e elas ficaram na tela como linhas só de travessão. Marca cujos
    vídeos foram TODOS apagados não tem o que reportar.

    Mas não pode sumir em silêncio, senão vira "cadê a charlots?": o nome vai
    numa lista à parte, pra tela poder dizer quantas estão fora e por quê.
    """
    await _ve(make_user, auth_as)
    await _cenario(db)

    async def apagado(url, *, client):
        raise RuntimeError(f"{svc.REMOVIDO} o vídeo não está mais no ar")

    monkeypatch.setattr(svc, "do_tiktok", apagado)
    await svc.coletar(db)

    r = (await client.get(API_M)).json()
    assert r["marcas"] == [], "marca sem vídeo no ar não ocupa espaço na tela"
    assert r["sem_video_no_ar"] == ["Uranyx"], "mas o nome não se perde"


async def test_video_apagado_nao_conta_no_total_da_marca(
    client, db, make_user, auth_as, monkeypatch
):
    """Com um vídeo no ar e outro apagado, a marca aparece — com UM vídeo.
    Contar o apagado faria a marca parecer ter mais material publicado do que
    tem, e derrubaria qualquer média por vídeo."""
    from app.models import MarketingPostagem

    await _ve(make_user, auth_as)
    _, p = await _cenario(db)
    morto = MarketingPostagem(
        creative_id=p.creative_id, file_id=p.file_id, rede_social_id=p.rede_social_id,
        plataforma="tiktok", conta=p.conta, status="publicado",
        publicado_em=datetime.now(UTC) - timedelta(hours=3),
        post_url="https://www.tiktok.com/@uranyx_brasil/video/999",
        post_external_id="999",
    )
    db.add(morto)
    await db.commit()

    async def talvez(url, *, client):
        if url.endswith("/999"):
            raise RuntimeError(f"{svc.REMOVIDO} o vídeo não está mais no ar")
        return {"views": 50, "curtidas": 5, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", talvez)
    await svc.coletar(db)

    marca = (await client.get(API_M)).json()["marcas"][0]
    assert marca["posts"] == 1, "só o que está no ar conta"
    assert marca["acumulado"]["views"] == 50
    plat = marca["plataformas"][0]
    assert plat["posts"] == 1
    assert [v["post_url"] for v in plat["videos"]] == [p.post_url], "o apagado sai da lista"


async def test_falha_de_verdade_continua_alertando(
    client, db, make_user, auth_as, monkeypatch
):
    """Bloqueio ou mudança de layout não podem se disfarçar de vídeo apagado."""
    await _ve(make_user, auth_as)
    await _cenario(db)

    async def quebrou(url, *, client):
        raise RuntimeError("o TikTok recusou a página: rate_limit")

    monkeypatch.setattr(svc, "do_tiktok", quebrou)
    await svc.coletar(db)

    plat = (await client.get(API_M)).json()["marcas"][0]["plataformas"][0]
    assert plat["erro"] is not None, "problema de verdade tem que alertar"
    assert plat["videos"][0]["removido"] is False


async def test_instagram_post_apagado_e_reconhecido_como_removido():
    """A Meta diz "apagado" com code=100/subcode=33 ("Object with ID does not
    exist"). Descoberto em 23/09/2026: com o token velho essa resposta vinha
    MASCARADA de erro de permissão (code 10), então post apagado e permissão
    faltando eram indistinguíveis — e os dois viravam alerta na tela."""
    import httpx

    class _R:
        status_code = 400
        headers = {"content-type": "application/json"}

        def json(self):
            return {"error": {"code": 100, "error_subcode": 33,
                              "message": "Unsupported get request. Object with ID does not exist"}}

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw): return _R()

    import app.services.marketing.metricas as m
    orig = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: _C()
    try:
        with pytest.raises(RuntimeError) as e:
            await m.do_instagram("123", "token-falso")
        assert m.foi_removido(str(e.value)), "apagado tem que ser reconhecível pela tela"
    finally:
        httpx.AsyncClient = orig
