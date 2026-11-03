# ruff: noqa: F811 — a fixture `ajustes` vem do teste do replay
"""A amostra do eval do agente: janela explícita, exclusão e turnos congelados (03/11/2026).

O dono quer comparar o agente de hoje com o corrigido nas conversas JÁ analisadas (31/10–02/11,
inclusive as que a IA respondeu ou ninguém respondeu) e em conversas novas (30/10 e 03/11):

- `--desde/--ate` (datas de São Paulo, em pares) no lugar do `--dias`;
- `--excluir` tira os gatilhos que outro eval já usou;
- `--gatilhos` congela exatamente os turnos dados (o 1º id do turno, como o turnos.sql da
  análise), de quem quer que tenha respondido; o padrão (só a equipe) não muda.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.atendimento import constantes, ia
from app.services.atendimento.agente import replay
from scripts.agente_eval import amostra
from tests.test_atendimento_agente_replay import (  # noqa: F401 — fixture
    _conversa,
    _m,
    _msg,
    ajustes,
)

CLI, LOJA = constantes.AUTOR_CLIENTE, constantes.AUTOR_LOJA


def _sp(dia: date, hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), tzinfo=ia.SAO_PAULO)


# ─────────────── puras ───────────────


def test_janelas_sp_em_pares_com_o_ate_inclusive():
    agora = datetime(2026, 11, 3, 20, 0, tzinfo=UTC)
    janelas = amostra.janelas_sp(
        [date(2026, 10, 30), date(2026, 11, 3)],
        [date(2026, 10, 30), date(2026, 11, 3)],
        agora=agora,
    )
    # São Paulo = UTC-3: o dia 30/10 vai das 03:00 UTC do 30 às 03:00 UTC do 31.
    assert janelas == [
        (datetime(2026, 10, 30, 3, tzinfo=UTC), datetime(2026, 10, 31, 3, tzinfo=UTC)),
        (datetime(2026, 11, 3, 3, tzinfo=UTC), datetime(2026, 11, 4, 3, tzinfo=UTC)),
    ]
    # Um --desde sozinho vai até agora.
    assert amostra.janelas_sp([date(2026, 11, 1)], [], agora=agora) == [
        (datetime(2026, 11, 1, 3, tzinfo=UTC), agora)
    ]
    with pytest.raises(ValueError):  # um --desde sem o seu --ate
        amostra.janelas_sp(
            [date(2026, 10, 30), date(2026, 11, 3)], [date(2026, 10, 30)], agora=agora
        )
    with pytest.raises(ValueError):  # o fim antes do início
        amostra.janelas_sp([date(2026, 11, 3)], [date(2026, 11, 1)], agora=agora)
    with pytest.raises(ValueError):  # o --desde depois de agora
        amostra.janelas_sp([date(2026, 11, 5)], [], agora=agora)


def test_ler_gatilhos_um_por_linha_sem_repetir(tmp_path: Path):
    a, b = uuid4(), uuid4()
    arquivo = tmp_path / "gatilhos.txt"
    arquivo.write_text(f"# os turnos da análise\n{a}\n\n{b}|vortan|2026-11-02\n  {a}  # de novo\n")
    assert amostra.ler_gatilhos(arquivo) == [a, b]
    arquivo.write_text(f"{a}\nnao-e-uuid\n")
    with pytest.raises(SystemExit, match=":2:"):
        amostra.ler_gatilhos(arquivo)


def test_turno_do_pedido_pelo_primeiro_id_ou_pelo_gatilho():
    a1, a2 = _m(CLI, "oi", 0), _m(CLI, "tem na cor azul?", 1)
    r1 = _m(LOJA, "Temos sim!", 3)
    b1 = _m(CLI, "e na verde?", 10)
    r2 = _m(LOJA, "Também temos.", 12, origem=constantes.ORIGEM_IA)
    c1 = _m(CLI, "qual a voltagem?", 20)
    turnos = replay.turnos([a1, a2, r1, b1, r2, c1])
    # O 1º id do turno (o do turnos.sql) e o gatilho dão o MESMO turno do replay.
    assert amostra.turno_do_pedido(turnos, a1.id).gatilho is a2
    assert amostra.turno_do_pedido(turnos, str(a2.id)).gatilho is a2
    assert amostra.turno_do_pedido(turnos, a1.id).fechado_por == replay.FECHADO_EQUIPE
    assert amostra.turno_do_pedido(turnos, b1.id).fechado_por == replay.FECHADO_IA
    assert amostra.turno_do_pedido(turnos, c1.id).fechado_por is None
    # A mensagem da loja não é turno do cliente.
    assert amostra.turno_do_pedido(turnos, r1.id) is None
    assert amostra.turno_do_pedido(turnos, uuid4()) is None


# ─────────────── no banco ───────────────


async def _turno_com_equipe(db: AsyncSession, conversa, quando: datetime, texto: str):
    gatilho = await _msg(db, conversa, texto, quando=quando)
    await _msg(
        db,
        conversa,
        "Temos sim, pode comprar pelo anúncio.",
        quando=quando + timedelta(minutes=30),
        autor=LOJA,
        origem=constantes.ORIGEM_EXTERNO,
    )
    return gatilho


async def test_amostra_janelas_explicitas_e_excluir(
    db: AsyncSession, make_user, ajustes, tmp_path: Path, capsys
):
    d30, d31, d03 = date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 3)
    a = await _conversa(db, make_user, externo_id="janela-a")
    a30 = await _turno_com_equipe(db, a, _sp(d30, 10), "Tem na cor azul?")
    a03 = await _turno_com_equipe(db, a, _sp(d03, 10), "E na cor verde?")
    b = await _conversa(db, make_user, externo_id="janela-b")
    await _turno_com_equipe(db, b, _sp(d31, 10), "Tem na cor preta?")  # fora das janelas
    c = await _conversa(db, make_user, externo_id="janela-c")
    # 23:30 em SP = 02:30 UTC do dia 31 — ainda é 30/10 para o dono.
    c30 = await _turno_com_equipe(db, c, _sp(d30, 23, 30), "Tem na cor rosa?")
    agora = _sp(date(2026, 11, 4), 9)
    args = ["--loja", "todas", "--n", "10"]
    args += ["--desde", "2026-10-30", "--ate", "2026-10-30"]
    args += ["--desde", "2026-11-03", "--ate", "2026-11-03"]

    novas = tmp_path / "novas.jsonl"
    assert await amostra.principal([*args, "--saida", str(novas)], agora=agora) == 0
    itens = amostra.ler_jsonl(novas)
    # A conversa A entra UMA vez, pelo turno mais novo (03/11); o 31/10 fica fora.
    assert {i["gatilho_id"] for i in itens} == {str(a03.id), str(c30.id)}
    assert {i["fechado_por"] for i in itens} == {replay.FECHADO_EQUIPE}
    resumo = json.loads(capsys.readouterr().out)
    assert resumo["janelas"] == [
        [_sp(d30, 0).astimezone(UTC).isoformat(), _sp(d31, 0).astimezone(UTC).isoformat()],
        [
            _sp(d03, 0).astimezone(UTC).isoformat(),
            _sp(date(2026, 11, 4), 0).astimezone(UTC).isoformat(),
        ],
    ]

    # --excluir: o que outro eval usou sai; a conversa A entra pelo turno de 30/10.
    outra = tmp_path / "outra.jsonl"
    assert (
        await amostra.principal(
            [*args, "--excluir", str(novas), "--saida", str(outra)], agora=agora
        )
        == 0
    )
    assert [i["gatilho_id"] for i in amostra.ler_jsonl(outra)] == [str(a30.id)]
    assert json.loads(capsys.readouterr().out)["excluidos_conhecidos"] == 2

    # --ate sem --desde, e o par incompleto: erro de uso (nada roda).
    with pytest.raises(SystemExit):
        await amostra.principal(["--ate", "2026-10-30", "--saida", str(outra)], agora=agora)
    with pytest.raises(SystemExit):
        await amostra.principal(
            ["--desde", "2026-10-30", "--desde", "2026-11-03", "--ate", "2026-11-03"], agora=agora
        )


async def test_amostra_gatilhos_congela_os_turnos_de_quem_quer_que_tenha_respondido(
    db: AsyncSession, make_user, ajustes, tmp_path: Path, capsys
):
    base = _sp(date(2026, 10, 31), 15)
    # A IA respondeu (o 1º id é o "oi"; o gatilho é a pergunta).
    c1 = await _conversa(db, make_user, externo_id="gatilho-ia")
    p1 = await _msg(db, c1, "Oi", quando=base)
    g1 = await _msg(db, c1, "Tem na cor azul?", quando=base + timedelta(minutes=1))
    await _msg(
        db,
        c1,
        "Temos sim na cor azul.",
        quando=base + timedelta(minutes=3),
        autor=LOJA,
        origem=constantes.ORIGEM_IA,
    )
    # Ninguém respondeu.
    c2 = await _conversa(db, make_user, externo_id="gatilho-ninguem")
    p2 = await _msg(db, c2, "Qual a capacidade da bateria?", quando=base + timedelta(hours=1))
    # A equipe respondeu.
    c3 = await _conversa(db, make_user, externo_id="gatilho-equipe")
    p3 = await _turno_com_equipe(db, c3, base + timedelta(hours=2), "Vem com nota fiscal?")
    resposta3 = await _msg(
        db,
        c3,
        "Obrigado pela compra!",
        quando=base + timedelta(hours=4),
        autor=LOJA,
        origem=constantes.ORIGEM_EXTERNO,
    )

    arquivo = tmp_path / "gatilhos.txt"
    arquivo.write_text(
        "\n".join(
            ["# os turnos da análise", str(p1.id), str(g1.id), str(p2.id), str(p3.id), str(p1.id)]
            + [str(uuid4()), str(resposta3.id), ""]
        )
    )
    saida = tmp_path / "antigas.jsonl"
    assert await amostra.principal(["--gatilhos", str(arquivo), "--saida", str(saida)]) == 0
    itens = {i["gatilho_id"]: i for i in amostra.ler_jsonl(saida)}
    assert set(itens) == {str(g1.id), str(p2.id), str(p3.id)}
    ia_, ninguem, equipe = itens[str(g1.id)], itens[str(p2.id)], itens[str(p3.id)]
    # Os dois ids do mesmo turno viram UM item, com os dois pedidos.
    assert ia_["fechado_por"] == replay.FECHADO_IA and ia_["pedidos"] == [str(p1.id), str(g1.id)]
    assert ia_["resposta_em"] is None and ia_["conversa_id"] == str(c1.id)
    assert ninguem["fechado_por"] is None and ninguem["pedidos"] == [str(p2.id)]
    assert equipe["fechado_por"] == replay.FECHADO_EQUIPE and equipe["resposta_em"] is not None
    # O item tem os campos de sempre (o rodar e o exportar leem os mesmos).
    assert equipe["id"] == amostra.id_do_item(p3.id) and equipe["estrato"] == "nota_fiscal"
    resumo = json.loads(capsys.readouterr().out)
    assert resumo["itens"] == 3 and resumo["pedidos"] == 6
    assert resumo["faltas"] == {
        amostra.FALTA_NAO_ACHADO: 1,
        amostra.FALTA_NAO_E_DO_CLIENTE: 1,
        amostra.FALTA_MESMO_TURNO: 1,
    }
    assert resumo["por_fechado"] == {"ia": 1, "ninguem": 1, "equipe": 1}
    # Nada de texto do cliente no arquivo.
    assert "bateria" not in saida.read_text() and "azul" not in saida.read_text()

    # O caminho de sempre (sem --gatilhos) segue só com o turno que a EQUIPE respondeu.
    async with replay.sessao_replay() as s:
        padrao = await amostra.montar(
            s,
            canal_ids=[c1.canal_id, c2.canal_id, c3.canal_id],
            janelas=[(base - timedelta(hours=1), base + timedelta(days=1))],
            n=10,
        )
    assert [i["gatilho_id"] for i in padrao] == [str(p3.id)]

    # --gatilhos não se mistura com a janela.
    with pytest.raises(SystemExit):
        await amostra.principal(
            ["--gatilhos", str(arquivo), "--desde", "2026-10-31", "--saida", str(saida)]
        )
