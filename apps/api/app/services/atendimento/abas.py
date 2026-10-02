"""As ABAS da conversa aberta (RF2, 02/10/2026): tudo do mesmo comprador e pedido.

"Como o Duoke: 'Com o comprador / Com Meli'". Embaixo da conversa aberta,
as abas

    Pré-venda · Pós-venda · Reclamação · Mediador · E-mail · Zap · Avaliação

nessa ordem (`ORDEM_ABAS`), cada uma com a contagem de mensagens, só as que
têm conteúdo — e sempre a da própria conversa (é para onde a pessoa volta).
Elas juntam as conversas do MESMO COMPRADOR e do MESMO PEDIDO, NA MESMA LOJA
(a "família" da conversa aberta). Quem lê é `routers/atendimento_abas.py`.

QUEM ENTRA NA FAMÍLIA (`familia`):

  1. O PEDIDO ÂNCORA: as chaves da conversa aberta (`etiqueta_fatos.
     chaves_do_pedido`: o nº na plataforma e, no ML, o pack, o order e o order
     do retrato), mais o pedido e o pack das reclamações e das avaliações
     ligadas a ela ou a essas chaves — é assim que a conversa do pack
     (`pedido_marketplace` = pack) acha a conversa `reclamacao` (gravada pelo
     ORDER) e vice-versa. A pergunta do ML não tem pedido: a âncora dela é a
     COMPRA SEGUINTE do mesmo comprador no mesmo anúncio (`_compra_seguinte`).
  2. As conversas da mesma loja ligadas a esse pedido: o pack/chat, a
     `reclamacao` (comprador + loja na aba Reclamação, o `mediador` na aba
     Mediador), a `avaliacao`, o SAC, o e-mail e o Zap do pedido.
  3. Pré-venda do ML (e da Magalu): as PERGUNTAS do mesmo comprador NO MESMO
     ANÚNCIO feitas ANTES da compra (`_produto_bate`: o id do anúncio, o
     título ou a foto do item do pedido — o retrato do pack não guarda o id
     do anúncio; em produção, 02/10/2026, 9 das 12 perguntas de quem comprou
     batem pelo título). Sem a hora da compra, entram todas as do anúncio.
  4. Shopee/TikTok: o chat é UM SÓ por comprador (medido: no máximo 1 por
     comprador e loja) — entra pelo comprador, mesmo com o pedido de outra
     compra gravado nele. O corte entre Pré e Pós-venda é a PRIMEIRA compra
     do comprador na loja (`Familia.corte_do_chat`: a mais antiga entre a
     hora do pedido âncora e o índice `atendimento_pedidos_comprador`):
     antes dela = Pré-venda, depois = Pós-venda. Com uma compra só, é a
     hora do pedido âncora. Com duas ou mais, a conversa entre uma compra e
     a seguinte fica em Pós-venda — a hora não diz se ela é o pós-venda da
     anterior ou o pré-venda da próxima, e o pós-venda de uma compra antiga
     nunca pode virar "Pré-venda" do pedido de agora (produção, 02/10/2026:
     181 de 4.546 chats Shopee/TikTok são de comprador com 2+ pedidos).
  5. E-mail e Zap (`CANAIS_DE_CONTATO`): ligados ao pedido (o nº) ou ao
     comprador. Na Amazon o e-mail É o canal da plataforma (o `comprador_id`
     é o endereço que a Amazon repassa; aba Pós-venda). O e-mail do Tuta
     (`dados.fonte = 'tuta'`, `constantes.FONTE_TUTA`) e o Zap (o outro dev)
     guardam o CONTATO em `comprador_id` e o id do comprador NA PLATAFORMA,
     quando se sabe, em `dados.comprador_plataforma`. Pela PESSOA (sem o nº
     deste pedido) só entra o e-mail/Zap SEM pedido próprio — o de outro
     pedido é de outra compra, mesmo sendo do mesmo comprador.

NUNCA MISTURA (a regra que não pode falhar):
  - LOJA: mesma `integration_id` (nas lojas do robô, sem integração, o mesmo
    `canal_id`). Conversa sem loja conhecida (a Amazon que chegou sem conta)
    fica sozinha.
  - COMPRADOR: duas conversas com ids de comprador DIFERENTES (no mesmo
    "espaço": o id da plataforma × o contato do e-mail/Zap) nunca se juntam,
    nem com o mesmo nº de pedido. Sem id de um dos lados, vale o pedido.
  - CONTATO: o mesmo endereço/telefone só junta e-mail/Zap SEM pedido, e só
    quando a família também não tem pedido (o e-mail/Zap "sem vínculo"). O
    remetente de AVISO (noreply, "não responder", o domínio da plataforma —
    `contato_generico`) nunca junta nada: dois compradores recebem do mesmo.

A ABA DE CADA MENSAGEM (`aba_da_mensagem`, pura):
  - autor `mediador` → Mediador;
  - canal `reclamacao` → Reclamação (comprador, loja e os avisos do sistema);
  - pergunta → Pré-venda; pack (pos_venda), SAC e o e-mail DA AMAZON (o
    pós-venda da plataforma, a mesma regra da etiqueta) → Pós-venda;
    `avaliacao` → Avaliação; o e-mail do Tuta → E-mail; `zap` → Zap;
  - chat (Shopee, TikTok, Magalu, Temu, AliExpress): pelo corte do chat
    (acima; fora de Shopee/TikTok, a hora do pedido âncora: o `criado_em`
    do retrato ou, sem ele, o do índice de pedidos); sem a hora, a regra dos
    filtros (`etiqueta_fatos.e_pos_venda`: com pedido = Pós-venda).
  - Carrinho do site e comentário das redes não têm abas (não há pedido).

QUEM RESPONDE EM CADA ABA (`quem_responde`): a conversa aberta, se ela está
na aba (o chat da Shopee responde nas duas abas dele); senão a da aba que
espera resposta, a mais recente. A caixa da tela responde NESSA conversa
(`POST /conversas/{id}/responder`, com as travas de sempre). Mediador: só
leitura — ninguém responde ao mediador por aqui (`MOTIVO_MEDIADOR`).

CONSULTAS (sem N+1, com qualquer tamanho de família): as candidatas (1), as
reclamações e as avaliações do pedido (2), as que só elas trouxeram (0 ou
1), as do comprador do pedido (0 ou 1), a hora da compra e a primeira compra
do comprador no índice (0 ou 1, juntas), as mensagens LEVES de toda a
família (1: id, conversa, autor, origem, tipo, hora) e as COMPLETAS só da
página de cada aba (1, no router). Nada aqui escreve no banco nem fala com
loja nenhuma.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoAvaliacaoLoja,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
)
from app.services.atendimento.constantes import (
    AUTOR_MEDIADOR,
    CANAL_AVALIACAO,
    CANAL_CARRINHO,
    CANAL_CHAT,
    CANAL_COMENTARIO,
    CANAL_EMAIL,
    CANAL_PERGUNTA,
    CANAL_POS_VENDA,
    CANAL_RECLAMACAO,
    CANAL_SAC,
    CANAL_ZAP,
    FONTE_TUTA,
    PLATAFORMAS_EXTERNAS,
    e_nota,
)
from app.services.atendimento.etiqueta_fatos import (
    chaves_do_pedido,
    condicao_avaliacao_do_pedido,
    condicao_do_pedido,
    e_pos_venda,
)

# ── As abas (o vocabulário que a tela lê) ─────────────────────────────────
ABA_PRE_VENDA = "pre_venda"
ABA_POS_VENDA = "pos_venda"
ABA_RECLAMACAO = "reclamacao"
ABA_MEDIADOR = "mediador"
ABA_EMAIL = "email"
ABA_ZAP = "zap"
ABA_AVALIACAO = "avaliacao"
# A ordem da tela (o esboço da Tela 1 do PROJETO-COMUNICADOR: E-mail, Zap e
# Avaliação sempre depois do Mediador). `atendimento-abas.cjs` confere que a
# tela usa a mesma.
ORDEM_ABAS = (
    ABA_PRE_VENDA,
    ABA_POS_VENDA,
    ABA_RECLAMACAO,
    ABA_MEDIADOR,
    ABA_EMAIL,
    ABA_ZAP,
    ABA_AVALIACAO,
)
ROTULO_ABA = {
    ABA_PRE_VENDA: "Pré-venda",
    ABA_POS_VENDA: "Pós-venda",
    ABA_RECLAMACAO: "Reclamação",
    ABA_MEDIADOR: "Mediador",
    ABA_EMAIL: "E-mail",
    ABA_ZAP: "Zap",
    ABA_AVALIACAO: "Avaliação",
}

# A aba de cada canal. O chat NÃO está aqui: ele se divide pela hora da
# compra (`aba_da_mensagem`). O `email` aqui é o do TUTA: o e-mail DA AMAZON
# (o canal da plataforma) é Pós-venda (`aba_do_canal`).
ABA_DO_CANAL = {
    CANAL_PERGUNTA: ABA_PRE_VENDA,
    CANAL_POS_VENDA: ABA_POS_VENDA,
    CANAL_SAC: ABA_POS_VENDA,
    CANAL_RECLAMACAO: ABA_RECLAMACAO,
    CANAL_EMAIL: ABA_EMAIL,
    CANAL_ZAP: ABA_ZAP,
    CANAL_AVALIACAO: ABA_AVALIACAO,
}
# O nome da conversa de origem na tela ("Pergunta no anúncio · Mala ABS").
ROTULO_CANAL = {
    CANAL_PERGUNTA: "Pergunta no anúncio",
    CANAL_POS_VENDA: "Pós-venda",
    CANAL_SAC: "SAC",
    CANAL_CHAT: "Chat",
    CANAL_RECLAMACAO: "Reclamação",
    CANAL_AVALIACAO: "Avaliação",
    CANAL_EMAIL: "E-mail",
    CANAL_ZAP: "Zap",
}
ROTULO_EMAIL_AMAZON = "Mensagens da Amazon"
# Sem pedido de plataforma: nem família, nem abas.
CANAIS_SEM_ABAS = frozenset({CANAL_CARRINHO, CANAL_COMENTARIO})
# Onde o chat é UM SÓ por comprador e loja (produção, 02/10/2026: 3.982
# conversas da Shopee e 545 da TikTok, no máximo 1 por comprador).
PLATAFORMAS_CHAT_POR_COMPRADOR = frozenset({"shopee", "tiktok"})
# Canais de CONTATO: entram pelo pedido ou pela pessoa. Fora da Amazon (onde
# o e-mail é o próprio canal da plataforma), o `comprador_id` deles é o
# contato (e-mail, telefone) — outro "espaço" de id (`_comprador`).
CANAIS_DE_CONTATO = frozenset({CANAL_EMAIL, CANAL_ZAP})
# Onde o id do comprador NA PLATAFORMA fica numa conversa de contato (o
# e-mail do Tuta, o Zap): é por ele que ela entra pela pessoa, sem nº de pedido.
CHAVE_COMPRADOR_PLATAFORMA = "comprador_plataforma"
# A marca da conversa que veio do Tuta (`dados.fonte = constantes.FONTE_TUTA`):
# é o que separa, numa venda da Amazon, o e-mail do Tuta (contato, aba E-mail)
# do e-mail da Amazon (canal da plataforma, aba Pós-venda) — os dois são
# `canal 'email'` + `plataforma 'amazon'`.
CHAVE_FONTE = "fonte"

# Remetentes de AVISO — o "não responder" da plataforma, o robô do e-mail —
# não são o comprador: dois compradores diferentes recebem do mesmo endereço.
# Nunca servem de CONTATO para juntar conversas (`contato_generico`). A parte
# antes do @ é comparada sem acento, caixa nem pontuação ("nao-responder" =
# "naoresponder"); estas CONTIDAS nela...
_AVISO_CONTIDO = ("noreply", "naoresponda", "naoresponder", "donotreply", "mailerdaemon")
# ...e estas IGUAIS a ela.
_AVISO_IGUAL = frozenset(
    {
        "postmaster",
        "notificacao",
        "notificacoes",
        "notification",
        "notifications",
        "bounce",
        "bounces",
    }
)
# O domínio das plataformas (e os subdomínios): comprador não escreve de lá.
# O endereço de RETRANSMISSÃO da Amazon (`…@marketplace.amazon.com.br`) é um
# por comprador — esse não é aviso.
_DOMINIOS_DE_AVISO = (
    "mercadolivre.com",
    "mercadolivre.com.br",
    "mercadolibre.com",
    "shopee.com",
    "shopee.com.br",
    "tiktok.com",
    "tiktokshop.com",
    "amazon.com",
    "amazon.com.br",
    "magalu.com",
    "magazineluiza.com.br",
    "temu.com",
    "aliexpress.com",
)
_RETRANSMISSAO_AMAZON = "marketplace.amazon."

MOTIVO_MEDIADOR = (
    "Mediador: só leitura. É a plataforma falando na reclamação — a resposta ao "
    "mediador é dada na própria plataforma."
)

# Teto de conversas da família (as do pedido vêm primeiro na consulta).
MAX_CONVERSAS = 200
# Mensagens por aba na primeira leitura; a tela pede as mais antigas com
# `?aba=…&antes_de=<proximo>`.
POR_PAGINA = 50
MAX_POR_PAGINA = 200
# Teto da lista de mensagens DA CONVERSA ABERTA que moram em outra aba (a
# tela esconde da vista dela e mostra na aba certa). O detalhe traz 300.
MAX_FORA_DA_ABA = 1000

_SEM_HORA = datetime.min.replace(tzinfo=UTC)


# ── Conversões ────────────────────────────────────────────────────────────


def _texto(v: Any) -> str:
    return "" if v is None or isinstance(v, bool) else str(v).strip()


def _dados(c: AtendimentoConversa) -> dict:
    return c.dados if isinstance(c.dados, dict) else {}


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _de_iso(bruto: Any) -> datetime | None:
    if isinstance(bruto, datetime):
        return _utc(bruto)
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    try:
        return _utc(datetime.fromisoformat(bruto.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _normal(t: Any) -> str:
    """Título comparável: sem espaços repetidos, sem caixa."""
    return " ".join(_texto(t).split()).casefold()


# ── Loja, comprador e pedido ──────────────────────────────────────────────


def mesma_loja(a: AtendimentoConversa, b: AtendimentoConversa) -> bool:
    """Mesma loja: a mesma integração; nas lojas do robô (sem integração), o mesmo canal."""
    if a.integration_id is not None or b.integration_id is not None:
        return a.integration_id == b.integration_id
    return a.canal_id is not None and a.canal_id == b.canal_id


def e_email_tuta(c: AtendimentoConversa) -> bool:
    """O e-mail veio do Tuta (`dados.fonte = 'tuta'`)."""
    return c.canal == CANAL_EMAIL and _texto(_dados(c).get(CHAVE_FONTE)) == FONTE_TUTA


def e_contato(c: AtendimentoConversa) -> bool:
    """Conversa de CONTATO (e-mail do Tuta, Zap): o `comprador_id` é o contato,
    não o id do comprador na plataforma. Na Amazon, o e-mail sem a marca do
    Tuta é o canal da plataforma (o endereço de retransmissão da Amazon)."""
    if c.canal == CANAL_ZAP:
        return True
    if c.canal != CANAL_EMAIL:
        return False
    return c.plataforma != "amazon" or e_email_tuta(c)


def contato_generico(contato: str) -> bool:
    """Remetente de AVISO (noreply, "não responder", domínio da plataforma)? PURA.

    Ele não é o comprador: nunca junta conversas. Telefone nunca é aviso.
    """
    local, arroba, dominio = _texto(contato).casefold().rpartition("@")
    if not arroba or not local:
        return False
    sem_acento = unicodedata.normalize("NFKD", local).encode("ascii", "ignore").decode()
    chave = re.sub(r"[^a-z0-9]", "", sem_acento)
    if any(t in chave for t in _AVISO_CONTIDO) or chave in _AVISO_IGUAL:
        return True
    dominio = dominio.strip().rstrip(".")
    if dominio.startswith(_RETRANSMISSAO_AMAZON):
        return False
    return any(dominio == d or dominio.endswith("." + d) for d in _DOMINIOS_DE_AVISO)


@dataclass(frozen=True)
class Pessoa:
    """Quem é o comprador de uma conversa, nos dois "espaços" de id.

    `plataforma` = o id do comprador NA PLATAFORMA (`comprador_id` das
    conversas da loja; nas de contato, `dados.comprador_plataforma`, quando se
    sabe). `contato` = o e-mail/telefone das conversas de contato, que só se
    compara com outro contato do MESMO canal (e-mail × telefone não dizem nada
    um do outro). Só o id da plataforma EXCLUI: dois ids diferentes = duas
    pessoas; o contato só serve para JUNTAR (o mesmo endereço, o mesmo número)
    — e o remetente de aviso (`contato_generico`) nem isso: fica vazio.
    """

    plataforma: str = ""
    contato: str = ""
    canal: str = ""
    # O pedido diz DOIS compradores (dado torto) e a conversa aberta não diz
    # qual: nenhuma conversa com id da plataforma entra.
    conflito: bool = False


def pessoa(c: AtendimentoConversa) -> Pessoa:
    if e_contato(c):
        contato = _texto(c.comprador_id)
        return Pessoa(
            plataforma=_texto(_dados(c).get(CHAVE_COMPRADOR_PLATAFORMA)),
            contato="" if contato_generico(contato) else contato,
            canal=c.canal,
        )
    return Pessoa(plataforma=_texto(c.comprador_id))


def pessoas_compativeis(a: Pessoa, b: Pessoa) -> bool:
    """Podem ser a mesma pessoa? Ids da plataforma DIFERENTES = nunca."""
    if (a.conflito and b.plataforma) or (b.conflito and a.plataforma):
        return False
    return not (a.plataforma and b.plataforma and a.plataforma != b.plataforma)


def mesmo_id(a: Pessoa, b: Pessoa) -> bool:
    """O mesmo id do comprador NA PLATAFORMA."""
    return bool(a.plataforma) and a.plataforma == b.plataforma


def mesmo_contato(a: Pessoa, b: Pessoa) -> bool:
    """O mesmo endereço/telefone no mesmo canal de contato (nunca o de aviso)."""
    return bool(a.contato) and a.canal == b.canal and a.contato == b.contato


def mesma_pessoa(a: Pessoa, b: Pessoa) -> bool:
    """A mesma pessoa, com certeza: o mesmo id da plataforma, ou o mesmo contato no mesmo canal."""
    return mesmo_id(a, b) or mesmo_contato(a, b)


def compradores_compativeis(a: AtendimentoConversa, b: AtendimentoConversa) -> bool:
    return pessoas_compativeis(pessoa(a), pessoa(b))


def mesmo_comprador(a: AtendimentoConversa, b: AtendimentoConversa) -> bool:
    return mesma_pessoa(pessoa(a), pessoa(b))


def chaves(c: AtendimentoConversa) -> set[str]:
    """Os números do pedido da conversa (o mesmo elo da etiqueta e do painel)."""
    return set(chaves_do_pedido(c))


def e_pre_venda(c: AtendimentoConversa) -> bool:
    """Conversa de ANTES da compra que entra pelo anúncio: a pergunta; o chat sem pedido
    (fora de Shopee/TikTok, onde o chat entra pelo comprador)."""
    if c.canal == CANAL_PERGUNTA:
        return True
    return (
        c.canal == CANAL_CHAT
        and c.plataforma not in PLATAFORMAS_CHAT_POR_COMPRADOR
        and not chaves(c)
    )


def quando_perguntou(c: AtendimentoConversa) -> datetime | None:
    """A hora da fala do cliente (a mesma régua do cartão Cliente)."""
    return _utc(c.ultima_do_cliente_em) or _utc(c.ultima_mensagem_em) or _utc(c.created_at)


def _retrato(c: AtendimentoConversa) -> dict:
    return _dict(_dados(c).get("pedido_mkt"))


def compra_do_retrato(c: AtendimentoConversa, pedido: Iterable[str]) -> datetime | None:
    """A hora da compra pelo retrato do pedido — só se o retrato é DESTE pedido."""
    retrato = _retrato(c)
    if _texto(retrato.get("pedido")) not in set(pedido):
        return None
    return _de_iso(retrato.get("criado_em"))


# ── Produto (a pergunta é do mesmo anúncio?) ──────────────────────────────


@dataclass
class Produtos:
    """O que se sabe dos produtos do pedido âncora."""

    ids: set[str] = field(default_factory=set)
    titulos: set[str] = field(default_factory=set)
    imagens: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.ids or self.titulos or self.imagens)


def produtos_do_pedido(
    conversas: Iterable[AtendimentoConversa], pedido: Iterable[str], itens: Iterable[str] = ()
) -> Produtos:
    """Os anúncios do pedido: o `anuncio_id` das conversas do pedido (menos o chat,
    onde ele é só o último produto falado), os itens das avaliações e o título e
    a foto dos itens do retrato (o retrato do ML não guarda o id do anúncio)."""
    numeros = set(pedido)
    p = Produtos(ids={i for i in (_texto(x) for x in itens) if i})
    for c in conversas:
        if c.canal not in (CANAL_CHAT, CANAL_PERGUNTA) and _texto(c.anuncio_id):
            p.ids.add(_texto(c.anuncio_id))
        retrato = _retrato(c)
        if _texto(retrato.get("pedido")) not in numeros:
            continue
        for it in retrato.get("itens") or []:
            it = _dict(it)
            if titulo := _normal(it.get("titulo")):
                p.titulos.add(titulo)
            if imagem := _texto(it.get("imagem")):
                p.imagens.add(imagem)
    return p


def produto_bate(c: AtendimentoConversa, produtos: Produtos) -> bool:
    """A conversa de pré-venda é sobre um dos produtos do pedido? (id, título ou foto)"""
    dados = _dados(c)
    produto = _dict(dados.get("produto"))
    item = _texto(c.anuncio_id) or _texto(dados.get("item_id")) or _texto(produto.get("item_id"))
    if item and item in produtos.ids:
        return True
    for titulo in (_normal(c.anuncio_titulo), _normal(produto.get("titulo"))):
        if titulo and titulo in produtos.titulos:
            return True
    imagem = _texto(produto.get("imagem"))
    return bool(imagem) and imagem in produtos.imagens


def mesmo_anuncio(a: AtendimentoConversa, b: AtendimentoConversa) -> bool:
    """Duas conversas de pré-venda sobre o mesmo anúncio (sem pedido à vista)."""

    def item(c: AtendimentoConversa) -> str:
        return _texto(c.anuncio_id) or _texto(_dados(c).get("item_id"))

    return bool(item(a)) and item(a) == item(b)


# ── A aba de cada mensagem ────────────────────────────────────────────────


def aba_do_canal(conversa: AtendimentoConversa) -> str | None:
    """A aba que o CANAL decide (None = o chat, que se divide pela hora). PURA.

    O e-mail da Amazon é o pós-venda da plataforma (as mensagens do comprador
    repassadas pela Amazon; a mesma regra da etiqueta, `CANAIS_SEMPRE_POS_VENDA`,
    e da tabela de integrações do PROJETO-COMUNICADOR); a aba E-mail é a do
    Tuta (RF5).
    """
    if conversa.canal == CANAL_EMAIL and not e_contato(conversa):
        return ABA_POS_VENDA
    return ABA_DO_CANAL.get(conversa.canal)


def rotulo_do_canal(conversa: AtendimentoConversa) -> str:
    """O nome da conversa de origem na tela ("Pergunta no anúncio", "Mensagens da Amazon")."""
    if conversa.canal == CANAL_EMAIL and not e_contato(conversa):
        return ROTULO_EMAIL_AMAZON
    return ROTULO_CANAL.get(conversa.canal, conversa.canal)


def aba_da_mensagem(
    conversa: AtendimentoConversa,
    autor: str | None,
    momento: datetime | None,
    corte: datetime | None,
) -> str | None:
    """A aba de uma mensagem (PURA). None = conversa sem abas (carrinho, comentário).

    `corte` = o corte do chat entre Pré e Pós-venda (`Familia.corte_do_chat`).
    """
    if conversa.canal in CANAIS_SEM_ABAS:
        return None
    if autor == AUTOR_MEDIADOR:
        return ABA_MEDIADOR
    base = aba_do_canal(conversa)
    if base is not None:
        return base
    # O chat (e o canal que ainda não tem aba própria): pela hora da compra.
    momento = _utc(momento)
    if corte is not None and momento is not None:
        return ABA_PRE_VENDA if momento < corte else ABA_POS_VENDA
    if e_pos_venda(conversa.canal, conversa.pedido_marketplace):
        return ABA_POS_VENDA
    return ABA_PRE_VENDA


def aba_da_conversa(
    conversa: AtendimentoConversa, abas_das_mensagens: Sequence[str], corte: datetime | None
) -> str | None:
    """A aba da conversa ABERTA (a padrão da tela). PURA.

    O canal diz (a reclamação é Reclamação, mesmo com o mediador falando por
    último); o chat fica na aba da ÚLTIMA mensagem dele — a parte da
    conversa que está acontecendo agora.
    """
    if conversa.canal in CANAIS_SEM_ABAS:
        return None
    base = aba_do_canal(conversa)
    if base is not None:
        return base
    for aba in reversed(abas_das_mensagens):
        if aba != ABA_MEDIADOR:
            return aba
    return aba_da_mensagem(conversa, None, None, corte)


# ── A família ─────────────────────────────────────────────────────────────


@dataclass
class Familia:
    aberta: AtendimentoConversa
    # A aberta primeiro; depois as outras.
    conversas: list[AtendimentoConversa]
    # As chaves do pedido âncora (vazio = sem pedido).
    pedido: frozenset[str] = frozenset()
    # A hora da compra do pedido âncora (o corte das perguntas do anúncio).
    compra_em: datetime | None = None
    # A PRIMEIRA compra do comprador na loja, pelo índice de pedidos (só onde
    # o chat é um por comprador: Shopee/TikTok).
    primeira_compra_em: datetime | None = None

    @property
    def corte_do_chat(self) -> datetime | None:
        """O corte do chat entre Pré e Pós-venda: a compra mais antiga que se conhece."""
        horas = [d for d in (self.compra_em, self.primeira_compra_em) if d is not None]
        return min(horas) if horas else None


def _clausula_loja(aberta: AtendimentoConversa):
    """WHERE da mesma loja; None = sem loja conhecida (fica só a aberta)."""
    c = AtendimentoConversa
    if aberta.integration_id is not None:
        return c.integration_id == aberta.integration_id
    if aberta.canal_id is not None:
        return and_(c.integration_id.is_(None), c.canal_id == aberta.canal_id)
    return None


def _condicao_chaves(numeros: Sequence[str]):
    """SQL de `chaves()`: o nº, o pack, o order ou o order do retrato (ML) da conversa."""
    c = AtendimentoConversa
    lista = sorted(numeros)
    return or_(
        c.pedido_marketplace.in_(lista),
        c.dados["pack_id"].astext.in_(lista),
        c.dados["order_id"].astext.in_(lista),
        and_(c.plataforma == "ml", c.dados[("pedido_mkt", "pedido")].astext.in_(lista)),
    )


async def _candidatas(
    session: AsyncSession,
    loja,
    *,
    ref: Pessoa | None = None,
    numeros: Iterable[str] = (),
    ids: Iterable[UUID] = (),
) -> list[AtendimentoConversa]:
    """As conversas da loja do mesmo comprador, das chaves do pedido ou destes ids.

    As do pedido (e as dos ids) primeiro: o teto nunca corta o pedido por
    causa de um comprador que perguntou 300 vezes.
    """
    c = AtendimentoConversa
    numeros = sorted({n for n in numeros if n})
    ids = list({i for i in ids if i is not None})
    do_pedido = []
    if numeros:
        do_pedido.append(_condicao_chaves(numeros))
    if ids:
        do_pedido.append(c.id.in_(ids))
    conds = list(do_pedido)
    if ref is not None and ref.plataforma:
        # Pela coluna e pelo id da plataforma que o e-mail/Zap guarda em `dados`.
        conds.append(c.comprador_id == ref.plataforma)
        conds.append(c.dados[CHAVE_COMPRADOR_PLATAFORMA].astext == ref.plataforma)
    if ref is not None and ref.contato and not numeros:
        # O mesmo endereço/número, no mesmo canal de contato — só sem pedido
        # (com pedido, o e-mail/Zap entra pelo nº ou pelo id do comprador).
        conds.append(and_(c.canal == ref.canal, c.comprador_id == ref.contato))
    if not conds:
        return []
    ordem = [case((or_(*do_pedido), 0), else_=1)] if do_pedido else []
    consulta = (
        select(c)
        .where(loja, or_(*conds), c.canal.not_in(sorted(CANAIS_SEM_ABAS)))
        .order_by(*ordem, c.ultima_mensagem_em.desc().nulls_last(), c.created_at.desc())
        .limit(MAX_CONVERSAS)
    )
    return list((await session.execute(consulta)).scalars().all())


def _compra_seguinte(
    aberta: AtendimentoConversa, candidatas: Iterable[AtendimentoConversa]
) -> AtendimentoConversa | None:
    """A conversa do pedido que a pergunta virou: o mesmo comprador, o mesmo
    anúncio, comprado DEPOIS da pergunta — a primeira compra assim."""
    perguntou = quando_perguntou(aberta)
    candidatas = [
        x
        for x in candidatas
        if x.id != aberta.id and mesmo_comprador(x, aberta) and not e_contato(x) and chaves(x)
    ]
    # A hora da compra é do PEDIDO, não da conversa: a reclamação (sem
    # retrato) vale a hora que o retrato do pack do mesmo pedido diz.
    compra_por_numero: dict[str, datetime] = {}
    for x in candidatas:
        numeros = chaves(x)
        if (compra := compra_do_retrato(x, numeros)) is not None:
            for n in numeros:
                if n not in compra_por_numero or compra < compra_por_numero[n]:
                    compra_por_numero[n] = compra
    achadas: list[tuple[datetime, AtendimentoConversa]] = []
    for x in candidatas:
        numeros = chaves(x)
        if not produto_bate(aberta, produtos_do_pedido([x], numeros)):
            continue
        compras = [compra_por_numero[n] for n in numeros if n in compra_por_numero]
        compra = min(compras) if compras else None
        if compra is not None and perguntou is not None and compra < perguntou:
            continue  # comprou ANTES de perguntar: não é a compra desta pergunta
        achadas.append((compra or datetime.max.replace(tzinfo=UTC), x))
    if not achadas:
        return None
    return min(achadas, key=lambda par: par[0])[1]


async def _ligadas_ao_pedido(
    session: AsyncSession, aberta: AtendimentoConversa, numeros: set[str]
) -> tuple[set[str], set[UUID], set[str]]:
    """Reclamações e avaliações da aberta ou do pedido → (números a mais, conversas, itens).

    Na mesma plataforma (o nº do pedido é único nela). Duas consultas.
    """
    mais: set[str] = set()
    conversas: set[UUID] = set()
    itens: set[str] = set()
    r = AtendimentoReclamacao
    conds = [r.conversa_id == aberta.id]
    if numeros:
        conds.append(condicao_do_pedido(sorted(numeros)))
    for pedido, pack, conversa_id in (
        await session.execute(
            select(r.pedido_marketplace, r.dados["pack_id"].astext, r.conversa_id).where(
                r.plataforma == aberta.plataforma, or_(*conds)
            )
        )
    ).all():
        mais |= {_texto(pedido), _texto(pack)}
        if conversa_id is not None:
            conversas.add(conversa_id)
    a = AtendimentoAvaliacaoLoja
    conds = [a.conversa_id == aberta.id]
    if numeros:
        conds.append(condicao_avaliacao_do_pedido(sorted(numeros)))
    for pedido, pack, conversa_id, item in (
        await session.execute(
            select(a.pedido, a.dados["pack_id"].astext, a.conversa_id, a.item_id).where(
                a.plataforma == aberta.plataforma, or_(*conds)
            )
        )
    ).all():
        mais |= {_texto(pedido), _texto(pack)}
        if conversa_id is not None:
            conversas.add(conversa_id)
        if _texto(item):
            itens.add(_texto(item))
    mais.discard("")
    return mais, conversas, itens


async def _compras_no_indice(
    session: AsyncSession, aberta: AtendimentoConversa, numeros: set[str], comprador: str
) -> tuple[datetime | None, datetime | None]:
    """Pelo índice de pedidos (Shopee) → (a hora da compra destes números, a
    PRIMEIRA compra deste comprador na loja). Uma consulta (0 sem nada a ver)."""
    if aberta.integration_id is None or not (numeros or comprador):
        return None, None
    t = AtendimentoPedidoComprador
    do_pedido = t.pedido.in_(sorted(numeros)) if numeros else false()
    do_comprador = t.comprador_id == comprador if comprador else false()
    linha = (
        await session.execute(
            select(
                func.min(case((do_pedido, t.criado_em))),
                func.min(case((do_comprador, t.criado_em))),
            ).where(t.integration_id == aberta.integration_id, or_(do_pedido, do_comprador))
        )
    ).one()
    return _utc(linha[0]), _utc(linha[1])


def _membro(
    x: AtendimentoConversa,
    aberta: AtendimentoConversa,
    ref: Pessoa,
    pedido: set[str],
    ligadas: set[UUID],
    produtos: Produtos,
    compra_em: datetime | None,
) -> bool:
    """A conversa `x` (da mesma loja) entra na família da aberta? PURA.

    `ref` é o comprador da família: o da aberta, ou o do pedido quando a
    aberta (o e-mail/Zap) só tem o contato.
    """
    if x.id == aberta.id:
        return True
    if not mesma_loja(x, aberta) or x.canal in CANAIS_SEM_ABAS:
        return False
    px = pessoa(x)
    if not pessoas_compativeis(px, ref):
        return False  # comprador diferente NUNCA entra, nem com o mesmo nº de pedido
    numeros = chaves(x)
    if (pedido and numeros & pedido) or x.id in ligadas:
        return True
    if not mesma_pessoa(ref, px):
        return False
    if x.canal in CANAIS_DE_CONTATO:
        # O e-mail/Zap (e o e-mail da Amazon) de OUTRO pedido é de outra
        # compra, mesmo sendo do mesmo comprador: pela pessoa, só o SEM pedido.
        if numeros:
            return False
        # E pelo CONTATO (sem o id da plataforma), só quando a família também
        # não tem pedido — o e-mail/Zap "sem vínculo" do mesmo endereço.
        return not pedido or mesmo_id(ref, px)
    if x.canal == CANAL_CHAT and x.plataforma in PLATAFORMAS_CHAT_POR_COMPRADOR:
        return True
    if e_pre_venda(x) and not numeros:
        if pedido:
            if not produto_bate(x, produtos):
                return False
            perguntou = quando_perguntou(x)
            return compra_em is None or perguntou is None or perguntou < compra_em
        # Sem pedido nenhum (a pergunta que não virou compra): as outras
        # perguntas do mesmo comprador no mesmo anúncio.
        return e_pre_venda(aberta) and mesmo_anuncio(x, aberta)
    return False


async def familia(session: AsyncSession, aberta: AtendimentoConversa) -> Familia:
    """As conversas do mesmo comprador e pedido, na mesma loja (ver o topo)."""
    if aberta.canal in CANAIS_SEM_ABAS or aberta.plataforma in PLATAFORMAS_EXTERNAS:
        return Familia(aberta=aberta, conversas=[aberta])
    pedido = chaves(aberta)
    loja = _clausula_loja(aberta)
    if loja is None:
        return Familia(
            aberta=aberta,
            conversas=[aberta],
            pedido=frozenset(pedido),
            compra_em=compra_do_retrato(aberta, pedido),
        )
    ref = pessoa(aberta)
    candidatas = await _candidatas(session, loja, ref=ref, numeros=pedido)
    buscados = set(pedido)
    if not pedido and e_pre_venda(aberta):
        ancora = _compra_seguinte(aberta, candidatas)
        if ancora is not None:
            pedido = chaves(ancora)
    mais, ligadas, itens = await _ligadas_ao_pedido(session, aberta, pedido)
    pedido |= mais
    # Uma consulta a mais só quando a âncora ou as reclamações/avaliações
    # trouxeram um nº (ou uma conversa) que a primeira não procurou — as do
    # pedido sem `comprador_id` (a avaliação da Shopee) só aparecem pelo nº.
    conhecidas = {x.id for x in candidatas}
    faltam = pedido - buscados
    if faltam or (ligadas - conhecidas):
        candidatas += [
            x
            for x in await _candidatas(session, loja, numeros=faltam, ids=ligadas - conhecidas)
            if x.id not in conhecidas
        ]
    do_pedido = [
        x
        for x in candidatas
        if x.id != aberta.id
        and mesma_loja(x, aberta)
        and x.canal not in CANAIS_SEM_ABAS
        and ((pedido and chaves(x) & pedido) or x.id in ligadas)
    ]
    if not ref.plataforma and do_pedido:
        # A aberta sem o id do comprador (o e-mail/Zap que só tem o contato,
        # a avaliação da Shopee): o comprador é o do pedido — se o pedido
        # disser UM só. As conversas dele que entram pela pessoa (as
        # perguntas, o chat da Shopee) pedem uma consulta a mais.
        donos = {p for x in do_pedido if (p := pessoa(x).plataforma)}
        if len(donos) > 1:
            ref = replace(ref, conflito=True)
        elif donos:
            ref = replace(ref, plataforma=donos.pop())
            conhecidas = {x.id for x in candidatas}
            candidatas += [
                x
                for x in await _candidatas(session, loja, ref=Pessoa(plataforma=ref.plataforma))
                if x.id not in conhecidas
            ]
    do_pedido = [aberta] + [x for x in do_pedido if pessoas_compativeis(pessoa(x), ref)]
    compra_em = None
    if pedido:
        compras = [d for x in do_pedido if (d := compra_do_retrato(x, pedido)) is not None]
        compra_em = min(compras) if compras else None
    # O índice de pedidos (uma consulta, as duas perguntas juntas): a hora da
    # compra quando nenhum retrato a diz, e — onde o chat é um por comprador
    # — a PRIMEIRA compra dele na loja (o corte do chat, `corte_do_chat`).
    sem_hora = set(pedido) if pedido and compra_em is None else set()
    comprador_do_chat = (
        ref.plataforma if aberta.plataforma in PLATAFORMAS_CHAT_POR_COMPRADOR else ""
    )
    primeira_compra_em = None
    if sem_hora or comprador_do_chat:
        do_indice, primeira_compra_em = await _compras_no_indice(
            session, aberta, sem_hora, comprador_do_chat
        )
        if sem_hora:
            compra_em = do_indice
    produtos = produtos_do_pedido(do_pedido, pedido, itens)
    membros = [aberta] + [
        x
        for x in candidatas
        if x.id != aberta.id and _membro(x, aberta, ref, pedido, ligadas, produtos, compra_em)
    ]
    return Familia(
        aberta=aberta,
        conversas=membros,
        pedido=frozenset(pedido),
        compra_em=compra_em,
        primeira_compra_em=primeira_compra_em,
    )


# ── As mensagens, por aba ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Leve:
    """A mensagem sem o texto: o que decide a aba, a contagem e a página."""

    id: UUID
    conversa_id: UUID
    autor: str
    momento: datetime
    nota: bool

    @property
    def ordem(self) -> tuple[datetime, str]:
        return (self.momento, str(self.id))


async def mensagens_leves(session: AsyncSession, ids: Sequence[UUID]) -> list[Leve]:
    """As mensagens de toda a família, sem texto, da mais antiga para a mais nova (1 consulta)."""
    if not ids:
        return []
    m = AtendimentoMensagem
    momento = func.coalesce(m.enviada_em, m.created_at)
    linhas = (
        await session.execute(
            select(m.id, m.conversa_id, m.autor, m.origem, m.tipo, momento.label("momento")).where(
                m.conversa_id.in_(list(ids))
            )
        )
    ).all()
    leves = [
        Leve(
            id=linha.id,
            conversa_id=linha.conversa_id,
            autor=linha.autor,
            momento=_utc(linha.momento) or _SEM_HORA,
            nota=e_nota(linha.origem, linha.tipo),
        )
        for linha in linhas
    ]
    leves.sort(key=lambda x: x.ordem)
    return leves


@dataclass
class Aba:
    chave: str
    mensagens: list[Leve] = field(default_factory=list)
    # As conversas que têm mensagem nesta aba (na ordem em que aparecem).
    conversas: list[UUID] = field(default_factory=list)


@dataclass
class Abas:
    familia: Familia
    aba_da_conversa: str | None
    # As abas com conteúdo (e a da conversa aberta), na ordem da tela.
    abas: list[Aba]
    # Mensagens da conversa aberta que moram em OUTRA aba: {id: aba}.
    fora_da_aba: dict[UUID, str]

    def aba(self, chave: str) -> Aba | None:
        return next((a for a in self.abas if a.chave == chave), None)


def separar(fam: Familia, leves: Sequence[Leve]) -> Abas:
    """Cada mensagem na sua aba (PURA)."""
    por_conversa = {c.id: c for c in fam.conversas}
    por_aba: dict[str, Aba] = {chave: Aba(chave) for chave in ORDEM_ABAS}
    da_aberta: list[tuple[Leve, str]] = []
    for m in leves:
        conversa = por_conversa.get(m.conversa_id)
        if conversa is None:
            continue
        chave = aba_da_mensagem(conversa, m.autor, m.momento, fam.corte_do_chat)
        if chave is None:
            continue
        aba = por_aba[chave]
        aba.mensagens.append(m)
        if m.conversa_id not in aba.conversas:
            aba.conversas.append(m.conversa_id)
        if m.conversa_id == fam.aberta.id:
            da_aberta.append((m, chave))
    casa = aba_da_conversa(
        fam.aberta, [chave for m, chave in da_aberta if not m.nota], fam.corte_do_chat
    )
    if casa is not None and fam.aberta.id not in por_aba[casa].conversas:
        por_aba[casa].conversas.insert(0, fam.aberta.id)
    visiveis = [por_aba[chave] for chave in ORDEM_ABAS if por_aba[chave].mensagens or chave == casa]
    fora = {m.id: chave for m, chave in da_aberta if chave != casa}
    if len(fora) > MAX_FORA_DA_ABA:
        fora = dict(list(fora.items())[-MAX_FORA_DA_ABA:])
    return Abas(familia=fam, aba_da_conversa=casa, abas=visiveis, fora_da_aba=fora)


def pagina(
    aba: Aba,
    *,
    sem_conversa: UUID | None = None,
    antes: tuple[datetime, str] | None = None,
    limite: int = POR_PAGINA,
) -> tuple[list[Leve], bool]:
    """As `limite` mais novas da aba (antes do cursor), em ordem → (página, tem_mais).

    `sem_conversa`: na aba da conversa aberta, as dela já vêm no detalhe —
    a página traz só as das OUTRAS conversas.
    """
    itens = [m for m in aba.mensagens if m.conversa_id != sem_conversa]
    if antes is not None:
        itens = [m for m in itens if m.ordem < antes]
    return itens[-limite:] if limite > 0 else [], len(itens) > limite


def cursor_de(m: Leve) -> str:
    """O cursor da página seguinte (a mais antiga da página): "<ISO>|<id>"."""
    return f"{m.momento.isoformat()}|{m.id}"


def ler_cursor(bruto: str | None) -> tuple[datetime, str] | None:
    """`cursor_de` → (hora, id); levanta ValueError se não for um cursor."""
    if bruto is None or not bruto.strip():
        return None
    hora, sep, ident = bruto.strip().rpartition("|")
    if not sep:
        raise ValueError("cursor_invalido")
    quando = _de_iso(hora)
    if quando is None:
        raise ValueError("cursor_invalido")
    return quando, str(UUID(ident))


def quem_responde(
    chave: str, conversas: Sequence[AtendimentoConversa], aberta: AtendimentoConversa
) -> AtendimentoConversa | None:
    """A conversa por onde a caixa responde nesta aba. PURA. None = só leitura (Mediador)."""
    if chave == ABA_MEDIADOR or not conversas:
        return None
    if any(c.id == aberta.id for c in conversas):
        return aberta
    return min(
        conversas,
        key=lambda c: (
            not c.aguardando_resposta,
            -(_utc(c.ultima_mensagem_em) or _SEM_HORA).timestamp(),
        ),
    )


def titulo_da_conversa(c: AtendimentoConversa) -> str | None:
    """O que identifica a conversa de origem na tela (sem dado pessoal)."""
    if c.canal in (CANAL_PERGUNTA, CANAL_AVALIACAO):
        return (
            _texto(c.anuncio_titulo)
            or _texto(_dict(_dados(c).get("produto")).get("titulo"))
            or None
        )
    if c.canal == CANAL_RECLAMACAO:
        return f"nº {c.externo_id}" if _texto(c.externo_id) else None
    pedido = _texto(c.pedido_marketplace)
    return f"pedido {pedido}" if pedido else None


async def abas_da_conversa(session: AsyncSession, aberta: AtendimentoConversa) -> Abas:
    """A família, as mensagens leves e a separação por aba (ver o topo)."""
    fam = await familia(session, aberta)
    if aberta.canal in CANAIS_SEM_ABAS or aberta.plataforma in PLATAFORMAS_EXTERNAS:
        return Abas(familia=fam, aba_da_conversa=None, abas=[], fora_da_aba={})
    leves = await mensagens_leves(session, [c.id for c in fam.conversas])
    return separar(fam, leves)
