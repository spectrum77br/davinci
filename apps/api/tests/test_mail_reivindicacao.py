"""Reivindicação A a Z e estorno da Amazon por e-mail — as partes PURAS (16/11/2026).

Pedido 306097 (Amazon Kia, 702-3809368-8549006): a Amazon CONCEDEU uma
reivindicação da Garantia de A a Z de R$ 865,59 e o resumo do pedido dizia
"Chamados: nenhum" — a mensagem do cliente (13/11) estava no Atendimento e o
e-mail da decisão (de `atoz-guarantee-no-reply@amazon.com`) tinha entrado só
como aviso INFORMATIVO numa conversa "não precisa de resposta".

  B) o e-mail da reivindicação/estorno vai para a FILA de pessoa (tipo
     `chamado`), sem criar chamado — e o e-mail comum da Amazon não cai nisso;
  C) o espelho em `atendimento_reclamacoes` só pelo remetente do MODELO;
  A) a linha da conversa no `consultar_pedido` (o bloco "Atendimento").

Os textos abaixo são o MODELO da Amazon (encurtado), sem dado de comprador.
Os testes com banco (gravar, ponte, consultar_pedido) estão em
`test_mail_reivindicacao_ponte.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models import AtendimentoReclamacao
from app.services import claude_tarefas
from app.services.atendimento import etiqueta_fatos, reclamacoes
from app.services.mail_atendimento import classificacao, reivindicacao

PEDIDO = "702-3809368-8549006"
ATOZ = "atoz-guarantee-no-reply@amazon.com"
CB = "cb-seller-notification@amazon.com.br"

DECISAO_ASSUNTO = f"Decisão da reivindicação referente ao pedido {PEDIDO}"
DECISAO_TEXTO = (
    "Olá, Kia Comercio,\n\n"
    "Nós concedemos uma reivindicação da Garantia de A a Z de R$ 865,59 referente ao "
    f"pedido {PEDIDO}. Debitamos o valor de sua conta e contabilizamos a reivindicação "
    "em seu Índice de pedidos com problemas.\n\n"
    "Neste caso, o cliente alegou um problema com a entrega e as informações de "
    "rastreamento fornecidas por você foram insuficientes ou o pedido não foi realmente "
    "recebido pelo cliente.\n\n"
    "Como posso recorrer no caso de a reivindicação ter sido concedida por engano?\n"
    "Se você não concorda com a nossa decisão e tem informações novas para analisarmos, "
    "você tem a opção de recorrer em até 30 dias corridos na página de reivindicações da "
    "Garantia de A a Z no Seller Central.\n"
)

ESTORNO_PEDIDO = "702-8218469-5748230"
ESTORNO_ASSUNTO = f"Seu pedido da Amazon.com.br {ESTORNO_PEDIDO}: Informação necessária"
ESTORNO_TEXTO = (
    "Olá,\nGostaríamos de informar que a operadora do cartão entrou em contato conosco em "
    "nome do cliente. No momento, há uma contestação de estorno envolvendo a seguinte "
    f"transação:\nNúmero do pedido: {ESTORNO_PEDIDO}\n"
    "Produtos comprados: 1 of (Celular) for 1088.0 BRL\n"
    "Em resposta a este e-mail, forneça as seguintes informações:\n"
    "-- Confirmação de que o pedido foi enviado.\n"
    "Observe que deixar de responder a esta consulta dentro de sete (7) dias do envio "
    "deste e-mail com informações suficientes pode resultar em débito de sua conta.\n"
)


# ─────────────── B/C: o que o e-mail diz ───────────────


def test_decisao_concedida_do_306097():
    r = reivindicacao.ler(
        de=ATOZ, plataforma="amazon", assunto=DECISAO_ASSUNTO, texto=DECISAO_TEXTO
    )
    assert r is not None
    assert (r.tipo, r.status, r.pedido) == ("atoz", "concedida", PEDIDO)
    assert r.oficial and r.da_amazon and not r.aberta
    assert r.valor == 865.59 and r.dias_recurso == 30 and r.dias_prazo is None
    assert r.motivo is not None and r.motivo.startswith("Problema com a entrega")
    assert r.externo_id == f"atoz:{PEDIDO}"
    quando = datetime(2026, 11, 16, 16, 9, tzinfo=UTC)
    assert reivindicacao.status_texto(r, quando) == (
        "Concedida ao comprador — R$ 865,59 debitado da conta; "
        "dá para recorrer até 16/12/2026 no Seller Central"
    )
    assert reivindicacao.acao_texto(r) is None


def test_estorno_informacao_necessaria():
    r = reivindicacao.ler(de=CB, plataforma=None, assunto=ESTORNO_ASSUNTO, texto=ESTORNO_TEXTO)
    assert r is not None
    assert (r.tipo, r.status, r.pedido) == ("chargeback", "informacao_necessaria", ESTORNO_PEDIDO)
    assert r.oficial and r.aberta and r.dias_prazo == 7
    assert r.valor == 1088.0
    assert r.motivo == "Contestação de estorno aberta pela operadora do cartão"
    assert r.externo_id == f"cb:{ESTORNO_PEDIDO}"
    assert "comprovante" in (reivindicacao.acao_texto(r) or "")


def test_estorno_pelo_assunto_de_outro_endereco_da_amazon():
    # O mesmo "Informação necessária" vindo de outro endereço da Amazon: fila,
    # mas NÃO é o remetente do modelo (não vira linha da plataforma).
    r = reivindicacao.ler(
        de="seller-notification@amazon.com.br",
        plataforma="amazon",
        assunto=ESTORNO_ASSUNTO,
        texto=ESTORNO_TEXTO,
    )
    assert r is not None and r.tipo == "chargeback"
    assert r.da_amazon and not r.oficial


@pytest.mark.parametrize(
    "assunto,texto,status",
    [
        (
            f"Reivindicação da Garantia de A a Z referente ao pedido {PEDIDO}",
            "Um cliente registrou uma reivindicação. Responda em até 3 dias. Se a "
            "reivindicação for concedida, o valor será debitado.",
            "aberta",
        ),
        (
            DECISAO_ASSUNTO,
            "Analisamos a reivindicação e decidimos a seu favor. Nenhum valor será debitado.",
            "negada",
        ),
        (
            DECISAO_ASSUNTO,
            "Não concedemos a reivindicação: as informações de rastreamento confirmam a entrega.",
            "negada",
        ),
        (
            f"Atualização da reivindicação do pedido {PEDIDO}",
            "O comprador retirou a reivindicação da Garantia de A a Z.",
            "retirada",
        ),
        (DECISAO_ASSUNTO, "Veja os detalhes no Seller Central.", "decidida"),
    ],
)
def test_status_pelo_modelo(assunto, texto, status):
    r = reivindicacao.ler(de=ATOZ, plataforma=None, assunto=assunto, texto=texto)
    assert r is not None and r.status == status


# 16/11 (Eduardo, revisão): textos plausíveis que a 1ª versão lia do lado errado.
@pytest.mark.parametrize(
    "assunto,texto,status",
    [
        # "não debitamos" contém "debitamos": a negação vale (a loja ganhou).
        (
            DECISAO_ASSUNTO,
            "A reivindicação foi encerrada a seu favor. Não debitamos nenhum valor da sua conta.",
            "negada",
        ),
        (f"Atualização da reivindicação do pedido {PEDIDO}", "Não haverá débito.", "negada"),
        (
            f"Atualização da reivindicação do pedido {PEDIDO}",
            "Nenhum valor será debitado da sua conta.",
            "negada",
        ),
        (
            f"Reivindicação da Garantia de A a Z referente ao pedido {PEDIDO}",
            "A reivindicação foi retirada pelo cliente.",
            "retirada",
        ),
        (
            f"Atualização da reivindicação do pedido {PEDIDO}",
            "O comprador cancelou a reivindicação da Garantia de A a Z.",
            "retirada",
        ),
        # O trecho de AJUDA no e-mail da concedida não vira "negada".
        (
            DECISAO_ASSUNTO,
            "Nós concedemos a reivindicação. Se a reivindicação foi negada por engano, "
            "fale conosco.",
            "concedida",
        ),
        # "não recebeu e debitamos": o "não" é do recebimento, não do débito.
        (
            f"Atualização da reivindicação do pedido {PEDIDO}",
            "Neste caso o cliente alegou que não recebeu e debitamos o valor da sua conta.",
            "concedida",
        ),
        # O "quando" do relato não é hipótese.
        (
            f"Atualização da reivindicação do pedido {PEDIDO}",
            "Debitamos o valor quando o comprador abriu a reivindicação.",
            "concedida",
        ),
        # Os dois lados afirmados no mesmo e-mail: decidida (ver o e-mail).
        (
            f"Atualização da reivindicação do pedido {PEDIDO}",
            "Concedemos a reivindicação. Decidimos a seu favor.",
            "decidida",
        ),
        # "Reivindicação encerrada" no assunto, sem o lado: decidida.
        (
            f"Reivindicação encerrada — pedido {PEDIDO}",
            "Veja os detalhes no Seller Central.",
            "decidida",
        ),
    ],
)
def test_status_dos_textos_da_revisao(assunto, texto, status):
    r = reivindicacao.ler(de=ATOZ, plataforma=None, assunto=assunto, texto=texto)
    assert r is not None and r.status == status and r.reconhecido


@pytest.mark.parametrize(
    "texto,status",
    [
        ("A contestação de estorno foi resolvida a seu favor.", "negada"),
        ("A contestação foi resolvida a favor do titular do cartão.", "concedida"),
        ("A operadora do cartão retirou a contestação.", "retirada"),
        ("A contestação de estorno foi cancelada pelo banco emissor.", "retirada"),
        ("O banco emissor cancelou o chargeback.", "retirada"),
    ],
)
def test_status_do_estorno(texto, status):
    r = reivindicacao.ler(
        de=CB, plataforma=None, assunto=f"Contestação de estorno do pedido {PEDIDO}", texto=texto
    )
    assert r is not None and r.tipo == "chargeback" and r.status == status and r.reconhecido
    if status == "negada":
        quando = datetime(2026, 11, 16, 16, 9, tzinfo=UTC)
        assert reivindicacao.status_texto(r, quando) == "Resolvido a favor da loja — sem débito"


def test_texto_que_nao_diz_a_situacao_e_palpite():
    """Sem frase conhecida: "aberta", mas NÃO reconhecida — o `gravar` não muda
    nem reabre a linha que já existe (só cria a primeira)."""
    for texto in (
        "Veja suas reivindicações abertas no Seller Central.",
        "A reivindicação foi retirada do seu ODR.",
        "Prezada equipe de Suporte ao Parceiro de Vendas, ...",
    ):
        r = reivindicacao.ler(
            de=ATOZ,
            plataforma=None,
            assunto=f"Atualização da reivindicação do pedido {PEDIDO}",
            texto=texto,
        )
        assert r is not None and r.status == "aberta" and not r.reconhecido, texto
        quando = datetime(2026, 11, 16, 16, 9, tzinfo=UTC)
        assert "não diz a situação" in reivindicacao.status_texto(r, quando)
        assert reivindicacao.acao_texto(r) == (
            "ler o e-mail da Amazon e conferir o caso no Seller Central"
        )
    # A aberta dita com todas as letras é reconhecida.
    r = reivindicacao.ler(
        de=ATOZ,
        plataforma=None,
        assunto=f"Reivindicação da Garantia de A a Z referente ao pedido {PEDIDO}",
        texto="Uma reivindicação foi registrada pelo comprador.",
    )
    assert r is not None and r.status == "aberta" and r.reconhecido
    # O "Informação necessária" do estorno também.
    r = reivindicacao.ler(de=CB, plataforma=None, assunto=ESTORNO_ASSUNTO, texto=ESTORNO_TEXTO)
    assert r is not None and r.reconhecido


def test_reivindicacao_aberta_guarda_o_prazo():
    r = reivindicacao.ler(
        de=ATOZ,
        plataforma=None,
        assunto=f"Reivindicação da Garantia de A a Z referente ao pedido {PEDIDO}",
        texto="Um cliente registrou uma reivindicação. Responda em até 3 dias.",
    )
    assert r is not None and r.aberta and r.dias_prazo == 3 and r.dias_recurso is None


def test_copia_do_caso_que_a_loja_abriu_vai_para_a_fila_sem_virar_linha():
    """16/11 20:59: a cópia do caso aberto no Suporte ("Retirada do ODR -
    reivindicação A a Z do pedido …") diz "reivindicação" — vai à fila pelo
    assunto, mas não é o modelo: não pode reabrir a decisão no espelho."""
    r = reivindicacao.ler(
        de="seller-support@amazon.com.br",
        plataforma="amazon",
        assunto=f"Retirada do ODR - reivindicação A a Z do pedido {PEDIDO}",
        texto="---Original Message---\nPrezada equipe de Suporte ao Parceiro de Vendas, ...",
    )
    assert r is not None and r.tipo == "atoz" and r.status == "aberta"
    assert r.da_amazon and not r.oficial


@pytest.mark.parametrize(
    "de,plataforma,assunto,texto",
    [
        # Os outros avisos da Amazon do mesmo pedido continuam informativos.
        (
            "donotreply@amazon.com.br",
            "amazon",
            f"Reembolso de 865.59 BRL iniciado para o pedido {PEDIDO}",
            f"Reembolso de 865.59 BRL iniciado para o pedido {PEDIDO}",
        ),
        (
            "donotreply@amazon.com.br",
            "amazon",
            f"Importante Pedido {PEDIDO}: entrega registrada pela transportadora",
            "Você recebeu uma mensagem.",
        ),
        (
            "donotreply@amazon.com.br",
            "amazon",
            "Vendido, envie agora: Celular 4G 128GB",
            "Data de envio: 27/10/2026",
        ),
        # "estorno" sozinho é também o reembolso que a própria loja deu.
        (
            "donotreply@amazon.com.br",
            "amazon",
            f"Estorno do pedido {PEDIDO} processado",
            "O estorno foi processado.",
        ),
        # A novidade sobre a política, sem pedido: informativa.
        (
            "seller-news@amazon.com.br",
            "amazon",
            "Novidades sobre a Garantia de A a Z",
            "Conheça as mudanças na política da Garantia de A a Z.",
        ),
        # Outras plataformas: "reclamação"/"chargeback" é outro caminho.
        (
            "nao-responder@mercadolivre.com.br",
            "ml",
            "Você tem uma reivindicação aberta",
            "Pedido 2000012345678901",
        ),
        (
            "noreply@shopee.com.br",
            "shopee",
            f"Chargeback do pedido {PEDIDO}",
            "Disputa aberta.",
        ),
        # Domínio que só PARECE da Amazon (o suspeito fica com a régua dele).
        (
            "atoz-guarantee-no-reply@amazon-seller.com",
            None,
            DECISAO_ASSUNTO,
            DECISAO_TEXTO,
        ),
        (
            "atoz-guarantee-no-reply@amazon.com.br.example.com",
            None,
            DECISAO_ASSUNTO,
            DECISAO_TEXTO,
        ),
    ],
)
def test_email_comum_nao_cai_na_reivindicacao(de, plataforma, assunto, texto):
    assert reivindicacao.ler(de=de, plataforma=plataforma, assunto=assunto, texto=texto) is None


def test_retransmissao_do_comprador_nao_e_a_amazon():
    de = "abc123@marketplace.amazon.com.br"
    assert not reivindicacao.e_da_amazon(de) and not reivindicacao.e_oficial(de)
    # O comprador que fala em reivindicação continua na fila (é cliente), mas o
    # que ele escreve nunca vira linha da plataforma.
    r = reivindicacao.ler(
        de=de,
        plataforma="amazon",
        assunto=f"Vou abrir uma reivindicação de garantia de A a Z do pedido {PEDIDO}",
        texto="Não recebi.",
    )
    assert r is not None and not r.da_amazon and not r.oficial


@pytest.mark.parametrize(
    "endereco,esperado",
    [
        ("atoz-guarantee-no-reply@amazon.com", True),
        ("ATOZ-Guarantee-No-Reply@Amazon.com.br", True),
        ("cb-seller-notification@amazon.com.br", True),
        ("cb-seller-notification@marketplace.amazon.com.br", False),
        ("atoz-guarantee-no-reply@amazon-seller.com", False),
        ("donotreply@amazon.com", False),
        (None, False),
        # 16/11 (revisão): a família do remetente do modelo, só no domínio da Amazon.
        ("atoz-guarantee@amazon.com.br", True),
        ("cb-seller-query@amazon.com", True),
        ("atoz-guarantee@amazon.com.br.example.com", False),
        ("cbseller@amazon.com", False),
    ],
)
def test_remetente_do_modelo(endereco, esperado):
    assert reivindicacao.e_oficial(endereco) is esperado


def test_familia_do_remetente_da_o_tipo():
    r = reivindicacao.ler(
        de="cb-seller-query@amazon.com",
        plataforma=None,
        assunto=ESTORNO_ASSUNTO,
        texto=ESTORNO_TEXTO,
    )
    assert r is not None and r.tipo == "chargeback" and r.oficial


# ─────────────── B: a classificação manda para a fila ───────────────


def test_classificacao_reivindicacao_e_chamado_sem_caso():
    c = classificacao.classificar(
        plataforma="amazon",
        finalidade="entrada",
        assunto=DECISAO_ASSUNTO,
        texto=DECISAO_TEXTO,
        remetente_tipo="aviso",
        remetente=ATOZ,
    )
    assert c.tipo_email == "chamado" and c.caso_id is None
    assert not c.tipo_inferido and not c.casos_ambiguos
    # Sem o remetente (legado), o assunto num e-mail da loja Amazon basta.
    c = classificacao.classificar(
        plataforma="amazon",
        finalidade="entrada",
        assunto=DECISAO_ASSUNTO,
        texto=DECISAO_TEXTO,
        remetente_tipo="aviso",
    )
    assert c.tipo_email == "chamado"
    c = classificacao.classificar(
        plataforma="amazon",
        finalidade="entrada",
        assunto=ESTORNO_ASSUNTO,
        texto=ESTORNO_TEXTO,
        remetente_tipo="aviso",
        remetente=CB,
    )
    assert c.tipo_email == "chamado"


def test_classificacao_dos_outros_avisos_da_amazon_nao_muda():
    for assunto in (
        f"Reembolso de 865.59 BRL iniciado para o pedido {PEDIDO}",
        f"Importante Pedido {PEDIDO}: entrega registrada pela transportadora",
    ):
        c = classificacao.classificar(
            plataforma="amazon",
            finalidade="entrada",
            assunto=assunto,
            texto="Você recebeu uma mensagem.",
            remetente_tipo="aviso",
            remetente="donotreply@amazon.com.br",
        )
        assert c.tipo_email == "informativo", assunto


# ─────────────── C: a linha na tela e na etiqueta ───────────────


def _linha_amazon(**kw) -> AtendimentoReclamacao:
    base = {
        "plataforma": "amazon",
        "externo_id": f"atoz:{PEDIDO}",
        "tipo": "reclamacao",
        "status": "concedida",
        "pedido_marketplace": PEDIDO,
        "motivo": "Problema com a entrega",
        "aberta_em": datetime(2026, 11, 16, 16, 9, tzinfo=UTC),
        "encerrada_em": datetime(2026, 11, 16, 16, 9, tzinfo=UTC),
        "dados": {
            "fonte": "email",
            "tipo_caso": "atoz",
            "status_texto": "Concedida ao comprador — R$ 865,59 debitado da conta",
            "acao_texto": None,
            "valor_reembolso": 865.59,
            "moeda": "BRL",
        },
    }
    base.update(kw)
    return AtendimentoReclamacao(**base)


def test_para_tela_da_reivindicacao_amazon():
    t = reclamacoes.para_tela(_linha_amazon())
    assert t["plataforma_nome"] == "Amazon"
    assert t["tipo_rotulo"] == "Reivindicação A a Z" and t["numero"] is None
    assert t["status_rotulo"].startswith("Concedida ao comprador")
    assert t["aberta"] is False and t["acao_pendente"] is None
    assert t["valor_reembolso"] == 865.59 and t["moeda"] == "BRL"
    assert t["url_plataforma"] == f"https://sellercentral.amazon.com.br/orders-v3/order/{PEDIDO}"
    estorno = _linha_amazon(
        externo_id=f"cb:{PEDIDO}",
        status="informacao_necessaria",
        encerrada_em=None,
        dados={"tipo_caso": "chargeback", "acao_texto": "responder ao e-mail do estorno"},
    )
    t = reclamacoes.para_tela(estorno)
    assert t["tipo_rotulo"] == "Estorno (chargeback)" and t["aberta"] is True
    assert t["acao_pendente"] == "responder ao e-mail do estorno"


def test_motivo_da_etiqueta_da_reivindicacao_amazon():
    assert etiqueta_fatos._motivo_reclamacao(_linha_amazon(encerrada_em=None)) == (
        f"Reivindicação A a Z do pedido {PEDIDO} aberta na Amazon"
    )


# ─────────────── A: a conversa no resumo do pedido ───────────────


def _conversa(**kw) -> SimpleNamespace:
    base = {
        "canal": "email",
        "plataforma": "amazon",
        "conta": "Kia",
        "situacao": "respondida",
        "aguardando_resposta": False,
        "sem_resposta_necessaria": False,
        "prazo_resposta_em": None,
        "ultima_do_cliente_em": None,
        "ultima_da_loja_em": None,
        "dados": {},
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_linha_da_conversa_do_cliente():
    c = _conversa(
        ultima_do_cliente_em=datetime(2026, 11, 13, 21, 34, tzinfo=UTC),
        ultima_da_loja_em=datetime(2026, 11, 14, 10, 22, tzinfo=UTC),
        dados={"assunto": "Pedido não recebido"},
    )
    linha = claude_tarefas.linha_da_conversa(c, [], de_email=False)
    assert linha == (
        "e-mail Amazon (Kia) [Caixa] · respondida · última do cliente 13/11 18:34 · "
        "última da loja 14/11 07:22 · assunto: Pedido não recebido"
    )


def test_linha_da_conversa_de_email_com_os_avisos_da_plataforma():
    c = _conversa(sem_resposta_necessaria=True)
    avisos = [
        (datetime(2026, 11, 16, 16, 9, tzinfo=UTC), DECISAO_ASSUNTO),
        (datetime(2026, 11, 16, 19, 17, tzinfo=UTC), "Reembolso de 865.59 BRL iniciado"),
    ]
    linha = claude_tarefas.linha_da_conversa(c, avisos, de_email=True)
    assert linha.startswith('e-mail Amazon (Kia) [aba E-mail] · marcada "não precisa de resposta"')
    assert f"aviso da plataforma 16/11 13:09: {DECISAO_ASSUNTO}" in linha
    assert linha.endswith("aviso da plataforma 16/11 16:17: Reembolso de 865.59 BRL iniciado")


def test_linha_da_conversa_falta_responder_com_prazo_e_assunto_comprido():
    c = _conversa(
        canal="chat",
        plataforma="shopee",
        conta=None,
        situacao="aberta",
        aguardando_resposta=True,
        prazo_resposta_em=datetime(2026, 11, 17, 15, 0, tzinfo=UTC),
        dados={"assunto": "x" * 300},
    )
    linha = claude_tarefas.linha_da_conversa(c, [], de_email=False)
    assert linha.startswith("chat Shopee [Caixa] · FALTA RESPONDER (prazo 17/11 12:00)")
    assert linha.endswith("x…") and len(linha) < 260


# ─────────────── C: a aberta sem notícia (16/11, revisão) ───────────────


def test_vence_em_conta_do_mais_novo():
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    linha = _linha_amazon(
        status="aberta",
        encerrada_em=None,
        aberta_em=t0,
        prazo_em=datetime(2026, 11, 16, 12, 0, tzinfo=UTC),
        dados={"email_em": t0.isoformat()},
    )
    # 30 dias depois do prazo (o mais novo).
    assert reivindicacao.vence_em(linha) == datetime(2026, 12, 16, 12, 0, tzinfo=UTC)
    # Um e-mail do caso depois do prazo (mesmo o que não disse a situação) adia.
    linha.dados = {"email_em": t0.isoformat(), "visto_em": "2026-11-20T09:00:00+00:00"}
    assert reivindicacao.vence_em(linha) == datetime(2026, 12, 20, 9, 0, tzinfo=UTC)
    # Sem prazo nem e-mail: da abertura.
    linha.prazo_em, linha.dados = None, {}
    assert reivindicacao.vence_em(linha) == t0 + reivindicacao.SEM_NOTICIA
    # Encerrada: não vence.
    assert reivindicacao.vence_em(_linha_amazon()) is None


def test_texto_da_sem_noticia():
    desde = datetime(2026, 11, 16, 16, 9, tzinfo=UTC)
    assert reivindicacao.status_texto_sem_noticia("atoz", desde) == (
        "Reivindicação sem notícia da Amazon desde 16/11/2026 — encerrada pelo DaVinci "
        "depois de 30 dias (confira no Seller Central)"
    )
    assert reivindicacao.status_texto_sem_noticia("chargeback", desde).startswith("Estorno")
