"""Cadastros › E-mails — /api/email-padroes (routers/email_padroes.py).

Eduardo (15/09/2026): "o envio automático de e-mail dos SAC tem que ser
padronizado com logo da marca, assinatura da empresa, site, logo do zap e o
zap" e "em cadastro, uma padronização de e-mails por marca: o padrão pro
SAC, o padrão pro Mercado Livre…". Cobre o CRUD, o grid (linha = marca,
coluna = contexto), o 409 por (marca, contexto, nome) sem caixa — na
pré-checagem e no IntegrityError —, a permissão `email_padroes` (própria,
separada de `marcas`/`redes_sociais`), os 422 do template (bloco `{% %}`,
sintaxe, filtro inexistente, sandbox), a prévia (placeholders, nl2br,
escape do corpo, imagens em data:, avisos, variáveis desconhecidas), o
/render pra robôs (cid:) e o envio de teste com o sender MONKEYPATCHADO —
nunca sai e-mail de verdade daqui; o rate limit também vira no-op porque
o Redis pode não existir no ambiente de teste.

Senhas/e-mails aqui são FAKE; a planilha real fica fora do git.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers.email_padroes as email_router
from app.models import EMAIL_CONTEXTOS, Company, Marca, UserRole
from app.schemas.segments import _slugify
from app.services.email_marca import WHATSAPP_ICON
from app.services.rate_limit import RateLimitError

pytestmark = pytest.mark.asyncio

PNG_FAKE = b"\x89PNG\r\n\x1a\n" + b"logo-fake-" * 8
CNPJ = "12345678000195"
# Valores de exemplo da prévia (os mesmos que a tela manda).
VARS = {
    "cliente": "Maria",
    "pedido": "2000123456789",
    "produto": 'Mala de bordo 20"',
    "plataforma": "Mercado Livre",
}
_CAMPOS_MARCA_REF_EMAIL = {"id", "nome", "slug", "ativo", "sac_email", "has_logo", "updated_at"}
_CAMPOS_PADRAO = {
    "id",
    "marca_id",
    "marca_nome",
    "contexto",
    "nome",
    "remetente_nome",
    "remetente_email",
    "assunto",
    "corpo",
    "incluir_logo",
    "incluir_assinatura",
    "ativo",
    "created_at",
    "updated_at",
}
_CAMPOS_RENDER = {
    "assunto",
    "html",
    "text",
    "from_email",
    "from_name",
    "reply_to",
    "inline_images",
    "avisos",
    "variaveis_desconhecidas",
}
_AVISOS_TODOS = [
    "marca sem empresa vinculada: assinatura sai sem razão social/CNPJ",
    "marca sem Fone/WhatsApp: assinatura sai sem WhatsApp",
    "marca sem e-mail SAC: sem Reply-To e sem e-mail na assinatura",
    "marca sem site",
    "marca sem logo",
]


def _perms(*, view: bool = True, edit: bool = True, delete: bool = True) -> dict:
    return {"email_padroes": {"view": view, "edit": edit, "delete": delete}}


async def _admin(make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    return admin


async def _seed_company(
    db: AsyncSession, razao_social: str = "Poofy Comércio LTDA", cnpj: str | None = None
) -> Company:
    c = Company(razao_social=razao_social, apelido=razao_social.split()[0], cnpj=cnpj)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _seed_marca(
    db: AsyncSession, nome: str, *, completa: bool = False, cnpj: str | None = None, **kw: object
) -> Marca:
    """`completa=True` = marca com tudo que a assinatura usa: empresa (com o
    `cnpj` dado), site, fone/e-mail SAC e logo."""
    if completa:
        empresa = await _seed_company(db, cnpj=cnpj)
        kw = {
            "company_id": empresa.id,
            "site": "https://poofy.com.br",
            "sac_fone": "11983517003",
            "sac_email": "sac@poofy.com",
            "logo_mime": "image/png",
            "logo": PNG_FAKE,
            **kw,
        }
    m = Marca(nome=nome, slug=_slugify(nome), **kw)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


def _body(marca: Marca, **kw: object) -> dict:
    return {
        "marca_id": str(marca.id),
        "contexto": "sac",
        "nome": "Resposta padrão SAC",
        "assunto": "{{ marca }} — atendimento",
        "corpo": (
            "Olá {{ cliente }},\n\nRecebemos sua mensagem sobre o pedido {{ pedido }}."
            "\n\nEquipe {{ marca }}"
        ),
        **kw,
    }


async def _cria(client, marca: Marca, **kw: object) -> dict:
    r = await client.post("/api/email-padroes", json=_body(marca, **kw))
    assert r.status_code == 201, r.text
    return r.json()


def _preview(marca: Marca, **kw: object) -> dict:
    return {
        "marca_id": str(marca.id),
        "assunto": "{{ marca }} — pedido {{ pedido }}",
        "corpo": "Olá {{ cliente }},\n\nseu pedido {{ pedido }} da {{ marca }}.",
        "variaveis": VARS,
        **kw,
    }


class _SenderFake:
    """Sender de mentira: só grava os kwargs de send() (nunca sai e-mail).
    `erro` preenchido = o send() levanta (simula o Mailjet recusando)."""

    name = "fake"

    def __init__(self) -> None:
        self.chamadas: list[dict] = []
        self.erro: Exception | None = None

    async def send(self, **kw) -> None:
        if self.erro is not None:
            raise self.erro
        self.chamadas.append(kw)


@pytest.fixture
def sender_fake(monkeypatch) -> _SenderFake:
    """Troca o sender do app pelo fake e desliga o rate limit (Redis pode
    não existir no ambiente de teste) — os dois pelos NOMES importados no
    router, que é onde o endpoint os resolve."""
    fake = _SenderFake()
    monkeypatch.setattr(email_router, "get_email_sender", lambda: fake)

    async def _sem_limite(**kw) -> int:
        return 1

    monkeypatch.setattr(email_router, "sliding_window_check", _sem_limite)
    return fake


# ================================================================== POST / CRUD


async def test_post_cria_padrao_e_normaliza_campos(client, db, make_user, auth_as):
    """nome com strip; remetente_nome vazio vira null (= nome da marca);
    remetente_email minúsculo; assunto/corpo vão como estão (as quebras são
    do corpo); defaults incluir_logo/incluir_assinatura/ativo = true."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")

    r = await client.post(
        "/api/email-padroes",
        json=_body(
            marca,
            contexto="ml",
            nome="  Resposta ML  ",
            remetente_nome="   ",
            remetente_email="  SAC@Poofy.com ",
            assunto="Pedido {{ pedido }}",
            corpo="Olá {{ cliente }}\n",
            incluir_logo=False,
        ),
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == _CAMPOS_PADRAO
    assert body["marca_id"] == str(marca.id)
    assert body["marca_nome"] == "Poofy"
    assert body["contexto"] == "ml"
    assert body["nome"] == "Resposta ML"
    assert body["remetente_nome"] is None
    assert body["remetente_email"] == "sac@poofy.com"
    assert body["assunto"] == "Pedido {{ pedido }}"
    assert body["corpo"] == "Olá {{ cliente }}\n"
    assert body["incluir_logo"] is False
    assert body["incluir_assinatura"] is True
    assert body["ativo"] is True

    minimo = await _cria(client, marca)
    assert minimo["incluir_logo"] is True
    assert minimo["incluir_assinatura"] is True
    assert minimo["remetente_nome"] is None
    assert minimo["remetente_email"] is None


async def test_post_valida_marca_contexto_nome_e_templates(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")

    ghost = await client.post("/api/email-padroes", json=_body(marca, marca_id=str(uuid.uuid4())))
    assert ghost.status_code == 404, ghost.text
    assert ghost.json()["detail"]["code"] == "marca_not_found"

    for ruim in (
        {"contexto": "orkut"},
        {"nome": "   "},
        {"nome": "x" * 129},
        {"assunto": ""},
        {"assunto": None},
        {"corpo": "  \n "},
        {"corpo": "x" * 10_001},
        {"remetente_email": "sem-arroba"},
    ):
        r = await client.post("/api/email-padroes", json=_body(marca, **ruim))
        assert r.status_code == 422, (ruim, r.text)

    assert (await client.get("/api/email-padroes")).json() == []


async def test_nome_duplicado_no_mesmo_contexto_da_409_sem_caixa(client, db, make_user, auth_as):
    """(marca, contexto, lower(nome)) é único: "resposta padrão sac" e
    "Resposta padrão SAC" são o mesmo padrão. Outro contexto ou outra marca
    pode repetir o nome. PATCH que cai numa chave ocupada também é 409;
    repetir a própria chave não conflita consigo."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")
    await _cria(client, poofy)

    dup = await client.post("/api/email-padroes", json=_body(poofy, nome="  resposta padrão SAC "))
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["code"] == "email_padrao_conflict"

    outro_ctx = await _cria(client, poofy, contexto="ml")
    outra_marca = await _cria(client, locagil)

    ctx = await client.patch(f"/api/email-padroes/{outro_ctx['id']}", json={"contexto": "sac"})
    assert ctx.status_code == 409, ctx.text
    assert ctx.json()["detail"]["code"] == "email_padrao_conflict"

    mv = await client.patch(
        f"/api/email-padroes/{outra_marca['id']}", json={"marca_id": str(poofy.id)}
    )
    assert mv.status_code == 409, mv.text
    assert mv.json()["detail"]["code"] == "email_padrao_conflict"

    proprio = await client.patch(
        f"/api/email-padroes/{outro_ctx['id']}",
        json={"nome": "RESPOSTA padrão SAC", "contexto": "ml"},
    )
    assert proprio.status_code == 200, proprio.text
    assert proprio.json()["nome"] == "RESPOSTA padrão SAC"

    # Nada mudou nos que falharam.
    assert (await client.get(f"/api/email-padroes/{outra_marca['id']}")).json()["marca_id"] == str(
        locagil.id
    )
    assert (await client.get(f"/api/email-padroes/{outro_ctx['id']}")).json()["contexto"] == "ml"


async def test_integrity_error_vira_409(client, db, make_user, auth_as, monkeypatch):
    """Corrida entre dois POSTs: a pré-checagem passa, o índice único
    (lower(nome)) segura e o router traduz o IntegrityError pra 409 — não
    500. Simula tirando a pré-checagem."""

    async def _sem_checagem(session, **kw):
        return None

    monkeypatch.setattr(email_router, "_checa_conflito", _sem_checagem)
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    await _cria(client, poofy)

    dup = await client.post("/api/email-padroes", json=_body(poofy, nome="resposta padrão sac"))
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["code"] == "email_padrao_conflict"

    outro = await _cria(client, poofy, contexto="ml")
    patch = await client.patch(f"/api/email-padroes/{outro['id']}", json={"contexto": "sac"})
    assert patch.status_code == 409, patch.text
    assert patch.json()["detail"]["code"] == "email_padrao_conflict"

    # A API segue de pé (sessão desfeita, nada pela metade).
    lista = await client.get("/api/email-padroes")
    assert lista.status_code == 200
    assert sorted(p["contexto"] for p in lista.json()) == ["ml", "sac"]


async def test_lista_ordena_e_filtra_por_marca_e_contexto(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")
    await _cria(client, poofy, contexto="ml", nome="ML b")
    await _cria(client, poofy, contexto="ml", nome="ML a")
    await _cria(client, poofy, contexto="sac", nome="SAC")
    await _cria(client, locagil, contexto="sac", nome="SAC LG")

    tudo = await client.get("/api/email-padroes")
    assert tudo.status_code == 200, tudo.text
    # Ordem: marca, contexto, nome.
    assert [(p["marca_nome"], p["contexto"], p["nome"]) for p in tudo.json()] == [
        ("Locagil", "sac", "SAC LG"),
        ("Poofy", "ml", "ML a"),
        ("Poofy", "ml", "ML b"),
        ("Poofy", "sac", "SAC"),
    ]

    por_marca = await client.get("/api/email-padroes", params={"marca_id": str(locagil.id)})
    assert [p["nome"] for p in por_marca.json()] == ["SAC LG"]

    por_ctx = await client.get("/api/email-padroes", params={"contexto": " ML "})
    assert [p["nome"] for p in por_ctx.json()] == ["ML a", "ML b"]

    combinado = await client.get(
        "/api/email-padroes", params={"marca_id": str(poofy.id), "contexto": "sac"}
    )
    assert [p["nome"] for p in combinado.json()] == ["SAC"]

    nada = await client.get("/api/email-padroes", params={"contexto": "geral"})
    assert nada.json() == []


async def test_get_detalhe_patch_delete_e_404s(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")
    criado = await _cria(client, poofy)
    url = f"/api/email-padroes/{criado['id']}"

    detalhe = await client.get(url)
    assert detalhe.status_code == 200, detalhe.text
    assert detalhe.json() == criado

    # PATCH parcial: null em coluna NOT NULL = "não mexe" (o modal manda o
    # objeto inteiro); só o que veio com valor muda.
    nulos = await client.patch(
        url,
        json={
            "assunto": None,
            "corpo": None,
            "contexto": None,
            "nome": None,
            "incluir_logo": None,
            "incluir_assinatura": None,
            "ativo": None,
            "marca_id": None,
            "remetente_nome": " SAC Poofy ",
        },
    )
    assert nulos.status_code == 200, nulos.text
    b = nulos.json()
    assert b["remetente_nome"] == "SAC Poofy"
    for k in (
        "assunto",
        "corpo",
        "contexto",
        "nome",
        "incluir_logo",
        "incluir_assinatura",
        "ativo",
        "marca_id",
    ):
        assert b[k] == criado[k], k

    valores = await client.patch(
        url,
        json={
            "ativo": False,
            "incluir_logo": False,
            "assunto": "Novo {{ pedido }}",
            "marca_id": str(locagil.id),
            "remetente_nome": "",
            "remetente_email": " X@Y.com ",
        },
    )
    assert valores.status_code == 200, valores.text
    b = valores.json()
    assert b["ativo"] is False
    assert b["incluir_logo"] is False
    assert b["assunto"] == "Novo {{ pedido }}"
    assert b["marca_id"] == str(locagil.id)
    assert b["marca_nome"] == "Locagil"
    assert b["remetente_nome"] is None
    assert b["remetente_email"] == "x@y.com"

    ghost = uuid.uuid4()
    for metodo, u, kw in (
        ("get", f"/api/email-padroes/{ghost}", {}),
        ("patch", f"/api/email-padroes/{ghost}", {"json": {"nome": "x"}}),
        ("delete", f"/api/email-padroes/{ghost}", {}),
        ("post", f"/api/email-padroes/{ghost}/render", {"json": {}}),
        ("post", f"/api/email-padroes/{ghost}/enviar-teste", {"json": {"para": "t@example.com"}}),
    ):
        r = await getattr(client, metodo)(u, **kw)
        assert r.status_code == 404, (metodo, u, r.text)
        assert r.json()["detail"]["code"] == "email_padrao_not_found"

    marca_ghost = await client.patch(url, json={"marca_id": str(ghost)})
    assert marca_ghost.status_code == 404, marca_ghost.text
    assert marca_ghost.json()["detail"]["code"] == "marca_not_found"
    preview_ghost = await client.post(
        "/api/email-padroes/preview", json={"marca_id": str(ghost), "assunto": "x", "corpo": "y"}
    )
    assert preview_ghost.status_code == 404
    assert preview_ghost.json()["detail"]["code"] == "marca_not_found"

    delete = await client.delete(url)
    assert delete.status_code == 204, delete.text
    assert (await client.get(url)).status_code == 404
    assert (await client.delete(url)).status_code == 404


async def test_apagar_marca_cascateia_nos_padroes(client, db, make_user, auth_as):
    """FK ON DELETE CASCADE (sem relationship, de propósito)."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")
    do_poofy = await _cria(client, poofy)
    do_locagil = await _cria(client, locagil)

    await db.delete(poofy)
    await db.commit()

    assert (await client.get(f"/api/email-padroes/{do_poofy['id']}")).status_code == 404
    lista = await client.get("/api/email-padroes")
    assert [p["id"] for p in lista.json()] == [do_locagil["id"]]
    grid = await client.get("/api/email-padroes/grid")
    assert [row["marca"]["nome"] for row in grid.json()["rows"]] == ["Locagil"]


# ========================================================================= grid


async def test_grid_todas_as_marcas_por_contexto(client, db, make_user, auth_as):
    """Linha = marca (todas, por nome), coluna = contexto (EMAIL_CONTEXTOS na
    ordem do enum), célula = padrões por nome. MarcaRefEmail é enxuta:
    id/nome/slug/ativo/sac_email/has_logo/updated_at — nada de login/e-mail
    do registro, fone/senha das redes nem domínios."""
    await _admin(make_user, auth_as)
    zeta = await _seed_marca(
        db,
        "Zeta",
        usuario="login",
        email="inpi@z.com",
        dominio="z.com",
        sac_fone="11",
        sac_senha_enc="x",
    )
    alpha = await _seed_marca(db, "Alpha", completa=True)
    await _cria(client, alpha, contexto="ml", nome="ML b")
    await _cria(client, alpha, contexto="ml", nome="ML a")
    await _cria(client, alpha, contexto="sac", nome="SAC", ativo=False)

    g = await client.get("/api/email-padroes/grid")

    assert g.status_code == 200, g.text
    body = g.json()
    assert body["contextos"] == list(EMAIL_CONTEXTOS)
    assert body["contextos"] == [
        "sac", "ml", "shopee", "amazon", "aliexpress", "temu", "tiktok", "shein", "magalu",
        "site", "geral",
    ]
    assert [row["marca"]["nome"] for row in body["rows"]] == ["Alpha", "Zeta"]
    for row in body["rows"]:
        assert set(row["marca"]) == _CAMPOS_MARCA_REF_EMAIL
        assert set(row["cells"]) == set(EMAIL_CONTEXTOS)

    alpha_row, zeta_row = body["rows"]
    assert alpha_row["marca"]["id"] == str(alpha.id)
    assert alpha_row["marca"]["has_logo"] is True
    assert alpha_row["marca"]["sac_email"] == "sac@poofy.com"
    assert [p["nome"] for p in alpha_row["cells"]["ml"]] == ["ML a", "ML b"]
    assert [p["nome"] for p in alpha_row["cells"]["sac"]] == ["SAC"]
    assert alpha_row["cells"]["sac"][0]["ativo"] is False
    assert alpha_row["cells"]["sac"][0]["marca_nome"] == "Alpha"
    assert set(alpha_row["cells"]["sac"][0]) == _CAMPOS_PADRAO
    assert alpha_row["cells"]["geral"] == []

    assert zeta_row["marca"]["id"] == str(zeta.id)
    assert zeta_row["marca"]["has_logo"] is False
    assert zeta_row["marca"]["sac_email"] is None
    assert all(v == [] for v in zeta_row["cells"].values())


# =================================================================== permissões


async def test_sem_permissao_403(client, db, make_user, auth_as):
    """`email_padroes` é recurso próprio: ter `marcas` e `redes_sociais` não
    libera esta aba — em nenhum endpoint (a permissão vem antes do 404)."""
    marca = await _seed_marca(db, "Poofy")
    tudo = {"view": True, "edit": True, "delete": True}
    user = await make_user(permissions={"marcas": tudo, "redes_sociais": tudo})
    auth_as(user)
    ghost = uuid.uuid4()

    for metodo, u, kw in (
        ("get", "/api/email-padroes", {}),
        ("get", "/api/email-padroes/grid", {}),
        ("get", f"/api/email-padroes/{ghost}", {}),
        ("post", "/api/email-padroes/preview", {"json": _preview(marca)}),
        ("post", "/api/email-padroes", {"json": _body(marca)}),
        ("patch", f"/api/email-padroes/{ghost}", {"json": {"nome": "x"}}),
        ("delete", f"/api/email-padroes/{ghost}", {}),
        ("post", f"/api/email-padroes/{ghost}/render", {"json": {}}),
        ("post", f"/api/email-padroes/{ghost}/enviar-teste", {"json": {"para": "t@example.com"}}),
    ):
        r = await getattr(client, metodo)(u, **kw)
        assert r.status_code == 403, (metodo, u, r.text)
        assert r.json()["detail"]["code"] == "forbidden"
        assert r.json()["detail"]["resource"] == "email_padroes"


async def test_so_view_le_e_preve_mas_nao_escreve(client, db, make_user, auth_as, sender_fake):
    """view: lista/grid/detalhe/preview/render 200; POST/PATCH/enviar-teste
    403 (action edit); DELETE 403. edit sem delete: escreve e manda teste,
    não apaga."""
    admin = await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")
    criado = await _cria(client, marca)
    url = f"/api/email-padroes/{criado['id']}"

    viewer = await make_user(permissions=_perms(view=True, edit=False, delete=False))
    auth_as(viewer)

    assert (await client.get("/api/email-padroes")).status_code == 200
    assert (await client.get("/api/email-padroes/grid")).status_code == 200
    assert (await client.get(url)).status_code == 200
    preview = await client.post("/api/email-padroes/preview", json=_preview(marca))
    assert preview.status_code == 200, preview.text
    render = await client.post(f"{url}/render", json={"variaveis": VARS})
    assert render.status_code == 200, render.text

    post = await client.post("/api/email-padroes", json=_body(marca, contexto="ml"))
    assert post.status_code == 403
    assert post.json()["detail"]["action"] == "edit"
    patch = await client.patch(url, json={"nome": "x"})
    assert patch.status_code == 403
    assert patch.json()["detail"]["action"] == "edit"
    teste = await client.post(f"{url}/enviar-teste", json={"para": "t@example.com"})
    assert teste.status_code == 403
    assert teste.json()["detail"]["action"] == "edit"
    assert sender_fake.chamadas == []
    delete = await client.delete(url)
    assert delete.status_code == 403
    assert delete.json()["detail"]["action"] == "delete"

    editor = await make_user(permissions=_perms(view=True, edit=True, delete=False))
    auth_as(editor)
    patch_ok = await client.patch(url, json={"nome": "Editado"})
    assert patch_ok.status_code == 200, patch_ok.text
    teste_ok = await client.post(f"{url}/enviar-teste", json={"para": "t@example.com"})
    assert teste_ok.status_code == 200, teste_ok.text
    assert len(sender_fake.chamadas) == 1
    delete = await client.delete(url)
    assert delete.status_code == 403
    assert delete.json()["detail"]["action"] == "delete"

    auth_as(admin)
    assert (await client.get(url)).json()["nome"] == "Editado"
    assert (await client.delete(url)).status_code == 204


# ==================================================================== template


async def test_template_com_bloco_da_422_no_post_patch_e_preview(client, db, make_user, auth_as):
    """Só expressões `{{ }}`: bloco `{% %}` (for/if/macro) é barrado já no
    schema — em POST, PATCH e preview — e nunca chega no Jinja."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")
    bloco = "{% for i in range(3) %}x{% endfor %}"

    post = await client.post("/api/email-padroes", json=_body(marca, corpo=bloco))
    assert post.status_code == 422, post.text
    assert "template_bloco_nao_permitido" in post.text

    criado = await _cria(client, marca)
    patch = await client.patch(f"/api/email-padroes/{criado['id']}", json={"assunto": bloco})
    assert patch.status_code == 422, patch.text
    assert "template_bloco_nao_permitido" in patch.text
    assert (await client.get(f"/api/email-padroes/{criado['id']}")).json()["assunto"] == criado[
        "assunto"
    ]

    preview = await client.post("/api/email-padroes/preview", json=_preview(marca, corpo=bloco))
    assert preview.status_code == 422, preview.text
    assert "template_bloco_nao_permitido" in preview.text


async def test_template_que_nao_compila_da_422_template_invalido(client, db, make_user, auth_as):
    """Sintaxe quebrada, filtro fora da lista ou acesso perigoso: a prévia
    responde 422 com o código da whitelist ({code, erro}); e o POST/PATCH
    também barram (o schema chama a mesma validação) — padrão inválido nunca
    é SALVO, então o /render dos robôs não quebra depois."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")

    for corpo, code, trecho in (
        ("Olá {{ marca", "template_invalido", "unexpected end of template"),
        ("{{ marca | nao_existe }}", "filtro_nao_permitido", "nao_existe"),
        ("{{ ''.__class__.__mro__ }}", "template_bloco_nao_permitido", "Getattr"),
        ("{{ 'x' * 10**9 }}", "template_bloco_nao_permitido", "Mul"),
    ):
        # A validação roda no schema (pydantic → detail em lista) — o código
        # e o trecho vêm na mensagem.
        r = await client.post("/api/email-padroes/preview", json=_preview(marca, corpo=corpo))
        assert r.status_code == 422, (corpo, r.text)
        assert code in r.text and trecho in r.text

    salvar = await client.post(
        "/api/email-padroes",
        json={
            "marca_id": str(marca.id), "contexto": "sac", "nome": "Ruim",
            "assunto": "x", "corpo": "{{ marca | nao_existe }}",
        },
    )
    assert salvar.status_code == 422, salvar.text
    assert "filtro_nao_permitido" in salvar.text
    lista = await client.get("/api/email-padroes", params={"marca_id": str(marca.id)})
    assert lista.json() == []


async def test_sandbox_nunca_executa_atributo_no_preview_nem_no_render(
    client, db, make_user, auth_as
):
    """`{{ marca.__class__ }}` é barrado pela whitelist na prévia (422) e no
    POST (422 do schema) — o nome da classe nunca chega perto de um e-mail."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")

    previa = await client.post(
        "/api/email-padroes/preview",
        json=_preview(marca, corpo="x{{ marca.__class__ }}y", incluir_assinatura=False),
    )
    assert previa.status_code == 422, previa.text
    assert "template_bloco_nao_permitido" in previa.text

    salvar = await client.post(
        "/api/email-padroes",
        json={
            "marca_id": str(marca.id), "contexto": "sac", "nome": "Perigoso",
            "assunto": "x", "corpo": "x{{ marca.__class__ }}y",
        },
    )
    assert salvar.status_code == 422, salvar.text
    assert "template_bloco_nao_permitido" in salvar.text


async def test_placeholder_desconhecido_da_422_com_o_nome(client, db, make_user, auth_as):
    """`{{ marcaa }}` (erro de digitação): prévia e POST respondem 422
    placeholder_desconhecido com o nome — o operador corrige antes de salvar.
    Placeholders conhecidos passam e `variaveis_desconhecidas` fica vazio."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")

    previa = await client.post(
        "/api/email-padroes/preview",
        json=_preview(marca, assunto="{{ assuntoo }}", corpo="Olá {{ marcaa }}!"),
    )
    assert previa.status_code == 422, previa.text
    assert "placeholder_desconhecido" in previa.text and "assuntoo" in previa.text

    ok = await client.post("/api/email-padroes/preview", json=_preview(marca))
    assert ok.status_code == 200, ok.text
    assert ok.json()["variaveis_desconhecidas"] == []

    salvar = await client.post(
        "/api/email-padroes",
        json={
            "marca_id": str(marca.id), "contexto": "sac", "nome": "Typo",
            "assunto": "x", "corpo": "Olá {{ marcaa }}!",
        },
    )
    assert salvar.status_code == 422, salvar.text
    assert "placeholder_desconhecido" in salvar.text


async def test_preview_renderiza_placeholders_logo_em_data_e_assinatura(
    client, db, make_user, auth_as
):
    """Prévia = HTML pronto pro <iframe sandbox srcdoc>: placeholders da
    marca e do contexto substituídos, logo e ícone do Zap como data: (nada
    de cid:, que o iframe não resolve), assinatura completa, texto puro,
    From/Reply-To e sem avisos."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy", completa=True, cnpj=CNPJ)

    r = await client.post("/api/email-padroes/preview", json=_preview(marca))

    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == _CAMPOS_RENDER
    assert b["assunto"] == "Poofy — pedido 2000123456789"
    html = b["html"]
    assert "Olá Maria,<br>\n<br>\nseu pedido 2000123456789 da Poofy." in html
    assert "cid:" not in html
    assert html.count("data:image/png;base64,") == 2
    assert "Poofy Comércio LTDA &middot; CNPJ 12.345.678/0001-95" in html
    assert 'href="https://poofy.com.br"' in html
    assert 'href="https://wa.me/5511983517003"' in html
    assert "WhatsApp (11) 98351-7003" in html
    assert 'href="mailto:sac@poofy.com"' in html
    assert b["text"].startswith("Olá Maria,\n\nseu pedido 2000123456789 da Poofy.\n\n--\nPoofy\n")
    assert "<" not in b["text"]
    assert b["inline_images"] == ["logo", "whatsapp"]
    assert b["from_email"] is None
    assert b["from_name"] == "Poofy"
    assert b["reply_to"] == "sac@poofy.com"
    assert b["avisos"] == []
    assert b["variaveis_desconhecidas"] == []

    # Remetente explícito; sem logo e sem assinatura.
    r2 = await client.post(
        "/api/email-padroes/preview",
        json=_preview(
            marca,
            remetente_nome=" Equipe Poofy ",
            remetente_email=" SAC@Poofy.com ",
            incluir_logo=False,
            incluir_assinatura=False,
        ),
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["from_name"] == "Equipe Poofy"
    assert b2["from_email"] == "sac@poofy.com"
    assert b2["reply_to"] == "sac@poofy.com"
    assert b2["inline_images"] == []
    assert "data:" not in b2["html"]
    assert "wa.me" not in b2["html"]
    assert "--" not in b2["text"]


async def test_preview_escapa_html_do_corpo_e_apara_assunto(client, db, make_user, auth_as):
    """O corpo é texto: `<b>` chega escapado no HTML e literal no texto puro;
    quebra vira <br>; o `&` do nome da marca não é escapado duas vezes;
    assunto sai sem CR/LF/espaços nas pontas."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy & Cia")

    r = await client.post(
        "/api/email-padroes/preview",
        json=_preview(
            marca,
            assunto="  {{ marca }} — atendimento \r\n",
            corpo="<b>negrito</b> & tal\r\nlinha 2\n{{ marca }} agradece",
            incluir_assinatura=False,
        ),
    )

    assert r.status_code == 200, r.text
    b = r.json()
    assert b["assunto"] == "Poofy & Cia — atendimento"
    assert "<title>Poofy &amp; Cia — atendimento</title>" in b["html"]
    assert (
        "&lt;b&gt;negrito&lt;/b&gt; &amp; tal<br>\nlinha 2<br>\nPoofy &amp; Cia agradece"
        in b["html"]
    )
    assert "<b>negrito</b>" not in b["html"]
    assert "&amp;amp;" not in b["html"]
    # O Jinja normaliza CRLF pra "\n" (o textarea manda CRLF).
    assert b["text"] == "<b>negrito</b> & tal\nlinha 2\nPoofy & Cia agradece"


async def test_preview_avisos_quando_falta_dado_na_marca(client, db, make_user, auth_as):
    """`avisos` diz o que falta na marca pra assinatura sair completa
    (empresa, fone, e-mail SAC, site, logo); some conforme preenche; sem
    logo nem assinatura não tem aviso nenhum."""
    await _admin(make_user, auth_as)
    seca = await _seed_marca(db, "Seca")

    r = await client.post("/api/email-padroes/preview", json=_preview(seca))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["avisos"] == _AVISOS_TODOS
    assert b["inline_images"] == []
    assert b["reply_to"] is None
    assert "data:" not in b["html"]
    assert "cid:" not in b["html"]
    assert "wa.me" not in b["html"]
    assert "mailto:" not in b["html"]
    assert "CNPJ" not in b["html"]

    meio = await _seed_marca(db, "Meio", sac_fone="11983517003", sac_email="sac@meio.com")
    r2 = await client.post("/api/email-padroes/preview", json=_preview(meio))
    assert r2.status_code == 200, r2.text
    assert r2.json()["avisos"] == [_AVISOS_TODOS[0], _AVISOS_TODOS[3], _AVISOS_TODOS[4]]
    assert r2.json()["inline_images"] == ["whatsapp"]
    assert r2.json()["reply_to"] == "sac@meio.com"
    assert "wa.me/5511983517003" in r2.json()["html"]

    r3 = await client.post(
        "/api/email-padroes/preview",
        json=_preview(seca, incluir_logo=False, incluir_assinatura=False),
    )
    assert r3.status_code == 200
    assert r3.json()["avisos"] == []


# ======================================================================= render


async def test_render_devolve_cid_e_inline_images_para_robos(client, db, make_user, auth_as):
    """POST /{id}/render: HTML com `cid:logo`/`cid:whatsapp` (é o que vai pro
    Mailjet como InlinedAttachments) + a lista dos content-ids, com as
    variáveis do contexto; sem DebugUndefined (é envio, não prévia)."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy", completa=True, cnpj=CNPJ)
    padrao = await _cria(client, marca, remetente_nome="SAC Poofy")
    url = f"/api/email-padroes/{padrao['id']}/render"

    r = await client.post(url, json={"variaveis": VARS})

    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == _CAMPOS_RENDER
    assert b["assunto"] == "Poofy — atendimento"
    assert 'src="cid:logo"' in b["html"]
    assert 'src="cid:whatsapp"' in b["html"]
    assert "data:" not in b["html"]
    assert b["inline_images"] == ["logo", "whatsapp"]
    assert "Recebemos sua mensagem sobre o pedido 2000123456789." in b["html"]
    assert "Olá Maria," in b["text"]
    assert "CNPJ 12.345.678/0001-95" in b["text"]
    assert b["from_name"] == "SAC Poofy"
    assert b["from_email"] is None
    assert b["reply_to"] == "sac@poofy.com"
    assert b["avisos"] == []
    assert b["variaveis_desconhecidas"] == []

    # Sem variáveis: placeholders de contexto viram "" (não 422).
    vazio = await client.post(url, json={})
    assert vazio.status_code == 200, vazio.text
    assert "Olá ," in vazio.json()["text"]

    # incluir_logo=False → só o Zap; marca sem fone → só o logo.
    sem_logo = await _cria(client, marca, contexto="ml", incluir_logo=False)
    r2 = await client.post(f"/api/email-padroes/{sem_logo['id']}/render", json={})
    assert r2.json()["inline_images"] == ["whatsapp"]
    assert "cid:logo" not in r2.json()["html"]

    locagil = await _seed_marca(db, "Locagil", logo_mime="image/png", logo=PNG_FAKE)
    so_logo = await _cria(client, locagil)
    r3 = await client.post(f"/api/email-padroes/{so_logo['id']}/render", json={})
    assert r3.json()["inline_images"] == ["logo"]
    assert "cid:whatsapp" not in r3.json()["html"]
    assert "wa.me" not in r3.json()["html"]


# ================================================================ enviar-teste


async def test_enviar_teste_envia_pelo_sender_com_prefixo_reply_to_e_imagens(
    client, db, make_user, auth_as, sender_fake
):
    """POST /{id}/enviar-teste manda o padrão renderizado pelo sender do app
    (aqui o fake): assunto com "[TESTE] ", From = EMAIL_FROM com o nome da
    marca (from_email None), Reply-To = SAC da marca, imagens inline logo +
    whatsapp (cid:) e a resposta diz qual sender foi."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy", completa=True, cnpj=CNPJ)
    padrao = await _cria(client, marca)

    r = await client.post(
        f"/api/email-padroes/{padrao['id']}/enviar-teste",
        json={"para": " Conceicao.Teste@Example.com ", "variaveis": VARS},
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "sender": "fake", "para": "conceicao.teste@example.com"}
    assert len(sender_fake.chamadas) == 1
    kw = sender_fake.chamadas[0]
    assert kw["to"] == "conceicao.teste@example.com"
    assert kw["subject"] == "[TESTE] Poofy — atendimento"
    assert kw["from_email"] is None
    assert kw["from_name"] == "Poofy"
    assert kw["reply_to"] == ("sac@poofy.com", "Poofy")
    assert set(kw["inline_images"]) == {"logo", "whatsapp"}
    assert kw["inline_images"]["logo"] == ("image/png", PNG_FAKE)
    assert kw["inline_images"]["whatsapp"] == ("image/png", WHATSAPP_ICON.read_bytes())
    assert 'src="cid:logo"' in kw["html"]
    assert 'src="cid:whatsapp"' in kw["html"]
    assert "data:" not in kw["html"]
    assert "Olá Maria," in kw["html"]
    assert "Olá Maria," in kw["text"]
    # O prefixo é só do assunto.
    assert "[TESTE]" not in kw["html"]
    assert "[TESTE]" not in kw["text"]

    # remetente_email preenchido → From explícito (precisa estar validado no
    # Mailjet); remetente_nome substitui o nome da marca.
    com_from = await _cria(
        client, marca, contexto="ml", remetente_email="sac@poofy.com", remetente_nome="Equipe Poofy"
    )
    r2 = await client.post(
        f"/api/email-padroes/{com_from['id']}/enviar-teste", json={"para": "t@example.com"}
    )
    assert r2.status_code == 200, r2.text
    kw2 = sender_fake.chamadas[1]
    assert kw2["from_email"] == "sac@poofy.com"
    assert kw2["from_name"] == "Equipe Poofy"
    assert kw2["reply_to"] == ("sac@poofy.com", "Poofy")


async def test_enviar_teste_imagens_so_do_que_a_marca_tem(
    client, db, make_user, auth_as, sender_fake
):
    """'logo' só com has_logo, 'whatsapp' só com sac_fone; sem nenhum dos
    dois o sender recebe inline_images=None. Sem sac_email, sem Reply-To."""
    await _admin(make_user, auth_as)
    casos = (
        ("Nada", {}, None),
        ("So fone", {"sac_fone": "11983517003"}, {"whatsapp"}),
        ("So logo", {"logo_mime": "image/png", "logo": PNG_FAKE}, {"logo"}),
        (
            "Ambos",
            {"sac_fone": "11983517003", "logo_mime": "image/png", "logo": PNG_FAKE},
            {"logo", "whatsapp"},
        ),
    )
    for nome, kw, esperado in casos:
        marca = await _seed_marca(db, nome, **kw)
        padrao = await _cria(client, marca)
        r = await client.post(
            f"/api/email-padroes/{padrao['id']}/enviar-teste", json={"para": "t@example.com"}
        )
        assert r.status_code == 200, (nome, r.text)
        chamada = sender_fake.chamadas[-1]
        if esperado is None:
            assert chamada["inline_images"] is None, nome
        else:
            assert set(chamada["inline_images"]) == esperado, nome
        assert chamada["reply_to"] is None, nome
        assert chamada["subject"] == f"[TESTE] {nome} — atendimento"
    assert len(sender_fake.chamadas) == len(casos)


async def test_enviar_teste_502_quando_o_sender_falha(client, db, make_user, auth_as, sender_fake):
    """Mailjet recusou (HTTPStatusError → status + texto curto) ou qualquer
    outra falha do sender → 502 email_envio_falhou com o motivo, nunca 500."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")
    padrao = await _cria(client, marca)
    url = f"/api/email-padroes/{padrao['id']}/enviar-teste"

    req = httpx.Request("POST", "https://api.mailjet.com/v3.1/send")
    resp = httpx.Response(400, text='{"ErrorMessage": "sender not validated"}', request=req)
    sender_fake.erro = httpx.HTTPStatusError("400", request=req, response=resp)
    r = await client.post(url, json={"para": "t@example.com"})
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "email_envio_falhou"
    assert r.json()["detail"]["status"] == 400
    assert "sender not validated" in r.json()["detail"]["erro"]

    sender_fake.erro = RuntimeError("boom")
    r2 = await client.post(url, json={"para": "t@example.com"})
    assert r2.status_code == 502, r2.text
    assert r2.json()["detail"] == {"code": "email_envio_falhou", "erro": "boom"}
    assert sender_fake.chamadas == []


async def test_enviar_teste_429_quando_estoura_o_rate_limit(
    client, db, make_user, auth_as, sender_fake, monkeypatch
):
    """5 testes por usuário a cada 10 min (mesmo helper do OTP): estourou →
    429 rate_limited e o sender nem é chamado."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")
    padrao = await _cria(client, marca)
    chaves: list[tuple] = []

    async def _estourou(*, key: str, limit: int, window_seconds: int) -> int:
        chaves.append((key, limit, window_seconds))
        raise RateLimitError(retry_after=60)

    monkeypatch.setattr(email_router, "sliding_window_check", _estourou)

    r = await client.post(
        f"/api/email-padroes/{padrao['id']}/enviar-teste", json={"para": "t@example.com"}
    )

    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "rate_limited"
    assert sender_fake.chamadas == []
    assert len(chaves) == 1
    assert chaves[0][0].startswith("email_teste:")
    assert chaves[0][1:] == (5, 600)


async def test_enviar_teste_em_prod_so_para_o_proprio_email(
    client, db, make_user, auth_as, sender_fake, monkeypatch
):
    """Em produção, usuário comum só manda o teste pro PRÓPRIO e-mail (não
    usar a conta Mailjet da empresa pra disparar e-mail com a marca pra
    endereço alheio); admin manda pra qualquer um. Fora de prod, livre."""
    monkeypatch.setattr(email_router, "get_settings", lambda: SimpleNamespace(is_prod=True))
    admin = await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")
    padrao = await _cria(client, marca)
    url = f"/api/email-padroes/{padrao['id']}/enviar-teste"

    editor = await make_user(email="editor@davinci-test.com", permissions=_perms(delete=False))
    auth_as(editor)
    alheio = await client.post(url, json={"para": "outra@example.com"})
    assert alheio.status_code == 403, alheio.text
    assert alheio.json()["detail"]["code"] == "teste_so_para_proprio_email"
    assert sender_fake.chamadas == []

    proprio = await client.post(url, json={"para": " Editor@davinci-test.com "})
    assert proprio.status_code == 200, proprio.text
    assert sender_fake.chamadas[-1]["to"] == "editor@davinci-test.com"

    auth_as(admin)
    assert (await client.post(url, json={"para": "outra@example.com"})).status_code == 200

    monkeypatch.setattr(email_router, "get_settings", lambda: SimpleNamespace(is_prod=False))
    auth_as(editor)
    assert (await client.post(url, json={"para": "outra@example.com"})).status_code == 200
    assert len(sender_fake.chamadas) == 3


async def test_enviar_teste_valida_o_email_e_as_variaveis(
    client, db, make_user, auth_as, sender_fake
):
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")
    padrao = await _cria(client, marca)
    url = f"/api/email-padroes/{padrao['id']}/enviar-teste"

    for para in ("", "   ", "sem-arroba", "@x.com", "a b@x.com", None):
        r = await client.post(url, json={"para": para})
        assert r.status_code == 422, (para, r.text)
    # `variaveis` é dict de texto (nada de número/objeto no sandbox).
    r = await client.post(url, json={"para": "t@example.com", "variaveis": {"pedido": 123}})
    assert r.status_code == 422, r.text
    assert sender_fake.chamadas == []
