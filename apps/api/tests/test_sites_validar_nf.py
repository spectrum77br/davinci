"""Conferência da NF do SAC dos sites — o que a porta deixa passar e o que não.

Na ordem: a porta (token, limite), o corpo (422 só para o malformado), o
número (normalização), a marca do token, o 2º fator (data, pedido, chave), a
época sem cobertura (409) e, por fim, que nada vaza (resposta, cabeçalhos,
log, CORS, openapi).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.config import get_settings
from app.models.bling_nota import BlingNota, BlingNotaEmitida
from app.models.bling_order import BlingOrder
from app.models.nf import NfNota
from app.models.pricing import StoreInfo
from app.routers import sites_validar_nf as rota
from app.services.rate_limit import RateLimitError

URL = "/api/sites/validar-nf"
TOK_CH = "tok-site-charlots-0123456789"
TOK_UR = "tok-site-uranyx-9876543210"
TOK_SEM_MARCA = "tok-site-poofy-5555555555"

UR = {"Authorization": f"Bearer {TOK_UR}"}
CH = {"Authorization": f"Bearer {TOK_CH}"}

# Pedido de celular da Uranyx de 10/10/2026, NF emitida no dia 12.
DIA_PEDIDO = date(2026, 10, 10)
DIA_NF = date(2026, 10, 12)
CHAVE = "35261012345678000190550010000123451000000017"  # nNF = 000012345


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setattr(
        get_settings(),
        "sites_estoque_tokens",
        f"{TOK_CH}:charlots, {TOK_UR}:uranyx,{TOK_SEM_MARCA}:poofy",
    )


@pytest.fixture(autouse=True)
def chamadas_do_limite(monkeypatch) -> list[str]:
    """O limite vira um contador das chaves pedidas (o teste do Redis de verdade é separado)."""
    chaves: list[str] = []

    async def _ok(*, key: str, limit: int, window_seconds: int) -> int:
        chaves.append(key)
        return limit

    monkeypatch.setattr(rota, "sliding_window_check", _ok)
    return chaves


@pytest.fixture(autouse=True)
async def _limpa_contas_de_emissao(db: AsyncSession):
    """`bling_notas*` não estão no cleanup do conftest."""
    yield
    await db.execute(text("DELETE FROM bling_notas_emitidas"))
    await db.execute(text("DELETE FROM bling_notas"))
    await db.commit()


def _meia_noite_sp(d: date) -> datetime:
    # O Bling grava a data do pedido à meia-noite de São Paulo.
    return datetime(d.year, d.month, d.day, 3, 0, tzinfo=UTC)


async def _pedido(
    db: AsyncSession,
    numero: str,
    skus: list[str],
    *,
    dia: date = DIA_PEDIDO,
    numeroloja: str | None = None,
    loja: str | None = "203000001",
) -> None:
    for i, sku in enumerate(skus):
        db.add(
            BlingOrder(
                bling_id=int(numero) * 10,
                numero=numero,
                numeroloja=numeroloja,
                data=_meia_noite_sp(dia),
                loja=loja,
                item_index=i,
                item_codigo=sku,
                item_descricao=f"Item {sku}",
                item_quantidade=1,
                documento_destinatario="12345678909",
                nome_destinatario="Cliente Teste",
            )
        )
    await db.commit()


async def _nota(
    db: AsyncSession,
    numero: str,
    pedido_bling: str | None,
    *,
    dia: date = DIA_NF,
    chave: str | None = None,
    situacao: str = "100",
) -> None:
    db.add(
        NfNota(
            chave=chave or f"{uuid4().int % 10**44:044d}",
            pedido_bling=pedido_bling,
            numero=numero,
            serie="1",
            emitente_cnpj="12345678000190",
            destinatario_doc="12345678909",
            destinatario_nome="Cliente Teste",
            data_emissao=datetime(dia.year, dia.month, dia.day, 15, 0, tzinfo=UTC),
            situacao=situacao,
            xml=b"<nfeProc/>",
        )
    )
    await db.commit()


async def _nota_emitida(
    db: AsyncSession, numero: str, complemento: str | None, *, dia: date, situacao: int = 5
) -> None:
    conta = BlingNota(nome=f"conta{numero}", client_id="x", basic_auth_b64="eA==")
    db.add(conta)
    await db.flush()
    db.add(
        BlingNotaEmitida(
            conta_id=conta.id,
            bling_id=int(numero) + 900000,
            numero=numero,
            situacao=situacao,
            data_emissao=datetime(dia.year, dia.month, dia.day, 10, 0),
            complemento=complemento,
            cpf_dest="12345678909",
        )
    )
    await db.commit()


@pytest.fixture
async def celular_uranyx(db: AsyncSession) -> None:
    """Pedido 500123 (ML 2000009876543210) de um dg053, com a NF 12345."""
    await _pedido(db, "500123", ["dg053.ci", "a003.pi"], numeroloja="2000009876543210")
    await _nota(db, "12345", "500123", chave=CHAVE)


async def _pergunta(client: AsyncClient, corpo: dict | str | bytes, headers: dict | None = None):
    if isinstance(corpo, dict):
        corpo = json.dumps(corpo)
    return await client.post(
        URL,
        content=corpo,
        headers={"Content-Type": "application/json", **(headers if headers is not None else UR)},
    )


def _corpo(nf: str = "12345", data_compra: str = DIA_PEDIDO.isoformat(), pedido: str = "") -> dict:
    return {"nf": nf, "data_compra": data_compra, "pedido": pedido}


# ─────────────── a porta ───────────────


async def test_sem_configuracao_a_rota_fica_fechada(
    client: AsyncClient, monkeypatch, celular_uranyx
):
    monkeypatch.setattr(get_settings(), "sites_estoque_tokens", "")
    r = await _pergunta(client, _corpo())
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "sites_nao_autorizado"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": TOK_UR},
        {"Authorization": "Bearer errado"},
        {"Authorization": f"Basic {TOK_UR}"},
    ],
)
async def test_token_ausente_ou_errado_401(client: AsyncClient, headers, celular_uranyx):
    r = await _pergunta(client, _corpo(), headers)
    assert r.status_code == 401
    assert "encontrada" not in r.text


async def test_token_valido_de_site_sem_marca_401(client: AsyncClient):
    r = await _pergunta(client, _corpo(), {"Authorization": f"Bearer {TOK_SEM_MARCA}"})
    assert r.status_code == 401


async def test_401_vem_antes_do_corpo(client: AsyncClient):
    """Corpo torto sem token: 401, não 422 — quem não tem token não descobre o formato."""
    r = await _pergunta(client, b"{nao e json", {})
    assert r.status_code == 401


async def test_limite_por_site_429(client: AsyncClient, monkeypatch, celular_uranyx):
    async def _estourou(*, key: str, limit: int, window_seconds: int) -> int:
        assert key == "sites_validar_nf:rl:uranyx"
        assert (limit, window_seconds) == (30, 60)
        raise RateLimitError(retry_after=17)

    monkeypatch.setattr(rota, "sliding_window_check", _estourou)
    r = await _pergunta(client, _corpo())
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "17"
    assert r.json()["detail"] == {"code": "sites_limite"}


async def test_limite_conta_por_site(client: AsyncClient, chamadas_do_limite, celular_uranyx):
    await _pergunta(client, _corpo())
    await _pergunta(client, _corpo(), CH)
    await _pergunta(client, b"lixo")  # corpo torto também gasta o limite
    assert chamadas_do_limite == [
        "sites_validar_nf:rl:uranyx",
        "sites_validar_nf:rl:charlots",
        "sites_validar_nf:rl:uranyx",
    ]


async def test_redis_fora_do_ar_nao_derruba(client: AsyncClient, monkeypatch, celular_uranyx):
    async def _caiu(**_):
        raise ConnectionError("redis down")

    monkeypatch.setattr(rota, "sliding_window_check", _caiu)
    r = await _pergunta(client, _corpo())
    assert r.status_code == 200
    assert r.json() == {"encontrada": True}


async def test_limite_no_redis_de_verdade(client: AsyncClient, monkeypatch, celular_uranyx):
    """O 31º pedido do minuto leva 429 (Redis local, chave própria do teste)."""
    from app.redis_client import redis
    from app.services import rate_limit

    try:
        await redis.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("sem Redis local")
    chave = "sites_validar_nf:rl:uranyx"
    await redis.delete(chave)
    monkeypatch.setattr(rota, "sliding_window_check", rate_limit.sliding_window_check)
    monkeypatch.setattr(rota, "LIMITE_CHAMADAS_MIN", 3)
    try:
        codigos = []
        for _ in range(4):
            codigos.append((await _pergunta(client, _corpo())).status_code)
            await asyncio.sleep(0.002)  # o membro do zset é o milissegundo
        assert codigos == [200, 200, 200, 429]
    finally:
        await redis.delete(chave)


# ─────────────── o corpo: 422 só para o malformado ───────────────


@pytest.mark.parametrize(
    ("corpo", "campo"),
    [
        (b"{nao e json", "corpo"),
        (b"[1, 2]", "corpo"),
        (b"", "corpo"),
        ({"data_compra": "2026-10-10"}, "nf"),
        ({"nf": "", "data_compra": "2026-10-10"}, "nf"),
        ({"nf": "   ", "data_compra": "2026-10-10"}, "nf"),
        ({"nf": "sem numero", "data_compra": "2026-10-10"}, "nf"),
        ({"nf": ["12345"], "data_compra": "2026-10-10"}, "nf"),
        ({"nf": "1" * 61, "data_compra": "2026-10-10"}, "nf"),
        ({"nf": "1" * 45, "data_compra": "2026-10-10"}, "nf"),
        ({"nf": "12345"}, "data_compra"),
        ({"nf": "12345", "data_compra": ""}, "data_compra"),
        ({"nf": "12345", "data_compra": "2026-13-01"}, "data_compra"),
        ({"nf": "12345", "data_compra": "2026-02-30"}, "data_compra"),
        ({"nf": "12345", "data_compra": "ontem"}, "data_compra"),
        ({"nf": "12345", "data_compra": "2014-12-31"}, "data_compra"),
        ({"nf": "12345", "data_compra": 20261010}, "data_compra"),
    ],
)
async def test_corpo_malformado_422_sem_eco(client: AsyncClient, corpo, campo):
    r = await _pergunta(client, corpo)
    assert r.status_code == 422
    assert r.json() == {"detail": {"code": "sites_nf_corpo_invalido", "campo": campo}}
    assert r.headers["Cache-Control"] == "no-store"


async def test_data_no_futuro_422(client: AsyncClient):
    futuro = (rota.hoje() + timedelta(days=30)).isoformat()
    r = await _pergunta(client, _corpo(data_compra=futuro))
    assert r.status_code == 422
    assert r.json()["detail"]["campo"] == "data_compra"


async def test_corpo_grande_422(client: AsyncClient):
    r = await _pergunta(client, _corpo(pedido="x" * 5000))
    assert r.status_code == 422
    assert r.json()["detail"]["campo"] == "corpo"


@pytest.mark.parametrize("pedido", ["", "   ", None, "-", "x" * 101, 0, {"a": 1}, True])
async def test_pedido_vazio_ou_estranho_e_ausente(client: AsyncClient, celular_uranyx, pedido):
    """`pedido: ""` (o site manda assim quando o cliente não preenche) nunca é 422.

    A data decide."""
    corpo = {"nf": "12345", "data_compra": "2026-10-10", "pedido": pedido}
    assert (await _pergunta(client, corpo)).json() == {"encontrada": True}
    corpo["data_compra"] = "2026-09-20"
    assert (await _pergunta(client, corpo)).json() == {"encontrada": False}


async def test_sem_a_chave_pedido_tambem_vale(client: AsyncClient, celular_uranyx):
    r = await _pergunta(client, {"nf": "12345", "data_compra": "2026-10-10"})
    assert r.json() == {"encontrada": True}


async def test_campos_a_mais_sao_ignorados(client: AsyncClient, celular_uranyx):
    corpo = {**_corpo(), "site": "charlots", "captcha": "x", "cpf": "000"}
    assert (await _pergunta(client, corpo)).json() == {"encontrada": True}


async def test_nf_numerica_no_json_e_aceita(client: AsyncClient, celular_uranyx):
    assert (await _pergunta(client, {"nf": 12345, "data_compra": "2026-10-10"})).json() == {
        "encontrada": True
    }


@pytest.mark.parametrize(
    "data", ["2026-10-10", "10/10/2026", "2026-10-10T00:00:00", " 2026-10-10 "]
)
async def test_formatos_de_data(client: AsyncClient, celular_uranyx, data):
    assert (await _pergunta(client, _corpo(data_compra=data))).json() == {"encontrada": True}


# ─────────────── o número da NF ───────────────


@pytest.mark.parametrize(
    "digitado",
    [
        "12345",
        "000012345",
        "000.012.345",
        "12.345",
        " 12 345 ",
        "1/12345",
        "12345-1",
        "12345/001",
        "Série 001 Nº 000.012.345",
        "NF-e nº 12345",
        "nota 12345 serie 1",
    ],
)
async def test_normalizacao_do_numero(client: AsyncClient, celular_uranyx, digitado):
    r = await _pergunta(client, _corpo(nf=digitado))
    assert r.status_code == 200
    assert r.json() == {"encontrada": True}


@pytest.mark.parametrize("digitado", ["1234", "123456", "12346", "0", "000", "99999999999"])
async def test_numero_que_nao_existe(client: AsyncClient, celular_uranyx, digitado):
    assert (await _pergunta(client, _corpo(nf=digitado))).json() == {"encontrada": False}


def test_numeros_da_nf_puro():
    f = rota.numeros_da_nf
    assert f("000.012.345") == (["12345"], None)
    assert f("1/12345") == (["112345", "1", "12345"], None)
    assert f("12345-1") == (["123451", "12345", "1"], None)
    assert f("Série 001 Nº 000.012.345") == (["1", "12345"], None)
    assert f("0000") == ([], None)
    assert f("1 2 3 4 5 6 7 8") == (["12345678", "1", "2", "3", "4", "5"], None)
    # Chave de acesso, junta ou impressa de 4 em 4: vale pela chave e pelo nNF.
    assert f(CHAVE) == (["12345"], CHAVE)
    impressa = " ".join(CHAVE[i : i + 4] for i in range(0, 44, 4))
    assert f(impressa) == (["12345"], CHAVE)


def test_normalizar_pedido_puro():
    f = rota.normalizar_pedido
    assert f(" 702-1234567-7654321 ") == "70212345677654321"
    assert f("2510279abc12xy") == "2510279ABC12XY"
    assert f(500123) == "500123"
    assert f("") is None and f(" - ") is None and f(None) is None and f(True) is None


async def test_bling_notas_emitidas_com_zeros_a_esquerda(client: AsyncClient, db: AsyncSession):
    """As notas de jan-ago/2026 vêm com 6 dígitos ("001234"); o cliente digita 1234 ou 1.234."""
    await _pedido(db, "400777", ["dg041.pi"], dia=date(2026, 6, 3), numeroloja="2000001111")
    await _nota_emitida(db, "001234", "2000001111", dia=date(2026, 6, 4))
    for digitado in ("1234", "001234", "1.234", "000.001.234"):
        r = await _pergunta(client, _corpo(nf=digitado, data_compra="2026-06-03"))
        assert r.json() == {"encontrada": True}, digitado


async def test_nota_nao_autorizada_nao_conta(client: AsyncClient, db: AsyncSession):
    await _pedido(db, "500200", ["dg053.ci"])
    await _nota(db, "777", "500200", situacao="101")  # cancelada
    await _pedido(db, "400300", ["dg041.pi"], numeroloja="2000002222")
    await _nota_emitida(db, "000888", "2000002222", dia=DIA_NF, situacao=2)  # cancelada
    assert (await _pergunta(client, _corpo(nf="777"))).json() == {"encontrada": False}
    assert (await _pergunta(client, _corpo(nf="888"))).json() == {"encontrada": False}


async def test_nota_sem_pedido_nao_conta(client: AsyncClient, db: AsyncSession):
    """Sem pedido não há como saber a marca: fica de fora."""
    await _nota(db, "4321", None)
    assert (await _pergunta(client, _corpo(nf="4321"))).json() == {"encontrada": False}


# ─────────────── a marca vem do token ───────────────


async def test_nf_de_outra_marca_e_false(client: AsyncClient, celular_uranyx):
    """A NF do celular existe, mas a Charlots pergunta: o mesmo `false` de NF inexistente."""
    r = await _pergunta(client, _corpo(), CH)
    assert r.status_code == 200
    assert r.json() == {"encontrada": False}


async def test_nf_da_charlots(client: AsyncClient, db: AsyncSession):
    await _pedido(db, "500300", ["b009.24", "a075"])
    await _nota(db, "321", "500300")
    assert (await _pergunta(client, _corpo(nf="321"), CH)).json() == {"encontrada": True}
    assert (await _pergunta(client, _corpo(nf="321"), UR)).json() == {"encontrada": False}


@pytest.mark.parametrize(
    ("skus", "uranyx", "charlots"),
    [
        (["dg053.ci"], True, False),
        (["uaf001m1.110"], True, False),
        (["dg053.pi+a003.pi"], True, False),
        (["a003.pi"], True, False),  # acessório de celular (sufixo regional)
        (["a009.cd"], True, False),
        (["b005.20+a075+bp003"], False, True),
        (["bp001"], False, True),
        (["a073"], False, True),  # rodinha: acessório de mala, sem sufixo
        (["a006"], False, True),
        (["z005.20.mala"], False, False),  # salvado de devolução: de ninguém
        (["i15.128"], False, False),  # Apple: revenda
        (["bx01"], False, False),
        (["a100"], False, False),
        (["fake01"], False, False),
    ],
)
async def test_regra_da_marca_pelos_skus(
    client: AsyncClient, db: AsyncSession, skus, uranyx, charlots
):
    await _pedido(db, "500400", skus)
    await _nota(db, "55", "500400")
    assert (await _pergunta(client, _corpo(nf="55"), UR)).json() == {"encontrada": uranyx}
    assert (await _pergunta(client, _corpo(nf="55"), CH)).json() == {"encontrada": charlots}


async def test_loja_interna_nao_conta(client: AsyncClient, db: AsyncSession, make_user):
    dono = await make_user()
    db.add(StoreInfo(user_id=dono.id, platform="Interno", bling_store_id="203999999"))
    await db.commit()
    await _pedido(db, "500500", ["dg053.ci"], loja="203999999")
    await _nota(db, "66", "500500")
    assert (await _pergunta(client, _corpo(nf="66"))).json() == {"encontrada": False}


async def test_mesmo_numero_em_dois_emitentes(client: AsyncClient, db: AsyncSession):
    """NF 12345 da Charlots em outubro e da Uranyx em setembro: cada token vê só a sua."""
    await _pedido(db, "500600", ["b009.24"], dia=date(2026, 10, 1))
    await _nota(db, "12345", "500600", dia=date(2026, 10, 1))
    await _pedido(db, "500601", ["dg053.ci"], dia=date(2026, 9, 10))
    await _nota(db, "12345", "500601", dia=date(2026, 9, 11))
    assert (await _pergunta(client, _corpo(data_compra="2026-10-01"), CH)).json() == {
        "encontrada": True
    }
    assert (await _pergunta(client, _corpo(data_compra="2026-10-01"), UR)).json() == {
        "encontrada": False
    }
    assert (await _pergunta(client, _corpo(data_compra="2026-09-10"), UR)).json() == {
        "encontrada": True
    }


# ─────────────── o 2º fator ───────────────


@pytest.mark.parametrize(
    ("data", "esperado"),
    [
        (DIA_PEDIDO - timedelta(days=6), False),
        (DIA_PEDIDO - timedelta(days=5), True),  # 5 dias antes do pedido
        (DIA_PEDIDO, True),
        (DIA_NF, True),
        (DIA_NF + timedelta(days=15), True),  # 15 dias depois da NF (a data da entrega)
        (DIA_NF + timedelta(days=16), False),
        (date(2026, 9, 1), False),
    ],
)
async def test_janela_da_data(client: AsyncClient, celular_uranyx, data, esperado):
    r = await _pergunta(client, _corpo(data_compra=data.isoformat()))
    assert r.status_code == 200
    assert r.json() == {"encontrada": esperado}


@pytest.mark.parametrize(
    "pedido", ["500123", "2000009876543210", "2000-0098-7654-3210", "000500123"]
)
async def test_pedido_certo_vale_mesmo_com_a_data_errada(
    client: AsyncClient, celular_uranyx, pedido
):
    r = await _pergunta(client, _corpo(data_compra="2026-09-15", pedido=pedido))
    assert r.json() == {"encontrada": True}


async def test_pedido_de_outro_numero_nao_atrapalha_a_data(client: AsyncClient, celular_uranyx):
    """O cliente pôs o nº do pacote em vez do pedido: com a data certa, segue `true`."""
    assert (await _pergunta(client, _corpo(pedido="999888777"))).json() == {"encontrada": True}
    assert (
        await _pergunta(client, _corpo(pedido="999888777", data_compra="2026-09-15"))
    ).json() == {"encontrada": False}


async def test_pedido_amazon_com_hifens(client: AsyncClient, db: AsyncSession):
    await _pedido(db, "500700", ["dg053.ci"], numeroloja="702-1234567-7654321")
    await _nota(db, "88", "500700")
    r = await _pergunta(
        client, _corpo(nf="88", data_compra="2026-09-15", pedido="70212345677654321")
    )
    assert r.json() == {"encontrada": True}


async def test_chave_de_acesso_vale_sozinha(client: AsyncClient, celular_uranyx):
    """A chave inteira (44 dígitos) é o fator forte: a data não precisa bater."""
    r = await _pergunta(client, _corpo(nf=CHAVE, data_compra="2026-09-15"))
    assert r.json() == {"encontrada": True}
    impressa = " ".join(CHAVE[i : i + 4] for i in range(0, 44, 4))
    assert (await _pergunta(client, _corpo(nf=impressa, data_compra="2026-09-15"))).json() == {
        "encontrada": True
    }
    # Chave de outra nota com o mesmo nNF: só a data decide.
    outra = CHAVE[:-1] + "9"
    assert (await _pergunta(client, _corpo(nf=outra, data_compra="2026-09-15"))).json() == {
        "encontrada": False
    }
    assert (await _pergunta(client, _corpo(nf=outra))).json() == {"encontrada": True}
    # E a chave não abre a NF para a outra marca.
    assert (await _pergunta(client, _corpo(nf=CHAVE), CH)).json() == {"encontrada": False}


# ─────────────── a época sem cobertura ───────────────


async def test_compra_antiga_sem_nota_409(client: AsyncClient, celular_uranyx):
    """Antes de 01/09/2026 o DaVinci não tem as NFs completas: não diz "não existe"."""
    r = await _pergunta(client, _corpo(nf="4444", data_compra="2026-05-20"))
    assert r.status_code == 409
    assert r.json() == {"detail": {"code": "sites_nf_sem_cobertura"}}
    assert r.headers["Cache-Control"] == "no-store"
    # Mesmo com uma NF de mesmo número em outra data (não bate): 409, não false.
    assert (await _pergunta(client, _corpo(data_compra="2026-08-20"))).status_code == 409


async def test_compra_antiga_achada_e_true(client: AsyncClient, db: AsyncSession):
    await _pedido(db, "400800", ["dg041.pi"], dia=date(2026, 4, 2), numeroloja="2000003333")
    await _nota_emitida(db, "002468", "2000003333", dia=date(2026, 4, 3))
    r = await _pergunta(client, _corpo(nf="2468", data_compra="2026-04-02"))
    assert r.status_code == 200
    assert r.json() == {"encontrada": True}


async def test_a_partir_de_setembro_e_false(client: AsyncClient, celular_uranyx):
    r = await _pergunta(client, _corpo(nf="4444", data_compra=rota.COBERTURA_DESDE.isoformat()))
    assert r.status_code == 200
    assert r.json() == {"encontrada": False}


# ─────────────── nada vaza ───────────────


async def test_nao_achada_e_data_errada_tem_a_mesma_resposta(client: AsyncClient, celular_uranyx):
    sem_nota = await _pergunta(client, _corpo(nf="4444"))
    data_errada = await _pergunta(client, _corpo(data_compra="2026-09-15"))
    outra_marca = await _pergunta(client, _corpo(), CH)
    for r in (sem_nota, data_errada, outra_marca):
        assert r.status_code == 200
        assert r.content == b'{"encontrada":false}'
        assert r.headers["Cache-Control"] == "no-store"
        assert "set-cookie" not in r.headers
    assert set(sem_nota.headers) == set(data_errada.headers)


async def test_resposta_so_tem_o_booleano(client: AsyncClient, celular_uranyx):
    r = await _pergunta(client, _corpo(pedido="500123"))
    assert r.content == b'{"encontrada":true}'
    for dado in ("500123", "2000009876543210", "12345", "Cliente", "12345678909", "dg053"):
        assert dado not in r.text


async def test_log_sem_nf_pedido_data_nem_ip(client: AsyncClient, celular_uranyx):
    with capture_logs() as logs:
        await client.post(
            URL,
            content=json.dumps(_corpo(nf="000.012.345", pedido="2000009876543210")),
            headers={**UR, "X-Forwarded-For": "203.0.113.77"},
        )
        await _pergunta(client, _corpo(nf="4444"))
        await _pergunta(client, {"nf": "sem numero", "data_compra": "2026-10-10"})
    nossos = [e for e in logs if str(e.get("event", "")).startswith("sites_validar_nf")]
    assert [e.get("resultado") for e in nossos if e["event"] == "sites_validar_nf"] == [
        "encontrada",
        "sem_nota",
    ]
    assert nossos[0]["via"] == "pedido"
    texto = repr(nossos)
    for dado in (
        "12345",
        "2000009876543210",
        "2026-10-10",
        "203.0.113.77",
        "4444",
        "sem numero",
        TOK_UR,
    ):
        assert dado not in texto


async def test_sem_cors_para_os_sites(client: AsyncClient):
    """Servidor a servidor: o navegador do cliente não tem permissão de chamar a rota."""
    for origem in ("https://uranyx.com.br", "https://www.charlots.com.br"):
        r = await client.options(
            URL,
            headers={"Origin": origem, "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in r.headers
        r = await client.post(URL, content=json.dumps(_corpo()), headers={**UR, "Origin": origem})
        assert "access-control-allow-origin" not in r.headers


async def test_fora_do_openapi(client: AsyncClient):
    r = await client.get("/api/openapi.json")
    assert "validar-nf" not in r.text


async def test_rapida(client: AsyncClient, db: AsyncSession, celular_uranyx):
    """Com algumas centenas de notas, bem abaixo dos 300 ms (o alvo do site)."""
    for i in range(300):
        numero = str(600000 + i)
        await _pedido(db, numero, ["dg053.ci" if i % 2 else "b009.24"], numeroloja=f"ML{numero}")
        await _nota(db, str(1000 + i), numero)

    tempos = []
    for _ in range(10):
        t = time.perf_counter()
        r = await _pergunta(client, _corpo(nf="1001"))
        tempos.append(time.perf_counter() - t)
        assert r.json() == {"encontrada": True}
    assert sorted(tempos)[8] < 0.3


def test_regras_de_marca_fixas():
    assert rota.ACESSORIOS_MALA == (
        "a006",
        "a015",
        "a073",
        "a074",
        "a075",
        "a076",
        "a077",
        "a078",
        "a079",
        "a080",
    )
    assert set(rota.MARCA_SKU) == {"uranyx", "charlots"}
