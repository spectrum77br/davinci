# ruff: noqa: E501
"""Acompanhamento da devolução TikTok: o que o comprador ESCREVE, o que ele
ANEXA e o caso que ele REABRE.

Vinicius 22/09, caso 293798 / return 4042406038488843708 (loja TikTok Mini):
"na tela do TikTok a mulher escreve, não veio a escrita; a mulher mandou vídeo,
não veio o vídeo; e ela reabriu a reclamação e não está aparecendo aqui". Os
três buracos, medidos no caso real:

  - a API manda até três campos de texto no MESMO evento (`note` livre,
    `description` do evento e `reason_text` do motivo escolhido) e só o primeiro
    preenchido era lido — o que ela digitou sumia atrás do rótulo;
  - `videos[]`/`images[]` vinham no mesmo pacote e eram jogados fora (vai o
    LINK: o arquivo fica na TikTok, ninguém baixa nada);
  - a troca pelo caso novo do mesmo pedido só acontecia quando o anterior
    estava CANCELADO — um caso RECUSADO (ou concluído/reembolsado) não cedia a
    vez, e o chamado continuava olhando um caso morto pra sempre.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services import chamados as chamados_svc
from app.services import chamados_devolucao as svc
from app.services import chamados_devolucao_sync as sync
from tests.test_chamados_devolucao import (
    OID_REEMB,
    RID_REEMB,
    _FakeML,
    _FakeTikTokRecusa,
    _lancar_296936,
    _recebidas,
    _sistema_txts,
)

RID_NOVO = "4042406038488843708"  # o caso que a compradora reabriu (293798)
RID_TARDE = "4042491100000000001"  # outro caso, ainda mais novo
VIDEO = "https://p16-oec.tiktokcdn.com/tos-video-caixa.mp4"
FOTO1 = "https://p16-oec.tiktokcdn.com/tos-foto-1.jpeg"
FOTO2 = "https://p16-oec.tiktokcdn.com/tos-foto-2.jpeg"
ESCREVEU = int(datetime(2026, 9, 19, 13, 2, tzinfo=UTC).timestamp())
ANEXOU = int(datetime(2026, 9, 19, 13, 9, tzinfo=UTC).timestamp())


@pytest.fixture
def ml(monkeypatch):
    fake = _FakeML()

    async def _client(session, conta):
        return fake

    monkeypatch.setattr(svc.chamados_svc, "_ml_client_para", _client)
    monkeypatch.setattr(svc, "ENFILEIRAR", False)  # dispara inline (sem Redis)
    return fake


def _hora(epoch: int) -> str:
    """A hora como a linha do tempo grava (fuso da loja)."""
    return datetime.fromtimestamp(epoch, UTC).astimezone(chamados_svc.SAO_PAULO).strftime("%d/%m %H:%M")


class _TikTokCasos(_FakeTikTokRecusa):
    """Os casos do MESMO pedido sob controle do teste e a linha do tempo POR
    caso — o fake de origem devolve a mesma lista de registros pra qualquer
    return_id, e aqui é justamente a diferença entre um caso e outro que importa."""

    def __init__(self):
        super().__init__()
        self.outros: list[dict] = []
        self.next_return_id = ""
        self.linhas: dict[str, list[dict]] = {}
        self.records_pedidos: list[str] = []

    async def get_return_list(self, *, order_ids=None, **kw):
        casos = await super().get_return_list(order_ids=order_ids, **kw)
        if self.next_return_id:
            casos[0]["next_return_id"] = self.next_return_id
        return casos + [dict(c) for c in self.outros]

    async def get_return_records(self, return_id, *, locale="pt-BR"):
        self.records_pedidos.append(str(return_id))
        return [dict(r) for r in self.linhas.get(str(return_id), [])]


def _caso(rid: str, status: str, *, criado: int) -> dict:
    return {
        "order_id": OID_REEMB, "return_id": rid, "return_type": "REFUND",
        "return_status": status, "create_time": criado, "update_time": criado,
        "refund_amount": {"currency": "BRL", "refund_total": "803.2"},
    }


async def _abrir(client, db, make_user, auth_as, monkeypatch, fake):
    """Lançamento do 296936 (só reembolso, recusado por nós) com o fake de casos.
    A linha do tempo começa limpa: o que a abertura leu não conta."""
    ch = await _lancar_296936(client, db, make_user, auth_as, monkeypatch, fake)
    fake.records_pedidos.clear()
    return ch, datetime.fromtimestamp(fake.update_time, UTC)


# ---------------------------------------------------------------- a escrita


async def test_escrita_do_comprador_vem_inteira_sem_repetir_e_sem_a_nossa(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(a) `note` + `reason_text` diferentes → os dois no histórico; (c)
    `description` igual ao `note` → uma vez só; (d) o que a LOJA escreveu
    continua fora (é a nossa fala, não resposta da plataforma)."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    fake.linhas[RID_REEMB] = [
        {"role": "BUYER", "create_time": ESCREVEU,
         "note": "Recebi uma caixa de sabonete no lugar do celular",
         "reason_text": "Pacote recebido, mas faltam alguns itens"},
        {"role": "BUYER", "create_time": ESCREVEU + 300,
         "note": "Abri o pacote na frente do entregador",
         "description": "abri o pacote na frente do entregador"},  # mesmo texto
        {"role": "SELLER", "create_time": ESCREVEU + 600, "note": "nossa resposta (ignorada)"},
    ]

    s1 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    assert s1["verificados"] == 1 and s1["encerrados"] == 0, s1
    txts = await _recebidas(db, ch.id)

    # (a) o rótulo do motivo NÃO engole mais o que ela digitou
    assert (
        f"Comprador {_hora(ESCREVEU)}: Recebi uma caixa de sabonete no lugar do celular"
        " — Pacote recebido, mas faltam alguns itens"
    ) in txts, txts
    # (c) sem eco: `description` repetindo o `note` não vira "texto — texto"
    repetido = [t for t in txts if "Abri o pacote na frente do entregador" in t]
    assert repetido == [f"Comprador {_hora(ESCREVEU + 300)}: Abri o pacote na frente do entregador"]
    # (d) SELLER fora
    assert not any("ignorada" in t for t in txts)
    # (i) a passada de novo não duplica nada
    s2 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=4))
    assert s2["novos"] == 0 and await _recebidas(db, ch.id) == txts


async def test_video_e_fotos_do_comprador_entram_como_link(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(b) evento SÓ com mídia vira mensagem com os links — o arquivo fica na
    TikTok (decisão do dono 22/09: link, sem baixar nada). Item pode vir como
    dict (`url`/`image_url`) ou string crua, em `videos`/`video_list` e
    `images`/`image_list`, e link repetido entra uma vez só."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    fake.linhas[RID_REEMB] = [
        {"role": "BUYER", "create_time": ANEXOU,
         "videos": [{"url": VIDEO}], "image_list": [{"image_url": FOTO1}, FOTO2, FOTO1]},
        {"role": "BUYER", "create_time": ANEXOU + 120,
         "note": "Olha a foto do que veio", "images": [{"url": FOTO2}]},
    ]

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    txts = await _recebidas(db, ch.id)

    assert f"Comprador {_hora(ANEXOU)}: Anexou 1 vídeo e 2 fotos: {VIDEO} | {FOTO1} | {FOTO2}" in txts, txts
    # texto e anexo no mesmo evento: a escrita primeiro, o link depois
    assert f"Comprador {_hora(ANEXOU + 120)}: Olha a foto do que veio Anexou 1 foto: {FOTO2}" in txts, txts


def test_texto_e_midia_de_um_registro_isolados():
    """As duas funções puras, nos cantos que a API mostra de verdade."""
    assert sync._tt_texto_do_registro(
        {"note": "n", "comment": "c", "description": "d", "reason_text": "r"}
    ) == "n — c — d — r"
    # repetido em caixa diferente = mesmo texto (a TikTok manda o motivo nos dois)
    assert sync._tt_texto_do_registro({"note": "Produto errado", "description": "produto errado"}) == "Produto errado"
    assert sync._tt_texto_do_registro({"note": "  ", "reason_text": " Item faltando "}) == "Item faltando"
    assert sync._tt_texto_do_registro({"event": "ORDER_REFUND"}) == ""

    assert sync._tt_midia_do_registro({}) == ""
    assert sync._tt_midia_do_registro({"images": [{"url": FOTO1}]}) == f"Anexou 1 foto: {FOTO1}"
    assert sync._tt_midia_do_registro(
        {"video_list": [VIDEO, {"url": VIDEO}], "images": [{"image_url": FOTO1}, {"url": FOTO2}]}
    ) == f"Anexou 1 vídeo e 2 fotos: {VIDEO} | {FOTO1} | {FOTO2}"
    assert sync._tt_midia_do_registro({"images": [{"sem_url": "x"}, ""]}) == ""


# ---------------------------------------------------------------- o caso reaberto


async def test_caso_recusado_cede_a_vez_pro_caso_novo_com_a_linha_do_tempo_dele(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(e) 293798: a nossa recusa matou o caso antigo e a compradora ABRIU OUTRO.
    O chamado passa a acompanhar o caso novo (não era só pra CANCELADO) e a
    linha do tempo dele entra na MESMA passada — antes o painel ficava mudo."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    novo_em = fake.update_time + 7200
    fake.outros = [_caso(RID_NOVO, "RETURN_OR_REFUND_REQUEST_PENDING", criado=novo_em)]
    fake.linhas[RID_REEMB] = [{"role": "BUYER", "create_time": ESCREVEU, "note": "caso velho"}]
    fake.linhas[RID_NOVO] = [
        {"role": "BUYER", "create_time": novo_em + 60,
         "note": "Abri de novo: não foi o que pedi", "videos": [{"url": VIDEO}]},
    ]

    s1 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)

    assert s1["encerrados"] == 0 and ch.resolvido is False
    assert ch.chamado == RID_NOVO
    txts = await _recebidas(db, ch.id)
    assert any(
        "ABRIU OUTRO caso" in t and RID_NOVO in t and "já recusado" in t
        and "confira se ainda dá pra responder" in t
        for t in txts
    ), txts
    # a fala do caso NOVO veio junto, com vídeo e tudo
    assert f"Comprador {_hora(novo_em + 60)}: Abri de novo: não foi o que pedi Anexou 1 vídeo: {VIDEO}" in txts
    assert RID_NOVO in fake.records_pedidos
    # nada de "refez o pedido" (aquele texto é do caso CANCELADO) nem de desfecho
    assert not any("refez o pedido" in t or "valor fica com o vendedor" in t for t in txts)
    # (i) de novo: o chamado já está no caso novo e nada duplica
    s2 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=4))
    assert s2["novos"] == 0 and await _recebidas(db, ch.id) == txts


async def test_caso_morto_com_sucessor_tambem_morto_nao_troca(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(f) o caso seguinte também já acabou: não há pra onde ir. O chamado fica
    no caso que está acompanhando — e continua lendo a linha do tempo dele."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    novo_em = fake.update_time + 7200
    fake.outros = [_caso(RID_NOVO, "REFUND_OR_RETURN_REQUEST_REJECT", criado=novo_em)]
    fake.linhas[RID_REEMB] = [{"role": "BUYER", "create_time": ESCREVEU, "note": "cadê meu dinheiro"}]

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)

    assert ch.chamado == RID_REEMB
    txts = await _recebidas(db, ch.id)
    assert not any("ABRIU OUTRO" in t or "refez o pedido" in t for t in txts)
    assert any("cadê meu dinheiro" in t for t in txts), txts
    assert fake.records_pedidos == [RID_REEMB]


async def test_next_return_id_da_tiktok_manda_mesmo_com_caso_mais_novo_na_lista(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(g) quando a TikTok diz qual é o sucessor (`next_return_id`), é ele —
    mesmo que outro caso do pedido tenha sido aberto depois."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    novo_em = fake.update_time + 3600
    fake.next_return_id = RID_NOVO
    fake.outros = [
        _caso(RID_NOVO, "RETURN_OR_REFUND_REQUEST_PENDING", criado=novo_em),
        _caso(RID_TARDE, "RETURN_OR_REFUND_REQUEST_PENDING", criado=novo_em + 86400),
    ]

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)

    assert ch.chamado == RID_NOVO
    assert any(RID_NOVO in t and "ABRIU OUTRO caso" in t for t in await _recebidas(db, ch.id))
    assert RID_TARDE not in fake.records_pedidos
    # a função pura: o sucessor apontado ganha até de quem é MAIS VELHO que o caso atual
    atual = {"return_id": "A", "create_time": 500, "next_return_id": "B"}
    casos = [atual, {"return_id": "B", "create_time": 100,
                     "return_status": "RETURN_OR_REFUND_REQUEST_PENDING"},
             {"return_id": "C", "create_time": 900,
              "return_status": "RETURN_OR_REFUND_REQUEST_PENDING"}]
    assert sync._tiktok_caso_novo(casos, atual)["return_id"] == "B"
    # sem `next_return_id`: vale a data, e o VIVO ganha do morto mais novo
    atual2 = {"return_id": "A", "create_time": 500}
    casos2 = [atual2, {"return_id": "B", "create_time": 600,
                       "return_status": "RETURN_OR_REFUND_REQUEST_PENDING"},
              {"return_id": "C", "create_time": 900,
               "return_status": "RETURN_OR_REFUND_REQUEST_CANCEL"}]
    assert sync._tiktok_caso_novo(casos2, atual2)["return_id"] == "B"


async def test_arbitragem_ganha_por_nos_nao_cede_a_vez(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(h) SUPPORT_SELLER: a TikTok decidiu a nosso favor e o caso acabou ali.
    Caso novo do comprador é briga nova — o chamado encerra como ganhamos."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    fake.status_reembolso = "RETURN_OR_REFUND_REQUEST_CANCEL"
    fake.arb = "SUPPORT_SELLER"
    novo_em = fake.update_time + 7200
    fake.outros = [_caso(RID_NOVO, "RETURN_OR_REFUND_REQUEST_PENDING", criado=novo_em)]

    s1 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)

    assert ch.chamado == RID_REEMB
    assert s1["encerrados"] == 1 and ch.status_plataforma == "ganhamos"
    txts = await _recebidas(db, ch.id)
    assert not any("ABRIU OUTRO" in t or "refez o pedido" in t for t in txts)
    assert any("A FAVOR DO VENDEDOR" in t for t in txts)
    assert any("Plataforma encerrou o caso" in t for t in await _sistema_txts(db, ch.id))
