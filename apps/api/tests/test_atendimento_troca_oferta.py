"""Item 4, fase 4d — a OFERTA DE TROCA pelo chat da plataforma (07/10/2026).

`services/atendimento/troca_oferta.py` e a rota POST
/api/atendimento/pedidos/{numero_bling}/troca/oferta. O adaptador da Shopee
e o validador são FALSOS (nada sai daqui), e o Bling é proibido: a oferta
só fala com o comprador. O que estes testes seguram (decisão (f) do dono):

- a oferta sai SÓ pelo caminho único do envio (`enviar.enviar_resposta`),
  com as travas dele: o envio desligado e a loja em observar recusam sem
  chamar a plataforma (`envio_desligado`, `canal_nao_envia`);
- as travas da oferta antes do envio: a chave e o piloto da troca, o
  motivo (falta de estoque de UM item), a troca aberta, a sugestão
  elegível da 4b (nem fora, nem nível 0) e a conversa do pedido;
- o caminho feliz manda o texto da 4b (ou o editado), marca a mensagem
  (`payload.oferta_troca`) e grava a nota "Oferta de troca enviada (a -> b)
  por <nome>"; a plataforma que falha não vira nota;
- a troca que vem depois guarda `oferta_mensagem_id` (crítica M11);
- a permissão: `_so_admin` + `atendimento.edit` + `margem.edit`, como a troca
  (decisão (g) do dono: a oferta promete a troca ao comprador);
- as travas do PEDIDO que a troca recusaria com certeza (a plataforma, a NF
  na fila…) e o kit de lotes misturados: nada sai;
- a conversa que mudou: fala do CLIENTE ou da loja depois do que a tela tinha
  (`ultima_vista_id`; sem ele, a marca da falta de estoque) = 409
  `conversa_mudou`, e o `confirmar` envia mesmo assim;
- o `oferta_envio` do painel e da lista diz o mesmo que a rota (e leva o
  `ultima_mensagem_id`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import delete, select, update

from app.config import get_settings
from app.main import app
from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    AtendimentoTroca,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    NfCommand,
    NfFaturamento,
    Product,
    StoreInfo,
    UserRole,
)
from app.routers import atendimento as rota
from app.routers import atendimento_troca
from app.security.cipher import encrypt_json
from app.services.atendimento import (
    clientes,
    gravar,
    painel,
    troca,
    troca_oferta,
    troca_sugestoes,
    validador,
)
from app.services.atendimento import shopee as adaptador_shopee
from app.services.atendimento.constantes import ResultadoEnvio
from tests.test_atendimento_troca import BlingFalso, ShopeeFalsa, _ligar

NUMERO = "300101"
BLING_ID = 77001
NUMEROLOJA = "SHPTROCA1"
LOJA = "7001"
AGORA = datetime.now(UTC)
URL = f"/api/atendimento/pedidos/{NUMERO}/troca/oferta"
ROTA = "/api/atendimento/pedidos/{numero_bling}/troca/oferta"
ERRO_MARCA = "Aguardando Cancelamento — saldo negativo: {}"

A17_BRANCO = "Uranyx A17 Pro Max 12.64 - Branco"
A17_LARANJA = "Uranyx A17 Pro Max 12.64 - Laranja"
A17_AZUL = "Uranyx A17 Pro Max 12.64 - Azul"


# ─────────────── as chaves e os falsos ───────────────


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_envio_ativo",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_simulador",
        "atendimento_troca_sugestoes_ativa",
        "atendimento_troca_lote_auto",
        "nf_auto_enfileirar",
        "nf_auto_ml_amazon",
        "estoque_familia_ativo",
        "estoque_familia_redireciona",
        "prioridade_substitui_item",
    ):
        monkeypatch.setattr(s, nome, False)
    monkeypatch.setattr(s, "atendimento_troca_ativa", True)
    monkeypatch.setattr(s, "atendimento_troca_pedidos", "")
    monkeypatch.setattr(s, "atendimento_troca_folga_prazo_horas", 2.0)
    monkeypatch.setattr(s, "atendimento_troca_aceite_max_dias", 7)
    monkeypatch.setattr(s, "atendimento_troca_teto_custo_pct", 5.0)
    monkeypatch.setattr(s, "atendimento_troca_piso_nivel2_pct", -10.0)
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "estoque_familia_prefixos", "")
    monkeypatch.setattr(troca, "ESPERAS_CONFERENCIA_S", (0.0, 0.0, 0.0))
    troca_sugestoes.limpar_memoria()
    yield s
    troca_sugestoes.limpar_memoria()


@pytest.fixture(autouse=True)
def _sem_bling(monkeypatch):
    """A oferta nunca fala com o Bling (nem a Shopee do pedido): só com o comprador."""

    async def _proibido(*args, **kwargs):
        raise AssertionError("a oferta de troca não fala com o Bling nem com o pedido na Shopee")

    async def _sem_observacoes(session):
        return None

    async def _esquecer(bling_id):
        return None

    monkeypatch.setattr(troca, "_cliente_bling", _proibido)
    monkeypatch.setattr(troca, "_cliente_shopee", _proibido)
    # As Observações do painel: nenhum Bling por trás.
    monkeypatch.setattr(painel, "_cliente_bling", _sem_observacoes)
    monkeypatch.setattr(painel, "invalidar_observacoes", _esquecer)


@pytest.fixture(autouse=True)
def _validador_falso(monkeypatch):
    """Validador pelo contrato: normaliza espaços; reprova vazio e WhatsApp."""

    def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
        return " ".join((texto or "").split())

    def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
        motivos = []
        if not texto.strip():
            motivos.append("Resposta vazia.")
        if "whatsapp" in texto.lower():
            motivos.append("Contato fora da loja (WhatsApp).")
        return motivos

    monkeypatch.setattr(validador, "normalizar", normalizar)
    monkeypatch.setattr(validador, "validar", validar)


class _Chat:
    """O chat da Shopee falso: grava cada texto enviado e devolve o combinado."""

    def __init__(self, monkeypatch) -> None:
        self.textos: list[str] = []
        self.resultado = ResultadoEnvio(ok=True, externo_id="plat-oferta-1")

        async def enviar_texto(session, conversa, integration, cliente, texto):
            self.textos.append(texto)
            return self.resultado

        async def cliente_falso(integration):
            return object()

        monkeypatch.setattr(adaptador_shopee, "enviar_texto", enviar_texto)
        monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_falso)


@pytest.fixture
def chat(monkeypatch) -> _Chat:
    return _Chat(monkeypatch)


@pytest.fixture
def envio(monkeypatch, _chaves):
    """O envio pelo DaVinci LIGADO (o padrão dos testes é desligado)."""
    monkeypatch.setattr(_chaves, "atendimento_envio_ativo", True)
    return _chaves


# ─────────────── fábrica ───────────────


async def _pedido(db, itens=(("dg053.sp", 1, 501),), *, status=None):
    for i, (sku, qtd, pid) in enumerate(itens):
        db.add(
            BlingOrder(
                bling_id=BLING_ID,
                item_index=i,
                numero=NUMERO,
                numeroloja=NUMEROLOJA,
                situacao="83955",
                loja=LOJA,
                item_codigo=sku,
                item_produto_id=pid,
                item_descricao=f"Produto {sku}",
                item_quantidade=qtd,
                itemvalor=899,
                status=status,
                data=AGORA - timedelta(days=1),
                marketplace_ship_deadline=AGORA + timedelta(hours=20),
            )
        )
    await db.commit()


async def _marca(db, skus=("dg053.sp",)):
    quando = AGORA - timedelta(hours=3)
    db.add(
        NfFaturamento(
            pedido_bling=NUMERO,
            status_faturamento="sem_estoque",
            erro_faturamento=ERRO_MARCA.format(", ".join(skus)),
            created_at=quando,
            updated_at=quando,
        )
    )
    await db.commit()


async def _produtos(db, dono, linhas):
    """(sku, nome, estoque no DaVinci, custo, id no Bling)."""
    for sku, nome, estoque, custo, pid in linhas:
        db.add(
            Product(
                user_id=dono.id,
                sku=sku,
                name=nome,
                stock=estoque,
                formato="S",
                situacao="A",
                bling_cost_price=Decimal(str(custo)),
                bling_product_id=pid,
            )
        )
    await db.commit()


async def _cenario(
    db,
    make_user,
    monkeypatch,
    *,
    modo="humano",
    conversa=True,
    nivel0=False,
    itens=None,
    marca=None,
    status=None,
):
    """O pedido 300101 em 83955 por falta de estoque do dg053.sp, com a conversa na Shopee.

    O parecido elegível é o dg054.sp (Laranja, nível 1); o dg055.sp (Azul)
    está sem estoque no DaVinci. `nivel0`: dg053.ci → dg053.sp (o mesmo
    produto, com a soma por família ligada).
    """
    dono = await make_user()
    adm = await make_user(role=UserRole.ADMIN, email=f"adm-{uuid4().hex[:6]}@davinci-test.com")
    adm.name = "Fulana Atendimento"
    await db.commit()
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo=modo, status="ok"
    )
    db.add(canal)
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform="shopee",
            account_name="kfa",
            bling_store_id=LOJA,
            integration_id=integ.id,
        )
    )
    await db.commit()
    if nivel0:
        s = get_settings()
        monkeypatch.setattr(s, "estoque_familia_ativo", True)
        monkeypatch.setattr(s, "estoque_familia_redireciona", True)
        await _produtos(
            db, dono, [("dg053.ci", A17_BRANCO, 0, 495, 511), ("dg053.sp", A17_BRANCO, 7, 495, 512)]
        )
        antigo, novo = "dg053.ci", "dg053.sp"
        itens = itens or (("dg053.ci", 1, 511),)
    else:
        await _produtos(
            db,
            dono,
            [
                ("dg053.sp", A17_BRANCO, 0, 495, 501),
                ("dg054.sp", A17_LARANJA, 9, 495, 502),
                ("dg055.sp", A17_AZUL, 0, 495, 503),
                ("a001.sp", "Fone Uranyx", 0, 20, 504),
            ],
        )
        antigo, novo = "dg053.sp", "dg054.sp"
        itens = itens or (("dg053.sp", 1, 501),)
    await _pedido(db, itens, status=status)
    await _marca(db, marca or (antigo,))
    conv = None
    if conversa:
        conv, _ = await gravar.upsert_conversa(
            db,
            canal=canal,
            integration=integ,
            plataforma="shopee",
            canal_nome="chat",
            externo_id="conv-oferta",
            pedido_marketplace=NUMEROLOJA,
            comprador_nome="Comprador Fictício",
        )
        await db.commit()
    return SimpleNamespace(
        dono=dono, adm=adm, integ=integ, canal=canal, conversa=conv, antigo=antigo, novo=novo
    )


async def _mensagens(db, conversa_id, *, autor=None, tipo=None):
    q = select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa_id)
    if autor:
        q = q.where(AtendimentoMensagem.autor == autor)
    if tipo:
        q = q.where(AtendimentoMensagem.tipo == tipo)
    q = q.order_by(AtendimentoMensagem.created_at).execution_options(populate_existing=True)
    return list((await db.execute(q)).scalars().all())


async def _nada_saiu(db, c, chat):
    assert chat.textos == []
    if c.conversa is not None:
        assert await _mensagens(db, c.conversa.id, autor="loja") == []
        assert await _mensagens(db, c.conversa.id, tipo="nota") == []


def _texto_4b() -> str:
    return troca_sugestoes.texto_oferta(A17_BRANCO, A17_LARANJA)


# ─────────────── a rota e a permissão ───────────────


def test_rota_tem_a_trava_da_caixa_e_a_margem():
    [r] = [r for r in app.routes if isinstance(r, APIRoute) and r.path == ROTA]
    assert r.methods == {"POST"}
    assert r.endpoint.__module__ == atendimento_troca.__name__
    deps = [d.call for d in r.dependant.dependencies]
    assert rota._so_admin in deps
    assert rota._edit in deps
    # Decisão (g): a oferta promete a troca — pede o mesmo que a troca.
    assert atendimento_troca._margem_edit in deps


async def test_quem_so_le_nao_envia(db, client, make_user, auth_as, monkeypatch, envio, chat):
    c = await _cenario(db, make_user, monkeypatch)
    leitor = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    auth_as(leitor)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "atendimento_so_leitura"
    await _nada_saiu(db, c, chat)


async def test_pede_atendimento_edit_e_a_margem(
    db, client, make_user, auth_as, monkeypatch, envio, chat
):
    """Fora da observação: sem atendimento.edit = 403; sem margem.edit = 403 (quem não
    pode trocar não promete a troca); com os dois, envia."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    c = await _cenario(db, make_user, monkeypatch)
    sem = await make_user(permissions={"atendimento": {"view": True}, "margem": {"edit": True}})
    auth_as(sem)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "forbidden", "resource": "atendimento", "action": "edit"}
    so_atendimento = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    auth_as(so_atendimento)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "forbidden", "resource": "margem", "action": "edit"}
    # O botão diz o mesmo (cinza, "só leitura"), sem a Margem.
    out = await painel.painel_da_conversa(db, c.conversa, user=so_atendimento)
    assert out["ag_cancelamento"]["oferta_envio"]["motivo"] == "atendimento_so_leitura"
    await _nada_saiu(db, c, chat)

    com = await make_user(
        permissions={"atendimento": {"view": True, "edit": True}, "margem": {"edit": True}}
    )
    auth_as(com)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 200, r.text
    assert r.json()["enviada"] is True
    assert chat.textos == [_texto_4b()]


# ─────────────── as travas do envio (nada sai) ───────────────


async def test_envio_desligado_recusa_sem_enviar(db, client, auth_as, make_user, monkeypatch, chat):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409, r.text
    corpo = r.json()["detail"]
    assert corpo["code"] == "envio_desligado"
    assert "ATENDIMENTO_ENVIO_ATIVO" in corpo["detail"]
    await _nada_saiu(db, c, chat)


async def test_loja_em_observar_recusa_sem_enviar(
    db, client, auth_as, make_user, monkeypatch, envio, chat
):
    c = await _cenario(db, make_user, monkeypatch, modo="observar")
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409, r.text
    corpo = r.json()["detail"]
    assert corpo["code"] == "canal_nao_envia"
    assert "observar" in corpo["detail"]
    await _nada_saiu(db, c, chat)


async def test_texto_reprovado_pelo_validador_422(
    db, client, auth_as, make_user, monkeypatch, envio, chat
):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo, "texto": "chama no whatsapp"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "texto_invalido"
    assert r.json()["detail"]["detail"] == ["Contato fora da loja (WhatsApp)."]
    await _nada_saiu(db, c, chat)


# ─────────────── as travas da oferta (nada sai) ───────────────


@pytest.mark.parametrize(
    ("sku_novo", "trecho"),
    [
        ("xx999.sp", "parecido válido"),
        # O parecido de fora: sem estoque no DaVinci.
        ("dg055.sp", "sem estoque no DaVinci"),
    ],
)
async def test_sugestao_invalida_recusa(
    db, client, auth_as, make_user, monkeypatch, envio, chat, sku_novo, trecho
):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": sku_novo})
    assert r.status_code == 409, r.text
    corpo = r.json()["detail"]
    assert corpo["code"] == "sugestao_invalida"
    assert trecho in corpo["detail"]
    await _nada_saiu(db, c, chat)


async def test_nivel_0_nao_tem_oferta(db, make_user, monkeypatch, envio, chat):
    """O mesmo produto de outro lote não pede aceite: nada a oferecer (é Trocar)."""
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    with pytest.raises(troca_oferta.OfertaRecusada) as e:
        await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert e.value.code == "sugestao_invalida"
    assert "Trocar" in e.value.detail
    await _nada_saiu(db, c, chat)


async def test_troca_desligada_e_fora_do_piloto(
    db, client, auth_as, make_user, monkeypatch, envio, chat
):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    monkeypatch.setattr(envio, "atendimento_troca_pedidos", "999999")
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "pedido_fora_do_piloto"
    monkeypatch.setattr(envio, "atendimento_troca_ativa", False)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "troca_desligada"
    await _nada_saiu(db, c, chat)


async def test_motivo_nao_permite(db, client, auth_as, make_user, monkeypatch, envio, chat):
    """Trava da Margem (não é falta de estoque) e o item escolhido que não falta."""
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo, "sku_antigo": "dg054.sp"})
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "motivo_nao_permite",
        "detail": "O item escolhido não é o que está em falta neste pedido.",
    }
    await db.execute(
        update(BlingOrder).where(BlingOrder.numero == NUMERO).values(status="Pendente")
    )
    await db.commit()
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "motivo_nao_permite"
    await _nada_saiu(db, c, chat)


async def test_dois_itens_em_falta_nao_tem_oferta(db, make_user, monkeypatch, envio, chat):
    c = await _cenario(
        db,
        make_user,
        monkeypatch,
        itens=(("dg053.sp", 1, 501), ("a001.sp", 1, 504)),
        marca=("dg053.sp", "a001.sp"),
    )
    with pytest.raises(troca_oferta.OfertaRecusada) as e:
        await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert e.value.code == "motivo_nao_permite"
    assert "Outro item também está em falta" in e.value.detail
    await _nada_saiu(db, c, chat)


async def test_sem_conversa_recusa(db, client, auth_as, make_user, monkeypatch, envio, chat):
    c = await _cenario(db, make_user, monkeypatch, conversa=False)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_conversa"
    r = await client.post("/api/atendimento/pedidos/999999/troca/oferta", json={"sku_novo": c.novo})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "pedido_nao_encontrado"
    assert chat.textos == []


async def test_troca_aberta_recusa(db, client, auth_as, make_user, monkeypatch, envio, chat):
    c = await _cenario(db, make_user, monkeypatch)
    db.add(
        AtendimentoTroca(
            pedido_bling=NUMERO,
            bling_id=BLING_ID,
            motivo_codigo="sem_estoque",
            sku_antigo=c.antigo,
            sku_novo=c.novo,
            produto_novo_id=502,
            quantidade=1,
            nivel=1,
            estado="incerta",
            passos=[],
            idem_key=uuid4(),
            criado_por_nome="Fulana",
        )
    )
    await db.commit()
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "troca_em_andamento"
    await _nada_saiu(db, c, chat)


# ─────────────── o caminho feliz ───────────────


async def test_caminho_feliz_manda_o_texto_da_4b_e_grava_a_nota(
    db, client, auth_as, make_user, monkeypatch, envio, chat
):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 200, r.text
    corpo = r.json()
    esperado = _texto_4b()
    assert "Laranja" in esperado and "cancelamos" in esperado
    # O texto que foi é o MESMO que a 4b mostra na sugestão.
    assert chat.textos == [esperado]
    assert (corpo["enviada"], corpo["status"], corpo["texto"]) == (True, "enviada", esperado)
    assert (corpo["sku_antigo"], corpo["sku_novo"], corpo["nivel"]) == ("dg053.sp", "dg054.sp", 1)
    assert corpo["conversa_id"] == str(c.conversa.id)

    [m] = await _mensagens(db, c.conversa.id, autor="loja")
    assert str(m.id) == corpo["mensagem_id"]
    assert (m.origem, m.status, m.autor_user_id) == ("davinci_humano", "enviada", c.adm.id)
    assert m.payload["oferta_troca"] == {
        "pedido": NUMERO,
        "sku_antigo": "dg053.sp",
        "sku_novo": "dg054.sp",
        "nivel": 1,
        "editada": False,
    }
    [nota] = await _mensagens(db, c.conversa.id, tipo="nota")
    assert nota.texto == "Oferta de troca enviada (dg053.sp -> dg054.sp) por Fulana Atendimento."
    assert nota.autor_user_id == c.adm.id
    # A troca que vier depois acha esta oferta.
    assert await troca_oferta.oferta_enviada_id(db, c.conversa.id, NUMERO, "DG054.SP") == m.id
    assert await troca_oferta.oferta_enviada_id(db, c.conversa.id, NUMERO, "dg055.sp") is None

    # A outra aba (o bloco montado antes da oferta): a oferta que acabou de sair é
    # fala da loja depois do que ela tinha — `conversa_mudou`, com quem e a hora.
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conversa_mudou"
    assert r.json()["detail"]["detail"].startswith("A loja escreveu às ")
    # O "enviar mesmo assim" ainda esbarra no envio repetido (nada sai de novo).
    r = await client.post(URL, json={"sku_novo": c.novo, "confirmar": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "envio_repetido"
    assert len(chat.textos) == 1


async def test_texto_editado_pela_pessoa(db, make_user, monkeypatch, envio, chat):
    c = await _cenario(db, make_user, monkeypatch)
    out = await troca_oferta.enviar_oferta(
        db,
        c.adm,
        numero_bling=NUMERO,
        sku_novo=c.novo,
        texto="  Oi! Acabou a branca.   Podemos mandar a laranja, pelo mesmo valor?  ",
    )
    assert chat.textos == ["Oi! Acabou a branca. Podemos mandar a laranja, pelo mesmo valor?"]
    assert out["enviada"] is True and out["texto"] == chat.textos[0]
    [m] = await _mensagens(db, c.conversa.id, autor="loja")
    assert m.payload["oferta_troca"]["editada"] is True
    # Texto em branco = o da 4b (a tela já mostrava a 1ª oferta: a vista é ela).
    chat.textos.clear()
    c2 = await troca_oferta.enviar_oferta(
        db, c.adm, numero_bling=NUMERO, sku_novo=c.novo, texto="   ", ultima_vista_id=m.id
    )
    assert chat.textos == [_texto_4b()] and c2["texto"] == _texto_4b()


async def test_plataforma_falhou_nao_vira_nota(db, make_user, monkeypatch, envio, chat):
    c = await _cenario(db, make_user, monkeypatch)
    chat.resultado = ResultadoEnvio(ok=False, erro="shopee_recusou")
    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert (out["enviada"], out["status"], out["erro"]) == (False, "falhou", "shopee_recusou")
    assert await _mensagens(db, c.conversa.id, tipo="nota") == []
    assert await troca_oferta.oferta_enviada_id(db, c.conversa.id, NUMERO, c.novo) is None

    # Ambíguo (pode ter saído): `revisar`, e a nota manda conferir.
    chat.resultado = ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    out = await troca_oferta.enviar_oferta(
        db, c.adm, numero_bling=NUMERO, sku_novo=c.novo, texto="Podemos mandar a laranja?"
    )
    assert (out["enviada"], out["status"]) == (False, "revisar")
    [nota] = await _mensagens(db, c.conversa.id, tipo="nota")
    assert "confira a conversa" in nota.texto


async def test_conversa_de_outro_pedido_422(
    db, client, auth_as, make_user, monkeypatch, envio, chat
):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo, "conversa_id": str(uuid4())})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "conversa_de_outro_pedido"
    await _nada_saiu(db, c, chat)


async def test_a_troca_guarda_a_oferta(db, make_user, monkeypatch, envio, chat):
    """Crítica M11: a troca feita depois da oferta guarda o id da mensagem dela."""
    c = await _cenario(db, make_user, monkeypatch)
    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    bling = BlingFalso(((c.antigo, 1, 501),))
    bling.produto(c.novo, 502, 9, A17_LARANJA)
    _ligar(monkeypatch, bling, ShopeeFalsa())
    pv = await troca.previa(db, c.adm, numero_bling=NUMERO, sku_antigo=c.antigo, sku_novo=c.novo)
    assert pv["pode"] is True, pv["travas"]
    # A oferta que saiu é da loja, depois da marca: a dica da prévia a vê.
    feita = await troca.executar(
        db,
        c.adm,
        numero_bling=NUMERO,
        sku_antigo=c.antigo,
        sku_novo=c.novo,
        idem_key=uuid4(),
        confirmar=True,
        previa_hash=pv["previa_hash"],
    )
    assert feita.estado == "concluida", (feita.codigo_erro, feita.erro)
    assert feita.oferta_mensagem_id == out["mensagem_id"]


# ─────────────── o botão no painel e na lista ───────────────


async def test_painel_diz_se_a_oferta_pode_sair(db, make_user, monkeypatch, _chaves):
    c = await _cenario(db, make_user, monkeypatch)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    oferta = out["ag_cancelamento"]["oferta_envio"]
    assert (oferta["disponivel"], oferta["motivo"]) == (False, "envio_desligado")
    assert "ATENDIMENTO_ENVIO_ATIVO" in oferta["texto_motivo"]

    monkeypatch.setattr(_chaves, "atendimento_envio_ativo", True)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["oferta_envio"] == {
        "disponivel": True,
        "motivo": None,
        "texto_motivo": None,
        "ultima_mensagem_id": None,  # a conversa ainda sem fala
    }
    # Quem só lê: o botão desligado com o porquê (a rota daria 403).
    leitor = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    out = await painel.painel_da_conversa(db, c.conversa, user=leitor)
    assert out["ag_cancelamento"]["oferta_envio"]["motivo"] == "atendimento_so_leitura"
    # A chave da troca desligada.
    monkeypatch.setattr(_chaves, "atendimento_troca_ativa", False)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["oferta_envio"]["motivo"] == "troca_desligada"


async def test_painel_loja_em_observar(db, make_user, monkeypatch, envio):
    c = await _cenario(db, make_user, monkeypatch, modo="observar")
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    oferta = out["ag_cancelamento"]["oferta_envio"]
    assert (oferta["disponivel"], oferta["motivo"]) == (False, "canal_nao_envia")


async def test_painel_motivo_que_nao_e_falta_de_estoque(db, make_user, monkeypatch, envio):
    c = await _cenario(db, make_user, monkeypatch, status="Pendente")
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["codigo"] == "margem_trava"
    assert out["ag_cancelamento"]["oferta_envio"]["motivo"] == "motivo_nao_permite"


async def test_painel_conferencia_quebrada_nao_derruba(db, make_user, monkeypatch, envio):
    c = await _cenario(db, make_user, monkeypatch)

    async def _quebra(*args, **kwargs):
        raise RuntimeError("fora")

    monkeypatch.setattr(troca_oferta.enviar, "motivo_para_nao_enviar", _quebra)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["oferta_envio"]["motivo"] == "falhou"
    assert out["ag_cancelamento"]["codigo"] == "sem_estoque"


async def test_lista_traz_a_oferta(db, client, auth_as, make_user, monkeypatch, envio):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.get("/api/atendimento/ag-cancelamento")
    assert r.status_code == 200, r.text
    [p] = r.json()["pedidos"]
    assert p["motivo"]["oferta_envio"] == {
        "disponivel": True,
        "motivo": None,
        "texto_motivo": None,
        "ultima_mensagem_id": None,
    }


async def test_lista_sem_conversa(db, client, auth_as, make_user, monkeypatch, envio):
    c = await _cenario(db, make_user, monkeypatch, conversa=False)
    auth_as(c.adm)
    r = await client.get("/api/atendimento/ag-cancelamento")
    assert r.status_code == 200, r.text
    [p] = r.json()["pedidos"]
    assert p["conversa_id"] is None
    assert p["motivo"]["oferta_envio"]["motivo"] == "sem_conversa"


# ─────────────── as correções da revisão (08/10) ───────────────


async def _fala(db, conversa, autor, texto, minutos_atras, *, tipo="texto", status="recebida"):
    m = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=f"m-{uuid4().hex[:10]}",
        autor=autor,
        origem="cliente" if autor == "cliente" else "externo",
        tipo=tipo,
        texto=texto,
        enviada_em=AGORA - timedelta(minutes=minutos_atras),
        status=status,
        anexos=[],
        payload={},
    )
    db.add(m)
    await db.commit()
    return m


async def test_conversa_mudou_conta_a_fala_do_cliente(db, make_user, monkeypatch, envio, chat):
    """O `enviar` só confere a resposta da LOJA depois da vista; a oferta também a do
    CLIENTE — ele pode ter recusado ("não quero outra cor, cancela")."""
    c = await _cenario(db, make_user, monkeypatch)
    vista = await _fala(db, c.conversa, "cliente", "cadê meu pedido?", 60)
    # O painel montou o botão com esta fala como a última.
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["oferta_envio"]["ultima_mensagem_id"] == str(vista.id)
    # Depois disso o cliente escreveu: a oferta não sai por cima.
    await _fala(db, c.conversa, "cliente", "não quero outra cor, cancela", 5)
    with pytest.raises(troca_oferta.OfertaRecusada) as e:
        await troca_oferta.enviar_oferta(
            db, c.adm, numero_bling=NUMERO, sku_novo=c.novo, ultima_vista_id=vista.id
        )
    assert e.value.code == "conversa_mudou"
    assert e.value.detail.startswith("O cliente escreveu às ")
    # Sem a vista (API antiga): a referência é a marca da falta de estoque (3 h).
    with pytest.raises(troca_oferta.OfertaRecusada) as e:
        await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert e.value.code == "conversa_mudou"
    await _nada_saiu(db, c, chat)
    # O "enviar mesmo assim".
    out = await troca_oferta.enviar_oferta(
        db, c.adm, numero_bling=NUMERO, sku_novo=c.novo, ultima_vista_id=vista.id, confirmar=True
    )
    assert out["enviada"] is True and chat.textos == [_texto_4b()]


async def test_conversa_vista_ate_a_ultima_fala_envia(db, make_user, monkeypatch, envio, chat):
    c = await _cenario(db, make_user, monkeypatch)
    await _fala(db, c.conversa, "cliente", "cadê meu pedido?", 60)
    ultima = await _fala(db, c.conversa, "loja", "Olá! Estamos conferindo.", 30)
    # Uma nota interna e um envio da loja que falhou não são fala nova.
    await _fala(db, c.conversa, "equipe", "conferir o estoque", 10, tipo="nota")
    await _fala(db, c.conversa, "loja", "não saiu", 5, status="falhou")
    out = await troca_oferta.enviar_oferta(
        db, c.adm, numero_bling=NUMERO, sku_novo=c.novo, ultima_vista_id=ultima.id
    )
    assert out["enviada"] is True


async def test_travas_do_pedido_que_a_troca_recusaria(
    db, client, auth_as, make_user, monkeypatch, envio, chat
):
    """Oferecer o que a troca recusa com certeza seria prometer à toa: a plataforma (só a
    Shopee na v1) e a NF na fila — na rota e no botão."""
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    db.add(
        NfCommand(
            action="import_avulsa",
            numeros=[NUMERO],
            planilha=b"x",
            nome_arquivo="a.csv",
            status="pending",
            attempts=0,
            source="auto",
        )
    )
    await db.commit()
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "em_fila_nf"
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["oferta_envio"]["motivo"] == "em_fila_nf"
    await db.execute(delete(NfCommand))
    await db.execute(
        update(StoreInfo).where(StoreInfo.bling_store_id == LOJA).values(platform="ml")
    )
    await db.commit()
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plataforma_sem_conferencia"
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    oferta = out["ag_cancelamento"]["oferta_envio"]
    assert (oferta["disponivel"], oferta["motivo"]) == (False, "plataforma_sem_conferencia")
    [p] = (await client.get("/api/atendimento/ag-cancelamento")).json()["pedidos"]
    assert p["motivo"]["oferta_envio"]["motivo"] == "plataforma_sem_conferencia"
    assert p["motivo"]["troca_envio"]["motivo"] == "plataforma_sem_conferencia"
    await _nada_saiu(db, c, chat)


def test_kit_de_lotes_misturados_nao_tem_oferta():
    with pytest.raises(troca_oferta.OfertaRecusada) as e:
        troca_oferta._conferir_kit("dg057.ci+a001.sp", "dg057.sp+a001.sp")
    assert e.value.code == "kit_lotes_misturados"
    troca_oferta._conferir_kit("dg057.ci+a001.ci", "dg057.sp+a001.sp")
