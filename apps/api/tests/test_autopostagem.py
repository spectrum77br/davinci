"""O robô que publica sozinho (services/marketing/autopostagem.py).

Eduardo, 24/09/2026: "tem posts por dia e intervalo, mas onde boto que horas
ele começa?". A pergunta expôs que "Publicação automática" prometia mais do que
entregava — os dois números eram guarda-corpos, e quem escolhia vídeo e hora
era sempre uma pessoa.

O que estes testes defendem:

  - A GRADE é um horário de trabalho, não uma cota. 18h + 60min + teto 2 dá
    18h e 19h — e o das 19h não sai às 18h só porque a vaga existe.
  - LIGAR É DELIBERADO. Exige interruptor E hora. O interruptor sozinho já
    significava outra coisa ("pode executar o que foi agendado à mão"), e há
    conta com ele ligado por esse motivo: tratá-las como autorizadas faria o
    robô publicar sozinho, no dia do deploy, em conta que ninguém pediu.
  - NÃO REPETE. O vídeo que já saiu naquela conta não volta.
  - AS GUARDAS DO HUMANO VALEM PRO ROBÔ. Ele passa pelo mesmo `agendar()`.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import get_settings
from app.models import (
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingPostagem,
    RedeSocial,
)
from app.services.marketing import autopostagem as svc
from app.services.marketing.postagens import BRT

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _uploads(tmp_path, monkeypatch):
    """`settings.uploads_dir` vira o tmp_path do teste.

    A guarda `arquivo_sumiu` lê o vídeo do DISCO do servidor. Sem apontar o
    diretório pra cá, todo criativo seria recusado — e a recusa esconderia o
    que o teste quer medir.
    """
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))


def _rede(**kw) -> RedeSocial:
    # TikTok de propósito: é a plataforma onde a credencial é a sessão do
    # navegador (o executor local), não um token — o que deixa o teste medir o
    # AGENDAMENTO sem precisar montar token falso pra Meta.
    base = {"plataforma": "tiktok", "conta": "x", "ativo": True,
            "adspower_user_id": "k1h9eqn7",
            "postagem_auto": True, "postagem_hora_inicio": 18,
            "postagem_max_dia": 2, "postagem_intervalo_min": 60}
    return RedeSocial(**{**base, **kw})


# ---------- a grade do dia ----------


def test_grade_18h_intervalo_60_teto_2_da_18h_e_19h():
    """O exemplo que o Eduardo deu, ao pé da letra."""
    agora = datetime(2026, 9, 24, 23, 0, tzinfo=UTC)  # 20h em BRT
    g = svc.horarios_do_dia(_rede(), get_settings(), agora=agora)
    assert [h.strftime("%H:%M") for h in g] == ["18:00", "19:00"]


def test_grade_nao_atravessa_a_meia_noite():
    """Post que não coube hoje não vira madrugada de amanhã sozinho — silêncio
    é melhor que vídeo da marca às 3h."""
    agora = datetime(2026, 9, 24, 23, 0, tzinfo=UTC)
    g = svc.horarios_do_dia(
        _rede(postagem_hora_inicio=22, postagem_max_dia=5, postagem_intervalo_min=90),
        get_settings(), agora=agora,
    )
    assert [h.strftime("%H:%M") for h in g] == ["22:00", "23:30"], "o terceiro seria 01:00"


def test_teto_zero_nao_gera_horario_nenhum():
    g = svc.horarios_do_dia(_rede(postagem_max_dia=0), get_settings(),
                            agora=datetime(2026, 9, 24, 23, 0, tzinfo=UTC))
    assert g == []


# ---------- quem está autorizado ----------


async def test_so_conta_com_interruptor_E_hora_entra(db):
    """O interruptor sozinho NÃO basta: até hoje ele significava 'pode executar
    o que foi agendado à mão', e há conta ligada por esse motivo."""
    m = Marca(nome="Uranyx", slug="uranyx")
    db.add(m)
    await db.flush()
    db.add_all([
        _rede(marca_id=m.id, conta="completa"),
        _rede(marca_id=m.id, conta="sem_hora", postagem_hora_inicio=None),
        _rede(marca_id=m.id, conta="sem_auto", postagem_auto=False),
        _rede(marca_id=m.id, conta="inativa", ativo=False),
    ])
    await db.commit()

    contas = [r.conta for r in await svc.contas_ligadas(db)]
    assert contas == ["completa"], "ligar tem que ser ato deliberado, com hora escolhida"


# ---------- a escolha do vídeo ----------


async def _legenda(db, marca):
    """Uma variação de legenda pra marca.

    Obrigatório pro robô desde 24/09/2026: a guarda `sem_legenda` recusa
    publicação autônoma quando a cascata não acha texto. Reel sem legenda é
    criativo queimado, e no robô não tem ninguém olhando pra perceber.
    """
    from app.models import MarketingLegendaModelo

    db.add(MarketingLegendaModelo(marca_id=marca.id, texto="Legenda da {{ marca }}"))
    await db.commit()


async def _cenario(db, *, com_legenda: bool = True):
    m = Marca(nome="Uranyx", slug="uranyx")
    db.add(m)
    await db.flush()
    r = _rede(marca_id=m.id, conta="uranyx_br")
    db.add(r)
    await db.commit()
    if com_legenda:
        await _legenda(db, m)
    return m, r


async def _criativo(
    db,
    marca,
    *,
    nome: str,
    quando: datetime,
    aprovado: bool = True,
    sha: str | None = None,
    fila: int | None = None,
):
    c = MarketingCreative(
        modelo=nome, marca=marca.slug, marca_id=marca.id, aprovado=aprovado, fila_posicao=fila
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
        sha256=sha,
    )
    f.created_at = quando
    db.add(f)
    await db.commit()
    return c, f


async def test_escolhe_o_mais_recente_aprovado(db):
    """'Tacamos do mais recente' — escolha do Eduardo."""
    m, r = await _cenario(db)
    agora = datetime.now(UTC)
    await _criativo(db, m, nome="antigo", quando=agora - timedelta(days=5))
    _, novo_f = await _criativo(db, m, nome="novo", quando=agora - timedelta(hours=1))
    await _criativo(db, m, nome="reprovado", quando=agora, aprovado=False)

    achado = await svc.proximo_criativo(db, r)
    assert achado is not None
    assert achado[1].id == novo_f.id, "o mais recente, e reprovado não entra"


async def test_video_que_ja_saiu_na_conta_nao_volta(db):
    m, r = await _cenario(db)
    agora = datetime.now(UTC)
    c_ant, f_ant = await _criativo(db, m, nome="antigo", quando=agora - timedelta(days=5))
    c_nov, f_nov = await _criativo(db, m, nome="novo", quando=agora - timedelta(hours=1))

    db.add(MarketingPostagem(
        creative_id=c_nov.id, file_id=f_nov.id, rede_social_id=r.id,
        plataforma=r.plataforma, conta=r.conta, status="publicado",
        publicado_em=agora - timedelta(minutes=30),
    ))
    await db.commit()

    achado = await svc.proximo_criativo(db, r)
    assert achado is not None and achado[1].id == f_ant.id, "pula o que já saiu"


async def test_sem_criativo_novo_devolve_nada(db):
    _, r = await _cenario(db)
    assert await svc.proximo_criativo(db, r) is None


# ---------- a rodada ----------


async def test_nao_adianta_o_post_das_19h_para_as_18h(db, monkeypatch):
    """A grade é horário de trabalho, não cota pra esvaziar de uma vez."""
    m, r = await _cenario(db)
    await _criativo(db, m, nome="a", quando=datetime.now(UTC) - timedelta(days=1))
    await _criativo(db, m, nome="b", quando=datetime.now(UTC))

    # 18h30 em BRT: só o horário das 18h já chegou.
    agora = datetime.now(UTC).astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    saida = await svc.rodada(db, agora=agora)
    assert saida["agendadas"] == 1, "só o das 18h; o das 19h espera a hora dele"


async def test_antes_da_hora_de_inicio_nao_agenda_nada(db):
    m, r = await _cenario(db)
    await _criativo(db, m, nome="a", quando=datetime.now(UTC))
    agora = datetime.now(UTC).astimezone(BRT).replace(hour=9, minute=0).astimezone(UTC)
    assert (await svc.rodada(db, agora=agora))["agendadas"] == 0


async def test_rodar_duas_vezes_no_mesmo_horario_nao_duplica(db):
    """O cron roda de hora em hora. A segunda passada não pode agendar de novo
    o que a primeira já pôs de pé."""
    m, r = await _cenario(db)
    await _criativo(db, m, nome="a", quando=datetime.now(UTC) - timedelta(days=1))
    await _criativo(db, m, nome="b", quando=datetime.now(UTC))
    agora = datetime.now(UTC).astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)

    primeira = await svc.rodada(db, agora=agora)
    segunda = await svc.rodada(db, agora=agora)
    assert primeira["agendadas"] == 1
    assert segunda["agendadas"] == 0, "a vaga das 18h já está ocupada"


async def test_sem_legenda_o_robo_RECUSA_em_vez_de_publicar_mudo(db):
    """A guarda mais importante do robô, e a que quase não existiu.

    `agendar()` deduzia "é robô?" pela presença de hora marcada. A rodada
    publica AGORA, sem hora — então o robô entrava como clique humano e a
    recusa por `sem_legenda` (que vale só pro robô, porque no clique tem gente
    olhando) nunca disparava. Marca sem biblioteca de legenda teria Reel mudo
    no ar, de hora em hora, sem ninguém perceber.

    Achado por auditoria adversarial ANTES de subir.
    """
    m, r = await _cenario(db, com_legenda=False)
    await _criativo(db, m, nome="a", quando=datetime.now(UTC))
    agora = datetime.now(UTC).astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)

    saida = await svc.rodada(db, agora=agora)
    assert saida["agendadas"] == 0
    assert saida["recusadas"] == 1, "recusa seca é melhor que post mudo"


async def test_a_postagem_do_robo_nasce_marcada_como_dele(db):
    """`origem` não é só rastreabilidade: o publicador decide por ela se
    reexamina as guardas do robô antes de publicar. Nascendo como `manual`,
    desligar o interruptor não segurava o que já estava na fila."""
    m, r = await _cenario(db)
    await _criativo(db, m, nome="a", quando=datetime.now(UTC))
    agora = datetime.now(UTC).astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    await svc.rodada(db, agora=agora)

    from sqlalchemy import select as _sel

    linha = (await db.execute(_sel(MarketingPostagem))).scalars().first()
    assert linha.origem == "robo", "senão desligar não é botão de pânico"


def test_no_instagram_produto_vai_pra_grade_e_historia_so_pra_aba_reels():
    """Eduardo, 03/11/2026: vídeo de produto também no feed; história só na
    aba Reels. Nas outras redes a opção não existe e não deve viajar."""
    produto = MarketingCreative(modelo="p", tipo_conteudo="produto")
    historia = MarketingCreative(modelo="h", tipo_conteudo="historia")
    ig = _rede(plataforma="instagram")
    assert svc._opcoes_do_robo(ig, produto) == {"share_to_feed": True}
    assert svc._opcoes_do_robo(ig, historia) == {"share_to_feed": False}
    assert svc._opcoes_do_robo(_rede(plataforma=" Instagram "), historia) == {
        "share_to_feed": False
    }
    for outra in ("tiktok", "facebook", "youtube", "shopee"):
        assert svc._opcoes_do_robo(_rede(plataforma=outra), produto) == {}, outra


async def test_postagem_do_robo_fora_do_instagram_sai_sem_share_to_feed(db):
    m, r = await _cenario(db)
    await _criativo(db, m, nome="a", quando=datetime.now(UTC))
    agora = datetime.now(UTC).astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    await svc.rodada(db, agora=agora)

    from sqlalchemy import select as _sel

    linha = (await db.execute(_sel(MarketingPostagem))).scalars().first()
    assert "share_to_feed" not in (linha.opcoes or {})


async def test_cancelar_na_tela_segura_o_robo(db):
    """O "não" da pessoa tem que valer. Antes, cancelar às 18h50 e o robô
    reagendava o MESMO vídeo às 19h02 — o cancelamento virava adiamento."""
    m, r = await _cenario(db)
    await _criativo(db, m, nome="unico", quando=datetime.now(UTC))
    agora = datetime.now(UTC).astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    assert (await svc.rodada(db, agora=agora))["agendadas"] == 1

    from sqlalchemy import select as _sel

    linha = (await db.execute(_sel(MarketingPostagem))).scalars().first()
    linha.status = "cancelado"
    await db.commit()

    depois = datetime.now(UTC).astimezone(BRT).replace(hour=19, minute=30).astimezone(UTC)
    assert (await svc.rodada(db, agora=depois))["agendadas"] == 0, "cancelado é não"


async def test_video_que_falhou_nao_volta_sozinho(db):
    """Falha pode ter deixado container na Meta — o vídeo talvez TENHA saído.
    É o estado em que o retentar humano se recusa a agir, porque republicar ali
    duplica. O robô não pode entrar por uma porta onde a guarda não existe."""
    m, r = await _cenario(db)
    await _criativo(db, m, nome="unico", quando=datetime.now(UTC))
    agora = datetime.now(UTC).astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    await svc.rodada(db, agora=agora)

    from sqlalchemy import select as _sel

    linha = (await db.execute(_sel(MarketingPostagem))).scalars().first()
    linha.status = "falhou"
    await db.commit()

    depois = datetime.now(UTC).astimezone(BRT).replace(hour=19, minute=30).astimezone(UTC)
    assert (await svc.rodada(db, agora=depois))["agendadas"] == 0, "retomar falha é do humano"


async def test_so_video_entra_na_fila(db):
    """O upload aceita imagem e PDF. Sem filtro, o robô pegaria o JPG mais
    recente e tentaria publicá-lo como Reel — o modal manual pré-seleciona
    vídeo, mas aqui não há ninguém pra pré-selecionar."""
    from app.models import MarketingCreativeFile

    m, r = await _cenario(db)
    c, _f = await _criativo(db, m, nome="video", quando=datetime.now(UTC) - timedelta(days=1))
    # Uma arte MAIS RECENTE que o vídeo: sem o filtro, ela ganharia a escolha.
    db.add(MarketingCreativeFile(
        creative_id=c.id, file_name="arte.jpg", file_mime="image/jpeg",
        file_rel=f"x/{c.id}-arte.jpg",
    ))
    await db.commit()

    achado = await svc.proximo_criativo(db, r)
    assert achado is not None
    assert achado[1].file_mime.startswith("video/"), "JPG não é Reel"


async def test_hora_ja_passada_ha_muito_nao_despeja_o_dia(db):
    """Ligar às 20h com hora 8 não pode publicar os posts das 08h e 09h nos
    minutos seguintes. O teto de atraso é o mesmo do promotor de agendadas."""
    m, r = await _cenario(db)
    r.postagem_hora_inicio = 8
    await db.commit()
    await _criativo(db, m, nome="a", quando=datetime.now(UTC))

    agora = datetime.now(UTC).astimezone(BRT).replace(hour=20, minute=0).astimezone(UTC)
    assert (await svc.rodada(db, agora=agora))["agendadas"] == 0, "a janela das 8h já passou"


# ---------- o dia inteiro, com a configuração REAL ----------


async def test_dia_inteiro_12h_e_19h_com_intervalo_de_7_horas(db, monkeypatch):
    """A configuração que o Eduardo pediu: 12h, 2 por dia, 420 min — um post no
    almoço e outro às 19h.

    Simulando o dia hora a hora, ANTES de ligar, apareceram dois defeitos que
    nenhum teste de intervalo curto pegaria:

    1. o post das 19h nunca saía. A grade era filtrada pela janela de atraso
       (6h) ANTES de subtrair os ocupados; às 19h a vaga das 12h já estava fora
       da janela, e a das 19h era subtraída do post das 12h: 1 − 1 = 0;
    2. corrigido o primeiro, o das 19h caía pras 20h. A guarda de espaçamento
       usa o mesmo número da grade, e a demora de publicar empurra o primeiro
       post pra 12:03 — às 19:02 ela media 6h59m, um minuto "cedo demais".

    O relógio da guarda é congelado junto com o do robô: sem isso o `agendar()`
    usa a hora real e a simulação mente.
    """
    import app.services.marketing.postagens as pp
    from sqlalchemy import select

    m, r = await _cenario(db)
    r.postagem_hora_inicio = 12
    r.postagem_max_dia = 2
    r.postagem_intervalo_min = 420
    await db.commit()
    for i in range(3):
        await _criativo(db, m, nome=f"v{i}", quando=datetime.now(UTC) - timedelta(days=i))

    saidas = []
    for hora in (11, 12, 13, 18, 19, 20, 21):
        congelado = datetime.now(UTC).astimezone(BRT).replace(
            hour=hora, minute=2, second=0, microsecond=0
        ).astimezone(UTC)
        monkeypatch.setattr(pp, "_agora", lambda q=None, _c=congelado: q or _c)
        await svc.rodada(db, agora=congelado)
        # O publicador: o que o robô agendou sai um minuto depois.
        for p in (await db.execute(
            select(MarketingPostagem).where(MarketingPostagem.status == "pendente")
        )).scalars().all():
            p.status = "publicado"
            p.publicado_em = congelado + timedelta(minutes=1)
            saidas.append(p.publicado_em.astimezone(BRT).strftime("%H:%M"))
        await db.commit()

    assert saidas == ["12:03", "19:03"], "um no almoço, outro às 19h — nem 20h, nem só um"


# ---------- Shopee Vídeo: sorteio, nunca repetido (Eduardo, 16/11/2026) ----------
#
# As lojas de celular da Uranyx (ATLAS, Atv, Mega) postam sozinhas às 12h e
# 19h, tirando do MESMO acervo aprovado. Duas decisões do dono:
#   1. o robô SORTEIA o vídeo de cada loja (não a fila fixa);
#   2. o MESMO vídeo nunca sai duas vezes na Shopee — em loja nenhuma,
#      incluindo a própria e loja já apagada.
# A conferência por loja (anúncio, formato) é de outro módulo e vem dublada
# aqui (`loja_aceita`): estes testes medem o sorteio e o corte do já usado.


class _Inverte:
    """RNG previsível: "embaralhar" = inverter. Mostra QUEM decide a ordem."""

    def shuffle(self, x):
        x.reverse()


class _SemSorteio:
    def shuffle(self, _x):
        raise AssertionError("fora da Shopee não tem sorteio")


@pytest.fixture
def loja_aceita(monkeypatch):
    """A loja aceita todo vídeo; guarda quais chegaram a ser conferidos."""
    vistos: list = []

    async def _aceita(_session, _criativo, arquivo, _rede, **_kw):
        vistos.append(arquivo.id)
        return None

    monkeypatch.setattr(svc, "motivo_do_video_na_loja", _aceita)
    return vistos


async def _uranyx(db) -> Marca:
    m = Marca(nome="Uranyx", slug="uranyx")
    db.add(m)
    await db.commit()
    return m


async def _loja_shopee(db, marca, conta: str, **kw) -> RedeSocial:
    r = _rede(
        marca_id=marca.id, plataforma="shopee", conta=conta, adspower_user_id=None,
        postagem_hora_inicio=12, postagem_max_dia=1, postagem_intervalo_min=420, **kw,
    )
    db.add(r)
    await db.commit()
    return r


async def _postou(db, c, f, rede, *, status="publicado", plataforma=None, **campos):
    db.add(MarketingPostagem(
        creative_id=c.id, file_id=f.id, rede_social_id=rede.id if rede else None,
        plataforma=plataforma or (rede.plataforma if rede else "shopee"),
        conta=rede.conta if rede else "loja apagada", status=status,
        publicado_em=datetime.now(UTC) - timedelta(days=2), **campos,
    ))
    await db.commit()


async def _outro_arquivo(db, c, *, nome: str, quando: datetime) -> MarketingCreativeFile:
    """Mais um vídeo na MESMA linha do criativo (outra versão do mesmo vídeo)."""
    rel = f"x/{c.id}-{nome}.mp4"
    caminho = Path(get_settings().uploads_dir) / rel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"\x00\x00\x00\x18ftypmp42-fake")
    f = MarketingCreativeFile(
        creative_id=c.id, file_name=f"{nome}.mp4", file_mime="video/mp4", file_rel=rel,
    )
    f.created_at = quando
    db.add(f)
    await db.commit()
    return f


def _semente(n: int) -> random.Random:
    # Pseudoaleatório DE PROPÓSITO: o teste precisa repetir o mesmo sorteio.
    return random.Random(n)  # noqa: S311


async def _sorteia(db, monkeypatch, rede, semente):
    monkeypatch.setattr(svc, "_sorteio", _semente(semente))
    achado = await svc.proximo_criativo(db, rede)
    return achado[1].id if achado else None


async def test_shopee_sorteia_o_video_e_nao_tira_sempre_o_mais_recente(
    db, monkeypatch, loja_aceita
):
    """Decisão 1: na fila fixa toda loja pegaria o mesmo "mais recente"."""
    m = await _uranyx(db)
    atlas = await _loja_shopee(db, m, "atlas")
    agora = datetime.now(UTC)
    arquivos = set()
    for i in range(6):
        _c, f = await _criativo(db, m, nome=f"v{i}", quando=agora - timedelta(days=i))
        arquivos.add(f.id)

    escolhas = [await _sorteia(db, monkeypatch, atlas, s) for s in range(12)]
    assert set(escolhas) <= arquivos
    assert len(set(escolhas)) >= 3, f"sorteio, não fila: {escolhas}"
    # Reproduzível com a mesma semente: a ordem do SQL é fixa antes do embaralho.
    assert await _sorteia(db, monkeypatch, atlas, 7) == escolhas[7]


async def test_shopee_ignora_a_fila_e_sorteia_no_monte_inteiro(db, monkeypatch, loja_aceita):
    """Sorteio PURO ("randomiza", 16/11/2026): `fila_posicao` não dá
    prioridade na Shopee — o vídeo que furou a fila entra no mesmo monte que
    os outros, e o resto sai mesmo com ele ainda livre."""
    m = await _uranyx(db)
    atlas = await _loja_shopee(db, m, "atlas")
    agora = datetime.now(UTC)
    prio = {
        (await _criativo(db, m, nome="prio1", quando=agora - timedelta(days=30), fila=1))[1].id,
        (await _criativo(db, m, nome="prio2", quando=agora - timedelta(days=20), fila=2))[1].id,
    }
    resto = {
        (await _criativo(db, m, nome=f"novo{i}", quando=agora - timedelta(hours=i)))[1].id
        for i in range(4)
    }

    escolhas = [await _sorteia(db, monkeypatch, atlas, s) for s in range(24)]
    assert set(escolhas) <= prio | resto
    assert set(escolhas) & resto, f"o resto sai com os priorizados ainda livres: {escolhas}"
    assert set(escolhas) & prio, f"os priorizados também entram no monte: {escolhas}"

    # Um embaralho só, do monte inteiro (priorizados e resto juntos).
    class _Anota:
        def __init__(self):
            self.montes = []

        def shuffle(self, x):
            self.montes.append(len(x))

    rng = _Anota()
    monkeypatch.setattr(svc, "_sorteio", rng)
    await svc.proximo_criativo(db, atlas)
    assert rng.montes == [6]


async def test_shopee_sorteia_por_criativo_e_leva_o_arquivo_mais_novo(db, monkeypatch):
    """Um criativo = um vídeo na Shopee: o sorteio é entre CRIATIVOS (o de
    três arquivos não tem três vezes a chance), e do criativo sorteado vai o
    arquivo mais novo — a versão antiga só se a nova não serve pra loja."""
    m = await _uranyx(db)
    atlas = await _loja_shopee(db, m, "atlas")
    agora = datetime.now(UTC)
    c_a, a_velho = await _criativo(db, m, nome="a_v1", quando=agora - timedelta(days=9))
    a_meio = await _outro_arquivo(db, c_a, nome="a_v2", quando=agora - timedelta(days=5))
    a_novo = await _outro_arquivo(db, c_a, nome="a_v3", quando=agora - timedelta(days=1))
    _c_b, b = await _criativo(db, m, nome="b", quando=agora - timedelta(days=3))

    recusados: set = set()

    async def loja(_s, _c, arquivo, _r, **_kw):
        return "video_formato_nao_aceito" if arquivo.id in recusados else None

    monkeypatch.setattr(svc, "motivo_do_video_na_loja", loja)

    class _Anota:
        def __init__(self):
            self.montes = []

        def shuffle(self, x):
            self.montes.append([[f.id for _c, f in arquivos] for arquivos in x])

    rng = _Anota()
    monkeypatch.setattr(svc, "_sorteio", rng)
    assert (await svc.proximo_criativo(db, atlas))[1].id in (a_novo.id, b.id)
    # O embaralho é de 2 criativos (não de 4 arquivos), cada um com os
    # arquivos do mais novo pro mais antigo.
    (monte,) = rng.montes
    assert sorted(monte, key=len) == [[b.id], [a_novo.id, a_meio.id, a_velho.id]]

    escolhas = [await _sorteia(db, monkeypatch, atlas, s) for s in range(16)]
    assert set(escolhas) == {a_novo.id, b.id}, "a versão antiga nunca ganha da nova"

    # A tela "Fila do robô" promete o mesmo: na loja Shopee só espera o
    # arquivo que o sorteio levaria de cada criativo.
    from app.services.marketing import shopee_video_anuncio as anuncio

    async def _sem_ffprobe(_caminho):
        return None

    monkeypatch.setattr(anuncio, "sondar_video", _sem_ffprobe)

    async def _na_loja():
        return {
            it.arquivo.id
            for it in await svc.fila(db, m.id)
            if any(x.id == atlas.id for x in it.pendente_em)
        }

    assert await _na_loja() == {a_novo.id, b.id}

    # A nova não serve (formato): vai a mais nova que serve, do mesmo criativo.
    recusados.add(a_novo.id)
    escolhas = {await _sorteia(db, monkeypatch, atlas, s) for s in range(16)}
    assert escolhas == {a_meio.id, b.id}
    assert await _na_loja() == {a_meio.id, b.id}


async def test_shopee_video_ja_usado_em_qualquer_loja_nem_entra_no_sorteio(db, monkeypatch):
    """Decisão 2: nunca o mesmo vídeo duas vezes na Shopee. O corte é no SQL,
    ANTES da conferência por loja (anúncio + ffprobe): o já usado nem chega
    a ser conferido."""
    m = await _uranyx(db)
    atlas = await _loja_shopee(db, m, "atlas")
    atv = await _loja_shopee(db, m, "atv")
    ig = _rede(marca_id=m.id, plataforma="instagram", conta="uranyx_br")
    db.add(ig)
    await db.commit()
    agora = datetime.now(UTC)

    async def cri(nome, **kw):
        return await _criativo(db, m, nome=nome, quando=agora - timedelta(days=1), **kw)

    # Saem do sorteio:
    await _postou(db, *(await cri("na_atv")), atv)
    await _postou(db, *(await cri("em_voo_na_atv")), atv, status="agendado")
    await _postou(db, *(await cri("em_duvida_na_atv")), atv, status="revisar")
    await _postou(db, *(await cri("loja_apagada")), None)  # rede_social_id NULL
    _c, _mesmo_conteudo = await cri("mesmo_conteudo", sha="abc")
    await _postou(db, *(await cri("reenvio_na_propria", sha="abc")), atlas, status="revisar")
    await _postou(db, *(await cri("sem_hash_usado", sha="")), atv)
    # Continuam:
    c, f_cancelado = await cri("cancelado_na_atv")
    await _postou(db, c, f_cancelado, atv, status="cancelado")
    c, f_ig = await cri("so_no_instagram")
    await _postou(db, c, f_ig, ig)
    _c, f_sem_hash = await cri("sem_hash_livre", sha="")  # vazio não é "todos iguais"

    vistos = []

    async def recusa(_s, _c, arquivo, _r, **_kw):
        vistos.append(arquivo.id)
        return "sem_anuncio_na_loja"

    monkeypatch.setattr(svc, "motivo_do_video_na_loja", recusa)
    assert await svc.proximo_criativo(db, atlas) is None
    assert set(vistos) == {f_cancelado.id, f_ig.id, f_sem_hash.id}


async def test_shopee_sorteio_usa_a_mesma_regra_do_ja_na_shopee(db, monkeypatch):
    """A regra do "já está na Shopee" é UMA (a da conferência por loja):
    linha cancelada que chegou ao upload (`container_id`) ou ao post segura o
    vídeo; cancelada sem upload não; e outro arquivo do MESMO criativo também
    tira o criativo do sorteio (um criativo = um vídeo)."""
    m = await _uranyx(db)
    atlas = await _loja_shopee(db, m, "atlas")
    atv = await _loja_shopee(db, m, "atv")
    agora = datetime.now(UTC)

    async def cri(nome):
        return await _criativo(db, m, nome=nome, quando=agora - timedelta(days=1))

    # Saem do sorteio:
    await _postou(db, *(await cri("revisar_cancelado")), atv, status="cancelado",
                  container_id="br-123")
    await _postou(db, *(await cri("post_cancelado")), None, status="cancelado",
                  post_external_id="POST-1==")
    c_v1, v1 = await cri("versao_1")
    v2 = await _outro_arquivo(db, c_v1, nome="versao_2", quando=agora)
    await _postou(db, c_v1, v1, atv)  # a v1 saiu na Atv: a v2 é o MESMO vídeo
    # Continua:
    c, f_livre = await cri("cancelado_sem_upload")
    await _postou(db, c, f_livre, atv, status="cancelado")

    vistos = []

    async def recusa(_s, _c, arquivo, _r, **_kw):
        vistos.append(arquivo.id)
        return "sem_anuncio_na_loja"

    monkeypatch.setattr(svc, "motivo_do_video_na_loja", recusa)
    assert await svc.proximo_criativo(db, atlas) is None
    assert set(vistos) == {f_livre.id}
    assert v2.id not in vistos

    # A tela segue a mesma regra: nenhuma loja Shopee espera a v2.
    from app.services.marketing import shopee_video_anuncio as anuncio

    async def _sem_ffprobe(_caminho):
        return None

    monkeypatch.setattr(anuncio, "sondar_video", _sem_ffprobe)
    monkeypatch.setattr(svc, "motivo_do_video_na_loja", lambda *_a, **_k: _nada())
    por = {it.arquivo.id: {x.conta for x in it.pendente_em} for it in await svc.fila(db, m.id)}
    assert v2.id not in por
    assert por[f_livre.id] == {"atlas"}


async def _nada():
    return None


async def test_rodada_sorteia_a_vez_so_entre_as_lojas_shopee(monkeypatch):
    """A loja que vai primeiro tem a primeira escolha do monte comum: a vez
    muda a cada rodada. As outras redes ficam exatamente onde estavam."""
    from uuid import uuid4

    tt = _rede(conta="tt", id=uuid4())
    ig = _rede(plataforma="instagram", conta="ig", id=uuid4())
    lojas = [_rede(plataforma="shopee", conta=n, id=uuid4()) for n in ("atlas", "atv", "mega")]
    contas = [tt, lojas[0], ig, lojas[1], lojas[2]]

    primeiras = set()
    for semente in range(20):
        monkeypatch.setattr(svc, "_sorteio", _semente(semente))
        vez = svc._vez_das_contas(contas)
        assert vez[0] is tt and vez[2] is ig
        assert sorted(r.conta for r in (vez[1], vez[3], vez[4])) == ["atlas", "atv", "mega"]
        primeiras.add(vez[1].conta)
    assert primeiras == {"atlas", "atv", "mega"}, "ninguém é sempre o primeiro"

    monkeypatch.setattr(svc, "_sorteio", _SemSorteio())
    assert svc._vez_das_contas([tt, ig, lojas[0]]) == [tt, ig, lojas[0]]


def _agendar_dublado(monkeypatch):
    """O `agendar` de verdade da Shopee pede loja, token e anúncio (coberto em
    test_shopee_video_postagem). Aqui só importa que a postagem nasça."""

    async def agendar(session, *, creative, file, redes, **kw):
        (rede,) = redes
        p = MarketingPostagem(
            creative_id=creative.id, file_id=file.id, rede_social_id=rede.id,
            plataforma=rede.plataforma, conta=rede.conta, status="pendente",
            origem=kw.get("origem") or "robo",
        )
        session.add(p)
        await session.commit()
        return [p]

    monkeypatch.setattr(svc, "agendar", agendar)


async def test_rodada_duas_lojas_shopee_nunca_pegam_o_mesmo_video(db, monkeypatch, loja_aceita):
    from sqlalchemy import select as _sel

    _agendar_dublado(monkeypatch)
    m = await _uranyx(db)
    atlas = await _loja_shopee(db, m, "atlas")
    atv = await _loja_shopee(db, m, "atv")
    for i in range(2):
        await _criativo(db, m, nome=f"v{i}", quando=datetime.now(UTC) - timedelta(days=i))

    for semente in range(4):
        monkeypatch.setattr(svc, "_sorteio", _semente(semente))
        for p in (await db.execute(_sel(MarketingPostagem))).scalars().all():
            await db.delete(p)
        await db.commit()
        agora = datetime.now(UTC).astimezone(BRT).replace(hour=12, minute=5).astimezone(UTC)
        assert (await svc.rodada(db, agora=agora))["agendadas"] == 2
        posts = (await db.execute(_sel(MarketingPostagem))).scalars().all()
        assert {p.rede_social_id for p in posts} == {atlas.id, atv.id}
        assert len({p.file_id for p in posts}) == 2, "o mesmo vídeo nunca em duas lojas"


async def test_com_um_video_so_quem_tem_a_vez_leva(db, monkeypatch, loja_aceita):
    from sqlalchemy import select as _sel

    _agendar_dublado(monkeypatch)
    monkeypatch.setattr(svc, "_sorteio", _Inverte())
    m = await _uranyx(db)
    lojas = [await _loja_shopee(db, m, "atlas"), await _loja_shopee(db, m, "atv")]
    await _criativo(db, m, nome="unico", quando=datetime.now(UTC))

    agora = datetime.now(UTC).astimezone(BRT).replace(hour=12, minute=5).astimezone(UTC)
    r = await svc.rodada(db, agora=agora)
    assert r["agendadas"] == 1 and r["sem_criativo"] == 1, r
    (p,) = (await db.execute(_sel(MarketingPostagem))).scalars().all()
    # Ordem por id e depois o "embaralho" (aqui: inverter) → vai a de maior id.
    assert p.rede_social_id == max(lojas, key=lambda x: str(x.id)).id


async def test_fora_da_shopee_a_fila_continua_igual_e_sem_sorteio(db, monkeypatch):
    """TikTok/Instagram/Facebook/YouTube: o mais recente, sem sorteio — e o
    vídeo que já saiu na Shopee continua valendo nas outras redes."""
    monkeypatch.setattr(svc, "_sorteio", _SemSorteio())
    m, r = await _cenario(db)
    agora = datetime.now(UTC)
    await _criativo(db, m, nome="antigo", quando=agora - timedelta(days=5))
    c_nov, f_nov = await _criativo(db, m, nome="novo", quando=agora - timedelta(hours=1))
    loja = await _loja_shopee(db, m, "atlas", postagem_auto=False)
    await _postou(db, c_nov, f_nov, loja)

    achado = await svc.proximo_criativo(db, r)
    assert achado is not None and achado[1].id == f_nov.id

    from sqlalchemy import select as _sel

    quando = datetime.now(UTC).astimezone(BRT).replace(hour=18, minute=30).astimezone(UTC)
    assert (await svc.rodada(db, agora=quando))["agendadas"] == 1
    p = (await db.execute(
        _sel(MarketingPostagem).where(MarketingPostagem.rede_social_id == r.id)
    )).scalars().one()
    assert p.file_id == f_nov.id


async def test_fila_da_tela_e_o_monte_do_sorteio_na_shopee(db, monkeypatch, loja_aceita):
    """A tela não pode prometer pra ATLAS o vídeo que já saiu na Atv (nem a
    cópia dele com o mesmo conteúdo), e marca a conta como sorteio."""
    from app.routers.marketing_creatives import _item_fila_out
    from app.services.marketing import shopee_video_anuncio as anuncio

    async def _sem_ffprobe(_caminho):
        return None

    monkeypatch.setattr(anuncio, "sondar_video", _sem_ffprobe)
    m = await _uranyx(db)
    await _loja_shopee(db, m, "atlas")
    atv = await _loja_shopee(db, m, "atv")
    ig = _rede(marca_id=m.id, plataforma="instagram", conta="uranyx_br")
    db.add(ig)
    await db.commit()
    agora = datetime.now(UTC)
    c, f_usado = await _criativo(db, m, nome="usado", quando=agora, sha="abc")
    await _postou(db, c, f_usado, atv)
    _c, f_copia = await _criativo(db, m, nome="copia", quando=agora, sha="abc")
    _c, f_livre = await _criativo(db, m, nome="livre", quando=agora)

    itens = await svc.fila(db, m.id)
    por = {it.arquivo.id: {x.conta for x in it.pendente_em} for it in itens}
    assert por[f_usado.id] == {"uranyx_br"}
    assert por[f_copia.id] == {"uranyx_br"}
    assert por[f_livre.id] == {"atlas", "atv", "uranyx_br"}

    livre = next(it for it in itens if it.arquivo.id == f_livre.id)
    saida = {p["conta"]: p.get("sorteio") for p in _item_fila_out(livre, {})["pendente_em"]}
    assert saida == {"atlas": True, "atv": True, "uranyx_br": None}


async def test_shopee_criativo_com_linha_nesta_loja_sai_inteiro_do_sorteio_dela(
    db, monkeypatch, loja_aceita
):
    """Um criativo = um vídeo na Shopee: na loja onde QUALQUER arquivo dele já
    teve postagem (até cancelada sem upload — uma pessoa disse não ali),
    nenhum outro arquivo do criativo volta. A tela segue igual. Na outra loja
    (nada dele tentado lá) e fora da Shopee (por arquivo) nada muda."""
    from app.services.marketing import shopee_video_anuncio as anuncio

    async def _sem_ffprobe(_caminho):
        return None

    monkeypatch.setattr(anuncio, "sondar_video", _sem_ffprobe)
    m = await _uranyx(db)
    atlas = await _loja_shopee(db, m, "atlas")
    atv = await _loja_shopee(db, m, "atv")
    tt = _rede(marca_id=m.id, conta="tt")
    db.add(tt)
    await db.commit()
    agora = datetime.now(UTC)
    c, v1 = await _criativo(db, m, nome="v1", quando=agora - timedelta(days=2))
    v2 = await _outro_arquivo(db, c, nome="v2", quando=agora)
    await _postou(db, c, v1, atlas, status="cancelado")
    await _postou(db, c, v1, tt, status="cancelado")

    for semente in range(4):
        assert await _sorteia(db, monkeypatch, atlas, semente) is None, "a v2 é o MESMO vídeo"
    assert await _sorteia(db, monkeypatch, atv, 0) == v2.id
    assert (await svc.proximo_criativo(db, tt))[1].id == v2.id, "fora da Shopee: por arquivo"

    por = {it.arquivo.id: {x.conta for x in it.pendente_em} for it in await svc.fila(db, m.id)}
    assert por[v2.id] == {"atv", "tt"}
    assert v1.id not in por


def test_selo_n_na_fila_ignora_quem_so_espera_conta_de_sorteio():
    """O "#n na fila" é a ordem da fila FIXA: o sorteio da Shopee ignora a
    prioridade, então o vídeo que só espera conta de Shopee não ocupa número."""
    from uuid import uuid4

    loja = _rede(plataforma="shopee", conta="atlas", id=uuid4())
    tt = _rede(conta="tt", id=uuid4())

    def item(posicao, contas):
        c = MarketingCreative(id=uuid4(), fila_posicao=posicao)
        return svc.ItemFila(c, MarketingCreativeFile(id=uuid4(), creative_id=c.id), contas)

    so_shopee, misto, so_tt = item(1, [loja]), item(2, [loja, tt]), item(3, [tt])
    assert svc.posicoes_efetivas([so_shopee, misto, so_tt]) == {
        misto.criativo.id: 1,
        so_tt.criativo.id: 2,
    }
