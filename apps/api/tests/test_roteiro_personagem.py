"""Aba Roteiros e Personagens — o que entra, e o que nunca vira href.

Eduardo, 21/09/2026: o briefing saiu da linha de produção, ganhou destino por
agência e passou a carregar personagens. As três coisas ATRAVESSAM pro portal
— um site PHP que não é nosso — então o que entra aqui é o que sai lá.

Daí o foco: a lista branca da extensão (nada de SVG nem HTML), o MIME vindo
da extensão e não do uploader, o esquema do link, e a lápide do campo antigo.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MarketingCreative, UserRole

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200
R = "/api/marketing/roteiros"
P = "/api/marketing/personagens"


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _novo(client: AsyncClient, **extra) -> dict:
    corpo = {"titulo": "video 30s — mala de bordo", "texto": "cena 1", **extra}
    r = await client.post(R, json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


# ─────────────── links de produto ───────────────


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "  javascript:alert(1)  ",  # o strip não pode liberar o esquema
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//evil.example.com/pagina",  # protocol-relative também é navegável
        "/produtos/dgd23",  # relativo: o portal é outro domínio
        "",
        "   ",
    ],
)
async def test_link_fora_de_http_e_recusado(client: AsyncClient, admin, url: str):
    """LISTA BRANCA. O campo vira `href` num site que não é nosso."""
    rot = await _novo(client)
    r = await client.post(f"{R}/{rot['id']}/referencia/link", json={"url": url})
    assert r.status_code == 400, f"{url!r} passou"
    assert r.json()["detail"]["code"] in {"link_invalido", "link_vazio"}


async def test_link_http_entra_com_titulo(client: AsyncClient, admin):
    rot = await _novo(client)
    r = await client.post(
        f"{R}/{rot['id']}/referencia/link",
        json={"url": "https://uranyx.com.br/p/dgd23", "titulo": "página do produto"},
    )
    assert r.status_code == 200, r.text
    ref = r.json()["referencias"][0]
    assert (ref["tipo"], ref["url"], ref["titulo"]) == (
        "link", "https://uranyx.com.br/p/dgd23", "página do produto",
    )


# ─────────────── imagens de referência ───────────────


@pytest.mark.parametrize(
    "nome",
    ["golpe.html", "vetor.svg", "macro.svgz", "filme.mp4", "sem_extensao"],
)
async def test_extensao_fora_da_lista_e_recusada(client: AsyncClient, admin, nome: str):
    """SVG entra aqui junto com HTML: é XML, carrega <script> e roda no
    domínio de quem abrir. MP4 é recusado por outro motivo — vídeo é ENTREGA,
    e entrega mora na tabela do criativo."""
    rot = await _novo(client)
    r = await client.post(
        f"{R}/{rot['id']}/referencia", files={"files": (nome, PNG, "image/png")}
    )
    assert r.status_code == 400, f"{nome!r} passou"
    assert r.json()["detail"]["code"] == "extensao_nao_aceita"


async def test_mime_vem_da_extensao_e_nao_do_uploader(client: AsyncClient, admin):
    """Quem sobe o arquivo escolhe o `Content-Type`. Se o banco guardasse esse
    valor, a rota que serve a imagem estaria servindo o tipo que um terceiro
    escolheu."""
    rot = await _novo(client)
    r = await client.post(
        f"{R}/{rot['id']}/referencia", files={"files": ("print.png", PNG, "text/html")}
    )
    assert r.status_code == 200, r.text
    assert r.json()["referencias"][0]["file_mime"] == "image/png"


async def test_baixar_e_apagar_referencia(client: AsyncClient, admin):
    rot = await _novo(client)
    ref = (
        await client.post(
            f"{R}/{rot['id']}/referencia", files={"files": ("print.png", PNG, "image/png")}
        )
    ).json()["referencias"][0]

    baixa = await client.get(f"{R}/{rot['id']}/referencia/{ref['id']}")
    assert baixa.status_code == 200
    assert baixa.headers["content-type"] == "image/png"
    assert baixa.headers["x-content-type-options"] == "nosniff"
    assert baixa.content == PNG

    apaga = await client.delete(f"{R}/{rot['id']}/referencia/{ref['id']}")
    assert apaga.status_code == 200
    assert apaga.json()["referencias"] == []


async def test_link_nao_e_baixavel(client: AsyncClient, admin):
    """`tipo='link'` não tem arquivo no disco — a rota de download não pode
    cair num `None` e virar leitura de caminho vazio."""
    rot = await _novo(client)
    ref = (
        await client.post(
            f"{R}/{rot['id']}/referencia/link", json={"url": "https://exemplo.com"}
        )
    ).json()["referencias"][0]
    assert (await client.get(f"{R}/{rot['id']}/referencia/{ref['id']}")).status_code == 404


# ─────────────── destino (a regra invertida) ───────────────


async def test_destino_vazio_vira_null(client: AsyncClient, admin):
    """"Se não preenchido vai para os 2" — e `"  "` tem que virar NULL, senão
    o destino passa a ser um nome feito de espaço, que não casa com token
    nenhum e o roteiro some sem erro."""
    for vazio in ("", "   ", None):
        rot = await _novo(client, equipe_destino=vazio)
        assert rot["equipe_destino"] is None


async def test_usuario_restrito_nao_ve_roteiro_de_outra_equipe(
    client: AsyncClient, db: AsyncSession, make_user, auth_as
):
    """Hoje o texto mora na linha do criativo, e a linha JÁ é filtrada por
    equipe. Sem este recorte, tirar o roteiro de lá abriria o briefing de
    todas as equipes pra todo usuário restrito no dia do deploy."""
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    await _novo(client, titulo="da alpha", equipe_destino="alpha")
    await _novo(client, titulo="da beta", equipe_destino="beta")
    await _novo(client, titulo="de todos")

    restrito = await make_user(
        role=UserRole.USER, permissions={"marketing_criativos": {"view": True}}
    )
    restrito.marketing_teams = ["alpha"]
    await db.commit()
    auth_as(restrito)

    titulos = {x["titulo"] for x in (await client.get(R)).json()}
    assert titulos == {"da alpha", "de todos"}, "sem destino é de todos; da beta não"


async def test_restrito_nao_cria_roteiro_para_outra_equipe(
    client: AsyncClient, db: AsyncSession, make_user, auth_as
):
    """O PATCH já barrava; o POST não — e o commit vinha ANTES do 403, então a
    linha ficava gravada, visível pra agência errada, e a própria autora não
    conseguia mais apagar (o `_get` passava a escondê-la dela)."""
    restrito = await make_user(
        role=UserRole.USER, permissions={"marketing_criativos": {"edit": True, "view": True}}
    )
    restrito.marketing_teams = ["Mindset"]
    await db.commit()
    auth_as(restrito)

    r = await client.post(
        R, json={"titulo": "Mala 20kg", "texto": "cena 1", "equipe_destino": "Bill Gates"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "fora_da_sua_equipe"
    # E, o que mais importa: NÃO pode ter sobrado linha nenhuma no banco.
    assert (await client.get(R)).json() == [], "403 não pode deixar o roteiro gravado"


async def test_restrito_nao_solta_roteiro_para_as_duas(
    client: AsyncClient, db: AsyncSession, make_user, auth_as
):
    """Destino vazio = as DUAS agências. Quem só enxerga uma não pode publicar
    pra outra que ele nem vê — é a mesma decisão que o PATCH já tomava."""
    restrito = await make_user(
        role=UserRole.USER, permissions={"marketing_criativos": {"edit": True, "view": True}}
    )
    restrito.marketing_teams = ["Mindset"]
    await db.commit()
    auth_as(restrito)

    r = await client.post(R, json={"titulo": "Livre", "texto": "cena 1"})
    assert r.status_code == 403
    assert (await client.get(R)).json() == []


async def test_destinos_nao_conta_as_equipes_alheias(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, admin
):
    """O select não pode oferecer opção que responde 403 — nem contar pro
    restrito o nome das agências que não são dele."""
    await _novo(client, equipe_destino=None)  # admin cria, pra existir equipe
    restrito = await make_user(
        role=UserRole.USER, permissions={"marketing_criativos": {"edit": True, "view": True}}
    )
    restrito.marketing_teams = ["Mindset"]
    await db.commit()
    auth_as(restrito)

    destinos = (await client.get(f"{R}/destinos")).json()
    assert destinos == ["Mindset"]


async def test_reenviar_arquivo_grande_demais_nao_destroi_o_que_ja_estava(
    client: AsyncClient, admin
):
    """Abrir o destino em "wb" trunca antes de saber se o novo cabe: o
    `print.png` bom era apagado pelo `print.png` grande demais, e a linha do
    banco sobrevivia apontando pro vazio."""
    rot = await _novo(client)
    bom = await client.post(
        f"{R}/{rot['id']}/referencia", files={"files": ("print.png", PNG, "image/png")}
    )
    assert bom.status_code == 200
    ref = bom.json()["referencias"][0]

    gigante = b"\x89PNG\r\n\x1a\n" + b"x" * (26 * 1024 * 1024)
    estourou = await client.post(
        f"{R}/{rot['id']}/referencia", files={"files": ("print.png", gigante, "image/png")}
    )
    assert estourou.status_code == 413

    # O arquivo bom continua servindo, byte a byte.
    baixa = await client.get(f"{R}/{rot['id']}/referencia/{ref['id']}")
    assert baixa.status_code == 200, "o upload recusado apagou o arquivo que estava lá"
    assert baixa.content == PNG


async def test_nome_longo_demais_da_400_e_nao_500(client: AsyncClient, admin):
    """Nome de 300 caracteres estourava no open() (OSError 63) ou no INSERT —
    500 com stack trace em vez de um 400 dizendo o que houve."""
    rot = await _novo(client)
    r = await client.post(
        f"{R}/{rot['id']}/referencia", files={"files": ("x" * 300 + ".png", PNG, "image/png")}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "nome_longo_demais"


# String vazia NÃO entra na lista: ela cai no default "arquivo" de propósito,
# e aí morre um passo depois, no `mime_da_extensao` (sem extensão = 400).
@pytest.mark.parametrize("nome", ["ok\x00.png", "a\tb.png", "..", "."])
def test_nome_seguro_recusa_nome_torto(nome: str):
    """Testado na função, não pela rota: o multipart do httpx/starlette já
    normaliza byte nulo e tab antes de chegar no servidor, então pela rota
    este teste passaria verde sem exercitar a guarda. Ela continua valendo
    pra qualquer outro caminho que alimente `file_name` (import, script)."""
    from fastapi import HTTPException

    from app.services.marketing.anexos import nome_seguro

    with pytest.raises(HTTPException) as exc:
        nome_seguro(nome)
    assert exc.value.status_code == 400


def test_nome_seguro_tira_a_pasta():
    from app.services.marketing.anexos import nome_seguro

    assert nome_seguro("../../etc/passwd") == "passwd"
    assert nome_seguro("print.png") == "print.png"


# ─────────────── personagens ───────────────


async def test_personagem_guarda_a_referencia_do_gerador(client: AsyncClient, admin):
    """As três partes: quem é, como chamamos, e a etiqueta que o gerador
    entende — era o que os roteiros de produção colavam à mão no prompt."""
    r = await client.post(
        P,
        json={
            "nome": "Lívia",
            "descricao": "estudante brasileira de 22 anos",
            "referencia": "<<<48dbb6ed-155f-485d-90c0-1730bf39529d>>>",
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["referencia"] == "<<<48dbb6ed-155f-485d-90c0-1730bf39529d>>>"
    assert d["ativo"] is True


async def test_personagem_repetido_da_409(client: AsyncClient, admin):
    """Dois "Lívia" no select do roteiro e ninguém sabe qual é qual."""
    assert (await client.post(P, json={"nome": "Lívia"})).status_code == 200
    r = await client.post(P, json={"nome": "lívia"})  # maiúscula não é outro
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "personagem_repetido"


async def test_foto_do_personagem_nao_aceita_pdf(client: AsyncClient, admin):
    """A referência do briefing aceita PDF; a FOTO do personagem não — aqui é
    rosto, e cada tipo a menos é superfície a menos."""
    p = (await client.post(P, json={"nome": "Lívia"})).json()
    r = await client.post(
        f"{P}/{p['id']}/arquivo", files={"files": ("ficha.pdf", PNG, "application/pdf")}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "extensao_nao_aceita"


MP3 = b"ID3\x03\x00\x00\x00" + b"\x00" * 400


async def test_personagem_guarda_voz_e_video_de_referencia(client: AsyncClient, admin):
    """As 8 personas reais têm as duas coisas: um MP3 de voz e um link de
    Shorts. Sem isso, o cadastro descreve o rosto e some com o resto."""
    p = (
        await client.post(
            P,
            json={
                "nome": "Elias",
                "descricao": "pedreiro autônomo brasileiro, 38 anos",
                "video_url": "https://www.youtube.com/shorts/UW33EHPAjaY",
            },
        )
    ).json()
    assert p["video_url"] == "https://www.youtube.com/shorts/UW33EHPAjaY"

    voz = await client.post(
        f"{P}/{p['id']}/arquivo?tipo=voz",
        files={"files": ("Elias-Pedreiro.mp3", MP3, "audio/mpeg")},
    )
    assert voz.status_code == 200, voz.text
    d = voz.json()
    assert [v["file_name"] for v in d["vozes"]] == ["Elias-Pedreiro.mp3"]
    assert d["vozes"][0]["file_mime"] == "audio/mpeg"
    assert d["imagens"] == [], "voz não pode entrar como foto"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.example.com", "data:text/html,x"])
async def test_video_de_referencia_fora_de_http_e_recusado(
    client: AsyncClient, admin, url: str
):
    """Esse campo vira href no site das agências, igual ao link de produto."""
    r = await client.post(P, json={"nome": "Elias", "video_url": url})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "link_invalido"


async def test_voz_nao_aceita_imagem_nem_o_contrario(client: AsyncClient, admin):
    """A lista branca é escolhida pelo `tipo`: é ela que impede subir um HTML
    disfarçado de voz, ou um MP3 que a tela tentaria desenhar."""
    p = (await client.post(P, json={"nome": "Elias"})).json()
    como_voz = await client.post(
        f"{P}/{p['id']}/arquivo?tipo=voz", files={"files": ("rosto.png", PNG, "image/png")}
    )
    assert como_voz.status_code == 400
    como_foto = await client.post(
        f"{P}/{p['id']}/arquivo?tipo=imagem", files={"files": ("voz.mp3", MP3, "audio/mpeg")}
    )
    assert como_foto.status_code == 400


async def test_arquivo_do_personagem_pode_ser_baixado(client: AsyncClient, admin):
    """O ponto do pedido do Eduardo: a etiqueta pode quebrar, o arquivo não —
    então a agência tem que conseguir levar o arquivo, não só ver."""
    p = (await client.post(P, json={"nome": "Elias"})).json()
    arq = (
        await client.post(
            f"{P}/{p['id']}/arquivo", files={"files": ("rosto.png", PNG, "image/png")}
        )
    ).json()["imagens"][0]

    ver = await client.get(f"{P}/{p['id']}/arquivo/{arq['id']}")
    assert ver.headers["content-disposition"].startswith("inline")

    baixar = await client.get(f"{P}/{p['id']}/arquivo/{arq['id']}?download=1")
    assert baixar.status_code == 200
    assert baixar.headers["content-disposition"].startswith("attachment")
    assert baixar.content == PNG


async def test_ligar_e_desligar_personagem_do_roteiro(client: AsyncClient, admin):
    rot = await _novo(client)
    p = (await client.post(P, json={"nome": "Lívia", "referencia": "@Lívia"})).json()

    liga = await client.post(f"{R}/{rot['id']}/personagem", json={"personagem_id": p["id"]})
    assert liga.status_code == 200, liga.text
    assert [x["nome"] for x in liga.json()["personagens"]] == ["Lívia"]
    assert liga.json()["personagens"][0]["referencia"] == "@Lívia"

    # Clicar duas vezes é clique repetido, não erro.
    de_novo = await client.post(f"{R}/{rot['id']}/personagem", json={"personagem_id": p["id"]})
    assert de_novo.status_code == 200
    assert len(de_novo.json()["personagens"]) == 1

    desliga = await client.delete(f"{R}/{rot['id']}/personagem/{p['id']}")
    assert desliga.json()["personagens"] == []


async def test_apagar_personagem_nao_leva_o_roteiro(client: AsyncClient, admin):
    rot = await _novo(client)
    p = (await client.post(P, json={"nome": "Lívia"})).json()
    await client.post(f"{R}/{rot['id']}/personagem", json={"personagem_id": p["id"]})

    assert (await client.delete(f"{P}/{p['id']}")).status_code == 200
    sobrou = await client.get(R)
    assert len(sobrou.json()) == 1
    assert sobrou.json()[0]["personagens"] == []


# ─────────────── a lápide do campo antigo ───────────────


async def test_criativo_recusa_roteiro_em_vez_de_engolir(client: AsyncClient, admin):
    """`CreativeIn` é BaseModel puro e o default do Pydantic v2 é
    `extra="ignore"` — sem a lápide, um POST antigo mandando `roteiro`
    responderia 200 e jogaria o texto fora em silêncio."""
    r = await client.post(
        "/api/marketing/creatives",
        json={"modelo": "video 30s", "roteiro": "cena 1: abre a mala"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "roteiro_mudou_de_lugar"
    assert r.json()["detail"]["onde"] == "/api/marketing/roteiros"


async def test_criativo_aponta_pro_roteiro(client: AsyncClient, db: AsyncSession, admin):
    rot = await _novo(client)
    criativo = (
        await client.post("/api/marketing/creatives", json={"modelo": "video 30s"})
    ).json()

    r = await client.patch(
        f"/api/marketing/creatives/{criativo['id']}", json={"roteiro_id": rot["id"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["roteiro_id"] == rot["id"]
    assert r.json()["roteiro_titulo"] == "video 30s — mala de bordo"

    # desvincular
    solto = await client.patch(
        f"/api/marketing/creatives/{criativo['id']}", json={"roteiro_id": None}
    )
    assert solto.json()["roteiro_id"] is None


async def test_roteiro_inexistente_da_404_e_nao_500(client: AsyncClient, admin):
    """Sem a conferência, o vínculo errado só aparece como IntegrityError no
    commit, com a mensagem crua da FK na cara do operador."""
    criativo = (
        await client.post("/api/marketing/creatives", json={"modelo": "video 30s"})
    ).json()
    r = await client.patch(
        f"/api/marketing/creatives/{criativo['id']}",
        json={"roteiro_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "roteiro_nao_encontrado"


async def test_apagar_roteiro_nao_apaga_o_criativo(
    client: AsyncClient, db: AsyncSession, admin
):
    """O motivo de a FK ser SET NULL: apagar um briefing não pode levar a
    entrega que a agência já mandou."""
    rot = await _novo(client)
    criativo = (
        await client.post("/api/marketing/creatives", json={"modelo": "video 30s"})
    ).json()
    await client.patch(
        f"/api/marketing/creatives/{criativo['id']}", json={"roteiro_id": rot["id"]}
    )

    assert (await client.delete(f"{R}/{rot['id']}")).status_code == 200
    linhas = (await client.get("/api/marketing/creatives")).json()
    # Afirma sobre ESTA entrega, não sobre a contagem: criar o roteiro agora
    # abre uma linha por agência endereçada, então o total não é mais 1 — e
    # contar escondia que o assert antigo dependia da ordem do SELECT.
    minha = next(x for x in linhas if x["id"] == criativo["id"])
    assert minha["roteiro_id"] is None, "SET NULL: o briefing some, a entrega fica"


# ─────────────── o recado da recusa (continua na ENTREGA) ───────────────


async def test_recusar_com_comentario_carimba_a_data(client: AsyncClient, db: AsyncSession, admin):
    c = MarketingCreative(modelo="video 30s", equipe="alpha")
    db.add(c)
    await db.commit()
    r = await client.post(
        f"/api/marketing/creatives/{c.id}/aprovar",
        json={"aprovado": False, "feedback": "áudio estourado nos 3s finais"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["aprovado"] is False
    assert d["feedback"] == "áudio estourado nos 3s finais"
    assert d["feedback_em"], "sem data a agência não sabe de que versão é o recado"


async def test_recusar_sem_escrever_nada_preserva_o_recado(
    client: AsyncClient, db: AsyncSession, admin
):
    """Campo AUSENTE mantém; string vazia apaga. Um botão que não manda nada
    não pode apagar o que alguém escreveu."""
    c = MarketingCreative(modelo="video 30s", equipe="alpha")
    db.add(c)
    await db.commit()
    await client.patch(f"/api/marketing/creatives/{c.id}", json={"feedback": "refazer a abertura"})
    mantido = await client.post(
        f"/api/marketing/creatives/{c.id}/aprovar", json={"aprovado": False}
    )
    assert mantido.json()["feedback"] == "refazer a abertura"

    apagado = await client.patch(f"/api/marketing/creatives/{c.id}", json={"feedback": "   "})
    assert apagado.json()["feedback"] is None
    assert apagado.json()["feedback_em"] is None


# ─────────────── aprovação de requisição vinda da agência ───────────────
# O caminho externo (portal) propõe; estes testes cobrem o lado de dentro, que é
# onde o personagem realmente nasce.


async def _requisicao(db, *, nome="Joana Enfermeira", status="pendente"):
    from app.models.marketing_personagem_requisicao import MarketingPersonagemRequisicao

    r = MarketingPersonagemRequisicao(
        nome=nome,
        descricao="plantonista, 35 anos",
        origem_imagem="gerada no Higgsfield, prompt e seed guardados",
        origem_voz="banco de vozes licenciado da ferramenta",
        equipe="alpha",
        status=status,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def test_aprovar_cria_o_personagem_e_deixa_o_rastro(client, db, admin):
    req = await _requisicao(db)
    r = await client.post(
        f"/api/marketing/personagens/requisicoes/{req.id}/aprovar"
    )
    assert r.status_code == 200
    pid = r.json()["personagem_id"]

    from app.models import MarketingPersonagem

    p = await db.get(MarketingPersonagem, UUID(pid))
    assert p is not None and p.nome == "Joana Enfermeira"

    await db.refresh(req)
    assert req.status == "aprovada"
    # O rastro é o que permite auditar a procedência do rosto meses depois.
    assert str(req.personagem_id) == pid
    assert req.decidido_em is not None


async def test_aprovar_duas_vezes_da_409(client, db, admin):
    req = await _requisicao(db)
    await client.post(
        f"/api/marketing/personagens/requisicoes/{req.id}/aprovar"
    )
    r = await client.post(
        f"/api/marketing/personagens/requisicoes/{req.id}/aprovar"
    )
    assert r.status_code == 409


async def test_recusar_guarda_o_motivo(client, db, admin):
    req = await _requisicao(db, nome="Outro Nome")
    r = await client.post(
        f"/api/marketing/personagens/requisicoes/{req.id}/recusar",
        json={"motivo": "sem cessão escrita da voz"},
    )
    assert r.status_code == 200
    await db.refresh(req)
    assert req.status == "recusada"
    assert req.motivo == "sem cessão escrita da voz"


async def test_fila_traz_so_as_pendentes(client, db, admin):
    await _requisicao(db, nome="Pendente Uma")
    await _requisicao(db, nome="Já Decidida", status="aprovada")
    r = await client.get("/api/marketing/personagens/requisicoes")
    assert r.status_code == 200
    nomes = [x["nome"] for x in r.json()["requisicoes"]]
    # Afirmar a lista inteira tornaria o teste refém da ordem dos arquivos: outro
    # teste que deixe uma pendente entra na fila e derruba este sem haver defeito.
    assert "Pendente Uma" in nomes
    assert "Já Decidida" not in nomes


async def test_a_versao_da_agencia_chega_marcada_na_tela_de_dentro(
    client: AsyncClient, db: AsyncSession, admin
):
    """`origem_id` tem que sair na resposta da tela de dentro.

    Sem ele a versão que a agência escreveu entra na lista com o mesmo título
    da ideia e vira duplicata sem explicação — e é justamente o par
    ideia/versão que a rota do portal existe para preservar.
    """
    from app.models import MarketingRoteiro

    ideia = (await client.post(R, json={"titulo": "Ideia da casa"})).json()
    versao = (await client.post(R, json={"titulo": "Ideia da casa"})).json()

    linha = await db.get(MarketingRoteiro, UUID(versao["id"]))
    linha.origem_id = UUID(ideia["id"])
    await db.commit()

    lista = (await client.get(R)).json()
    porid = {x["id"]: x for x in lista}
    assert porid[ideia["id"]]["origem_id"] is None
    assert porid[versao["id"]]["origem_id"] == ideia["id"]


# ─────────── o que a auditoria de 22/09/2026 pegou, e não pode voltar ───────────


async def test_fila_de_pedidos_respeita_a_equipe_de_quem_olha(
    client: AsyncClient, db: AsyncSession, make_user, auth_as
):
    """Era o único lugar do módulo sem recorte por equipe — e nele se DECIDE."""
    await _requisicao(db, nome="Pedido da Alpha")
    outra = await _requisicao(db, nome="Pedido da Bravo")
    outra.equipe = "bravo"
    await db.commit()

    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    u.marketing_teams = ["alpha"]
    await db.commit()
    auth_as(u)

    r = await client.get("/api/marketing/personagens/requisicoes")
    assert r.status_code == 200
    nomes = [x["nome"] for x in r.json()["requisicoes"]]
    assert "Pedido da Alpha" in nomes
    assert "Pedido da Bravo" not in nomes

    # Ler é metade: decidir o da outra equipe tem que ser 404, não 200.
    for acao in ("aprovar", "recusar"):
        d = await client.post(
            f"/api/marketing/personagens/requisicoes/{outra.id}/{acao}", json={}
        )
        assert d.status_code == 404, f"{acao} da outra equipe passou"


async def test_apagar_ideia_com_versao_da_agencia_e_barrado(
    client: AsyncClient, db: AsyncSession, admin
):
    """SET NULL apagava o SENTIDO da versão: ela virava ideia da casa, com o
    mesmo título e o texto da agência."""
    from app.models import MarketingRoteiro

    ideia = (await client.post(R, json={"titulo": "Ideia da casa"})).json()
    versao = (await client.post(R, json={"titulo": "Ideia da casa"})).json()
    linha = await db.get(MarketingRoteiro, UUID(versao["id"]))
    linha.origem_id = UUID(ideia["id"])
    await db.commit()

    r = await client.delete(f"{R}/{ideia['id']}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "tem_versoes"

    # A versão sai do caminho e aí a ideia pode ser apagada.
    assert (await client.delete(f"{R}/{versao['id']}")).status_code == 200
    assert (await client.delete(f"{R}/{ideia['id']}")).status_code == 200
