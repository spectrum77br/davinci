"""OFERTA DE TROCA pelo chat da plataforma (item 4, fase 4d, 07/10/2026).

O pedido está em "Aguardando Cancelamento" (83955) por falta de estoque e a
4b achou um parecido (`troca_sugestoes`): o botão "Enviar oferta" manda ao
comprador o texto da oferta — o MESMO que a 4b monta (`texto_oferta`); a
pessoa pode editar — pela conversa do pedido. Só pessoa, e só pelo caminho
ÚNICO de saída (`enviar.enviar_resposta`, origem humano), com TODAS as
travas dele: o freio `atendimento_envio_ativo`, a loja em observar, a
conversa bloqueada, a resposta repetida, a conversa que mudou, o texto
reprovado pelo validador...

AS TRAVAS DA OFERTA vêm antes das do envio (nada sai; 409 `{code, detail}`):
  troca_desligada / pedido_fora_do_piloto — a chave e a lista piloto da
      troca (`troca.conferir_chaves`): oferecer o que o DaVinci não troca
      depois seria prometer à toa;
  motivo_nao_permite — só o pedido em 83955 por falta de estoque VIVA, do
      item em falta; e um item só (dois em falta, uma troca não resolve: a
      troca recusa com `outro_item_sem_estoque`);
  troca_em_andamento — já há troca de produto aberta no pedido;
  sugestao_invalida — o produto tem de ser um parecido ELEGÍVEL pela regra
      da 4b (com estoque no DaVinci e dentro do teto do custo) que pede o
      aceite: o nível 0 (o mesmo produto, outro lote) não tem oferta;
  kit_lotes_misturados — o kit do produto escolhido com lotes misturados;
  as do PEDIDO que a troca recusaria com certeza (`troca.travas_do_pedido`,
      com o código da troca): `plataforma_sem_conferencia` (só a Shopee na
      v1), `nf_emitida`, `em_fila_nf`, `etiqueta_gerada`, `rastreio`,
      `pedido_saiu`, `pedido_em_dobro`, `prazo_vencido`;
  sem_conversa — o pedido não tem conversa no DaVinci (abrir a conversa
      pelo DaVinci fica fora da 4d);
  conversa_mudou — chegou mensagem (do CLIENTE ou da loja) depois da última
      que a tela tinha (`ultima_vista_id` = o `oferta_envio.
      ultima_mensagem_id`; sem ele, a marca da falta de estoque): o cliente
      pode ter recusado ou outra pessoa já ter oferecido pelo Duoke.
      `confirmar` = "enviar mesmo assim";
  e as do envio, com o código do `enviar` — a loja em observar
      (`canal_em_observacao`) vira `canal_nao_envia` (o contrato da tela).

O QUE FICA GRAVADO: a mensagem do envio (enviada / revisar / falhou — erro
da plataforma não é recusa: a linha existe e a tela mostra), com a marca
`payload.oferta_troca` (o pedido e os SKUs) — é por ela que a troca guarda
`oferta_mensagem_id` (`oferta_enviada_id`, crítica M11); e, quando saiu ou
pode ter saído, a nota interna "Oferta de troca enviada (a -> b) por <nome>".
A oferta NÃO aprova a Margem nem fala com o Bling, mas PROMETE a troca ao
comprador: a rota pede o mesmo da troca (decisão (g) do dono, 07/10/2026) —
`_so_admin` + `atendimento.edit` + `margem.edit` (`troca.pode_escrever`).

`situacao_da_oferta` é o `oferta_envio` do bloco Ag. cancelamento (painel e
lista): as MESMAS travas que não dependem do produto escolhido, para o
botão nunca dizer "pode" e a rota dizer "não" — e o `ultima_mensagem_id`
que a tela devolve como `ultima_vista_id`.

FORA DA 4d (desenho §4): a IA marcar "parece aceite / recusa", a conversa
nova para o pedido sem conversa, a tabela `atendimento_ag_cancelamento` e
o lembrete antes do prazo.

Texto de comprador (e o nosso) nunca vai para o log — só ids, códigos e SKUs.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoMensagem, User
from app.services.atendimento import enviar, etiqueta_fatos, gravar, troca, troca_sugestoes
from app.services.atendimento.ag_cancelamento import SEM_ESTOQUE
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    MSG_ENVIADA,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGEM_HUMANO,
    TIPO_NOTA,
)
from app.services.atendimento.enviar import EnvioRecusado

logger = structlog.get_logger()

# ── Códigos de recusa (estáveis: a tela traduz) ───────────────────────────
SEM_CONVERSA = "sem_conversa"
CANAL_NAO_ENVIA = "canal_nao_envia"
SO_LEITURA = troca.SO_LEITURA
# O mesmo código do `enviar` (a tela já trata o "enviar mesmo assim").
CONVERSA_MUDOU = enviar.RECUSA_CONVERSA_MUDOU
_SP = ZoneInfo("America/Sao_Paulo")
# O código do envio que muda de nome na oferta; os outros passam como vêm.
_CODIGO_DO_ENVIO = {enviar.RECUSA_CANAL_EM_OBSERVACAO: CANAL_NAO_ENVIA}

TEXTOS = {
    SEM_CONVERSA: (
        "O pedido não tem conversa no DaVinci: mande a oferta pelo Duoke ou pela plataforma."
    ),
    SO_LEITURA: (
        "Só leitura por enquanto: a oferta sai por quem cuida do Atendimento e mexe na Margem."
    ),
    "item_nao_em_falta": "O item escolhido não é o que está em falta neste pedido.",
    "item_saiu": "O item em falta já não está no pedido.",
    "nivel_sem_oferta": (
        "O mesmo produto de outro lote não pede o aceite do cliente: não há oferta a "
        "enviar (use Trocar)."
    ),
    troca_sugestoes.FORA_SEM_ESTOQUE: "O produto escolhido está sem estoque no DaVinci.",
    troca_sugestoes.FORA_CUSTO_ACIMA: troca.TEXTOS["custo_acima"],
    troca_sugestoes.FORA_CUSTO_ABAIXO_PISO: troca.TEXTOS["custo_abaixo_piso"],
    "conversa_nao_encontrada": "Conversa não encontrada.",
}

# O `oferta_envio` quando a conferência quebrou (o resto do painel segue).
OFERTA_NAO_CONFERIDA = {
    "disponivel": False,
    "motivo": "falhou",
    "texto_motivo": "Não consegui conferir agora se a oferta pode sair.",
}


class OfertaRecusada(troca.TrocaRecusada):
    """A oferta NÃO saiu, por uma trava nossa ou do envio (nada foi à plataforma).

    Filha de `TrocaRecusada`: o router devolve as duas do mesmo jeito
    (`{code, detail}`). Em `texto_invalido` (422) o `detail` é a lista de
    motivos do validador, como no responder.
    """

    def __init__(
        self, code: str, detail: str | list[str] | None = None, *, status: int = 409
    ) -> None:
        super().__init__(code, TEXTOS.get(code) if detail is None else detail, status=status)


def _da_recusa_do_envio(e: EnvioRecusado) -> OfertaRecusada:
    """A trava do `enviar` → a recusa da oferta (o texto reprovado é 422, como no responder)."""
    status = 422 if e.code == enviar.RECUSA_TEXTO_INVALIDO else 409
    return OfertaRecusada(_CODIGO_DO_ENVIO.get(e.code, e.code), e.detail, status=status)


# ── Quem pode ─────────────────────────────────────────────────────────────


def pode_escrever(user: User | None) -> bool:
    """Passaria nas travas da rota? O botão desliga sem.

    As MESMAS da troca (`troca.pode_escrever`, decisão (g) do dono):
    `_so_admin` + `atendimento.edit` + `margem.edit` — quem não pode trocar
    não promete a troca ao comprador.
    """
    return troca.pode_escrever(user)


# ── As travas da oferta ───────────────────────────────────────────────────


def _item_em_falta(skus: Collection[str], sku_antigo: str | None = None) -> str:
    """O ÚNICO SKU em falta do pedido (o escolhido tem de ser ele); levanta `motivo_nao_permite`."""
    em_falta = [s for s in skus if (s or "").strip()]
    if not em_falta:
        raise OfertaRecusada("motivo_nao_permite", TEXTOS["item_saiu"])
    if len(em_falta) > 1:
        raise OfertaRecusada(
            "motivo_nao_permite",
            f"Outro item também está em falta ({', '.join(em_falta)}): uma troca não "
            "resolve o pedido.",
        )
    if sku_antigo and sku_antigo.strip().lower() != em_falta[0].strip().lower():
        raise OfertaRecusada("motivo_nao_permite", TEXTOS["item_nao_em_falta"])
    return em_falta[0].strip()


def _conferir_motivo(motivo: Mapping[str, Any] | None) -> None:
    """Só a falta de estoque viva (o bloco Ag. cancelamento, já mascarado ou não)."""
    if (
        motivo is None
        or motivo.get("codigo") != SEM_ESTOQUE
        or not motivo.get("pode_sugerir_troca")
    ):
        raise OfertaRecusada("motivo_nao_permite", troca.TEXTOS["motivo_nao_permite"])
    _item_em_falta(motivo.get("skus") or ())


def _sugestao(
    cat: troca_sugestoes.Catalogo, item: Mapping[str, Any], sku_novo: str
) -> tuple[troca_sugestoes.Sugestao, str]:
    """O parecido ELEGÍVEL da 4b e o texto da oferta dele (o mesmo da tela).

    Levanta `sugestao_invalida`: fora da regra, de fora (sem estoque no
    DaVinci, custo acima do teto ou abaixo do piso) ou o nível 0.
    """
    s = get_settings()
    sku = str(item["sku"])
    cands = troca_sugestoes.candidatos(
        cat,
        sku,
        item.get("quantidade") or 1,
        teto_pct=float(s.atendimento_troca_teto_custo_pct),
        piso_n2_pct=float(s.atendimento_troca_piso_nivel2_pct),
    )
    alvo = sku_novo.strip().lower()
    sug = next((c for c in cands if c.sku.strip().lower() == alvo), None)
    if sug is None:
        raise OfertaRecusada("sugestao_invalida", troca.TEXTOS["sugestao_invalida"])
    if sug.motivo_fora is not None:
        raise OfertaRecusada("sugestao_invalida", TEXTOS.get(sug.motivo_fora))
    if sug.nivel == troca_sugestoes.NIVEL_LOTE:
        raise OfertaRecusada("sugestao_invalida", TEXTOS["nivel_sem_oferta"])
    # O nome do original como a 4b põe na oferta (`troca_sugestoes.item_de_troca`).
    o = cat.produtos.get(sku.lower())
    nome = (o.nome if o is not None else "") or (item.get("descricao") or "").strip() or sku
    return sug, troca_sugestoes.texto_oferta(nome, sug.nome, mesmo_produto=sug.mesmo_produto)


def _visivel(conversa: AtendimentoConversa, integracoes: Collection[UUID] | None) -> bool:
    return integracoes is None or conversa.integration_id in integracoes


async def _conversa(
    session: AsyncSession,
    numero: str,
    carregada: AtendimentoConversa | None,
    *,
    conversa_id: UUID | None,
    integracoes: Collection[UUID] | None,
) -> AtendimentoConversa | None:
    """A conversa do pedido DENTRO do escopo da equipe (a escolhida, ou a 1ª visível)."""
    if carregada is None or _visivel(carregada, integracoes):
        return carregada
    if conversa_id is not None:
        # A escolhida é do pedido, mas de uma loja de fora da equipe.
        raise OfertaRecusada("conversa_nao_encontrada", status=404)
    return next(
        (
            c
            for c in await etiqueta_fatos.conversas_do_pedido_bling(session, numero)
            if _visivel(c, integracoes)
        ),
        None,
    )


def _momento():
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


def _da_conversa(conversa: AtendimentoConversa) -> list[Any]:
    """As falas que contam (do cliente e da loja; nem nota, nem envio que falhou)."""
    return [
        AtendimentoMensagem.conversa_id == conversa.id,
        AtendimentoMensagem.tipo != TIPO_NOTA,
        AtendimentoMensagem.status.is_distinct_from(MSG_FALHOU),
        AtendimentoMensagem.autor.in_((AUTOR_CLIENTE, AUTOR_LOJA)),
    ]


async def ultima_mensagem_id(session: AsyncSession, conversa: AtendimentoConversa) -> UUID | None:
    """A fala mais recente da conversa — o que a tela "viu" ao montar o botão."""
    return await session.scalar(
        select(AtendimentoMensagem.id)
        .where(*_da_conversa(conversa))
        .order_by(_momento().desc(), AtendimentoMensagem.created_at.desc())
        .limit(1)
    )


async def _conferir_vista(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    ultima_vista_id: UUID | None,
    marca: datetime | None,
) -> None:
    """Chegou fala (do cliente ou da loja) depois do que a pessoa viu? → 409 `conversa_mudou`.

    O `enviar` só confere a resposta da LOJA depois da vista; aqui conta
    também o CLIENTE — ele pode ter escrito "não quero outra cor, cancela" —
    e a oferta de outra pessoa pelo Duoke com outras palavras (o
    `envio_repetido` só pega o mesmo texto). A referência é a mensagem que
    a tela tinha (`ultima_mensagem_id` do `oferta_envio`); sem ela (ou de
    outra conversa), a marca da falta de estoque: o que veio depois dela
    ninguém disse que viu. Sem nenhuma das duas, não trava.
    """
    filtros = _da_conversa(conversa)
    vista = await session.get(AtendimentoMensagem, ultima_vista_id) if ultima_vista_id else None
    if vista is not None and vista.conversa_id == conversa.id:
        quando = vista.enviada_em or vista.created_at
        filtros += [AtendimentoMensagem.id != vista.id, _momento() >= quando]
    elif marca is not None:
        filtros.append(_momento() > marca)
    else:
        return
    nova = (
        await session.execute(
            select(AtendimentoMensagem.autor, _momento().label("em"))
            .where(*filtros)
            .order_by(_momento().desc())
            .limit(1)
        )
    ).first()
    if nova is None:
        return
    quem = "O cliente" if nova.autor == AUTOR_CLIENTE else "A loja"
    em = nova.em if nova.em.tzinfo is not None else nova.em.replace(tzinfo=UTC)
    raise OfertaRecusada(
        CONVERSA_MUDOU,
        f"{quem} escreveu às {em.astimezone(_SP):%H:%M} depois do que você viu: confira a "
        "conversa antes de mandar a oferta.",
    )


def _conferir_kit(sku_antigo: str, sku_novo: str) -> None:
    """Kit com lotes misturados fica fora da troca (`troca._travas_banco`): sem oferta."""
    if len(troca._lotes(sku_antigo)) > 1 or len(troca._lotes(sku_novo)) > 1:
        raise OfertaRecusada("kit_lotes_misturados", troca.TEXTOS["kit_lotes_misturados"])


async def _conferir_pedido(session: AsyncSession, p: Any) -> None:
    """As travas do PEDIDO que a troca recusaria com certeza (`troca.travas_do_pedido`)."""
    falha = troca._primeira_falha(await troca.travas_do_pedido(session, p))
    if falha is not None:
        raise OfertaRecusada(str(falha["code"]), str(falha["texto"]))


# ── O envio ───────────────────────────────────────────────────────────────


def _nome(user: User) -> str:
    return (user.name or "").strip() or (user.email or "").strip() or str(user.id)


def texto_da_nota(antigo: str, novo: str, nome: str, *, status: str) -> str:
    """A nota interna da oferta. PURA."""
    texto = f"Oferta de troca enviada ({antigo} -> {novo}) por {nome}."
    if status == MSG_REVISAR:
        texto += " A plataforma não confirmou o envio: confira a conversa."
    return texto


async def _registrar(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    *,
    marca: dict[str, Any],
    nota: str | None,
    user_id: UUID,
) -> None:
    """A marca da oferta na mensagem e a nota interna. A mensagem JÁ SAIU: nunca levanta.

    A trava da conversa antes (a ordem do sync: conversa, depois mensagem),
    e a mensagem relida — o sync pode tê-la adotado no meio.
    """
    from app.services.atendimento import painel

    try:
        await gravar.travar_linha(session, conversa)
        await session.refresh(mensagem)
        mensagem.payload = {**(mensagem.payload or {}), "oferta_troca": marca}
        if nota:
            await painel.criar_nota(session, conversa, nota, user_id=user_id)
        await session.commit()
    except Exception as e:  # noqa: BLE001 — a oferta saiu; a marca e a nota não a desfazem
        await session.rollback()
        logger.warning(
            "atendimento_troca_oferta_registro_falhou",
            conversa_id=str(conversa.id),
            mensagem_id=str(mensagem.id),
            err=type(e).__name__,
        )
        for obj in (conversa, mensagem):
            try:
                await session.refresh(obj)
            except Exception:  # noqa: BLE001, S110 — sessão quebrada; o log já está
                pass


async def enviar_oferta(
    session: AsyncSession,
    user: User,
    *,
    numero_bling: str,
    sku_novo: str,
    sku_antigo: str | None = None,
    conversa_id: UUID | None = None,
    texto: str | None = None,
    ultima_vista_id: UUID | None = None,
    confirmar: bool = False,
    integracoes: Collection[UUID] | None = None,
) -> dict[str, Any]:
    """O botão "Enviar oferta"; devolve o `OfertaTrocaOut`.

    Levanta `OfertaRecusada` (ou a `TrocaRecusada` das chaves e do pedido:
    404 `pedido_nao_encontrado`, 422 `conversa_de_outro_pedido`) quando uma
    trava impede — nada saiu. Erro da PLATAFORMA não levanta: a mensagem
    volta `falhou`/`revisar`, como no responder. `texto` vazio = o da 4b.
    `ultima_vista_id` = o `oferta_envio.ultima_mensagem_id` que a tela tinha
    (`conversa_mudou` se chegou fala depois); `confirmar` envia mesmo assim.
    `integracoes` = as integrações da equipe (None = sem filtro). COMMITA
    (o `enviar` commita antes e depois da plataforma).
    """
    if user is None:
        raise ValueError("a oferta precisa de quem clicou")
    numero = numero_bling.strip()
    # Antes do envio: um rollback no meio expira o usuário.
    user_id, nome = user.id, _nome(user)
    # As chaves e o piloto antes de qualquer leitura (como a troca).
    troca.conferir_chaves(numero)
    p = await troca._carregar(session, numero, conversa_id)
    m = p.motivo
    if m is None or m.codigo != SEM_ESTOQUE:
        raise OfertaRecusada("motivo_nao_permite", troca.TEXTOS["motivo_nao_permite"])
    antigo = _item_em_falta(m.skus, sku_antigo)
    item = p.item(antigo)
    if item is None:
        raise OfertaRecusada("motivo_nao_permite", TEXTOS["item_saiu"])
    aberta = await troca.troca_aberta_do_pedido(session, numero)
    if aberta is not None:
        raise OfertaRecusada("troca_em_andamento", troca.TEXTOS["troca_em_andamento"])
    sug, texto_4b = _sugestao(await troca_sugestoes.catalogo(session), item, sku_novo)
    _conferir_kit(antigo, sug.sku)
    # O que a troca recusaria com certeza vem antes da conversa: sem a troca,
    # nem pelo Duoke se oferece.
    await _conferir_pedido(session, p)
    conversa = await _conversa(
        session, numero, p.conversa, conversa_id=conversa_id, integracoes=integracoes
    )
    if conversa is None:
        raise OfertaRecusada(SEM_CONVERSA)
    if not confirmar:
        marcada_em = p.pedido.nf_marcada_em if p.pedido is not None else None
        if marcada_em is not None and marcada_em.tzinfo is None:
            marcada_em = marcada_em.replace(tzinfo=UTC)
        await _conferir_vista(session, conversa, ultima_vista_id, marcada_em)

    escrito = (texto or "").strip()
    try:
        mensagem = await enviar.enviar_resposta(
            session,
            conversa,
            escrito or texto_4b,
            user=user,
            origem=ORIGEM_HUMANO,
            ultima_vista_id=ultima_vista_id,
            confirmar=confirmar,
        )
    except EnvioRecusado as e:
        logger.info(
            "atendimento_troca_oferta_recusada",
            pedido=numero,
            conversa_id=str(conversa.id),
            code=e.code,
            user_id=str(user_id),
        )
        raise _da_recusa_do_envio(e) from e

    novo = sug.sku
    saiu = mensagem.status in (MSG_ENVIADA, MSG_REVISAR)
    await _registrar(
        session,
        conversa,
        mensagem,
        marca={
            "pedido": numero,
            "sku_antigo": item["sku"],
            "sku_novo": novo,
            "nivel": sug.nivel,
            "editada": bool(escrito) and escrito != texto_4b,
        },
        nota=texto_da_nota(item["sku"], novo, nome, status=mensagem.status) if saiu else None,
        user_id=user_id,
    )
    logger.info(
        "atendimento_troca_oferta",
        pedido=numero,
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        de=item["sku"],
        para=novo,
        nivel=sug.nivel,
        status=mensagem.status,
        user_id=str(user_id),
    )
    return {
        "enviada": mensagem.status == MSG_ENVIADA,
        "mensagem_id": mensagem.id,
        "texto": mensagem.texto or "",
        "status": mensagem.status,
        "erro": mensagem.erro,
        "conversa_id": conversa.id,
        "sku_antigo": item["sku"],
        "sku_novo": novo,
        "nivel": sug.nivel,
    }


# ── O botão (painel e lista) ──────────────────────────────────────────────


def _indisponivel(code: str, texto: Any) -> dict[str, Any]:
    return {"disponivel": False, "motivo": code, "texto_motivo": str(texto) if texto else None}


async def situacao_da_oferta(
    session: AsyncSession,
    *,
    numero: str,
    motivo: Mapping[str, Any] | None,
    conversa: AtendimentoConversa | None,
    user: User | None,
    memo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """O `oferta_envio` do bloco Ag. cancelamento: o botão "Enviar oferta" pode, e o porquê.

    `motivo` = o bloco (`painel.bloco_do_motivo`, com o `troca_aberta` já
    lido); `conversa` = a do painel (ou a principal do pedido, na lista). As
    travas da rota que não dependem do produto, NA MESMA ORDEM (quem pode,
    as chaves, o motivo, a troca aberta, as do pedido —
    `troca.primeira_trava_do_pedido`, com o `memo` do "Trocar" —, a
    conversa) e no fim as do envio (`enviar.motivo_para_nao_enviar`): o
    botão e a rota nunca discordam. Pode = leva o `ultima_mensagem_id` (a tela o devolve como
    `ultima_vista_id`). Nada é gravado.
    """
    try:
        if not pode_escrever(user):
            raise OfertaRecusada(SO_LEITURA)
        troca.conferir_chaves(numero)
        _conferir_motivo(motivo)
        if (motivo or {}).get("troca_aberta"):
            raise OfertaRecusada("troca_em_andamento", troca.TEXTOS["troca_em_andamento"])
    except troca.TrocaRecusada as e:
        return _indisponivel(e.code, e.detail)
    falha = await troca.primeira_trava_do_pedido(session, numero, memo=memo)
    if falha is not None:
        return _indisponivel(str(falha["code"]), falha["texto"])
    if conversa is None:
        return _indisponivel(SEM_CONVERSA, TEXTOS[SEM_CONVERSA])
    recusa = await enviar.motivo_para_nao_enviar(session, conversa, origem=ORIGEM_HUMANO)
    if recusa is not None:
        return _indisponivel(_CODIGO_DO_ENVIO.get(recusa.code, recusa.code), recusa.detail)
    ultima = await ultima_mensagem_id(session, conversa)
    return {
        "disponivel": True,
        "motivo": None,
        "texto_motivo": None,
        "ultima_mensagem_id": str(ultima) if ultima else None,
    }


# ── A oferta na troca (crítica M11) ───────────────────────────────────────


async def oferta_enviada_id(
    session: AsyncSession, conversa_id: UUID | None, numero: str, sku_novo: str
) -> UUID | None:
    """A última oferta que saiu (ou pode ter saído) NESTA conversa, deste pedido e produto.

    Para a troca guardar `oferta_mensagem_id`. Só a dos últimos
    `atendimento_troca_aceite_max_dias` (a janela do aceite).
    """
    if conversa_id is None:
        return None
    dias = int(get_settings().atendimento_troca_aceite_max_dias)
    desde = datetime.now(UTC) - timedelta(days=dias)
    marca = AtendimentoMensagem.payload["oferta_troca"]
    return await session.scalar(
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id == conversa_id,
            AtendimentoMensagem.status.in_((MSG_ENVIADA, MSG_REVISAR)),
            AtendimentoMensagem.created_at >= desde,
            marca["pedido"].astext == numero.strip(),
            func.lower(marca["sku_novo"].astext) == sku_novo.strip().lower(),
        )
        .order_by(AtendimentoMensagem.created_at.desc())
        .limit(1)
    )
