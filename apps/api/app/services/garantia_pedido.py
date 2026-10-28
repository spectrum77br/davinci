"""Pedido de garantia (28/10/2026).

Pedido do Eduardo: "a garantia é só para o pessoal que solicita a garantia do
aparelho" — o Painel de Garantia não é a lista de vendas. O robô continua
criando a garantia de toda venda com NF de produto (é ela que guarda a NF, o
prazo e o "vendidas" da porcentagem), mas o Painel e o Resumo mostram só a que
tem PEDIDO DE GARANTIA (`solicitada_em`), e a porcentagem do Resumo é
pediram ÷ vendidas.

Conta como pedido (o que o Eduardo marcou, 28/10):
  • a mensagem do cliente no chat da loja (Shopee, ML, TikTok, Amazon) ou por
    e-mail, DEPOIS da compra, relatando defeito do produto ou pedindo a
    garantia (`pede_garantia`);
  • o formulário do SAC do site ligado à garantia (`garantia_sac`);
  • a devolução lançada como "Dano funcional / Não funciona" ou "Danificado
    (Outros)", ou mandada para a manutenção — "Bloqueado" NÃO: lendo as
    conversas (28/10), é o cliente que desistiu e devolveu com a senha dele;
  • o atendimento registrado na garantia e a garantia cadastrada à mão.
Vale o sinal mais antigo. A pessoa corrige: "não é pedido de garantia" (o robô
não marca de novo) e "marcar como pedido de garantia".

O detector é PRÓPRIO, não o `_relata_defeito` do robô do Atendimento: aquele
decide se uma pessoa precisa responder e conta também o pedido que veio
errado, o item faltando, a caixa amassada e o rastreio que parou — aqui só
vale o defeito do PRODUTO.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.models.atendimento import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
)
from app.models.devolution import Devolution
from app.models.garantia import Garantia, GarantiaAtendimento, GarantiaConversa
from app.services import garantia as svc
from app.services.mail_atendimento.ponte import PREFIXO_PROTOCOLO

ORIGEM_MENSAGEM = "mensagem"
ORIGEM_SAC = "sac"
ORIGEM_DEVOLUCAO = "devolucao"
ORIGEM_ATENDIMENTO = "atendimento"
ORIGEM_MANUAL = "manual"
ORIGEM_PESSOA = "pessoa"
# As origens que o robô marca (e reavalia quando o pedido da garantia muda).
ORIGENS_DO_ROBO = (ORIGEM_MENSAGEM, ORIGEM_SAC, ORIGEM_DEVOLUCAO)
ORIGEM_ROTULOS = {
    ORIGEM_MENSAGEM: "mensagem do cliente",
    ORIGEM_SAC: "SAC do site",
    ORIGEM_DEVOLUCAO: "devolução por defeito",
    ORIGEM_ATENDIMENTO: "atendimento registrado",
    ORIGEM_MANUAL: "cadastrada à mão",
    ORIGEM_PESSOA: "marcada pela equipe",
}
# A devolução que é defeito (os motivos do módulo Devoluções) ou que foi para
# a manutenção. "Bloqueado" é o aparelho devolvido com a senha do cliente (a
# desistência), não defeito — nem quando vai para a manutenção desbloquear.
MOTIVOS_DEFEITO = ("Dano funcional / Não funciona", "Danificado (Outros)")
MOTIVO_BLOQUEADO = "Bloqueado"
CONDICAO_MANUTENCAO = "Manutenção"
# Sem a data da compra no Bling: desde as primeiras NFs de produto do DaVinci.
INICIO = datetime(2026, 8, 19, tzinfo=UTC)
ROTULO_ROBO = svc.ROTULO_GARANTIA_AUTOMATICA
# As candidatas vão às fontes em lotes (o asyncpg aceita até 32.767 parâmetros).
_LOTE = 1000


def _plano(texto: str | None) -> str:
    """Minúsculo, sem acento, espaços simples (a pontuação fica: o "?" diz
    que é pergunta)."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    return re.sub(r"\s+", " ", t).strip()


# O PRODUTO (a peça ou o aparelho) — o sujeito do "quebrou", do "parou".
_PARTES = (
    r"celular|aparelho|telefone|smartphone|tela|display|vidro|touch|camera|lente"
    r"|bateria|carregador|carregamento|cabo|conector|fone|microfone|alto\s?falante|som|audio|botao"
    r"|botoes|chip|sinal|wi[\s-]?fi|bluetooth|relogio|smartwatch|pulseira|air\s?fryer|fritadeira"
    r"|panela|cesto|resistencia|ventilador|motor|liquidificador|cafeteira|maquina|aspirador"
    r"|ferro|secador|chapinha|caixa\s+de\s+som|controle|tablet|notebook|mouse|teclado"
    r"|fonte|tomada|plugue|lampada|led|luz"
)
_PECA = rf"(?:produto|{_PARTES})s?"
# Sem "produto": "problema com a separação do produto" é logística.
_PARTE = rf"(?:{_PARTES})s?"
_PEDIDO_DE_GARANTIA = re.compile(
    # Pedir a garantia, com o verbo ("quero acionar a garantia", "como faço pra
    # usar minha garantia", "solicitar garantia", "preciso da garantia").
    r"\b(?:acion\w*|usar|utilizar|ativar|solicit\w*|pedir|abrir|entrar\s+(?:com|na))\s+"
    r"(?:\w+\s+){0,2}?garantia\b"
    r"|\b(?:gostaria|preciso|quero|queria)\s+(?:d[ae]\s+|a\s+|da\s+|o\s+)?garantia\b"
    r"|\bgarantia\s+d[oa]\s+(?:meu|minha)\s+"
    r"|\b(?:pela|minha|na\s+minha)\s+garantia\b"
    r"|\bassistencia\s+tecnica\b|\bmanutencao\b"
)
_NAO = r"(?:nao|n|nn)"
# Logística, pagamento e cadastro: o "problema" deles não é do produto.
_LOGISTICA = re.compile(
    r"\b(?:pedido|entrega|rastreio|rastreamento|envio|encomenda|correios|transportadora"
    r"|pagamento|pix|boleto|reembolso|estorno|nota\s+fiscal|cadastro|endereco|frete|postagem"
    r"|cupom|desconto|preco|valor)\b"
)
_DEFEITO = re.compile(
    r"\bdefeit\w*"
    rf"|\b{_NAO}\s+(?:(?:esta|ta|tah|quer|quis|consegue|mais)\s+){{0,2}}(?:funcion\w*"
    r"|liga\b|ligar\b|ligou|ligando|acende\w*|carreg\w*|reconhec\w*|conect\w*"
    r"|segura\s+(?:a\s+)?carga|aguenta\w*\s+(?:a\s+)?carga|esta\s+aguentando\s+carga"
    r"|segurando\s+(?:a\s+)?(?:carga|bateria)|sustenta\s+(?:a\s+)?carga"
    r"|da\s+imagem|sai\s+(?:o\s+)?som|tem\s+som|gira\b|fecha\b|abre\b"
    r"|pegando\s+(?:mais\s+)?(?:o\s+|a\s+)?(?:sinal|area|aria|rede|chip|wi\s?fi|internet|4g|5g"
    r"|gps|fone))"
    r"|\bnao\s+(?:esquenta|aquece)\w*\s+mais\b"
    r"|\bparo?u?\w?\s+de\s+(?:funcionar|fu\w*ciona\w*|ligar|carregar|pegar|responder|tocar"
    r"|esquentar|aquecer|girar|fritar|conectar)\b"
    rf"|\b{_PECA}\s+(?:\w+\s+){{0,4}}?(?:parou|quebr\w*|trinc\w*|rach\w*|queimou|pifou|estrag\w*"
    r"|estufou|derreteu|apagou|falh\w*|piscando|pisca|travad\w*|travou|travando|chiando"
    r"|embacad\w*|manchad\w*|riscad\w*|escura|bloquead\w*|com\s+(?:problema|defeito|agua)"
    r"|tem\s+(?:um\s+)?problema|trava\b"
    rf"|{_NAO}\s+(?:funciona\w*|liga|carrega|conecta|pega|responde\w*|esquenta|aquece))\b"
    rf"|\bproblema\w*\s+(?:\w+\s+){{0,3}}?(?:na|no|nas|nos|com\s+(?:a|o)|de|do|da)\s+{_PARTE}"
    rf"|\bfalha\w*\s+(?:no|na|do|da)\s+{_PECA}|\bvicio\s+do\s+produto\b"
    r"|\bacende\s+e\s+apaga\b|\bja\s+(?:esta|ta)\s+descarregad\w*"
    r"|\bpar(?:a|ou|o)\s+do\s+nada\b|\bsinal\s+de\s+vida\b|\btrincou\s+(?:toda\s+)?a\s+tela\b"
    r"|\b(?:fica|ta|esta)\s+dando\s+(?:erro|e\d|ei)\b"
    r"|\bfica\s+caindo\s+o\s+(?:wi[\s-]?fi|sinal|internet|chip)\b"
    r"|\bdesligou\s+(?:novamente|de\s+novo|e\s+nao)|\bsimplesmente\s+desligou"
    r"|\bcomecou\s+a\s+desligar|\bdesligava\s+e\s+nao"
    r"|\b(?:chegou|veio)\s+(?:com\s+)?(?:\w+\s+){0,3}?(?:quebrad\w*|trincad\w*|rachad\w*"
    r"|danificad\w*|estragad\w*|riscad\w*|sem\s+funcionar)\b"
    r"|\b(?:esta|ta|estava|tava)\s+(?:\w+\s+){0,2}?(?:quebrad\w*|trincad\w*|rachad\w*|danificad\w*"
    r"|sem\s+funcionar)\b"
    r"|\bapresent\w+\s+(?:\w+\s+){0,3}?(?:linhas?|listras?|manchas?|defeito|problemas?|falhas?"
    r"|erros?|aquecimento)\b"
    r"|\bdeu\s+(?:um\s+|uns\s+|\d+\s+)?(?:problema|defeito|pau|apag\w*)\b"
    r"|\bapagou\s+(?:do\s+nada|sozinh\w*|de\s+vez|e\s+nao)"
    r"|\bdeslig\w*\s+(?:do\s+nada|toda\s+hora|o\s+tempo\s+todo|direto)\b"
    r"|\b(?:fica|ta|esta|tah|so)\s+(?:so\s+)?desligando\b"
    r"|\bcaiu\s+e\s+(?:\w+\s+){0,2}?(?:quebr|trinc)\w*|\bqueda\s+(?:\w+\s+){0,2}?quebr\w*"
    r"|\bquebrou\s+(?:a|o)\s+(?:tela|vidro)\b|\barrum\w*\s+(?:essa|a|minha)\s+tela\b"
    r"|\bburaco\s+na\s+tela\b|\bmex\w+\s+sozinh\w*|\bfecha\s+sozinh\w*"
    r"|\b(?:so|apenas)\s+um\s+lado\s+(?:funciona|toca|sai)"
    r"|\b(?:entrada|conector)\s+(?:\w+\s+){0,10}?(?:folga|froux\w*|mau\s+contato|nao\s+segura"
    r"|esquent\w*|aquec\w*)"
    r"|\bcamera\s+(?:\w+\s+){0,4}?(?:preta|embacad\w*|tremend\w*|desfocad\w*)"
    r"|\b(?:sinal|wi[\s-]?fi|internet|rede|gps)\s+(?:\w+\s+){0,3}?(?:caindo|sumindo|some\b)"
    r"|\bsome\s+o\s+sinal\b|\bsinal\s+fraco\b"
    r"|\b(?:travou|tela\s+(?:\w+\s+){0,2}?(?:preta|apagad\w*|branca|piscando|verde|rosa|escura"
    r"|toda\s+riscad\w*|bugand\w*"
    r"|com\s+(?:linhas?|listras?|manchas?|pontos?|risco))|touch\s+fantasma"
    r"|bateria\s+(?:viciad\w*|estufad\w*|fraca|nao\s+(?:carrega|segura|dura|aguenta|sustenta))"
    r"|queimou|pifou|estufou|derreteu|cheiro\s+de\s+queimado|soltando\s+fumaca|deu\s+curto"
    r"|choque)\b"
)
# O "problema" sem dizer de quê ("ele tá com um problema", "está dando
# problema"): só fora de conversa de logística/pagamento.
_PROBLEMA_GENERICO = re.compile(
    r"\b(?:esta|ta|tah|anda|esta\s+dando|ta\s+dando|dando|veio)\s+(?:com\s+)?(?:um\s+|uns\s+"
    r"|algum\s+|esse\s+|o\s+mesmo\s+)?(?:problema|bug|defeito|erro)\w*"
    r"|\brelatar\s+(?:um\s+)?problema\b|\bbugand\w*"
    r"|\b(?:esse|o)\s+problema\s+(?:e|eh)\s+(?:no|do)\s+(?:aparelho|celular|telefone)\b"
    r"|\bbugs?\b"
)
# O sintoma que também é CARACTERÍSTICA ("trava muito?", "esquenta?", "a
# bateria dura pouco?", "descarrega rápido?"): na pergunta, só conta com a
# marca do que já aconteceu ("o meu tá travando?", "esquenta muito, é normal?").
_SINTOMA = re.compile(
    r"\b(?:travando|trava\s+(?:muito|toda|sozinh|demais|bastante|constantemente|direto)"
    r"|(?:modelo|celular|aparelho|telefone)\s+trava|reinician\w*|reinici\w+\s+sozinh\w*"
    r"|deslig\w+\s+sozinh\w*|esquent\w+\s+(?:muito|demais|de\s+mais|bastante|absurd\w*)"
    r"|superaquec\w*|descarregamento"
    r"|(?<!nao )(?:esquentando|esquentou)|dando\s+esquent\w*|aquecimento|aquecendo\s+muito"
    r"|(?:ficar|ficou|esta|ta|tah|muito)\s+lent[oa]"
    r"|descarreg\w*\s+(?:muito\s+|bem\s+|super\s+|mto\s+)?(?:rapid\w*|depressa|rapidinho)"
    r"|descarreg\w*\s+(?:ate\s+)?sem\s+uso|carregando\s+(?:muito\s+)?(?:lento|devagar)"
    r"|demora(?:ndo)?\s+(?:muito\s+)?(?:a|pra|para)\s+carregar"
    r"|bateria\s+(?:\w+\s+){0,3}?(?:acab\w+\s+(?:rapido|muito)"
    r"|dura(?:ndo)?\s+(?:muito\s+)?(?:pouco|nada)"
    r"|nao\s+(?:\w+\s+)?(?:dura|durando|aguenta|segura|sustenta)\w*|foi\s+caindo|fraca))\b"
)
_JA_ACONTECEU = re.compile(
    r"\b(?:meu|minha|meus|minhas|esta|ta|tah|estava|tava|chegou|veio|comprei|recebi|ficou"
    r"|comecou|agora|hoje|ontem|aqui|depois)\b"
    r"|\b(?:o\s*que\s+(?:acontece|faco|fazer|eu\s+faco|pode\s+ser)|oque\s+(?:acontece|faco)"
    r"|(?:e|eh)\s+normal|tem\s+(?:conserto|solucao|jeito)|como\s+(?:resolvo|arrumo|conserto))"
)
# A conversa sobre o que AINDA não aconteceu ("se der defeito…", "caso
# quebre…", "imagina se vier com defeito", "se ficar sem uso ele desliga?") e
# a pergunta de informação sobre a garantia ("tem garantia?").
_HIPOTESE = re.compile(
    r"\b(?:se|caso|imagina\s+se|e\s+se)\s+(?:\w+\s+){0,6}?(?:der|tiver|apresentar|quebrar"
    r"|parar|vier|estragar|acontecer|ficar|estiver|precisar|nao\s+(?:ligar|funcionar"
    r"|carregar))\b"
    r"|\b(?:quanto\s+tempo|qual\s+(?:a|e\s+a)\s+garantia|tem\s+garantia|seria\s+(?:um\s+)?defeito"
    r"|como\s+funciona\s+a\s+garantia|validade\s+da\s+garantia)\b"
)
# "sem defeito", "defeito nenhum", "não é defeito", "não veio com defeito".
_NEGA = re.compile(
    r"\b(?:sem|nenhum|nenhuma|nao\s+(?:e|eh|era|foi|tem|teve|tinha|deu|apresentou|esta\s+com"
    r"|veio\s+com|vem\s+com))\s+(?:\w+\s+){0,1}?(?:defeit\w*|problema\w*)"
    r"|\b(?:defeit\w*|problema\w*)\s+(?:nenhum|nenhuma|algum)\b"
)
_FRASE = re.compile(r"[^.!?\n]+[.!?]*")


def pede_garantia(texto: str | None) -> bool:
    """A fala do cliente (depois da compra) relata defeito do produto ou pede a
    garantia? Frase a frase: a hipótese e a negação não contam. PURA."""
    for frase in _FRASE.findall(_plano(texto)):
        if _HIPOTESE.search(frase):
            continue
        limpa = _NEGA.sub(" ", frase)
        if _DEFEITO.search(limpa) or _PEDIDO_DE_GARANTIA.search(limpa):
            return True
        pergunta = frase.rstrip().endswith("?")
        if _SINTOMA.search(limpa) and (not pergunta or _JA_ACONTECEU.search(frase)):
            return True
        if _PROBLEMA_GENERICO.search(limpa) and (
            not _LOGISTICA.search(limpa) or re.search(rf"\b{_PECA}\b", limpa)
        ):
            return True
    return False


# ── Os sinais ─────────────────────────────────────────────────────────────


def compra_da_garantia():
    """O dia da compra no Bling (00:00 de São Paulo) da garantia da linha."""
    return (
        select(func.min(BlingOrder.data))
        .where(BlingOrder.numero == Garantia.pedido_bling)
        .correlate(Garantia)
        .scalar_subquery()
    )


async def _por_mensagem(session: AsyncSession, ids: list[int]) -> dict[int, tuple]:
    """A 1ª mensagem do cliente, depois da compra, que pede a garantia — nas
    conversas ligadas (fora as do SAC, que contam pelo formulário). A compra é
    a HORA do pedido no índice do comprador (Shopee/TikTok); sem ela, o dia do
    pedido no Bling (00:00 de São Paulo). Na conversa do comprador (uma para
    todos os pedidos dele — também quando ligou pelo nº do pedido), só até o
    PRÓXIMO pedido dele: dali para baixo pode ser sobre o outro (revisão
    28/10)."""
    if not ids:
        return {}
    g, gc, c, m, p = (
        Garantia,
        GarantiaConversa,
        AtendimentoConversa,
        AtendimentoMensagem,
        AtendimentoPedidoComprador,
    )
    hora_do_pedido = (
        select(func.min(p.criado_em))
        .where(p.pedido == g.pedido_marketplace)
        .correlate(g)
        .scalar_subquery()
    )
    base = (
        select(
            g.id.label("gid"),
            func.coalesce(g.pedido_marketplace, "").label("pedido"),
            func.coalesce(hora_do_pedido, compra_da_garantia(), literal(INICIO)).label("compra"),
        )
        .where(g.id.in_(ids))
        .cte("base")
    )
    quando = func.coalesce(m.enviada_em, m.created_at)
    proximo = (
        select(func.min(p.criado_em))
        .where(
            p.integration_id == c.integration_id,
            p.comprador_id == c.comprador_id,
            p.pedido != base.c.pedido,
            p.criado_em > base.c.compra,
        )
        .correlate(c, base)
        .scalar_subquery()
    )
    q = (
        select(base.c.gid, m.id, m.texto, quando.label("quando"), c.plataforma)
        .select_from(base)
        .join(gc, gc.garantia_id == base.c.gid)
        .join(c, c.id == gc.conversa_id)
        .join(m, m.conversa_id == c.id)
        .where(
            func.coalesce(c.externo_id, "").not_like(PREFIXO_PROTOCOLO + "%"),
            m.autor == "cliente",
            m.tipo == "texto",
            m.texto.is_not(None),
            quando >= base.c.compra,
            or_(proximo.is_(None), quando < proximo),
        )
        .order_by(base.c.gid, quando, m.id)
    )
    achados: dict[int, tuple] = {}
    for gid, mid, texto, em, plataforma in (await session.execute(q)).all():
        if gid in achados or not pede_garantia(texto):
            continue
        achados[gid] = (em, ORIGEM_MENSAGEM, str(mid), f"mensagem do cliente ({plataforma})")
    return achados


async def _por_sac(session: AsyncSession, ids: list[int]) -> dict[int, tuple]:
    """O formulário do SAC do site ligado à garantia (a hora do formulário)."""
    if not ids:
        return {}
    gc, c, m = GarantiaConversa, AtendimentoConversa, AtendimentoMensagem
    primeira = (
        select(func.min(func.coalesce(m.enviada_em, m.created_at)))
        .where(m.conversa_id == c.id)
        .correlate(c)
        .scalar_subquery()
    )
    q = (
        select(gc.garantia_id, func.coalesce(primeira, c.created_at), c.externo_id)
        .join(c, c.id == gc.conversa_id)
        .where(gc.garantia_id.in_(ids), c.externo_id.like(PREFIXO_PROTOCOLO + "%"))
    )
    achados: dict[int, tuple] = {}
    for gid, em, externo in (await session.execute(q)).all():
        protocolo = (externo or "")[len(PREFIXO_PROTOCOLO) :]
        if gid not in achados or em < achados[gid][0]:
            achados[gid] = (em, ORIGEM_SAC, protocolo[:64], f"SAC do site · protocolo {protocolo}")
    return achados


async def _por_devolucao(session: AsyncSession, ids: list[int]) -> dict[int, tuple]:
    """A devolução de defeito (motivo) ou mandada para a manutenção, na hora
    em que foi LANÇADA (`devolutions.data` é a data da venda — revisão 28/10)."""
    if not ids:
        return {}
    d, g = Devolution, Garantia
    q = (
        select(g.id, d.id, d.created_at, d.motivo_devolucao)
        .join(d, d.pedido_bling == g.pedido_bling)
        .where(
            g.id.in_(ids),
            or_(
                d.motivo_devolucao.in_(MOTIVOS_DEFEITO),
                and_(
                    or_(d.manutencao.is_(True), d.condicao_produto == CONDICAO_MANUTENCAO),
                    func.coalesce(d.motivo_devolucao, "") != MOTIVO_BLOQUEADO,
                ),
            ),
        )
    )
    achados: dict[int, tuple] = {}
    for gid, did, em, motivo in (await session.execute(q)).all():
        if gid in achados and em >= achados[gid][0]:
            continue
        o_que = motivo if motivo in MOTIVOS_DEFEITO else "foi para a manutenção"
        achados[gid] = (em, ORIGEM_DEVOLUCAO, str(did), f"devolução: {o_que}")
    return achados


async def _por_atendimento(session: AsyncSession, ids: list[int]) -> dict[int, tuple]:
    if not ids:
        return {}
    a = GarantiaAtendimento
    q = (
        select(a.garantia_id, func.min(a.data_atendimento), func.min(a.id))
        .where(a.garantia_id.in_(ids))
        .group_by(a.garantia_id)
    )
    return {
        gid: (em, ORIGEM_ATENDIMENTO, str(aid), f"atendimento #{aid} registrado")
        for gid, em, aid in (await session.execute(q)).all()
    }


def _candidatas(garantia_ids: list[int] | None, desde: datetime | None):
    """As vivas, sem pedido e que a pessoa não desmarcou; com `desde`, só as
    que ganharam conversa, mensagem, devolução ou atendimento depois dele."""
    g = Garantia
    conds = [
        g.descartada_em.is_(None),
        g.solicitada_em.is_(None),
        g.nao_solicitada_em.is_(None),
    ]
    if garantia_ids is not None:
        conds.append(g.id.in_(garantia_ids))
    if desde is not None:
        gc, c, d, a = GarantiaConversa, AtendimentoConversa, Devolution, GarantiaAtendimento
        conds.append(
            or_(
                g.criado_em >= desde,
                select(gc.garantia_id)
                .join(c, c.id == gc.conversa_id)
                .where(
                    gc.garantia_id == g.id,
                    or_(gc.ligada_em >= desde, c.ultima_mensagem_em >= desde),
                )
                .exists(),
                select(d.id)
                .where(
                    d.pedido_bling == g.pedido_bling,
                    or_(d.created_at >= desde, d.updated_at >= desde),
                )
                .exists(),
                select(a.id).where(a.garantia_id == g.id, a.criado_em >= desde).exists(),
            )
        )
    return select(g.id, g.origem, g.criado_em).where(and_(*conds))


async def marcar_pedidos(
    session: AsyncSession,
    *,
    garantia_ids: list[int] | None = None,
    desde: datetime | None = None,
) -> int:
    """Marca o pedido de garantia (o sinal mais antigo) nas garantias ainda sem
    pedido. `desde` = a volta de 15 min; sem ele, todas (o diário e o
    retroativo). NÃO commita. Devolve quantas marcou."""
    linhas = (await session.execute(_candidatas(garantia_ids, desde))).all()
    if not linhas:
        return 0
    ids = [r.id for r in linhas]
    sinais: dict[int, list[tuple]] = {}
    for i in range(0, len(ids), _LOTE):
        lote = ids[i : i + _LOTE]
        for fonte in (_por_mensagem, _por_sac, _por_devolucao, _por_atendimento):
            for gid, sinal in (await fonte(session, lote)).items():
                sinais.setdefault(gid, []).append(sinal)
    for r in linhas:
        if r.origem == ORIGEM_MANUAL:
            sinais.setdefault(r.id, []).append(
                (r.criado_em, ORIGEM_MANUAL, None, "cadastrada à mão")
            )
    marcadas = 0
    for gid, lista in sinais.items():
        em, origem, ref, detalhe = min(lista, key=lambda s: s[0])
        feito = await session.execute(
            update(Garantia)
            .where(Garantia.id == gid, Garantia.solicitada_em.is_(None))
            .values(solicitada_em=em, solicitacao_origem=origem, solicitacao_ref=ref)
        )
        if not feito.rowcount:
            continue
        marcadas += 1
        svc.registrar_log(
            session,
            gid,
            "pedido_automatico",
            None,
            detalhe=f"pedido de garantia: {detalhe} em "
            f"{svc._br(em.astimezone(svc.SAO_PAULO).date())}",
            robo=ROTULO_ROBO,
        )
    return marcadas


def filtro_pedido():
    """A garantia que entra no Painel e no Resumo: com pedido e sem o "não é
    pedido de garantia" da pessoa."""
    return and_(Garantia.solicitada_em.is_not(None), Garantia.nao_solicitada_em.is_(None))


def marcar_pela_pessoa(g: Garantia, quando: datetime, origem: str = ORIGEM_PESSOA) -> None:
    """Marca o pedido na hora (a pessoa, o cadastro à mão, o atendimento
    registrado) e desfaz o "não é pedido de garantia" de antes. Se já tinha
    pedido, fica o de antes (o mais antigo)."""
    if g.solicitada_em is None:
        g.solicitada_em = quando
        g.solicitacao_origem = origem
        g.solicitacao_ref = None
    g.nao_solicitada_em = None
    g.nao_solicitada_por = None
