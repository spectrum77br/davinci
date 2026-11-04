"""A NF cheia que não está no `nf_nota`, lida do Bling (04/11/2026, `nf_bling.py`, decisão D1).

O que estes testes seguram, com o Bling falso (nada de rede):

- a nota errada nunca entra nem sai: a parcial, a de outro CPF, a cancelada no Bling, a devolução,
  a sem protocolo, a já ligada a outro pedido;
- qualquer falha do Bling deixa o caso com a pessoa, sem exceção, e o pedido só volta ao Bling
  depois de `REPETE_EM_S`;
- as contas de emissão: só a `active`, com o token em dia e a nota do complemento do pedido;
- o caminho feliz: a nota entra no `nf_nota` e sai pela mesma régua da `conferir_nf`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.models import BlingOrder, NfNota
from app.models.bling_nota import BlingNota, BlingNotaEmitida
from app.routers import notas_fiscais
from app.services.atendimento import ia, nf_bling, painel
from app.services.marketplaces.bling import BlingClient
from tests.test_atendimento_agente_ligacao import agente_ligado  # noqa: F401
from tests.test_atendimento_danfe import _xml
from tests.test_atendimento_ia import PEDIDO_MKT, ia_ligada  # noqa: F401
from tests.test_atendimento_nf_automatica import (
    CPF,
    PEDIDO_BLING,
    _cenario,
    _chave,
    _decisao,
    _rascunho,
    ligado,  # noqa: F401
    plataforma,  # noqa: F401
)

NOTA_ID = 777
LINK = "https://s3.bling.test/nota.xml"


class _BlingFalso:
    """A conta principal: o pedido, o detalhe da nota, e o que foi chamado."""

    def __init__(self, *, ordem: dict | None = None, detalhe: dict | None = None) -> None:
        self.ordem = (
            {"contato": {"numeroDocumento": CPF}, "notaFiscal": {"id": NOTA_ID}}
            if ordem is None
            else ordem
        )
        self.detalhe = {"situacao": 6, "xml": LINK} if detalhe is None else detalhe
        self.chamadas: list[str] = []
        self.erro: Exception | None = None
        self.erro_na_nota: Exception | None = None

    async def get_order(self, bling_id: int) -> dict:
        self.chamadas.append(f"pedido:{bling_id}")
        if self.erro:
            raise self.erro
        return self.ordem

    async def get_nfe(self, nfe_id: int) -> dict:
        self.chamadas.append(f"nota:{nfe_id}")
        if self.erro_na_nota:
            raise self.erro_na_nota
        return self.detalhe


@pytest.fixture
def bling(monkeypatch):
    """A conta principal falsa, a marca do Redis em memória e o XML da nota no link."""
    falso = _BlingFalso()
    marcas: set[str] = set()
    baixar: dict[str, bytes | None] = {LINK: _xml(chave=_chave(1883), numero="1883", v_nf="424.00")}

    class _Redis:
        async def set(self, chave, valor, *, nx=False, ex=None):
            assert nx and ex == nf_bling.REPETE_EM_S
            if chave in marcas:
                return None
            marcas.add(chave)
            return True

    async def cliente(session):
        return falso

    async def download(url):
        return baixar.get(url)

    monkeypatch.setattr(nf_bling, "redis", _Redis())
    monkeypatch.setattr(painel, "_cliente_bling", cliente)
    monkeypatch.setattr(notas_fiscais, "_download_xml", download)
    falso.baixar = baixar  # type: ignore[attr-defined]
    return falso


@pytest.fixture(autouse=True)
async def _sem_contas_de_emissao(db):
    """As contas de emissão não estão na faxina do `conftest`: cada teste limpa as dele."""
    yield
    await db.rollback()
    await db.execute(delete(BlingNotaEmitida))
    await db.execute(delete(BlingNota))
    await db.commit()


async def _pedido_de_outubro(db) -> None:
    """O pedido é de antes do XML de teste (emitido em 27/10): a `_nota_unica` aceita."""
    pedido = await db.scalar(select(BlingOrder).where(BlingOrder.numero == PEDIDO_BLING))
    pedido.data = datetime(2026, 10, 20, tzinfo=UTC)
    await db.commit()


async def _sem_nf(db, make_user, **kw: Any):
    conversa, gatilho = await _cenario(db, make_user, notas=(), **kw)
    await _pedido_de_outubro(db)
    return conversa, gatilho


async def _no_nf_nota(db) -> list[NfNota]:
    return list((await db.execute(select(NfNota))).scalars().all())


# ─────────────── o caminho feliz ───────────────


async def test_sem_nf_no_nf_nota_le_no_bling_e_manda(db, make_user, ligado, plataforma, bling):  # noqa: F811
    conversa, gatilho = await _sem_nf(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert [s["tipo"] for s in plataforma] == ["imagem", "texto"]
    assert bling.chamadas == [f"pedido:{int(PEDIDO_BLING)}", f"nota:{NOTA_ID}"]
    notas = await _no_nf_nota(db)
    assert [(n.chave, n.pedido_bling, n.situacao) for n in notas] == [
        (_chave(1883), PEDIDO_BLING, "100")
    ]
    assert notas[0].destinatario_doc == CPF and notas[0].valor == Decimal("424.00")


async def test_so_embalagem_no_nf_nota_le_a_cheia_no_bling(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
):
    conversa, gatilho = await _cenario(db, make_user, notas=(("embalagem", 77, "8.48"),))
    await _pedido_de_outubro(db)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert len(await _no_nf_nota(db)) == 2


async def test_com_a_cheia_no_nf_nota_nao_vai_ao_bling(db, make_user, ligado, plataforma, bling):  # noqa: F811
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert bling.chamadas == []


async def test_pedido_antigo_sem_cpf_no_espelho_confere_com_o_do_bling(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
):
    """Antes de 08/2026 o espelho não guarda o CPF: vale o do pedido no Bling."""
    conversa, gatilho = await _sem_nf(db, make_user, documento=None)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)


async def test_nota_sem_pedido_no_nf_nota_ganha_o_vinculo(db, make_user, ligado, plataforma, bling):  # noqa: F811
    """O coletor subiu sem conseguir casar: o Bling diz de qual pedido é."""
    conversa, gatilho = await _sem_nf(db, make_user)
    db.add(
        NfNota(
            chave=_chave(1883),
            pedido_bling=None,
            numero="1883",
            serie="1",
            emitente_cnpj="00000000000191",
            valor=Decimal("424.00"),
            data_emissao=datetime(2026, 10, 27, tzinfo=UTC),
            situacao="100",
            xml=_xml(chave=_chave(1883), numero="1883", v_nf="424.00"),
        )
    )
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert [n.pedido_bling for n in await _no_nf_nota(db)] == [PEDIDO_BLING]


# ─────────────── a nota errada nunca entra ───────────────


@pytest.mark.parametrize(
    "xml",
    [
        # A parcial: a de embalagem, e a da faixa do meio.
        _xml(chave=_chave(1883), numero="1883", v_nf="8.48"),
        _xml(chave=_chave(1883), numero="1883", v_nf="212.00"),
        # A devolução, a complementar, a homologação, a sem protocolo e o que não é NF-e.
        _xml(chave=_chave(1883), numero="1883", v_nf="424.00", tp_nf="0"),
        _xml(chave=_chave(1883), numero="1883", v_nf="424.00", fin_nfe="2"),
        _xml(chave=_chave(1883), numero="1883", v_nf="424.00", tp_amb="2"),
        _xml(chave=_chave(1883), numero="1883", v_nf="424.00", protocolo=False),
        b"<html>nao e xml",
    ],
)
async def test_nota_que_nao_e_a_cheia_nao_entra_e_nao_sai(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
    xml,
):
    conversa, gatilho = await _sem_nf(db, make_user)
    bling.baixar[LINK] = xml
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []
    assert await _no_nf_nota(db) == []


async def test_parcial_no_nf_nota_e_parcial_no_bling_segue_so_embalagem(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
):
    conversa, gatilho = await _cenario(db, make_user, notas=(("embalagem", 77, "8.48"),))
    await _pedido_de_outubro(db)
    bling.baixar[LINK] = _xml(chave=_chave(1883), numero="1883", v_nf="8.48")
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_so_embalagem" and plataforma == []
    assert [n.numero for n in await _no_nf_nota(db)] == ["77"]


async def test_nota_de_outro_cpf_nao_entra_e_nao_sai(db, make_user, ligado, plataforma, bling):  # noqa: F811
    conversa, gatilho = await _sem_nf(db, make_user, documento="98765432100")
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []
    assert await _no_nf_nota(db) == []


async def test_pedido_antigo_com_outro_cpf_no_bling_nao_entra(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
):
    conversa, gatilho = await _sem_nf(db, make_user, documento=None)
    bling.ordem = {"contato": {"numeroDocumento": "98765432100"}, "notaFiscal": {"id": NOTA_ID}}
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []
    assert await _no_nf_nota(db) == []


async def test_pedido_antigo_sem_cpf_em_lugar_nenhum_nao_entra(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
):
    conversa, gatilho = await _sem_nf(db, make_user, documento=None)
    bling.ordem = {"contato": {}, "notaFiscal": {"id": NOTA_ID}}
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []
    assert await _no_nf_nota(db) == []


async def test_nf_com_cpf_da_nota_diferente_do_pedido_antigo_segura_na_conferencia(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
):
    """O coletor já tinha a nota do pedido antigo: o CPF do Bling segura na `conferir_nf`."""
    conversa, gatilho = await _cenario(db, make_user, documento=None)
    await _pedido_de_outubro(db)
    bling.ordem = {"contato": {"numeroDocumento": "98765432100"}}
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:destinatario_diferente" and plataforma == []


@pytest.mark.parametrize(
    "detalhe",
    [
        {"situacao": 2, "xml": LINK},  # cancelada no Bling (o XML continua dizendo autorizada)
        {"situacao": 1, "xml": LINK},  # pendente
        {"situacao": 6},  # sem o link
    ],
)
async def test_nota_cancelada_no_bling_ou_sem_xml_nao_entra(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
    detalhe,
):
    conversa, gatilho = await _sem_nf(db, make_user)
    bling.detalhe = detalhe
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []
    assert await _no_nf_nota(db) == []


async def test_nota_ligada_a_outro_pedido_nao_muda_de_vinculo(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
):
    conversa, gatilho = await _sem_nf(db, make_user)
    db.add(
        NfNota(
            chave=_chave(1883),
            pedido_bling="999999",
            numero="1883",
            serie="1",
            valor=Decimal("424.00"),
            situacao="100",
            xml=_xml(chave=_chave(1883), numero="1883", v_nf="424.00"),
        )
    )
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []
    assert [n.pedido_bling for n in await _no_nf_nota(db)] == ["999999"]


async def test_duas_cheias_diferentes_no_bling_seguram(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
    monkeypatch,
):
    """A principal e a conta de emissão com cheias diferentes (a reemitida?): qual é a certa?"""
    conversa, gatilho = await _sem_nf(db, make_user)
    await _nota_na_conta(db)

    async def bling_get(token, path, params=None):
        return {"data": {"situacao": 6, "xml": "https://s3.bling.test/outra.xml"}}

    bling.baixar["https://s3.bling.test/outra.xml"] = _xml(
        chave=_chave(1884), numero="1884", v_nf="424.00"
    )
    monkeypatch.setattr(notas_fiscais, "_bling_get", bling_get)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:mais_de_uma_nf_cheia" and plataforma == []


async def test_pedido_cancelado_nao_vai_ao_bling(db, make_user, ligado, plataforma, bling):  # noqa: F811
    from app.services.bling_situacoes import SITUACAO_CANCELADO

    conversa, gatilho = await _sem_nf(db, make_user, situacao=str(SITUACAO_CANCELADO))
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_pedido_cancelado" and plataforma == []
    assert bling.chamadas == []


# ─────────────── a falha fica com a pessoa ───────────────


async def test_erro_no_bling_fica_com_a_pessoa_sem_excecao(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
):
    conversa, gatilho = await _sem_nf(db, make_user)
    bling.erro = RuntimeError("bling http 500")
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []
    assert await _no_nf_nota(db) == []


async def test_a_nota_sem_permissao_na_principal_nao_trava_as_contas(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
    monkeypatch,
):
    """O app da principal sem acesso a `/nfe`: o CPF do pedido vale e a conta de emissão serve."""
    conversa, gatilho = await _sem_nf(db, make_user, documento=None)
    bling.erro_na_nota = RuntimeError("403")
    await _nota_na_conta(db)

    async def bling_get(token, path, params=None):
        return {"data": {"situacao": 6, "xml": LINK}}

    monkeypatch.setattr(notas_fiscais, "_bling_get", bling_get)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)


async def test_bling_lento_para_no_teto(db, make_user, ligado, plataforma, bling, monkeypatch):  # noqa: F811
    conversa, gatilho = await _sem_nf(db, make_user)

    async def lento(bling_id):
        await asyncio.sleep(30)
        return {}

    bling.get_order = lento
    monkeypatch.setattr(nf_bling, "TEMPO_MAXIMO_S", 0.01)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []


async def test_redis_fora_do_ar_nao_vai_ao_bling(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    bling,
    monkeypatch,
):
    class _Fora:
        async def set(self, *a, **kw):
            raise ConnectionError("redis")

    monkeypatch.setattr(nf_bling, "redis", _Fora())
    conversa, gatilho = await _sem_nf(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and bling.chamadas == []


async def test_uma_tentativa_por_pedido_no_intervalo(db, make_user, bling):
    """Sem a cheia agora: o mesmo pedido não volta ao Bling a cada turno."""
    conversa, _ = await _sem_nf(db, make_user)
    bling.ordem = {"contato": {"numeroDocumento": CPF}}
    assert await nf_bling.trazer_a_cheia(db, PEDIDO_BLING) is False
    assert await nf_bling.trazer_a_cheia(db, PEDIDO_BLING) is False
    assert bling.chamadas == [f"pedido:{int(PEDIDO_BLING)}"]
    assert conversa is not None


async def test_pedido_que_nao_existe_e_id_ambiguo_nao_chamam_o_bling(db, make_user, bling):
    await _sem_nf(db, make_user)
    assert await nf_bling.trazer_a_cheia(db, "pedido-que-nao-existe") is False
    db.add(
        BlingOrder(
            bling_id=999,
            item_index=1,
            numero=PEDIDO_BLING,
            numeroloja=PEDIDO_MKT,
            total=Decimal("424.00"),
            situacao="9",
            item_codigo="b096.22",
            documento_destinatario=CPF,
            data=datetime(2026, 10, 20, tzinfo=UTC),
        )
    )
    await db.commit()
    assert await nf_bling.trazer_a_cheia(db, PEDIDO_BLING) is False
    assert bling.chamadas == []


# ─────────────── as contas de emissão ───────────────


async def _nota_na_conta(
    db,
    *,
    status: str = "active",
    expira: datetime | None = None,
    complemento: str = PEDIDO_MKT,
    situacao: int = 6,
    valor: str | None = None,
) -> None:
    conta = BlingNota(
        nome=f"conta-{status}-{complemento}",
        client_id="cid",
        basic_auth_b64="eA==",
        access_token="token-da-conta",  # noqa: S106
        refresh_token="rt",  # noqa: S106
        token_expires_at=expira or datetime.now(UTC) + timedelta(hours=3),
        status=status,
    )
    db.add(conta)
    await db.flush()
    db.add(
        BlingNotaEmitida(
            conta_id=conta.id,
            bling_id=555,
            numero="1883",
            situacao=situacao,
            data_emissao=datetime(2026, 10, 27, 12),
            complemento=complemento,
            valor=None if valor is None else Decimal(valor),
        )
    )
    await db.commit()


@pytest.fixture
def conta_de_emissao(bling, monkeypatch):
    """A principal sem nota; o GET da conta de emissão anota o token e o caminho."""
    bling.ordem = {"contato": {"numeroDocumento": CPF}}
    chamadas: list[tuple[str, str]] = []

    async def bling_get(token, path, params=None):
        chamadas.append((token, path))
        return {"data": {"situacao": 6, "xml": LINK}}

    monkeypatch.setattr(notas_fiscais, "_bling_get", bling_get)
    return chamadas


async def test_nota_da_conta_de_emissao_pelo_complemento_manda(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    conta_de_emissao,
):
    conversa, gatilho = await _sem_nf(db, make_user)
    await _nota_na_conta(db)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert conta_de_emissao == [("token-da-conta", "/nfe/555")]


@pytest.mark.parametrize(
    "nota",
    [
        # 13 das 14 contas estavam assim em 04/11: nunca usar o token morto.
        {"status": "pausada_token_morto"},
        # Vencido não renova aqui: o cron é quem renova (o Bling troca o refresh token a cada uso).
        {"expira": datetime(2026, 1, 1, tzinfo=UTC)},
        # A nota de outro pedido, a não emitida e a parcial já conhecida pelo espelho.
        {"complemento": "OUTROPEDIDO"},
        {"situacao": 2},
        {"valor": "8.48"},
    ],
)
async def test_conta_ou_nota_que_nao_serve_nao_chama_o_bling(
    db,
    make_user,
    ligado,  # noqa: F811
    plataforma,  # noqa: F811
    conta_de_emissao,
    nota,
):
    conversa, gatilho = await _sem_nf(db, make_user)
    await _nota_na_conta(db, **nota)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_nao_chegou" and plataforma == []
    assert conta_de_emissao == []


# ─────────────── as peças ───────────────


async def test_get_nfe_e_so_leitura(monkeypatch):
    chamadas: list[tuple[str, str]] = []

    class _Resposta:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"situacao": 6}}

    async def request(self, metodo, caminho, **kw):
        chamadas.append((metodo, caminho))
        return _Resposta()

    monkeypatch.setattr(BlingClient, "_request", request)
    assert await BlingClient({}).get_nfe(9) == {"situacao": 6}
    assert chamadas == [("GET", "/nfe/9")]


def test_a_cheia_do_pedido():
    cheia = _xml(chave=_chave(1883), numero="1883", v_nf="424.00")
    assert nf_bling._a_cheia_do_pedido(cheia, Decimal("424.00"), CPF).numero == "1883"
    # O documento do pedido desconhecido, ou o total: não dá para conferir.
    assert nf_bling._a_cheia_do_pedido(cheia, Decimal("424.00"), "") is None
    assert nf_bling._a_cheia_do_pedido(cheia, None, CPF) is None
    # 70% (a poofy) é cheia; 59% não.
    setenta = _xml(chave=_chave(1883), numero="1883", v_nf="70.00")
    assert nf_bling._a_cheia_do_pedido(setenta, Decimal("100.00"), CPF) is not None
    meio = _xml(chave=_chave(1883), numero="1883", v_nf="59.00")
    assert nf_bling._a_cheia_do_pedido(meio, Decimal("100.00"), CPF) is None


def test_documento_da_ordem():
    assert nf_bling._documento_da_ordem({"contato": {"numeroDocumento": "123.456.789-09"}}) == CPF
    assert (
        nf_bling._documento_da_ordem(
            {"contato": {}, "transporte": {"contato": {"numeroDocumento": CPF}}}
        )
        == CPF
    )
    assert nf_bling._documento_da_ordem({"contato": None}) == ""
