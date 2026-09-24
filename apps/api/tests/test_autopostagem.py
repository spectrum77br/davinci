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


async def _criativo(db, marca, *, nome: str, quando: datetime, aprovado: bool = True):
    c = MarketingCreative(modelo=nome, marca=marca.slug, marca_id=marca.id, aprovado=aprovado)
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
