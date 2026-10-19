"""Consultas simples aprovadas para qualquer loja, com fatos da própria conversa.

O Manual vigente autoriza cada resposta. Editar, desativar ou acrescentar uma
regra conflitante revoga o atalho. Não escolhe modo da loja nem envia mensagem;
as travas de envio, pessoa, canal e pausa continuam no chamador.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.atendimento.validador import plano_de

REGRA_PULSEIRA = {
    "tipo": "categoria",
    "categoria": "espec_outros",
    "plataforma": None,
    "canal": None,
    "prioridade": 50,
    "quando": "Apple ou outro produto.",
    "faca": (
        "Responder com o dado do título ou da ficha própria do anúncio nos FATOS. "
        "Pergunta simples sobre o tamanho da pulseira: informar somente o tamanho "
        "explícito na ficha própria ou o fato aprovado para o mesmo produto, identificado "
        "por vínculo vivo do catálogo da própria loja. A pulseira dos produtos Apple Watch "
        "aprovados pela equipe tem tamanho padrão; isso não escolhe P/M ou M/G nem confirma "
        "ajuste no braço. Sem fato aprovado explicitamente nos FATOS, não presumir que "
        "todo Apple Watch tenha essa pulseira. Sem identificar o modelo ou a variação, pedir "
        "que o cliente "
        "confirme o modelo ou envie o card do anúncio, sem encaminhar só por faltar esse "
        "dado. Divergência, medida ausente, compatibilidade ou outra especificação sem "
        "prova: precisa_humano=true. Apple: condição, garantia e versão só com o título. "
        "Vale para qualquer loja ativada, usando os dados da própria conversa."
    ),
}
REGRA_FOTO = {
    "tipo": "categoria",
    "categoria": "pedido_link_midia",
    "plataforma": None,
    "canal": None,
    "prioridade": 50,
    "quando": "Pedido de link, foto ou vídeo.",
    "faca": (
        "Pedido genérico para ver como é o modelo: orientar consultar as fotos do próprio "
        "anúncio e perguntar qual detalhe deseja ver; precisa_humano=false. Sem identificar "
        "o anúncio, pedir modelo ou card, respeitando os recursos do canal, sem encaminhar "
        "só por faltar esse dado. Não prometer enviar foto nova nem afirmar que as fotos "
        "são reais, originais ou comprovam a condição. Sem link: dizer que o anúncio aparece "
        "buscando o nome dele na nossa loja e oferecer explicar o detalhe que o cliente quer "
        "ver. Foto adicional, ângulo ou detalhe ausente, vídeo, número de série ou divergência "
        "entre produto e anúncio: precisa_humano=true quando precisar obter material novo. "
        "Vale para qualquer loja ativada, usando o anúncio da própria conversa."
    ),
}
REGRA_CONDICAO = {
    "tipo": "categoria",
    "categoria": "originalidade_condicao",
    "plataforma": None,
    "canal": None,
    "prioridade": 50,
    "quando": "Originalidade, condição, homologação ou confiança.",
    "faca": (
        "Responder só com o que está no título próprio e atualizado do anúncio (original, "
        "novo, lacrado, marca) e dizer que a compra é protegida pela plataforma. Pergunta "
        "simples antes da compra se o produto é novo ou recondicionado: confirmar a condição "
        "somente quando explícita no título próprio; sem confirmação, pedir que confirme o "
        "modelo ou envie o card do anúncio, respeitando os recursos do canal, sem afirmar "
        "a condição e sem encaminhar só por faltar esse dado. Não usar outro anúncio nem "
        "o texto do comprador como prova. Produto recebido, reclamação, caixa com outra "
        "marca, importadora, fabricação, homologação ou conferência antes do envio: "
        "precisa_humano=true até o texto ser aprovado. Vale para qualquer loja ativada."
    ),
}
REGRA_ENVIO = {
    "tipo": "categoria",
    "categoria": "prazo_envio",
    "plataforma": None,
    "canal": None,
    "prioridade": 50,
    "quando": "Pedido pago sem postagem.",
    "faca": (
        "Com pedido pago nos FATOS e ainda sem postagem, decida pelo que o cliente solicita. "
        "CONSULTA COMUM, sem justificativa de urgência ou pedido explícito de prioridade: "
        "'Tranquilo, vocês conseguem enviar hoje?', 'Vocês enviam amanhã?', 'envia amanhã?' "
        "e 'quando será postado?' -> precisa_humano=false; motivo 'consulta de postagem, "
        "sem urgência explícita'. Esses exemplos perguntam sobre o envio normal: hoje/amanhã, "
        "por si só, não são urgência nem antecipação. Não é necessário saber ou confirmar a "
        "data para responder apenas o STATUS conhecido. Para TODOS esses exemplos, se os "
        "FATOS indicam preparação, a resposta aprovada é: 'Olá! O pedido {numero_pedido} está "
        "em preparação e ainda não foi postado. Quando for enviado, o código de rastreio e a "
        "previsão de entrega aparecerão no próprio pedido.' A resposta não depende de ação "
        "humana. Não acrescentar promessa de acompanhar, avisar depois, verificar ou priorizar. "
        "Consulta simples de previsão de entrega ou envio com o pedido já identificado: "
        "orientar seguir o prazo informado pela própria plataforma no pedido, sem inventar "
        "data e sem pedir novamente o número já ligado. Origem do envio só pode ser "
        "afirmada quando comprovada para essa loja e esse pedido ou anúncio; não copiar a "
        "origem de outra loja ou de produto equivalente. Sem origem comprovada, informar "
        "que ela não consta nos dados disponíveis do pedido; só essa ausência não exige "
        "pessoa. Essas consultas simples: precisa_humano=false. Não confundir rastreio ou "
        "etiqueta com postagem. PESSOA (precisa_humano=true): urgência EXPLÍCITA ('é urgente', "
        "'preciso receber até amanhã', 'preciso que envie hoje'), pedido EXPLÍCITO de "
        "prioridade/antecipação ('passe meu pedido na frente', 'antecipe o envio'), prazo de "
        "postagem já vencido, região não atendida, ou os FATOS não mostram pedido pago. "
        "Pergunta junto de necessidade de receber até certa data continua sendo urgência, "
        "motivo 'pedido urgente'. Nunca confirmar nem negar postagem em dia específico sem "
        "dado, nem prometer prioridade, prazo ou embalagem especial. Vale para qualquer "
        "loja ativada e respeita os recursos da plataforma e do canal."
    ),
}
REGRAS = (REGRA_PULSEIRA, REGRA_FOTO, REGRA_ENVIO, REGRA_CONDICAO)

# Aprovação da equipe para o produto, não para lojas que tenham título parecido.
# Vínculos mortos e variantes desconhecidas não fazem parte desta aprovação.
IDS_PRODUTOS_PULSEIRA_PADRAO = frozenset(
    {
        "df0823b8-0674-4f50-896c-179364acb823",
        "ef352399-7446-4e6b-a82b-3a2ff106a05f",
        "377c85df-57d7-43f8-b832-4e7884bf9c3e",
        "cbdf0902-c63e-4abb-a8dd-8b3ae98fec58",
        "5158a6f8-8d5e-40a2-bc4a-f1240e6a5ddd",
    }
)
# As duas informações já aprovadas para esse anúncio continuam disponíveis.
# Origem de despacho é da loja/anúncio, e não se transfere pelo catálogo.
_APROVACAO_WATCH = ("shopee", "2ac2633b-c698-4718-99cb-6e5443a80689", "22299473628")
_PLATAFORMAS = {"shopee": "Shopee", "ml": "Mercado Livre", "tiktok": "TikTok Shop"}
_PRAZO_PLATAFORMA = {
    "shopee": "pela Shopee",
    "ml": "pelo Mercado Livre",
    "tiktok": "pelo TikTok Shop",
}
_CANAIS = {"shopee": {"chat"}, "ml": {"pergunta", "pos_venda"}, "tiktok": {"chat"}}
_ORIGENS_FICHA = {
    "cartao de produto que o cliente mandou",
    "anuncio ligado a conversa",
    "anuncio da pergunta",
    "item do pedido da conversa",
}


@dataclass(frozen=True)
class RespostaAprovada:
    categoria: str
    texto: str
    fato: dict[str, Any]


def _campo(obj: Any, chave: str, padrao: Any = None) -> Any:
    return obj.get(chave, padrao) if isinstance(obj, Mapping) else getattr(obj, chave, padrao)


def _plano(texto: Any) -> str:
    return " ".join(plano_de(str(texto or "")).split())


def regra_aprovada(regra: Any) -> bool:
    return any(all(_campo(regra, k) == a[k] for k in a) for a in REGRAS)


def _autorizada(categoria: str, regras: list[Any]) -> Any | None:
    aplicaveis = [
        r
        for r in regras
        if _campo(r, "ativa", True) is not False
        and _campo(r, "tipo") == "categoria"
        and _campo(r, "categoria") == categoria
    ]
    return aplicaveis[0] if len(aplicaveis) == 1 and regra_aprovada(aplicaveis[0]) else None


def _partes(textos: list[str]) -> list[str]:
    if not textos or any(not isinstance(t, str) for t in textos):
        return []
    partes = []
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
_TAMANHOS = re.compile(r"\b(?:p\s*/\s*m|m\s*/\s*g|s\s*/\s*m|m\s*/\s*l)\b")


def _ficha_propria(conversa: Any, fatos: dict) -> Mapping | None:
    fichas = fatos.get("ficha_do_anuncio")
    if not isinstance(fichas, list) or len(fichas) != 1 or not isinstance(fichas[0], Mapping):
        return None
    ficha = fichas[0]
    plataforma = _PLATAFORMAS[_campo(conversa, "plataforma")]
    fontes = {_plano(f"loja ({plataforma}), {idade}") for idade in ("agora", "há menos de um dia")}
    titulo = _plano(ficha.get("titulo"))
    if (
        _plano(ficha.get("fonte")) not in fontes
        or _plano(ficha.get("de_onde")) not in _ORIGENS_FICHA
        or not titulo
        or (fatos.get("anuncio") and _plano(fatos["anuncio"]) != titulo)
    ):
        return None
    return ficha


def _anuncio_identificado(conversa: Any, fatos: dict) -> bool:
    # Orientar a galeria não depende de inferir a condição ou o conteúdo de
    # uma imagem. Sem ficha, o vínculo próprio já identifica qual anúncio abrir.
    if fatos.get("ficha_do_anuncio"):
        return _ficha_propria(conversa, fatos) is not None
    return bool(str(_campo(conversa, "anuncio_id") or "").strip())


def _tamanhos_da_variacao(conversa: Any) -> set[str]:
    dados = _campo(conversa, "dados") or {}
    retrato = dados.get("pedido_mkt") or {} if isinstance(dados, Mapping) else {}
    if not isinstance(retrato, Mapping):
        return set()
    return {
        _plano(t.group()).replace(" ", "")
        for item in retrato.get("itens") or []
        if isinstance(item, Mapping)
        for t in _TAMANHOS.finditer(_plano(item.get("variacao")))
    }


def _item_aprovado(conversa: Any, fatos: dict) -> bool:
    if (
        _campo(conversa, "plataforma"),
        str(_campo(conversa, "integration_id") or ""),
    ) != _APROVACAO_WATCH[:2]:
        return False
    esperado = _APROVACAO_WATCH[2]
    dados = _campo(conversa, "dados") or {}
    retrato = dados.get("pedido_mkt") or {} if isinstance(dados, Mapping) else {}
    if not isinstance(retrato, Mapping):
        return False
    itens = retrato.get("itens") or []
    ids = {str(i.get("item_id") or "") for i in itens if isinstance(i, Mapping)}
    anuncio = str(_campo(conversa, "anuncio_id") or "")
    if (anuncio and anuncio != esperado) or (ids and ids != {esperado}):
        return False
    if anuncio != esperado and ids != {esperado}:
        return False
    if any(_TAMANHOS.search(_plano(i.get("variacao"))) for i in itens if isinstance(i, Mapping)):
        return False
    if not fatos.get("ficha_do_anuncio"):
        return True
    ficha = _ficha_propria(conversa, fatos)
    return bool(
        ficha is not None
        and "apple watch" in _plano(ficha.get("titulo"))
        and _sem_conflito_pulseira(ficha)
    )


def _tamanho_pulseira(ficha: Mapping) -> str | None:
    valores = []
    for atributo in ficha.get("atributos") or []:
        if isinstance(atributo, str) and ":" in atributo:
            chave, valor = atributo.split(":", 1)
            if _plano(chave) in {
                "tamanho da pulseira",
                "tamanho pulseira",
                "band size",
                "strap size",
            }:
                valores.append(valor.strip())
    if len({_plano(v) for v in valores}) != 1:
        return None
    valor = valores[0]
    if _plano(valor) != "padrao" and not _TAMANHOS.fullmatch(_plano(valor)):
        return None
    texto = _plano(f"{ficha.get('titulo') or ''} {ficha.get('descricao') or ''}")
    # Sem escolher entre variações ou resolver uma divergência da ficha.
    if re.search(r"\b(?:nao|sem|varia|variacoes)\b.{0,60}\bpulseira\b", texto):
        return None
    encontrados = {_plano(m.group()) for m in _TAMANHOS.finditer(texto)}
    if any(t != _plano(valor) for t in encontrados):
        return None
    return valor


def _sem_conflito_pulseira(ficha: Mapping) -> bool:
    texto = _plano(
        " ".join(
            [
                str(ficha.get("titulo") or ""),
                str(ficha.get("descricao") or ""),
                *(str(a) for a in ficha.get("atributos") or []),
            ]
        )
    )
    return not (
        _TAMANHOS.search(texto)
        or re.search(
            r"\b(?:nao|sem)\b.{0,50}\b(?:pulseira|padrao)\b"
            r"|\bpulseira\b.{0,50}\b(?:nao|sem|varia|variacoes)\b",
            texto,
        )
    )


def _catalogo_aprovado(ficha: Mapping) -> bool:
    vinculos = ficha.get("produtos_vinculados")
    if not isinstance(vinculos, list) or not vinculos:
        return False
    if any(not isinstance(v, Mapping) or not v.get("product_id") for v in vinculos):
        return False
    ids = {str(v["product_id"]) for v in vinculos}
    return ids.issubset(IDS_PRODUTOS_PULSEIRA_PADRAO) and _sem_conflito_pulseira(ficha)


def _identificar(conversa: Any) -> str:
    if _campo(conversa, "plataforma") == "ml" and _campo(conversa, "canal") == "pergunta":
        return "Olá! Pode confirmar qual é o modelo ou o nome do anúncio que está consultando?"
    return "Olá! Pode confirmar qual é o modelo ou enviar o card do anúncio por aqui?"


def _pedido_em_preparacao(conversa: Any, fatos: dict, agora: datetime) -> bool:
    pedido = fatos.get("pedido")
    dados = _campo(conversa, "dados") or {}
    retrato = dados.get("pedido_mkt") or {} if isinstance(dados, Mapping) else {}
    numero = str(_campo(conversa, "pedido_marketplace") or "")
    entrega = fatos.get("entrega") or {}
    if (
        not isinstance(pedido, Mapping)
        or not fatos.get("pedido_informado_na_conversa")
        or not numero
    ):
        return False
    if _plano(pedido.get("situacao_no_sistema")) not in {
        "6",
        "21",
        "em aberto",
        "em digitacao",
        "em preparacao",
        "pedido em preparacao",
    }:
        return False
    if not isinstance(retrato, Mapping) or not isinstance(entrega, Mapping):
        return False
    try:
        atualizado = datetime.fromisoformat(str(retrato.get("atualizado_em") or ""))
        recente = atualizado.tzinfo is not None and agora - timedelta(
            days=1
        ) <= atualizado <= agora + timedelta(minutes=5)
    except ValueError:
        return False
    plataforma = _campo(conversa, "plataforma")
    chaves = {numero}
    if plataforma == "ml" and isinstance(dados, Mapping):
        chaves.update(str(dados.get(k)) for k in ("order_id", "pack_id") if dados.get(k))
    status = _plano(retrato.get("status"))
    logistica = retrato.get("logistica") or {}
    if not isinstance(logistica, Mapping):
        return False
    if plataforma == "ml":
        status_preparacao = status == "paid" and _plano(logistica.get("status")) in {
            "pending",
            "handling",
            "ready to ship",
            "ready_to_ship",
        }
    else:
        status_preparacao = status in {
            "processed",
            "ready_to_ship",
            "ready to ship",
            "awaiting shipment",
            "awaiting_shipment",
        }
    return bool(
        recente
        and retrato.get("fonte") == plataforma
        and str(retrato.get("pedido") or "") in chaves
        and retrato.get("pago_em")
        and status_preparacao
        and not retrato.get("enviado_em")
        and not retrato.get("concluido_em")
        and "{data_envio}" not in (fatos.get("lacunas_disponiveis") or [])
        and not entrega.get("entregue")
        and _plano(entrega.get("status")) in {"", "pedido em preparacao", "coleta solicitada"}
    )


def resposta_aprovada(
    conversa: Any,
    textos: list[str],
    fatos: dict,
    regras: list[Any],
    *,
    agora: datetime | None = None,
) -> RespostaAprovada | None:
    """Responde só se todo o turno estiver coberto e a regra atual autorizar."""
    plataforma = _campo(conversa, "plataforma")
    if _campo(conversa, "canal") not in _CANAIS.get(plataforma, set()) or any(
        fatos.get(k) for k in ("chamados_abertos", "devolucoes", "reclamacoes_abertas")
    ):
        return None
    partes = _partes(textos)
    if not partes:
        return None
    categoria = politica = texto = None
    ficha = _ficha_propria(conversa, fatos)
    if (
        all(_CONDICAO.fullmatch(p) for p in partes)
        and not _campo(conversa, "pedido_marketplace")
        and not fatos.get("pedido")
    ):
        categoria, politica, texto = (
            "originalidade_condicao",
            "identificar_condicao",
            _identificar(conversa),
        )
        if ficha is not None:
            from app.services.atendimento import evidencias

            confirmado = "Olá! O produto é novo, conforme informado no título do anúncio."
            titulo = _plano(ficha.get("titulo"))
            if not re.search(
                r"\b(?:recondicionad[oa]|usad[oa]|seminov[oa])\b", titulo
            ) and not evidencias.motivos_sem_evidencia(confirmado, {"ficha_do_anuncio": [ficha]}):
                texto, politica = confirmado, "condicao_no_titulo"
            elif re.search(r"\brecondicionad[oa]\b", titulo) and not re.search(
                r"\b(?:nao|sem|nov[oa]|usad[oa]|seminov[oa])\b", titulo
            ):
                texto = "Olá! O produto é recondicionado, conforme informado no título do anúncio."
                politica = "condicao_no_titulo"
    elif all(_PULSEIRA.fullmatch(p) for p in partes):
        categoria, politica, texto = "espec_outros", "identificar_pulseira", _identificar(conversa)
        tamanho = _tamanho_pulseira(ficha) if ficha is not None else None
        selecionados = _tamanhos_da_variacao(conversa)
        if selecionados and (tamanho is None or selecionados != {_plano(tamanho).replace(" ", "")}):
            return None
        if tamanho is not None:
            texto = f"Olá! Conforme a ficha deste anúncio, o tamanho da pulseira é {tamanho}."
            politica = "pulseira_na_ficha"
        elif (ficha is not None and _catalogo_aprovado(ficha)) or _item_aprovado(conversa, fatos):
            texto, politica = (
                "Olá! A pulseira deste modelo tem tamanho padrão.",
                "pulseira_padrao_aprovada",
            )
    elif all(_FOTO.fullmatch(p) for p in partes):
        categoria, politica, texto = (
            "pedido_link_midia",
            "identificar_foto_modelo",
            _identificar(conversa),
        )
        if _anuncio_identificado(conversa, fatos):
            texto = (
                "Olá! Você pode conferir como é o modelo nas fotos do próprio anúncio. "
                "Se quiser, diga qual detalhe gostaria de ver."
            )
            politica = "foto_do_modelo"
    else:
        consultas = [_PREAMBULO_COMPRA.sub("", p).strip(" ,:") for p in partes]
        consultas = [p for p in consultas if p]
        if not consultas or any(
            not (_PRAZO.fullmatch(p) or _ORIGEM.fullmatch(p)) for p in consultas
        ):
            return None
        if not _pedido_em_preparacao(conversa, fatos, agora or datetime.now(UTC)):
            return None
        categoria, politica = "prazo_envio", "prazo_e_origem"
        trechos = []
        if any(_PRAZO.fullmatch(p) for p in consultas):
            trechos.append(
                f"Você pode seguir o prazo informado {_PRAZO_PLATAFORMA[plataforma]} no seu pedido."
            )
        if any(_ORIGEM.fullmatch(p) for p in consultas):
            trechos.append(
                "O envio deste pedido será feito de São Paulo."
                if _item_aprovado(conversa, fatos)
                else "A origem do envio não está informada nos dados disponíveis deste pedido."
            )
        texto = "Olá! " + " ".join(trechos)
    regra = _autorizada(categoria, regras)
    if regra is None:
        return None
    return RespostaAprovada(
        categoria,
        texto,
        {
            "politica": politica,
            "integration_id": str(_campo(conversa, "integration_id") or ""),
            "anuncio_id": str(_campo(conversa, "anuncio_id") or ""),
            "regra_id": str(_campo(regra, "id") or ""),
            **(
                {
                    "produtos_aprovados": sorted(
                        str(v["product_id"]) for v in ficha.get("produtos_vinculados") or []
                    )
                }
                if politica == "pulseira_padrao_aprovada"
                and ficha is not None
                and _catalogo_aprovado(ficha)
                else {}
            ),
            "fonte": "política geral aprovada no Manual; fatos da própria conversa",
        },
    )
