# ruff: noqa: F811 — fixtures compartilhadas
"""Casos reais do fim de semana: foco, NF, cartão e fatos que chegam depois."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

import app.db as _db
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
)
from app.services.atendimento import ficha, gravar, humano, ia
from app.services.atendimento.constantes import RASCUNHO_BLOQUEADO, RASCUNHO_SUBSTITUIDO
from tests.test_atendimento_ia import (  # noqa: F401
    ModeloSequencia,
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
    _saida,
    envios,
    ia_ligada,
    modelo,
)


@pytest.fixture(autouse=True)
def sem_ficha_viva(monkeypatch):
    async def nada(*args, **kwargs):
        return []

    monkeypatch.setattr(ficha, "fichas_da_conversa", nada)


@pytest.mark.parametrize("final", ["Obg", "Olá", "6"])
async def test_nf_politica_sem_pedido_nao_se_perde_no_agradecimento(
    db, make_user, ia_ligada, modelo, envios, final
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["nota_fiscal"])
    await _msg(db, c, "Ainda não comprei. Vai com nota fiscal?", ha=timedelta(minutes=8))
    await _msg(db, c, final)
    r = await ia.gerar_rascunho(db, c)
    assert r.categoria == "nota_fiscal"
    assert r.validador_ok and not r.precisa_humano, r.motivo
    assert "nota fiscal" in r.texto and len(envios) == 1
    assert modelo.chamadas == []
    assert not (
        await humano.triar_conversa(db, c, so_humano=await humano.assuntos_so_humano(db))
    ).para_pessoa


@pytest.mark.parametrize(
    "texto",
    [
        "Já comprei, não recebi a nota fiscal",
        "Pode enviar o arquivo da nota fiscal?",
        "Emite nota fiscal no CNPJ?",
        "Comprei e quero o número da NF",
        "Vai com nota fiscal e garantia?",
        "Vai com nota fiscal? Quero reembolso",
    ],
)
async def test_nf_documental_e_assuntos_sensiveis_nao_usam_template(
    db, make_user, ia_ligada, modelo, envios, texto
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["nota_fiscal"])
    await _msg(db, c, texto)
    modelo.saida = _saida(
        "Vou verificar sua solicitação.", categoria="nota_fiscal", precisa_humano=True
    )
    r = await ia.gerar_rascunho(db, c)
    assert modelo.chamadas and r.precisa_humano
    assert envios == []
    assert not r.fatos.get("resposta_basica_aprovada")


@pytest.mark.parametrize("cartao", [True, False])
async def test_cartao_estruturado_pede_duvida_foto_real_pede_pessoa(
    db, make_user, ia_ligada, modelo, envios, cartao
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(
        db, make_user, pedido=None, modo="auto", auto_categorias=["saudacao_ou_vaga"]
    )
    await gravar.gravar_mensagem(
        db,
        c,
        externo_id="card",
        autor="cliente",
        texto=None,
        enviada_em=datetime.now(UTC) - timedelta(minutes=5),
        tipo="produto" if cartao else "imagem",
        payload={"source": "openapi", "message_type": "item"} if cartao else {},
    )
    await db.commit()
    r = await ia.gerar_rascunho(db, c)
    if cartao:
        assert r.validador_ok and not r.precisa_humano, r.motivo
        assert "podemos ajudar" in r.texto and len(envios) == 1
    else:
        assert r.status == RASCUNHO_BLOQUEADO and r.precisa_humano
        assert envios == []
    assert modelo.chamadas == []


async def test_pergunta_fica_no_prompt_depois_de_muitos_menus_e_opcao6(
    db, make_user, ia_ligada, modelo, envios
):
    c = await _conversa(db, make_user, pedido=None)
    await _msg(db, c, "A pulseira é P/M ou M/G?", ha=timedelta(minutes=20))
    for i in range(10):
        await _msg(
            db,
            c,
            "Olá, a sua mensagem foi recebida. Aguarde um atendente.",
            autor="loja",
            ha=timedelta(minutes=19 - i),
        )
    await _msg(db, c, "6")
    modelo.saida = _saida("Olá! Conte sua dúvida.", categoria="agradecimento")
    r = await ia.gerar_rascunho(db, c)
    assert "A pulseira é P/M ou M/G?" in modelo.usuario
    assert "PERGUNTAS AINDA SEM RESPOSTA" in modelo.usuario
    assert r.precisa_humano and "pergunta concreta pendente" in r.motivo
    assert len(await ia._mensagens_recentes(db, c)) <= ia.MAX_TROCAS_LIDAS
    assert envios == []


@pytest.mark.parametrize(
    "texto",
    [
        "Por que o nome do remetente é o meu nome?",
        "Quem vai receber no endereço da empresa são meus colegas",
    ],
)
async def test_etiqueta_e_recebimento_nao_sao_alteracao_de_endereco(
    db, make_user, ia_ligada, modelo, texto
):
    assert "endereco" not in ia.pistas_de_categoria(texto)
    c = await _conversa(db, make_user, pedido=None)
    await _msg(db, c, texto)
    modelo.saida = _saida("Vou verificar com a equipe.", categoria="duvida_produto")
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano and ("procedimento" in r.motivo or "recebimento" in r.motivo)


@pytest.mark.parametrize(
    "texto",
    [
        "Quero mudar o endereço",
        "O endereço está errado",
        "Meu endereço é outro",
    ],
)
def test_alteracao_de_endereco_continua_sensivel(texto):
    assert "endereco" in ia.pistas_de_categoria(texto)


async def test_novo_pedido_reavalia_mesmo_gatilho_uma_vez_sem_nova_mensagem(
    db, make_user, ia_ligada, modelo, envios
):
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["prazo_envio"])
    gatilho = await _msg(db, c, "Já realizei o pedido, qual o prazo de envio?")
    modelo.saida = _saida(
        "Pode conferir o número do pedido?", categoria="prazo_envio", precisa_humano=True
    )
    antigo = await ia.gerar_rascunho(db, c)
    assert "conversa sem pedido identificado" in antigo.motivo
    await db.execute(
        update(AtendimentoRascunho)
        .where(AtendimentoRascunho.id == antigo.id)
        .values(created_at=func.now() - timedelta(minutes=10))
    )
    await db.commit()
    await _pedido_completo(db)
    c.pedido_marketplace = "250925ABCDEF12"
    await db.commit()
    modelo.saida = _saida(
        "Seu pedido foi enviado; acompanhe pelo rastreio {rastreio}.", categoria="prazo_envio"
    )
    assert await ia.reavaliar_contextos(db) == 1
    novo = await db.scalar(
        select(AtendimentoRascunho)
        .where(AtendimentoRascunho.conversa_id == c.id)
        .order_by(AtendimentoRascunho.created_at.desc())
        .limit(1)
    )
    assert novo.id != antigo.id and novo.mensagem_gatilho_id == gatilho.id
    assert novo.fatos["reavaliacao_contexto"]["n"] == 1
    assert not novo.fatos.get(ia.FATO_PEDIDA_PELA_TELA)
    await db.refresh(antigo)
    assert antigo.status == RASCUNHO_SUBSTITUIDO
    assert len(envios) == 1
    chamadas = len(modelo.chamadas)
    assert await ia.reavaliar_contextos(db) == 0
    assert len(modelo.chamadas) == chamadas


async def test_nf_nao_recebida_com_pedido_achado_continua_pessoa_mesmo_modelo_otimista(
    db, make_user, ia_ligada, modelo, envios
):
    await _pedido_completo(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, modo="auto", auto_categorias=["nota_fiscal"])
    await _msg(db, c, "Não recebi a nota fiscal. Pode mandar o arquivo?")
    modelo.saida = _saida(
        "Vou verificar sua nota fiscal.", categoria="nota_fiscal", precisa_humano=False
    )
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano and "nota fiscal não recebida" in r.motivo
    assert envios == []


async def test_nf_template_desliga_apos_edicao_manual(db, make_user, ia_ligada, modelo, envios):
    from app.models import AtendimentoRegra

    await _importar_manual_base(db)
    regra = await db.scalar(
        select(AtendimentoRegra).where(AtendimentoRegra.categoria == "nota_fiscal")
    )
    regra.faca = "A política de NF mudou: a equipe confirma cada caso."
    await db.commit()
    c = await _conversa(db, make_user, pedido=None)
    await _msg(db, c, "Vai com nota fiscal?")
    modelo.saida = _saida(
        "Vou verificar com a equipe.", categoria="nota_fiscal", precisa_humano=True
    )
    r = await ia.gerar_rascunho(db, c)
    assert modelo.chamadas and r.precisa_humano
    assert not r.fatos.get("resposta_basica_aprovada")


@pytest.mark.parametrize(
    "texto",
    [
        "Emite NF e tem NFC?",
        "Tem NF e vem carregador?",
        "Tem NF e bateria boa?",
        "Tem NF e quais dimensões?",
    ],
)
async def test_template_nf_nao_apaga_segunda_demanda(db, make_user, ia_ligada, modelo, texto):
    await _importar_manual_base(db)
    c = await _conversa(db, make_user, pedido=None)
    await _msg(db, c, texto)
    modelo.saida = _saida(
        "Vou conferir os detalhes para você.", categoria="nota_fiscal", precisa_humano=True
    )
    r = await ia.gerar_rascunho(db, c)
    assert modelo.chamadas
    assert not r.fatos.get("resposta_basica_aprovada")


async def test_especializacao_cortada_do_prompt_ainda_desliga_template(
    db, make_user, ia_ligada, modelo, monkeypatch
):
    from app.models import AtendimentoRegra

    await _importar_manual_base(db)
    db.add(
        AtendimentoRegra(
            tipo="categoria",
            categoria="estoque_variacao",
            plataforma="shopee",
            quando="Pergunta de estoque nesta loja",
            faca="A equipe confirma a disponibilidade.",
        )
    )
    await db.commit()
    original = ia.montar_resposta

    def cortar(*args, **kwargs):
        prompt = original(*args, **kwargs)
        prompt.regras = [r for r in prompt.regras if r.plataforma is None]
        return prompt

    monkeypatch.setattr(ia, "montar_resposta", cortar)
    c = await _conversa(db, make_user, pedido=None)
    await _msg(db, c, "Não tem mais a cor Starlight?")
    modelo.saida = _saida(
        "Vou conferir a disponibilidade.", categoria="estoque_variacao", precisa_humano=True
    )
    r = await ia.gerar_rascunho(db, c)
    assert len(modelo.chamadas) == 2
    assert r.precisa_humano and not r.fatos.get("resposta_basica_aprovada")


async def test_insistencia_nao_some_depois_de_muitos_menus(
    db, make_user, ia_ligada, modelo, envios
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["pede_atendente"])
    await _msg(db, c, "Quero falar com um atendente", ha=timedelta(hours=2))
    await _msg(db, c, "Conte sua dúvida por aqui", autor="loja", ha=timedelta(hours=1))
    for i in range(10):
        await _msg(
            db,
            c,
            "Olá, a sua mensagem foi recebida. Aguarde um atendente.",
            autor="loja",
            ha=timedelta(minutes=20 - i),
        )
    await _msg(db, c, "6")
    modelo.saida = _saida("Olá! Conte sua dúvida.", categoria="pede_atendente")
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano and "insistiu" in r.motivo
    assert envios == []


@pytest.mark.parametrize("autorizado", [True, False])
async def test_ficha_literal_tem_excecao_estreita_sem_remover_gate_geral(
    db, make_user, ia_ligada, modelo, envios, monkeypatch, autorizado
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["espec_mala"])
    c.anuncio_titulo = "Mala compacta"
    await db.commit()
    await _msg(db, c, "Qual é o peso?")

    async def fonte(*args, **kwargs):
        return [
            {
                "fonte": "loja (Shopee), agora" if autorizado else "outro anúncio do mesmo produto",
                "de_onde": "anúncio ligado à conversa",
                "titulo": c.anuncio_titulo,
                "atributos": ["Peso do produto: 2 kg"],
            }
        ]

    monkeypatch.setattr(ficha, "fichas_da_conversa", fonte)
    modelo.saida = _saida("O peso é 2 kg.", categoria="espec_mala")
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano is not autorizado, r.motivo
    assert len(envios) == int(autorizado)
    assert bool(r.fatos.get("resposta_ficha_literal")) is autorizado
    assert len(modelo.chamadas) == (1 if autorizado else 2)


@pytest.mark.parametrize("intervencao", ["sugestao", "avaliacao"])
async def test_reavaliacao_nao_substitui_sugestao_manual_que_chegou_no_intervalo(
    db, make_user, ia_ligada, modelo, envios, monkeypatch, intervencao
):
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["prazo_envio"])
    await _msg(db, c, "Já comprei, qual o prazo de envio?")
    modelo.saida = _saida(
        "Confira o número do pedido.", categoria="prazo_envio", precisa_humano=True
    )
    antigo = await ia.gerar_rascunho(db, c)
    await db.execute(
        update(AtendimentoRascunho)
        .where(AtendimentoRascunho.id == antigo.id)
        .values(created_at=func.now() - timedelta(minutes=10))
    )
    await _pedido_completo(db)
    c.pedido_marketplace = "250925ABCDEF12"
    await db.commit()
    modelo.saida = _saida("O rastreio é {rastreio}.", categoria="prazo_envio")
    salvar_original = ia._salvar
    manuais = []

    async def pessoa_pediu(session, conversa, rascunho):
        if rascunho.fatos.get("reavaliacao_contexto"):
            if intervencao == "avaliacao":
                from app.models import AtendimentoAvaliacao

                manual = AtendimentoAvaliacao(rascunho_id=antigo.id, acao="observou", nota="erro")
            else:
                manual = AtendimentoRascunho(
                    conversa_id=conversa.id,
                    mensagem_gatilho_id=antigo.mensagem_gatilho_id,
                    texto="Sugestão pedida pela pessoa.",
                    status=RASCUNHO_BLOQUEADO,
                    fatos={ia.FATO_PEDIDA_PELA_TELA: True},
                )
            db.add(manual)
            await db.commit()
            manuais.append(manual)
        return await salvar_original(session, conversa, rascunho)

    monkeypatch.setattr(ia, "_salvar", pessoa_pediu)
    assert await ia.reavaliar_contextos(db) == 0
    assert manuais and envios == []
    await db.refresh(antigo)
    assert antigo.status == "pendente"


async def _aguardar_bloqueio(db, pid):
    """O teste só prossegue quando o Postgres confirma a espera real da trava."""
    async with asyncio.timeout(5):
        while not await db.scalar(select(func.cardinality(func.pg_blocking_pids(pid)))):
            await asyncio.sleep(0.01)


async def _origem_para_corrida(db, make_user):
    c = await _conversa(db, make_user)
    gatilho = await _msg(db, c, "Qual o prazo de envio?")
    antigo = AtendimentoRascunho(
        conversa_id=c.id, mensagem_gatilho_id=gatilho.id, texto="Confira o número do pedido."
    )
    db.add(antigo)
    await db.commit()
    return c, antigo


async def test_avaliacao_em_voo_faz_reavaliacao_desistir_sob_trava(db, make_user):
    c, antigo = await _origem_para_corrida(db, make_user)
    async with _db.SessionLocal() as tela, _db.SessionLocal() as cron:
        tela.add(AtendimentoAvaliacao(rascunho_id=antigo.id, acao="observou", nota="erro"))
        await tela.flush()  # A FK segura KEY SHARE antes de a avaliação commitar.
        conversa = await cron.get(AtendimentoConversa, c.id)
        novo = AtendimentoRascunho(
            conversa_id=c.id,
            texto="Rastreio encontrado.",
            fatos={"reavaliacao_contexto": {"rascunho_anterior_id": str(antigo.id)}},
        )
        pid = await cron.scalar(select(func.pg_backend_pid()))
        tarefa = asyncio.create_task(ia._salvar(cron, conversa, novo))
        try:
            await _aguardar_bloqueio(db, pid)
            await tela.commit()
            assert await asyncio.wait_for(tarefa, 5) is None
        finally:
            await tela.rollback()
            if not tarefa.done():
                tarefa.cancel()
                await asyncio.gather(tarefa, return_exceptions=True)
    await db.refresh(antigo)
    assert antigo.status == "pendente"
    assert await db.scalar(select(func.count()).select_from(AtendimentoRascunho)) == 1


async def test_sugestao_manual_bloqueada_serializa_antes_da_reavaliacao(
    db, make_user, monkeypatch
):
    c, antigo = await _origem_para_corrida(db, make_user)
    travar_original = ia._travar_gravacao_rascunho
    entrou, liberar = asyncio.Event(), asyncio.Event()
    async with _db.SessionLocal() as tela, _db.SessionLocal() as cron:
        async def segurar_gravacao(session, conversa):
            await travar_original(session, conversa)
            if session is tela:
                entrou.set()
                await liberar.wait()

        monkeypatch.setattr(ia, "_travar_gravacao_rascunho", segurar_gravacao)
        c_tela = await tela.get(AtendimentoConversa, c.id)
        c_cron = await cron.get(AtendimentoConversa, c.id)
        manual = AtendimentoRascunho(
            conversa_id=c.id,
            texto="Sugestão pedida pela pessoa.",
            status=RASCUNHO_BLOQUEADO,
            fatos={ia.FATO_PEDIDA_PELA_TELA: True},
        )
        novo = AtendimentoRascunho(
            conversa_id=c.id,
            texto="Rastreio encontrado.",
            fatos={"reavaliacao_contexto": {"rascunho_anterior_id": str(antigo.id)}},
        )
        pid = await cron.scalar(select(func.pg_backend_pid()))
        humana = asyncio.create_task(ia._salvar(tela, c_tela, manual))
        await asyncio.wait_for(entrou.wait(), 5)
        automatica = asyncio.create_task(ia._salvar(cron, c_cron, novo))
        try:
            await _aguardar_bloqueio(db, pid)
            liberar.set()
            assert await asyncio.wait_for(humana, 5) is manual
            assert await asyncio.wait_for(automatica, 5) is None
        finally:
            liberar.set()
            for tarefa in (humana, automatica):
                if not tarefa.done():
                    tarefa.cancel()
            await asyncio.gather(humana, automatica, return_exceptions=True)
    await db.refresh(antigo)
    assert antigo.status == "pendente"
    assert await db.scalar(select(func.count()).select_from(AtendimentoRascunho)) == 2


async def test_origem_bloqueada_aposenta_no_mesmo_commit_da_reavaliacao(db, make_user):
    c, antigo = await _origem_para_corrida(db, make_user)
    antigo.status = RASCUNHO_BLOQUEADO
    await db.commit()
    novo = AtendimentoRascunho(
        conversa_id=c.id,
        texto="Agora há dados do pedido.",
        fatos={"reavaliacao_contexto": {"rascunho_anterior_id": str(antigo.id)}},
    )
    assert await ia._salvar(db, c, novo) is novo
    async with _db.SessionLocal() as outra:
        origem = await outra.get(AtendimentoRascunho, antigo.id)
        gerado = await outra.get(AtendimentoRascunho, novo.id)
        assert origem.status == RASCUNHO_SUBSTITUIDO
        assert gerado.status == "pendente"


@pytest.mark.parametrize(
    "perguntas",
    [
        ["Qual é o peso?", "Quais são as dimensões?", "Tem idioma português?"],
        ["Emite nota fiscal?", "Vai com nota fiscal?", "Vem com nota fiscal?"],
    ],
)
async def test_demanda_sensivel_nao_some_apos_tres_perguntas_e_muitos_menus(
    db, make_user, ia_ligada, modelo, envios, perguntas
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(
        db, make_user, pedido=None, modo="auto", auto_categorias=["espec_mala", "nota_fiscal"]
    )
    sensivel = "Recebi produto errado, quero troca."
    await _msg(db, c, sensivel, ha=timedelta(minutes=30))
    for i, pergunta in enumerate(perguntas):
        await _msg(db, c, pergunta, ha=timedelta(minutes=25 - i))
    for i in range(8):
        await _msg(
            db, c, "Olá, a sua mensagem foi recebida. Aguarde um atendente.",
            autor="loja", ha=timedelta(minutes=20 - i),
        )
        await _msg(db, c, "obrigado", ha=timedelta(minutes=19.5 - i))
    categoria = "nota_fiscal" if "fiscal" in perguntas[0] else "espec_mala"
    modelo.saida = _saida("Vou conferir os detalhes do anúncio.", categoria=categoria)
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano and "troca" in r.motivo
    assert not r.fatos.get("resposta_basica_aprovada")
    assert sensivel in modelo.usuario
    recentes = await ia._mensagens_recentes(db, c)
    assert len(ia.perguntas_pendentes(recentes, recentes[-1])) == 4
    assert envios == []
    assert (
        await humano.triar_conversa(db, c, so_humano=await humano.assuntos_so_humano(db))
    ).para_pessoa


async def test_inicio_do_turno_fora_das_quarenta_mensagens_exige_pessoa(
    db, make_user, ia_ligada, modelo, envios
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["nota_fiscal"])
    await _msg(db, c, "Recebi produto errado, quero troca.", ha=timedelta(minutes=60))
    for i in range(40):
        await _msg(db, c, "obrigado", ha=timedelta(minutes=50 - i))
    await _msg(db, c, "Emite nota fiscal?")
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano and ia.MOTIVO_TURNO_INCOMPLETO in r.motivo
    assert envios == []
    assert (
        await humano.triar_conversa(db, c, so_humano=await humano.assuntos_so_humano(db))
    ).para_pessoa


@pytest.mark.parametrize("caso", ["mensagem", "soma"])
async def test_demandas_que_excedem_orcamento_do_prompt_exigem_pessoa(
    db, make_user, ia_ligada, modelo, envios, caso
):
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["espec_mala"])
    textos = (
        ["Qual é o peso? " + "detalhe " * 110]
        if caso == "mensagem"
        else ["Quais são as dimensões? " + "detalhe " * 80] * 10
    )
    for i, texto in enumerate(textos):
        await _msg(db, c, texto, ha=timedelta(minutes=20 - i))
    await _msg(db, c, "obrigado")
    modelo.saida = _saida("Vou conferir os detalhes do anúncio.", categoria="espec_mala")
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano and ia.MOTIVO_TURNO_INCOMPLETO in r.motivo
    recentes = await ia._mensagens_recentes(db, c)
    bloco = ia._bloco_perguntas_pendentes(recentes, ())
    transcricao = "\n".join(
        ia._linhas_da_conversa(recentes, (), teto=ia.MAX_CHARS_CONVERSA - len(bloco))
    )
    assert len(bloco) + len(transcricao) <= ia.MAX_CHARS_CONVERSA
    assert envios == []


async def test_demanda_sensivel_respondida_nao_contamina_turno_novo(
    db, make_user, ia_ligada, modelo, envios
):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["nota_fiscal"])
    await _msg(db, c, "Quero reembolso.", ha=timedelta(minutes=50))
    await _msg(db, c, "Sua solicitação foi atendida.", autor="loja", ha=timedelta(minutes=40))
    await _msg(db, c, "Emite nota fiscal?")
    r = await ia.gerar_rascunho(db, c)
    assert not r.precisa_humano, r.motivo
    assert len(envios) == 1
    assert not (
        await humano.triar_conversa(db, c, so_humano=await humano.assuntos_so_humano(db))
    ).para_pessoa


def test_mascara_que_expande_demanda_alem_do_teto_exige_pessoa():
    texto = "Qual é o peso? " + "Ana " * 140
    m = AtendimentoMensagem(autor="cliente", texto=texto)
    assert len(texto) < ia.MAX_CHARS_MENSAGEM
    assert len(ia.mascarar(texto, ("Ana",))) > ia.MAX_CHARS_MENSAGEM
    assert not ia.turno_incompleto([m], m)
    assert ia.turno_incompleto([m], m, nomes=("Ana",))


async def test_mascara_expandida_e_conferida_antes_do_envio_automatico(
    db, make_user, ia_ligada, modelo, envios
):
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(db, make_user, pedido=None, modo="auto", auto_categorias=["espec_mala"])
    c.comprador_nome = "Ana"
    await db.commit()
    await _msg(db, c, "Qual é o peso? " + "Ana " * 140)
    await _msg(db, c, "obrigado")
    modelo.saida = _saida("Vou conferir os detalhes do anúncio.", categoria="espec_mala")
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano and ia.MOTIVO_TURNO_INCOMPLETO in r.motivo
    assert envios == []
    assert "Ana" not in modelo.usuario
