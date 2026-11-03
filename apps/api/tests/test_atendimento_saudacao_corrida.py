# ruff: noqa: F401, F811 — fixtures compartilhadas, sem rede/modelo/envio real
"""Item 1 da análise de 31/10–02/11 (03/11/2026, "aplique as correções").

(a) A CORRIDA DA SAUDAÇÃO: a saudação ao cartão saía na hora e a pergunta do
    cliente, escrita antes dela, chegava ao banco 3 s depois (Vortan 41415df4 e
    a1929b16, 02/11) — a conversa ficava respondida e a pergunta, sem resposta.
    Agora a espera de silêncio também conta pela CHEGADA (`created_at`), todo envio
    sozinho confere o turno (`ia._trava_do_turno`), o cumprimento do legado espera o
    reenvio como o do agente, e a fala gravada depois da resposta da IA reabre a vez
    (`gravar.depois_da_resposta`).
(b) O CARTÃO SOZINHO, sem texto, não ganha saudação: o envio segura e a fila o
    processa sem resposta (`sem_demanda.processar`) — fora da Caixa Humano.
(c) O TETO: o envio sozinho nunca responde gatilho de mais de 3 h.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    AtendimentoRegra,
)
from app.services.atendimento import enviar, gravar, humano, ia, sem_demanda
from app.services.atendimento.constantes import (
    ORIGEM_IA,
    RASCUNHO_ENVIADO,
    RASCUNHO_PENDENTE,
    RASCUNHO_SUBSTITUIDO,
)
from tests.test_atendimento_enviar import _cenario as _cenario_envio
from tests.test_atendimento_enviar import ligado, plataforma
from tests.test_atendimento_ia import (
    PEDIDO_MKT,
    _cenario_auto,
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
    _saida,
    envios,
    ia_ligada,
    modelo,
)

CARTAO = {"message_type": "item", "content": {"item_id": 123}}
MENU = "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!"


# ─────────────── fábrica ───────────────


async def _regra_do_cartao(db) -> AtendimentoRegra:
    regra = AtendimentoRegra(
        tipo="categoria",
        categoria="saudacao_ou_vaga",
        quando=sem_demanda.REGRA_QUANDO,
        faca=sem_demanda.REGRA_FACA,
        ativa=True,
    )
    db.add(regra)
    await db.commit()
    return regra


async def _loja_auto(
    db, make_user, ia_ligada, *, categorias=("saudacao_ou_vaga", "rastreio"), manual_base=False
):
    ia_ligada.atendimento_auto_ativo = True
    await _pedido_completo(db)
    conversa = await _conversa(db, make_user, modo="auto", auto_categorias=list(categorias))
    if manual_base:
        # O Manual base inteiro (com a regra aprovada do cartão): os assuntos de verdade.
        await _importar_manual_base(db)
    else:
        await _regra_do_cartao(db)
    return conversa


async def _cartao(db, conversa, *, ha=timedelta(minutes=2, seconds=18), externo="card"):
    card, _ = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=externo,
        autor="cliente",
        texto=None,
        tipo="produto",
        enviada_em=datetime.now(UTC) - ha,
        payload=dict(CARTAO),
    )
    await db.commit()
    return card


async def _chegou_ha(db, conversa, quanto: timedelta) -> None:
    """Simula a chegada ao banco no passado (o `created_at` é do servidor)."""
    await db.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id == conversa.id)
        .values(created_at=func.now() - quanto)
    )
    await db.commit()


async def _resposta_ia(db, conversa, texto, *, ha, gatilho=None, enviada=True):
    """A resposta da IA já gravada (a que o envio sozinho deixou na conversa)."""
    rascunho = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=gatilho.id if gatilho is not None else None,
        texto=texto,
        categoria="saudacao_ou_vaga",
        status=RASCUNHO_ENVIADO,
        precisa_humano=False,
        validador_ok=True,
        fatos={},
    )
    db.add(rascunho)
    await db.flush()
    m = AtendimentoMensagem(
        conversa_id=conversa.id,
        autor="loja",
        origem=ORIGEM_IA,
        tipo="texto",
        texto=texto,
        enviada_em=(datetime.now(UTC) - ha) if enviada else None,
        status="enviada" if enviada else "enviando",
        rascunho_id=rascunho.id,
        payload={},
    )
    db.add(m)
    await db.flush()
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    return m


async def _cliente(db, conversa, texto, *, ha, externo=None):
    m, criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=externo or f"c-{uuid4().hex[:8]}",
        autor="cliente",
        texto=texto,
        enviada_em=datetime.now(UTC) - ha,
    )
    await db.commit()
    assert criada
    return m


# ─────────────── (a) a fala que a resposta da IA não viu ───────────────


def test_depois_da_resposta_fica_logo_depois_e_na_ordem_original():
    resposta = datetime(2026, 11, 2, 22, 41, 2, 203000, tzinfo=UTC)
    a = gravar.depois_da_resposta(resposta, resposta - timedelta(minutes=1, seconds=36))
    b = gravar.depois_da_resposta(resposta, resposta - timedelta(seconds=17))
    antiga = gravar.depois_da_resposta(resposta, resposta - timedelta(hours=5))
    assert resposta < antiga < a < b < resposta + timedelta(seconds=1)
    # Idempotente: o mesmo relógio original dá o mesmo lugar.
    assert a == gravar.depois_da_resposta(resposta, resposta - timedelta(minutes=1, seconds=36))


async def test_corrida_41415df4_pergunta_gravada_depois_da_saudacao_reabre_a_vez(
    db, make_user, ia_ligada
):
    """O 41415df4 (02/11): cartão 22:38:44, saudação 22:41:02, pergunta escrita às
    22:39:26 e gravada às 22:41:05. Antes: respondida para sempre. Agora: reabre."""
    conversa = await _loja_auto(db, make_user, ia_ligada)
    card = await _cartao(db, conversa)
    saudacao = await _resposta_ia(
        db, conversa, ia.TEXTO_CARTAO_APROVADO, ha=timedelta(seconds=3), gatilho=card
    )
    assert not conversa.aguardando_resposta

    pergunta = await _cliente(
        db, conversa, "Qual a capacidade da bateria?", ha=timedelta(minutes=1, seconds=36)
    )

    assert conversa.aguardando_resposta and conversa.situacao == "aberta"
    assert pergunta.enviada_em > saudacao.enviada_em
    marca = pergunta.payload[gravar.CHAVE_FORA_DE_ORDEM]
    assert marca["resposta_id"] == str(saudacao.id)
    assert datetime.fromisoformat(marca["enviada_em_plataforma"]) < saudacao.enviada_em
    # Durável: o recálculo do zero (o do envio, o da fila) não fecha de novo.
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta
    # O turno que a IA lê agora é a pergunta, depois da saudação.
    gatilho = await ia._ultima_do_cliente(db, conversa)
    recentes = await ia._mensagens_recentes(db, conversa)
    assert gatilho.id == pergunta.id
    assert [m.id for m in ia.rajada_do_cliente(recentes, gatilho)] == [pergunta.id]
    assert humano.ia_sem_resposta(conversa, datetime.now(UTC) + timedelta(minutes=11))


async def test_corrida_a1929b16_duas_perguntas_na_mesma_leva_mantem_a_ordem(
    db, make_user, ia_ligada
):
    """O a1929b16 (02/11): as duas perguntas (09:36:07 e 09:36:40) chegaram juntas,
    3 s depois da saudação (09:38:03) — e a ordem da leva não é a do cliente."""
    conversa = await _loja_auto(db, make_user, ia_ligada)
    card = await _cartao(db, conversa, ha=timedelta(minutes=2, seconds=28))
    await _resposta_ia(db, conversa, ia.TEXTO_CARTAO_APROVADO, ha=timedelta(0), gatilho=card)
    segunda = await _cliente(db, conversa, "das marcas Doogee ou Uranyx", ha=timedelta(seconds=83))
    primeira = await _cliente(
        db, conversa, "Ainda tem modelo resistente?", ha=timedelta(seconds=116)
    )
    assert conversa.aguardando_resposta
    gatilho = await ia._ultima_do_cliente(db, conversa)
    recentes = await ia._mensagens_recentes(db, conversa)
    assert gatilho.id == segunda.id
    assert [m.id for m in ia.rajada_do_cliente(recentes, gatilho)] == [primeira.id, segunda.id]


async def test_historia_antiga_e_resposta_de_pessoa_nao_sao_reordenadas(db, make_user, ia_ligada):
    conversa = await _loja_auto(db, make_user, ia_ligada)
    card = await _cartao(db, conversa, ha=timedelta(hours=2))
    resposta = await _resposta_ia(
        db, conversa, "O pedido saiu hoje.", ha=timedelta(minutes=1), gatilho=card
    )
    # A primeira leitura traz história de mais de 15 min antes da resposta: fica onde está.
    velha = await _cliente(db, conversa, "mensagem de antes", ha=timedelta(minutes=40))
    assert velha.enviada_em < resposta.enviada_em
    assert gravar.CHAVE_FORA_DE_ORDEM not in (velha.payload or {})
    assert not conversa.aguardando_resposta
    # Só a resposta da IA: a de pessoa (Duoke) não reordena.
    await _msg(db, conversa, "Já verifiquei.", autor="loja", ha=timedelta(seconds=5))
    depois = await _cliente(db, conversa, "e a nota?", ha=timedelta(seconds=30))
    assert gravar.CHAVE_FORA_DE_ORDEM not in (depois.payload or {})
    assert not conversa.aguardando_resposta


@pytest.mark.parametrize("antes_da_linha", [False, True])
async def test_fala_que_chega_com_a_resposta_da_ia_em_voo_reabre(
    db, make_user, ligado, plataforma, monkeypatch, antes_da_linha
):
    """O envio terminou (o relógio da resposta passa a ser o de agora) com a fala do
    cliente gravada pelo sync no meio — escrita antes (`antes_da_linha`) ou durante o voo."""
    monkeypatch.setattr(get_settings(), "atendimento_auto_ativo", True)
    conversa, _ = await _cenario_envio(db, make_user, modo="auto")
    gatilho = await ia._ultima_do_cliente(db, conversa)
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=gatilho.id,
        texto="Olá! O seu pedido já saiu e está a caminho.",
        categoria="rastreio",
        status=RASCUNHO_PENDENTE,
        precisa_humano=False,
        validador_ok=True,
    )
    db.add(r)
    await db.commit()

    async def sync_no_meio():
        async with _db.SessionLocal() as outra:
            c = await outra.get(AtendimentoConversa, conversa.id)
            await gravar.gravar_mensagem(
                outra,
                c,
                externo_id="c-no-voo",
                autor="cliente",
                texto="E vem com nota fiscal?",
                enviada_em=datetime.now(UTC) - timedelta(seconds=20 if antes_da_linha else 0),
            )
            await outra.commit()

    plataforma.antes = sync_no_meio
    m = await enviar.enviar_resposta(
        db, conversa, r.texto, user=None, rascunho_id=r.id, origem=ORIGEM_IA
    )
    assert m.status == "enviada"
    await db.refresh(conversa)
    nova = await db.scalar(
        select(AtendimentoMensagem)
        .where(AtendimentoMensagem.externo_id == "c-no-voo")
        .execution_options(populate_existing=True)
    )
    assert nova.enviada_em > m.enviada_em
    assert nova.payload[gravar.CHAVE_FORA_DE_ORDEM]["resposta_id"] == str(m.id)
    assert conversa.aguardando_resposta


# ─────────────── (a) a espera pela chegada e o turno na hora do envio ───────────────


@pytest.mark.espera_na_chegada
async def test_a_espera_conta_pela_chegada_e_nao_so_pelo_relogio_da_plataforma(
    db, make_user, ia_ligada, modelo, envios
):
    """A pergunta escrita há 5 min (o relógio da plataforma "já passou dos 90 s") mas
    que acabou de chegar ao banco espera: a rajada pode estar a caminho."""
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    assert await ia.gerar_pendentes(db) == 0
    assert modelo.chamadas == [] and envios == []
    await _chegou_ha(db, conversa, timedelta(minutes=2))
    assert await ia.gerar_pendentes(db) == 1
    assert len(envios) == 1


@pytest.mark.espera_na_chegada
async def test_o_turno_na_hora_do_envio(db, make_user, ia_ligada, modelo, envios):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    await _chegou_ha(db, conversa, timedelta(minutes=2))
    ia_ligada.atendimento_auto_ativo = False  # só gera: o envio é conferido abaixo
    r = await ia.gerar_rascunho(db, conversa)
    ia_ligada.atendimento_auto_ativo = True
    assert r is not None and r.fatos[ia.FATO_CHEGADA_VISTA]
    assert await ia._trava_do_turno(db, conversa, r) is None

    # Chegou mensagem com o relógio de ANTES do gatilho (o sync atrasou): o rascunho
    # não a leu — e o `_cobre` sozinho não veria (o gatilho continua o último).
    tardia = await _cliente(db, conversa, "e vem com nota?", ha=timedelta(minutes=6))
    assert (await ia._ultima_do_cliente(db, conversa)).id == r.mensagem_gatilho_id
    assert await ia._trava_do_turno(db, conversa, r) == ia.MOTIVO_TURNO_MUDOU
    await db.delete(tardia)
    await db.commit()

    # A fala chegou há pouco (outro rascunho, sem a marca): espera a chegada.
    r.fatos = {k: v for k, v in r.fatos.items() if k != ia.FATO_CHEGADA_VISTA}
    await db.commit()
    nova = await _cliente(db, conversa, "alô?", ha=timedelta(minutes=7))
    assert await ia._trava_do_turno(db, conversa, r) == ia.MOTIVO_ESPERA_CHEGADA
    await _chegou_ha(db, conversa, timedelta(minutes=2))
    assert await ia._trava_do_turno(db, conversa, r) is None
    await db.delete(nova)
    await db.commit()

    # O cliente escreveu de novo depois do gatilho: o turno mudou.
    await _cliente(db, conversa, "alguém?", ha=timedelta(seconds=10))
    await _chegou_ha(db, conversa, timedelta(minutes=2))
    assert await ia._trava_do_turno(db, conversa, r) == ia.MOTIVO_TURNO_MUDOU
    assert envios == []


async def _cumprimento_do_legado(db, make_user, ia_ligada, modelo):
    """O "card + oi" no caminho que NÃO é do agente: o rascunho do legado só com o
    cumprimento ("Olá! Como podemos ajudar?") para a última fala do cliente."""
    conversa = await _cenario_auto(
        db, make_user, ia_ligada, modelo, auto_categorias=["rastreio", "saudacao_ou_vaga"]
    )
    gatilho = await ia._ultima_do_cliente(db, conversa)
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=gatilho.id,
        texto="Olá! Como podemos ajudar?",
        categoria="saudacao_ou_vaga",
        status=RASCUNHO_PENDENTE,
        precisa_humano=False,
        validador_ok=True,
        confianca=0.95,
        prompt_versao=ia.PROMPT_VERSAO,
        fatos={},
    )
    db.add(r)
    await db.commit()
    assert not ia.rascunho_do_agente(r)
    return conversa, r


async def _nasceu_ha(db, r, quanto):
    await db.execute(
        update(AtendimentoRascunho)
        .where(AtendimentoRascunho.id == r.id)
        .values(created_at=func.now() - quanto)
    )
    await db.commit()


async def test_cumprimento_do_legado_espera_o_reenvio_e_cai_se_o_cliente_escreveu(
    db, make_user, ia_ligada, modelo, envios
):
    """Antes só o cumprimento do AGENTE esperava o reenvio; o do legado (a saudação
    aprovada) saía na hora. Agora espera, e o reenvio confere o turno."""
    conversa, r = await _cumprimento_do_legado(db, make_user, ia_ligada, modelo)
    assert not await ia._talvez_enviar(db, conversa, r)
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == ia.MOTIVO_GENERICA_ESPERA
    assert envios == []
    await _nasceu_ha(db, r, timedelta(minutes=3))
    # A pergunta de verdade chegou no meio: o cumprimento não sai mais.
    await _cliente(db, conversa, "qual o prazo de entrega?", ha=timedelta(seconds=30))
    assert await ia.reenviar_pendentes(db) == 0
    assert envios == []


async def test_cumprimento_do_legado_sai_no_reenvio_com_o_turno_igual(
    db, make_user, ia_ligada, modelo, envios
):
    conversa, r = await _cumprimento_do_legado(db, make_user, ia_ligada, modelo)
    assert not await ia._talvez_enviar(db, conversa, r)
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == ia.MOTIVO_GENERICA_ESPERA
    await _nasceu_ha(db, r, timedelta(minutes=3))
    assert await ia.reenviar_pendentes(db) == 1
    assert [e["rascunho_id"] for e in envios] == [r.id]


# ─────────────── (b) o cartão sozinho ───────────────


def _m(autor, texto=None, *, payload=None, minutos=5, origem=None):
    return SimpleNamespace(
        id=uuid4(),
        autor=autor,
        texto=texto,
        tipo="produto" if payload else "texto",
        payload=payload or {},
        origem=origem or ("cliente" if autor == "cliente" else "externo"),
        status="recebida" if autor == "cliente" else "enviada",
        enviada_em=datetime(2026, 11, 2, 22, tzinfo=UTC) - timedelta(minutes=minutos),
    )


def test_turno_so_de_cartao_e_so_o_cartao_sem_texto():
    card = _m("cliente", payload=dict(CARTAO))
    assert sem_demanda.turno_so_de_cartao([card], card)
    # Com o menu do Duoke depois: continua só cartão (e "já acolhido").
    menu = _m("loja", MENU, minutos=4)
    assert sem_demanda.turno_so_de_cartao([card, menu], card)
    # O cartão COM texto e o cartão com pergunta junto: comportamento de antes.
    com_texto = _m("cliente", "tem na cor azul?", payload=dict(CARTAO))
    assert not sem_demanda.turno_so_de_cartao([com_texto], com_texto)
    pergunta = _m("cliente", "tem na cor azul?", minutos=6)
    assert not sem_demanda.turno_so_de_cartao([pergunta, card], card)
    oi = _m("cliente", "bom dia", minutos=6)
    assert not sem_demanda.turno_so_de_cartao([oi, card], card)
    # A pergunta já respondida não conta: o turno novo é só o cartão.
    resposta = _m("loja", "Temos sim.", minutos=5.5, origem="davinci_humano")
    assert sem_demanda.turno_so_de_cartao([pergunta, resposta, card], card)


async def test_cartao_sozinho_nao_ganha_saudacao_e_o_rascunho_fica_segurado(
    db, make_user, ia_ligada, modelo, envios
):
    conversa = await _loja_auto(db, make_user, ia_ligada, manual_base=True)
    await _cartao(db, conversa)
    r = await ia.gerar_rascunho(db, conversa)
    assert r is not None and r.texto == ia.TEXTO_CARTAO_APROVADO
    assert modelo.chamadas == []  # o atalho aprovado do Manual, sem o modelo
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == ia.MOTIVO_CARTAO_SEM_TEXTO
    assert envios == []
    # O reenvio também segura (não gasta a tentativa).
    await db.execute(
        update(AtendimentoRascunho)
        .where(AtendimentoRascunho.id == r.id)
        .values(created_at=func.now() - timedelta(minutes=3))
    )
    await db.commit()
    assert await ia.reenviar_pendentes(db) == 0
    assert envios == []
    assert await ia.redis.get(ia._CHAVE_REENVIO.format(ia_ligada.database_schema, r.id)) is None
    # A fila o processa sem resposta: a saudação segurada é aposentada e a conversa
    # sai da fila (não vai para a Caixa Humano como trabalho).
    assert await sem_demanda.processar(db, conversa.id, canal_autorizado_id=conversa.canal_id)
    await db.refresh(r)
    assert r.status == RASCUNHO_SUBSTITUIDO
    assert conversa.sem_resposta_necessaria and not conversa.aguardando_resposta
    auditoria = conversa.dados[sem_demanda.CHAVE_AUDITORIA]
    assert auditoria["motivo"] == sem_demanda.MOTIVO_SEM_TEXTO
    assert auditoria["rascunhos_substituidos"] == [str(r.id)]
    assert not humano.ia_sem_resposta(conversa, datetime.now(UTC) + timedelta(hours=1))
    assert envios == []


@pytest.mark.espera_na_chegada
async def test_cartao_sozinho_sem_o_menu_sai_da_fila_so_depois_da_chegada(
    db, make_user, ia_ligada, modelo, envios
):
    conversa = await _loja_auto(db, make_user, ia_ligada)
    await _cartao(db, conversa)
    agora = datetime.now(UTC)
    assert not await sem_demanda.processar(db, conversa.id, agora=agora)
    assert not conversa.sem_resposta_necessaria
    assert await sem_demanda.processar(db, conversa.id, agora=agora + timedelta(minutes=2))
    assert conversa.sem_resposta_necessaria and not conversa.aguardando_resposta
    assert modelo.chamadas == [] and envios == []
    # A pergunta depois do cartão reabre a conversa.
    await _cliente(db, conversa, "Qual a capacidade da bateria?", ha=timedelta(seconds=40))
    assert conversa.aguardando_resposta and not conversa.sem_resposta_necessaria


@pytest.mark.espera_na_chegada
async def test_rodada_inteira_cartao_e_pergunta_atrasada_ganham_a_resposta_certa(
    db, make_user, ia_ligada, modelo, envios
):
    """A rodada do cron de ponta a ponta no formato do 41415df4: o cartão chega, o
    cliente escreve a pergunta, e ela só chega ao banco minutos depois."""
    conversa = await _loja_auto(db, make_user, ia_ligada)
    modelo.saida = _saida("Seu pedido foi enviado pela {transportadora}; o rastreio é {rastreio}.")
    await _cartao(db, conversa)
    await _chegou_ha(db, conversa, timedelta(minutes=2))

    assert await ia.gerar_pendentes(db) == 0
    assert modelo.chamadas == [] and envios == []
    await db.refresh(conversa)
    assert conversa.sem_resposta_necessaria  # cartão processado, sem saudação

    await _cliente(db, conversa, "cadê meu pedido?", ha=timedelta(minutes=1, seconds=36))
    await db.refresh(conversa)
    assert conversa.aguardando_resposta
    assert await ia.gerar_pendentes(db) == 0  # acabou de chegar: espera
    assert envios == []

    await _chegou_ha(db, conversa, timedelta(minutes=2))
    assert await ia.gerar_pendentes(db) == 1
    assert len(envios) == 1 and "rastreio" in envios[0]["texto"].lower()


# ─────────────── (c) o teto de 3 h ───────────────


@pytest.mark.parametrize(
    ("idade", "sai"), [(timedelta(hours=4), False), (timedelta(hours=2, minutes=50), True)]
)
async def test_envio_sozinho_nunca_responde_gatilho_de_mais_de_3h(
    db, make_user, ia_ligada, modelo, envios, idade, sai
):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    cliente = await ia._ultima_do_cliente(db, conversa)
    cliente.enviada_em = datetime.now(UTC) - idade
    conversa.ultima_do_cliente_em = cliente.enviada_em
    await db.commit()
    r = await ia.gerar_rascunho(db, conversa)
    assert r is not None and r.status == RASCUNHO_PENDENTE
    if sai:
        assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "enviado" and len(envios) == 1
    else:
        assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == ia.MOTIVO_GATILHO_ANTIGO
        assert envios == []
        # Fica para a pessoa: a conversa continua esperando (Caixa Humano).
        assert conversa.aguardando_resposta


async def test_loja_que_entra_no_automatico_nao_manda_rascunho_velho(
    db, make_user, ia_ligada, modelo, envios
):
    """O 30/10: a Vortan e a Mini entraram no Automático e 18 saudações antigas saíram."""
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo, modo="copiloto")
    cliente = await ia._ultima_do_cliente(db, conversa)
    cliente.enviada_em = datetime.now(UTC) - timedelta(hours=5)
    conversa.ultima_do_cliente_em = cliente.enviada_em
    await db.commit()
    r = await ia.gerar_rascunho(db, conversa)
    assert r is not None and envios == []
    await db.execute(
        update(AtendimentoRascunho)
        .where(AtendimentoRascunho.id == r.id)
        .values(created_at=func.now() - timedelta(minutes=3))
    )
    await db.execute(
        update(AtendimentoCanal).where(AtendimentoCanal.id == conversa.canal_id).values(modo="auto")
    )
    await db.commit()
    assert await ia.reenviar_pendentes(db) == 0
    assert envios == []
    # Com o gatilho recente, o mesmo rascunho sai (o teto é só o de 3 h).
    cliente.enviada_em = datetime.now(UTC) - timedelta(minutes=20)
    conversa.ultima_do_cliente_em = cliente.enviada_em
    await db.commit()
    assert await ia.reenviar_pendentes(db) == 1
    assert [e["rascunho_id"] for e in envios] == [r.id]
