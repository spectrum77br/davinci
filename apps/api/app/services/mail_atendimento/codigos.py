"""Código de verificação, senha e link de acesso: o que a equipe NUNCA vê.

D8 + as críticas de 08/10 (a revisão e a conferência pré-subida).

Os aliases da conta geral do Tuta são os e-mails de LOGIN das contas das lojas
no ML, na Shopee e nas outras. Um código ou um link de "redefinir senha" na
mão de qualquer leitor do /atendimento tomaria a conta da loja. Por isso,
antes de qualquer coisa ir para a conversa (e para a IA, o cartão, a fila):

  1. e-mail DE SEGURANÇA (`e_de_seguranca`) NUNCA vira conversa nem aparece
     em fila — estado `seguranca`, fica só na caixa inteira (dono e admins).
     Na regra ESTRITA (o que não vem de pessoa num lugar de pessoa) é de
     segurança: uma frase da lista (código de verificação, senha, novo
     acesso, confirmar e-mail, link mágico…); OU um CÓDIGO SOLTO junto de uma
     palavra de acesso ("código", "code", "token", "PIN", "senha", "clave"…)
     ou de entrada ("entrar", "login", "sign in"…) em qualquer lugar do
     e-mail — sem depender da frase exata (crítica pré-subida de 08/10:
     "Use o código 482913 para entrar", "Your sign-in code is 482913",
     "Seu token de acesso: 482913" viravam conversa); OU uma senha escrita
     ("Senha gerada: Kx81mq2z"); OU (crítica pré-subida 2 de 08/10) um código
     FORTE com uma palavra de CÓDIGO ÚNICO perto ("digite este número 731
     604", "informe a chave 804 117", "Enter 604 381 … to approve", "Tu
     número de verificación es 903 512"); OU a senha escrita em frase ("sua
     senha foi redefinida para X", "a senha ficou X", "has been reset to
     X", "quedó como X"); OU o link com prazo ("Click here … (expires in
     15 minutes)");
  2. em todo o resto, o link que dá ACESSO (login, senha, confirmação,
     token na URL, valor opaco longo) vira "[link de acesso removido]"
     (`mascarar_links`) — e, no e-mail que FALA de senha, código ou entrada
     (`fala_de_acesso`), TODOS os links (o link curto "bit.ly/3xYz" não tem
     cara de acesso); se o e-mail fala de código ou de acesso, os códigos
     viram "•" (`mascarar`); e o VALOR da senha escrita no texto ("Senha do
     painel: Kx81mq2z", "sua senha Kx81mq2z", "a senha ficou Alfa@2026x",
     "Troquei a do ML pra MlBarb0sa#26" com "senha" no assunto) vira "•"
     sempre (`mascarar_senha`), o valor INTEIRO.

O CÓDIGO SOLTO (`_codigos`), por "pedaços" do texto (letras, números, "_",
"-" e "•" juntos — a "palavra"):
  • FORTE: 4 a 8 dígitos; 6 a 8 de [A-Z0-9] com letra e dígito ("AB12CD");
    "G-482913"; o partido "482 913", "48 29 13", "4829 1300", "123-456";
  • FRACO (só conta perto — até 4 pedaços — de uma palavra de acesso, ou no
    e-mail de código de acesso): o minúsculo com letra e dígito ("k7x9q2"),
    o de 5 com letra e dígito ("F4K2T"), o de letras e números com hífen
    ("QXF-7KT") e o número com cara de ano (1900–2099);
  • JÁ MASCARADO pelo conector do Mac ("••••••", "••• •••", "G-••••••"):
    conta sempre (o número nunca chegou aqui).
  Nunca é código (o texto normal do cliente fica): o número que faz parte de
  um maior ("123.456.789-09", "3524 1012 3456 …", "11 98765 4321"), o valor
  ("R$ 1500", "1500,00", "15%"), a data e o caminho ("08/10/2026"), o
  "#1234", o telefone "(11) 3456-7890", o que vem depois de "pedido", "CEP",
  "CPF", "nota", "rastreio", "série"… (mesmo com "número", "é", "do" no
  meio: "pedido número 48291375", "o rastreio, o número 48291375") e antes
  de uma unidade ("5000 mAh", "1500 reais"). E "código de rastreio", "código
  do produto", "tracking code", "postal code"… não são palavra de acesso.

As listas e as regras (em português, inglês e espanhol) são as MESMAS do
conector do Mac (`apps/tuta-conector/src/texto/protecao.rs`): o que ele
mascara lá ainda faz o e-mail ser de segurança aqui, e o mesmo texto sai
igual dos dois lados (o teste de paridade roda os dois).

Texto só (a regra da Central: nunca HTML). PURO. A parte de código veio do
wt-tuta (`tuta/codigos.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

# Sem acento, minúsculo (comparado com o texto plano). O e-mail que fala de
# código tem os códigos mascarados.
GATILHOS = (
    "codigo de verificacao",
    "codigo de seguranca",
    "codigo de acesso",
    "codigo de confirmacao",
    "codigo de login",
    "codigo para entrar",
    "codigo para acessar",
    "codigo de autenticacao",
    "codigo de uso unico",
    "seu codigo",
    "o codigo e",
    "codigo:",
    "senha temporaria",
    "senha de uso unico",
    "verification code",
    "security code",
    "login code",
    "confirmation code",
    "access code",
    "sign-in code",
    "signin code",
    "one-time",
    "one time password",
    "otp",
    "2fa",
    "two-factor",
    "authentication code",
    "your code",
    "codigo otp",
    "token de acesso",
    # senha escrita no e-mail
    "nova senha",
    "senha de acesso",
    "senha provisoria",
    "senha gerada",
    "senha:",
    "password:",
    # espanhol
    "codigo de verificacion",
    "codigo de seguridad",
    "codigo de acceso",
    "codigo de confirmacion",
    "codigo de inicio de sesion",
    "tu codigo",
    "token de acceso",
    "contrasena",
    "clave temporal",
    "clave de acceso",
)
# O CÓDIGO de acesso (vale também para e-mail de pessoa, com o código junto).
GATILHOS_CODIGO_DE_ACESSO = (
    "codigo de verificacao",
    "codigo de seguranca",
    "codigo de acesso",
    "codigo de confirmacao",
    "codigo de login",
    "codigo para entrar",
    "codigo para acessar",
    "codigo de autenticacao",
    "codigo de uso unico",
    "codigo otp",
    "senha temporaria",
    "senha de uso unico",
    "verification code",
    "security code",
    "login code",
    "authentication code",
    "confirmation code",
    "access code",
    "sign-in code",
    "signin code",
    "one-time password",
    "one time password",
    "one-time code",
    "otp",
    "2fa",
    "two-factor",
    "two-step",
    "dois fatores",
    "duas etapas",
    "token de acesso",
    "codigo de verificacion",
    "codigo de seguridad",
    "codigo de acceso",
    "codigo de confirmacion",
    "codigo de inicio de sesion",
    "token de acceso",
    "verificacion en dos pasos",
    # crítica pré-subida 2 de 08/10
    "chave de seguranca",
    "numero de verificacao",
    "numero de verificacion",
    "verification number",
    "contrasena de un solo uso",
    "clave dinamica",
)
# Senha, novo acesso, confirmar e-mail, ativar conta, link de entrada: só
# quando quem manda NÃO é uma pessoa (a plataforma, um serviço, o Tuta).
GATILHOS_SEGURANCA = (
    *GATILHOS_CODIGO_DE_ACESSO,
    # senha
    "redefinir senha",
    "redefinir sua senha",
    "redefinicao de senha",
    "redefina sua senha",
    "recuperar senha",
    "recuperar sua senha",
    "recuperacao de senha",
    "recupere sua senha",
    "alterar senha",
    "alterar sua senha",
    "alteracao de senha",
    "senha alterada",
    "senha foi alterada",
    "sua nova senha",
    "nova senha",
    "senha de acesso",
    "senha provisoria",
    "senha gerada",
    "criar nova senha",
    "troca de senha",
    "trocar sua senha",
    "esqueceu sua senha",
    "esqueceu a senha",
    "esqueci minha senha",
    "reset your password",
    "reset password",
    "password reset",
    "change your password",
    "password changed",
    "password was changed",
    "forgot your password",
    "new password",
    "temporary password",
    # a senha escrita em frase (crítica pré-subida 2 de 08/10)
    "senha redefinida",
    "senha foi redefinida",
    "senha foi gerada",
    "senha foi criada",
    "password has been reset",
    "password was reset",
    "password has been changed",
    "your password is",
    "contrasena restablecida",
    # novo acesso
    "novo acesso",
    "novo login",
    "login detectado",
    "acesso detectado",
    "novo dispositivo",
    "dispositivo novo",
    "tentativa de login",
    "tentativa de acesso",
    "new sign-in",
    "new sign in",
    "new login",
    "sign-in attempt",
    "login attempt",
    "new device",
    # confirmar e-mail / ativar conta / link de entrada
    "confirme seu e-mail",
    "confirme seu email",
    "confirmar seu e-mail",
    "confirmar seu email",
    "confirmacao de e-mail",
    "confirmacao de email",
    "verifique seu e-mail",
    "verifique seu email",
    "verificar seu e-mail",
    "verificar seu email",
    "verify your email",
    "verify your e-mail",
    "confirm your email",
    "confirm your e-mail",
    "email verification",
    "ative sua conta",
    "ativar sua conta",
    "ativacao da conta",
    "ativacao de conta",
    "activate your account",
    "link de acesso",
    "link magico",
    "magic link",
    "link de login",
    "login link",
    "sign-in link",
    "desbloquear sua conta",
    "unlock your account",
    "link para entrar",
    "link para acessar",
    # espanhol
    "restablecer contrasena",
    "restablecer tu contrasena",
    "cambiar tu contrasena",
    "cambio de contrasena",
    "nueva contrasena",
    "olvidaste tu contrasena",
    "contrasena temporal",
    "clave temporal",
    "clave de acceso",
    "inicio de sesion",
    "iniciar sesion",
    "nuevo dispositivo",
    "verifica tu correo",
    "verificar tu correo",
    "confirma tu correo",
    "confirmar tu correo",
    "activa tu cuenta",
    "enlace de acceso",
    "enlace para entrar",
    "enlace de inicio de sesion",
)
# Palavra que, sozinha, faz o e-mail "falar de senha" (todos os links saem).
PALAVRAS_DE_SENHA = ("senha", "password", "passcode", "contrasena")
# A palavra (pedaço inteiro, sem acento, minúsculo) que diz "código/segredo":
# com um código solto no e-mail, ele é de segurança (regra estrita).
PALAVRAS_DE_CODIGO = frozenset(
    {
        "codigo",
        "codigos",
        "code",
        "codes",
        "token",
        "pin",
        "clave",
        "senha",
        "password",
        "contrasena",
        "passcode",
        "otp",
    }
)
# "código"/"code" que NÃO é de acesso: o que vem logo DEPOIS (1 ou 2 pedaços)…
NEUTROS_DEPOIS = (
    ("de", "rastreio"),
    ("de", "rastreamento"),
    ("do", "rastreio"),
    ("do", "rastreamento"),
    ("de", "rastreo"),
    ("de", "seguimiento"),
    ("do", "produto"),
    ("dos", "produtos"),
    ("del", "producto"),
    ("do", "anuncio"),
    ("do", "item"),
    ("do", "pedido"),
    ("del", "pedido"),
    ("de", "barras"),
    ("da", "nota"),
    ("do", "cupom"),
    ("de", "cupom"),
    ("de", "desconto"),
    ("de", "envio"),
    ("de", "postagem"),
    ("do", "objeto"),
    ("de", "objeto"),
    ("postal",),
    ("fiscal",),
    ("sku",),
    ("ean",),
    ("ncm",),
    ("cfop",),
    ("promocional",),
)
# …ou logo ANTES ("tracking code", "postal code").
NEUTROS_ANTES = frozenset(
    {
        "tracking",
        "postal",
        "zip",
        "product",
        "promo",
        "coupon",
        "discount",
        "qr",
        "bar",
        "hs",
        "country",
        "area",
        "source",
    }
)
# O número logo DEPOIS destes pedaços não é código (pedido, CEP, nota…).
NAO_E_CODIGO_DEPOIS_DE = frozenset(
    {
        "cep",
        "cpf",
        "cnpj",
        "pedido",
        "pedidos",
        "order",
        "nf",
        "nfe",
        "nota",
        "fiscal",
        "rastreio",
        "rastreamento",
        "tracking",
        "sku",
        "ean",
        "anuncio",
        "item",
        "protocolo",
        "telefone",
        "tel",
        "fone",
        "celular",
        "pacote",
        "pergunta",
        "reclamacao",
        "devolucao",
        "chamado",
        "serie",
        "serial",
        "imei",
        "modelo",
        "model",
        "invoice",
        "shipment",
        "danfe",
    }
)
# Entre um destes e o número podem vir até 3 pedaços "de enchimento" ("pedido
# número 48291375", "o rastreio, o número 48291375", "Your order number is
# 12345678", "CEP é 04567000"): continua não sendo código. O TELEFONE só
# logo antes ("liberar o novo celular é 7 3 1 8 2 0" é código).
TELEFONES = frozenset({"telefone", "tel", "fone", "celular"})
ENCHIMENTO_ANTES = frozenset(
    {
        "e",
        "eh",
        "de",
        "do",
        "da",
        "n",
        "no",
        "nº",
        "nr",
        "num",
        "numero",
        "number",
        "is",
        "es",
        "o",
        "a",
        "the",
        "seu",
        "sua",
        "meu",
        "minha",
        "your",
        "my",
        "tu",
        "mi",
        "su",
    }
)
# …nem o que vem logo ANTES de uma unidade ("5000 mAh", "1500 reais").
NAO_E_CODIGO_ANTES_DE = frozenset(
    {
        "reais",
        "real",
        "centavos",
        "dolares",
        "dollars",
        "usd",
        "brl",
        "mah",
        "gb",
        "mb",
        "tb",
        "kb",
        "mp",
        "hz",
        "ghz",
        "mhz",
        "w",
        "v",
        "kg",
        "g",
        "mm",
        "cm",
        "m",
        "km",
        "ml",
        "dias",
        "horas",
        "minutos",
        "unidades",
        "un",
        "pecas",
        "itens",
        "parcelas",
        "pontos",
        "anos",
        "meses",
    }
)
# Fala de ENTRAR na conta (frase inteira, sem acento, minúscula): com um código
# solto, o e-mail estrito é de segurança; e TODOS os links saem. "entrar em
# contato" não conta.
PALAVRAS_DE_ENTRADA = (
    "entrar",
    "acessar",
    "acesse sua conta",
    "acesse a sua conta",
    "acesse a conta",
    "access your account",
    "login",
    "log in",
    "log-in",
    "logon",
    "sign in",
    "sign-in",
    "signin",
    "iniciar sesion",
    "inicia sesion",
    "ingresa",
    "ingresar",
    "reset",
    # crítica pré-subida 2 de 08/10 (o link mágico sem "entrar")
    "log into",
    "logged in",
    "accede",
    "acceder",
    "accede a tu cuenta",
    "logado",
    "logada",
    "ja logado",
    "continue to your account",
)
_ENTRAR_EM_CONTATO = ("entrar em contato", "entrar em contacto")
# CÓDIGO ÚNICO sem a palavra "código" (crítica pré-subida 2 de 08/10): o
# código FORTE (que não é pedido, CEP, valor nem data; 5+ caracteres — o
# "1578" do endereço e o "2026" ficam) com uma destas a até `PERTO_OTP`
# pedaços é de segurança na regra estrita, e é mascarado sempre.
PALAVRAS_DE_OTP = frozenset(
    {
        "verificacao",
        "verificacion",
        "verification",
        "verify",
        "verifique",
        "verifica",
        "verificar",
        "confirme",
        "confirmar",
        "confirm",
        "confirma",
        "confirmacao",
        "confirmacion",
        "confirmation",
        "identidade",
        "identidad",
        "identity",
        "digite",
        "insira",
        "informe",
        "enter",
        "ingresa",
        "ingrese",
        "introduce",
        "introduzca",
        "aprovar",
        "aprove",
        "approve",
        "autorizar",
        "autorize",
        "autoriza",
        "authorize",
        "expira",
        "expiram",
        "expires",
        "expire",
        "vence",
        "caduca",
        "valido",
        "valida",
        "valid",
        "compartilhe",
        "share",
        "compartas",
        "comparta",
    }
)
# …e estas só bem perto (até `PERTO` pedaços): "digite o número 615 029",
# "informe a chave 804 117".
PALAVRAS_DE_OTP_PERTO = frozenset({"numero", "number", "chave", "key"})
PERTO_OTP = 10
# O LINK COM PRAZO ("Click here … (expires in 15 minutes)", "Accede … con este
# enlace … Caduca en 15 minutos"): uma palavra de link perto (até
# `PERTO_LINK` pedaços) de uma de prazo, num e-mail com link — TODOS os links
# saem e, na regra estrita, é de segurança.
PALAVRAS_DE_LINK = frozenset(
    {"link", "links", "enlace", "botao", "button", "clique", "click", "toque", "tap"}
)
PALAVRAS_DE_PRAZO = frozenset(
    {
        "expira",
        "expiram",
        "expirar",
        "expires",
        "expire",
        "caduca",
        "vence",
        "valido",
        "valida",
        "valid",
        "minutos",
        "minuto",
        "minutes",
        "minute",
    }
)
PERTO_LINK = 12
# Na regra AFROUXADA (pessoa num lugar de pessoa), o ASSUNTO com o código de
# acesso em inglês ("Slack confirmation code: QXF-7KT") já é de segurança,
# mesmo sem um código que a regra reconheça: o cliente daqui não escreve
# isso. (Só o Python: o conector do Mac não decide segurança.)
GATILHOS_DE_SERVICO = (
    "verification code",
    "confirmation code",
    "security code",
    "login code",
    "sign-in code",
    "signin code",
    "access code",
    "authentication code",
    "one-time password",
    "one time password",
    "one-time code",
    "two-factor",
    "two-step",
    "otp",
    "2fa",
)
# As classes das regex, ESCRITAS À MÃO e iguais às do Rust (o `\b`, o `\w` e
# o `\s` dos dois motores não são iguais: "²" é letra num e não no outro).
# O espaço (o White_Space do Unicode) e a letra de palavra (latim, número, _).
_ESPACO = " \t\n\r\x0b\x0c\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
_LETRA = "0-9A-Za-z_\u00c0-\u024f"
# No "(?i:…)", o "i" é escrito "[iİı]": o Python casa o "i" com "İ" e "ı", o
# Rust não (o "s"/"ſ" e o "k"/"K" casam igual nos dois).
# O `strip` do nome do parâmetro: os espaços e o \x1c–\x1f (o `strip()` do
# Python tira os dois; o `trim()` do Rust, só os espaços — os dois lados usam ESTE).
_BRANCOS = (
    " \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85\xa0\u1680"
    + "".join(map(chr, range(0x2000, 0x200B)))
    + "\u2028\u2029\u202f\u205f\u3000"
)
_RE_LETRAS = re.compile(rf"[{_LETRA}]+")
# "Use o código … para entrar", "code … to log in" (até 3 pedaços no meio).
_RE_CODIGO_PARA_ENTRAR = re.compile(
    rf"(?:^|[^{_LETRA}])(?:codigo|code)(?:[{_ESPACO}]+[^{_ESPACO}]+){{0,3}}[{_ESPACO}]+"
    rf"(?:para|to)[{_ESPACO}]+(?:entrar|acessar|logar|fazer[{_ESPACO}]+login"
    rf"|iniciar[{_ESPACO}]+sessao|iniciar[{_ESPACO}]+sesion|log[{_ESPACO}]+in|sign[{_ESPACO}]+in"
    rf"|login)(?:$|[^{_LETRA}])"
)
# Até 4 pedaços entre a palavra de acesso e o código FRACO.
PERTO = 4
# O VALOR da senha (ou do token) escrita no texto. (1) o termo, até 6
# palavras (com "," ou "(…)" no meio: "senha do painel da Nuvemshop, agora é
# X", "Senha (temporária): X") e um separador (":", "=", " - ", "→", "é",
# "is", "es"): "Senha gerada: X", "Password for login: X", "Tu clave temporal
# es X", "Token: X". Com ":"/"=" o valor de 3+ some sempre; com o resto, só
# com cara de senha (dígito, maiúscula no meio ou símbolo) — "a senha é
# importante" fica. Os grupos: 1 o que vem antes (a borda), 2 o termo, 3 as
# palavras, 4 o separador, 5 o valor.
_RE_SENHA = re.compile(
    rf"(^|[^{_LETRA}])((?i:senha|password|passcode|contrase[nñ]a|clave|token))"
    rf"((?:(?:[{_ESPACO}]*[,(][{_ESPACO}]*|[{_ESPACO}]+)[{_LETRA}]+\)?){{0,6}}?)"
    rf"([{_ESPACO}]*(?:→|->|=>)[{_ESPACO}]*|[{_ESPACO}]*[:=][{_ESPACO}]*"
    rf"|[{_ESPACO}]+[-–—][{_ESPACO}]+"
    rf"|[{_ESPACO}]+(?i:é|e|eh|[iİı]s|es)(?:[{_ESPACO}]*[:=][{_ESPACO}]*|[{_ESPACO}]+))"
    rf"([^{_ESPACO},;]{{1,64}})"
)
# (2) Sem separador, logo depois do termo (com um qualificador): "sua senha
# Kx81mq2z", "Sua senha provisória Kx81mq2z" — só com cara de senha.
_RE_SENHA_SOLTA = re.compile(
    rf"(^|[^{_LETRA}])((?i:senha|password|contrase[nñ]a|clave))"
    rf"((?:[{_ESPACO}]+(?i:prov[iİı]s[oó]r[iİı]a|tempor[aá]r[iİı]a|temporal|temporary|nova|nueva|new|gerada"
    rf"|[iİı]n[iİı]c[iİı]al|atual))?)([{_ESPACO}]+)([^{_ESPACO},;]{{4,64}})"
)
# As palavras no meio que dizem que o valor é OUTRA coisa ("senha do pedido:
# 2000…"). "A senha do celular é 1234" (o desbloqueio do aparelho, na
# garantia) é senha: o celular, o telefone e o WhatsApp não estão aqui.
NAO_E_SENHA_NO_MEIO = frozenset(
    {
        "pedido",
        "pedidos",
        "order",
        "rastreio",
        "rastreamento",
        "cpf",
        "cnpj",
        "cep",
        "nota",
        "nf",
        "valor",
        "total",
        "preco",
        "protocolo",
    }
)
# A palavra logo antes do valor que diz que ele é outra coisa (regras a, b, c).
_ANTES_NAO_E_SENHA = NAO_E_CODIGO_DEPOIS_DE | NAO_E_SENHA_NO_MEIO
_FIM_DO_VALOR = ".)!?\"'»:>"
_ABRE_O_VALOR = "<(\"'«"

# (3) A senha escrita em FRASE (crítica pré-subida 2 de 08/10), pelas
# "palavras" do texto (o que fica entre espaços, sem a pontuação das pontas):
#   a. a palavra com CARA FORTE de senha (letra e dígito, e símbolo ou
#      maiúscula com minúscula: "Xp7!kL2wQz", "Alfa@2026uranyx", "Kx81mq2z")
#      até `PERTO_SENHA` palavras depois do termo: "sua senha foi redefinida
#      para X", "a senha ficou X", "password has been reset to X", "quedó
#      como X";
#   b. com o termo no ASSUNTO, a de cara forte logo depois de um conector
#      ("pra", "para", "é", ":", "→"…): assunto "senha nova do ML", texto
#      "Troquei a do ML pra MlBarb0sa#26";
#   c. depois de "para", "pra", "to", "como", "ficou", "será", "quedó" com o
#      termo antes (até `PERTO_SENHA`): a de cara de senha comum ("senha foi
#      redefinida para abc12345").
# O VALOR INTEIRO vira "•" (o "Xp7!" de antes do símbolo também).
TERMOS_DE_SENHA = frozenset(
    {"senha", "senhas", "password", "passwords", "passcode", "contrasena", "clave"}
)
CONECTORES_DE_SENHA = frozenset(
    {"e", "eh", "is", "es", "para", "pra", "to", "como", "ficou", "fica", "sera", "quedo"}
)
CONECTORES_DE_VALOR = frozenset({"para", "pra", "to", "como", "ficou", "fica", "sera", "quedo"})
SINAIS_DE_SENHA = frozenset({":", "=", "-", "–", "—", "→", "->", "=>"})
SIMBOLOS_DE_SENHA = "!#$%&*@+?=~^"
PERTO_SENHA = 8
_RE_PALAVRA = re.compile(rf"[^{_ESPACO}]+")
_ABRE_A_PALAVRA = "<(\"'«["
_FIM_DA_PALAVRA = ".,;)!?\"'»:>]"

# ── Links ──────────────────────────────────────────────────────────────────
LINK_REMOVIDO = "[link de acesso removido]"
_RE_URL = re.compile(rf"(?i:https?://|www\.)[^{_ESPACO}<>\"'()\[\]{{}}]+")
# Na URL (caminho e parâmetros, sem acento e minúscula, em pedaços de letras e
# números): é link que dá acesso. O pedaço COMEÇA com um destes…
PREFIXOS_LINK_DE_ACESSO = (
    "reset",
    "password",
    "passwd",
    "redefin",
    "recuper",
    "verify",
    "verific",
    "confirm",
    "token",
    "magic",
    "login",
    "logon",
    "signin",
    "unlock",
    "desbloq",
    "validat",
    "activat",
    "authent",
    "authoriz",
    "oauth",
    "onetime",
    "session",
    "sessao",
    "invite",
    "convite",
    "ativac",
    "ativar",
)
# …ou É um destes ("acessorios" não é "acesso").
PEDACOS_LINK_DE_ACESSO = frozenset(
    {"senha", "auth", "otp", "2fa", "mfa", "sso", "acesso", "access", "valida", "validar", "sign"}
)
# Parâmetro com nome de segredo (o valor nunca aparece).
PARAMETROS_SECRETOS = frozenset(
    {
        "token",
        "code",
        "codigo",
        "key",
        "chave",
        "auth",
        "sig",
        "signature",
        "hash",
        "otp",
        "session",
        "sid",
        "ticket",
        "secret",
        "pass",
        "pwd",
        "jwt",
        "access",
        "t",
        "k",
    }
)
# Valor opaco longo (no caminho ou num parâmetro): pode ser um token.
_RE_OPACO = re.compile(r"^[A-Za-z0-9_\-.%=+]{24,}$")

BOLINHA = "•"
FORTE = "forte"
FRACO = "fraco"
MASCARADO = "mascarado"


# Sem acento: as MESMAS trocas do `plano` do Rust (o NFKD do Python faria
# outras, e os dois lados têm de ler igual).
_SEM_ACENTO = {
    **dict.fromkeys("áàâãäå", "a"),
    **dict.fromkeys("éèêë", "e"),
    **dict.fromkeys("íìîï", "i"),
    **dict.fromkeys("óòôõö", "o"),
    **dict.fromkeys("úùûü", "u"),
    "ç": "c",
    "ñ": "n",
    **dict.fromkeys("ýÿ", "y"),
}


def _plano(texto: str) -> str:
    """Minúsculo, sem acento (e sem o acento solto U+0300–U+036F) — igual ao `plano` do Rust."""
    return "".join(
        _SEM_ACENTO.get(c, c)
        for ch in texto or ""
        for c in ch.lower()
        if not "\u0300" <= c <= "\u036f"
    )


def _tem(plano: str, gatilho: str) -> bool:
    # Os curtos ("otp", "2fa") só como palavra inteira ("hotpot" não é código).
    if len(gatilho) <= 4:
        return _tem_palavra(plano, gatilho)
    return gatilho in plano


def _letra_de_palavra(c: str) -> bool:
    return c.isalnum() or c == "_"


def _tem_palavra(plano: str, palavra: str) -> bool:
    """A palavra (ou frase) inteira no texto plano: sem letra/número/_ colado dos lados."""
    de = 0
    while (ini := plano.find(palavra, de)) >= 0:
        fim = ini + len(palavra)
        antes = plano[ini - 1] if ini > 0 else ""
        depois = plano[fim] if fim < len(plano) else ""
        if not (antes and _letra_de_palavra(antes)) and not (depois and _letra_de_palavra(depois)):
            return True
        de = ini + 1
    return False


def _sem_links(texto: str | None) -> str:
    """O texto sem as URLs (o que está DENTRO do link não é palavra nem código do e-mail)."""
    return _RE_URL.sub(" ", texto or "")


# ── Os pedaços do texto e os códigos soltos ───────────────────────────────


@dataclass(frozen=True)
class _Pedaco:
    ini: int
    fim: int
    texto: str
    plano: str


@dataclass(frozen=True)
class _Codigo:
    tipo: str  # FORTE, FRACO ou MASCARADO
    primeiro: int  # índice do 1º pedaço
    ultimo: int  # índice do último pedaço
    trechos: tuple[tuple[int, int], ...]  # o que vira "•" (posições no texto)


def _de_pedaco(c: str) -> bool:
    return c.isalnum() or c in "_-" or c == BOLINHA


def _pedacos(texto: str) -> list[_Pedaco]:
    saida: list[_Pedaco] = []
    ini: int | None = None
    for i, c in enumerate(texto):
        if _de_pedaco(c):
            if ini is None:
                ini = i
        elif ini is not None:
            saida.append(_Pedaco(ini, i, texto[ini:i], _plano(texto[ini:i])))
            ini = None
    if ini is not None:
        saida.append(_Pedaco(ini, len(texto), texto[ini:], _plano(texto[ini:])))
    return saida


def _digitos(t: str) -> bool:
    return bool(t) and all("0" <= c <= "9" for c in t)


def _bolinhas(t: str) -> bool:
    return bool(t) and all(c == BOLINHA for c in t)


def _ascii_alnum(c: str) -> bool:
    return "0" <= c <= "9" or "a" <= c <= "z" or "A" <= c <= "Z"


def _alnum_ascii(t: str) -> bool:
    return bool(t) and all(_ascii_alnum(c) for c in t)


_FORMAS_PARTIDAS = ((3, 3), (2, 2, 2), (4, 4))


def _forma_de_codigo(forma: tuple[int, ...]) -> bool:
    """'482 913', '48 29 13', '4829 1300' ou um dígito por vez ('4 8 2 9 1 3')."""
    return forma in _FORMAS_PARTIDAS or (4 <= len(forma) <= 8 and all(n == 1 for n in forma))


def _prefixo(t: str) -> tuple[int, str] | None:
    """'G-482913' / 'G-••••••' → (onde começa o número, o número); senão None."""
    letras, hifen, resto = t.partition("-")
    if (
        hifen
        and 1 <= len(letras) <= 3
        and all("A" <= c <= "Z" for c in letras)
        and 4 <= len(resto) <= 8
        and (_digitos(resto) or _bolinhas(resto))
    ):
        return len(letras) + 1, resto
    return None


def _partido_no_pedaco(t: str) -> str | None:
    """'123-456', '48-29-13', '1234-5678' (ou de bolinhas) → FORTE/MASCARADO; senão None."""
    partes = t.split("-")
    if len(partes) < 2 or tuple(len(p) for p in partes) not in _FORMAS_PARTIDAS:
        return None
    if all(_digitos(p) for p in partes):
        return FORTE
    if all(_bolinhas(p) for p in partes):
        return MASCARADO
    return None


def _maiusculo_ou_digito(c: str) -> bool:
    return "0" <= c <= "9" or "A" <= c <= "Z"


def _letras_com_hifen(t: str) -> bool:
    """'QXF-7KT', 'AB1-C2D': duas partes de 2 a 4 maiúsculas/dígitos, com letra e dígito."""
    partes = t.split("-")
    return (
        len(partes) == 2
        and all(2 <= len(p) <= 4 and all(_maiusculo_ou_digito(c) for c in p) for p in partes)
        and any("A" <= c <= "Z" for c in t)
        and any("0" <= c <= "9" for c in t)
    )


def _tipo_do_pedaco(t: str) -> str | None:
    if _bolinhas(t):
        return MASCARADO if 4 <= len(t) <= 8 else None
    partido = _partido_no_pedaco(t)
    if partido:
        return partido
    if _letras_com_hifen(t):
        return FRACO
    n = len(t)
    if n == 5 and _alnum_ascii(t) and not _digitos(t):
        # "F4K2T": o de 5 com letra e dígito (fraco: só perto da palavra).
        if any("0" <= c <= "9" for c in t):
            return FRACO
        return None
    if _digitos(t):
        if not 4 <= n <= 8:
            return None
        return FRACO if n == 4 and 1900 <= int(t) <= 2099 else FORTE
    if not (6 <= n <= 8 and _alnum_ascii(t)):
        return None
    tem_digito = any("0" <= c <= "9" for c in t)
    tem_maiuscula = any("A" <= c <= "Z" for c in t)
    tem_minuscula = any("a" <= c <= "z" for c in t)
    if not tem_digito or not (tem_maiuscula or tem_minuscula):
        return None
    return FRACO if tem_minuscula else FORTE


def _contexto_exclui(texto: str, ps: list[_Pedaco], primeiro: int, ultimo: int) -> bool:
    """O número é valor, data, caminho, telefone, pedido, CEP…? (então não é código)"""
    ini, fim = ps[primeiro].ini, ps[ultimo].fim
    antes = texto[ini - 1] if ini > 0 else ""
    depois = texto[fim] if fim < len(texto) else ""
    if antes in ("/", "#", "@") or depois in ("/", "%", "@"):
        return True  # caminho, data, "#1234", "15%" e o endereço ("21max@tuta.com")
    if depois == "," and fim + 1 < len(texto) and "0" <= texto[fim + 1] <= "9":
        return True
    j = ini
    if j > 0 and texto[j - 1] == " ":
        j -= 1
    if j > 0 and texto[j - 1] in ("$", "€", "£", ")"):
        return True
    if _depois_de_pedido(ps, primeiro):
        return True
    return ultimo + 1 < len(ps) and ps[ultimo + 1].plano in NAO_E_CODIGO_ANTES_DE


def _depois_de_pedido(ps: list[_Pedaco], primeiro: int) -> bool:
    """Antes do número: "pedido", "CEP", "rastreio"… — com até 3 pedaços de
    enchimento no meio ("pedido número", "rastreio, o número", "order number is")."""
    i = primeiro - 1
    for passo in range(4):
        if i < 0:
            return False
        plano = ps[i].plano
        if plano in NAO_E_CODIGO_DEPOIS_DE and (passo == 0 or plano not in TELEFONES):
            return True
        if plano not in ENCHIMENTO_ANTES:
            return False
        i -= 1
    return False


def _junto(texto: str, ps: list[_Pedaco], a: int, b: int) -> bool:
    """Os pedaços a e b (= a+1) estão separados por UM espaço ou ponto?"""
    return texto[ps[a].fim : ps[b].ini] in (" ", ".")


def _codigos(texto: str, ps: list[_Pedaco]) -> list[_Codigo]:
    saida: list[_Codigo] = []
    k = 0
    while k < len(ps):
        t = ps[k].texto
        # Um grupo de números (ou de bolinhas) separados por UM espaço/ponto.
        if _digitos(t) or _bolinhas(t):
            fim = k
            while (
                fim + 1 < len(ps)
                and _junto(texto, ps, fim, fim + 1)
                and (_digitos(ps[fim + 1].texto) or _bolinhas(ps[fim + 1].texto))
            ):
                fim += 1
            if fim > k:
                grupo = ps[k : fim + 1]
                forma = tuple(len(p.texto) for p in grupo)
                # Parte de um número maior ("123.456.789-09"): nenhum é código.
                colado_antes = (
                    k > 0 and _junto(texto, ps, k - 1, k) and _digitos(ps[k - 1].texto[:1])
                )
                colado_depois = (
                    fim + 1 < len(ps)
                    and _junto(texto, ps, fim, fim + 1)
                    and _digitos(ps[fim + 1].texto[:1])
                )
                if _forma_de_codigo(forma) and not colado_antes and not colado_depois:
                    trechos = tuple((p.ini, p.fim) for p in grupo)
                    if all(_bolinhas(p.texto) for p in grupo):
                        saida.append(_Codigo(MASCARADO, k, fim, trechos))
                    elif all(_digitos(p.texto) for p in grupo) and not _contexto_exclui(
                        texto, ps, k, fim
                    ):
                        saida.append(_Codigo(FORTE, k, fim, trechos))
                k = fim + 1
                continue
        prefixo = _prefixo(t)
        if prefixo is not None:
            onde, numero = prefixo
            do_prefixo = MASCARADO if _bolinhas(numero) else FORTE
            saida.append(_Codigo(do_prefixo, k, k, ((ps[k].ini + onde, ps[k].fim),)))
            k += 1
            continue
        tipo = _tipo_do_pedaco(t)
        if tipo is not None and (tipo == MASCARADO or not _contexto_exclui(texto, ps, k, k)):
            saida.append(_Codigo(tipo, k, k, ((ps[k].ini, ps[k].fim),)))
        k += 1
    return saida


def _neutro(ps: list[_Pedaco], i: int) -> bool:
    """O "código"/"code" de rastreio, do produto, postal…: não é de acesso."""
    if ps[i].plano not in ("codigo", "codigos", "code", "codes"):
        return False
    if i > 0 and ps[i - 1].plano in NEUTROS_ANTES:
        return True
    seguintes = tuple(p.plano for p in ps[i + 1 : i + 3])
    return any(seguintes[: len(n)] == n for n in NEUTROS_DEPOIS)


def _palavras_de_codigo(ps: list[_Pedaco]) -> list[int]:
    return [i for i, p in enumerate(ps) if p.plano in PALAVRAS_DE_CODIGO and not _neutro(ps, i)]


def _perto(c: _Codigo, palavras: list[int]) -> bool:
    return any(c.primeiro - PERTO <= i <= c.ultimo + PERTO for i in palavras)


def _tamanho(c: _Codigo) -> int:
    return sum(fim - ini for ini, fim in c.trechos)


def _codigo_unico(ps: list[_Pedaco], c: _Codigo) -> bool:
    """O código FORTE (ou já mascarado) de 5+ com uma palavra de CÓDIGO ÚNICO
    perto ("digite", "Enter", "confirme", "vence"…; "número", "chave" bem perto)."""
    if c.tipo == FRACO or _tamanho(c) < 5:
        return False
    for i in range(max(0, c.primeiro - PERTO_OTP), min(len(ps), c.ultimo + PERTO_OTP + 1)):
        if c.primeiro <= i <= c.ultimo:
            continue
        plano = ps[i].plano
        if plano in PALAVRAS_DE_OTP:
            return True
        if plano in PALAVRAS_DE_OTP_PERTO and c.primeiro - PERTO <= i <= c.ultimo + PERTO:
            return True
    return False


@dataclass(frozen=True)
class _Leitura:
    """Um texto lido: a palavra de acesso, os códigos que contam e o código único."""

    palavra: bool
    contam: tuple[_Codigo, ...]  # FORTE e MASCARADO em qualquer lugar; FRACO perto da palavra
    unico: bool  # um código com palavra de código único perto ("digite este número 731 604")


def _ler(texto: str) -> _Leitura:
    ps = _pedacos(texto)
    palavras = _palavras_de_codigo(ps)
    todos = _codigos(texto, ps)
    contam = tuple(c for c in todos if c.tipo != FRACO or _perto(c, palavras))
    return _Leitura(bool(palavras), contam, any(_codigo_unico(ps, c) for c in todos))


def _link_com_prazo(*textos: str | None) -> bool:
    """Link e prazo juntos ("Click here … (expires in 15 minutes)", "con este
    enlace … Caduca en 15 minutos"), num e-mail com link."""
    if not any(_RE_URL.search(t or "") for t in textos):
        return False
    for t in textos:
        ps = _pedacos(_sem_links(t))
        links = [i for i, p in enumerate(ps) if p.plano in PALAVRAS_DE_LINK]
        prazos = [i for i, p in enumerate(ps) if p.plano in PALAVRAS_DE_PRAZO]
        if any(abs(a - b) <= PERTO_LINK for a in links for b in prazos):
            return True
    return False


def _fala_de_entrada(plano: str) -> bool:
    for frase in _ENTRAR_EM_CONTATO:
        plano = plano.replace(frase, " ")
    return any(_tem_palavra(plano, p) for p in PALAVRAS_DE_ENTRADA)


def _planos(*textos: str | None) -> tuple[str, str]:
    """(o plano do texto inteiro, o plano sem as URLs)."""
    return (
        " ".join(_plano(t or "") for t in textos),
        " ".join(_plano(_sem_links(t)) for t in textos),
    )


def fala_de_acesso(*textos: str | None) -> bool:
    """O e-mail fala de senha, de código ou de entrar na conta? Então TODOS os links saem."""
    plano, sem_links = _planos(*textos)
    return (
        any(_tem(plano, g) for g in GATILHOS_SEGURANCA)
        or any(_tem_palavra(plano, p) for p in PALAVRAS_DE_SENHA)
        or _fala_de_entrada(sem_links)
        or _RE_CODIGO_PARA_ENTRAR.search(sem_links) is not None
        or any(_ler(_sem_links(t)).palavra for t in textos)
        or _link_com_prazo(*textos)
    )


def fala_de_codigo(*textos: str | None) -> bool:
    """O e-mail fala de código (pelo assunto ou pelo texto, ou tem um código
    único: "digite este número 731 604")? Os códigos são mascarados."""
    plano, sem_links = _planos(*textos)
    if any(_tem(plano, g) for g in GATILHOS) or _RE_CODIGO_PARA_ENTRAR.search(sem_links):
        return True
    leituras = [_ler(_sem_links(t)) for t in textos]
    return any(leitura.palavra or leitura.unico for leitura in leituras)


def e_de_codigo_de_acesso(*textos: str | None) -> bool:
    """O e-mail fala de CÓDIGO DE ACESSO ("código de verificação", "sign-in
    code", "use o código … para entrar")? Então até o código fraco some."""
    plano, sem_links = _planos(*textos)
    return _RE_CODIGO_PARA_ENTRAR.search(sem_links) is not None or any(
        _tem(plano, g) for g in GATILHOS_CODIGO_DE_ACESSO
    )


def e_de_seguranca(*textos: str | None, de_pessoa: bool = False) -> bool:
    """O e-mail é de SEGURANÇA (código de acesso, senha, novo login, confirmar
    e-mail, link de entrada)? Então nunca vira conversa (estado `seguranca`).

    Regra ESTRITA (o padrão): uma frase da lista; ou um código solto (forte em
    qualquer lugar, fraco perto da palavra) num e-mail com uma palavra de
    acesso ("código", "token", "PIN", "senha"…) ou de entrada ("entrar",
    "login", "sign in"…); ou um código único ("digite este número 731 604",
    "Enter 604 381 … to approve"); ou uma senha escrita (com separador ou em
    frase: "a senha ficou X"); ou um link com prazo.

    `de_pessoa` (o comprador, o formulário do site): "esqueci minha senha" de
    um cliente é atendimento — só esconde o CÓDIGO de acesso de verdade (o
    gatilho e o código juntos, ou o assunto de código de acesso em inglês:
    "Slack confirmation code: QXF-7KT"). O link de acesso, a senha escrita e
    o código somem do texto do mesmo jeito (`proteger`).

    O 1º texto é o ASSUNTO (a senha em frase com o termo só no assunto)."""
    plano, sem_links = _planos(*textos)
    de_acesso = e_de_codigo_de_acesso(*textos)
    if de_pessoa:
        # O código de acesso de verdade: o gatilho e um código (até o fraco) juntos.
        if de_acesso and any(_codigos(t, _pedacos(t)) for t in map(_sem_links, textos)):
            return True
        assunto = _plano((textos[0] or "") if textos else "")
        return any(_tem(assunto, g) for g in GATILHOS_DE_SERVICO)
    if de_acesso or any(_tem(plano, g) for g in GATILHOS_SEGURANCA):
        return True
    leituras = [_ler(_sem_links(t)) for t in textos]
    tem_codigo = any(leitura.contam for leitura in leituras)
    palavra = any(leitura.palavra for leitura in leituras)
    if tem_codigo and (palavra or _fala_de_entrada(sem_links)):
        return True
    if any(leitura.unico for leitura in leituras) or _link_com_prazo(*textos):
        return True
    if any(
        _cara_de_senha(v) or len(v) >= 6 for t in textos for v in _senhas_escritas(_sem_links(t))
    ):
        return True
    no_assunto = len(textos) > 1 and _fala_de_senha(textos[0])
    return any(
        _senhas_em_frase(_sem_links(t), termo_no_assunto=no_assunto, com_mascaradas=True)
        for t in textos
    )


def mascarar(texto: str | None, *, todos_os_fracos: bool = False) -> str:
    """Troca os códigos do texto por "•" (mesmo tamanho; o separador fica). Sem código, igual.

    O FORTE some sempre; o FRACO ("k7x9q2", "2019") só perto de uma palavra de
    acesso — ou em qualquer lugar com `todos_os_fracos` (o e-mail é de código
    de acesso: "Your verification code" no assunto e "k7x9q2" sozinho no
    texto); o já mascarado fica como está."""
    if not texto:
        return texto or ""
    ps = _pedacos(texto)
    palavras = _palavras_de_codigo(ps)
    trechos = sorted(
        trecho
        for c in _codigos(texto, ps)
        if c.tipo == FORTE or (c.tipo == FRACO and (todos_os_fracos or _perto(c, palavras)))
        for trecho in c.trechos
    )
    if not trechos:
        return texto
    partes: list[str] = []
    ultimo = 0
    for ini, fim in trechos:
        partes.append(texto[ultimo:ini])
        partes.append("".join(BOLINHA if _ascii_alnum(c) else c for c in texto[ini:fim]))
        ultimo = fim
    partes.append(texto[ultimo:])
    return "".join(partes)


def _cara_de_senha(valor: str) -> bool:
    return (
        any("0" <= c <= "9" for c in valor)
        or any(c.isupper() for c in valor[1:])
        or any(not c.isalnum() for c in valor)
    )


def _valor_da_senha(m: re.Match, *, solta: bool) -> tuple[str, str, str] | None:
    """(o que abre, o valor, o que sobra no fim) se o achado é mesmo uma senha; senão None."""
    bruto = m.group(5)
    sem_abre = bruto.lstrip(_ABRE_O_VALOR)
    abre = bruto[: len(bruto) - len(sem_abre)]
    # Nunca um link nem o "[link de acesso removido]" que já está lá (nem o
    # "<url>" que o HTML vira: "Redefinir senha <[link de acesso removido]>").
    limpo = sem_abre.lower()
    if limpo.startswith("[") or limpo.startswith(("http://", "https://", "www.")):
        return None
    valor = sem_abre.rstrip(_FIM_DO_VALOR)
    sobra = sem_abre[len(valor) :]
    if not valor or _e_email(valor):
        return None
    if solta:
        if len(valor) < 4 or not _cara_de_senha(valor):
            return None
    else:
        no_meio = [_plano(p) for p in _RE_LETRAS.findall(m.group(3))]
        if any(p in NAO_E_SENHA_NO_MEIO for p in no_meio):
            return None
        com_dois_pontos = any(c in m.group(4) for c in ":=")
        if com_dois_pontos:
            if len(valor) < 3:
                return None
        elif len(valor) < 4 or not _cara_de_senha(valor):
            return None
        if no_meio and _digitos(valor) and len(valor) >= 9:
            return None  # "senha do app 2000012345678901": é número de outra coisa
    if solta and _digitos(valor) and len(valor) >= 9:
        return None
    return abre, valor, sobra


def _senhas_escritas(texto: str) -> list[str]:
    """Os valores de senha escritos no texto (os dois jeitos)."""
    achados: list[str] = []
    for regex, solta in ((_RE_SENHA, False), (_RE_SENHA_SOLTA, True)):
        for m in regex.finditer(texto):
            ok = _valor_da_senha(m, solta=solta)
            if ok:
                achados.append(ok[1])
    return achados


def _e_email(valor: str) -> bool:
    """'joao.silva@gmail.com' é endereço, não senha ('Alfa@2026x' é senha)."""
    _usuario, arroba, dominio = valor.partition("@")
    return bool(arroba) and "." in dominio


@dataclass(frozen=True)
class _Palavra:
    """O que fica entre espaços: `nucleo` sem a pontuação das pontas (posições no texto)."""

    ini: int
    fim: int
    bruto: str
    nucleo: str
    plano: str


def _palavras(texto: str) -> list[_Palavra]:
    saida: list[_Palavra] = []
    for m in _RE_PALAVRA.finditer(texto):
        bruto = m.group(0)
        sem_abre = bruto.lstrip(_ABRE_A_PALAVRA)
        nucleo = sem_abre.rstrip(_FIM_DA_PALAVRA)
        ini = m.start() + len(bruto) - len(sem_abre)
        saida.append(_Palavra(ini, ini + len(nucleo), bruto, nucleo, _plano(nucleo)))
    return saida


def _fala_de_senha(texto: str | None) -> bool:
    """O texto (o assunto) tem o termo ("senha", "password", "contraseña", "clave")?"""
    return any(p.plano in TERMOS_DE_SENHA for p in _pedacos(texto or ""))


def _forte_de_senha(valor: str) -> bool:
    """Cara FORTE de senha: letra e dígito, e símbolo ou maiúscula com minúscula
    ("Xp7!kL2wQz", "Alfa@2026uranyx", "Kx81mq2z"); a já mascarada ("••••") também.
    Nunca e-mail, link, data ou caminho."""
    if _bolinhas(valor):
        return len(valor) >= 4
    if not 6 <= len(valor) <= 64 or "/" in valor or _e_email(valor):
        return False
    if valor.lower().startswith(("http://", "https://", "www.")):
        return False
    tem_letra = any(_ascii_alnum(c) and not "0" <= c <= "9" for c in valor)
    tem_digito = any("0" <= c <= "9" for c in valor)
    if not (tem_letra and tem_digito):
        return False
    return any(c in SIMBOLOS_DE_SENHA for c in valor) or (
        any("A" <= c <= "Z" for c in valor) and any("a" <= c <= "z" for c in valor)
    )


def _comum_de_senha(valor: str) -> bool:
    """Cara de senha comum (depois de "para", "ficou"…): 4+, dígito, maiúscula no
    meio ou símbolo — nunca o número longo (pedido), e-mail ou link."""
    if _bolinhas(valor):
        return len(valor) >= 4
    if not 4 <= len(valor) <= 64 or "/" in valor or _e_email(valor):
        return False
    if valor.lower().startswith(("http://", "https://", "www.")):
        return False
    if _digitos(valor) and len(valor) >= 9:
        return False
    return _cara_de_senha(valor)


def _conector(p: _Palavra) -> bool:
    return (
        p.plano in CONECTORES_DE_SENHA or p.bruto in SINAIS_DE_SENHA or p.bruto.endswith((":", "="))
    )


def _senhas_em_frase(texto: str, *, termo_no_assunto: bool, com_mascaradas: bool) -> list[_Palavra]:
    """As senhas escritas em FRASE (as regras a, b e c do `TERMOS_DE_SENHA`)."""
    ps = _palavras(texto)
    achadas: list[_Palavra] = []
    for j, p in enumerate(ps):
        if not p.nucleo or (_bolinhas(p.nucleo) and not com_mascaradas):
            continue
        anterior = ps[j - 1] if j > 0 else None
        if anterior is not None and anterior.plano in _ANTES_NAO_E_SENHA:
            continue
        termo_perto = any(x.plano in TERMOS_DE_SENHA for x in ps[max(0, j - PERTO_SENHA) : j])
        forte = _forte_de_senha(p.nucleo)
        if forte and termo_perto:
            achadas.append(p)
        elif forte and termo_no_assunto and anterior is not None and _conector(anterior):
            achadas.append(p)
        elif (
            anterior is not None
            and anterior.plano in CONECTORES_DE_VALOR
            and any(x.plano in TERMOS_DE_SENHA for x in ps[max(0, j - PERTO_SENHA) : j - 1])
            and _comum_de_senha(p.nucleo)
        ):
            achadas.append(p)
    return achadas


def mascarar_senha(texto: str | None, *, termo_no_assunto: bool = False) -> tuple[str, int]:
    """O VALOR da senha escrita no texto vira "•" → (texto, quantos). Vale em todo e-mail.

    `termo_no_assunto`: o assunto fala de senha ("senha nova do ML") — a de
    cara forte logo depois de um conector também some ("pra MlBarb0sa#26")."""
    if not texto:
        return texto or "", 0
    trocados = 0

    def _troca(m: re.Match, *, solta: bool) -> str:
        nonlocal trocados
        ok = _valor_da_senha(m, solta=solta)
        if ok is None:
            return m.group(0)
        abre, valor, sobra = ok
        trocados += 1
        return (
            f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{abre}{BOLINHA * len(valor)}{sobra}"
        )

    saida = _RE_SENHA.sub(lambda m: _troca(m, solta=False), texto)
    saida = _RE_SENHA_SOLTA.sub(lambda m: _troca(m, solta=True), saida)
    achadas = _senhas_em_frase(saida, termo_no_assunto=termo_no_assunto, com_mascaradas=False)
    if not achadas:
        return saida, trocados
    partes: list[str] = []
    ultimo = 0
    for p in achadas:
        partes.append(saida[ultimo : p.ini])
        partes.append(BOLINHA * len(p.nucleo))
        ultimo = p.fim
    partes.append(saida[ultimo:])
    return "".join(partes), trocados + len(achadas)


def link_de_acesso(url: str) -> bool:
    """A URL pode dar acesso a uma conta (login, senha, confirmação, token)? PURA."""
    # Só o esquema do COMEÇO conta ("www.x/auth!https://y" é um link só, como no Mac).
    bruto = url if url.lower().startswith(("http://", "https://")) else f"https://{url}"
    try:
        partes = urlsplit(bruto)
    except ValueError:
        return True  # URL que nem se lê: na dúvida, some
    pedacos = re.findall(
        r"[a-z0-9]+",
        _plano(f"{partes.path or ''} {partes.query or ''} {partes.fragment or ''}"),
    )
    for pedaco in pedacos:
        if pedaco in PEDACOS_LINK_DE_ACESSO or pedaco.startswith(PREFIXOS_LINK_DE_ACESSO):
            return True
    try:
        parametros = parse_qsl(partes.query or "", keep_blank_values=True)
    except ValueError:
        return True
    for nome, valor in parametros:
        if nome.strip(_BRANCOS).lower() in PARAMETROS_SECRETOS or _RE_OPACO.match(valor or ""):
            return True
    return any(_RE_OPACO.match(segmento) for segmento in (partes.path or "").split("/"))


def mascarar_links(texto: str | None, *, todos: bool = False) -> tuple[str, int]:
    """Os links de acesso (com `todos`, TODOS os links) viram "[link de acesso
    removido]" → (texto, quantos)."""
    if not texto:
        return texto or "", 0
    removidos = 0

    def _troca(m: re.Match) -> str:
        nonlocal removidos
        url = m.group(0).rstrip(".,;:!?")
        sobra = m.group(0)[len(url) :]
        if todos or link_de_acesso(url):
            removidos += 1
            return LINK_REMOVIDO + sobra
        return m.group(0)

    return _RE_URL.sub(_troca, texto), removidos


@dataclass(frozen=True)
class Protegido:
    """O assunto e o texto que a equipe pode ver."""

    assunto: str
    texto: str
    codigo_mascarado: bool
    links_removidos: int


def proteger(assunto: str | None, texto: str | None) -> Protegido:
    """Assunto e texto com os links de acesso removidos (TODOS, se o e-mail fala de
    senha, código ou acesso), a senha escrita mascarada e, se o e-mail fala de
    código ou de acesso, os códigos mascarados. É o que vai para a conversa, o
    cartão e a fila."""
    todos = fala_de_acesso(assunto, texto)
    codigo = todos or fala_de_codigo(assunto, texto)
    fracos = codigo and e_de_codigo_de_acesso(assunto, texto)
    no_assunto = _fala_de_senha(assunto)
    assunto_ok, n1 = mascarar_links(assunto or "", todos=todos)
    texto_ok, n2 = mascarar_links(texto or "", todos=todos)
    assunto_ok, s1 = mascarar_senha(assunto_ok, termo_no_assunto=no_assunto)
    texto_ok, s2 = mascarar_senha(texto_ok, termo_no_assunto=no_assunto)
    if codigo:
        assunto_ok = mascarar(assunto_ok, todos_os_fracos=fracos)
        texto_ok = mascarar(texto_ok, todos_os_fracos=fracos)
    return Protegido(
        assunto=assunto_ok,
        texto=texto_ok,
        codigo_mascarado=codigo or bool(s1 + s2),
        links_removidos=n1 + n2,
    )
