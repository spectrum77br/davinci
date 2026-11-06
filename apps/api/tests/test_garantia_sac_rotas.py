"""Garantia × SAC (06/11/2026, "Painel de Garantia: alterações"): as rotas do
caso do SAC — Confirmar (a garantia vencida avisa antes), Encerrar, o SAC sem
pedido e o vínculo à mão, o Resumo do SAC com o CPF mascarado e os anexos —, a
lista com as colunas novas e a conversa do SAC no histórico de quem é de
equipe."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    BlingOrder,
    Garantia,
    GarantiaConversa,
    GarantiaLog,
    GarantiaSac,
    SituacaoBling,
    UserRole,
)
from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.models.mail import MailAttachment, MailMailbox, MailMessage
from app.models.mail_admin import MailAcesso
from app.models.mail_atendimento import MailMessageMeta
from app.models.pricing import PricingProduct, StoreInfo
from app.models.segment import Segment
from app.security.cipher import encrypt_bytes, encrypt_json
from app.services import garantia as svc
from app.services import garantia_resumo as resumo
from app.services import garantia_sac as sac
from app.services import mail_central
from app.services.atendimento import escopo
from tests.conftest import _make_user
from tests.lojas_da_pessoa import dar_lojas
from tests.test_garantia_sac import _formulario
from tests.test_garantias_router import _loja_padrao

pytestmark = pytest.mark.asyncio

CPF = "52998224725"
CONSULTA = {"garantias": {"view": True}}
ATENDENTE = {"garantias": {"view": True}, "garantias_atendimento": {"view": True, "edit": True}}
VE_CPF = {"garantias": {"view": True}, "garantias_cpf": {"view": True}}


def _hoje() -> date:
    return datetime.now(svc.SAO_PAULO).date()


@pytest.fixture(autouse=True)
def _mapa_novo():
    resumo.limpar_cache()
    yield
    resumo.limpar_cache()


@pytest.fixture
def make_user(db):
    """Quem não é admin vem com a loja "L1" das garantias daqui."""

    async def _f(**kwargs):
        u = await _make_user(db, **kwargs)
        if u.role != UserRole.ADMIN:
            integ, ficha = await _loja_padrao(db)
            await dar_lojas(db, u, integ, escopo.chave(escopo.FICHA, ficha.id))
        return u

    return _f


async def _garantia(
    db,
    numero="600001",
    *,
    loja="L1",
    entrega: date | None = None,
    prazo: int | None = 12,
    nome="Maria Souza (maria123)",
    sku="dg053",
    descricao="Celular Uranyx T8 128GB + Fone",
    situacao="83953",
    conta="loja teste",
):
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=f"MKT{numero}",
            loja=loja,
            data=datetime.now(UTC) - timedelta(days=200),
            total=1000,
            situacao=situacao,
            item_index=0,
            item_codigo=sku,
            item_descricao=descricao,
            item_quantidade=1,
        )
    )
    g = Garantia(
        origem="robo",
        pedido_bling=numero,
        pedido_marketplace=f"MKT{numero}",
        loja=loja,
        plataforma="shopee",
        conta=conta,
        nf_numero="9" + numero[-4:],
        cliente_nome=nome,
        cpf=CPF,
        itens=[{"sku": sku, "descricao": descricao, "quantidade": 1, "uranyx": True}],
        prazo_meses=prazo,
    )
    if entrega is not None:
        svc.aplicar_entrega(
            g, svc.Entrega(datetime.combine(entrega, time(15), svc.SAO_PAULO), "shopee")
        )
    db.add(g)
    await db.commit()
    return g


async def _caso(db, g=None, *, protocolo="US-26-0101", texto=None, recebido=None):
    recebido = recebido or datetime.now(UTC) - timedelta(days=3)
    c = AtendimentoConversa(
        plataforma="site",
        canal="email",
        conta="uranyx",
        externo_id=f"mail-protocolo:{protocolo}",
        dados={"mail": {"protocolo": protocolo, "marca": "uranyx"}},
        ultima_mensagem_em=recebido,
    )
    db.add(c)
    await db.flush()
    texto = texto or _formulario(protocolo=protocolo)
    m = AtendimentoMensagem(
        conversa_id=c.id, autor="cliente", origem="cliente", texto=texto, enviada_em=recebido
    )
    db.add(m)
    await db.flush()
    caso = GarantiaSac(
        conversa_id=c.id,
        mensagem_id=m.id,
        protocolo=protocolo,
        recebido_em=recebido,
        campos=sac.campos_do_formulario(texto),
        garantia_id=g.id if g else None,
        vinculo="pedido" if g else None,
    )
    db.add(caso)
    if g is not None:
        db.add(GarantiaConversa(garantia_id=g.id, conversa_id=c.id, como="pedido"))
    await db.commit()
    return caso


async def _lista(client, **corpo):
    r = await client.post("/api/garantias/lista", json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


async def _logs(db, garantia_id, acao):
    return list(
        (
            await db.execute(
                select(GarantiaLog)
                .where(GarantiaLog.garantia_id == garantia_id, GarantiaLog.acao == acao)
                .order_by(GarantiaLog.id)
            )
        ).scalars()
    )


async def test_confirmar_avisa_a_vencida_e_aciona_a_garantia(client, db, make_user, auth_as):
    hoje = _hoje()
    g = await _garantia(db, entrega=hoje - timedelta(days=130), prazo=3)  # venceu
    caso = await _caso(db, g)
    url = f"/api/garantias/sac/{caso.id}/confirmar"

    auth_as(await make_user(permissions=CONSULTA))
    assert (await client.post(url, json={})).status_code == 403

    atendente = await make_user(permissions=ATENDENTE)
    auth_as(atendente)
    r = await client.post(url, json={})
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "garantia_vencida",
        "fim_garantia": svc.fim_da_garantia(hoje - timedelta(days=130), 3).isoformat(),
    }
    assert (await db.get(GarantiaSac, caso.id)).confirmado_em is None
    r = await client.post(url, json={"ciente_vencida": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["confirmado_por"]["id"] == str(atendente.id)
    assert (out["garantia_id"], out["vinculo"], out["protocolo"]) == (g.id, "pedido", "US-26-0101")
    # Clicar de novo não muda nada (nem grava outro log).
    again = (await client.post(url, json={})).json()
    assert again["confirmado_em"] == out["confirmado_em"]
    [log] = await _logs(db, g.id, "confirmou_atendimento")
    assert (log.user_id, log.detalhe, log.campo) == (atendente.id, "US-26-0101", "garantia_vencida")
    assert log.valor_novo == svc._br(svc.fim_da_garantia(hoje - timedelta(days=130), 3))
    await db.refresh(g)
    assert (g.solicitacao_origem, g.solicitacao_ref) == ("sac", "US-26-0101")
    # Garantia acionada: no filtro, na aba Atendimento e nos cartões.
    h = await _lista(client, mostrar="acionada")
    assert [i["id"] for i in h["itens"]] == [g.id]
    assert h["itens"][0]["sac"]["confirmado_por"]["id"] == str(atendente.id)
    assert (h["indicadores"]["acionadas"], h["indicadores"]["atendimentos_no_mes"]) == (1, 1)
    h = await _lista(client, em_atendimento=True)
    assert [i["id"] for i in h["itens"]] == [g.id]
    assert h["abas"]["celular"]["atendimento"] == 1
    # Corpo fora do contrato: recusa.
    assert (await client.post(url, json={"enviar_email": True})).status_code == 422


async def test_confirmar_produto_vendido_sem_garantia(client, db, make_user, auth_as):
    """Prazo 0 (06/11/2026, "se for avulso não tem garantia"): o Confirmar avisa
    antes (409 produto_sem_garantia) e confirma de novo com a pessoa ciente; o
    log guarda que foi sem garantia."""
    g = await _garantia(db, entrega=_hoje() - timedelta(days=10), prazo=0)
    assert g.fim_garantia is None
    caso = await _caso(db, g)
    url = f"/api/garantias/sac/{caso.id}/confirmar"
    atendente = await make_user(permissions=ATENDENTE)
    auth_as(atendente)
    r = await client.post(url, json={})
    assert (r.status_code, r.json()["detail"]) == (409, {"code": "produto_sem_garantia"})
    assert (await db.get(GarantiaSac, caso.id)).confirmado_em is None
    assert await _logs(db, g.id, "confirmou_atendimento") == []
    r = await client.post(url, json={"ciente_vencida": True})
    assert r.status_code == 200, r.text
    assert r.json()["confirmado_por"]["id"] == str(atendente.id)
    [log] = await _logs(db, g.id, "confirmou_atendimento")
    assert (log.user_id, log.campo, log.valor_novo, log.detalhe) == (
        atendente.id,
        "sem_garantia",
        "0 meses",
        "US-26-0101",
    )
    h = await _lista(client, mostrar="acionada")
    assert [i["id"] for i in h["itens"]] == [g.id]
    assert (h["itens"][0]["status"], h["itens"][0]["status_rotulo"]) == (
        "sem_garantia",
        "Sem garantia",
    )


async def test_confirmar_sem_garantia_e_encerrar(client, db, make_user, auth_as):
    sem = await _caso(db, protocolo="US-26-0102")
    g = await _garantia(db, entrega=_hoje() - timedelta(days=10))
    caso = await _caso(db, g)
    atendente = await make_user(permissions=ATENDENTE)
    auth_as(atendente)
    # O caso sem garantia não existe para quem é de equipe; para o admin, 409.
    r = await client.post(f"/api/garantias/sac/{sem.id}/confirmar", json={})
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "sac_nao_encontrado")
    assert (await client.post("/api/garantias/sac/999999/confirmar", json={})).status_code == 404

    enc = f"/api/garantias/sac/{caso.id}/encerrar"
    r = await client.post(enc)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sac_nao_confirmado")
    assert (
        await client.post(f"/api/garantias/sac/{caso.id}/confirmar", json={})
    ).status_code == 200
    r = await client.post(enc)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["encerrado_motivo"], out["encerrado_por"]["id"]) == ("pessoa", str(atendente.id))
    assert (await client.post(enc)).json()["encerrado_em"] == out["encerrado_em"]
    [log] = await _logs(db, g.id, "encerrou_atendimento")
    assert log.user_id == atendente.id and log.detalhe == "US-26-0101"
    # Sai da aba Atendimento; continua "garantia acionada".
    h = await _lista(client, em_atendimento=True)
    assert h["itens"] == [] and h["abas"]["celular"]["atendimento"] == 0
    assert [i["id"] for i in (await _lista(client, mostrar="acionada"))["itens"]] == [g.id]

    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(f"/api/garantias/sac/{sem.id}/confirmar", json={})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sac_sem_garantia")


async def test_sac_sem_pedido_e_vincular_a_mao(client, db, make_user, auth_as):
    g1 = await _garantia(db, "600011", entrega=_hoje() - timedelta(days=10))
    g2 = await _garantia(db, "600012", entrega=_hoje() - timedelta(days=10))
    caso = await _caso(db, protocolo="US-26-0103")

    # O SAC do site não tem loja: quem é de equipe não vê nem vincula.
    auth_as(await make_user(permissions=ATENDENTE))
    assert (await client.get("/api/garantias/sac/sem-pedido")).json() == {"itens": [], "total": 0}
    assert (await _lista(client))["sac_sem_pedido"] == 0
    r = await client.post(f"/api/garantias/sac/{caso.id}/vincular", json={"garantia_id": g1.id})
    assert r.status_code == 404

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = (await client.get("/api/garantias/sac/sem-pedido")).json()
    assert r["total"] == 1 and r["itens"][0]["nome"] == "Maria da Nota (maria_n)"
    assert (await _lista(client))["sac_sem_pedido"] == 1
    r = await client.post(f"/api/garantias/sac/{caso.id}/vincular", json={"garantia_id": g1.id})
    assert r.status_code == 200, r.text
    assert (r.json()["garantia_id"], r.json()["vinculo"]) == (g1.id, "manual")
    k = await db.get(GarantiaSac, caso.id)
    await db.refresh(k)
    assert k.vinculado_por == admin.id
    ligadas = set(
        (
            await db.execute(
                select(GarantiaConversa.garantia_id).where(
                    GarantiaConversa.conversa_id == caso.conversa_id
                )
            )
        ).scalars()
    )
    assert ligadas == {g1.id}
    [log] = await _logs(db, g1.id, "vinculou_sac")
    assert log.user_id == admin.id and "à mão" in log.detalhe
    assert (await client.get("/api/garantias/sac/sem-pedido")).json()["total"] == 0
    # Trocar antes de confirmar: a conversa muda de garantia.
    r = await client.post(f"/api/garantias/sac/{caso.id}/vincular", json={"garantia_id": g2.id})
    assert r.status_code == 200
    ligadas = set(
        (
            await db.execute(
                select(GarantiaConversa.garantia_id).where(
                    GarantiaConversa.conversa_id == caso.conversa_id
                )
            )
        ).scalars()
    )
    assert ligadas == {g2.id}
    # Confirmado: não troca mais de garantia.
    assert (
        await client.post(f"/api/garantias/sac/{caso.id}/confirmar", json={})
    ).status_code == 200
    r = await client.post(f"/api/garantias/sac/{caso.id}/vincular", json={"garantia_id": g1.id})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sac_ja_confirmado")
    r = await client.post(f"/api/garantias/sac/{caso.id}/vincular", json={"garantia_id": g2.id})
    assert r.status_code == 200


async def _anexo(db, caso, nome, tipo, corpo=b"conteudo", *, conversa_id=None):
    dono = await _make_user(db, role=UserRole.ADMIN)
    caixa = MailMailbox(
        owner_user_id=dono.id,
        label="sac",
        config_enc=encrypt_json({}),
        agent_token_hash=mail_central.token_hash(uuid.uuid4().hex),
    )
    db.add(caixa)
    await db.flush()
    msg = MailMessage(
        mailbox_id=caixa.id,
        source_id=uuid.uuid4().hex,
        direction="in",
        received_at=datetime.now(UTC),
        content_enc=encrypt_json({}),
    )
    db.add(msg)
    await db.flush()
    db.add(
        MailMessageMeta(
            message_id=msg.id,
            mailbox_id=caixa.id,
            estado="gravado",
            conversa_id=conversa_id or caso.conversa_id,
            mensagem_id=caso.mensagem_id if conversa_id is None else None,
        )
    )
    a = MailAttachment(
        message_id=msg.id,
        metadata_enc=encrypt_json({"filename": nome, "content_type": tipo}),
        content_enc=encrypt_bytes(corpo),
        size=len(corpo),
    )
    db.add(a)
    await db.commit()
    return a


async def test_resumo_do_sac_com_cpf_mascarado_e_os_anexos(client, db, make_user, auth_as):
    g = await _garantia(db, entrega=_hoje() - timedelta(days=10))
    caso = await _caso(db, g)
    foto = await _anexo(db, caso, "IMG_0001.jpg", "image/jpeg", b"\xff\xd8foto")
    video = await _anexo(db, caso, "video.mp4", "video/mp4")
    pdf = await _anexo(db, caso, "comprovante.pdf", "application/pdf")
    html = await _anexo(db, caso, "pagina.html", "text/html", b"<script>x</script>")
    outro = await _caso(db, protocolo="US-26-0199")
    de_fora = await _anexo(db, outro, "fora.jpg", "image/jpeg", conversa_id=outro.conversa_id)

    auth_as(await make_user(permissions=CONSULTA))
    r = await client.get(f"/api/garantias/{g.id}/sac")
    assert r.status_code == 200, r.text
    d = r.json()
    assert [s["titulo"] for s in d["secoes"]] == [
        "Atendimento",
        "Comprador",
        "Compra",
        "Produto",
        "Defeito",
    ]
    campos = {c["rotulo"]: c["valor"] for s in d["secoes"] for c in s["campos"]}
    assert campos["CPF"] == "***.982.247-**"
    assert campos["Protocolo"] == "US-26-0101" and campos["Número do pedido"] == "261001ABCD1234"
    assert campos["Modelo"] == "T8" and campos["Valor pago"] is None
    assert campos["Sofreu queda, contato com líquido ou voltagem errada"] == "Não"
    assert set(campos) >= {
        "Data de envio",
        "Nome",
        "E-mail",
        "Telefone/WhatsApp",
        "CEP",
        "Endereço",
        "Local da compra",
        "Loja/vendedor",
        "Data da compra",
        "Data de recebimento",
        "Nota fiscal",
        "Cor",
        "Voltagem",
        "Número de série ou lote",
        "Data do problema",
        "Descrição",
        "Tipo de conexão",
    }
    # Consultar não grava "consultou".
    assert await _logs(db, g.id, "consultou") == []

    auth_as(await make_user(permissions=VE_CPF))
    d = (await client.get(f"/api/garantias/{g.id}/sac")).json()
    campos = {c["rotulo"]: c["valor"] for s in d["secoes"] for c in s["campos"]}
    assert campos["CPF"] == "529.982.247-25"

    auth_as(await make_user(role=UserRole.ADMIN))
    d = (await client.get(f"/api/garantias/{g.id}/sac")).json()
    papeis = {a["nome"]: a["papel"] for a in d["anexos"]}
    assert papeis == {
        "IMG_0001.jpg": "Foto do produto",
        "video.mp4": "Vídeo",
        "comprovante.pdf": None,
        "pagina.html": None,
    }
    url = {a["nome"]: a["url"] for a in d["anexos"]}
    assert url["IMG_0001.jpg"] == f"/api/garantias/sac/{caso.id}/anexos/{foto.id}"
    r = await client.get(url["IMG_0001.jpg"])
    assert r.status_code == 200 and r.content == b"\xff\xd8foto"
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, no-store"
    for a, inline in ((video, True), (pdf, True), (html, False)):
        r = await client.get(f"/api/garantias/sac/{caso.id}/anexos/{a.id}")
        assert r.status_code == 200
        assert r.headers["content-disposition"].startswith("inline" if inline else "attachment")
    assert (await client.get(f"/api/garantias/sac/{caso.id}/anexos/{html.id}")).headers[
        "content-type"
    ] == "application/octet-stream"
    # O anexo de outra conversa não sai por este caso.
    r = await client.get(f"/api/garantias/sac/{caso.id}/anexos/{de_fora.id}")
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "anexo_nao_encontrado")
    # A garantia sem caso: nada.
    g2 = await _garantia(db, "600002", entrega=_hoje() - timedelta(days=10))
    assert (await client.get(f"/api/garantias/{g2.id}/sac")).json() is None


async def test_lista_com_as_colunas_novas(client, db, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    await db.merge(SituacaoBling(id=83953, nome="Entregue"))
    seg = Segment(name="Celular", slug="celular")
    db.add(seg)
    await db.flush()
    db.add(
        PricingProduct(
            user_id=admin.id, sku="dg053", name="Uranyx T8", segment_id=seg.id, garantia_meses=12
        )
    )
    # A conta renomeada (Kia → Fiore): a lista mostra o nome de hoje.
    db.add(
        StoreInfo(user_id=admin.id, platform="shopee", account_name="Fiore", bling_store_id="L9")
    )
    await db.commit()
    entrega = _hoje() - timedelta(days=10)
    na_tabela = await _garantia(db, "600021", loja="L9", entrega=entrega, conta="kia")
    fora = await _garantia(
        db,
        "600022",
        sku="dg999",
        descricao="Celular Antigo X + Fone",
        prazo=None,
        nome="João (joao_99",
        situacao="excluido",
        entrega=entrega,
    )
    recebido = datetime.now(UTC) - timedelta(days=2)
    await _caso(db, na_tabela, recebido=recebido)
    h = await _lista(client)
    por_id = {i["id"]: i for i in h["itens"]}
    a, b = por_id[na_tabela.id], por_id[fora.id]
    assert (a["nome"], a["cliente_nome"]) == ("Maria Souza", "Maria Souza (maria123)")
    assert (a["produto_nome"], a["produto_na_tabela"]) == ("Uranyx T8", True)
    assert (a["garantia_meses"], a["fim_garantia"]) == (
        12,
        svc.fim_da_garantia(entrega, 12).isoformat(),
    )
    assert (a["status"], a["status_rotulo"], a["status_pedido"]) == ("ativa", "Ativa", "Entregue")
    assert (a["plataforma"], a["conta"]) == ("shopee", "Fiore")
    assert a["sac"]["protocolo"] == "US-26-0101" and a["sac"]["confirmado_em"] is None
    assert datetime.fromisoformat(a["sac"]["recebido_em"]) == recebido
    assert (b["nome"], b["produto_nome"], b["produto_na_tabela"]) == (
        "João",
        "Celular Antigo X",
        False,
    )
    assert (b["garantia_meses"], b["fim_garantia"]) == (None, None)
    assert (b["status"], b["status_rotulo"]) == ("sem_prazo", "Prazo não cadastrado")
    assert b["status_pedido"] == "Excluído no Bling" and b["sac"] is None
    assert b["conta"] == "loja teste"  # sem a loja em store_info, o nome gravado
    ind = h["indicadores"]
    assert (ind["ativas"], ind["sem_prazo"], ind["sem_garantia"], ind["total"]) == (1, 1, 0, 2)
    assert [i["id"] for i in (await _lista(client, status="sem_prazo"))["itens"]] == [fora.id]
    assert (await _lista(client, status="sem_garantia"))["itens"] == []
    # O detalhe tem as mesmas colunas.
    d = (await client.get(f"/api/garantias/{na_tabela.id}")).json()
    assert (d["nome"], d["produto_nome"], d["sac"]["protocolo"]) == (
        "Maria Souza",
        "Uranyx T8",
        "US-26-0101",
    )


async def test_lista_com_o_produto_sem_garantia(client, db, make_user, auth_as):
    """Prazo 0 (06/11/2026): o status "Sem garantia" vale antes de aguardando,
    ativa e expirada; o filtro novo acha só elas; "Sem prazo cadastrado"
    continua só o vazio; aguardando e "entregue sem data" não contam o 0."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    hoje = _hoje()
    entregue = await _garantia(db, "600031", entrega=hoje - timedelta(days=10), prazo=0)
    # Sem entrega, com o Bling dizendo entregue: não é "aguardando".
    sem_entrega = await _garantia(db, "600032", prazo=0, situacao="83953")
    ativa = await _garantia(db, "600033", entrega=hoje - timedelta(days=10), prazo=12)
    aguardando = await _garantia(db, "600034", prazo=12, situacao="83953")
    vazio = await _garantia(db, "600035", entrega=hoje - timedelta(days=10), prazo=None)
    h = await _lista(client)
    por_id = {i["id"]: i for i in h["itens"]}
    for g in (entregue, sem_entrega):
        linha = por_id[g.id]
        assert (linha["status"], linha["status_rotulo"]) == ("sem_garantia", "Sem garantia")
        assert (linha["garantia_meses"], linha["fim_garantia"]) == (0, None)
        assert linha["entregue_sem_data"] is False
    assert por_id[ativa.id]["status"] == "ativa"
    assert (por_id[aguardando.id]["status"], por_id[aguardando.id]["entregue_sem_data"]) == (
        "aguardando_entrega",
        True,
    )
    assert por_id[vazio.id]["status"] == "sem_prazo"
    ind = h["indicadores"]
    assert (
        ind["sem_garantia"],
        ind["sem_prazo"],
        ind["aguardando_entrega"],
        ind["entregue_sem_data"],
        ind["ativas"],
        ind["expiradas"],
        ind["total"],
    ) == (2, 1, 1, 1, 1, 0, 5)

    async def ids(status):
        return sorted(i["id"] for i in (await _lista(client, status=status))["itens"])

    assert await ids("sem_garantia") == sorted([entregue.id, sem_entrega.id])
    assert await ids("sem_prazo") == [vazio.id]
    assert await ids("aguardando_entrega") == [aguardando.id]
    assert await ids("entregue_sem_data") == [aguardando.id]
    assert await ids("expirada") == []
    # O detalhe: sem a barra de prazo (sem fim) e com o mesmo status.
    d = (await client.get(f"/api/garantias/{entregue.id}")).json()
    assert (d["status"], d["garantia_meses"], d["fim_garantia"]) == ("sem_garantia", 0, None)


async def test_a_conversa_do_sac_aparece_para_a_equipe(client, db, make_user, auth_as):
    """O SAC do site não tem integração (loja): antes, quem é de equipe não via
    a conversa do protocolo no histórico. Desde 06/11/2026 a conversa do caso
    ligado a ESTA garantia aparece para quem vê a garantia."""
    g = await _garantia(db, entrega=_hoje() - timedelta(days=10))
    caso = await _caso(db, g)
    # Uma conversa de protocolo ligada sem caso (fora do escopo da equipe).
    solta = AtendimentoConversa(
        plataforma="site", canal="email", externo_id="mail-protocolo:US-26-0300"
    )
    db.add(solta)
    await db.flush()
    db.add(GarantiaConversa(garantia_id=g.id, conversa_id=solta.id, como="pedido"))
    await db.commit()
    auth_as(await make_user(permissions=CONSULTA))
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    assert [c["id"] for c in h["conversas"]] == [str(caso.conversa_id)]
    assert h["conversas"][0]["protocolo"] == "US-26-0101"
    assert [m["formulario_sac"] for m in h["mensagens"]] == [True]


# ── Revisão de 06/11/2026 ────────────────────────────────────────────────────


async def test_a_linha_e_o_resumo_mostram_o_caso_confirmado_e_aberto(
    client, db, make_user, auth_as
):
    """O cliente mandou o formulário duas vezes para a mesma venda: o caso novo,
    sem confirmação, tomava o lugar do confirmado e aberto (sumiam o Encerrar
    e a resposta por e-mail). A linha (lista, aba Atendimento, garantia
    acionada) e o Resumo do SAC mostram o MESMO caso da aba: 1º o confirmado e
    aberto mais antigo; 2º o aberto sem confirmação mais antigo; 3º o
    encerrado mais recente."""
    auth_as(await make_user(permissions=ATENDENTE))
    agora = datetime.now(UTC)
    g = await _garantia(db, entrega=_hoje() - timedelta(days=10))
    antigo = await _caso(db, g, protocolo="US-26-0110", recebido=agora - timedelta(days=5))
    novo = await _caso(db, g, protocolo="US-26-0115", recebido=agora - timedelta(days=1))
    url = f"/api/garantias/sac/{antigo.id}/confirmar"
    assert (await client.post(url, json={})).status_code == 200
    for corpo in ({}, {"em_atendimento": True}, {"mostrar": "acionada"}):
        [linha] = (await _lista(client, **corpo))["itens"]
        assert linha["sac"]["protocolo"] == "US-26-0110", corpo
        assert linha["sac"]["confirmado_em"] is not None
    d = (await client.get(f"/api/garantias/{g.id}/sac")).json()
    assert (d["protocolo"], d["conversa_id"]) == ("US-26-0110", str(antigo.conversa_id))
    # Encerrado o confirmado: o novo (aberto, sem confirmação) volta com o Confirmar.
    assert (await client.post(f"/api/garantias/sac/{antigo.id}/encerrar")).status_code == 200
    [linha] = (await _lista(client))["itens"]
    assert (linha["sac"]["protocolo"], linha["sac"]["confirmado_em"]) == ("US-26-0115", None)
    assert (await client.get(f"/api/garantias/{g.id}/sac")).json()["id"] == novo.id
    # Dois abertos sem confirmação: o mais antigo.
    g2 = await _garantia(db, "600002", entrega=_hoje() - timedelta(days=10))
    await _caso(db, g2, protocolo="US-26-0120", recebido=agora - timedelta(days=4))
    await _caso(db, g2, protocolo="US-26-0121", recebido=agora - timedelta(days=2))
    por_id = {i["id"]: i for i in (await _lista(client))["itens"]}
    assert por_id[g2.id]["sac"]["protocolo"] == "US-26-0120"
    # Só encerrados: o mais recente.
    assert (
        await client.post(f"/api/garantias/sac/{novo.id}/confirmar", json={})
    ).status_code == 200
    assert (await client.post(f"/api/garantias/sac/{novo.id}/encerrar")).status_code == 200
    por_id = {i["id"]: i for i in (await _lista(client))["itens"]}
    assert por_id[g.id]["sac"]["protocolo"] == "US-26-0115"


async def test_trocar_o_pedido_mantem_o_caso_confirmado_e_solta_o_outro(
    client, db, make_user, auth_as
):
    """Corrigir a garantia com outro pedido (PUT). O caso do SAC CONFIRMADO fica
    (é a mesma compra, corrigida): a conversa dele continua no histórico e o
    pedido de garantia (SAC) não é zerado. O não confirmado volta para "SAC
    sem pedido" e o robô pode religar pelo formulário."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    g1 = await _garantia(db, "600031", entrega=_hoje() - timedelta(days=10))
    g2 = await _garantia(db, "600032", entrega=_hoje() - timedelta(days=10))
    for numero in ("600041", "600042"):
        db.add(
            BlingOrder(
                numero=numero,
                numeroloja=f"2610{numero}AB",
                loja="L1",
                data=datetime.now(UTC) - timedelta(days=30),
                total=1000,
                situacao="83953",
                item_index=0,
                item_codigo="dg053",
                item_descricao="Celular Uranyx T8 128GB",
                item_quantidade=1,
            )
        )
    await db.commit()
    gid1, gid2 = g1.id, g2.id
    confirmado = await _caso(db, g1, protocolo="US-26-0131")
    solto = await _caso(
        db,
        g2,
        protocolo="US-26-0132",
        texto=_formulario(protocolo="US-26-0132", pedido="2610600042AB"),
    )
    kid1, conv1 = confirmado.id, confirmado.conversa_id
    kid2, conv2 = solto.id, solto.conversa_id
    url = f"/api/garantias/sac/{kid1}/confirmar"
    assert (await client.post(url, json={})).status_code == 200

    r = await client.put(f"/api/garantias/{gid1}", json={"pedido": "600041"})
    assert r.status_code == 200, r.text
    assert r.json()["pedido_bling"] == "600041"
    db.expire_all()
    await db.refresh(admin)
    k1 = await db.get(GarantiaSac, kid1)
    assert (k1.garantia_id, k1.vinculo, k1.confirmado_em is not None) == (gid1, "pedido", True)
    g1 = await db.get(Garantia, gid1)
    assert (g1.solicitacao_origem, g1.solicitacao_ref) == ("sac", "US-26-0131")
    assert g1.solicitada_em is not None
    h = (await client.get(f"/api/garantias/{gid1}/historico")).json()
    assert [c["id"] for c in h["conversas"]] == [str(conv1)]
    assert [i["id"] for i in (await _lista(client, em_atendimento=True))["itens"]] == [gid1]

    r = await client.put(f"/api/garantias/{gid2}", json={"pedido": "600042"})
    assert r.status_code == 200, r.text
    db.expire_all()
    await db.refresh(admin)
    k2 = await db.get(GarantiaSac, kid2)
    assert (k2.garantia_id, k2.vinculo, k2.vinculado_em) == (None, None, None)
    ligadas = (
        await db.execute(
            select(GarantiaConversa.garantia_id).where(GarantiaConversa.conversa_id == conv2)
        )
    ).all()
    assert ligadas == []
    sem = (await client.get("/api/garantias/sac/sem-pedido")).json()
    assert [i["protocolo"] for i in sem["itens"]] == ["US-26-0132"]
    # O robô religa pelo formulário (o nº do pedido novo, da plataforma).
    await sac.sincronizar_sac(db)
    await db.commit()
    db.expire_all()
    await db.refresh(admin)
    k2 = await db.get(GarantiaSac, kid2)
    assert (k2.garantia_id, k2.vinculo) == (gid2, "pedido")


async def test_resumo_do_sac_segue_a_trava_do_email(client, db, make_user, auth_as):
    """Quem tem a caixa do SAC bloqueada (`MailAcesso`) não lê o formulário pelo
    Resumo do SAC — a mesma trava do /historico: só o protocolo, as datas e o
    estado; sem Comprador/Compra/Produto/Defeito e sem os anexos."""
    g = await _garantia(db, entrega=_hoje() - timedelta(days=10))
    caso = await _caso(db, g)
    conv = await db.get(AtendimentoConversa, caso.conversa_id)
    conv.dados = {**conv.dados, "fonte": "tuta"}
    await db.commit()
    foto = await _anexo(db, caso, "IMG_0001.jpg", "image/jpeg")
    caixa = (await db.get(MailMessage, foto.message_id)).mailbox_id
    bloqueada = await make_user(permissions=CONSULTA)
    db.add(MailAcesso(mailbox_id=caixa, user_id=bloqueada.id, modo="bloqueado"))
    await db.commit()

    auth_as(bloqueada)
    r = await client.get(f"/api/garantias/{g.id}/sac")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["id"], d["protocolo"], d["anexos"]) == (caso.id, "US-26-0101", [])
    assert [s["titulo"] for s in d["secoes"]] == ["Atendimento"]
    assert {c["rotulo"] for c in d["secoes"][0]["campos"]} == {"Protocolo", "Data de envio"}
    assert "maria@example.com" not in r.text and "Rua das Flores" not in r.text
    assert "982.247" not in r.text
    # Nem o arquivo pelo link direto.
    r = await client.get(f"/api/garantias/sac/{caso.id}/anexos/{foto.id}")
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "anexo_nao_encontrado")
    # O /historico fecha igual.
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    assert (h["conversas"], h["restrito"]) == ([], True)

    auth_as(await make_user(permissions=CONSULTA))
    d = (await client.get(f"/api/garantias/{g.id}/sac")).json()
    assert len(d["secoes"]) == 5 and [a["nome"] for a in d["anexos"]] == ["IMG_0001.jpg"]
    assert (await client.get(d["anexos"][0]["url"])).status_code == 200
