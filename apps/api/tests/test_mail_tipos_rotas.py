# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""Tipo e chamado visíveis na aba E-mail, no escopo Tuta e sem exigir pedido de um aviso."""

import pytest

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento import gravar
from tests.test_mail_atendimento_rotas import admin
from tests.test_mail_ponte import _config, cena, entrar, meta_de, rodar

URL = "/api/atendimento"


async def _emails(db, cena):
    resultado = {}
    for tipo in ("informativo", "chamado", "venda"):
        mid = await entrar(
            db,
            cena.geral,
            pasta="INBOX",
            de="avisos@mercadolivre.com.br",
            assunto=f"E-mail de teste {tipo}",
            corpo="Conteúdo de teste, sem pedido de venda.",
        )
        await rodar(db)
        meta = await meta_de(db, mid)
        conversa = await db.get(AtendimentoConversa, meta.conversa_id)
        mensagem = await db.get(AtendimentoMensagem, meta.mensagem_id)
        caso = "ML-123456" if tipo == "chamado" else None
        bloco = {"tipo_email": tipo, "caso_id": caso}
        conversa.dados = {
            **conversa.dados,
            "mail": {**conversa.dados["mail"], **bloco},
        }
        mensagem.payload = {**mensagem.payload, "mail": {**mensagem.payload["mail"], **bloco}}
        meta.protocolo = caso
        # O legado pode continuar marcado assim no banco: a fila deve
        # separar falta de pedido de um informativo/venda/chamado já identificado.
        meta.estado = "sem_vinculo"
        await db.commit()
        resultado[tipo] = (mid, conversa.id)
    return resultado


async def test_tipo_e_identificador_aparecem_e_filtram_so_email_tuta(db, cena, client, admin):
    emails = await _emails(db, cena)
    gmail, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=cena.amazon,
        plataforma="amazon",
        canal_nome="email",
        externo_id="gmail-fora-dos-filtros-tuta",
        comprador_id="comprador-falso",
        dados={"mail": {"tipo_email": "informativo"}},
    )
    await gravar.gravar_mensagem(
        db,
        gmail,
        externo_id="gmail-msg",
        autor="cliente",
        texto="Mensagem pelo Gmail.",
        enviada_em=None,
    )
    await db.commit()

    for tipo, (mid, cid) in emails.items():
        r = await client.get(f"{URL}/conversas", params={"caixa": "email", "tipo_email": tipo})
        assert r.status_code == 200, r.text
        assert [c["id"] for c in r.json()["itens"]] == [str(cid)]
        assert r.json()["itens"][0]["email"]["tipo_email"] == tipo
        cartao = await client.get(f"{URL}/email/emails/{mid}")
        assert cartao.status_code == 200
        assert cartao.json()["tipo_email"] == tipo
        assert cartao.json()["caso_id"] == ("ML-123456" if tipo == "chamado" else None)
    assert gmail.id not in {cid for _, cid in emails.values()}
    busca = await client.get(
        f"{URL}/conversas", params={"caixa": "email", "tipo_email": "chamado", "q": "ML-123456"}
    )
    assert [c["id"] for c in busca.json()["itens"]] == [str(emails["chamado"][1])]


async def test_contagens_e_sem_vinculo_nao_cobram_pedido_dos_tres_tipos(db, cena, client, admin):
    await _emails(db, cena)
    contagens = await client.get(f"{URL}/email/contagens")
    assert contagens.json()["por_tipo_email"] == {"informativo": 1, "chamado": 1, "venda": 1}
    filas = await client.get(f"{URL}/email/filas")
    assert filas.json()["sem_vinculo"] == 0
    fila = await client.get(f"{URL}/email/emails", params={"fila": "sem_vinculo"})
    assert fila.json()["itens"] == []
    conversas = await client.get(
        f"{URL}/conversas", params={"caixa": "email", "filtro": "email_sem_vinculo"}
    )
    assert conversas.json()["itens"] == []


@pytest.mark.parametrize("tipo", ["outro", "INFO", "cancelamento"])
async def test_tipo_invalido_nao_vira_filtro_solto(client, admin, tipo):
    for rota in (
        f"{URL}/conversas?caixa=email&tipo_email={tipo}",
        f"{URL}/email/emails?tipo_email={tipo}",
    ):
        assert (await client.get(rota)).status_code == 422


async def test_filtro_de_tipo_so_pertence_a_aba_email(client, admin):
    assert (await client.get(f"{URL}/conversas?tipo_email=informativo")).status_code == 422


async def test_fallback_legado_classifica_sem_regravar_dados(db, cena, client, admin):
    emails = await _emails(db, cena)
    cid = emails["venda"][1]
    conversa = await db.get(AtendimentoConversa, cid)
    dados = {**conversa.dados, "mail": {"alias": "loja@example.com", "finalidade": "vendas"}}
    conversa.dados = dados
    await db.commit()

    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "tipo_email": "venda"})

    assert [c["id"] for c in r.json()["itens"]] == [str(cid)]
    assert r.json()["itens"][0]["email"]["tipo_email"] == "venda"
    assert r.json()["itens"][0]["email"]["tipo_email_inferido"] is True
    await db.refresh(conversa)
    assert conversa.dados == dados
