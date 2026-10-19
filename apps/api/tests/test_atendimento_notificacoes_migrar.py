"""Migração verificada: prévia não escreve, IDs completos e epoch idempotente."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update

from app import db as app_db
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
)
from app.services.atendimento import gravar, pendente
from app.services.atendimento import migrar_notificacoes as mig
from scripts import atendimento_notificacoes_migrar as cli

AGORA = datetime.now(UTC).replace(microsecond=0)
CORTE = AGORA - timedelta(minutes=10)


@pytest.fixture(autouse=True)
def _conta_sem_segredos(monkeypatch):
    monkeypatch.setattr(mig, "decrypt_json", lambda _: {"shop_id": 111, "access_token": "SEGREDO"})


async def _mundo(db, make_user):
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name="Loja de teste",
        credentials=b"somente-teste",
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo="observar", status="ok"
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _conversa(db, integ, canal, externo, *, afiliado=False, vazia=False):
    c, _ = await gravar.upsert_conversa(
        db,
        integration=integ,
        canal=canal,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=externo,
        comprador_id=f"comprador:{externo}",
        dados={"afiliado": True} if afiliado else {"campo_preservado": 1},
    )
    ms = []
    if not vazia:
        for i in range(2):
            m = AtendimentoMensagem(
                conversa_id=c.id,
                externo_id=f"{externo}:opaco:{'z' if i == 0 else 'a'}",
                autor="cliente",
                origem="cliente",
                texto="Pergunta não deve aparecer em relatório",
                enviada_em=CORTE - timedelta(minutes=1 - i),
                created_at=CORTE - timedelta(minutes=1 - i) + timedelta(seconds=1),
            )
            db.add(m)
            ms.append(m)
    await db.flush()
    await gravar.recalcular_conversa(db, c)
    await db.commit()
    await db.refresh(c)
    return c, ms


def _declarar(c, ms, *, nao_lidas=None):
    return {
        "externo_id": c.externo_id,
        "comprador_id": c.comprador_id,
        "ultima_mensagem_id": ms[-1].externo_id if ms else None,
        "sem_mensagens": not ms,
        "corte_em": CORTE.isoformat(),
        "nao_lidas_ids": [m.externo_id for m in ms] if nao_lidas is None else nao_lidas,
        "observadas_limite_ids": [m.externo_id for m in ms if m.enviada_em == CORTE],
    }


def _manifesto(
    tmp_path, integ_id, canal_id, declaradas, *, business="comprador", nome="snapshot.json"
):
    prova = tmp_path / "evidencia.json"
    prova.write_text('{"fonte":"fixture verificada"}', encoding="utf-8")
    dados = {
        "versao": 1,
        "snapshot_id": str(uuid4()),
        "origem": "duoke",
        "capturado_em": (AGORA - timedelta(minutes=5)).isoformat(),
        "evidencia": {
            "arquivo": prova.name,
            "sha256": hashlib.sha256(prova.read_bytes()).hexdigest(),
            "lista_completa": True,
            "corte_conferido": True,
            "operacao_pausada": True,
        },
        "escopos": [
            {
                "canal_id": str(canal_id),
                "integration_id": str(integ_id),
                "plataforma": "shopee",
                "canal": "chat",
                "conta_externa": "111",
                "business_type": business,
                "corte_em": CORTE.isoformat(),
                "evidencia_corte": "Fixture: limite conferido por IDs em todos os empates.",
                "total_nao_lidas": sum(len(c["nao_lidas_ids"]) for c in declaradas),
                "conversas": declaradas,
            }
        ],
    }
    arquivo = tmp_path / nome
    arquivo.write_text(json.dumps(dados), encoding="utf-8")
    return arquivo, dados


def _reescrever(arquivo, dados):
    arquivo.write_text(json.dumps(dados), encoding="utf-8")


@pytest.mark.parametrize(
    "caso,code",
    [
        ("sem_ids", "total"),
        ("parcial", "cobertura"),
        ("corte_nao_conferido", "cobertura"),
        ("operando", "cobertura"),
        ("sha_errado", "evidencia"),
        ("path_fora", "evidencia"),
        ("futuro", "futuro"),
        ("data_ingenua", "formato"),
        ("id_numero", "formato"),
        ("id_duplicado", "duplicado"),
        ("chave_desconhecida", "formato"),
        ("scope_duplicado", "duplicado"),
    ],
)
def test_arquivo_recusa_fonte_insuficiente(tmp_path, caso, code):
    declarada = {
        "externo_id": "opaque:conversation",
        "comprador_id": "buyer:1",
        "ultima_mensagem_id": "opaque:last",
        "sem_mensagens": False,
        "corte_em": CORTE.isoformat(),
        "nao_lidas_ids": ["opaque:last"],
        "observadas_limite_ids": ["opaque:last"],
    }
    arquivo, dados = _manifesto(tmp_path, uuid4(), uuid4(), [declarada])
    if caso == "sem_ids":
        dados["escopos"][0]["conversas"][0]["nao_lidas_ids"] = []
    elif caso == "parcial":
        dados["evidencia"]["lista_completa"] = False
    elif caso == "corte_nao_conferido":
        dados["evidencia"]["corte_conferido"] = False
    elif caso == "operando":
        dados["evidencia"]["operacao_pausada"] = False
    elif caso == "sha_errado":
        dados["evidencia"]["sha256"] = "0" * 64
    elif caso == "path_fora":
        dados["evidencia"]["arquivo"] = "../evidencia.json"
    elif caso == "futuro":
        dados["capturado_em"] = (AGORA + timedelta(days=1)).isoformat()
    elif caso == "data_ingenua":
        dados["capturado_em"] = "2026-10-19T11:00:00"
    elif caso == "id_numero":
        dados["escopos"][0]["conversas"][0]["nao_lidas_ids"] = [123]
    elif caso == "id_duplicado":
        dados["escopos"][0]["conversas"][0]["nao_lidas_ids"] *= 2
    elif caso == "chave_desconhecida":
        dados["pular_validacao"] = True
    elif caso == "scope_duplicado":
        dados["escopos"] *= 2
    _reescrever(arquivo, dados)
    with pytest.raises(mig.MigracaoRecusada) as erro:
        mig.ler_snapshot(arquivo, agora=AGORA)
    assert erro.value.code == code


async def test_previa_readonly_aplicar_idempotente_e_preservar_fila(db, make_user, tmp_path):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    c.ia_pausada = True
    c.visto_em = AGORA - timedelta(minutes=1)  # epoch nova não altera este campo
    await db.commit()
    await db.refresh(c)
    antes = {
        k: getattr(c, k)
        for k in (
            "dados",
            "visto_em",
            "aguardando_resposta",
            "prazo_resposta_em",
            "situacao",
            "sem_resposta_necessaria",
            "ia_pausada",
            "updated_at",
        )
    }
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, ms)])
    snapshot = mig.ler_snapshot(arquivo)
    await db.commit()
    await db.execute(text("SET TRANSACTION READ ONLY"))
    previa = await mig.migrar(db, snapshot, agora=AGORA)
    await db.rollback()
    assert previa["escopos"][0]["numero_depois"] == 2
    assert previa["aplicado"] is False
    await db.refresh(c)
    assert c.notificacoes_corte_em is None
    await mig.migrar(db, snapshot, aplicar=True, hash_previsto=previa["hash"], agora=AGORA)
    await db.commit()
    await db.refresh(c)
    await db.refresh(canal)
    assert c.notificacoes_corte_em == CORTE
    assert c.notificacoes_iniciais["origem"] == "duoke_snapshot"
    assert c.notificacoes_iniciais["nao_lidas_ids"] == sorted(m.externo_id for m in ms)
    assert c.notificacoes_visto_em is None
    assert canal.notificacoes_migracao["comprador"]["hash"] == snapshot.hash
    for campo, valor in antes.items():
        assert getattr(c, campo) == valor, campo
    # Uma leitura posterior não pode ser apagada por executar o mesmo arquivo outra vez.
    c.notificacoes_visto_em = AGORA
    await db.commit()
    repetida = await mig.migrar(
        db, snapshot, aplicar=True, hash_previsto=previa["hash"], agora=AGORA
    )
    await db.commit()
    await db.refresh(c)
    assert repetida["escopos"][0]["ja_aplicado"] is True
    assert c.notificacoes_visto_em == AGORA
    assert repetida["escopos"][0]["numero_depois"] == 0


@pytest.mark.parametrize(
    "caso,code",
    [
        ("canal_errado", "identidade"),
        ("conta_errada", "conta"),
        ("comprador_errado", "comprador"),
        ("ancora_errada", "ancora"),
        ("empate_incompleto", "limite"),
        ("id_outra_conversa", "mensagem"),
        ("conversa_omitida", "cobertura"),
        ("zero_sem_ancoras", "cobertura"),
    ],
)
async def test_recusa_identidade_cobertura_e_limite(db, make_user, tmp_path, caso, code):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    outro, outros_ms = await _conversa(db, integ, canal, "dois")
    arquivo, dados = _manifesto(
        tmp_path, integ.id, canal.id, [_declarar(c, ms), _declarar(outro, outros_ms, nao_lidas=[])]
    )
    escopo = dados["escopos"][0]
    if caso == "canal_errado":
        escopo["integration_id"] = str(uuid4())
    elif caso == "conta_errada":
        escopo["conta_externa"] = "outra"
    elif caso == "comprador_errado":
        escopo["conversas"][0]["comprador_id"] = "outro"
    elif caso == "ancora_errada":
        escopo["conversas"][0]["ultima_mensagem_id"] = "nao-existe"
    elif caso == "empate_incompleto":
        escopo["conversas"][0]["observadas_limite_ids"] = []
    elif caso == "id_outra_conversa":
        escopo["conversas"][0]["nao_lidas_ids"] = [outros_ms[0].externo_id]
        escopo["total_nao_lidas"] = 1
    elif caso == "conversa_omitida":
        escopo["conversas"].pop()
    elif caso == "zero_sem_ancoras":
        escopo["conversas"] = []
        escopo["total_nao_lidas"] = 0
    _reescrever(arquivo, dados)
    with pytest.raises(mig.MigracaoRecusada) as erro:
        await mig.migrar(db, mig.ler_snapshot(arquivo), agora=AGORA)
    assert erro.value.code == code
    await db.rollback()
    await db.refresh(c)
    await db.refresh(canal)
    assert c.notificacoes_corte_em is None
    assert canal.notificacoes_migracao == {}


async def test_leitura_entre_previa_e_apply_recusa_sem_reiniciar_epoch(db, make_user, tmp_path):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, ms)])
    snapshot = mig.ler_snapshot(arquivo)
    previa = await mig.migrar(db, snapshot, agora=AGORA)
    c.visto_em = AGORA
    await db.commit()
    with pytest.raises(mig.MigracaoRecusada, match="hash_previa"):
        await mig.migrar(db, snapshot, aplicar=True, hash_previsto=previa["hash"], agora=AGORA)
    await db.rollback()
    await db.refresh(c)
    assert c.notificacoes_corte_em is None
    assert c.visto_em == AGORA


async def test_nova_conversa_entre_previa_e_apply_nao_e_zerada(db, make_user, tmp_path):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, ms)])
    snapshot = mig.ler_snapshot(arquivo)
    previa = await mig.migrar(db, snapshot)
    nova, _ = await _conversa(db, integ, canal, "nova")
    with pytest.raises(mig.MigracaoRecusada, match="cobertura"):
        await mig.migrar(db, snapshot, aplicar=True, hash_previsto=previa["hash"])
    await db.rollback()
    await db.refresh(nova)
    assert nova.notificacoes_corte_em is None


async def test_escopo_afiliado_isolado_e_mensagem_nova_conta(db, make_user, tmp_path):
    integ, canal = await _mundo(db, make_user)
    normal, normal_ms = await _conversa(db, integ, canal, "normal")
    afiliado, afi_ms = await _conversa(db, integ, canal, "afiliado", afiliado=True)
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(normal, normal_ms)])
    snapshot = mig.ler_snapshot(arquivo)
    previa = await mig.migrar(db, snapshot)
    # Import atrasado: novas mensagens posteriores ao corte continuam contando.
    nova = AtendimentoMensagem(
        conversa_id=normal.id,
        externo_id="nova:opaca",
        autor="cliente",
        origem="cliente",
        enviada_em=CORTE + timedelta(minutes=1),
        created_at=AGORA,
    )
    db.add(nova)
    await db.flush()
    await gravar.recalcular_conversa(db, normal)
    await db.commit()
    await mig.migrar(db, snapshot, aplicar=True, hash_previsto=previa["hash"])
    await db.commit()
    await db.refresh(normal)
    await db.refresh(afiliado)
    assert pendente.nao_lidas_duoke(normal, [*normal_ms, nova], agora=AGORA) == 3
    assert afiliado.notificacoes_corte_em is None
    assert len(afi_ms) == 2
    # Outra geração não pode sobrescrever o estado aplicado.
    dados = json.loads(arquivo.read_text())
    dados["snapshot_id"] = str(uuid4())
    _reescrever(arquivo, dados)
    with pytest.raises(mig.MigracaoRecusada, match="ja_migrado"):
        await mig.migrar(db, mig.ler_snapshot(arquivo))


async def test_conversa_vazia_exige_declaracao_e_nenhuma_mensagem(db, make_user, tmp_path):
    integ, canal = await _mundo(db, make_user)
    c, _ = await _conversa(db, integ, canal, "vazia", vazia=True)
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, [])])
    snapshot = mig.ler_snapshot(arquivo)
    previa = await mig.migrar(db, snapshot)
    await mig.migrar(db, snapshot, aplicar=True, hash_previsto=previa["hash"])
    await db.commit()
    await db.refresh(c)
    assert c.notificacoes_corte_em == CORTE
    assert c.notificacoes_iniciais["nao_lidas_ids"] == []


async def test_cli_default_readonly_e_relatorio_privado(db, make_user, tmp_path, capsys):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, ms)])
    relatorio = tmp_path / "previa.json"
    assert await cli.executar([str(arquivo), "--relatorio", str(relatorio)]) == 0
    stdout = capsys.readouterr().out
    dados = json.loads(stdout)
    assert dados["aplicado"] is False
    assert "Pergunta" not in stdout and "SEGREDO" not in stdout and "comprador:" not in stdout
    assert relatorio.stat().st_mode & 0o777 == 0o600
    await db.refresh(c)
    assert c.notificacoes_corte_em is None


async def test_manifesto_com_dois_escopos_preserva_os_dois_perfis(db, make_user, tmp_path):
    integ, canal = await _mundo(db, make_user)
    normal, normal_ms = await _conversa(db, integ, canal, "normal")
    afiliado, afiliado_ms = await _conversa(db, integ, canal, "afiliado", afiliado=True)
    arquivo, dados = _manifesto(tmp_path, integ.id, canal.id, [_declarar(normal, normal_ms)])
    outro = copy.deepcopy(dados["escopos"][0])
    outro.update(business_type="afiliado", conversas=[_declarar(afiliado, afiliado_ms)])
    dados["escopos"].append(outro)
    _reescrever(arquivo, dados)
    snapshot = mig.ler_snapshot(arquivo)
    previa = await mig.migrar(db, snapshot)
    await mig.migrar(db, snapshot, aplicar=True, hash_previsto=previa["hash"])
    await db.commit()
    await db.refresh(canal)
    assert set(canal.notificacoes_migracao) == {"comprador", "afiliado"}
    assert {p["hash"] for p in canal.notificacoes_migracao.values()} == {snapshot.hash}


async def test_apply_exclusivo_bloqueia_criacao_ate_perfil_ser_publicado(db, make_user, tmp_path):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, ms)])
    snapshot = mig.ler_snapshot(arquivo)
    previa = await mig.migrar(db, snapshot)
    await mig.migrar(db, snapshot, aplicar=True, hash_previsto=previa["hash"])
    # O serviço deixa a trava exclusiva até o COMMIT do chamador.
    iniciou = asyncio.Event()
    pid = None

    async def criar_em_outra_sessao():
        nonlocal pid
        async with app_db.SessionLocal() as outra:
            i = await outra.get(Integration, integ.id)
            k = await outra.get(AtendimentoCanal, canal.id)
            pid = await outra.scalar(select(func.pg_backend_pid()))
            iniciou.set()
            nova, _ = await gravar.upsert_conversa(
                outra,
                integration=i,
                canal=k,
                plataforma="shopee",
                canal_nome="chat",
                externo_id="criada-durante-apply",
                comprador_id="novo-comprador",
            )
            await outra.commit()
            return nova.notificacoes_corte_em, nova.notificacoes_iniciais

    tarefa = asyncio.create_task(criar_em_outra_sessao())
    try:
        await asyncio.wait_for(iniciou.wait(), timeout=5)
        # Prova o bloqueio no PostgreSQL; não é uma inferência por tempo ou
        # uma chamada falsa ao helper de locks.
        esperando = False
        for _ in range(30):
            esperando = bool(
                await db.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid=:pid AND locktype='advisory' AND NOT granted)"
                    ),
                    {"pid": pid},
                )
            )
            if esperando:
                break
            await asyncio.sleep(0.05)
        assert esperando and not tarefa.done()
        await db.commit()
        corte_novo, iniciais = await asyncio.wait_for(tarefa, timeout=5)
        assert corte_novo == CORTE
        assert iniciais["snapshot_id"] == str(snapshot.snapshot_id)
        assert iniciais["hash"] == snapshot.hash
        assert iniciais["nao_lidas_ids"] == []
    finally:
        await db.rollback()
        if not tarefa.done():
            tarefa.cancel()
        await asyncio.gather(tarefa, return_exceptions=True)


async def test_conversa_em_uso_recusa_apply_sem_deadlock_nem_import_parcial(
    db, make_user, tmp_path
):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, ms)])
    snapshot = mig.ler_snapshot(arquivo)
    previa = await mig.migrar(db, snapshot)
    await db.execute(
        update(AtendimentoConversa)
        .where(AtendimentoConversa.id == c.id)
        .values(ultima_mensagem_resumo="Writer mantém esta linha travada.")
    )
    # Simula sync que já atualizou uma conversa e pode criar outra. O
    # importador não pode segurar exclusiva esperando esta linha.
    async with app_db.SessionLocal() as outra:
        with pytest.raises(mig.MigracaoRecusada, match="escopo_ocupado"):
            await asyncio.wait_for(
                mig.migrar(outra, snapshot, aplicar=True, hash_previsto=previa["hash"]), timeout=5
            )
        await outra.rollback()
    nova, _ = await asyncio.wait_for(
        gravar.upsert_conversa(
            db,
            integration=integ,
            canal=canal,
            plataforma="shopee",
            canal_nome="chat",
            externo_id="writer-segue-criando",
            comprador_id="novo-buyer",
        ),
        timeout=5,
    )
    await db.commit()
    await db.refresh(c)
    await db.refresh(canal)
    assert c.notificacoes_corte_em is None
    assert nova.notificacoes_corte_em is None
    assert canal.notificacoes_migracao == {}


async def test_sync_com_shared_em_voo_recusa_apply_imediatamente(db, make_user, tmp_path):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, ms)])
    snapshot = mig.ler_snapshot(arquivo)
    previa = await mig.migrar(db, snapshot)
    # Uma criação real segura a shared até seu COMMIT.
    await gravar.upsert_conversa(
        db,
        integration=integ,
        canal=canal,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="sync-nao-commitado",
        comprador_id="novo-buyer",
    )
    async with app_db.SessionLocal() as outra:
        with pytest.raises(mig.MigracaoRecusada, match="escopo_ocupado"):
            await asyncio.wait_for(
                mig.migrar(outra, snapshot, aplicar=True, hash_previsto=previa["hash"]), timeout=5
            )
        await outra.rollback()
    await db.commit()
    await db.refresh(c)
    await db.refresh(canal)
    assert c.notificacoes_corte_em is None
    assert canal.notificacoes_migracao == {}


@pytest.mark.parametrize("alvo", ["manifesto", "evidencia"])
async def test_relatorio_nao_substitui_artefatos_da_fonte(db, make_user, tmp_path, capsys, alvo):
    integ, canal = await _mundo(db, make_user)
    c, ms = await _conversa(db, integ, canal, "um")
    arquivo, _ = _manifesto(tmp_path, integ.id, canal.id, [_declarar(c, ms)])
    saida = arquivo if alvo == "manifesto" else tmp_path / "evidencia.json"
    antes = saida.read_bytes()
    assert await cli.executar([str(arquivo), "--relatorio", str(saida)]) == cli.SAIDA_RECUSADO
    assert json.loads(capsys.readouterr().err)["code"] == "relatorio"
    assert saida.read_bytes() == antes
