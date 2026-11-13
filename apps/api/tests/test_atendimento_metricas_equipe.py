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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoAvaliacao, AtendimentoRascunho
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
    loja_a = await _conversa(db, make_user, externo_id="a1", comprador_nome="Maria Souza")
    loja_b = await _conversa(db, make_user, externo_id="b1", comprador_nome="Maria Souza")
    db.add(
        AtendimentoLojaPessoa(user_id=londres.id, chave=escopo.chave("i", loja_a.integration_id))
    )
    await db.commit()
    for j in ("igual", "igual", "forma", "conteudo", "so_saudacao"):
        await _julgamento(db, loja_a, j)
    await _julgamento(db, loja_b, "outra_acao", tipo="garantia")
    await _envio(db, loja_a, constantes.ORIGEM_IA, "ia1")
    await _envio(db, loja_a, constantes.ORIGEM_HUMANO, "eq1")
    await _envio(db, loja_b, constantes.ORIGEM_HUMANO, "eq2")
    return londres, loja_a, loja_b


async def test_resumo_por_equipe_e_por_loja(db: AsyncSession, make_user):
    _londres, loja_a, loja_b = await _cenario(db, make_user)
    r = await metricas_equipe.resumo(db, dias=7, agora=AGORA)
    (equipe,) = [e for e in r["equipes"] if e["equipe"] == "londres"]
    # A loja A: 4 decisões (a saudação fica fora), 3 acertos (igual, igual, forma).
    assert (equipe["lojas"], equipe["decisoes"], equipe["acertos"]) == (1, 4, 3)
    assert equipe["aprovacao"] == 0.75
    assert (equipe["ia_enviou"], equipe["equipe_enviou"]) == (1, 1)
    lojas = {x["integration_id"]: x for x in r["lojas"]}
    assert lojas[str(loja_a.integration_id)]["equipe"] == "londres"
    sem_equipe = lojas[str(loja_b.integration_id)]
    assert sem_equipe["equipe"] is None and sem_equipe["aprovacao"] == 0.0
    assert r["total"]["decisoes"] == 5 and r["total"]["acertos"] == 3


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
