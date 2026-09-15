"""Cadastros › Marcas — CRUD de /api/marcas (routers/marcas.py).

Pedido do Eduardo (15/09/2026): aba Marcas no grupo Cadastros, vinda da
planilha `redes sociais.xlsx`. Aqui cobre o router: permissões por recurso
`marcas`, slug derivado do nome (chave estável pras outras telas), filtros
da listagem, semântica do PATCH (senha ausente/""/texto, slug null = não
mexe, normalização de texto/e-mail/data), a senha que NUNCA sai em
listagem (só no GET /{id}/senha, sem cache), o 409 via IntegrityError (a
corrida que passa pela pré-checagem), a cascata nas redes sociais e os 404.

v3 (15/09/2026, tarde): empresa da assinatura (`company_id` +
`empresa_razao_social`, GET /empresas), `site` validado, logo em bytes no
banco (PUT/GET/DELETE /{id}/logo, magic bytes, 1 MB, nosniff, liberado pra
quem vê qualquer das três abas), e o que esta aba NÃO edita: fone/usuário/
senha das redes e a verificação do Zap são da aba Redes Sociais
(PATCH /api/redes-sociais/marca/{id}) — aqui só saem como leitura.

Toda senha aqui é FAKE — a planilha real fica fora do git e do teste.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, Marca, MarcaEmailPadrao, RedeSocial, UserRole
from app.routers.marcas import LOGO_MAX_BYTES, LOGO_MAX_GUARDADO, LOGO_MAX_LARGURA
from app.security.cipher import encrypt

pytestmark = pytest.mark.asyncio

SENHA_FAKE = "s3nh4-teste"


def _perms(*, view: bool = True, edit: bool = False, delete: bool = False) -> dict:
    return {"marcas": {"view": view, "edit": edit, "delete": delete}}


async def _admin(make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    return admin


async def _cria(client, nome: str, **extra: object) -> dict:
    r = await client.post("/api/marcas", json={"nome": nome, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _sem_senha(body: dict) -> None:
    """Nenhuma resposta (lista/detalhe/create/patch) pode trazer senha
    nenhuma — nem a do registro nem a das redes/SAC (v3), nem o logo."""
    assert "senha" not in body
    assert "senha_enc" not in body
    assert "sac_senha" not in body
    assert "sac_senha_enc" not in body
    assert "logo" not in body


def _revelada(body: dict) -> str:
    """GET /{id}/senha devolve SenhaOut {senha, origem} (v3). A senha do
    REGISTRO (INPI) não é herdada de ninguém, então `origem` é sempre null —
    o campo existe por causa da senha das redes, que pode vir da marca."""
    assert set(body) == {"senha", "origem"}
    assert body["origem"] is None
    return body["senha"]


# ================================================================ POST


async def test_admin_cria_marca_com_todos_os_campos(client, make_user, auth_as):
    """Slug vem de _slugify(nome); senha vira só `has_senha`; a resposta não
    tem chave de senha."""
    await _admin(make_user, auth_as)

    body = await _cria(
        client,
        "Charlots Park",
        inpi_status="aguardando",
        usuario="charlots",
        senha=SENHA_FAKE,
        email="Contato@Charlots.com",
        dominio_br="charlotspark.com.br",
        dominio="charlotspark.com",
        dono_dominio="omar",
        dominio_validade="2034-07-04",
        classe="celular",
        funcao="locação celular",
        tipo="cel",
        obs="teste",
        ativo=True,
    )

    assert body["nome"] == "Charlots Park"
    assert body["slug"] == "charlots-park"
    assert body["inpi_status"] == "aguardando"
    assert body["usuario"] == "charlots"
    assert body["has_senha"] is True
    assert body["email"] == "contato@charlots.com"
    assert body["dominio_br"] == "charlotspark.com.br"
    assert body["dominio"] == "charlotspark.com"
    assert body["dono_dominio"] == "omar"
    assert body["dominio_validade"] == "2034-07-04"
    assert body["classe"] == "celular"
    assert body["funcao"] == "locação celular"
    assert body["tipo"] == "cel"
    assert body["obs"] == "teste"
    assert body["ativo"] is True
    _sem_senha(body)

    # Defaults quando o body só traz o nome.
    minima = await _cria(client, "Poofy")
    assert minima["slug"] == "poofy"
    assert minima["inpi_status"] == "nao_registrado"
    assert minima["has_senha"] is False
    assert minima["ativo"] is True
    _sem_senha(minima)


async def test_nome_duplicado_com_caixa_diferente_da_409(client, make_user, auth_as):
    """"charlots park" e "Charlots Park" viram o mesmo slug → 409."""
    await _admin(make_user, auth_as)
    await _cria(client, "Charlots Park")

    r = await client.post("/api/marcas", json={"nome": "charlots park"})

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "marca_slug_conflict"


async def test_create_normaliza_texto_email_e_slug_explicito(client, make_user, auth_as):
    await _admin(make_user, auth_as)

    body = await _cria(
        client,
        "  Locagil  ",
        slug="Loca Gil",
        usuario="  admin  ",
        email="  SAC@Locagil.COM ",
        classe="",
        obs="   ",
    )

    assert body["nome"] == "Locagil"
    assert body["slug"] == "loca-gil"
    assert body["usuario"] == "admin"
    assert body["email"] == "sac@locagil.com"
    assert body["classe"] is None
    assert body["obs"] is None


async def test_create_nome_vazio_ou_inpi_invalido_da_422(client, make_user, auth_as):
    await _admin(make_user, auth_as)

    vazio = await client.post("/api/marcas", json={"nome": "   "})
    assert vazio.status_code == 422
    inpi = await client.post("/api/marcas", json={"nome": "Poofy", "inpi_status": "ok"})
    assert inpi.status_code == 422


# ========================================================== Permissões


async def test_usuario_sem_permissao_nao_lista(client, make_user, auth_as):
    user = await make_user(permissions={})
    auth_as(user)

    r = await client.get("/api/marcas")

    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "forbidden"
    assert r.json()["detail"]["resource"] == "marcas"


async def test_usuario_so_view_le_mas_nao_escreve_nem_revela_senha(
    client, make_user, auth_as
):
    """view: lista e detalhe 200; POST/PATCH/DELETE e GET /senha 403 (a senha
    é atrás de edit, como no nf_faturador)."""
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy", senha=SENHA_FAKE)

    viewer = await make_user(permissions=_perms(view=True))
    auth_as(viewer)

    lista = await client.get("/api/marcas")
    assert lista.status_code == 200
    assert [m["id"] for m in lista.json()] == [marca["id"]]
    for m in lista.json():
        _sem_senha(m)

    detalhe = await client.get(f"/api/marcas/{marca['id']}")
    assert detalhe.status_code == 200
    assert detalhe.json()["has_senha"] is True
    _sem_senha(detalhe.json())

    post = await client.post("/api/marcas", json={"nome": "Locagil"})
    assert post.status_code == 403
    patch = await client.patch(f"/api/marcas/{marca['id']}", json={"obs": "x"})
    assert patch.status_code == 403
    delete = await client.delete(f"/api/marcas/{marca['id']}")
    assert delete.status_code == 403
    senha = await client.get(f"/api/marcas/{marca['id']}/senha")
    assert senha.status_code == 403
    assert senha.json()["detail"]["action"] == "edit"


async def test_usuario_com_edit_sem_delete_nao_apaga(client, make_user, auth_as):
    editor = await make_user(permissions=_perms(view=True, edit=True, delete=False))
    auth_as(editor)
    marca = await _cria(client, "Poofy")

    patch = await client.patch(f"/api/marcas/{marca['id']}", json={"obs": "ok"})
    assert patch.status_code == 200
    delete = await client.delete(f"/api/marcas/{marca['id']}")
    assert delete.status_code == 403
    assert delete.json()["detail"]["action"] == "delete"


# ============================================================ Listagem


async def test_lista_ordena_por_nome_e_filtra_por_search_e_ativo(
    client, make_user, auth_as
):
    """`search` casa nome/domínio/classe (ilike); `ativo` filtra; ordem por
    nome."""
    await _admin(make_user, auth_as)
    poofy = await _cria(client, "Poofy", dominio="poofy.com", classe="celular")
    locagil = await _cria(
        client, "Locagil", dominio_br="locagil.com.br", classe="Locação", ativo=False
    )
    charlots = await _cria(client, "Charlots Park", classe="eletro")

    todos = await client.get("/api/marcas")
    assert todos.status_code == 200
    assert [m["nome"] for m in todos.json()] == ["Charlots Park", "Locagil", "Poofy"]
    for m in todos.json():
        _sem_senha(m)

    por_nome = await client.get("/api/marcas", params={"search": "charlots"})
    assert [m["id"] for m in por_nome.json()] == [charlots["id"]]

    por_dominio = await client.get("/api/marcas", params={"search": "POOFY.COM"})
    assert [m["id"] for m in por_dominio.json()] == [poofy["id"]]

    por_dominio_br = await client.get("/api/marcas", params={"search": "locagil.com.br"})
    assert [m["id"] for m in por_dominio_br.json()] == [locagil["id"]]

    por_classe = await client.get("/api/marcas", params={"search": "eletro"})
    assert [m["id"] for m in por_classe.json()] == [charlots["id"]]

    ativas = await client.get("/api/marcas", params={"ativo": "true"})
    assert [m["nome"] for m in ativas.json()] == ["Charlots Park", "Poofy"]

    inativas = await client.get("/api/marcas", params={"ativo": "false"})
    assert [m["id"] for m in inativas.json()] == [locagil["id"]]

    nada = await client.get("/api/marcas", params={"search": "zzz-nao-existe"})
    assert nada.json() == []


# =============================================================== PATCH


async def test_patch_obs_preserva_senha_nome_e_slug(client, make_user, auth_as):
    """Regressão do padrão companies: PATCH parcial não pode zerar o que não
    veio no body (exclude_unset + só field_validator)."""
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Charlots Park", senha=SENHA_FAKE, usuario="charlots")

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"obs": "só a obs"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["obs"] == "só a obs"
    assert body["has_senha"] is True
    assert body["nome"] == "Charlots Park"
    assert body["slug"] == "charlots-park"
    assert body["usuario"] == "charlots"
    _sem_senha(body)

    senha = await client.get(f"/api/marcas/{marca['id']}/senha")
    assert senha.status_code == 200
    assert _revelada(senha.json()) == SENHA_FAKE


async def test_patch_senha_vazia_limpa_a_senha(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy", senha=SENHA_FAKE)

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"senha": ""})

    assert r.status_code == 200, r.text
    assert r.json()["has_senha"] is False
    _sem_senha(r.json())
    senha = await client.get(f"/api/marcas/{marca['id']}/senha")
    assert _revelada(senha.json()) == ""

    # null também limpa (botão "limpar senha" do modal manda senha:null).
    marca2 = await _cria(client, "Locagil", senha=SENHA_FAKE)
    r2 = await client.patch(f"/api/marcas/{marca2['id']}", json={"senha": None})
    assert r2.status_code == 200
    assert r2.json()["has_senha"] is False


async def test_patch_senha_nova_e_get_senha_revela(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")
    assert marca["has_senha"] is False

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"senha": SENHA_FAKE})

    assert r.status_code == 200, r.text
    assert r.json()["has_senha"] is True
    _sem_senha(r.json())

    senha = await client.get(f"/api/marcas/{marca['id']}/senha")
    assert senha.status_code == 200
    assert _revelada(senha.json()) == SENHA_FAKE
    assert senha.headers["cache-control"] == "no-store"


async def test_patch_slug_null_mantem_o_slug(client, make_user, auth_as):
    """slug é NOT NULL e é a chave estável: null/"" no body = não mexe
    (renomear a marca também não troca o slug sozinho)."""
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Charlots Park")

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"slug": None})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "charlots-park"

    r2 = await client.patch(f"/api/marcas/{marca['id']}", json={"slug": ""})
    assert r2.status_code == 200, r2.text
    assert r2.json()["slug"] == "charlots-park"

    r3 = await client.patch(f"/api/marcas/{marca['id']}", json={"nome": "Charlots"})
    assert r3.status_code == 200, r3.text
    assert r3.json()["nome"] == "Charlots"
    assert r3.json()["slug"] == "charlots-park"


async def test_patch_slug_e_normalizado(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Charlots Park")

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"slug": "Outro Slug"})

    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "outro-slug"
    assert r.json()["nome"] == "Charlots Park"


async def test_patch_slug_em_uso_por_outra_marca_da_409(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    await _cria(client, "Poofy")
    marca = await _cria(client, "Locagil")

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"slug": "poofy"})

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "marca_slug_conflict"

    # Mandar o próprio slug não conflita consigo mesma.
    proprio = await client.patch(f"/api/marcas/{marca['id']}", json={"slug": "locagil"})
    assert proprio.status_code == 200, proprio.text


async def test_patch_inpi_status_invalido_da_422(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"inpi_status": "invalido"})
    assert r.status_code == 422

    ok = await client.patch(f"/api/marcas/{marca['id']}", json={"inpi_status": "registrado"})
    assert ok.status_code == 200
    assert ok.json()["inpi_status"] == "registrado"


async def test_patch_dominio_validade_vazia_limpa(client, make_user, auth_as):
    """<Input type=date> limpo manda "" — vira null, não 422."""
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy", dominio_validade="2030-01-31")
    assert marca["dominio_validade"] == "2030-01-31"

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"dominio_validade": ""})

    assert r.status_code == 200, r.text
    assert r.json()["dominio_validade"] is None


async def test_patch_dominio_validade_data_iso(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")

    r = await client.patch(
        f"/api/marcas/{marca['id']}", json={"dominio_validade": "2034-07-04"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["dominio_validade"] == "2034-07-04"

    invalida = await client.patch(
        f"/api/marcas/{marca['id']}", json={"dominio_validade": "04/07/2034"}
    )
    assert invalida.status_code == 422


async def test_patch_email_normaliza_lower_e_strip(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"email": " X@Y.COM "})

    assert r.status_code == 200, r.text
    assert r.json()["email"] == "x@y.com"


async def test_patch_texto_faz_strip_e_vazio_vira_null(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(
        client, "Poofy", usuario="poofy", classe="celular", dominio_br="poofy.com.br"
    )

    r = await client.patch(
        f"/api/marcas/{marca['id']}",
        json={
            "usuario": "  admin  ",
            "classe": "",
            "dominio_br": "   ",
            "dono_dominio": " omar ",
            "funcao": "",
            "tipo": " cel ",
            "obs": None,
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["usuario"] == "admin"
    assert body["classe"] is None
    assert body["dominio_br"] is None
    assert body["dono_dominio"] == "omar"
    assert body["funcao"] is None
    assert body["tipo"] == "cel"
    assert body["obs"] is None


async def test_patch_ativo_false(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")

    r = await client.patch(f"/api/marcas/{marca['id']}", json={"ativo": False})

    assert r.status_code == 200, r.text
    assert r.json()["ativo"] is False
    inativas = await client.get("/api/marcas", params={"ativo": "false"})
    assert [m["id"] for m in inativas.json()] == [marca["id"]]


# ======================================================== GET /senha


async def test_get_senha_sem_senha_devolve_vazio_e_no_store(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")

    r = await client.get(f"/api/marcas/{marca['id']}/senha")

    assert r.status_code == 200, r.text
    assert _revelada(r.json()) == ""
    assert r.headers["cache-control"] == "no-store"


# ==================================================== IntegrityError


async def test_corrida_no_slug_vira_409_e_nao_500(
    client, db: AsyncSession, make_user, auth_as, monkeypatch
):
    """Se a pré-checagem passar (corrida entre dois POSTs), o UNIQUE do banco
    ainda segura e o router traduz o IntegrityError em 409."""
    import app.routers.marcas as marcas_mod

    async def _nunca_em_uso(*_a, **_kw) -> bool:
        return False

    monkeypatch.setattr(marcas_mod, "_slug_em_uso", _nunca_em_uso)

    db.add(Marca(nome="x", slug="x"))
    await db.commit()

    await _admin(make_user, auth_as)
    r = await client.post("/api/marcas", json={"nome": "X"})

    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "marca_slug_conflict"

    # A sessão do app fez rollback: a API continua servindo.
    lista = await client.get("/api/marcas")
    assert lista.status_code == 200
    assert [m["slug"] for m in lista.json()] == ["x"]


# ============================================================== DELETE


async def test_delete_cascateia_nas_redes_sociais_e_padroes_de_email(
    client, db: AsyncSession, make_user, auth_as
):
    """FK ON DELETE CASCADE: apagar a marca leva as contas e os padrões de
    e-mail junto (sem relationship no ORM — é o banco quem cascateia)."""
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")
    marca_id = uuid.UUID(marca["id"])

    rede = RedeSocial(marca_id=marca_id, plataforma="instagram", conta="poofy")
    padrao = MarcaEmailPadrao(
        marca_id=marca_id, contexto="sac", nome="Resposta padrão SAC", assunto="a", corpo="b"
    )
    db.add_all([rede, padrao])
    await db.commit()
    rede_id, padrao_id = rede.id, padrao.id

    r = await client.delete(f"/api/marcas/{marca_id}")

    assert r.status_code == 204, r.text
    assert (await client.get(f"/api/marcas/{marca_id}")).status_code == 404

    sobrou = (
        await db.execute(select(RedeSocial).where(RedeSocial.id == rede_id))
    ).scalar_one_or_none()
    assert sobrou is None
    sobrou_padrao = (
        await db.execute(select(MarcaEmailPadrao).where(MarcaEmailPadrao.id == padrao_id))
    ).scalar_one_or_none()
    assert sobrou_padrao is None
    marca_db = (
        await db.execute(select(Marca).where(Marca.id == marca_id))
    ).scalar_one_or_none()
    assert marca_db is None


# ================================================================ 404


async def test_404_em_marca_inexistente(client, make_user, auth_as):
    await _admin(make_user, auth_as)
    ghost = uuid.uuid4()

    get = await client.get(f"/api/marcas/{ghost}")
    assert get.status_code == 404
    assert get.json()["detail"]["code"] == "marca_not_found"

    patch = await client.patch(f"/api/marcas/{ghost}", json={"obs": "x"})
    assert patch.status_code == 404
    assert patch.json()["detail"]["code"] == "marca_not_found"

    delete = await client.delete(f"/api/marcas/{ghost}")
    assert delete.status_code == 404
    assert delete.json()["detail"]["code"] == "marca_not_found"

    senha = await client.get(f"/api/marcas/{ghost}/senha")
    assert senha.status_code == 404
    assert senha.json()["detail"]["code"] == "marca_not_found"


# ---- correções da revisão adversarial (15/09/2026) ---------------------------


def test_senha_nao_aparece_no_repr_do_body():
    """O Sentry serializa variáveis locais com repr(); o body pydantic com a
    senha em claro ia parar no evento de um 500. `Field(repr=False)`."""
    from app.schemas.marcas import MarcaCreate, MarcaPatch, RedeSocialCreate, RedeSocialPatch

    for cls, extra in (
        (MarcaCreate, {"nome": "X"}),
        (MarcaPatch, {}),
        (RedeSocialCreate, {"marca_id": uuid.uuid4(), "plataforma": "instagram"}),
        (RedeSocialPatch, {}),
    ):
        obj = cls(senha="segredo-repr-teste", **extra)
        assert "segredo-repr-teste" not in repr(obj)
        assert obj.model_dump(exclude_unset=True)["senha"] == "segredo-repr-teste"


async def test_patch_null_em_coluna_not_null_nao_vira_409(client, make_user, auth_as):
    """`{"ativo": null}` / `{"nome": null}` / `{"inpi_status": null}` = "não
    mexe" (como slug); antes virava NotNullViolation traduzida em 409
    marca_slug_conflict — código de conflito falso pra um cliente da API."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post("/api/marcas", json={"nome": "Nula", "inpi_status": "registrado"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    r2 = await client.patch(
        f"/api/marcas/{mid}", json={"ativo": None, "nome": None, "inpi_status": None, "obs": "x"}
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["ativo"] is True
    assert body["nome"] == "Nula"
    assert body["inpi_status"] == "registrado"
    assert body["obs"] == "x"


# ================================================ v3 (15/09/2026): assinatura

_CAMPOS_MARCA_OUT = {
    "id",
    "nome",
    "slug",
    "inpi_status",
    "usuario",
    "has_senha",
    "email",
    "dominio_br",
    "dominio",
    "dono_dominio",
    "dominio_validade",
    "classe",
    "funcao",
    "tipo",
    "obs",
    "ativo",
    "sac_fone",
    "sac_email",
    "has_sac_senha",
    "whatsapp_verificacao_status",
    "whatsapp_verificacao_obs",
    "company_id",
    "empresa_razao_social",
    "site",
    "has_logo",
    "created_at",
    "updated_at",
}


async def _seed_company(
    db: AsyncSession, apelido: str, razao_social: str, cnpj: str | None = None
) -> Company:
    c = Company(apelido=apelido, razao_social=razao_social, cnpj=cnpj)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def test_marca_out_tem_os_campos_v3_e_nunca_os_segredos(client, make_user, auth_as):
    """Contrato do MarcaOut (a tela e os grids dependem das chaves): os
    campos novos da v3 entram, senha/sac_senha/logo nunca saem."""
    await _admin(make_user, auth_as)

    body = await _cria(client, "Poofy")

    assert set(body) == _CAMPOS_MARCA_OUT
    assert body["company_id"] is None
    assert body["empresa_razao_social"] is None
    assert body["site"] is None
    assert body["has_logo"] is False
    assert body["sac_fone"] is None
    assert body["sac_email"] is None
    assert body["has_sac_senha"] is False
    assert body["whatsapp_verificacao_status"] == "nao_solicitado"
    assert body["whatsapp_verificacao_obs"] is None
    _sem_senha(body)


async def test_company_id_vincula_empresa_e_out_traz_razao_social(
    client, db: AsyncSession, make_user, auth_as
):
    """`company_id` é a "Empresa da assinatura" (razão social/CNPJ no rodapé
    dos e-mails). O Out traz `empresa_razao_social` pelo join — na criação,
    no detalhe e na listagem."""
    await _admin(make_user, auth_as)
    empresa = await _seed_company(db, "Poofy", "Poofy Comércio LTDA", "12345678000195")

    body = await _cria(client, "Poofy", company_id=str(empresa.id))

    assert body["company_id"] == str(empresa.id)
    assert body["empresa_razao_social"] == "Poofy Comércio LTDA"
    detalhe = await client.get(f"/api/marcas/{body['id']}")
    assert detalhe.json()["empresa_razao_social"] == "Poofy Comércio LTDA"
    lista = await client.get("/api/marcas")
    assert [m["empresa_razao_social"] for m in lista.json()] == ["Poofy Comércio LTDA"]

    # Sem empresa: os dois vêm nulos (e a listagem não perde a marca no join).
    solta = await _cria(client, "Locagil")
    assert solta["company_id"] is None
    assert solta["empresa_razao_social"] is None
    lista2 = await client.get("/api/marcas")
    assert [m["empresa_razao_social"] for m in lista2.json()] == [None, "Poofy Comércio LTDA"]


async def test_company_id_inexistente_da_404(client, db: AsyncSession, make_user, auth_as):
    await _admin(make_user, auth_as)
    ghost = str(uuid.uuid4())

    post = await client.post("/api/marcas", json={"nome": "Poofy", "company_id": ghost})
    assert post.status_code == 404, post.text
    assert post.json()["detail"]["code"] == "company_not_found"
    # Nada foi criado pelo caminho.
    assert (await client.get("/api/marcas")).json() == []

    marca = await _cria(client, "Poofy")
    patch = await client.patch(f"/api/marcas/{marca['id']}", json={"company_id": ghost})
    assert patch.status_code == 404, patch.text
    assert patch.json()["detail"]["code"] == "company_not_found"
    assert (await client.get(f"/api/marcas/{marca['id']}")).json()["company_id"] is None

    invalido = await client.post("/api/marcas", json={"nome": "X", "company_id": "nao-e-uuid"})
    assert invalido.status_code == 422


async def test_patch_company_id_troca_e_null_desvincula(
    client, db: AsyncSession, make_user, auth_as
):
    await _admin(make_user, auth_as)
    a = await _seed_company(db, "A", "A Comércio LTDA", "11111111000111")
    b = await _seed_company(db, "B", "B Comércio LTDA", "22222222000122")
    marca = await _cria(client, "Poofy", company_id=str(a.id))
    url = f"/api/marcas/{marca['id']}"

    troca = await client.patch(url, json={"company_id": str(b.id)})
    assert troca.status_code == 200, troca.text
    assert troca.json()["company_id"] == str(b.id)
    assert troca.json()["empresa_razao_social"] == "B Comércio LTDA"

    # PATCH de outro campo (chave ausente) não mexe no vínculo.
    obs = await client.patch(url, json={"obs": "x"})
    assert obs.json()["company_id"] == str(b.id)
    assert obs.json()["empresa_razao_social"] == "B Comércio LTDA"

    solta = await client.patch(url, json={"company_id": None})
    assert solta.status_code == 200, solta.text
    assert solta.json()["company_id"] is None
    assert solta.json()["empresa_razao_social"] is None
    assert (await client.get(url)).json()["company_id"] is None


async def test_apagar_empresa_desvincula_a_marca(client, db: AsyncSession, make_user, auth_as):
    """FK ON DELETE SET NULL: a marca sobrevive à empresa, só perde a
    assinatura."""
    await _admin(make_user, auth_as)
    empresa = await _seed_company(db, "A", "A Comércio LTDA")
    marca = await _cria(client, "Poofy", company_id=str(empresa.id))

    await db.delete(empresa)
    await db.commit()

    r = await client.get(f"/api/marcas/{marca['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["company_id"] is None
    assert r.json()["empresa_razao_social"] is None


async def test_get_empresas_lista_por_apelido_sob_marcas_view(
    client, db: AsyncSession, make_user, auth_as
):
    """Select "Empresa da assinatura" do modal: GET /api/marcas/empresas sob
    `marcas:view` (não exige `empresa:view`), ordenado por apelido, só
    {id, apelido, razao_social, cnpj}. A rota literal não colide com
    GET /{marca_id}."""
    await _seed_company(db, "Zeta", "Zeta LTDA", "33333333000133")
    await _seed_company(db, "Alpha", "Alpha LTDA")
    viewer = await make_user(permissions=_perms(view=True))
    auth_as(viewer)

    r = await client.get("/api/marcas/empresas")

    assert r.status_code == 200, r.text
    assert [e["apelido"] for e in r.json()] == ["Alpha", "Zeta"]
    assert set(r.json()[0]) == {"id", "apelido", "razao_social", "cnpj"}
    assert r.json()[0]["razao_social"] == "Alpha LTDA"
    assert r.json()[0]["cnpj"] is None
    assert r.json()[1]["cnpj"] == "33333333000133"

    # Quem só tem `empresa` (a aba Empresas) não entra por aqui.
    sem = await make_user(permissions={"empresa": {"view": True, "edit": True, "delete": True}})
    auth_as(sem)
    negado = await client.get("/api/marcas/empresas")
    assert negado.status_code == 403
    assert negado.json()["detail"] == {"code": "forbidden", "resource": "marcas", "action": "view"}


async def test_site_valida_esquema_e_vazio_limpa(client, make_user, auth_as):
    """`site` precisa começar com http:// ou https:// (422 site_invalido);
    strip; vazio limpa."""
    await _admin(make_user, auth_as)

    sem_esquema = await client.post("/api/marcas", json={"nome": "Poofy", "site": "poofy.com"})
    assert sem_esquema.status_code == 422, sem_esquema.text
    assert "site_invalido" in sem_esquema.text

    marca = await _cria(client, "Poofy", site="  https://poofy.com.br ")
    assert marca["site"] == "https://poofy.com.br"
    url = f"/api/marcas/{marca['id']}"

    ftp = await client.patch(url, json={"site": "ftp://poofy.com"})
    assert ftp.status_code == 422, ftp.text
    assert "site_invalido" in ftp.text
    assert (await client.get(url)).json()["site"] == "https://poofy.com.br"

    http = await client.patch(url, json={"site": "http://poofy.com"})
    assert http.status_code == 200, http.text
    assert http.json()["site"] == "http://poofy.com"

    vazio = await client.patch(url, json={"site": ""})
    assert vazio.status_code == 200, vazio.text
    assert vazio.json()["site"] is None
    nulo = await client.patch(url, json={"site": "https://x.com"})
    assert nulo.json()["site"] == "https://x.com"
    nulo = await client.patch(url, json={"site": None})
    assert nulo.json()["site"] is None


# ================================================================== logo

PNG_FAKE = b"\x89PNG\r\n\x1a\n" + b"nao-e-png-de-verdade-mas-os-magic-bytes-batem-" * 4
JPEG_FAKE = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF_FAKE = b"GIF89a" + b"\x00" * 32


def _arquivo(
    conteudo: bytes, nome: str = "logo.png", content_type: str = "image/png"
) -> dict:
    """Campo multipart `file` do PUT /logo."""
    return {"file": (nome, conteudo, content_type)}


async def test_logo_put_get_delete(client, make_user, auth_as):
    """PUT multipart `file` → has_logo; GET devolve os bytes com o mime dos
    MAGIC BYTES (não o content-type do upload), nosniff, inline e cache
    privado; DELETE limpa e o GET vira 404 marca_sem_logo."""
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")
    url = f"/api/marcas/{marca['id']}/logo"
    assert marca["has_logo"] is False
    sem = await client.get(url)
    assert sem.status_code == 404
    assert sem.json()["detail"]["code"] == "marca_sem_logo"

    # content-type do upload mente ("text/plain"): o tipo sai dos magic bytes.
    put = await client.put(url, files=_arquivo(PNG_FAKE, "logo.txt", "text/plain"))
    assert put.status_code == 200, put.text
    assert put.json()["has_logo"] is True
    assert put.json()["id"] == marca["id"]
    assert "logo_mime" not in put.json()
    _sem_senha(put.json())

    get = await client.get(url)
    assert get.status_code == 200
    assert get.content == PNG_FAKE
    assert get.headers["content-type"] == "image/png"
    assert get.headers["x-content-type-options"] == "nosniff"
    assert get.headers["cache-control"] == "private, max-age=300"
    assert get.headers["content-disposition"] == "inline"
    # Listagem e detalhe só sinalizam (has_logo) — os bytes ficam deferred.
    assert (await client.get("/api/marcas")).json()[0]["has_logo"] is True
    assert (await client.get(f"/api/marcas/{marca['id']}")).json()["has_logo"] is True

    # Trocar por JPEG e por GIF (magic bytes de cada um).
    jpeg = await client.put(url, files=_arquivo(JPEG_FAKE, "logo.jpg", "image/jpeg"))
    assert jpeg.status_code == 200, jpeg.text
    get_jpeg = await client.get(url)
    assert get_jpeg.headers["content-type"] == "image/jpeg"
    assert get_jpeg.content == JPEG_FAKE
    gif = await client.put(url, files=_arquivo(GIF_FAKE, "logo.gif", "image/gif"))
    assert gif.status_code == 200, gif.text
    assert (await client.get(url)).headers["content-type"] == "image/gif"

    delete = await client.delete(url)
    assert delete.status_code == 200, delete.text
    assert delete.json()["has_logo"] is False
    _sem_senha(delete.json())
    depois = await client.get(url)
    assert depois.status_code == 404
    assert depois.json()["detail"]["code"] == "marca_sem_logo"
    # Remover de novo é idempotente.
    assert (await client.delete(url)).status_code == 200

    ghost = f"/api/marcas/{uuid.uuid4()}/logo"
    r = await client.get(ghost)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "marca_not_found"
    assert (await client.put(ghost, files=_arquivo(PNG_FAKE))).status_code == 404
    assert (await client.delete(ghost)).status_code == 404


async def test_logo_tipo_invalido_415_e_grande_413(client, make_user, auth_as):
    """Só png/jpeg/gif pelos magic bytes (texto, SVG — que viraria HTML —,
    WebP, vazio ou cabeçalho cortado → 415 logo_tipo_invalido); > 1 MB →
    413 logo_too_large; exatamente 1 MB passa. Tentativa recusada não grava."""
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")
    url = f"/api/marcas/{marca['id']}/logo"

    for conteudo, nome, ct in (
        (b"isso nao e imagem", "logo.png", "image/png"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "logo.svg", "image/svg+xml"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "logo.webp", "image/webp"),
        (b"", "logo.png", "image/png"),
        (b"\x89PN", "logo.png", "image/png"),
    ):
        r = await client.put(url, files=_arquivo(conteudo, nome, ct))
        assert r.status_code == 415, (nome, r.text)
        assert r.json()["detail"]["code"] == "logo_tipo_invalido"

    grande = await client.put(url, files=_arquivo(b"\x89PNG\r\n\x1a\n" + b"\x00" * LOGO_MAX_BYTES))
    assert grande.status_code == 413, grande.text
    assert grande.json()["detail"]["code"] == "logo_too_large"

    # Nenhuma das tentativas gravou nada.
    assert (await client.get(f"/api/marcas/{marca['id']}")).json()["has_logo"] is False
    assert (await client.get(url)).status_code == 404

    # Cabe no upload (1 MB) mas não no que é GUARDADO (300 KB) — um "PNG" que
    # o PyMuPDF não decodifica não encolhe, então é recusado.
    meio = b"\x89PNG\r\n\x1a\n" + b"\x00" * (LOGO_MAX_GUARDADO + 1)
    r = await client.put(url, files=_arquivo(meio))
    assert r.status_code == 413, r.text

    limite = b"\x89PNG\r\n\x1a\n"
    limite += b"\x00" * (LOGO_MAX_GUARDADO - len(limite))
    ok = await client.put(url, files=_arquivo(limite))
    assert ok.status_code == 200, ok.text
    assert ok.json()["has_logo"] is True
    assert len((await client.get(url)).content) == LOGO_MAX_GUARDADO


async def test_logo_grande_e_reduzido_no_upload(client, make_user, auth_as):
    """Logo largo (1200 px) é encolhido pra ≤ LOGO_MAX_LARGURA e reencodado em
    PNG no upload — ele vai em base64 dentro de cada assinatura/e-mail, e o
    Tuta recomenda ≤ 15 KB de assinatura. Logo pequeno fica como veio."""
    import fitz

    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")
    url = f"/api/marcas/{marca['id']}/logo"

    grande = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1200, 400), 0)
    grande.set_rect(grande.irect, (30, 144, 255))
    r = await client.put(url, files=_arquivo(grande.tobytes("png"), "logo.png", "image/png"))
    assert r.status_code == 200, r.text
    guardado = await client.get(url)
    assert guardado.headers["content-type"].startswith("image/png")
    pix = fitz.Pixmap(guardado.content)
    assert pix.width <= LOGO_MAX_LARGURA
    assert pix.width == 300 and pix.height == 100  # 1200 → 600 → 300

    pequeno = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 80), 0)
    pequeno.set_rect(pequeno.irect, (255, 0, 0))
    raw = pequeno.tobytes("png")
    r = await client.put(url, files=_arquivo(raw, "logo.png", "image/png"))
    assert r.status_code == 200, r.text
    assert (await client.get(url)).content == raw


async def test_logo_get_liberado_para_quem_ve_qualquer_das_tres_abas(client, make_user, auth_as):
    """A miniatura aparece nos grids de Marcas, Redes Sociais e E-mails: GET
    /logo libera com view de QUALQUER um dos três recursos; PUT/DELETE
    continuam atrás de marcas:edit. Sem nenhum dos três → 403 (resource
    'marcas', o principal)."""
    await _admin(make_user, auth_as)
    marca = await _cria(client, "Poofy")
    url = f"/api/marcas/{marca['id']}/logo"
    assert (await client.put(url, files=_arquivo(PNG_FAKE))).status_code == 200

    for perms in (
        {"redes_sociais": {"view": True, "edit": False, "delete": False}},
        {"email_padroes": {"view": True, "edit": False, "delete": False}},
        _perms(view=True),
    ):
        auth_as(await make_user(permissions=perms))
        r = await client.get(url)
        assert r.status_code == 200, (perms, r.text)
        assert r.content == PNG_FAKE
        assert r.headers["content-type"] == "image/png"
        # Sem marcas:edit ninguém troca nem remove.
        put = await client.put(url, files=_arquivo(JPEG_FAKE, "logo.jpg", "image/jpeg"))
        assert put.status_code == 403, perms
        assert put.json()["detail"] == {"code": "forbidden", "resource": "marcas", "action": "edit"}
        assert (await client.delete(url)).status_code == 403

    nada = await make_user(permissions={"empresa": {"view": True, "edit": True, "delete": True}})
    auth_as(nada)
    negado = await client.get(url)
    assert negado.status_code == 403
    assert negado.json()["detail"] == {"code": "forbidden", "resource": "marcas", "action": "view"}

    # O logo continua o PNG original (os 403 não gravaram nada).
    auth_as(await make_user(role=UserRole.ADMIN))
    assert (await client.get(url)).content == PNG_FAKE


# ======================================== sac_* / verificação do Zap (leitura)


async def test_marcas_nao_edita_sac_nem_verificacao_do_zap(
    client, db: AsyncSession, make_user, auth_as
):
    """v3.1: fone/usuário/senha das redes e a verificação do Zap têm UM dono,
    a aba Redes Sociais (PATCH /api/redes-sociais/marca/{id}). Aqui
    MarcaCreate/MarcaPatch ignoram essas chaves (pydantic descarta extras —
    nem 422 nem gravação) e o Out só expõe sac_fone/sac_email/has_sac_senha/
    whatsapp_* pra leitura; `has_*` são só leitura. Não existe
    /api/marcas/{id}/sac-senha."""
    await _admin(make_user, auth_as)

    body = await _cria(
        client,
        "Poofy",
        sac_fone="11983517003",
        sac_email="sac@poofy.com",
        sac_senha=SENHA_FAKE,
        whatsapp_verificacao_status="verificado",
        whatsapp_verificacao_obs="protocolo",
        has_sac_senha=True,
        has_logo=True,
        has_senha=True,
        empresa_razao_social="X",
    )
    assert body["sac_fone"] is None
    assert body["sac_email"] is None
    assert body["has_sac_senha"] is False
    assert body["whatsapp_verificacao_status"] == "nao_solicitado"
    assert body["whatsapp_verificacao_obs"] is None
    assert body["has_logo"] is False
    assert body["has_senha"] is False
    assert body["empresa_razao_social"] is None
    _sem_senha(body)
    url = f"/api/marcas/{body['id']}"

    # Valores gravados pela aba Redes Sociais (aqui direto no banco).
    m = (await db.execute(select(Marca).where(Marca.id == uuid.UUID(body["id"])))).scalar_one()
    m.sac_fone = "11983517003"
    m.sac_email = "sac@poofy.com"
    m.sac_senha_enc = encrypt(SENHA_FAKE)
    m.whatsapp_verificacao_status = "em_andamento"
    m.whatsapp_verificacao_obs = "protocolo 42"
    await db.commit()
    enc_antes = m.sac_senha_enc

    lido = (await client.get(url)).json()
    assert lido["sac_fone"] == "11983517003"
    assert lido["sac_email"] == "sac@poofy.com"
    assert lido["has_sac_senha"] is True
    assert lido["whatsapp_verificacao_status"] == "em_andamento"
    assert lido["whatsapp_verificacao_obs"] == "protocolo 42"
    _sem_senha(lido)

    patch = await client.patch(
        url,
        json={
            "sac_fone": "0",
            "sac_email": "outro@x.com",
            "sac_senha": "",
            "whatsapp_verificacao_status": "recusado",
            "whatsapp_verificacao_obs": "x",
            "has_sac_senha": False,
            "has_logo": True,
            "obs": "só a obs",
        },
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["obs"] == "só a obs"
    assert patch.json()["sac_fone"] == "11983517003"
    assert patch.json()["sac_email"] == "sac@poofy.com"
    assert patch.json()["has_sac_senha"] is True
    assert patch.json()["whatsapp_verificacao_status"] == "em_andamento"
    assert patch.json()["whatsapp_verificacao_obs"] == "protocolo 42"
    assert patch.json()["has_logo"] is False
    await db.refresh(m)
    assert m.sac_senha_enc == enc_antes
    assert m.obs == "só a obs"

    # A revelação da senha das redes/SAC é da aba Redes Sociais.
    assert (await client.get(f"{url}/sac-senha")).status_code == 404
    # Um status fora do enum também não vira 422 aqui: é chave ignorada.
    ignorado = await client.patch(url, json={"whatsapp_verificacao_status": "xxx"})
    assert ignorado.status_code == 200, ignorado.text
    assert ignorado.json()["whatsapp_verificacao_status"] == "em_andamento"


# ================================================================== Sentry


def test_sentry_scrubber_mascara_senha_e_sac_senha():
    """services/sentry.py: o denylist padrão do SDK só cobre password/passwd
    — um 500 num PATCH com `senha`/`sac_senha` mandava a senha em claro pro
    Sentry (body do request). A lista lá é literal (não exportada):
    replicada aqui e conferida no fonte — se alguém tirar uma chave de lá,
    este teste avisa."""
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    import app.services.sentry as sentry_mod

    extras = ["senha", "senha_enc", "sac_senha", "sac_senha_enc", "password_enc", "pwd"]
    fonte = Path(sentry_mod.__file__).read_text(encoding="utf-8")
    for chave in extras:
        assert f'"{chave}",' in fonte, chave
    assert "recursive=True" in fonte
    assert "include_local_variables=False" in fonte

    scrubber = EventScrubber(denylist=[*DEFAULT_DENYLIST, *extras], recursive=True)
    event = {
        "request": {
            "data": {
                "sac_senha": "x",
                "senha": "y",
                "nome": "Poofy",
                "conta": {"senha_enc": "z", "sac_senha_enc": "w", "usuario": "login"},
            }
        }
    }
    scrubber.scrub_event(event)

    data = event["request"]["data"]

    def _valor(v: object) -> object:
        # O SDK troca por AnnotatedValue("[Filtered]").
        return getattr(v, "value", v)

    assert _valor(data["sac_senha"]) == "[Filtered]"
    assert _valor(data["senha"]) == "[Filtered]"
    assert _valor(data["conta"]["senha_enc"]) == "[Filtered]"
    assert _valor(data["conta"]["sac_senha_enc"]) == "[Filtered]"
    assert data["nome"] == "Poofy"
    assert data["conta"]["usuario"] == "login"
