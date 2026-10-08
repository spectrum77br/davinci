"""Item 4, fase 4b — as SUGESTÕES DE TROCA do pedido em falta de estoque (05/10/2026).

A regra do Eduardo (`services/atendimento/troca_sugestoes.py`): o mesmo
produto de outro lote (nível 0, só quando o robô de lote já faria o mesmo),
outra cor do MESMO modelo (nível 1) e outro modelo com a MESMA
especificação (nível 2) se o NOSSO custo subir no máximo 5% — o cliente
paga o mesmo. O que estes testes seguram:

- as CHAVES: linha + especificação do celular (12.64+64 ≠ 12.64), o código
  M/P/ME e o tamanho da mala, a voltagem do eletro, o 5G que só está no
  nome da linha (WP60), o AVULSO e o que fica fora (salvado, `z*`,
  `fake.`, kit com lotes misturados, situação que não é 'A', NULL inclusive);
- o TAMANHO da mala: `.10` = `.8` só com a prova da Tabela de Preços (a
  "ABS 8"); a P5 de 16" não vira a de 14" sem essa prova;
- a CALIBRAÇÃO: as 16 trocas feitas à mão de 02/09 a 02/10/2026 são
  aceitas pela regra, no nível esperado (13 no 1, 3 no 2), com o catálogo
  do retrato de 02/10 (`tests/fixtures/troca_catalogo_0210.json`), e nada
  pior que a escolha da equipe vem antes dela. O estoque de hoje não é o
  do dia da troca: 12 das 16 ficam entre as 3 primeiras com o estoque de
  hoje; nas outras 4, opções do mesmo nível ou melhores voltaram a ter
  peça (`FORA_DAS_3_PELO_ESTOQUE_DE_HOJE`);
- o TETO de 5% (pela soma e pelo custo do próprio SKU, a trava do kit com
  óculos), o PISO de −10% no nível 2, o custo do kit com componente a
  custo zero e o original sem custo;
- a ORDEM (com estoque, nível, custo, estoque) e as de fora esmaecidas;
- o nível 0 só com a prioridade de lote ou o redirecionamento ligado
  (crítica M1), nunca `.us`/`.cd`;
- o TEXTO da oferta: sem número, sem prazo, sem margem;
- o catálogo do banco (3 consultas, memória de 10 min) e o bloco do
  pedido (`sugestoes_do_pedido`): o SKU em falta que saiu do pedido vira
  aviso, e o % do custo só para quem vê a Margem.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.models import BlingKitComponent, PricingProduct, Product, Segment
from app.services.atendimento import troca_sugestoes as ts
from app.services.atendimento.constantes import TEXTO_OFERTA_MESMO_PRODUTO, TEXTO_OFERTA_TROCA
from app.services.atendimento.troca_sugestoes import (
    APPLE,
    CELULAR,
    ELETRO,
    FORA_CUSTO_ABAIXO_PISO,
    FORA_CUSTO_ACIMA,
    FORA_SEM_ESTOQUE,
    MALA,
    NIVEL_ESPECIFICACAO,
    NIVEL_LOTE,
    NIVEL_MODELO,
    Sugestao,
    chaves,
    montar_catalogo,
    sugerir,
    texto_oferta,
)

FIXTURE = Path(__file__).parent / "fixtures" / "troca_catalogo_0210.json"
DADOS = json.loads(FIXTURE.read_text(encoding="utf-8"))
UPD = "2026-10-02T12:00:00+00:00"


def _catalogo_0210(**kw):
    return montar_catalogo(DADOS["produtos"], DADOS["kits"], DADOS["linhas"], **kw)


@pytest.fixture(scope="module")
def cat():
    return _catalogo_0210()


def _prod(sku, name, custo, stock=10, *, formato="S", situacao="A", bid=None):
    return {
        "sku": sku,
        "name": name,
        "formato": formato,
        "situacao": situacao,
        "stock": stock,
        "custo": custo,
        "bid": bid,
        "upd": UPD,
    }


def _linha(id_, nome, skus):
    return {"id": id_, "sku": ",".join(skus), "name": nome, "ativo": True}


def _skus(lista: list[Sugestao]) -> list[str]:
    return [s.sku for s in lista]


# ─────────────── chaves ───────────────


def test_chaves_celular_linha_e_especificacao():
    linha = ("L1", "uranyx A17 pro max 12.64 prata/preto/laranja - USM003")
    k = chaves("DG057.ci+A001.ci", "Uranyx A17 Pro Max 12.64 - Branco + Fone", "e", linha=linha)
    assert (k["sku"], k["lote"], k["main"], k["acess"]) == (
        "dg057.ci+a001.ci",
        "ci",
        "dg057",
        ("a001",),
    )
    assert (k["fam"], k["modelo"], k["spec"], k["formato"]) == (CELULAR, "L1", "12.64", "E")
    # O nome do modelo sem a cor e sem o acessório (o nível 1 sem linha).
    assert k["nome_modelo"] == "uranyx a17 pro max 12.64"
    assert (k["g5"], k["avulso"], k["descartado"]) == (False, False, False)
    # RAM.ROM + expansão é OUTRA especificação.
    mais = chaves("dg099.ci", "Uranyx X 12.64+64 - Preto", "S")
    assert mais["spec"] == "12.64.64" != k["spec"]
    # "4+64" e "4.64" são a mesma especificação.
    assert chaves("dg1.ci", "Uranyx C2 4+64 Azul", "S")["spec"] == "4.64"
    # O nome do aparelho (o simples do mesmo lote) vence o nome do kit.
    k2 = chaves("dg057.ci+a001.ci", "Kit sem spec", "E", nome_aparelho="A17 Pro Max 12.64 - Branco")
    assert k2["spec"] == "12.64"
    # Apple pela mesma regra (sem nível 2).
    assert chaves("i223.sa", 'Macbook Neo 8.256 13,6" - Rosa', "S")["fam"] == APPLE


def test_chaves_mala_codigo_e_tamanho():
    k = chaves("b036.12.18", "Kit Malas Chanfrada M3 tamanho 12.18 Polegadas - Roxo Escuro", "E")
    assert (k["fam"], k["modelo"], k["spec"], k["main"], k["lote"]) == (
        MALA,
        "M3",
        "12.18",
        "b036.12.18",
        None,
    )
    # O tamanho é número, não lote (`.10` não é sufixo de estoque), e fica
    # como está no SKU: os nomes iguais só com a prova da Tabela de Preços
    # (`test_mala_tamanho_igual_so_com_a_prova_da_tabela_de_precos`).
    assert chaves("b026.10", "Mala Chanfrada M3 tamanho 10 - Prata", "S")["spec"] == "10"
    assert chaves("b053.8", "Mala Sorriso M6 tamanho 8 - Cinza", "S")["spec"] == "8"
    assert chaves("b1.16", "Mala P5 tamanho 16", "S")["spec"] == "16"
    assert chaves("b045.12.14.16", "Kit Malas P5 tamanho 12.14.16", "E")["spec"] == "12.14.16"
    assert chaves("b097.12", "Mala Seta P5 tamanho 12 - Azul", "S")["modelo"] == "P5"
    assert chaves("b200.12", "Mala Executiva ME2 tamanho 12", "S")["modelo"] == "ME2"
    # Sem tamanho = o kit de 6.
    assert chaves("b027", "Kit 6 Malas chanfradas M3 - Branca", "E")["spec"] == ""


def test_chaves_eletro_voltagem():
    k = chaves("uaf001m1.110", "Air Fryer UAF001 Preta 110V", "S")
    assert (k["fam"], k["modelo"], k["spec"]) == (ELETRO, "uaf001", "110")
    assert chaves("uaf001m2.220", "Air Fryer UAF001 Branca 220V", "S")["spec"] == "220"
    # O que não segue o padrão `u<tipo><nº>` fica fora das famílias.
    assert chaves("usb-c", "Cabo", "S")["fam"] is None


def test_chaves_5g_no_nome_da_linha():
    """O WP60 só é 5G no nome da LINHA: a marca olha os dois."""
    linha = ("W", "uranyx wp60 5g 36.512 cinza/amarelo/branco - USM001")
    k = chaves("dg090.pi", "Uranyx WP60 36.512 - Amarelo", "S", linha=linha)
    assert (k["g5"], k["spec"]) == (True, "36.512")
    assert chaves("dg090.pi", "Uranyx WP60 36.512 - Amarelo", "S")["g5"] is False
    assert chaves("dg018.pi", "Uranyx F109 5G 24.256 - Preto", "S")["g5"] is True


def test_chaves_avulso_e_descartados():
    assert chaves("dg1040.pi", "Uranyx C51 6+128 Roxo AVULSO", "S")["avulso"] is True
    assert chaves("dg1041.pi", "Uranyx C51 6+128 Roxo", "S")["avulso"] is False
    for sku, nome in (
        ("z0167.mala", "Mala salvado"),
        ("fake.dg057.ci", "Uranyx A17"),
        ("dg057.ci", "Uranyx A17 Pro Max 12.64 - Branco SALVADO"),
        ("dg057.ci", "Uranyx A17 avariado"),
        ("dg057.ci+a001.sp", "Kit com lotes misturados"),
        ("", ""),
    ):
        assert chaves(sku, nome, "S")["descartado"] is True, (sku, nome)


# ─────────────── calibração ───────────────

# As 4 trocas à mão que, com o estoque de 02/10, ficam ABAIXO das 3
# primeiras — e por quê (o estoque do dia da troca não é o do retrato):
#   295846 b036.12.18 → b017.12.18 (n2): as Chanfradas M3 12.18 (nível 1)
#          voltaram a ter peça (b026 63, b030 61, b033 29);
#   298713 b013.12.24 → b009.12.24 (n1): b001/b002/b003 (M2 12.24, mesmo
#          nível e custo) têm peça hoje; o b009 gastou a dele;
#   298888 b030.10 → b053.8 (n2): as M3 tamanho 10 (nível 1) têm peça hoje;
#   300321 b027 → b012 (n2): 16 kits de 6 da mesma linha a 0% — a equipe
#          escolheu um deles, a regra mostra os de mais estoque.
# Nas 4, o que vem antes é do MESMO nível ou melhor e não encarece mais
# (`test_calibracao_16_trocas_de_setembro`).
FORA_DAS_3_PELO_ESTOQUE_DE_HOJE = {"295846", "298713", "298888", "300321"}


def _pela_regra(cat, sku, qtd):
    """Os parecidos que a regra aceita (nível + custo), na ordem dela, SEM a trava do estoque.

    O estoque do retrato é o de 02/10, não o do dia da troca: o produto
    trocado costuma estar zerado (gastou a peça). A ordem é a de `sugerir`
    com o "sem estoque" contando como elegível.
    """
    todas = ts.candidatos(cat, sku, qtd)
    pela_regra = [s for s in todas if s.motivo_fora in (None, FORA_SEM_ESTOQUE)]
    return sorted(
        pela_regra,
        key=lambda s: (s.nivel, not s.mesmo_produto, max(s.dif_custo_pct or 0.0, 0.0), -s.estoque),
    )


def _com_peca(sku, qtd):
    """O catálogo de 02/10 com o produto trocado COM peça (a troca prova que ele tinha)."""
    produtos = [
        {**p, "stock": max(int(p["stock"] or 0), qtd)} if p["sku"].lower() == sku else p
        for p in DADOS["produtos"]
    ]
    return montar_catalogo(produtos, DADOS["kits"], DADOS["linhas"])


@pytest.mark.parametrize("troca", DADOS["trocas"], ids=lambda t: f"{t['pedido']}-{t['original']}")
def test_calibracao_16_trocas_de_setembro(cat, troca):
    """A troca feita à mão é uma das que a regra aceita, no nível esperado; nada pior vem antes."""
    lista = _pela_regra(cat, troca["original"], troca["quantidade"])
    achou = [i for i, s in enumerate(lista) if s.sku.lower() == troca["trocado"]]
    assert achou, (troca, _skus(lista[:6]))
    pos = achou[0]
    s = lista[pos]
    assert s.nivel == troca["nivel"], (troca, s)
    # Em todas as 16 o custo foi igual ou menor (o teto nunca foi usado).
    assert s.dif_custo_pct is not None and s.dif_custo_pct <= 0
    # Antes da escolha da equipe, só o que é tão bom quanto ela: o mesmo
    # nível ou melhor, e o custo não sobe mais.
    for antes in lista[:pos]:
        assert antes.nivel <= s.nivel, (troca, antes)
        assert max(antes.dif_custo_pct or 0.0, 0.0) <= max(s.dif_custo_pct, 0.0), (troca, antes)

    # Entre as 3 primeiras de `sugerir` (com a trava do estoque), com o
    # trocado com peça — salvo as 4 em que o estoque de hoje põe opções tão
    # boas ou melhores na frente (ver acima).
    top3 = _skus(sugerir(_com_peca(troca["trocado"], troca["quantidade"]), troca["original"], 1))[
        :3
    ]
    if troca["pedido"] in FORA_DAS_3_PELO_ESTOQUE_DE_HOJE:
        assert troca["trocado"] not in top3
    else:
        assert troca["trocado"] in top3, (troca, top3)


def test_calibracao_13_no_nivel_1_e_3_no_nivel_2():
    niveis = [t["nivel"] for t in DADOS["trocas"]]
    assert (len(niveis), niveis.count(1), niveis.count(2)) == (16, 13, 3)
    assert {t["pedido"] for t in DADOS["trocas"]} >= FORA_DAS_3_PELO_ESTOQUE_DE_HOJE


# ─────────────── custo ───────────────


def test_teto_5_por_cento(cat):
    """G1 24.256 (R$ 920) → C68 Plus 24.256 (R$ 980): +6,5%, outra linha = custo_acima."""
    todas = ts.candidatos(cat, "dg011.pi", 1)
    c68 = next(s for s in todas if s.sku == "dg064.pi")
    assert (c68.nivel, c68.motivo_fora, c68.dif_custo_pct) == (
        NIVEL_ESPECIFICACAO,
        FORA_CUSTO_ACIMA,
        6.5,
    )
    # Com o teto em 7%, passa (só falta o estoque: o retrato tem 0).
    c68_7 = next(s for s in ts.candidatos(cat, "dg011.pi", 1, teto_pct=7.0) if s.sku == "dg064.pi")
    assert c68_7.motivo_fora == FORA_SEM_ESTOQUE
    # `sugerir` devolve as de fora DEPOIS das elegíveis, esmaecidas.
    assert all(s.motivo_fora is not None for s in sugerir(cat, "dg011.pi", 1))


def test_piso_nivel2(cat):
    """F109S 24.256 (R$ 1.050) → G1 24.256 (R$ 920): −12,4% no nível 2 = rebaixar o produto."""
    todas = ts.candidatos(cat, "dg017.pi", 1)
    g1 = next(s for s in todas if s.sku == "dg011.pi")
    assert (g1.nivel, g1.motivo_fora) == (NIVEL_ESPECIFICACAO, FORA_CUSTO_ABAIXO_PISO)
    assert g1.dif_custo_pct == pytest.approx(-12.4)
    # Sem o piso (−20%), o G1 com estoque vira sugestão.
    sem_piso = sugerir(cat, "dg017.pi", 1, piso_n2_pct=-20.0)
    assert sem_piso[0].sku == "dg011.pi" and sem_piso[0].motivo_fora is None


def _kits_com_oculos(custo_proprio_novo):
    """Dois kits celular + óculos do mesmo modelo: a soma igual, o custo do próprio SKU não."""
    produtos = [
        _prod("dg065.pi", "Uranyx C68 Plus 24.256 - Dourado", 980, bid=1),
        _prod("dg066.pi", "Uranyx C68 Plus 24.256 - Azul", 980, bid=2),
        _prod("a020.pi", "Óculos", 20, bid=3),
        _prod(
            "dg065.pi+a020.pi",
            "Uranyx C68 Plus 24.256 - Dourado + Óculos",
            1000,
            0,
            formato="E",
            bid=11,
        ),
        _prod(
            "dg066.pi+a020.pi",
            "Uranyx C68 Plus 24.256 - Azul + Óculos",
            custo_proprio_novo,
            5,
            formato="E",
            bid=12,
        ),
    ]
    kits = [
        {"kit": 11, "comp": 1, "q": 1},
        {"kit": 11, "comp": 3, "q": 1},
        {"kit": 12, "comp": 2, "q": 1},
        {"kit": 12, "comp": 3, "q": 1},
    ]
    linhas = [_linha("C68", "uranyx C68 plus 24.256", ["dg064", "dg065", "dg066"])]
    return montar_catalogo(produtos, kits, linhas)


def test_trava_do_custo_proprio_do_kit():
    """O kit com `a020` carimba ~R$ 360 acima da soma: a Margem vê o próprio, que também trava."""
    ok = _kits_com_oculos(1000)
    assert _skus(sugerir(ok, "dg065.pi+a020.pi", 1)) == ["dg066.pi+a020.pi"]
    assert sugerir(ok, "dg065.pi+a020.pi", 1)[0].dif_custo_pct == 0.0
    caro = _kits_com_oculos(1360)
    s = sugerir(caro, "dg065.pi+a020.pi", 1)
    assert [(x.sku, x.motivo_fora) for x in s] == [("dg066.pi+a020.pi", FORA_CUSTO_ACIMA)]
    # Pela soma o kit não encarece (a % que a tela mostra é a da soma).
    assert s[0].dif_custo_pct == 0.0


def test_kit_com_componente_a_custo_zero_fica_fora():
    """Salvar a estrutura do kit no Bling zera o custo dos componentes (crítica M10)."""
    produtos = [
        _prod("dg055.ci", "Uranyx A17 Pro Max 12.64 - Preto", 495, bid=1),
        _prod("dg056.ci", "Uranyx A17 Pro Max 12.64 - Laranja", 0, bid=2),
        _prod("a001.ci", "Fone", 4.5, bid=3),
        _prod("dg055.ci+a001.ci", "A17 Preto + Fone", 499.5, 0, formato="E", bid=11),
        _prod("dg056.ci+a001.ci", "A17 Laranja + Fone", 499.5, 9, formato="E", bid=12),
    ]
    kits = [
        {"kit": 11, "comp": 1, "q": 1},
        {"kit": 11, "comp": 3, "q": 1},
        {"kit": 12, "comp": 2, "q": 1},
        {"kit": 12, "comp": 3, "q": 1},
    ]
    linhas = [_linha("A17", "uranyx A17 pro max 12.64", ["dg055", "dg056", "dg057"])]
    cat = montar_catalogo(produtos, kits, linhas)
    assert cat.produtos["dg056.ci+a001.ci"].custo is None
    assert sugerir(cat, "dg055.ci+a001.ci", 1) == []


def test_original_sem_custo_nao_recebe_nivel_1_nem_2():
    produtos = [
        _prod("dg055.ci", "Uranyx A17 Pro Max 12.64 - Preto", None, 0),
        _prod("dg056.ci", "Uranyx A17 Pro Max 12.64 - Laranja", 495, 9),
        _prod("dg055.sp", "Uranyx A17 Pro Max 12.64 - Preto", None, 4),
    ]
    linhas = [_linha("A17", "uranyx A17 pro max 12.64", ["dg055", "dg056"])]
    cat = montar_catalogo(produtos, [], linhas)
    s = sugerir(cat, "dg055.ci", 1)
    # Só o mesmo produto em outro lote (sem trava de custo; com aceite).
    assert [(x.sku, x.nivel, x.mesmo_produto) for x in s] == [("dg055.sp", NIVEL_MODELO, True)]
    assert ts.motivo_sem_sugestao(cat, "dg055.ci").startswith("sem o custo")


def test_mala_nivel_2_com_tamanho_diferente_fica_fora():
    """A especificação vale em TODOS os níveis (crítica M9): mala do nível 2 com o mesmo tamanho."""
    produtos = [
        _prod(
            "b036.12.18",
            "Kit Malas Chanfrada M3 tamanho 12.18 Polegadas - Roxo",
            141,
            0,
            formato="E",
        ),
        _prod(
            "b017.12.18",
            "Kit Malas Listrada M1 tamanho 12.18 Polegadas - Rosa",
            131,
            7,
            formato="E",
        ),
        _prod(
            "b017.12.24",
            "Kit Malas Listrada M1 tamanho 12.24 Polegadas - Rosa",
            141,
            7,
            formato="E",
        ),
    ]
    linhas = [_linha("ABS", "ABS 12+18", ["b036.12.18", "b017.12.18", "b017.12.24"])]
    cat = montar_catalogo(produtos, [], linhas)
    assert [(s.sku, s.nivel) for s in sugerir(cat, "b036.12.18", 1)] == [
        ("b017.12.18", NIVEL_ESPECIFICACAO)
    ]


def _malas_p5(linhas, *extra):
    """P5 de 16" em falta (simples e kit 12+16), uma P5 de 16" e uma de 14" com peça."""
    produtos = [
        _prod("b097.16", "Mala Seta P5 tamanho 16 - Azul", 100, 0),
        _prod("b099.16", "Mala Seta P5 tamanho 16 - Verde", 100, 3),
        _prod("b098.14", "Mala Seta P5 tamanho 14 - Rosa", 100, 9),
        _prod("b097.12.16", "Kit Malas Seta P5 tamanho 12.16 - Azul", 200, 0, formato="E"),
        _prod("b098.12.14", "Kit Malas Seta P5 tamanho 12.14 - Rosa", 200, 9, formato="E"),
        *extra,
    ]
    return montar_catalogo(produtos, [], linhas)


def test_mala_tamanho_igual_so_com_a_prova_da_tabela_de_precos(cat):
    """`.10` = `.8` porque a "ABS 8" junta os dois; `.16` ≠ `.14` sem essa prova (revisão da 4b)."""
    # O retrato de 02/10: a única junção é a da "ABS 8" (nenhum estilo tem o `.8` E o `.10`).
    assert cat.tamanhos_iguais == {frozenset({"8", "10"})}
    # A 298888 (b030.10 → b053.8, nível 2) segue pela linha "ABS 8".
    assert ("b053.8", NIVEL_ESPECIFICACAO) in [
        (s.sku, s.nivel) for s in ts.candidatos(cat, "b030.10", 1)
    ]

    # Cada tamanho na sua linha: a P5 de 16" em falta NÃO sugere a de 14".
    separadas = [
        _linha("PP14", "PP 14", ["b098.14"]),
        _linha("PP16", "PP 16", ["b097.16", "b099.16"]),
        _linha("PP1214", "PP 12+14", ["b098.12.14"]),
        _linha("PP1216", "PP 12+16", ["b097.12.16"]),
    ]
    sep = _malas_p5(separadas)
    assert sep.tamanhos_iguais == frozenset()
    assert [(s.sku, s.nivel) for s in sugerir(sep, "b097.16", 1)] == [("b099.16", NIVEL_MODELO)]
    assert sugerir(sep, "b097.12.16", 1) == []
    # Sem linha nenhuma, só o mesmo número vale.
    assert _skus(sugerir(_malas_p5([]), "b097.16", 1)) == ["b099.16"]

    # A Tabela de Preços juntando o `.14` e o `.16` na MESMA linha é a prova.
    junta = [_linha("PP14", "PP 14", ["b098.14", "b097.16", "b099.16"])]
    j = _malas_p5(junta)
    assert j.tamanhos_iguais == {frozenset({"14", "16"})}
    assert _skus(sugerir(j, "b097.16", 1)) == ["b098.14", "b099.16"]
    # O kit 12+16 fora de linha não vira 12+14 (os dois precisam estar na mesma linha).
    assert sugerir(j, "b097.12.16", 1) == []

    # Um SKU com os dois tamanhos prova que são diferentes, mesmo com a linha juntando.
    kit = _prod("b045.12.14.16", "Kit Malas P5 tamanho 12.14.16 - Preto", 300, 1, formato="E")
    estilo = _prod("b045.14", "Mala P5 tamanho 14 - Preto", 100, 1)
    estilo16 = _prod("b045.16", "Mala P5 tamanho 16 - Preto", 100, 1)
    for prova in ((kit,), (estilo, estilo16)):
        d = _malas_p5(junta, *prova)
        assert d.tamanhos_iguais == frozenset(), prova
        assert "b098.14" not in _skus(ts.candidatos(d, "b097.16", 1)), prova


def test_eletro_sem_nivel_2():
    produtos = [
        _prod("uaf001m1.110", "Air Fryer UAF001 Preta 110V", 200, 0),
        _prod("uaf001m2.110", "Air Fryer UAF001 Branca 110V", 200, 3),
        _prod("uaf001m2.220", "Air Fryer UAF001 Branca 220V", 200, 3),
        _prod("uaf002m1.110", "Air Fryer UAF002 Preta 110V", 200, 3),
    ]
    cat = montar_catalogo(produtos, [], [])
    assert [(s.sku, s.nivel) for s in sugerir(cat, "uaf001m1.110", 1)] == [
        ("uaf001m2.110", NIVEL_MODELO)
    ]


# ─────────────── exclusões e ordem ───────────────


def test_exclui_salvado_inativo_fake_e_lote_misturado():
    linhas = [
        _linha(
            "A17",
            "uranyx A17 pro max 12.64",
            ["dg055", "dg056", "dg057", "dg058", "dg059", "dg060"],
        )
    ]
    produtos = [
        _prod("dg055.ci", "Uranyx A17 Pro Max 12.64 - Preto", 495, 0),
        _prod("dg056.ci", "Uranyx A17 Pro Max 12.64 - Laranja SALVADO", 495, 9),
        _prod("dg057.ci", "Uranyx A17 Pro Max 12.64 - Branco", 495, 9, situacao="I"),
        # Situação desconhecida (NULL) e excluída: o robô de lote também não usa.
        _prod("dg059.ci", "Uranyx A17 Pro Max 12.64 - Rosa", 495, 9, situacao=None),
        _prod("dg060.ci", "Uranyx A17 Pro Max 12.64 - Azul", 495, 9, situacao="E"),
        _prod("fake.dg058.ci", "Uranyx A17 Pro Max 12.64 - Verde", 495, 9),
        _prod("dg058.ci", "Uranyx A17 Pro Max 12.64 - Verde avariado", 495, 9),
        _prod("dg055.ci+a001.sp", "A17 Preto + Fone (lotes misturados)", 499.5, 9, formato="E"),
        _prod("z0055.ci", "Uranyx A17 Pro Max 12.64 - Preto", 495, 9),
    ]
    cat = montar_catalogo(produtos, [], linhas)
    assert ts.candidatos(cat, "dg055.ci", 1) == []
    assert (cat.produtos["dg059.ci"].ativo, cat.produtos["dg060.ci"].ativo) == (False, False)
    # O original descartado não tem sugestão nenhuma.
    assert ts.candidatos(cat, "dg055.ci+a001.sp", 1) == []
    assert ts.candidatos(cat, "fake.dg058.ci", 1) == []
    assert "fica fora da troca" in ts.motivo_sem_sugestao(cat, "dg056.ci")


def test_mesmo_formato_acessorios_e_avulso():
    linhas = [_linha("C51", "Oukitel C51 6+128 AVULSO", ["dg1004", "dg1040", "dg1041"])]
    produtos = [
        _prod("dg1040.pi", "Uranyx C51 6+128 Roxo AVULSO", 450, 0),
        _prod("dg1004.pi", "Uranyx C51 6+128 Preto AVULSO", 450, 3),
        _prod("dg1041.pi", "Uranyx C51 6+128 Azul", 450, 3),
        _prod("dg1004.pi+a020.pi", "Uranyx C51 6+128 Preto AVULSO + Óculos", 470, 3, formato="E"),
        _prod("dg1004.pi+x", "Uranyx C51 6+128 Preto AVULSO (kit)", 450, 3, formato="E"),
    ]
    cat = montar_catalogo(produtos, [], linhas)
    assert _skus(sugerir(cat, "dg1040.pi", 1)) == ["dg1004.pi"]


def test_ordem():
    """Com estoque antes; depois o nível; depois o que não encarece; depois o maior estoque."""
    linhas = [
        _linha("A17", "uranyx A17 pro max 12.64", ["dg055", "dg056", "dg057", "dg058", "dg059"]),
        _linha("X9", "uranyx X9 12.64", ["dg200", "dg201"]),
    ]
    produtos = [
        _prod("dg055.ci", "Uranyx A17 Pro Max 12.64 - Preto", 500, 0),
        _prod("dg056.ci", "Uranyx A17 Pro Max 12.64 - Laranja", 520, 50),  # n1, +4%
        _prod("dg057.ci", "Uranyx A17 Pro Max 12.64 - Branco", 500, 5),  # n1, 0%
        _prod("dg058.ci", "Uranyx A17 Pro Max 12.64 - Verde", 490, 9),  # n1, −2%
        _prod("dg059.ci", "Uranyx A17 Pro Max 12.64 - Rosa", 500, 0),  # n1, sem estoque
        _prod("dg200.ci", "Uranyx X9 12.64 - Preto", 500, 99),  # n2, 0%
        _prod("dg201.ci", "Uranyx X9 12.64 - Azul", 530, 99),  # n2, +6% (fora)
    ]
    cat = montar_catalogo(produtos, [], linhas)
    todas = sugerir(cat, "dg055.ci", 1, limite=None)
    assert [(s.sku, s.nivel, s.motivo_fora) for s in todas] == [
        # Elegíveis: nível 1 (o que não encarece, maior estoque primeiro), depois o 2.
        ("dg058.ci", NIVEL_MODELO, None),
        ("dg057.ci", NIVEL_MODELO, None),
        ("dg056.ci", NIVEL_MODELO, None),
        ("dg200.ci", NIVEL_ESPECIFICACAO, None),
        # De fora: só o estoque falta, antes do que o custo barra.
        ("dg059.ci", NIVEL_MODELO, FORA_SEM_ESTOQUE),
        ("dg201.ci", NIVEL_ESPECIFICACAO, FORA_CUSTO_ACIMA),
    ]
    # O limite: 3 elegíveis + até 3 de fora.
    assert _skus(sugerir(cat, "dg055.ci", 1)) == [
        "dg058.ci",
        "dg057.ci",
        "dg056.ci",
        "dg059.ci",
        "dg201.ci",
    ]
    # A quantidade do pedido conta: 2 peças tiram o que só tem 1.
    produtos[3] = _prod("dg058.ci", "Uranyx A17 Pro Max 12.64 - Verde", 490, 1)
    cat2 = montar_catalogo(produtos, [], linhas)
    s2 = {s.sku: s for s in sugerir(cat2, "dg055.ci", 2, limite=None)}
    assert s2["dg058.ci"].motivo_fora == FORA_SEM_ESTOQUE


def test_nivel0_lote_irmao():
    """dg057.ci+a001.ci → dg057.sp+a001.sp: só com o robô de lote ligado; nunca `.us`/`.cd`."""
    extra = [
        _prod(
            "dg057.us+a001.us",
            "Uranyx A17 Pro Max 12.64 - Branco + Fone (usado)",
            499.5,
            50,
            formato="E",
        ),
        _prod(
            "dg057.cd+a001.cd", "Uranyx A17 Pro Max 12.64 - Branco + Fone", 499.5, 50, formato="E"
        ),
    ]
    produtos = DADOS["produtos"] + extra

    # O redirecionamento por família ligado (o robô de lote mandaria sozinho): nível 0.
    ligado = montar_catalogo(
        produtos, DADOS["kits"], DADOS["linhas"], familia_ativa=True, familia_redireciona=True
    )
    s = sugerir(ligado, "dg057.ci+a001.ci", 1)
    assert (s[0].sku, s[0].nivel, s[0].mesmo_produto) == ("dg057.sp+a001.sp", NIVEL_LOTE, True)
    assert not any(
        x.sku.endswith((".us", ".cd")) for x in ts.candidatos(ligado, "dg057.ci+a001.ci", 1)
    )
    # A outra cor do mesmo modelo vem logo depois (nível 1).
    assert (s[1].sku, s[1].nivel) == ("dg056.ci+a001.ci", NIVEL_MODELO)

    # A prioridade de lote da Tabela de Preços apontando para o `sp` também libera.
    prio = montar_catalogo(produtos, DADOS["kits"], DADOS["linhas"], prioridades={"dg057": "sp"})
    assert sugerir(prio, "dg057.ci+a001.ci", 1)[0].nivel == NIVEL_LOTE

    # Prefixos da família que não pegam a linha: não libera.
    fora = montar_catalogo(
        produtos,
        DADOS["kits"],
        DADOS["linhas"],
        familia_ativa=True,
        familia_redireciona=True,
        familia_prefixos=("b",),
    )
    s_fora = {x.sku: x for x in sugerir(fora, "dg057.ci+a001.ci", 1)}
    assert s_fora["dg057.sp+a001.sp"].nivel == NIVEL_MODELO

    # Desligado (o padrão): o mesmo produto ainda aparece, mas no nível 1 (com aceite).
    desligado = montar_catalogo(produtos, DADOS["kits"], DADOS["linhas"])
    s_off = sugerir(desligado, "dg057.ci+a001.ci", 1)
    assert (s_off[0].sku, s_off[0].nivel, s_off[0].mesmo_produto) == (
        "dg057.sp+a001.sp",
        NIVEL_MODELO,
        True,
    )


# ─────────────── texto da oferta ───────────────


def test_texto_oferta_sem_numero_nem_margem():
    for modelo in (TEXTO_OFERTA_TROCA, TEXTO_OFERTA_MESMO_PRODUTO):
        assert not re.search(r"\d", modelo)
    t = texto_oferta("Mala Chanfrada - Prata", "Mala Chanfrada - Roxo Escuro (DT - DTLG115 - DT14)")
    assert not re.search(r"\d", t)
    baixo = t.lower()
    for proibida in ("margem", "lucro", "prazo", "dias", "hora", "r$", "%"):
        assert proibida not in baixo, proibida
    assert "pelo mesmo valor, sem custo a mais" in baixo
    assert "se preferir, cancelamos" in baixo
    # O código interno entre parênteses não vai ao comprador.
    assert "DTLG" not in t and "Roxo Escuro" in t and "Prata" in t
    # O mesmo produto (de outro lote) tem o texto próprio.
    mesmo = texto_oferta("Uranyx A17 - Branco", "Uranyx A17 - Branco")
    assert "outro lote" in mesmo and "cancelamos" in mesmo


def test_nivel0_nao_tem_texto(cat):
    ligado = _catalogo_0210(familia_ativa=True, familia_redireciona=True)
    bloco = ts.item_de_troca(ligado, "dg057.ci+a001.ci", 1, None, ve_custo=True)
    primeira, segunda = bloco["sugestoes"][:2]
    assert (primeira["nivel"], primeira["texto_oferta"]) == (NIVEL_LOTE, None)
    assert segunda["texto_oferta"] and "Laranja" in segunda["texto_oferta"]
    # O texto do item é o da 1ª sugestão (o nível 0 não pede aceite).
    assert bloco["texto_oferta"] is None


def test_item_de_troca_esconde_o_custo_de_quem_nao_ve_a_margem(cat):
    com = ts.item_de_troca(cat, "b026.10", 1, None, ve_custo=True)
    sem = ts.item_de_troca(cat, "b026.10", 1, None, ve_custo=False)
    assert com["sugestoes"] and all(s["dif_custo_pct"] is not None for s in com["sugestoes"])
    assert all(s["dif_custo_pct"] is None for s in sem["sugestoes"] + sem["fora"])
    assert com["nome_original"] == "Mala Chanfrada M3 tamanho 10 - Prata"
    assert "Prata" in com["texto_oferta"] and "pelo mesmo valor" in com["texto_oferta"]
    # Sem parecido nenhum: diz por quê.
    nada = ts.item_de_troca(cat, "xyz.ci", 1, "Produto X", ve_custo=True)
    assert (nada["sem_parecido"], nada["motivo_sem_sugestao"]) == (
        True,
        "o SKU não está no catálogo do DaVinci",
    )


def test_motivo_de_fora_pelo_custo_mascarado_para_quem_nao_ve_a_margem(cat):
    """Decisão (g): custo é só da Margem — o "sobe mais que o teto" / "bem mais barato"
    também. Para os outros, os dois saem `fora_da_regra`; o sem estoque fica."""
    caro = _kits_com_oculos(1360)
    com = ts.item_de_troca(caro, "dg065.pi+a020.pi", 1, None, ve_custo=True)
    sem = ts.item_de_troca(caro, "dg065.pi+a020.pi", 1, None, ve_custo=False)
    assert [(s["sku"], s["motivo_fora"]) for s in com["fora"]] == [
        ("dg066.pi+a020.pi", FORA_CUSTO_ACIMA)
    ]
    assert [(s["sku"], s["motivo_fora"], s["dif_custo_pct"]) for s in sem["fora"]] == [
        ("dg066.pi+a020.pi", ts.FORA_DA_REGRA, None)
    ]
    # O piso do nível 2 (o G1 a −12,4%), direto na saída da tela.
    g1 = next(s for s in ts.candidatos(cat, "dg017.pi", 1) if s.sku == "dg011.pi")
    assert g1.motivo_fora == ts.FORA_CUSTO_ABAIXO_PISO
    assert ts._sugestao_out(g1, "F109S", ve_custo=False)["motivo_fora"] == ts.FORA_DA_REGRA
    assert ts._sugestao_out(g1, "F109S", ve_custo=True)["motivo_fora"] == ts.FORA_CUSTO_ABAIXO_PISO
    # O sem estoque não é do custo: fica igual para todos.
    cands = ts.candidatos(cat, "dg011.pi", 1, teto_pct=7.0)
    sem_estoque = next(s for s in cands if s.sku == "dg064.pi")
    assert ts._sugestao_out(sem_estoque, "G1", ve_custo=False)["motivo_fora"] == FORA_SEM_ESTOQUE


# ─────────────── catálogo do banco e o bloco do pedido ───────────────


async def _catalogo_no_banco(db, dono):
    seg_raiz = Segment(slug="celular-troca", name="Celular", parent_id=None, sort_order=0)
    db.add(seg_raiz)
    await db.flush()
    for sku, nome, custo, estoque, formato, bid in (
        ("dg055.ci", "Uranyx A17 Pro Max 12.64 - Preto", "495", 0, "S", 1),
        ("dg056.ci", "Uranyx A17 Pro Max 12.64 - Laranja", "495", 114, "S", 2),
        ("a001.ci", "Uranyx Fone com fio UFF001", "4.5", 900, "S", 3),
        ("dg055.ci+a001.ci", "Uranyx A17 Pro Max 12.64 - Preto + Fone", "499.5", 0, "E", 11),
        ("dg056.ci+a001.ci", "Uranyx A17 Pro Max 12.64 - Laranja + Fone", "499.5", 114, "E", 12),
    ):
        db.add(
            Product(
                user_id=dono.id,
                sku=sku,
                name=nome,
                stock=estoque,
                formato=formato,
                situacao="A",
                bling_cost_price=Decimal(custo),
                bling_product_id=bid,
            )
        )
    # O mesmo SKU de outro dono, INATIVO: vale o ativo.
    db.add(
        Product(
            user_id=dono.id,
            sku="DG056.ci",
            name="velho",
            stock=0,
            formato="S",
            situacao="I",
            bling_cost_price=Decimal("9999"),
        )
    )
    # E com a situação NULL (desconhecida), mais NOVO: também não vence o ativo.
    db.add(
        Product(
            user_id=dono.id,
            sku="dg056.CI",
            name="sem situação",
            stock=0,
            formato="S",
            situacao=None,
            bling_cost_price=Decimal("9999"),
            updated_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    for kit, comp in ((11, 1), (11, 3), (12, 2), (12, 3)):
        db.add(
            BlingKitComponent(
                kit_bling_product_id=kit, component_bling_product_id=comp, quantidade=Decimal("1")
            )
        )
    db.add(
        PricingProduct(
            user_id=dono.id,
            sku="dg055, dg056.ci ,dg057",
            name="uranyx A17 pro max 12.64",
            segment_id=seg_raiz.id,
        )
    )
    await db.commit()


@pytest.fixture
def _memoria_limpa(monkeypatch):
    ts.limpar_memoria()
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_troca_teto_custo_pct", 5.0)
    monkeypatch.setattr(s, "atendimento_troca_piso_nivel2_pct", -10.0)
    yield
    ts.limpar_memoria()


async def test_catalogo_do_banco_com_memoria_de_10_min(db, make_user, monkeypatch, _memoria_limpa):
    dono = await make_user()
    await _catalogo_no_banco(db, dono)
    try:
        cat = await ts.catalogo(db)
        # O ativo vence o inativo E o de situação NULL mais novo (a ordem da consulta).
        assert cat.produtos["dg056.ci"].nome == "Uranyx A17 Pro Max 12.64 - Laranja"
        assert cat.produtos["dg056.ci"].ativo is True
        assert cat.produtos["dg056.ci"].custo == 495.0
        # O kit: a soma dos componentes (495 + 4,5).
        assert cat.produtos["dg056.ci+a001.ci"].custo == pytest.approx(499.5)
        assert cat.produtos["dg056.ci"].linha_nome == "uranyx A17 pro max 12.64"
        assert cat.lido_em is not None and cat.lido_em.tzinfo is not None
        s = sugerir(cat, "dg055.ci+a001.ci", 1)
        assert [(x.sku, x.nivel) for x in s] == [("dg056.ci+a001.ci", NIVEL_MODELO)]

        # Na memória: a 2ª leitura não vai ao banco.
        lidas = []
        original = ts._ler_catalogo

        async def contando(session):
            lidas.append(1)
            return await original(session)

        monkeypatch.setattr(ts, "_ler_catalogo", contando)
        assert await ts.catalogo(db) is cat
        assert lidas == []
        # Passados 10 min, relê.
        agora = ts.time.monotonic()
        monkeypatch.setattr(ts.time, "monotonic", lambda: agora + ts.CATALOGO_TTL_S + 1)
        assert await ts.catalogo(db) is not cat
        assert lidas == [1]
    finally:
        await db.execute(text("DELETE FROM bling_kit_components"))
        await db.commit()


async def test_sugestoes_do_pedido_interseccao_e_custo(db, make_user, _memoria_limpa):
    dono = await make_user()
    await _catalogo_no_banco(db, dono)
    try:
        itens = [
            {"sku": "DG055.ci+a001.ci", "descricao": "A17 Preto + Fone", "quantidade": 1},
            # O mesmo SKU em duas linhas soma a quantidade.
            {"sku": "dg055.ci+a001.ci", "descricao": None, "quantidade": 1},
        ]
        b = await ts.sugestoes_do_pedido(
            db, ["dg055.ci+a001.ci", "dg999.ci"], itens, ve_custo=False
        )
        assert b["aviso"] == "o item em falta já não está no pedido: dg999.ci"
        assert b["falhou"] is False and b["ve_custo"] is False
        assert b["catalogo_lido_em"] is not None
        [item] = b["itens"]
        assert (item["sku_original"], item["quantidade"]) == ("DG055.ci+a001.ci", 2)
        # 114 em estoque cobre 2; o % do custo NÃO vai para quem não vê a Margem.
        assert [x["sku"] for x in item["sugestoes"]] == ["dg056.ci+a001.ci"]
        assert item["sugestoes"][0]["dif_custo_pct"] is None
        assert "Laranja" in item["texto_oferta"]

        com = await ts.sugestoes_do_pedido(db, ["dg055.ci+a001.ci"], itens, ve_custo=True)
        assert com["itens"][0]["sugestoes"][0]["dif_custo_pct"] == 0.0

        # Todos os SKUs em falta já saíram do pedido: nem lê o catálogo.
        ts.limpar_memoria()
        fora = await ts.sugestoes_do_pedido(db, ["dg999.ci"], itens, ve_custo=True)
        assert (fora["itens"], fora["catalogo_lido_em"]) == ([], None)
        assert fora["aviso"].endswith("dg999.ci")
    finally:
        await db.execute(text("DELETE FROM bling_kit_components"))
        await db.commit()
