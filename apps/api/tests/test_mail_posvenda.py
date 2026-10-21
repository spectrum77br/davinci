# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""Pós-venda › Chamados × e-mail (21/10/2026, decisão 3 do Eduardo).

A pasta CRIADA com a categoria "Chamado (Pós-venda)" liga o e-mail ao chamado que
JÁ existe para o pedido (ou para o caso). Aqui a pasta de teste "problema ml" é
categorizada como chamado (em produção nenhuma pasta nasce assim).

SÓ ACRESCENTA (regra do dono, 21/10): o que não pode falhar —
  • nenhuma tabela dos chamados muda: nem chamado novo (um chamado aberto tira o
    pedido do automático do /atendimento), nem linha no histórico, nem status;
    o vínculo é só `mail_message_meta.chamado_id`;
  • pedido sem chamado → o e-mail fica guardado (`chamado_sem_chamado`); sem pedido
    nem caso → `chamado_sem_pedido`; uma pessoa liga à mão a um chamado que existe;
  • o texto do e-mail sai na hora, só para quem lê aquele e-mail no /atendimento
    (loja no escopo da equipe); os outros veem só que ele existe;
  • o chamado de OUTRA loja/plataforma nunca recebe o e-mail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    AtendimentoConversa,
    BlingOrder,
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    ChamadoPedido,
    Company,
    MailAttachment,
    MailMessage,
    Store,
)
from app.models.enums import Marketplace
from app.models.mail_atendimento import MailFolder, MailMessageMeta
from app.services import chamados as svc_chamados
from app.services.mail_atendimento import codigos, ponte, posvenda, regras, rotear, texto
from tests.test_mail_ponte import AGORA, _config, cena, entrar, meta_de, rodar

X = "2000012345678901"  # pedido ML (16 dígitos) da loja Barbosa
Y = "2000012345678902"
NUMERO_BLING = "290001"
CASO = "478705311"
ASSUNTO = "Reclamação aberta no pedido 2000012345678901"
CORPO = "Meu nome é Maria Souza, o produto chegou quebrado. maria.souza@gmail.com"
HOJE = datetime.now(svc_chamados.SAO_PAULO).date()


# ── o mundo ────────────────────────────────────────────────────────────────


async def _pasta_chamado(db, cena, nome: str = "problema ml") -> MailFolder:
    pasta = MailFolder(
        mailbox_id=cena.geral.id,
        chave=nome,
        nome=nome,
        tipo_tuta=regras.tipo_pelo_nome(nome),
        plataforma="ml",
        finalidade="problema",
        ler="corpo",
        revisada=True,
        categoria="chamado",
        categoria_em=AGORA,
    )
    db.add(pasta)
    await db.commit()
    return pasta


async def _pedido_no_bling(db, cena, numero: str = X, numero_bling: str = NUMERO_BLING) -> None:
    cena.loja_ml.bling_store_id = "55"
    db.add(
        BlingOrder(
            id=uuid4(),
            numero=numero_bling,
            numeroloja=numero,
            bling_id=int(numero_bling),
            loja="55",
            situacao="6",
            data=datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
            item_index=0,
            item_codigo="uaf001",
            item_descricao="airfryer UAF001",
        )
    )
    await db.commit()


def _rota(cena) -> rotear.Rota:
    return rotear.Rota(
        alias="21max@tuta.com",
        store_info_id=cena.loja_ml.id,
        integration_id=cena.ml.id,
        plataforma="ml",
        loja_nome="Barbosa",
    )


async def _meta(db, cena, pasta: MailFolder, *, assunto=ASSUNTO, corpo=CORPO, **kw):
    """O e-mail na Central + a meta como a ponte deixa antes de chamar o posvenda."""
    mid = await entrar(
        db,
        cena.geral,
        pasta=pasta.nome,
        de="maria.souza@gmail.com",
        de_nome="Maria Souza",
        assunto=assunto,
        corpo=corpo,
        **kw,
    )
    meta = MailMessageMeta(
        message_id=mid,
        mailbox_id=cena.geral.id,
        folder_id=pasta.id,
        estado="novo",
        plataforma="ml",
        finalidade="problema",
        integration_id=cena.ml.id,
        store_info_id=cena.loja_ml.id,
        alias_recebido="21max@tuta.com",
        remetente_tipo="comprador",
    )
    db.add(meta)
    await db.commit()
    return mid, meta


async def _ligar(db, cena, pasta, meta, *, pedido=X, caso_id=None, citados=None):
    message = await db.get(MailMessage, meta.message_id)
    email = ponte.decifrar(message)
    protegido = codigos.proteger(email.assunto, texto.texto_novo(email.texto))
    r = await posvenda.ligar_ao_chamado(
        db,
        email,
        pasta,
        meta=meta,
        rota=_rota(cena),
        protegido=protegido,
        pedido=pedido,
        pedidos_citados=citados if citados is not None else ([pedido] if pedido else []),
        caso_id=caso_id,
        agora=datetime.now(UTC),
    )
    await db.commit()
    return r


async def _chamado(db, **campos) -> Chamado:
    base = {
        "data": HOJE - timedelta(days=3),
        "pedido_bling": NUMERO_BLING,
        "pedido_marketplace": X,
        "plataforma": "ml",
        "conta": "Barbosa",
        "origem": "devolucao",
        "canal": "manual",
    }
    ch = Chamado(**{**base, **campos})
    db.add(ch)
    await db.commit()
    return ch


async def _contar(db, modelo) -> int:
    return int(await db.scalar(select(func.count()).select_from(modelo)) or 0)


async def _retrato_dos_chamados(db) -> list[tuple]:
    """Tudo o que os chamados guardam — para provar que o e-mail não mudou nada."""
    chamados = [
        (c.id, c.updated_at, c.resolvido, c.status_plataforma, c.canal, c.origem)
        for c in (
            await db.execute(
                select(Chamado).order_by(Chamado.id).execution_options(populate_existing=True)
            )
        ).scalars()
    ]
    return [
        *chamados,
        await _contar(db, ChamadoMensagem),
        await _contar(db, ChamadoAnexo),
        await _contar(db, ChamadoPedido),
    ]


# ── ligar_ao_chamado (o caminho automático, só leitura) ────────────────────


async def test_pedido_com_chamado_liga_sem_mudar_nada_nos_chamados(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    antigo = await _chamado(db, resolvido=True, resolvido_at=AGORA - timedelta(days=2))
    aberto = await _chamado(db, pedido_marketplace=None)  # só o nº do Bling da loja
    outro_pedido = await _chamado(db, pedido_bling="999999", pedido_marketplace=None)
    _mid, meta = await _meta(db, cena, pasta)
    antes = await _retrato_dos_chamados(db)

    r = await _ligar(db, cena, pasta, meta)

    assert r.chamado_id == aberto.id and r.vinculado_por == posvenda.VINCULADO_PEDIDO
    assert r.criado is False and r.chamado_mensagem_id is None and r.motivo is None
    assert r.pedido == X
    assert await _retrato_dos_chamados(db) == antes
    # a ponte é quem grava a meta (contrato §3)
    assert (await meta_de(db, meta.message_id)).estado == "novo"
    assert antigo.id != r.chamado_id != outro_pedido.id


async def test_chamado_em_lote_do_pedido_tambem_vale(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    lote = await _chamado(db, pedido_bling="111111", pedido_marketplace=None, origem="logistica")
    db.add(ChamadoPedido(chamado_id=lote.id, pedido_bling=NUMERO_BLING, motivo="fila"))
    await db.commit()
    _mid, meta = await _meta(db, cena, pasta)
    r = await _ligar(db, cena, pasta, meta)
    assert r.chamado_id == lote.id


async def test_so_concluido_liga_e_nada_reabre(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    fechado = await _chamado(db, resolvido=True, resolvido_at=AGORA - timedelta(days=1))
    _mid, meta = await _meta(db, cena, pasta)
    antes = await _retrato_dos_chamados(db)
    r = await _ligar(db, cena, pasta, meta)
    assert r.chamado_id == fechado.id
    assert await _retrato_dos_chamados(db) == antes


async def test_pedido_sem_chamado_nao_cria_e_guarda_como_sem_chamado(db, cena):
    """Criar chamado tiraria o pedido do automático do /atendimento: não cria."""
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    _mid, meta = await _meta(db, cena, pasta)
    r = await _ligar(db, cena, pasta, meta)
    assert r == posvenda.LigacaoChamado(
        chamado_id=None, pedido=X, motivo=posvenda.MOTIVO_SEM_CHAMADO
    )
    assert await _contar(db, Chamado) == 0 and await _contar(db, ChamadoMensagem) == 0


async def test_chamado_de_outra_conta_ou_plataforma_nunca_recebe(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    await _chamado(db, conta="Oliveira")
    await _chamado(db, plataforma="shopee")
    _mid, meta = await _meta(db, cena, pasta)
    r = await _ligar(db, cena, pasta, meta)
    assert r.chamado_id is None and r.motivo == posvenda.MOTIVO_SEM_CHAMADO


async def test_numero_do_bling_de_outra_loja_nao_liga(db, cena):
    """O nº do Bling vem do espelho DA LOJA: a linha de outra empresa não conta."""
    pasta = await _pasta_chamado(db, cena)
    empresa = Company(razao_social="Outra LTDA", apelido="outra")
    db.add(empresa)
    await db.flush()
    loja_oliveira = Store(
        company_id=empresa.id, marketplace=Marketplace.ML, integration_id=cena.oliveira_ml.id
    )
    db.add(loja_oliveira)
    await db.flush()
    db.add(
        BlingOrder(
            id=uuid4(), numero="777777", numeroloja=X, store_id=loja_oliveira.id, item_index=0
        )
    )
    await db.commit()
    # o chamado da OUTRA loja, só com o nº do Bling dela (sem conta para distinguir)
    await _chamado(db, pedido_bling="777777", pedido_marketplace=None, conta=None)
    _mid, meta = await _meta(db, cena, pasta)
    r = await _ligar(db, cena, pasta, meta)
    assert r.chamado_id is None and r.motivo == posvenda.MOTIVO_SEM_CHAMADO


async def test_sem_pedido_liga_pelo_caso_da_mesma_plataforma(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _chamado(db, chamado=CASO, plataforma="shopee", pedido_marketplace=None)
    do_caso = await _chamado(db, chamado=CASO, pedido_marketplace=None, origem="logistica")
    _mid, meta = await _meta(db, cena, pasta, assunto=f"Case: {CASO}")
    r = await _ligar(db, cena, pasta, meta, pedido=None, caso_id=CASO)
    assert r.chamado_id == do_caso.id and r.vinculado_por == posvenda.VINCULADO_CASO


async def test_sem_pedido_e_sem_caso_fica_sem_pedido(db, cena):
    pasta = await _pasta_chamado(db, cena)
    _mid, meta = await _meta(db, cena, pasta, assunto="Dúvida", corpo="Oi")
    r = await _ligar(db, cena, pasta, meta, pedido=None, caso_id=None, citados=[Y])
    assert r == posvenda.LigacaoChamado(chamado_id=None, motivo=posvenda.MOTIVO_SEM_PEDIDO)
    # caso sem chamado e sem pedido: também "sem pedido"
    r2 = await _ligar(db, cena, pasta, meta, pedido=None, caso_id="123456789")
    assert r2.chamado_id is None and r2.motivo == posvenda.MOTIVO_SEM_PEDIDO
    assert await _contar(db, Chamado) == 0


async def test_meta_ja_ligada_devolve_o_mesmo(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    ch = await _chamado(db)
    _mid, meta = await _meta(db, cena, pasta)
    r1 = await _ligar(db, cena, pasta, meta)
    meta.chamado_id, meta.vinculado_por, meta.pedido_marketplace = (
        r1.chamado_id,
        r1.vinculado_por,
        X,
    )
    await db.commit()
    await _chamado(db)  # um chamado mais novo do mesmo pedido não rouba o e-mail
    r2 = await _ligar(db, cena, pasta, meta)
    assert r1.chamado_id == r2.chamado_id == ch.id


# ── pela ponte, com a pasta de teste categorizada como chamado ─────────────


async def test_ponte_liga_ao_chamado_existente_sem_conversa_e_sem_mexer_no_chamado(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    ch = await _chamado(db)
    antes = await _retrato_dos_chamados(db)
    primeiro = await entrar(db, cena.geral, pasta=pasta.nome, assunto=ASSUNTO, corpo=CORPO)
    segundo = await entrar(
        db, cena.geral, pasta=pasta.nome, assunto=f"Re: pedido {X}", corpo="Alguma novidade?"
    )
    await rodar(db)

    for mid in (primeiro, segundo):
        m = await meta_de(db, mid)
        assert (m.estado, m.chamado_id, m.vinculado_por) == (
            "gravado",
            ch.id,
            posvenda.VINCULADO_PEDIDO,
        )
        assert m.pedido_marketplace == X and m.conversa_id is None and m.mensagem_id is None
    assert await _contar(db, AtendimentoConversa) == 0
    assert await _retrato_dos_chamados(db) == antes


async def test_ponte_liga_pelo_caso_quando_o_email_nao_cita_pedido(db, cena):
    pasta = await _pasta_chamado(db, cena)
    do_caso = await _chamado(db, chamado=CASO, pedido_marketplace=None, origem="logistica")
    mid = await entrar(
        db,
        cena.geral,
        pasta=pasta.nome,
        de="nao-responder@mercadolivre.com.br",
        assunto=f"Case: {CASO}",
        corpo="Há uma nova mensagem na sua reclamação.",
    )
    await rodar(db)
    meta = await meta_de(db, mid)
    assert (meta.estado, meta.chamado_id) == ("gravado", do_caso.id)
    assert meta.vinculado_por == posvenda.VINCULADO_CASO


async def test_ponte_sem_pedido_guarda_e_nao_cria_nada(db, cena):
    pasta = await _pasta_chamado(db, cena)
    mid = await entrar(db, cena.geral, pasta=pasta.nome, assunto="Dúvida", corpo="Oi")
    await rodar(db)
    meta = await meta_de(db, mid)
    assert (meta.estado, meta.motivo, meta.chamado_id) == ("ignorado", "chamado_sem_pedido", None)
    assert await _contar(db, Chamado) == 0 and await _contar(db, AtendimentoConversa) == 0


async def test_ponte_pedido_sem_chamado_guarda_e_nao_cria_nada(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    mid = await entrar(db, cena.geral, pasta=pasta.nome, assunto=ASSUNTO, corpo=CORPO)
    await rodar(db)
    meta = await meta_de(db, mid)
    assert (meta.estado, meta.chamado_id) == ("ignorado", None)
    assert await _contar(db, Chamado) == 0 and await _contar(db, AtendimentoConversa) == 0


async def test_ponte_grava_o_motivo_chamado_sem_chamado(db, cena):
    pasta = await _pasta_chamado(db, cena)
    await _pedido_no_bling(db, cena)
    mid = await entrar(db, cena.geral, pasta=pasta.nome, assunto=ASSUNTO, corpo=CORPO)
    await rodar(db)
    assert (await meta_de(db, mid)).motivo == posvenda.MOTIVO_SEM_CHAMADO


async def test_ponte_em_pasta_sem_categoria_nao_chega_na_pos_venda(db, cena):
    """Decisão 3: nenhuma pasta nasce "chamado" — sem categoria, nada chega lá."""
    await _pedido_no_bling(db, cena)
    await _chamado(db)
    mid = await entrar(db, cena.geral, pasta="problema ml", assunto=ASSUNTO, corpo=CORPO)
    await rodar(db)
    assert (await meta_de(db, mid)).chamado_id is None


# ── a tela do chamado (bloco novo, só leitura) ────────────────────────────


async def _ligado(db, cena, pasta, **kw):
    await _pedido_no_bling(db, cena)
    ch = await _chamado(db)
    _mid, meta = await _meta(db, cena, pasta, **kw)
    r = await _ligar(db, cena, pasta, meta)
    meta.estado = "gravado"  # o que a ponte faz com o retorno (contrato §4)
    meta.chamado_id = r.chamado_id
    meta.vinculado_por = r.vinculado_por
    meta.pedido_marketplace = r.pedido
    await db.commit()
    return ch, meta


async def test_tela_mostra_o_email_protegido_para_quem_le(db, cena, client, auth_as):
    pasta = await _pasta_chamado(db, cena)
    corpo = f"{CORPO}\nRedefina sua senha: https://conta.exemplo.com/reset?token=abc123"
    ch, meta = await _ligado(
        db, cena, pasta, corpo=corpo, anexos=[("foto.jpg", "image/jpeg", b"jpeg!")]
    )
    auth_as(cena.dono)

    resp = await client.get(f"/api/chamados/{ch.id}/emails")
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    d = resp.json()
    assert d["total"] == 1 and d["ocultos"] == 0
    (item,) = d["itens"]
    assert item["pode_ler"] is True and item["id"] == str(meta.message_id)
    assert item["pasta"] == "problema ml" and item["loja"] == "Barbosa" and item["pedido"] == X
    assert item["assunto"] == ASSUNTO and item["de"] == "maria.souza@gmail.com"
    assert "chegou quebrado" in item["texto"] and "token=abc123" not in item["texto"]
    (anexo,) = item["anexos"]
    assert anexo["filename"] == "foto.jpg"
    baixa = await client.get(f"/api/chamados/emails/anexos/{anexo['id']}")
    assert baixa.status_code == 200 and baixa.content == b"jpeg!"
    assert baixa.headers["x-content-type-options"] == "nosniff"
    assert baixa.headers["content-type"] == "application/octet-stream"
    # as rotas de sempre não mudaram: nada de e-mail no histórico nem na lista
    assert (await client.get(f"/api/chamados/{ch.id}/mensagens")).json() == []
    (linha,) = (await client.get("/api/chamados")).json()["items"]
    assert linha["mensagens_total"] == 0 and "emails_total" not in linha


async def test_quem_nao_le_a_loja_ve_so_que_o_email_existe(db, cena, client, auth_as, make_user):
    pasta = await _pasta_chamado(db, cena)
    ch, meta = await _ligado(db, cena, pasta, anexos=[("foto.jpg", "image/jpeg", b"jpeg!")])
    cena.loja_ml.sales_team = 1
    await db.commit()
    outra_equipe = await make_user(permissions={"chamados": {"view": True}})
    outra_equipe.sales_teams = [2]
    await db.commit()
    auth_as(outra_equipe)

    d = (await client.get(f"/api/chamados/{ch.id}/emails")).json()
    assert d["total"] == 1 and d["ocultos"] == 1
    (item,) = d["itens"]
    assert item["pode_ler"] is False
    assert not ({"texto", "assunto", "de", "de_nome", "anexos"} & item.keys())
    anexo_id = await db.scalar(
        select(MailAttachment.id).where(MailAttachment.message_id == meta.message_id)
    )
    assert (await client.get(f"/api/chamados/emails/anexos/{anexo_id}")).status_code == 404


async def test_sem_permissao_de_chamados_nada(db, cena, client, auth_as, make_user):
    pasta = await _pasta_chamado(db, cena)
    ch, _meta_ = await _ligado(db, cena, pasta)
    auth_as(await make_user(permissions={}))
    assert (await client.get(f"/api/chamados/{ch.id}/emails")).status_code == 403


async def test_anexo_de_email_que_nao_e_de_chamado_nao_sai_por_aqui(db, cena, client, auth_as):
    mid = await entrar(db, cena.geral, anexos=[("x.pdf", "application/pdf", b"%PDF")])
    anexo_id = await db.scalar(select(MailAttachment.id).where(MailAttachment.message_id == mid))
    auth_as(cena.dono)
    assert (await client.get(f"/api/chamados/emails/anexos/{anexo_id}")).status_code == 404


# ── ligar à mão (o e-mail guardado) ───────────────────────────────────────


async def _guardado(db, cena, pasta, motivo=posvenda.MOTIVO_SEM_PEDIDO) -> MailMessageMeta:
    _mid, meta = await _meta(db, cena, pasta, assunto="Dúvida", corpo="Quando chega?")
    meta.estado = "ignorado"
    meta.motivo = motivo
    await db.commit()
    return meta


@pytest.mark.parametrize("motivo", [posvenda.MOTIVO_SEM_PEDIDO, posvenda.MOTIVO_SEM_CHAMADO])
async def test_ligar_a_mao_ao_chamado_escolhido(db, cena, client, auth_as, motivo):
    pasta = await _pasta_chamado(db, cena)
    ch = await _chamado(db)
    meta = await _guardado(db, cena, pasta, motivo)
    antes = await _retrato_dos_chamados(db)
    auth_as(cena.dono)

    lista = (await client.get("/api/chamados/emails/sem-chamado")).json()
    assert [(i["id"], i["motivo"]) for i in lista["itens"]] == [(str(meta.message_id), motivo)]
    assert lista["itens"][0]["assunto"] == "Dúvida"

    url = f"/api/chamados/emails/{meta.message_id}/ligar"
    resp = await client.post(url, json={"chamado_id": str(ch.id)})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": str(meta.message_id),
        "chamado_id": str(ch.id),
        "estado": "gravado",
    }
    meta = await meta_de(db, meta.message_id)
    assert (meta.estado, meta.motivo, meta.chamado_id) == ("gravado", None, ch.id)
    assert meta.vinculado_por == "chamado_manual" and meta.pedido_marketplace == X
    assert meta.alertas[-1]["codigo"] == "ligado_ao_chamado_por_pessoa"
    assert meta.alertas[-1]["por"] == str(cena.dono.id)
    assert await _retrato_dos_chamados(db) == antes
    # de novo: já está ligado (nunca duas vezes) e saiu da lista
    assert (await client.post(url, json={"chamado_id": str(ch.id)})).status_code == 409
    assert (await client.get("/api/chamados/emails/sem-chamado")).json()["itens"] == []
    assert (await client.get(f"/api/chamados/{ch.id}/emails")).json()["total"] == 1


async def test_ligar_a_mao_recusa_outra_loja(db, cena, client, auth_as):
    pasta = await _pasta_chamado(db, cena)
    outra = await _chamado(db, conta="Oliveira")
    meta = await _guardado(db, cena, pasta)
    auth_as(cena.dono)
    url = f"/api/chamados/emails/{meta.message_id}/ligar"
    r1 = await client.post(url, json={"chamado_id": str(outra.id)})
    assert r1.status_code == 409 and r1.json()["detail"]["code"] == "chamado_de_outra_loja"
    assert (await client.post(url, json={})).status_code == 422
    r3 = await client.post(url, json={"chamado_id": str(uuid4())})
    assert r3.status_code == 404
    assert (await meta_de(db, meta.message_id)).estado == "ignorado"


async def test_ligar_a_mao_so_quem_mexe(db, cena, client, auth_as, make_user):
    pasta = await _pasta_chamado(db, cena)
    ch = await _chamado(db)
    meta = await _guardado(db, cena, pasta)
    quem_le = await make_user(permissions={"chamados": {"view": True, "edit": True}})
    auth_as(quem_le)
    resp = await client.post(
        f"/api/chamados/emails/{meta.message_id}/ligar", json={"chamado_id": str(ch.id)}
    )
    assert resp.status_code == 403
    assert (await client.get("/api/chamados/emails/sem-chamado")).status_code == 403
    assert (await meta_de(db, meta.message_id)).estado == "ignorado"


async def test_ligar_a_mao_so_o_guardado(db, cena, client, auth_as):
    """O e-mail que a ponte ligou (ou guardou por outro motivo) não se religa por aqui."""
    pasta = await _pasta_chamado(db, cena)
    ch = await _chamado(db)
    meta = await _guardado(db, cena, pasta, motivo="pasta_sem_categoria")
    auth_as(cena.dono)
    resp = await client.post(
        f"/api/chamados/emails/{meta.message_id}/ligar", json={"chamado_id": str(ch.id)}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "email_nao_esta_para_ligar"


async def test_lista_para_ligar_filtra_antes_do_limite_e_mostra_o_suspeito(
    db, cena, client, auth_as, monkeypatch
):
    """Revisão de 21/10: a lista do "ligar à mão" filtra quem LÊ no SQL, antes do
    limite — os mais novos do banco inteiro (aqui, na lixeira) não podem ocupar a
    lista e esconder o e-mail que a pessoa lê. E o cartão traz o remetente
    SUSPEITO (com os motivos), como o do /atendimento: quem liga à mão vê antes."""
    from app.models.mail_admin import MailLixeira

    monkeypatch.setattr(posvenda, "LIMITE_EMAILS", 2)
    pasta = await _pasta_chamado(db, cena)
    _mid, lido = await _meta(
        db, cena, pasta, assunto="Pedido bloqueado", recebido_em=AGORA - timedelta(hours=3)
    )
    lido.estado = "ignorado"
    lido.motivo = posvenda.MOTIVO_SEM_PEDIDO
    lido.suspeito = True
    lido.suspeito_motivos = ["dominio_parecido"]
    for i in range(3):
        _m, meta = await _meta(
            db, cena, pasta, assunto=f"Na lixeira {i}", recebido_em=AGORA - timedelta(minutes=i)
        )
        meta.estado = "ignorado"
        meta.motivo = posvenda.MOTIVO_SEM_CHAMADO
        db.add(MailLixeira(message_id=meta.message_id, excluido_em=AGORA))
    await db.commit()
    auth_as(cena.dono)

    lista = (await client.get("/api/chamados/emails/sem-chamado")).json()
    assert (lista["total"], lista["ocultos"]) == (1, 3)
    (item,) = lista["itens"]
    assert (item["id"], item["assunto"]) == (str(lido.message_id), "Pedido bloqueado")
    assert item["suspeito"] is True
    assert item["suspeito_motivos"] == [
        "o remetente se diz da plataforma, mas o domínio não é o oficial"
    ]
    # Ligado à mão, o cartão do bloco "E-mails ligados" do chamado traz o mesmo selo.
    ch = await _chamado(db)
    resp = await client.post(
        f"/api/chamados/emails/{lido.message_id}/ligar", json={"chamado_id": str(ch.id)}
    )
    assert resp.status_code == 200, resp.text
    (ligado,) = (await client.get(f"/api/chamados/{ch.id}/emails")).json()["itens"]
    assert ligado["suspeito"] is True and ligado["suspeito_motivos"]
