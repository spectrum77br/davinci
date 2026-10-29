"""O que não pode sair — vale para PESSOA e para IA.

É o que protege a loja de punição da plataforma e de prometer o que não
pode. Toda resposta passa por aqui antes de ir para o comprador: a da
pessoa pela tela, a da IA no rascunho e a do envio automático.

    normalizar  →  validar  →  lista vazia? pode sair
                            →  senão: os MOTIVOS, em português curto,
                               que a tela mostra embaixo da caixa de resposta

Duas camadas:

1. DURO PARA TODOS (pessoa e IA) — o que dá punição da plataforma ou fere
   regra dela, não importa quem escreveu:
     • vazio; acima do limite de caracteres do canal (ML pós-venda: 350);
     • contato fora da loja: WhatsApp/zap, telefone, e-mail, @perfil, rede
       social, link/encurtador, PIX/conta bancária, "por fora"/"fora da
       plataforma" — Shopee e ML suspendem conta por isso;
     • Mercado Livre: caractere fora do ISO-8859-1 (a API recusa a mensagem
       INTEIRA); Amazon: emoji (a política de mensagens proíbe); Magalu: CPF
       e CNPJ (a moderação dela barra a resposta, de pessoa também);
     • pedir ou condicionar avaliação ("5 estrelas", "nos avalie", "mude a
       sua nota"). Desestimular reclamação/devolução ("não precisa abrir
       reclamação", "não precisa devolver") e link: desde 28/10/2026 só fora da
       PESSOA (a IA, o agente, a automática) — quem responde pela tela decide;
     • lacuna não preenchida (`{rastreio}` saindo literal para o cliente).

ZAP (`plataforma='whatsapp'` + `canal='zap'`, 26/10/2026 — docs/zap/desenho.md §7):
   o número de WhatsApp é canal da PRÓPRIA marca — o cliente já está no
   WhatsApp. O bloco de contato fora da loja (e-mail, WhatsApp, link,
   telefone, @perfil, rede social) NÃO gera motivo ali (os `_apagar` seguem
   rodando: um link com "pix" no caminho não vira falso PIX). Continuam:
   vazio, limite, lacuna, avaliação, reclamação (fora da pessoa), "por fora" e
   as regras só da IA. E entra uma regra SÓ do Zap, para PESSOA e IA, pela política do
   WhatsApp ("Don't share or ask people to share full length individual
   payment card numbers, financial account numbers…" — whatsappbusiness.com/
   policy): número de cartão (13 a 19 dígitos que passam no Luhn, com cara de
   cartão), agência e conta, pedido de cartão/RG/CPF e número de CPF — e o
   PIX, BLOQUEADO no Zap por decisão do Eduardo (26/10/2026: "por enquanto",
   para pessoa e IA). Conta bancária e documento não são vetáveis. Pedir CPF
   para a nota fiscal: barra até a política confirmar a exceção [confirmar].

2. SÓ IA (`origem == "davinci_ia"`) — prazo e valor ditos em canal de
   atendimento VINCULAM o fornecedor (CDC art. 30 e 35; o art. 34 fecha o
   "foi o robô"). A IA não diz R$ (nem "199,90." no fim da frase, nem
   "cinquenta reais"), %, "frete grátis"/"envio gratuito", prazo (em número
   ou por extenso: "três a cinco dias", "semana que vem", "no mesmo dia"),
   garantia com número ou "vitalícia", nem pede/repete CPF ou endereço, nem
   PROMETE reembolso, troca, reenvio ou "sem custo": o que é fato do sistema
   entra por LACUNA que o código preenche (ia.py, que também barra qualquer
   número que o modelo inventou fora das lacunas). A pessoa pode dizer —
   ela sabe o que promete.

   GARANTIA DA CONVERSA (07/10/2026, `conferir_garantia`, chamada pela IA
   com o bloco do Painel de Garantia): a data de garantia que a IA escreve
   tem de ser uma das do bloco, no lugar certo (início, fim do hardware,
   fim do software) — senão "data de garantia inventada"; e "está
   coberto"/"ainda tem garantia" sem garantia Ativa (ou Somente software)
   no bloco reprova.

Falso positivo custa caro também (a pessoa perde tempo reescrevendo, e a IA
vai para humano à toa), por isso as regras olham CONTEXTO: horário ("14h")
não é prazo, número de pedido e CEP não são telefone, "e-mail" como palavra
não é endereço, "preta por fora" não é negociação por fora, "avaliação
técnica do produto" não é pedir avaliação.

Tudo aqui é função pura, sem banco e sem rede: o router, a IA e o envio
chamam o mesmo código.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence

from app.services.atendimento.constantes import (
    CANAL_ZAP,
    ORIGEM_HUMANO,
    ORIGEM_IA,
    PLATAFORMA_WHATSAPP,
    limite_caracteres,
)

# ── Normalização ──────────────────────────────────────────────────────────

# Tipografia que o editor do celular/Word põe sozinho e que tem equivalente
# ASCII. No ML ela vira ASCII (fora do ISO-8859-1 a API recusa a mensagem).
_TIPOGRAFIA = str.maketrans(
    {
        "‘": "'",  # ‘
        "’": "'",  # ’
        "‚": "'",  # ‚
        "‛": "'",  # ‛
        "′": "'",  # ′
        "“": '"',  # “
        "”": '"',  # ”
        "„": '"',  # „
        "‟": '"',  # ‟
        "″": '"',  # ″
        "‐": "-",  # ‐
        "‑": "-",  # ‑
        "‒": "-",  # ‒
        "–": "-",  # –
        "—": "-",  # —
        "―": "-",  # ―
        "−": "-",  # −
        "…": "...",  # …
        "•": "-",  # •
        "‣": "-",  # ‣
        "⁃": "-",  # ⁃
        "→": "->",  # →
        "←": "<-",  # ←
        "⇒": "=>",  # ⇒
        "≤": "<=",  # ≤
        "≥": ">=",  # ≥
        "≠": "!=",  # ≠
        "⁄": "/",  # ⁄
        "€": "EUR",  # € (não está no ISO-8859-1)
        "™": "TM",  # ™
    }
)

# Invisíveis que só atrapalham (e às vezes servem para esconder palavra do
# filtro: "whats​app").
_INVISIVEIS = dict.fromkeys(
    map(ord, "​‌‎‏⁠﻿­"),
    None,
)

# Espaços "especiais" que viram espaço comum.
_ESPACOS = str.maketrans(dict.fromkeys("     　", " "))

# Emoji e pictogramas por faixa Unicode — sem dependência nova. Inclui o
# "cola" dos emoji compostos (ZWJ, seletor de variação, tom de pele, tecla).
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictogramas, emoticons, transporte, suplementos
    "\U0001fb00-\U0001fbff"
    "☀-➿"  # símbolos diversos e dingbats (☀ ✔ ❤ ✨)
    "⬀-⯿"  # ⭐ ⬆ e cia
    "⌀-⏿"  # ⌚ ⏰ ⏳
    "←-⇿"  # setas que sobraram depois da tipografia (↩ ↪)
    "■-◿"  # ▶ ◀ ■ ●
    "⤀-⥿"  # ⤴ ⤵
    "〰〽㊗㊙"
    "‍︎️⃣"  # ZWJ, seletores de variação, tecla (1️⃣)
    "\U000e0020-\U000e007f"  # tags das bandeiras regionais
    "]"
)


def _sem_emoji(texto: str) -> str:
    return _EMOJI.sub("", texto)


def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
    """Texto limpo e no alfabeto que a plataforma aceita.

    Para todos: forma NFC (acento composto — "é" em dois pedaços não cabe
    no ISO-8859-1), sem caracteres invisíveis, espaços repetidos viram um,
    linhas aparadas, no máximo uma linha em branco seguida.

    Mercado Livre: aspas curvas, travessão, reticências, marcador e setas
    viram ASCII e o emoji sai (a API só aceita ISO-8859-1 e recusa a
    mensagem INTEIRA por um caractere). O que sobrar fora do ISO-8859-1
    (letra chinesa, "≈") NÃO é apagado em silêncio — apagar muda o sentido
    da frase; o `validar` aponta, e a pessoa decide.
    """
    t = unicodedata.normalize("NFC", texto or "")
    t = t.translate(_INVISIVEIS).translate(_ESPACOS)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    if plataforma == "ml":
        t = _sem_emoji(t.translate(_TIPOGRAFIA))
    linhas = [re.sub(r"[ \t\f\v]+", " ", linha).strip() for linha in t.split("\n")]
    t = "\n".join(linhas)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# Letras cirílicas e gregas IGUAIS às latinas ("whаtsapp" com o "а" cirílico).
# O NFKD não as troca — são outras letras para o Unicode —, e o filtro não
# as veria. Só para a forma de busca.
_HOMOGLIFOS = str.maketrans(
    {
        # cirílico
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
        "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ԛ": "q", "ԝ": "w", "һ": "h",
        "ӏ": "l", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h", "ѡ": "w",
        "А": "a", "В": "b", "Е": "e", "К": "k", "М": "m", "Н": "h", "О": "o",
        "Р": "p", "С": "c", "Т": "t", "У": "y", "Х": "x", "І": "i", "Ј": "j",
        "Ѕ": "s",
        # grego
        "α": "a", "ο": "o", "ρ": "p", "ε": "e", "ι": "i", "κ": "k", "ν": "v",
        "τ": "t", "υ": "u", "χ": "x", "ω": "w", "Α": "a", "Β": "b", "Ε": "e",
        "Ζ": "z", "Η": "h", "Ι": "i", "Κ": "k", "Μ": "m", "Ν": "n", "Ο": "o",
        "Ρ": "p", "Τ": "t", "Υ": "y", "Χ": "x",
    }
)


def plano_de(texto: str) -> str:
    """Forma de BUSCA: sem acento, minúscula, compatibilidade Unicode.

    NFKD também desfaz truques de evasão ("ｗｈａｔｓ" em largura cheia vira
    "whats"), e os homóglifos cirílicos/gregos viram a letra latina igual.
    Serve só para casar as regras; nunca é o texto que sai. A IA usa a mesma
    forma para ler as palavras de alerta do cliente.
    """
    decomposto = unicodedata.normalize("NFKD", texto.translate(_HOMOGLIFOS))
    sem_acento = "".join(ch for ch in decomposto if not unicodedata.combining(ch))
    return sem_acento.casefold()


# ── Regras duras (pessoa e IA) ────────────────────────────────────────────
# Todas casam sobre o texto PLANO (sem acento, minúsculo).

_MOTIVO_WHATSAPP = "contato fora da loja (WhatsApp)"
_MOTIVO_TELEFONE = "contato fora da loja (telefone)"
_MOTIVO_EMAIL = "contato fora da loja (e-mail)"
_MOTIVO_PERFIL = "contato fora da loja (@perfil)"
_MOTIVO_REDE = "contato fora da loja (rede social)"
_MOTIVO_LINK = "link"
_MOTIVO_PIX = "pagamento fora da plataforma (PIX)"
_MOTIVO_BANCO = "pagamento fora da plataforma (conta bancária)"
_MOTIVO_POR_FORA = "negociação fora da plataforma"
# O nome público (os testes do agente, 21/10/2026): o motivo vale também no
# agente — o prompt manda escrever "a compra é pelo próprio app" e nunca
# "direto com a loja/conosco" (revisão r5i: o envio revalida e recusaria).
MOTIVO_POR_FORA = _MOTIVO_POR_FORA
_MOTIVO_AVALIACAO = "pede ou condiciona avaliação"
_MOTIVO_RECLAMACAO = "desestimula reclamação"
_MOTIVO_ML_ISO = "caractere que o Mercado Livre não aceita"
_MOTIVO_EMOJI_AMAZON = "emoji (Amazon)"
_MOTIVO_DOCUMENTO_MAGALU = "documento (CPF/CNPJ): a moderação da Magalu barra"
_MOTIVO_LACUNA = "lacuna não preenchida"
# Zap (26/10/2026): a política do WhatsApp — para pessoa E IA, só no Zap.
_MOTIVO_ZAP_PIX = "PIX: bloqueado no WhatsApp por enquanto"
_MOTIVO_ZAP_CARTAO = "número de cartão: a Meta proíbe no WhatsApp"
_MOTIVO_ZAP_CONTA = "agência e conta bancária: a Meta proíbe no WhatsApp"
_MOTIVO_ZAP_DOCUMENTO = "número de documento (CPF/RG): a Meta proíbe no WhatsApp"
_MOTIVO_ZAP_PEDE = "pedir cartão, CPF ou RG: a Meta proíbe no WhatsApp"

# O e-mail vem ANTES de link e @perfil: "fulano@gmail.com" é um motivo só.
_EMAIL = (
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    # "fulano@uol" — o provedor sem o ".com" ainda entrega o endereço.
    re.compile(
        r"[\w.+-]+@(?:gmail|hotmail|outlook|yahoo|icloud|uol|bol|terra|live|msn|globo"
        r"|protonmail|proton|zipmail|ig)\b"
    ),
    # "fulano arroba gmail ponto com", "fulano [arroba] uol [ponto] com" — o
    # jeito de escapar do filtro.
    re.compile(
        r"\b[\w.+-]+\s*[(\[]?\s*arroba\s*[)\]]?\s*[\w-]+\s*[(\[]?\s*(?:\.|ponto|dot)\s*[)\]]?"
        r"\s*(?:com|br|net|org)\b"
    ),
    re.compile(r"\b(?:gmail|hotmail|outlook|yahoo|icloud)\b"),
)

_WHATSAPP = (
    re.compile(
        r"\b(?:whats?|wats?|uats?|whatz|watz)\s*(?:app?|aap|ap|zap)?\b"
        r"|\bwhats?ap+\b|\bwatsap+\b"
        r"|\bzap\s*zap\b|\bzapzap\b|\bzap\b|\bzapp+\b|\bz[@4]p+\b"
        r"|\buatiz?a?p+\w*|\bwhatiz?ap+\b"
        r"|\bwpp?s?\b|\bwa\.me\b"
    ),
    # Soletrado com separador: "w h a t s", "w.h.a.t.s".
    re.compile(r"\bw[\s._-]+h[\s._-]*a[\s._-]*t[\s._-]*s"),
)

# Telefone. As âncoras (?<![\w/.-]) e (?![\w/-]) impedem casar PEDAÇO de um
# número maior (pedido do ML de 16 dígitos, pedido da Amazon 702-…-…,
# chave de NF, rastreio AA123456789BR) ou de uma data (25/09/2026).
_BORDA_INI = r"(?<![\w/.-])"
_BORDA_FIM = r"(?![\w/-])"
_TELEFONE = (
    # Celular com DDD: (11) 98765-4321 · 11 9 8765 4321 · +55 11987654321
    re.compile(
        _BORDA_INI + r"(?:\+?55[\s.-]?)?\(?[1-9]{2}\)?[\s.-]?9[\s.-]?\d{4}[\s.-]?\d{4}" + _BORDA_FIM
    ),
    # Fixo com DDD: (11) 3333-4444 · 1133334444
    re.compile(
        _BORDA_INI + r"(?:\+?55[\s.-]?)?\(?[1-9]{2}\)?[\s.-]?[2-5]\d{3}[\s.-]?\d{4}" + _BORDA_FIM
    ),
    # Sem DDD, com hífen: 98765-4321 · 3333-4444 (CEP é 5-3: não casa).
    re.compile(_BORDA_INI + r"9?\d{4}-\d{4}" + _BORDA_FIM),
    # 0800 / 0300
    re.compile(_BORDA_INI + r"0[38]00[\s.-]?\d{3}[\s.-]?\d{4}" + _BORDA_FIM),
    # "tel: 3333 4444", "celular 9 8765 4321" — a palavra entrega o número
    # mesmo quando o formato foge dos de cima.
    re.compile(r"\b(?:tel|telefone|fone|celular|cel|ligue|ligar|liga)\b\D{0,6}\d[\d\s.()-]{6,}\d"),
)

_PERFIL = re.compile(r"(?<![\w@.])@[a-z0-9_](?:[a-z0-9_.]{0,28}[a-z0-9_])?\b")

_REDE_SOCIAL = (
    re.compile(
        r"\b(?:instagram|insta|facebook|telegram|messenger|kwai|discord|twitter|youtube"
        r"|linkedin|skype|signal)\b"
    ),
    # Apelido curto só com contexto: "face" sozinho é a face da mala.
    re.compile(
        r"\b(?:no|pelo|meu|nosso|nossa|seu|sua)\s+(?:face|fb|ig|insta|tg|tt)\b"
    ),
)

_LINK = (
    re.compile(r"\bhttps?\s*:\s*/\s*/|\bwww\s*\."),
    # Encurtadores e links de aplicativo.
    re.compile(
        r"\b(?:bit\.ly|tinyurl\.com|goo\.gl|cutt\.ly|t\.co|is\.gd|ow\.ly|rebrand\.ly"
        r"|linktr\.ee|shorturl\.at|shope\.ee|amzn\.to|a\.co|tiktok\.com|vm\.tiktok)\b"
    ),
    # Domínio solto: "minhaloja.com.br", "loja.shop". A lista de terminações
    # é fechada de propósito — "obs." e "etc." não são link.
    re.compile(
        r"\b[a-z0-9][a-z0-9-]{1,62}\.(?:com|net|org|br|io|me|ly|app|shop|store|site"
        r"|online|info|biz|co|link|xyz|tv|gg)(?:\.[a-z]{2})?\b(?!\.?[a-z0-9])"
    ),
    # Domínio escrito para escapar: "nossaloja ponto com ponto br",
    # "nossaloja [ponto] com", "nossaloja . com . br".
    re.compile(
        r"\b[a-z0-9][a-z0-9-]{1,62}\s*(?:[(\[]\s*(?:ponto|dot)\s*[)\]]|\bponto\b|\bdot\b)\s*"
        r"(?:com|net|org|shop|store|site|online)\b"
    ),
    re.compile(r"\b[a-z0-9][a-z0-9-]{1,62}\s*\.\s*(?:com|net|org)\s*\.\s*br\b"),
)

# "pix" seguido de LETRA é outra palavra ("pixel"); seguido de dígito é o
# PIX copia e cola (EMV do Banco Central: "br.gov.bcb.pix0136<chave>…"), que
# o `\b` deixava passar (revisão da Onda 2 do Zap, 26/10/2026). O
# identificador do arranjo também vale sozinho.
_PIX = re.compile(r"\bpix(?![a-z])|br\.gov\.bcb\.pix")
_BANCO = (
    re.compile(r"\b(?:dados|conta|transferencia)\s+bancari[ao]s?\b|\bagencia\s+e\s+conta\b"),
    # "transfere pra minha conta", "deposita na conta" — mas não o estorno
    # "depositado na sua conta" nem "na conta do Mercado Pago".
    re.compile(
        r"\b(?:transfer\w*|deposit\w*)\s+(?:\w+\s+){0,2}?(?:pra|para|na|em|numa)\s+(?:a\s+)?"
        r"(?:minha\s+|nossa\s+|essa\s+|esta\s+|seguinte\s+|uma\s+)?conta\b"
        r"(?!\s+d[oa]\s+(?:mercado|shopee|amazon|tiktok|plataforma|app|aplicativo|comprador"
        r"|cliente))"
    ),
    re.compile(r"\b(?:faz|faca|fazer|manda|mande|mandar|via|por)\s+(?:um\s+|uma\s+)?(?:ted|doc)\b"),
)

# "fora da plataforma" é inequívoco; "por fora" não é ("a mala é preta por
# fora"), por isso só conta perto de verbo de negócio/contato ou de vantagem.
_FORA_DA_PLATAFORMA = re.compile(
    r"\bfora\s+d[aoe]s?\s+(?:plataforma|app|aplicativo|site|shopee|amazon|mercado\s*livre"
    r"|ml|tiktok|marketplace)\b"
)
# Formas FLEXIONADAS, não radicais: "fecho por fora" (zíper da mala) e
# "falta acabamento por fora" são descrição de produto, não negócio.
_VERBO_NEGOCIO = (
    r"(?:compr(?:a|ar|e|o|amos|ando|ou)|vend(?:a|er|e|o|emos|endo|eu)"
    r"|pag(?:a|ar|ue|amos|ando|amento|o)|negoci\w*|fechar|fechamos|fechar\s+negocio"
    r"|fal(?:a|ar|e|amos|ando)|cham(?:a|ar|e|amos)|convers(?:ar|amos|e)"
    r"|combin(?:a|ar|e|amos)|contat(?:ar|e|o)|acert(?:ar|amos|e)|resolv(?:e|er|emos)"
    r"|transfer\w*|direto)"
)
_POR_FORA = (
    re.compile(rf"\b{_VERBO_NEGOCIO}(?:\s+\w+){{0,3}}?\s+por\s+fora\b"),
    re.compile(
        r"\bpor\s+fora\b(?:\s+\w+){0,4}?\s+(?:mais\s+barat\w*|desconto|sem\s+taxa|economiz\w*"
        r"|direto|pix)\b"
    ),
    re.compile(r"\b(?:boleto|pix|deposito|transferencia|pagamento)\s+por\s+fora\b"),
    # "compra direto comigo que sai mais barato".
    re.compile(
        r"\b(?:compr\w*|vend\w*|pag\w*|negoci\w*|fech\w*)\s+(?:\w+\s+){0,2}?direto\s+"
        r"(?:comigo|conosco|com\s+(?:a\s+gente|a\s+loja|nos|o\s+vendedor|a\s+vendedora))\b"
    ),
)

# Pedir/condicionar avaliação. "Avaliação técnica do produto" (devolução) e
# "nota fiscal" NÃO são avaliação — as regras abaixo evitam os dois.
_NAO_AVALIACAO = r"(?!\s+(?:do|da|dos|das|de|tecnica|interna)\b)"
_PECA_AVALIACAO = r"(?:avaliac\w+|feedback|comentario|review|qualificac\w+|estrelas?)"
_AVALIACAO = (
    re.compile(r"\bavalie-?nos\b|\b(?:nos|me)\s+avali(?:e|em|ar)\b"),
    # Imperativo no começo de frase ou depois de "por favor", "pode"...
    re.compile(
        r"(?:^|[.!?;:,\n]\s*|\b(?:favor|pode|poderia|puder|gentileza|voce\s+pode)\s+)"
        r"avalie(?:m)?\b"
    ),
    re.compile(r"\b(?:esqueca|lembre(?:-se)?|deixe\s+de)\s+(?:de\s+)?(?:nos\s+)?avali\w+"),
    re.compile(
        r"\b(?:deix\w*|fac\w*|poste\w*|mand\w*|registr\w*|coloc\w*)\s+"
        r"(?:uma?\s+|sua\s+|seu\s+|a\s+sua\s+|o\s+seu\s+)(?:boa\s+|otima\s+|excelente\s+)?"
        rf"{_PECA_AVALIACAO}{_NAO_AVALIACAO}"
    ),
    # "dê" sem acento é "de" — a preposição ("depois de uma avaliação do
    # técnico"). Só conta no começo de frase ou depois de um pedido.
    re.compile(
        r"(?:^|[.!?;:,\n]\s*|\b(?:favor|pode|poderia|nos|me)\s+)(?:de|da|dar)\s+"
        r"(?:uma?\s+|sua\s+|a\s+sua\s+)(?:boa\s+|otima\s+|excelente\s+)?"
        rf"(?:{_PECA_AVALIACAO}|nota(?!\s*fiscal)){_NAO_AVALIACAO}"
    ),
    re.compile(r"\bdeix\w*\s+(?:uma?\s+|sua\s+|a\s+sua\s+)nota\b(?!\s*fiscal)"),
    re.compile(
        r"\b(?:avaliac\w+|avaliar|nota|feedback|comentario|review|qualificac\w+)\s+"
        r"(?:positiv\w*|maxim\w*|boa|otima|excelente|5|cinco|10|dez)\b"
    ),
    re.compile(r"\b(?:5|cinco)\s+estrelas?\b|\bestrelinhas?\b"),
    # Mudar/tirar a avaliação que o cliente já deu.
    re.compile(
        r"\b(?:mud|alter|retir|remov|exclu|apag|tir|corrig|revis|melhor(?:ar|e))\w*\s+"
        r"(?:a\s+|o\s+|sua\s+|essa\s+|esse\s+|esta\s+|o\s+seu\s+|seu\s+|a\s+sua\s+)?"
        rf"(?:avaliac\w+|feedback|comentario|review|qualificac\w+){_NAO_AVALIACAO}"
    ),
    re.compile(
        r"\b(?:sua|a)\s+(?:avaliacao|opiniao)\s+(?:e|eh)\s+(?:muito\s+)?"
        r"(?:importante|essencial|fundamental)"
    ),
    # O jeito educado — e o mais natural para um modelo na categoria
    # "agradecimento": "que tal avaliar?", "ficaremos felizes se você
    # avaliar", "poderia deixar sua opinião?", "sua avaliação nos ajuda".
    # "Avalie a embalagem ao receber" é conferir, não avaliar a loja.
    re.compile(
        r"\b(?:que\s+tal|se\s+puder|se\s+possivel|quando\s+puder|nao\s+esqueca\s+de"
        r"|ficaremos\s+(?:muito\s+)?(?:felizes|gratos|agradecidos|contentes)\s+se"
        r"|ficariamos\s+(?:muito\s+)?\w+\s+se|gostariamos\s+(?:muito\s+)?(?:que|de)"
        r"|adorariamos\s+(?:que|se|saber)|contamos\s+com|poderia|podia|voce\s+pode)\b"
        r"(?:\s+\w+){0,5}?\s+(?:avali\w*(?!\s+(?:a|o)\s+(?:embalagem|estado|pacote|caixa))"
        r"|deix\w*\s+(?:\w+\s+){0,2}?(?:opiniao|nota|estrelas?|comentario|feedback|review))"
    ),
    re.compile(
        r"\b(?:sua|a\s+sua|seu|o\s+seu)\s+(?:avaliacao|opiniao|feedback|nota|comentario|review)"
        r"\s+(?:nos\s+|me\s+)?(?:ajuda|ajudara|e\s+(?:muito\s+)?(?:importante|essencial"
        r"|fundamental|valiosa)|faz\s+(?:toda\s+)?(?:a\s+)?diferenca|conta\s+muito|vale\s+muito"
        r"|significa)"
    ),
    # Estrela em emoji: "5⭐", "⭐⭐⭐⭐⭐".
    re.compile(r"\d\s*[\u2b50\u2605\U0001f31f]|[\u2b50\u2605\U0001f31f](?:\s*[\u2b50\u2605\U0001f31f])+"),
    # Pedir para NÃO avaliar mal: "não deixe avaliação negativa", "evite dar
    # nota baixa", "não avalie mal".
    re.compile(
        r"\b(?:nao|evite|evitem|evitar)\s+(?:\w+\s+){0,3}?(?:avaliac\w*|notas?|comentario"
        r"|review|feedback|qualificac\w*|estrelas?)\s+(?:\w+\s+)?(?:negativ\w*|baixas?|ruins?"
        r"|mas?)\b"
    ),
    re.compile(r"\bnao\s+(?:nos\s+|me\s+)?avali(?:e|em)\b|\bevit\w*\s+(?:\w+\s+){0,2}?avali\w*"),
    # Condicionar: avaliação em troca de cupom/brinde/reembolso.
    re.compile(
        rf"\bavaliac\w+{_NAO_AVALIACAO}(?:\s+\w+){{0,6}}?\s+"
        r"(?:cupom|brinde|desconto|bonus|presente|premio)"
    ),
    re.compile(
        r"\b(?:cupom|brinde|desconto|bonus|presente|premio)(?:\s+\w+){0,6}?\s+"
        rf"(?:avali\w+{_NAO_AVALIACAO}|estrelas?)"
    ),
)

# Desestimular reclamação/mediação/chamado na plataforma.
_ALVO_RECLAMACAO = (
    r"(?:reclam\w*|disputa\w*|media[cç]\w*|denunci\w*|contest\w*|chamado|procon"
    r"|reclame\s+aqui)"
)
_RECLAMACAO = (
    re.compile(
        r"\b(?:nao|nem)\s+(?:precisa(?:ra|mos)?|e\s+(?:necessario|preciso|recomendado)"
        r"|ha\s+(?:necessidade|motivo)|tem\s+(?:necessidade|por\s*que|motivo)|abra|abre|abrir"
        r"|faca|faz|registre|registrar|va|deve|recomendamos|aconselhamos|vale\s+a\s+pena"
        r"|compensa)\b"
        rf"(?:\s+\w+){{0,3}}?\s+{_ALVO_RECLAMACAO}"
    ),
    # Imperativo contra o caminho da plataforma: "não abra devolução", "não
    # solicite reembolso pela plataforma, a gente resolve aqui".
    re.compile(
        r"\bnao\s+(?:abra|abram|solicite|solicitem|peca|pecam|faca|facam|registre|registrem"
        r"|inicie|iniciem|acione|acionem)\s+(?:\w+\s+){0,2}?(?:devoluc\w*|reembols\w*|estorn\w*"
        r"|reclam\w*|disputa\w*|media\w*|chamado|cancelamento|troca)"
    ),
    re.compile(r"\bnao\s+(?:reclame|reclamem)\b"),
    re.compile(
        r"\b(?:evite|evitem|evitar|sem\s+necessidade\s+de|dispensa|desnecessario|antes\s+de)\b"
        rf"(?:\s+\w+){{0,3}}?\s+{_ALVO_RECLAMACAO}"
    ),
    # Pedido para fechar a reclamação — imperativo/infinitivo, não o fato
    # ("a plataforma encerrou a disputa a seu favor" é notícia, não pedido).
    re.compile(
        r"\b(?:cancel(?:e|ar|a)|retir(?:e|ar)|remov(?:a|er)|exclu(?:a|ir)|fech(?:e|ar)"
        r"|encerr(?:e|ar)|desist(?:a|ir)|tir(?:e|ar)|finaliz(?:e|ar)|arquiv(?:e|ar))\s+"
        r"(?:a\s+|sua\s+|essa\s+|esta\s+|o\s+|seu\s+|a\s+sua\s+|o\s+seu\s+|da\s+|do\s+"
        r"|dessa\s+|desta\s+|da\s+sua\s+|do\s+seu\s+)?"
        r"(?:reclam\w*|disputa\w*|media[cç]\w*|denuncia\w*|chamado|contestac\w*)"
    ),
)

# Desestimular a DEVOLUÇÃO ou o reembolso (28/10/2026, a revisão da trava): o
# `_RECLAMACAO` só olhava reclamação/disputa/mediação; com o agente tratando a
# devolução como a equipe trata, "não precisa devolver", "evite o reembolso",
# "não siga com a devolução" e "sugerimos não…" não podem sair sozinhos. Vale
# junto com o `_RECLAMACAO` (fora da pessoa). Sem o "antes de" (o "a Shopee
# analisa antes de liberar o reembolso" é o andamento, não desestímulo).
_ALVO_DEVOLUCAO = r"(?:devoluc\w*|devolv\w*|reembols\w*|estorn\w*)"
_DESESTIMULA_DEVOLUCAO = (
    re.compile(
        r"\b(?:nao|nem)\s+(?:precisa(?:ra|mos)?|e\s+(?:necessario|preciso|recomendado)"
        r"|ha\s+(?:necessidade|motivo)|tem\s+(?:necessidade|por\s*que|motivo)|vale\s+a\s+pena"
        r"|compensa|recomendamos|aconselhamos)\b"
        rf"(?:\s+\w+){{0,3}}?\s+{_ALVO_DEVOLUCAO}"
    ),
    re.compile(
        r"\b(?:evite|evitem|evitar|sem\s+necessidade\s+de|desnecessari[oa])\b"
        rf"(?:\s+\w+){{0,3}}?\s+{_ALVO_DEVOLUCAO}"
    ),
    re.compile(
        r"\bnao\s+(?:siga|sigam|prossiga|prossigam|continue|continuem)\s+(?:com\s+)?"
        r"(?:a\s+|o\s+|sua\s+|seu\s+|essa\s+|esse\s+)?"
        rf"(?:{_ALVO_DEVOLUCAO}|reclam\w*|disputa\w*)"
    ),
    re.compile(r"\b(?:sugerimos|recomendamos|aconselhamos)\s+(?:que\s+(?:voce\s+)?)?nao\b"),
    re.compile(
        r"\b(?:cancel(?:e|ar|a)|desist(?:a|ir)|retir(?:e|ar))\s+"
        r"(?:a\s+|sua\s+|essa\s+|esta\s+|o\s+|seu\s+|da\s+|do\s+|dessa\s+|desta\s+|da\s+sua\s+"
        r"|do\s+seu\s+)?"
        rf"{_ALVO_DEVOLUCAO}"
    ),
)

_LACUNA = re.compile(r"\{\{?\s*[a-z_]+\s*\}?\}", re.IGNORECASE)

# ── Regras só da IA ───────────────────────────────────────────────────────

_MOTIVO_VALOR = "valor em R$ só pode vir do sistema"
_MOTIVO_PERCENTUAL = "percentual só pode vir do sistema"
_MOTIVO_FRETE = "frete grátis só pessoa pode prometer"
_MOTIVO_PRAZO = "prazo em números só pode vir do sistema"
# O nome público (rodada 4 do agente, 21/10/2026: só o caminho do agente tira
# este motivo quando o prazo vem negado — "não conseguimos garantir até sexta").
MOTIVO_PRAZO = _MOTIVO_PRAZO
_MOTIVO_GARANTIA = "prazo de garantia só pode vir do sistema"
_MOTIVO_DADO_PESSOAL = "pede ou repete dado pessoal (CPF, endereço, telefone)"
_MOTIVO_PROMESSA = "promessa (reembolso, troca, sem custo) só pessoa pode fazer"

_NUMERO_POR_EXTENSO = (
    r"(?:um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze"
    r"|quatorze|catorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta"
    r"|cinquenta|sessenta|setenta|oitenta|noventa|cem|cento|duzent[oa]s|trezent[oa]s"
    r"|quatrocent[oa]s|quinhent[oa]s|seiscent[oa]s|setecent[oa]s|oitocent[oa]s|novecent[oa]s"
    r"|mil)"
)
# Sem "um/uma": "tenha um ótimo dia" não é prazo (a regra com "um" exige
# palavra de prazo antes: "leva uma semana", "é de um dia").
_NUMERO_POR_EXTENSO_2 = _NUMERO_POR_EXTENSO.replace("(?:um|uma|", "(?:")
# Número com unidade não é preço: "1,50 m", "6,75 polegadas", "1.26 GHz",
# "2,40 kg". A ficha do anúncio (14/10/2026) leva a IA a responder dado
# técnico — sem estas unidades, "a tela tem 6,75 polegadas" virava "valor em
# R$" e a sugestão certa ficava bloqueada. Letra solta (V, W) e MB/KB não
# contam ("Fica 129,90 V à vista", "Sai por 49,90 w" são preço): volts e
# watts só por extenso.
_UNIDADE_MEDIDA = (
    r"(?!\s*(?:m|cm|mm|km|kg|g|l|ml|pol|polegadas?|gb|tb|mah|ah|mp|mpx|ghz|mhz|hz"
    r"|kw|volts?|watts?|ppi|nits?|litros?)\b)"
)
# Aspas como polegada ("6.75\"", "6,56''", "15.6\"") só com a TELA junto e
# no formato de tela (até 9,99 ou 10,0 a 99,9): sem ela, ou com '"59,90"',
# é preço entre aspas. Sai do texto antes de procurar valor.
_ASPAS_POLEGADA = r"(?:\"|''|\u201d|\u2033|\u2032\u2032)"
_TAMANHO_DE_TELA = r"(?<![\d.,])(?:\d[.,]\d{1,2}|\d{2}[.,]\d)\s*" + _ASPAS_POLEGADA
_MEDIDA_DE_TELA = re.compile(
    r"\b(?:tela|display|visor)\b[^\d\n.!?]{0,30}" + _TAMANHO_DE_TELA
    + r"|" + _TAMANHO_DE_TELA + r"\s*(?:de\s+)?(?:tela|display)\b"
)
# A variação de MEMÓRIA do celular ("12.64 + 64 Sd Card", "F105 - 12.64", "Branco
# 12.64 está esgotada": RAM.armazenamento, como os anúncios Uranyx escrevem) não é
# preço: em 28/10/2026 ela barrava a resposta do agente como "valor em R$" (b10e8d7b,
# c790959a). Só sai do texto (antes de procurar valor, como a medida de tela) o par
# de memória que existe (RAM de 1 a 32, armazenamento de 8 a 1024) COM um sinal de
# memória junto — o "+"/"gb"/"sd" depois, ou o código do modelo, a cor, "variação",
# "versão", "memória" logo antes — e SEM sinal de preço por perto (a revisão de
# 28/10: "12x de 32,64", "fica 12,64", "Valor: 24,64", "US$ 12,64" continuam valor).
_PAR_DE_MEMORIA = re.compile(
    r"(?<![\d.,/])(?:1|2|3|4|6|8|12|16|24|32)\s*[.,/]\s*(?:8|16|32|64|128|256|512|1024)"
    r"(?![\d]|[.,]\d)"
)
_CORES = (
    r"(?:preto|preta|branco|branca|azul|verde|laranja|prata|dourado|dourada|rosa|roxo|roxa"
    r"|cinza|grafite|vermelho|vermelha|amarelo|amarela|lilas|bege|titanio|champagne)"
)
_MEMORIA_DEPOIS = re.compile(r"\s*(?:\+|gb\b|sd\b|" + _CORES + r"\b)")
_MEMORIA_ANTES = re.compile(
    r"(?:\b(?:variacao|variacoes|versao|versoes|memoria|modelo|opcao)\b\W*"
    r"|\b[a-z]{1,3}\d{1,4}[a-z]?(?:\s+(?:pro|max|plus|ultra|lite|mini|se|neo))*\W*"
    r"|\b" + _CORES + r"\b\W*)$"
)
_PRECO_ANTES = re.compile(
    r"(?:\$|\bus\s*\$|\brs\b|\b(?:custa\w*|custo|preco\w*|valor\w*|fica|ficou|sai|saiu|por"
    r"|pagar|paga|pago|parcel\w*|\d+\s*x|vezes|frete|reembols\w*|estorn\w*|diferenca|total"
    r"|entre|desconto|cupom|promocao|apenas|somente|so)\b)[^\d]{0,12}$"
)
_PRECO_DEPOIS = re.compile(r"\s*(?:reais|real|rs|brl|conto|contos|\$)\b")


def _sem_variacao_de_memoria(plano: str) -> str:
    """O texto sem o par de memória com sinal de memória (ver `_PAR_DE_MEMORIA`)."""

    def trocar(m: re.Match[str]) -> str:
        antes, depois = plano[max(0, m.start() - 30) : m.start()], plano[m.end() : m.end() + 12]
        if _PRECO_ANTES.search(antes) or _PRECO_DEPOIS.match(depois):
            return m.group(0)
        if _MEMORIA_DEPOIS.match(depois) or _MEMORIA_ANTES.search(antes):
            return " "
        return m.group(0)

    return _PAR_DE_MEMORIA.sub(trocar, plano)


_DIA_DA_SEMANA = r"(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)"

_VALOR = (
    re.compile(r"\br\s*\$"),
    re.compile(rf"\d[\d.,]*\s*(?:mil\s+)?reais\b|\b{_NUMERO_POR_EXTENSO}\s+reais\b"),
    # 59,90 · 1.234,56 — mas não "1,50 m" (medida do produto). O ponto ou a
    # vírgula do FIM DA FRASE não impede ("custa 199,90."): só dígito depois.
    re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})*,\d{2}(?!\d|[.,]\d)" + _UNIDADE_MEDIDA),
    # 59.90 (decimal com ponto, do jeito do teclado em inglês).
    re.compile(r"(?<![\d.,])\d{1,4}\.\d{2}(?!\d|[.,]\d)" + _UNIDADE_MEDIDA),
)
_PERCENTUAL = re.compile(r"\d\s*%|\bpor\s*cento\b")
_FRETE = re.compile(
    r"\b(?:frete|envio|entrega|postagem|reenvio)\b[^.!?\n]{0,25}\b(?:gratis|gratuit[oa]s?|free"
    r"|por\s+nossa\s+conta|por\s+conta\s+da\s+loja|zerad[oa])\b"
    r"|\b(?:gratis|gratuit[oa]s?)\b[^.!?\n]{0,10}\b(?:frete|envio|entrega)\b"
    r"|\b(?:pagamos|pagaremos|arcamos|arcaremos|cobrimos|cobriremos|custeamos|bancamos)\b"
    r"[^.!?\n]{0,20}\b(?:frete|envio|postagem)\b"
)
_PRAZO = (
    # "3 dias", "48 horas", "2 a 5 dias úteis". "14h" (horário) fica de fora.
    re.compile(r"\b\d+\s*(?:a\s+\d+\s*)?(?:dias?|horas?|semanas?|meses|mes)\b"),
    re.compile(
        r"\b(?:em|dentro\s+de|ate|no\s+maximo|daqui\s+a|prazo\s+de)\s+(?:ate\s+)?"
        rf"{_NUMERO_POR_EXTENSO}\s+(?:dias?|horas?|semanas?|meses)\b"
    ),
    # "três a cinco dias úteis", "dois dias".
    re.compile(
        rf"\b{_NUMERO_POR_EXTENSO_2}\s+(?:a\s+{_NUMERO_POR_EXTENSO}\s+)?"
        r"(?:dias?|horas?|semanas?|meses)\b"
    ),
    # "leva uma semana", "o prazo médio é de um mês", "demora um dia".
    re.compile(
        r"\b(?:de|em|dentro\s+de|ate|no\s+maximo|daqui\s+a|prazo\s+de|leva|levam|levara|demora"
        r"|demoram|demorara|cerca\s+de|aproximadamente|mais\s+ou\s+menos)\s+(?:ate\s+)?"
        r"(?:um|uma|uns|umas)\s+(?:dias?|semanas?|mes|meses|horas?)\b"
    ),
    # "semana que vem", "próxima segunda", "fim do mês", "no mesmo dia".
    re.compile(
        r"\b(?:semana|mes)\s+que\s+vem\b"
        rf"|\bproxim[ao]s?\s+(?:semana|mes|dias?|{_DIA_DA_SEMANA})\b"
        r"|\b(?:fim|final|inicio|comeco|meio)\s+d[oa]\s+(?:mes|semana)\b|\bmesmo\s+dia\b"
    ),
    # "em 48h" / "em 48hs" é prazo; "às 14h" é horário.
    re.compile(r"\b(?:em|dentro\s+de|ate|no\s+maximo)\s+(?:ate\s+)?\d+\s*(?:h|hs|hrs?)\b"),
    re.compile(r"\bate\s+(?:o\s+)?dia\s+\d"),
    # Promessa de dia ("chega dia 10", "será entregue no dia 30"). O FATO
    # passado ("enviado no dia {data_envio}") é lacuna do código e passa.
    re.compile(
        r"\b(?:chega|chegam|chegara|chegarao|chegue|entrega|entregaremos|entregara"
        r"|recebe|recebera|receberao|enviaremos|enviara|postaremos|postara|despacharemos"
        r"|despachara|sera\s+(?:entregue|enviado|postado|despachado)"
        r"|vai\s+(?:chegar|ser\s+entregue|ser\s+enviado))"
        r"\s+(?:no\s+|ate\s+(?:o\s+)?|o\s+)?dia\s+\d"
    ),
    re.compile(
        r"\b(?:cheg|entreg|receb|envi|post|despach|sai|saira)\w*\s+(?:ate\s+)?"
        r"(?:hoje|amanha|segunda|terca|quarta|quinta|sexta|sabado|domingo)\b"
    ),
)
_GARANTIA = (
    re.compile(rf"\bgarantia\s+(?:de\s+)?(?:\d+|{_NUMERO_POR_EXTENSO})\b"),
    # "a garantia é de 1 ano", "garantia de fábrica de doze meses".
    re.compile(
        rf"\bgarantia\b[^.!?\n]{{0,25}}?\b(?:\d+|{_NUMERO_POR_EXTENSO})\s*"
        r"(?:anos?|mes|meses|dias?|semanas?)\b"
    ),
    re.compile(
        rf"\b(?:\d+|{_NUMERO_POR_EXTENSO})\s*(?:anos?|mes|meses|dias?)\s+de\s+garantia\b"
    ),
    re.compile(
        r"\bgarantia\s+(?:\w+\s+){0,2}?(?:vitalicia|eterna|ilimitada|total|para\s+sempre"
        r"|por\s+toda\s+a\s+vida)\b"
    ),
)
# Promessa que só pessoa faz: reembolso, estorno, troca, reenvio, "pode
# ficar com o produto", "sem custo". A pessoa sabe o que promete; a IA não.
_PROMESSA = (
    re.compile(r"\b(?:reembols|estorn|ressarc|reenvi|devolv|troc|cancel)(?:aremos|eremos|iremos)\b"),
    re.compile(
        r"\bvamos\s+(?:\w+\s+)?(?:reembolsar|estornar|ressarcir|reenviar|trocar|cancelar"
        r"|devolver\s+(?:o\s+|seu\s+)?(?:valor|dinheiro)|enviar\s+(?:outr[oa]|um\s+nov[oa]"
        r"|uma\s+nov[oa])|mandar\s+(?:outr[oa]|um\s+nov[oa]|uma\s+nov[oa]))"
    ),
    re.compile(r"\b(?:enviaremos|mandaremos)\s+(?:outr[oa]|um\s+nov[oa]|uma\s+nov[oa])\b"),
    re.compile(r"\bpode\s+ficar\s+com\b"),
    re.compile(r"\bsem\s+(?:nenhum\s+)?custo\b"),
    re.compile(
        r"\b(?:troca|devolucao|coleta|retorno)\b[^.!?\n]{0,20}\b(?:gratis|gratuit[oa]s?"
        r"|por\s+nossa\s+conta|por\s+conta\s+da\s+loja)\b"
    ),
    re.compile(
        r"\b(?:reembolso|estorno|devolucao\s+do\s+valor)\s+(?:integral|total|completo)\b"
        r"|\bvalor\s+(?:integral|total)\b"
    ),
    re.compile(r"\bser(?:a|ao)\s+(?:estornad|reembolsad|ressarcid)\w*"),
)
# A IA não pede nem repete dado pessoal: o modelo nem o recebe (vai
# mascarado), e pedir CPF/endereço no chat é assunto de pessoa.
_DADO_PESSOAL = (
    # CPF/CNPJ FORMATADOS: 11 dígitos soltos podem ser número de pedido, e o
    # modelo nunca vê o documento real (vai mascarado) — o que ele escrever
    # com pontuação de documento é invenção ou eco.
    re.compile(r"(?<![\w/.-])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\w/-])"),  # CPF
    re.compile(r"(?<![\w/.-])\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?![\w/-])"),  # CNPJ
    # PEDIR o dado ("me informe seu CPF", "qual é o seu telefone?") — com o
    # possessivo, para "enviamos o e-mail com a nota" continuar passando.
    re.compile(
        r"\b(?:inform|envi|mand|pass|confirm|digit|forne[cç]|preciso\s+d|precisamos\s+d)\w*"
        r"\s+(?:\w+\s+){0,2}?(?:seu|sua|seus|suas)\s+(?:cpf|cnpj|rg|endereco|telefone|celular"
        r"|e-?mail|dados|cartao)\b"
    ),
    re.compile(
        r"\bqual\s+(?:e\s+|eh\s+)?(?:o\s+)?seu\s+(?:cpf|cnpj|rg|endereco|telefone|celular)\b"
    ),
    re.compile(r"\b(?:confirm|atualiz|corrig)\w*\s+(?:o\s+|seu\s+|o\s+seu\s+)?endereco\b"),
    # Documento não tem "e-mail com a nota" para confundir: pedir CPF/RG,
    # com ou sem possessivo ("nos informe o CPF", "manda o cpf"), é pedir.
    re.compile(
        r"\b(?:inform|envi|mand|pass|confirm|digit|forne[cç]|preciso\s+d|precisamos\s+d)\w*"
        r"\s+(?:\w+\s+){0,2}?(?:o\s+|a\s+|seu\s+|sua\s+)?(?:cpf|cnpj|rg)\b"
    ),
    re.compile(r"\bqual\s+(?:e\s+|eh\s+)?(?:o\s+)?(?:cpf|cnpj|rg)\b"),
)


# Magalu: a MODERAÇÃO dela barra CPF/CNPJ na resposta — de pessoa também (o
# mesmo texto que sai na Shopee volta recusado depois de "enviado"). Pix,
# e-mail e telefone já são barrados para todos, acima. Aqui: o documento
# formatado, ou os dígitos logo depois da palavra ("CPF 123.456.789-09",
# "cnpj: 12345678000190"). Onze dígitos soltos continuam passando: podem ser
# o número do pedido ou do rastreio.
_DOCUMENTO_MAGALU = (
    re.compile(r"(?<![\w/.-])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\w/-])"),  # CPF
    re.compile(r"(?<![\w/.-])\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?![\w/-])"),  # CNPJ
    re.compile(r"\b(?:cpf|cnpj)\b[^\d\n]{0,8}\d[\d./ -]{9,17}\d"),
)


# Zap (26/10/2026, docs/zap/desenho.md §7): o que a política do WhatsApp
# proíbe pedir ou mandar, para PESSOA e IA, só no Zap. Casa sobre o plano.
# É o `_DADO_SENSIVEL_ZAP` do desenho, partido em quatro (um motivo cada,
# para a pessoa saber o que tirar): `_tem_cartao`, `_CONTA_BANCARIA_ZAP`,
# `_DOCUMENTO_ZAP` e `_PEDE_ZAP` — mais o PIX, que no Zap tem a frase própria
# (`_MOTIVO_ZAP_PIX`: bloqueado "por enquanto", decisão do Eduardo).
#
# Cartão: 13 a 19 dígitos seguidos ou em grupos (espaço, ponto, hífen), com
# a cara de cartão — o começo E o tamanho da bandeira — E o dígito
# verificador (Luhn). A cara e o Luhn juntos tiram o número de pedido (ML
# "2000…", Amazon "702-…"); o tamanho tira o telefone com DDI ("+55 11
# 98888-7777" = 13 dígitos começando por 55: Mastercard tem 16), que no Zap
# é liberado. O número inteiro: um pedaço de um número maior (a chave da NF,
# 44 dígitos, em grupos de 4) não conta — nem dígito colado, nem grupo
# continuando depois de um espaço/ponto/hífen.
_CARTAO_CANDIDATO = re.compile(
    r"(?<![\d.,/-])(?<!\d[ .-])\d(?:[ .-]?\d){12,18}(?![ .-]?\d)(?![/-])"
)
# (começo, tamanhos) de cada bandeira aceita no Brasil.
_BANDEIRAS_CARTAO: tuple[tuple[re.Pattern[str], tuple[int, ...]], ...] = (
    (re.compile(r"^4"), (13, 16, 19)),  # Visa (e o Elo 4011/4312/4389/4514/4576)
    # Mastercard (51–55, 2221–2720); Elo/Aura 50…
    (re.compile(r"^(?:5[0-5]|2(?:2[2-9]|[3-6]\d|7[01]|720))"), (16, 19)),
    (re.compile(r"^3[47]"), (15,)),  # Amex
    (re.compile(r"^3[068]"), (14, 16, 19)),  # Diners; Hipercard 38…
    (re.compile(r"^35"), (16, 17, 18, 19)),  # JCB
    (re.compile(r"^6"), (16, 17, 18, 19)),  # Discover, Elo 6…, Hipercard 606282
)
# Agência e conta: "ag 1234", "agência: 0001-9", "c/c 12345-6", "conta
# corrente 98765-0", "conta poupança", "cc 12345". A palavra "conta" sozinha
# (a conta da loja, a conta do pedido) só com 4+ dígitos logo depois.
_CONTA_BANCARIA_ZAP = (
    re.compile(r"\bag(?:encia|\.)?\s*(?:n[o°º]?\.?\s*)?[:#]?\s*\d{3,5}(?:\s*-\s*[\dx])?\b"),
    re.compile(
        r"(?:\bc/c|\bcc\b|\bconta(?:\s+(?:corrente|poupanca|salario))?)\.?\s*"
        r"(?:n[o°º]?\.?\s*)?[:#]?\s*\d{4,13}(?:\s*-\s*[\dx])?\b"
    ),
    re.compile(r"\bconta\s+(?:corrente|poupanca|salario)\b"),
    # A ordem inversa do "agência e conta" do `_BANCO` (revisão, 26/10/2026).
    re.compile(r"\bconta\s+e\s+agencia\b"),
)
# O número de documento escrito: CPF formatado, ou dígitos logo depois da
# palavra ("CPF 123.456.789-09", "rg: 12.345.678-9").
_DOCUMENTO_ZAP = (
    re.compile(r"(?<![\w/.-])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\w/-])"),
    re.compile(r"\b(?:cpf|rg|cnh|identidade)\b[^\d\n]{0,8}\d[\d./ -]{5,17}[\dx]"),
)
# O link INTEIRO (com o caminho), só para limpar o plano do Zap: lá o link
# é liberado, e o `_apagar(_LINK)` tira só o "www."/"https://" — sem isto
# "www.loja.com.br/pix-desconto" viraria um falso PIX pelo caminho.
_URL_INTEIRA_ZAP = (
    re.compile(r"(?:\bhttps?\s*:\s*/\s*/|\bwww\s*\.)\S*"),
    re.compile(
        r"\b[a-z0-9][a-z0-9-]{1,62}\.(?:com|net|org|br|io|me|ly|app|shop|store|site"
        r"|online|info|biz|co|link|xyz|tv|gg)(?:\.[a-z]{2})?/\S*"
    ),
)
# PEDIR cartão, CPF ou RG: o verbo de pedir antes (até 3 palavras), "qual
# o seu CPF", "foto do RG", "número/dados do cartão". "Nunca pedimos o
# número do cartão" passa ("pedimos" não está na lista: é aviso, não pedido).
_VERBO_PEDE = (
    r"(?:inform|envi|mand|pass|confirm|digit|forne[cç]|preciso\s+d|precisamos\s+d"
    r"|precisa\s+d|poderia\s+(?:me\s+)?(?:inform|envi|mand|pass)|me\s+(?:diga|diz|fala|fale))"
)
_PEDE_ZAP = (
    re.compile(rf"\b{_VERBO_PEDE}\w*\s+(?:\w+\s+){{0,3}}?(?:cpf|rg|cnh|identidade)\b"),
    re.compile(
        r"\bqual\s+(?:e\s+|eh\s+)?(?:o\s+|a\s+)?(?:seu\s+|sua\s+)?"
        r"(?:cpf|rg|cnh|identidade|numero\s+do\s+(?:seu\s+)?cartao)\b"
    ),
    re.compile(
        rf"\b(?:{_VERBO_PEDE}\w*|qual)\s+(?:\w+\s+){{0,3}}?(?:numeros?|numeracao|dados|digitos"
        r"|cvv|codigo\s+de\s+seguranca|foto|frente|verso|validade)\s+d[oe]\s+(?:seu\s+|sua\s+)?"
        r"cartao\b"
    ),
    # "me passa o cartão de crédito", "envie seu cartão": o artigo/possessivo
    # E (sem o "seu") o "de crédito/débito". "Passa NO cartão de crédito" (a
    # forma de pagar, na venda) e "mando o cartão junto com o presente" (o
    # cartãozinho da loja de festa) não são pedido do número.
    re.compile(
        rf"\b{_VERBO_PEDE}\w*\s+(?:me\s+|pra\s+mim\s+|para\s+mim\s+)?"
        r"(?:(?:o\s+|os\s+dados\s+do\s+)(?:seu\s+)?cartao\s+(?:de\s+)?(?:credito|debito)"
        r"|seu\s+cartao)\b"
    ),
    re.compile(
        r"\bfoto\s+d[oa]\s+(?:seu\s+|sua\s+)?(?:rg|cpf|cnh|identidade|documento|cartao)\b"
    ),
    re.compile(r"\b(?:cvv|cvc)\b"),
)
# PEDIR o número da CONTA (financeira): "manda o número da sua conta", "me
# fala o número da sua conta e agência" (revisão, 26/10/2026) — o motivo é o
# da conta bancária. "Os dados da sua conta no site/app" (o cadastro na loja)
# não é conta bancária.
_PEDE_CONTA_ZAP = re.compile(
    rf"\b(?:{_VERBO_PEDE}\w*|qual)\s+(?:\w+\s+){{0,3}}?(?:numeros?|numeracao|dados)\s+"
    r"d[ae]\s+(?:sua\s+)?(?:conta|agencia)\b"
    r"(?!\s+(?:n[oa]|d[oa])\s+(?:site|app|aplicativo|loja|cadastro|shopee|mercado|amazon"
    r"|tiktok|magalu))"
)
# Documento no Zap: FALHA FECHADO (decisão do Eduardo, 26/10/2026: não se pede
# CPF pelo Zap; §7 do desenho). O pedido nem sempre tem verbo ("CPF?", "Seu
# CPF, por favor", "Nome completo e CPF") nem vem colado nele ("Pode me
# enviar nome completo, CPF e endereço?"): QUALQUER menção a CPF/RG/CNH/
# identidade barra. Fica de fora só a negação explícita ("não precisamos do
# seu CPF", "nunca pedimos CPF nem RG") — com os verbos de pedir/precisar e
# só artigo/possessivo no meio, para "não esquece de mandar o CPF" (que é
# pedido) continuar barrado. CNPJ (o da loja) não entra; "identidade
# visual" (a da festa) também não.
_DOC_ZAP = r"(?:cpf|rg|cnh|identidade(?!\s+visual))"
_MENCIONA_DOCUMENTO_ZAP = re.compile(rf"\b{_DOC_ZAP}\b")
_NEGA_DOCUMENTO_ZAP = re.compile(
    r"\b(?:nao|nunca|jamais)\s+(?:(?:e|eh|sera|vai\s+ser)\s+)?"
    r"(?:precis\w*|ped\w*|solicit\w*|exig\w*|necessari\w*|preciso)\s+"
    r"(?:(?:d[eoa]s?|o|a|os|as|seu|sua|seus|suas|nenhum|nenhuma|informar|enviar|mandar"
    r"|passar)\s+){0,3}"
    rf"{_DOC_ZAP}(?:\s*(?:,|\be\b|\bnem\b|\bou\b)\s*(?:(?:o|a|seu|sua)\s+)?{_DOC_ZAP})*\b"
)


def _luhn(digitos: str) -> bool:
    soma = 0
    for i, ch in enumerate(reversed(digitos)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        soma += n
    return soma % 10 == 0


def _tem_cartao(plano: str) -> bool:
    """Um número de cartão no texto (13–19 dígitos, cara de cartão e Luhn)? PURA."""
    for m in _CARTAO_CANDIDATO.finditer(plano):
        digitos = re.sub(r"\D", "", m.group(0))
        tamanho_ok = any(
            p.match(digitos) and len(digitos) in tamanhos for p, tamanhos in _BANDEIRAS_CARTAO
        )
        if tamanho_ok and _luhn(digitos):
            return True
    return False


def e_zap(plataforma: str | None, canal: str | None) -> bool:
    """A conversa é do Zap do número (o canal da marca, não o de marketplace)? PURA."""
    return plataforma == PLATAFORMA_WHATSAPP and canal == CANAL_ZAP


# ── Validação ─────────────────────────────────────────────────────────────


def _algum(padroes: tuple[re.Pattern[str], ...], texto: str) -> re.Match[str] | None:
    for p in padroes:
        m = p.search(texto)
        if m:
            return m
    return None


def _apagar(padroes: tuple[re.Pattern[str], ...], texto: str) -> str:
    """Tira do texto o que já virou motivo, para não virar dois ("e-mail" + "link")."""
    for p in padroes:
        texto = p.sub(" ", texto)
    return texto


def _fora_do_latin1(texto: str) -> list[str]:
    """Caracteres que o ML recusa — depois da tipografia e sem os emoji."""
    vistos: list[str] = []
    for ch in _sem_emoji(texto.translate(_TIPOGRAFIA)):
        if ord(ch) > 0xFF and ch not in vistos:
            vistos.append(ch)
    return vistos


def validar(
    texto: str,
    *,
    plataforma: str,
    canal: str,
    origem: str,
    prazos_de_garantia: Collection[tuple[int, str, str]] = (),
) -> list[str]:
    """Motivos pelos quais o texto NÃO pode sair (lista vazia = pode).

    Valida o texto como ele SAI (normalizado): o limite de caracteres é o
    que a plataforma conta. Motivos em português curto, sem repetir, na
    ordem em que a pessoa deve corrigir.

    `prazos_de_garantia` = só o AGENTE (20/10/2026): os prazos da regra que
    `ver_garantia` devolveu (`prazos_da_regra`: "3 meses" de hardware e "12
    meses" de software da Uranyx, "90 dias" da Charlots) deixam de ser
    "prazo"/"garantia em número" na frase que fala da garantia, cada um com
    o seu par na mesma oração. Qualquer outro prazo continua barrado. Vazio
    (o legado e a pessoa): nada muda.
    """
    limpo = normalizar(texto or "", plataforma=plataforma, canal=canal)
    if not limpo:
        return ["texto vazio"]

    motivos: list[str] = []
    limite = limite_caracteres(plataforma, canal)
    if len(limpo) > limite:
        motivos.append(f"passa do limite de {limite} caracteres ({len(limpo)})")

    plano = plano_de(limpo)
    # Zap (26/10/2026): o canal da própria marca — o contato não é "fora da loja".
    zap = e_zap(plataforma, canal)

    # Zap: o número de cartão, ANTES de o telefone sair do texto (no Zap ele
    # não é motivo, mas os dígitos são os mesmos).
    if zap and _tem_cartao(plano):
        motivos.append(_MOTIVO_ZAP_CARTAO)

    # Contato fora da loja. A ordem importa: o que casou sai do texto antes
    # da regra seguinte (e-mail não vira também link e @perfil). No Zap os
    # `_apagar` continuam (um link com "pix" no caminho não vira falso PIX),
    # mas nada disso vira motivo.
    if _algum(_EMAIL, plano):
        if not zap:
            motivos.append(_MOTIVO_EMAIL)
        plano = _apagar(_EMAIL, plano)
    if _algum(_WHATSAPP, plano):
        if not zap:
            motivos.append(_MOTIVO_WHATSAPP)
        plano = _apagar(_WHATSAPP, plano)
    if zap:
        # O link inteiro sai antes (o caminho não vira PIX nem documento).
        plano = _apagar(_URL_INTEIRA_ZAP, plano)
    if _algum(_LINK, plano):
        # Link (28/10/2026, o dono: "tem que deixar enviar link na conversa"): quem
        # responde pela tela manda (o risco da plataforma é decisão dela); a IA, o
        # agente e a automática seguem sem link.
        if not zap and origem != ORIGEM_HUMANO:
            motivos.append(_MOTIVO_LINK)
        plano = _apagar(_LINK, plano)
    if not zap:
        if _algum(_TELEFONE, plano):
            motivos.append(_MOTIVO_TELEFONE)
        if _PERFIL.search(plano):
            motivos.append(_MOTIVO_PERFIL)
        if _algum(_REDE_SOCIAL, plano):
            motivos.append(_MOTIVO_REDE)
    # Pix e conta bancária (28/10/2026, o dono: "tem que puder deixar a questao de pix
    # quando e a pessoa digitando" — a devolução em dinheiro da roda que não veio sai
    # por Pix): no marketplace quem responde pela tela decide; a IA, o agente e a
    # automática seguem barrados. No Zap a regra é a da política do WhatsApp, para
    # pessoa e IA (a frase é a da decisão, bloqueado "por enquanto"). Na Magalu a
    # moderação da plataforma recusa Pix e conta de quem quer que escreva: barra todos.
    pix_e_conta_valem = zap or origem != ORIGEM_HUMANO or plataforma == "magalu"
    if _PIX.search(plano) and pix_e_conta_valem:
        motivos.append(_MOTIVO_ZAP_PIX if zap else _MOTIVO_PIX)
    if _algum(_BANCO, plano) and pix_e_conta_valem:
        motivos.append(_MOTIVO_ZAP_CONTA if zap else _MOTIVO_BANCO)
    if _FORA_DA_PLATAFORMA.search(plano) or _algum(_POR_FORA, plano):
        motivos.append(_MOTIVO_POR_FORA)
    if zap:
        # A política do WhatsApp (pessoa e IA): conta, documento, pedido.
        if _algum(_CONTA_BANCARIA_ZAP, plano) or _PEDE_CONTA_ZAP.search(plano):
            motivos.append(_MOTIVO_ZAP_CONTA)
        tem_documento = bool(_algum(_DOCUMENTO_ZAP, plano))
        if tem_documento:
            motivos.append(_MOTIVO_ZAP_DOCUMENTO)
        # A negação explícita sai antes ("não precisamos do seu CPF" não é
        # pedido); o resto que fala de documento barra (falha fechado). Com o
        # número escrito, o motivo do número já diz o que tirar.
        sem_negacao = _NEGA_DOCUMENTO_ZAP.sub(" ", plano)
        if _algum(_PEDE_ZAP, sem_negacao) or (
            not tem_documento and _MENCIONA_DOCUMENTO_ZAP.search(sem_negacao)
        ):
            motivos.append(_MOTIVO_ZAP_PEDE)

    # Regras da plataforma.
    if plataforma == "ml":
        fora = _fora_do_latin1(limpo)
        if fora:
            motivos.append(f"{_MOTIVO_ML_ISO} ({' '.join(fora[:5])})")
    if plataforma == "amazon" and _EMOJI.search(limpo):
        motivos.append(_MOTIVO_EMOJI_AMAZON)
    if plataforma == "magalu" and _algum(_DOCUMENTO_MAGALU, plano):
        motivos.append(_MOTIVO_DOCUMENTO_MAGALU)

    # Avaliação: proibido para todos, em todas.
    if _algum(_AVALIACAO, plano):
        motivos.append(_MOTIVO_AVALIACAO)
    # Reclamação (28/10/2026, o dono: "temos que perder essa trava pra ele aprender
    # corretamente o que precisamos pra cada caso"): desestimular reclamação,
    # disputa ou devolução é decisão de PESSOA — quem responde pela tela manda. Fora
    # dela (a IA e o agente, a automática que sai para todo comprador da regra) o
    # motivo segue: o rascunho vai para a pessoa, nunca sai sozinho.
    if origem != ORIGEM_HUMANO and (
        _algum(_RECLAMACAO, plano) or _algum(_DESESTIMULA_DEVOLUCAO, plano)
    ):
        motivos.append(_MOTIVO_RECLAMACAO)

    lacunas = sorted({m.group(0) for m in _LACUNA.finditer(limpo)})
    if lacunas:
        motivos.append(f"{_MOTIVO_LACUNA} ({', '.join(lacunas)})")

    # Só IA: prazo e valor vinculam — só por lacuna preenchida pelo código.
    if origem == ORIGEM_IA:
        do_prazo = _sem_prazo_da_regra(plano, prazos_de_garantia) if prazos_de_garantia else plano
        if _algum(_VALOR, _sem_variacao_de_memoria(_MEDIDA_DE_TELA.sub(" ", plano))):
            motivos.append(_MOTIVO_VALOR)
        if _PERCENTUAL.search(plano):
            motivos.append(_MOTIVO_PERCENTUAL)
        if _FRETE.search(plano):
            motivos.append(_MOTIVO_FRETE)
        if _algum(_PRAZO, _sem_ate_o_dia_da_garantia(do_prazo)):
            motivos.append(_MOTIVO_PRAZO)
        if _algum(_GARANTIA, do_prazo):
            motivos.append(_MOTIVO_GARANTIA)
        if _algum(_DADO_PESSOAL, plano):
            motivos.append(_MOTIVO_DADO_PESSOAL)
        if _algum(_PROMESSA, plano):
            motivos.append(_MOTIVO_PROMESSA)

    return list(dict.fromkeys(motivos))


def contato_fora_da_loja(texto: str) -> bool:
    """O texto tem contato ou pagamento fora da loja? PURA.

    As MESMAS regras duras de `validar` (e-mail, WhatsApp, link, telefone,
    @perfil, rede social, PIX, conta bancária, "por fora"), sem limite nem
    regra de plataforma. Serve para limpar o texto que vai PARA o modelo — a
    descrição do anúncio na ficha (`ficha.py`) —, não o que sai para o
    cliente (esse passa pelo `validar` inteiro).
    """
    plano = plano_de(normalizar(texto or "", plataforma="", canal=""))
    if not plano:
        return False
    return bool(
        _algum(_EMAIL, plano)
        or _algum(_WHATSAPP, plano)
        or _algum(_LINK, plano)
        or _algum(_TELEFONE, plano)
        or _PERFIL.search(plano)
        or _algum(_REDE_SOCIAL, plano)
        or _PIX.search(plano)
        or _algum(_BANCO, plano)
        or _FORA_DA_PLATAFORMA.search(plano)
        or _algum(_POR_FORA, plano)
    )


def so_da_ia(motivos: list[str]) -> bool:
    """Algum motivo é das regras SÓ da IA (valor, %, frete, prazo, garantia, promessa)?

    A IA usa isto para decidir entre deixar o rascunho na caixa (a pessoa
    corrige o que o envio de qualquer jeito barraria) e bloqueá-lo (o envio
    pela pessoa NÃO barraria — "frete grátis" inventado sairia num clique).
    O "desestimula reclamação" (28/10/2026) NÃO está aqui de propósito: a
    pessoa pode mandar, e o rascunho da IA que o tem fica na caixa para ela
    decidir (nunca no automático: o motivo tira o `validador_ok`).
    """
    da_ia = {
        _MOTIVO_VALOR,
        _MOTIVO_PERCENTUAL,
        _MOTIVO_FRETE,
        _MOTIVO_PRAZO,
        _MOTIVO_GARANTIA,
        _MOTIVO_DADO_PESSOAL,
        _MOTIVO_PROMESSA,
    }
    return any(m in da_ia or m.startswith(_PREFIXOS_GARANTIA_IA) for m in motivos)


# ── Garantia da conversa (só IA, com o bloco do Painel de Garantia) ───────
#
# 07/10/2026 (combinado com o dono): a IA diz se o aparelho tem garantia
# pelas datas do Painel de Garantia (`contexto.garantia_para_ia`) — o
# modelo RECEBE as datas e as escreve; esta conferência é a trava:
#   • data de garantia que não é do bloco → "data de garantia inventada".
#     E no lugar certo: "até/vence/termina" só casa com um FIM (nunca o
#     início), "começou/a partir de/desde" só com o INÍCIO, e a data dita do
#     hardware só com o fim do hardware (a do software, com o do software).
#     A data de OUTRO assunto na mesma frase ("enviado em {data_envio} e a
#     garantia começa na entrega", "previsão é…", "compra feita em…") vale
#     se é do sistema (`outras_datas`: lacunas e data da compra). Numa
#     resposta que fala de garantia, data em outra frase ("Ela vai até
#     07/02/2027") também precisa ser do bloco ou do sistema;
#   • promessa de cobertura ("está coberto", "tá na garantia", "ainda tem
#     garantia", "a garantia vale", "a garantia cobre", "entra na garantia",
#     "pode mandar que cobre", "seu aparelho está garantido", "a garantia
#     ainda não venceu", "dentro da validade", "esse defeito é coberto", "a
#     gente cobre defeito", "a garantia se aplica") sem garantia Ativa ou
#     Somente software no bloco → reprova. A promessa é de cobertura do
#     APARELHO (o defeito, a tela, o conserto) quando não diz "software" ou
#     quando, dizendo, oferece o defeito/a tela ("garantia de software até
#     X, pode mandar o vídeo do defeito"): só com garantia Ativa — com só o
#     software valendo, reprova. E, com mais de uma garantia e nem todas
#     cobrindo, a promessa que não diz de qual compra fala (pela data ou
#     pelo produto) também.
# A proteção DA PLATAFORMA ("Garantia Shopee", "Compra Garantida do Mercado
# Livre", "coberto pelo Mercado Livre", "proteção da Shopee", "política de
# devolução") e a de TERCEIROS ("seguro da transportadora", "coberto pelos
# Correios", "entrega garantida") não são a garantia do aparelho: saem do
# texto antes. Número de NF/pedido não é ano ("a nota fiscal 2045").
# Datas como o brasileiro escreve: 07/01/2027, 7/1/27, 07-01-2027,
# 2027-01-07, "7 de janeiro de 2027", "sete de janeiro de 2027", "até 7 de
# janeiro", "janeiro de 2027", "jan/2027", "01/2027", "em janeiro", "até
# 2027" — vale o que foi escrito (dia e mês sem ano casam com a data do
# bloco de mesmo dia e mês). "De 07/10/2026 a 07/01/2027": a primeira é o
# início, a segunda o fim (da mesma parte); "hardware e software até X": X
# tem de ser o fim dos dois; "até X para hardware e até Y para software",
# "X (hardware)": a parte escrita logo DEPOIS da data é dela.
#
# `garantias=None` = pergunta pré-venda: não há data nenhuma para citar, e a
# promessa de cobertura não é conferida (lá não existe compra para cobrir;
# o "garantia com número" de `validar` vale). `conferir_promessa=False` =
# conversa que não é de garantia (o bloco nem foi consultado): a promessa
# não BLOQUEIA a sugestão — `fala_de_garantia` a manda para pessoa.
# `fala_de_garantia` diz se a resposta fala de garantia (a palavra, o verbo
# — "garantimos", "a loja garante" —, uma paráfrase — "garantido",
# "defeito", "conserto", "assistência técnica", "fabricante", "protegido
# contra", "a gente troca" —, uma promessa de cobertura ou uma data do
# bloco) — e aí ela vai para pessoa (ia.py). O defeito conta mesmo dito
# junto da proteção da plataforma ou de terceiros ("a Garantia Shopee cobre
# defeitos", "coberto pelo seguro contra defeitos"); o seguro do transporte
# ("o seguro cobre extravio", "tem cobertura do seguro da transportadora")
# não conta, nem bloqueia.

MOTIVO_DATA_GARANTIA = "data de garantia inventada"
MOTIVO_COBERTURA_SEM_GARANTIA = "promessa de cobertura sem garantia ativa"
MOTIVO_COBERTURA_HARDWARE = "promessa de cobertura de hardware com só o software na garantia"
MOTIVO_COBERTURA_QUAL_COMPRA = (
    "promessa de cobertura sem dizer de qual compra (há garantia que não cobre)"
)
_PREFIXOS_GARANTIA_IA = (
    MOTIVO_DATA_GARANTIA,
    MOTIVO_COBERTURA_SEM_GARANTIA,
    MOTIVO_COBERTURA_HARDWARE,
    MOTIVO_COBERTURA_QUAL_COMPRA,
)
# Os status do bloco (`contexto.garantia_para_ia`) que cobrem algo HOJE.
_STATUS_ATIVA = "ativa"
_STATUS_SOMENTE_SOFTWARE = "somente_software"

_MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
_MESES_CURTOS = {nome[:3]: n for nome, n in _MESES.items()}
_MES = "(?:" + "|".join(_MESES) + ")"
_MES_CURTO = "(?:" + "|".join(_MESES_CURTOS) + ")"
_ORDINAL = r"(?:\s?(?:o|°))?"
# O dia por extenso ("sete de janeiro", "vinte e um de março").
_UNIDADES = {
    "um": 1,
    "dois": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
}
_DIAS_POR_EXTENSO = {
    "primeiro": 1,
    **_UNIDADES,
    "dez": 10,
    "onze": 11,
    "doze": 12,
    "treze": 13,
    "quatorze": 14,
    "catorze": 14,
    "quinze": 15,
    "dezesseis": 16,
    "dezessete": 17,
    "dezoito": 18,
    "dezenove": 19,
    "vinte": 20,
    "trinta": 30,
}
_DIA_POR_EXTENSO = (
    r"(?:(?:vinte|trinta)\s+e\s+(?:" + "|".join(_UNIDADES) + ")"
    "|" + "|".join(sorted(_DIAS_POR_EXTENSO, key=len, reverse=True)) + ")"
)


def _dia_por_extenso(texto: str) -> int:
    partes = texto.split()
    if len(partes) == 3:  # "vinte e um"
        return _DIAS_POR_EXTENSO[partes[0]] + _UNIDADES[partes[2]]
    return _DIAS_POR_EXTENSO[texto]


# Do mais completo ao mais solto: o que casou sai do texto antes do seguinte
# ("7 de janeiro de 2027" não vira também "janeiro de 2027" e "2027").
_DATAS = (
    # 2027-01-07 (ISO)
    re.compile(r"(?<![\d/.-])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\d/.-]?\d)"),
    # 07/01/2027 · 7/1/27 · 07-01-2027 · 07.01.2027
    re.compile(
        r"(?<![\d/.-])(?P<d>\d{1,2})\s?(?P<sep>[/.-])\s?(?P<m>\d{1,2})\s?(?P=sep)\s?"
        r"(?P<y>\d{4}|\d{2})(?!\d)"
    ),
    # 7 de janeiro de 2027 · 1º de jan. de 2027 · 7 janeiro 2027
    re.compile(
        rf"\b(?P<d>\d{{1,2}}){_ORDINAL}\s*(?:de\s+)?(?P<mn>{_MES}|{_MES_CURTO})\b\.?"
        r"(?:\s*(?:de|/|,|-)\s*|\s+)(?P<y>\d{4})\b"
    ),
    # sete de janeiro de 2027 · vinte e um de março · primeiro de jan.: o dia
    # escrito por extenso é conferido também (não só o mês e o ano).
    re.compile(
        rf"\b(?P<dx>{_DIA_POR_EXTENSO})\s+de\s+(?P<mn>{_MES}|{_MES_CURTO})\b\.?"
        r"(?:(?:\s*(?:de|/|,|-)\s*|\s+)(?P<y>\d{4})\b)?"
    ),
    # 01/2027
    re.compile(r"(?<![\d/.-])(?P<m>\d{1,2})\s?/\s?(?P<y>\d{4})(?!\d)"),
    # janeiro de 2027 · jan/2027 · janeiro 2027 · jan/27
    re.compile(rf"\b(?P<mn>{_MES}|{_MES_CURTO})\b\.?(?:\s*(?:de|/|-)\s*|\s+)(?P<y>\d{{4}})\b"),
    re.compile(rf"\b(?P<mn>{_MES_CURTO})/(?P<y>\d{{2}})\b"),
    # 07/01 · 7/1
    re.compile(r"(?<![\d/.-])(?P<d>\d{1,2})\s?/\s?(?P<m>\d{1,2})(?![\d/])"),
    # 7 de janeiro · 1º de jan · 15 dezembro
    re.compile(rf"\b(?P<d>\d{{1,2}}){_ORDINAL}\s*(?:de\s+)?(?P<mn>{_MES})\b"),
    re.compile(rf"\b(?P<d>\d{{1,2}}){_ORDINAL}\s*(?:de\s+|/)(?P<mn>{_MES_CURTO})\b"),
    # em janeiro · até março
    re.compile(rf"\b(?P<mn>{_MES})\b"),
    # até o dia 10
    re.compile(r"\bdia\s+(?P<d>\d{1,2})\b"),
    # até 2027
    re.compile(r"\b(?P<y>20\d{2})\b"),
)

# Frase que fala da garantia. "Garantido/garantida" do envio ("entrega
# garantida", "a entrega é garantida", "compra garantida") sai antes, em
# `_preparar` — o que sobra é do aparelho ("seu aparelho é garantido").
_FRASE_GARANTIA = re.compile(
    r"\b(?:garantias?|garantid[oa]s?|cobert[oa]s?|cobertura|cobre|cobrem|cobria|validade"
    r"|vigencia|vigente|hardware|software)\b"
)
# A resposta FALA de garantia (vai para pessoa, `fala_de_garantia`): a
# palavra e as paráfrases ("é garantido", "defeito de fábrica", "assistência
# da fábrica", "suporte do fabricante"); sem "hardware"/"software" sozinhos —
# "atualize o software" é dúvida de uso.
_FALA_GARANTIA = re.compile(
    r"\b(?:garantias?|garantid[oa]s?|cobert[oa]s?|cobertura|cobre|cobrem|validade|vigencia"
    r"|vigente)\b"
    r"|\bdefeitos?\s+de\s+fabric\w*"
    r"|\bassistencia(?:\s+tecnica)?\s+(?:d[ao]\s+)?fabric\w*"
    r"|\bsuporte\s+(?:tecnico\s+)?d[oa]\s+fabric\w*"
    # o verbo ("garantimos o aparelho", "a loja garante") — o da plataforma
    # ("a Shopee garante") e o do envio ("entrega garantida") já saíram
    r"|\bgarant\w*"
)
# Defeito, conserto, fabricante, "protegido contra", "a gente troca": a
# resposta fala do aparelho com defeito — conferido no texto ANTES de tirar a
# proteção da plataforma/de terceiros ("a Garantia Shopee cobre defeitos do
# aparelho", "coberto pelo seguro contra defeitos"). O risco do TRANSPORTE
# ("segurado contra extravio", "protegido contra roubo") não é defeito.
_FALA_DEFEITO = re.compile(
    r"\bdefeit\w*|\bconsert\w*|\breparo\w*|\bassistencia\s+tecnica\b|\bfabricante\w*"
    r"|\b(?:protegid[oa]s?|protecao|assegurad[oa]s?|resguardad[oa]s?|amparad[oa]s?|segurad[oa]s?)"
    r"\s+contra\b(?!\s+(?:\w+\s+){0,2}?(?:extravios?|roubos?|furtos?|perdas?|atrasos?|avarias?"
    r"|fraudes?|golpes?)\b)"
    # "qualquer problema, a gente troca", "trocamos o aparelho"
    r"|\b(?:a\s+gente|a\s+loja)\s+(?:\w+\s+){0,2}?(?:troca|substitui)\b"
    r"|\b(?:trocamos|substituimos)\b"
)
# No AGENTE (esclarecimento 2 do orquestrador, 20/10/2026): a orientação de
# defeito da spec 6.4 (acolher, pedir vídeo curto e fotos por aqui, a equipe
# analisa e responde por aqui) cita "defeito" sem ir para pessoa. Vai o
# DIAGNÓSTICO ("é defeito de fábrica", "parece problema de hardware",
# "constatamos o defeito"), a PROMESSA sobre o defeito na mesma frase ("se der
# defeito, a gente troca"; "vamos consertar") — a troca por falta de estoque
# ("podemos trocar pela cor Laranja") não é defeito — e, como no legado,
# fabricante, assistência técnica e "protegido contra".
_DIAGNOSTICO_DE_DEFEITO = re.compile(
    r"\b(?:e|foi|sera|seria|parece(?:\s+ser)?|deve\s+ser|trata-se\s+de|se\s+trata\s+de)"
    r"\s+(?:um\s+|uma\s+|o\s+|a\s+)?(?:defeito|vicio|problema|falha)"
    r"\s+(?:de|do|da|no|na)\s+(?:fabric\w*|hardware|aparelho|placa|bateria|tela|software)\b"
    r"|\b(?:confirmamos|constatamos|identificamos|verificamos|confirmado|constatado)"
    r"\s+(?:o\s+|um\s+)?(?:defeito|vicio)"
)
_PROMESSA_DE_REPARO = re.compile(
    r"\b(?:vamos|iremos|podemos|faremos|vai|vao)\s+(?:\w+\s+){0,2}?"
    r"(?:consertar|reparar|trocar|substituir|arrumar)\b"
    r"|\b(?:consertamos|reparamos|trocamos|substituimos)\b"
    r"|\b(?:a\s+gente|a\s+loja)\s+(?:\w+\s+){0,2}?(?:troca|substitui|conserta)\b"
)
_ASSUNTO_DEFEITO = re.compile(
    r"\b(?:defeit\w*|consert\w*|repar\w*|problema|falha|garantia|quebr\w*|estragad\w*"
    r"|parou|nao\s+funciona\w*|nao\s+liga)\b"
)
_DEFEITO_FORA_DA_LOJA = re.compile(
    r"\bassistencia\s+tecnica\b|\bfabricante\w*"
    r"|\b(?:protegid[oa]s?|protecao|assegurad[oa]s?|resguardad[oa]s?|amparad[oa]s?|segurad[oa]s?)"
    r"\s+contra\b(?!\s+(?:\w+\s+){0,2}?(?:extravios?|roubos?|furtos?|perdas?|atrasos?|avarias?"
    r"|fraudes?|golpes?)\b)"
)


def diagnostica_ou_promete_defeito(texto: str) -> bool:
    """A resposta DIAGNOSTICA o defeito ou PROMETE algo sobre ele? (só o agente) PURA."""
    plano = _HIFEN_NA_PALAVRA.sub("", plano_de(texto or ""))
    if _DIAGNOSTICO_DE_DEFEITO.search(plano) or _DEFEITO_FORA_DA_LOJA.search(plano):
        return True
    return any(
        _PROMESSA_DE_REPARO.search(frase) and _ASSUNTO_DEFEITO.search(frase)
        for frase in _FIM_DE_FRASE.split(plano)
    )


# "garan-tia": a palavra partida por hífen (só para dizer se FALA de garantia).
_HIFEN_NA_PALAVRA = re.compile(r"(?<=[a-z])[-\u2010\u2011]\s*(?=[a-z])")
_ABREVIACAO_MES = re.compile(rf"\b({_MES_CURTO})\.")
_FIM_DE_FRASE = re.compile(r"(?<=[.!?;])\s+|\n+")

# A proteção DA PLATAFORMA ("coberto pela Garantia Shopee", "a Compra
# Garantida do Mercado Livre cobre", "sua compra está coberta pelo Mercado
# Livre") não é a garantia do aparelho: sai do texto antes das conferências.
_PLATAFORMA = (
    r"(?:shopee|mercado\s+livre|mercado\s+pago|ml|tiktok(?:\s+shop)?|amazon|magalu"
    r"|magazine\s+luiza|aliexpress|temu|plataforma|marketplace)"
)
_PROGRAMA_DA_PLATAFORMA = (
    rf"(?:garantia\s+(?:d[aeo]\s+)?{_PLATAFORMA}"
    rf"|compra\s+garantida(?:\s+d[aeo]\s+{_PLATAFORMA})?"
    r"|garantia\s+de\s+a\s+(?:a|ate)\s+z|garantia\s+a-?(?:to-?)?z"
    rf"|protecao\s+(?:ao?|d[eo])\s+comprador(?:\s+d[aeo]\s+{_PLATAFORMA})?"
    rf"|protecao\s+d[aeo]\s+{_PLATAFORMA}|programa\s+de\s+protecao"
    r"|garantia\s+de\s+reembolso|politica\s+de\s+(?:devolucao|reembolso|troca)"
    r"|entrega\s+garantida|garantia\s+de\s+entrega)"
)
_GARANTIA_DA_PLATAFORMA = re.compile(
    # "está coberto pela Garantia Shopee", "coberta pelo Mercado Livre"
    r"\b(?:(?:esta|estao|fica|ficam|e|sao|segue|seguem|continua|continuam)\s+)?"
    r"(?:cobert[oa]s?|protegid[oa]s?|amparad[oa]s?|garantid[oa]s?)\s+(?:\w+\s+){0,2}?(?:pel[oa]|n[oa])\s+"
    rf"(?:propri[oa]\s+)?(?:{_PROGRAMA_DA_PLATAFORMA}|{_PLATAFORMA})\b"
    # "a Compra Garantida (do Mercado Livre) cobre", "a Garantia Shopee"
    rf"|\b{_PROGRAMA_DA_PLATAFORMA}\b"
    r"(?:\s+(?:\w+\s+)?(?:cobre|cobrem|cobrira|garante|garantem|protege|reembolsa)\b)?"
    # "o Mercado Livre cobre", "a Shopee garante"
    rf"|\b{_PLATAFORMA}\s+(?:\w+\s+){{0,2}}?(?:cobre|cobrem|cobrira|garante|garantem|protege"
    r"|reembolsa)\b"
)
# A cobertura de um TERCEIRO ("coberto pelo seguro da transportadora",
# "coberta pelos Correios", "pela política de devolução") não é a garantia
# do aparelho: sai do texto como a da plataforma.
_TERCEIRO = (
    r"(?:seguro|seguradora|transportadora|correios|frete|envio|entrega|plataforma|marketplace"
    r"|politica|programa)"
)
_COBERTO_POR_TERCEIRO = re.compile(
    r"\b(?:(?:esta|estao|fica|ficam|e|sao|segue|seguem|continua|continuam)\s+)?"
    r"(?:cobert[oa]s?|protegid[oa]s?|amparad[oa]s?|segurad[oa]s?|garantid[oa]s?)\s+(?:\w+\s+){0,2}?"
    rf"pel[oa]s?\s+(?:propri[oa]\s+|mesm[oa]\s+)?{_TERCEIRO}\b"
    # ...menos "coberto pelo programa/pela política DE GARANTIA (da loja)"
    r"(?!\s+(?:d[aeo]\s+)?garantia\b)"
    # "o seguro (da transportadora) cobre", "a transportadora cobre o extravio"
    rf"|\b{_TERCEIRO}\s+(?:(?!garantia\b)\w+\s+){{0,3}}?(?:cobre|cobrem|cobrira|cobriria)\b"
    # "tem cobertura do seguro", "com cobertura da transportadora"
    rf"|\b(?:(?:tem|com)\s+)?cobertura\s+(?:d[aeo]s?|pel[oa]s?)\s+(?:propri[oa]\s+)?{_TERCEIRO}\b"
)
# "Garantido" do envio/pagamento ("a entrega é garantida", "seu reembolso
# está garantido") não é a garantia do aparelho: vira "assegurado" na forma
# que as conferências leem.
_GARANTIDO_DE_OUTRO = re.compile(
    r"(\b(?:entregas?|envios?|chegada|recebimento|compras?|pedidos?|encomendas?|pacotes?"
    r"|pagamentos?|reembolsos?|estornos?"
    r"|dinheiro|frete|devolucao|prazo)\s+(?:\w+\s+){0,2}?)garantid([oa]s?)\b"
    # "a entrega é garantida, assim como o aparelho": o aparelho também.
    r"(?!\s*,?\s*(?:assim\s+como|bem\s+como|como\s+tambem|e\s+tambem|tambem)\b)"
)


# "A transportadora tem cobertura na sua cidade": a área de entrega.
_COBERTURA_DA_REGIAO = re.compile(
    r"\bcobertura\s+(?:n[ao]s?|em|para|pra)\s+(?:\w+\s+)?(?:regiao|cidade|area|cep|bairro"
    r"|endereco|localidade|estado)\b"
)


def _sem_protecao_de_terceiros(plano: str) -> str:
    """O texto sem a proteção da plataforma e a de terceiros (seguro,
    transportadora, Correios, a área de entrega) e sem o "garantido" do
    envio."""
    plano = _COBERTO_POR_TERCEIRO.sub(" ", _GARANTIA_DA_PLATAFORMA.sub(" ", plano))
    plano = _COBERTURA_DA_REGIAO.sub(" ", plano)
    return _GARANTIDO_DE_OUTRO.sub(r"\1assegurad\2", plano)


# Número de documento não é ano: "a nota fiscal 2045", "NF nº 000.002.026". A
# data ("pedido: 07/10/2026") continua data.
_NUMERO_DE_DOCUMENTO = re.compile(
    r"(\b(?:nota\s+fiscal|nf-?e?|nfs|danfe|pedidos?|rastreio|codigo|numero|serie|chave)\b"
    r"\s*(?:(?:n[o°]\.?|numero|num\.?|e|eh|#|:|-)\s*){0,3})(\d+(?:\.\d{3})*)(?!\d|[/.-]\d)"
)

_COBERTURA = (
    # "está coberto", "tá na garantia", "ainda está na garantia", "segue
    # dentro do prazo de garantia", "está no período de garantia"
    re.compile(
        r"\b(?:esta|estao|ta|tah|tao|tava|continua|continuam|segue|seguem|permanece|permanecem"
        r"|fica|ficam)\s+"
        r"(?:\w+\s+){0,3}?(?:cobert[oa]s?|na\s+garantia|em\s+garantia"
        r"|dentro\s+d[oa]\s+(?:prazo\s+d[ae]\s+)?garantia|amparad[oa]s?\s+pela\s+garantia"
        r"|n[oa]\s+(?:prazo|periodo)\s+d[ae]\s+garantia)\b"
    ),
    # "coberto pela garantia"
    re.compile(r"\bcobert[oa]s?\s+(?:\w+\s+){0,2}?(?:pela|na)\s+garantia\b"),
    # "a garantia cobre", "a garantia vai cobrir", "a garantia resolve"
    re.compile(
        r"\bgarantia\s+(?:\w+\s+){0,3}?(?:cobre|cobrem|cobrira|cobriria|vai\s+cobrir"
        r"|ira\s+cobrir|resolve|resolvera|vai\s+resolver)\b"
    ),
    # "entra na garantia", "cobrimos pela garantia", "a gente cobre pela
    # garantia", "fazemos a troca pela garantia", "dentro da garantia"
    re.compile(r"\bentra(?:m|ria)?\s+na\s+garantia\b"),
    re.compile(r"\b(?:cobrimos|cobre|cobrem)\s+(?:\w+\s+){0,2}?(?:pela|na)\s+garantia\b"),
    re.compile(
        r"\b(?:troca|trocamos|trocar|reparo|reparamos|reparar|conserto|consertamos|consertar)"
        r"\s+(?:\w+\s+){0,2}?(?:pela|na)\s+garantia\b"
    ),
    re.compile(r"\bdentro\s+da\s+garantia\b"),
    # "protegido pela garantia", "dentro da validade", "está na validade"
    re.compile(
        r"\b(?:protegid|amparad|resguardad)[oa]s?\s+(?:\w+\s+){0,2}?(?:pela|na)\s+garantia\b"
    ),
    re.compile(r"\bdentro\s+d[aoe]\s+(?:\w+\s+){0,2}?(?:validade|vigencia)\b"),
    re.compile(
        r"\b(?:esta|estao|ta|segue|continua)\s+(?:\w+\s+){0,2}?n[ao]\s+(?:validade|vigencia)\b"
    ),
    # "esse defeito é coberto", "o problema fica coberto", "seu caso está garantido"
    re.compile(
        r"\b(?:defeitos?|tela|bateria|problemas?|isso|esse|essa|caso|conserto|reparo|troca)\s+"
        r"(?:\w+\s+){0,3}?(?:e|esta|ta|fica|sera|seria)\s+(?:\w+\s+)?(?:cobert[oa]s?|garantid[oa]s?)\b"
    ),
    # "a gente cobre defeito de fábrica", "cobrimos", "resolvemos pela garantia"
    re.compile(
        r"\b(?:(?:a\s+gente|nos|a\s+loja)\s+(?:\w+\s+){0,2}?(?:cobre|garante)|cobrimos|garantimos)"
        r"\s+(?:\w+\s+){0,2}?(?:defeit\w*|conserto|reparo|troca)\b"
    ),
    re.compile(
        r"\b(?:resolve|resolvemos|resolver|resolvido)\s+(?:\w+\s+){0,2}?(?:pela|na)\s+garantia\b"
    ),
    # "a garantia se aplica", "a garantia te atende", "a garantia está de pé"
    re.compile(r"\bgarantia\s+(?:\w+\s+){0,3}?(?:se\s+aplica|atende|esta\s+de\s+pe)\b"),
    # "fique tranquilo que está garantido" (o do envio já virou "assegurado")
    re.compile(r"\b(?:esta|estao|ta|segue|continua)\s+(?:\w+\s+)?garantid[oa]s?\b"),
    # "ainda dá tempo de acionar a garantia", "garantia ok", "está tudo certo
    # com a garantia"
    re.compile(
        r"\bda\s+tempo\s+de\s+(?:acionar|usar|utilizar)\s+(?:a\s+(?:sua\s+)?|sua\s+)?garantia\b"
    ),
    re.compile(r"\bgarantia\s+(?:\w+\s+)?(?:ok|okay|em\s+ordem)\b"),
    re.compile(r"\b(?:certo|certinho|ok|em\s+ordem)\s+com\s+a\s+(?:sua\s+)?garantia\b"),
    # Na ordem invertida: "defeito de fábrica a gente cobre", "a tela a loja
    # resolve", "assistência da fábrica cobre"
    re.compile(
        r"\b(?:defeit\w*|conserto|reparo|troca|tela|bateria)\s+(?:\w+\s+){0,3}?"
        r"(?:a\s+gente|nos|a\s+loja|a\s+garantia|a\s+fabrica|o\s+fabricante)\s+(?:\w+\s+)?"
        r"(?:cobre|cobrimos|garante|garantimos|resolve|resolvemos)\b"
    ),
    re.compile(
        r"\b(?:assistencia|fabrica|fabricante)\s+(?:\w+\s+){0,2}?(?:cobre|cobrem|cobrira)\b"
    ),
    # "está valendo a garantia", "pode mandar que cobre", "tem cobertura" —
    # não a da transportadora na região ("tem cobertura na sua cidade").
    re.compile(r"\b(?:esta|ta|segue|continua)\s+valendo\s+a\s+garantia\b"),
    re.compile(r"\bpode\s+mandar\s+que\s+(?:\w+\s+){0,2}?cobre\b"),
    re.compile(
        r"\btem\s+cobertura\b(?!\s+(?:n[ao]s?|em|para|pra)\s+(?:\w+\s+)?(?:regiao|cidade|area"
        r"|cep|bairro|endereco|localidade|estado)\b)"
    ),
    # "seu aparelho é coberto", "ele está garantido", "o celular segue garantido"
    re.compile(
        r"\b(?:aparelho|celular|produto|ele|ela)\s+(?:\w+\s+){0,2}?(?:e|esta|ta|segue|continua)"
        r"\s+(?:cobert[oa]|garantid[oa])\b"
    ),
    # "sua garantia está ativa", "a garantia é válida até", "segue valendo",
    # "está garantida"
    re.compile(
        r"\bgarantia\s+(?:\w+\s+){0,3}?(?:esta|segue|continua|permanece|e|eh)\s+(?:\w+\s+)?"
        r"(?:ativa|valida|vigente|em\s+vigor|em\s+dia|valendo|garantida)\b"
    ),
    # "a garantia vale", "a garantia ainda vale até…" — não "vale a partir da
    # entrega"/"vale por"/"vale para defeito de fábrica" (é a regra, não a
    # promessa de que ESTA compra está coberta).
    re.compile(
        r"\bgarantia\s+(?:\w+\s+){0,3}?(?:vale|valera|valendo)\b(?!\s+(?:a\s+partir|apos|depois"
        r"|desde|so|somente|apenas|por|contando|quando|para|pra|assim|d[ao]\s+entrega"
        r"|n[ao]\s+entrega)\b)"
    ),
    # "você tem direito à garantia", "ainda tem garantia", "tem garantia sim",
    # "ele tem garantia"
    re.compile(
        r"\b(?:tem|tera|possui|possuem|possuira)\s+(?:sim\s+)?(?:direito\s+(?:a|ao)\s+)?"
        r"(?:a\s+|sua\s+)?garantia\b"
    ),
    # "pode acionar a garantia"
    re.compile(
        r"\b(?:pode|podera|podemos|vamos|iremos|vai|consegue)\s+(?:\w+\s+)?"
        r"(?:acionar|usar|utilizar)\s+(?:a\s+(?:sua\s+)?|sua\s+)?garantia\b"
    ),
)
# "Sua garantia ainda não venceu", "não expirou a garantia": a negação do
# vencimento É a promessa — conferida sem o filtro de negação de dentro.
_COBERTURA_POR_NEGACAO = (
    re.compile(
        r"\b(?:garantia|validade)\s+(?:\w+\s+){0,3}?nao\s+(?:\w+\s+)?(?:venceu|vence|vencera"
        r"|expirou|expira|expirara|acabou|acaba|terminou|termina|caducou)\b"
    ),
    re.compile(
        r"\bnao\s+(?:\w+\s+)?(?:venceu|expirou|acabou|terminou|caducou)\s+(?:a\s+|sua\s+)?"
        r"(?:garantia|validade)\b"
    ),
)
# Antes da promessa (até 5 palavras) ou dentro dela: negação ("não está
# mais coberto") ou condição/verificação ("vamos verificar se ainda está na
# garantia") — não é promessa.
_NEGACAO = {"nao", "nem", "nunca", "jamais"}
_CONDICAO = {
    "se",
    "caso",
    "verificar",
    "verificaremos",
    "verificamos",
    "conferir",
    "conferimos",
    "confirmar",
    "confirmaremos",
    "checar",
    "saber",
    "analisar",
    "analisaremos",
    "avaliar",
    "consultar",
}
# Logo antes da promessa (até 3 palavras): o que está coberto é o frete, a
# devolução, o custo, a encomenda na transportadora — não o aparelho ("o
# frete está coberto pela loja", "a transportadora tem cobertura").
_NAO_E_O_APARELHO = {
    "transportadora",
    "correios",
    "seguro",
    "seguradora",
    "encomenda",
    "pacote",
    "frete",
    "envio",
    "postagem",
    "devolucao",
    "reembolso",
    "custo",
    "custos",
    "taxa",
    "taxas",
    "cupom",
    "desconto",
}
_HARDWARE = re.compile(r"\bhardware\b")
# Na frase da promessa, o que é do APARELHO mesmo que a frase diga "software"
# ("garantia de software até X, pode mandar o vídeo do defeito").
_DO_APARELHO = re.compile(
    r"\b(?:hardware|defeit\w*|tela|bateria|camera|carregad\w*|conserto|consert\w*|repar\w*"
    r"|quebr\w*|placa|alto-?falante|microfone|botao|botoes)\b"
)
_DEFEITO_DE_SOFTWARE = re.compile(r"\bdefeitos?\s+(?:de|no|do|d[eo]\s+seu)\s+software\b")
_SOFTWARE = re.compile(r"\bsoftware\b")
# "hardware e (o) software" / "software e (o) hardware": a data que vem
# depois é dos dois — tem de ser o fim do hardware E o do software.
_AS_DUAS_PARTES = re.compile(
    r"\b(?:hardware|software)\s+(?:e|quanto)\s+(?:o\s+|a\s+)?(?:d[aeo]\s+)?(?:hardware|software)\b"
)
# "mais que a de hardware", "menor do que o hardware": a parte comparada não
# é a da data.
_COMPARACAO = re.compile(
    r"\b(?:mais|menos|maior|menor)\s+(?:\w+\s+)?(?:do\s+)?que\s+(?:[ao]\s+)?(?:d[aeo]\s+)?$"
)

# "A garantia vai até o dia 07/01/2027": o "até o dia N" da regra de PRAZO é
# promessa de ENTREGA. Numa frase de garantia, com a data COMPLETA, ele sai
# da forma que a regra de prazo lê — a data é conferida contra o bloco por
# `conferir_garantia` (a IA só monta a sugestão com as duas).
_ATE_O_DIA_COM_DATA = re.compile(
    r"\bate\s+(?:o\s+)?dia\s+(?="
    r"\d{1,2}\s?[/.-]\s?\d{1,2}\s?[/.-]\s?\d{2,4}"
    rf"|\d{{1,2}}{_ORDINAL}\s*(?:de\s+)?(?:{_MES}|{_MES_CURTO})\b\.?(?:\s*(?:de|/|,|-)\s*|\s+)"
    r"\d{4})"
)
_SEPARA_FRASES = re.compile(r"((?<=[.!?;])\s+|\n+)")


def _sem_ate_o_dia_da_garantia(plano: str) -> str:
    """O texto sem o "até o dia <data completa>" cujo ASSUNTO é a garantia
    ("a garantia vai até o dia 07/01/2027"). O de entrega fica, mesmo numa
    frase que cita a garantia: "chega até o dia 15/10/2026 e está coberto
    pela Garantia Shopee" continua prazo (a proteção da plataforma sai antes;
    o assunto de "até o dia" é "chega")."""

    def tira(p: str) -> str:
        return _ATE_O_DIA_COM_DATA.sub(
            lambda m: "ate " if _de_que_e(p[: m.start()], 0, p)[0] == "garantia" else m.group(0),
            p,
        )

    partes = _SEPARA_FRASES.split(_sem_protecao_de_terceiros(plano))
    return "".join(tira(p) for p in partes)


# ── O prazo da REGRA de garantia, no AGENTE (20/10/2026) ──────────────────
#
# Esclarecimento 1 do orquestrador (fontes v1.3): o dono mandou usar o
# cálculo do Painel de Garantia. No caminho do agente, os prazos que vieram
# de `ver_garantia` (a regra: 3 meses de hardware e 12 de software da
# Uranyx, a partir da entrega; os 90 dias da Charlots, do fato aprovado da
# marca) podem ser escritos — só eles, e só na frase que fala da garantia.
# As datas continuam com `conferir_garantia` (do bloco, no lugar certo).
# Revisão de 20/10/2026: cada número só com o SEU par, na mesma oração — os 3
# meses com "hardware/aparelho", os 12 com "software/sistema", os 90 dias com
# "garantia/defeito" —; "a garantia do aparelho é de 12 meses" e "a garantia
# é de 3 meses e a entrega leva 12 meses" continuam barrados.
# Revisão r5i de 21/10/2026 (decisões 6 a 8 do dono, só o agente): a garantia
# Uranyx de 1 ano — "1 ano de garantia" (com "garantia"), os 90 dias com "loja"
# e os 9 meses com "Uranyx" na mesma oração ("os 90 primeiros dias com a loja e
# os 9 meses seguintes direto com a Uranyx").
PARTE_HARDWARE, PARTE_SOFTWARE, PARTE_GERAL = "hardware", "software", "geral"
PARTE_LOJA, PARTE_URANYX = "loja", "uranyx"
_CHAVES_DA_REGRA = (
    ("hardware_meses", "mes", PARTE_HARDWARE),
    ("software_meses", "mes", PARTE_SOFTWARE),
    ("dias", "dia", PARTE_GERAL),
    ("garantia_anos", "ano", PARTE_GERAL),
    ("loja_dias", "dia", PARTE_LOJA),
    ("uranyx_meses", "mes", PARTE_URANYX),
)
_POR_EXTENSO = {
    1: "um",
    2: "dois",
    3: "tres",
    4: "quatro",
    5: "cinco",
    6: "seis",
    7: "sete",
    8: "oito",
    9: "nove",
    10: "dez",
    11: "onze",
    12: "doze",
    15: "quinze",
    18: "dezoito",
    24: "vinte e quatro",
    30: "trinta",
    60: "sessenta",
    90: "noventa",
    180: "cento e oitenta",
}


def prazos_da_regra(garantias: Sequence[Mapping] | None) -> frozenset[tuple[int, str, str]]:
    """Os prazos (número, "mes"|"dia", parte) da `regra` de cada garantia de `ver_garantia`. PURA.

    `{"hardware_meses": 3, "software_meses": 12, ...}` → {(3, "mes",
    "hardware"), (12, "mes", "software")}; `{"dias": 90, ...}` → {(90,
    "dia", "geral")}; sem regra (outra linha, o registro do Painel sem
    regra) → nada.
    """
    prazos: set[tuple[int, str, str]] = set()
    for g in garantias or ():
        regra = g.get("regra") if isinstance(g, Mapping) else None
        if not isinstance(regra, Mapping):
            continue
        for chave, unidade, parte in _CHAVES_DA_REGRA:
            n = regra.get(chave)
            if isinstance(n, int) and not isinstance(n, bool) and 0 < n < 1000:
                prazos.add((n, unidade, parte))
    return frozenset(prazos)


def _prazo_da_regra(n: int, unidade: str) -> re.Pattern[str]:
    numero = rf"(?:{n}|{_POR_EXTENSO[n]})" if n in _POR_EXTENSO else str(n)
    nome = {"mes": r"(?:mes|meses)", "ano": r"anos?"}.get(unidade, r"dias?")
    # "os 90 primeiros dias" (revisão r5i, a garantia Uranyx).
    return re.compile(rf"(?<![\d.,]){numero}\s*(?:primeiros\s+)?{nome}\b")


# A oração dentro da frase: a vírgula que abre OUTRO sujeito (", a troca", ",
# e o reembolso", ", se"), ponto e vírgula, "mas", e o "e" que abre outro
# sujeito ("e a entrega"); "3 meses de hardware e 12 de software" e "e a de
# software, de 12 meses" ficam juntos.
_ORACAO_DA_REGRA = re.compile(
    r",(?=\s*(?:(?:e|mas|porem)\s+)?(?:o|a|os|as|seu|sua|seus|suas|se|quando|que|ja|ele|ela)\b)"
    r"|;|\bmas\b|\bporem\b|\be\s+(?=(?:o|a|os|as|seu|sua|seus|suas)\b)"
)
_PALAVRA_DA_PARTE = (
    (PARTE_HARDWARE, r"hardware|aparelho|fisic\w*|pecas?|componentes?"),
    (PARTE_SOFTWARE, r"software|sistema"),
    (PARTE_LOJA, r"loja"),
    (PARTE_URANYX, r"uranyx"),
)
# Item 2 da auditoria ao vivo (22/10/2026 22:09, Barbosa Shopee, 3ee1f81e): o verbo de
# ligação entre o número e o par ("os primeiros 90 dias SÃO com a loja e os 9 meses
# seguintes SÃO direto com a Uranyx": 5 palavras até o par) — só no agente (o legado
# nunca tem prazo da regra).
_PARTE_DA_REGRA_DEPOIS = re.compile(
    r"\s+(?:(?:de|da|do|para|pro|pra|no|na|em|o|a|garantia|cobertura|com|seguintes|direto"
    r"|sao|serao|fica|ficam)\s+){0,5}(?P<parte>"
    + "|".join(p for _n, p in _PALAVRA_DA_PARTE)
    + r")\b"
)
_PARTE_DA_REGRA_ANTES = re.compile(r"\b(?:" + "|".join(p for _n, p in _PALAVRA_DA_PARTE) + r")\b")
_DA_GARANTIA = re.compile(r"\b(?:garantias?|defeitos?|fabrica\w*|cobert\w*)\b")
# O demonstrativo que RETOMA o prazo já dito ("nesses primeiros 90 dias", "desses 9 meses").
_RETOMA_O_PRAZO = re.compile(
    r"\b(?:n|d)?(?:esses|estes|essas|estas)\s+(?:(?:primeiros|ultimos|seguintes)\s+)?$"
)
# O prazo escrito num FATO APROVADO ("1 ano de garantia", "os 90 primeiros dias com a loja",
# "garantia de 90 dias contra defeitos").
_PRAZO_DO_FATO = re.compile(
    r"(?<![\d.,])(?P<n>\d{1,3})\s*(?:primeiros\s+)?(?P<u>dias?|mes|meses|anos?)\b"
)
# Revisão do fechamento da rodada "redondo" (23/10/2026): o prazo vindo de FATO leva a marca
# `DO_FATO` na parte ("fato:loja", "fato:geral:apple") — `_sem_prazo_da_regra` o confere
# mais estrito que o da regra do `ver_garantia`, e `conferir_garantia` nunca pula a
# promessa por ele (só a regra da linha antes da compra pula). O prazo GERAL de fato (sem
# par no próprio fato: "garantia Apple de 1 ano", "1 ano de garantia") só vale na oração
# da garantia que (a) não fala da loja ("1 ano com a loja" com o fato 25 da Apple), (b) não
# dá outro par logo depois do número ("1 ano direto com a Uranyx") e (c) diz a MARCA que o
# fato diz (`_MARCAS_DO_FATO`: o fato 25 da Kia, "produtos Apple", não vale para "o celular
# tem 1 ano de garantia").
DO_FATO = "fato:"
_MARCAS_DO_FATO = (
    "apple",
    "uranyx",
    "charlots",
    "samsung",
    "xiaomi",
    "motorola",
    "doogee",
    "oukitel",
    "blackview",
    "fossibot",
)
_FALA_DA_LOJA = re.compile(r"\bloja\b")


def _marcas_do_texto(plano: str) -> list[str]:
    return [m for m in _MARCAS_DO_FATO if re.search(rf"\b{m}\b", plano)]


def _parte_do_fato(parte: str) -> tuple[str, tuple[str, ...]] | None:
    """("geral"|"loja"|…, marcas) da parte de um prazo de FATO; None = prazo da regra."""
    if not parte.startswith(DO_FATO):
        return None
    base, *marcas = parte[len(DO_FATO) :].split(":")
    return base, tuple(m for m in marcas if m)


def _geral_do_fato_vale(oracao: str, m: re.Match[str], marcas: Sequence[str]) -> bool:
    """O prazo GERAL de um fato vale nesta oração (ver acima)? PURA. A loja conta até o
    próximo prazo ou os dois-pontos ("1 ano de garantia: os 90 primeiros dias com a loja…"
    — a loja ali é do par dos 90 dias)."""
    if not _DA_GARANTIA.search(oracao):
        return False
    fim = len(oracao)
    if (dois_pontos := oracao.find(":", m.end())) >= 0:
        fim = dois_pontos
    if proximo := _PRAZO_DO_FATO.search(oracao, m.end()):
        fim = min(fim, proximo.start())
    if _FALA_DA_LOJA.search(oracao[:fim]):
        return False
    if _PARTE_DA_REGRA_DEPOIS.match(oracao, m.end()):
        return False
    return all(re.search(rf"\b{marca}\b", oracao) for marca in marcas)


def so_da_regra(prazos: Collection[tuple[int, str, str]]) -> frozenset[tuple[int, str, str]]:
    """Os prazos da REGRA do `ver_garantia` (sem os de fato). PURA."""
    return frozenset(p for p in prazos if not str(p[2]).startswith(DO_FATO))


_FATO_DA_GARANTIA_URANYX = re.compile(r"\buranyx\b.*\bgarantia\b|\bgarantia\b.*\buranyx\b")


def prazos_dos_fatos(textos: Iterable[str | None]) -> frozenset[tuple[int, str, str]]:
    """Os prazos (número, "dia"|"mes"|"ano", parte) dos FATOS APROVADOS citados, cada um
    com o par que o PRÓPRIO fato dá. PURA. Só o agente usa (o legado não cita fato).

    Item 2 da auditoria ao vivo (22/10/2026 22:09, Barbosa Shopee, 3ee1f81e): o agente
    citou o fato 23 ("Aparelhos Uranyx … têm 1 ano de garantia: os 90 primeiros dias com
    a loja e os 9 meses seguintes direto com a Uranyx") e o validador barrava "prazo de
    garantia só pode vir do sistema" — o número de fato aprovado citado é do sistema. O
    par vem da palavra logo depois do número no fato ("90 primeiros dias com a loja" →
    loja; "9 meses seguintes direto com a Uranyx" → uranyx) e, sem ela, da garantia na
    oração ("1 ano de garantia" → geral); número sem par nenhum não entra. No rascunho,
    cada número só sai do que o validador confere com o MESMO par, na frase da garantia
    (`_sem_prazo_da_regra`): "a garantia é de 90 dias" com o fato da Uranyx continua
    barrado.
    """
    prazos: set[tuple[int, str, str]] = set()
    unidades = {"dia": "dia", "dias": "dia", "mes": "mes", "meses": "mes", "ano": "ano"}
    for texto in textos:
        if not isinstance(texto, str) or not texto.strip():
            continue
        plano = plano_de(texto)
        # O dono, 27/10/2026 (a garantia como a equipe responde): o prazo do fato da garantia
        # Uranyx ("1 ano: 90 dias com a loja e 9 meses com a Uranyx") não livra mais nada —
        # o fato fica fora até ser desativado ou reescrito no banco.
        if _FATO_DA_GARANTIA_URANYX.search(plano):
            continue
        # Revisão (23/10/2026): a marca que o fato diz vai com o prazo geral dele.
        marcas = "".join(f":{m}" for m in _marcas_do_texto(plano))
        for frase in _SEPARA_FRASES.split(plano):
            for oracao in _ORACAO_DA_REGRA.split(frase):
                for m in _PRAZO_DO_FATO.finditer(oracao):
                    n, unidade = int(m["n"]), unidades.get(m["u"], "ano")
                    if not 0 < n < 1000:
                        continue
                    if depois := _PARTE_DA_REGRA_DEPOIS.match(oracao, m.end()):
                        prazos.add((n, unidade, DO_FATO + _parte_de(depois["parte"])))
                    elif _DA_GARANTIA.search(oracao):
                        prazos.add((n, unidade, f"{DO_FATO}{PARTE_GERAL}{marcas}"))
    return frozenset(prazos)


def _parte_de(palavra: str) -> str:
    for parte, padrao in _PALAVRA_DA_PARTE:
        if re.fullmatch(padrao, palavra):
            return parte
    return PARTE_GERAL


def _parte_do_numero(oracao: str, m: re.Match[str]) -> str | None:
    """A parte (hardware/software) a que o número pertence: a palavra logo
    depois ("3 meses para hardware") ou a última antes na oração ("a garantia
    de hardware é de 3 meses"); None = nenhuma."""
    if depois := _PARTE_DA_REGRA_DEPOIS.match(oracao, m.end()):
        return _parte_de(depois["parte"])
    antes = list(_PARTE_DA_REGRA_ANTES.finditer(oracao, 0, m.start()))
    return _parte_de(antes[-1].group(0)) if antes else None


def _sem_prazo_da_regra(plano: str, prazos: Collection[tuple[int, str, str]]) -> str:
    """O texto sem os prazos da regra NA FRASE QUE FALA DA GARANTIA, cada um
    com o SEU par na mesma oração ("a garantia é de 3 meses para hardware e
    12 meses para software"; os 90 dias com "garantia"/"defeito"). O mesmo
    número noutra frase ("chega em 3 meses"), noutra oração ("…, e o
    reembolso sai em 3 meses") ou com o par errado ("a garantia do aparelho
    é de 12 meses") continua prazo."""
    regras = [(_prazo_da_regra(n, u), parte, (n, u)) for n, u, parte in sorted(prazos)]
    # Item 2 da auditoria ao vivo (22/10/2026, 3ee1f81e): o mesmo prazo RETOMADO numa frase
    # seguinte da garantia ("se precisar acionar a garantia nesses primeiros 90 dias")
    # depois de dito com o seu par — só com o demonstrativo logo antes do número.
    ditos: set[tuple[int, str]] = set()

    def na_oracao(oracao: str) -> str:
        cortes: list[tuple[int, int]] = []
        for padrao, parte, chave in regras:
            do_fato = _parte_do_fato(parte)
            for m in padrao.finditer(oracao):
                if do_fato is not None and do_fato[0] == PARTE_GERAL:
                    # Revisão (23/10/2026): o prazo geral de FATO, mais estrito.
                    vale = _geral_do_fato_vale(oracao, m, do_fato[1])
                elif do_fato is not None:
                    vale = _parte_do_numero(oracao, m) == do_fato[0]
                elif parte == PARTE_GERAL:
                    vale = bool(_DA_GARANTIA.search(oracao))
                else:
                    vale = _parte_do_numero(oracao, m) == parte
                if not vale and chave in ditos and _RETOMA_O_PRAZO.search(oracao[: m.start()]):
                    vale = True
                if vale:
                    cortes.append(m.span())
                    ditos.add(chave)
        # O mesmo número pela regra E pelo fato (revisão de 23/10/2026: "fato:loja" e
        # "loja"): cada trecho sai uma vez só (o corte repetido comia o texto ao lado).
        fim_anterior = None
        for inicio, fim in sorted(set(cortes), reverse=True):
            if fim_anterior is not None and fim > fim_anterior:
                continue
            oracao = oracao[:inicio] + " " + oracao[fim:]
            fim_anterior = inicio
        return oracao

    def tira(frase: str) -> str:
        if not _FRASE_GARANTIA.search(frase):
            return frase
        partes = _ORACAO_DA_REGRA.split(frase)
        separadores = _ORACAO_DA_REGRA.findall(frase)
        saida = na_oracao(partes[0])
        for sep, oracao in zip(separadores, partes[1:], strict=True):
            saida += sep + na_oracao(oracao)
        return saida

    return "".join(tira(p) for p in _SEPARA_FRASES.split(plano))


# ── O que só o AGENTE confere na garantia e no defeito (revisão de 20/10/2026) ──
#
# A regra 7 e a spec 6.4: nunca dizer que não tem garantia, que venceu, que
# acabou ou que está fora dela — nem com o status do Painel "expirada" (é
# pessoa quem decide, e o CDC vale). E nunca sugerir reset ou formatação (o
# comprador perde os dados). No legado, toda fala de garantia já ia para
# pessoa; no agente, a fala que sai de `ver_garantia` não vai — por isso a
# trava própria.
MOTIVO_NEGA_GARANTIA = "nega a garantia ou a dá como vencida — só pessoa"
MOTIVO_SUGERE_RESET = "sugere reset ou formatação (apaga os dados) — só pessoa"
_NEGA_GARANTIA = re.compile(
    r"\bsem\s+garantia\b(?!\s+(?:estendida|adicional|extra)\b)"
    r"|\b(?:nao|nem)\s+(?:tem|possui|ha|oferece\w*|cobre|cobrimos|da|conta\s+com|vale|existe)"
    r"\s+(?:mais\s+)?(?:\w+\s+){0,2}?garantia\b"
    r"|\bgarantia\s+(?:\w+\s+){0,8}?(?:ja\s+)?(?:venceu|vencida|expirou|expirada|acabou|terminou"
    r"|encerrou|encerrada|esgotou|caducou|perdeu|perdida|nao\s+(?:cobre|vale|esta\s+(?:ativa"
    r"|valendo|vigente)|e\s+mais\s+valida))\b"
    r"|\bfora\s+d[ao]\s+(?:prazo\s+d[ae]\s+|periodo\s+d[ae]\s+)?garantia\b"
    r"|\bperd\w+\s+(?:a\s+)?garantia\b"
    r"|\b(?:nao|nem)\s+(?:conseguimos|podemos|vamos)\s+(?:\w+\s+){0,2}?(?:ajudar|atender|trocar"
    r"|consertar)\s+(?:\w+\s+){0,3}?(?:defeito|garantia)\b"
    # Revisão 2 de 20/10/2026: a cobertura recusada sem a palavra "garantia" e
    # as paráfrases comuns ("não é coberto", "não entra na garantia", "não há
    # cobertura", "mau uso anula a garantia", "passou do prazo de garantia",
    # "a garantia de hardware não está mais valendo", "o status é expirada").
    r"|\b(?:nao|nem)\s+(?:e|esta|sao|estao|fica|ficam)\s+(?:mais\s+)?cobert[oa]s?\b"
    r"|\b(?:nao|nem)\s+(?:tem|ha|possui|existe|conta\s+com|oferece)\s+(?:mais\s+)?cobertura\b"
    r"|\b(?:nao|nem)\s+(?:entra|entram|se\s+enquadra\w*|esta|estao|e|sao)\s+(?:mais\s+)?"
    r"(?:na|pela|dentro\s+da|coberto\s+pela)\s+garantia\b"
    r"|\b(?:anula|anulam|anular|invalida|invalidam|invalidar|cancela|cancelam|tira|tiram)\s+"
    r"(?:a\s+|sua\s+|a\s+sua\s+)?garantia\b"
    r"|\bpassou\s+(?:do\s+)?(?:prazo|periodo)\s+(?:de\s+|da\s+)?(?:garantia|cobertura)\b"
    r"|\bgarantia\s+(?:\w+\s+){0,4}?(?:nao|nem)\s+(?:esta|e)\s+mais\s+(?:ativa|valendo|vigente"
    r"|valida)\b"
    r"|\b(?:status|situacao|painel|prazo|periodo|cobertura|garantia)\s+(?:\w+\s+){0,4}?"
    r"(?:expirad[oa]|vencid[oa])\b"
    # Revisão "furos": a passiva ("a garantia é anulada", "fica invalidada").
    r"|\bgarantia\s+(?:(?!nao\b|nem\b)\w+\s+){0,3}?(?:e|fica|sera|esta|seria)\s+"
    r"(?:automaticamente\s+)?"
    r"(?:anulad|invalidad|cancelad|suspens|perdid)[oa]\b"
)
# "Ainda não venceu", "não expirou": a garantia VALE (é promessa, não negação).
_NEGACAO_DO_VENCIMENTO = re.compile(
    r"\b(?:nao|nem)\s+(?:\w+\s+)?(?:venceu|expirou|acabou|terminou|encerrou|esgotou|caducou"
    r"|passou|anula|invalida|perde|cancela)\b"
)
# O PRAZO ou a COBERTURA que acabou, numa frase de garantia (revisão 2 de
# 20/10/2026: "a cobertura de hardware já se encerrou", "o prazo para acionar
# já se esgotou", "o prazo de 3 meses de hardware já se encerrou") — "o prazo
# de entrega já passou" não é de garantia e fica de fora.
_PRAZO_QUE_ACABOU = re.compile(
    r"(?:\b(?:prazo|periodo)\s+(?:\w+\s+){0,5}?(?:garantia|cobertura|acionar\w*|hardware"
    r"|software)\b|\b(?:cobertura|vigencia)\b)\s+(?:\w+\s+){0,5}?(?:ja\s+)?(?:se\s+)?"
    r"(?:encerrou|encerrad[oa]|passou(?!\s+a\b)|venceu|vencid[oa]|acabou|terminou|esgotou"
    r"|expirou|expirad[oa]|caducou)\b"
)
_RESET = re.compile(
    r"\b(?:reset\w*|hard\s*reset|formata\w*|formatar|formate|wipe)\b"
    r"|\brestaur\w*\s+(?:(?:o|a|seu|sua)\s+)?(?:(?:as\s+)?(?:configurac\w+|padroes?)\s+)?"
    r"(?:de\s+|d[ao]s?\s+|para\s+(?:o\s+)?)?(?:padrao\s+(?:de\s+)?)?"
    r"(?:fabrica|aparelho|celular|relogio|telefone|smartphone|sistema)\b"
    r"|\bredefini\w*\s+(?:\w+\s+){0,4}?(?:fabrica|padrao)\b"
    r"|\bapagar\s+tudo\b|\bmodo\s+de\s+recupera\w*|\blimpar\s+(?:todos\s+os\s+)?dados\b"
    # Revisão 2 de 20/10/2026: "volte o aparelho às configurações de fábrica",
    # "apague os dados do aparelho e configure de novo".
    r"|\bconfigurac\w+\s+(?:de\s+|da\s+|originais\s+de\s+)?fabrica\b"
    r"|\b(?:volt\w*|retorn\w*|deix\w*)\s+(?:\w+\s+){0,4}?(?:a|as|ao|de|da|para\s+(?:a|o))\s+"
    r"(?:configurac\w+\s+(?:de\s+)?)?fabrica\b"
    r"|\bapag\w*\s+(?:\w+\s+){0,2}?dados\b|\bzer(?:ar|e|ando)\s+o\s+(?:aparelho|celular|relogio)\b"
    # Revisão "furos" de 20/10/2026: "volte para as configurações originais",
    # "apague tudo", "restaure o padrão", "redefina o telefone", "comece do
    # zero", "modo recovery", "opções de redefinição", "remova a conta Google".
    r"|\bconfigurac\w+\s+(?:originais|iniciais|de\s+origem)\b"
    r"|\b(?:apag|limp|delet|exclu|remov)\w*\s+tudo\b|\blimp\w*\s+(?:o\s+|seu\s+)?(?:aparelho"
    r"|celular|relogio|telefone|sistema)\b"
    r"|\brestaur(?!ante)\w*\b|\bredefin\w*\b|\b(?:comec|recomec|configur)\w*\s+(?:\w+\s+)?do\s+zero\b"
    r"|\brecovery\b|\bremov\w*\s+(?:a\s+)?(?:sua\s+)?conta\s+(?:do\s+)?google\b"
)
# A RECUSA de troca, conserto ou cobertura sem a palavra "garantia" (revisão
# "furos": "o prazo para troca já acabou", "não conseguimos mais trocar o
# aparelho", "nesse caso não é possível a troca", "hoje só vale para
# software", "está fora do prazo"). Só o agente usa (`recusa_cobertura`). A
# recusa padrão aprovada da troca pagando a diferença não conta, nem o prazo
# de ENTREGA que passou.
_RECUSA_COBERTURA = re.compile(
    r"\b(?:nao|nem)\s+(?:e|sera|seria|foi|esta|e\s+mais)\s+possivel\s+(?:\w+\s+){0,3}?"
    r"(?:troc|consert|repar|devolv|devoluc|reembols|cobr|substitu|atend|garant|acion)\w*"
    r"|\b(?:nao|nem)\s+(?:mais\s+)?(?:conseguimos|consigo|podemos|posso|fazemos|faco|realizamos"
    r"|temos\s+como|tenho\s+como|vamos|iremos|efetuamos|aceitamos|cobrimos|trocamos|consertamos"
    r"|atendemos|oferecemos|damos)\s+(?:mais\s+)?(?:\w+\s+){0,3}?(?:troc|consert|repar|devolv|devoluc"
    r"|reembols|cobr|substitu|atend|garant)\w*"
    r"|\b(?:prazo|periodo)\s+(?!d[eoa]\s+(?:entrega|envio|postagem|chegada)\b)(?:\w+\s+){0,6}?"
    r"(?:ja\s+)?(?:se\s+)?(?:acabou|passou(?!\s+a\b)|encerrou|venceu|expirou|esgotou|terminou)\b"
    r"|\b(?:ja\s+)?passou\s+(?:o|do)\s+prazo\b(?!\s+d[eoa]\s+(?:entrega|envio|postagem|chegada))"
    r"|\bfora\s+do\s+prazo\b(?!\s+d[eoa]\s+(?:entrega|envio|postagem|chegada))"
    r"|\b(?:so|apenas|somente)\s+(?:vale|cobre|tem|esta\s+coberto|esta\s+valendo|fica)\s+"
    r"(?:\w+\s+){0,2}?software\b"
    # Ajuste r3 (a sonda da revisão: as recusas por paráfrase passavam): o
    # estorno e o "aceitar"; a 3ª pessoa ("não pode ser devolvido", "a loja não
    # faz troca", "a loja não troca produto com defeito", "a loja não realiza
    # reembolso"); "perdeu o direito", "invalida", "não é responsabilidade da
    # loja", "não tem como", "a tela não entra". O "cancelar" fica de fora: "a
    # loja não consegue mais cancelar" é a orientação aprovada (spec 6.4).
    r"|\b(?:nao|nem)\s+(?:e|sera|seria|foi|esta|e\s+mais)\s+possivel\s+(?:\w+\s+){0,3}?"
    r"(?:estorn|aceit)\w*"
    r"|\b(?:nao|nem)\s+(?:mais\s+)?(?:conseguimos|consigo|podemos|posso|temos\s+como|tenho\s+como"
    r"|aceitamos|fazemos|realizamos)\s+(?:mais\s+)?(?:\w+\s+){0,3}?(?:estorn|aceit)\w*"
    r"|\b(?:nao|nem)\s+(?:mais\s+)?(?:pode|podem|deve|devem|podera|poderao|consegue|conseguem)\s+"
    r"(?:mais\s+)?(?:ser\s+)?(?:\w+\s+){0,2}?(?:troc|consert|repar|devolv|reembols|estorn|substitu"
    r"|aceit|cobert|garant)\w*"
    r"|\b(?:loja|equipe|gente|vendedor|empresa)\s+(?:\w+\s+){0,1}?(?:nao|nunca|nem)\s+(?:mais\s+)?"
    r"(?:faz|fazemos|realiza|aceita|troca|devolve|reembolsa|estorna|conserta|cobre|substitui"
    r"|oferece|da)\s+(?:\w+\s+){0,2}?(?:troc|consert|repar|devolu|devolv|reembols|estorn"
    r"|substitu|garant|cobert|produto|aparelho|celular|item)\w*"
    r"|\bperd\w*\s+(?:o\s+|a\s+|seu\s+|sua\s+)?(?:direito|garantia|cobertura)\b"
    r"|\b(?:invalid\w*|anul\w*)\s+(?:\w+\s+){0,3}?(?:garantia|cobertura|reparo|conserto|troca"
    r"|devolucao|direito|reembolso)\b"
    r"|\b(?:nao|nem)\s+(?:e|sera|seria)\s+(?:de\s+)?(?:nossa\s+|da\s+)?responsabilidade\b"
    r"|\b(?:nao|nem)\s+(?:temos|tem|ha|haveria)\s+como\s+(?:\w+\s+){0,2}?(?:ajud|troc|consert"
    r"|repar|devolv|reembols|estorn|substitu|resolv|atend)\w*"
    r"|\bnesse\s+caso\s+(?:a\s+loja\s+|nos\s+|a\s+gente\s+)?nao\s+(?:tem|temos|ha)\s+como\b"
    r"|\b(?:nao|nem)\s+(?:entra|entram)\s+(?:na\s+|no\s+|em\s+)?(?:garantia|cobertura|troca"
    r"|devolucao|reembolso)\b"
    r"|\binfelizmente[\s,]+(?:o\s+|a\s+|os\s+|as\s+|esse\s+|essa\s+)?(?:\w+\s+){0,2}?nao\s+(?:entra"
    r"|entram|cobre|cobrem|tem\s+direito)\b"
    # Ajuste rf3 (21/10/2026): "Tela quebrada é considerada dano físico e fica
    # de fora", "a política da loja não permite isso", "esse caso não se
    # enquadra", "não tem direito" (a regra estrutural, `recusa_por_estrutura`,
    # pega as que trazem o direito na oração).
    r"|\b(?:fica|ficam|ficaria|ficariam)\s+de\s+fora\b(?!\s+d[ao]\s+(?:caixa|embalagem|kit|pacote))"
    r"|\bpolitica\s+(?:\w+\s+){0,4}?nao\s+(?:permite|aceita|cobre|preve|contempla)\b"
    r"|\bnao\s+se\s+enquadra\w*|\bnao\s+(?:tem|ha|possui)\s+direito\b"
    # (a sonda de holdout do ajuste rf3) a condição que recusa: "devolução só
    # é aceita com a embalagem intacta", "só aceitamos troca de produto lacrado".
    r"|\b(?:troca|devoluc\w*|reembolso|garantia)\s+(?:\w+\s+){0,2}?(?:so|somente|apenas)\s+"
    r"(?:e|eh|sao|sera|serao)\s+aceit\w*"
    r"|\b(?:so|somente|apenas)\s+(?:aceitamos|aceita|aceitam)\s+(?:a\s+|o\s+)?(?:troca|devoluc\w*"
    r"|produto|item|aparelho)"
    # Rodada 4 (achados 5 e 21 da re-revisão final): "a sua solicitação foi
    # indeferida", "não dá direito a…", "a loja não assume/não se
    # responsabiliza", "o valor do frete é perdido".
    r"|\bsolicitac\w*\s+(?:\w+\s+){0,3}?(?:foi\s+|esta\s+|ficou\s+)?(?:negad|indeferid|recusad"
    r"|reprovad)[oa]s?\b"
    r"|\b(?:nao|nem)\s+(?:da|dao|garante|confere|gera)\s+direito\b"
    r"|\b(?:loja|equipe|empresa|vendedor|gente)\s+(?:\w+\s+)?(?:nao|nunca)\s+(?:se\s+)?(?:assume"
    r"|responsabiliz\w*)"
    r"|\b(?:valor|frete|dinheiro)\s+(?:\w+\s+){0,3}?(?:e|sera|fica)\s+perdid[oa]\b"
    # "Pela nossa política, esse caso não tem como."
    r"|\b(?:esse|nesse|neste|este|seu|o\s+seu)\s+caso\s+(?:\w+\s+){0,2}?nao\s+(?:tem|temos|ha)\s+"
    r"(?:como|jeito)\b"
)


def recusa_cobertura(texto: str) -> bool:
    """A resposta recusa troca, conserto ou cobertura (sem precisar da palavra
    "garantia")? PURA (agente; revisão "furos" de 20/10/2026).

    Rodada 4: "não conseguimos garantir a entrega até sexta" é do PRAZO, não a
    recusa da garantia (`_GARANTIR_A_ENTREGA` sai antes)."""
    for frase in re.split(r"[.!?;\n]+", " ".join(plano_de(texto or "").split())):
        if "diferenca" in frase:  # a recusa padrão aprovada da troca pagando a diferença
            continue
        if _RECUSA_COBERTURA.search(_GARANTIR_A_ENTREGA.sub(" ", frase)):
            return True
    return False


# Ajuste rf3 de 21/10/2026 (a recusa de direito por paráfrase NOVA: "não há
# como aceitar a devolução", "a política da loja não permite trocar", "o
# reembolso não procede", "sua solicitação de devolução foi negada", "danos por
# mau uso não são cobertos", "não se enquadra na nossa política de troca"): a
# lista do `_RECUSA_COBERTURA` fecha só as formas já vistas. A regra
# ESTRUTURAL: na mesma ORAÇÃO, a negação (ou "negada", "fica de fora",
# "excluído", "não contempla") e o substantivo/verbo de DIREITO (troca,
# devolução, reembolso, estorno, conserto, garantia, cobertura, substituição,
# aceitar). Sem "cancelar" (a orientação aprovada "a loja não consegue mais
# cancelar"). Só o agente usa (`recusa_por_estrutura`), nos assuntos de
# orientação e de cobertura.
_NEGA_DIREITO = re.compile(
    r"\b(?:nao|nunca|nem|negad[oa]s?|indeferid[oa]s?|recusad[oa]s?|excluid[oa]s?)\b"
    r"|\bfica(?:m)?\s+de\s+fora\b|\bsem\s+(?:direito|cobertura)\b"
    # Rodada 4 (achados 5, 18 e 21 da re-revisão final): a recusa sem a palavra
    # de negação — "inviável", "fora de questão", "sem possibilidade", "é
    # perdido", "não assume", "não dá direito", "não procede", "não se
    # qualifica", "inelegível".
    r"|\binviave(?:l|is)\b|\bfora\s+de\s+(?:questao|cogitacao)\b|\bsem\s+(?:possibilidade|chance)\b"
    r"|\b(?:e|sera|fica)\s+perdid[oa]s?\b|\binelegive(?:l|is)\b|\bimpossive(?:l|is)\b"
)
_DIREITO = re.compile(
    r"\b(?:troca\w*|trocar|devoluc\w*|devolv\w*|reembols\w*|estorn\w*|consert\w*|repar[oa]s?"
    r"|reparar|garantia|cobertura|cobert[oa]s?|cobr(?:e|em|ir|imos|ira)|substitu\w*|aceit\w*"
    r"|ressarc\w*"
    # Rodada 4: o direito dito com outras palavras — "receber/aceitar de
    # volta", "incluso na (nossa) política/garantia", "se responsabiliza",
    # "considerado defeito".
    r"|receb\w*\s+de\s+volta|(?:incluso|inclusa|incluid[oa])s?\s+(?:na|no|em)\s+(?:nossa\s+|nosso\s+)?"
    r"(?:politica|garantia|cobertura|regra)|responsabiliz\w*"
    r"|considerad[oa]s?\s+(?:como\s+)?defeitos?)\b"
)
# As orações em que o "não" não recusa nada do cliente: a condição ("se não
# servir, peça a devolução"), o que o cliente NÃO precisa fazer ou pagar ("a
# devolução não tem custo", "você não precisa devolver a caixa", "não perde a
# garantia"), o lembrete ("não esqueça de…") e o "ainda não" do andamento.
_NAO_RECUSA = re.compile(
    r"^(?:(?:e|mas|porem|entao)\s+)?(?:se|caso|quando|enquanto|assim\s+que|depois\s+que|antes"
    r"\s+de|antes\s+que|ate\s+que)\b"
    r"|\bnao\s+(?:tem|ha|tera|havera|precisa\s+pagar|paga|pagara|cobra|cobramos|cobrara)\s+"
    r"(?:nada|nenhum\w*|custo|taxa|cobranca|valor|frete)\b"
    r"|\bsem\s+(?:custo|taxa|cobranca)\b"
    r"|\bnao\s+(?:precisa|e\s+preciso|e\s+necessari[oa]|sera\s+necessari[oa]|perde|perdera"
    r"|(?:se\s+)?esqueca|deixe\s+de|se\s+preocupe)\b"
    r"|\bainda\s+nao\b"
    # Rodada 4 (achado 7 da re-revisão final): quem FAZ é a plataforma — "o
    # reembolso não é feito pela loja", "a troca não é feita por aqui pelo
    # chat" (a frase que nomeia a plataforma ou o app como o caminho; conferido
    # na frase inteira, `_QUEM_FAZ_E_A_PLATAFORMA`) — e o "não cai na hora".
    r"|\bnao\s+(?:cai|caem|aparece|aparecem|volta|voltam|entra|entram|e\s+(?:creditad|devolvid"
    r"|estornad)[oa])\s+(?:\w+\s+)?(?:na\s+hora|imediatamente|na\s+mesma\s+hora|no\s+mesmo\s+dia"
    r"|de\s+imediato|instantaneamente|na\s+hora\s+da\s+compra)\b"
)
# Rodada 4 (achado 7): "não é feito/feita pela loja | por aqui | pelo chat" com a
# plataforma (ou o app) nomeada na MESMA frase como quem faz.
_NAO_E_FEITO_PELA_LOJA = re.compile(
    r"\bnao\s+(?:e|sao|sera|serao|e\s+mais)\s+(?:feit|realizad|processad|efetuad|solicitad|pedid"
    r"|aberto|aberta)[oa]?s?\s+(?:pela\s+loja|por\s+aqui|pelo\s+chat|aqui|pela\s+gente|por\s+nos"
    r"|pelo\s+vendedor|com\s+a\s+loja)\b"
)
_A_PLATAFORMA_FAZ = re.compile(
    r"\b(?:shopee|mercado\s*livre|tiktok|amazon|magalu|plataforma|minhas\s+compras|app"
    r"|aplicativo)\b"
)
_ORACOES_DA_RECUSA = re.compile(r",|\bmas\b|\bporem\b|\bpois\b|\bporque\b|\bja\s+que\b")


def recusa_por_estrutura(texto: str) -> bool:
    """A resposta recusa um direito pela ESTRUTURA da oração (ver acima)? PURA (agente)."""
    for frase in re.split(r"[.!?;\n]+", " ".join(plano_de(texto or "").split())):
        if "diferenca" in frase:  # a recusa padrão aprovada da troca pagando a diferença
            continue
        for oracao in _ORACOES_DA_RECUSA.split(frase):
            oracao = oracao.strip()
            if _NAO_E_FEITO_PELA_LOJA.search(oracao) and _A_PLATAFORMA_FAZ.search(frase):
                continue
            if (
                oracao
                and not _NAO_RECUSA.search(oracao)
                and _NEGA_DIREITO.search(oracao)
                and _DIREITO.search(oracao)
            ):
                return True
    return False


def nega_garantia(texto: str) -> bool:
    """A resposta nega a garantia, a dá como vencida ou recusa o defeito por ela? PURA (agente)."""
    plano = _NEGACAO_DO_VENCIMENTO.sub(" ", _HIFEN_NA_PALAVRA.sub("", plano_de(texto or "")))
    plano = " ".join(plano.split())
    if _NEGA_GARANTIA.search(plano):
        return True
    return bool(_PRAZO_QUE_ACABOU.search(plano))


def sugere_reset(texto: str) -> bool:
    """A resposta sugere reset, formatação ou restauração de fábrica? PURA (agente)."""
    return bool(_RESET.search(" ".join(plano_de(texto or "").split())))


def _como_data(valor) -> tuple[int, int, int] | None:
    """date/datetime, ISO ("2027-01-07") ou "07/01/2027" → (dia, mês, ano)."""
    if valor is None:
        return None
    if hasattr(valor, "year") and hasattr(valor, "month") and hasattr(valor, "day"):
        return (valor.day, valor.month, valor.year)
    texto = str(valor).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", texto)
    if m:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


_Data = tuple[int | None, int | None, int | None]
# O ano sozinho só é data depois de "até/em/de/desde/ano..." ("vale até
# 2027", "termina em 2027").
_ANTES_DO_ANO = re.compile(
    r"\b(?:ate|em|de|desde|ano|partir|fim|final|inicio|comeco|durante|vence|termina|acaba"
    r"|expira)\s+(?:o\s+)?$"
)


def _datas_com_posicao(plano: str) -> list[tuple[int, int, str, _Data]]:
    """(início, fim, trecho, (dia, mês, ano)) de cada data do texto, na ordem."""
    achadas: list[tuple[int, int, str, _Data]] = []
    resto = plano
    for padrao in _DATAS:
        for m in padrao.finditer(resto):
            grupos = m.groupdict()
            if grupos.get("dx"):
                dia: int | None = _dia_por_extenso(re.sub(r"\s+", " ", grupos["dx"]))
            else:
                dia = int(grupos["d"]) if grupos.get("d") else None
            if grupos.get("mn"):
                nome = grupos["mn"]
                mes = _MESES.get(nome) or _MESES_CURTOS.get(nome)
            else:
                mes = int(grupos["m"]) if grupos.get("m") else None
            ano = int(grupos["y"]) if grupos.get("y") else None
            if ano is not None and ano < 100:
                ano += 2000
            if (dia, mes) == (None, None) and not _ANTES_DO_ANO.search(resto[: m.start()]):
                continue  # "Redmi 13C 2024": o ano do modelo, não uma data
            if (dia is not None and not 1 <= dia <= 31) or (mes is not None and not 1 <= mes <= 12):
                continue
            achadas.append((m.start(), m.end(), m.group(0).strip(), (dia, mes, ano)))
        # Apaga o que casou (mesmo tamanho: as posições seguem valendo).
        resto = padrao.sub(lambda m: " " * len(m.group(0)), resto)
    return sorted(achadas)


def datas_citadas(plano: str) -> list[tuple[str, _Data]]:
    """As datas escritas num texto JÁ na forma de busca (`plano_de`), na ordem.

    Cada uma = (trecho como escrito, (dia, mês, ano)) com o que faltar como
    None ("até 7 de janeiro" → (7, 1, None); "janeiro de 2027" → (None, 1,
    2027)). Ano de dois dígitos vira 20AA. O que não é data possível
    (32/13) fica de fora.
    """
    return [(trecho, data) for _, _, trecho, data in _datas_com_posicao(plano)]


def _bate(citada: _Data, datas: list[tuple[int, int, int]]) -> bool:
    """A data escrita (com o que faltar como None) é uma das `datas`?"""
    return any(
        all(c is None or c == v for c, v in zip(citada, conhecida, strict=True))
        for conhecida in datas
    )


def _preparar(texto: str) -> str:
    """A forma que as conferências de garantia leem: `plano_de`, sem o ponto
    da abreviação do mês ("jan." não fecha frase), sem a proteção da
    plataforma e sem os números de documento (NF, pedido, rastreio)."""
    plano = _sem_protecao_de_terceiros(_ABREVIACAO_MES.sub(r"\1", plano_de(texto or "")))
    return _NUMERO_DE_DOCUMENTO.sub(lambda m: m.group(1) + re.sub(r"\d", "#", m.group(2)), plano)


def _frases(plano: str) -> list[str]:
    return [f for f in _FIM_DE_FRASE.split(plano) if f and f.strip()]


# De QUE é a data — olhando para trás a partir dela, na mesma frase:
#   • o assunto mais perto: garantia ("garantia", "hardware", "coberto",
#     "validade"...) ou outro ("pedido foi enviado em", "previsão é",
#     "compra feita em", "chega até", "a entrega é", "marcada para") —
#     "da compra"/"do pedido"/"na entrega" depois de "garantia" não muda o
#     assunto ("a garantia da sua compra vai até", "começou na entrega,
#     em"); sem palavra de garantia antes, é do outro ("o prazo de entrega
#     é", "data estimada de entrega:");
#   • o marco entre o assunto e a data: fim ("até", "vence", "termina") ou
#     início ("começou", "a partir de", "desde");
#   • a parte: a palavra hardware/software mais perto ("a de software até")
#     — "hardware e software até" = as duas; a comparada ("mais que a de
#     hardware") não conta.
_PALAVRA = re.compile(r"[a-z]+")
_ASSUNTO_GARANTIA = re.compile(
    r"(?:garantias?|garantid[oa]s?|cobert[oa]s?|cobertura|cobre|cobrem|cobria|validade"
    r"|vigencia|vigente|hardware|software)$"
)
_ASSUNTO_OUTRO = re.compile(
    r"(?:pedidos?|compras?|comprou|comprad[oa]s?|envi\w*|postad[oa]|postagem|despachad[oa]"
    r"|coletad[oa]|previs\w*|previst[oa]|cheg\w*|entreg\w*|marcad[oa]|agendad[oa]"
    r"|estimad[oa]|nota|nf|nfe|rastreio|transportadora|pagamento|pag[oa]|aprovad[oa]"
    r"|faturad[oa]|emitid[oa]|feit[oa]|realizad[oa]|efetuad[oa])$"
)
_GENITIVO = re.compile(r"(?:d[aeo]s?|n[ao]s?|dess[ae]s?|dest[ae]s?|nest[ae]s?|naquel[ae])$")
_ARTIGO_OU_POSSESSIVO = {"a", "o", "as", "os", "sua", "seu", "suas", "seus", "minha", "meu"}


def _depois_de_genitivo(anteriores: list[str]) -> bool:
    """A palavra vem logo depois de "da/do/na/no" ("da entrega", "da sua
    compra")? Em "na entrega, prevista para", "prevista" não vem."""
    if anteriores and _GENITIVO.fullmatch(anteriores[-1]):
        return True
    return (
        len(anteriores) >= 2
        and anteriores[-1] in _ARTIGO_OU_POSSESSIVO
        and bool(_GENITIVO.fullmatch(anteriores[-2]))
    )


_UMA_PARTE = re.compile(r"\b(?:hardware|software)\b")
_MARCO_FIM = re.compile(
    r"(?:ate|venc\w*|termin\w*|expir\w*|acab\w*|encerr\w*|fim|final|vale|valida|vigente"
    r"|validade)$"
)
_MARCO_INICIO = re.compile(r"(?:comec\w*|inici\w*|partir|desde|cont[ao]|contad[oa]|contar\w*)$")


def _parte_antes(trecho: str) -> str | None:
    """A parte ("hardware" | "software" | "ambos") mais perto do fim de `trecho`."""
    candidatas = [
        m for m in _UMA_PARTE.finditer(trecho) if not _COMPARACAO.search(trecho[: m.start()])
    ]
    if not candidatas:
        return None
    ultima = candidatas[-1]
    if any(d.start() <= ultima.start() < d.end() for d in _AS_DUAS_PARTES.finditer(trecho)):
        return "ambos"
    return ultima.group(0)


def _de_que_e(prefixo: str, desde: int, frase: str) -> tuple[str | None, str | None, str | None]:
    """(assunto, marco, parte) da data que vem logo depois de `prefixo`.

    assunto: "garantia" | "outro" | None (nenhuma palavra de assunto antes);
    marco: "fim" | "inicio" | None; parte: "hardware" | "software" |
    "ambos" | None — só depois da data anterior da frase (`desde`): em "o
    hardware até 07/01/2027 e o software até 07/10/2027" cada data tem a
    sua. Sem assunto antes da data ("Até 07/01/2027 vale a garantia de
    hardware"), a parte vem da frase inteira (quando só uma das duas é
    citada).
    """
    assunto = marco = None
    parte = _parte_antes(prefixo[desde:])
    outro_depois_de_genitivo = False
    palavras = list(_PALAVRA.finditer(prefixo))
    for distancia, m in enumerate(reversed(palavras)):
        p = m.group(0)
        # "a partir da entrega em {data}": a palavra de outro assunto colada
        # na data (ou só com "em/para/no/dia" no meio) é o assunto dela.
        colada = distancia == 0 or (
            distancia == 1 and palavras[-1].group(0) in {"em", "para", "pra", "no", "dia"}
        )
        if marco is None:
            if _MARCO_INICIO.fullmatch(p):
                marco = "inicio"
            elif _MARCO_FIM.fullmatch(p):
                marco = "fim"
        if _ASSUNTO_GARANTIA.fullmatch(p):
            assunto = "garantia"
            break
        if _ASSUNTO_OUTRO.fullmatch(p):
            if colada or not _depois_de_genitivo(_PALAVRA.findall(prefixo[: m.start()])[-2:]):
                assunto = "outro"
                break
            # "a garantia da sua compra", "o prazo de entrega": quem decide é
            # a palavra de antes; sem palavra de garantia, é do outro assunto.
            outro_depois_de_genitivo = True
    if assunto is None and outro_depois_de_genitivo:
        assunto = "outro"
    if parte is None and assunto is None:
        tem_hw, tem_sw = bool(_HARDWARE.search(frase)), bool(_SOFTWARE.search(frase))
        if tem_hw != tem_sw:
            parte = "hardware" if tem_hw else "software"
    return assunto, marco, parte


def _datas_do_bloco(garantias: list[dict] | None) -> list[tuple[int, int, int]]:
    # `fim_loja`/`fim_uranyx`: só o `ver_garantia` do agente devolve (a garantia
    # Uranyx, revisão r5i de 21/10/2026); o legado nunca tem.
    return [
        d
        for g in garantias or []
        for d in (
            _como_data(g.get(k))
            for k in ("inicio", "fim_hardware", "fim_software", "fim_loja", "fim_uranyx")
        )
        if d is not None
    ]


def _validas_para(
    garantias: list[dict] | None, marco: str | None, parte: str | None
) -> list[tuple[int, int, int]]:
    """As datas do bloco que cabem no lugar: "até" → só os fins (o do
    hardware, o do software, ou os dois); "começou" → só o início; "hardware
    e software até" → só o fim que é dos dois (quando o bloco tem os dois
    iguais)."""
    if marco == "inicio":
        chaves: tuple[str, ...] = ("inicio",)
    elif parte == "ambos":
        dos_dois = [
            fh
            for g in garantias or []
            if (fh := _como_data(g.get("fim_hardware"))) is not None
            and fh == _como_data(g.get("fim_software"))
        ]
        return dos_dois if marco == "fim" else dos_dois + _validas_para(garantias, "inicio", None)
    else:
        fins = {"hardware": ("fim_hardware",), "software": ("fim_software",)}.get(
            parte or "", ("fim_hardware", "fim_software", "fim_loja", "fim_uranyx")
        )
        chaves = fins if marco == "fim" else ("inicio", *fins)
    return [
        d for g in garantias or [] for d in (_como_data(g.get(k)) for k in chaves) if d is not None
    ]


# "Caso" com artigo/possessivo antes é o substantivo ("seu caso entra na
# garantia"), não a condição ("caso esteja na garantia").
_ANTES_DO_SUBSTANTIVO = {"o", "seu", "teu", "esse", "este", "nesse", "neste", "no", "do", "um"}


def _condicao(antes: list[str]) -> bool:
    return any(
        p in _CONDICAO and not (p == "caso" and i and antes[i - 1] in _ANTES_DO_SUBSTANTIVO)
        for i, p in enumerate(antes)
    )


def _promessas(frase: str) -> list[re.Match[str]]:
    """As promessas de cobertura de uma frase (sem negação nem condição)."""
    if frase.rstrip().endswith("?"):
        return []
    achadas = []
    for padrao in _COBERTURA:
        for m in padrao.finditer(frase):
            antes = re.findall(r"[a-z]+", frase[: m.start()])[-5:]
            dentro = re.findall(r"[a-z]+", m.group(0))
            if (
                _NEGACAO.intersection(antes + dentro)
                or _condicao(antes)
                or _NAO_E_O_APARELHO.intersection(antes[-3:])
            ):
                continue
            achadas.append(m)
    for padrao in _COBERTURA_POR_NEGACAO:
        for m in padrao.finditer(frase):
            antes = re.findall(r"[a-z]+", frase[: m.start()])[-5:]
            if _NEGACAO.intersection(antes) or _condicao(antes):
                continue
            achadas.append(m)
    return achadas


def _diz_qual(plano: str, boas: list[dict], ruins: list[dict]) -> bool:
    """A resposta diz DE QUAL compra fala: cita uma data só de garantia que
    cobre, ou o produto de uma que cobre (e que não é o de uma que não cobre)."""
    datas_boas, datas_ruins = _datas_do_bloco(boas), _datas_do_bloco(ruins)
    for _, data in datas_citadas(plano):
        if _bate(data, datas_boas) and not _bate(data, datas_ruins):
            return True
    produtos_ruins = {plano_de(str(g.get("produto") or "")) for g in ruins}
    return any(
        (produto := plano_de(str(g.get("produto") or "")).rstrip("…").strip())
        and produto in plano
        and produto not in produtos_ruins
        for g in boas
    )


def _motivo_da_promessa(frase: str, plano: str, garantias: list[dict]) -> str | None:
    """Promessa de cobertura numa frase: cabe no bloco?

    A promessa que não diz "software" é de cobertura do APARELHO ("pode
    mandar o vídeo do defeito", "a tela está coberta", "o hardware está
    coberto"): só com garantia Ativa — com só o software valendo, o defeito
    não está coberto. A que diz "software" vale com Ativa ou Somente
    software. Com mais de uma garantia e nem todas cobrindo, a resposta tem
    de dizer de qual compra fala.
    """
    # Da promessa até o fim da frase ("…garantia de software até X, pode
    # mandar o vídeo do defeito"): o que vem antes pode ser o hardware negado.
    trecho = frase[min((m.start() for m in _promessas(frase)), default=0) :]
    do_aparelho = not _SOFTWARE.search(frase) or bool(
        _DO_APARELHO.search(_DEFEITO_DE_SOFTWARE.sub(" ", trecho))
    )
    cobrem = {_STATUS_ATIVA} if do_aparelho else {_STATUS_ATIVA, _STATUS_SOMENTE_SOFTWARE}
    boas = [g for g in garantias if str(g.get("status") or "") in cobrem]
    ruins = [g for g in garantias if str(g.get("status") or "") not in cobrem]
    if not boas:
        if do_aparelho and any(
            str(g.get("status") or "") == _STATUS_SOMENTE_SOFTWARE for g in garantias
        ):
            return MOTIVO_COBERTURA_HARDWARE
        return MOTIVO_COBERTURA_SEM_GARANTIA
    if ruins and not _diz_qual(plano, boas, ruins):
        return MOTIVO_COBERTURA_QUAL_COMPRA
    return None


# "Garantir a entrega/o prazo/que chegue": o verbo do prazo (só o agente).
# Rodada 4b (21/10/2026, achado 6 da revisão r4): também o verbo com o objeto
# do ESTOQUE/da disponibilidade ("não conseguimos garantir como estará o
# estoque", "…garantir que continue disponível até amanhã", "…garantir a cor")
# — o verbo só fala de garantia com o aparelho, o defeito ou a cobertura.
_GARANTIR_A_ENTREGA = re.compile(
    r"\bgarantir\s+(?:a\s+entrega|o\s+prazo|a\s+data|a\s+chegada|que\s+(?:chegue|chega|chegara"
    r"|sera\s+entregue|seja\s+entregue|o\s+pedido|a\s+entrega|o\s+envio))\b"
    r"|\bgarant(?:ir|imos|e|o)\s+(?:a\s+|o\s+|as\s+|os\s+|essa\s+|esse\s+)?(?:estoque|disponibilidade"
    r"|cor|cores|variac\w+|quantidade|unidades?|reposicao|preco|valor|frete|horario|postagem"
    r"|coleta)\b"
    r"|\bgarantir\s+(?:que|como|se|ate\s+quando)\s+(?!(?:\w+\s+){0,4}?(?:aparelho|celular|produto"
    r"|defeito|funcion\w*|conserto|reparo|troca|cobert\w*|garantia|quebr\w*|estrag\w*)\b)"
    r"(?:\w+\s+){0,2}?(?:continu\w*|ainda|tenha|tera|haja|havera|fique|fica|ficara|esteja|estara"
    r"|vai\s+estar|vai\s+ter|sobre|sobra|reste|o\s+estoque|a\s+cor|a\s+variacao|disponive\w*)\b"
)


def fala_de_garantia(
    texto: str, garantias: list[dict] | None = None, *, agente: bool = False
) -> bool:
    """A resposta fala da garantia do aparelho (ou cita uma data do bloco)?

    Garantia é assunto só de pessoa no automático: a resposta que fala dela
    — mesmo numa conversa de rastreio, e por paráfrase ("é garantido",
    "assistência da fábrica", "suporte do fabricante") ou promessa de
    cobertura — nunca sai sozinha. A proteção da plataforma ("Garantia
    Shopee", "Compra Garantida") e a de terceiros ("seguro da
    transportadora", "entrega garantida") não contam.

    `agente=True` (esclarecimento 2 do orquestrador, 20/10/2026): citar o
    defeito para ORIENTAR ("mande um vídeo curto mostrando o defeito") não
    conta; conta o diagnóstico e a promessa sobre o defeito
    (`diagnostica_ou_promete_defeito`) — e todo o resto, igual.
    """
    # Defeito/conserto/"protegido contra" no texto INTEIRO (antes de tirar a
    # proteção da plataforma e a de terceiros: "a Garantia Shopee cobre
    # defeitos" fala do aparelho).
    if agente:
        if diagnostica_ou_promete_defeito(texto):
            return True
    elif _FALA_DEFEITO.search(_HIFEN_NA_PALAVRA.sub("", plano_de(texto or ""))):
        return True
    plano = _preparar(texto)
    if agente:
        # Ajuste r3: "não conseguimos garantir a entrega antes do Natal" é do
        # PRAZO (o verbo, sem o produto), não da garantia do aparelho.
        plano = _GARANTIR_A_ENTREGA.sub(" ", plano)
    if _FALA_GARANTIA.search(_HIFEN_NA_PALAVRA.sub("", plano)) or any(
        _promessas(f) for f in _frases(plano)
    ):
        return True
    do_bloco = _datas_do_bloco(garantias)
    return any(
        sum(x is not None for x in data) >= 2 and _bate(data, do_bloco)
        for _, data in datas_citadas(plano)
    )


# "07/01/2027 para hardware", "07/01/2027 (hardware)", "07/01/2027 (defeitos
# de hardware)": a parte escrita LOGO DEPOIS da data é dela, não da seguinte.
_PARTE_DEPOIS = re.compile(
    r"\s*(?:\(\s*(?:(?:defeitos?|garantia|parte)\s+d[eo]\s+)?(?P<p1>hardware|software)\s*\)"
    r"|(?:para|pra|pro|no|do)\s+(?:o\s+)?(?P<p2>hardware|software)\b)"
)
# "de 07/10/2026 a 07/01/2027", "entre X e Y", "válida de X até Y": a
# primeira data é o início; a segunda, o fim (da mesma parte).
_ABRE_INTERVALO = re.compile(r"\b(?:de|desde|entre|do\s+dia|desde\s+o\s+dia)\s+$")
_FECHA_INTERVALO = re.compile(r"\s*(?:a|ate|e|ao\s+dia|ate\s+o\s+dia|ate\s+dia)\s+$")
# "válida de 07/10/2026": é o começo, não o fim.
_INICIO_POR_VALIDADE = re.compile(
    r"\b(?:valida|valido|vale|vigente|valendo)\s+(?:de|desde)\s+(?:o\s+dia\s+)?$"
)


def _papeis_das_datas(frase: str, datas: list) -> list[tuple[str | None, str | None, str | None]]:
    """(assunto, marco, parte) de cada data da frase, com os intervalos."""
    papeis: list[list[str | None]] = []
    desde = 0
    for inicio, fim, _trecho, _data in datas:
        assunto, marco, parte = _de_que_e(frase[:inicio], desde, frase)
        if _INICIO_POR_VALIDADE.search(frase[:inicio]):
            marco = "inicio"
        depois = _PARTE_DEPOIS.match(frase, fim)
        if depois:
            parte = depois.group("p1") or depois.group("p2")
        papeis.append([assunto, marco, parte])
        desde = depois.end() if depois else fim
    for i in range(1, len(datas)):
        abre = _ABRE_INTERVALO.search(frase[: datas[i - 1][0]])
        entre = frase[datas[i - 1][1] : datas[i][0]]
        if not (abre and _FECHA_INTERVALO.fullmatch(entre)):
            continue
        if entre.strip() == "e" and not abre.group(0).startswith("entre"):
            continue  # "de X e Y" não é intervalo
        papeis[i - 1][1], papeis[i][1] = "inicio", "fim"
        papeis[i][0] = papeis[i][0] or papeis[i - 1][0]
        papeis[i][2] = papeis[i][2] or papeis[i - 1][2]
    return [(a, m, p) for a, m, p in papeis]


# A garantia LEGAL (CDC) e a cobertura dita por extenso (ajuste r3, agente).
_GARANTIA_LEGAL = re.compile(r"\bgarantia\s+legal\b|\bcodigo\s+de\s+defesa\b|\bcdc\b")
_COBERTO = re.compile(r"\bcobert[oa]s?\b|\bcobre\w*|\bgarantid[oa]s?\b|\bcobertura\b")


def conferir_garantia(
    texto: str,
    garantias: list[dict] | None,
    *,
    outras_datas: tuple | list = (),
    conferir_promessa: bool = True,
    prazos_de_garantia: Collection[tuple[int, str, str]] = (),
) -> list[str]:
    """Motivos (só IA) pelos quais a resposta não pode citar a garantia assim.

    `garantias` = o bloco da conversa (`contexto.garantia_para_ia`): cada uma
    com `status`, as datas `inicio`, `fim_hardware`, `fim_software` (date,
    ISO ou dd/mm/aaaa) e o `produto`; [] = sem garantia cadastrada (ou não
    consultada numa conversa que não é de garantia); None = pergunta
    pré-venda. `outras_datas` = datas que o sistema deu por outro caminho
    (lacunas, data da compra): valem para o que não é garantia ("enviado
    em", "previsão é"). `conferir_promessa=False` = a conversa não é de
    garantia e o bloco nem foi consultado: a promessa de cobertura não
    reprova aqui (quem chama a manda para pessoa por `fala_de_garantia`).
    `prazos_de_garantia` = só o AGENTE (revisão de 20/10/2026): a frase que
    diz a REGRA da linha com o prazo dela ("as malas têm garantia de 90 dias
    contra defeitos de fabricação", `prazos_da_regra`) não é promessa de
    cobertura do pedido — sem status (antes da compra, sem data de entrega)
    ela vale. Vazio (o legado): nada muda. Função pura.
    """
    plano = _preparar(texto)
    frases = _frases(plano)
    sobre_garantia = [bool(_FRASE_GARANTIA.search(f)) for f in frases]
    if not any(sobre_garantia) and not any(_promessas(f) for f in frases):
        return []
    outras = [d for d in (_como_data(x) for x in outras_datas) if d is not None]
    motivos: list[str] = []

    inventadas: list[str] = []
    for frase, de_garantia in zip(frases, sobre_garantia, strict=True):
        datas = _datas_com_posicao(frase)
        for (_i, _f, trecho, data), (assunto, marco, parte) in zip(
            datas, _papeis_das_datas(frase, datas), strict=True
        ):
            if assunto == "outro":
                validas = _datas_do_bloco(garantias) + outras
            elif assunto == "garantia" or de_garantia:
                validas = _validas_para(garantias, marco, parte)
            else:
                # Frase sem palavra de garantia numa resposta que fala dela
                # ("Ela vai até 07/02/2027"): do bloco, ou do sistema.
                validas = _validas_para(garantias, marco, parte) + outras
            if not _bate(data, validas) and trecho not in inventadas:
                inventadas.append(trecho)
    if inventadas:
        motivos.append(f"{MOTIVO_DATA_GARANTIA} ({', '.join(inventadas[:3])})")

    if garantias is not None and conferir_promessa:
        # Só a REGRA da linha antes da compra (todas sem status) livra a frase
        # com o prazo dela; com status (o pedido tem garantia calculada ou
        # registrada), o prazo sai da frase e a promessa do resto é conferida
        # (revisão 2 de 20/10/2026: "está coberto pela garantia de 3 meses para
        # hardware" passava com a garantia expirada).
        # (revisão "furos") e só ANTES da compra: o item que `ver_garantia` marca
        # `antes_da_compra: False` (há pedido ou o cliente fala da compra feita)
        # tem a promessa conferida sem o prazo da regra.
        so_regra = all(not g.get("status") and g.get("antes_da_compra", True) for g in garantias)
        # Ajuste r3: a garantia LEGAL (`{"legal": True}`, sem status e sem
        # prazo — o que o `ver_garantia` do AGENTE devolve para as outras
        # linhas e a Charlots sem o fato) não é promessa de cobertura: "tem a
        # garantia legal; para acionar, …" sai. "Está coberto (pela garantia
        # legal)" continua barrado. O legado nunca tem a regra legal.
        so_legal = bool(garantias) and all(
            not g.get("status")
            and isinstance(g.get("regra"), Mapping)
            and g["regra"].get("legal") is True
            for g in garantias
        )
        for frase in frases:
            if so_legal and _GARANTIA_LEGAL.search(frase) and not _COBERTO.search(frase):
                continue
            conferida = frase
            if prazos_de_garantia:
                sem_prazo = _sem_prazo_da_regra(frase, prazos_de_garantia)
                if sem_prazo != frase:
                    # Revisão (23/10/2026): só o prazo da REGRA livra a frase da promessa;
                    # o de fato só sai da conta do número (`so_da_regra`).
                    da_regra = so_da_regra(prazos_de_garantia)
                    if so_regra and da_regra and _sem_prazo_da_regra(frase, da_regra) != frase:
                        continue  # a regra da linha, com o prazo dela (agente)
                    conferida = sem_prazo
            if _promessas(conferida) and (
                motivo := _motivo_da_promessa(conferida, plano, garantias)
            ):
                motivos.append(motivo)
    return list(dict.fromkeys(motivos))
