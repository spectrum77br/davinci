"""scripts/trocar_sku_anuncios.py com clientes FALSOS (sem rede): o vínculo
(religar) e o envio de estoque (enfileirar_sync) são trocados por gravadores.
Um bloco por defeito da revisão adversarial de 30/09/2026."""

from __future__ import annotations

import copy
import json
import os
import re
import types
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import scripts.trocar_sku_anuncios as S  # noqa: N812
from app.models import IntegrationPlatform
from app.services import troca_sku_anuncio as ts


@pytest.fixture(autouse=True)
def mundo(monkeypatch):
    async def _zero(_s):
        return None

    monkeypatch.setattr(S, "_dormir", _zero)
    religados: list = []
    syncs: list = []

    async def _religar(linha, sku_novo, *, desfazendo):
        religados.append((linha.external_id, linha.variation_id, sku_novo, desfazendo))
        return {lid: "religado" for lid, _de, _para in linha.links}

    async def _sync(pid, lids, *, origem):
        syncs.append((pid, lids))
        return {"ok": True, "job_id": f"J-{pid}", "arq_job_id": "A1"}

    monkeypatch.setattr(S, "religar", _religar)
    monkeypatch.setattr(S, "enfileirar_sync", _sync)
    return types.SimpleNamespace(religados=religados, syncs=syncs)


def _args(**kw):
    base = {
        "pausa": 0.0, "pausa_leitura": 0.0, "ml_incluir_waiting_for_patch": False,
        "tiktok_incluir_reprovados": False, "tiktok_seguir": False,
        "aceitar_lote_irmao": False, "incluir_ferias": False, "sync_a_cada": 20,
        "max_erros_seguidos": 5,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _ex(tmp_path: Path, modo="executar", indice=None, **kw) -> S.Execucao:
    n = len(list(tmp_path.glob("*.jsonl")))
    return S.Execucao(_args(**kw), modo, tmp_path / f"{modo}-{n}.jsonl", indice=indice)


def _linha(plat, ext, var, esp, novo, iid="I1"):
    return ts.PlanoLinha(plat, iid, "c1", ext, var, esp, novo,
                         links=[(f"L-{var}", "P-sp", "P-novo")])


def _log(ex: S.Execucao) -> list[dict]:
    return [json.loads(x) for x in ex.log_path.read_text().splitlines() if x.strip()]


class _R:
    def __init__(self, status, body):
        self.status_code = status
        self._b = body
        self.text = json.dumps(body)

    def json(self):
        return self._b


# ------------------------------------------------------------------ ML falso


def _ml_item(sku="dg010.sp", scf=None):
    return {
        "id": "MLB1", "status": "active", "pictures": [{"id": "p1"}],
        "variations": [
            {"id": 11, "attributes": [{"id": "SELLER_SKU", "value_name": sku},
                                      {"id": "GTIN", "value_name": "789"}],
             "attribute_combinations": [{"id": "COLOR", "value_name": "Preto"}],
             "picture_ids": ["p1"], "available_quantity": 3, "price": 10,
             "seller_custom_field": scf},
        ],
    }


class FakeML:
    def __init__(self, item, *, atraso=0, put_status=200, put_body="item", efeito=None,
                 log_path=None):
        self.item = item
        self.creds = {"access_token": "x"}
        self.puts: list = []
        self.atraso = atraso  # quantas releituras ainda mostram o anúncio de antes
        self.antigo = None
        self.put_status = put_status
        self.put_body = put_body
        self.efeito = efeito
        self.log_path = log_path
        self.log_na_escrita = None

    async def _request(self, method, path, params=None, json=None):
        if method == "GET":
            if self.antigo is not None and self.atraso > 0:
                self.atraso -= 1
                return _R(200, copy.deepcopy(self.antigo))
            return _R(200, copy.deepcopy(self.item))
        if self.log_path is not None:
            self.log_na_escrita = Path(self.log_path).read_text()
        self.puts.append(json)
        if self.put_status >= 400:
            return _R(self.put_status, {"message": "erro"})
        self.antigo = copy.deepcopy(self.item)
        for v in json.get("variations") or []:
            for iv in self.item["variations"]:
                if iv["id"] == v["id"] and "attributes" in v:
                    iv["attributes"] = [a for a in iv["attributes"] if a["id"] != "SELLER_SKU"]
                    iv["attributes"] += v["attributes"]
        if self.efeito:
            self.efeito(self.item)
        body = copy.deepcopy(self.item) if self.put_body == "item" else self.put_body
        return _R(200, body)


# 3 + 4) linha 'escrevendo' com fsync ANTES da escrita; estoque enfileirado


async def test_ml_escrevendo_antes_da_escrita_e_sync_do_produto_novo(tmp_path, mundo):
    ex = _ex(tmp_path)
    item = _ml_item()
    ml = FakeML(copy.deepcopy(item), log_path=ex.log_path)
    await S.processar_ml(ex, ml, [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.pi")])

    na_escrita = [json.loads(x) for x in ml.log_na_escrita.splitlines()]
    assert [r["resultado"] for r in na_escrita] == [ts.ESCREVENDO]
    esc = na_escrita[0]
    assert (esc["sku_antes"], esc["sku_novo"], esc["chave"]) == ("dg010.sp", "dg010.pi", "11")
    assert esc["payload"] == ml.puts[0]
    assert esc["antes_json"] == item  # o JSON COMPLETO lido antes

    fim = ex.registros[-1]
    assert (fim["resultado"], fim["confirmado_por"]) == (ts.TROCADO, "releitura")
    assert mundo.religados == [("MLB1", "11", "dg010.pi", False)]
    await ex.sync_talvez(forcar=True)
    assert mundo.syncs == [("P-novo", ["L-11"])]
    sync = ex.registros[-1]
    assert (sync["tipo"], sync["resultado"], sync["job_id"]) == ("sync", ts.SYNC_ENFILEIRADO,
                                                                 "J-P-novo")
    assert ts.resumir(ex.registros)["sync_estoque"][ts.SYNC_ENFILEIRADO] == 1


async def test_sync_junta_por_produto_a_cada_n_anuncios(tmp_path, mundo):
    ex = _ex(tmp_path, sync_a_cada=2)
    ln1, ln2 = _linha("ml", "MLB1", "1", "a.sp", "a.ci"), _linha("ml", "MLB2", "2", "a.sp", "a.ci")
    ex._guardar_sync(ln1, {"L-1": "religado"})
    ex._anuncios_desde_sync = 1
    await ex.sync_talvez()
    assert mundo.syncs == []  # ainda não chegou em 2 anúncios
    ex._guardar_sync(ln2, {"L-2": "religado"})
    ex._guardar_sync(ln2, {"L-x": "ja_estava"})  # só o que foi religado
    ex._anuncios_desde_sync = 2
    await ex.sync_talvez()
    assert mundo.syncs == [("P-novo", ["L-1", "L-2"])]


# 2) ML: releitura até 3 vezes / corpo do PUT; nao_confirmado entra no desfazer


async def test_ml_releitura_atrasada_ainda_confirma(tmp_path):
    ex = _ex(tmp_path)
    ml = FakeML(_ml_item(), atraso=2, put_body={})  # 2 releituras velhas, PUT sem corpo
    await S.processar_ml(ex, ml, [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.pi")])
    assert (ex.registros[-1]["resultado"], ex.registros[-1]["confirmado_por"]) == (
        ts.TROCADO, "releitura")


async def test_ml_confirma_pelo_corpo_do_put(tmp_path):
    ex = _ex(tmp_path)
    ml = FakeML(_ml_item(), atraso=3)  # as 3 releituras velhas; o PUT devolve o anúncio
    await S.processar_ml(ex, ml, [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.pi")])
    assert (ex.registros[-1]["resultado"], ex.registros[-1]["confirmado_por"]) == (
        ts.TROCADO, "resposta_put")


async def test_ml_nao_confirmado_vai_para_o_desfazer(tmp_path, mundo):
    ex = _ex(tmp_path)
    ml = FakeML(_ml_item(), atraso=3, put_body={})
    await S.processar_ml(ex, ml, [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.pi")])
    assert ex.registros[-1]["resultado"] == ts.NAO_CONFIRMADO
    assert mundo.religados == []
    (d,) = ts.plano_desfazer(_log(ex))
    assert (d.variation_id, d.sku_esperado, d.sku_novo) == ("11", "dg010.pi", "dg010.sp")

    # o --desfazer relê e só escreve onde encontrar o SKU novo
    ex2 = _ex(tmp_path, modo="desfazer")
    ml2 = FakeML(ml.item)  # o anúncio (de verdade) está com o novo
    await S.processar_ml(ex2, ml2, [d])
    assert ml2.puts[0]["variations"][0]["attributes"][0]["value_name"] == "dg010.sp"
    assert ex2.registros[-1]["resultado"] == ts.TROCADO


# 5) ML: status / fotos mudaram → PARA, com o JSON de antes no log


async def test_ml_status_mudou_para_tudo(tmp_path):
    ex = _ex(tmp_path)

    def _revisao(item):
        item["status"] = "under_review"

    ml = FakeML(_ml_item(), efeito=_revisao)
    with pytest.raises(S.ParadaSegurancaError):
        await S.processar_ml(ex, ml, [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.pi")])
    res = [r["resultado"] for r in _log(ex)]
    assert res == [ts.ESCREVENDO, ts.DIVERGENTE]
    assert "status_mudou: active → under_review" in ex.registros[-1]["erro"]
    assert _log(ex)[0]["antes_json"]["status"] == "active"


async def test_ml_fotos_da_variacao_sumiram_para_tudo(tmp_path):
    ex = _ex(tmp_path)

    def _sem_foto(item):
        item["variations"][0]["picture_ids"] = []

    with pytest.raises(S.ParadaSegurancaError, match="fotos_mudaram"):
        await S.processar_ml(ex, FakeML(_ml_item(), efeito=_sem_foto),
                             [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.pi")])


# 11) dry-run lista seller_custom_field .sp


async def test_ml_dry_run_conta_seller_custom_field_sp(tmp_path):
    ex = _ex(tmp_path, modo="dry-run")
    ml = FakeML(_ml_item(scf="dg010.sp"))
    await S.processar_ml(ex, ml, [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.pi")])
    assert ml.puts == []
    r = ex.registros[-1]
    assert (r["resultado"], r["seller_custom_field_sp"], r["seller_custom_field"]) == (
        ts.TROCARIA, True, "dg010.sp")
    assert ts.resumir(ex.registros)["ml_seller_custom_field_sp"]["n"] == 1


# 7) lote irmão só quando o SKU lido não existe ativo


async def test_ml_lote_irmao_respeita_o_indice(tmp_path):
    idx = ts.IndiceProdutos([ts.ProdutoInfo("1", "dg010.pi", "A")])
    ex = _ex(tmp_path, modo="dry-run", indice=idx, aceitar_lote_irmao=True)
    # anúncio já num lote válido e ativo: não mexe
    await S.processar_ml(ex, FakeML(_ml_item(sku="dg010.pi")),
                         [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.ci")])
    assert ex.registros[-1]["resultado"] == ts.SKU_INESPERADO
    # lote que não existe no DaVinci (caso Barbosa): troca
    await S.processar_ml(ex, FakeML(_ml_item(sku="dg010.ra")),
                         [_linha("ml", "MLB1", "11", "dg010.sp", "dg010.ci")])
    assert ex.registros[-1]["resultado"] == ts.TROCARIA


async def test_main_lote_irmao_exige_item_ou_conta():
    assert await S.main(["--dry-run", "--aceitar-lote-irmao"]) == 2
    assert await S.main(["--executar", "--plataforma", "ml", "--aceitar-lote-irmao"]) == 2


# ------------------------------------------------------------------ Shopee falsa


class FakeShopee:
    def __init__(self, models, *, status="NORMAL", efeito=None, erro_escrita=None):
        self.models = models
        self.status = status
        self.creds = {"access_token": "x"}
        self.posts: list = []
        self.efeito = efeito
        self.erro_escrita = erro_escrita

    async def refresh(self):  # o script nunca pode chamar
        raise AssertionError("refresh chamado")

    async def _request(self, method, path, params=None, json=None):
        if path.endswith("get_item_base_info"):
            return _R(200, {"error": "", "response": {"item_list": [
                {"item_id": 1, "item_status": self.status}]}})
        if path.endswith("get_model_list"):
            return _R(200, {"error": "", "response": {"model": copy.deepcopy(self.models)}})
        self.posts.append(json)
        if self.erro_escrita:
            return _R(200, {"error": self.erro_escrita, "message": "token"})
        for m in json["model"]:
            for mm in self.models:
                if mm["model_id"] == m["model_id"]:
                    mm["model_sku"] = m["model_sku"]
        if self.efeito:
            self.efeito(self)
        return _R(200, {"error": "", "response": {}})


def _modelo(sku="dg010.sp", preco=10):
    return {"model_id": 149, "model_sku": sku,
            "price_info": [{"current_price": preco, "original_price": 20}]}


async def test_shopee_troca_e_confere(tmp_path, mundo):
    ex = _ex(tmp_path)
    sh = FakeShopee([_modelo()])
    await S.processar_shopee(ex, sh, [_linha("shopee", "58", "149", "dg010.sp", "dg010.pi")])
    assert [r["resultado"] for r in ex.registros] == [ts.ESCREVENDO, ts.TROCADO]
    assert ex.registros[0]["antes_json"]["modelos"][0]["model_sku"] == "dg010.sp"
    assert ex.registros[0]["antes_json"]["item"]["item_status"] == "NORMAL"


@pytest.mark.parametrize(
    "efeito,trecho",
    [
        (lambda sh: sh.models[0]["price_info"][0].update(current_price=9), "preco_mudou"),
        (lambda sh: setattr(sh, "status", "UNLIST"), "status_mudou"),
    ],
)
async def test_shopee_preco_ou_status_mudou_para_tudo(tmp_path, efeito, trecho):
    ex = _ex(tmp_path)
    with pytest.raises(S.ParadaSegurancaError, match=trecho):
        await S.processar_shopee(ex, FakeShopee([_modelo()], efeito=efeito),
                                 [_linha("shopee", "58", "149", "dg010.sp", "dg010.pi")])


# 9) token vencido: conta_sem_acesso, sem refresh


async def test_shopee_token_vencido_na_escrita_nao_renova(tmp_path):
    ex = _ex(tmp_path)
    sh = FakeShopee([_modelo()], erro_escrita="error_auth")
    await S.processar_shopee(ex, sh, [_linha("shopee", "58", "149", "dg010.sp", "dg010.pi")])
    assert ex.registros[-1]["resultado"] == ts.CONTA_SEM_ACESSO
    assert "I1" in ex.contas.sem_acesso
    assert ex.erros_seguidos == 0  # não conta como erro de escrita


async def test_cliente_nunca_renova_token_e_volta_quando_o_worker_renova(monkeypatch):
    creds = {"access_token": "t1", "refresh_token": "r1", "client_id": "c",
             "client_secret": "s", "expires_at": 1}  # vencido
    iid = str(uuid.uuid4())

    async def _ler(self, _iid):
        return (IntegrationPlatform.ML, dict(creds), uuid.UUID(iid), False)

    monkeypatch.setattr(S.Contas, "_ler_integ", _ler)
    contas = S.Contas(escrever=True)
    c = await contas.cliente(iid)
    with pytest.raises(S.RefreshBloqueadoError):
        await c.refresh()
    r = await S.ml_ler(c, "MLB1")  # vencido → refresh bloqueado → 401, sem rede
    assert r.status == 401 and S._falha_de_token(r)
    contas.marcar_sem_acesso(iid, "ml 401")
    assert not await contas.token_renovado(iid)
    creds["access_token"] = "t2"  # noqa: S105 — o worker renovou
    assert await contas.token_renovado(iid)
    assert iid not in contas.sem_acesso


@asynccontextmanager
async def _trava_ocupada():
    yield False


async def test_segunda_rodada_executar_sai_com_4(monkeypatch):
    monkeypatch.setattr(S, "trava_execucao", _trava_ocupada)
    assert await S.main(["--executar", "--item", "MLB1"]) == 4
    assert await S.main(["--desfazer", "x.jsonl", "--item", "MLB1"]) == 4


# ------------------------------------------------------------------ TikTok falso


def _tk(status="ACTIVATE", audit="APPROVED", skus=(("1", "dg073.pi"), ("2", "dg090.sp"))):
    return {
        "status": status,
        "audit": {"status": audit},
        "skus": [{"id": sid, "seller_sku": sku, "price": {"amount": "10"},
                  "inventory": [{"quantity": 2}]} for sid, sku in skus],
    }


class FakeTK:
    """Guarda a versão no ar e a mais recente. `auditoria=True`: a edição vai
    para a versão recente (em auditoria) e a no ar não muda."""

    def __init__(self, no_ar, recente=None, *, auditoria=True):
        self.no_ar = no_ar
        self.recente = recente if recente is not None else copy.deepcopy(no_ar)
        self.auditoria = auditoria
        self.creds = {"access_token": "x"}
        self.gets: list = []
        self.posts: list = []

    async def _get(self, path, params=None):
        self.gets.append(params)
        p = self.recente if (params or {}).get("return_under_review_version") else self.no_ar
        return {"code": 0, "data": copy.deepcopy(p)}

    async def _post(self, path, body):
        self.posts.append(body)
        novo = {s["id"]: s.get("seller_sku") for s in body["skus"]}
        alvo = self.recente
        alvo["skus"] = [{**s, "seller_sku": novo[s["id"]]} for s in alvo["skus"]
                        if s["id"] in novo]  # SKU fora do payload é APAGADO
        if self.auditoria:
            alvo["audit"] = {"status": "AUDITING"}
            self.no_ar["audit"] = {"status": "AUDITING"}
        else:
            self.no_ar = copy.deepcopy(alvo)
        return {"code": 0, "data": {"product_id": "P"}}


async def test_tiktok_em_auditoria_nao_religa_e_segura_o_resto(tmp_path, mundo):
    ex = _ex(tmp_path)
    tk = FakeTK(_tk())
    await S.processar_tiktok(ex, tk, [_linha("tiktok", "1736", "2", "dg090.sp", "dg090.pi")])
    assert {"return_under_review_version": "true"} in tk.gets  # lê a versão em auditoria
    assert ex.registros[-1]["resultado"] == ts.PENDENTE_AUDITORIA
    assert "auditoria" in ex.registros[-1]["erro"]
    assert mundo.religados == [] and ex.tiktok_piloto == "1736"

    # o 2º produto espera o piloto
    tk2 = FakeTK(_tk())
    await S.processar_tiktok(ex, tk2, [_linha("tiktok", "1737", "2", "dg090.sp", "dg090.pi")])
    assert ex.registros[-1]["resultado"] == ts.TIKTOK_AGUARDANDO_PILOTO
    assert tk2.gets == [] and tk2.posts == []

    ex.args.tiktok_seguir = True
    await S.processar_tiktok(ex, tk2, [_linha("tiktok", "1737", "2", "dg090.sp", "dg090.pi")])
    assert ex.registros[-1]["resultado"] == ts.PENDENTE_AUDITORIA


async def test_tiktok_aprovado_na_hora_religa(tmp_path, mundo):
    ex = _ex(tmp_path)
    tk = FakeTK(_tk(), auditoria=False)
    await S.processar_tiktok(ex, tk, [_linha("tiktok", "1736", "2", "dg090.sp", "dg090.pi")])
    assert ex.registros[-1]["resultado"] == ts.TROCADO
    assert mundo.religados == [("1736", "2", "dg090.pi", False)]
    assert ex.tiktok_piloto is None


async def test_tiktok_reprovado_monta_payload_da_versao_recente(tmp_path):
    no_ar = _tk(audit="REJECTED")
    recente = _tk(audit="REJECTED", skus=(("1", "dg073.pi"), ("2", "dg090.sp"), ("3", "x.pi")))
    ex = _ex(tmp_path, modo="dry-run", tiktok_incluir_reprovados=True)
    await S.processar_tiktok(ex, FakeTK(no_ar, recente),
                             [_linha("tiktok", "1735", "2", "dg090.sp", "dg090.pi")])
    r = ex.registros[-1]
    assert r["resultado"] == ts.TROCARIA
    assert [s["id"] for s in r["payload"]["skus"]] == ["1", "2", "3"]  # o SKU 3 não é apagado
    assert r["skus_so_numa_versao"] == ["3"]
    # produto aprovado: a "versão em auditoria" é cópia velha (piloto de 30/09)
    # e fica fora da conta — vale a versão no ar
    ex2 = _ex(tmp_path, modo="dry-run")
    await S.processar_tiktok(ex2, FakeTK(_tk(), _tk(skus=(("2", "dg090.sp"), ("3", "x")))),
                             [_linha("tiktok", "1735", "2", "dg090.sp", "dg090.pi")])
    assert ex2.registros[-1]["resultado"] == ts.TROCARIA


async def test_tiktok_desfazer_pela_versao_em_auditoria(tmp_path):
    ex = _ex(tmp_path)
    tk = FakeTK(_tk())
    await S.processar_tiktok(ex, tk, [_linha("tiktok", "1736", "2", "dg090.sp", "dg090.pi")])
    (d,) = ts.plano_desfazer(_log(ex))
    ex2 = _ex(tmp_path, modo="desfazer")
    await S.processar_tiktok(ex2, tk, [d])
    # a versão no ar ainda tem o .sp; a recente tem o .pi → escreve o .sp de volta
    assert tk.posts[-1]["skus"][1] == {"id": "2", "seller_sku": "dg090.sp"}
    assert ex2.registros[-1]["resultado"] == ts.PENDENTE_AUDITORIA


# 6) log com segundos + pid, exclusivo


def test_log_com_segundos_pid_e_exclusivo(tmp_path):
    p = S._novo_log(tmp_path, "executar")
    assert re.fullmatch(rf"\d{{8}}-\d{{6}}-{os.getpid()}\.jsonl", p.name)
    assert S._novo_log(tmp_path, "dry-run").name.endswith(f"-{os.getpid()}-dry-run.jsonl")
    S.Execucao(_args(), "executar", p).fechar()
    with pytest.raises(FileExistsError):
        S.Execucao(_args(), "executar", p)


# 8) férias e 3) erro interno depois de enviar a escrita (rodada inteira)


def _plano_fixo(linhas):
    async def _plano(args, somente_leitura):
        return list(linhas), [], ts.IndiceProdutos([])

    return _plano


async def test_conta_em_ferias_fica_de_fora(tmp_path, monkeypatch):
    iid = str(uuid.uuid4())
    monkeypatch.setattr(S, "_plano", _plano_fixo([_linha("ml", "MLB1", "11", "a.sp", "a.ci", iid)]))

    async def _ler(self, _iid):
        return (IntegrationPlatform.ML, {"access_token": "t"}, uuid.UUID(iid), True)

    monkeypatch.setattr(S.Contas, "_ler_integ", _ler)
    chamados = []

    async def _proc(ex, c, linhas):
        chamados.append(linhas[0].external_id)

    monkeypatch.setitem(S.PROCESSADORES, "ml", _proc)
    args = S._args(["--log-dir", str(tmp_path)])
    assert await S._rodar(args, "dry-run") == 0
    log = [json.loads(x) for p in tmp_path.glob("*.jsonl") for x in p.read_text().splitlines()]
    assert [r["resultado"] for r in log] == [ts.FERIAS] and chamados == []

    args = S._args(["--log-dir", str(tmp_path), "--incluir-ferias"])
    assert await S._rodar(args, "dry-run") == 0
    assert chamados == ["MLB1"]


async def test_erro_interno_depois_de_enviar_entra_no_desfazer(tmp_path, monkeypatch):
    iid = str(uuid.uuid4())
    ln = _linha("ml", "MLB1", "11", "dg010.sp", "dg010.pi", iid)
    monkeypatch.setattr(S, "_plano", _plano_fixo([ln]))

    async def _ler(self, _iid):
        return (IntegrationPlatform.ML, {"access_token": "t"}, uuid.UUID(iid), False)

    monkeypatch.setattr(S.Contas, "_ler_integ", _ler)

    async def _proc(ex, c, linhas):
        ex.escrevendo([(linhas[0], "11", "dg010.sp", {"chave": "11"})], payload={"p": 1},
                      endpoint="PUT", antes_json={"id": "MLB1"})
        raise RuntimeError("banco caiu no religar")

    monkeypatch.setitem(S.PROCESSADORES, "ml", _proc)
    args = S._args(["--executar", "--item", "MLB1", "--log-dir", str(tmp_path)])
    assert await S._rodar(args, "executar") == 3  # parada
    (log_path,) = tmp_path.glob("*.jsonl")
    log = [json.loads(x) for x in log_path.read_text().splitlines()]
    assert [r["resultado"] for r in log] == [ts.ESCREVENDO, ts.ERRO_INTERNO]
    assert (log[1]["sku_antes"], log[1]["escrita_enviada"]) == ("dg010.sp", True)
    (d,) = ts.plano_desfazer(log)
    assert (d.sku_esperado, d.sku_novo) == ("dg010.pi", "dg010.sp")
    prog = json.loads(log_path.with_name(log_path.stem + ".progresso.json").read_text())
    assert prog["estado"] == "parada" and prog["pid"] == os.getpid()


async def test_tiktok_aprovado_usa_a_versao_no_ar(tmp_path):
    """Piloto de 30/09: a troca de seller_sku entrou direto no ar (ACTIVATE,
    auditoria APPROVED) e a "versão em auditoria" devolvida pela TikTok era a
    cópia velha, ainda .sp. Sem análise pendente, vale a versão no ar."""
    no_ar = _tk(audit="APPROVED", skus=(("1", "dg073.pi"), ("2", "dg090.pi")))
    copia_velha = _tk(audit="APPROVED", skus=(("1", "dg073.pi"), ("2", "dg090.sp")))
    ex = _ex(tmp_path, modo="dry-run")
    await S.processar_tiktok(ex, FakeTK(no_ar, copia_velha),
                             [_linha("tiktok", "1735", "2", "dg090.sp", "dg090.pi")])
    assert ex.registros[-1]["resultado"] == ts.JA_TROCADO

