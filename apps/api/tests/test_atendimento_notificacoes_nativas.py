"""Leitura nativa após snapshot verificado, sem expiração de notificações.

Não consulta Duoke nem marketplaces. Os mesmos estados são contados por
SQL e Python sobre o banco isolado; a importação da fonte tem testes próprios.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, UserRole
from app.services.atendimento import gravar, lida, notificacoes, pendente
from tests.test_atendimento_router import _loja

AGORA = datetime.now(UTC).replace(microsecond=0)
CORTE = AGORA - timedelta(days=20)
MENU = "Olá, por favor selecione sua dúvida e logo te atenderemos: 1 - Entrega"


@pytest.fixture(autouse=True)
def _desligado(monkeypatch):
    settings = get_settings()
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_envio_ativo",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_alerta_telegram",
        "atendimento_simulador",
    ):
        monkeypatch.setattr(settings, nome, False)
    monkeypatch.setattr(settings, "atendimento_usuarios", "")


async def _conversa(db, make_user, *, corte=CORTE, seed=(), observadas=()):
    user = await make_user(role=UserRole.ADMIN)
    integ, canal = await _loja(db, user)
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=f"native:{uuid4()}",
        comprador_id="buyer",
    )
    conversa.notificacoes_corte_em = corte
    conversa.notificacoes_iniciais = {
        "versao": 1,
        "snapshot_id": str(uuid4()),
        "origem": "duoke_snapshot",
        "hash": "a" * 64,
        "nao_lidas_ids": list(seed),
        "observadas_limite_ids": list(observadas),
    }
    await db.commit()
    return user, integ, canal, conversa


async def _mensagem(db, conversa, id, momento, *, autor="cliente", chegada=None, **campos):
    mensagem = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=id,
        autor=autor,
        origem=campos.pop("origem", "externo"),
        tipo=campos.pop("tipo", "texto"),
        texto=campos.pop("texto", id),
        enviada_em=momento,
        created_at=chegada or momento,
        **campos,
    )
    db.add(mensagem)
    await db.flush()
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    return mensagem


async def _numero(db, conversa, esperado, *, agora=AGORA):
    await db.refresh(conversa)
    mensagens = list(
        (
            await db.scalars(
                select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa.id)
            )
        ).all()
    )
    assert pendente.nao_lidas_duoke(conversa, mensagens, agora=agora) == esperado
    assert (
        await db.scalar(
            select(pendente.nao_lidas_duoke_sql(agora=agora)).where(
                AtendimentoConversa.id == conversa.id
            )
        )
    ) == esperado
    return mensagens


async def test_seed_autoritario_ignora_legacy_sem_expirar_ou_retroimportar(db, make_user):
    _, _, _, c = await _conversa(db, make_user, seed=["seed"])
    await _mensagem(db, c, "seed", CORTE - timedelta(days=2))
    await _mensagem(db, c, "read-old", CORTE - timedelta(days=1))
    await _mensagem(db, c, "person-before", CORTE - timedelta(hours=1), autor="loja")
    await _mensagem(db, c, "menu", CORTE + timedelta(hours=1), autor="loja", texto=MENU)
    await _mensagem(db, c, "new", CORTE + timedelta(days=1))
    # O corte não é o horário da chegada: isso é uma importação histórica.
    await _mensagem(db, c, "late-history", CORTE - timedelta(days=3), chegada=AGORA)
    c.visto_em = AGORA
    c.nao_lidas = 0
    c.ia_pausada = True
    await db.commit()
    await _numero(db, c, 2)
    await _numero(db, c, 2, agora=AGORA + timedelta(days=500))
    assert c.aguardando_resposta and c.ia_pausada
    assert c.notificacoes_visto_em is None


async def test_limite_completo_ids_conhecidos_e_empatados_nao_ordenam_ids(db, make_user):
    _, _, _, c = await _conversa(
        db, make_user, seed=["a-seed"], observadas=["a-seed", "z-read", "person"]
    )
    await _mensagem(db, c, "a-seed", CORTE)
    await _mensagem(db, c, "z-read", CORTE)
    # Esta resposta já estava no snapshot: a fonte mantém a-seed não lida.
    await _mensagem(db, c, "person", CORTE, autor="loja")
    await _mensagem(db, c, "000-new-same-second", CORTE)
    await _numero(db, c, 2)


@pytest.mark.parametrize("origem", ["externo", "davinci_humano", "davinci_ia"])
async def test_resposta_real_posterior_baixa_anteriores_sem_apagar_empate(db, make_user, origem):
    _, _, _, c = await _conversa(db, make_user, seed=["seed"])
    await _mensagem(db, c, "seed", CORTE - timedelta(days=1))
    instante = CORTE + timedelta(days=1)
    await _mensagem(db, c, "reply", instante, autor="loja", origem=origem)
    await _numero(db, c, 0)
    # Não há sequência documentada para decidir a ordem destes IDs. O
    # comprador no mesmo segundo fica contado; a mensagem não desaparece.
    await _mensagem(db, c, "000-customer", instante)
    await _numero(db, c, 1)
    await _mensagem(db, c, "customer-next", instante + timedelta(seconds=1))
    await _numero(db, c, 2)


async def test_visto_epoch_atual_obrigatorio_e_mensagem_nova_reabre(db, make_user):
    user, _, _, c = await _conversa(db, make_user, seed=["seed"])
    seed = await _mensagem(db, c, "seed", CORTE - timedelta(days=1))
    c.visto_em = AGORA  # leituras da época anterior não consomem este seed.
    await db.commit()
    snapshot = c.notificacoes_iniciais["snapshot_id"]
    for invalido in (None, "", "not-a-uuid", str(uuid4())):
        await lida.gravar_visto(db, c, user, seed.created_at, notificacoes_migracao_id=invalido)
        await _numero(db, c, 1)
        assert c.notificacoes_visto_em is None
    await lida.gravar_visto(db, c, user, seed.created_at, notificacoes_migracao_id=snapshot)
    await _numero(db, c, 0)
    assert c.visto_em == AGORA
    assert c.notificacoes_visto_em == seed.created_at
    await _mensagem(db, c, "new", CORTE + timedelta(hours=1), chegada=AGORA)
    await _numero(db, c, 1)
    # Um retorno antigo da mesma epoch também nunca anda para trás.
    await lida.gravar_visto(db, c, user, AGORA, notificacoes_migracao_id=snapshot)
    await lida.gravar_visto(db, c, user, seed.created_at, notificacoes_migracao_id=snapshot)
    await _numero(db, c, 0)
    assert c.notificacoes_visto_em == AGORA


async def test_troca_de_epoch_nao_aceita_visto_antigo_e_zerar_explicitamente_funciona(
    db, make_user
):
    user, _, _, c = await _conversa(db, make_user, seed=["seed"])
    mensagem = await _mensagem(db, c, "seed", CORTE - timedelta(days=1))
    antiga = c.notificacoes_iniciais["snapshot_id"]
    c.notificacoes_iniciais = {**c.notificacoes_iniciais, "snapshot_id": str(uuid4())}
    await db.commit()
    await lida.gravar_visto(db, c, user, AGORA, notificacoes_migracao_id=antiga)
    await _numero(db, c, 1)
    assert await lida.zerar_nao_lidas(db, user, integration_id=c.integration_id) == 1
    await _numero(db, c, 0)
    assert c.notificacoes_visto_em == mensagem.created_at


async def test_futuros_herdam_perfil_fresco_sem_cursor_ou_dados_sobrescreverem(db, make_user):
    user, integ, canal, c = await _conversa(db, make_user, corte=None)
    perfil = {
        "versao": 1,
        "snapshot_id": str(uuid4()),
        "hash": "a" * 64,
        "origem": "duoke_snapshot",
        "corte_em": CORTE.isoformat(),
    }
    await db.execute(
        update(AtendimentoCanal)
        .where(AtendimentoCanal.id == canal.id)
        .values(
            notificacoes_migracao={"comprador": perfil},
            cursor={"desde": 1},
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert canal.notificacoes_migracao == {}  # objeto stale do worker
    nova, criada = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=f"new:{uuid4()}",
        dados={"produto": "a"},
    )
    assert criada and nova.notificacoes_corte_em == CORTE
    assert nova.notificacoes_iniciais["snapshot_id"] == perfil["snapshot_id"]
    assert nova.notificacoes_iniciais["nao_lidas_ids"] == []
    await _mensagem(db, nova, "history", CORTE - timedelta(seconds=1), chegada=AGORA)
    await _numero(db, nova, 0)
    await _mensagem(db, nova, "new", CORTE + timedelta(seconds=1), chegada=AGORA)
    await _numero(db, nova, 1)
    await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=nova.externo_id,
        dados={"produto": "b"},
    )
    await db.commit()
    await _numero(db, nova, 1)
    assert nova.notificacoes_corte_em == CORTE
    # Não vaza a herança para o chat de afiliado do mesmo canal.
    afiliada, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=f"affiliate:{uuid4()}",
        dados={"afiliado": True},
    )
    await db.commit()
    assert afiliada.notificacoes_corte_em is None
    assert c.notificacoes_corte_em is None


def test_perfis_invalidos_nao_inventam_fonte_e_chave_de_lock_estavel():
    for valor in (None, 3, "bad", "2026-10-19T13:00:00", "2026-02-31T13:00:00Z"):
        assert notificacoes.data_utc(valor) is None
    assert notificacoes.data_utc("2026-10-19T10:00:00-03:00") == datetime(
        2026, 10, 19, 13, tzinfo=UTC
    )
    assert notificacoes.chave_trava("abc") == "atendimento_notificacoes:abc"
    assert notificacoes.escopo("shopee", {"afiliado": True}) == "afiliado"
    assert notificacoes.escopo("tiktok", {"afiliado": True}) == "comprador"


@pytest.mark.parametrize(
    "alteracao",
    [
        {"versao": 2},
        {"versao": True},
        {"versao": 1.0},
        {"versao": "1"},
        {"snapshot_id": "unknown"},
        {"hash": "a" * 63},
        {"hash": "A" * 64},
        {"origem": "unknown"},
        {"nao_lidas_ids": None},
        {"nao_lidas_ids": "a"},
        {"nao_lidas_ids": ["a", 3]},
        {"nao_lidas_ids": [""]},
        {"nao_lidas_ids": ["a", "a"]},
        {"observadas_limite_ids": ["a" * 192]},
        {"observadas_limite_ids": {"a": True}},
    ],
)
async def test_estado_invalido_faz_fallback_legado_python_sql_sem_cast_que_falhe(
    db, make_user, alteracao
):
    _, _, _, c = await _conversa(db, make_user, corte=AGORA + timedelta(days=1))
    await _mensagem(db, c, "recent", AGORA - timedelta(minutes=1))
    await db.execute(
        update(AtendimentoConversa)
        .where(AtendimentoConversa.id == c.id)
        .values(notificacoes_iniciais={**c.notificacoes_iniciais, **alteracao})
    )
    await db.commit()
    await db.refresh(c)
    assert not notificacoes.estado_valido(c)
    assert not await db.scalar(
        select(notificacoes.estado_valido_sql()).where(AtendimentoConversa.id == c.id)
    )
    await _numero(db, c, 1)


async def test_gate_sql_puro_aceitam_id_opaco_sem_supor_ascii(db, make_user):
    id_ = "é\n" + "字" * 189
    _, _, _, c = await _conversa(db, make_user, seed=[id_])
    await _mensagem(db, c, id_, CORTE - timedelta(days=1))
    assert notificacoes.estado_valido(c)
    assert await db.scalar(
        select(notificacoes.estado_valido_sql()).where(AtendimentoConversa.id == c.id)
    )
    await _numero(db, c, 1)
