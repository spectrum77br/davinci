"""Sistema › Histórico: o que as pessoas mudam fica registrado; robô não.

Eduardo (25/09/2026): "uma lista de histórico do que está sendo mudado no
davinci, por exemplo alterou a tabela de preços". Só pessoas; só ele vê —
"invisível para todas as outras pessoas, ATÉ PARA ADMIN".

Estes testes entram pelo login DE VERDADE (cookie de sessão), não pelo
`auth_as`: é o caminho do cookie que diz ao Histórico quem é a pessoa.
"""

# ruff: noqa: S105, S106  (senhas de teste, nada real)

import pytest
from sqlalchemy import select, text

from app.config import get_settings
from app.historico.contexto import Ator, abrir, ator_atual, fechar
from app.historico.middleware import HistoricoMiddleware
from app.models import (
    HistoricoAcesso,
    HistoricoAlteracao,
    HistoricoEvento,
    PricingAccount,
    PricingProduct,
    Segment,
    UserRole,
)
from app.models.enums import PricingPlatform
from app.security.jwt import issue_session_token


def _logar(client, user) -> None:
    token, _, _ = issue_session_token(sub=user.open_id, role=user.role.value)
    client.cookies.set(get_settings().cookie_name, token)


async def _eventos(db) -> list[HistoricoEvento]:
    q = select(HistoricoEvento).order_by(HistoricoEvento.id)
    return (await db.execute(q.execution_options(populate_existing=True))).scalars().all()


async def _alteracoes(db, **filtro) -> list[HistoricoAlteracao]:
    q = select(HistoricoAlteracao).order_by(HistoricoAlteracao.id)
    for k, v in filtro.items():
        q = q.where(getattr(HistoricoAlteracao, k) == v)
    return (await db.execute(q.execution_options(populate_existing=True))).scalars().all()


async def _conta_e_produto(db, dono):
    """Conta e produto criados direto no banco (como um robô: não entram)."""
    seg = Segment(name="Celular", slug=f"celular-{dono.id.hex[:6]}")
    db.add(seg)
    await db.flush()
    conta = PricingAccount(user_id=dono.id, name="ML kia", platform=PricingPlatform.SHOPEE, segment_id=seg.id)
    prod = PricingProduct(user_id=dono.id, sku="dg053", name="Celular", segment_id=seg.id)
    db.add_all([conta, prod])
    await db.commit()
    return {"id": str(conta.id)}, {"id": str(prod.id)}


# --- captura ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mudar_preco_fica_com_quem_antes_e_depois(client, db, make_user):
    """O exemplo do Eduardo: alterou a Tabela de preços."""
    eu = await make_user(role=UserRole.ADMIN, email="heisenberg@davinci-test.com")
    eu_id = eu.id
    _logar(client, eu)
    a, p = await _conta_e_produto(db, eu)
    corpo = {"pricing_product_id": p["id"], "pricing_account_id": a["id"], "cell_status": "manual"}
    r = await client.put("/api/pricing/overrides", json={**corpo, "price_override": "1299.00"})
    assert r.status_code == 200
    r = await client.put(
        "/api/pricing/overrides",
        json={**corpo, "price_override": "1249.00"},
        headers={"referer": "https://app.hadken.com/pricing/tabela?dept=x"},
    )
    assert r.status_code == 200

    [mudou] = await _alteracoes(db, tabela="pricing_overrides", operacao="U")
    assert str(mudou.ator_id) == str(eu_id)
    assert mudou.antes["price_override"] == 1299.0
    assert mudou.depois["price_override"] == 1249.0
    assert "updated_at" not in mudou.depois  # carimbo de hora não é mudança

    ev = [e for e in await _eventos(db) if e.req_id == mudou.req_id]
    assert len(ev) == 1
    assert ev[0].ator_nome is not None
    assert ev[0].tela == "Tabela de preços"
    assert ev[0].pagina == "/pricing/tabela"
    assert ev[0].n_alteracoes == 1
    assert ev[0].corpo["price_override"] == "1249.00"


@pytest.mark.asyncio
async def test_apagar_preco_por_comando_direto_tambem_fica(client, db, make_user):
    """DELETE /overrides apaga com delete() em massa, sem carregar a linha: o
    ORM nunca veria. O gatilho vê, com o preço que existia."""
    eu = await make_user(role=UserRole.ADMIN)
    _logar(client, eu)
    a, p = await _conta_e_produto(db, eu)
    await client.put(
        "/api/pricing/overrides",
        json={"pricing_product_id": p["id"], "pricing_account_id": a["id"], "price_override": "99.90"},
    )
    r = await client.delete(
        f"/api/pricing/overrides?pricing_product_id={p['id']}&pricing_account_id={a['id']}"
    )
    assert r.status_code == 204
    [apagou] = await _alteracoes(db, tabela="pricing_overrides", operacao="D")
    assert apagou.antes["price_override"] == 99.9
    assert apagou.ident["pricing_product_id"] == p["id"]


@pytest.mark.asyncio
async def test_robo_nao_entra(db, make_user):
    """Sem pedido de pessoa (worker, cron, script) nada é gravado."""
    await make_user(role=UserRole.ADMIN)
    await db.execute(text("UPDATE users SET name = 'mudado pelo robô'"))
    await db.commit()
    assert await _alteracoes(db) == []


@pytest.mark.asyncio
async def test_leitura_nao_marca_nem_grava(client, db, make_user):
    eu = await make_user(role=UserRole.ADMIN)
    _logar(client, eu)
    await client.get("/api/pricing/accounts")
    await client.get("/api/auth/me")
    assert await _eventos(db) == []


@pytest.mark.asyncio
async def test_senha_nunca_entra(client, db, make_user):
    eu = await make_user(role=UserRole.ADMIN)
    _logar(client, eu)
    r = await client.post(
        "/api/pricing/store-info",
        json={"platform": "shopee", "account_name": "loja1", "password": "Segredo-Real-123"},
    )
    assert r.status_code in (200, 201), r.text
    loja = r.json()
    r = await client.patch(
        f"/api/pricing/store-info/{loja['id']}",
        json={"password": "Outra-Senha-456", "observation": "senha do aparelho: 4821"},
    )
    assert r.status_code == 200, r.text

    tudo = (
        await db.execute(text(
            "SELECT coalesce(string_agg(t::text, ' '), '') FROM ("
            " SELECT row_to_json(a) AS t FROM historico_alteracao a"
            " UNION ALL SELECT row_to_json(e) FROM historico_evento e) x"
        ))
    ).scalar_one()
    for segredo in ("Segredo-Real-123", "Outra-Senha-456", "4821"):
        assert segredo not in tudo, segredo
    [mudou] = await _alteracoes(db, tabela="store_info", operacao="U")
    assert mudou.antes["password_enc"] == {"_oculto": True}
    assert mudou.depois["password_enc"] == {"_oculto": True}
    assert mudou.depois["observation"] == "senha do aparelho: ***"


@pytest.mark.asyncio
async def test_commit_no_meio_continua_marcando(db, make_user):
    """Rota que comita duas vezes: a segunda transação também é da pessoa."""
    eu = await make_user(role=UserRole.ADMIN)
    ator = Ator(metodo="POST", caminho="/api/x", grava=True, user_id=eu.id)
    token = abrir(ator)
    try:
        from app.db import SessionLocal

        async with SessionLocal() as s:
            await s.execute(text("UPDATE users SET name = 'um' WHERE id = :i"), {"i": eu.id})
            await s.commit()
            await s.execute(text("UPDATE users SET name = 'dois' WHERE id = :i"), {"i": eu.id})
            await s.commit()
    finally:
        fechar(token)
    linhas = await _alteracoes(db, tabela="users")
    assert [x.depois["name"] for x in linhas] == ["um", "dois"]
    assert {x.req_id for x in linhas} == {ator.req_id}


@pytest.mark.asyncio
async def test_teto_por_acao(db, make_user):
    """Importar milhares de linhas não enche o disco: 500 detalhadas + 1 aviso."""
    eu = await make_user(role=UserRole.ADMIN)
    ator = Ator(metodo="POST", caminho="/api/x", grava=True, user_id=eu.id)
    token = abrir(ator)
    try:
        from app.db import SessionLocal

        async with SessionLocal() as s:
            await s.execute(text(
                "INSERT INTO segments (id, name, slug) SELECT gen_random_uuid(), 'seg ' || g, 'seg-' || g"
                " FROM generate_series(1, 520) g"
            ))
            await s.commit()
    finally:
        fechar(token)
    linhas = await _alteracoes(db, tabela="segments")
    assert len(linhas) == 501
    assert linhas[-1].operacao == "X"


@pytest.mark.asyncio
async def test_se_o_historico_falhar_a_mudanca_passa(db, make_user):
    """O Histórico nunca trava ninguém: com a marca estragada o gatilho só
    avisa (WARNING) e a mudança da pessoa é gravada normalmente."""
    eu = await make_user(role=UserRole.ADMIN)
    await db.execute(text("SELECT set_config('davinci.ator', 'nao-e-um-uuid', true)"))
    await db.execute(text("UPDATE users SET name = 'passou' WHERE id = :i"), {"i": eu.id})
    await db.commit()
    nome = (await db.execute(text("SELECT name FROM users WHERE id = :i"), {"i": eu.id})).scalar_one()
    assert nome == "passou"
    assert await _alteracoes(db) == []


@pytest.mark.asyncio
async def test_pedido_sem_mudanca_nao_vira_evento(client, db, make_user):
    eu = await make_user(role=UserRole.ADMIN)
    _logar(client, eu)
    await client.post("/api/email-assinaturas/preview", json={})
    assert await _eventos(db) == []


@pytest.mark.asyncio
async def test_recarregar_da_margem_nao_e_da_pessoa():
    """O recarregar automático da Margem roda o auto-hold (robô). Mesmo com a
    pessoa logada, o pedido não marca o banco em nome dela."""
    visto = {}

    async def app_falso(scope, receive, send):
        visto["grava"] = ator_atual().grava
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_msg):
        return None

    mw = HistoricoMiddleware(app_falso)
    for caminho, esperado in [
        ("/api/margens/marketplace/refresh", False),
        ("/api/margens/pedido/1/saldo", True),
    ]:
        await mw({"type": "http", "method": "POST", "path": caminho, "headers": []}, receive, send)
        assert visto["grava"] is esperado, caminho


# --- quem vê ------------------------------------------------------------------------


async def _liberar(db, user, gerente=False):
    db.add(HistoricoAcesso(user_id=user.id, pode_gerenciar=gerente))
    await db.commit()


@pytest.mark.asyncio
async def test_admin_fora_da_lista_nao_sabe_que_existe(client, db, make_user):
    outro_admin = await make_user(role=UserRole.ADMIN)
    _logar(client, outro_admin)
    inexistente = await client.get("/api/nao-existe-mesmo")
    for metodo, caminho in [
        ("GET", "/api/historico"),
        ("GET", "/api/historico/"),
        ("GET", "/api/historico/1"),
        ("GET", "/api/historico/acesso"),
        ("PUT", f"/api/historico/acesso/{outro_admin.id}"),
        ("POST", "/api/historico"),
        ("DELETE", "/api/historico/1"),
        ("GET", "/api/historico/qualquer/coisa"),
    ]:
        r = await client.request(metodo, caminho, json={"liberado": True})
        assert r.status_code == 404, (metodo, caminho, r.status_code)
        assert r.json() == inexistente.json(), (metodo, caminho)
    me = (await client.get("/api/auth/me")).json()
    assert "historico" not in me
    caminhos = (await client.get("/api/openapi.json")).json()["paths"]
    assert not [c for c in caminhos if c.startswith("/api/historico")]


@pytest.mark.asyncio
async def test_sem_login_tambem_404(client):
    assert (await client.get("/api/historico")).status_code == 404


@pytest.mark.asyncio
async def test_eduardo_ve_e_gerencia(client, db, make_user):
    eu = await make_user(role=UserRole.ADMIN)
    await _liberar(db, eu, gerente=True)
    _logar(client, eu)
    me = (await client.get("/api/auth/me")).json()
    assert me["historico"] is True
    a, p = await _conta_e_produto(db, eu)
    await client.put(
        "/api/pricing/overrides",
        json={"pricing_product_id": p["id"], "pricing_account_id": a["id"], "price_override": "10"},
    )
    r = await client.get("/api/historico")
    assert r.status_code == 200, r.text
    dados = r.json()
    assert dados["pode_gerenciar"] is True
    preco = [i for i in dados["items"] if i["alteracoes"] and i["alteracoes"][0]["tabela"] == "pricing_overrides"]
    assert preco, dados["items"]
    alt = preco[0]["alteracoes"][0]
    assert alt["entidade"] == "Preço da célula"
    assert alt["verbo"] == "criou"
    assert alt["item"] == "dg053 · ML kia"  # nomes resolvidos pelas chaves
    campos = {c["nome"]: c for c in alt["campos"]}
    assert campos["Preço manual"]["depois"] == "10"

    detalhe = (await client.get(f"/api/historico/{preco[0]['id']}")).json()
    assert detalhe["alteracoes"][0]["item"] == "dg053 · ML kia"


@pytest.mark.asyncio
async def test_liberar_um_admin(client, db, make_user):
    eu = await make_user(role=UserRole.ADMIN)
    joana = await make_user(role=UserRole.ADMIN)
    usuario_comum = await make_user(role=UserRole.USER)
    await _liberar(db, eu, gerente=True)
    _logar(client, eu)

    lista = (await client.get("/api/historico/acesso")).json()
    assert {x["id"] for x in lista} >= {str(eu.id), str(joana.id)}
    assert str(usuario_comum.id) not in {x["id"] for x in lista}  # só admin

    r = await client.put(f"/api/historico/acesso/{joana.id}", json={"liberado": True})
    assert r.status_code == 200
    assert (await client.put(f"/api/historico/acesso/{usuario_comum.id}", json={"liberado": True})).status_code == 400
    assert (await client.put(f"/api/historico/acesso/{eu.id}", json={"liberado": False})).status_code == 400

    # a liberação também fica no Histórico
    [lib] = await _alteracoes(db, tabela="historico_acesso", operacao="I")
    assert lib.ator_id == eu.id

    # Joana vê, mas não libera ninguém (nem sabe que dá)
    client.cookies.clear()
    _logar(client, joana)
    assert (await client.get("/api/historico")).status_code == 200
    assert (await client.get("/api/historico")).json()["pode_gerenciar"] is False
    assert (await client.get("/api/historico/acesso")).status_code == 404
    assert (await client.put(f"/api/historico/acesso/{usuario_comum.id}", json={"liberado": True})).status_code == 404
