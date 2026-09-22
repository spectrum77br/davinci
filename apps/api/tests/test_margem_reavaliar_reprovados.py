"""Reavaliação dos reprovados pelo robô (services/margem_auto_hold.
reavaliar_reprovados) — caso 297400 (16/09/2026): reprovado às 14:17 com o
repasse provisório da Shopee (margem < 16%), horas depois o escrow subiu e a
margem final (17,1%) passava pela Condição Especial ≥16% do segmento — mas
nada revisitava um 'Reprovado'.

Cobre: quem é reavaliado (só reprovação DO ROBÔ, ainda em 83955, com mais de
1h30, sem decisão humana depois), a liberação como o Aprovar (Observações +
Atendido → Em aberto + espelhos + auditoria + Threema), o 'Pendente' quando a
margem passa mas o saldo diverge, e os casos em que NADA muda (margem ainda
baixa, sem margem, Bling recusa, pedido moveu no Bling, flag desligada).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import MargemAudit, Segment, SegmentSpecialDate, ThreemaInformarConfig
from app.services import margem_auto_hold, threema
from tests.test_margem_auto_hold import FakeBling, _audits, _seed_pedido, _snapshot

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 9, 16)
AGORA = datetime(2026, 9, 16, 12, 35, tzinfo=UTC)
DATA_PEDIDO = datetime(2026, 9, 15, 16, 44, tzinfo=UTC)


class FakeBlingComSituacao(FakeBling):
    """FakeBling do hold + situação atual do pedido no Bling (o robô só
    libera quem ainda está em Aguardando Cancelamento)."""

    def __init__(self, *a: object, situacao: int = 83955, **k: object) -> None:
        super().__init__(*a, **k)
        self.situacao = situacao

    async def get_order(self, bling_id: int) -> dict:
        order = await super().get_order(bling_id)
        order["situacao"] = {"id": self.situacao}
        return order


@pytest.fixture(autouse=True)
def _sem_refetch(monkeypatch):
    """O refetch real apagaria a linha semeada do snapshot (refresh_for_bling_id
    reconstrói a partir da view). Aqui o snapshot semeado JÁ É o "dado
    atualizado"; a chamada fica registrada pra provar que aconteceu."""
    chamadas: list[int] = []

    async def _fake(session: AsyncSession, bling_id: int) -> None:
        chamadas.append(bling_id)

    monkeypatch.setattr(margem_auto_hold, "_atualizar_financeiro", _fake)
    return chamadas


async def _reprovo_do_robo(
    db: AsyncSession, *, pedido: str, bling_id: int, ha: timedelta = timedelta(hours=2)
) -> None:
    """Pedido reprovado pelo robô há `ha`: situação 83955 + pino 'Reprovado'
    (já semeados) + as duas linhas de auditoria que _hold_one grava."""
    quando = AGORA - ha
    for acao, antigo, novo in (("situacao", "6", "83955"), ("status", None, "Reprovado")):
        db.add(
            MargemAudit(
                created_at=quando,
                pedido_bling=pedido,
                bling_id=str(bling_id),
                acao=acao,
                valor_antigo=antigo,
                valor_novo=novo,
                origem="margens_auto",
                mudado_por=None,
            )
        )
    await db.commit()


async def _seed_reprovado(
    db: AsyncSession,
    *,
    pedido: str,
    bling_id: int,
    margem: float,
    minima: float = 0.18,
    ha: timedelta = timedelta(hours=2),
    leaf_segment_id: object = None,
    produto: str | None = None,
    saldo_gap: bool = False,
    plataforma: str = "shopee",
    lucro: float | None = None,
    situacao: str = "83955",
) -> None:
    await _seed_pedido(
        db,
        pedido=pedido,
        bling_id=bling_id,
        situacao=situacao,
        status="Reprovado",
        margem=margem,
        minima=minima,
        plataforma=plataforma,
        loja="Shopee ATV",
        lucro=lucro,
        data=DATA_PEDIDO,
        leaf_segment_id=leaf_segment_id,
        saldo_gap=saldo_gap,
    )
    if produto:
        await db.execute(
            text("UPDATE verificar_margem SET produto = :p WHERE pedido_bling = :n"),
            {"p": produto, "n": pedido},
        )
        await db.commit()
    await _reprovo_do_robo(db, pedido=pedido, bling_id=bling_id, ha=ha)


async def _segmento_hotwav(db: AsyncSession) -> Segment:
    """Celular > Regular (mínima 18%) com a Condição Especial real do caso:
    14/09–21/09, nome contém 'hotwav', piso 16%."""
    seg = Segment(
        name="Regular", slug=f"regular-{uuid.uuid4().hex[:6]}", min_margin=Decimal("0.18")
    )
    db.add(seg)
    await db.flush()
    db.add(
        SegmentSpecialDate(
            segment_id=seg.id,
            date_start=date(2026, 9, 14),
            date_end=date(2026, 9, 21),
            nome_contem="hotwav",
            min_margin=Decimal("0.16"),
        )
    )
    await db.commit()
    return seg


async def _bling_order(db: AsyncSession, bling_id: int) -> tuple:
    return (
        await db.execute(
            text("SELECT situacao, status, aprovado_por FROM bling_orders WHERE bling_id = :b"),
            {"b": bling_id},
        )
    ).one()


async def _pessoa(db: AsyncSession):
    from app.models import User, UserRole, UserStatus

    u = User(
        open_id=f"t-{uuid.uuid4().hex[:8]}",
        email=f"t-{uuid.uuid4().hex[:6]}@hadken.com",
        name="Pessoa",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.flush()
    return u


def _threema_fake(monkeypatch) -> list[tuple[str, list[str]]]:
    enviados: list[tuple[str, list[str]]] = []

    class _FakeThreema:
        def __init__(self, *a: object, **k: object) -> None: ...

        async def send_to_all(self, msg: str, recipients: list[str]) -> dict:
            enviados.append((msg, list(recipients)))
            return {"sent": list(recipients), "failed": []}

    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)
    return enviados


async def test_libera_reprovado_cuja_margem_passou_pela_condicao_especial(
    db: AsyncSession, monkeypatch, _sem_refetch
):
    """O caso 297400: margem 17,1% < mínima 18%, mas a Condição Especial
    (hotwav ≥16%) casa → o robô libera como o Aprovar: recado nas Observações,
    83955 → Atendido → Em aberto, espelhos 'Aprovado'/'6', auditoria do robô e
    aviso Threema."""
    seg = await _segmento_hotwav(db)
    await _seed_reprovado(
        db,
        pedido="297400",
        bling_id=26879607522,
        margem=0.1713,
        leaf_segment_id=seg.id,
        produto="Hotwav A17 Pro Max 12.64 - Laranja + Fone",
        lucro=85.57,
    )
    db.add(ThreemaInformarConfig(contexto="margem_auto", recipients="AAAA1111"))
    await db.commit()
    enviados = _threema_fake(monkeypatch)
    fake = FakeBlingComSituacao(
        observacoes="15/09 - Margem DaVinci: pedido reprovado automaticamente"
    )

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res == {
        "avaliados": 1,
        "liberados": 1,
        "pendentes": 0,
        "ainda_baixa": 0,
        "sem_margem": 0,
        "failed": 0,
    }
    # Financeiro rebuscado ANTES de julgar.
    assert _sem_refetch == [26879607522]
    # Bling: recado no topo (preserva o antigo), depois Atendido → Em aberto.
    assert fake.get_calls == [26879607522]
    obs = fake.put_bodies[0][1]["observacoes"]
    assert obs.startswith("16/09 - Margem DaVinci: pedido liberado automaticamente")
    assert "Condição Especial" in obs
    assert obs.endswith("\n15/09 - Margem DaVinci: pedido reprovado automaticamente")
    assert fake.situacao_calls == [(26879607522, 9), (26879607522, 6)]
    # Espelhos locais.
    assert tuple(await _bling_order(db, 26879607522)) == ("6", "Aprovado", None)
    snap = await _snapshot(db, "297400")
    assert (snap["situacao"], snap["situacao_nome"], snap["bling_status_margem"]) == (
        "6",
        "Em aberto",
        "Aprovado",
    )
    # Auditoria: as duas do hold + as duas da liberação, todas do robô.
    audits = await _audits(db, "297400")
    assert len(audits) == 4
    novas = sorted(
        (a for a in audits if a["valor_novo"] in ("6", "Aprovado")), key=lambda a: a["acao"]
    )
    assert novas == [
        {
            "acao": "situacao",
            "valor_antigo": "83955",
            "valor_novo": "6",
            "origem": "margens_auto",
            "mudado_por": None,
        },
        {
            "acao": "status",
            "valor_antigo": "Reprovado",
            "valor_novo": "Aprovado",
            "origem": "margens_auto",
            "mudado_por": None,
        },
    ]
    # Threema: uma mensagem, com motivo, margem vs mínima e link da aba.
    assert len(enviados) == 1
    msg, recipients = enviados[0]
    assert recipients == ["AAAA1111"]
    linhas = msg.splitlines()
    assert linhas[:6] == [
        "DaVinci — Margem: pedido liberado automaticamente",
        "Pedido 297400 — shopee Shopee ATV",
        "Produto: Hotwav A17 Pro Max 12.64 - Laranja + Fone",
        "Motivo: margem passou a atender a Condição Especial do segmento",
        "Margem: 17,1% (mínimo 18%)",
        "Lucro: R$ 85,57",
    ]
    assert linhas[-1].startswith("Ver no DaVinci: http://localhost:3000/margem")


async def test_libera_quando_margem_passou_a_atender_o_minimo(db: AsyncSession):
    """Sem Condição Especial: repasse atualizado levou a margem acima da
    mínima → libera, motivo 'mínimo'."""
    await _seed_reprovado(db, pedido="297401", bling_id=701, margem=0.19)
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["liberados"] == 1
    assert "passou a atender o mínimo" in fake.put_bodies[0][1]["observacoes"]
    assert fake.situacao_calls == [(701, 9), (701, 6)]


async def test_segundo_tick_nao_repete(db: AsyncSession):
    """Liberado vira 'Aprovado' → sai dos candidatos; e o hold também não o
    segura de novo (pino 'Aprovado')."""
    await _seed_reprovado(db, pedido="297401", bling_id=701, margem=0.19)
    fake = FakeBlingComSituacao()
    await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    res = await margem_auto_hold.reavaliar_reprovados(
        db, client=fake, hoje=HOJE, agora=AGORA + timedelta(hours=1)
    )

    assert res["avaliados"] == 0
    assert len(fake.situacao_calls) == 2


async def test_margem_ainda_baixa_nao_mexe_nem_avisa(db: AsyncSession, monkeypatch, _sem_refetch):
    """Rebuscou, continua abaixo → nada muda, sem mensagem (sem spam a cada
    hora); volta a olhar na próxima."""
    await _seed_reprovado(db, pedido="297402", bling_id=702, margem=0.15)
    db.add(ThreemaInformarConfig(contexto="margem_auto", recipients="AAAA1111"))
    await db.commit()
    enviados = _threema_fake(monkeypatch)
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["avaliados"] == 1
    assert res["ainda_baixa"] == 1
    assert res["liberados"] == 0
    assert _sem_refetch == [702]
    assert fake.get_calls == [] and fake.situacao_calls == []
    assert tuple(await _bling_order(db, 702)) == ("83955", "Reprovado", None)
    assert enviados == []


async def test_sem_margem_conhecida_nao_libera_as_cegas(db: AsyncSession):
    """Repasse sumiu (NULL) → não há o que julgar; fica como está."""
    await _seed_pedido(
        db,
        pedido="297403",
        bling_id=703,
        situacao="83955",
        status="Reprovado",
        margem_null=True,
        plataforma="shopee",
        data=DATA_PEDIDO,
    )
    await _reprovo_do_robo(db, pedido="297403", bling_id=703)
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["sem_margem"] == 1 and res["liberados"] == 0
    assert fake.situacao_calls == []


async def test_reprovacao_recente_espera_1h30(db: AsyncSession, _sem_refetch):
    """Reprovado há 1h: ainda não é revisitado (nem rebusca o financeiro)."""
    await _seed_reprovado(db, pedido="297404", bling_id=704, margem=0.19, ha=timedelta(hours=1))
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["avaliados"] == 0
    assert _sem_refetch == []
    assert fake.situacao_calls == []

    # Passou de 1h30 → entra.
    res = await margem_auto_hold.reavaliar_reprovados(
        db, client=fake, hoje=HOJE, agora=AGORA + timedelta(minutes=31)
    )
    assert res["avaliados"] == 1 and res["liberados"] == 1


async def test_reprovacao_confirmada_por_pessoa_nao_e_reavaliada(db: AsyncSession):
    """Reprovar no clique sobre um 83955 não toca o Bling — a única marca é o
    autor em bling_orders.aprovado_por. Autor preenchido = decisão humana."""
    await _seed_reprovado(db, pedido="297405", bling_id=705, margem=0.19)
    pessoa = await _pessoa(db)
    await db.execute(
        text("UPDATE bling_orders SET aprovado_por = :u, verificado = true WHERE bling_id = 705"),
        {"u": str(pessoa.id)},
    )
    await db.commit()
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["avaliados"] == 0
    assert fake.situacao_calls == []


async def test_acao_humana_posterior_na_auditoria_bloqueia(db: AsyncSession):
    """Qualquer ação de pessoa depois da reprovação do robô (ex.: edição do
    Saldo Efetivo com mudado_por) tira o pedido da reavaliação."""
    await _seed_reprovado(db, pedido="297406", bling_id=706, margem=0.19)
    db.add(
        MargemAudit(
            created_at=AGORA - timedelta(hours=1),
            pedido_bling="297406",
            bling_id="706",
            acao="saldo_final",
            valor_antigo="100",
            valor_novo="120",
            origem="margens",
            mudado_por=None,
        )
    )
    await db.commit()
    # Sem autor ainda é ação do sistema → continua candidato.
    res = await margem_auto_hold.reavaliar_reprovados(
        db, client=FakeBlingComSituacao(situacao=15), hoje=HOJE, agora=AGORA
    )
    assert res["avaliados"] == 1

    u = await _pessoa(db)
    db.add(
        MargemAudit(
            created_at=AGORA - timedelta(minutes=50),
            pedido_bling="297406",
            bling_id="706",
            acao="observacao",
            valor_antigo=None,
            valor_novo="conferido",
            origem="margens",
            mudado_por=u.id,
        )
    )
    await db.commit()

    res = await margem_auto_hold.reavaliar_reprovados(
        db, client=FakeBlingComSituacao(), hoje=HOJE, agora=AGORA
    )
    assert res["avaliados"] == 0


async def test_reprovado_no_clique_sem_auditoria_do_robo_fica_de_fora(db: AsyncSession):
    """Pino 'Reprovado' em 83955 SEM reprovação automática na auditoria (foi
    o Reprovar da aba, que grava origem 'margens' com autor) → não é do robô."""
    await _seed_pedido(
        db,
        pedido="297407",
        bling_id=707,
        situacao="83955",
        status="Reprovado",
        margem=0.19,
        plataforma="shopee",
        data=DATA_PEDIDO,
    )
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["avaliados"] == 0
    assert fake.situacao_calls == []


async def test_pedido_que_saiu_de_aguardando_cancelamento_fica_de_fora(db: AsyncSession):
    """Espelho já diz Cancelado (12): ponto final, não se ressuscita venda."""
    await _seed_reprovado(db, pedido="297408", bling_id=708, margem=0.19, situacao="12")
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["avaliados"] == 0
    assert fake.situacao_calls == []


async def test_bling_ja_moveu_o_pedido_nao_toca(db: AsyncSession):
    """Espelho ainda 83955, mas o Bling responde outra situação (alguém mexeu
    no painel) → nada no Bling, nada local; o sync acerta o espelho depois."""
    await _seed_reprovado(db, pedido="297409", bling_id=709, margem=0.19)
    fake = FakeBlingComSituacao(situacao=12)

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["avaliados"] == 1 and res["liberados"] == 0 and res["failed"] == 0
    assert fake.get_calls == [709]
    assert fake.put_bodies == [] and fake.situacao_calls == []
    assert tuple(await _bling_order(db, 709)) == ("83955", "Reprovado", None)


async def test_parado_no_degrau_atendido_so_completa_o_caminho(db: AsyncSession):
    """Tentativa anterior chegou a Atendido (9) e caiu antes do Em aberto: a
    próxima hora só manda o passo que falta."""
    await _seed_reprovado(db, pedido="297410", bling_id=710, margem=0.19)
    fake = FakeBlingComSituacao(situacao=9)

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["liberados"] == 1
    assert fake.situacao_calls == [(710, 6)]
    assert tuple(await _bling_order(db, 710)) == ("6", "Aprovado", None)


async def test_bling_recusa_situacao_nada_muda_localmente(db: AsyncSession, monkeypatch):
    """O robô nunca aprova só no DaVinci: Bling fora → pedido fica exatamente
    como estava (sem pino 'Aprovado' preso em 83955), failed=1, sem Threema;
    a próxima hora tenta de novo."""
    await _seed_reprovado(db, pedido="297411", bling_id=711, margem=0.19)
    db.add(ThreemaInformarConfig(contexto="margem_auto", recipients="AAAA1111"))
    await db.commit()
    enviados = _threema_fake(monkeypatch)
    fake = FakeBlingComSituacao(fail_situacao_for={711})

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["liberados"] == 0 and res["failed"] == 1
    assert tuple(await _bling_order(db, 711)) == ("83955", "Reprovado", None)
    snap = await _snapshot(db, "297411")
    assert (snap["situacao"], snap["bling_status_margem"]) == ("83955", "Reprovado")
    assert len(await _audits(db, "297411")) == 2  # só as do hold
    assert enviados == []


async def test_margem_passou_mas_saldo_divergente_volta_pra_pendente(db: AsyncSession, monkeypatch):
    """Amazon com repasse presente e divergente: margem agora atende, mas o
    saldo pende → só o pino vira 'Pendente' (segue segurado no Bling, volta
    pra aba) e o Threema avisa que voltou pra análise."""
    await _seed_reprovado(
        db, pedido="297412", bling_id=712, margem=0.19, plataforma="amazon", saldo_gap=True
    )
    db.add(ThreemaInformarConfig(contexto="margem_auto", recipients="AAAA1111"))
    await db.commit()
    enviados = _threema_fake(monkeypatch)
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["pendentes"] == 1 and res["liberados"] == 0
    assert fake.get_calls == [] and fake.situacao_calls == []
    assert tuple(await _bling_order(db, 712)) == ("83955", "Pendente", None)
    snap = await _snapshot(db, "297412")
    assert (snap["situacao"], snap["bling_status_margem"]) == ("83955", "Pendente")
    assert (await _audits(db, "297412"))[-1] == {
        "acao": "status",
        "valor_antigo": "Reprovado",
        "valor_novo": "Pendente",
        "origem": "margens_auto",
        "mudado_por": None,
    }
    assert len(enviados) == 1
    linhas = enviados[0][0].splitlines()
    assert linhas[0] == "DaVinci — Margem: pedido reprovado voltou para análise"
    assert "saldo continua divergente" in enviados[0][0]


async def test_falha_ao_voltar_pra_pendente_abre_a_ocorrencia_da_operacao_certa(
    db: AsyncSession, monkeypatch
):
    """Ouvidoria (22/09): o `except` da reavaliação cobre DOIS desfechos. Se o
    que falhou foi devolver o pedido pra aba Pendentes, a ocorrência tem que
    dizer isso — como "liberar" o título ficava errado E a rede de segurança do
    vigia (que confere situação + pino) nunca fecharia a linha, porque ela
    espera Em aberto + 'Aprovado' num pedido que está em 83955 + 'Pendente'."""
    from sqlalchemy import select

    from app.models import OuvidoriaOcorrencia
    from app.services import ouvidoria, vigia_margem

    await ouvidoria.sincronizar_catalogo(db)
    await db.commit()
    await _seed_reprovado(
        db, pedido="297415", bling_id=715, margem=0.19, plataforma="amazon", saldo_gap=True
    )

    async def _explode(session, *, pedido_bling, bling_id):
        raise RuntimeError("bling fora do ar")

    monkeypatch.setattr(margem_auto_hold, "_voltar_pendente_one", _explode)

    res = await margem_auto_hold.reavaliar_reprovados(
        db, client=FakeBlingComSituacao(), hoje=HOJE, agora=AGORA
    )

    assert res["failed"] == 1 and res["pendentes"] == 0 and res["liberados"] == 0
    o = (
        await db.execute(
            select(OuvidoriaOcorrencia).where(OuvidoriaOcorrencia.chave == "falha:297415")
        )
    ).scalar_one()
    assert o.titulo == "Não consegui devolver o pedido 297415 para a aba Pendentes"
    assert o.dados["operacao"] == "voltar_pendente"
    # E é essa operação que a rodada do vigia sabe conferir pra fechar sozinha.
    assert vigia_margem.operacao_concluida("voltar_pendente", "83955", "Pendente")
    # As tabelas da Ouvidoria não estão no cleanup do conftest (quem limpa são
    # os arquivos dos robôs) — este teste tira o que semeou.
    await db.execute(text("DELETE FROM ouvidoria_ocorrencias"))
    await db.execute(text("DELETE FROM ouvidoria_robos"))
    await db.commit()


async def test_falha_num_pedido_nao_derruba_os_demais(db: AsyncSession):
    await _seed_reprovado(db, pedido="297413", bling_id=713, margem=0.19)
    await _seed_reprovado(db, pedido="297414", bling_id=714, margem=0.19)
    fake = FakeBlingComSituacao(fail_situacao_for={713})

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["liberados"] == 1 and res["failed"] == 1
    assert tuple(await _bling_order(db, 713)) == ("83955", "Reprovado", None)
    assert tuple(await _bling_order(db, 714)) == ("6", "Aprovado", None)


async def test_fora_da_janela_de_30_dias_fica_de_fora(db: AsyncSession):
    await _seed_reprovado(db, pedido="297415", bling_id=715, margem=0.19, ha=timedelta(days=31))
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["avaliados"] == 0


async def test_flag_desligada(db: AsyncSession, monkeypatch, _sem_refetch):
    await _seed_reprovado(db, pedido="297416", bling_id=716, margem=0.19)
    monkeypatch.setattr(get_settings(), "margem_reavaliar_reprovados", False)

    res = await margem_auto_hold.reavaliar_reprovados(
        db, client=FakeBlingComSituacao(), hoje=HOJE, agora=AGORA
    )

    assert res["skipped"] == "disabled" and res["avaliados"] == 0
    assert _sem_refetch == []


async def test_hold_nao_segura_de_novo_o_liberado(db: AsyncSession):
    """Depois da liberação o pedido está em 6 com pino 'Aprovado' — o tick do
    hold (mesma margem baixa vs mínima, sem condição) NÃO o segura de novo:
    a decisão do robô vale como um Aprovar."""
    await _seed_reprovado(db, pedido="297417", bling_id=717, margem=0.19)
    fake = FakeBlingComSituacao()
    await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)
    # Simula o repasse caindo de novo: margem 15% < 18%.
    await db.execute(
        text("UPDATE verificar_margem SET marketplace_margem = 0.15 WHERE pedido_bling = '297417'")
    )
    await db.commit()

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res["reprovados"] == 0 and res["held"] == 0
    assert tuple(await _bling_order(db, 717)) == ("6", "Aprovado", None)


async def test_refetch_do_financeiro_falhando_julga_com_o_snapshot_atual(
    db: AsyncSession, monkeypatch
):
    """`_atualizar_financeiro` REAL (sem o fake da fixture): no schema de teste
    não há integração de marketplace nem a função da view, então a rebusca
    falha — o robô loga, limpa a transação e segue julgando com o snapshot que
    tem (empate = nada muda; aqui a margem passa e ele libera)."""
    monkeypatch.undo()  # desfaz o fake de _atualizar_financeiro da fixture
    await _seed_reprovado(db, pedido="297418", bling_id=718, margem=0.19)
    fake = FakeBlingComSituacao()

    res = await margem_auto_hold.reavaliar_reprovados(db, client=fake, hoje=HOJE, agora=AGORA)

    assert res["liberados"] == 1 and res["failed"] == 0
    assert tuple(await _bling_order(db, 718)) == ("6", "Aprovado", None)
