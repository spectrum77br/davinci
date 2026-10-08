"""O perfil de cada plataforma da Conferência: Shopee, Mercado Livre e Amazon.

Decisões do dono (07/10/2026): o MESMO relatório (planilha Mala · Celular ·
Eletro · Geral, mesmas semanas, mesmas regras) para as três, um por
marketplace. O que muda de uma para outra mora aqui — nada de `if plataforma`
espalhado pelo cálculo e pelos arquivos:

  • rótulo ("Mercado Livre") e o pedaço do nome do arquivo;
  • quem coleta: o executor do Mac (Shopee, pelo AdsPower) ou o servidor (ML
    e Amazon, no worker — coletores/);
  • ESTADOS: células que não têm número por um motivo CONHECIDO, diferente do
    "—" (que quer dizer "a coleta falhou / não veio"):
      - `nao_coletado`      — existe, mas esta versão ainda não lê (afiliados do
                              ML: sem API; a leitura do painel pelo AdsPower vem
                              depois);
      - `nao_se_aplica`     — não existe na plataforma (afiliados da Amazon;
                              Saldo Ads no ML e na Amazon, que cobram depois);
      - `aguardando_acesso` — existe, mas falta o acesso (Ads da Amazon, até a
                              API de Anúncios ser conectada).
    A chave é a da métrica (calculo.METRICAS) ou `impressoes_afiliados` (a linha
    da planilha que não tem métrica). Célula com estado sai sem número em toda
    linha, total e Geral; a tela, o Excel, o HTML, o MD e o CSV escrevem o texto
    do estado no lugar do "—";
  • as seções que a coleta tem de trazer (sem uma delas a coleta é `parcial`);
  • as notas fixas do relatório.

O cadastro "Quem recebe" do Threema é o mesmo para as três (contexto
`conferencia_shopee`).
"""

from __future__ import annotations

from collections.abc import Mapping

PLATAFORMAS = ("shopee", "ml", "amazon")
PADRAO = "shopee"
# Coletadas no worker (coletores/); a Shopee é do executor do Mac.
SERVIDOR = ("ml", "amazon")

ROTULO = {"shopee": "Shopee", "ml": "Mercado Livre", "amazon": "Amazon"}
# conferencia-<slug>-<S1.inicio>_<S1.fim>.<ext> (o mesmo em lib/conferencia.ts;
# casos em apps/web/tests/conferencia-arredondamento.json → "nomes_arquivo").
SLUG_ARQUIVO = {"shopee": "shopee", "ml": "ml", "amazon": "amazon"}

NAO_COLETADO = "nao_coletado"
NAO_SE_APLICA = "nao_se_aplica"
AGUARDANDO_ACESSO = "aguardando_acesso"
ESTADOS_VALIDOS = (NAO_COLETADO, NAO_SE_APLICA, AGUARDANDO_ACESSO)
ROTULO_ESTADO = {
    NAO_COLETADO: "não coletado",
    NAO_SE_APLICA: "não se aplica",
    AGUARDANDO_ACESSO: "aguardando acesso",
}
# A linha "Impressões · afiliados" da planilha não tem métrica: o estado dela
# usa esta chave.
IMPRESSOES_AFILIADOS = "impressoes_afiliados"

_AFILIADOS = (
    "vendas_afiliados",
    IMPRESSOES_AFILIADOS,
    "conversao_afiliados",
    "invest_afiliados",
    "cliques_afiliados",
    "pedidos_afiliados",
)
_ADS = ("vendas_ads", "impressoes", "conversao_ads", "invest_ads", "cliques_ads", "pedidos_ads")

ESTADOS: dict[str, dict[str, str]] = {
    # A Shopee fica como foi aprovada em 07/10/2026 (impressões de afiliados "—").
    "shopee": {},
    "ml": {
        **dict.fromkeys(_AFILIADOS, NAO_COLETADO),
        "saldo_ads": NAO_SE_APLICA,
    },
    "amazon": {
        **dict.fromkeys(_AFILIADOS, NAO_SE_APLICA),
        **dict.fromkeys(_ADS, AGUARDANDO_ACESSO),
        "saldo_ads": NAO_SE_APLICA,
        # Sem Ads e sem afiliados não há investimento para dividir.
        "pct": AGUARDANDO_ACESSO,
    },
}

# Seções do ColetaDados que a coleta do servidor tem de trazer em toda semana;
# faltou alguma → a coleta fica `parcial` (os avisos dizem o quê).
SECOES_ESPERADAS: dict[str, tuple[str, ...]] = {
    "shopee": ("afiliados", "ads", "vendas"),
    "ml": ("vendas", "ads"),
    "amazon": ("vendas",),
}

_NOTA_VENDAS_BLING = (
    "Vendas: itens dos pedidos do Bling da loja na semana (pela data do pedido, a mesma da "
    "aba Faturamento), pelo valor de tabela dos produtos (valor do item × quantidade): sem "
    "frete e SEM tirar os descontos e promoções do pedido — pode ficar acima do que o "
    "cliente pagou. Pedidos cancelados na hora da coleta ficam de fora; por isso uma semana "
    "antiga refeita pode mudar (cancelamento depois)."
)
_NOTA_ELETRO_SKU = (
    "Eletro: pelo SKU de cada item (a lista de eletro do DaVinci: SKU, categoria do Bling "
    "'Eletro…' ou segmento Eletro); Celular = total da conta − Eletro. Contas de Mala entram "
    "inteiras em Mala."
)
_NOTA_PCT = (
    "% investimento / vendas = (Invest. afiliados + Invest. Ads) ÷ Vendas × 100; no total do "
    "grupo, calculado com as somas do grupo."
)

NOTAS: dict[str, tuple[str, ...]] = {
    "ml": (
        "Fonte: pedidos do Bling no DaVinci (Vendas) e a API de Anúncios do Mercado Livre "
        "(Product Ads, dia a dia), lidos pelo servidor — sem AdsPower.",
        _NOTA_VENDAS_BLING,
        "Ads: Vendas Ads = receita total do Product Ads (venda direta + indireta), Impressões, "
        "Cliques, Invest. Ads = custo e Pedidos Ads = unidades vendidas pelo anúncio. "
        "Conversão Ads = pedidos Ads ÷ cliques × 100. Só Product Ads (Brand Ads e Display não "
        "entram).",
        "O Mercado Livre atribui a venda ao anúncio até 14 dias depois do clique e fecha o custo "
        "com alguns dias de atraso: a semana mais recente ainda cresce depois do relatório.",
        "Afiliados (Venda com Afiliados): o Mercado Livre não dá esses números pela API — ficam "
        "\"não coletado\" nesta versão. Por isso o % investimento / vendas do Mercado Livre é "
        "só o investimento em Ads.",
        _NOTA_PCT,
        _NOTA_ELETRO_SKU
        + " No Ads, o anúncio vai para o SKU pelo vínculo do DaVinci (anúncio com variações é "
        "eletro se alguma variação for; sem vínculo, pelo título); conta ou semana sem os "
        "anúncios por item fica com o Ads inteiro em Celular (com aviso).",
        "Saldo Ads: não existe no Mercado Livre (o Product Ads é cobrado depois, na fatura).",
    ),
    "amazon": (
        "Fonte: pedidos do Bling no DaVinci (Vendas), lidos pelo servidor — sem AdsPower.",
        _NOTA_VENDAS_BLING,
        "Ads (Vendas Ads, Impressões, Conversão e Invest. Ads) e o % investimento / vendas: "
        "\"aguardando acesso\" até a API de Anúncios da Amazon ser conectada.",
        "Afiliados: não se aplica na Amazon (o programa de Associados é pago pela própria "
        "Amazon).",
        _NOTA_ELETRO_SKU,
        "Saldo Ads: não existe na Amazon (os anúncios são cobrados depois).",
    ),
}


def valida(plataforma: str | None) -> str:
    """A plataforma (minúscula) ou ValueError; None → Shopee (o padrão)."""
    p = (plataforma or PADRAO).strip().lower()
    if p not in PLATAFORMAS:
        raise ValueError(f"plataforma desconhecida: {plataforma!r}")
    return p


def de(rel_ou_execucao: Mapping | None) -> str:
    """A plataforma de um relatório/execução; relatório antigo (sem o campo) =
    Shopee."""
    p = (rel_ou_execucao or {}).get("plataforma") or PADRAO
    return p if p in PLATAFORMAS else PADRAO


def rotulo(plataforma: str | None) -> str:
    return ROTULO.get(plataforma or PADRAO, ROTULO[PADRAO])


def estados(plataforma: str | None) -> dict[str, str]:
    return dict(ESTADOS.get(plataforma or PADRAO, {}))


def rotulo_estado(estado: str | None) -> str | None:
    """O texto do estado ("não coletado"); estado que esta versão não conhece
    sai com o sublinhado virando espaço ("sem_api" → "sem api"), como na tela;
    sem estado → None."""
    if not estado:
        return None
    return ROTULO_ESTADO.get(estado) or str(estado).replace("_", " ")


def do_servidor(plataforma: str | None) -> bool:
    return (plataforma or PADRAO) in SERVIDOR
