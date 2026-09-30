"""O manual da IA sem regra batendo com regra (parte 2, P7).

- os assuntos vêm do banco (manual base) ou, vazio, das constantes; o
  `so_humano` do banco só ACRESCENTA ao das constantes;
- conflito = duas regras ATIVAS de assunto para o mesmo (assunto,
  plataforma, canal); regra geral, de segurança, de estilo ou inativa não
  conflita;
- o importador confere tudo antes e só grava se nada falhou; `--seco` não
  grava; reimportar não duplica; exportar → importar não muda nada.
"""

from __future__ import annotations

import copy
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoCategoria, AtendimentoModelo, AtendimentoRegra
from app.services.atendimento import manual
from app.services.atendimento.constantes import CATEGORIAS, CATEGORIAS_SO_HUMANO
from scripts import atendimento_manual as script

MANUAL = {
    "categorias": [
        {
            "id": "rastreio",
            "nome": "Rastreio",
            "descricao": "Pedido já enviado: onde está, código de rastreio.",
            "exemplos": ["cadê meu pedido?", "qual o rastreio?"],
            "so_humano": False,
            "lacunas": ["rastreio", "transportadora"],
        },
        {
            "id": "troca_devolucao",
            "nome": "Troca ou devolução",
            "descricao": "Quer trocar ou devolver.",
            "exemplos": [],
            # Tentativa de liberar para a IA: o banco não tira o só-humano.
            "so_humano": False,
            "lacunas": [],
        },
        {
            "id": "brinde",
            "nome": "Brinde",
            "descricao": "Pergunta se vem brinde.",
            "exemplos": [],
            "so_humano": True,
            "lacunas": [],
        },
    ],
    "regras": [
        {
            "tipo": "seguranca",
            "categoria": None,
            "plataforma": None,
            "canal": None,
            "prioridade": 10,
            "quando": "o cliente citar concorrente",
            "faca": "não comente outras lojas",
        },
        {
            "tipo": "categoria",
            "categoria": "rastreio",
            "plataforma": "shopee",
            "canal": "chat",
            "prioridade": 100,
            "quando": "perguntarem do rastreio",
            "faca": "informe {rastreio} e {transportadora}",
        },
        {
            "tipo": "categoria",
            "categoria": "rastreio",
            "plataforma": "ml",
            "canal": None,
            "prioridade": 100,
            "quando": "perguntarem do rastreio no ML",
            "faca": "informe {rastreio}",
        },
        {
            "tipo": "estilo",
            "categoria": None,
            "plataforma": None,
            "canal": None,
            "prioridade": 100,
            "quando": "sempre",
            "faca": "tom leve, sem gírias",
        },
    ],
    "respostas_prontas": [
        {
            "titulo": "Rastreio",
            "texto": "Seu pedido está a caminho, o rastreio é {rastreio}.",
            "plataforma": "shopee",
            "canal": "chat",
            "categoria": "rastreio",
        },
        {
            "titulo": "Agradecimento",
            "texto": "Nós que agradecemos! Qualquer coisa, estamos por aqui.",
            "plataforma": None,
            "canal": None,
            "categoria": None,
        },
    ],
}


async def _conta(db: AsyncSession, modelo) -> int:
    return int(await db.scalar(select(func.count()).select_from(modelo)))


# ─────────────── assuntos ───────────────


async def test_categorias_sem_banco_vem_das_constantes(db: AsyncSession):
    cats = await manual.categorias_ativas(db)
    assert [c["id"] for c in cats] == list(CATEGORIAS)
    rastreio = next(c for c in cats if c["id"] == "rastreio")
    # A IA classifica pela DESCRIÇÃO: toda categoria das constantes tem uma.
    assert all(c["descricao"] for c in cats)
    assert "enviado" in rastreio["descricao"].lower()
    assert {c["id"] for c in cats if c["so_humano"]} == set(CATEGORIAS_SO_HUMANO)


async def test_categorias_do_banco_ativas_na_ordem_e_so_humano_so_soma(db: AsyncSession):
    db.add_all(
        [
            AtendimentoCategoria(
                id="brinde", nome="Brinde", descricao="d", so_humano=True, ordem=20
            ),
            AtendimentoCategoria(
                id="troca_devolucao", nome="Troca", descricao="d", so_humano=False, ordem=10,
                lacunas=["rastreio", "inventada"],
            ),
            AtendimentoCategoria(id="velha", nome="Velha", descricao="d", ativa=False, ordem=5),
        ]
    )
    await db.commit()

    cats = await manual.categorias_ativas(db)

    assert [c["id"] for c in cats] == ["troca_devolucao", "brinde"]
    por_id = {c["id"]: c for c in cats}
    # O banco disse que troca não é só-humano: as constantes mandam (CDC).
    assert por_id["troca_devolucao"]["so_humano"] is True
    assert por_id["brinde"]["so_humano"] is True
    # Lacuna que não existe no código não chega à IA.
    assert por_id["troca_devolucao"]["lacunas"] == ["rastreio"]
    assert "brinde" in manual.ids_so_humano(cats)


# ─────────────── conflitos ───────────────


async def _regra(db: AsyncSession, **campos) -> AtendimentoRegra:
    padrao = {"quando": "q", "faca": "f", "tipo": "categoria"}
    r = AtendimentoRegra(**{**padrao, **campos})
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def test_conflito_mesmo_assunto_plataforma_e_canal(db: AsyncSession):
    existente = await _regra(db, categoria="rastreio", plataforma="shopee", canal="chat")

    conflitos = await manual.conflitos_da_regra(
        db, {"tipo": "categoria", "categoria": "rastreio", "plataforma": "shopee", "canal": "chat"}
    )
    assert [c["id"] for c in conflitos] == [str(existente.id)]
    assert conflitos[0]["quando"] == "q"
    # JSON puro: vai direto no 409 `regra_conflitante`.
    json.dumps(conflitos)

    # A própria regra, na edição, não conflita com ela mesma.
    assert (
        await manual.conflitos_da_regra(
            db,
            {"categoria": "rastreio", "plataforma": "shopee", "canal": "chat"},
            ignorar_id=existente.id,
        )
        == []
    )


@pytest.mark.parametrize(
    "dados",
    [
        # outra plataforma / canal: especialização, não briga
        {"categoria": "rastreio", "plataforma": "ml", "canal": "chat"},
        {"categoria": "rastreio", "plataforma": "shopee", "canal": None},
        {"categoria": "rastreio", "plataforma": None, "canal": None},
        # outro assunto
        {"categoria": "nota_fiscal", "plataforma": "shopee", "canal": "chat"},
        # geral (sem assunto), segurança e estilo não conflitam
        {"categoria": None, "plataforma": "shopee", "canal": "chat"},
        {"tipo": "seguranca", "categoria": "rastreio", "plataforma": "shopee", "canal": "chat"},
        {"tipo": "estilo", "plataforma": "shopee", "canal": "chat"},
        # a regra nova nasce desligada
        {"categoria": "rastreio", "plataforma": "shopee", "canal": "chat", "ativa": False},
    ],
)
async def test_sem_conflito(db: AsyncSession, dados):
    await _regra(db, categoria="rastreio", plataforma="shopee", canal="chat")
    assert await manual.conflitos_da_regra(db, dados) == []


async def test_regra_inativa_no_banco_nao_conflita(db: AsyncSession):
    await _regra(db, categoria="rastreio", plataforma="shopee", canal="chat", ativa=False)
    assert (
        await manual.conflitos_da_regra(
            db, {"categoria": "rastreio", "plataforma": "shopee", "canal": "chat"}
        )
        == []
    )


async def test_conflitos_existentes_agrupa_as_que_batem(db: AsyncSession):
    a = await _regra(db, categoria="rastreio", plataforma="shopee", quando="a")
    b = await _regra(db, categoria="rastreio", plataforma="shopee", quando="b")
    await _regra(db, categoria="rastreio", plataforma="shopee", quando="c", ativa=False)
    await _regra(db, categoria="rastreio", plataforma="ml", quando="d")
    await _regra(db, categoria=None, plataforma="shopee", quando="e")
    await _regra(db, categoria=None, plataforma="shopee", quando="f")

    grupos = await manual.conflitos_existentes(db)

    assert grupos == [
        {
            "categoria": "rastreio",
            "plataforma": "shopee",
            "canal": None,
            "regra_ids": [str(a.id), str(b.id)],
            "regras": [manual.regra_dict(a), manual.regra_dict(b)],
        }
    ]


# ─────────────── importar / exportar ───────────────


async def test_importar_grava_e_reimportar_nao_duplica(db: AsyncSession):
    rel = await manual.importar_manual(db, copy.deepcopy(MANUAL))
    await db.commit()

    assert rel.ok and rel.gravado, rel.linhas()
    assert len(rel.categorias_novas) == 3 and len(rel.regras_novas) == 4
    assert len(rel.respostas_novas) == 2
    cats = {c.id: c for c in (await db.execute(select(AtendimentoCategoria))).scalars()}
    assert cats["rastreio"].exemplos == ["cadê meu pedido?", "qual o rastreio?"]
    assert cats["rastreio"].lacunas == ["rastreio", "transportadora"]
    # A ordem do arquivo é a ordem do manual.
    assert cats["rastreio"].ordem < cats["troca_devolucao"].ordem < cats["brinde"].ordem
    regras = (await db.execute(select(AtendimentoRegra))).scalars().all()
    seg = next(r for r in regras if r.tipo == "seguranca")
    assert (seg.categoria, seg.prioridade, seg.ativa) == (None, 10, True)
    resposta = (
        await db.execute(select(AtendimentoModelo).where(AtendimentoModelo.titulo == "Rastreio"))
    ).scalar_one()
    assert resposta.categoria == "rastreio"

    de_novo = await manual.importar_manual(db, copy.deepcopy(MANUAL))
    await db.commit()
    assert de_novo.ok
    assert (de_novo.categorias_iguais, de_novo.regras_iguais, de_novo.respostas_iguais) == (
        3,
        4,
        2,
    )
    assert not (de_novo.categorias_novas or de_novo.regras_novas or de_novo.respostas_novas)
    assert await _conta(db, AtendimentoRegra) == 4
    assert await _conta(db, AtendimentoModelo) == 2


async def test_importar_atualiza_pelo_quando_e_pelo_id(db: AsyncSession):
    await manual.importar_manual(db, copy.deepcopy(MANUAL))
    await db.commit()
    novo = copy.deepcopy(MANUAL)
    novo["categorias"][0]["descricao"] = "Descrição nova."
    # Mesmo QUANDO (com outra caixa e espaços): é a mesma regra, muda o FAÇA.
    novo["regras"][1]["quando"] = "  Perguntarem   do RASTREIO "
    novo["regras"][1]["faca"] = "informe só {rastreio}"
    novo["respostas_prontas"][1]["texto"] = "Nós que agradecemos!"

    rel = await manual.importar_manual(db, novo)
    await db.commit()

    assert rel.ok
    assert rel.categorias_atualizadas == ["rastreio (Rastreio)"]
    assert len(rel.regras_atualizadas) == 1 and len(rel.respostas_atualizadas) == 1
    assert await _conta(db, AtendimentoRegra) == 4
    regra = (
        await db.execute(
            select(AtendimentoRegra).where(
                AtendimentoRegra.categoria == "rastreio", AtendimentoRegra.plataforma == "shopee"
            )
        )
    ).scalar_one()
    assert regra.faca == "informe só {rastreio}"


async def test_seco_nao_grava_nada(db: AsyncSession):
    rel = await manual.importar_manual(db, copy.deepcopy(MANUAL), seco=True)
    await db.commit()

    assert rel.ok and not rel.gravado
    assert len(rel.regras_novas) == 4
    assert await _conta(db, AtendimentoRegra) == 0
    assert await _conta(db, AtendimentoCategoria) == 0
    assert await _conta(db, AtendimentoModelo) == 0


async def test_conflito_dentro_do_arquivo_recusa_tudo(db: AsyncSession):
    ruim = copy.deepcopy(MANUAL)
    ruim["regras"].append(
        {
            "tipo": "categoria",
            "categoria": "rastreio",
            "plataforma": "shopee",
            "canal": "chat",
            "prioridade": 50,
            "quando": "outro jeito de perguntar do rastreio",
            "faca": "diga outra coisa",
        }
    )

    rel = await manual.importar_manual(db, ruim)
    await db.commit()

    assert not rel.ok and not rel.gravado
    assert any("regras[5] e regras[2]" in c for c in rel.conflitos), rel.conflitos
    # Nada entrou — nem os assuntos, nem as outras regras.
    assert await _conta(db, AtendimentoRegra) == 0
    assert await _conta(db, AtendimentoCategoria) == 0


async def test_conflito_com_regra_ativa_do_banco_recusa(db: AsyncSession):
    existente = await _regra(
        db, categoria="rastreio", plataforma="shopee", canal="chat", quando="rastreio (tela)"
    )

    rel = await manual.importar_manual(db, copy.deepcopy(MANUAL))
    await db.commit()

    assert not rel.ok
    assert any(str(existente.id) in c for c in rel.conflitos), rel.conflitos
    assert await _conta(db, AtendimentoRegra) == 1

    # Desativada na tela, o arquivo entra.
    existente.ativa = False
    await db.commit()
    rel = await manual.importar_manual(db, copy.deepcopy(MANUAL))
    await db.commit()
    assert rel.ok, rel.linhas()


@pytest.mark.parametrize(
    ("mexer", "trecho"),
    [
        (
            lambda m: m["regras"][1].update(categoria="inexistente"),
            "categoria inexistente não existe",
        ),
        (lambda m: m["regras"][1].update(tipo="outro"), "tipo desconhecido"),
        # A Magalu entrou na caixa (30/09): a Shein continua sem API de atendimento.
        (lambda m: m["regras"][1].update(plataforma="shein"), "plataforma desconhecida"),
        (lambda m: m["regras"][1].update(canal="pos_venda"), "canal pos_venda não existe"),
        (lambda m: m["regras"][0].update(categoria="rastreio"), "vale para todos os assuntos"),
        (lambda m: m["regras"][1].update(prioridade="alta"), "prioridade"),
        (lambda m: m["regras"][1].update(faca="  "), "`faca` vazio"),
        (lambda m: m["regras"][1].update(quandoo="x"), "chave desconhecida: quandoo"),
        (lambda m: m["categorias"][0].update(id="Rastreio!"), "`id` tem de ser"),
        (lambda m: m["categorias"][0].update(lacunas=["cpf"]), "lacuna desconhecida: cpf"),
        (lambda m: m["categorias"].append(dict(m["categorias"][0])), "id repetido"),
        (
            lambda m: m["respostas_prontas"][1].update(texto="Me chama no WhatsApp 11 98765-4321"),
            "o envio barraria",
        ),
        (lambda m: m.update(outra=[]), "chave desconhecida: outra"),
    ],
)
async def test_arquivo_invalido_recusa_com_o_motivo(db: AsyncSession, mexer, trecho):
    ruim = copy.deepcopy(MANUAL)
    mexer(ruim)

    rel = await manual.importar_manual(db, ruim)
    await db.commit()

    assert not rel.ok and not rel.gravado
    assert any(trecho in e for e in rel.erros), rel.erros
    assert await _conta(db, AtendimentoRegra) == 0


async def test_regra_com_assunto_das_constantes_sem_categorias_no_arquivo(db: AsyncSession):
    """Sem manual base (tabela e arquivo sem assuntos), valem os das constantes."""
    rel = await manual.importar_manual(
        db,
        {
            "regras": [
                {"tipo": "categoria", "categoria": "nota_fiscal", "quando": "pedir NF",
                 "faca": "informe {nf_numero}"}
            ]
        },
    )
    assert rel.ok, rel.erros
    rel = await manual.importar_manual(
        db, {"regras": [{"categoria": "brinde", "quando": "q", "faca": "f"}]}
    )
    assert any("categoria brinde não existe" in e for e in rel.erros)


async def test_exportar_e_importar_de_volta_nao_muda_nada(db: AsyncSession):
    await manual.importar_manual(db, copy.deepcopy(MANUAL))
    await db.commit()

    exportado = await manual.exportar_manual(db)

    assert [c["id"] for c in exportado["categorias"]] == ["rastreio", "troca_devolucao", "brinde"]
    # O so_humano exportado é o do BANCO (o arquivo disse False para troca).
    assert exportado["categorias"][1]["so_humano"] is False
    assert exportado["regras"][0]["tipo"] == "seguranca"
    assert exportado["regras"][-1]["tipo"] == "estilo"
    rel = await manual.importar_manual(db, json.loads(json.dumps(exportado)))
    assert rel.ok
    assert not (rel.categorias_novas or rel.categorias_atualizadas)
    assert not (rel.regras_novas or rel.regras_atualizadas)
    assert not (rel.respostas_novas or rel.respostas_atualizadas)


async def test_exportar_sem_manual_sai_a_lista_das_constantes(db: AsyncSession):
    exportado = await manual.exportar_manual(db)
    assert [c["id"] for c in exportado["categorias"]] == list(CATEGORIAS)
    assert exportado["regras"] == [] and exportado["respostas_prontas"] == []


# ─────────────── o comando ───────────────


async def test_comando_importar_seco_importar_e_exportar(db: AsyncSession, tmp_path, capsys):
    arquivo = tmp_path / "manual.json"
    arquivo.write_text(json.dumps(MANUAL, ensure_ascii=False), encoding="utf-8")

    assert await script.executar(["importar", str(arquivo), "--seco"]) == script.SAIDA_OK
    assert "(seco) nada foi gravado." in capsys.readouterr().out
    assert await _conta(db, AtendimentoRegra) == 0

    assert await script.executar(["importar", str(arquivo)]) == script.SAIDA_OK
    assert "Gravado." in capsys.readouterr().out
    assert await _conta(db, AtendimentoRegra) == 4

    saida = tmp_path / "exportado.json"
    assert await script.executar(["exportar", str(saida)]) == script.SAIDA_OK
    assert len(json.loads(saida.read_text(encoding="utf-8"))["regras"]) == 4


async def test_comando_recusa_conflito_e_arquivo_ilegivel(db: AsyncSession, tmp_path, capsys):
    await _regra(db, categoria="rastreio", plataforma="shopee", canal="chat", quando="outra")
    arquivo = tmp_path / "manual.json"
    arquivo.write_text(json.dumps(MANUAL), encoding="utf-8")

    assert await script.executar(["importar", str(arquivo)]) == script.SAIDA_RECUSADO
    saida = capsys.readouterr().out
    assert "CONFLITO:" in saida and "RECUSADO" in saida
    assert await _conta(db, AtendimentoRegra) == 1
    assert await _conta(db, AtendimentoCategoria) == 0

    quebrado = tmp_path / "quebrado.json"
    quebrado.write_text("{nao é json", encoding="utf-8")
    assert await script.executar(["importar", str(quebrado)]) == script.SAIDA_ARQUIVO


# ─────────────── revisão de 28/09 (LOGICA-01) ───────────────


async def test_regra_repetida_no_arquivo_e_recusada_qualquer_tipo(db: AsyncSession):
    """Duas regras com a mesma identidade (tipo, assunto, plataforma, canal,
    QUANDO) cairiam na MESMA regra do banco na reimportação — a segunda
    sobrescreveria a primeira. Recusa, como o título repetido das respostas."""
    ruim = copy.deepcopy(MANUAL)
    for faca in ("tom cordial", "falar como nós"):
        ruim["regras"].append(
            {
                "tipo": "estilo",
                "categoria": None,
                "plataforma": None,
                "canal": None,
                "prioridade": 80,
                "quando": "Sempre.",
                "faca": faca,
            }
        )

    rel = await manual.importar_manual(db, ruim)

    assert not rel.ok and not rel.gravado
    assert any("repete a regra de regras[" in e for e in rel.erros), rel.erros
    assert await _conta(db, AtendimentoRegra) == 0


async def test_manual_base_reimportado_nao_muda_nada(db: AsyncSession):
    """O `atendimento_manual_base.json` de verdade: importar de novo não mexe em nada
    (antes, a regra de estilo "Português correto" era sobrescrita pela "Falar como nós")."""
    from pathlib import Path

    caminho = Path(script.__file__).with_name("atendimento_manual_base.json")
    base = json.loads(caminho.read_text(encoding="utf-8"))

    primeira = await manual.importar_manual(db, copy.deepcopy(base))
    await db.commit()
    assert primeira.ok, primeira.linhas()
    regras = len(primeira.regras_novas)

    segunda = await manual.importar_manual(db, copy.deepcopy(base))
    await db.commit()

    assert segunda.ok, segunda.linhas()
    assert segunda.regras_novas == [] and segunda.regras_atualizadas == []
    assert segunda.regras_iguais == regras
    assert not (segunda.categorias_novas or segunda.categorias_atualizadas)
    assert not (segunda.respostas_novas or segunda.respostas_atualizadas)
    estilo = (
        (await db.execute(select(AtendimentoRegra).where(AtendimentoRegra.tipo == "estilo")))
        .scalars()
        .all()
    )
    assert any(r.faca.startswith("Português correto") for r in estilo)
