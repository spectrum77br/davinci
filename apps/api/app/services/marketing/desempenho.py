"""Qual vídeo rendeu mais, e onde vale investir — as contas da tela (24/09/2026).

Pedido do Eduardo: "trackear o que cada vídeo deu de retorno pra saber o que
investir", "mais robusta / melhor visualmente pra entender". A tela de 23/09
somava o acumulado de cada vídeo, e isso responde a pergunta errada: vídeo de
um mês sempre ganha do de ontem só por ter tido mais tempo, e conta grande
sempre ganha de conta pequena só por ser grande. Aqui moram as três decisões
que consertam isso, todas sem banco (o router carrega as linhas e chama
`montar`, e o teste chama as funções direto):

  MESMA IDADE    todo vídeo é comparado pelas views que tinha com N dias de
                 publicado (D+1, D+3, D+7), interpoladas entre as duas leituras
                 que cercam essa idade. Sem leitura perto (36h), fica NULO — não
                 se inventa número. D+3 é o padrão: com a leitura no fim do dia,
                 a interpolação do D+1 fica uns 5% abaixo (igual pros posts das
                 12h e das 19h); a do D+3, menos de 1%.

  "× O NORMAL"   o índice divide as views na idade pela MEDIANA dos OUTROS
                 vídeos da mesma conta na mesma rede (últimos 90 dias). 2,0× =
                 o dobro do normal daquela conta. Assim conta pequena não perde
                 por ser pequena, e Instagram nunca é comparado direto com
                 TikTok — "view" não conta igual em cada rede. Com menos de 4
                 outros vídeos na conta, não há índice: a base seria sorte.

  GANHO POR DIA  uma regra só, pra cartão, série, matriz de marca e "no
                 período": a diferença entre dois retratos, dividida por igual
                 quando há dias sem leitura no meio (e marcada "estimado"). A
                 primeira leitura só é ganho se o vídeo foi lido desde o
                 nascimento (até 36h de publicado); senão é BASE — vídeo antigo
                 e métrica que aparece depois (views do Instagram quando os
                 insights chegam) não viram pico falso.

NULO nunca vira zero em lugar nenhum: nulo é "a rede não deu", zero é "deu, e
é zero".

O que NÃO é medido, e a tela diz: quanto cada vídeo VENDEU. Os posts não levam
link rastreado, UTM nem cupom, então venda, receita e conversão por vídeo
ficam pra quando levarem; custo por view, pra quando o criativo tiver custo.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.marketing.metricas import (
    HORA_NOTURNA_UTC,
    JANELA_DIAS,
    MINUTO_COLETA,
    foi_removido,
)

BRT = ZoneInfo("America/Sao_Paulo")

# As idades em que dá pra comparar. D+3 é o padrão: é a que a leitura do fim
# do dia acerta com menos de 1% de erro.
MARCOS = (1, 3, 7)
MARCO_PADRAO = 3
# Distância máxima (horas) entre a idade pedida e a leitura mais perto de cada
# lado. Mais que isso é buraco de coleta, e buraco vira NULO, não palpite.
TOL_H = 36
# Outros vídeos da conta, além do próprio, pra existir "normal da conta".
MIN_OUTROS = 4
# Abaixo disso a taxa de interação é ruído (3 curtidas em 20 views = 15%).
MIN_VIEWS_TAXA = 100
# Quantos criativos com índice um grupo precisa pra virar indício / comparável.
INDICIO = 3
COMPARAVEL = 8

NUMEROS = ("views", "curtidas", "comentarios", "compartilhamentos", "salvamentos", "alcance")
INTERACOES = ("curtidas", "comentarios", "compartilhamentos", "salvamentos")
ORDEM_REDES = ("instagram", "youtube", "tiktok", "facebook")

_CONTADOS = ("ok", "falhou")
_NA_TELA = ("ok", "falhou", "aguardando")


# ─── utilidades ─────────────────────────────────────────────────────────


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return _utc(dt).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _data_brt(x: datetime | date) -> date:
    """O `dia` do retrato é meia-noite BRT gravada como timestamptz — no
    Postgres ele volta em UTC (03:00), e `.date()` direto daria o mesmo dia só
    por sorte do fuso. Converte antes."""
    if isinstance(x, datetime):
        return x.astimezone(BRT).date()
    return x


def _mediana(vals: Iterable[float]) -> float | None:
    v = list(vals)
    return statistics.median(v) if v else None


def _limpo(x: float | None, casas: int = 1) -> float | int | None:
    """Mediana de inteiros pode dar x,5 — mas 590,0 é 590 na tela."""
    if x is None:
        return None
    r = round(x, casas)
    return int(r) if r == int(r) else r


def _soma(acc: dict[str, int], d: dict[str, int]) -> None:
    """Soma só o que existe: chave ausente continua ausente (a tela escreve "—")."""
    for k, v in d.items():
        if v is not None:
            acc[k] = acc.get(k, 0) + v


def _ordem_rede(plataforma: str) -> tuple[int, str]:
    pos = ORDEM_REDES.index(plataforma) if plataforma in ORDEM_REDES else len(ORDEM_REDES)
    return (pos, plataforma)


def _primeira_linha(texto: str | None) -> str:
    """Primeira linha da legenda: é como o Eduardo reconhece o vídeo na lista."""
    return ((texto or "").strip().splitlines() or [""])[0][:80]


# ─── identidade da conta ────────────────────────────────────────────────

_RE_ARROBA = re.compile(r"@([\w.\-]+)")


def norm_conta(s: str | None) -> str | None:
    """ "@Uranyx_BR ", "https://www.tiktok.com/@uranyx_br" e "uranyx_br" são a
    mesma conta. Sem isto, a mesma conta vira duas bases de comparação."""
    if s is None:
        return None
    t = str(s).strip().lower()
    m = _RE_ARROBA.search(t)
    if m:
        return m.group(1) or None
    if "/" in t:
        partes = [x for x in re.split(r"[/?#]", t.split("://", 1)[-1]) if x]
        t = partes[-1] if len(partes) > 1 else ""
    return t.lstrip("@") or None


def chave_conta(post: dict[str, Any]) -> str:
    """A base de comparação é POR CONTA: o id do cadastro; sem ele (conta
    apagada do cadastro), a rede + o nome normalizado do snapshot."""
    if post.get("rede_social_id") is not None:
        return str(post["rede_social_id"])
    return f"{post.get('plataforma')}:{norm_conta(post.get('conta'))}"


def autor_diferente(post: dict[str, Any], leituras: list[dict[str, Any]]) -> str | None:
    """Pista, nunca exclusão: o TikTok diz de quem é o vídeo, e se não for a
    conta cadastrada a tela avisa. Foi assim que os dois TikToks da conta antiga
    da Uranyx passaram despercebidos. Compara com o nome ATUAL da conta (a
    página do TikTok sempre mostra o @ atual do dono, então renomear a conta não
    dispara o aviso)."""
    if post.get("plataforma") != "tiktok":
        return None
    handle = None
    for r in reversed(leituras):
        a = r.get("autor") or {}
        if isinstance(a, dict) and a.get("handle"):
            handle = a["handle"]
            break
    if handle is None:
        return None
    conta = post.get("conta_atual") or post.get("conta")
    return handle if norm_conta(handle) != norm_conta(conta) else None


# ─── grupos ─────────────────────────────────────────────────────────────

# A duração vem do `modelo` do criativo ("video 15s", "Vídeo de 30 segundos").
# O lookbehind é o que impede "F109S 256 GB" (nome de celular) de virar 109 s.
_RE_FORMATO = re.compile(r"(?<!\w)(\d{1,3})\s*(?:s|seg|segundos?)\b", re.I)


def grupo_produto(product_id: Any, produto_nome: str | None, sku: str | None) -> tuple[str, str]:
    if product_id is not None:
        return f"prod:{product_id}", produto_nome or "(produto sem nome)"
    base = re.split(r"[.,\s/]", (sku or "").strip())[0].lower()
    if base:
        return f"sku:{base}", f"SKU {base} (sem produto ligado)"
    return "nenhum", "(sem produto)"


def grupo_formato(modelo: str | None) -> tuple[str, str]:
    m = _RE_FORMATO.search(modelo or "")
    if m and 5 <= int(m.group(1)) <= 180:
        n = int(m.group(1))
        return f"{n}s", f"vídeo {n}s"
    return "nenhum", "(formato não informado)"


def grupo_agencia(equipe: str | None) -> tuple[str, str]:
    e = (equipe or "").strip()
    return (e.lower(), e) if e else ("nenhum", "(sem agência)")


def grupo_roteiro(roteiro_id: Any, titulo: str | None) -> tuple[str, str]:
    if roteiro_id is None:
        return "nenhum", "(sem roteiro)"
    return str(roteiro_id), titulo or "(roteiro sem título)"


_ROTULO_HORARIO = {"12h": "12h", "19h": "19h", "outro": "outro horário"}


def faixa_horario(publicado_em: datetime) -> str:
    """Os dois horários da grade (12h e 19h), com uma hora de folga pra cada
    lado — o robô e as pessoas não publicam no minuto exato."""
    h = publicado_em.astimezone(BRT).hour
    if 11 <= h <= 13:
        return "12h"
    if 18 <= h <= 20:
        return "19h"
    return "outro"


def leitura_do_grupo(n: int) -> str:
    """Quanto dá pra confiar num grupo, pelo número de criativos com índice.
    A mediana de 2 vídeos é sorte; a tela diz isso em vez de esconder."""
    if n < INDICIO:
        return "pouco_dado"
    if n < COMPARAVEL:
        return "indicio"
    return "comparavel"


# ─── o horário da coleta ────────────────────────────────────────────────


def proxima_noturna(t: datetime) -> datetime:
    """A primeira leitura da noite (23:47 BRT) em `t` ou depois."""
    t = _utc(t)
    c = t.replace(hour=HORA_NOTURNA_UTC, minute=MINUTO_COLETA, second=0, microsecond=0)
    return c if c >= t else c + timedelta(days=1)


def proxima_leitura(t: datetime) -> datetime:
    """O próximo :47 — de hora em hora tem passe, e às 02:47 UTC é o da noite."""
    t = _utc(t)
    c = t.replace(minute=MINUTO_COLETA, second=0, microsecond=0)
    return c if c >= t else c + timedelta(hours=1)


def ultimo_dia_fechado(agora: datetime, *, em_andamento: bool = False) -> date:
    """O dia mais novo cujo retrato já é o número do FIM do dia.

    O retrato de hoje só fecha na leitura das 23:47; até lá hoje tem ganho
    parcial, ou nenhum (o passe de hora em hora só lê vídeo novo). Comparar
    "os últimos 7 dias" com esse hoje pela metade contra 7 dias inteiros dava
    "▼ 14%" com o fluxo parado, o dia inteiro, todo dia (24/09/2026). Com a
    leitura da noite ainda rodando, hoje também não fechou.
    """
    agora = _utc(agora)
    hoje = agora.astimezone(BRT).date()
    noite = proxima_noturna(datetime.combine(hoje, time(), BRT))
    if agora >= noite and not em_andamento:
        return hoje
    return hoje - timedelta(days=1)


def corte_universo(agora: datetime, dias: int) -> datetime:
    """Quem aparece na tela: publicado nos últimos max(90, dias) dias. Cobre a
    base da conta mesmo quando a janela é menor — o "normal" de um vídeo de
    ontem são os vídeos do último trimestre."""
    return _utc(agora) - timedelta(days=max(JANELA_DIAS, dias))


def corte_soma(agora: datetime, dias: int) -> datetime:
    """Até onde vão os posts que entram SÓ nas somas por rede (série, "no
    período" e comparação com o período anterior).

    A coleta lê todo post de até 90 dias NO DIA da leitura. No começo do
    período anterior (até 2×dias atrás) ela ainda lia posts de até 2×dias + 90
    dias atrás, e o que eles renderam é parte do fluxo daqueles dias. Cortar
    no universo da tela deixava o período anterior sem eles — com 90 dias, sem
    ninguém, e a tela mostrava "▲ 42322%" (24/09/2026). Os +2 dias são a
    leitura-base da véspera e a virada do dia em Brasília.
    """
    return _utc(agora) - timedelta(days=2 * dias + 2 + JANELA_DIAS)


# ─── um vídeo ───────────────────────────────────────────────────────────


def ganhos_diarios(
    leituras: list[tuple[date, int]], *, pub_dia: date, do_zero: bool
) -> tuple[dict[date, int], set[date]]:
    """Quanto a métrica ganhou em cada dia, a partir dos retratos `(dia, valor)`.

    Buraco de vários dias entre dois retratos é dividido por igual entre eles
    (o resto vai pros últimos) e marcado "estimado" — a tela desenha mais
    claro. `divmod` do Python é exato com negativo (o YouTube tira view de
    robô), então a soma dos dias é sempre a diferença real.
    """
    if not leituras:
        return {}, set()
    if do_zero:
        ant_dia, ant_v = min(pub_dia, leituras[0][0]) - timedelta(days=1), 0
        resto = leituras
    else:
        (ant_dia, ant_v), resto = leituras[0], leituras[1:]
    g: dict[date, int] = {}
    est: set[date] = set()
    for dia, v in resto:
        k = (dia - ant_dia).days
        base, r = divmod(v - ant_v, k)
        for j in range(k):
            d = ant_dia + timedelta(days=j + 1)
            g[d] = g.get(d, 0) + base + (1 if j >= k - r else 0)
            if k > 1:
                est.add(d)
        ant_dia, ant_v = dia, v
    return g, est


def pontos_views(leituras: list[dict[str, Any]], publicado_em: datetime) -> list[tuple[float, int]]:
    """(idade em horas, views) de cada leitura, com a âncora (0 h, 0 views).

    Leitura com idade negativa (relógio, ou retrato gravado antes da
    publicação) sai: não existe view antes de o vídeo existir.
    """
    pub = _utc(publicado_em)
    pts: list[tuple[float, int]] = []
    for r in leituras:
        v = r.get("views")
        if v is None or r.get("lido_em") is None:
            continue
        h = (_utc(r["lido_em"]) - pub).total_seconds() / 3600
        if h >= 0:
            pts.append((h, v))
    pts.sort()
    return [(0.0, 0), *pts]


def views_no_marco(
    leituras: list[dict[str, Any]], publicado_em: datetime, n: int, agora: datetime
) -> tuple[int | None, str | None]:
    """Views com `n` dias de publicado, e o motivo quando não dá pra saber.

    Interpola entre a leitura de antes e a de depois da idade; sem leitura a
    menos de 36h de cada lado é buraco, e buraco é NULO. Sem leitura depois da
    idade, ainda é cedo (ou buraco, se já devia ter tido).
    """
    if not leituras:
        return None, "aguardando"
    if all(r.get("views") is None for r in leituras):
        return None, "sem_views"
    pts = pontos_views(leituras, publicado_em)
    alvo = 24 * n
    hi = next((p for p in pts if p[0] >= alvo), None)
    lo = [p for p in pts if p[0] <= alvo][-1]  # a âncora garante que existe
    if hi is None:
        idade_h = (_utc(agora) - _utc(publicado_em)).total_seconds() / 3600
        return None, ("cedo" if idade_h < alvo + TOL_H else "buraco")
    if hi[0] - alvo > TOL_H or alvo - lo[0] > TOL_H:
        return None, "buraco"
    if hi[0] == lo[0]:
        return hi[1], None
    return round(lo[1] + (hi[1] - lo[1]) * (alvo - lo[0]) / (hi[0] - lo[0])), None


def taxa_interacao(leituras: list[dict[str, Any]]) -> float | None:
    """(curtidas + comentários + compartilhamentos + salvamentos) / views, na
    última leitura com views. Soma só o que a rede deu — o YouTube não tem
    compartilhamento nem salvamento, e por isso a taxa dele é menor por
    construção: só vale comparar dentro da mesma conta."""
    r = next((x for x in reversed(leituras) if x.get("views") is not None), None)
    if r is None or r["views"] < MIN_VIEWS_TAXA:
        return None
    partes = [r[k] for k in INTERACOES if r.get(k) is not None]
    if not partes:
        return None
    return round(sum(partes) / r["views"], 4)


def ritmo_dia(pts: list[tuple[float, int]]) -> int | None:
    """Views por dia entre as duas últimas leituras. Só informativo: nunca
    ordena nada (vídeo novo sempre tem ritmo maior)."""
    if len(pts) < 2:
        return None
    (h0, v0), (h1, v1) = pts[-2], pts[-1]
    if h1 - h0 < 6:
        return None
    return round((v1 - v0) / ((h1 - h0) / 24))


def _analisa(
    p: dict[str, Any],
    linhas: list[dict[str, Any]],
    *,
    marco: int,
    agora: datetime,
) -> dict[str, Any]:
    """Tudo que dá pra saber de UM post olhando só as linhas dele."""
    pub = _utc(p["publicado_em"])
    ultima = linhas[-1] if linhas else None
    leituras = [r for r in linhas if r.get("lido_em") is not None]
    if p.get("fora_do_desempenho_em") is not None:
        estado = "excluido"
    elif ultima is None:
        estado = "aguardando"
    elif not ultima.get("erro"):
        estado = "ok"
    elif foi_removido(ultima["erro"]):
        estado = "removido"
    else:
        estado = "falhou"

    # Acumulado por métrica, da leitura mais nova QUE TEM a métrica: os
    # insights do Instagram vêm e vão, e o número não pode piscar.
    acumulado: dict[str, int] = {}
    for k in NUMEROS:
        for r in reversed(leituras):
            if r.get(k) is not None:
                acumulado[k] = r[k]
                break

    # Lido desde o nascimento = a primeira leitura saiu até 36h depois de
    # publicar. Só aí a primeira leitura é ganho; senão ela é a base.
    nasceu_lido = False
    if leituras:
        idade = (_utc(leituras[0]["lido_em"]) - pub).total_seconds()
        nasceu_lido = 0 <= idade <= TOL_H * 3600
    ganhos: dict[str, tuple[dict[date, int], set[date]]] = {}
    pub_dia = pub.astimezone(BRT).date()
    for k in NUMEROS:
        serie = [(_data_brt(r["dia"]), r[k]) for r in leituras if r.get(k) is not None]
        do_zero = nasceu_lido and leituras[0].get(k) is not None
        ganhos[k] = ganhos_diarios(serie, pub_dia=pub_dia, do_zero=do_zero)

    pts = pontos_views(leituras, pub)
    vm, vm_motivo = views_no_marco(leituras, pub, marco, agora)
    return {
        "p": p,
        "pub": pub,
        "estado": estado,
        "linhas": linhas,
        "leituras": leituras,
        "acumulado": acumulado,
        "lido_em": _utc(leituras[-1]["lido_em"]) if leituras else None,
        "tentado_em": _utc(ultima.get("updated_at")) if ultima else None,
        "erro": ultima.get("erro") if ultima else None,
        "dia_ultima": _data_brt(ultima["dia"]) if ultima else None,
        "ganhos": ganhos,
        "pts": pts,
        "vm": vm,
        "vm_motivo": vm_motivo,
        "taxa": taxa_interacao(leituras),
        "conta": chave_conta(p),
    }


def _no_periodo(a: dict[str, Any], dias_janela: set[date]) -> tuple[dict[str, int], bool]:
    out: dict[str, int] = {}
    estimado = False
    for k, (g, est) in a["ganhos"].items():
        dentro = [d for d in g if d in dias_janela]
        if dentro:
            out[k] = sum(g[d] for d in dentro)
            estimado = estimado or any(d in est for d in dentro)
    return out, estimado


# ─── a resposta inteira ─────────────────────────────────────────────────


def montar(
    posts: list[dict[str, Any]],
    linhas: list[dict[str, Any]],
    *,
    dias: int,
    marco: int,
    agora: datetime,
    marca_id: Any = None,
    equipes_permitidas: set[str] | None = None,
    inicio_da_coleta: date | datetime | None = None,
    coleta: dict[str, Any] | None = None,
    posts_antigos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A resposta do GET /api/marketing/metricas (contrato "versao": 2).

    `posts` é o universo (publicados nos últimos max(90, dias) dias, com o
    criativo, a marca, o produto, o roteiro e o nome atual da conta); `linhas`
    são os retratos deles. A base "normal da conta" é calculada ANTES do
    filtro de marca e do escopo de equipe: todo mundo vê o mesmo índice pro
    mesmo vídeo, e só a mediana e o tamanho da base saem daqui.

    `posts_antigos` são os mais velhos que o universo que a coleta ainda lia
    durante a janela ou o período anterior (ver `corte_soma`), com os
    retratos deles também em `linhas`. Entram SÓ nas somas por rede — não
    viram linha de tabela, nem mexem na base da conta.
    """
    agora = _utc(agora)
    coleta = coleta or {}
    hoje = agora.astimezone(BRT).date()
    desde = hoje - timedelta(days=dias - 1)
    janela = [desde + timedelta(days=i) for i in range(dias)]
    dias_janela = set(janela)
    # A comparação com o período anterior é entre DIAS FECHADOS e do mesmo
    # tamanho: os `dias` até o último dia fechado contra os `dias` antes
    # deles. A janela da tela inclui hoje, que ainda não fechou.
    fechado = ultimo_dia_fechado(agora, em_andamento=bool(coleta.get("em_andamento")))
    dias_comp = {fechado - timedelta(days=i) for i in range(dias)}
    dias_anteriores = {fechado - timedelta(days=dias + i) for i in range(dias)}
    inicio = _data_brt(inicio_da_coleta) if inicio_da_coleta is not None else None
    # O ganho do dia X é a diferença contra a leitura de X-1: o período
    # anterior só está inteiro se a leitura já existia na VÉSPERA do primeiro
    # dia dele. No dia exato do começo, o vídeo antigo ainda era só base.
    tem_anterior = inicio is not None and inicio < min(dias_anteriores)
    marca_filtro = str(marca_id) if marca_id is not None else None

    por_post: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in linhas:
        por_post[str(r["postagem_id"])].append(r)

    def _carrega(p: dict[str, Any]) -> dict[str, Any]:
        rows = sorted(por_post.get(str(p["id"]), []), key=lambda r: _data_brt(r["dia"]))
        a = _analisa(p, rows, marco=marco, agora=agora)
        equipe = (p.get("equipe") or "").strip().lower()
        a["no_escopo"] = equipes_permitidas is None or equipe in equipes_permitidas
        a["visivel"] = a["no_escopo"] and (
            marca_filtro is None or str(p.get("marca_id")) == marca_filtro
        )
        a["no_periodo"], a["no_periodo_estimado"] = _no_periodo(a, dias_janela)
        return a

    info: dict[str, dict[str, Any]] = {str(p["id"]): _carrega(p) for p in posts}
    antigos: list[dict[str, Any]] = []
    for p in posts_antigos or ():
        if str(p["id"]) in info:
            continue
        a = _carrega(p)
        if a["visivel"] and a["estado"] in _CONTADOS:
            antigos.append(a)

    # ── base da conta (antes de filtro e escopo; leave-one-out) ──
    corte_base = agora - timedelta(days=JANELA_DIAS)
    pop_views: dict[str, list[tuple[str, int]]] = defaultdict(list)
    pop_taxa: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for pid, a in info.items():
        if a["estado"] not in _CONTADOS or a["pub"] < corte_base:
            continue
        if a["vm"] is not None:
            pop_views[a["conta"]].append((pid, a["vm"]))
        if a["taxa"] is not None:
            pop_taxa[a["conta"]].append((pid, a["taxa"]))

    def _indices(pid: str, a: dict[str, Any]) -> None:
        outros = [v for q, v in pop_views.get(a["conta"], []) if q != pid]
        med = _mediana(outros)
        # Views são contagem: mediana de 2 vídeos dá x,5, e "2.048,5 views"
        # na tela parece erro. Arredonda (a de TAXA segue com casas).
        a["base"] = {"mediana": _limpo(med, 0), "n": len(outros)}
        if a["vm"] is None:
            a["indice_views"], a["indice_motivo"] = None, "sem_marco"
        elif len(outros) < MIN_OUTROS:
            a["indice_views"], a["indice_motivo"] = None, "base_pequena"
        elif not med or med <= 0:
            a["indice_views"], a["indice_motivo"] = None, "base_zero"
        else:
            a["indice_views"], a["indice_motivo"] = round(a["vm"] / med, 2), None
        outras_taxas = [t for q, t in pop_taxa.get(a["conta"], []) if q != pid]
        med_t = _mediana(outras_taxas)
        a["indice_interacao"] = (
            round(a["taxa"] / med_t, 2)
            if a["taxa"] is not None and len(outras_taxas) >= MIN_OUTROS and med_t
            else None
        )

    # ── o que a tela mostra ──
    na_tela = sorted(
        (pid for pid, a in info.items() if a["visivel"] and a["estado"] in _NA_TELA),
        key=lambda pid: info[pid]["pub"],
        reverse=True,
    )
    for pid in na_tela:
        _indices(pid, info[pid])
    contados = [pid for pid in na_tela if info[pid]["estado"] in _CONTADOS]

    postagens = [_postagem(pid, info[pid], marco=marco, agora=agora) for pid in na_tela]

    # ── criativos (a unidade de investimento: um vídeo produzido) ──
    por_criativo: dict[str, list[str]] = defaultdict(list)
    for pid in na_tela:
        por_criativo[str(info[pid]["p"]["creative_id"])].append(pid)
    criativos_int = {cid: _criativo(cid, pids, info) for cid, pids in por_criativo.items()}
    criativos = sorted(
        criativos_int.values(),
        key=lambda c: (
            c["indice_views"] is None,
            -(c["indice_views"] or 0),
            -c["_ultima_pub"].timestamp(),
        ),
    )[:300]

    grupos = {
        dim: _grupos_de_criativos(dim, criativos_int, info)
        for dim in ("produto", "formato", "agencia", "roteiro")
    }
    grupos["horario"] = _grupos_de_horario(na_tela, info)

    resumo = _resumo(
        info,
        na_tela,
        contados,
        antigos,
        janela,
        dias_janela,
        dias_comp,
        dias_anteriores,
        tem_anterior,
        fechado,
    )

    marcas, sem_video = _marcas(info)

    fora_do_ar = [
        {
            "postagem_id": pid,
            # O criativo vai junto pra tabela de vídeos marcar a célula como
            # "apagado da rede": sem ele, o post apagado aparecia como "não
            # postado" — que é mentira, ele saiu e foi apagado depois.
            "creative_id": str(a["p"]["creative_id"]),
            "marca": a["p"].get("marca") or "(sem marca)",
            "plataforma": a["p"]["plataforma"],
            "post_url": a["p"].get("post_url"),
            "publicado_em": _iso(a["pub"]),
        }
        for pid, a in sorted(info.items(), key=lambda x: x[1]["pub"], reverse=True)
        if a["visivel"] and a["estado"] == "removido"
    ]
    fora_do_desempenho = [
        {
            "postagem_id": pid,
            "marca": a["p"].get("marca") or "(sem marca)",
            "plataforma": a["p"]["plataforma"],
            "conta": a["p"].get("conta"),
            "post_url": a["p"].get("post_url"),
            "publicado_em": _iso(a["pub"]),
            "motivo": a["p"].get("fora_do_desempenho_motivo"),
            "em": _iso(a["p"].get("fora_do_desempenho_em")),
        }
        for pid, a in sorted(info.items(), key=lambda x: x[1]["pub"], reverse=True)
        if a["visivel"] and a["estado"] == "excluido"
    ]

    marcas_disp: dict[str, str] = {}
    for a in info.values():
        if a["no_escopo"] and a["p"].get("marca_id") is not None:
            marcas_disp[str(a["p"]["marca_id"])] = a["p"].get("marca") or "(sem marca)"

    lidos = [info[pid]["lido_em"] for pid in na_tela if info[pid]["lido_em"] is not None]
    publicacoes = defaultdict(int)
    for pid in na_tela:
        publicacoes[info[pid]["pub"].astimezone(BRT).date()] += 1

    return {
        "versao": 2,
        "dias": dias,
        "desde": desde.isoformat(),
        "ate": hoje.isoformat(),
        "marco": marco,
        "marca_id": marca_filtro,
        "gerado_em": _iso(agora),
        "minimos": {
            "base_conta": MIN_OUTROS + 1,
            "views_taxa": MIN_VIEWS_TAXA,
            "indicio": INDICIO,
            "comparavel": COMPARAVEL,
            "tolerancia_h": TOL_H,
        },
        "coleta": {
            "ultima_leitura_em": _iso(max(lidos)) if lidos else None,
            "proxima_leitura_em": _iso(proxima_leitura(agora)),
            "proxima_noturna_em": _iso(proxima_noturna(agora)),
            "inicio_da_coleta": inicio.isoformat() if inicio else None,
            "em_andamento": bool(coleta.get("em_andamento")),
            "pode_atualizar_em": coleta.get("pode_atualizar_em"),
            "ultima_rodada": coleta.get("ultima_rodada"),
        },
        "marcas_disponiveis": [
            {"id": i, "nome": n} for i, n in sorted(marcas_disp.items(), key=lambda x: x[1].lower())
        ],
        "resumo": resumo,
        "serie_dias": [d.isoformat() for d in janela],
        "publicacoes_por_dia": [publicacoes.get(d, 0) for d in janela],
        "postagens": postagens,
        "criativos": [{k: v for k, v in c.items() if not k.startswith("_")} for c in criativos],
        "grupos": grupos,
        "marcas": marcas,
        "sem_video_no_ar": sem_video,
        "fora_do_ar": fora_do_ar,
        "fora_do_desempenho": fora_do_desempenho,
    }


def _postagem(pid: str, a: dict[str, Any], *, marco: int, agora: datetime) -> dict[str, Any]:
    p = a["p"]
    return {
        "postagem_id": pid,
        "creative_id": str(p["creative_id"]),
        "marca_id": str(p["marca_id"]) if p.get("marca_id") is not None else None,
        "marca": p.get("marca") or "(sem marca)",
        "plataforma": p["plataforma"],
        "conta": p.get("conta_atual") or p.get("conta"),
        "post_url": p.get("post_url"),
        "publicado_em": _iso(a["pub"]),
        "origem": p.get("origem"),
        "titulo": _primeira_linha(p.get("legenda")),
        "horario": faixa_horario(a["pub"]),
        "idade_horas": round((agora - a["pub"]).total_seconds() / 3600, 1),
        "estado": a["estado"],
        "lido_em": _iso(a["lido_em"]),
        "tentado_em": _iso(a["tentado_em"]),
        "erro": a["erro"] if a["estado"] == "falhou" else None,
        "acumulado": a["acumulado"],
        "no_periodo": a["no_periodo"],
        "no_periodo_estimado": a["no_periodo_estimado"],
        "views_marco": a["vm"],
        "marco_motivo": a["vm_motivo"],
        "marco_pronto_em": _iso(proxima_noturna(a["pub"] + timedelta(hours=24 * marco))),
        "indice_views": a["indice_views"],
        "indice_motivo": a["indice_motivo"],
        "base": a["base"],
        "taxa_interacao": a["taxa"],
        "indice_interacao": a["indice_interacao"],
        "ritmo_dia": ritmo_dia(a["pts"]),
        "curva": [[round(h / 24, 1), v] for h, v in a["pts"] if h <= 14 * 24],
        "autor_diferente": autor_diferente(p, a["leituras"]),
    }


def _criativo(cid: str, pids: list[str], info: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """`pids` vem do mais novo pro mais velho."""
    p0 = info[pids[0]]["p"]
    idx_v = [info[q]["indice_views"] for q in pids if info[q]["indice_views"] is not None]
    idx_i = [info[q]["indice_interacao"] for q in pids if info[q]["indice_interacao"] is not None]
    titulo = next((t for q in pids if (t := _primeira_linha(info[q]["p"].get("legenda")))), "")
    titulo = titulo or p0.get("roteiro_titulo") or p0.get("modelo") or ""
    redes: dict[str, list[str]] = {"instagram": [], "youtube": [], "tiktok": []}
    for q in pids:
        redes.setdefault(info[q]["p"]["plataforma"], []).append(q)
    chave, rotulo = grupo_produto(p0.get("product_id"), p0.get("produto_nome"), p0.get("sku"))
    fch, frot = grupo_formato(p0.get("modelo"))
    ach, arot = grupo_agencia(p0.get("equipe"))
    rch, rrot = grupo_roteiro(p0.get("roteiro_id"), p0.get("roteiro_titulo"))
    return {
        "creative_id": cid,
        "titulo": titulo[:80],
        "marca": p0.get("marca") or "(sem marca)",
        "marca_id": str(p0["marca_id"]) if p0.get("marca_id") is not None else None,
        "sku": p0.get("sku"),
        "modelo": p0.get("modelo"),
        "produto": {"chave": chave, "rotulo": rotulo},
        "formato": {"chave": fch, "rotulo": frot},
        "agencia": {"chave": ach, "rotulo": arot},
        "roteiro": {"chave": rch, "rotulo": rrot},
        "primeira_publicacao_em": _iso(min(info[q]["pub"] for q in pids)),
        "indice_views": round(statistics.median(idx_v), 2) if idx_v else None,
        "indice_interacao": round(statistics.median(idx_i), 2) if idx_i else None,
        "n_indices": len(idx_v),
        "postagens": redes,
        "_pids": pids,
        "_ultima_pub": info[pids[0]]["pub"],
    }


def _por_rede(pids: Iterable[str], info: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Views na idade, cruas, por rede — honesto só DENTRO de uma rede."""
    por: dict[str, list[int]] = {}
    for q in pids:
        lst = por.setdefault(info[q]["p"]["plataforma"], [])
        if info[q]["vm"] is not None:
            lst.append(info[q]["vm"])
    return {
        rede: {"mediana": _limpo(_mediana(vs), 0), "n": len(vs)}
        for rede, vs in sorted(por.items(), key=lambda x: _ordem_rede(x[0]))
    }


def _ordena_grupos(grupos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peso = {"comparavel": 0, "indicio": 1, "pouco_dado": 2}
    return sorted(
        grupos,
        key=lambda g: (
            peso[g["leitura"]],
            g["indice_views"] is None,
            -(g["indice_views"] or 0),
            -g["total"],
        ),
    )


def _grupo(
    chave: str,
    rotulo: str,
    unidade: str,
    membros: list[dict[str, Any]],
    pids_posts: list[str],
    info: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """`membros`: {indice_views, indice_interacao, melhor} de cada membro."""
    idx = [m["indice_views"] for m in membros if m["indice_views"] is not None]
    idx_i = [m["indice_interacao"] for m in membros if m["indice_interacao"] is not None]
    com_indice = [m for m in membros if m["indice_views"] is not None]
    melhor = max(com_indice, key=lambda m: m["indice_views"]) if com_indice else None
    return {
        "chave": chave,
        "rotulo": rotulo,
        "unidade": unidade,
        "total": len(membros),
        "n": len(idx),
        "indice_views": round(statistics.median(idx), 2) if idx else None,
        "indice_interacao": round(statistics.median(idx_i), 2) if idx_i else None,
        "leitura": leitura_do_grupo(len(idx)),
        "por_rede": _por_rede(pids_posts, info),
        # A mediana esconde o acerto isolado; o melhor vai do lado dela.
        "melhor": melhor["melhor"] if melhor else None,
    }


def _grupos_de_criativos(
    dim: str, criativos: dict[str, dict[str, Any]], info: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Membro = CRIATIVO, não post: o mesmo vídeo em 3 redes é 1 decisão de
    produção, e contá-lo 3 vezes faria o grupo parecer mais testado do que é."""
    por: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rotulos: dict[str, str] = {}
    for c in criativos.values():
        por[c[dim]["chave"]].append(c)
        rotulos[c[dim]["chave"]] = c[dim]["rotulo"]
    saida = []
    for chave, cs in por.items():
        membros = [
            {
                "indice_views": c["indice_views"],
                "indice_interacao": c["indice_interacao"],
                "melhor": {
                    "creative_id": c["creative_id"],
                    "postagem_id": None,
                    "titulo": c["titulo"],
                    "indice_views": c["indice_views"],
                },
            }
            for c in cs
        ]
        pids = [q for c in cs for q in c["_pids"]]
        saida.append(_grupo(chave, rotulos[chave], "criativo", membros, pids, info))
    return _ordena_grupos(saida)


def _grupos_de_horario(na_tela: list[str], info: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Horário é do POST, não do criativo: o mesmo vídeo pode ter saído às
    12h numa rede e às 19h noutra."""
    por: dict[str, list[str]] = defaultdict(list)
    for pid in na_tela:
        por[faixa_horario(info[pid]["pub"])].append(pid)
    saida = []
    for chave, pids in por.items():
        membros = [
            {
                "indice_views": info[q]["indice_views"],
                "indice_interacao": info[q]["indice_interacao"],
                "melhor": {
                    "creative_id": str(info[q]["p"]["creative_id"]),
                    "postagem_id": q,
                    "titulo": _primeira_linha(info[q]["p"].get("legenda"))
                    or info[q]["p"].get("modelo")
                    or "",
                    "indice_views": info[q]["indice_views"],
                },
            }
            for q in pids
        ]
        saida.append(_grupo(chave, _ROTULO_HORARIO[chave], "postagem", membros, pids, info))
    return _ordena_grupos(saida)


def _ganho_nos_dias(ganhos: list[dict[date, int]], dias: set[date]) -> int:
    return sum(v for g in ganhos for d, v in g.items() if d in dias)


def _resumo(
    info: dict[str, dict[str, Any]],
    na_tela: list[str],
    contados: list[str],
    antigos: list[dict[str, Any]],
    janela: list[date],
    dias_janela: set[date],
    dias_comp: set[date],
    dias_anteriores: set[date],
    tem_anterior: bool,
    fechado: date,
) -> dict[str, Any]:
    visiveis = [a for a in info.values() if a["visivel"]]
    videos = {
        "no_ar": sum(1 for a in visiveis if a["estado"] in _CONTADOS),
        "aguardando": sum(1 for a in visiveis if a["estado"] == "aguardando"),
        "com_falha": sum(1 for a in visiveis if a["estado"] == "falhou"),
        "fora_do_ar": sum(1 for a in visiveis if a["estado"] == "removido"),
        "fora_do_desempenho": sum(1 for a in visiveis if a["estado"] == "excluido"),
    }
    redes_pids: dict[str, list[str]] = defaultdict(list)
    for pid in na_tela:
        redes_pids[info[pid]["p"]["plataforma"]].append(pid)
    antigos_por_rede: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in antigos:
        antigos_por_rede[a["p"]["plataforma"]].append(a)

    contados_set = set(contados)
    redes = []
    for rede in sorted(redes_pids, key=_ordem_rede):
        pids = redes_pids[rede]
        cont = [q for q in pids if q in contados_set]
        # "Sem views" é ter leitura e nenhuma com views (o Instagram sem
        # insights). Rede sem leitura NENHUMA ainda — vídeo aguardando a 1ª,
        # ou falhando desde a 1ª — não é isso: o TikTok recém-publicado
        # aparecia com "a conta ainda não liberou insights" (24/09/2026).
        tem_leitura = any(info[q]["leituras"] for q in cont)
        tem_views = any(any(r.get("views") is not None for r in info[q]["leituras"]) for q in cont)
        sem_views = tem_leitura and not tem_views
        metrica = "curtidas" if sem_views else "views"
        # O que rendeu soma também os posts mais velhos que o universo que a
        # coleta ainda lia (ver `corte_soma`): sem eles o período anterior
        # perdia o fluxo de quem já saiu da tela. Acumulado e contagem de
        # vídeos continuam só os da tela.
        soma = [info[q] for q in cont] + antigos_por_rede.get(rede, [])
        no_periodo: dict[str, int] = {}
        acumulado: dict[str, int] = {}
        for a in soma:
            _soma(no_periodo, a["no_periodo"])
        for q in cont:
            _soma(acumulado, info[q]["acumulado"])
        # Mesma regra de ganho do "no período": a soma da série fecha com ele.
        ganhos = [a["ganhos"][metrica][0] for a in soma]
        serie: list[int | None] = []
        for d in janela:
            vals = [g[d] for g in ganhos if d in g]
            serie.append(sum(vals) if vals else None)
        estimados: set[date] = set()
        for a in soma:
            estimados |= a["ganhos"][metrica][1] & dias_janela
        # A comparação é entre dias FECHADOS, do mesmo tamanho (ver
        # `ultimo_dia_fechado`) — não o "no período", que tem hoje pela
        # metade. E só existe se a leitura já existia no período anterior:
        # senão "▲ 100%" seria só "antes a gente não lia".
        comparacao = {
            "atual": _ganho_nos_dias(ganhos, dias_comp),
            "anterior": _ganho_nos_dias(ganhos, dias_anteriores) if tem_anterior else None,
            "ate": fechado.isoformat(),
        }
        inter = [no_periodo[k] for k in INTERACOES if k in no_periodo]
        lidos = [info[q]["lido_em"] for q in pids if info[q]["lido_em"] is not None]
        falhas = sorted(
            (q for q in pids if info[q]["estado"] == "falhou"),
            key=lambda q: info[q]["tentado_em"] or datetime.min.replace(tzinfo=UTC),
        )
        redes.append(
            {
                "plataforma": rede,
                "videos": len(cont),
                "aguardando": sum(1 for q in pids if info[q]["estado"] == "aguardando"),
                "com_falha": len(falhas),
                "metrica_serie": metrica,
                "sem_views": sem_views,
                "no_periodo": no_periodo,
                "interacoes_no_periodo": sum(inter) if inter else None,
                "acumulado": acumulado,
                "comparacao": comparacao,
                # O mesmo número com o nome de antes: é por ele que a tela diz
                # "sem base de comparação ainda".
                "periodo_anterior": comparacao["anterior"],
                "serie": serie,
                "estimado_dias": sorted(d.isoformat() for d in estimados),
                "lido_em": _iso(max(lidos)) if lidos else None,
                "erro": info[falhas[-1]]["erro"] if falhas else None,
            }
        )
    com_views = [
        r["no_periodo"]["views"] for r in redes if not r["sem_views"] and "views" in r["no_periodo"]
    ]
    return {
        "videos": videos,
        "redes": redes,
        "tendencia": {
            "views_no_periodo": sum(com_views) if com_views else None,
            "redes_sem_views": [r["plataforma"] for r in redes if r["sem_views"]],
        },
    }


def _marcas(info: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """A matriz por marca — o formato da tela de 23/09, mais o que faltava
    (quem está aguardando, quando foi lido, o estado de cada vídeo).

    Marca cujos vídeos foram TODOS apagados não tem o que reportar e vai pra
    `sem_video_no_ar` (Eduardo, 24/09/2026, depois de apagar os testes da
    charlots e da 7buyers). Marca com vídeo ainda aguardando a 1ª leitura NÃO
    vai: o vídeo está no ar, só não foi lido.
    """
    por_marca: dict[str, dict[str, Any]] = {}
    for pid, a in info.items():
        if not a["visivel"] or a["estado"] == "excluido":
            continue
        p = a["p"]
        nome = p.get("marca") or "(sem marca)"
        m = por_marca.setdefault(
            nome,
            {
                "marca": nome,
                "marca_id": str(p["marca_id"]) if p.get("marca_id") is not None else None,
                "posts": 0,
                "aguardando": 0,
                "acumulado": {},
                "no_periodo": {},
                "plataformas": {},
            },
        )
        # Vídeo removido não entra em conta nenhuma: não está no ar, não está
        # rendendo, e contá-lo faria a marca parecer ter mais material.
        if a["estado"] == "removido":
            continue
        plat = m["plataformas"].setdefault(
            p["plataforma"],
            {
                "plataforma": p["plataforma"],
                "posts": 0,
                "aguardando": 0,
                "acumulado": {},
                "no_periodo": {},
                "coletado_em": None,
                "lido_em": None,
                "erro": None,
                "videos": [],
                "_erro_em": None,
            },
        )
        if a["estado"] == "aguardando":
            m["aguardando"] += 1
            plat["aguardando"] += 1
        else:
            m["posts"] += 1
            plat["posts"] += 1
            for alvo in (m, plat):
                _soma(alvo["acumulado"], a["acumulado"])
                _soma(alvo["no_periodo"], a["no_periodo"])
        dia = a["dia_ultima"]
        if dia and (plat["coletado_em"] is None or dia > plat["coletado_em"]):
            plat["coletado_em"] = dia
        if a["lido_em"] and (plat["lido_em"] is None or a["lido_em"] > plat["lido_em"]):
            plat["lido_em"] = a["lido_em"]
        if a["estado"] == "falhou":
            quando = a["tentado_em"] or datetime.min.replace(tzinfo=UTC)
            if plat["_erro_em"] is None or quando >= plat["_erro_em"]:
                plat["erro"], plat["_erro_em"] = a["erro"], quando
        plat["videos"].append(
            {
                "postagem_id": pid,
                "post_url": p.get("post_url"),
                "publicado_em": _iso(a["pub"]),
                "titulo": _primeira_linha(p.get("legenda")),
                "acumulado": a["acumulado"],
                "no_periodo": a["no_periodo"],
                "coletado_em": a["dia_ultima"].isoformat() if a["dia_ultima"] else None,
                "removido": False,
                "erro": a["erro"] if a["estado"] == "falhou" else None,
                "estado": a["estado"],
            }
        )

    sem_video: list[str] = []
    saida: list[dict[str, Any]] = []
    for m in sorted(
        por_marca.values(), key=lambda x: (-(x["acumulado"].get("views") or 0), x["marca"].lower())
    ):
        redes = [p for p in m["plataformas"].values() if p["posts"] or p["aguardando"]]
        if not redes:
            sem_video.append(m["marca"])
            continue
        for plat in redes:
            plat.pop("_erro_em", None)
            plat["coletado_em"] = plat["coletado_em"].isoformat() if plat["coletado_em"] else None
            plat["lido_em"] = _iso(plat["lido_em"])
            # Mais views primeiro: a pergunta é "o que rendeu", não "o que saiu".
            plat["videos"].sort(key=lambda v: -(v["acumulado"].get("views") or 0))
        m["plataformas"] = sorted(redes, key=lambda x: x["plataforma"])
        saida.append(m)
    return saida, sorted(sem_video)
