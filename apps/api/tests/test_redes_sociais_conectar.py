"""Cadastros › Redes Sociais — conectar/desconectar a conta de publicação.

Eduardo, 15/09/2026: "um robô que fará a postagem desses vídeos do criativo
automaticamente". Pra o robô publicar, a conta precisa de uma CREDENCIAL, e é
por aqui que ela entra: o operador cola na tela o token gerado no Business
Suite e o `POST /api/redes-sociais/{id}/conectar` descobre sozinho o id
externo (as Páginas do portfólio e a conta do Instagram ligada a cada uma),
casa com o @ cadastrado e grava CIFRADO. O `DELETE` tira a credencial — o
robô para de publicar naquela conta na hora.

**Nada de rede aqui.** `app.services.marketing.meta_client` é fakado inteiro
(fixture `meta`, autouse): nenhum teste deste arquivo abre socket. A Graph
API de verdade é coberta por `test_meta_client.py` (respx). O dublê também
REGISTRA as chamadas — é assim que se prova que o gate de permissão barra
ANTES de o token sair do processo.

O que este arquivo insiste em provar, porque token vazado não tem desfazer:

  • o valor do token SOME depois do POST — não volta em resposta nenhuma
    (conectar, detalhe, listagem, grid), não fica em claro no banco (varredura
    do `row::text` da tabela, inclusive pelo hex do bytea) e não aparece nem
    no corpo do 422 quando a Meta recusa;
  • sem escolha possível (várias Páginas, nenhuma casando com o @) o endpoint
    NÃO GRAVA NADA e devolve a lista pro operador escolher — gravar "a
    primeira que apareceu" publicaria em nome da marca errada;
  • reconectar sobrescreve a MESMA linha (senão sobraria credencial órfã).

Tokens aqui são todos FAKE ("FAKE-TOKEN-XYZ-…"). Token real não entra em
teste, em log, em `:title`, em URL nem em localStorage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Marca, RedeSocial, RedeSocialToken, User, UserRole
from app.schemas.segments import _slugify
from app.security.cipher import decrypt_json
from app.services.marketing import meta_client

pytestmark = pytest.mark.asyncio

API = "/api/redes-sociais"

# Tokens de mentira. Se um destes textos aparecer em QUALQUER corpo de
# resposta — ou em claro no banco — a credencial de publicação vazou. Nenhum é
# prefixo do outro de propósito: a varredura do "sumiu o antigo" depois do
# reconectar precisa saber distinguir os dois.
SEGREDO_FAKE = "FAKE-TOKEN-XYZ-nunca-pode-vazar"
SEGREDO_FAKE_2 = "FAKE-TOKEN-XYZ-2-o-do-reconectar"
# Token da PÁGINA: segredo diferente do colado, porque ele é o que realmente
# publica. Se aparecer numa resposta, vazou o poder de postar na marca.
SEGREDO_PAGINA = "FAKE-PAGE-TOKEN-o-que-publica"

# `status` da credencial: "ok" | "expirado" | "revogado" (models/
# marketing_postagem.py). Constante porque o literal solto ao lado de uma
# chave "token_*" acende o S105 do ruff (falso positivo de senha no código).
STATUS_OK = "ok"
IG_ID = "17841400000000001"
CONTA = "poofy.oficial"


# ═══════════════════════════════════════════════════════════ dublê da Meta


class MetaFake:
    """Braço HTTP da Meta, fakado: devolve o que o teste mandar e anota o que
    recebeu. `contas` é o `GET /me/accounts` (Páginas + IG ligado); `direta` é
    o fallback da trilha "Instagram Login" (conta sem Página)."""

    def __init__(self) -> None:
        self.contas: list[dict[str, str | None]] = []
        self.direta: dict[str, str | None] | None = None
        self.validade: datetime | None = None
        self.erro: meta_client.MetaError | None = None
        self.chamadas: list[str] = []
        # Tokens que CHEGARAM ao cliente (só na memória do teste; nunca sai em
        # print/log) — serve pra provar que o token colado é o que vai pra Meta
        # e, no teste de permissão, que NADA saiu.
        self.recebeu: list[str] = []

    async def contas_do_token(self, token: str) -> list[dict[str, str | None]]:
        self.chamadas.append("contas_do_token")
        self.recebeu.append(token)
        if self.erro is not None:
            raise self.erro
        return [dict(c) for c in self.contas]

    async def conta_instagram_direta(self, token: str) -> dict[str, str | None] | None:
        self.chamadas.append("conta_instagram_direta")
        self.recebeu.append(token)
        return dict(self.direta) if self.direta else None

    async def validade_do_token(self, token: str):
        """`GET /debug_token` — o router grava a validade em claro pro cron de
        renovação achar o que vence sem decifrar nada."""
        self.chamadas.append("validade_do_token")
        self.recebeu.append(token)
        return self.validade


@pytest.fixture(autouse=True)
def meta(monkeypatch) -> MetaFake:
    """AUTOUSE: mesmo um teste que não fale da Meta fica sem rede. O router faz
    `from app.services.marketing import meta_client` e chama pelo módulo, então
    trocar o atributo do módulo basta."""
    fake = MetaFake()
    monkeypatch.setattr(meta_client, "contas_do_token", fake.contas_do_token)
    monkeypatch.setattr(meta_client, "conta_instagram_direta", fake.conta_instagram_direta)
    monkeypatch.setattr(meta_client, "validade_do_token", fake.validade_do_token)
    return fake


def _pagina(
    *,
    page_id: str,
    page_nome: str,
    ig_user_id: str | None = None,
    ig_username: str | None = None,
    page_token: str | None = None,
) -> dict[str, str | None]:
    return {
        "page_id": page_id,
        "page_nome": page_nome,
        "ig_user_id": ig_user_id,
        "ig_username": ig_username,
        # `GET /me/accounts?fields=…,access_token` devolve o token da Página
        # junto. É o que publica de fato — e o que nunca pode sair por API.
        "page_token": page_token,
    }


# ═══════════════════════════════════════════════════════════════ sementes


def _perms(*, view: bool = True, edit: bool = True, delete: bool = True) -> dict:
    return {"redes_sociais": {"view": view, "edit": edit, "delete": delete}}


async def _seed_marca(db: AsyncSession, nome: str = "Poofy") -> Marca:
    m = Marca(nome=nome, slug=_slugify(nome))
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _seed_conta(
    db: AsyncSession,
    marca: Marca,
    *,
    plataforma: str = "instagram",
    conta: str | None = CONTA,
) -> RedeSocial:
    r = RedeSocial(marca_id=marca.id, plataforma=plataforma, conta=conta)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _cenario(
    db: AsyncSession,
    make_user,
    auth_as,
    *,
    perms: dict | None = None,
    plataforma: str = "instagram",
    conta: str | None = CONTA,
) -> tuple[User, RedeSocial]:
    """Marca + conta + usuário logado. `perms=None` = admin (passa por tudo);
    qualquer dict = usuário comum com exatamente aquelas permissões."""
    if perms is None:
        user = await make_user(role=UserRole.ADMIN)
    else:
        user = await make_user(role=UserRole.USER, permissions=perms)
    auth_as(user)
    marca = await _seed_marca(db)
    rede = await _seed_conta(db, marca, plataforma=plataforma, conta=conta)
    return user, rede


# ══════════════════════════════════════════════════════════════ varreduras


async def _tokens(db: AsyncSession) -> list[RedeSocialToken]:
    """Linhas gravadas. `populate_existing` porque quem escreveu foi a sessão
    do APP (outra sessão): sem isso a identity map devolveria o objeto velho —
    e `expire_all()` aqui quebraria os objetos já carregados (o próximo
    `rede.id` viraria I/O fora do greenlet)."""
    stmt = select(RedeSocialToken).execution_options(populate_existing=True)
    return list((await db.execute(stmt)).scalars().all())


async def _dump_tokens(db: AsyncSession) -> str:
    """A tabela inteira como TEXTO (o bytea sai em hex) — é a varredura que
    prova "cifrado no banco", não só "a coluna é bytea"."""
    linhas = (
        await db.execute(text("SELECT t::text FROM redes_sociais_tokens AS t"))
    ).scalars().all()
    return "\n".join(linhas)


def _nao_esta_em_claro(dump: str, segredo: str) -> None:
    assert segredo not in dump
    # O bytea vira "\x<hex>" no ::text — plaintext escondido em hex também
    # seria vazamento.
    assert segredo.encode().hex() not in dump.lower()


def _sem_segredo(r) -> None:
    """Nenhuma resposta pode carregar o token: nem o valor, nem a coluna.
    `has_token`/`token_status` são permitidos de propósito (a tela precisa do
    ESTADO) — por isso a busca é pelo texto do token fake, não pela palavra
    "token"."""
    assert SEGREDO_FAKE not in r.text
    assert SEGREDO_FAKE_2 not in r.text
    assert SEGREDO_PAGINA not in r.text
    assert "token_enc" not in r.text
    assert "access_token" not in r.text
    assert "page_token" not in r.text


def _guardado(tok: RedeSocialToken) -> str:
    return decrypt_json(tok.token_enc)["access_token"]


# ═══════════════════════════════ (1) uma Página, o @ bate: conecta e cifra


async def test_uma_pagina_com_instagram_grava_token_cifrado(client, db, make_user, auth_as, meta):
    """O caso comum: um token por marca, uma Página, o @ da Página bate com o
    @ cadastrado. O operador só cola o token — quem acha o `ig_user_id` é o
    backend (ninguém caça id numérico no Business Suite)."""
    user, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000001", page_nome="Poofy Store", ig_user_id=IG_ID, ig_username=CONTA)
    ]

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["external_user_id"] == IG_ID
    assert body["external_username"] == CONTA
    # A resposta mostra o que o token enxerga (conferência visual) — sem o token.
    assert [c["page_id"] for c in body["contas"]] == ["10000001"]
    _sem_segredo(r)
    # O token colado é o que foi perguntado à Meta (não um recorte/normalizado).
    # Duas idas: descobrir as contas e perguntar a validade (`debug_token`).
    assert set(meta.recebeu) == {SEGREDO_FAKE}
    assert "validade_do_token" in meta.chamadas

    tok = (await _tokens(db))[0]
    assert tok.rede_social_id == rede.id
    assert tok.external_user_id == IG_ID
    assert tok.external_username == CONTA
    assert tok.status == STATUS_OK
    assert tok.last_error is None
    assert tok.connected_by == user.id
    assert tok.connected_at is not None and tok.last_ok_at is not None

    # Cifrado: decrypt_json devolve o token; em claro não está em lugar nenhum.
    assert _guardado(tok) == SEGREDO_FAKE
    assert SEGREDO_FAKE.encode() not in tok.token_enc
    _nao_esta_em_claro(await _dump_tokens(db), SEGREDO_FAKE)


# ═════════════════ (2) várias Páginas, nenhuma casa: o operador é quem escolhe


async def test_varias_paginas_sem_casar_o_arroba_nao_grava_e_pede_escolha(
    client, db, make_user, auth_as, meta
):
    """Token de portfólio grande: chutar qual conta é a da marca publicaria
    vídeo na conta errada — e publicar não tem desfazer. Então devolve ok=false
    com a lista e NÃO grava."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="1", page_nome="Mala Viagem", ig_user_id="111", ig_username="mala.viagem"),
        _pagina(page_id="2", page_nome="Eletro Top", ig_user_id="222", ig_username="eletro.top"),
        _pagina(page_id="3", page_nome="Kit Celular", ig_user_id="333", ig_username="kit.celular"),
    ]

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["external_user_id"] is None
    assert [c["ig_user_id"] for c in body["contas"]] == ["111", "222", "333"]
    assert [c["ig_username"] for c in body["contas"]] == [
        "mala.viagem",
        "eletro.top",
        "kit.celular",
    ]
    _sem_segredo(r)
    assert await _tokens(db) == []


async def test_reenviar_com_external_user_id_escolhido_grava(client, db, make_user, auth_as, meta):
    """Segunda tentativa: a tela devolve o `external_user_id` que o operador
    marcou na lista e aí sim grava. (O `external_username` fica em branco — o
    backend só recebeu o id de volta.)"""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="1", page_nome="Mala Viagem", ig_user_id="111", ig_username="mala.viagem"),
        _pagina(page_id="2", page_nome="Eletro Top", ig_user_id="222", ig_username="eletro.top"),
    ]

    r = await client.post(
        f"{API}/{rede.id}/conectar",
        json={"access_token": SEGREDO_FAKE, "external_user_id": "222"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["external_user_id"] == "222"
    _sem_segredo(r)

    tok = (await _tokens(db))[0]
    assert tok.external_user_id == "222"
    assert tok.status == STATUS_OK
    assert _guardado(tok) == SEGREDO_FAKE


# ══════════════════════════ (3) conta de Instagram SEM Página: fallback direto


async def test_conta_sem_pagina_cai_no_fallback_instagram_direta(
    client, db, make_user, auth_as, meta
):
    """Trilha "Instagram Login": a conta não está ligada a nenhuma Página, o
    token é da PRÓPRIA conta. `/me/accounts` volta vazio e o `GET /me` do
    graph.instagram.com identifica."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = []
    meta.direta = {"ig_user_id": "17841400000000003", "ig_username": CONTA}

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["external_user_id"] == "17841400000000003"
    assert body["external_username"] == CONTA
    # Só tenta a trilha direta DEPOIS de a lista de Páginas vir vazia.
    assert meta.chamadas[:2] == ["contas_do_token", "conta_instagram_direta"]
    # A conta devolvida pra tela não tem Página — é a própria conta do IG.
    assert body["contas"] == [
        {
            "page_id": None,
            "page_nome": None,
            "ig_user_id": "17841400000000003",
            "ig_username": CONTA,
        }
    ]
    _sem_segredo(r)

    tok = (await _tokens(db))[0]
    assert tok.external_user_id == "17841400000000003"
    assert _guardado(tok) == SEGREDO_FAKE


# ═══════════════════════════════════ (4) a Meta recusa o token → 422, sem gravar


async def test_meta_recusa_o_token_vira_422_e_nao_grava(client, db, make_user, auth_as, meta):
    """Token expirado/inválido não pode virar linha "ok" no banco — o robô
    tentaria publicar com ele para sempre. E a mensagem da Meta que volta pra
    tela já vem redigida pelo cliente (o token não entra nela)."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.erro = meta_client.MetaError(
        "token inválido ou expirado — reconecte a conta (code=190)", code=190
    )

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "token_recusado_pela_meta"
    assert "reconecte a conta" in detail["erro"]
    _sem_segredo(r)
    assert await _tokens(db) == []


# ═════════════════════════════ (5) reconectar sobrescreve a MESMA linha


async def test_reconectar_sobrescreve_a_mesma_linha(client, db, make_user, auth_as, meta):
    """Uma credencial por conta (`uq_redes_sociais_tokens_rede_social_id`): a
    segunda conexão TROCA o token da linha existente. Duas linhas significaria
    um token órfão ainda válido publicando em nome da marca."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000001", page_nome="Poofy Store", ig_user_id=IG_ID, ig_username=CONTA)
    ]

    r1 = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})
    assert r1.status_code == 200, r1.text
    antes = (await _tokens(db))[0]
    id_antes, criado_em = antes.id, antes.created_at

    r2 = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE_2})
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is True

    linhas = await _tokens(db)
    assert len(linhas) == 1
    assert linhas[0].id == id_antes
    assert linhas[0].created_at == criado_em
    assert _guardado(linhas[0]) == SEGREDO_FAKE_2

    # O token antigo sumiu do banco — não sobra rastro do que foi substituído.
    dump = await _dump_tokens(db)
    _nao_esta_em_claro(dump, SEGREDO_FAKE)
    _nao_esta_em_claro(dump, SEGREDO_FAKE_2)


# ══════════════════════════════════ (6) DELETE: a conta volta a "sem token"


async def test_delete_remove_a_credencial(client, db, make_user, auth_as, meta):
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000001", page_nome="Poofy Store", ig_user_id=IG_ID, ig_username=CONTA)
    ]
    assert (
        await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})
    ).status_code == 200
    assert len(await _tokens(db)) == 1

    r = await client.delete(f"{API}/{rede.id}/conectar")

    assert r.status_code == 204, r.text
    assert await _tokens(db) == []
    detalhe = await client.get(f"{API}/{rede.id}")
    assert detalhe.status_code == 200
    assert detalhe.json()["has_token"] is False
    assert detalhe.json()["token_conta_externa"] is None
    _sem_segredo(detalhe)

    # Idempotente: desconectar de novo não é erro (a tela pode repetir o clique).
    assert (await client.delete(f"{API}/{rede.id}/conectar")).status_code == 204


async def test_conectar_e_desconectar_rede_inexistente_dao_404(client, db, make_user, auth_as):
    await _cenario(db, make_user, auth_as)
    sumida = uuid.uuid4()

    r = await client.post(f"{API}/{sumida}/conectar", json={"access_token": SEGREDO_FAKE})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "rede_social_not_found"
    assert (await client.delete(f"{API}/{sumida}/conectar")).status_code == 404


# ═══════════════════════════════════════════════════ (7) permissões (edit)


async def test_view_nao_conecta_nem_desconecta(client, db, make_user, auth_as, meta):
    """Conectar é `redes_sociais:edit`: quem só enxerga a aba não pode trocar a
    credencial de publicação da marca."""
    _, rede = await _cenario(
        db, make_user, auth_as, perms=_perms(view=True, edit=False, delete=False)
    )

    post = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})
    delete = await client.delete(f"{API}/{rede.id}/conectar")

    assert post.status_code == 403, post.text
    assert post.json()["detail"] == {
        "code": "forbidden",
        "resource": "redes_sociais",
        "action": "edit",
    }
    assert delete.status_code == 403, delete.text
    # O gate roda ANTES do cliente da Meta: o token nem saiu do processo.
    assert meta.chamadas == []
    assert meta.recebeu == []
    assert await _tokens(db) == []


async def test_sem_permissao_nenhuma_403(client, db, make_user, auth_as, meta):
    _, rede = await _cenario(db, make_user, auth_as, perms={})

    post = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})
    delete = await client.delete(f"{API}/{rede.id}/conectar")

    assert post.status_code == 403, post.text
    assert delete.status_code == 403, delete.text
    assert meta.chamadas == []
    assert await _tokens(db) == []


# ══════════════════════════ (8) detalhe/listagem/grid: estado sim, token nunca


async def test_detalhe_listagem_e_grid_nunca_devolvem_o_token(
    client, db, make_user, auth_as, meta
):
    """Varredura no JSON INTEIRO das três telas que mostram a conta. O contrato
    tem os campos de ESTADO da credencial (has_token, token_status,
    token_conta_externa, token_expires_at) — e o valor do token em nenhum
    deles."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000001", page_nome="Poofy Store", ig_user_id=IG_ID, ig_username=CONTA)
    ]
    assert (
        await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})
    ).status_code == 200

    detalhe = await client.get(f"{API}/{rede.id}")
    listagem = await client.get(API)
    grid = await client.get(f"{API}/grid")

    for r in (detalhe, listagem, grid):
        assert r.status_code == 200, r.text
        _sem_segredo(r)

    estado = {"has_token", "token_status", "token_conta_externa", "token_expires_at"}
    assert estado <= set(detalhe.json())
    assert estado <= set(listagem.json()[0])
    celulas = grid.json()["rows"][0]["cells"]["instagram"]
    assert estado <= set(celulas[0])
    # E os tetos do robô viajam junto (a tela liga/desliga a auto-postagem ali).
    assert {"postagem_auto", "postagem_max_dia", "postagem_intervalo_min"} <= set(detalhe.json())


async def test_detalhe_mostra_a_conta_externa_depois_de_conectar(
    client, db, make_user, auth_as, meta
):
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000001", page_nome="Poofy Store", ig_user_id=IG_ID, ig_username=CONTA)
    ]
    assert (
        await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})
    ).status_code == 200

    detalhe = await client.get(f"{API}/{rede.id}")

    corpo = detalhe.json()
    assert corpo["has_token"] is True
    assert corpo["token_conta_externa"] == CONTA
    assert corpo["token_status"] == STATUS_OK


# ════════════════════════════════ (9) PATCH: interruptor e tetos do robô


async def test_patch_grava_postagem_auto_e_os_tetos(client, db, make_user, auth_as):
    """O robô só publica sozinho em conta com `postagem_auto` ligada; os tetos
    são por conta (Eduardo, 15/09/2026: rodar automático sem babá)."""
    _, rede = await _cenario(db, make_user, auth_as)
    assert rede.postagem_auto is False

    r = await client.patch(
        f"{API}/{rede.id}",
        json={"postagem_auto": True, "postagem_max_dia": 5, "postagem_intervalo_min": 30},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["postagem_auto"], body["postagem_max_dia"], body["postagem_intervalo_min"]) == (
        True,
        5,
        30,
    )
    stmt = select(RedeSocial).where(RedeSocial.id == rede.id)
    salvo = (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one()
    assert (salvo.postagem_auto, salvo.postagem_max_dia, salvo.postagem_intervalo_min) == (
        True,
        5,
        30,
    )


async def test_patch_null_limpa_os_tetos_e_volta_ao_padrao_do_servidor(
    client, db, make_user, auth_as
):
    """Teto NULL = usa o padrão do servidor (2 posts/dia, 90 min). Limpar o
    campo na tela precisa GRAVAR NULL — se "null = não mexe" valesse aqui, não
    haveria como voltar ao padrão."""
    _, rede = await _cenario(db, make_user, auth_as)
    assert (
        await client.patch(
            f"{API}/{rede.id}",
            json={"postagem_auto": True, "postagem_max_dia": 5, "postagem_intervalo_min": 30},
        )
    ).status_code == 200

    r = await client.patch(
        f"{API}/{rede.id}", json={"postagem_max_dia": None, "postagem_intervalo_min": None}
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["postagem_max_dia"] is None
    assert body["postagem_intervalo_min"] is None
    # O interruptor NÃO veio no body: continua ligado (ausente = não altera).
    assert body["postagem_auto"] is True
    stmt = select(RedeSocial).where(RedeSocial.id == rede.id)
    salvo = (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one()
    assert salvo.postagem_max_dia is None
    assert salvo.postagem_intervalo_min is None
    assert salvo.postagem_auto is True


# ══════════════════════════════════════════════════════════════════════════
# Regressões da revisão adversarial (15/09/2026): escolher a conta errada
# publica o vídeo de uma marca no perfil de outra, e isso não tem desfazer.
# ══════════════════════════════════════════════════════════════════════════


async def test_arroba_parecido_nao_casa_sozinho(client, db, make_user, auth_as, meta):
    """`@poofy.oficial` NÃO pode casar com `@poofy.oficial.br`.

    O casamento por "contém" escolhia sozinho a conta errada — e o robô
    publicaria lá. Sem nome idêntico o endpoint devolve a lista e não grava.
    """
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(
            page_id="10000009",
            page_nome="Poofy BR",
            ig_user_id="17841400000000099",
            ig_username=f"{CONTA}.br",
        )
    ]

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200
    assert r.json()["ok"] is False, "casou com um @ que não é o da conta"
    assert await _tokens(db) == [], "não pode gravar credencial sem certeza"


async def test_unica_conta_do_token_nao_e_assumida(client, db, make_user, auth_as, meta):
    """"Só tem uma, deve ser essa" é como um token colado na linha errada vira
    post na conta de outra marca. Uma opção só continua sendo uma ESCOLHA."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(
            page_id="10000010",
            page_nome="Uranyx",
            ig_user_id="17841400000000010",
            ig_username="uranyx.oficial",
        )
    ]

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.json()["ok"] is False
    assert await _tokens(db) == []


async def test_id_escolhido_que_o_token_nao_enxerga_e_recusado(
    client, db, make_user, auth_as, meta
):
    """O `external_user_id` vem do cliente: sem conferir contra o que o token
    enxerga, um id digitado (ou de outra marca) seria gravado como se fosse
    da conta."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(
            page_id="10000011",
            page_nome="Poofy Store",
            ig_user_id=IG_ID,
            ig_username="outra.conta",
        )
    ]

    r = await client.post(
        f"{API}/{rede.id}/conectar",
        json={"access_token": SEGREDO_FAKE, "external_user_id": "17841499999999999"},
    )

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "conta_nao_pertence_ao_token"
    assert await _tokens(db) == []


async def test_validade_do_token_fica_em_claro_pro_cron(client, db, make_user, auth_as, meta):
    """Sem `token_expires_at` preenchido o `meta_token_refresh` não tem o que
    achar: o token vence calado e a primeira notícia é um post que não saiu."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000012", page_nome="Poofy", ig_user_id=IG_ID, ig_username=CONTA)
    ]
    meta.validade = datetime.now(UTC) + timedelta(days=55)

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200 and r.json()["ok"] is True
    tok = (await _tokens(db))[0]
    assert tok.token_expires_at is not None
    assert (tok.token_expires_at - meta.validade).total_seconds() < 1


async def test_conta_sem_pagina_marca_o_provedor_instagram(
    client, db, make_user, auth_as, meta
):
    """Trilha Instagram Login: daí em diante TUDO fala com graph.instagram.com.
    Quem decide o host é a origem do token, não a plataforma da linha."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = []
    meta.direta = {"ig_user_id": "17841400000000013", "ig_username": CONTA}

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200 and r.json()["ok"] is True
    tok = (await _tokens(db))[0]
    assert tok.provedor == "instagram"


async def test_conta_com_pagina_fica_no_provedor_facebook(
    client, db, make_user, auth_as, meta
):
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000014", page_nome="Poofy", ig_user_id=IG_ID, ig_username=CONTA)
    ]

    await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    tok = (await _tokens(db))[0]
    assert tok.provedor == "facebook"


async def test_token_da_trilha_instagram_login_nao_morre_no_me_accounts(
    client, db, make_user, auth_as, meta
):
    """Token da trilha "Instagram API with Instagram Login" (conta SEM Página).

    Ele é emitido pelo graph.instagram.com, e o graph.facebook.com o recusa com
    code 190 — que LÊ como "token inválido" e é só host errado. Antes, essa
    recusa virava 422 na hora e a trilha direta nunca era tentada: quem não tem
    Página (o caso das marcas hoje) não conseguia conectar conta nenhuma.
    """
    _, rede = await _cenario(db, make_user, auth_as)
    meta.erro = meta_client.MetaError(
        "token inválido ou expirado — reconecte a conta (code 190)", code=190
    )
    meta.direta = {"ig_user_id": "17841400000000021", "ig_username": CONTA}

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    tok = (await _tokens(db))[0]
    assert tok.provedor == "instagram"
    assert tok.external_user_id == "17841400000000021"
    # `debug_token` é do graph.facebook.com — não se pergunta a validade de um
    # token do Instagram por lá.
    assert "validade_do_token" not in meta.chamadas
    _sem_segredo(r)


async def test_token_recusado_pelas_duas_trilhas_vira_422(
    client, db, make_user, auth_as, meta
):
    """Contraprova: token ruim de verdade (nenhuma das duas trilhas reconhece)
    continua sendo 422, sem gravar nada."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.erro = meta_client.MetaError("token inválido (code 190)", code=190)
    meta.direta = None

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "token_recusado_pela_meta"
    assert await _tokens(db) == []
    _sem_segredo(r)


async def test_token_da_pagina_e_guardado_e_nunca_sai_por_api(
    client, db, make_user, auth_as, meta
):
    """O `GET /me/accounts` devolve, junto de cada Página, o token DELA.

    É esse o token que a doc de Content Publishing pede pra publicar ("A Page
    access token requested from your app user who can perform the
    CREATE_CONTENT task on the Page") — o colado serve pra descobrir. Ele é
    guardado cifrado no mesmo blob e **não pode aparecer em resposta nenhuma**:
    quem tem o token da Página posta na marca.
    """
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(
            page_id="10000020",
            page_nome="Poofy Store",
            ig_user_id=IG_ID,
            ig_username=CONTA,
            page_token=SEGREDO_PAGINA,
        )
    ]

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200 and r.json()["ok"] is True
    _sem_segredo(r)

    tok = (await _tokens(db))[0]
    guardado = decrypt_json(tok.token_enc)
    assert guardado["access_token"] == SEGREDO_FAKE
    assert guardado["page_access_token"] == SEGREDO_PAGINA

    # E nem em claro no banco (inclusive escondido no hex do bytea).
    dump = await db.scalar(
        text("SELECT string_agg(t::text, ' ') FROM redes_sociais_tokens t")
    )
    _nao_esta_em_claro(dump or "", SEGREDO_PAGINA)

    # A listagem e o detalhe também não podem carregar.
    _sem_segredo(await client.get(f"{API}/{rede.id}"))
    _sem_segredo(await client.get(API))


async def test_sem_pagina_nao_inventa_token_de_pagina(
    client, db, make_user, auth_as, meta
):
    """Trilha Instagram Login: não existe Página, logo não existe token de
    Página. O blob guarda só o colado — e o worker cai nele."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = []
    meta.direta = {"ig_user_id": "17841400000000022", "ig_username": CONTA}

    r = await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    assert r.status_code == 200 and r.json()["ok"] is True
    guardado = decrypt_json((await _tokens(db))[0].token_enc)
    assert guardado["access_token"] == SEGREDO_FAKE
    assert "page_access_token" not in guardado


async def test_trocar_o_arroba_da_conta_descarta_o_token(
    client, db, make_user, auth_as, meta
):
    """Mudar marca/@ /plataforma faz a linha apontar pra OUTRA conta.

    Se o token da conta antiga continuasse ali, o robô publicaria o vídeo de
    uma marca no perfil de outra — com a credencial de quem nem sabe. A
    credencial cai junto e alguém reconecta conscientemente.
    """
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000030", page_nome="Poofy", ig_user_id=IG_ID, ig_username=CONTA)
    ]
    assert (
        await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})
    ).status_code == 200
    assert len(await _tokens(db)) == 1

    r = await client.patch(f"{API}/{rede.id}", json={"conta": "outra.conta.oficial"})

    assert r.status_code == 200, r.text
    assert await _tokens(db) == [], "o token da conta antiga sobreviveu à troca"
    assert r.json()["has_token"] is False


async def test_editar_campo_inofensivo_mantem_o_token(
    client, db, make_user, auth_as, meta
):
    """Contraprova: mexer em algo que NÃO muda de qual conta a linha é não
    pode derrubar a credencial — senão toda edição vira retrabalho."""
    _, rede = await _cenario(db, make_user, auth_as)
    meta.contas = [
        _pagina(page_id="10000031", page_nome="Poofy", ig_user_id=IG_ID, ig_username=CONTA)
    ]
    await client.post(f"{API}/{rede.id}/conectar", json={"access_token": SEGREDO_FAKE})

    r = await client.patch(f"{API}/{rede.id}", json={"postagem_max_dia": 3})

    assert r.status_code == 200, r.text
    assert len(await _tokens(db)) == 1
    assert r.json()["has_token"] is True
