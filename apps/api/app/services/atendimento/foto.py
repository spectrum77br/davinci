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
  Amazon, Magalu, Temu, AliExpress, Instagram: sem foto pelo DaVinci (por
  enquanto).

Tamanho: a tela aceita até 10 MB e até `MAX_PIXELS` de resolução (JPEG ou
PNG, conferido pelos BYTES e pelo cabeçalho, não pelo nome); na hora de
subir, a foto é REDUZIDA até o teto medido de cada plataforma
(`chamados_devolucao.preparar_foto`, o mesmo das evidências de devolução):
Shopee 900 KB, TikTok 3 MB, ML 5 MB.

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
    ResultadoEnvio,
)

logger = structlog.get_logger()

# ── O que se aceita ───────────────────────────────────────────────────────
TIPOS_ACEITOS: dict[str, str] = {"image/jpeg": ".jpg", "image/png": ".png"}
# O que a TELA pode mandar ao DaVinci (antes da redução para a plataforma).
MAX_BYTES_UPLOAD = 10 * 1024 * 1024
# O teto de cada plataforma (medidos; ver `chamados_devolucao` e
# `devolucao_mensagem_comprador`): acima disto, a foto é reduzida.
MAX_BYTES_PLATAFORMA: dict[str, int] = {
    "shopee": 900_000,
    "tiktok": 3_000_000,
    "ml": 5 * 1024 * 1024,
}
# Teto de RESOLUÇÃO (largura × altura, lido do cabeçalho). Os 10 MB não
# bastam: um PNG de 1 MB com 9000×9000 pixels decodifica para mais de 1 GB
# na redução (PyMuPDF, síncrona — trava a api inteira enquanto roda). 40
# megapixels cobre a foto de celular e de câmera (12–24 MP); acima disso a
# pessoa reduz antes de anexar.
MAX_PIXELS = 40_000_000
# (plataforma, caixa) que aceitam foto pelo DaVinci.
CAIXAS_COM_FOTO = frozenset(
    {("shopee", CANAL_CHAT), ("tiktok", CANAL_CHAT), ("ml", CANAL_POS_VENDA)}
)
# No ML a foto vai DENTRO de uma mensagem com texto.
CAIXAS_COM_LEGENDA = frozenset({("ml", CANAL_POS_VENDA)})

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
        raise FotoInvalida("foto_tipo", "Só dá para mandar foto JPG ou PNG.")
    tamanho = dimensoes(dados)
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


def motivo_sem_foto(conversa: AtendimentoConversa) -> str | None:
    """Por que esta conversa não aceita foto pelo DaVinci; None = aceita."""
    par = (conversa.plataforma, conversa.canal)
    if par in CAIXAS_COM_FOTO:
        return None
    if conversa.plataforma == "ml":
        return "Pergunta no anúncio do Mercado Livre não aceita foto — só texto."
    return "Mandar foto por aqui ainda não está liberado para esta plataforma."


def legenda_obrigatoria(conversa: AtendimentoConversa) -> bool:
    """No ML a foto vai dentro de uma mensagem com texto."""
    return (conversa.plataforma, conversa.canal) in CAIXAS_COM_LEGENDA


def legenda_permitida(conversa: AtendimentoConversa) -> bool:
    """Shopee e TikTok mandam a foto SOZINHA (o texto vai pela caixa)."""
    return legenda_obrigatoria(conversa)


def preparar_para(plataforma: str, foto: Foto) -> tuple[str, bytes, str]:
    """(nome, bytes, tipo) dentro do teto da plataforma — reduz se precisar.

    A redução é a das evidências de devolução (PyMuPDF, JPEG 85, encolhendo
    pela metade até caber). Levanta `FotoInvalida` se nem assim couber.
    """
    from app.services.chamados_devolucao import preparar_foto

    teto = MAX_BYTES_PLATAFORMA.get(plataforma, MAX_BYTES_PLATAFORMA["shopee"])
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
) -> ResultadoEnvio:
    """Sobe a foto e manda a mensagem com ela. Nunca levanta.

    O `payload` leva `imagem_url` (Shopee/TikTok: a URL da plataforma, que a
    tela mostra no balão) ou `ml_anexo` (ML: o id do anexo).
    """
    try:
        nome, dados, mime = preparar_para(conversa.plataforma, foto)
    except FotoInvalida as e:
        return ResultadoEnvio(ok=False, erro=f"{conversa.plataforma} {e.code}")
    try:
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
    # 2. A mensagem com a imagem: a mesma régua do texto.
    try:
        resp = await cliente.chat_send_message(to_id, image_url=url)
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
    return ResultadoEnvio(
        ok=True,
        externo_id=message_id or None,
        payload={**resp, "imagem_url": url},
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


async def _ml_subir_anexo(cliente: Any, nome: str, dados: bytes, mime: str) -> str:
    """Sobe o anexo da mensagem pós-venda; devolve o id. Levanta `_UploadML`.

    POST /messages/attachments?tag=post_sale&site_id=MLB (multipart `file`).
    O `MercadoLivreClient` não tem upload multipart genérico: a chamada é
    feita aqui com o token DELE (renovando como ele renova: vencido antes,
    401 uma vez). Upload não fala com o comprador — repetir no 401 é seguro.
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
                f"{ML_API_BASE}/messages/attachments",
                params={"tag": "post_sale", "site_id": ML_SITE},
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
    anexo = str((corpo or {}).get("id") or "").strip() if isinstance(corpo, dict) else ""
    if not anexo:
        raise _UploadML("upload sem_id")
    return anexo


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
