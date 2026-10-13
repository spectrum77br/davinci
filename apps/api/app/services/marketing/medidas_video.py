"""Duração e resolução do vídeo entregue, carimbadas no envio (migration 0392).

Marco, 13/10/2026: o Studio DaVinci (portal das agências) ganha um relatório
dos vídeos entregues — minutos, média, mediana, faixas de duração. Medir na
hora do relatório seria rodar ffprobe em centenas de arquivos a cada clique;
medir no envio é uma vez por arquivo, e o número fica no banco.

O ffprobe é o MESMO do vídeo da Shopee (`shopee_video_anuncio.sondar_video`,
com o cache por caminho/tamanho/mtime): duas sondas diferentes dariam duas
durações para o mesmo arquivo.

Regra que não se negocia: **a medição nunca barra o envio**. ffprobe ausente,
arquivo que ele não lê, tempo estourado ou qualquer outra exceção = as três
colunas ficam NULL e o vídeo entra igual. O relatório conta esses à parte
(`sem_duracao`) em vez de tratá-los como zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

import structlog
from sqlalchemy import func, or_

from app.models.marketing import MarketingCreativeFile
from app.services.marketing.anexos import _EXT_VIDEO, MIMES_VIDEO, caminho_confinado
from app.services.marketing.shopee_video_anuncio import sondar_video

logger = structlog.get_logger()

# NUMERIC(8,2): até 999999.99 s. Vídeo de criativo tem segundos; um número
# acima disso é cabeçalho torto, e gravá-lo estouraria o INSERT do envio.
_DURACAO_MAX_S = Decimal("999999.99")
# INT do Postgres. Mesmo raciocínio: largura absurda é leitura errada.
_LADO_MAX = 2_147_483_647


def eh_video(mime: str | None, nome: str | None) -> bool:
    """Vídeo pelo tipo OU pela extensão.

    O tipo é o que o NAVEGADOR de quem enviou disse (o portal repassa o
    `$_FILES['type']`), e há navegador que manda `application/octet-stream`
    para .mov. A extensão é a segunda opinião; as duas juntas são o mesmo
    conjunto que o portal toca (`anexos._EXT_VIDEO`).
    """
    m = (mime or "").strip().lower()
    if m in MIMES_VIDEO or m.startswith("video/"):
        return True
    return Path(nome or "").suffix.lower() in _EXT_VIDEO


def eh_video_sql():
    """A mesma regra do `eh_video`, no WHERE (relatório e backfill)."""
    exts = "|".join(e.lstrip(".") for e in sorted(_EXT_VIDEO))
    return or_(
        func.lower(func.coalesce(MarketingCreativeFile.file_mime, "")).like("video/%"),
        func.lower(MarketingCreativeFile.file_name).op("~")(rf"\.({exts})$"),
    )


def _duracao(valor: float | None) -> Decimal | None:
    if valor is None:
        return None
    try:
        d = Decimal(str(valor)).quantize(Decimal("0.01"))
    except ArithmeticError:
        return None
    if not d.is_finite() or d < 0 or d > _DURACAO_MAX_S:
        return None
    return d


def _lado(valor: int | None) -> int | None:
    if valor is None or valor <= 0 or valor > _LADO_MAX:
        return None
    return int(valor)


async def medir(rec: MarketingCreativeFile) -> bool:
    """Carimba `duracao_s`/`largura`/`altura` de UM arquivo. Nunca levanta.

    Devolve True se o ffprobe leu o vídeo. Não faz commit: quem chama decide
    (o envio grava junto com o arquivo; o backfill, em lote).
    """
    if not eh_video(rec.file_mime, rec.file_name):
        return False
    caminho = caminho_confinado(rec.file_rel)
    if caminho is None:
        return False
    try:
        info = await sondar_video(caminho)
    except Exception as e:  # noqa: BLE001 — medir é extra; o envio não pode cair por isso
        logger.warning("criativo_medida_falhou", arquivo_id=str(rec.id), err=str(e)[:160])
        return False
    if info is None:
        return False
    rec.duracao_s = _duracao(info.duracao)
    rec.largura = _lado(info.largura)
    rec.altura = _lado(info.altura)
    return True


async def medir_arquivos(arquivos: Iterable[MarketingCreativeFile]) -> int:
    """`medir` em cada arquivo recém-gravado; devolve quantos foram lidos."""
    lidos = 0
    for rec in arquivos:
        if await medir(rec):
            lidos += 1
    return lidos
