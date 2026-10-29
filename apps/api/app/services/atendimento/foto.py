"""Foto na resposta ao comprador (RF1 ③, "anexo na caixa de resposta"; 01/10/2026).

A pessoa anexa UMA imagem na caixa de resposta e ela sai pela API de cada
plataforma — sempre pelo caminho único de saída (`enviar.enviar_foto`, com
as MESMAS travas do texto: envio desligado, loja em Observar, conversa
bloqueada...). O upload para a plataforma acontece SÓ na hora do envio, e
só com o envio ligado: anexar e desistir não sobe nada para lugar nenhum.

Por plataforma (a resposta é a do chat da conversa):

  Shopee (chat)       `sellerchat/upload_image` (multipart `file`) → url →
                      `send_message` com `message_type=image`. Foto sozinha:
                      o texto vai pela caixa de resposta, separado.
  TikTok (chat)       `customer_service/202309/images/upload` (multipart
                      `data`) → url/largura/altura → mensagem `IMAGE`. Foto
                      sozinha. [confirmar] formato pela doc oficial, não medido.
  ML (pós-venda)      `POST /messages/attachments?tag=post_sale&site_id=MLB`
                      (multipart `file`) → id do anexo → mensagem no pack com
                      `attachments: [id]` e um TEXTO (o ML manda o anexo junto
                      de uma mensagem; até 350 caracteres, como o texto).
                      Pergunta do ML (no anúncio) não aceita anexo.
  Zap (WhatsApp)      Onda 3 (27/10/2026, docs/zap/desenho.md §6.8): `POST
                      /{pnid}/media` (multipart) → id → mensagem `type=image`
                      com legenda OPCIONAL (até 1024 caracteres, pelo
                      validador). O ramo mora no adaptador do Zap
                      (`zap.enviar_imagem`), chamado pelo
                      `enviar._chamar_plataforma_foto` ANTES do
                      `cliente_da_integracao` (o Zap não tem loja); a nossa
                      cópia fica no disco (`zap_midia.guardar_saida`) porque o
                      WhatsApp não devolve URL. Só o Zap DO NÚMERO
                      (`whatsapp` + `zap`): o formato antigo (com a plataforma
                      da venda) continua sem foto.
  ML (reclamação)     28/10/2026, doc "Gerenciar mensagem de uma reclamação":
                      `POST /post-purchase/v1/claims/{id}/attachments`
                      (multipart `file`; JPG, PNG ou PDF até 5 MB; nome até
                      125 caracteres [A-Za-z0-9_.-]) → `filename` → a
                      mensagem ao comprador ou ao mediador com
                      `attachments: [filename]` e um TEXTO (obrigatório, como
                      no pós-venda). O upload só acontece depois de conferida
                      a ação da loja (`ml.responder_reclamacao`).
  Amazon, Magalu, Temu, AliExpress, Instagram: sem foto pelo DaVinci (por
  enquanto) — `motivo_sem_foto` diz à pessoa o caminho de cada plataforma.

PDF (28/10/2026, a NF — "o primeiro empecilho" da equipe respondendo pelo
DaVinci): o mesmo botão aceita PDF (até 10 MB, conferido pelos bytes e
aberto pelo PyMuPDF: sem senha, com página). Só o PÓS-VENDA DO ML recebe o
PDF como arquivo (`CAIXAS_COM_PDF`: o upload de anexo da doc oficial aceita
JPG, PNG, PDF, HEIC e GIF até 25 MB). O chat da Shopee e o do TikTok não têm
mensagem de arquivo (Shopee: nenhum `file` em 113 mil mensagens de 60 dias;
TikTok: texto, imagem, vídeo e cartões) — lá o PDF vira UMA IMAGEM com as
páginas empilhadas (`pdf_em_imagem`, até `MAX_PAGINAS_IMAGEM`) e sai pelo
caminho da foto. O Zap também (a mídia `document` da Cloud API fica para
depois). A NF do pedido em PDF vem do `danfe.py`.

Tamanho: a tela aceita até 10 MB e até `MAX_PIXELS` de resolução (JPEG ou
PNG, conferido pelos BYTES e pelo cabeçalho, não pelo nome); na hora de
subir, a foto é REDUZIDA até o teto medido de cada plataforma
(`chamados_devolucao.preparar_foto`, o mesmo das evidências de devolução):
Shopee 900 KB, TikTok 3 MB, ML 5 MB, Zap 5 MB (o teto de imagem da Cloud API).

Erro ANTES de a mensagem sair (o upload) é falha limpa — nada chegou ao
comprador e dá para tentar de novo. Erro no envio da mensagem segue a regra
do texto: recusa com código = não saiu; timeout/sem código = AMBÍGUO (vira
`revisar`, nunca se retenta).

Nunca levanta (o envio trata tudo como `ResultadoEnvio`); nada de bytes,
nome de arquivo do comprador nem URL assinada no log.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import structlog

from app.models import AtendimentoConversa
from app.services.atendimento.constantes import (
    CANAL_CHAT,
    CANAL_POS_VENDA,
    CANAL_RECLAMACAO,
    CANAL_ZAP,
    PLATAFORMA_WHATSAPP,
    ResultadoEnvio,
)

logger = structlog.get_logger()

# ── O que se aceita ───────────────────────────────────────────────────────
PDF = "application/pdf"
TIPOS_ACEITOS: dict[str, str] = {"image/jpeg": ".jpg", "image/png": ".png", PDF: ".pdf"}
# O que a TELA pode mandar ao DaVinci (antes da redução para a plataforma).
MAX_BYTES_UPLOAD = 10 * 1024 * 1024
# O teto de cada plataforma (medidos; ver `chamados_devolucao` e
# `devolucao_mensagem_comprador`): acima disto, a foto é reduzida.
MAX_BYTES_PLATAFORMA: dict[str, int] = {
    "shopee": 900_000,
    "tiktok": 3_000_000,
    "ml": 5 * 1024 * 1024,
    # Cloud API: imagem até 5 MB (documentação da mídia, consultada em 17/10).
    PLATAFORMA_WHATSAPP: 5 * 1024 * 1024,
}
# Teto de RESOLUÇÃO (largura × altura, lido do cabeçalho). Os 10 MB não
# bastam: um PNG de 1 MB com 9000×9000 pixels decodifica para mais de 1 GB
# na redução (PyMuPDF, síncrona — trava a api inteira enquanto roda). 40
# megapixels cobre a foto de celular e de câmera (12–24 MP); acima disso a
# pessoa reduz antes de anexar.
MAX_PIXELS = 40_000_000
# (plataforma, caixa) que aceitam foto pelo DaVinci. O Zap do número desde a
# Onda 3 (27/10/2026).
CAIXAS_COM_FOTO = frozenset(
    {
        ("shopee", CANAL_CHAT),
        ("tiktok", CANAL_CHAT),
        ("ml", CANAL_POS_VENDA),
        ("ml", CANAL_RECLAMACAO),
        (PLATAFORMA_WHATSAPP, CANAL_ZAP),
    }
)
# No ML a foto vai DENTRO de uma mensagem com texto (obrigatório).
CAIXAS_COM_LEGENDA = frozenset({("ml", CANAL_POS_VENDA), ("ml", CANAL_RECLAMACAO)})
# No Zap a legenda é PERMITIDA sem ser obrigatória (§6.8): a foto pode ir
# sozinha ou com uma frase — que passa pelo validador como qualquer resposta.
CAIXAS_COM_LEGENDA_OPCIONAL = frozenset({(PLATAFORMA_WHATSAPP, CANAL_ZAP)})
# O teto da LEGENDA, quando é menor que o do texto da caixa (o WhatsApp corta
# a legenda em 1.024; o texto vai até 4.096).
LIMITE_LEGENDA: dict[tuple[str, str], int] = {(PLATAFORMA_WHATSAPP, CANAL_ZAP): 1024}

# (plataforma, caixa) que recebem o PDF como ARQUIVO; nas outras com foto, o
# PDF vira imagem. O teto do arquivo na plataforma (ML: 25 MB, doc oficial).
CAIXAS_COM_PDF = frozenset({("ml", CANAL_POS_VENDA), ("ml", CANAL_RECLAMACAO)})
MAX_BYTES_PDF_PLATAFORMA: dict[str, int] = {"ml": 25 * 1024 * 1024}
# O anexo da RECLAMAÇÃO do ML tem teto próprio (doc: 5 MB, foto ou PDF).
MAX_BYTES_RECLAMACAO_ML = 5_000_000
# PDF que vira imagem: até 3 páginas empilhadas (a DANFE costuma ter 1); a
# resolução sai do teto de pixels abaixo (A4 a ~150 dpi por página).
MAX_PAGINAS_IMAGEM = 3
MAX_PIXELS_PDF_IMAGEM = 7_000_000
DPI_PDF_IMAGEM = 150
MIN_DPI_PDF_IMAGEM = 60
# Toda página vira a largura do A4 (pontos); página mais de 4× mais alta que
# larga (ou o contrário) não é documento — é recusada.
LARGURA_PDF_IMAGEM = 595.0
MAX_PROPORCAO_PAGINA = 4.0

# O ML: o anexo sobe para o site da conta (Brasil).
ML_SITE = "MLB"


class FotoInvalida(ValueError):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """A foto não serve (tipo, tamanho, vazia). `code` estável; `detail` = frase da tela."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass(frozen=True)
class Foto:
    """A imagem que a pessoa anexou, já conferida (`validar_foto`)."""

    conteudo: bytes
    nome: str
    mime: str
    sha256: str

    @property
    def tamanho(self) -> int:
        return len(self.conteudo)


def tipo_pelo_conteudo(dados: bytes) -> str | None:
    """O tipo pelos primeiros bytes (a "assinatura" do arquivo), não pelo nome."""
    if dados.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if dados.startswith(b"%PDF-"):
        return PDF
    return None


# Marcadores JPEG de início de quadro (SOF) — onde estão largura e altura.
_JPEG_SOF = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def dimensoes(dados: bytes) -> tuple[int, int] | None:
    """(largura, altura) pelo CABEÇALHO, sem decodificar a imagem; ilegível → None.

    PNG: o bloco IHDR (logo depois da assinatura). JPEG: o primeiro SOF,
    andando pelos segmentos antes dos dados da imagem.
    """
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(dados) >= 24 and dados[12:16] == b"IHDR":
            return int.from_bytes(dados[16:20], "big"), int.from_bytes(dados[20:24], "big")
        return None
    if not dados.startswith(b"\xff\xd8"):
        return None
    i, n = 2, len(dados)
    while i + 4 <= n:
        if dados[i] != 0xFF:
            return None
        marcador = dados[i + 1]
        if marcador == 0xFF:  # byte de preenchimento
            i += 1
            continue
        if marcador == 0x01 or 0xD0 <= marcador <= 0xD8:  # sem tamanho
            i += 2
            continue
        if marcador in (0xD9, 0xDA):  # fim, ou os dados começaram sem SOF
            return None
        if marcador in _JPEG_SOF:
            if i + 9 > n:
                return None
            altura = int.from_bytes(dados[i + 5 : i + 7], "big")
            largura = int.from_bytes(dados[i + 7 : i + 9], "big")
            return largura, altura
        tamanho = int.from_bytes(dados[i + 2 : i + 4], "big")
        if tamanho < 2:
            return None
        i += 2 + tamanho
    return None


_RE_NOME = re.compile(r"[^A-Za-z0-9._-]+")


def _nome_limpo(nome: str | None, mime: str) -> str:
    """Nome curto e sem caminho/acento, com a extensão do tipo de verdade."""
    base = (nome or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _RE_NOME.sub("_", base).strip("._")[:60] or "foto"
    raiz = base.rsplit(".", 1)[0] if "." in base else base
    return f"{raiz or 'foto'}{TIPOS_ACEITOS[mime]}"


def validar_foto(dados: bytes | None, nome: str | None, mime_declarado: str | None = None) -> Foto:
    """Confere a foto anexada; levanta `FotoInvalida` com a frase para a tela."""
    if not dados:
        raise FotoInvalida("foto_vazia", "O arquivo da foto veio vazio.")
    if len(dados) > MAX_BYTES_UPLOAD:
        raise FotoInvalida(
            "foto_grande",
            f"A foto passa de {MAX_BYTES_UPLOAD // (1024 * 1024)} MB — escolha uma menor.",
        )
    mime = tipo_pelo_conteudo(dados)
    if mime is None:
        raise FotoInvalida("foto_tipo", "Só dá para mandar foto JPG ou PNG, ou PDF.")
    if mime == PDF:
        _conferir_pdf(dados)
    tamanho = dimensoes(dados) if mime != PDF else None
    if tamanho is not None and tamanho[0] * tamanho[1] > MAX_PIXELS:
        largura, altura = tamanho
        raise FotoInvalida(
            "foto_grande",
            f"A foto tem resolução grande demais ({largura}×{altura} pixels; o máximo é "
            f"{MAX_PIXELS // 1_000_000} megapixels) — reduza a foto e anexe de novo.",
        )
    declarado = (mime_declarado or "").split(";")[0].strip().lower()
    if declarado and declarado not in TIPOS_ACEITOS and declarado != "application/octet-stream":
        # O navegador disse outro tipo (HEIC renomeado, GIF): o conteúdo manda.
        logger.info("atendimento_foto_tipo_divergente", declarado=declarado[:40], real=mime)
    return Foto(
        conteudo=bytes(dados),
        nome=_nome_limpo(nome, mime),
        mime=mime,
        sha256=hashlib.sha256(dados).hexdigest(),
    )


def _conferir_pdf(dados: bytes) -> int:
    """O PDF abre, não tem senha e tem página; devolve o nº de páginas."""
    import pymupdf

    try:
        doc = pymupdf.open(stream=dados, filetype="pdf")
    except Exception as e:  # noqa: BLE001 — PDF corrompido
        raise FotoInvalida("pdf_invalido", "Não consegui abrir este PDF.") from e
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise FotoInvalida("pdf_senha", "O PDF tem senha: tire a senha e anexe de novo.")
        if doc.page_count < 1:
            raise FotoInvalida("pdf_invalido", "O PDF não tem página.")
        return doc.page_count
    finally:
        doc.close()


# O caminho de cada plataforma quando o anexo não sai pelo DaVinci (28/10/2026).
_SEM_ANEXO: dict[str, str] = {
    "amazon": (
        "Amazon: anexo pelo DaVinci ainda não está liberado. Para mandar a NF (PDF), abra a "
        "conversa no Seller Central (Mensagens do comprador) e use o anexo de lá."
    ),
    "magalu": (
        "Magalu: anexo pelo DaVinci ainda não está liberado. Para mandar a NF (PDF), anexe "
        "pelo portal do parceiro Magalu, no SAC/atendimento do pedido."
    ),
}


def motivo_sem_foto(conversa: AtendimentoConversa) -> str | None:
    """Por que esta conversa não aceita anexo pelo DaVinci (e o caminho); None = aceita."""
    par = (conversa.plataforma, conversa.canal)
    if par in CAIXAS_COM_FOTO:
        return None
    if conversa.plataforma == "ml":
        return (
            "Pergunta no anúncio do Mercado Livre não aceita anexo — só texto. A NF vai pelas "
            "mensagens do pedido (pós-venda), depois da compra."
        )
    if conversa.plataforma in _SEM_ANEXO:
        return _SEM_ANEXO[conversa.plataforma]
    return "Mandar anexo por aqui ainda não está liberado para esta plataforma."


def aceita_pdf(conversa: AtendimentoConversa) -> bool:
    """O PDF chega como ARQUIVO nesta caixa (pós-venda do ML)? Senão vira imagem."""
    return (conversa.plataforma, conversa.canal) in CAIXAS_COM_PDF


def como_vai_o_pdf(conversa: AtendimentoConversa) -> str | None:
    """'arquivo' (chega como PDF), 'imagem' (vira imagem) ou None (sem anexo aqui)."""
    if motivo_sem_foto(conversa):
        return None
    return "arquivo" if aceita_pdf(conversa) else "imagem"


def pdf_em_imagem(anexo: Foto) -> Foto:
    """O PDF como UMA imagem JPEG (páginas empilhadas) — para o chat sem arquivo.

    Síncrona (PyMuPDF): quem chama da rota roda em thread. Cada página é
    escalada para a MESMA largura (`LARGURA_PDF_IMAGEM`, a do A4) e a
    resolução sai do teto de pixels; página de proporção absurda, PDF com
    mais de `MAX_PAGINAS_IMAGEM` páginas ou que só caberia ilegível são
    recusados (`FotoInvalida`) — revisão de 28/10: nada de bomba de memória.
    """
    import pymupdf

    origem = pymupdf.open(stream=anexo.conteudo, filetype="pdf")
    try:
        if origem.page_count > MAX_PAGINAS_IMAGEM:
            raise FotoInvalida(
                "pdf_paginas",
                f"Este chat não aceita PDF, e o PDF vira imagem só até {MAX_PAGINAS_IMAGEM} "
                f"páginas (este tem {origem.page_count}). Mande só as páginas que importam.",
            )
        alturas: list[float] = []
        for p in origem:
            w, h = p.rect.width, p.rect.height
            if w <= 0 or h <= 0 or h / w > MAX_PROPORCAO_PAGINA or w / h > MAX_PROPORCAO_PAGINA:
                raise FotoInvalida(
                    "pdf_pagina", "Uma página deste PDF tem um formato que não vira imagem."
                )
            alturas.append(h * LARGURA_PDF_IMAGEM / w)
        largura, altura = LARGURA_PDF_IMAGEM, sum(alturas)
        polegadas2 = (largura / 72) * (altura / 72)
        dpi = min(DPI_PDF_IMAGEM, int((MAX_PIXELS_PDF_IMAGEM / polegadas2) ** 0.5))
        if dpi < MIN_DPI_PDF_IMAGEM:
            raise FotoInvalida(
                "pdf_paginas", "Este PDF é grande demais para virar uma imagem legível."
            )
        alvo = pymupdf.open()
        folha = alvo.new_page(width=largura, height=altura)
        y = 0.0
        for i, h in enumerate(alturas):
            folha.show_pdf_page(pymupdf.Rect(0, y, largura, y + h), origem, i)
            y += h
        jpeg = folha.get_pixmap(dpi=dpi, alpha=False).tobytes("jpeg", jpg_quality=85)
        alvo.close()
    except FotoInvalida:
        raise
    except Exception as e:  # noqa: BLE001 — página que não renderiza
        raise FotoInvalida("pdf_invalido", "Não consegui transformar este PDF em imagem.") from e
    finally:
        origem.close()
    raiz = anexo.nome.rsplit(".", 1)[0] or "anexo"
    return Foto(
        conteudo=jpeg,
        nome=f"{raiz}.jpg",
        mime="image/jpeg",
        sha256=hashlib.sha256(jpeg).hexdigest(),
    )


def para_a_caixa(conversa: AtendimentoConversa, anexo: Foto) -> Foto:
    """O anexo como esta caixa recebe: PDF fica PDF só onde a plataforma aceita."""
    if anexo.mime == PDF and not aceita_pdf(conversa):
        return pdf_em_imagem(anexo)
    if (
        anexo.mime == PDF
        and (conversa.plataforma, conversa.canal) == ("ml", CANAL_RECLAMACAO)
        and anexo.tamanho > MAX_BYTES_RECLAMACAO_ML
    ):
        # A foto é reduzida na hora de subir; o PDF não tem como (doc: 5 MB).
        raise FotoInvalida("pdf_grande", "A reclamação do Mercado Livre aceita PDF de até 5 MB.")
    return anexo


def validar_para_a_caixa(
    conversa: AtendimentoConversa, dados: bytes | None, nome: str | None, mime: str | None
) -> Foto:
    """`validar_foto` + `para_a_caixa` de uma vez — síncrona: a rota roda em thread
    (abrir um PDF corrompido de 10 MB reconstrói a xref; não no laço de eventos)."""
    return para_a_caixa(conversa, validar_foto(dados, nome, mime))


def legenda_obrigatoria(conversa: AtendimentoConversa) -> bool:
    """No ML a foto vai dentro de uma mensagem com texto."""
    return (conversa.plataforma, conversa.canal) in CAIXAS_COM_LEGENDA


def legenda_permitida(conversa: AtendimentoConversa) -> bool:
    """ML (obrigatória) e Zap (opcional) levam texto junto; Shopee e TikTok mandam
    a foto SOZINHA (o texto vai pela caixa)."""
    par = (conversa.plataforma, conversa.canal)
    return legenda_obrigatoria(conversa) or par in CAIXAS_COM_LEGENDA_OPCIONAL


def limite_da_legenda(conversa: AtendimentoConversa) -> int | None:
    """O teto próprio da legenda (Zap: 1.024); None = o limite do texto da caixa."""
    return LIMITE_LEGENDA.get((conversa.plataforma, conversa.canal))


def preparar_para(
    plataforma: str, foto: Foto, *, teto_caixa: int | None = None
) -> tuple[str, bytes, str]:
    """(nome, bytes, tipo) dentro do teto da plataforma — reduz se precisar.

    A redução é a das evidências de devolução (PyMuPDF, JPEG 85, encolhendo
    pela metade até caber). Levanta `FotoInvalida` se nem assim couber.
    `teto_caixa`: o teto próprio da caixa (a reclamação do ML: 5 MB), para a
    foto E para o PDF.
    """
    from app.services.chamados_devolucao import preparar_foto

    if foto.mime == PDF:
        # O PDF vai como está — só para quem recebe arquivo (`para_a_caixa`).
        teto_pdf = teto_caixa or MAX_BYTES_PDF_PLATAFORMA.get(plataforma)
        if teto_pdf is None:
            raise FotoInvalida("pdf_nao_suportado", "Esta plataforma não recebe PDF por aqui.")
        if foto.tamanho > teto_pdf:
            raise FotoInvalida(
                "pdf_grande",
                f"O PDF passa do tamanho que a plataforma aceita ({teto_pdf // 1_000_000} MB).",
            )
        return foto.nome, foto.conteudo, foto.mime
    teto = teto_caixa or MAX_BYTES_PLATAFORMA.get(plataforma, MAX_BYTES_PLATAFORMA["shopee"])
    anexo = SimpleNamespace(content_type=foto.mime, blob=foto.conteudo, filename=foto.nome)
    try:
        return preparar_foto(anexo, max_bytes=teto)
    except Exception as e:  # noqa: BLE001 — imagem corrompida, grande demais
        raise FotoInvalida(
            "foto_grande",
            f"Não deu para reduzir a foto ao tamanho que a plataforma aceita ({teto // 1000} KB).",
        ) from e


# ── Envio por plataforma ──────────────────────────────────────────────────


async def enviar_imagem(
    session: Any,
    conversa: AtendimentoConversa,
    integration: Any,
    cliente: Any,
    foto: Foto,
    legenda: str | None,
    *,
    para: str | None = None,
) -> ResultadoEnvio:
    """Sobe a foto e manda a mensagem com ela. Nunca levanta.

    O `payload` leva `imagem_url` (Shopee/TikTok: a URL da plataforma, que a
    tela mostra no balão) ou `ml_anexo` (ML: o id do anexo).
    Reclamação do ML: `para` = `comprador` | `mediador` (`ml.responder_reclamacao`).

    O Zap NÃO passa por aqui (`zap.enviar_imagem`, com o destino montado
    pelas travas do Zap): chegar aqui com ele é um caminho novo que pulou as
    travas — cinto, nada sai.
    """
    if conversa.plataforma == PLATAFORMA_WHATSAPP:
        return ResultadoEnvio(ok=False, erro="zap_sem_destino")
    reclamacao = conversa.plataforma == "ml" and conversa.canal == CANAL_RECLAMACAO
    try:
        nome, dados, mime = preparar_para(
            conversa.plataforma,
            foto,
            teto_caixa=MAX_BYTES_RECLAMACAO_ML if reclamacao else None,
        )
    except FotoInvalida as e:
        return ResultadoEnvio(ok=False, erro=f"{conversa.plataforma} {e.code}")
    try:
        if reclamacao:
            return await _ml_reclamacao(conversa, cliente, nome, dados, mime, legenda, para)
        if conversa.plataforma == "shopee":
            return await _shopee(conversa, cliente, nome, dados, mime)
        if conversa.plataforma == "tiktok":
            return await _tiktok(conversa, cliente, nome, dados, mime)
        if conversa.plataforma == "ml" and conversa.canal == CANAL_POS_VENDA:
            return await _ml(conversa, cliente, nome, dados, mime, legenda)
    except Exception as e:  # noqa: BLE001 — envio nunca levanta; sem saber, é ambíguo
        logger.warning(
            "atendimento_foto_envio_inesperado",
            conversa_id=str(conversa.id),
            erro=type(e).__name__,
        )
        return ResultadoEnvio(
            ok=False, ambiguo=True, erro=f"{conversa.plataforma} {type(e).__name__}"
        )
    return ResultadoEnvio(ok=False, erro=f"foto_nao_suportada:{conversa.plataforma}")


async def _shopee(
    conversa: AtendimentoConversa, cliente: Any, nome: str, dados: bytes, mime: str
) -> ResultadoEnvio:
    from app.services.atendimento.shopee import (
        _PISTAS_AMBIGUAS,
        _codigo_shopee,
        _erro_operacao,
        conferir_chat_do_afiliado,
        negocio_do_envio,
    )

    to_id = str(conversa.comprador_id or "").strip()
    if not to_id:
        return ResultadoEnvio(ok=False, erro="shopee sem_comprador")
    # 1. Upload: falhou aqui = nada saiu (a imagem nem existe na conversa).
    try:
        url = await cliente.chat_upload_image(nome, dados, mime)
    except httpx.HTTPError as exc:
        return ResultadoEnvio(ok=False, erro=f"shopee upload {type(exc).__name__}")
    except RuntimeError as exc:
        codigo = _codigo_shopee(exc) or "erro"
        return ResultadoEnvio(ok=False, erro=f"shopee upload {codigo}")
    # 2. A mensagem com a imagem: a mesma régua do texto — no chat de
    # AFILIADO (29/10/2026), com `business_type` 11 + o `conversation_id`.
    try:
        resp = await cliente.chat_send_message(to_id, image_url=url, **negocio_do_envio(conversa))
    except httpx.HTTPStatusError as exc:
        return ResultadoEnvio(ok=False, erro=f"shopee token_http_{exc.response.status_code}")
    except httpx.HTTPError as exc:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=_erro_operacao(exc))
    except ValueError:
        return ResultadoEnvio(ok=False, erro="shopee envio_invalido")
    except RuntimeError as exc:
        codigo = _codigo_shopee(exc)
        if codigo and not any(p in codigo for p in _PISTAS_AMBIGUAS):
            return ResultadoEnvio(ok=False, erro=f"shopee {codigo}")
        return ResultadoEnvio(ok=False, ambiguo=True, erro=_erro_operacao(exc))
    resp = resp if isinstance(resp, dict) else {}
    message_id = str(resp.get("message_id") or "").strip()
    return conferir_chat_do_afiliado(
        conversa,
        ResultadoEnvio(
            ok=True,
            externo_id=message_id or None,
            payload={**resp, "imagem_url": url},
        ),
    )


async def _tiktok(
    conversa: AtendimentoConversa, cliente: Any, nome: str, dados: bytes, mime: str
) -> ResultadoEnvio:
    from app.services.atendimento.tiktok import (
        _CODIGO_JANELA_FECHADA,
        _CODIGOS_AMBIGUOS,
        MOTIVO_JANELA,
        _codigo,
        _eh_sem_escopo,
    )

    if not str(conversa.externo_id or "").strip():
        return ResultadoEnvio(ok=False, erro="tiktok sem_conversa")
    # 1. Upload: falhou = nada saiu.
    try:
        imagem = await cliente.cs_upload_image(nome, dados, mime)
    except httpx.HTTPError as exc:
        return ResultadoEnvio(ok=False, erro=f"tiktok upload {type(exc).__name__}")
    except RuntimeError as exc:
        m = re.search(r"code=(\S+)", str(exc))
        return ResultadoEnvio(ok=False, erro=f"tiktok upload code={m.group(1) if m else 'erro'}")
    url = str((imagem or {}).get("url") or "")
    # 2. A mensagem IMAGE: a mesma régua do texto.
    try:
        resp = await cliente.cs_send_image(
            conversa.externo_id, url, (imagem or {}).get("width"), (imagem or {}).get("height")
        )
    except Exception as exc:  # noqa: BLE001 — pode ter saído
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"tiktok {type(exc).__name__}")
    resp = resp if isinstance(resp, dict) else {}
    codigo = _codigo(resp)
    if codigo == 0:
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        message_id = str(data.get("message_id") or "").strip()
        return ResultadoEnvio(
            ok=True, externo_id=message_id or None, payload={**resp, "imagem_url": url}
        )
    if _eh_sem_escopo(resp):
        return ResultadoEnvio(ok=False, erro="tiktok sem_escopo", payload=resp)
    if codigo == _CODIGO_JANELA_FECHADA:
        return ResultadoEnvio(
            ok=False, erro=f"tiktok code={codigo}", bloqueio=MOTIVO_JANELA, payload=resp
        )
    if codigo is None or codigo in _CODIGOS_AMBIGUOS or 500 <= codigo < 600:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"tiktok code={codigo}", payload=resp)
    return ResultadoEnvio(ok=False, erro=f"tiktok code={codigo}", payload=resp)


def _http(timeout: float) -> httpx.AsyncClient:
    """O cliente HTTP do upload do ML (o teste troca por um transporte falso)."""
    return httpx.AsyncClient(timeout=timeout)


class _UploadML(Exception):  # noqa: N818
    def __init__(self, erro: str) -> None:
        self.erro = erro
        super().__init__(erro)


async def _ml_upload(
    cliente: Any, caminho: str, params: dict | None, nome: str, dados: bytes, mime: str
) -> dict:
    """O POST multipart (`file`) de um anexo no ML; devolve o corpo. Levanta `_UploadML`.

    O `MercadoLivreClient` não tem upload multipart genérico: a chamada é
    feita aqui com o token DELE (renovando como ele renova: vencido antes,
    401 uma vez). Upload não fala com ninguém — repetir no 401 é seguro.
    """
    from app.services.marketplaces.ml import ML_API_BASE

    if cliente._expired():  # noqa: SLF001 — mesmo critério do cliente
        await cliente.refresh()
    r = None
    for tentativa in range(2):
        headers = {
            "Authorization": f"Bearer {cliente.access_token}",
            "Accept": "application/json",
        }
        async with _http(120.0) as c:
            r = await c.post(
                f"{ML_API_BASE}{caminho}",
                params=params,
                headers=headers,
                files={"file": (nome, dados, mime)},
            )
        if r.status_code == 401 and tentativa == 0:
            await cliente.refresh()
            continue
        break
    assert r is not None
    if r.status_code >= 400:
        try:
            corpo = r.json()
        except ValueError:
            corpo = None
        codigo = ""
        if isinstance(corpo, dict):
            codigo = str(corpo.get("code") or corpo.get("error") or "")[:60]
        raise _UploadML(f"upload HTTP {r.status_code} {codigo}".strip())
    try:
        corpo = r.json()
    except ValueError:
        corpo = None
    return corpo if isinstance(corpo, dict) else {}


async def _ml_subir_anexo(cliente: Any, nome: str, dados: bytes, mime: str) -> str:
    """Sobe o anexo da mensagem pós-venda; devolve o id. Levanta `_UploadML`.

    POST /messages/attachments?tag=post_sale&site_id=MLB (multipart `file`).
    """
    corpo = await _ml_upload(
        cliente,
        "/messages/attachments",
        {"tag": "post_sale", "site_id": ML_SITE},
        nome,
        dados,
        mime,
    )
    anexo = str(corpo.get("id") or "").strip()
    if not anexo:
        raise _UploadML("upload sem_id")
    return anexo


async def _ml_subir_anexo_reclamacao(
    cliente: Any, claim_id: str, nome: str, dados: bytes, mime: str
) -> str:
    """Sobe o anexo da mensagem na RECLAMAÇÃO; devolve o `filename`. Levanta `_UploadML`.

    POST /post-purchase/v1/claims/{id}/attachments (multipart `file`; o nome
    até 125 caracteres [A-Za-z0-9_.-] — o `_nome_limpo` já cabe).
    """
    corpo = await _ml_upload(
        cliente,
        f"/post-purchase/v1/claims/{claim_id}/attachments",
        None,
        _nome_limpo(nome, mime),
        dados,
        mime,
    )
    arquivo = str(corpo.get("filename") or corpo.get("file_name") or "").strip()
    if not arquivo:
        raise _UploadML("upload sem_filename")
    return arquivo


async def _ml_reclamacao(
    conversa: AtendimentoConversa,
    cliente: Any,
    nome: str,
    dados: bytes,
    mime: str,
    legenda: str | None,
    para: str | None,
) -> ResultadoEnvio:
    """A foto/PDF na mensagem da reclamação (ao comprador ou ao mediador)."""
    from app.services.atendimento import ml as adaptador_ml

    if not (legenda or "").strip():
        return ResultadoEnvio(ok=False, erro="ml legenda_vazia")
    if para is None:
        return ResultadoEnvio(ok=False, erro="reclamacao_sem_destino")

    async def subir(claim_id: str) -> str:
        return await _ml_subir_anexo_reclamacao(cliente, claim_id, nome, dados, mime)

    return await adaptador_ml.responder_reclamacao(
        None, conversa, None, cliente, legenda, para=para, subir_anexo=subir
    )


async def _ml(
    conversa: AtendimentoConversa,
    cliente: Any,
    nome: str,
    dados: bytes,
    mime: str,
    legenda: str | None,
) -> ResultadoEnvio:
    from app.services.atendimento import ml as adaptador_ml

    if not (legenda or "").strip():
        return ResultadoEnvio(ok=False, erro="ml legenda_vazia")
    seller_id = adaptador_ml._seller_id(cliente)  # noqa: SLF001 — o mesmo do texto
    if not seller_id:
        return ResultadoEnvio(ok=False, erro="sem_seller_id")
    dados_conversa = conversa.dados if isinstance(conversa.dados, dict) else {}
    destinatario = (
        adaptador_ml.AGENTE_ML_BR if dados_conversa.get("via_agente") else conversa.comprador_id
    )
    if not destinatario:
        return ResultadoEnvio(ok=False, erro="sem_comprador")
    # 1. Upload: falhou = nada saiu.
    try:
        anexo = await _ml_subir_anexo(cliente, nome, dados, mime)
    except _UploadML as e:
        return ResultadoEnvio(ok=False, erro=f"ml {e.erro}")
    except (httpx.HTTPError, RuntimeError) as e:
        return ResultadoEnvio(ok=False, erro=f"ml upload {type(e).__name__}")

    def _uid(v: Any) -> Any:
        return int(v) if str(v).isdigit() else str(v)

    pack_id = conversa.externo_id
    corpo = {
        "from": {"user_id": _uid(seller_id)},
        "to": {"user_id": _uid(destinatario)},
        "text": legenda,
        "attachments": [anexo],
    }
    # 2. A mensagem: a régua do texto (`_postar`: 2xx saiu, 4xx recusou —
    # com bloqueio quando é o caso —, 5xx/sem resposta ambíguo). Uma
    # tentativa só (`_request_uma_vez`): mensagem não se desenvia.
    resultado = await adaptador_ml._postar(  # noqa: SLF001
        lambda: cliente._request_uma_vez(  # noqa: SLF001
            "POST",
            f"/messages/packs/{pack_id}/sellers/{seller_id}",
            params={"tag": "post_sale"},
            json=corpo,
        ),
        conversa=conversa,
    )
    resultado.payload = {**(resultado.payload or {}), "ml_anexo": anexo}
    return resultado
