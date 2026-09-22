"""Chamados: réplica e monitoramento (robô da Ouvidoria) — 22/09/2026.

O que este arquivo trava:

- **hook do envio** (`envio:<chamado_id>`): a réplica que não sai abre
  ocorrência `pessoa` no ato e a que sai fecha; `pendente` (fila do robô),
  `registrada` (canal manual) e os erros de `ERROS_ACOMPANHADOS` (disputa já
  contestada, prazo, caso encerrado) NÃO são "envio falhou";
- o mesmo hook no `/agent/resultado`: a falha que volta pra fila não abre
  nada, a que esgotou as tentativas abre, e o `ok=true` fecha;
- **hook da consulta** (`consulta:<chamado_id>`): a falha do monitor do ML
  escala `info` → `baixa` (no limite) → `pessoa` (mais 3 passadas), e a
  leitura que volta a funcionar fecha a linha — é o que zera o contador;
  `chamado_sem_integracao_ml` NÃO abre (quem lê esses é o sync das
  devoluções) e a `Passada` resolve robô + abertas uma vez por varredura,
  deixando o caminho feliz sem tocar no banco;
- **rodada** (`encerrado:<chamado_id>`): só chamado com status final parado
  há mais que `encerrado_dias`, nunca o concluído nem o que tem instrução
  pendente pro robô (esse está em Análise Robô na aba); fecha como "sumiu"
  quando a pessoa conclui ou a linha é excluída;
- a rodada NÃO fecha `envio:`/`consulta:` pelo `fechar_nao_vistas` (elas são
  de hook), mas RECONCILIA: chamado concluído/excluído, abertura que voltou
  pra fila → "sumiu";
- robô `desligado` não grava nada, e o hook sem a linha do robô (deploy antes
  do catálogo sincronizar) não derruba o envio;
- o resumo da rodada e o tick do worker que sai quando está desligado.
"""
# ruff: noqa: S608 — o `_limpar` monta o DELETE com nomes de tabela fixos.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Chamado,
    ChamadoMensagem,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    OuvidoriaRodada,
)
from app.services import chamados as chamados_svc
from app.services import ouvidoria as svc
from app.services import vigia_chamados as vigia

pytestmark = pytest.mark.asyncio

ROBO = vigia.ROBO


# ─── fixtures ──────────────────────────────────────────────────────────────


async def _limpar(db: AsyncSession) -> None:
    for tbl in (
        "ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos",
        "chamado_mensagem", "chamados",
    ):
        await db.execute(text(f"DELETE FROM {tbl}"))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db: AsyncSession):
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


async def _catalogo(db: AsyncSession) -> OuvidoriaRobo:
    await svc.sincronizar_catalogo(db)
    await db.commit()
    return await db.get(OuvidoriaRobo, ROBO)


async def _chamado(db: AsyncSession, **kw) -> Chamado:
    base = {
        "origem": "devolucao",
        "canal": "api",
        "plataforma": "Mercado Livre",
        "conta": "ML Aguiar",
        "pedido_bling": "300001",
        "pedido_marketplace": "ML1",
        "chamado": "5100000001",
    }
    base.update(kw)
    ch = Chamado(**base)
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ch


async def _abertas(db: AsyncSession, prefixo: str = "") -> list[OuvidoriaOcorrencia]:
    rows = list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
                .order_by(OuvidoriaOcorrencia.aberta_em)
            )
        ).scalars()
    )
    return [o for o in rows if o.chave.startswith(prefixo)]


async def _linha(db: AsyncSession, chave: str) -> OuvidoriaOcorrencia | None:
    return (
        await db.execute(
            select(OuvidoriaOcorrencia)
            .where(OuvidoriaOcorrencia.robo_chave == ROBO, OuvidoriaOcorrencia.chave == chave)
            .order_by(OuvidoriaOcorrencia.aberta_em.desc())
            .limit(1)
        )
    ).scalars().first()


def _falha_ml(monkeypatch, erro: Exception | None) -> None:
    """`_enviar_api_ml` do módulo de chamados: levanta `erro` ou deixa passar."""

    async def _fake(session, ch, texto):
        if erro is not None:
            raise erro

    monkeypatch.setattr(chamados_svc, "_enviar_api_ml", _fake)


async def _replicar(db: AsyncSession, ch: Chamado, texto: str = "oi") -> ChamadoMensagem:
    msg = chamados_svc.nova_mensagem(ch, texto=texto, tipo="replica", autor_nome="Vinicius")
    db.add(msg)
    await db.flush()
    await chamados_svc.enviar_mensagem(db, ch, msg)
    await db.commit()
    return msg


# ─── hook do envio ─────────────────────────────────────────────────────────


async def test_replica_que_nao_sai_abre_e_a_que_sai_fecha(db, monkeypatch):
    await _catalogo(db)
    ch = await _chamado(db)

    _falha_ml(monkeypatch, RuntimeError("500 do ML"))
    msg = await _replicar(db, ch)
    assert msg.status == "falhou"

    o = await _linha(db, f"envio:{ch.id}")
    assert o is not None and o.fechada_em is None
    assert o.titulo == "Réplica não enviada — Mercado Livre 300001"
    assert o.severidade == "pessoa" and o.precisa_pessoa is True
    assert o.plataforma == "ml" and o.conta == "ML Aguiar" and o.pedido == "300001"
    assert o.link == "/chamados?search=300001"
    assert "500 do ML" in (o.detalhe or "")
    assert o.dados["mensagem_id"] == str(msg.id) and o.dados["tipo"] == "replica"
    assert o.acao == vigia.ACAO_ENVIO

    # o envio seguinte dá certo → a mesma chave fecha como "sumiu"
    _falha_ml(monkeypatch, None)
    msg2 = await _replicar(db, ch, "de novo")
    assert msg2.status == "enviada"
    await db.refresh(o)
    assert o.fechada_em is not None and o.fechamento == "sumiu" and o.fechada_por == "robô"


async def test_abertura_tem_acao_propria_e_o_erro_vira_frase(db, monkeypatch):
    """O erro com código conhecido vira o texto da coluna Status, e a abertura
    ganha a ação de abrir no Seller Center (não há o que "responder")."""
    await _catalogo(db)
    ch = await _chamado(db, plataforma="Shopee", pedido_bling="", pedido_marketplace="SP9")
    msg = chamados_svc.nova_mensagem(ch, texto="contestação", tipo="abertura")
    msg.status = "falhou"
    msg.erro = "shopee_captcha_humano"
    msg.tentativas = 2
    db.add(msg)
    await db.flush()

    await vigia.registrar_resultado_envio(db, ch, msg)
    await db.commit()

    o = await _linha(db, f"envio:{ch.id}")
    assert o is not None
    assert o.titulo == "Abertura não enviada — Shopee 5100000001"  # sem pedido Bling: protocolo
    assert o.acao == vigia.ACAO_ABERTURA
    assert chamados_svc.MOTIVO_DO_ERRO["shopee_captcha_humano"] in (o.detalhe or "")
    assert "tentativa 2 de 3" in (o.detalhe or "")
    assert o.link == "/chamados?search=5100000001"


@pytest.mark.parametrize(
    ("status", "erro"),
    [
        ("pendente", "shopee_aguardando_pacote"),  # fila/retry do robô
        ("registrada", None),  # canal manual, não houve envio
        ("falhou", "shopee_ja_contestada"),  # o acompanhamento já segue
        ("falhou", "substituida_pelo_robo"),
        ("falhou", "devolucao_motivo_sem_chamado"),  # o motivo não abre chamado
    ],
)
async def test_o_que_nao_conta_como_envio_falho(db, status, erro):
    await _catalogo(db)
    ch = await _chamado(db)
    msg = chamados_svc.nova_mensagem(ch, texto="x", tipo="abertura")
    msg.status = status
    msg.erro = erro
    db.add(msg)
    await db.flush()

    await vigia.registrar_resultado_envio(db, ch, msg)
    await db.commit()
    assert await _abertas(db) == []


async def test_agent_resultado_so_abre_quando_a_tarefa_falha_de_vez(db, client, monkeypatch):
    """O robô do Mac devolve o resultado: falha que volta pra fila não é
    ocorrência (a tarefa segue com ele); a que esgota as tentativas é."""
    from app.config import get_settings

    await _catalogo(db)
    token = "tok-vigia-chamados"  # noqa: S105
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    hdr = {"X-Agent-Token": token}

    ch = await _chamado(db, canal="robo", origem="logistica", chamado=None)
    chave = f"envio:{ch.id}"
    msg = chamados_svc.nova_mensagem(ch, texto="abrir", tipo="abertura", status="pendente")
    db.add(msg)
    await db.commit()
    mensagem_id = str(msg.id)

    for tentativa in (1, 2):
        r = await client.post(
            "/api/chamados/agent/resultado", headers=hdr,
            json={"mensagem_id": mensagem_id, "ok": False, "erro": f"timeout {tentativa}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pendente"
        assert await _abertas(db, "envio:") == []

    r = await client.post(
        "/api/chamados/agent/resultado", headers=hdr,
        json={"mensagem_id": mensagem_id, "ok": False, "erro": "timeout 3"},
    )
    assert r.status_code == 200 and r.json()["status"] == "falhou"
    o = await _linha(db, chave)
    assert o is not None and o.fechada_em is None
    assert o.titulo.startswith("Abertura não enviada")
    assert o.dados["canal"] == "robo" and o.dados["tentativas"] == 3

    r = await client.post(
        "/api/chamados/agent/resultado", headers=hdr,
        json={"mensagem_id": mensagem_id, "ok": True, "chamado": "PROTO-9"},
    )
    assert r.status_code == 200
    db.expire_all()
    o = await _linha(db, chave)
    assert o.fechada_em is not None and o.fechamento == "sumiu"


async def test_hook_desligado_nao_grava_e_sem_catalogo_nao_derruba_o_envio(db, monkeypatch):
    ch = await _chamado(db)

    # 1) sem a linha do robô (deploy antes de o worker sincronizar o catálogo):
    #    o envio passa e nada é gravado — a FK estouraria a transação da pessoa.
    _falha_ml(monkeypatch, RuntimeError("500 do ML"))
    msg = await _replicar(db, ch)
    assert msg.status == "falhou"
    assert (await db.execute(select(OuvidoriaOcorrencia))).scalars().all() == []

    # 2) com a linha, mas desligado
    robo = await _catalogo(db)
    robo.modo = "desligado"
    await db.commit()
    msg = await _replicar(db, ch)
    assert msg.status == "falhou"
    assert await _abertas(db) == []

    # 3) silencioso (o padrão de nascença) registra
    robo.modo = "silencioso"
    await db.commit()
    await _replicar(db, ch)
    assert len(await _abertas(db, "envio:")) == 1


# ─── hook da consulta ──────────────────────────────────────────────────────


class _ClaimAberto:
    async def get_claim(self, claim_id: str) -> dict:
        return {"status": "opened", "stage": "claim"}


async def _monitorar(db: AsyncSession, monkeypatch, *, erro: Exception | None) -> None:
    """Uma passada do monitor do ML (`run_replica_automatica`). O resolvedor de
    conta dele é o `_ml_client_para` (pelo nome da integração) — o monitor NÃO
    usa o resolvedor completo, pra não passar a encerrar sozinho chamado de
    devolução que ele nunca leu."""

    async def _client(session, conta):
        if erro is not None:
            raise erro
        return _ClaimAberto()

    monkeypatch.setattr(chamados_svc, "_ml_client_para", _client)
    await chamados_svc.run_replica_automatica(db)


async def test_consulta_escala_de_info_ate_pessoa_e_o_sucesso_fecha(db, monkeypatch):
    robo = await _catalogo(db)
    robo.config = {**robo.config, "consultas_falhas_seguidas": 2}
    await db.commit()
    ch = await _chamado(db)

    await _monitorar(db, monkeypatch, erro=RuntimeError("403 do ML"))
    o = await _linha(db, f"consulta:{ch.id}")
    assert o is not None and o.severidade == "info" and o.precisa_pessoa is False
    assert o.titulo == "Consulta do caso falhou (1×) — Mercado Livre 300001"
    assert o.dados["falhas_seguidas"] == 1 and o.dados["varredura"] == "monitor_ml"
    assert "403 do ML" in (o.detalhe or "")

    await _monitorar(db, monkeypatch, erro=RuntimeError("403 do ML"))
    await db.refresh(o)
    assert o.severidade == "baixa" and o.precisa_pessoa is True
    assert o.titulo == "Caso sem consulta há 2 passadas — Mercado Livre 300001"

    for _ in range(3):
        await _monitorar(db, monkeypatch, erro=RuntimeError("403 do ML"))
    await db.refresh(o)
    assert o.dados["falhas_seguidas"] == 5 and o.severidade == "pessoa"

    # leu de novo → fecha, e a falha seguinte começa do 1 (linha nova)
    await _monitorar(db, monkeypatch, erro=None)
    await db.refresh(o)
    assert o.fechada_em is not None and o.fechamento == "sumiu"

    await _monitorar(db, monkeypatch, erro=RuntimeError("403 do ML"))
    nova = await _linha(db, f"consulta:{ch.id}")
    assert nova.id != o.id and nova.dados["falhas_seguidas"] == 1


async def test_chamado_sem_integracao_ml_nao_vira_ocorrencia_de_consulta(db, monkeypatch):
    """O chamado de devolução do ML guarda a conta como NOME DA LOJA ("ML
    Aguiar"), que não casa com o nome da integração: o monitor sempre falhou
    nesses e quem os acompanha é o `_sync_ml` das devoluções. Abrir `consulta:`
    aqui seria uma linha piscando no painel a cada passada — o sync a fecharia
    minutos depois."""
    await _catalogo(db)
    ch = await _chamado(db)

    await _monitorar(
        db, monkeypatch, erro=chamados_svc.ChamadoError("chamado_sem_integracao_ml")
    )

    assert await _linha(db, f"consulta:{ch.id}") is None
    # Outro erro qualquer, sim, abre.
    await _monitorar(db, monkeypatch, erro=RuntimeError("403 do ML"))
    assert await _linha(db, f"consulta:{ch.id}") is not None


async def test_passada_resolve_o_robo_uma_vez_e_o_caminho_feliz_nao_toca_no_banco(
    db, monkeypatch
):
    """A `Passada` é o que evita ~4 idas ao banco por chamado nas varreduras de
    hora em hora: o robô e as `consulta:` abertas são lidos UMA vez."""
    await _catalogo(db)
    ch = await _chamado(db)

    passada = await vigia.abrir_passada(db)
    assert passada.robo is not None and passada.abertas == set()

    # Caminho feliz sem nada aberto: sai sem abrir savepoint nem consultar.
    async def _nunca(*_a, **_k):
        raise AssertionError("não devia tocar no banco")

    monkeypatch.setattr(vigia.ouvidoria, "fechar_por_chave", _nunca)
    await vigia.consulta_ok(db, ch, passada=passada)
    monkeypatch.undo()

    # Depois de uma falha, a chave entra na passada e o sucesso fecha de fato.
    await vigia.registrar_falha_consulta(
        db, ch, plat="ml", erro="403", varredura="monitor_ml", passada=passada
    )
    await db.commit()
    assert passada.abertas == {f"consulta:{ch.id}"}
    await vigia.consulta_ok(db, ch, passada=passada)
    await db.commit()
    assert passada.abertas == set()
    o = await _linha(db, f"consulta:{ch.id}")
    assert o.fechada_em is not None and o.fechamento == "sumiu"


async def test_passada_com_robo_desligado_nao_grava_nada(db):
    robo = await _catalogo(db)
    robo.modo = "desligado"
    await db.commit()
    ch = await _chamado(db)

    passada = await vigia.abrir_passada(db)
    assert passada.robo is None

    await vigia.registrar_falha_consulta(
        db, ch, plat="ml", erro="403", varredura="monitor_ml", passada=passada
    )
    await vigia.consulta_ok(db, ch, passada=passada)
    await db.commit()

    assert await _abertas(db) == []


async def test_sync_das_devolucoes_tambem_abre_e_fecha_a_consulta(db, monkeypatch):
    """O outro lado do `consulta:`: a varredura das devoluções
    (`sync_respostas`), que cobre Shopee/TikTok além do ML."""
    from app.services import chamados_devolucao_sync as sync

    await _catalogo(db)
    ch = await _chamado(db, plataforma="Shopee", pedido_bling="300010", chamado="SP-CASE-1")
    chave = f"consulta:{ch.id}"
    db.add(chamados_svc.nova_mensagem(ch, texto="contestação", tipo="abertura", status="enviada"))
    await db.commit()

    async def _falha(session, chamado, dev):
        raise RuntimeError("Shopee 500")

    monkeypatch.setattr(sync, "_sync_shopee", _falha)
    out = await sync.sync_respostas(db)
    assert out["falhas"] == 1
    o = await _linha(db, chave)
    assert o is not None and o.dados["varredura"] == "sync_respostas"
    assert o.plataforma == "shopee" and o.severidade == "info"

    async def _ok(session, chamado, dev):
        return 0

    monkeypatch.setattr(sync, "_sync_shopee", _ok)
    await sync.sync_respostas(db)
    await db.refresh(o)
    assert o.fechada_em is not None and o.fechamento == "sumiu"


async def test_prova_da_shopee_tem_titulo_proprio(db):
    """A prova adicional é uma `replica` no histórico, mas no painel a
    operação precisa ler "Prova não enviada" — não "Réplica"."""
    await _catalogo(db)
    ch = await _chamado(db, plataforma="Shopee", pedido_bling="300011")
    msg = chamados_svc.nova_mensagem(
        ch, texto="Prova adicional enviada à Shopee (2 foto(s)): olha a etiqueta", tipo="replica"
    )
    msg.status = "falhou"
    msg.erro = "upload recusado"
    db.add(msg)
    await db.flush()

    await vigia.registrar_resultado_envio(db, ch, msg)
    await db.commit()
    o = await _linha(db, f"envio:{ch.id}")
    assert o.titulo == "Prova não enviada — Shopee 300011"


# ─── rodada: Encerrado parado ──────────────────────────────────────────────


async def _encerrado(db: AsyncSession, *, dias: float, **kw) -> Chamado:
    ch = await _chamado(db, **kw)
    ch.status_plataforma = chamados_svc.STATUS_PERDEMOS
    ch.status_plataforma_at = datetime.now(UTC) - timedelta(days=dias)
    await db.commit()
    return ch


async def test_rodada_abre_encerrado_so_depois_do_prazo(db):
    await _catalogo(db)
    novo = await _encerrado(db, dias=1, pedido_bling="300002")
    velho = await _encerrado(db, dias=5, pedido_bling="300003")

    out = await vigia.vigia_chamados_run(db)
    assert out["encerrados"] == 1 and out["novas"] == 1
    assert await _linha(db, f"encerrado:{novo.id}") is None
    o = await _linha(db, f"encerrado:{velho.id}")
    assert o is not None and o.fechada_em is None
    assert o.titulo == "Encerrado há 5 dias esperando o resolver"
    assert o.severidade == "baixa" and o.precisa_pessoa is True
    assert o.acao == vigia.ACAO_ENCERRADO and o.link == "/chamados?search=300003"
    assert "perdemos" in (o.detalhe or "")
    assert o.dados["status_plataforma"] == chamados_svc.STATUS_PERDEMOS
    assert o.dados["dias"] == 5 and o.dados["protocolo"] == "5100000001"

    # rodada seguinte: a mesma linha, agora só re-vista
    out = await vigia.vigia_chamados_run(db)
    assert out["encerrados"] == 1 and out["novas"] == 0 and out["persistem"] == 1


async def test_rodada_nao_cobra_resolvido_nem_chamado_com_instrucao_pendente(db):
    await _catalogo(db)
    concluido = await _encerrado(db, dias=5, pedido_bling="300004")
    concluido.resolvido = True
    com_instrucao = await _encerrado(db, dias=5, pedido_bling="300005")
    db.add(
        chamados_svc.nova_mensagem(
            com_instrucao, texto="responde que o pacote foi entregue", tipo="instrucao",
            direcao="sistema",
        )
    )
    await db.commit()

    out = await vigia.vigia_chamados_run(db)
    assert out["encerrados"] == 0 and out["com_instrucao"] == 1
    assert await _abertas(db, "encerrado:") == []

    # o robô analisou (a instrução deixou de estar pendente) → volta a ser cobrado
    db.add(
        chamados_svc.nova_mensagem(
            com_instrucao, texto="analisei", tipo="analise", direcao="sistema"
        )
    )
    await db.commit()
    out = await vigia.vigia_chamados_run(db)
    assert out["encerrados"] == 1 and out["com_instrucao"] == 0


async def test_rodada_fecha_encerrado_quando_a_pessoa_conclui(db):
    await _catalogo(db)
    ch = await _encerrado(db, dias=4)
    await vigia.vigia_chamados_run(db)
    o = await _linha(db, f"encerrado:{ch.id}")
    assert o.fechada_em is None

    ch.resolvido = True
    ch.resolvido_at = datetime.now(UTC)
    await db.commit()
    out = await vigia.vigia_chamados_run(db)
    assert out["sumiram"] == 1
    await db.refresh(o)
    assert o.fechada_em is not None and o.fechamento == "sumiu"


# ─── rodada: reconciliação das ocorrências de hook ─────────────────────────


async def test_rodada_nao_mata_envio_e_consulta_mas_reconcilia(db, monkeypatch):
    await _catalogo(db)
    ch_envio = await _chamado(db, pedido_bling="300006")
    ch_consulta = await _chamado(db, pedido_bling="300007")

    _falha_ml(monkeypatch, RuntimeError("500 do ML"))
    await _replicar(db, ch_envio)
    await vigia.registrar_falha_consulta(
        db, ch_consulta, plat="ml", erro="timeout", varredura="sync_respostas"
    )
    await db.commit()
    assert len(await _abertas(db, "envio:")) == 1
    assert len(await _abertas(db, "consulta:")) == 1

    # a rodada não vê nenhuma das duas e MESMO ASSIM elas ficam de pé
    out = await vigia.vigia_chamados_run(db)
    assert out["sumiram"] == 0 and out["reconciliadas"] == 0
    assert out["envios_falhos"] == 1 and out["consultas_falhando"] == 1

    # o problema some por fora do envio: a abertura volta pra fila do robô
    # (garantir_chamado) e o outro chamado é concluído
    msg = (
        await db.execute(
            select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch_envio.id)
        )
    ).scalars().first()
    msg.status = "pendente"
    ch_consulta.resolvido = True
    await db.commit()

    out = await vigia.vigia_chamados_run(db)
    assert out["reconciliadas"] == 2
    assert out["envios_falhos"] == 0 and out["consultas_falhando"] == 0


async def test_reconciliacao_fecha_quando_o_chamado_foi_excluido(db, monkeypatch):
    await _catalogo(db)
    ch = await _chamado(db)
    _falha_ml(monkeypatch, RuntimeError("500 do ML"))
    await _replicar(db, ch)
    assert len(await _abertas(db, "envio:")) == 1

    await db.delete(ch)
    await db.commit()
    out = await vigia.vigia_chamados_run(db)
    assert out["reconciliadas"] == 1 and out["envios_falhos"] == 0


async def test_resumo_da_rodada(db, monkeypatch):
    await _catalogo(db)
    await _encerrado(db, dias=6, pedido_bling="300008")
    ch = await _chamado(db, pedido_bling="300009")
    _falha_ml(monkeypatch, RuntimeError("500 do ML"))
    await _replicar(db, ch)

    out = await vigia.vigia_chamados_run(db)
    assert out["resumo"] == "1 encerrado parado · 1 envio falho · 0 consultas falhando"
    rodadas = list((await db.execute(select(OuvidoriaRodada))).scalars())
    assert len(rodadas) == 1 and rodadas[0].ok is True
    assert rodadas[0].resumo == out["resumo"]


async def test_tick_do_worker_sai_quando_desligado(db, monkeypatch):
    from app import worker

    chamadas: list[int] = []

    async def _sweep() -> dict:
        chamadas.append(1)
        return {}

    monkeypatch.setattr("app.services.vigia_chamados.vigia_chamados_sweep", _sweep)
    robo = await _catalogo(db)
    robo.modo = "desligado"
    await db.commit()
    await worker.vigia_chamados_tick({})
    assert chamadas == []

    for modo in ("silencioso", "ligado"):
        robo.modo = modo
        await db.commit()
        await worker.vigia_chamados_tick({})
    assert chamadas == [1, 1]
