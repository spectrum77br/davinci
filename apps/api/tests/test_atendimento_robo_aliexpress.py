"""O leitor do chat do AliExpress (robô do Mac mini) — Python puro, eventos sintéticos.

Formatos RECONSTRUÍDOS do código da tela (im-ae 1.42.0; a loja observada não
tinha conversa): quadro ACCS com `data` em base64 (e GZIP) trazendo
`bizDataV2`, resposta mtop do `js.sync` e do `querySessionList`. Confere:

- autor por `fromType` (11 comprador, 12/13 loja), robô da plataforma
  (`im_ai`) como `sistema`;
- a sessão da lista vira conversa (nome, foto, não lidas, tags) e a prévia
  da última mensagem vira mensagem `previa:` (sem id próprio);
- cartão de pedido dá o número;
- batimento e ACK são controle; a resposta do getToken nem é lida;
- erro do mtop guarda só o código; o que não se conhece vira NOME contado.
"""

from __future__ import annotations

import base64
import gzip
import json

from app.services.atendimento import robo_aliexpress
from app.services.atendimento.robo_leitura import PREFIXO_PREVIA, Evento

MTOP = "https://seller-acs.aliexpress.com/h5"
TEXTO_COMPRADOR = "Hello, where is my order? My phone is 5511999999999"


def _item(mid: str, sid: str, *, from_type: int, texto: str = TEXTO_COMPRADOR, card="1", **td):
    return {
        "header": {"type": 1},
        "body": {
            "typeData": {
                "mid": mid,
                "sid": sid,
                "from": td.pop("de", "buyer-77" if from_type == 11 else "seller-1"),
                "fromType": from_type,
                "sendTime": td.pop("sendTime", 1759150000123),
                "bizUnique": "u-" + mid,
                "status": td.pop("status", 0),
                "ext": td.pop("ext", {}),
                **td,
            },
            "layoutData": {"card": card},
            "templateData": json.dumps(td.pop("modelo", None) or {"txt": texto}),
        },
    }


def _accs(item: dict, *, gz: bool = False, servico: str = "ae-lz-sync") -> Evento:
    dados = json.dumps(
        {
            "syncBody": {
                "namespace": 2,
                "typeAndIdMap": {"im": 88},
                "serverTime": 1759150000999,
                "sessionId": item["body"]["typeData"]["sid"],
                "messageId": item["body"]["typeData"]["mid"],
                "sellerUserId": 1,
            },
            "bizDataV2": json.dumps(item),
        }
    ).encode()
    if gz:
        dados = gzip.compress(dados)
    quadro = {
        "protocol": "ACCS_H5",
        "type": "DATA",
        "err": "0",
        "serviceId": servico,
        "dataId": "123_abcd",
        "compressType": "GZIP" if gz else "COMMON",
        "data": base64.b64encode(dados).decode(),
        "extHeader": {},
    }
    return Evento(
        tipo="ws",
        url="wss://msgacs.m.aliexpress.com/accs/auth?token=SEGREDO",
        corpo=json.dumps(quadro),
    )


def _mtop(api: str, data: dict, *, ret: str = "SUCCESS::调用成功") -> Evento:
    corpo = {"api": api, "data": data, "ret": [ret], "v": "1.0"}
    return Evento(
        tipo="http",
        url=f"{MTOP}/{api.lower()}/1.0/?jsv=2.7.2&appKey=25556077&t=1&sign=x",
        metodo="POST",
        status=200,
        corpo=json.dumps(corpo),
    )


def test_empurrao_accs_com_bizdatav2_comum_e_gzip():
    comum = _accs(_item("m-1", "s-1", from_type=11))
    compactado = _accs(_item("m-2", "s-1", from_type=11, texto="segunda"), gz=True)
    leitura = robo_aliexpress.ler([comum, compactado])
    assert leitura.usados == 2
    c = leitura.conversas["s-1"]
    assert c.comprador_id == "buyer-77"
    assert c.mensagens["m-1"].autor == "cliente"
    assert c.mensagens["m-1"].texto == TEXTO_COMPRADOR
    assert c.mensagens["m-1"].enviada_em.year == 2025
    assert c.mensagens["m-2"].texto == "segunda"


def test_batimento_ack_e_campainha_sao_controle():
    batimento = Evento(
        tipo="ws", corpo=json.dumps({"type": "ACK", "protocol": "HEARTBEAT_ACCS_H5", "data": ""})
    )
    ping = Evento(
        tipo="ws", corpo=json.dumps({"type": "RES", "protocol": "ACCS_H5", "serviceId": "h5_ping"})
    )
    # Empurrão só com o syncBody (sem bizDataV2): a página puxa pelo sync.
    campainha = Evento(
        tipo="ws",
        corpo=json.dumps(
            {
                "type": "DATA",
                "serviceId": "ae-csp-im-sync",
                "data": base64.b64encode(
                    json.dumps({"syncBody": {"sessionId": "s"}}).encode()
                ).decode(),
            }
        ),
    )
    leitura = robo_aliexpress.ler([batimento, ping, campainha])
    assert leitura.controle == 3 and leitura.usados == 0 and leitura.ignorados == 0
    assert leitura.reconhecidos["accs_campainha"] == 1


def test_sync_mtop_com_comprador_loja_e_robo_da_plataforma():
    itens = [
        _item("m-10", "s-2", from_type=11),
        _item("m-11", "s-2", from_type=12, texto="Enviamos ontem."),
        _item("m-12", "s-2", from_type=12, texto="Resposta automática", ext={"fromCode": "im_ai"}),
        _item("m-13", "s-2", from_type=11, status=1, texto="apaguei isto"),
        {"header": {"type": 4}, "body": {"typeData": {"sid": "s-2"}}},
    ]
    evento = _mtop(
        "mtop.gsp.web.seller.js.sync",
        {
            "syncDataStatusMap": {"im": -1},
            "syncDataValuesMap": {
                "im": [
                    {
                        "syncBody": {"syncId": 90 + i, "sellerUserId": 1, "namespace": 2},
                        "bizData": json.dumps(it),
                    }
                    for i, it in enumerate(itens)
                ]
            },
        },
    )
    leitura = robo_aliexpress.ler([evento])
    msgs = leitura.conversas["s-2"].mensagens
    assert msgs["m-10"].autor == "cliente"
    assert msgs["m-11"].autor == "loja"
    assert msgs["m-12"].autor == "sistema" and msgs["m-12"].robo_plataforma
    # Recolhida pelo comprador: não mostramos o que ele retirou.
    assert msgs["m-13"].texto == "[Mensagem recolhida]"
    assert leitura.reconhecidos["status_ou_leitura"] == 1


def test_lista_de_sessoes_vira_conversa_com_previa():
    evento = _mtop(
        "mtop.gsp.im.use.web.seller.messagebox.querySessionList",
        {
            "userIdToSessionView": {
                "1": {
                    "nameSpaceToSessionView": {
                        "2": {
                            "sessionViewDTOList": [
                                {
                                    "sessionId": "s-3",
                                    "serverTime": 1759150000000,
                                    "target": {"targetId": "buyer-5", "userAccountType": 11},
                                    "sessionData": {
                                        "title": "John B.",
                                        "headUrl": "https://ae01.alicdn.com/x.png",
                                        "content": TEXTO_COMPRADOR,
                                        "nonReadNumber": 1,
                                        "senderId": "buyer-5",
                                        "senderAccountType": 11,
                                        "tags": ["un-replied"],
                                    },
                                }
                            ],
                            "hasMore": False,
                        }
                    }
                }
            },
            "nameSpaceToSyncData": {"2": {"syncNamespace": 2, "syncId": 88}},
        },
    )
    leitura = robo_aliexpress.ler([evento])
    c = leitura.conversas["s-3"]
    assert (c.comprador_id, c.comprador_nome, c.nao_lidas) == ("buyer-5", "John B.", 1)
    assert c.dados["tags"] == ["un-replied"]
    [previa] = c.mensagens.values()
    assert previa.previa and previa.externo_id.startswith(f"{PREFIXO_PREVIA}s-3:")
    assert previa.autor == "cliente" and previa.texto == TEXTO_COMPRADOR
    # O id da prévia é um hash: texto de comprador não vira id.
    assert "where" not in previa.externo_id
    # Relida (a lista se repete a cada atualização): o mesmo id.
    de_novo = robo_aliexpress.ler([evento]).conversas["s-3"]
    assert list(de_novo.mensagens) == [previa.externo_id]


def test_pedido_token_erro_e_desconhecido():
    pedido = _accs(
        _item(
            "m-20",
            "s-4",
            from_type=11,
            card="10004",
            modelo={"orderId": "8190000111", "status": "x"},
        )
    )
    token = _mtop("mtop.gsp.im.use.web.seller.getToken", {"result": "TOKEN-DA-CONEXAO"})
    nao_lidas = _mtop("mtop.gsp.im.use.web.seller.unreadcount", {"result": 4})
    punido = _mtop("mtop.gsp.web.seller.js.sync", {}, ret="RGV587_ERROR::SM::" + TEXTO_COMPRADOR)
    api_nova = _mtop("mtop.gsp.im.algo.novo", {"result": [TEXTO_COMPRADOR]})
    card_novo = _accs(
        _item("m-21", "s-4", from_type=11, card="99999", modelo={"foo": TEXTO_COMPRADOR})
    )
    leitura = robo_aliexpress.ler([pedido, token, nao_lidas, punido, api_nova, card_novo])
    c = leitura.conversas["s-4"]
    assert c.pedido == "8190000111"
    assert c.mensagens["m-20"].tipo == "pedido"
    assert c.mensagens["m-21"].tipo == "outro" and c.mensagens["m-21"].texto == "[Mensagem]"
    assert leitura.sinais["nao_lidas_loja"] == 4
    assert leitura.sinais["erro_plataforma"] == "RGV587_ERROR"
    assert leitura.reconhecidos["mtop_controle"] == 1
    assert leitura.desconhecidos["api:mtop.gsp.im.algo.novo"] == 1
    assert leitura.desconhecidos["mensagem.card=99999"] == 1
    tudo = json.dumps([dict(leitura.desconhecidos), leitura.sinais, dict(leitura.reconhecidos)])
    assert "5511999999999" not in tudo and "TOKEN-DA-CONEXAO" not in tudo


def test_token_da_mtop_vencido_e_rotina():
    """Teste real (30/09): a abertura do IM trouxe FAIL_SYS_TOKEN_EXOIRED — a
    página renova o token e repete sozinha. Não é erro da loja."""
    vencido = _mtop(
        "mtop.gsp.im.use.web.seller.messagebox.querySessionList",
        {},
        ret="FAIL_SYS_TOKEN_EXOIRED::令牌过期",
    )
    leitura = robo_aliexpress.ler([vencido])
    assert "erro_plataforma" not in leitura.sinais
    assert leitura.reconhecidos["mtop_token_renovado"] == 1
    assert leitura.ignorados == 0


def test_sessao_no_formato_real_da_vita():
    """Forma da querySessionList na loja Vita (teste real de 30/09, só os NOMES
    de campo): senderAccountType e lastMessageTime vêm como TEXTO, e a sessão
    traz lastMessageId, version, viewModifyTime, sellerId e buyerAppUrl."""
    dto = {
        "entityId": "e-1",
        "namespace": 2,
        "realSessionId": "r-1",
        "serverTime": 1759236000000,
        "sessionId": "s-9",
        "status": 0,
        "tag": 0,
        "type": 103,
        "target": {"targetId": "buyer-9", "userAccountType": 11},
        "sessionData": {
            "selfPosition": "1759000000000",
            "country": "BR",
            "toPosition": "0",
            "viewModifyTime": 1759000000001,
            "headUrl": "https://ae01.alicdn.com/y.png",
            "lastMessageTime": "1759064042233",
            "title": "Comprador",
            "version": 1759000000002,
            "content": TEXTO_COMPRADOR,
            "senderAccountType": "12",
            "tags": [],
            "senderId": "seller-1",
            "sellerId": "seller-1",
            "lastMessageId": "1001234567890123456789",
            "buyerAppUrl": "https://m.aliexpress.com/app/x",
        },
    }
    evento = _mtop(
        "mtop.gsp.im.use.web.seller.messagebox.querySessionList",
        {
            "userIdToSessionView": {
                "1": {
                    "nameSpaceToSessionView": {"2": {"sessionViewDTOList": [dto], "hasMore": False}}
                }
            }
        },
    )
    leitura = robo_aliexpress.ler([evento])
    assert not leitura.desconhecidos and leitura.ignorados == 0
    [previa] = leitura.conversas["s-9"].mensagens.values()
    assert previa.autor == "loja"  # "12" em texto
    assert previa.enviada_em is not None and previa.enviada_em.year == 2025
    assert previa.payload["mensagem_id"] == "1001234567890123456789"


def _lista(*dtos: dict) -> Evento:
    return _mtop(
        "mtop.gsp.im.use.web.seller.messagebox.querySessionList",
        {"x": {"sessionViewDTOList": list(dtos)}},
    )


def _sessao(sid: str, conteudo: str, **dados) -> dict:
    return {
        "sessionId": sid,
        "serverTime": 1759236000000,
        "target": {"targetId": "buyer-1", "userAccountType": 11},
        "sessionData": {"title": "Comprador", "content": conteudo, **dados},
    }


def test_id_da_previa_vem_do_id_da_ultima_mensagem_ou_da_hora():
    """Revisão 30/09: o id da prévia não pode ser só o hash do texto — "Hello"
    de hoje e "Hello" de semana passada são mensagens diferentes."""
    com_id = _sessao(
        "s-1",
        "Hello",
        senderAccountType="11",
        lastMessageId="30011",
        lastMessageTime="1759064042233",
    )
    [p] = robo_aliexpress.ler([_lista(com_id)]).conversas["s-1"].mensagens.values()
    assert p.externo_id == f"{PREFIXO_PREVIA}s-1:30011"
    assert p.payload["mensagem_id"] == "30011" and p.payload["hora"] == "mensagem"

    def _id(**dados) -> str:
        dto = _sessao("s-2", "Hello", senderAccountType="11", **dados)
        [m] = robo_aliexpress.ler([_lista(dto)]).conversas["s-2"].mensagens.values()
        return m.externo_id

    assert _id(lastMessageTime="1759064042233") == _id(lastMessageTime="1759064042233")
    assert _id(lastMessageTime="1759064042233") != _id(lastMessageTime="1759150000000")
    # Sem id nem hora da mensagem: o hash do texto, e a hora é a do servidor.
    dto = _sessao("s-3", "Hello", senderAccountType="11")
    [m] = robo_aliexpress.ler([_lista(dto)]).conversas["s-3"].mensagens.values()
    assert m.payload["hora"] == "servidor" and "Hello" not in m.externo_id


def test_previa_da_ia_com_a_conta_do_vendedor_e_sistema():
    """A IA do AliExpress (e o plugin de atendimento) responde com a conta do
    vendedor; na lista, sem o `fromCode`, só as tags dizem que não foi a
    equipe. `un-replied` diz que o comprador espera: entra o marcador."""
    ia = _sessao(
        "s-4",
        "Hi, I am the assistant",
        senderAccountType="12",
        lastMessageId="40022",
        lastMessageTime="1759064042233",
        tags=["un-replied", "ai-serving"],
    )
    msgs = robo_aliexpress.ler([_lista(ia)]).conversas["s-4"].mensagens
    previa = msgs[f"{PREFIXO_PREVIA}s-4:40022"]
    assert previa.autor == "sistema"
    [marcador] = [m for m in msgs.values() if m.payload.get("nao_vista")]
    assert marcador.autor == "cliente" and marcador.enviada_em < previa.enviada_em
    # Robô de atendimento sem "un-replied": sistema, sem marcador.
    bot = _sessao(
        "s-5",
        "Order shipped",
        senderAccountType="12",
        lastMessageId="50033",
        tags=["bot-agent-service"],
    )
    msgs = robo_aliexpress.ler([_lista(bot)]).conversas["s-5"].mensagens
    assert [m.autor for m in msgs.values()] == ["sistema"]
    # A equipe (sem tag de IA): loja.
    loja = _sessao("s-6", "Enviamos hoje", senderAccountType="12", lastMessageId="60044", tags=[])
    [m] = robo_aliexpress.ler([_lista(loja)]).conversas["s-6"].mensagens.values()
    assert m.autor == "loja"
