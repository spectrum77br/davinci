"""O vocabulário da camada do atendimento sobre a Central de e-mail — um lugar só.

O model (`models/mail_atendimento.py`, os CHECKs da 0387), a ponte, a
resposta, as rotas e os testes falam as mesmas palavras. Veio do lado do
DaVinci do e-mail do Tuta (wt-tuta, 08/10/2026), sem o que a Central do outro
dev já faz (robô, pulso, fila própria de envio, .eml em disco).

DECISÕES PADRÃO (o dono pode mudar depois; cada uma é uma constante daqui):
  • texto só (a regra da Central: nunca HTML), com "Abrir no Tuta" quando
    houver o id do e-mail no Tuta;
  • código de verificação e link de login/senha/confirmação mascarados antes
    de qualquer coisa ir para a conversa; e-mail de segurança/acesso nunca
    vira conversa (`ESTADO_SEGURANCA`);
  • pasta sem plataforma (financeiro, contabilidade, DNP, devoluções, envio,
    envio erro, retido, *avisos) = só contar (`PASTAS_SO_CONTAR`);
  • e-mail das plataformas com API (ML, Shopee, TikTok, Amazon) = aviso, sem
    pendência própria (`PLATAFORMAS_EMAIL_SO_AVISO`);
  • "não responder" = avisa e exige confirmação;
  • Amazon continua pelo Gmail: não se responde Amazon pelo Tuta.
"""

from __future__ import annotations

from datetime import timedelta

# ── Estado do e-mail (`mail_message_meta.estado`) ──────────────────────────
ESTADO_NOVO = "novo"
ESTADO_GRAVADO = "gravado"
ESTADO_SEM_VINCULO = "sem_vinculo"
ESTADO_SEM_LOJA = "sem_loja"
ESTADO_IGNORADO = "ignorado"
ESTADO_INTERNO = "interno"
ESTADO_RESUMO = "resumo"
ESTADO_DUPLICADO = "duplicado"
ESTADO_SEGURANCA = "seguranca"
ESTADO_PRIVADO = "privado"
ESTADO_ERRO = "erro"
# O que a ponte LEVOU à equipe (é contra estes, e só estes, que um e-mail
# repetido vira "duplicado" — nunca contra um privado, ignorado ou de segurança).
ESTADOS_DA_EQUIPE = (ESTADO_GRAVADO, ESTADO_SEM_VINCULO, ESTADO_SEM_LOJA, ESTADO_RESUMO)
# Os que têm o texto mostrado no cartão/fila (para quem pode ver).
ESTADOS_COM_TEXTO = (ESTADO_GRAVADO, ESTADO_SEM_VINCULO, ESTADO_SEM_LOJA, ESTADO_RESUMO)

# As filas da tela (só para quem MEXE no /atendimento).
FILA_SEM_LOJA = "sem_loja"
FILA_SEM_VINCULO = "sem_vinculo"
FILA_SUSPEITO = "suspeito"
FILA_RESUMO = "resumo"
FILA_ERRO = "erro"
FILAS = (FILA_SEM_LOJA, FILA_SEM_VINCULO, FILA_SUSPEITO, FILA_RESUMO, FILA_ERRO)

# Motivos do `ignorado` (código, nunca texto do e-mail).
IGNORADO_PASTA_SO_CONTAR = "pasta_so_contar"
IGNORADO_PASTA_NAO_LER = "pasta_nao_ler"
IGNORADO_AVISO_DO_TUTA = "aviso_do_tuta"
IGNORADO_ALIAS_INTERNO = "alias_interno"
IGNORADO_ENVIADO_SEM_CONVERSA = "enviado_sem_conversa"
IGNORADO_POR_PESSOA = "por_pessoa"
IGNORADO_DEVOLUCAO = "devolucao_sem_conversa"
PRIVADO_NAO_E_DE_LOJA = "nao_e_alias_de_loja"
SEGURANCA_CODIGO = "codigo_ou_acesso"

# ── Finalidade (a primeira palavra da pasta: "vendas ml") ──────────────────
FINALIDADE_VENDAS = "vendas"
FINALIDADE_MENSAGENS = "mensagens"
FINALIDADE_PROBLEMA = "problema"
FINALIDADE_RECLAMACAO = "reclamacao"
FINALIDADE_ENTRADA = "entrada"
FINALIDADE_ENVIADOS = "enviados"
# Pendente (falta responder); problema e reclamação com destaque. `vendas` =
# só histórico, sem pendência (decidido no spec, RF5).
FINALIDADES_PENDENTES = (FINALIDADE_MENSAGENS, FINALIDADE_PROBLEMA, FINALIDADE_RECLAMACAO)
FINALIDADES_DESTAQUE = (FINALIDADE_PROBLEMA, FINALIDADE_RECLAMACAO)
ROTULO_FINALIDADE = {
    FINALIDADE_VENDAS: "vendas",
    FINALIDADE_MENSAGENS: "mensagens",
    FINALIDADE_PROBLEMA: "problema",
    FINALIDADE_RECLAMACAO: "reclamação",
    FINALIDADE_ENTRADA: "Entrada",
    FINALIDADE_ENVIADOS: "Enviados",
}
# Pastas que só se CONTAM até o dono liberar (decisão padrão de 08/10/2026),
# pela primeira palavra do nome (sem acento): mesmo com palavra de plataforma
# no fim ("devolucoes ml") o corpo não entra no /atendimento.
PASTAS_SO_CONTAR = frozenset(
    {
        "financeiro",
        "contabilidade",
        "contabil",
        "dnp",
        "devolucoes",
        "devolucao",
        "envio",
        "envios",
        "retido",
        "retidos",
        "avisos",
        "aviso",
    }
)

# A plataforma "site" é a das caixas das marcas (RF6: sac@, atacado@, duvidas@).
PLATAFORMA_SITE = "site"
PLATAFORMAS_PASTA = (
    "ml",
    "shopee",
    "amazon",
    "tiktok",
    "temu",
    "magalu",
    "aliexpress",
    "shein",
    PLATAFORMA_SITE,
)
# E-mail de PLATAFORMA que tem API (decisão padrão de 08/10): o aviso dela
# vai para a conversa da API do pedido (sem pendência própria); sem a
# conversa da API, fica na conversa de e-mail da loja, também sem pendência.
# Nas outras (Temu, AliExpress, Magalu, Shein, sites) o aviso de
# mensagens/problema/reclamação vira pendência.
PLATAFORMAS_EMAIL_SO_AVISO = frozenset({"ml", "shopee", "tiktok", "amazon"})

# ── RF6: as caixas dos sites e o tipo do chamado ───────────────────────────
TIPO_SAC = "sac"
TIPO_ATACADO = "atacado"
TIPO_DUVIDAS = "duvidas"
ROTULO_TIPO_CAIXA = {
    TIPO_SAC: "SAC",
    TIPO_ATACADO: "Atacado",
    TIPO_DUVIDAS: "Dúvidas e sugestões",
}

# Os alertas do CHAMADO (RF6): ficam no cartão do e-mail (`meta.alertas`) e,
# somados, na conversa (`dados.mail.alertas`) — a faixa do chamado mostra.
ALERTA_SEM_PROTOCOLO = "sem_protocolo"
ALERTA_PROTOCOLO_FORMATO = "protocolo_formato"
ALERTA_PROTOCOLO_INVALIDO = "protocolo_invalido"
ALERTA_PROTOCOLO_OUTRA_MARCA = "protocolo_outra_marca"
ALERTA_TIPO_X_CAIXA = "tipo_x_caixa"
ALERTA_PROTOCOLO_REPETIDO = "protocolo_repetido"
# O e-mail que entrou no chamado (pelo protocolo ou pelo fio) veio de um
# endereço que NÃO é o da cliente do chamado: confira antes de responder.
ALERTA_OUTRO_REMETENTE = "outro_remetente"
ROTULO_ALERTA_DO_CHAMADO = {
    ALERTA_SEM_PROTOCOLO: "SEM PROTOCOLO: o formulário veio sem o número do site",
    ALERTA_PROTOCOLO_FORMATO: (
        "Protocolo fora do formato (marca + tipo + -AA-NNNN, ex.: US-26-0001): avise o time do site"
    ),
    ALERTA_PROTOCOLO_INVALIDO: "O protocolo não existe (a Locagil não tem Atacado)",
    ALERTA_PROTOCOLO_OUTRA_MARCA: "A marca do protocolo não é a da caixa que recebeu",
    ALERTA_TIPO_X_CAIXA: "O tipo do protocolo não é o da caixa que recebeu (vale o protocolo)",
    ALERTA_PROTOCOLO_REPETIDO: (
        "Protocolo repetido: o mesmo número já veio no formulário de OUTRA cliente "
        "(cada uma tem a sua conversa)"
    ),
    ALERTA_OUTRO_REMETENTE: (
        "E-mail de OUTRO endereço neste chamado (não é o e-mail da cliente do formulário): "
        "confira se é a mesma pessoa antes de responder"
    ),
}
ALERTAS_DO_CHAMADO = frozenset(ROTULO_ALERTA_DO_CHAMADO)

# O status do chamado na faixa (RF6), derivado da conversa.
STATUS_CHAMADO_ABERTO = "aberto"
STATUS_CHAMADO_AGUARDANDO = "aguardando_cliente"
STATUS_CHAMADO_RESOLVIDO = "resolvido"
ROTULO_STATUS_CHAMADO = {
    STATUS_CHAMADO_ABERTO: "Aberto",
    STATUS_CHAMADO_AGUARDANDO: "Aguardando cliente",
    STATUS_CHAMADO_RESOLVIDO: "Resolvido",
}

# ── Por que o e-mail ficou SEM LOJA (`motivo`) ─────────────────────────────
SEM_LOJA_ALIAS_SEM_CADASTRO = "alias_sem_cadastro"
SEM_LOJA_AMBIGUO = "ambiguo"
SEM_LOJA_SEM_ALIAS = "sem_alias"
SEM_LOJA_ENTRADA = "entrada_sem_plataforma"
SEM_LOJA_SEM_INTEGRACAO = "loja_sem_integracao"
SEM_LOJA_ENCAMINHADO = "encaminhado"
SEM_LOJA_MARCA_AMBIGUA = "marca_ambigua"
MOTIVOS_SEM_LOJA = {
    SEM_LOJA_ALIAS_SEM_CADASTRO: "o alias que recebeu não está no cadastro de nenhuma loja "
    "desta plataforma (Cadastros › Lojas, campo e-mail)",
    SEM_LOJA_AMBIGUO: "o alias que recebeu está em mais de uma loja desta plataforma: "
    "escolha a loja",
    SEM_LOJA_SEM_ALIAS: "nenhum endereço nosso no Para/Cc (cópia oculta ou lista): escolha a loja",
    SEM_LOJA_ENTRADA: "chegou na Entrada sem regra de pasta e o alias não é de uma loja só: "
    "escolha a loja (a sugestão pelo remetente precisa ser confirmada)",
    SEM_LOJA_SEM_INTEGRACAO: "a loja do alias não está ligada à integração "
    "(Cadastros › Lojas): ligar a loja à integração",
    SEM_LOJA_ENCAMINHADO: "chegou encaminhado de um endereço que não é alias da caixa: "
    "a resposta fica bloqueada até decisão",
    SEM_LOJA_MARCA_AMBIGUA: "o domínio do endereço está em mais de uma marca (Cadastros › "
    "Marcas) e nem a pasta nem o protocolo dizem qual: escolha a marca",
}
# A loja SEM integração (Temu, AliExpress, Magalu, Shein, a ficha que não casa
# com nenhuma integração): o e-mail entra na conversa da FICHA (integração
# nula, `dados.mail.store_info_id`), visível como o site (só quem vê tudo).
# O aviso fica no cartão, sem travar nada.
ALERTA_LOJA_SEM_INTEGRACAO = (
    "a loja não está ligada a uma integração: o pedido é procurado só no espelho do Bling"
)

# As CAIXAS DE SITE (RF6) pela parte antes do @ num domínio de marca: só
# estas viram chamado do site (crítica de 08/10: gabrieli@7buyers, ouvidoria@
# e qualquer outro endereço do domínio da marca NÃO são site — vão para
# "sem loja", que só quem mexe vê). O tipo do chamado: o do nome; `None` = o
# protocolo (ou a pasta) diz.
LOCAIS_DE_SITE: dict[str, str | None] = {
    "sac": "sac",
    "atacado": "atacado",
    "duvidas": "duvidas",
    "duvida": "duvidas",
    "support": None,
    "suporte": None,
}

# ── Remetente ──────────────────────────────────────────────────────────────
REMETENTE_COMPRADOR = "comprador"
REMETENTE_AVISO = "aviso"
REMETENTE_NOSSO = "nosso"
REMETENTE_TUTA = "tuta"
# Domínios do PRÓPRIO Tuta (aviso da conta, não é cliente).
DOMINIOS_DO_TUTA_SISTEMA = ("tutao.de", "tutanota.de")
# Quem NÃO tem cara de pessoa (crítica pré-subida de 08/10): o aviso de acesso
# de um SERVIÇO numa caixa de site (atacado@, duvidas@ da marca podem ser o
# login do painel do site, do Meta, do Google) segue a regra estrita de
# segurança. Os domínios de serviço (o próprio ou um subdomínio)…
DOMINIOS_DE_SERVICO = frozenset(
    {
        "facebookmail.com",
        "facebook.com",
        "fb.com",
        "meta.com",
        "metamail.com",
        "instagram.com",
        "whatsapp.com",
        "google.com",
        "youtube.com",
        "apple.com",
        "microsoft.com",
        "microsoftonline.com",
        "office.com",
        "office365.com",
        "nuvemshop.com.br",
        "tiendanube.com",
        "lojavirtualnuvem.com.br",
        "shopify.com",
        "shopifyemail.com",
        "myshopify.com",
        "tiktok.com",
        "bytedance.com",
        "kwai.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "paypal.com",
        "paypal.com.br",
        "mercadopago.com",
        "mercadopago.com.br",
        "pagseguro.com.br",
        "pagbank.com.br",
        "stripe.com",
        "bling.com.br",
        "tiny.com.br",
        "olist.com",
        "melhorenvio.com.br",
        "hostinger.com",
        "hostinger.com.br",
        "godaddy.com",
        "registro.br",
        "cloudflare.com",
        "wix.com",
        "canva.com",
        "zoom.us",
        "dropbox.com",
        "github.com",
        "amazonaws.com",
        "amazonses.com",
        "sendgrid.net",
        "mailchimp.com",
        "mandrillapp.com",
        "rdstation.com.br",
        "hubspot.com",
        "hubspotemail.net",
        "zendesk.com",
    }
)
# …e a parte antes do @ (em pedaços: "account-security-noreply" → account,
# security, noreply) que é de máquina, não de pessoa. Pedaço igual a um destes…
LOCAIS_DE_SERVICO = frozenset(
    {
        "security",
        "seguranca",
        "account",
        "accounts",
        "conta",
        "contas",
        "notification",
        "notifications",
        "notificacao",
        "notificacoes",
        "notify",
        "alert",
        "alerts",
        "alerta",
        "alertas",
        "verify",
        "verification",
        "verificacao",
        "auth",
        "login",
        "password",
        "senha",
        "mailer",
        "system",
        "sistema",
        "robot",
        "bot",
        "automatico",
        "automatic",
        "noreply",
        "donotreply",
        "naoresponda",
        "naoresponder",
        "info",
        "news",
        "newsletter",
        "marketing",
    }
)
# …ou que COMEÇA com um destes.
PREFIXOS_LOCAIS_DE_SERVICO = ("secur", "segur", "notif", "alert", "verif", "noreply", "naorespond")

# Endereços INTERNOS da empresa (a parte antes do @): o e-mail que chega só
# para eles nunca vai para a fila "Sem loja" (banco, contador, contratos) —
# fica na caixa inteira, do dono e dos admins (crítica de 08/10).
LOCAIS_INTERNOS = frozenset(
    {
        "adm",
        "admin",
        "administrativo",
        "financeiro",
        "contabilidade",
        "contabil",
        "fiscal",
        "ti",
        "controle",
        "app",
        "rh",
        "compras",
    }
)

# ── O Tuta por dentro (o que o conector v2 manda; o v1 não manda) ─────────
# MailSetKind (src/entities/tutanota/Utils.ts).
KIND_PESSOAL = "0"
KIND_ENTRADA = "1"
KIND_ENVIADOS = "2"
KIND_LIXEIRA = "3"
KIND_ARQUIVO = "4"
KIND_SPAM = "5"
KIND_RASCUNHOS = "6"
KIND_TODOS = "7"
KIND_MARCADOR = "8"
KIND_IMPORTADOS = "9"
KIND_AGENDADOS = "10"
KINDS_SISTEMA = frozenset(
    {
        KIND_ENTRADA,
        KIND_ENVIADOS,
        KIND_LIXEIRA,
        KIND_ARQUIVO,
        KIND_SPAM,
        KIND_RASCUNHOS,
        KIND_TODOS,
        KIND_AGENDADOS,
    }
)
# Pasta do agente v1 (IMAP) → o tipo do Tuta, pelo nome (sem acento,
# minúsculo, a última parte do caminho).
KIND_PELO_NOME = {
    "inbox": KIND_ENTRADA,
    "entrada": KIND_ENTRADA,
    "caixa de entrada": KIND_ENTRADA,
    "sent": KIND_ENVIADOS,
    "sent items": KIND_ENVIADOS,
    "sent messages": KIND_ENVIADOS,
    "sent mail": KIND_ENVIADOS,
    "enviados": KIND_ENVIADOS,
    "itens enviados": KIND_ENVIADOS,
    "trash": KIND_LIXEIRA,
    "deleted": KIND_LIXEIRA,
    "deleted items": KIND_LIXEIRA,
    "deleted messages": KIND_LIXEIRA,
    "lixeira": KIND_LIXEIRA,
    "archive": KIND_ARQUIVO,
    "arquivo": KIND_ARQUIVO,
    "spam": KIND_SPAM,
    "junk": KIND_SPAM,
    "lixo eletronico": KIND_SPAM,
    "drafts": KIND_RASCUNHOS,
    "rascunhos": KIND_RASCUNHOS,
}
# MailAuthenticationStatus: 0 ok, 1 falha forte, 2 falha fraca, 3/4 From inválido/ausente.
AUTH_FALHAS = frozenset({"1", "2", "3", "4"})
# MailPhishingStatus: 1 = suspeito.
PHISHING_SUSPEITO = "1"

# Domínios das contas do Tuta: "16tr" no cadastro casa com "16tr@tuta.com"
# (decisão padrão de 08/10/2026: o domínio que falta é `@tuta.com`); a
# mesma parte antes do @ em OUTRO domínio do Tuta deixa ambíguo.
DOMINIO_TUTA_PADRAO = "tuta.com"
DOMINIOS_TUTA = frozenset(
    {"tuta.com", "tutamail.com", "tuta.io", "tutanota.com", "tutanota.de", "keemail.me"}
)
# "Abrir no Tuta" (o id "lista/elemento" do e-mail): abre E MARCA como lido —
# é a pessoa abrindo, no navegador dela.
LINK_TUTA = "https://app.tuta.com/mail?mail={lista},{elemento}"

# ── A ponte ────────────────────────────────────────────────────────────────
# A versão das regras que decidiram cada e-mail (`regras_versao`). Mudar a
# regra (o roteamento, a máscara) = subir este número; o que já foi decidido
# não é reprocessado sozinho (só sem_loja/erro, por pessoa).
REGRAS_VERSAO = 1
# E-mails por caixa em cada volta (o job roda a cada minuto).
PONTE_POR_VOLTA = 50
# O texto que vai para a mensagem da conversa (o novo, sem a citação).
TEXTO_NA_CONVERSA_MAX = 20_000
# O enviado pelo Tuta que responde a um e-mail com resposta NOSSA na fila:
# espera o recibo (o Enviados pode ser lido antes dele) até este tanto.
ESPERA_RECIBO = timedelta(minutes=30)
# A Amazon (desde 28/09 lida pelo Gmail): o e-mail do Tuta cujo FIO aponta para
# a conversa do Gmail espera a mesma mensagem chegar por lá (liga, nunca grava
# cópia na conversa do Gmail) até este tanto; depois entra numa conversa à parte.
ESPERA_GMAIL_AMAZON = timedelta(hours=6)
# Os que esperam (o recibo, o Gmail) saem da busca da volta, e o e-mail de trás
# é lido na mesma volta; o teto dos que esperam numa volta é este × o limite.
ESPERA_POR_VOLTA = 10
# Mais que isto de pedidos que EXISTEM = resumo (diário, marketing).
MAX_PEDIDOS_POR_EMAIL = 3
# Resumo/marketing da plataforma: só histórico, sem pendência (começo do
# assunto, sem acento e minúsculo).
ASSUNTOS_SEM_PENDENCIA = (
    "resumo",
    "relatorio",
    "newsletter",
    "novidades",
    "promocao",
    "promocoes",
    "campanha",
    "dicas",
    "seu desempenho",
    "sua reputacao",
)

# ── A resposta ─────────────────────────────────────────────────────────────
RESPOSTA_MAX_CARACTERES = 10_000
CITACAO_MAX_LINHAS = 30
CITACAO_MAX_CARACTERES = 2_000
ASSINATURA_LOJA = "Atenciosamente,\n{loja}"
# O status do job da fila dele → a mensagem do /atendimento.
STATUS_DO_JOB = {
    "queued": "enviando",
    "leased": "enviando",
    "sent": "enviada",
    "failed": "falhou",
    "uncertain": "revisar",
}
# Vivo = na fila, ou pego pelo Mac há menos que o LEASE_TIMEOUT da Central
# (15 min; `responder.fila_viva_existe`). O `leased` mais velho que isso é de
# um Mac que sumiu: a mensagem vai para "revisar" e uma pessoa confere.
JOBS_VIVOS = ("queued", "leased")
# O que a pessoa lê quando o job falhou (o código cru fica no `erro`). Os
# códigos do conector v2 (apps/tuta-conector/src/envio.rs) e os da Central.
FRASE_DA_FALHA = {
    "author_access_revoked": "quem respondeu perdeu o acesso à caixa antes do envio",
    "sender_not_authorized": "o endereço de envio saiu da lista da caixa antes do envio",
    "resolved_not_sent": "alguém conferiu no Tuta: não saiu",
    "receipt_timeout": "o Mac não confirmou o envio: confira no Tuta",
    "queued_timeout": "a resposta ficou mais de 2 h na fila sem sair: nada foi enviado "
    "(responda de novo se ainda fizer sentido)",
    "test_mode_recipient": "a caixa voltou ao modo de teste e o destinatário não está na "
    "lista: nada foi enviado",
    "sem_fio": "o e-mail original não tem Message-ID: nada foi enviado",
    "sender_not_active_alias": "o endereço de envio não está ativo na conta do Tuta: nada "
    "foi enviado",
    "destinatario_invalido": "o endereço do cliente não é válido: nada foi enviado",
    "destinatario_tuta": "o cliente usa o Tuta (precisa da cifra do Tuta): responda pelo app "
    "do Tuta",
    "texto_vazio": "a resposta chegou vazia ao Mac: nada foi enviado",
    "rascunho_falhou": "o Tuta não aceitou o rascunho: nada foi enviado",
    "envio_desligado": "o envio da caixa foi desligado antes de sair: nada foi enviado "
    "(o rascunho pode ter ficado no Tuta)",
    "sem_confirmacao": "o Mac não conseguiu confirmar com o DaVinci antes de enviar: nada "
    "foi enviado (o rascunho pode ter ficado no Tuta)",
    "lease_vencido": "o Mac demorou demais para enviar (o DaVinci já ia pedir conferência): "
    "nada foi enviado (o rascunho pode ter ficado no Tuta)",
    "teto_local": "o Mac chegou ao teto de envios: nada foi enviado",
    "tuta_indisponivel": "o Tuta não respondeu no Mac: nada foi enviado",
    "sessao_caiu": "a sessão do Tuta caiu no Mac: nada foi enviado",
    "ja_visto_no_diario": "o Mac já tinha visto este envio antes: confira no Tuta se saiu",
    "envio_sem_confirmacao": "o Tuta não confirmou o envio: confira no Tuta",
    "main_address_not_allowed": "a resposta sairia pelo endereço principal da conta do Tuta (o "
    "de login), que nunca aparece para o cliente: nada foi enviado (responda por um alias)",
    "visibilidade_mudou": "a caixa passou de empresa para privada antes do envio: nada foi "
    "enviado (responda de novo se ainda fizer sentido)",
}
