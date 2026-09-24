"""As contas da tela Desempenho (services/marketing/desempenho.py) — sem banco.

Pedido do Eduardo (24/09/2026): "trackear o que cada vídeo deu de retorno pra
saber o que investir". O que estes testes defendem, e por quê:

  - MESMA IDADE. Vídeo de um mês sempre ganha do de ontem no acumulado. Aqui
    todo vídeo é comparado pelas views que tinha com N dias, interpoladas entre
    as leituras que cercam essa idade — e buraco de coleta vira NULO, nunca um
    número inventado.
  - "× O NORMAL DA CONTA". Conta grande sempre ganha de conta pequena, e view
    de TikTok não vale view de Instagram. O índice divide pelo normal dos
    OUTROS vídeos da mesma conta, e só existe com base de 4 outros vídeos.
  - UMA REGRA DE GANHO. Cartão, série e "no período" usam a mesma conta, e a
    primeira leitura só é ganho se o vídeo foi lido desde o nascimento —
    senão vídeo antigo vira pico falso no dia em que a leitura começou.
  - GRUPO CONTA CRIATIVO. O mesmo vídeo em 3 redes é 1 decisão de produção.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.services.marketing import desempenho as d
from app.services.marketing.metricas import REMOVIDO

# 15:00 em Brasília.
AGORA = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)


def _l(pub: datetime, horas: float, views: int | None = None, **kw) -> dict:
    """Uma leitura boa com `horas` de idade do vídeo."""
    lido = pub + timedelta(hours=horas)
    return {
        "postagem_id": kw.pop("postagem_id", "p"),
        "dia": lido,
        "lido_em": lido,
        "updated_at": lido,
        "erro": None,
        "views": views,
        **kw,
    }


def _post(pid: str, *, pub: datetime, **kw) -> dict:
    base = {
        "id": pid,
        "creative_id": f"c-{pid}",
        "plataforma": "tiktok",
        "conta": "uranyx_br",
        "conta_atual": "uranyx_br",
        "rede_social_id": "conta-a",
        "post_url": None,
        "publicado_em": pub,
        "origem": "robo",
        "legenda": f"vídeo {pid}",
        "fora_do_desempenho_em": None,
        "fora_do_desempenho_motivo": None,
        "marca_id": "m1",
        "marca": "Uranyx",
        "equipe": None,
        "sku": None,
        "modelo": "video 30s",
        "product_id": None,
        "produto_nome": None,
        "roteiro_id": None,
        "roteiro_titulo": None,
    }
    base.update(kw)
    return base


def _conta(prefixo: str, valores: list[int], *, rede: str = "conta-a", **kw):
    """Posts de uma conta com leitura EXATAMENTE na idade de 3 dias — o valor
    no marco é o próprio número, sem interpolação no meio do teste."""
    pub = AGORA - timedelta(days=10)
    posts, linhas = [], []
    for i, v in enumerate(valores):
        pid = f"{prefixo}{i}"
        posts.append(_post(pid, pub=pub, rede_social_id=rede, **kw))
        linhas.append(_l(pub, 72, v, postagem_id=pid))
    return posts, linhas


def _noite(dia: date, views: int, pid: str = "p") -> dict:
    """O retrato da leitura das 23:47 de `dia`."""
    t = datetime.combine(dia, time(23, 47), d.BRT)
    return {
        "postagem_id": pid,
        "dia": t,
        "lido_em": t,
        "updated_at": t,
        "erro": None,
        "views": views,
    }


def _montar(posts, linhas, **kw):
    kw.setdefault("dias", 30)
    kw.setdefault("marco", 3)
    kw.setdefault("agora", AGORA)
    return d.montar(posts, linhas, **kw)


def _por_id(r: dict) -> dict[str, dict]:
    return {p["postagem_id"]: p for p in r["postagens"]}


# ---------- views na mesma idade ----------


def test_marco_interpola_entre_as_leituras_que_cercam_a_idade():
    """Post das 12:04 lido às 23:47 (11,7 h) e na noite seguinte (35,7 h): o
    D+1 é o ponto da reta entre as duas, não a leitura mais perto."""
    pub = AGORA - timedelta(days=5)
    v, motivo = d.views_no_marco([_l(pub, 11.7, 180), _l(pub, 35.7, 420)], pub, 1, AGORA)
    assert v == 303, "180 + 240 × 12,3/24"
    assert motivo is None


def test_marco_exato_quando_ha_leitura_na_idade():
    pub = AGORA - timedelta(days=5)
    v, _ = d.views_no_marco([_l(pub, 24, 500), _l(pub, 48, 900)], pub, 1, AGORA)
    assert v == 500


def test_sem_leitura_depois_do_marco_e_nulo_nao_extrapola():
    """Vídeo de 30 h não tem D+3. Extrapolar a curva seria inventar número —
    e o vídeo novo apareceria na frente só por ser novo."""
    pub = AGORA - timedelta(hours=30)
    v, motivo = d.views_no_marco([_l(pub, 10, 100), _l(pub, 28, 300)], pub, 3, AGORA)
    assert v is None
    assert motivo == "cedo"


def test_buraco_de_coleta_maior_que_36h_vira_nulo():
    """Leituras com 16 h e 88 h: o D+1 no meio seria chute, não medida."""
    pub = AGORA - timedelta(days=10)
    v, motivo = d.views_no_marco([_l(pub, 16, 100), _l(pub, 88, 900)], pub, 1, AGORA)
    assert v is None
    assert motivo == "buraco"


def test_ancora_zero_vale_so_perto_do_marco():
    """O vídeo nasce com 0 views — isso é medida, não palpite. Mas só serve de
    ponto de apoio perto da idade pedida (até 36 h)."""
    pub = AGORA - timedelta(days=10)
    v, _ = d.views_no_marco([_l(pub, 28, 700)], pub, 1, AGORA)
    assert v == round(700 * 24 / 28)
    v3, motivo = d.views_no_marco([_l(pub, 16, 700)], pub, 3, AGORA)
    assert v3 is None
    assert motivo == "buraco", "10 dias de vida e nenhuma leitura perto do D+3"
    # Leitura DEPOIS perto, mas a de ANTES longe demais: a âncora 0 h está a
    # 72 h do D+3, e uma leitura com 10 h está a 62 h. Interpolar daí é chute.
    assert d.views_no_marco([_l(pub, 88, 1000)], pub, 3, AGORA) == (None, "buraco")
    assert d.views_no_marco([_l(pub, 10, 100), _l(pub, 100, 1000)], pub, 3, AGORA) == (
        None,
        "buraco",
    )


def test_leitura_antes_da_publicacao_e_ignorada():
    """Retrato com data antes da publicação (relógio, ou gravado antes do
    post existir) não é idade negativa: sai da conta."""
    pub = AGORA - timedelta(days=5)
    leituras = [_l(pub, -5, 50), _l(pub, 24, 200)]
    v, _ = d.views_no_marco(leituras, pub, 1, AGORA)
    assert v == 200
    assert all(h >= 0 for h, _ in d.pontos_views(leituras, pub))


def test_sem_nenhuma_leitura_e_aguardando_e_sem_views_e_sem_views():
    pub = AGORA - timedelta(days=5)
    assert d.views_no_marco([], pub, 3, AGORA) == (None, "aguardando")
    assert d.views_no_marco([_l(pub, 80, None, curtidas=4)], pub, 3, AGORA) == (
        None,
        "sem_views",
    )


# ---------- índice: × o normal da conta ----------


def test_indice_exige_4_outros_videos_da_conta():
    """Com 3 outros vídeos, a "mediana da conta" é sorte. Sem índice, com o
    motivo, em vez de um 2,0× que não quer dizer nada."""
    posts, linhas = _conta("a", [1000, 1000, 1000, 2000])
    r = _por_id(_montar(posts, linhas))
    assert r["a3"]["indice_views"] is None
    assert r["a3"]["indice_motivo"] == "base_pequena"
    assert r["a3"]["base"]["n"] == 3

    posts, linhas = _conta("a", [1000, 1000, 1000, 1000, 2000])
    r = _por_id(_montar(posts, linhas))
    assert r["a4"]["indice_views"] == 2.0
    assert r["a4"]["base"] == {"mediana": 1000, "n": 4}


def test_indice_e_relativo_a_propria_conta():
    """20 views numa conta que faz 10 vale o mesmo que 2000 numa que faz 1000."""
    pa, la = _conta("a", [1000, 1000, 1000, 1000, 2000], rede="grande")
    pb, lb = _conta("b", [10, 10, 10, 10, 20], rede="pequena")
    r = _por_id(_montar(pa + pb, la + lb))
    assert r["a4"]["indice_views"] == 2.0
    assert r["b4"]["indice_views"] == 2.0


def test_indice_nao_usa_o_proprio_video_na_base():
    """O normal são os OUTROS vídeos. Com o próprio na base, o sucesso puxa a
    mediana pra cima e se esconde."""
    posts, linhas = _conta("a", [100, 200, 300, 400, 10000])
    p = _por_id(_montar(posts, linhas))["a4"]
    assert p["base"] == {"mediana": 250, "n": 4}
    assert p["indice_views"] == 40.0


def test_video_apagado_nao_entra_na_base_da_conta():
    """Vídeo apagado das redes (o Eduardo apaga teste de propósito) não é o
    "normal" de ninguém: mexeria na mediana e no tamanho da base de todos os
    outros vídeos da conta — e poderia até completar a base mínima."""
    posts, linhas = _conta("a", [100, 100, 100, 100, 10000])
    quando = AGORA - timedelta(hours=1)
    linhas.append(
        {
            "postagem_id": "a4",
            "dia": quando,
            "lido_em": None,
            "updated_at": quando,
            "erro": f"{REMOVIDO} saiu do ar",
            "views": None,
        }
    )
    r = _por_id(_montar(posts, linhas))
    assert "a4" not in r, "apagado vai pro rodapé, não pra tabela"
    for pid in ("a0", "a1", "a2", "a3"):
        assert r[pid]["base"] == {"mediana": 100, "n": 3}
        assert r[pid]["indice_motivo"] == "base_pequena", "o apagado não completa a base"


def test_base_ignora_filtro_de_marca_e_escopo():
    """Todo mundo vê o mesmo índice pro mesmo vídeo: a base é calculada antes
    do filtro de marca e do escopo de equipe."""
    posts, linhas = _conta("a", [1000, 1000, 1000, 1000, 2000])
    for p in posts[:4]:
        p["equipe"] = "Outra"
        p["marca_id"] = "m2"
    posts[4]["equipe"] = "Bill Gates"
    r = _montar(posts, linhas, marca_id="m1", equipes_permitidas={"bill gates"})
    assert [p["postagem_id"] for p in r["postagens"]] == ["a4"]
    assert r["postagens"][0]["indice_views"] == 2.0


def test_taxa_ignora_nulo_e_exige_100_views():
    """O YouTube não tem compartilhamento nem salvamento: a taxa soma só o que
    veio. Abaixo de 100 views, a taxa é ruído."""
    pub = AGORA - timedelta(days=5)
    yt = [_l(pub, 48, 1000, curtidas=50, comentarios=10)]
    assert d.taxa_interacao(yt) == 0.06
    assert d.taxa_interacao([_l(pub, 48, 99, curtidas=50)]) is None
    assert d.taxa_interacao([_l(pub, 48, 1000)]) is None, "sem interação nenhuma ≠ zero"


def test_indice_do_criativo_e_a_mediana_dos_posts():
    posts, linhas = [], []
    for rede, v in (("instagram", 200), ("youtube", 300), ("tiktok", 50)):
        p, lin = _conta(f"{rede}-", [100, 100, 100, 100], rede=rede, plataforma=rede)
        posts += p
        linhas += lin
        pub = AGORA - timedelta(days=10)
        pid = f"cx-{rede}"
        posts.append(_post(pid, pub=pub, rede_social_id=rede, plataforma=rede, creative_id="cx"))
        linhas.append(_l(pub, 72, v, postagem_id=pid))
    r = _montar(posts, linhas)
    cx = next(c for c in r["criativos"] if c["creative_id"] == "cx")
    assert cx["n_indices"] == 3
    assert cx["indice_views"] == 2.0, "mediana de 2,0 / 3,0 / 0,5"
    assert cx["postagens"]["tiktok"] == ["cx-tiktok"]


# ---------- grupos ----------


def test_grupo_conta_criativo_nao_postagem():
    """Um vídeo em 3 redes é UM criativo: contar 3 faria o grupo parecer mais
    testado do que é."""
    posts, linhas = [], []
    for rede in ("instagram", "youtube", "tiktok"):
        p, lin = _conta(f"{rede}-", [100, 100, 100, 100], rede=rede, plataforma=rede)
        posts += p
        linhas += lin
        pub = AGORA - timedelta(days=10)
        pid = f"cx-{rede}"
        posts.append(
            _post(
                pid,
                pub=pub,
                rede_social_id=rede,
                plataforma=rede,
                creative_id="cx",
                modelo="video 15s",
            )
        )
        linhas.append(_l(pub, 72, 200, postagem_id=pid))
    g = {x["chave"]: x for x in _montar(posts, linhas)["grupos"]["formato"]}
    assert g["15s"]["total"] == 1
    assert g["15s"]["n"] == 1
    assert g["15s"]["unidade"] == "criativo"
    assert g["15s"]["melhor"]["creative_id"] == "cx"
    assert set(g["15s"]["por_rede"]) == {"instagram", "youtube", "tiktok"}
    assert g["15s"]["por_rede"]["tiktok"] == {"mediana": 200, "n": 1}


def test_mediana_de_views_sai_inteira():
    """Views são contagem: mediana de 2 vídeos (2.048 e 2.051) dava "2.049,5
    views" na tela, que parece erro de conta. Sai arredondada."""
    posts, linhas = [], []
    for i, v in enumerate((2048, 2051)):
        pub = AGORA - timedelta(days=10)
        pid = f"m{i}"
        posts.append(
            _post(pid, pub=pub, rede_social_id="tt", plataforma="tiktok",
                  creative_id=f"c{i}", modelo="video 15s")
        )
        linhas.append(_l(pub, 72, v, postagem_id=pid))
    g = {x["chave"]: x for x in _montar(posts, linhas)["grupos"]["formato"]}
    med = g["15s"]["por_rede"]["tiktok"]["mediana"]
    assert med == 2050 and isinstance(med, int)


def test_leitura_do_grupo_por_tamanho():
    assert d.leitura_do_grupo(2) == "pouco_dado"
    assert d.leitura_do_grupo(3) == "indicio"
    assert d.leitura_do_grupo(7) == "indicio"
    assert d.leitura_do_grupo(8) == "comparavel"


def test_grupo_ordena_por_leitura_e_indice_nulos_por_ultimo():
    """Primeiro o que dá pra comparar; dentro de cada leitura, o maior índice;
    sem índice por último."""

    def g(chave, leitura, indice, total=1):
        return {"chave": chave, "leitura": leitura, "indice_views": indice, "total": total}

    grupos = [
        g("pouco", "pouco_dado", 9.0),
        g("ind-nulo", "indicio", None, total=9),
        g("ind-baixo", "indicio", 0.8),
        g("comp", "comparavel", 1.1),
        g("ind-alto", "indicio", 1.9),
    ]
    assert [x["chave"] for x in d._ordena_grupos(grupos)] == [
        "comp",
        "ind-alto",
        "ind-baixo",
        "ind-nulo",
        "pouco",
    ]


def test_formato_extrai_duracao_do_modelo():
    assert d.grupo_formato("video 15s") == ("15s", "vídeo 15s")
    assert d.grupo_formato("Vídeo de 30 segundos") == ("30s", "vídeo 30s")
    # O nome do celular não é duração de vídeo.
    assert d.grupo_formato("F109S 256 GB") == ("nenhum", "(formato não informado)")
    assert d.grupo_formato(None) == ("nenhum", "(formato não informado)")


def test_produto_cai_na_base_do_sku_sem_produto_ligado():
    assert d.grupo_produto(None, None, "dg017.pi") == (
        "sku:dg017",
        "SKU dg017 (sem produto ligado)",
    )
    assert d.grupo_produto("u1", "Fone DG017", "dg017.pi") == ("prod:u1", "Fone DG017")
    assert d.grupo_produto(None, None, "  ") == ("nenhum", "(sem produto)")


def test_faixa_de_horario_em_brt():
    assert d.faixa_horario(datetime(2026, 9, 24, 15, 4, tzinfo=UTC)) == "12h"
    assert d.faixa_horario(datetime(2026, 9, 24, 22, 3, tzinfo=UTC)) == "19h"
    assert d.faixa_horario(datetime(2026, 9, 24, 12, 0, tzinfo=UTC)) == "outro", "09h BRT"


# ---------- ganho por dia ----------


def test_ganho_do_zero_quando_lido_desde_o_nascimento():
    """Publicado hoje às 12:04 e lido com 30 views: as 30 foram ganhas hoje."""
    pub = datetime(2026, 9, 24, 15, 4, tzinfo=UTC)
    r = _montar([_post("p", pub=pub)], [_l(pub, 2.7, 30, postagem_id="p")], dias=1)
    assert r["postagens"][0]["no_periodo"] == {"views": 30}
    assert r["resumo"]["redes"][0]["serie"] == [30]


def test_intervalo_de_varios_dias_e_dividido_e_marcado_estimado():
    d0 = date(2026, 9, 10)
    g, est = d.ganhos_diarios([(d0, 100), (d0 + timedelta(days=4), 203)], pub_dia=d0, do_zero=False)
    dias = [d0 + timedelta(days=i) for i in range(1, 5)]
    assert [g[x] for x in dias] == [25, 26, 26, 26], "a soma fecha: 103"
    assert est == set(dias)

    g, _ = d.ganhos_diarios([(d0, 10), (d0 + timedelta(days=2), 5)], pub_dia=d0, do_zero=False)
    assert [g[d0 + timedelta(days=1)], g[d0 + timedelta(days=2)]] == [-3, -2]


def test_primeira_leitura_tardia_e_base_nao_ganho():
    """Vídeo publicado 10 dias antes de a leitura começar: as 1000 views dele
    NÃO foram ganhas no primeiro dia lido."""
    pub = AGORA - timedelta(days=10)
    linhas = [_l(pub, 24 * 8, 1000, postagem_id="p"), _l(pub, 24 * 9, 1100, postagem_id="p")]
    r = _montar([_post("p", pub=pub)], linhas)
    assert r["postagens"][0]["no_periodo"] == {"views": 100}


def test_primeira_leitura_antes_da_publicacao_e_base_nao_ganho():
    """Retrato gravado 5 h ANTES de publicar não é "lido desde o nascimento":
    as 100 views dele são base, não ganho. Só os 50 de depois contam."""
    pub = AGORA - timedelta(days=3)
    r = _montar(
        [_post("p", pub=pub)],
        [_l(pub, -5, 100, postagem_id="p"), _l(pub, 20, 150, postagem_id="p")],
        dias=30,
    )
    assert r["postagens"][0]["no_periodo"] == {"views": 50}


def test_metrica_que_aparece_depois_nao_vira_ganho():
    """As views do Instagram chegam no dia em que os insights chegam. O
    acumulado inteiro não pode virar o "ganho" desse dia."""
    pub = AGORA - timedelta(days=2, hours=3)
    linhas = [
        _l(pub, 10, None, postagem_id="p", curtidas=5),
        _l(pub, 34, 500, postagem_id="p", curtidas=8),
    ]
    r = _montar([_post("p", pub=pub, plataforma="instagram")], linhas)
    p = r["postagens"][0]
    assert "views" not in p["no_periodo"]
    assert p["no_periodo"]["curtidas"] == 8, "curtidas foram lidas desde o nascimento"
    assert p["acumulado"]["views"] == 500


def test_acumulado_de_cada_metrica_vem_da_ultima_leitura_que_tem_ela():
    """Os insights do Instagram vêm e vão: hoje a leitura veio sem views.
    O total de views não pode piscar pra "—" — vale o da última leitura que
    tinha views; as curtidas, que vieram hoje, são as de hoje."""
    pub = AGORA - timedelta(days=2, hours=3)
    linhas = [
        _l(pub, 24, 500, postagem_id="p", curtidas=5),
        _l(pub, 48, None, postagem_id="p", curtidas=8),
    ]
    r = _montar([_post("p", pub=pub, plataforma="instagram")], linhas)
    assert r["postagens"][0]["acumulado"] == {"views": 500, "curtidas": 8}


def test_serie_soma_igual_ao_periodo():
    """A barra diária e o "+N no período" são a mesma conta, sempre — com
    buraco de leitura, dia negativo e vídeo antigo no meio."""
    posts, linhas = [], []
    # Novo, lido desde o nascimento, com buraco de 3 dias.
    pub1 = AGORA - timedelta(days=6)
    posts.append(_post("n", pub=pub1))
    for h, v in ((8, 50), (32, 120), (104, 400), (128, 390)):
        linhas.append(_l(pub1, h, v, postagem_id="n", curtidas=v // 10))
    # Antigo: a primeira leitura é base.
    pub2 = AGORA - timedelta(days=200)
    posts.append(_post("v", pub=pub2, plataforma="youtube", rede_social_id="yt"))
    for i, v in enumerate((5000, 5100, 5050, 5300)):
        linhas.append(_l(AGORA - timedelta(days=40), 24 * i * 9, v, postagem_id="v"))
    # Instagram sem views: a série cai pra curtidas.
    pub3 = AGORA - timedelta(days=3)
    posts.append(_post("i", pub=pub3, plataforma="instagram", rede_social_id="ig"))
    for h, c in ((5, 3), (29, 9), (53, 12)):
        linhas.append(_l(pub3, h, None, postagem_id="i", curtidas=c))

    for dias in (1, 7, 30, 90):
        r = _montar(posts, linhas, dias=dias)
        assert len(r["serie_dias"]) == dias
        for rede in r["resumo"]["redes"]:
            k = rede["metrica_serie"]
            presentes = [v for v in rede["serie"] if v is not None]
            assert len(rede["serie"]) == dias
            assert (sum(presentes) if presentes else None) == rede["no_periodo"].get(k), (
                dias,
                rede["plataforma"],
            )
    r = _montar(posts, linhas, dias=30)
    ig = next(x for x in r["resumo"]["redes"] if x["plataforma"] == "instagram")
    assert ig["metrica_serie"] == "curtidas" and ig["sem_views"] is True
    assert r["resumo"]["tendencia"]["redes_sem_views"] == ["instagram"]


def test_periodo_anterior_so_com_leitura_desde_antes():
    """ "▲ 100%" contra um período em que ninguém lia seria mentira."""
    pub = AGORA - timedelta(days=20)
    linhas = [_l(pub, 24 * i, 100 * i, postagem_id="p") for i in range(2, 20)]
    sem = _montar(
        [_post("p", pub=pub)], linhas, dias=7, inicio_da_coleta=AGORA - timedelta(days=10)
    )
    assert sem["resumo"]["redes"][0]["periodo_anterior"] is None
    com = _montar(
        [_post("p", pub=pub)], linhas, dias=7, inicio_da_coleta=AGORA - timedelta(days=18)
    )
    assert com["resumo"]["redes"][0]["periodo_anterior"] == 700
    # Às 15h o último dia fechado é ontem (23/09): o período anterior começa
    # em 10/09. O ganho de 10/09 é contra a leitura de 09/09 — com a leitura
    # começando no próprio dia 10, o primeiro dia dele ainda era só base.
    no_dia = _montar([_post("p", pub=pub)], linhas, dias=7, inicio_da_coleta=date(2026, 9, 10))
    assert no_dia["resumo"]["redes"][0]["periodo_anterior"] is None
    vespera = _montar([_post("p", pub=pub)], linhas, dias=7, inicio_da_coleta=date(2026, 9, 9))
    assert vespera["resumo"]["redes"][0]["periodo_anterior"] == 700


def test_fluxo_parado_da_zero_por_cento_mesmo_com_hoje_pela_metade():
    """Um vídeo que ganha 100 views por dia, lido toda noite às 23:47. Às 15h
    o hoje ainda não tem leitura: comparar "os últimos 7 dias" (com esse hoje
    vazio) contra 7 dias inteiros dava "▼ 14%" com o fluxo parado — o dia
    inteiro, todo dia. A comparação é entre dias FECHADOS."""
    hoje = AGORA.astimezone(d.BRT).date()
    ini = hoje - timedelta(days=40)
    linhas = [_noite(ini + timedelta(days=i), 1000 + 100 * i) for i in range(40)]  # até ontem
    posts = [_post("p", pub=AGORA - timedelta(days=60))]

    rede = _montar(posts, linhas, dias=7, inicio_da_coleta=ini)["resumo"]["redes"][0]
    assert rede["no_periodo"] == {"views": 600}, "o título mostra o que já rendeu, hoje parcial"
    assert rede["comparacao"] == {
        "atual": 700,
        "anterior": 700,
        "ate": (hoje - timedelta(days=1)).isoformat(),
    }
    assert rede["periodo_anterior"] == 700

    # Depois da leitura da noite, hoje fechou e entra na comparação.
    linhas.append(_noite(hoje, 1000 + 100 * 40))
    depois = datetime(2026, 9, 25, 2, 50, tzinfo=UTC)  # 23:50 de 24/09 em Brasília
    rede = _montar(posts, linhas, dias=7, inicio_da_coleta=ini, agora=depois)["resumo"]["redes"][0]
    assert rede["comparacao"] == {"atual": 700, "anterior": 700, "ate": hoje.isoformat()}
    # …mas não enquanto a leitura da noite ainda está rodando.
    rodando = _montar(
        posts, linhas, dias=7, inicio_da_coleta=ini, agora=depois, coleta={"em_andamento": True}
    )
    assert (
        rodando["resumo"]["redes"][0]["comparacao"]["ate"] == (hoje - timedelta(days=1)).isoformat()
    )


def _estacionario(agora: datetime) -> tuple[list[dict], list[dict]]:
    """Um vídeo por dia às 12:04, todos com a mesma curva, lidos toda noite
    enquanto têm até 90 dias de vida — como a coleta faz. O fluxo de cada dia
    é o mesmo: qualquer comparação honesta dá 0%."""
    hoje = agora.astimezone(d.BRT).date()
    ganho = [max(1, 300 // (k + 1)) for k in range(90)]  # cauda longa
    posts, linhas = [], []
    for n in range(280, -1, -1):
        dia_pub = hoje - timedelta(days=n)
        pub = datetime.combine(dia_pub, time(12, 4), d.BRT)
        if pub > agora:
            continue
        pid = f"v{n}"
        posts.append(_post(pid, pub=pub, creative_id=f"c{n}"))
        v = 0
        for k in range(90):
            noite = _noite(dia_pub + timedelta(days=k), 0, pid)
            if noite["lido_em"] > agora:
                break
            v += ganho[k]
            noite["views"] = v
            linhas.append(noite)
    return posts, linhas


def test_fluxo_parado_da_zero_por_cento_em_7_30_e_90_dias():
    """O período anterior soma o que rendeu quem a coleta lia NAQUELES dias —
    inclusive o post que hoje já passou de 90 dias e saiu da tela. Sem ele,
    o anterior ficava menor que o atual (com 90 dias, quase zero: "▲ 42322%")."""
    posts, linhas = _estacionario(AGORA)
    inicio = AGORA.astimezone(d.BRT).date() - timedelta(days=280)
    for dias in (7, 30, 90):
        corte = d.corte_universo(AGORA, dias)
        tela = [p for p in posts if p["publicado_em"] >= corte]
        antigos = [p for p in posts if d.corte_soma(AGORA, dias) <= p["publicado_em"] < corte]
        r = _montar(tela, linhas, dias=dias, inicio_da_coleta=inicio, posts_antigos=antigos)
        [rede] = r["resumo"]["redes"]
        c = rede["comparacao"]
        assert c["anterior"] is not None and c["atual"] == c["anterior"], (dias, c)
        # Os antigos só somam: não viram linha nem vídeo contado.
        assert {p["postagem_id"] for p in r["postagens"]} == {p["id"] for p in tela}
        assert rede["videos"] == len(tela) - 1, "o de hoje ainda aguarda a 1ª leitura"
        presentes = [v for v in rede["serie"] if v is not None]
        assert sum(presentes) == rede["no_periodo"]["views"], "série e título fecham"


def test_rede_sem_leitura_ainda_nao_e_sem_views():
    """ "Views indisponíveis: a conta não liberou insights" é do Instagram sem
    permissão. TikTok recém-publicado (aguardando a 1ª leitura) e YouTube com
    a leitura falhando desde a 1ª ainda não disseram nada — não são isso."""
    posts = [
        _post("tt", pub=AGORA - timedelta(minutes=15)),
        _post("yt", pub=AGORA - timedelta(days=1), plataforma="youtube", rede_social_id="yt"),
    ]
    quando = AGORA - timedelta(hours=1)
    linhas = [
        {
            "postagem_id": "yt",
            "dia": quando,
            "lido_em": None,
            "updated_at": quando,
            "erro": "HTTPStatusError: 401",
            "views": None,
        }
    ]
    r = _montar(posts, linhas)
    redes = {x["plataforma"]: x for x in r["resumo"]["redes"]}
    assert redes["tiktok"]["aguardando"] == 1 and redes["tiktok"]["sem_views"] is False
    assert redes["youtube"]["com_falha"] == 1 and redes["youtube"]["sem_views"] is False
    assert redes["tiktok"]["metrica_serie"] == "views"
    assert r["resumo"]["tendencia"]["redes_sem_views"] == []


# ---------- conta e autor ----------


def test_norm_conta():
    assert d.norm_conta("@Uranyx_BR ") == "uranyx_br"
    assert d.norm_conta("https://www.tiktok.com/@uranyx_br") == "uranyx_br"
    assert d.norm_conta("https://www.tiktok.com/@uranyx_br/video/123") == "uranyx_br"
    assert d.norm_conta("uranyx_br") == "uranyx_br"
    assert d.norm_conta(None) is None
    assert d.norm_conta("  ") is None


def test_autor_diferente_so_quando_handle_difere():
    """Pista, não exclusão: o TikTok diz de quem é o vídeo."""
    pub = AGORA - timedelta(days=2)
    tt = _post("p", pub=pub, conta="uranyx_brasil", conta_atual="Uranyx_BR")
    outra = [_l(pub, 30, 10, autor={"handle": "uranyx_brasil", "id": "1"})]
    mesma = [_l(pub, 30, 10, autor={"handle": "uranyx_br", "id": "2"})]
    assert d.autor_diferente(tt, outra) == "uranyx_brasil"
    assert d.autor_diferente(tt, mesma) is None, "compara com o nome ATUAL da conta"
    assert d.autor_diferente(tt, [_l(pub, 30, 10)]) is None, "sem autor, sem pista"
    yt = _post("y", pub=pub, plataforma="youtube", conta_atual="uranyx_br")
    assert d.autor_diferente(yt, outra) is None, "só o TikTok diz o autor"


def test_marco_pronto_em_e_a_primeira_leitura_da_noite_depois_da_idade():
    """Post das 12:04 BRT de 24/09: o D+3 fica pronto na leitura das 23:47 de
    27/09 (02:47 UTC de 28/09)."""
    pub = datetime(2026, 9, 24, 15, 4, tzinfo=UTC)
    r = _montar([_post("p", pub=pub)], [], agora=pub + timedelta(minutes=5))
    p = r["postagens"][0]
    assert p["estado"] == "aguardando"
    assert p["marco_pronto_em"] == "2026-09-28T02:47:00.000Z"
    assert r["coleta"]["proxima_leitura_em"] == "2026-09-24T15:47:00.000Z"
    assert r["coleta"]["proxima_noturna_em"] == "2026-09-25T02:47:00.000Z"
