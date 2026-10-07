"""Leitura parada do /atendimento — o que este arquivo trava.

Checagem de 05/10/2026: a Temu ficou sem ler desde 01/10 sem ninguém saber, e
nada da casa avisava leitura parada do /atendimento. O Eduardo decidiu que o
aviso fica no PRÓPRIO /atendimento ("já avisa ali no próprio atendimento"),
não na Ouvidoria: o resumo da tela traz a lista e a Caixa mostra a faixa.

- loja por API parada vira UMA linha por integração (ML Poofy: pergunta e
  pós-venda numa linha só), com o motivo curto e a ação certos (sem
  permissão, token, Magalu, erro);
- é o retrato de agora: voltou a ler, sai da lista na hora (sem histerese);
- Magalu: limite de 6 h (a falha noturna medida não aparece) e as 3 caixas da
  loja numa linha só;
- a leitura inteira parada (o sync caiu; o robô do Mac mini sem sinal) vira
  UMA linha geral, e junto dela só o que JÁ estava parado antes;
- robô: sessão caída com pulso vivo é a linha da loja, não a geral;
- desligado de propósito (interruptor, caixa `desligado`, loja arquivada,
  robô sem token) não aparece;
- rodadas de reclamações/avaliações pelo carimbo que o worker grava, com
  carência; Redis fora não derruba nada;
- só leitura: nada é gravado no banco nem no Redis;
- o resumo da tela traz a lista (só para quem vê o /atendimento), sem cair
  se a lista falhar, e quem vê só a própria equipe vê só as lojas dela.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps.team_scope import TeamScope
from app.models import AtendimentoCanal, Integration, IntegrationPlatform, UserRole
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services import vigia_leitura_atendimento as vigia

pytestmark = pytest.mark.asyncio


# ─────────────── infraestrutura ───────────────


class RedisFalso:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.escritas = 0

    async def hgetall(self, chave):
        return dict(self.hashes.get(chave, {}))

    async def hset(self, chave, campo, valor):
        self.escritas += 1
        self.hashes.setdefault(chave, {})[campo] = str(valor)
        return 1

    async def hsetnx(self, chave, campo, valor):
        self.escritas += 1
        h = self.hashes.setdefault(chave, {})
        if campo in h:
            return 0
        h[campo] = str(valor)
        return 1

    async def hdel(self, chave, campo):
        self.escritas += 1
        return 1 if self.hashes.get(chave, {}).pop(campo, None) is not None else 0


class RedisFora:
    async def hgetall(self, *_a, **_k):
        raise ConnectionError("fora")

    async def hset(self, *_a, **_k):
        raise ConnectionError("fora")

    async def hsetnx(self, *_a, **_k):
        raise ConnectionError("fora")

    async def hdel(self, *_a, **_k):
        raise ConnectionError("fora")


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_robo_token", "segredo-do-robo")
    monkeypatch.setattr(s, "atendimento_robo_parado_min", 5)
    monkeypatch.setattr(s, "atendimento_carrinhos_ativa", True)
    monkeypatch.setattr(s, "atendimento_redes_ativa", True)
    monkeypatch.setattr(s, "atendimento_reclamacoes_ativa", True)
    monkeypatch.setattr(s, "atendimento_avaliacoes_ativa", True)
    return s


@pytest.fixture(autouse=True)
def redis_falso(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(vigia, "redis", r)
    return r


def _agora() -> datetime:
    return datetime.now(UTC)


def _ha(**kw) -> datetime:
    return _agora() - timedelta(**kw)


async def _integ(db: AsyncSession, user, platform: IntegrationPlatform, nome: str, **kw):
    integ = Integration(
        user_id=user.id,
        platform=platform,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
        **kw,
    )
    db.add(integ)
    await db.commit()
    return integ


async def _canal(
    db: AsyncSession,
    *,
    integ: Integration | None = None,
    plataforma: str | None = None,
    canal: str = "chat",
    status: str = "ok",
    ok: datetime | None = None,
    criado: datetime | None = None,
    erro: str | None = None,
    perfil: str | None = None,
    externo_ref: str | None = None,
    cursor: dict | None = None,
) -> AtendimentoCanal:
    c = AtendimentoCanal(
        id=uuid4(),
        integration_id=integ.id if integ is not None else None,
        robo_perfil_id=perfil,
        externo_ref=externo_ref,
        plataforma=plataforma or getattr(integ.platform, "value", integ.platform),
        canal=canal,
        status=status,
        cursor=cursor or {},
        ultimo_ok_em=ok,
        ultimo_erro=erro,
        ultimo_erro_em=_agora() if erro else None,
    )
    db.add(c)
    await db.flush()
    if criado is not None:
        c.created_at = criado
    await db.commit()
    return c


async def _lista(db: AsyncSession) -> dict[str, vigia.LeituraParada]:
    return {lp.chave: lp for lp in await vigia.leitura_parada(db)}


async def _lojas_lendo(db, user, n: int = 2) -> list[AtendimentoCanal]:
    """Lojas Shopee saudáveis (a régua da linha geral precisa de >= 2)."""
    out = []
    for i in range(n):
        integ = await _integ(db, user, IntegrationPlatform.SHOPEE, f"shopee ok {i}")
        out.append(await _canal(db, integ=integ, ok=_ha(minutes=1)))
    return out


# ─────────────── puro ───────────────


async def test_medir_e_limites():
    agora = _agora()
    lim = timedelta(minutes=30)
    assert vigia.medir(agora - timedelta(minutes=10), None, agora, lim).parado is False
    i = vigia.medir(agora - timedelta(minutes=40), None, agora, lim)
    assert i.parado and not i.nunca_leu and i.minutos == 40
    # Nunca leu: conta desde a criação da caixa.
    i = vigia.medir(None, agora - timedelta(minutes=10), agora, lim)
    assert not i.parado and i.nunca_leu
    assert vigia.medir(None, agora - timedelta(days=5), agora, lim).parado
    # Os limites calibrados em produção (05/10/2026).
    lim = {k: int(v.total_seconds() // 60) for k, v in vigia.LIMITES.items()}
    assert lim == {
        "loja": 30,
        "magalu": 6 * 60,
        "robo": 60,
        "site": 120,
        "redes": 60,
        "reclamacoes": 60,
        "avaliacoes": 120,
    }


async def test_rodada_ok():
    assert vigia.rodada_ok("reclamacoes", {"ml": {"contas": 21, "contas_com_erro": 2}})
    assert vigia.rodada_ok("reclamacoes", {"ml": {"contas": 0, "contas_com_erro": 0}})
    assert not vigia.rodada_ok("reclamacoes", {"ml": {"contas": 3, "contas_com_erro": 3}})
    assert not vigia.rodada_ok("reclamacoes", {"pulado": True})
    assert not vigia.rodada_ok("reclamacoes", None)
    assert vigia.rodada_ok("avaliacoes", {"shopee": {"lojas": 14, "lojas_com_erro": 0}})
    assert not vigia.rodada_ok("avaliacoes", {"shopee": {"lojas": 2, "lojas_com_erro": 2}})
    assert not vigia.rodada_ok("outra", {"ml": {}})


# ─────────────── lojas pela API ───────────────


async def test_loja_parada_uma_linha_por_loja(db, make_user):
    user = await make_user()
    await _lojas_lendo(db, user)
    poofy = await _integ(db, user, IntegrationPlatform.ML, "Poofy")
    erro = "HTTP 403 PA_UNAUTHORIZED_RESULT_FROM_POLICIES"
    for canal in ("pergunta", "pos_venda"):
        await _canal(
            db, integ=poofy, canal=canal, status="sem_escopo", criado=_ha(days=5), erro=erro
        )
    lucas = await _integ(db, user, IntegrationPlatform.ML, "lucas mei")
    for canal in ("pergunta", "pos_venda"):
        await _canal(
            db, integ=lucas, canal=canal, status="erro", criado=_ha(days=5),
            erro="token_nao_renovou",
        )
    tiktok = await _integ(db, user, IntegrationPlatform.TIKTOK, "inova")
    await _canal(
        db, integ=tiktok, status="sem_escopo", criado=_ha(days=5),
        erro="tiktok 401/105005: app sem o escopo seller.customer_service",
    )
    shopee = await _integ(db, user, IntegrationPlatform.SHOPEE, "atv")
    await _canal(db, integ=shopee, status="erro", ok=_ha(minutes=45), erro="HTTP 500 x")

    lista = await _lista(db)
    assert set(lista) == {
        f"loja:{poofy.id}", f"loja:{lucas.id}", f"loja:{tiktok.id}", f"loja:{shopee.id}"
    }
    o = lista[f"loja:{poofy.id}"]
    assert (o.tipo, o.plataforma, o.loja) == ("loja", "ml", "Poofy")
    assert o.nunca_leu and o.motivo == "sem permissão" and o.integration_id == poofy.id
    assert o.caixas == ("Perguntas", "Pós-venda")
    assert "403" in o.detalhe and "restrição" in o.acao
    assert o.limite_min == 30 and o.minutos >= 5 * 24 * 60 - 1
    assert lista[f"loja:{lucas.id}"].motivo == "token não renova"
    assert "token" in lista[f"loja:{lucas.id}"].acao
    t = lista[f"loja:{tiktok.id}"]
    assert t.motivo == "sem permissão" and "Atendimento ao Cliente" in t.acao
    assert t.caixas == ()  # uma caixa só: não repete o nome dela
    s = lista[f"loja:{shopee.id}"]
    assert s.motivo == "erro na leitura" and not s.nunca_leu
    assert 44 <= s.minutos <= 46 and s.detalhe == "HTTP 500 x"
    # O retrato é o mesmo na segunda vez (nada guardado entre uma e outra).
    assert set(await _lista(db)) == set(lista)
    # Ordem: por plataforma e nome.
    assert [lp.plataforma for lp in await vigia.leitura_parada(db)] == [
        "ml", "ml", "shopee", "tiktok",
    ]


async def test_loja_lendo_nao_aparece(db, make_user):
    user = await make_user()
    await _lojas_lendo(db, user, n=3)
    integ = await _integ(db, user, IntegrationPlatform.ML, "kfa")
    await _canal(db, integ=integ, canal="pergunta", ok=_ha(minutes=6))
    # Um erro numa conversa (o canal segue ok e lendo) não é leitura parada.
    await _canal(
        db, integ=integ, canal="pos_venda", ok=_ha(minutes=2),
        erro="1 de 21 conversas com erro: HTTP 500 runtime_error",
    )
    # Caixa nova, criada agora, ainda não leu: dentro do limite.
    nova = await _integ(db, user, IntegrationPlatform.TIKTOK, "nova")
    await _canal(db, integ=nova, status="novo")
    assert await _lista(db) == {}


async def test_voltou_a_ler_sai_da_lista_na_hora(db, make_user):
    """Sem histerese: o retrato de agora. Parou de novo, volta."""
    user = await make_user()
    await _lojas_lendo(db, user)
    integ = await _integ(db, user, IntegrationPlatform.SHOPEE, "atv")
    c = await _canal(db, integ=integ, ok=_ha(minutes=45), erro="shopee invalid_acceess_token")
    assert set(await _lista(db)) == {f"loja:{integ.id}"}
    c.ultimo_ok_em = _ha(minutes=1)
    await db.commit()
    assert await _lista(db) == {}
    c.ultimo_ok_em = _ha(minutes=31)
    await db.commit()
    assert set(await _lista(db)) == {f"loja:{integ.id}"}


async def test_magalu_noite_medida_nao_aparece_e_as_tres_caixas_sao_uma_linha(db, make_user):
    user = await make_user()
    await _lojas_lendo(db, user)
    integ = await _integ(db, user, IntegrationPlatform.MAGALU, "poofy")
    caixas = [
        await _canal(
            db, integ=integ, canal=canal, status="erro", ok=_ha(hours=4, minutes=12),
            erro="ReadTimeout",
        )
        for canal in ("pergunta", "chat", "sac")
    ]
    assert await _lista(db) == {}  # 4 h 12 sem ler à noite: abaixo das 6 h

    for c in caixas:
        c.ultimo_ok_em = _ha(hours=7)
    await db.commit()
    lista = await _lista(db)
    assert list(lista) == [f"loja:{integ.id}"]
    o = lista[f"loja:{integ.id}"]
    assert o.caixas == ("Chat", "Perguntas", "SAC") and o.limite_min == 6 * 60
    assert o.motivo == "erro na leitura" and o.detalhe == "ReadTimeout"
    assert "proxy do Mac do dono" in o.acao


async def test_leitura_inteira_parada_vira_uma_linha_geral(db, make_user):
    user = await make_user()
    lojas = await _lojas_lendo(db, user, n=3)
    poofy = await _integ(db, user, IntegrationPlatform.TIKTOK, "Poofy")
    await _canal(db, integ=poofy, status="sem_escopo", criado=_ha(days=5))
    # Sites/redes/rodadas também rodam no worker.
    site_ok = await _canal(
        db, plataforma="site", canal="carrinho", externo_ref="site:charlots",
        ok=_ha(minutes=50), cursor={"externo": {"nome": "Charlots"}},
    )
    site_quebrado = await _canal(
        db, plataforma="site", canal="carrinho", externo_ref="site:uranyx",
        status="sem_endpoint", criado=_ha(days=3), cursor={"externo": {"nome": "Uranyx"}},
    )
    assert set(await _lista(db)) == {f"loja:{poofy.id}", f"canal:{site_quebrado.id}"}

    # O worker caiu há 2 h 40: nenhuma loja lê. O site que lia parou junto.
    for c in lojas:
        c.ultimo_ok_em = _ha(hours=2, minutes=40)
    site_ok.ultimo_ok_em = _ha(hours=3)
    await db.commit()
    lista = await _lista(db)
    # Só a geral + o que JÁ estava parado antes (TikTok sem escopo, Uranyx).
    assert set(lista) == {"geral:lojas", f"loja:{poofy.id}", f"canal:{site_quebrado.id}"}
    g = lista["geral:lojas"]
    assert (g.tipo, g.plataforma, g.loja, g.motivo) == (
        "geral", "interno", "Leitura das lojas", "nenhuma loja lê"
    )
    assert 159 <= g.minutos <= 161 and "worker" in g.acao
    # A geral vem primeiro.
    assert (await vigia.leitura_parada(db))[0].chave == "geral:lojas"

    # Voltou: a geral some e as lojas voltam a ser julgadas uma a uma.
    for c in lojas:
        c.ultimo_ok_em = _ha(minutes=1)
    await db.commit()
    assert set(await _lista(db)) == {
        f"loja:{poofy.id}", f"canal:{site_quebrado.id}", f"canal:{site_ok.id}"
    }


async def test_desligado_de_proposito_nao_aparece(db, make_user, _config, monkeypatch):
    user = await make_user()
    await _lojas_lendo(db, user)
    amazon = await _integ(db, user, IntegrationPlatform.AMAZON, "kfa")
    await _canal(db, integ=amazon, canal="email", status="desligado", criado=_ha(days=3))
    velha = await _integ(db, user, IntegrationPlatform.SHOPEE, "velha", archived_at=_ha(days=1))
    await _canal(db, integ=velha, ok=_ha(days=2))
    parada = await _integ(db, user, IntegrationPlatform.SHOPEE, "parada")
    await _canal(db, integ=parada, ok=_ha(hours=2))
    temu = await _canal(
        db, plataforma="temu", perfil="k1dkegpc", ok=_ha(days=4),
        cursor={"robo": {"loja": "Atv", "estado": "lendo", "ultimo_pulso_em": None}},
    )
    assert set(await _lista(db)) == {f"loja:{parada.id}", f"canal:{temu.id}"}

    # Robô sem token: ele está desligado (o router devolve 404).
    monkeypatch.setattr(_config, "atendimento_robo_token", " ")
    assert set(await _lista(db)) == {f"loja:{parada.id}"}
    # Leitura desligada no .env: nada a vigiar.
    monkeypatch.setattr(_config, "atendimento_leitura_ativa", False)
    assert await _lista(db) == {}


# ─────────────── robô do Mac mini ───────────────


def _cursor_robo(loja: str, *, pulso: datetime | None, estado: str = "lendo") -> dict:
    return {
        "robo": {
            "loja": loja,
            "estado": estado,
            "ultimo_pulso_em": pulso.isoformat() if pulso else None,
        }
    }


async def test_robo_sessao_caida_e_sem_sinal_geral(db):
    erro = "o Seller Center saiu da conta no perfil do AdsPower: alguém precisa entrar de novo"
    temu = await _canal(
        db, plataforma="temu", perfil="k1dkegpc", status="sessao_caiu", ok=_ha(days=4),
        erro=erro, cursor=_cursor_robo("Atv", pulso=_ha(minutes=1), estado="sessao_caiu"),
    )
    ali = await _canal(
        db, plataforma="aliexpress", perfil="k1do5vfw", ok=_ha(minutes=1),
        cursor=_cursor_robo("Vita", pulso=_ha(minutes=1)),
    )
    lista = await _lista(db)
    assert set(lista) == {f"canal:{temu.id}"}
    o = lista[f"canal:{temu.id}"]
    assert (o.loja, o.plataforma, o.canal_id) == ("Atv", "temu", temu.id)
    assert o.motivo == "sessão caiu no AdsPower" and o.minutos >= 4 * 24 * 60 - 1
    assert "Entrar de novo no Seller Center" in o.acao and "k1dkegpc" in o.detalhe

    # O Mac mini apagou: nenhum pulso de loja nenhuma há 2 h → UMA linha
    # geral; junto, só a Temu, que já estava parada antes.
    temu.cursor = _cursor_robo("Atv", pulso=_ha(hours=2), estado="sessao_caiu")
    ali.cursor = _cursor_robo("Vita", pulso=_ha(hours=2))
    ali.ultimo_ok_em = _ha(hours=2)
    await db.commit()
    lista = await _lista(db)
    assert set(lista) == {"geral:robo", f"canal:{temu.id}"}
    assert lista["geral:robo"].motivo == "Mac mini sem sinal"
    assert lista[f"canal:{temu.id}"].motivo == "robô sem sinal"


async def test_robo_sem_sinal_de_uma_loja_so(db):
    await _canal(
        db, plataforma="aliexpress", perfil="k1do5vfw", ok=_ha(minutes=1),
        cursor=_cursor_robo("Vita", pulso=_ha(minutes=1)),
    )
    temu = await _canal(
        db, plataforma="temu", perfil="k1docw56", ok=_ha(hours=3),
        cursor=_cursor_robo("Barbosa", pulso=_ha(hours=3)),
    )
    # A janela medida em 05/10: o AliExpress ficou 31 min sem ler — não aparece.
    await _canal(
        db, plataforma="aliexpress", perfil="k1doxxxx", ok=_ha(minutes=31),
        cursor=_cursor_robo("Outra", pulso=_ha(minutes=1)),
    )
    lista = await _lista(db)
    assert set(lista) == {f"canal:{temu.id}"}
    assert lista[f"canal:{temu.id}"].motivo == "robô sem sinal"
    assert "Mac mini está ligado" in lista[f"canal:{temu.id}"].acao


# ─────────────── sites e redes ───────────────


async def test_site_e_rede(db, _config, monkeypatch):
    site = await _canal(
        db, plataforma="site", canal="carrinho", externo_ref="site:charlots", status="sem_endpoint",
        criado=_ha(hours=3), erro="HTTP 404: a rota /api/davinci/carrinhos não está publicada",
        cursor={"externo": {"nome": "Charlots"}},
    )
    await _canal(
        db, plataforma="instagram", canal="comentario", externo_ref="rede:instagram:1",
        ok=_ha(minutes=50), cursor={"externo": {"nome": "@uranyx_br"}},
    )
    rede = await _canal(
        db, plataforma="facebook", canal="comentario", externo_ref="rede:facebook:2",
        status="sem_escopo", ok=_ha(hours=2), cursor={"externo": {"nome": "Charlots"}},
    )
    lista = await _lista(db)
    assert set(lista) == {f"canal:{site.id}", f"canal:{rede.id}"}  # a do IG está nos 60 min
    o = lista[f"canal:{site.id}"]
    assert (o.loja, o.plataforma, o.nunca_leu) == ("Charlots", "site", True)
    assert o.motivo == "rota do carrinho não publicada" and "pacote do carrinho" in o.acao
    assert o.caixas == ("Carrinho abandonado",) and o.limite_min == 120
    assert lista[f"canal:{rede.id}"].motivo == "token sem permissão"

    # Carrinho e redes desligados no .env: não é leitura parada.
    monkeypatch.setattr(_config, "atendimento_carrinhos_ativa", False)
    monkeypatch.setattr(_config, "atendimento_redes_ativa", False)
    assert await _lista(db) == {}


# ─────────────── rodadas sem caixa ───────────────


async def test_rodadas_pelo_carimbo_com_carencia(db, redis_falso, _config, monkeypatch):
    # Sem carimbo nenhum (primeira subida, Redis reiniciado): carência.
    assert await _lista(db) == {}

    # O worker roda as duas: reclamações leu; avaliações rodou mas não leu.
    assert await vigia.carimbar_rodada("reclamacoes", {"ml": {"contas": 21, "contas_com_erro": 0}})
    assert not await vigia.carimbar_rodada(
        "avaliacoes", {"shopee": {"lojas": 2, "lojas_com_erro": 2}}
    )
    assert set(redis_falso.hashes[vigia.CHAVE_VISTO]) == {"reclamacoes", "avaliacoes"}
    assert set(redis_falso.hashes[vigia.CHAVE_OK]) == {"reclamacoes"}
    assert await _lista(db) == {}  # dentro dos limites

    # A primeira vez de avaliações foi há 3 h (limite 2 h) e nenhuma leu.
    redis_falso.hashes[vigia.CHAVE_VISTO]["avaliacoes"] = str(int(_ha(hours=3).timestamp()))
    # A primeira marca não anda: o hsetnx não sobrescreve.
    await vigia.carimbar_rodada("avaliacoes", None)
    lista = await _lista(db)
    assert set(lista) == {"rodada:avaliacoes"}
    o = lista["rodada:avaliacoes"]
    assert (o.tipo, o.loja, o.nunca_leu) == ("rodada", "Avaliações de venda", True)
    assert o.motivo == "rodada não termina lendo"
    assert "atendimento_avaliacoes_falhou" in o.acao and 179 <= o.minutos <= 181

    # Reclamações sem ler há 70 min (limite 60): aparece também.
    redis_falso.hashes[vigia.CHAVE_OK]["reclamacoes"] = str(int(_ha(minutes=70).timestamp()))
    lista = await _lista(db)
    assert set(lista) == {"rodada:avaliacoes", "rodada:reclamacoes"}
    assert not lista["rodada:reclamacoes"].nunca_leu

    # Desligaram as avaliações no .env: sai da lista; o worker esquece os
    # carimbos dela (ao religar, a carência recomeça).
    monkeypatch.setattr(_config, "atendimento_avaliacoes_ativa", False)
    assert set(await _lista(db)) == {"rodada:reclamacoes"}
    await vigia.esquecer_rodada("avaliacoes")
    assert "avaliacoes" not in redis_falso.hashes[vigia.CHAVE_VISTO]
    assert "avaliacoes" not in redis_falso.hashes[vigia.CHAVE_OK]


async def test_redis_fora_tira_so_as_rodadas(db, make_user, monkeypatch):
    user = await make_user()
    await _lojas_lendo(db, user)
    parada = await _integ(db, user, IntegrationPlatform.SHOPEE, "parada")
    await _canal(db, integ=parada, ok=_ha(hours=2))
    monkeypatch.setattr(vigia, "redis", RedisFora())
    assert set(await _lista(db)) == {f"loja:{parada.id}"}
    # E o carimbo do worker nunca derruba a rodada.
    assert not await vigia.carimbar_rodada("reclamacoes", {"ml": {"contas": 1}})
    await vigia.esquecer_rodada("reclamacoes")


async def test_so_leitura(db, make_user, redis_falso):
    """Nada é gravado: nem no banco (sessão limpa), nem no Redis."""
    user = await make_user()
    await _lojas_lendo(db, user)
    parada = await _integ(db, user, IntegrationPlatform.SHOPEE, "parada")
    await _canal(db, integ=parada, ok=_ha(hours=2))
    redis_falso.hashes[vigia.CHAVE_OK] = {"reclamacoes": str(int(_ha(hours=2).timestamp()))}
    lista = await _lista(db)
    assert set(lista) == {f"loja:{parada.id}", "rodada:reclamacoes"}
    assert redis_falso.escritas == 0
    assert not db.new and not db.dirty and not db.deleted


# ─────────────── worker ───────────────


async def test_worker_carimba_as_rodadas_e_esquece_a_desligada(redis_falso, monkeypatch):
    from app import worker
    from app.services.atendimento import avaliacoes, reclamacoes

    async def _recl(ctx):
        return {"ml": {"contas": 21, "contas_com_erro": 1}, "devolucoes": {}}

    async def _aval(ctx):
        return {"shopee": {"lojas": 3, "lojas_com_erro": 3}}

    monkeypatch.setattr(reclamacoes, "atendimento_reclamacoes", _recl)
    monkeypatch.setattr(avaliacoes, "atendimento_avaliacoes", _aval)
    monkeypatch.setattr(worker._settings, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(worker._settings, "atendimento_reclamacoes_ativa", True)
    monkeypatch.setattr(worker._settings, "atendimento_avaliacoes_ativa", True)
    assert (await worker.atendimento_reclamacoes({}))["ml"]["contas"] == 21
    await worker.atendimento_avaliacoes({})
    assert set(redis_falso.hashes.get(vigia.CHAVE_OK, {})) == {"reclamacoes"}
    assert set(redis_falso.hashes.get(vigia.CHAVE_VISTO, {})) == {"reclamacoes", "avaliacoes"}

    # Desligada no .env: a rodada não roda e os carimbos dela somem.
    monkeypatch.setattr(worker._settings, "atendimento_avaliacoes_ativa", False)
    assert await worker.atendimento_avaliacoes({}) is None
    assert "avaliacoes" not in redis_falso.hashes[vigia.CHAVE_VISTO]

    # Redis fora: a rodada devolve o resumo do mesmo jeito.
    monkeypatch.setattr(vigia, "redis", RedisFora())
    assert (await worker.atendimento_reclamacoes({}))["ml"]["contas"] == 21
    monkeypatch.setattr(worker._settings, "atendimento_reclamacoes_ativa", False)
    assert await worker.atendimento_reclamacoes({}) is None


async def test_worker_nao_tem_mais_o_robo_da_ouvidoria():
    """O aviso não vai para a Ouvidoria (Eduardo, 05/10/2026): nenhum robô
    novo no catálogo nem cron no worker."""
    from app import worker
    from app.services import ouvidoria

    assert "vigia_leitura_atendimento" not in ouvidoria.ROBOS
    assert not hasattr(worker, "vigia_leitura_atendimento_tick")
    nomes = {
        getattr(getattr(c, "coroutine", None), "__name__", "")
        for c in worker.WorkerSettings.cron_jobs
    }
    assert not any("leitura_atendimento" in n for n in nomes)


# ─────────────── a tela: o resumo ───────────────


async def test_resumo_traz_a_faixa_e_ela_some_quando_volta_a_ler(
    client, db, make_user, auth_as, _config, monkeypatch
):
    monkeypatch.setattr(_config, "atendimento_usuarios", "dono@davinci-test.com")
    dono = await make_user(email="dono@davinci-test.com", role=UserRole.ADMIN)
    user = await make_user()
    await _lojas_lendo(db, user)
    poofy = await _integ(db, user, IntegrationPlatform.ML, "Poofy")
    caixas = [
        await _canal(
            db, integ=poofy, canal=canal, status="sem_escopo", criado=_ha(days=5),
            erro="HTTP 403 PA_UNAUTHORIZED_RESULT_FROM_POLICIES",
        )
        for canal in ("pergunta", "pos_venda")
    ]
    auth_as(dono)
    r = await client.get("/api/atendimento/resumo")
    assert r.status_code == 200, r.text
    faixa = r.json()["leitura_parada"]
    assert len(faixa) == 1
    lp = faixa[0]
    assert lp["chave"] == f"loja:{poofy.id}" and lp["integration_id"] == str(poofy.id)
    assert (lp["tipo"], lp["plataforma"], lp["motivo"], lp["nunca_leu"]) == (
        "loja", "ml", "sem permissão", True
    )
    assert lp["caixas"] == ["Perguntas", "Pós-venda"] and lp["limite_min"] == 30
    assert lp["desde"] and lp["minutos"] >= 5 * 24 * 60 - 1 and lp["acao"]
    # O nome é o mesmo da barra de lojas.
    barra = {lj["integration_id"]: lj["conta"] for lj in r.json()["lojas"]}
    assert lp["loja"] == barra[str(poofy.id)] == "Poofy"

    # A loja voltou a ler: a faixa some sozinha na recarga seguinte.
    for c in caixas:
        c.status, c.ultimo_ok_em, c.ultimo_erro = "ok", _ha(minutes=1), None
    await db.commit()
    r = await client.get("/api/atendimento/resumo")
    assert r.status_code == 200 and r.json()["leitura_parada"] == []

    # Só quem vê o /atendimento: desde 07/10/2026 (fase de observação) toda
    # pessoa ativa lê — o admin fora da lista vê a mesma faixa; o operador de
    # estoque (que o web prende no /controle-estoque) não chega no resumo.
    auth_as(await make_user(email="outro@davinci-test.com", role=UserRole.ADMIN))
    r = await client.get("/api/atendimento/resumo")
    assert r.status_code == 200 and r.json()["leitura_parada"] == []
    operador = await make_user(email="op@davinci-test.com")
    operador.stock_tags = ["ci"]
    await db.commit()
    auth_as(operador)
    assert (await client.get("/api/atendimento/resumo")).status_code == 403


async def test_resumo_nao_cai_se_a_faixa_falhar(client, db, make_user, auth_as, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")

    async def _quebra(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(vigia, "leitura_parada", _quebra)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get("/api/atendimento/resumo")
    assert r.status_code == 200, r.text
    assert r.json()["leitura_parada"] == [] and "lojas" in r.json()


async def test_equipe_so_ve_as_lojas_dela(db, monkeypatch):
    minha, outra = uuid4(), uuid4()

    def _lp(chave, integration_id=None, tipo="loja"):
        return vigia.LeituraParada(
            chave=chave, tipo=tipo, plataforma="shopee", loja=chave, motivo="m", acao="a",
            desde=_agora(), nunca_leu=False, minutos=40, limite_min=30,
            integration_id=integration_id,
        )

    async def _falsa(*_a, **_k):
        return [
            _lp("geral:lojas", tipo="geral"),
            _lp(f"loja:{minha}", minha),
            _lp(f"loja:{outra}", outra),
            _lp("canal:temu"),
        ]

    monkeypatch.setattr(vigia, "leitura_parada", _falsa)
    todas = await rota._leitura_parada(db, TeamScope(unrestricted=True), [])
    assert [x.chave for x in todas] == [
        "geral:lojas", f"loja:{minha}", f"loja:{outra}", "canal:temu"
    ]
    da_equipe = await rota._leitura_parada(
        db, TeamScope(unrestricted=False, integration_ids={minha}), []
    )
    assert [x.chave for x in da_equipe] == [f"loja:{minha}"]
