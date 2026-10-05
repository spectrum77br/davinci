"""O caminho único de saída: travas, em voo, resultado da plataforma e avaliação.

O que estes testes seguram, na ordem em que um erro custaria mais caro:

- mensagem para comprador NÃO se desenvia: ambíguo vira `revisar` e nunca
  se retenta; duas abas apertando "Enviar" juntas mandam UMA resposta;
- nada sai com o envio desligado, com a loja em `observar`, com a conversa
  bloqueada, sem loja por trás, ou com o texto reprovado pelo validador;
- o simulador (só local) percorre o caminho inteiro sem chamar plataforma;
- o que a pessoa fez com a sugestão da IA fica registrado — e o envio
  automático NÃO conta como aprovação de pessoa.

O validador e os adaptadores são de outros lotes: aqui entram falsos, pelo
contrato da seção 5 da spec.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import clientes, enviar, gravar, validador
from app.services.atendimento import shopee as adaptador_shopee
from app.services.atendimento.constantes import (
    MOTIVO_TUTA_SEM_ENVIO,
    MOTIVO_ZAP_SEM_ENVIO,
    ResultadoEnvio,
    motivo_canal_sem_envio,
)
from app.services.atendimento.enviar import EnvioRecusado

# ─────────────── falsos (contratos dos outros lotes) ───────────────


@pytest.fixture(autouse=True)
def _validador_falso(monkeypatch):
    """Validador pelo contrato: normaliza espaços; reprova vazio e WhatsApp."""

    def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
        return " ".join((texto or "").split())

    def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
        motivos = []
        if not texto.strip():
            motivos.append("Resposta vazia.")
        if "whatsapp" in texto.lower():
            motivos.append("Contato fora da loja (WhatsApp).")
        return motivos

    monkeypatch.setattr(validador, "normalizar", normalizar)
    monkeypatch.setattr(validador, "validar", validar)


@pytest.fixture
def ligado(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_auto_ativo", False)
    return s


class _Plataforma:
    """Adaptador falso da Shopee: grava as chamadas e devolve o combinado."""

    def __init__(self, monkeypatch) -> None:
        self.chamadas: list[str] = []
        self.resultado = ResultadoEnvio(ok=True, externo_id="plat-1", payload={"x": 1})
        self.antes = None  # corrotina rodada durante a "chamada" (corrida, adoção)
        self.levanta: Exception | None = None

        async def enviar_texto(session, conversa, integration, cliente, texto):
            self.chamadas.append(texto)
            if self.antes is not None:
                await self.antes()
            if self.levanta is not None:
                raise self.levanta
            return self.resultado

        async def cliente_falso(integration):
            return object()

        monkeypatch.setattr(adaptador_shopee, "enviar_texto", enviar_texto)
        monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_falso)


@pytest.fixture
def plataforma(monkeypatch) -> _Plataforma:
    return _Plataforma(monkeypatch)


# ─────────────── fábrica ───────────────

T_CLIENTE = datetime.now(UTC) - timedelta(minutes=30)


async def _cenario(
    db: AsyncSession,
    make_user,
    *,
    modo: str = "humano",
    arquivada: bool = False,
    texto_cliente: str = "cadê meu pedido?",
) -> tuple[AtendimentoConversa, User]:
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
        archived_at=datetime.now(UTC) if arquivada else None,
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo=modo, status="ok"
    )
    db.add(canal)
    await db.flush()
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="conv-1",
        pedido_marketplace="250925ABC",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="c-1",
        autor="cliente",
        texto=texto_cliente,
        enviada_em=T_CLIENTE,
    )
    await db.commit()
    return conversa, user


async def _rascunho(db: AsyncSession, conversa: AtendimentoConversa, texto: str):
    r = AtendimentoRascunho(
        conversa_id=conversa.id, texto=texto, categoria="rastreio", status="pendente"
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _mensagens_da_loja(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == conversa_id,
                    AtendimentoMensagem.autor == "loja",
                )
                .order_by(AtendimentoMensagem.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _recusa(coro) -> EnvioRecusado:
    with pytest.raises(EnvioRecusado) as e:
        await coro
    return e.value


# ─────────────── travas (nada vai à plataforma) ───────────────


async def test_envio_desligado_recusa_sem_gravar_nada(db, make_user, plataforma):
    conversa, user = await _cenario(db, make_user)
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "envio_desligado"
    assert plataforma.chamadas == []
    assert await _mensagens_da_loja(db, conversa.id) == []


async def test_loja_em_observar_nao_envia(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user, modo="observar")
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "canal_em_observacao"
    assert plataforma.chamadas == []


async def test_conversa_bloqueada_e_janela_fechada(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    conversa.situacao = "bloqueada"
    conversa.bloqueio_motivo = "mediação aberta"
    await db.commit()
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert (e.code, e.detail) == ("conversa_bloqueada", "mediação aberta")

    conversa.situacao = "aberta"
    conversa.pode_enviar_ate = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "conversa_bloqueada"
    assert plataforma.chamadas == []


async def test_sem_integracao(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user, arquivada=True)
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "sem_integracao"

    conversa.canal_id = None
    await db.commit()
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "sem_integracao"


async def test_plataforma_sem_adaptador_e_somente_leitura(db, make_user, ligado):
    conversa, user = await _cenario(db, make_user)
    conversa.plataforma = "instagram"
    await db.commit()
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "somente_leitura"


async def test_texto_invalido_devolve_os_motivos(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    e = await _recusa(
        enviar.enviar_resposta(db, conversa, "me chama no WhatsApp", user=user)
    )
    assert e.code == "texto_invalido"
    assert e.detail == ["Contato fora da loja (WhatsApp)."]
    e = await _recusa(enviar.enviar_resposta(db, conversa, "   ", user=user))
    assert e.code == "texto_invalido"
    assert plataforma.chamadas == []
    assert await _mensagens_da_loja(db, conversa.id) == []


async def test_ia_so_envia_com_o_automatico_ligado_nos_dois_lugares(
    db, make_user, ligado, plataforma, monkeypatch
):
    conversa, _user = await _cenario(db, make_user, modo="copiloto")
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=None, origem="davinci_ia"))
    assert e.code == "auto_desligado"

    # Canal em auto, mas a chave global desligada: continua recusando.
    await db.execute(update(AtendimentoCanal).values(modo="auto"))
    await db.commit()
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=None, origem="davinci_ia"))
    assert e.code == "auto_desligado"

    monkeypatch.setattr(get_settings(), "atendimento_auto_ativo", True)
    m = await enviar.enviar_resposta(db, conversa, "Olá!", user=None, origem="davinci_ia")
    assert m.status == "enviada"
    assert m.origem == "davinci_ia"


async def test_motivo_para_nao_enviar_espelha_as_travas(db, make_user, ligado):
    conversa, _user = await _cenario(db, make_user)
    assert await enviar.motivo_para_nao_enviar(db, conversa) is None

    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id, autor="loja", origem="davinci_humano", status="enviando"
        )
    )
    await db.commit()
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa is not None and recusa.code == "envio_em_andamento"

    await db.execute(update(AtendimentoCanal).values(modo="observar"))
    await db.commit()
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa is not None and recusa.code == "canal_em_observacao"


# ─────────────── simulador e plataforma ───────────────


async def test_simulador_ponta_a_ponta_sem_plataforma(
    db, make_user, ligado, plataforma, monkeypatch
):
    monkeypatch.setattr(get_settings(), "atendimento_simulador", True)
    plataforma.levanta = AssertionError("o simulador não pode chamar a plataforma")
    conversa, user = await _cenario(db, make_user)
    assert conversa.aguardando_resposta is True

    m = await enviar.enviar_resposta(db, conversa, "  Seu pedido  saiu hoje. ", user=user)

    assert plataforma.chamadas == []
    assert m.status == "enviada"
    assert m.externo_id.startswith("sim:")
    assert m.texto == "Seu pedido saiu hoje."  # o que SAIU é o normalizado
    assert m.autor == "loja" and m.origem == "davinci_humano"
    assert m.autor_user_id == user.id
    assert m.payload["envio"] == {"simulador": True}
    await db.refresh(conversa)
    assert conversa.aguardando_resposta is False
    assert conversa.prazo_resposta_em is None
    assert conversa.situacao == "respondida"
    assert conversa.ultima_autor == "loja"


async def test_ok_grava_id_da_plataforma(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Seu pedido saiu.", user=user)
    assert plataforma.chamadas == ["Seu pedido saiu."]
    assert (m.status, m.externo_id, m.erro) == ("enviada", "plat-1", None)
    assert m.enviada_em is not None
    assert m.payload["envio"] == {"x": 1}

    # O sync trazendo a mesma mensagem de volta NÃO duplica: o id bate.
    _, criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="plat-1",
        autor="loja",
        texto="Seu pedido saiu.",
        enviada_em=datetime.now(UTC),
    )
    assert criada is False
    assert len(await _mensagens_da_loja(db, conversa.id)) == 1


async def test_ambiguo_vira_revisar_e_nunca_retenta(db, make_user, ligado, plataforma):
    plataforma.resultado = ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Olá!", user=user)
    assert len(plataforma.chamadas) == 1
    assert (m.status, m.erro) == ("revisar", "timeout")
    # Pode ter saído: conta como resposta, a conversa sai da fila.
    await db.refresh(conversa)
    assert conversa.aguardando_resposta is False


async def test_adaptador_que_levanta_e_ambiguo(db, make_user, ligado, plataforma):
    plataforma.levanta = RuntimeError("conexão caiu no meio")
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Olá!", user=user)
    assert m.status == "revisar"
    assert m.erro == "erro_inesperado: RuntimeError"
    assert len(plataforma.chamadas) == 1


async def test_falha_deixa_a_conversa_esperando(db, make_user, ligado, plataforma):
    plataforma.resultado = ResultadoEnvio(ok=False, erro="error_param")
    conversa, user = await _cenario(db, make_user)
    prazo = conversa.prazo_resposta_em
    m = await enviar.enviar_resposta(db, conversa, "Olá!", user=user)
    assert (m.status, m.erro) == ("falhou", "error_param")
    await db.refresh(conversa)
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == prazo
    # A falha soltou a trava de "uma em voo": dá para tentar de novo.
    plataforma.resultado = ResultadoEnvio(ok=True, externo_id="plat-2")
    m2 = await enviar.enviar_resposta(db, conversa, "Olá!", user=user)
    assert m2.status == "enviada"


async def test_bloqueio_da_plataforma_bloqueia_a_conversa(db, make_user, ligado, plataforma):
    plataforma.resultado = ResultadoEnvio(ok=False, erro="403", bloqueio="pós-venda bloqueada")
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Olá!", user=user)
    assert m.status == "falhou"
    await db.refresh(conversa)
    assert (conversa.situacao, conversa.bloqueio_motivo) == ("bloqueada", "pós-venda bloqueada")
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "conversa_bloqueada"
    assert len(plataforma.chamadas) == 1


async def test_sync_adota_no_meio_do_envio(db, make_user, ligado, plataforma):
    """A plataforma demorou (timeout), mas o sync já trouxe a resposta de volta:
    a adoção é a prova de que saiu — fica `enviada`, com o id da plataforma."""
    conversa, user = await _cenario(db, make_user)

    async def sync_no_meio():
        async with _db.SessionLocal() as outra:
            c = await outra.get(AtendimentoConversa, conversa.id)
            _, criada = await gravar.gravar_mensagem(
                outra,
                c,
                externo_id="p-99",
                autor="loja",
                texto="Seu pedido saiu.",
                enviada_em=datetime.now(UTC),
            )
            assert criada is False  # adotou a nossa linha `enviando`
            await outra.commit()

    plataforma.antes = sync_no_meio
    plataforma.resultado = ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    m = await enviar.enviar_resposta(db, conversa, "Seu pedido saiu.", user=user)
    assert (m.status, m.externo_id) == ("enviada", "p-99")
    assert len(await _mensagens_da_loja(db, conversa.id)) == 1


async def test_corrida_de_dois_envios_manda_uma_resposta(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    em_voo = asyncio.Event()
    solta = asyncio.Event()

    async def segura():
        em_voo.set()
        await solta.wait()

    plataforma.antes = segura

    async def primeira_aba():
        async with _db.SessionLocal() as s:
            c = await s.get(AtendimentoConversa, conversa.id)
            u = await s.get(User, user.id)
            return await enviar.enviar_resposta(s, c, "Primeira.", user=u)

    tarefa = asyncio.create_task(primeira_aba())
    await asyncio.wait_for(em_voo.wait(), timeout=10)
    try:
        e = await _recusa(enviar.enviar_resposta(db, conversa, "Segunda.", user=user))
        assert e.code == "envio_em_andamento"
    finally:
        solta.set()
    m = await asyncio.wait_for(tarefa, timeout=10)
    assert m.status == "enviada"
    assert plataforma.chamadas == ["Primeira."]
    textos = [x.texto for x in await _mensagens_da_loja(db, conversa.id)]
    assert textos == ["Primeira."]


async def test_envio_preso_de_processo_morto_nao_trava_a_conversa(
    db, make_user, ligado, plataforma
):
    conversa, user = await _cenario(db, make_user)
    preso = AtendimentoMensagem(
        conversa_id=conversa.id, autor="loja", origem="davinci_humano", status="enviando"
    )
    db.add(preso)
    await db.commit()
    await db.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.id == preso.id)
        .values(created_at=datetime.now(UTC) - timedelta(minutes=30))
    )
    await db.commit()

    m = await enviar.enviar_resposta(db, conversa, "Olá!", user=user)
    assert m.status == "enviada"
    await db.refresh(preso)
    assert (preso.status, preso.erro) == ("revisar", "envio_interrompido")


# ─────────────── rascunho e avaliação ───────────────


async def _avaliacoes(db: AsyncSession) -> list[AtendimentoAvaliacao]:
    return list((await db.execute(select(AtendimentoAvaliacao))).scalars().all())


async def test_enviou_a_sugestao_igual(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, "Seu pedido foi enviado hoje, rastreio BR123.")
    m = await enviar.enviar_resposta(
        db, conversa, "Seu pedido foi enviado hoje,  rastreio BR123.", user=user, rascunho_id=r.id
    )
    assert m.rascunho_id == r.id
    await db.refresh(r)
    assert r.status == "enviado"
    (av,) = await _avaliacoes(db)
    assert av.acao == "enviou_igual"
    assert av.similaridade >= 0.97
    assert av.texto_final == "Seu pedido foi enviado hoje, rastreio BR123."
    assert av.user_id == user.id


async def test_editou_a_sugestao(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, "Seu pedido foi enviado hoje.")
    await enviar.enviar_resposta(
        db,
        conversa,
        "Oi! Seu pedido saiu ontem pela transportadora, já está a caminho.",
        user=user,
        rascunho_id=r.id,
    )
    await db.refresh(r)
    assert r.status == "editado"
    (av,) = await _avaliacoes(db)
    assert av.acao == "editou"
    assert av.similaridade < 0.97
    assert av.texto_final.startswith("Oi! Seu pedido saiu ontem")


async def test_escreveu_do_zero_substitui_a_sugestao(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, "Seu pedido foi enviado hoje.")
    await enviar.enviar_resposta(db, conversa, "Vou verificar e já te retorno.", user=user)
    await db.refresh(r)
    assert r.status == "substituido"
    (av,) = await _avaliacoes(db)
    assert (av.acao, av.texto_final) == ("escreveu_do_zero", "Vou verificar e já te retorno.")


async def test_nota_do_modo_observacao_vira_a_acao_de_verdade_no_envio(
    db, make_user, ligado, plataforma
):
    """👍 dado na sugestão pendente (observou) + envio pelo DaVinci → enviou_igual.

    Sem isso, a avaliação ficava `observou` para sempre, a sugestão enviada
    não virava `enviado` e o exemplo para a IA se perdia.
    """
    conversa, user = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, "Seu pedido foi enviado hoje, rastreio BR123.")
    db.add(AtendimentoAvaliacao(rascunho_id=r.id, acao="observou", nota="ok"))
    await db.commit()

    await enviar.enviar_resposta(
        db, conversa, "Seu pedido foi enviado hoje, rastreio BR123.", user=user, rascunho_id=r.id
    )
    await db.refresh(r)
    assert r.status == "enviado"
    (av,) = await _avaliacoes(db)
    await db.refresh(av)
    assert (av.acao, av.nota) == ("enviou_igual", "ok")
    assert av.texto_final == "Seu pedido foi enviado hoje, rastreio BR123."
    assert av.similaridade >= 0.97
    assert av.user_id == user.id


async def test_nota_do_modo_observacao_vira_escreveu_do_zero(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, "Seu pedido foi enviado hoje.")
    db.add(
        AtendimentoAvaliacao(rascunho_id=r.id, acao="observou", nota="erro", correcao="Peça foto.")
    )
    await db.commit()

    await enviar.enviar_resposta(db, conversa, "Vou verificar e já te retorno.", user=user)
    await db.refresh(r)
    assert r.status == "substituido"
    (av,) = await _avaliacoes(db)
    await db.refresh(av)
    assert (av.acao, av.texto_final) == ("escreveu_do_zero", "Vou verificar e já te retorno.")
    # A nota e a correção da observação ficam.
    assert (av.nota, av.correcao) == ("erro", "Peça foto.")


async def test_joinha_na_sugestao_que_a_ia_refez_vale_no_envio(
    db, make_user, ligado, plataforma
):
    """Parte 2 (P3): 👍 na sugestão, a IA refaz (a do 👍 vira `substituido`) e
    a pessoa envia a do 👍 por uma aba aberta antes. Conta o que SAIU (editou),
    a nota e a correção ficam; a sugestão nova sai da caixa sem avaliação —
    ninguém a viu, não é um "não serviu"."""
    conversa, user = await _cenario(db, make_user)
    velha = await _rascunho(db, conversa, "Seu pedido foi enviado hoje.")
    db.add(AtendimentoAvaliacao(rascunho_id=velha.id, acao="observou", nota="ok"))
    velha.status = "substituido"
    await db.commit()
    nova = await _rascunho(db, conversa, "Sugestão refeita pela IA.")

    await enviar.enviar_resposta(
        db, conversa, "Seu pedido foi enviado hoje, obrigado!", user=user, rascunho_id=velha.id
    )

    await db.refresh(velha)
    await db.refresh(nova)
    assert (velha.status, nova.status) == ("editado", "substituido")
    (av,) = await _avaliacoes(db)
    await db.refresh(av)
    assert av.rascunho_id == velha.id
    assert (av.acao, av.nota) == ("editou", "ok")
    assert av.texto_final == "Seu pedido foi enviado hoje, obrigado!"


async def test_avaliacao_de_verdade_nao_e_sobrescrita_pelo_envio(
    db, make_user, ligado, plataforma
):
    """A sugestão já saiu da caixa e foi avaliada de verdade (a loja respondeu
    por fora e a nota promoveu a ação): a aba velha que a envia depois não
    reescreve a comparação IA × equipe."""
    conversa, user = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, "Seu pedido foi enviado hoje.")
    r.status = "substituido"
    db.add(
        AtendimentoAvaliacao(
            rascunho_id=r.id, acao="escreveu_do_zero", nota="ok", texto_final="Já enviamos."
        )
    )
    await db.commit()
    await enviar.enviar_resposta(
        db, conversa, "Seu pedido foi enviado hoje.", user=user, rascunho_id=r.id
    )
    (av,) = await _avaliacoes(db)
    await db.refresh(av)
    assert (av.acao, av.texto_final) == ("escreveu_do_zero", "Já enviamos.")


async def test_falha_nao_gasta_a_sugestao(db, make_user, ligado, plataforma):
    plataforma.resultado = ResultadoEnvio(ok=False, erro="error_param")
    conversa, user = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, "Seu pedido foi enviado hoje.")
    await enviar.enviar_resposta(
        db, conversa, "Seu pedido foi enviado hoje.", user=user, rascunho_id=r.id
    )
    await db.refresh(r)
    assert r.status == "pendente"
    assert await _avaliacoes(db) == []


async def test_rascunho_de_outra_conversa_nao_e_avaliado(db, make_user, ligado, plataforma):
    conversa, user = await _cenario(db, make_user)
    outra, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=None,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="conv-2",
    )
    await db.commit()
    alheio = await _rascunho(db, outra, "Texto de outra conversa.")
    m = await enviar.enviar_resposta(
        db, conversa, "Texto de outra conversa.", user=user, rascunho_id=alheio.id
    )
    assert m.rascunho_id is None
    await db.refresh(alheio)
    assert alheio.status == "pendente"
    assert await _avaliacoes(db) == []


async def test_envio_automatico_nao_vira_exemplo_aprovado(
    db, make_user, ligado, plataforma, monkeypatch
):
    monkeypatch.setattr(get_settings(), "atendimento_auto_ativo", True)
    conversa, _user = await _cenario(db, make_user, modo="auto")
    r = await _rascunho(db, conversa, "Seu pedido foi enviado hoje.")
    m = await enviar.enviar_resposta(
        db, conversa, r.texto, user=None, rascunho_id=r.id, origem="davinci_ia"
    )
    assert (m.status, m.origem, m.autor_user_id) == ("enviada", "davinci_ia", None)
    await db.refresh(r)
    assert r.status == "enviado"
    assert await _avaliacoes(db) == []


def test_similaridade():
    assert enviar.similaridade("a  b\nc", "a b c") == 1.0
    assert enviar.similaridade("", "") == 1.0
    assert enviar.similaridade("bom dia", "boa noite") < 0.97


async def test_texto_do_comprador_nao_vai_para_o_log(db, make_user, ligado, plataforma, capsys):
    conversa, user = await _cenario(db, make_user, texto_cliente="meu cpf é 123.456.789-00")
    await enviar.enviar_resposta(db, conversa, "Resposta secreta 42.", user=user)
    saida = capsys.readouterr()
    assert "123.456.789-00" not in saida.out + saida.err
    assert "Resposta secreta 42" not in saida.out + saida.err
    total = await db.scalar(select(func.count()).select_from(AtendimentoMensagem))
    assert total == 2


# ─────────────── revisão de 25/09: estado de AGORA, repetido, quem mudou ───────────────


async def test_ia_rele_o_canal_do_banco_antes_de_enviar(
    db, make_user, ligado, plataforma, monkeypatch
):
    """A IA roda minutos com a mesma sessão. A loja posta em `observar` (o Duoke
    volta a responder) no meio disso tem que valer já — mesmo com o canal
    velho ainda na memória da sessão."""
    monkeypatch.setattr(get_settings(), "atendimento_auto_ativo", True)
    conversa, _user = await _cenario(db, make_user, modo="auto")
    canal_na_memoria = await db.get(AtendimentoCanal, conversa.canal_id)
    assert canal_na_memoria.modo == "auto"
    async with _db.SessionLocal() as tela:
        await tela.execute(
            update(AtendimentoCanal)
            .where(AtendimentoCanal.id == conversa.canal_id)
            .values(modo="observar")
        )
        await tela.commit()

    e = await _recusa(
        enviar.enviar_resposta(db, conversa, "Olá!", user=None, origem="davinci_ia")
    )
    assert e.code == "canal_em_observacao"
    assert plataforma.chamadas == []


@pytest.mark.parametrize(
    "mudanca",
    [
        {"ia_pausada": True},
        {"situacao": "fechada"},
        {"sem_resposta_necessaria": True},
    ],
)
async def test_ia_nao_envia_se_a_tela_tirou_a_conversa_dela_no_meio(
    db, make_user, ligado, plataforma, monkeypatch, mudanca
):
    """A pessoa pausou a IA / fechou / marcou "não precisa de resposta" enquanto
    o modelo escrevia. O envio relê a conversa sob a trava e recusa."""
    monkeypatch.setattr(get_settings(), "atendimento_auto_ativo", True)
    conversa, _user = await _cenario(db, make_user, modo="auto")
    assert conversa.aguardando_resposta is True  # o objeto em memória é o velho
    async with _db.SessionLocal() as tela:
        c = await tela.get(AtendimentoConversa, conversa.id)
        for campo, valor in mudanca.items():
            setattr(c, campo, valor)
        await gravar.recalcular_conversa(tela, c)
        await tela.commit()

    e = await _recusa(
        enviar.enviar_resposta(db, conversa, "Olá!", user=None, origem="davinci_ia")
    )
    assert e.code == "nao_aguarda"
    assert plataforma.chamadas == []
    assert await _mensagens_da_loja(db, conversa.id) == []


@pytest.mark.parametrize("mudanca", ["reclamacao", "amazon"])
async def test_ia_nao_envia_com_reclamacao_aberta_nem_na_amazon(
    db, make_user, ligado, plataforma, monkeypatch, mudanca
):
    """Cinto do envio (origem IA): reclamação/mediação aberta no ML e a Amazon
    (resposta do Seller Central invisível) nunca recebem resposta automática.
    A pessoa continua podendo responder."""
    monkeypatch.setattr(get_settings(), "atendimento_auto_ativo", True)
    conversa, user = await _cenario(db, make_user, modo="auto")
    if mudanca == "reclamacao":
        conversa.dados = {"claim_ids": ["5123456789"]}
    else:
        conversa.plataforma = "amazon"
    await db.commit()

    e = await _recusa(
        enviar.enviar_resposta(db, conversa, "Olá!", user=None, origem="davinci_ia")
    )
    assert e.code == "nao_aguarda"
    assert plataforma.chamadas == []
    if mudanca == "reclamacao":
        m = await enviar.enviar_resposta(db, conversa, "Olá, vou verificar.", user=user)
        assert m.status == "enviada"


async def test_ia_nao_responde_de_novo_a_pergunta_ja_respondida(
    db, make_user, ligado, plataforma, monkeypatch
):
    """Duas rodadas da IA geraram para a MESMA pergunta. A primeira saiu; a
    segunda chega com a conversa velha na memória e a sugestão dela — o envio
    encontra a resposta sob a trava e recusa (o comprador não recebe duas)."""
    monkeypatch.setattr(get_settings(), "atendimento_auto_ativo", True)
    conversa, _user = await _cenario(db, make_user, modo="auto")
    gatilho = await db.scalar(
        select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa.id)
    )
    r1 = AtendimentoRascunho(
        conversa_id=conversa.id, mensagem_gatilho_id=gatilho.id, texto="Já saiu!",
        status="pendente",
    )
    db.add(r1)
    await db.commit()
    async with _db.SessionLocal() as rodada_a:
        c = await rodada_a.get(AtendimentoConversa, conversa.id)
        await enviar.enviar_resposta(
            rodada_a, c, "Já saiu!", user=None, rascunho_id=r1.id, origem="davinci_ia"
        )
    # A rodada B salvou a SUA sugestão para o mesmo gatilho (a de A já não
    # estava pendente) e tenta enviar com o objeto velho.
    r2 = AtendimentoRascunho(
        conversa_id=conversa.id, mensagem_gatilho_id=gatilho.id, texto="Seu pedido saiu hoje.",
        status="pendente",
    )
    db.add(r2)
    await db.commit()
    e = await _recusa(
        enviar.enviar_resposta(
            db, conversa, r2.texto, user=None, rascunho_id=r2.id, origem="davinci_ia"
        )
    )
    assert e.code == "nao_aguarda"
    assert plataforma.chamadas == ["Já saiu!"]


async def test_mesma_resposta_em_menos_de_2_minutos_e_envio_repetido(
    db, make_user, ligado, plataforma
):
    """Clique duplo / aba duplicada: o índice de "uma em voo" só barra o que é
    simultâneo; depois que a primeira saiu, a segunda passaria."""
    conversa, user = await _cenario(db, make_user)
    await enviar.enviar_resposta(db, conversa, "Seu pedido saiu hoje!", user=user)
    e = await _recusa(enviar.enviar_resposta(db, conversa, "seu pedido  saiu hoje", user=user))
    assert e.code == "envio_repetido"
    assert plataforma.chamadas == ["Seu pedido saiu hoje!"]
    # Outro texto passa; e o mesmo texto depois da janela também.
    await enviar.enviar_resposta(db, conversa, "Qualquer dúvida, estamos aqui.", user=user)
    await db.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id == conversa.id)
        .values(created_at=datetime.now(UTC) - timedelta(minutes=3))
    )
    await db.commit()
    m = await enviar.enviar_resposta(db, conversa, "Seu pedido saiu hoje!", user=user)
    assert m.status == "enviada"


async def test_resposta_falhada_nao_conta_como_repetida(db, make_user, ligado, plataforma):
    plataforma.resultado = ResultadoEnvio(ok=False, erro="error_param")
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Seu pedido saiu hoje!", user=user)
    assert m.status == "falhou"
    plataforma.resultado = ResultadoEnvio(ok=True, externo_id="plat-2")
    m = await enviar.enviar_resposta(db, conversa, "Seu pedido saiu hoje!", user=user)
    assert m.status == "enviada"


async def test_outra_pessoa_respondeu_enquanto_eu_escrevia(db, make_user, ligado, plataforma):
    """Duas pessoas na mesma conversa: a que envia depois recebe `conversa_mudou`
    (com quem e quando), e só envia confirmando."""
    conversa, user = await _cenario(db, make_user)
    vista = await db.scalar(
        select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa.id)
    )
    outra_pessoa = await make_user()
    outra_pessoa.name = "Paula Atendente"
    await db.commit()
    async with _db.SessionLocal() as aba_a:
        c = await aba_a.get(AtendimentoConversa, conversa.id)
        u = await aba_a.get(User, outra_pessoa.id)
        await enviar.enviar_resposta(
            aba_a, c, "Já saiu, segue o rastreio.", user=u, ultima_vista_id=vista.id
        )

    e = await _recusa(
        enviar.enviar_resposta(
            db, conversa, "Vou verificar.", user=user, ultima_vista_id=vista.id
        )
    )
    assert e.code == "conversa_mudou"
    assert "Paula Atendente respondeu às" in e.detail
    assert plataforma.chamadas == ["Já saiu, segue o rastreio."]

    m = await enviar.enviar_resposta(
        db, conversa, "Vou verificar.", user=user, ultima_vista_id=vista.id, confirmar=True
    )
    assert m.status == "enviada"
    # Quem já viu a resposta da outra pessoa não é barrado.
    ultima = (await _mensagens_da_loja(db, conversa.id))[-1]
    m = await enviar.enviar_resposta(
        db, conversa, "Mais alguma dúvida?", user=user, ultima_vista_id=ultima.id
    )
    assert m.status == "enviada"


async def test_conversa_travada_pelo_sync_devolve_ocupada_sem_pendurar(
    db, make_user, ligado, plataforma, monkeypatch
):
    """A rodada do sync segura a conversa enquanto fala com a API da loja. O
    "Enviar" não pode pendurar até ela acabar: espera pouco e diz "tente de
    novo" — sem gravar nada e sem deixar a sessão de quem chamou quebrada."""
    monkeypatch.setattr(gravar, "ESPERA_TRAVA_TELA", "300ms")
    conversa, user = await _cenario(db, make_user)
    async with _db.SessionLocal() as sync_s:
        c = await sync_s.get(AtendimentoConversa, conversa.id)
        c.nao_lidas = 7
        await sync_s.flush()  # UPDATE: a linha fica travada até o commit
        e = await _recusa(
            asyncio.wait_for(enviar.enviar_resposta(db, conversa, "Olá!", user=user), 5)
        )
        assert e.code == "conversa_ocupada"
        await sync_s.rollback()
    assert plataforma.chamadas == []
    assert str(conversa.id)  # o objeto de quem chamou continua legível
    m = await enviar.enviar_resposta(db, conversa, "Olá!", user=user)
    assert m.status == "enviada"


async def test_recusa_solta_a_trava_da_conversa(db, make_user, ligado, plataforma):
    """Recusado, o envio não deixa a conversa travada na transação de quem chamou
    (a IA segue a rodada na mesma sessão; o sync esperaria por ela)."""
    conversa, user = await _cenario(db, make_user)
    e = await _recusa(enviar.enviar_resposta(db, conversa, "me chama no WhatsApp", user=user))
    assert e.code == "texto_invalido"
    async with _db.SessionLocal() as outra:
        await outra.execute(text("SET LOCAL lock_timeout = '500ms'"))
        c = await outra.get(AtendimentoConversa, conversa.id, with_for_update=True)
        assert c is not None
        await outra.rollback()


async def test_sync_adotando_enquanto_o_envio_grava_o_resultado_nao_trava(
    db, make_user, ligado, monkeypatch
):
    """O sync trava a conversa (contador de não lidas) e adota a nossa resposta;
    o envio grava o resultado. A ordem das travas é a mesma (conversa antes
    da mensagem): nada de deadlock, nem 500 para um envio que saiu."""
    conversa, _user = await _cenario(db, make_user)
    plataforma_respondeu = asyncio.Event()
    sync_travou = asyncio.Event()

    async def plataforma_falsa(session, conversa_, integration, texto):
        plataforma_respondeu.set()
        await sync_travou.wait()
        return ResultadoEnvio(ok=True, externo_id="shopee-777")

    monkeypatch.setattr(enviar, "_chamar_plataforma", plataforma_falsa)

    async def lado_envio():
        async with _db.SessionLocal() as tela:
            c = await tela.get(AtendimentoConversa, conversa.id)
            return await enviar.enviar_resposta(tela, c, "Olá! Seu pedido já saiu.", user=None)

    async def lado_sync():
        await plataforma_respondeu.wait()
        async with _db.SessionLocal() as sync_s:
            c = await sync_s.get(AtendimentoConversa, conversa.id)
            c.nao_lidas = 5
            await sync_s.flush()  # UPDATE: trava a conversa
            sync_travou.set()
            await asyncio.sleep(0.3)  # o envio tenta gravar o resultado agora
            await gravar.gravar_mensagem(
                sync_s, c, externo_id="shopee-777", autor="loja",
                texto="Olá! Seu pedido já saiu.", enviada_em=datetime.now(UTC),
            )
            await sync_s.commit()

    m, _ = await asyncio.wait_for(asyncio.gather(lado_envio(), lado_sync()), 20)
    assert (m.status, m.externo_id) == ("enviada", "shopee-777")
    assert len(await _mensagens_da_loja(db, conversa.id)) == 1


async def test_envio_preso_velho_nao_trava_a_tela(db, make_user, ligado):
    """Linha `enviando` de um processo que morreu (deploy) com a leitura
    desligada: ninguém a aposentaria, e a tela travaria a caixa para sempre."""
    conversa, _user = await _cenario(db, make_user)
    preso = AtendimentoMensagem(
        conversa_id=conversa.id, autor="loja", origem="davinci_humano", status="enviando"
    )
    db.add(preso)
    await db.commit()
    await db.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.id == preso.id)
        .values(created_at=datetime.now(UTC) - timedelta(days=2))
    )
    await db.commit()
    assert await enviar.motivo_para_nao_enviar(db, conversa) is None


async def test_simulador_ligado_em_producao_recusa(db, make_user, ligado, plataforma, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_simulador", True)
    monkeypatch.setattr(s, "env", "production")
    conversa, user = await _cenario(db, make_user)
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "simulador_em_producao"
    assert plataforma.chamadas == []
    assert await _mensagens_da_loja(db, conversa.id) == []
    # O cinto do passo 3 também não finge sucesso.
    r = await enviar._chamar_plataforma(db, conversa, None, "Olá!")
    assert (r.ok, r.erro) == (False, "simulador_em_producao")


# ─────────────── exceção do simulador (só local) ───────────────
# `atendimento_simulador_exceto` tira plataformas do simulador: o teste local
# de responder pela tela a um e-mail REAL da Amazon com o resto fingindo.

RELAY = "a1b2c3d4e5f6+caso@marketplace.amazon.com.br"


class _Amazon:
    """Adaptador falso da Amazon (o SMTP de verdade nunca é chamado aqui)."""

    def __init__(self, monkeypatch) -> None:
        from app.services.atendimento import amazon_email

        self.chamadas: list[tuple[str, str]] = []
        self.levanta: Exception | None = None

        async def enviar_texto(session, conversa, integration, cliente, texto):
            if self.levanta is not None:
                raise self.levanta
            self.chamadas.append((conversa.comprador_id, texto))
            return ResultadoEnvio(
                ok=True, externo_id="<resposta-1@gmail.com>", payload={"smtp": None}
            )

        monkeypatch.setattr(amazon_email, "enviar_texto", enviar_texto)


@pytest.fixture
def amazon(monkeypatch) -> _Amazon:
    return _Amazon(monkeypatch)


async def _cenario_amazon(db: AsyncSession, make_user) -> tuple[AtendimentoConversa, User]:
    """Conversa de e-mail da Amazon KFA, loja em `humano`, com o endereço de
    retransmissão do comprador (é para ele que a resposta sai)."""
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.AMAZON,
        name="kfa",
        credentials=encrypt_json({"refresh_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="amazon", canal="email", modo="humano", status="ok"
    )
    db.add(canal)
    await db.flush()
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="amazon",
        canal_nome="email",
        externo_id=f"{RELAY}|-",
        comprador_id=RELAY,
        comprador_nome="Comprador Teste",
        dados={"assunto": "Pergunta do cliente"},
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="<pergunta-1@amazon.com.br>",
        autor="cliente",
        texto="Esse produto tem garantia?",
        enviada_em=T_CLIENTE,
    )
    await db.commit()
    return conversa, user


async def test_simulador_com_excecao_amazon_envia_so_a_amazon_de_verdade(
    db, make_user, ligado, plataforma, amazon, monkeypatch
):
    """O `api --envio-real-amazon` do ambiente local: simulador LIGADO com a
    Amazon na exceção. A Amazon chama o adaptador (o SMTP, aqui falso); a
    Shopee continua no simulador sem chamar a plataforma."""
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_simulador", True)
    # Espaço e maiúscula no .env não podem fazer a exceção sumir sem aviso.
    monkeypatch.setattr(s, "atendimento_simulador_exceto", " Amazon ")
    plataforma.levanta = AssertionError("a Shopee tem de continuar no simulador")

    assert enviar.vai_para_o_simulador("amazon") is False
    for outra in ("shopee", "ml", "tiktok"):
        assert enviar.vai_para_o_simulador(outra) is True

    conversa_amz, user = await _cenario_amazon(db, make_user)
    m = await enviar.enviar_resposta(db, conversa_amz, "  Tem sim, 12 meses. ", user=user)
    assert amazon.chamadas == [(RELAY, "Tem sim, 12 meses.")]
    assert m.status == "enviada"
    assert m.externo_id == "<resposta-1@gmail.com>"
    assert "simulador" not in m.payload["envio"]

    conversa_sh, user_sh = await _cenario(db, make_user)
    m_sh = await enviar.enviar_resposta(db, conversa_sh, "Seu pedido saiu hoje.", user=user_sh)
    assert plataforma.chamadas == []
    assert m_sh.status == "enviada"
    assert m_sh.externo_id.startswith("sim:")
    assert m_sh.payload["envio"] == {"simulador": True}
    assert len(amazon.chamadas) == 1  # a Shopee não passou pela Amazon


async def test_sem_excecao_a_amazon_tambem_fica_no_simulador(
    db, make_user, ligado, amazon, monkeypatch
):
    """Sem a opção (exceção vazia, o padrão), nada muda: tudo no simulador."""
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_simulador", True)
    assert s.atendimento_simulador_exceto == ""
    amazon.levanta = AssertionError("sem a exceção a Amazon não pode sair de verdade")

    assert enviar.vai_para_o_simulador("amazon") is True
    conversa, user = await _cenario_amazon(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Tem sim, 12 meses.", user=user)
    assert amazon.chamadas == []
    assert m.status == "enviada"
    assert m.externo_id.startswith("sim:")


async def test_excecao_sem_simulador_nao_muda_nada(db, make_user, ligado, plataforma, monkeypatch):
    """Sem o simulador a exceção não tem efeito: tudo já sai de verdade."""
    monkeypatch.setattr(get_settings(), "atendimento_simulador_exceto", "amazon")
    for p in ("amazon", "shopee", "ml", "tiktok"):
        assert enviar.vai_para_o_simulador(p) is False
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Seu pedido saiu.", user=user)
    assert plataforma.chamadas == ["Seu pedido saiu."]
    assert m.externo_id == "plat-1"


async def test_excecao_com_simulador_em_producao_recusa_a_amazon_tambem(
    db, make_user, ligado, amazon, monkeypatch
):
    """Simulador ligado em produção recusa TUDO — a exceção não abre porta:
    quem ligou o simulador achava que nada sairia."""
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_simulador", True)
    monkeypatch.setattr(s, "atendimento_simulador_exceto", "amazon")
    monkeypatch.setattr(s, "env", "production")
    conversa, user = await _cenario_amazon(db, make_user)
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
    assert e.code == "simulador_em_producao"
    assert amazon.chamadas == []
    r = await enviar._chamar_plataforma(db, conversa, None, "Olá!")
    assert (r.ok, r.erro) == (False, "simulador_em_producao")
    assert amazon.chamadas == []


async def test_sugestao_velha_na_aba_nao_deixa_a_nova_pendente(db, make_user, ligado, plataforma):
    """A caixa tinha R1; a IA refez (R2) antes do poll; a pessoa envia com R1.
    R1 é o texto que ela usou (vira `enviado` com avaliação); R2 sai da caixa
    SEM avaliação — senão ela voltaria pré-preenchida numa conversa respondida."""
    conversa, user = await _cenario(db, make_user)
    r1 = await _rascunho(db, conversa, "Seu pedido foi enviado hoje.")
    r1.status = "substituido"
    await db.commit()
    r2 = await _rascunho(db, conversa, "Olá! Seu pedido já está a caminho.")

    await enviar.enviar_resposta(
        db, conversa, "Seu pedido foi enviado hoje.", user=user, rascunho_id=r1.id
    )
    await db.refresh(r1)
    await db.refresh(r2)
    assert r1.status == "enviado"
    assert r2.status == "substituido"
    (av,) = await _avaliacoes(db)
    assert (av.rascunho_id, av.acao) == (r1.id, "enviou_igual")


async def test_historico_nao_guarda_texto_do_comprador(
    db, make_user, ligado, plataforma, monkeypatch
):
    """O gatilho do Histórico copiava, a cada resposta pela tela, a última
    mensagem do comprador (`ultima_mensagem_resumo`) e a resposta inteira para
    `historico_alteracao`, por 365 dias. As tabelas com texto de comprador
    ficam fora do gatilho."""
    from app.historico import contexto as hctx

    conversa, user = await _cenario(
        db, make_user, texto_cliente="Meu endereço é Rua Secreta 999, apto 12"
    )
    ator = hctx.Ator(
        metodo="POST",
        caminho="/api/atendimento/conversas/x/responder",
        grava=True,
        escrita=True,
        user_id=user.id,
        nome="Pessoa",
    )
    tok = hctx.abrir(ator)
    try:
        await db.commit()  # a próxima transação nasce marcada
        await enviar.enviar_resposta(db, conversa, "Seu pedido saiu hoje.", user=user)
    finally:
        hctx.fechar(tok)
    linhas = (
        await db.execute(
            text(
                "select tabela, coalesce(antes::text, '') || coalesce(depois::text, '')"
                " from historico_alteracao"
            )
        )
    ).all()
    assert not [t for t, corpo in linhas if "Rua Secreta" in corpo or "saiu hoje" in corpo]
    assert not [
        t
        for t, _ in linhas
        if t in ("atendimento_conversas", "atendimento_mensagens", "atendimento_rascunhos")
    ]
    # E o CORPO do pedido (a nossa resposta, a correção, o motivo do descarte)
    # nunca entra no evento, mesmo que outra tabela com gatilho mude junto.
    from app.historico import nomes as hnomes

    for caminho in (
        "/api/atendimento/conversas/abc/responder",
        "/api/atendimento/conversas/abc",
        "/api/atendimento/rascunhos/abc/descartar",
        "/api/atendimento/rascunhos/abc/avaliacao",
        "/api/atendimento/mensagens/abc/conferir",
    ):
        assert hnomes.SEM_CORPO.search(caminho), caminho
    # O que muda o comportamento da loja (manual, respostas prontas, modo)
    # continua com o corpo no Histórico.
    for caminho in (
        "/api/atendimento/regras",
        "/api/atendimento/modelos",
        "/api/atendimento/canais/x",
    ):
        assert not hnomes.SEM_CORPO.search(caminho), caminho


# ─────────────── e-mail do Tuta e Zap: sem envio no DaVinci (05/10/2026) ───────────────
# O envio deles ainda não existe (o outro dev, RF5/RF10). Gravados com a
# plataforma da venda e o canal da loja, cairiam no adaptador do marketplace —
# que mandaria o texto pelo chat da loja para o `comprador_id` (o e-mail ou o
# telefone do contato). A trava vem antes de tudo e diz qual dos dois é.


async def _contato(
    db: AsyncSession,
    da_loja: AtendimentoConversa,
    *,
    canal_nome: str,
    dados: dict | None = None,
    contato: str = "comprador@exemplo.com",
) -> AtendimentoConversa:
    """Conversa de CONTATO gravada como o outro dev vai gravar: a plataforma,
    a loja e o CANAL da venda (aqui, o chat da Shopee)."""
    canal = await db.get(AtendimentoCanal, da_loja.canal_id)
    integ = await db.get(Integration, da_loja.integration_id)
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=da_loja.plataforma,
        canal_nome=canal_nome,
        externo_id=f"{canal_nome}:{contato}",
        comprador_id=contato,
        pedido_marketplace=da_loja.pedido_marketplace,
        dados=dados,
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{canal_nome}-c-1",
        autor="cliente",
        texto="cadê meu pedido?",
        enviada_em=T_CLIENTE,
    )
    await db.commit()
    return conversa


def test_regua_do_canal_sem_envio():
    from app.services.atendimento import abas

    casos = [
        ("zap", "shopee", {}, MOTIVO_ZAP_SEM_ENVIO),
        ("zap", "amazon", None, MOTIVO_ZAP_SEM_ENVIO),
        ("email", "amazon", {"fonte": "tuta"}, MOTIVO_TUTA_SEM_ENVIO),
        ("email", "amazon", {"fonte": " tuta "}, MOTIVO_TUTA_SEM_ENVIO),
        # Só o Tuta grava e-mail fora da Amazon (mesmo sem a marca).
        ("email", "shopee", {}, MOTIVO_TUTA_SEM_ENVIO),
        ("email", "ml", None, MOTIVO_TUTA_SEM_ENVIO),
        # O e-mail da Amazon (o canal da plataforma) continua saindo.
        ("email", "amazon", {}, None),
        ("email", "amazon", None, None),
        ("email", "amazon", {"fonte": True}, None),
        ("email", "amazon", {"fonte": "amazon"}, None),
        ("chat", "shopee", {"fonte": "tuta"}, None),
        ("pos_venda", "ml", {}, None),
    ]
    for canal, plat, dados, esperado in casos:
        assert motivo_canal_sem_envio(canal, plat, dados) == esperado, (canal, plat, dados)
        # A MESMA régua da aba E-mail/Zap (`abas.e_contato`): se uma mudar
        # sozinha, a conversa que a aba diz ser do Tuta sairia pela Amazon.
        conv = AtendimentoConversa(canal=canal, plataforma=plat, dados=dados or {})
        assert abas.e_contato(conv) is (esperado is not None), (canal, plat, dados)


async def test_email_do_tuta_e_zap_nunca_caem_no_adaptador_do_marketplace(
    db, make_user, ligado, plataforma, monkeypatch
):
    da_loja, user = await _cenario(db, make_user)
    tuta = await _contato(db, da_loja, canal_nome="email", dados={"fonte": "tuta"})
    sem_marca = await _contato(db, da_loja, canal_nome="email", contato="outro@exemplo.com")
    zap = await _contato(db, da_loja, canal_nome="zap", contato="5511999990000")
    # O automático ligado nos dois lugares: a IA também não passa.
    monkeypatch.setattr(get_settings(), "atendimento_auto_ativo", True)
    await db.execute(update(AtendimentoCanal).values(modo="auto"))
    await db.commit()

    for conversa, frase in (
        (tuta, MOTIVO_TUTA_SEM_ENVIO),
        (sem_marca, MOTIVO_TUTA_SEM_ENVIO),
        (zap, MOTIVO_ZAP_SEM_ENVIO),
    ):
        recusa = await enviar.motivo_para_nao_enviar(db, conversa)
        assert recusa is not None
        assert (recusa.code, recusa.detail) == ("canal_sem_envio", frase)
        e = await _recusa(enviar.enviar_resposta(db, conversa, "Seu pedido saiu.", user=user))
        assert (e.code, e.detail) == ("canal_sem_envio", frase)
        e = await _recusa(
            enviar.enviar_resposta(db, conversa, "Seu pedido saiu.", user=None, origem="davinci_ia")
        )
        assert e.code == "canal_sem_envio"
        # A foto também: a trava vem antes de olhar o arquivo.
        e = await _recusa(enviar.enviar_foto(db, conversa, object(), user=user))
        assert e.code == "canal_sem_envio"
        assert await _mensagens_da_loja(db, conversa.id) == []
    assert plataforma.chamadas == []

    # O chat da MESMA loja continua saindo (a trava é do canal, não da loja).
    m = await enviar.enviar_resposta(db, da_loja, "Seu pedido saiu.", user=user)
    assert m.status == "enviada"
    assert plataforma.chamadas == ["Seu pedido saiu."]


async def test_trava_do_tuta_e_zap_vem_antes_do_envio_desligado(db, make_user, plataforma):
    """Com o envio desligado (produção hoje), a tela já diz o porquê de verdade:
    ligar o ATENDIMENTO_ENVIO_ATIVO não faria o Tuta nem o Zap sair."""
    da_loja, user = await _cenario(db, make_user)
    tuta = await _contato(db, da_loja, canal_nome="email", dados={"fonte": "tuta"})
    zap = await _contato(db, da_loja, canal_nome="zap", contato="5511999990000")
    for conversa in (tuta, zap):
        e = await _recusa(enviar.enviar_resposta(db, conversa, "Olá!", user=user))
        assert e.code == "canal_sem_envio"
    # A conversa da loja continua com a recusa de sempre.
    e = await _recusa(enviar.enviar_resposta(db, da_loja, "Olá!", user=user))
    assert e.code == "envio_desligado"
    assert plataforma.chamadas == []


async def test_email_da_amazon_continua_saindo_e_o_do_tuta_na_amazon_nao(
    db, make_user, ligado, amazon
):
    conversa, user = await _cenario_amazon(db, make_user)
    m = await enviar.enviar_resposta(db, conversa, "Tem sim, 12 meses.", user=user)
    assert m.status == "enviada"
    assert amazon.chamadas == [(RELAY, "Tem sim, 12 meses.")]

    # O mesmo canal `email` + `amazon`, com a marca do Tuta: é o e-mail do
    # Tuta de uma venda Amazon — nunca sai pelo SMTP da Amazon.
    conversa.dados = {**(conversa.dados or {}), "fonte": "tuta"}
    await db.commit()
    e = await _recusa(enviar.enviar_resposta(db, conversa, "Outra resposta.", user=user))
    assert (e.code, e.detail) == ("canal_sem_envio", MOTIVO_TUTA_SEM_ENVIO)
    assert len(amazon.chamadas) == 1
