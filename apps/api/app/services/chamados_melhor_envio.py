"""Atendimentos do Melhor Envio na aba Chamados (13/11/2026, passo 2).

Vinicius: "consegue incluir o melhor envio… olhar o melhor envio os chamados e
responder pelo davinci". O Melhor Envio (frete próprio da Amazon) tem os chamados
dele em Envios › Atendimentos (ex.: AD952521063BR, "Falha na entrega"). A sonda de
13/11 mostrou que o token do painel (o da Conciliação) lê tudo pela API:

- a lista: `GET /api/v2/me/orders` com os filtros da aba (`PARAMS_ATENDIMENTOS`) —
  cada envio traz `tickets[]` (`number`, `status`, `summary`) e a tag
  `mi:reference_code` com o nº do pedido no Bling;
- a conversa: `GET /api/v2/me/tickets/{number}` → `interactions.comments[]`
  (`type_user` "Cliente" = nós; o resto é o atendente do ME).

Cada atendimento ATIVO vira uma linha (origem `melhor_envio`, plataforma "melhor
envio", canal `manual`, ligada ao pedido do Bling) e a conversa cai no histórico.
Canal `manual` deixa a linha fora do robô de tela e das varreduras de API do ML.

Passo 4 (13/11, Vinicius: "sempre sozinha"): a IA de Chamado pega o caso quando a
atendente fala e responde sem esperar instrução (`agent_analise`, `via_api`); a
coluna Status mostra Análise Robô (`chamados._robo_atende`). Dois dias parado depois
de um "esperar" vira lembrete pra IA cobrar no atendimento (`_lembrete_txt`).

Passo 3 (13/11): a réplica da aba vai pro atendimento no ME (`enviar`), pelo mesmo
`POST /api/v2/me/tickets/{n}` do botão Enviar da tela. Quando a leitura seguinte
traz a nossa fala de volta, ela não duplica (`_ja_gravada`, janela de 2 dias).

Atendimento resolvido no ME põe o chamado em Encerrado (aguardando fechamento) —
nenhum robô conclui (reforma de 19/09). Atendimento já resolvido que nunca entrou
aqui não vira linha: seria trabalho velho pra fechar à mão.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoAnexo, ChamadoMensagem
from app.services import chamados as chamados_svc
from app.services import melhor_envio as me
from app.services.devolucao_returns import iso_to_dt
from app.services.texto_html import limpar_html

logger = structlog.get_logger()

ORIGEM = "melhor_envio"
PLATAFORMA = "melhor envio"
URL_ATENDIMENTOS = "https://melhorenvio.com.br/painel/meus-envios#atendimentos"
# Erros do envio (a tela traduz os códigos; o resto vai cru, com o HTTP do ME).
ERRO_SEM_TOKEN = "melhor_envio_sem_token"  # noqa: S105 — código de erro, não senha
ERRO_ENCERRADO = "melhor_envio_atendimento_encerrado"
MAX_PAGINAS = 10
# Status do atendimento no ME que encerram o caso (comparados sem acento/caixa).
_STATUS_FINAIS = ("resolvido", "fechado", "encerrado", "cancelado", "concluido", "finalizado")
# Mesma fala gravada de novo? Nossa (eco do que um dia sairá pelo DaVinci) olha 2 dias;
# a do atendente, o mesmo minuto.
_JANELA_NOSSA = timedelta(days=2)
_JANELA_DELES = timedelta(minutes=5)
_INVOLUCRO = re.compile(r"(?i)</?\s*(html|body|head)\b[^>]*>")


def _sem_acento(s: str) -> str:
    return (
        "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")
        .strip()
        .lower()
    )


def ticket_ativo(status: str | None) -> bool:
    s = _sem_acento(status or "")
    return bool(s) and not s.startswith(_STATUS_FINAIS)


def _tag(envio: dict, nome: str) -> str | None:
    for t in envio.get("tags") or []:
        if isinstance(t, dict) and t.get("tag") == nome and t.get("url"):
            return str(t["url"]).strip() or None
    return None


@dataclass(frozen=True)
class Fala:
    direcao: str  # enviada (nós, digitado no site do ME) | recebida (atendente do ME)
    texto: str
    quando: datetime | None
    autor: str
    base: str = ""  # o texto sem o "[N anexos no Melhor Envio]" (eco da réplica com foto)


def fala_do_comentario(c: dict) -> Fala | None:
    """Um comentário do atendimento → fala do histórico (None se vazio)."""
    bruto = str(c.get("description_html") or c.get("description") or "")
    # O ME embrulha a fala em <html><body> — sozinho, isso não é "HTML" pro limpar_html.
    texto = limpar_html(_INVOLUCRO.sub("", bruto)).strip()
    base = texto
    anexos = c.get("attachments") if isinstance(c.get("attachments"), list) else []
    if anexos:
        n = len(anexos)
        aviso = f"[{n} anexo{'s' if n > 1 else ''} no Melhor Envio]"
        texto = f"{texto}\n{aviso}" if texto else aviso
    if not texto:
        return None
    pessoa = c.get("person") if isinstance(c.get("person"), dict) else {}
    nome = str(pessoa.get("name") or c.get("name_requester_comment") or "").strip()
    nossa = _sem_acento(str(c.get("type_user") or "")) == "cliente"
    if nossa:
        autor = f"{nome or 'nós'} · no site do Melhor Envio"
    else:
        # O ME já assina a atendente como "Natalia A S - Melhor Envio" (visto 13/11).
        autor = nome or "Atendente"
        if "melhor envio" not in _sem_acento(autor):
            autor = f"{autor} · Melhor Envio"
    return Fala(
        direcao="enviada" if nossa else "recebida",
        texto=texto,
        quando=iso_to_dt(str(c.get("date_creation_raw") or "")) or None,
        autor=autor,
        base=base,
    )


def _comentarios(corpo: dict) -> list[dict]:
    inter = corpo.get("interactions") if isinstance(corpo.get("interactions"), dict) else {}
    lista = inter.get("comments") if isinstance(inter.get("comments"), list) else []
    return [c for c in lista if isinstance(c, dict)]


def dia_da_abertura(corpo: dict) -> date | None:
    """Dia (SP) da primeira fala do atendimento — a coluna Data da aba."""
    horas = [f.quando for c in _comentarios(corpo) if (f := fala_do_comentario(c)) and f.quando]
    return min(horas).astimezone(chamados_svc.SAO_PAULO).date() if horas else None


async def _ja_gravada(session: AsyncSession, ch: Chamado, f: Fala) -> ChamadoMensagem | None:
    textos = {f.texto, f.base} - {""} if f.direcao == "enviada" else {f.texto}
    q = select(ChamadoMensagem).where(
        ChamadoMensagem.chamado_id == ch.id,
        ChamadoMensagem.direcao == f.direcao,
        ChamadoMensagem.texto.in_(sorted(textos)),
    )
    if f.quando is not None:
        janela = _JANELA_NOSSA if f.direcao == "enviada" else _JANELA_DELES
        q = q.where(ChamadoMensagem.created_at.between(f.quando - janela, f.quando + janela))
    return (await session.execute(q.limit(1))).scalar_one_or_none()


async def gravar_falas(session: AsyncSession, ch: Chamado, corpo: dict) -> int:
    """Grava no histórico as falas do atendimento que ainda não estão lá."""
    novas = 0
    primeira_nossa = True
    for c in _comentarios(corpo):
        f = fala_do_comentario(c)
        if f is None:
            continue
        tipo = "resposta"
        if f.direcao == "enviada":
            tipo = "abertura" if primeira_nossa else "replica"
            primeira_nossa = False
        gravada = await _ja_gravada(session, ch, f)
        if gravada is not None:
            # Fala que ESTA leitura gravou (autor "… Melhor Envio") acompanha o
            # nome atual; a nossa vinda do DaVinci (passo 3) fica com quem mandou.
            if gravada.autor_nome != f.autor and (gravada.autor_nome or "").endswith(
                "Melhor Envio"
            ):
                gravada.autor_nome = f.autor
            continue
        msg = chamados_svc.nova_mensagem(
            ch,
            texto=f.texto,
            tipo=tipo,
            direcao=f.direcao,
            autor_nome=f.autor,
            status="enviada" if f.direcao == "enviada" else "registrada",
        )
        msg.canal = "api"
        if f.quando is not None:
            msg.created_at = f.quando
            msg.enviada_at = f.quando
        session.add(msg)
        await session.flush()
        novas += 1
    return novas


async def _evento(session: AsyncSession, ch: Chamado, texto: str) -> None:
    ja = (
        await session.execute(
            select(ChamadoMensagem.id)
            .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.texto == texto)
            .limit(1)
        )
    ).scalar_one_or_none()
    if ja is None:
        session.add(chamados_svc.registrar_sistema(ch, texto))


async def aplicar_status(session: AsyncSession, ch: Chamado, status_me: str | None) -> str:
    """Status do atendimento no ME → estado do chamado. Devolve o que fez."""
    if ch.resolvido:
        return "concluido"
    if not ticket_ativo(status_me):
        if ch.status_plataforma in chamados_svc.STATUS_FINAIS:
            return "ja_encerrado"
        chamados_svc.set_status_plataforma(ch, chamados_svc.STATUS_ENCERRADO)
        await _evento(
            session,
            ch,
            f"Melhor Envio encerrou o atendimento ({status_me or 'sem status'}) "
            "— aguardando fechamento",
        )
        return "encerrado"
    evento = chamados_svc.sair_de_encerrado(
        ch, f"o atendimento voltou a {status_me} no Melhor Envio"
    )
    if evento is not None:
        session.add(evento)
        return "reaberto"
    return "ativo"


async def _chamado_do_ticket(session: AsyncSession, numero: str) -> Chamado | None:
    return (
        await session.execute(
            select(Chamado)
            .where(Chamado.origem == ORIGEM, Chamado.origem_ref == numero)
            .order_by(Chamado.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _novo_chamado(session: AsyncSession, envio: dict, ticket: dict, numero: str) -> Chamado:
    ch = Chamado(
        data=datetime.now(chamados_svc.SAO_PAULO).date(),  # vira o dia da abertura no ME
        origem=ORIGEM,
        origem_ref=numero,
        plataforma=PLATAFORMA,
        canal="manual",
        chamado=numero,
        chamado_url=URL_ATENDIMENTOS,
        pedido_bling=_tag(envio, "mi:reference_code"),
        pedido_marketplace=_tag(envio, "mi:marketplace_code"),
    )
    session.add(ch)
    await session.flush()
    # Conta, produto, SKU e situação do Bling vêm do pedido; a plataforma fica
    # "melhor envio" (o preencher só mexe no que está vazio).
    await chamados_svc.preencher_do_pedido(session, ch)
    session.add(
        chamados_svc.registrar_sistema(
            ch,
            f"Atendimento {numero} do Melhor Envio trazido para a aba "
            f"({ticket.get('summary') or 'sem assunto'}, rastreio {envio.get('tracking') or '—'})",
        )
    )
    return ch


async def _lista_de_atendimentos(token: str) -> tuple[dict[str, tuple[dict, dict]], int | None]:
    """{nº do atendimento: (envio, ticket)} da aba Atendimentos do ME."""
    achados: dict[str, tuple[dict, dict]] = {}
    pagina, ultima = 1, 1
    while pagina <= min(ultima, MAX_PAGINAS):
        http, corpo = await me.ler_envios(token, page=pagina, extra=me.PARAMS_ATENDIMENTOS)
        if http != 200:
            return achados, http
        for envio in corpo.get("data") or []:
            if not isinstance(envio, dict):
                continue
            for t in envio.get("tickets") or []:
                if isinstance(t, dict) and t.get("number") is not None:
                    achados[str(t["number"])] = (envio, t)
        try:
            ultima = int(corpo.get("last_page") or 1)
        except (TypeError, ValueError):
            ultima = 1
        pagina += 1
    return achados, None


async def sincronizar(session: AsyncSession) -> dict[str, Any]:
    """Uma passada: atendimentos ativos novos viram linha; os chamados do ME que
    ainda estão vivos aqui relêem a conversa e o status. Best-effort por caso.
    NÃO commita no meio — o caller fecha a sessão (o `session_scope` commita)."""
    resumo: dict[str, Any] = {
        "me_atendimentos": 0,
        "me_novos": 0,
        "me_lidos": 0,
        "me_falas": 0,
        "me_encerrados": 0,
        "me_falhas": 0,
    }
    token, _ = await me.token_do_painel(session)
    if not token:
        resumo["me_sem_token"] = 1
        return resumo
    lista, erro_http = await _lista_de_atendimentos(token)
    if erro_http is not None:
        logger.warning("chamados_melhor_envio_lista_falhou", http=erro_http)
        resumo["me_falhas"] += 1
    resumo["me_atendimentos"] = len(lista)

    criados: set = set()
    for numero, (envio, ticket) in lista.items():
        if await _chamado_do_ticket(session, numero) is None and ticket_ativo(ticket.get("status")):
            criados.add((await _novo_chamado(session, envio, ticket, numero)).id)
            resumo["me_novos"] += 1

    vivos = (
        (
            await session.execute(
                select(Chamado).where(
                    Chamado.origem == ORIGEM,
                    Chamado.resolvido.is_(False),
                    Chamado.origem_ref.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for ch in vivos:
        numero = str(ch.origem_ref)
        try:
            http, corpo = await me.ler_atendimento(token, numero)
            if http != 200:
                logger.warning("chamados_melhor_envio_ticket_falhou", ticket=numero, http=http)
                resumo["me_falhas"] += 1
                continue
            resumo["me_lidos"] += 1
            resumo["me_falas"] += await gravar_falas(session, ch, corpo)
            if ch.id in criados:
                ch.data = dia_da_abertura(corpo) or ch.data
            status_me = corpo.get("status") or (lista.get(numero, ({}, {}))[1].get("status"))
            if await aplicar_status(session, ch, status_me) == "encerrado":
                resumo["me_encerrados"] += 1
            await session.flush()
        except Exception as e:  # noqa: BLE001 — um caso não derruba a passada
            logger.warning("chamados_melhor_envio_caso_falhou", ticket=numero, err=str(e)[:200])
            resumo["me_falhas"] += 1
    resumo["me_em"] = datetime.now(UTC).isoformat()
    logger.info("chamados_melhor_envio_sync", **resumo)
    return resumo


async def enviar(session: AsyncSession, ch: Chamado, msg: ChamadoMensagem) -> None:
    """Réplica da aba → atendimento no Melhor Envio. Marca a mensagem `enviada` ou
    `falhou` (+ `erro`); nunca levanta. Lê o atendimento antes: o envio precisa do
    `order_id` do ME e atendimento já resolvido lá não recebe resposta daqui."""
    msg.canal = "api"

    def falhou(erro: str) -> None:
        msg.status = "falhou"
        msg.erro = erro[:300]
        logger.warning("chamado_melhor_envio_envio_falhou", chamado_id=str(ch.id), erro=msg.erro)

    token, _ = await me.token_do_painel(session)
    if not token:
        return falhou(ERRO_SEM_TOKEN)
    numero = str(ch.origem_ref or ch.chamado or "")
    http, corpo = await me.ler_atendimento(token, numero)
    if http != 200:
        return falhou(f"melhor_envio_{http}: {corpo.get('message') or 'não leu o atendimento'}")
    if not ticket_ativo(corpo.get("status")):
        return falhou(ERRO_ENCERRADO)
    anexos = (
        (await session.execute(select(ChamadoAnexo).where(ChamadoAnexo.mensagem_id == msg.id)))
        .scalars()
        .all()
    )
    http, resp = await me.enviar_no_atendimento(
        token,
        numero,
        order_id=str(corpo.get("order_id") or ""),
        texto=msg.texto,
        anexos=[(a.filename, a.blob, a.content_type) for a in anexos],
    )
    if http // 100 != 2:
        detalhe = resp.get("message") or resp.get("error") or resp.get("texto") or ""
        return falhou(f"melhor_envio_{http}: {str(detalhe)[:200]}")
    msg.status = "enviada"
    msg.erro = None
    msg.enviada_at = datetime.now(UTC)
    logger.info("chamado_melhor_envio_enviada", chamado_id=str(ch.id), ticket=numero)
