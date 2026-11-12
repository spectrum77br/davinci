"""Prazos da aba Acompanhamento (12/11/2026): 45 dias sem o pacote chegar →
lançar Não devolvido + Problemas + chamado (por enquanto só avisa), com as
exceções (prazo na observação, extraviado, não recebido); Amazon 14 dias sem
postar e sem reembolso → cancelar a devolução no Seller Central."""

from __future__ import annotations

from datetime import date, timedelta

from app.services import devolucao_prazo_acompanhamento as dp

HOJE = date(2026, 11, 12)


def _avaliar(**kw):
    base = {
        "fila": "acompanhamento",
        "plataforma": "ml",
        "entrada": HOJE - timedelta(days=48),
        "hoje": HOJE,
        "observacao": None,
        "localizacao": None,
        "lancada": False,
        "chegou_em": None,
        "reembolso": None,
        "meli_status": None,
    }
    base.update(kw)
    return dp.avaliar(**base)


def test_passou_de_45_dias_vencido():
    r = _avaliar()
    assert r["prazo_estado"] == dp.VENCIDO
    assert r["prazo_ate"] == HOJE - timedelta(days=3)
    assert r["prazo_dias_restantes"] == -3
    assert r["prazo_origem"] == "padrao"
    assert "lançar Não devolvido" in r["prazo_texto"]


def test_vence_logo_e_dentro_do_prazo():
    r = _avaliar(entrada=HOJE - timedelta(days=42))
    assert (r["prazo_estado"], r["prazo_dias_restantes"]) == (dp.VENCE_LOGO, 3)
    assert "aguarde 60 dias" in r["prazo_texto"]
    assert _avaliar(entrada=HOJE - timedelta(days=20))["prazo_estado"] is None


def test_observacao_com_prazo_novo_segura():
    """O caso real 292597 (ML, 48 dias): "09/11 Chamado aberto, se não for
    devolvido até 60 dias vai s…" → prazo de 60 dias, dentro."""
    obs = "09/11 Chamado aberto, se não for devolvido até 60 dias vai solicitar"
    r = _avaliar(observacao=obs)
    assert r["prazo_estado"] is None
    assert r["prazo_origem"] == "observacao"
    assert r["prazo_ate"] == HOJE - timedelta(days=48) + timedelta(days=60)
    assert "estendido" in r["prazo_texto"]


def test_observacao_com_data_e_mais_n_dias():
    # "até dia 24/11" (304748: "CHAMADO ABERTO RESPOSTA ATE DIA 24/11")
    r = _avaliar(observacao="CHAMADO ABERTO RESPOSTA ATE DIA 24/11 - joana")
    assert r["prazo_ate"] == date(2026, 11, 24) and r["prazo_estado"] is None
    # "mais 15 dias" a partir da data no começo da observação
    r = _avaliar(observacao="05/11 cliente pediu, aguardar mais 15 dias")
    assert r["prazo_ate"] == date(2026, 11, 20)
    # sem data no começo, "mais N dias" não estende (não se sabe de quando)
    assert _avaliar(observacao="aguardar mais 15 dias")["prazo_estado"] == dp.VENCIDO
    # número de 45 ou menos não estende ("a Amazon dá 30 dias")
    assert _avaliar(observacao="Joana verificando, pq amazon da 30 dias")["prazo_estado"] == (
        dp.VENCIDO
    )
    # prazo da observação que já passou também vence
    r = _avaliar(entrada=HOJE - timedelta(days=70), observacao="aguarde 60 dias")
    assert (r["prazo_estado"], r["prazo_origem"]) == (dp.VENCIDO, "observacao")


def test_excecoes_extraviado_e_nao_recebido():
    r = _avaliar(localizacao="Objeto extraviado")
    assert r["prazo_estado"] == dp.EXCECAO and "extraviado" in r["prazo_texto"]
    r = _avaliar(observacao="cliente diz que não recebeu - não recebido")
    assert r["prazo_estado"] == dp.EXCECAO and "não recebido" in r["prazo_texto"]
    r = _avaliar(meli_status={"return_reason": "Não recebido"})
    assert r["prazo_estado"] == dp.EXCECAO
    # Antes dos 45 dias a exceção não aparece (não há o que avisar).
    assert _avaliar(entrada=HOJE - timedelta(days=10), localizacao="extraviado")[
        "prazo_estado"
    ] is None


def test_fora_da_regra():
    assert _avaliar(lancada=True)["prazo_estado"] is None
    assert _avaliar(chegou_em=HOJE)["prazo_estado"] is None
    assert _avaliar(fila="fraude")["prazo_estado"] is None
    assert _avaliar(entrada=None)["prazo_estado"] is None


def test_amazon_14_dias_sem_postar():
    amz = {"plataforma": "amazon", "entrada": HOJE - timedelta(days=14)}
    pedida = {"return_status": "PENDING"}
    r = _avaliar(**amz, meli_status=pedida)
    assert r["amazon_cancelar"] is True
    assert "Seller Central" in r["amazon_cancelar_texto"]
    # 13 dias, já postou (a caminho), reembolsada ou chegou: não
    assert not _avaliar(**{**amz, "entrada": HOJE - timedelta(days=13)}, meli_status=pedida)[
        "amazon_cancelar"
    ]
    assert not _avaliar(**amz, meli_status={"return_status": "SHIPPED"})["amazon_cancelar"]
    assert not _avaliar(**amz, meli_status=pedida, reembolso=True)["amazon_cancelar"]
    assert not _avaliar(**amz, meli_status=pedida, chegou_em=HOJE)["amazon_cancelar"]
    assert not _avaliar(plataforma="ml", meli_status=pedida)["amazon_cancelar"]
