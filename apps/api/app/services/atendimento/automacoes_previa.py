"""A prévia de UMA linha do registro: como o comprador receberia × o que o Duoke mandou.

Eduardo, 05/10/2026 (à noite): "deixe só pra mostrar que ele enviaria mesmo
corretamente, mas não enviar". Para cada linha do registro das mensagens
automáticas (docs/atendimento-automacoes.md §8.3), a aba "Automáticas" abre,
lado a lado:

  • DAVINCI — as partes EXATAS que sairiam, montadas NA HORA (nada é gravado
    no registro, que continua sem texto nenhum): o cartão do pedido com o nº,
    o texto com o usuário do comprador preenchido (o mesmo `{comprador}` que o
    motor usa: o da conversa, o do escrow do financeiro ou o da avaliação), o
    valor do cupom do "carrinho", a figurinha, a resposta pública da avaliação.
    É a regra de AGORA (a versão dela e a da linha vão juntas: se o texto mudou
    depois da decisão, a tela avisa). A linha que já saiu de verdade (`enviado`,
    `revisar`) mostra as mensagens que saíram (`mensagem_ids`), não a regra.
  • DUOKE — as mensagens da LOJA que o Duoke mandou de verdade para a mesma
    conversa/pedido (a que o comparador casou e as que vão junto com ela, a até
    10 s: o cartão do pedido, o nome sozinho, a figurinha) e, na avaliação, a
    resposta pública gravada na avaliação. Com a hora, a diferença para a nossa
    e se bateu.

SÓ LEITURA: nenhuma consulta escreve, nada chama a plataforma, e o texto do
COMPRADOR nunca sai daqui (só mensagens da loja e a resposta da loja na
avaliação). A trava é a da rota (a mesma do /atendimento).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoAutomacaoRegra,
    AtendimentoConversa,
    AtendimentoMensagem,
)
from app.services.atendimento import automacoes, automacoes_comparar
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento.constantes import AUTOR_CLIENTE, ORIGEM_EXTERNO, ORIGEM_SISTEMA

_M = AtendimentoMensagem
# As mensagens que o Duoke manda JUNTO (o cartão 1 s antes do texto; o nome
# sozinho e a figurinha no mesmo segundo).
JUNTO = timedelta(seconds=10)
# O que conta como "a nossa sai/sairia" (o mesmo do comparador).
_MANDAM = (
    cat.ESTADO_SIMULADO,
    cat.ESTADO_ENVIADO,
    cat.ESTADO_ENVIANDO,
    cat.ESTADO_REVISAR,
)


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def comparacao_da_linha(x: Any) -> str | None:
    """bateu / so_davinci / so_duoke / combinada / pendente (o critério da conta). PURA."""
    if x.divergencia:
        return "combinada"
    manda = x.estado in _MANDAM
    if manda and x.duoke == cat.DUOKE_MANDOU:
        return "bateu"
    if x.estado == cat.ESTADO_PULADO and x.duoke == cat.DUOKE_NAO_MANDOU:
        return "bateu"
    if manda and x.duoke == cat.DUOKE_NAO_MANDOU:
        return "so_davinci"
    if (x.estado == cat.ESTADO_PULADO and x.duoke == cat.DUOKE_MANDOU) or (
        x.estado == cat.ESTADO_SO_DUOKE
    ):
        return "so_duoke"
    if x.duoke == cat.DUOKE_PENDENTE and x.estado != cat.ESTADO_AGENDADO:
        return "pendente"
    return None


def _parte(
    tipo: str,
    *,
    texto: str | None = None,
    pedido: str | None = None,
    figurinha: str | None = None,
    imagem_url: str | None = None,
    em: datetime | None = None,
    base: datetime | None = None,
    principal: bool = False,
    nota: str | None = None,
) -> dict[str, Any]:
    """Uma parte da prévia (o mesmo desenho dos dois lados)."""
    return {
        "tipo": tipo,
        "texto": texto,
        "pedido": pedido,
        "figurinha": figurinha,
        "imagem_url": imagem_url,
        "em": em,
        "diferenca_s": int((em - base).total_seconds()) if em and base else None,
        "principal": principal,
        "nota": nota,
    }


def _conteudo(payload: Any) -> dict:
    """O `content` do payload: objeto na Shopee, JSON EM TEXTO no TikTok (o
    ORDER_CARD: `"{\"order_id\": …}"`). Não sendo objeto, vazio."""
    if not isinstance(payload, dict):
        return {}
    conteudo = payload.get("content")
    if isinstance(conteudo, str) and conteudo.strip():
        try:
            conteudo = json.loads(conteudo)
        except ValueError:
            return {}
    return conteudo if isinstance(conteudo, dict) else {}


def _vista_da_mensagem(
    m: Any, *, base: datetime | None, principal: bool, nota: str | None = None
) -> dict[str, Any]:
    """A mensagem da LOJA gravada → a parte da prévia (texto, cartão, figurinha)."""
    payload = m.payload if isinstance(m.payload, dict) else {}
    conteudo = _conteudo(payload)
    em = _utc(m.enviada_em) or _utc(m.created_at)
    tipo_pl = payload.get("message_type") or payload.get("type")
    marca = payload.get("automacao") if isinstance(payload.get("automacao"), dict) else {}
    if tipo_pl in ("order", "ORDER_CARD") or marca.get("parte") == "cartao_pedido":
        pedido = str(conteudo.get("order_sn") or conteudo.get("order_id") or "").strip()
        return _parte(
            "cartao_pedido",
            pedido=pedido or marca.get("pedido"),
            em=em,
            base=base,
            principal=principal,
            nota=nota,
        )
    if tipo_pl == "sticker" or marca.get("parte") == "figurinha":
        return _parte(
            "figurinha",
            figurinha=str(conteudo.get("sticker_id") or marca.get("figurinha") or "") or None,
            imagem_url=str(conteudo.get("image_url") or "") or None,
            em=em,
            base=base,
            principal=principal,
            nota=nota,
        )
    if m.texto:
        return _parte("texto", texto=m.texto, em=em, base=base, principal=principal, nota=nota)
    return _parte("outro", texto=None, em=em, base=base, principal=principal, nota=nota)


async def _do_duoke_junto(
    session: AsyncSession, principal: Any, *, base: datetime | None
) -> list[dict[str, Any]]:
    """A mensagem do Duoke que casou e as que ele mandou junto (a até 10 s, da loja, de fora)."""
    em = _utc(principal.enviada_em) or _utc(principal.created_at)
    momento = func.coalesce(_M.enviada_em, _M.created_at)
    irmas = (
        (
            await session.execute(
                select(_M)
                .where(
                    _M.conversa_id == principal.conversa_id,
                    _M.autor != AUTOR_CLIENTE,
                    _M.origem.in_((ORIGEM_EXTERNO, ORIGEM_SISTEMA)),
                    func.coalesce(func.jsonb_typeof(_M.payload.op("->")("automacao")), "")
                    != "object",
                    _M.enviada_em >= em - JUNTO,
                    _M.enviada_em <= em + JUNTO,
                    momento >= em - JUNTO,
                    momento <= em + JUNTO,
                )
                .order_by(momento, _M.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not any(m.id == principal.id for m in irmas):
        irmas = [*irmas, principal]
    # Só o que é do MODELO do Duoke: o texto que casou, o cartão, a figurinha e
    # o nome sozinho (a parte que leva o usuário do comprador). Resposta de
    # pessoa que caiu no mesmo segundo não entra.
    saida = []

    # No mesmo segundo, a ordem da plataforma (o id da Shopee cresce).
    def _ordem(y: Any) -> tuple:
        externo = str(y.externo_id or "")
        return (_utc(y.enviada_em) or _utc(y.created_at), len(externo), externo, str(y.id))

    for m in sorted(irmas, key=_ordem):
        e_principal = m.id == principal.id
        vista = _vista_da_mensagem(m, base=base, principal=e_principal)
        if not e_principal and vista["tipo"] == "texto" and len(vista["texto"] or "") > 40:
            continue
        saida.append(vista)
    return saida


async def _nome_do_comprador(
    session: AsyncSession, x: AtendimentoAutomacaoRegistro, conversa: AtendimentoConversa | None
) -> str | None:
    """O `{comprador}`: o da conversa, o do escrow (pedido sem conversa) ou o da avaliação."""
    if conversa is not None and (conversa.comprador_nome or "").strip():
        return " ".join(conversa.comprador_nome.split())
    if x.pedido and x.plataforma == "shopee":
        nomes = await automacoes._nomes_do_escrow(session, [(x.integration_id, x.pedido)])
        if nomes.get((x.integration_id, x.pedido)):
            return nomes[(x.integration_id, x.pedido)]
    avaliacoes = await automacoes.avaliacoes_das_linhas(session, [x])
    av = avaliacoes.get(x.id)
    nome = " ".join(((av.comprador_nome_loja if av is not None else None) or "").split())
    return nome or None


async def _valores(
    session: AsyncSession,
    x: AtendimentoAutomacaoRegistro,
    aut: cat.Automacao,
    nome_loja: str | None,
) -> dict[str, str]:
    """As lacunas próprias (o cupom do "carrinho", pela faixa do valor do pedido)."""
    if "valor_cupom" not in aut.lacunas or not x.pedido:
        return {}
    indice = await automacoes._compradores_do_pedido(session, [(x.integration_id, x.pedido)])
    idx = indice.get((x.integration_id, x.pedido))
    valor = cat.valor_cupom(nome_loja, idx[3] if idx else None)
    return {"valor_cupom": str(valor)} if valor is not None else {}


async def _hora_nossa(
    session: AsyncSession, x: AtendimentoAutomacaoRegistro
) -> tuple[datetime | None, str]:
    """Quando a nossa saiu (a 1ª parte gravada), sairia (a decisão) ou vence (agendada)."""
    saidas = await automacoes_comparar._saidas(session, [x])
    if x.mensagem_ids:
        return saidas.get(x.id), "saiu"
    if x.estado in _MANDAM:
        return saidas.get(x.id), "sairia"
    return _utc(x.devido_em), "devido"


async def _partes_que_sairam(
    session: AsyncSession, x: AtendimentoAutomacaoRegistro, base: datetime | None
) -> list[dict[str, Any]]:
    """As mensagens que o DaVinci MANDOU de verdade por esta linha (`mensagem_ids`)."""
    ids = []
    for i in x.mensagem_ids or []:
        try:
            ids.append(UUID(str(i)))
        except ValueError:
            continue
    if not ids:
        return []
    msgs = (await session.execute(select(_M).where(_M.id.in_(ids)))).scalars().all()
    por_id = {m.id: m for m in msgs}
    return [
        _vista_da_mensagem(por_id[i], base=base, principal=n == 0)
        for n, i in enumerate(ids)
        if i in por_id
    ]


def _cartao_tiktok(duoke: list[dict[str, Any]], conversa: AtendimentoConversa | None) -> str | None:
    """O nº do pedido do TikTok (o aviso não traz): o da conversa (o que o DaVinci
    sabe; em produção, igual ao do cartão do Duoke em 157 de 157) ou, sem ele, o
    do cartão do Duoke."""
    if conversa is not None and (conversa.pedido_marketplace or "").strip():
        return conversa.pedido_marketplace.strip()
    for p in duoke:
        if p["tipo"] == "cartao_pedido" and p.get("pedido"):
            return p["pedido"]
    return None


async def montar(
    session: AsyncSession, x: AtendimentoAutomacaoRegistro, *, nome_loja: str | None
) -> dict[str, Any]:
    """A prévia da linha (sem a linha em si, que a rota acrescenta). SÓ LEITURA."""
    aut = cat.CATALOGO.get(x.automacao)
    regra = (
        await session.execute(
            select(AtendimentoAutomacaoRegra).where(
                AtendimentoAutomacaoRegra.automacao == x.automacao,
                AtendimentoAutomacaoRegra.integration_id == x.integration_id,
            )
        )
    ).scalar_one_or_none()
    conversa = await session.get(AtendimentoConversa, x.conversa_id) if x.conversa_id else None
    hora, hora_tipo = await _hora_nossa(session, x)

    # ── O Duoke de verdade ────────────────────────────────────────────────
    duoke: list[dict[str, Any]] = []
    if x.duoke_mensagem_id is not None:
        principal = await session.get(AtendimentoMensagem, x.duoke_mensagem_id)
        if principal is not None and principal.autor != AUTOR_CLIENTE:
            duoke = await _do_duoke_junto(session, principal, base=hora)
    if aut is not None and aut.alvo == cat.ALVO_AVALIACAO:
        av = (await automacoes.avaliacoes_das_linhas(session, [x])).get(x.id)
        resposta = (av.resposta_loja or "").strip() if av is not None else ""
        if resposta:
            em = automacoes_comparar.hora_da_resposta_publica(av.resposta_em)
            duoke.insert(
                0,
                _parte(
                    cat.PARTE_RESPOSTA_PUBLICA,
                    texto=resposta,
                    em=em,
                    base=hora,
                    principal=cat.assinatura_avaliacao(resposta) == aut.tipo,
                    nota=(
                        "resposta pública gravada na avaliação (a hora é a da Shopee "
                        "menos os 59 min que ela grava a mais)"
                        if cat.assinatura_avaliacao(resposta)
                        else "resposta pública de PESSOA (não é o modelo do Duoke)"
                    ),
                ),
            )
    janela_de = janela_ate = None
    if aut is not None and x.estado != cat.ESTADO_SO_DUOKE:
        janela_de, janela_ate = cat.janela_comparacao(aut, x.evento_em, x.devido_em)

    # ── O DaVinci: as partes exatas, montadas agora (ou as que saíram) ───
    comprador = await _nome_do_comprador(session, x, conversa)
    valores = await _valores(session, x, aut, nome_loja) if aut is not None else {}
    motivos: list[str] = []
    de_verdade = bool(x.mensagem_ids)
    partes: list[dict[str, Any]] = []
    if de_verdade:
        partes = await _partes_que_sairam(session, x, hora)
    elif aut is not None and x.estado != cat.ESTADO_SO_DUOKE:
        base = (regra.partes if regra is not None else None) or cat.partes_padrao(aut, nome_loja)
        prontas, motivos = cat.renderizar(
            base, comprador=comprador, plataforma=aut.plataforma, canal=aut.canal, valores=valores
        )
        pedido_cartao = x.pedido
        nota_cartao = None
        if not pedido_cartao and aut.plataforma == "tiktok":
            pedido_cartao = _cartao_tiktok(duoke, conversa)
            nota_cartao = (
                "o aviso de pedido da TikTok não traz o nº: este é o da conversa (sem ele, o do "
                "cartão do Duoke)"
            )
        imagem = next(
            (p["imagem_url"] for p in duoke if p["tipo"] == "figurinha" and p.get("imagem_url")),
            None,
        )
        for p in prontas:
            tipo = p.get("tipo")
            if tipo == "cartao_pedido":
                partes.append(
                    _parte(
                        "cartao_pedido", pedido=pedido_cartao, em=hora, base=hora, nota=nota_cartao
                    )
                )
            elif tipo == "figurinha":
                partes.append(
                    _parte(
                        "figurinha",
                        figurinha=p.get("figurinha"),
                        imagem_url=imagem,
                        em=hora,
                        base=hora,
                        nota=f"pacote {p.get('pacote') or '—'}",
                    )
                )
            else:
                partes.append(_parte(tipo, texto=p.get("texto"), em=hora, base=hora))
        if partes:
            partes[0]["principal"] = True
    sairia = x.estado in _MANDAM or x.estado == cat.ESTADO_AGENDADO
    return {
        "automacao": {
            "codigo": x.automacao,
            "nome": aut.nome if aut else x.automacao,
            "plataforma": x.plataforma,
            "tipo": aut.tipo if aut else None,
            "so_simulacao": aut.so_simular if aut else None,
            "so_simulacao_texto": cat.SO_SIMULAR.get(aut.so_simular or "") if aut else None,
        },
        "davinci": {
            "sairia": sairia,
            "de_verdade": de_verdade,
            "estado": x.estado,
            "motivo": x.motivo,
            "motivo_texto": cat.MOTIVOS.get(x.motivo or "") if x.motivo else None,
            "hora": hora,
            "hora_tipo": hora_tipo,
            "comprador": comprador,
            "valores": valores,
            "partes": partes,
            "motivos_validador": motivos,
            "versao_regra": regra.versao if regra is not None else None,
            "versao_da_linha": x.regra_versao,
        },
        "duoke": {
            "estado": x.duoke,
            "comparacao": comparacao_da_linha(x),
            "diferenca_s": x.duoke_diferenca_s,
            "em": _utc(x.duoke_em),
            "janela_de": janela_de,
            "janela_ate": janela_ate,
            "partes": duoke,
        },
    }
