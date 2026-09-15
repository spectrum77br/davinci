"""Amazon — DBA × Envio próprio e contas de prazo (funções puras, sem banco).

A Amazon trata "Delivery by Amazon" (DBA) e "Envio próprio" como dois painéis
separados no Seller Central (Gerenciar pedidos › Logística pelo vendedor).
Os dois são MFN (o vendedor emite a NF e embala), mas a entrega é de quem
transporta: no DBA a própria Amazon coleta e entrega; no Envio próprio o
vendedor posta nos Correios e RESPONDE pela entrega — passado o prazo da
Amazon o cliente é reembolsado.

Sinais que separam os dois (verificados em produção, 15/09/2026):
- SP-API: pedido DBA vem com `EasyShipShipmentStatus` (PendingPickUp, PickedUp,
  Delivered…); Envio próprio vem `FulfillmentChannel=MFN` SEM EasyShip. AFN é
  FBA (Logística da Amazon).
- Bling: o volume do pedido vem com `servico` "Logistica Amazon Dba" no DBA e o
  serviço dos Correios ("SEDEX", "PAC") no Envio próprio.
"""

from __future__ import annotations

from datetime import date, timedelta

CANAL_DBA = "dba"
CANAL_PROPRIO = "proprio"
CANAL_FBA = "fba"

CANAL_LABELS_PT: dict[str, str] = {
    CANAL_DBA: "Amazon DBA",
    CANAL_PROPRIO: "Envio próprio",
    CANAL_FBA: "Logística da Amazon (FBA)",
}


def canal_por_servico_bling(servico: str | None) -> str | None:
    """'dba' quando o serviço do volume no Bling é o da Amazon ("Logistica
    Amazon Dba"); 'proprio' pra qualquer outro serviço preenchido (SEDEX, PAC,
    Mini Envios…); None sem serviço."""
    s = (servico or "").strip().lower()
    if not s:
        return None
    if "amazon" in s or "dba" in s:
        return CANAL_DBA
    return CANAL_PROPRIO


def classificar(meli_status: dict | None, servico_envio: str | None = None) -> str | None:
    """Canal do pedido a partir da assinatura da SP-API (`meli_status`) e, como
    segundo sinal, do serviço do volume no Bling. A SP-API manda: EasyShip
    presente é DBA sempre; AFN é FBA; MFN sem EasyShip é Envio próprio. Sem
    resposta da SP-API ainda, vale o Bling. None = sem sinal nenhum."""
    m = meli_status or {}
    easy = str(m.get("easyship_status") or "").strip()
    canal_sp = str(m.get("fulfillment_channel") or "").strip().upper()
    if easy:
        return CANAL_DBA
    if canal_sp == "AFN":
        return CANAL_FBA
    pelo_bling = canal_por_servico_bling(servico_envio)
    if canal_sp == "MFN":
        # MFN sem EasyShip: Envio próprio — a menos que o Bling diga DBA (pedido
        # DBA recém-criado, antes de a Amazon preencher o EasyShip).
        return pelo_bling or CANAL_PROPRIO
    return pelo_bling


def dias_uteis_apos(inicio: date, dias: int) -> date:
    """`inicio` + `dias` dias ÚTEIS (segunda a sexta; feriados não entram — é a
    mesma conta que o Bling faz na "Data de entrega" da cotação: 14/09 + 7 =
    23/09). Zero ou negativo devolve o próprio `inicio`."""
    atual = inicio
    restantes = max(0, int(dias))
    while restantes > 0:
        atual += timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def previsao_correios(data_saida: date | None, prazo_dias: int | None) -> date | None:
    """Previsão de entrega dos Correios = data de saída + prazo previsto em dias
    úteis (objeto de postagem do Bling). None sem data de saída."""
    if data_saida is None:
        return None
    return dias_uteis_apos(data_saida, prazo_dias or 0)


def eh_email_relay_amazon(email: str | None) -> bool:
    """Só o endereço de retransmissão da Amazon (`…@marketplace.amazon.com.br`,
    `…@marketplace.amazon.com`) pode receber mensagem — e-mail real de cliente
    nunca é usado (política de comunicação da Amazon)."""
    e = (email or "").strip().lower()
    return "@" in e and e.split("@", 1)[1].startswith("marketplace.amazon.")
