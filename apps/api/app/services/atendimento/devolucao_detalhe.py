"""A devolução do TikTok inteira na aba Pedido, e a resposta da loja (18/11/2026).

O dono comparou o mesmo pedido no Duoke e no DaVinci (Poofy, pedido
586887215793276360, reembolso nº 4042968144158361032) e quer o nosso "tão
completo quanto o Duoke": o bloco do produto em devolução (nº, montante,
logística da volta, motivo, a linha do produto com foto, variação, SKU, preço
cheio riscado e quantidade) e o "Histórico de reembolso" com data e hora, o
texto, as fotos e o vídeo do comprador. No mesmo dia: "se der p eles responder
a devolução por ali tbm" — e escolheu liberar TUDO, inclusive o pacote que
voltou (aprovar ou recusar o pedido; confirmar ou recusar o pacote).

DE ONDE VEM (medido ao vivo em 18/11/2026, só leitura, no caso do dono):
  • `returns/search` por `order_ids` (o caso): `refund_amount` (refund_total,
    refund_subtotal, refund_shipping_fee, currency); `discount_amount`
    (product_platform_discount + product_seller_discount: o preço cheio do
    Duoke é o subtotal + os descontos, 122,96 + 30,74 + 259,71 = 413,41);
    `return_line_items` (product_name, sku_name, seller_sku,
    product_image.url — UMA linha por unidade); `return_tracking_number` e
    `return_provider_name` (a volta); `return_reason_text` (o rótulo do motivo,
    em pt-BR com o `locale`); `is_quick_refund`; `seller_next_action_response`
    (action + deadline: o que a loja tem de fazer e até quando);
    `shipping_fee_amount.seller_paid_return_shipping_fee` (o frete da volta que
    a loja paga) e `arbitration_status`;
  • `returns/{id}/records` (`locale=pt-BR`): os eventos, com `create_time`,
    `role`, `event`, `description` (o título, já em português), `reason_text`,
    `note` (o texto do comprador), `images[].url` e `videos[]` (`url`,
    `cover`, `duration_millis`).
  O link do VÍDEO vence em cerca de 1 h (`x-expires`); as fotos não trazem
  prazo. Por isso a tela lê na hora e guarda só por `CACHE_S` (Redis).

NADA DO COMPRADOR FICA GUARDADO NO BANCO: o texto e as fotos dele passam pela
tela (e pelo cache curto), como no Duoke, mas `atendimento_reclamacoes` segue
sem eles — a regra de 02/10/2026 continua valendo para a gravação, a IA não os
recebe (`contexto._reclamacoes` só lê `para_tela`) e o log só leva ids.

O STATUS NÃO ESTAVA PARADO: no caso do dono o TikTok fez o REEMBOLSO RÁPIDO
(`is_quick_refund`) — pagou o comprador no minuto em que ele postou o pacote
(07/11 13:55, evento REFUND_SUCCESS) —, e o caso segue em BUYER_SHIPPED_ITEM
esperando a loja confirmar ou recusar o pacote até 19/11 09:55. O "faltam
19 h" era esse prazo. O bloco agora diz as duas coisas.

AS RESPOSTAS (documentação oficial 202309, Approve Return e Reject Return,
lidas em 18/11/2026): `POST returns/{id}/approve` com `decision` =
APPROVE_RETURN (o pedido de devolução), APPROVE_REFUND (o pedido de só
reembolso) ou APPROVE_RECEIVED_PACKAGE (o pacote que o comprador já mandou);
`POST returns/{id}/reject` com REJECT_RETURN / REJECT_REFUND /
REJECT_RECEIVED_PACKAGE, um `reject_reason` da lista que o TikTok dá para o
caso naquele estado (`reject_reasons`), o comentário e as fotos. Regras:
  • a ação só aparece quando o TikTok espera exatamente ela
    (`seller_next_action_response`, prazo no futuro, sem arbitragem);
  • antes de agir, o caso é RELIDO na hora: se mudou, a ação não sai;
  • reembolso rápido: o TikTok não deixa recusar o pacote pela API (erro
    25011025) — o botão aparece travado, com o caminho (apelação na Central);
  • recusar o pacote exige foto (como no chamado da Devoluções: o que vale é a
    foto do dano); recusar sempre leva motivo e comentário (até 500 bytes, o
    limite do TikTok);
  • a mesma chave de idempotência para o mesmo caso no mesmo estado: o clique
    repetido não vira duas decisões;
  • quem fez fica numa NOTA INTERNA da conversa (a equipe vê, o comprador não,
    a IA não lê) e no log (só ids);
  • tudo atrás de `ATENDIMENTO_DEVOLUCAO_ACOES_ATIVA` (nasce desligada, como o
    botão da troca: a primeira resposta é acompanhada pelo dono).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoReclamacao, Integration
from app.redis_client import redis
from app.services import logistica_rules
from app.services.atendimento import reclamacoes

logger = structlog.get_logger()

FabricaCliente = Callable[[Integration], Awaitable[Any]]

# Onde o histórico completo existe hoje (Shopee e ML: levantamento à parte).
PLATAFORMAS = frozenset({"tiktok"})

# A leitura na hora vale isto no cache: o link do vídeo do TikTok vence em ~1 h.
CACHE_S = 300
_CHAVE_CACHE = "atendimento:devolucao_detalhe:v1:{}"

# O comentário da recusa: o TikTok recusa acima disto (98001004 "seller words
# over limit", medido em 18/09 — `chamados_tiktok_reembolso.COMENTARIO_MAX_BYTES`).
COMENTARIO_MAX_BYTES = 500
COMENTARIO_MIN = 5
# Fotos da recusa (a TikTok aceita até 6 imagens por decisão).
MAX_FOTOS = 6
FOTO_MAX_BYTES = 5 * 1024 * 1024
FOTO_TIPOS = frozenset({"image/jpeg", "image/png", "image/webp"})

# A chave de idempotência: a mesma decisão para o mesmo caso no mesmo estado.
_NS_IDEMPOTENCIA = UUID("6f0f5b8e-2f8c-4f53-9a51-8d6a2a1c7e41")

# ── As respostas da loja ──────────────────────────────────────────────────
# O que o TikTok espera (`seller_next_action_response.action`) → (aprovar, recusar).
ACOES_POR_PENDENCIA: dict[str, tuple[str, str]] = {
    "SELLER_RESPOND_RETURN": ("aprovar_devolucao", "recusar_devolucao"),
    "SELLER_RESPOND_REFUND": ("aprovar_reembolso", "recusar_reembolso"),
    "SELLER_RESPOND_RECEIVE_PACKAGE": ("confirmar_pacote", "recusar_pacote"),
}
DECISAO: dict[str, str] = {
    "aprovar_devolucao": "APPROVE_RETURN",
    "aprovar_reembolso": "APPROVE_REFUND",
    "confirmar_pacote": "APPROVE_RECEIVED_PACKAGE",
    "recusar_devolucao": "REJECT_RETURN",
    "recusar_reembolso": "REJECT_REFUND",
    "recusar_pacote": "REJECT_RECEIVED_PACKAGE",
}
ROTULO: dict[str, str] = {
    "aprovar_devolucao": "Aprovar a devolução",
    "recusar_devolucao": "Recusar a devolução",
    "aprovar_reembolso": "Aprovar o reembolso",
    "recusar_reembolso": "Recusar o reembolso",
    "confirmar_pacote": "Confirmar o pacote recebido",
    "recusar_pacote": "Recusar o pacote recebido",
}
# O que acontece no TikTok — a confirmação mostra antes do clique final.
_EFEITO: dict[str, str] = {
    "aprovar_devolucao": (
        "O TikTok libera o comprador para devolver o produto. O reembolso de {valor} "
        "sai quando o pacote voltar (ou antes, se o TikTok fizer o reembolso rápido)."
    ),
    "aprovar_reembolso": (
        "O TikTok devolve {valor} ao comprador e o produto fica com ele. Não dá para desfazer."
    ),
    "confirmar_pacote": (
        "A loja confirma que o pacote voltou certo: o TikTok conclui o reembolso de {valor} "
        "e o caso fecha. Depois disso não dá mais para recusar o pacote — confira o produto "
        "antes."
    ),
    "recusar_devolucao": (
        "O TikTok avisa o comprador que a loja recusou. Ele pode contestar e o TikTok decide."
    ),
    "recusar_reembolso": (
        "O TikTok avisa o comprador que a loja recusou. Ele pode contestar e o TikTok decide."
    ),
    "recusar_pacote": (
        "A loja contesta o pacote que voltou, com as fotos. O TikTok analisa e decide; o "
        "comprador pode recorrer."
    ),
}
_EFEITO_RAPIDO = (
    "O comprador já recebeu {valor} (reembolso rápido do TikTok). Confirmar fecha o caso; "
    "depois não dá mais para contestar o pacote — confira o produto antes."
)
_BLOQUEIO_RAPIDO = (
    "O TikTok já fez o reembolso rápido: pela API ele não deixa recusar o pacote. Para "
    "contestar, abra o caso no TikTok e faça a apelação na Central do Vendedor."
)

# Erros que o TikTok devolve nas duas rotas (documentação 202309) → o que a pessoa lê.
_ERROS_TIKTOK: dict[str, str] = {
    "25011025": "O TikTok já fez o reembolso rápido e não deixa recusar o pacote pela API.",
    "25011035": "O TikTok ainda não deixa recusar o pacote (ele ainda está a caminho).",
    "25011010": "O caso já está com a equipe do TikTok (arbitragem): a loja não decide mais.",
    "25011012": "O caso não está mais esperando a resposta ao pedido.",
    "25011013": "O caso não está mais esperando a confirmação do pacote.",
    "25020004": "A decisão não combina com o tipo do caso no TikTok.",
    "25020009": "O comprador já cancelou o pedido de devolução.",
    "25020010": "O TikTok já aprovou o caso: não dá mais para recusar.",
    "25020011": "O pacote já consta como recebido.",
    "25020012": "O caso não está num estado em que dá para recusar o pacote.",
    "25001014": "O motivo da recusa não vale para este caso.",
    "25001016": "O motivo da recusa não vale para este caso.",
    "25001020": "Esse motivo de recusa saiu do ar no TikTok. Escolha outro.",
    "25001057": "O motivo da recusa não vale para este caso.",
    "25011026": "Pedido Fulfilled by TikTok: a loja não decide o pacote.",
    "25011023": "Pedido com garantia de autenticidade: a loja não decide pela API.",
    "25020005": "O TikTok diz que a loja não tem permissão para este pedido.",
    "98001004": "O comentário passou do tamanho que o TikTok aceita.",
}

# Quando o `description` do evento não vem (não aconteceu em 18/11/2026; é a rede).
_TITULO_EVENTO: dict[str, str] = {
    "ORDER_RETURN": "Pedido de devolução enviado pelo comprador",
    "ORDER_REFUND": "Pedido de reembolso enviado pelo comprador",
    "ORDER_REPLACEMENT": "Pedido de troca enviado pelo comprador",
    "SELLER_AGGREE_RETURN": "Devolução aprovada",
    "BUYER_SHIPPED": "O comprador enviou o pacote de devolução",
    "REFUND_SUCCESS": "Reembolso concluído",
}
_QUEM: dict[str, str] = {
    "BUYER": "Comprador",
    "SELLER": "Loja",
    "SYSTEM": "TikTok",
    "PLATFORM": "TikTok",
    "OPERATOR": "TikTok (operador)",
}
_RE_HTTPS = re.compile(r"^https://[^\s\"'<>]+$")


class AcaoRecusada(Exception):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """A resposta não saiu: `code` para a tela, `detail` em português."""

    def __init__(self, code: str, detail: str, *, http: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.http = http


@dataclass(frozen=True)
class FotoDaRecusa:
    nome: str
    dados: bytes
    tipo: str


# ── Puro: o que a tela mostra ─────────────────────────────────────────────


def _texto(bruto: Any) -> str:
    return "" if bruto is None else str(bruto).strip()


def _dinheiro(bruto: Any) -> float | None:
    """ "131.57" → 131.57; vazio, ilegível ou infinito → None."""
    if bruto is None or isinstance(bruto, bool):
        return None
    try:
        valor = float(str(bruto).strip())
    except ValueError:
        return None
    return valor if math.isfinite(valor) else None


def _quando(epoch: Any) -> str | None:
    """Epoch (s) do TikTok → ISO UTC; 0, vazio ou ilegível → None."""
    try:
        segundos = int(epoch)
    except (TypeError, ValueError):
        return None
    if segundos <= 0:
        return None
    return datetime.fromtimestamp(segundos, UTC).isoformat(timespec="seconds")


def _https(url: Any) -> str | None:
    texto = _texto(url)
    return texto if _RE_HTTPS.match(texto) else None


def _legivel(codigo: str) -> str:
    texto = codigo.replace("_", " ").strip().lower()
    return texto[:1].upper() + texto[1:]


def reembolso_do_caso(caso: dict) -> dict | None:
    """O montante do reembolso (`refund_amount`); sem o total, None."""
    bruto = caso.get("refund_amount") if isinstance(caso.get("refund_amount"), dict) else {}
    total = _dinheiro(bruto.get("refund_total"))
    if total is None:
        return None
    return {
        "total": total,
        "subtotal": _dinheiro(bruto.get("refund_subtotal")),
        "frete": _dinheiro(bruto.get("refund_shipping_fee")),
        "moeda": _texto(bruto.get("currency")) or None,
    }


def _desconto_do_caso(caso: dict) -> float:
    """Os descontos do produto (plataforma + loja) do caso inteiro."""
    soma = 0.0
    for d in caso.get("discount_amount") or []:
        if not isinstance(d, dict):
            continue
        for campo in ("product_platform_discount", "product_seller_discount"):
            valor = _dinheiro(d.get(campo))
            if valor and valor > 0:
                soma += valor
    return round(soma, 2)


def itens_do_caso(caso: dict) -> list[dict]:
    """Os produtos devolvidos, agrupados por SKU (o TikTok manda uma linha por unidade).

    `preco` = o que o comprador pagou pela unidade (o subtotal reembolsado dela);
    `preco_original` = o preço cheio, riscado na tela — só com UMA unidade no
    caso, porque o desconto vem do caso inteiro e não dá para repartir."""
    linhas = [li for li in caso.get("return_line_items") or [] if isinstance(li, dict)]
    grupos: dict[str, dict] = {}
    for li in linhas:
        chave = (
            _texto(li.get("sku_id"))
            or _texto(li.get("seller_sku"))
            or _texto(li.get("order_line_item_id"))
            or str(len(grupos))
        )
        grupo = grupos.get(chave)
        if grupo is None:
            reembolso = li.get("refund_amount") if isinstance(li.get("refund_amount"), dict) else {}
            imagem = li.get("product_image") if isinstance(li.get("product_image"), dict) else {}
            grupo = grupos[chave] = {
                "nome": _texto(li.get("product_name")) or None,
                "variacao": _texto(li.get("sku_name")) or None,
                "sku": _texto(li.get("seller_sku")) or None,
                "foto": _https(imagem.get("url")),
                "preco": _dinheiro(reembolso.get("refund_subtotal")),
                "preco_original": None,
                "moeda": _texto(reembolso.get("currency")) or None,
                "quantidade": 0,
            }
        grupo["quantidade"] += 1
    itens = list(grupos.values())
    if len(linhas) == 1 and itens and itens[0]["preco"] is not None:
        desconto = _desconto_do_caso(caso)
        if desconto > 0:
            itens[0]["preco_original"] = round(itens[0]["preco"] + desconto, 2)
    return itens


def volta_do_caso(caso: dict) -> dict | None:
    """O pacote que volta: rastreio, transportadora e o frete que a loja paga."""
    rastreio = _texto(caso.get("return_tracking_number")) or None
    transportadora = _texto(caso.get("return_provider_name")) or None
    frete_loja = None
    moeda = None
    for f in caso.get("shipping_fee_amount") or []:
        if isinstance(f, dict):
            valor = _dinheiro(f.get("seller_paid_return_shipping_fee"))
            if valor and valor > 0:
                frete_loja = (frete_loja or 0.0) + valor
                moeda = moeda or (_texto(f.get("currency")) or None)
    if not (rastreio or transportadora or frete_loja):
        return None
    return {
        "rastreio": rastreio,
        "transportadora": transportadora,
        "frete_loja": round(frete_loja, 2) if frete_loja else None,
        "moeda": moeda,
    }


def _midia(itens: Any, *, video: bool) -> list[dict]:
    saida: list[dict] = []
    vistos: set[str] = set()
    for item in itens or []:
        bruto = item if isinstance(item, dict) else {"url": item}
        url = _https(bruto.get("url") or bruto.get("image_url"))
        if not url or url in vistos:
            continue
        vistos.add(url)
        if video:
            duracao = _dinheiro(bruto.get("duration_millis"))
            saida.append(
                {
                    "url": url,
                    "capa": _https(bruto.get("cover")),
                    "duracao_s": round(duracao / 1000) if duracao else None,
                }
            )
        else:
            saida.append({"url": url})
    return saida


def historico(registros: list[dict] | None) -> list[dict]:
    """A linha do tempo do caso, da mais antiga para a mais nova (como o Duoke).

    Cada evento: quando, quem (papel), o título do TikTok (`description`, em
    português), o motivo escolhido, o texto que a pessoa escreveu (`note`, ou o
    `comment` da loja) e as fotos e vídeos. Só https vira mídia."""
    eventos: list[tuple[int, int, dict]] = []
    for ordem, r in enumerate(registros or []):
        if not isinstance(r, dict):
            continue
        evento = _texto(r.get("event")).upper()
        papel = _texto(r.get("role")).upper()
        titulo = (
            _texto(r.get("description"))
            or _TITULO_EVENTO.get(evento)
            or (_legivel(evento) if evento else "Atualização do caso")
        )
        texto = _texto(r.get("note")) or _texto(r.get("comment"))
        motivo = _texto(r.get("reason_text"))
        try:
            momento = int(r.get("create_time") or 0)
        except (TypeError, ValueError):
            momento = 0
        eventos.append(
            (
                momento,
                ordem,
                {
                    "quando": _quando(momento),
                    "papel": papel or None,
                    "quem": _QUEM.get(papel) or (_legivel(papel) if papel else None),
                    "evento": evento or None,
                    "titulo": titulo,
                    "motivo": motivo or None,
                    "texto": texto or None,
                    "fotos": _midia(r.get("images") or r.get("image_list"), video=False),
                    "videos": _midia(r.get("videos") or r.get("video_list"), video=True),
                },
            )
        )
    eventos.sort(key=lambda t: (t[0], t[1]))
    return [e for _, _, e in eventos]


def reembolso_pago_em(registros: list[dict] | None) -> str | None:
    """Quando o TikTok concluiu o reembolso (o último REFUND_SUCCESS), ou None."""
    pagos = [
        r
        for r in registros or []
        if isinstance(r, dict) and _texto(r.get("event")).upper() == "REFUND_SUCCESS"
    ]
    if not pagos:
        return None
    return _quando(max(int(r.get("create_time") or 0) for r in pagos))


def pendencias(caso: dict, agora: datetime) -> dict[str, str | None]:
    """O que o TikTok espera da loja agora → prazo (ISO). Prazo vencido não conta."""
    saida: dict[str, str | None] = {}
    for p in caso.get("seller_next_action_response") or []:
        if not isinstance(p, dict):
            continue
        acao = _texto(p.get("action")).upper()
        if not acao:
            continue
        prazo = _quando(p.get("deadline"))
        if prazo is not None and datetime.fromisoformat(prazo) <= agora:
            continue
        saida[acao] = prazo
    return saida


def _valor_texto(valor: float | None, moeda: str | None) -> str:
    if valor is None:
        return "o valor do caso"
    simbolo = "R$" if (moeda or "BRL").upper() == "BRL" else (moeda or "")
    inteiro, _, centavos = f"{valor:,.2f}".partition(".")
    return f"{simbolo} {inteiro.replace(',', '.')},{centavos}".strip()


def acoes_do_caso(caso: dict, agora: datetime) -> list[dict]:
    """As respostas que o TikTok espera AGORA deste caso (vazia = nada a decidir).

    Com o caso na arbitragem (o TikTok decide), nenhuma. Recusar o pacote do
    reembolso rápido vem TRAVADA (`bloqueio`): a API recusa (25011025)."""
    if _texto(caso.get("arbitration_status")).upper() == "IN_PROGRESS":
        return []
    reembolso = reembolso_do_caso(caso) or {}
    valor = _valor_texto(reembolso.get("total"), reembolso.get("moeda"))
    rapido = bool(caso.get("is_quick_refund"))
    saida: list[dict] = []
    for pendencia, prazo in pendencias(caso, agora).items():
        for codigo in ACOES_POR_PENDENCIA.get(pendencia, ()):
            recusa = codigo.startswith("recusar_")
            efeito = _EFEITO[codigo]
            if rapido and codigo == "confirmar_pacote":
                efeito = _EFEITO_RAPIDO
            saida.append(
                {
                    "codigo": codigo,
                    "rotulo": ROTULO[codigo],
                    "recusa": recusa,
                    "exige_foto": codigo == "recusar_pacote",
                    "efeito": efeito.format(valor=valor),
                    "bloqueio": _BLOQUEIO_RAPIDO if rapido and codigo == "recusar_pacote" else None,
                    "prazo": prazo,
                }
            )
    return saida


def escolher_caso(casos: list[dict] | None, return_id: str) -> dict | None:
    """O caso do `return_id` entre os do pedido (o pedido pode ter mais de um)."""
    for caso in casos or []:
        if isinstance(caso, dict) and _texto(caso.get("return_id")) == return_id:
            return caso
    return None


def detalhe(caso: dict, registros: list[dict] | None, agora: datetime) -> dict:
    """O bloco inteiro da tela a partir do caso (`returns/search`) e da linha do tempo."""
    from app.services.atendimento import reclamacoes_devolucoes

    status = _texto(caso.get("return_status")).upper() or None
    tipo_caso = _texto(caso.get("return_type")).upper() or None
    pendentes = pendencias(caso, agora)
    acao = next(iter(pendentes), None)
    return {
        "status": status,
        "status_rotulo": reclamacoes_devolucoes.status_tela("tiktok", status, tipo_caso),
        "tipo_caso": tipo_caso,
        "solucao": reclamacoes_devolucoes.solucao_do_caso(tipo_caso),
        "motivo": _texto(caso.get("return_reason_text")) or None,
        "reembolso": reembolso_do_caso(caso),
        "reembolso_rapido": bool(caso.get("is_quick_refund")),
        "reembolso_pago_em": reembolso_pago_em(registros),
        "volta": volta_do_caso(caso),
        "itens": itens_do_caso(caso),
        "acao_pendente": logistica_rules.acao_plataforma_pt("tiktok", acao) if acao else None,
        "prazo": pendentes.get(acao) if acao else None,
        "arbitragem": _texto(caso.get("arbitration_status")).upper() or None,
        "aberto_em": _quando(caso.get("create_time")),
        "acoes": acoes_do_caso(caso, agora),
        "historico": historico(registros),
    }


def erro_legivel(exc: BaseException) -> tuple[str, str]:
    """O erro da API do TikTok → (código, frase). Código desconhecido: a mensagem crua."""
    texto = str(exc)
    codigo = re.search(r"code=(\d+)", texto)
    if codigo and codigo.group(1) in _ERROS_TIKTOK:
        return codigo.group(1), _ERROS_TIKTOK[codigo.group(1)]
    mensagem = re.search(r"msg=(.*)$", texto)
    cru = (mensagem.group(1) if mensagem else texto).strip()[:200]
    return (codigo.group(1) if codigo else "erro"), f"O TikTok recusou: {cru}"


def chave_de_idempotencia(
    reclamacao_id: UUID,
    codigo: str,
    caso: dict,
    *,
    motivo: str | None = None,
    comentario: str | None = None,
    fotos: int = 0,
) -> str:
    """A mesma para o mesmo caso, no mesmo estado, com a mesma resposta — o clique
    repetido (ou a nova tentativa depois de um "não respondeu a tempo") não vira
    duas decisões. Recusa com outro motivo/comentário/fotos é outro pedido: outra chave."""
    estado = f"{_texto(caso.get('return_status'))}:{_texto(caso.get('update_time'))}"
    conteudo = f"{motivo or ''}|{comentario or ''}|{fotos}"
    return str(uuid5(_NS_IDEMPOTENCIA, f"{reclamacao_id}:{codigo}:{estado}:{conteudo}"))


def numero_do_caso(r: AtendimentoReclamacao) -> str | None:
    """O return_id do TikTok (None enquanto a Logística não o tem)."""
    numero = _texto(r.externo_id)
    return None if not numero or numero.startswith(reclamacoes.PREFIXO_SEM_ID) else numero


def tem_detalhe(r: AtendimentoReclamacao) -> bool:
    """Este caso tem o bloco completo (a plataforma e o nº do caso)?"""
    return r.plataforma in PLATAFORMAS and numero_do_caso(r) is not None


# ── Leitura na hora (só GET) ──────────────────────────────────────────────


async def _integracao(session: AsyncSession, r: AtendimentoReclamacao) -> Integration | None:
    if r.integration_id is not None:
        integ = await session.get(Integration, r.integration_id)
        if integ is not None:
            return integ
    conta = _texto((r.dados or {}).get("conta"))
    if not conta:
        return None
    from app.services.logistica_tiktok import _tiktok_integration_for_conta

    return await _tiktok_integration_for_conta(session, conta)


async def _cliente(
    session: AsyncSession, r: AtendimentoReclamacao, fabrica: FabricaCliente | None
) -> Any:
    integ = await _integracao(session, r)
    if integ is None:
        raise AcaoRecusada("sem_integracao", "A loja deste caso não está ligada no DaVinci.")
    if fabrica is None:
        from app.services.atendimento import clientes

        fabrica = clientes.cliente_da_integracao
    try:
        return await fabrica(integ)
    except Exception as exc:  # noqa: BLE001 — sem cliente, a tela mostra o que já tem
        logger.warning(
            "atendimento_devolucao_detalhe_cliente_falhou",
            reclamacao_id=str(r.id),
            erro=type(exc).__name__,
        )
        raise AcaoRecusada(
            "tiktok_indisponivel", "Não deu para falar com o TikTok agora. Tente de novo."
        ) from exc


async def _ler_caso(cliente: Any, r: AtendimentoReclamacao, numero: str) -> dict:
    casos = await cliente.get_return_list(order_ids=[_texto(r.pedido_marketplace)])
    caso = escolher_caso(casos, numero)
    if caso is None:
        raise AcaoRecusada(
            "caso_nao_achado",
            "O TikTok não devolveu este caso agora. Tente de novo em instantes.",
        )
    return caso


def _limpar_cache_de(reclamacao_id: UUID) -> str:
    return _CHAVE_CACHE.format(reclamacao_id)


async def _do_cache(reclamacao_id: UUID) -> dict | None:
    try:
        bruto = await redis.get(_limpar_cache_de(reclamacao_id))
    except Exception:  # noqa: BLE001 — sem Redis, lê na hora
        return None
    if not bruto:
        return None
    try:
        valor = json.loads(bruto)
    except ValueError:
        return None
    return valor if isinstance(valor, dict) else None


async def _guardar_cache(reclamacao_id: UUID, dados: dict) -> None:
    try:
        await redis.set(_limpar_cache_de(reclamacao_id), json.dumps(dados), ex=CACHE_S)
    except Exception:  # noqa: BLE001 — o cache é só atalho
        logger.info("atendimento_devolucao_detalhe_cache_falhou", reclamacao_id=str(reclamacao_id))


async def esquecer(reclamacao_id: UUID) -> None:
    try:
        await redis.delete(_limpar_cache_de(reclamacao_id))
    except Exception:  # noqa: BLE001
        logger.info("atendimento_devolucao_detalhe_cache_falhou", reclamacao_id=str(reclamacao_id))


async def _ler_na_hora(
    cliente: Any, r: AtendimentoReclamacao, numero: str, agora: datetime
) -> dict:
    """O caso e a linha do tempo no TikTok agora → o bloco (vai para o cache)."""
    caso = await _ler_caso(cliente, r, numero)
    registros = await cliente.get_return_records_strict(numero)
    saida = {
        "ao_vivo": True,
        "erro": None,
        "erro_texto": None,
        "lido_em": agora.isoformat(timespec="seconds"),
        **detalhe(caso, registros, agora),
    }
    await _guardar_cache(r.id, saida)
    return saida


def _falha(code: str, texto: str | None) -> dict:
    return {"ao_vivo": False, "erro": code, "erro_texto": texto}


async def ler_detalhe(
    session: AsyncSession,
    r: AtendimentoReclamacao,
    *,
    agora: datetime | None = None,
    forcar: bool = False,
    fabrica_cliente: FabricaCliente | None = None,
    cliente: Any | None = None,
) -> dict:
    """O bloco do caso, lido na hora no TikTok (ou do cache de `CACHE_S`).

    Nunca levanta por causa do TikTok: a falha volta em `erro` (a tela fica com
    o que o cartão já tem). Não grava nada no banco."""
    agora = agora or datetime.now(UTC)
    numero = numero_do_caso(r)
    if r.plataforma not in PLATAFORMAS or numero is None:
        return _falha("sem_detalhe", None)
    if not forcar:
        guardado = await _do_cache(r.id)
        if guardado is not None:
            guardado["acoes"] = acoes_do_caso_guardado(guardado, agora)
            return guardado
    try:
        if cliente is None:
            cliente = await _cliente(session, r, fabrica_cliente)
        return await _ler_na_hora(cliente, r, numero, agora)
    except AcaoRecusada as exc:
        return _falha(exc.code, exc.detail)
    except Exception as exc:  # noqa: BLE001 — o TikTok fora do ar não derruba a tela
        logger.warning(
            "atendimento_devolucao_detalhe_falhou",
            reclamacao_id=str(r.id),
            erro=type(exc).__name__,
        )
        return _falha(
            "tiktok_indisponivel", "Não deu para ler o histórico no TikTok agora. Tente de novo."
        )


def acoes_do_caso_guardado(guardado: dict, agora: datetime) -> list[dict]:
    """As ações do cache, sem as que venceram desde a leitura."""
    saida = []
    for acao in guardado.get("acoes") or []:
        prazo = acao.get("prazo") if isinstance(acao, dict) else None
        if prazo and datetime.fromisoformat(prazo) <= agora:
            continue
        saida.append(acao)
    return saida


async def _motivos(cliente: Any, numero: str) -> list[dict]:
    saida = []
    for m in await cliente.get_reject_reasons(numero) or []:
        codigo = _texto(m.get("name")) if isinstance(m, dict) else ""
        if codigo:
            saida.append({"codigo": codigo, "texto": _texto(m.get("text")) or _legivel(codigo)})
    return saida


async def motivos_de_recusa(
    session: AsyncSession,
    r: AtendimentoReclamacao,
    *,
    fabrica_cliente: FabricaCliente | None = None,
) -> list[dict]:
    """Os motivos de recusa que o TikTok aceita para este caso AGORA ([{codigo, texto}])."""
    numero = numero_do_caso(r)
    if r.plataforma not in PLATAFORMAS or numero is None:
        raise AcaoRecusada("sem_detalhe", "Este caso não tem resposta pelo DaVinci.", http=404)
    cliente = await _cliente(session, r, fabrica_cliente)
    return await _motivos(cliente, numero)


# ── A resposta da loja (POST no TikTok) ───────────────────────────────────


def _bytes(texto: str) -> int:
    return len(texto.encode("utf-8"))


def conferir_pedido(
    codigo: str, *, motivo: str | None, comentario: str | None, fotos: list[FotoDaRecusa]
) -> tuple[str | None, str | None]:
    """Confere o que a pessoa mandou ANTES de ir ao TikTok → (motivo, comentário) limpos."""
    if codigo not in DECISAO:
        raise AcaoRecusada("acao_invalida", "Resposta desconhecida.", http=422)
    if not codigo.startswith("recusar_"):
        return None, None
    motivo = _texto(motivo)
    comentario = " ".join(_texto(comentario).split())
    if not motivo:
        raise AcaoRecusada("motivo_obrigatorio", "Escolha o motivo da recusa.", http=422)
    if len(comentario) < COMENTARIO_MIN:
        raise AcaoRecusada(
            "comentario_obrigatorio",
            "Explique a recusa no comentário (é o que o TikTok lê para decidir).",
            http=422,
        )
    if _bytes(comentario) > COMENTARIO_MAX_BYTES:
        raise AcaoRecusada(
            "comentario_longo",
            f"O comentário passa do limite do TikTok ({COMENTARIO_MAX_BYTES} bytes). Resuma.",
            http=422,
        )
    if codigo == "recusar_pacote" and not fotos:
        raise AcaoRecusada(
            "foto_obrigatoria",
            "Para recusar o pacote, mande ao menos uma foto do produto que voltou.",
            http=422,
        )
    if len(fotos) > MAX_FOTOS:
        raise AcaoRecusada("fotos_demais", f"No máximo {MAX_FOTOS} fotos.", http=422)
    for foto in fotos:
        if foto.tipo not in FOTO_TIPOS:
            raise AcaoRecusada("foto_invalida", "Só foto JPG, PNG ou WEBP.", http=422)
        if not foto.dados or len(foto.dados) > FOTO_MAX_BYTES:
            raise AcaoRecusada("foto_invalida", "Foto vazia ou acima de 5 MB.", http=422)
    return motivo, comentario


def nota_da_resposta(
    codigo: str,
    numero: str,
    *,
    quem: str,
    motivo_texto: str | None,
    comentario: str | None,
    fotos: int,
) -> str:
    """A nota interna que registra a resposta (quem fez fica no autor da nota)."""
    linhas = [
        f"Respondido no TikTok pelo DaVinci: {ROTULO[codigo]} (devolução nº {numero}).",
        f"Por: {quem}.",
    ]
    if motivo_texto:
        linhas.append(f"Motivo: {motivo_texto}.")
    if comentario:
        linhas.append(f"Comentário: {comentario}")
    if fotos:
        linhas.append(f"Fotos enviadas: {fotos}.")
    return "\n".join(linhas)


async def responder(
    session: AsyncSession,
    r: AtendimentoReclamacao,
    conversa: AtendimentoConversa,
    codigo: str,
    *,
    user_id: UUID | None,
    quem: str,
    motivo: str | None = None,
    comentario: str | None = None,
    fotos: list[FotoDaRecusa] | None = None,
    agora: datetime | None = None,
    fabrica_cliente: FabricaCliente | None = None,
) -> dict:
    """Manda a resposta da loja ao TikTok. Levanta `AcaoRecusada` (nada saiu ou o
    TikTok recusou). Grava a nota interna (não commita) e devolve o bloco relido."""
    from app.services.atendimento import painel

    agora = agora or datetime.now(UTC)
    fotos = list(fotos or [])
    if not get_settings().atendimento_devolucao_acoes_ativa:
        raise AcaoRecusada(
            "devolucao_acoes_desligadas",
            "As respostas de devolução pelo DaVinci ainda estão desligadas.",
        )
    numero = numero_do_caso(r)
    if r.plataforma not in PLATAFORMAS or numero is None:
        raise AcaoRecusada("sem_detalhe", "Este caso não tem resposta pelo DaVinci.", http=404)
    motivo, comentario = conferir_pedido(codigo, motivo=motivo, comentario=comentario, fotos=fotos)

    cliente = await _cliente(session, r, fabrica_cliente)
    # O caso RELIDO agora: só sai a resposta que o TikTok espera neste momento.
    caso = await _ler_caso(cliente, r, numero)
    disponiveis = {a["codigo"]: a for a in acoes_do_caso(caso, agora)}
    escolhida = disponiveis.get(codigo)
    if escolhida is None:
        await esquecer(r.id)
        raise AcaoRecusada(
            "acao_indisponivel",
            "O caso mudou no TikTok e essa resposta não vale mais. A tela foi atualizada.",
        )
    if escolhida["bloqueio"]:
        raise AcaoRecusada("acao_travada", escolhida["bloqueio"])

    motivo_texto = None
    imagens: list[dict] = []
    if escolhida["recusa"]:
        validos = {m["codigo"]: m["texto"] for m in await _motivos(cliente, numero)}
        if validos and motivo not in validos:
            raise AcaoRecusada(
                "motivo_invalido",
                "Esse motivo não vale mais para o caso. Escolha de novo na lista.",
                http=422,
            )
        motivo_texto = validos.get(motivo) if validos else motivo
        for foto in fotos:
            try:
                dado = await cliente.upload_image(foto.nome, foto.dados, foto.tipo)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "atendimento_devolucao_foto_falhou",
                    reclamacao_id=str(r.id),
                    erro=type(exc).__name__,
                )
                raise AcaoRecusada(
                    "foto_nao_subiu", "O TikTok não aceitou uma das fotos. Nada foi enviado."
                ) from exc
            imagem: dict[str, Any] = {"image_id": dado.get("uri"), "mime_type": foto.tipo}
            if dado.get("width"):
                imagem["width"] = int(dado["width"])
            if dado.get("height"):
                imagem["height"] = int(dado["height"])
            imagens.append(imagem)

    chave = chave_de_idempotencia(
        r.id, codigo, caso, motivo=motivo, comentario=comentario, fotos=len(imagens)
    )
    try:
        if escolhida["recusa"]:
            await cliente.reject_return(
                numero,
                decision=DECISAO[codigo],
                reject_reason=motivo,
                comment=comentario,
                images=imagens or None,
                idempotency_key=chave,
            )
        else:
            await cliente.approve_return(numero, decision=DECISAO[codigo], idempotency_key=chave)
    except RuntimeError as exc:
        erro, frase = erro_legivel(exc)
        logger.warning(
            "atendimento_devolucao_resposta_recusada",
            reclamacao_id=str(r.id),
            acao=codigo,
            erro=erro,
        )
        await esquecer(r.id)
        raise AcaoRecusada(f"tiktok_{erro}", frase) from exc
    except Exception as exc:  # noqa: BLE001 — sem resposta: não se sabe se o TikTok recebeu
        logger.warning(
            "atendimento_devolucao_resposta_incerta",
            reclamacao_id=str(r.id),
            acao=codigo,
            erro=type(exc).__name__,
        )
        await esquecer(r.id)
        raise AcaoRecusada(
            "resposta_incerta",
            "O TikTok não respondeu a tempo: confira o caso no TikTok antes de tentar de novo "
            "(tentar de novo é seguro, a mesma resposta não sai duas vezes).",
            http=502,
        ) from exc

    logger.info(
        "atendimento_devolucao_respondida",
        reclamacao_id=str(r.id),
        conversa_id=str(conversa.id),
        acao=codigo,
        user_id=str(user_id) if user_id else None,
        fotos=len(imagens),
    )
    await painel.criar_nota(
        session,
        conversa,
        nota_da_resposta(
            codigo,
            numero,
            quem=quem,
            motivo_texto=motivo_texto,
            comentario=comentario,
            fotos=len(imagens),
        ),
        user_id=user_id,
    )
    await esquecer(r.id)
    return await ler_detalhe(session, r, agora=agora, forcar=True, cliente=cliente)
