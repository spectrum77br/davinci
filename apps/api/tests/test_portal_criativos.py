"""A porta estreita para as agências — o que ela deixa passar e o que não.

Estes testes existem por causa de um achado concreto: em 18/09/2026 uma
revisão encontrou que o webhook do Bling nunca rejeita (a função de
verificação não tem um `raise` sequer). Este router é o contrário disso, e é
isto que os primeiros testes travam: **sem configuração, fechado**.

Depois vem o que separa uma agência da outra, e o que a lista branca esconde.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.marketing import MarketingCreative, MarketingCreativeFile
from app.models.marketing_personagem import MarketingPersonagem, MarketingPersonagemArquivo
from app.models.marketing_roteiro import (
    MarketingRoteiro,
    MarketingRoteiroPersonagem,
    MarketingRoteiroRef,
)

TOK_A = "tok-agencia-a-0123456789"
TOK_B = "tok-agencia-b-9876543210"


@pytest.fixture(autouse=True)
def _dois_tokens(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "portal_tokens", f"{TOK_A}:alpha,{TOK_B}:beta"
    )


async def _linha(
    db: AsyncSession,
    *,
    equipe: str | None,
    roteiro: str | None = "cena 1: abre a mala",
    aprovado: bool | None = True,
    com_arquivo: bool = True,
) -> MarketingCreative:
    c = MarketingCreative(
        modelo="Mala de bordo 20kg",
        marca="poofy",
        sku="dgd23",
        equipe=equipe,
        roteiro=roteiro,
        aprovado=aprovado,
        legenda="LEGENDA INTERNA que não é da conta da agência",
        pushed_dest="/Marcas/Charlots/dgd23",
    )
    db.add(c)
    await db.flush()
    if com_arquivo:
        db.add(
            MarketingCreativeFile(
                creative_id=c.id,
                file_name="video.mp4",
                file_mime="video/mp4",
                file_size=1234,
                file_rel=f"creatives/{c.id}/video.mp4",
                sha256="a" * 64,
            )
        )
    await db.commit()
    await db.refresh(c)
    return c


# ─────────────── fecha por padrão ───────────────


async def test_sem_configuracao_o_portal_fica_fechado(client: AsyncClient, monkeypatch):
    """Segredo vazio = 401, NUNCA aberto. É a lição do webhook do Bling."""
    monkeypatch.setattr(get_settings(), "portal_tokens", "")
    r = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    assert r.status_code == 401


async def test_sem_header_401(client: AsyncClient):
    assert (await client.get("/api/portal/criativos")).status_code == 401


async def test_token_errado_401(client: AsyncClient):
    r = await client.get("/api/portal/criativos", headers={"X-Portal-Token": "chute"})
    assert r.status_code == 401


async def test_token_de_outra_agencia_nao_vira_admin(client: AsyncClient, db: AsyncSession):
    """Token válido dá acesso à própria equipe, não a um papel."""
    await _linha(db, equipe="alpha")
    r = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_B})
    assert r.status_code == 200
    assert r.json()["criativos"] == []


# ─────────────── cada agência vê a sua ───────────────


async def test_ve_so_a_propria_equipe(client: AsyncClient, db: AsyncSession):
    await _linha(db, equipe="alpha")
    await _linha(db, equipe="beta")
    await _linha(db, equipe=None)  # sem equipe: de ninguém, some pra todos

    a = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    b = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_B})
    assert a.json()["equipe"] == "alpha"
    assert len(a.json()["criativos"]) == 1
    assert len(b.json()["criativos"]) == 1


async def test_linha_sem_equipe_nao_vaza(client: AsyncClient, db: AsyncSession):
    """O `_user_equipes` do router interno trata 'sem equipe' como SEM
    RESTRIÇÃO. Aqui é o oposto: sem equipe não é de ninguém de fora."""
    await _linha(db, equipe=None)
    r = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    assert r.json()["criativos"] == []


# ─────────────── a lista branca ───────────────


async def test_nao_vaza_campo_interno(client: AsyncClient, db: AsyncSession):
    await _linha(db, equipe="alpha")
    bruto = (
        await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    ).text
    # legenda é decisão interna; pushed_dest é o caminho da pasta no MEGA;
    # file_rel é caminho no disco do servidor.
    for proibido in ("legenda", "pushed_dest", "file_rel", "product_id", "Marcas/"):
        assert proibido not in bruto, f"vazou {proibido!r}"


async def test_entregue_e_booleano_nao_caminho(client: AsyncClient, db: AsyncSession):
    """A agência precisa saber que foi entregue, não ONDE o arquivo mora."""
    await _linha(db, equipe="alpha")
    d = (
        await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    ).json()["criativos"][0]
    assert d["entregue"] is False
    assert set(d["arquivos"][0]) == {"id", "nome", "tamanho", "enviado_em"}

# ─────────────── roteiros (entidade própria, migration 0299) ───────────────
#
# Aqui vale a regra INVERTIDA: `equipe_destino` vazio = as DUAS agências.
# É o oposto de `marketing_creatives.equipe`, logo acima, onde vazio = ninguém
# de fora vê. As duas convivem, e estes testes travam as duas.

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


async def _roteiro(
    db: AsyncSession,
    *,
    destino: str | None,
    texto: str | None = "cena 1: abre a mala",
    ativo: bool = True,
) -> MarketingRoteiro:
    r = MarketingRoteiro(
        titulo="Mala de bordo 20kg",
        texto=texto,
        marca="poofy",
        sku="dgd23",
        equipe_destino=destino,
        ativo=ativo,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _ref_imagem(db: AsyncSession, roteiro: MarketingRoteiro, *, mime="image/png"):
    rel = f"roteiro_refs/{roteiro.id}/print.png"
    caminho = Path(get_settings().uploads_dir) / rel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(PNG)
    rec = MarketingRoteiroRef(
        roteiro_id=roteiro.id, tipo="imagem", file_name="print.png",
        file_mime=mime, file_size=len(PNG), file_rel=rel,
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return rec


async def test_roteiro_sem_destino_vai_para_as_duas(client: AsyncClient, db: AsyncSession):
    """A regra do Eduardo: "se não preenchido vai para os 2"."""
    await _roteiro(db, destino=None)
    for tok in (TOK_A, TOK_B):
        r = await client.get("/api/portal/roteiros", headers={"X-Portal-Token": tok})
        assert r.status_code == 200
        assert len(r.json()["roteiros"]) == 1, f"token {tok} não recebeu o roteiro aberto"


async def test_roteiro_endereçado_so_vai_pra_quem_foi_endereçado(
    client: AsyncClient, db: AsyncSession
):
    await _roteiro(db, destino="alpha")
    a = await client.get("/api/portal/roteiros", headers={"X-Portal-Token": TOK_A})
    b = await client.get("/api/portal/roteiros", headers={"X-Portal-Token": TOK_B})
    assert len(a.json()["roteiros"]) == 1
    assert b.json()["roteiros"] == []


async def test_roteiro_sem_texto_nao_aparece(client: AsyncClient, db: AsyncSession):
    """É este filtro que deixa o roteiro nascer visível sem passo de publicar:
    a linha recém-criada, ainda sendo escrita, não chega na agência."""
    await _roteiro(db, destino=None, texto=None)
    await _roteiro(db, destino=None, texto="   ")
    r = await client.get("/api/portal/roteiros", headers={"X-Portal-Token": TOK_A})
    assert r.json()["roteiros"] == []


async def test_roteiro_desligado_some_da_lista(client: AsyncClient, db: AsyncSession):
    await _roteiro(db, destino=None, ativo=False)
    r = await client.get("/api/portal/roteiros", headers={"X-Portal-Token": TOK_A})
    assert r.json()["roteiros"] == []


async def test_desligar_para_de_servir_a_imagem_tambem(client: AsyncClient, db: AsyncSession):
    """O achado que motivou `_visivel_pra_fora`: a rota de bytes NÃO pode
    resolver a referência pelo id direto. Se resolvesse, desligar o roteiro
    tiraria o card da tela e continuaria entregando o arquivo pra sempre a
    quem tivesse anotado o id."""
    rot = await _roteiro(db, destino=None)
    ref = await _ref_imagem(db, rot)
    url = f"/api/portal/roteiros/{rot.id}/referencia/{ref.id}"

    assert (await client.get(url, headers={"X-Portal-Token": TOK_A})).status_code == 200
    rot.ativo = False
    await db.commit()
    assert (await client.get(url, headers={"X-Portal-Token": TOK_A})).status_code == 404


async def test_reenderecar_corta_a_imagem_da_outra_agencia(
    client: AsyncClient, db: AsyncSession
):
    """Mesma trava pelo outro lado: a Bill Gates leu a lista enquanto estava
    aberta; corrigir o destino para 'alpha' tem que cortar os bytes também."""
    rot = await _roteiro(db, destino=None)
    ref = await _ref_imagem(db, rot)
    url = f"/api/portal/roteiros/{rot.id}/referencia/{ref.id}"

    assert (await client.get(url, headers={"X-Portal-Token": TOK_B})).status_code == 200
    rot.equipe_destino = "alpha"
    await db.commit()
    assert (await client.get(url, headers={"X-Portal-Token": TOK_B})).status_code == 404
    assert (await client.get(url, headers={"X-Portal-Token": TOK_A})).status_code == 200


async def test_referencia_com_mime_torto_nao_desce_como_html(
    client: AsyncClient, db: AsyncSession
):
    """A escrita já deduz o MIME da extensão; a leitura confere de novo, pra
    uma linha antiga não transformar esta rota num servidor de HTML dentro do
    site da agência."""
    rot = await _roteiro(db, destino=None)
    ref = await _ref_imagem(db, rot, mime="text/html")
    r = await client.get(
        f"/api/portal/roteiros/{rot.id}/referencia/{ref.id}",
        headers={"X-Portal-Token": TOK_A},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")


async def test_referencia_com_caminho_escapando_da_raiz_nao_abre(
    client: AsyncClient, db: AsyncSession
):
    rot = await _roteiro(db, destino=None)
    ref = await _ref_imagem(db, rot)
    ref.file_rel = "../../../../etc/passwd"
    await db.commit()
    r = await client.get(
        f"/api/portal/roteiros/{rot.id}/referencia/{ref.id}",
        headers={"X-Portal-Token": TOK_A},
    )
    assert r.status_code == 404


async def test_roteiro_nao_vaza_campo_interno(client: AsyncClient, db: AsyncSession):
    rot = await _roteiro(db, destino=None)
    await _ref_imagem(db, rot)
    bruto = (
        await client.get("/api/portal/roteiros", headers={"X-Portal-Token": TOK_A})
    ).text
    for proibido in ("file_rel", "created_by", "roteiro_refs/", "equipe_destino"):
        assert proibido not in bruto, f"vazou {proibido!r}"


async def test_detalhe_do_roteiro_respeita_o_mesmo_recorte(
    client: AsyncClient, db: AsyncSession
):
    rot = await _roteiro(db, destino="alpha")
    a = await client.get(
        f"/api/portal/roteiros/{rot.id}", headers={"X-Portal-Token": TOK_A}
    )
    b = await client.get(
        f"/api/portal/roteiros/{rot.id}", headers={"X-Portal-Token": TOK_B}
    )
    assert a.status_code == 200
    assert a.json()["roteiro"]["titulo"] == "Mala de bordo 20kg"
    assert b.status_code == 404, "404 e não 403: o que não é seu não existe"


async def test_agencia_nao_escreve_roteiro_nem_referencia(
    client: AsyncClient, db: AsyncSession
):
    """Quem escreve briefing é a equipe interna, no DaVinci. Este teste aponta
    pros caminhos NOVOS de propósito — um teste que só confere 404 em rota
    inexistente não é trava de nada."""
    rot = await _roteiro(db, destino=None)
    ref = await _ref_imagem(db, rot)
    h = {"X-Portal-Token": TOK_A}
    tentativas = [
        client.post("/api/portal/roteiros", headers=h, json={"titulo": "x"}),
        client.patch(f"/api/portal/roteiros/{rot.id}", headers=h, json={"texto": "x"}),
        client.delete(f"/api/portal/roteiros/{rot.id}", headers=h),
        client.post(
            f"/api/portal/roteiros/{rot.id}/referencia", headers=h,
            files={"files": ("x.png", PNG, "image/png")},
        ),
        client.post(
            f"/api/portal/roteiros/{rot.id}/referencia/link", headers=h,
            json={"url": "https://exemplo.com"},
        ),
        client.delete(f"/api/portal/roteiros/{rot.id}/referencia/{ref.id}", headers=h),
    ]
    for coro in tentativas:
        assert (await coro).status_code in (404, 405)


# ─────────────── personagens ───────────────


async def _personagem(db: AsyncSession, *, nome="Lívia", ativo=True, com_imagem=True):
    p = MarketingPersonagem(
        nome=nome,
        descricao="estudante brasileira de 22 anos",
        referencia="<<<48dbb6ed-155f-485d-90c0-1730bf39529d>>>",
        ativo=ativo,
    )
    db.add(p)
    await db.flush()
    if com_imagem:
        rel = f"personagens/{p.id}/rosto.png"
        caminho = Path(get_settings().uploads_dir) / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(PNG)
        db.add(
            MarketingPersonagemArquivo(
                personagem_id=p.id, tipo="imagem", file_name="rosto.png",
                file_mime="image/png", file_size=len(PNG), file_rel=rel,
            )
        )
    await db.commit()
    await db.refresh(p)
    return p


async def test_personagem_e_catalogo_global(client: AsyncClient, db: AsyncSession):
    """Decisão escrita: personagem não tem dono de equipe. As duas agências
    leem o mesmo elenco."""
    await _personagem(db)
    for tok in (TOK_A, TOK_B):
        r = await client.get("/api/portal/personagens", headers={"X-Portal-Token": tok})
        assert r.status_code == 200
        assert [p["nome"] for p in r.json()["personagens"]] == ["Lívia"]


async def test_personagem_leva_a_referencia_do_gerador(client: AsyncClient, db: AsyncSession):
    """Sem a `referencia`, o roteiro descreve uma pessoa genérica e cada
    geração inventa outro rosto — era o que os roteiros de produção colavam
    à mão dentro do prompt."""
    await _personagem(db)
    d = (
        await client.get("/api/portal/personagens", headers={"X-Portal-Token": TOK_A})
    ).json()["personagens"][0]
    assert d["referencia"] == "<<<48dbb6ed-155f-485d-90c0-1730bf39529d>>>"
    assert d["imagens"][0]["nome"] == "rosto.png"


async def test_personagem_desligado_some_e_para_de_servir_foto(
    client: AsyncClient, db: AsyncSession
):
    p = await _personagem(db)
    img = p.arquivos[0]
    url = f"/api/portal/personagens/{p.id}/arquivo/{img.id}"
    assert (await client.get(url, headers={"X-Portal-Token": TOK_A})).status_code == 200

    p.ativo = False
    await db.commit()
    lista = await client.get("/api/portal/personagens", headers={"X-Portal-Token": TOK_A})
    assert lista.json()["personagens"] == []
    assert (await client.get(url, headers={"X-Portal-Token": TOK_A})).status_code == 404


async def test_personagem_aparece_dentro_do_roteiro(client: AsyncClient, db: AsyncSession):
    """"Neste vídeo use a Lívia" — o vínculo que o Eduardo pediu."""
    rot = await _roteiro(db, destino=None)
    p = await _personagem(db)
    db.add(MarketingRoteiroPersonagem(roteiro_id=rot.id, personagem_id=p.id))
    await db.commit()

    d = (
        await client.get("/api/portal/roteiros", headers={"X-Portal-Token": TOK_A})
    ).json()["roteiros"][0]
    assert [x["nome"] for x in d["personagens"]] == ["Lívia"]


async def test_portal_de_personagens_tambem_fecha_sem_token(
    client: AsyncClient, db: AsyncSession
):
    await _personagem(db)
    assert (await client.get("/api/portal/personagens")).status_code == 401


# ─────────────── upload ───────────────


async def test_envia_arquivo_e_volta_pra_pendente(client: AsyncClient, db: AsyncSession):
    linha = await _linha(db, equipe="alpha", aprovado=True, com_arquivo=False)
    r = await client.post(
        f"/api/portal/criativos/{linha.id}/arquivo",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("novo.mp4", b"\x00\x00\x00\x18ftypmp42" + b"x" * 500, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["aprovado"] is None, "arquivo novo tem que voltar pra pendente"
    assert d["arquivos"][0]["nome"] == "novo.mp4"


async def test_nao_envia_em_linha_de_outra_equipe(client: AsyncClient, db: AsyncSession):
    """404, não 403: do lado de fora, o que não é seu não existe."""
    linha = await _linha(db, equipe="beta", com_arquivo=False)
    r = await client.post(
        f"/api/portal/criativos/{linha.id}/arquivo",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("x.mp4", b"abc", "video/mp4")},
    )
    assert r.status_code == 404


async def test_linha_de_outra_equipe_da_404_e_nao_403(client: AsyncClient, db: AsyncSession):
    linha = await _linha(db, equipe="beta")
    r = await client.post(
        f"/api/portal/criativos/{linha.id}/arquivo",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("x.mp4", b"abc", "video/mp4")},
    )
    assert r.status_code == 404
    assert "fora_da_sua_equipe" not in r.text, "não conte que a linha existe"


# ─────────────── entrega a partir do roteiro ───────────────
# O vínculo roteiro→criativo era o dado mais valioso e o menos preenchido: 5 de
# 49 em 22/09/2026. Estes testes existem para que ele deixe de depender de
# alguém lembrar de escolher a linha certa.


async def test_entrega_do_roteiro_cria_a_linha_ja_vinculada(
    client: AsyncClient, db: AsyncSession
):
    r0 = await _roteiro(db, destino="alpha")
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/entrega",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("peca.mp4", b"bytes de video", "video/mp4")},
    )
    assert r.status_code == 200
    linha = r.json()
    # Herda do briefing em vez de pedir de novo à agência: linha e roteiro não
    # podem discordar sobre de que produto estão falando.
    assert linha["marca"] == "poofy"
    assert linha["sku"] == "dgd23"

    row = (
        await db.execute(
            select(MarketingCreative).where(MarketingCreative.roteiro_id == r0.id)
        )
    ).scalar_one()
    assert row.equipe == "alpha"
    assert row.aprovado is None  # entregar não é aprovar
    assert len(row.files) == 1


async def test_entrega_em_roteiro_de_outra_agencia_nao_existe(
    client: AsyncClient, db: AsyncSession
):
    """404, não 403: quem não pode ver também não aprende que aquilo existe."""
    r0 = await _roteiro(db, destino="beta")
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/entrega",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("peca.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 404


async def test_entrega_em_roteiro_desligado_nao_existe(
    client: AsyncClient, db: AsyncSession
):
    """`ativo` é o interruptor, e ele vale nas TRÊS portas: lista, bytes e agora
    entrega. Senão despublicar continuaria aceitando arquivo de quem anotou o id."""
    r0 = await _roteiro(db, destino="alpha", ativo=False)
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/entrega",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("peca.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 404


async def test_entrega_em_roteiro_sem_texto_nao_existe(
    client: AsyncClient, db: AsyncSession
):
    """Roteiro sem texto é rascunho sendo escrito — não pode receber entrega."""
    r0 = await _roteiro(db, destino=None, texto=None)
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/entrega",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("peca.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 404


async def test_entrega_sem_token_401(client: AsyncClient, db: AsyncSession):
    r0 = await _roteiro(db, destino=None)
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/entrega",
        files={"files": ("peca.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 401


# ─────────────── versão do roteiro, e não edição por cima ───────────────


async def test_versao_cria_nova_linha_e_nao_toca_no_original(
    client: AsyncClient, db: AsyncSession
):
    r0 = await _roteiro(db, destino=None, texto="ideia da casa")
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/versao",
        headers={"X-Portal-Token": TOK_A},
        json={"texto": "a leitura que a agência fez"},
    )
    assert r.status_code == 200
    novo = r.json()
    assert novo["origem_id"] == str(r0.id)

    await db.refresh(r0)
    assert r0.texto == "ideia da casa"  # o "antes" sobrevive

    versao = (
        await db.execute(
            select(MarketingRoteiro).where(MarketingRoteiro.id == UUID(novo["id"]))
        )
    ).scalar_one()
    # Endereçada só a quem escreveu: a ideia da casa continua valendo pras duas,
    # a leitura de uma delas é dela.
    assert versao.equipe_destino == "alpha"
    assert versao.marca == r0.marca and versao.sku == r0.sku


async def test_versao_de_versao_aponta_pra_raiz(client: AsyncClient, db: AsyncSession):
    """Senão vira corrente e ninguém acha mais a ideia de partida."""
    r0 = await _roteiro(db, destino=None)
    p = await client.post(
        f"/api/portal/roteiros/{r0.id}/versao",
        headers={"X-Portal-Token": TOK_A},
        json={"texto": "primeira versão"},
    )
    filha = p.json()["id"]
    n = await client.post(
        f"/api/portal/roteiros/{filha}/versao",
        headers={"X-Portal-Token": TOK_A},
        json={"texto": "segunda versão"},
    )
    assert n.status_code == 200
    assert n.json()["origem_id"] == str(r0.id)


async def test_versao_sem_texto_recusada(client: AsyncClient, db: AsyncSession):
    r0 = await _roteiro(db, destino=None)
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/versao",
        headers={"X-Portal-Token": TOK_A},
        json={"texto": "   "},
    )
    assert r.status_code == 400


async def test_versao_de_roteiro_de_outra_agencia_404(client: AsyncClient, db: AsyncSession):
    r0 = await _roteiro(db, destino="beta")
    r = await client.post(
        f"/api/portal/roteiros/{r0.id}/versao",
        headers={"X-Portal-Token": TOK_A},
        json={"texto": "x"},
    )
    assert r.status_code == 404


# ─────────────── requisição de personagem ───────────────


async def test_requisicao_nasce_pendente_e_nao_cria_personagem(
    client: AsyncClient, db: AsyncSession
):
    r = await client.post(
        "/api/portal/personagens/requisicao",
        headers={"X-Portal-Token": TOK_A},
        json={
            "nome": "Joana Enfermeira",
            "descricao": "plantonista, 35 anos",
            "origem_imagem": "gerada no Higgsfield, prompt e seed guardados",
            "origem_voz": "banco de vozes licenciado da própria ferramenta",
            "cessao": False,
        },
    )
    assert r.status_code == 201
    assert r.json()["status"] == "pendente"

    # Ninguém vira personagem sem alguém de dentro aprovar.
    achou = (
        await db.execute(
            select(MarketingPersonagem).where(MarketingPersonagem.nome == "Joana Enfermeira")
        )
    ).first()
    assert achou is None


async def test_requisicao_sem_procedencia_recusada(client: AsyncClient):
    """Rosto sem origem declarada não chega à mesa de quem decide — Súmula 403."""
    r = await client.post(
        "/api/portal/personagens/requisicao",
        headers={"X-Portal-Token": TOK_A},
        json={"nome": "Sem Origem", "origem_imagem": "  ", "origem_voz": "  "},
    )
    assert r.status_code == 400


async def test_requisicao_sem_campo_de_origem_e_422(client: AsyncClient):
    r = await client.post(
        "/api/portal/personagens/requisicao",
        headers={"X-Portal-Token": TOK_A},
        json={"nome": "Faltando"},
    )
    assert r.status_code == 422


async def test_requisicao_de_nome_que_ja_existe_409(client: AsyncClient, db: AsyncSession):
    db.add(MarketingPersonagem(nome="Lívia"))
    await db.commit()
    r = await client.post(
        "/api/portal/personagens/requisicao",
        headers={"X-Portal-Token": TOK_A},
        json={"nome": "lívia", "origem_imagem": "x", "origem_voz": "y"},
    )
    assert r.status_code == 409


async def test_requisicao_sem_token_401(client: AsyncClient):
    r = await client.post(
        "/api/portal/personagens/requisicao",
        json={"nome": "X", "origem_imagem": "a", "origem_voz": "b"},
    )
    assert r.status_code == 401


async def test_a_agencia_le_o_veredito_do_proprio_pedido(client: AsyncClient):
    """A recusa volta COM o motivo — senão o mesmo pedido volta igual depois."""
    await client.post(
        "/api/portal/personagens/requisicao",
        headers={"X-Portal-Token": TOK_A},
        json={"nome": "Meu Pedido", "origem_imagem": "a", "origem_voz": "b"},
    )
    r = await client.get(
        "/api/portal/personagens/requisicoes", headers={"X-Portal-Token": TOK_A}
    )
    assert r.status_code == 200, "a rota precisa vir ANTES de /personagens/{id}"
    nomes = [x["nome"] for x in r.json()["requisicoes"]]
    assert "Meu Pedido" in nomes


async def test_uma_agencia_nao_ve_o_pedido_da_outra(client: AsyncClient):
    await client.post(
        "/api/portal/personagens/requisicao",
        headers={"X-Portal-Token": TOK_A},
        json={"nome": "So Da A", "origem_imagem": "a", "origem_voz": "b"},
    )
    r = await client.get(
        "/api/portal/personagens/requisicoes", headers={"X-Portal-Token": TOK_B}
    )
    assert r.status_code == 200
    assert "So Da A" not in [x["nome"] for x in r.json()["requisicoes"]]
