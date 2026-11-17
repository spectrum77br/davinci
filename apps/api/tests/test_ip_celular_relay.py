# ruff: noqa: S104, S105, S106  (senhas e endereços de teste, nada real)
"""Intermediário SOCKS5 do "Usar este IP no celular" (16/11/2026), de verdade:
sockets locais, o Redis local e um proxy SOCKS5 de mentira no papel do proxy
da empresa. O que estes testes seguram:

- a credencial temporária leva SÓ ao proxy da sua empresa, com a senha real
  dela; trocar o proxy cifrado de uma credencial pelo de outra não funciona;
- senha errada recusa; 10 erros bloqueiam o IP para senhas erradas, mas a
  credencial CERTA passa (CGNAT do 4G) e credencial que venceu não conta;
- quem não autenticou não tranca o celular: vagas separadas, 5 s de prazo e a
  mais antiga sai quando enche; limite por IP;
- a credencial fica presa ao primeiro IP que entrou; outro IP é recusado e
  anotado (a tela solta);
- destino interno (IP privado, loopback, link-local, do servidor, nome sem
  ponto, localhost, IPv4 disfarçado, nome que aponta para dentro) é recusado
  sem nem chamar o proxy da empresa; proxy da empresa interno também;
- só TCP CONNECT (UDP ASSOCIATE e BIND recusados) e só usuário/senha;
- no fim do prazo e no "Cortar agora" as conexões ABERTAS caem, e a que já
  autenticou e ainda ia pedir o destino também;
- limites de conexões (também com pedidos juntos), banda e teto de dados;
- nome que resolve para o proxy da empresa, DNS que não responde e nome com
  byte NUL: recusados sem traceback;
- Redis fora = ninguém entra; falha só ao GRAVAR os números não derruba;
- nenhum segredo no log.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import time
import uuid

import pytest
from redis.asyncio import from_url

from app.ip_celular.comum import (
    Chaves,
    Cofre,
    gravar_link,
    motivo_destino_bloqueado,
    motivo_upstream_bloqueado,
    redes_extras,
)
from app.ip_celular.relay import Recusa, Relay
from app.redis_client import redis
from tests.ipcel_apoio import Eco, ProxyFalso, eco_ok, fechou, socks

CHAVE = "chave-de-teste-do-intermediario-0123456789"
COFRE = Cofre(CHAVE)


@pytest.fixture
async def prefixo():
    p = f"davinci_test:ipcel:{uuid.uuid4().hex[:8]}:"
    yield p
    chaves = [k async for k in redis.scan_iter(match=p + "*")]
    if chaves:
        await redis.delete(*chaves)


@pytest.fixture
async def eco():
    e = await Eco().iniciar()
    yield e
    await e.fechar()


@pytest.fixture
async def proxies(eco):
    criados: list[ProxyFalso] = []

    async def _novo(usuario: str, senha: str, **kw) -> ProxyFalso:
        p = await ProxyFalso(usuario, senha, eco.porta, **kw).iniciar()
        criados.append(p)
        return p

    yield _novo
    for p in criados:
        await p.fechar()


@pytest.fixture
async def relay_rodando(prefixo):
    abertos: list[tuple[Relay, asyncio.base_events.Server]] = []

    async def _novo(**kw) -> tuple[Relay, int]:
        opcoes = {
            "prefixo": prefixo,
            "permitir_upstream_local": True,
            "checar_dns": False,
            "vigia_s": 0.2,
            "bloquear": ["46.225.188.20"],
        }
        opcoes.update(kw)
        cliente = opcoes.pop("redis", redis)
        r = Relay(cliente, COFRE, **opcoes)
        servidor = await r.iniciar("127.0.0.1", 0)
        abertos.append((r, servidor))
        await asyncio.sleep(0.1)  # o canal de corte assina
        return r, servidor.sockets[0].getsockname()[1]

    yield _novo
    for r, s in abertos:
        s.close()
        await r.fechar()
        await s.wait_closed()


async def _credencial(
    prefixo: str,
    proxy: ProxyFalso,
    *,
    usuario: str | None = None,
    senha: str = "SenhaTemporaria123456789abc",
    segundos: int = 600,
    max_conexoes: int = 48,
    banda: int = 50_000_000,
    teto: int = 2_000_000_000,
    host: str = "127.0.0.1",
) -> tuple[str, str, str]:
    usuario = usuario or "dv" + uuid.uuid4().hex[:14]
    link_id = uuid.uuid4().hex[:12]
    await gravar_link(
        redis,
        Chaves(prefixo),
        COFRE,
        link_id=link_id,
        codigo=uuid.uuid4().hex,
        usuario=usuario,
        senha=senha,
        upstream={
            "host": host,
            "porta": proxy.porta,
            "usuario": proxy.usuario,
            "senha": proxy.senha,
        },
        expira=int(time.time()) + segundos,
        meta={"empresa_id": "e1", "empresa": "Atlas", "nome": "ML Atlas"},
        max_conexoes=max_conexoes,
        banda_bytes_s=banda,
        teto_bytes=teto,
    )
    return usuario, senha, link_id


# ---------------------------------------------------------------- passa


async def test_conecta_e_repassa_pelo_proxy_da_empresa(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando()
    u, s, link = await _credencial(prefixo, atlas)
    res = await socks(porta, u, s, "www.mercadolivre.com.br", 443)
    assert res.metodo == b"\x05\x02" and res.auth == b"\x01\x00" and res.rep == 0
    assert await eco_ok(res, b"x" * 5000)
    # O nome do site seguiu para o proxy da empresa (DNS resolvido LÁ).
    assert [(v.usuario, v.senha, v.destino, v.porta) for v in atlas.vistos] == [
        ("pxatlas", "SenhaRealAtlas1", "www.mercadolivre.com.br", 443)
    ]
    await relay.vigiar_uma_vez()
    met = await redis.hgetall(Chaves(prefixo).met(link))
    assert int(met["sobe"]) == 5000 and int(met["desce"]) == 5000
    assert int(met["conexoes"]) == 1 and int(met["abertas"]) == 1
    res.fechar()


async def test_so_a_empresa_certa(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    fiore = await proxies("pxfiore", "SenhaRealFiore2")
    _relay, porta = await relay_rodando()
    ua, sa, _ = await _credencial(prefixo, atlas, senha="TempAtlas111111111111")
    uf, sf, _ = await _credencial(prefixo, fiore, senha="TempFiore222222222222")
    ra = await socks(porta, ua, sa)
    rf = await socks(porta, uf, sf)
    assert ra.rep == 0 and rf.rep == 0
    assert [v.usuario for v in atlas.vistos] == ["pxatlas"]
    assert [v.usuario for v in fiore.vistos] == ["pxfiore"]
    ra.fechar()
    rf.fechar()
    # Senha temporária de uma com o usuário da outra: nada.
    cruzado = await socks(porta, ua, sf)
    assert cruzado.auth == b"\x01\x01"
    # Trocar no Redis o proxy cifrado da Atlas pelo da Fiore não funciona: a
    # cifra está amarrada ao usuário temporário.
    k = Chaves(prefixo)
    rec_a = json.loads(await redis.get(k.cred(ua)))
    rec_f = json.loads(await redis.get(k.cred(uf)))
    rec_a["up"] = rec_f["up"]
    await redis.set(k.cred(ua), json.dumps(rec_a), ex=600)
    trocado = await socks(porta, ua, sa)
    assert trocado.auth == b"\x01\x01"
    assert len(fiore.vistos) == 1  # a Fiore não recebeu a conexão da Atlas


# ---------------------------------------------------------------- senha


async def test_senha_errada_e_usuario_que_nao_existe(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, _s, _ = await _credencial(prefixo, atlas)
    assert (await socks(porta, u, "errada")).auth == b"\x01\x01"
    assert (await socks(porta, "dvnaoexiste123456", "x")).auth == b"\x01\x01"
    assert (await socks(porta, "a:b@c", "x")).auth == b"\x01\x01"
    assert atlas.vistos == []


async def test_dez_erros_bloqueiam_o_ip_mas_a_credencial_certa_passa(
    prefixo, proxies, relay_rodando, caplog
):
    """No 4G muitos celulares dividem o mesmo IP (CGNAT): um robô chutando
    senhas por ele não pode trancar o iPhone com a credencial certa."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando(max_falhas_ip=10)
    u, s, _ = await _credencial(prefixo, atlas)
    for _ in range(10):
        assert (await socks(porta, u, "chute")).auth == b"\x01\x01"
    with caplog.at_level(logging.INFO, logger="ipcel.relay"):
        assert (await socks(porta, u, "chute")).auth == b"\x01\x01"
        assert (await socks(porta, "dvnaoexiste123456", "x")).auth == b"\x01\x01"
    assert "ip_bloqueado_por_tentativas" in caplog.text
    certo = await socks(porta, u, s)
    assert certo.auth == b"\x01\x00" and certo.rep == 0
    assert len(atlas.vistos) == 1
    certo.fechar()


async def test_credencial_vencida_nao_conta_como_tentativa(prefixo, proxies, relay_rodando):
    """O iPhone insistindo com a credencial antiga não pode bloquear o IP dele
    antes de importar o link novo."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando(max_falhas_ip=3)
    velho, sv, _ = await _credencial(prefixo, atlas)
    await redis.delete(Chaves(prefixo).cred(velho))  # venceu/cortado
    for _ in range(10):
        assert (await socks(porta, velho, sv)).auth == b"\x01\x01"
    novo, sn, _ = await _credencial(prefixo, atlas)
    assert (await socks(porta, novo, sn)).rep == 0


# ---------------------------------------------------------------- destinos


@pytest.mark.parametrize(
    "destino",
    [
        "127.0.0.1",
        "10.0.1.5",
        "172.18.0.2",
        "192.168.0.10",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "46.225.188.20",  # o próprio servidor
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "localhost",
        "LocalHost.",
        "redis7",
        "api",
        "pg18",
        "algo.localhost",
        "impressora.local",
        "metadata.google.internal",
        "2130706433",
        "0x7f.1",
        "127.1",
    ],
)
async def test_destino_interno_recusado_sem_chamar_o_proxy(
    destino, prefixo, proxies, relay_rodando
):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas)
    res = await socks(porta, u, s, destino, 80)
    assert res.auth == b"\x01\x00"
    assert res.rep == 0x02  # "não permitido pelas regras"
    assert atlas.vistos == []
    res.fechar()


async def test_porta_25_recusada(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas)
    assert (await socks(porta, u, s, "smtp.exemplo.com", 25)).rep == 0x02


async def test_destino_igual_ao_proxy_da_empresa_recusado(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas, host="45.60.1.10")
    # Sem a liberação de teste o 45.60.1.10 seria o proxy; aqui só a regra.
    res = await socks(porta, u, s, "45.60.1.10", 22)
    assert res.rep == 0x02


async def test_nome_que_aponta_para_dentro(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando(checar_dns=True)

    async def _dns(nome, porta):
        return ["127.0.0.1"] if nome == "localtest.me" else ["200.147.35.149"]

    relay._resolver = _dns  # type: ignore[method-assign]
    u, s, _ = await _credencial(prefixo, atlas)
    assert (await socks(porta, u, s, "localtest.me", 80)).rep == 0x02
    ok = await socks(porta, u, s, "www.uol.com.br", 443)
    assert ok.rep == 0
    ok.fechar()
    assert [v.destino for v in atlas.vistos] == ["www.uol.com.br"]


def test_regras_de_destino():
    extras = redes_extras(["46.225.188.20"])
    assert motivo_destino_bloqueado("www.mercadolivre.com.br", 443, extras) is None
    assert motivo_destino_bloqueado(ipaddress.ip_address("200.147.35.149"), 443, extras) is None
    assert motivo_destino_bloqueado("46.225.188.20", 443, extras) == "ip_do_servidor"
    assert motivo_destino_bloqueado("10.0.0.1", 443, extras) == "ip_interno"
    assert (
        motivo_destino_bloqueado("64:ff9b::a00:1", 443, extras) == "ip_interno"
    )  # NAT64 de 10.0.0.1
    assert motivo_destino_bloqueado("2002:a00:1::", 443, extras) == "ip_interno"  # 6to4 de 10.0.0.1
    assert motivo_destino_bloqueado("redis7", 6379, extras) == "nome_sem_dominio"
    assert motivo_destino_bloqueado("x.home.arpa", 80, extras) == "nome_interno"
    assert motivo_destino_bloqueado("a b.com", 80, extras) == "nome_invalido"
    assert motivo_destino_bloqueado("exemplo.com", 0, extras) == "porta_bloqueada"
    # O proxy da empresa: só IP público e nunca o servidor.
    assert motivo_upstream_bloqueado("45.60.1.10", extras) is None
    assert motivo_upstream_bloqueado("127.0.0.1", extras) == "ip_interno"
    assert motivo_upstream_bloqueado("46.225.188.20", extras) == "ip_do_servidor"
    assert motivo_upstream_bloqueado("redis7", extras) == "upstream_nao_e_ip"


async def test_proxy_da_empresa_interno_recusado(prefixo, proxies, relay_rodando):
    """Sem a liberação de teste local, credencial cujo proxy é 127.0.0.1 (ou
    a rede do docker) não autentica: o intermediário não vira porta para
    dentro do servidor."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando(permitir_upstream_local=False)
    u, s, _ = await _credencial(prefixo, atlas, host="127.0.0.1")
    assert (await socks(porta, u, s)).auth == b"\x01\x01"
    u2, s2, _ = await _credencial(prefixo, atlas, host="172.18.0.5")
    assert (await socks(porta, u2, s2)).auth == b"\x01\x01"
    assert atlas.vistos == []


# ---------------------------------------------------------------- protocolo


async def test_so_tcp_connect_e_so_usuario_senha(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas)
    assert (await socks(porta, u, s, cmd=3)).rep == 0x07  # UDP ASSOCIATE
    assert (await socks(porta, u, s, cmd=2)).rep == 0x07  # BIND
    sem_senha = await socks(porta, None, None, metodos=b"\x00")
    assert sem_senha.metodo == b"\x05\xff"
    assert atlas.vistos == []


async def test_proxy_da_empresa_recusa_a_senha_real(prefixo, proxies, relay_rodando, caplog):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas)
    atlas.senha = "trocaram-no-provedor"
    with caplog.at_level(logging.INFO, logger="ipcel.relay"):
        res = await socks(porta, u, s)
    assert res.auth == b"\x01\x00" and res.rep == 0x01
    assert "upstream_recusou_senha" in caplog.text


async def test_destino_que_o_proxy_nao_alcanca(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1", rep=0x04)
    _relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas)
    assert (await socks(porta, u, s)).rep == 0x04


# ---------------------------------------------------------------- prazo e corte


async def test_prazo_derruba_a_conexao_aberta_e_recusa_as_novas(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas, segundos=2)
    res = await socks(porta, u, s)
    assert res.rep == 0 and await eco_ok(res)
    inicio = time.monotonic()
    assert await fechou(res, prazo=5)
    assert time.monotonic() - inicio < 4
    await asyncio.sleep(0.5)
    assert (await socks(porta, u, s)).auth == b"\x01\x01"


async def test_cortar_agora_derruba_na_hora(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando(vigia_s=60)  # só o canal de corte
    u, s, link = await _credencial(prefixo, atlas)
    a = await socks(porta, u, s)
    b = await socks(porta, u, s, "outra.exemplo.com", 80)
    assert a.rep == 0 and b.rep == 0
    k = Chaves(prefixo)
    await redis.delete(k.cred(u))
    await redis.publish(k.canal_cortar, link)
    inicio = time.monotonic()
    assert await fechou(a, prazo=2) and await fechou(b, prazo=2)
    assert time.monotonic() - inicio < 2
    assert (await socks(porta, u, s)).auth == b"\x01\x01"


async def test_corte_derruba_na_hora_mesmo_com_o_celular_sem_ler(prefixo, proxies, relay_rodando):
    """Celular parado (não lê) com o buffer cheio: o corte não espera o
    fechamento educado — derruba na hora."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando(vigia_s=60)
    u, s, link = await _credencial(prefixo, atlas)
    res = await socks(porta, u, s)
    assert res.rep == 0

    async def _empurrar():
        try:
            for _ in range(200):
                res.writer.write(b"w" * 65536)
                await res.writer.drain()
        except (ConnectionError, OSError):
            return

    empurrando = asyncio.create_task(_empurrar())
    await asyncio.sleep(0.5)  # o eco devolve e o buffer até o celular enche
    k = Chaves(prefixo)
    await redis.delete(k.cred(u))
    await redis.publish(k.canal_cortar, link)
    inicio = time.monotonic()
    while relay._links[link].conexoes and time.monotonic() - inicio < 5:
        await asyncio.sleep(0.02)
    assert not relay._links[link].conexoes
    assert time.monotonic() - inicio < 1.0
    empurrando.cancel()
    res.fechar()


async def test_corte_sem_mensagem_cai_pela_vigia(prefixo, proxies, relay_rodando):
    """Se a mensagem do canal se perder, a conferência a cada poucos segundos
    vê que a credencial sumiu e derruba."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando(vigia_s=60)
    u, s, _ = await _credencial(prefixo, atlas)
    res = await socks(porta, u, s)
    assert res.rep == 0
    await redis.delete(Chaves(prefixo).cred(u))
    await relay.vigiar_uma_vez()
    assert await fechou(res, prazo=2)


# ---------------------------------------------------------------- limites


async def test_limite_de_conexoes_por_credencial(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas, max_conexoes=2)
    a = await socks(porta, u, s)
    b = await socks(porta, u, s)
    c = await socks(porta, u, s)
    assert (a.rep, b.rep, c.rep) == (0, 0, 0x01)
    a.fechar()
    await asyncio.sleep(0.2)
    d = await socks(porta, u, s)
    assert d.rep == 0
    for x in (b, d):
        x.fechar()


async def test_banda_por_credencial(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    # 200 KB/s somando os dois sentidos: 300 KB ida e volta = 600 KB, com o
    # balde começando cheio (200 KB) → pelo menos ~2 s.
    u, s, _ = await _credencial(prefixo, atlas, banda=200_000)
    res = await socks(porta, u, s)
    inicio = time.monotonic()
    assert await eco_ok(res, b"z" * 300_000)
    assert time.monotonic() - inicio >= 1.5
    res.fechar()


async def test_teto_de_dados_corta_o_link(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando()
    u, s, link = await _credencial(prefixo, atlas, teto=100_000)
    res = await socks(porta, u, s)
    res.writer.write(b"y" * 200_000)
    assert await fechou(res, prazo=5)
    await asyncio.sleep(0.3)
    k = Chaves(prefixo)
    assert await redis.get(k.cred(u)) is None
    assert (await redis.hget(k.met(link), "teto")) == "1"


# ---------------------------------------------------------------- falha fechada e log


async def test_redis_fora_ninguem_entra(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    u, s, _ = await _credencial(prefixo, atlas)
    fora = from_url("redis://127.0.0.1:1/0", decode_responses=True)
    _relay, porta = await relay_rodando(redis=fora)
    assert (await socks(porta, u, s)).auth == b"\x01\x01"
    assert atlas.vistos == []
    await fora.aclose()


async def test_log_sem_segredo(prefixo, proxies, relay_rodando, caplog):
    atlas = await proxies("pxrealatlas", "SenhaRealSecreta987")
    relay, porta = await relay_rodando()
    u, s, link = await _credencial(prefixo, atlas, senha="TempSecretaXYZ123456789")
    with caplog.at_level(logging.DEBUG, logger="ipcel.relay"):
        ok = await socks(porta, u, s)
        assert ok.rep == 0 and await eco_ok(ok)
        ok.fechar()
        await socks(porta, u, "SenhaChutada000")
        await socks(porta, u, s, "127.0.0.1", 22)
        atlas.senha = "outra"
        await socks(porta, u, s)
        await relay.vigiar_uma_vez()
        await asyncio.sleep(0.2)
    texto = caplog.text
    assert link in texto  # identifica pelo link
    for segredo in (
        "SenhaRealSecreta987",
        "pxrealatlas",
        "TempSecretaXYZ123456789",
        u,
        "SenhaChutada000",
        str(atlas.porta),
    ):
        assert segredo not in texto


# ---------------------------------------------------------------- achados da revisão (16/11)


async def _parado(porta: int):
    """Conexão que entra e não manda nada (segura uma vaga sem senha)."""
    return await asyncio.open_connection("127.0.0.1", porta)


async def test_conexoes_sem_senha_nao_trancam_o_celular(prefixo, proxies, relay_rodando):
    """Sem credencial nenhuma, encher as vagas de quem não autenticou não
    deixa o iPhone de fora: quando enche, sai a mais antiga."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando(
        max_pre_auth=16, max_pre_auth_por_ip=1000, max_pre_auth_por_rede=1000
    )
    u, s, _ = await _credencial(prefixo, atlas)
    paradas = [await _parado(porta) for _ in range(16)]
    await asyncio.sleep(0.2)
    assert len(relay._pre) == 16
    inicio = time.monotonic()
    res = await asyncio.wait_for(socks(porta, u, s), 5)
    assert res.auth == b"\x01\x00" and res.rep == 0
    assert time.monotonic() - inicio < 2
    assert await eco_ok(res)
    assert len(relay._pre) <= 16  # a mais antiga saiu para o celular entrar
    res.fechar()
    for _r, w in paradas:
        w.transport.abort()


async def test_quem_nao_autentica_sai_em_poucos_segundos(prefixo, proxies, relay_rodando):
    _relay, porta = await relay_rodando(handshake_s=0.5)
    r, w = await _parado(porta)
    inicio = time.monotonic()
    try:
        dado = await asyncio.wait_for(r.read(10), 3)
    except (ConnectionError, OSError):
        dado = b""
    assert dado == b"" and time.monotonic() - inicio < 2
    w.transport.abort()


async def test_limite_de_vagas_sem_senha_por_ip(prefixo, proxies, relay_rodando):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando(max_pre_auth_por_ip=4)
    paradas = [await _parado(porta) for _ in range(4)]
    await asyncio.sleep(0.2)
    r5, w5 = await _parado(porta)
    try:
        dado = await asyncio.wait_for(r5.read(10), 2)
    except (ConnectionError, OSError):
        dado = b""
    assert dado == b""  # a 5ª do mesmo IP nem entra
    assert len(relay._pre) == 4
    for _r, w in [*paradas, (r5, w5)]:
        w.transport.abort()
    await asyncio.sleep(0.2)
    u, s, _ = await _credencial(prefixo, atlas)
    ok = await socks(porta, u, s)
    assert ok.rep == 0  # as vagas voltaram
    ok.fechar()


async def test_limite_de_conexoes_com_pedidos_juntos(prefixo, proxies, relay_rodando):
    """A vaga é reservada antes do DNS: 20 pedidos ao mesmo tempo com limite 2
    abrem só 2."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando(checar_dns=True)

    async def _dns_lento(nome, porta):
        await asyncio.sleep(0.3)
        return ["200.147.35.149"]

    relay._resolver = _dns_lento  # type: ignore[method-assign]
    u, s, _ = await _credencial(prefixo, atlas, max_conexoes=2)
    todos = await asyncio.gather(*(socks(porta, u, s, "site.exemplo.com", 443) for _ in range(20)))
    assert sum(1 for x in todos if x.rep == 0) == 2
    assert len(atlas.vistos) == 2
    for x in todos:
        x.fechar()


async def _so_autenticar(porta: int, u: str, s: str):
    r, w = await asyncio.open_connection("127.0.0.1", porta)
    w.write(b"\x05\x01\x02")
    await w.drain()
    assert await r.readexactly(2) == b"\x05\x02"
    ub, sb = u.encode(), s.encode()
    w.write(b"\x01" + bytes([len(ub)]) + ub + bytes([len(sb)]) + sb)
    await w.drain()
    assert await r.readexactly(2) == b"\x01\x00"
    return r, w


async def _pedir(r, w, nome: bytes = b"loja.exemplo.com.br") -> int | None:
    try:
        w.write(b"\x05\x01\x00\x03" + bytes([len(nome)]) + nome + b"\x01\xbb")
        await w.drain()
        cab = await asyncio.wait_for(r.readexactly(10), 3)
        return cab[1]
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        return None


async def test_corte_pega_quem_autenticou_e_ainda_ia_pedir(prefixo, proxies, relay_rodando):
    """Autenticou, o admin cortou, e só depois veio o pedido: não abre."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando(vigia_s=60)
    u, s, link = await _credencial(prefixo, atlas)
    r, w = await _so_autenticar(porta, u, s)
    k = Chaves(prefixo)
    await redis.delete(k.cred(u))
    await redis.publish(k.canal_cortar, link)
    await asyncio.sleep(0.3)
    assert await _pedir(r, w) != 0
    assert atlas.vistos == []
    w.transport.abort()


async def test_corte_sem_mensagem_no_meio_da_negociacao(prefixo, proxies, relay_rodando):
    """A mensagem do canal se perdeu: a conferência da credencial antes de
    abrir o proxy da empresa recusa mesmo assim."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    _relay, porta = await relay_rodando(vigia_s=60)
    u, s, _ = await _credencial(prefixo, atlas)
    r, w = await _so_autenticar(porta, u, s)
    await redis.delete(Chaves(prefixo).cred(u))  # cortado, sem PUBLISH
    assert await _pedir(r, w) == 0x01
    assert atlas.vistos == []
    w.transport.abort()


async def test_nome_que_resolve_para_o_proxy_da_empresa(prefixo, proxies, eco):
    """O destino não pode ser o proxy da empresa, nem por IP escrito nem por
    um nome que resolve para ele."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")

    class _R(Relay):
        async def _abrir_upstream(self, host, porta):
            return await asyncio.open_connection("127.0.0.1", atlas.porta)

        async def _resolver(self, nome, porta):
            return {"8-8-4-4.sslip.io": ["8.8.4.4"]}.get(nome, ["200.147.35.149"])

    relay = _R(redis, COFRE, prefixo=prefixo, checar_dns=True, bloquear=["46.225.188.20"])
    servidor = await relay.iniciar("127.0.0.1", 0)
    porta = servidor.sockets[0].getsockname()[1]
    try:
        u, s, _ = await _credencial(prefixo, atlas, host="8.8.4.4")
        assert (await socks(porta, u, s, "8.8.4.4", 8080)).rep == 0x02
        assert (await socks(porta, u, s, "::ffff:8.8.4.4", 8080)).rep == 0x02
        assert (await socks(porta, u, s, "8-8-4-4.sslip.io", 8080)).rep == 0x02
        ok = await socks(porta, u, s, "www.mercadolivre.com.br", 443)
        assert ok.rep == 0
        ok.fechar()
        assert [v.destino for v in atlas.vistos] == ["www.mercadolivre.com.br"]
    finally:
        servidor.close()
        await relay.fechar()
        await servidor.wait_closed()


async def test_dns_que_nao_responde_recusa(prefixo, proxies, relay_rodando):
    """Falha fechada: nome que demora (rebinding lento) ou não existe daqui
    não segue para o proxy da empresa (código 04)."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando(checar_dns=True, dns_s=0.3)

    async def _dns(nome, porta):
        if nome.startswith("lento."):
            await asyncio.sleep(2)
            return ["127.0.0.1"]
        raise socket.gaierror("nao existe")

    relay._resolver = _dns  # type: ignore[method-assign]
    u, s, _ = await _credencial(prefixo, atlas)
    assert (await socks(porta, u, s, "lento.rebind.exemplo.com", 80)).rep == 0x04
    assert (await socks(porta, u, s, "naoexiste.exemplo.com", 80)).rep == 0x04
    assert atlas.vistos == []


async def test_nome_com_byte_nul_sem_traceback(prefixo, proxies, relay_rodando, caplog):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando()
    u, s, _ = await _credencial(prefixo, atlas)
    assert motivo_destino_bloqueado("a\x00.exemplo.com", 443) is not None
    with caplog.at_level(logging.DEBUG):
        res = await socks(porta, u, s, "a\x00.exemplo.com", 443)
        await asyncio.sleep(0.1)
    assert res.rep == 0x02
    assert "Traceback" not in caplog.text and "Unhandled exception" not in caplog.text
    assert atlas.vistos == [] and not relay._pre and len(relay._clientes) == 0


async def test_erro_inesperado_responde_e_nao_vaza(prefixo, proxies, relay_rodando, caplog):
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando()

    async def _quebra(reader):
        raise RuntimeError("algo estranho")

    relay._pedido = _quebra  # type: ignore[method-assign]
    u, s, _ = await _credencial(prefixo, atlas)
    with caplog.at_level(logging.DEBUG):
        res = await socks(porta, u, s)
        await asyncio.sleep(0.1)
    assert res.rep == 0x01
    assert "erro_inesperado tipo=RuntimeError" in caplog.text
    assert "Traceback" not in caplog.text and "Unhandled exception" not in caplog.text


async def test_credencial_presa_ao_primeiro_ip(prefixo, proxies, relay_rodando):
    """SOCKS5 não tem cifra: quem copiar a credencial no caminho não usa de
    outro IP. Outro IP não conta como senha errada e fica anotado para a tela,
    que solta (o celular trocou de rede)."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, _porta = await relay_rodando(max_falhas_ip=2)
    u, s, link = await _credencial(prefixo, atlas)
    k = Chaves(prefixo)
    cred = await relay._conferir("200.1.1.1", u.encode(), s.encode())
    assert cred.link_id == link
    assert await redis.hget(k.met(link), "preso_ip") == "200.1.1.1"
    assert (await relay._conferir("200.1.1.1", u.encode(), s.encode())).link_id == link
    for _ in range(5):
        with pytest.raises(Recusa) as e:
            await relay._conferir("200.9.9.9", u.encode(), s.encode())
        assert e.value.motivo == "outro_ip"
    met = await redis.hgetall(k.met(link))
    assert met["outro_ip"] == "200.9.9.9" and met["outro_ip_n"] == "5"
    assert relay._falhas_recentes("200.9.9.9") == 0  # não é senha errada
    # IPv6: preso ao /64 (o fim do endereço muda sozinho).
    u6, s6, link6 = await _credencial(prefixo, atlas)
    await relay._conferir("2804:14c:1:2::10", u6.encode(), s6.encode())
    await relay._conferir("2804:14c:1:2:abcd::99", u6.encode(), s6.encode())
    with pytest.raises(Recusa):
        await relay._conferir("2804:14c:1:3::10", u6.encode(), s6.encode())
    # Soltar (o que a tela faz): o próximo IP fica com ela.
    await redis.hdel(k.met(link), "preso_ip")
    assert (await relay._conferir("200.9.9.9", u.encode(), s.encode())).link_id == link
    assert await redis.hget(k.met(link), "preso_ip") == "200.9.9.9"


async def test_falha_so_ao_gravar_numeros_nao_derruba(prefixo, proxies, relay_rodando):
    """Disco cheio / MISCONF: gravar os números falha, mas LER funciona — a
    vigia não pode derrubar o celular por isso."""
    atlas = await proxies("pxatlas", "SenhaRealAtlas1")
    relay, porta = await relay_rodando(vigia_s=0.05, redis_fora_max_s=0.01)

    async def _grava_falha():
        raise RuntimeError("MISCONF")

    relay._descarregar_metricas = _grava_falha  # type: ignore[method-assign]
    u, s, _ = await _credencial(prefixo, atlas)
    res = await socks(porta, u, s)
    assert res.rep == 0
    await asyncio.sleep(0.5)  # várias voltas da vigia
    assert await eco_ok(res)
    res.fechar()
