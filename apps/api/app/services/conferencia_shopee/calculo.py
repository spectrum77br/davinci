"""O relatório da Conferência Shopee (versao 1) — função PURA sobre as coletas.

Entra: a execução (semanas, tipo, origem), as coletas (status, erro e os
números crus `dados`, ColetaDados versao 1), o mapa de eletro do DaVinci e o
histórico de saldos de Ads. Sai: o JSON congelado em
`conferencia_shopee_execucao.relatorio` — o que a tela, o Excel, o CSV, o MD,
o HTML e o Threema mostram. "Recalcular" chama de novo com os `dados`
guardados: nada aqui lê banco nem relógio.

Por conta e semana:
  • total da conta: afiliados (vendas, comissão, cliques, pedidos), Ads
    (vendas, gasto, impressões, cliques, pedidos) e vendas pagas;
  • parte eletro: as mesmas métricas somadas só nos itens eletro
    (classificacao.classificar, item a item);
  • Mala → uma linha em Mala com o total. Celular → linha em Celular com
    total − eletro (seção que veio vazia continua vazia) e linha em Eletro
    com a parte eletro, só se a conta teve algum valor eletro > 0 em alguma
    das 4 semanas;
  • cliques de afiliados são a exceção: o total (seller_daily → clicks) e os
    cliques por produto (seller_item_detail → clicks) são contas DIFERENTES
    da Shopee e não fecham (Barbosa, 06/10/2026: total 18.299, soma dos
    produtos 20.884). Subtrair daria Celular errado e até negativo; então o
    total é DIVIDIDO na proporção dos produtos: eletro = total × Σcliques
    eletro ÷ Σcliques de todos os produtos (arredondado), Celular = o resto;
  • parte eletro desconhecida (item sem o campo) de cliques OU de pedidos →
    o par inteiro fica em Celular (pedidos e cliques juntos, senão a
    conversão de Celular e a de Eletro saem tortas);
  • Celular que daria negativo (a parte eletro passou do total da conta:
    fontes que não batem) fica vazio ("—"), Eletro fica com o total e a linha
    ganha um aviso — nunca um número ou uma conversão negativa;
  • Saldo Ads: S1 = o saldo lido nesta coleta; S2..S4 = a leitura mais
    perto do dia seguinte ao fim da semana, ±1 dia (empate: a mais cedo).
    Em Eletro, sempre vazio (o saldo é da conta inteira).

Totais de grupo somam as linhas (vazio + x = x; tudo vazio = vazio) e o % do
grupo e as conversões (pedidos ÷ cliques) saem das SOMAS, nunca da média dos
percentuais. A conversão do total soma pedidos e cliques só das linhas que
têm os DOIS: linha com pedidos e sem cliques (coleta antiga) não infla a
conversão do grupo. Geral = Mala + Celular + Eletro.

Coleta antiga (antes de 07/10/2026) não traz os cliques de afiliados: a
métrica fica vazia ("—"), nunca zero.

As regras de variação ("▲ 12,3%", "▼ 0,8 p.p.", "novo", "=", "—") e da média
das 3 semanas são as MESMAS de apps/web/lib/conferencia.ts: mudou lá, muda
aqui.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any

from app.services.conferencia_shopee import classificacao
from app.services.conferencia_shopee.periodos import FUSO, dia_da_semana, rotulo

VERSAO = 1

METRICAS: tuple[dict[str, str], ...] = (
    {"chave": "vendas_afiliados", "rotulo": "Vendas afiliados", "tipo": "dinheiro", "bom": "sobe"},
    {"chave": "vendas_ads", "rotulo": "Vendas Ads", "tipo": "dinheiro", "bom": "sobe"},
    {"chave": "saldo_ads", "rotulo": "Saldo Ads", "tipo": "dinheiro", "bom": "neutro"},
    {"chave": "impressoes", "rotulo": "Impressões", "tipo": "inteiro", "bom": "sobe"},
    {
        "chave": "invest_afiliados",
        "rotulo": "Invest. afiliados",
        "tipo": "dinheiro",
        "bom": "neutro",
    },
    {"chave": "invest_ads", "rotulo": "Invest. Ads", "tipo": "dinheiro", "bom": "neutro"},
    {"chave": "pct", "rotulo": "% s/ vendas", "tipo": "percentual", "bom": "desce"},
    {"chave": "vendas", "rotulo": "Vendas", "tipo": "dinheiro", "bom": "sobe"},
    # Pedido de 07/10/2026 (Resumo no formato da planilha antiga): cliques,
    # pedidos e conversão de afiliados e de Ads. Vêm DEPOIS das 8 primeiras
    # para o CSV e o relatório antigo não mudarem de lugar.
    {"chave": "cliques_afiliados", "rotulo": "Cliques afiliados", "tipo": "inteiro",
     "bom": "neutro"},
    {"chave": "pedidos_afiliados", "rotulo": "Pedidos afiliados", "tipo": "inteiro",
     "bom": "sobe"},
    {"chave": "conversao_afiliados", "rotulo": "Conversão afiliados", "tipo": "percentual",
     "bom": "sobe"},
    {"chave": "cliques_ads", "rotulo": "Cliques Ads", "tipo": "inteiro", "bom": "neutro"},
    {"chave": "pedidos_ads", "rotulo": "Pedidos Ads", "tipo": "inteiro", "bom": "sobe"},
    {"chave": "conversao_ads", "rotulo": "Conversão Ads", "tipo": "percentual", "bom": "sobe"},
)
CHAVES = tuple(m["chave"] for m in METRICAS)
METRICA = {m["chave"]: m for m in METRICAS}
# Conversão = pedidos ÷ cliques × 100: (pedidos, cliques) de cada uma.
CONVERSOES: dict[str, tuple[str, str]] = {
    "conversao_afiliados": ("pedidos_afiliados", "cliques_afiliados"),
    "conversao_ads": ("pedidos_ads", "cliques_ads"),
}
# Recalculadas das somas (nunca somadas nem tiradas média).
DERIVADAS = ("pct", *CONVERSOES)
SOMAVEIS = tuple(c for c in CHAVES if c not in DERIVADAS)
# As que saem dos itens (a parte eletro); o saldo é da conta inteira.
DIVISIVEIS = tuple(c for c in SOMAVEIS if c != "saldo_ads")

GRUPOS = (("mala", "Mala"), ("celular", "Celular"), ("eletro", "Eletro"))
ROTULO_GRUPO = dict(GRUPOS)
STATUS_COM_DADOS = ("ok", "parcial")

# Grupo do Celular: o que não é eletro (ou não deu para separar) fica aqui.
_SECOES = {
    # métrica → (seção do total, campo do total, lista de itens, campo do item)
    "vendas_afiliados": ("afiliados", "vendas", "afiliados_itens", "vendas"),
    "invest_afiliados": ("afiliados", "comissao", "afiliados_itens", "comissao"),
    "vendas_ads": ("ads", "vendas", "ads_itens", "vendas"),
    "invest_ads": ("ads", "gasto", "ads_itens", "gasto"),
    "impressoes": ("ads", "impressoes", "ads_itens", "impressoes"),
    "vendas": ("vendas", "valor", "vendas_itens", "valor"),
    "cliques_afiliados": ("afiliados", "cliques", "afiliados_itens", "cliques"),
    "pedidos_afiliados": ("afiliados", "pedidos", "afiliados_itens", "pedidos"),
    "cliques_ads": ("ads", "cliques", "ads_itens", "cliques"),
    "pedidos_ads": ("ads", "pedidos", "ads_itens", "pedidos"),
}
# Métricas cujo total e cujos itens vêm de chamadas DIFERENTES da Shopee e
# não fecham: a parte eletro é o total dividido na proporção dos itens (ver o
# topo do arquivo), nunca a soma dos itens eletro tirada do total.
_RATEIO = frozenset({"cliques_afiliados"})
# Campos de item que um executor mais velho pode não mandar (os cliques por
# item de afiliados chegaram em 07/10/2026). Item eletro SEM o campo → a parte
# eletro dessa métrica é desconhecida: fica vazia em Eletro e o total inteiro
# em Celular (como quando a lista de itens não veio), nunca "0 de eletro" — e
# o par dela (pedidos ↔ cliques da mesma seção) vai junto para Celular. Nos
# cliques de afiliados (_RATEIO) basta QUALQUER item sem o campo.
_CAMPO_PODE_FALTAR = frozenset(
    {"cliques_afiliados", "pedidos_afiliados", "cliques_ads", "pedidos_ads"}
)
_NOME_SECAO = {
    "afiliados_itens": "afiliados",
    "ads_itens": "Ads",
    "vendas_itens": "vendas",
}

NOTAS_FIXAS = (
    "Fonte: Central do Vendedor da Shopee de cada loja (Afiliados do Vendedor, Shopee Ads, "
    "carteira de Ads e desempenho dos produtos), lida pelo executor do Mac no perfil do "
    "AdsPower da loja.",
    "% s/ vendas = (Invest. afiliados + Invest. Ads) ÷ Vendas × 100; no total do grupo, "
    "calculado com as somas do grupo.",
    "Conversão = pedidos ÷ cliques × 100 (afiliados: painel de Afiliados do Vendedor; Ads: "
    "Shopee Ads); no total do grupo, calculada com as somas. Impressões de afiliados: a "
    "Shopee não informa (fica \"—\").",
    "Vendas afiliados e Vendas Ads contam pedidos feitos; Vendas conta só os pagos — por "
    "isso podem passar de Vendas.",
    "Comissão de afiliados é estimada; a semana mais recente ainda pode mudar.",
    "Quem liga e desliga o Ads é o robô de horários (18h–22h); 'pausado' na hora da coleta "
    "é normal.",
    "Saldo Ads: na semana do relatório, o saldo no momento da coleta; nas anteriores, o "
    "saldo lido na coleta mais próxima do dia seguinte ao fim da semana (±1 dia) — sem "
    "leitura, fica vazio. Em Eletro fica vazio: o saldo é da conta inteira.",
    "Eletro: produto vinculado no DaVinci a SKU de eletro; sem vínculo, categoria da Shopee "
    "(100010 Eletrodomésticos, 100636 Casa e Decoração); sem categoria, pelo título. "
    "Celular = total da conta − Eletro.",
    "Cliques de afiliados de Eletro: o total de cliques da loja dividido na proporção dos "
    "cliques por produto (a Shopee conta os cliques por produto de outro jeito e a soma não "
    "bate com o total). Conversão do grupo: só as lojas que têm pedidos e cliques.",
)


# ───────────────────────────────────────────────────────────── números


def _num(valor: Any) -> float | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


# Folga para o quantize: com a precisão padrão (28 dígitos) um 1e30 num
# payload torto levantaria InvalidOperation e derrubaria o relatório.
_CONTEXTO = Context(prec=400)


def meio_para_cima(v: float | int, casas: int) -> Decimal:
    """`v` com `casas` decimais, meio para longe do zero, sobre o número como
    ele se escreve (a forma curta do float: 6.35 → 6,4; 2.675 → 2,68; 8.25 →
    8,3) — como o Excel mostra a célula. A tela (lib/conferencia.ts,
    `digitosMeioParaCima`) arredonda igual: mudou aqui, muda lá."""
    base = Decimal(v) if isinstance(v, int) else Decimal(repr(float(v)))
    return base.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP, context=_CONTEXTO)


def _arred(chave: str, valor: float | None) -> float | int | None:
    if valor is None:
        return None
    if METRICA[chave]["tipo"] == "inteiro":
        return int(round(valor))
    return float(meio_para_cima(valor, 2))


def soma(valores: Iterable[float | None]) -> float | None:
    """Vazio + x = x; tudo vazio = vazio."""
    total: float | None = None
    for v in valores:
        if v is not None:
            total = (total or 0.0) + v
    return total


def investimento(semana: Mapping[str, Any] | None) -> float | None:
    """Invest. afiliados + Invest. Ads (vazio + x = x; os dois vazios = vazio)."""
    s = semana or {}
    return soma([_num(s.get("invest_afiliados")), _num(s.get("invest_ads"))])


def pct_de(invest: float | None, vendas: float | None) -> float | None:
    """Investimento ÷ vendas × 100; vazio sem vendas (ou 0) ou sem investimento."""
    if invest is None or vendas is None or vendas == 0:
        return None
    return invest / vendas * 100


def conversao_de(pedidos: float | None, cliques: float | None) -> float | None:
    """Pedidos ÷ cliques × 100; vazio sem cliques (0 ou negativo) ou sem
    pedidos — nunca uma conversão negativa."""
    if pedidos is None or cliques is None or cliques <= 0 or pedidos < 0:
        return None
    return pedidos / cliques * 100


def _com_pct(valores: dict[str, Any]) -> dict[str, Any]:
    """Os valores somáveis + as derivadas (% s/ vendas e as conversões),
    calculadas dos valores JÁ arredondados, com 2 casas, na ordem de CHAVES."""
    saida = {c: valores.get(c) for c in SOMAVEIS}
    p = pct_de(investimento(valores), _num(valores.get("vendas")))
    saida["pct"] = None if p is None else float(meio_para_cima(p, 2))
    for chave, (pedidos, cliques) in CONVERSOES.items():
        c = conversao_de(_num(valores.get(pedidos)), _num(valores.get(cliques)))
        saida[chave] = None if c is None else float(meio_para_cima(c, 2))
    return {c: saida[c] for c in CHAVES}


def _conversoes_pareadas(semanas: Iterable[Mapping[str, Any]]) -> dict[str, float | None]:
    """As conversões de um TOTAL (grupo ou Geral): Σpedidos ÷ Σcliques só das
    linhas que têm os dois números. Linha com pedidos e sem cliques (coleta
    antiga, Celular que ficou vazio) somaria pedidos sem os cliques deles e
    inflaria a conversão do grupo."""
    lista = list(semanas)
    saida: dict[str, float | None] = {}
    for chave, (pedidos, cliques) in CONVERSOES.items():
        pares = [(_num(s.get(pedidos)), _num(s.get(cliques))) for s in lista]
        pares = [(p, c) for p, c in pares if p is not None and c is not None]
        conv = conversao_de(soma(p for p, _ in pares), soma(c for _, c in pares))
        saida[chave] = None if conv is None else float(meio_para_cima(conv, 2))
    return saida


def _vazio() -> dict[str, Any]:
    return dict.fromkeys(CHAVES)


# ───────────────────────────────────────────────────────────── variação


def _decimais(v: float, casas: int) -> str:
    """1234.5 → "1.234,50" (pt-BR). Meio arredonda para longe do zero sobre o
    número como ele se escreve (meio_para_cima: 8,25 → "8,3"; 6,35 → "6,4";
    o `format` do Python daria "8,2" e "6,3")."""
    texto = f"{meio_para_cima(v, casas):,.{casas}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def dinheiro(v: float | None, casas: int = 2) -> str:
    """"R$ 1.234,56"; vazio → "—"."""
    if v is None:
        return "—"
    return f"{'-' if v < 0 else ''}R$ {_decimais(abs(v), casas)}"


def inteiro(v: float | None) -> str:
    return "—" if v is None else _decimais(round(v), 0)


def percentual(v: float | None, casas: int = 1) -> str:
    """8.2 → "8,2%" (o valor já vem × 100); com `casas=2` → "8,20%" (o
    Resumo no formato da planilha)."""
    return "—" if v is None else f"{_decimais(v, casas)}%"


def formatar(v: float | None, tipo: str) -> str:
    if tipo == "dinheiro":
        return dinheiro(v)
    if tipo == "percentual":
        return percentual(v)
    return inteiro(v)


# Casas do % na planilha do Resumo — no valor ("7,50%") e na variação em p.p.
# ("▼ 0,01 p.p."). O CASAS_PLANILHA de lib/conferencia.ts é o mesmo.
CASAS_PLANILHA = 2


def formatar_planilha(v: float | None, tipo: str) -> str:
    """Célula do Resumo em formato de planilha: igual ao `formatar`, mas o %
    com 2 casas ("7,50%") — o `fmtPlanilha` da tela."""
    return percentual(v, CASAS_PLANILHA) if tipo == "percentual" else formatar(v, tipo)


def _cor(direcao: str, bom: str) -> str:
    if bom == "neutro":
        return "cinza"
    return "verde" if direcao == bom else "vermelho"


_SEM_VARIACAO = {"texto": "—", "direcao": None, "cor": "cinza"}
_IGUAL = {"texto": "=", "direcao": None, "cor": "cinza"}


def variacao(
    atual: float | None,
    anterior: float | None,
    tipo: str,
    bom: str,
    chave: str | None = None,
    casas: int = 1,
) -> dict[str, str | None]:
    """{"texto", "cor" (verde | vermelho | cinza), "direcao" (sobe | desce |
    None)} — de `anterior` para `atual`:
      • dinheiro/inteiro: (atual − anterior) ÷ |anterior| × 100 → "▲ 12,3%" /
        "▼ 4,1%"; anterior 0 e atual > 0 → "novo"; os dois 0 (ou iguais) →
        "="; algum vazio → "—".
      • percentual: diferença em pontos → "▲ 1,2 p.p." / "▼ 0,8 p.p."; iguais
        → "=". `casas` = as casas dos p.p.: a planilha do Resumo mostra o %
        com 2 casas e a variação também ("▼ 0,01 p.p."; com 1 casa uma
        conversão de 0,44% → 0,43% sairia "▼ 0,0 p.p." em vermelho).
      • cor: bom `sobe` → subir verde, cair vermelho; `desce` ao contrário;
        `neutro` sempre cinza.
      • Saldo Ads (chave `saldo_ads`) nunca é "novo": sai "—"."""
    a, b = _num(atual), _num(anterior)
    if a is None or b is None:
        return dict(_SEM_VARIACAO)
    if tipo == "percentual":
        d = a - b
        if d == 0:
            return dict(_IGUAL)
        direcao = "sobe" if d > 0 else "desce"
        seta = "▲" if d > 0 else "▼"
        return {"texto": f"{seta} {_decimais(abs(d), casas)} p.p.", "direcao": direcao,
                "cor": _cor(direcao, bom)}
    if b == 0:
        if a == 0:
            return dict(_IGUAL)
        # De 0 para negativo não há porcentagem que diga algo.
        if chave == "saldo_ads" or a < 0:
            return dict(_SEM_VARIACAO)
        return {"texto": "novo", "direcao": "sobe", "cor": _cor("sobe", bom)}
    p = (a - b) / abs(b) * 100
    if p == 0:
        return dict(_IGUAL)
    direcao = "sobe" if p > 0 else "desce"
    seta = "▲" if p > 0 else "▼"
    return {"texto": f"{seta} {_decimais(abs(p), 1)}%", "direcao": direcao,
            "cor": _cor(direcao, bom)}


def media3(semanas: Sequence[Mapping[str, Any]] | None, chave: str) -> float | None:
    """Média de S2..S4 para o "vs média 3 sem.": só os valores que existem
    entram; nenhum → vazio. Para o % é Σinvestimento ÷ Σvendas das três; para
    a conversão, Σpedidos ÷ Σcliques."""
    anteriores = list(semanas or [])[1:4]
    if chave == "pct":
        return pct_de(
            soma(investimento(s) for s in anteriores),
            soma(_num((s or {}).get("vendas")) for s in anteriores),
        )
    if chave in CONVERSOES:
        pedidos, cliques = CONVERSOES[chave]
        return conversao_de(
            soma(_num((s or {}).get(pedidos)) for s in anteriores),
            soma(_num((s or {}).get(cliques)) for s in anteriores),
        )
    valores = [v for v in (_num((s or {}).get(chave)) for s in anteriores) if v is not None]
    return sum(valores) / len(valores) if valores else None


def variacoes(semanas: Sequence[Mapping[str, Any]], chave: str) -> tuple[dict, dict]:
    """(vs semana anterior, vs média 3 sem.) de uma métrica de uma linha/total."""
    m = METRICA[chave]
    atual = _num((semanas[0] if semanas else {}).get(chave))
    anterior = _num((semanas[1] if len(semanas) > 1 else {}).get(chave))
    return (
        variacao(atual, anterior, m["tipo"], m["bom"], chave),
        variacao(atual, media3(semanas, chave), m["tipo"], m["bom"], chave),
    )


# ───────────────────────────────────────────────────────────── saldo


def _instante(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    if isinstance(valor, str) and valor:
        try:
            q = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            return None
        return q if q.tzinfo else q.replace(tzinfo=UTC)
    return None


def _data(valor: Any) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str) and len(valor) >= 10:
        try:
            return date.fromisoformat(valor[:10])
        except ValueError:
            return None
    return None


def saldo_da_semana(leituras: Iterable[tuple[Any, Any]], fim: date) -> float | None:
    """Saldo de Ads de uma semana passada: a leitura cujo DIA (em Brasília)
    é o mais perto de fim + 1, dentro de ±1 dia ([fim, fim + 2]); empate → a
    mais cedo. Nenhuma → vazio."""
    alvo = fim + timedelta(days=1)
    melhor: tuple[int, datetime, float] | None = None
    for lido_em, valor in leituras:
        quando = _instante(lido_em)
        v = _num(valor)
        if quando is None or v is None:
            continue
        distancia = abs((quando.astimezone(FUSO).date() - alvo).days)
        if distancia > 1:
            continue
        chave = (distancia, quando, v)
        if melhor is None or chave[:2] < melhor[:2]:
            melhor = chave
    return None if melhor is None else float(meio_para_cima(melhor[2], 2))


# ───────────────────────────────────────────────────────────── por conta


def _lista(valor: Any) -> list:
    """A lista dos `dados`, ou vazia se veio outra coisa (executor com defeito
    não pode derrubar o relatório da rodada inteira)."""
    return valor if isinstance(valor, list) else []


def _semanas(dados: Mapping) -> list[Mapping]:
    return [s for s in _lista(dados.get("semanas")) if isinstance(s, Mapping)]


def _nome_item(dados: Mapping) -> dict[str, str]:
    """item_id → nome, para a regra do título: o nome do produto (afiliados,
    vendas) antes do título do anúncio de Ads."""
    nomes: dict[str, str] = {}
    for lista in ("afiliados_itens", "vendas_itens", "ads_itens"):
        for semana in _semanas(dados):
            for item in _lista(semana.get(lista)):
                if not isinstance(item, Mapping):
                    continue
                item_id = item.get("item_id")
                nome = item.get("nome")
                if item_id is not None and nome:
                    nomes.setdefault(str(item_id), str(nome))
    return nomes


class _Classificador:
    """Decide (e lembra) o eletro de cada item_id de UMA conta."""

    def __init__(self, dados: Mapping, mapa_davinci: Mapping[str, bool]):
        self.categorias = classificacao.categorias_por_item(dados)
        self.mapa = {str(k): bool(v) for k, v in (mapa_davinci or {}).items()}
        self.nomes = _nome_item(dados)
        self._cache: dict[str, bool] = {}

    def eletro(self, item_id: Any, nome: Any) -> bool:
        if item_id is None:
            # Ads sem item (anúncio da loja, GMV Max): fica em Celular.
            return False
        chave = str(item_id)
        if chave not in self._cache:
            nome = self.nomes.get(chave) or (str(nome) if nome else None)
            self._cache[chave], _ = classificacao.classificar(
                chave, nome, self.categorias, self.mapa
            )
        return self._cache[chave]


def _semana_dos_dados(dados: Mapping, semana: Mapping) -> Mapping | None:
    """A semana dos `dados` com as mesmas datas (o executor manda na ordem do
    job, mas a data é que manda: semana que não bate fica vazia)."""
    for s in _semanas(dados):
        if (s.get("inicio"), s.get("fim")) == (
            semana["inicio"],
            semana["fim"],
        ):
            return s
    return None


def _total_e_eletro(
    s: Mapping | None, classificador: _Classificador | None
) -> tuple[dict[str, float | None], dict[str, float | None], list[str], list[str]]:
    """(total, parte eletro, seções sem itens, métricas sem o campo nos
    itens eletro) de uma semana de uma conta."""
    total: dict[str, float | None] = {}
    eletro: dict[str, float | None] = {}
    sem_itens: list[str] = []
    sem_campo: list[str] = []
    s = s or {}
    for chave, (secao, campo, lista, campo_item) in _SECOES.items():
        bloco = s.get(secao)
        total[chave] = _num(bloco.get(campo)) if isinstance(bloco, Mapping) else None
        itens = s.get(lista)
        if classificador is None:
            eletro[chave] = None
            continue
        if total[chave] is None:
            # A seção falhou e só os itens vieram (são chamadas separadas no
            # executor): vazia nos DOIS lados. Somar a parte eletro pelos
            # itens poria em Eletro e no Geral um número pela metade, sem a
            # parte de Celular da conta.
            eletro[chave] = None
            continue
        if not isinstance(itens, list):
            eletro[chave] = None
            if lista not in sem_itens:
                sem_itens.append(lista)
            continue
        validos = [it for it in itens if isinstance(it, Mapping)]
        valores_eletro = [
            _num(it.get(campo_item))
            for it in validos
            if classificador.eletro(it.get("item_id"), it.get("nome"))
        ]
        if chave in _RATEIO:
            eletro[chave] = _rateio(
                total[chave], valores_eletro, [_num(it.get(campo_item)) for it in validos]
            )
            if eletro[chave] is None:
                sem_campo.append(chave)
            continue
        if chave in _CAMPO_PODE_FALTAR and any(v is None for v in valores_eletro):
            # Executor antigo: o item eletro veio sem o campo. Não dá para
            # separar → vazio em Eletro, o total inteiro em Celular.
            eletro[chave] = None
            sem_campo.append(chave)
            continue
        eletro[chave] = sum((v or 0.0 for v in valores_eletro), 0.0)
    if classificador is not None:
        # Pedidos e cliques andam juntos: parte eletro desconhecida de um (o
        # total veio, a divisão não) → o outro também fica inteiro em Celular.
        # Senão Celular teria os cliques de eletro sem os pedidos deles (a
        # conversão de Celular cai) e Eletro os pedidos sem os cliques.
        for par in CONVERSOES.values():
            if any(total[c] is not None and eletro[c] is None for c in par):
                for c in par:
                    eletro[c] = None
    return total, eletro, sem_itens, sem_campo


def _rateio(
    total: float | None, eletro: Sequence[float | None], todos: Sequence[float | None]
) -> float | None:
    """A parte eletro de um total que NÃO é a soma dos itens (cliques de
    afiliados): total × Σitens eletro ÷ Σtodos os itens, inteiro (meio para
    cima). Sem item eletro → 0. Algum item sem o número → desconhecida (None):
    a proporção sairia torta."""
    if total is None:
        return None
    if not eletro:
        return 0.0
    if any(v is None for v in todos):
        return None
    soma_eletro = sum(max(v or 0.0, 0.0) for v in eletro)
    soma_todos = sum(max(v or 0.0, 0.0) for v in todos)
    if soma_todos <= 0 or soma_eletro <= 0 or total <= 0:
        return 0.0
    fracao = min(soma_eletro / soma_todos, 1.0)
    return float(meio_para_cima(total * fracao, 0))


def _linha(coleta: Mapping, semanas: list[dict], status: str) -> dict[str, Any]:
    dados = coleta.get("dados") or {}
    login = dados.get("login") if isinstance(dados.get("login"), Mapping) else {}
    usuario = (login or {}).get("username")
    conta_id = coleta.get("conta_id")
    return {
        "conta_id": str(conta_id) if conta_id is not None else None,
        "conta": coleta.get("nome") or "",
        "usuario": str(usuario) if usuario else None,
        "status": status,
        "erro": coleta.get("erro"),
        "avisos": [],
        "semanas": semanas,
    }


def _avisos_da_coleta(dados: Mapping, semanas: Sequence[Mapping]) -> list[str]:
    avisos = [str(a) for a in _lista(dados.get("avisos")) if a]
    for sem in semanas:
        s = _semana_dos_dados(dados, sem) or {}
        for a in _lista(s.get("avisos")):
            if a:
                avisos.append(f"{rotulo(sem['inicio'], sem['fim'])}: {a}")
    return avisos


def _chave_ordem(linha: Mapping) -> tuple[str, str]:
    return (classificacao.sem_acento(linha["conta"]), linha["conta"])


def _total(linhas: Sequence[Mapping], n_semanas: int) -> dict[str, Any]:
    semanas = []
    for i in range(n_semanas):
        valores = {c: soma(_num(lin["semanas"][i].get(c)) for lin in linhas) for c in SOMAVEIS}
        semana = _com_pct({c: _arred(c, v) for c, v in valores.items()})
        semana.update(_conversoes_pareadas(lin["semanas"][i] for lin in linhas))
        semanas.append(semana)
    return {
        "contas": len(linhas),
        "sem_dados": sum(1 for lin in linhas if lin["status"] not in STATUS_COM_DADOS),
        "semanas": semanas,
    }


def montar_relatorio(
    execucao: Mapping[str, Any],
    coletas: Iterable[Mapping[str, Any]],
    *,
    mapa_davinci: Mapping[str, Mapping[str, bool]] | None = None,
    saldos: Mapping[str, Iterable[tuple[Any, Any]]] | None = None,
    gerado_em: datetime | str,
) -> dict[str, Any]:
    """O relatório (versao 1, contrato §5) de uma execução.

    execucao: {"id", "tipo", "origem", "semanas" ([{inicio, fim}] × 4, 0 =
      S1), "afiliados_ate" (date ou "AAAA-MM-DD"), "criado_em"}.
    coletas: [{"conta_id", "conta_key", "adspower_user_id", "nome", "grupo"
      (mala | celular), "status", "erro", "dados" (ColetaDados | None)}] — a
      `conta_key` vem da conta (`conferencia_shopee_conta`), não da coleta.
    mapa_davinci: conta_key → {item_id: eletro?}
      (classificacao.carregar_mapa_davinci).
    saldos: adspower_user_id → [(lido_em, valor)] de conferencia_shopee_saldo.
    """
    semanas_exec = [
        {"inicio": str(s["inicio"])[:10], "fim": str(s["fim"])[:10]}
        for s in (execucao.get("semanas") or [])
    ][:4]
    afiliados_ate = _data(execucao.get("afiliados_ate")) or (
        date.fromisoformat(semanas_exec[0]["fim"]) if semanas_exec else None
    )
    mapa_davinci = mapa_davinci or {}
    saldos = saldos or {}

    linhas: dict[str, list[dict]] = {g: [] for g, _ in GRUPOS}
    sem_dados: list[dict] = []
    incompletos: list[dict] = []
    divergencias: list[dict] = []
    nao_atribuido: list[dict] = []
    contas_vistas: dict[str, bool] = {}

    for coleta in coletas:
        status = str(coleta.get("status") or "")
        grupo = "mala" if coleta.get("grupo") == "mala" else "celular"
        nome = coleta.get("nome") or ""
        dados = coleta.get("dados") if isinstance(coleta.get("dados"), Mapping) else None
        com_dados = status in STATUS_COM_DADOS and dados is not None
        id_conta = str(coleta.get("conta_id") or coleta.get("adspower_user_id") or nome)
        contas_vistas[id_conta] = status in STATUS_COM_DADOS

        if not com_dados:
            linha = _linha(coleta, [_vazio() for _ in semanas_exec], status)
            if dados:
                linha["avisos"] = _avisos_da_coleta(dados, semanas_exec)
            linhas[grupo].append(linha)
            if status not in STATUS_COM_DADOS:
                sem_dados.append({"conta": nome, "status": status, "erro": coleta.get("erro")})
            continue

        avisos = _avisos_da_coleta(dados, semanas_exec)
        ultimo = _data(dados.get("afiliados_ultimo_dia"))
        if ultimo is not None and afiliados_ate is not None and ultimo < afiliados_ate:
            incompletos.append({"conta": nome, "ate": ultimo.isoformat()})

        chave_conta = (coleta.get("conta_key") or "").strip().lower()
        mapa = mapa_davinci.get(chave_conta) or {}
        classificador = _Classificador(dados, mapa) if grupo == "celular" else None
        leituras = list(saldos.get(str(coleta.get("adspower_user_id") or ""), []))

        principal: list[dict] = []
        parte_eletro: list[dict] = []
        tem_eletro = False
        for i, sem in enumerate(semanas_exec):
            s = _semana_dos_dados(dados, sem)
            total, eletro, sem_itens, sem_campo = _total_e_eletro(s, classificador)
            if i == 0:
                saldo = _num(dados.get("saldo_ads"))
            else:
                saldo = saldo_da_semana(leituras, date.fromisoformat(sem["fim"]))
            if classificador is None:
                valores = dict(total)
            else:
                valores = {
                    c: None if total[c] is None else total[c] - (eletro[c] or 0.0)
                    for c in DIVISIVEIS
                }
                quando = rotulo(sem["inicio"], sem["fim"])
                for c in DIVISIVEIS:
                    resto = _arred(c, valores[c])
                    if resto is not None and resto < 0 and (eletro[c] or 0) > 0:
                        # A parte eletro passou do total da conta (fontes da
                        # Shopee que não batem): Celular negativo não existe.
                        # Celular vazio, Eletro com o total — o Geral continua
                        # sendo o total da conta.
                        valores[c] = None
                        eletro[c] = total[c]
                        avisos.append(
                            f"{quando}: {METRICA[c]['rotulo']} de eletro passou do total da "
                            "conta — Celular ficou sem esse número e Eletro com o total"
                        )
                for lista in sem_itens:
                    avisos.append(
                        f"{quando}: sem os itens de "
                        f"{_NOME_SECAO[lista]} — o eletro dessa parte ficou em Celular"
                    )
                for chave in sem_campo:
                    _, _, lista, campo = _SECOES[chave]
                    avisos.append(
                        f"{quando}: sem os {campo} por produto de {_NOME_SECAO[lista]} — "
                        "os cliques e os pedidos de eletro dessa parte ficaram em Celular"
                    )
                tem_eletro = tem_eletro or any((v or 0) > 0 for v in eletro.values())
                parte_eletro.append(
                    _com_pct({c: _arred(c, eletro[c]) for c in DIVISIVEIS} | {"saldo_ads": None})
                )
            valores["saldo_ads"] = saldo
            principal.append(_com_pct({c: _arred(c, valores.get(c)) for c in SOMAVEIS}))

        linha = _linha(coleta, principal, status)
        linha["avisos"] = avisos
        linhas[grupo].append(linha)
        if classificador is not None and tem_eletro:
            linha_eletro = _linha(coleta, parte_eletro, status)
            linha_eletro["avisos"] = []
            linhas["eletro"].append(linha_eletro)

        if classificador is not None:
            divergencias.extend(_divergencias(nome, dados, classificador))
            gasto = _gasto_sem_item(dados, semanas_exec[0] if semanas_exec else None)
            if gasto:
                nao_atribuido.append({"conta": nome, "gasto": float(meio_para_cima(gasto, 2))})

    grupos = []
    for chave, rot in GRUPOS:
        ordenadas = sorted(linhas[chave], key=_chave_ordem)
        grupos.append(
            {
                "chave": chave,
                "rotulo": rot,
                "linhas": ordenadas,
                "total": _total(ordenadas, len(semanas_exec)),
            }
        )
    geral_semanas = []
    for i in range(len(semanas_exec)):
        valores = {
            c: soma(_num(g["total"]["semanas"][i].get(c)) for g in grupos) for c in SOMAVEIS
        }
        semana = _com_pct({c: _arred(c, v) for c, v in valores.items()})
        # Conversão do Geral: os pares de TODAS as linhas (os totais de grupo
        # já perderam quais linhas tinham os dois números).
        semana.update(
            _conversoes_pareadas(lin["semanas"][i] for g in grupos for lin in g["linhas"])
        )
        geral_semanas.append(semana)

    return {
        "versao": VERSAO,
        "execucao_id": str(execucao.get("id") or ""),
        "tipo": execucao.get("tipo"),
        "origem": execucao.get("origem"),
        "gerado_em": _iso(gerado_em),
        "criado_em": _iso(execucao.get("criado_em")),
        "semanas": [{**s, "rotulo": rotulo(s["inicio"], s["fim"])} for s in semanas_exec],
        "metricas": [dict(m) for m in METRICAS],
        "grupos": grupos,
        "geral": {
            "contas": len(contas_vistas),
            "sem_dados": sum(1 for tem in contas_vistas.values() if not tem),
            "semanas": geral_semanas,
        },
        "notas": notas(execucao.get("tipo"), semanas_exec),
        "contas_sem_dados": sorted(sem_dados, key=lambda d: classificacao.sem_acento(d["conta"])),
        "afiliados_incompletos": sorted(
            incompletos, key=lambda d: classificacao.sem_acento(d["conta"])
        ),
        "divergencias": sorted(
            divergencias, key=lambda d: (classificacao.sem_acento(d["conta"]), d["item_id"])
        ),
        "nao_atribuido_ads": sorted(
            nao_atribuido, key=lambda d: classificacao.sem_acento(d["conta"])
        ),
    }


def _iso(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return (valor if valor.tzinfo else valor.replace(tzinfo=UTC)).isoformat()
    return str(valor)


def _divergencias(conta: str, dados: Mapping, classificador: _Classificador) -> list[dict]:
    """Itens vinculados no DaVinci cuja categoria da Shopee discorda."""
    saida = []
    for item_id in sorted(classificador.mapa):
        d = classificacao.divergencia(item_id, classificador.categorias, classificador.mapa)
        if d is None:
            continue
        saida.append(
            {"conta": conta, "item_id": item_id, "nome": classificador.nomes.get(item_id, ""), **d}
        )
    return saida


def _gasto_sem_item(dados: Mapping, s1: Mapping | None) -> float:
    """Gasto de Ads da S1 sem item (anúncio da loja, GMV Max): fica em Celular."""
    if s1 is None:
        return 0.0
    semana = _semana_dos_dados(dados, s1) or {}
    return sum(
        _num(it.get("gasto")) or 0.0
        for it in _lista(semana.get("ads_itens"))
        if isinstance(it, Mapping) and it.get("item_id") is None
    )


def notas(tipo: str | None, semanas: Sequence[Mapping[str, str]]) -> list[str]:
    """As notas fixas do relatório (pt-BR) e, na parcial, o trecho comparado."""
    saida = list(NOTAS_FIXAS)
    if tipo == "parcial" and semanas:
        de = dia_da_semana(semanas[0]["inicio"])
        ate = dia_da_semana(semanas[0]["fim"])
        saida.append(
            f"Semana parcial: {de} a {ate}, comparada com {de} a {ate} das semanas anteriores."
        )
    return saida
