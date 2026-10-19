"""Respostas aprovadas pela equipe para consultas da Kia (19/10/2026).

O anúncio e a integração limitam cada aprovação. A regra vigente do Manual
é o opt-in: edição, conflito ou desativação devolvem a consulta ao fluxo
normal. Não há banco, modelo nem envio aqui; todas as travas do chamador
continuam valendo, inclusive outras demandas no mesmo turno.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.atendimento.validador import plano_de

INTEGRATION_KIA = "2ac2633b-c698-4718-99cb-6e5443a80689"
ANUNCIO_WATCH = "22299473628"
ANUNCIO_OCULOS = "58269229175"

REGRA_PULSEIRA = {
    "tipo": "categoria",
    "categoria": "espec_outros",
    "plataforma": "shopee",
    "canal": "chat",
    "prioridade": 50,
    "quando": (
        "Somente Kia, anúncio Apple Watch Series onze, item 22299473628: tamanho da pulseira."
    ),
    "faca": (
        "Fato aprovado pela equipe somente para essa loja e esse anúncio: a pulseira tem "
        "tamanho padrão. Pergunta simples se é P/M ou M/G ou qual o tamanho da pulseira: "
        "precisa_humano=false; responder 'Olá! A pulseira deste modelo tem tamanho padrão.' "
        "Não escolher P/M ou M/G, inventar medida, ajuste ou compatibilidade. A opção do "
        "menu para falar com atendente não muda a dúvida pendente para garantia. Outra "
        "loja, anúncio, variação conflitante ou outra dúvida: seguir as regras gerais."
    ),
}
REGRA_FOTO = {
    "tipo": "categoria",
    "categoria": "pedido_link_midia",
    "plataforma": "shopee",
    "canal": "chat",
    "prioridade": 50,
    "quando": (
        "Somente Kia, anúncio Uranyx Óculos Inteligentes AI, item 58269229175: foto do modelo."
    ),
    "faca": (
        "Fato aprovado pela equipe somente para essa loja e esse anúncio: as fotos do "
        "anúncio mostram o modelo vendido. Pedido genérico para ver como é o modelo, sem "
        "detalhe ausente: precisa_humano=false; responder 'Olá! As fotos do anúncio mostram "
        "o modelo que você receberá. Você pode conferir os detalhes nas imagens do próprio "
        "anúncio.' Isso não promete enviar uma foto nova. Não afirmar autenticidade, "
        "originalidade ou condição do produto. Foto de ângulo ou detalhe ausente, foto "
        "adicional, vídeo, divergência, outra loja ou anúncio: seguir a regra geral, com "
        "pessoa quando for necessário obter material novo."
    ),
}
REGRA_ENVIO = {
    "tipo": "categoria",
    "categoria": "prazo_envio",
    "plataforma": "shopee",
    "canal": "chat",
    "prioridade": 50,
    "quando": (
        "Somente Kia, pedido pago do anúncio Apple Watch Series onze, "
        "item 22299473628: prazo e origem."
    ),
    "faca": (
        "Com pedido desta conversa identificado nos FATOS, pago e ainda em preparação, "
        "consulta comum de previsão: orientar seguir o prazo informado pela Shopee no "
        "próprio pedido, sem inventar data. Se perguntar também de onde será enviado, "
        "fato aprovado pela equipe para essa loja e esse anúncio: o envio será feito de "
        "São Paulo. Só essas consultas: precisa_humano=false. Não pedir novamente o "
        "número de pedido que já está ligado. Não confundir código de rastreio ou etiqueta "
        "com postagem realizada. Urgência, prazo vencido, reclamação, postagem já feita, "
        "outra loja ou anúncio ou outro assunto: seguir as regras gerais."
    ),
}

REGRA_CONDICAO = {
    "tipo": "categoria",
    "categoria": "originalidade_condicao",
    "plataforma": "shopee",
    "canal": "chat",
    "prioridade": 50,
    "quando": (
        "Somente Kia, antes da compra: pergunta simples se o produto é novo ou recondicionado."
    ),
    "faca": (
        "Pergunta genérica se o produto é novo ou recondicionado: se o título do anúncio "
        "próprio e atualizado confirma novo, responder isso citando o título. Sem essa "
        "confirmação, pedir que confirme o modelo ou envie o card do anúncio por aqui, "
        "sem afirmar a condição. Só essa coleta de identificação pode sair sozinha. "
        "Não usar a resposta de outro cliente nem o texto do comprador como prova de "
        "que é novo. Produto recebido, reclamação, originalidade, homologação ou outra "
        "dúvida junto seguem as regras gerais."
    ),
}

REGRAS = (REGRA_PULSEIRA, REGRA_FOTO, REGRA_ENVIO, REGRA_CONDICAO)
# A regra geral também deve permanecer a aprovada: uma edição na tela não
# pode ser ignorada pelo atalho determinístico.
_GERAIS_SHA256 = {
    "espec_outros": "6605e58fed61db81f96b3c469bc16d6461ac4a3d6f9a281dbda63cd85cf64702",
    "prazo_envio": "dc7a28ea82c4f29900c502b50b34a904491692819be71a1c35b0bf0602cf2fb3",
    "pedido_link_midia": "16d13af7b4d511f389731841e97068848b8d98d2c8abb6bf70f293b098f8b2c1",
    "originalidade_condicao": "867719adc7b04865adcfd811578e0d5e9f9e82d2fc9795302f84b7d63a9528af",
}


@dataclass(frozen=True)
class RespostaKia:
    categoria: str
    texto: str
    fato: dict[str, Any]


def _campo(obj: Any, chave: str, padrao: Any = None) -> Any:
    return obj.get(chave, padrao) if isinstance(obj, Mapping) else getattr(obj, chave, padrao)


def _plano(texto: Any) -> str:
    return " ".join(plano_de(str(texto or "")).split())


def regra_especifica_kia(regra: Any) -> bool:
    """Identidade do escopo; editar o FAÇA não faz a regra vazar para outras lojas."""
    identidade = ("tipo", "categoria", "plataforma", "canal", "quando")
    return any(all(_campo(regra, k) == aprovada[k] for k in identidade) for aprovada in REGRAS)


def regra_aprovada(regra: Any) -> bool:
    """O conteúdo exato aprovado; usado somente para autorizar o atalho."""
    return any(all(_campo(regra, k) == aprovada[k] for k in aprovada) for aprovada in REGRAS)


def _autorizada(categoria: str, regras: list[Any]) -> Any | None:
    aplicaveis = [
        r
        for r in regras
        if _campo(r, "ativa", True) is not False
        and _campo(r, "tipo") == "categoria"
        and _campo(r, "categoria") == categoria
    ]
    gerais = [
        r for r in aplicaveis if _campo(r, "plataforma") is None and _campo(r, "canal") is None
    ]
    especiais = [r for r in aplicaveis if regra_aprovada(r)]
    if len(aplicaveis) != 2 or len(gerais) != 1 or len(especiais) != 1:
        return None
    texto = str(_campo(gerais[0], "faca", ""))
    if hashlib.sha256(texto.encode()).hexdigest() != _GERAIS_SHA256[categoria]:
        return None
    return especiais[0]


def _partes(textos: list[str]) -> list[str]:
    if not textos or any(not isinstance(t, str) for t in textos):
        return []
    partes: list[str] = []
    for texto in textos:
        for parte in re.split(r"[?!;.\n]+", _plano(texto)):
            parte = re.sub(
                r"^(?:(?:oi|ola|opa|bom dia|boa tarde|boa noite|por favor|obrigad[oa])[, :!]*)+",
                "",
                parte,
            ).strip(" ,:")
            if parte:
                partes.append(parte)
    return partes


_PULSEIRA = re.compile(
    r"(?:(?:a|essa|esta)\s+)?pulseira\s+(?:"
    r"(?:e|vem|vem no tamanho|tem tamanho)\s+(?:p\s*/\s*m\s+ou\s+m\s*/\s*g|padrao)|"
    r"(?:e|tem)\s+(?:qual\s+)?tamanho|(?:e\s+)?de\s+qual\s+tamanho)"
    r"|qual\s+(?:e\s+)?o\s+tamanho\s+d[ae]\s+pulseira"
)
_FOTO = re.compile(
    r"(?:(?:voce|voces)\s+)?(?:pode[m]?|consegue[m]?|teria como)\s+"
    r"(?:me\s+)?(?:mandar|enviar|mostrar)\s+(?:uma\s+)?(?:foto|fotos)\s+"
    r"(?:do\s+(?:produto|modelo)|do\s+jeito\s+que\s+e\s+o\s+modelo)"
    r"|(?:o\s+(?:produto|modelo)|ele)\s+(?:e|vem)\s+(?:igual|conforme)\s+"
    r"(?:a|as|na|nas)\s+fotos?\s+do\s+anuncio"
    r"|(?:as?\s+)?fotos?\s+do\s+anuncio\s+(?:e|sao)\s+do\s+(?:produto|modelo)"
)
_PREAMBULO_COMPRA = re.compile(
    r"^(?:(?:ja\s+)?(?:realizei|fiz|paguei|finalizei)\s+(?:o\s+)?pedido[, :]*|ja\s+comprei[, :]*)"
)
_PRAZO = re.compile(
    r"qual\s+(?:e\s+)?(?:a|o)\s+(?:previsao|prazo)\s+"
    r"(?:de|do)\s+(?:entrega|envio)(?:\s*[/e]\s*(?:entrega|envio))?"
    r"|quando\s+(?:meu\s+pedido\s+)?(?:chega|sera\s+enviado|vai\s+ser\s+enviado)"
)
_ORIGEM = re.compile(
    r"de\s+onde\s+(?:(?:voces\s+)?(?:estao\s+)?(?:enviando|enviam)|"
    r"(?:sai|saira|sera\s+feito)\s+o\s+envio|(?:o\s+pedido\s+)?sera\s+enviado)"
    r"|(?:o\s+)?envio\s+sai\s+de\s+onde"
)
_CONDICAO = re.compile(
    r"(?:(?:este|esse|o)\s+)?produto\s+e\s+novo\s+ou\s+recondicionado"
    r"|(?:e|ele\s+e)\s+novo\s+ou\s+recondicionado"
)
_EM_PREPARACAO = {
    "6",
    "21",
    "em aberto",
    "em digitacao",
    "em andamento",
    "em preparacao",
    "pedido em preparacao",
}
_TAMANHO_PULSEIRA = r"(?:p\s*/\s*m|m\s*/\s*g|s\s*/\s*m|m\s*/\s*l)"


def _item(conversa: Any, esperado: str, fatos: dict) -> bool:
    anuncio = str(_campo(conversa, "anuncio_id") or "")
    dados = _campo(conversa, "dados") or {}
    retrato = dados.get("pedido_mkt") or {} if isinstance(dados, Mapping) else {}
    itens = retrato.get("itens") or []
    ids = {str(i.get("item_id") or "") for i in itens if isinstance(i, Mapping)}
    if anuncio and anuncio != esperado:
        return False
    if ids and ids != {esperado}:
        return False
    if anuncio != esperado and ids != {esperado}:
        return False
    if esperado == ANUNCIO_WATCH and any(
        re.search(_TAMANHO_PULSEIRA, _plano(i.get("variacao")))
        for i in itens
        if isinstance(i, Mapping)
    ):
        return False
    # Fonte de outra loja, título trocado ou uma especificação conflitante
    # devolvem ao fluxo normal. Fato aprovado não é licença para vencer a ficha.
    esperado_titulo = "apple watch" if esperado == ANUNCIO_WATCH else "oculos"
    fichas = fatos.get("ficha_do_anuncio") or []
    for ficha in fichas:
        if not isinstance(ficha, Mapping):
            return False
        if not _plano(ficha.get("fonte")).startswith("loja (shopee),"):
            return False
        if esperado_titulo not in _plano(ficha.get("titulo")):
            return False
        if esperado == ANUNCIO_WATCH:
            texto = _plano(
                " ".join(
                    [
                        str(ficha.get("titulo") or ""),
                        str(ficha.get("descricao") or ""),
                        *(str(a) for a in ficha.get("atributos") or []),
                    ]
                )
            )
            if re.search(r"(?:pulseira|band|strap).{0,80}" + _TAMANHO_PULSEIRA, texto):
                return False
    return True


def _retrato_recente(retrato: Mapping, agora: datetime) -> bool:
    try:
        atualizado = datetime.fromisoformat(str(retrato.get("atualizado_em") or ""))
        if atualizado.tzinfo is None:
            return False
    except ValueError:
        return False
    return agora - timedelta(days=1) <= atualizado <= agora + timedelta(minutes=5)


def resposta_aprovada(
    conversa: Any,
    textos: list[str],
    fatos: dict,
    regras: list[Any],
    *,
    agora: datetime | None = None,
) -> RespostaKia | None:
    """Somente uma consulta integralmente coberta pelos fatos aprovados da Kia."""
    if (
        str(_campo(conversa, "integration_id") or "") != INTEGRATION_KIA
        or _campo(conversa, "plataforma") != "shopee"
        or _campo(conversa, "canal") != "chat"
        or any(fatos.get(k) for k in ("chamados_abertos", "devolucoes", "reclamacoes_abertas"))
    ):
        return None
    partes = _partes(textos)
    if not partes:
        return None
    categoria: str | None = None
    texto: str | None = None
    item: str | None = None
    politica: str | None = None
    if (
        all(_CONDICAO.fullmatch(p) for p in partes)
        and not _campo(conversa, "pedido_marketplace")
        and not fatos.get("pedido")
    ):
        from app.services.atendimento import evidencias

        categoria, politica = "originalidade_condicao", "identificar_condicao"
        texto = "Olá! Pode confirmar qual é o modelo ou enviar o card do anúncio por aqui?"
        fichas = fatos.get("ficha_do_anuncio") or []
        resposta_novo = "Olá! O produto é novo, conforme informado no título do anúncio."
        if (
            fichas
            and all(
                isinstance(f, Mapping)
                and _plano(f.get("fonte")).startswith("loja (shopee),")
                and "ha menos de um dia" in _plano(f.get("fonte"))
                for f in fichas
            )
            and not evidencias.motivos_sem_evidencia(resposta_novo, fatos)
        ):
            texto = resposta_novo
    elif all(_PULSEIRA.fullmatch(p) for p in partes) and _item(conversa, ANUNCIO_WATCH, fatos):
        categoria, item, politica = "espec_outros", ANUNCIO_WATCH, "pulseira_padrao"
        texto = "Olá! A pulseira deste modelo tem tamanho padrão."
    elif all(_FOTO.fullmatch(p) for p in partes) and _item(conversa, ANUNCIO_OCULOS, fatos):
        categoria, item, politica = "pedido_link_midia", ANUNCIO_OCULOS, "foto_do_modelo"
        texto = (
            "Olá! As fotos do anúncio mostram o modelo que você receberá. "
            "Você pode conferir os detalhes nas imagens do próprio anúncio."
        )
    elif _item(conversa, ANUNCIO_WATCH, fatos):
        consultas = [_PREAMBULO_COMPRA.sub("", p).strip(" ,:") for p in partes]
        consultas = [p for p in consultas if p]
        if not consultas or any(
            not (_PRAZO.fullmatch(p) or _ORIGEM.fullmatch(p)) for p in consultas
        ):
            return None
        pedido = fatos.get("pedido")
        dados = _campo(conversa, "dados") or {}
        retrato = dados.get("pedido_mkt") or {} if isinstance(dados, Mapping) else {}
        entrega = fatos.get("entrega") or {}
        if (
            not isinstance(pedido, Mapping)
            or not fatos.get("pedido_informado_na_conversa")
            or _plano(pedido.get("situacao_no_sistema")) not in _EM_PREPARACAO
            or retrato.get("fonte") != "shopee"
            or not _campo(conversa, "pedido_marketplace")
            or retrato.get("pedido") != _campo(conversa, "pedido_marketplace")
            or not _retrato_recente(retrato, agora or datetime.now(UTC))
            or not retrato.get("pago_em")
            or retrato.get("status") not in {"PROCESSED", "READY_TO_SHIP"}
            or retrato.get("enviado_em")
            or retrato.get("concluido_em")
            or "{data_envio}" in (fatos.get("lacunas_disponiveis") or [])
            or entrega.get("entregue")
            or _plano(entrega.get("status"))
            not in {"", "pedido em preparacao", "coleta solicitada"}
        ):
            return None
        categoria, item, politica = "prazo_envio", ANUNCIO_WATCH, "prazo_e_origem"
        trechos = []
        if any(_PRAZO.fullmatch(p) for p in consultas):
            trechos.append("Você pode seguir o prazo informado pela Shopee no seu pedido.")
        if any(_ORIGEM.fullmatch(p) for p in consultas):
            trechos.append("O envio deste pedido será feito de São Paulo.")
        texto = "Olá! " + " ".join(trechos)
    if categoria is None or texto is None:
        return None
    regra = _autorizada(categoria, regras)
    if regra is None:
        return None
    return RespostaKia(
        categoria=categoria,
        texto=texto,
        fato={
            "politica": politica,
            "integration_id": INTEGRATION_KIA,
            "anuncio_id": item,
            "regra_id": str(_campo(regra, "id") or ""),
            "fonte": "fato aprovado pela equipe no Manual",
        },
    )
