"""Vocabulário do atendimento unificado — um lugar só.

O model, os adaptadores, a IA, o router e a tela falam as mesmas palavras;
se cada um escrevesse a sua string, um `"pos-venda"` contra `"pos_venda"`
bastaria para uma conversa sumir da fila sem erro nenhum.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── Plataformas e canais ──────────────────────────────────────────────────
# O Instagram NÃO entra aqui: aparece na tela por um adaptador só leitura
# sobre `dm_conversas` e tem robô próprio (services/instagram_dm.py).
#
# `PLATAFORMAS` são as lidas POR API da loja: é a lista que o cron do sync
# (`sync.garantir_canais`/`_canais_da_rodada`) percorre, com o cliente da
# integração. Temu e AliExpress NÃO entram nela de propósito: não têm API de
# chat, e o `TemuClient` da factory não pode ser acionado para ler conversa
# (nem com credencial vazia). Quem precisa da caixa inteira usa
# `PLATAFORMAS_CAIXA`; o que não foi revisto continua ignorando as lojas do
# robô — o erro, se houver, é a loja não aparecer, nunca uma chamada à Temu.
# Magalu (30/09/2026): três APIs do vendedor — perguntas, chat e SAC —, lidas
# pelo `MagaluClient` da integração (`services/atendimento/magalu.py`).
PLATAFORMAS = ("shopee", "ml", "tiktok", "amazon", "magalu")

# Lidas pelo ROBÔ do Mac mini (30/09/2026): um perfil do AdsPower por loja
# com a lista de conversas do Seller Center aberta; o robô só ESCUTA o que a
# página já recebe e repassa ao DaVinci (`services/atendimento/robo.py`). A
# resposta é dada pela pessoa no Seller Center: o envio por aqui fica
# bloqueado (`enviar`), e a IA só sugere.
PLATAFORMAS_ROBO = ("temu", "aliexpress")

# Tudo o que aparece na caixa (lista, resumo, manual da IA, cron da IA).
PLATAFORMAS_CAIXA = PLATAFORMAS + PLATAFORMAS_ROBO

# Lidas por um CANAL EXTERNO (02/10/2026): nem integração de marketplace nem
# robô do Mac mini. O canal é identificado por `atendimento_canais.externo_ref`
# (migration 0362, `services/atendimento/canais_externos.py`):
#   site      — Charlots e Uranyx (PHP próprio na Hostinger): o carrinho
#               abandonado do lojista logado, lido do site servidor a
#               servidor com o MESMO token do estoque (`sites_estoque_tokens`);
#               `externo_ref = "site:<site>"`;
#   instagram — comentários e menções da conta (RF7), pelo token do usuário
#               de sistema do app "DaVinci Publicador"
#               (`redes_sociais_tokens`); `externo_ref =
#               "rede:instagram:<ig_user_id>"`. O Direct continua no adaptador
#               só leitura (`instagram.py`, sobre `dm_conversas`);
#   facebook  — comentários dos posts da Página; `externo_ref =
#               "rede:facebook:<page_id>"`.
# FORA de `PLATAFORMAS_CAIXA` de propósito: o cron do sync, o da IA (que só
# sugere no clique aqui) e o manual não as veem. Nada sai pelo DaVinci por
# elas enquanto o envio estiver desligado (`enviar` não tem adaptador delas:
# recusa como somente leitura).
PLATAFORMA_SITE = "site"
PLATAFORMA_INSTAGRAM = "instagram"
PLATAFORMA_FACEBOOK = "facebook"
PLATAFORMAS_REDE = (PLATAFORMA_INSTAGRAM, PLATAFORMA_FACEBOOK)
PLATAFORMAS_EXTERNAS = (PLATAFORMA_SITE, *PLATAFORMAS_REDE)
# O que o filtro `?plataforma=` da lista aceita (a caixa + as externas; o
# Instagram também traz as DMs do adaptador).
PLATAFORMAS_LISTA = PLATAFORMAS_CAIXA + PLATAFORMAS_EXTERNAS

CANAL_CHAT = "chat"
CANAL_PERGUNTA = "pergunta"
CANAL_POS_VENDA = "pos_venda"
CANAL_EMAIL = "email"
# SAC da Magalu: o protocolo de pós-venda (ticket), com prazo próprio
# (`due_date`) e a Magalu mediando. Não é o "pós-venda" do ML (outra API,
# outro limite, outra regra de bloqueio).
CANAL_SAC = "sac"
# Reclamação/mediação/devolução da plataforma (RF2, 01/10/2026): a conversa
# que nasce da reclamação do ML (as mensagens do comprador e do mediador, o
# "Com Meli"), com `externo_id` = id da reclamação. NÃO entra em
# `CANAIS_POR_PLATAFORMA`: não é uma caixa lida pelo cron do sync
# (`sync.garantir_canais` criaria um canal por conta ML e o adaptador de chat
# tentaria lê-lo) — quem a escreve é `services/atendimento/reclamacoes.py`.
CANAL_RECLAMACAO = "reclamacao"
# Avaliação de venda PENDENTE (RF8, 02/10/2026): a conversa que nasce quando
# uma avaliação fica sem resposta da loja (Shopee) ou com nota 1–3 sem
# tratar (ML), com `externo_id` = id da avaliação na plataforma. Como a
# reclamação, NÃO entra em `CANAIS_POR_PLATAFORMA` (não é caixa lida pelo
# sync): quem a escreve é `services/atendimento/avaliacoes.py`. A resposta
# dela é PÚBLICA (aparece no anúncio), por outro endpoint que o chat.
CANAL_AVALIACAO = "avaliacao"
# Carrinho abandonado do site (RF9, 02/10/2026): UMA conversa por lojista
# por site (`externo_id = "lojista:<id do site>"`), no canal do site; cada
# carrinho parado vira uma linha de `atendimento_carrinhos` e uma mensagem
# nela. Quem escreve é `services/atendimento/carrinhos.py`.
CANAL_CARRINHO = "carrinho"
# Comentário e menção nas redes (RF7, 02/10/2026): UMA conversa por (pessoa,
# publicação) no canal da conta; cada comentário é uma linha de
# `atendimento_comentarios` e uma mensagem nela. O comentário da própria
# marca é a RESPOSTA (autor loja), nunca conversa nova. Quem escreve é
# `services/atendimento/redes.py`.
CANAL_COMENTARIO = "comentario"
# WhatsApp (RF10, o outro dev; 02/10/2026 só o NOME): a conversa do Zap entra
# na mesma `atendimento_conversas` com `canal = 'zap'` e vira a aba "Zap" da
# conversa do pedido (`services/atendimento/abas.py`). Ainda sem leitor.
CANAL_ZAP = "zap"
# E-mail do TUTA (RF5, o outro dev; 02/10/2026 só o NOME): `canal = 'email'`
# com `dados.fonte = 'tuta'`. Na Amazon o canal `email` é o da PLATAFORMA (o
# comprador escrevendo pela Amazon, `amazon_email.py`); é esta marca que
# separa o e-mail do Tuta de uma venda Amazon do e-mail da Amazon — nas abas
# (`abas.e_contato`: aba E-mail × Pós-venda) e no envio (o `enviar.py` vai
# rotear por ela: o Tuta responde pelo Tuta, nunca pelo SMTP da Amazon).
# Até esse roteamento existir, o envio RECUSA (`motivo_canal_sem_envio`).
FONTE_TUTA = "tuta"

# Canais SEM envio no DaVinci (trava de 05/10/2026): o e-mail do Tuta e o Zap
# ainda não têm adaptador (o outro dev, RF5/RF10). Gravados com a plataforma
# da venda (shopee/ml/tiktok/magalu/amazon) e o `canal_id` da loja, eles
# cairiam no adaptador do MARKETPLACE, que manda o texto para o
# `comprador_id` — ali, o e-mail ou o telefone do contato — e só falharia
# por acaso. O `enviar` recusa ANTES de qualquer adaptador (código
# `canal_sem_envio`, com esta frase na tela) e a IA não os pega (nem o cron,
# nem o "Sugerir", nem o automático). O e-mail da AMAZON sem a marca do
# Tuta é o canal da plataforma (`amazon_email.py`, SMTP) e continua como está.
MOTIVO_TUTA_SEM_ENVIO = (
    "E-mail do Tuta: o envio do e-mail do Tuta ainda não existe no DaVinci. "
    "Responda pelo próprio Tuta."
)
MOTIVO_ZAP_SEM_ENVIO = (
    "Zap (WhatsApp): o envio do Zap ainda não existe no DaVinci. Responda pelo Duoke."
)
# O e-mail que a PONTE da Central levou à conversa (`dados.fonte='tuta'` +
# `dados.mail`, 08/10/2026): a pessoa responde pela caixa da conversa (a fila
# da Central de e-mail); a IA e a mensagem automática NUNCA respondem e-mail.
# É a frase do "Sugerir resposta" e da automática nessa conversa (a velha, "o
# envio ainda não existe", enganava).
MOTIVO_EMAIL_SO_PESSOA = (
    "E-mail: a IA não sugere nem manda resposta de e-mail (nem a mensagem automática). "
    "Quem responde é uma pessoa, pela caixa de resposta desta conversa."
)


def motivo_canal_sem_envio(canal: str | None, plataforma: str | None, dados: object) -> str | None:
    """A frase de por que esta conversa não sai pelo DaVinci (None = o canal tem envio). PURA.

    A régua é a de `abas.e_contato`: o Zap sempre; o e-mail fora da Amazon
    (só o Tuta grava e-mail fora dela) ou com a marca `dados.fonte = 'tuta'`.
    O cron da IA filtra o mesmo no SQL (`ia._sql_canal_sem_envio`).
    """
    if canal == CANAL_ZAP:
        return MOTIVO_ZAP_SEM_ENVIO
    if canal != CANAL_EMAIL:
        return None
    fonte = dados.get("fonte") if isinstance(dados, dict) else None
    marca = "" if fonte is None or isinstance(fonte, bool) else str(fonte).strip()
    if marca == FONTE_TUTA and isinstance(dados, dict) and isinstance(dados.get("mail"), dict):
        # A conversa da ponte: responde a PESSOA (a IA e a automática nunca).
        return MOTIVO_EMAIL_SO_PESSOA
    if plataforma != "amazon" or marca == FONTE_TUTA:
        return MOTIVO_TUTA_SEM_ENVIO
    return None


# Os canais de cada plataforma externa (NÃO entram em
# `CANAIS_POR_PLATAFORMA`: o sync não lê nenhum deles).
CANAIS_EXTERNOS: dict[str, tuple[str, ...]] = {
    PLATAFORMA_SITE: (CANAL_CARRINHO,),
    PLATAFORMA_INSTAGRAM: (CANAL_COMENTARIO,),
    PLATAFORMA_FACEBOOK: (CANAL_COMENTARIO,),
}

# Canais que são SEMPRE depois da compra, com ou sem número de pedido gravado
# na conversa: pós-venda do ML (pack), SAC da Magalu, e-mail da Amazon, a
# reclamação e a avaliação. Nos demais (chat), o que decide é o pedido
# ligado; a pergunta no anúncio é sempre antes. É a regra dos filtros
# Pré-venda/Pós-venda da lista e da etiqueta (`etiqueta_fatos.e_pos_venda`)
# — um lugar só.
CANAIS_SEMPRE_POS_VENDA = (
    CANAL_POS_VENDA,
    CANAL_SAC,
    CANAL_EMAIL,
    CANAL_RECLAMACAO,
    CANAL_AVALIACAO,
)

# O ML tem DUAS caixas por conta, com API, prazo e limite diferentes:
# pergunta pré-venda (pública, no anúncio) e mensagem pós-venda (por pack).
CANAIS_POR_PLATAFORMA: dict[str, tuple[str, ...]] = {
    "shopee": (CANAL_CHAT,),
    "ml": (CANAL_PERGUNTA, CANAL_POS_VENDA),
    "tiktok": (CANAL_CHAT,),
    "amazon": (CANAL_EMAIL,),
    # Magalu: pergunta pré-venda (no anúncio), chat com o cliente (aberto pelo
    # produto, segue depois da compra) e SAC (protocolo de pós-venda).
    "magalu": (CANAL_PERGUNTA, CANAL_CHAT, CANAL_SAC),
    # Um canal por loja do robô, sem integração por trás (a migration 0347
    # explica): `atendimento_canais.robo_perfil_id` é o perfil do AdsPower.
    "temu": (CANAL_CHAT,),
    "aliexpress": (CANAL_CHAT,),
}

# ── Quem responde, por canal ──────────────────────────────────────────────
# observar = só lê (é como TODA loja nasce; o Duoke segue respondendo)
# humano   = a equipe responde pelo DaVinci
# copiloto = a IA sugere, a pessoa confere e envia
# auto     = a IA envia sozinha, só nas categorias liberadas no canal
MODO_OBSERVAR = "observar"
MODO_HUMANO = "humano"
MODO_COPILOTO = "copiloto"
MODO_AUTO = "auto"
MODOS = (MODO_OBSERVAR, MODO_HUMANO, MODO_COPILOTO, MODO_AUTO)
MODOS_QUE_ENVIAM = (MODO_HUMANO, MODO_COPILOTO, MODO_AUTO)
# Loja do robô: nada sai pelo DaVinci, então `humano` (a equipe responde por
# aqui) e `auto` (a IA envia) não fazem sentido. Observar = só lê; copiloto =
# a IA escreve a sugestão sozinha (cron) e a pessoa cola no Seller Center.
MODOS_ROBO = (MODO_OBSERVAR, MODO_COPILOTO)
# Canal EXTERNO (site, redes — `CANAIS_EXTERNOS`): o modo que cada um aceita
# na aba Lojas. O site não tem por onde responder (sem Zap/e-mail por
# enquanto): só observar. As redes podem chegar a `humano` (a resposta
# pública/privada sai pela tela, com o envio ligado); IA sozinha, nunca.
MODOS_EXTERNOS: dict[str, tuple[str, ...]] = {
    PLATAFORMA_SITE: (MODO_OBSERVAR,),
    PLATAFORMA_INSTAGRAM: (MODO_OBSERVAR, MODO_HUMANO),
    PLATAFORMA_FACEBOOK: (MODO_OBSERVAR, MODO_HUMANO),
}

# Saúde da leitura do canal. `sem_escopo` = a plataforma disse "sem
# permissão" (TikTok sem `seller.customer_service`, ML com 403 de política,
# Magalu sem o escopo da caixa no token — reautorizar a loja): não é erro
# passageiro, não adianta insistir a cada rodada.
# `sem_endpoint` (02/10/2026) = só do canal do SITE: a rota de leitura do
# carrinho ainda não está publicada no site (404) — publicar o pacote do
# carrinho na Hostinger; até lá o site não é lido.
STATUS_CANAL_SEM_ENDPOINT = "sem_endpoint"
STATUS_CANAL = ("novo", "ok", "sem_escopo", "erro", "desligado", STATUS_CANAL_SEM_ENDPOINT)
# Só das lojas do robô:
#   parado      — o robô não dá sinal (pulso) há mais de
#                 `atendimento_robo_parado_min` minutos: o Mac mini, o
#                 AdsPower ou o robô caiu. Calculado na LEITURA (nenhum pulso
#                 chega para avisar que parou), nunca gravado.
#   sessao_caiu — o robô avisou que o Seller Center saiu da conta no perfil
#                 do AdsPower: alguém precisa entrar de novo (o robô nunca
#                 digita senha).
STATUS_CANAL_PARADO = "parado"
STATUS_CANAL_SESSAO_CAIU = "sessao_caiu"

# ── Prazo de resposta (horas corridas) ───────────────────────────────────
# ML pergunta: quem pergunta no anúncio compra de quem responde primeiro.
# Amazon: a métrica é 24 h corridas, fim de semana e feriado contam.
SLA_HORAS: dict[tuple[str, str], int] = {
    ("shopee", CANAL_CHAT): 12,
    ("ml", CANAL_PERGUNTA): 1,
    ("ml", CANAL_POS_VENDA): 24,
    ("tiktok", CANAL_CHAT): 24,
    ("amazon", CANAL_EMAIL): 24,
    # Temu: a página traz um prazo por conversa (`countDownInfo.deadlineTime`,
    # guardado em `dados.robo.prazo_plataforma`), mas é só visual e o valor
    # real ainda não foi visto numa loja com conversa. 24 h até conferir.
    ("temu", CANAL_CHAT): 24,
    ("aliexpress", CANAL_CHAT): 24,
    # Magalu: pergunta e chat não têm prazo oficial publicado — vale o padrão
    # da caixa (sem entrada aqui de propósito). O SAC tem o prazo POR
    # PROTOCOLO (`due_date`), que o adaptador guarda em
    # `dados[CHAVE_PRAZO_PLATAFORMA]` e o `gravar` usa no lugar deste número;
    # sem `due_date`, o padrão.
    # Externas (02/10/2026): o carrinho parado e o comentário entram na fila
    # "Falta responder" com o prazo padrão — escrito aqui para ninguém achar
    # que foi esquecido. A pergunta no comentário só ordena (RF7: "isso só
    # ordena; não tira nada da fila"), pelo prazo próprio que o leitor das
    # redes pode gravar em `dados[CHAVE_PRAZO_PLATAFORMA]`.
    (PLATAFORMA_SITE, CANAL_CARRINHO): 24,
    (PLATAFORMA_INSTAGRAM, CANAL_COMENTARIO): 24,
    (PLATAFORMA_FACEBOOK, CANAL_COMENTARIO): 24,
}
SLA_PADRAO_HORAS = 24

# O prazo que a PRÓPRIA plataforma deu para esta conversa (ISO 8601, UTC),
# em `atendimento_conversas.dados`. Vale mais que o SLA fixo
# (`gravar._derivar`) quando é posterior à mensagem do cliente — ou quando foi
# LIDO depois dela (`CHAVE_PRAZO_LIDO_EM`): aí vale mesmo vencido, que é a
# verdade da plataforma. Hoje: SAC da Magalu.
CHAVE_PRAZO_PLATAFORMA = "prazo_plataforma"
CHAVE_PRAZO_LIDO_EM = "prazo_plataforma_lido_em"

# De quem é a vez, quando a PRÓPRIA plataforma diz (SAC da Magalu: o status
# do protocolo), em `dados`. Sem a chave, a fila sai das mensagens (quem falou
# por último). Com ela: None = não é a vez da loja; ISO = é a vez da loja
# desde esse momento — resposta da loja DEPOIS dele (o envio pelo DaVinci)
# tira da fila até a leitura seguinte.
CHAVE_VEZ_DA_LOJA = "vez_da_loja_desde"

# ── Limite de caracteres por resposta ─────────────────────────────────────
# ML pós-venda: 350 — acima disso a API recusa a mensagem inteira.
LIMITE_CARACTERES: dict[tuple[str, str], int] = {
    ("shopee", CANAL_CHAT): 1000,
    ("ml", CANAL_PERGUNTA): 2000,
    ("ml", CANAL_POS_VENDA): 350,
    ("tiktok", CANAL_CHAT): 2000,
    ("amazon", CANAL_EMAIL): 2000,
    # Temu: o botão do Seller Center só habilita até 2000 (código da tela).
    # AliExpress: a tela não tem limite no campo; fica o padrão, que é o que
    # cabe numa resposta de chat sem virar e-mail.
    ("temu", CANAL_CHAT): 2000,
    ("aliexpress", CANAL_CHAT): 1000,
    # Magalu (doc de 30/09/2026): chat 2200, SAC 3000. A resposta de pergunta
    # não tem limite documentado: fica o padrão da caixa.
    ("magalu", CANAL_CHAT): 2200,
    ("magalu", CANAL_SAC): 3000,
    # Resposta PÚBLICA à avaliação da Shopee (`reply_comment`): 500, o teto
    # do campo no Seller Center (a confirmar na documentação da API).
    ("shopee", CANAL_AVALIACAO): 500,
    # Resposta PÚBLICA a comentário (RF7): o Instagram corta em 2.200 (o
    # mesmo teto da legenda); a Página do Facebook aceita bem mais, 8.000.
    (PLATAFORMA_INSTAGRAM, CANAL_COMENTARIO): 2200,
    (PLATAFORMA_FACEBOOK, CANAL_COMENTARIO): 8000,
}
LIMITE_PADRAO_CARACTERES = 1000

# ── Autor × origem ────────────────────────────────────────────────────────
# autor = QUEM escreveu na plataforma; origem = POR ONDE saiu.
# Uma resposta da loja pode ter saído pelo DaVinci (pessoa ou IA) ou por fora
# (Duoke, Seller Center, celular) — e é a origem `externo` que diz "alguém
# já respondeu por fora, a IA se cala".
AUTOR_CLIENTE = "cliente"
AUTOR_LOJA = "loja"
AUTOR_SISTEMA = "sistema"
# A plataforma falando NA reclamação (o mediador do ML, o "Com Meli"; 01/10/2026).
# Não é o comprador nem a loja: não conta como fala do cliente na fila nem
# como resposta da loja.
AUTOR_MEDIADOR = "mediador"
AUTORES = (AUTOR_CLIENTE, AUTOR_LOJA, AUTOR_SISTEMA, AUTOR_MEDIADOR)
# Quem escreveu a NOTA INTERNA (01/10/2026): a equipe, no DaVinci — não é
# autor de plataforma (fica fora de AUTORES: a nota não passa pelo
# `gravar_mensagem`). NÃO é `sistema`: o SAC da Magalu usa a última
# mensagem `sistema` para decidir de quem é a vez e para quem vai a
# resposta — uma nota `sistema` mandaria a resposta ao lugar errado.
AUTOR_EQUIPE = "equipe"

ORIGEM_CLIENTE = "cliente"
ORIGEM_HUMANO = "davinci_humano"
ORIGEM_IA = "davinci_ia"
ORIGEM_EXTERNO = "externo"
ORIGEM_SISTEMA = "sistema"
# NOTA INTERNA (01/10/2026): escrita pela equipe no DaVinci, só a equipe vê.
# Nunca é enviada, não conta como resposta, fica fora da pendência e não vai
# para a IA como fala do comprador. Vem com `tipo = TIPO_NOTA`.
ORIGEM_NOTA = "davinci_nota"
# MENSAGEM AUTOMÁTICA DO DAVINCI (05/10/2026, docs/atendimento-automacoes.md):
# o motor de automações (`services/atendimento/automacoes.py`) — menu,
# respostas das opções, "aguarde", convite, "ficou alguma dúvida", pedido
# recebido, entregue e pós-conclusão — no lugar do Duoke. A linha nasce com
# `payload.automacao` ({"codigo", "registro_id", "regra_versao", "parte"}),
# e é por essa marca que a régua (`e_mensagem_automatica`), a fila, a
# métrica e a IA sabem que ninguém da equipe escreveu.
ORIGEM_AUTO = "davinci_auto"
# As que NÓS mandamos: só elas podem ser "adotadas" quando o sync traz de
# volta a mensagem que acabamos de enviar (e só elas vão para "A conferir"
# quando o envio fica ambíguo). A nota NÃO entra: ela não sai.
ORIGENS_DAVINCI = (ORIGEM_HUMANO, ORIGEM_IA, ORIGEM_AUTO)

# Tipo da mensagem da nota interna (os outros tipos vêm da plataforma:
# texto, imagem, video, produto, pedido, arquivo, outro).
TIPO_NOTA = "nota"


def e_nota(origem: str | None, tipo: str | None = None) -> bool:
    """A mensagem é NOTA INTERNA? Quem monta fila, prévia, pendência e prompt pula ela."""
    return origem == ORIGEM_NOTA or tipo == TIPO_NOTA

# ── Estados da mensagem ───────────────────────────────────────────────────
# Entrada: `recebida`. Saída: enviando → enviada | falhou | revisar.
# `revisar` é o envio AMBÍGUO (timeout, erro sem código): pode ter saído, e
# NUNCA se retenta em cima — mensagem não se desenvia.
MSG_RECEBIDA = "recebida"
MSG_ENVIANDO = "enviando"
MSG_ENVIADA = "enviada"
MSG_FALHOU = "falhou"
MSG_REVISAR = "revisar"

# ── Situação da conversa ──────────────────────────────────────────────────
CONVERSA_ABERTA = "aberta"
CONVERSA_RESPONDIDA = "respondida"
CONVERSA_FECHADA = "fechada"
CONVERSA_BLOQUEADA = "bloqueada"

# ── Etiqueta = status atual da conversa (RF1, decidido em 01/10/2026) ─────
# UMA etiqueta por conversa, que muda sozinha quando o status muda
# (PÓS-VENDA → RECLAMAÇÃO). Quem calcula e grava é
# `services/atendimento/etiqueta.recalcular_etiqueta` — ninguém grava a
# coluna direto. CANAL NUNCA É ETIQUETA: e-mail, Zap e a própria reclamação
# são `canal`; a etiqueta é o status do pedido.
ETIQUETA_PRE_VENDA = "pre_venda"
ETIQUETA_POS_VENDA = "pos_venda"
ETIQUETA_RECLAMACAO = "reclamacao"
ETIQUETA_DEVOLUCAO = "devolucao"
ETIQUETA_AG_CANCELAMENTO = "ag_cancelamento"
# Avaliação de venda SEM resposta da loja (RF8, 02/10/2026): "AVALIAÇÃO até
# ser respondida; depois volta ao status anterior" — a pendência é o fato
# (`atendimento_avaliacoes_loja.pendente_desde`), e quando ela acaba o motor
# devolve a etiqueta que os outros fatos dão (a base ou a urgente de antes).
ETIQUETA_AVALIACAO = "avaliacao"
# Carrinho abandonado do site (RF9, 02/10/2026): CARRINHO enquanto o
# carrinho do lojista estiver aberto (`atendimento_carrinhos.situacao =
# 'aberto'`). Recuperado (o lojista finalizou pelo WhatsApp) → PÓS-VENDA
# ("virou pedido"); não recuperado em 7 dias ou resolvido à mão → a base
# (pré-venda: não há pedido).
ETIQUETA_CARRINHO = "carrinho"
# Mídia (RF7, 02/10/2026): a etiqueta PRÓPRIA das conversas de comentário e
# menção das redes. É uma BASE (como pré/pós-venda): não é "algo aberto" num
# pedido, é o que a conversa é — nunca vira indicador secundário. Rosa na tela.
ETIQUETA_MIDIA = "midia"
# Da MAIS urgente para a menos: duas coisas abertas ao mesmo tempo, vale a
# primeira e a outra vira o indicador pequeno (`etiquetas_secundarias`).
# As que vêm depois entram NO LUGAR CERTO desta ordem quando a fonte existir:
# `sac`, `atacado` e `duvidas_sugestoes` (sites) ainda sem lugar decidido.
# A Mídia fica por último: é a base das conversas de rede (nada de pedido
# compete com ela) e só aparece na ordem para o menu Filtrar e a troca à mão.
PRIORIDADE_ETIQUETAS: tuple[str, ...] = (
    ETIQUETA_RECLAMACAO,
    ETIQUETA_AG_CANCELAMENTO,
    ETIQUETA_DEVOLUCAO,
    ETIQUETA_AVALIACAO,
    ETIQUETA_CARRINHO,
    ETIQUETA_PRE_VENDA,
    ETIQUETA_POS_VENDA,
    ETIQUETA_MIDIA,
)
ETIQUETAS = PRIORIDADE_ETIQUETAS
# A etiqueta de BASE (sem nada aberto): pelo pedido ligado — e a Mídia nas
# conversas de comentário/menção. Nunca aparece como indicador secundário:
# toda conversa tem uma delas.
ETIQUETAS_BASE = (ETIQUETA_PRE_VENDA, ETIQUETA_POS_VENDA, ETIQUETA_MIDIA)
# Como a tela escreve (o histórico também: "de Pós-venda para Reclamação").
ROTULO_ETIQUETA: dict[str, str] = {
    ETIQUETA_PRE_VENDA: "Pré-venda",
    ETIQUETA_POS_VENDA: "Pós-venda",
    ETIQUETA_RECLAMACAO: "Reclamação",
    ETIQUETA_DEVOLUCAO: "Devolução",
    ETIQUETA_AG_CANCELAMENTO: "Ag. cancelamento",
    ETIQUETA_AVALIACAO: "Avaliação",
    ETIQUETA_CARRINHO: "Carrinho",
    ETIQUETA_MIDIA: "Mídia",
}


def rotulo_etiqueta(etiqueta: str | None) -> str:
    """"Reclamação", "Pós-venda"... — o id cru para etiqueta que ainda não tem nome."""
    if not etiqueta:
        return "—"
    return ROTULO_ETIQUETA.get(etiqueta, etiqueta)


# ── Troca de produto (item 4, fase 4b, 05/10/2026) ───────────────────────
# O texto da OFERTA de troca que a pessoa copia e manda ao comprador do pedido
# em "Aguardando Cancelamento" por falta de estoque
# (`troca_sugestoes.texto_oferta`). SEM NÚMERO (nem do pedido, nem preço,
# nem quantidade), SEM PRAZO e SEM MARGEM: só os nomes dos produtos, "pelo
# mesmo valor, sem custo a mais" e "se preferir, cancelamos". O lote irmão
# que o robô de lote já trocaria sozinho (nível 0) não tem texto.
TEXTO_OFERTA_TROCA = (
    "Olá! O produto que você comprou ({original}) ficou sem estoque. "
    "Podemos enviar no lugar: {novo} — pelo mesmo valor, sem custo a mais para você. "
    "Se preferir, cancelamos o pedido. Podemos seguir com a troca?"
)
# O MESMO produto de outro lote do estoque, quando o robô de lote não está
# ligado para a linha (crítica M1): o comprador confirma antes.
TEXTO_OFERTA_MESMO_PRODUTO = (
    "Olá! Para enviar o seu pedido ({original}), vamos separar o mesmo produto de "
    "outro lote do nosso estoque — pelo mesmo valor, sem custo a mais para você. "
    "Se preferir, cancelamos o pedido. Podemos seguir assim?"
)


# ── Reclamações, mediações e devoluções da plataforma (RF2) ──────────────
# `atendimento_reclamacoes.tipo`. A reclamação do ML que subiu para a
# plataforma decidir é `mediacao`; Shopee/TikTok: disputa = `reclamacao`,
# pedido de devolução/reembolso = `devolucao`.
RECLAMACAO_TIPO_RECLAMACAO = "reclamacao"
RECLAMACAO_TIPO_MEDIACAO = "mediacao"
RECLAMACAO_TIPO_DEVOLUCAO = "devolucao"
TIPOS_RECLAMACAO = (
    RECLAMACAO_TIPO_RECLAMACAO,
    RECLAMACAO_TIPO_MEDIACAO,
    RECLAMACAO_TIPO_DEVOLUCAO,
)
# Os tipos que viram a etiqueta RECLAMAÇÃO (o resto, DEVOLUÇÃO). ABERTA =
# `encerrada_em IS NULL` — o `status` é o da plataforma, cru (cada uma tem o
# seu vocabulário), e não decide nada sozinho.
TIPOS_QUE_SAO_RECLAMACAO = (RECLAMACAO_TIPO_RECLAMACAO, RECLAMACAO_TIPO_MEDIACAO)

# ── Avaliações de venda (RF8, 02/10/2026) ─────────────────────────────────
# As plataformas lidas (`services/atendimento/avaliacoes.py`). TikTok,
# Magalu e Amazon ficam de fora: sem API de avaliação para o vendedor.
PLATAFORMAS_AVALIACAO = ("shopee", "ml")
# As que deixam a LOJA responder pela API (resposta pública, no anúncio).
PLATAFORMAS_RESPONDEM_AVALIACAO = frozenset({"shopee"})
# Nota "baixa" (1–3): o destaque na lista e, no ML, o que vira pendência.
NOTA_BAIXA_AVALIACAO = 3

# ── Sites: carrinho abandonado (RF9, 02/10/2026) ──────────────────────────
# Os sites lidos (o nome é o do token em `sites_estoque_tokens`, o mesmo do
# GET /api/sites/estoque) e o endereço de produção de cada um. O endereço
# pode ser trocado sem deploy de código por `atendimento_sites_urls` (o
# ensaio local aponta para o PHP no localhost).
SITES = ("charlots", "uranyx")
NOME_SITE: dict[str, str] = {"charlots": "Charlots", "uranyx": "Uranyx"}
URL_SITE: dict[str, str] = {
    "charlots": "https://charlots.com.br",
    "uranyx": "https://uranyx.com.br",
}
# A rota de LEITURA que cada site expõe ao DaVinci (o contrato exato está no
# topo de `services/atendimento/carrinhos.py` e no LEIA-ME do pacote do site):
# GET, `Authorization: Bearer <token do site>`.
ROTA_CARRINHOS_DO_SITE = "/api/davinci/carrinhos"
# `atendimento_carrinhos.situacao`. ABERTO = parado há mais de
# `atendimento_carrinho_horas` e ainda sem desfecho. Só um aberto por
# (site, lojista) — índice único parcial no banco.
CARRINHO_ABERTO = "aberto"
# O lojista finalizou pelo WhatsApp depois de o carrinho ficar parado (o
# evento `finalizado` do site): "virou pedido".
CARRINHO_RECUPERADO = "recuperado"
# Passou `CARRINHO_DIAS_RECUPERACAO` sem finalizar, ou o lojista esvaziou o
# carrinho sem pedir.
CARRINHO_NAO_RECUPERADO = "nao_recuperado"
# Alguém da equipe marcou como resolvido na tela (sem lembrete por enquanto).
CARRINHO_RESOLVIDO = "resolvido"
SITUACOES_CARRINHO = (
    CARRINHO_ABERTO,
    CARRINHO_RECUPERADO,
    CARRINHO_NAO_RECUPERADO,
    CARRINHO_RESOLVIDO,
)
# Por que o carrinho saiu de aberto (`atendimento_carrinhos.motivo_fim`).
FIM_CARRINHO_FINALIZADO = "finalizado_whatsapp"
FIM_CARRINHO_ESVAZIADO = "esvaziado"
FIM_CARRINHO_PRAZO = "prazo"
FIM_CARRINHO_RESOLVIDO = "marcado_resolvido"
# RF9: "sem compra depois de um prazo [sugestão: 7 dias] → não recuperado".
CARRINHO_DIAS_RECUPERACAO = 7

# ── Redes: comentários e menções (RF7, 02/10/2026) ────────────────────────
# Só as publicações dos últimos N dias são lidas (mídia mais velha raramente
# ganha comentário novo, e cada uma custa uma chamada à Graph API).
MIDIA_DIAS_LEITURA = 30
# `atendimento_publicacoes.tipo`: a publicação da PRÓPRIA conta (os
# comentários dela viram conversa) ou a de outra pessoa que MARCOU a marca
# (IG `/tags`: a menção vira conversa com a legenda dela).
PUBLICACAO_PROPRIA = "propria"
PUBLICACAO_MENCAO = "mencao"
TIPOS_PUBLICACAO = (PUBLICACAO_PROPRIA, PUBLICACAO_MENCAO)
# Resposta privada a um comentário ("Responder no Direct"): uma mensagem,
# até 7 dias depois do comentário (regra da Meta para o Instagram).
RESPOSTA_PRIVADA_DIAS = 7
# RF7: "Perguntas vão para o topo: comentário com '?' ou palavras como ..."
# Só ORDENA (o leitor das redes dá à pergunta um prazo mais curto); não tira
# nada da fila. Sem acento e minúsculas (comparação por palavra inteira,
# depois de `normalizar_inicio`).
PALAVRAS_PERGUNTA: tuple[str, ...] = (
    "quanto",
    "qual",
    "quais",
    "quando",
    "onde",
    "como",
    "tem",
    "vende",
    "vendem",
    "entrega",
    "entregam",
    "frete",
    "prazo",
    "preco",
    "valor",
    "link",
    "cupom",
    "disponivel",
    "aluga",
    "alugam",
)
# A pergunta no comentário ganha este prazo (horas) em vez do padrão.
SLA_PERGUNTA_COMENTARIO_HORAS = 4

# ── Estados do rascunho da IA ─────────────────────────────────────────────
# substituido = alguém respondeu sem usar a sugestão (pelo DaVinci ou por fora)
# bloqueado   = a IA não produziu texto que possa sair
RASCUNHO_PENDENTE = "pendente"
RASCUNHO_ENVIADO = "enviado"
RASCUNHO_EDITADO = "editado"
RASCUNHO_DESCARTADO = "descartado"
RASCUNHO_SUBSTITUIDO = "substituido"
RASCUNHO_BLOQUEADO = "bloqueado"

# O que a pessoa fez com a sugestão — o material do aprendizado.
# `observou` = nota dada à sugestão AINDA pendente, no modo observação (nada
# sai pelo DaVinci; a pessoa só diz se a IA acertou). Nunca vira exemplo para
# a IA, e é PROVISÓRIA: quando a sugestão sai da caixa (enviada pelo DaVinci,
# substituída pela resposta de fora, descartada), a ação vira a de verdade e
# a nota fica.
AVALIACAO_OBSERVOU = "observou"
# O nome que o router usa (contrato da parte 2): um lugar só para a string.
ACAO_OBSERVOU = AVALIACAO_OBSERVOU
AVALIACAO_ACOES = (
    "enviou_igual",
    "editou",
    "descartou",
    "escreveu_do_zero",
    AVALIACAO_OBSERVOU,
)
# O que saiu pelo DaVinci com o texto da sugestão (igual ou editado): o
# `texto_final` é resposta aprovada por pessoa.
ACOES_QUE_SAIRAM = ("enviou_igual", "editou")
# A nota que a pessoa dá (👍/👎).
NOTA_OK = "ok"
NOTA_ERRO = "erro"

# ── Mensagem sem texto da plataforma ──────────────────────────────────────
# O que não é texto, imagem, vídeo, produto nem pedido (figurinha, aviso,
# FAQ, cupom...) e não traz texto próprio: o balão e a prévia da lista
# mostram um RÓTULO em português. O código cru da API ("[sticker]",
# "[EMOTICONS]", "[faq_liveagent]") não diz nada a quem atende — e a IA lê o
# mesmo texto. O conteúdo cru continua no `payload` da mensagem.
ROTULO_TIPO_PLATAFORMA = {
    # Shopee (`message_type`, minúsculo)
    "sticker": "Figurinha",
    "notification": "Aviso da plataforma",
    "system": "Aviso da plataforma",
    "faq": "Pergunta do FAQ",
    "faq_liveagent": "Pergunta do FAQ",
    "bundle_message": "Resposta automática (FAQ)",
    "voucher": "Cupom",
    # TikTok (`type`, maiúsculo na API; a chave aqui é em minúsculo)
    "emoticons": "Figurinha",
    "coupon_card": "Cupom",
    "allocated_service": "Atendente designado",
    "buyer_enter_from_transfer": "Conversa transferida",
}
ROTULO_TIPO_DESCONHECIDO = "Mensagem"


def rotulo_tipo_plataforma(tipo: str | None) -> str:
    """"[Figurinha]", "[Aviso da plataforma]"... — "[Mensagem]" para o que não conhecemos."""
    chave = (tipo or "").strip().lower()
    return f"[{ROTULO_TIPO_PLATAFORMA.get(chave, ROTULO_TIPO_DESCONHECIDO)}]"


# ── Categorias de atendimento ─────────────────────────────────────────────
# A lista OFICIAL de assuntos até o manual base entrar: com a tabela
# `atendimento_categorias` preenchida, a IA lê de lá
# (`manual.categorias_ativas`); vazia, vale esta. Um lugar só no código —
# ninguém mais escreve a lista.
CATEGORIAS = (
    "rastreio",
    "prazo_envio",
    "nota_fiscal",
    "duvida_produto",
    "troca_devolucao",
    "cancelamento",
    "defeito",
    "reembolso",
    "garantia",
    "endereco",
    "desconto",
    "reclamacao_forte",
    "agradecimento",
    "outro",
)
# Assunto que envolve dinheiro, direito do consumidor (art. 49 do CDC) ou dado
# pessoal: a IA pode sugerir, mas quem envia é pessoa — sempre.
CATEGORIAS_SO_HUMANO = (
    "troca_devolucao",
    "cancelamento",
    "defeito",
    "reembolso",
    "garantia",
    "endereco",
    "desconto",
    "reclamacao_forte",
)

# Nome e DESCRIÇÃO de cada assunto: a IA classifica pela descrição, não pelo
# id ("prazo_envio" sozinho não diz se "já foi postado?" é prazo ou
# rastreio). É o que vale com a tabela `atendimento_categorias` vazia.
CATEGORIAS_INFO: dict[str, tuple[str, str]] = {
    "rastreio": (
        "Rastreio",
        "O pedido JÁ FOI ENVIADO e o cliente quer saber onde está, por que não chegou "
        "ou o código de rastreio.",
    ),
    "prazo_envio": (
        "Prazo de envio",
        "O pedido AINDA NÃO FOI ENVIADO e o cliente quer saber quando vai ser postado "
        "ou despachado.",
    ),
    "nota_fiscal": ("Nota fiscal", "Pede a nota fiscal (NF-e, DANFE) ou um dado dela."),
    "duvida_produto": (
        "Dúvida sobre o produto",
        "Pergunta sobre o produto: medida, cor, material, compatibilidade, "
        "disponibilidade, como usar.",
    ),
    "troca_devolucao": (
        "Troca ou devolução",
        "Quer trocar (tamanho, cor, modelo) ou devolver o produto.",
    ),
    "cancelamento": ("Cancelamento", "Quer cancelar a compra, ou pergunta se ainda dá."),
    "defeito": (
        "Defeito ou avaria",
        "O produto chegou quebrado, com defeito, danificado, faltando peça ou diferente "
        "do anúncio.",
    ),
    "reembolso": ("Reembolso", "Quer o dinheiro de volta, estorno, ou pergunta dele."),
    "garantia": ("Garantia", "Pergunta sobre a garantia ou quer acioná-la."),
    "endereco": ("Endereço", "Quer mudar ou confirmar o endereço de entrega."),
    "desconto": ("Desconto", "Pede desconto, cupom, preço menor ou frete grátis."),
    "reclamacao_forte": (
        "Reclamação forte",
        "Cliente irritado: ameaça Procon, Reclame Aqui ou Justiça, fala em golpe ou xinga.",
    ),
    "agradecimento": (
        "Agradecimento",
        "Só agradece, elogia ou confirma que recebeu — não pede nada.",
    ),
    "outro": ("Outro assunto", "Nenhum dos assuntos acima."),
}

# ── Lacunas ───────────────────────────────────────────────────────────────
# As únicas lacunas que a IA pode escrever na resposta; o CÓDIGO preenche
# com o dado do pedido (ia.preencher). O manual base diz quais cada assunto
# usa (`atendimento_categorias.lacunas`).
LACUNAS = (
    "numero_pedido",
    "rastreio",
    "transportadora",
    "previsao_entrega",
    "data_envio",
    "nf_numero",
)

# ── Caixa Humano (09/10/2026) ─────────────────────────────────────────────
# Ao lado da Caixa, só o que a IA NÃO pode responder e falta responder — o
# que ela mandaria para pessoa (`services/atendimento/humano.py`). Os códigos
# dos motivos, da MAIS importante para a menos (o chip da lista mostra o
# primeiro). Ficam gravados em `atendimento_conversas.dados.humano`: só o
# código, nunca texto de comprador.
HUMANO_RECLAMACAO = "reclamacao"
HUMANO_AG_CANCELAMENTO = "ag_cancelamento"
HUMANO_DEVOLUCAO = "devolucao"
HUMANO_CHAMADO = "chamado"
HUMANO_AVALIACAO = "avaliacao"
HUMANO_ALERTA = "alerta"
HUMANO_XINGAMENTO = "xingamento"
HUMANO_ATENDENTE = "atendente"
HUMANO_INSTRUCAO = "instrucao"
HUMANO_EMAIL = "e_mail"
HUMANO_PEDIDO_NAO_ACHADO = "pedido_nao_achado"
HUMANO_SEM_DADO = "sem_dado"
HUMANO_ASSUNTO = "assunto"
HUMANO_SO_ANEXO = "so_anexo"
HUMANO_IA = "ia"
# A conversa BLOQUEADA (a plataforma não deixa responder pelo DaVinci: a
# reclamação só leitura, a avaliação do ML, a janela do TikTok) entra só com
# um motivo de ESTADO (`HUMANO_DE_ESTADO`), e com este junto: quem resolve
# é uma pessoa, na plataforma.
HUMANO_BLOQUEADA = "bloqueada"
# A triagem desta conversa deu erro: na dúvida, ela aparece (e a rodada só
# tenta de novo na revisão de 30 min, sem travar a fila).
HUMANO_FALHA = "falha"
# Não é gravado: a tela pausou a IA nesta conversa (entra na hora, pela consulta).
HUMANO_IA_PAUSADA = "ia_pausada"
# Os motivos do estado do pedido/conversa (os únicos que põem a bloqueada na Caixa Humano).
HUMANO_DE_ESTADO = frozenset(
    {
        HUMANO_RECLAMACAO,
        HUMANO_AG_CANCELAMENTO,
        HUMANO_DEVOLUCAO,
        HUMANO_CHAMADO,
        HUMANO_AVALIACAO,
    }
)
ORDEM_HUMANO: tuple[str, ...] = (
    HUMANO_RECLAMACAO,
    HUMANO_AG_CANCELAMENTO,
    HUMANO_DEVOLUCAO,
    HUMANO_CHAMADO,
    HUMANO_AVALIACAO,
    HUMANO_BLOQUEADA,
    HUMANO_ALERTA,
    HUMANO_XINGAMENTO,
    HUMANO_ATENDENTE,
    HUMANO_INSTRUCAO,
    HUMANO_EMAIL,
    HUMANO_PEDIDO_NAO_ACHADO,
    HUMANO_SEM_DADO,
    HUMANO_ASSUNTO,
    HUMANO_SO_ANEXO,
    HUMANO_IA,
    HUMANO_FALHA,
    HUMANO_IA_PAUSADA,
)
# Como a tela escreve (a API manda junto; a tela só tem reserva para código novo).
ROTULO_HUMANO: dict[str, str] = {
    HUMANO_RECLAMACAO: "Reclamação aberta",
    HUMANO_AG_CANCELAMENTO: "Pedido em Ag. cancelamento",
    HUMANO_DEVOLUCAO: "Pedido com devolução",
    HUMANO_CHAMADO: "Pedido com chamado aberto",
    HUMANO_AVALIACAO: "Avaliação com nota baixa",
    HUMANO_BLOQUEADA: "Bloqueada: responder na plataforma",
    HUMANO_ALERTA: "Procon, Justiça ou golpe",
    HUMANO_XINGAMENTO: "Cliente exaltado",
    HUMANO_ATENDENTE: "Pediu atendente",
    HUMANO_INSTRUCAO: "Texto com cara de instrução",
    HUMANO_EMAIL: "E-mail: só pessoa",
    HUMANO_PEDIDO_NAO_ACHADO: "Pedido não achado no Bling",
    HUMANO_SEM_DADO: "Falta dado (rastreio/NF)",
    HUMANO_ASSUNTO: "Assunto só de pessoa",
    HUMANO_SO_ANEXO: "Só foto/vídeo/arquivo",
    HUMANO_IA: "A IA marcou: precisa de pessoa",
    HUMANO_FALHA: "Não deu para triar: confira",
    HUMANO_IA_PAUSADA: "IA pausada",
}


def rotulo_humano(codigo: str) -> str:
    """"Reclamação aberta", "Pediu atendente"... — o código cru para motivo sem nome."""
    return ROTULO_HUMANO.get(codigo, codigo)


# ── Manual da IA: tipo da regra (parte 2, P7) ─────────────────────────────
# A ordem da tupla é a ordem no prompt:
#   seguranca — vale para TODA mensagem e vem primeiro;
#   categoria — só entra quando a mensagem foi classificada no assunto dela
#               (sem categoria = geral, entra sempre);
#   estilo    — tom e assinatura, por último.
TIPO_REGRA_SEGURANCA = "seguranca"
TIPO_REGRA_CATEGORIA = "categoria"
TIPO_REGRA_ESTILO = "estilo"
TIPOS_REGRA = (TIPO_REGRA_SEGURANCA, TIPO_REGRA_CATEGORIA, TIPO_REGRA_ESTILO)
# Menor = mais importante (vem antes dentro do mesmo tipo).
PRIORIDADE_REGRA_PADRAO = 100


@dataclass
class ResultadoSync:
    """O que um adaptador devolve de uma rodada de leitura de um canal.

    `status` vai para `atendimento_canais.status` (ok | sem_escopo | erro).
    `erro` é texto de OPERAÇÃO (código, HTTP) — nunca conteúdo de comprador.
    """

    status: str = "ok"
    conversas_novas: int = 0
    conversas_atualizadas: int = 0
    mensagens_novas: int = 0
    nao_lidas: int | None = None
    erro: str | None = None


@dataclass
class ResultadoEnvio:
    """O que um adaptador devolve de um envio.

    ok=True e `externo_id` = saiu. `ambiguo=True` = pode ter saído (timeout,
    erro sem código): vira `revisar` e NUNCA se retenta. `bloqueio` = a
    plataforma não deixa mais responder nesta conversa (a conversa vira
    `bloqueada` com esse motivo). `payload` = resposta crua, para depurar.
    """

    ok: bool
    externo_id: str | None = None
    ambiguo: bool = False
    erro: str | None = None
    bloqueio: str | None = None
    payload: dict = field(default_factory=dict)


def sla_horas(plataforma: str, canal: str) -> int:
    """Horas corridas para responder antes de a conversa contar como atrasada."""
    return SLA_HORAS.get((plataforma, canal), SLA_PADRAO_HORAS)


def limite_caracteres(plataforma: str, canal: str) -> int:
    """Tamanho máximo de uma resposta no canal (o validador e a tela usam)."""
    return LIMITE_CARACTERES.get((plataforma, canal), LIMITE_PADRAO_CARACTERES)


def reclamacao_aberta(dados: object) -> bool:
    """A conversa tem reclamação/mediação aberta no ML (`dados.claim_ids`)?

    O adaptador do ML regrava `claim_ids` a cada leitura do pack. Com ela
    aberta, quem responde é pessoa: o que se diz ali entra na mediação.

    Medido em produção (01/10/2026): o ML NÃO esvazia `claim_ids` quando a
    reclamação acaba (nem tira a de cancelamento). Aqui isso só deixa a IA e
    o envio mais cautelosos (pessoa responde); a ETIQUETA usa a regra estrita
    de `etiqueta_fatos._claims_do_pack` (chat bloqueado pela reclamação).
    """
    return isinstance(dados, dict) and bool(dados.get("claim_ids"))


# A Amazon não manda para a caixa a resposta dada no Seller Central: o
# silêncio da caixa não prova que ninguém respondeu. O automático não
# responde a Amazon sozinho (a sugestão continua indo para a caixa).
# Temu e AliExpress: nada sai pelo DaVinci (a pessoa responde no Seller
# Center) — o automático nunca vale para elas.
PLATAFORMAS_SEM_AUTO = frozenset({"amazon", *PLATAFORMAS_ROBO})


# ── Resposta automática da loja (Duoke) ───────────────────────────────────
# O robô do Duoke responde sozinho ("selecione sua dúvida", "sua mensagem foi
# recebida"...) pela mesma API da pessoa, então chega como mensagem DA LOJA.
# Ela não responde o comprador: a conversa continua esperando uma pessoa
# (01/10/2026: a Shopee ATV tinha 7 conversas esperando e a Caixa mostrava 2).
# Começo do texto, sem acento e em minúsculas; medido em 13 lojas Shopee
# (2.499 / 638 / 259 envios). Modelo novo do Duoke = acrescentar aqui.
RESPOSTAS_AUTOMATICAS: tuple[str, ...] = (
    # Robô de atendimento do Duoke (Shopee, ML e TikTok).
    "ola, por favor selecione sua duvida",
    "ola, a sua mensagem foi recebida",
    "descreva sua duvida que assim que um atendente",
    # As respostas das opções 1, 2, 3 e 5 do robô (05/10/2026). Contavam como
    # "a loja respondeu", mas são o robô: medido em produção (14 dias), na
    # Shopee 125 das 152 saíram exatamente 12 h depois do menu (o ciclo do
    # robô), nenhuma entre 5 min e 12 h, e as outras em ciclos seguintes; no
    # ML, o mesmo. A opção 1 do ML tem texto próprio.
    "a entrega e feita pela shopee, nao temos acesso ao transporte",
    "a entrega e feita pelo mercado livre, nao temos acesso ao transporte",
    "todos nossos produtos sao enviados com nota fiscal",
    "o pedido pode ser encerrado a qualquer momento antes do envio",
    "por favor, descreva qual e o defeito do produto",
    # Campanhas automáticas (pedido, entrega, carrinho, "ficou alguma
    # dúvida?"). Na Shopee chegam como `sistema`; no TikTok, como da LOJA.
    # Nenhuma responde o que o comprador perguntou. O "bom dia! ficou alguma
    # dúvida em que eu possa te ajudar" SAIU em 05/10/2026: é pessoa (medido
    # em 14 dias: nenhuma a 12 h do menu, das 7 h às 13 h, espaçamento de
    # gente) — contando como automática, escondia uma resposta da equipe.
    "oi! recebemos seu pedido e ja estamos preparando",
    "ficou alguma duvida sobre o produto? estou aqui pra te ajuda",
    "tudo bem? caso ainda esteja em duvida, posso te explicar",
    "oi! seu produto ainda esta no carrinho",
    "oi! tudo bem? 😊 confirmamos a entrega do seu pedido",
    "oi! 🧳✨ que alegria saber que sua mala ja chegou",
    "oi! so passando para saber se esta tudo certo com o seu prod",
    "oi! passando rapidinho pra saber se esta tudo certo",
    "oi! 👋 notamos que voce deixou alguns itens no carrinho",
    "oi! vi que voce fez o pedido mas o pagamento ainda nao foi",
)


def normalizar_inicio(texto: str | None) -> str:
    import unicodedata

    base = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(sem_acento.lower().split())


def e_resposta_automatica(texto: str | None) -> bool:
    """A mensagem da loja é a resposta automática do Duoke (só o começo do texto).

    A régua ANTIGA (01/10/2026). Desde 05/10/2026 a fila "Falta responder"
    (`gravar.recalcular`), a métrica e a IA usam `e_mensagem_automatica`, que
    a contém inteira (o teste confere) e soma a campanha com o usuário, a
    senha da devolução, a figurinha da campanha e os cartões da Shopee. Ela
    só continua valendo no turno em que o comprador só mandou cartão
    (`e_cartao_do_comprador`): ali, o que fecha a vez é o que ela não pega.
    """
    inicio = normalizar_inicio(texto)
    return bool(inicio) and inicio.startswith(RESPOSTAS_AUTOMATICAS)


# ── Mensagens que o PRÓPRIO DaVinci manda fora do /atendimento ────────────
# Saem por outros módulos e voltam na caixa como resposta de FORA
# (`externo`): ninguém da equipe escreveu, e nenhuma responde o que o
# comprador perguntou. Medido em produção em 05/10/2026:
#   - a senha da devolução travada (`devolucao_mensagem_comprador.texto_para`):
#     Shopee pela API do app, TikTok pelo robô do AdsPower — 12 na caixa
#     (4 Shopee, 8 TikTok);
#   - os e-mails da logística ao comprador da Amazon
#     (`logistica_cliente_mensagens`, os modelos padrão: rastreio, ocorrência,
#     previsão vencida e entregue) — nenhum dos 43 voltou para a caixa (a
#     Amazon só copia o que sai pelo Seller Central); fica reconhecido para o
#     dia em que ela copiar;
#   - o robô do Direct do Instagram NÃO entra: as DMs ficam em `dm_mensagens`
#     e nunca viram `atendimento_mensagens`.
# Escritos sobre o texto como o `normalizar_inicio` o deixa (sem acento,
# minúsculas, espaço único) e começando por "^" + letra (o pré-filtro do SQL
# conta com isso); quem casa é `PADRAO_MENSAGEM_AUTOMATICA`.
MENSAGENS_DAVINCI_FORA: tuple[str, ...] = (
    # "Olá! Aqui é a loja X. Recebemos de volta a mala/o aparelho do pedido…"
    r"^ola! aqui e a loja .*recebemos de volta (a mala|o aparelho)",
    # "Olá, Maria. Seu pedido 701-… foi postado nos Correios em…" (e os outros três)
    r"^ola, .*(foi postado nos correios em"
    r"|a transportadora registrou (uma ocorrencia no transporte|a entrega) do seu pedido"
    r"|a previsao de entrega da transportadora para o seu pedido)",
    # O motor de automações (05/10/2026, `automacoes_catalogo`): os textos
    # padrão que MUDAM em relação ao Duoke — o "aguarde" em português do
    # Brasil e o nome do comprador DENTRO do entregue e do pós-conclusão
    # ("Oi, fulana! …"), e o convite sem o nome ("Já segue…"). A mensagem
    # que o motor manda já é reconhecida pela marca no payload
    # (`e_automatica_pelo_payload`); o texto é a rede para quando a leitura
    # não adota a nossa linha (a plataforma devolveu o texto diferente) e a
    # mensagem volta como `externo` sem a marca.
    r"^ola! recebemos sua mensagem\. estamos com muitos atendimentos",
    r"^oi, .*(confirmamos a entrega do seu pedido"
    r"|que alegria saber que sua mala ja chegou"
    r"|so passando para saber se esta tudo certo com o seu produto)",
    r"^ja segue nossa loja aqui",
)
# Campanhas do Duoke que começam pelo USUÁRIO do comprador, sem saudação
# ("fulana.123 já segue nossa loja aqui no TikTok? …"). No TikTok chegam como
# mensagem da LOJA (286 em 30 dias, 6 lojas; na Shopee, "…aqui na Shopee?",
# chegam como `sistema` e não contam), menos de 1 min depois da fala do
# comprador e a qualquer hora: fechavam 127 dos 133 turnos do TikTok
# respondidos em menos de 2 min, e a mediana do TikTok caía para 2,4 min
# (medido em produção em 05/10/2026). A frase fixa que vem DEPOIS do usuário
# — uma palavra só, sem espaço (as 2.720 dos 30 dias, TikTok e Shopee) —,
# escrita como as de cima. Pessoa que escrevesse "Você já segue nossa loja
# aqui?" também casaria; nenhuma das 2.720 é de pessoa. O usuário começa por
# qualquer caractere (número, "_", "."), então o pré-filtro da primeira letra
# não vale para elas: o SQL testa antes o `TRECHOS_CAMPANHA_COM_USUARIO`.
CAMPANHAS_COM_USUARIO: tuple[str, ...] = ("ja segue nossa loja aqui",)
# Só o começo do texto conta (o `.*` acima não passa daqui, e o SQL não lê o
# texto inteiro de cada mensagem): o mais longo, o e-mail de rastreio, cabe
# com folga.
INICIO_AUTOMATICA = 400
# Tabela fixa de acentos (minúscula e maiúscula → letra sem acento). Para
# estas letras dá o mesmo que o NFKD do `normalizar_inicio`.
_ACENTOS = {
    "a": "áàâãäå",
    "e": "éèêë",
    "i": "íìîï",
    "o": "óòôõö",
    "u": "úùûü",
    "c": "ç",
    "n": "ñ",
    "y": "ýÿ",
}
ACENTOS_DE = "".join(v + v.upper() for v in _ACENTOS.values())
ACENTOS_PARA = "".join(k * (2 * len(v)) for k, v in _ACENTOS.items())
# Os espaços que o `normalizar_inicio` junta num só: os do `split()` e os que
# o NFKD vira espaço (o inseparável). O `\s`/`[[:space:]]` do Python e o do
# Postgres não concordam — a lista vai escrita.
ESPACOS = "".join(
    ch
    for ch in map(chr, range(0x3001))
    if ch.isspace() or unicodedata.normalize("NFKD", ch).isspace()
)
# Só estes são especiais nos dois (Python e o ARE do Postgres); com a barra
# antes, os dois leem o caractere como ele é.
_ESPECIAIS_REGEX = frozenset("\\^$.|?*+()[]{}")


def _literal_regex(texto: str) -> str:
    return "".join(f"\\{ch}" if ch in _ESPECIAIS_REGEX else ch for ch in texto)


def _com_acento(letra: str) -> str:
    """A letra e as formas dela com acento (minúsculas e maiúsculas: o `lower`
    do Postgres pode não baixar acento, conforme o locale)."""
    acentos = _ACENTOS.get(letra, "")
    return letra + acentos + acentos.upper()


def _sobre_o_texto_cru(padrao: str) -> str:
    """O regex do texto normalizado → o mesmo regex sobre o texto CRU em minúsculas.

    Normalizar no banco (`translate` dos acentos em cada mensagem) custava
    mais que o próprio regex (medido em produção, 05/10/2026). A troca é
    mecânica: o começo aceita espaço antes, espaço vira "um ou mais espaços"
    e letra que tem acento vira a classe dela (á/Á contam como a). Os espaços
    vão em `\\uXXXX`, que o Python e o Postgres leem igual.
    """
    espaco = "[" + "".join(f"\\u{ord(ch):04x}" for ch in ESPACOS) + "]"
    saida: list[str] = []
    i = 0
    while i < len(padrao):
        ch = padrao[i]
        if ch == "\\":
            saida.append(padrao[i : i + 2])
            i += 2
            continue
        if ch == "^":
            saida.append(f"^{espaco}*")
        elif ch == " ":
            saida.append(f"{espaco}+")
        elif ch in _ACENTOS:
            saida.append(f"[{_com_acento(ch)}]")
        else:
            saida.append(ch)
        i += 1
    return "".join(saida)


# O regex que o Python (`e_mensagem_automatica`) e o SQL
# (`gravar.mensagem_automatica_sql`) usam, sobre o começo do texto em
# minúsculas — a MESMA string nos dois.
PADRAO_MENSAGEM_AUTOMATICA = _sobre_o_texto_cru(
    "|".join(
        (
            "^(" + "|".join(_literal_regex(p) for p in RESPOSTAS_AUTOMATICAS) + ")",
            *MENSAGENS_DAVINCI_FORA,
            # O usuário: uma palavra (`\S`, igual nos dois) antes da frase.
            *(r"^\S+ " + _literal_regex(frase) for frase in CAMPANHAS_COM_USUARIO),
        )
    )
)
# DOTALL: no Postgres o `.` pega a quebra de linha (o e-mail da Amazon tem).
_RE_MENSAGEM_AUTOMATICA = re.compile(PADRAO_MENSAGEM_AUTOMATICA, re.DOTALL)
# A régua ANTIGA (`e_resposta_automatica`, só o robô e as campanhas do Duoke
# pelo começo do texto) no mesmo molde, para o SQL do turno só de cartão
# (`gravar.resposta_automatica_sql`; o teste compara com o Python).
PADRAO_RESPOSTA_AUTOMATICA = _sobre_o_texto_cru(
    "^(" + "|".join(_literal_regex(p) for p in RESPOSTAS_AUTOMATICAS) + ")"
)
# Pré-filtro do SQL, barato e exato: depois dos espaços, toda mensagem
# automática começa por uma destas letras (em qualquer caixa e acento). Só ~1/3
# das mensagens da loja passa e paga o regex (medido em produção, 05/10/2026).
PRIMEIRAS_LETRAS_AUTOMATICA = frozenset(
    "".join(
        _com_acento(p.lstrip("^")[0]) + p.lstrip("^")[0].upper()
        for p in (*RESPOSTAS_AUTOMATICAS, *MENSAGENS_DAVINCI_FORA)
    )
)


def _trecho_like(frase: str) -> str:
    """A frase (texto normalizado) → o LIKE que o texto CRU em minúsculas casa
    sempre que o regex dela casa: letra que tem acento vira `_` (a classe do
    regex é um caractere só) e espaço vira `%` (o regex pede um ou mais)."""
    saida = []
    for ch in frase:
        if ch == " ":
            saida.append("%")
        elif ch in _ACENTOS:
            saida.append("_")
        else:
            saida.append(f"\\{ch}" if ch in "%_\\" else ch)
    return "%" + "".join(saida) + "%"


# O pré-filtro das campanhas que começam pelo usuário (a primeira letra não
# serve para elas): condição necessária do regex, então o resultado do SQL é
# o mesmo do Python.
TRECHOS_CAMPANHA_COM_USUARIO = tuple(_trecho_like(f) for f in CAMPANHAS_COM_USUARIO)

# ── Mensagens da Shopee que não são de pessoa, pelo PAYLOAD ───────────────
# O texto delas é só o rótulo ("[Figurinha]", "[Cupom]", "[Mensagem]"): o que
# separa a da campanha da que a pessoa mandou é o item cru da Shopee
# (`payload`). Medido em produção em 05/10/2026 (10 dias, mensagens da LOJA):
#   - a figurinha 0007 mandada pela API (`source` "openapi") é a campanha do
#     Duoke: 1.009 das 1.318 saíram entre 1555 e 1565 min depois da última
#     fala do comprador (26 h), e fechavam 393 turnos da Shopee com o p90
#     cravado em 1560 min. As outras figurinhas (0028, 0012, 0013, 0014) não
#     têm esse relógio e continuam contando;
#   - o que a PRÓPRIA Shopee põe na conversa em nome da loja: `source`
#     "server" (cupom, cartão de logística, devolução) e "crm" (pedido de
#     avaliação, vitrine, disparo) — o "crm" sai 0,3 min depois do comprador.
# Só a Shopee grava `source` no payload (ML, TikTok, Amazon e AliExpress não).
FONTES_SHOPEE_DA_PLATAFORMA: tuple[str, ...] = ("server", "crm")
FONTE_SHOPEE_API = "openapi"
TIPO_SHOPEE_FIGURINHA = "sticker"
FIGURINHA_CAMPANHA_DUOKE = "0007"
# O CARTÃO DO PEDIDO que a LOJA manda pela API (05/10/2026): é o "pedido
# recebido" e o "entregue" das campanhas do Duoke — o cartão vai 1 s antes do
# texto. Medido em produção (7 dias): na Shopee (`message_type` "order",
# `source` "openapi") 1.934 dos 1.935 vieram a até 15 s de um texto de
# campanha; no TikTok (`type` "ORDER_CARD", papel `CUSTOMER_SERVICE`), 157 de
# 157. Contando como resposta de pessoa, o cartão calava a IA por 24 h
# (`ia._humano_respondeu_recente`) e fechava a vez do comprador. O cartão
# que a PRÓPRIA TikTok põe (papel `ROBOT`) já é `sistema`.
TIPO_SHOPEE_CARTAO_PEDIDO = "order"
TIPO_TIKTOK_CARTAO_PEDIDO = "ORDER_CARD"
PAPEL_TIKTOK_ATENDIMENTO = "CUSTOMER_SERVICE"
# A marca do motor de automações do DaVinci no payload (`ORIGEM_AUTO`).
CHAVE_AUTOMACAO = "automacao"
# As partes da mensagem automática do DaVinci que FECHAM o turno em que o
# comprador só mandou cartão (`gravar._fechava_a_vez`) — as mesmas que, vindas
# do Duoke, a régua antiga contava como resposta: o "já segue" do TikTok
# (loja), a figurinha 0007 do "caso ainda esteja em dúvida" e o cartão do
# pedido das campanhas. O resto (menu, opções, "aguarde", os textos das
# campanhas) nunca fechou a vez, e continua não fechando: depois da troca, a
# fila fica IGUAL à de hoje. (codigo da automação, tipo da parte).
AUTOMACOES_QUE_FECHAM_A_VEZ: frozenset[tuple[str, str]] = frozenset(
    {
        ("tiktok_convite", "texto"),
        ("shopee_duvida_26h", "figurinha"),
        ("shopee_pedido_recebido", "cartao_pedido"),
        ("shopee_entregue", "cartao_pedido"),
    }
)


def marca_automacao(payload: object) -> dict | None:
    """A marca do motor de automações (`payload.automacao`), ou None. PURA."""
    if not isinstance(payload, dict):
        return None
    marca = payload.get(CHAVE_AUTOMACAO)
    return marca if isinstance(marca, dict) else None


def e_automatica_pelo_payload(payload: object) -> bool:
    """O item cru é automático: cartão da Shopee, figurinha ou cartão de pedido da
    campanha, ou a marca do motor de automações do DaVinci? PURA.

    O SQL é `gravar.mensagem_automatica_sql(texto, payload)`; o teste compara.
    Só vale para mensagem da LOJA (quem chama já filtra o autor): o cartão de
    pedido do COMPRADOR é outra coisa (`e_cartao_do_comprador`).
    """
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get(CHAVE_AUTOMACAO), dict):
        return True
    fonte = payload.get("source")
    if fonte in FONTES_SHOPEE_DA_PLATAFORMA:
        return True
    conteudo = payload.get("content")
    tipo = payload.get("message_type")
    if fonte == FONTE_SHOPEE_API and tipo == TIPO_SHOPEE_CARTAO_PEDIDO:
        return True
    remetente = payload.get("sender")
    if (
        payload.get("type") == TIPO_TIKTOK_CARTAO_PEDIDO
        and isinstance(remetente, dict)
        and remetente.get("role") == PAPEL_TIKTOK_ATENDIMENTO
    ):
        return True
    return (
        fonte == FONTE_SHOPEE_API
        and tipo == TIPO_SHOPEE_FIGURINHA
        and isinstance(conteudo, dict)
        and conteudo.get("sticker_id") == FIGURINHA_CAMPANHA_DUOKE
    )


def e_mensagem_automatica(texto: str | None, payload: object = None) -> bool:
    """Mensagem da loja que ninguém da equipe escreveu (05/10/2026).

    A resposta automática do Duoke (`RESPOSTAS_AUTOMATICAS`), a campanha do
    Duoke que começa pelo usuário do comprador (`CAMPANHAS_COM_USUARIO`), a
    que o DaVinci manda sozinho fora do /atendimento (`MENSAGENS_DAVINCI_FORA`)
    e, com o `payload`, a figurinha da campanha e os cartões da Shopee
    (`e_automatica_pelo_payload`). Não é resposta ao comprador: não fecha a
    vez dele na fila "Falta responder" (`gravar.recalcular` — menos no turno
    em que ele só mandou cartão, `e_cartao_do_comprador`), não aposenta a
    sugestão da IA (`gravar._aposentar_rascunho`) e a métrica de tempo de
    resposta não conta. Nem exemplo de como a equipe responde (a IA não
    aprende com ela). O SQL é `gravar.mensagem_automatica_sql`.
    """
    inicio = (texto or "")[:INICIO_AUTOMATICA].lower()
    if _RE_MENSAGEM_AUTOMATICA.search(inicio) is not None:
        return True
    return e_automatica_pelo_payload(payload)


# ── O cartão que o COMPRADOR manda sem escrever nada ──────────────────────
# Quem abre o chat pela página do produto ou do pedido manda o cartão dele
# (o item cru, no `payload`): na Shopee `message_type` "item",
# "variation_card" ou "order"; no TikTok `type` "PRODUCT_CARD" ou
# "ORDER_CARD". Medido em produção em 05/10/2026 (turnos do comprador nos
# últimos 30 dias, Shopee): quando ele SÓ mandou cartão desde a última
# resposta de pessoa, a equipe respondeu 47 de 710 (7%) e em 590 quem fechou
# a vez foi a figurinha 0007 da campanha do Duoke, 26 h depois; quando ele
# escreveu, a equipe respondeu 3.327 de 3.511 (95%). Com a régua nova sem
# exceção, esses turnos só de cartão ficariam em "Falta responder" para
# sempre, já vencidos (~375 por semana). Então o turno SÓ DE CARTÃO segue a
# régua de antes de 05/10: a mensagem automática que ela já contava (tudo,
# menos o robô e as campanhas do Duoke de `e_resposta_automatica`) fecha a
# vez — `gravar.recalcular_conversa`. No TikTok foram 19 turnos assim.
CARTOES_DO_COMPRADOR_SHOPEE: tuple[str, ...] = ("item", "variation_card", "order")
CARTOES_DO_COMPRADOR_TIKTOK: tuple[str, ...] = ("PRODUCT_CARD", "ORDER_CARD")


def e_cartao_do_comprador(payload: object) -> bool:
    """A mensagem do comprador é só o cartão do produto/pedido? PURA.

    Pelo item cru (`payload`) — o `tipo` não basta: o cartão de variação da
    Shopee é gravado como `outro`. O SQL é `gravar.cartao_do_comprador_sql`;
    o teste compara.
    """
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("message_type") in CARTOES_DO_COMPRADOR_SHOPEE
        or payload.get("type") in CARTOES_DO_COMPRADOR_TIKTOK
    )


def e_pergunta(texto: str | None) -> bool:
    """O comentário é PERGUNTA (RF7)? Tem "?" ou uma das `PALAVRAS_PERGUNTA`.

    Palavra inteira, sem acento e sem diferenciar maiúscula ("Preço?",
    "qual o valor", "TEM no azul"). Só ordena a fila — não decide pendência.
    """
    import re

    if "?" in (texto or ""):
        return True
    palavras = set(re.findall(r"[a-z0-9]+", normalizar_inicio(texto)))
    return not palavras.isdisjoint(PALAVRAS_PERGUNTA)
