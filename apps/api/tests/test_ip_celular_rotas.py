# ruff: noqa: S105, S106  (senhas de teste, nada real)
"""Rotas e página do "Usar este IP no celular" (16/11/2026). O que estes
testes seguram:

- só ADMIN e só com a tela Empresas desbloqueada (senha extra);
- o link sai amarrado à empresa certa (pela empresa ou pela loja, pelo apelido)
  e com o proxy real CIFRADO no Redis; a senha real e o usuário real do proxy
  nunca aparecem em resposta, página ou log;
- empresa sem IP / sem senha do proxy / proxy HTTP / IP interno não gera;
- link curto de uso único: o GET (prévia do WhatsApp) não gasta, o POST gasta
  e redireciona (303) com um cookie SÓ daquele endereço; recarregar volta à
  mesma página só neste iPhone, sem cookie nada; vence se não abrir;
- a página manda instalar a proteção (módulo do Shadowrocket) e conferir o
  vazamento (IPv4, IPv6, WebRTC) antes do ML;
- lista com status e "Cortar agora" (apaga a credencial e avisa o
  intermediário pelo canal); abrir e cortar juntos: cortado vence;
- "venceu sem abrir" apaga a credencial e não conta no limite;
- o QR de novo só para quem gerou; a lista mostra de que IP abriram;
- de ponta a ponta: link → página → Shadowrocket (cliente SOCKS5) →
  intermediário → proxy da empresa, e o corte derruba a conexão aberta.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import urllib.parse
import uuid

import pytest
from structlog.testing import capture_logs

from app.config import get_settings
from app.ip_celular.comum import Chaves, Cofre
from app.ip_celular.relay import Relay
from app.main import app
from app.models import Company, StoreInfo, UserRole
from app.redis_client import redis
from app.routers import ip_celular as ip_celular_router
from app.security.cipher import encrypt_bytes
from app.security.senha_extra import require_empresas_unlock
from tests.ipcel_apoio import Eco, ProxyFalso, eco_ok, fechou, socks

CHAVE = "chave-de-teste-do-intermediario-0123456789"
SENHA_REAL = "SenhaRealDoProxy987"
USUARIO_REAL = "pxusuarioreal01"
URL = "/api/ip-celular"


@pytest.fixture(autouse=True)
async def ambiente(monkeypatch):
    s = get_settings()
    prefixo = f"davinci_test:ipcel:{uuid.uuid4().hex[:8]}:"
    monkeypatch.setattr(s, "ipcel_relay_key", CHAVE)
    monkeypatch.setattr(s, "ipcel_prefixo", prefixo)
    monkeypatch.setattr(s, "ipcel_host_publico", "app.hadken.com")
    monkeypatch.setattr(s, "ipcel_link_base", "https://app.hadken.com")
    monkeypatch.setattr(s, "ipcel_porta_publica", 21080)
    yield prefixo
    chaves = [k async for k in redis.scan_iter(match=prefixo + "*")]
    if chaves:
        await redis.delete(*chaves)


async def _empresa(db, apelido="Atlas", ip="45.60.1.10", senha=SENHA_REAL, **extra) -> Company:
    c = Company(
        razao_social=f"{apelido.upper()} LTDA",
        apelido=apelido,
        ip=ip,
        proxy_tipo=extra.pop("proxy_tipo", "socks5"),
        proxy_porta=extra.pop("proxy_porta", 7128),
        proxy_usuario=extra.pop("proxy_usuario", USUARIO_REAL),
        proxy_senha_enc=encrypt_bytes(senha.encode()) if senha else None,
        **extra,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


def _codigo(url: str) -> str:
    return url.rsplit("/", 1)[1]


async def _abrir(client, caminho: str) -> str:
    """O que o iPhone faz: toca no botão (POST), segue o 303 com o cookie."""
    p = await client.post(caminho)
    assert p.status_code == 303, p.text
    assert p.headers["location"] == caminho
    cookie = p.headers["set-cookie"]
    assert "HttpOnly" in cookie and f"Path={caminho}" in cookie and "SameSite=lax" in cookie
    g = await client.get(caminho)
    assert g.status_code == 200, g.text
    assert g.headers["cache-control"] == "no-store"
    return g.text


async def _item(client, link_id: str) -> dict:
    return next(x for x in (await client.get(f"{URL}/links")).json() if x["link_id"] == link_id)


def _credencial_da_pagina(html: str) -> tuple[str, str, str, int, str]:
    m = re.search(r"shadowrocket://add/socks://([A-Za-z0-9+/=]+)\?remarks=([^'\"&]+)", html)
    assert m, html
    bruto = base64.b64decode(m.group(1)).decode()
    cred, hostporta = bruto.rsplit("@", 1)
    usuario, senha = cred.split(":", 1)
    host, porta = hostporta.rsplit(":", 1)
    return usuario, senha, host, int(porta), urllib.parse.unquote(m.group(2))


# ---------------------------------------------------------------- permissão


async def test_so_admin(client, make_user, auth_as, db):
    c = await _empresa(db)
    u = await make_user(
        role=UserRole.USER, permissions={"empresa": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(u)
    assert (
        await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
    ).status_code == 403
    assert (await client.get(f"{URL}/links")).status_code == 403
    assert (await client.get(f"{URL}/alvo", params={"company_id": str(c.id)})).status_code == 403
    assert (await client.post(f"{URL}/links/abc/cortar")).status_code == 403
    assert (await client.get(f"{URL}/links/abc/qr")).status_code == 403


async def test_sem_a_senha_extra_trancado(client, make_user, auth_as, db):
    c = await _empresa(db)
    await _admin(make_user, auth_as)
    app.dependency_overrides.pop(require_empresas_unlock, None)
    r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "empresas_locked"
    assert (await client.get(f"{URL}/links")).status_code == 401
    assert (await client.post(f"{URL}/links/abc/cortar")).status_code == 401


# ---------------------------------------------------------------- criar


async def test_cria_link_sem_segredo_na_resposta_nem_no_log(
    client, make_user, auth_as, db, ambiente
):
    c = await _empresa(db)
    await _admin(make_user, auth_as)
    with capture_logs() as logs:
        r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 60})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "aguardando" and d["empresa"] == "Atlas" and d["minutos"] == 60
    assert d["nome"].startswith("Atlas · até ")
    assert d["url"].startswith("https://app.hadken.com/api/ipcel/")
    assert d["qr"].startswith("data:image/svg+xml")
    for segredo in (SENHA_REAL, USUARIO_REAL):
        assert segredo not in r.text
    # No Redis: a senha real só cifrada; da temporária, só o resumo.
    k = Chaves(ambiente)
    tudo = ""
    async for chave in redis.scan_iter(match=ambiente + "*"):
        tipo = await redis.type(chave)
        if tipo == "string":
            tudo += await redis.get(chave)
        elif tipo == "hash":
            tudo += json.dumps(await redis.hgetall(chave))
    assert SENHA_REAL not in tudo and USUARIO_REAL not in tudo
    cartao = json.loads(await redis.get(k.link(d["link_id"])))
    rec = json.loads(await redis.get(k.cred(cartao["usuario"])))
    up = Cofre(CHAVE).decifrar(rec["up"], f"up:{cartao['usuario']}")
    assert up == {"host": "45.60.1.10", "porta": 7128, "usuario": USUARIO_REAL, "senha": SENHA_REAL}
    assert 3590 <= await redis.ttl(k.cred(cartao["usuario"])) <= 3600
    assert 590 <= await redis.ttl(k.codigo(cartao["codigo"])) <= 600
    # Log: o link e a empresa, nunca senha, usuário ou código.
    texto = json.dumps(logs, default=str)
    assert "ipcel_link_criado" in texto
    for segredo in (SENHA_REAL, USUARIO_REAL, cartao["usuario"], cartao["codigo"]):
        assert segredo not in texto


@pytest.mark.parametrize(
    ("ajuste", "codigo"),
    [
        ({"senha": ""}, "proxy_sem_senha"),
        ({"ip": None}, "empresa_sem_ip"),
        ({"proxy_tipo": "http"}, "proxy_nao_socks5"),
    ],
)
async def test_empresa_que_nao_pode(client, make_user, auth_as, db, ajuste, codigo):
    c = await _empresa(db, apelido=f"Emp{uuid.uuid4().hex[:6]}", **ajuste)
    await _admin(make_user, auth_as)
    r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
    assert r.status_code == 409 and r.json()["detail"]["code"] == codigo
    alvo = (await client.get(f"{URL}/alvo", params={"company_id": str(c.id)})).json()
    assert alvo["pode"] is False and alvo["motivo"] == codigo


async def test_ip_interno_nao_gera(client, make_user, auth_as, db):
    """IP gravado antes da validação (ou mexido no banco) não vira porta para
    dentro: a API recusa e o intermediário também recusaria."""
    c = await _empresa(db, apelido="Interna", ip="45.60.1.11")
    await db.execute(Company.__table__.update().where(Company.id == c.id).values(ip="10.0.1.5"))
    await db.commit()
    await _admin(make_user, auth_as)
    r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ip_nao_publico"


async def test_desligado_sem_a_chave(client, make_user, auth_as, db, monkeypatch):
    c = await _empresa(db, apelido="SemChave", ip="45.60.1.12")
    await _admin(make_user, auth_as)
    monkeypatch.setattr(get_settings(), "ipcel_relay_key", "")
    r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "ipcel_desligado"
    alvo = (await client.get(f"{URL}/alvo", params={"company_id": str(c.id)})).json()
    assert alvo["ligado"] is False and alvo["pode"] is False


async def test_tempo_so_15_60_240(client, make_user, auth_as, db):
    c = await _empresa(db, apelido="Tempo", ip="45.60.1.13")
    await _admin(make_user, auth_as)
    r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 600})
    assert r.status_code == 422


async def test_pela_loja_acha_a_empresa_pelo_apelido(client, make_user, auth_as, db):
    admin = await _admin(make_user, auth_as)
    c = await _empresa(db, apelido="Atlas Loja", ip="45.60.1.14")
    si = StoreInfo(user_id=admin.id, platform="ml", account_name="atlasloja")
    sem = StoreInfo(user_id=admin.id, platform="ml", account_name="ninguem")
    db.add_all([si, sem])
    await db.commit()
    alvo = (await client.get(f"{URL}/alvo", params={"store_info_id": str(si.id)})).json()
    assert alvo["empresa_id"] == str(c.id) and alvo["nome"] == "ML Atlasloja" and alvo["pode"]
    r = await client.post(f"{URL}/links", json={"store_info_id": str(si.id), "minutos": 15})
    assert r.status_code == 201 and r.json()["empresa_id"] == str(c.id)
    assert r.json()["nome"].startswith("ML Atlasloja · até ")
    r2 = await client.post(f"{URL}/links", json={"store_info_id": str(sem.id), "minutos": 15})
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "loja_sem_empresa"
    # Os dois juntos (ou nenhum): recusa.
    r3 = await client.post(
        f"{URL}/links", json={"store_info_id": str(si.id), "company_id": str(c.id), "minutos": 15}
    )
    assert r3.status_code == 422


async def test_limite_de_links_por_empresa(client, make_user, auth_as, db):
    c = await _empresa(db, apelido="Limite", ip="45.60.1.15")
    await _admin(make_user, auth_as)
    for _ in range(3):
        assert (
            await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
        ).status_code == 201
    r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "muitos_links_empresa"


# ---------------------------------------------------------------- página do iPhone


async def test_link_de_uso_unico(client, make_user, auth_as, db):
    c = await _empresa(db, apelido="Unico", ip="45.60.1.16")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    caminho = "/api/ipcel/" + _codigo(d["url"])
    auth_as(None)  # a página abre no iPhone, sem login
    # GET (prévia do WhatsApp/iMessage) não gasta, e não mostra credencial.
    for _ in range(2):
        g = await client.get(caminho)
        assert g.status_code == 200
        assert "socks://" not in g.text and "shadowrocket://add" not in g.text
        assert "Unico · até" in g.text
        assert g.headers["cache-control"] == "no-store"
    html = await _abrir(client, caminho)
    usuario, senha, host, porta, nome = _credencial_da_pagina(html)
    assert host == "app.hadken.com" and porta == 21080 and nome.startswith("Unico · até ")
    assert usuario.startswith("dv") and len(senha) >= 28
    for segredo in (SENHA_REAL, USUARIO_REAL):
        assert segredo not in html
    assert "45.60.1.16" in html  # o IP esperado, para conferir antes do ML
    # Uso único: sem o cookie deste iPhone, nada (outro aparelho, encaminhado).
    client.cookies.clear()
    assert (await client.post(caminho)).status_code == 404
    g = await client.get(caminho)
    assert g.status_code == 404 and "socks://" not in g.text
    assert "shadowrocket://disconnect" in g.text  # mas dá para desligar
    # A tela vê "em uso" e de que IP abriram.
    await _admin(make_user, auth_as)
    itens = (await client.get(f"{URL}/links")).json()
    item = next(x for x in itens if x["link_id"] == d["link_id"])
    assert item["status"] == "em_uso" and item["aberto_as"] and item["aberto_ip"]


async def test_pagina_volta_ao_recarregar_so_neste_iphone(client, make_user, auth_as, db):
    """O iOS descarta a aba ao trocar para o Shadowrocket, ou a pessoa
    recarrega: a página volta (sem reenviar formulário), enquanto o IP valer."""
    c = await _empresa(db, apelido="Volta", ip="45.60.1.23")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    caminho = "/api/ipcel/" + _codigo(d["url"])
    auth_as(None)
    primeira = _credencial_da_pagina(await _abrir(client, caminho))
    for _ in range(3):  # recarregar
        g = await client.get(caminho)
        assert g.status_code == 200 and _credencial_da_pagina(g.text) == primeira
    # O Safari "enviar de novo o formulário": volta para a mesma página.
    p = await client.post(caminho)
    assert p.status_code == 303 and "set-cookie" not in p.headers
    # O cookie de um link não abre outro link.
    await _admin(make_user, auth_as)
    d2 = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    auth_as(None)
    outro = "/api/ipcel/" + _codigo(d2["url"])
    token = client.cookies.get(ip_celular_router.COOKIE)
    g2 = await client.get(outro, cookies={ip_celular_router.COOKIE: token})
    assert g2.status_code == 200 and "socks://" not in g2.text  # só o botão do outro
    # Cortado: a página diz e mostra só o Desligar.
    await _admin(make_user, auth_as)
    await client.post(f"{URL}/links/{d['link_id']}/cortar")
    auth_as(None)
    g3 = await client.get(caminho)
    assert g3.status_code == 200 and "foi cortado" in g3.text
    assert "socks://" not in g3.text and "shadowrocket://disconnect" in g3.text


async def test_pagina_protecao_e_conferencia_de_vazamento(client, make_user, auth_as, db):
    c = await _empresa(db, apelido="Vaza", ip="45.60.1.24")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    auth_as(None)
    html = await _abrir(client, "/api/ipcel/" + _codigo(d["url"]))
    modulo = "https://app.hadken.com/api/ipcel/davinci-sem-vazamento.sgmodule"
    assert "shadowrocket://install?module=" + urllib.parse.quote(modulo, safe="") in html
    assert "Ajustes (Settings) › UDP" in html and "Desativar STUN" in html
    assert "Configuração › UDP" not in html and "ipinfo.io" not in html
    assert "browserleaks.com/ip" in html and "browserleaks.com/webrtc" in html
    assert "qualquer IPv6 ou qualquer outro IP" in html
    # O módulo (sem segredo): UDP recusado em vez de DIRETO, QUIC bloqueado.
    m = await client.get("/api/ipcel/davinci-sem-vazamento.sgmodule")
    assert m.status_code == 200 and m.headers["content-type"].startswith("text/plain")
    assert "[General]" in m.text
    assert "udp-policy-not-supported-behaviour = REJECT" in m.text
    assert "block-quic = all-proxy" in m.text
    # Formato de reserva estilo v2rayN/Sub-Store: socks://BASE64(usuario:senha)@host:porta#nome
    usuario, senha, *_ = _credencial_da_pagina(html)
    mv2 = re.search(r"socks://([A-Za-z0-9%+/=]+)@app\.hadken\.com:21080#", html)
    assert mv2, html
    assert base64.b64decode(urllib.parse.unquote(mv2.group(1))).decode() == f"{usuario}:{senha}"


async def test_link_vence_se_ninguem_abrir(client, make_user, auth_as, db, ambiente):
    c = await _empresa(db, apelido="Vence", ip="45.60.1.17")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    codigo = _codigo(d["url"])
    k = Chaves(ambiente)
    await _passar_dos_10_minutos(k, d["link_id"])
    assert (await client.get(f"/api/ipcel/{codigo}")).status_code == 404
    assert (await client.post(f"/api/ipcel/{codigo}")).status_code == 404
    itens = (await client.get(f"{URL}/links")).json()
    assert next(x for x in itens if x["link_id"] == d["link_id"])["status"] == "nao_aberto"
    assert (await client.get("/api/ipcel/nao-e-um-codigo!")).status_code == 404


async def _passar_dos_10_minutos(k: Chaves, link_id: str) -> None:
    """O código venceu (Redis apagou) e já passou da folga."""
    cartao = json.loads(await redis.get(k.link(link_id)))
    await redis.delete(k.codigo(cartao["codigo"]))
    cartao["link_vence_em"] = int(time.time()) - 120
    await redis.set(k.link(link_id), json.dumps(cartao), ex=3600)


async def test_venceu_sem_abrir_nao_conta_no_limite(client, make_user, auth_as, db, ambiente):
    """3 QRs que a câmera não leu: depois de 10 minutos eles venceram e não
    podem travar a empresa no limite de 3 até o fim do tempo (4 h)."""
    c = await _empresa(db, apelido="Camera", ip="45.60.1.25")
    await _admin(make_user, auth_as)
    k = Chaves(ambiente)
    ids = []
    for _ in range(3):
        d = (
            await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 240})
        ).json()
        ids.append(d["link_id"])
    r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
    assert r.status_code == 409
    usuarios = []
    for i in ids:
        usuarios.append(json.loads(await redis.get(k.link(i)))["usuario"])
        await _passar_dos_10_minutos(k, i)
    itens = {x["link_id"]: x for x in (await client.get(f"{URL}/links")).json()}
    assert all(itens[i]["status"] == "nao_aberto" for i in ids)
    # Ninguém tem a senha: a credencial foi apagada.
    for u in usuarios:
        assert await redis.get(k.cred(u)) is None
    r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
    assert r.status_code == 201


async def test_abrir_e_cortar_juntos_cortado_vence(client, make_user, auth_as, db, ambiente):
    """Abrir (iPhone) e cortar (admin) no mesmo instante: um não apaga o
    outro — o cartão não é regravado, cada um grava o seu campo."""
    c = await _empresa(db, apelido="Corrida", ip="45.60.1.26")
    admin = await _admin(make_user, auth_as)
    k = Chaves(ambiente)
    from app.ip_celular import servico

    for _ in range(5):
        d = (
            await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
        ).json()
        codigo = _codigo(d["url"])
        await asyncio.gather(
            servico.abrir_codigo(codigo, ip="200.1.2.3"), servico.cortar(d["link_id"], admin)
        )
        item = next(x for x in await servico.listar(por=admin) if x["link_id"] == d["link_id"])
        assert item["status"] == "cortado"
        cartao = json.loads(await redis.get(k.link(d["link_id"])))
        assert "aberto_em" not in cartao and "cortado_em" not in cartao
    # A gravação atrasada do "aberto" depois do corte também não apaga o corte.
    await redis.hset(k.met(d["link_id"]), "aberto_em", int(time.time()))
    item = next(x for x in await servico.listar(por=admin) if x["link_id"] == d["link_id"])
    assert item["status"] == "cortado" and item["cortado_em"]


async def test_qr_de_novo_so_enquanto_ninguem_abriu(client, make_user, auth_as, db):
    c = await _empresa(db, apelido="Qr", ip="45.60.1.18")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    q = await client.get(f"{URL}/links/{d['link_id']}/qr")
    assert q.status_code == 200 and q.json()["url"] == d["url"]
    # A lista não leva o link nem o QR.
    lista = (await client.get(f"{URL}/links")).json()
    assert all(x["url"] is None and x["qr"] is None for x in lista)
    assert next(x for x in lista if x["link_id"] == d["link_id"])["meu"] is True
    await client.post("/api/ipcel/" + _codigo(d["url"]))
    q2 = await client.get(f"{URL}/links/{d['link_id']}/qr")
    assert q2.status_code == 409


async def test_qr_de_novo_so_quem_gerou(client, make_user, auth_as, db):
    """Outro admin com a senha extra não pega o link de um colega para gastar
    primeiro."""
    c = await _empresa(db, apelido="QrDono", ip="45.60.1.27")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    await _admin(make_user, auth_as)  # outro admin
    q = await client.get(f"{URL}/links/{d['link_id']}/qr")
    assert q.status_code == 403 and q.json()["detail"]["code"] == "so_quem_gerou"
    item = next(
        x for x in (await client.get(f"{URL}/links")).json() if x["link_id"] == d["link_id"]
    )
    assert item["meu"] is False and item["url"] is None


async def test_soltar_o_ip(client, make_user, auth_as, db, ambiente):
    c = await _empresa(db, apelido="Solta", ip="45.60.1.28")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    k = Chaves(ambiente)
    await redis.hset(
        k.met(d["link_id"]),
        mapping={
            "preso_ip": "200.1.1.1",
            "outro_ip": "200.9.9.9",
            "outro_ip_em": 1,
            "outro_ip_n": 3,
        },
    )
    item = next(
        x for x in (await client.get(f"{URL}/links")).json() if x["link_id"] == d["link_id"]
    )
    assert item["preso_ip"] == "200.1.1.1" and item["outro_ip"] == "200.9.9.9"
    assert item["outro_ip_n"] == 3
    r = await client.post(f"{URL}/links/{d['link_id']}/soltar")
    assert r.status_code == 200 and r.json()["preso_ip"] is None and r.json()["outro_ip"] is None
    assert await redis.hget(k.met(d["link_id"]), "preso_ip") is None
    await client.post(f"{URL}/links/{d['link_id']}/cortar")
    assert (await client.post(f"{URL}/links/{d['link_id']}/soltar")).status_code == 409


async def test_redis_proprio_do_recurso(monkeypatch):
    """Com IPCEL_REDIS_URL o recurso usa o Redis dele (o intermediário só
    alcança esse); vazio, o principal."""
    from app.ip_celular import servico

    assert await servico._redis() is redis
    monkeypatch.setattr(get_settings(), "ipcel_redis_url", "redis://ipcel_redis:6379/0")
    monkeypatch.setattr(servico, "_redis_proprio", None)
    proprio = await servico._redis()
    assert proprio is not redis
    assert proprio.connection_pool.connection_kwargs["host"] == "ipcel_redis"
    assert await servico._redis() is proprio
    await proprio.aclose()


async def test_pagina_escapa_o_nome(client, make_user, auth_as, db):
    c = await _empresa(db, apelido="<script>x</script>", ip="45.60.1.19")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})).json()
    g = await client.get("/api/ipcel/" + _codigo(d["url"]))
    assert "<script>x</script>" not in g.text and "&lt;script&gt;" in g.text


# ---------------------------------------------------------------- cortar


async def test_cortar_apaga_a_credencial_e_avisa_o_intermediario(
    client, make_user, auth_as, db, ambiente
):
    c = await _empresa(db, apelido="Corta", ip="45.60.1.20")
    await _admin(make_user, auth_as)
    d = (await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 240})).json()
    k = Chaves(ambiente)
    cartao = json.loads(await redis.get(k.link(d["link_id"])))
    pubsub = redis.pubsub()
    await pubsub.subscribe(k.canal_cortar)
    await pubsub.get_message(timeout=1)  # confirmação da assinatura
    r = await client.post(f"{URL}/links/{d['link_id']}/cortar")
    assert r.status_code == 200 and r.json()["status"] == "cortado"
    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert msg and msg["data"] == d["link_id"]
    await pubsub.aclose()
    assert await redis.get(k.cred(cartao["usuario"])) is None
    assert await redis.get(k.codigo(cartao["codigo"])) is None
    # O link curto também morreu.
    assert (await client.post("/api/ipcel/" + _codigo(d["url"]))).status_code == 404
    assert (await client.post(f"{URL}/links/naoexiste/cortar")).status_code == 404


def test_codigo_some_do_log_de_acesso():
    ip_celular_router.mascarar_codigo_no_access_log()
    registro = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", "/api/ipcel/AbCdEf123456_-xyz", "1.1", 200),
        None,
    )
    for filtro in logging.getLogger("uvicorn.access").filters:
        filtro.filter(registro)
    assert "AbCdEf123456" not in registro.getMessage()
    assert "/api/ipcel/***" in registro.getMessage()


# ---------------------------------------------------------------- ponta a ponta


class _RelayParaOProxyFalso(Relay):
    """O intermediário de verdade; só o TCP para 45.60.x.x vai para o proxy
    falso local (a regra do IP público do proxy continua valendo)."""

    destinos: dict[tuple[str, int], int] = {}

    async def _abrir_upstream(self, host, porta):
        return await asyncio.open_connection("127.0.0.1", self.destinos[(host, porta)])


async def test_ponta_a_ponta_link_pagina_shadowrocket_intermediario_e_corte(
    client, make_user, auth_as, db, ambiente
):
    eco = await Eco().iniciar()
    atlas = await ProxyFalso(USUARIO_REAL, SENHA_REAL, eco.porta).iniciar()
    outro = await ProxyFalso("pxoutro", "SenhaDoOutro", eco.porta).iniciar()
    c = await _empresa(db, apelido="Ponta", ip="45.60.1.21")
    await _empresa(
        db, apelido="Outra", ip="45.60.1.22", senha="SenhaDoOutro", proxy_usuario="pxoutro"
    )
    relay = _RelayParaOProxyFalso(
        redis, Cofre(CHAVE), prefixo=ambiente, checar_dns=False, bloquear=["46.225.188.20"]
    )
    relay.destinos = {("45.60.1.21", 7128): atlas.porta, ("45.60.1.22", 7128): outro.porta}
    servidor = await relay.iniciar("127.0.0.1", 0)
    porta = servidor.sockets[0].getsockname()[1]
    await asyncio.sleep(0.1)
    try:
        await _admin(make_user, auth_as)
        d = (
            await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
        ).json()
        pagina = await _abrir(client, "/api/ipcel/" + _codigo(d["url"]))
        usuario, senha, _host, _porta, _nome = _credencial_da_pagina(pagina)
        res = await socks(porta, usuario, senha, "www.mercadolivre.com.br", 443)
        assert res.rep == 0 and await eco_ok(res)
        # Saiu pelo proxy da Ponta, com a senha real dela; a Outra não viu nada.
        assert [(v.usuario, v.senha, v.destino) for v in atlas.vistos] == [
            (USUARIO_REAL, SENHA_REAL, "www.mercadolivre.com.br")
        ]
        assert outro.vistos == []
        # "Cortar agora" pela tela derruba a conexão aberta na hora.
        inicio = time.monotonic()
        r = await client.post(f"{URL}/links/{d['link_id']}/cortar")
        assert r.status_code == 200
        assert await fechou(res, prazo=3)
        assert time.monotonic() - inicio < 3
        assert (await socks(porta, usuario, senha)).auth == b"\x01\x01"
        await relay.vigiar_uma_vez()
        item = next(
            x for x in (await client.get(f"{URL}/links")).json() if x["link_id"] == d["link_id"]
        )
        assert item["status"] == "cortado" and item["conexoes"] == 1 and item["bytes_sobe"] > 0
        assert item["preso_ip"] == "127.0.0.1"  # preso ao IP do "iPhone"
    finally:
        servidor.close()
        await relay.fechar()
        await servidor.wait_closed()
        await atlas.fechar()
        await outro.fechar()
        await eco.fechar()


async def test_historico_registra_gerar_qr_e_cortar(client, make_user, db):
    """Tudo no Redis, nada no banco: sem as frases em historico/nomes.py não
    ficaria rastro de quem gerou, viu o QR de novo e cortou."""
    from sqlalchemy import select

    from app.models import HistoricoEvento
    from app.security.jwt import issue_session_token

    c = await _empresa(db, apelido="Rastro", ip="45.60.1.29")
    eu = await make_user(role=UserRole.ADMIN)
    token, _, _ = issue_session_token(sub=eu.open_id, role=eu.role.value)
    client.cookies.set(get_settings().cookie_name, token)

    async def _liberado():
        return None

    app.dependency_overrides[require_empresas_unlock] = _liberado
    try:
        r = await client.post(f"{URL}/links", json={"company_id": str(c.id), "minutos": 15})
        assert r.status_code == 201, r.text
        link_id = r.json()["link_id"]
        assert (await client.get(f"{URL}/links/{link_id}/qr")).status_code == 200
        assert (await client.post(f"{URL}/links/{link_id}/cortar")).status_code == 200
    finally:
        app.dependency_overrides.pop(require_empresas_unlock, None)
    q = select(HistoricoEvento).where(HistoricoEvento.ator_id == eu.id).order_by(HistoricoEvento.id)
    evs = (await db.execute(q.execution_options(populate_existing=True))).scalars().all()
    assert [(e.acao, e.tela) for e in evs] == [
        ("gerou link para usar o IP da empresa no celular", "Cadastros › Empresas"),
        ("viu de novo o QR do IP da empresa no celular", "Cadastros › Empresas"),
        ("cortou o IP da empresa no celular", "Cadastros › Empresas"),
    ]
    assert evs[0].itens_texto == "Rastro"
