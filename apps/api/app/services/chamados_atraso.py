"""Chamados de ATRASO NA POSTAGEM em lote — Controle de Estoque › Pedidos.

Eduardo, 15/09/2026: "um botão onde eu vou selecionar todos os pedidos e o
sistema abre um chamado em cada loja — não precisa ser um chamado para cada
pedido, pode juntar: todos ML Aguiar num único chamado. Informando que tivemos
um problema de atraso: não conseguimos postar devido a fila, se for poucos
minutos; se for depois de muito tempo do corte, dentro do mesmo dia, é
problema de falta de energia."

Regras (combinadas 15/09):
- atraso = postagem confirmada (ledger `bling_envio_evento`) − "despachar até"
  do marketplace (`bling_orders.marketplace_ship_deadline`);
- até `LIMITE_FILA_MIN` (60) minutos depois do corte → `fila`; mais que isso,
  no MESMO dia (BRT) → `energia`. Postado no dia seguinte, ainda não postado,
  sem corte ou no prazo fica FORA (o botão antigo de "pedido parado" continua
  valendo pro que não foi postado);
- um chamado por LOJA (conta do marketplace). Loja com os dois motivos no
  mesmo dia recebe UM chamado só, com cada pedido no seu bloco;
- Mercado Livre: canal `robo` — a abertura fica `pendente` e o robô do
  formulário de ajuda do ML abre e devolve o protocolo. Shopee/TikTok/Amazon:
  não têm API nem robô pra chamado de suporte — o chamado nasce `manual` com
  o texto pronto (abertura `registrada`, erro `plataforma_sem_api`) pra alguém
  colar no Seller Center;
- cada pedido fica ligado ao chamado em `chamado_pedidos` (aba Pedidos mostra
  o chamado na linha; pedido com chamado de atraso em aberto não entra de novo).

Os textos foram aprovados pelo Eduardo em 15/09 e ficam editáveis na tela de
conferência antes de enviar.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingEnvioEvento,
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    ChamadoPedido,
    StoreInfo,
    User,
)
from app.models.company import Store
from app.models.integration import Integration
from app.services import chamados as chamados_svc
from app.services.logistica_rules import _ML_PLATAFORMAS
from app.services.sku_tags import classify_sku_tag

logger = structlog.get_logger()

BRT = ZoneInfo("America/Sao_Paulo")

LIMITE_FILA_MIN = 60
MOTIVO_FILA = "fila"
MOTIVO_ENERGIA = "energia"
MOTIVOS = (MOTIVO_FILA, MOTIVO_ENERGIA)
MOTIVO_LABEL = {MOTIVO_FILA: "fila na postagem", MOTIVO_ENERGIA: "queda de energia"}

ORIGEM = "logistica"
REGRA = "atraso na postagem (Controle de Estoque)"

TEXTO_FILA = (
    "Olá, equipe. Entramos em contato sobre {n} pedido(s) desta conta com despacho "
    "previsto para {data}, listados abaixo.\n\n"
    "Todos foram embalados e tiveram a etiqueta gerada dentro do prazo. A confirmação "
    "da postagem, porém, ficou alguns minutos além do horário de corte por causa da fila "
    "na agência no momento da entrega dos pacotes. Os volumes foram entregues à "
    "transportadora no mesmo dia e já seguem para os compradores.\n\n"
    "Como o atraso foi de poucos minutos e não partiu de falha na separação, pedimos que "
    "ele não seja considerado nos indicadores de reputação da loja.\n\n"
    "Pedidos:\n{linhas}\n\n"
    "Obrigado pela atenção."
)

TEXTO_ENERGIA = (
    "Olá, equipe. Entramos em contato sobre {n} pedido(s) desta conta com despacho "
    "previsto para {data}, listados abaixo.\n\n"
    "Em {data} tivemos uma queda de energia elétrica na nossa operação, que interrompeu "
    "a impressão de etiquetas e a expedição por algumas horas. Assim que o fornecimento "
    "foi restabelecido, retomamos a separação e todos os pedidos foram postados ainda no "
    "mesmo dia, embora depois do horário de corte.\n\n"
    "O atraso foi causado por um evento fora do nosso controle e os pacotes já estão a "
    "caminho dos compradores. Pedimos que ele não seja considerado nos indicadores de "
    "reputação da loja.\n\n"
    "Pedidos:\n{linhas}\n\n"
    "Obrigado pela atenção."
)

TEXTO_MISTO = (
    "Olá, equipe. Entramos em contato sobre {n} pedidos desta conta com despacho "
    "previsto para {data}, que tiveram a postagem confirmada depois do horário de "
    "corte.\n\n"
    "Parte deles ficou poucos minutos além do corte por causa da fila na agência no "
    "momento da entrega dos pacotes. Os demais atrasaram mais porque tivemos uma queda "
    "de energia elétrica na operação, que interrompeu a impressão de etiquetas e a "
    "expedição por algumas horas. Assim que o fornecimento voltou, tudo foi postado "
    "ainda no mesmo dia.\n\n"
    "Em todos os casos as etiquetas já estavam geradas e os pacotes foram entregues à "
    "transportadora no próprio dia. Pedimos que esses atrasos não sejam considerados "
    "nos indicadores de reputação da loja.\n\n"
    "Fila na postagem:\n{linhas_fila}\n\n"
    "Queda de energia:\n{linhas_energia}\n\n"
    "Obrigado pela atenção."
)


@dataclass
class PedidoAtraso:
    pedido_bling: str
    pedido_marketplace: str | None
    corte: str  # ISO (UTC)
    postagem: str  # ISO (UTC)
    atraso_min: int
    motivo: str


@dataclass
class Excluido:
    pedido_bling: str
    pedido_marketplace: str | None
    motivo: str
    detalhe: str


@dataclass
class Grupo:
    chave: str  # id da loja no Bling (string) — estável entre preview e abrir
    loja: str
    plataforma: str | None
    conta: str | None
    canal: str  # robo (ML) | manual (demais)
    pedidos: list[PedidoAtraso]
    texto: str


def eh_ml(plataforma: str | None) -> bool:
    return (plataforma or "").strip().lower() in _ML_PLATAFORMAS


def motivo_por_atraso(atraso_min: int) -> str:
    return MOTIVO_FILA if atraso_min <= LIMITE_FILA_MIN else MOTIVO_ENERGIA


def _hora(dt: datetime) -> str:
    return dt.astimezone(BRT).strftime("%H:%M")


def _data(dt: datetime) -> str:
    return dt.astimezone(BRT).strftime("%d/%m/%Y")


def _iso(v: str) -> datetime:
    return datetime.fromisoformat(v)


def linha_pedido(p: PedidoAtraso) -> str:
    return (
        f"• {p.pedido_marketplace or p.pedido_bling} — despachar até {_hora(_iso(p.corte))}, "
        f"postagem confirmada às {_hora(_iso(p.postagem))}"
    )


def render_texto(pedidos: list[PedidoAtraso]) -> str:
    """Texto do chamado pelo(s) motivo(s) dos pedidos: fila, energia ou misto."""
    if not pedidos:
        return ""
    datas = sorted({_data(_iso(p.corte)) for p in pedidos}, key=lambda d: d[6:] + d[3:5] + d[:2])
    data = " e ".join(datas) if len(datas) <= 2 else ", ".join(datas)
    motivos = {p.motivo for p in pedidos}
    fila = [linha_pedido(p) for p in pedidos if p.motivo == MOTIVO_FILA]
    energia = [linha_pedido(p) for p in pedidos if p.motivo == MOTIVO_ENERGIA]
    if motivos == {MOTIVO_FILA}:
        return TEXTO_FILA.format(n=len(pedidos), data=data, linhas="\n".join(fila))
    if motivos == {MOTIVO_ENERGIA}:
        return TEXTO_ENERGIA.format(n=len(pedidos), data=data, linhas="\n".join(energia))
    return TEXTO_MISTO.format(
        n=len(pedidos),
        data=data,
        linhas_fila="\n".join(fila),
        linhas_energia="\n".join(energia),
    )


async def _lojas(session: AsyncSession, loja_ids: set[str]) -> dict[str, dict]:
    """Por id da loja no Bling: plataforma/conta (store_info — é o que os
    chamados usam) e o rótulo da aba Pedidos (Store + Integration)."""
    out: dict[str, dict] = {str(k): {} for k in loja_ids}
    if not loja_ids:
        return out
    for si in (
        await session.execute(select(StoreInfo).where(StoreInfo.bling_store_id.in_(list(loja_ids))))
    ).scalars():
        k = str(si.bling_store_id)
        plat = si.platform.value if hasattr(si.platform, "value") else str(si.platform or "")
        out.setdefault(k, {})
        out[k]["plataforma"] = (plat or "").strip().lower() or None
        out[k]["conta"] = (si.account_name or "").strip() or None
    ints: set[int] = set()
    for k in loja_ids:
        try:
            ints.add(int(k))
        except (TypeError, ValueError):
            continue
    if ints:
        rows = (
            await session.execute(
                select(Store.bling_store_id, Integration.name, Integration.platform)
                .join(Integration, Integration.id == Store.integration_id, isouter=True)
                .where(Store.bling_store_id.in_(list(ints)))
            )
        ).all()
        for r in rows:
            k = str(r.bling_store_id)
            plat = (
                r.platform.value if hasattr(r.platform, "value") else str(r.platform or "")
            ).strip()
            label = (r.name or "").strip()
            out.setdefault(k, {})
            if plat and label:
                out[k]["loja"] = f"{plat.upper()} {label}"
            elif label:
                out[k]["loja"] = label
            if plat and not out[k].get("plataforma"):
                out[k]["plataforma"] = plat.lower()
            if label and not out[k].get("conta"):
                out[k]["conta"] = label
    return out


async def _com_chamado_aberto(session: AsyncSession, numeros: list[str]) -> dict[str, str]:
    """pedido → nº/id do chamado de atraso ainda ABERTO (não entra de novo)."""
    if not numeros:
        return {}
    rows = (
        await session.execute(
            select(ChamadoPedido.pedido_bling, Chamado.chamado, Chamado.id)
            .join(Chamado, Chamado.id == ChamadoPedido.chamado_id)
            .where(ChamadoPedido.pedido_bling.in_(numeros), Chamado.resolvido.is_(False))
        )
    ).all()
    return {r.pedido_bling: (r.chamado or str(r.id)[:8]) for r in rows}


async def montar(
    session: AsyncSession,
    numeros: list[str],
    *,
    tags: list[str] | None,
    motivos: dict[str, str] | None = None,
) -> dict:
    """Conferência: classifica cada pedido selecionado e monta um grupo por
    loja com o texto pronto. `motivos` = escolha manual da tela (pedido →
    fila|energia), que vence o cálculo. Não grava nada."""
    limpos: list[str] = []
    for n in numeros:
        n = (n or "").strip()
        if n and n not in limpos:
            limpos.append(n)
    motivos = {str(k).strip(): v for k, v in (motivos or {}).items() if v in MOTIVOS}
    excluidos: list[Excluido] = []
    if not limpos:
        return {"grupos": [], "excluidos": []}

    itens = (
        (
            await session.execute(
                select(BlingOrder)
                .where(BlingOrder.numero.in_(limpos))
                .order_by(
                    BlingOrder.numero, BlingOrder.data.desc().nulls_last(), BlingOrder.item_index
                )
            )
        )
        .scalars()
        .all()
    )
    por_numero: dict[str, list[BlingOrder]] = {}
    for o in itens:
        por_numero.setdefault(o.numero, []).append(o)

    bling_ids = [int(rows[0].bling_id) for rows in por_numero.values() if rows[0].bling_id]
    postagem_por_bling: dict[int, datetime] = {}
    if bling_ids:
        ev = (
            await session.execute(
                select(BlingEnvioEvento.bling_id, func.min(BlingEnvioEvento.occurred_at))
                .where(BlingEnvioEvento.bling_id.in_(bling_ids))
                .group_by(BlingEnvioEvento.bling_id)
            )
        ).all()
        postagem_por_bling = {int(b): dt for b, dt in ev}
    lojas = await _lojas(
        session, {str(rows[0].loja) for rows in por_numero.values() if rows[0].loja}
    )
    ja_abertos = await _com_chamado_aberto(session, limpos)

    grupos: dict[str, Grupo] = {}
    for numero in limpos:
        rows = por_numero.get(numero)
        if not rows:
            excluidos.append(
                Excluido(numero, None, "pedido_nao_encontrado", "não está no espelho do Bling")
            )
            continue
        cabeca = rows[0]
        mk = (cabeca.numeroloja or "").strip() or None
        # Mesma cerca de tag da listagem: quem só enxerga a própria operação
        # não abre chamado de pedido de outro time só por saber o número.
        if tags is not None and not any(classify_sku_tag(i.item_codigo) in tags for i in rows):
            excluidos.append(Excluido(numero, mk, "fora_da_sua_tag", "pedido de outra operação"))
            continue
        if numero in ja_abertos:
            excluidos.append(
                Excluido(
                    numero,
                    mk,
                    "ja_tem_chamado",
                    f"já tem chamado de atraso aberto ({ja_abertos[numero]})",
                )
            )
            continue
        if not mk:
            excluidos.append(
                Excluido(numero, mk, "sem_numero_marketplace", "sem nº do pedido na plataforma")
            )
            continue
        corte = cabeca.marketplace_ship_deadline
        if corte is None:
            excluidos.append(Excluido(numero, mk, "sem_corte", "sem horário de corte capturado"))
            continue
        postagem = postagem_por_bling.get(int(cabeca.bling_id)) if cabeca.bling_id else None
        if postagem is None:
            excluidos.append(Excluido(numero, mk, "nao_postado", "postagem ainda não confirmada"))
            continue
        atraso_min = int((postagem - corte).total_seconds() // 60)
        if atraso_min <= 0:
            excluidos.append(Excluido(numero, mk, "no_prazo", "postado dentro do prazo"))
            continue
        if postagem.astimezone(BRT).date() != corte.astimezone(BRT).date():
            excluidos.append(
                Excluido(numero, mk, "dia_seguinte", "postado em outro dia (fora da regra)")
            )
            continue
        motivo = motivos.get(numero) or motivo_por_atraso(atraso_min)
        chave = str(cabeca.loja or "")
        info = lojas.get(chave, {})
        plataforma = info.get("plataforma")
        if chave not in grupos:
            grupos[chave] = Grupo(
                chave=chave,
                loja=info.get("loja")
                or (
                    f"{(plataforma or '').upper()} {info.get('conta') or ''}".strip()
                    or chave
                    or "loja"
                ),
                plataforma=plataforma,
                conta=info.get("conta"),
                canal="robo" if eh_ml(plataforma) else "manual",
                pedidos=[],
                texto="",
            )
        grupos[chave].pedidos.append(
            PedidoAtraso(
                pedido_bling=numero,
                pedido_marketplace=mk,
                corte=corte.isoformat(),
                postagem=postagem.isoformat(),
                atraso_min=atraso_min,
                motivo=motivo,
            )
        )
    saida = []
    for g in grupos.values():
        g.pedidos.sort(key=lambda p: p.postagem)
        g.texto = render_texto(g.pedidos)
        saida.append(asdict(g))
    saida.sort(key=lambda g: g["loja"].lower())
    return {"grupos": saida, "excluidos": [asdict(e) for e in excluidos]}


async def abrir(
    session: AsyncSession,
    grupos_in: list[dict],
    *,
    user: User,
    tags: list[str] | None,
) -> list[dict]:
    """Abre os chamados conferidos na tela: reclassifica os pedidos (a regra
    vale de novo — pedido que ganhou chamado nesse meio-tempo fica fora), usa
    o texto editado na tela quando veio, senão o texto padrão. NÃO commita."""
    abertos: list[dict] = []
    for gin in grupos_in:
        pedidos_in = gin.get("pedidos") or []
        numeros = [str(p.get("pedido_bling") or "").strip() for p in pedidos_in]
        motivos = {
            str(p.get("pedido_bling") or "").strip(): p.get("motivo")
            for p in pedidos_in
            if p.get("motivo") in MOTIVOS
        }
        conferido = await montar(session, numeros, tags=tags, motivos=motivos)
        texto_tela = (gin.get("texto") or "").strip()
        for g in conferido["grupos"]:
            pedidos = [PedidoAtraso(**p) for p in g["pedidos"]]
            # Texto da tela só vale pro grupo que a tela mostrou (mesma loja).
            texto = (
                texto_tela
                if texto_tela and g["chave"] == str(gin.get("chave") or "")
                else render_texto(pedidos)
            )
            ch = await _abrir_grupo(session, g, pedidos, texto, user=user)
            abertos.append(
                {
                    "chave": g["chave"],
                    "loja": g["loja"],
                    "canal": ch.canal,
                    "chamado_id": str(ch.id),
                    "pedidos": len(pedidos),
                }
            )
        for e in conferido["excluidos"]:
            abertos.append({"chave": str(gin.get("chave") or ""), "excluido": e})
    return abertos


async def _abrir_grupo(
    session: AsyncSession, g: dict, pedidos: list[PedidoAtraso], texto: str, *, user: User
) -> Chamado:
    primeiro = pedidos[0]
    robo = g["canal"] == "robo"
    nums = ", ".join(p.pedido_marketplace or p.pedido_bling for p in pedidos)
    datas = sorted({_data(_iso(p.corte)) for p in pedidos})
    resumo = ", ".join(
        f"{sum(1 for p in pedidos if p.motivo == m)} {MOTIVO_LABEL[m]}"
        for m in MOTIVOS
        if any(p.motivo == m for p in pedidos)
    )
    ch = Chamado(
        data=datetime.now(chamados_svc.SAO_PAULO).date(),
        pedido_bling=primeiro.pedido_bling,
        pedido_marketplace=primeiro.pedido_marketplace,
        plataforma=g.get("plataforma"),
        conta=g.get("conta"),
        origem=ORIGEM,
        canal="robo" if robo else "manual",
        observacao=(
            f"Atraso na postagem em {' e '.join(datas)} — {len(pedidos)} pedido(s) pela aba "
            f"Pedidos (Controle de Estoque): {nums}"
        ),
        created_by=user.id,
    )
    await chamados_svc.preencher_do_pedido(session, ch)
    session.add(ch)
    await session.flush()
    quem = (user.name or user.email or "").strip() or "usuário"
    session.add(
        chamados_svc.registrar_sistema(
            ch,
            f"Chamado de atraso na postagem aberto pela aba Pedidos por {quem}: "
            f"{len(pedidos)} pedido(s) ({resumo}) — {g['loja']}",
        )
    )
    abertura = chamados_svc.nova_mensagem(
        ch,
        texto=texto,
        tipo="abertura",
        direcao="enviada",
        autor_nome=quem,
        autor_id=user.id,
        status="pendente" if robo else "registrada",
    )
    if not robo:
        # Shopee/TikTok/Amazon: sem API nem robô — o texto fica pronto pra
        # alguém abrir na mão no Seller Center (mesmo código da devolução).
        abertura.erro = "plataforma_sem_api"
    session.add(abertura)
    for p in pedidos:
        session.add(
            ChamadoPedido(
                chamado_id=ch.id,
                pedido_bling=p.pedido_bling,
                pedido_marketplace=p.pedido_marketplace,
                motivo=p.motivo,
                corte_at=_iso(p.corte),
                postagem_at=_iso(p.postagem),
            )
        )
    logger.info(
        "chamado_atraso_aberto",
        chamado_id=str(ch.id),
        loja=g["loja"],
        canal=ch.canal,
        pedidos=len(pedidos),
        por=user.email,
    )
    return ch


async def chamados_por_pedido(session: AsyncSession, numeros: list[str]) -> dict[str, dict]:
    """Pra aba Pedidos: pedido → chamado de atraso (o aberto, senão o mais
    recente) com nº/protocolo, canal, resolvido e o status da abertura
    (pendente na fila do robô, enviada, registrada = abrir na mão)."""
    limpos = [n for n in {(n or "").strip() for n in numeros} if n]
    if not limpos:
        return {}
    rows = (
        await session.execute(
            select(ChamadoPedido, Chamado)
            .join(Chamado, Chamado.id == ChamadoPedido.chamado_id)
            .where(ChamadoPedido.pedido_bling.in_(limpos))
            .order_by(Chamado.resolvido.asc(), Chamado.created_at.desc())
        )
    ).all()
    escolhido: dict[str, tuple[ChamadoPedido, Chamado]] = {}
    for cp, ch in rows:
        escolhido.setdefault(cp.pedido_bling, (cp, ch))
    ids = {ch.id for _, ch in escolhido.values()}
    status_por_chamado: dict = {}
    if ids:
        msgs = (
            await session.execute(
                select(ChamadoMensagem.chamado_id, ChamadoMensagem.status)
                .where(
                    ChamadoMensagem.chamado_id.in_(list(ids)), ChamadoMensagem.tipo == "abertura"
                )
                .order_by(ChamadoMensagem.created_at.desc())
            )
        ).all()
        for cid, st in msgs:
            status_por_chamado.setdefault(cid, st)
    return {
        numero: {
            "chamado_id": str(ch.id),
            "chamado": ch.chamado,
            "canal": ch.canal,
            "resolvido": bool(ch.resolvido),
            "status": status_por_chamado.get(ch.id),
            "motivo": cp.motivo,
        }
        for numero, (cp, ch) in escolhido.items()
    }
