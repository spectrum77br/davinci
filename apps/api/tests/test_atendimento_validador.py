"""O validador do atendimento: o que não pode sair, e o que NÃO pode ser barrado.

Os dois lados importam. Deixar passar um WhatsApp suspende a loja na
Shopee; barrar à toa ("14h", o número do pedido, "preta por fora") faz a
pessoa reescrever e manda a IA para humano sem motivo. Cada lista abaixo é
um caso que alguém da equipe escreveria de verdade.

Função pura: sem banco (os testes não usam o `db`).
"""

from __future__ import annotations

import pytest

from app.services.atendimento.constantes import ORIGEM_HUMANO, ORIGEM_IA
from app.services.atendimento.validador import normalizar, so_da_ia, validar


def _v(texto: str, *, plataforma: str = "shopee", canal: str = "chat", origem: str = ORIGEM_HUMANO):
    return validar(texto, plataforma=plataforma, canal=canal, origem=origem)


def _tem(motivos: list[str], trecho: str) -> bool:
    return any(trecho in m for m in motivos)


# ─────────────── normalizar ───────────────


def test_normalizar_ml_troca_tipografia_e_tira_emoji():
    t = normalizar(
        "  Olá “cliente” — seu pedido… ‘ok’ 😊👍🏽\n\n\n\nObrigado  ",
        plataforma="ml",
        canal="pos_venda",
    )
    assert t == "Olá \"cliente\" - seu pedido... 'ok'\n\nObrigado"
    # Tudo cabe no ISO-8859-1 depois de normalizar.
    t.encode("latin-1")


def test_normalizar_ml_bandeira_e_tecla_somem_inteiras():
    t = normalizar("Brasil 🇧🇷 nº 1️⃣ ok", plataforma="ml", canal="pergunta")
    assert t == "Brasil nº 1 ok"


def test_normalizar_outras_plataformas_so_limpa_espacos():
    # Fora do ML a tipografia e o emoji ficam: a Shopee aceita.
    t = normalizar("  Oi  “você” 😊​  \r\n\r\n\r\n tchau ", plataforma="shopee", canal="chat")
    assert t == "Oi “você” 😊\n\ntchau"


def test_normalizar_compoe_acento():
    decomposto = "coracao".replace("a", "ã", 1)  # "ã" em dois pedaços
    t = normalizar(decomposto, plataforma="ml", canal="pos_venda")
    assert t == "corãcao"
    t.encode("latin-1")


def test_normalizar_vazio():
    assert normalizar("   \n  ", plataforma="ml", canal="pos_venda") == ""
    assert normalizar(None, plataforma="shopee", canal="chat") == ""  # type: ignore[arg-type]


# ─────────────── duro para todos ───────────────


def test_vazio_e_limite():
    assert _v("   ") == ["texto vazio"]
    assert _v("") == ["texto vazio"]
    motivos = _v("a" * 351, plataforma="ml", canal="pos_venda")
    assert motivos == ["passa do limite de 350 caracteres (351)"]
    assert _v("a" * 350, plataforma="ml", canal="pos_venda") == []
    # Limite conta o texto NORMALIZADO (espaço sobrando não conta).
    assert _v("  " + "a" * 350 + "   ", plataforma="ml", canal="pos_venda") == []
    assert _v("a" * 2000, plataforma="ml", canal="pergunta") == []
    assert _v("a" * 1001, plataforma="shopee", canal="chat") == [
        "passa do limite de 1000 caracteres (1001)"
    ]


@pytest.mark.parametrize(
    "texto",
    [
        "Me chama no whats",
        "me chama no Whats",
        "Nosso WhatsApp é o mesmo da loja",
        "Whatsapp: é só chamar",
        "chama no whats app",
        "chama no watsapp",
        "chama no zap",
        "manda no zapzap",
        "manda no zap zap",
        "me chama no wpp",
        "fala comigo no w h a t s",
        "wa.me/5511987654321",
        "Chama no ZAP",
        "whats​app",  # invisível no meio não esconde
    ],
)
def test_bloqueia_whatsapp(texto):
    assert "contato fora da loja (WhatsApp)" in _v(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "Ligue (11) 98765-4321",
        "11 98765-4321",
        "11987654321",
        "+55 11 98765 4321",
        "(11)98765-4321",
        "11 9 8765 4321",
        "(11) 3333-4444",
        "1133334444",
        "me liga 98765-4321",
        "fixo 3333-4444",
        "0800 123 4567",
        "tel: 3333 4444",
        "celular 9 8765 4321",
    ],
)
def test_bloqueia_telefone(texto):
    assert "contato fora da loja (telefone)" in _v(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "manda para fulano@gmail.com",
        "FULANO.SILVA+loja@empresa.com.br",
        "fulano arroba gmail ponto com",
        "me manda no gmail",
    ],
)
def test_bloqueia_email(texto):
    motivos = _v(texto)
    assert "contato fora da loja (e-mail)" in motivos
    # Um e-mail é UM motivo — não vira também "link" nem "@perfil".
    assert "link" not in motivos
    assert "contato fora da loja (@perfil)" not in motivos


@pytest.mark.parametrize(
    "texto", ["segue a gente @minhaloja", "@fulano", "procura @loja_oficial.br"]
)
def test_bloqueia_perfil(texto):
    assert "contato fora da loja (@perfil)" in _v(texto)


@pytest.mark.parametrize("texto", ["nos siga no Instagram", "estamos no insta", "fala no Telegram"])
def test_bloqueia_rede_social(texto):
    assert "contato fora da loja (rede social)" in _v(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "acesse www.loja.com.br",
        "https://minhaloja.com/produto",
        "http : // x.y",
        "bit.ly/abc123",
        "veja em minhaloja.com.br",
        "loja.shop tem mais",
        "tinyurl.com/xyz",
        "amzn.to/3abc",
    ],
)
def test_bloqueia_link(texto):
    assert "link" in _v(texto)


@pytest.mark.parametrize(
    "texto,motivo",
    [
        ("pode pagar no pix", "pagamento fora da plataforma (PIX)"),
        ("te passo a chave PIX", "pagamento fora da plataforma (PIX)"),
        ("me passa seus dados bancários", "pagamento fora da plataforma (conta bancária)"),
        ("faço transferência bancária", "pagamento fora da plataforma (conta bancária)"),
        ("se comprar por fora sai mais barato", "negociação fora da plataforma"),
        ("vendemos por fora também", "negociação fora da plataforma"),
        ("pagando por fora tem desconto", "negociação fora da plataforma"),
        ("dá pra fechar negócio por fora", "negociação fora da plataforma"),
        ("por fora sai mais barato", "negociação fora da plataforma"),
        ("podemos negociar fora da plataforma", "negociação fora da plataforma"),
        ("resolvemos fora do Mercado Livre", "negociação fora da plataforma"),
    ],
)
def test_bloqueia_pagamento_e_negocio_por_fora(texto, motivo):
    assert motivo in _v(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "Nos avalie com 5 estrelas!",
        "Por favor, avalie nossa loja",
        "Avalie o produto quando chegar",
        "avalie-nos",
        "Pode nos avaliar?",
        "Não esqueça de nos avaliar",
        "Deixe sua avaliação positiva",
        "deixe seu feedback",
        "faça sua avaliação",
        "dê uma nota pra gente",
        "Pode nos dar uma boa avaliação?",
        "Sua avaliação é muito importante para nós",
        "avaliação positiva e ganhe um cupom",
        "ganhe um brinde ao avaliar",
        "desconto na próxima compra se deixar 5 estrelas",
        "poderia mudar sua avaliação?",
        "pode retirar o comentário negativo?",
        "nota máxima pra gente",
        "cinco estrelas pra nós",
        "manda umas estrelinhas",
        "qualificação positiva ajuda muito",
    ],
)
def test_bloqueia_pedir_ou_condicionar_avaliacao(texto):
    assert "pede ou condiciona avaliação" in _v(texto), texto


@pytest.mark.parametrize(
    "texto",
    [
        "Não abra reclamação, resolvemos aqui",
        "não precisa reclamar",
        "Não reclame, vamos resolver",
        "Não é necessário abrir uma nova reclamação",
        "não precisa abrir chamado",
        "Não precisa abrir disputa",
        "não faz sentido abrir mediação",
        "pode cancelar a reclamação?",
        "retire a disputa por favor",
        "feche a mediação que a gente resolve",
        "antes de abrir disputa fale com a gente",
        "evite abrir reclamação",
        "não precisa ir ao Procon",
    ],
)
def test_bloqueia_desestimular_reclamacao(texto):
    assert "desestimula reclamação" in _v(texto), texto


def test_lacuna_literal_nao_sai():
    assert _v("Seu código é {rastreio}") == ["lacuna não preenchida ({rastreio})"]
    assert _v("pedido {{numero_pedido}}") == ["lacuna não preenchida ({{numero_pedido}})"]


def test_ml_fora_do_iso_8859_1():
    # Tipografia vira ASCII e emoji sai na normalização: passa.
    assert _v("Olá “cliente” — tudo… ok 😊", plataforma="ml", canal="pos_venda") == []
    # O que não tem equivalente não some em silêncio: a pessoa decide.
    motivos = _v("Preço ≈ combinado 你好", plataforma="ml", canal="pos_venda")
    assert motivos == ["caractere que o Mercado Livre não aceita (≈ 你 好)"]
    # Acento de português é ISO-8859-1.
    assert _v("Ação, coração, pé, ç, º, ª", plataforma="ml", canal="pergunta") == []


def test_amazon_sem_emoji():
    assert _v("Obrigado! 😊", plataforma="amazon", canal="email") == ["emoji (Amazon)"]
    assert _v("Obrigado! ⭐", plataforma="amazon", canal="email") == ["emoji (Amazon)"]
    assert _v("Obrigado!", plataforma="amazon", canal="email") == []
    # Na Shopee emoji pode.
    assert _v("Obrigado! 😊", plataforma="shopee", canal="chat") == []


def test_varios_motivos_sem_repetir():
    motivos = _v("chama no whats 11 98765-4321 ou no whats de novo, e avalie com 5 estrelas")
    assert motivos == [
        "contato fora da loja (WhatsApp)",
        "contato fora da loja (telefone)",
        "pede ou condiciona avaliação",
    ]


# ─────────────── falsos positivos óbvios: tem que passar ───────────────


@pytest.mark.parametrize(
    "texto,plataforma,canal",
    [
        # Horário não é prazo. ("sai no mesmo dia" É prazo: ver a lista da IA.)
        ("Atendemos das 8h às 18h; o suporte responde até as 14h aos sábados.", "shopee", "chat"),
        ("Seu pedido 2000012345678901 foi localizado.", "ml", "pos_venda"),
        ("Pedido 702-1234567-1234567 recebido.", "amazon", "email"),
        ("O pedido 576543210987654321 foi enviado.", "tiktok", "chat"),
        ("Seu pedido 250925ABCDEF12 está em separação.", "shopee", "chat"),
        ("Entregamos no CEP 01310-100.", "shopee", "chat"),
        ("CEP 01310100", "shopee", "chat"),
        ("Pode responder por e-mail, se preferir.", "amazon", "email"),
        ("Enviamos o e-mail com a nota.", "shopee", "chat"),
        ("A mala é preta por fora e cinza por dentro.", "shopee", "chat"),
        ("O fecho fica por fora.", "shopee", "chat"),
        ("Falta acabamento por fora?", "shopee", "chat"),
        ("O produto passará por avaliação técnica, depois o reembolso.", "ml", "pos_venda"),
        ("Depois de uma avaliação do produto, liberamos o reembolso.", "ml", "pos_venda"),
        ("Após a avaliação o reembolso será feito.", "shopee", "chat"),
        ("Para que o técnico avalie o produto, envie pelo app.", "shopee", "chat"),
        ("Mandamos a nota fiscal por aqui.", "shopee", "chat"),
        ("Deixei a nota fiscal dentro da caixa.", "shopee", "chat"),
        ("Obrigado pela nota!", "shopee", "chat"),
        ("Não precisa se preocupar, estamos cuidando.", "shopee", "chat"),
        ("Não recebemos nenhuma reclamação sobre isso.", "shopee", "chat"),
        ("A plataforma encerrou a disputa a seu favor.", "shopee", "chat"),
        ("Código de rastreio AA123456789BR.", "shopee", "chat"),
        ("Rastreio: 44123456789", "ml", "pos_venda"),
        ("Potência de 1000 watts.", "shopee", "chat"),
        ("O celular A15 tem 128gb e tela de 6.5 polegadas.", "shopee", "chat"),
        ("Fone de ouvido bluetooth 5.3", "shopee", "chat"),
        ("Instalação simples, vem com manual.", "shopee", "chat"),
        ("Chave de acesso 35250912345678000190550010000123451000012345", "shopee", "chat"),
        ("Anúncio MLB1234567890", "ml", "pergunta"),
        ("Sim, tem rodinhas 360° e cadeado TSA. Ótima compra!", "ml", "pergunta"),
        ("Ligue para o SAC do fabricante pelo manual.", "shopee", "chat"),
        ("Pague pelo Mercado Pago normalmente.", "ml", "pos_venda"),
        ("Oi! Chegou certinho? Qualquer coisa estamos aqui.", "shopee", "chat"),
        # As regras novas (25/09, revisão) não podem pegar o que é comum:
        ("Seu reembolso será depositado na sua conta pelo Mercado Pago.", "ml", "pos_venda"),
        ("O valor foi depositado na conta do Mercado Pago.", "ml", "pos_venda"),
        ("A face frontal da mala é de ABS.", "shopee", "chat"),
        ("Tenha um ótimo dia!", "shopee", "chat"),
        ("A mala tem garantia contra defeitos de fabricação.", "shopee", "chat"),
        ("Fale direto conosco por aqui.", "shopee", "chat"),
        ("Não é necessário fazer nada, a devolução já foi aprovada.", "shopee", "chat"),
        ("Vamos verificar com a transportadora.", "shopee", "chat"),
        ("O pedido será enviado assim que a transportadora coletar.", "shopee", "chat"),
    ],
)
def test_nao_bloqueia_falso_positivo(texto, plataforma, canal):
    # Nem para a IA, que tem as regras mais duras.
    assert validar(texto, plataforma=plataforma, canal=canal, origem=ORIGEM_IA) == []
    assert validar(texto, plataforma=plataforma, canal=canal, origem=ORIGEM_HUMANO) == []


# ─────────────── só IA ───────────────


@pytest.mark.parametrize(
    "texto,motivo",
    [
        ("custa R$ 59,90", "valor em R$ só pode vir do sistema"),
        ("custa R$59", "valor em R$ só pode vir do sistema"),
        ("fica 60 reais", "valor em R$ só pode vir do sistema"),
        ("fica 59,90 com frete", "valor em R$ só pode vir do sistema"),
        ("sai por 1.234,56", "valor em R$ só pode vir do sistema"),
        ("10% de desconto", "percentual só pode vir do sistema"),
        ("dez por cento a menos", "percentual só pode vir do sistema"),
        ("o frete é grátis", "frete grátis só pessoa pode prometer"),
        ("frete por nossa conta", "frete grátis só pessoa pode prometer"),
        ("envio grátis de frete", "frete grátis só pessoa pode prometer"),
        ("chega em 3 dias", "prazo em números só pode vir do sistema"),
        ("em até cinco dias úteis", "prazo em números só pode vir do sistema"),
        ("de 2 a 5 dias úteis", "prazo em números só pode vir do sistema"),
        ("até dia 10", "prazo em números só pode vir do sistema"),
        ("até o dia 10 chega", "prazo em números só pode vir do sistema"),
        ("em 48 horas", "prazo em números só pode vir do sistema"),
        ("em 48h", "prazo em números só pode vir do sistema"),
        ("chega dia 30/09", "prazo em números só pode vir do sistema"),
        ("será entregue no dia 30", "prazo em números só pode vir do sistema"),
        ("será entregue amanhã", "prazo em números só pode vir do sistema"),
        ("sai hoje", "prazo em números só pode vir do sistema"),
        ("garantia de 90 dias", "prazo de garantia só pode vir do sistema"),
        ("garantia de um ano", "prazo de garantia só pode vir do sistema"),
        ("me informe seu CPF", "pede ou repete dado pessoal (CPF, endereço, telefone)"),
        ("confirme o endereço de entrega", "pede ou repete dado pessoal (CPF, endereço, telefone)"),
        ("CPF 123.456.789-09 confere", "pede ou repete dado pessoal (CPF, endereço, telefone)"),
    ],
)
def test_so_ia_prazo_valor_e_dado(texto, motivo):
    motivos_ia = _v(texto, origem=ORIGEM_IA)
    assert motivo in motivos_ia, (texto, motivos_ia)
    assert so_da_ia(motivos_ia)
    # A pessoa pode: ela sabe o que promete.
    assert _v(texto, origem=ORIGEM_HUMANO) == []


def test_lacunas_preenchidas_pelo_codigo_passam_para_a_ia():
    """O que o CÓDIGO preenche (data, rastreio, pedido, NF) não pode reprovar.

    O envio automático valida o texto FINAL com origem davinci_ia — se uma
    data preenchida pela lacuna reprovasse, nada sairia sozinho nunca.
    """
    final = (
        "Olá! Seu pedido 2000012345678901 foi enviado em 24/09/2026 pela SEDEX. "
        "O código de rastreio é AA123456789BR e a previsão de entrega da "
        "transportadora é 30/09/2026. A nota fiscal é a 123456."
    )
    assert _v(final, plataforma="ml", canal="pergunta", origem=ORIGEM_IA) == []
    assert _v("Seu pedido foi enviado no dia 24/09/2026.", origem=ORIGEM_IA) == []


def test_so_da_ia_distingue_regra_dura():
    assert not so_da_ia(
        ["contato fora da loja (WhatsApp)", "passa do limite de 350 caracteres (400)"]
    )
    assert so_da_ia(["link", "prazo em números só pode vir do sistema"])
    assert not so_da_ia([])


# ─────────────── buracos da revisão de 25/09 (SEG-4 e SEG-5) ───────────────


@pytest.mark.parametrize(
    "texto,motivo",
    [
        # Preço no FIM da frase (o ponto final não pode salvar o número).
        ("Esse modelo custa 199,90.", "valor em R$ só pode vir do sistema"),
        ("sai 1.299,90.", "valor em R$ só pode vir do sistema"),
        ("sai por 59.90", "valor em R$ só pode vir do sistema"),
        ("cinquenta reais", "valor em R$ só pode vir do sistema"),
        ("cem reais", "valor em R$ só pode vir do sistema"),
        # Prazo por extenso ou relativo.
        ("normalmente leva de três a cinco dias úteis", "prazo em números só pode vir do sistema"),
        ("O prazo médio é de uma semana", "prazo em números só pode vir do sistema"),
        ("Vai chegar semana que vem", "prazo em números só pode vir do sistema"),
        ("Chega na próxima segunda-feira", "prazo em números só pode vir do sistema"),
        ("Despachamos no mesmo dia", "prazo em números só pode vir do sistema"),
        ("pedido feito até as 14h sai no mesmo dia", "prazo em números só pode vir do sistema"),
        ("em 48hs", "prazo em números só pode vir do sistema"),
        # Frete e gratuidade.
        ("O envio é gratuito", "frete grátis só pessoa pode prometer"),
        ("nós pagamos o frete", "frete grátis só pessoa pode prometer"),
        ("o frete é por conta da loja", "frete grátis só pessoa pode prometer"),
        ("A troca é sem custo", "promessa (reembolso, troca, sem custo) só pessoa pode fazer"),
        # Garantia em qualquer ordem.
        ("A garantia é de 1 ano", "prazo de garantia só pode vir do sistema"),
        ("garantia de fábrica de doze meses", "prazo de garantia só pode vir do sistema"),
        ("garantia vitalícia", "prazo de garantia só pode vir do sistema"),
        # Documento sem possessivo.
        ("nos informe o CPF", "pede ou repete dado pessoal (CPF, endereço, telefone)"),
        ("manda o cpf", "pede ou repete dado pessoal (CPF, endereço, telefone)"),
        # Promessa.
        (
            "reembolsaremos o valor total",
            "promessa (reembolso, troca, sem custo) só pessoa pode fazer",
        ),
        ("pode ficar com o produto", "promessa (reembolso, troca, sem custo) só pessoa pode fazer"),
        (
            "Pode ficar com o produto, vamos estornar o valor integral.",
            "promessa (reembolso, troca, sem custo) só pessoa pode fazer",
        ),
        (
            "Confirmamos o cancelamento sem custo.",
            "promessa (reembolso, troca, sem custo) só pessoa pode fazer",
        ),
    ],
)
def test_so_ia_buracos_da_revisao(texto, motivo):
    motivos_ia = _v(texto, origem=ORIGEM_IA)
    assert motivo in motivos_ia, (texto, motivos_ia)
    # Bloqueia a sugestão (a pessoa não a mandaria num clique).
    assert so_da_ia(motivos_ia)


def test_pergunta_publica_do_ml_nao_publica_preco():
    assert "valor em R$ só pode vir do sistema" in _v(
        "Custa 199,90.", plataforma="ml", canal="pergunta", origem=ORIGEM_IA
    )


@pytest.mark.parametrize(
    "texto,motivo",
    [
        # Avaliação no tom educado de um modelo na categoria "agradecimento".
        (
            "Obrigado pela compra! Se gostou, que tal avaliar o produto?",
            "pede ou condiciona avaliação",
        ),
        ("Ficaremos muito felizes se você avaliar o produto!", "pede ou condiciona avaliação"),
        ("Esperamos que goste! Sua avaliação nos ajuda muito.", "pede ou condiciona avaliação"),
        ("Poderia deixar sua opinião no produto?", "pede ou condiciona avaliação"),
        ("5⭐", "pede ou condiciona avaliação"),
        ("⭐⭐⭐⭐⭐", "pede ou condiciona avaliação"),
        ("não deixe avaliação negativa", "pede ou condiciona avaliação"),
        ("evite dar nota baixa", "pede ou condiciona avaliação"),
        ("não avalie mal", "pede ou condiciona avaliação"),
        # Desestimular reclamação, devolução e reembolso pela plataforma.
        ("Nem precisa reclamar", "desestimula reclamação"),
        ("Não vale a pena abrir disputa", "desestimula reclamação"),
        ("Não abra devolução", "desestimula reclamação"),
        ("Não solicite reembolso pela plataforma, a gente resolve aqui", "desestimula reclamação"),
        ("desista da reclamação", "desestimula reclamação"),
        # Contato fora da loja, escrito para escapar.
        ("Me chama no zapp", "contato fora da loja (WhatsApp)"),
        ("uatizap", "contato fora da loja (WhatsApp)"),
        ("z@p", "contato fora da loja (WhatsApp)"),
        ("whаtsapp", "contato fora da loja (WhatsApp)"),  # "а" cirílico
        ("chama no face", "contato fora da loja (rede social)"),
        ("nosso ig é lojaxyz", "contato fora da loja (rede social)"),
        ("fala no discord", "contato fora da loja (rede social)"),
        ("fulano@uol", "contato fora da loja (e-mail)"),
        ("fulano [arroba] uol [ponto] com", "contato fora da loja (e-mail)"),
        ("nossaloja ponto com ponto br", "link"),
        ("nossaloja . com . br", "link"),
        # Pagamento por fora.
        ("transfere pra minha conta", "pagamento fora da plataforma (conta bancária)"),
        ("deposita na conta", "pagamento fora da plataforma (conta bancária)"),
        ("faz um ted", "pagamento fora da plataforma (conta bancária)"),
        ("compra direto comigo que sai mais barato", "negociação fora da plataforma"),
        ("boleto por fora", "negociação fora da plataforma"),
    ],
)
def test_regras_duras_buracos_da_revisao(texto, motivo):
    # Duro para todos: a pessoa E a IA.
    assert motivo in _v(texto, origem=ORIGEM_HUMANO), texto
    assert motivo in _v(texto, origem=ORIGEM_IA), texto
