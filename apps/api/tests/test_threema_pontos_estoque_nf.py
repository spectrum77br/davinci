"""F1/C8b da tarefa 16: os avisos de Estoque e NF que liam o .env passam a ler
o Cadastro (Cadastros › Envios Threema) — itens 18 (pedido movido para
Aguardando Cancelamento), 19 (negativo fora da passada), 20 (kit sem
composição), 21 (Bling sem responder o estoque), 22 (Bling recusou a 83955)
e 23 (kit da prioridade de estoque precisa de conferência).

Um teste por ponto, PELO CAMINHO REAL da função (só o ThreemaClient é falso;
o Bling e o Redis são os falsos dos testes da NF automática):

* (a) Cadastro semeado com o .env VAZIO: manda para quem está no Cadastro,
  com a chave do envio e o remetente de hoje ("estoque");
* (b) desligado: não manda, e o carimbo segue o `ao_desligar` do catálogo.
  18 a 22 descartam — desligado sai ANTES das marcas de "já avisei", igual a
  hoje com o .env vazio: nada do período fica guardado e a passada seguinte
  só aponta o que ela mesma achar; 23 acumula — `avisado_at` só é carimbado
  quando o aviso sai, e ao religar sai tudo numa mensagem;
* (c) ninguém válido (a única pessoa da lista foi suspensa): não manda,
  acende o sino dos admins e grava o registro `ok=false`. Nos avisos com
  marca de "já avisei" (19 a 22) a marca NÃO é gasta: quem acertar a lista
  recebe o mesmo pedido/kit na passada seguinte. A tentativa que registra
  sai no máximo uma vez por hora nos que repetem a cada passada (19 a 21;
  o 22 não volta, então cada um fica no registro); falha de envio continua
  desfazendo a marca. Com a configuração antiga (emergência) e o .env
  vazio, o ponto sai antes das marcas, calado, como hoje.

E a semente da fatia: `semear` + `conferir` das 6 chaves dá 0 diferenças e,
depois dela, cada ponto manda EXATAMENTE para quem o legado mandaria (as duas
leis do desenho: nenhum aviso para de sair; nenhum vai para quem foi
suspenso).
"""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.models import (
    NfFaturamento,
    PrioridadeEstoqueMovimento,
    Product,
    ThreemaEnvioDestinatario,
    User,
    UserRole,
    UserStatus,
)
from app.services import (
    nf_auto_enfileirar,
    nf_emissao_gerar,
    threema,
    threema_envios,
    threema_registro,
)
from app.services import prioridade_estoque_movimentos as mov
from app.services.nf_auto_enfileirar import run_auto_enfileirar_nf
from app.services.threema_catalogo import CATALOGO, K
from tests.test_nf_auto_enfileirar import (
    _async_return,
    _FakeBlingSituacao,
    _redis_falso,
    _seed_loja,
    _seed_pedido,
    _sem_espera_do_tipo_de_envio,
    relogio,
)
from tests.test_threema_pontos_logistica_devolucoes import _Fake, _ligar, _registro, _sinos
from tests.test_threema_resolver import _usuario, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

# Fixtures importadas: as autouse da NF automática (Redis em memória, relógio
# parado e sem a espera do Flex) valem aqui como no módulo de origem.
_FIXTURES = (_redis_falso, relogio, _sem_espera_do_tipo_de_envio)

# As 6 chaves da C8b. O remetente de HOJE de todas é "estoque" (o `contexto`
# que o código passava ao ThreemaClient antes da fatia).
MINHAS = [
    K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO,
    K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE,
    K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO,
    K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING,
    K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO,
    K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA,
]
REMETENTE_DE_HOJE = "estoque"
# O .env que estes pontos liam (fica congelado como reserva de emergência).
_ENVS = ("nf_sem_estoque_threema_recipients", "threema_recipients")
_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _Fake.envios = []
    threema_envios.limpar_cache()
    threema_registro.reiniciar_disjuntor()
    s = get_settings()
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    monkeypatch.setattr(s, "threema_registro_ativo", True)
    for nome in _ENVS:  # o .env VAZIO: quem recebe só pode vir do Cadastro
        monkeypatch.setattr(s, nome, "")
    monkeypatch.setattr(threema, "ThreemaClient", _Fake)
    yield
    threema_envios.limpar_cache()


async def _pessoas(db):
    admin = await _usuario(db, "admin c8b", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c8b", "TSTJ0006")
    sumido = await _usuario(db, "sumido c8b", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, sumido


async def _dono(db) -> User:
    """Dono das lojas e produtos dos cenários. Não é admin: o sino dos admins
    é conferido."""
    email = "dono.c8b@davinci-test.com"
    u = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    return u or await _usuario(db, "dono c8b", None)


def _usa_o_bling(monkeypatch, fake) -> None:
    monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake))


def _marcas(prefixo: str) -> list[str]:
    return [c for c in nf_auto_enfileirar.redis.chaves if c.startswith(prefixo)]


async def _status(db, numero: str) -> str | None:
    return (
        await db.execute(
            select(NfFaturamento.status_faturamento).where(NfFaturamento.pedido_bling == numero)
        )
    ).scalar_one_or_none()


# ─────────────────────────────────────────────── os pontos, pelo caminho real


async def _movido(db, monkeypatch) -> str:
    """Item 18: a passada da NF automática move para Aguardando Cancelamento
    um pedido Shopee com estoque negativo."""
    n = next(_SEQ)
    loja, numero, sku = f"93{n:04d}", f"83{n:04d}", f"x18.{n}"
    dono = await _dono(db)
    await _seed_loja(db, dono, plataforma="shopee", bling_store_id=loja)
    await _seed_pedido(db, numero=numero, loja=loja, sku=sku, bling_id=710000 + n)
    db.add(Product(user_id=dono.id, sku=sku, name=sku, stock=-2))
    await db.commit()
    _usa_o_bling(monkeypatch, _FakeBlingSituacao())
    resumo = await run_auto_enfileirar_nf()
    assert resumo["sem_estoque"] == 1, resumo
    return numero


async def _fora_do_lote(db, monkeypatch) -> str:
    """Item 19: o FIFO aponta como negativo um pedido do ML, que a passada não
    confere — o Shopee mais antigo segue para a fila e o ML vira aviso."""
    n = next(_SEQ)
    dono = await _dono(db)
    shopee, ml, pid = f"93{n:04d}", f"94{n:04d}", 5000 + n
    antigo, novo = f"83{n:04d}", f"84{n:04d}"
    await _seed_loja(db, dono, plataforma="shopee", bling_store_id=shopee)
    await _seed_loja(db, dono, plataforma="ml", bling_store_id=ml, com_faturador=False)
    await _seed_pedido(
        db, numero=antigo, loja=shopee, sku="b1", bling_id=720000 + n, item_produto_id=pid
    )
    await _seed_pedido(db, numero=novo, loja=ml, sku="b1", bling_id=730000 + n, item_produto_id=pid)
    await db.commit()
    fake = _FakeBlingSituacao(saldos={pid: -1.0})
    _usa_o_bling(monkeypatch, fake)
    resumo = await run_auto_enfileirar_nf()
    assert resumo["sem_estoque"] == 0 and fake.chamadas == [], resumo
    return novo


async def _kit(db, monkeypatch) -> str:
    """Item 20: kit sem composição segurando peça com saldo negativo. A
    passada e o botão Enfileirar (routers/nf.py) chamam a mesma função."""
    n = next(_SEQ)
    dono = await _dono(db)
    kit = 950000 + n
    db.add(
        Product(
            user_id=dono.id,
            sku=f"kit.{n}",
            name="Kit",
            stock=0,
            formato="E",
            bling_product_id=kit,
        )
    )
    await db.commit()
    await nf_auto_enfileirar.avisar_kit_sem_composicao(db, [kit])
    return f"id Bling {kit}"


async def _sem_leitura(db, monkeypatch) -> str:
    """Item 21: pedido sem leitura do Bling há mais de 30 min — o relógio da
    passada (`_sem_leitura_longa`) chama o aviso agregado."""
    numero = f"85{next(_SEQ):04d}"
    adiados = {numero: ["b1"]}
    await nf_auto_enfileirar._sem_leitura_longa(db, [numero], adiados)
    nf_auto_enfileirar._agora_ts.avanca(31)
    assert await nf_auto_enfileirar._sem_leitura_longa(db, [numero], adiados) == adiados
    return numero


async def _recusa(db, monkeypatch) -> str:
    """Item 22: o Bling recusou a 83955 até o teto do dia (a passada tira o
    pedido da fila com 'erro' e chama o aviso)."""
    numero = f"86{next(_SEQ):04d}"
    await nf_auto_enfileirar._avisar_recusa_83955(db, {numero: ["b1"]})
    return numero


async def _kit_conferencia(db, monkeypatch) -> str:
    """Item 23: lançamento de kit da prioridade de estoque sem confirmação no
    Bling; o cron das :16/:46 (`manutencao_movimentos_sweep`) avisa."""
    pedido = f"87{next(_SEQ):04d}"
    db.add(
        PrioridadeEstoqueMovimento(
            pedido_bling=pedido,
            sku="dg053.ci",
            bling_product_id=101,
            operacao="E",
            quantidade=1,
            status="incerto",
            erro="timeout",
        )
    )
    await db.commit()
    _usa_o_bling(monkeypatch, None)  # sem Bling: o cron só avisa
    await mov.manutencao_movimentos_sweep()
    return pedido


PONTOS = {
    K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO: _movido,
    K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE: _fora_do_lote,
    K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO: _kit,
    K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING: _sem_leitura,
    K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO: _recusa,
    K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA: _kit_conferencia,
}


def test_as_chaves_da_c8b_estao_migradas_com_o_remetente_de_hoje():
    assert list(PONTOS) == MINHAS
    for chave in MINHAS:
        d = CATALOGO[chave]
        assert d.migrado and d.fatia == "F1", chave
        assert d.remetente == REMETENTE_DE_HOJE, chave


# ─────────────────────────────────────────────── (a) Cadastro com o .env vazio


@pytest.mark.parametrize("chave", MINHAS)
async def test_cadastro_com_env_vazio_manda_pela_chave_e_pelo_remetente_de_hoje(
    db, monkeypatch, chave
):
    _admin, joana, sumido = await _pessoas(db)
    # a lista do Cadastro tem quem foi suspenso também: ele fica de fora
    await semeado(db, chave, joana, sumido)
    assunto = await PONTOS[chave](db, monkeypatch)
    assert [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios] == [
        (chave, "cadastro", REMETENTE_DE_HOJE, ["TSTJ0006"])
    ]
    assert assunto in _Fake.envios[0]["texto"]


# ─────────────────────────────────────────────── (c) ninguém válido


@pytest.mark.parametrize("chave", MINHAS)
async def test_ninguem_valido_nao_manda_acende_o_sino_e_registra(db, monkeypatch, chave):
    admin, _joana, sumido = await _pessoas(db)
    # o .env tem gente: lista vazia NUNCA pode cair nele
    for nome in _ENVS:
        monkeypatch.setattr(get_settings(), nome, "TSTZ0009")
    await semeado(db, chave, sumido)
    await PONTOS[chave](db, monkeypatch)
    assert _Fake.envios == []
    assert [(a.user_id, a.escopo) for a in await _sinos(db, chave)] == [(None, "adm")]
    (linha,) = await _registro(db, chave)
    assert (linha.ok, linha.erro, linha.via, linha.destinatario_id, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        None,
        REMETENTE_DE_HOJE,
    )


# Os avisos com marca de "já avisei" (19 a 22), chamados direto, sempre com o
# mesmo assunto (é a passada seguinte, 2 min depois, achando o mesmo).
_DE_NOVO = {
    K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE: (
        lambda db: nf_auto_enfileirar._avisar_negativo_fora_do_lote(db, {"840001": ["b1"]}),
        nf_auto_enfileirar._AVISO_FORA_DO_LOTE_PREFIXO,
    ),
    K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO: (
        lambda db: nf_auto_enfileirar.avisar_kit_sem_composicao(db, [950001]),
        nf_auto_enfileirar._AVISO_KIT_SEM_COMPOSICAO_PREFIXO,
    ),
    K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING: (
        lambda db: nf_auto_enfileirar._avisar_sem_leitura(db, {"850001": ["b1"]}),
        nf_auto_enfileirar._AVISO_SEM_LEITURA_CHAVE,
    ),
    K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO: (
        lambda db: nf_auto_enfileirar._avisar_recusa_83955(db, {"860001": ["b1"]}),
        nf_auto_enfileirar._AVISO_RECUSA_83955_PREFIXO,
    ),
}


# Os que a passada acha de novo a cada 2 min enquanto valerem (19 a 21); o
# 22 tira o pedido da fila e não volta.
_REPETEM = [
    K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE,
    K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO,
    K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING,
]


async def _trocar_por(db, chave: str, quem: User) -> None:
    """O admin vê o sino e troca, em Cadastros › Envios Threema, quem estava
    na lista por `quem`."""
    await db.execute(
        update(ThreemaEnvioDestinatario)
        .where(ThreemaEnvioDestinatario.envio_chave == chave)
        .values(user_id=quem.id)
    )
    await db.commit()
    threema_envios.limpar_cache()


@pytest.mark.parametrize("chave", _REPETEM)
async def test_ninguem_valido_nao_gasta_a_marca_e_quem_acerta_a_lista_recebe(
    db, monkeypatch, chave
):
    """Ninguém válido não gasta a marca de "já avisei": depois que o admin
    acerta a lista, a passada seguinte avisa o MESMO pedido/kit (antes, a
    marca do dia ficava e o aviso só voltava no dia seguinte). Enquanto a
    lista não é acertada, as passadas de 2 min não repetem registro nem sino."""
    _admin, joana, sumido = await _pessoas(db)
    await semeado(db, chave, sumido)
    chamar, prefixo = _DE_NOVO[chave]
    await chamar(db)
    assert _Fake.envios == [] and _marcas(prefixo) == []
    assert len(await _registro(db, chave)) == 1 and len(await _sinos(db, chave)) == 1
    for _ in range(3):
        nf_auto_enfileirar._agora_ts.avanca(2)
        await chamar(db)
    assert _Fake.envios == [] and _marcas(prefixo) == []
    assert len(await _registro(db, chave)) == 1 and len(await _sinos(db, chave)) == 1
    await _trocar_por(db, chave, joana)
    nf_auto_enfileirar._agora_ts.avanca(2)
    await chamar(db)
    assert [(e["chave"], e["destinos"]) for e in _Fake.envios] == [(chave, ["TSTJ0006"])]
    assert _marcas(prefixo)
    await chamar(db)  # e a marca de agora vale: não repete
    assert len(_Fake.envios) == 1


@pytest.mark.parametrize("chave", _REPETEM)
async def test_ninguem_valido_registra_de_novo_so_depois_de_uma_hora(db, monkeypatch, chave):
    _admin, _joana, sumido = await _pessoas(db)
    await semeado(db, chave, sumido)
    chamar, _prefixo = _DE_NOVO[chave]
    await chamar(db)
    nf_auto_enfileirar._agora_ts.avanca(58)
    await chamar(db)
    assert len(await _registro(db, chave)) == 1
    nf_auto_enfileirar._agora_ts.avanca(3)
    await chamar(db)
    assert len(await _registro(db, chave)) == 2
    assert len(await _sinos(db, chave)) == 1  # o sino é um por dia
    assert _Fake.envios == []


async def test_recusa_83955_ninguem_valido_registra_cada_pedido(db, monkeypatch):
    """O 22 não volta (o pedido sai da fila com 'erro'): cada aviso que não
    saiu fica no registro, sem esperar a hora; o sino do dia é um só."""
    chave = K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO
    _admin, _joana, sumido = await _pessoas(db)
    await semeado(db, chave, sumido)
    await nf_auto_enfileirar._avisar_recusa_83955(db, {"860001": ["b1"]})
    nf_auto_enfileirar._agora_ts.avanca(2)
    await nf_auto_enfileirar._avisar_recusa_83955(db, {"860002": ["b2"]})
    assert _Fake.envios == []
    assert _marcas(nf_auto_enfileirar._AVISO_RECUSA_83955_PREFIXO) == []
    registro = await _registro(db, chave)
    assert len(registro) == 2 and not any(r.ok for r in registro)
    assert len(await _sinos(db, chave)) == 1


@pytest.mark.parametrize("chave", list(_DE_NOVO))
async def test_configuracao_antiga_vazia_sai_antes_das_marcas_como_hoje(db, monkeypatch, chave):
    """Emergência (vale a configuração antiga) com o .env vazio: o ponto sai
    antes das marcas, calado, como antes da F1 (o `ativo` do legado é sempre
    ligado; quem desliga hoje é a lista vazia). Quando o .env ganha alguém, a
    passada seguinte avisa o mesmo pedido/kit."""
    await _pessoas(db)
    monkeypatch.setattr(get_settings(), "threema_cadastro_ativo", False)
    chamar, prefixo = _DE_NOVO[chave]
    await chamar(db)
    assert _Fake.envios == [] and _marcas(prefixo) == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    monkeypatch.setattr(get_settings(), "nf_sem_estoque_threema_recipients", "TSTJ0006")
    nf_auto_enfileirar._agora_ts.avanca(2)
    await chamar(db)
    assert [(e["chave"], e["via"], e["destinos"]) for e in _Fake.envios] == [
        (chave, "legado", ["TSTJ0006"])
    ]


class _FakeQueFalha(_Fake):
    falhar = True

    async def send_to_all(self, texto, destinos):
        if _FakeQueFalha.falhar:
            raise RuntimeError("Threema fora do ar")
        return await super().send_to_all(texto, destinos)


@pytest.mark.parametrize("chave", list(_DE_NOVO))
async def test_falha_de_envio_continua_desfazendo_a_marca(db, monkeypatch, chave):
    """A retentativa de hoje não mudou: o Threema fora desfaz a marca e a
    passada seguinte manda."""
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana)
    monkeypatch.setattr(threema, "ThreemaClient", _FakeQueFalha)
    _FakeQueFalha.falhar = True
    chamar, prefixo = _DE_NOVO[chave]
    await chamar(db)
    assert _Fake.envios == [] and _marcas(prefixo) == []
    _FakeQueFalha.falhar = False
    await chamar(db)
    assert [(e["chave"], e["destinos"]) for e in _Fake.envios] == [(chave, ["TSTJ0006"])]
    assert _marcas(prefixo)


# ─────────────────────────────────────────────── (b) desligado, ponto a ponto


async def test_movido_desligado_descarta(db, monkeypatch):
    chave = K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    primeiro = await _movido(db, monkeypatch)
    assert _Fake.envios == []
    assert await _status(db, primeiro) == "sem_estoque"  # o pedido foi movido
    assert await _registro(db, chave) == []  # desligado não grava registro
    assert await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    # religado: a passada não volta ao pedido já movido (o aviso dele se
    # perdeu); o próximo movido sai normalmente, sozinho
    segundo = await _movido(db, monkeypatch)
    (envio,) = _Fake.envios
    assert envio["destinos"] == ["TSTJ0006"]
    assert f"Pedido {segundo}" in envio["texto"] and primeiro not in envio["texto"]


@pytest.mark.parametrize("chave", list(_DE_NOVO))
async def test_aviso_com_marca_desligado_descarta_sem_deixar_marca(db, monkeypatch, chave):
    """19 a 22 desligados: saem ANTES da marca de "já avisei" (igual a hoje
    com o .env vazio). Nada do período fica guardado: religado, o aviso só
    sai quando uma passada achar de novo — o que já sumiu não volta."""
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    chamar, prefixo = _DE_NOVO[chave]
    await chamar(db)
    assert _Fake.envios == []
    assert _marcas(prefixo) == []
    assert await _registro(db, chave) == [] and await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    assert _Fake.envios == []  # religar não despeja nada
    nf_auto_enfileirar._agora_ts.avanca(2)
    await chamar(db)  # a passada seguinte ainda acha: sai uma vez
    await chamar(db)
    assert [(e["chave"], e["destinos"]) for e in _Fake.envios] == [(chave, ["TSTJ0006"])]


async def test_negativo_fora_do_lote_desligado_nao_guarda_o_que_sumiu(db, monkeypatch):
    chave = K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    await nf_auto_enfileirar._avisar_negativo_fora_do_lote(db, {"840001": ["b1"]})
    await _ligar(db, chave, None)
    # o 840001 deixou de estar negativo enquanto estava desligado; agora é o 840002
    await nf_auto_enfileirar._avisar_negativo_fora_do_lote(db, {"840002": ["b2"]})
    (envio,) = _Fake.envios
    assert "Pedido 840002" in envio["texto"] and "840001" not in envio["texto"]


async def test_recusa_83955_desligado_descarta_pela_passada(db, monkeypatch):
    """A 5ª recusa do dia tira o pedido da fila com 'erro' e chama o aviso:
    desligado, ele se perde — a passada não volta a esse pedido."""
    chave = K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    dono = await _dono(db)
    await _seed_loja(db, dono, plataforma="shopee", bling_store_id="939001")
    await _seed_pedido(
        db, numero="839001", loja="939001", sku="b1", bling_id=739001, item_produto_id=5551
    )
    await db.commit()

    class _Recusa(_FakeBlingSituacao):
        async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
            raise RuntimeError("400")

    _usa_o_bling(
        monkeypatch,
        _Recusa(saldos={5551: -1.0}, orders={739001: {"id": 739001, "observacoes": ""}}),
    )
    for _ in range(nf_auto_enfileirar._MAX_RECUSAS_83955):
        resumo = await run_auto_enfileirar_nf()
    assert resumo["sem_estoque_recusado"] == 1
    assert await _status(db, "839001") == "erro"
    assert _Fake.envios == []
    assert _marcas(nf_auto_enfileirar._AVISO_RECUSA_83955_PREFIXO) == []
    await _ligar(db, chave, None)
    resumo = await run_auto_enfileirar_nf()
    assert resumo["candidatos"] == 0  # o pedido saiu da fila
    assert _Fake.envios == []


async def test_kit_conferencia_desligado_acumula(db, monkeypatch):
    chave = K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA
    assert CATALOGO[chave].ao_desligar == "acumula"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    primeiro = await _kit_conferencia(db, monkeypatch)
    segundo = await _kit_conferencia(db, monkeypatch)
    assert _Fake.envios == []

    async def _avisados() -> list:
        return list(
            (
                await db.execute(
                    select(PrioridadeEstoqueMovimento.avisado_at)
                    .where(PrioridadeEstoqueMovimento.pedido_bling.in_([primeiro, segundo]))
                    .execution_options(populate_existing=True)
                )
            ).scalars()
        )

    assert await _avisados() == [None, None]  # não carimbou: fica para quando religar
    await _ligar(db, chave, None)
    resumo = await mov.manutencao_movimentos_sweep()
    assert resumo["avisados"] == 2
    (envio,) = _Fake.envios
    assert envio["destinos"] == ["TSTJ0006"]
    assert f"Pedido {primeiro}" in envio["texto"] and f"Pedido {segundo}" in envio["texto"]
    assert None not in await _avisados()
    # e não repete
    assert (await mov.manutencao_movimentos_sweep())["avisados"] == 0
    assert len(_Fake.envios) == 1


# ─────────────────────────────────────────────── a semente da fatia


@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_ponto_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, variante
):
    await montar(db, monkeypatch, variante=variante)
    esperado = {
        c: list(threema_envios._dedupe((await threema_envios.legado(c, session=db)).ids))
        for c in MINHAS
    }
    await threema_envios.semear(db, fatia="F1", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in MINHAS]
    # o .env fica congelado como reserva, mas o ponto não lê mais: esvaziar
    # não muda quem recebe
    for nome in _ENVS:
        monkeypatch.setattr(get_settings(), nome, "")
    for chave in MINHAS:
        _Fake.envios = []
        await PONTOS[chave](db, monkeypatch)
        enviados = [(e["chave"], e["via"], e["destinos"]) for e in _Fake.envios]
        assert enviados == ([(chave, "cadastro", esperado[chave])] if esperado[chave] else []), (
            chave
        )
        if not esperado[chave]:
            # .env vazio hoje = calado: a semente desligou, nada de sino
            assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    grupo = [ID["londres"], ID["harry"], ID["heisenberg"], ID["cairo_sa"]]
    assert esperado == dict.fromkeys(MINHAS, [] if variante == "vazias" else grupo)


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(db, monkeypatch):
    c = await montar(db, monkeypatch)
    await threema_envios.semear(db, fatia="F1", chaves=MINHAS, aplicar=True)
    await db.commit()
    c.usuarios["londres"].status = UserStatus.SUSPENDED
    await db.commit()
    await _movido(db, monkeypatch)
    await _kit_conferencia(db, monkeypatch)
    assert [e["destinos"] for e in _Fake.envios] == [
        [ID["harry"], ID["heisenberg"], ID["cairo_sa"]]
    ] * 2
    # o legado (o .env) ainda mandaria para ele
    leg = await threema_envios.legado(K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO, session=db)
    assert ID["londres"] in leg.ids
