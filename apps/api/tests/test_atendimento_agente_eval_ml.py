# ruff: noqa: F811 — a fixture `ajustes` vem do teste do replay
"""O eval do agente no MERCADO LIVRE (03/11/2026): a corrente inteira, sem modelo e sem loja.

O dono quer ligar o automático em vários chats do ML: o A/B de 03/11 também mede, no ML,
quantas vezes o agente responderia sozinho e certo. O que este arquivo prende:

- amostra: o pós-venda (`pos_venda`, uma conversa por pack, SEM anúncio ligado — os anúncios
  vêm do retrato do pedido) e a pergunta pública (`pergunta`, uma conversa por pergunta) que a
  equipe respondeu; `--canal` deixa a reclamação de fora; `--excluir-conversas` tira as
  conversas de um eval anterior inteiras;
- preaquecer: o anúncio do pós-venda vem do item do pedido (o retrato `pedido_mkt`), o da
  pergunta é o anúncio perguntado — os dois entram no aquecimento (só o cache, sem a loja);
- rodar: o replay do ML sem ida à loja (nenhum cliente do ML, nenhum HTTP) e com o canal no
  registro;
- regatilho: a conversa do ML reconstruída com o SEU canal — antes o pós-venda virava "chat"
  e o limite de 350 caracteres não valia; o resumo por canal; a régua congelada da Kia
  (`config_canal`) no `--config-canal-json`;
- exportar: o canal vai para a nota (a régua da nota do ML é outra).
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoCanal, Integration, IntegrationPlatform
from app.security.cipher import encrypt_json
from app.services.atendimento import constantes, gravar, ia
from app.services.atendimento.agente import replay
from app.services.marketplaces import ml as ml_client
from scripts.agente_eval import amostra, config_canal, exportar, preaquecer, regatilho, rodar
from tests.test_atendimento_agente_furos import SAIU
from tests.test_atendimento_agente_replay import (  # noqa: F401 — fixture
    _Api,
    _final,
    _loja,
    _msg,
    ajustes,
)

CLI, LOJA = constantes.AUTOR_CLIENTE, constantes.AUTOR_LOJA
POS = ia._CANAL_DESCRICAO[("ml", constantes.CANAL_POS_VENDA)]
PERGUNTA = ia._CANAL_DESCRICAO[("ml", constantes.CANAL_PERGUNTA)]
# A régua da Kia (todos os assuntos do agente), como o `config_canal` congela.
KIA_AUTO = {
    "modo": "auto",
    "auto_categorias": ["rastreio", "prazo_envio", "espec_mala", "saudacao_ou_vaga"],
    "auto_agente": ["rastreio", "prazo_envio", "saudacao_ou_vaga"],
    "auto_orientacao": [],
}


def _sp(dia: date, hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), tzinfo=ia.SAO_PAULO)


# ─────────────── o regatilho (puro) ───────────────


def _pacote(canal: str, bloco: dict | None) -> str:
    cab = {"agora_sao_paulo": "03/11/2026 10:00", "canal": canal, "plataforma": "Mercado Livre"}
    linhas = ["<dados>", "CABEÇALHO: " + json.dumps(cab, ensure_ascii=False)]
    linhas.append(
        "PEDIDO DA CONVERSA: " + (json.dumps(bloco, ensure_ascii=False) if bloco else "nenhum")
    )
    linhas.append("EVIDÊNCIAS DESTES DADOS (ids): " + json.dumps(["pedido"] if bloco else []))
    return "\n".join([*linhas, "</dados>"])


def _registro_ml(texto: str, *, canal: str = POS, **kw: Any) -> dict:
    bloco = {**SAIU, "numero": "{numero_pedido}", "itens": []} if canal == POS else None
    return {
        "id": "ml1",
        "integration_id": "6d0c3a51-0000-4000-8000-000000000001",
        "plataforma": "ml",
        "parada": "fim",
        "decisao": {"acoes_codigo": []},
        "proposta": {
            "acao": "responder",
            "acao_pessoa": None,
            "assunto": "rastreio",
            "confianca": 0.9,
            "evidencias": ["pedido"] if bloco else [],
            "motivo_pessoa": "",
            "texto": texto,
        },
        "texto_final": texto,
        "resposta_real": "ok",
        "rastro": {
            "pacote": _pacote(canal, bloco),
            "transcricao": ["[03/11 09:59] Cliente: cadê meu pedido?"],
        },
        **kw,
    }


def test_o_canal_do_registro_vem_gravado_ou_do_cabecalho_do_pacote():
    # O gravado pelo rodar (o da amostra) vence.
    assert regatilho.canal_do_registro({"canal": "pos_venda"}) == constantes.CANAL_POS_VENDA
    # Sem ele: a descrição do cabeçalho, pela plataforma (antes o pós-venda virava "chat").
    assert regatilho.canal_do_registro(_registro_ml("x")) == constantes.CANAL_POS_VENDA
    assert (
        regatilho.canal_do_registro(_registro_ml("x", canal=PERGUNTA)) == constantes.CANAL_PERGUNTA
    )
    shopee = {
        "plataforma": "shopee",
        "rastro": {"pacote": _pacote("chat da Shopee com o comprador", None)},
    }
    assert regatilho.canal_do_registro(shopee) == constantes.CANAL_CHAT
    # O canal sem descrição vem pelo nome; o resto, como antes.
    assert regatilho.canal_do_registro(_registro_ml("x", canal="reclamacao")) == "reclamacao"
    assert regatilho.canal_do_registro({"canal": "inventado"}) == constantes.CANAL_CHAT
    assert regatilho.canal_do_registro({}) == constantes.CANAL_CHAT
    # A calibração (o cabeçalho "chat") segue chat.
    r = regatilho.registro_da_calibracao({"id": "c", "texto": "Olá!", "conversa": []})
    assert regatilho.canal_do_registro(r) == constantes.CANAL_CHAT
    m = regatilho.montar(_registro_ml("x"))
    assert m.conversa.plataforma == "ml" and m.conversa.canal == constantes.CANAL_POS_VENDA


async def test_o_pos_venda_do_ml_e_medido_com_o_limite_de_350_caracteres():
    curto = "O seu pedido {numero_pedido} já foi postado; acompanhe em Minhas compras."
    bom = await regatilho.regatilhar(_registro_ml(curto), KIA_AUTO)
    assert bom["decisao"] == regatilho.CHEGA, bom
    assert bom["canal"] == constantes.CANAL_POS_VENDA and bom["plataforma"] == "ml"
    longo = " ".join([curto] * 6)
    assert 350 < len(longo) < 1000
    segurado = await regatilho.regatilhar(_registro_ml(longo), KIA_AUTO)
    assert segurado["decisao"].startswith("pessoa:"), segurado
    assert any("350" in str(b) for b in segurado["bloqueios"]), segurado["bloqueios"]
    # O mesmo texto como se fosse chat (o regatilho de antes): o limite não pegava.
    como_chat = await regatilho.regatilhar({**_registro_ml(longo), "canal": "chat"}, KIA_AUTO)
    assert not any("350" in str(b) for b in como_chat["bloqueios"]), como_chat["bloqueios"]
    # A reclamação/mediação aberta no ML (o rodar grava `reclamacao_aberta`): nunca sozinho.
    mediado = await regatilho.regatilhar(
        {**_registro_ml(curto), "reclamacao_aberta": True}, KIA_AUTO
    )
    # (a 1ª camada já segura pela reclamação do ML; sem a marca, o pacote não diz nada)
    assert mediado["decisao"].startswith("pessoa:"), mediado
    assert any("reclamação/mediação aberta no ML" in b for b in mediado["bloqueios"]), mediado
    res = regatilho.resumo([bom, segurado])
    assert res["por_canal"]["ml:pos_venda"]["total"] == 2
    assert res["por_canal"]["ml:pos_venda"]["agora"] == 1


# ─────────────── a amostra, o aquecimento e o rodar no banco ───────────────


async def _loja_ml(db: AsyncSession, make_user) -> tuple[Integration, dict[str, AtendimentoCanal]]:
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.ML,
        name="Loja ML Teste",
        credentials=encrypt_json({"access_token": "x", "refresh_token": "y", "expires_at": 1}),
    )
    db.add(integ)
    await db.commit()
    canais: dict[str, AtendimentoCanal] = {}
    # Como em produção: os canais do ML em "humano" (nenhum no automático).
    for nome in (
        constantes.CANAL_PERGUNTA,
        constantes.CANAL_POS_VENDA,
        constantes.CANAL_RECLAMACAO,
    ):
        k = AtendimentoCanal(
            integration_id=integ.id, plataforma="ml", canal=nome, modo="humano",
            auto_categorias=[], auto_orientacao=[], auto_agente=[],
        )  # fmt: skip
        db.add(k)
        canais[nome] = k
    await db.commit()
    return integ, canais


async def _conversa_ml(db, integ, canal, *, externo_id: str, **kw: Any):
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="ml",
        canal_nome=canal.canal,
        externo_id=externo_id,
        comprador_id=f"comprador-{externo_id}",
        comprador_nome="Joana Pereira",
        **kw,
    )
    await db.commit()
    return conversa


async def _turno(db, conversa, quando: datetime, texto: str, resposta: str):
    gatilho = await _msg(db, conversa, texto, quando=quando)
    await _msg(
        db, conversa, resposta, quando=quando + timedelta(minutes=40), autor=LOJA,
        origem=constantes.ORIGEM_EXTERNO,
    )  # fmt: skip
    return gatilho


async def _cenario(db: AsyncSession, make_user, tmp_path: Path):
    integ, canais = await _loja_ml(db, make_user)
    d03, d15 = date(2026, 11, 3), date(2026, 10, 15)
    pos = await _conversa_ml(
        db, integ, canais["pos_venda"], externo_id="pack-1", pedido_marketplace="2000001",
        dados={"pack_id": "2000001", "pedido_mkt": {"itens": [{"item_id": "MLB456"}]}},
    )  # fmt: skip
    g_pos = await _turno(db, pos, _sp(d03, 9), "Quando chega o meu pedido?", "Já foi postado!")
    perg = await _conversa_ml(
        db, integ, canais["pergunta"], externo_id="q-1", anuncio_id="MLB123",
        anuncio_titulo="Mala de bordo 10kg",
    )  # fmt: skip
    g_perg = await _turno(db, perg, _sp(d15, 14), "Tem rodinha 360?", "Tem sim, 4 rodas 360.")
    # O pack com a reclamação/mediação aberta (`dados.claim_ids`): o automático nunca responde.
    mediado = await _conversa_ml(
        db, integ, canais["pos_venda"], externo_id="pack-mediado", pedido_marketplace="2000003",
        dados={"pack_id": "2000003", "claim_ids": ["5001"]},
    )  # fmt: skip
    g_med = await _turno(db, mediado, _sp(d03, 12), "Cadê o reembolso?", "Estamos vendo.")
    recl = await _conversa_ml(db, integ, canais["reclamacao"], externo_id="recl-1")
    await _turno(db, recl, _sp(d03, 10), "Quero o meu dinheiro de volta", "Vamos verificar.")
    # A conversa de um eval anterior: um turno NOVO dela não entra com --excluir-conversas.
    velha = await _conversa_ml(
        db, integ, canais["pos_venda"], externo_id="pack-velho", pedido_marketplace="2000002"
    )
    g_velha = await _turno(db, velha, _sp(d03, 11), "E a nota fiscal?", "Segue anexa.")
    anterior = tmp_path / "amostraD.jsonl"
    amostra.gravar_jsonl(
        anterior, [{"id": "x", "conversa_id": str(velha.id), "gatilho_id": "outro-turno"}]
    )
    return integ, {"pos": g_pos, "perg": g_perg, "med": g_med, "velha": g_velha}, anterior


def _sem_loja(monkeypatch) -> list[str]:
    """Qualquer ida à loja do ML (o cliente) ou HTTP de saída quebra o teste."""
    idas: list[str] = []

    def _cliente(self, *a: Any, **kw: Any) -> None:
        idas.append("cliente_ml")
        raise AssertionError("o eval foi à loja do ML")

    async def _http(self, *a: Any, **kw: Any) -> None:
        idas.append("http")
        raise AssertionError("o eval fez HTTP de saída")

    monkeypatch.setattr(ml_client.MercadoLivreClient, "__init__", _cliente)
    monkeypatch.setattr(httpx.AsyncClient, "send", _http)
    return idas


async def test_a_corrente_do_eval_no_ml_sem_modelo_e_sem_loja(
    db: AsyncSession, make_user, ajustes, tmp_path: Path, capsys, monkeypatch
):
    integ, g, anterior = await _cenario(db, make_user, tmp_path)
    idas = _sem_loja(monkeypatch)
    saida = tmp_path / "amostra_ml.jsonl"
    args = ["--loja", "todas", "--plataforma", "ml", "--n", "10", "--conjunto", "todos"]
    args += ["--desde", "2026-11-03", "--ate", "2026-11-03"]
    args += ["--desde", "2026-10-10", "--ate", "2026-10-18"]
    args += ["--canal", "pos_venda", "--canal", "pergunta", "--excluir-conversas", str(anterior)]
    agora = _sp(date(2026, 11, 3), 20)
    assert await amostra.principal([*args, "--saida", str(saida)], agora=agora) == 0
    itens = {i["gatilho_id"]: i for i in amostra.ler_jsonl(saida)}
    # O pós-venda e a pergunta; a reclamação (outro canal) e a conversa já usada, fora.
    assert set(itens) == {str(g["pos"].id), str(g["perg"].id), str(g["med"].id)}
    pos, perg, med = (itens[str(g[k].id)] for k in ("pos", "perg", "med"))
    assert pos["canal"] == "pos_venda" and perg["canal"] == "pergunta"
    assert {pos["plataforma"], perg["plataforma"]} == {"ml"}
    # O pós-venda do ML não tem anúncio ligado: o anúncio vem do item do pedido (o retrato),
    # e o aquecimento o lê; na pergunta, o anúncio perguntado.
    assert pos["anuncio"] == "MLB456" and perg["anuncio"] == "MLB123"
    resumo = json.loads(capsys.readouterr().out)
    assert resumo["por_canal"] == {"ml:pos_venda": 2, "ml:pergunta": 1}
    assert resumo["canais"] == ["pergunta", "pos_venda"]
    assert resumo["conversas_excluidas_conhecidas"] == 1
    # Sem o --excluir-conversas, a conversa velha entra (o turno dela é outro).
    outra = tmp_path / "sem_excluir.jsonl"
    sem = [a for a in args if a not in ("--excluir-conversas", str(anterior))]
    assert await amostra.principal([*sem, "--saida", str(outra)], agora=agora) == 0
    assert str(g["velha"].id) in {i["gatilho_id"] for i in amostra.ler_jsonl(outra)}
    capsys.readouterr()
    # Nenhum texto nem nome do cliente no arquivo.
    texto = saida.read_text()
    assert "rodinha" not in texto and "Joana" not in texto

    # O aquecimento: os anúncios do item do pedido e da pergunta; sem a loja, só o cache.
    lista = preaquecer.anuncios_distintos(itens.values())
    assert set(lista) == {("ml", str(integ.id), "MLB456"), ("ml", str(integ.id), "MLB123")}
    aquecido = await preaquecer.aquecer(list(itens.values()), sem_loja=True)
    assert aquecido["anuncios"] == 2 and aquecido["erros"] == 0 and not aquecido["foi_a_loja"]

    # O rodar (Sonnet, como o braço S1, no automático simulado) com o modelo FALSO.
    monkeypatch.setattr(ajustes, "atendimento_agente_api_key", "sk-ant-teste-falsa")
    monkeypatch.setattr(replay, "janela_de_postagem", lambda agora=None: None)
    api = _Api(
        [
            _final(
                texto="Olá! Obrigado pela mensagem, já vou te ajudar.",
                assunto="saudacao_ou_vaga",
                evidencias=[],
            )
            for _ in range(4)
        ]
    )
    monkeypatch.setattr(
        replay, "cliente_do_replay", lambda chave, captura=None, **kw: api.cliente(captura)
    )
    destino = tmp_path / "base"
    codigo = await rodar.principal(
        ["--amostra", str(saida), "--saida", str(destino), "--braco", "S1", "--modo", "auto",
         "--concorrencia", "1", "--teto-usd", "5"],
        agora=datetime(2026, 11, 3, 9, 0, tzinfo=UTC),
        eco=io.StringIO(),
    )  # fmt: skip
    assert codigo == rodar.OK
    regs = {r["id"]: r for r in rodar.ler_resultados(destino / "resultados_S1.jsonl")}
    assert set(regs) == {pos["id"], perg["id"], med["id"]}
    # O rodar grava a reclamação aberta da conversa (o regatilho não lê o banco).
    assert regs[med["id"]]["reclamacao_aberta"] is True and not regs[med["id"]].get("erro")
    for item in (pos, perg):
        r = regs[item["id"]]
        assert not r.get("erro") and r["parada"] == "fim", r.get("erro")
        assert r["canal"] == item["canal"] and r["plataforma"] == "ml"
        # O pacote leva a descrição do canal do ML (a pergunta é pública).
        assert ia._CANAL_DESCRICAO[("ml", item["canal"])] in r["rastro"]["pacote"]
        assert "Joana" not in json.dumps(r, ensure_ascii=False)
        assert "reclamacao_aberta" not in r
    assert len(api.pedidos) in (2, 3)
    assert idas == []  # nenhuma ida à loja nem HTTP

    # A régua da Kia congelada (o config_canal) e o regatilho com ela, sem o verificador.
    _integ_kia, kia = await _loja(
        db, make_user, modo="auto", auto_categorias=KIA_AUTO["auto_categorias"],
        auto_agente=KIA_AUTO["auto_agente"],
    )  # fmt: skip
    config = tmp_path / "config_ml.json"
    assert await config_canal.principal(["--canal", str(kia.id), "--saida", str(config)]) == 0
    congelada = json.loads(config.read_text())
    assert congelada["modo"] == "auto" and congelada["auto_agente"] == KIA_AUTO["auto_agente"]
    assert congelada["de_canal"] == str(kia.id)
    capsys.readouterr()
    saida_reg = tmp_path / "regatilho_ml.jsonl"
    rc = await regatilho.main(
        ["--resultados", str(destino / "resultados_S1.jsonl"), "--saida", str(saida_reg),
         "--config-canal-json", str(config), "--resumo-so"]
    )  # fmt: skip
    assert rc == 0
    linhas = [json.loads(x) for x in saida_reg.read_text().splitlines()]
    res = linhas[-1]["resumo"]
    assert set(res["por_canal"]) == {"ml:pos_venda", "ml:pergunta"}
    assert {x["canal"] for x in linhas[:-1]} == {"pos_venda", "pergunta"}
    (do_med,) = [x for x in linhas[:-1] if x["id"] == med["id"]]
    assert do_med["decisao"].startswith("pessoa:"), do_med
    # O canal do ML em "humano" ficaria de fora; com a régua da Kia, o canal não segura.
    assert all(x["decisao"] != "pessoa:canal_fora_do_auto" for x in linhas[:-1]), linhas
    assert all(x["decisao"] not in ("erro", "pulado") for x in linhas[:-1]), linhas
    assert "por_canal:" in capsys.readouterr().out
    assert idas == []

    # A nota leva o canal (a régua da nota do ML é outra).
    registros, _chave = exportar.montar(
        list(itens.values()), {"S1": list(regs.values())}, controle=0, plantados=0
    )
    assert {r["canal"] for r in registros} == {"pos_venda", "pergunta"}
    assert len(registros) == 3


async def test_o_canal_que_nao_existe_nao_grava_a_regua(db: AsyncSession, tmp_path: Path):
    destino = tmp_path / "config.json"
    rc = await config_canal.principal(
        ["--canal", "00000000-0000-4000-8000-000000000000", "--saida", str(destino)]
    )
    assert rc == config_canal.SEM_CANAL and not destino.exists()


def test_congelar_a_regua_troca_so_o_modo():
    agora = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    canal = {"modo": "copiloto", "auto_categorias": ["a"], "auto_orientacao": ["b"],
             "auto_agente": ["a"]}  # fmt: skip
    c = config_canal.congelar(canal, de_canal="k", modo="auto", agora=agora)
    assert c["modo"] == "auto" and c["modo_do_canal"] == "copiloto"
    assert regatilho._canal_ns(c).auto_categorias == ["a"]
    assert config_canal.congelar(canal, de_canal="k", modo=None, agora=agora)["modo"] == "copiloto"
