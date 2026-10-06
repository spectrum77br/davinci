"""O relatório da Conferência Shopee (versao 1) — função PURA sobre as coletas.

Entra: a execução (semanas, tipo, origem), as coletas (status, erro e os
números crus `dados`, ColetaDados versao 1), o mapa de eletro do DaVinci e o
histórico de saldos de Ads. Sai: o JSON congelado em
`conferencia_shopee_execucao.relatorio` — o que a tela, o Excel, o CSV, o MD,
o HTML e o Threema mostram. "Recalcular" chama de novo com os `dados`
guardados: nada aqui lê banco nem relógio.

Por conta e semana:
  • total da conta: afiliados (vendas, comissão), Ads (vendas, gasto,
    impressões) e vendas pagas;
  • parte eletro: as mesmas métricas somadas só nos itens eletro
    (classificacao.classificar, item a item);
  • Mala → uma linha em Mala com o total. Celular → linha em Celular com
    total − eletro (seção que veio vazia continua vazia) e linha em Eletro
    com a parte eletro, só se a conta teve algum valor eletro > 0 em alguma
    das 4 semanas;
  • Saldo Ads: S1 = o saldo lido nesta coleta; S2..S4 = a leitura mais
    perto do dia seguinte ao fim da semana, ±1 dia (empate: a mais cedo).
    Em Eletro, sempre vazio (o saldo é da conta inteira).

Totais de grupo somam as linhas (vazio + x = x; tudo vazio = vazio) e o % do
grupo sai das SOMAS, nunca da média dos percentuais. Geral = Mala + Celular +
Eletro.

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
)
CHAVES = tuple(m["chave"] for m in METRICAS)
METRICA = {m["chave"]: m for m in METRICAS}
# Tudo menos o %, que é recalculado das somas.
SOMAVEIS = tuple(c for c in CHAVES if c != "pct")
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
}
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


def _com_pct(valores: dict[str, Any]) -> dict[str, Any]:
    p = pct_de(investimento(valores), _num(valores.get("vendas")))
    saida = {c: valores.get(c) for c in CHAVES if c != "pct"}
    saida["pct"] = None if p is None else float(meio_para_cima(p, 2))
    return {c: saida[c] for c in CHAVES}


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


def percentual(v: float | None) -> str:
    """8.2 → "8,2%" (o valor já vem × 100)."""
    return "—" if v is None else f"{_decimais(v, 1)}%"


def formatar(v: float | None, tipo: str) -> str:
    if tipo == "dinheiro":
        return dinheiro(v)
    if tipo == "percentual":
        return percentual(v)
    return inteiro(v)


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
) -> dict[str, str | None]:
    """{"texto", "cor" (verde | vermelho | cinza), "direcao" (sobe | desce |
    None)} — de `anterior` para `atual`:
      • dinheiro/inteiro: (atual − anterior) ÷ |anterior| × 100 → "▲ 12,3%" /
        "▼ 4,1%"; anterior 0 e atual > 0 → "novo"; os dois 0 (ou iguais) →
        "="; algum vazio → "—".
      • percentual: diferença em pontos → "▲ 1,2 p.p." / "▼ 0,8 p.p."; iguais
        → "=".
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
        return {"texto": f"{seta} {_decimais(abs(d), 1)} p.p.", "direcao": direcao,
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
    entram; nenhum → vazio. Para o % é Σinvestimento ÷ Σvendas das três."""
    anteriores = list(semanas or [])[1:4]
    if chave == "pct":
        return pct_de(
            soma(investimento(s) for s in anteriores),
            soma(_num((s or {}).get("vendas")) for s in anteriores),
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
) -> tuple[dict[str, float | None], dict[str, float | None], list[str]]:
    """(total, parte eletro, seções sem itens) de uma semana de uma conta."""
    total: dict[str, float | None] = {}
    eletro: dict[str, float | None] = {}
    sem_itens: list[str] = []
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
        eletro[chave] = sum(
            (
                _num(it.get(campo_item)) or 0.0
                for it in itens
                if isinstance(it, Mapping)
                and classificador.eletro(it.get("item_id"), it.get("nome"))
            ),
            0.0,
        )
    return total, eletro, sem_itens


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
        semanas.append(_com_pct({c: _arred(c, v) for c, v in valores.items()}))
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
            total, eletro, sem_itens = _total_e_eletro(s, classificador)
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
                for lista in sem_itens:
                    avisos.append(
                        f"{rotulo(sem['inicio'], sem['fim'])}: sem os itens de "
                        f"{_NOME_SECAO[lista]} — o eletro dessa parte ficou em Celular"
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
        geral_semanas.append(_com_pct({c: _arred(c, v) for c, v in valores.items()}))

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
