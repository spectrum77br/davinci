"""Leitor do chat da Temu (Seller Center BR) a partir do que o robô copiou da página.

A Temu não tem API de chat. A aba `br.seller.temu.com/chat.html` (lista de
conversas, NENHUMA selecionada) recebe tudo sozinha, e o robô só copia:

  • WebSocket "Titan" (wss://<host>?ws-titan-request-sign=...): quadro
    binário (cabeçalho de 16 bytes + protobuf, gzip opcional) com um JSON
    dentro. O do chat tem `actionId` 100260003 e `payload.push_type`:
      2 = dados — `push_data.seq_type` 1 (mensagem em `data[i].message`),
          103 (estado/grupo da conversa) e 10 (recibo de leitura);
      3 = evento — `push_data.type` 101 traz o prazo (`deadlineTime`).
    O robô manda o quadro em base64 (ou já em JSON, se decodificar na
    página): os dois servem.
  • HTTP que a página faz sozinha a cada 60 s (envelope
    `{"success", "errorCode", "result"}`):
      /api/plateau/conv/getConvList e getBizConvList → result.data[] conversa
      /api/plateau/conv/getConvInfo                  → result = conversa
      /api/plateau/sync/message                      → result.syncData[]
      /api/plateau/message/getHistoryMsg             → result.data[].message
      /api/plateau/conv/needReplyCount               → result.needReplyCount
      /api/plateau/conv/unReadConvList, sync/getInitState → só controle

Mensagem (reconstruída do código da página): msg_id, conv_id, type (0 texto,
1 imagem, 14 vídeo, 16 arquivo, 31 sistema, 64 cartão, 1002 edição/recolhida),
content, info, ts (segundos), from{uid, user_type}, to{uid, user_type},
context{robot}. É do COMPRADOR quando `to.user_type == 2` (vai para a loja);
da LOJA quando `from.user_type == 2`; `context.robot == 1` é o robô da Temu.
O pedido só aparece em cartão: `info.data.parent_order_sn` (PO-...).

A lista só traz a ÚLTIMA mensagem de cada conversa. Quando ela é do robô da
Temu (ou aviso do sistema) e a Temu diz "sem resposta" (`groupList` com
unReply; sem grupos, o `countDownInfo.lastUnReplyTime`), entra o marcador da
pergunta que o robô não viu — senão a conversa sairia da fila.

Nada aqui chama a Temu, nem pede nada à página: é Python puro sobre cópias.
Texto de comprador nunca vai para o log (só contagens e nomes de campo).
"""

from __future__ import annotations

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
    TIPO_ARQUIVO,
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
    dicionario,
    inteiro,
    jsons_embutidos,
    marcador_nao_vista,
    quando,
    texto,
)

PLATAFORMA = "temu"

# O SDK de chat da página só aceita este `actionId` da Titan.
ACTION_CHAT = 100260003
PUSH_DADOS = 2
PUSH_EVENTO = 3
EVENTO_PRAZO = 101
SEQ_MENSAGEM = 1
SEQ_LEITURA = 10
SEQ_ESTADO = 103

USER_COMPRADOR = 1
USER_LOJA = 2

T_TEXTO = 0
T_IMAGEM = 1
T_VIDEO = 14
T_ARQUIVO = 16
T_SISTEMA = 31
T_CARTAO = 64
T_EDICAO = 1002

# O grupo da lista que diz "o comprador espera resposta".
GRUPO_SEM_RESPOSTA = "unReply"

# Chaves que só o JSON do chat tem: é o que o garimpo no binário procura.
_MARCAS = (b'"push_data"', b'"push_type"', b'"actionId"', b'"msg_id"')

_CONHECIDOS_CONVERSA = frozenset(
    {
        "convId",
        "conv_id",
        "chatTypeId",
        "convInfo",
        "message",
        "hostId",
        "convUserType",
        "uid",
        "stateData",
        "countDownInfo",
        "convUserPreferLanguage",
        "otherLastRead",
        "agentLastRead",
        "userLastRead",
        "top",
        "mark",
        "markType",
        "extra",
        "ext",
    }
)
_CONHECIDOS_MENSAGEM = frozenset(
    {
        "msg_id",
        "msgId",
        "client_msg_id",
        "conv_id",
        "convId",
        "chat_type_id",
        "type",
        "content",
        "info",
        "ts",
        "from",
        "to",
        "context",
        "quote_msg",
        "translated_content",
        "region_id",
        "status",
        "biz_context",
        "extra",
        "ext",
        "host_id",
        "is_expired",
    }
)


# ── Mensagem ──────────────────────────────────────────────────────────────


def _autor(msg: dict, tipo: int, leitura: Leitura) -> tuple[str, bool]:
    """(autor, é o robô da Temu?) pela direção da mensagem."""
    de = dicionario(msg.get("from"))
    para = dicionario(msg.get("to"))
    contexto = dicionario(msg.get("context"))
    if tipo == T_SISTEMA:
        return AUTOR_SISTEMA, False
    if inteiro(contexto.get("robot")) == 1 or contexto.get("robot") is True:
        # Resposta automática do robô da Temu: não é a equipe respondendo.
        return AUTOR_SISTEMA, True
    tipo_de = inteiro(de.get("user_type"))
    tipo_para = inteiro(para.get("user_type"))
    if tipo_para == USER_LOJA or tipo_de == USER_COMPRADOR:
        return AUTOR_CLIENTE, False
    if tipo_de == USER_LOJA:
        return AUTOR_LOJA, False
    leitura.desconhecido(f"mensagem.from.user_type={tipo_de}")
    # Na dúvida, SISTEMA: não tira a conversa da fila (loja) nem inventa
    # pergunta (cliente).
    return AUTOR_SISTEMA, False


def _conteudo(msg: dict, tipo: int, leitura: Leitura) -> tuple[str, str | None, list, str | None]:
    """(tipo da caixa, texto, anexos, pedido) pelo `type` da Temu."""
    info = dicionario(msg.get("info"))
    conteudo = msg.get("content")
    if tipo in (T_TEXTO, T_SISTEMA):
        return TIPO_TEXTO, texto(conteudo) if isinstance(conteudo, str) else None, [], None
    if tipo == T_IMAGEM:
        return TIPO_IMAGEM, None, anexo(TIPO_IMAGEM, info.get("image_url") or info.get("url")), None
    if tipo == T_VIDEO:
        previa = dicionario(info.get("preview"))
        url = info.get("download_url") or info.get("url") or previa.get("url")
        return TIPO_VIDEO, None, anexo(TIPO_VIDEO, url), None
    if tipo == T_ARQUIVO:
        nome = texto(info.get("file_name"))
        return TIPO_ARQUIVO, None, anexo(TIPO_ARQUIVO, info.get("download_url"), nome=nome), None
    if tipo == T_CARTAO:
        dados = dicionario(info.get("data"))
        pedido = texto(dados.get("parent_order_sn") or dados.get("parentOrderSn"))
        if pedido:
            return TIPO_PEDIDO, None, [enriquecer.cartao_pedido_vazio(pedido)], pedido
        produto = dicionario(dados.get("goods_info") or dados.get("goodsInfo"))
        item = texto(produto.get("goods_id") or produto.get("goodsId") or dados.get("goods_id"))
        if item:
            return TIPO_PRODUTO, None, [enriquecer.cartao_produto_vazio(item)], None
        leitura.desconhecido(f"cartao.key={texto(info.get('key')) or '?'}")
        return TIPO_OUTRO, rotulo_tipo_plataforma(None), [], None
    leitura.desconhecido(f"mensagem.type={tipo}")
    t = texto(conteudo) if isinstance(conteudo, str) else None
    return TIPO_OUTRO, t or rotulo_tipo_plataforma(None), [], None


def ler_mensagem(msg: Any, leitura: Leitura, conversa_id: str | None = None) -> bool:
    """Uma mensagem da Temu → a conversa dela na `leitura`. False = nada gravável."""
    msg = dicionario(msg)
    msg_id = texto(msg.get("msg_id") or msg.get("msgId"))
    conv_id = texto(msg.get("conv_id") or msg.get("convId")) or conversa_id
    if not msg_id or not conv_id:
        leitura.desconhecido("mensagem.sem_id")
        return False
    tipo = inteiro(msg.get("type"))
    tipo = T_TEXTO if tipo is None else tipo
    if tipo == T_EDICAO:
        # Edição/recolhimento de uma mensagem anterior: a mensagem gravada é
        # imutável aqui (como nas outras lojas); conta e segue.
        leitura.reconhecidos["edicao"] += 1
        return False
    campos_desconhecidos(leitura, msg, _CONHECIDOS_MENSAGEM, "mensagem")
    autor, robo = _autor(msg, tipo, leitura)
    tipo_caixa, conteudo, anexos, pedido = _conteudo(msg, tipo, leitura)
    conversa = leitura.conversa(conv_id)
    if autor == AUTOR_CLIENTE:
        uid = texto(dicionario(msg.get("from")).get("uid"))
        if uid and uid != "0" and conversa.comprador_id is None:
            conversa.comprador_id = uid
    if pedido:
        conversa.juntar(pedido=pedido)
    conversa.mensagem(
        MensagemLida(
            externo_id=msg_id,
            autor=autor,
            tipo=tipo_caixa,
            texto=conteudo,
            enviada_em=quando(msg.get("ts")),
            anexos=anexos,
            payload={"fonte": "robo", "plataforma": PLATAFORMA, "item": msg},
            robo_plataforma=robo,
        )
    )
    leitura.reconhecidos["mensagem"] += 1
    return True


# ── Conversa ──────────────────────────────────────────────────────────────


def ler_conversa(conv: Any, leitura: Leitura) -> bool:
    """Um item de getConvList/getBizConvList/getConvInfo → conversa (+ última mensagem)."""
    conv = dicionario(conv)
    conv_id = texto(conv.get("convId") or conv.get("conv_id"))
    if not conv_id:
        leitura.desconhecido("conversa.sem_convId")
        return False
    campos_desconhecidos(leitura, conv, _CONHECIDOS_CONVERSA, "conversa")
    info = dicionario(conv.get("convInfo"))
    c = leitura.conversa(conv_id)
    uid = texto(conv.get("uid"))
    c.juntar(
        comprador_nome=texto(info.get("name")),
        comprador_avatar=texto(info.get("avatar")),
        nao_lidas=inteiro(info.get("unreadCount")),
        comprador_id=uid if uid and uid != "0" else None,
    )
    grupos = dicionario(conv.get("stateData")).get("groupList")
    if isinstance(grupos, list):
        # unReply / replied / noNeedReply / robotReplied / important: o que a
        # Temu acha da conversa (a fila do DaVinci sai das mensagens).
        grupos = [str(g)[:32] for g in grupos if isinstance(g, str | int)][:10]
        c.dados["grupos"] = grupos
    contagem = dicionario(conv.get("countDownInfo"))
    prazo = quando(contagem.get("deadlineTime"))
    if prazo is not None:
        c.dados["prazo_plataforma"] = prazo.isoformat()
    bruta = conv.get("message")
    if isinstance(bruta, dict | str) and ler_mensagem(bruta, leitura, conv_id):
        ultima = dicionario(bruta)
        lida = c.mensagens.get(texto(ultima.get("msg_id") or ultima.get("msgId")) or "")
        # Sem grupos, o `lastUnReplyTime` é o sinal de "sem resposta".
        pergunta_em = quando(contagem.get("lastUnReplyTime"))
        sem_resposta = (
            GRUPO_SEM_RESPOSTA in grupos if isinstance(grupos, list) else (pergunta_em is not None)
        )
        if lida is not None and lida.autor == AUTOR_SISTEMA and sem_resposta:
            # A lista só traz a ÚLTIMA mensagem, e ela é do robô da Temu (ou
            # aviso do sistema) — que não conta como pergunta. A Temu diz que
            # o comprador espera: a pergunta existe, o robô é que não a viu
            # (1ª leitura, robô fora). O marcador a põe na fila (revisão 30/09).
            antes = lida.enviada_em - timedelta(seconds=1) if lida.enviada_em else None
            c.mensagem(
                marcador_nao_vista(
                    conv_id,
                    hora=pergunta_em or antes,
                    referencia=str(contagem.get("lastUnReplyTime") or lida.externo_id),
                    plataforma=PLATAFORMA,
                )
            )
    leitura.reconhecidos["conversa"] += 1
    return True


# ── Titan (WebSocket) e sync ──────────────────────────────────────────────


def _dados_de_sync(seq_type: int | None, itens: Any, leitura: Leitura) -> bool:
    """Os itens de um `seq_type` (do push ou do /sync/message)."""
    if not isinstance(itens, list):
        return False
    usou = False
    for item in itens:
        item = dicionario(item)
        if seq_type == SEQ_MENSAGEM or (seq_type is None and "message" in item):
            usou |= ler_mensagem(item.get("message", item), leitura)
        elif seq_type == SEQ_LEITURA:
            leitura.reconhecidos["recibo_leitura"] += 1
        elif seq_type == SEQ_ESTADO:
            leitura.reconhecidos["estado_conversa"] += 1
        else:
            leitura.desconhecido(f"sync.seq_type={seq_type}")
    return usou


def _push(obj: Any, leitura: Leitura) -> str:
    """Um JSON vindo da Titan → USADO / CONTROLE / IGNORADO."""
    obj = dicionario(obj)
    if not obj:
        return IGNORADO
    if "actionId" in obj:
        acao = inteiro(obj.get("actionId"))
        if acao != ACTION_CHAT:
            leitura.reconhecidos["titan_outra_acao"] += 1
            return CONTROLE
        payload = dicionario(obj.get("payload"))
    elif "push_type" in obj or "push_data" in obj:
        payload = obj
    elif "msg_id" in obj or "msgId" in obj:
        return USADO if ler_mensagem(obj, leitura) else IGNORADO
    elif "message" in obj and isinstance(obj.get("message"), dict | str):
        return USADO if ler_mensagem(obj.get("message"), leitura) else IGNORADO
    else:
        return IGNORADO
    tipo_push = inteiro(payload.get("push_type"))
    dados = dicionario(payload.get("push_data"))
    if tipo_push == PUSH_DADOS:
        usou = _dados_de_sync(inteiro(dados.get("seq_type")), dados.get("data"), leitura)
        return USADO if usou else CONTROLE
    if tipo_push == PUSH_EVENTO:
        evento = dicionario(dados.get("data"))
        conv_id = texto(evento.get("convId") or evento.get("conv_id"))
        prazo = quando(evento.get("deadlineTime"))
        if inteiro(dados.get("type")) == EVENTO_PRAZO and conv_id and prazo is not None:
            leitura.conversa(conv_id).dados["prazo_plataforma"] = prazo.isoformat()
            leitura.reconhecidos["prazo"] += 1
            return USADO
        leitura.reconhecidos["evento_titan"] += 1
        return CONTROLE
    leitura.desconhecido(f"titan.push_type={tipo_push}")
    return IGNORADO


def _ws(evento: Evento, leitura: Leitura) -> str:
    """Quadro da Titan. O robô diz no `metodo` como ler o corpo: "binario"
    (base64 do quadro), "json" (o payload que a própria página decodificou)
    ou "texto" (quadro de texto, como veio). Sem `metodo`, tenta os dois."""
    obj = como_json(evento.corpo)
    if obj is not None:
        candidatos = obj if isinstance(obj, list) else [obj]
    elif (evento.metodo or "").lower() in ("texto", "json"):
        # Texto que não é JSON não é quadro binário disfarçado.
        leitura.desconhecido(f"ws.{evento.metodo.lower()}_nao_json")
        return IGNORADO
    else:
        dados = bytes_do_corpo(evento.corpo)
        if dados is None:
            leitura.desconhecido("ws.corpo_ilegivel")
            return IGNORADO
        candidatos = jsons_embutidos(dados, marcas=_MARCAS)
        if not candidatos:
            # Batimento, ping/pong, ack de transporte: binário sem JSON de chat.
            leitura.reconhecidos["titan_controle"] += 1
            return CONTROLE
    resultados = [_push(c, leitura) for c in candidatos]
    if USADO in resultados:
        return USADO
    if CONTROLE in resultados:
        return CONTROLE
    leitura.desconhecido("ws.json_sem_chat")
    return IGNORADO


# ── HTTP ──────────────────────────────────────────────────────────────────


def _lista(resultado: dict, leitura: Leitura) -> str:
    itens = resultado.get("data")
    if not isinstance(itens, list):
        itens = resultado.get("list")
    if itens is None and ("data" in resultado or "list" in resultado):
        itens = []  # `"data": null` é lista vazia
    if not isinstance(itens, list):
        # Sem `data`/`list` não dá para dizer "loja sem conversa" — pode ser a
        # Temu que mudou o formato. Registra os NOMES das chaves (nunca valor).
        chaves = ",".join(sorted(str(k) for k in resultado)[:6]) or "vazio"
        leitura.desconhecido(f"conv_lista.chaves={chaves}")
        return IGNORADO
    usou = [ler_conversa(c, leitura) for c in itens]
    return USADO if any(usou) else CONTROLE


def _uma(resultado: dict, leitura: Leitura) -> str:
    alvo = resultado.get("conv") if isinstance(resultado.get("conv"), dict) else resultado
    return USADO if ler_conversa(alvo, leitura) else IGNORADO


def _sync(resultado: dict, leitura: Leitura) -> str:
    blocos = resultado.get("syncData")
    if not isinstance(blocos, list):
        leitura.desconhecido("sync.sem_syncData")
        return IGNORADO
    usou = False
    for bloco in blocos:
        bloco = dicionario(bloco)
        usou |= _dados_de_sync(inteiro(bloco.get("seqType")), bloco.get("data"), leitura)
    return USADO if usou else CONTROLE


def _historico(resultado: dict, leitura: Leitura) -> str:
    itens = resultado.get("data") if isinstance(resultado.get("data"), list) else []
    usou = False
    for item in itens:
        item = dicionario(item)
        usou |= ler_mensagem(item.get("message", item), leitura)
    return USADO if usou else CONTROLE


def _precisa_responder(resultado: dict, leitura: Leitura) -> str:
    n = inteiro(resultado.get("needReplyCount"))
    if n is not None:
        leitura.sinais["precisa_responder"] = n
    return CONTROLE


def _so_controle(_resultado: dict, _leitura: Leitura) -> str:
    return CONTROLE


# Pelo FIM do caminho: o prefixo de /latitude/* muda por região.
_ROTAS = (
    ("/conv/getconvlist", _lista),
    ("/conv/getbizconvlist", _lista),
    ("/conv/getconvinfo", _uma),
    ("/sync/message", _sync),
    ("/message/gethistorymsg", _historico),
    ("/conv/needreplycount", _precisa_responder),
    ("/conv/unreadconvlist", _so_controle),
    ("/sync/getinitstate", _so_controle),
)


def _http(evento: Evento, leitura: Leitura) -> str:
    rota = caminho(evento.url)
    tratar = next((f for fim, f in _ROTAS if rota.endswith(fim)), None)
    if tratar is None:
        leitura.desconhecido(f"http:{caminho_para_log(evento.url)}")
        return IGNORADO
    envelope = como_json(evento.corpo)
    if not isinstance(envelope, dict):
        leitura.desconhecido("http.corpo_nao_json")
        return IGNORADO
    if envelope.get("success") is False:
        # Só o CÓDIGO (54001 = captcha/antirrobô): a mensagem pode ter texto.
        codigo = texto(envelope.get("errorCode") or envelope.get("error_code")) or "?"
        leitura.sinais["erro_plataforma"] = codigo[:32]
        leitura.reconhecidos["erro_plataforma"] += 1
        return CONTROLE
    resultado = envelope.get("result", envelope)
    resultado = resultado if isinstance(resultado, dict) else {"data": resultado}
    return tratar(resultado, leitura)


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
            # Formato inesperado num evento não derruba a leva.
            leitura.desconhecido(f"erro_leitor.{type(e).__name__}")
            resultado = IGNORADO
        leitura.contar(resultado)
    return leitura
