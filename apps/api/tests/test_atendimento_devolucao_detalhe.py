"""A devolução do TikTok inteira na aba Pedido e a resposta da loja (18/11/2026).

O caso medido ao vivo em 18/11/2026 (Poofy, pedido 586887215793276360, reembolso
4042968144158361032), com os mesmos valores, datas e rastreio — o texto e os links
do comprador trocados por falsos. O que se mede: o bloco igual ao do Duoke
(montante, a volta, o produto com o preço cheio, o histórico com o texto, as fotos
e o vídeo), o reembolso rápido (pago com o caso aberto pelo pacote), a leitura na
hora com cache curto e sem gravar nada do comprador no banco; as respostas só as
que o TikTok espera AGORA (relido antes), a recusa do pacote do reembolso rápido
travada, a chave de idempotência, a nota interna com quem fez, o interruptor e a
trava de quem responde nas rotas.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoReclamacao,
    Integration,
    IntegrationPlatform,
    UserRole,
)
from app.routers import atendimento as rota_atendimento
from app.security.cipher import encrypt_json
from app.services.atendimento import devolucao_detalhe as dd
from app.services.atendimento import reclamacoes
from app.services.marketplaces.tiktok import TikTokClient

PEDIDO = "586887215793276360"
CASO = "4042968144158361032"
AGORA = datetime(2026, 11, 18, 17, 50, tzinfo=UTC)
PRAZO = 1795092912  # 19/11/2026 12:55:12 UTC
TEXTO_DO_COMPRADOR = "TEXTO FALSO DO COMPRADOR — não pode ir para o banco"
AUTO = "Aprovado automaticamente pela TikTok Shop de acordo com a política aplicável"


def _caso(**o: Any) -> dict:
    caso = {
        "return_id": CASO,
        "order_id": PEDIDO,
        "return_status": "BUYER_SHIPPED_ITEM",
        "return_type": "RETURN_AND_REFUND",
        "return_reason_text": "O item chegou danificado. Exemplo: o item está amassado.",
        "is_quick_refund": True,
        "create_time": 1794012645,
        "update_time": 1794920112,
        "refund_amount": {
            "currency": "BRL",
            "refund_shipping_fee": "8.61",
            "refund_subtotal": "122.96",
            "refund_tax": "0",
            "refund_total": "131.57",
        },
        "discount_amount": [
            {
                "currency": "BRL",
                "product_platform_discount": "30.74",
                "product_seller_discount": "259.71",
            }
        ],
        "return_line_items": [
            {
                "order_line_item_id": "1",
                "sku_id": "1737340448984761375",
                "seller_sku": "b033.24",
                "sku_name": "M3 Coral",
                "product_name": "Mala de Viagem Despacho 24 Polegadas",
                "product_image": {"url": "https://cdn.exemplo.test/mala.jpeg"},
                "refund_amount": {"currency": "BRL", "refund_subtotal": "122.96"},
            }
        ],
        "return_tracking_number": "632881002",
        "return_provider_name": "Jadlog BR 4PL",
        "shipping_fee_amount": [{"currency": "BRL", "seller_paid_return_shipping_fee": "32.56"}],
        "seller_next_action_response": [
            {"action": "SELLER_RESPOND_RECEIVE_PACKAGE", "deadline": PRAZO}
        ],
    }
    caso.update(o)
    return caso


REGISTROS = [
    # Fora de ordem de propósito: a tela mostra do mais antigo ao mais novo.
    {
        "create_time": 1794070532,
        "description": "Reembolso concluído",
        "event": "REFUND_SUCCESS",
        "role": "SYSTEM",
    },
    {
        "create_time": 1794012644,
        "description": "Solicitação de devolução enviada pelo comprador",
        "event": "ORDER_RETURN",
        "role": "BUYER",
        "reason_text": "O item chegou danificado.",
        "note": TEXTO_DO_COMPRADOR,
        "images": [{"url": f"https://cdn.exemplo.test/f{i}.jpeg"} for i in range(5)]
        + [{"url": "http://inseguro.test/x.jpeg"}],
        "videos": [
            {
                "url": "https://cdn.exemplo.test/v.mp4",
                "cover": "https://cdn.exemplo.test/capa.jpeg",
                "duration_millis": 21834,
            }
        ],
    },
    {
        "create_time": 1794012645,
        "description": AUTO,
        "event": "SELLER_AGGREE_RETURN",
        "role": "SELLER",
    },
    {
        "create_time": 1794070513,
        "description": "O comprador enviou o pacote de devolução ao vendedor",
        "event": "BUYER_SHIPPED",
        "role": "BUYER",
    },
]


class TikTokFalso:
    """Só o que o serviço usa; guarda cada chamada."""

    def __init__(self, caso: dict | None = None) -> None:
        self.caso = caso if caso is not None else _caso()
        self.registros = REGISTROS
        self.chamadas: list[tuple] = []
        self.falhar_registros = False
        self.erro_post: Exception | None = None

    async def get_return_list(self, *, order_ids=None, **_):
        self.chamadas.append(("search", tuple(order_ids or ())))
        return [self.caso] if self.caso else []

    async def get_return_records_strict(self, return_id):
        self.chamadas.append(("records", return_id))
        if self.falhar_registros:
            raise RuntimeError("tiktok_return_records code=36009004 msg=fora do ar")
        return self.registros

    async def get_reject_reasons(self, return_id):
        self.chamadas.append(("reasons", return_id))
        return [
            {"name": "reverse_reject_return_parcel_reason_1", "text": "O produto voltou danificado"}
        ]

    async def upload_image(self, nome, dados, tipo):
        self.chamadas.append(("upload", nome, tipo, len(dados)))
        return {"uri": f"tos-br/{nome}", "width": 800, "height": 600}

    async def approve_return(self, return_id, *, decision, idempotency_key=None):
        self.chamadas.append(("approve", return_id, decision, idempotency_key))
        if self.erro_post:
            raise self.erro_post
        return {}

    async def reject_return(self, return_id, **kw):
        self.chamadas.append(("reject", return_id, kw))
        if self.erro_post:
            raise self.erro_post
        return {}


class RedisFalso:
    def __init__(self) -> None:
        self.dados: dict[str, Any] = {}

    async def get(self, chave):
        return self.dados.get(chave)

    async def set(self, chave, valor, *, ex=None, nx=False):
        self.dados[chave] = valor
        return True

    async def delete(self, chave):
        self.dados.pop(chave, None)


@pytest.fixture
def redis_falso(monkeypatch):
    r = RedisFalso()
    monkeypatch.setattr(dd, "redis", r)
    return r


def _fabrica(falso: TikTokFalso):
    async def fabrica(_integ):
        return falso

    return fabrica


async def _cenario(db, make_user) -> tuple[Integration, AtendimentoConversa, AtendimentoReclamacao]:
    dono = await make_user()
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.TIKTOK,
        name="poofy",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    conversa = AtendimentoConversa(
        integration_id=integ.id,
        plataforma="tiktok",
        canal="reclamacao",
        externo_id=CASO,
        pedido_marketplace=PEDIDO,
        situacao="bloqueada",
    )
    db.add(conversa)
    await db.flush()
    linha = AtendimentoReclamacao(
        integration_id=integ.id,
        conversa_id=conversa.id,
        plataforma="tiktok",
        externo_id=CASO,
        tipo="devolucao",
        status="BUYER_SHIPPED_ITEM",
        motivo="O item chegou danificado. Exemplo: o item está amassado.",
        pedido_marketplace=PEDIDO,
        prazo_em=datetime.fromtimestamp(PRAZO, UTC),
        aberta_em=datetime(2026, 11, 7, 0, 50, 45, tzinfo=UTC),
        dados={"fonte": "logistica", "conta": "poofy", "return_id": CASO},
    )
    db.add(linha)
    await db.commit()
    return integ, conversa, linha


# ─────────────── o bloco (puro) ───────────────


def test_o_bloco_igual_ao_do_duoke():
    d = dd.detalhe(_caso(), REGISTROS, AGORA)
    assert d["status_rotulo"] == "Comprador enviou o produto"
    assert d["solucao"] == "Devolução + reembolso"
    assert d["reembolso"] == {"total": 131.57, "subtotal": 122.96, "frete": 8.61, "moeda": "BRL"}
    assert d["volta"] == {
        "rastreio": "632881002",
        "transportadora": "Jadlog BR 4PL",
        "frete_loja": 32.56,
        "moeda": "BRL",
    }
    (item,) = d["itens"]
    assert (item["variacao"], item["sku"], item["quantidade"]) == ("M3 Coral", "b033.24", 1)
    # O preço riscado do Duoke: o subtotal + os descontos da plataforma e da loja.
    assert (item["preco"], item["preco_original"]) == (122.96, 413.41)
    # O reembolso rápido: pago às 13:55 de 07/11, com o caso aberto pelo pacote.
    assert d["reembolso_rapido"] is True
    assert d["reembolso_pago_em"] == "2026-11-07T16:55:32+00:00"
    assert d["acao_pendente"] == "Confirmar ou recusar o pacote recebido no TikTok"
    assert d["prazo"] == "2026-11-19T12:55:12+00:00"
    # O histórico, do mais antigo ao mais novo, com as horas do Duoke.
    titulos = [(e["quando"], e["titulo"]) for e in d["historico"]]
    assert titulos == [
        ("2026-11-07T00:50:44+00:00", "Solicitação de devolução enviada pelo comprador"),
        (
            "2026-11-07T00:50:45+00:00",
            "Aprovado automaticamente pela TikTok Shop de acordo com a política aplicável",
        ),
        ("2026-11-07T16:55:13+00:00", "O comprador enviou o pacote de devolução ao vendedor"),
        ("2026-11-07T16:55:32+00:00", "Reembolso concluído"),
    ]
    abertura = d["historico"][0]
    assert abertura["texto"] == TEXTO_DO_COMPRADOR and abertura["quem"] == "Comprador"
    assert len(abertura["fotos"]) == 5, "só https vira foto"
    assert abertura["videos"] == [
        {
            "url": "https://cdn.exemplo.test/v.mp4",
            "capa": "https://cdn.exemplo.test/capa.jpeg",
            "duracao_s": 22,
        }
    ]


def test_as_respostas_sao_as_que_o_tiktok_espera_agora():
    acoes = {a["codigo"]: a for a in dd.acoes_do_caso(_caso(), AGORA)}
    assert set(acoes) == {"confirmar_pacote", "recusar_pacote"}
    assert "R$ 131,57" in acoes["confirmar_pacote"]["efeito"]
    assert acoes["confirmar_pacote"]["bloqueio"] is None
    # Reembolso rápido: o TikTok não deixa recusar o pacote pela API (25011025).
    assert "reembolso rápido" in acoes["recusar_pacote"]["bloqueio"]
    assert acoes["recusar_pacote"]["exige_foto"] is True

    lento = {a["codigo"]: a for a in dd.acoes_do_caso(_caso(is_quick_refund=False), AGORA)}
    assert lento["recusar_pacote"]["bloqueio"] is None

    pedido = _caso(
        return_status="RETURN_OR_REFUND_REQUEST_PENDING",
        seller_next_action_response=[{"action": "SELLER_RESPOND_RETURN", "deadline": PRAZO}],
    )
    assert [a["codigo"] for a in dd.acoes_do_caso(pedido, AGORA)] == [
        "aprovar_devolucao",
        "recusar_devolucao",
    ]
    so_reembolso = _caso(
        return_type="REFUND",
        seller_next_action_response=[{"action": "SELLER_RESPOND_REFUND", "deadline": PRAZO}],
    )
    assert [a["codigo"] for a in dd.acoes_do_caso(so_reembolso, AGORA)] == [
        "aprovar_reembolso",
        "recusar_reembolso",
    ]
    # Prazo vencido, arbitragem ou nada pendente: nenhuma.
    assert dd.acoes_do_caso(_caso(), datetime.fromtimestamp(PRAZO + 1, UTC)) == []
    assert dd.acoes_do_caso(_caso(arbitration_status="IN_PROGRESS"), AGORA) == []
    assert dd.acoes_do_caso(_caso(seller_next_action_response=[]), AGORA) == []
    assert dd.DECISAO["confirmar_pacote"] == "APPROVE_RECEIVED_PACKAGE"
    assert dd.DECISAO["aprovar_devolucao"] == "APPROVE_RETURN"
    assert dd.DECISAO["recusar_pacote"] == "REJECT_RECEIVED_PACKAGE"


def test_preco_cheio_so_com_uma_unidade_e_erros_legiveis():
    dois = _caso(return_line_items=_caso()["return_line_items"] * 2)
    (item,) = dd.itens_do_caso(dois)
    assert item["quantidade"] == 2 and item["preco_original"] is None
    codigo, frase = dd.erro_legivel(
        RuntimeError("tiktok_reject_return code=25011025 msg=Quick refund forbid reject receive.")
    )
    assert codigo == "25011025" and "reembolso rápido" in frase
    codigo, frase = dd.erro_legivel(RuntimeError("tiktok_approve_return code=99999 msg=coisa nova"))
    assert codigo == "99999" and frase == "O TikTok recusou: coisa nova"
    a = dd.chave_de_idempotencia("r", "confirmar_pacote", _caso())
    assert a == dd.chave_de_idempotencia("r", "confirmar_pacote", _caso()), "o clique repetido"
    assert a != dd.chave_de_idempotencia("r", "confirmar_pacote", _caso(update_time=1))
    assert dd.chave_de_idempotencia("r", "recusar_pacote", _caso(), motivo="x", comentario="a") != (
        dd.chave_de_idempotencia("r", "recusar_pacote", _caso(), motivo="x", comentario="b")
    )


def test_conferir_o_pedido_antes_de_ir_ao_tiktok():
    foto = dd.FotoDaRecusa("dano.jpg", b"x" * 10, "image/jpeg")
    assert dd.conferir_pedido("confirmar_pacote", motivo="lixo", comentario="lixo", fotos=[]) == (
        None,
        None,
    )
    with pytest.raises(dd.AcaoRecusada) as e:
        dd.conferir_pedido(
            "recusar_pacote", motivo="m", comentario="  riscado  na  lateral ", fotos=[]
        )
    assert e.value.code == "foto_obrigatoria"
    assert dd.conferir_pedido(
        "recusar_pacote", motivo="m", comentario="  riscado  na  lateral ", fotos=[foto]
    ) == ("m", "riscado na lateral")
    for kw, code in (
        ({"motivo": "", "comentario": "riscado"}, "motivo_obrigatorio"),
        ({"motivo": "m", "comentario": "oi"}, "comentario_obrigatorio"),
        ({"motivo": "m", "comentario": "ã" * 251}, "comentario_longo"),
    ):
        with pytest.raises(dd.AcaoRecusada) as e:
            dd.conferir_pedido("recusar_devolucao", fotos=[], **kw)
        assert e.value.code == code
    with pytest.raises(dd.AcaoRecusada) as e:
        dd.conferir_pedido("apagar_tudo", motivo=None, comentario=None, fotos=[])
    assert e.value.code == "acao_invalida"


async def test_approve_return_do_cliente_manda_a_decisao_e_a_chave():
    cliente = TikTokClient({"access_token": "t", "shop_cipher": "c"})
    enviados = []

    async def _post(path, body=None, extra=None, **_):
        enviados.append((path, body, extra))
        return {"code": 0, "data": {}}

    cliente._post = _post
    await cliente.approve_return(CASO, decision="APPROVE_RECEIVED_PACKAGE", idempotency_key="k1")
    assert enviados == [
        (
            f"/return_refund/202309/returns/{CASO}/approve",
            {"decision": "APPROVE_RECEIVED_PACKAGE"},
            {"idempotency_key": "k1"},
        )
    ]

    async def _recusa(path, body=None, extra=None, **_):
        return {"code": 25011013, "message": "reverse status cannot approve receive"}

    cliente._post = _recusa
    with pytest.raises(RuntimeError, match="code=25011013"):
        await cliente.approve_return(CASO, decision="APPROVE_RECEIVED_PACKAGE")


# ─────────────── a leitura na hora ───────────────


async def test_leitura_na_hora_com_cache_e_nada_do_comprador_no_banco(db, make_user, redis_falso):
    _, _, linha = await _cenario(db, make_user)
    falso = TikTokFalso()
    d = await dd.ler_detalhe(db, linha, agora=AGORA, fabrica_cliente=_fabrica(falso))
    assert d["ao_vivo"] is True and d["erro"] is None
    assert d["reembolso"]["total"] == 131.57 and len(d["historico"]) == 4
    assert falso.chamadas == [("search", (PEDIDO,)), ("records", CASO)]
    # Segunda vez: do cache (5 min), sem ir ao TikTok.
    d2 = await dd.ler_detalhe(db, linha, agora=AGORA, fabrica_cliente=_fabrica(falso))
    assert d2["historico"] == d["historico"] and len(falso.chamadas) == 2
    # `forcar` relê.
    await dd.ler_detalhe(db, linha, agora=AGORA, forcar=True, fabrica_cliente=_fabrica(falso))
    assert len(falso.chamadas) == 4
    # Nada do comprador foi para o banco (a regra de 02/10/2026 continua).
    await db.refresh(linha)
    assert TEXTO_DO_COMPRADOR not in json.dumps(linha.dados, ensure_ascii=False)
    assert "cdn.exemplo.test" not in json.dumps(linha.dados)


async def test_tiktok_fora_do_ar_nao_derruba_a_tela(db, make_user, redis_falso):
    _, _, linha = await _cenario(db, make_user)
    falso = TikTokFalso()
    falso.falhar_registros = True
    d = await dd.ler_detalhe(db, linha, agora=AGORA, fabrica_cliente=_fabrica(falso))
    assert (d["ao_vivo"], d["erro"]) == (False, "tiktok_indisponivel")
    assert redis_falso.dados == {}, "a falha não vai para o cache"
    sem_caso = TikTokFalso(caso={})
    d = await dd.ler_detalhe(
        db, linha, agora=AGORA, forcar=True, fabrica_cliente=_fabrica(sem_caso)
    )
    assert d["erro"] == "caso_nao_achado"
    # Sem o nº do caso (a Logística ainda não tem), não há o que ler.
    sem_id = AtendimentoReclamacao(
        plataforma="tiktok", externo_id=f"{reclamacoes.PREFIXO_SEM_ID}{PEDIDO}", tipo="devolucao"
    )
    assert (await dd.ler_detalhe(db, sem_id, agora=AGORA))["erro"] == "sem_detalhe"


# ─────────────── a resposta da loja ───────────────


async def test_resposta_desligada_nao_sai(db, make_user, redis_falso, monkeypatch):
    _, conversa, linha = await _cenario(db, make_user)
    monkeypatch.setattr(get_settings(), "atendimento_devolucao_acoes_ativa", False)
    falso = TikTokFalso()
    with pytest.raises(dd.AcaoRecusada) as e:
        await dd.responder(
            db,
            linha,
            conversa,
            "confirmar_pacote",
            user_id=None,
            quem="Ana",
            agora=AGORA,
            fabrica_cliente=_fabrica(falso),
        )
    assert e.value.code == "devolucao_acoes_desligadas"
    assert falso.chamadas == []


async def test_confirmar_o_pacote_relido_com_chave_e_nota(db, make_user, redis_falso, monkeypatch):
    _, conversa, linha = await _cenario(db, make_user)
    pessoa = await make_user()
    monkeypatch.setattr(get_settings(), "atendimento_devolucao_acoes_ativa", True)
    falso = TikTokFalso()
    d = await dd.responder(
        db,
        linha,
        conversa,
        "confirmar_pacote",
        user_id=pessoa.id,
        quem="Ana",
        agora=AGORA,
        fabrica_cliente=_fabrica(falso),
    )
    await db.commit()
    tipos = [c[0] for c in falso.chamadas]
    assert tipos[:2] == ["search", "approve"], "relê o caso ANTES de mandar"
    _, rid, decisao, chave = falso.chamadas[1]
    assert (rid, decisao) == (CASO, "APPROVE_RECEIVED_PACKAGE")
    assert chave == dd.chave_de_idempotencia(linha.id, "confirmar_pacote", _caso())
    assert d["ao_vivo"] is True, "o bloco volta relido"
    nota = (
        await db.execute(
            select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa.id)
        )
    ).scalar_one()
    assert nota.tipo == "nota" and nota.autor_user_id == pessoa.id
    assert (
        "Confirmar o pacote recebido" in nota.texto and CASO in nota.texto and "Ana" in nota.texto
    )


async def test_caso_mudou_ou_recusa_travada_nada_sai(db, make_user, redis_falso, monkeypatch):
    _, conversa, linha = await _cenario(db, make_user)
    monkeypatch.setattr(get_settings(), "atendimento_devolucao_acoes_ativa", True)
    # O comprador já foi atendido: o TikTok não espera mais o pacote.
    mudou = TikTokFalso(
        caso=_caso(
            return_status="RETURN_OR_REFUND_REQUEST_COMPLETE", seller_next_action_response=[]
        )
    )
    with pytest.raises(dd.AcaoRecusada) as e:
        await dd.responder(
            db,
            linha,
            conversa,
            "confirmar_pacote",
            user_id=None,
            quem="Ana",
            agora=AGORA,
            fabrica_cliente=_fabrica(mudou),
        )
    assert e.value.code == "acao_indisponivel"
    assert [c[0] for c in mudou.chamadas] == ["search"]
    # Reembolso rápido: a recusa do pacote é travada antes de subir foto.
    rapido = TikTokFalso()
    foto = dd.FotoDaRecusa("dano.jpg", b"x" * 10, "image/jpeg")
    with pytest.raises(dd.AcaoRecusada) as e:
        await dd.responder(
            db,
            linha,
            conversa,
            "recusar_pacote",
            user_id=None,
            quem="Ana",
            motivo="reverse_reject_return_parcel_reason_1",
            comentario="Voltou riscado.",
            fotos=[foto],
            agora=AGORA,
            fabrica_cliente=_fabrica(rapido),
        )
    assert e.value.code == "acao_travada"
    assert [c[0] for c in rapido.chamadas] == ["search"]


async def test_recusar_o_pacote_com_motivo_comentario_e_fotos(
    db, make_user, redis_falso, monkeypatch
):
    _, conversa, linha = await _cenario(db, make_user)
    monkeypatch.setattr(get_settings(), "atendimento_devolucao_acoes_ativa", True)
    falso = TikTokFalso(caso=_caso(is_quick_refund=False))
    fotos = [dd.FotoDaRecusa(f"dano{i}.jpg", b"x" * 10, "image/jpeg") for i in range(2)]
    # Motivo que o TikTok não deu para o caso: recusa sem subir nada.
    with pytest.raises(dd.AcaoRecusada) as e:
        await dd.responder(
            db,
            linha,
            conversa,
            "recusar_pacote",
            user_id=None,
            quem="Ana",
            motivo="inventado",
            comentario="Voltou riscado.",
            fotos=fotos,
            agora=AGORA,
            fabrica_cliente=_fabrica(falso),
        )
    assert e.value.code == "motivo_invalido"
    assert "upload" not in [c[0] for c in falso.chamadas]

    falso.chamadas.clear()
    await dd.responder(
        db,
        linha,
        conversa,
        "recusar_pacote",
        user_id=None,
        quem="Ana",
        motivo="reverse_reject_return_parcel_reason_1",
        comentario="Voltou   riscado.",
        fotos=fotos,
        agora=AGORA,
        fabrica_cliente=_fabrica(falso),
    )
    assert [c[0] for c in falso.chamadas][:5] == ["search", "reasons", "upload", "upload", "reject"]
    _, rid, kw = falso.chamadas[4]
    assert rid == CASO
    assert kw["decision"] == "REJECT_RECEIVED_PACKAGE"
    assert kw["reject_reason"] == "reverse_reject_return_parcel_reason_1"
    assert kw["comment"] == "Voltou riscado."
    assert kw["images"] == [
        {"image_id": f"tos-br/dano{i}.jpg", "mime_type": "image/jpeg", "width": 800, "height": 600}
        for i in range(2)
    ]
    assert kw["idempotency_key"]
    await db.commit()
    nota = (
        await db.execute(
            select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa.id)
        )
    ).scalar_one()
    assert (
        "Motivo: O produto voltou danificado." in nota.texto and "Fotos enviadas: 2." in nota.texto
    )


async def test_tiktok_recusa_ou_nao_responde(db, make_user, redis_falso, monkeypatch):
    _, conversa, linha = await _cenario(db, make_user)
    monkeypatch.setattr(get_settings(), "atendimento_devolucao_acoes_ativa", True)
    falso = TikTokFalso()
    falso.erro_post = RuntimeError(
        "tiktok_approve_return code=25011013 msg=reverse status cannot approve receive"
    )
    with pytest.raises(dd.AcaoRecusada) as e:
        await dd.responder(
            db,
            linha,
            conversa,
            "confirmar_pacote",
            user_id=None,
            quem="Ana",
            agora=AGORA,
            fabrica_cliente=_fabrica(falso),
        )
    assert e.value.code == "tiktok_25011013" and "confirmação do pacote" in e.value.detail
    falso.erro_post = TimeoutError()
    with pytest.raises(dd.AcaoRecusada) as e:
        await dd.responder(
            db,
            linha,
            conversa,
            "confirmar_pacote",
            user_id=None,
            quem="Ana",
            agora=AGORA,
            fabrica_cliente=_fabrica(falso),
        )
    assert (e.value.code, e.value.http) == ("resposta_incerta", 502)
    # As duas tentativas usaram a MESMA chave: a nova tentativa é segura.
    chaves = {c[3] for c in falso.chamadas if c[0] == "approve"}
    assert len(chaves) == 1


# ─────────────── as rotas ───────────────


async def test_rotas_do_detalhe_e_da_resposta(
    client, db, make_user, auth_as, redis_falso, monkeypatch
):
    _, conversa, linha = await _cenario(db, make_user)
    falso = TikTokFalso()

    async def fabrica(_integ):
        return falso

    monkeypatch.setattr(dd, "_cliente", lambda _s, _r, _f: fabrica(None))
    base = f"/api/atendimento/conversas/{conversa.id}/reclamacoes/{linha.id}"

    # O cartão marca o bloco completo (TikTok, do pedido da conversa).
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"/api/atendimento/conversas/{conversa.id}/reclamacoes")
    item = r.json()["itens"][0]
    assert (item["do_pedido"], item["detalhe_plataforma"]) == (True, True)

    r = await client.get(f"{base}/detalhe")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["ao_vivo"] is True and corpo["acoes_ligadas"] is False
    assert corpo["reembolso"]["total"] == 131.57
    assert corpo["historico"][0]["texto"] == TEXTO_DO_COMPRADOR
    assert {a["codigo"] for a in corpo["acoes"]} == {"confirmar_pacote", "recusar_pacote"}

    r = await client.get(f"{base}/motivos-recusa")
    assert r.status_code == 200 and r.json()["itens"][0]["codigo"].startswith("reverse_reject")

    # Desligado: 409 com a frase, nada sai.
    r = await client.post(f"{base}/responder", data={"acao": "confirmar_pacote"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "devolucao_acoes_desligadas"
    assert "approve" not in [c[0] for c in falso.chamadas]

    monkeypatch.setattr(get_settings(), "atendimento_devolucao_acoes_ativa", True)
    r = await client.post(f"{base}/responder", data={"acao": "confirmar_pacote"})
    assert r.status_code == 200, r.text
    assert any(c[0] == "approve" for c in falso.chamadas)

    # Caso de outra conversa: 404.
    outro = "00000000-0000-0000-0000-000000000000"
    r = await client.get(f"/api/atendimento/conversas/{conversa.id}/reclamacoes/{outro}/detalhe")
    assert r.status_code == 404

    # Quem só lê (fora da fase de observação, sem `atendimento.edit`): 403 no POST.
    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    auth_as(await make_user(role=UserRole.USER))
    r = await client.post(f"{base}/responder", data={"acao": "confirmar_pacote"})
    assert r.status_code == 403


def test_a_rota_da_resposta_e_de_quem_responde():
    assert (
        "POST",
        "/api/atendimento/conversas/{conversa_id}/reclamacoes/{reclamacao_id}/responder",
    ) in rota_atendimento.ROTAS_DE_QUEM_RESPONDE


def test_a_conversa_da_devolucao_do_tiktok_aponta_o_bloco():
    from app.services.atendimento import reclamacoes_devolucoes as rd

    assert "aba Pedido" in rd.bloqueio_so_leitura("tiktok")
    assert "só leitura no DaVinci" in rd.bloqueio_so_leitura("shopee"), "a Shopee segue igual"
