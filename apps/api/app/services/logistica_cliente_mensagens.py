"""Mensagens ao comprador da Amazon (e-mail pro endereço de retransmissão).

Vinicius, 15/09/2026: o cliente precisa saber quando a entrega tem problema,
atrasa e quando é entregue. A Amazon não deixa o vendedor mandar "seu pedido
foi postado" (ela mesma manda, com o rastreio, ao confirmar o envio) — mas
aceita mensagens necessárias pra o comprador receber a compra. O caminho é o
mesmo que o Bling usa no "Enviar por e-mail": um e-mail comum pro endereço
`…@marketplace.amazon.com.br` do pedido, que a Amazon repassa ao cliente.

Regras da Amazon embutidas aqui:
- só o endereço de retransmissão (logistica.cliente_email, que só guarda
  `…@marketplace.amazon.*`);
- texto puro, sem HTML, sem link e sem e-mail (validação ao salvar o modelo);
- número do pedido de 17 dígitos em toda mensagem (`{pedido_amazon}`);
- UMA mensagem por pedido × evento (unique em logistica_mensagem_cliente).

Pré-requisito fora do código: o remetente (`email_from`) cadastrado como
remetente aprovado no Seller Central de cada conta — sem isso a Amazon
descarta. Por isso o envio nasce DESLIGADO (`amazon_mensagens_cliente=false`)
e liga só quando o cadastro estiver feito.

Eventos:
- `rastreio`: pacote postado — código dos Correios + previsão (Vinicius
  15/09: "vamos deixar esse aviso de rastreio também automático; a Amazon
  manda, mas ficou muito bom assim"). A Amazon prefere que aviso de postagem
  venha só dela; decisão do Vinicius, uma vez por pedido;
- `problema_correios`: ocorrência grave lida pelo 17track (apreendido,
  extraviado, devolvido…);
- `previsao_vencida`: previsão dos Correios (Bling) passou e não entregou;
- `entregue`: 17track "Delivered" — escrito como "se não recebeu, responda"
  (evitar problema na entrega é o que a Amazon permite).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Logistica, LogisticaMensagemCliente, LogisticaMensagemTemplate
from app.services import (
    imagem_publica,
    logistica_amazon_canal,
    logistica_cartao_rastreio,
    logistica_rules,
    logistica_track,
)

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

EVENTO_RASTREIO = "rastreio"
EVENTO_PROBLEMA = "problema_correios"
EVENTO_PREVISAO = "previsao_vencida"
EVENTO_ENTREGUE = "entregue"
EVENTOS: tuple[str, ...] = (EVENTO_RASTREIO, EVENTO_PROBLEMA, EVENTO_PREVISAO, EVENTO_ENTREGUE)
EVENTO_LABELS_PT: dict[str, str] = {
    EVENTO_RASTREIO: "Aviso de rastreio",
    EVENTO_PROBLEMA: "Problema na transportadora",
    EVENTO_PREVISAO: "Previsão da transportadora vencida",
    EVENTO_ENTREGUE: "Pacote entregue",
}

JANELA_DIAS = 60
MAX_TENTATIVAS = 3

# Campos que o texto pode usar entre chaves. `{pedido_amazon}` é obrigatório
# (a Amazon exige o número do pedido em toda mensagem).
PLACEHOLDERS: dict[str, str] = {
    "cliente": "primeiro nome do comprador",
    "pedido_amazon": "número do pedido na Amazon (obrigatório)",
    "pedido_bling": "número do pedido no Bling",
    "rastreio": "código de rastreio dos Correios",
    "servico": "serviço de envio (SEDEX, PAC…)",
    "localizacao": "última posição informada pela transportadora",
    "postagem": "data de postagem",
    "previsao_correios": "previsão de entrega da transportadora",
    "prazo_amazon": "data máxima de entrega da Amazon",
    "entregue_em": "data em que a transportadora registrou a entrega",
    "ocorrencia": "texto da ocorrência da transportadora",
}

TEMPLATES_PADRAO: dict[str, dict[str, str]] = {
    EVENTO_RASTREIO: {
        "assunto": "Pedido {pedido_amazon}: seu pacote está a caminho",
        "corpo": (
            "Olá, {cliente}.\n\n"
            "Seu pedido {pedido_amazon} foi postado nos Correios em {postagem} pelo "
            "serviço {servico}.\n"
            "O código de rastreio é {rastreio} e a previsão de entrega da transportadora "
            "é {previsao_correios}.\n\n"
            "Se tiver qualquer dúvida sobre a entrega, é só responder esta mensagem.\n\n"
            "Atenciosamente,\nequipe da loja"
        ),
    },
    EVENTO_PROBLEMA: {
        "assunto": "Pedido {pedido_amazon}: ocorrência no transporte",
        "corpo": (
            "Olá, {cliente}.\n\n"
            "A transportadora registrou uma ocorrência no transporte do seu pedido "
            "{pedido_amazon}: \"{ocorrencia}\".\n\n"
            "Já estamos acompanhando junto à transportadora para resolver o mais rápido "
            "possível. Se precisar de algo, é só responder esta mensagem.\n\n"
            "Código de rastreio: {rastreio}\n\n"
            "Atenciosamente,\nequipe da loja"
        ),
    },
    EVENTO_PREVISAO: {
        "assunto": "Pedido {pedido_amazon}: atualização sobre a entrega",
        "corpo": (
            "Olá, {cliente}.\n\n"
            "A previsão de entrega da transportadora para o seu pedido {pedido_amazon} era "
            "{previsao_correios} e o pacote ainda está a caminho. Última posição "
            "informada pela transportadora: {localizacao}.\n\n"
            "Estamos acompanhando junto à transportadora. A data máxima de entrega do "
            "pedido é {prazo_amazon}. Se tiver qualquer dúvida, responda esta mensagem.\n\n"
            "Código de rastreio: {rastreio}\n\n"
            "Atenciosamente,\nequipe da loja"
        ),
    },
    EVENTO_ENTREGUE: {
        "assunto": "Pedido {pedido_amazon}: entrega registrada pela transportadora",
        "corpo": (
            "Olá, {cliente}.\n\n"
            "A transportadora registrou a entrega do seu pedido {pedido_amazon} em "
            "{entregue_em}.\n\n"
            "Se você não recebeu o pacote, responda esta mensagem que vamos resolver.\n\n"
            "Obrigado pela compra!\nequipe da loja"
        ),
    },
}


@dataclass(frozen=True)
class Template:
    evento: str
    assunto: str
    corpo: str
    ativo: bool = True
    padrao: bool = True  # True = texto do código (sem linha no banco)


class TemplateInvalidoError(ValueError):
    """Texto que a Amazon recusaria (link, e-mail, HTML) ou sem o nº do pedido."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_RE_LINK = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RE_HTML = re.compile(r"<[a-zA-Z/][^>]*>")


def validar_texto(assunto: str, corpo: str) -> None:
    txt = f"{assunto}\n{corpo}"
    if _RE_LINK.search(txt):
        raise TemplateInvalidoError("mensagem_com_link")
    if _RE_EMAIL.search(txt):
        raise TemplateInvalidoError("mensagem_com_email")
    if _RE_HTML.search(txt):
        raise TemplateInvalidoError("mensagem_com_html")
    if "{pedido_amazon}" not in corpo:
        raise TemplateInvalidoError("mensagem_sem_pedido")
    try:
        (assunto + corpo).format_map(_Contexto(dict.fromkeys(PLACEHOLDERS, "")))
    except (ValueError, IndexError) as e:
        raise TemplateInvalidoError("mensagem_chaves_invalidas") from e


def templates_padrao() -> dict[str, Template]:
    return {
        ev: Template(evento=ev, assunto=t["assunto"], corpo=t["corpo"], ativo=True, padrao=True)
        for ev, t in TEMPLATES_PADRAO.items()
    }


async def carregar_templates(session: AsyncSession) -> dict[str, Template]:
    """Texto do banco quando existe; senão o padrão do código."""
    out = templates_padrao()
    rows = (await session.execute(select(LogisticaMensagemTemplate))).scalars().all()
    for r in rows:
        if r.evento in out:
            out[r.evento] = Template(
                evento=r.evento,
                assunto=r.assunto or out[r.evento].assunto,
                corpo=r.corpo or out[r.evento].corpo,
                ativo=bool(r.ativo),
                padrao=False,
            )
    return out


async def salvar_template(
    session: AsyncSession, evento: str, *, assunto: str, corpo: str, ativo: bool
) -> Template:
    if evento not in EVENTOS:
        raise TemplateInvalidoError("evento_desconhecido")
    assunto = assunto.strip()
    corpo = corpo.strip()
    validar_texto(assunto, corpo)
    row = (
        await session.execute(
            select(LogisticaMensagemTemplate).where(LogisticaMensagemTemplate.evento == evento)
        )
    ).scalar_one_or_none()
    if row is None:
        row = LogisticaMensagemTemplate(evento=evento, assunto=assunto, corpo=corpo, ativo=ativo)
        session.add(row)
    else:
        row.assunto, row.corpo, row.ativo = assunto, corpo, ativo
    await session.commit()
    return Template(evento=evento, assunto=assunto, corpo=corpo, ativo=ativo, padrao=False)


class _Contexto(dict):
    """format_map que devolve vazio pra chave desconhecida (texto editado à
    mão com um campo que não existe não pode derrubar o envio)."""

    def __missing__(self, key: str) -> str:
        return ""


def _fmt(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(SAO_PAULO).strftime("%d/%m/%Y")


def _primeiro_nome(nome: str | None) -> str:
    n = (nome or "").strip()
    if not n:
        return "tudo bem?"
    return n.split()[0].capitalize()


def contexto(row: Logistica) -> dict[str, str]:
    return {
        "cliente": _primeiro_nome(row.cliente_nome),
        "pedido_amazon": (row.pedido_marketplace or "").strip(),
        "pedido_bling": (row.pedido_bling or "").strip(),
        "rastreio": (row.rastreio or "").strip(),
        "servico": (row.servico_envio or "").strip(),
        "localizacao": (row.localizacao or "").strip() or "em trânsito",
        "postagem": _fmt(row.postagem_data),
        "previsao_correios": _fmt(row.previsao_correios),
        "prazo_amazon": _fmt(row.prazo_entrega_amazon),
        "entregue_em": _fmt_dt(row.entregue_em),
        "ocorrencia": (row.problema_correios or "").strip(),
    }


def renderizar(tpl: Template, row: Logistica) -> tuple[str, str]:
    ctx = _Contexto(contexto(row))
    return tpl.assunto.format_map(ctx).strip(), tpl.corpo.format_map(ctx).strip()


def eventos_devidos(row: Logistica, hoje: date | None = None) -> list[str]:
    """Eventos que este pedido de Envio próprio já viveu (o histórico decide o
    que ainda não foi mandado)."""
    if (row.amazon_canal or "") != logistica_amazon_canal.CANAL_PROPRIO:
        return []
    hoje = hoje or datetime.now(SAO_PAULO).date()
    out: list[str] = []
    # Postado: o Bling já tem o código dos Correios, a data de saída e a
    # previsão (objeto de postagem) e a data de saída não está no futuro.
    if (
        logistica_track.is_correios(row.rastreio)
        and row.postagem_data is not None
        and row.postagem_data <= hoje
        and row.previsao_correios is not None
        and row.entregue_em is None
    ):
        out.append(EVENTO_RASTREIO)
    if row.problema_correios_em is not None and row.problema_correios:
        out.append(EVENTO_PROBLEMA)
    if (
        row.previsao_correios is not None
        and row.previsao_correios < hoje
        and row.entregue_em is None
    ):
        out.append(EVENTO_PREVISAO)
    if row.entregue_em is not None:
        out.append(EVENTO_ENTREGUE)
    return out


class Sender(Protocol):
    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        attachments: list[tuple[str, str, bytes]] | None = None,
    ) -> None: ...


async def montar_cartao(
    session: AsyncSession, row: Logistica
) -> tuple[str, str, bytes] | None:
    """Cartão de rastreio pra anexar (nome, mime, png), ou None quando não faz
    sentido mandar imagem.

    Sai None — e a mensagem vai sem anexo, nunca deixa de ir — só quando:
    - o rastreio não é dos Correios (o 17track só lê `...BR`);
    - o 17track não respondeu ou não conhece o número;
    - o número não tem evento nenhum (não há o que desenhar).

    Pedido com só "Etiqueta emitida" LEVA o cartão (Vinicius, 22/09).
    """
    rastreio = (row.rastreio or "").strip()
    if not logistica_track.is_correios(rastreio):
        return None
    try:
        por_numero = await logistica_track.eventos([rastreio])
    except Exception as e:  # noqa: BLE001 — imagem é extra, mensagem é o que importa
        logger.warning(
            "logistica_cartao_rastreio_sem_eventos", pedido=row.pedido_bling, err=str(e)[:200]
        )
        return None
    brutos = por_numero.get(rastreio) or []
    if not brutos:
        return None
    eventos = [
        logistica_cartao_rastreio.Evento(
            quando=e.quando,
            titulo=e.descricao,
            local=e.local,
            entregue=any(p in e.descricao.lower() for p in logistica_rules._CORREIOS_ENTREGUE),
        )
        for e in brutos
    ]
    # Logo dos Correios no canto (Vinicius, 22/09): sai do banco, não de pasta
    # no servidor. Se a linha não estiver lá, o cartão vai sem a figura.
    logo = await imagem_publica.carregar(session, imagem_publica.LOGO_CORREIOS)
    try:
        png = logistica_cartao_rastreio.gerar(
            codigo=rastreio,
            pedido=(row.pedido_marketplace or row.pedido_bling or "").strip(),
            servico=(row.servico_envio or "").strip(),
            previsao=row.previsao_correios,
            eventos=eventos,
            consultado_em=datetime.now(UTC),
            logo=logo,
        )
    except ValueError as e:
        logger.warning(
            "logistica_cartao_rastreio_falhou", pedido=row.pedido_bling, err=str(e)[:200]
        )
        return None
    return (
        logistica_cartao_rastreio.CARTAO_NOME,
        logistica_cartao_rastreio.CARTAO_MIME,
        png,
    )


def _linha_exemplo() -> Logistica:
    """Pedido fictício pra ver o texto montado quando não há pedido escolhido."""
    return Logistica(
        pedido_bling="000000",
        pedido_marketplace="701-0000000-0000000",
        plataforma="Amazon",
        conta="exemplo",
        amazon_canal=logistica_amazon_canal.CANAL_PROPRIO,
        servico_envio="SEDEX",
        rastreio="AA123456789BR",
        localizacao="São Paulo/SP — Objeto em trânsito",
        cliente_nome="Cliente Exemplo",
        postagem_data=date(2026, 9, 14),
        previsao_correios=date(2026, 9, 23),
        prazo_entrega_amazon=date(2026, 10, 8),
        entregue_em=datetime(2026, 9, 23, 18, 0, tzinfo=UTC),
        problema_correios="Objeto extraviado — em análise",
        problema_correios_em=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
    )


async def enviar_teste(
    session: AsyncSession,
    evento: str,
    *,
    email: str,
    pedido_bling: str | None = None,
    sender: Sender | None = None,
) -> dict[str, Any]:
    """Manda o texto de um evento, montado com os dados de um pedido real (ou
    do exemplo), pra um e-mail SEU — pra ver na caixa de entrada o que o
    cliente receberia. Nunca pra endereço de retransmissão da Amazon (isso
    seria uma mensagem de verdade pro comprador). Ignora a chave global e o
    `ativo` do evento: é teste."""
    if evento not in EVENTOS:
        raise TemplateInvalidoError("evento_desconhecido")
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@", 1)[1]:
        raise TemplateInvalidoError("email_invalido")
    if logistica_amazon_canal.eh_email_relay_amazon(email):
        raise TemplateInvalidoError("teste_nao_vai_para_cliente")
    row: Logistica | None = None
    if (pedido_bling or "").strip():
        row = (
            await session.execute(
                select(Logistica).where(
                    func.lower(func.trim(Logistica.plataforma)).in_(
                        tuple(logistica_rules._AMAZON_PLATAFORMAS)
                    ),
                    Logistica.pedido_bling == pedido_bling.strip(),
                )
            )
        ).scalars().first()
        if row is None:
            raise TemplateInvalidoError("pedido_nao_encontrado")
    else:
        row = _linha_exemplo()
    templates = await carregar_templates(session)
    assunto, corpo = renderizar(templates[evento], row)
    assunto = f"[TESTE] {assunto}"
    corpo = (
        corpo
        + "\n\n— Este é um teste do DaVinci: o cliente NÃO recebeu esta mensagem. "
        + f"Pedido usado: {row.pedido_bling}."
    )
    if sender is None:
        from app.services.email import get_email_sender

        sender = get_email_sender()
    # Pedido de verdade leva o cartão; o exemplo não tem rastreio que o
    # 17track conheça, então o teste sai sem imagem (e o retorno avisa).
    cartao = await montar_cartao(session, row) if row.id is not None else None
    await sender.send(
        to=email, subject=assunto, html="", text=corpo,
        attachments=[cartao] if cartao else None,
    )
    logger.info(
        "logistica_cliente_mensagem_teste",
        evento=evento,
        para=email,
        pedido=row.pedido_bling,
        com_cartao=bool(cartao),
    )
    return {
        "assunto": assunto,
        "corpo": corpo,
        "pedido": row.pedido_bling or "",
        "cartao": bool(cartao),
    }


async def enviar_agora(
    session: AsyncSession,
    *,
    pedido_bling: str,
    evento: str,
    sender: Sender | None = None,
) -> dict[str, Any]:
    """Manda AGORA, pro comprador de verdade, a mensagem de um evento de um
    pedido escolhido.

    Vinicius, 22/09: é o disparo controlado pra descobrir se a Amazon repassa
    o anexo — dispara em um pedido, confere no Seller Central, e só então o
    robô solta pra todo mundo. Diferente do `enviar_teste` (que vai pro
    e-mail do operador), esta chega no comprador: só admin, um pedido por vez,
    e reenvia mesmo que o evento já tenha ido — senão não dava pra testar.
    """
    if evento not in EVENTOS:
        raise TemplateInvalidoError("evento_desconhecido")
    if not get_settings().amazon_mensagens_cliente:
        # Sem a chave ligada o remetente não está aprovado no Seller Central e
        # a Amazon descarta: o teste não provaria nada.
        raise TemplateInvalidoError("envio_desligado")
    pedido = (pedido_bling or "").strip()
    row = (
        await session.execute(
            select(Logistica).where(
                func.lower(func.trim(Logistica.plataforma)).in_(
                    tuple(logistica_rules._AMAZON_PLATAFORMAS)
                ),
                Logistica.pedido_bling == pedido,
            )
        )
    ).scalars().first()
    if row is None:
        raise TemplateInvalidoError("pedido_nao_encontrado")
    if (row.amazon_canal or "") != logistica_amazon_canal.CANAL_PROPRIO:
        raise TemplateInvalidoError("pedido_nao_e_envio_proprio")
    destino = (row.cliente_email or "").strip()
    if not logistica_amazon_canal.eh_email_relay_amazon(destino):
        raise TemplateInvalidoError("pedido_sem_email_do_comprador")

    templates = await carregar_templates(session)
    assunto, corpo = renderizar(templates[evento], row)
    cartao = await montar_cartao(session, row)
    if sender is None:
        from app.services.email import get_email_sender

        sender = get_email_sender()
    m = (
        await session.execute(
            select(LogisticaMensagemCliente).where(
                LogisticaMensagemCliente.logistica_id == row.id,
                LogisticaMensagemCliente.evento == evento,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        m = LogisticaMensagemCliente(logistica_id=row.id, evento=evento, destinatario=destino)
        session.add(m)
    m.assunto, m.corpo, m.destinatario = assunto, corpo, destino
    m.tentativas = (m.tentativas or 0) + 1
    try:
        await sender.send(
            to=destino, subject=assunto, html="", text=corpo,
            attachments=[cartao] if cartao else None,
        )
    except Exception as e:  # noqa: BLE001 — o painel mostra o erro
        m.erro = str(e)[:300]
        await session.commit()
        logger.warning(
            "logistica_cliente_mensagem_agora_falhou", pedido=pedido, evento=evento, err=m.erro
        )
        raise
    m.enviado_em = datetime.now(UTC)
    m.erro = None
    await session.commit()
    logger.info(
        "logistica_cliente_mensagem_agora",
        pedido=pedido,
        evento=evento,
        destinatario=destino,
        com_cartao=bool(cartao),
    )
    return {
        "assunto": assunto,
        "corpo": corpo,
        "pedido": row.pedido_bling or "",
        "destinatario": destino,
        "cartao": bool(cartao),
    }


async def _historico(
    session: AsyncSession, rows: list[Logistica]
) -> dict[tuple[Any, str], LogisticaMensagemCliente]:
    ids = [r.id for r in rows]
    if not ids:
        return {}
    msgs = (
        await session.execute(
            select(LogisticaMensagemCliente).where(LogisticaMensagemCliente.logistica_id.in_(ids))
        )
    ).scalars().all()
    return {(m.logistica_id, m.evento): m for m in msgs}


async def _alvo(session: AsyncSession) -> list[Logistica]:
    corte = date.today() - timedelta(days=JANELA_DIAS)
    rows = (
        await session.execute(
            select(Logistica).where(
                func.lower(func.trim(Logistica.plataforma)).in_(
                    tuple(logistica_rules._AMAZON_PLATAFORMAS)
                ),
                Logistica.amazon_canal == logistica_amazon_canal.CANAL_PROPRIO,
                Logistica.cliente_email.isnot(None),
                or_(Logistica.data.is_(None), Logistica.data >= corte),
            )
        )
    ).scalars().all()
    return [r for r in rows if logistica_amazon_canal.eh_email_relay_amazon(r.cliente_email)]


async def run(
    session: AsyncSession,
    *,
    sender: Sender | None = None,
    hoje: date | None = None,
    limit: int = 50,
) -> dict[str, int]:
    """Uma rodada: manda ao cliente o que ainda não foi mandado. Desligado por
    padrão (`amazon_mensagens_cliente`); falha de envio fica registrada com o
    erro e é retentada até MAX_TENTATIVAS."""
    resumo = {"desligado": 0, "pedidos": 0, "devidas": 0, "enviadas": 0, "falhas": 0, "puladas": 0}
    if not get_settings().amazon_mensagens_cliente:
        resumo["desligado"] = 1
        return resumo
    rows = await _alvo(session)
    resumo["pedidos"] = len(rows)
    if not rows:
        return resumo
    templates = await carregar_templates(session)
    hist = await _historico(session, rows)
    fila: list[tuple[Logistica, str]] = []
    for r in rows:
        for ev in eventos_devidos(r, hoje):
            m = hist.get((r.id, ev))
            if m is not None and (m.enviado_em is not None or m.tentativas >= MAX_TENTATIVAS):
                continue
            tpl = templates[ev]
            if not tpl.ativo:
                resumo["puladas"] += 1
                continue
            fila.append((r, ev))
    resumo["devidas"] = len(fila)
    if not fila:
        return resumo
    if sender is None:
        from app.services.email import get_email_sender

        sender = get_email_sender()
    agora = datetime.now(UTC)
    # Um cartão por PEDIDO: dois eventos do mesmo pedido na mesma rodada
    # (previsão vencida + entregue, por exemplo) reaproveitam a leitura.
    cartoes: dict[Any, tuple[str, str, bytes] | None] = {}
    for r, ev in fila[:limit]:
        assunto, corpo = renderizar(templates[ev], r)
        if r.id not in cartoes:
            cartoes[r.id] = await montar_cartao(session, r)
        cartao = cartoes[r.id]
        m = hist.get((r.id, ev))
        if m is None:
            m = LogisticaMensagemCliente(
                logistica_id=r.id, evento=ev, destinatario=r.cliente_email or ""
            )
            session.add(m)
            hist[(r.id, ev)] = m
        m.assunto, m.corpo = assunto, corpo
        m.tentativas = (m.tentativas or 0) + 1
        try:
            await sender.send(
                to=r.cliente_email or "",
                subject=assunto,
                html="",
                text=corpo,
                attachments=[cartao] if cartao else None,
            )
        except Exception as e:  # noqa: BLE001 — registra e retenta na próxima rodada
            m.erro = str(e)[:300]
            resumo["falhas"] += 1
            logger.warning(
                "logistica_cliente_mensagem_falhou", pedido=r.pedido_bling, evento=ev, err=m.erro
            )
            continue
        m.enviado_em = agora
        m.erro = None
        resumo["enviadas"] += 1
        logger.info(
            "logistica_cliente_mensagem_enviada",
            pedido=r.pedido_bling,
            evento=ev,
            destinatario=r.cliente_email,
            com_cartao=bool(cartao),
        )
    await session.commit()
    return resumo
