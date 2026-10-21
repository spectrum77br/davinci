"""A pasta CRIADA em "Site (como hoje)" liga EXATAMENTE como antes (21/10/2026).

Decisão 9 do dono ("as dos sites Uranyx e Charlots então pode manter como era;
o resto já vem esse novo padrão"): sem categoria, as pastas criadas param de
ligar, e nenhuma das três categorias devolve o que as dos SITES faziam — Venda
recusa o site, o Chamado do site nunca acha pedido (`chamado_sem_pedido`), o
Informativo só vira linha. Por isso:
  • a categoria `site` ("Site (como hoje)"): a pasta liga como antes — a loja
    pelo endereço, o tipo pelo conteúdo, o chamado RF6 do site pelo protocolo,
    a conversa na aba E-mail. A 0424 a dá às pastas de site da Uranyx
    ("*uranyx sac") e da Charlots ("*poofy": a marca `poofy` do cadastro tem o
    nome "charlots" e o domínio charlots.com.br); numa outra pasta, uma pessoa
    pode escolher e ela liga como antes, igual (aqui também nas de marketplace);
  • na pasta criada SEM categoria, a resposta num fio (References/fio do Tuta)
    ou com o protocolo do site que a EQUIPE já atende segue como sempre: o
    cliente de uma conversa aberta nunca some (o e-mail novo fica guardado —
    `test_mail_pastas_categoria.py`).

Como se prova "exatamente": o `INSTANTANEO` (tests/fixtures/mail_pasta_como_hoje.json)
foi gravado com o código de ANTES da categoria por pasta (739dc957, o origin/main
de 21/10 — lá a pasta criada ligava pelo conteúdo) rodando os mesmos e-mails nas
mesmas pastas (gravado de novo em 21/10 com o código do origin/main 2e8d1c02 —
a ponte é a mesma da 739dc957 — quando entrou o site da Charlots); aqui cada
coluna que já existia na meta, na pasta, na conversa e na mensagem tem de sair
IGUAL (as fichas de `test_mail_entrada_igual.py`), as colunas novas ficam
vazias (a pasta só tem `site` ou nada) e a regra é a versão 1 (a de antes,
inteira).

Gravar de novo (só se a regra de antes mudar DE PROPÓSITO; com o código de antes):
MAIL_COMO_HOJE_GRAVAR=1 pytest tests/test_mail_pasta_como_hoje.py
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from app.models import Marca
from app.models.mail_atendimento import MailFolder
from app.security.cipher import decrypt_json, encrypt_json
from app.services.mail_atendimento import pastas, regras
from tests.test_mail_entrada_igual import _foto
from tests.test_mail_ponte import (
    _config,  # noqa: F401 — o ambiente da ponte
    cena,  # noqa: F401 — o mundo da ponte (as duas caixas, as lojas, a marca)
    entrar,
    pedido_existe,
    rodar,
)

INSTANTANEO = Path(__file__).parent / "fixtures" / "mail_pasta_como_hoje.json"
# As pastas em "Site (como hoje)" (no código de antes, só criadas: lá já ligavam
# assim): os dois sites da decisão 9 e, para provar que a categoria é a regra de
# antes inteira, três de marketplace.
COMO_HOJE = ("problema ml", "mensagens ml", "problema shopee", "*uranyx sac", "*poofy")
# O site da Charlots como em produção: a pasta "*poofy" (palavra de marca
# `poofy`) e a marca `poofy` com o nome "charlots" e o domínio charlots.com.br —
# o mesmo da Charlots Park (na Entrada, a marca ambígua; na pasta, a dela).
MARCAS_CHARLOTS = (
    {"nome": "charlots", "slug": "poofy", "dominio_br": "charlots.com.br"},
    {
        "nome": "Charlots Park",
        "slug": "charlots-park",
        "dominio": "charlotspark.com",
        "dominio_br": "charlotspark.com.br, charlots.com.br",
    },
)
ALIASES_CHARLOTS = ("sac@charlots.com.br", "sac@poofy.com.br")
# As pastas SEM categoria onde só chega a resposta a algo que a equipe já tem.
SEM_CATEGORIA = ("reclamacao ml", "*uranyx")
FORM_LIA = (
    "Nome: Lia Reis\nE-mail: lia.reis@gmail.com\nTelefone: (11) 97777-6666\n"
    "Mensagem: o carregador parou"
)


async def _pasta(db, caixa, nome: str, categoria: str | None) -> None:
    """A pasta criada como a ponte a garante (a chave é o nome, como o agente v1
    manda) e, no código de hoje, a categoria que uma PESSOA escolheu."""
    pasta, _ = await pastas.garantir(db, caixa.id, pastas.do_conteudo({"folder": nome}))
    if hasattr(MailFolder, "categoria"):
        pasta.categoria = categoria
        pasta.categoria_em = datetime.now(UTC) if categoria else None
        regras.reclassificar(pasta, await regras.carregar(db))
    await db.commit()


async def _cenario(db, cena) -> tuple[list, list]:  # noqa: F811 — `cena` é a fixture
    await pedido_existe(db, cena.ml, "2000012345670002")
    await pedido_existe(db, cena.ml, "2000012345670003")
    db.add_all([Marca(**m) for m in MARCAS_CHARLOTS])
    config = decrypt_json(cena.geral.config_enc)
    config["aliases"] = [*config["aliases"], *ALIASES_CHARLOTS]
    cena.geral.config_enc = encrypt_json(config)
    await db.commit()
    for nome in COMO_HOJE:
        await _pasta(db, cena.geral, nome, "site")
    for nome in SEM_CATEGORIA:
        await _pasta(db, cena.geral, nome, None)
    ml = {"de": "aviso@mercadolivre.com.br", "de_nome": "Mercado Livre"}
    roteiro = [
        # ── "Site (como hoje)" ──
        # A1: o cliente com o pedido que existe (problema = destaque e pendência).
        {
            "pasta": "problema ml",
            "assunto": "Reclamação no pedido 2000012345670002",
            "corpo": "Chegou quebrado.",
            "message_id": "<a1@c.com>",
        },
        # A2: o aviso oficial do ML do mesmo pedido.
        {
            "pasta": "mensagens ml",
            **ml,
            "assunto": "Nova mensagem no pedido 2000012345670002",
            "corpo": "O comprador mandou uma mensagem.",
            "message_id": "<a2@ml.com>",
        },
        # A3: novidades do ML (informativo PELO CONTEÚDO, numa conversa, como antes).
        {
            "pasta": "mensagens ml",
            "de": "novidades@mercadolivre.com.br",
            "de_nome": "Mercado Livre",
            "assunto": "Novidades da semana para vendedores",
            "corpo": "Confira as novidades.",
            "message_id": "<a3@ml.com>",
        },
        # A4: o FORMULÁRIO do site (DE sac@ PARA sac@) → o chamado RF6.
        {
            "pasta": "*uranyx sac",
            "de": "sac@uranyx.com.br",
            "de_nome": "Site Uranyx",
            "para": ["sac@uranyx.com.br"],
            "assunto": "[US-26-0041] Defeito",
            "corpo": (
                "Nome: Ana Souza\nE-mail: ana.souza@gmail.com\nTelefone: (11) 98888-7777\n"
                "Pedido: 4455\nMensagem: a tela apagou"
            ),
            "message_id": "<a4@site.com>",
        },
        # A5: a cliente escrevendo para o sac@ com o protocolo.
        {
            "pasta": "*uranyx sac",
            "para": ["sac@uranyx.com.br"],
            "assunto": "[US-26-0042] Troca",
            "corpo": "Quero trocar a capa.",
            "message_id": "<a5@c.com>",
        },
        # A6: o golpe que imita a plataforma, citando o pedido.
        {
            "pasta": "problema ml",
            "de": "suporte@mercadolivre-br.com",
            "de_nome": "Mercado Livre",
            "assunto": "Pedido 2000012345670002 bloqueado",
            "corpo": "Clique para liberar.",
            "message_id": "<a6@golpe.com>",
        },
        # A7: o cliente da Shopee da Barbosa (22max).
        {
            "pasta": "problema shopee",
            "de": "joao@gmail.com",
            "de_nome": "João",
            "para": ["22max@tuta.com"],
            "assunto": "Dúvida",
            "corpo": "Quando chega?",
            "message_id": "<a7@c.com>",
        },
        # A8: a resposta da cliente do site no fio do A5.
        {
            "pasta": "*uranyx sac",
            "para": ["sac@uranyx.com.br"],
            "assunto": "Re: [US-26-0042] Troca",
            "corpo": "Alguma novidade?",
            "in_reply_to": "<a5@c.com>",
            "references": ["<a5@c.com>"],
            "message_id": "<a8@c.com>",
        },
        # A9: a devolução do servidor do cliente no fio do A1.
        {
            "pasta": "problema ml",
            "de": "mailer-daemon@gmail.com",
            "de_nome": "Mail Delivery Subsystem",
            "assunto": "Delivery Status Notification (Failure)",
            "corpo": "Address not found.",
            "in_reply_to": "<a1@c.com>",
            "references": ["<a1@c.com>"],
            "message_id": "<a9@gmail.com>",
        },
        # A10: o cliente sem pedido → sem vínculo.
        {
            "pasta": "problema ml",
            "de": "carla@gmail.com",
            "de_nome": "Carla",
            "assunto": "Ajuda",
            "corpo": "Preciso de ajuda.",
            "message_id": "<a10@c.com>",
        },
        # A11: a cópia do A2 (mesmo Message-ID) → duplicado.
        {
            "pasta": "mensagens ml",
            **ml,
            "assunto": "Nova mensagem no pedido 2000012345670002",
            "corpo": "O comprador mandou uma mensagem.",
            "message_id": "<a2@ml.com>",
        },
        # A12: código de verificação → segurança.
        {
            "pasta": "problema ml",
            "de": "no-reply@mercadolivre.com.br",
            "assunto": "Seu código de verificação",
            "corpo": "Use 482913 para entrar na sua conta.",
            "message_id": "<a12@ml.com>",
        },
        # ── SEM categoria: só a resposta ao que a equipe já atende ──
        # B1: a cliente na Entrada (como sempre).
        {
            "pasta": "INBOX",
            "de": "bia@gmail.com",
            "de_nome": "Bia",
            "assunto": "Pedido 2000012345670003 não chegou",
            "corpo": "Onde está?",
            "message_id": "<b1@c.com>",
        },
        # B2: a resposta dela no fio do B1, numa pasta sem categoria.
        {
            "pasta": "reclamacao ml",
            "de": "bia@gmail.com",
            "de_nome": "Bia",
            "assunto": "Re: Pedido 2000012345670003 não chegou",
            "corpo": "Ainda nada.",
            "in_reply_to": "<b1@c.com>",
            "references": ["<b1@c.com>"],
            "message_id": "<b2@c.com>",
        },
        # B3: o formulário do site na Entrada → o chamado RF6.
        {
            "pasta": "INBOX",
            "de": "sac@uranyx.com.br",
            "de_nome": "Site Uranyx",
            "para": ["sac@uranyx.com.br"],
            "assunto": "[US-26-0051] Garantia",
            "corpo": FORM_LIA,
            "message_id": "<b3@site.com>",
        },
        # B4: a cliente escreve DE NOVO (sem responder no fio) com o protocolo,
        # numa pasta de site sem categoria.
        {
            "pasta": "*uranyx",
            "de": "lia.reis@gmail.com",
            "de_nome": "Lia Reis",
            "para": ["sac@uranyx.com.br"],
            "assunto": "[US-26-0051] Ainda aguardo",
            "corpo": "Alguma resposta?",
            "message_id": "<b4@c.com>",
        },
        # ── "Site (como hoje)": o site da Charlots ("*poofy") ──
        # C1: o FORMULÁRIO do site (DE sac@ PARA sac@ charlots.com.br) → o chamado RF6.
        {
            "pasta": "*poofy",
            "de": "sac@charlots.com.br",
            "de_nome": "Site Charlots",
            "para": ["sac@charlots.com.br"],
            "assunto": "[CS-26-0007] Troca de tamanho",
            "corpo": (
                "Nome: Rita Alves\nE-mail: rita.alves@gmail.com\nTelefone: (21) 96666-5555\n"
                "Pedido: 9911\nMensagem: veio o tamanho errado"
            ),
            "message_id": "<c1@site.com>",
        },
        # C2: a resposta da cliente no fio do C1.
        {
            "pasta": "*poofy",
            "de": "rita.alves@gmail.com",
            "de_nome": "Rita Alves",
            "para": ["sac@charlots.com.br"],
            "assunto": "Re: [CS-26-0007] Troca de tamanho",
            "corpo": "Já mandei as fotos.",
            "in_reply_to": "<c1@site.com>",
            "references": ["<c1@site.com>"],
            "message_id": "<c2@c.com>",
        },
        # C3: outra cliente escrevendo para o sac@poofy com outro protocolo.
        {
            "pasta": "*poofy",
            "de": "joana@gmail.com",
            "de_nome": "Joana",
            "para": ["sac@poofy.com.br"],
            "assunto": "[CS-26-0009] Atacado",
            "corpo": "Quero comprar 50 peças.",
            "message_id": "<c3@c.com>",
        },
    ]
    ids, resumos = [], []
    for item in roteiro:
        ids.append(await entrar(db, cena.geral, **item))
        resumos.append(await rodar(db))
    return ids, resumos


def _sem_novas(tabela: str, linhas: list) -> list:
    """As colunas novas saem da comparação: a pasta só tem `site` ou nada, e
    nenhum e-mail chega à Pós-venda."""
    saida = []
    for linha in linhas:
        if linha is None:
            saida.append(None)
            continue
        if tabela == "mail_folders":
            assert linha.get("categoria") in (None, "site"), linha.get("categoria")
            assert linha.get("categoria_por") is None
            novas = ("categoria", "categoria_por", "categoria_em")
        else:
            assert linha.get("chamado_id") is None
            novas = ("chamado_id",)
        saida.append({k: v for k, v in linha.items() if k not in novas})
    return saida


async def test_como_hoje_e_a_resposta_ao_que_a_equipe_tem_ligam_exatamente_como_antes(
    db,
    cena,  # noqa: F811
):
    ids, resumos = await _cenario(db, cena)
    foto = await _foto(db, ids, resumos)
    if hasattr(MailFolder, "categoria"):
        # Aqui (o código de hoje) a pasta TEM a categoria `site` — a prova não é vazia.
        categorias = {p["nome"]: p["categoria"] for p in foto["pastas"]}
        assert categorias == {
            **dict.fromkeys(COMO_HOJE, "site"),
            **dict.fromkeys(SEM_CATEGORIA, None),
            "INBOX": None,
        }
    foto["metas"] = _sem_novas("mail_message_meta", foto["metas"])
    foto["pastas"] = _sem_novas("mail_folders", foto["pastas"])
    if os.environ.get("MAIL_COMO_HOJE_GRAVAR") == "1":
        INSTANTANEO.write_text(json.dumps(foto, ensure_ascii=False, indent=1, sort_keys=True))
    esperado = json.loads(INSTANTANEO.read_text())
    atual = json.loads(json.dumps(foto, ensure_ascii=False, sort_keys=True))
    # Coluna a coluna, para o erro dizer ONDE mudou.
    for chave in ("resumos", "totais", "pastas", "metas", "conversas", "mensagens"):
        assert atual[chave] == esperado[chave], chave
    assert atual == esperado
    # A regra de antes, inteira (versão 1); nada guardado pela falta de categoria.
    assert {m["regras_versao"] for m in atual["metas"] if m} == {1}
    assert not any(
        m and m["motivo"] in ("pasta_sem_categoria", "chamado_sem_pedido") for m in atual["metas"]
    )
    # B2 e B4 (pastas sem categoria) entraram nas conversas que a equipe já tinha.
    b1, b2, b3, b4 = atual["metas"][12:16]
    assert (b2["conversa_id"], b2["vinculado_por"]) == (b1["conversa_id"], "referencia")
    assert (b4["conversa_id"], b4["vinculado_por"]) == (b3["conversa_id"], "protocolo")
    # O site da Charlots em "Site (como hoje)": o chamado RF6 de cada protocolo,
    # a resposta no fio do formulário, na conversa da aba E-mail.
    c1, c2, c3 = atual["metas"][16:19]
    assert {m["estado"] for m in (c1, c2, c3)} == {"gravado"}
    assert {m["plataforma"] for m in (c1, c2, c3)} == {"site"}
    assert (c1["protocolo"], c3["protocolo"]) == ("CS-26-0007", "CS-26-0009")
    assert c2["conversa_id"] == c1["conversa_id"] != c3["conversa_id"]
