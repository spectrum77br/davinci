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

import importlib.util
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select, text

from app.models import MarketingPostagemMetrica
from app.services.marketing import metricas as svc

pytestmark = pytest.mark.asyncio


class _RedisFalso:
    """O pedaço do Redis que a tela e o worker usam, em memória: a trava do
    botão, a trava da rodada e a `ultima_rodada`. Com o Redis de verdade, um
    teste herdaria a trava de 10 min do anterior."""

    def __init__(self) -> None:
        self.d: dict[str, tuple[str, float | None]] = {}

    def _vivo(self, k: str) -> bool:
        if k not in self.d:
            return False
        _, expira = self.d[k]
        if expira is not None and expira <= time.monotonic():
            del self.d[k]
            return False
        return True

    async def set(self, k, v, nx=False, ex=None):
        if nx and self._vivo(k):
            return None
        self.d[k] = (v, time.monotonic() + ex if ex else None)
        return True

    async def get(self, k):
        return self.d[k][0] if self._vivo(k) else None

    async def ttl(self, k):
        if not self._vivo(k):
            return -2
        expira = self.d[k][1]
        return -1 if expira is None else max(int(expira - time.monotonic()), 0)

    async def exists(self, k):
        return 1 if self._vivo(k) else 0

    async def delete(self, *ks):
        n = 0
        for k in ks:
            if self._vivo(k):
                del self.d[k]
                n += 1
        return n


@pytest.fixture(autouse=True)
def redis_falso(monkeypatch):
    """Sem pausa entre páginas do TikTok (1 s por post deixaria a suíte lenta
    à toa) e com o Redis da tela em memória."""
    from app.routers import marketing_metricas as rota

    monkeypatch.setattr(svc, "PAUSA_TIKTOK_S", 0)
    falso = _RedisFalso()
    monkeypatch.setattr(rota, "redis", falso)
    return falso


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


def _pagina_tiktok(stats: dict | None, *, status: int | str = 0, **item) -> str:
    corpo = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "statusCode": 0 if not status else 10204,
                "statusMsg": "" if not status else str(status),
                "itemInfo": (
                    {"itemStruct": {"id": "123", "statsV2": stats, **item}} if stats else {}
                ),
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


async def _cenario(
    db, *, plataforma: str = "tiktok", publicado_ha: timedelta = timedelta(hours=2)
):
    """Marca + criativo + conta + uma postagem PUBLICADA.

    `publicado_ha` (24/09/2026): a tela nova compara "na mesma idade" e só
    trata a primeira leitura como ganho quando o vídeo foi lido desde o
    nascimento. Teste que simula leitura de dias atrás precisa de um post que
    já existia nesses dias — senão está medindo um vídeo antes de ele nascer.
    """
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
        publicado_em=datetime.now(UTC) - publicado_ha,
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
    o velho. Somar todos os retratos multiplicaria o mesmo vídeo.

    24/09/2026: o post agora é de 40 dias atrás. Com o post de 2 h, a primeira
    leitura (de 2 dias atrás) era de antes de ele existir. O esperado continua
    200: a primeira leitura de um vídeo antigo é BASE, não ganho."""
    await _ve(make_user, auth_as)
    _, p = await _cenario(db, publicado_ha=timedelta(days=40))

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
    """Pedindo 1 dia, o ganho tem que ser o do dia — não o histórico inteiro.

    Mudou de propósito em 24/09/2026: antes, 1 dia com um retrato só na janela
    dava "não dá pra saber" (vazio). Agora vale a regra única de ganho diário
    — o buraco de 10 dias entre os dois retratos é dividido por igual (80 por
    dia) e o post sai marcado "estimado", que a tela desenha mais claro. O
    post é de 40 dias atrás pra não ser medido antes de nascer."""
    await _ve(make_user, auth_as)
    _, p = await _cenario(db, publicado_ha=timedelta(days=40))

    async def falso(url, *, client):
        return {"views": falso.n, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", falso)
    falso.n = 100
    await svc.coletar(db, agora=datetime.now(UTC) - timedelta(days=10))
    falso.n = 900
    await svc.coletar(db)

    largo = (await client.get(API_M, params={"dias": 30})).json()["marcas"][0]
    assert largo["no_periodo"]["views"] == 800

    r = (await client.get(API_M, params={"dias": 1})).json()
    curto = r["marcas"][0]
    assert curto["acumulado"]["views"] == 900, "o acumulado é o mesmo sempre"
    assert curto["no_periodo"]["views"] == 80, "800 em 10 dias, dividido por igual"
    assert r["postagens"][0]["no_periodo_estimado"] is True


async def test_post_que_passou_de_90_dias_ainda_soma_no_que_a_rede_rendeu(
    client, db, make_user, auth_as
):
    """A coleta lê todo post de até 90 dias NO DIA da leitura: o que hoje tem
    100 dias rendeu nas semanas passadas, e isso é parte do que a rede rendeu
    no período. Sem ele, o período anterior perdia gente e a comparação
    mentia. Mas ele não volta pra tabela — a tela é dos últimos 90 dias."""
    await _ve(make_user, auth_as)
    agora = datetime.now(UTC)
    _, velho = await _cenario(db, publicado_ha=timedelta(days=100))
    await _retrato(db, velho, lido_em=agora - timedelta(days=20), views=100)
    await _retrato(db, velho, lido_em=agora - timedelta(days=12), views=900)
    novo = await _outro_post(db, velho, "novo", publicado_em=agora - timedelta(days=5))
    await _retrato(db, novo, lido_em=agora - timedelta(days=4, hours=12), views=50)

    r = (await client.get(API_M, params={"dias": 30})).json()
    assert [p["postagem_id"] for p in r["postagens"]] == [str(novo.id)]
    [rede] = r["resumo"]["redes"]
    assert rede["videos"] == 1, "o velho só soma, não é vídeo da tela"
    assert rede["no_periodo"]["views"] == 850, "800 do velho + 50 do novo"
    assert rede["comparacao"]["atual"] == 850
    assert r["marcas"][0]["no_periodo"]["views"] == 50, "a matriz por marca é a da tela"


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
    # …mas vai pro rodapé COM o criativo: é o que deixa a tabela de vídeos
    # escrever "apagado da rede" em vez de "não postado" (24/09/2026).
    fora = (await client.get(API_M)).json()["fora_do_ar"]
    assert [(f["postagem_id"], f["creative_id"]) for f in fora] == [
        (str(morto.id), str(p.creative_id))
    ]


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


# ---------- Facebook: o Reel da Página (25/09/2026) ----------
#
# Até 25/09 o Facebook caía no leitor do Instagram, que pede `like_count` e
# `/insights?metric=views,...` — campos que o nó de VÍDEO do Facebook não tem.
# Todo reel do FB voltava "(#100) Tried accessing nonexisting field" e nunca
# teve número no Desempenho. A Meta inteira é fakada com respx: ZERO rede, e
# nenhum token de verdade sai daqui.

TOKEN_FB = "TOKENPAGINAFB123"  # noqa: S105 — token de teste, não é segredo
VIDEO_FB = "4491972657708925"


def _base_fb() -> str:
    from app.services.marketing import meta_client

    return f"{meta_client.GRAPH_HOST}/{meta_client.graph_version()}"


def _no_video(curtidas: int = 4, comentarios: int = 3) -> dict:
    return {
        "id": VIDEO_FB,
        "comments": {"data": [], "summary": {"order": "ranked", "total_count": comentarios}},
        "likes": {"data": [], "summary": {"total_count": curtidas, "can_like": True}},
    }


def _metrica(nome: str, valor, period: str = "lifetime") -> dict:
    return {
        "name": nome,
        "period": period,
        "values": [{"value": valor}],
        "id": f"{VIDEO_FB}/video_insights/{nome}/{period}",
    }


def _sem_token(router) -> None:
    """O token vai no cabeçalho e em NENHUM outro lugar (vazamento de 23/09)."""
    for chamada in router.calls:
        assert TOKEN_FB not in str(chamada.request.url), "token não pode ir na URL"
        assert chamada.request.headers["authorization"] == f"Bearer {TOKEN_FB}"


async def test_facebook_le_views_e_interacoes_do_video_insights():
    insights = {
        "data": [
            _metrica("fb_reels_total_plays", 1500),
            _metrica("blue_reels_play_count", 1200),
            _metrica("post_impressions_unique", 900),
            _metrica("post_video_social_actions", {"COMMENT": 5, "SHARE": 2}),
            _metrica("post_video_likes_by_reaction_type", {"REACTION_LIKE": 4, "REACTION_LOVE": 1}),
            _metrica("post_video_retention_graph", {str(i): 100 - i for i in range(40)}),
        ],
        "paging": {},
    }
    with respx.mock(assert_all_called=True) as router:
        no = router.get(f"{_base_fb()}/{VIDEO_FB}").mock(
            return_value=httpx.Response(200, json=_no_video())
        )
        ins = router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(
            return_value=httpx.Response(200, json=insights)
        )
        d = await svc.do_facebook(VIDEO_FB, TOKEN_FB)
        _sem_token(router)

    assert d["views"] == 1500, "total de plays conta replay, como as views do Instagram"
    assert d["curtidas"] == 5, "curtida no Facebook é qualquer reação, somadas"
    assert d["comentarios"] == 5, "o insights conta resposta; o nó só o comentário raiz"
    assert d["compartilhamentos"] == 2
    assert d["alcance"] == 900
    assert d["salvamentos"] is None, "o Facebook não mede salvamento: nulo, não zero"
    assert d["bruto"]["fonte"] == {
        "views": "fb_reels_total_plays",
        "curtidas": "insights",
        "comentarios": "insights",
        "compartilhamentos": "insights",
    }
    assert "post_video_retention_graph" not in d["bruto"]["insights"], "gráfico não é número"
    assert d["bruto"]["video"]["id"] == VIDEO_FB, "o cru fica guardado pra conferência"

    campos = no.calls.last.request.url.params["fields"]
    assert "likes" in campos and "comments" in campos
    assert "reactions" not in campos, "campo que o nó não conhece derruba a chamada inteira"
    params = ins.calls.last.request.url.params
    assert "metric" not in params and "period" not in params, (
        "métrica aposentada na lista derruba o insights inteiro; reel recusa period"
    )


async def test_facebook_bruto_nao_guarda_link_de_paginacao_com_token():
    """Aresta da Graph pode trazer `paging.next` com `access_token=` na URL, e o
    bruto vai pro banco sem redação. Só os totais do nó entram."""
    no = _no_video()
    no["likes"]["paging"] = {"next": f"{_base_fb()}/{VIDEO_FB}/likes?access_token={TOKEN_FB}"}
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(return_value=httpx.Response(200, json=no))
        router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        d = await svc.do_facebook(VIDEO_FB, TOKEN_FB)

    assert TOKEN_FB not in json.dumps(d)
    assert d["curtidas"] == 4 and d["comentarios"] == 3


async def test_facebook_metrica_so_do_dia_nao_vira_total():
    """`day` é o balde do último dia: gravado como acumulado, o "ganhou no dia"
    seguinte viraria tombo negativo. Só o `lifetime` entra."""
    insights = {
        "data": [
            {
                "name": "fb_reels_total_plays",
                "period": "day",
                "values": [{"value": 900}, {"value": 12}],
            },
            _metrica("blue_reels_play_count", 700),
        ]
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(
            return_value=httpx.Response(200, json=_no_video())
        )
        router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(
            return_value=httpx.Response(200, json=insights)
        )
        d = await svc.do_facebook(VIDEO_FB, TOKEN_FB)

    assert d["views"] == 700
    assert d["bruto"]["fonte"]["views"] == "blue_reels_play_count"


@pytest.mark.parametrize(
    ("code", "frase"),
    [
        (10, "(#10) Application does not have permission."),
        (200, "(#200) Requires extended permission: read_insights."),
    ],
    ids=["code-10", "code-200"],
)
async def test_facebook_sem_permissao_de_insights_ainda_da_curtidas_e_comentarios(code, frase):
    """Mesmo desenho do Instagram: o token pode não ter `read_insights`. Aí
    sobram curtidas e comentários do próprio nó, que é melhor que linha vazia —
    e NÃO é falha da coleta, é permissão que falta."""
    recusa = {
        "error": {
            "message": f"{frase} access_token={TOKEN_FB}",
            "type": "OAuthException",
            "code": code,
        }
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(
            return_value=httpx.Response(200, json=_no_video(curtidas=4, comentarios=3))
        )
        router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(
            return_value=httpx.Response(400, json=recusa)
        )
        d = await svc.do_facebook(VIDEO_FB, TOKEN_FB)
        _sem_token(router)

    assert "erro" not in d, "permissão faltando não é falha da leitura"
    assert d["curtidas"] == 4 and d["comentarios"] == 3
    assert d["bruto"]["fonte"] == {"curtidas": "video", "comentarios": "video"}
    assert d.get("views") is None, "não veio ≠ veio zero"
    assert d.get("compartilhamentos") is None
    assert f"code {code}" in d["bruto"]["insights_erro"], "o motivo fica anotado pra investigar"
    assert TOKEN_FB not in json.dumps(d), "nem quando a Meta ecoa o token de volta"


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(
            500, json={"error": {"message": "An unknown error", "code": 1, "is_transient": True}}
        ),
        httpx.Response(400, json={"error": {"message": "(#4) Request limit reached", "code": 4}}),
        httpx.Response(200, json={"error": {"message": "(#613) Calls exceeded", "code": 613}}),
        httpx.Response(503, text="<html><body>Service Unavailable</body></html>"),
    ],
    ids=["500-code-1", "400-code-4", "200-com-code-613", "503-html"],
)
async def test_facebook_soluco_no_insights_e_falha_nao_permissao(resposta):
    """O insights é a fonte das views do FB. Soluço dele (5xx, limite de
    chamadas, página HTML) tratado como "falta permissão" voltava views NULAS
    como leitura boa: apagava as do mesmo dia e, sem `erro`, a rodada de hora
    em hora nunca relia (revisão de 25/09/2026)."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(
            return_value=httpx.Response(200, json=_no_video())
        )
        router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(return_value=resposta)
        with pytest.raises(RuntimeError) as e:
            await svc.do_facebook(VIDEO_FB, TOKEN_FB)
        _sem_token(router)

    msg = str(e.value)
    assert "insights" in msg
    assert not svc.foi_removido(msg), "soluço tem que alertar e ser relido, não ir pro rodapé"
    assert TOKEN_FB not in msg


async def test_facebook_soluco_no_insights_nao_apaga_a_leitura_boa_do_dia(db, monkeypatch):
    """Ponta a ponta: views às 10h, insights fora do ar às 11h. A linha do
    dia fica com as views das 10h — e com o erro à vista pra ser relida."""
    p = await _cenario_fb(db, monkeypatch)
    insights = {
        "data": [
            _metrica("fb_reels_total_plays", 1500),
            _metrica("post_video_likes_by_reaction_type", {"REACTION_LIKE": 5}),
        ]
    }
    t = _hoje_as(10)
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/123").mock(return_value=httpx.Response(200, json=_no_video()))
        ins = router.get(f"{_base_fb()}/123/video_insights")
        ins.mock(return_value=httpx.Response(200, json=insights))
        await svc.coletar(db, agora=t)
        ins.mock(return_value=httpx.Response(500, json={"error": {"code": 2, "message": "retry"}}))
        r = await svc.coletar(db, agora=t + timedelta(hours=1))
    assert r == {"total": 1, "ok": 0, "falhou": 1}

    pid = p.id
    db.expire_all()  # o upsert não passa pelo mapa de identidade da sessão
    [linha] = await _linhas(db, pid)
    assert linha.views == 1500, "a leitura boa das 10h fica"
    assert linha.curtidas == 5, "e não troca pra contagem do nó (só o joinha)"
    assert linha.lido_em == t
    assert "code 2" in linha.erro and not svc.foi_removido(linha.erro)


async def test_facebook_no_recusado_com_insights_vazio_e_falha():
    """O nó recusou (não é o 100/33 de apagado) e o insights veio `data: []`:
    não há número de lugar nenhum. Voltar isso sem `erro` gravava uma leitura
    "boa" toda nula, sem alerta e sem releitura — o reel sem número e sem
    motivo que esta mudança veio consertar (revisão de 25/09/2026)."""
    campo = {
        "error": {
            "message": "(#100) Tried accessing nonexisting field (likes)",
            "type": "OAuthException",
            "code": 100,
        }
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(return_value=httpx.Response(400, json=campo))
        router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        with pytest.raises(RuntimeError) as e:
            await svc.do_facebook(VIDEO_FB, TOKEN_FB)
        _sem_token(router)

    msg = str(e.value)
    assert not svc.foi_removido(msg), "campo inválido não é post apagado"
    assert "Tried accessing nonexisting field" in msg and "code 100" in msg


async def test_facebook_no_recusado_mas_insights_com_numero_ainda_le():
    """O outro lado: o nó é plano B. Se ele recusar e o insights trouxer
    número, a leitura vale — e o motivo do nó fica no bruto."""
    campo = {"error": {"message": "(#100) Tried accessing nonexisting field (likes)", "code": 100}}
    insights = {"data": [_metrica("fb_reels_total_plays", 80)]}
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(return_value=httpx.Response(400, json=campo))
        router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(
            return_value=httpx.Response(200, json=insights)
        )
        d = await svc.do_facebook(VIDEO_FB, TOKEN_FB)

    assert "erro" not in d
    assert d["views"] == 80
    assert d.get("curtidas") is None, "sem o nó e sem reações no insights: nulo"
    assert "code 100" in d["bruto"]["video_erro"]


async def test_facebook_reel_recem_publicado_fica_nulo_e_nao_zero():
    """Reel novo volta `data: []` sem erro. É "ainda não tem número": a
    próxima leitura preenche. Zero ali diria que ninguém viu."""
    insights = {"data": [_metrica("post_video_social_actions", {"COMMENT": 1})]}
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(
            return_value=httpx.Response(200, json=_no_video(curtidas=2, comentarios=1))
        )
        router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(
            return_value=httpx.Response(200, json=insights)
        )
        d = await svc.do_facebook(VIDEO_FB, TOKEN_FB)

    assert d.get("views") is None, "sem play no insights ≠ zero play"
    assert d.get("alcance") is None
    assert d["compartilhamentos"] == 0, "o dict de ações veio e não tinha SHARE: é zero medido"
    assert d["curtidas"] == 2 and d["bruto"]["fonte"]["curtidas"] == "video"
    assert "insights_erro" not in d["bruto"], "resposta vazia não é erro"


@pytest.mark.parametrize("status", [400, 200], ids=["http-400", "200-com-error-no-corpo"])
async def test_facebook_reel_apagado_e_removido(status):
    """Os posts de teste que o Eduardo apagou do Facebook (25/09/2026) têm
    que ir pro rodapé "fora do ar", sem alerta. A Meta diz apagado do mesmo
    jeito que no Instagram (100/33) — e às vezes com HTTP 200 e o erro no
    corpo. Código como texto também vale."""
    apagado = {
        "error": {
            "message": f"Unsupported get request. Object with ID '{VIDEO_FB}' does not exist",
            "type": "GraphMethodException",
            "code": "100",
            "error_subcode": 33,
        }
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(
            return_value=httpx.Response(status, json=apagado)
        )
        ins = router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights")
        with pytest.raises(RuntimeError) as e:
            await svc.do_facebook(VIDEO_FB, TOKEN_FB)

    assert svc.foi_removido(str(e.value)), "apagado tem que ser reconhecível pela tela"
    assert "Facebook" in str(e.value)
    assert not ins.called, "apagado não precisa de insights"


async def test_facebook_campo_invalido_nao_e_apagado_e_a_frase_da_meta_fica():
    """100 SEM o 33 é campo/parâmetro inválido — exatamente o erro que todo
    reel do FB dava até 25/09. Não pode se disfarçar de post apagado, senão o
    alerta some pra sempre. E o erro gravado tem que ter a frase da Meta, não
    só "400 Bad Request" — sem o token, mesmo quando ela o ecoa."""
    campo = {
        "error": {
            "message": "(#100) Tried accessing nonexisting field (like_count)",
            "type": "OAuthException",
            "code": 100,
        }
    }
    vencido = {
        "error": {
            "message": f"Error validating access token: {TOKEN_FB} expired",
            "type": "OAuthException",
            "code": 190,
            "error_subcode": 463,
        }
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/{VIDEO_FB}").mock(return_value=httpx.Response(400, json=campo))
        router.get(f"{_base_fb()}/{VIDEO_FB}/video_insights").mock(
            return_value=httpx.Response(400, json=vencido)
        )
        with pytest.raises(RuntimeError) as e:
            await svc.do_facebook(VIDEO_FB, TOKEN_FB)
        _sem_token(router)

    msg = str(e.value)
    assert not svc.foi_removido(msg), "problema de verdade tem que alertar"
    assert "Tried accessing nonexisting field" in msg
    assert "code 190" in msg and "subcode 463" in msg
    assert TOKEN_FB not in msg


async def _cenario_fb(db, monkeypatch, plataforma: str = "facebook"):
    """Postagem de Meta com a conta conectada: o cofre guarda o token da
    Página (`page_access_token`) e o do usuário que o gerou."""
    _, p = await _cenario(db, plataforma=plataforma)

    async def token(session, rede_social_id):
        return {"page_access_token": TOKEN_FB, "access_token": "TOKENDOUSUARIO"}

    monkeypatch.setattr(svc, "_token_de", token)
    return p


@pytest.mark.parametrize(
    ("plataforma", "leitor", "outro"),
    [("facebook", "do_facebook", "do_instagram"), ("instagram", "do_instagram", "do_facebook")],
)
async def test_cada_rede_da_meta_vai_pro_seu_leitor(db, monkeypatch, plataforma, leitor, outro):
    """Mesmo token, API diferente: o Reel do FB é nó de vídeo, e o do IG é
    mídia. Trocar os leitores é o bug de 25/09 de volta."""
    p = await _cenario_fb(db, monkeypatch, plataforma)
    chamou = []

    async def certo(media_id, access_token):
        chamou.append((media_id, access_token))
        return {
            "views": 70,
            "curtidas": 5,
            "comentarios": 2,
            "compartilhamentos": 1,
            "salvamentos": None,
            "bruto": {},
        }

    async def errado(*a, **kw):
        raise AssertionError(f"{plataforma} não pode cair em {outro}")

    monkeypatch.setattr(svc, leitor, certo)
    monkeypatch.setattr(svc, outro, errado)
    assert await svc.coletar(db) == {"total": 1, "ok": 1, "falhou": 0}

    assert chamou == [("123", TOKEN_FB)], "o id da postagem, com o token da Página"
    linha = (await _linhas(db, p.id))[0]
    assert (linha.views, linha.curtidas, linha.comentarios) == (70, 5, 2)
    assert linha.compartilhamentos == 1
    assert linha.salvamentos is None, "não veio ≠ veio zero"
    assert linha.erro is None


async def test_facebook_apagado_grava_removido_na_linha(db, monkeypatch):
    """Ponta a ponta, com a Meta fakada: o reel de teste apagado vira
    `removido:` na linha — rodapé, sem alerta."""
    p = await _cenario_fb(db, monkeypatch)
    apagado = {
        "error": {
            "message": "Object with ID '123' does not exist",
            "code": 100,
            "error_subcode": 33,
        }
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{_base_fb()}/123").mock(return_value=httpx.Response(400, json=apagado))
        r = await svc.coletar(db)

    assert r == {"total": 1, "ok": 0, "falhou": 1}
    linha = (await _linhas(db, p.id))[0]
    assert svc.foi_removido(linha.erro), "o prefixo tem que ficar no começo pra tela ler"


async def test_facebook_recusado_grava_a_frase_da_meta_sem_token(db, monkeypatch):
    """A coluna `erro` aparece no tooltip da tela: tem que dizer o motivo, e
    nunca carregar o token."""
    p = await _cenario_fb(db, monkeypatch)
    vencido = {"error": {"message": f"Invalid OAuth access token {TOKEN_FB}.", "code": 190}}
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_base_fb()}/123").mock(return_value=httpx.Response(400, json=vencido))
        router.get(f"{_base_fb()}/123/video_insights").mock(
            return_value=httpx.Response(400, json=vencido)
        )
        await svc.coletar(db)

    linha = (await _linhas(db, p.id))[0]
    assert "Invalid OAuth access token" in linha.erro and "code 190" in linha.erro
    assert TOKEN_FB not in linha.erro
    assert not svc.foi_removido(linha.erro)


# ═══ desempenho v2 (24/09/2026) ═══════════════════════════════════════════
#
# Eduardo: "tirar aqueles 2 tiktoks da outra conta da uranyx", "esses vídeos
# ainda não aparecem… será que demora?" e "trackear o que cada vídeo deu de
# retorno pra saber o que investir".


# ---------- o autor do TikTok (pista, nunca exclusão) ----------


async def test_tiktok_guarda_autor_no_bruto():
    """O post dizia uma conta e o link dizia outra — foi assim que os dois
    TikToks da conta antiga passaram despercebidos. O autor fica guardado pra
    tela poder avisar."""
    pagina = _pagina_tiktok({"playCount": "10"}, author={"uniqueId": "Uranyx_BR", "id": 6789})
    d = await svc.do_tiktok("https://x", client=_ClienteFalso(pagina))
    assert d["bruto"]["autor"] == {"handle": "uranyx_br", "id": "6789"}
    assert d["bruto"]["playCount"] == "10", "os números crus continuam lá"

    # Layout antigo da página: `author` é o @ e o id vem em `authorId`.
    pagina = _pagina_tiktok({"playCount": "10"}, author="uranyx_brasil", authorId="555")
    d = await svc.do_tiktok("https://x", client=_ClienteFalso(pagina))
    assert d["bruto"]["autor"] == {"handle": "uranyx_brasil", "id": "555"}


async def test_tiktok_sem_autor_nao_quebra_leitura():
    d = await svc.do_tiktok("https://x", client=_ClienteFalso(_pagina_tiktok({"playCount": "7"})))
    assert d["views"] == 7
    assert "autor" not in d["bruto"]


# ---------- falha não apaga leitura boa ----------


def _hoje_as(hora: int) -> datetime:
    """Hoje, na hora dada de Brasília — duas leituras no MESMO dia BRT."""
    return svc._dia(datetime.now(UTC)) + timedelta(hours=hora)


async def _boa(url, *, client):
    return {"views": _boa.n, "curtidas": 4, "bruto": {"playCount": _boa.n}}


async def _ruim(url, *, client):
    raise RuntimeError("o TikTok recusou a página: rate_limit")


async def test_falha_no_mesmo_dia_nao_apaga_leitura_boa(db, monkeypatch):
    """Com leitura de hora em hora, uma falha às 11h por cima da leitura boa
    das 10h apagava os números do dia — e o vídeo aparecia sem número por
    causa de um soluço do TikTok."""
    _, p = await _cenario(db)
    t = _hoje_as(10)
    _boa.n = 100
    monkeypatch.setattr(svc, "do_tiktok", _boa)
    await svc.coletar(db, agora=t)
    monkeypatch.setattr(svc, "do_tiktok", _ruim)
    r = await svc.coletar(db, agora=t + timedelta(hours=1))
    assert r == {"total": 1, "ok": 0, "falhou": 1}

    pid = p.id
    db.expire_all()  # o upsert não passa pelo mapa de identidade da sessão
    [linha] = await _linhas(db, pid)
    assert linha.views == 100, "a leitura boa fica"
    assert linha.curtidas == 4
    assert linha.bruto == {"playCount": 100}
    assert "rate_limit" in linha.erro, "e a falha fica visível"
    assert linha.lido_em == t, "os números são das 10h"
    assert linha.updated_at == t + timedelta(hours=1), "a última TENTATIVA foi às 11h"


async def test_sucesso_depois_de_falha_limpa_erro(db, monkeypatch):
    _, p = await _cenario(db)
    t = _hoje_as(10)
    monkeypatch.setattr(svc, "do_tiktok", _ruim)
    await svc.coletar(db, agora=t)
    _boa.n = 120
    monkeypatch.setattr(svc, "do_tiktok", _boa)
    await svc.coletar(db, agora=t + timedelta(hours=1))

    pid = p.id
    db.expire_all()  # o upsert não passa pelo mapa de identidade da sessão
    [linha] = await _linhas(db, pid)
    assert linha.erro is None
    assert linha.views == 120
    assert linha.lido_em == t + timedelta(hours=1)


async def test_recoleta_no_mesmo_dia_atualiza_lido_em(db, monkeypatch):
    """`updated_at` ficava congelado no primeiro insert do dia (o upsert não
    aplica o `onupdate` do ORM). A idade da leitura depende da hora certa."""
    _, p = await _cenario(db)
    t = _hoje_as(10)
    monkeypatch.setattr(svc, "do_tiktok", _boa)
    _boa.n = 100
    await svc.coletar(db, agora=t)
    _boa.n = 150
    await svc.coletar(db, agora=t + timedelta(hours=1))

    pid = p.id
    db.expire_all()  # o upsert não passa pelo mapa de identidade da sessão
    [linha] = await _linhas(db, pid)
    assert linha.views == 150
    assert linha.lido_em == t + timedelta(hours=1)
    assert linha.updated_at == t + timedelta(hours=1)


# ---------- quem cada rodada lê ----------


async def _outro_post(db, p, sufixo: str, *, publicado_em: datetime, **kw):
    """Mais uma postagem publicada, no mesmo criativo/conta de `p` por padrão."""
    from app.models import MarketingPostagem

    campos = {
        "creative_id": p.creative_id,
        "file_id": p.file_id,
        "rede_social_id": p.rede_social_id,
        "plataforma": p.plataforma,
        "conta": p.conta,
        "status": "publicado",
        "publicado_em": publicado_em,
        "post_url": f"https://www.tiktok.com/@uranyx_brasil/video/{sufixo}",
        "post_external_id": sufixo,
    }
    campos.update(kw)
    q = MarketingPostagem(**campos)
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return q


async def _retrato(db, post, *, lido_em=None, tentado_em=None, erro=None, **numeros):
    """Um retrato gravado à mão, com a hora que o teste precisa."""
    quando = tentado_em or lido_em
    db.add(
        MarketingPostagemMetrica(
            postagem_id=post.id,
            dia=svc._dia(quando),
            plataforma=post.plataforma,
            conta=post.conta,
            lido_em=lido_em,
            updated_at=quando,
            erro=erro,
            **numeros,
        )
    )
    await db.commit()


def _espiao(monkeypatch) -> list[str]:
    lidos: list[str] = []

    async def falso(url, *, client):
        lidos.append(url.rsplit("/", 1)[-1])
        return {"views": 1, "bruto": {}}

    monkeypatch.setattr(svc, "do_tiktok", falso)
    return lidos


async def test_modo_recentes_pega_nunca_lido_e_falha_recente(db, monkeypatch):
    """O passe de hora em hora é o que faz vídeo novo ganhar número em até
    1 hora, em vez de esperar a madrugada (até 16 h). Ele não relê o resto."""
    agora = datetime.now(UTC)
    _, novo = await _cenario(db, publicado_ha=timedelta(minutes=30))  # entra
    await _outro_post(db, novo, "recem", publicado_em=agora - timedelta(minutes=10))  # cedo
    lido = await _outro_post(db, novo, "lido", publicado_em=agora - timedelta(days=3))
    await _retrato(db, lido, lido_em=agora - timedelta(days=1), views=10)
    falha = await _outro_post(db, novo, "falha", publicado_em=agora - timedelta(days=2))
    await _retrato(db, falha, tentado_em=agora - timedelta(hours=2), erro="rate_limit")
    agorinha = await _outro_post(db, novo, "agorinha", publicado_em=agora - timedelta(days=2))
    await _retrato(db, agorinha, tentado_em=agora - timedelta(minutes=20), erro="rate_limit")
    apagado = await _outro_post(db, novo, "apagado", publicado_em=agora - timedelta(days=2))
    await _retrato(
        db, apagado, tentado_em=agora - timedelta(hours=2), erro=f"{svc.REMOVIDO} saiu do ar"
    )

    lidos = _espiao(monkeypatch)
    await svc.coletar(db, agora=agora, modo="recentes")
    assert sorted(lidos) == ["123", "falha"]


async def test_modo_agora_pula_o_tentado_ha_menos_de_10_min(db, monkeypatch):
    agora = datetime.now(UTC)
    _, nunca = await _cenario(db)  # entra
    fresco = await _outro_post(db, nunca, "fresco", publicado_em=agora - timedelta(days=2))
    await _retrato(db, fresco, lido_em=agora - timedelta(minutes=5), views=1)
    velho = await _outro_post(db, nunca, "velho", publicado_em=agora - timedelta(days=2))
    await _retrato(db, velho, lido_em=agora - timedelta(minutes=30), views=1)  # entra
    apagado = await _outro_post(db, nunca, "apagado", publicado_em=agora - timedelta(days=2))
    await _retrato(
        db, apagado, tentado_em=agora - timedelta(minutes=30), erro=f"{svc.REMOVIDO} saiu"
    )
    antigo = await _outro_post(db, nunca, "antigo", publicado_em=agora - timedelta(days=10))
    await _retrato(db, antigo, lido_em=agora - timedelta(minutes=30), views=1)

    lidos = _espiao(monkeypatch)
    await svc.coletar(db, agora=agora, modo="agora")
    assert sorted(lidos) == ["123", "velho"]


async def test_completo_le_o_mais_atrasado_primeiro(db, monkeypatch):
    """O `LIMIT 300` antigo ia do mais novo pro mais velho e deixaria o post
    antigo sem leitura pra sempre. Agora o que não coube é o primeiro da vez
    seguinte."""
    monkeypatch.setitem(svc.CAP, "completo", 2)
    agora = datetime.now(UTC)
    _, a = await _cenario(db)
    await _outro_post(db, a, "b", publicado_em=agora - timedelta(hours=3))
    await _outro_post(db, a, "c", publicado_em=agora - timedelta(hours=4))

    lidos = _espiao(monkeypatch)
    assert (await svc.coletar(db, agora=agora))["total"] == 2
    assert sorted(lidos) == ["123", "b"], "sem leitura nenhuma, vai do mais novo"
    lidos.clear()
    await svc.coletar(db, agora=agora + timedelta(minutes=1))
    assert "c" in lidos, "o que ficou de fora é o primeiro da rodada seguinte"


async def test_fora_do_desempenho_nao_e_coletado(db, monkeypatch):
    _, p = await _cenario(db)
    p.fora_do_desempenho_em = datetime.now(UTC)
    p.fora_do_desempenho_motivo = "vídeo de teste"
    await db.commit()

    lidos = _espiao(monkeypatch)
    for modo in ("completo", "recentes", "agora"):
        assert (await svc.coletar(db, modo=modo))["total"] == 0, modo
    assert lidos == []


# ---------- a tela: aguardando, falha, fora do desempenho ----------


async def test_postagem_sem_leitura_aparece_como_aguardando(client, db, make_user, auth_as):
    """"esses vídeos ainda não aparecem… será que demora?" — a tela partia do
    retrato, e o retrato só nascia na madrugada. Agora o post aparece na hora."""
    await _ve(make_user, auth_as)
    await _cenario(db)

    r = (await client.get(API_M)).json()
    assert r["versao"] == 2
    [post] = r["postagens"]
    assert post["estado"] == "aguardando"
    assert post["acumulado"] == {}
    assert post["marco_motivo"] == "aguardando"
    assert r["resumo"]["videos"]["aguardando"] == 1
    assert r["resumo"]["videos"]["no_ar"] == 0
    [rede] = r["resumo"]["redes"]
    assert rede["aguardando"] == 1 and rede["videos"] == 0
    assert rede["acumulado"] == {} and rede["no_periodo"] == {}, "não entra em soma nenhuma"
    assert r["sem_video_no_ar"] == [], "a marca tem vídeo no ar, só não lido ainda"
    marca = r["marcas"][0]
    assert marca["posts"] == 0 and marca["aguardando"] == 1
    assert marca["plataformas"][0]["videos"][0]["estado"] == "aguardando"


async def test_falha_de_hoje_mostra_a_ultima_leitura_boa_e_nao_fica_negativa(
    client, db, make_user, auth_as, monkeypatch
):
    """Antes, a falha de hoje virava uma linha de números NULOS, e o "no
    período" do vídeo sumia ou ia pra baixo. Agora ele fica com a última
    leitura boa, com o aviso de que a de hoje falhou."""
    await _ve(make_user, auth_as)
    _, p = await _cenario(db, publicado_ha=timedelta(days=40))
    monkeypatch.setattr(svc, "do_tiktok", _boa)
    _boa.n = 80
    await svc.coletar(db, agora=datetime.now(UTC) - timedelta(days=2))
    _boa.n = 100
    await svc.coletar(db, agora=datetime.now(UTC) - timedelta(days=1))
    monkeypatch.setattr(svc, "do_tiktok", _ruim)
    await svc.coletar(db)

    r = (await client.get(API_M)).json()
    [post] = r["postagens"]
    assert post["estado"] == "falhou"
    assert post["acumulado"]["views"] == 100, "a última leitura boa"
    assert post["lido_em"] is not None and "rate_limit" in post["erro"]
    [rede] = r["resumo"]["redes"]
    assert rede["com_falha"] == 1 and "rate_limit" in rede["erro"]
    assert rede["no_periodo"]["views"] == 20, "100 - 80, nunca negativo"
    assert rede["serie"][-1] is None, "hoje não tem leitura boa: sem barra, não zero"
    assert r["marcas"][0]["acumulado"]["views"] == 100


async def _criativo(db, marca, *, equipe=None, modelo="video 15s"):
    from app.models import MarketingCreative, MarketingCreativeFile

    c = MarketingCreative(
        modelo=modelo, marca=marca.slug, marca_id=marca.id, aprovado=True, equipe=equipe
    )
    db.add(c)
    await db.flush()
    f = MarketingCreativeFile(
        creative_id=c.id, file_name="v.mp4", file_mime="video/mp4", file_rel=f"x/{c.id}.mp4"
    )
    db.add(f)
    await db.commit()
    return c, f


async def test_fora_do_desempenho_some_de_tudo_e_aparece_no_rodape(
    client, db, make_user, auth_as
):
    from app.models import Marca

    await _ve(make_user, auth_as)
    m, p = await _cenario(db)
    pub = datetime.now(UTC) - timedelta(days=3)
    p.publicado_em = pub
    await db.commit()
    await _retrato(db, p, lido_em=pub + timedelta(hours=24), views=100)
    m = (await db.execute(select(Marca).where(Marca.id == m.id))).scalar_one()
    c2, f2 = await _criativo(db, m)
    fora = await _outro_post(
        db, p, "999", publicado_em=pub, creative_id=c2.id, file_id=f2.id
    )
    await _retrato(db, fora, lido_em=pub + timedelta(hours=24), views=500)
    fora.fora_do_desempenho_em = datetime.now(UTC)
    fora.fora_do_desempenho_motivo = "conta antiga"
    await db.commit()

    r = (await client.get(API_M, params={"marco": 1})).json()
    assert [x["postagem_id"] for x in r["postagens"]] == [str(p.id)]
    assert [x["creative_id"] for x in r["criativos"]] == [str(p.creative_id)]
    for dim in ("produto", "formato", "agencia", "roteiro", "horario"):
        assert sum(g["total"] for g in r["grupos"][dim]) == 1, dim
    assert r["resumo"]["videos"]["fora_do_desempenho"] == 1
    assert r["resumo"]["videos"]["no_ar"] == 1
    assert r["resumo"]["redes"][0]["acumulado"]["views"] == 100
    assert r["postagens"][0]["base"]["n"] == 0, "nem na base da conta ele entra"
    videos = r["marcas"][0]["plataformas"][0]["videos"]
    assert [v["postagem_id"] for v in videos] == [str(p.id)]
    [rodape] = r["fora_do_desempenho"]
    assert rodape["postagem_id"] == str(fora.id)
    assert rodape["motivo"] == "conta antiga"
    assert rodape["plataforma"] == "tiktok" and rodape["em"] is not None


def _migration_0317():
    caminho = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0317_desempenho_videos.py"
    )
    spec = importlib.util.spec_from_file_location("m0317", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_sql_conta_antiga_tira_so_os_tiktoks_de_uranyx_brasil(db):
    """Casa pelo @ do LINK, nunca pela coluna `conta`: o canal do YouTube da
    Uranyx foi renomeado e o post dele ainda diz `uranyx_brasil` — é da marca e
    tem que continuar contando. Regex e não LIKE: no LIKE o `_` é curinga."""
    from tests.conftest import TEST_SCHEMA

    agora = datetime.now(UTC)
    _, antiga = await _cenario(db)  # https://www.tiktok.com/@uranyx_brasil/video/123
    atual = await _outro_post(
        db, antiga, "456", publicado_em=agora,
        post_url="https://www.tiktok.com/@uranyx_br/video/456", conta="uranyx_br",
    )
    youtube = await _outro_post(
        db, antiga, "jkt9LLoL9vw", publicado_em=agora, plataforma="youtube",
        post_url="https://youtu.be/jkt9LLoL9vw", conta="uranyx_brasil",
    )
    parecida = await _outro_post(
        db, antiga, "789", publicado_em=agora,
        post_url="https://www.tiktok.com/@uranyxAbrasil/video/789",
    )

    sql = _migration_0317().sql_conta_antiga(TEST_SCHEMA)
    r = await db.execute(text(sql))
    await db.commit()
    assert r.rowcount == 1

    for x in (antiga, atual, youtube, parecida):
        await db.refresh(x)
    assert antiga.fora_do_desempenho_em is not None
    assert "@uranyx_brasil" in antiga.fora_do_desempenho_motivo
    assert atual.fora_do_desempenho_em is None
    assert youtube.fora_do_desempenho_em is None, "o YouTube renomeado continua contando"
    assert parecida.fora_do_desempenho_em is None, "o _ não é curinga"

    r = await db.execute(text(sql))
    await db.commit()
    assert r.rowcount == 0, "rodar de novo não mexe em quem já saiu"


async def test_youtube_renomeado_continua_contando(client, db, make_user, auth_as):
    """A conta hoje é `uranyx_br`; o post foi publicado quando ela se chamava
    `uranyx_brasil`. É a mesma conta (o mesmo cadastro), e divide a base."""
    from app.models import RedeSocial

    await _ve(make_user, auth_as)
    _, p = await _cenario(db, plataforma="youtube")
    rede = (
        await db.execute(select(RedeSocial).where(RedeSocial.id == p.rede_social_id))
    ).scalar_one()
    rede.conta = "uranyx_br"
    pub = datetime.now(UTC) - timedelta(days=10)
    p.publicado_em = pub
    p.post_url = "https://youtu.be/jkt9LLoL9vw"
    await db.commit()
    await _retrato(db, p, lido_em=pub + timedelta(hours=72), views=200)
    for i in range(4):
        q = await _outro_post(
            db, p, f"yt{i}", publicado_em=pub, conta="uranyx_br",
            post_url=f"https://youtu.be/yt{i}",
        )
        await _retrato(db, q, lido_em=pub + timedelta(hours=72), views=100)
    await db.execute(text(_migration_0317().sql_conta_antiga("davinci_test")))
    await db.commit()

    r = (await client.get(API_M)).json()
    assert r["fora_do_desempenho"] == []
    post = next(x for x in r["postagens"] if x["postagem_id"] == str(p.id))
    assert post["estado"] == "ok"
    assert post["conta"] == "uranyx_br", "a tela mostra o nome atual"
    assert post["base"] == {"mediana": 100, "n": 4}
    assert post["indice_views"] == 2.0
    outros = [x for x in r["postagens"] if x["postagem_id"] != str(p.id)]
    assert all(x["base"]["n"] == 4 for x in outros), "e ele entra na base dos outros"


# ---------- tirar do desempenho pela tela ----------


async def test_patch_desempenho_exige_edit_e_motivo_e_volta(client, db, make_user, auth_as):
    """Da próxima vez é um clique, não um UPDATE no banco — e o motivo é
    obrigatório: daqui a um mês ninguém lembra por que o vídeo sumiu."""
    so_ve = await make_user(permissions={"marketing_criativos": {"view": True}})
    edita = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    _, p = await _cenario(db)
    url = f"{API_M}/postagens/{p.id}/desempenho"

    auth_as(so_ve)
    r = await client.patch(url, json={"contar": False, "motivo": "conta antiga"})
    assert r.status_code == 403

    auth_as(edita)
    r = await client.patch(url, json={"contar": False})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "motivo_obrigatorio"
    r = await client.patch(url, json={"contar": False, "motivo": "  x "})
    assert r.status_code == 422, "motivo de 1 letra não explica nada"

    r = await client.patch(url, json={"contar": False, "motivo": " conta antiga "})
    assert r.status_code == 200, r.text
    assert r.json()["motivo"] == "conta antiga" and r.json()["em"] is not None
    tela = (await client.get(API_M)).json()
    assert tela["postagens"] == []
    assert [x["postagem_id"] for x in tela["fora_do_desempenho"]] == [str(p.id)]
    await db.refresh(p)
    assert p.fora_do_desempenho_por == edita.id

    r = await client.patch(url, json={"contar": True})
    assert r.status_code == 200 and r.json()["em"] is None
    tela = (await client.get(API_M)).json()
    assert [x["postagem_id"] for x in tela["postagens"]] == [str(p.id)]
    assert tela["fora_do_desempenho"] == []

    p.status = "falhou"
    await db.commit()
    r = await client.patch(url, json={"contar": False, "motivo": "teste"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "nao_publicada"
    r = await client.patch(
        f"{API_M}/postagens/00000000-0000-0000-0000-000000000000/desempenho",
        json={"contar": True},
    )
    assert r.status_code == 404


async def _equipe(db, p, equipe: str) -> None:
    from app.models import MarketingCreative

    c = (
        await db.execute(select(MarketingCreative).where(MarketingCreative.id == p.creative_id))
    ).scalar_one()
    c.equipe = equipe
    await db.commit()


async def test_patch_desempenho_respeita_equipe(client, db, make_user, auth_as):
    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    u.marketing_teams = ["Outra Agência"]
    await db.commit()
    _, p = await _cenario(db)
    await _equipe(db, p, "Bill Gates")

    auth_as(u)
    r = await client.patch(
        f"{API_M}/postagens/{p.id}/desempenho", json={"contar": False, "motivo": "teste"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "fora_da_sua_equipe"


async def test_equipe_restrita_so_ve_os_proprios_criativos(client, db, make_user, auth_as):
    """Mesmo escopo de Criativos: agência vê o que é dela. Até 23/09 esta tela
    não tinha escopo nenhum."""
    from app.models import Marca

    m, p = await _cenario(db)
    await _equipe(db, p, "Bill Gates")
    m = (await db.execute(select(Marca).where(Marca.id == m.id))).scalar_one()
    c2, f2 = await _criativo(db, m, equipe="Outra")
    outro = await _outro_post(
        db, p, "999", publicado_em=datetime.now(UTC), creative_id=c2.id, file_id=f2.id
    )

    u = await make_user(permissions={"marketing_criativos": {"view": True}})
    u.marketing_teams = ["bill gates"]
    await db.commit()
    auth_as(u)
    r = (await client.get(API_M)).json()
    assert [x["postagem_id"] for x in r["postagens"]] == [str(p.id)]
    assert [x["creative_id"] for x in r["criativos"]] == [str(p.creative_id)]
    videos = [v for pl in r["marcas"][0]["plataformas"] for v in pl["videos"]]
    assert [v["postagem_id"] for v in videos] == [str(p.id)]

    todos = await make_user(permissions={"marketing_criativos": {"view": True}})
    auth_as(todos)
    r = (await client.get(API_M)).json()
    assert {x["postagem_id"] for x in r["postagens"]} == {str(p.id), str(outro.id)}


async def test_filtro_marca_mantem_marcas_disponiveis(client, db, make_user, auth_as):
    """Filtrar a marca não pode sumir com as outras dos chips — senão não dá
    pra voltar."""
    from app.models import Marca

    await _ve(make_user, auth_as)
    m1, p = await _cenario(db)
    m2 = Marca(nome="Charlots", slug="charlots")
    db.add(m2)
    await db.commit()
    c2, f2 = await _criativo(db, m2)
    await _outro_post(
        db, p, "999", publicado_em=datetime.now(UTC), creative_id=c2.id, file_id=f2.id
    )

    r = (await client.get(API_M, params={"marca_id": str(m1.id)})).json()
    assert r["marca_id"] == str(m1.id)
    assert [x["postagem_id"] for x in r["postagens"]] == [str(p.id)]
    assert [x["marca"] for x in r["marcas"]] == ["Uranyx"]
    assert [x["nome"] for x in r["marcas_disponiveis"]] == ["Charlots", "Uranyx"]


async def test_marco_e_indice_no_endpoint(client, db, make_user, auth_as):
    """5 vídeos na mesma conta, lidos com 60 h e 84 h: o D+3 é o meio do
    caminho, e o índice é contra a mediana dos OUTROS 4."""
    await _ve(make_user, auth_as)
    _, p = await _cenario(db)
    pub = datetime.now(UTC) - timedelta(days=5)
    posts = [p]
    for i in range(1, 5):
        posts.append(await _outro_post(db, p, f"v{i}", publicado_em=pub))
    p.publicado_em = pub
    await db.commit()
    pares = [(100, 300), (200, 400), (300, 500), (400, 600), (900, 1300)]
    for post, (v60, v84) in zip(posts, pares, strict=True):
        await _retrato(db, post, lido_em=pub + timedelta(hours=60), views=v60)
        await _retrato(db, post, lido_em=pub + timedelta(hours=84), views=v84)

    r = (await client.get(API_M)).json()
    assert r["marco"] == 3
    por = {x["postagem_id"]: x for x in r["postagens"]}
    assert [por[str(x.id)]["views_marco"] for x in posts] == [200, 300, 400, 500, 1100]
    top = por[str(posts[4].id)]
    assert top["base"] == {"mediana": 350, "n": 4}
    assert top["indice_views"] == round(1100 / 350, 2)
    assert por[str(posts[0].id)]["indice_views"] == round(200 / 450, 2)
    assert r["criativos"][0]["n_indices"] == 5

    assert (await client.get(API_M, params={"marco": 2})).status_code == 422


# ---------- "Atualizar agora" e a trava da rodada ----------


class _PoolFalso:
    def __init__(self, erro: Exception | None = None) -> None:
        self.chamadas: list[tuple] = []
        self.erro = erro

    async def enqueue_job(self, nome, *args, **kwargs):
        if self.erro:
            raise self.erro
        self.chamadas.append((nome, args, kwargs))
        return object()


async def test_atualizar_agora_enfileira_e_segura_10_min(
    client, db, make_user, auth_as, monkeypatch, redis_falso
):
    """Sem `_job_id` fixo: o worker UI guarda o resultado por 1 hora, e com id
    fixo todo clique dessa hora sumiria calado. Quem segura é a trava."""
    from app.routers import marketing_metricas as rota

    pool = _PoolFalso()

    async def _pool():
        return pool

    monkeypatch.setattr(rota, "get_arq_ui_pool", _pool)
    await _ve(make_user, auth_as)

    r = await client.post(f"{API_M}/atualizar")
    assert r.status_code == 202, r.text
    corpo = r.json()
    assert corpo["enfileirado"] is True and corpo["pedido_em"] and corpo["pode_atualizar_em"]
    assert pool.chamadas == [("marketing_postagens_metricas_agora", (), {})]

    r = await client.post(f"{API_M}/atualizar")
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "atualizacao_recente"
    assert r.json()["detail"]["pode_atualizar_em"]
    assert len(pool.chamadas) == 1

    tela = (await client.get(API_M)).json()
    assert tela["coleta"]["pode_atualizar_em"] is not None

    # Fila fora: 503, e a trava sai — senão o botão ficaria 10 min travado
    # por um pedido que nunca existiu.
    redis_falso.d.clear()
    pool.erro = RuntimeError("redis caiu")
    r = await client.post(f"{API_M}/atualizar")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "fila_indisponivel"
    assert not await redis_falso.exists(svc.CHAVE_AGORA)

    auth_as(await make_user(permissions={}))
    assert (await client.post(f"{API_M}/atualizar")).status_code == 403


async def test_rodada_pula_quando_outra_esta_rodando(monkeypatch):
    """Três portas pra mesma leitura (noite, hora em hora, botão): duas juntas
    leriam o mesmo TikTok duas vezes do mesmo IP. O passe curto e o botão
    desistem (o próximo resolve); a da noite tenta de novo em 5 min."""
    from arq import Retry

    from app import worker

    falso = _RedisFalso()
    monkeypatch.setattr(worker, "redis", falso)
    monkeypatch.setattr(worker._settings, "enable_marketing", True)
    chamou: list[str] = []
    dias: dict[str, datetime | None] = {}

    async def coletar_falso(session, *, agora=None, modo="completo", dia=None):
        chamou.append(modo)
        dias[modo] = dia
        return {"total": 1, "ok": 1, "falhou": 0}

    monkeypatch.setattr(svc, "coletar", coletar_falso)

    await falso.set(svc.CHAVE_RODANDO, "completo", ex=1800)
    await worker.marketing_postagens_metricas_recentes({})
    assert chamou == []
    with pytest.raises(Retry):
        await worker.marketing_postagens_metricas({})
    assert chamou == []
    assert await falso.exists(svc.CHAVE_RODANDO), "a trava de quem está rodando fica"

    await falso.delete(svc.CHAVE_RODANDO)
    await worker.marketing_postagens_metricas_agora({})
    assert chamou == ["agora"]
    assert not await falso.exists(svc.CHAVE_RODANDO), "terminou, soltou"
    ultima = json.loads(await falso.get(svc.CHAVE_ULTIMA_RODADA))
    assert ultima["modo"] == "agora" and ultima["ok"] == 1 and ultima["fim"]
    assert dias["agora"] is None, "o botão grava no dia de hoje"

    # A da noite grava no dia DA NOITE (a que começou depois da meia-noite
    # ainda é o fim de ontem).
    antes = svc.dia_da_noite(datetime.now(UTC))
    await worker.marketing_postagens_metricas({})
    depois = svc.dia_da_noite(datetime.now(UTC))
    assert dias["completo"] in (antes, depois)


async def test_noite_que_comeca_depois_da_meia_noite_e_da_noite_de_ontem():
    """A da noite é marcada 13 min antes de o dia virar, numa fila que
    atrasa. Começando 00:05, o retrato do fim de 27/09 ia pro dia 28 — e a
    leitura das 23:47 do dia 28 sobrescrevia: o fim do dia 27 se perdia."""
    assert svc.dia_da_noite(datetime(2026, 9, 28, 2, 47, tzinfo=UTC)).date().isoformat() == (
        "2026-09-27"
    ), "no horário: 23:47 de 27/09"
    assert svc.dia_da_noite(datetime(2026, 9, 28, 3, 5, tzinfo=UTC)).date().isoformat() == (
        "2026-09-27"
    ), "00:05 de 28/09 ainda é a noite de 27/09, atrasada"
    assert svc.dia_da_noite(datetime(2026, 9, 28, 9, 0, tzinfo=UTC)).date().isoformat() == (
        "2026-09-28"
    ), "06:00 de Brasília já é outro dia"


async def test_noite_atrasada_grava_no_dia_da_noite(db, monkeypatch):
    """O dia do retrato vem do worker; a hora da leitura continua a real (é
    ela que diz a idade do vídeo)."""
    _, p = await _cenario(db, publicado_ha=timedelta(days=40))
    _boa.n = 300
    monkeypatch.setattr(svc, "do_tiktok", _boa)
    t = _hoje_as(0) + timedelta(minutes=5)  # 00:05 de Brasília
    await svc.coletar(db, agora=t, dia=svc.dia_da_noite(t))

    pid = p.id
    db.expire_all()
    [linha] = await _linhas(db, pid)
    ontem = (t.astimezone(svc.BRT) - timedelta(days=1)).date()
    assert linha.dia.astimezone(svc.BRT).date() == ontem, "o fim de ontem fica em ontem"
    assert linha.lido_em == t


async def test_leitura_da_noite_e_de_hora_em_hora_no_minuto_47():
    """23:47 BRT faz o retrato do dia D ser o número do fim de D; o passe de
    hora em hora cobre as outras 23 horas no mesmo minuto."""
    from app import worker

    por_nome = {c.coroutine.__name__: c for c in worker.WorkerSettings.cron_jobs}
    noite = por_nome["marketing_postagens_metricas"]
    assert noite.hour == {svc.HORA_NOTURNA_UTC} and noite.minute == {svc.MINUTO_COLETA}
    hora = por_nome["marketing_postagens_metricas_recentes"]
    assert hora.hour == set(range(24)) - {svc.HORA_NOTURNA_UTC}
    assert hora.minute == {svc.MINUTO_COLETA}
    ui = {getattr(f, "name", None): f for f in worker.WorkerSettingsUI.functions}
    assert ui["marketing_postagens_metricas_agora"].timeout_s == 600, "o 60s da fila UI mataria"


async def test_noite_travada_tenta_de_novo_ate_a_trava_vencer():
    """Travada, a da noite levanta Retry(defer=300). O `cron()` do arq tem
    max_tries=1 por padrão — e aí o Retry morria sem rodar, e o dia ficava
    sem o retrato do fim. As retentativas têm que cobrir os 30 min da trava
    (a órfã, de worker morto no meio da rodada, só sai pelo TTL)."""
    from app import worker

    por_nome = {c.coroutine.__name__: c for c in worker.WorkerSettings.cron_jobs}
    noite = por_nome["marketing_postagens_metricas"]
    assert noite.max_tries is not None and (noite.max_tries - 1) * 300 >= 1800
