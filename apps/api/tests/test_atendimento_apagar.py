# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""O "apagar" da mensagem da loja (03/11/2026, o "Retirar" do Duoke).

POST /api/atendimento/mensagens/{id}/apagar com a caixa como em produção
(`SO_ADMIN` ligado, uma pessoa que responde, com a loja dela), um cliente
FALSO da Shopee no lugar do de verdade (nada sai para a rede) e o adaptador e
o serviço de verdade (`services/atendimento/shopee.apagar_mensagem`,
`services/atendimento/apagar.py`).

O que se segura, na ordem em que um erro custaria mais caro:

- só a mensagem que o DaVinci MANDOU (nunca `externo` = Duoke/Seller
  Center), `enviada`, com o id da Shopee; o `sim:` e o simulador ligado NUNCA
  chamam a plataforma; fora da janela, 409 sem chamar;
- a Shopee recusou → nada muda (409 `plataforma_recusou` com o erro curto);
  sem resposta → o status fica, `payload.apagar_incerto` e 502;
- apagou → `falhou` + `ERRO_APAGADA` + `payload.apagada`, o texto fica, a
  conversa volta para a fila e, se a apagada era da IA, a IA pausa;
- fora do escopo da pessoa = 404;
- a tela recebe `pode_apagar_ate` (as travas são do servidor);
- o corpo do `delete_message` (o formato do EcomPHP, não confirmado na doc).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import apagar, clientes, gravar, shopee
from app.services.atendimento.constantes import (
    ERRO_APAGADA,
    MSG_ENVIADA,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGEM_AUTO,
    ORIGEM_EXTERNO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
)
from app.services.marketplaces.shopee import ShopeeClient
from tests.lojas_da_pessoa import dar_lojas

# A regra de verdade das lojas por pessoa (sem o legado do conftest).
pytestmark = pytest.mark.lojas_por_pessoa

URL = "/api/atendimento"
MID = "2302748948493123953"
TEXTO = "Seu pedido sai hoje à tarde."


# ─────────────── falsos ───────────────


class ShopeeQueApaga:
    """O `ShopeeClient` no que o apagar usa: registra o pedido e devolve o combinado."""

    def __init__(self) -> None:
        self.shop_id = 111
        self.apagadas: list[tuple[str, str]] = []
        self.resposta: Any = {}

    async def chat_delete_message(self, message_id, message_type) -> dict:
        self.apagadas.append((str(message_id), message_type))
        if isinstance(self.resposta, BaseException):
            raise self.resposta
        return self.resposta


@pytest.fixture
def falso(monkeypatch) -> ShopeeQueApaga:
    f = ShopeeQueApaga()

    async def cliente_falso(integration):
        return f

    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_falso)
    return f


@pytest.fixture(autouse=True)
def _sem_simulador(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_simulador_exceto", "")
    return s


# ─────────────── fábrica ───────────────


async def _loja(db: AsyncSession, dono: User, nome: str = "kfa"):
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t", "shop_id": 111}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo="humano", status="ok"
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _cenario(
    db: AsyncSession,
    make_user,
    *,
    origem: str = ORIGEM_HUMANO,
    tipo: str = "texto",
    externo_id: str | None = MID,
    enviada_ha: timedelta = timedelta(seconds=30),
    status: str = MSG_ENVIADA,
    payload: dict | None = None,
    nome: str = "kfa",
    dono: User | None = None,
):
    """Uma conversa da Shopee: o comprador perguntou, a loja respondeu pelo DaVinci."""
    integ, canal = await _loja(db, dono or await make_user(), nome)
    agora = datetime.now(UTC)
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=f"conv-{uuid4().hex[:8]}",
        comprador_id="555",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"c-{uuid4().hex[:8]}",
        autor="cliente",
        texto="quando sai meu pedido?",
        enviada_em=agora - timedelta(minutes=10),
    )
    nossa = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=externo_id,
        autor="loja",
        origem=origem,
        tipo=tipo,
        texto=TEXTO,
        status=status,
        enviada_em=agora - enviada_ha,
        payload=payload if payload is not None else {"envio": {"message_id": externo_id}},
    )
    db.add(nossa)
    await db.flush()
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    return integ, conversa, nossa


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    """Quem responde (não é admin): a caixa como em produção, `SO_ADMIN` ligado."""
    u = await make_user()
    auth_as(u)
    return u


async def _apagar(client, mensagem):
    return await client.post(f"{URL}/mensagens/{mensagem.id}/apagar")


async def _relida(db: AsyncSession, obj):
    await db.refresh(obj)
    return obj


# ─────────────── sucesso ───────────────


async def test_apagou_marca_a_linha_reabre_a_fila_e_guarda_o_texto(
    client, db, make_user, pessoa, falso
):
    integ, conversa, nossa = await _cenario(db, make_user)
    await dar_lojas(db, pessoa, integ)
    assert (await _relida(db, conversa)).aguardando_resposta is False
    falso.resposta = {"message_id": int(MID)}

    r = await _apagar(client, nossa)

    assert r.status_code == 200, r.text
    # O pedido que foi à Shopee: o id como ela devolveu e o tipo dela.
    assert falso.apagadas == [(MID, "text")]
    corpo = r.json()
    assert corpo["mensagem"]["status"] == MSG_FALHOU
    assert corpo["mensagem"]["erro"] == ERRO_APAGADA
    assert corpo["mensagem"]["apagada"] is True
    assert corpo["mensagem"]["pode_apagar_ate"] is None
    # O texto fica para a equipe.
    assert corpo["mensagem"]["texto"] == TEXTO
    # A resposta apagada não conta: o comprador volta a esperar.
    assert corpo["conversa"]["aguardando_resposta"] is True
    assert corpo["ia_pausada"] is False

    await _relida(db, nossa)
    assert (nossa.status, nossa.erro, nossa.texto) == (MSG_FALHOU, ERRO_APAGADA, TEXTO)
    marca = nossa.payload["apagada"]
    assert marca["por"] == apagar.POR_DAVINCI
    assert marca["user_id"] == str(pessoa.id)
    assert datetime.fromisoformat(marca["em"]) <= datetime.now(UTC)
    assert marca["resposta"] == {"message_id": int(MID)}
    # O que já estava no payload continua.
    assert nossa.payload["envio"] == {"message_id": MID}
    c = await _relida(db, conversa)
    assert c.aguardando_resposta is True and c.ia_pausada is False

    # Apagada não se apaga de novo (e nada vai à Shopee).
    r = await _apagar(client, nossa)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, apagar.MENSAGEM_NAO_APAGAVEL)
    assert len(falso.apagadas) == 1


async def test_apagou_a_da_ia_pausa_a_ia_na_conversa(client, db, make_user, pessoa, falso):
    """Nas lojas em AUTO a IA mandaria de novo a mesma coisa no minuto seguinte."""
    integ, conversa, nossa = await _cenario(db, make_user, origem=ORIGEM_IA)
    await dar_lojas(db, pessoa, integ)

    r = await _apagar(client, nossa)

    assert r.status_code == 200, r.text
    assert r.json()["ia_pausada"] is True
    assert r.json()["conversa"]["ia_pausada"] is True
    assert (await _relida(db, conversa)).ia_pausada is True
    assert (await _relida(db, nossa)).erro == ERRO_APAGADA


@pytest.mark.parametrize(
    ("tipo", "payload", "tipo_shopee"),
    [
        ("imagem", {"envio": {"message_id": MID, "imagem_url": "https://cf.shopee/x"}}, "image"),
        ("pedido", {"automacao": {"parte": "cartao_pedido", "pedido": "251103ABC"}}, "order"),
        (
            "outro",
            {"automacao": {"parte": "figurinha", "figurinha": "0012", "pacote": "br_shoppito"}},
            "sticker",
        ),
        # O que a própria Shopee disse vale mais que o tipo da linha.
        ("texto", {"envio": {"message_id": MID, "message_type": "text"}}, "text"),
    ],
    ids=["imagem", "cartao", "figurinha", "tipo-da-shopee"],
)
async def test_cada_tipo_vai_com_o_message_type_da_shopee(
    client, db, make_user, pessoa, falso, tipo, payload, tipo_shopee
):
    integ, _conversa, nossa = await _cenario(
        db, make_user, origem=ORIGEM_AUTO, tipo=tipo, payload=payload
    )
    await dar_lojas(db, pessoa, integ)
    r = await _apagar(client, nossa)
    assert r.status_code == 200, r.text
    assert falso.apagadas == [(MID, tipo_shopee)]


# ─────────────── travas: nada vai à Shopee ───────────────


async def test_prazo_vencido_e_409_sem_chamar(client, db, make_user, pessoa, falso):
    integ, _conversa, nossa = await _cenario(
        db, make_user, enviada_ha=shopee.JANELA_APAGAR + timedelta(seconds=5)
    )
    await dar_lojas(db, pessoa, integ)
    r = await _apagar(client, nossa)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, apagar.PRAZO_APAGAR_PASSOU)
    assert falso.apagadas == []
    assert (await _relida(db, nossa)).status == MSG_ENVIADA


async def test_janela_conta_pelo_relogio_da_shopee(client, db, make_user, pessoa, falso):
    """O `created_timestamp` da resposta do envio (o da Shopee) é anterior ao
    `enviada_em` (a hora em que ela respondeu): vale o menor."""
    agora = datetime.now(UTC)
    integ, _conversa, nossa = await _cenario(
        db,
        make_user,
        enviada_ha=timedelta(seconds=10),
        payload={
            "envio": {
                "message_id": MID,
                "created_timestamp": int((agora - timedelta(minutes=4)).timestamp()),
            }
        },
    )
    await dar_lojas(db, pessoa, integ)
    r = await _apagar(client, nossa)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, apagar.PRAZO_APAGAR_PASSOU)
    assert falso.apagadas == []


async def test_mensagem_do_duoke_nunca_se_apaga_por_aqui(client, db, make_user, pessoa, falso):
    """`externo` = mandada pelo Duoke/Seller Center: a Shopee só deixa o app que mandou."""
    integ, _conversa, nossa = await _cenario(db, make_user, origem=ORIGEM_EXTERNO)
    await dar_lojas(db, pessoa, integ)
    r = await _apagar(client, nossa)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, apagar.MENSAGEM_NAO_APAGAVEL)
    assert falso.apagadas == []


async def test_simulador_nunca_chama_a_shopee(client, db, make_user, pessoa, falso, _sem_simulador):
    # O id que o simulador deu ao envio que fingiu.
    integ, _conversa, sim = await _cenario(db, make_user, externo_id=f"sim:{uuid4()}")
    await dar_lojas(db, pessoa, integ)
    r = await _apagar(client, sim)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, apagar.MENSAGEM_NAO_APAGAVEL)
    # E com o simulador ligado, nem a de id de verdade.
    _sem_simulador.atendimento_simulador = True
    integ2, _conversa2, nossa = await _cenario(db, make_user, nome="kfa2")
    await dar_lojas(db, pessoa, integ2)
    r = await _apagar(client, nossa)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, apagar.MENSAGEM_NAO_APAGAVEL)
    assert falso.apagadas == []


async def test_fora_do_escopo_da_pessoa_e_404(client, db, make_user, pessoa, falso):
    dono = await make_user()
    integ_a, _ca, _na = await _cenario(db, make_user, nome="loja-a", dono=dono)
    _integ_b, _cb, da_outra = await _cenario(db, make_user, nome="loja-b", dono=dono)
    await dar_lojas(db, pessoa, integ_a)
    r = await _apagar(client, da_outra)
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "mensagem_nao_encontrada")
    r = await client.post(f"{URL}/mensagens/{uuid4()}/apagar")
    assert r.status_code == 404
    assert falso.apagadas == []
    assert (await _relida(db, da_outra)).status == MSG_ENVIADA


async def test_quem_nao_ve_a_caixa_nao_apaga(client, db, make_user, auth_as, falso):
    """O operador de estoque nem vê a caixa: 403 antes de qualquer trava."""
    integ, _conversa, nossa = await _cenario(db, make_user)
    operador = await make_user(permissions={"controle_estoque": {"view": True}})
    operador.stock_tags = ["cd"]
    await db.commit()
    await dar_lojas(db, operador, integ)
    auth_as(operador)
    r = await _apagar(client, nossa)
    assert r.status_code == 403
    assert falso.apagadas == []


# ─────────────── a Shopee não apagou ───────────────


async def test_recusa_da_shopee_nao_muda_nada(client, db, make_user, pessoa, falso):
    integ, conversa, nossa = await _cenario(db, make_user)
    await dar_lojas(db, pessoa, integ)
    falso.resposta = RuntimeError(
        "shopee_chat_delete error_param: message cannot be recalled after 3 minutes"
    )
    r = await _apagar(client, nossa)
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": apagar.PLATAFORMA_RECUSOU,
        "erro": "shopee error_param: message cannot be recalled after 3 minutes",
    }
    await _relida(db, nossa)
    assert (nossa.status, nossa.erro) == (MSG_ENVIADA, None)
    assert "apagada" not in nossa.payload and "apagar_incerto" not in nossa.payload
    assert (await _relida(db, conversa)).aguardando_resposta is False


async def test_http_4xx_fora_do_json_e_recusa(client, db, make_user, pessoa, falso):
    """O caminho errado (ou o app sem a permissão) volta 4xx: o pedido nem entrou."""
    integ, _conversa, nossa = await _cenario(db, make_user)
    await dar_lojas(db, pessoa, integ)
    falso.resposta = RuntimeError("shopee_chat_delete HTTP 404: <html>not found")
    r = await _apagar(client, nossa)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, apagar.PLATAFORMA_RECUSOU)
    assert r.json()["detail"]["erro"] == "shopee HTTP 404"
    assert (await _relida(db, nossa)).status == MSG_ENVIADA


@pytest.mark.parametrize(
    "erro",
    [
        httpx.ReadTimeout("t"),
        RuntimeError("shopee_chat_delete error_server: busy"),
        RuntimeError("shopee_chat_delete HTTP 502: <html>bad gateway"),
    ],
    ids=["timeout", "erro-interno", "http-502"],
)
async def test_sem_resposta_e_incerto_502_e_o_status_fica(
    client, db, make_user, pessoa, falso, erro
):
    integ, conversa, nossa = await _cenario(db, make_user)
    await dar_lojas(db, pessoa, integ)
    falso.resposta = erro
    r = await _apagar(client, nossa)
    assert (r.status_code, r.json()["detail"]["code"]) == (502, apagar.APAGAR_INCERTO)
    await _relida(db, nossa)
    assert (nossa.status, nossa.erro) == (MSG_ENVIADA, None)
    incerto = nossa.payload["apagar_incerto"]
    assert incerto["user_id"] == str(pessoa.id) and incerto["erro"]
    assert (await _relida(db, conversa)).aguardando_resposta is False
    # Tentar de novo é seguro (apagar duas vezes não manda nada ao comprador).
    falso.resposta = {}
    r = await _apagar(client, nossa)
    assert r.status_code == 200, r.text
    assert len(falso.apagadas) == 2


# ─────────────── a saída da conversa ───────────────


async def test_pode_apagar_ate_na_saida_da_conversa(client, db, make_user, pessoa, falso):
    integ, conversa, nossa = await _cenario(db, make_user, enviada_ha=timedelta(seconds=40))
    await dar_lojas(db, pessoa, integ)
    externa = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id="2302748948493000001",
        autor="loja",
        origem=ORIGEM_EXTERNO,
        tipo="texto",
        texto="pelo Duoke",
        status=MSG_ENVIADA,
        enviada_em=datetime.now(UTC) - timedelta(seconds=20),
    )
    db.add(externa)
    await db.commit()
    await db.refresh(nossa)

    d = (await client.get(f"{URL}/conversas/{conversa.id}")).json()
    por_id = {m["id"]: m for m in d["mensagens"]}
    ate = datetime.fromisoformat(por_id[str(nossa.id)]["pode_apagar_ate"])
    enviada = nossa.enviada_em if nossa.enviada_em.tzinfo else nossa.enviada_em.replace(tzinfo=UTC)
    assert abs((ate - (enviada + shopee.JANELA_APAGAR)).total_seconds()) < 1
    assert por_id[str(nossa.id)]["apagada"] is False
    # A do Duoke e a do comprador: sem botão.
    outras = [m for i, m in por_id.items() if i != str(nossa.id)]
    assert {m["pode_apagar_ate"] for m in outras} == {None}


def _par(**campos) -> tuple[AtendimentoConversa, AtendimentoMensagem]:
    """Conversa e mensagem em memória (sem banco), para as travas PURAS."""
    conversa = AtendimentoConversa(
        id=uuid4(),
        plataforma=campos.pop("plataforma", "shopee"),
        canal=campos.pop("canal", "chat"),
        externo_id="379920374879551489",
        dados=campos.pop("dados", {}),
    )
    m = AtendimentoMensagem(
        id=uuid4(),
        conversa_id=conversa.id,
        autor=campos.pop("autor", "loja"),
        origem=campos.pop("origem", ORIGEM_HUMANO),
        tipo=campos.pop("tipo", "texto"),
        texto=TEXTO,
        status=campos.pop("status", MSG_ENVIADA),
        externo_id=campos.pop("externo_id", MID),
        enviada_em=campos.pop("enviada_em", datetime.now(UTC) - timedelta(seconds=30)),
        payload=campos.pop("payload", {}),
    )
    assert not campos
    return conversa, m


def test_travas_puras_de_quem_ganha_o_botao():
    conversa, m = _par()
    assert apagar.pode_apagar_ate(m, conversa) is not None
    sem_botao = {
        "afiliado": _par(dados={"afiliado": True}),
        "video": _par(tipo="video"),
        "outro sem ser figurinha": _par(tipo="outro"),
        "revisar": _par(status=MSG_REVISAR),
        "falhou": _par(status=MSG_FALHOU),
        "sem id": _par(externo_id=None),
        "id não numérico": _par(externo_id="abc"),
        "cliente": _par(autor="cliente", origem="cliente"),
        "ml": _par(plataforma="ml"),
        "pós-venda": _par(canal="pos_venda"),
        "vídeo pela Shopee": _par(payload={"sync": {"message_type": "video"}}),
        "sem hora": _par(enviada_em=None),
    }
    for nome, (c, msg) in sem_botao.items():
        assert apagar.pode_apagar_ate(msg, c) is None, nome
    # A mensagem de outra conversa nunca passa (a rota sempre manda a dela).
    outra, _ = _par()
    assert apagar.pode_apagar_ate(m, outra) is None


# ─────────────── o cliente da Shopee ───────────────


async def test_cliente_manda_o_corpo_do_delete_message(monkeypatch):
    chamadas: list[dict] = []

    async def _call(self, method, path, *, params=None, json=None, what="shopee"):
        chamadas.append({"method": method, "path": path, "json": json, "what": what})
        return {}

    monkeypatch.setattr(ShopeeClient, "_call", _call)
    cliente = ShopeeClient({"shop_id": 111, "access_token": "t", "expires_at": 9_999_999_999})
    await cliente.chat_delete_message(MID, "text")
    assert chamadas == [
        {
            "method": "POST",
            "path": "/api/v2/sellerchat/delete_message",
            # Numérico, como a Shopee devolveu no send_message.
            "json": {"message_id": int(MID), "message_type": "text"},
            "what": "shopee_chat_delete",
        }
    ]
    for mid, tipo in (("sim:1", "text"), ("", "text"), (MID, "")):
        with pytest.raises(ValueError):
            await cliente.chat_delete_message(mid, tipo)
    assert len(chamadas) == 1
