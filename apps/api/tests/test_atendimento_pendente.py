"""O "Falta responder" da Caixa IGUAL AO DUOKE (14/10/2026).

O dono: "tem que arrumar pro atendimento ter literalmente o comportamento do
Duoke, pra estar marcado literalmente as mesmas" — a Caixa mostrava Shopee 99+
e o "Pendente" do Duoke, 15. O que não pode falhar
(`services/atendimento/pendente.py`):

  • a régua: não resolvida E (a última mensagem que não é do sistema é do
    comprador — qualquer mensagem da loja responde, até a automática do
    robô/campanha — OU não lida no chat da Shopee/TikTok, com fala do
    comprador); a de `sistema`/`mediador` não conta para nenhum lado, a que
    falhou não saiu, a nota não é mensagem;
    fechada e "não precisa de resposta" saem; a bloqueada fica; o não lido do
    ML não conta (ele não zera quando a loja responde); onde a plataforma diz
    de quem é a vez (reclamação…) e no e-mail da ponte, vale a fila;
  • Python (a linha) e SQL (os números e o filtro) são a MESMA régua;
  • a `ultima_loja_qualquer_em` que a régua lê: o `gravar.recalcular` e o
    `recalcular_conversa` mantêm, e a migration 0396 preenche igual;
  • o /resumo (por plataforma e por loja) e o filtro "Falta responder" da
    Caixa contam por ela — a FILA (`aguardando_resposta`, o prazo, vencendo/
    vencidas) não muda; a lista "Falta responder" traz quem não tem prazo no
    fim, a mais recente primeiro, sem pular nem repetir no "carregar mais";
  • o NÃO LIDO fresco: Shopee (total + lista `type=unread`), TikTok (a lista
    relida em passadas que continuam na rodada seguinte, só pelas não lidas
    que contam) e ML pós-venda (a lista de não lidos acerta todo pack
    gravado) — falha não zera nada, lista cortada não zera ninguém, e nenhum
    "marcar como lido" sai;
  • SÓ O CHAT e o NÚMERO DO DUOKE (14/10/2026, o dono: "o nosso ATV mostra
    9, o deles 5 … tá pegando uns antigos de avaliação"): o filtro e o
    número da aba "Falta responder" deixam de fora a avaliação, a
    reclamação, o carrinho e os comentários (a marca da linha fica em
    todas); os números da Caixa (`nao_lidas_duoke`: menu de plataformas,
    barra de lojas, aba Caixa) eram, até 15/10, a soma das mensagens não lidas das
    conversas de chat esperando resposta na fila, não fechadas — 1 por
    conversa onde a plataforma não dá o não lido —, Python e SQL gêmeos;
    só conta a conversa que está no "Falta responder" (o não lido do ML não
    zera quando a automática responde: o número não acende sem linha
    marcada); a Caixa Humano não muda;
  • O NÚMERO = O "NÃO LIDA" DO DUOKE (15/10/2026, o dono: "os números da
    Caixa têm que bater com o Duoke"): nas conversas de chat esperando
    resposta na fila, com o comprador nas últimas 24 h, as mensagens DELE
    depois da última resposta de PESSOA da loja ou da última abertura no
    DaVinci (`visto_em`) — o robô não zera, a pessoa e o "visto" zeram, o
    não lido da plataforma não entra, fechada/sem fila/fora do chat não
    contam; Python e SQL gêmeos; o /resumo soma por loja e plataforma, e
    abrir a conversa (POST /visto) zera o número dela.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoMensagem, UserRole
from app.services.atendimento import gravar, pendente, shopee, tiktok
from app.services.atendimento import ml as ml_atd
from app.services.atendimento.constantes import CHAVE_VEZ_DA_LOJA
from app.services.marketplaces.shopee import ShopeeClient
from tests import test_atendimento_ml as tml
from tests import test_atendimento_shopee as tsh
from tests import test_atendimento_tiktok as ttk
from tests.test_atendimento_router import (  # noqa: F401 — as autouse da API valem aqui
    PODE_TUDO,
    _cerebro_falso,
    _chaves,
    _loja,
    _permissao_fina,
)

_ALEMBIC = Path(__file__).resolve().parents[1] / "alembic"
_spec = importlib.util.spec_from_file_location(
    "m0396", _ALEMBIC / "versions" / "0396_ultima_loja_qualquer.py"
)
m0396 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0396)

URL = "/api/atendimento"
AGORA = datetime.now(UTC).replace(microsecond=0)
MENU_DUOKE = "Olá, por favor selecione sua dúvida e logo te atenderemos: 1 - Entrega"
JA_SEGUE = "fulano já segue nossa loja aqui na Shopee? Ganhe um cupom"


# ─────────────── fábrica ───────────────


async def _cena(db: AsyncSession, integ, canal, externo: str, *falas, **campos):
    """Uma conversa com as falas `(autor, minutos atrás[, texto])`, na ordem dada.

    `campos` vão direto na conversa (situação, não lidas, "não precisa",
    `dados`) e a fila é refeita (`recalcular_conversa`); `plataforma` e
    `canal_nome` (padrão: o chat da Shopee) vão na criação.
    """
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=campos.pop("plataforma", "shopee"),
        canal_nome=campos.pop("canal_nome", "chat"),
        externo_id=externo,
        comprador_nome="Comprador",
    )
    for i, fala in enumerate(falas):
        autor, minutos, *resto = fala
        await gravar.gravar_mensagem(
            db,
            conversa,
            externo_id=f"{externo}-{i}",
            autor=autor,
            texto=resto[0] if resto else f"{autor} {i}",
            enviada_em=AGORA - timedelta(minutes=minutos),
        )
    if campos:
        for nome, valor in campos.items():
            setattr(conversa, nome, valor)
        await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    return conversa


async def _nossa(db, conversa, minutos: int, **campos) -> AtendimentoMensagem:
    """Uma linha NOSSA gravada direto (a que falhou, a nota, o e-mail da ponte)."""
    m = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=campos.pop("externo_id", f"nossa:{uuid4()}"),
        autor=campos.pop("autor", "loja"),
        origem=campos.pop("origem", "davinci_humano"),
        tipo=campos.pop("tipo", "texto"),
        texto=campos.pop("texto", "resposta"),
        enviada_em=AGORA - timedelta(minutes=minutos),
        **campos,
    )
    db.add(m)
    await db.flush()
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    return m


async def _mundo(db, make_user) -> dict[str, tuple[AtendimentoConversa, bool]]:
    """Cada caso da régua → (conversa, falta responder?)."""
    integ, canal = await _loja(db, await make_user(), "kfa")
    c = {}

    async def caso(nome, esperado, *falas, **campos):
        c[nome] = (await _cena(db, integ, canal, nome, *falas, **campos), esperado)

    await caso("cliente_por_ultimo", True, ("loja", 90), ("cliente", 60))
    # O menu do robô do Duoke depois do comprador: para o Duoke, respondeu.
    await caso("menu_do_duoke", False, ("cliente", 60), ("loja", 59, MENU_DUOKE))
    # ...mas não lida na plataforma: pendente.
    await caso("menu_e_nao_lida", True, ("cliente", 60), ("loja", 59, MENU_DUOKE), nao_lidas=2)
    # O aviso do sistema depois da resposta não reabre; depois do comprador não fecha.
    aviso = ("sistema", 40, JA_SEGUE)
    await caso("sistema_depois_da_loja", False, ("cliente", 60), ("loja", 50), aviso)
    await caso("sistema_depois_do_cliente", True, ("loja", 60), ("cliente", 50), aviso)
    await caso("mediador_depois", True, ("cliente", 60), ("mediador", 30))
    await caso("loja_por_ultimo", False, ("cliente", 60), ("loja", 30))
    await caso("so_loja", False, ("loja", 30))
    await caso("sem_mensagem", False)
    # A Shopee lista com não lida, mas não devolve mensagem nenhuma (Inova,
    # 14/10/2026): o Duoke não mostra.
    await caso("nao_lida_sem_mensagem", False, nao_lidas=1)
    # A conversa SÓ de aviso do sistema fica não lida na Shopee/TikTok e o
    # Duoke não mostra (medido 14/10/2026); o aviso DEPOIS da fala do
    # comprador não tira (duas das 15 do Duoke: comprador › menu › aviso).
    await caso("nao_lida_so_sistema", False, ("sistema", 30, JA_SEGUE), nao_lidas=1)
    await caso(
        "nao_lida_aviso_por_ultimo",
        True,
        ("cliente", 60),
        ("loja", 59, MENU_DUOKE),
        ("sistema", 30, JA_SEGUE),
        nao_lidas=2,
    )
    # TikTok: igual à Shopee (o não lido zera quando alguém abre).
    await caso(
        "tiktok_menu_e_nao_lida",
        True,
        ("cliente", 60),
        ("loja", 59, MENU_DUOKE),
        nao_lidas=1,
        plataforma="tiktok",
    )
    # ML pós-venda: o não lido do ML não zera quando a loja responde — a loja
    # respondeu depois do comprador, então não falta responder.
    await caso(
        "ml_respondida_e_nao_lida",
        False,
        ("cliente", 3 * 24 * 60),
        ("loja", 2 * 24 * 60),
        nao_lidas=2,
        plataforma="ml",
        canal_nome="pos_venda",
    )
    await caso(
        "ml_cliente_por_ultimo",
        True,
        ("loja", 90),
        ("cliente", 60),
        plataforma="ml",
        canal_nome="pos_venda",
    )
    # Onde a PLATAFORMA diz de quem é a vez (a reclamação), vale a fila: a
    # vez da loja só com o aviso do mediador está no "Falta responder"...
    await caso(
        "reclamacao_vez_da_loja",
        True,
        ("sistema", 60),
        dados={CHAVE_VEZ_DA_LOJA: (AGORA - timedelta(minutes=60)).isoformat()},
        canal_nome="reclamacao",
    )
    # ...e o comprador por último quando a plataforma diz que não é a vez da loja, não.
    await caso(
        "reclamacao_vez_do_comprador",
        False,
        ("loja", 90),
        ("cliente", 60),
        dados={CHAVE_VEZ_DA_LOJA: None},
        canal_nome="reclamacao",
    )
    # O e-mail da ponte: a régua da aba E-mail é a fila (a automática não responde).
    await caso(
        "email_da_ponte",
        True,
        ("cliente", 60),
        ("loja", 59, MENU_DUOKE),
        dados={"fonte": "tuta"},
        canal_nome="email",
    )
    await caso("fechada", False, ("cliente", 60), situacao="fechada")
    await caso("fechada_nao_lida", False, ("cliente", 60), situacao="fechada", nao_lidas=1)
    await caso("nao_precisa", False, ("cliente", 60), sem_resposta_necessaria=True)
    await caso("bloqueada", True, ("cliente", 60), situacao="bloqueada")
    # A resposta que falhou não saiu; a nota interna não é mensagem.
    falhou = await _cena(db, integ, canal, "falhou", ("cliente", 60))
    await _nossa(db, falhou, 30, status="falhou")
    c["falhou"] = (falhou, True)
    nota = await _cena(db, integ, canal, "nota", ("cliente", 60))
    await _nossa(db, nota, 30, origem="davinci_nota", tipo="nota", texto="nota interna")
    c["nota"] = (nota, True)
    # O e-mail da ponte gravado DENTRO do chat (antes de 13/10/2026) não aparece nele.
    email = await _cena(db, integ, canal, "email_no_chat", ("cliente", 60))
    await _nossa(db, email, 30, payload={"mail": {"message_id": "x"}})
    c["email_no_chat"] = (email, True)
    return c


# ─────────────── a régua ───────────────


async def test_a_regua_python_e_sql_sao_gemeas(db, make_user):
    mundo = await _mundo(db, make_user)
    for nome, (conversa, esperado) in mundo.items():
        await db.refresh(conversa)
        assert pendente.falta_responder(conversa) is esperado, nome
    no_sql = set(
        (
            await db.execute(
                select(AtendimentoConversa.externo_id).where(pendente.falta_responder_sql())
            )
        )
        .scalars()
        .all()
    )
    assert no_sql == {nome for nome, (_, esperado) in mundo.items() if esperado}


async def test_a_fila_de_verdade_nao_muda(db, make_user):
    """O menu do robô continua NÃO fechando a vez do comprador na fila (a IA, o
    prazo, a Caixa Humano) — só o "Falta responder" da Caixa conta ele."""
    mundo = await _mundo(db, make_user)
    menu = mundo["menu_do_duoke"][0]
    await db.refresh(menu)
    assert menu.aguardando_resposta is True and menu.prazo_resposta_em is not None
    assert menu.ultima_da_loja_em is None, "a automática não é resposta na fila"
    assert gravar._utc(menu.ultima_loja_qualquer_em) == AGORA - timedelta(minutes=59)
    loja = mundo["loja_por_ultimo"][0]
    await db.refresh(loja)
    assert loja.ultima_da_loja_em == loja.ultima_loja_qualquer_em


async def test_recalcular_do_zero_da_o_mesmo_carimbo(db, make_user):
    """`recalcular_conversa` (a pessoa fechou, o envio falhou) refaz a
    `ultima_loja_qualquer_em` igual à gravação mensagem a mensagem."""
    mundo = await _mundo(db, make_user)
    for nome, (conversa, _) in mundo.items():
        await db.refresh(conversa)
        antes = gravar._utc(conversa.ultima_loja_qualquer_em)
        await gravar.recalcular_conversa(db, conversa)
        assert gravar._utc(conversa.ultima_loja_qualquer_em) == antes, nome
    await db.rollback()


async def test_migration_0396_preenche_igual_ao_recalculo(db, make_user):
    mundo = await _mundo(db, make_user)
    esperado = {}
    for conversa, _ in mundo.values():
        await db.refresh(conversa)
        esperado[conversa.id] = gravar._utc(conversa.ultima_loja_qualquer_em)
    assert esperado[mundo["menu_do_duoke"][0].id] is not None
    assert esperado[mundo["falhou"][0].id] is None, "só a resposta que falhou"
    assert esperado[mundo["nota"][0].id] is None and esperado[mundo["email_no_chat"][0].id] is None
    await db.execute(update(AtendimentoConversa).values(ultima_loja_qualquer_em=None))
    await db.commit()
    conexao = await db.connection()
    schema = get_settings().database_schema
    mudaram = await conexao.run_sync(lambda c: m0396.preencher(c, schema))
    await db.commit()
    assert mudaram == sum(1 for v in esperado.values() if v is not None)
    db.expire_all()
    for cid, valor in esperado.items():
        c = await db.get(AtendimentoConversa, cid)
        assert gravar._utc(c.ultima_loja_qualquer_em) == valor
    # Rodar de novo não muda nada.
    conexao = await db.connection()
    assert await conexao.run_sync(lambda c: m0396.preencher(c, schema)) == 0
    await db.rollback()


# ─────────────── a tela: /resumo e a lista ───────────────


async def test_resumo_e_lista_contam_pela_regua_do_duoke(
    client, db, make_user, auth_as, monkeypatch
):
    mundo = await _mundo(db, make_user)
    auth_as(await make_user(permissions=PODE_TUDO))
    # O e-mail da ponte mora na aba E-mail: não conta na Caixa.
    na_caixa = {n: (c, e) for n, (c, e) in mundo.items() if n != "email_da_ponte"}
    # O filtro e o número são SÓ O CHAT (14/10/2026): a reclamação tem o filtro
    # dela; a marca da linha continua em todas.
    marcadas_esperadas = {nome for nome, (_, e) in na_caixa.items() if e}
    esperados = {nome for nome, (c, e) in na_caixa.items() if e and pendente.do_chat(c)}
    assert marcadas_esperadas - esperados == {"reclamacao_vez_da_loja"}
    r = (await client.get(f"{URL}/resumo")).json()
    for plat in ("shopee", "ml", "tiktok"):
        da_plat = sum(
            1 for n, (c, _) in na_caixa.items() if n in esperados and c.plataforma == plat
        )
        p = next(p for p in r["plataformas"] if p["plataforma"] == plat)
        assert p["aguardando"] == da_plat, plat
        (loja,) = [lj for lj in r["lojas"] if lj["conta"] == "kfa" and lj["plataforma"] == plat]
        assert loja["aguardando"] == da_plat, plat
        # Vencendo/vencidas seguem o PRAZO da fila (nada perto de vencer aqui).
        assert (p["vencendo"], p["vencidas"]) == (0, 0), plat

    r = await client.get(f"{URL}/conversas", params={"filtro": "aguardando", "limite": 100})
    assert r.status_code == 200, r.text
    itens = r.json()["itens"]
    nomes = {str(c.id): n for n, (c, _) in mundo.items()}
    assert {nomes[i["id"]] for i in itens} == esperados
    assert all(i["falta_responder"] for i in itens)
    # Em "Todas", a linha diz se está no "Falta responder" (a tela relê os números).
    todas = (await client.get(f"{URL}/conversas", params={"limite": 100})).json()["itens"]
    marcadas = {nomes[i["id"]] for i in todas if i["falta_responder"]}
    assert marcadas == marcadas_esperadas
    menu = next(i for i in todas if nomes[i["id"]] == "menu_do_duoke")
    assert menu["aguardando_resposta"] is True and menu["falta_responder"] is False


async def test_reclamacao_vencida_fica_em_vencidas_e_fora_do_falta_responder(
    client, db, make_user, auth_as
):
    """A reclamação em que a plataforma diz que é a vez da loja (só o aviso do
    mediador), com o prazo vencido: está em "Vencidas" e marcada — mas, desde
    14/10/2026 (só o chat), fora do filtro e do número "Falta responder" e do
    número do Duoke (a reclamação tem o filtro dela)."""
    integ, canal = await _loja(db, await make_user(), "kfa")
    await _cena(
        db,
        integ,
        canal,
        "rec",
        ("sistema", 3 * 24 * 60),
        dados={CHAVE_VEZ_DA_LOJA: (AGORA - timedelta(days=3)).isoformat()},
        canal_nome="reclamacao",
    )
    auth_as(await make_user(permissions=PODE_TUDO))
    r = (await client.get(f"{URL}/resumo")).json()
    sh = next(p for p in r["plataformas"] if p["plataforma"] == "shopee")
    assert (sh["aguardando"], sh["vencidas"], sh["nao_lidas_duoke"]) == (0, 1, 0)
    itens = (
        await client.get(f"{URL}/conversas", params={"filtro": "aguardando", "limite": 100})
    ).json()["itens"]
    assert itens == []
    itens = (
        await client.get(f"{URL}/conversas", params={"filtro": "vencidas", "limite": 100})
    ).json()["itens"]
    assert [i["falta_responder"] for i in itens] == [True]


# ─────────────── só o chat e o número do Duoke (14 e 15/10/2026) ───────────────
#
# O número da Caixa = o "não lida" do PRÓPRIO Duoke (15/10/2026): nas
# conversas de chat esperando resposta com o comprador nas últimas 24 h, as
# mensagens dele depois da última resposta de PESSOA da loja ou da última vez
# que alguém abriu a conversa no DaVinci (`visto_em`).

HORA = 60  # minutos
ATRASO_DO_SYNC = timedelta(minutes=1)


async def _mundo_duoke(
    db, make_user, nome_loja: str = "atv"
) -> dict[str, tuple[AtendimentoConversa, int]]:
    """Cada caso do número do Duoke → (conversa, quanto ela soma)."""
    integ, canal = await _loja(db, await make_user(), nome_loja)
    c = {}

    async def caso(nome, esperado, *falas, **campos):
        c[nome] = (await _cena(db, integ, canal, f"{nome_loja}:{nome}", *falas, **campos), esperado)

    vez = {CHAVE_VEZ_DA_LOJA: (AGORA - timedelta(minutes=60)).isoformat()}

    def visto(minutos: float) -> datetime:
        """O `visto_em` de quem abriu a conversa no DaVinci há `minutos` (a
        chegada da mensagem mais nova que a tela mostrava — no fim, todas as
        mensagens daqui CHEGAM 1 min depois de mandadas: `ATRASO_DO_SYNC`)."""
        return AGORA - timedelta(minutes=minutos)

    # O comprador escreveu 4 vezes e ninguém respondeu: 4.
    await caso("quatro", 4, ("cliente", 60), ("cliente", 50), ("cliente", 40), ("cliente", 30))
    # O ROBÔ NÃO ZERA: o menu do Duoke depois do comprador não é resposta de
    # pessoa (a Shopee zera o não lido dela; o Duoke continua contando).
    await caso(
        "robo_nao_zera",
        3,
        ("cliente", 60),
        ("loja", 59, MENU_DUOKE),
        ("cliente", 50),
        ("cliente", 40),
    )
    # O robô por ÚLTIMO: para o "Falta responder" (e para o Duoke na lista
    # "Pendente") respondeu — sai da marca —, mas o número dele continua.
    await caso(
        "robo_por_ultimo",
        2,
        ("cliente", 60),
        ("cliente", 55),
        ("loja", 54, MENU_DUOKE),
    )
    # A resposta de PESSOA zera: só o que o comprador mandou depois dela.
    await caso(
        "pessoa_zera",
        2,
        ("cliente", 90),
        ("cliente", 80),
        ("loja", 70),
        ("cliente", 60),
        ("cliente", 50),
    )
    # A pessoa respondeu por último: não espera resposta.
    await caso("respondida", 0, ("cliente", 60), ("loja", 30), nao_lidas=2)
    # ABRIR NO DAVINCI ZERA (`visto_em`): só o que chegou depois de abrir.
    await caso(
        "visto_zera",
        1,
        ("cliente", 90),
        ("cliente", 80),
        ("cliente", 30),
        visto_em=visto(60),
    )
    await caso("visto_depois_de_tudo", 0, ("cliente", 60), ("cliente", 50), visto_em=visto(10))
    # O visto é no relógio do DAVINCI (a CHEGADA): a mensagem mandada ANTES
    # de abrir (3 min atrás) que o sync trouxe DEPOIS (2 min atrás) a pessoa
    # não viu — conta (a revisão de 15/10/2026: comparando com o `enviada_em`,
    # ela sumia do número para sempre). A de 5 min (chegou 4 min atrás), não.
    await caso(
        "chegou_depois_de_abrir",
        1,
        ("cliente", 5),
        ("cliente", 3),
        visto_em=visto(2.5),
    )
    # O mais recente entre a resposta de pessoa e a abertura vale.
    await caso(
        "pessoa_depois_do_visto",
        1,
        ("cliente", 120),
        ("loja", 80),
        ("cliente", 60),
        visto_em=visto(100),
    )
    # Sem janela de 24 h (como no Duoke, que acumula até alguém abrir,
    # responder ou zerar): a de 2 dias atrás conta; a de mais de 7 dias
    # (`JANELA_DO_NUMERO`), não — mesmo que a conversa tenha mensagem nova.
    await caso("dois_dias", 2, ("cliente", 50 * HORA), ("cliente", 48 * HORA))
    await caso(
        "fora_dos_7_dias", 1, ("cliente", 8 * 24 * HORA), ("cliente", 7 * 24 * HORA + 30),
        ("sistema", 60, JA_SEGUE), ("cliente", 30),
    )
    # A mensagem `sistema` que o PRÓPRIO comprador mandou conta (15/10/2026):
    # a pergunta do FAQ e a resposta automática da conta dele (`from_id` = o
    # `comprador_id`). Ela não abre a vez dele na fila — o número não depende
    # da fila. A do sistema da loja (o "já segue") continua fora.
    faq = await _cena(
        db, integ, canal, f"{nome_loja}:faq_do_comprador", ("cliente", 90), ("loja", 80)
    )
    await _nossa(
        db, faq, 60, autor="sistema", origem="sistema", tipo="outro",
        texto="[Resposta automática (FAQ)]",
        payload={"message_type": "bundle_message", "from_id": 555, "source": "server"},
    )
    await _nossa(
        db, faq, 59, autor="sistema", origem="sistema", texto=JA_SEGUE,
        payload={"message_type": "text", "from_id": 777, "status": "auto_reply"},
    )
    faq.comprador_id = "555"
    await db.commit()
    c["faq_do_comprador"] = (faq, 1)
    # O TURNO SÓ DE CARTÃO (15/10/2026, a 3ª medição): o comprador só mandou
    # o cartão do produto e a campanha do Duoke (a figurinha 0007, 26 h
    # depois) fecha a vez dele na FILA (`ultima_da_loja_em`) — mas no Duoke o
    # cartão continua não lido: o número acha a resposta de PESSOA nas
    # mensagens (`resposta_de_pessoa`) e conta 1.
    figurinha = {"source": "openapi", "message_type": "sticker"}
    cartao = await _cena(db, integ, canal, f"{nome_loja}:so_cartao_e_campanha")
    await _nossa(
        db, cartao, 120, autor="cliente", origem="cliente", tipo="produto", texto="",
        payload={"message_type": "item", "content": {"item_id": 1}},
    )
    await _nossa(
        db, cartao, 60, autor="loja", origem="externo", tipo="outro", texto="[Figurinha]",
        payload={**figurinha, "content": {"sticker_id": "0007"}},
    )
    c["so_cartao_e_campanha"] = (cartao, 1)
    # A figurinha 0028 da campanha "Só passando para saber…" não é pessoa:
    # nem fecha a vez, nem zera o número.
    campanha = await _cena(db, integ, canal, f"{nome_loja}:figurinha_0028", ("cliente", 90))
    await _nossa(
        db, campanha, 60, autor="loja", origem="externo", tipo="outro", texto="[Figurinha]",
        payload={**figurinha, "content": {"sticker_id": "0028"}},
    )
    c["figurinha_0028"] = (campanha, 1)
    # O não lido da PLATAFORMA não entra mais: lida lá (zero), conta igual.
    await caso("lida_na_plataforma", 1, ("cliente", 60), nao_lidas=0)
    await caso("muitas_na_plataforma", 1, ("cliente", 60), nao_lidas=9)
    # Fechada e "não precisa de resposta": fora. A bloqueada espera alguém.
    await caso("fechada", 0, ("cliente", 60), situacao="fechada", nao_lidas=3)
    await caso("nao_precisa", 0, ("cliente", 60), sem_resposta_necessaria=True, nao_lidas=2)
    await caso("bloqueada", 2, ("cliente", 60), ("cliente", 50), situacao="bloqueada")
    # O aviso do sistema não é do comprador (nem resposta).
    await caso("aviso_do_sistema", 1, ("cliente", 60), ("sistema", 50, JA_SEGUE))
    # Sem mensagem nenhuma (a Shopee lista com não lida): nada.
    await caso("sem_mensagem", 0, nao_lidas=5)
    # Fora do chat (não está no Duoke): nada, mesmo esperando.
    await caso(
        "avaliacao", 0, ("cliente", 3 * 24 * HORA), dados=vez, canal_nome="avaliacao", nao_lidas=1
    )
    await caso("reclamacao", 0, ("cliente", 60), dados=vez, canal_nome="reclamacao", nao_lidas=1)
    await caso("carrinho", 0, ("cliente", 60), dados=vez, plataforma="site", canal_nome="carrinho")
    await caso("comentario", 0, ("cliente", 60), plataforma="instagram", canal_nome="comentario")
    # O e-mail da ponte mora na aba E-mail.
    await caso("email_da_ponte", 0, ("cliente", 60), dados={"fonte": "tuta"}, canal_nome="email")
    # A mesma régua nas outras plataformas do chat.
    await caso("tiktok", 3, ("cliente", 60), ("cliente", 50), ("cliente", 40), plataforma="tiktok")
    ml = {"plataforma": "ml", "canal_nome": "pos_venda"}
    await caso("ml_pos_venda", 1, ("loja", 90), ("cliente", 60), nao_lidas=2, **ml)
    # A automática do ML 1 min depois do comprador não é resposta de pessoa:
    # conta, como no Duoke (na janela de 7 dias — as de 8 a 25 dias atrás, não).
    await caso("ml_automatica", 1, ("cliente", 60), ("loja", 59, MENU_DUOKE), nao_lidas=3, **ml)
    await caso(
        "ml_automatica_velha",
        0,
        ("cliente", 8 * 24 * HORA),
        ("loja", 8 * 24 * HORA - 1, MENU_DUOKE),
        nao_lidas=3,
        **ml,
    )
    # A pergunta do anúncio do ML não está no número da loja no Duoke (só os
    # "packs" do pós-venda) — segue no "Falta responder".
    await caso("ml_pergunta", 0, ("cliente", 60), plataforma="ml", canal_nome="pergunta")
    await caso(
        "amazon_email",
        2,
        ("cliente", 60),
        ("cliente", 50),
        plataforma="amazon",
        canal_nome="email",
    )
    await caso(
        "amazon_respondida",
        0,
        ("cliente", 60),
        ("loja", 30),
        plataforma="amazon",
        canal_nome="email",
    )
    # O SAC da Magalu: a vez é da plataforma (o aviso do mediador) — só a
    # mensagem do comprador soma.
    await caso("magalu_sac", 0, ("sistema", 60), dados=vez, plataforma="magalu", canal_nome="sac")
    # A que falhou não saiu (não zera) e a nota interna não é mensagem.
    falhou = await _cena(db, integ, canal, f"{nome_loja}:falhou_e_nota", ("cliente", 60))
    await _nossa(db, falhou, 40, status="falhou")
    await _nossa(db, falhou, 30, origem="davinci_nota", tipo="nota", texto="nota", autor="equipe")
    c["falhou_e_nota"] = (falhou, 1)
    # O e-mail da ponte gravado DENTRO do chat (antes de 13/10/2026) não aparece nele: não conta.
    email = await _cena(db, integ, canal, f"{nome_loja}:email_no_chat", ("cliente", 60))
    await _nossa(db, email, 30, autor="cliente", origem="cliente", payload={"mail": {"id": "x"}})
    c["email_no_chat"] = (email, 1)
    # O sync traz a mensagem ~1 min depois de mandada (mediana medida em
    # produção, 15/10/2026): a CHEGADA (`created_at`) de cada mensagem daqui.
    await db.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id.in_([conversa.id for conversa, _ in c.values()]))
        .values(created_at=AtendimentoMensagem.enviada_em + ATRASO_DO_SYNC)
    )
    await db.commit()
    return c


async def _mensagens_de(db, conversa) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def test_numero_do_duoke_python_e_sql_sao_gemeos(db, make_user):
    mundo = await _mundo_duoke(db, make_user)
    for nome, (conversa, esperado) in mundo.items():
        await db.refresh(conversa)
        msgs = await _mensagens_de(db, conversa)
        assert pendente.nao_lidas_duoke(conversa, msgs, agora=AGORA) == esperado, nome
    linhas = (
        await db.execute(
            select(AtendimentoConversa.externo_id, pendente.nao_lidas_duoke_sql(agora=AGORA)).where(
                AtendimentoConversa.externo_id.like("atv:%")
            )
        )
    ).all()
    assert {e.removeprefix("atv:"): n for e, n in linhas} == {
        nome: esperado for nome, (_, esperado) in mundo.items()
    }
    # A fila fecha a vez no turno só de cartão; o número, não.
    cartao = mundo["so_cartao_e_campanha"][0]
    assert not cartao.aguardando_resposta and cartao.ultima_da_loja_em is not None
    # A 0028 não fecha a vez.
    assert mundo["figurinha_0028"][0].aguardando_resposta
    # O robô NÃO zera e a fila continua igual (o menu não fecha a vez); com
    # ele por último, a linha sai do "Falta responder" e o número fica.
    robo = mundo["robo_por_ultimo"][0]
    assert robo.aguardando_resposta and robo.ultima_da_loja_em is None
    assert not pendente.falta_responder(robo), "para o Duoke, o robô respondeu: sem a marca"
    # A janela anda com o relógio: daqui a 7 dias, o que hoje conta sai.
    depois = AGORA + pendente.JANELA_DO_NUMERO
    quatro = mundo["quatro"][0]
    assert pendente.nao_lidas_duoke(quatro, await _mensagens_de(db, quatro), agora=depois) == 0
    n = (
        await db.execute(
            select(pendente.nao_lidas_duoke_sql(agora=depois)).where(
                AtendimentoConversa.id == quatro.id
            )
        )
    ).scalar_one()
    assert n == 0
    # Só as mensagens DA conversa contam (a lista de outra não soma).
    outra = mundo["tiktok"][0]
    assert pendente.nao_lidas_duoke(quatro, await _mensagens_de(db, outra), agora=AGORA) == 0
    # O "esperando" que não vale: a fechada com o carimbo da fila de antes de
    # fechar (o `gravar` zera; aqui, direto no banco).
    fechada = mundo["fechada"][0]
    await db.execute(
        update(AtendimentoConversa)
        .where(AtendimentoConversa.id == fechada.id)
        .values(situacao="fechada", aguardando_resposta=True)
    )
    await db.refresh(fechada)
    assert pendente.nao_lidas_duoke(fechada, await _mensagens_de(db, fechada), agora=AGORA) == 0
    n = (
        await db.execute(
            select(pendente.nao_lidas_duoke_sql(agora=AGORA)).where(
                AtendimentoConversa.id == fechada.id
            )
        )
    ).scalar_one()
    assert n == 0
    await db.rollback()


def test_mensagem_no_numero_dois_relogios():
    """Os dois marcos: a resposta de PESSOA no relógio da plataforma
    (`enviada_em`) e o "visto no DaVinci" no relógio do DaVinci (a CHEGADA,
    `created_at` — o sync traz a mensagem ~1 min depois)."""
    from types import SimpleNamespace

    ns = SimpleNamespace
    t = datetime(2026, 10, 15, 7, 32, tzinfo=UTC)
    m = timedelta(minutes=1)

    def msg(enviada, chegou, autor="cliente", **extra):
        return ns(autor=autor, enviada_em=enviada, created_at=chegou, payload={}, **extra)

    assert pendente.marcos_do_numero(ns(ultima_da_loja_em=None, visto_em=None)) == (None, None)
    sem_fuso = pendente.marcos_do_numero(ns(ultima_da_loja_em=t.replace(tzinfo=None), visto_em=t))
    assert sem_fuso == (t, t), "sem fuso (o teste de mesa): UTC"
    # Sem marco: toda mensagem do comprador conta; a da loja e a do sistema, não.
    assert pendente.mensagem_no_numero(msg(t, t + m))
    assert not pendente.mensagem_no_numero(msg(t, t + m, autor="loja"))
    assert not pendente.mensagem_no_numero(msg(t, t + m, autor="sistema"))
    # A resposta de pessoa às 07:32: a mandada depois conta, mesmo chegando tarde.
    assert pendente.mensagem_no_numero(msg(t + m, t + 3 * m), pessoa=t)
    assert not pendente.mensagem_no_numero(msg(t - m, t + 3 * m), pessoa=t)
    # Sem `enviada_em`, a chegada vale para o lado da pessoa (como a fila).
    assert pendente.mensagem_no_numero(msg(None, t + m), pessoa=t)
    # O visto às 07:32: a que CHEGOU depois conta, mesmo mandada antes (a
    # pessoa não a viu); a que chegou antes, não — mesmo mandada "depois".
    assert pendente.mensagem_no_numero(msg(t - m, t + m), visto=t)
    assert not pendente.mensagem_no_numero(msg(t + m, t - m), visto=t)
    # A chegada igual ao visto: a tela mostrou (a mesma transação do sync).
    assert not pendente.mensagem_no_numero(msg(t - m, t), visto=t)
    # Os dois juntos: tem de passar dos dois.
    assert pendente.mensagem_no_numero(msg(t + m, t + 2 * m), pessoa=t, visto=t + m)
    assert not pendente.mensagem_no_numero(msg(t - m, t + 2 * m), pessoa=t, visto=t + m)
    assert pendente.JANELA_DO_NUMERO == timedelta(days=7)
    # Desde quando: a de antes do `desde` (o Duoke zerado / 7 dias) não conta.
    assert not pendente.mensagem_no_numero(msg(t - m, t + m), desde=t)
    assert pendente.mensagem_no_numero(msg(t + m, t + 2 * m), desde=t)
    # A `sistema` do próprio comprador (`from_id` = `comprador_id`) conta; a da loja, não.
    do_comprador = ns(autor="sistema", enviada_em=t, created_at=t + m, payload={"from_id": 555})
    assert pendente.mensagem_no_numero(do_comprador, comprador="555")
    assert not pendente.mensagem_no_numero(do_comprador, comprador="777")
    assert not pendente.mensagem_no_numero(do_comprador)
    sem_from = ns(autor="sistema", enviada_em=t, created_at=t + m, payload={})
    assert not pendente.mensagem_no_numero(sem_from, comprador="555")


def test_o_numero_conta_desde_que_o_duoke_foi_zerado(monkeypatch):
    """O Duoke foi zerado inteiro em 14/10/2026 às 12:35 (15:35 UTC): o número
    conta dali, e no máximo 7 dias para trás (`desde_do_numero`)."""
    zerado = datetime(2026, 10, 14, 15, 35, tzinfo=UTC)
    monkeypatch.setattr(pendente, "ZERADO_NO_DUOKE_EM", zerado)
    assert pendente.desde_do_numero(datetime(2026, 10, 15, 11, 55, tzinfo=UTC)) == zerado
    depois = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    assert pendente.desde_do_numero(depois) == depois - timedelta(days=7)
    # Sem fuso (o teste de mesa): UTC.
    assert pendente.desde_do_numero(depois.replace(tzinfo=None)) == depois - timedelta(days=7)


async def test_falta_responder_no_chat_python_e_sql_sao_gemeas(db, make_user):
    """O filtro "Falta responder" da Caixa: a régua do Duoke, só no chat."""
    mundo = await _mundo(db, make_user)
    esperado = set()
    for nome, (conversa, falta) in mundo.items():
        await db.refresh(conversa)
        no_chat = falta and conversa.canal not in pendente.CANAIS_FORA_DO_CHAT
        no_chat = no_chat and nome != "email_da_ponte"
        assert pendente.falta_responder_no_chat(conversa) is no_chat, nome
        if no_chat:
            esperado.add(nome)
    assert "reclamacao_vez_da_loja" not in esperado and "cliente_por_ultimo" in esperado
    no_sql = set(
        (
            await db.execute(
                select(AtendimentoConversa.externo_id).where(pendente.falta_responder_no_chat_sql())
            )
        )
        .scalars()
        .all()
    )
    assert no_sql == esperado


async def test_resumo_conta_o_numero_do_duoke_so_no_chat(client, db, make_user, auth_as):
    """O /resumo: o número do Duoke por plataforma, por loja e o total (a aba
    Caixa); o "Falta responder" (`aguardando`) só no chat e pela régua dele;
    a Caixa Humano e o não lido da plataforma (`nao_lidas`) como antes."""
    mundo = await _mundo_duoke(db, make_user, "atv")
    # Outra loja Shopee: o Aguiar, 1 mensagem do comprador.
    aguiar, canal_ag = await _loja(db, await make_user(), "aguiar")
    await _cena(db, aguiar, canal_ag, "aguiar:um", ("cliente", 30), nao_lidas=1)
    auth_as(await make_user(permissions=PODE_TUDO))
    r = (await client.get(f"{URL}/resumo")).json()
    plats = {p["plataforma"]: p for p in r["plataformas"]}
    soma: dict[str, int] = {"shopee": 1}  # o Aguiar
    falta: dict[str, int] = {"shopee": 1}
    for c, n in mundo.values():
        await db.refresh(c)
        soma[c.plataforma] = soma.get(c.plataforma, 0) + n
        falta[c.plataforma] = falta.get(c.plataforma, 0) + pendente.falta_responder_no_chat(c)
    assert soma == {
        # quatro 4 + robô 3 + robô por último 2 + pessoa 2 + visto 1 + chegou
        # depois de abrir 1 + pessoa depois do visto 1 + dois dias 2 + fora
        # dos 7 dias 1 + FAQ do comprador 1 + só cartão 1 + figurinha 0028 1 +
        # lida 1 + muitas 1 + bloqueada 2 + aviso 1 + falhou/nota 1 + e-mail no
        # chat 1 — e o Aguiar, 1.
        "shopee": 4 + 3 + 2 + 2 + 1 + 1 + 1 + 2 + 1 + 1 + 1 + 1 + 1 + 1 + 2 + 1 + 1 + 1 + 1,
        "tiktok": 3,
        # O pós-venda com o comprador por último e a automática de agora (a
        # pergunta do anúncio fica fora, como no Duoke).
        "ml": 1 + 1,
        "amazon": 2,
        "magalu": 0,
        "site": 0,
        "instagram": 0,
    }
    for plat, n in soma.items():
        assert plats[plat]["nao_lidas_duoke"] == n, plat
        assert plats[plat]["aguardando"] == falta[plat], plat
    assert r["nao_lidas_duoke"] == sum(soma.values()) == 35
    lojas = {(lj["plataforma"], lj["conta"]): lj for lj in r["lojas"]}
    assert lojas[("shopee", "atv")]["nao_lidas_duoke"] == soma["shopee"] - 1 == 27
    assert lojas[("shopee", "aguiar")]["nao_lidas_duoke"] == 1
    assert lojas[("tiktok", "atv")]["nao_lidas_duoke"] == 3
    assert lojas[("ml", "atv")]["nao_lidas_duoke"] == 2
    # O número NÃO depende da marca do "Falta responder": a conversa em que o
    # robô respondeu depois do comprador está no número e fora do filtro.
    itens = (
        await client.get(f"{URL}/conversas", params={"filtro": "aguardando", "limite": 100})
    ).json()["itens"]
    nomes = {str(c.id): n for n, (c, _) in mundo.items()}
    vistos = {nomes.get(i["id"], "outra") for i in itens}
    assert "robo_por_ultimo" not in vistos and "ml_automatica" not in vistos
    assert "robo_nao_zera" in vistos, "o comprador escreveu depois do robô"
    assert not vistos & {"avaliacao", "reclamacao", "carrinho", "comentario", "email_da_ponte"}
    # O "Falta responder" segue a régua dele (a)/(b): o visto não tira ninguém.
    assert {"visto_depois_de_tudo", "quatro", "ml_pergunta", "amazon_email"} <= vistos
    # O não lido da plataforma (`nao_lidas`) continua a soma de tudo da loja na Caixa.
    atv = lojas[("shopee", "atv")]
    assert atv["nao_lidas"] == sum(
        c.nao_lidas for n, (c, _) in mundo.items() if c.plataforma == "shopee"
    )
    # A Caixa Humano não muda (as lojas daqui estão em Observar: nada nela).
    assert r["humano"] == sum(p["humano"] for p in r["plataformas"]) == 0


async def test_abrir_no_davinci_zera_o_numero_da_conversa(
    client, db, make_user, auth_as, monkeypatch
):
    """Como no Duoke: a pessoa que mexe abre a conversa (POST /visto, a loja em
    Observar, com o `visto_ate` do detalhe) e o número dela zera no /resumo e
    na linha; o que CHEGOU depois conta — a mensagem que o comprador mandou
    antes de abrir e o sync trouxe depois (a revisão de 15/10/2026: sumia), e a
    que chegou entre a leitura do detalhe e o POST (a tela não mostrou). O robô
    depois disso não zera nada."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "quem.mexe@davinci-test.com")
    integ, canal = await _loja(db, await make_user(), "kia")
    conversa = await _cena(db, integ, canal, "kia:9", *[("cliente", 90 - i) for i in range(9)])
    pessoa = await make_user(email="quem.mexe@davinci-test.com", role=UserRole.ADMIN)
    auth_as(pessoa)
    cid = str(conversa.id)

    async def numero() -> int:
        r = (await client.get(f"{URL}/resumo")).json()
        (kia,) = [lj for lj in r["lojas"] if lj["conta"] == "kia"]
        return kia["nao_lidas_duoke"]

    async def linha() -> int:
        itens = (await client.get(f"{URL}/conversas", params={"limite": 100})).json()["itens"]
        (item,) = [i for i in itens if i["id"] == cid]
        return item["numero_duoke"]

    async def chega(externo: str, autor: str = "cliente", segundos: int = 0) -> None:
        """O sync grava uma mensagem mandada há `segundos` (a transação começa agora)."""
        await db.commit()
        await gravar.gravar_mensagem(
            db,
            conversa,
            externo_id=externo,
            autor=autor,
            texto=MENU_DUOKE if autor == "loja" else "oi?",
            enviada_em=datetime.now(UTC) - timedelta(seconds=segundos),
        )
        await db.commit()

    assert await numero() == 9, "a Kia com 9 numa conversa, como no Duoke"
    assert await linha() == 9, "e a linha mostra os 9"
    d = (await client.get(f"{URL}/conversas/{cid}")).json()
    assert d["conversa"]["numero_duoke"] == 9 and d["visto_ate"]
    r = await client.post(f"{URL}/conversas/{cid}/visto", json={"ate": d["visto_ate"]})
    assert r.status_code == 200, r.text
    assert r.json()["visto_em"] and r.json()["conversa"]["numero_duoke"] == 0
    assert await numero() == 0 and await linha() == 0
    # Mandada 60 s ANTES de abrir, trazida pelo sync DEPOIS: a pessoa não viu.
    await chega("kia:9:atrasada", segundos=60)
    assert await numero() == 1 and await linha() == 1
    # A releitura do detalhe mostra a atrasada (`visto_ate` andou); antes do
    # POST, chega mais uma: a tela não a mostrou — continua contando.
    d2 = (await client.get(f"{URL}/conversas/{cid}")).json()
    assert datetime.fromisoformat(d2["visto_ate"]) > datetime.fromisoformat(d["visto_ate"])
    await chega("kia:9:corrida")
    r = await client.post(f"{URL}/conversas/{cid}/visto", json={"ate": d2["visto_ate"]})
    assert r.status_code == 200
    assert await numero() == 1 and await linha() == 1
    # O robô respondeu: não zera.
    await chega("kia:9:menu", autor="loja")
    assert await numero() == 1


async def test_a_linha_mostra_o_numero_da_conversa(client, db, make_user, auth_as, monkeypatch):
    """A bolinha da linha na Caixa (`numero_duoke`, 15/10/2026, o Eduardo: "1
    mensagem na kia e nem mostra dessa conversa"): a mesma régua do número da
    loja, com ou sem a marca do "Falta responder" — a soma das linhas de uma
    loja é o número dela. A conversa com o robô por último (fora do "Falta
    responder") mostra o número; a aberta no DaVinci fica marcada, sem número.
    Fora do chat, a linha não tem número (None: a bolinha de antes)."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "quem.mexe@davinci-test.com")
    integ, canal = await _loja(db, await make_user(), "kia")
    robo = await _cena(
        db,
        integ,
        canal,
        "kia:robo",
        ("cliente", 30),
        ("cliente", 20),
        ("cliente", 10),
        ("loja", 9, MENU_DUOKE),
    )
    aberta = await _cena(
        db, integ, canal, "kia:aberta", ("cliente", 30), ("cliente", 20), ("cliente", 10)
    )
    vez = {CHAVE_VEZ_DA_LOJA: (AGORA - timedelta(minutes=60)).isoformat()}
    avaliacao = await _cena(
        db, integ, canal, "kia:avaliacao", ("cliente", 60), dados=vez, canal_nome="avaliacao"
    )
    auth_as(await make_user(email="quem.mexe@davinci-test.com", role=UserRole.ADMIN))

    async def ver() -> tuple[int, dict[str, dict]]:
        r = (await client.get(f"{URL}/resumo")).json()
        (kia,) = [lj for lj in r["lojas"] if lj["conta"] == "kia"]
        itens = (await client.get(f"{URL}/conversas", params={"limite": 100})).json()["itens"]
        return kia["nao_lidas_duoke"], {i["id"]: i for i in itens}

    n, itens = await ver()
    linha_robo, linha_aberta = itens[str(robo.id)], itens[str(aberta.id)]
    assert n == 6
    # O robô por último: fora do "Falta responder" (sem negrito), com o número.
    assert linha_robo["falta_responder"] is False and linha_robo["numero_duoke"] == 3
    assert linha_aberta["falta_responder"] is True and linha_aberta["numero_duoke"] == 3
    # Fora do chat: sem número (a tela usa a bolinha de antes); a marca fica.
    linha_avaliacao = itens[str(avaliacao.id)]
    assert linha_avaliacao["numero_duoke"] is None and linha_avaliacao["falta_responder"] is True
    assert n == sum(i["numero_duoke"] or 0 for i in itens.values())
    # Abriu a conversa: o número dela zera na loja e na linha; a marca fica.
    r = await client.post(f"{URL}/conversas/{aberta.id}/visto")
    assert r.status_code == 200
    n, itens = await ver()
    assert n == 3
    assert itens[str(aberta.id)]["numero_duoke"] == 0
    assert itens[str(aberta.id)]["falta_responder"] is True, "o negrito é do Falta responder"
    assert n == sum(i["numero_duoke"] or 0 for i in itens.values())
    # O detalhe e a resposta das ações levam o mesmo número.
    d = (await client.get(f"{URL}/conversas/{robo.id}")).json()
    assert d["conversa"]["numero_duoke"] == 3
    d = (await client.get(f"{URL}/conversas/{avaliacao.id}")).json()
    assert d["conversa"]["numero_duoke"] is None


# ─────────────── a reclamação do ML no número (15/10/2026) ───────────────
#
# O Duoke junta a reclamação/mediação do ML à conversa do pack e conta a
# mensagem do MEDIADOR como não lida: os dois packs do ML que faltavam na
# medição das 10:13 (injox 2000018538895678 e aguiar 2000018464125672) eram
# isso. A reclamação do DaVinci (`canal = 'reclamacao'`, só leitura) soma no
# número da loja pela mesma régua, com o mediador contando.


async def _mundo_reclamacao_ml(db, make_user) -> dict[str, tuple[AtendimentoConversa, int]]:
    """Cada caso da reclamação no número → (conversa, quanto ela soma)."""
    integ, canal = await _loja(db, await make_user(), "injox")
    c: dict[str, tuple[AtendimentoConversa, int]] = {}
    # A conversa da reclamação do ML: sem canal, bloqueada (só leitura), a
    # vez pela plataforma (sem ação pendente da loja).
    reclamacao = {
        "plataforma": "ml",
        "canal_nome": "reclamacao",
        "situacao": "bloqueada",
        "dados": {CHAVE_VEZ_DA_LOJA: None, "claim_id": "1"},
    }

    async def caso(nome, esperado, *falas, **campos):
        campos = {**reclamacao, **campos}
        c[nome] = (await _cena(db, integ, None, f"rml:{nome}", *falas, **campos), esperado)

    # O injox: a loja respondeu ao mediador; depois, o mediador escreveu (1).
    await caso("mediador_depois_da_loja", 1, ("mediador", 300), ("loja", 200), ("mediador", 60))
    # O aguiar: a decisão do mediador numa reclamação sem resposta da loja.
    await caso("decisao_do_mediador", 2, ("mediador", 600), ("mediador", 60))
    # O comprador (na etapa com ele) também conta.
    await caso("comprador_e_mediador", 3, ("cliente", 90), ("mediador", 60), ("mediador", 50))
    # A resposta de pessoa da loja zera.
    await caso("loja_respondeu", 0, ("mediador", 90), ("loja", 30))
    # As linhas que o DaVinci escreve ("aberta", "encerrada") não contam.
    await caso(
        "so_sistema",
        0,
        ("sistema", 90, "Mediação 1 aberta no Mercado Livre."),
        ("sistema", 30, "Mediação encerrada no Mercado Livre — decisão a favor da loja."),
    )
    # Visto no DaVinci: só o que chegou depois (a chegada = 1 min depois).
    await caso(
        "visto", 1, ("mediador", 90), ("mediador", 30), visto_em=AGORA - timedelta(minutes=60)
    )
    # Fechada pela pessoa e "não precisa de resposta": fora, como no chat.
    await caso("fechada", 0, ("mediador", 60), situacao="fechada")
    await caso("nao_precisa", 0, ("mediador", 60), sem_resposta_necessaria=True)
    # Fora da janela do número (mais de 7 dias): nada.
    await caso("velha", 0, ("mediador", 8 * 24 * HORA))
    # A reclamação/devolução da Shopee continua fora do número.
    await caso("shopee", 0, ("cliente", 60), ("mediador", 50), plataforma="shopee")
    # O mediador só conta na reclamação do ML: no SAC da Magalu, não.
    await caso(
        "magalu_sac",
        0,
        ("cliente", 90),
        ("loja", 80),
        ("mediador", 60),
        plataforma="magalu",
        canal_nome="sac",
        situacao="aberta",
    )
    await db.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id.in_([conversa.id for conversa, _ in c.values()]))
        .values(created_at=AtendimentoMensagem.enviada_em + ATRASO_DO_SYNC)
    )
    await db.commit()
    return c


async def test_reclamacao_do_ml_no_numero_python_e_sql_sao_gemeos(db, make_user):
    mundo = await _mundo_reclamacao_ml(db, make_user)
    for nome, (conversa, esperado) in mundo.items():
        await db.refresh(conversa)
        msgs = await _mensagens_de(db, conversa)
        assert pendente.nao_lidas_duoke(conversa, msgs, agora=AGORA) == esperado, nome
    linhas = (
        await db.execute(
            select(AtendimentoConversa.externo_id, pendente.nao_lidas_duoke_sql(agora=AGORA)).where(
                AtendimentoConversa.externo_id.like("rml:%")
            )
        )
    ).all()
    assert {e.removeprefix("rml:"): n for e, n in linhas} == {
        nome: esperado for nome, (_, esperado) in mundo.items()
    }
    # A reclamação do ML é do número; a da Shopee e a avaliação, não — e o
    # filtro "Falta responder" continua só o chat.
    rml = mundo["mediador_depois_da_loja"][0]
    assert pendente.do_numero(rml) and not pendente.do_chat(rml)
    assert not pendente.falta_responder_no_chat(rml)
    assert not pendente.do_numero(mundo["shopee"][0])
    no_numero = set(
        (
            await db.execute(
                select(AtendimentoConversa.externo_id).where(
                    AtendimentoConversa.externo_id.like("rml:%"), pendente.do_numero_sql()
                )
            )
        )
        .scalars()
        .all()
    )
    assert no_numero == {f"rml:{n}" for n in mundo if n != "shopee"}


async def test_reclamacao_do_ml_desligada_tira_do_numero_sem_soltar_o_mediador(
    db, make_user, monkeypatch
):
    """A chave para desligar (revisão de 15/10/2026, se o Duoke mostrar menos
    no mediador): `RECLAMACAO_NO_NUMERO = ()` tira a reclamação do ML do
    número no Python e no SQL — e o `or_()` vazio não pode virar "o mediador
    conta em toda conversa" (o SAC da Magalu continua 0)."""
    mundo = await _mundo_reclamacao_ml(db, make_user)
    monkeypatch.setattr(pendente, "RECLAMACAO_NO_NUMERO", ())
    for nome, (conversa, _) in mundo.items():
        await db.refresh(conversa)
        msgs = await _mensagens_de(db, conversa)
        assert pendente.nao_lidas_duoke(conversa, msgs, agora=AGORA) == 0, nome
        assert not pendente.reclamacao_no_numero(conversa), nome
    linhas = (
        await db.execute(
            select(AtendimentoConversa.externo_id, pendente.nao_lidas_duoke_sql(agora=AGORA)).where(
                AtendimentoConversa.externo_id.like("rml:%")
            )
        )
    ).all()
    assert {e.removeprefix("rml:"): n for e, n in linhas} == dict.fromkeys(mundo, 0)
    no_numero = (
        (
            await db.execute(
                select(AtendimentoConversa.externo_id).where(
                    AtendimentoConversa.externo_id.like("rml:%"), pendente.do_numero_sql()
                )
            )
        )
        .scalars()
        .all()
    )
    # Só o SAC da Magalu, que é do chat (e lá o mediador não conta).
    assert set(no_numero) == {"rml:magalu_sac"}


async def test_reclamacao_do_ml_soma_na_loja_e_na_linha_e_zera(
    client, db, make_user, auth_as, monkeypatch
):
    """O /resumo soma a reclamação do ML no número da loja, a linha dela mostra
    o número (a da Shopee, None) e o "Zerar não lidas" da loja a zera."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "quem.mexe@davinci-test.com")
    integ, canal = await _loja(db, await make_user(), "injox")
    reclamacao = {"plataforma": "ml", "canal_nome": "reclamacao", "situacao": "bloqueada"}
    rml = await _cena(db, integ, None, "rml:injox", ("loja", 200), ("mediador", 60), **reclamacao)
    pack = await _cena(
        db, integ, canal, "pack:injox", ("cliente", 90), plataforma="ml", canal_nome="pos_venda"
    )
    shopee = await _cena(
        db, integ, None, "rsh:injox", ("cliente", 60), plataforma="shopee", canal_nome="reclamacao"
    )
    auth_as(await make_user(email="quem.mexe@davinci-test.com", role=UserRole.ADMIN))

    async def ver() -> tuple[int, dict[str, dict]]:
        r = (await client.get(f"{URL}/resumo")).json()
        (loja,) = [lj for lj in r["lojas"] if (lj["plataforma"], lj["conta"]) == ("ml", "injox")]
        itens = (await client.get(f"{URL}/conversas", params={"limite": 100})).json()["itens"]
        return loja["nao_lidas_duoke"], {i["id"]: i for i in itens}

    n, itens = await ver()
    assert n == 2, "o pack com 1 do comprador + a reclamação com 1 do mediador"
    assert itens[str(rml.id)]["numero_duoke"] == 1
    assert itens[str(pack.id)]["numero_duoke"] == 1
    assert itens[str(shopee.id)]["numero_duoke"] is None
    d = (await client.get(f"{URL}/conversas/{rml.id}")).json()
    assert d["conversa"]["numero_duoke"] == 1

    r = await client.post(f"{URL}/zerar-nao-lidas", json={"integration_id": str(integ.id)})
    assert r.status_code == 200, r.text
    n, itens = await ver()
    assert n == 0 and itens[str(rml.id)]["numero_duoke"] == 0


# ─────────────── a ordem da lista "Falta responder" ───────────────


async def test_lista_falta_responder_sem_prazo_pela_recencia(client, db, make_user, auth_as):
    """Quem tem prazo (a fila) vem primeiro, pelo prazo; quem entra só pela régua
    do Duoke (não lida, sem prazo) vem depois, a mais recente primeiro — e o
    "carregar mais" não pula nem repete, inclusive na virada de um grupo para o
    outro e com o cursor de antes (sem a recência)."""
    integ, canal = await _loja(db, await make_user(), "kfa")
    nomes: dict[str, str] = {}
    for nome, minutos in (("p_velha", 120), ("p_nova", 30)):
        c = await _cena(db, integ, canal, nome, ("loja", minutos + 10), ("cliente", minutos))
        nomes[str(c.id)] = nome
    for i in range(6):
        c = await _cena(
            db,
            integ,
            canal,
            f"nl{i}",
            ("cliente", 600 - i * 60),
            ("loja", 590 - i * 60),
            nao_lidas=1,
        )
        nomes[str(c.id)] = f"nl{i}"
    # Duas no MESMO segundo: o id desempata, sem sumir no corte da página.
    for nome in ("empate_a", "empate_b"):
        c = await _cena(db, integ, canal, nome, ("cliente", 1000), ("loja", 999), nao_lidas=1)
        nomes[str(c.id)] = nome
    auth_as(await make_user(permissions=PODE_TUDO))
    esperado_inicio = ["p_velha", "p_nova", "nl5", "nl4", "nl3", "nl2", "nl1", "nl0"]

    tudo = (
        await client.get(f"{URL}/conversas", params={"filtro": "aguardando", "limite": 100})
    ).json()
    lista = [nomes[i["id"]] for i in tudo["itens"]]
    assert lista[:8] == esperado_inicio, lista
    assert sorted(lista[8:]) == ["empate_a", "empate_b"]
    assert tudo["proximo"] is None

    for limite in (1, 2, 3):
        vistos: list[str] = []
        cursor = None
        for _ in range(20):
            params = {"filtro": "aguardando", "limite": limite}
            if cursor:
                params["antes_de"] = cursor
            r = await client.get(f"{URL}/conversas", params=params)
            assert r.status_code == 200, r.text
            vistos += [nomes[i["id"]] for i in r.json()["itens"]]
            cursor = r.json()["proximo"]
            if cursor is None:
                break
        assert vistos == lista, (limite, vistos)

    # O cursor de antes (`prazo:|<id>`, sem a recência): o grupo sem prazo do começo.
    r = await client.get(
        f"{URL}/conversas",
        params={"filtro": "aguardando", "antes_de": f"prazo:|{uuid4()}", "limite": 100},
    )
    assert r.status_code == 200, r.text
    assert [nomes[i["id"]] for i in r.json()["itens"]] == lista[2:]
    for ruim in ("prazo:|lixo|abc", "prazo:||", "prazo:|2026-10-14T10:00:00+00:00|"):
        r = await client.get(f"{URL}/conversas", params={"filtro": "aguardando", "antes_de": ruim})
        assert (r.status_code, r.json()["detail"]) == (422, {"code": "cursor_invalido"}), ruim


def test_ordem_do_prazo_em_python_e_a_mesma_do_cursor():
    """A ordem em Python (o Direct do Instagram entra nela) e o corte pelo cursor
    batem: depois do cursor de cada item vêm exatamente os seguintes."""
    from app.routers import atendimento as rota

    base = datetime(2026, 10, 14, 12, tzinfo=UTC)
    itens = [
        {"id": "a", "prazo_resposta_em": base + timedelta(hours=2), "ultima_mensagem_em": base},
        {"id": "b", "prazo_resposta_em": base + timedelta(hours=1), "ultima_mensagem_em": base},
        {"id": "c", "prazo_resposta_em": None, "ultima_mensagem_em": base - timedelta(hours=1)},
        {"id": "d", "prazo_resposta_em": None, "ultima_mensagem_em": base},
        {"id": "e", "prazo_resposta_em": None, "ultima_mensagem_em": base},
        {"id": "f", "prazo_resposta_em": None, "ultima_mensagem_em": None},
        {
            "id": "g",
            "prazo_resposta_em": None,
            "ultima_mensagem_em": base + timedelta(microseconds=1),
        },
        {
            "id": "h",
            "canal": "comentario",
            "prazo_resposta_em": None,
            "ultima_mensagem_em": base + timedelta(hours=3),
        },
    ]
    ordem = [i["id"] for i in sorted(itens, key=rota._chave_do_prazo)]
    assert ordem == ["b", "a", "g", "d", "e", "c", "f", "h"]
    por_id = {i["id"]: i for i in itens}
    for k, ident in enumerate(ordem):
        momento, cid, sem_prazo, grupo = rota._cursor(
            rota._cursor_do_prazo(por_id[ident]), pelo_prazo=True
        )
        depois = [
            i for i in ordem if rota._depois_do_cursor(por_id[i], momento, cid, sem_prazo, grupo)
        ]
        assert depois == ordem[k + 1 :], ident


# ─────────────── não lido fresco: a gravação ───────────────


async def test_aplicar_nao_lidas_marca_zera_e_nao_cria(db, make_user):
    integ, canal = await _loja(db, await make_user(), "kfa")
    a = await _cena(db, integ, canal, "A", ("cliente", 60), nao_lidas=3)
    b = await _cena(db, integ, canal, "B", ("cliente", 60))
    c = await _cena(db, integ, canal, "C", ("cliente", 60), nao_lidas=1)
    # Lista cortada: marca o que viu, não zera ninguém.
    n = await gravar.aplicar_nao_lidas(
        db,
        integration_id=integ.id,
        canal_nome="chat",
        contagens={"B": 2, "nao-existe": 5},
        completa=False,
    )
    await db.commit()
    assert n == 1
    assert [x.nao_lidas for x in (a, b, c)] == [3, 2, 1], "a sessão já vê o valor novo"
    # Lista completa: quem não veio zera; quem já está certo não conta.
    n = await gravar.aplicar_nao_lidas(
        db, integration_id=integ.id, canal_nome="chat", contagens={"B": 2}, completa=True
    )
    await db.commit()
    assert n == 2
    ids = [x.id for x in (a, b, c)]
    db.expire_all()
    assert [(await db.get(AtendimentoConversa, i)).nao_lidas for i in ids] == [0, 2, 0]
    criada = await db.scalar(
        select(AtendimentoConversa.id).where(AtendimentoConversa.externo_id == "nao-existe")
    )
    assert criada is None, "não cria conversa"


# ─────────────── não lido fresco: Shopee ───────────────


async def _shopee_lida(db, make_user, teto):
    """Rodada 1: X (2 não lidas) e Y (1 não lida) gravadas."""
    integ, canal = await tsh._canal(db, await make_user())
    f = tsh.ShopeeFalso()
    f.conversa("X", tsh.msg("X", AGORA - timedelta(hours=1)), nao_lidas=2)
    f.conversa("Y", tsh.msg("Y", AGORA - timedelta(hours=2)), nao_lidas=1)
    f.nao_lidas = 2
    await tsh._rodar(db, canal, integ, f)
    cs = await tsh._conversas(db)
    assert (cs["X"].nao_lidas, cs["Y"].nao_lidas) == (2, 1)
    f.chamadas_lista.clear()
    return integ, canal, f


async def test_shopee_lida_no_duoke_volta_a_zero_sem_mensagem_nova(db, make_user, teto):
    integ, canal, f = await _shopee_lida(db, make_user, teto)
    # A equipe abriu as duas no Duoke: a Shopee zera; nenhuma mensagem nova.
    f.conversas["X"]["unread_count"] = 0
    f.conversas["Y"]["unread_count"] = 0
    f.nao_lidas = 0
    r = await tsh._rodar(db, canal, integ, f)
    assert r.status == shopee.STATUS_OK
    cs = await tsh._conversas(db)
    assert (cs["X"].nao_lidas, cs["Y"].nao_lidas) == (0, 0)
    # Total zero: nem pede a lista das não lidas. E o sync nunca marca como
    # lida: desde 14/10/2026 só a pessoa que abre a conversa (lida.py, com o
    # chat_read_conversation) marca — o read_conversation genérico não existe.
    assert not any(c["tipo"] == shopee.TIPO_NAO_LIDAS for c in f.chamadas_lista)
    assert not hasattr(ShopeeClient, "read_conversation")


async def test_shopee_lista_so_das_nao_lidas_acerta_cada_conversa(db, make_user, teto):
    integ, canal, f = await _shopee_lida(db, make_user, teto)
    f.conversas["X"]["unread_count"] = 0  # lida no Duoke
    f.conversas["Y"]["unread_count"] = 4  # continua não lida (o número mudou)
    f.nao_lidas = 1
    await tsh._rodar(db, canal, integ, f)
    cs = await tsh._conversas(db)
    assert (cs["X"].nao_lidas, cs["Y"].nao_lidas) == (0, 4)
    pedidas = [c for c in f.chamadas_lista if c["tipo"] == shopee.TIPO_NAO_LIDAS]
    assert pedidas and all(
        c["direction"] == shopee.DIRECAO_MAIS_ANTIGAS and c["page_size"] <= 25 for c in pedidas
    )


async def test_shopee_lista_das_nao_lidas_falhando_nao_zera_nada(db, make_user, teto):
    integ, canal, f = await _shopee_lida(db, make_user, teto)
    original = f.chat_conversation_list

    async def lista(**kw):
        if kw.get("tipo") == shopee.TIPO_NAO_LIDAS:
            raise httpx.ConnectTimeout("lento")
        return await original(**kw)

    f.chat_conversation_list = lista
    f.conversas["X"]["unread_count"] = 0
    f.nao_lidas = 1
    r = await tsh._rodar(db, canal, integ, f)
    assert r.status == shopee.STATUS_OK, "a leitura segue"
    cs = await tsh._conversas(db)
    assert (cs["X"].nao_lidas, cs["Y"].nao_lidas) == (2, 1), "fica o valor de antes"


async def test_shopee_total_falhando_nao_mexe_no_nao_lido(db, make_user, teto):
    integ, canal, f = await _shopee_lida(db, make_user, teto)
    f.conversas["X"]["unread_count"] = 0
    f.erro_nao_lidas = httpx.ConnectTimeout("lento")
    await tsh._rodar(db, canal, integ, f)
    cs = await tsh._conversas(db)
    assert (cs["X"].nao_lidas, cs["Y"].nao_lidas) == (2, 1)


async def test_shopee_lista_cortada_marca_e_nao_zera(db, make_user, teto, monkeypatch):
    integ, canal, f = await _shopee_lida(db, make_user, teto)
    f.conversa("Z", tsh.msg("Z", AGORA - timedelta(minutes=30)), nao_lidas=0)
    await tsh._rodar(db, canal, integ, f)
    assert (await tsh._conversas(db))["Z"].nao_lidas == 0
    monkeypatch.setattr(shopee, "MAX_PAGINAS_NAO_LIDAS", 1)
    monkeypatch.setattr(shopee, "PAGINA_CONVERSAS", 1)
    # X lida no Duoke; Y e Z não lidas: a 1ª (e única) página traz só a Z
    # (a lista vem da mais nova para trás).
    f.conversas["X"]["unread_count"] = 0
    f.conversas["Y"]["unread_count"] = 5
    f.conversas["Z"]["unread_count"] = 7
    f.nao_lidas = 2
    await tsh._rodar(db, canal, integ, f)
    cs = await tsh._conversas(db)
    assert cs["Z"].nao_lidas == 7, "a que veio ganha o número dela"
    # Lista cortada: quem não veio fica como estava — nem a X (lida no Duoke)
    # zera, nem a Y muda.
    assert (cs["X"].nao_lidas, cs["Y"].nao_lidas) == (2, 1)


# ─────────────── não lido fresco: TikTok ───────────────


async def _tiktok_lida(db, make_user):
    integ, canal = await ttk._canal(db, await make_user())
    f = ttk.TikTokFalso()
    f.conversa("A", ttk.tmsg(AGORA - timedelta(hours=1)), nao_lidas=2)
    f.conversa("B", ttk.tmsg(AGORA - timedelta(hours=2)), nao_lidas=0)
    await ttk._rodar(db, canal, integ, f)
    cs = await ttk._conversas(db)
    assert (cs["A"].nao_lidas, cs["B"].nao_lidas) == (2, 0)
    f.chamadas_lista.clear()
    return integ, canal, f


async def test_tiktok_lida_no_duoke_volta_a_zero(db, make_user, teto):
    integ, canal, f = await _tiktok_lida(db, make_user)
    f.conversas["A"]["unread_count"] = 0  # lida; nenhuma mensagem nova
    await ttk._rodar(db, canal, integ, f)
    cs = await ttk._conversas(db)
    assert cs["A"].nao_lidas == 0
    # Sem não lida gravada, a rodada seguinte não relê a lista para isso.
    f.chamadas_lista.clear()
    await ttk._rodar(db, canal, integ, f)
    assert len(f.chamadas_lista) == 1, "só o topo da caminhada"


async def test_tiktok_falha_nao_zera_e_lista_cortada_so_marca(db, make_user, teto, monkeypatch):
    integ, canal, f = await _tiktok_lida(db, make_user)
    f.conversas["A"]["unread_count"] = 0
    f.resposta_lista = {"code": 36009003, "message": "Internal error"}
    await ttk._rodar(db, canal, integ, f)
    assert (await ttk._conversas(db))["A"].nao_lidas == 2, "falhou: fica o de antes"
    # Lista cortada (1 conversa por página, 1 página): a vista ganha o número
    # dela; a não lida que não foi vista não zera.
    f.resposta_lista = None
    f.conversas["A"]["unread_count"] = 2  # ainda não lida na TikTok
    f.conversa("C", ttk.tmsg(AGORA - timedelta(minutes=10)), nao_lidas=1)
    await ttk._rodar(db, canal, integ, f)  # C entra pela caminhada
    cs = await ttk._conversas(db)
    assert (cs["A"].nao_lidas, cs["C"].nao_lidas) == (2, 1)
    monkeypatch.setattr(tiktok, "PAGINA_CONVERSAS", 1)
    monkeypatch.setattr(tiktok, "MAX_PAGINAS_NAO_LIDAS", 1)
    f.conversas["A"]["unread_count"] = 0  # lida no Duoke, mas fica fora da página
    f.conversas["C"]["unread_count"] = 3
    await ttk._rodar(db, canal, integ, f)
    cs = await ttk._conversas(db)
    assert cs["C"].nao_lidas == 3
    assert cs["A"].nao_lidas == 2


def _passada(canal) -> dict | None:
    return (canal.cursor or {}).get(tiktok.CHAVE_PASSADA_NAO_LIDAS)


async def test_tiktok_passada_continua_na_rodada_seguinte_e_zera_no_fim(
    db, make_user, teto, monkeypatch
):
    """A não lida lida no Duoke além das páginas de uma rodada: a passada guarda
    onde parou e continua na seguinte; vista lida, zera; a que não aparece na
    lista inteira zera no fim. Depois, sem candidata, nenhuma chamada a mais."""
    integ, canal = await ttk._canal(db, await make_user())
    f = ttk.TikTokFalso()
    f.conversa("A", ttk.tmsg(AGORA - timedelta(hours=5)), nao_lidas=1)
    f.conversa("B", ttk.tmsg(AGORA - timedelta(hours=6)), nao_lidas=2)
    for i in range(4):
        f.conversa(f"N{i}", ttk.tmsg(AGORA - timedelta(hours=1, minutes=i)), nao_lidas=0)
    await ttk._rodar(db, canal, integ, f)
    cs = await ttk._conversas(db)
    assert (cs["A"].nao_lidas, cs["B"].nao_lidas) == (1, 2)

    monkeypatch.setattr(tiktok, "PAGINA_CONVERSAS", 1)
    monkeypatch.setattr(tiktok, "MAX_PAGINAS_NAO_LIDAS", 2)
    f.conversas["A"]["unread_count"] = 0  # lida no Duoke, sem mensagem nova
    del f.conversas["B"]  # some da lista (a TikTok não mostra mais)
    # Ordem da lista: N0, N1, N2, N3, A.
    f.chamadas_lista.clear()
    await ttk._rodar(db, canal, integ, f)
    tokens = [c["page_token"] for c in f.chamadas_lista]
    assert tokens[:2] == [None, "1"], "a passada começa do topo, 2 páginas"
    estado = _passada(canal)
    assert estado and estado["retomar"] == "2", "guarda onde parou (a caminhada não apaga)"
    assert (await ttk._conversas(db))["A"].nao_lidas == 1, "ainda não vista"

    f.chamadas_lista.clear()
    await ttk._rodar(db, canal, integ, f)
    assert [c["page_token"] for c in f.chamadas_lista][:2] == ["2", "3"]
    f.chamadas_lista.clear()
    await ttk._rodar(db, canal, integ, f)
    assert [c["page_token"] for c in f.chamadas_lista][0] == "4"
    cs = await ttk._conversas(db)
    assert cs["A"].nao_lidas == 0, "vista lida: zera"
    assert cs["B"].nao_lidas == 0, "a lista acabou sem ela: zera"
    assert _passada(canal) is None, "a passada acabou"

    # Sem candidata: só a caminhada (uma página do topo).
    f.chamadas_lista.clear()
    await ttk._rodar(db, canal, integ, f)
    assert [c["page_token"] for c in f.chamadas_lista] == [None]


async def test_tiktok_aviso_do_sistema_nao_lido_nao_custa_chamada(db, make_user, teto):
    """A conversa só com o aviso do sistema (o cartão de status do pedido) fica
    não lida na TikTok para sempre e não conta no "Falta responder": não é
    candidata — nenhuma chamada a mais por rodada por causa dela."""
    integ, canal = await ttk._canal(db, await make_user())
    f = ttk.TikTokFalso()
    f.conversa("S", ttk.tmsg(AGORA - timedelta(days=5), papel="SYSTEM"), nao_lidas=1)
    await ttk._rodar(db, canal, integ, f)
    s = (await ttk._conversas(db))["S"]
    assert s.nao_lidas == 1 and s.ultima_autor == "sistema"
    assert pendente.falta_responder(s) is False
    f.chamadas_lista.clear()
    await ttk._rodar(db, canal, integ, f)
    assert [c["page_token"] for c in f.chamadas_lista] == [None], "só a caminhada"


async def test_tiktok_falha_na_passada_recomeca_do_topo(db, make_user, teto, monkeypatch):
    integ, canal = await ttk._canal(db, await make_user())
    f = ttk.TikTokFalso()
    f.conversa("A", ttk.tmsg(AGORA - timedelta(hours=5)), nao_lidas=1)
    for i in range(3):
        f.conversa(f"N{i}", ttk.tmsg(AGORA - timedelta(hours=1, minutes=i)), nao_lidas=0)
    await ttk._rodar(db, canal, integ, f)
    monkeypatch.setattr(tiktok, "PAGINA_CONVERSAS", 1)
    monkeypatch.setattr(tiktok, "MAX_PAGINAS_NAO_LIDAS", 1)
    f.conversas["A"]["unread_count"] = 0
    await ttk._rodar(db, canal, integ, f)
    assert _passada(canal)["retomar"] == "1"
    # O token guardado venceu: a TikTok recusa. Nada muda e a passada recomeça.
    f.resposta_lista = {"code": 36009003, "message": "Internal error"}
    await ttk._rodar(db, canal, integ, f)
    assert _passada(canal) is None
    assert (await ttk._conversas(db))["A"].nao_lidas == 1, "falhou: fica o de antes"
    f.resposta_lista = None
    f.chamadas_lista.clear()
    await ttk._rodar(db, canal, integ, f)
    assert f.chamadas_lista[0]["page_token"] is None, "do topo"


# ─────────────── não lido fresco: ML pós-venda ───────────────


async def test_ml_pos_venda_acerta_o_nao_lido_alem_do_teto(db, make_user, tml_relogio, teto):
    canal, integ = await tml._canal(db, make_user, "pos_venda")
    cli = tml.ClienteFalso()
    outro = "2000009876543211"
    cli.nao_lidos = {tml.PACK: 1, outro: 1}
    cli.packs[tml.PACK] = {"messages": [tml._do_cliente("c1", "Oi", tml.T0)]}
    cli.packs[outro] = {"messages": [tml._do_cliente("o1", "Oi", tml.T0)]}
    await tml._rodar(db, canal, integ, cli)
    assert (await tml._conversa(db, outro)).nao_lidas == 1
    # Só 1 pack relido por rodada: o que muda de contagem vai primeiro; o outro
    # também fica com o número da lista (antes ficava com o velho).
    teto(1)
    tml_relogio["agora"] += timedelta(minutes=10)
    cli.nao_lidos = {tml.PACK: 4, outro: 3}
    await tml._rodar(db, canal, integ, cli)
    assert (await tml._conversa(db, tml.PACK)).nao_lidas == 4
    assert (await tml._conversa(db, outro)).nao_lidas == 3
    # Lido no Duoke: sai da lista e zera.
    tml_relogio["agora"] += timedelta(minutes=10)
    cli.nao_lidos = {outro: 3}
    await tml._rodar(db, canal, integ, cli)
    assert (await tml._conversa(db, tml.PACK)).nao_lidas == 0
    assert (await tml._conversa(db, outro)).nao_lidas == 3


@pytest.fixture
def tml_relogio(monkeypatch):
    atual = {"agora": tml.T0 + timedelta(minutes=30)}
    monkeypatch.setattr(ml_atd, "_agora", lambda: atual["agora"])
    return atual


@pytest.fixture
def teto(monkeypatch):
    def _f(n: int) -> None:
        monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", n)

    _f(40)
    return _f
