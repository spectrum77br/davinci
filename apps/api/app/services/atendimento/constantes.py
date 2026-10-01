"""Vocabulário do atendimento unificado — um lugar só.

O model, os adaptadores, a IA, o router e a tela falam as mesmas palavras;
se cada um escrevesse a sua string, um `"pos-venda"` contra `"pos_venda"`
bastaria para uma conversa sumir da fila sem erro nenhum.
"""

from __future__ import annotations

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

CANAL_CHAT = "chat"
CANAL_PERGUNTA = "pergunta"
CANAL_POS_VENDA = "pos_venda"
CANAL_EMAIL = "email"
# SAC da Magalu: o protocolo de pós-venda (ticket), com prazo próprio
# (`due_date`) e a Magalu mediando. Não é o "pós-venda" do ML (outra API,
# outro limite, outra regra de bloqueio).
CANAL_SAC = "sac"

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

# Saúde da leitura do canal. `sem_escopo` = a plataforma disse "sem
# permissão" (TikTok sem `seller.customer_service`, ML com 403 de política,
# Magalu sem o escopo da caixa no token — reautorizar a loja): não é erro
# passageiro, não adianta insistir a cada rodada.
STATUS_CANAL = ("novo", "ok", "sem_escopo", "erro", "desligado")
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
AUTORES = (AUTOR_CLIENTE, AUTOR_LOJA, AUTOR_SISTEMA)

ORIGEM_CLIENTE = "cliente"
ORIGEM_HUMANO = "davinci_humano"
ORIGEM_IA = "davinci_ia"
ORIGEM_EXTERNO = "externo"
ORIGEM_SISTEMA = "sistema"
# As que NÓS mandamos: só elas podem ser "adotadas" quando o sync traz de
# volta a mensagem que acabamos de enviar.
ORIGENS_DAVINCI = (ORIGEM_HUMANO, ORIGEM_IA)

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

    O adaptador do ML regrava `claim_ids` a cada leitura do pack (vazio
    quando a reclamação acaba). Com ela aberta, quem responde é pessoa: o
    que se diz ali entra na mediação.
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
    "ola, por favor selecione sua duvida",
    "ola, a sua mensagem foi recebida",
    "descreva sua duvida que assim que um atendente",
)


def normalizar_inicio(texto: str | None) -> str:
    import unicodedata

    base = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(sem_acento.lower().split())


def e_resposta_automatica(texto: str | None) -> bool:
    """A mensagem da loja é a resposta automática do Duoke (não conta como resposta)."""
    inicio = normalizar_inicio(texto)
    return bool(inicio) and inicio.startswith(RESPOSTAS_AUTOMATICAS)
