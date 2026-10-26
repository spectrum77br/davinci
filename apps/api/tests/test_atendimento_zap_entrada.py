# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""O processador do Zap (WhatsApp Cloud API, RF10, Onda 1) — docs/zap/desenho.md §5.2–§5.7.

Os eventos entram pela MESMA porta do webhook (`zap_entrada.gravar_itens`,
com os corpos das fixtures no formato real da Meta) e o processador roda numa
sessão própria, como o worker. Nada fala com a Meta.

O que se garante:
- texto vira canal `zap:<pnid>` em `observar` + conversa `whatsapp/zap` sem
  integração + mensagem; o evento novo (`proxima_em` NULL) é processado; o
  canal fica `ok` (a barra não diz "Ainda não foi lida"); PRÉ-VENDA; a janela
  de 24 h pelo relógio da Meta (segundos);
- UM commit por evento (a conversa já gravada não fica travada pelo resto da
  rodada); `IntegrityError` vai para o backoff, nunca para `repetido`;
- idempotência, fora de ordem, duas mensagens ao mesmo tempo de cliente novo;
- o contato: só BSUID, telefone que ganha BSUID, NÚMERO RECICLADO, 9º dígito
  só em celular, `user_changed_number`;
- opt-out: `user_preferences`, "PARAR" e a frase parecida (só nota);
- reação (não entra na fila), não suportada, botão/lista, CTWA, localização,
  contato, mídia registrada `pendente`;
- `value.errors` e eventos de conta na saúde; número/WABA desconhecidos,
  número de outra WABA; chave desligada deixa pendente;
- status antes da mensagem fica pendente e é aplicado depois; ordem que não
  volta; `failed` 131047 fecha a janela; linha `enviando` intocada;
- o job e o cron no worker, saindo cedo com as chaves desligadas.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

import app.db as _db
from app import worker
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoZapApp,
    AtendimentoZapContato,
    AtendimentoZapEvento,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
)
from app.services.atendimento import gravar, zap_contas, zap_entrada
from tests.zap_apoio import (  # noqa: F401
    APP_ID,
    DISPLAY,
    NOME,
    PNID,
    T0,
    WA_ID,
    WABA,
    cadastrar,
    corpo,
    eventos,
    gravar_corpo,
    processar,
    zap,
)


def _com_bsuid(c: dict, bsuid: str) -> dict:
    valor = c["entry"][0]["changes"][0]["value"]
    valor["messages"][0]["from_user_id"] = bsuid
    for contato in valor.get("contacts") or []:
        contato["user_id"] = bsuid
    return c


async def _conversas(db) -> list[AtendimentoConversa]:
    return list(
        (
            await db.execute(
                select(AtendimentoConversa)
                .order_by(AtendimentoConversa.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _mensagens(db, conversa_id) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _contatos(db) -> list[AtendimentoZapContato]:
    return list(
        (
            await db.execute(
                select(AtendimentoZapContato)
                .order_by(AtendimentoZapContato.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _receber(app, tipo: str, **kw) -> dict:
    """Grava o evento (como o webhook) e processa (como o worker)."""
    await gravar_corpo(corpo(tipo, **kw), app=app)
    return await processar()


# ─────────────── texto: canal, conversa, mensagem ───────────────


async def test_texto_vira_canal_conversa_e_mensagem_pre_venda(db, zap):
    app, _, numero = await cadastrar(db, com_canal=False)
    await gravar_corpo(corpo("texto", wamid="wamid.OI", texto="oi, tem a mala?"), app=app)
    (ev,) = await eventos(db)
    assert ev.proxima_em is None and ev.processado_em is None  # o evento novo
    resumo = await processar()
    assert resumo["processados"] == 1 and resumo["resultados"] == {"gravado": 1}

    (ev,) = await eventos(db)
    assert ev.resultado == "gravado" and ev.processado_em is not None
    canal = (
        await db.execute(
            select(AtendimentoCanal).where(AtendimentoCanal.externo_ref == f"zap:{PNID}")
        )
    ).scalar_one()
    assert (canal.plataforma, canal.canal, canal.modo) == ("whatsapp", "zap", "observar")
    assert canal.integration_id is None
    assert canal.status == "ok" and canal.ultimo_ok_em is not None
    assert canal.cursor["externo"]["nome"] == "Charlots · Zap"
    await db.refresh(numero)
    assert numero.canal_id == canal.id and numero.ultimo_recebido_em == T0

    (conversa,) = await _conversas(db)
    (contato,) = await _contatos(db)
    assert (conversa.plataforma, conversa.canal, conversa.canal_id) == ("whatsapp", "zap", canal.id)
    assert conversa.integration_id is None
    assert conversa.externo_id == str(contato.id)  # P3: o id do contato, não o telefone
    assert (conversa.comprador_id, conversa.comprador_nome) == (WA_ID, NOME)
    assert conversa.conta == DISPLAY
    assert conversa.pode_enviar_ate == T0 + timedelta(hours=24)  # ts em SEGUNDOS
    assert conversa.etiqueta == "pre_venda"
    assert conversa.aguardando_resposta is True
    assert conversa.dados["zap"] == {"numero_id": str(numero.id), "contato_id": str(contato.id)}
    assert contato.conversa_id == conversa.id
    assert (contato.wa_id, contato.wa_id_norm, contato.nome_perfil) == (WA_ID, WA_ID, NOME)
    assert contato.ultima_recebida_em == T0
    (m,) = await _mensagens(db, conversa.id)
    assert (m.autor, m.origem, m.tipo, m.texto) == (
        "cliente",
        "cliente",
        "texto",
        "oi, tem a mala?",
    )
    assert m.externo_id == "wamid.OI" and m.enviada_em == T0
    assert m.payload["zap"]["tipo_meta"] == "text"


async def test_um_commit_por_evento_a_conversa_gravada_nao_fica_travada(db, zap, monkeypatch):
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto", wamid="wamid.A", wa_id="5511911110001"), app=app)
    await gravar_corpo(corpo("texto", wamid="wamid.B", wa_id="5511911110002"), app=app)
    parou, solta = asyncio.Event(), asyncio.Event()
    original = zap_entrada._processar_um
    chamadas: list[int] = []

    async def _lento(session, ev, agora):
        chamadas.append(ev.id)
        if len(chamadas) == 2:
            parou.set()
            await solta.wait()
        return await original(session, ev, agora)

    monkeypatch.setattr(zap_entrada, "_processar_um", _lento)
    tarefa = asyncio.create_task(processar())
    await asyncio.wait_for(parou.wait(), 20)
    try:
        async with _db.SessionLocal() as outra:
            conversa_a = (
                await outra.execute(
                    select(AtendimentoConversa).where(
                        AtendimentoConversa.comprador_id == "5511911110001"
                    )
                )
            ).scalar_one()
            inicio = time.monotonic()
            assert await gravar.travar_linha(outra, conversa_a, espera="5s")
            assert time.monotonic() - inicio < 5
            await outra.rollback()
    finally:
        solta.set()
    resumo = await asyncio.wait_for(tarefa, 30)
    assert resumo["processados"] == 2
    assert len(await _conversas(db)) == 2


async def test_integrity_error_vai_para_o_backoff_nunca_para_repetido(db, zap, monkeypatch):
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto", wamid="wamid.QUEBRA"), app=app)

    async def _quebra(*a, **k):
        raise IntegrityError("INSERT", {}, Exception("uq_qualquer"))

    monkeypatch.setattr(zap_entrada, "_mensagem", _quebra)
    resumo = await processar()
    assert resumo["erros"] == 1 and resumo["processados"] == 0
    (ev,) = await eventos(db)
    assert ev.tentativas == 1 and ev.erro == "IntegrityError"
    assert ev.processado_em is None and ev.resultado is None
    assert ev.proxima_em is not None and ev.proxima_em > datetime.now(UTC)
    # Em backoff: a rodada seguinte não o pega antes da hora.
    assert (await processar())["processados"] == 0
    assert await _conversas(db) == []
    # Na hora certa, com o defeito corrigido, ele entra.
    monkeypatch.undo()
    monkeypatch.setattr(zap.settings, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(zap.settings, "atendimento_zap_leitura_ativa", True)
    await db.execute(update(AtendimentoZapEvento).values(proxima_em=func.now()))
    await db.commit()
    assert (await processar())["resultados"] == {"gravado": 1}


async def test_desiste_depois_de_20_tentativas_com_resultado_erro(db, zap, monkeypatch):
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto"), app=app)
    await db.execute(update(AtendimentoZapEvento).values(tentativas=19))
    await db.commit()

    async def _quebra(*a, **k):
        raise RuntimeError("defeito")

    monkeypatch.setattr(zap_entrada, "_mensagem", _quebra)
    await processar()
    (ev,) = await eventos(db)
    assert (ev.tentativas, ev.resultado, ev.erro) == (20, "erro", "RuntimeError")
    assert ev.processado_em is not None


async def test_reentrega_e_idempotente(db, zap):
    app, _, _ = await cadastrar(db)
    c = corpo("texto", wamid="wamid.DUAS")
    assert await gravar_corpo(c, app=app) == 1
    assert await gravar_corpo(c, app=app) == 0  # o UNIQUE da chave
    await processar()
    # A mesma mensagem por outro caminho (outra chave): `repetido`, sem duplicar.
    item = zap_entrada.itens_do_corpo(c)[0]
    item.chave = "m:wamid.DUAS:copia"
    async with _db.SessionLocal() as s:
        await zap_entrada.gravar_itens(s, [item], app_ref=app.id)
        await s.commit()
    assert (await processar())["resultados"] == {"repetido": 1}
    (conversa,) = await _conversas(db)
    assert len(await _mensagens(db, conversa.id)) == 1


async def test_fora_de_ordem_a_janela_usa_o_maior_ts_e_o_atraso_nao_reabre(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.DEPOIS", ts=T0 + timedelta(minutes=10))
    await _receber(app, "texto", wamid="wamid.ANTES", ts=T0)
    (conversa,) = await _conversas(db)
    (contato,) = await _contatos(db)
    assert contato.ultima_recebida_em == T0 + timedelta(minutes=10)
    assert conversa.pode_enviar_ate == T0 + timedelta(minutes=10, hours=24)
    assert conversa.ultima_mensagem_em == T0 + timedelta(minutes=10)
    # A loja respondeu (por fora) às 12:20; uma mensagem das 12:15 chega atrasada.
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="wamid.DA_LOJA",
        autor="loja",
        texto="Temos sim!",
        enviada_em=T0 + timedelta(minutes=20),
    )
    await db.commit()
    (conversa,) = await _conversas(db)
    assert conversa.aguardando_resposta is False
    await _receber(app, "texto", wamid="wamid.ATRASADA", ts=T0 + timedelta(minutes=15))
    (conversa,) = await _conversas(db)
    assert conversa.aguardando_resposta is False
    assert len(await _mensagens(db, conversa.id)) == 4


async def test_duas_mensagens_ao_mesmo_tempo_de_cliente_novo_uma_conversa(db, zap):
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto", wamid="wamid.J1"), app=app)
    await gravar_corpo(corpo("texto", wamid="wamid.J2"), app=app)
    r1, r2 = await asyncio.gather(processar(), processar())
    assert r1["processados"] + r2["processados"] == 2
    assert len(await _contatos(db)) == 1
    (conversa,) = await _conversas(db)
    assert len(await _mensagens(db, conversa.id)) == 2


# ─────────────── o contato ───────────────


def test_nono_digito_so_em_celular():
    n = zap_contas.normalizar_wa_id
    assert n("551198765432") == "5511998765432"  # celular antigo, sem o 9
    assert n("+55 (11) 99876-5432") == "5511998765432"
    assert n("551134567890") == "551134567890"  # fixo (começa por 3): fica
    assert n("15550001111") == "15550001111"  # fora do Brasil: só dígitos
    assert n("") is None and n(None) is None and n("123") is None


async def test_celular_com_e_sem_o_9_e_o_mesmo_contato(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.SEM9", wa_id="551198765432")
    await _receber(app, "texto", wamid="wamid.COM9", wa_id="5511998765432")
    (contato,) = await _contatos(db)
    assert contato.wa_id_norm == "5511998765432"
    assert contato.wa_id == "5511998765432"  # o formato mais recente (é o `to`)
    assert len(await _conversas(db)) == 1


async def test_contato_so_com_bsuid(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "so_bsuid", wamid="wamid.SB", bsuid="BR.sem.telefone")
    (contato,) = await _contatos(db)
    assert (contato.bsuid, contato.wa_id, contato.wa_id_norm) == ("BR.sem.telefone", None, None)
    (conversa,) = await _conversas(db)
    assert conversa.comprador_id == "BR.sem.telefone"
    assert conversa.dados["comprador_plataforma"] == "BR.sem.telefone"


async def test_telefone_que_depois_ganha_bsuid_continua_o_mesmo_contato(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.T1")
    await gravar_corpo(_com_bsuid(corpo("texto", wamid="wamid.T2"), "BR.ana"), app=app)
    await processar()
    (contato,) = await _contatos(db)
    assert (contato.bsuid, contato.wa_id_norm) == ("BR.ana", WA_ID)
    assert len(await _conversas(db)) == 1


async def test_numero_reciclado_vira_contato_novo_e_o_antigo_e_solto(db, zap):
    app, _, _ = await cadastrar(db)
    await gravar_corpo(_com_bsuid(corpo("texto", wamid="wamid.DONO1"), "BR.dono.antigo"), app=app)
    await processar()
    await gravar_corpo(
        _com_bsuid(corpo("texto", wamid="wamid.DONO2", nome="Outra Pessoa"), "BR.dono.novo"),
        app=app,
    )
    await processar()
    antigo, novo = await _contatos(db)
    assert antigo.bsuid == "BR.dono.antigo" and antigo.wa_id_norm is None  # solto
    assert novo.bsuid == "BR.dono.novo" and novo.wa_id_norm == WA_ID
    c_antiga, c_nova = await _conversas(db)
    assert c_antiga.id == antigo.conversa_id and c_nova.id == novo.conversa_id
    assert c_nova.comprador_nome == "Outra Pessoa"
    textos_antiga = [m.texto for m in await _mensagens(db, c_antiga.id)]
    assert any("passou a ser de outra pessoa" in (t or "") for t in textos_antiga)
    assert [m.externo_id for m in await _mensagens(db, c_nova.id)] == ["wamid.DONO2"]


async def test_user_changed_number_atualiza_o_contato(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.ANTES_DA_TROCA")
    await _receber(
        app, "sistema", wamid="wamid.TROCA", novo_wa_id="5511922223333", ts=T0 + timedelta(hours=1)
    )
    (contato,) = await _contatos(db)
    assert (contato.wa_id, contato.wa_id_norm) == ("5511922223333", "5511922223333")
    assert contato.ultima_recebida_em == T0  # o aviso da Meta não abre a janela
    (conversa,) = await _conversas(db)
    nota = [m for m in await _mensagens(db, conversa.id) if m.autor == "sistema"]
    assert len(nota) == 1 and "trocou de número" in nota[0].texto
    # O número novo cai na MESMA conversa.
    await _receber(app, "texto", wamid="wamid.DEPOIS_DA_TROCA", wa_id="5511922223333")
    assert len(await _contatos(db)) == 1 and len(await _conversas(db)) == 1


# ─────────────── opt-out ───────────────


async def test_user_preferences_stop_e_resume(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.P1")
    r = await _receber(app, "preferencia", valor="stop")
    assert r["resultados"] == {"gravado": 1}
    (contato,) = await _contatos(db)
    assert contato.opt_out_em is not None and contato.opt_out_tipo == "todos"
    await _receber(app, "preferencia", valor="resume", ts=T0 + timedelta(minutes=1))
    (contato,) = await _contatos(db)
    assert contato.opt_out_em is None and contato.opt_out_tipo is None
    (conversa,) = await _conversas(db)
    notas = [m for m in await _mensagens(db, conversa.id) if m.autor == "sistema"]
    assert len(notas) == 2  # não vira mensagem do cliente: só a nota
    assert conversa.aguardando_resposta is True  # a nota não mexe na vez
    # Preferência de quem nunca falou com o número: o contato nasce com o
    # opt-out (sem conversa e sem abrir a janela de 24 h).
    r = await _receber(app, "preferencia", valor="stop", wa_id="5511900001111")
    assert r["resultados"] == {"gravado": 1}
    novo = next(c for c in await _contatos(db) if c.wa_id == "5511900001111")
    assert (novo.opt_out_tipo, novo.conversa_id, novo.ultima_recebida_em) == ("todos", None, None)
    assert len(await _conversas(db)) == 1


async def test_stop_antes_do_primeiro_contato_vale_quando_o_cliente_escreve(db, zap):
    """O cliente da época do Duoke toca "parar" no dia da migração, antes de
    escrever para o DaVinci: o stop não pode se perder (§5.3 item 7, §10.5)."""
    app, _, _ = await cadastrar(db)
    r = await _receber(app, "preferencia", valor="stop", ts=T0)
    assert r["resultados"] == {"gravado": 1}
    (contato,) = await _contatos(db)
    assert contato.opt_out_em == T0 and contato.opt_out_tipo == "todos"
    assert contato.ultima_recebida_em is None and contato.conversa_id is None
    assert await _conversas(db) == []
    depois = T0 + timedelta(days=2)
    await _receber(app, "texto", wamid="wamid.DEPOIS", ts=depois)
    (contato,) = await _contatos(db)  # o MESMO contato
    assert contato.opt_out_em == T0 and contato.opt_out_tipo == "todos"
    assert contato.ultima_recebida_em == depois and contato.conversa_id is not None
    (conversa,) = await _conversas(db)
    assert conversa.pode_enviar_ate == depois + timedelta(hours=24)


async def test_preferencia_fora_de_ordem_vale_o_gesto_mais_novo(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.ORDEM", ts=T0)
    # O cliente: resume às T0+1min e stop às T0+2min; a Meta entrega o stop antes.
    await gravar_corpo(corpo("preferencia", valor="stop", ts=T0 + timedelta(minutes=2)), app=app)
    await gravar_corpo(corpo("preferencia", valor="resume", ts=T0 + timedelta(minutes=1)), app=app)
    await processar()
    (contato,) = await _contatos(db)
    assert contato.opt_out_tipo == "todos" and contato.opt_out_em == T0 + timedelta(minutes=2)
    assert contato.preferencia_em == T0 + timedelta(minutes=2)
    # Um resume mais NOVO libera.
    await _receber(app, "preferencia", valor="resume", ts=T0 + timedelta(minutes=3))
    (contato,) = await _contatos(db)
    assert contato.opt_out_em is None and contato.opt_out_tipo is None
    # "PARAR" mais ANTIGO que o resume (chegou atrasado): não vale.
    velho = T0 + timedelta(seconds=150)
    await _receber(app, "texto", wamid="wamid.PARAR_VELHO", texto="parar", ts=velho)
    (contato,) = await _contatos(db)
    assert contato.opt_out_em is None
    # "PARAR" mais novo: vale.
    novo = T0 + timedelta(minutes=4)
    await _receber(app, "texto", wamid="wamid.PARAR_NOVO", texto="PARAR", ts=novo)
    (contato,) = await _contatos(db)
    assert contato.opt_out_tipo == "todos" and contato.preferencia_em == T0 + timedelta(minutes=4)
    # As notas de preferência só para o que foi aplicado (stop, resume).
    (conversa,) = await _conversas(db)
    notas = [m for m in await _mensagens(db, conversa.id) if m.autor == "sistema"]
    assert len(notas) == 2


async def test_parar_grava_na_hora_e_a_frase_parecida_so_poe_a_nota(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.CANCELAR", texto="quero cancelar o pedido")
    (contato,) = await _contatos(db)
    assert contato.opt_out_em is None  # "cancelar" nunca conta
    await _receber(app, "texto", wamid="wamid.PARECE", texto="Quero parar de receber essas msgs")
    (contato,) = await _contatos(db)
    assert contato.opt_out_em is None
    (conversa,) = await _conversas(db)
    notas = [m for m in await _mensagens(db, conversa.id) if m.autor == "sistema"]
    assert len(notas) == 1 and "confirme" in notas[0].texto
    assert notas[0].payload["zap"]["opt_out_sugerido"] is True
    await _receber(app, "texto", wamid="wamid.PARAR", texto="PARAR!")
    (contato,) = await _contatos(db)
    assert contato.opt_out_em is not None and contato.opt_out_tipo == "todos"


# ─────────────── os tipos (§5.4) ───────────────


async def test_reacao_nao_entra_na_fila(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.CITADA")
    (conversa,) = await _conversas(db)
    antes = conversa.ultima_mensagem_em
    r = await _receber(app, "reacao", wamid="wamid.R1", alvo="wamid.CITADA", texto="👍")
    assert r["resultados"] == {"gravado": 1}
    (m,) = await _mensagens(db, conversa.id)
    assert m.payload["zap"]["reacoes"]["cliente"]["emoji"] == "👍"
    (conversa,) = await _conversas(db)
    assert conversa.ultima_mensagem_em == antes
    # Tirou a reação (emoji vazio).
    await _receber(app, "reacao", wamid="wamid.R2", alvo="wamid.CITADA", texto="")
    (m,) = await _mensagens(db, conversa.id)
    assert m.payload["zap"]["reacoes"] == {}
    r = await _receber(app, "reacao", wamid="wamid.R3", alvo="wamid.NAO_EXISTE")
    assert r["resultados"] == {"sem_alvo": 1}


async def test_tipos_traduzidos(db, zap):
    app, _, _ = await cadastrar(db)
    casos = [
        ("nao_suportada", {}, "outro", "não entrega pela API"),
        ("botao", {"alvo": "wamid.MENU", "texto": "Sim, quero"}, "botao", "Sim, quero"),
        ("lista", {"alvo": "wamid.MENU", "texto": "Opção 2"}, "botao", "Opção 2"),
        ("botao_modelo", {"alvo": "wamid.MODELO", "texto": "Rastrear"}, "botao", "Rastrear"),
        ("localizacao", {}, "localizacao", "[Localização] Loja Centro · Praça da Sé, São Paulo"),
        ("contato", {}, "contato", "[Contato] Maria Fictícia · +55 11 90000-0001"),
    ]
    for i, (tipo, kw, _gravado, _trecho) in enumerate(casos):
        await _receber(app, tipo, wamid=f"wamid.TIPO{i}", ts=T0 + timedelta(seconds=i), **kw)
    (conversa,) = await _conversas(db)
    ms = await _mensagens(db, conversa.id)
    assert [m.tipo for m in ms] == [c[2] for c in casos]
    for m, (_, _, _, trecho) in zip(ms, casos, strict=True):
        assert trecho in m.texto, m.texto
    assert all(m.autor == "cliente" for m in ms)
    assert ms[1].payload["zap"]["contexto_wamid"] == "wamid.MENU"
    assert ms[1].payload["zap"]["botao"] == {"tipo": "button_reply", "id": "sim-quero"}
    assert ms[2].payload["zap"]["botao"]["tipo"] == "list_reply"
    assert ms[3].payload["zap"]["botao"] == {"tipo": "button", "payload": "RASTREAR"}
    assert ms[4].payload["zap"]["localizacao"]["lat"] == -23.55052
    assert ms[5].payload["zap"]["contatos"][0]["nome"] == "Maria Fictícia"
    assert conversa.aguardando_resposta is True  # a não suportada conta como fala


async def test_ctwa_vai_para_a_conversa_e_a_janela_gratis(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "ctwa", wamid="wamid.ANUNCIO")
    (conversa,) = await _conversas(db)
    (contato,) = await _contatos(db)
    assert conversa.dados["zap"]["ctwa"]["ctwa_clid"] == "ARAkLkA8rmlFeiCktE"
    assert contato.janela_gratis_ate == T0 + timedelta(hours=72)
    (m,) = await _mensagens(db, conversa.id)
    assert m.payload["zap"]["referral"]["source_type"] == "ad"
    # A mensagem seguinte (sem anúncio) não apaga o CTWA da conversa.
    await _receber(app, "texto", wamid="wamid.SEM_ANUNCIO", ts=T0 + timedelta(minutes=1))
    (conversa,) = await _conversas(db)
    assert conversa.dados["zap"]["ctwa"]["source_id"] == "120200000000001"


async def test_midia_registrada_pendente(db, zap):
    app, _, numero = await cadastrar(db)
    casos = [
        ("imagem", {"texto": "olha a foto"}, "imagem", "olha a foto", "image/jpeg"),
        ("audio", {}, "audio", "[Áudio de voz]", "audio/ogg; codecs=opus"),
        ("documento", {}, "arquivo", "nota fiscal.pdf", "application/pdf"),
        ("figurinha", {}, "figurinha", "[Figurinha]", "image/webp"),
        ("video", {"texto": ""}, "video", "[Vídeo]", "video/mp4"),
    ]
    for i, (tipo, kw, _, _, _) in enumerate(casos):
        await _receber(app, tipo, wamid=f"wamid.MIDIA{i}", ts=T0 + timedelta(seconds=i), **kw)
    (conversa,) = await _conversas(db)
    ms = await _mensagens(db, conversa.id)
    midias = {
        str(md.id): md for md in (await db.execute(select(AtendimentoZapMidia))).scalars().all()
    }
    assert len(midias) == 5
    for m, (_, _, tipo, texto, mime) in zip(ms, casos, strict=True):
        assert (m.tipo, m.texto) == (tipo, texto)
        (anexo,) = m.anexos
        assert anexo["status"] == "pendente" and anexo["mime"] == mime
        md = midias[anexo["midia_id"]]
        assert (md.status, md.direcao, md.mensagem_id, md.numero_ref) == (
            "pendente",
            "entrada",
            m.id,
            numero.id,
        )
        assert md.media_id and md.mime == mime
        assert md.expira_meta_em == m.enviada_em + timedelta(days=7)
    assert ms[1].anexos[0]["voz"] is True and ms[1].payload["zap"]["voz"] is True
    assert midias[ms[2].anexos[0]["midia_id"]].nome_original == "nota fiscal.pdf"


# ─────────────── saúde, conta e erros ───────────────


async def test_value_errors_poe_o_canal_em_erro_e_a_mensagem_seguinte_volta_a_ok(db, zap):
    app, _, numero = await cadastrar(db)
    canal_id, numero_id = numero.canal_id, numero.id
    r = await _receber(app, "erros")
    assert r["resultados"] == {"gravado": 1}
    canal = await db.get(AtendimentoCanal, canal_id, populate_existing=True)
    assert canal.status == "erro" and "131000" in canal.ultimo_erro
    numero = await db.get(AtendimentoZapNumero, numero_id, populate_existing=True)
    assert numero.ultimo_erro_code == 131000
    await _receber(app, "texto")
    canal = await db.get(AtendimentoCanal, canal_id, populate_existing=True)
    assert canal.status == "ok"


async def test_eventos_de_conta_restricao_qualidade_e_modelo(db, zap):
    app, _, numero = await cadastrar(db)
    canal_id, numero_id = numero.canal_id, numero.id
    await _receber(app, "qualidade")
    numero = await db.get(AtendimentoZapNumero, numero_id, populate_existing=True)
    assert numero.saude["qualidade_evento"] == "FLAGGED"
    assert numero.saude["limite"] == "TIER_1K"
    r = await _receber(app, "modelo_status")
    assert r["resultados"] == {"gravado": 1}
    await _receber(app, "account_update", valor="ACCOUNT_RESTRICTION")
    numero = await db.get(AtendimentoZapNumero, numero_id, populate_existing=True)
    assert numero.saude_status == "restrito"
    canal = await db.get(AtendimentoCanal, canal_id, populate_existing=True)
    assert canal.status == "erro" and "restringiu" in canal.ultimo_erro


async def test_restricao_da_conta_fica_na_barra_ate_a_meta_reabilitar(db, zap):
    """O número restrito continua recebendo: a mensagem seguinte não pode
    apagar o aviso (na Onda 1 a barra é o único lugar que o mostra)."""
    app, _, numero = await cadastrar(db)
    canal_id, numero_id = numero.canal_id, numero.id
    await _receber(app, "account_update", valor="ACCOUNT_RESTRICTION")
    frase = "A Meta restringiu a conta do WhatsApp (account_restriction)"
    await _receber(app, "texto", wamid="wamid.RESTRITO")
    canal = await db.get(AtendimentoCanal, canal_id, populate_existing=True)
    assert (canal.status, canal.ultimo_erro) == ("erro", frase)
    # Um erro passageiro por cima, e a mensagem seguinte: volta a frase da restrição.
    await _receber(app, "erros")
    await _receber(app, "texto", wamid="wamid.RESTRITO2")
    canal = await db.get(AtendimentoCanal, canal_id, populate_existing=True)
    assert (canal.status, canal.ultimo_erro) == ("erro", frase)
    # A Meta reabilita (`DISABLED_UPDATE` + `REINSTATE`: o "DISABLED" não é restrição).
    reabilitou = corpo("account_update", valor="DISABLED_UPDATE", ts=T0 + timedelta(hours=1))
    reabilitou["entry"][0]["changes"][0]["value"]["ban_info"] = {"waba_ban_state": "REINSTATE"}
    await gravar_corpo(reabilitou, app=app)
    await processar()
    numero = await db.get(AtendimentoZapNumero, numero_id, populate_existing=True)
    assert numero.saude_status == "ok" and "restricao" not in (numero.saude or {})
    canal = await db.get(AtendimentoCanal, canal_id, populate_existing=True)
    assert canal.status == "ok"
    await _receber(app, "texto", wamid="wamid.LIVRE")
    canal = await db.get(AtendimentoCanal, canal_id, populate_existing=True)
    assert canal.status == "ok"


async def test_numero_desconhecido_fica_reprocessavel(db, zap):
    app, conta, _ = await cadastrar(db)
    r = await _receber(app, "texto", pnid="939999999999999", wamid="wamid.ORFA")
    assert r["resultados"] == {"numero_desconhecido": 1}
    assert await _conversas(db) == []
    # Cadastrado o número, o evento volta para a fila.
    await cadastrar(db, pnid="939999999999999", nome_na_barra="Uranyx · Zap")
    async with _db.SessionLocal() as s:
        assert await zap_entrada.reabrir_eventos_do_numero(s, "939999999999999") == 1
        await s.commit()
    assert (await processar())["resultados"] == {"gravado": 1}
    assert len(await _conversas(db)) == 1


async def test_waba_de_outro_app_e_numero_de_outra_waba_sao_recusados(db, zap):
    app, _, _ = await cadastrar(db)
    outro = AtendimentoZapApp(
        app_id="910000000000002",
        segredo_enc=zap_contas.cifrar_segredo_app("outro-segredo"),
        verify_token_enc=zap_contas.cifrar_verify_token("outro-verify"),
    )
    db.add(outro)
    await db.commit()
    # A WABA é do app 1, mas o evento veio assinado pelo app 2.
    r = await _receber(outro, "texto", wamid="wamid.ROTA_TROCADA")
    assert r["resultados"] == {"waba_desconhecida": 1}
    # WABA fora do cadastro.
    r = await _receber(app, "texto", waba="929999999999999", wamid="wamid.SEM_WABA")
    assert r["resultados"] == {"waba_desconhecida": 1}
    # O número existe, mas é de OUTRA WABA do mesmo app.
    await cadastrar(db, waba="920000000000002", pnid="930000000000002")
    r = await _receber(app, "texto", pnid="930000000000002", wamid="wamid.OUTRA_WABA")
    assert r["resultados"] == {"numero_de_outra_waba": 1}
    assert await _conversas(db) == []


async def test_chave_desligada_deixa_o_evento_pendente(db, zap, monkeypatch):
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto"), app=app)
    monkeypatch.setattr(zap.settings, "atendimento_zap_leitura_ativa", False)
    assert await processar() == {"desligado": True}
    monkeypatch.setattr(zap.settings, "atendimento_zap_leitura_ativa", True)
    monkeypatch.setattr(zap.settings, "atendimento_leitura_ativa", False)
    assert await processar() == {"desligado": True}
    (ev,) = await eventos(db)
    assert ev.processado_em is None and ev.tentativas == 0
    assert await _conversas(db) == []


# ─────────────── status (§5.6) ───────────────


async def _da_loja(db, wamid: str, *, status: str = "enviada", minutos: int = 1):
    (conversa,) = await _conversas(db)
    m, _ = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=wamid,
        autor="loja",
        texto=f"resposta {wamid}",
        enviada_em=T0 + timedelta(minutes=minutos),
    )
    m.status = status
    await db.commit()
    return m


async def test_status_antes_da_mensagem_fica_pendente_e_e_aplicado_depois(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.CLIENTE")
    r = await _receber(app, "status", alvo="wamid.NOSSA", status="delivered")
    assert r["pendentes"] == 1 and r["processados"] == 0
    (ev,) = [e for e in await eventos(db) if e.tipo == "status"]
    assert ev.processado_em is None and ev.proxima_em is not None
    # A mensagem aparece; passada a espera, o status é aplicado.
    m = await _da_loja(db, "wamid.NOSSA")
    await db.execute(update(AtendimentoZapEvento).values(proxima_em=func.now()))
    await db.commit()
    assert (await processar())["resultados"] == {"gravado": 1}
    await db.refresh(m)
    entrega = m.payload["zap"]["entrega"]
    assert (entrega["status"], entrega["rank"]) == ("delivered", 2)
    assert entrega["entregue_em"] and entrega["categoria"] == "service"
    assert entrega["tipo_preco"] == "regular"
    # Um `sent` atrasado não volta atrás; o `read` anda.
    await _receber(app, "status", alvo="wamid.NOSSA", status="sent")
    await db.refresh(m)
    assert m.payload["zap"]["entrega"]["status"] == "delivered"
    assert m.payload["zap"]["entrega"]["enviada_em"]
    await _receber(app, "status", alvo="wamid.NOSSA", status="read")
    await db.refresh(m)
    assert (m.payload["zap"]["entrega"]["status"], m.status) == ("read", "enviada")


async def test_status_sem_alvo_depois_de_1h_e_linha_enviando_intocada(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.CLIENTE")
    m = await _da_loja(db, "wamid.EM_VOO", status="enviando")
    r = await _receber(app, "status", alvo="wamid.EM_VOO", status="delivered")
    assert r["pendentes"] == 1
    await db.refresh(m)
    assert m.status == "enviando" and "zap" not in (m.payload or {})
    # Do Duoke (nunca vai existir aqui): depois de 1 h, `sem_alvo`.
    await gravar_corpo(corpo("status", alvo="wamid.DO_DUOKE", status="read"), app=app)
    await db.execute(
        update(AtendimentoZapEvento)
        .where(AtendimentoZapEvento.chave == "s:wamid.DO_DUOKE:read")
        .values(recebido_em=func.now() - timedelta(hours=2))
    )
    await db.commit()
    r = await processar()
    assert r["resultados"].get("sem_alvo") == 1


async def test_failed_131047_marca_falhou_e_fecha_a_janela(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.CLIENTE")
    m = await _da_loja(db, "wamid.TARDE")
    await _receber(app, "status_falhou", alvo="wamid.TARDE")
    await db.refresh(m)
    assert (m.status, m.erro) == ("falhou", "meta:131047")
    assert m.payload["zap"]["entrega"]["erro_code"] == 131047
    (conversa,) = await _conversas(db)
    assert conversa.pode_enviar_ate <= datetime.now(UTC)


async def test_status_pelo_biz_opaque_callback_data(db, zap):
    app, _, _ = await cadastrar(db)
    await _receber(app, "texto", wamid="wamid.CLIENTE")
    m = await _da_loja(db, "wamid.COM_BIZ")
    # Sem o contato pelo telefone (recipient de outro formato): acha pelo id.
    await _receber(
        app, "status", alvo="wamid.COM_BIZ", status="read", biz=str(m.id), wa_id="000000000"
    )
    await db.refresh(m)
    assert m.payload["zap"]["entrega"]["status"] == "read"


# ─────────────── worker ───────────────


async def test_job_e_cron_registrados_e_saem_cedo_com_as_chaves_desligadas(monkeypatch):
    crons = {c.name: c for c in worker.WorkerSettings.cron_jobs}
    cron = crons["cron:atendimento_zap_varrer"]
    assert cron.second == {45} and not cron.run_at_startup
    assert cron.timeout_s == zap_entrada.TRAVA_TTL_S == 120
    funcoes = {
        getattr(f, "name", getattr(f, "__name__", None)): f for f in worker.WorkerSettings.functions
    }
    assert funcoes["atendimento_zap_processar"].timeout_s == 120
    assert zap_entrada.ORCAMENTO_S < zap_entrada.TRAVA_TTL_S

    async def _nao_pode(*a, **k):
        raise AssertionError("com a chave desligada a rodada nem começa")

    monkeypatch.setattr(zap_entrada, "rodada", _nao_pode)
    for leitura, zap_leitura in ((False, False), (True, False), (False, True)):
        monkeypatch.setattr(worker._settings, "atendimento_leitura_ativa", leitura)
        monkeypatch.setattr(worker._settings, "atendimento_zap_leitura_ativa", zap_leitura)
        assert await worker.atendimento_zap_processar({}) is None
        assert await worker.atendimento_zap_varrer({}) is None


async def test_rodada_do_job_apaga_o_pedido_e_respeita_a_trava(db, zap):
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto"), app=app)
    zap.redis.dados[zap_entrada.CHAVE_PEDIDO] = "1"
    zap.redis.dados[zap_entrada.CHAVE_TRAVA] = "de-outra-rodada"
    assert await zap_entrada.rodada(origem="job") == {"pulado": "ocupado"}
    assert zap_entrada.CHAVE_PEDIDO not in zap.redis.dados  # o que chegar pede outra
    del zap.redis.dados[zap_entrada.CHAVE_TRAVA]
    resumo = await zap_entrada.rodada(origem="cron")
    assert resumo is not None and resumo["processados"] == 1
    assert zap_entrada.CHAVE_TRAVA not in zap.redis.dados  # soltou
    assert len(await _conversas(db)) == 1


def test_itens_do_corpo_e_puro_e_nao_descarta(zap):
    c = corpo("texto")
    assert [i.chave for i in zap_entrada.itens_do_corpo(c)] == [
        "m:" + c["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    ]
    # Sem `entry.time`, vale o dia do recebimento no hash dos itens `e:`.
    erros = corpo("erros")
    a = zap_entrada.itens_do_corpo(erros, hoje="2026-10-19")[0].chave
    b = zap_entrada.itens_do_corpo(erros, hoje="2026-10-19")[0].chave
    c2 = zap_entrada.itens_do_corpo(erros, hoje="2026-10-20")[0].chave
    assert a == b != c2
    assert json.dumps(zap_entrada.item_json_invalido(b"\xff{").payload)
    assert APP_ID and WABA
