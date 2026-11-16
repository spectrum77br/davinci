"""As MÉTRICAS POR EQUIPE e a análise do dia (13/11/2026, `metricas_equipe.py`).

O Eduardo: "em métricas ... por equipe e não mais por loja; traz as %; faz o agente 1 vez
por dia trazer uma análise geral". A equipe é a divisão da planilha (as lojas da pessoa em
`atendimento_lojas_pessoa`); a aprovação é a conta da régua (igual/forma sobre as decisões
julgadas); "saiu sozinho" = origem davinci_ia; "pela equipe" = davinci_humano.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoAvaliacao, AtendimentoRascunho, UserRole
from app.models.atendimento import AtendimentoJulgamento
from app.models.atendimento_lojas_pessoa import AtendimentoLojaPessoa
from app.services.atendimento import constantes, escopo, gravar, metricas_equipe
from tests.test_atendimento_ia import _conversa

AGORA = datetime.now(UTC)


async def _julgamento(db: AsyncSession, conversa, julgamento: str, *, tipo: str = "rastreio"):
    r = AtendimentoRascunho(conversa_id=conversa.id, texto="x", categoria=tipo, status="editado")
    db.add(r)
    await db.flush()
    a = AtendimentoAvaliacao(rascunho_id=r.id, acao="editou", texto_final="y")
    db.add(a)
    await db.flush()
    db.add(
        AtendimentoJulgamento(
            avaliacao_id=a.id,
            rascunho_id=r.id,
            plataforma=conversa.plataforma,
            integration_id=conversa.integration_id,
            tipo=tipo,
            julgamento=julgamento,
            motivo="mudou o prazo" if julgamento == "conteudo" else None,
            pelo_codigo=True,
            decidido_em=AGORA - timedelta(hours=2),
        )
    )
    await db.commit()


async def _envio(db: AsyncSession, conversa, origem: str, n: str):
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{conversa.externo_id}-{n}",
        autor="loja",
        texto="Olá, Maria Souza! Seu pedido saiu.",
        enviada_em=AGORA - timedelta(hours=1),
        origem=origem,
    )
    await db.commit()


async def _cenario(db: AsyncSession, make_user):
    londres = await make_user()
    londres.name = "londres"
    londres.sales_teams = [101]
    # O login da empresa (16/11/2026: "cairo sa", as lojas todas, sem equipe de vendas).
    empresa = await make_user()
    empresa.name = "cairo sa"
    loja_a = await _conversa(db, make_user, externo_id="a1", comprador_nome="Maria Souza")
    loja_b = await _conversa(db, make_user, externo_id="b1", comprador_nome="Maria Souza")
    db.add(
        AtendimentoLojaPessoa(user_id=londres.id, chave=escopo.chave("i", loja_a.integration_id))
    )
    for loja in (loja_a, loja_b):
        db.add(
            AtendimentoLojaPessoa(user_id=empresa.id, chave=escopo.chave("i", loja.integration_id))
        )
    await db.commit()
    for j in ("igual", "igual", "forma", "conteudo", "so_saudacao"):
        await _julgamento(db, loja_a, j)
    await _julgamento(db, loja_b, "outra_acao", tipo="garantia")
    await _envio(db, loja_a, constantes.ORIGEM_IA, "ia1")
    await _envio(db, loja_a, constantes.ORIGEM_HUMANO, "eq1")
    # A resposta por fora do DaVinci (Duoke, a central da plataforma) conta nas respostas.
    await _envio(db, loja_a, constantes.ORIGEM_EXTERNO, "ex1")
    await _envio(db, loja_b, constantes.ORIGEM_HUMANO, "eq2")
    return londres, loja_a, loja_b


async def test_resumo_por_equipe_e_por_loja(db: AsyncSession, make_user):
    _londres, loja_a, loja_b = await _cenario(db, make_user)
    r = await metricas_equipe.resumo(db, dias=7, agora=AGORA)
    # Só as três equipes de vendas (Londres, Israel, Marrocos): o login da empresa não é equipe.
    assert [e["equipe"] for e in r["equipes"]] == ["Londres"]
    (equipe,) = r["equipes"]
    # A loja A: 4 decisões (a saudação fica fora), 3 acertos (igual, igual, forma).
    assert (equipe["lojas"], equipe["decisoes"], equipe["acertos"]) == (1, 4, 3)
    assert equipe["aprovacao"] == 0.75
    assert (equipe["ia_enviou"], equipe["equipe_enviou"]) == (1, 1)
    # 16/11/2026: das 3 respostas da loja no chat, 1 saiu sozinha; 1 foi por fora.
    assert (equipe["respostas"], equipe["fora"], equipe["pct_sozinho"]) == (3, 1, 0.3333)
    lojas = {x["integration_id"]: x for x in r["lojas"]}
    assert lojas[str(loja_a.integration_id)]["equipe"] == "Londres"
    sem_equipe = lojas[str(loja_b.integration_id)]
    assert sem_equipe["equipe"] is None and sem_equipe["aprovacao"] == 0.0
    assert r["sem_equipe"]["lojas"] == 1 and r["sem_equipe"]["respostas"] == 1
    assert r["total"]["decisoes"] == 5 and r["total"]["acertos"] == 3
    assert r["total"]["respostas"] == 4 and r["total"]["pct_sozinho"] == 0.25
    # Sem os turnos (a análise do dia), nada de tempo.
    assert equipe["tempo_medio_min"] is None and equipe["pct_no_prazo"] is None


async def test_o_tempo_medio_e_o_no_prazo_vem_dos_turnos(db: AsyncSession, make_user):
    _londres, loja_a, loja_b = await _cenario(db, make_user)
    turnos = {
        (loja_a.integration_id, "shopee", None): {
            "tempos": [10.0, 50.0],
            "no_prazo": 1,
            "vencidas": 1,
        },
        (loja_b.integration_id, "shopee", None): {"tempos": [6.0], "no_prazo": 1, "vencidas": 0},
        # A loja do robô (sem integração) não é de equipe nem de loja.
        (None, "temu", uuid4()): {"tempos": [999.0], "no_prazo": 0, "vencidas": 0},
    }
    r = await metricas_equipe.resumo(
        db,
        dias=7,
        agora=AGORA,
        turnos=turnos,
        nomes_das_lojas={loja_a.integration_id: "Kia"},
    )
    (equipe,) = r["equipes"]
    # A média de TODAS as esperas da equipe (não a mediana): (10 + 50) / 2.
    assert (equipe["respondidas"], equipe["tempo_medio_min"]) == (2, 30.0)
    # No prazo sobre as decididas: 1 de (2 respondidas + 1 vencida).
    assert equipe["pct_no_prazo"] == 33.3
    lojas = {x["integration_id"]: x for x in r["lojas"]}
    assert lojas[str(loja_a.integration_id)]["loja"] == "Kia"
    assert r["sem_equipe"]["tempo_medio_min"] == 6.0
    # A do robô fica fora até do total (não é loja de equipe nenhuma).
    assert r["total"]["respondidas"] == 3


async def test_a_rota_das_equipes_traz_o_tempo(db: AsyncSession, make_user, client, auth_as):
    await _cenario(db, make_user)
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    resp = await client.get("/api/atendimento/metricas/equipes?dias=7")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert [e["equipe"] for e in corpo["equipes"]] == ["Londres"]
    assert {"sem_equipe", "lojas", "total"} <= set(corpo)
    assert {"pct_sozinho", "tempo_medio_min", "pct_no_prazo", "respostas"} <= set(corpo["total"])


class _ClienteFalso:
    def __init__(self):
        self.messages = self
        self.pedidos: list[dict] = []

    async def create(self, **kwargs):
        self.pedidos.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Ontem a IA acertou 75% em londres.")],
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(
                input_tokens=900,
                output_tokens=120,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


async def test_a_analise_do_dia_vai_para_o_redis_sem_texto_de_cliente(db: AsyncSession, make_user):
    await _cenario(db, make_user)
    cliente = _ClienteFalso()
    analise = await metricas_equipe.gerar_analise(
        db, cliente=cliente, modelo="claude-opus-5-5", agora=AGORA
    )
    assert analise is not None and analise["texto"].startswith("Ontem a IA acertou")
    (pedido,) = cliente.pedidos
    corpo = json.dumps(pedido, ensure_ascii=False, default=str)
    # Só números, assuntos e os motivos curtos do juiz — nada do que o cliente escreveu.
    assert "Maria" not in corpo and "Seu pedido saiu" not in corpo
    assert "mudou o prazo" in corpo
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(pedido)
    assert (await metricas_equipe.ultima_analise())["texto"] == analise["texto"]


# ── O "Não precisa de resposta" conta como resposta no tempo (16/11/2026) ──────────


async def _cliente(db: AsyncSession, conversa, n: str, quando: datetime):
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{conversa.externo_id}-c{n}",
        autor="cliente",
        texto="Olá",
        enviada_em=quando,
        origem=constantes.ORIGEM_CLIENTE,
    )
    await db.commit()


async def test_o_clique_sem_resposta_fecha_a_espera_na_hora(db: AsyncSession, make_user):
    from app.deps.team_scope import TeamScope
    from app.routers import atendimento as rota

    t0 = AGORA - timedelta(hours=5)
    marcada = await _conversa(db, make_user, externo_id="sr1", comprador_nome="Maria Souza")
    await _cliente(db, marcada, "1", t0)
    marcada.dados = {
        **(marcada.dados or {}),
        "sem_resposta_em": (t0 + timedelta(minutes=30)).isoformat(),
    }
    marcada.sem_resposta_necessaria = True
    # A espera fechada no clique e uma nova, depois dele, respondida pela loja.
    outra = await _conversa(db, make_user, externo_id="sr2", comprador_nome="Maria Souza")
    await _cliente(db, outra, "1", t0)
    outra.dados = {
        **(outra.dados or {}),
        "sem_resposta_em": (t0 + timedelta(minutes=10)).isoformat(),
    }
    await db.commit()
    await _cliente(db, outra, "2", t0 + timedelta(minutes=60))
    await gravar.gravar_mensagem(
        db,
        outra,
        externo_id=f"{outra.externo_id}-l1",
        autor="loja",
        texto="Bom dia! Como posso ajudar?",
        enviada_em=t0 + timedelta(minutes=90),
        origem=constantes.ORIGEM_HUMANO,
    )
    await db.commit()

    turnos = await rota._turnos_das_lojas(
        db, TeamScope(unrestricted=True), AGORA - timedelta(days=1), AGORA
    )
    por_loja = {k[0]: v for k, v in turnos.items()}
    a = por_loja[marcada.integration_id]
    assert (a["tempos"], a["recebidas"], a["vencidas"]) == ([30.0], 1, 0)
    b = por_loja[outra.integration_id]
    assert sorted(b["tempos"]) == [10.0, 30.0] and b["vencidas"] == 0
    assert rota._hora_do_clique("lixo") is None and rota._hora_do_clique(None) is None
