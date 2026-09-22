"""Ocorrência grave nos Correios (robô da Ouvidoria) — o que este arquivo trava.

- ABRE por `problema_correios_em` preenchido e NÃO abre pro que terminou
  (situação final no Bling ou `entregue_em` carimbado) — Entregue fica 90
  dias na tabela, então é filtrado na mão;
- rótulo e ação saem da palavra-chave do `logistica_track.evento_grave`
  (apreensão → Correios/SEFAZ, extravio/roubo/avaria → chamado, devolvido →
  aguardar, não entregue/endereço/recusado → nova tentativa);
- a ocorrência FECHA como "sumiu" quando o pedido chega a Entregue ou a
  linha some da tabela (cleanup de Cancelado/Resolvido/Perdimento), e NÃO
  fecha porque a localização atual perdeu a palavra grave;
- `logistica.chamado` preenchido rebaixa pra `baixa` sem pessoa (e não abre
  linha nova — é a mesma ocorrência), e a família "nova tentativa" (não
  entregue / endereço / recusado) já NASCE em `baixa` sem pessoa;
- `17track:saldo` abre e fecha pela flag do Redis; `rastreio:<codigo>` abre
  só pro número PENDENTE de registro que está em quarentena e fecha quando o
  sync consegue registrar;
- Redis mudo não fecha saldo nem rastreio (só re-vê) e continua julgando os
  pedidos, inclusive as linhas manuais sem número do Bling (`linha:<id>`);
- o sweep é serializado por advisory lock e o tick sai sem rodar com o robô
  `desligado`.

O Redis é sempre substituído pelos wrappers do módulo (`_sem_saldo_desde` /
`_em_quarentena`): nenhum teste encosta no Redis de verdade.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Logistica,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    OuvidoriaRodada,
)
from app.services import ouvidoria as svc
from app.services import vigia_correios as vigia

pytestmark = pytest.mark.asyncio

ROBO = vigia.ROBO


# ─── fixtures ──────────────────────────────────────────────────────────────


async def _limpar(db: AsyncSession) -> None:
    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos"):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.execute(delete(Logistica))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa_ouvidoria(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


class _Threema:
    enviados: list[tuple[str, list[str]]] = []

    async def send_to_all(self, texto: str, recipients=None) -> dict:
        self.enviados.append((texto, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}


@pytest.fixture(autouse=True)
def _sem_threema(monkeypatch) -> list[tuple[str, list[str]]]:
    _Threema.enviados = []
    monkeypatch.setattr(svc.threema, "ThreemaClient", _Threema)
    return _Threema.enviados


@pytest.fixture(autouse=True)
def redis_fake(monkeypatch) -> dict:
    """Substitui os dois wrappers de Redis do robô. `falhar=True` devolve a
    sentinela REDIS_FALHOU — o mesmo que o módulo faz quando o Redis não
    responde (aqui não existe Redis, e tocar nele erraria por conexão)."""
    estado: dict = {"desde": None, "quarentena": set(), "falhar": False, "perguntou": []}

    async def _saldo():
        return vigia.REDIS_FALHOU if estado["falhar"] else estado["desde"]

    async def _quarentena(numeros):
        estado["perguntou"].append(list(numeros))
        if not numeros:  # o wrapper real nem chega no Redis
            return set()
        if estado["falhar"]:
            return vigia.REDIS_FALHOU
        return {n for n in numeros if n in estado["quarentena"]}

    monkeypatch.setattr(vigia, "_sem_saldo_desde", _saldo)
    monkeypatch.setattr(vigia, "_em_quarentena", _quarentena)
    return estado


async def _linha(db: AsyncSession, **campos) -> Logistica:
    """Linha da aba Logística no padrão de tests/test_logistica_track_sync."""
    dados = {
        "plataforma": "Mercado Livre",
        "conta": "Mercado Livre marquezini",
        "data": date.today(),
        "status_bling": "Problemas",
    }
    dados.update(campos)
    row = Logistica(**dados)
    db.add(row)
    await db.commit()
    return row


def _grave(**campos) -> dict:
    """Campos de uma linha que JÁ teve leitura grave dos Correios (é o
    `aplicar_leitura` que carimba os dois campos, na primeira vez)."""
    texto = campos.pop("texto", "Objeto apreendido pela Receita Federal")
    visto = campos.pop("visto", datetime(2026, 9, 18, 14, 8, tzinfo=UTC))
    base = {
        "localizacao": texto,
        "localizacao_at": visto,
        "problema_correios": texto,
        "problema_correios_em": visto,
    }
    base.update(campos)
    return base


async def _abertas(db: AsyncSession) -> dict[str, OuvidoriaOcorrencia]:
    rows = (
        (
            await db.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {o.chave: o for o in rows}


async def _todas(db: AsyncSession) -> dict[str, OuvidoriaOcorrencia]:
    rows = (
        (
            await db.execute(
                select(OuvidoriaOcorrencia).where(OuvidoriaOcorrencia.robo_chave == ROBO)
            )
        )
        .scalars()
        .all()
    )
    return {o.chave: o for o in rows}


async def _rodadas(db: AsyncSession) -> list[OuvidoriaRodada]:
    return list(
        (
            await db.execute(
                select(OuvidoriaRodada)
                .where(OuvidoriaRodada.robo_chave == ROBO)
                .order_by(OuvidoriaRodada.iniciada_em)
            )
        )
        .scalars()
        .all()
    )


# ─── pedido com ocorrência grave ───────────────────────────────────────────


async def test_abre_pelo_problema_e_ignora_o_que_ja_terminou(db):
    await _linha(
        db,
        pedido_bling="295070",
        pedido_marketplace="2000012345",
        rastreio="AD828496989BR",
        servico_envio="SEDEX",
        **_grave(),
    )
    # Entregue continua na tabela por 90 dias (o cleanup só tira depois) —
    # é aqui que ele tem que ser filtrado.
    await _linha(
        db, pedido_bling="295071", rastreio="AD1BR", status_bling="Entregue",
        **_grave(texto="Objeto extraviado"),
    )
    # 17track confirmou a entrega, o Bling ainda não foi classificado.
    await _linha(
        db, pedido_bling="295072", rastreio="AD2BR",
        entregue_em=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        **_grave(texto="Objeto roubado"),
    )
    # Sem leitura grave nenhuma: não é assunto do robô.
    await _linha(db, pedido_bling="295073", rastreio="AD3BR", localizacao="Em trânsito")

    resultado = await vigia.vigia_correios_run(db)

    abertas = await _abertas(db)
    assert set(abertas) == {"pedido:295070"}
    o = abertas["pedido:295070"]
    assert o.titulo == "Apreensão/retenção fiscal — SEDEX AD828496989BR"
    assert o.acao == vigia.ACAO_APREENSAO
    assert o.severidade == "pessoa" and o.precisa_pessoa is True
    assert o.plataforma == "ml" and o.conta == "Mercado Livre marquezini"
    assert o.pedido == "295070"
    assert o.link == "/logistica?tab=ml&q=295070"
    assert "Última localização: Objeto apreendido pela Receita Federal (Correios," in o.detalhe
    assert "Ocorrência vista em 18/09 11:08" in o.detalhe  # 14:08 UTC = 11:08 BR
    assert "Situação no Bling: Problemas" in o.detalhe
    assert "Chamado:" not in o.detalhe
    assert o.dados["evento"] == "apreendid" and o.dados["rastreio"] == "AD828496989BR"
    assert o.dados["pedido_marketplace"] == "2000012345"
    assert o.dados["problema_correios_em"] == "2026-09-18T14:08:00+00:00"

    assert resultado["linhas_graves"] == 1 and resultado["finais_ignoradas"] == 2
    assert resultado["novas"] == 1 and resultado["persistem"] == 0
    assert resultado["sumiram"] == 0 and resultado["redis_falhou"] == 0
    assert resultado["resumo"] == "1 grave · 0 fecharam · 0 em quarentena · 17track ok"

    rodadas = await _rodadas(db)
    assert len(rodadas) == 1 and rodadas[0].ok
    assert rodadas[0].contadores["linhas_graves"] == 1
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.ultima_rodada_ok is True
    assert robo.ultima_rodada_resumo == resultado["resumo"]
    # Robô novo nasce silencioso: registra no painel e não manda Threema.
    assert robo.modo == "silencioso" and resultado["avisadas"] == 0


@pytest.mark.parametrize(
    ("texto", "rotulo", "acao"),
    [
        ("Objeto apreendido pela SEFAZ", "Apreensão/retenção fiscal", vigia.ACAO_APREENSAO),
        ("Objeto extraviado", "Extravio", vigia.ACAO_CHAMADO),
        ("Objeto roubado em trânsito", "Roubo/furto", vigia.ACAO_CHAMADO),
        ("Objeto danificado", "Avaria", vigia.ACAO_CHAMADO),
        ("Objeto devolvido ao remetente", "Devolvido ao remetente", vigia.ACAO_AGUARDAR),
        ("Endereço incorreto", "Endereço incorreto", vigia.ACAO_NOVA_TENTATIVA),
        (
            "Entrega recusada pelo destinatário",
            "Recusado pelo destinatário",
            vigia.ACAO_NOVA_TENTATIVA,
        ),
    ],
)
async def test_rotulo_e_acao_saem_da_palavra_do_evento(db, texto, rotulo, acao):
    await _linha(db, pedido_bling="300001", rastreio="AD9BR", **_grave(texto=texto))

    await vigia.vigia_correios_run(db)

    o = (await _abertas(db))["pedido:300001"]
    assert o.titulo == f"{rotulo} — Correios AD9BR"  # sem servico_envio → "Correios"
    assert o.acao == acao


@pytest.mark.parametrize(
    ("texto", "pessoa"),
    [
        ("Objeto não entregue — carteiro não atendido", False),
        ("Endereço incorreto", False),
        ("Entrega recusada pelo destinatário", False),
        ("Objeto devolvido ao remetente", True),
        ("Objeto apreendido pela SEFAZ", True),
    ],
)
async def test_nova_tentativa_fica_so_no_painel(db, texto, pessoa):
    """Tentativa frustrada, endereço a confirmar e recusa são o dia a dia dos
    Correios e se resolvem na tentativa seguinte. Como `problema_correios`
    nunca é limpo, cobrar pessoa nesses viraria Threema permanente até o pedido
    chegar a situação final — `pessoa` fica pra apreensão, extravio, roubo,
    avaria e devolvido ao remetente."""
    await _linha(db, pedido_bling="300010", rastreio="AD9BR", **_grave(texto=texto))

    await vigia.vigia_correios_run(db)

    o = (await _abertas(db))["pedido:300010"]
    assert o.precisa_pessoa is pessoa
    assert o.severidade == ("pessoa" if pessoa else "baixa")
    # A ação continua dizendo o que fazer nos dois casos.
    assert o.acao


async def test_evento_sem_palavra_conhecida_ainda_abre(db):
    """Redação nova dos Correios (ou proxy do marketplace): o carimbo de
    `problema_correios_em` é o que manda, não a palavra."""
    await _linha(
        db, pedido_bling="300002", **_grave(texto="Objeto retido — situação inusitada")
    )

    await vigia.vigia_correios_run(db)

    o = (await _abertas(db))["pedido:300002"]
    assert o.titulo == "Ocorrência grave"  # sem rastreio: só o rótulo
    assert o.acao == vigia.ACAO_CHAMADO and o.dados["evento"] is None


async def test_localizacao_sem_carimbo_e_rotulada_como_do_marketplace(db):
    """O proxy do ML sobrescreve a Localização sem carimbo físico — chamar
    aquilo de leitura dos Correios enganaria quem vai abrir o chamado."""
    await _linha(
        db,
        pedido_bling="300003",
        **_grave(texto="Objeto apreendido", localizacao_at=None),
    )

    await vigia.vigia_correios_run(db)

    o = (await _abertas(db))["pedido:300003"]
    assert "(informada pelo marketplace)" in o.detalhe
    assert "Correios, lido" not in o.detalhe


async def test_fecha_quando_o_pedido_chega_a_entregue_ou_a_linha_some(db):
    fica = await _linha(db, pedido_bling="295070", rastreio="AD1BR", **_grave())
    entregue = await _linha(db, pedido_bling="295071", rastreio="AD2BR", **_grave())
    some = await _linha(db, pedido_bling="295072", rastreio="AD3BR", **_grave())

    r1 = await vigia.vigia_correios_run(db)
    assert r1["novas"] == 3 and set(await _abertas(db)) == {
        "pedido:295070", "pedido:295071", "pedido:295072"
    }

    entregue.status_bling = "Entregue"
    # O evento seguinte de uma apreensão não tem palavra grave — e o problema
    # continua: `problema_correios` é a memória, não a localização atual.
    fica.localizacao = "Objeto em análise de destinação"
    await db.delete(some)  # cleanup_finalizados (Cancelado/Resolvido/Perdimento)
    await db.commit()

    r2 = await vigia.vigia_correios_run(db)

    assert set(await _abertas(db)) == {"pedido:295070"}
    assert r2["linhas_graves"] == 1 and r2["persistem"] == 1 and r2["novas"] == 0
    assert r2["sumiram"] == 2
    assert r2["resumo"] == "1 grave · 2 fecharam · 0 em quarentena · 17track ok"
    todas = await _todas(db)
    assert todas["pedido:295071"].fechamento == "sumiu"
    assert todas["pedido:295071"].fechada_por == "robô"
    assert todas["pedido:295072"].fechamento == "sumiu"


async def test_chamado_aberto_rebaixa_a_mesma_ocorrencia(db):
    row = await _linha(db, pedido_bling="295070", rastreio="AD1BR", **_grave())

    await vigia.vigia_correios_run(db)
    antes = (await _abertas(db))["pedido:295070"]
    assert antes.severidade == "pessoa" and antes.precisa_pessoa is True

    row.chamado = "5104417290"
    await db.commit()
    r2 = await vigia.vigia_correios_run(db)

    depois = (await _abertas(db))["pedido:295070"]
    # Mesma linha (a ocorrência persiste enquanto o pacote não resolve) —
    # só para de cobrar pessoa, pra o "Tratado" não virar Threema amanhã.
    assert depois.id == antes.id and r2["persistem"] == 1 and r2["novas"] == 0
    assert depois.severidade == "baixa" and depois.precisa_pessoa is False
    assert depois.titulo.endswith(" (chamado aberto)")
    assert "Chamado: 5104417290" in depois.detalhe
    assert depois.dados["chamado"] == "5104417290"


async def test_linha_manual_sem_pedido_bling_usa_o_id(db):
    row = await _linha(db, pedido_bling=None, plataforma="Magalu", **_grave())

    await vigia.vigia_correios_run(db)

    o = (await _abertas(db))[f"linha:{row.id}"]
    assert o.pedido is None and o.plataforma == "magalu"
    assert o.link == "/logistica?tab=magalu"


async def test_plataforma_desconhecida_abre_sem_codigo(db):
    await _linha(db, pedido_bling="300004", plataforma="Duoke", **_grave())

    await vigia.vigia_correios_run(db)

    o = (await _abertas(db))["pedido:300004"]
    assert o.plataforma is None and o.link == "/logistica?q=300004"


# ─── 17track sem saldo ─────────────────────────────────────────────────────


async def test_saldo_abre_e_fecha_pela_flag(db, redis_fake):
    redis_fake["desde"] = "2026-09-22T10:30:00+00:00"

    r1 = await vigia.vigia_correios_run(db)

    o = (await _abertas(db))["17track:saldo"]
    assert o.titulo == "17track sem saldo — nada mais atualiza"
    assert o.severidade == "urgente" and o.precisa_pessoa is True
    assert o.plataforma == "interno" and o.pedido is None and o.link == "/logistica"
    assert o.acao == vigia.ACAO_SALDO
    assert "Sem saldo desde 22/09 07:30" in o.detalhe
    assert o.dados["desde"] == "2026-09-22T10:30:00+00:00"
    assert r1["sem_saldo"] == 1
    assert r1["resumo"] == "0 graves · 0 fecharam · 0 em quarentena · 17track SEM SALDO"

    redis_fake["desde"] = None  # recarregou (ou a flag expirou em 3 h)
    r2 = await vigia.vigia_correios_run(db)

    assert await _abertas(db) == {}
    assert r2["sem_saldo"] == 0 and r2["sumiram"] == 1
    assert (await _todas(db))["17track:saldo"].fechamento == "sumiu"


# ─── rastreio recusado (quarentena) ────────────────────────────────────────


async def _pendente(db: AsyncSession, **campos) -> Logistica:
    """Linha que o sync de 17track tentaria registrar: Correios, ML "Em
    andamento", dentro da janela e com `rastreio_17track` diferente."""
    dados = {"status_bling": "Em andamento", "data": date.today()}
    dados.update(campos)
    return await _linha(db, **dados)


async def test_quarentena_abre_so_pro_pendente_e_fecha_quando_registra(db, redis_fake):
    preso = await _pendente(db, pedido_bling="296762", rastreio="AA123456789BR")
    # Já registrado: sai dos pendentes e nem é perguntado ao Redis.
    await _pendente(
        db, pedido_bling="296763", rastreio="BB1BR", rastreio_17track="BB1BR"
    )
    # Fora da varredura do sync (situação que não é "Em andamento").
    await _pendente(db, pedido_bling="296764", rastreio="CC1BR", status_bling="Problemas")
    redis_fake["quarentena"] = {"AA123456789BR"}

    r1 = await vigia.vigia_correios_run(db)

    assert redis_fake["perguntou"] == [["AA123456789BR"]]
    o = (await _abertas(db))["rastreio:AA123456789BR"]
    assert o.titulo == "Rastreio recusado pelo 17track — AA123456789BR"
    assert o.severidade == "baixa" and o.precisa_pessoa is False
    assert o.acao == vigia.ACAO_RASTREIO and o.pedido == "296762"
    assert o.link == "/logistica?tab=ml&q=296762"
    assert o.dados["rastreio"] == "AA123456789BR"
    assert r1["quarentena"] == 1 and r1["novas"] == 1
    assert r1["resumo"] == "0 graves · 0 fecharam · 1 em quarentena · 17track ok"

    preso.rastreio_17track = "AA123456789BR"  # o sync conseguiu registrar
    await db.commit()
    r2 = await vigia.vigia_correios_run(db)

    assert await _abertas(db) == {} and r2["quarentena"] == 0 and r2["sumiram"] == 1


async def test_rastreio_minusculo_casa_com_a_chave_do_redis(db, redis_fake):
    """`_num` normaliza pra MAIÚSCULAS — é assim que o sync grava a chave."""
    await _pendente(db, pedido_bling="296765", rastreio=" aa123456789br ")
    redis_fake["quarentena"] = {"AA123456789BR"}

    await vigia.vigia_correios_run(db)

    assert "rastreio:AA123456789BR" in await _abertas(db)


# ─── Redis mudo ────────────────────────────────────────────────────────────


async def test_redis_mudo_nao_fecha_saldo_nem_rastreio_mas_julga_o_pedido(db, redis_fake):
    pedido = await _linha(db, pedido_bling="295070", rastreio="AD1BR", **_grave())
    manual = await _linha(db, pedido_bling=None, **_grave())
    await _pendente(db, pedido_bling="296762", rastreio="AA1BR")
    redis_fake["desde"] = "2026-09-22T10:30:00+00:00"
    redis_fake["quarentena"] = {"AA1BR"}

    r1 = await vigia.vigia_correios_run(db)
    assert set(await _abertas(db)) == {
        "pedido:295070", f"linha:{manual.id}", "17track:saldo", "rastreio:AA1BR"
    }
    assert r1["redis_falhou"] == 0

    # Redis cai e o pedido chega a Entregue na mesma rodada.
    redis_fake["falhar"] = True
    pedido.status_bling = "Entregue"
    await db.commit()
    r2 = await vigia.vigia_correios_run(db)

    # Saldo e rastreio ficam de pé (não sabemos o estado deles); o pedido é
    # julgado normalmente — o banco respondeu — e a linha manual também.
    assert set(await _abertas(db)) == {
        f"linha:{manual.id}", "17track:saldo", "rastreio:AA1BR"
    }
    assert r2["redis_falhou"] == 1 and r2["sumiram"] == 1 and r2["sem_saldo"] == 0
    assert r2["resumo"] == (
        "1 grave · 1 fechou · 0 em quarentena · 17track ok · Redis não respondeu"
    )


async def test_redis_mudo_sem_ocorrencia_aberta_nao_quebra(db, redis_fake):
    redis_fake["falhar"] = True

    r = await vigia.vigia_correios_run(db)

    assert r["redis_falhou"] == 1 and r["sumiram"] == 0
    assert (await _rodadas(db))[-1].ok is True


# ─── aviso, sweep e tick ───────────────────────────────────────────────────


async def test_so_avisa_no_threema_com_o_robo_ligado(db, _sem_threema):
    await _linha(db, pedido_bling="295070", rastreio="AD1BR", **_grave())
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.threema_recipients = "ABCDEFGH"
    await db.commit()

    assert (await vigia.vigia_correios_run(db))["avisadas"] == 0  # silencioso
    assert _sem_threema == []

    robo.modo = "ligado"
    await db.commit()
    assert (await vigia.vigia_correios_run(db))["avisadas"] == 1
    assert len(_sem_threema) == 1
    assert "Apreensão/retenção fiscal" in _sem_threema[0][0]


async def test_sweep_e_serializado_pelo_advisory_lock(db, monkeypatch):
    """Sessão que segura o lock do sweep → o outro sweep sai na hora."""
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vigia._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vigia.vigia_correios_sweep() == {"skipped": "lock_busy"}
    await db.rollback()  # solta o lock

    chamado = []

    async def _run(session):
        chamado.append(1)
        return {"ok": True}

    monkeypatch.setattr(vigia, "vigia_correios_run", _run)
    assert await vigia.vigia_correios_sweep() == {"ok": True} and chamado == [1]


async def test_tick_nao_roda_com_o_robo_desligado(db, monkeypatch):
    from app import worker

    chamadas: list[int] = []

    async def _sweep():
        chamadas.append(1)
        return {"novas": 0}

    # O tick faz `from app.services.vigia_correios import vigia_correios_sweep`
    # DENTRO da função, então quem tem que ser trocado é o módulo do serviço.
    monkeypatch.setattr(vigia, "vigia_correios_sweep", _sweep)
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.modo = "desligado"
    await db.commit()

    await worker.vigia_correios_tick({})
    assert chamadas == [] and await _rodadas(db) == []

    for modo in ("silencioso", "ligado"):
        robo.modo = modo
        await db.commit()
        await worker.vigia_correios_tick({})
    assert chamadas == [1, 1]


async def test_rodada_que_quebra_fica_gravada_como_falha(db, monkeypatch):
    async def _explode(*_a, **_k):
        raise RuntimeError("banco caiu")

    # O catálogo já está no banco (o startup do worker sincroniza): a rodada
    # quebrada é revertida antes de ser gravada, e sem a linha do robô o
    # INSERT da rodada não teria FK pra apontar.
    await svc.sincronizar_catalogo(db)
    await db.commit()
    monkeypatch.setattr(vigia, "_graves", _explode)
    with pytest.raises(RuntimeError):
        await vigia.vigia_correios_run(db)

    rodada = (await _rodadas(db))[-1]
    assert rodada.ok is False and "banco caiu" in (rodada.erro or "")
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.ultima_rodada_ok is False


async def test_hora_do_detalhe_e_a_de_brasilia(db):
    """A data que a pessoa lê é a de Brasília (UTC-3), não a do banco — de
    madrugada as duas nem caem no mesmo dia."""
    await _linha(
        db,
        pedido_bling="300005",
        **_grave(visto=datetime(2026, 9, 19, 2, 30, tzinfo=UTC)),
    )

    await vigia.vigia_correios_run(db)

    o = (await _abertas(db))["pedido:300005"]
    assert "Ocorrência vista em 18/09 23:30" in o.detalhe
