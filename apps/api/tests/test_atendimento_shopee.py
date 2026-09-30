# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""Adaptador da Shopee: a leitura do chat e o envio, com um cliente FALSO.

Sem rede: o `ShopeeFalso` devolve payloads no formato medido em produção em
25/09/2026 (`scratchpad/formatos_api.txt`) e pagina a lista de conversas pelo
horário, como a Shopee (`next_cursor.next_message_time_nano`) — e no SENTIDO
medido em 28/09 (`formato_cliente.txt`): `older` = das mais novas para trás;
`latest` SEM horário = as mais ANTIGAS da loja, crescente (a armadilha);
`page_size` acima de 25 = lista vazia.

O que se mede aqui é o que faria a caixa perder ou duplicar mensagem:

- o cursor desce do topo até onde parou, respeita o teto de conversas por
  rodada e CONTINUA de onde parou, sem pular conversa — e toda rodada lê o
  TOPO antes de continuar a caminhada antiga (a mensagem nova não espera);
- o topo é pedido com `direction=older` sem horário (com `latest`, a loja
  antiga devolveria 2023 e a mensagem de hoje ficaria de fora — há um teste
  que prova que o falso pega esse erro), a página nunca passa de 25, e a
  ordem da lista não é presumida (página fora de ordem não encerra cedo);
- a nossa resposta barrada DEPOIS de aceita é vista (reconferência);
- NENHUM pedido de "marcar como lido" sai do cliente de verdade;
- a primeira leitura só pega os últimos 7 dias;
- quem é loja e quem é cliente sai de `from_shop_id == shop_id`;
- tipos (texto, imagem, produto, pedido, figurinha, notificação) e o elo com
  o pedido;
- rodar duas vezes não duplica; a nossa resposta voltando é adotada;
- envio ok / ambíguo / recusado com código; loja sem permissão = sem_escopo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import enriquecer, gravar, shopee
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    MSG_ENVIADA,
    MSG_FALHOU,
    ORIGEM_CLIENTE,
    ORIGEM_EXTERNO,
    ORIGEM_HUMANO,
    PLATAFORMAS,
    STATUS_CANAL,
)
from app.services.marketplaces.shopee import ShopeeClient

SHOP = 111
COMPRADOR = 555
AGORA = datetime.now(UTC).replace(microsecond=0)
# Acima disso, a Shopee devolveu lista VAZIA em várias lojas (60, em 28/09).
PAGINA_MAXIMA_REAL = 25


def _s(quando: datetime) -> int:
    return int(quando.timestamp())


def _ns(quando: datetime) -> int:
    return _s(quando) * 10**9


# ─────────────── cliente falso ───────────────


class ShopeeFalso:
    """Imita o `ShopeeClient` no que o adaptador usa, com o formato real."""

    def __init__(self, shop_id: int = SHOP) -> None:
        self.shop_id = shop_id
        self.conversas: dict[str, dict] = {}
        self.mensagens: dict[str, list[dict]] = {}
        self.nao_lidas = 0
        self.erro_nao_lidas: Exception | None = None
        self.erro_mensagens: dict[str, Exception] = {}
        self.chamadas_lista: list[dict] = []
        self.chamadas_mensagens: list[tuple[str, str | None]] = []
        self.resposta_envio: Any = {"message_id": "9000000000000000001"}
        self.enviados: list[tuple[str, str]] = []

    # — montagem do cenário —

    def conversa(
        self,
        cid: str,
        *msgs: dict,
        nome: str = "comprador_teste",
        pinned: bool = False,
        nao_lidas: int = 1,
        unidade_ts: str = "ns",
    ) -> dict:
        """Conversa com as mensagens dadas; `last_message_timestamp` sai da mais nova."""
        existentes = self.mensagens.setdefault(cid, [])
        existentes.extend(msgs)
        ultima = max(existentes, key=lambda m: m["created_timestamp"])
        s = ultima["created_timestamp"]
        ts = {"s": s, "ms": s * 1000, "us": s * 10**6, "ns": s * 10**9}[unidade_ts]
        conv = {
            "conversation_id": cid,
            "to_id": COMPRADOR,
            "to_name": nome,
            "to_avatar": "",
            "shop_id": SHOP,
            "unread_count": nao_lidas,
            "pinned": pinned,
            "last_read_message_id": "0",
            "latest_message_id": ultima["message_id"],
            "latest_message_type": ultima["message_type"],
            "latest_message_content": ultima["content"],
            "latest_message_from_id": ultima["from_id"],
            "last_message_timestamp": ts,
            "last_message_option": 0,
            "max_general_option_hide_time": "9223372036854775",
            "mute": False,
            "opposite_last_deliver_msg_id": "0",
            "opposite_last_read_msg_id": "0",
        }
        self.conversas[cid] = conv
        return conv

    # — API —

    async def chat_unread_count(self) -> int:
        if self.erro_nao_lidas is not None:
            raise self.erro_nao_lidas
        return self.nao_lidas

    async def chat_conversation_list(
        self,
        *,
        direction: str = "latest",
        tipo: str = "all",
        page_size: int = 25,
        next_timestamp_nano: int | str | None = None,
    ) -> dict:
        """A lista como a Shopee de VERDADE a devolve (medido em 28/09/2026, loja kfa).

        - `older` sem horário: as MAIS NOVAS primeiro, decrescente (fixadas antes);
        - `older` + horário X: as de antes de X, decrescente;
        - `latest` SEM horário: as MAIS ANTIGAS da loja, CRESCENTE — a armadilha;
        - `latest` + horário X: as de depois de X, crescente;
        - `type=unread` só as não lidas; `page_size` > 25: lista vazia.
        """
        self.chamadas_lista.append(
            {
                "direction": direction,
                "tipo": tipo,
                "page_size": page_size,
                "next": next_timestamp_nano,
            }
        )
        if direction not in ("older", "latest"):
            raise RuntimeError("shopee_chat_conversas error_param: direction")

        def ts(c: dict) -> int:
            return shopee._ns(c["last_message_timestamp"])

        todas = list(self.conversas.values())
        if tipo == "unread":
            todas = [c for c in todas if c["unread_count"]]
        elif tipo == "pinned":
            todas = [c for c in todas if c["pinned"]]
        if page_size > PAGINA_MAXIMA_REAL:
            todas = []
        crescente = sorted(todas, key=ts)
        corte = int(next_timestamp_nano) if next_timestamp_nano is not None else None
        if direction == "older" and corte is None:
            # Fixadas primeiro (como na tela da Shopee), depois da mais nova para trás.
            lista = [c for c in todas if c["pinned"]] + [
                c for c in reversed(crescente) if not c["pinned"]
            ]
        elif direction == "older":
            lista = [c for c in reversed(crescente) if not c["pinned"] and ts(c) < corte]
        elif corte is None:
            lista = crescente  # `latest` sem horário: 2023 primeiro
        else:
            lista = [c for c in crescente if ts(c) > corte]
        pagina = lista[:page_size]
        ultimo = pagina[-1] if pagina else None
        return {
            "page_result": {
                "page_size": len(pagina),
                "next_cursor": {
                    "next_message_time_nano": str(ts(ultimo)) if ultimo else "0",
                    "conversation_id": "0",
                },
                "more": len(lista) > page_size,
            },
            "conversations": pagina,
        }

    async def chat_mensagens_pagina(
        self, conversation_id: str, *, offset: str | None = None, page_size: int = 25
    ) -> dict:
        self.chamadas_mensagens.append((conversation_id, offset))
        if conversation_id in self.erro_mensagens:
            raise self.erro_mensagens[conversation_id]
        msgs = sorted(
            self.mensagens.get(conversation_id, []),
            key=lambda m: m["created_timestamp"],
            reverse=True,
        )
        inicio = int(offset or 0)
        fim = inicio + page_size
        return {
            "messages": msgs[inicio:fim],
            "page_result": {
                "next_offset": str(fim) if fim < len(msgs) else "",
                "page_size": page_size,
            },
        }

    async def chat_send_message(self, to_id, *, text: str = "", image_url: str = "") -> dict:
        self.enviados.append((str(to_id), text))
        if isinstance(self.resposta_envio, BaseException):
            raise self.resposta_envio
        return self.resposta_envio


_seq = iter(range(10**6))


def msg(
    cid: str,
    quando: datetime,
    *,
    da_loja: bool = False,
    tipo: str = "text",
    content: dict | None = None,
    texto: str = "oi",
    source: dict | None = None,
    status: str = "normal",
    business_type: int | None = None,
    mid: str | None = None,
) -> dict:
    """Mensagem no formato do `get_message`. O comprador TAMBÉM tem from_shop_id."""
    m = {
        "message_id": mid or f"70{next(_seq):017d}",
        "from_id": 222 if da_loja else COMPRADOR,
        "to_id": COMPRADOR if da_loja else 222,
        "from_shop_id": SHOP if da_loja else 987654,
        "to_shop_id": 987654 if da_loja else SHOP,
        "message_type": tipo,
        "content": content if content is not None else {"text": texto},
        "conversation_id": cid,
        "created_timestamp": _s(quando),
        "region": "BR",
        "status": status,
        "message_option": 0,
        "source": "new_webchat",
        "source_content": source or {},
        "quoted_msg": None,
    }
    if business_type is not None:
        m["business_type"] = business_type
    return m


# ─────────────── fixtures ───────────────


async def _canal(db: AsyncSession, user: User, cursor: dict | None = None):
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name="Loja KFA",
        credentials=encrypt_json({"access_token": "t", "refresh_token": "r", "shop_id": SHOP}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", cursor=cursor or {}
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _rodar(db: AsyncSession, canal, integ, falso: ShopeeFalso):
    r = await shopee.sincronizar(db, canal, integ, falso)
    await db.commit()  # quem commita é o sync (lote E); aqui, o teste
    return r


async def _conversas(db: AsyncSession) -> dict[str, AtendimentoConversa]:
    linhas = (await db.execute(select(AtendimentoConversa))).scalars().all()
    for c in linhas:
        await db.refresh(c)
    return {c.externo_id: c for c in linhas}


async def _mensagens(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    linhas = (
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em)
            )
        )
        .scalars()
        .all()
    )
    for m in linhas:
        await db.refresh(m)
    return list(linhas)


async def _total_mensagens(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(AtendimentoMensagem)))


@pytest.fixture
def teto(monkeypatch):
    def _f(n: int) -> None:
        monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", n)

    _f(40)
    return _f


# ─────────────── vocabulário ───────────────


def test_constantes_do_adaptador_batem_com_o_vocabulario():
    assert shopee.PLATAFORMA in PLATAFORMAS
    for s in (shopee.STATUS_OK, shopee.STATUS_SEM_ESCOPO, shopee.STATUS_ERRO):
        assert s in STATUS_CANAL


def test_horario_em_qualquer_unidade_vira_o_mesmo_utc():
    base = datetime(2026, 9, 25, 12, 0, 1, tzinfo=UTC)
    s = int(base.timestamp())
    for bruto in (s, s * 1000, s * 10**6, s * 10**9, str(s * 10**9)):
        assert shopee.para_datetime(bruto) == base
    assert shopee.para_datetime(1_758_801_601_123_456_789).microsecond == 123456
    assert shopee.para_datetime(None) is None
    assert shopee.para_datetime("lixo") is None
    assert shopee.para_datetime(0) is None


def test_autor_pela_from_shop_id_nunca_por_diferente_de_zero():
    do_comprador = msg("c", AGORA)  # from_shop_id do comprador NÃO é zero
    da_loja = msg("c", AGORA, da_loja=True)
    assert do_comprador["from_shop_id"] != 0
    assert shopee.interpretar(do_comprador, SHOP).autor == AUTOR_CLIENTE
    assert shopee.interpretar(da_loja, SHOP).autor == AUTOR_LOJA
    # Resposta automática da loja, notificação e afiliado: sistema.
    assert shopee.interpretar(msg("c", AGORA, da_loja=True, status="auto_reply"), SHOP).autor == (
        AUTOR_SISTEMA
    )
    assert shopee.interpretar(msg("c", AGORA, tipo="notification", content={}), SHOP).autor == (
        AUTOR_SISTEMA
    )
    assert shopee.interpretar(msg("c", AGORA, business_type=11), SHOP).autor == AUTOR_SISTEMA


# ─────────────── leitura ───────────────


async def test_primeira_rodada_so_ultimos_7_dias_e_cursor(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.nao_lidas = 4
    f.conversa("A", msg("A", AGORA - timedelta(hours=1), texto="cadê meu pedido?"))
    f.conversa(
        "B",
        msg("B", AGORA - timedelta(days=2), texto="chegou quebrado"),
        msg("B", AGORA - timedelta(days=2) + timedelta(minutes=5), da_loja=True, texto="vamos ver"),
    )
    f.conversa("C", msg("C", AGORA - timedelta(days=8), texto="velha"))

    r = await _rodar(db, canal, integ, f)

    assert r.status == shopee.STATUS_OK
    assert (r.conversas_novas, r.conversas_atualizadas, r.mensagens_novas) == (2, 0, 3)
    # O contador vai no resultado; quem grava no canal é o sync, no fim da
    # rodada (mexer no canal no começo o travaria durante a rodada inteira).
    assert r.nao_lidas == 4 and canal.nao_lidas_plataforma in (None, 0)
    convs = await _conversas(db)
    assert set(convs) == {"A", "B"}  # C está fora da janela de 7 dias
    a, b = convs["A"], convs["B"]
    assert a.comprador_id == str(COMPRADOR) and a.comprador_nome == "comprador_teste"
    assert a.conta == "Loja KFA" and a.canal_id == canal.id and a.nao_lidas == 1
    assert a.aguardando_resposta is True
    assert a.prazo_resposta_em == AGORA - timedelta(hours=1) + timedelta(hours=12)
    assert b.aguardando_resposta is False and b.situacao == "respondida"
    resposta = (await _mensagens(db, b.id))[-1]
    assert (resposta.autor, resposta.origem, resposta.status) == (
        AUTOR_LOJA,
        ORIGEM_EXTERNO,
        MSG_ENVIADA,
    )
    pergunta = (await _mensagens(db, a.id))[0]
    assert (pergunta.autor, pergunta.origem, pergunta.texto) == (
        AUTOR_CLIENTE,
        ORIGEM_CLIENTE,
        "cadê meu pedido?",
    )
    assert pergunta.enviada_em == AGORA - timedelta(hours=1)
    # Cursor: o horário da mais nova, nada por ler.
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(hours=1))
    assert canal.cursor["lacunas"] == []
    # O topo: `older` SEM horário (do agora para trás) — o uso documentado.
    assert f.chamadas_lista[0]["direction"] == "older"
    assert f.chamadas_lista[0]["next"] is None


# ─────────────── o sentido da lista (P1, medido em 28/09) ───────────────


def _ids(resp: dict) -> list[str]:
    return [c["conversation_id"] for c in resp["conversations"]]


async def test_falso_devolve_a_lista_no_sentido_medido_em_28_09():
    """O falso tem de errar do MESMO jeito que a Shopee — é o que dá sentido aos
    testes do topo: `latest` sem horário devolve as mais ANTIGAS, crescente."""
    f = ShopeeFalso()
    velha = AGORA - timedelta(days=900)  # as conversas de 2023/2024 da loja
    f.conversa("V1", msg("V1", velha))
    f.conversa("V2", msg("V2", velha + timedelta(days=1)))
    f.conversa("N1", msg("N1", AGORA - timedelta(hours=2)))
    f.conversa("N2", msg("N2", AGORA - timedelta(hours=1)), nao_lidas=0)

    assert _ids(await f.chat_conversation_list(direction="latest", page_size=2)) == ["V1", "V2"]
    topo = await f.chat_conversation_list(direction="older", page_size=2)
    assert _ids(topo) == ["N2", "N1"] and topo["page_result"]["more"] is True
    corte = topo["page_result"]["next_cursor"]["next_message_time_nano"]
    assert corte == str(_ns(AGORA - timedelta(hours=2)))  # a hora da última da página
    seguinte = await f.chat_conversation_list(direction="older", next_timestamp_nano=corte)
    assert _ids(seguinte) == ["V2", "V1"]
    depois = str(_ns(velha + timedelta(days=2)))
    assert _ids(
        await f.chat_conversation_list(direction="latest", next_timestamp_nano=depois)
    ) == ["N1", "N2"]
    assert _ids(await f.chat_conversation_list(direction="older", tipo="unread")) == [
        "N1",
        "V2",
        "V1",
    ]
    assert _ids(await f.chat_conversation_list(direction="older", page_size=60)) == []


async def _loja_com_2023_e_duas_de_hoje(db, make_user) -> tuple[Integration, Any, ShopeeFalso]:
    """Loja antiga (30 conversas de 2023) com 2 conversas de hoje e cursor de 6 h atrás."""
    integ, canal = await _canal(
        db, await make_user(), cursor={"ultimo_ts": _ns(AGORA - timedelta(hours=6))}
    )
    f = ShopeeFalso()
    velha = AGORA - timedelta(days=900)
    for i in range(30):
        f.conversa(f"V{i:02d}", msg(f"V{i:02d}", velha + timedelta(hours=i)))
    f.conversa("HOJE1", msg("HOJE1", AGORA - timedelta(hours=1), texto="cadê?"))
    f.conversa("HOJE2", msg("HOJE2", AGORA - timedelta(minutes=5), texto="e aí?"))
    return integ, canal, f


async def test_loja_antiga_o_topo_le_as_de_hoje_e_nunca_pede_latest(db, make_user, teto):
    """P1 (28/09): com `latest` no topo a Shopee devolve 2023, a rodada acha
    "tudo antigo" e termina sem a mensagem de hoje. O topo é `older`."""
    integ, canal, f = await _loja_com_2023_e_duas_de_hoje(db, make_user)

    r = await _rodar(db, canal, integ, f)

    assert (r.status, r.conversas_novas) == (shopee.STATUS_OK, 2)
    assert set(await _conversas(db)) == {"HOJE1", "HOJE2"}
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(minutes=5))
    assert {c["direction"] for c in f.chamadas_lista} == {"older"}
    assert f.chamadas_lista[0]["next"] is None
    assert max(c["page_size"] for c in f.chamadas_lista) <= PAGINA_MAXIMA_REAL


async def test_prova_latest_no_topo_perderia_a_mensagem_de_hoje(
    db, make_user, teto, monkeypatch
):
    """A prova de que o teste de cima pega o erro: com `latest` no topo, a
    mesma loja termina a rodada "ok" sem ler NADA (o que aconteceria em
    produção). Se este teste falhar, o falso deixou de imitar a Shopee."""
    monkeypatch.setattr(shopee, "DIRECAO_TOPO", "latest")
    integ, canal, f = await _loja_com_2023_e_duas_de_hoje(db, make_user)

    r = await _rodar(db, canal, integ, f)

    assert r.status == shopee.STATUS_OK
    assert await _conversas(db) == {}
    assert f.chamadas_lista[0]["direction"] == "latest"


async def test_pagina_da_lista_nunca_passa_de_25(db, make_user, teto, monkeypatch):
    """`page_size=60` devolveu lista VAZIA em várias lojas (28/09): vazio que a
    passada leria como "acabou". Com teto de 40, as páginas são 25 + 15."""
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    for i in range(30):
        f.conversa(f"C{i:02d}", msg(f"C{i:02d}", AGORA - timedelta(minutes=10 + i)))

    r = await _rodar(db, canal, integ, f)

    assert r.conversas_novas == 30
    assert [c["page_size"] for c in f.chamadas_lista] == [25, 15]

    # A prova: com página de 60, a mesma loja não seria lida.
    monkeypatch.setattr(shopee, "PAGINA_CONVERSAS", 60)
    integ2, canal2 = await _canal(db, await make_user())
    f2 = ShopeeFalso()
    f2.conversa("X", msg("X", AGORA - timedelta(minutes=1)))
    r2 = await shopee.sincronizar(db, canal2, integ2, f2)
    await db.commit()
    assert r2.conversas_novas == 0


async def test_rodar_duas_vezes_nao_duplica(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.conversa("A", msg("A", AGORA - timedelta(hours=3)), msg("A", AGORA - timedelta(hours=2)))
    f.conversa("B", msg("B", AGORA - timedelta(hours=1)))

    await _rodar(db, canal, integ, f)
    assert await _total_mensagens(db) == 3

    # Mesma lista: o topo já é o cursor — nada é relido.
    r2 = await _rodar(db, canal, integ, f)
    assert (r2.conversas_novas, r2.conversas_atualizadas, r2.mensagens_novas) == (0, 0, 0)
    assert await _total_mensagens(db) == 3

    # Cursor zerado (pior caso): relê a lista, mas a última mensagem de cada
    # conversa já está gravada — nem chama o get_message.
    antes = len(f.chamadas_mensagens)
    canal.cursor = {}
    r3 = await _rodar(db, canal, integ, f)
    assert (r3.conversas_novas, r3.conversas_atualizadas, r3.mensagens_novas) == (0, 2, 0)
    assert len(f.chamadas_mensagens) == antes
    assert await _total_mensagens(db) == 3
    assert len(await _conversas(db)) == 2


async def test_teto_por_rodada_e_retomada_sem_pular(db, make_user, teto):
    teto(2)
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    horas = {"E": 1, "D": 2, "C": 3, "B": 4, "A": 5}
    for cid, h in horas.items():
        f.conversa(cid, msg(cid, AGORA - timedelta(hours=h)))

    r1 = await _rodar(db, canal, integ, f)
    assert r1.conversas_novas == 2
    assert set(await _conversas(db)) == {"E", "D"}
    # Parou no teto: o trecho de D para baixo fica como lacuna.
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(hours=1))
    [lacuna] = canal.cursor["lacunas"]
    assert lacuna["retomar"]["next_message_time_nano"] == str(_ns(AGORA - timedelta(hours=2)))

    r2 = await _rodar(db, canal, integ, f)
    assert r2.conversas_novas == 2
    assert set(await _conversas(db)) == {"E", "D", "C", "B"}
    # O topo é conferido primeiro (nada novo) e a lacuna continua de onde parou.
    retomada = str(_ns(AGORA - timedelta(hours=2)))
    assert [c["next"] for c in f.chamadas_lista[-2:]] == [None, retomada]
    assert all(c["direction"] == shopee.DIRECAO_MAIS_ANTIGAS for c in f.chamadas_lista)

    # Chega conversa nova no topo NO MEIO da caminhada: a rodada 3 lê a nova
    # PRIMEIRO e termina a caminhada com o que sobra do teto.
    f.conversa("F", msg("F", AGORA - timedelta(minutes=10)))
    r3 = await _rodar(db, canal, integ, f)
    assert r3.conversas_novas == 2
    assert set(await _conversas(db)) == {"E", "D", "C", "B", "A", "F"}
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(minutes=10))
    assert canal.cursor["lacunas"] == []

    r4 = await _rodar(db, canal, integ, f)
    assert (r4.conversas_novas, r4.conversas_atualizadas) == (0, 0)
    # Nenhuma página pediu mais do que o teto.
    assert all(c["page_size"] <= 2 for c in f.chamadas_lista)


async def test_caminhada_longa_nao_segura_a_mensagem_nova_do_topo(db, make_user, teto):
    """API-12: com a caminhada retomada lida ANTES do topo, a mensagem nova
    esperava a caminhada inteira (primeira leitura de loja grande: várias
    rodadas com o prazo correndo)."""
    teto(3)
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    for i in range(12):
        f.conversa(f"V{i:02d}", msg(f"V{i:02d}", AGORA - timedelta(hours=2 + i)))
    await _rodar(db, canal, integ, f)  # lê 3, deixa a lacuna com 9

    f.conversa("NOVA", msg("NOVA", AGORA - timedelta(minutes=1), texto="cadê?"))
    r = await _rodar(db, canal, integ, f)

    assert "NOVA" in await _conversas(db)
    assert r.conversas_novas == 3  # a nova + 2 da caminhada antiga
    assert (await _conversas(db))["NOVA"].aguardando_resposta is True
    assert len(canal.cursor["lacunas"]) == 1  # a antiga segue de onde parou

    # E a caminhada antiga termina sem pular nenhuma.
    for _ in range(4):
        await _rodar(db, canal, integ, f)
    assert len(await _conversas(db)) == 13
    assert canal.cursor["lacunas"] == []


async def test_lista_fora_da_ordem_nao_encerra_a_caminhada_cedo(db, make_user, teto):
    """API-9: se a página vier em ordem crescente, a primeira conversa já é
    antiga e a rodada terminava "ok" sem ler nada — para sempre."""

    class ShopeeCrescente(ShopeeFalso):
        async def chat_conversation_list(self, **kw) -> dict:
            resp = await super().chat_conversation_list(**kw)
            resp["conversations"] = list(reversed(resp["conversations"]))
            return resp

    piso = AGORA - timedelta(hours=3)
    integ, canal = await _canal(db, await make_user(), cursor={"ultimo_ts": _ns(piso)})
    f = ShopeeCrescente()
    f.conversa("VELHA1", msg("VELHA1", AGORA - timedelta(hours=5)))
    f.conversa("VELHA2", msg("VELHA2", AGORA - timedelta(hours=4)))
    f.conversa("NOVA1", msg("NOVA1", AGORA - timedelta(hours=2)))
    f.conversa("NOVA2", msg("NOVA2", AGORA - timedelta(hours=1)))

    r = await _rodar(db, canal, integ, f)

    assert r.conversas_novas == 2
    assert set(await _conversas(db)) == {"NOVA1", "NOVA2"}
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(hours=1))


async def test_rodada_seguinte_so_pega_o_que_mudou(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.conversa("A", msg("A", AGORA - timedelta(hours=5)))
    f.conversa("B", msg("B", AGORA - timedelta(hours=4)))
    await _rodar(db, canal, integ, f)

    # O cliente da A escreve de novo: ela sobe para o topo.
    f.conversa("A", msg("A", AGORA - timedelta(minutes=1), texto="e aí?"))
    antes = len(f.chamadas_mensagens)
    r = await _rodar(db, canal, integ, f)
    assert (r.conversas_novas, r.conversas_atualizadas, r.mensagens_novas) == (0, 1, 1)
    assert [c for c, _ in f.chamadas_mensagens[antes:]] == ["A"]
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(minutes=1))


async def test_conversa_fixada_antiga_nao_encerra_a_caminhada(db, make_user, teto):
    integ, canal = await _canal(db, await make_user(), cursor={})
    f = ShopeeFalso()
    f.conversa("FIX", msg("FIX", AGORA - timedelta(days=30)), pinned=True)
    f.conversa("N1", msg("N1", AGORA - timedelta(hours=1)))
    f.conversa("N2", msg("N2", AGORA - timedelta(hours=2)))

    r = await _rodar(db, canal, integ, f)
    assert r.conversas_novas == 2
    assert set(await _conversas(db)) == {"N1", "N2"}


async def test_unidade_do_last_message_timestamp_nao_importa(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.conversa("S", msg("S", AGORA - timedelta(hours=1)), unidade_ts="s")
    f.conversa("M", msg("M", AGORA - timedelta(hours=2)), unidade_ts="ms")
    f.conversa("U", msg("U", AGORA - timedelta(days=9)), unidade_ts="us")

    r = await _rodar(db, canal, integ, f)
    assert r.conversas_novas == 2
    assert set(await _conversas(db)) == {"S", "M"}
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(hours=1))


async def test_mensagens_param_na_primeira_ja_gravada_e_no_maximo_3_paginas(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    inicio = AGORA - timedelta(days=1)
    f.conversa("A", *[msg("A", inicio + timedelta(minutes=i)) for i in range(80)])

    r1 = await _rodar(db, canal, integ, f)
    assert r1.mensagens_novas == 75  # 3 páginas de 25: o fim da conversa
    assert len(f.chamadas_mensagens) == 3

    f.conversa(
        "A",
        msg("A", inicio + timedelta(minutes=90)),
        msg("A", inicio + timedelta(minutes=91), da_loja=True, texto="resposta por fora"),
    )
    antes = len(f.chamadas_mensagens)
    r2 = await _rodar(db, canal, integ, f)
    assert r2.mensagens_novas == 2
    assert len(f.chamadas_mensagens) - antes == 1  # a 1ª página já tinha gravada


async def test_tipos_e_elo_com_o_pedido(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    t = AGORA - timedelta(hours=2)
    img = "https://cf.shopee.com.br/file/abc"
    f.conversa(
        "T",
        msg("T", t, texto="olá", source={"item_id": 4112503530}),
        msg("T", t + timedelta(minutes=1), tipo="image", content={"url": img, "thumb_url": "x"}),
        msg("T", t + timedelta(minutes=2), tipo="item",
            content={"shop_id": SHOP, "item_id": 9112503530}),
        msg("T", t + timedelta(minutes=3), tipo="order",
            content={"shop_id": SHOP, "order_sn": "250925ABCD"}),
        msg("T", t + timedelta(minutes=4), tipo="sticker", content={"sticker_id": "1"}),
        msg("T", t + timedelta(minutes=5), tipo="notification", content={}),
        msg("T", t + timedelta(minutes=6), tipo="video", content={"video_url": "https://v/1"}),
        msg("T", t + timedelta(minutes=7), da_loja=True, status="auto_reply", texto="já respondo"),
    )

    await _rodar(db, canal, integ, f)

    conv = (await _conversas(db))["T"]
    ms = await _mensagens(db, conv.id)
    vistos = [(m.tipo, m.autor) for m in ms]
    assert vistos == [
        ("texto", AUTOR_CLIENTE),
        ("imagem", AUTOR_CLIENTE),
        ("produto", AUTOR_CLIENTE),
        ("pedido", AUTOR_CLIENTE),
        ("outro", AUTOR_CLIENTE),
        ("outro", AUTOR_SISTEMA),
        ("video", AUTOR_CLIENTE),
        ("texto", AUTOR_SISTEMA),
    ]
    assert ms[1].anexos == [{"tipo": "imagem", "url": img}] and ms[1].texto is None
    # Cartões no formato da tela. Este cliente falso não tem as leituras de
    # produto/pedido: o cartão fica só com o id, sem derrubar a rodada (os
    # cartões preenchidos estão em test_atendimento_enriquecer.py).
    assert ms[2].anexos == [enriquecer.cartao_produto_vazio("9112503530")]
    assert ms[3].anexos == [enriquecer.cartao_pedido_vazio("250925ABCD")]
    # Figurinha e aviso: rótulo em português, não o código cru da API.
    assert ms[4].texto == "[Figurinha]"
    assert ms[5].texto == "[Aviso da plataforma]"
    assert ms[4].payload["content"] == {"sticker_id": "1"}  # o cru fica guardado
    assert ms[0].payload["source_content"] == {"item_id": 4112503530}  # o cru fica guardado
    # O elo com o resto do DaVinci.
    assert conv.pedido_marketplace == "250925ABCD"
    assert conv.anuncio_id == "9112503530"
    # A resposta automática NÃO conta como resposta: o cliente segue esperando.
    assert conv.aguardando_resposta is True
    assert conv.ultima_do_cliente_em == t + timedelta(minutes=6)


def test_tipo_desconhecido_vira_rotulo_em_portugues():
    """Tipo que a Shopee inventar amanhã: "[Mensagem]" no balão, o cru no payload."""
    lida = shopee.interpretar(msg("c", AGORA, tipo="add_on_deal", content={"x": 1}), SHOP)
    assert (lida.tipo, lida.texto) == ("outro", "[Mensagem]")


async def test_source_content_sozinho_liga_o_pedido(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.conversa(
        "P",
        msg("P", AGORA - timedelta(hours=1), tipo="faq_liveagent",
            content={"text": "Conversar com o vendedor"}, source={"order_sn": "250920XYZ"}),
    )
    await _rodar(db, canal, integ, f)
    conv = (await _conversas(db))["P"]
    assert conv.pedido_marketplace == "250920XYZ"
    (m,) = await _mensagens(db, conv.id)
    # O texto da opção do FAQ que o comprador tocou (não "[faq_liveagent]").
    assert (m.tipo, m.texto, m.autor) == ("outro", "Conversar com o vendedor", AUTOR_CLIENTE)


async def test_chat_de_afiliado_nao_entra_na_fila(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.conversa("AF", msg("AF", AGORA - timedelta(hours=1), business_type=11, texto="parceria?"))
    await _rodar(db, canal, integ, f)
    conv = (await _conversas(db))["AF"]
    assert conv.dados.get("afiliado") is True
    assert conv.aguardando_resposta is False
    (m,) = await _mensagens(db, conv.id)
    assert m.autor == AUTOR_SISTEMA


async def test_resposta_barrada_pela_shopee_nao_conta(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    t = AGORA - timedelta(hours=1)
    f.conversa(
        "X",
        msg("X", t, texto="tem outro canal?"),
        msg("X", t + timedelta(minutes=2), da_loja=True, status="censored_blacklist",
            texto="chama"),
    )
    await _rodar(db, canal, integ, f)
    conv = (await _conversas(db))["X"]
    resposta = (await _mensagens(db, conv.id))[-1]
    assert resposta.status == MSG_FALHOU and resposta.erro == "shopee censored_blacklist"
    assert conv.aguardando_resposta is True


async def test_adota_a_nossa_resposta_que_volta_pelo_sync(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    t = AGORA - timedelta(minutes=30)
    f.conversa("Q", msg("Q", t, texto="quando chega?"))
    await _rodar(db, canal, integ, f)
    conv = (await _conversas(db))["Q"]

    # A tela mandou pelo DaVinci (lote E): linha nossa, sem id da Shopee.
    nossa = AtendimentoMensagem(
        conversa_id=conv.id,
        autor=AUTOR_LOJA,
        origem=ORIGEM_HUMANO,
        texto="Olá! Seu pedido chega amanhã.",
        status=MSG_ENVIADA,
        enviada_em=t + timedelta(minutes=5),
    )
    db.add(nossa)
    await db.commit()

    # A Shopee devolve a mesma frase (pontuação diferente) na leitura seguinte.
    volta = msg("Q", t + timedelta(minutes=5), da_loja=True, texto="Olá, seu pedido chega amanhã")
    f.conversa("Q", volta)
    r = await _rodar(db, canal, integ, f)

    assert r.mensagens_novas == 0
    ms = await _mensagens(db, conv.id)
    assert len(ms) == 2
    await db.refresh(nossa)
    assert nossa.externo_id == f.conversas["Q"]["latest_message_id"]
    assert nossa.origem == ORIGEM_HUMANO and nossa.status == MSG_ENVIADA
    await db.refresh(conv)
    assert conv.aguardando_resposta is False


async def test_nossa_resposta_barrada_depois_de_aceita_e_vista(db, make_user, teto):
    """API-10: depois do envio o `latest_message_id` da lista é a nossa própria
    mensagem, já gravada — o atalho pulava o `get_message` e o status
    `blocked` que a Shopee põe depois nunca era visto."""
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    t = AGORA - timedelta(minutes=30)
    f.conversa("Q", msg("Q", t, texto="tem outro canal?"))
    f.conversa("ANTIGA", msg("ANTIGA", t, texto="oi"))
    await _rodar(db, canal, integ, f)
    convs = await _conversas(db)

    # Duas respostas nossas: uma há 1 min (Q), outra há 20 min (ANTIGA).
    agora = datetime.now(UTC).replace(microsecond=0)
    for cid, quando, mid in (
        ("Q", agora - timedelta(minutes=1), "9000000000000000701"),
        ("ANTIGA", agora - timedelta(minutes=20), "9000000000000000702"),
    ):
        db.add(
            AtendimentoMensagem(
                conversa_id=convs[cid].id,
                externo_id=mid,
                autor=AUTOR_LOJA,
                origem=ORIGEM_HUMANO,
                texto="me chama no zap",
                status=MSG_ENVIADA,
                enviada_em=quando,
            )
        )
        f.conversa(cid, msg(cid, quando, da_loja=True, texto="me chama no zap", mid=mid))
    await db.commit()
    for c in convs.values():
        await gravar.recalcular_conversa(db, c)
    await db.commit()
    await _rodar(db, canal, integ, f)  # a lista traz as nossas: atalho, nada muda
    assert (await _conversas(db))["Q"].aguardando_resposta is False

    # A Shopee barra a nossa DEPOIS (o horário da conversa nem muda).
    for m in f.mensagens["Q"] + f.mensagens["ANTIGA"]:
        if m["from_shop_id"] == SHOP:
            m["status"] = "blocked"
    f.chamadas_mensagens.clear()
    await _rodar(db, canal, integ, f)

    q = (await _conversas(db))["Q"]
    nossa = (await _mensagens(db, q.id))[-1]
    assert (nossa.status, nossa.erro) == (MSG_FALHOU, "shopee blocked")
    assert q.aguardando_resposta is True
    # Só a enviada nos últimos 10 min é reconferida (a antiga não custa chamada).
    assert [c for c, _ in f.chamadas_mensagens] == ["Q"]


@pytest.mark.parametrize("com_id", [True, False], ids=["envio-com-id", "envio-sem-id"])
async def test_ponta_a_ponta_ler_responder_pelo_davinci_e_ler_de_novo(
    db, make_user, teto, monkeypatch, com_id
):
    """Integração dos lotes (base × A × D × E), sem nada falso além da Shopee:
    o sync lê a pergunta, a pessoa responde pelo caminho ÚNICO de saída
    (`enviar.enviar_resposta`, com o validador de verdade) e a leitura
    seguinte traz a resposta de volta — sem duplicar e sem pôr a conversa de
    novo na fila. Com o id da Shopee no envio, casa pelo id; sem ele, a
    adoção casa pelo texto."""
    from app.services.atendimento import clientes, enviar

    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    integ, canal = await _canal(db, await make_user())
    canal.modo = "humano"
    await db.commit()
    f = ShopeeFalso()
    f.resposta_envio = {"message_id": "9000000000000000777"} if com_id else {}

    async def cliente_falso(integration):
        return f

    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_falso)

    t = AGORA - timedelta(minutes=20)
    f.conversa("E2E", msg("E2E", t, texto="quando chega meu pedido?"))
    await _rodar(db, canal, integ, f)
    conv = (await _conversas(db))["E2E"]
    assert conv.aguardando_resposta is True

    nossa = await enviar.enviar_resposta(
        db, conv, "  Olá!   Seu pedido chega amanhã.  ", user=None
    )
    assert f.enviados == [(str(COMPRADOR), "Olá! Seu pedido chega amanhã.")]
    assert nossa.status == MSG_ENVIADA and nossa.origem == ORIGEM_HUMANO
    assert nossa.externo_id == ("9000000000000000777" if com_id else None)
    await db.refresh(conv)
    assert conv.aguardando_resposta is False

    # A Shopee lista a resposta na leitura seguinte (sem id no envio, o id
    # só aparece aqui — e a pontuação pode vir diferente).
    volta = msg(
        "E2E",
        datetime.now(UTC).replace(microsecond=0),
        da_loja=True,
        texto="Olá! Seu pedido chega amanhã." if com_id else "Olá, seu pedido chega amanhã",
        mid="9000000000000000777" if com_id else "9000000000000000888",
    )
    f.conversa("E2E", volta)
    r = await _rodar(db, canal, integ, f)

    assert r.mensagens_novas == 0
    ms = await _mensagens(db, conv.id)
    assert [(m.autor, m.origem) for m in ms] == [
        (AUTOR_CLIENTE, ORIGEM_CLIENTE),
        (AUTOR_LOJA, ORIGEM_HUMANO),
    ]
    await db.refresh(nossa)
    assert nossa.externo_id == volta["message_id"] and nossa.status == MSG_ENVIADA
    await db.refresh(conv)
    assert conv.aguardando_resposta is False and conv.situacao == "respondida"


async def test_sem_permissao_do_chat_vira_sem_escopo_sem_gravar(db, make_user, teto):
    integ, canal = await _canal(db, await make_user(), cursor={"ultimo_ts": 123})
    f = ShopeeFalso()
    f.conversa("A", msg("A", AGORA - timedelta(hours=1)))
    f.erro_nao_lidas = RuntimeError("shopee_chat_nao_lidas error_permission: no permission")

    r = await _rodar(db, canal, integ, f)

    assert r.status == shopee.STATUS_SEM_ESCOPO
    assert "error_permission" in r.erro
    assert await _conversas(db) == {}
    assert canal.cursor == {"ultimo_ts": 123}
    assert f.chamadas_lista == []


async def test_nao_lidas_falhando_nao_derruba_a_leitura(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.conversa("A", msg("A", AGORA - timedelta(hours=1)))
    f.erro_nao_lidas = httpx.ConnectTimeout("lento")
    r = await _rodar(db, canal, integ, f)
    assert r.status == shopee.STATUS_OK and r.nao_lidas is None and r.conversas_novas == 1


async def test_erro_no_meio_recomeca_da_pagina_sem_pular(db, make_user, teto):
    teto(10)
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.conversa("A", msg("A", AGORA - timedelta(hours=1)))
    f.conversa("B", msg("B", AGORA - timedelta(hours=2)))
    f.conversa("C", msg("C", AGORA - timedelta(hours=3)))
    f.erro_mensagens["B"] = httpx.ReadTimeout("caiu")

    r1 = await _rodar(db, canal, integ, f)
    assert r1.status == shopee.STATUS_ERRO and r1.erro == "shopee ReadTimeout"
    assert set(await _conversas(db)) == {"A", "B"}  # B nasceu, mas sem mensagem
    assert canal.cursor["ultimo_ts"] is None  # não andou
    assert canal.cursor["falhas"] == {"B": 1}

    del f.erro_mensagens["B"]
    r2 = await _rodar(db, canal, integ, f)
    assert r2.status == shopee.STATUS_OK
    assert set(await _conversas(db)) == {"A", "B", "C"}
    assert await _total_mensagens(db) == 3
    assert canal.cursor["falhas"] == {}
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(hours=1))


async def test_conversa_que_sempre_falha_e_pulada_na_terceira(db, make_user, teto):
    integ, canal = await _canal(db, await make_user())
    f = ShopeeFalso()
    f.conversa("A", msg("A", AGORA - timedelta(hours=1)))
    f.conversa("RUIM", msg("RUIM", AGORA - timedelta(hours=2)))
    f.conversa("C", msg("C", AGORA - timedelta(hours=3)))
    f.erro_mensagens["RUIM"] = RuntimeError("shopee_chat_mensagens error_param: bad")

    for _ in range(2):
        r = await _rodar(db, canal, integ, f)
        assert r.status == shopee.STATUS_ERRO and r.erro == "shopee error_param"
    r3 = await _rodar(db, canal, integ, f)
    assert r3.status == shopee.STATUS_OK
    assert "C" in await _conversas(db)
    assert canal.cursor["falhas"] == {}
    assert canal.cursor["ultimo_ts"] == _ns(AGORA - timedelta(hours=1))


# ─────────────── envio ───────────────


async def _conversa_para_envio(db, make_user, comprador_id: str | None = str(COMPRADOR)):
    integ, canal = await _canal(db, await make_user())
    conv = AtendimentoConversa(
        canal_id=canal.id,
        integration_id=integ.id,
        plataforma="shopee",
        canal="chat",
        externo_id="conv-envio",
        comprador_id=comprador_id,
    )
    db.add(conv)
    await db.commit()
    return integ, conv


async def test_envio_ok_devolve_o_id_da_shopee(db, make_user):
    integ, conv = await _conversa_para_envio(db, make_user)
    f = ShopeeFalso()
    f.resposta_envio = {
        "to_id": COMPRADOR,
        "message_id": "2302748948493123953",
        "conversation_id": "x",
    }
    r = await shopee.enviar_texto(db, conv, integ, f, "Seu pedido foi enviado.")
    assert r.ok and not r.ambiguo and r.externo_id == "2302748948493123953"
    assert f.enviados == [(str(COMPRADOR), "Seu pedido foi enviado.")]


async def test_envio_ok_sem_message_id_fica_para_a_adocao(db, make_user):
    integ, conv = await _conversa_para_envio(db, make_user)
    f = ShopeeFalso()
    f.resposta_envio = {}
    r = await shopee.enviar_texto(db, conv, integ, f, "oi")
    assert r.ok and r.externo_id is None


@pytest.mark.parametrize(
    ("erro", "ambiguo", "texto_erro"),
    [
        (httpx.ReadTimeout("t"), True, "shopee ReadTimeout"),
        (httpx.ConnectError("c"), True, "shopee ConnectError"),
        (RuntimeError("shopee_chat_send HTTP 502: <html>bad gateway"), True, None),
        (RuntimeError("shopee_chat_send error_server: tente de novo"), True, None),
        (RuntimeError("shopee_chat_send error_param: invalid to_id"), False, "shopee error_param"),
        (
            RuntimeError("shopee_chat_send user_is_forbidden: janela"),
            False,
            "shopee user_is_forbidden",
        ),
        (RuntimeError("missing refresh_token"), False, "shopee sem_refresh_token"),
        (KeyError("x"), True, "shopee KeyError"),
    ],
)
async def test_envio_com_erro_nunca_levanta(db, make_user, erro, ambiguo, texto_erro):
    integ, conv = await _conversa_para_envio(db, make_user)
    f = ShopeeFalso()
    f.resposta_envio = erro
    texto = "Texto que não pode ir para o erro"
    r = await shopee.enviar_texto(db, conv, integ, f, texto)
    assert r.ok is False
    assert r.ambiguo is ambiguo
    if texto_erro is not None:
        assert r.erro == texto_erro
    assert texto not in (r.erro or "")


async def test_envio_sem_comprador_ou_vazio_nem_chama_a_shopee(db, make_user):
    integ, conv = await _conversa_para_envio(db, make_user, comprador_id=None)
    f = ShopeeFalso()
    r = await shopee.enviar_texto(db, conv, integ, f, "oi")
    assert (r.ok, r.ambiguo, r.erro) == (False, False, "shopee sem_comprador")
    conv.comprador_id = str(COMPRADOR)
    await db.commit()
    r2 = await shopee.enviar_texto(db, conv, integ, f, "   ")
    assert (r2.ok, r2.erro) == (False, "shopee texto_vazio")
    assert f.enviados == []


async def test_envio_com_to_id_invalido_nao_saiu(db, make_user):
    integ, conv = await _conversa_para_envio(db, make_user, comprador_id="abc")
    cliente = ShopeeClient({"shop_id": SHOP, "access_token": "t", "expires_at": 9_999_999_999})
    r = await shopee.enviar_texto(db, conv, integ, cliente, "oi")
    assert (r.ok, r.ambiguo, r.erro) == (False, False, "shopee envio_invalido")


# ─────────────── métodos novos do ShopeeClient ───────────────


@pytest.fixture
def cliente_gravando(monkeypatch):
    chamadas: list[dict] = []
    respostas: dict[str, dict] = {}

    async def _call(self, method, path, *, params=None, json=None, what="shopee"):
        chamadas.append({"method": method, "path": path, "params": params, "what": what})
        return respostas.get(path, {})

    monkeypatch.setattr(ShopeeClient, "_call", _call)
    cliente = ShopeeClient({"shop_id": SHOP, "access_token": "t", "expires_at": 9_999_999_999})
    return cliente, chamadas, respostas


async def test_cliente_lista_de_conversas(cliente_gravando):
    cliente, chamadas, respostas = cliente_gravando
    respostas["/api/v2/sellerchat/get_conversation_list"] = {"conversations": [], "page_result": {}}
    await cliente.chat_conversation_list()
    await cliente.chat_conversation_list(
        direction="older", page_size=10, next_timestamp_nano="1758801601000000000"
    )
    await cliente.chat_conversation_list(page_size=60)
    assert chamadas[0]["method"] == "GET"
    # O padrão é `older` (as mais novas primeiro): `latest` sem horário lê
    # as conversas de 2023 (medido em 28/09).
    assert chamadas[0]["params"] == {"direction": "older", "type": "all", "page_size": 25}
    assert chamadas[1]["params"] == {
        "direction": "older",
        "type": "all",
        "page_size": 10,
        "next_timestamp_nano": 1758801601000000000,
    }
    # 60 voltava lista vazia em algumas lojas: o cliente nunca pede mais de 25.
    assert chamadas[2]["params"]["page_size"] == 25


async def test_cliente_nao_lidas_uma_conversa_e_pagina_de_mensagens(cliente_gravando):
    cliente, chamadas, respostas = cliente_gravando
    respostas["/api/v2/sellerchat/get_unread_conversation_count"] = {"total_unread_count": 7}
    assert await cliente.chat_unread_count() == 7
    await cliente.chat_one_conversation(123)
    assert chamadas[-1]["path"] == "/api/v2/sellerchat/get_one_conversation"
    assert chamadas[-1]["params"] == {"conversation_id": "123"}
    await cliente.chat_mensagens_pagina("123")
    assert chamadas[-1]["params"] == {"conversation_id": "123", "page_size": 25}
    await cliente.chat_mensagens_pagina("123", offset="2302748948493123953", page_size=200)
    assert chamadas[-1]["path"] == "/api/v2/sellerchat/get_message"
    assert chamadas[-1]["params"] == {
        "conversation_id": "123",
        "page_size": 50,
        "offset": "2302748948493123953",
    }


def test_cliente_nao_tem_como_marcar_como_lido():
    """Enquanto o Duoke estiver ligado, o "não lido" da Shopee é da equipe."""
    nomes = [n.lower() for n in dir(ShopeeClient)]
    assert not [n for n in nomes if "read_conversation" in n or "marcar_lid" in n]


_AsyncClientReal = httpx.AsyncClient


async def test_nenhum_pedido_de_marcar_como_lido_sai_do_cliente_de_verdade(
    db, make_user, teto, monkeypatch
):
    """COMPL-7: o teste acima só olha NOMES de método — um POST a
    `read_conversation` dentro de `chat_mensagens_pagina` passaria. Aqui a
    rodada inteira (não lidas, lista, mensagens, reconferência) e o envio
    rodam com o `ShopeeClient` de VERDADE sobre um transporte falso, e cada
    pedido que sairia para a Shopee é conferido."""
    feitos: list[httpx.Request] = []
    t = AGORA - timedelta(minutes=5)
    conv = ShopeeFalso().conversa("R1", msg("R1", t, texto="oi"))
    mensagens = [msg("R1", t, texto="oi", mid="7000000000000000001")]

    def api(request: httpx.Request) -> httpx.Response:
        feitos.append(request)
        caminho = request.url.path
        if caminho.endswith("/get_unread_conversation_count"):
            resposta = {"total_unread_count": 1}
        elif caminho.endswith("/get_conversation_list"):
            resposta = {"conversations": [conv], "page_result": {"more": False}}
        elif caminho.endswith("/get_message"):
            resposta = {"messages": mensagens, "page_result": {"next_offset": ""}}
        elif caminho.endswith("/send_message"):
            resposta = {"message_id": "9000000000000000999", "to_id": COMPRADOR}
        else:
            return httpx.Response(404, json={"error": "error_not_found", "message": caminho})
        return httpx.Response(200, json={"error": "", "message": "", "response": resposta})

    def fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(api)
        return _AsyncClientReal(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fabrica)
    cliente = ShopeeClient({"shop_id": SHOP, "access_token": "t", "expires_at": 9_999_999_999})
    integ, canal = await _canal(db, await make_user())

    r = await _rodar(db, canal, integ, cliente)
    assert (r.status, r.conversas_novas) == (shopee.STATUS_OK, 1)
    leitura = list(feitos)
    conversa = (await _conversas(db))["R1"]
    envio = await shopee.enviar_texto(db, conversa, integ, cliente, "Chega amanhã.")
    assert envio.ok
    # A resposta enviada agora entra na reconferência da rodada seguinte.
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=envio.externo_id,
            autor=AUTOR_LOJA,
            origem=ORIGEM_HUMANO,
            texto="Chega amanhã.",
            status=MSG_ENVIADA,
            enviada_em=datetime.now(UTC),
        )
    )
    await db.commit()
    await _rodar(db, canal, integ, cliente)

    assert any(p.url.path.endswith("/get_message") for p in feitos)
    # Lista FECHADA do que pode sair: qualquer outro endpoint (read_conversation,
    # um "mark" qualquer) reprova — não só o nome que alguém lembrou de proibir.
    permitidos = {
        "/api/v2/sellerchat/get_unread_conversation_count",
        "/api/v2/sellerchat/get_conversation_list",
        "/api/v2/sellerchat/get_message",
        "/api/v2/sellerchat/send_message",
    }
    assert {p.url.path for p in feitos} <= permitidos
    assert not [p for p in feitos if p.url.path.rsplit("/", 1)[-1] == "read_conversation"]
    # Ler nunca escreve: toda chamada da leitura é GET.
    assert {p.method for p in leitura} == {"GET"}
    assert [p.url.path for p in feitos if p.method != "GET"] == [
        "/api/v2/sellerchat/send_message"
    ]


# ─────────────── DADOS-5: a rodada não segura a conversa nem o canal ───────────────


async def test_rodada_nao_trava_conversa_ja_gravada_nem_o_canal(db: AsyncSession, make_user):
    """Enquanto a rodada fala com a Shopee sobre a conversa B, a tela consegue
    travar (Enviar, Fechar) a conversa A, já gravada, e o canal (trocar o
    modo). Antes, tudo ficava travado até o fim da rodada."""
    from sqlalchemy import text

    import app.db as _db

    user = await make_user()
    integ, canal = await _canal(db, user)
    f = ShopeeFalso()
    f.nao_lidas = 2
    f.conversa("A", msg("A", AGORA - timedelta(minutes=5), texto="cadê meu pedido?"))
    f.conversa("B", msg("B", AGORA - timedelta(minutes=10), texto="tem nota fiscal?"))
    travas: dict[str, bool] = {}
    original = f.chat_mensagens_pagina

    async def _tenta_travar(sql: str, params: dict) -> bool:
        async with _db.SessionLocal() as tela:
            await tela.execute(text("SET LOCAL lock_timeout = '300ms'"))
            try:
                await tela.execute(text(sql), params)
            except Exception:  # noqa: BLE001 — lock_timeout = travada
                await tela.rollback()
                return False
            await tela.rollback()
            return True

    async def mensagens_com_espia(conversation_id, **kw):
        if conversation_id == "B":
            travas["conversa_A"] = await _tenta_travar(
                "SELECT id FROM atendimento_conversas WHERE externo_id = :e"
                " FOR NO KEY UPDATE",
                {"e": "A"},
            )
            travas["canal"] = await _tenta_travar(
                "SELECT id FROM atendimento_canais WHERE id = :i FOR NO KEY UPDATE",
                {"i": canal.id},
            )
        return await original(conversation_id, **kw)

    f.chat_mensagens_pagina = mensagens_com_espia

    r = await _rodar(db, canal, integ, f)

    assert r.status == shopee.STATUS_OK and r.mensagens_novas == 2
    assert travas == {"conversa_A": True, "canal": True}
