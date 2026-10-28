"""A NF pelo agente, SOZINHA, nas lojas no automático (28/10/2026).

O Eduardo, ao ver o anexo da NF pela tela: "liga sim fechou so para lojas
automaticas ele consegue enviar por ele mesmo a nf na conversa". As condições
aprovadas, TODAS juntas (qualquer uma que falte → fica com a pessoa, com o
"Anexar a NF" do cartão da sugestão, como antes):

1. Qualquer canal em modo `auto` com o agente ligado — sem lista de lojas
   (complemento do Eduardo: "se eu ativar outra loja ela já deve poder enviar
   a nf automaticamente também") —, nas caixas que recebem o anexo (`CAIXAS`:
   chat da Shopee e do TikTok como imagem, pós-venda do ML como PDF; Amazon e
   Magalu fora até terem envio de anexo) e com o assunto `nota_fiscal` MARCADO
   na loja, como todo assunto do automático (`auto_categorias` e
   `auto_agente` do canal — é o que marcar ao ligar uma loja nova).
2. O cliente pediu a NF de forma CLARA (`pedido_claro`): verbo de pedido ou
   "não veio a nota", sem CNPJ/XML/correção/reemissão, sem outro assunto no
   turno (o rascunho do agente só com a NF: `elegivel`). Ambíguo → pessoa.
3. A nota CERTA (`conferir_nf`): a cheia, autorizada, de saída normal, do
   pedido DESTA conversa (o `numeroloja` do Bling É o pedido da conversa), o
   pedido é DESTE comprador — conferido AO VIVO na plataforma na hora do envio
   (`pedido_na_plataforma`: Shopee `buyer_user_id`, TikTok `user_id` = o
   comprador do chat; no ML a conversa pós-venda É a do pacote do pedido) e,
   na Shopee, também no índice de pedidos —, o destinatário da NF é o do
   pedido (CPF/CNPJ) e a chave bate com o número, o emitente e o XML. LGPD: a
   DANFE tem nome, endereço e CPF — nunca a nota de outro cliente.
4. Pedido cancelado, carrinho com vários pedidos, só a NF de embalagem (a
   cheia sai no lote da noite) e comprador com MAIS DE UM pedido (na loja, pelo
   índice da Shopee, ou nos cartões de pedido da conversa: qual NF ele quer?)
   → pessoa.
5. As travas do envio sozinho: o automático ligado, o modo `auto`, o agente
   ligado no canal, a conversa aguardando (não pausada, atribuída, fechada,
   bloqueada, sem reclamação), a pausa humana de 30 min, a triagem da Caixa
   Humano (só a ação "Enviar o arquivo da NF" é liberada — qualquer outro
   motivo segura) e, no envio, a trava da conversa e o gatilho ainda sem
   resposta (`enviar.enviar_nf_automatica`). O verificador semântico NÃO
   roda aqui: ele confere o TEXTO que o modelo escreveu, e o que sai é a frase
   fixa `LEGENDA` com o documento que o CÓDIGO escolheu — as conferências
   acima fazem o papel dele, todas determinísticas.
6. Na Shopee e no TikTok a NF vai como IMAGEM da DANFE (o chat não aceita
   arquivo) e, depois, a frase `LEGENDA`; no pós-venda do ML, o PDF junto da
   frase (o ML manda o anexo dentro de uma mensagem com texto).

NUNCA DUAS VEZES na mesma conversa: a mensagem leva `payload.nf.chave`; antes
de sair (e de novo sob a trava da conversa) qualquer mensagem da conversa com
a mesma chave ou a mesma imagem (a DANFE é determinística: o mesmo XML dá os
mesmos bytes, inclusive a mandada pela pessoa no botão "NF") segura.

Chave geral: `ATENDIMENTO_NF_AUTOMATICA` (padrão ligado; desligada, tudo fica
com a pessoa). Só ids e códigos no log, nunca dado do cliente.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoRascunho,
    BlingOrder,
    NfNota,
)
from app.services.atendimento import constantes, danfe, validador

logger = structlog.get_logger()

LEGENDA = "Segue a nota fiscal do seu pedido."
CATEGORIA = "nota_fiscal"
# As caixas que recebem o anexo (`foto.CAIXAS_COM_FOTO`, sem o Zap) e onde o
# comprador do pedido é conferido (`pedido_na_plataforma`).
CAIXAS = frozenset(
    {
        ("shopee", constantes.CANAL_CHAT),
        ("tiktok", constantes.CANAL_CHAT),
        ("ml", constantes.CANAL_POS_VENDA),
    }
)
# Pedidos do mesmo comprador na loja nesta janela: mais de um = qual NF? → pessoa.
JANELA_PEDIDOS = timedelta(days=60)
# O motivo que o agente grava no `sem_auto` para a ação da NF (o único aceito).
MOTIVO_SEM_AUTO_NF = (
    f"ação de pessoa: {constantes.rotulo_acao_pessoa(constantes.ACAO_ENVIAR_ARQUIVO_NF)}"
)


class NaoSai(Exception):  # noqa: N818 — nome do domínio
    """A NF não sai sozinha; `code` vai para o registro do automático (`nf:<code>`)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ── O pedido claro (PURA) ─────────────────────────────────────────────────

_DOC = r"(?:nota(?:\s+fiscal)?|nf-?e?|nfe)"
_PEDE = re.compile(
    # "me manda a nota", "pode enviar a nf?", "passa a nota fiscal"
    r"\b(?:manda|mande|mandar|mandem|envia|envie|enviar|enviem|passa|passe|passar)\b"
    r"(?:\s+\w+){0,3}?\s+(?:a\s+|minha\s+|sua\s+)?" + _DOC + r"\b"
    # "preciso da nota fiscal", "queria a nf do pedido"
    r"|\b(?:preciso|precisava|precisaria|quero|queria|gostaria|necessito)\b"
    r"(?:\s+\w+){0,3}?\s+(?:a\s+|da\s+|de\s+|uma\s+)?" + _DOC + r"\b"
    # "não veio a nota", "a nota fiscal não chegou", "cadê a nf"
    r"|\bnao\s+(?:veio|vieram|recebi|chegou|encontrei|achei|tinha|tem|estava)\b(?:\s+\w+){0,4}?\s+"
    r"(?:a\s+)?" + _DOC + r"\b"
    r"|\b" + _DOC + r"\b(?:\s+\w+){0,3}?\s+nao\s+(?:veio|chegou|estava|tinha|foi)\b"
    r"|\bcade\s+(?:a\s+)?" + _DOC + r"\b"
)
# Pedido que NÃO é "mande a NF do pedido": CNPJ, XML, correção, reemissão,
# série/IMEI, nota de outro, ou outro assunto colado (dinheiro, devolução).
_NAO_E_SO_A_NF = re.compile(
    r"\b(?:cnpj|inscricao\s+estadual|razao\s+social|empresa|xml|danfe|"
    r"corrig\w*|correc\w*|reemit\w*|reemiss\w*|errad\w*|incorret\w*|alter\w*|troc\w*|"
    r"outro\s+nome|outro\s+cpf|outra\s+pessoa|serie|imei|"
    r"cancel\w*|devol\w*|reembols\w*|estorn\w*|defeito|quebrad\w*|reclam\w*|procon|"
    r"garantia|mandam|enviam|emitem|emite|acompanha\w*|vem\s+com)\b"
    r"|\bja\s+(?:recebi|tenho|veio|chegou)\s+(?:a\s+)?" + _DOC + r"\b"
    r"|\bnao\s+precis\w*\b"
)


# Revisão de 28/10: a régua é a LISTA DO QUE PODE — tirando o pedido da NF e a
# cortesia, nada pode sobrar ("no meu cpf", "no nome da minha esposa", "do
# outro pedido", "nem o produto", "faltando o cabo" vão para a pessoa). Número
# (CPF, pedido) também não: a palavra com dígito não está na lista.
_PALAVRAS_DO_PEDIDO = frozenset(
    """
    oi ola ei bom boa dia tarde noite tudo bem ok certo sim por favor pf pfv porfavor
    obrigado obrigada obg grato grata agradeco desde ja
    pode poderia podem poderiam consegue conseguem conseguiria tem como teria seria possivel
    voce voces vc vcs me mim pra para pro por aqui ai la
    manda mande mandar mandem envia envie enviar enviem passa passe passar
    preciso precisava precisaria quero queria gostaria necessito
    nao veio vieram recebi chegou encontrei achei tinha estava foi cade ainda
    nota notas fiscal nf nfe copia arquivo pdf digital via segunda de novo
    a o as os um uma da do de na no em e minha meu sua seu desse deste dessa desta
    esse este essa esta junto caixa pacote embalagem pedido compra produto
    """.split()
)


def pedido_claro(textos: Sequence[str | None]) -> bool:
    """O turno do cliente PEDE a NF do pedido, e só isso? PURA."""
    plano = " ".join(validador.plano_de(" ".join(t for t in textos if t)).split())
    if not plano or len(plano) > 400:
        return False
    if not _PEDE.search(plano) or _NAO_E_SO_A_NF.search(plano):
        return False
    palavras = re.findall(r"[a-z0-9]+", plano)
    return all(p in _PALAVRAS_DO_PEDIDO for p in palavras)


# ── O rascunho do agente só com a NF (PURA) ───────────────────────────────


def _agente(rascunho: Any) -> dict:
    fatos = getattr(rascunho, "fatos", None)
    agente = fatos.get("agente") if isinstance(fatos, dict) else None
    return agente if isinstance(agente, dict) else {}


def candidato(rascunho: Any) -> bool:
    """O rascunho do agente pede pessoa SÓ pela ação "Enviar o arquivo da NF"? PURA.

    É o que leva o `ia._talvez_enviar` a este caminho (os outros seguem o de
    sempre); a decisão de verdade é a `motivo_do_rascunho` + as travas.
    """
    from app.services.atendimento import ia

    return ia.rascunho_do_agente(rascunho) and ia.acoes_pessoa_do_rascunho(rascunho) == [
        constantes.ACAO_ENVIAR_ARQUIVO_NF
    ]


def motivo_do_rascunho(rascunho: Any) -> str | None:
    """Por que o rascunho não serve para a NF sozinha (None = serve). PURA.

    Só a NF no turno: o assunto `nota_fiscal`, gerado no automático, sem
    aviso, sem pista de outro assunto, sem checagem pendente, e o `sem_auto`
    só com a ação da NF.
    """
    if not candidato(rascunho):
        return "outra_acao"
    if getattr(rascunho, "categoria", None) != CATEGORIA:
        return "assunto"
    agente = _agente(rascunho)
    if agente.get("modo") not in (None, constantes.MODO_AUTO):
        return "gerado_no_copiloto"
    for chave in ("avisos_auto", "pistas_alem", "pistas_orientacao", "checagens"):
        if agente.get(chave):
            return chave
    if any(p != CATEGORIA for p in agente.get("pistas_turno") or []):
        return "outro_assunto"
    if list(agente.get("sem_auto") or []) != [MOTIVO_SEM_AUTO_NF]:
        return "sem_auto"
    return None


# ── A nota certa, do comprador certo ──────────────────────────────────────


def _cpf_da_nf(xml: bytes) -> str:
    nota = danfe.ler_nota(xml)
    return "".join(c for c in nota.dest["doc"] if c.isdigit())


def conferir_xml(xml: bytes, nota: Any) -> str | None:
    """A chave da linha = a do XML, e a chave bate com o número e o emitente. PURA.

    A chave de acesso: cUF(2) AAMM(4) CNPJ(14) mod(2) série(3) nNF(9) …
    """
    try:
        lida = danfe.ler_nota(xml)
    except danfe.DanfeInvalida:
        return "xml_invalido"
    chave = str(nota.chave or "")
    if lida.chave != chave or len(chave) != 44:
        return "chave_do_xml"
    if int(chave[25:34]) != int("".join(c for c in str(nota.numero) if c.isdigit()) or -1):
        return "chave_numero"
    cnpj = "".join(c for c in str(nota.emitente_cnpj or "") if c.isdigit())
    if cnpj and chave[6:20] != cnpj.rjust(14, "0"):
        return "chave_emitente"
    if lida.tp_amb != "1" or not lida.protocolo:
        return "nao_autorizada"
    return None


async def pedidos_recentes_do_comprador(
    session: AsyncSession, conversa: AtendimentoConversa, agora: datetime
) -> int:
    """Quantos pedidos este comprador tem nesta loja na janela (o índice da Shopee)."""
    return int(
        await session.scalar(
            select(func.count(func.distinct(AtendimentoPedidoComprador.pedido))).where(
                AtendimentoPedidoComprador.integration_id == conversa.integration_id,
                AtendimentoPedidoComprador.comprador_id == str(conversa.comprador_id or ""),
                or_(
                    AtendimentoPedidoComprador.criado_em.is_(None),
                    AtendimentoPedidoComprador.criado_em >= agora - JANELA_PEDIDOS,
                ),
            )
        )
        or 0
    )


async def conferir_nf(
    session: AsyncSession, conversa: AtendimentoConversa, agora: datetime | None = None
) -> tuple[danfe.NfDoPedido, bytes]:
    """A nota cheia do pedido DESTA conversa e DESTE comprador, com o XML. Levanta `NaoSai`."""
    agora = agora or datetime.now(UTC)
    pedido_mkt = str(conversa.pedido_marketplace or "").strip()
    comprador = str(conversa.comprador_id or "").strip()
    if not pedido_mkt or not comprador or conversa.integration_id is None:
        raise NaoSai("sem_pedido_ou_comprador")
    if conversa.plataforma == "shopee":
        # O índice da Shopee (`buyer_user_id` = o `to_id` do chat): o pedido de
        # outro comprador, ou mais de um pedido dele na loja, seguram já aqui.
        dono = set(
            (
                await session.execute(
                    select(AtendimentoPedidoComprador.comprador_id).where(
                        AtendimentoPedidoComprador.integration_id == conversa.integration_id,
                        AtendimentoPedidoComprador.pedido == pedido_mkt,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not dono:
            raise NaoSai("comprador_nao_conferido")
        if dono != {comprador}:
            raise NaoSai("pedido_de_outro_comprador")
        if await pedidos_recentes_do_comprador(session, conversa, agora) > 1:
            raise NaoSai("varios_pedidos_do_comprador")
    if await pedidos_nos_cartoes(session, conversa, agora) - _ids_do_pedido(conversa):
        raise NaoSai("varios_pedidos_do_comprador")
    try:
        nf = await danfe.nf_da_conversa(session, conversa)
    except danfe.NfIndisponivel as e:
        raise NaoSai(e.code) from e
    if motivo := await _nota_unica(session, nf):
        raise NaoSai(motivo)
    # O pedido do Bling achado É o da conversa (nada de chave reserva).
    linhas = (
        await session.execute(
            select(BlingOrder.numeroloja, BlingOrder.documento_destinatario).where(
                BlingOrder.numero == nf.pedido_bling
            )
        )
    ).all()
    if not linhas or not {str(n or "").strip() for n, _ in linhas} <= _ids_do_pedido(conversa):
        raise NaoSai("pedido_do_bling_diferente")
    docs = {"".join(c for c in str(d or "") if c.isdigit()) for _, d in linhas}
    if len(docs) != 1 or not next(iter(docs)):
        raise NaoSai("pedido_sem_documento")
    nota = await session.get(NfNota, nf.id)
    if nota is None or nota.pedido_bling != nf.pedido_bling:
        raise NaoSai("nf_sumiu")
    xml = bytes(nota.xml or b"")
    if motivo := conferir_xml(xml, nota):
        raise NaoSai(motivo)
    if _cpf_da_nf(xml) != next(iter(docs)):
        raise NaoSai("destinatario_diferente")
    # Por último (é ida à plataforma): o pedido é deste comprador e não foi
    # cancelado, ao vivo.
    dono, status = await pedido_na_plataforma(session, conversa)
    if dono != comprador:
        raise NaoSai("comprador_diferente_na_plataforma")
    if str(status or "").upper() in STATUS_CANCELADOS.get(conversa.plataforma, frozenset()):
        raise NaoSai("pedido_cancelado_na_plataforma")
    return nf, xml


# O pedido cancelado (ou voltando) na plataforma, que o Bling ainda não viu.
STATUS_CANCELADOS: dict[str, frozenset[str]] = {
    "shopee": frozenset({"CANCELLED", "IN_CANCEL", "TO_RETURN"}),
    "tiktok": frozenset({"CANCELLED"}),
}
# A nota cheia não passa disto do total do pedido (frete/arredondamento).
TETO_DO_TOTAL = Decimal("1.05")


async def _nota_unica(session: AsyncSession, nf: danfe.NfDoPedido) -> str | None:
    """Revisão de 28/10 (A1): a NF escolhida é a ÚNICA cheia do pedido, não passa do
    total e não é de antes do pedido — a NF de outro pedido do mesmo CPF (ligada
    antes de o pedido dele chegar ao espelho) ou a reemitida (o `nf_nota` não sabe
    do cancelamento) ficam com a pessoa."""
    pedido = (
        await session.execute(
            select(func.max(BlingOrder.total), func.min(BlingOrder.data)).where(
                BlingOrder.numero == nf.pedido_bling
            )
        )
    ).one()
    total, data_pedido = pedido
    notas = (
        await session.execute(
            select(NfNota.valor, NfNota.data_emissao, NfNota.xml).where(
                NfNota.pedido_bling == nf.pedido_bling,
                NfNota.situacao == danfe.SITUACAO_AUTORIZADA,
            )
        )
    ).all()
    cheias = [n for n in notas if danfe._eh_cheia(n.valor, total) and danfe._saida_normal(n.xml)]
    if len(cheias) != 1:
        return "mais_de_uma_nf_cheia"
    valor = Decimal(str(cheias[0].valor or 0))
    if total is None or valor > Decimal(str(total)) * TETO_DO_TOTAL:
        return "nf_acima_do_pedido"
    emitida = cheias[0].data_emissao
    if data_pedido is None or emitida is None or _dia(emitida) < _dia(data_pedido):
        return "nf_antes_do_pedido"
    return None


def _dia(quando: datetime):
    from zoneinfo import ZoneInfo

    return quando.astimezone(ZoneInfo("America/Sao_Paulo")).date()


def _ids_do_pedido(conversa: AtendimentoConversa) -> set[str]:
    """Os números com que o pedido DESTA conversa pode estar no `numeroloja` do Bling.

    Shopee/TikTok: o pedido da conversa. ML pós-venda: a conversa É o pacote
    (`externo_id`); o Bling guarda o pack ou o order (`dados.pack_id`/`order_id`).
    """
    ids = {str(conversa.pedido_marketplace or "").strip()}
    if conversa.plataforma == "ml" and conversa.canal == constantes.CANAL_POS_VENDA:
        dados = conversa.dados if isinstance(conversa.dados, dict) else {}
        ids |= {
            str(v).strip()
            for v in (conversa.externo_id, dados.get("pack_id"), dados.get("order_id"))
            if v
        }
    return {i for i in ids if i}


async def pedidos_nos_cartoes(
    session: AsyncSession, conversa: AtendimentoConversa, agora: datetime
) -> set[str]:
    """Os pedidos dos cartões de pedido da conversa na janela (de quais o comprador falou)."""
    linhas = (
        (
            await session.execute(
                select(AtendimentoMensagem.anexos).where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.tipo == "pedido",
                    AtendimentoMensagem.created_at >= agora - JANELA_PEDIDOS,
                )
            )
        )
        .scalars()
        .all()
    )
    pedidos: set[str] = set()
    for anexos in linhas:
        for a in anexos or []:
            if isinstance(a, dict) and a.get("tipo") == "pedido" and a.get("pedido"):
                pedidos.add(str(a["pedido"]).strip())
    return pedidos


async def pedido_na_plataforma(
    session: AsyncSession, conversa: AtendimentoConversa
) -> tuple[str | None, str | None]:
    """(comprador, status) do pedido da conversa NA PLATAFORMA, agora (None = não deu).

    Shopee: `get_order_detail` com `buyer_user_id` (o `to_id` do chat) e o
    `order_status`. TikTok: o `user_id` do pedido (o mesmo do participante
    BUYER do chat) e o `status`. ML pós-venda: a conversa é a do pacote do
    pedido — o comprador é o dela (o ML bloqueia a mensagem do cancelado).
    """
    from app.models import Integration
    from app.services.atendimento import clientes

    if conversa.plataforma == "ml" and conversa.canal == constantes.CANAL_POS_VENDA:
        return str(conversa.comprador_id or "").strip() or None, None
    integration = await session.get(Integration, conversa.integration_id)
    if integration is None:
        return None, None
    pedido = str(conversa.pedido_marketplace or "").strip()
    try:
        cliente = await clientes.cliente_da_integracao(integration)
        if conversa.plataforma == "shopee":
            o = await cliente.get_order_buyer(pedido) or {}
            return str(o.get("buyer_user_id") or "").strip() or None, o.get("order_status")
        if conversa.plataforma == "tiktok":
            o = await cliente.get_order_detail(pedido) or {}
            return str(o.get("user_id") or "").strip() or None, o.get("status")
    except Exception as e:  # noqa: BLE001 — sem saber, não sai
        logger.info(
            "atendimento_nf_auto_comprador_indisponivel",
            conversa_id=str(conversa.id),
            erro=type(e).__name__,
        )
    return None, None


async def ja_enviada(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    chave: str,
    sha256: str,
    numero: str | None = None,
) -> bool:
    """Esta NF já saiu ou está saindo nesta conversa? Pela chave, pela mesma imagem/PDF
    ou (revisão de 28/10, B1: o desenho pode mudar num deploy) pelo NOME do anexo que
    o botão "NF" dá (`NF_<número>.pdf`, `.jpg` na conversa que não aceita PDF)."""
    achada = await session.scalar(
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.status != constantes.MSG_FALHOU,
            or_(
                AtendimentoMensagem.payload["nf"]["chave"].astext == chave,
                AtendimentoMensagem.payload["foto"]["sha256"].astext == sha256,
                *(
                    AtendimentoMensagem.anexos.contains([{"nome": f"NF_{numero}.{ext}"}])
                    for ext in ("jpg", "pdf")
                    if numero
                ),
            ),
        )
        .limit(1)
    )
    return achada is not None


# ── O caminho (chamado pelo `ia._talvez_enviar`) ──────────────────────────


async def _travas_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, rascunho: AtendimentoRascunho
) -> str | None:
    """As travas do envio sozinho que não dependem da NF (as do `ia._talvez_enviar`)."""
    from app.services.atendimento import agente_config, ia

    s = get_settings()
    if not s.atendimento_auto_ativo:
        return "auto_desligado"
    if not s.atendimento_nf_automatica:
        return "nf_automatica_desligada"
    if rascunho.status != constantes.RASCUNHO_PENDENTE:
        return "rascunho_nao_pendente"
    if (rascunho.fatos or {}).get(ia.FATO_PEDIDA_PELA_TELA):
        return "pedida_pela_tela"
    if motivo := motivo_do_rascunho(rascunho):
        return motivo
    if not s.atendimento_agente_verificador:
        # Revisão de 28/10: com o verificador desligado, NENHUM rascunho do agente sai
        # sozinho — a NF também não.
        return "verificador_desligado"
    if (rascunho.confianca or 0.0) < ia.confianca_minima(True):
        return "confianca_baixa"
    await session.refresh(conversa)
    if (conversa.plataforma, conversa.canal) not in CAIXAS:
        return "caixa_sem_nf_automatica"
    if not conversa.aguardando_resposta:
        return "ja_respondida"
    if conversa.ia_pausada:
        return "ia_pausada"
    if conversa.atribuido_a is not None:
        return "atribuida"
    if conversa.sem_resposta_necessaria:
        return "sem_resposta_necessaria"
    if conversa.situacao in (constantes.CONVERSA_FECHADA, constantes.CONVERSA_BLOQUEADA):
        return "conversa_fechada_ou_bloqueada"
    if constantes.reclamacao_aberta(conversa.dados):
        return "reclamacao_aberta"
    if conversa.plataforma in constantes.PLATAFORMAS_SEM_AUTO:
        return "plataforma_sem_auto"
    if constantes.motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados):
        return "canal_sem_envio"
    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id, populate_existing=True)
        if conversa.canal_id is not None
        else None
    )
    if canal is None or canal.modo != constantes.MODO_AUTO:
        return "canal_fora_do_auto"
    if not agente_config.agente_ligado(s, canal):
        return "agente_desligado"
    # A régua dos assuntos do automático: o `nota_fiscal` marcado na loja.
    if CATEGORIA not in (canal.auto_categorias or []):
        return "assunto_nao_liberado"
    if CATEGORIA not in (canal.auto_agente or []):
        return "assunto_nao_liberado_agente"
    from app.services.atendimento import manual as manual_svc

    if CATEGORIA in manual_svc.ids_so_humano(await manual_svc.categorias_ativas(session)):
        return "assunto_so_humano"
    if ia.andamento_no_rascunho(rascunho, ia._etiquetas_da_conversa(conversa)):
        return "em_andamento"
    # O pedido claro, pelo turno do cliente que o rascunho responde.
    recentes = await ia._mensagens_recentes(session, conversa)
    gatilho = (
        await session.get(AtendimentoMensagem, rascunho.mensagem_gatilho_id)
        if rascunho.mensagem_gatilho_id is not None
        else None
    )
    if gatilho is None:
        return "sem_gatilho"
    ultima = await ia._ultima_do_cliente(session, conversa)
    if ultima is None or not ia._cobre(rascunho, ultima):
        # O cliente escreveu de novo depois do turno que este rascunho responde.
        return "turno_mudou"
    textos = [m.texto for m in ia.rajada_do_cliente(recentes, gatilho)]
    if not pedido_claro(textos):
        return "pedido_nao_claro"
    if await ia._motivos_humano_para_auto(session, conversa, rascunho, nf_automatica=True):
        return "caixa_humano"
    if await ia._pausa_humana_ate(session, conversa) is not None:
        return "pausa_humana"
    return None


async def talvez_enviar(
    session: AsyncSession, conversa: AtendimentoConversa, rascunho: AtendimentoRascunho
) -> bool:
    """Confere tudo e manda a NF + a frase. True = tentou o envio. Nunca levanta.

    Não sai → registra `nf:<motivo>` (o rascunho fica para a pessoa, com o
    "Anexar a NF" no cartão da sugestão).
    """
    from app.services.atendimento import enviar, foto, ia

    tentou = False
    try:
        try:
            motivo = await _travas_da_conversa(session, conversa, rascunho)
            if motivo is not None:
                raise NaoSai(motivo)
            nf, xml = await conferir_nf(session, conversa)
            pdf = await asyncio.to_thread(danfe.gerar_pdf, xml)
            anexo = await asyncio.to_thread(
                foto.validar_para_a_caixa, conversa, pdf, nf.nome_arquivo, foto.PDF
            )
            chave = danfe.ler_nota(xml).chave
            if await ja_enviada(
                session, conversa, chave=chave, sha256=anexo.sha256, numero=nf.numero
            ):
                raise NaoSai("nf_ja_enviada")
        except NaoSai as e:
            await ia._registrar_auto_envio(session, conversa, rascunho, f"nf:{e.code}")
            logger.info(
                "atendimento_nf_auto_segurada",
                conversa_id=str(conversa.id),
                rascunho_id=str(rascunho.id),
                motivo=e.code,
            )
            return False
        tentou = True
        try:
            imagem, texto = await enviar.enviar_nf_automatica(
                session,
                conversa,
                anexo,
                rascunho=rascunho,
                chave=chave,
                numero=nf.numero,
                legenda=LEGENDA,
            )
        except enviar.EnvioRecusado as e:
            await ia._registrar_auto_envio(session, conversa, rascunho, f"nf:recusado:{e.code}")
            logger.info(
                "atendimento_nf_auto_recusada",
                conversa_id=str(conversa.id),
                rascunho_id=str(rascunho.id),
                code=e.code,
            )
            return True
        status = "enviado_nf" if imagem.status == constantes.MSG_ENVIADA else f"nf:{imagem.status}"
        await ia._registrar_auto_envio(session, conversa, rascunho, status)
        logger.info(
            "atendimento_nf_auto_enviada",
            conversa_id=str(conversa.id),
            rascunho_id=str(rascunho.id),
            nf_id=nf.id,
            imagem=imagem.status,
            texto=texto.status if texto is not None else None,
        )
        return True
    except Exception as e:  # noqa: BLE001 — a sugestão está salva; fica com a pessoa
        logger.warning(
            "atendimento_nf_auto_falhou",
            conversa_id=str(conversa.id),
            rascunho_id=str(rascunho.id),
            err=type(e).__name__,
        )
        await ia._desfazer(session, conversa, rascunho)
        try:
            await ia._registrar_auto_envio(session, conversa, rascunho, "nf:falha_interna")
        except Exception:  # noqa: BLE001
            await ia._desfazer(session, conversa, rascunho)
        return tentou
