# ruff: noqa: S105, S106  (tokens de teste de um ML falso, nada real)
"""A foto/arquivo que o comprador manda no Mercado Livre, MOSTRADA na conversa (28/10/2026).

`GET /api/atendimento/conversas/{id}/anexos/{arquivo}` (`services/atendimento/anexo_ml.py`).
Sem rede: o cliente HTTP do download (`anexo_ml._http`) fala com um ML falso
que guarda cada pedido. O que se segura:

- só o `arquivo` que está numa mensagem DAQUELA conversa (outro id, o de
  outra conversa, o de uma nota interna, um formato estranho: 404 sem ir ao ML);
- as travas de quem lê a conversa: login, `pode_ver` e o escopo da equipe;
- o endereço da doc oficial (pós-venda: `/messages/attachments/{id}?tag=
  post_sale&site_id=MLB`; reclamação: `/post-purchase/v1/claims/{id}/
  attachments/{arquivo}/download`) com o token da loja DA CONVERSA;
- o `Content-Type` do ML passa (da lista); genérico → o guardado / a
  assinatura; HTML nunca; `inline` só imagem, PDF `attachment` com nome seguro;
- erros: 422/404 do ML → 404; 5xx → 502; tempo esgotado → 504; acima do teto
  → 502 sem guardar nada; redirecionamento sem o token para outro host;
- nada de token nem do id do anexo no log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from structlog.testing import capture_logs

from app.config import get_settings
from app.deps.team_scope import TeamScope
from app.models import AtendimentoMensagem, Integration, IntegrationPlatform, UserRole
from app.routers import atendimento as rota_atendimento
from app.security.cipher import encrypt_json
from app.services.atendimento import anexo_ml, gravar
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_EQUIPE,
    CANAL_POS_VENDA,
    CANAL_RECLAMACAO,
    ORIGEM_NOTA,
    TIPO_NOTA,
)

URL = "/api/atendimento"
SELLER = 999000111
CLAIM = "5601757827"
ARQUIVO = "118859700_3dba482b-8c64-4bdf-b64e-dd2bbd9bf89f.jpeg"
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"1" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"2" * 120
PDF = b"%PDF-1.4\n" + b"3" * 80
_AsyncClientReal = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    return s


class _MLFalso:
    """O ML: cada GET responde o que o teste mandou (`respostas[caminho]`)."""

    def __init__(self, monkeypatch) -> None:
        self.feitos: list[httpx.Request] = []
        self.respostas: dict[str, httpx.Response | Exception] = {}

        def registrar(request: httpx.Request) -> httpx.Response:
            self.feitos.append(request)
            chave = f"{request.url.host}{request.url.path}"
            resposta = self.respostas.get(chave)
            if isinstance(resposta, Exception):
                raise resposta
            if resposta is None:
                return httpx.Response(404, json={"code": "not_found"})
            return resposta

        def http() -> httpx.AsyncClient:
            return _AsyncClientReal(
                transport=httpx.MockTransport(registrar),
                timeout=anexo_ml.TEMPO_HTTP,
                follow_redirects=False,
            )

        monkeypatch.setattr(anexo_ml, "_http", http)

    def arquivo(self, caminho: str, conteudo: bytes, tipo: str, *, host: str = anexo_ml.ML_HOST):
        self.respostas[f"{host}{caminho}"] = httpx.Response(
            200, content=conteudo, headers={"content-type": tipo}
        )

    def bearer(self, i: int = -1) -> str | None:
        return self.feitos[i].headers.get("authorization")


async def _loja(db, user, nome: str, token: str) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.ML,
        name=nome,
        credentials=encrypt_json({"access_token": token, "refresh_token": "rt", "user_id": SELLER}),
    )
    db.add(integ)
    await db.flush()
    return integ


async def _conversa(
    db,
    integ: Integration,
    *,
    canal: str = CANAL_POS_VENDA,
    externo_id: str = "2000019150957778",
    anexos: list[dict] | None = None,
    plataforma: str = "ml",
):
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal,
        externo_id=externo_id,
        comprador_id="111",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"m-{externo_id}",
        autor=AUTOR_CLIENTE,
        texto=None,
        tipo="imagem",
        anexos=anexos
        if anexos is not None
        else [
            {
                "arquivo": ARQUIVO,
                "nome": "Screenshot_20261028_110935_Gallery.jpeg",
                "tipo": "image/jpeg",
                "tamanho": len(JPG),
            }
        ],
        enviada_em=datetime.now(UTC) - timedelta(minutes=5),
    )
    await db.commit()
    return conversa


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


def _rota(conversa, arquivo: str = ARQUIVO) -> str:
    return f"{URL}/conversas/{conversa.id}/anexos/{arquivo}"


# ─────────────── o caminho feliz: a doc oficial, o token da loja ───────────────


async def test_foto_do_pos_venda_vem_do_ml_com_o_token_da_loja(db, client, admin, monkeypatch):
    ml = _MLFalso(monkeypatch)
    ml.arquivo(f"/messages/attachments/{ARQUIVO}", JPG, "image/jpeg")
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    conversa = await _conversa(db, integ)
    r = await client.get(_rota(conversa))
    assert r.status_code == 200, r.text
    assert r.content == JPG
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith("inline;")
    assert "Screenshot_20261028_110935_Gallery.jpeg" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["cache-control"] == f"private, max-age={anexo_ml.CACHE_S}"
    [pedido] = ml.feitos
    assert pedido.method == "GET"
    assert str(pedido.url.copy_with(query=None)) == (
        f"https://api.mercadolibre.com/messages/attachments/{ARQUIVO}"
    )
    assert dict(pedido.url.params) == {"tag": "post_sale", "site_id": "MLB"}
    assert ml.bearer() == "Bearer tok-kfa"


async def test_token_e_o_da_loja_da_conversa(db, client, admin, monkeypatch):
    """Duas lojas do ML com o mesmo anexo: cada conversa vai com o token DELA."""
    ml = _MLFalso(monkeypatch)
    ml.arquivo(f"/messages/attachments/{ARQUIVO}", JPG, "image/jpeg")
    loja_a = await _loja(db, admin, "loja-a", "tok-a")
    loja_b = await _loja(db, admin, "loja-b", "tok-b")
    conversa_a = await _conversa(db, loja_a, externo_id="2000000000000001")
    conversa_b = await _conversa(db, loja_b, externo_id="2000000000000002")
    assert (await client.get(_rota(conversa_b))).status_code == 200
    assert ml.bearer() == "Bearer tok-b"
    assert (await client.get(_rota(conversa_a))).status_code == 200
    assert ml.bearer() == "Bearer tok-a"


async def test_anexo_da_reclamacao_pelo_download_da_reclamacao(db, client, admin, monkeypatch):
    ml = _MLFalso(monkeypatch)
    arquivo = "3cf94d52-0248-4bb4-98cc-b76c01ff5dc0.jpeg"
    ml.arquivo(f"/post-purchase/v1/claims/{CLAIM}/attachments/{arquivo}/download", PNG, "image/png")
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    conversa = await _conversa(
        db,
        integ,
        canal=CANAL_RECLAMACAO,
        externo_id=CLAIM,
        anexos=[{"arquivo": arquivo, "nome": "foto.jpg", "tipo": "image/jpeg", "tamanho": 17950}],
    )
    r = await client.get(_rota(conversa, arquivo))
    assert r.status_code == 200, r.text
    assert r.content == PNG
    # O tipo que o ML diz (da lista) vale mais que o guardado.
    assert r.headers["content-type"] == "image/png"
    [pedido] = ml.feitos
    assert pedido.url.path == f"/post-purchase/v1/claims/{CLAIM}/attachments/{arquivo}/download"
    assert not pedido.url.query
    assert ml.bearer() == "Bearer tok-kfa"


# ─────────────── o tipo: passa o do ML; HTML nunca; PDF baixa ───────────────


async def test_pdf_com_tipo_generico_vale_o_guardado_e_vai_como_attachment(
    db, client, admin, monkeypatch
):
    ml = _MLFalso(monkeypatch)
    arquivo = "118859700_aa11.pdf"
    ml.arquivo(f"/messages/attachments/{arquivo}", PDF, "application/octet-stream")
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    conversa = await _conversa(
        db,
        integ,
        anexos=[
            {
                "arquivo": arquivo,
                "nome": 'nota "fiscal"\r\nX-Injetado: 1.pdf',
                "tipo": "application/pdf",
                "tamanho": len(PDF),
            }
        ],
    )
    r = await client.get(_rota(conversa, arquivo))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert "\r" not in cd and "\n" not in cd and '"fiscal"' not in cd
    assert "x-injetado" not in {k.lower() for k in r.headers}
    assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"


async def test_html_nunca_passa_como_html(db, client, admin, monkeypatch):
    ml = _MLFalso(monkeypatch)
    ml.arquivo(f"/messages/attachments/{ARQUIVO}", b"<html><script>x</script>", "text/html")
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    conversa = await _conversa(db, integ, anexos=[{"arquivo": ARQUIVO, "nome": "a.jpeg"}])
    r = await client.get(_rota(conversa))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment;")


def test_tipo_da_resposta_pela_lista_pelo_guardado_e_pela_assinatura():
    assert anexo_ml.tipo_da_resposta("image/png; charset=binary", None, b"") == "image/png"
    assert anexo_ml.tipo_da_resposta("image/jpg", None, b"") == "image/jpeg"
    assert anexo_ml.tipo_da_resposta("", "image/jpeg", b"") == "image/jpeg"
    assert anexo_ml.tipo_da_resposta("application/octet-stream", None, PNG) == "image/png"
    assert anexo_ml.tipo_da_resposta("image/svg+xml", "image/svg+xml", b"<svg/>") == (
        "application/octet-stream"
    )
    assert anexo_ml.tipo_da_resposta(None, None, b"GIF89a....") == "image/gif"
    assert anexo_ml.tipo_da_resposta(None, None, b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert anexo_ml.tipo_da_resposta("image/heic", None, b"") == "image/heic"


# ─────────────── só o anexo DAQUELA conversa ───────────────


async def test_arquivo_de_outra_conversa_ou_inventado_e_404_sem_ir_ao_ml(
    db, client, admin, monkeypatch
):
    ml = _MLFalso(monkeypatch)
    ml.arquivo(f"/messages/attachments/{ARQUIVO}", JPG, "image/jpeg")
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    com_foto = await _conversa(db, integ, externo_id="2000000000000001")
    sem_foto = await _conversa(
        db, integ, externo_id="2000000000000002", anexos=[{"arquivo": "1_outro.jpeg"}]
    )
    # O arquivo é da OUTRA conversa: 404, e o ML nem é chamado.
    r = await client.get(_rota(sem_foto))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "anexo_nao_encontrado"
    # Um id inventado, um formato estranho e uma conversa que não existe.
    for ruim in ("999_inventado.jpeg", "a..b.jpeg", ".oculto", "x" * 201):
        assert (await client.get(_rota(com_foto, ruim))).status_code == 404, ruim
    assert (await client.get(f"{URL}/conversas/nao-e-uuid/anexos/{ARQUIVO}")).status_code == 404
    assert (await client.get(f"{URL}/conversas/ig:abc/anexos/{ARQUIVO}")).status_code == 404
    assert ml.feitos == []
    # O da própria conversa: vai.
    assert (await client.get(_rota(com_foto))).status_code == 200
    assert len(ml.feitos) == 1


async def test_nota_interna_com_arquivo_nao_abre_o_anexo(db, client, admin, monkeypatch):
    ml = _MLFalso(monkeypatch)
    ml.arquivo(f"/messages/attachments/{ARQUIVO}", JPG, "image/jpeg")
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    conversa = await _conversa(db, integ, anexos=[{"arquivo": "1_outro.jpeg"}])
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=None,
            autor=AUTOR_EQUIPE,
            origem=ORIGEM_NOTA,
            tipo=TIPO_NOTA,
            texto="olha a foto",
            anexos=[{"arquivo": ARQUIVO, "tipo": "image/jpeg"}],
            status="enviada",
        )
    )
    await db.commit()
    assert (await client.get(_rota(conversa))).status_code == 404
    assert ml.feitos == []


async def test_fora_do_ml_ou_da_caixa_com_anexo_e_404(db, client, admin, monkeypatch):
    ml = _MLFalso(monkeypatch)
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    pergunta = await _conversa(db, integ, canal="pergunta", externo_id="Q1")
    shopee = await _conversa(db, integ, plataforma="shopee", canal="chat", externo_id="S1")
    for conversa in (pergunta, shopee):
        assert (await client.get(_rota(conversa))).status_code == 404
    assert ml.feitos == []


# ─────────────── as travas de quem lê a conversa ───────────────


async def test_sem_login_401_e_quem_nao_ve_a_caixa_403(db, client, make_user, auth_as, monkeypatch):
    ml = _MLFalso(monkeypatch)
    dono = await make_user(role=UserRole.ADMIN)
    integ = await _loja(db, dono, "kfa", "tok-kfa")
    conversa = await _conversa(db, integ)
    assert (await client.get(_rota(conversa))).status_code == 401
    # O operador de estoque (a tela do atendimento nem existe para ele).
    operador = await make_user(role=UserRole.USER)
    operador.stock_tags = ["estoque"]
    await db.commit()
    auth_as(operador)
    r = await client.get(_rota(conversa))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "atendimento_restrito"
    assert ml.feitos == []


async def test_fora_do_escopo_da_equipe_e_404(db, client, make_user, auth_as, monkeypatch):
    ml = _MLFalso(monkeypatch)
    ml.arquivo(f"/messages/attachments/{ARQUIVO}", JPG, "image/jpeg")
    dono = await make_user(role=UserRole.ADMIN)
    da_equipe = await _loja(db, dono, "loja-da-equipe", "tok-a")
    de_fora = await _loja(db, dono, "loja-de-fora", "tok-b")
    minha = await _conversa(db, da_equipe, externo_id="2000000000000001")
    alheia = await _conversa(db, de_fora, externo_id="2000000000000002")
    ids = {da_equipe.id}

    async def escopo(session, user):
        return TeamScope(unrestricted=False, integration_ids=set(ids), email_user=user)

    monkeypatch.setattr(rota_atendimento, "escopo_do_atendimento", escopo)
    vendedora = await make_user(role=UserRole.USER)
    auth_as(vendedora)
    r = await client.get(_rota(alheia))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conversa_nao_encontrada"
    assert ml.feitos == []
    # Quem só lê (não admin) vê o anexo da conversa da equipe.
    assert (await client.get(_rota(minha))).status_code == 200
    assert ml.bearer() == "Bearer tok-a"


# ─────────────── os erros do ML ───────────────


async def test_erros_do_ml_viram_404_502_e_504(db, client, admin, monkeypatch):
    ml = _MLFalso(monkeypatch)
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    conversa = await _conversa(db, integ)
    chave = f"{anexo_ml.ML_HOST}/messages/attachments/{ARQUIVO}"
    casos = [
        (
            httpx.Response(422, json={"message": "Attachment key is invalid or not found"}),
            404,
            "anexo_nao_encontrado",
        ),
        (httpx.Response(404), 404, "anexo_nao_encontrado"),
        (httpx.Response(500, json={"message": "File can not be saved"}), 502, "anexo_indisponivel"),
        (httpx.Response(403), 502, "anexo_indisponivel"),
        (httpx.ReadTimeout("lento"), 504, "anexo_sem_resposta"),
        (httpx.ConnectError("fora"), 502, "anexo_indisponivel"),
        (
            httpx.Response(200, content=b"", headers={"content-type": "image/jpeg"}),
            502,
            "anexo_indisponivel",
        ),
        # Um JSON no lugar do arquivo (erro ou metadado): nunca vira a "foto".
        (httpx.Response(200, json={"filename": ARQUIVO}), 502, "anexo_indisponivel"),
    ]
    with capture_logs() as logs:
        for resposta, http, code in casos:
            ml.respostas[chave] = resposta
            r = await client.get(_rota(conversa))
            assert (r.status_code, r.json()["detail"]["code"]) == (http, code), resposta
    # O log só tem o id da conversa e os códigos: nada de token nem do anexo.
    texto = repr(logs)
    assert "tok-kfa" not in texto and ARQUIVO not in texto and "118859700" not in texto
    assert any(e.get("event") == "atendimento_anexo_ml_indisponivel" for e in logs)


async def test_acima_do_teto_nao_entrega(db, client, admin, monkeypatch):
    ml = _MLFalso(monkeypatch)
    monkeypatch.setattr(anexo_ml, "MAX_BYTES", 100)
    ml.arquivo(f"/messages/attachments/{ARQUIVO}", JPG, "image/jpeg")
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    conversa = await _conversa(db, integ, anexos=[{"arquivo": ARQUIVO, "tipo": "image/jpeg"}])
    r = await client.get(_rota(conversa))
    assert (r.status_code, r.json()["detail"]["code"]) == (502, "anexo_grande_demais")
    # Pelo tamanho que a leitura guardou: nem vai ao ML.
    outra = await _conversa(
        db,
        integ,
        externo_id="2000000000000009",
        anexos=[{"arquivo": "1_grande.jpeg", "tipo": "image/jpeg", "tamanho": 5000}],
    )
    antes = len(ml.feitos)
    r = await client.get(_rota(outra, "1_grande.jpeg"))
    assert (r.status_code, r.json()["detail"]["code"]) == (502, "anexo_grande_demais")
    assert len(ml.feitos) == antes


async def test_redirecionamento_segue_sem_o_token_para_outro_host(db, client, admin, monkeypatch):
    ml = _MLFalso(monkeypatch)
    cdn = "mla-s2-p.mlstatic.com"
    ml.respostas[f"{anexo_ml.ML_HOST}/messages/attachments/{ARQUIVO}"] = httpx.Response(
        302, headers={"location": f"https://{cdn}/anexo/abc.jpeg?assinatura=1"}
    )
    ml.arquivo("/anexo/abc.jpeg", JPG, "image/jpeg", host=cdn)
    integ = await _loja(db, admin, "kfa", "tok-kfa")
    conversa = await _conversa(db, integ)
    r = await client.get(_rota(conversa))
    assert r.status_code == 200, r.text
    assert r.content == JPG
    assert [p.url.host for p in ml.feitos] == [anexo_ml.ML_HOST, cdn]
    assert ml.bearer(0) == "Bearer tok-kfa"
    assert ml.bearer(1) is None
    # Para http (sem TLS): não segue.
    ml.feitos.clear()
    ml.respostas[f"{anexo_ml.ML_HOST}/messages/attachments/{ARQUIVO}"] = httpx.Response(
        302, headers={"location": f"http://{cdn}/anexo/abc.jpeg"}
    )
    r = await client.get(_rota(conversa))
    assert (r.status_code, r.json()["detail"]["code"]) == (502, "anexo_indisponivel")
    assert len(ml.feitos) == 1


def test_formato_do_arquivo_e_o_caminho_no_ml():
    assert anexo_ml.arquivo_valido(ARQUIVO)
    assert anexo_ml.arquivo_valido("3cf94d52-0248-4bb4-98cc-b76c01ff5dc0.jpeg")
    for ruim in ("", None, "../x", "a/b", "a..b", ".x", "a b", "a%2Fb", "x" * 201):
        assert not anexo_ml.arquivo_valido(ruim), ruim
