"""Imagem guardada no banco e servida por link ABERTO (sem login).

Vinicius 22/09: "sobe essa imagem no servidor e me passa o link dela… no banco
de dados". O ponto do recurso é o link funcionar FORA do painel — por isso o
teste que mais importa aqui é o do acesso sem sessão nenhuma.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.models import ImagemPublica

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")  # cabeçalho de PNG, basta pro teste
CORREIOS_ID = UUID("b6a1f2c4-5d3e-4a7b-9c81-0f2e3d4c5b6a")


async def test_link_abre_sem_login_e_devolve_a_imagem(client, db):
    img = ImagemPublica(nome="correios.png", content_type="image/png", size_bytes=len(PNG), blob=PNG)
    db.add(img)
    await db.commit()

    # sem auth_as: ninguém logado
    r = await client.get(f"/api/imagens/{img.id}")
    assert r.status_code == 200, r.text
    assert r.content == PNG
    assert r.headers["content-type"].startswith("image/png")
    assert "immutable" in r.headers.get("cache-control", "")
    assert "correios.png" in r.headers.get("content-disposition", "")


async def test_id_que_nao_existe_da_404(client, db):
    r = await client.get(f"/api/imagens/{uuid4()}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "imagem_not_found"
