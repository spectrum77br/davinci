"""Abrir o atendimento do Melhor Envio pela Logística (18/11/2026, passo 5).

Vinicius: "nos Amazon envio próprio … você cria algo que a gente clica, Abrir
chamado, ele vem os motivos do Melhor Envio e a gente abre o chamado" — os
clientes dizem que não receberam e o atendimento no ME era aberto à mão.

O botão fica na coluna Chamado da aba Amazon, nas linhas Envio próprio (rastreio
dos Correios, etiqueta do ME) sem chamado. Tudo pela API do ME, com o mesmo token
que lê e responde os atendimentos (passos 2–4, `chamados_melhor_envio`): nada de
robô de tela. O caminho foi lido no JS do painel do ME (ver `melhor_envio`):

1. `preparar` — acha o envio pelo rastreio e traz os motivos do "Iniciar
   atendimento" (a mesma lista do site, na ordem dele);
2. `consultar` — nos motivos que o site consulta antes (acareação, atraso,
   comprovante), o que o ME diz do envio: prazo, 48 h depois da entrega, se libera;
3. `abrir` — confere tudo de novo no servidor, abre no ME e cria na hora o chamado
   da aba Chamados (origem `melhor_envio`, ligado ao pedido), então a coluna
   Chamado da Logística já mostra "Aberto". A conversa segue pela leitura do :25 e
   a IA de Chamado responde quando a atendente fala (passo 4).

Alteração de endereço e Produto avariado têm formulário próprio no site do ME
(endereço novo campo a campo; avaria com peças e foto do recebedor): ficam com o
link pro site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, Logistica
from app.services import chamados as chamados_svc
from app.services import chamados_melhor_envio as cme
from app.services import logistica_amazon_canal, logistica_track
from app.services import melhor_envio as me
from app.services.texto_html import limpar_html

logger = structlog.get_logger()

# Como a janela trata cada motivo (o slug é o do ME).
COMO_ACAREACAO = "acareacao"  # Entrega não reconhecida: recebedor + descrição + anexos
COMO_ATRASO = "atraso"  # Atraso na entrega: só abre se o ME disser que atrasou
COMO_COMPROVANTE = "comprovante"  # Comprovante de entrega: sem campos
COMO_SITE = "site"  # formulário próprio no site do ME
COMO_DESCRICAO = "descricao"  # o resto: descrição obrigatória
_COMO = {
    "confrontation": COMO_ACAREACAO,
    "delayed_shipping": COMO_ATRASO,
    "delivery_receipt": COMO_COMPROVANTE,
    "address_change": COMO_SITE,
    "damage": COMO_SITE,
}
COMO_CONSULTA = (COMO_ACAREACAO, COMO_ATRASO, COMO_COMPROVANTE)
# `configs_type.current_status` que liberam o botão do site.
_LIBERA = {
    "confrontation": "confrontation_opening_allowed",
    "delayed_shipping": "shipping_delayed_late",
}

# Limites da tela do ME (dash.js, módulo 79710 e o campo de arquivos).
DESCRICAO_MIN, DESCRICAO_MAX = 10, 2048
RECEBEDOR_MIN, RECEBEDOR_MAX = 6, 120
ANEXOS_MAX = 10
ANEXO_MAX_BYTES = 3 * 1024 * 1024
ANEXO_TIPOS = ("image/jpeg", "image/png", "image/gif", "application/pdf")


class AberturaRecusadaError(Exception):
    """Motivo (em português, pra tela) de não dar pra abrir."""

    def __init__(self, code: str, mensagem: str, http: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.mensagem = mensagem
        self.http = http


def como_do_motivo(slug: str | None) -> str:
    return _COMO.get((slug or "").strip(), COMO_DESCRICAO)


def liberado(slug: str, situacao: str | None) -> bool:
    """O ME deixa abrir? Acareação e atraso: só no status que o site libera; o
    resto, enquanto não vier "blocked"/"expired"."""
    st = (situacao or "").strip().lower()
    if slug in _LIBERA:
        return st == _LIBERA[slug]
    return not any(p in st for p in ("blocked", "expired"))


def problema_da_linha(linha: Logistica) -> str | None:
    """None quando a linha pode ter o botão; senão o motivo (pra tela)."""
    if (linha.amazon_canal or "") != logistica_amazon_canal.CANAL_PROPRIO:
        return "Só pedidos da Amazon com Envio próprio (etiqueta do Melhor Envio)."
    if not logistica_track.is_correios(linha.rastreio):
        return "O pedido ainda não tem o rastreio dos Correios."
    return None


@dataclass
class Envio:
    id: str
    rastreio: str
    status: str | None
    protocolo: str | None
    categoria: str
    atendimento_ativo: str | None  # nº do atendimento ainda aberto no ME
    pode_abrir: bool | None  # `ticket_opening.allowed` do ME (None = não disse)
    bruto: dict = field(repr=False, default_factory=dict)


async def _token(session: AsyncSession) -> str:
    token, _ = await me.token_do_painel(session)
    if not token:
        raise AberturaRecusadaError(
            "melhor_envio_sem_token",
            "O DaVinci está sem o acesso ao Melhor Envio (Conciliação › Amazon › Melhor Envio).",
            503,
        )
    return token


def _rastreio(linha: Logistica) -> str:
    return (linha.rastreio or "").strip().upper()


async def _envio_da_linha(token: str, linha: Logistica) -> Envio:
    rastreio = _rastreio(linha)
    http, corpo = await me.ler_envios(token, q=rastreio)
    if http != 200:
        raise AberturaRecusadaError(
            f"melhor_envio_{http}",
            f"O Melhor Envio não respondeu à busca do envio (HTTP {http}). Tente de novo.",
            502,
        )
    for e in corpo.get("data") or []:
        if not isinstance(e, dict):
            continue
        codigos = {str(e.get(k) or "").strip().upper() for k in ("tracking", "self_tracking")}
        if rastreio not in codigos or not e.get("id"):
            continue
        ativo = next(
            (
                str(t.get("number"))
                for t in e.get("tickets") or []
                if isinstance(t, dict)
                and t.get("number") is not None
                and cme.ticket_ativo(t.get("status"))
            ),
            None,
        )
        abertura = e.get("ticket_opening")
        pode = abertura.get("allowed") if isinstance(abertura, dict) else None
        return Envio(
            id=str(e["id"]),
            rastreio=rastreio,
            status=e.get("status"),
            protocolo=e.get("protocol"),
            categoria=me.categoria_do_envio(e.get("status")),
            atendimento_ativo=ativo,
            pode_abrir=pode if isinstance(pode, bool) else None,
            bruto=e,
        )
    raise AberturaRecusadaError(
        "envio_nao_achado",
        f"O rastreio {rastreio} não aparece nos envios do Melhor Envio desta conta.",
        404,
    )


async def chamado_vivo(session: AsyncSession, linha: Logistica) -> Chamado | None:
    """Chamado do ME ainda não concluído pra este pedido (não abre outro)."""
    if not (linha.pedido_bling or "").strip():
        return None
    return (
        await session.execute(
            select(Chamado)
            .where(
                Chamado.origem == cme.ORIGEM,
                Chamado.pedido_bling == linha.pedido_bling,
                Chamado.resolvido.is_(False),
            )
            .order_by(Chamado.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _travas(envio: Envio) -> None:
    if envio.atendimento_ativo:
        raise AberturaRecusadaError(
            "melhor_envio_ja_tem_atendimento",
            f"Este envio já tem o atendimento {envio.atendimento_ativo} aberto no Melhor Envio "
            "— ele entra na aba Chamados na próxima leitura.",
            409,
        )
    if envio.pode_abrir is False:
        raise AberturaRecusadaError(
            "melhor_envio_nao_deixa_abrir",
            "O Melhor Envio não deixa abrir atendimento neste envio agora.",
            409,
        )


async def preparar(session: AsyncSession, linha: Logistica) -> dict[str, Any]:
    """O que a janela precisa: o envio no ME e os motivos (na ordem do site)."""
    problema = problema_da_linha(linha)
    if problema:
        raise AberturaRecusadaError("logistica_sem_melhor_envio", problema)
    vivo = await chamado_vivo(session, linha)
    if vivo is not None:
        raise AberturaRecusadaError(
            "chamado_ja_aberto",
            f"Este pedido já tem o atendimento {vivo.chamado} na aba Chamados.",
            409,
        )
    token = await _token(session)
    envio = await _envio_da_linha(token, linha)
    _travas(envio)
    http, motivos = await me.ler_motivos_atendimento(token, envio.categoria)
    if http != 200 or not motivos:
        raise AberturaRecusadaError(
            f"melhor_envio_{http}",
            f"O Melhor Envio não trouxe os motivos (HTTP {http}). Tente de novo.",
            502,
        )
    return {
        "envio": {
            "id": envio.id,
            "rastreio": envio.rastreio,
            "status": envio.status,
            "protocolo": envio.protocolo,
        },
        "motivos": [
            {
                "slug": str(m.get("slug")),
                "nome": str(m.get("name") or m.get("slug")),
                "como": como_do_motivo(m.get("slug")),
            }
            for m in motivos
            if m.get("slug")
        ],
        "url_site": cme.URL_ATENDIMENTOS,
    }


def _texto(valor: object) -> str:
    return limpar_html(str(valor or "")).strip()


def resumo_da_consulta(slug: str, corpo: dict) -> dict[str, Any]:
    """O que a janela mostra da consulta do ME: título, texto, detalhes do envio
    (transportadora, datas, prazo restante) e se libera."""
    det = corpo.get("ticket_details") if isinstance(corpo.get("ticket_details"), dict) else {}
    tipo = corpo.get("configs_type") if isinstance(corpo.get("configs_type"), dict) else {}
    situacao = str(tipo.get("current_status") or "") or None
    detalhes = [
        {
            "chave": str(d.get("key") or ""),
            "titulo": _texto(d.get("title")),
            "valor": _texto(d.get("value")),
        }
        for d in corpo.get("order_details") or []
        if isinstance(d, dict) and (d.get("title") or d.get("value"))
    ]
    return {
        "situacao": situacao,
        "liberado": liberado(slug, situacao),
        "titulo": _texto(det.get("title")) or None,
        "texto": _texto(det.get("description")) or None,
        "info": _texto(det.get("additional_info")) or None,
        "detalhes": detalhes,
        "aceita_anexos": bool(tipo.get("allows_attachments")),
    }


async def consultar(session: AsyncSession, linha: Logistica, slug: str) -> dict[str, Any]:
    if como_do_motivo(slug) not in COMO_CONSULTA:
        return {
            "situacao": None,
            "liberado": True,
            "titulo": None,
            "texto": None,
            "info": None,
            "detalhes": [],
            "aceita_anexos": False,
        }
    problema = problema_da_linha(linha)
    if problema:
        raise AberturaRecusadaError("logistica_sem_melhor_envio", problema)
    token = await _token(session)
    envio = await _envio_da_linha(token, linha)
    return await _consulta(token, envio, slug)


async def _consulta(token: str, envio: Envio, slug: str) -> dict[str, Any]:
    http, corpo = await me.ler_config_atendimento(token, slug, envio.id)
    if http != 200:
        raise AberturaRecusadaError(
            f"melhor_envio_{http}",
            str(corpo.get("message") or f"O Melhor Envio não respondeu à consulta (HTTP {http}).")[
                :300
            ],
            502,
        )
    return resumo_da_consulta(slug, corpo)


def validar_campos(
    como: str,
    *,
    descricao: str,
    recebedor: str,
    anexos: list[tuple[str, bytes, str]],
) -> None:
    """As mesmas regras da tela do ME (a API recusaria de qualquer jeito)."""
    if como == COMO_SITE:
        raise AberturaRecusadaError(
            "motivo_so_no_site",
            "Esse motivo tem formulário próprio: abra pelo site do Melhor Envio.",
        )
    if como in (COMO_ACAREACAO, COMO_DESCRICAO) and not (
        DESCRICAO_MIN <= len(descricao) <= DESCRICAO_MAX
    ):
        raise AberturaRecusadaError(
            "descricao_tamanho",
            f"A descrição precisa ter de {DESCRICAO_MIN} a {DESCRICAO_MAX} letras.",
        )
    if como != COMO_ACAREACAO:
        if recebedor or anexos:
            raise AberturaRecusadaError(
                "campo_so_na_acareacao",
                "Nome de quem recebeu e anexos só vão na Entrega não reconhecida.",
            )
        return
    if recebedor and not (RECEBEDOR_MIN <= len(recebedor) <= RECEBEDOR_MAX):
        raise AberturaRecusadaError(
            "recebedor_tamanho",
            f"O nome de quem recebeu precisa ter de {RECEBEDOR_MIN} a {RECEBEDOR_MAX} letras.",
        )
    if len(anexos) > ANEXOS_MAX:
        raise AberturaRecusadaError("anexos_demais", f"No máximo {ANEXOS_MAX} arquivos.")
    for nome, blob, ctype in anexos:
        if ctype not in ANEXO_TIPOS:
            raise AberturaRecusadaError("anexo_tipo", f"{nome}: só jpg, png, gif ou pdf.")
        if not blob:
            raise AberturaRecusadaError("anexo_vazio", f"{nome} está vazio.")
        if len(blob) > ANEXO_MAX_BYTES:
            raise AberturaRecusadaError("anexo_grande", f"{nome}: cada arquivo vai até 3 MB.")


async def abrir(
    session: AsyncSession,
    linha: Logistica,
    *,
    slug: str,
    descricao: str = "",
    recebedor: str = "",
    anexos: list[tuple[str, bytes, str]] | None = None,
    por: str,
) -> Chamado:
    """Abre o atendimento no ME e cria o chamado da aba Chamados. Não commita."""
    descricao = (descricao or "").strip()
    recebedor = (recebedor or "").strip()
    anexos = list(anexos or [])
    como = como_do_motivo(slug)
    validar_campos(como, descricao=descricao, recebedor=recebedor, anexos=anexos)
    problema = problema_da_linha(linha)
    if problema:
        raise AberturaRecusadaError("logistica_sem_melhor_envio", problema)
    vivo = await chamado_vivo(session, linha)
    if vivo is not None:
        raise AberturaRecusadaError(
            "chamado_ja_aberto",
            f"Este pedido já tem o atendimento {vivo.chamado} na aba Chamados.",
            409,
        )
    token = await _token(session)
    envio = await _envio_da_linha(token, linha)
    _travas(envio)
    http, motivos = await me.ler_motivos_atendimento(token, envio.categoria)
    nomes = {str(m.get("slug")): str(m.get("name") or m.get("slug")) for m in motivos}
    if http == 200 and slug not in nomes:
        raise AberturaRecusadaError(
            "motivo_invalido", "Esse motivo não vale para este envio no Melhor Envio."
        )
    if como in COMO_CONSULTA:
        consulta = await _consulta(token, envio, slug)
        if not consulta["liberado"]:
            raise AberturaRecusadaError(
                "melhor_envio_nao_libera",
                consulta["titulo"]
                or "O Melhor Envio não libera esse motivo para este envio agora.",
            )

    http, resp = await me.abrir_atendimento(
        token,
        envio_id=envio.id,
        tipo=slug,
        descricao=descricao or None,
        recebedor=recebedor or None,
        anexos=anexos,
    )
    if http // 100 != 2:
        detalhe = resp.get("message") or resp.get("error") or resp.get("texto") or ""
        erros = resp.get("errors")
        if isinstance(erros, dict) and erros:
            detalhe = "; ".join(
                f"{k}: {' '.join(map(str, v)) if isinstance(v, list) else v}"
                for k, v in erros.items()
            )
        logger.warning("melhor_envio_abrir_recusado", http=http, detalhe=str(detalhe)[:300])
        raise AberturaRecusadaError(
            f"melhor_envio_{http}",
            f"O Melhor Envio recusou: {str(detalhe)[:300] or f'HTTP {http}'}",
            502,
        )
    ticket = resp.get("ticket") if isinstance(resp.get("ticket"), dict) else resp
    numero = str(ticket.get("number") or "").strip()
    if not numero:
        # Aberto lá sem número na resposta: a leitura do :25 traz o chamado.
        logger.warning("melhor_envio_abrir_sem_numero", pedido=linha.pedido_bling)
        raise AberturaRecusadaError(
            "melhor_envio_sem_numero",
            "O Melhor Envio aceitou, mas não devolveu o número — o chamado entra na aba "
            "Chamados na próxima leitura (minuto 25).",
            502,
        )

    ch = await cme._chamado_do_ticket(session, numero)
    if ch is None:
        ch = Chamado(
            data=datetime.now(chamados_svc.SAO_PAULO).date(),
            origem=cme.ORIGEM,
            origem_ref=numero,
            plataforma=cme.PLATAFORMA,
            canal="manual",
            chamado=numero,
            chamado_url=cme.URL_ATENDIMENTOS,
            pedido_bling=linha.pedido_bling or cme._tag(envio.bruto, "mi:reference_code"),
            pedido_marketplace=linha.pedido_marketplace
            or cme._tag(envio.bruto, "mi:marketplace_code"),
        )
        session.add(ch)
        await session.flush()
        await chamados_svc.preencher_do_pedido(session, ch)
    session.add(
        chamados_svc.registrar_sistema(
            ch,
            f"Atendimento {numero} aberto no Melhor Envio pela Logística por {por} "
            f"({nomes.get(slug, slug)}, rastreio {envio.rastreio})",
        )
    )
    # A abertura do jeito que o ME guardou ("Nome de quem recebeu…") vira a 1ª fala;
    # se a leitura falhar agora, a do :25 traz.
    try:
        http, corpo = await me.ler_atendimento(token, numero)
        if http == 200:
            await cme.gravar_falas(session, ch, corpo)
    except Exception as e:  # noqa: BLE001 — o atendimento já está aberto
        logger.warning("melhor_envio_abrir_leitura_falhou", ticket=numero, err=str(e)[:200])
    await session.flush()
    logger.info(
        "melhor_envio_atendimento_aberto",
        ticket=numero,
        pedido=linha.pedido_bling,
        motivo=slug,
        por=por,
    )
    return ch
