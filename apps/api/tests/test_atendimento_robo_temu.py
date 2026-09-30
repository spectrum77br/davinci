"""O leitor do chat da Temu (robô do Mac mini) — Python puro, eventos sintéticos.

Os formatos são os RECONSTRUÍDOS do código da página (a loja observada não
tinha conversa): resposta de getConvList, de /sync/message, e o quadro
binário da Titan (cabeçalho de 16 bytes + protobuf, com e sem gzip). O que
se confere:

- conversa com comprador, foto, não lidas, grupos e o prazo da Temu;
- autor pela direção (to.user_type 2 = comprador → loja; from 2 = loja),
  robô da Temu e aviso do sistema como `sistema`;
- cartão de pedido dá o número do pedido;
- o que não se reconhece vira contagem por NOME (nunca o valor), e o evento
  estranho não derruba a leva;
- erro da plataforma (54001, captcha) guarda só o código.
"""

from __future__ import annotations

import base64
import gzip
import json
import struct

from app.services.atendimento import robo_temu
from app.services.atendimento.robo_leitura import Evento

HOST = "https://br.seller.temu.com"
TEXTO_COMPRADOR = "Oi, meu pedido PO-211 ainda não chegou, moro na rua secreta 42"


def _msg(msg_id: str, conv: str, *, de: int, para: int, tipo: int = 0, **extra) -> dict:
    return {
        "msg_id": msg_id,
        "client_msg_id": "c" * 36,
        "conv_id": conv,
        "chat_type_id": 1,
        "type": tipo,
        "content": extra.pop("content", TEXTO_COMPRADOR),
        "info": extra.pop("info", {}),
        "ts": extra.pop("ts", "1759150000"),
        "from": {"uid": extra.pop("uid", "buyer-9"), "user_type": de},
        "to": {"uid": "mall-1", "user_type": para},
        "context": extra.pop("context", {"robot": 0}),
        **extra,
    }


def _http(caminho: str, resultado: dict, **envelope) -> Evento:
    corpo = {"success": True, "errorCode": 1000000, "result": resultado, **envelope}
    return Evento(
        tipo="http", url=f"{HOST}{caminho}", metodo="POST", status=200, corpo=json.dumps(corpo)
    )


def _conv_list() -> Evento:
    return _http(
        "/api/plateau/conv/getConvList",
        {
            "hasMore": False,
            "data": [
                {
                    "convId": "conv-1",
                    "chatTypeId": 1,
                    "convInfo": {
                        "name": "Maria S.",
                        "avatar": "https://img.temu/a.png",
                        "unreadCount": 2,
                    },
                    "message": _msg("1001", "conv-1", de=1, para=2),
                    "hostId": "h",
                    "convUserType": 1,
                    "uid": 0,
                    "stateData": {"groupList": ["unReply"], "stateDataMap": {}},
                    "countDownInfo": {"deadlineTime": 1759236400, "lastUnReplyTime": 1759150000},
                    "campoNovoDaTemu": {"x": 1},
                }
            ],
        },
    )


def test_lista_de_conversas_traz_conversa_e_ultima_mensagem():
    leitura = robo_temu.ler([_conv_list()])
    assert leitura.usados == 1 and leitura.ignorados == 0
    c = leitura.conversas["conv-1"]
    assert c.comprador_nome == "Maria S."
    assert c.comprador_avatar == "https://img.temu/a.png"
    assert c.nao_lidas == 2
    # `uid` 0 na conversa não é comprador; o da mensagem dele é.
    assert c.comprador_id == "buyer-9"
    assert c.dados["grupos"] == ["unReply"]
    assert c.dados["prazo_plataforma"].startswith("2025-09-30")
    m = c.mensagens["1001"]
    assert m.autor == "cliente" and m.tipo == "texto" and m.texto == TEXTO_COMPRADOR
    assert m.enviada_em is not None and m.enviada_em.year == 2025
    # Campo que o leitor não conhece: só o NOME.
    assert leitura.desconhecidos["conversa.campoNovoDaTemu"] == 1


def test_sync_separa_comprador_loja_robo_e_sistema():
    evento = _http(
        "/api/plateau/sync/message",
        {
            "syncData": [
                {
                    "seqType": 1,
                    "seqId": 12,
                    "hasMore": False,
                    "data": [
                        {"message": _msg("2001", "conv-2", de=1, para=2)},
                        {"message": _msg("2002", "conv-2", de=2, para=1, content="Já enviamos!")},
                        {
                            "message": _msg(
                                "2003",
                                "conv-2",
                                de=2,
                                para=1,
                                content="Sou o assistente",
                                context={"robot": 1},
                            )
                        },
                        {
                            "message": _msg(
                                "2004", "conv-2", de=0, para=0, tipo=31, content="Pedido entregue"
                            )
                        },
                    ],
                },
                {"seqType": 10, "seqId": 3, "data": [{"conv_id": "conv-2", "state_type": 2}]},
                {"seqType": 103, "seqId": 5, "data": [{"conv_id": "conv-2", "state_type": 102}]},
            ]
        },
    )
    leitura = robo_temu.ler([evento])
    msgs = leitura.conversas["conv-2"].mensagens
    assert msgs["2001"].autor == "cliente"
    assert msgs["2002"].autor == "loja" and not msgs["2002"].robo_plataforma
    assert msgs["2003"].autor == "sistema" and msgs["2003"].robo_plataforma
    assert msgs["2004"].autor == "sistema"
    assert leitura.reconhecidos["recibo_leitura"] == 1
    assert leitura.reconhecidos["estado_conversa"] == 1


def _quadro_titan(payload: dict, *, comprimir: bool = False) -> bytes:
    """Cabeçalho de 16 bytes + um protobuf de mentira com o JSON num campo."""
    corpo_json = json.dumps(payload).encode()
    if comprimir:
        corpo_json = gzip.compress(corpo_json)
    proto = b"\x0a\x13titan.notifyDataLite\x12" + bytes([0x80 | 5, 1]) + corpo_json + b"\x18\x01"
    cabecalho = struct.pack(">hhiii", 10, 102, 101, 1, len(proto))
    return cabecalho + proto


def _push(msg: dict) -> dict:
    return {
        "actionId": 100260003,
        "subType": 1,
        "payload": {
            "push_type": 2,
            "target_id": "t",
            "push_data": {
                "seq_type": 1,
                "base_seq_id": 10,
                "seq_id": 11,
                "data": [{"message": msg}],
            },
        },
    }


def test_titan_binario_em_base64_com_e_sem_gzip():
    for comprimir in (False, True):
        quadro = _quadro_titan(_push(_msg("3001", "conv-3", de=1, para=2)), comprimir=comprimir)
        evento = Evento(
            tipo="ws",
            url="wss://br.seller.temu.com?ws-titan-request-sign=dee0ea73",
            corpo=base64.b64encode(quadro).decode(),
        )
        leitura = robo_temu.ler([evento])
        assert leitura.usados == 1, comprimir
        assert leitura.conversas["conv-3"].mensagens["3001"].autor == "cliente"


def test_titan_ja_decodificado_e_evento_de_prazo():
    ws_json = Evento(tipo="ws", corpo=json.dumps(_push(_msg("3101", "conv-4", de=1, para=2))))
    prazo = Evento(
        tipo="ws",
        corpo=json.dumps(
            {
                "actionId": 100260003,
                "payload": json.dumps(
                    {
                        "push_type": 3,
                        "push_data": {
                            "type": 101,
                            "data": {"convId": "conv-4", "deadlineTime": 1759236400},
                        },
                    }
                ),
            }
        ),
    )
    outra_acao = Evento(tipo="ws", corpo=json.dumps({"actionId": 555, "payload": {}}))
    batimento = Evento(
        tipo="ws", corpo=base64.b64encode(struct.pack(">hhiii", 0, 0, 7, 1, 0)).decode()
    )
    leitura = robo_temu.ler([ws_json, prazo, outra_acao, batimento])
    assert leitura.usados == 2
    assert leitura.controle == 2
    assert leitura.ignorados == 0
    c = leitura.conversas["conv-4"]
    assert "3101" in c.mensagens
    assert c.dados["prazo_plataforma"].startswith("2025-09-30")


def test_cartao_de_pedido_imagem_e_edicao():
    evento = _http(
        "/api/plateau/message/getHistoryMsg",
        {
            "hasMore": False,
            "data": [
                {
                    "message": _msg(
                        "4001",
                        "conv-5",
                        de=1,
                        para=2,
                        tipo=64,
                        content="",
                        info={
                            "key": "mall-parent-order-from-consumer",
                            "data": {
                                "parent_order_sn": "PO-211-000",
                                "goods_info": {"thumb_url": "x"},
                            },
                        },
                    ),
                    "sourceUserInfo": {"name": "atendente"},
                },
                {
                    "message": _msg(
                        "4002",
                        "conv-5",
                        de=1,
                        para=2,
                        tipo=1,
                        content="",
                        info={"image_url": "https://img.temu/foto.jpg"},
                    )
                },
                {
                    "message": _msg(
                        "4003", "conv-5", de=1, para=2, tipo=1002, info={"msg_id": "4001"}
                    )
                },
            ],
        },
    )
    leitura = robo_temu.ler([evento])
    c = leitura.conversas["conv-5"]
    assert c.pedido == "PO-211-000"
    assert c.mensagens["4001"].tipo == "pedido"
    assert c.mensagens["4001"].anexos[0]["pedido"] == "PO-211-000"
    assert c.mensagens["4002"].tipo == "imagem"
    assert c.mensagens["4002"].anexos == [{"tipo": "imagem", "url": "https://img.temu/foto.jpg"}]
    # Edição/recolhimento: conta, não grava.
    assert "4003" not in c.mensagens
    assert leitura.reconhecidos["edicao"] == 1


def test_contadores_erro_e_desconhecido_sem_vazar_valor():
    precisa = _http("/api/plateau/conv/needReplyCount", {"needReplyCount": 7})
    captcha = Evento(
        tipo="http",
        url=f"{HOST}/api/plateau/conv/getConvList",
        corpo=json.dumps(
            {
                "success": False,
                "errorCode": 54001,
                "errorMsg": TEXTO_COMPRADOR,
                "verify_auth_token": "x",
            }
        ),
    )
    rota_nova = _http("/api/plateau/conv/rotaQueNaoExiste123", {"data": [TEXTO_COMPRADOR]})
    lixo = Evento(tipo="ws", corpo=TEXTO_COMPRADOR)
    autor_estranho = _http(
        "/api/plateau/sync/message",
        {"syncData": [{"seqType": 1, "data": [{"message": _msg("5001", "conv-6", de=9, para=9)}]}]},
    )
    quebrado = Evento(tipo="http", url=f"{HOST}/api/plateau/sync/message", corpo="{nao é json")
    leitura = robo_temu.ler([precisa, captcha, rota_nova, lixo, autor_estranho, quebrado])
    assert leitura.sinais["precisa_responder"] == 7
    assert leitura.sinais["erro_plataforma"] == "54001"
    assert leitura.ignorados == 3
    # Autor que não se sabe: sistema (não tira da fila, não inventa pergunta).
    assert leitura.conversas["conv-6"].mensagens["5001"].autor == "sistema"
    assert leitura.desconhecidos["mensagem.from.user_type=9"] == 1
    assert "http:/api/plateau/conv/rotaquenaoexiste#" in leitura.desconhecidos
    # Nada do texto do comprador nos nomes contados nem nos sinais.
    tudo = json.dumps([dict(leitura.desconhecidos), leitura.sinais, dict(leitura.reconhecidos)])
    assert "rua secreta" not in tudo and "PO-211" not in tudo


def test_formatos_que_o_robo_manda_binario_json_e_texto():
    """Como o robô do Mac mini manda (scripts/atendimento_robo_adspower.py): `metodo`
    "binario" = quadro em base64 sem prefixo; "json" = o payload que a PRÓPRIA
    página decodificou (tem "push_data"); "texto" = quadro de texto como veio."""
    push = _push(_msg("6001", "conv-7", de=1, para=2))
    binario = Evento(
        tipo="ws",
        url="wss://br.seller.temu.com/",
        metodo="binario",
        corpo=base64.b64encode(_quadro_titan(push)).decode(),
    )
    so_payload = Evento(
        tipo="ws",
        url="wss://br.seller.temu.com/",
        metodo="json",
        corpo=json.dumps(
            {
                **push["payload"],
                "push_data": {
                    **push["payload"]["push_data"],
                    "data": [{"message": _msg("6002", "conv-7", de=2, para=1, content="Olá!")}],
                },
            }
        ),
    )
    # "AAAA" em texto não é base64 disfarçado: não se garimpa.
    texto = Evento(tipo="ws", url="wss://br.seller.temu.com/", metodo="texto", corpo="AAAAAAAA")
    leitura = robo_temu.ler([binario, so_payload, texto])
    msgs = leitura.conversas["conv-7"].mensagens
    assert msgs["6001"].autor == "cliente" and msgs["6002"].autor == "loja"
    assert (leitura.usados, leitura.ignorados) == (2, 1)
    assert leitura.desconhecidos["ws.texto_nao_json"] == 1


def test_lista_vazia_x_formato_desconhecido():
    """Loja sem conversa (data: [] ou null) é CONTROLE e não registra nada; lista
    num campo que o leitor não conhece vira nome de chave contado — senão
    "0 conversas" esconderia a Temu mudando o formato."""
    vazia = _http("/api/plateau/conv/getConvList", {"data": [], "total": 0})
    nula = _http("/api/plateau/conv/getConvList", {"data": None})
    outra = _http("/api/plateau/conv/getConvList", {"convs": [TEXTO_COMPRADOR], "total": 1})
    leitura = robo_temu.ler([vazia, nula])
    assert leitura.ignorados == 0 and not leitura.desconhecidos and not leitura.conversas
    leitura = robo_temu.ler([outra])
    assert leitura.ignorados == 1
    assert dict(leitura.desconhecidos) == {"conv_lista.chaves=convs,total": 1}


def test_ultima_do_robo_da_temu_sem_resposta_ganha_marcador_da_pergunta():
    """Revisão 30/09: a lista só traz a ÚLTIMA mensagem. Se ela é do robô da
    Temu e a Temu diz "sem resposta" (unReply), existe uma pergunta do
    comprador que o robô não viu: entra um MARCADOR do comprador (prévia sem
    texto de comprador), para a conversa não sumir da fila."""
    robo_da_temu = _msg(
        "2001",
        "conv-2",
        de=2,
        para=1,
        context={"robot": 1},
        content="Sou o assistente",
        ts="1759150060",
    )
    base = {"convId": "conv-2", "convInfo": {"name": "Ana"}, "message": robo_da_temu}

    def _lista(**extra) -> Evento:
        return _http("/api/plateau/conv/getConvList", {"data": [{**base, **extra}]})

    sem_resposta = _lista(stateData={"groupList": ["unReply", "robotReplied"]})
    msgs = robo_temu.ler([sem_resposta]).conversas["conv-2"].mensagens
    [marcador] = [m for m in msgs.values() if m.previa]
    assert marcador.autor == "cliente" and marcador.payload["nao_vista"] is True
    assert marcador.externo_id.startswith("previa:conv-2:")
    assert "Seller Center" in marcador.texto
    # Sem a hora da pergunta, logo antes da resposta do robô.
    assert marcador.enviada_em < msgs["2001"].enviada_em
    # Com o `lastUnReplyTime`, a hora dele; relida, o mesmo id.
    com_hora = _lista(
        stateData={"groupList": ["unReply"]}, countDownInfo={"lastUnReplyTime": 1759150000}
    )
    [m1] = [m for m in robo_temu.ler([com_hora]).conversas["conv-2"].mensagens.values() if m.previa]
    [m2] = [m for m in robo_temu.ler([com_hora]).conversas["conv-2"].mensagens.values() if m.previa]
    assert m1.enviada_em.timestamp() == 1759150000 and m1.externo_id == m2.externo_id
    # Respondida (sem unReply): nada de marcador.
    respondida = _lista(stateData={"groupList": ["replied", "robotReplied"]})
    assert list(robo_temu.ler([respondida]).conversas["conv-2"].mensagens) == ["2001"]
    # Última do comprador: a própria pergunta está ali, nada de marcador.
    assert list(robo_temu.ler([_conv_list()]).conversas["conv-1"].mensagens) == ["1001"]
