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
  - VIEWS SOMADAS, SEMANA E MÊS (06/10/2026). "Onde vale investir" mostra
    as views que os vídeos de cada grupo GANHARAM na semana e no mês (dias de
    calendário de Brasília; vídeo antigo que continua rendendo conta, e a soma
    fecha com o Resumo) e quantos vídeos foram publicados nesses dias. O
    produto agrupa por APARELHO: cores, tamanhos e cadastros do mesmo aparelho
    viram uma linha, e o vídeo de várias cores conta no aparelho inteiro.
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
    """Um vídeo em 3 redes é UM vídeo, com as views das 3 somadas: contar 3
    faria o grupo parecer mais testado do que é."""
    posts, linhas = _juntos(
        _video("cx", pub=AGORA - timedelta(days=10),
               views={"instagram": 200, "youtube": 200, "tiktok": 200}, modelo="video 15s"),
        _video("outro", pub=AGORA - timedelta(days=10), views={"tiktok": 100}, modelo="video 30s"),
    )
    g = {x["chave"]: x for x in _montar(posts, linhas)["grupos"]["formato"]}
    assert g["15s"]["mes"]["videos"] == 1
    assert g["15s"]["mes"]["views"] == 600
    assert g["15s"]["semana"]["videos"] == 0, "publicado há 10 dias: fora da semana"
    assert g["15s"]["semana"]["views"] is None, "e não ganhou nada nela"
    assert g["15s"]["unidade"] == "criativo"
    assert g["15s"]["melhor"]["creative_id"] == "cx"
    assert g["15s"]["por_rede_mes"] == {"instagram": 200, "youtube": 200, "tiktok": 200}
    assert list(g["15s"]["por_rede_mes"]) == ["instagram", "youtube", "tiktok"], "ordem das redes"


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
    med = g["15s"]["mes"]["mediana"]
    assert med == 2050 and isinstance(med, int)
    assert g["15s"]["mes"]["media"] == 2050 and isinstance(g["15s"]["mes"]["media"], int)


def _video(cid: str, *, pub: datetime, views: dict[str, int | None], **kw):
    """Um criativo com um post por rede; views None = aguardando a 1ª leitura."""
    posts, linhas = [], []
    for rede, v in views.items():
        pid = f"{cid}-{rede}"
        posts.append(
            _post(pid, pub=pub, creative_id=cid, plataforma=rede, rede_social_id=rede,
                  post_url=f"https://x/{pid}", **kw)
        )
        if v is not None:
            linhas.append(_l(pub, 2, v, postagem_id=pid))
    return posts, linhas


def _juntos(*videos):
    return [p for ps, _ in videos for p in ps], [r for _, ls in videos for r in ls]


def test_grupos_ordenam_por_views_do_mes_e_nenhum_por_ultimo():
    """Mais views no mês primeiro. "(sem agência)" junta a maior parte dos
    vídeos e, no topo, esconderia a resposta: vai sempre pro fim."""
    pub = AGORA - timedelta(days=3)
    posts, linhas = _juntos(
        _video("a", pub=pub, views={"tiktok": 100}, equipe="Pequena"),
        _video("b", pub=pub, views={"tiktok": 900}, equipe="Grande"),
        _video("c", pub=pub, views={"tiktok": 5000}, equipe=None),
        _video("d", pub=pub, views={"tiktok": None}, equipe="Nova"),
        _video("e", pub=pub, views={"tiktok": 100}, equipe="Outra"),
        _video("f", pub=pub, views={"tiktok": 0}, equipe="Outra"),
    )
    g = _montar(posts, linhas)["grupos"]["agencia"]
    assert [x["rotulo"] for x in g] == ["Grande", "Outra", "Pequena", "Nova", "(sem agência)"]
    outra = g[1]
    assert outra["mes"] == {
        "views": 100, "videos": 2, "com_numero": 2, "views_dos_publicados": 100,
        "media": 50, "mediana": 50,
    }
    nova = g[3]
    assert nova["mes"]["videos"] == 1 and nova["mes"]["views"] is None, "sem número: nulo, não 0"
    assert nova["melhor"] is None


def test_produto_dois_cadastros_do_mesmo_aparelho_viram_um_grupo():
    """O F105 tem dois cadastros no Bling com o MESMO nome (dg019.ra e
    dg019.sp) e aparecia duas vezes na tela (06/10/2026). Agrupado por
    aparelho, é um produto só, e o grupo diz a cor e quais SKUs juntou."""
    nome = "Uranyx F105 12.64 - Preto + cartão 64GB"
    posts, linhas = _juntos(
        _video("turismo", pub=AGORA - timedelta(days=8), views={"youtube": 1050, "tiktok": 800},
               product_id="3aec", produto_nome=nome, produto_sku="dg019.sp", sku="dg019.sp",
               modelo="Turismo Jurássico 2"),
        _video("saque", pub=AGORA - timedelta(days=2), views={"instagram": 6755, "tiktok": 1607},
               product_id="bc31", produto_nome=nome, produto_sku="dg019.ra", sku="dg019",
               modelo="Saque Rápido"),
    )
    r = _montar(posts, linhas)
    [g] = r["grupos"]["produto"]
    assert g["chave"] == "dev:uranyx f105 12.64"
    assert g["rotulo"] == "Uranyx F105 12.64"
    assert g["detalhe"] == "Preto + cartão 64GB · SKU dg019.ra, dg019.sp"
    assert g["mes"]["videos"] == 2 and g["mes"]["views"] == 10212
    assert g["semana"]["videos"] == 1 and g["semana"]["views"] == 8362
    assert g["melhor"]["nome"] == "Saque Rápido" and g["melhor"]["views"] == 8362
    por_id = {c["creative_id"]: c for c in r["criativos"]}
    assert por_id["saque"]["produto"] == {
        "chave": "dev:uranyx f105 12.64",
        "rotulo": "Uranyx F105 12.64",
        "skus": ["dg019.ra"],
        "variantes": ["Preto + cartão 64GB"],
    }, '"dg019" digitado junto do produto dg019.ra é o mesmo cadastro'


def test_criativo_so_com_sku_junta_no_produto_da_mesma_base():
    """Criativo sem produto ligado, só com o SKU digitado ("dg046"), é o mesmo
    aparelho do criativo ligado ao dg046.pi. O nome dele vem do produto ativo
    com a mesma base (`nomes_por_base`, que o router busca)."""
    nomes = {"dg046": ["Uranyx F110L 8.128 - Preto"], "dg017": "Uranyx F109S 24.256 - Preto"}
    pub = AGORA - timedelta(days=1)
    posts, linhas = _juntos(
        _video("ligado", pub=pub - timedelta(days=5), views={"tiktok": 800},
               product_id="u1", produto_nome="Uranyx F110L 8.128 - Preto", produto_sku="dg046.pi",
               sku="dg046.sp"),
        _video("solto", pub=pub, views={"tiktok": 1200}, sku="dg046"),
        _video("so-sku", pub=pub, views={"tiktok": 300}, sku="dg017.pi"),
        _video("sem-nome", pub=pub, views={"tiktok": 50}, sku="zz999.ci, zz998.ci"),
    )
    r = _montar(posts, linhas, nomes_por_base=nomes)
    g = {x["chave"]: x for x in r["grupos"]["produto"]}
    f110 = g["dev:uranyx f110l 8.128"]
    assert f110["mes"]["videos"] == 2 and f110["mes"]["views"] == 2000
    assert f110["rotulo"] == "Uranyx F110L 8.128"
    assert f110["detalhe"] == "Preto · SKU dg046, dg046.pi, dg046.sp"
    assert g["dev:uranyx f109s 24.256"]["rotulo"] == "Uranyx F109S 24.256", "nome pela base"
    sem = g["sku:zz998+zz999"]
    assert sem["rotulo"] == "SKU zz998, zz999 (sem produto ligado)"
    assert sem["detalhe"] == "SKU zz998.ci, zz999.ci"
    solto = next(c for c in r["criativos"] if c["creative_id"] == "solto")
    assert solto["produto"]["rotulo"] == "Uranyx F110L 8.128"


def test_janelas_sao_dias_de_calendario_em_brasilia():
    """Semana = hoje e os 6 dias antes; mês = hoje e os 29 antes — em dias de
    Brasília. Às 15h de 24/09, o vídeo das 23:30 de 17/09 (02:30 UTC de 18/09)
    é PUBLICADO no mês e NÃO na semana; o das 00:10 de 18/09 é da semana.

    As views da janela são as GANHAS nela: o das 23:30 de 17/09 foi lido às
    01:30 de 18/09 com 10 views, e a leitura divide o ganho entre 17 e 18/09
    — os 5 do dia 18 são da semana, mesmo ele não sendo publicado nela."""
    def brt(*a):
        return datetime(*a, tzinfo=d.BRT)

    posts, linhas = _juntos(
        _video("fora-semana", pub=brt(2026, 9, 17, 23, 30), views={"tiktok": 10}, equipe="A"),
        _video("dentro-semana", pub=brt(2026, 9, 18, 0, 10), views={"tiktok": 20}, equipe="A"),
        _video("limite-mes", pub=brt(2026, 8, 26, 0, 5), views={"tiktok": 40}, equipe="A"),
        _video("fora-mes", pub=brt(2026, 8, 25, 23, 55), views={"tiktok": 80}, equipe="A"),
    )
    assert posts[0]["publicado_em"].astimezone(UTC).day == 18, "em UTC já é dia 18"
    r = _montar(posts, linhas)
    assert r["janelas"] == {
        "semana": {"dias": 7, "desde": "2026-09-18"},
        "mes": {"dias": 30, "desde": "2026-08-26"},
    }
    idade = {c["creative_id"]: c["idade_dias"] for c in r["criativos"]}
    assert idade == {"fora-semana": 7, "dentro-semana": 6, "limite-mes": 29, "fora-mes": 30}
    [g] = r["grupos"]["agencia"]
    assert g["semana"]["videos"] == 1 and g["semana"]["views"] == 20 + 5
    assert g["semana"]["views_dos_publicados"] == 20, "os publicados na semana têm 20 até hoje"
    assert g["mes"]["videos"] == 3 and g["mes"]["views"] == 10 + 20 + 40 + 40
    assert g["mes"]["views_dos_publicados"] == 70


def test_grupo_sem_video_no_mes_nao_aparece():
    posts, linhas = _juntos(
        _video("velho", pub=AGORA - timedelta(days=45), views={"tiktok": 9000}, equipe="Antiga"),
        _video("novo", pub=AGORA - timedelta(days=2), views={"tiktok": 10}, equipe="Atual"),
    )
    r = _montar(posts, linhas)
    assert [g["rotulo"] for g in r["grupos"]["agencia"]] == ["Atual"]
    assert {c["creative_id"] for c in r["criativos"]} == {"velho", "novo"}, "a lista tem os 90 dias"


def test_video_aguardando_conta_como_video_e_nao_soma_views():
    pub = AGORA - timedelta(hours=1)
    posts, linhas = _juntos(
        _video("lido", pub=pub, views={"tiktok": 300}, equipe="A"),
        _video("novo", pub=pub, views={"tiktok": None, "youtube": None}, equipe="A"),
    )
    r = _montar(posts, linhas)
    [g] = r["grupos"]["agencia"]
    assert g["mes"] == {
        "views": 300, "videos": 2, "com_numero": 1, "views_dos_publicados": 300,
        "media": 300, "mediana": 300,
    }
    novo = next(c for c in r["criativos"] if c["creative_id"] == "novo")
    assert novo["views"] == {"total": None, "por_rede": {}, "postagens": 2, "com_numero": 0}
    assert novo["melhor_post"] is None
    assert r["criativos"][-1]["creative_id"] == "novo", "sem número vai pro fim da lista"


def test_post_apagado_e_tirado_do_desempenho_nao_contam():
    pub = AGORA - timedelta(days=2)
    posts, linhas = _juntos(
        _video("c", pub=pub, views={"tiktok": 100, "youtube": 500, "instagram": 700}, equipe="A"),
    )
    quando = AGORA - timedelta(hours=1)
    linhas.append({"postagem_id": "c-youtube", "dia": quando, "lido_em": None,
                   "updated_at": quando, "erro": f"{REMOVIDO} saiu do ar", "views": None})
    next(p for p in posts if p["id"] == "c-instagram")["fora_do_desempenho_em"] = quando
    r = _montar(posts, linhas)
    [c] = r["criativos"]
    assert c["views"] == {
        "total": 100, "por_rede": {"tiktok": 100}, "postagens": 1, "com_numero": 1
    }
    [g] = r["grupos"]["agencia"]
    assert g["mes"]["views"] == 100 and g["por_rede_mes"] == {"tiktok": 100}


def test_views_do_video_somam_todas_as_redes_e_posts_repetidos():
    """Dois posts do mesmo vídeo no TikTok somam. O link do vídeo é o post
    mais visto; empate, o mais novo."""
    pub = AGORA - timedelta(days=4)
    posts, linhas = _juntos(
        _video("c", pub=pub, views={"instagram": 6755, "youtube": 1159, "facebook": 0}),
    )
    for pid, quando, v in (("tt1", pub, 300), ("tt2", pub + timedelta(days=1), 148)):
        posts.append(_post(pid, pub=quando, creative_id="c", plataforma="tiktok",
                           post_url=f"https://x/{pid}"))
        linhas.append(_l(quando, 2, v, postagem_id=pid))
    r = _montar(posts, linhas)
    [c] = r["criativos"]
    assert c["views"]["total"] == 8362
    assert c["views"]["por_rede"] == {
        "instagram": 6755, "youtube": 1159, "tiktok": 448, "facebook": 0
    }
    assert list(c["views"]["por_rede"]) == ["instagram", "youtube", "tiktok", "facebook"]
    assert c["views"]["postagens"] == 5 and c["views"]["com_numero"] == 5
    assert c["melhor_post"] == {"plataforma": "instagram", "postagem_id": "c-instagram",
                                "post_url": "https://x/c-instagram", "views": 6755}
    # Empate: o post mais novo.
    posts2, linhas2 = _juntos(_video("e", pub=pub, views={"instagram": 50}))
    posts2.append(_post("e2", pub=pub + timedelta(hours=5), creative_id="e", plataforma="youtube"))
    linhas2.append(_l(pub + timedelta(hours=5), 2, 50, postagem_id="e2"))
    [e] = _montar(posts2, linhas2)["criativos"]
    assert e["melhor_post"]["postagem_id"] == "e2"


def test_melhor_do_grupo_e_o_video_mais_visto_com_o_link_do_post_mais_visto():
    pub = AGORA - timedelta(days=2)
    posts, linhas = _juntos(
        _video("fraco", pub=pub, views={"tiktok": 900}, equipe="A", modelo="Fraco"),
        _video("forte", pub=pub, views={"tiktok": 400, "youtube": 1100}, equipe="A",
               modelo="Forte", legenda="Legenda do forte"),
    )
    [g] = _montar(posts, linhas)["grupos"]["agencia"]
    assert g["melhor"] == {
        "creative_id": "forte", "postagem_id": "forte-youtube", "nome": "Forte",
        "titulo": "Legenda do forte", "views": 1500, "plataforma": "youtube",
        "post_url": "https://x/forte-youtube",
    }


def test_horario_conta_postagens_cada_uma_na_sua_janela():
    """Horário é do POST: o mesmo vídeo saiu às 12h numa rede e às 19h noutra,
    e cada post entra na janela pela data dele. As views da janela são as que
    os posts daquele horário ganharam nela."""
    def brt(*a):
        return datetime(*a, tzinfo=d.BRT)

    posts = [
        _post("ig", pub=brt(2026, 9, 17, 12, 4), creative_id="c", plataforma="instagram",
              post_url="https://x/ig"),
        _post("tt", pub=brt(2026, 9, 18, 19, 3), creative_id="c", plataforma="tiktok",
              post_url="https://x/tt"),
        _post("yt", pub=brt(2026, 9, 24, 12, 10), creative_id="c", plataforma="youtube"),
    ]
    # Lidos 30 h depois: o ganho se divide entre o dia da publicação e o seguinte.
    linhas = [_l(posts[0]["publicado_em"], 30, 700, postagem_id="ig"),
              _l(posts[1]["publicado_em"], 30, 90, postagem_id="tt")]
    g = {x["chave"]: x for x in _montar(posts, linhas)["grupos"]["horario"]}
    assert g["12h"]["unidade"] == "postagem"
    assert g["12h"]["mes"]["videos"] == 2 and g["12h"]["mes"]["views"] == 700
    assert g["12h"]["semana"] == {
        "views": 350, "videos": 1, "com_numero": 0, "views_dos_publicados": None,
        "media": None, "mediana": None,
    }, "publicado na semana: só o do YouTube (aguardando); ganho na semana: o 18/09 do Instagram"
    assert g["12h"]["melhor"]["post_url"] == "https://x/ig"
    assert g["12h"]["melhor"]["postagem_id"] == "ig"
    assert g["19h"]["semana"] == {
        "views": 90, "videos": 1, "com_numero": 1, "views_dos_publicados": 90,
        "media": 90, "mediana": 90,
    }
    assert g["19h"]["por_rede_mes"] == {"tiktok": 90}


def test_nome_do_video():
    """Desde 29/09 o `modelo` é o título da ideia. Quando é só a duração, o
    nome cai pra legenda (1ª linha) e depois pro roteiro. Aspas saem."""
    assert d.nome_do_video("Saque Rápido", "Aparelho que…", None) == "Saque Rápido"
    assert d.nome_do_video('"Caiu do barco"', "x", None) == "Caiu do barco"
    assert d.nome_do_video('Ninja Jiraya"', "x", None) == "Ninja Jiraya"
    legenda = "Bateria que dura ⚡\nlinha 2"
    assert d.nome_do_video("video 15s", legenda, "R") == "Bateria que dura ⚡"
    assert d.nome_do_video("Vídeo de 30 segundos", "", "Roteiro X") == "Roteiro X"
    assert d.nome_do_video("  “ ” ", None, None) == ""
    assert d.nome_do_video("Próxima Parada: o Castelo (30 s, 9:16)", "x", None) == (
        "Próxima Parada: o Castelo (30 s, 9:16)"
    ), "duração no meio do título não é modelo genérico"
    r = _montar(*_video("c", pub=AGORA - timedelta(days=1), views={"tiktok": 5},
                        modelo="video 15s", legenda="Tecnologia que aguenta o teu dia 🔋"))
    assert r["criativos"][0]["nome"] == "Tecnologia que aguenta o teu dia 🔋"


def test_base_sku():
    assert d.base_sku("dg019.ra") == "dg019"
    assert d.base_sku(" DG089.ci, dg088.ci") == "dg089"
    assert d.base_sku("dg019+ca064") == "dg019"
    assert d.base_sku("b055.24") == "b055"
    assert d.base_sku("  ") is None
    assert d.base_sku(None) is None


def test_formato_extrai_duracao_do_modelo():
    assert d.grupo_formato("video 15s") == ("15s", "vídeo 15s")
    assert d.grupo_formato("Vídeo de 30 segundos") == ("30s", "vídeo 30s")
    # O nome do celular não é duração de vídeo.
    assert d.grupo_formato("F109S 256 GB") == ("nenhum", "(formato não informado)")
    assert d.grupo_formato(None) == ("nenhum", "(formato não informado)")


def test_produto_cai_na_base_do_sku_sem_produto_ligado():
    def gp(*a):
        g = d.grupo_produto(*a)
        return g["chave"], g["rotulo"], g["skus"], g["variantes"]

    assert gp(None, None, None, "dg017.pi") == (
        "sku:dg017", "SKU dg017 (sem produto ligado)", ["dg017.pi"], []
    )
    assert gp(None, None, None, "dg017.pi", {"dg017": "Uranyx F109S - Preto"}) == (
        "dev:uranyx f109s", "Uranyx F109S", ["dg017.pi"], ["Preto"]
    )
    # Ligado: o produto manda; SKU digitado que não é produto nenhum não abre linha.
    assert gp("u1", "Fone DG017", "dg017.pi", "dg099.sp") == (
        "dev:fone dg017", "Fone DG017", ["dg017.pi"], []
    )
    assert gp("u1", "Sem SKU", None, None) == ("dev:sem sku", "Sem SKU", [], [])
    assert gp("u1", None, None, None) == ("prod:u1", "(produto sem nome)", [], [])
    assert gp(None, None, None, "  ") == ("nenhum", "(sem produto)", [], [])
    # Nem um SKU errado ao lado de um certo.
    assert gp(None, None, None, "dg017.pi, zz1", {"dg017": ["Uranyx F109S - Preto"]})[0] == (
        "dev:uranyx f109s"
    )


def test_aparelho_tira_cor_tamanho_e_codigo_do_fornecedor():
    """O nome do Bling diz modelo + cor ("- Vermelho") e, na mala, o tamanho;
    o aparelho é o que sobra. "- S5 Edition" não é cor e fica."""
    casos = {
        "Uranyx F112 Pro 5G 24.256 - Vermelho": ("Uranyx F112 Pro 5G 24.256", "Vermelho"),
        "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata": (
            "Uranyx A18 Pro Max - S5 Edition 16.128", "Prata",
        ),
        "Uranyx F105 12.64 - Preto + cartão 64GB": ("Uranyx F105 12.64", "Preto + cartão 64GB"),
        "Mala Sorriso M6 tamanho 24 - Branco (DT - DTLG115 - DT16)": (
            "Mala Sorriso M6", "Branco tam. 24",
        ),
        "Mala Escudo P4 tamanho 24 - Verde Escuro (DT - PPDT803 - DT13)": (
            "Mala Escudo P4", "Verde Escuro tam. 24",
        ),
        "Kit Malas Sorriso M6 tamanho 12.18 Polegadas - Branca": (
            "Kit Malas Sorriso M6", "Branca tam. 12.18",
        ),
        "Uranyx Fossibot F109S 24.256 - KIT 2": ("Uranyx Fossibot F109S 24.256 - KIT 2", None),
        "Fone sem cor": ("Fone sem cor", None),
    }
    for nome, esperado in casos.items():
        assert d.aparelho(nome) == esperado, nome


def test_video_de_varias_cores_conta_no_aparelho_inteiro():
    """Na fila de 06/10 há 5 vídeos do F112 com "dg082, dg083, dg084, dg085,
    dg086" (as 5 cores) e o A18 com "dg089.ci, dg088.ci". Contar tudo na
    primeira cor inflava o F112 Vermelho e escondia as outras cores: o grupo é
    o APARELHO, e a linha cinza diz as cores e os SKUs."""
    f112 = "Uranyx F112 Pro 5G 24.256"
    a18 = "Uranyx A18 Pro Max - S5 Edition 16.128"
    por_base = {
        "dg082": [f"{f112} - Vermelho"], "dg083": [f"{f112} - Azul"],
        "dg088": [f"{a18} - Laranja"], "dg089": [f"{a18} - Prata"],
    }
    por_sku = {"dg082.ra": f"{f112} - Vermelho", "dg088.ci": f"{a18} - Laranja",
               "dg089.ci": f"{a18} - Prata"}
    pub = AGORA - timedelta(days=1)
    posts, linhas = _juntos(
        _video("vermelho", pub=pub, views={"tiktok": 500}, product_id="p82",
               produto_nome=f"{f112} - Vermelho", produto_sku="dg082.ra", sku="dg082"),
        _video("cores", pub=pub, views={"tiktok": 900}, sku="dg082, dg083"),
        _video("a18-duas", pub=pub, views={"tiktok": 300}, product_id="p89",
               produto_nome=f"{a18} - Prata", produto_sku="dg089.ci", sku="dg089.ci, dg088.ci"),
        _video("a18-laranja", pub=pub, views={"tiktok": 100}, sku="dg088.ci"),
    )
    r = _montar(posts, linhas, nomes_por_base=por_base, nomes_por_sku=por_sku)
    g = {x["chave"]: x for x in r["grupos"]["produto"]}
    assert set(g) == {"dev:uranyx f112 pro 5g 24.256", "dev:uranyx a18 pro max - s5 edition 16.128"}
    x = g["dev:uranyx f112 pro 5g 24.256"]
    assert x["rotulo"] == f112
    assert x["mes"]["videos"] == 2 and x["mes"]["views"] == 1400
    assert x["detalhe"] == "Azul, Vermelho · SKU dg082, dg082.ra, dg083"
    y = g["dev:uranyx a18 pro max - s5 edition 16.128"]
    assert y["mes"]["videos"] == 2 and y["mes"]["views"] == 400
    assert y["detalhe"] == "Laranja, Prata · SKU dg088.ci, dg089.ci"
    cores = next(c for c in r["criativos"] if c["creative_id"] == "cores")
    assert cores["produto"]["skus"] == ["dg082", "dg083"]
    assert cores["produto"]["variantes"] == ["Azul", "Vermelho"]
    # Dois aparelhos diferentes no mesmo vídeo: linha própria, sem contar em dobro.
    dois = d.grupo_produto(None, None, None, "dg082, dg088.ci", por_base, por_sku)
    assert dois["rotulo"] == f"{a18} + {f112}"
    assert dois["chave"] == (
        "dev:uranyx a18 pro max - s5 edition 16.128+dev:uranyx f112 pro 5g 24.256"
    )


def test_malas_de_tamanhos_diferentes_sao_a_mesma_mala_e_o_sku_exato_da_o_tamanho():
    """A base b055 é a Mala Sorriso M6 em 5 tamanhos (e kits). O SKU exato diz
    o tamanho ("b055.20" é a de 20, não a de 8, que é o nome mais curto da
    base); pela base só, o tamanho não é afirmado. As duas malas são uma linha,
    e a linha cinza lista os tamanhos."""
    def mala(t):
        return f"Mala Sorriso M6 tamanho {t} - Branco (DT - DTLG115 - DT16)"

    por_base = {"b055": [mala(t) for t in (8, 12, 18, 20, 24)]}
    por_sku = {"b055.20": mala(20), "b055.24": mala(24), "b055": "Kit 6 Malas Sorriso M6 - Branco"}
    pub = AGORA - timedelta(days=1)
    posts, linhas = _juntos(
        _video("m24", pub=pub, views={"tiktok": 700}, product_id="p24",
               produto_nome=mala(24), produto_sku="b055.24", sku="b055.24"),
        _video("m20", pub=pub, views={"tiktok": 300}, sku="b055.20"),
    )
    r = _montar(posts, linhas, nomes_por_base=por_base, nomes_por_sku=por_sku)
    [g] = r["grupos"]["produto"]
    assert g["chave"] == "dev:mala sorriso m6" and g["rotulo"] == "Mala Sorriso M6"
    assert g["mes"]["videos"] == 2 and g["mes"]["views"] == 1000
    assert g["detalhe"] == "Branco tam. 20, Branco tam. 24 · SKU b055.20, b055.24"
    # Sem o SKU exato: a mala, sem tamanho (a base tem 5).
    so_base = d.grupo_produto(None, None, None, "b055.20", por_base)
    assert (so_base["rotulo"], so_base["variantes"]) == ("Mala Sorriso M6", [])
    # "b055" sozinho é a mala, não o kit que tem esse SKU exato.
    assert d.grupo_produto(None, None, None, "b055", por_base, por_sku)["rotulo"] == (
        "Mala Sorriso M6"
    )


def test_views_da_janela_sao_as_ganhas_nela_inclusive_por_video_antigo():
    """Eduardo quer "quantas views cada agência atraiu na semana". Vídeo de 3
    semanas que continua rendendo atrai views na semana — e somar só os vídeos
    publicados na semana perdia isso (Bill Gates: 39.169 na tela contra 41.851
    ganhas, 06/10/2026). Agora a semana soma o que TODOS os vídeos do grupo
    ganharam nela, a mesma conta do Resumo: com 7 dias, os grupos fecham com o
    Resumo. Até post mais velho que a tela (que a coleta ainda lia) entra."""
    hoje = AGORA.astimezone(d.BRT).date()
    pub_velho = AGORA - timedelta(days=20)
    posts = [
        _post("velho", pub=pub_velho, creative_id="cv", equipe="A", product_id="pf",
              produto_nome="Uranyx F117 24.256 - Preto", produto_sku="dg048.ra"),
        _post("novo", pub=AGORA - timedelta(days=2), creative_id="cn", equipe="A"),
        _post("trimestre", pub=AGORA - timedelta(days=60), creative_id="ct", equipe="B"),
    ]
    linhas = [
        # Lido desde o nascimento: 1.000 no dia em que saiu, parado até hoje-7,
        # e +300 de hoje-6 a hoje-1 (50 por dia) — tudo dentro da semana.
        _l(pub_velho, 2, 1000, postagem_id="velho"),
        _noite(hoje - timedelta(days=7), 1000, "velho"),
        _noite(hoje - timedelta(days=1), 1300, "velho"),
        _l(posts[1]["publicado_em"], 2, 500, postagem_id="novo"),
        # Lido só depois de velho (a 1ª leitura é base): +70 de hoje-9 a
        # hoje-3, 10 por dia — 40 deles na semana.
        _noite(hoje - timedelta(days=10), 4000, "trimestre"),
        _noite(hoje - timedelta(days=3), 4070, "trimestre"),
    ]
    # Mais velho que a tela (95 dias): só soma, não vira linha. +40 de
    # hoje-11 a hoje-5 (5,5,6,6,6,6,6) — 12 na semana.
    antigo = _post("antigo", pub=AGORA - timedelta(days=95), creative_id="ca", equipe="B")
    linhas += [_noite(hoje - timedelta(days=12), 9000, "antigo"),
               _noite(hoje - timedelta(days=5), 9040, "antigo")]
    r = _montar(posts, linhas, dias=7, posts_antigos=[antigo])
    g = {x["rotulo"]: x for x in r["grupos"]["agencia"]}
    a, b = g["A"], g["B"]
    assert a["semana"]["views"] == 300 + 500, "o velho conta o que ganhou na semana"
    assert a["semana"]["videos"] == 1 and a["semana"]["views_dos_publicados"] == 500
    assert a["mes"]["views"] == 1300 + 500 and a["mes"]["videos"] == 2
    assert a["mes"]["views_dos_publicados"] == 1800 and a["mes"]["media"] == 900
    # B não publicou nada no mês, mas atraiu views nele: aparece, com 0 vídeos.
    assert b["semana"] == {
        "views": 40 + 12, "videos": 0, "com_numero": 0, "views_dos_publicados": None,
        "media": None, "mediana": None,
    }
    assert b["mes"]["views"] == 70 + 40 and b["mes"]["videos"] == 0
    assert b["melhor"] is None, "nenhum vídeo dele publicado no mês"
    # A soma dos grupos na semana é o Resumo de 7 dias, em toda dimensão.
    resumo = r["resumo"]["tendencia"]["views_no_periodo"]
    assert resumo == 300 + 500 + 40 + 12
    for dim in ("agencia", "produto", "formato", "roteiro", "horario"):
        assert sum(x["semana"]["views"] or 0 for x in r["grupos"][dim]) == resumo, dim
    f117 = next(x for x in r["grupos"]["produto"] if x["rotulo"] == "Uranyx F117 24.256")
    assert f117["semana"]["videos"] == 0 and f117["semana"]["views"] == 300


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
