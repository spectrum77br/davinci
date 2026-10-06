"""Módulo App Uranyx: o repasse /api/app-uranyx/* → {APP_URANYX_API_URL}/admin/*.

Contrato conteudo-e-catalogo-v1, seção 6. A API do app é FALSA
(httpx.MockTransport no lugar de `repasse._cliente`): o teste vê exatamente o
pedido que sairia do DaVinci (URL, cabeçalhos, corpo, tempo limite).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.config import get_settings
from app.models import UserRole
from app.routers import app_uranyx as rota
from app.services.app_uranyx import repasse

BASE = "http://app.test/api/app/v1"
TOKEN = "token-da-equipe-de-teste"  # noqa: S105 — token de teste, não é segredo
THORFINN = "thorfinn@davinci-test.com"


class AppFalso:
    def __init__(self) -> None:
        self.pedidos: list[httpx.Request] = []
        self.tempos: list[float] = []
        self.responder: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(
            200, json={"ok": True}
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        return self.responder(request)

    @property
    def ultimo(self) -> httpx.Request:
        assert self.pedidos, "nenhum pedido chegou à API do app"
        return self.pedidos[-1]


class LogFalso:
    def __init__(self) -> None:
        self.linhas: list[tuple[str, dict]] = []

    def info(self, evento: str, **kw) -> None:
        self.linhas.append((evento, kw))

    warning = error = info


@pytest.fixture
def configurado(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "app_uranyx_api_url", BASE)
    monkeypatch.setattr(s, "app_uranyx_admin_token", SecretStr(TOKEN))
    monkeypatch.setattr(s, "app_uranyx_usuarios", f"{THORFINN},heisenberg@davinci-test.com")
    return s


@pytest.fixture
def app_falso(monkeypatch) -> AppFalso:
    falso = AppFalso()

    def cliente(tempo: float) -> httpx.AsyncClient:
        falso.tempos.append(tempo)
        return httpx.AsyncClient(transport=httpx.MockTransport(falso.handler), timeout=tempo)

    monkeypatch.setattr(repasse, "_cliente", cliente)
    return falso


@pytest.fixture
def log(monkeypatch) -> LogFalso:
    falso = LogFalso()
    monkeypatch.setattr(rota, "logger", falso)
    return falso


@pytest_asyncio.fixture
async def thorfinn(make_user, auth_as, configurado):
    u = await make_user(email=THORFINN, role=UserRole.ADMIN)
    auth_as(u)
    return u


# ─── Acesso ────────────────────────────────────────────────────────────────────


async def test_sem_login_401(client, configurado, app_falso):
    r = await client.get("/api/app-uranyx/catalogo")
    assert r.status_code == 401
    assert not app_falso.pedidos


@pytest.mark.parametrize(
    ("email", "papel"),
    [
        (THORFINN, UserRole.USER),  # na lista, mas não é admin
        ("admin-fora@davinci-test.com", UserRole.ADMIN),  # admin fora da lista
    ],
)
async def test_quem_nao_esta_liberado_recebe_403(
    client, make_user, auth_as, configurado, app_falso, email, papel
):
    auth_as(await make_user(email=email, role=papel))
    for metodo in ("GET", "POST", "DELETE"):
        r = await client.request(metodo, "/api/app-uranyx/catalogo")
        assert r.status_code == 403, (metodo, r.text)
        assert r.json()["detail"]["code"] == "app_uranyx_restrito"
    assert not app_falso.pedidos


async def test_lista_vazia_desliga_ate_para_quem_estava_nela(
    client, thorfinn, configurado, app_falso, monkeypatch
):
    monkeypatch.setattr(configurado, "app_uranyx_usuarios", "")
    r = await client.get("/api/app-uranyx/catalogo")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "app_uranyx_restrito"
    assert not app_falso.pedidos


async def test_liberado_passa(client, thorfinn, app_falso):
    r = await client.get("/api/app-uranyx/catalogo")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert len(app_falso.pedidos) == 1


# ─── Lista de rotas ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rota_permitida",
    [
        "catalogo",
        "catalogo/sincronizar",
        "catalogo/sincronizacao",
        "catalogo/abc/manuais",
        "skus-sem-mapa",
        "skus-ignorados/kit%20promo%2001",
        "arquivos",
        "manuais/m1",
        "receitas/r1",
        "apps/categorias",
        "contas/busca",
        "reclamacoes/x/decidir",
        "fila/c1/reenviar",
    ],
)
def test_rotas_do_contrato_passam(rota_permitida):
    assert repasse.caminho_permitido(rota_permitida)


@pytest.mark.parametrize(
    "rota_proibida",
    [
        "",
        "/catalogo",
        "perfil",
        "produtos",
        "chamados/rascunho",
        "_dev/emails",
        "catalogo-x",
        "cat%61logo",  # codificado não vale
        "catalogo/../perfil",
        "catalogo/%2e%2e/%2E%2E/perfil",
        "catalogo/./x",
        "catalogo/abc/skus/dg300%2F..",  # %2F que vira ".." depois de decodificado
    ],
)
def test_o_resto_nao_passa(rota_proibida):
    assert not repasse.caminho_permitido(rota_proibida)


@pytest.mark.parametrize(
    "caminho",
    [
        "/api/app-uranyx/perfil",
        "/api/app-uranyx/chamados",
        "/api/app-uranyx/_dev/emails",
        "/api/app-uranyx/catalogo-x",
        "/api/app-uranyx/catalogo/%2e%2e/%2e%2e/perfil",
        "/api/app-uranyx/",
    ],
)
async def test_rota_fora_da_lista_e_404_sem_chamar_a_api(client, thorfinn, app_falso, caminho):
    r = await client.post(caminho, json={"x": 1})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "app_uranyx_rota_desconhecida"
    assert not app_falso.pedidos


# ─── Repasse ───────────────────────────────────────────────────────────────────


async def test_get_mantem_query_poe_o_token_e_nao_leva_o_cookie(client, thorfinn, app_falso):
    catalogo = [{"id": "c1", "nome": "Uranyx A17", "fora_do_site": False}]
    app_falso.responder = lambda _r: httpx.Response(200, json=catalogo)
    r = await client.get(
        "/api/app-uranyx/catalogo?ativo=true&busca=a%20b",
        headers={"Cookie": "davinci_session=segredo-do-davinci", "X-Qualquer": "1"},
    )
    assert r.status_code == 200
    assert r.json() == catalogo
    assert r.headers["content-type"].startswith("application/json")

    p = app_falso.ultimo
    assert p.method == "GET"
    assert str(p.url) == f"{BASE}/admin/catalogo?ativo=true&busca=a%20b"
    assert p.headers["authorization"] == f"Bearer {TOKEN}"
    assert "cookie" not in p.headers
    assert "x-qualquer" not in p.headers
    assert app_falso.tempos == [repasse.TEMPO_S] == [10.0]


async def test_caminho_vai_cru_com_a_barra_codificada(client, thorfinn, app_falso):
    """O código-base vai no caminho com encodeURIComponent: `dg300/azul`."""
    app_falso.responder = lambda _r: httpx.Response(200, json={"codigo_base": "dg300/azul"})
    corpo = {"cor": "Azul"}
    r = await client.put("/api/app-uranyx/catalogo/c1/skus/dg300%2Fazul", json=corpo)
    assert r.status_code == 200, r.text
    p = app_falso.ultimo
    assert p.method == "PUT"
    assert p.url.raw_path == b"/api/app/v1/admin/catalogo/c1/skus/dg300%2Fazul"
    assert json.loads(p.content) == corpo
    assert p.headers["content-type"] == "application/json"


@pytest.mark.parametrize(
    ("status", "corpo"),
    [
        (422, {"erro": "validacao", "mensagem": "Confira os campos", "campos": {"titulo": "x"}}),
        (409, {"erro": "em_uso", "mensagem": "O arquivo está em uso", "campos": {}}),
        (404, {"erro": "nao_encontrado", "mensagem": "Não encontrado", "campos": {}}),
        (500, {"erro": "erro_servidor", "mensagem": "Algo deu errado", "campos": {}}),
        (503, {"erro": "erro_servidor", "mensagem": "Sem espaço", "campos": {}}),
    ],
)
async def test_erro_da_api_passa_como_veio(client, thorfinn, app_falso, status, corpo):
    app_falso.responder = lambda _r: httpx.Response(status, json=corpo)
    r = await client.patch("/api/app-uranyx/manuais/m1", json={"titulo": ""})
    assert r.status_code == status
    assert r.json() == corpo


async def test_429_leva_o_retry_after(client, thorfinn, app_falso):
    corpo = {"erro": "limite_excedido", "mensagem": "Espere", "campos": {}}
    app_falso.responder = lambda _r: httpx.Response(
        429, json=corpo, headers={"Retry-After": "60", "Set-Cookie": "x=1"}
    )
    r = await client.post("/api/app-uranyx/contas/busca", json={"documento": "52998224725"})
    assert r.status_code == 429
    assert r.json() == corpo
    assert r.headers["retry-after"] == "60"
    assert "set-cookie" not in r.headers


async def test_201_e_204_passam(client, thorfinn, app_falso, log):
    app_falso.responder = lambda _r: httpx.Response(201, json={"id": "r-123", "titulo": "Bolo"})
    r = await client.post("/api/app-uranyx/receitas", json={"titulo": "Bolo"})
    assert r.status_code == 201
    assert r.json() == {"id": "r-123", "titulo": "Bolo"}

    app_falso.responder = lambda _r: httpx.Response(204)
    r = await client.delete("/api/app-uranyx/receitas/r-123")
    assert r.status_code == 204
    assert r.content == b""
    assert app_falso.ultimo.method == "DELETE"


async def test_post_sem_corpo(client, thorfinn, app_falso):
    r = await client.post("/api/app-uranyx/fila/c1/reenviar")
    assert r.status_code == 200
    p = app_falso.ultimo
    assert p.content == b""
    assert "content-type" not in p.headers


async def test_sincronizar_tem_60_s(client, thorfinn, app_falso):
    await client.post("/api/app-uranyx/catalogo/sincronizar")
    await client.get("/api/app-uranyx/catalogo/sincronizacao")
    assert app_falso.tempos == [60.0, 10.0]


# ─── Upload ────────────────────────────────────────────────────────────────────


async def test_upload_repassa_o_multipart_igual(client, thorfinn, app_falso):
    app_falso.responder = lambda _r: httpx.Response(
        201, json={"id": "a1", "url": "http://x/conteudo/arquivos/a1", "mime": "application/pdf"}
    )
    pdf = b"%PDF-1.7\n" + b"x" * 5000
    r = await client.post(
        "/api/app-uranyx/arquivos",
        files={"arquivo": ("manual.pdf", pdf, "application/pdf")},
        data={"nome": "Manual A17"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["id"] == "a1"
    p = app_falso.ultimo
    assert str(p.url) == f"{BASE}/admin/arquivos"
    assert p.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert pdf in p.content
    assert b'name="nome"' in p.content and b"Manual A17" in p.content
    assert app_falso.tempos == [60.0]


def test_teto_do_upload_e_25_mb():
    assert rota.UPLOAD_MAX_BYTES == 25 * 1024 * 1024
    assert 0 < rota.FOLGA_MULTIPART_BYTES <= 1024 * 1024


async def test_upload_acima_do_teto_e_413(client, thorfinn, app_falso, monkeypatch, log):
    monkeypatch.setattr(rota, "UPLOAD_MAX_BYTES", 2000)
    monkeypatch.setattr(rota, "FOLGA_MULTIPART_BYTES", 500)
    # Com Content-Length (o normal do navegador).
    r = await client.post(
        "/api/app-uranyx/arquivos", files={"arquivo": ("a.pdf", b"x" * 3000, "application/pdf")}
    )
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "app_uranyx_arquivo_grande"

    # Sem Content-Length (corpo em pedaços): para de ler no teto.
    async def pedacos():
        for _ in range(10):
            yield b"y" * 400

    r = await client.post(
        "/api/app-uranyx/arquivos",
        content=pedacos(),
        headers={"Content-Type": "multipart/form-data; boundary=abc"},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "app_uranyx_arquivo_grande"
    assert not app_falso.pedidos
    assert [kw["status"] for _ev, kw in log.linhas if _ev == "app_uranyx_acao"] == [413, 413]

    # Logo abaixo do teto passa.
    r = await client.post(
        "/api/app-uranyx/arquivos", files={"arquivo": ("a.pdf", b"x" * 1900, "application/pdf")}
    )
    assert r.status_code == 200, r.text


async def test_json_grande_demais_e_413_e_tipo_estranho_415(
    client, thorfinn, app_falso, monkeypatch
):
    monkeypatch.setattr(rota, "JSON_MAX_BYTES", 100)
    r = await client.post("/api/app-uranyx/receitas", json={"titulo": "x" * 200})
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "app_uranyx_corpo_grande"

    r = await client.post(
        "/api/app-uranyx/receitas", content=b"oi", headers={"Content-Type": "text/plain"}
    )
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == "app_uranyx_tipo_nao_suportado"
    assert not app_falso.pedidos


# ─── API do app fora do ar / sem configuração ──────────────────────────────────


def _indisponivel(r: httpx.Response) -> str:
    assert r.status_code == 503, r.text
    detalhe = r.json()["detail"]
    assert detalhe["code"] == "app_uranyx_indisponivel"
    assert detalhe["message"]
    assert TOKEN not in r.text
    return detalhe["message"]


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("app_uranyx_api_url", ""),
        ("app_uranyx_api_url", "app.test/sem-esquema"),
        ("app_uranyx_admin_token", SecretStr("")),
    ],
)
async def test_sem_configuracao_e_503(
    client, thorfinn, app_falso, configurado, monkeypatch, campo, valor
):
    monkeypatch.setattr(configurado, campo, valor)
    msg = _indisponivel(await client.get("/api/app-uranyx/catalogo"))
    assert "configurado" in msg
    assert not app_falso.pedidos


async def test_sem_conexao_e_503(client, thorfinn, app_falso):
    def recusa(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    app_falso.responder = recusa
    assert "fora do ar" in _indisponivel(await client.get("/api/app-uranyx/catalogo"))


async def test_sem_resposta_no_tempo_e_503(client, thorfinn, app_falso):
    def demora(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    app_falso.responder = demora
    msg = _indisponivel(await client.post("/api/app-uranyx/fila/c1/reenviar"))
    assert "a tempo" in msg


async def test_servidor_de_verdade_desligado_e_503(client, thorfinn, configurado, monkeypatch):
    """Sem o MockTransport: porta fechada no próprio Mac."""
    monkeypatch.setattr(configurado, "app_uranyx_api_url", "http://127.0.0.1:1/api/app/v1")
    _indisponivel(await client.get("/api/app-uranyx/catalogo"))


async def test_token_recusado_vira_503_e_nao_401(client, thorfinn, app_falso):
    app_falso.responder = lambda _r: httpx.Response(
        401, json={"erro": "nao_autenticado", "mensagem": "Token inválido", "campos": {}}
    )
    msg = _indisponivel(await client.get("/api/app-uranyx/catalogo"))
    assert "token" in msg
    assert '"uxadm_…"' in msg and "sha256" in msg  # qual token vai no DaVinci
    assert "10 tentativas" in msg and "15 min" in msg  # o bloqueio que vem depois


async def test_token_errado_depois_do_bloqueio_continua_503(client, thorfinn, app_falso, log):
    """A API do app (servicos/admin.autenticar): 10 pedidos com 401 e, depois, 429
    `bloqueado_temporariamente` por 15 min. O 429 é o MESMO token errado: não pode
    chegar à tela como "Muitas tentativas"."""
    pedidos = 0

    def api_do_app(_r: httpx.Request) -> httpx.Response:
        nonlocal pedidos
        pedidos += 1
        if pedidos <= 10:
            return httpx.Response(
                401, json={"erro": "nao_autenticado", "mensagem": "Acesso ao painel negado."}
            )
        return httpx.Response(
            429,
            json={
                "erro": "bloqueado_temporariamente",
                "mensagem": "Muitas tentativas. Espere um pouco e tente de novo.",
            },
            headers={"Retry-After": "900"},
        )

    app_falso.responder = api_do_app
    mensagens = set()
    for _ in range(12):
        r = await client.patch("/api/app-uranyx/manuais/m1", json={"ativo": False})
        mensagens.add(_indisponivel(r))
        assert "Muitas tentativas" not in r.text
        assert "retry-after" not in r.headers
    assert pedidos == 12
    assert mensagens == {repasse.MSG_DAVINCI_RECUSADO}
    acoes = [kw["status"] for ev, kw in log.linhas if ev == "app_uranyx_acao"]
    assert acoes == [503] * 12


@pytest.mark.parametrize(
    "resposta",
    [
        # outro 429 (hipotético: hoje o /admin só dá o bloqueado_temporariamente): passa como veio
        httpx.Response(429, json={"erro": "limite_excedido", "mensagem": "Espere"}),
        # 429 sem JSON (não é a API do app): passa como veio
        httpx.Response(
            429, text="bloqueado_temporariamente", headers={"Content-Type": "text/plain"}
        ),
    ],
)
async def test_429_que_nao_e_o_bloqueio_do_token_passa(client, thorfinn, app_falso, resposta):
    app_falso.responder = lambda _r: resposta
    r = await client.get("/api/app-uranyx/catalogo")
    assert r.status_code == 429
    assert r.content == resposta.content


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(404),  # o `respond @interno 404` do Caddy: sem corpo nem tipo
        httpx.Response(404, text="Not Found", headers={"Content-Type": "text/plain"}),
        httpx.Response(404, text="<h1>404</h1>", headers={"Content-Type": "text/html"}),
    ],
)
async def test_404_sem_json_e_endereco_errado(client, thorfinn, app_falso, resposta):
    """APP_URANYX_API_URL com o endereço PÚBLICO: o Caddy esconde /admin com 404 sem JSON."""
    app_falso.responder = lambda _r: resposta
    msg = _indisponivel(await client.get("/api/app-uranyx/catalogo"))
    assert msg == repasse.MSG_ENDERECO_ERRADO
    assert "http://uranyx-api:8000/api/app/v1" in msg
    assert "APP_URANYX_API_URL" in msg


async def test_404_com_json_da_api_do_app_passa(client, thorfinn, app_falso):
    corpo = {"erro": "nao_encontrado", "mensagem": "Não encontramos o que você procurava."}
    app_falso.responder = lambda _r: httpx.Response(404, json=corpo)
    r = await client.delete("/api/app-uranyx/receitas/r-inexistente")
    assert r.status_code == 404
    assert r.json() == corpo


async def test_gateway_sem_json_vira_503(client, thorfinn, app_falso):
    app_falso.responder = lambda _r: httpx.Response(
        502, text="<html>Bad Gateway</html>", headers={"Content-Type": "text/html"}
    )
    _indisponivel(await client.get("/api/app-uranyx/catalogo"))


# ─── Cabeçalhos: Content-Type fora do ASCII e nosniff ──────────────────────────


def _nosniff(r: httpx.Response) -> None:
    assert r.headers.get("x-content-type-options") == "nosniff", (r.status_code, r.headers)


@pytest.mark.parametrize(
    "tipo",
    [
        "application/json; charset=utf-8; x=€".encode(),
        "multipart/form-data; boundary=ção".encode(),
        b"application/json; x=\xff",  # nem UTF-8
    ],
)
async def test_tipo_fora_do_ascii_no_pedido_e_415_sem_chamar_a_api(
    client, thorfinn, app_falso, log, tipo
):
    """O httpx só manda cabeçalho ASCII: antes, o repasse caía com 500."""
    r = await client.post(
        "/api/app-uranyx/receitas", content=b'{"titulo": "Bolo"}', headers={"Content-Type": tipo}
    )
    assert r.status_code == 415, r.text
    assert r.json()["detail"]["code"] == "app_uranyx_tipo_nao_suportado"
    _nosniff(r)
    assert not app_falso.pedidos
    assert [kw["status"] for ev, kw in log.linhas if ev == "app_uranyx_acao"] == [415]


@pytest.mark.parametrize(
    "tipo",
    [
        "image/png; nome=foto€.png".encode(),  # fora do latin-1: o Starlette caía com 500
        "text/html; x=ção".encode(),  # dentro do latin-1: passava torto, e como HTML
        b"text/html; x=\xff",
    ],
)
async def test_tipo_fora_do_ascii_na_resposta_vira_octet_stream(client, thorfinn, app_falso, tipo):
    corpo = b"<script>alert(1)</script>"
    app_falso.responder = lambda _r: httpx.Response(
        200,
        content=corpo,
        headers={
            "Content-Type": tipo,
            "Content-Disposition": 'inline; filename="manual€.pdf"'.encode(),
            "ETag": '"abc"',
        },
    )
    r = await client.get("/api/app-uranyx/catalogo")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.content == corpo
    assert "content-disposition" not in r.headers  # outro cabeçalho fora do ASCII não volta
    assert r.headers["etag"] == '"abc"'
    _nosniff(r)


async def test_nosniff_em_toda_resposta_do_repasse(
    client, make_user, auth_as, configurado, app_falso, monkeypatch
):
    r = await client.get("/api/app-uranyx/catalogo")
    assert r.status_code == 401
    _nosniff(r)

    auth_as(await make_user(email="admin-fora@davinci-test.com", role=UserRole.ADMIN))
    r = await client.get("/api/app-uranyx/catalogo")
    assert r.status_code == 403
    _nosniff(r)

    auth_as(await make_user(email=THORFINN, role=UserRole.ADMIN))
    r = await client.get("/api/app-uranyx/perfil")
    assert r.status_code == 404
    _nosniff(r)

    for status, resposta in [
        (200, httpx.Response(200, json={"ok": True})),
        (204, httpx.Response(204)),
        (422, httpx.Response(422, json={"erro": "validacao", "mensagem": "x", "campos": {}})),
        (503, httpx.Response(502, text="Bad Gateway", headers={"Content-Type": "text/html"})),
    ]:
        app_falso.responder = lambda _r, resposta=resposta: resposta
        r = await client.patch("/api/app-uranyx/manuais/m1", json={"ativo": False})
        assert r.status_code == status, r.text
        _nosniff(r)

    r = await client.post(
        "/api/app-uranyx/receitas", content=b"oi", headers={"Content-Type": "text/plain"}
    )
    assert r.status_code == 415
    _nosniff(r)

    monkeypatch.setattr(rota, "JSON_MAX_BYTES", 10)
    r = await client.post("/api/app-uranyx/receitas", json={"titulo": "x" * 20})
    assert r.status_code == 413
    _nosniff(r)


@pytest.mark.parametrize(
    ("tipo", "corpo", "disposicao"),
    [
        ("image/jpeg", b"\xff\xd8\xff\xe0" + b"x" * 100, None),
        ("application/pdf", b"%PDF-1.7\n" + b"x" * 100, 'inline; filename="Manual.pdf"'),
    ],
)
async def test_foto_e_pdf_passam_com_o_tipo_certo(
    client, thorfinn, app_falso, tipo, corpo, disposicao
):
    """O nosniff não muda a prévia: o tipo e o `inline` da API do app voltam iguais."""
    cabecalhos = {"Content-Type": tipo, "Cache-Control": "public, max-age=60"}
    if disposicao:
        cabecalhos["Content-Disposition"] = disposicao
    app_falso.responder = lambda _r: httpx.Response(200, content=corpo, headers=cabecalhos)
    r = await client.get("/api/app-uranyx/arquivos")
    assert r.status_code == 200
    assert r.content == corpo
    assert r.headers["content-type"] == tipo
    assert r.headers.get("content-disposition") == disposicao
    assert r.headers["cache-control"] == "public, max-age=60"
    _nosniff(r)


# ─── Registro ──────────────────────────────────────────────────────────────────


async def test_registra_quem_fez_sem_corpo_nem_token(client, thorfinn, app_falso, log):
    await client.get("/api/app-uranyx/catalogo")
    assert not log.linhas  # GET não registra

    app_falso.responder = lambda _r: httpx.Response(201, json={"id": "m-9"})
    await client.post(
        "/api/app-uranyx/catalogo/c1/manuais", json={"titulo": "Manual secreto", "arquivo_id": "a1"}
    )
    app_falso.responder = lambda _r: httpx.Response(
        422, json={"erro": "validacao", "mensagem": "x", "campos": {}}
    )
    await client.patch("/api/app-uranyx/manuais/m-9", json={"ativo": False})

    acoes = [kw for ev, kw in log.linhas if ev == "app_uranyx_acao"]
    assert acoes == [
        {
            "email": THORFINN,
            "metodo": "POST",
            "caminho": "/catalogo/c1/manuais",
            "status": 201,
            "id_criado": "m-9",
        },
        {
            "email": THORFINN,
            "metodo": "PATCH",
            "caminho": "/manuais/m-9",
            "status": 422,
            "id_criado": None,
        },
    ]
    texto = repr(log.linhas)
    assert TOKEN not in texto
    assert "Manual secreto" not in texto


async def test_503_tambem_registra(client, thorfinn, app_falso, log, configurado, monkeypatch):
    monkeypatch.setattr(configurado, "app_uranyx_api_url", "")
    await client.delete("/api/app-uranyx/receitas/r1")
    acoes = [kw for ev, kw in log.linhas if ev == "app_uranyx_acao"]
    assert [(a["metodo"], a["caminho"], a["status"]) for a in acoes] == [
        ("DELETE", "/receitas/r1", 503)
    ]
