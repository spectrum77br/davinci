"""A revisão de 20/10/2026 do AGENTE de atendimento (a rodada "ajustar" 1).

Cada achado confirmado da revisão de segurança, legado, SDK, dados e
completude, com as frases e os títulos REAIS que ela usou:

- a promessa de ação da equipe no presente ("em breve retornamos"), também na
  pergunta; o pedido de evidência só dispensa "a equipe analisa e responde";
- o caminho para fora (o site da marca, "nosso site", Reclame Aqui, "outra
  loja"), o nome interno da loja e o rótulo do cadastro ("SALVADO");
- a resistência à água e a biometria sem fonte; a entrega por estado contra a
  `restricao_envio`;
- a garantia negada ou vencida (bloqueia), o reset (pessoa), o prazo da regra
  com o par certo, a regra da linha sem status;
- a régua de código: a ameaça de reclamação/disputa (2), o estado de
  postagem desconhecido (7 e 8), "consta entregue" sem a logística (11), o
  Pix da compra e a troca "grátis" (12 e 13), a foto junto do texto (10), as
  pistas de orientação do turno, os OUTROS pedidos lidos por ferramenta;
- as especificações: "64 + 64 Sd Card" e "64/128 Gb Memoria" não são RAM; o
  anúncio de dois modelos;
- a linha da garantia pelo SKU (a0NN), o nome do cadastro sem rótulo interno,
  os pares sem nome de terceiro nem rua, o teto do replay, o prazo da rodada.

Tudo puro (sem banco, sem modelo); o que precisa do banco está nos testes de
cada módulo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.atendimento import constantes, especificacoes, ia, sites_marca, validador
from app.services.atendimento import variacoes as variacoes_svc
from app.services.atendimento.agente import acao_pessoa, checagens, custo, ferramentas, pares
from tests.test_atendimento_agente_pacote import _cliente, _codigos, _conv, _gat, _loja

_AGORA = datetime(2026, 10, 20, 15, tzinfo=UTC)

# ─────────────── a promessa de ação da equipe (checagens 5) ───────────────


def _promessa(texto: str, acao: str = "responder", assunto: str = "outro") -> list[str]:
    return [
        p.motivo
        for p in checagens.conferir_promessa_de_equipe(
            texto, acao=acao, acoes_pessoa=[], assunto=assunto
        )
    ]


@pytest.mark.parametrize(
    "texto",
    [
        "Em breve retornamos com uma posição.",
        "Assim que tivermos novidades, avisamos por aqui.",
        "Já pedimos para a transportadora verificar.",
        "Vou pedir para a equipe olhar isso.",
        "Nossa equipe já está ciente e logo te responde.",
        "Pode deixar que separamos a cor azul para você.",
    ],
)
def test_promessa_no_presente_vai_para_pessoa(texto):
    assert _promessa(texto), texto


@pytest.mark.parametrize(
    "texto",
    [
        "Já enviamos o seu pedido, o código de rastreio está em Minhas compras.",
        "Enviamos para todo o Brasil.",
        "Respondemos de segunda a sexta, das 8h às 18h.",
        "Verificamos aqui e o seu pedido está a caminho.",
        "A loja verifica cada aparelho antes do envio.",
    ],
)
def test_o_presente_que_e_fato_nao_e_promessa(texto):
    assert _promessa(texto) == [], texto


def test_a_pergunta_tambem_e_conferida_fora_da_frase_da_pergunta():
    assert _promessa("Vamos verificar e retornamos. Qual o número do pedido?", acao="perguntar")
    assert _promessa("Vamos verificar juntos?", acao="perguntar") == []
    assert _promessa("Vamos verificar.", acao="pessoa") == []


@pytest.mark.parametrize("assunto", ["defeito", "item_errado_faltando", "garantia"])
def test_o_pedido_de_evidencia_so_dispensa_a_analise(assunto):
    for texto in (
        "Pode nos mandar um vídeo curto do defeito por aqui? Assim que recebermos, vamos "
        "providenciar o envio de outra unidade.",
        "Envie fotos do produto e da embalagem por aqui; vamos separar outra unidade e enviar "
        "ainda esta semana.",
        "Pode mandar um vídeo do problema por aqui? Nossa equipe vai cuidar do estorno para você.",
        "Envie por aqui um vídeo do defeito; após a análise, vamos providenciar a troca.",
        "Mande um vídeo do defeito por aqui, vamos analisar e providenciar a troca.",
    ):
        assert _promessa(texto, assunto=assunto), texto
    orientacao = (
        "Sentimos muito! Envie por aqui um vídeo curto mostrando o defeito e fotos; a nossa "
        "equipe vai analisar e responder por aqui, e a troca segue depois da análise."
    )
    assert _promessa(orientacao, assunto=assunto) == []


# ─────────────── o caminho para fora, o nome interno, o rótulo ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "Você também pode conferir as especificações no site da Uranyx.",
        "Compre pelo nosso site oficial que sai mais barato.",
        "Você pode abrir um chamado no Reclame Aqui.",
        "Temos esse modelo na nossa outra loja, a Barbosa.",
        "Acesse o site oficial da Uranyx.",
        "Conforme o site da Charlots, a garantia é de 90 dias.",
        "Você pode registrar no Procon.",
        "Veja em uranyx.com.br.",
    ],
)
def test_caminho_para_fora_bloqueia(texto):
    (p,) = checagens.conferir_caminho_da_plataforma(texto, "shopee")
    assert p.bloqueia and p.codigo == checagens.CAMINHO_FORA_DA_PLATAFORMA


def test_a_nossa_loja_e_o_app_nao_sao_caminho_para_fora():
    for texto in ("Na nossa loja todos os produtos são novos.", "Acompanhe pelo app da Shopee."):
        assert checagens.conferir_caminho_da_plataforma(texto, "shopee") == []


def test_nome_interno_da_loja_e_rotulo_do_cadastro_bloqueiam():
    (p,) = checagens.conferir_nomes_internos("Aqui é a loja Victor Mei!", ["victor mei"])
    assert p.bloqueia and p.codigo == checagens.ROTULO_INTERNO
    assert checagens.conferir_nomes_internos("Somos a KFA 2.", ["kfa2"])
    # O nome de uma palavra só ("mini", "minas") casaria o produto e o estado.
    assert checagens.conferir_nomes_internos("O Mac Mini vai para Minas Gerais.", ["mini"]) == []
    assert checagens.conferir_nomes_internos("Temos a cor Azul AVULSO SALVADO.", [])
    # "Peças avulsas" é resposta (fato candidato); "usado" é anúncio de usados.
    assert checagens.conferir_nomes_internos("Não vendemos peças avulsas.", []) == []


def test_o_nome_do_cadastro_perde_os_rotulos_internos():
    assert variacoes_svc.sem_rotulos_internos("Azul AVULSO SALVADO") == "Azul"
    assert variacoes_svc.sem_rotulos_internos("Mala (DT - DTLG045 - DT23) Preta") == "Mala Preta"
    assert variacoes_svc.sem_rotulos_internos("Prata - usado lote 3") == "Prata"
    assert variacoes_svc.sem_rotulos_internos("(DT23)") is None


# ─────────────── a fonte do produto: água, biometria, modelos ───────────────

TITULO_F110 = "Celular Uranyx F110 Pro 5G 8GB 256GB"


@pytest.mark.parametrize(
    "texto",
    [
        "Sim, ele é à prova d'água, com certificação IP68.",
        "Sim, é resistente à água e poeira.",
        "Sim, ele tem leitor de digital na tela e desbloqueio facial.",
    ],
)
def test_agua_e_biometria_sem_fonte_vao_para_pessoa(texto):
    pend = checagens.conferir_fonte_produto(texto, [{"item_id": "1", "titulo": TITULO_F110}])
    assert any(p.codigo == checagens.PRODUTO_SEM_FONTE for p in pend), texto


def test_agua_so_pelo_titulo_e_biometria_por_qualquer_fonte():
    relogio = [{"item_id": "1", "titulo": "Relógio IP68 à prova d'água"}]
    assert checagens.conferir_fonte_produto("Sim, é à prova d'água.", relogio) == []
    (p,) = checagens.conferir_fonte_produto("Não é à prova d'água.", relogio)
    # Rodada 4 (21/10/2026): a contradição com o anúncio é pessoa nos dois modos,
    # sem bloquear (antes, bloqueava).
    assert not p.bloqueia and not p.so_auto and p.codigo == checagens.PRODUTO_CONTRADIZ
    # A água na DESCRIÇÃO não basta (a regra do prompt: só o título).
    descricao = [{"item_id": "1", "titulo": TITULO_F110, "descricao": "Resistente à água"}]
    assert checagens.conferir_fonte_produto("É resistente à água.", descricao)
    leitor = [{"item_id": "1", "titulo": TITULO_F110, "descricao": "Leitor de digital lateral"}]
    assert checagens.conferir_fonte_produto("Sim, ele tem leitor de digital.", leitor) == []


def _espec(*campos: dict) -> list[dict]:
    return [{"item_id": "1", "especificacoes": {"campos": list(campos), "conflitos": []}}]


def test_o_valor_de_cada_variacao_vale_e_o_campo_de_varios_modelos_vai_para_pessoa():
    chips = _espec({"campo": "processador", "valor": "Dimensity 6300 ou Helio G81"})
    assert checagens.conferir_fonte_produto("O F109S tem o Helio G81.", chips) == []
    modelo = _espec(
        {"campo": "rede_5g", "valor": "sim", "fonte": "anuncio", "varia_por_modelo": True}
    )
    (p,) = checagens.conferir_fonte_produto("O F109S não é 5G.", modelo)
    assert p.codigo == checagens.PRODUTO_SEM_FONTE and not p.bloqueia
    assert checagens.conferir_fonte_produto("Ele é 5G.", modelo)


@pytest.mark.parametrize(
    ("titulo", "ram", "armazenamento"),
    [
        # 11 anúncios vivos (Shopee e TikTok): o cartão SD e a capacidade não são RAM.
        ("Uranyx F105 64 + 64 Sd Card - Resistente", [], ["64 GB"]),
        ("Hotwav A17 Pro Max 64/128 Gb Memoria 12Gb", [], ["64 GB", "128 GB"]),
        (
            "Uranyx Séries C com 64,128,256 e 512 GB Memoria",
            [],
            ["64 GB", "128 GB", "256 GB", "512 GB"],
        ),
        # O RAM.ROM de verdade continua (até 48 GB de RAM, a maior em produção).
        ("Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium", ["16 GB"], ["128 GB"]),
        ("Uranyx S7 32.256", ["32 GB"], ["256 GB"]),
        ("WP200 48.512", ["48 GB"], ["512 GB"]),
    ],
)
def test_titulos_reais_de_ram_e_memoria(titulo, ram, armazenamento):
    achados = especificacoes.achados_do_texto(titulo)
    assert [a.valor for a in achados if a.campo == "ram"] == ram
    assert [a.valor for a in achados if a.campo == "armazenamento"] == armazenamento


def test_o_anuncio_do_f105_nao_contradiz_a_ram_do_cadastro():
    ficha = {
        "titulo": "Uranyx F105 64 + 64 Sd Card - Resistente",
        "descricao": "Memoria Ram 12 GB sendo (4 GB Fisica + 8 GB Virtual)\n"
        "Memoria Interna ( 64 GB + 64 SD Card )",
    }
    r = especificacoes.juntar(
        especificacoes.do_anuncio(ficha, None),
        especificacoes.do_cadastro(["Uranyx F105 12.64 - Preto"], ["dg050"], ficha["titulo"], None),
        {},
    )
    campos = {c["campo"]: c["valor"] for c in r["campos"]}
    assert campos["ram"] == "12 GB" and campos["armazenamento"] == "64 GB"
    assert r["conflitos"] == [] and r["conflito_no_anuncio"] is False
    (p,) = checagens.conferir_fonte_produto("Ele tem 64 GB de RAM.", _espec(*r["campos"]))
    # Rodada 4: pessoa nos dois modos, sem bloquear (antes, bloqueava).
    assert p.codigo == checagens.PRODUTO_CONTRADIZ and not p.bloqueia and not p.so_auto
    assert checagens.conferir_fonte_produto("Ele tem 12 GB de RAM.", _espec(*r["campos"])) == []


def test_os_modelos_do_cadastro_e_o_5g_de_todos():
    nomes = ["Uranyx F109 5G 24.256 - Preto", "Uranyx F109S 24.256 - Azul"]
    assert especificacoes.modelos_do_cadastro(nomes) == ["uranyx f109 5g", "uranyx f109s"]
    assert especificacoes.modelos_do_cadastro(
        ["Uranyx S7 8.128 - Preto", "Uranyx S7 12.256 - Cinza", "Uranyx S7 8.128 + Capa - Preto"]
    ) == ["uranyx s7"]
    # O 5G só quando TODOS os celulares do anúncio dizem.
    assert "rede_5g" not in especificacoes.do_cadastro(nomes, [], None, None)
    assert "rede_5g" in especificacoes.do_cadastro(nomes[:1], [], None, None)
    marcado = especificacoes.marcar_varia_por_modelo(
        {
            "campos": [
                {"campo": "rede_5g", "valor": "sim", "fonte": "anuncio"},
                {"campo": "ram", "valor": "24 GB", "fonte": "anuncio"},
                {"campo": "peso", "valor": "218 g", "fonte": "site"},
            ]
        }
    )
    assert [c.get("varia_por_modelo") for c in marcado["campos"]] == [True, None, None]


def test_a_pagina_do_site_so_quando_todos_os_produtos_casam_com_ela():
    f109 = SimpleNamespace(marca="uranyx", slug="f109-5g", modelo_chave="f109 5g", specs=[])
    f109s = SimpleNamespace(marca="uranyx", slug="f109-s", modelo_chave="f109 s", specs=[])
    paginas = [f109, f109s]
    titulo = "Uranyx F109 5G 24.256 ou F109S 24.256"
    # Dois modelos e o F109S não casa: nenhuma página (antes: a do F109 5G).
    dois = ["Uranyx F109 5G 24.256 - Preto", "Uranyx F109S 24.256 - Azul"]
    assert (
        especificacoes.pagina_dos_produtos(titulo, dois, paginas, "uranyx", varios_no_anuncio=True)
        is None
    )
    # A variação selecionada do F109 5G: a página dele.
    assert (
        especificacoes.pagina_dos_produtos(
            titulo, dois[:1], paginas, "uranyx", varios_no_anuncio=True
        )
        is f109
    )
    # Um modelo só, sem produto que case: o título, como antes.
    assert (
        especificacoes.pagina_dos_produtos(
            "Uranyx F109 5G", ["Celular 24.256"], paginas, "uranyx", varios_no_anuncio=False
        )
        is f109
    )


def test_a_ram_fisica_de_dois_modelos_nao_vira_a_do_primeiro():
    unido = especificacoes._unir(
        [
            especificacoes.Achado("ram", "20 GB", frozenset({"20", "8"}), ram_fisica=8),
            especificacoes.Achado("ram", "8 GB", frozenset({"8", "4"}), ram_fisica=4),
        ]
    )
    assert unido.ram_fisica is None and unido.nota == especificacoes.NOTA_VARIA


# ─────────────── a entrega por estado ───────────────

_DO_ANUNCIO = [
    {
        "item_id": "1",
        "restricao_envio": {
            "ufs": ["RJ"],
            "fonte": "anuncio",
            "trecho": "Não enviamos para o Rio de Janeiro.",
            "ufs_conforme_pedido": [],
        },
    }
]
_DA_LOJA = [
    {
        "item_id": "1",
        "restricao_envio": {"ufs": ["RJ"], "fonte": "loja", "ufs_conforme_pedido": []},
    }
]
_CONFORME = [
    {
        "item_id": "1",
        "restricao_envio": {"ufs": [], "fonte": "loja", "ufs_conforme_pedido": ["RJ"]},
    }
]


# Rodada 4 (21/10/2026), o contrato novo: a 4ª coluna (antes "bloqueia") é a
# contradição com a restrição CERTA — pessoa nos dois modos, sem bloquear; o
# resto é a regra suave.
@pytest.mark.parametrize(
    ("texto", "fichas", "codigo", "contradiz"),
    [
        ("Olá! Entregamos sim para o Rio de Janeiro.", _DO_ANUNCIO, "regiao_contradiz", True),
        ("Olá! Não enviamos para o RJ.", _DA_LOJA, "regiao_sem_fonte", False),
        ("Entregamos para o Rio de Janeiro.", _CONFORME, "regiao_sem_fonte", False),
        ("Entregamos para SP sim!", [], "regiao_sem_fonte", False),
    ],
)
def test_regiao_contra_a_restricao(texto, fichas, codigo, contradiz):
    (p,) = checagens.conferir_regiao(texto, fichas)
    assert not p.bloqueia
    assert (p.codigo, not p.so_auto) == (codigo, contradiz)


def test_regiao_que_o_anuncio_diz_passa():
    citando = (
        "Conforme informado no anúncio, não realizamos envios para o Rio de Janeiro. Você "
        "pode conferir pelo CEP no anúncio."
    )
    assert checagens.conferir_regiao(citando, _DO_ANUNCIO) == []
    assert checagens.conferir_regiao("Entregamos para SP sim!", _DO_ANUNCIO) == []
    # A origem do envio não é destino.
    assert checagens.conferir_regiao("O envio deste pedido será feito de São Paulo.", []) == []


# ─────────────── garantia, defeito e o prazo da regra (validador) ───────────────


@pytest.mark.parametrize(
    ("texto", "nega"),
    [
        ("Infelizmente a garantia já expirou, então não conseguimos ajudar com o defeito.", True),
        ("Esse produto não possui garantia.", True),
        ("A garantia de hardware do seu aparelho já venceu em 10/04/2026.", True),
        ("Seu aparelho está fora da garantia.", True),
        ("Seu pedido não tem mais garantia.", True),
        ("A garantia acabou, sinto muito.", True),
        ("Sua garantia ainda não venceu.", False),
        ("Sua garantia de hardware termina em 07/01/2027.", False),
        ("A garantia é de 3 meses para hardware.", False),
        ("Os acessórios vêm sem garantia estendida.", False),
    ],
)
def test_nega_garantia(texto, nega):
    assert validador.nega_garantia(texto) is nega


@pytest.mark.parametrize(
    ("texto", "reset"),
    [
        ("Sinto muito! Antes, tente fazer a restauração de fábrica do celular.", True),
        ("Tente formatar o aparelho e, se continuar, nos mande um vídeo por aqui.", True),
        ("Faça um reset no aparelho segurando o botão.", True),
        ("Restaure as configurações de fábrica.", True),
        ("Mande o vídeo em qualquer formato.", False),
        ("Tente reiniciar o aparelho.", False),
    ],
)
def test_sugere_reset(texto, reset):
    assert validador.sugere_reset(texto) is reset


URANYX = validador.prazos_da_regra([{"regra": {"hardware_meses": 3, "software_meses": 12}}])
CHARLOTS = validador.prazos_da_regra([{"regra": {"dias": 90}}])


def _valida(texto: str, prazos: Any) -> list[str]:
    return validador.validar(
        texto, plataforma="shopee", canal="chat", origem="davinci_ia", prazos_de_garantia=prazos
    )


def test_o_prazo_da_regra_so_com_o_seu_par():
    assert URANYX == {(3, "mes", "hardware"), (12, "mes", "software")}
    for certo in (
        "Olá! A garantia é de 3 meses para hardware e 12 meses para software, contados da entrega.",
        "A garantia de hardware é de 3 meses e a de software, de 12 meses.",
    ):
        assert _valida(certo, URANYX) == [], certo
    for errado in (
        "Olá! A garantia do aparelho é de 12 meses.",
        "A garantia é de 3 meses e a entrega leva 12 meses.",
        "A garantia de hardware é de 3 meses, e o reembolso sai em 3 meses.",
        "Se precisar acionar a garantia, a troca sai em 3 meses.",
    ):
        assert _valida(errado, URANYX), errado
    assert (
        _valida("As malas têm garantia de 90 dias contra defeitos de fabricação.", CHARLOTS) == []
    )
    assert _valida("A garantia é de 90 dias e a entrega leva 90 dias.", CHARLOTS)


def test_a_regra_da_linha_sem_status_nao_e_promessa_de_cobertura():
    texto = "As malas Charlots têm garantia de 90 dias contra defeitos de fabricação."
    sem_status = [{"status": None, "regra": {"dias": 90}}]
    assert validador.conferir_garantia(texto, sem_status, prazos_de_garantia=CHARLOTS) == []
    # O legado (sem os prazos): como sempre.
    assert validador.conferir_garantia(texto, sem_status) == [
        validador.MOTIVO_COBERTURA_SEM_GARANTIA
    ]
    # A promessa sobre o aparelho do cliente, sem status, continua barrada.
    assert validador.conferir_garantia(
        "Seu aparelho está coberto.", sem_status, prazos_de_garantia=CHARLOTS
    )


@pytest.mark.parametrize(
    ("texto", "afirma"),
    [
        ("Seu pedido já foi enviado e está a caminho! Pode me passar o número?", True),
        ("Seu pedido foi cancelado e o reembolso já foi feito pela plataforma.", True),
        ("Seu pedido já foi entregue!", True),
        ("Olá! Pode me passar o número do pedido? Ele aparece em Minhas compras.", False),
        ("Você acompanha o envio em Minhas Compras > o pedido > Informações do envio.", False),
    ],
)
def test_afirma_do_pedido(texto, afirma):
    assert ia.afirma_do_pedido(texto) is afirma


# ─────────────── a régua de código (acao_pessoa) ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "Cadê meu pedido? Se não chegar amanhã vou abrir uma reclamação na Shopee.",
        "Ainda não chegou, vou abrir disputa.",
        "Quando vai ser enviado? Senão vou pedir mediação.",
    ],
)
def test_2_ameaca_de_reclamacao_disputa_ou_mediacao(texto):
    assert "juridico_fraude_ofensa" in _codigos(_gat([_cliente(texto, 5)]))


def test_7_e_8_com_o_estado_de_postagem_desconhecido_e_o_cancelado():
    # O estado desconhecido conta como "pode não ter saído" quando HÁ pedido
    # (revisão 2 de 20/10/2026: sem pedido nenhum, é a pré-venda — orientação).
    com_pedido = _conv(pedido_marketplace="251020ABCDEF")
    (g,) = _gat([_cliente("Quero cancelar, comprei ontem", 5)], postado=None, conversa=com_pedido)
    assert g.codigo == "segurar_envio"
    assert acao_pessoa.DETALHE_POSTAGEM_DESCONHECIDA in g.motivo
    assert _gat([_cliente("Quero cancelar, comprei ontem", 5)], postado=None) == []
    assert _gat([_cliente("quero cancelar", 5)], postado=acao_pessoa.POSTADO_CANCELADO) == []
    assert acao_pessoa.postado_de("12", None, agora=_AGORA) == acao_pessoa.POSTADO_CANCELADO
    assert acao_pessoa.postado_de("83962", None, agora=_AGORA) is None
    assert acao_pessoa.pode_nao_ter_saido(None, _conv())
    assert not acao_pessoa.pode_nao_ter_saido(None, _conv(canal=constantes.CANAL_PERGUNTA))


@pytest.mark.parametrize(
    "texto",
    [
        "consta como entregue mas não recebi",
        "no app aparece entregue mas não chegou nada",
        "o app da shopee mostra entregue, mas não recebi o produto",
    ],
)
def test_11_consta_entregue_sem_a_logistica_do_espelho(texto):
    for ctx in ({"logistica": {"status": "Objeto em trânsito"}}, {}):
        assert "abrir_chamado" in _codigos(_gat([_cliente(texto, 5)], ctx=ctx)), (texto, ctx)


def test_11_a_pergunta_de_entregue_nao_e_afirmacao():
    em_transito = {"logistica": {"status": "Objeto em trânsito"}}
    assert _gat([_cliente("já está entregue? não recebi ainda", 5)], ctx=em_transito) == []


@pytest.mark.parametrize(
    "texto",
    [
        "paguei no pix, quando cai o reembolso?",
        "paguei com pix, o estorno volta pra onde?",
        "comprei no pix, quanto tempo demora o reembolso?",
        "aceita pix?",
        "o pix não caiu ainda",
    ],
)
def test_12_o_pix_da_compra_e_orientacao(texto):
    assert "aprovar_fora_da_plataforma" not in _codigos(_gat([_cliente(texto, 5)])), texto


def test_12_e_13_o_dinheiro_por_fora_e_o_brinde_continuam():
    for texto in ("pode me devolver no pix?", "minha chave pix é a do celular"):
        assert _codigos(_gat([_cliente(texto, 5)])) == ["aprovar_fora_da_plataforma"], texto
    for texto in ("a troca é grátis?", "a devolução é de graça?"):
        assert _gat([_cliente(texto, 5)]) == [], texto
    for texto in ("vem algo grátis junto?", "manda um brinde?", "vem com algum mimo?"):
        assert _codigos(_gat([_cliente(texto, 5)])) == ["desconto_brinde"], texto


def test_10_a_foto_junto_do_texto_no_ml():
    foto = [{"arquivo": "a.jpg", "tipo": "image/jpeg"}]
    pediu = [
        _cliente("chegou com defeito", 30),
        _loja("Poderia nos enviar um vídeo curto mostrando o defeito, por favor?", 20),
        _cliente("segue a foto do defeito", 5, anexos=foto),
    ]
    (g,) = _gat(pediu)
    assert g.codigo == "analisar_evidencia" and g.antes_do_modelo is False
    assert acao_pessoa.midia_da_mensagem(pediu[-1]) == "foto"
    assert acao_pessoa.midia_da_mensagem(_cliente("oi", 1, anexos=[{"tipo": "produto"}])) is None


def test_as_pistas_de_orientacao_do_turno():
    misto = [_cliente("Cadê meu pedido? Se não chegar quero cancelar", 5)]
    assert acao_pessoa.pistas_de_orientacao(misto, misto[-1]) == ["cancelamento"]
    assert acao_pessoa.pistas_de_orientacao([_cliente("cadê meu pedido?", 5)], None) == []


def test_os_gatilhos_de_estado_de_outro_pedido():
    turno = [_cliente("e o meu outro pedido?", 5)]
    em_83955 = {
        "pedido": {
            "situacao": "Aguardando Cancelamento",
            "ag_cancelamento": {"codigo": "sem_estoque"},
        }
    }
    (g,) = acao_pessoa.gatilhos_de_outro_pedido(em_83955, None, turno, turno[-1])
    assert g.codigo == "ag_cancelamento" and "83955" not in g.motivo
    reclamado = {"pedido": {"situacao": "Atendido"}, "reclamacoes": [{"aberta": True}]}
    assert _codigos(
        acao_pessoa.gatilhos_de_outro_pedido(reclamado, acao_pessoa.POSTADO_SIM, turno, turno[-1])
    ) == ["reclamacao_aberta"]
    cancelar = [_cliente("quero cancelar o pedido 2410ABC", 5)]
    nao_postado = {"pedido": {"situacao": "Em aberto"}}
    assert _codigos(
        acao_pessoa.gatilhos_de_outro_pedido(
            nao_postado, acao_pessoa.POSTADO_NAO, cancelar, cancelar[-1]
        )
    ) == ["segurar_envio"]
    assert (
        acao_pessoa.gatilhos_de_outro_pedido(
            nao_postado, acao_pessoa.POSTADO_SIM, cancelar, cancelar[-1]
        )
        == []
    )


# ─────────────── a linha da garantia (ferramentas) ───────────────


@pytest.mark.parametrize(
    ("sku", "descricao", "linha"),
    [
        ("dg089.ci", "Uranyx A18 Pro Max", ferramentas.LINHA_URANYX),
        ("u0012", "Air Fryer Uranyx", ferramentas.LINHA_URANYX),
        ("a003.ci", "Fone Uranyx UFB10", ferramentas.LINHA_URANYX),
        ("a006", "Kit 2 Rodinha Dtluggage", ferramentas.LINHA_OUTRA),
        ("a015", "Balança de Mala", ferramentas.LINHA_OUTRA),
        ("a009.cd", "Fonte Carregador Motorola", ferramentas.LINHA_OUTRA),
        ("b012", "Mala de Bordo 20", ferramentas.LINHA_CHARLOTS),
    ],
)
def test_a_linha_do_item(sku, descricao, linha):
    assert ferramentas.linha_do_item(sku, descricao) == linha


def test_o_pedido_com_mala_e_acessorio_e_da_charlots():
    info = SimpleNamespace(
        itens=[
            {"sku": "a006", "descricao": "Kit 2 Rodinha Dtluggage", "uranyx": True},
            {"sku": "b012", "descricao": "Mala de Bordo 20", "uranyx": False},
        ]
    )
    linha, item = ferramentas._linha_do_pedido(info)  # type: ignore[arg-type]
    assert linha == ferramentas.LINHA_CHARLOTS and item["sku"] == "b012"
    assert sites_marca.CHARLOTS == "charlots"


# ─────────────── pares sem dado pessoal de terceiro ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "O pedido foi entregue para Joana Ribeiro na portaria",
        "manda pro meu marido Roberto Carlos Pereira",
        "Boa tarde, sou Ana Paula",
        "Meu endereço é Rua das Flores, apto 12, bairro Centro",
        "Condomínio Vila Verde bloco B apto 204",
    ],
)
def test_par_com_dado_pessoal_sai(texto):
    contagem: dict[str, int] = {}
    resposta = "Olá! Recebemos o seu recado e seguimos acompanhando."
    assert not pares._filtro(
        texto + ", obrigada", resposta, plataforma="shopee", canal="chat", contagem=contagem
    )
    assert contagem == {"dado_pessoal": 1}


def test_par_sem_dado_pessoal_fica():
    assert pares._filtro(
        "o meu marido vai receber o pedido amanhã?",
        "Olá! A entrega segue o endereço do pedido, pode ficar tranquilo.",
        plataforma="shopee",
        canal="chat",
        contagem=None,
    )


# ─────────────── o teto do replay e o tempo da rodada ───────────────


def test_o_replay_tem_a_chave_propria_da_conversa_e_o_teto_da_vez():
    dia_ao_vivo, conversa_ao_vivo = custo._chaves("c1", "2026-10-20")
    dia_replay, conversa_replay = custo._chaves("c1", "2026-10-20", replay=True)
    # 28/10/2026: o dia dos testes também é outro (o eval não para o ao vivo).
    assert dia_ao_vivo != dia_replay and dia_replay.endswith(":2026-10-20:replay")
    assert conversa_ao_vivo != conversa_replay
    assert ":replay:conversa:c1" in (conversa_replay or "")
    assert custo._tetos(0.5)[1] == 0.5 and custo._tetos(0)[1] == 0
    # A requisição que estourou o tempo: o custo provável entra no teto.
    assert custo.estimativa_de_requisicao_perdida("claude-haiku-5-5", 4000) > 0.003


def test_o_prazo_do_agente_na_rodada_e_no_sugerir(monkeypatch):
    import time as _time

    assert ia.prazo_do_agente(True) == ia.PRAZO_SUGERIR_COOPERATIVO_S
    token = ia._INICIO_DA_RODADA.set(_time.monotonic() - 150)
    try:
        assert ia.prazo_do_agente(False) == ia.PRAZO_MINIMO_DO_AGENTE_S
    finally:
        ia._INICIO_DA_RODADA.reset(token)
    token = ia._INICIO_DA_RODADA.set(_time.monotonic())
    try:
        # Item 5 da rodada "redondo" (23/10/2026): o prazo do Sonnet é 60 s (o pior caso da
        # rodada continua limitado pelo `AGENTE_FIM_DO_LACO_S`).
        assert ia.prazo_do_agente(False) <= 60
    finally:
        ia._INICIO_DA_RODADA.reset(token)
    # A margem da rodada (300 s do job): o pior caso de uma conversa começada
    # no último segundo cabe antes do fim.
    assert ia.AGENTE_SEM_CONVERSA_NOVA_S < ia.AGENTE_FIM_DO_LACO_S < ia.TEMPO_DA_RODADA_S - 90
