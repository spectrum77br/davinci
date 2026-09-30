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
       sua nota") e desestimular reclamação ("não precisa abrir reclamação");
     • lacuna não preenchida (`{rastreio}` saindo literal para o cliente).

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

from app.services.atendimento.constantes import ORIGEM_IA, limite_caracteres

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
_MOTIVO_AVALIACAO = "pede ou condiciona avaliação"
_MOTIVO_RECLAMACAO = "desestimula reclamação"
_MOTIVO_ML_ISO = "caractere que o Mercado Livre não aceita"
_MOTIVO_EMOJI_AMAZON = "emoji (Amazon)"
_MOTIVO_DOCUMENTO_MAGALU = "documento (CPF/CNPJ): a moderação da Magalu barra"
_MOTIVO_LACUNA = "lacuna não preenchida"

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

_PIX = re.compile(r"\bpix\b")
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

_LACUNA = re.compile(r"\{\{?\s*[a-z_]+\s*\}?\}", re.IGNORECASE)

# ── Regras só da IA ───────────────────────────────────────────────────────

_MOTIVO_VALOR = "valor em R$ só pode vir do sistema"
_MOTIVO_PERCENTUAL = "percentual só pode vir do sistema"
_MOTIVO_FRETE = "frete grátis só pessoa pode prometer"
_MOTIVO_PRAZO = "prazo em números só pode vir do sistema"
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
_UNIDADE_MEDIDA = r"(?!\s*(?:m|cm|mm|km|kg|g|l|ml|pol)\b)"
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


def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
    """Motivos pelos quais o texto NÃO pode sair (lista vazia = pode).

    Valida o texto como ele SAI (normalizado): o limite de caracteres é o
    que a plataforma conta. Motivos em português curto, sem repetir, na
    ordem em que a pessoa deve corrigir.
    """
    limpo = normalizar(texto or "", plataforma=plataforma, canal=canal)
    if not limpo:
        return ["texto vazio"]

    motivos: list[str] = []
    limite = limite_caracteres(plataforma, canal)
    if len(limpo) > limite:
        motivos.append(f"passa do limite de {limite} caracteres ({len(limpo)})")

    plano = plano_de(limpo)

    # Contato fora da loja. A ordem importa: o que casou sai do texto antes
    # da regra seguinte (e-mail não vira também link e @perfil).
    if _algum(_EMAIL, plano):
        motivos.append(_MOTIVO_EMAIL)
        plano = _apagar(_EMAIL, plano)
    if _algum(_WHATSAPP, plano):
        motivos.append(_MOTIVO_WHATSAPP)
        plano = _apagar(_WHATSAPP, plano)
    if _algum(_LINK, plano):
        motivos.append(_MOTIVO_LINK)
        plano = _apagar(_LINK, plano)
    if _algum(_TELEFONE, plano):
        motivos.append(_MOTIVO_TELEFONE)
    if _PERFIL.search(plano):
        motivos.append(_MOTIVO_PERFIL)
    if _algum(_REDE_SOCIAL, plano):
        motivos.append(_MOTIVO_REDE)
    if _PIX.search(plano):
        motivos.append(_MOTIVO_PIX)
    if _algum(_BANCO, plano):
        motivos.append(_MOTIVO_BANCO)
    if _FORA_DA_PLATAFORMA.search(plano) or _algum(_POR_FORA, plano):
        motivos.append(_MOTIVO_POR_FORA)

    # Regras da plataforma.
    if plataforma == "ml":
        fora = _fora_do_latin1(limpo)
        if fora:
            motivos.append(f"{_MOTIVO_ML_ISO} ({' '.join(fora[:5])})")
    if plataforma == "amazon" and _EMOJI.search(limpo):
        motivos.append(_MOTIVO_EMOJI_AMAZON)
    if plataforma == "magalu" and _algum(_DOCUMENTO_MAGALU, plano):
        motivos.append(_MOTIVO_DOCUMENTO_MAGALU)

    # Avaliação e reclamação: proibido para todos, em todas.
    if _algum(_AVALIACAO, plano):
        motivos.append(_MOTIVO_AVALIACAO)
    if _algum(_RECLAMACAO, plano):
        motivos.append(_MOTIVO_RECLAMACAO)

    lacunas = sorted({m.group(0) for m in _LACUNA.finditer(limpo)})
    if lacunas:
        motivos.append(f"{_MOTIVO_LACUNA} ({', '.join(lacunas)})")

    # Só IA: prazo e valor vinculam — só por lacuna preenchida pelo código.
    if origem == ORIGEM_IA:
        if _algum(_VALOR, plano):
            motivos.append(_MOTIVO_VALOR)
        if _PERCENTUAL.search(plano):
            motivos.append(_MOTIVO_PERCENTUAL)
        if _FRETE.search(plano):
            motivos.append(_MOTIVO_FRETE)
        if _algum(_PRAZO, plano):
            motivos.append(_MOTIVO_PRAZO)
        if _algum(_GARANTIA, plano):
            motivos.append(_MOTIVO_GARANTIA)
        if _algum(_DADO_PESSOAL, plano):
            motivos.append(_MOTIVO_DADO_PESSOAL)
        if _algum(_PROMESSA, plano):
            motivos.append(_MOTIVO_PROMESSA)

    return list(dict.fromkeys(motivos))


def so_da_ia(motivos: list[str]) -> bool:
    """Algum motivo é das regras SÓ da IA (valor, %, frete, prazo, garantia, promessa)?

    A IA usa isto para decidir entre deixar o rascunho na caixa (a pessoa
    corrige o que o envio de qualquer jeito barraria) e bloqueá-lo (o envio
    pela pessoa NÃO barraria — "frete grátis" inventado sairia num clique).
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
    return any(m in da_ia for m in motivos)
