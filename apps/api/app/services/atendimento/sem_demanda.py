"""Processamento local de cartão sem pergunta já acolhido pelo robô da Kia.

Nenhum texto é gerado ou enviado. A regra exige autorização literal do
Manual e a conversa continua reabrindo quando chega nova fala do cliente.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
)
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    MODO_AUTO,
    MSG_FALHOU,
    ORIGEM_AUTO,
    ORIGEM_NOTA,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
    RASCUNHO_SUBSTITUIDO,
    e_cartao_do_comprador,
    e_mensagem_automatica,
)
from app.services.atendimento.pendente import resposta_de_pessoa

REGRA_QUANDO = "Kia: cartão de produto ou pedido sem dúvida, já acolhido pelo robô."
REGRA_FACA = (
    "Somente na loja Kia: quando o cliente enviou apenas cartão de produto ou pedido, "
    "não escreveu dúvida e o robô da loja já enviou uma mensagem automática depois do "
    "cartão, marcar como processado no DaVinci, sem enviar nova mensagem. Não confundir "
    "cartão com foto, vídeo, áudio ou arquivo. Nunca encerrar pergunta pendente, "
    "solicitação de pessoa, reclamação ou conversa pausada/atribuída. Histórico "
    "incompleto exige pessoa. Figurinha estruturada muito antiga, anterior ao cartão "
    "por sete dias ou mais, não cria dúvida sobre o produto novo. Nova mensagem do "
    "cliente reabre a conversa. Opção dois do menu, sem pergunta nem pedido identificado: "
    "informar apenas a política aprovada de que o pedido acompanha nota fiscal. Seleção "
    "ambígua de dois e cinco: informar a mesma política e pedir que detalhe a dúvida, "
    "sem inventar pedido de arquivo nem afirmar cobertura ou prazo de garantia."
)
CHAVE_AUDITORIA = "processamento_sem_demanda"
MOTIVO = "cartao_sem_pergunta_ja_acolhido"
INTERVALO_FIGURINHA_ANTIGA = timedelta(days=7)
MOTIVO_CARD_ANTIGO = "cliente mandou só foto/vídeo/arquivo — a IA não vê anexo"


def resposta_de_menu(conversa: Any, mensagens: Sequence[Any], gatilho: Any, regras: list[Any]):
    """A seleção de assunto não é pedido de documento nem confirmação de garantia."""
    from app.services.atendimento import ia, politicas_kia
    from app.services.atendimento.politicas_kia import RespostaKia
    from app.services.atendimento.validador import plano_de

    if (
        str(conversa.integration_id) != politicas_kia.INTEGRATION_KIA
        or conversa.plataforma != "shopee"
        or conversa.canal != "chat"
        or conversa.pedido_marketplace
        or getattr(mensagens, "turno_incompleto", False)
    ):
        return None
    autorizacoes = [r for r in regras if regra_aprovada(r)]
    if (
        len(autorizacoes) != 1
        or not ia._regra_basica_igual(regras, "nota_fiscal", ia._REGRA_NF_APROVADA)
        or not ia._regra_basica_igual(regras, "saudacao_ou_vaga", ia._REGRA_CARTAO_APROVADA)
    ):
        return None
    selecao = "".join(plano_de(gatilho.texto or "").split())
    if selecao not in {"2", "dois", "2i5", "2e5", "2,5", "2/5"}:
        return None
    for m in ia.rajada_do_cliente(mensagens, gatilho):
        if e_cartao_do_comprador(getattr(m, "payload", None)) and not (m.texto or "").strip():
            continue
        texto = "".join(plano_de(m.texto or "").split())
        if not texto or not (
            texto in {"2", "dois", "2i5", "2e5", "2,5", "2/5"}
            or ia.mensagem_de_continuidade(m.texto)
        ):
            return None
    menus = [
        m
        for m in mensagens
        if m.autor == AUTOR_LOJA
        and m.status != MSG_FALHOU
        and _momento(m) is not None
        and _momento(gatilho) is not None
        and _momento(m) <= _momento(gatilho)
        and "2 - nota fiscal" in plano_de(m.texto or "")
        and "5 - garantia" in plano_de(m.texto or "")
        and e_mensagem_automatica(m.texto, getattr(m, "payload", None))
    ]
    if not menus:
        return None
    texto = "Olá! Os pedidos acompanham nota fiscal, que vai junto com o produto."
    if selecao not in {"2", "dois"}:
        texto += " Conte também qual é a sua dúvida sobre o produto para que possamos ajudar."
    return RespostaKia(
        categoria="saudacao_ou_vaga",
        texto=texto,
        fato={
            "politica": "opcao_menu_sem_pedido_documental",
            "integration_id": str(conversa.integration_id),
            "regra_id": str(autorizacoes[0].id),
        },
    )


def regra_especifica_kia(regra: Any) -> bool:
    """Identifica o escopo mesmo após edição, sem vazar a regra para outra loja."""
    return (
        getattr(regra, "tipo", None) == "categoria"
        and getattr(regra, "categoria", None) == "saudacao_ou_vaga"
        and getattr(regra, "plataforma", None) == "shopee"
        and getattr(regra, "canal", None) == "chat"
        and getattr(regra, "quando", None) == REGRA_QUANDO
    )


def regra_aprovada(regra: Any) -> bool:
    """A autorização é exata; edição no Manual desliga o processamento."""
    return (
        regra_especifica_kia(regra)
        and getattr(regra, "ativa", False)
        and getattr(regra, "faca", None) == REGRA_FACA
    )


def _utc(valor: datetime | None) -> datetime | None:
    if valor is None:
        return None
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=UTC)


def _momento(mensagem: Any) -> datetime | None:
    return _utc(getattr(mensagem, "enviada_em", None) or getattr(mensagem, "created_at", None))


def _figurinha_estruturada_antiga(mensagem: Any, ultimo_cartao: Any) -> bool:
    payload = getattr(mensagem, "payload", None)
    if not isinstance(payload, dict) or payload.get("message_type") != "sticker":
        return False
    conteudo = payload.get("content")
    if not isinstance(conteudo, dict) or not conteudo.get("sticker_id"):
        return False
    # Só o placeholder do adaptador: legenda ou texto concreto não são descartados.
    if (getattr(mensagem, "texto", None) or "").strip() not in ("", "[Figurinha]"):
        return False
    antes, depois = _momento(mensagem), _momento(ultimo_cartao)
    return antes is not None and depois is not None and depois - antes >= INTERVALO_FIGURINHA_ANTIGA


def cartao_ja_acolhido(mensagens: Sequence[Any], gatilho: Any | None) -> bool:
    """Histórico completo e só cartões, sem apagar pergunta por menu ou campanha.

    A única exceção é figurinha estruturada de uma visita muito anterior,
    como a de junho que antecedeu o cartão de setembro no caso revisado.
    Foto opaca, texto, opção de menu e figurinha recente continuam na fila.
    """
    if gatilho is None or getattr(mensagens, "turno_incompleto", False):
        return False
    if not e_cartao_do_comprador(getattr(gatilho, "payload", None)):
        return False
    if (getattr(gatilho, "texto", None) or "").strip():
        return False
    ids = [getattr(m, "id", None) for m in mensagens]
    if getattr(gatilho, "id", None) not in ids:
        return False
    ultima_resposta = max(
        (
            i
            for i, m in enumerate(mensagens)
            if resposta_de_pessoa(m) and getattr(m, "origem", None) != ORIGEM_AUTO
        ),
        default=-1,
    )
    turno = mensagens[ultima_resposta + 1 :]
    clientes = [m for m in turno if getattr(m, "autor", None) == AUTOR_CLIENTE]
    if not clientes or getattr(clientes[-1], "id", None) != getattr(gatilho, "id", None):
        return False
    for mensagem in clientes:
        if e_cartao_do_comprador(getattr(mensagem, "payload", None)):
            if (getattr(mensagem, "texto", None) or "").strip():
                return False
        elif not _figurinha_estruturada_antiga(mensagem, gatilho):
            return False
    quando = _momento(gatilho)
    if quando is None:
        return False
    return any(
        getattr(m, "autor", None) == AUTOR_LOJA
        and getattr(m, "status", None) != MSG_FALHOU
        and getattr(m, "origem", None) != ORIGEM_NOTA
        and _momento(m) is not None
        and _momento(m) > quando
        and e_mensagem_automatica(getattr(m, "texto", None), getattr(m, "payload", None))
        for m in turno
    )


async def _contexto_estrito(session: AsyncSession, conversa: AtendimentoConversa) -> dict:
    """Só consultas locais; erro não vira contexto vazio que autorizaria encerrar."""
    from app.services.atendimento import ag_cancelamento, contexto

    ctx = contexto.vazio()
    ctx["avaliacoes"] = await contexto._avaliacoes(session, conversa)
    chaves = contexto._chaves_do_pedido(conversa)
    if not chaves:
        return ctx
    numero_mkt = chaves[0]
    for chave in chaves:
        pedido = await contexto._pedido(
            session,
            chave,
            status_plataforma=ag_cancelamento.status_na_plataforma(conversa.dados),
        )
        if pedido is not None:
            ctx["pedido"] = pedido
            numero_mkt = chave
            break
    numero_bling = (ctx["pedido"] or {}).get("numero")
    ctx["chamados"] = await contexto._chamados(session, numero_bling, numero_mkt)
    ctx["devolucoes"] = await contexto._devolucoes(session, numero_bling, numero_mkt)
    ctx["reclamacoes"] = await contexto._reclamacoes(session, conversa)
    return ctx


def _rascunho_aposentavel(rascunho: AtendimentoRascunho, gatilho: AtendimentoMensagem) -> bool:
    """Só a antiga confusão cartão/anexo vazio; decisões e textos não são apagados."""
    return (
        rascunho.mensagem_gatilho_id == gatilho.id
        and rascunho.status == RASCUNHO_BLOQUEADO
        and rascunho.motivo == MOTIVO_CARD_ANTIGO
        and not rascunho.texto
        and rascunho.categoria is None
        and rascunho.modelo is None
        and not (rascunho.fatos or {}).get("pedida_pela_tela")
    )


async def processar(
    session: AsyncSession,
    conversa_id: UUID,
    *,
    canal_autorizado_id: UUID,
    agora: datetime | None = None,
    turno_esperado: datetime | None = None,
) -> bool:
    """Marca localmente e commita; em no-op libera as travas sem mudar a conversa.

    Quem chama limita o canal explicitamente aprovado. A linha da conversa
    trava o sync/envio; a trava de sugestão e o FOR UPDATE do rascunho
    preservam avaliação humana e a geração concorrente. Não consulta rede.
    """
    from app.services.atendimento import gravar, humano, ia

    s = get_settings()
    if not s.atendimento_ia_ativa or not s.atendimento_auto_ativo:
        return False
    agora = agora or datetime.now(UTC)

    async def _sob_trava() -> bool:
        # A mesma ordem do salvamento evita deadlock com INSERT de sugestão.
        # FOR UPDATE também detecta mensagem nova ainda em INSERT/FK.
        await ia._travar_gravacao_rascunho(session, conversa_id)
        conversa = await session.scalar(
            select(AtendimentoConversa)
            .where(AtendimentoConversa.id == conversa_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if (
            conversa is None
            or conversa.canal_id != canal_autorizado_id
            or conversa.plataforma != "shopee"
            or conversa.canal != "chat"
            or conversa.ia_pausada
            or conversa.atribuido_a is not None
            or conversa.situacao in (CONVERSA_FECHADA, CONVERSA_BLOQUEADA)
            or conversa.sem_resposta_necessaria
            or not conversa.aguardando_resposta
            or (
                turno_esperado is not None
                and _utc(conversa.ultima_do_cliente_em) != _utc(turno_esperado)
            )
        ):
            return False
        canal = await session.get(AtendimentoCanal, conversa.canal_id, populate_existing=True)
        if canal is None or canal.modo != MODO_AUTO:
            return False
        regras = await ia._regras_aplicaveis(session, conversa)
        autorizacoes = [r for r in regras if regra_aprovada(r)]
        saudacoes = [
            r for r in regras if r.tipo == "categoria" and r.categoria == "saudacao_ou_vaga"
        ]
        gerais = [r for r in saudacoes if r.plataforma is None and r.canal is None]
        if (
            len(autorizacoes) != 1
            or len(saudacoes) != 2
            or not ia._regra_basica_igual(gerais, "saudacao_ou_vaga", ia._REGRA_CARTAO_APROVADA)
        ):
            return False
        if await ia._pausa_humana_ate(session, conversa, agora=agora) is not None:
            return False
        gatilho = await ia._ultima_do_cliente(session, conversa)
        recentes = await ia._mensagens_recentes(session, conversa)
        if (
            gatilho is None
            or _momento(gatilho) != _utc(conversa.ultima_do_cliente_em)
            or not cartao_ja_acolhido(recentes, gatilho)
        ):
            return False
        triagem = humano.triar(
            conversa,
            recentes,
            await _contexto_estrito(session, conversa),
            so_humano=await humano.assuntos_so_humano(session),
            sugestao=None,
            gatilho=gatilho,
        )
        if triagem.motivos:
            return False
        rascunhos = list(
            (
                await session.scalars(
                    select(AtendimentoRascunho)
                    .where(
                        AtendimentoRascunho.conversa_id == conversa.id,
                        AtendimentoRascunho.status.in_((RASCUNHO_BLOQUEADO, RASCUNHO_PENDENTE)),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for rascunho in rascunhos:
            if not _rascunho_aposentavel(rascunho, gatilho):
                return False
            avaliado = await session.scalar(
                select(AtendimentoAvaliacao.id)
                .where(AtendimentoAvaliacao.rascunho_id == rascunho.id)
                .limit(1)
            )
            ligada = await session.scalar(
                select(AtendimentoMensagem.id)
                .where(AtendimentoMensagem.rascunho_id == rascunho.id)
                .limit(1)
            )
            if avaliado is not None or ligada is not None:
                return False
        for rascunho in rascunhos:
            rascunho.status = RASCUNHO_SUBSTITUIDO
        dados = dict(conversa.dados or {})
        dados[CHAVE_AUDITORIA] = {
            "v": 1,
            "em": agora.isoformat(),
            "motivo": MOTIVO,
            "gatilho_id": str(gatilho.id),
            "turno": _momento(gatilho).isoformat(),
            "regra_id": str(autorizacoes[0].id),
            "rascunhos_substituidos": [str(r.id) for r in rascunhos],
        }
        dados["humano"] = {
            "v": humano.VERSAO,
            "em": agora.timestamp(),
            "turno": _momento(gatilho).timestamp(),
            "motivos": [],
            "assuntos": [],
        }
        conversa.dados = dados
        conversa.sem_resposta_necessaria = True
        await gravar.recalcular_conversa(session, conversa)
        return True

    async with session.begin_nested():
        processada = await _sob_trava()
    await session.commit()
    return processada
