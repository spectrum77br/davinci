# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""O religamento das vendas sem pedido (scripts/mail_religar_vendas.py, 21/10/2026).

O script NÃO roda sozinho (decisão 6 do dono: o passado não muda; fica o
`--dry-run` para quando ele pedir). Aqui: o seco não grava nada; o `--gravar`
liga a conversa ao nº (ou muda as mensagens para a conversa de e-mail da venda
que já existe), só quando o e-mail cita UM nº que existe hoje e a conversa não
mistura vendas — e nunca deixa nada do e-mail na saída.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.models.mail_atendimento import MailMessageMeta
from scripts import mail_religar_vendas as script
from tests.test_mail_ponte import (
    _config,
    categorizar,
    cena,
    conversa,
    entrar,
    mensagens,
    meta_de,
    pedido_existe,
    rodar,
)

ML = "nao-responder@mercadolivre.com.br"


async def _venda_antiga(db, cena, numero: str) -> UUID:
    """Uma venda de 09–21/10: a conversa de e-mail SEM pedido (o nº ainda não
    estava no Bling quando a ponte leu) — como ficou antes da 0422."""
    await categorizar(db, cena.geral, "vendas ml", "venda")
    mid = await entrar(
        db,
        cena.geral,
        pasta="vendas ml",
        de=ML,
        de_nome="Mercado Livre",
        assunto=f"Você vendeu! Pedido {numero}",
    )
    await rodar(db)
    meta = await meta_de(db, mid)
    c = await conversa(db, meta.conversa_id)
    meta.pedido_marketplace = None
    meta.vinculado_por = None
    meta.regras_versao = 1
    meta.alertas = [{"codigo": "pedido_nao_encontrado", "texto": "pedido citado, não encontrado"}]
    c.pedido_marketplace = None
    c.externo_id = f"mail:{mid}"
    await db.commit()
    return mid


async def test_seco_conta_e_nao_grava_nada(db, cena):
    numero = "2000012345679001"
    mid = await _venda_antiga(db, cena, numero)
    await pedido_existe(db, cena.ml, numero)
    resumo = await script.religar(seco=True)
    assert (resumo["seco"], resumo["candidatas"], resumo["ligado"]) == (True, 1, 1)
    assert resumo["pedido_na_conversa"] == 1
    m = await meta_de(db, mid)
    assert m.pedido_marketplace is None and m.vinculado_por is None
    assert (await conversa(db, m.conversa_id)).pedido_marketplace is None
    # Só contagens na saída: nem o nº, nem assunto, nem remetente.
    assert numero not in json.dumps(resumo) and "mercadolivre" not in json.dumps(resumo)


async def test_gravar_liga_a_conversa_ao_numero(db, cena):
    numero = "2000012345679002"
    mid = await _venda_antiga(db, cena, numero)
    await pedido_existe(db, cena.ml, numero)
    resumo = await script.religar(seco=False)
    assert (resumo["ligado"], resumo["pedido_na_conversa"]) == (1, 1)
    assert resumo["ligado_por_plataforma"] == {"ml": 1}
    m = await meta_de(db, mid)
    assert (m.estado, m.pedido_marketplace) == ("gravado", numero)
    assert any(a["codigo"] == "religado_venda" for a in m.alertas)
    assert (await conversa(db, m.conversa_id)).pedido_marketplace == numero
    # Rodar de novo não acha mais nada (idempotente).
    assert (await script.religar(seco=False))["candidatas"] == 0


async def test_gravar_muda_para_a_conversa_da_venda_que_ja_existe(db, cena):
    numero = "2000012345679003"
    mid = await _venda_antiga(db, cena, numero)
    origem = (await meta_de(db, mid)).conversa_id
    await pedido_existe(db, cena.ml, numero)
    cliente = await entrar(db, cena.geral, pasta="INBOX", assunto=f"Pedido {numero} não chegou")
    await rodar(db)
    destino = (await meta_de(db, cliente)).conversa_id
    assert destino != origem
    resumo = await script.religar(seco=False)
    assert (resumo["ligado"], resumo["movidas_para_a_venda"]) == (1, 1)
    m = await meta_de(db, mid)
    assert (m.conversa_id, m.pedido_marketplace) == (destino, numero)
    assert len(await mensagens(db, destino)) == 2 and await mensagens(db, origem) == []
    velha = await conversa(db, origem)
    assert velha.situacao == "fechada" and velha.dados["mail"]["movida_para"] == str(destino)


async def test_fica_de_fora_o_que_nao_existe_ou_mistura_vendas(db, cena):
    fora = await _venda_antiga(db, cena, "2000012345679004")
    a = await _venda_antiga(db, cena, "2000012345679005")
    b = await _venda_antiga(db, cena, "2000012345679006")
    for n in ("2000012345679005", "2000012345679006"):
        await pedido_existe(db, cena.ml, n)
    # O e-mail da venda B dentro da conversa da venda A: a conversa mistura vendas.
    meta_a, meta_b = await meta_de(db, a), await meta_de(db, b)
    meta_b.conversa_id = meta_a.conversa_id
    msg_b = await db.get(AtendimentoMensagem, meta_b.mensagem_id)
    msg_b.conversa_id = meta_a.conversa_id
    await db.commit()
    resumo = await script.religar(seco=False)
    assert resumo["nao_existe_no_bling"] == 1
    assert resumo["conversa_mista"] == 1 and resumo.get("ligado", 0) == 0
    for mid in (fora, a, b):
        assert (await meta_de(db, mid)).pedido_marketplace is None


def test_argumentos_seco_por_padrao():
    args = script._argumentos([])
    assert (args.gravar, args.dry_run, args.desde, args.limite) == (False, False, None, None)
    args = script._argumentos(["--dry-run", "--desde", "2026-10-09", "--limite", "5"])
    assert args.dry_run and args.desde.date().isoformat() == "2026-10-09" and args.limite == 5
    with pytest.raises(SystemExit):
        script._argumentos(["--dry-run", "--gravar"])
