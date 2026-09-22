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
from sqlalchemy import select

from app.models import ChamadoMensagem
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
        # `seller_next_action_response` do caso ATUAL: None = o que o fake de
        # origem manda (só no caso pendente); lista = o teste é que manda.
        self.acoes: list[dict] | None = None

    async def get_return_list(self, *, order_ids=None, **kw):
        casos = await super().get_return_list(order_ids=order_ids, **kw)
        if self.next_return_id:
            casos[0]["next_return_id"] = self.next_return_id
        if self.acoes is not None:
            casos[0]["seller_next_action_response"] = [dict(a) for a in self.acoes]
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
    # 22/09 (Vinicius: "vai pegar tudo o que a mulher falou? é isso que preciso"):
    # a conversa de TODOS os casos do pedido é lida, não só a do caso acompanhado —
    # o que ele escreveu no caso anterior também precisa aparecer no chamado.
    assert fake.records_pedidos[0] == RID_REEMB
    assert set(fake.records_pedidos) == {RID_REEMB, RID_NOVO}


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
    # a conversa dos outros casos do pedido também é lida (inclusive a do
    # RID_TARDE), mas quem o chamado ACOMPANHA é o sucessor apontado pela TikTok
    assert fake.records_pedidos[0] == RID_NOVO
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


# -------------------------------------------- o que a TikTok espera de nós AGORA


"""Vinicius 22/09, o mesmo 293798 (R$ 640,54, loja TikTok Mini): no painel o
histórico terminava em "Arbitragem encerrada na TikTok." + a análise do robô
pedindo humano, e a equipe leu como caso ENCERRADO. No Seller Center, no mesmo
minuto, o caso estava "Aguardando emissão de…", com **19h56m para aprovação
automática** e um botão Responder — "veja tá diferente, aqui tá para responder,
não encerrado". Arbitragem encerrada não é caso encerrado: ele volta pro fluxo
normal e o relógio do reembolso corre contra a loja.

A TikTok entrega isso em `seller_next_action_response[].action/deadline` (o
mesmo campo do "Prazo p/ responder" da Logística). Agora a última linha do
histórico diz, em português, a situação, a ação e o prazo — e entra como FALA
DA PLATAFORMA, então a aba Chamados tira o chamado de "parado" e põe em Análise
Humano."""

ESPERA = "A TikTok está esperando a NOSSA resposta neste caso"
ACAO_REEMBOLSO = "Responder ao pedido de reembolso no TikTok"
ACAO_DEVOLUCAO = "Responder à solicitação de devolução no TikTok"


def _prazo_txt(epoch: int) -> str:
    """O prazo como a linha grava: data inteira, no fuso de São Paulo."""
    return (
        datetime.fromtimestamp(epoch, UTC)
        .astimezone(chamados_svc.SAO_PAULO)
        .strftime("%d/%m/%Y %H:%M")
    )


def _estado(situacao: str, acao: str, epoch: int | None) -> str:
    fecho = (
        f" Prazo até {_prazo_txt(epoch)} — sem resposta, a TikTok aprova o reembolso ao "
        "comprador automaticamente."
        if epoch is not None
        else " A plataforma não informou prazo."
    )
    return f"{ESPERA} — {situacao}. O que fazer: {acao}.{fecho}"


def _estados(txts: list[str]) -> list[str]:
    return [t for t in txts if t.startswith(ESPERA)]


def _relogio_real(fake) -> datetime:
    """A passada acontece AGORA — é o que a produção faz (`agora` default =
    `datetime.now`). Só com o relógio real a linha de estado cai DEPOIS das que
    a mesma passada grava sem hora própria (essas nascem com o `now()` do
    banco): é essa ordem que a equipe lê no painel."""
    agora = datetime.now(UTC)
    fake.create_time = int((agora - timedelta(days=2)).timestamp())
    fake.update_time = int((agora - timedelta(hours=6)).timestamp())
    return agora


async def test_arbitragem_encerrada_mas_a_tiktok_ainda_espera_a_nossa_resposta(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(1) A reprodução do 293798: caso com `arbitration_status=CLOSED` E uma
    ação pendente com prazo. O histórico continua dizendo que a arbitragem
    acabou — e ganha, DEPOIS dela, a linha que faltava."""
    fake = _TikTokCasos()
    ch, _ = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    agora = _relogio_real(fake)
    fake.arb = "CLOSED"
    prazo = int((agora + timedelta(hours=19, minutes=56)).timestamp())
    fake.acoes = [{"action": "SELLER_RESPOND_REFUND", "deadline": prazo}]

    s1 = await sync.sync_respostas(db, agora=agora)
    await db.refresh(ch)

    assert s1["verificados"] == 1 and s1["encerrados"] == 0, s1
    assert ch.resolvido is False
    txts = await _recebidas(db, ch.id)
    estado = _estado("Recusado pela loja", ACAO_REEMBOLSO, prazo)
    assert estado in txts, txts
    # o texto inteiro, como a equipe lê: situação e ação em português e o prazo
    # no fuso da loja (a API manda epoch UTC)
    assert estado == (
        "A TikTok está esperando a NOSSA resposta neste caso — Recusado pela loja. "
        f"O que fazer: Responder ao pedido de reembolso no TikTok. Prazo até {_prazo_txt(prazo)}"
        " — sem resposta, a TikTok aprova o reembolso ao comprador automaticamente."
    )
    # e vem DEPOIS do "acabou", que era onde o painel parava
    assert txts.index("Arbitragem encerrada na TikTok.") < txts.index(estado), txts
    # a ordem não é sorte: a linha do estado nasce com a HORA DA PASSADA e as
    # outras da mesma passada com o `now()` do banco, de quando ela começou
    quando = {
        m.texto: m.created_at
        for m in (
            await db.execute(
                select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)
            )
        ).scalars()
    }
    assert quando[estado] == agora
    assert quando["Arbitragem encerrada na TikTok."] < agora


async def test_o_estado_e_fala_da_plataforma_e_a_aba_pede_humano(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(2) A linha é `recebida` — fala da PLATAFORMA, não evento de sistema. É
    isso que tira o chamado de "Aguard. Plataforma" (onde ele dormia enquanto o
    prazo corria) e põe em Análise Humano, "responder no Seller Center"."""
    from tests.test_chamados_devolucao import _status_aba

    fake = _TikTokCasos()
    ch, _ = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    agora = _relogio_real(fake)
    prazo = int((agora + timedelta(days=1)).timestamp())
    fake.acoes = [{"action": "SELLER_RESPOND_REFUND", "deadline": prazo}]

    await sync.sync_respostas(db, agora=agora)
    await db.refresh(ch)

    estado = _estado("Recusado pela loja", ACAO_REEMBOLSO, prazo)
    # sem arbitragem a passada não grava mais nada: a linha do estado é a ÚNICA
    # fala da plataforma, então é ela que decide a coluna Status
    assert await _recebidas(db, ch.id) == [estado]
    msg = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.texto == estado
            )
        )
    ).scalars().one()
    assert (msg.direcao, msg.tipo, msg.canal) == ("recebida", "resposta", "api")
    assert msg.autor_nome == "TikTok Shop" and msg.created_at == agora
    # o chamado seguia "aguardando" pela nossa recusa; a fala nova é mais recente
    assert ch.status_plataforma == "aguardando"
    assert await _status_aba(db, ch) == (
        "analise_humano", "plataforma respondeu — responder no Seller Center"
    )


async def test_mesmo_prazo_nao_repete_a_linha_prazo_novo_entra(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(3) O cron passa de hora em hora: o mesmo prazo não pode encher o
    histórico. Quando a TikTok MUDA o prazo (prorrogou, ou é outra ação), aí
    sim entra linha nova — é informação nova pra equipe."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    prazo = int((recusa + timedelta(days=2)).timestamp())
    fake.acoes = [{"action": "SELLER_RESPOND_REFUND", "deadline": prazo}]

    # 2 novos na 1ª passada: o evento da nossa recusa + a linha do estado
    s1 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    assert s1["novos"] == 2, s1
    txts = await _recebidas(db, ch.id)
    assert _estados(txts) == [_estado("Recusado pela loja", ACAO_REEMBOLSO, prazo)]

    s2 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=4))
    assert s2["novos"] == 0 and await _recebidas(db, ch.id) == txts

    prazo2 = int((recusa + timedelta(days=4)).timestamp())
    fake.acoes = [{"action": "SELLER_RESPOND_REFUND", "deadline": prazo2}]
    s3 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=5))
    assert s3["novos"] == 1, s3
    assert _estados(await _recebidas(db, ch.id)) == [
        _estado("Recusado pela loja", ACAO_REEMBOLSO, prazo),
        _estado("Recusado pela loja", ACAO_REEMBOLSO, prazo2),
    ]


async def test_sem_acao_pendente_nenhuma_linha_de_estado(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(4) Caso sem `seller_next_action_response` (ou com ação SEM prazo): a
    TikTok não está cobrando nada — não inventa prazo nem linha."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    fake.linhas[RID_REEMB] = [{"role": "BUYER", "create_time": ESCREVEU, "note": "cadê meu dinheiro"}]

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    txts = await _recebidas(db, ch.id)
    assert _estados(txts) == [] and any("cadê meu dinheiro" in t for t in txts), txts

    # ação anunciada sem `deadline`: a TikTok não deu prazo → continua sem linha
    fake.acoes = [{"action": "SELLER_RESPOND_REFUND"}]
    s2 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=4))
    assert s2["novos"] == 0 and _estados(await _recebidas(db, ch.id)) == []


async def test_entre_varias_acoes_vale_a_de_prazo_mais_curto(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(5) A TikTok pode listar mais de uma ação pendente no mesmo caso. Quem
    manda é a que vence primeiro — é a que faz o dinheiro ir embora antes."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    perto = int((recusa + timedelta(hours=20)).timestamp())
    longe = int((recusa + timedelta(days=5)).timestamp())
    fake.acoes = [
        {"action": "SELLER_RESPOND_RETURN", "deadline": longe},
        {"action": "SELLER_RESPOND_REFUND", "deadline": perto},
        {"action": "SELLER_RESPOND_RECEIVE_PACKAGE", "deadline": longe + 86400},
    ]

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))

    assert _estados(await _recebidas(db, ch.id)) == [
        _estado("Recusado pela loja", ACAO_REEMBOLSO, perto)
    ]


async def test_depois_da_troca_o_estado_e_o_do_caso_novo(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(6) 293798 inteiro: o caso velho morreu, a compradora abriu outro e é o
    NOVO que tem prazo correndo. A linha de estado tem que ser a dele — a do
    caso morto mandaria a equipe responder onde não dá mais."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    novo_em = fake.update_time + 7200
    prazo_novo = novo_em + 2 * 86400
    fake.acoes = [  # o caso VELHO também traz ação pendente (a TikTok não limpa)
        {"action": "SELLER_RESPOND_RETURN", "deadline": int((recusa + timedelta(hours=8)).timestamp())}
    ]
    fake.outros = [
        _caso(RID_NOVO, "RETURN_OR_REFUND_REQUEST_PENDING", criado=novo_em)
        | {"seller_next_action_response": [{"action": "SELLER_RESPOND_REFUND", "deadline": prazo_novo}]}
    ]

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)

    assert ch.chamado == RID_NOVO
    assert _estados(await _recebidas(db, ch.id)) == [
        _estado("Aberto — aguardando resposta da loja", ACAO_REEMBOLSO, prazo_novo)
    ]


async def test_acao_ou_situacao_que_a_tiktok_inventar_nao_quebra_a_linha(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """(7) Código de ação fora do dicionário (a TikTok cria): o prazo NUNCA pode
    sumir por falta de tradução — vai o rótulo genérico com o código cru. Idem
    pro `return_status` desconhecido, e pro prazo que vem sem ação nenhuma."""
    fake = _TikTokCasos()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    fake.status_reembolso = "AWAITING_SELLER_EVIDENCE"  # status que ninguém traduziu
    prazo = int((recusa + timedelta(days=1)).timestamp())
    fake.acoes = [{"action": "SELLER_UPLOAD_ARBITRATION_EVIDENCE", "deadline": prazo}]

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    txts = await _recebidas(db, ch.id)
    assert _estados(txts) == [
        _estado(
            "awaiting_seller_evidence", "Ação pendente: SELLER_UPLOAD_ARBITRATION_EVIDENCE", prazo
        )
    ], txts

    # prazo sem ação nomeada: ainda assim a equipe precisa saber que corre relógio
    prazo2 = int((recusa + timedelta(days=3)).timestamp())
    fake.acoes = [{"action": "", "deadline": prazo2}]
    fake.status_reembolso = "RETURN_OR_REFUND_REQUEST_PENDING"
    await sync.sync_respostas(db, agora=recusa + timedelta(hours=4))
    assert _estado(
        "Aberto — aguardando resposta da loja", "Responder na plataforma", prazo2
    ) in await _recebidas(db, ch.id)
