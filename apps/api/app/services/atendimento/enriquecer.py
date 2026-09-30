"""Pedido, produto e foto das lojas na caixa de atendimento — o que o Duoke mostra.

O Eduardo quer a caixa "com a cara do Duoke": a mensagem de produto vira um
cartão com FOTO, título e preço; a de pedido, um cartão com o status e o
total; e o painel da direita mostra o pedido como a loja o vê (itens com
foto, valor pago, pagamento, transportadora, rastreio). Tudo isso vem da API
da própria loja — este módulo busca, traduz para português e guarda no
formato que a tela lê (contrato na spec do atendimento com a cara do Duoke):

  anexo da mensagem   {"tipo": "produto", ...} / {"tipo": "pedido", ...}
  conversa.dados      ["pedido_mkt"] (o retrato do pedido) e, na pergunta do
                      ML, ["produto"] (o cartão do anúncio perguntado)

Por que cada cuidado:

- SÓ LEITURA. Nada aqui escreve na loja: GET de pedido, produto e rastreio.
- SEM DADO PESSOAL A MAIS. O retrato leva pedido, itens, valores, pagamento e
  logística — nunca nome, endereço, CPF ou telefone do comprador, mesmo
  quando a resposta da API os traz (o ML traz). A descrição livre do rastreio
  (texto da transportadora) só entra quando não tem NENHUM sinal de dado
  pessoal; com qualquer sinal, fica de fora inteira (`_descricao_segura`).
- ECONOMIA DE CHAMADA. O cartão de produto fica 24 h no Redis (o mesmo
  anúncio aparece em dezenas de conversas); o retrato do pedido é renovado no
  máximo a cada 30 min por conversa; e cada rodada do sync tem uma COTA de
  enriquecimentos por canal (`Cota`), para a loja com 300 conversas novas não
  estourar o limite da API nem a trava de 4 min da rodada. Falha ESPAÇA a
  próxima tentativa (30 min → 2 h → 12 h → 24 h; pedido que a loja diz não
  existir, 24 h direto): o que nunca vai dar certo não pode gastar a cota
  de toda rodada na frente de quem espera resposta.
- FALHA NÃO DERRUBA O SYNC. Erro da loja (ou de rede) vira log e o cartão
  fica com o que já tinha — a mensagem é o que importa; a foto é enfeite.
  Sem a API (TikTok sem escopo, Amazon), o cartão sai do nosso catálogo
  (`listings`), quando o anúncio está lá.

Hora de envio e tempo concluído (28/09/2026, "como no Duoke"): o retrato
leva `enviado_em` (a coleta/postagem) e `concluido_em` (o pedido concluído /
entregue), em ISO UTC ou None — sem inventar: sem o evento, fica None.

Índice de pedidos do comprador: a Shopee não filtra pedido por comprador, e o
cartão "Cliente" precisa do histórico de compra. Cada retrato que dá certo
grava a linha do pedido (`indice.registrar_pedido`) com o id do comprador na
PLATAFORMA — número, não dado pessoal —, que nunca entra no retrato.

Log só com ids (conversa, pedido, anúncio) — nunca texto de comprador.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    Listing,
)
from app.redis_client import redis
from app.services.atendimento import gravar
from app.services.atendimento.constantes import CANAL_PERGUNTA

logger = structlog.get_logger()

MOEDA = "BRL"
# Plataformas cujo cliente sabe buscar pedido/produto (TikTok ainda sem
# escopo; Amazon fica para quando a caixa de lá existir).
PLATAFORMAS_COM_API = ("shopee", "ml")

# ── Economia de chamada ───────────────────────────────────────────────────
CACHE_PRODUTO_S = 24 * 3600
RENOVAR_DEPOIS_DE = timedelta(minutes=30)
# Por canal, por rodada: cada unidade é UM cartão ou UM retrato (que pode
# custar até 3 idas à loja: pedido + rastreio + eventos, ou pedido + envio +
# fotos). O resto fica para a rodada seguinte (a de 2 min).
MAX_POR_RODADA = 20
# E um teto de TEMPO: a trava da rodada do canal dura 240 s (sync.TRAVA_TTL_S);
# o enriquecimento é enfeite e não pode empurrar a leitura para fora dela.
TEMPO_MAX_RODADA_S = 90.0

# Onde o carimbo das tentativas fica na conversa: `{"pedido_mkt": {...},
# "produto": {...}}`, cada um `{"id", "em", "resultado", "falhas", "proxima"}`
# (`carimbo()`). Guarda a TENTATIVA (deu certo ou não): pedido que a loja não
# devolve não é pedido de novo a cada 2 min.
CHAVE_CARIMBOS = "enriquecimento"
CHAVE_PEDIDO = "pedido_mkt"
CHAVE_PRODUTO = "produto"

# Resultado da última ida à loja (vai no carimbo).
RESULTADO_OK = "ok"
RESULTADO_ERRO = "erro"  # rede, 5xx, 429, erro da API: passa sozinho
RESULTADO_NAO_ENCONTRADO = "nao_encontrado"  # 404/403, pedido vazio: não passa
# `retrato_pedido` com a cota da rodada já no fim: nem foi à loja (não carimba).
_SEM_COTA = "sem_cota"
# Quanto esperar depois de uma falha, pela quantidade de falhas SEGUIDAS do
# mesmo pedido/anúncio. Sem isto, a conversa que nunca ganha retrato (pack
# que a loja não acha, anúncio apagado) voltava a cada 30 min PARA SEMPRE, na
# frente das que esperam resposta, gastando a cota inteira da rodada — e é
# o mesmo Client ID do ML que a importação de pedidos e os robôs usam.
ESPERAS_FALHA = (
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
    timedelta(hours=24),
)
# A loja disse que não existe (404/403, ou devolveu vazio): tentar de novo em
# 30 min não muda nada. Pedido novo na conversa (outro id) tenta na hora; o
# botão "atualizar" da tela também passa por cima.
ESPERA_NAO_ENCONTRADO = timedelta(hours=24)
# `enriquecer_canal` só completa sem retrato a conversa que andou nesta
# janela (ou que espera resposta): a de meses atrás não precisa de painel.
JANELA_SEM_RETRATO = timedelta(days=7)
# Pack do ML (carrinho) com muitos pedidos: cada pedido é uma ida ao /orders.
MAX_PEDIDOS_PACK = 10

# ── Textos em português (os códigos crus continuam no campo `status`) ─────
STATUS_PEDIDO_SHOPEE = {
    "UNPAID": "Aguardando pagamento",
    "READY_TO_SHIP": "Pronto para enviar",
    "PROCESSED": "Processado",
    "RETRY_SHIP": "Reenviar",
    "SHIPPED": "Enviado",
    "TO_CONFIRM_RECEIVE": "Para confirmar recebimento",
    "IN_CANCEL": "Cancelando",
    "CANCELLED": "Cancelado",
    "TO_RETURN": "Em devolução",
    "COMPLETED": "Concluído",
    # Status oficial no BR: o pedido espera a NF-e da loja para seguir
    # (o mesmo que `logistica_rules` e `marketplaces/shopee` reconhecem).
    "INVOICE_PENDING": "Aguardando nota fiscal",
}
STATUS_LOGISTICA_SHOPEE = {
    "LOGISTICS_READY": "Pronto para coleta",
    "LOGISTICS_REQUEST_CREATED": "Coleta solicitada",
    "LOGISTICS_PICKUP_DONE": "Coletado",
    "LOGISTICS_DELIVERY_DONE": "Entregue",
    "LOGISTICS_DELIVERY_FAILED": "Falha na entrega",
    "LOGISTICS_LOST": "Extraviado",
    # Além dos da spec: os que o repositório já traduz (`logistica_rules`),
    # para o selo não sair em inglês.
    "LOGISTICS_NOT_START": "Aguardando envio",
    "LOGISTICS_PENDING_ARRANGE": "Aguardando postagem",
    "LOGISTICS_PICKUP_RETRY": "Nova tentativa de coleta",
    "LOGISTICS_PICKUP_FAILED": "Falha na coleta",
    "LOGISTICS_REQUEST_CANCELED": "Coleta cancelada",
    "LOGISTICS_INVALID": "Envio inválido",
}
# Antes da coleta combinada não há rastreio: duas idas à Shopee a menos.
_SHOPEE_SEM_RASTREIO = frozenset(
    {"LOGISTICS_NOT_START", "LOGISTICS_PENDING_ARRANGE", "LOGISTICS_READY"}
)
# Sem pacote (`package_list` vazio), só vale perguntar o rastreio do pedido
# que já saiu — antes disso a Shopee devolve vazio e são 2 idas à toa.
_SHOPEE_JA_SAIU = frozenset({"SHIPPED", "TO_CONFIRM_RECEIVE", "COMPLETED", "TO_RETURN"})
# Evento de rastreio em que o pacote JÁ ESTÁ com a transportadora: o primeiro
# deles é a "Hora de envio". Os dois vocabulários, porque o do evento não foi
# medido (o pedido sondado em 28/09 ainda não tinha evento): o do pacote
# (`LOGISTICS_*`) e o dos eventos na doc da Shopee (`PICKED_UP`...).
_SHOPEE_DEPOIS_DA_COLETA = frozenset(
    {
        "LOGISTICS_PICKUP_DONE",
        "LOGISTICS_DELIVERY_DONE",
        "LOGISTICS_DELIVERY_FAILED",
        "LOGISTICS_LOST",
        "PICKED_UP",
        "DELIVERY_PENDING",
        "DELIVERED",
        "FAILED_DELIVERED",
        "LOST",
    }
)
_SHOPEE_CONCLUIDO = "COMPLETED"
# Resumo dos itens no índice de pedidos do comprador (a linha do tempo do
# cartão "Cliente" mostra "2x Mochila..."): curto de propósito.
RESUMO_ITENS_MAX = 300
PAGAMENTO_SHOPEE = {
    "credit card": "Cartão de crédito",
    "pix": "Pix",
    "boleto": "Boleto",
}
STATUS_PEDIDO_ML = {
    "paid": "Pago",
    "confirmed": "Confirmado",
    "payment_required": "Aguardando pagamento",
    "cancelled": "Cancelado",
    "invalid": "Inválido",
}
STATUS_ENVIO_ML = {
    "pending": "Pendente",
    "handling": "Em preparação",
    "ready_to_ship": "Pronto para enviar",
    "shipped": "A caminho",
    "delivered": "Entregue",
    "not_delivered": "Não entregue",
    "cancelled": "Cancelado",
}
# A "descrição mais recente" do envio do ML é o `substatus`. Só os comuns;
# o que não estiver aqui fica sem descrição (o código cru não ajuda ninguém).
SUBSTATUS_ENVIO_ML = {
    "buffered": "Aguardando liberação para envio",
    "shipment_paid": "Envio pago",
    "ready_to_print": "Etiqueta pronta para imprimir",
    "printed": "Etiqueta impressa",
    "in_packing_list": "Na lista de coleta",
    "in_hub": "No centro de distribuição",
    "picked_up": "Coletado",
    "dropped_off": "Entregue na agência",
    "in_transit": "Em trânsito",
    "out_for_delivery": "Saiu para entrega",
    "receiver_absent": "Destinatário ausente",
    "waiting_for_withdrawal": "Aguardando retirada",
    "delayed": "Atrasado",
    "returning_to_sender": "Voltando ao remetente",
    "returned": "Devolvido",
    "lost": "Extraviado",
    "stolen": "Roubado",
    "damaged": "Danificado",
}
PAGAMENTO_ML = {
    "credit_card": "Cartão de crédito",
    "debit_card": "Cartão de débito",
    "account_money": "Saldo Mercado Pago",
    "ticket": "Boleto",
    "bank_transfer": "Pix/Transferência",
    "digital_currency": "Linha de crédito Mercado Pago",
}

# Sinais de dado pessoal na descrição livre do rastreio. A transportadora às
# vezes põe quem recebeu, o documento dele ou o endereço. MASCARAR texto
# livre fura (nome não tem formato: a máscara antiga cortava o nome no
# primeiro ponto e deixava 9 dos 11 dígitos do CPF), então a regra é outra:
# qualquer sinal → a descrição inteira fica de fora e o painel mostra só o
# estado traduzido. Falso positivo ("Retirado pelo comprador") custa só a
# frase de um evento; falso negativo põe nome de comprador no banco.
_RE_DADO_PESSOAL = re.compile(
    r"""
      \d(?:[\s./-]?\d){6,}                     # 7+ dígitos: CPF, RG, CEP, telefone, CNPJ
    | \b(?:cpf|rg|cnpj|cnh|documento|identidade)\b | \bdoc\.
    | \b(?:recebid[oa]|retirad[oa]|assinad[oa]|entregue)\s+(?:por|pel[oa]|a|ao|à|aos|às|para|pra)\b
    | \b(?:recebedor|recebedora|recebeu|retirou|assinou|assinatura)\b
    | \bdestinat[aá]ri[oa]s?\b
      (?!\s+(?:ausente|n[aã]o|desconhecid[oa]|mudou|recusou|inexistente)\b)
    | \b(?:received|signed|picked\s+up)\s+by\b | \bdelivered\s+to\b | \b(?:recipient|receiver)\b
    | \b(?:rua|avenida|av\.|travessa|alameda|estrada|rodovia|pra[çc]a|largo|quadra|lote
         |apto|apartamento|bloco|condom[ií]nio|bairro|cep)(?!\w)
    | \bn[º°o]\.?\s*\d
    | \b(?:tel|telefone|fone|celular|whatsapp)\b
    | @
    """,
    re.IGNORECASE | re.VERBOSE,
)
_RE_FOTO_ML_PEQUENA = re.compile(r"-I\.(jpe?g|png|webp)$", re.IGNORECASE)
_RE_FRACAO = re.compile(r"(\.\d{6})\d+")
_RE_CODIGO_SHOPEE = re.compile(r"^\S+ ([a-z][a-z0-9_.]*):")
DESCRICAO_MAX = 300


def _agora() -> datetime:
    """Relógio do módulo (os testes trocam)."""
    return datetime.now(UTC)


# ── Cota da rodada ────────────────────────────────────────────────────────


@dataclass
class Cota:
    """Quantos enriquecimentos a rodada de UM canal ainda pode fazer.

    Cada unidade é um cartão de produto ou um retrato de pedido buscado na
    loja (o que vem do cache do Redis não gasta). `retratos` guarda o que a
    rodada já buscou: o cartão da mensagem de pedido e o painel da conversa
    do mesmo pedido saem de UMA ida à loja.
    """

    restam: int = MAX_POR_RODADA
    segundos: float = TEMPO_MAX_RODADA_S
    retratos: dict[str, dict | None] = field(default_factory=dict)
    # Por que o retrato não veio (`RESULTADO_ERRO`/`RESULTADO_NAO_ENCONTRADO`):
    # é o que o carimbo usa para espaçar a próxima tentativa.
    falhas: dict[str, str] = field(default_factory=dict)
    inicio: float = field(default_factory=time.monotonic)

    @classmethod
    def sem_teto(cls) -> Cota:
        """Uma "cota" sem limite, para quem não é a rodada (o botão da tela).

        Só para ter a mesma memória (`retratos`/`falhas`) nos dois caminhos.
        """
        return cls(restam=sys.maxsize, segundos=math.inf)

    def tem(self) -> bool:
        return self.restam > 0 and time.monotonic() - self.inicio < self.segundos

    def gastar(self) -> bool:
        if not self.tem():
            return False
        self.restam -= 1
        return True


# ── Conversões ────────────────────────────────────────────────────────────


def _texto(valor: Any) -> str | None:
    """Texto limpo, ou None para vazio. Número vira texto (ids da Shopee são int)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int | float):
        valor = str(valor)
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    return valor or None


def _valor(bruto: Any) -> float | None:
    """Dinheiro em reais como float de 2 casas (a Shopee manda int ou float)."""
    if bruto is None or isinstance(bruto, bool):
        return None
    try:
        v = float(bruto)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, 2)


def _positivo(bruto: Any) -> float | None:
    v = _valor(bruto)
    return v if v is not None and v > 0 else None


def _inteiro(bruto: Any) -> int | None:
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return None


def _iso_epoch(bruto: Any) -> str | None:
    """Horário da Shopee (segundos; ms também) → ISO UTC. Zero = não aconteceu."""
    n = _inteiro(bruto)
    if not n or n <= 0:
        return None
    if n > 10**11:
        n //= 1000
    return datetime.fromtimestamp(n, tz=UTC).isoformat()


def _iso_texto(bruto: Any) -> str | None:
    """Data do ML (fração de até 9 casas, fuso -03/-04) → ISO UTC."""
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    texto = _RE_FRACAO.sub(r"\1", bruto.strip().replace("Z", "+00:00"))
    try:
        quando = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return quando.astimezone(UTC).isoformat()


def _de_iso(bruto: Any) -> datetime | None:
    if not isinstance(bruto, str):
        return None
    try:
        quando = datetime.fromisoformat(bruto)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _lista(bruto: Any) -> list[dict]:
    return [x for x in bruto if isinstance(x, dict)] if isinstance(bruto, list) else []


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


def _descricao_segura(texto: Any) -> str | None:
    """A descrição do rastreio, cortada em 300 — ou None se tiver sinal de dado pessoal.

    Tudo ou nada, de propósito (ver `_RE_DADO_PESSOAL`): nada de meia frase
    mascarada que ainda leva o nome ou metade do documento para o banco.
    """
    t = _texto(texto)
    if t is None or _RE_DADO_PESSOAL.search(t):
        return None
    return t[:DESCRICAO_MAX]


def _https(url: Any) -> str | None:
    """A URL em https (a tela é https: imagem em http vira conteúdo misto)."""
    u = _texto(url)
    if u is None:
        return None
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    return u


def _foto_ml(url: Any) -> str | None:
    """Miniatura do ML em https e no tamanho maior (`-I.jpg` → `-O.jpg`)."""
    u = _https(url)
    if u is None:
        return None
    return _RE_FOTO_ML_PEQUENA.sub(r"-O.\1", u)


def _foto(plataforma: str, url: Any) -> str | None:
    """Foto de qualquer origem no jeito da tela: https, e a do ML no tamanho maior."""
    return _foto_ml(url) if plataforma == "ml" else _https(url)


def _erro_curto(exc: BaseException) -> str:
    """Erro para o log: classe, HTTP ou código da loja — nunca a URL (tem token)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, RuntimeError):
        m = _RE_CODIGO_SHOPEE.match(str(exc))
        if m:
            return m.group(1)
    return type(exc).__name__


def _tipo_falha(exc: BaseException) -> str:
    """A falha passa sozinha (`erro`) ou a loja disse que não existe (`nao_encontrado`)?

    404/403 = pedido/anúncio que não é desta conta ou não existe mais (o
    `/orders/{pack}` do ML, o anúncio apagado): tentar de novo em 30 min
    não muda nada. Rede, 429, 5xx e erro da API passam — tenta de novo, com
    espera crescente.
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 404):
        return RESULTADO_NAO_ENCONTRADO
    if isinstance(exc, RuntimeError) and "not_found" in _erro_curto(exc):
        return RESULTADO_NAO_ENCONTRADO
    return RESULTADO_ERRO


# ── Cartões (o formato dos anexos) ────────────────────────────────────────


def cartao_produto_vazio(item_id: Any) -> dict:
    """O cartão de produto sem nada além do id (a loja não respondeu, ainda)."""
    return {
        "tipo": "produto",
        "item_id": str(item_id),
        "titulo": None,
        "imagem": None,
        "preco": None,
        "preco_original": None,
        "moeda": MOEDA,
        "link": None,
    }


def cartao_pedido_vazio(pedido: Any) -> dict:
    """O cartão de pedido só com o número (a loja não respondeu, ainda)."""
    return {
        "tipo": "pedido",
        "pedido": str(pedido),
        "status": None,
        "status_texto": None,
        "criado_em": None,
        "total": None,
        "moeda": MOEDA,
        "itens": [],
    }


def cartao_pedido(retrato: dict) -> dict:
    """O cartão "Confirmação de pedido" da mensagem, tirado do retrato do pedido."""
    return {
        "tipo": "pedido",
        "pedido": str(retrato.get("pedido") or ""),
        "status": retrato.get("status"),
        "status_texto": retrato.get("status_texto"),
        "criado_em": retrato.get("criado_em"),
        "total": retrato.get("total"),
        "moeda": retrato.get("moeda") or MOEDA,
        "itens": [dict(i) for i in _lista(retrato.get("itens"))],
    }


def cartao_incompleto(anexos: Any) -> bool:
    """O anexo de produto/pedido ainda não tem o que a loja informa (título/status)?

    É o que decide se a mensagem, relida numa rodada futura, tenta de novo.
    """
    for a in _lista(anexos):
        if a.get("tipo") == "produto" and not a.get("titulo"):
            return True
        if a.get("tipo") == "pedido" and not a.get("status"):
            return True
    return False


# ── Cache do cartão de produto ────────────────────────────────────────────


def chave_produto(plataforma: str, integration_id: Any, item_id: str) -> str:
    return f"atd:prod:{plataforma}:{integration_id}:{item_id}"


async def _cache_ler(chave: str) -> dict | None:
    try:
        bruto = await redis.get(chave)
    except Exception as exc:  # noqa: BLE001 — sem Redis, vai à loja (com a cota)
        logger.info("atendimento_enriquecer_cache_indisponivel", erro=type(exc).__name__)
        return None
    if not bruto:
        return None
    try:
        cartao = json.loads(bruto)
    except (TypeError, ValueError):
        return None
    return cartao if isinstance(cartao, dict) and cartao.get("tipo") == "produto" else None


async def _cache_gravar(chave: str, cartao: dict) -> None:
    try:
        await redis.set(chave, json.dumps(cartao, ensure_ascii=False), ex=CACHE_PRODUTO_S)
    except Exception as exc:  # noqa: BLE001 — cache é economia, não correção
        logger.info("atendimento_enriquecer_cache_indisponivel", erro=type(exc).__name__)


# ── Produto: loja → cartão ────────────────────────────────────────────────


def _precos(infos: list[dict]) -> tuple[float | None, float | None]:
    """(preço, preço original) de uma lista de `price_info` da Shopee.

    Com variações de preços diferentes, vale o MENOR (o "a partir de" da
    vitrine). O original só aparece quando é maior que o preço (desconto).
    """
    atuais = [v for i in infos if (v := _positivo(i.get("current_price"))) is not None]
    originais = [v for i in infos if (v := _positivo(i.get("original_price"))) is not None]
    preco = min(atuais) if atuais else (min(originais) if originais else None)
    original = min(originais) if originais else None
    if preco is None or original is None or original <= preco:
        original = None
    return preco, original


async def _produtos_shopee(cliente: Any, ids: list[str]) -> dict[str, dict]:
    """Cartões da Shopee: `get_item_base_info` (foto, título) e, no anúncio
    com variação, `get_model_list` (o preço). Levanta se a 1ª chamada falhar."""
    itens = await cliente.get_item_base_info([int(i) for i in ids])
    shop_id = _inteiro(getattr(cliente, "shop_id", None))
    saida: dict[str, dict] = {}
    for it in _lista(itens):
        item_id = _texto(it.get("item_id"))
        if item_id is None or item_id not in ids:
            continue
        fotos = [u for u in (_dict(it.get("image")).get("image_url_list") or []) if _texto(u)]
        preco, original = _precos(_lista(it.get("price_info")))
        if preco is None and it.get("has_model"):
            try:
                modelos = await cliente.get_model_list(int(item_id))
            except Exception as exc:  # noqa: BLE001 — sem preço, o cartão ainda sai
                logger.info(
                    "atendimento_enriquecer_variacoes_falhou",
                    item_id=item_id,
                    erro=_erro_curto(exc),
                )
            else:
                infos = [
                    info
                    for m in _lista(_dict(modelos).get("model"))
                    for info in _lista(m.get("price_info"))
                ]
                preco, original = _precos(infos)
        saida[item_id] = {
            **cartao_produto_vazio(item_id),
            "titulo": _texto(it.get("item_name")),
            "imagem": _texto(fotos[0]) if fotos else None,
            "preco": preco,
            "preco_original": original,
            "link": f"https://shopee.com.br/product/{shop_id}/{item_id}" if shop_id else None,
        }
    return saida


def _cartao_ml(body: dict) -> dict | None:
    item_id = _texto(body.get("id"))
    if item_id is None:
        return None
    preco = _positivo(body.get("price"))
    original = _positivo(body.get("original_price"))
    if preco is None or original is None or original <= preco:
        original = None
    return {
        **cartao_produto_vazio(item_id),
        "titulo": _texto(body.get("title")),
        "imagem": _foto_ml(body.get("secure_thumbnail") or body.get("thumbnail")),
        "preco": preco,
        "preco_original": original,
        "link": _texto(body.get("permalink")),
    }


async def _produtos_ml(cliente: Any, ids: list[str]) -> dict[str, dict]:
    """Cartões do ML numa ida só ao `/items?ids=` (o cliente divide de 20 em 20)."""
    saida: dict[str, dict] = {}
    for body in _lista(await cliente.itens(ids)):
        cartao = _cartao_ml(body)
        if cartao is not None and cartao["item_id"] in ids:
            saida[cartao["item_id"]] = cartao
    return saida


def _titulo_base(titulos: list[str]) -> str | None:
    """O título do anúncio a partir das linhas por variação ("Mochila X - Preta")."""
    titulos = [t for t in titulos if t]
    if not titulos:
        return None
    if len(titulos) > 1:
        comum = os.path.commonprefix(titulos)
        if comum.rstrip().endswith("-") and len(comum.rstrip(" -")) >= 3:
            return comum.rstrip(" -")
    return min(titulos, key=len)


async def _do_catalogo(
    session: AsyncSession, integration: Integration | None, plataforma: str, item_id: str
) -> dict | None:
    """O cartão pelo NOSSO catálogo (`listings` da mesma loja, pelo `external_id`).

    É o que sobra quando a loja não responde ou não tem API de produto.
    `listings.price` é em centavos; com variações, vale o menor. A foto passa
    pelo mesmo tratamento da que vem da loja: o `thumbnail_url` do ML no
    catálogo é `http://` e a miniatura `-I` (90 px) — esticada no cartão e
    conteúdo misto na tela https.
    """
    if integration is None:
        return None
    linhas = (
        await session.execute(
            select(Listing.title, Listing.thumbnail_url, Listing.price)
            .where(Listing.integration_id == integration.id, Listing.external_id == item_id)
            .limit(50)
        )
    ).all()
    if not linhas:
        return None
    precos = [p for _, _, p in linhas if isinstance(p, int) and p > 0]
    return {
        **cartao_produto_vazio(item_id),
        "titulo": _titulo_base([t for t, _, _ in linhas if t]),
        "imagem": _foto(plataforma, next((u for _, u, _ in linhas if _texto(u)), None)),
        "preco": round(min(precos) / 100, 2) if precos else None,
    }


def _juntar(da_loja: dict | None, do_catalogo: dict | None) -> dict | None:
    """A loja manda; o catálogo só completa o que ela não trouxe."""
    if da_loja is None:
        return do_catalogo
    if do_catalogo is None:
        return da_loja
    return {
        **da_loja,
        **{
            k: do_catalogo[k]
            for k in ("titulo", "imagem", "preco")
            if da_loja.get(k) is None and do_catalogo.get(k) is not None
        },
    }


async def _cartoes(
    session: AsyncSession,
    integration: Integration | None,
    cliente: Any,
    plataforma: str,
    ids: list[str],
    *,
    cota: Cota | None,
    gastar: bool = True,
) -> tuple[dict[str, dict], set[str], str | None]:
    """Cartões de vários anúncios: cache → UMA ida à loja pelos que faltam → catálogo.

    Devolve (cartões, resolvidos, falha). Resolvido = veio do cache ou a loja
    foi consultada (deu certo ou não): não precisa de nova tentativa tão
    cedo. `falha` = o tipo da falha da ida à loja (`_tipo_falha`), None se
    ela respondeu (mesmo sem o anúncio) ou não foi preciso ir. Sem cota (ou
    `gastar=False`, quando o retrato do pedido já pagou a ida), não há teto.
    Só a resposta da loja vai para o cache.
    """
    ids = [i for i in dict.fromkeys(_texto(x) for x in ids) if i]
    integ_id = integration.id if integration is not None else "-"
    cartoes: dict[str, dict] = {}
    resolvidos: set[str] = set()
    falha: str | None = None
    faltam: list[str] = []
    for item_id in ids:
        em_cache = await _cache_ler(chave_produto(plataforma, integ_id, item_id))
        if em_cache is not None:
            cartoes[item_id] = em_cache
            resolvidos.add(item_id)
        else:
            faltam.append(item_id)

    da_loja: dict[str, dict] = {}
    pode = (
        faltam
        and cliente is not None
        and plataforma in PLATAFORMAS_COM_API
        and (cota is None or not gastar or cota.gastar())
    )
    if pode:
        resolvidos.update(faltam)
        try:
            if plataforma == "shopee":
                da_loja = await _produtos_shopee(cliente, faltam)
            else:
                da_loja = await _produtos_ml(cliente, faltam)
        except Exception as exc:  # noqa: BLE001 — falha da loja nunca derruba o sync
            logger.info(
                "atendimento_enriquecer_produto_falhou",
                plataforma=plataforma,
                itens=faltam[:5],
                erro=_erro_curto(exc),
            )
            da_loja = {}
            falha = _tipo_falha(exc)

    for item_id in faltam:
        loja = da_loja.get(item_id)
        cartao = loja
        if loja is None or any(loja.get(k) is None for k in ("titulo", "imagem", "preco")):
            cartao = _juntar(
                loja, await _do_catalogo(session, integration, plataforma, item_id)
            )
        if cartao is None:
            continue
        cartoes[item_id] = cartao
        if loja is not None:
            await _cache_gravar(chave_produto(plataforma, integ_id, item_id), cartao)
    return cartoes, resolvidos, falha


async def cartao_produto(
    session: AsyncSession,
    integration: Integration | None,
    cliente: Any,
    plataforma: str,
    item_id: Any,
    *,
    cota: Cota | None = None,
) -> dict | None:
    """O cartão de UM anúncio (foto, título, preço, link) — ou None se ninguém o conhece.

    Cache do Redis por 24 h (`atd:prod:{plataforma}:{integration_id}:{item_id}`);
    sem cache, a loja (se a `cota` deixar); o que faltar, o nosso catálogo.
    Nunca levanta por erro da loja.
    """
    item = _texto(item_id)
    if item is None:
        return None
    cartoes, _, _ = await _cartoes(session, integration, cliente, plataforma, [item], cota=cota)
    return cartoes.get(item)


# ── Retrato do pedido ─────────────────────────────────────────────────────


@dataclass
class _LinhaIndice:
    """Um pedido para o índice de pedidos do comprador (`indice.registrar_pedido`).

    Fica FORA do retrato: o id do comprador na plataforma não vai para o
    painel nem para `conversa.dados`.
    """

    pedido: str
    comprador_id: str | None
    criado_em: datetime | None
    total: float | None
    status: str | None
    itens_resumo: str | None


def _resumo_itens(itens: list[dict]) -> str | None:
    """Os itens em uma linha ("2x Mochila Executiva (Preta); 1x Fone"), até `RESUMO_ITENS_MAX`."""
    partes = []
    for it in itens:
        titulo = _texto(it.get("titulo"))
        if titulo is None:
            continue
        variacao = _texto(it.get("variacao"))
        partes.append(
            f"{it.get('quantidade') or 1}x {titulo}" + (f" ({variacao})" if variacao else "")
        )
    texto = "; ".join(partes)
    return texto[:RESUMO_ITENS_MAX] or None


def _logistica_vazia() -> dict:
    return {
        "transportadora": None,
        "rastreio": None,
        "status": None,
        "status_texto": None,
        "descricao": None,
        "atualizado_em": None,
    }


def _pagamento_shopee(bruto: Any) -> str | None:
    t = _texto(bruto)
    if t is None:
        return None
    return PAGAMENTO_SHOPEE.get(t.lower(), t)


def _enviado_em_shopee(detalhe: dict, eventos: dict) -> str | None:
    """A "Hora de envio" da Shopee: quando a transportadora pegou o pacote.

    `pickup_done_time` (do pedido ou do pacote), quando a Shopee o manda — é
    o horário exato da coleta; senão, o PRIMEIRO evento do rastreio já com o
    pacote coletado. Sem nenhum dos dois, None (não se chuta pelo status).
    """
    coletas = [
        n
        for fonte in (detalhe, *_lista(detalhe.get("package_list")))
        if (n := _inteiro(fonte.get("pickup_done_time"))) and n > 0
    ]
    if not coletas:
        coletas = [
            n
            for e in _lista(eventos.get("tracking_info"))
            if (_texto(e.get("logistics_status")) or "").upper() in _SHOPEE_DEPOIS_DA_COLETA
            and (n := _inteiro(e.get("update_time")))
            and n > 0
        ]
    return _iso_epoch(min(coletas)) if coletas else None


async def _retrato_shopee(cliente: Any, pedido: str) -> tuple[dict | None, list[_LinhaIndice]]:
    """(retrato, linha do índice) de um pedido da Shopee; (None, []) se a loja não o devolve."""
    detalhe = _dict(await cliente.get_order_detail_completo(pedido))
    if not detalhe:
        return None, []
    status = (_texto(detalhe.get("order_status")) or "").upper() or None
    pacote = next(iter(_lista(detalhe.get("package_list"))), {})
    status_log = (_texto(pacote.get("logistics_status")) or "").upper() or None

    # Rastreio: só depois da coleta combinada (antes disso a Shopee devolve
    # vazio — medido em 28/09 num READY_TO_SHIP/LOGISTICS_READY). Sem pacote
    # nenhum, só se o pedido já saiu.
    rastreio: str | None = None
    eventos: dict = {}
    if (
        status != "UNPAID"
        and status_log not in _SHOPEE_SEM_RASTREIO
        and (status_log is not None or status in _SHOPEE_JA_SAIU)
    ):
        try:
            rastreio = _texto(await cliente.get_tracking_number(pedido))
            eventos = _dict(await cliente.get_tracking_info(pedido))
        except Exception as exc:  # noqa: BLE001 — o painel sai sem o rastreio
            logger.info(
                "atendimento_enriquecer_rastreio_falhou", pedido=pedido, erro=_erro_curto(exc)
            )
    status_log = (_texto(eventos.get("logistics_status")) or "").upper() or status_log
    ultimo = max(
        _lista(eventos.get("tracking_info")),
        key=lambda e: _inteiro(e.get("update_time")) or 0,
        default={},
    )

    itens = [
        {
            "titulo": _texto(it.get("item_name")),
            "imagem": _texto(_dict(it.get("image_info")).get("image_url")),
            "variacao": _texto(it.get("model_name")),
            "sku": _texto(it.get("model_sku")) or _texto(it.get("item_sku")),
            "quantidade": _inteiro(it.get("model_quantity_purchased")) or 1,
            "preco": _positivo(it.get("model_discounted_price"))
            or _positivo(it.get("model_original_price")),
        }
        for it in _lista(detalhe.get("item_list"))
    ]
    total = _valor(detalhe.get("total_amount"))
    pago_em = _iso_epoch(detalhe.get("pay_time"))
    nota = _dict(detalhe.get("invoice_data"))
    numero = _texto(detalhe.get("order_sn")) or pedido
    criado_em = _iso_epoch(detalhe.get("create_time"))
    retrato = {
        "fonte": "shopee",
        "pedido": numero,
        "status": status,
        "status_texto": STATUS_PEDIDO_SHOPEE.get(status or "", status),
        "criado_em": criado_em,
        "pago_em": pago_em,
        "enviado_em": _enviado_em_shopee(detalhe, eventos),
        # "Tempo concluído": a Shopee não tem um `complete_time`; o
        # `update_time` do pedido CONCLUÍDO é a última mudança dele — a conclusão.
        "concluido_em": (
            _iso_epoch(detalhe.get("update_time")) if status == _SHOPEE_CONCLUIDO else None
        ),
        "total": total,
        # `total_amount` é o que o comprador pagou (itens + frete dele −
        # promoções da Shopee); só vale como "pago" depois do pagamento.
        "valor_pago": total if pago_em else None,
        # O frete que o COMPRADOR pagou não vem no `get_order_detail`: os
        # `estimated/actual_shipping_fee` são o custo da logística para a
        # loja (ver `marketplace_financials`). Num pedido com frete grátis, o
        # painel diria "Frete R$ 22,50" que ele não pagou — e quem atende
        # repetiria isso. O certo (`buyer_paid_shipping_fee`) está no escrow:
        # outra ida à loja por retrato, fica para quando fizer falta.
        "frete": None,
        "moeda": _texto(detalhe.get("currency")) or MOEDA,
        "pagamento_metodo": _pagamento_shopee(detalhe.get("payment_method")),
        "itens": itens,
        "logistica": {
            "transportadora": _texto(pacote.get("shipping_carrier"))
            or _texto(detalhe.get("shipping_carrier")),
            "rastreio": rastreio,
            "status": status_log,
            "status_texto": STATUS_LOGISTICA_SHOPEE.get(status_log or "", status_log),
            "descricao": _descricao_segura(ultimo.get("description")),
            "atualizado_em": _iso_epoch(ultimo.get("update_time")),
        },
        "nf": {"numero": _texto(nota.get("number")), "status": _texto(nota.get("status"))},
        "atualizado_em": _agora().isoformat(timespec="seconds"),
    }
    # `buyer_user_id` só vem quando é pedido nos campos opcionais; sem ele, o
    # índice usa o comprador da conversa (`retrato_pedido(comprador_id=...)`).
    linha = _LinhaIndice(
        pedido=numero,
        comprador_id=_texto(detalhe.get("buyer_user_id")),
        criado_em=_de_iso(criado_em),
        total=total,
        status=status,
        itens_resumo=_resumo_itens(itens),
    )
    return retrato, [linha]


def _variacao_ml(item: dict) -> str | None:
    partes = [
        f"{n}: {v}" if (n := _texto(a.get("name"))) else v
        for a in _lista(item.get("variation_attributes"))
        if (v := _texto(a.get("value_name")))
    ]
    return ", ".join(partes) or None


def _pagamento_ml(pagamento: dict) -> str | None:
    if (_texto(pagamento.get("payment_method_id")) or "").lower() == "pix":
        return "Pix"
    tipo = _texto(pagamento.get("payment_type"))
    return PAGAMENTO_ML.get(tipo or "", tipo)


async def _pedidos_ml(cliente: Any, numero: str, *, pack: bool) -> tuple[list[dict], str | None]:
    """Os pedidos do ML por trás do número (e o envio, quando o pack o diz).

    A pós-venda do ML sem `order_id` à vista — o formato REAL: os
    `message_resources` são packs + sellers e o `data.order_id` vem null —
    guarda o PACK na conversa, e `/orders/{pack}` é 404: um pack não é um
    pedido (o repositório já tropeçou nisso em `marketplace_financials` e
    `logistica_meli`). Então, sendo pack: `/packs/{id}` → `orders[].id` →
    cada pedido. 404 no `/packs` = pedido sem carrinho, que usa o próprio
    order id no lugar do pack: aí vai direto ao `/orders`.
    """
    envio_id: str | None = None
    if pack:
        try:
            corpo = _dict(await cliente.get_pack(numero))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            corpo = {}
        ids = [i for o in _lista(corpo.get("orders")) if (i := _texto(o.get("id")))]
        envio_id = _texto(_dict(corpo.get("shipment")).get("id"))
        if ids:
            if len(ids) > MAX_PEDIDOS_PACK:
                logger.info(
                    "atendimento_enriquecer_pack_grande", pack_id=numero, pedidos=len(ids)
                )
            ordens = [_dict(await cliente.pedido(i)) for i in ids[:MAX_PEDIDOS_PACK]]
            return [o for o in ordens if o.get("id") is not None], envio_id
    ordem = _dict(await cliente.pedido(numero))
    return ([ordem] if ordem.get("id") is not None else []), envio_id


def _soma(valores: list[float]) -> float | None:
    return round(sum(valores), 2) if valores else None


def _datas_ml(principal: dict, envio: dict, tem_envio: bool) -> tuple[str | None, str | None]:
    """(enviado_em, concluido_em) do ML: o `status_history` do envio (formato novo).

    "Hora de envio" = `date_shipped`; "Tempo concluído" = `date_delivered`.
    O `date_closed` do pedido NÃO é conclusão quando há envio: no ML ele é o
    fechamento da VENDA (o pagamento confirmado, minutos depois da compra) —
    usado como conclusão, o painel diria "concluído" num pedido nem enviado.
    Só vale no pedido SEM envio pelo ML (retirada/a combinar), em que a venda
    fechada é tudo o que o ML sabe.
    """
    historico = _dict(envio.get("status_history"))
    enviado = _iso_texto(historico.get("date_shipped"))
    concluido = _iso_texto(historico.get("date_delivered"))
    if concluido is None and not tem_envio:
        concluido = _iso_texto(principal.get("date_closed"))
    return enviado, concluido


def _linhas_ml(ordens: list[dict], itens_por_ordem: dict[str, list[dict]]) -> list[_LinhaIndice]:
    """Uma linha do índice por PEDIDO do carrinho (cada um com o seu total e status)."""
    linhas = []
    for o in ordens:
        numero = _texto(o.get("id"))
        if numero is None:
            continue
        linhas.append(
            _LinhaIndice(
                pedido=numero,
                comprador_id=_texto(_dict(o.get("buyer")).get("id")),
                criado_em=_de_iso(_iso_texto(o.get("date_created"))),
                total=_valor(o.get("total_amount")),
                status=(_texto(o.get("status")) or "").lower() or None,
                itens_resumo=_resumo_itens(itens_por_ordem.get(numero, [])),
            )
        )
    return linhas


async def _retrato_ml(
    session: AsyncSession,
    integration: Integration | None,
    cliente: Any,
    numero: str,
    *,
    pack: bool = False,
) -> tuple[dict | None, list[_LinhaIndice]]:
    """(retrato, linhas do índice) de um pedido do ML — ou do carrinho (pack) inteiro.

    Carrinho com vários pedidos: itens de todos, total e valor pago somados,
    e o número mostrado é o do pack (é o que agrupa a compra). Com um pedido
    só, o número é o do pedido. O índice ganha uma linha por pedido.
    """
    ordens, envio_id = await _pedidos_ml(cliente, numero, pack=pack)
    if not ordens:
        return None, []
    principal = ordens[0]
    estados = [s for o in ordens if (s := (_texto(o.get("status")) or "").lower())]
    # Carrinho com um pedido cancelado e os outros pagos: vale o que segue.
    status = next((s for s in estados if s != "cancelled"), None) or (
        estados[0] if estados else None
    )
    pagamentos = [p for o in ordens for p in _lista(o.get("payments"))]
    # `payments` traz TODA tentativa de pagamento (o cartão recusado antes do
    # Pix também está lá, com o mesmo `shipping_cost`): o que conta é o aprovado.
    aprovados = [p for p in pagamentos if p.get("status") == "approved"]
    pagamento = aprovados[0] if aprovados else (pagamentos[0] if pagamentos else {})

    # O envio (formato novo): status, substatus, rastreio. Falhou → sem logística.
    # O carrinho tem UM envio (todos os pedidos do pack o compartilham).
    envio: dict = {}
    envio_id = envio_id or next(
        (i for o in ordens if (i := _texto(_dict(o.get("shipping")).get("id")))), None
    )
    if envio_id:
        try:
            envio = _dict(await cliente.envio(envio_id))
        except Exception as exc:  # noqa: BLE001 — o painel sai sem o envio
            logger.info(
                "atendimento_enriquecer_envio_falhou", pedido=numero, erro=_erro_curto(exc)
            )

    # Foto de cada item: o pedido do ML não traz — sai do cartão do anúncio
    # (cache de 24 h; senão UMA ida ao /items por todos). A ida já está paga
    # pela unidade da cota que este retrato gastou.
    linhas = [
        (_texto(o.get("id")) or "", ln) for o in ordens for ln in _lista(o.get("order_items"))
    ]
    ids = [i for _, ln in linhas if (i := _texto(_dict(ln.get("item")).get("id")))]
    cartoes, _, _ = await _cartoes(
        session, integration, cliente, "ml", ids, cota=None, gastar=False
    )
    itens = []
    itens_por_ordem: dict[str, list[dict]] = {}
    for ordem_id, ln in linhas:
        item = _dict(ln.get("item"))
        cartao = cartoes.get(_texto(item.get("id")) or "") or {}
        linha_item = {
            "titulo": _texto(item.get("title")) or cartao.get("titulo"),
            "imagem": cartao.get("imagem"),
            "variacao": _variacao_ml(item),
            "sku": _texto(item.get("seller_sku")) or _texto(item.get("seller_custom_field")),
            "quantidade": _inteiro(ln.get("quantity")) or 1,
            "preco": _valor(ln.get("unit_price")),
        }
        itens.append(linha_item)
        itens_por_ordem.setdefault(ordem_id, []).append(linha_item)

    criados = sorted(c for o in ordens if (c := _iso_texto(o.get("date_created"))))
    status_envio = (_texto(envio.get("status")) or "").lower() or None
    substatus = (_texto(envio.get("substatus")) or "").lower()
    enviado_em, concluido_em = _datas_ml(principal, envio, tem_envio=envio_id is not None)
    retrato = {
        "fonte": "ml",
        "pedido": (_texto(principal.get("id")) or numero) if len(ordens) == 1 else numero,
        "status": status,
        "status_texto": STATUS_PEDIDO_ML.get(status or "", status),
        "criado_em": criados[0] if criados else None,
        "pago_em": _iso_texto(pagamento.get("date_approved")),
        "enviado_em": enviado_em,
        "concluido_em": concluido_em,
        "total": _soma([v for o in ordens if (v := _valor(o.get("total_amount"))) is not None]),
        "valor_pago": _soma(
            [v for o in ordens if (v := _valor(o.get("paid_amount"))) is not None]
        ),
        # Sem pagamento aprovado, não há frete pago a mostrar.
        "frete": _soma(
            [v for p in aprovados if (v := _valor(p.get("shipping_cost"))) is not None]
        ),
        "moeda": _texto(principal.get("currency_id")) or MOEDA,
        "pagamento_metodo": _pagamento_ml(pagamento) if pagamento else None,
        "itens": itens,
        "logistica": {
            **_logistica_vazia(),
            "transportadora": _texto(envio.get("tracking_method")),
            "rastreio": _texto(envio.get("tracking_number")),
            "status": status_envio,
            "status_texto": STATUS_ENVIO_ML.get(status_envio or "", status_envio),
            "descricao": SUBSTATUS_ENVIO_ML.get(substatus),
            "atualizado_em": _iso_texto(envio.get("last_updated")),
        },
        # O pedido do ML não traz a NF; o painel mostra a do Bling ("No DaVinci").
        "nf": {"numero": None, "status": None},
        "atualizado_em": _agora().isoformat(timespec="seconds"),
    }
    return retrato, _linhas_ml(ordens, itens_por_ordem)


async def _buscar_retrato(
    session: AsyncSession,
    integration: Integration | None,
    cliente: Any,
    plataforma: str,
    numero: str,
    *,
    pack: bool,
) -> tuple[dict | None, str | None, list[_LinhaIndice]]:
    """(retrato, tipo da falha, linhas do índice) — a ida à loja de verdade. Nunca levanta."""
    try:
        if plataforma == "shopee":
            retrato, linhas = await _retrato_shopee(cliente, numero)
        else:
            retrato, linhas = await _retrato_ml(session, integration, cliente, numero, pack=pack)
    except Exception as exc:  # noqa: BLE001 — falha da loja nunca derruba o sync
        logger.info(
            "atendimento_enriquecer_pedido_falhou",
            plataforma=plataforma,
            pedido=numero,
            erro=_erro_curto(exc),
        )
        return None, _tipo_falha(exc), []
    # A loja respondeu sem o pedido (lista vazia, corpo sem id): não existe
    # para esta conta — tão permanente quanto o 404.
    if retrato is None:
        return None, RESULTADO_NAO_ENCONTRADO, []
    return retrato, None, linhas


async def _indexar(
    session: AsyncSession,
    integration: Integration | None,
    plataforma: str,
    linhas: list[_LinhaIndice],
    comprador_id: Any = None,
) -> int:
    """Grava no índice de pedidos do comprador os pedidos do retrato que acabou de chegar.

    O comprador é o que o PEDIDO diz (`buyer_user_id` da Shopee, `buyer.id`
    do ML); sem isso, o da conversa (`comprador_id` — na Shopee, o `to_id`
    do chat é o mesmo id de usuário do comprador). Sem comprador, a linha não
    serve para nada e não é gravada.

    Import TARDIO de `indice`: o enriquecimento não pode depender dele para
    funcionar (o painel do pedido vem antes do cartão "Cliente"). Cada linha
    num SAVEPOINT: erro do índice desfaz só a linha, nunca a rodada nem o
    retrato. Nunca levanta; devolve quantas gravou. Log só com ids.
    """
    if integration is None or not linhas:
        return 0
    try:
        from app.services.atendimento import indice
    except Exception as exc:  # noqa: BLE001 — índice ausente/quebrado: o retrato segue
        logger.info("atendimento_enriquecer_indice_indisponivel", erro=type(exc).__name__)
        return 0
    registrar = getattr(indice, "registrar_pedido", None)
    if registrar is None:
        return 0
    reserva = _texto(comprador_id)
    gravadas = 0
    for linha in linhas:
        comprador = linha.comprador_id or reserva
        if comprador is None:
            continue
        try:
            # O pendente alheio vai ANTES do SAVEPOINT: o rollback dele desfaz
            # só esta linha do índice.
            await session.flush()
            async with session.begin_nested():
                await registrar(
                    session,
                    integration_id=integration.id,
                    plataforma=plataforma,
                    comprador_id=comprador,
                    pedido=linha.pedido,
                    criado_em=linha.criado_em,
                    total=linha.total,
                    status=linha.status,
                    itens_resumo=linha.itens_resumo,
                )
        except Exception as exc:  # noqa: BLE001 — o índice é acessório do retrato
            logger.info(
                "atendimento_enriquecer_indice_falhou",
                plataforma=plataforma,
                pedido=linha.pedido,
                erro=_erro_curto(exc),
            )
            continue
        gravadas += 1
    return gravadas


async def retrato_pedido(
    session: AsyncSession,
    integration: Integration | None,
    cliente: Any,
    plataforma: str,
    pedido: Any,
    *,
    cota: Cota | None = None,
    pack: bool = False,
    comprador_id: Any = None,
) -> dict | None:
    """O pedido como a loja o mostra (formato `pedido_mkt`) — ou None.

    None quando a plataforma não tem API de pedido aqui, a `cota` da rodada
    acabou, ou a loja não devolveu o pedido (erro, 404, pedido de outra
    conta) — e o porquê fica em `cota.falhas[pedido]`. `pack=True` (ML): o
    número é um pack (carrinho), não um pedido. Nunca levanta por erro da
    loja. Sem dado pessoal do comprador.

    Cada ida à loja que dá certo alimenta o índice de pedidos do comprador
    (`_indexar`); `comprador_id` é o comprador da conversa, para quando o
    pedido não diz de quem é.
    """
    numero = _texto(pedido)
    if numero is None or cliente is None or plataforma not in PLATAFORMAS_COM_API:
        return None
    if cota is not None:
        if numero in cota.retratos:
            memo = cota.retratos[numero]
            return dict(memo) if memo is not None else None
        if not cota.gastar():
            cota.falhas[numero] = _SEM_COTA
            return None
    retrato, falha, linhas = await _buscar_retrato(
        session, integration, cliente, plataforma, numero, pack=pack
    )
    if cota is not None:
        cota.retratos[numero] = retrato
        if falha is not None:
            cota.falhas[numero] = falha
        else:
            cota.falhas.pop(numero, None)
    if retrato is not None:
        await _indexar(session, integration, plataforma, linhas, comprador_id)
    return retrato


# ── A conversa ────────────────────────────────────────────────────────────


def pedido_da_conversa(conversa: AtendimentoConversa) -> str | None:
    """O pedido da loja por trás da conversa, se a plataforma tem API de pedido.

    ML pós-venda: o `order_id` que o pack mostrou, senão o próprio pack (o
    `pedido_marketplace` da conversa) — que o retrato resolve pelo `/packs`
    (`pedido_e_pack`). Pergunta do ML é pré-venda: não tem pedido.
    """
    if conversa.plataforma not in PLATAFORMAS_COM_API or conversa.canal == CANAL_PERGUNTA:
        return None
    if conversa.plataforma == "ml":
        return _texto(_dict(conversa.dados).get("order_id")) or _texto(
            conversa.pedido_marketplace
        )
    return _texto(conversa.pedido_marketplace)


def pedido_e_pack(conversa: AtendimentoConversa, pedido: str | None) -> bool:
    """O número do pedido desta conversa do ML é o PACK (sem `order_id` à vista)?"""
    if conversa.plataforma != "ml" or pedido is None:
        return False
    dados = _dict(conversa.dados)
    return not _texto(dados.get("order_id")) and _texto(dados.get("pack_id")) == pedido


def produto_da_conversa(conversa: AtendimentoConversa) -> str | None:
    """O anúncio perguntado (só a pergunta do ML ganha o cartão na conversa)."""
    if conversa.plataforma == "ml" and conversa.canal == CANAL_PERGUNTA:
        return _texto(conversa.anuncio_id)
    return None


def carimbo(
    item_id: str,
    agora: datetime,
    resultado: str = RESULTADO_OK,
    anterior: Any = None,
) -> dict:
    """O carimbo de UMA tentativa: quando foi, como foi e quando tentar de novo.

    `falhas` conta as falhas SEGUIDAS do mesmo pedido/anúncio (outro id
    recomeça do zero) e escolhe a espera em `ESPERAS_FALHA`; "não
    encontrado" espera 24 h direto; deu certo, 30 min (`RENOVAR_DEPOIS_DE`).
    Público: a semente local grava o mesmo formato.
    """
    marca = _dict(anterior)
    falhas = 0
    if resultado != RESULTADO_OK:
        seguidas = _inteiro(marca.get("falhas")) or 0
        falhas = (seguidas if _texto(marca.get("id")) == item_id else 0) + 1
    if resultado == RESULTADO_OK:
        espera = RENOVAR_DEPOIS_DE
    elif resultado == RESULTADO_NAO_ENCONTRADO:
        espera = ESPERA_NAO_ENCONTRADO
    else:
        espera = ESPERAS_FALHA[min(falhas, len(ESPERAS_FALHA)) - 1]
    return {
        "id": item_id,
        "em": agora.isoformat(timespec="seconds"),
        "resultado": resultado,
        "falhas": falhas,
        "proxima": (agora + espera).isoformat(timespec="seconds"),
    }


def _precisa_renovar(carimbo_atual: Any, atual: Any, esperado: str, agora: datetime) -> bool:
    """Tentar de novo? Sim se é outro pedido/anúncio ou se chegou a hora do carimbo."""
    marca = _dict(carimbo_atual)
    if marca:
        if _texto(marca.get("id")) != esperado:
            return True
        proxima = _de_iso(marca.get("proxima"))
        if proxima is not None:
            return agora >= proxima
        # Carimbo sem `proxima` (de antes da espera crescente): 30 min.
        quando = _de_iso(marca.get("em"))
        return quando is None or agora - quando >= RENOVAR_DEPOIS_DE
    # Sem carimbo (conversa semeada à mão, ou de antes do carimbo): vale o
    # próprio retrato, se for deste pedido.
    atual = _dict(atual)
    if not atual or _texto(atual.get("pedido") or atual.get("item_id")) != esperado:
        return True
    quando = _de_iso(atual.get("atualizado_em"))
    return quando is None or agora - quando >= RENOVAR_DEPOIS_DE


def _retrato_de_outro(dados: dict, esperado: str) -> bool:
    """O retrato guardado é de OUTRO pedido (a conversa da Shopee trocou de pedido)?

    A regra que mantém isto simples: depois de toda tentativa pelo pedido X,
    o retrato guardado é de X ou não existe (quando a busca de X falha e o
    retrato era de outro, ele sai — ver `_enriquecer`). Então o carimbo diz
    de quem é o retrato; sem carimbo (semente, versão anterior), o número
    dentro do próprio retrato.
    """
    atual = _dict(dados.get(CHAVE_PEDIDO))
    if not atual:
        return False
    marca = _dict(_dict(dados.get(CHAVE_CARIMBOS)).get(CHAVE_PEDIDO))
    if marca:
        return _texto(marca.get("id")) != esperado
    return _texto(atual.get("pedido")) != esperado


async def _atualizar_cartoes_de_pedido(
    session: AsyncSession, conversa: AtendimentoConversa, retrato: dict
) -> int:
    """O cartão das mensagens de pedido desta conversa acompanha o retrato novo.

    A mensagem que chegou quando a cota da rodada já tinha acabado ficou só
    com o número; e o selo de status do cartão (como no Duoke) é o de AGORA,
    não o do dia em que o comprador mandou o pedido. Sem ida à loja: sai do
    retrato que acabou de chegar. Devolve quantas mensagens mudaram.
    """
    numero = _texto(retrato.get("pedido"))
    if numero is None:
        return 0
    novo = cartao_pedido(retrato)
    mensagens = (
        (
            await session.execute(
                select(AtendimentoMensagem).where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.tipo == "pedido",
                )
            )
        )
        .scalars()
        .all()
    )
    mudaram = 0
    for m in mensagens:
        anexos = _lista(m.anexos)
        saida = [
            novo
            if a.get("tipo") == "pedido" and _texto(a.get("pedido") or a.get("id")) == numero
            else a
            for a in anexos
        ]
        if saida != anexos:
            m.anexos = saida  # lista NOVA: o JSONB só é regravado se a coluna mudar
            mudaram += 1
    return mudaram


async def _enriquecer(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration | None,
    cliente: Any,
    *,
    forcar: bool,
    cota: Cota | None,
) -> bool:
    agora = _agora()
    dados = _dict(conversa.dados)
    carimbos = _dict(dados.get(CHAVE_CARIMBOS))
    # Fora da rodada (o botão da tela) não há teto, mas a memória é a mesma:
    # é por ela que a falha da loja chega ao carimbo.
    memoria = cota if cota is not None else Cota.sem_teto()
    novos: dict[str, Any] = {}
    tentativas: dict[str, dict] = {}

    pedido = pedido_da_conversa(conversa)
    # O retrato que a rodada JÁ buscou (para o cartão da mensagem) entra no
    # painel mesmo dentro dos 30 min: não custa ida à loja.
    na_memoria = pedido is not None and bool(memoria.retratos.get(pedido))
    de_outro = pedido is not None and _retrato_de_outro(dados, pedido)
    if pedido and (
        forcar
        or na_memoria
        or _precisa_renovar(carimbos.get(CHAVE_PEDIDO), dados.get(CHAVE_PEDIDO), pedido, agora)
    ):
        # Sem cota não se tenta (nem se carimba): a próxima rodada tenta.
        if pedido in memoria.retratos or memoria.tem():
            retrato = await retrato_pedido(
                session,
                integration,
                cliente,
                conversa.plataforma,
                pedido,
                cota=memoria,
                pack=pedido_e_pack(conversa, pedido),
                comprador_id=conversa.comprador_id,
            )
            resultado = (
                RESULTADO_OK
                if retrato is not None
                else memoria.falhas.get(pedido, RESULTADO_ERRO)
            )
            if resultado != _SEM_COTA:
                tentativas[CHAVE_PEDIDO] = carimbo(
                    pedido, agora, resultado, carimbos.get(CHAVE_PEDIDO)
                )
            if retrato is not None:
                novos[CHAVE_PEDIDO] = retrato

    item = produto_da_conversa(conversa)
    if item and (
        forcar
        or _precisa_renovar(carimbos.get(CHAVE_PRODUTO), dados.get(CHAVE_PRODUTO), item, agora)
    ):
        cartoes, resolvidos, falha = await _cartoes(
            session, integration, cliente, conversa.plataforma, [item], cota=memoria
        )
        if item in resolvidos:
            # Resolvido sem cartão: a loja respondeu sem o anúncio (apagado)
            # e nem o catálogo o conhece — ou a ida falhou.
            resultado = (
                RESULTADO_OK
                if item in cartoes
                else (falha or RESULTADO_NAO_ENCONTRADO)
            )
            tentativas[CHAVE_PRODUTO] = carimbo(
                item, agora, resultado, carimbos.get(CHAVE_PRODUTO)
            )
        if item in cartoes:
            novos[CHAVE_PRODUTO] = cartoes[item]

    # O retrato de OUTRO pedido não fica no painel desta conversa: o status,
    # os itens e o rastreio do pedido A numa conversa sobre o B é pior que
    # painel vazio (que se completa na próxima tentativa).
    tirar_retrato = de_outro and CHAVE_PEDIDO not in novos
    if not novos and not tentativas and not tirar_retrato:
        return False
    # Trava e relê a conversa antes de mesclar: o botão "atualizar" da tela e
    # a rodada do sync podem mexer em `dados` ao mesmo tempo, e mesclar sobre
    # um retrato velho apagaria o que o outro gravou.
    await gravar.travar_linha(session, conversa)
    base = _dict(conversa.dados)
    # Dicionário NOVO: mutar o JSONB no lugar não marca a coluna como suja.
    saida = {
        **base,
        **novos,
        CHAVE_CARIMBOS: {**_dict(base.get(CHAVE_CARIMBOS)), **tentativas},
    }
    # Conferido de novo sobre o `dados` RELIDO: quem gravou no meio pode ter
    # trazido o retrato certo.
    if pedido and CHAVE_PEDIDO not in novos and _retrato_de_outro(base, pedido):
        saida.pop(CHAVE_PEDIDO, None)
    conversa.dados = saida
    if CHAVE_PEDIDO in novos:
        await _atualizar_cartoes_de_pedido(session, conversa, novos[CHAVE_PEDIDO])
    await session.flush()
    return bool(novos)


async def enriquecer_conversa(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration | None,
    cliente: Any,
    *,
    forcar: bool = False,
    cota: Cota | None = None,
) -> bool:
    """Grava `dados["pedido_mkt"]` (e `dados["produto"]` na pergunta do ML).

    Renova quando chega a hora do carimbo da última TENTATIVA (30 min depois
    de dar certo; mais, e crescendo, depois de falhar) ou com `forcar` (o
    botão "atualizar" da tela — que tem a sua própria trava de 60 s). Devolve
    se gravou algo novo. NUNCA levanta: o enriquecimento é enfeite da caixa,
    e a rodada do sync (ou o clique na tela) segue sem ele. Não commita.
    """
    conversa_id = str(conversa.id)
    plataforma = conversa.plataforma
    try:
        return await _enriquecer(
            session, conversa, integration, cliente, forcar=forcar, cota=cota
        )
    except Exception as exc:  # noqa: BLE001 — ver docstring
        logger.warning(
            "atendimento_enriquecer_falhou",
            conversa_id=conversa_id,
            plataforma=plataforma,
            erro=_erro_curto(exc),
        )
        return False


async def enriquecer_canal(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration | None,
    cliente: Any,
    cota: Cota,
) -> int:
    """No fim da rodada, o que sobrou da cota vai para quem precisa do painel.

    A rodada só lê as conversas que mudaram: sem isto, a conversa que entrou
    quando a cota já tinha acabado (a primeira leitura de uma loja traz 40)
    só ganharia o painel quando o comprador escrevesse de novo.

    Quem entra: a que chegou a hora do carimbo (`_precisa_renovar`, em SQL)
    E (espera resposta OU está sem retrato e andou nos últimos 7 dias). Em
    que ordem: primeiro as que ESPERAM RESPOSTA (sem retrato antes; o pedido
    pode ter saído para entrega enquanto o comprador espera), depois as sem
    retrato, as mais recentes antes. A que falha espera cada vez mais (ver
    `carimbo`) — não volta toda rodada na frente das outras. Commita por
    conversa. Devolve quantas ganharam algo novo. Nunca levanta.
    """
    if canal.plataforma not in PLATAFORMAS_COM_API or not cota.tem():
        return 0
    canal_id = str(canal.id)
    feitos = 0
    try:
        conv = AtendimentoConversa
        if canal.canal == CANAL_PERGUNTA:
            chave, tem_elo = CHAVE_PRODUTO, conv.anuncio_id.is_not(None)
        else:
            chave, tem_elo = CHAVE_PEDIDO, conv.pedido_marketplace.is_not(None)
        agora = _agora()
        marca = conv.dados[CHAVE_CARIMBOS][chave]
        em, proxima = marca["em"].astext, marca["proxima"].astext
        # Mesmo formato de texto dos dois lados (ISO UTC em segundos, `carimbo`):
        # a comparação de texto é a de datas.
        corte = (agora - RENOVAR_DEPOIS_DE).isoformat(timespec="seconds")
        chegou_a_hora = or_(
            em.is_(None),
            and_(proxima.is_(None), em < corte),
            proxima <= agora.isoformat(timespec="seconds"),
        )
        sem_retrato = conv.dados[chave].is_(None)
        aguardando = conv.aguardando_resposta.is_(True)
        recente = conv.ultima_mensagem_em >= agora - JANELA_SEM_RETRATO
        conversas = (
            (
                await session.execute(
                    select(conv)
                    .where(
                        conv.integration_id == canal.integration_id,
                        conv.canal == canal.canal,
                        tem_elo,
                        chegou_a_hora,
                        or_(aguardando, and_(sem_retrato, recente)),
                    )
                    .order_by(
                        aguardando.desc(),
                        sem_retrato.desc(),
                        conv.ultima_mensagem_em.desc().nulls_last(),
                    )
                    .limit(max(1, cota.restam) * 2)
                )
            )
            .scalars()
            .all()
        )
        for conversa in conversas:
            if not cota.tem():
                break
            if await enriquecer_conversa(session, conversa, integration, cliente, cota=cota):
                feitos += 1
            await gravar.fim_do_item(session)
    except Exception as exc:  # noqa: BLE001 — enriquecer é enfeite; a rodada já leu
        logger.warning(
            "atendimento_enriquecer_canal_falhou",
            canal_id=canal_id,
            erro=_erro_curto(exc),
        )
    return feitos
