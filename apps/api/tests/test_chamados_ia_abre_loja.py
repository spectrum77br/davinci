"""IA de Chamado abre na TELA da Shopee/TikTok (17/11/2026).

Vinicius: "ele joga para o painel de chamado, lá no painel de chamado a IA de
Chamado vai analisar se abriu ou não, caso não ele reabre como precisar". O que a
API não aceita mais (só reembolso já pago ou em análise pela plataforma) vira
abertura `pendente` no canal robô — e ninguém pegava essa fila (298295 esperou 19
dias; a Fraude automática empilhou 307228, 307238 e 308618).

O que é garantido aqui:
- a fila tem só abertura Shopee/TikTok sem nº, que ninguém tratou depois dela
  (análise, instrução ou fala da plataforma posterior = já anda por outro caminho);
- pegar marca `enviando` (a mesma abertura não é pega duas vezes);
- abriu: protocolo vira o nº (sem ir pra leitura do Portal, salvo `portal`), a
  conversa vira falas e fica a análise "Feito na tela … → aguardar a plataforma";
- não abriu: volta pra fila; "humano:" e captcha vão pra pessoa.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoCerebro, ChamadoMensagem, DevolucaoRastreio
from app.services import chamados as svc

pytestmark = pytest.mark.asyncio

_IA = "tok-ia-de-chamado-loja"  # noqa: S105
IA = {"X-Agent-Token": _IA}


@pytest.fixture(autouse=True)
async def ia(db):
    row = ChamadoCerebro(
        nome="IA de Chamado", token_hash=hashlib.sha256(_IA.encode()).hexdigest(), ligada=True
    )
    db.add(row)
    await db.commit()
    return row


async def _abertura(
    db,
    *,
    plataforma: str = "shopee",
    pedido: str = "307228",
    conta: str = "Shopee Vortan",
    origem: str = "devolucao",
    tipo_auto: str | None = "REFUND",
    canal: str = "robo",
) -> ChamadoMensagem:
    """Como o 307228 em 16/11: Shopee em análise do só reembolso → fila da tela."""
    if await db.get(DevolucaoRastreio, pedido) is None:
        db.add(DevolucaoRastreio(pedido_bling=pedido, devolucao_tipo_auto=tipo_auto))
    ch = Chamado(
        id=uuid4(),
        pedido_bling=pedido,
        pedido_marketplace="261104C9E5KFYR",
        plataforma=plataforma,
        conta=conta,
        origem=origem,
        canal=canal,
    )
    db.add(ch)
    await db.flush()
    m = svc.nova_mensagem(
        ch,
        texto="O comprador pediu reembolso SEM devolução do produto (caixa vazia).",
        tipo="abertura",
        direcao="enviada",
        autor_nome="sistema",
        status="pendente",
    )
    m.canal = "robo"
    # foi pro robô na hora em que nasceu (o `_encaminhar_robo` mexe no updated_at)
    m.created_at = m.updated_at = datetime(2026, 11, 16, 20, 37, tzinfo=UTC)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _fila(client) -> list[dict]:
    r = await client.post("/api/chamados/agent/abrir-loja/fila", headers=IA, json={})
    assert r.status_code == 200, r.text
    return r.json()["tarefas"]


async def _pegar(client, m) -> None:
    r = await client.post(
        "/api/chamados/agent/abrir-loja/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    assert r.status_code == 200, r.text


async def _depois(db, m, **kw) -> None:
    ch = await db.get(Chamado, m.chamado_id)
    x = svc.nova_mensagem(ch, autor_nome="alguém", status="registrada", **kw)
    x.created_at = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    db.add(x)
    await db.commit()


async def test_fila_tem_shopee_e_tiktok_e_nao_ml(client, db):
    s = await _abertura(db)
    t = await _abertura(db, plataforma="tiktok", pedido="307238", conta="TikTok Poofy")
    await _abertura(db, plataforma="Mercado Livre", pedido="298394", conta="forpaper")
    ids = {x["mensagem_id"] for x in await _fila(client)}
    assert ids == {str(s.id), str(t.id)}
    (x,) = [x for x in await _fila(client) if x["mensagem_id"] == str(s.id)]
    assert x["tipo"] == "abrir" and x["pedido_marketplace"] == "261104C9E5KFYR"
    await db.refresh(s)
    assert s.status == "pendente"  # olhar não marca


@pytest.mark.parametrize(
    "kw",
    [
        {"texto": "Análise da IA de Chamado [x]: Feito na tela … → aguardar a plataforma",
         "tipo": "analise", "direcao": "sistema"},
        {"texto": "revisar", "tipo": "instrucao", "direcao": "sistema"},
        {"texto": "A Shopee respondeu", "tipo": "resposta", "direcao": "recebida"},
    ],
)
async def test_quem_ja_foi_tratado_depois_nao_entra(client, db, kw):
    """298295: a IA tratou pela instrução do Cairo em 04/11 e a abertura de 29/10
    ficou `pendente` — abrir de novo duplicaria o chamado na Shopee."""
    m = await _abertura(db, pedido="298295", conta="Shopee KFA")
    await _depois(db, m, **kw)
    assert await _fila(client) == []
    r = await client.post(
        "/api/chamados/agent/abrir-loja/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "abertura_fora_da_fila"


async def test_pegar_marca_e_ninguem_pega_de_novo(client, db):
    m = await _abertura(db)
    await _pegar(client, m)
    await db.refresh(m)
    assert m.status == "enviando"
    assert await _fila(client) == []
    r = await client.post(
        "/api/chamados/agent/abrir-loja/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    assert r.status_code == 409


async def test_abriu_grava_protocolo_conversa_e_analise(client, db):
    m = await _abertura(db)
    await _pegar(client, m)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={
            "mensagem_id": str(m.id),
            "ok": True,
            "protocolo": "2121234567890123456",
            "resumo": "atendente abriu a revisão do só reembolso com o vídeo",
            "conversa": [
                {"quem": "nos", "texto": "O comprador alega caixa vazia…",
                 "quando": "2026-11-17T11:02"},
                {"quem": "plataforma", "texto": "Vou encaminhar para análise.",
                 "quando": "2026-11-17T11:05", "autor": "Ana · atendente Shopee"},
            ],
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "enviada", r.text
    ch = await db.get(Chamado, m.chamado_id)
    await db.refresh(ch)
    assert ch.chamado == "2121234567890123456"
    assert not ch.chamado_de_tela  # chat de atendente não abre no Portal (296423)
    msgs = (
        await db.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == ch.id)
            .order_by(ChamadoMensagem.created_at)
        )
    ).scalars().all()
    falas = [x for x in msgs if x.tipo != "sistema" and x.id != m.id]
    assert [x.tipo for x in falas][-1] == "analise"  # a análise é a mais nova
    analise = falas[-1]
    assert analise.texto.startswith("Análise da IA de Chamado [abertura_na_tela]: Feito na tela")
    assert analise.texto.endswith(svc.ACAO_ESPERAR_TXT)  # base do lembrete de 2 dias
    assert any(x.direcao == "recebida" and "encaminhar" in x.texto for x in falas)
    assert await _fila(client) == []


async def test_protocolo_do_portal_vai_pra_leitura(client, db):
    m = await _abertura(db)
    await _pegar(client, m)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": True, "protocolo": "2102457029504077868",
              "resumo": "solicitação aberta no Portal", "portal": True},
    )
    assert r.status_code == 200, r.text
    ch = await db.get(Chamado, m.chamado_id)
    await db.refresh(ch)
    assert ch.chamado_de_tela is True


async def test_abriu_sem_protocolo_e_recusado(client, db):
    m = await _abertura(db)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": True, "resumo": "abri"},
    )
    assert r.status_code == 422


async def test_nao_abriu_volta_pra_fila(client, db):
    m = await _abertura(db, plataforma="tiktok", pedido="307238", conta="TikTok Poofy")
    await _pegar(client, m)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": False, "erro": "a página do caso não carregou"},
    )
    assert r.status_code == 200 and r.json()["status"] == "pendente", r.text
    assert [t["mensagem_id"] for t in await _fila(client)] == [str(m.id)]


async def test_humano_vai_pra_pessoa_e_conversa_vira_nota(client, db):
    m = await _abertura(db)
    await _pegar(client, m)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={
            "mensagem_id": str(m.id),
            "ok": False,
            "erro": "humano: a Shopee já decidiu a favor da loja",
            "conversa": [{"quem": "plataforma", "texto": "Caso encerrado a seu favor.",
                          "quando": "2026-11-17T11:05", "autor": "Ana · atendente Shopee"}],
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "falhou", r.text
    assert await _fila(client) == []
    recebidas = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == m.chamado_id, ChamadoMensagem.direcao == "recebida"
            )
        )
    ).scalars().all()
    assert recebidas == []  # nota, não fala: não tira o chamado de Análise Humano


async def test_parado_no_captcha_avisa(client, db, monkeypatch):
    from app.services import chamados_ia_aviso

    avisos = []

    async def _avisar(session, ch, parado, resumo):
        avisos.append((ch.pedido_bling, parado))
        return "avisado"

    monkeypatch.setattr(chamados_ia_aviso, "avisar", _avisar)
    m = await _abertura(db)
    await _pegar(client, m)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": False, "parado": "captcha"},
    )
    assert r.status_code == 200 and r.json()["status"] == "falhou", r.text
    assert avisos == [("307228", "captcha")]


async def test_resultado_so_de_shopee_ou_tiktok(client, db):
    m = await _abertura(db, plataforma="Mercado Livre", pedido="298394", conta="forpaper")
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": True, "protocolo": "485999001", "resumo": "x"},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "abertura_fora_da_ia"


async def test_sem_cadastro_nao_entra(client, db):
    await _abertura(db)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/fila", headers={"X-Agent-Token": "outro"}, json={}
    )
    assert r.status_code in (401, 403)


async def test_rascunho_sem_devolucao_nao_diz_recebemos():
    """308618 (print do Vinicius): o rascunho começava com "Recebemos a devolução"
    num caso em que nada voltou."""
    from app.models import Devolution
    from app.services import chamados_devolucao as cd

    dev = Devolution(
        pedido_bling="308618", pedido_marketplace="2611123TYHM4PU", conta="Shopee Vortan",
        sku="dg053.sp", produtos="Uranyx A17", condicao_produto="Não devolvido",
        motivo_devolucao="Golpe", quantidade=1,
    )
    txt = cd.texto_padrao(dev, "SRF4", so_reembolso=True)
    assert txt.startswith("O comprador pediu reembolso sem devolver o produto, alegando")
    # golpe COM pacote (voltou outra coisa na caixa): continua o texto da devolução
    assert cd.texto_padrao(dev, "SRF4").startswith("Recebemos a devolução e o produto devolvido")


@pytest.mark.parametrize(
    "kw",
    [
        {"origem": "logistica"},  # regra da aba Status / pedido parado: outro texto
        {"tipo_auto": "RETURN_AND_REFUND"},  # "não recebi o pacote de volta": com pacote
        {"tipo_auto": None},
    ],
)
async def test_so_devolucao_de_so_reembolso_entra(client, db, kw):
    await _abertura(db, **kw)
    assert await _fila(client) == []


async def test_tiktok_shop_entra(client, db):
    m = await _abertura(db, plataforma="TikTok Shop", pedido="307238", conta="TikTok Poofy")
    assert [t["mensagem_id"] for t in await _fila(client)] == [str(m.id)]


async def test_analise_de_antes_de_ir_pro_robo_nao_tira_da_fila(client, db):
    """A IA decidiu "esperar" num bloqueio da API; dias depois a plataforma pagou e a
    abertura foi pro robô: a análise antiga não é "alguém tratou depois"."""
    m = await _abertura(db, plataforma="tiktok", pedido="307238", conta="TikTok Poofy")
    ch = await db.get(Chamado, m.chamado_id)
    velha = svc.nova_mensagem(ch, texto="… → aguardar a plataforma", tipo="analise",
                              direcao="sistema", autor_nome="IA de Chamado", status="registrada")
    velha.created_at = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
    db.add(velha)
    m.updated_at = datetime(2026, 11, 17, 11, 28, tzinfo=UTC)  # foi pro robô
    await db.commit()
    assert [t["mensagem_id"] for t in await _fila(client)] == [str(m.id)]


async def test_outro_chamado_do_pedido_com_numero_segura(client, db):
    """Troca de motivo: o chamado antigo já foi aberto na tela, nasce outro."""
    antigo = await _abertura(db)
    ch = await db.get(Chamado, antigo.chamado_id)
    ch.chamado = "2121234567890123456"
    antigo.status = "enviada"
    await db.commit()
    await _abertura(db)
    assert await _fila(client) == []


async def test_pegar_deixa_marca_no_historico(client, db):
    m = await _abertura(db)
    await _pegar(client, m)
    notas = (
        await db.execute(
            select(ChamadoMensagem.texto).where(
                ChamadoMensagem.chamado_id == m.chamado_id, ChamadoMensagem.tipo == "sistema"
            )
        )
    ).scalars().all()
    assert any("pegou pra abrir na tela" in t for t in notas), notas


async def test_abriu_tira_da_varredura_por_api(client, db):
    """Chamado que o `chamados_pendencias` deixou em canal api: com o nº do chat
    gravado, a varredura por API leria um protocolo que ela não conhece."""
    m = await _abertura(db, canal="api")
    await _pegar(client, m)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": True, "protocolo": "2121234567890123456",
              "resumo": "atendente abriu a revisão"},
    )
    assert r.status_code == 200, r.text
    ch = await db.get(Chamado, m.chamado_id)
    await db.refresh(ch)
    assert ch.canal == "robo"


async def test_abriu_com_o_chamado_ja_encerrado_nao_deixa_aguardar(client, db):
    m = await _abertura(db)
    await _pegar(client, m)
    ch = await db.get(Chamado, m.chamado_id)
    ch.status_plataforma = svc.STATUS_ENCERRADO
    await db.commit()
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": True, "protocolo": "2121234567890123456",
              "resumo": "atendente abriu a revisão",
              "conversa": [{"quem": "plataforma", "texto": "Vou analisar.",
                            "quando": "2026-11-17T08:05"}]},
    )
    assert r.status_code == 200, r.text
    tipos = (
        await db.execute(
            select(ChamadoMensagem.tipo, ChamadoMensagem.direcao, ChamadoMensagem.texto).where(
                ChamadoMensagem.chamado_id == m.chamado_id
            )
        )
    ).all()
    assert not any(t == "analise" for t, _d, _x in tipos)
    assert not any(d == "recebida" for _t, d, _x in tipos)
    assert any("depois de o chamado ficar Encerrado" in x for _t, _d, x in tipos)


async def test_humano_com_maiuscula_tambem_vai_pra_pessoa(client, db):
    m = await _abertura(db)
    await _pegar(client, m)
    r = await client.post(
        "/api/chamados/agent/abrir-loja/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": False, "erro": "Humano: a tela não mostra reembolso"},
    )
    assert r.status_code == 200 and r.json()["status"] == "falhou", r.text
