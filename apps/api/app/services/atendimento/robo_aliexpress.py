"""Leitor do chat do AliExpress (AliExpress Chat do vendedor) a partir do que o robô copiou.

O AliExpress não tem API de chat (a FAQ 792 diz que o IM não é lido por
interface). A aba `gsp.aliexpress.com/m_apps/im-chat/im#/window` (lista de
sessões, NENHUMA aberta) recebe tudo sozinha, e o robô só copia:

  • WebSocket ACCS (wss://msgacs.m.aliexpress.com/accs/auth?token=...): quadro
    JSON `{"type":"DATA","serviceId":"ae-lz-sync"|"ae-csp-im-sync",
    "compressType":"COMMON"|"GZIP","data":"<base64>"}`. O `data` (base64,
    gzip quando GZIP) é `{"syncBody":{sessionId, messageId, sellerUserId,
    typeAndIdMap}, "bizDataV2":"<JSON do item>"}`; sem `bizDataV2` a página
    puxa pelo sync (e o robô copia a resposta). Batimento (`HEARTBEAT_ACCS_H5`),
    ACK e `h5_ping` são controle.
  • mtop que a página faz (envelope `{"api","data","ret":["SUCCESS::..."]}`),
    reconhecida pelo nome da api no caminho (/h5/<api>/1.0/) ou no corpo:
      mtop.gsp.web.seller.js.sync — data.syncDataValuesMap.im[] =
          {syncBody, bizData:"<JSON do item>"}
      mtop.gsp.im.use.web.seller.messagebox.querysessionlist — as sessões
          (…sessionViewDTOList[]: sessionId, target.targetId = comprador,
          sessionData{title, headUrl, content, nonReadNumber, senderAccountType,
          tags})
      mtop.gsp.im.web.seller.messagebox.direction.querybysessionid —
          data.result[] mensagens (só quando alguém abre a sessão na aba)
      mtop.gsp.im.use.web.seller.unreadcount — data.result (não lidas da loja)
    A resposta do getToken (token da conexão ACCS) é controle e NUNCA é lida.

Item (sync, bizDataV2, histórico): {header:{type}, body:{typeData, layoutData,
templateData}}. header.type 1 = mensagem; 11/12 = atualização de sessão;
3 = status; 4 = recibo de leitura. typeData: mid, sid, from, fromType
(11 = comprador, 12 = vendedor, 13 = conta agregada da loja), sendTime (ms),
status (1 = recolhida), ext.fromCode (`im_ai`/`im_open_api` = robô da
plataforma). layoutData.card: 1 texto, 3 imagem, 10003/10006 produto,
10004/10007 pedido. templateData é TEXTO com JSON: txt, imgUrl, orderId...

A lista de sessões só traz a PRÉVIA da última mensagem: ela vira mensagem
com id `previa:<sessão>:<lastMessageId>` (a lista real traz o id; sem ele, o
hash do texto com a hora), que o `robo` troca pelo id de verdade quando a
mensagem chega (sync/empurrão). É o que põe na fila a pergunta que chegou
com o robô fora (antes de ligar, sessão caída).

A IA do AliExpress (e o plugin de atendimento) responde com a CONTA DO
VENDEDOR; na lista, sem o `fromCode`, só as tags da sessão (`ai-serving`,
`un-replied`...) dizem que não foi a equipe: essa prévia entra como
`sistema`, e com `un-replied` entra também o marcador da pergunta que o robô
não viu (`robo_leitura.marcador_nao_vista`).

Python puro sobre cópias: nada aqui chama o AliExpress. Texto de comprador
nunca vai para o log (só contagens e nomes de campo).
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from app.services.atendimento import enriquecer
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    rotulo_tipo_plataforma,
)
from app.services.atendimento.robo_leitura import (
    CONTROLE,
    IGNORADO,
    TIPO_IMAGEM,
    TIPO_OUTRO,
    TIPO_PEDIDO,
    TIPO_PRODUTO,
    TIPO_TEXTO,
    TIPO_VIDEO,
    USADO,
    Evento,
    Leitura,
    MensagemLida,
    anexo,
    bytes_do_corpo,
    caminho,
    caminho_para_log,
    campos_desconhecidos,
    como_json,
    descomprimir,
    dicionario,
    id_de_previa,
    inteiro,
    json_ou_valor,
    listas_na_chave,
    marcador_nao_vista,
    quando,
    texto,
)

PLATAFORMA = "aliexpress"

SERVICOS_ACCS = frozenset({"ae-lz-sync", "ae-csp-im-sync"})

API_SYNC = "mtop.gsp.web.seller.js.sync"
API_SESSOES = "mtop.gsp.im.use.web.seller.messagebox.querysessionlist"
API_HISTORICO = "mtop.gsp.im.web.seller.messagebox.direction.querybysessionid"
API_NAO_LIDAS = "mtop.gsp.im.use.web.seller.unreadcount"
# Chamadas da página que não têm conversa. O getToken traz o token da
# conexão ACCS: é descartado sem ler o corpo.
_APIS_CONTROLE = frozenset(
    {
        "mtop.gsp.im.use.web.seller.gettoken",
        "mtop.gsp.im.web.seller.userinfo.get",
        "mtop.gsp.im.use.web.seller.getshoplist",
        "mtop.gsp.im.use.web.seller.getuserinfo",
        "mtop.common.gettimestamp",
        "mtop.csp.im.web.seller.session.info.get",
        "mtop.gsp.im.biz.seller.buyerprofile.get",
        "mtop.gsp.im.web.card.order.list.get",
    }
)

# Códigos da mtop que a própria página resolve (renova o token `_m_h5_tk` e
# repete a chamada). O robô também não os trata como sessão caída.
_ERROS_DE_ROTINA = frozenset(
    {"FAIL_SYS_TOKEN_EXOIRED", "FAIL_SYS_TOKEN_EXPIRED", "FAIL_SYS_TOKEN_EMPTY"}
)

HEADER_MENSAGEM = 1
HEADERS_SESSAO = (11, 12)
HEADERS_STATUS = (3, 4)

FROM_COMPRADOR = 11
FROMS_LOJA = (12, 13)
# Resposta automática do robô da plataforma (ou de plugin de terceiro).
FROM_CODES_ROBO = frozenset({"im_ai", "im_open_api"})

CARDS_TEXTO = frozenset({"1"})
CARDS_IMAGEM = frozenset({"3"})
CARDS_VIDEO = frozenset({"5", "7"})
CARDS_PRODUTO = frozenset({"10003", "10006"})
CARDS_PEDIDO = frozenset({"10004", "10007"})

_CONHECIDOS_TYPE_DATA = frozenset(
    {
        "mid",
        "messageId",
        "sid",
        "sessionId",
        "from",
        "fromType",
        "to",
        "toType",
        "sendTime",
        "bizUnique",
        "status",
        "ext",
        "cursor",
        "readStatus",
        "namespace",
        "templateId",
        "summary",
        "extendType",
        "extendContent",
    }
)
_CONHECIDOS_SESSAO = frozenset(
    {
        "title",
        "headUrl",
        "content",
        "nonReadNumber",
        "senderId",
        "senderAccountType",
        "country",
        "tags",
        "openTags",
        "shuntedUserId",
        "shuntedUserEmail",
        "toPosition",
        "selfPosition",
        "silenced",
        "top",
        "lastMessageTime",
        "sendTime",
        "summary",
        # Vistos na lista real da Vita (30/09): versão/hora de atualização da
        # sessão, o id da loja, o link da conversa no app do comprador e o id
        # da última mensagem (guardado na prévia para a adoção por id).
        "version",
        "viewModifyTime",
        "sellerId",
        "buyerAppUrl",
        "lastMessageId",
    }
)
_API_NO_CAMINHO = re.compile(r"/h5/(?P<api>mtop\.[a-z0-9._]+)/", re.IGNORECASE)


# ── Mensagem ──────────────────────────────────────────────────────────────


def _autor(type_data: dict, leitura: Leitura, vendedor: str | None) -> tuple[str, bool]:
    ext = dicionario(type_data.get("ext"))
    codigo = texto(ext.get("fromCode"))
    if codigo in FROM_CODES_ROBO:
        return AUTOR_SISTEMA, True
    tipo = inteiro(type_data.get("fromType"))
    de = texto(type_data.get("from"))
    if tipo == FROM_COMPRADOR:
        return AUTOR_CLIENTE, False
    if tipo in FROMS_LOJA or (vendedor and de == vendedor):
        return AUTOR_LOJA, False
    leitura.desconhecido(f"mensagem.fromType={tipo}")
    # Na dúvida, SISTEMA: nem tira a conversa da fila, nem inventa pergunta.
    return AUTOR_SISTEMA, False


def _conteudo(body: dict, leitura: Leitura) -> tuple[str, str | None, list, str | None]:
    """(tipo da caixa, texto, anexos, pedido) pelo `layoutData.card`."""
    card = texto(dicionario(body.get("layoutData")).get("card")) or ""
    modelo = json_ou_valor(body.get("templateData"))
    if not isinstance(modelo, dict):
        # Texto simples fora de JSON: é o próprio conteúdo.
        return TIPO_TEXTO, texto(modelo), [], None
    txt = texto(modelo.get("txt") or modelo.get("text") or modelo.get("content"))
    if card in CARDS_TEXTO:
        return TIPO_TEXTO, txt, [], None
    if card in CARDS_IMAGEM:
        imagem = anexo(TIPO_IMAGEM, modelo.get("imgUrl") or modelo.get("url"))
        return TIPO_IMAGEM, None, imagem, None
    if card in CARDS_VIDEO:
        video = anexo(TIPO_VIDEO, modelo.get("videoUrl") or modelo.get("url"))
        return TIPO_VIDEO, None, video, None
    if card in CARDS_PEDIDO:
        pedido = texto(modelo.get("orderId"))
        if pedido:
            return TIPO_PEDIDO, None, [enriquecer.cartao_pedido_vazio(pedido)], pedido
    if card in CARDS_PRODUTO:
        item = texto(modelo.get("productId") or modelo.get("itemId") or modelo.get("id"))
        if item:
            return TIPO_PRODUTO, None, [enriquecer.cartao_produto_vazio(item)], None
    # Cartão que não conhecemos: aproveita o que der (texto, imagem).
    leitura.desconhecido(f"mensagem.card={card or '?'}")
    if txt:
        return TIPO_OUTRO, txt, [], None
    imagem = anexo(TIPO_IMAGEM, modelo.get("imgUrl"))
    if imagem:
        return TIPO_IMAGEM, None, imagem, None
    return TIPO_OUTRO, rotulo_tipo_plataforma(None), [], None


def ler_mensagem(
    body: Any,
    leitura: Leitura,
    *,
    sessao: str | None = None,
    vendedor: str | None = None,
    item: Any = None,
) -> bool:
    """O `body` de um item de mensagem → a conversa dele. False = nada gravável."""
    body = dicionario(body)
    type_data = dicionario(body.get("typeData"))
    mid = texto(type_data.get("mid") or type_data.get("messageId"))
    sid = texto(type_data.get("sid") or type_data.get("sessionId")) or sessao
    if not mid or not sid:
        leitura.desconhecido("mensagem.sem_id_ou_sessao")
        return False
    campos_desconhecidos(leitura, type_data, _CONHECIDOS_TYPE_DATA, "typeData")
    autor, robo = _autor(type_data, leitura, vendedor)
    tipo, conteudo, anexos, pedido = _conteudo(body, leitura)
    if inteiro(type_data.get("status")) == 1:
        # Recolhida pelo autor: não mostramos o que ele retirou.
        tipo, conteudo, anexos = TIPO_OUTRO, "[Mensagem recolhida]", []
    conversa = leitura.conversa(sid)
    if autor == AUTOR_CLIENTE and conversa.comprador_id is None:
        conversa.comprador_id = texto(type_data.get("from"))
    if pedido:
        conversa.juntar(pedido=pedido)
    conversa.mensagem(
        MensagemLida(
            externo_id=mid,
            autor=autor,
            tipo=tipo,
            texto=conteudo,
            enviada_em=quando(type_data.get("sendTime")),
            anexos=anexos,
            payload={"fonte": "robo", "plataforma": PLATAFORMA, "item": item or body},
            robo_plataforma=robo,
        )
    )
    leitura.reconhecidos["mensagem"] += 1
    return True


# ── Sessão (lista de conversas) ───────────────────────────────────────────


# Tags da sessão que dizem que a última mensagem da CONTA DO VENDEDOR não foi
# a equipe: a IA do AliExpress atendendo (ou sem resolver), o robô de
# atendimento, ou a própria plataforma dizendo que o comprador espera
# resposta. Sem isto a prévia viraria "a equipe respondeu": sairia da fila e
# aposentaria a sugestão da IA do DaVinci (revisão 30/09).
TAG_SEM_RESPOSTA = "un-replied"
TAGS_NAO_E_A_EQUIPE = frozenset(
    {TAG_SEM_RESPOSTA, "ai-serving", "ai-unresolved", "bot-agent-service"}
)


def _autor_da_previa(tipo: int | None, tags: list[str], leitura: Leitura) -> tuple[str, bool]:
    """(autor, é robô/IA da plataforma?) da prévia da lista."""
    if tipo == FROM_COMPRADOR:
        return AUTOR_CLIENTE, False
    if tipo in FROMS_LOJA:
        if TAGS_NAO_E_A_EQUIPE.intersection(tags):
            return AUTOR_SISTEMA, True
        return AUTOR_LOJA, False
    leitura.desconhecido(f"sessao.senderAccountType={tipo}")
    return AUTOR_SISTEMA, False


def ler_sessao(dto: Any, leitura: Leitura) -> bool:
    """Um item da lista de sessões (ou a atualização de sessão do sync) → conversa."""
    dto = dicionario(dto)
    sid = texto(dto.get("sessionId") or dto.get("sessionViewId") or dto.get("sid"))
    if not sid:
        leitura.desconhecido("sessao.sem_sessionId")
        return False
    alvo = dicionario(dto.get("target"))
    dados = dicionario(dto.get("sessionData"))
    campos_desconhecidos(leitura, dados, _CONHECIDOS_SESSAO, "sessionData")
    c = leitura.conversa(sid)
    c.juntar(
        comprador_id=texto(alvo.get("targetId")),
        comprador_nome=texto(dados.get("title")),
        comprador_avatar=texto(dados.get("headUrl")),
        nao_lidas=inteiro(dados.get("nonReadNumber")),
    )
    tags: list[str] = []
    if isinstance(dados.get("tags"), list):
        # "un-replied" etc.: vocabulário da plataforma, sem dado pessoal.
        tags = [str(t)[:32] for t in dados["tags"] if isinstance(t, str | int)][:10]
        c.dados["tags"] = tags
    conteudo = texto(dados.get("content"))
    ultimo_id = texto(dados.get("lastMessageId"))
    if conteudo:
        # A hora da MENSAGEM (lastMessageTime/sendTime) ou, sem ela, a hora
        # em que a lista foi servida (serverTime: só um teto — a mensagem é
        # daquela hora ou de antes). O `robo` decide "já tenho" de um jeito
        # diferente para cada uma.
        hora_mensagem = quando(dados.get("lastMessageTime")) or quando(dados.get("sendTime"))
        hora = hora_mensagem or quando(dto.get("serverTime"))
        autor, robo = _autor_da_previa(inteiro(dados.get("senderAccountType")), tags, leitura)
        c.mensagem(
            MensagemLida(
                externo_id=id_de_previa(sid, conteudo, mensagem_id=ultimo_id, hora=hora_mensagem),
                autor=autor,
                tipo=TIPO_TEXTO,
                texto=conteudo,
                enviada_em=hora,
                payload={
                    "fonte": "robo",
                    "plataforma": PLATAFORMA,
                    "previa": True,
                    "hora": "mensagem" if hora_mensagem is not None else "servidor",
                    # A lista real traz o id da última mensagem: a mensagem de
                    # verdade (sync/empurrão) com esse id adota a prévia mesmo
                    # que o texto da prévia venha resumido ("[Image]"...).
                    **({"mensagem_id": ultimo_id} if ultimo_id else {}),
                },
                robo_plataforma=robo,
                previa=True,
            )
        )
        if autor == AUTOR_SISTEMA and TAG_SEM_RESPOSTA in tags:
            # A última é da IA/robô e a plataforma diz que o comprador espera:
            # a pergunta dele existe, o robô é que não a viu.
            c.mensagem(
                marcador_nao_vista(
                    sid,
                    hora=hora - timedelta(seconds=1) if hora else None,
                    # Estável na lista relida (o serverTime muda a cada vez).
                    referencia=ultimo_id
                    or f"{conteudo}|{hora_mensagem.isoformat() if hora_mensagem else ''}",
                    plataforma=PLATAFORMA,
                )
            )
    leitura.reconhecidos["sessao"] += 1
    return True


# ── Item (sync, empurrão ACCS, histórico) ─────────────────────────────────


def ler_item(item: Any, leitura: Leitura, *, corpo_sync: Any = None) -> bool:
    item = dicionario(item)
    if not item:
        return False
    corpo_sync = dicionario(corpo_sync)
    vendedor = texto(corpo_sync.get("sellerUserId"))
    sessao = texto(corpo_sync.get("sessionId"))
    header = dicionario(item.get("header"))
    body = dicionario(item.get("body")) or item
    tipo = inteiro(header.get("type"))
    if tipo in HEADERS_SESSAO:
        type_data = dicionario(body.get("typeData"))
        interno = dicionario(type_data.get("sessionData"))
        if "sessionId" in interno and "sessionData" in interno:
            # O item de sessão inteiro embrulhado (como na lista de sessões).
            return ler_sessao(interno, leitura)
        sid = texto(type_data.get("sessionId") or type_data.get("sid")) or sessao
        return ler_sessao({**type_data, "sessionId": sid}, leitura)
    if tipo in HEADERS_STATUS:
        leitura.reconhecidos["status_ou_leitura"] += 1
        return False
    if tipo in (HEADER_MENSAGEM, None):
        return ler_mensagem(body, leitura, sessao=sessao, vendedor=vendedor, item=item)
    leitura.desconhecido(f"item.header.type={tipo}")
    return False


# ── WebSocket ACCS ────────────────────────────────────────────────────────


def _dados_accs(quadro: dict) -> Any:
    bruto = quadro.get("data")
    if isinstance(bruto, dict | list):
        return bruto
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    dados = bytes_do_corpo(bruto)
    if dados is None:
        return como_json(bruto)
    if str(quadro.get("compressType") or "").upper() == "GZIP" or dados[:2] == b"\x1f\x8b":
        dados = descomprimir(dados) or dados
    try:
        return como_json(dados.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def _quadro(quadro: Any, leitura: Leitura) -> str:
    quadro = dicionario(quadro)
    tipo = str(quadro.get("type") or "").upper()
    protocolo = str(quadro.get("protocol") or "").upper()
    if protocolo.startswith("HEARTBEAT") or tipo in ("ACK", "RES", "PING", "PONG"):
        leitura.reconhecidos["accs_controle"] += 1
        return CONTROLE
    if tipo != "DATA":
        leitura.desconhecido(f"accs.type={tipo or '?'}")
        return IGNORADO
    if quadro.get("serviceId") not in SERVICOS_ACCS:
        leitura.reconhecidos["accs_outro_servico"] += 1
        return CONTROLE
    dados = _dados_accs(quadro)
    if dados is None:
        leitura.desconhecido("accs.data_ilegivel")
        return IGNORADO
    usou = False
    for d in dados if isinstance(dados, list) else [dados]:
        d = dicionario(d)
        biz = json_ou_valor(d.get("bizDataV2") or d.get("bizData"))
        if isinstance(biz, dict):
            usou |= ler_item(biz, leitura, corpo_sync=d.get("syncBody"))
        else:
            # Só a campainha (o conteúdo vem pelo sync que a página faz).
            leitura.reconhecidos["accs_campainha"] += 1
    return USADO if usou else CONTROLE


def _ws(evento: Evento, leitura: Leitura) -> str:
    obj = como_json(evento.corpo)
    if obj is None:
        leitura.desconhecido("ws.corpo_nao_json")
        return IGNORADO
    resultados = [_quadro(q, leitura) for q in (obj if isinstance(obj, list) else [obj])]
    if USADO in resultados:
        return USADO
    return CONTROLE if CONTROLE in resultados else IGNORADO


# ── mtop (HTTP) ───────────────────────────────────────────────────────────


def _api_da_url(url: str) -> str:
    m = _API_NO_CAMINHO.search(caminho(url))
    return m.group("api").lower() if m else ""


def _sync(dados: dict, leitura: Leitura) -> str:
    usou = False
    achou = False
    candidatos = (
        dados.get("syncDataValuesMap"),
        dicionario(dados.get("result")).get("syncDataValuesMap"),
    )
    for mapa in candidatos:
        mapa = dicionario(mapa)
        for itens in mapa.values():
            if not isinstance(itens, list):
                continue
            achou = True
            for entrada in itens:
                entrada = dicionario(entrada)
                biz = json_ou_valor(entrada.get("bizData") or entrada.get("bizDataV2"))
                usou |= ler_item(biz, leitura, corpo_sync=entrada.get("syncBody"))
    if not achou:
        leitura.desconhecido("sync.sem_syncDataValuesMap")
        return IGNORADO
    return USADO if usou else CONTROLE


def _sessoes(dados: dict, leitura: Leitura) -> str:
    listas = listas_na_chave(dados, "sessionViewDTOList")
    if not listas:
        leitura.desconhecido("sessoes.sem_sessionViewDTOList")
        return IGNORADO
    usou = [ler_sessao(dto, leitura) for lista in listas for dto in lista]
    return USADO if any(usou) else CONTROLE


def _historico(dados: dict, leitura: Leitura) -> str:
    itens = dados.get("result")
    if not isinstance(itens, list):
        leitura.desconhecido("historico.sem_result")
        return IGNORADO
    usou = [ler_item(i, leitura) for i in itens]
    return USADO if any(usou) else CONTROLE


def _nao_lidas(dados: dict, leitura: Leitura) -> str:
    n = inteiro(dados.get("result"))
    if n is not None:
        leitura.sinais["nao_lidas_loja"] = n
    return CONTROLE


_TRATAR = {
    API_SYNC: _sync,
    API_SESSOES: _sessoes,
    API_HISTORICO: _historico,
    API_NAO_LIDAS: _nao_lidas,
}


def _http(evento: Evento, leitura: Leitura) -> str:
    # O nome da api pela URL ANTES de abrir o corpo: o do getToken (token da
    # conexão ACCS) nem é lido.
    api = _api_da_url(evento.url)
    if api in _APIS_CONTROLE:
        leitura.reconhecidos["mtop_controle"] += 1
        return CONTROLE
    envelope = como_json(evento.corpo)
    if not api and isinstance(envelope, dict) and isinstance(envelope.get("api"), str):
        api = envelope["api"].strip().lower()
        if api in _APIS_CONTROLE:
            leitura.reconhecidos["mtop_controle"] += 1
            return CONTROLE
    tratar = _TRATAR.get(api)
    if tratar is None:
        leitura.desconhecido(f"api:{api}" if api else f"http:{caminho_para_log(evento.url)}")
        return IGNORADO
    if not isinstance(envelope, dict):
        leitura.desconhecido("mtop.corpo_nao_json")
        return IGNORADO
    ret = envelope.get("ret")
    if isinstance(ret, list) and ret and not any(str(r).startswith("SUCCESS") for r in ret):
        # Só o CÓDIGO (RGV587_ERROR, FAIL_SYS_USER_VALIDATE...): o texto depois
        # do "::" é mensagem da plataforma.
        codigo = str(ret[0]).split("::", 1)[0][:60]
        if codigo in _ERROS_DE_ROTINA:
            # Token da mtop vencido: a página renova e repete sozinha (visto no
            # teste real de 30/09 logo na abertura) — não é erro da loja.
            leitura.reconhecidos["mtop_token_renovado"] += 1
            return CONTROLE
        leitura.sinais["erro_plataforma"] = codigo
        leitura.reconhecidos["erro_plataforma"] += 1
        return CONTROLE
    dados = dicionario(envelope.get("data"))
    return tratar(dados, leitura)


def ler(eventos: list[Evento]) -> Leitura:
    """Uma leva de eventos do robô → `Leitura`. Nunca levanta por formato."""
    leitura = Leitura()
    for evento in eventos:
        try:
            if evento.tipo == "ws":
                resultado = _ws(evento, leitura)
            elif evento.tipo == "http":
                resultado = _http(evento, leitura)
            else:
                leitura.desconhecido(f"evento.tipo={evento.tipo}")
                resultado = IGNORADO
        except (TypeError, ValueError, AttributeError, KeyError) as e:
            leitura.desconhecido(f"erro_leitor.{type(e).__name__}")
            resultado = IGNORADO
        leitura.contar(resultado)
    return leitura
