"""Chamados — regras e ações da aba (Pós-venda → Chamados).

A aba centraliza os chamados abertos nas plataformas, venham da Margem
(diferença de frete), da Logística (mediação/reclamação do comprador) ou de
uma Devolução. Este módulo concentra o que não é CRUD:

- `lookup_pedido` / `preencher_do_pedido`: espelho do pedido (bling_orders +
  store_info) pra preencher Data / pedidos / plataforma / conta / produto / sku
  / status Bling sem digitação.
- `enviar_mensagem`: despacha uma réplica pelo canal do chamado — `api` manda
  na mediação do Mercado Livre (claim já aberto pelo comprador; o vendedor não
  abre reclamação do zero), `robo` deixa `pendente` na fila do robô de browser
  (chamados de formulário/protocolo, sem API), `manual` só registra.
- `aplicar_status_bling`: muda a situação do pedido no Bling (mesmo PATCH
  dedicado da Logística) e carimba o histórico.
- `run_replica_automatica`: cron — reenvia a mensagem automática a cada N dias
  enquanto ligada, e acompanha TODO chamado de API do ML: quando o ML encerra
  o claim, o chamado vai pro estado Encerrado (Eduardo 15/09: "o robô precisa
  acompanhar todos os chamados" — o antigo sim/não "monitoramento" saiu da aba
  e do banco).

19/09 (Vinicius): NENHUM caminho automático fecha chamado. "Encerrado" é um
ESTADO (status oficial final + evento), não um fechamento — só uma pessoa
conclui, pelo `POST /{id}/resolver`, dizendo lucro/prejuízo. O robô e as
plataformas só SUGEREM o valor (`Chamado.valor_sugerido`).

Regra combinada com o usuário (planilha, célula "alterar status bling"):
Logística → "Problemas" ao abrir e "Resolvido"/"Perdimento" ao fechar; Margem
não altera; Devolução ficou em branco (sem padrão).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    Devolution,
    Logistica,
    SituacaoBling,
    StoreInfo,
)
from app.services import logistica_bling, logistica_meli
from app.services.marketplaces.ml import MercadoLivreClient

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# Situação Bling padrão ao ABRIR um chamado, por origem (célula M2 da planilha).
STATUS_ABERTURA_POR_ORIGEM: dict[str, str] = {"logistica": "Problemas"}
# Opções oferecidas ao FECHAR, por origem.
STATUS_FECHAMENTO_POR_ORIGEM: dict[str, tuple[str, ...]] = {
    "logistica": ("Resolvido", "Perdimento"),
}

AUTOR_SISTEMA = "sistema"
AUTOR_AUTO = "réplica automática"

# Coluna "Status" da aba. Dois níveis:
#
# 1. Status OFICIAL da plataforma (`Chamado.status_plataforma` + desde quando):
#    lido pela API e gravado pelos syncs. Vinicius 17/09: "o chamado está com
#    status finalizado, aberto, de todas as plataformas". Continua igual em 19/09
#    — vira o "motivo" (tooltip) do status da aba e alimenta a derivação.
STATUS_EM_ANALISE = "em_analise"  # plataforma julgando a disputa
STATUS_PROVA = "prova"  # plataforma pediu prova extra
STATUS_REEMBOLSO_PAGO = "reembolso_pago"  # Shopee reembolsou o comprador; compensação ainda não veio
STATUS_GANHAMOS = "ganhamos"
STATUS_PERDEMOS = "perdemos"
STATUS_ENCERRADO = "encerrado"  # encerrou sem dizer quem ganhou
STATUS_AGUARDANDO = "aguardando"  # nós falamos por último (recusa registrada)
STATUS_FINAIS = frozenset({STATUS_GANHAMOS, STATUS_PERDEMOS, STATUS_ENCERRADO})
# 19/09: chamado no estado Encerrado (status final, ninguém concluiu) já não tem
# nada pra fazer sozinho — réplica automática, reenvio de abertura pendente e a
# varredura da plataforma param nele. Filtro SQL compartilhado pelos crons
# (`run_replica_automatica`, `sync_respostas`, `processar_pendentes`).
NAO_ENCERRADO_SQL = or_(
    Chamado.status_plataforma.is_(None),
    Chamado.status_plataforma.not_in(sorted(STATUS_FINAIS)),
)

# 2. Status da ABA (`status_aba`, derivado na hora pela listagem, não persiste).
#    Vinicius 19/09: os 13 códigos de antes viraram CINCO, e a regra é "primeiro o
#    robô; gente só quando o robô desiste":
#      Análise Humano   — o robô não conseguiu / precisa de gente;
#      Análise Robô     — o robô (IA do Eduardo) tem trabalho aqui: responder,
#                         reenviar, achar outro caminho, ou uma instrução nossa;
#      Aguard. Plataforma — a bola está com a plataforma;
#      Encerrado        — a plataforma encerrou o caso (ganhamos/perdemos/sem
#                         decisão) e falta uma PESSOA fechar com lucro/prejuízo;
#      Concluído        — fechado por pessoa (POST /{id}/resolver).
#    Ver `status_e_motivo_da_aba`.
ABA_ANALISE_HUMANO = "analise_humano"
ABA_ANALISE_ROBO = "analise_robo"
ABA_AGUARD_PLATAFORMA = "aguard_plataforma"
ABA_ENCERRADO = "encerrado"
ABA_CONCLUIDO = "concluido"

# 19/09: o robô tenta de novo antes de desistir (Vinicius: "envio falhou → fila do
# robô; se não conseguir, humano"). `/agent/resultado` com ok=false devolve a
# mensagem pra fila até esta contagem; na última fica `falhou` e vira Análise Humano.
MAX_TENTATIVAS_ROBO = 3

# O que a coluna diz embaixo de "Aguard. Plataforma" pra cada status oficial.
# 18/09 (Vinicius, 296936): a recusa que MANDAMOS não é "ganhamos" — a plataforma
# registrou e o outro lado ainda pode recorrer; o sync grava `aguardando`.
MOTIVO_STATUS_OFICIAL = {
    STATUS_EM_ANALISE: "em análise na plataforma",
    STATUS_PROVA: "prova enviada — a Shopee analisa",
    STATUS_REEMBOLSO_PAGO: "reembolso pago — 24 h de carência",
    STATUS_AGUARDANDO: "nossa recusa registrada — o comprador ainda pode recorrer",
}
# Motivo dos status finais (Encerrado / Concluído).
MOTIVO_FINAL = {STATUS_GANHAMOS: "ganhamos", STATUS_PERDEMOS: "perdemos"}

# 18/09 (Eduardo: "está uma zona, precisamos dos status verdadeiros"): o erro da
# última mensagem nossa diz DE QUEM é a vez — e a coluna tem que dizer isso.
# A plataforma ainda não liberou pela API. 19/09 (Vinicius): nesses casos o robô
# tem OUTRO caminho na plataforma (Seller Center) — é Análise Robô, não espera;
# `/agent/analisar` entrega o chamado com `bloqueio` e `responder` é aceito em
# canal api (o robô assume).
ERROS_ESPERA_PLATAFORMA = frozenset({
    "shopee_motivo_indisponivel", "shopee_aguardando_pacote", "tiktok_aguardando_pacote",
    "tiktok_recusa_bloqueada", "tiktok_arbitragem", "return_review_indisponivel",
})
# Falta algo que só gente resolve (foto, quebra-cabeça, login, tarefa sem API):
ERROS_PEDEM_HUMANO = frozenset({
    "devolucao_sem_foto", "devolucao_motivo_sem_chamado", "devolucao_sem_pedido_marketplace",
    "devolucao_sem_claim", "devolucao_nao_encontrada", "shopee_captcha_humano",
    "plataforma_sem_api", "plataforma_sem_api_replica", "sem_perfil_adspower", "perfil_deslogado",
})
# Texto curto que a coluna mostra (tooltip) junto do status.
MOTIVO_DO_ERRO = {
    "shopee_motivo_indisponivel": "Shopee ainda não libera o motivo da contestação",
    "shopee_aguardando_pacote": "pacote da devolução ainda em trânsito",
    "tiktok_aguardando_pacote": "pacote da devolução ainda em trânsito",
    "tiktok_recusa_bloqueada": "TikTok ainda não libera a recusa",
    "tiktok_arbitragem": "em arbitragem na TikTok",
    "return_review_indisponivel": "ML ainda não libera a revisão",
    "devolucao_sem_foto": "falta foto na devolução",
    "devolucao_motivo_sem_chamado": "motivo não abre chamado nessa plataforma",
    "devolucao_sem_pedido_marketplace": "falta o nº do pedido no marketplace",
    "devolucao_sem_claim": "sem reclamação aberta no ML",
    "devolucao_nao_encontrada": "devolução não encontrada",
    "shopee_captcha_humano": "formulário pronto — falta o quebra-cabeça",
    "plataforma_sem_api": "tarefa da equipe (plataforma sem API)",
    "plataforma_sem_api_replica": "réplica sem API — responder no Seller Center",
    "sem_perfil_adspower": "conta sem perfil no AdsPower",
    "perfil_deslogado": "perfil do AdsPower deslogado",
    "substituida_pelo_robo": "o robô assumiu por outro caminho",
}
# Falha que o acompanhamento já segue (contestada à mão, prazo, caso encerrado)
# ou que o robô substituiu por outro caminho (19/09): não é "envio falhou".
ERROS_ACOMPANHADOS = frozenset({
    "shopee_ja_contestada", "shopee_prazo_contestacao_esgotado", "shopee_devolucao_encerrada",
    "tiktok_ja_recusada", "tiktok_devolucao_encerrada", "ml_claim_encerrada",
    "ml_claim_encerrada_sem_prejuizo", "substituida_pelo_robo",
})


def _erro_pede_humano(erro: str) -> bool:
    e = (erro or "").strip()
    # o robô da página do ML grava a frase inteira ("tem foto anexada: …")
    return e in ERROS_PEDEM_HUMANO or e.startswith("tem foto anexada")


def _robo_atende(ch: Chamado) -> bool:
    """Tem robô que responde por este chamado? Canal `robo` sim; canal `manual`
    do ML também (o cérebro assume os manuais do ML — Eduardo 09/09). Canal `api`
    (devolução Shopee/TikTok/ML pela API) e manual de outra plataforma não têm robô
    que responda: quando a plataforma fala neles, é gente (Seller Center)."""
    return ch.canal == "robo" or (ch.canal == "manual" and _eh_ml(ch))


class ChamadoError(Exception):
    """Falha de negócio com código legível pro endpoint (422)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------- pedido


async def situacoes_nomes(session: AsyncSession) -> list[str]:
    """Nomes das situações de pedido que EXISTEM no Bling hoje (catálogo
    `situacao_bling`, só `ativo`). As que o Bling apagou ficam no catálogo
    (pedidos antigos ainda apontam pra elas) mas somem dos dropdowns —
    Eduardo 15/09: "tem situação ali que nem existe mais, ex. Enviado Geral CI"."""
    nomes = (
        (
            await session.execute(
                select(SituacaoBling.nome)
                .where(SituacaoBling.ativo.is_(True))
                .distinct()
                .order_by(SituacaoBling.nome)
            )
        )
        .scalars()
        .all()
    )
    return [n for n in nomes if n]


async def status_bling_atual_map(session: AsyncSession, numeros: set[str]) -> dict[str, str]:
    """Situação ATUAL do pedido no espelho bling_orders, por número (nome via
    situacao_bling; fallback no id cru). Em lote — a listagem tem N linhas."""
    limpos = {n for n in numeros if n}
    if not limpos:
        return {}
    rows = await session.execute(
        select(
            BlingOrder.numero,
            func.max(func.coalesce(SituacaoBling.nome, BlingOrder.situacao)),
        )
        .join(SituacaoBling, cast(SituacaoBling.id, Text) == BlingOrder.situacao, isouter=True)
        .where(BlingOrder.numero.in_(list(limpos)))
        .group_by(BlingOrder.numero)
    )
    return {str(numero): nome for numero, nome in rows.all() if numero and nome}


async def lookup_pedido(session: AsyncSession, pedido: str) -> dict | None:
    """Dados do pedido pra preencher a linha: casa `numero` (Bling) OU
    `numeroloja` (marketplace). Produto/SKU = itens do pedido deduplicados,
    juntados por "; " / ", ". None se o pedido não está no espelho."""
    pedido = (pedido or "").strip()
    if not pedido:
        return None
    rows = list(
        (
            await session.execute(
                select(BlingOrder)
                .where(or_(BlingOrder.numero == pedido, BlingOrder.numeroloja == pedido))
                .order_by(BlingOrder.data.desc().nulls_last(), BlingOrder.item_index)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    first = rows[0]
    # O espelho pode ter mais de um pedido com o mesmo numeroloja (raro):
    # fica só com os itens do pedido mais recente.
    itens = [r for r in rows if r.numero == first.numero]
    produtos: list[str] = []
    skus: list[str] = []
    for r in itens:
        if r.item_descricao and r.item_descricao not in produtos:
            produtos.append(r.item_descricao)
        if r.item_codigo and r.item_codigo not in skus:
            skus.append(r.item_codigo)

    store = None
    if first.loja:
        store = (
            await session.execute(
                select(StoreInfo).where(StoreInfo.bling_store_id == str(first.loja)).limit(1)
            )
        ).scalar_one_or_none()

    situacao_nome = None
    if first.situacao:
        situacao_nome = (
            await session.execute(
                select(SituacaoBling.nome).where(cast(SituacaoBling.id, Text) == first.situacao)
            )
        ).scalar_one_or_none()

    return {
        "data": first.data.astimezone(SAO_PAULO).date() if first.data else None,
        "pedido_bling": first.numero,
        "pedido_marketplace": first.numeroloja,
        "plataforma": (store.platform if store else None),
        "conta": (store.account_name if store else None),
        "produto": "; ".join(produtos) or None,
        "sku": ", ".join(skus) or None,
        "status_bling": situacao_nome or first.situacao,
    }


async def preencher_do_pedido(session: AsyncSession, ch: Chamado) -> bool:
    """Completa os campos VAZIOS do chamado com o espelho do pedido. Nunca
    sobrescreve o que o operador digitou. True se achou o pedido."""
    info = await lookup_pedido(session, ch.pedido_bling or ch.pedido_marketplace or "")
    if not info:
        return False
    for campo, valor in info.items():
        if valor is None:
            continue
        if getattr(ch, campo) in (None, ""):
            setattr(ch, campo, valor)
    return True


# ---------------------------------------------------------------- histórico


def nova_mensagem(
    ch: Chamado,
    *,
    texto: str,
    tipo: str,
    direcao: str = "enviada",
    autor_nome: str | None = None,
    autor_id: UUID | None = None,
    status: str = "registrada",
) -> ChamadoMensagem:
    return ChamadoMensagem(
        chamado_id=ch.id,
        direcao=direcao,
        tipo=tipo,
        texto=texto,
        canal=ch.canal,
        status=status,
        autor_nome=autor_nome,
        autor_id=autor_id,
    )


def registrar_sistema(ch: Chamado, texto: str) -> ChamadoMensagem:
    """Evento do sistema no histórico (status alterado, resolvido, etc.)."""
    return nova_mensagem(
        ch, texto=texto, tipo="sistema", direcao="sistema", autor_nome=AUTOR_SISTEMA
    )


# ---------------------------------------------------------------- devolução

# Motivos de devolução que ABREM CHAMADO SOZINHOS na aba Chamados (Eduardo
# 03/09: "mudou de ideia … tem que abrir chamado sozinho / oque for golpe abre
# chamado sozinho / quando for item faltando … tbm / Não recebido, abrir
# chamado / danificado a mesma coisa"). Comparação case-insensitive pra
# aguentar variação de digitação em linhas antigas (motivo é texto livre).
# "Bloqueado" é o nome novo de "Mudou de ideia" (03/09: "mudou de ideia -
# bloqueado"; migration 0239 renomeou as linhas antigas) — o legado fica na
# lista por segurança. "Item Incorreto" entrou 04/09 e SAIU 07/09 (Eduardo:
# "para item incorreto, não deve abrir chamado" — produto errado enviado por
# nós não tem o que contestar; a Shopee nem oferece motivo de disputa).
MOTIVOS_ABREM_CHAMADO = frozenset(
    {
        "bloqueado",
        "mudou de ideia",
        "golpe",
        "item faltando",
        "não recebido",
        "danificado (outros)",
    }
)


def motivo_pede_chamado(dev: Devolution) -> bool:
    return (dev.motivo_devolucao or "").strip().lower() in MOTIVOS_ABREM_CHAMADO


# Observação gravada na abertura automática. Quem troca o motivo depois
# (`atualizar_observacao_motivo`) só reescreve se a observação ainda for essa —
# texto editado por pessoa na aba fica como está.
OBS_AUTO_PREFIXO = "Aberto automaticamente pela devolução — motivo: "


def atualizar_observacao_motivo(ch: Chamado, de: str, para: str) -> bool:
    """Reflete o motivo novo da devolução na observação do chamado quando ela
    ainda é a automática (Vinicius 21/09: a observação ficava congelada em
    "Não recebido" depois da troca pra "Danificado"). Troca SÓ o trecho do
    motivo anterior (`de`), preservando o que vier depois — " (substitui o
    chamado …)" ou uma linha anexada por pessoa/robô; se a observação não começa
    pelo texto automático + motivo anterior, não mexe."""
    obs = ch.observacao or ""
    if not obs.startswith(OBS_AUTO_PREFIXO):
        return False
    resto = obs[len(OBS_AUTO_PREFIXO):]
    de = (de or "").strip()
    if not de or not resto.lower().startswith(de.lower()):
        return False
    novo = f"{OBS_AUTO_PREFIXO}{para}{resto[len(de):]}"
    if novo == obs:
        return False
    ch.observacao = novo
    return True


async def chamado_da_devolucao(session: AsyncSession, dev: Devolution) -> Chamado | None:
    """Chamado de origem `devolucao` já registrado pra essa linha: por pedido
    Bling (kit com 3 linhas = 1 chamado) ou, sem pedido, pelo id da linha
    (`origem_ref`). O mais recente quando houver mais de um."""
    conds = [Chamado.origem == "devolucao"]
    if (dev.pedido_bling or "").strip():
        conds.append(Chamado.pedido_bling == dev.pedido_bling.strip())
    else:
        conds.append(Chamado.origem_ref == str(dev.id))
    return (
        await session.execute(
            select(Chamado).where(*conds).order_by(Chamado.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def abrir_chamado_devolucao(
    session: AsyncSession,
    dev: Devolution,
    *,
    substituindo: Chamado | None = None,
    motivo_anterior: str | None = None,
) -> Chamado | None:
    """Abre (registra) automaticamente um chamado de origem `devolucao` quando
    o motivo da devolução pede chamado. Aqui só REGISTRA na aba Chamados (canal
    `manual`); quem leva pro Mercado Livre — revisão da devolução com problema,
    com as fotos da linha — é `services/chamados_devolucao.garantir_chamado`,
    que troca o canal pra `api` quando a conta é ML.

    Dedupe: UM chamado de devolução por pedido Bling (kit com 3 linhas de
    devolução não vira 3 chamados); sem pedido Bling, cai pro id da linha
    (`origem_ref`). `substituindo` (Vinicius 21/09, troca de motivo): o caller
    acabou de pôr aquele chamado em Encerrado e quer OUTRO no lugar — pula o
    dedupe e anota a substituição. NÃO commita — o caller controla a transação.
    Devolve o chamado criado, ou None quando o motivo não pede/já existe."""
    motivo = (dev.motivo_devolucao or "").strip()
    if motivo.lower() not in MOTIVOS_ABREM_CHAMADO:
        return None
    if substituindo is None and await chamado_da_devolucao(session, dev) is not None:
        return None
    ch = Chamado(
        data=datetime.now(SAO_PAULO).date(),
        pedido_bling=(dev.pedido_bling or "").strip() or None,
        pedido_marketplace=(dev.pedido_marketplace or "").strip() or None,
        conta=dev.conta,
        produto=dev.produtos,
        sku=dev.sku,
        origem="devolucao",
        origem_ref=str(dev.id),
        canal="manual",
        observacao=f"{OBS_AUTO_PREFIXO}{motivo}",
    )
    # Espelho do pedido completa o que a devolução não tem (plataforma/status
    # Bling/data) sem sobrescrever o que veio dela.
    await preencher_do_pedido(session, ch)
    session.add(ch)
    await session.flush()
    evento = f"Chamado aberto automaticamente pela devolução (motivo: {motivo})"
    if substituindo is not None:
        de = (motivo_anterior or "").strip() or "—"
        ch.observacao = f"{ch.observacao} (substitui o chamado anterior, motivo \"{de}\")"
        evento += f' — substitui o chamado anterior (motivo "{de}"), que ficou Encerrado'
    session.add(registrar_sistema(ch, evento))
    logger.info(
        "chamado_auto_devolucao",
        chamado_id=str(ch.id),
        devolution_id=str(dev.id),
        pedido_bling=ch.pedido_bling,
        motivo=motivo,
        substitui=str(substituindo.id) if substituindo is not None else None,
    )
    return ch


# ---------------------------------------------------------------- logística


async def chamado_da_logistica(session: AsyncSession, row: Logistica) -> Chamado | None:
    """Chamado já registrado pra esta linha da Logística (origem `logistica`):
    pela referência da linha ou, sem ela, pelo pedido Bling."""
    conds = [Chamado.origem == "logistica"]
    numero = (row.pedido_bling or "").strip()
    if numero:
        conds.append(or_(Chamado.origem_ref == str(row.id), Chamado.pedido_bling == numero))
    else:
        conds.append(Chamado.origem_ref == str(row.id))
    return (
        await session.execute(
            select(Chamado).where(*conds).order_by(Chamado.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def abertura_do_chamado(session: AsyncSession, ch: Chamado) -> ChamadoMensagem | None:
    """Mensagem de abertura (tipo `abertura`) do chamado, se já existe."""
    return (
        await session.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura")
            .order_by(ChamadoMensagem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def abrir_chamado_logistica(
    session: AsyncSession,
    row: Logistica,
    *,
    mensagem: str,
    claim_id: str | None = None,
    regra: str | None = None,
    autor_nome: str | None = None,
    anexos: list | None = None,
) -> Chamado:
    """Registra na aba Chamados o chamado da Logística (motor do recarregar ou
    botão da linha) — Eduardo 07/09: "lembra que vai para a aba chamados" e
    "se tiver disponível pela venda, faz pela venda; se não, pelo formulário".

    Dois caminhos:
    - `claim_id` dado → já abriu PELA VENDA (mediação do ML via API): canal
      `api`, nº = claim_id (o cron fecha quando o ML encerrar), histórico com
      o evento do sistema + abertura `enviada`.
    - `claim_id` None → PELO FORMULÁRIO: canal `robo`, abertura `pendente` com
      o texto da regra (+ as imagens da regra como anexos) → vira tarefa
      `abrir` no `/agent/lease`; o robô do Mac abre no formulário de ajuda do
      ML e devolve o protocolo pelo `/agent/resultado`.
    Dedupe: chamado de origem `logistica` já existente pra linha (ou pedido) é
    reaproveitado; abertura já pendente/enviando não é duplicada. NÃO commita."""
    ch = await chamado_da_logistica(session, row)
    novo = ch is None
    if ch is None:
        ch = Chamado(
            data=datetime.now(SAO_PAULO).date(),
            pedido_bling=(row.pedido_bling or "").strip() or None,
            pedido_marketplace=(row.pedido_marketplace or "").strip() or None,
            plataforma=row.plataforma,
            conta=row.conta,
            status_bling=row.status_bling,
            origem="logistica",
            origem_ref=str(row.id),
            canal="api" if claim_id else "robo",
            observacao=(
                f"Aberto automaticamente pela Logística — regra: {regra}"
                if regra
                else "Aberto pela Logística"
            ),
        )
        await preencher_do_pedido(session, ch)
        session.add(ch)
        await session.flush()
    quem = autor_nome or AUTOR_SISTEMA
    if claim_id:
        ch.chamado = str(claim_id)
        ch.canal = "api"
        session.add(
            registrar_sistema(
                ch,
                (
                    f"Chamado aberto na mediação do Mercado Livre pela Logística"
                    f"{' (regra: ' + regra + ')' if regra else ''}; referência {claim_id}"
                ),
            )
        )
        abertura = nova_mensagem(
            ch,
            texto=mensagem,
            tipo="abertura",
            direcao="enviada",
            autor_nome=quem,
            status="enviada",
        )
        abertura.enviada_at = datetime.now(UTC)
        session.add(abertura)
    else:
        existente = await abertura_do_chamado(session, ch)
        if existente is not None and existente.status in ("pendente", "enviando", "enviada"):
            return ch  # robô já está com a tarefa (ou já abriu)
        ch.canal = "robo"
        ch.plataforma = ch.plataforma or row.plataforma
        if (row.plataforma or "").strip().lower() in logistica_meli._ML_PLATAFORMAS:
            aviso = (
                "Pedido sem reclamação aberta pelo comprador — chamado encaminhado ao "
                "robô do formulário de ajuda do Mercado Livre"
            )
        else:
            # TikTok/Shopee não têm API pra loja abrir reclamação: a regra da
            # aba Status manda direto pro robô abrir no Seller Center
            # (Vinicius 16/09: "se chegar no painel de chamado, o robô abre").
            aviso = (
                f"{(row.plataforma or 'Plataforma').strip()} não tem API pra abrir "
                "chamado pela venda — encaminhado ao robô pra abrir no Seller Center"
            )
        session.add(
            registrar_sistema(
                ch, aviso + (f" (regra: {regra})" if regra else "")
            )
        )
        abertura = nova_mensagem(
            ch,
            texto=mensagem,
            tipo="abertura",
            direcao="enviada",
            autor_nome=quem,
            status="pendente",
        )
        abertura.canal = "robo"
        session.add(abertura)
        await session.flush()
        for a in anexos or []:
            session.add(
                ChamadoAnexo(
                    chamado_id=ch.id,
                    mensagem_id=abertura.id,
                    filename=a.filename,
                    content_type=a.content_type,
                    size_bytes=a.size_bytes,
                    blob=a.blob,
                )
            )
    logger.info(
        "chamado_auto_logistica",
        chamado_id=str(ch.id),
        logistica_id=str(row.id),
        pedido_bling=ch.pedido_bling,
        claim_id=claim_id,
        canal=ch.canal,
        novo=novo,
    )
    return ch


# ---------------------------------------------------------------- envio


def _eh_ml(ch: Chamado) -> bool:
    return (ch.plataforma or "").strip().lower() in logistica_meli._ML_PLATAFORMAS


async def _ml_client_para(session: AsyncSession, conta: str | None) -> MercadoLivreClient:
    integ = await logistica_meli._ml_integration_for_conta(session, conta)
    if integ is None:
        raise ChamadoError("chamado_sem_integracao_ml")
    return logistica_meli._build_ml_client(session, integ)


async def _enviar_api_ml(session: AsyncSession, ch: Chamado, texto: str) -> None:
    """Manda `texto` na reclamação (claim) do pedido via API do ML. Fala com o
    mediador quando a mediação está aberta; senão com o comprador."""
    if not (ch.chamado or "").strip():
        raise ChamadoError("chamado_sem_numero")
    if not _eh_ml(ch):
        raise ChamadoError("chamado_nao_ml")
    try:
        client = await _ml_client_para(session, ch.conta)
    except ChamadoError:
        # 15/09: chamado de devolução guarda a conta como NOME DA LOJA ("ML Aguiar"),
        # que não é o nome da integração ("aguiar") — a réplica manual falhava com
        # chamado_sem_integracao_ml. Mesmo resolvedor do sync (contas candidatas).
        from uuid import UUID

        from app.models import Devolution
        from app.services import chamados_devolucao as cd  # lazy: cd importa este módulo

        dev = None
        if ch.origem_ref:
            try:
                dev = await session.get(Devolution, UUID(str(ch.origem_ref)))
            except ValueError:
                dev = None
        dev = dev or Devolution(
            conta=ch.conta or "", pedido_bling=ch.pedido_bling,
            pedido_marketplace=ch.pedido_marketplace,
        )
        client = await cd._ml_client_para(session, ch, dev)
    claim = await client.get_claim(ch.chamado.strip())
    if (claim.get("status") or "").lower() == "closed":
        raise ChamadoError("chamado_encerrado")
    actions = logistica_meli._respondent_actions(claim)
    if "send_message_to_mediator" in actions:
        role = "mediator"
    elif "send_message_to_complainant" in actions:
        role = "complainant"
    else:
        raise ChamadoError("chamado_sem_acao")
    await client.send_claim_message(ch.chamado.strip(), texto, receiver_role=role)


async def enviar_mensagem(
    session: AsyncSession, ch: Chamado, msg: ChamadoMensagem
) -> ChamadoMensagem:
    """Despacha a mensagem pelo canal do chamado e atualiza status/erro/
    enviada_at nela. NÃO commita — o caller controla a transação. Nunca
    levanta: falha vira `status='falhou'` + `erro` (o histórico mostra)."""
    msg.canal = ch.canal
    if ch.canal == "manual":
        msg.status = "registrada"
    elif ch.canal == "robo":
        # Fila do robô de browser (formulário/protocolo). Ele marca enviada.
        msg.status = "pendente"
    elif (ch.origem_ref or "").startswith("tiktok_reembolso:"):
        # Só reembolso da TikTok (Eduardo 16/09): a réplica é a contestação.
        from app.services import chamados_tiktok_reembolso  # lazy: ele importa este módulo

        await chamados_tiktok_reembolso.contestar(session, ch, msg)
    elif ch.origem == "devolucao" and not _eh_ml(ch):
        # Shopee/TikTok: sem API de mensagem na disputa — a réplica reabre a
        # abertura (se ainda não saiu) ou fica só no histórico (Eduardo 07/09:
        # "todos falharam em chamados, isso não pode").
        from app.services import chamados_devolucao

        try:
            await chamados_devolucao.replicar_devolucao(session, ch, msg)
        except Exception as e:  # noqa: BLE001
            msg.status = "falhou"
            msg.erro = str(e)[:300]
            logger.warning("chamado_replica_devolucao_falhou", chamado_id=str(ch.id), err=msg.erro)
    else:
        try:
            await _enviar_api_ml(session, ch, msg.texto)
            msg.status = "enviada"
            msg.enviada_at = datetime.now(UTC)
        except ChamadoError as e:
            msg.status = "falhou"
            msg.erro = e.code
        except Exception as e:  # noqa: BLE001 — erro cru da API do ML
            msg.status = "falhou"
            msg.erro = str(e)[:300]
            logger.warning("chamado_envio_api_falhou", chamado_id=str(ch.id), err=msg.erro)
    return msg


# ---------------------------------------------------------------- Bling


async def _bling_order_id(session: AsyncSession, ch: Chamado) -> int:
    numero = (ch.pedido_bling or "").strip()
    if not numero:
        raise ChamadoError("chamado_sem_pedido_bling")
    bid = (
        await session.execute(
            select(BlingOrder.bling_id)
            .where(BlingOrder.numero == numero, BlingOrder.bling_id.isnot(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if bid is None:
        raise ChamadoError("chamado_pedido_bling_nao_achado")
    return int(bid)


async def aplicar_status_bling(session: AsyncSession, ch: Chamado, nome: str) -> dict:
    """PATCH da situação do pedido no Bling (endpoint dedicado, reversível) e
    sincroniza o snapshot `status_bling` + histórico. NÃO commita."""
    nome = (nome or "").strip()
    if not nome:
        raise ChamadoError("chamado_sem_status_bling")
    sid = await logistica_bling._situacao_id_por_nome_opt(session, nome)
    if sid is None:
        raise ChamadoError("chamado_status_bling_desconhecido")
    bling_id = await _bling_order_id(session, ch)
    try:
        client = await logistica_bling._bling_client(session)
    except logistica_bling.BlingObsError as e:
        raise ChamadoError("chamado_sem_integracao_bling") from e
    # False = o pedido JÁ estava nessa situação (o Bling recusa repetir com 400
    # "mesma situação"; o cliente engole). Pro chamado é o mesmo desfecho —
    # Vinicius, 21/09/2026: resolver o 295680 já Resolvido travava na tela.
    mudou = await client.update_order_situacao(bling_id, sid) is not False
    # Nome do catálogo pro id realmente aplicado: regra escrita com o apelido
    # legado "Enviado Etiqueta" move pra 21 → snapshot/histórico dizem
    # "Em digitação" (mesmo ajuste de logistica_bling.apply_alterar_status_bling).
    nome_aplicado = await logistica_bling._situacao_nome_por_id(session, sid) or nome
    ch.status_bling = nome_aplicado
    texto = (
        f"Status Bling alterado para {nome_aplicado}"
        if mudou
        else f"Status Bling já era {nome_aplicado} (mantido)"
    )
    session.add(registrar_sistema(ch, texto))
    return {"bling_order_id": bling_id, "situacao": nome_aplicado, "situacao_id": sid}


# ---------------------------------------------------------------- resolvido


def resultado_texto(valor: Decimal | None) -> str:
    """Texto do resultado financeiro do chamado (coluna "Valor" do Controle):
    positivo = lucro, negativo = prejuízo, zero = empate. None = sem valor."""
    if valor is None:
        return ""
    v = Decimal(valor)
    moeda = f"R$ {abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if v > 0:
        return f"lucro de {moeda}"
    if v < 0:
        return f"prejuízo de {moeda}"
    return "sem lucro nem prejuízo (R$ 0,00)"


def marcar_resolvido(
    ch: Chamado,
    resolvido: bool,
    *,
    autor_nome: str | None = None,
    valor: Decimal | None = None,
) -> ChamadoMensagem:
    """Fecha (ou reabre) o chamado e devolve o evento do histórico. `valor` é
    o resultado do chamado (lucro/prejuízo em R$) — Eduardo 15/09: obrigatório
    ao resolver pela aba. 19/09 (Vinicius): fechar é só de PESSOA — o único
    caminho que chama com `resolvido=True` é o endpoint `/resolver`; o cérebro
    só reabre (`resolvido=False`) o que o monitor antigo fechou cedo demais."""
    agora = datetime.now(UTC)
    ch.resolvido = resolvido
    ch.resolvido_at = agora if resolvido else None
    if resolvido:
        # Réplica automática não faz sentido em chamado fechado.
        ch.auto_ligada = False
        if valor is not None:
            ch.valor_recuperado = valor
        quem = f" por {autor_nome}" if autor_nome else ""
        resultado = resultado_texto(valor)
        sufixo = f" — {resultado}" if resultado else ""
        return registrar_sistema(ch, f"Chamado marcado como resolvido{quem}{sufixo}")
    return registrar_sistema(ch, f"Chamado reaberto{(' por ' + autor_nome) if autor_nome else ''}")


# ---------------------------------------------------------------- status da aba


def set_status_plataforma(ch: Chamado, codigo: str, quando: datetime | None = None) -> bool:
    """Grava o status oficial da plataforma (e desde quando) se mudou. `quando`
    = hora da plataforma; sem ela, agora. Repetir o mesmo status não mexe na
    data (a coluna mostra "desde"); um status FINAL não volta pra intermediário
    (o sync roda de hora em hora e a API pode seguir dizendo "em análise" num
    caso já decidido)."""
    if ch.status_plataforma == codigo:
        return False
    if ch.status_plataforma in STATUS_FINAIS and codigo not in STATUS_FINAIS:
        return False
    ch.status_plataforma = codigo
    ch.status_plataforma_at = quando or datetime.now(UTC)
    return True


def status_da_aba(
    ch: Chamado,
    *,
    ultima_fala: ChamadoMensagem | None,
    ultima_analise: ChamadoMensagem | None,
    analise_pede_humano: bool,
    analise_pede_esperar: bool = False,
    instrucao_pendente: ChamadoMensagem | None = None,
    nossa_fala_apos_status: bool = False,
) -> tuple[str, datetime | None]:
    """(código, desde quando) da coluna Status — ver `status_e_motivo_da_aba`."""
    codigo, quando, _motivo = status_e_motivo_da_aba(
        ch,
        ultima_fala=ultima_fala,
        ultima_analise=ultima_analise,
        analise_pede_humano=analise_pede_humano,
        analise_pede_esperar=analise_pede_esperar,
        instrucao_pendente=instrucao_pendente,
        nossa_fala_apos_status=nossa_fala_apos_status,
    )
    return codigo, quando


def _texto_curto(texto: str | None, n: int) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _respondeu(ch: Chamado, quando: datetime | None) -> tuple[str, datetime | None, str]:
    """A plataforma falou por último e ninguém tratou: é do robô, se há robô
    pra este chamado; senão é gente (responder no Seller Center)."""
    if _robo_atende(ch):
        return ABA_ANALISE_ROBO, quando, "plataforma respondeu — o robô analisa"
    return ABA_ANALISE_HUMANO, quando, "plataforma respondeu — responder no Seller Center"


def status_e_motivo_da_aba(
    ch: Chamado,
    *,
    ultima_fala: ChamadoMensagem | None,
    ultima_analise: ChamadoMensagem | None,
    analise_pede_humano: bool,
    analise_pede_esperar: bool = False,
    instrucao_pendente: ChamadoMensagem | None = None,
    nossa_fala_apos_status: bool = False,
) -> tuple[str, datetime | None, str | None]:
    """(código ABA_*, desde quando, motivo curto) que a coluna Status mostra.

    `nossa_fala_apos_status` (19/09): a listagem olha o histórico inteiro e diz se
    existe ALGUMA fala nossa que saiu (`enviada`/`registrada`) depois do status
    oficial — sem isso a prova que já mandamos voltava a pedir humano assim que a
    Shopee respondia (a última fala passava a ser dela). Aqui, sem o kwarg, vale
    só a última fala (chamadas antigas/unitárias).

    Precedência (Vinicius 19/09 — cinco status; "primeiro o robô, gente só quando
    o robô desiste"):
      1. resolvido por pessoa → Concluído (motivo: ganhamos/perdemos/sem decisão
         + lucro/prejuízo);
      2. instrução nossa mais nova que a última análise → Análise Robô — de
         QUALQUER estado, inclusive Encerrado (Vinicius 21/09: "qualquer status
         que esteja, se eu mandar pro robô tem que ir pra análise dele"; até
         19/09 o Encerrado vinha antes e a linha não saía de lá);
      3. status oficial FINAL sem pessoa fechar → Encerrado (+ sugestão do robô);
      4. o cérebro pediu gente e ninguém falou depois → Análise Humano;
      5. Shopee pediu prova e ainda não mandamos NADA depois do pedido → Análise
         Humano (prova é humano — decisão do Vinicius); mandamos → cai na 7;
      6. a NOSSA última mensagem não saiu: o erro dela diz de quem é a vez —
         plataforma não libera → Análise Robô (ele procura outro caminho; se já
         leu e decidiu aguardar, Aguard. Plataforma); falta foto/quebra-cabeça/
         login/tarefa sem API → Análise Humano; sem erro → Análise Robô (na fila);
         falhou de vez (o retry do robô esgotou, §MAX_TENTATIVAS_ROBO) → Análise
         Humano;
      7. status oficial da API: resposta da plataforma mais nova que ele → quem
         responde (robô ou gente); senão Aguard. Plataforma com o motivo oficial;
      8. nenhuma fala no histórico → Análise Humano (registrado à mão);
      9. a plataforma falou por último → robô/gente, a menos que o robô já leu e
         decidiu aguardar;
     10. nós falamos por último → Aguard. Plataforma."""
    if ch.resolvido:
        motivo = MOTIVO_FINAL.get(ch.status_plataforma or "", "sem decisão da plataforma")
        resultado = resultado_texto(ch.valor_recuperado)
        if resultado:
            motivo = f"{motivo} — {resultado}"
        return ABA_CONCLUIDO, ch.resolvido_at, motivo
    if instrucao_pendente is not None:
        return (
            ABA_ANALISE_ROBO,
            instrucao_pendente.created_at,
            f"instrução pendente pro robô: {_texto_curto(instrucao_pendente.texto, 60)}",
        )
    if ch.status_plataforma in STATUS_FINAIS:
        motivo = MOTIVO_FINAL.get(ch.status_plataforma, "plataforma encerrou sem decisão")
        if ch.valor_sugerido is not None:
            motivo = f"{motivo} · robô sugere {resultado_texto(ch.valor_sugerido)}"
        return ABA_ENCERRADO, ch.status_plataforma_at, motivo
    fala_em = _quando(ultima_fala)
    analise_depois = (
        ultima_analise is not None and (fala_em is None or ultima_analise.created_at >= fala_em)
    )
    if analise_pede_humano and analise_depois:
        return ABA_ANALISE_HUMANO, ultima_analise.created_at, "o robô pediu revisão humana"
    nossa_fala_depois_do_status = nossa_fala_apos_status or (
        ultima_fala is not None
        and ultima_fala.direcao == "enviada"
        and fala_em is not None
        and (ch.status_plataforma_at is None or fala_em > ch.status_plataforma_at)
    )
    if ch.status_plataforma == STATUS_PROVA and not nossa_fala_depois_do_status:
        return ABA_ANALISE_HUMANO, ch.status_plataforma_at, "Shopee pediu prova adicional"
    if ultima_fala is not None and ultima_fala.direcao == "enviada":
        erro = (ultima_fala.erro or "").strip()
        st = ultima_fala.status
        if st in ("pendente", "enviando"):
            if erro in ERROS_ESPERA_PLATAFORMA:
                if analise_pede_esperar and analise_depois:
                    return (
                        ABA_AGUARD_PLATAFORMA,
                        ultima_analise.created_at,
                        f"{MOTIVO_DO_ERRO[erro]} — o robô decidiu aguardar",
                    )
                return (
                    ABA_ANALISE_ROBO,
                    fala_em,
                    f"plataforma não libera: {MOTIVO_DO_ERRO[erro]} — "
                    "robô procura outro caminho",
                )
            if _erro_pede_humano(erro):
                return ABA_ANALISE_HUMANO, fala_em, MOTIVO_DO_ERRO.get(erro, erro[:80])
            quem = "na fila do robô" if (ultima_fala.canal or "") == "robo" else "saindo pela API"
            tentativas = ultima_fala.tentativas or 0
            if erro and tentativas:
                quem = (
                    f"{quem} — tentativa {tentativas} de {MAX_TENTATIVAS_ROBO} falhou: "
                    f"{_texto_curto(erro, 60)}"
                )
            return ABA_ANALISE_ROBO, fala_em, quem
        if st == "falhou":
            if _erro_pede_humano(erro):
                return ABA_ANALISE_HUMANO, fala_em, MOTIVO_DO_ERRO.get(erro, erro[:80])
            if erro not in ERROS_ACOMPANHADOS:
                return (
                    ABA_ANALISE_HUMANO,
                    fala_em,
                    f"envio falhou: {erro[:80]}" if erro else "envio falhou",
                )
        if st == "registrada" and _erro_pede_humano(erro):
            return ABA_ANALISE_HUMANO, fala_em, MOTIVO_DO_ERRO.get(erro, erro[:80])
    if ch.status_plataforma:
        if (
            ultima_fala is not None
            and ultima_fala.direcao == "recebida"
            and fala_em is not None
            and (ch.status_plataforma_at is None or fala_em > ch.status_plataforma_at)
            and not (analise_pede_esperar and analise_depois)
        ):
            return _respondeu(ch, fala_em)
        return (
            ABA_AGUARD_PLATAFORMA,
            ch.status_plataforma_at,
            MOTIVO_STATUS_OFICIAL.get(ch.status_plataforma),
        )
    if ultima_fala is None:
        return ABA_ANALISE_HUMANO, None, "registrado à mão — acompanhar no site"
    if ultima_fala.direcao == "recebida":
        if analise_pede_esperar and analise_depois:
            return (
                ABA_AGUARD_PLATAFORMA,
                ultima_analise.created_at,
                "o robô leu a resposta e decidiu aguardar",
            )
        return _respondeu(ch, fala_em)
    return ABA_AGUARD_PLATAFORMA, fala_em, None


def _quando(m: ChamadoMensagem | None) -> datetime | None:
    if m is None:
        return None
    return m.enviada_at or m.created_at


def ml_beneficiado(claim: dict) -> str:
    """`resolution.benefited` do claim (string ou lista) em minúsculas."""
    benef = (claim.get("resolution") or {}).get("benefited")
    if isinstance(benef, list):
        benef = benef[0] if benef else None
    return str(benef or "").strip().lower()


def ml_status_encerrado(claim: dict) -> str:
    """Status da aba pra um claim do ML fechado: quem o ML beneficiou."""
    return {
        "respondent": STATUS_GANHAMOS,
        "complainant": STATUS_PERDEMOS,
    }.get(ml_beneficiado(claim), STATUS_ENCERRADO)


def ml_quando(claim: dict) -> datetime | None:
    """Hora da última mudança do claim (resolução, senão last_updated)."""
    from app.services.devolucao_returns import iso_to_dt  # lazy: evita import circular

    res = claim.get("resolution") or {}
    return iso_to_dt(res.get("date_created") or claim.get("last_updated") or claim.get("date_created"))


# ---------------------------------------------------------------- cron


def auto_proximo_envio(ch: Chamado) -> datetime | None:
    """Quando a próxima réplica automática sai (ou None se desligada). Base =
    último envio automático; ao ligar, o PATCH carimba `auto_ultimo_envio_at`
    pra primeira réplica só sair depois de N dias. 19/09: chamado Encerrado
    (a plataforma já decidiu) também não tem próxima — não adianta cobrar."""
    if not ch.auto_ligada or not ch.auto_dias or ch.resolvido:
        return None
    if ch.status_plataforma in STATUS_FINAIS:
        return None
    base = ch.auto_ultimo_envio_at or ch.created_at
    if base is None:
        return None
    return base + timedelta(days=ch.auto_dias)


async def run_replica_automatica(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    """Passada do cron (de hora em hora):
    1. réplica automática ligada + vencida → cria a mensagem no histórico e
       despacha pelo canal; carimba `auto_ultimo_envio_at` (mesmo se falhou —
       a próxima tentativa é dali a N dias, sem spammar o histórico);
    2. TODO chamado aberto de canal API do ML (com nº do claim) → se o ML já
       encerrou o claim, o chamado vai pro estado Encerrado (status oficial
       ganhamos/perdemos/encerrado + evento) e fica esperando uma pessoa fechar
       — 19/09 (Vinicius): nada fecha sozinho. Não depende de flag nenhuma
       (Eduardo 15/09: o robô acompanha todos). Chamado já Encerrado não é
       consultado de novo.
    Best-effort por linha: falha de uma não derruba as outras."""
    agora = agora or datetime.now(UTC)
    rows = list(
        (
            await session.execute(
                select(Chamado).where(
                    Chamado.resolvido.is_(False),
                    # 19/09: Encerrado (a plataforma decidiu) não recebe réplica
                    # automática nem volta a ser consultado no ML.
                    NAO_ENCERRADO_SQL,
                    or_(Chamado.auto_ligada.is_(True), Chamado.canal == "api"),
                )
            )
        )
        .scalars()
        .all()
    )
    enviados = encerrados = falhas = 0
    for ch in rows:
        texto = (ch.auto_mensagem or "").strip()
        if ch.auto_ligada and ch.auto_dias and texto:
            proximo = auto_proximo_envio(ch)
            if proximo is None or proximo <= agora:
                msg = nova_mensagem(ch, texto=texto, tipo="replica_auto", autor_nome=AUTOR_AUTO)
                session.add(msg)
                await enviar_mensagem(session, ch, msg)
                ch.auto_ultimo_envio_at = agora
                enviados += 1
                if msg.status == "falhou":
                    falhas += 1
        if (
            ch.canal == "api"
            and (ch.chamado or "").strip()
            and _eh_ml(ch)
            and ch.status_plataforma not in STATUS_FINAIS
        ):
            try:
                client = await _ml_client_para(session, ch.conta)
                claim = await client.get_claim(ch.chamado.strip())
                if (claim.get("status") or "").lower() == "closed":
                    ch.auto_ligada = False
                    set_status_plataforma(ch, ml_status_encerrado(claim), ml_quando(claim))
                    session.add(
                        registrar_sistema(
                            ch, "Reclamação encerrada no Mercado Livre — aguardando fechamento"
                        )
                    )
                    encerrados += 1
                elif (claim.get("stage") or "").lower() == "dispute":
                    set_status_plataforma(ch, STATUS_EM_ANALISE, ml_quando(claim))
            except Exception as e:  # noqa: BLE001
                falhas += 1
                logger.warning(
                    "chamado_monitoramento_falhou", chamado_id=str(ch.id), err=str(e)[:300]
                )
    await session.commit()
    return {
        "verificados": len(rows),
        "enviados": enviados,
        "encerrados": encerrados,
        "falhas": falhas,
    }
