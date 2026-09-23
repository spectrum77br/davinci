"""Cadastros › Redes Sociais — /api/redes-sociais (routers/redes_sociais.py).

Pedido do Eduardo (15/09/2026): aba nova no grupo Cadastros com as contas de
cada marca por plataforma, vindas da planilha `redes sociais.xlsx`. Cobre o
CRUD, o grid (linha = marca, coluna = plataforma), a normalização dos campos
(conta sem "@", fone só dígitos, e-mail minúsculo, url com esquema), as duas
unicidades por índice parcial (mesma conta em duas marcas / duas linhas sem
conta na mesma marca+plataforma) tanto na pré-checagem quanto no
IntegrityError, a permissão `redes_sociais` (separada de `marcas`) e a senha
cifrada — que NUNCA sai em listagem/grid/detalhe, só no GET /{id}/senha.

v3 (15/09/2026, tarde — "seguir bem a planilha"): só as 5 redes da aba
r.social; fone/usuário(e-mail)/senha são da MARCA (`sac_*`) e esta aba é a
DONA deles (PATCH /marca/{id} + GET /marca/{id}/sac-senha, sob
redes_sociais:edit); a conta só guarda o que DIFERE (NULL = herda) e o Out
resolve os efetivos (`email_efetivo`, `fone_efetivo`, `has_senha_efetiva`,
`senha_origem`); `verificado` (bool) saiu, entrou `verificacao_status` +
`verificacao_obs` (o DaVinci só registra o andamento do selo).

Senhas aqui são todas FAKE ("abc", "senha-fake-…"); a planilha real fica
fora do git e nunca entra nos testes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers.redes_sociais as redes_router
from app.models import REDES_SOCIAIS_PLATAFORMAS, Marca, UserRole
from app.schemas.segments import _slugify
from app.security.cipher import encrypt

pytestmark = pytest.mark.asyncio

# MarcaRef (v3): a linha da marca na aba r.social — fone/usuário/has_sac_senha,
# verificação do Zap, função/tipo/obs, has_logo. NUNCA login/e-mail/domínio do
# registro (permissão `marcas`).
_CAMPOS_MARCA_REF = {
    "id",
    "nome",
    "slug",
    "ativo",
    "classe",
    "funcao",
    "tipo",
    "obs",
    "sac_fone",
    "sac_email",
    "has_sac_senha",
    "whatsapp_verificacao_status",
    "whatsapp_verificacao_obs",
    "has_logo",
    "updated_at",
}
_CAMPOS_SO_DA_ABA_MARCAS = (
    "usuario",
    "email",
    "dominio",
    "dominio_br",
    "senha",
    "senha_enc",
    "sac_senha",
    "sac_senha_enc",
    "logo",
)
PLATAFORMAS_PLANILHA = ["instagram", "facebook", "twitter", "tiktok", "youtube"]


def _perms(*, view: bool = True, edit: bool = True, delete: bool = True) -> dict:
    return {"redes_sociais": {"view": view, "edit": edit, "delete": delete}}


async def _seed_marca(db: AsyncSession, nome: str, **kw: object) -> Marca:
    m = Marca(nome=nome, slug=_slugify(nome), **kw)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


def _body(marca: Marca, plataforma: str = "instagram", conta: str | None = "poofy", **kw) -> dict:
    return {"marca_id": str(marca.id), "plataforma": plataforma, "conta": conta, **kw}


def _sem_senha(body: dict) -> None:
    """Nenhum Out devolve a senha (SPEC v2): só `has_senha` (e, na v3,
    `has_senha_efetiva` + `senha_origem`)."""
    assert "senha" not in body
    assert "senha_enc" not in body
    assert "has_senha" in body
    assert "has_senha_efetiva" in body
    assert "senha_origem" in body


def _senha(body: dict) -> tuple[str, str | None]:
    """SenhaOut v3: {senha, origem} — 'conta' (própria), 'marca' (herdada de
    sac_senha) ou null (nenhuma)."""
    assert set(body) == {"senha", "origem"}
    return body["senha"], body["origem"]


async def _admin(make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    return admin


# ============================================================== POST / validação


async def test_post_exige_marca_existente(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)

    r = await client.post(
        "/api/redes-sociais",
        json={"marca_id": str(uuid.uuid4()), "plataforma": "instagram", "conta": "x"},
    )

    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "marca_not_found"


async def test_post_cria_conta_e_normaliza_campos(client, db, make_user, auth_as):
    """conta perde o "@" e os espaços; fone vira só dígitos; e-mail vai pra
    minúsculo sem espaços; senha vira `has_senha` (nunca o valor)."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")

    r = await client.post(
        "/api/redes-sociais",
        json=_body(
            marca,
            conta="@Poofy_Brasil ",
            fone="11 98351-7003",
            email="  SAC@Poofy.COM ",
            url="https://instagram.com/Poofy_Brasil",
            senha="senha-fake-123",
            verificacao_status="verificado",
            verificacao_obs="  protocolo 123 ",
            obs="  loja oficial ",
        ),
    )

    assert r.status_code == 201, r.text
    body = r.json()
    _sem_senha(body)
    assert body["has_senha"] is True
    assert body["marca_id"] == str(marca.id)
    assert body["marca_nome"] == "Poofy"
    assert body["plataforma"] == "instagram"
    assert body["conta"] == "Poofy_Brasil"
    assert body["fone"] == "11983517003"
    assert body["email"] == "sac@poofy.com"
    assert body["url"] == "https://instagram.com/Poofy_Brasil"
    assert body["verificacao_status"] == "verificado"
    assert body["verificacao_obs"] == "protocolo 123"
    assert "verificado" not in body
    assert body["obs"] == "loja oficial"
    assert body["ativo"] is True
    # Com e-mail/fone/senha próprios, os efetivos são os da conta.
    assert body["email_efetivo"] == "sac@poofy.com"
    assert body["fone_efetivo"] == "11983517003"
    assert body["has_senha_efetiva"] is True
    assert body["senha_origem"] == "conta"


async def test_post_url_sem_esquema_422(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")

    r = await client.post("/api/redes-sociais", json=_body(marca, url="instagram.com/x"))

    assert r.status_code == 422, r.text


async def test_post_plataforma_invalida_422(client, db, make_user, auth_as):
    """Plataforma é Literal no schema; whatsapp saiu na SPEC v2."""
    await _admin(make_user, auth_as)
    marca = await _seed_marca(db, "Poofy")

    orkut = await client.post("/api/redes-sociais", json=_body(marca, plataforma="orkut"))
    assert orkut.status_code == 422, orkut.text

    whatsapp = await client.post("/api/redes-sociais", json=_body(marca, plataforma="whatsapp"))
    assert whatsapp.status_code == 422, whatsapp.text


# ==================================================================== conflitos


async def test_post_mesma_conta_outra_marca_409(client, db, make_user, auth_as):
    """A mesma conta (case-insensitive) não pode estar em duas marcas na
    mesma plataforma — pré-checagem com código legível."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")

    ok = await client.post("/api/redes-sociais", json=_body(poofy, conta="Poofy_Brasil"))
    assert ok.status_code == 201, ok.text

    dup = await client.post("/api/redes-sociais", json=_body(locagil, conta="poofy_brasil"))
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["code"] == "rede_social_conta_conflict"


async def test_post_mesma_conta_outra_plataforma_201(client, db, make_user, auth_as):
    """A unicidade é por plataforma: o mesmo @ no instagram e no tiktok pode."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")

    ig = await client.post("/api/redes-sociais", json=_body(poofy, "instagram", "poofy"))
    assert ig.status_code == 201, ig.text

    tt = await client.post("/api/redes-sociais", json=_body(locagil, "tiktok", "poofy"))
    assert tt.status_code == 201, tt.text
    assert tt.json()["plataforma"] == "tiktok"


async def test_post_segunda_linha_sem_conta_409(client, db, make_user, auth_as):
    """Linha "sem conta" (e-mail/fone reservados) é no máximo UMA por
    (marca, plataforma); outra marca na mesma plataforma pode ter a sua."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")

    r1 = await client.post("/api/redes-sociais", json=_body(poofy, conta=None, email="a@b.com"))
    assert r1.status_code == 201, r1.text
    assert r1.json()["conta"] is None

    # "?" da planilha e "@" sozinho também viram conta NULL → mesmo placeholder.
    r2 = await client.post("/api/redes-sociais", json=_body(poofy, conta="?"))
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["code"] == "rede_social_placeholder_conflict"

    outra = await client.post("/api/redes-sociais", json=_body(locagil, conta=None))
    assert outra.status_code == 201, outra.text


async def test_patch_move_conta_para_existente_409(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")

    await client.post("/api/redes-sociais", json=_body(poofy, conta="poofy"))
    sem_conta = await client.post("/api/redes-sociais", json=_body(poofy, conta=None))
    assert sem_conta.status_code == 201, sem_conta.text
    lg = await client.post("/api/redes-sociais", json=_body(locagil, conta="locagil"))
    assert lg.status_code == 201, lg.text
    lg_id = lg.json()["id"]

    # Renomear pra uma conta que já existe na plataforma (outra marca, outro case).
    conta = await client.patch(f"/api/redes-sociais/{lg_id}", json={"conta": "POOFY"})
    assert conta.status_code == 409, conta.text
    assert conta.json()["detail"]["code"] == "rede_social_conta_conflict"

    # Virar placeholder numa marca+plataforma que já tem o seu.
    placeholder = await client.patch(
        f"/api/redes-sociais/{lg_id}", json={"marca_id": str(poofy.id), "conta": None}
    )
    assert placeholder.status_code == 409, placeholder.text
    assert placeholder.json()["detail"]["code"] == "rede_social_placeholder_conflict"

    # Nada mudou na linha.
    atual = await client.get(f"/api/redes-sociais/{lg_id}")
    assert atual.json()["conta"] == "locagil"
    assert atual.json()["marca_id"] == str(locagil.id)


async def test_patch_sem_mexer_na_chave_nao_conflita_consigo(client, db, make_user, auth_as):
    """PATCH só de obs (ou repetindo a própria conta) não pode bater no
    índice da própria linha — regressão clássica do "exceto=r.id"."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post("/api/redes-sociais", json=_body(poofy, conta="poofy"))
    rede_id = criado.json()["id"]

    obs = await client.patch(f"/api/redes-sociais/{rede_id}", json={"obs": "só a obs"})
    assert obs.status_code == 200, obs.text
    assert obs.json()["obs"] == "só a obs"
    assert obs.json()["conta"] == "poofy"

    mesma = await client.patch(f"/api/redes-sociais/{rede_id}", json={"conta": "@poofy"})
    assert mesma.status_code == 200, mesma.text
    assert mesma.json()["conta"] == "poofy"

    # Placeholder repetindo os próprios (marca, plataforma) idem.
    ph = await client.post("/api/redes-sociais", json=_body(poofy, "tiktok", None))
    ph_id = ph.json()["id"]
    ph_patch = await client.patch(f"/api/redes-sociais/{ph_id}", json={"plataforma": "tiktok"})
    assert ph_patch.status_code == 200, ph_patch.text


# ================================================= IntegrityError (corrida) → 409


async def test_integrity_error_vira_409_conta(client, db, make_user, auth_as, monkeypatch):
    """Se duas requisições passam pela pré-checagem ao mesmo tempo, o índice
    parcial segura e o router traduz o IntegrityError pro mesmo código
    (não 500). Simula tirando a pré-checagem."""

    async def _sem_checagem(session, **kw):
        return None

    monkeypatch.setattr(redes_router, "_checa_conflito", _sem_checagem)
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")

    ok = await client.post("/api/redes-sociais", json=_body(poofy, conta="Poofy"))
    assert ok.status_code == 201, ok.text

    dup = await client.post("/api/redes-sociais", json=_body(locagil, conta="POOFY"))
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["code"] == "rede_social_conta_conflict"

    # O PATCH também cai no índice e também traduz.
    outra = await client.post("/api/redes-sociais", json=_body(locagil, conta="locagil"))
    patch = await client.patch(f"/api/redes-sociais/{outra.json()['id']}", json={"conta": "poofy"})
    assert patch.status_code == 409, patch.text
    assert patch.json()["detail"]["code"] == "rede_social_conta_conflict"

    # A API segue de pé (sessão foi desfeita, nada ficou pela metade).
    lista = await client.get("/api/redes-sociais")
    assert lista.status_code == 200
    assert sorted(x["conta"] for x in lista.json()) == ["Poofy", "locagil"]


async def test_integrity_error_vira_409_placeholder(client, db, make_user, auth_as, monkeypatch):
    async def _sem_checagem(session, **kw):
        return None

    monkeypatch.setattr(redes_router, "_checa_conflito", _sem_checagem)
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")

    ok = await client.post("/api/redes-sociais", json=_body(poofy, conta=None))
    assert ok.status_code == 201, ok.text

    dup = await client.post("/api/redes-sociais", json=_body(poofy, conta=None))
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["code"] == "rede_social_placeholder_conflict"


# ========================================================================= grid


async def test_grid_todas_as_marcas_por_nome_com_toda_plataforma(client, db, make_user, auth_as):
    """Grid = TODAS as marcas (mesmo sem conta) por nome; cada linha tem
    chave pra toda plataforma (lista vazia quando não há), as contas caem na
    célula certa e a marca vem enxuta (MarcaRef — sem login/e-mail/domínio,
    que são da permissão `marcas`)."""
    await _admin(make_user, auth_as)
    zeta = await _seed_marca(db, "Zeta", usuario="login-zeta", email="z@z.com", dominio="z.com")
    alpha = await _seed_marca(db, "Alpha", classe="celular", funcao="prc", tipo="cel")

    contas = (("instagram", "alpha_b"), ("instagram", "alpha_a"), ("youtube", "alpha_yt"))
    for plat, conta in contas:
        r = await client.post(
            "/api/redes-sociais", json=_body(alpha, plat, conta, senha="senha-fake")
        )
        assert r.status_code == 201, r.text

    g = await client.get("/api/redes-sociais/grid")

    assert g.status_code == 200, g.text
    body = g.json()
    assert body["plataformas"] == list(REDES_SOCIAIS_PLATAFORMAS)
    assert body["plataformas"] == PLATAFORMAS_PLANILHA
    assert [row["marca"]["nome"] for row in body["rows"]] == ["Alpha", "Zeta"]
    for row in body["rows"]:
        assert set(row["marca"].keys()) == _CAMPOS_MARCA_REF
        for chave in _CAMPOS_SO_DA_ABA_MARCAS:
            assert chave not in row["marca"], chave
        assert set(row["cells"].keys()) == set(REDES_SOCIAIS_PLATAFORMAS)
        for redes in row["cells"].values():
            for rede in redes:
                _sem_senha(rede)

    alpha_row, zeta_row = body["rows"]
    assert alpha_row["marca"]["id"] == str(alpha.id)
    assert alpha_row["marca"]["classe"] == "celular"
    assert alpha_row["marca"]["funcao"] == "prc"
    assert alpha_row["marca"]["tipo"] == "cel"
    # Dentro da célula, ordenado por conta.
    assert [c["conta"] for c in alpha_row["cells"]["instagram"]] == ["alpha_a", "alpha_b"]
    assert [c["conta"] for c in alpha_row["cells"]["youtube"]] == ["alpha_yt"]
    assert alpha_row["cells"]["instagram"][0]["marca_nome"] == "Alpha"
    assert alpha_row["cells"]["instagram"][0]["has_senha"] is True
    assert alpha_row["cells"]["tiktok"] == []

    assert zeta_row["marca"]["id"] == str(zeta.id)
    assert all(v == [] for v in zeta_row["cells"].values())


# ============================================================ lista / detalhe


async def test_lista_filtra_por_marca_plataforma_e_busca(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")

    for marca, plat, conta, email in (
        (poofy, "tiktok", "poofy_tt", None),
        (poofy, "instagram", "poofy_ig", "sac@poofy.com"),
        (locagil, "instagram", "lg_oficial", "contato@lg.com"),
    ):
        r = await client.post("/api/redes-sociais", json=_body(marca, plat, conta, email=email))
        assert r.status_code == 201, r.text

    tudo = await client.get("/api/redes-sociais")
    assert tudo.status_code == 200, tudo.text
    # Ordem: marca, plataforma, conta.
    assert [(x["marca_nome"], x["plataforma"]) for x in tudo.json()] == [
        ("Locagil", "instagram"),
        ("Poofy", "instagram"),
        ("Poofy", "tiktok"),
    ]
    for x in tudo.json():
        _sem_senha(x)

    por_marca = await client.get("/api/redes-sociais", params={"marca_id": str(poofy.id)})
    assert sorted(x["conta"] for x in por_marca.json()) == ["poofy_ig", "poofy_tt"]

    por_plat = await client.get("/api/redes-sociais", params={"plataforma": " TikTok "})
    assert [x["conta"] for x in por_plat.json()] == ["poofy_tt"]

    por_conta = await client.get("/api/redes-sociais", params={"search": "POOFY_TT"})
    assert [x["conta"] for x in por_conta.json()] == ["poofy_tt"]

    por_email = await client.get("/api/redes-sociais", params={"search": "contato@"})
    assert [x["conta"] for x in por_email.json()] == ["lg_oficial"]

    por_marca_nome = await client.get("/api/redes-sociais", params={"search": "locag"})
    assert [x["conta"] for x in por_marca_nome.json()] == ["lg_oficial"]

    combinado = await client.get(
        "/api/redes-sociais",
        params={"marca_id": str(poofy.id), "plataforma": "instagram", "search": "poofy"},
    )
    assert [x["conta"] for x in combinado.json()] == ["poofy_ig"]


async def test_get_detalhe_e_404(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post("/api/redes-sociais", json=_body(poofy, senha="senha-fake"))
    rede_id = criado.json()["id"]

    r = await client.get(f"/api/redes-sociais/{rede_id}")

    assert r.status_code == 200, r.text
    body = r.json()
    _sem_senha(body)
    assert body["id"] == rede_id
    assert body["marca_nome"] == "Poofy"
    assert body["has_senha"] is True

    ghost = await client.get(f"/api/redes-sociais/{uuid.uuid4()}")
    assert ghost.status_code == 404
    assert ghost.json()["detail"]["code"] == "rede_social_not_found"


# =================================================================== permissões


async def test_sem_permissao_403(client, db, make_user, auth_as):
    """`redes_sociais` é recurso próprio: ter `marcas` não libera esta aba."""
    user = await make_user(
        permissions={"marcas": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(user)

    lista = await client.get("/api/redes-sociais")
    assert lista.status_code == 403
    assert lista.json()["detail"] == {
        "code": "forbidden",
        "resource": "redes_sociais",
        "action": "view",
    }

    grid = await client.get("/api/redes-sociais/grid")
    assert grid.status_code == 403


async def test_so_view_lista_mas_nao_edita_nem_ve_senha(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post("/api/redes-sociais", json=_body(poofy, senha="senha-fake"))
    assert criado.status_code == 201, criado.text
    rede_id = criado.json()["id"]

    viewer = await make_user(permissions=_perms(view=True, edit=False, delete=False))
    auth_as(viewer)

    lista = await client.get("/api/redes-sociais")
    assert lista.status_code == 200
    assert [x["id"] for x in lista.json()] == [rede_id]
    _sem_senha(lista.json()[0])

    grid = await client.get("/api/redes-sociais/grid")
    assert grid.status_code == 200

    detalhe = await client.get(f"/api/redes-sociais/{rede_id}")
    assert detalhe.status_code == 200

    post = await client.post("/api/redes-sociais", json=_body(poofy, "tiktok", "poofy_tt"))
    assert post.status_code == 403
    assert post.json()["detail"]["action"] == "edit"

    patch = await client.patch(f"/api/redes-sociais/{rede_id}", json={"obs": "x"})
    assert patch.status_code == 403

    delete = await client.delete(f"/api/redes-sociais/{rede_id}")
    assert delete.status_code == 403

    senha = await client.get(f"/api/redes-sociais/{rede_id}/senha")
    assert senha.status_code == 403
    assert "senha" not in senha.json().get("detail", {})

    # Nada foi alterado pelo caminho: a conta continua igual.
    auth_as(admin)
    atual = await client.get(f"/api/redes-sociais/{rede_id}")
    assert atual.json()["obs"] is None
    assert atual.json()["has_senha"] is True


async def test_edit_sem_delete_403_no_delete(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    rede_id = (await client.post("/api/redes-sociais", json=_body(poofy))).json()["id"]

    editor = await make_user(permissions=_perms(view=True, edit=True, delete=False))
    auth_as(editor)

    patch = await client.patch(f"/api/redes-sociais/{rede_id}", json={"obs": "pode editar"})
    assert patch.status_code == 200, patch.text
    assert patch.json()["obs"] == "pode editar"

    senha = await client.get(f"/api/redes-sociais/{rede_id}/senha")
    assert senha.status_code == 200

    delete = await client.delete(f"/api/redes-sociais/{rede_id}")
    assert delete.status_code == 403
    assert delete.json()["detail"]["action"] == "delete"

    auth_as(admin)
    assert (await client.get(f"/api/redes-sociais/{rede_id}")).status_code == 200


# ======================================================================== senha


async def test_senha_patch_limpa_cifra_e_revela(client, db, make_user, auth_as):
    """PATCH: chave ausente mantém; "" ou null limpa; texto cifra. O valor só
    volta no GET /{id}/senha (sem cache), com a `origem` — "" e null quando
    não há senha nem na conta nem na marca."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post("/api/redes-sociais", json=_body(poofy))
    assert criado.json()["has_senha"] is False
    assert criado.json()["has_senha_efetiva"] is False
    assert criado.json()["senha_origem"] is None
    rede_id = criado.json()["id"]
    url = f"/api/redes-sociais/{rede_id}/senha"

    vazia = await client.get(url)
    assert vazia.status_code == 200, vazia.text
    assert _senha(vazia.json()) == ("", None)
    assert vazia.headers["cache-control"] == "no-store"

    definida = await client.patch(f"/api/redes-sociais/{rede_id}", json={"senha": "abc"})
    assert definida.status_code == 200, definida.text
    _sem_senha(definida.json())
    assert definida.json()["has_senha"] is True
    assert definida.json()["has_senha_efetiva"] is True
    assert definida.json()["senha_origem"] == "conta"

    revelada = await client.get(url)
    assert revelada.status_code == 200, revelada.text
    assert _senha(revelada.json()) == ("abc", "conta")
    assert revelada.headers["cache-control"] == "no-store"

    # PATCH de outro campo (chave `senha` ausente) preserva a senha.
    obs = await client.patch(f"/api/redes-sociais/{rede_id}", json={"obs": "x"})
    assert obs.json()["has_senha"] is True
    assert _senha((await client.get(url)).json()) == ("abc", "conta")

    # Espaço faz parte da senha (sem strip).
    com_espaco = await client.patch(f"/api/redes-sociais/{rede_id}", json={"senha": " a b "})
    assert com_espaco.json()["has_senha"] is True
    assert _senha((await client.get(url)).json()) == (" a b ", "conta")

    limpa = await client.patch(f"/api/redes-sociais/{rede_id}", json={"senha": ""})
    assert limpa.status_code == 200, limpa.text
    assert limpa.json()["has_senha"] is False
    assert limpa.json()["senha_origem"] is None
    assert _senha((await client.get(url)).json()) == ("", None)

    # null também limpa (o modal manda senha:null pra "voltar a herdar").
    await client.patch(f"/api/redes-sociais/{rede_id}", json={"senha": "abc"})
    nula = await client.patch(f"/api/redes-sociais/{rede_id}", json={"senha": None})
    assert nula.json()["has_senha"] is False
    assert _senha((await client.get(url)).json()) == ("", None)

    ghost = await client.get(f"/api/redes-sociais/{uuid.uuid4()}/senha")
    assert ghost.status_code == 404
    assert ghost.json()["detail"]["code"] == "rede_social_not_found"


# ======================================================================== PATCH


async def test_patch_marca_e_plataforma_null_sao_ignorados(client, db, make_user, auth_as):
    """marca_id/plataforma são NOT NULL: null no body = "não mexe" (o modal
    manda o objeto inteiro)."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post("/api/redes-sociais", json=_body(poofy, "tiktok", "poofy_tt"))
    rede_id = criado.json()["id"]

    r = await client.patch(
        f"/api/redes-sociais/{rede_id}",
        json={"marca_id": None, "plataforma": None, "obs": "mantém as chaves"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["marca_id"] == str(poofy.id)
    assert r.json()["plataforma"] == "tiktok"
    assert r.json()["conta"] == "poofy_tt"
    assert r.json()["obs"] == "mantém as chaves"


async def test_patch_move_para_outra_marca(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")
    criado = await client.post("/api/redes-sociais", json=_body(poofy, conta="conta_x"))
    rede_id = criado.json()["id"]

    movida = await client.patch(
        f"/api/redes-sociais/{rede_id}", json={"marca_id": str(locagil.id)}
    )

    assert movida.status_code == 200, movida.text
    assert movida.json()["marca_id"] == str(locagil.id)
    assert movida.json()["marca_nome"] == "Locagil"

    da_poofy = await client.get("/api/redes-sociais", params={"marca_id": str(poofy.id)})
    assert da_poofy.json() == []
    da_locagil = await client.get("/api/redes-sociais", params={"marca_id": str(locagil.id)})
    assert [x["id"] for x in da_locagil.json()] == [rede_id]

    grid = await client.get("/api/redes-sociais/grid")
    por_marca = {row["marca"]["nome"]: row["cells"]["instagram"] for row in grid.json()["rows"]}
    assert por_marca["Poofy"] == []
    assert [c["id"] for c in por_marca["Locagil"]] == [rede_id]


async def test_patch_marca_inexistente_404(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post("/api/redes-sociais", json=_body(poofy))
    rede_id = criado.json()["id"]

    r = await client.patch(f"/api/redes-sociais/{rede_id}", json={"marca_id": str(uuid.uuid4())})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "marca_not_found"

    # Plataforma fora do Literal e conta inexistente também não passam.
    plat = await client.patch(f"/api/redes-sociais/{rede_id}", json={"plataforma": "orkut"})
    assert plat.status_code == 422
    ghost = await client.patch(f"/api/redes-sociais/{uuid.uuid4()}", json={"obs": "x"})
    assert ghost.status_code == 404
    assert ghost.json()["detail"]["code"] == "rede_social_not_found"

    atual = await client.get(f"/api/redes-sociais/{rede_id}")
    assert atual.json()["marca_id"] == str(poofy.id)


# ======================================================================= DELETE


async def test_delete_remove_e_get_404(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post("/api/redes-sociais", json=_body(poofy))
    rede_id = criado.json()["id"]

    r = await client.delete(f"/api/redes-sociais/{rede_id}")
    assert r.status_code == 204, r.text

    depois = await client.get(f"/api/redes-sociais/{rede_id}")
    assert depois.status_code == 404
    assert depois.json()["detail"]["code"] == "rede_social_not_found"

    de_novo = await client.delete(f"/api/redes-sociais/{rede_id}")
    assert de_novo.status_code == 404


async def test_apagar_marca_cascateia_nas_contas(client, db, make_user, auth_as):
    """FK ON DELETE CASCADE (sem relationship no ORM, de propósito): apagar a
    marca leva as contas junto; a outra marca fica intacta."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    locagil = await _seed_marca(db, "Locagil")
    ids_poofy = [
        (await client.post("/api/redes-sociais", json=_body(poofy, plat, conta))).json()["id"]
        for plat, conta in (("instagram", "poofy"), ("tiktok", None))
    ]
    id_locagil = (await client.post("/api/redes-sociais", json=_body(locagil, conta="lg"))).json()[
        "id"
    ]

    await db.delete(poofy)
    await db.commit()

    for rede_id in ids_poofy:
        r = await client.get(f"/api/redes-sociais/{rede_id}")
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["code"] == "rede_social_not_found"

    lista = await client.get("/api/redes-sociais")
    assert [x["id"] for x in lista.json()] == [id_locagil]

    grid = await client.get("/api/redes-sociais/grid")
    assert [row["marca"]["nome"] for row in grid.json()["rows"]] == ["Locagil"]


async def test_patch_verificacao_status_e_ativo_null_sao_ignorados(
    client, db, make_user, auth_as
):
    """Correção da revisão (15/09): null em coluna NOT NULL = "não mexe" —
    antes a NotNullViolation virava 409 rede_social_conta_conflict. Na v3 a
    coluna é `verificacao_status` (o bool `verificado` saiu)."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    m = await client.post("/api/marcas", json={"nome": "Nula RS"})
    r = await client.post(
        "/api/redes-sociais",
        json={
            "marca_id": m.json()["id"],
            "plataforma": "tiktok",
            "conta": "nula_rs",
            "verificacao_status": "verificado",
        },
    )
    assert r.status_code == 201, r.text
    r2 = await client.patch(
        f"/api/redes-sociais/{r.json()['id']}",
        json={"verificacao_status": None, "ativo": None, "obs": "ok"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["verificacao_status"] == "verificado"
    assert r2.json()["ativo"] is True
    assert r2.json()["obs"] == "ok"


# ======================================= v3 (15/09/2026): linha da marca


def _perms_marcas() -> dict:
    return {"marcas": {"view": True, "edit": True, "delete": True}}


async def test_patch_marca_social_edita_fone_usuario_senha_e_verificacao(
    client, db, make_user, auth_as
):
    """PATCH /marca/{id} (MarcaSocialPatch): sac_fone só dígitos, sac_email
    minúsculo, sac_senha com a semântica ausente/""/null/texto, verificação
    do Zap + obs, função/tipo/obs. Resposta = MarcaRef (sem login/e-mail do
    registro). A senha volta só no GET /marca/{id}/sac-senha (origem
    'marca', sem cache)."""
    await _admin(make_user, auth_as)
    m = await _seed_marca(
        db, "Poofy", usuario="login-inpi", email="inpi@poofy.com", dominio="poofy.com"
    )
    url = f"/api/redes-sociais/marca/{m.id}"

    r = await client.patch(
        url,
        json={
            "sac_fone": "(11) 98351-7003",
            "sac_email": "  SAC@Poofy.COM ",
            "sac_senha": "senha-fake-marca",
            "whatsapp_verificacao_status": "em_andamento",
            "whatsapp_verificacao_obs": "  protocolo 42 ",
            "funcao": " prc ",
            "tipo": "cel",
            "obs": "  obs da marca ",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == _CAMPOS_MARCA_REF
    assert body["id"] == str(m.id)
    assert body["sac_fone"] == "11983517003"
    assert body["sac_email"] == "sac@poofy.com"
    assert body["has_sac_senha"] is True
    assert body["whatsapp_verificacao_status"] == "em_andamento"
    assert body["whatsapp_verificacao_obs"] == "protocolo 42"
    assert body["funcao"] == "prc"
    assert body["tipo"] == "cel"
    assert body["obs"] == "obs da marca"

    senha = await client.get(f"{url}/sac-senha")
    assert senha.status_code == 200, senha.text
    assert _senha(senha.json()) == ("senha-fake-marca", "marca")
    assert senha.headers["cache-control"] == "no-store"

    # Chave ausente mantém a senha; "" e null limpam.
    so_obs = await client.patch(url, json={"obs": "x"})
    assert so_obs.json()["has_sac_senha"] is True
    assert so_obs.json()["sac_fone"] == "11983517003"
    limpa = await client.patch(url, json={"sac_senha": ""})
    assert limpa.status_code == 200, limpa.text
    assert limpa.json()["has_sac_senha"] is False
    vazia = await client.get(f"{url}/sac-senha")
    assert _senha(vazia.json()) == ("", None)
    assert vazia.headers["cache-control"] == "no-store"
    await client.patch(url, json={"sac_senha": " outra "})
    assert _senha((await client.get(f"{url}/sac-senha")).json()) == (" outra ", "marca")
    nula = await client.patch(url, json={"sac_senha": None})
    assert nula.json()["has_sac_senha"] is False

    # Vazio limpa fone/e-mail/obs; inválidos → 422 (nada gravado).
    limpo = await client.patch(url, json={"sac_fone": "", "sac_email": "   ", "obs": ""})
    assert limpo.status_code == 200, limpo.text
    assert limpo.json()["sac_fone"] is None
    assert limpo.json()["sac_email"] is None
    assert limpo.json()["obs"] is None
    for ruim in (
        {"sac_email": "sem-arroba"},
        {"sac_fone": "1" * 21},
        {"whatsapp_verificacao_status": "aprovado"},
        {"whatsapp_verificacao_status": True},
    ):
        bad = await client.patch(url, json=ruim)
        assert bad.status_code == 422, (ruim, bad.text)
    # null no status = "não mexe" (coluna NOT NULL).
    nulo = await client.patch(url, json={"whatsapp_verificacao_status": None, "tipo": "y"})
    assert nulo.status_code == 200, nulo.text
    assert nulo.json()["whatsapp_verificacao_status"] == "em_andamento"
    assert nulo.json()["tipo"] == "y"
    for st in ("verificado", "recusado", "nao_solicitado"):
        assert (await client.patch(url, json={"whatsapp_verificacao_status": st})).json()[
            "whatsapp_verificacao_status"
        ] == st

    # O registro INPI não foi tocado; a aba Marcas lê o resultado.
    await db.refresh(m)
    assert m.usuario == "login-inpi"
    assert m.email == "inpi@poofy.com"
    assert m.dominio == "poofy.com"
    marcas = await client.get(f"/api/marcas/{m.id}")
    assert marcas.json()["whatsapp_verificacao_status"] == "nao_solicitado"
    assert marcas.json()["has_sac_senha"] is False
    assert marcas.json()["tipo"] == "y"


async def test_patch_marca_social_ignora_campos_da_aba_marcas(client, db, make_user, auth_as):
    """nome/slug/usuario/email/senha/site/company_id/ativo são da aba Marcas
    (MarcaPatch): aqui chaves extras são descartadas (pydantic) e a linha
    fica igual; `has_sac_senha`/`has_logo` são só leitura."""
    await _admin(make_user, auth_as)
    m = await _seed_marca(
        db,
        "Poofy",
        usuario="login-inpi",
        email="inpi@poofy.com",
        site="https://poofy.com",
        senha_enc=encrypt("senha-fake-registro"),
        sac_senha_enc=encrypt("senha-fake-marca"),
    )
    enc_registro, enc_marca = m.senha_enc, m.sac_senha_enc

    r = await client.patch(
        f"/api/redes-sociais/marca/{m.id}",
        json={
            "nome": "Outra",
            "slug": "outra",
            "usuario": "hacker",
            "email": "x@y.com",
            "senha": "",
            "site": "https://x.com",
            "company_id": None,
            "ativo": False,
            "has_sac_senha": False,
            "has_logo": True,
            "funcao": "prc",
        },
    )

    assert r.status_code == 200, r.text
    assert r.json()["nome"] == "Poofy"
    assert r.json()["slug"] == "poofy"
    assert r.json()["ativo"] is True
    assert r.json()["has_sac_senha"] is True
    assert r.json()["has_logo"] is False
    assert r.json()["funcao"] == "prc"
    await db.refresh(m)
    assert m.usuario == "login-inpi"
    assert m.email == "inpi@poofy.com"
    assert m.site == "https://poofy.com"
    assert m.senha_enc == enc_registro
    assert m.sac_senha_enc == enc_marca
    assert m.ativo is True
    assert m.funcao == "prc"


async def test_patch_marca_social_404_e_permissoes(client, db, make_user, auth_as):
    """Gate `redes_sociais:edit`: view-only → 403 (action edit); quem tem SÓ
    `marcas:edit` também → 403 (resource redes_sociais) — o dono da senha
    das redes/SAC é esta aba. sac-senha idem. Marca inexistente → 404."""
    admin = await _admin(make_user, auth_as)
    m = await _seed_marca(db, "Poofy", sac_senha_enc=encrypt("senha-fake-marca"))
    url = f"/api/redes-sociais/marca/{m.id}"
    ghost = f"/api/redes-sociais/marca/{uuid.uuid4()}"

    r = await client.patch(ghost, json={"obs": "x"})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "marca_not_found"
    r = await client.get(f"{ghost}/sac-senha")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "marca_not_found"

    viewer = await make_user(permissions=_perms(view=True, edit=False, delete=False))
    auth_as(viewer)
    patch = await client.patch(url, json={"sac_fone": "1"})
    assert patch.status_code == 403
    assert patch.json()["detail"] == {
        "code": "forbidden",
        "resource": "redes_sociais",
        "action": "edit",
    }
    senha = await client.get(f"{url}/sac-senha")
    assert senha.status_code == 403
    assert "senha" not in senha.json()["detail"]

    so_marcas = await make_user(permissions=_perms_marcas())
    auth_as(so_marcas)
    patch = await client.patch(url, json={"sac_fone": "1"})
    assert patch.status_code == 403
    assert patch.json()["detail"]["resource"] == "redes_sociais"
    assert (await client.get(f"{url}/sac-senha")).status_code == 403
    # Pela aba Marcas (que ele edita) não dá: não existe a rota, e o PATCH
    # de lá ignora sac_*.
    assert (await client.get(f"/api/marcas/{m.id}/sac-senha")).status_code == 404
    pela_marca = await client.patch(f"/api/marcas/{m.id}", json={"sac_fone": "1"})
    assert pela_marca.status_code == 200, pela_marca.text
    assert pela_marca.json()["sac_fone"] is None

    editor = await make_user(permissions=_perms(view=True, edit=True, delete=False))
    auth_as(editor)
    ok = await client.patch(url, json={"sac_fone": "11983517003"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["sac_fone"] == "11983517003"
    assert _senha((await client.get(f"{url}/sac-senha")).json()) == ("senha-fake-marca", "marca")

    auth_as(admin)
    assert (await client.get(f"/api/marcas/{m.id}")).json()["sac_fone"] == "11983517003"


# ================================================================ herança


async def test_out_resolve_email_fone_e_senha_efetivos_da_marca(client, db, make_user, auth_as):
    """Como na planilha: uma credencial por marca (sac_*), compartilhada
    pelas contas; a conta só guarda o que DIFERE. Os `*_efetivo` resolvem:
    próprio → origem 'conta'; herdado → 'marca'; nada → null. GET /{id}/senha
    devolve a EFETIVA com a origem; trocar o sac_* da marca reflete nas
    contas que herdam (lista e grid)."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(
        db,
        "Poofy",
        sac_fone="11983517003",
        sac_email="sac@poofy.com",
        sac_senha_enc=encrypt("senha-fake-marca"),
    )
    locagil = await _seed_marca(db, "Locagil")

    herda = (
        await client.post("/api/redes-sociais", json=_body(poofy, "instagram", "poofy"))
    ).json()
    assert herda["email"] is None
    assert herda["fone"] is None
    assert herda["has_senha"] is False
    assert herda["email_efetivo"] == "sac@poofy.com"
    assert herda["fone_efetivo"] == "11983517003"
    assert herda["has_senha_efetiva"] is True
    assert herda["senha_origem"] == "marca"

    propria = (
        await client.post(
            "/api/redes-sociais",
            json=_body(
                poofy,
                "tiktok",
                "poofy_tt",
                email="tt@poofy.com",
                fone="11900000000",
                senha="senha-fake-conta",
            ),
        )
    ).json()
    assert propria["email"] == "tt@poofy.com"
    assert propria["email_efetivo"] == "tt@poofy.com"
    assert propria["fone_efetivo"] == "11900000000"
    assert propria["has_senha"] is True
    assert propria["has_senha_efetiva"] is True
    assert propria["senha_origem"] == "conta"

    nada = (
        await client.post("/api/redes-sociais", json=_body(locagil, "instagram", "locagil"))
    ).json()
    assert nada["email_efetivo"] is None
    assert nada["fone_efetivo"] is None
    assert nada["has_senha"] is False
    assert nada["has_senha_efetiva"] is False
    assert nada["senha_origem"] is None

    # GET /{id}/senha: a própria, senão a da marca, senão "".
    for rede, esperado in (
        (herda, ("senha-fake-marca", "marca")),
        (propria, ("senha-fake-conta", "conta")),
        (nada, ("", None)),
    ):
        r = await client.get(f"/api/redes-sociais/{rede['id']}/senha")
        assert r.status_code == 200, r.text
        assert _senha(r.json()) == esperado
        assert r.headers["cache-control"] == "no-store"

    # Conta que "volta a herdar" (senha null) passa a revelar a da marca.
    volta = await client.patch(f"/api/redes-sociais/{propria['id']}", json={"senha": None})
    assert volta.status_code == 200, volta.text
    assert volta.json()["has_senha"] is False
    assert volta.json()["has_senha_efetiva"] is True
    assert volta.json()["senha_origem"] == "marca"
    assert _senha((await client.get(f"/api/redes-sociais/{propria['id']}/senha")).json()) == (
        "senha-fake-marca",
        "marca",
    )
    # E-mail próprio vazio também volta a herdar.
    sem_email = await client.patch(f"/api/redes-sociais/{propria['id']}", json={"email": ""})
    assert sem_email.json()["email"] is None
    assert sem_email.json()["email_efetivo"] == "sac@poofy.com"

    # Trocar o sac_* da marca reflete nas contas que herdam.
    troca = await client.patch(
        f"/api/redes-sociais/marca/{poofy.id}",
        json={"sac_email": "novo@poofy.com", "sac_senha": ""},
    )
    assert troca.status_code == 200, troca.text
    lista = await client.get("/api/redes-sociais", params={"marca_id": str(poofy.id)})
    por_conta = {x["conta"]: x for x in lista.json()}
    assert por_conta["poofy"]["email_efetivo"] == "novo@poofy.com"
    assert por_conta["poofy"]["fone_efetivo"] == "11983517003"
    assert por_conta["poofy"]["has_senha_efetiva"] is False
    assert por_conta["poofy"]["senha_origem"] is None
    assert por_conta["poofy_tt"]["email_efetivo"] == "novo@poofy.com"
    assert por_conta["poofy_tt"]["fone_efetivo"] == "11900000000"
    assert por_conta["poofy_tt"]["senha_origem"] is None
    herda_senha = await client.get(f"/api/redes-sociais/{herda['id']}/senha")
    assert _senha(herda_senha.json()) == ("", None)

    grid = await client.get("/api/redes-sociais/grid")
    row = next(r for r in grid.json()["rows"] if r["marca"]["nome"] == "Poofy")
    assert row["marca"]["sac_email"] == "novo@poofy.com"
    assert row["marca"]["has_sac_senha"] is False
    assert row["cells"]["instagram"][0]["email_efetivo"] == "novo@poofy.com"
    assert row["cells"]["instagram"][0]["senha_origem"] is None
    detalhe = await client.get(f"/api/redes-sociais/{herda['id']}")
    assert detalhe.json()["email_efetivo"] == "novo@poofy.com"


# ============================================================ verificação


async def test_verificacao_status_default_invalido_e_obs(client, db, make_user, auth_as):
    """`verificado` (bool) SAIU; entra `verificacao_status` (enum, default
    nao_solicitado) + `verificacao_obs` (protocolo/etapa) — o DaVinci só
    registra o andamento do selo Meta Verified. Cliente velho mandando
    `verificado` não quebra (chave ignorada) nem muda nada."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")

    padrao = await client.post("/api/redes-sociais", json=_body(poofy))
    assert padrao.status_code == 201, padrao.text
    assert padrao.json()["verificacao_status"] == "nao_solicitado"
    assert padrao.json()["verificacao_obs"] is None
    assert "verificado" not in padrao.json()
    rede_id = padrao.json()["id"]

    velho = await client.post(
        "/api/redes-sociais", json=_body(poofy, "tiktok", "poofy_tt", verificado=True)
    )
    assert velho.status_code == 201, velho.text
    assert velho.json()["verificacao_status"] == "nao_solicitado"

    for ruim in ("aprovado", "sim", "", True, 1):
        r = await client.post(
            "/api/redes-sociais", json=_body(poofy, "youtube", "poofy_yt", verificacao_status=ruim)
        )
        assert r.status_code == 422, (ruim, r.text)
        r = await client.patch(f"/api/redes-sociais/{rede_id}", json={"verificacao_status": ruim})
        assert r.status_code == 422, (ruim, r.text)
    assert (await client.get(f"/api/redes-sociais/{rede_id}")).json()["verificacao_status"] == (
        "nao_solicitado"
    )

    for st in ("em_andamento", "verificado", "recusado", "nao_solicitado"):
        r = await client.patch(f"/api/redes-sociais/{rede_id}", json={"verificacao_status": st})
        assert r.status_code == 200, r.text
        assert r.json()["verificacao_status"] == st

    obs = await client.patch(
        f"/api/redes-sociais/{rede_id}", json={"verificacao_obs": "  protocolo 1 "}
    )
    assert obs.json()["verificacao_obs"] == "protocolo 1"
    vazia = await client.patch(f"/api/redes-sociais/{rede_id}", json={"verificacao_obs": ""})
    assert vazia.json()["verificacao_obs"] is None

    # Lista e grid carregam o status (é o que desenha o selo/pontinho).
    await client.patch(f"/api/redes-sociais/{rede_id}", json={"verificacao_status": "verificado"})
    lista = await client.get("/api/redes-sociais", params={"plataforma": "instagram"})
    assert [x["verificacao_status"] for x in lista.json()] == ["verificado"]
    grid = await client.get("/api/redes-sociais/grid")
    assert grid.json()["rows"][0]["cells"]["instagram"][0]["verificacao_status"] == "verificado"


# ================================================================== grid v3


async def test_grid_plataformas_sao_as_5_da_planilha_e_marca_ref_completo(
    client, db, make_user, auth_as
):
    """Só instagram, facebook, twitter, tiktok, youtube — na ordem da planilha
    (kwai/pinterest/linkedin/threads saíram na v3; POST com elas → 422).
    MarcaRef traz a linha da marca da aba r.social (fone/usuário/
    has_sac_senha/verificação do Zap/função/tipo/obs/has_logo) e NUNCA o
    login/e-mail/domínio do registro nem senha nem os bytes do logo."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(
        db,
        "Poofy",
        usuario="login-inpi",
        email="inpi@poofy.com",
        dominio="poofy.com",
        dominio_br="poofy.com.br",
        senha_enc=encrypt("senha-fake-registro"),
        sac_fone="11983517003",
        sac_email="sac@poofy.com",
        sac_senha_enc=encrypt("senha-fake-marca"),
        whatsapp_verificacao_status="verificado",
        whatsapp_verificacao_obs="selo ok",
        funcao="prc",
        tipo="cel",
        obs="obs da marca",
        logo_mime="image/png",
        logo=b"\x89PNG\r\n\x1a\n" + b"x" * 8,
    )

    g = await client.get("/api/redes-sociais/grid")

    assert g.status_code == 200, g.text
    body = g.json()
    assert body["plataformas"] == PLATAFORMAS_PLANILHA
    assert len(body["plataformas"]) == 5
    assert list(REDES_SOCIAIS_PLATAFORMAS) == PLATAFORMAS_PLANILHA
    (row,) = body["rows"]
    assert set(row["marca"]) == _CAMPOS_MARCA_REF
    for chave in _CAMPOS_SO_DA_ABA_MARCAS:
        assert chave not in row["marca"], chave
    assert row["marca"]["id"] == str(poofy.id)
    assert row["marca"]["sac_fone"] == "11983517003"
    assert row["marca"]["sac_email"] == "sac@poofy.com"
    assert row["marca"]["has_sac_senha"] is True
    assert row["marca"]["whatsapp_verificacao_status"] == "verificado"
    assert row["marca"]["whatsapp_verificacao_obs"] == "selo ok"
    assert row["marca"]["funcao"] == "prc"
    assert row["marca"]["tipo"] == "cel"
    assert row["marca"]["obs"] == "obs da marca"
    assert row["marca"]["has_logo"] is True
    assert list(row["cells"]) == PLATAFORMAS_PLANILHA

    for plat in ("kwai", "pinterest", "linkedin", "threads", "whatsapp", "x"):
        r = await client.post("/api/redes-sociais", json=_body(poofy, plat, "poofy"))
        assert r.status_code == 422, (plat, r.text)
    assert (await client.get("/api/redes-sociais")).json() == []


# ─── perfil do AdsPower (migration 0309) ───────────────────────────────
#
# O TikTok não tem API pra nós (o app foi recusado nas duas auditorias), então
# quem publica é o navegador logado no AdsPower, na máquina do Eduardo. A
# conta guarda QUAL perfil abrir. É o campo mais perigoso desta aba: apontar
# pro perfil errado não dá erro nenhum — o vídeo só sai na conta de outra marca.


async def test_adspower_grava_com_trim_e_vazio_limpa(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post("/api/redes-sociais", json=_body(poofy, plataforma="tiktok"))
    rede_id = criado.json()["id"]
    # Conta nova nasce sem perfil: o executor não publica sozinho até alguém
    # preencher conscientemente.
    assert criado.json()["adspower_user_id"] is None

    salvo = await client.patch(
        f"/api/redes-sociais/{rede_id}", json={"adspower_user_id": "  k1dohvrh  "}
    )
    assert salvo.status_code == 200, salvo.text
    assert salvo.json()["adspower_user_id"] == "k1dohvrh"

    # Vazio LIMPA — é como se desliga o executor numa conta sem apagá-la.
    limpo = await client.patch(f"/api/redes-sociais/{rede_id}", json={"adspower_user_id": "  "})
    assert limpo.json()["adspower_user_id"] is None


async def test_adspower_cai_quando_a_linha_muda_de_conta(client, db, make_user, auth_as):
    """Trocar o @ (ou a marca) faz a linha apontar pra OUTRA conta — e o perfil
    do AdsPower continua sendo o navegador logado na anterior. Mantê-lo é o
    caminho direto pro vídeo de uma marca sair no perfil de outra, sem nenhum
    erro no meio. Mesma regra que já derruba o token."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post(
        "/api/redes-sociais", json=_body(poofy, plataforma="tiktok", conta="poofy_brasil")
    )
    rede_id = criado.json()["id"]
    await client.patch(f"/api/redes-sociais/{rede_id}", json={"adspower_user_id": "k1dohvrh"})

    trocada = await client.patch(f"/api/redes-sociais/{rede_id}", json={"conta": "outra_conta"})

    assert trocada.status_code == 200, trocada.text
    assert trocada.json()["conta"] == "outra_conta"
    assert trocada.json()["adspower_user_id"] is None, "o perfil da conta antiga ficou pra trás"


async def test_adspower_novo_no_mesmo_patch_da_troca_vale(client, db, make_user, auth_as):
    """Quem troca o @ E manda o perfil novo na mesma chamada sabe o que está
    fazendo: o valor enviado ganha da limpeza automática."""
    await _admin(make_user, auth_as)
    poofy = await _seed_marca(db, "Poofy")
    criado = await client.post(
        "/api/redes-sociais", json=_body(poofy, plataforma="tiktok", conta="poofy_brasil")
    )
    rede_id = criado.json()["id"]
    await client.patch(f"/api/redes-sociais/{rede_id}", json={"adspower_user_id": "antigo01"})

    trocada = await client.patch(
        f"/api/redes-sociais/{rede_id}",
        json={"conta": "uranyx_oficial", "adspower_user_id": "novo02"},
    )

    assert trocada.json()["adspower_user_id"] == "novo02"
