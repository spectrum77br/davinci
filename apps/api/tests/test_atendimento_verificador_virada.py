"""A virada no verificador (16/11/2026, o Eduardo: "ajusta isso; se tiver que trocar o Haiku
pelo Sonnet, faça"): ele lê TODAS as respostas do banco que o agente viu (não só as citadas), a do
mesmo produto ou modelo vale para produto na conversa de um modelo só, o modelo é configurável e
o eval roda o verificador no replay com um modelo de sombra ao lado (só gravado)."""

from __future__ import annotations

import io
import types
from typing import Any

import pytest

from app.config import get_settings
from app.services.atendimento.agente import custo, verificador
from app.services.atendimento.constantes import MODO_AUTO
from scripts.agente_eval import rodar
from tests.test_atendimento_agente_verificador import (
    _CONVERSA,
    _base,
    _ctx,
    _final,
    _proposta,
    _veredito,
)

SONNET = "claude-sonnet-5-5"


def _par(i: str, **kw: Any) -> dict:
    return {
        "id": i,
        "pergunta": "tem nfc?",
        "resposta": "Tem sim, o modelo tem NFC.",
        "mesma_loja": False,
        "mesmo_anuncio": False,
        "mesmo_produto": False,
        "fonte_de_produto": False,
        "com_anuncio": True,
        "idade_dias": 3,
        **kw,
    }


# ─────────────── o modelo ───────────────


def test_o_modelo_do_verificador_vem_da_configuracao(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_verificador_modelo", "")
    assert verificador.modelo_do_verificador() == verificador.MODELO == "claude-haiku-5-5"
    monkeypatch.setattr(s, "atendimento_verificador_modelo", f"  {SONNET} ")
    assert verificador.modelo_do_verificador() == SONNET


def test_o_pedido_com_o_sonnet_nao_leva_pensamento():
    pedido = verificador.argumentos("entrada", modelo=SONNET)
    assert pedido["model"] == SONNET and "thinking" not in pedido


# ─────────────── as respostas do banco que ele lê ───────────────


def _ctx_com_pares(*, virada: bool) -> Any:
    ctx = _ctx(banco_exigido=virada)
    ctx.registrar_pares(
        [_par("par:b1"), _par("par:b2"), _par("par:77"), *(_par(f"par:b{n}") for n in range(3, 15))]
    )
    ctx.evidencias = ["par:b2", "par:77"]
    return ctx


def test_fora_da_virada_so_as_citadas():
    ids = [p["id"] for p in verificador.pares_para_o_verificador(_ctx_com_pares(virada=False))]
    assert ids == ["par:b2", "par:77"]


def test_na_virada_as_citadas_primeiro_e_as_outras_do_banco():
    ids = [p["id"] for p in verificador.pares_para_o_verificador(_ctx_com_pares(virada=True))]
    assert ids[:3] == ["par:b2", "par:77", "par:b1"]
    # A do pacote que não é do banco (`par:77`) só citada; o teto de 10.
    assert len(ids) == verificador.MAX_PARES_NO_VERIFICADOR and len(set(ids)) == len(ids)
    assert all(i.startswith("par:b") for i in ids if i != "par:77")


# ─────────────── o que vale para produto ───────────────


def _linha(par: dict, *, virada: bool) -> str:
    linhas = verificador.linhas_dos_pares([par], (), virada=virada)
    assert len(linhas) == 2
    return linhas[1]


VALE = f": {verificador.VALE_PRODUTO};"


def test_na_virada_a_do_mesmo_modelo_vale_para_produto():
    par = _par("par:b1", mesmo_modelo=True, mesmo_produto=True, um_modelo=True)
    assert VALE in _linha(par, virada=True)
    # Fora da virada, a regra de antes (só o `fonte_de_produto`).
    assert verificador.NAO_VALE_PRODUTO in _linha(par, virada=False)


def test_na_virada_com_outro_modelo_na_conversa_so_o_nomeado():
    par = _par("par:b1", mesmo_produto=True, um_modelo=False)
    linha = _linha(par, virada=True)
    assert verificador.VALE_PRODUTO_NOMEADO in linha and VALE not in linha


def test_na_virada_o_par_fora_do_banco_e_o_de_outro_produto_nao_mudam():
    fora_do_banco = _par("par:77", mesmo_modelo=True, mesmo_produto=True, um_modelo=True)
    assert verificador.NAO_VALE_PRODUTO in _linha(fora_do_banco, virada=True)
    outro_produto = _par("par:b1", um_modelo=True)
    assert verificador.NAO_VALE_PRODUTO in _linha(outro_produto, virada=True)


def test_o_mesmo_anuncio_de_varios_modelos_segue_so_o_nomeado():
    par = _par("par:b1", mesmo_anuncio=True, mesmo_produto=True, mesmo_modelo=True)
    linha = _linha(par, virada=True)
    assert verificador.VALE_PRODUTO_NOMEADO in linha and VALE not in linha


def test_o_cabecalho_da_virada_fala_do_banco_com_o_mesmo_nome_da_secao():
    linhas = verificador.linhas_dos_pares([_par("par:b1")], (), virada=True)
    assert linhas[0] == verificador.CABECALHO_PARES_VIRADA
    assert linhas[0].startswith("RESPOSTAS DA EQUIPE CITADAS (as do banco de respostas")
    assert verificador.linhas_dos_pares([_par("par:b1")], ())[0] == verificador.CABECALHO_PARES


# ─────────────── o eval: o verificador no replay e a sombra ───────────────


def _resultado(ok: bool, custo_usd: float) -> verificador.Resultado:
    v = _veredito(ok_para_envio_automatico=ok, afirma_sem_lastro=[] if ok else ["tem NFC"])
    return verificador.Resultado(
        ok=ok,
        veredito=verificador.Veredito(**v),
        pediu=True,
        uso=custo.Uso(custo_usd=custo_usd),
    )


async def _canal(_session, _conversa):
    return types.SimpleNamespace(
        modo=MODO_AUTO, auto_categorias=["rastreio"], auto_agente=["rastreio"]
    )


async def test_no_replay_com_o_eval_roda_e_grava_a_sombra(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_verificador_no_replay", True)
    monkeypatch.setattr(s, "atendimento_verificador_sombra", SONNET)
    monkeypatch.setattr(s, "atendimento_verificador_modelo", "")
    chamadas: list[str | None] = []

    async def _verificar(*_a, modelo=None, **_k):
        chamadas.append(modelo)
        return _resultado(True, 0.001) if modelo is None else _resultado(False, 0.004)

    monkeypatch.setattr(verificador, "verificar", _verificar)
    monkeypatch.setattr(verificador, "_canal", _canal)
    ctx = _ctx(modo_replay=True)
    assert verificador.por_que_nao_verifica(_final(), _proposta(), ctx, _base(), _CONVERSA) is None

    conf = await verificador.no_rascunho(
        None,  # type: ignore[arg-type]
        _CONVERSA,
        None,
        _base(),
        ctx,
        _proposta(),
        _final(),
        cliente=None,
        prazo_s=30.0,
    )

    # Quem decide é o modelo do verificador (o Haiku aprovou); o Sonnet só fica gravado.
    assert chamadas == [None, SONNET]
    assert conf.codigo is None and conf.final == _final()
    assert conf.registro["ok"] is True
    sombra = conf.registro["sombra"]
    assert sombra["modelo"] == SONNET and sombra["ok"] is False
    assert sombra["problemas"] == [verificador.SEM_LASTRO]
    assert conf.custo_usd == pytest.approx(0.005)


async def test_ao_vivo_sem_sombra_uma_chamada_so(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_verificador_sombra", "")
    chamadas: list[str | None] = []

    async def _verificar(*_a, modelo=None, **_k):
        chamadas.append(modelo)
        return _resultado(False, 0.002)

    monkeypatch.setattr(verificador, "verificar", _verificar)
    monkeypatch.setattr(verificador, "_canal", _canal)
    conf = await verificador.no_rascunho(
        None,  # type: ignore[arg-type]
        _CONVERSA,
        None,
        _base(),
        _ctx(),
        _proposta(),
        _final(),
        cliente=None,
        prazo_s=30.0,
    )
    assert chamadas == [None] and "sombra" not in conf.registro
    assert conf.codigo == verificador.CODIGO and conf.custo_usd == pytest.approx(0.002)


async def test_o_rodar_liga_o_verificador_so_no_processo(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_verificador_no_replay", False)
    monkeypatch.setattr(s, "atendimento_verificador_sombra", "")
    monkeypatch.setattr(s, "atendimento_verificador_modelo", "")
    monkeypatch.setattr(rodar.agente_config, "chave_do_agente", lambda _s: None)
    codigo = await rodar.principal(
        ["--amostra", "/nao/existe.jsonl", "--verificador", "--verificador-sombra", SONNET],
        eco=io.StringIO(),
    )
    assert codigo == rodar.SEM_CHAVE
    assert s.atendimento_verificador_no_replay is True
    assert s.atendimento_verificador_sombra == SONNET
    assert s.atendimento_verificador_modelo == ""


# ─────────────── a resposta da equipe à mesma pergunta solta o "não responde" ───────────────


def _barrado(**marcas: Any) -> verificador.Resultado:
    v = _veredito(ok_para_envio_automatico=False, motivos=["não responde o prazo"], **marcas)
    return verificador.Resultado(ok=False, veredito=verificador.Veredito(**v), pediu=True)


def _ctx_do_banco(veredito: str = "serve", *, virada: bool = True) -> Any:
    return _ctx(
        banco_exigido=virada,
        banco_bateu="par:b5040",
        banco_confere={"id": "par:b5040", "veredito": veredito, "pessoa": True},
    )


def test_a_resposta_do_banco_a_mesma_pergunta_solta_o_nao_responde():
    r = verificador.respondida_pelo_banco(
        _barrado(responde_o_que_foi_perguntado=False), _ctx_do_banco()
    )
    assert r.ok and r.problemas == [] and r.solto_por == "par:b5040"
    fatos = r.para_fatos("Reembolso direto com a plataforma, não temos acesso!")
    assert fatos["ok"] is True and fatos["soltos"] == [verificador.NAO_RESPONDE]
    assert verificador.respondida_pelo_banco(
        _barrado(responde_o_que_foi_perguntado=False), _ctx_do_banco("mesma")
    ).ok


def test_a_resposta_do_banco_a_mesma_pergunta_solta_o_nega_direito():
    """17/11/2026 (o juiz mais liberal): "Não fazemos troca somente devolução!" e "Não temos como
    alterar, a logística fica em outra cidade" são a resposta da equipe à mesma pergunta — o
    "nega direito" do juiz não segura; a promessa e a afirmação sem lastro continuam valendo."""
    marcas = {"nega_direito_ou_garantia": True, "responde_o_que_foi_perguntado": False}
    r = verificador.respondida_pelo_banco(_barrado(**marcas), _ctx_do_banco())
    assert r.ok and r.problemas == []
    assert set(r.para_fatos("Não fazemos troca somente devolução!")["soltos"]) == {
        verificador.NEGA,
        verificador.NAO_RESPONDE,
    }
    for outra in (
        {"nega_direito_ou_garantia": True, "promete_acao_da_equipe": True},
        {"nega_direito_ou_garantia": True, "afirma_sem_lastro": ["não temos como trocar"]},
    ):
        antes = _barrado(**outra)
        assert verificador.respondida_pelo_banco(antes, _ctx_do_banco()) is antes
    antes = _barrado(nega_direito_ou_garantia=True)
    assert verificador.respondida_pelo_banco(antes, _ctx_do_banco("diferente")) is antes


@pytest.mark.parametrize(
    ("marcas", "ctx"),
    [
        # A afirmação sem lastro continua valendo (o "não tem rádio" de outro modelo).
        (
            {"responde_o_que_foi_perguntado": False, "afirma_sem_lastro": ["não tem rádio"]},
            _ctx_do_banco(),
        ),
        ({"afirma_sem_lastro": ["não tem rádio"]}, _ctx_do_banco()),
        # Outro veredito da conferência, fora da virada, ou sem o banco ter batido: nada muda.
        ({"responde_o_que_foi_perguntado": False}, _ctx_do_banco("diferente")),
        ({"responde_o_que_foi_perguntado": False}, _ctx_do_banco(virada=False)),
        ({"responde_o_que_foi_perguntado": False}, _ctx(banco_exigido=True)),
    ],
)
def test_o_resto_continua_barrado(marcas, ctx):
    antes = _barrado(**marcas)
    depois = verificador.respondida_pelo_banco(antes, ctx)
    assert depois is antes and not depois.ok
    assert "soltos" not in depois.para_fatos("x")


def test_o_padrao_e_o_sonnet():
    from app.config import Settings

    assert Settings.model_fields["atendimento_verificador_modelo"].default == SONNET


# ─────────────── a resposta de produto do banco só do mesmo produto ou modelo ───────────────


def test_a_pergunta_de_produto_so_confere_com_o_mesmo_modelo():
    from app.services.atendimento.agente import confere_banco

    ctx = _ctx()
    ctx.registrar_pares(
        [
            # O "não tem rádio" de outro celular (o teste de 16/11): fora da conferência.
            _par("par:b4841", tipo="espec_celular", resposta="Não tem rádio, mas tem Spotify!"),
            _par("par:b7", tipo="espec_celular", mesmo_modelo=True, resposta="Tem rádio FM!"),
            _par("par:b9", tipo="reembolso", resposta="Reembolso é com a plataforma!"),
        ]
    )
    # A pergunta de produto: só a resposta do mesmo produto ou modelo.
    de_produto = [i for i, _p, _r in confere_banco.candidatas(ctx, "espec_celular")]
    assert de_produto == ["par:b7"]
    # A pergunta que não é de produto confere com a política de qualquer modelo; a resposta de
    # produto de outro modelo continua fora.
    assert [i for i, _p, _r in confere_banco.candidatas(ctx, "reembolso")] == ["par:b9", "par:b7"]


def test_o_pacote_marca_a_do_mesmo_modelo_como_valendo_para_produto():
    from app.services.atendimento.agente import pacote

    mesmo = _par("par:b7", mesmo_modelo=True, mesmo_produto=True, um_modelo=True)
    assert ", vale para produto," in pacote._onde_do_par(mesmo)
    outro = _par("par:b8", um_modelo=True)
    assert "não vale para produto" in pacote._onde_do_par(outro)
    assert "outro modelo NÃO vale" in pacote.BANCO_CABECALHO


def test_o_padrao_da_equipe_vale_como_politica_para_o_verificador():
    par = _par("par:b7", padrao=4, resposta="Pode devolver para a Shopee!")
    assert f"padrão da equipe, 4 respostas iguais: {verificador.VALE_POLITICA}" in _linha(
        par, virada=True
    )
    # Fora da virada, a regra de sempre (de outra loja não vale para a política).
    assert verificador.NAO_VALE_POLITICA in _linha(par, virada=False)


def test_o_padrao_vai_primeiro_ao_verificador():
    ctx = _ctx(banco_exigido=True)
    ctx.registrar_pares(
        [*(_par(f"par:b{n}") for n in range(1, 12)), _par("par:b99", padrao=5, tipo="defeito")]
    )
    ctx.evidencias = []
    ids = [p["id"] for p in verificador.pares_para_o_verificador(ctx)]
    assert ids[0] == "par:b99" and len(ids) == verificador.MAX_PARES_NO_VERIFICADOR


def test_a_resposta_do_banco_do_mesmo_modelo_conta_como_da_equipe_para_produto():
    """O aviso "assunto de produto sem a resposta da equipe do mesmo produto" não vai ao
    verificador quando o rascunho cita a do banco do mesmo modelo (16/11/2026)."""
    par = _par("par:b5384", mesmo_produto=True, mesmo_modelo=True, um_modelo=True)
    ctx = _ctx(banco_exigido=True)
    ctx.registrar_pares([par, _par("par:b9", mesmo_produto=True)])
    assert [p["id"] for p in ctx.pares_citados(["par:b5384", "par:b9"], de_produto=True)] == [
        "par:b5384"
    ]
    fora = _ctx(banco_exigido=False)
    fora.registrar_pares([par])
    assert fora.pares_citados(["par:b5384"], de_produto=True) == []
    # A trava do envio lê o mesmo critério (`ia.produto_pela_equipe`, `pares_de_produto`): antes
    # ela barrava sem motivo a resposta do banco do mesmo modelo que o verificador aceitava.
    assert ctx.para_fatos()["pares_de_produto"] == ["par:b5384"]
    assert "pares_de_produto" not in fora.para_fatos()
