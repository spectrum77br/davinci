"""Imagem guardada no banco, lida aqui dentro (sem passar pelo link).

Vinicius, 22/09/2026: "pega esse link e põe a foto dentro da caixa". O link
`/api/imagens/{id}` (routers/imagens.py) é pra quem está FORA — navegador,
aviso, mensagem. Quando é o próprio servidor que precisa dos bytes (desenhar
o cartão de rastreio, por exemplo), ele lê a linha direto: mesma imagem, sem
sair pela rede e sem depender do painel estar no ar.

Guarda em memória o que já leu porque o conteúdo de um id NUNCA muda (imagem
nova é linha nova — é o que o router promete no Cache-Control): uma rodada do
robô desenha dezenas de cartões com o mesmo logo.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImagemPublica

# Logo dos Correios, 205x161 — entrou pela migração 0305_imagem_publica.
LOGO_CORREIOS = UUID("b6a1f2c4-5d3e-4a7b-9c81-0f2e3d4c5b6a")

_cache: dict[UUID, bytes] = {}


async def carregar(session: AsyncSession, imagem_id: UUID) -> bytes | None:
    """Bytes da imagem, ou None quando o id não está no banco.

    None é resposta normal, não erro: nenhuma imagem daqui é obrigatória —
    quem chama desenha sem ela (nos testes a tabela nasce vazia, porque o
    conteúdo da migração não roda no `create_all`).
    """
    if (guardada := _cache.get(imagem_id)) is not None:
        return guardada
    blob = (
        await session.execute(select(ImagemPublica.blob).where(ImagemPublica.id == imagem_id))
    ).scalar_one_or_none()
    if blob:
        _cache[imagem_id] = blob
    return blob
