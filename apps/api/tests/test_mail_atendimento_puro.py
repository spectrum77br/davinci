"""A camada do atendimento sobre a Central de e-mail — as regras PURAS (sem banco).

Reaproveitado do lado DaVinci do e-mail do Tuta (wt-tuta, `test_atendimento_tuta_puro`),
sem o que a Central do outro dev já faz (HTML, .eml, contrato do robô), e com o
que entrou agora: o filtro "só aliases de loja", o "sac@marca" completado pelo
domínio da marca, a pasta do agente v1 (IMAP) pelo nome, as pastas que só se
contam, o e-mail de SEGURANÇA e o link de acesso removido.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.mail import MailMessage
from app.models.mail_atendimento import MailFolder
from app.services.mail_atendimento import (
    codigos,
    enderecos,
    pastas,
    pedido,
    ponte,
    regras,
    responder,
    rotear,
    suspeito,
    texto,
)

R = regras.regras_padrao()

# As 40 pastas reais da conta geral (conferidas ao vivo em 08/10/2026), com o tipo do Tuta.
PASTAS_REAIS = {
    ("Entrada", "1"): ("entrada", None, True),
    ("Enviados", "2"): ("enviados", None, True),
    ("Lixeira", "3"): (None, None, False),
    ("Arquivo", "4"): (None, None, False),
    ("Spam", "5"): (None, None, False),
    ("Rascunhos", "6"): (None, None, False),
    ("vendas ml", "0"): ("vendas", "ml", True),
    ("mensagens ml", "0"): ("mensagens", "ml", True),
    ("problema ml", "0"): ("problema", "ml", True),
    ("reclamação ml", "0"): ("reclamacao", "ml", True),
    ("vendas shopee", "0"): ("vendas", "shopee", True),
    ("mensagens shopee", "0"): ("mensagens", "shopee", True),
    ("problema shopee", "0"): ("problema", "shopee", True),
    ("vendas amazon", "0"): ("vendas", "amazon", True),
    ("mensagens amazon", "0"): ("mensagens", "amazon", True),
    ("problema amazon", "0"): ("problema", "amazon", True),
    ("vendas tiktok", "0"): ("vendas", "tiktok", True),
    ("problema tiktok", "0"): ("problema", "tiktok", True),
    ("vendas temu", "0"): ("vendas", "temu", True),
    ("problema temu", "0"): ("problema", "temu", True),
    ("vendas magalu", "0"): ("vendas", "magalu", True),
    ("vendas ali", "0"): ("vendas", "aliexpress", True),
    # Sem plataforma: só contar (decisão padrão de 08/10).
    ("envio erro", "0"): (None, None, False),
    ("financeiro", "0"): (None, None, False),
    ("retido", "0"): (None, None, False),
    ("anuncio problema", "0"): (None, None, False),
    ("atacado", "0"): (None, None, False),
    ("contabilidade", "0"): (None, None, False),
    ("devoluçoes", "0"): (None, None, False),
    ("DNP", "0"): (None, None, False),
    ("envio", "0"): (None, None, False),
    ("*avisos", "0"): (None, None, False),
    # Os sites (RF6): "*" + marca.
    ("*uranyx", "0"): (None, "site", True),
    ("*uranyx sac", "0"): (None, "site", True),
    ("*uranyx atacado", "0"): (None, "site", True),
    ("*uranyx duvidas", "0"): (None, "site", True),
    ("*charlots", "0"): (None, "site", True),
    ("*7buyers", "0"): (None, "site", True),
    ("*poofy", "0"): (None, "site", True),
    ("*makisa", "0"): (None, "site", True),
}


# ─────────────── a regra das pastas (RF5) ───────────────


@pytest.mark.parametrize(("pasta", "esperado"), list(PASTAS_REAIS.items()))
def test_as_40_pastas_reais(pasta, esperado):
    nome, tipo = pasta
    c = regras.classificar(nome, tipo, R)
    assert (c.finalidade, c.plataforma, c.ler) == esperado, nome


def test_pasta_so_contar_mesmo_com_plataforma_no_nome():
    # "devoluções ml" tem a palavra da plataforma, mas é só contar (decisão padrão).
    c = regras.classificar("devoluções ml", "0", R)
    assert c.plataforma == "ml" and c.ler is False
    assert regras.classificar("financeiro shopee", "0", R).ler is False


def test_palavra_inteira_e_ultima_palavra():
    assert regras.classificar("vendas magalu", "0", R).plataforma == "magalu"
    assert regras.classificar("ml problema", "0", R).plataforma is None
    assert regras.classificar("RECLAMAÇÃO ML", "0", R).finalidade == "reclamacao"


def test_caixa_do_site_pela_palavra_da_pasta():
    c = regras.classificar("*uranyx sac", "0", R)
    assert (c.marca, c.tipo_caixa) == ("uranyx", "sac")
    assert regras.classificar("*uranyx duvidas", "0", R).tipo_caixa == "duvidas"
    assert regras.classificar("*charlots", "0", R).marca == "charlots-park"


def test_regra_nova_sem_mexer_no_codigo():
    r = regras._montar([*regras.SEMENTE, ("plataforma", "casas", "magalu")])
    assert regras.classificar("vendas casas", "0", r).plataforma == "magalu"


def test_escolha_de_pessoa_ganha_da_regra_e_o_ler_gravado():
    p = MailFolder(chave="x", nome="devolução temu x", tipo_tuta="0")
    p.ignorar = False
    p.plataforma_manual = None
    p.finalidade_manual = None
    assert regras.efetiva(p, R).ler is False
    assert regras.ler_da_pasta(p, regras.efetiva(p, R)) == "so_contar"
    p.plataforma_manual = "temu"
    p.finalidade_manual = "problema"
    c = regras.efetiva(p, R)
    assert (c.plataforma, c.finalidade, c.ler) == ("temu", "problema", True)
    assert regras.ler_da_pasta(p, c) == "corpo"
    p.ignorar = True
    assert regras.efetiva(p, R).ler is False
    assert regras.ler_da_pasta(p, regras.efetiva(p, R)) == "nao"


@pytest.mark.parametrize(
    ("caminho", "kind"),
    [
        ("INBOX", "1"),
        ("Sent", "2"),
        ("INBOX/Sent Items", "2"),
        ("Trash", "3"),
        ("Junk", "5"),
        ("Drafts", "6"),
        ("Entrada", "1"),
        ("INBOX/vendas ml", "0"),
    ],
)
def test_pasta_do_agente_v1_pelo_nome(caminho, kind):
    lida = pastas.do_conteudo({"folder": caminho})
    assert lida.tipo_tuta == kind
    assert lida.chave == caminho


def test_pasta_do_conector_v2_pela_chave_e_tipo():
    lida = pastas.do_conteudo(
        {
            "folder": "problema ml",
            "tuta": {"folder_key": "L1/E9", "folder_kind": "0", "folder_path": "lojas/problema ml"},
        }
    )
    assert (lida.chave, lida.nome, lida.tipo_tuta) == ("L1/E9", "problema ml", "0")


# ─────────────── endereços ───────────────


def test_normalizar_e_cadastro_sem_dominio():
    assert enderecos.normalizar(" <Fulano@Gmail.COM> ") == "fulano@gmail.com"
    assert enderecos.normalizar("mailto:a@b.com") == "a@b.com"
    assert enderecos.normalizar("sem arroba") == ""
    assert enderecos.endereco_do_cadastro("21max") == "21max@tuta.com"
    assert enderecos.endereco_do_cadastro("16TR@tuta.com") == "16tr@tuta.com"
    assert enderecos.endereco_do_cadastro("") == ""


def test_cadastro_sac_marca_e_parcial_e_casa_com_o_endereco_da_caixa():
    # Em produção a marca "poofy" tem o domínio charlots.com.br: o cadastro
    # "sac@poofy" casa com o endereço da caixa (sac@poofy.com.br), não com a marca.
    assert enderecos.endereco_do_cadastro("sac@poofy") == ""
    parcial = enderecos.parcial_do_cadastro("sac@poofy")
    assert parcial == ("sac", "poofy")
    assert enderecos.casa_parcial("sac@poofy.com.br", parcial)
    assert not enderecos.casa_parcial("adm@poofy.com.br", parcial)
    assert not enderecos.casa_parcial("sac@charlots.com.br", parcial)
    assert enderecos.parcial_do_cadastro("16tr") is None
    assert enderecos.parcial_do_cadastro("sac@") is None
    assert enderecos.parcial_do_cadastro("lixo com espaço@x") is None


def test_c4_cadastro_sem_dominio_ambiguo_com_outro_dominio_do_tuta():
    assert enderecos.ambiguo_no_tuta("16tr@tuta.com", {"16tr@tuta.com", "16tr@tutamail.com"})
    assert not enderecos.ambiguo_no_tuta("16tr@tuta.com", {"16tr@tuta.com", "16tr@gmail.com"})


def test_aviso_e_nao_responde():
    assert enderecos.e_aviso("nao-responder@mercadolivre.com")
    assert enderecos.e_aviso("MAILER-DAEMON@mx.tuta.com")
    assert enderecos.e_aviso("qualquer@shopee.com.br")
    assert not enderecos.e_aviso("maria@gmail.com")
    assert enderecos.nao_responde("no-reply@x.com")
    assert enderecos.nao_responde("naoresponda@loja.com.br")
    assert not enderecos.nao_responde("sac@uranyx.com.br")


def test_e5_email_do_cliente_no_formulario():
    nossos = {"sac@uranyx.com.br"}
    corpo = "Nome: Maria\nE-mail: Maria.Silva@Gmail.com\nTelefone: 11999\nMensagem: oi"
    assert enderecos.email_do_formulario(corpo, nossos) == "maria.silva@gmail.com"
    assert enderecos.email_do_formulario("enviado por sac@uranyx.com.br", nossos) == ""
    assert enderecos.email_do_formulario("de joao@x.com.br para sac@uranyx.com.br", nossos) == (
        "joao@x.com.br"
    )


# ─────────────── remetente falso (D5) ───────────────


def _av(**kw):
    base = {
        "de_endereco": "nao-responder@mercadolivre.com.br",
        "de_nome": "Mercado Livre",
        "reply_to": [],
        "auth_status": "0",
        "phishing_status": "0",
        "envelope_diferente": None,
        "cabecalhos": None,
        "plataforma_pasta": "ml",
    }
    base.update(kw)
    return suspeito.avaliar(**base)


def test_d5_oficial_nao_e_suspeito():
    assert not _av().suspeito
    assert not _av(envelope_diferente="bounce@amazonses.com").suspeito
    # Na caixa v1 o Tuta não manda o status: sem ele, nada acende sozinho.
    assert not _av(auth_status=None, phishing_status=None).suspeito


def test_d5_alerta_do_proprio_tuta():
    r = _av(auth_status="1")
    assert r.suspeito and suspeito.M_AUTH in r.motivos
    r = _av(phishing_status="1", envelope_diferente="x@y.com")
    assert suspeito.M_PHISHING in r.motivos and suspeito.M_ENVELOPE in r.motivos


@pytest.mark.parametrize(
    "dominio", ["rnercadolivre.com", "mercadolivre-br.com", "mercadolivrebr.net", "mercadolivr.com"]
)
def test_d5_dominio_parecido(dominio):
    r = _av(de_endereco=f"suporte@{dominio}")
    assert r.suspeito and suspeito.M_DOMINIO in r.motivos


def test_d5_nome_da_plataforma_com_dominio_de_fora_e_reply_to():
    r = _av(de_endereco="atendimento@golpe.com", de_nome="Mercado Livre Suporte")
    assert r.suspeito and suspeito.M_NOME in r.motivos
    assert suspeito.M_REPLY_TO in _av(reply_to=["golpe@outro.com"]).motivos
    assert not _av(de_endereco="maria@gmail.com", de_nome="Maria").suspeito


# ─────────────── segurança: código, senha e link de acesso ───────────────


@pytest.mark.parametrize(
    ("assunto", "corpo"),
    [
        ("Seu código de verificação Shopee", "Use 482913 para entrar."),
        ("Redefinir sua senha", "Clique no link para criar uma nova senha."),
        ("Novo acesso à sua conta", "Detectamos um novo login."),
        ("Confirme seu e-mail", "Confirme seu e-mail para ativar."),
        ("Your verification code", "123 456"),
        ("Aviso", "Seu código: 482913 — use para fazer login."),
        ("Magic link", "Entre com um clique."),
    ],
)
def test_email_de_seguranca_nunca_vira_conversa(assunto, corpo):
    assert codigos.e_de_seguranca(assunto, corpo)


@pytest.mark.parametrize(
    ("assunto", "corpo"),
    [
        ("Pedido enviado", "rastreio AA123456789BR"),
        (
            "Seu código de rastreio",
            "O código de rastreio do pedido 2000012345678901 é NX1234567BR.",
        ),
        ("Nova mensagem do comprador", "Quando chega meu pedido?"),
        ("Confirmação do pedido", "Pedido confirmado: 2000012345678901."),
    ],
)
def test_email_de_loja_nao_e_de_seguranca(assunto, corpo):
    assert not codigos.e_de_seguranca(assunto, corpo)


def test_cliente_pedindo_ajuda_com_senha_e_atendimento():
    # O comprador (ou o formulário do site) escrevendo sobre senha é atendimento.
    pedido_de_ajuda = ("Ajuda", "Esqueci minha senha do app, como redefinir sua senha?")
    assert codigos.e_de_seguranca(*pedido_de_ajuda)  # da plataforma/serviço: esconde
    assert not codigos.e_de_seguranca(*pedido_de_ajuda, de_pessoa=True)
    # O código de acesso de verdade esconde até vindo de pessoa.
    assert codigos.e_de_seguranca(
        "Fwd: código de verificação", "Seu código de verificação: 482913", de_pessoa=True
    )


def test_d8_codigo_mascarado():
    assert codigos.fala_de_codigo("Seu código de verificação Shopee", "")
    assert codigos.mascarar("Código: 482913.") == "Código: ••••••."
    assert codigos.mascarar("Seu OTP é 123 456") == "Seu OTP é ••• •••"
    assert codigos.mascarar("código A1B2C3") == "código ••••••"
    assert codigos.mascarar("pedido 2000012345678901") == "pedido 2000012345678901"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.mercadolivre.com.br/password/reset?x=1",
        "https://conta.shopee.com.br/login",
        "https://x.com/confirmar-email",
        "https://x.com/a?token=abc",
        "https://x.com/a?t=1",
        "https://x.com/v/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "https://x.com/redefinir-senha",
        "www.loja.com/auth/callback",
    ],
)
def test_link_de_acesso_some(url):
    texto_ok, n = codigos.mascarar_links(f"Clique: {url}.")
    assert n == 1 and url not in texto_ok and codigos.LINK_REMOVIDO in texto_ok
    assert texto_ok.endswith(".")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.mercadolivre.com.br/vendas/2000012345678901/detalhe",
        "https://loja.com/acessorios/capa-iphone",
        "https://rastreio.correios.com.br/app/index.php",
    ],
)
def test_link_comum_fica(url):
    texto_ok, n = codigos.mascarar_links(f"Veja {url}")
    assert n == 0 and url in texto_ok


def test_proteger_junta_links_e_codigos():
    p = codigos.proteger("Seu código 482913", "Código: 482913\nhttps://x.com/login?token=abc\nfim")
    assert "482913" not in p.assunto and "482913" not in p.texto
    assert "token=abc" not in p.texto and p.links_removidos == 1 and p.codigo_mascarado
    sem = codigos.proteger("Pedido", "Quando chega o pedido 2000012345678901?")
    assert sem.texto == "Quando chega o pedido 2000012345678901?" and not sem.codigo_mascarado


def test_lacunas_da_mascara_da_critica_de_08_10():
    # Senha em claro: o valor some (com ":"/"=" sempre; com "é"/"is" se tem cara de senha).
    p = codigos.proteger("Acesso", "Sua nova senha de acesso é: Ab12cd34")
    assert "Ab12cd34" not in p.texto and p.codigo_mascarado
    assert codigos.mascarar_senha("senha=abc12345 e Password: hunter2")[0] == (
        "senha=•••••••• e Password: •••••••"
    )
    assert codigos.mascarar_senha("Your password is Xy7!abc.")[0] == "Your password is •••••••."
    assert codigos.mascarar_senha("A senha é importante")[1] == 0
    # Link curto de reset: no e-mail que fala de senha/acesso, TODOS os links saem.
    p = codigos.proteger("Redefinição de senha", "Clique: https://www.bling.com.br/r/AbCdEf")
    assert "bling.com.br" not in p.texto and p.links_removidos == 1
    assert codigos.fala_de_acesso("", "Nuevo inicio de sesión en tu cuenta")
    assert not codigos.fala_de_acesso("Pedido", "Quando chega o meu pedido?")
    # Código com hífen e em espanhol.
    assert codigos.proteger("x", "G-482913 é o seu código de verificação").texto.startswith(
        "G-•••••• "
    )
    assert "482913" not in codigos.proteger("x", "Tu código de verificación es 482913").texto
    assert codigos.mascarar("PO-211-12345678 e US-26-0014") == "PO-211-12345678 e US-26-0014"
    assert codigos.e_de_seguranca("x", "Tu código de verificación es 482913", de_pessoa=True)
    # O código com prefixo que o Mac já mascarou ainda é de segurança.
    assert codigos.e_de_seguranca("x", "Seu código de verificação: G-••••••", de_pessoa=True)
    # Os três casos que viravam conversa: estritos, são de segurança.
    for assunto, corpo in (
        ("Redefinição de senha", "Clique para redefinir sua senha: https://x.example/r/Ab"),
        ("Acesso", "Sua nova senha de acesso é: Ab12cd34"),
        ("Aviso", "Detectamos um novo login na sua conta."),
    ):
        assert codigos.e_de_seguranca(assunto, corpo), assunto


# ─────── conferência pré-subida de 08/10: sem depender da frase exata ───────

# Os 7 do cético (para 21max, login da Barbosa): o código ou a senha com frase
# fora da lista viravam conversa. Na regra estrita, todos são de segurança.
SEGURANCA_SEM_FRASE = [
    ("Acesso à conta", "Use o código 482913 para entrar na sua conta."),
    ("Token", "Seu token de acesso: 482913"),
    ("Dados de acesso", "Usuário: barbosa\nSenha gerada: Kx81mq2z"),
    ("482913 is your Facebook confirmation code", "Enter this code: 482913"),
    ("Sign in", "Your sign-in code is 482913"),
    ("Código de confirmación", "El código de confirmación es 482913"),
    ("Tu cuenta", "Tu contraseña temporal es: Kx81mq2z"),
]


@pytest.mark.parametrize(("assunto", "corpo"), SEGURANCA_SEM_FRASE)
def test_codigo_ou_senha_com_frase_fora_da_lista_e_de_seguranca(assunto, corpo):
    assert codigos.e_de_seguranca(assunto, corpo)
    # E nada do segredo sobra no que a equipe veria (se algum dia passasse).
    p = codigos.proteger(assunto, corpo)
    assert "482913" not in p.assunto + p.texto and "Kx81mq2z" not in p.texto


@pytest.mark.parametrize(
    ("assunto", "corpo"),
    [
        # Qualquer código solto + palavra de acesso, em qualquer lugar do e-mail.
        ("Shopee", "Código Shopee: 482913. Não compartilhe."),
        ("Confirme sua identidade", "Digite o código 482913 no aplicativo."),
        ("Your Instagram code", "123456 is your Instagram code. Don't share it."),
        ("PIN", "Your PIN is 4829"),
        ("WhatsApp", "Your WhatsApp code: 123-456"),
        ("Acceso", "Ingresa el código 482913 para iniciar sesión."),
        ("Log in", "Enter 482913 to log in."),
        ("Code", "Your code is 4 8 2 9 1 3"),
        ("Seu código de verificação", "Código: k7x9q2"),
        # O código que o conector já mascarou ainda conta.
        ("Sign in", "Your sign-in code is ••••••"),
        ("Token", "Seu token de acesso: ••••••"),
        ("Dados", "Senha gerada: ••••••••"),
    ],
)
def test_codigo_solto_com_palavra_de_acesso_e_de_seguranca(assunto, corpo):
    assert codigos.e_de_seguranca(assunto, corpo)


def test_de_pessoa_so_o_codigo_de_acesso_de_verdade():
    # O cliente (formulário, caixa de site): o gatilho de CÓDIGO DE ACESSO e o código juntos.
    assert codigos.e_de_seguranca("Ajuda", "Use o código 482913 para entrar", de_pessoa=True)
    assert codigos.e_de_seguranca("Fwd", "Your sign-in code is 482913", de_pessoa=True)
    assert codigos.e_de_seguranca("Fwd", "Seu token de acesso: k7x9q2", de_pessoa=True)
    assert not codigos.e_de_seguranca(
        "Ajuda", "O código do produto não aparece, pedido 2000012345678901", de_pessoa=True
    )
    assert not codigos.e_de_seguranca("Ajuda", "Esqueci minha senha do app", de_pessoa=True)


# Os 14 textos e os 3 links curtos do cético: a senha, o código e o link somem
# (os dois lados, o Python e o Rust — ver `test_casos_iguais_ao_conector`).
MASCARA_PRE_SUBIDA = [
    ("Dados de acesso", "Senha gerada: Kx81mq2z", "Senha gerada: ••••••••"),
    ("Painel", "Senha do painel: Kx81mq2z", "Senha do painel: ••••••••"),
    ("Painel", "Senha - Kx81mq2z", "Senha - ••••••••"),
    (
        "Bem-vindo",
        "Seu login é barbosa e sua senha Kx81mq2z",
        "Seu login é barbosa e sua senha ••••••••",
    ),
    ("Dados", "Login: barbosa Senha: xyz", "Login: barbosa Senha: •••"),
    ("Painel", "Sua senha provisória Kx81mq2z", "Sua senha provisória ••••••••"),
    ("Tu cuenta", "Tu contraseña temporal es: Kx81mq2z", "Tu contraseña temporal es: ••••••••"),
    ("Tu cuenta", "Tu clave temporal es Kx81mq2z", "Tu clave temporal es ••••••••"),
    ("Account", "Temporary password - Kx81mq2z", "Temporary password - ••••••••"),
    ("Account", "Password for login: Kx81mq2z", "Password for login: ••••••••"),
    ("Seu código de verificação", "Código: k7x9q2", "Código: ••••••"),
    ("Seu código de verificação", "Seu código: 48 29 13", "Seu código: •• •• ••"),
    ("WhatsApp", "Your WhatsApp code: 123-456", "Your WhatsApp code: •••-•••"),
    (
        "Acceso",
        "Ingresa el código 482913 para iniciar sesión",
        "Ingresa el código •••••• para iniciar sesión",
    ),
]


@pytest.mark.parametrize(("assunto", "corpo", "esperado"), MASCARA_PRE_SUBIDA)
def test_mascara_dos_casos_do_cetico(assunto, corpo, esperado):
    p = codigos.proteger(assunto, corpo)
    assert p.texto == esperado
    assert p.codigo_mascarado


@pytest.mark.parametrize(
    "corpo",
    [
        "Clique para entrar: https://bit.ly/3xYz",
        "Acesse sua conta: https://lnk.to/aB3",
        "Reset here: https://t.co/AbCd",
    ],
)
def test_link_curto_de_entrar_some(corpo):
    p = codigos.proteger("Conta", corpo)
    assert "http" not in p.texto and p.links_removidos == 1
    assert p.texto.endswith(codigos.LINK_REMOVIDO)


@pytest.mark.parametrize(
    ("assunto", "corpo"),
    [
        (
            "Pedido não chegou",
            "Meu pedido 2000012345678901 ainda não chegou. Código de rastreio: AA123456789BR. "
            "CEP 01310-100. Paguei R$ 1.299,00 em 05/10/2026.",
        ),
        (
            "Troca",
            "Comprei dia 05/10/2026 o celular de 5000 mAh por 1500 reais, pedido "
            "241008ABCD1234, quero trocar. Meu CPF 123.456.789-09. Telefone (11) 3456-7890",
        ),
        ("Nota", "Segue a NF 52341 do pedido 2000012345678901, valor R$ 899,90"),
        ("Dúvida", "Qual o código do produto? Vi o anúncio MLB1234567890, vou entrar em contato"),
        ("Rastreio", "O código de rastreio NX1234567BR não atualiza desde 2026"),
        ("Amazon", "Order 701-1234567-1234567 not delivered, tracking code 1Z999AA10123456784"),
        ("Pedido", "Esqueci a senha do app, meu pedido 2000012345678901 chega quando?"),
    ],
)
def test_texto_normal_do_cliente_nao_e_mascarado_nem_seguranca(assunto, corpo):
    """Controle: nº do pedido (ML, Shopee, Amazon), rastreio, CEP, CPF, valor,
    data, telefone e unidade ficam — nem viram "segurança" na regra estrita."""
    p = codigos.proteger(assunto, corpo)
    assert p.texto == corpo and p.links_removidos == 0
    assert not codigos.e_de_seguranca(assunto, corpo)
    if "senha" not in corpo.lower():  # falar de senha já marca "pode ter código"
        assert not p.codigo_mascarado


def test_no_email_de_codigo_so_o_codigo_vira_bolinha():
    texto_ = (
        "Seu código: 482913. Pedido 2000012345678901, rastreio AA123456789BR, CEP 01310-100, "
        "CEP 01310100, valor R$ 150,00, R$ 1500, 1500,00, CPF 123.456.789-09, data 08/10/2026, "
        "telefone (11) 3456-7890, 11 98765-4321, +55 11 98765 4321, Shopee 241008ABCD1234, "
        "Amazon 701-1234567-1234567, chave 3524 1012 3456 7800 0123 5500 1000 0012 3410 0012 "
        "3456, 5000 mAh, 1500 reais, pedido #4521, 15%, ano 2026"
    )
    assert codigos.mascarar(texto_) == texto_.replace("482913", "••••••", 1)


def test_codigo_fraco_so_perto_da_palavra_ou_no_email_de_codigo_de_acesso():
    # O minúsculo e o "ano": só perto da palavra de acesso.
    assert (
        codigos.mascarar("Código: k7x9q2 e o modelo x1234y") == "Código: •••••• e o modelo x1234y"
    )
    assert (
        codigos.mascarar("Your PIN is 2019. We have been selling phones here since 2019.")
        == "Your PIN is ••••. We have been selling phones here since 2019."
    )
    # O e-mail de código de acesso (pelo assunto): até o fraco sozinho some.
    p = codigos.proteger("Your verification code", "k7x9q2")
    assert p.texto == "••••••"


# ─────── conferência pré-subida 2 de 08/10 (o cético 2: e-mails novos) ───────

# O código ÚNICO sem nenhuma palavra da lista ("número", "chave", "Enter"…):
# P02, P06, P16, P18, E08 e S08 do cético 2 (para 21max, login da Barbosa).
CODIGO_UNICO = [
    (
        "Confirme que é você",
        "Olá, Barbosa!\n\nPara continuar, digite este número no Mercado Livre:\n\n731 604\n\n"
        "Ele vence em 30 minutos. Não compartilhe com ninguém.",
        "731 604",
    ),
    (
        "Itaú: habilitação de aparelho",
        "Para habilitar o iToken no seu novo aparelho, informe a chave 804 117 no app Itaú.\n\n"
        "Se não foi você, ligue 4004 4828.",
        "804 117",
    ),
    (
        "Liberação do novo celular",
        "Sua chave de segurança para liberar o novo celular é 7 3 1 8 2 0.\n\n"
        "Não informe a ninguém.",
        "7 3 1 8 2 0",
    ),
    ("Confirme sua identidade", "Para concluir, digite o número 615 029 no app.", "615 029"),
    (
        "Confirm it's you",
        "Enter 604 381 on the Wise website to approve this request. It expires in 10 minutes.",
        "604 381",
    ),
    (
        "Confirmá tu identidad",
        "Tu número de verificación es 903 512. Vence en 5 minutos.",
        "903 512",
    ),
]


def _sem_o_segredo(segredo: str, *textos: str) -> bool:
    """Nem o segredo nem um pedaço dele (3+ letras/números seguidos, com dígito) sobra."""
    import re

    tudo = "\n".join(textos)
    juntos = re.sub(r"[^0-9A-Za-z]", "", tudo)
    pedacos = [
        x for x in re.split(r"[^0-9A-Za-z]+", segredo) if len(x) >= 3 and re.search(r"\d", x)
    ]
    return (
        segredo not in tudo
        and re.sub(r"[^0-9A-Za-z]", "", segredo) not in juntos
        and not any(x in tudo for x in pedacos)
    )


@pytest.mark.parametrize(("assunto", "corpo", "segredo"), CODIGO_UNICO)
def test_codigo_unico_sem_palavra_da_lista_e_de_seguranca_e_some(assunto, corpo, segredo):
    assert codigos.e_de_seguranca(assunto, corpo)
    p = codigos.proteger(assunto, corpo)
    assert _sem_o_segredo(segredo, p.assunto, p.texto), p.texto
    assert p.codigo_mascarado
    # O que o Mac já mascarou ainda é de segurança aqui.
    assert codigos.e_de_seguranca(p.assunto, p.texto)


# A senha escrita em FRASE (P19, E13, P12, P13, S05, X04 e as variantes): o
# valor some INTEIRO (o "Xp7!" de antes do símbolo também) e, na regra
# estrita, o e-mail é de segurança.
SENHA_EM_FRASE = [
    (
        "Senha redefinida",
        "Olá, sua senha foi redefinida para Xp7!kL2wQz. Recomendamos trocá-la no próximo acesso.",
        "Xp7!kL2wQz",
    ),
    (
        "Your LabelHub account",
        "Your password has been reset to Zq!8mw#Lp4. Please sign in and change it.",
        "Zq!8mw#Lp4",
    ),
    (
        "Acesso ao B2B",
        "Bom dia Eduardo,\n\nLiberei o acesso de vocês ao nosso portal de pedidos. O login é o "
        "CNPJ e a senha ficou Alfa@2026uranyx (pode trocar depois).\n\nAbraço, Carlos",
        "Alfa@2026uranyx",
    ),
    (
        "Painel da loja",
        "Fala pessoal, troquei a senha do painel da Nuvemshop, agora é Ur4nyx!Painel. "
        "Me avisa se der certo.",
        "Ur4nyx!Painel",
    ),
    (
        "Tu acceso",
        "Hola, tu usuario es uranyx y tu contraseña quedó como Prov#2026mx. "
        "Cualquier cosa me avisas.",
        "Prov#2026mx",
    ),
    ("senha nova do ML", "Troquei a do ML pra MlBarb0sa#26, anota aí", "MlBarb0sa#26"),
    ("Account", "Your password was reset to Xp7!kL2wQz", "Xp7!kL2wQz"),
    ("Senha redefinida", "Sua senha foi alterada para Xp7!kL2wQz.", "Xp7!kL2wQz"),
    ("Acesso", "Senha (temporária): Xp7!kL2wQz", "Xp7!kL2wQz"),
    ("Acesso", "Senha → Xp7!kL2wQz", "Xp7!kL2wQz"),
    ("Acceso", "Tu nueva contraseña será Xp7!kL2wQz", "Xp7!kL2wQz"),
    ("Acesso", "Sua senha agora é Xp7!kL2wQz", "Xp7!kL2wQz"),
    ("Cadastro aprovado", "Sua senha de acesso foi gerada: Kx81mq2z", "Kx81mq2z"),
    ("Senha", "Sua senha foi redefinida para abc12345.", "abc12345"),
]


@pytest.mark.parametrize(("assunto", "corpo", "senha"), SENHA_EM_FRASE)
def test_senha_em_frase_some_inteira_e_e_de_seguranca(assunto, corpo, senha):
    p = codigos.proteger(assunto, corpo)
    assert codigos.BOLINHA * len(senha) in p.texto, p.texto
    assert _sem_o_segredo(senha, p.assunto, p.texto), p.texto
    assert p.codigo_mascarado
    assert codigos.e_de_seguranca(assunto, corpo)
    assert codigos.e_de_seguranca(p.assunto, p.texto)  # já mascarada pelo Mac


# O link mágico sem "entrar" (E12, S06, X07): TODOS os links saem; com prazo,
# é de segurança na regra estrita.
LINK_MAGICO = [
    (
        "Welcome back to SellerApp",
        "Click here to log into your dashboard: https://getsellerapp.io/m/7QpX2v "
        "(expires in 15 minutes)",
        "7QpX2v",
        True,
    ),
    (
        "Tu enlace de acceso",
        "Accede a tu cuenta con este enlace: https://tiendita.mx/e/Qm3x9\n\nCaduca en 15 minutos.",
        "Qm3x9",
        True,
    ),
    (
        "Seu acesso à Loggi",
        "Toque para abrir o app já logado: abrir <https://lg.gy/a/Zp81Kd>",
        "Zp81Kd",
        False,
    ),
    ("Link", "Click to log into your account: https://x.io/m/7QpX2v", "7QpX2v", False),
    ("Acesso", "Accede a tu cuenta: https://x.mx/e/Qm3x9", "Qm3x9", False),
    (
        "Seu link",
        "Use o link abaixo, válido por 10 minutos: https://loja.example/l/Ab12Cd",
        "Ab12Cd",
        True,
    ),
]


@pytest.mark.parametrize(("assunto", "corpo", "segredo", "seguranca"), LINK_MAGICO)
def test_link_magico_sem_entrar_some(assunto, corpo, segredo, seguranca):
    p = codigos.proteger(assunto, corpo)
    assert segredo not in p.texto and "http" not in p.texto, p.texto
    assert p.links_removidos == 1
    if seguranca:
        assert codigos.e_de_seguranca(assunto, corpo)


def test_codigo_de_servico_com_cara_de_pessoa_na_caixa_de_site():
    # E06 (feedback@slack.com, "cara de pessoa"): o código "QXF-7KT" é de acesso.
    assunto = "Slack confirmation code: QXF-7KT"
    corpo = (
        "Your confirmation code is below — enter it in your open browser window and we'll help "
        "you get signed in.\n\nQXF-7KT"
    )
    assert codigos.e_de_seguranca(assunto, corpo, de_pessoa=True)
    p = codigos.proteger(assunto, corpo)
    assert "QXF" not in p.assunto + p.texto and "7KT" not in p.assunto + p.texto
    # Só o assunto em inglês já basta (o formato que a regra não conhece).
    assert codigos.e_de_seguranca("Your Notion login code", "fluffy-panda-42", de_pessoa=True)
    # P20 (atendimento@portalfornecedor): a senha some do chamado.
    p = codigos.proteger("Cadastro aprovado", "Sua senha de acesso foi gerada: Kx81mq2z")
    assert "Kx81mq2z" not in p.texto
    # O cliente que fala do código de verificação continua atendimento.
    assert not codigos.e_de_seguranca(
        "Não recebi o código de verificação",
        "Não chega o código de verificação no app, meu pedido é 2000012345678901",
        de_pessoa=True,
    )


# Controle (os 23 normais do cético 2, menos o "esqueci minha senha"): nada
# vira "•", nada vira segurança na regra estrita, nenhum link sai.
NORMAIS_DO_CETICO_2 = [
    "Olá, comprei o Uranyx U12 no pedido 2000012345678901 do Mercado Livre e ainda não chegou. "
    "Meu CPF é 123.456.789-09 e o CEP 01310-100.",
    "O pedido 240915ABCD1234 da Shopee chegou com a tela trincada. Nota fiscal 45872, valor "
    "R$ 1.299,90.",
    "O código de rastreio BR123456789BR não atualiza desde 02/10. Podem verificar?",
    "Qual a diferença entre o SKU URX-U12-128 e o URX-U12-256? Quero o de 256GB.",
    "O IMEI do meu aparelho é 356938035643809 e ele não liga desde ontem.",
    "Pode me chamar no WhatsApp (11) 98765-4321 ou 11 98765 4321.",
    "Paguei R$ 1500 no boleto e veio cobrado 1.599,90 no cartão em 3 parcelas.",
    "Chave de acesso da NF-e: 3525 1012 3456 7800 0199 5500 1000 0458 7210 0000 0001. Preciso da "
    "segunda via.",
    "A NF 004587 veio com o CNPJ errado. O correto é 12.345.678/0001-90.",
    "Meu CEP é 04567000, mas o sistema não aceita.",
    "Pedido 701-1234567-1234567: o carregador veio com defeito.",
    "Tentei usar o código do cupom URANYX10 e deu erro.",
    "Não consigo entrar no app Uranyx Care. Número de série K81Q2X, pedido 2000012345678901.",
    "Pedido 2000009876543210\nMensagem do comprador: Olá, quando chega meu pedido? Moro no CEP "
    "13010-000.",
    "Pedido 241008XYZ12345 foi entregue. Valor total R$ 89,90.",
    "Fiz o PIX de R$ 89,90, ID da transação E12345678202610081234abcdef. Segue comprovante.",
    "Mi pedido 2000011112222333 llegó incompleto, falta el cargador. "
    "Mi teléfono es +52 55 1234 5678.",
    "Order 112-3456789-0123456, tracking number 9400 1112 0123 4567 8901 23 shows delivered but I "
    "got nothing.",
    "Já abri o protocolo 482913 no Procon e o 20261008123 no Reclame Aqui.",
    "Meu Redmi Note 13 5G 256GB parou de carregar. Comprei em 15/08/2026, NF 7781.",
    "O código do produto 7891234567895 (EAN) está diferente da embalagem.",
    "Pergunta: o modelo 220333 serve no meu carro? Anúncio MLB3456789012.",
    "Pedido 2000005555666677\nMensagem do comprador: não consigo acessar o rastreio, o número "
    "48291375 não funciona.",
    # Avisos das plataformas com "confirme", "vence", "informe" e número.
    "Confirme a entrega do pedido 2000012345678901. Pacote 43210987654.",
    "Confirme os dados de envio. Rua das Flores, 1578 - CEP 01310-100.",
    "O pedido 241008XYZ12345 vence em 2 dias. Envie até 10/10.",
    "Esse celular aceita 2 chips? Informe o modelo. Anúncio MLB3456789012",
    "Devolução 48291375 aprovada. Confirme o envio até 12/10.",
    "Mensagem do comprador: o número do pedido é 48291375, confirme por favor",
    "Por segurança, não compartilhe sua senha. Venda #2000012345678901 do produto "
    "Redmi Note 13 5G.",
]


@pytest.mark.parametrize("corpo", NORMAIS_DO_CETICO_2)
def test_normais_do_cetico_2_nao_mudam_nem_viram_seguranca(corpo):
    p = codigos.proteger("Mensagem", corpo)
    assert p.texto == corpo and p.links_removidos == 0
    assert not codigos.e_de_seguranca("Mensagem", corpo)


def test_pedido_com_enchimento_antes_do_numero_nao_e_codigo():
    texto_ = (
        "Seu código: 482913. Pedido número 48291375. O rastreio, o número 48291375. "
        "Your order number is 12345678. Meu CEP é 04567000. Número de série K81Q2X."
    )
    assert codigos.mascarar(texto_) == texto_.replace("482913", "••••••", 1)
    # O código de verdade logo depois de "código" nunca é "enchimento".
    assert codigos.mascarar("Seu código é 482913") == "Seu código é ••••••"
    # A senha do celular (o desbloqueio, na garantia) é senha.
    assert codigos.proteger("Garantia", "A senha do celular é 1234").texto == (
        "A senha do celular é ••••"
    )


def test_casos_iguais_ao_conector():
    """Os casos de `apps/tuta-conector/tests/dados/protecao-casos.json`: o mesmo
    texto sai IGUAL aqui e no Rust (`casos_iguais_ao_davinci`)."""
    import json
    from pathlib import Path

    arquivo = (
        Path(__file__).resolve().parents[2]
        / "tuta-conector"
        / "tests"
        / "dados"
        / "protecao-casos.json"
    )
    if not arquivo.exists():
        pytest.skip("sem apps/tuta-conector no checkout")
    casos = json.loads(arquivo.read_text(encoding="utf-8"))
    assert len(casos) >= 60
    for caso in casos:
        p = codigos.proteger(caso["assunto"], caso["texto"])
        e = caso["esperado"]
        assert (p.assunto, p.texto, p.codigo_mascarado, p.links_removidos) == (
            e["assunto"],
            e["texto"],
            e["codigo_mascarado"],
            e["links_removidos"],
        ), caso["texto"]


def test_listas_iguais_as_do_conector():
    """As listas de `codigos.py` e de `protecao.rs` (o conector do Mac) são as MESMAS."""
    import re
    from pathlib import Path

    arquivo = (
        Path(__file__).resolve().parents[2] / "tuta-conector" / "src" / "texto" / "protecao.rs"
    )
    if not arquivo.exists():
        pytest.skip("sem apps/tuta-conector no checkout")
    rs = arquivo.read_text(encoding="utf-8")

    def lista(nome: str) -> set[str]:
        m = re.search(rf"const {nome}: &\[&str\] =\s*&\[(.*?)\];", rs, re.S)
        assert m, nome
        return set(re.findall(r'"([^"]*)"', m.group(1)))

    pares = {
        "GATILHOS": codigos.GATILHOS,
        "GATILHOS_CODIGO_DE_ACESSO": codigos.GATILHOS_CODIGO_DE_ACESSO,
        "PALAVRAS_DE_SENHA": codigos.PALAVRAS_DE_SENHA,
        "PALAVRAS_DE_CODIGO": codigos.PALAVRAS_DE_CODIGO,
        "NEUTROS_ANTES": codigos.NEUTROS_ANTES,
        "NAO_E_CODIGO_DEPOIS_DE": codigos.NAO_E_CODIGO_DEPOIS_DE,
        "NAO_E_CODIGO_ANTES_DE": codigos.NAO_E_CODIGO_ANTES_DE,
        "PALAVRAS_DE_ENTRADA": codigos.PALAVRAS_DE_ENTRADA,
        "PREFIXOS_LINK_DE_ACESSO": codigos.PREFIXOS_LINK_DE_ACESSO,
        "PEDACOS_LINK_DE_ACESSO": codigos.PEDACOS_LINK_DE_ACESSO,
        "PARAMETROS_SECRETOS": codigos.PARAMETROS_SECRETOS,
        "ENCHIMENTO_ANTES": codigos.ENCHIMENTO_ANTES,
        "TELEFONES": codigos.TELEFONES,
        "PALAVRAS_DE_OTP": codigos.PALAVRAS_DE_OTP,
        "PALAVRAS_DE_OTP_PERTO": codigos.PALAVRAS_DE_OTP_PERTO,
        "PALAVRAS_DE_LINK": codigos.PALAVRAS_DE_LINK,
        "PALAVRAS_DE_PRAZO": codigos.PALAVRAS_DE_PRAZO,
        "NAO_E_SENHA_NO_MEIO": codigos.NAO_E_SENHA_NO_MEIO,
        "TERMOS_DE_SENHA": codigos.TERMOS_DE_SENHA,
        "CONECTORES_DE_SENHA": codigos.CONECTORES_DE_SENHA,
        "CONECTORES_DE_VALOR": codigos.CONECTORES_DE_VALOR,
        "SINAIS_DE_SENHA": codigos.SINAIS_DE_SENHA,
    }
    for nome, do_python in pares.items():
        assert lista(nome) == set(do_python), nome
    # No Rust, o GATILHOS_SEGURANCA traz só o que vem DEPOIS dos de código de acesso.
    assert lista("GATILHOS_SEGURANCA") | lista("GATILHOS_CODIGO_DE_ACESSO") == set(
        codigos.GATILHOS_SEGURANCA
    )
    m = re.search(r"const NEUTROS_DEPOIS: &\[&\[&str\]\] = &\[(.*?)\];", rs, re.S)
    assert m
    depois = {tuple(re.findall(r'"([^"]*)"', x)) for x in re.findall(r"&\[([^\]]*)\]", m.group(1))}
    assert depois == set(codigos.NEUTROS_DEPOIS)
    # Os números e os símbolos.
    for nome, valor in (
        ("PERTO", codigos.PERTO),
        ("PERTO_OTP", codigos.PERTO_OTP),
        ("PERTO_LINK", codigos.PERTO_LINK),
        ("PERTO_SENHA", codigos.PERTO_SENHA),
    ):
        m = re.search(rf"const {nome}: usize = (\d+);", rs)
        assert m and int(m.group(1)) == valor, nome
    m = re.search(r"const SIMBOLOS_DE_SENHA: &\[char\] = &\[(.*?)\];", rs, re.S)
    assert m and "".join(re.findall(r"'(.)'", m.group(1))) == codigos.SIMBOLOS_DE_SENHA


@pytest.mark.parametrize(
    ("endereco", "pessoa"),
    [
        ("maria@gmail.com", True),
        ("compras@revenda.com.br", True),
        ("contato@empresa.com.br", True),
        ("seguranca@nuvemshop.com.br", False),
        ("security@facebookmail.com", False),
        ("account@service.com", False),
        ("notification@loja.com", False),
        ("no-reply@x.com", False),
        ("account-security-noreply@accountprotection.microsoft.com", False),
        ("loja@uranyx.lojavirtualnuvem.com.br", False),
        ("fulano@google.com", False),
    ],
)
def test_cara_de_pessoa(endereco, pessoa):
    assert enderecos.cara_de_pessoa(endereco) is pessoa


# ─────────────── o texto novo (sem a citação; nunca HTML) ───────────────


def test_texto_novo_sem_a_citacao():
    assert texto.sem_citacao("Oi\nobrigada\n\n> antigo\n> mais antigo") == "Oi\nobrigada"
    outlook = "Resposta\nDe: Loja\nEnviado: hoje\nPara: x\nAssunto: y\nantigo"
    assert texto.sem_citacao(outlook) == "Resposta"
    assert "De: São Paulo" in texto.sem_citacao("Moro longe.\nDe: São Paulo para o Rio")
    gmail = "Quero trocar.\n\nEm qua., 8 de out. de 2026 às 10:00, Loja <x@y.com> escreveu:\n> oi"
    assert texto.texto_novo(gmail) == "Quero trocar."
    # Com cara de HTML (não devia vir): vira texto, nunca é mostrado como HTML.
    assert "<" not in texto.texto_novo("<div>Oi <b>loja</b></div>")


def test_cortar():
    assert texto.cortar("abc", 10) == "abc"
    cortado = texto.cortar("x" * 100, 20, "[…]")
    assert len(cortado) <= 20 and cortado.endswith("[…]")


# ─────────────── pedido e protocolo (C9–C12, RF6) ───────────────


def test_c9_formato_de_cada_plataforma():
    assert pedido.citados("ml", "Pedido 2000012345678901") == ["2000012345678901"]
    assert pedido.citados("tiktok", "order 576461234567890123") == ["576461234567890123"]
    assert pedido.citados("amazon", "Pedido nº 701-1234567-1234567") == ["701-1234567-1234567"]
    assert pedido.citados("shopee", "pedido 251008ab12cd34") == ["251008AB12CD34"]
    assert pedido.citados("temu", "PO-211-12345678901234") == ["PO-211-12345678901234"]


def test_cpf_cep_telefone_e_rastreio_nao_viram_pedido():
    t = "CPF 123.456.789-09, CEP 01310-100, tel 11 98765-4321, rastreio AA123456789BR"
    for plataforma in ("ml", "shopee", "tiktok", "amazon", "temu"):
        assert pedido.citados(plataforma, t) == [], plataforma


def test_rf6_protocolo_e_tipo():
    p = pedido.protocolo("[US-26-0014] Troca de produto")
    assert (p.numero, p.letra, p.tipo, p.valido) == ("US-26-0014", "U", "sac", True)
    assert pedido.protocolo("Re: [UDS-26-0003] dúvida").tipo == "duvidas"
    assert pedido.protocolo("UA-26-0001 pedido de atacado").tipo == "atacado"
    assert pedido.protocolo("LA-26-0001").valido is False
    assert pedido.protocolo("sem nada aqui") is None


# ─────────────── destinatário → loja (C1–C8) ───────────────


def _cad(*lojas, caixas=None, marcas=None, ativas=None) -> rotear.Cadastro:
    c = rotear.Cadastro()
    for nome, plat, email, integ in lojas:
        c.lojas.append(
            rotear.Loja(
                store_info_id=uuid4(),
                plataforma=plat,
                endereco=enderecos.endereco_do_cadastro(email),
                integration_id=integ,
                nome=nome,
                sem_dominio="@" not in email,
            )
        )
    c.caixas = caixas or {}
    c.marcas = marcas or {}
    c.integracoes_ativas = set(ativas or [lj.integration_id for lj in c.lojas if lj.integration_id])
    return c


def _rota(cad, *, para, plataforma="ml", de="x@mercadolivre.com", aliases=None, marca=None):
    return rotear.rotear(
        recebeu=list(para),
        de=de,
        enviado_por_nos=False,
        plataforma_pasta=plataforma,
        marca_pasta=marca,
        caixa_pasta=None,
        aliases=set(aliases if aliases is not None else para),
        cad=cad,
    )


def test_c1_alias_e_plataforma_uma_loja():
    i = uuid4()
    cad = _cad(("Barbosa", "ml", "21max", i), ("Barbosa", "shopee", "21max", uuid4()))
    r = _rota(cad, para=["21max@tuta.com"])
    assert r.tem_loja and r.integration_id == i and r.plataforma == "ml"


def test_c2_duas_lojas_da_mesma_plataforma_e_ambiguo():
    cad = _cad(("A", "ml", "21max", uuid4()), ("B", "ml", "21max@tuta.com", uuid4()))
    r = _rota(cad, para=["21max@tuta.com"])
    assert r.motivo_sem_loja == "ambiguo" and len(r.sugestoes) == 2


def test_c3_c5_c6_sem_cadastro_sem_integracao_e_sem_alias():
    assert _rota(_cad(), para=["99x@tuta.com"]).motivo_sem_loja == "alias_sem_cadastro"
    # A ficha SEM integração (Temu, a loja que não casa com nenhuma): a LOJA é a
    # ficha — o e-mail entra na conversa dela (crítica de 08/10).
    cad = _cad(("X", "temu", "21max", None))
    r = _rota(cad, para=["21max@tuta.com"], plataforma="temu")
    assert r.tem_loja and r.so_ficha and r.integration_id is None
    assert r.store_info_id == cad.lojas[0].store_info_id and r.loja_nome == "X"
    assert cad.sem_integracao() == cad.lojas
    # O endereço que recebeu não é da caixa (lista, cópia oculta): sem alias.
    r = _rota(_cad(), para=["lista@grupo.com"], aliases=["21max@tuta.com"])
    assert r.motivo_sem_loja == "sem_alias"


def test_c8_entrada_alias_de_uma_loja_so_e_sugestao_pelo_remetente():
    i = uuid4()
    cad = _cad(("Barbosa", "ml", "21max", i))
    assert _rota(cad, para=["21max@tuta.com"], plataforma=None).integration_id == i
    cad = _cad(("Barbosa", "ml", "21max", i), ("Barbosa", "shopee", "21max", uuid4()))
    # O remetente OFICIAL da plataforma decide, mesmo na Entrada (crítica de 08/10).
    r = _rota(cad, para=["21max@tuta.com"], plataforma=None, de="aviso@mercadolivre.com.br")
    assert r.tem_loja and r.integration_id == i and r.plataforma == "ml"
    # Domínio PARECIDO não é oficial: sem loja, a pessoa escolhe.
    r = _rota(cad, para=["21max@tuta.com"], plataforma=None, de="aviso@mercadolivre-br.com")
    assert r.motivo_sem_loja == "entrada_sem_plataforma"
    assert not [s for s in r.sugestoes if s["sugestao"]]
    r = _rota(cad, para=["21max@tuta.com"], plataforma=None, de="cliente@gmail.com")
    assert r.motivo_sem_loja == "entrada_sem_plataforma" and len(r.sugestoes) == 2


def test_rf6_alias_de_marca_e_pasta_de_site():
    mid = uuid4()
    cad = _cad(
        caixas={
            "sac@uranyx.com.br": rotear.CaixaMarca(
                marca_id=mid, slug="uranyx", nome="Uranyx", tipo="sac"
            )
        },
        marcas={"uranyx": (mid, "Uranyx", ("uranyx.com.br",))},
    )
    r = _rota(cad, para=["sac@uranyx.com.br"], plataforma="site", marca="uranyx")
    assert r.site and r.marca_id == mid and r.tipo_caixa == "sac"
    r = _rota(cad, para=["duvidas@uranyx.com.br"], plataforma=None)
    assert r.site and r.tipo_caixa == "duvidas"


def test_loja_do_cadastro_sac_marca_casa_os_dois_dominios():
    i = uuid4()
    cad = rotear.Cadastro(
        lojas=[
            rotear.Loja(
                store_info_id=uuid4(),
                plataforma="ml",
                endereco="",
                integration_id=i,
                nome="VR",
                sem_dominio=False,
                parcial=("sac", "uranyx"),
            )
        ],
        integracoes_ativas={i},
    )
    assert _rota(cad, para=["sac@uranyx.com"]).integration_id == i
    assert _rota(cad, para=["sac@uranyx.com.br"]).integration_id == i
    assert cad.e_de_loja("sac@uranyx.com.br") and not cad.e_de_loja("adm@uranyx.com.br")


def test_integracao_da_ficha_pelo_par_nome_e_plataforma():
    a, b, c = uuid4(), uuid4(), uuid4()
    pares = {("jlas2", "ml"): [a], ("barbosa", "shopee"): [b], ("mega", "ml"): [c, uuid4()]}
    # Produção: "16tr"/"jlas2" sem FK; a integração " Barbosa" com espaço na frente.
    assert rotear.integracao_pelo_par(pares, " JLAS2 ", "ml") == a
    assert rotear.integracao_pelo_par(pares, "barbosa", "shopee") == b
    assert rotear.integracao_pelo_par(pares, "barbosa", "ml") is None
    # Duas integrações com o mesmo nome e plataforma: ninguém adivinha.
    assert rotear.integracao_pelo_par(pares, "mega", "ml") is None
    assert rotear.plataforma_da_ficha("mercadolivre") == "ml"


def _cad_marcas() -> rotear.Cadastro:
    """Como em produção: poofy (nome "charlots") e charlots-park dividem charlots.com.br."""
    cad = rotear.Cadastro()
    for slug, nome, doms in (
        ("7buyers", "7buyers", ("7buyers.com.br", "7buyers.com")),
        ("charlots-park", "Charlots Park", ("charlotspark.com.br", "charlots.com.br")),
        ("poofy", "charlots", ("charlots.com.br",)),
        ("uranyx", "uranyx", ("uranyx.com.br", "uranyx.com")),
    ):
        cad.marcas[slug] = (uuid4(), nome, doms)
    mid = cad.marcas["uranyx"][0]
    cad.caixas["sac@uranyx.com.br"] = rotear.CaixaMarca(
        marca_id=mid, slug="uranyx", nome="uranyx", tipo="sac"
    )
    return cad


def _site(cad, endereco, *, marca=None, de="cliente@gmail.com", plataforma="site"):
    return rotear.rotear(
        recebeu=[endereco],
        de=de,
        enviado_por_nos=False,
        plataforma_pasta=plataforma if marca else None,
        marca_pasta=marca,
        caixa_pasta=None,
        aliases={endereco},
        cad=cad,
    )


def test_so_caixa_de_site_vira_chamado():
    cad = _cad_marcas()
    assert _site(cad, "sac@7buyers.com.br").site
    assert _site(cad, "support@uranyx.com").site
    assert _site(cad, "atacado@uranyx.com.br").tipo_caixa == "atacado"
    # Endereço de PESSOA no domínio da marca (o contador para gabrieli@): não é site.
    for endereco, marca in (
        ("gabrieli@7buyers.com.br", None),
        ("gabrieli@7buyers.com.br", "7buyers"),
        ("ouvidoria@doogee.com.br", "uranyx"),
        ("bosso.g@doogee.com.br", "uranyx"),
    ):
        r = _site(cad, endereco, marca=marca)
        assert not r.site and not r.tem_loja, (endereco, marca)
    # sac@ de outro domínio numa pasta "*marca" de marca que EXISTE: a marca da pasta.
    r = _site(cad, "sac@doogee.com.br", marca="uranyx")
    assert r.site and r.marca_slug == "uranyx"
    # Pasta de marca que NÃO existe no cadastro (makisa): não vira site.
    assert not _site(cad, "sac@makisa.com.br", marca="makisa").site
    assert not rotear.e_caixa_de_site("sac@makisa.com.br", cad, marca_pasta="makisa")
    # …e o sac@ de uma marca de verdade nessa pasta segue o domínio dele.
    assert _site(cad, "sac@7buyers.com.br", marca="makisa").marca_slug == "7buyers"


def test_marca_pela_pasta_antes_do_dominio_e_dominio_em_duas_marcas():
    cad = _cad_marcas()
    r = _site(cad, "sac@charlots.com.br")
    assert r.motivo_sem_loja == "marca_ambigua" and not r.tem_loja
    assert {s["marca_slug"] for s in r.sugestoes} == {"charlots-park", "poofy"}
    # A pasta diz a marca.
    assert _site(cad, "sac@charlots.com.br", marca="charlots-park").marca_slug == "charlots-park"
    # O protocolo diz a marca (C → charlots-park), nunca a ordem da tabela.
    resolvida = rotear.marca_pelo_protocolo(r, pedido.MARCA_DA_LETRA["C"], cad)
    assert resolvida.tem_loja and resolvida.marca_slug == "charlots-park"
    # A ordem do cadastro não muda nada.
    invertido = rotear.Cadastro(marcas=dict(reversed(list(cad.marcas.items()))))
    assert [s["marca_slug"] for s in _site(invertido, "sac@charlots.com.br").sugestoes] == [
        "charlots-park",
        "poofy",
    ]


def test_aviso_da_plataforma_para_o_sac_que_e_login_de_loja_vai_para_a_loja():
    cad = _cad_marcas()
    i = uuid4()
    cad.lojas.append(
        rotear.Loja(
            store_info_id=uuid4(),
            plataforma="ml",
            endereco="",
            integration_id=i,
            nome="VR",
            sem_dominio=False,
            parcial=("sac", "uranyx"),
        )
    )
    cad.integracoes_ativas = {i}
    for marca in (None, "uranyx"):
        r = _site(cad, "sac@uranyx.com.br", marca=marca, de="noreply@mercadolivre.com")
        assert not r.site and r.integration_id == i, marca
    # O cliente escrevendo para o sac@: o chamado do site.
    assert _site(cad, "sac@uranyx.com.br", de="maria@gmail.com").site


# ─────────────── a ponte: filtro, hashes, quem escreveu, pendência ───────────────


def _email(**kw) -> ponte.Email:
    base = {
        "message": MailMessage(
            id=uuid4(),
            source_id="imap:1:1",
            direction="inbound",
            received_at=datetime(2026, 10, 8, 13, 15, tzinfo=UTC),
            attachment_count=0,
        ),
        "conteudo": {},
        "de": "maria@gmail.com",
        "de_nome": "Maria",
        "para": ["mia30@tuta.com"],
        "cc": [],
        "delivered_to": [],
        "reply_to": [],
        "assunto": "Troca",
        "texto": "Quero trocar.",
    }
    base.update(kw)
    return ponte.Email(**base)


def test_filtro_so_aliases_de_loja_nunca_o_principal():
    de_loja = {"mia30@tuta.com"}.__contains__
    aliases = {"goslin@tuta.com", "mia30@tuta.com"}
    kw = {"principal": "goslin@tuta.com", "aliases": aliases, "e_de_loja": de_loja}
    assert ponte.alias_de_loja(_email(), enviado=False, **kw) == "mia30@tuta.com"
    assert ponte.alias_de_loja(_email(para=["goslin@tuta.com"]), enviado=False, **kw) is None
    # Para o alias da loja, mas ele não está na lista da caixa: não passa.
    assert (
        ponte.alias_de_loja(
            _email(),
            enviado=False,
            principal="goslin@tuta.com",
            aliases={"goslin@tuta.com"},
            e_de_loja=de_loja,
        )
        is None
    )
    # O principal NUNCA passa, mesmo que um cadastro aponte para ele.
    assert (
        ponte.alias_de_loja(
            _email(para=["goslin@tuta.com"]),
            enviado=False,
            principal="goslin@tuta.com",
            aliases=aliases,
            e_de_loja={"goslin@tuta.com"}.__contains__,
        )
        is None
    )
    # O enviado: pelo remetente.
    assert ponte.alias_de_loja(_email(de="mia30@tuta.com", para=["x@y.com"]), enviado=True, **kw)


def test_hash_do_message_id_e_referencias():
    assert ponte.hash_mid(" <AbC@x.com> ") == ponte.hash_mid("AbC@x.com")
    assert ponte.hash_mid("AbC@x.com") != ponte.hash_mid("abc@x.com")
    assert ponte.hash_mid("") is None and ponte.hash_mid(None) is None
    e = _email(conteudo={"in_reply_to": "<a@x>", "references": ["<z@x>", "<a@x>"]})
    assert e.refs == [ponte.hash_mid("a@x"), ponte.hash_mid("z@x")]


def test_tuta_id_do_source_id_ou_do_bloco_v2():
    m = MailMessage(source_id="tuta:Lx/Ey")
    assert ponte.tuta_id_de(m, {}) == "Lx/Ey"
    assert ponte.tuta_id_de(MailMessage(source_id="imap:1:2"), {}) is None
    assert ponte.tuta_id_de(MailMessage(source_id="imap:1:2"), {"mail_id": ["L", "E"]}) == "L/E"


def test_quem_escreveu():
    nossos = {"21max@tuta.com"}
    assert ponte.remetente_tipo("21max@tuta.com", nossos) == "nosso"
    assert ponte.remetente_tipo("no-reply@tutao.de", nossos) == "tuta"
    assert ponte.remetente_tipo("nao-responder@mercadolivre.com.br", nossos) == "aviso"
    assert ponte.remetente_tipo("maria@gmail.com", nossos) == "comprador"


def test_pendencia_vendas_e_aviso_das_plataformas_com_api():
    assert ponte.abre_pendencia(plataforma="ml", finalidade="problema", autor="cliente")
    assert not ponte.abre_pendencia(plataforma="ml", finalidade="vendas", autor="cliente")
    # Aviso do ML (tem API): sem pendência própria; da Temu (sem API): pendência.
    assert not ponte.abre_pendencia(plataforma="ml", finalidade="problema", autor="sistema")
    assert ponte.abre_pendencia(plataforma="temu", finalidade="problema", autor="sistema")
    assert ponte.assunto_sem_pendencia("Resumo das suas vendas")


# ─────────────── a resposta ───────────────


def test_assunto_re_e_protocolo():
    assert responder.assunto_da_resposta("Troca", None) == "Re: Troca"
    assert responder.assunto_da_resposta("RE: Troca", None) == "RE: Troca"
    assert (
        responder.assunto_da_resposta("Troca de produto", "US-26-0014")
        == "Re: [US-26-0014] Troca de produto"
    )
    assert responder.assunto_da_resposta("", None) == "Re: Sua mensagem"


def test_citacao_curta_e_protegida():
    e = _email(texto="Código: 482913\n" + "\n".join(f"linha {i}" for i in range(50)))
    cabeca, citado = responder.citacao(e)
    assert cabeca == "Em 08/10/2026 10:15, Maria <maria@gmail.com> escreveu:"
    assert "482913" not in citado
    assert citado.count("\n") <= 31 and citado.endswith("[…]")


def test_corpo_da_resposta_com_assinatura_e_citacao():
    p = responder.Previa(
        assinatura="Atenciosamente,\nBarbosa", citacao_cabeca="Em X, Y escreveu:", citacao="oi"
    )
    corpo = p.corpo_texto("Olá! Vamos trocar.")
    assert corpo == "Olá! Vamos trocar.\n\nAtenciosamente,\nBarbosa\n\nEm X, Y escreveu:\n> oi"


def test_texto_da_resposta_validado():
    from app.services.atendimento.enviar import EnvioRecusado

    assert responder.preparar_texto("  Oi \r\n tudo bem  ") == "Oi\n tudo bem"
    with pytest.raises(EnvioRecusado) as e:
        responder.preparar_texto("   ")
    assert e.value.code == "texto_invalido"
    with pytest.raises(EnvioRecusado):
        responder.preparar_texto("Seu rastreio é {rastreio}")
