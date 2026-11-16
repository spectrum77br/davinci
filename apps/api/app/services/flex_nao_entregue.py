"""Botão "Não conseguiu entregar" da aba Flex (Vinicius, 16/11/2026).

"chamado vamos dizer que não conseguiu entregar consegue deixar um botão abrir
chamado e enviar mensagem ao cliente. daí esse chamado já abre na loja
informando que não conseguiu entregar".

Um clique da PESSOA no Raio-X do pedido Flex, com os dois textos na tela para
conferir e editar antes de sair (nada é automático):

- **Chamado na loja** — um chamado PRÓPRIO na aba Chamados (origem logística,
  canal robô). Só reaproveita o chamado que este botão já abriu (marcado pela
  nota `PREFIXO_NOTA`); chamado de outro assunto do mesmo pedido — atraso em
  lote, aberto à mão, regra da aba Status — nunca é tocado.
  - Mercado Livre: a abertura vai pra fila `abrir-ml` da IA de Chamado, que
    abre no Fale conosco (o pedido não tem reclamação: a API não abre).
  - Shopee: não há fila de abertura com dono (a do robô antigo ninguém pede).
    A abertura fica REGISTRADA (fora da fila — pendente ficaria "na fila do
    robô" pra sempre e travaria as regras "Abrir chamado" do pedido) e vai uma
    INSTRUÇÃO pra IA abrir pelo Assistente do Vendedor, o caminho que andou
    com os chamados da Shopee criados na Logística em 12/11. A instrução passa
    na frente da fila da IA.
  - De novo no mesmo pedido: com o nº do atendimento (ou chamado Encerrado) a
    novidade vai como instrução pro mesmo atendimento; ML com a abertura ainda
    na fila ganha a novidade no fim dela; Shopee sem nº repete a instrução
    COMPLETA (a IA só lê a última instrução não consumida). Chamado do botão
    já Concluído → abre outro.
- **Mensagem ao cliente** (a pessoa pode desmarcar):
  - Shopee: chat da loja (o mesmo da senha do aparelho bloqueado), comprador
    tirado do próprio pedido;
  - Mercado Livre: mensagem pós-venda do pack — até 350 letras e só os
    caracteres que o ML aceita (sem emoji; travessão e aspas curvas viram os
    simples). O ML pode recusar a loja puxar a conversa; aí a tela mostra o
    porquê e a pessoa manda à mão.
  A nota do clique entra no chamado ANTES da mensagem sair ("enviando…") e é
  completada com o resultado — é o "já avisado" da janela e a trava contra o
  segundo envio (`JANELA_REPETIDO`).

O motivo e a hora vêm do Tracken (a transportadora do Flex): o último evento
que parece tentativa sem entrega (ausente, recusado, endereço…).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoMensagem, Logistica, LogisticaTracken, User
from app.services import chamados as chamados_svc
from app.services import logistica_meli, logistica_rules, logistica_shopee, tracken
from app.services.atendimento import ml as atendimento_ml

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

PLAT_ML = "ml"
PLAT_SHOPEE = "shopee"

# Mensagem pós-venda do ML: até 350 caracteres, só ISO-8859-1 (marketplaces/ml.py).
ML_LIMITE = 350
TEXTO_MAX = 4000

# Começo das notas que esta ação deixa no histórico do chamado — é por ele que
# se acha o chamado do botão e os avisos anteriores do pedido.
PREFIXO_NOTA = "Flex · não conseguiu entregar"
NOTA_ENVIANDO = "mensagem ao cliente: enviando…"
# Outro clique no mesmo pedido dentro disto é recusado (duplo clique, duas
# abas, duas pessoas): a mensagem ao cliente não sai duas vezes.
JANELA_REPETIDO = timedelta(minutes=5)

MENSAGEM_ENVIADA = "enviada"
MENSAGEM_FALHOU = "falhou"
MENSAGEM_INCERTA = "incerta"
MENSAGEM_NAO_PEDIDA = "nao_pedida"

ACAO_ABERTO = "aberto"
ACAO_ACRESCENTADO = "acrescentado"
ACAO_INSTRUCAO = "instrucao"

_CANAL_MENSAGEM = {
    PLAT_ML: "mensagem pós-venda do Mercado Livre",
    PLAT_SHOPEE: "chat da Shopee",
}
_PLATAFORMA_LABEL = {PLAT_ML: "Mercado Livre", PLAT_SHOPEE: "Shopee"}
_SERVICO = {PLAT_ML: "envio Flex", PLAT_SHOPEE: "Entrega Direta"}


class FlexNaoEntregueError(Exception):
    """Pedido ou texto que não dá pra mandar (código legível pro endpoint)."""

    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.extra = extra


def plataforma_de(row: Logistica) -> str | None:
    """`ml` / `shopee` se a linha é um pedido Flex dessas plataformas; senão None."""
    if row.envio_flex is not True:
        return None
    p = (row.plataforma or "").strip().lower()
    if p in logistica_rules._ML_PLATAFORMAS:
        return PLAT_ML
    if p in logistica_rules._SHOPEE_PLATAFORMAS:
        return PLAT_SHOPEE
    return None


# ---------------------------------------------------------------- Tracken


@dataclass
class Tentativa:
    motivo: str | None = None
    em: datetime | None = None


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _motivo(descricao: str | None, observacao: str | None = None) -> str | None:
    """ "DESTINATÁRIO AUSENTE" → "destinatário ausente" (vai no meio da frase)."""
    partes = [re.sub(r"\s+", " ", (p or "")).strip() for p in (descricao, observacao)]
    partes = [p for p in partes if p]
    if not partes:
        return None
    return " - ".join(dict.fromkeys(partes)).lower()


def ultima_tentativa(tk: LogisticaTracken | None) -> Tentativa:
    """O último evento do Tracken que parece tentativa sem entrega (o histórico
    vem do mais antigo pro mais novo). Sem nenhum: o último evento, se for
    problema (devolução, extravio…). Nada disso: vazio — a pessoa escreve."""
    if tk is None:
        return Tentativa()
    for ev in reversed(list(tk.historico or [])):
        if not isinstance(ev, dict):
            continue
        desc, obs = ev.get("descricao"), ev.get("observacao")
        if tracken.eh_tentativa(desc) or tracken.eh_tentativa(obs):
            return Tentativa(_motivo(desc, obs), _dt(ev.get("em")))
    if tk.ultimo_evento and tracken.eh_problema(tk.ultimo_evento):
        return Tentativa(_motivo(tk.ultimo_evento), tk.ultimo_em)
    return Tentativa()


# ---------------------------------------------------------------- textos

# O que o ML recusa mas tem par simples (ISO-8859-1).
_PARA_LATIN1 = str.maketrans(
    {
        "—": "-",
        "–": "-",
        "‒": "-",
        "‑": "-",
        "‐": "-",
        "−": "-",
        "“": '"',
        "”": '"',
        "„": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "…": "...",
        "•": "-",
        "​": "",
        " ": " ",
    }
)


def normalizar_ml(texto: str) -> str:
    """Travessão, aspas curvas e reticências viram os simples que o ML aceita.
    Emoji e outros símbolos ficam: a validação recusa e a pessoa tira."""
    return (texto or "").translate(_PARA_LATIN1)


def _primeiro_nome(nome: str | None) -> str | None:
    n = (nome or "").strip()
    if not n:
        return None
    return n.split()[0].capitalize()


def _hora(d: datetime) -> str:
    return f"{d.hour}h{d.minute:02d}" if d.minute else f"{d.hour}h"


def _quando_absoluto(em: datetime) -> str:
    d = em.astimezone(SAO_PAULO)
    return f"{d:%d/%m} às {_hora(d)}"


def _quando_relativo(em: datetime, agora: datetime) -> str:
    d = em.astimezone(SAO_PAULO)
    hoje = agora.astimezone(SAO_PAULO).date()
    if d.date() == hoje:
        return f"hoje às {_hora(d)}"
    if d.date() == hoje - timedelta(days=1):
        return f"ontem às {_hora(d)}"
    return f"no dia {d:%d/%m} às {_hora(d)}"


def texto_chamado(row: Logistica, plataforma: str, t: Tentativa) -> str:
    pedido = (row.pedido_marketplace or row.pedido_bling or "").strip()
    quando = f" em {_quando_absoluto(t.em)}" if t.em else ""
    motivo = f" ({t.motivo})" if t.motivo else ""
    return (
        f"Pedido {pedido} ({_SERVICO[plataforma]}): a transportadora tentou entregar"
        f"{quando} e não conseguiu{motivo}. Vamos fazer uma nova tentativa de entrega. "
        "Pedimos que a tentativa seja considerada no prazo do envio e que a loja não "
        "seja penalizada pelo atraso."
    )


def texto_cliente(nome: str | None, plataforma: str, t: Tentativa, agora: datetime) -> str:
    primeiro = _primeiro_nome(nome)
    saudacao = f"Olá, {primeiro}!" if primeiro else "Olá!"
    quando = f" {_quando_relativo(t.em, agora)}" if t.em else ""

    def montar(motivo: str | None) -> str:
        m = f" ({motivo})" if motivo else ""
        return (
            f"{saudacao} Tentamos entregar seu pedido{quando}, mas não conseguimos{m}. "
            "Vamos tentar de novo. Se quiser combinar um horário ou indicar outra "
            "pessoa para receber, é só responder esta mensagem."
        )

    texto = montar(t.motivo)
    if plataforma == PLAT_ML:
        texto = normalizar_ml(texto)
        if len(texto) > ML_LIMITE:
            texto = normalizar_ml(montar(None))  # motivo comprido: não cabe no ML
    return texto


def validar(plataforma: str, texto_chamado_: str, texto_cliente_: str, mandar: bool) -> None:
    """`texto_cliente_` do ML já vem normalizado (`normalizar_ml`)."""
    if not texto_chamado_.strip():
        raise FlexNaoEntregueError("sem_texto_chamado")
    if len(texto_chamado_) > TEXTO_MAX or len(texto_cliente_) > TEXTO_MAX:
        raise FlexNaoEntregueError("texto_grande_demais")
    if not mandar:
        return
    if not texto_cliente_.strip():
        raise FlexNaoEntregueError("sem_texto_cliente")
    if plataforma == PLAT_ML:
        if len(texto_cliente_.strip()) > ML_LIMITE:
            raise FlexNaoEntregueError("texto_cliente_ml_grande")
        try:
            texto_cliente_.encode("iso-8859-1")
        except UnicodeEncodeError as e:
            raise FlexNaoEntregueError("texto_cliente_ml_caractere") from e


# ---------------------------------------------------------------- chamado


def _do_pedido(row: Logistica):
    numero = (row.pedido_bling or "").strip()
    if numero:
        return or_(Chamado.origem_ref == str(row.id), Chamado.pedido_bling == numero)
    return Chamado.origem_ref == str(row.id)


_NOTA_DO_BOTAO = (
    (ChamadoMensagem.tipo == "sistema")
    & ChamadoMensagem.texto.startswith(PREFIXO_NOTA)
    & ChamadoMensagem.excluida_at.is_(None)
)


async def chamado_do_botao(session: AsyncSession, row: Logistica) -> Chamado | None:
    """O chamado mais novo do pedido que ESTE botão abriu (tem a nota dele)."""
    return (
        await session.execute(
            select(Chamado)
            .join(ChamadoMensagem, ChamadoMensagem.chamado_id == Chamado.id)
            .where(Chamado.origem == "logistica", _do_pedido(row), _NOTA_DO_BOTAO)
            .order_by(Chamado.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def avisos_anteriores(session: AsyncSession, row: Logistica) -> list[ChamadoMensagem]:
    """As notas que este botão já deixou nos chamados do pedido (mais nova primeiro)."""
    return list(
        (
            await session.execute(
                select(ChamadoMensagem)
                .join(Chamado, Chamado.id == ChamadoMensagem.chamado_id)
                .where(Chamado.origem == "logistica", _do_pedido(row), _NOTA_DO_BOTAO)
                .order_by(ChamadoMensagem.created_at.desc())
                .limit(10)
            )
        ).scalars()
    )


def _tem_numero(ch: Chamado) -> bool:
    return bool((ch.chamado or "").strip())


def _encerrado(ch: Chamado) -> bool:
    return (ch.status_plataforma or "") in chamados_svc.STATUS_FINAIS


async def como_vai_o_chamado(
    session: AsyncSession, row: Logistica, plataforma: str
) -> tuple[Chamado | None, str]:
    """(chamado do botão que vai receber a novidade — None = abre um novo, ação)."""
    ch = await chamado_do_botao(session, row)
    if ch is None or ch.resolvido:
        return None, ACAO_ABERTO
    if _tem_numero(ch) or _encerrado(ch) or plataforma == PLAT_SHOPEE:
        return ch, ACAO_INSTRUCAO
    ab = await chamados_svc.abertura_do_chamado(session, ch)
    if ab is None or ab.status == "falhou":
        return ch, ACAO_ABERTO  # abertura do ML de novo, no mesmo chamado
    if ab.status == "pendente":
        return ch, ACAO_ACRESCENTADO
    return ch, ACAO_INSTRUCAO  # a IA está abrindo / abriu sem nº ainda


def _autor(user: User) -> str:
    return (user.name or user.email or "").strip() or "usuário"


def _instrucao(ch: Chamado, texto: str, user: User) -> ChamadoMensagem:
    return chamados_svc.nova_mensagem(
        ch,
        texto=texto,
        tipo="instrucao",
        direcao="sistema",
        autor_nome=_autor(user),
        autor_id=user.id,
        status="registrada",
    )


def _abertura(ch: Chamado, texto: str, user: User, *, status: str) -> ChamadoMensagem:
    m = chamados_svc.nova_mensagem(
        ch,
        texto=texto,
        tipo="abertura",
        direcao="enviada",
        autor_nome=_autor(user),
        autor_id=user.id,
        status=status,
    )
    m.canal = "robo"
    return m


def _instrucao_shopee(pedido: str, texto: str, *, ja_tem_numero: bool) -> str:
    if ja_tem_numero:
        return (
            f"Pedido {pedido} da Entrega Direta (Flex): nova tentativa de entrega sem "
            "sucesso. Avise a Shopee no MESMO atendimento deste chamado (não abra outro), "
            "só com os fatos:\n\n" + texto
        )
    return (
        f"Pedido {pedido} da Entrega Direta (Flex) que a transportadora não conseguiu "
        "entregar. Se o atendimento na Shopee deste pedido ainda não foi aberto, abra "
        "pelo Assistente do Vendedor (Envio e Frete); se já foi, avise no mesmo. Informe "
        "à Shopee o texto abaixo, só com os fatos, e anote o protocolo que ela der.\n\n" + texto
    )


async def _abrir_novo(
    session: AsyncSession, row: Logistica, plataforma: str, texto: str, user: User
) -> Chamado:
    """Chamado novo do botão — nunca reaproveita o de outro assunto (por isso
    não passa pelo `abrir_chamado_logistica`, que pega o mais novo do pedido)."""
    ch = Chamado(
        data=datetime.now(SAO_PAULO).date(),
        pedido_bling=(row.pedido_bling or "").strip() or None,
        pedido_marketplace=(row.pedido_marketplace or "").strip() or None,
        plataforma=row.plataforma,
        conta=row.conta,
        status_bling=row.status_bling,
        origem="logistica",
        origem_ref=str(row.id),
        canal="robo",
        observacao="Aberto pela aba Flex — a transportadora não conseguiu entregar",
    )
    await chamados_svc.preencher_do_pedido(session, ch)
    session.add(ch)
    await session.flush()
    if plataforma == PLAT_ML:
        aviso = (
            "Pedido Flex sem reclamação do comprador — a IA de Chamado abre no Fale "
            "conosco do Mercado Livre"
        )
    else:
        aviso = (
            "Pedido Flex da Shopee — a abertura fica registrada e a IA de Chamado abre "
            "pelo Assistente do Vendedor (instrução)"
        )
    session.add(chamados_svc.registrar_sistema(ch, aviso))
    return ch


async def registrar_chamado(
    session: AsyncSession,
    row: Logistica,
    plataforma: str,
    texto: str,
    user: User,
) -> tuple[Chamado, str]:
    """Abre o chamado do botão (ou leva a novidade ao que ele já abriu). NÃO commita."""
    texto = texto.strip()
    pedido = (row.pedido_marketplace or row.pedido_bling or "").strip()
    ch, acao = await como_vai_o_chamado(session, row, plataforma)
    if acao == ACAO_ACRESCENTADO:
        # Trava a abertura: a IA pode estar pegando agora (`/abrir-ml/pegar`).
        ab = (
            await session.execute(
                select(ChamadoMensagem)
                .where(
                    ChamadoMensagem.chamado_id == ch.id,
                    ChamadoMensagem.tipo == "abertura",
                )
                .order_by(ChamadoMensagem.created_at.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if ab is not None and ab.status == "pendente":
            agora = datetime.now(SAO_PAULO)
            ab.texto = f"{(ab.texto or '').rstrip()}\n\nAtualização {agora:%d/%m %H:%M}: {texto}"
        else:
            acao = ACAO_INSTRUCAO
    if acao == ACAO_ABERTO:
        if ch is None:
            ch = await _abrir_novo(session, row, plataforma, texto, user)
        if plataforma == PLAT_ML:
            session.add(_abertura(ch, texto, user, status="pendente"))
        else:
            session.add(_abertura(ch, texto, user, status="registrada"))
            session.add(_instrucao(ch, _instrucao_shopee(pedido, texto, ja_tem_numero=False), user))
    elif acao == ACAO_INSTRUCAO:
        if plataforma == PLAT_SHOPEE:
            # A IA só lê a última instrução não consumida: vai completa.
            corpo = _instrucao_shopee(pedido, texto, ja_tem_numero=_tem_numero(ch))
        else:
            corpo = (
                f"Pedido {pedido} (Flex): nova tentativa de entrega sem sucesso. Avise o "
                "Mercado Livre no MESMO atendimento deste chamado (não abra outro), só "
                "com os fatos:\n\n" + texto
            )
        session.add(_instrucao(ch, corpo, user))
    await session.flush()
    logger.info(
        "flex_nao_entregue_chamado",
        logistica_id=str(row.id),
        chamado_id=str(ch.id),
        acao=acao,
        plataforma=plataforma,
    )
    return ch, acao


async def recusar_repetido(session: AsyncSession, row: Logistica, agora: datetime) -> None:
    """Outro clique no mesmo pedido há menos de `JANELA_REPETIDO` → 409."""
    ultimo = next(iter(await avisos_anteriores(session, row)), None)
    if ultimo is not None and ultimo.created_at > agora - JANELA_REPETIDO:
        raise FlexNaoEntregueError("acabou_de_avisar", em=ultimo.created_at)


# ---------------------------------------------------------------- mensagem


def _ambigua_shopee(e: Exception) -> bool:
    """Falha que pode ter saído mesmo assim: conexão caiu no meio ou 5xx."""
    if isinstance(e, httpx.TimeoutException):
        return True
    if isinstance(e, httpx.TransportError) and not isinstance(e, httpx.ConnectError):
        return True
    return bool(re.search(r"\bHTTP 5\d\d\b", str(e)))


async def _mandar_ml(session: AsyncSession, row: Logistica, texto: str) -> tuple[str, str | None]:
    integ = await logistica_meli._ml_integration_for_conta(session, row.conta)
    if integ is None:
        return MENSAGEM_FALHOU, f"sem integração do Mercado Livre para a conta {row.conta or '—'}"
    client = logistica_meli._build_ml_client(session, integ)
    pedido = (row.pedido_marketplace or "").strip()
    if not pedido:
        return MENSAGEM_FALHOU, "pedido sem o número do Mercado Livre"
    try:
        order = await logistica_meli._fetch_order(client, pedido)
    except Exception as e:  # noqa: BLE001 — leitura: nada saiu
        return MENSAGEM_FALHOU, f"não deu para ler o pedido no Mercado Livre ({str(e)[:120]})"
    pack = str(order.get("pack_id") or order.get("id") or "").strip()
    comprador = (order.get("buyer") or {}).get("id")
    vendedor = (order.get("seller") or {}).get("id") or (client.creds or {}).get("user_id")
    if not (pack and comprador and vendedor):
        return MENSAGEM_FALHOU, "o Mercado Livre não informou o comprador do pedido"
    try:
        r = await client.enviar_mensagem_pack(pack, vendedor, comprador, texto)
    except httpx.ConnectError as e:
        return MENSAGEM_FALHOU, f"sem conexão com o Mercado Livre ({str(e)[:120]})"
    except httpx.TransportError:
        return (
            MENSAGEM_INCERTA,
            "a conexão com o Mercado Livre caiu no envio — confira no ML se a mensagem saiu",
        )
    except Exception as e:  # noqa: BLE001
        return MENSAGEM_FALHOU, f"erro ao falar com o Mercado Livre ({str(e)[:120]})"
    corpo = atendimento_ml._json(r)
    if 200 <= r.status_code < 300:
        if atendimento_ml._barrada_no_envio(corpo):
            return MENSAGEM_FALHOU, "o Mercado Livre barrou a mensagem (moderação)"
        return MENSAGEM_ENVIADA, None
    if r.status_code >= 500:
        return (
            MENSAGEM_INCERTA,
            f"o Mercado Livre respondeu {r.status_code} — confira no ML se a mensagem saiu",
        )
    motivo = atendimento_ml._motivo_bloqueio(corpo) or atendimento_ml._codigo_erro(r)
    return (
        MENSAGEM_FALHOU,
        f"o Mercado Livre recusou (HTTP {r.status_code}{' ' + motivo if motivo else ''})",
    )


async def _mandar_shopee(
    session: AsyncSession, row: Logistica, texto: str
) -> tuple[str, str | None]:
    integ = await logistica_shopee._shopee_integration_for_conta(session, row.conta)
    if integ is None:
        return MENSAGEM_FALHOU, f"sem integração da Shopee para a loja {row.conta or '—'}"
    order_sn = (row.pedido_marketplace or "").strip()
    if not order_sn:
        return MENSAGEM_FALHOU, "pedido sem o número da Shopee"
    client = logistica_shopee._build_shopee_client(session, integ)
    try:
        comprador = await client.get_order_buyer(order_sn)
    except Exception as e:  # noqa: BLE001 — leitura: nada saiu
        return MENSAGEM_FALHOU, f"não deu para ler o pedido na Shopee ({str(e)[:120]})"
    to_id = comprador.get("buyer_user_id")
    if not to_id:
        return MENSAGEM_FALHOU, "a Shopee não informou o comprador do pedido"
    try:
        await client.chat_send_message(to_id, text=texto)
    except Exception as e:  # noqa: BLE001 — recusa da Shopee: o texto dela explica
        if _ambigua_shopee(e):
            return (
                MENSAGEM_INCERTA,
                f"a Shopee não confirmou ({str(e)[:100]}) — confira no chat se a mensagem saiu",
            )
        return MENSAGEM_FALHOU, f"a Shopee recusou ({str(e)[:160]})"
    return MENSAGEM_ENVIADA, None


async def mandar_mensagem(
    session: AsyncSession, row: Logistica, plataforma: str, texto: str
) -> tuple[str, str | None]:
    """Manda a mensagem ao comprador. Nunca levanta: (status, motivo da falha)."""
    texto = texto.strip()
    try:
        if plataforma == PLAT_ML:
            res = await _mandar_ml(session, row, texto)
        else:
            res = await _mandar_shopee(session, row, texto)
    except Exception as e:  # noqa: BLE001 — a falha vira nota, não 500
        res = (MENSAGEM_INCERTA, f"erro inesperado no envio ({str(e)[:120]}) — confira se saiu")
    logger.info(
        "flex_nao_entregue_mensagem",
        logistica_id=str(row.id),
        plataforma=plataforma,
        status=res[0],
        erro=res[1],
    )
    return res


_O_QUE_FOI_FEITO = {
    ACAO_ABERTO: "chamado aberto",
    ACAO_ACRESCENTADO: "novidade somada à abertura que ainda está na fila",
    ACAO_INSTRUCAO: "novidade enviada à IA de Chamado como instrução",
}


def nota_enviando(ch: Chamado, *, acao: str, user: User, mandar: bool) -> ChamadoMensagem:
    """A nota do clique, gravada junto com o chamado (antes da mensagem sair)."""
    msg = NOTA_ENVIANDO if mandar else "sem mensagem ao cliente"
    return chamados_svc.registrar_sistema(
        ch, f"{PREFIXO_NOTA} ({_autor(user)}): {_O_QUE_FOI_FEITO[acao]}; {msg}"
    )


def completar_nota(
    nota: ChamadoMensagem,
    *,
    plataforma: str,
    mensagem_status: str,
    mensagem_erro: str | None,
    texto_cliente: str,
) -> None:
    """Troca o "enviando…" pelo resultado da mensagem ao cliente."""
    canal = _CANAL_MENSAGEM[plataforma]
    texto = (texto_cliente or "").strip()
    if mensagem_status == MENSAGEM_ENVIADA:
        msg = f"mensagem ao cliente enviada pelo {canal}: «{texto}»"
    elif mensagem_status == MENSAGEM_INCERTA:
        msg = f"mensagem ao cliente SEM CONFIRMAÇÃO ({mensagem_erro}): «{texto}»"
    elif mensagem_status == MENSAGEM_FALHOU:
        msg = f"mensagem ao cliente NÃO saiu ({mensagem_erro}) — mandar à mão: «{texto}»"
    else:
        return
    nota.texto = (nota.texto or "").replace(NOTA_ENVIANDO, msg)


def preview_dados(
    row: Logistica,
    plataforma: str,
    tk: LogisticaTracken | None,
    nome: str | None,
    agora: datetime,
) -> dict[str, Any]:
    t = ultima_tentativa(tk)
    return {
        "plataforma": plataforma,
        "plataforma_label": _PLATAFORMA_LABEL[plataforma],
        "canal_mensagem": _CANAL_MENSAGEM[plataforma],
        "limite_cliente": ML_LIMITE if plataforma == PLAT_ML else None,
        "cliente_nome": nome,
        "motivo": t.motivo,
        "tentativa_em": t.em,
        "tentativas": tk.tentativas if tk is not None else 0,
        "texto_chamado": texto_chamado(row, plataforma, t),
        "texto_cliente": texto_cliente(nome, plataforma, t, agora),
    }
